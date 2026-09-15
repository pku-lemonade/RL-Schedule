from typing import Literal

from ..configs.schemas.arch_config import NoCConfig
from ..topology import Topology, topology_from_legacy
from ..topology_compatibility import legacy_coordinates, require_legacy_topology
from ..utils.definitions import NoCChannel

PathNode = tuple[Literal["link", "core"], int]


class Mesh:
    """Fabric-qualified directional mesh used by the failure predictor."""

    def __init__(self, x: int, y: int, fabric_ids: tuple[NoCChannel, ...] = (NoCChannel.CH0, NoCChannel.CH1),
                 *, topology: Topology | None = None):
        self.topology = topology if topology is not None else topology_from_legacy(NoCConfig(x=x, y=y, fabric_ids=fabric_ids))
        config = require_legacy_topology(self.topology, "detailed_predictor")
        if (x, y) != (config.x, config.y) or set(fabric_ids) != set(config.fabric_ids) or len(set(fabric_ids)) != len(fabric_ids):
            raise ValueError("mesh view dimensions/fabrics differ from canonical topology")
        self.coordinates = legacy_coordinates(self.topology)
        self.fabric_ids = tuple(sorted(fabric_ids))
        self.coordinate_ids = {xy: index for (fabric, index), xy in self.coordinates.items()
                               if fabric == int(self.fabric_ids[0])}
        self.x = x
        self.y = y
        self.core_count = x * y
        self.to_link_index: dict[tuple[NoCChannel, int, int], int] = {}
        self.link_to_core_pair: dict[int, tuple[int, int]] = {}
        self.link_to_fabric: dict[int, NoCChannel] = {}
        self.core_link: list[list[int]] = [[], []]
        self.link_core: list[list[int]] = [[], []]
        self.link_count = 0
        self._build_links()

    def _core_id(self, x: int, y: int) -> int:
        try:
            return self.coordinate_ids[(x, y)]
        except KeyError as exc:
            raise ValueError("coordinate is outside the mesh view") from exc

    def _register_link(
        self,
        fabric_id: NoCChannel,
        src: int,
        dst: int,
    ) -> int:
        key = (fabric_id, src, dst)
        existing = self.to_link_index.get(key)
        if existing is not None:
            return existing

        link_id = self.link_count
        self.to_link_index[key] = link_id
        self.link_to_core_pair[link_id] = (src, dst)
        self.link_to_fabric[link_id] = fabric_id
        for core_id in (src, dst):
            self.core_link[0].append(core_id)
            self.core_link[1].append(link_id)
            self.link_core[0].append(link_id)
            self.link_core[1].append(core_id)
        self.link_count += 1
        return link_id

    def _build_links(self) -> None:
        for edge in sorted(self.topology.graph.links, key=lambda e: (e.fabric_id, self.topology.link_indices[e.key])):
            self._register_link(
                NoCChannel(edge.fabric_id),
                self.topology.router_indices[(edge.fabric_id, edge.src_router)],
                self.topology.router_indices[(edge.fabric_id, edge.dst_router)],
            )

    def manhattan_path_nodes(
        self,
        src: int,
        dst: int,
        fabric_id: NoCChannel,
    ) -> list[PathNode]:
        if fabric_id not in self.fabric_ids or (int(fabric_id), src) not in self.coordinates or (int(fabric_id), dst) not in self.coordinates:
            raise ValueError("path endpoint/fabric is outside the mesh view")
        nodes: list[PathNode] = []
        if src == dst:
            return nodes

        current_x, current_y = self.coordinates[(int(fabric_id), src)]
        dst_x, dst_y = self.coordinates[(int(fabric_id), dst)]
        while current_x != dst_x:
            next_x = current_x + 1 if dst_x > current_x else current_x - 1
            current = self._core_id(current_x, current_y)
            following = self._core_id(next_x, current_y)
            nodes.append(
                ("link", self.to_link_index[(fabric_id, current, following)])
            )
            if following != dst:
                nodes.append(("core", following))
            current_x = next_x

        while current_y != dst_y:
            next_y = current_y + 1 if dst_y > current_y else current_y - 1
            current = self._core_id(current_x, current_y)
            following = self._core_id(current_x, next_y)
            nodes.append(
                ("link", self.to_link_index[(fabric_id, current, following)])
            )
            if following != dst:
                nodes.append(("core", following))
            current_y = next_y
        return nodes


def parse_fabric_id(value: object) -> NoCChannel:
    if isinstance(value, NoCChannel):
        return value
    if isinstance(value, bool):
        raise TypeError(f"invalid NoC fabric identity type: {value!r}")
    if isinstance(value, str):
        try:
            return NoCChannel[value]
        except KeyError:
            try:
                return NoCChannel(int(value.removeprefix("CH")))
            except ValueError as exc:
                raise ValueError(
                    f"invalid NoC fabric identity: {value!r}"
                ) from exc
    if isinstance(value, int):
        try:
            return NoCChannel(value)
        except ValueError as exc:
            raise ValueError(
                f"invalid NoC fabric identity: {value!r}"
            ) from exc
    raise ValueError(f"invalid NoC fabric identity: {value!r}")

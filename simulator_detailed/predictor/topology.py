from typing import Dict, List, Literal, Tuple

from ..utils.definitions import NoCChannel

PathNode = Tuple[Literal["link", "core"], int]


class Mesh:
    """Fabric-qualified directional mesh used by the failure predictor."""

    def __init__(self, x: int, y: int):
        self.x = x
        self.y = y
        self.core_count = x * y
        self.to_link_index: Dict[Tuple[NoCChannel, int, int], int] = {}
        self.link_to_core_pair: Dict[int, Tuple[int, int]] = {}
        self.link_to_fabric: Dict[int, NoCChannel] = {}
        self.core_link: List[List[int]] = [[], []]
        self.link_core: List[List[int]] = [[], []]
        self.link_count = 0
        self._build_links()

    def _core_id(self, x: int, y: int) -> int:
        return y * self.x + x

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
        # Keep this order identical to NoC.build_connection_mesh().
        for fabric_id in NoCChannel:
            for y in range(self.y):
                for x in range(self.x):
                    current = self._core_id(x, y)
                    if x < self.x - 1:
                        east = self._core_id(x + 1, y)
                        self._register_link(fabric_id, current, east)
                        self._register_link(fabric_id, east, current)
                    if y < self.y - 1:
                        north = self._core_id(x, y + 1)
                        self._register_link(fabric_id, current, north)
                        self._register_link(fabric_id, north, current)

    def manhattan_path_nodes(
        self,
        src: int,
        dst: int,
        fabric_id: NoCChannel,
    ) -> List[PathNode]:
        nodes: List[PathNode] = []
        if src == dst:
            return nodes

        current_x = src % self.x
        current_y = src // self.x
        dst_x = dst % self.x
        dst_y = dst // self.x
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
        raise ValueError(f"invalid NoC fabric identity: {value!r}")
    if isinstance(value, str):
        try:
            return NoCChannel[value]
        except KeyError:
            try:
                return NoCChannel(int(value))
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

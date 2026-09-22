"""The public example adapter: synthetic grid world -> generic documents.

The conversion is a pure, total function over the validated synthetic world;
every output passes through the generic schemas before returning. This example
is the only adapter shipped in the public repository; private adapters are
constructed by private launch scripts and never committed here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from .configs.schemas.generic_graph import GenericSystemGraph
from .configs.schemas.generic_transactions import GenericTransactionBatch
from .configs.schemas.synthetic_world import (
    SyntheticMove,
    SyntheticRaise,
    SyntheticWork,
    SyntheticWorld,
)
from .generic_adapter import GenericInputAdapter

_NATURE_ROLES = {"logic": "compute", "storehouse": "memory", "relay": "transit"}


class SyntheticGridWorldAdapter(GenericInputAdapter):
    """Convert one validated synthetic world into generic documents."""

    def __init__(self, world: SyntheticWorld):
        self.world = SyntheticWorld.model_validate(world.model_dump(mode="json"))

    @classmethod
    def from_path(cls, path: str | Path) -> SyntheticGridWorldAdapter:
        raw: object = json.loads(Path(path).read_text())
        if not isinstance(raw, dict) or cast(dict[str, object], raw).get("kind") != (
            "synthetic_grid_world"
        ):
            raise ValueError("expected a synthetic_grid_world document")
        return cls(SyntheticWorld.model_validate(raw))

    def load_system_graph(self) -> GenericSystemGraph:
        world = self.world
        return GenericSystemGraph.model_validate({
            "kind": "generic_system_graph",
            "schema_version": 1,
            "system_id": world.world_id,
            "nodes": [
                {
                    "node_id": site.site,
                    "x": site.col,
                    "y": site.row,
                    "role": _NATURE_ROLES[site.nature],
                }
                for site in world.sites
            ],
            "networks": [{"network_id": plane.plane} for plane in world.planes],
            "ports": [
                {
                    "port_id": socket.socket,
                    "node_id": socket.site,
                    "network_id": socket.plane,
                    "kind": "network" if socket.facing == "wire" else "local",
                }
                for socket in world.sockets
            ],
            "links": [
                {
                    "link_id": wire.wire,
                    "network_id": wire.plane,
                    "src_node": wire.from_site,
                    "src_port": wire.from_socket,
                    "dst_node": wire.to_site,
                    "dst_port": wire.to_socket,
                }
                for wire in world.wires
            ],
            "dma_endpoints": [
                {
                    "endpoint_id": mover.mover,
                    "node_id": mover.site,
                    "network_id": mover.plane,
                    "port_id": mover.socket,
                }
                for mover in world.movers
            ],
            "execution_units": [
                {
                    "unit_id": worker.worker,
                    "node_id": worker.site,
                    "network_id": worker.plane,
                    "port_id": worker.socket,
                }
                for worker in world.workers
            ],
            "memory_resources": [
                {
                    "resource_id": store.store,
                    "owner_node": store.site,
                    "capacity_bytes": store.room_bytes,
                    "endpoint_id": store.service,
                    "network_id": store.plane,
                    "port_id": store.socket,
                }
                for store in world.stores
            ],
            "static_routes": [
                {
                    "network_id": path.plane,
                    "source": path.from_agent,
                    "destination": path.to_agent,
                    "link_ids": list(path.wires),
                }
                for path in world.paths
            ],
        })

    def load_transactions(self) -> GenericTransactionBatch:
        world = self.world
        transactions: list[dict[str, object]] = []
        for job in world.jobs:
            base: dict[str, object] = {
                "transaction_id": job.job,
                "depends_on": list(job.after),
                "start_cycles": job.at_cycle,
            }
            if isinstance(job, SyntheticMove):
                transactions.append(base | {
                    "kind": "transfer",
                    "network_id": job.plane,
                    "source": job.from_agent,
                    "destination": job.to_agent,
                    "payload_bytes": job.load_bytes,
                })
            elif isinstance(job, SyntheticWork):
                transactions.append(base | {
                    "kind": "compute",
                    "unit_id": job.worker,
                    "duration_cycles": job.span_cycles,
                })
            elif isinstance(job, SyntheticRaise):
                transactions.append(base | {
                    "kind": "signal",
                    "counter_id": job.gauge,
                    "delta": job.by,
                })
            else:
                transactions.append(base | {
                    "kind": "wait",
                    "counter_id": job.gauge,
                    "threshold": job.until,
                })
        return GenericTransactionBatch.model_validate({
            "kind": "generic_transaction_batch",
            "schema_version": 1,
            "batch_id": f"{world.world_id}-jobs",
            "graph_path": None,
            "timing": [
                {
                    "network_id": rate.plane,
                    "link": rate.rate.model_dump(mode="json"),
                    "overrides": [
                        {"link_id": tweak.wire, "timing": tweak.rate.model_dump(mode="json")}
                        for tweak in rate.tweaks
                    ],
                }
                for rate in world.rates
            ],
            "counters": [
                {"counter_id": gauge.gauge, "initial_value": gauge.start}
                for gauge in world.gauges
            ],
            "transactions": transactions,
            "max_cycles": world.limit_cycles,
        })

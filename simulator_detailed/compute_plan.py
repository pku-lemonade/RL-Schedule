"""Pure finite workload admission. Memory lowering and execution are separate."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass

from .compute_cost import compute_cost, rate_key
from .compute_records import ComputeJobPlan, ComputePlanRecord, ComputeWorkloadResult
from .configs.schemas.compute_workload import ComputeWorker, ComputeWorkload
from .configs.schemas.memory_replay import MemoryBuffer, MemoryRange
from .configs.schemas.topology import CanonicalTopology
from .memory_plan import (
    bind_memory_system,
    canonical_json,
    memory_configuration_identity,
)
from .memory_resources import memory_resource_definitions
from .topology import content_digest
from .torus import TorusRouting

Extent = tuple[str, int, int]


def _overlaps(left: Extent, right: Extent) -> bool:
    return left[0] == right[0] and left[1] < right[2] and right[1] < left[2]


@dataclass(frozen=True)
class ComputePlan:
    config: ComputeWorkload
    graph: CanonicalTopology
    record: ComputePlanRecord

    @classmethod
    def compile(cls, config: ComputeWorkload, source_document: object) -> ComputePlan:
        # model_copy/model_construct can bypass validators; the public compiler cannot.
        config = ComputeWorkload.model_validate(config.model_dump(mode="python"))
        graph = bind_memory_system(config.memory, source_document)
        memory_resource_definitions(config.memory, graph)
        jobs = _Admission(config, graph).compile()
        source_json = canonical_json(graph)
        configuration_json = canonical_json(config)
        identity = config.model_dump(mode="json")
        identity["memory"] = memory_configuration_identity(config.memory)
        source_sha = content_digest(json.loads(source_json))
        configuration_sha = content_digest(identity)
        # Include hardware settings/evidence even when a small cost happens to
        # quantize to the same duration under two different configurations.
        effective_sha = content_digest({"revision": "compute_planning_v1", "configuration": identity,
                                        "jobs": [j.model_dump(mode="json") for j in jobs]})
        plan_sha = content_digest({"source_sha256": source_sha, "configuration_sha256": configuration_sha,
                                   "effective_sha256": effective_sha, "execution_supported": False})
        record = ComputePlanRecord(source_sha256=source_sha, configuration_sha256=configuration_sha,
                                   effective_sha256=effective_sha, plan_sha256=plan_sha,
                                   source_json=source_json, configuration_json=configuration_json, jobs=jobs)
        return cls(config, graph, record)

    def revalidate(self) -> ComputePlan:
        if self.config.memory.source.kind == "hardware_profile":
            if self.graph.origin.kind != "hardware_profile" or self.graph.origin.document_json is None:
                raise ValueError("compute profile plan requires its source document")
            source: object = json.loads(self.graph.origin.document_json)
        else:
            source = self.graph.model_dump(mode="python")
        rebuilt = self.compile(self.config, source)
        if rebuilt.graph != self.graph or rebuilt.record != self.record:
            raise ValueError("compute plan differs from its admitted source/configuration")
        return rebuilt

    def planning_result(self) -> ComputeWorkloadResult:
        return ComputeWorkloadResult(plan=self.revalidate().record)

    def require_executable(self) -> None:
        raise NotImplementedError("a compute plan does not execute arithmetic; a workload coordinator is still required")


class _Admission:
    def __init__(self, config: ComputeWorkload, graph: CanonicalTopology):
        self.config = config
        self.graph = graph
        self.buffers = {b.buffer_id: b for b in config.memory.buffers}
        self.resources = {r.resource_id: r for r in graph.resources}
        self.endpoints = {e.endpoint_id: e for e in config.memory.endpoints}
        self.routers = {r.key: r for r in graph.routers}
        self.workers = {w.tile_id: w for w in config.workers}
        self.rates = {r.rate_id: r for r in config.rates}
        self.dtypes = {d.dtype_id: d for d in config.dtypes}
        self.jobs = {j.job_id: j for s in config.streams for j in s.jobs}
        self.job_workers = {j.job_id: s.worker_tile_id for s in config.streams for j in s.jobs}
        self.routing = TorusRouting.from_graph(graph, config.memory.routing) if config.memory.routing else None

    def compile(self) -> tuple[ComputeJobPlan, ...]:
        self._workers_and_rates()
        dependencies = self._dependencies()
        occupied: list[Extent] = []
        result: list[ComputeJobPlan] = []
        for stream in self.config.streams:
            if stream.worker_tile_id not in self.workers:
                raise ValueError("stream requires a canonical admitted physical worker")
            worker = self.workers[stream.worker_tile_id]
            for slot in stream.slots:
                for access in (slot.a, slot.b, slot.c):
                    buffer, extent = self._range(access)
                    if buffer.resource_id != worker.l1_resource_id:
                        raise ValueError("slot must reside in its worker's owned L1")
                    if not buffer.readable or not buffer.writable:
                        raise ValueError("reusable slots require readable and writable storage")
                    if any(_overlaps(extent, other) for other in occupied):
                        raise ValueError("compute slot bundles overlap")
                    occupied.append(extent)
            for index, job in enumerate(stream.jobs):
                matches = [self.rates[r] for r in worker.rate_ids if self.rates[r].key == rate_key(job.operation)]
                if len(matches) != 1:
                    raise ValueError("job requires exactly one matching effective rate; no precision/fidelity fallback")
                cost = compute_cost(job.operation, matches[0], self.dtypes,
                                    native_clock_hz=worker.native_clock_hz, aci_clock_hz=self.config.memory.aci_clock_hz)
                slot = stream.slots[index % len(stream.slots)]
                for access, footprint in zip((slot.a, slot.b, slot.c), (cost.a, cost.b, cost.c), strict=True):
                    if footprint.storage_bytes > access.size_bytes:
                        raise ValueError("complete operand/output including full K must fit its slot")
                for name, binding, slot_range, footprint in (("a", job.a, slot.a, cost.a), ("b", job.b, slot.b, cost.b)):
                    buffer, extent = self._range(binding.source)
                    if not buffer.readable or binding.source.size_bytes != footprint.storage_bytes:
                        raise ValueError("input range must be readable and exactly match tensor storage bytes")
                    if binding.mode == "local":
                        self._local_prefix(extent, slot_range)
                    else:
                        self._network(worker, binding.fabric_id, buffer.resource_id, response=True)
                    producer = binding.version.producer_job_id
                    if producer is None:
                        if not buffer.initially_ready:
                            raise ValueError("initial compute input requires initialized storage")
                    else:
                        prior = self.jobs[producer]
                        if prior.output.mode == "write_posted":
                            raise ValueError("posted output cannot provide remote completion for an input version")
                        _, prior_extent = self._range(prior.output.destination)
                        operand = job.operation.a if name == "a" else job.operation.b
                        if extent != prior_extent or operand != prior.operation.c:
                            raise ValueError("producer output range/shape/dtype/layout must match the input version")
                buffer, output = self._range(job.output.destination)
                if not buffer.writable or job.output.destination.size_bytes != cost.c.storage_bytes:
                    raise ValueError("output range must be writable and exactly match tensor storage bytes")
                if job.output.mode == "local":
                    self._local_prefix(output, slot.c)
                else:
                    self._network(worker, job.output.fabric_id, buffer.resource_id,
                                  response=job.output.mode == "write_acknowledged")
                if any(_overlaps(output, self._range(b.source)[1]) for b in (job.a, job.b)):
                    raise ValueError("in-place compute output is unsupported")
                result.append(ComputeJobPlan(job_id=job.job_id, stream_id=stream.stream_id, worker_tile_id=worker.tile_id,
                                             slot_id=slot.slot_id, slot_generation=index // len(stream.slots),
                                             predecessors=tuple(sorted(dependencies[job.job_id])),
                                             fifo_predecessor_id=stream.jobs[index - 1].job_id if index else None, cost=cost))
        return tuple(result)

    def _workers_and_rates(self) -> None:
        for rate in self.config.rates:
            if any(dtype not in self.dtypes for dtype in (rate.key.a_dtype, rate.key.b_dtype, rate.key.c_dtype)):
                raise ValueError("rate requires explicit storage dtype definitions")
        for worker in self.config.workers:
            if worker.tile_id not in self.graph.enabled_worker_ids:
                raise ValueError("compute worker must be an enabled canonical physical tile")
            resource = self.resources.get(worker.l1_resource_id)
            if resource is None or resource.kind != "local_sram" or resource.owner_tile_id != worker.tile_id:
                raise ValueError("compute worker requires its own canonical L1 resource")
            keys: set[str] = set()
            for rate_id in worker.rate_ids:
                if rate_id not in self.rates:
                    raise ValueError("worker names an unknown effective rate")
                key = canonical_json(self.rates[rate_id].key)
                if key in keys:
                    raise ValueError("ambiguous effective rate key on one physical worker")
                keys.add(key)
            fabrics: set[int] = set()
            for endpoint_id in worker.endpoint_ids:
                endpoint = self.endpoints.get(endpoint_id)
                if endpoint is None or not endpoint.enabled or "initiator" not in endpoint.roles:
                    raise ValueError("worker endpoint must be an enabled memory initiator")
                router = self.routers[(endpoint.fabric_id, endpoint.router_id)]
                if router.enabled is not True or router.tile_id != worker.tile_id or resource.resource_id not in endpoint.resource_ids:
                    raise ValueError("worker endpoint disagrees with physical tile/L1 ownership")
                if endpoint.fabric_id in fabrics:
                    raise ValueError("ambiguous worker interface on one fabric")
                fabrics.add(endpoint.fabric_id)

    def _range(self, access: MemoryRange) -> tuple[MemoryBuffer, Extent]:
        buffer = self.buffers.get(access.buffer_id)
        if buffer is None:
            raise ValueError("compute range names an unknown buffer")
        if access.offset_bytes + access.size_bytes > buffer.size_bytes:
            raise ValueError("compute range exceeds buffer reservation")
        start = buffer.base_address + access.offset_bytes
        alignment = self.config.memory.packet.address_alignment_bytes
        if start % alignment or access.size_bytes % alignment:
            raise ValueError("compute range is incompatible with memory alignment")
        return buffer, (buffer.resource_id, start, start + access.size_bytes)

    def _local_prefix(self, extent: Extent, slot: MemoryRange) -> None:
        _, allocated = self._range(slot)
        if extent[:2] != allocated[:2] or extent[2] > allocated[2]:
            raise ValueError("local tensor must reside in its assigned slot range")

    def _network(self, worker: ComputeWorker, fabric: int | None, remote_resource: str, *, response: bool) -> None:
        if fabric is None or self.routing is None:
            raise ValueError("remote compute I/O requires explicit fabric routing")
        initiators = [e for e in self.config.memory.endpoints if e.enabled and e.fabric_id == fabric
                      and "initiator" in e.roles and worker.l1_resource_id in e.resource_ids
                      and self.routers[(fabric, e.router_id)].tile_id == worker.tile_id]
        targets = [e for e in self.config.memory.endpoints if e.enabled and e.fabric_id == fabric
                   and "target" in e.roles and remote_resource in e.resource_ids]
        if len(initiators) != 1 or initiators[0].endpoint_id not in worker.endpoint_ids or len(targets) != 1:
            raise ValueError("remote compute I/O requires one admitted initiator and target per fabric")
        source, target = initiators[0], targets[0]
        self.routing.endpoint_path(fabric, source.endpoint_id, target.endpoint_id, "request")
        if response:
            if "response_sink" not in source.roles:
                raise ValueError("reader/acknowledged writer requires a response sink")
            self.routing.endpoint_path(fabric, target.endpoint_id, source.endpoint_id, "response")

    def _dependencies(self) -> dict[str, set[str]]:
        edges: dict[str, set[str]] = {}
        declared: dict[str, set[str]] = {}
        for stream in self.config.streams:
            previous: str | None = None
            for job in stream.jobs:
                predecessors = set(job.depends_on)
                predecessors.update(b.version.producer_job_id for b in (job.a, job.b) if b.version.producer_job_id is not None)
                declared[job.job_id] = set(predecessors)
                if previous is not None:
                    predecessors.add(previous)
                for predecessor in predecessors:
                    if predecessor not in self.jobs:
                        raise ValueError("unknown compute predecessor")
                    if self.job_workers[predecessor] != stream.worker_tile_id:
                        raise ValueError("cross-worker completion requires an unmodeled notification")
                edges[job.job_id] = predecessors
                previous = job.job_id
        # Iterative Kahn traversal also handles finite streams longer than Python's recursion limit.
        dependents: dict[str, list[str]] = {job: [] for job in edges}
        counts = {job: len(predecessors) for job, predecessors in edges.items()}
        for job, predecessors in edges.items():
            for predecessor in predecessors:
                dependents[predecessor].append(job)
        ready = deque(job for job, count in counts.items() if count == 0)
        visited = 0
        while ready:
            visited += 1
            for job in dependents[ready.popleft()]:
                counts[job] -= 1
                if counts[job] == 0:
                    ready.append(job)
        if visited != len(edges):
            raise ValueError("compute dependencies contain a cycle or backward FIFO wait")
        return declared

"""Pure lowering of local, generation-bound compute into the mixed access DAG."""
from __future__ import annotations

from .compute_cost import compute_cost, rate_key
from .compute_records import ComputeCost
from .configs.schemas.memory_replay import MemoryOperation, MemoryRange, MemoryVersion
from .configs.schemas.multicast_sync import (
    LocalDataPrerequisite,
    MulticastSyncWorkload,
    SyncGate,
)
from .configs.schemas.topology import CanonicalTopology, GraphRecord, Identifier, Index
from .multicast_inventory import inventory_id


class MixedComputeJobPlan(GraphRecord):
    job_id: Identifier
    stream_id: Identifier
    worker_tile_id: Identifier
    resource_id: Identifier
    slot_id: Identifier
    generation: Index
    cost: ComputeCost
    operands: tuple[Identifier, Identifier]
    result_operation: Identifier
    writer_operation: Identifier | None
    compute_after: Identifier | None
    writer_after: Identifier | None
    consumers: tuple[Identifier, ...] = ()


class MixedComputePlan(GraphRecord):
    jobs: tuple[MixedComputeJobPlan, ...]
    operations: tuple[MemoryOperation, ...]
    gates: tuple[SyncGate, ...]


def lower_compute(workload: MulticastSyncWorkload, graph: CanonicalTopology) -> MixedComputePlan | None:
    config = workload.compute
    if config is None:
        return None
    if workload.runtime is None:
        raise ValueError("mixed compute requires executable runtime settings")
    workers = {w.tile_id: w for w in config.workers}
    rates = {r.rate_id: r for r in config.rates}
    dtypes = {d.dtype_id: d for d in config.dtypes}
    resources = {r.resource_id: r for r in graph.resources}
    endpoints = {e.endpoint_id: e for e in workload.memory.endpoints}
    routers = {r.key: r for r in graph.routers}
    buffers = {b.buffer_id: b for b in workload.memory.buffers}
    occupied: list[tuple[str, int, int]] = []
    jobs: list[MixedComputeJobPlan] = []
    operations: list[MemoryOperation] = []
    gates: list[SyncGate] = []
    original_ids = {o.operation_id for o in workload.operations} | {w.operation_id for w in workload.writes} | {i.operation_id for i in workload.increments} | {w.wait_id for w in workload.waits}
    declared_jobs = {j.job_id for st in config.streams for j in st.jobs}
    result_ids = {j.job_id: (inventory_id("compute", j.job_id, "result") if j.output.mode == "local"
                           else inventory_id("compute", j.job_id, "writer")) for st in config.streams for j in st.jobs}

    def prerequisite(value: LocalDataPrerequisite) -> LocalDataPrerequisite:
        producer = value.version.producer_id
        if producer in result_ids:
            return value.model_copy(update={"version": MemoryVersion(kind="producer", producer_id=result_ids[producer])})
        return value

    def extent(access: MemoryRange, owner: str) -> tuple[str, int, int]:
        b = buffers.get(access.buffer_id)
        if b is None or b.resource_id != owner or access.offset_bytes + access.size_bytes > b.size_bytes or not b.readable or not b.writable:
            raise ValueError("compute slot must be readable/writable within its owned canonical L1")
        return owner, b.base_address + access.offset_bytes, b.base_address + access.offset_bytes + access.size_bytes

    for worker in config.workers:
        resource = resources.get(worker.l1_resource_id)
        if (worker.tile_id not in graph.enabled_worker_ids or resource is None or resource.kind != "local_sram"
                or resource.owner_tile_id != worker.tile_id):
            raise ValueError("compute worker requires its enabled physical tile and owned L1")
        if any(r not in rates for r in worker.rate_ids):
            raise ValueError("compute worker names unknown rate")
        if len({rates[r].key.model_dump_json() for r in worker.rate_ids}) != len(worker.rate_ids):
            raise ValueError("ambiguous compute rates")
        fabrics: set[int] = set()
        for endpoint_id in worker.endpoint_ids:
            e = endpoints.get(endpoint_id)
            if (e is None or not e.enabled or "initiator" not in e.roles or worker.l1_resource_id not in e.resource_ids
                    or routers[e.fabric_id, e.router_id].tile_id != worker.tile_id or e.fabric_id in fabrics):
                raise ValueError("compute endpoint must expose exactly one owned interface per fabric")
            fabrics.add(e.fabric_id)
    for stream in config.streams:
        if stream.worker_tile_id not in workers:
            raise ValueError("unknown compute worker")
        worker = workers[stream.worker_tile_id]
        for slot in stream.slots:
            for r in (slot.a, slot.b, slot.c):
                current = extent(r, worker.l1_resource_id)
                if any(current[0] == old[0] and current[1] < old[2] and old[1] < current[2] for old in occupied):
                    raise ValueError("compute slot bundles overlap")
                occupied.append(current)
        for index, job in enumerate(stream.jobs):
            if not set(job.depends_on) <= original_ids | declared_jobs:
                raise ValueError("unknown mixed compute dependency")
            matches = [rates[r] for r in worker.rate_ids if rates[r].key == rate_key(job.operation)]
            if len(matches) != 1:
                raise ValueError("compute needs exactly one matching effective rate")
            cost = compute_cost(job.operation, matches[0], dtypes, native_clock_hz=worker.native_clock_hz,
                                aci_clock_hz=workload.memory.aci_clock_hz)
            slot = stream.slots[index % len(stream.slots)]
            operands = (inventory_id("compute", job.job_id, "a"), inventory_id("compute", job.job_id, "b"))
            result_id = inventory_id("compute", job.job_id, "result")
            previous = stream.jobs[index - 1].job_id if index else None
            reuse = stream.jobs[index - len(stream.slots)].job_id if index >= len(stream.slots) else None
            endpoint = worker.endpoint_ids[0]
            deps = tuple(dict.fromkeys((*job.depends_on, *((reuse,) if reuse else ()))))
            user_gate = next((g for g in workload.gates if g.operation_id == job.job_id), None)
            for op, binding, target, footprint in zip(operands, (job.a, job.b), (slot.a, slot.b), (cost.a, cost.b), strict=True):
                value = prerequisite(binding)
                if (value.access.buffer_id != target.buffer_id or value.access.offset_bytes != target.offset_bytes
                        or value.access.size_bytes != footprint.storage_bytes or value.access.size_bytes > target.size_bytes):
                    raise ValueError("local compute operand must match its slot prefix and exact tensor footprint")
                extra = (inventory_id("compute", previous, "b"),) if previous else ()
                operations.append(MemoryOperation(operation_id=op, initiator_id=endpoint, kind="local_read", source=value.access,
                    source_version=value.version, depends_on=tuple(dict.fromkeys((*deps, *extra)))))
                gates.append(SyncGate(operation_id=op, after_waits=user_gate.after_waits if user_gate else (),
                    local_data=(value, *(tuple(prerequisite(p) for p in user_gate.local_data) if user_gate else ()))))
            if cost.c.storage_bytes > slot.c.size_bytes or job.output.destination.size_bytes != cost.c.storage_bytes:
                raise ValueError("compute result must fit C and match output footprint")
            result_range = slot.c.model_copy(update={"size_bytes": cost.c.storage_bytes})
            operations.append(MemoryOperation(operation_id=result_id, initiator_id=endpoint, kind="local_write",
                destination=result_range, depends_on=(*operands, *((inventory_id("compute", previous, "result"),) if previous else ()))))
            writer: str | None = None
            if job.output.mode == "local":
                if job.output.destination != result_range:
                    raise ValueError("local output must publish the exact C slot prefix")
            else:
                if job.output.mode != "write_acknowledged":
                    raise ValueError("mixed compute signals require local or acknowledged output")
                aliases = [e for e in worker.endpoint_ids if endpoints[e].fabric_id == job.output.fabric_id]
                if len(aliases) != 1:
                    raise ValueError("compute output needs its declared fabric interface")
                writer = inventory_id("compute", job.job_id, "writer")
                operations.append(MemoryOperation(operation_id=writer, initiator_id=aliases[0], fabric_id=job.output.fabric_id,
                    kind="write_acknowledged", source=result_range, source_version=MemoryVersion(kind="producer", producer_id=result_id),
                    destination=job.output.destination, depends_on=(result_id,)))
            operations.append(MemoryOperation(operation_id=job.job_id, initiator_id=endpoint, kind="fence",
                fence_mode="remote_completion", fence_operations=(writer or result_id,), depends_on=(writer or result_id, *((previous,) if previous else ()))))
            jobs.append(MixedComputeJobPlan(job_id=job.job_id, stream_id=stream.stream_id, worker_tile_id=worker.tile_id,
                resource_id=worker.l1_resource_id, slot_id=slot.slot_id, generation=index // len(stream.slots), cost=cost,
                operands=operands, result_operation=result_id, writer_operation=writer,
                compute_after=inventory_id("compute", previous, "result") if previous else None, writer_after=previous))
    generated = {o.operation_id for o in operations}
    if len(generated) != len(operations) or generated & original_ids:
        raise ValueError("compute lowering identity collides with declared operation")
    # Result consumers hold a generation until their real read has completed.
    for n, job in enumerate(jobs):
        consumers = tuple(o.operation_id for o in (*workload.operations, *operations)
                          if o.source_version is not None and o.source_version.producer_id == job.result_operation
                          and o.operation_id != job.writer_operation)
        jobs[n] = job.model_copy(update={"consumers": consumers})
        for later in jobs:
            if later.slot_id == job.slot_id and later.generation == job.generation + 1:
                for i, operation in enumerate(operations):
                    if operation.operation_id in later.operands:
                        operations[i] = operation.model_copy(update={"depends_on": tuple(dict.fromkeys((*operation.depends_on, *consumers)))})
    return MixedComputePlan(jobs=tuple(jobs), operations=tuple(operations), gates=tuple(gates))

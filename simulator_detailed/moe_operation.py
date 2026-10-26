"""Source-backed MoE operation graph for the two ADAS kernel profiles.

This module is deliberately timing-free.  It records the real communication
shapes, 8x4 PE ownership and dependency protocol; :mod:`moe_runtime` maps the
graph onto the existing packet/FIFO/multicast runtime with caller-supplied
costs.  No silicon latency or bandwidth value is embedded here.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Literal, Mapping, Sequence


ProfileName = Literal["standalone", "transformer-fused"]
Primitive = Literal[
    "point_to_point",
    "masked_multicast",
    "chain_reduce",
    "gm_write_sum",
    "compute",
    "signal",
    "wait",
    "adalink_collective",
]


@dataclass(frozen=True)
class MoeProfile:
    name: ProfileName
    hidden_size: int
    expert_dim: int
    ep_size: int
    tokens: int = 32
    topk: int = 8
    experts: int = 384
    pe_rows: int = 8
    pe_cols: int = 4
    router_partial_bytes: int = 8 * 384 * 4
    metadata_entry_bytes: int = 12
    metadata_buffer_bytes: int = 3 * 32 * 8 * 4
    valid_count_vector_bytes: int = 32 * 4
    gate_up_k_per_pe: int = 0
    gate_up_n_per_pe: int = 0
    down_n_per_pe: int = 0
    down_activation_bytes_per_root_per_v: int = 0
    fused_input_resident: bool = False

    @property
    def pe_count(self) -> int:
        return self.pe_rows * self.pe_cols

    @property
    def local_experts(self) -> int:
        return self.experts // self.ep_size

    @property
    def quant_upload_bytes_per_pe(self) -> int:
        return self.topk * (self.gate_up_k_per_pe + 128)

    @property
    def rms_bytes_per_pe(self) -> int:
        return self.hidden_size * 2

    @property
    def activation_bytes_per_pe(self) -> int:
        return self.hidden_size * 4

    @property
    def gate_reduce_bytes_per_v(self) -> int:
        return self.gate_up_n_per_pe * 4

    @property
    def down_reduce_bytes_per_v(self) -> int:
        return self.down_n_per_pe * 4

    @property
    def output_bytes(self) -> int:
        return self.tokens * self.hidden_size * 4


PROFILES: dict[ProfileName, MoeProfile] = {
    "standalone": MoeProfile(
        name="standalone",
        hidden_size=3072,
        expert_dim=1280,
        ep_size=8,
        gate_up_k_per_pe=512,
        gate_up_n_per_pe=640,
        down_n_per_pe=384,
        down_activation_bytes_per_root_per_v=640,
        fused_input_resident=False,
    ),
    "transformer-fused": MoeProfile(
        name="transformer-fused",
        hidden_size=2048,
        expert_dim=1024,
        ep_size=4,
        gate_up_k_per_pe=256,
        gate_up_n_per_pe=512,
        down_n_per_pe=256,
        down_activation_bytes_per_root_per_v=384,
        fused_input_resident=True,
    ),
}


@dataclass(frozen=True)
class RouteSelection:
    token_id: int
    local_expert_id: int
    score: float = 1.0


@dataclass(frozen=True)
class MoeTransaction:
    transaction_id: str
    stage_id: str
    primitive: Primitive
    dependencies: tuple[str, ...]
    resource: str
    participants: tuple[str, ...]
    payload_bytes: int = 0
    logical_source_bytes: int = 0
    endpoint_delivered_bytes: int = 0
    source: str | None = None
    destination: str | None = None
    multicast_group: str | None = None
    address: str | None = None
    counter_id: str | None = None
    counter_delta: int | None = None
    counter_threshold: int | None = None
    cost_kind: str | None = None
    work_items: int = 0
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MoeStage:
    stage_id: str
    source_stage: str
    label: str
    dependencies: tuple[str, ...]
    transaction_ids: tuple[str, ...]
    sync_semantics: tuple[str, ...]
    description: str
    skipped_reason: str | None = None


@dataclass(frozen=True)
class MoeOperationPlan:
    schema_version: int
    profile: MoeProfile
    ep_rank: int
    valid_selection_count: int
    route_mode: str
    routes: tuple[RouteSelection, ...]
    stages: tuple[MoeStage, ...]
    transactions: tuple[MoeTransaction, ...]
    counters: Mapping[str, int]
    include_allreduce: bool

    def transaction(self, transaction_id: str) -> MoeTransaction:
        return next(t for t in self.transactions if t.transaction_id == transaction_id)

    def to_dict(self) -> dict[str, Any]:
        multicast: dict[str, list[MoeTransaction]] = defaultdict(list)
        primitive_counts: Counter[str] = Counter()
        source_bytes: Counter[str] = Counter()
        endpoint_bytes: Counter[str] = Counter()
        for tx in self.transactions:
            primitive_counts[tx.primitive] += 1
            source_bytes[tx.primitive] += tx.logical_source_bytes
            endpoint_bytes[tx.primitive] += tx.endpoint_delivered_bytes
            if tx.multicast_group:
                multicast[tx.multicast_group].append(tx)
        multicast_groups = []
        for group_id, members in sorted(multicast.items()):
            multicast_groups.append({
                "group_id": group_id,
                "source": members[0].source,
                "payload_bytes": members[0].payload_bytes,
                "fanout": len(members),
                "destinations": [m.destination for m in members],
                "logical_source_transactions": 1,
                "physical_endpoint_transactions": len(members),
            })
        tx_rows = []
        for tx in self.transactions:
            row = asdict(tx)
            row["attributes"] = dict(tx.attributes)
            tx_rows.append(row)
        return {
            "schema_version": self.schema_version,
            "profile": asdict(self.profile),
            "ep_rank": self.ep_rank,
            "valid_selection_count": self.valid_selection_count,
            "route_mode": self.route_mode,
            "routes": [asdict(route) for route in self.routes],
            "token_histogram": dict(sorted(Counter(r.token_id for r in self.routes).items())),
            "expert_histogram": dict(sorted(Counter(r.local_expert_id for r in self.routes).items())),
            "stages": [asdict(stage) for stage in self.stages],
            "transactions": tx_rows,
            "counters": dict(sorted(self.counters.items())),
            "include_allreduce": self.include_allreduce,
            "transaction_accounting": {
                "logical_by_primitive": dict(sorted(primitive_counts.items())),
                "logical_source_bytes_by_primitive": dict(sorted(source_bytes.items())),
                "endpoint_delivered_bytes_by_primitive": dict(sorted(endpoint_bytes.items())),
                "multicast_groups": multicast_groups,
                "definitions": {
                    "masked_multicast": "one logical source transaction per group; one endpoint member per destination",
                    "physical_packets": "available only after runtime packet lowering",
                },
            },
            "invariants": {
                "pe_grid": [self.profile.pe_rows, self.profile.pe_cols],
                "cooperative_pes_per_valid_selection": self.profile.pe_count,
                "token_all_to_all_present": False,
                "weight_sram_gather_is_data_noc": False,
                "adalink_is_data_noc": False,
            },
        }


class _Builder:
    def __init__(self, profile: MoeProfile, ep_rank: int, routes: Sequence[RouteSelection], route_mode: str, include_allreduce: bool):
        self.profile = profile
        self.ep_rank = ep_rank
        self.routes = tuple(routes)
        self.route_mode = route_mode
        self.include_allreduce = include_allreduce
        self._transactions: list[MoeTransaction] = []
        self._stage_defs: list[dict[str, Any]] = []
        self._stage_transactions: dict[str, list[str]] = defaultdict(list)
        self._counters: dict[str, int] = {}
        self._engine_tail: dict[str, str] = {}

    def stage(self, stage_id: str, source_stage: str, label: str, dependencies: Iterable[str], sync: Iterable[str], description: str, skipped_reason: str | None = None) -> None:
        self._stage_defs.append({
            "stage_id": stage_id,
            "source_stage": source_stage,
            "label": label,
            "dependencies": tuple(dependencies),
            "sync_semantics": tuple(sync),
            "description": description,
            "skipped_reason": skipped_reason,
        })

    def add(self, tx: MoeTransaction, *, fifo_engine: str | None = None) -> str:
        deps = list(tx.dependencies)
        if fifo_engine and fifo_engine in self._engine_tail and self._engine_tail[fifo_engine] not in deps:
            deps.append(self._engine_tail[fifo_engine])
        if fifo_engine:
            self._engine_tail[fifo_engine] = tx.transaction_id
        if tuple(deps) != tx.dependencies:
            tx = MoeTransaction(**{**asdict(tx), "dependencies": tuple(deps), "attributes": dict(tx.attributes)})
        if any(old.transaction_id == tx.transaction_id for old in self._transactions):
            raise ValueError(f"duplicate transaction {tx.transaction_id}")
        self._transactions.append(tx)
        self._stage_transactions[tx.stage_id].append(tx.transaction_id)
        return tx.transaction_id

    def compute(self, stage: str, txid: str, resource: str, deps: Iterable[str], participants: Iterable[str], cost_kind: str, work_items: int, **attributes: Any) -> str:
        return self.add(MoeTransaction(
            transaction_id=txid,
            stage_id=stage,
            primitive="compute",
            dependencies=tuple(deps),
            resource=resource,
            participants=tuple(participants),
            cost_kind=cost_kind,
            work_items=work_items,
            attributes=attributes,
        ), fifo_engine=resource)

    def transfer(self, stage: str, txid: str, primitive: Primitive, source: str, destination: str, payload: int, deps: Iterable[str], participants: Iterable[str], *, multicast_group: str | None = None, address: str | None = None, fifo_engine: str | None = None, **attributes: Any) -> str:
        if payload <= 0:
            raise ValueError("transfer payload must be positive")
        endpoint_bytes = payload
        source_bytes = 0 if multicast_group else payload
        tx = MoeTransaction(
            transaction_id=txid,
            stage_id=stage,
            primitive=primitive,
            dependencies=tuple(deps),
            resource="data_noc/channel1",
            participants=tuple(participants),
            payload_bytes=payload,
            logical_source_bytes=source_bytes,
            endpoint_delivered_bytes=endpoint_bytes,
            source=source,
            destination=destination,
            multicast_group=multicast_group,
            address=address,
            attributes=attributes,
        )
        return self.add(tx, fifo_engine=fifo_engine)

    def multicast(self, stage: str, group: str, source: str, destinations: Sequence[str], payload: int, deps: Iterable[str], **attributes: Any) -> dict[str, str]:
        if len(set(destinations)) != len(destinations) or not destinations:
            raise ValueError("multicast destinations must be non-empty and unique")
        result: dict[str, str] = {}
        common_deps = tuple(deps)
        for destination in destinations:
            suffix = destination.replace("/", "-")
            txid = f"{group}.to.{suffix}"
            result[destination] = self.transfer(
                stage,
                txid,
                "masked_multicast",
                source,
                destination,
                payload,
                common_deps,
                (source, destination),
                multicast_group=group,
                fanout=len(destinations),
                **attributes,
            )
        # Exactly one member owns logical source bytes; endpoint bytes remain per member.
        first_id = result[destinations[0]]
        for index, tx in enumerate(self._transactions):
            if tx.transaction_id == first_id:
                self._transactions[index] = MoeTransaction(**{
                    **asdict(tx),
                    "logical_source_bytes": payload,
                    "attributes": dict(tx.attributes),
                })
                break
        return result

    def signal(self, stage: str, txid: str, counter: str, deps: Iterable[str], participant: str, *, delta: int = 1, **attributes: Any) -> str:
        return self.add(MoeTransaction(
            transaction_id=txid,
            stage_id=stage,
            primitive="signal",
            dependencies=tuple(deps),
            resource=f"counter/{counter}",
            participants=(participant,),
            counter_id=counter,
            counter_delta=delta,
            attributes=attributes,
        ))

    def wait(self, stage: str, txid: str, counter: str, threshold: int, deps: Iterable[str] = (), **attributes: Any) -> str:
        self._counters[counter] = max(self._counters.get(counter, 0), threshold)
        return self.add(MoeTransaction(
            transaction_id=txid,
            stage_id=stage,
            primitive="wait",
            dependencies=tuple(deps),
            resource=f"counter/{counter}",
            participants=("after-all",),
            counter_id=counter,
            counter_threshold=threshold,
            attributes=attributes,
        ))

    def after_all(self, stage: str, stem: str, inputs: Sequence[tuple[str, str]], **attributes: Any) -> str:
        if not inputs:
            raise ValueError("after-all requires at least one participant")
        counter = f"{stem}.counter"
        for index, (participant, dependency) in enumerate(inputs):
            self.signal(stage, f"{stem}.arrive.{index:03d}", counter, (dependency,), participant, expected=len(inputs), **attributes)
        return self.wait(stage, f"{stem}.wait", counter, len(inputs), (), expected=len(inputs), **attributes)

    def build(self) -> MoeOperationPlan:
        stages = tuple(MoeStage(
            transaction_ids=tuple(self._stage_transactions.get(row["stage_id"], ())),
            **row,
        ) for row in self._stage_defs)
        return MoeOperationPlan(
            schema_version=1,
            profile=self.profile,
            ep_rank=self.ep_rank,
            valid_selection_count=len(self.routes),
            route_mode=self.route_mode,
            routes=self.routes,
            stages=stages,
            transactions=tuple(self._transactions),
            counters=dict(self._counters),
            include_allreduce=self.include_allreduce,
        )


def _routes(profile: MoeProfile, valid_selection_count: int, route_mode: str, hot_token: int, explicit: Sequence[RouteSelection] | None) -> tuple[RouteSelection, ...]:
    if type(valid_selection_count) is not int or not 0 <= valid_selection_count <= profile.tokens * profile.topk:
        raise ValueError("V must be an integer in [0, tokens*topk]")
    if explicit is not None:
        if route_mode != "explicit":
            raise ValueError("explicit routes require route_mode='explicit'")
        result = tuple(explicit)
        if len(result) != valid_selection_count:
            raise ValueError("V does not match explicit route count")
    elif route_mode == "uniform":
        result = tuple(RouteSelection(
            token_id=index % profile.tokens,
            local_expert_id=(index * 7 + index // profile.tokens) % profile.local_experts,
            score=1.0 - (index % profile.topk) / 100.0,
        ) for index in range(valid_selection_count))
    elif route_mode == "single-token-hotspot":
        if not 0 <= hot_token < profile.tokens:
            raise ValueError("hot_token is outside token range")
        if valid_selection_count > profile.topk:
            raise ValueError("a single token cannot have more than topk valid selections")
        result = tuple(RouteSelection(hot_token, index % profile.local_experts, 1.0 - index / 100.0) for index in range(valid_selection_count))
    else:
        raise ValueError("route_mode must be uniform, single-token-hotspot or explicit")
    seen: set[tuple[int, int]] = set()
    token_counts: Counter[int] = Counter()
    for route in result:
        if type(route.token_id) is not int or not 0 <= route.token_id < profile.tokens:
            raise ValueError("route token_id is outside profile")
        if type(route.local_expert_id) is not int or not 0 <= route.local_expert_id < profile.local_experts:
            raise ValueError("route local_expert_id is outside EP rank")
        if type(route.score) not in (int, float) or not 0.0 <= float(route.score) <= 1.0:
            raise ValueError("route score must be in [0,1]")
        pair = (route.token_id, route.local_expert_id)
        if pair in seen:
            raise ValueError("duplicate token/expert route")
        seen.add(pair)
        token_counts[route.token_id] += 1
        if token_counts[route.token_id] > profile.topk:
            raise ValueError("token route count exceeds topk")
    return result


def _pe(pe: int) -> str:
    return f"pe/{pe:02d}"


def _gm(kind: str, index: int) -> str:
    return f"gm-{kind}/{index}"


def build_moe_operation_plan(
    profile: ProfileName | MoeProfile,
    valid_selection_count: int,
    *,
    route_mode: Literal["uniform", "single-token-hotspot", "explicit"] = "uniform",
    explicit_routes: Sequence[RouteSelection] | None = None,
    hot_token: int = 0,
    ep_rank: int = 0,
    include_allreduce: bool = False,
) -> MoeOperationPlan:
    """Build the timing-free operation graph.

    ``valid_selection_count`` is V: the effective token/expert selections that
    hit this EP rank.  Explicit routes use local expert ids after EP filtering.
    """
    p = PROFILES[profile] if isinstance(profile, str) else profile
    if not isinstance(p, MoeProfile):
        raise TypeError("profile must be a known name or MoeProfile")
    if type(ep_rank) is not int or not 0 <= ep_rank < p.ep_size:
        raise ValueError("ep_rank is outside profile")
    routes = _routes(p, valid_selection_count, route_mode, hot_token, explicit_routes)
    b = _Builder(p, ep_rank, routes, route_mode, include_allreduce)

    # R0: standalone pulls token activations; fused receives them from the bridge.
    b.stage("r0.router_input", "R0", "router input", (), ("outer-sync", "after-all"), "standalone GM point-to-point pull or fused local bridge handoff")
    input_ready: dict[int, str] = {}
    input_arrivals: list[tuple[str, str]] = []
    if p.fused_input_resident:
        for pe in range(p.pe_count):
            txid = b.signal("r0.router_input", f"r0.bridge.ready.{pe:02d}", "r0.bridge.counter", (), _pe(pe), bridge_resident=True)
            b._counters["r0.bridge.counter"] = p.pe_count
            input_ready[pe] = txid
            input_arrivals.append((_pe(pe), txid))
        input_join = b.wait("r0.router_input", "r0.bridge.wait", "r0.bridge.counter", p.pe_count, (), protocol="bridge-after-all")
    else:
        for pe in range(p.pe_count):
            txid = b.transfer("r0.router_input", f"r0.activation.{pe:02d}", "point_to_point", _gm("rdma", pe // 8), _pe(pe), p.activation_bytes_per_pe, (), (_gm("rdma", pe // 8), _pe(pe)), activation=True)
            input_ready[pe] = txid
            input_arrivals.append((_pe(pe), txid))
        input_join = b.after_all("r0.router_input", "r0.activation.after-all", input_arrivals, sync="outer")

    # Fused output preseed is real Data NoC traffic, but it is not router input.
    preseed_join: str | None = None
    if p.fused_input_resident:
        b.stage("r0b.residual_preseed", "R0b", "fused residual preseed", ("r0.router_input",), ("outer-sync", "after-all"), "PE residual upload initializes the GM output buffer")
        preseed: list[tuple[str, str]] = []
        for pe in range(p.pe_count):
            txid = b.transfer("r0b.residual_preseed", f"r0b.preseed.{pe:02d}", "point_to_point", _pe(pe), _gm("wdma", pe // 8), p.activation_bytes_per_pe, (input_ready[pe],), (_pe(pe), _gm("wdma", pe // 8)), fifo_engine=f"{_pe(pe)}/upload1", residual_preseed=True)
            preseed.append((_pe(pe), txid))
        preseed_join = b.after_all("r0b.residual_preseed", "r0b.preseed.after-all", preseed, sync="outer")

    # R1-R5: RMS/quant engines, gather, and actual masked multicast.
    b.stage("r1.rmsnorm", "R1", "RMSNorm", ("r0.router_input",), ("inner-sync", "engine-fifo"), "per-PE Vector RMSNorm")
    rms: dict[int, str] = {}
    for pe in range(p.pe_count):
        rms[pe] = b.compute("r1.rmsnorm", f"r1.rmsnorm.{pe:02d}", f"{_pe(pe)}/vector", (input_ready[pe],), (_pe(pe),), "rmsnorm", p.hidden_size, hidden_size=p.hidden_size)

    b.stage("r1.quant", "R1", "activation quant", ("r1.rmsnorm",), ("inner-sync", "engine-fifo"), "per-PE int8 quantization and scale generation")
    quant: dict[int, str] = {}
    for pe in range(p.pe_count):
        quant[pe] = b.compute("r1.quant", f"r1.quant.{pe:02d}", f"{_pe(pe)}/vector", (rms[pe],), (_pe(pe),), "activation_quant", p.hidden_size, hidden_size=p.hidden_size)

    b.stage("r2.rms_gather", "R2", "RMS gather", ("r1.rmsnorm",), ("inner-sync", "outer-sync", "engine-fifo"), "32 PE-to-GM point-to-point uploads")
    rms_upload: dict[int, str] = {}
    for pe in range(p.pe_count):
        rms_upload[pe] = b.transfer("r2.rms_gather", f"r2.rms.upload.{pe:02d}", "point_to_point", _pe(pe), _gm("wdma", pe // 8), p.rms_bytes_per_pe, (rms[pe],), (_pe(pe), _gm("wdma", pe // 8)), fifo_engine=f"{_pe(pe)}/upload1", gather="rms")

    b.stage("r4.quant_gather", "R4", "quant gather", ("r1.quant", "r2.rms_gather"), ("inner-sync", "outer-sync", "engine-fifo", "after-all"), "32 PE-to-GM uploads; per-PE Upload-1 follows RMS upload")
    quant_upload: dict[int, str] = {}
    gather_inputs: list[tuple[str, str]] = []
    for pe in range(p.pe_count):
        quant_upload[pe] = b.transfer("r4.quant_gather", f"r4.quant.upload.{pe:02d}", "point_to_point", _pe(pe), _gm("wdma", pe // 8), p.quant_upload_bytes_per_pe, (quant[pe],), (_pe(pe), _gm("wdma", pe // 8)), fifo_engine=f"{_pe(pe)}/upload1", gather="quant")
        gather_inputs.extend(((f"{_pe(pe)}:rms", rms_upload[pe]), (f"{_pe(pe)}:quant", quant_upload[pe])))
    gather_join = b.after_all("r4.quant_gather", "r4.gather.after-all", gather_inputs, sync="wdma-to-rdma", count=64)

    b.stage("r5.rms_multicast", "R5", "RMS masked multicast", ("r4.quant_gather",), ("outer-sync", "masked-multicast"), "four column groups, fanout eight")
    rms_mc: dict[int, str] = {}
    for col in range(p.pe_cols):
        destinations = [_pe(row * p.pe_cols + col) for row in range(p.pe_rows)]
        members = b.multicast("r5.rms_multicast", f"r5.rms.mc.c{col}", _gm("rdma", col), destinations, p.pe_rows * p.rms_bytes_per_pe, (gather_join,), mask="strided-column")
        for row in range(p.pe_rows):
            rms_mc[row * p.pe_cols + col] = members[_pe(row * p.pe_cols + col)]

    b.stage("r5.quant_multicast", "R5", "quant masked multicast", ("r5.rms_multicast",), ("outer-sync", "masked-multicast", "download-engine-fifo"), "eight contiguous row groups, fanout four")
    quant_mc: dict[int, str] = {}
    for row in range(p.pe_rows):
        destinations = [_pe(row * p.pe_cols + col) for col in range(p.pe_cols)]
        previous_downloads = tuple(rms_mc[row * p.pe_cols + col] for col in range(p.pe_cols))
        members = b.multicast("r5.quant_multicast", f"r5.quant.mc.r{row}", _gm("rdma", row // 2), destinations, p.pe_cols * p.quant_upload_bytes_per_pe, previous_downloads, mask="contiguous-row")
        for col in range(p.pe_cols):
            quant_mc[row * p.pe_cols + col] = members[_pe(row * p.pe_cols + col)]

    # R6-R9: router GEMM, column chain, broadcast, top-k fence and EP filter.
    b.stage("r6.router_gemm", "R6", "router GEMM", ("r5.rms_multicast",), ("inner-sync", "matrix-engine-fifo"), "32 bf16 partial GEMMs over eight tokens")
    router: dict[int, str] = {}
    for pe in range(p.pe_count):
        router[pe] = b.compute("r6.router_gemm", f"r6.router.{pe:02d}", f"{_pe(pe)}/matrix", (rms_mc[pe],), (_pe(pe),), "router_gemm", 8 * p.experts * (p.hidden_size // p.pe_rows), M=8, N=p.experts, K=p.hidden_size // p.pe_rows)

    b.stage("r7.router_col_reduce", "R7", "router column chain reduce", ("r6.router_gemm",), ("outer-sync", "chain-reduce", "engine-fifo"), "four parallel eight-PE chains")
    router_root: dict[int, str] = {}
    for col in range(p.pe_cols):
        previous: str | None = None
        for row in range(p.pe_rows - 1):
            source_pe = row * p.pe_cols + col
            destination_pe = (row + 1) * p.pe_cols + col
            deps = [router[source_pe], router[destination_pe]]
            if previous:
                deps.append(previous)
            previous = b.transfer("r7.router_col_reduce", f"r7.router.reduce.c{col}.h{row}", "chain_reduce", _pe(source_pe), _pe(destination_pe), p.router_partial_bytes, deps, (_pe(source_pe), _pe(destination_pe)), reduce_op="sum", chain="column")
        assert previous is not None
        router_root[col] = previous

    b.stage("r7.router_col_broadcast", "R7", "router column broadcast", ("r7.router_col_reduce",), ("outer-sync", "masked-multicast"), "four roots multicast to seven non-root PEs")
    router_bcast: dict[int, str] = {}
    for col in range(p.pe_cols):
        root_pe = (p.pe_rows - 1) * p.pe_cols + col
        router_bcast[root_pe] = router_root[col]
        destinations = [_pe(row * p.pe_cols + col) for row in range(p.pe_rows - 1)]
        members = b.multicast("r7.router_col_broadcast", f"r7.router.bc.c{col}", _pe(root_pe), destinations, p.router_partial_bytes, (router_root[col],), root=root_pe)
        for row in range(p.pe_rows - 1):
            router_bcast[row * p.pe_cols + col] = members[_pe(row * p.pe_cols + col)]

    b.stage("r8.softmax_topk", "R8", "softmax and top-k", ("r7.router_col_broadcast",), ("inner-sync", "vector-engine-fifo"), "stable softmax and global top-k=8")
    topk: dict[int, str] = {}
    for pe in range(p.pe_count):
        topk[pe] = b.compute("r8.softmax_topk", f"r8.topk.{pe:02d}", f"{_pe(pe)}/vector", (router_bcast[pe],), (_pe(pe),), "softmax_topk", p.experts, experts=p.experts, topk=p.topk)

    b.stage("r8.topk_fence", "R8", "top-k scalar fence", ("r8.softmax_topk",), ("fence",), "calculation fence before scalar metadata access")
    topk_fence: dict[int, str] = {}
    for pe in range(p.pe_count):
        topk_fence[pe] = b.signal("r8.topk_fence", f"r8.fence.{pe:02d}", "r8.fence.counter", (topk[pe],), _pe(pe), fence="calc")
    b._counters["r8.fence.counter"] = p.pe_count

    b.stage("r9.ep_filter", "R9", "EP filter", ("r8.topk_fence",), ("vector-scalar",), "filter global experts to the current EP rank and count valid selections")
    routes_by_token: dict[int, list[RouteSelection]] = defaultdict(list)
    for route in routes:
        routes_by_token[route.token_id].append(route)
    ep_filter: dict[int, str] = {}
    for pe in range(p.pe_count):
        ep_filter[pe] = b.compute("r9.ep_filter", f"r9.filter.{pe:02d}", f"{_pe(pe)}/vector", (topk_fence[pe],), (_pe(pe),), "ep_filter", p.topk, valid_count=len(routes_by_token[pe]), ep_rank=ep_rank)

    # R10-R12: count write-sum, count broadcast and variable metadata allgather.
    b.stage("r10.valid_count", "R10", "valid-count prefix protocol", ("r9.ep_filter",), ("outer-sync", "gm-write-sum", "counted-after-all"), "32 same-address 128-byte write-sum vectors followed by fanout-32 broadcast")
    count_upload: dict[int, str] = {}
    count_arrivals: list[tuple[str, str]] = []
    for pe in range(p.pe_count):
        count_upload[pe] = b.transfer("r10.valid_count", f"r10.count.upload.{pe:02d}", "gm_write_sum", _pe(pe), _gm("wdma", 0), p.valid_count_vector_bytes, (ep_filter[pe],), (_pe(pe), _gm("wdma", 0)), address="valid-count-prefix", fifo_engine=f"{_pe(pe)}/upload1", write_sum=True)
        count_arrivals.append((_pe(pe), count_upload[pe]))
    count_join = b.after_all("r10.valid_count", "r10.count.after-all", count_arrivals, sync="gm-write-sum")
    count_bcast_members = b.multicast("r10.valid_count", "r10.count.bc", _gm("rdma", 0), [_pe(pe) for pe in range(p.pe_count)], p.valid_count_vector_bytes, (count_join,), mask="all-pe")
    count_bcast = {pe: count_bcast_members[_pe(pe)] for pe in range(p.pe_count)}

    b.stage("r11.metadata", "R11", "top-k metadata protocol", ("r10.valid_count",), ("fence", "outer-sync", "counted-after-all", "masked-multicast"), "variable disjoint uploads and fixed 3x256 metadata broadcast")
    metadata_arrivals: list[tuple[str, str]] = []
    for pe in range(p.pe_count):
        count = len(routes_by_token[pe])
        dependency = count_bcast[pe]
        if count:
            dependency = b.transfer("r11.metadata", f"r11.meta.upload.{pe:02d}", "point_to_point", _pe(pe), _gm("wdma", 0), count * p.metadata_entry_bytes, (dependency,), (_pe(pe), _gm("wdma", 0)), fifo_engine=f"{_pe(pe)}/upload1", disjoint_offset=True, entries=count)
        metadata_arrivals.append((_pe(pe), dependency))
    metadata_join = b.after_all("r11.metadata", "r11.meta.after-all", metadata_arrivals, sync="wdma-to-rdma", variable_bytes=12 * len(routes))
    metadata_members = b.multicast("r11.metadata", "r11.meta.bc", _gm("rdma", 0), [_pe(pe) for pe in range(p.pe_count)], p.metadata_buffer_bytes, (metadata_join,), mask="all-pe")
    metadata_bcast = {pe: metadata_members[_pe(pe)] for pe in range(p.pe_count)}

    b.stage("r12.expert_fence", "R12", "expert-entry fence", ("r11.metadata", "r5.quant_multicast"), ("fence", "inner-sync"), "per-PE download fence before expert scalar/table access")
    expert_fence: dict[int, str] = {}
    for pe in range(p.pe_count):
        expert_fence[pe] = b.signal("r12.expert_fence", f"r12.fence.{pe:02d}", "r12.fence.counter", (metadata_bcast[pe], quant_mc[pe]), _pe(pe), fence="download1")
    b._counters["r12.fence.counter"] = p.pe_count

    v = len(routes)
    expert_skipped = "V=0: expert body has no effective selection" if v == 0 else None
    b.stage("e0.gate_up", "E0", "gate-up W4A8 GEMM", ("r12.expert_fence",), ("matrix-engine-fifo",), "all 32 PEs cooperate on the same V selections", expert_skipped)
    gate: dict[int, str] = {}
    if v:
        for pe in range(p.pe_count):
            gate[pe] = b.compute("e0.gate_up", f"e0.gate.{pe:02d}", f"{_pe(pe)}/matrix", (expert_fence[pe],), (_pe(pe),), "gate_up_w4a8", v, V=v, cooperative_pes=p.pe_count, M=1, N=p.gate_up_n_per_pe, K=p.gate_up_k_per_pe, weight_access="weight_sram_table_gather", weight_data_noc=False)

    b.stage("e1.gate_reduce", "E1", "gate-up column reduce", ("e0.gate_up",), ("outer-sync", "chain-reduce"), "four parallel eight-PE chains", expert_skipped)
    gate_root: dict[int, str] = {}
    if v:
        for col in range(p.pe_cols):
            previous = None
            for row in range(p.pe_rows - 1):
                source_pe = row * p.pe_cols + col
                destination_pe = (row + 1) * p.pe_cols + col
                deps = [gate[source_pe], gate[destination_pe]]
                if previous:
                    deps.append(previous)
                previous = b.transfer("e1.gate_reduce", f"e1.gate.reduce.c{col}.h{row}", "chain_reduce", _pe(source_pe), _pe(destination_pe), v * p.gate_reduce_bytes_per_v, deps, (_pe(source_pe), _pe(destination_pe)), reduce_op="sum", chain="column", V=v)
            assert previous is not None
            gate_root[col] = previous

    b.stage("e2.silu_quant", "E2", "SiLU, score fold and quant", ("e1.gate_reduce",), ("inner-sync", "root-only-vector"), "only PE28..31 execute this stage", expert_skipped)
    silu: dict[int, str] = {}
    if v:
        for col in range(p.pe_cols):
            root_pe = (p.pe_rows - 1) * p.pe_cols + col
            silu[col] = b.compute("e2.silu_quant", f"e2.silu.c{col}", f"{_pe(root_pe)}/vector", (gate_root[col],), (_pe(root_pe),), "silu_score_quant", v, V=v, root_only=True)

    b.stage("e3.down_activation", "E3", "down activation multicast", ("e2.silu_quant",), ("outer-sync", "masked-multicast"), "four root PEs multicast one K shard to seven peers", expert_skipped)
    down_activation: dict[int, str] = {}
    if v:
        for col in range(p.pe_cols):
            root_pe = (p.pe_rows - 1) * p.pe_cols + col
            down_activation[root_pe] = silu[col]
            destinations = [_pe(row * p.pe_cols + col) for row in range(p.pe_rows - 1)]
            members = b.multicast("e3.down_activation", f"e3.down.mc.c{col}", _pe(root_pe), destinations, v * p.down_activation_bytes_per_root_per_v, (silu[col],), V=v, root=root_pe)
            for row in range(p.pe_rows - 1):
                down_activation[row * p.pe_cols + col] = members[_pe(row * p.pe_cols + col)]

    b.stage("e4.down_gemm", "E4", "down W4A8 GEMM", ("e3.down_activation",), ("inner-sync", "matrix-engine-fifo"), "all 32 PEs use Weight-SRAM table gather; no weight Data NoC transfer", expert_skipped)
    down: dict[int, str] = {}
    if v:
        for pe in range(p.pe_count):
            down[pe] = b.compute("e4.down_gemm", f"e4.down.{pe:02d}", f"{_pe(pe)}/matrix", (down_activation[pe],), (_pe(pe),), "down_w4a8", v, V=v, cooperative_pes=p.pe_count, M=1, N=p.down_n_per_pe, K=p.gate_up_k_per_pe, weight_access="weight_sram_table_gather", weight_data_noc=False)

    b.stage("e5.down_reduce", "E5", "down row reduce", ("e4.down_gemm",), ("outer-sync", "chain-reduce"), "eight parallel four-PE chains", expert_skipped)
    row_root: dict[int, str] = {}
    if v:
        for row in range(p.pe_rows):
            previous = None
            for col in range(p.pe_cols - 1):
                source_pe = row * p.pe_cols + col
                destination_pe = row * p.pe_cols + col + 1
                deps = [down[source_pe], down[destination_pe]]
                if previous:
                    deps.append(previous)
                previous = b.transfer("e5.down_reduce", f"e5.down.reduce.r{row}.h{col}", "chain_reduce", _pe(source_pe), _pe(destination_pe), v * p.down_reduce_bytes_per_v, deps, (_pe(source_pe), _pe(destination_pe)), reduce_op="sum", chain="row", V=v)
            assert previous is not None
            row_root[row] = previous

    b.stage("e5.down_broadcast", "E5", "down row broadcast", ("e5.down_reduce",), ("outer-sync", "masked-multicast"), "eight row roots multicast to three peers", expert_skipped)
    row_ready: dict[int, str] = {}
    if v:
        for row in range(p.pe_rows):
            root_pe = row * p.pe_cols + p.pe_cols - 1
            row_ready[root_pe] = row_root[row]
            destinations = [_pe(row * p.pe_cols + col) for col in range(p.pe_cols - 1)]
            members = b.multicast("e5.down_broadcast", f"e5.down.bc.r{row}", _pe(root_pe), destinations, v * p.down_reduce_bytes_per_v, (row_root[row],), V=v, root=root_pe)
            for col in range(p.pe_cols - 1):
                row_ready[row * p.pe_cols + col] = members[_pe(row * p.pe_cols + col)]

    b.stage("e6.output_scatter", "E6", "GM write-sum scatter", ("e5.down_broadcast", "r0b.residual_preseed") if preseed_join else ("e5.down_broadcast",), ("upload-engine-fifo", "gm-write-sum", "address-conflict"), "selection i is written by column i mod 4; eight row shards per selection", expert_skipped)
    writes_by_address: dict[str, list[str]] = defaultdict(list)
    writes_by_pe: dict[int, list[str]] = defaultdict(list)
    address_contributors: dict[str, set[int]] = defaultdict(set)
    if v:
        for index, route in enumerate(routes):
            col = index % p.pe_cols
            for row in range(p.pe_rows):
                pe = row * p.pe_cols + col
                address = f"output/token-{route.token_id:02d}/row-{row}"
                deps = [row_ready[pe]]
                if preseed_join:
                    deps.append(preseed_join)
                txid = b.transfer("e6.output_scatter", f"e6.scatter.v{index:03d}.r{row}", "gm_write_sum", _pe(pe), _gm("wdma", row // 2), p.down_reduce_bytes_per_v, deps, (_pe(pe), _gm("wdma", row // 2)), address=address, fifo_engine=f"{_pe(pe)}/upload1", write_sum=True, token_id=route.token_id, local_expert_id=route.local_expert_id, row_shard=row, controller=row // 2)
                writes_by_address[address].append(txid)
                writes_by_pe[pe].append(txid)
                address_contributors[address].add(pe)

    # The transfer remains Data NoC.  RMW/address-conflict cost is a separate,
    # explicitly uncalibrated provider item on one of four GM controllers.
    b.stage("e6.write_sum_rmw", "E6", "GM write-sum RMW effects", ("e6.output_scatter",), ("resource-fifo", "address-conflict"), "one effect per output address; multiplicity exposes token hotspots", expert_skipped)
    rmw_by_address: dict[str, str] = {}
    rmw_by_pe: dict[int, list[str]] = defaultdict(list)
    if v:
        for address, writes in sorted(writes_by_address.items()):
            row = int(address.rsplit("-", 1)[1])
            txid = b.compute("e6.write_sum_rmw", f"e6.rmw.{address.replace('/', '.')}", f"gm-write-sum/{row // 2}", tuple(writes), tuple(sorted({_pe(pe) for pe in address_contributors[address]})), "gm_write_sum", len(writes), address=address, contributors=len(writes), conflict_group=address)
            rmw_by_address[address] = txid
            for write in writes:
                pe = int(next(t.source for t in b._transactions if t.transaction_id == write).split("/")[1])
                rmw_by_pe[pe].append(txid)

    b.stage("e7.output_after_all", "E7", "32-PE output completion join", ("e6.write_sum_rmw", "r11.metadata"), ("counted-after-all", "comm-id-16"), "every PE publishes one completion token, including V=0")
    completion_inputs: list[tuple[str, str]] = []
    for pe in range(p.pe_count):
        if v:
            deps = tuple(sorted(set(rmw_by_pe.get(pe, ())))) or (row_ready[pe],)
        else:
            deps = (expert_fence[pe],)
        signal = b.signal("e7.output_after_all", f"e7.complete.{pe:02d}", "e7.complete.counter", deps, _pe(pe), comm_id=16)
        completion_inputs.append((_pe(pe), signal))
    b._counters["e7.complete.counter"] = p.pe_count
    output_join = b.wait("e7.output_after_all", "e7.complete.wait", "e7.complete.counter", p.pe_count, (), expected=p.pe_count, comm_id=16)

    final_tx = output_join
    b.stage("e8.adalink", "E8", "optional cross-rank allreduce", ("e7.output_after_all",), ("counted-after-all", "adalink-only"), "reduce-scatter, barrier and all-gather on ADALINK; never Data NoC", None if include_allreduce else "disabled by caller")
    if include_allreduce:
        final_tx = b.add(MoeTransaction(
            transaction_id="e8.adalink.allreduce",
            stage_id="e8.adalink",
            primitive="adalink_collective",
            dependencies=(output_join,),
            resource="adalink/fullmesh",
            participants=tuple(f"rank/{rank}" for rank in range(p.ep_size)),
            payload_bytes=p.output_bytes,
            logical_source_bytes=p.output_bytes,
            endpoint_delivered_bytes=p.output_bytes * p.ep_size,
            cost_kind="adalink_allreduce",
            work_items=p.output_bytes,
            attributes={"ranks": p.ep_size, "data_noc": False, "algorithm": "reduce-scatter+barrier+all-gather"},
        ))

    plan = b.build()
    # Structural gate: the only terminal must be reachable and every dependency
    # must name an earlier transaction.  This also catches accidental all-to-all
    # construction or a stage-level pseudo dependency leaking into runtime.
    order = {tx.transaction_id: index for index, tx in enumerate(plan.transactions)}
    for tx in plan.transactions:
        for dependency in tx.dependencies:
            if dependency not in order or order[dependency] >= order[tx.transaction_id]:
                raise ValueError(f"non-causal dependency {dependency}->{tx.transaction_id}")
    if final_tx not in order:
        raise ValueError("missing terminal transaction")
    return plan


__all__ = [
    "MoeOperationPlan",
    "MoeProfile",
    "MoeStage",
    "MoeTransaction",
    "PROFILES",
    "RouteSelection",
    "build_moe_operation_plan",
]

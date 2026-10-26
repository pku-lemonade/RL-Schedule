"""Phase-0 MoE workload contracts.

The records in this module are timing-free.  They freeze source-backed profile
dimensions, an exact ordered route trace, per-rank V, address-hotspot inputs,
and useful/issued FLOP accounting.  Synthetic traces are allowed only when the
caller labels them as fixtures; they are never promoted to production input.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from typing import Any, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator


ProfileName = Literal["standalone", "transformer-fused"]
TraceKind = Literal["real_decode", "synthetic_fixture"]
TraceScope = Literal["rank_local", "ep_group"]


def canonical_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def stable_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class FrozenRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MoeProfileSpec(FrozenRecord):
    name: ProfileName
    token_count: int = Field(gt=0)
    top_k: int = Field(gt=0)
    global_experts: int = Field(gt=0)
    hidden_gating: int = Field(gt=0)
    hidden_moe: int = Field(gt=0)
    expert_dim: int = Field(gt=0)
    expert_dim_down_padded: int = Field(gt=0)
    output_hidden: int = Field(gt=0)
    ep_size: int = Field(gt=0)
    experts_per_rank: int = Field(gt=0)
    pe_rows: int = 8
    pe_cols: int = 4
    router_dtype: Literal["bf16xbf16_to_fp32"] = "bf16xbf16_to_fp32"
    expert_dtype: Literal["int8xu4_group256_to_fp32"] = "int8xu4_group256_to_fp32"
    gate_up_per_pe: tuple[int, int, int]
    down_per_pe: tuple[int, int, int]
    output_buffer_bytes_per_rank: int = Field(gt=0)
    entry_mode: Literal["gm_activation_pull", "bridge_resident_with_residual_preseed"]

    @model_validator(mode="after")
    def dimensions(self) -> "MoeProfileSpec":
        if self.pe_rows * self.pe_cols != 32:
            raise ValueError("MoE profile must use the source-backed 8x4/32-PE grid")
        if self.global_experts != self.ep_size * self.experts_per_rank:
            raise ValueError("global experts must equal EP times experts_per_rank")
        gm, gn, gk = self.gate_up_per_pe
        dm, dn, dk = self.down_per_pe
        if gm != 1 or dm != 1:
            raise ValueError("expert GEMM M must be one")
        if gk * self.pe_rows != self.hidden_moe or gn * self.pe_cols != 2 * self.expert_dim:
            raise ValueError("gate-up shape does not reconstruct profile dimensions")
        if dk * self.pe_cols != self.expert_dim_down_padded or dn * self.pe_rows != self.output_hidden:
            raise ValueError("down shape does not reconstruct profile dimensions")
        if self.output_buffer_bytes_per_rank != self.token_count * self.output_hidden * 4:
            raise ValueError("output buffer byte formula mismatch")
        return self


PROFILES: dict[ProfileName, MoeProfileSpec] = {
    "standalone": MoeProfileSpec(
        name="standalone",
        token_count=32,
        top_k=8,
        global_experts=384,
        hidden_gating=3072,
        hidden_moe=4096,
        expert_dim=1280,
        expert_dim_down_padded=2048,
        output_hidden=3072,
        ep_size=8,
        experts_per_rank=48,
        gate_up_per_pe=(1, 640, 512),
        down_per_pe=(1, 384, 512),
        output_buffer_bytes_per_rank=393216,
        entry_mode="gm_activation_pull",
    ),
    "transformer-fused": MoeProfileSpec(
        name="transformer-fused",
        token_count=32,
        top_k=8,
        global_experts=384,
        hidden_gating=2048,
        hidden_moe=2048,
        expert_dim=1024,
        expert_dim_down_padded=1024,
        output_hidden=2048,
        ep_size=4,
        experts_per_rank=96,
        gate_up_per_pe=(1, 512, 256),
        down_per_pe=(1, 256, 256),
        output_buffer_bytes_per_rank=262144,
        entry_mode="bridge_resident_with_residual_preseed",
    ),
}


class RouteTraceIdentity(FrozenRecord):
    model_id: str = Field(min_length=1)
    checkpoint_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    seed: int
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    profile: ProfileName
    capture_point: Literal["post_topk_pre_filter", "post_ep_filter_pre_metadata"]
    source_kind: Literal["kernel_trace", "cpu_reference", "preserved_artifact"]


class RouteRecord(FrozenRecord):
    sequence_index: int = Field(ge=0)
    token_id: int = Field(ge=0)
    topk_slot: int = Field(ge=0)
    global_expert_id: int | None = Field(default=None, ge=0)
    ep_rank: int = Field(ge=0)
    local_expert_id: int | None = Field(default=None, ge=0)
    score: float
    status: Literal["valid", "sentinel"]

    @model_validator(mode="after")
    def semantics(self) -> "RouteRecord":
        if not math.isfinite(self.score):
            raise ValueError("route score must be finite")
        if self.status == "valid":
            if self.global_expert_id is None or self.local_expert_id is None:
                raise ValueError("valid route requires global and local expert ids")
            if not 0.0 <= self.score <= 1.0:
                raise ValueError("valid softmax score must be in [0,1]")
        else:
            if self.local_expert_id is not None:
                raise ValueError("sentinel route cannot carry a local expert id")
            if self.score > 0.0:
                raise ValueError("sentinel score must be non-positive")
        return self


class RankValidCount(FrozenRecord):
    ep_rank: int = Field(ge=0)
    valid_selection_count: int = Field(ge=0)


class TokenHotspot(FrozenRecord):
    ep_rank: int = Field(ge=0)
    token_id: int = Field(ge=0)
    valid_selection_count: int = Field(ge=0)
    output_write_count: int = Field(ge=0)
    address_group_count: int = Field(ge=0)
    max_conflict_multiplicity: int = Field(ge=0)


class RankFlopAccounting(FrozenRecord):
    ep_rank: int = Field(ge=0)
    V: int = Field(ge=0)
    router_useful_flops: int = Field(ge=0)
    gate_up_useful_flops: int = Field(ge=0)
    down_useful_flops: int = Field(ge=0)
    total_useful_flops: int = Field(ge=0)
    router_issued_flops: int = Field(ge=0)
    gate_up_issued_flops: int = Field(ge=0)
    down_issued_flops: int = Field(ge=0)
    total_issued_flops: int = Field(ge=0)


class MoeFlopAccounting(FrozenRecord):
    convention: Literal["useful_dense_equivalent_and_issued_matrix"] = "useful_dense_equivalent_and_issued_matrix"
    per_rank: tuple[RankFlopAccounting, ...]
    group_useful_flops: int = Field(ge=0)
    group_issued_flops: int = Field(ge=0)


def flop_accounting(profile: MoeProfileSpec, rank_counts: Sequence[RankValidCount]) -> MoeFlopAccounting:
    router_useful = 2 * profile.token_count * profile.global_experts * profile.hidden_gating
    router_issued = 32 * 2 * 8 * profile.global_experts * (profile.hidden_gating // profile.pe_rows)
    rows: list[RankFlopAccounting] = []
    for count in sorted(rank_counts, key=lambda item: item.ep_rank):
        v = count.valid_selection_count
        # Mathematical gate/up work excludes the standalone padded hidden K.
        gate_useful = v * 4 * profile.output_hidden * profile.expert_dim
        down_useful = v * 2 * profile.expert_dim * profile.output_hidden
        _, gate_n, gate_k = profile.gate_up_per_pe
        _, down_n, down_k = profile.down_per_pe
        gate_issued = v * 32 * 2 * gate_n * gate_k
        down_issued = v * 32 * 2 * down_n * down_k
        rows.append(RankFlopAccounting(
            ep_rank=count.ep_rank,
            V=v,
            router_useful_flops=router_useful,
            gate_up_useful_flops=gate_useful,
            down_useful_flops=down_useful,
            total_useful_flops=router_useful + gate_useful + down_useful,
            router_issued_flops=router_issued,
            gate_up_issued_flops=gate_issued,
            down_issued_flops=down_issued,
            total_issued_flops=router_issued + gate_issued + down_issued,
        ))
    return MoeFlopAccounting(
        per_rank=tuple(rows),
        group_useful_flops=sum(row.total_useful_flops for row in rows),
        group_issued_flops=sum(row.total_issued_flops for row in rows),
    )


class FrozenMoeWorkload(FrozenRecord):
    schema_version: Literal[1] = 1
    identity: RouteTraceIdentity
    profile: MoeProfileSpec
    trace_kind: TraceKind
    trace_scope: TraceScope
    observed_ep_ranks: tuple[int, ...]
    records: tuple[RouteRecord, ...]
    rank_valid_counts: tuple[RankValidCount, ...]
    token_hotspots: tuple[TokenHotspot, ...]
    flops: MoeFlopAccounting
    source_refs: tuple[str, ...]
    workload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def integrity(self) -> "FrozenMoeWorkload":
        if self.identity.profile != self.profile.name:
            raise ValueError("identity/profile mismatch")
        if self.profile != PROFILES[self.identity.profile]:
            raise ValueError("profile differs from the source-backed profile")
        ranks, counts, hotspots = validate_and_derive_routes(self.profile, self.records, self.trace_scope)
        if self.observed_ep_ranks != ranks or self.rank_valid_counts != counts:
            raise ValueError("observed ranks or per-rank V disagree with route records")
        if self.token_hotspots != hotspots:
            raise ValueError("hotspot statistics disagree with route records")
        if self.flops != flop_accounting(self.profile, counts):
            raise ValueError("FLOP statistics disagree with route records")
        if not self.source_refs or any(not ref for ref in self.source_refs):
            raise ValueError("source_refs must be non-empty")
        expected = stable_sha256(self.model_dump(mode="json", exclude={"workload_sha256"}))
        if expected != self.workload_sha256:
            raise ValueError("workload hash mismatch")
        return self


def validate_and_derive_routes(profile: MoeProfileSpec, records: Sequence[RouteRecord], trace_scope: TraceScope):
    """The same semantic gate is used by construction and deserialization."""
    rows = tuple(RouteRecord.model_validate(row.model_dump()) for row in records)
    if not rows:
        raise ValueError("route trace is empty")
    if [row.sequence_index for row in rows] != list(range(len(rows))):
        raise ValueError("sequence_index must be stable and contiguous from zero")
    keys: set[tuple[int, int, int]] = set()
    observed: set[int] = set()
    valid_pairs: set[tuple[int, int, int]] = set()
    for row in rows:
        if row.ep_rank >= profile.ep_size:
            raise ValueError("ep_rank outside profile")
        if row.token_id >= profile.token_count or row.topk_slot >= profile.top_k:
            raise ValueError("token or top-k slot outside profile")
        key = (row.ep_rank, row.token_id, row.topk_slot)
        if key in keys:
            raise ValueError("duplicate rank/token/top-k slot")
        keys.add(key)
        observed.add(row.ep_rank)
        if row.global_expert_id is not None and row.global_expert_id >= profile.global_experts:
            raise ValueError("global expert outside profile")
        if row.status == "valid":
            first = row.ep_rank * profile.experts_per_rank
            if not first <= row.global_expert_id < first + profile.experts_per_rank:
                raise ValueError("valid expert is not owned by ep_rank")
            if row.local_expert_id != row.global_expert_id - first:
                raise ValueError("local expert id does not match global expert and rank")
            pair = (row.ep_rank, row.token_id, row.global_expert_id)
            if pair in valid_pairs:
                raise ValueError("duplicate token/expert selection")
            valid_pairs.add(pair)
        elif row.global_expert_id is not None and row.global_expert_id // profile.experts_per_rank == row.ep_rank:
            raise ValueError("sentinel global expert is owned by ep_rank")
    observed_ranks = tuple(sorted(observed))
    if trace_scope == "rank_local" and len(observed_ranks) != 1:
        raise ValueError("rank_local trace must contain exactly one rank")
    if trace_scope == "ep_group" and observed_ranks != tuple(range(profile.ep_size)):
        raise ValueError("ep_group trace must cover every EP rank")
    counts = tuple(RankValidCount(
        ep_rank=rank,
        valid_selection_count=sum(row.status == "valid" and row.ep_rank == rank for row in rows),
    ) for rank in observed_ranks)
    hotspots: list[TokenHotspot] = []
    by_rank_token = Counter((row.ep_rank, row.token_id) for row in rows if row.status == "valid")
    for (rank, token), count in sorted(by_rank_token.items()):
        hotspots.append(TokenHotspot(
            ep_rank=rank,
            token_id=token,
            valid_selection_count=count,
            output_write_count=count * profile.pe_rows,
            address_group_count=profile.pe_rows,
            max_conflict_multiplicity=count,
        ))
    return observed_ranks, counts, tuple(hotspots)


def freeze_workload(
    identity: RouteTraceIdentity,
    records: Sequence[RouteRecord],
    *,
    trace_kind: TraceKind,
    trace_scope: TraceScope,
    source_refs: Sequence[str],
) -> FrozenMoeWorkload:
    profile = PROFILES[identity.profile]
    rows = tuple(records)
    observed_ranks, counts, hotspots = validate_and_derive_routes(profile, rows, trace_scope)
    if not source_refs or any(not ref for ref in source_refs):
        raise ValueError("source_refs must be non-empty")
    data: dict[str, Any] = {
        "schema_version": 1,
        "identity": identity,
        "profile": profile,
        "trace_kind": trace_kind,
        "trace_scope": trace_scope,
        "observed_ep_ranks": observed_ranks,
        "records": rows,
        "rank_valid_counts": counts,
        "token_hotspots": tuple(hotspots),
        "flops": flop_accounting(profile, counts),
        "source_refs": tuple(source_refs),
    }
    # Hash the exact JSON-mode shape used by the model hash validator.
    temporary = {
        key: ([item.model_dump(mode="json") for item in value] if isinstance(value, tuple) and value and isinstance(value[0], BaseModel)
              else value.model_dump(mode="json") if isinstance(value, BaseModel) else value)
        for key, value in data.items()
    }
    digest = stable_sha256(temporary)
    return FrozenMoeWorkload(**data, workload_sha256=digest)


__all__ = [
    "FrozenMoeWorkload",
    "MoeFlopAccounting",
    "MoeProfileSpec",
    "PROFILES",
    "RankFlopAccounting",
    "RankValidCount",
    "RouteRecord",
    "RouteTraceIdentity",
    "TokenHotspot",
    "canonical_json",
    "flop_accounting",
    "freeze_workload",
    "stable_sha256",
]

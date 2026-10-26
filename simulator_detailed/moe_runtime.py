"""Lower and execute :mod:`moe_operation` graphs on simulator_detailed.

Data NoC transfers use the existing latency-FIFO packet runtime and multicast
fork implementation.  Compute, GM write-sum RMW and ADALINK costs are quoted
by an injected provider.  The module has no implicit silicon timing defaults.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import math
from typing import Any, Mapping, Protocol, Sequence

from .configs.schemas.generic_transactions import GenericLatencyFifoTiming
from .configs.schemas.operation_envelope import OperationEnvelope
from .configs.schemas.system_spec import (
    ImmutablePlan,
    PlanCompute,
    PlanContent,
    PlanCounter,
    PlanHop,
    PlanResource,
    PlanSignal,
    PlanTransfer,
    PlanWait,
)
from .moe_operation import MoeOperationPlan, MoeStage, MoeTransaction
from .runtime_context import RuntimeContext
from .topology import content_digest


@dataclass(frozen=True)
class DataNoCConfig:
    """Explicit packet-runtime parameters for Data NoC channel 1.

    ``issue_cycles`` is the packet serialization interval.  Bandwidth sweeps
    alter only this field; packet size, FIFO capacity and propagation remain
    fixed.  A finer runtime clock should be supplied when integer-cycle
    granularity would otherwise hide a requested bandwidth change.
    """

    packet_bytes: int
    issue_cycles: int
    input_latency_cycles: float
    channel_latency_cycles: float
    fifo_slots: int
    channel_slots: int
    route_reduce_extra_cycles: float
    gm_write_sum_transport_extra_cycles: float
    calibrated: bool
    source: str

    def __post_init__(self) -> None:
        for name in ("packet_bytes", "issue_cycles", "fifo_slots", "channel_slots"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("input_latency_cycles", "channel_latency_cycles", "route_reduce_extra_cycles", "gm_write_sum_transport_extra_cycles"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if type(self.calibrated) is not bool or not self.source:
            raise ValueError("Data NoC calibration state and source are required")

    @property
    def serialization_bandwidth_bytes_per_cycle(self) -> float:
        return self.packet_bytes / self.issue_cycles

    def scaled_bandwidth(self, scale: float) -> tuple["DataNoCConfig", float]:
        if type(scale) not in (int, float) or not math.isfinite(scale) or scale <= 0:
            raise ValueError("bandwidth scale must be finite and positive")
        new_issue = max(1, int(math.ceil(self.issue_cycles / float(scale))))
        updated = replace(self, issue_cycles=new_issue)
        effective = self.issue_cycles / new_issue
        return updated, effective


@dataclass(frozen=True)
class CostQuote:
    cost_kind: str
    duration_cycles: float
    calibrated: bool
    source: str
    work_items: int
    inputs: Mapping[str, Any]

    def __post_init__(self) -> None:
        if type(self.duration_cycles) not in (int, float) or not math.isfinite(self.duration_cycles) or self.duration_cycles <= 0:
            raise ValueError("quoted duration must be finite and positive")
        if type(self.calibrated) is not bool or not self.source:
            raise ValueError("cost quote calibration state and source are required")


class MoeCostProvider(Protocol):
    def quote(self, cost_kind: str, work_items: int, inputs: Mapping[str, Any]) -> CostQuote: ...
    def describe(self) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class CostRule:
    fixed_cycles: float
    cycles_per_item: float
    item_exponent: float
    calibrated: bool
    source: str

    def __post_init__(self) -> None:
        for name in ("fixed_cycles", "cycles_per_item", "item_exponent"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.fixed_cycles == 0 and self.cycles_per_item == 0:
            raise ValueError("a cost rule must produce positive duration")
        if not self.source:
            raise ValueError("cost rule source is required")


class MappingCostProvider:
    """Simple injectable provider; all rule values come from the caller."""

    def __init__(self, rules: Mapping[str, CostRule], *, provider_id: str):
        if not provider_id or not rules:
            raise ValueError("provider_id and cost rules are required")
        self._rules = dict(rules)
        self.provider_id = provider_id

    def quote(self, cost_kind: str, work_items: int, inputs: Mapping[str, Any]) -> CostQuote:
        if type(work_items) is not int or work_items < 0:
            raise ValueError("work_items must be a non-negative integer")
        try:
            rule = self._rules[cost_kind]
        except KeyError as exc:
            raise ValueError(f"missing cost rule for {cost_kind}") from exc
        duration = rule.fixed_cycles + rule.cycles_per_item * (work_items ** rule.item_exponent)
        return CostQuote(cost_kind, duration, rule.calibrated, rule.source, work_items, dict(inputs))

    def describe(self) -> Mapping[str, Any]:
        return {
            "provider_id": self.provider_id,
            "rules": {name: asdict(rule) for name, rule in sorted(self._rules.items())},
        }


@dataclass(frozen=True)
class MoeRuntimeConfig:
    data_noc: DataNoCConfig
    cost_provider: MoeCostProvider
    max_cycles: float

    def __post_init__(self) -> None:
        if type(self.max_cycles) not in (int, float) or not math.isfinite(self.max_cycles) or self.max_cycles <= 0:
            raise ValueError("max_cycles must be finite and positive")
        if not hasattr(self.cost_provider, "quote") or not hasattr(self.cost_provider, "describe"):
            raise TypeError("cost_provider must implement quote and describe")


@dataclass(frozen=True)
class MappedMoePlan:
    operation_plan: MoeOperationPlan
    runtime_plan: ImmutablePlan
    quotes: Mapping[str, CostQuote]
    data_noc: DataNoCConfig
    final_transaction_id: str


@dataclass(frozen=True)
class MoeExecution:
    mapped: MappedMoePlan
    runtime: RuntimeContext
    result: Any
    receipt: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_plan": self.mapped.operation_plan.to_dict(),
            "runtime_receipt": dict(self.receipt),
        }


def functional_test_runtime_config(*, max_cycles: float = 10_000_000) -> MoeRuntimeConfig:
    """Return a mechanics-only config for CPU tests, never a silicon claim.

    Every value is tagged ``un_calibrated`` in receipts.  Production studies
    must replace both this provider and the Data NoC configuration.
    """

    kinds = (
        "rmsnorm",
        "activation_quant",
        "router_gemm",
        "softmax_topk",
        "ep_filter",
        "gate_up_w4a8",
        "silu_score_quant",
        "down_w4a8",
        "gm_write_sum",
        "adalink_allreduce",
    )
    rules = {
        kind: CostRule(1.0, 0.0, 1.0, False, "functional-test-only")
        for kind in kinds
    }
    # A nonlinear mechanics-only rule makes token-address conflicts observable.
    rules["gm_write_sum"] = CostRule(1.0, 1.0, 2.0, False, "functional-test-only")
    return MoeRuntimeConfig(
        data_noc=DataNoCConfig(
            packet_bytes=4096,
            issue_cycles=8,
            input_latency_cycles=1,
            channel_latency_cycles=1,
            fifo_slots=4,
            channel_slots=4,
            route_reduce_extra_cycles=0,
            gm_write_sum_transport_extra_cycles=0,
            calibrated=False,
            source="functional-test-only",
        ),
        cost_provider=MappingCostProvider(rules, provider_id="functional-test-only"),
        max_cycles=max_cycles,
    )


def _digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _timing(config: DataNoCConfig, stage: str, source: str, primitive: str, *, multicast_branch: str | None = None):
    extra = 0.0
    if primitive == "chain_reduce":
        extra = config.route_reduce_extra_cycles
    elif primitive == "gm_write_sum":
        extra = config.gm_write_sum_transport_extra_cycles
    common = dict(
        mode="latency_fifo",
        packet_bytes=config.packet_bytes,
        issue_cycles=config.issue_cycles,
        fifo_slots=config.fifo_slots,
        channel_slots=config.channel_slots,
        # All channel-1 flows contend for prefix admission.  Multicast leaf
        # credits are branch-local: T71's physical fork requires all children
        # to become ready jointly, so a capacity-limited shared leaf resource
        # would be an artificial cyclic wait rather than real contention.
        input_resource_id="data-noc/channel1/input",
        output_resource_id=(
            f"data-noc/channel1/multicast-issue/{multicast_branch}"
            if multicast_branch else "data-noc/channel1/packet-issue"
        ),
        channel_resource_id=(
            f"data-noc/channel1/multicast-channel/{multicast_branch}"
            if multicast_branch else "data-noc/channel1/fabric"
        ),
        flow_serialized=False,
    )
    if stage == "ingress":
        return GenericLatencyFifoTiming(
            stage="ingress",
            input_latency_cycles=config.input_latency_cycles,
            channel_latency_cycles=0,
            **common,
        )
    return GenericLatencyFifoTiming(
        stage="egress",
        input_latency_cycles=0,
        channel_latency_cycles=config.channel_latency_cycles + extra,
        **common,
    )


def lower_moe_plan(operation_plan: MoeOperationPlan, config: MoeRuntimeConfig) -> MappedMoePlan:
    """Map a structural MoE plan to the existing generic runtime plan."""
    if not isinstance(operation_plan, MoeOperationPlan):
        raise TypeError("operation_plan must be MoeOperationPlan")
    transactions: list[Any] = []
    resources: dict[str, PlanResource] = {}
    quotes: dict[str, CostQuote] = {}

    def resource(resource_id: str, kind: str, capacity: int) -> None:
        old = resources.get(resource_id)
        item = PlanResource(resource_id=resource_id, kind=kind, capacity=capacity)
        if old is not None and old != item:
            raise ValueError(f"inconsistent resource declaration {resource_id}")
        resources[resource_id] = item

    for tx in operation_plan.transactions:
        if tx.primitive in ("point_to_point", "masked_multicast", "chain_reduce", "gm_write_sum"):
            if not tx.source or not tx.destination or tx.payload_bytes <= 0:
                raise ValueError(f"incomplete Data NoC transaction {tx.transaction_id}")
            ingress = _timing(config.data_noc, "ingress", tx.source, tx.primitive)
            branch = f"{tx.multicast_group}/{tx.destination}" if tx.multicast_group else None
            egress = _timing(config.data_noc, "egress", tx.source, tx.primitive, multicast_branch=branch)
            if tx.multicast_group:
                prefix = f"{tx.multicast_group}/prefix"
                leaf = f"{tx.multicast_group}/leaf/{tx.destination}"
            else:
                prefix = f"{tx.transaction_id}/prefix"
                leaf = f"{tx.transaction_id}/leaf"
            for link_id, timing in ((prefix, ingress), (leaf, egress)):
                resource(f"data-noc/{link_id}", "link", timing.buffer_slots)
                resource(timing.input_resource_id, "link", timing.fifo_slots)
                resource(timing.output_resource_id, "link", 1)
                resource(timing.channel_resource_id, "link", timing.channel_slots)
            transactions.append(PlanTransfer(
                transaction_id=tx.transaction_id,
                kind="transfer",
                network_id="data-noc",
                source=tx.source,
                destination=tx.destination,
                payload_bytes=tx.payload_bytes,
                address=None,
                depends_on=tx.dependencies,
                start_cycles=0,
                routing="static",
                hops=(
                    PlanHop(network_id="data-noc", link_id=prefix, timing=ingress),
                    PlanHop(network_id="data-noc", link_id=leaf, timing=egress),
                ),
                multicast_group=tx.multicast_group,
            ))
        elif tx.primitive in ("compute", "adalink_collective"):
            if not tx.cost_kind:
                raise ValueError(f"missing cost kind on {tx.transaction_id}")
            quote_inputs = {
                "profile": operation_plan.profile.name,
                "hidden_size": operation_plan.profile.hidden_size,
                "expert_dim": operation_plan.profile.expert_dim,
                "ep_size": operation_plan.profile.ep_size,
                "V": operation_plan.valid_selection_count,
                "payload_bytes": tx.payload_bytes,
                **dict(tx.attributes),
            }
            quote = config.cost_provider.quote(tx.cost_kind, tx.work_items, quote_inputs)
            quotes[tx.transaction_id] = quote
            resource(tx.resource, "execution_unit", 1)
            transactions.append(PlanCompute(
                transaction_id=tx.transaction_id,
                kind="compute",
                unit_id=tx.resource,
                duration_cycles=quote.duration_cycles,
                depends_on=tx.dependencies,
                start_cycles=0,
            ))
        elif tx.primitive == "signal":
            if tx.counter_id is None or tx.counter_delta is None:
                raise ValueError(f"incomplete signal {tx.transaction_id}")
            transactions.append(PlanSignal(
                transaction_id=tx.transaction_id,
                kind="signal",
                counter_id=tx.counter_id,
                delta=tx.counter_delta,
                depends_on=tx.dependencies,
                start_cycles=0,
            ))
        elif tx.primitive == "wait":
            if tx.counter_id is None or tx.counter_threshold is None:
                raise ValueError(f"incomplete wait {tx.transaction_id}")
            transactions.append(PlanWait(
                transaction_id=tx.transaction_id,
                kind="wait",
                counter_id=tx.counter_id,
                threshold=tx.counter_threshold,
                depends_on=tx.dependencies,
                start_cycles=0,
                reachable=operation_plan.counters[tx.counter_id],
            ))
        else:
            raise ValueError(f"unsupported MoE primitive {tx.primitive}")

    counters = tuple(PlanCounter(counter_id=counter, initial_value=0, upper_bound=upper) for counter, upper in sorted(operation_plan.counters.items()))
    if not transactions:
        raise ValueError("MoE runtime plan is empty")
    final_id = transactions[-1].transaction_id
    envelope = OperationEnvelope.model_validate({
        "operation_id": "moe.whole-operation",
        "member_transactions": [final_id],
        "boundary_ref": "moe-operation-plan-v1",
    })
    plan_dict = operation_plan.to_dict()
    content = PlanContent(
        spec_id=f"moe-{operation_plan.profile.name}",
        system_id="adas-moe-8x4",
        graph_sha256=_digest({"system": "adas-moe-8x4", "pe_grid": [8, 4]}),
        batch_sha256=_digest(plan_dict),
        resources=tuple(resources[key] for key in sorted(resources)),
        counters=counters,
        transactions=tuple(transactions),
        max_cycles=config.max_cycles,
        operation_envelopes=(envelope,),
    )
    runtime_plan = ImmutablePlan(
        schema_version=1,
        spec_sha256=_digest({"operation": plan_dict, "runtime": {"data_noc": asdict(config.data_noc), "cost_provider": dict(config.cost_provider.describe())}}),
        content=content,
        plan_sha256=content_digest(content.model_dump(mode="json")),
    )
    return MappedMoePlan(operation_plan, runtime_plan, quotes, config.data_noc, final_id)


def _stage_receipts(mapped: MappedMoePlan, result: Any) -> list[dict[str, Any]]:
    spans = {span.transaction_id: span for span in result.transactions}
    txs = {tx.transaction_id: tx for tx in mapped.operation_plan.transactions}
    completed_stages: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for stage in mapped.operation_plan.stages:
        stage_txs = [txs[txid] for txid in stage.transaction_ids]
        stage_spans = [spans[tx.transaction_id] for tx in stage_txs]
        complete_spans = [span for span in stage_spans if span.status == "complete"]
        dependency_end = max((completed_stages[d]["end_cycles"] for d in stage.dependencies if d in completed_stages and completed_stages[d]["end_cycles"] is not None), default=0.0)
        if not stage_txs:
            start = end = dependency_end
            status = "skipped"
        elif len(complete_spans) == len(stage_spans):
            start = min(span.start_cycles for span in complete_spans)
            end = max(span.end_cycles for span in complete_spans)
            status = "complete"
        else:
            start = min((span.start_cycles for span in complete_spans), default=None)
            end = None
            status = "incomplete"
        uncalibrated: list[str] = []
        if any(tx.primitive in ("point_to_point", "masked_multicast", "chain_reduce", "gm_write_sum") for tx in stage_txs) and not mapped.data_noc.calibrated:
            uncalibrated.append("data_noc")
        for tx in stage_txs:
            quote = mapped.quotes.get(tx.transaction_id)
            if quote is not None and not quote.calibrated:
                uncalibrated.append(quote.cost_kind)
        row = {
            "stage_id": stage.stage_id,
            "source_stage": stage.source_stage,
            "label": stage.label,
            "status": status,
            "skipped_reason": stage.skipped_reason,
            "start_cycles": start,
            "end_cycles": end,
            "duration_cycles": None if start is None or end is None else end - start,
            "wait_cycles": sum(float(span.wait_cycles or 0) for span in complete_spans),
            "resources": sorted({tx.resource for tx in stage_txs}),
            "bytes": {
                "logical_source": sum(tx.logical_source_bytes for tx in stage_txs),
                "endpoint_delivered": sum(tx.endpoint_delivered_bytes for tx in stage_txs),
                "data_noc_payload": sum(tx.payload_bytes for tx in stage_txs if tx.primitive in ("point_to_point", "masked_multicast", "chain_reduce", "gm_write_sum")),
                "adalink_payload": sum(tx.payload_bytes for tx in stage_txs if tx.primitive == "adalink_collective"),
            },
            "participants": sorted({participant for tx in stage_txs for participant in tx.participants}),
            "dependencies": list(stage.dependencies),
            "sync_semantics": list(stage.sync_semantics),
            "transaction_count": len(stage_txs),
            "uncalibrated_fields": sorted(set(uncalibrated)),
        }
        rows.append(row)
        completed_stages[stage.stage_id] = row
    return rows


def _critical_path(stages: Sequence[Mapping[str, Any]]) -> list[str]:
    completed = {row["stage_id"]: row for row in stages if row["end_cycles"] is not None}
    if not completed:
        return []
    current = max(completed.values(), key=lambda row: (row["end_cycles"], row["stage_id"]))
    path = [current["stage_id"]]
    while current["dependencies"]:
        candidates = [completed[dep] for dep in current["dependencies"] if dep in completed]
        if not candidates:
            break
        current = max(candidates, key=lambda row: (row["end_cycles"], row["stage_id"]))
        path.append(current["stage_id"])
    return list(reversed(path))


def _receipt(mapped: MappedMoePlan, runtime: RuntimeContext, result: Any) -> dict[str, Any]:
    stages = _stage_receipts(mapped, result)
    fifo = list(getattr(runtime, "_fifo_events", ()))
    physical_admits = {
        (event.get("owner", event["transaction_id"]), event["resource_id"], event["packet_index"])
        for event in fifo if event["action"] == "fifo_admit"
    }
    physical_deliveries = {
        (event["transaction_id"], event["resource_id"], event["packet_index"])
        for event in fifo if event["action"] == "packet_delivered"
    }
    operation_accounting = mapped.operation_plan.to_dict()["transaction_accounting"]
    uncalibrated = []
    if not mapped.data_noc.calibrated:
        uncalibrated.append({"field": "data_noc", "source": mapped.data_noc.source})
    for txid, quote in sorted(mapped.quotes.items()):
        if not quote.calibrated:
            uncalibrated.append({"field": quote.cost_kind, "transaction_id": txid, "source": quote.source})
    spans = {span.transaction_id: span for span in result.transactions}
    tx_receipts = []
    for tx in mapped.operation_plan.transactions:
        span = spans[tx.transaction_id]
        tx_receipts.append({
            "transaction_id": tx.transaction_id,
            "stage_id": tx.stage_id,
            "primitive": tx.primitive,
            "status": span.status,
            "start_cycles": span.start_cycles,
            "end_cycles": span.end_cycles,
            "wait_cycles": span.wait_cycles,
            "resource": tx.resource,
            "payload_bytes": tx.payload_bytes,
            "participants": list(tx.participants),
            "dependencies": list(tx.dependencies),
            "address": tx.address,
            "multicast_group": tx.multicast_group,
        })
    return {
        "schema_version": 1,
        "status": result.status,
        "reason": result.reason,
        "profile": mapped.operation_plan.profile.name,
        "V": mapped.operation_plan.valid_selection_count,
        "route_mode": mapped.operation_plan.route_mode,
        "completion_cycles": result.completion_cycles,
        "whole_operation": list(result.operation_envelopes),
        "stages": stages,
        "transactions": tx_receipts,
        "critical_path_stages": _critical_path(stages),
        "transaction_accounting": {
            **operation_accounting,
            "runtime_physical_packet_admissions": len(physical_admits),
            "runtime_endpoint_packet_deliveries": len(physical_deliveries),
        },
        "resources": [resource.model_dump(mode="json") for resource in result.resources],
        "runtime_config": {
            "data_noc": {
                **asdict(mapped.data_noc),
                "serialization_bandwidth_bytes_per_cycle": mapped.data_noc.serialization_bandwidth_bytes_per_cycle,
            },
            "cost_quotes": {txid: asdict(quote) for txid, quote in sorted(mapped.quotes.items())},
        },
        "uncalibrated_fields": uncalibrated,
        "cleanup": {
            "registry_released": runtime.registry.all_released(),
            "multicast_groups_empty": not bool(getattr(runtime, "_fifo_multicast_groups", {})),
            "background_processes_alive": sum(process.is_alive for process in runtime._background),
        },
        "capability_boundary": {
            "silicon_timing": "unvalidated" if uncalibrated else "provider-calibrated",
            "weight_sram_gather_modeled_as_data_noc": False,
            "adalink_modeled_as_data_noc": False,
            "token_all_to_all_present": False,
        },
    }


def execute_moe_plan(
    operation_plan: MoeOperationPlan,
    config: MoeRuntimeConfig,
    *,
    cancel_transaction_id: str | None = None,
    cancel_at_cycles: float | None = None,
) -> MoeExecution:
    mapped = lower_moe_plan(operation_plan, config)
    runtime = RuntimeContext(mapped.runtime_plan)
    if (cancel_transaction_id is None) != (cancel_at_cycles is None):
        raise ValueError("cancellation requires both transaction id and time")
    if cancel_transaction_id is not None:
        known = {tx.transaction_id for tx in mapped.runtime_plan.content.transactions} | {"moe.whole-operation"}
        if cancel_transaction_id not in known:
            raise ValueError("unknown cancellation target")
        if type(cancel_at_cycles) not in (int, float) or not math.isfinite(cancel_at_cycles) or cancel_at_cycles < 0:
            raise ValueError("cancel_at_cycles must be finite and non-negative")
        def cancellation():
            yield runtime.env.timeout(cancel_at_cycles)
            runtime.cancel(cancel_transaction_id)
        runtime.env.process(cancellation())
    result = runtime.run()
    receipt = _receipt(mapped, runtime, result)
    return MoeExecution(mapped, runtime, result, receipt)


def run_bandwidth_sweep(
    operation_plan: MoeOperationPlan,
    config: MoeRuntimeConfig,
    scales: Sequence[float],
    *,
    non_moe_decode_cycles: float | None = None,
    effective_flops: float | None = None,
    peak_flops_per_cycle: float | None = None,
) -> dict[str, Any]:
    """Run the same graph while changing only Data NoC serialization rate."""
    if not scales:
        raise ValueError("bandwidth sweep requires at least one scale")
    projection_values = (non_moe_decode_cycles, effective_flops, peak_flops_per_cycle)
    if any(value is not None for value in projection_values) and not all(value is not None for value in projection_values):
        raise ValueError("MFU projection requires non-MoE cycles, effective FLOPs and peak FLOPs/cycle")
    if all(value is not None for value in projection_values):
        if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in projection_values):
            raise ValueError("MFU projection inputs must be finite and positive")
    rows = []
    for requested in scales:
        data_noc, effective = config.data_noc.scaled_bandwidth(requested)
        execution = execute_moe_plan(operation_plan, replace(config, data_noc=data_noc))
        envelope = execution.receipt["whole_operation"][0]
        moe_cycles = envelope.get("duration_cycles") if envelope.get("status") == "complete" else None
        row = {
            "requested_bandwidth_scale": requested,
            "effective_bandwidth_scale": effective,
            "packet_issue_cycles": data_noc.issue_cycles,
            "moe_decode_cycles": moe_cycles,
            "status": execution.receipt["status"],
            "critical_path_stages": execution.receipt["critical_path_stages"],
        }
        if moe_cycles is not None and non_moe_decode_cycles is not None:
            total = non_moe_decode_cycles + moe_cycles
            row["projected_total_decode_cycles"] = total
            row["projected_mfu"] = effective_flops / (peak_flops_per_cycle * total)
            row["projection_kind"] = "composed-input-projection-not-silicon-validation"
        rows.append(row)
    baseline = rows[0]["moe_decode_cycles"]
    for row in rows:
        current = row["moe_decode_cycles"]
        row["moe_speedup_vs_first"] = None if baseline is None or current is None else baseline / current
    return {
        "schema_version": 1,
        "varied_parameter": "data_noc.packet_serialization_bandwidth",
        "held_constant": ["operation_graph", "routes", "compute_costs", "gm_write_sum_costs", "adalink_costs", "packet_bytes", "fifo_capacity", "propagation"],
        "rows": rows,
        "mfu_projection_enabled": non_moe_decode_cycles is not None,
        "interpretation": "latency changes are model counterfactuals; silicon claims require calibrated providers",
    }


__all__ = [
    "CostQuote",
    "CostRule",
    "DataNoCConfig",
    "MappedMoePlan",
    "MappingCostProvider",
    "MoeCostProvider",
    "MoeExecution",
    "MoeRuntimeConfig",
    "execute_moe_plan",
    "functional_test_runtime_config",
    "lower_moe_plan",
    "run_bandwidth_sweep",
]

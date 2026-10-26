"""M07 synchronization protocols lowered as zero-service event dependencies."""
from dataclasses import dataclass
from typing import Literal
import json,math
from pydantic import Field,model_validator
from .workload import FrozenRecord,stable_sha256
from .binding import SuppliedBinding
from .hardware import HardwareParameterSet
from ..configs.schemas.system_spec import SystemSpec
from ..system_compile import compile_system
from ..runtime_context import RuntimeContext
from .receipt import ResourceObserver

SyncKind=Literal['inner_release','inner_acquire','outer_release','outer_acquire','fence_calc','fence_io','count_signal','count_wait']

class SyncNode(FrozenRecord):
    node_id: str
    stage: str
    kind: SyncKind
    counter_id: str
    depends_on: tuple[str,...]=()
    start_cycles: float = Field(default=0,ge=0)
    delta: int | None = Field(default=None,gt=0)
    threshold: int | None = Field(default=None,gt=0)
    participants: tuple[str,...]
    @model_validator(mode='after')
    def protocol(self):
        if not self.node_id or not self.stage or not self.counter_id or not self.participants:raise ValueError('sync identity, counter and participants required')
        if not math.isfinite(self.start_cycles):raise ValueError('nonfinite sync start')
        signal=self.kind in ('inner_release','outer_release','fence_calc','fence_io','count_signal')
        if signal and (self.delta is None or self.threshold is not None):raise ValueError('release/fence/signal requires delta only')
        if not signal and (self.threshold is None or self.delta is not None):raise ValueError('acquire/wait requires threshold only')
        return self

class SyncPlan(FrozenRecord):
    protocol_id: str
    fixture_only: bool
    nodes: tuple[SyncNode,...]
    source_refs: tuple[str,...]
    plan_sha256: str
    @model_validator(mode='after')
    def integrity(self):
        if not self.protocol_id or not self.nodes or not self.source_refs:raise ValueError('sync plan identity and evidence required')
        known=set();signals={}
        for node in self.nodes:
            if node.node_id in known or not set(node.depends_on)<=known:raise ValueError('sync nodes must be unique and topological')
            known.add(node.node_id)
            if node.delta is not None:signals[node.counter_id]=signals.get(node.counter_id,0)+node.delta
            elif node.threshold>signals.get(node.counter_id,0):raise ValueError('sync wait threshold is unreachable')
        if stable_sha256(self.model_dump(mode='json',exclude={'plan_sha256'}))!=self.plan_sha256:raise ValueError('sync plan hash mismatch')
        return self

def freeze_sync_plan(**kwargs):
    from pydantic import TypeAdapter
    raw=TypeAdapter(dict).dump_python(kwargs,mode='json')
    return SyncPlan(**kwargs,plan_sha256=stable_sha256(raw))

@dataclass(frozen=True)
class SyncExecution:
    result: object
    runtime: object
    receipt: dict

def execute_sync_plan(sync_plan,binding,hardware,*,production=True,max_cycles=100000,cancel=None):
    sync_plan=SyncPlan.model_validate_json(sync_plan.model_dump_json());binding=SuppliedBinding.model_validate_json(binding.model_dump_json())
    hardware=HardwareParameterSet.model_validate_json(hardware.model_dump_json())
    if production and (sync_plan.fixture_only or binding.fixture_only):raise ValueError('production sync requires supplied real plan and binding')
    counters=sorted({n.counter_id for n in sync_plan.nodes});transactions=[]
    for node in sync_plan.nodes:
        row=dict(transaction_id=node.node_id,depends_on=list(node.depends_on),start_cycles=node.start_cycles,counter_id=node.counter_id)
        if node.delta is not None:row.update(kind='signal',delta=node.delta)
        else:row.update(kind='wait',threshold=node.threshold)
        transactions.append(row)
    raw=dict(kind='system_spec',schema_version=1,spec_id=sync_plan.protocol_id,graph=binding.graph,
      batch=dict(kind='generic_transaction_batch',schema_version=1,batch_id=sync_plan.protocol_id,timing=binding.effective_timing(hardware),
       counters=[dict(counter_id=c,initial_value=0) for c in counters],transactions=transactions,max_cycles=max_cycles,
       operation_envelopes=[dict(operation_id=sync_plan.protocol_id+'.whole',member_transactions=[n.node_id for n in sync_plan.nodes],boundary_ref='M07-protocol-member-closure')]))
    spec=SystemSpec.model_validate_json(json.dumps(raw));plan=compile_system(spec);runtime=RuntimeContext(plan);observer=ResourceObserver(runtime)
    if cancel:
        def cancel_later():
            yield runtime.env.timeout(cancel[1]);runtime.cancel(cancel[0])
        runtime.env.process(cancel_later())
    result=runtime.run();spans={s.transaction_id:s for s in result.transactions}
    from types import SimpleNamespace
    receipt=dict(schema='t81.m07.sync.receipt.v1',status=result.status,protocol_sha256=sync_plan.plan_sha256,binding_sha256=binding.binding_sha256,
      hardware_sha256=hardware.parameter_sha256,plan_sha256=plan.plan_sha256,service_duration_cycles=0,service_status='protocol-only-no-invented-sync-cost',
      nodes=[dict(node.model_dump(mode='json'),receipt=spans[node.node_id].model_dump(mode='json')) for node in sync_plan.nodes],
      counters=[c.model_dump(mode='json') for c in result.counters],whole_boundary=list(result.operation_envelopes),
      causal_graph=observer.causal_graph(SimpleNamespace(plan=plan),spans),cleanup=runtime.registry.all_released(),fixture_only=sync_plan.fixture_only or binding.fixture_only,
      architecture_benefit_claim=False)
    return SyncExecution(result,runtime,receipt)

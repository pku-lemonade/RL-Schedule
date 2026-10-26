"""Small component actions execute on supplied resources in RuntimeContext.

Duration comes from an explicit calibrated component formula. Zero duration is
represented by a signal, never an epsilon. Component protocols carry separate
issue, service, handoff and network boundaries; no complete-MoE time is quoted.
"""
from dataclasses import dataclass
import json,math
from pydantic import Field,model_validator
from .workload import FrozenRecord,stable_sha256
from .binding import SuppliedBinding
from .hardware import HardwareParameterSet
from ..configs.schemas.system_spec import SystemSpec
from ..system_compile import compile_system
from ..runtime_context import RuntimeContext
from .receipt import ResourceObserver

class ComponentAction(FrozenRecord):
    action_id: str
    stage: str
    component: str
    resource_id: str
    work: float = Field(ge=0)
    work_unit: str
    duration_cycles: float | None
    duration_source: str
    calibration_sha256: str
    dependencies: tuple[str,...]=()
    earliest_start: float = Field(default=0.0,ge=0)
    bytes: int = Field(default=0,ge=0)
    participants: tuple[str,...]
    status: str
    conditional_context: str | None = None
    conditional_members: int = Field(default=0, ge=0)
    conditional_start: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    simultaneous_ready: tuple[str,...] = ()
    @model_validator(mode='after')
    def cost(self):
        if self.status=='calibrated':
            if self.duration_cycles is None or self.duration_cycles<0 or not math.isfinite(self.duration_cycles):raise ValueError('invalid calibrated service duration')
        elif self.duration_cycles is not None:raise ValueError('unavailable/uncalibrated actions cannot have duration')
        if (self.conditional_context is None) != (self.conditional_members == 0):raise ValueError('conditional interval context incomplete')
        return self

@dataclass(frozen=True)
class ComponentExecution:
    result: object
    runtime: object
    receipt: dict

def _conditional_contexts(actions):
    groups = {}
    for action in actions:
        if action.conditional_context is not None:
            groups.setdefault(action.conditional_context, []).append(action)
    for context, members in groups.items():
        if any(action.conditional_members != len(members) for action in members):
            raise ValueError('conditional interval context requires all engine boundaries')
        resources = {action.resource_id for action in members}
        if any(action.duration_cycles and action.resource_id in resources and action.conditional_context != context for action in actions):
            raise ValueError('conditional interval context does not support shared-engine contention or finite-buffer composition')
    return groups

def _validate_conditional_schedule(groups, spans):
    for members in groups.values():
        for action in members:
            ready = [spans[identity].end_cycles for identity in action.simultaneous_ready]
            if ready and any(value != ready[0] for value in ready):
                raise ValueError('conditional interval context requires simultaneous engine issue readiness')
            expected = max((action.earliest_start, *(spans[identity].end_cycles for identity in action.dependencies)))
            if action.conditional_start is not None:
                expected = action.conditional_start
            if not math.isclose(spans[action.action_id].start_cycles, expected, rel_tol=0, abs_tol=1e-9):
                raise ValueError('conditional interval context invalidated by runtime engine delay')

def execute_actions(actions,binding,hardware,*,production=True,max_cycles=100000.0,cancel=None):
    binding=SuppliedBinding.model_validate_json(binding.model_dump_json())
    hardware=HardwareParameterSet.model_validate_json(hardware.model_dump_json())
    if production and binding.fixture_only:raise ValueError('production component requires supplied real binding')
    actions=tuple(ComponentAction.model_validate_json(a.model_dump_json()) for a in actions)
    conditional = _conditional_contexts(actions)
    units={u['unit_id'] for u in binding.graph['execution_units']}
    tx=[];counters=[]
    for a in actions:
        if a.status!='calibrated' or a.duration_cycles is None:raise ValueError('component cost unavailable')
        if a.resource_id not in units:raise ValueError('component unit absent from supplied graph')
        row=dict(transaction_id=a.action_id,depends_on=list(a.dependencies),start_cycles=a.earliest_start)
        if a.duration_cycles==0:
            counter=a.action_id+'.counter';counters.append(dict(counter_id=counter,initial_value=0))
            row.update(kind='signal',counter_id=counter,delta=1)
        else:row.update(kind='compute',unit_id=a.resource_id,duration_cycles=a.duration_cycles)
        tx.append(row)
    raw=dict(kind='system_spec',schema_version=1,spec_id='component-execution',graph=binding.graph,
        batch=dict(kind='generic_transaction_batch',schema_version=1,batch_id='component-execution',timing=binding.effective_timing(hardware),
            counters=counters,transactions=tx,max_cycles=max_cycles,operation_envelopes=[dict(operation_id='component.whole',member_transactions=[a.action_id for a in actions],boundary_ref='component-member-closure')]))
    spec=SystemSpec.model_validate_json(json.dumps(raw));plan=compile_system(spec);runtime=RuntimeContext(plan);observer=ResourceObserver(runtime)
    if cancel:
        def cancel_later():
            yield runtime.env.timeout(cancel[1]);runtime.cancel(cancel[0])
        runtime.env.process(cancel_later())
    result=runtime.run();spans={s.transaction_id:s for s in result.transactions}
    if result.status == 'complete':
        _validate_conditional_schedule(conditional, spans)
    from types import SimpleNamespace
    causal=observer.causal_graph(SimpleNamespace(plan=plan),spans)
    receipt=dict(status=result.status,workload_sha256=stable_sha256([a.model_dump(mode='json') for a in actions]),binding_sha256=binding.binding_sha256,
        hardware_sha256=hardware.parameter_sha256,plan_sha256=plan.plan_sha256,actions=[dict(a.model_dump(mode='json'),receipt=spans[a.action_id].model_dump(mode='json')) for a in actions],
        resources=observer.summary(),causal_graph=causal,whole_boundary=list(result.operation_envelopes),cleanup=runtime.registry.all_released(),
        architecture_benefit_claim=False,fixture_only=binding.fixture_only)
    if conditional:
        receipt['conditional_timing'] = dict(contexts=sorted(conditional), status='checked' if result.status == 'complete' else 'incomplete',
            scope='opaque current-context intervals; no producer progress or finite-buffer model', counterfactual_supported=False)
    return ComponentExecution(result,runtime,receipt)

def execute_component_network(actions, workload, binding, hardware, flow_dependencies, *, production=True, max_cycles=100000.0):
    """Compute/control service and packet flows share one native event graph."""
    from .lowering import compile_network, CompiledNetwork
    from .receipt import build_receipt
    actions = tuple(actions)
    if any(action.conditional_context is not None for action in actions):
        raise ValueError('conditional interval context does not support network/FIFO composition')
    base=compile_network(workload,binding,hardware,production=production,max_cycles=max_cycles)
    raw=base.spec.model_dump(mode='json');tx=raw['batch']['transactions'];known={a.action_id for a in actions}
    units={u['unit_id'] for u in raw['graph']['execution_units']}
    before=[]
    for a in actions:
        if a.status!='calibrated' or a.duration_cycles is None:raise ValueError('component cost unavailable')
        if a.resource_id not in units:raise ValueError('unknown component resource')
        row=dict(transaction_id=a.action_id,depends_on=list(a.dependencies),start_cycles=a.earliest_start)
        if a.duration_cycles==0:
            counter=a.action_id+'.counter';raw['batch']['counters'].append(dict(counter_id=counter,initial_value=0))
            row.update(kind='signal',counter_id=counter,delta=1)
        else:row.update(kind='compute',unit_id=a.resource_id,duration_cycles=a.duration_cycles)
        before.append(row)
    for fid,deps in flow_dependencies.items():
        if fid not in base.native_members or not set(deps)<=known:raise ValueError('component-flow dependency not resolved')
        for t in tx:
            if t['transaction_id'] in base.native_members[fid]:t['depends_on']+=list(deps)
    raw['batch']['transactions']=before+tx
    raw['batch']['operation_envelopes'][0]['member_transactions']+=list(known)
    spec=SystemSpec.model_validate_json(json.dumps(raw));plan=compile_system(spec)
    compiled=CompiledNetwork(workload,binding,hardware,spec,plan,base.native_members,base.effective_timing)
    runtime=RuntimeContext(plan);obs=ResourceObserver(runtime);result=runtime.run()
    receipt=build_receipt(compiled,runtime,result,obs,None)
    receipt['component_actions']=[a.model_dump(mode='json') for a in actions]
    return ComponentExecution(result,runtime,receipt)

"""Router source-work adapters and fixture-only parameter execution."""
from .router_matrix import RouterWork,RouterMatrixFormula
from .component_service import ComponentAction

def router_work_from_plan(workload,operation_plan):
 from .workload import FrozenMoeWorkload
 workload=FrozenMoeWorkload.model_validate_json(workload.model_dump_json())
 if operation_plan.workload_sha256!=workload.workload_sha256:raise ValueError('router workload and operation-plan identity differ')
 stage=next(s for s in operation_plan.stages if s.stage_id=='R6')
 if not stage.active:raise ValueError('router stage is not active')
 return RouterWork(profile=workload.profile.name,route_V=operation_plan.V,router_invocations=1,feature_layout='source-hidden-stride-pe-row-shard',weight_locality='resident-source-packed-current-address',shape=(8,384,workload.profile.hidden_gating//8))

def router_plan_inventory(workload,operation_plan):
 work=router_work_from_plan(workload,operation_plan)
 return dict(workload_sha256=workload.workload_sha256,operation_plan_sha256=operation_plan.plan_sha256,stage='R6',route_V=work.route_V,router_invocations=1,shape=list(work.shape),precision='BF16',feature_stride=workload.profile.hidden_gating,weight_locality=work.weight_locality,
  useful_flops_per_rank=work.issued_flops_per_pe()*32,issued_flops_per_rank=work.issued_flops_per_pe()*32,workload_not_scaled_by_V=True,physical_publishable=False)

def execute_router_actions(formula,work,binding,hardware,*,resource_ids,pe_id=0,changes=None,extra_actions=(),production=True):
 from .component_service import execute_actions
 from .adalink_execute import _critical_to_completion
 if production:raise ValueError('router physical timing and real engine/locality/timebase binding not calibrated')
 actions=formula.actions(work,pe_id,resource_ids=resource_ids,changes=changes)
 from .workload import stable_sha256
 terminal=ComponentAction(action_id='R6.complete',stage='R6',component='M05',resource_id=resource_ids['MATRIX_WRITE'],work=1.0,work_unit='after-all-engine-complete',duration_cycles=0.0,duration_source='zero-service counted engine completion',calibration_sha256=formula.formula_sha256,dependencies=tuple(a.action_id for a in actions if a.action_id.endswith('.tail')),earliest_start=0.0,participants=(f'pe{pe_id}',),status='calibrated')
 execution=execute_actions(actions+(terminal,)+tuple(extra_actions),binding,hardware,production=False)
 execution.receipt['workload_identity_sha256']=stable_sha256(work.model_dump(mode='json'))
 execution.receipt['router_formula_sha256']=formula.formula_sha256
 execution.receipt['reported_unit']='reported_ns'
 execution.receipt['physical_publishable']=False
 execution.receipt['router_event_resource_critical_path']=_critical_to_completion(execution.receipt['causal_graph'],'R6.complete')
 return execution

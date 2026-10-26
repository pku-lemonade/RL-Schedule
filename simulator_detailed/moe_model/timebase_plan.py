"""Timebase/MFU attachment over frozen workload and operation-plan schemas."""
from .timebase import resolve_timebase,convert_time_value,model_timebase_gate
from .workload import FrozenMoeWorkload,stable_sha256
from .mfu import DecodeBaselineManifest

def operation_timebase_inventory(plan,timebases=None,*,physical=True):
 components=tuple(sorted({s.component for s in plan.stages if s.active}))
 gate=model_timebase_gate(timebases,components,operation='full_moe_decode',profile=plan.profile,boundary_id='R0-E8-full-moe',physical=physical)
 return dict(operation_plan_sha256=plan.plan_sha256,workload_sha256=plan.workload_sha256,binding_sha256=plan.binding_sha256,timebase_bundle_sha256=None if timebases is None else timebases.bundle_sha256,source_unit='model_cycle',global_timebase=gate,physical_publishable=gate['status']=='physical',stage_units=[dict(stage=s.stage_id,component=s.component,active=s.active,unit='model_cycle' if s.active else 'not_applicable',timebase_bound=False) for s in plan.stages])

def fixed_timebase_delta(domain,base_value,changed_value,*,workload_sha256,binding_sha256,component_model_sha256):
 base=convert_time_value(domain,base_value);changed=convert_time_value(domain,changed_value)
 return dict(workload_sha256=workload_sha256,binding_sha256=binding_sha256,component_model_sha256=component_model_sha256,timebase_domain_sha256=base['domain_sha256'],base=base,changed=changed,delta_seconds=changed['converted_seconds']-base['converted_seconds'],timebase_refitted=False,physical_publishable=False)

def mfu_workload_inputs(workload,baseline=None):
 workload=FrozenMoeWorkload.model_validate_json(workload.model_dump_json())
 if baseline is not None:baseline=DecodeBaselineManifest.model_validate_json(baseline.model_dump_json())
 full_ready=baseline is not None and baseline.completeness=='complete' and baseline.workload_sha256==workload.workload_sha256
 return dict(workload_sha256=workload.workload_sha256,flop_unit='FLOP',useful_model_flops=workload.flops.group_useful_flops,moe_local_useful_flops=workload.flops.group_useful_flops,issued_engine_padded_ops=workload.flops.group_issued_flops,
  full_decode_useful_flops=baseline.full_decode_useful_flops if full_ready else None,device_count=baseline.device_count if full_ready else None,selected_peak_id=baseline.selected_peak_id if full_ready else None,
  moe_boundary='R0-E8 full operation; requires actual scope and overlap manifest',full_decode_boundary=None if not full_ready else baseline.full_decode_boundary.model_dump(mode='json'),
  status='inputs_complete' if full_ready else 'full_decode_incomplete',missing_fields=[] if full_ready else ['same workload full-decode/non-MoE time/FLOPs/overlap/device-count/selected peak manifest'],useful_and_issued_are_distinct=True,physical_publishable=False)

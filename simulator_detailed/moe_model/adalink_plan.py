"""Source-backed E8 inventory adapter over the existing operation plan.

Exact chunks, ports and CclConfig are imported, never guessed from PE or rank
coordinates. Existing frozen operation-plan bytes remain unchanged.
"""
from .workload import stable_sha256

def adalink_operation_inventory(plan,profile,*,schedule=None,binding=None,comm_program=None):
 stage=next(s for s in plan.stages if s.stage_id=='E8')
 result=dict(stage_id='E8',active=stage.active,dependencies=list(stage.dependencies),component='M08',source_backed_rank_count=profile.ep_size,payload_bytes_per_rank=profile.output_buffer_bytes_per_rank,
  phases=['reduce_scatter_write_sum','barrier','all_gather_write','completion'],handoff_pe_count=32,commid_slot_idx=None,ccl_config_sha256=None,rank_map_sha256=None,comm_program_sha256=None,chunk_count=None,schedule_sha256=None,binding_sha256=None,production_ready=False)
 if any(x is not None for x in (schedule,binding,comm_program)):
  if any(x is None for x in (schedule,binding,comm_program)):raise ValueError('E8 adapter needs schedule, supplied binding and consuming comm program')
  if schedule.rank_count!=profile.ep_size or schedule.payload_bytes_per_rank!=profile.output_buffer_bytes_per_rank:raise ValueError('E8 collective workload identity differs from profile')
  if len(binding.ranks)!=schedule.rank_count:raise ValueError('E8 rank binding differs from schedule')
  comm_program.validate_schedule(schedule)
  slots={x.commid_slot_idx for x in binding.ccl_configs}
  if len(slots)!=1:raise ValueError('E8 config slots differ')
  result.update(commid_slot_idx=next(iter(slots)),ccl_config_sha256=stable_sha256([x.model_dump(mode='json') for x in binding.ccl_configs]),rank_map_sha256=stable_sha256([x.model_dump(mode='json') for x in binding.ranks]),comm_program_sha256=comm_program.program_sha256,chunk_count=len(schedule.chunks),schedule_sha256=schedule.schedule_sha256,binding_sha256=binding.binding_sha256)
 result['inventory_sha256']=stable_sha256(result);return result

"""M06 source-shaped Vector work contracts; timing is supplied by calibration."""
from typing import Literal
from pydantic import Field,model_validator
from .workload import FrozenRecord,PROFILES

VectorOperation=Literal['rmsnorm','quant','softmax_topk','table','silu_score_quant']

class VectorWork(FrozenRecord):
    work_id: str
    profile: Literal['standalone','transformer-fused']
    operation: VectorOperation
    work_items: int = Field(ge=0,le=256)
    pe_id: int = Field(ge=0,lt=32)
    @model_validator(mode='after')
    def source_participants(self):
        if not self.work_id:raise ValueError('Vector work id required')
        if self.operation=='silu_score_quant' and self.work_items and self.pe_id not in (28,29,30,31):
            raise ValueError('SiLU/score quant executes only on column roots')
        return self
    def real_stage(self):
        return {'rmsnorm':'R1','quant':'R3','softmax_topk':'R8/R9','table':'R12','silu_score_quant':'E2'}[self.operation]
    def shape(self):
        profile=PROFILES[self.profile];gate_n=profile.gate_up_per_pe[1];gate_k=profile.gate_up_per_pe[2];down_k=profile.down_per_pe[2]
        if self.operation=='rmsnorm':return dict(input_elements=profile.hidden_gating,output_elements=profile.hidden_gating,input_dtype='fp32+residual',output_dtype='fp32-hidden+bf16-norm',
                                                  fused_pre_norm=self.profile=='transformer-fused')
        if self.operation=='quant':return dict(input_elements=profile.hidden_moe,quant_shards=8,quant_elements_per_shard=gate_k,scale_slot_bytes=128,input_dtype='bf16',output_dtype='int8+fp32-scale')
        if self.operation=='softmax_topk':return dict(input_elements=profile.global_experts,top_k=profile.top_k,experts_per_rank=profile.experts_per_rank,physical_output_bytes=2048,input_dtype='fp32',output_dtype='fp32+int32')
        if self.operation=='table':
            table_entries=4 if self.profile=='standalone' else 3
            return dict(metadata_records=self.work_items,metadata_bytes_per_record=12,mlp1_table_entries_per_record=table_entries,
                        mlp1_table_bytes_per_record=table_entries*8,gm_scatter_bytes_per_record=8,weight_payload_moved=False)
        return dict(input_elements_per_record=gate_n,valid_output_elements=gate_n//2,padded_output_elements=down_k,
                    zero_pad_elements=down_k-gate_n//2,score_elements_per_record=1,scale_slot_bytes=128,input_dtype='fp32',output_dtype='int8+fp32-scale')
    def physical_sram_bytes(self):
        shape=self.shape();count=self.work_items
        if self.operation=='rmsnorm':return count*shape['input_elements']*(16 if self.profile=='transformer-fused' else 14)
        if self.operation=='quant':return count*(shape['input_elements']*2+shape['quant_shards']*(shape['quant_elements_per_shard']+shape['scale_slot_bytes']))
        if self.operation=='softmax_topk':return count*(shape['input_elements']*4+shape['physical_output_bytes'])
        if self.operation=='table':return count*(shape['metadata_bytes_per_record']+shape['mlp1_table_bytes_per_record']+shape['gm_scatter_bytes_per_record'])
        return count*(shape['input_elements_per_record']*4+4+shape['padded_output_elements']+shape['scale_slot_bytes'])
    def resource(self):return f'pe{self.pe_id}-VECTOR'
    def bulk_v(self):return self.operation in ('table','silu_score_quant')

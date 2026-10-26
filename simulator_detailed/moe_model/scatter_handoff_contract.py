"""Source work and fail-closed identity contract for uncovered E5/E6/E7 cuts.

There is no accepted timing formula here. API return, queue order and generic
NMC timestamps cannot substitute for address-correlated admission/visibility.
"""
from collections import Counter
from typing import Literal
import math
import re
from pydantic import Field, model_validator
from .workload import FrozenRecord, stable_sha256

E5_PREDECESSOR = 'm03-c03-transformer-fused-E5-controlled_overlap-B65536'
E7_SUCCESSOR = 'm07-source-fused-E7-commid16-C32-W14'
SOURCE_CALLSITE = 'moe_gemm.hpp:253-273,107-139'
E5_E6_BLOCK = 'E5_E6_REAL_TOKEN_ADDRESS_AND_CORRELATED_ADMISSION_REQUIRED'
E6_E7_BLOCK = 'E6_E7_LAST_SCATTER_VISIBILITY_TO_RELEASE_IDENTITY_REQUIRED'

class ScatterHandoffWork(FrozenRecord):
    profile: Literal['transformer-fused'] = 'transformer-fused'
    V: Literal[64] = 64
    participants: tuple[int, ...] = tuple(range(32))
    roots: tuple[int, ...] = tuple(range(3, 32, 4))
    row_count: Literal[8] = 8
    columns_per_row: Literal[4] = 4
    down_elements: Literal[256] = 256
    row_output_bytes: Literal[65536] = 65536
    payload_per_request: Literal[1024] = 1024
    requests_per_pe: Literal[16] = 16
    total_requests: Literal[512] = 512
    total_scatter_bytes: Literal[524288] = 524288
    channel: Literal[1] = 1
    primitive: Literal['single_side_write_sum'] = 'single_side_write_sum'
    source_callsite: Literal[SOURCE_CALLSITE] = SOURCE_CALLSITE

    @model_validator(mode='after')
    def exact(self):
        if self.participants != tuple(range(32)) or self.roots != tuple(range(3,32,4)):
            raise ValueError('scatter handoff participant/root identity')
        if (self.row_output_bytes,self.total_requests,self.total_scatter_bytes) != (
            self.V*self.down_elements*4,32*self.requests_per_pe,32*self.requests_per_pe*self.payload_per_request
        ):
            raise ValueError('scatter handoff source byte/count identity')
        return self

    def request_slots(self, pe):
        if type(pe) is not int or not 0 <= pe < 32:
            raise ValueError('scatter handoff invalid PE')
        return tuple(range(pe % 4, self.V, 4))

    def controller(self, pe):
        self.request_slots(pe)
        return pe % 4

    def source_rows(self, pe):
        return tuple(i*self.down_elements*4 for i in self.request_slots(pe))

def extract_scatter_handoff_source(moe_source, row_source):
    """Reject source drift before deriving static work; never invent token values."""
    code=re.sub(r'//[^\n]*|/\*.*?\*/','',moe_source,flags=re.S)
    compact=''.join(code.split())
    expected=(
        'pe_rowwise_reduce<0,1>(down_res,down_res);',
        'pe_rowwise_broadcast<1>(down_res);',
        'perform_gm_scatter(valid_num,pe_id,down_res,gm_scatter_table);',
        'constexprintSCATTER_PE_DIVISOR=COL_NUM;',
        'intoc_tp_id=pe_id/SCATTER_PE_DIVISOR;',
        'intic_tp_id=pe_id%SCATTER_PE_DIVISOR;',
        'for(inti=ic_tp_id;i<valid_num;i+=SCATTER_PE_DIVISOR)',
        'inttoken_idx=dlcm_tl_sram->moe.topk_token_ids[i];',
        '&tl_gsram->moe.output[token_idx*HIDDEN_SIZE+oc_tp_id*DOWN_N]',
        'DmaParams_v1{ic_tp_id}',
        'ada_sync_release_comm_id(PE_UPLOAD_1,ADALINK,MOE_FULLMESH_ADALINK_MASK,MOE_FULLMESH_SYNC_ID,1,COMM_PRODUCE_CNT);',
    )
    if any(x not in compact for x in expected):
        raise ValueError('scatter handoff source/callsite drift')
    row=''.join(re.sub(r'//[^\n]*|/\*.*?\*/','',row_source,flags=re.S).split())
    if 'native_send<channel>(input,NodeType::PE,pe_id+1,1,reduce_type,pe_id-1,-1)' not in row or 'native_broadcast<channel>(view,NodeType::PE,dst_mask_sync)' not in row:
        raise ValueError('scatter handoff row chain source drift')
    return ScatterHandoffWork()

class ScatterAddressIdentity(FrozenRecord):
    workload_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    rank: int = Field(ge=0,lt=4)
    origin: Literal['real_decode','synthetic_fixture']
    authorized: bool
    token_indices: tuple[int,...]
    gm_base: int = Field(ge=0)
    sram_base: int = Field(ge=0)
    buffer_binding_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_refs: tuple[str,...]

    @model_validator(mode='after')
    def exact(self):
        if len(self.token_indices)!=64 or any(type(x) is not int or not 0<=x<32 for x in self.token_indices):
            raise ValueError('scatter real token work required')
        if any(n>8 for n in Counter(self.token_indices).values()) or self.gm_base%128 or self.sram_base%128 or not self.source_refs:
            raise ValueError('scatter token/address/source identity')
        return self

    def requests(self, *, production=True):
        if production and (self.origin!='real_decode' or not self.authorized):
            raise ValueError('real authorized scatter address identity required')
        work=ScatterHandoffWork();out=[]
        for pe in work.participants:
            for ordinal,i in enumerate(work.request_slots(pe)):
                out.append(dict(transaction_id=f'rank{self.rank}.E6.pe{pe}.i{i}',pe=pe,ordinal=ordinal,selection_index=i,token_id=self.token_indices[i],controller_id=pe%4,channel=1,payload_bytes=1024,primitive='single_side_write_sum',source_address=self.sram_base+i*1024,address=self.gm_base+4*(self.token_indices[i]*2048+(pe//4)*256),buffer_binding_sha256=self.buffer_binding_sha256))
        return tuple(out)

class ScatterHandoffEvent(FrozenRecord):
    run_id: str
    generation: int = Field(ge=0)
    transaction_id: str
    pe: int = Field(ge=0,lt=32)
    ordinal: int = Field(ge=0,lt=16)
    selection_index: int = Field(ge=0,lt=64)
    controller_id: int = Field(ge=0,lt=4)
    channel: Literal[1] = 1
    payload_bytes: Literal[1024] = 1024
    primitive: Literal['single_side_write_sum'] = 'single_side_write_sum'
    address: int = Field(ge=0)
    source_address: int = Field(ge=0)
    buffer_binding_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    phase: Literal['admission','visible_completion']
    timestamp: float = Field(ge=0)
    active_unit: Literal['simulation_seconds'] = 'simulation_seconds'
    source_callsite: Literal[SOURCE_CALLSITE] = SOURCE_CALLSITE

    @model_validator(mode='after')
    def exact(self):
        if not self.run_id or not self.transaction_id or not math.isfinite(self.timestamp):
            raise ValueError('scatter event missing identity/time')
        if self.controller_id!=self.pe%4 or self.selection_index!=self.pe%4+4*self.ordinal:
            raise ValueError('scatter event controller/selection identity')
        return self

def require_correlated_scatter_frontier(identity,events,*,run_id,generation,phase,production=True):
    """Validate complete supplied identity only; this does not calibrate a cost."""
    if phase not in ('admission','visible_completion'):
        raise ValueError('scatter phase outside handoff contract')
    expected={x['transaction_id']:x for x in identity.requests(production=production)}
    rows=tuple(ScatterHandoffEvent.model_validate_json(x.model_dump_json()) for x in events)
    if len(rows)!=512 or {x.transaction_id for x in rows}!=set(expected):
        raise ValueError('scatter missing/duplicate request frontier')
    for x in rows:
        e=expected[x.transaction_id]
        if (x.run_id,x.generation,x.phase)!=(run_id,generation,phase):
            raise ValueError('scatter run/generation/phase mismatch')
        if any(getattr(x,k)!=e[k] for k in ('pe','ordinal','selection_index','controller_id','channel','payload_bytes','primitive','address','source_address','buffer_binding_sha256')):
            raise ValueError('scatter address/request/resource mismatch')
    return {pe:max(x.timestamp for x in rows if x.pe==pe) for pe in range(32)}

def scatter_handoff_blockers():
    return dict(status='BLOCKED_SOURCE_SCATTER_HANDOFF_IDENTITY',accepted_domains=0,calibration=0,validation=0,parameter_paths=[],physical_publishable=False,candidates=[
        dict(action='E5_E6.local_handoff',status=E5_E6_BLOCK,source_work=ScatterHandoffWork().model_dump(mode='json'),missing_fields=['same-request ordered topk_token_ids[0..63]','GM/SRAM buffer binding and addresses','per-request PE_UPLOAD1 admission with transaction/generation/address identity'],known_controller='explicit source pe%4, not a default controller',sub_boundary='post-broadcast local DOWNLOAD1/Vector-to-UPLOAD1 sync is source-known but ends before token lookup/descriptor/admission; it cannot substitute for the requested cut'),
        dict(action='E6_E7.local_handoff',status=E6_E7_BLOCK,missing_fields=['per-request WRITE_SUM visible completion correlated to all 512 descriptors','last completion on each PE_UPLOAD1 queue linked to comm-id16 release generation/transaction'],excluded_owner=E7_SUCCESSOR,sub_boundary='E7 owns explicit comm-id release edges through counted acquire completion; reusing these edges would duplicate accepted ownership')])

def require_scatter_handoff_timing(action):
    if action not in ('E5_E6.local_handoff','E6_E7.local_handoff'):
        raise ValueError('scatter handoff action outside contract')
    row=next(x for x in scatter_handoff_blockers()['candidates'] if x['action']==action)
    raise ValueError(row['status']+': no calibrated timing entry or independent physical parameter')

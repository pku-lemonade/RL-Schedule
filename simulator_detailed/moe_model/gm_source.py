"""Source GM primitive identity and supplied-buffer work expansion.

Single-sided PE uploads are distinct from dual-sided WDMA receives. No cost is
borrowed from ordinary writes or inferred from a source address/controller ID.
"""
from collections import Counter
from typing import Literal
from pydantic import Field,model_validator
from .workload import FrozenRecord,PROFILES,stable_sha256
from .gm_service import GMTransaction

class GMSourceWork(FrozenRecord):
    profile:Literal['standalone','transformer-fused']
    stage:Literal['R0','R10','R11','E6','mechanism-control']
    primitive:Literal['dual_read','dual_write','dual_write_sum','single_write','single_write_sum','metadata_single','metadata_dual','scatter_single_write_sum']
    V:int=Field(ge=0,le=256)
    payload_bytes:int=Field(ge=0)
    source_pes:tuple[int,...]
    controller_by_pe:tuple[int,...]
    controller_binding_source:str
    channel:Literal[1]
    noc:Literal[1]
    address_groups:int=Field(gt=0)
    gm_base:int=Field(ge=0)
    sram_base:int=Field(ge=0)
    buffer_binding:str
    locality:str
    fixture_only:bool
    per_pe_counts:tuple[int,...]=()
    token_indices:tuple[int,...]=()
    source_commit:str=Field(pattern=r'^[0-9a-f]{40}$')
    @model_validator(mode='after')
    def source_contract(self):
        if not self.source_pes or len(set(self.source_pes))!=len(self.source_pes) or any(not 0<=p<32 for p in self.source_pes) or len(self.controller_by_pe)!=len(self.source_pes) or any(not 0<=c<4 for c in self.controller_by_pe):raise ValueError('GM explicit PE/controller identity invalid')
        if not self.controller_binding_source or not self.buffer_binding or not self.locality or self.gm_base%128 or self.sram_base%128:raise ValueError('GM buffer/locality/alignment required')
        if self.stage=='R0' and self.primitive!='dual_read':raise ValueError('R0 source primitive mismatch')
        if self.stage=='R10' and self.primitive!=('single_write_sum' if self.profile=='standalone' else 'dual_write_sum'):raise ValueError('R10 profile primitive mismatch')
        if self.stage=='R11':
            if self.primitive!=('metadata_single' if self.profile=='standalone' else 'metadata_dual') or len(self.per_pe_counts)!=32 or any(not 0<=n<=8 for n in self.per_pe_counts) or sum(self.per_pe_counts)!=self.V:raise ValueError('R11 dynamic lengths/prefix or primitive missing')
            if self.payload_bytes!=0:raise ValueError('R11 requires individual dynamic row bytes, not average payload')
        if self.stage=='R10' and (self.payload_bytes!=128 or self.source_pes!=tuple(range(32)) or (self.profile=='transformer-fused' and set(self.controller_by_pe)!={0})):raise ValueError('R10 requires 32 contributions of 128 bytes and source/supplied controller mapping')
        if self.stage=='R11' and (self.source_pes!=tuple(range(32)) or (self.profile=='transformer-fused' and self.controller_by_pe!=tuple(p%4 for p in range(32)))):raise ValueError('R11 source controller mapping mismatch')
        if (self.stage!='R11' and self.payload_bytes<=0) or self.payload_bytes%4:raise ValueError('GM fp32 payload required')
        if self.stage=='E6':
            if self.primitive!='scatter_single_write_sum' or self.source_pes!=tuple(range(32)) or self.controller_by_pe!=tuple(p%4 for p in range(32)):raise ValueError('E6 requires source single upload 32PE column binding')
            if self.payload_bytes!=PROFILES[self.profile].output_hidden//8*4 or len(self.token_indices)!=self.V or any(not 0<=t<32 for t in self.token_indices) or any(n>8 for n in Counter(self.token_indices).values()):raise ValueError('E6 source payload/token/top-k work mismatch')
        return self
    def identity(self):return stable_sha256(self.model_dump(mode='json'))
    def expand(self):
        result=[];descriptors=[];op='read' if self.primitive=='dual_read' else 'write_sum' if 'write_sum' in self.primitive else 'write'
        for index,pe in enumerate(self.source_pes):
            controller=self.controller_by_pe[index];rid=f'gm.{"rdma" if op=="read" else "wdma"}{controller}.channel{self.channel}'
            parts=[]
            if self.stage=='R11':
                length=self.per_pe_counts[pe];off=sum(self.per_pe_counts[:pe])
                if length==0:continue
                parts=[(self.gm_base+(row*256+off)*4,length*4,pe,row) for row in range(3)]
            elif self.stage=='E6':
                parts=[(self.gm_base+(self.token_indices[i]*PROFILES[self.profile].output_hidden+pe//4*(self.payload_bytes//4))*4,self.payload_bytes,self.token_indices[i],i) for i in range(pe%4,self.V,4)]
            else:parts=[(self.gm_base+(pe%self.address_groups)*self.payload_bytes,self.payload_bytes,pe,0)]
            for address,size,token,seq in parts:
                identity=f'{self.stage}.pe{pe}.{seq}';result.append(GMTransaction(transaction_id=identity,source_pe=pe,token_id=token,output_shard=pe//4,address=address,payload_bytes=size,operation=op,controller_id=rid));descriptors.append(f'{self.stage}.pe{pe}' if self.stage=='R11' else identity)
        return tuple(result),tuple(descriptors)
    def inventory(self):
        tx,desc=self.expand();return dict(work_sha256=self.identity(),primitive=self.primitive,stage=self.stage,logical_bytes=sum(t.payload_bytes for t in tx),segments=len(tx),descriptors=len(set(desc)),transactions=[t.model_dump(mode='json') for t in tx],descriptor_ids=desc,physical_route_inferred=False,fixture_only=self.fixture_only)

class GMServiceObservation(FrozenRecord):
    transaction_id:str
    controller_id:str
    channel:int=Field(ge=0,le=1)
    primitive:str
    address:int=Field(ge=0)
    payload_bytes:int=Field(gt=0)
    contributors:int=Field(gt=0)
    unit:Literal['reported_ns','reported_cycle']
    admission:float=Field(ge=0)
    service_start:float=Field(ge=0)
    service_end:float=Field(ge=0)
    visible_completion:float=Field(ge=0)
    boundary_source:str
    @model_validator(mode='after')
    def ordered(self):
        import math
        if not all(math.isfinite(v) for v in (self.admission,self.service_start,self.service_end,self.visible_completion)):raise ValueError('GM observation time must be finite')
        if not self.admission<=self.service_start<=self.service_end<=self.visible_completion or not self.transaction_id or not self.controller_id or not self.boundary_source:raise ValueError('GM correlated service observation incomplete')
        return self

def require_gm_service_observations(work,observations):
    """Transport/FIFO and uncorrelated timestamps are never memory service."""
    tx,_=work.expand();expected={t.transaction_id:t for t in tx}
    if len(observations)!=len(expected) or {o.transaction_id for o in observations}!=set(expected):raise ValueError('GM independent correlated service boundaries missing')
    counts=Counter((t.address,t.payload_bytes) for t in tx if t.operation=='write_sum')
    for o in observations:
        t=expected[o.transaction_id]
        if o.contributors!=counts.get((t.address,t.payload_bytes),1):raise ValueError('GM contributor identity mismatch')
        if (o.controller_id,o.channel,o.primitive,o.address,o.payload_bytes)!=(t.controller_id,work.channel,work.primitive,t.address,t.payload_bytes):raise ValueError('GM service identity mismatch')
    return True

def gm_source_work_from_plan(workload,plan,stage,*,gm_base,sram_base,buffer_binding,locality,controller_by_pe=None,controller_binding_source=None):
    """Derive only source semantics; callers must supply addresses/locality."""
    from .workload import FrozenMoeWorkload
    workload=FrozenMoeWorkload.model_validate_json(workload.model_dump_json())
    if plan.workload_sha256!=workload.workload_sha256 or not next(s for s in plan.stages if s.stage_id==stage).active:raise ValueError('GM source inactive or different workload plan')
    if stage not in ('R0','R10','R11','E6'):raise ValueError('GM source stage outside this contract')
    fused=workload.profile.name=='transformer-fused';pes=tuple(range(32));selected=sorted((r for r in workload.records if r.ep_rank==plan.ep_rank and r.status=='valid'),key=lambda r:(r.token_id,r.topk_slot))
    counts=tuple(sum(r.token_id==p for r in selected) for p in pes)
    auto=not fused and stage in ('R10','R11')
    if auto and (controller_by_pe is None or not controller_binding_source):raise ValueError('GM dma_id=-1 requires supplied resolved controller binding; cannot infer GM0')
    expected=tuple((p//8 if stage=='R0' else p%4 if stage=='E6' or stage=='R11' and fused else 0) for p in pes)
    if not auto and controller_by_pe is not None and tuple(controller_by_pe)!=expected:raise ValueError('GM explicit source controller mapping mismatch')
    mapping=tuple(controller_by_pe) if auto else expected
    primitive={'R0':'dual_read','R10':'dual_write_sum' if fused else 'single_write_sum','R11':'metadata_dual' if fused else 'metadata_single','E6':'scatter_single_write_sum'}[stage]
    return GMSourceWork(profile=workload.profile.name,stage=stage,primitive=primitive,V=plan.V,payload_bytes={'R0':workload.profile.output_hidden*4,'R10':128,'R11':0,'E6':workload.profile.output_hidden//8*4}[stage],source_pes=pes,controller_by_pe=mapping,controller_binding_source=controller_binding_source if auto else 'fixed-source-explicit-DMA-selection',channel=1,noc=1,address_groups=32 if stage=='R0' else 1,gm_base=gm_base,sram_base=sram_base,buffer_binding=buffer_binding,locality=locality,fixture_only=workload.trace_kind!='real_decode',per_pe_counts=counts if stage=='R11' else (),token_indices=tuple(r.token_id for r in selected) if stage=='E6' else (),source_commit=workload.identity.source_commit)

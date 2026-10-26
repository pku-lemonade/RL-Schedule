"""M03 source-backed PE chain graph and supplied-resource composition.

A chain is packet-streamed: packet j may proceed after predecessor j, while
later packets overlap. Root self contribution is explicit. No one-expert-per-PE
or complete-flow serial chain is introduced.
"""
from dataclasses import dataclass
import json, math
from typing import Literal
from pydantic import Field, model_validator
from .workload import FrozenRecord, stable_sha256
from .binding import LogicalFlow, NetworkWorkload, freeze_network_workload

class PEChain(FrozenRecord):
    collective_id: str
    axis: Literal['column','row']
    payload_bytes: int = Field(gt=0)
    packet_bytes: int = Field(gt=0)
    channel: Literal[1] = 1
    @model_validator(mode='after')
    def shape(self):
        if self.payload_bytes % 4:raise ValueError('fp32 reduce payload must align to 4 bytes')
        return self
    def groups(self):
        if self.axis=='column':return tuple(tuple(c+4*r for r in range(8)) for c in range(4))
        return tuple(tuple(4*r+c for c in range(4)) for r in range(8))
    def logical_plan(self):
        groups=[]
        for gi,pes in enumerate(self.groups()):
            hops=[dict(source=a,destination=b,kind='route_reduce_sum',hop=i) for i,(a,b) in enumerate(zip(pes,pes[1:]))]
            groups.append(dict(group_id=gi,participants=pes,root=pes[-1],hops=hops,
                root_self_send=dict(source=pes[-1],destination=pes[-1],kind='local_contribution'),
                broadcast=dict(source=pes[-1],destinations=pes[:-1],fanout=len(pes)-1)))
        return dict(collective_id=self.collective_id,axis=self.axis,channel=self.channel,payload_bytes=self.payload_bytes,
            packet_count=math.ceil(self.payload_bytes/self.packet_bytes),groups=groups,cooperative_pes=32,
            logical_reduce_link_bytes=sum(len(g['hops'])*self.payload_bytes for g in groups),
            broadcast_source_bytes=len(groups)*self.payload_bytes,weight_sram_noc=False)
    def streaming_flows(self):
        flows=[]
        for gi,pes in enumerate(self.groups()):
            tails=[]
            for pi,offset in enumerate(range(0,self.payload_bytes,self.packet_bytes)):
                previous=()
                for hi,(a,b) in enumerate(zip(pes,pes[1:])):
                    fid=f'{self.collective_id}.g{gi}.p{pi}.hop{hi}'
                    flows.append(LogicalFlow(flow_id=fid,source_role=f'pe{a}',destination_roles=(f'pe{b}',),
                        payload_bytes=min(self.packet_bytes,self.payload_bytes-offset),primitive='point_to_point',depends_on=previous))
                    previous=(fid,)
                tails.extend(previous)
            flows.append(LogicalFlow(flow_id=f'{self.collective_id}.g{gi}.broadcast',source_role=f'pe{pes[-1]}',
                destination_roles=tuple(f'pe{pe}' for pe in pes[:-1]),payload_bytes=self.payload_bytes,
                primitive='masked_multicast',depends_on=tuple(tails)))
        return tuple(flows)

class CollectiveFormula(FrozenRecord):
    component: Literal['M03']='M03'
    formula_id: str
    source_refs: tuple[str,...]
    per_packet_merge_cycles: float | None
    per_pe_issue_cycles: float | None
    root_handoff_cycles: float | None
    status: Literal['calibrated','unavailable']
    applicability: str
    @model_validator(mode='after')
    def values(self):
        values=(self.per_packet_merge_cycles,self.per_pe_issue_cycles,self.root_handoff_cycles)
        if self.status=='unavailable' and any(v is not None for v in values):raise ValueError('unavailable collective has no costs')
        if self.status=='calibrated' and any(v is None or v<0 or not math.isfinite(v) for v in values):raise ValueError('invalid collective cost')
        if not self.source_refs:raise ValueError('collective formula requires source')
        return self

"""M08 schedule-driven ADALINK collective work; no timing or topology defaults."""
from __future__ import annotations
from collections import Counter
from typing import Literal
from pydantic import Field,model_validator
from .workload import FrozenRecord,stable_sha256

Phase=Literal['handoff','reduce_scatter','barrier','all_gather','completion']
ActionKind=Literal['transfer','local_reduce','signal','wait']

class CollectiveChunk(FrozenRecord):
    chunk_id: str
    offset_bytes: int = Field(ge=0)
    length_bytes: int = Field(gt=0)
    owner_rank: int = Field(ge=0)

class AdalinkAction(FrozenRecord):
    action_id: str
    kind: ActionKind
    phase: Phase
    rank: int | None = Field(default=None,ge=0)
    peer_rank: int | None = Field(default=None,ge=0)
    participant: str | None=None
    chunk_id: str | None=None
    step_index: int | None = Field(default=None,ge=0)
    work_bytes: int = Field(default=0,ge=0)
    counter_id: str | None=None
    delta: int | None = Field(default=None,gt=0)
    threshold: int | None = Field(default=None,gt=0)
    depends_on: tuple[str,...]=()
    @model_validator(mode='after')
    def semantics(self):
        if not self.action_id:raise ValueError('action id required')
        if self.kind=='transfer':
            if self.rank is None or self.peer_rank is None or self.rank==self.peer_rank or self.chunk_id is None or self.step_index is None or self.work_bytes<=0:raise ValueError('transfer fields incomplete')
            if any(x is not None for x in (self.counter_id,self.delta,self.threshold)):raise ValueError('transfer cannot carry counter fields')
            if self.phase not in ('reduce_scatter','all_gather'):raise ValueError('transfer phase invalid')
        elif self.kind=='local_reduce':
            if self.rank is None or self.peer_rank is not None or self.chunk_id is None or self.step_index is None or self.work_bytes<=0:raise ValueError('local reduce fields incomplete')
            if self.phase!='reduce_scatter':raise ValueError('local reduce phase invalid')
        elif self.kind=='signal':
            if not self.counter_id or self.delta is None or self.threshold is not None:raise ValueError('signal fields incomplete')
        elif not self.counter_id or self.threshold is None or self.delta is not None:raise ValueError('wait fields incomplete')
        return self

class AdalinkSchedule(FrozenRecord):
    schema_version: int=1
    schedule_id: str
    algorithm: Literal['ring_reduce_scatter_all_gather','direct_reduce_all_gather','supplied_explicit']
    rank_count: int = Field(gt=1)
    payload_bytes_per_rank: int = Field(gt=0)
    pe_completion_count_per_rank: int = Field(default=32,gt=0)
    chunks: tuple[CollectiveChunk,...]
    actions: tuple[AdalinkAction,...]
    completion_action_id: str
    workload_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_refs: tuple[str,...]
    fixture_only: bool
    schedule_sha256: str
    @model_validator(mode='after')
    def integrity(self):
        if not self.schedule_id or not self.source_refs or not self.actions:raise ValueError('schedule identity/source/actions required')
        ids=[x.chunk_id for x in self.chunks]
        if len(ids)!=len(set(ids)) or not ids:raise ValueError('chunk ids required and unique')
        cursor=0
        for chunk in sorted(self.chunks,key=lambda x:x.offset_bytes):
            if chunk.offset_bytes!=cursor or chunk.owner_rank>=self.rank_count:raise ValueError('chunks must exactly partition payload')
            cursor+=chunk.length_bytes
        if cursor!=self.payload_bytes_per_rank:raise ValueError('chunk bytes do not conserve payload')
        known=set();signals=Counter();chunk_bytes={x.chunk_id:x.length_bytes for x in self.chunks};phase_transfers=Counter();phase_reduces=Counter()
        for action in self.actions:
            if action.action_id in known or not set(action.depends_on)<=known:raise ValueError('actions must be unique and topological')
            known.add(action.action_id)
            for rank in (action.rank,action.peer_rank):
                if rank is not None and rank>=self.rank_count:raise ValueError('action rank outside schedule')
            if action.chunk_id is not None:
                if action.chunk_id not in chunk_bytes or action.work_bytes!=chunk_bytes[action.chunk_id]:raise ValueError('action chunk bytes mismatch')
            if action.kind=='signal':signals[action.counter_id]+=action.delta
            elif action.kind=='wait' and action.threshold>signals[action.counter_id]:raise ValueError('counter wait unreachable')
            if action.kind=='transfer' and action.phase in ('reduce_scatter','all_gather'):phase_transfers[action.phase,action.chunk_id]+=1
            if action.kind=='local_reduce':phase_reduces[action.phase,action.chunk_id]+=1
        expected=self.rank_count-1
        for chunk in ids:
            if phase_transfers['reduce_scatter',chunk]!=expected or phase_transfers['all_gather',chunk]!=expected or phase_reduces['reduce_scatter',chunk]!=expected:
                raise ValueError('allreduce schedule must explicitly cover each chunk')
        completion=next((x for x in self.actions if x.action_id==self.completion_action_id),None)
        if completion is None or completion.kind!='wait' or completion.phase!='completion':raise ValueError('completion must name final wait')
        if stable_sha256(self.model_dump(mode='json',exclude={'schedule_sha256'}))!=self.schedule_sha256:raise ValueError('schedule hash mismatch')
        return self
    def bytes_by_phase(self):
        result=Counter()
        for action in self.actions:
            if action.kind=='transfer':result[action.phase]+=action.work_bytes
        return dict(result)

def freeze_schedule(**kwargs):
    from pydantic import TypeAdapter
    raw=dict(kwargs);raw.setdefault('schema_version',1);shape=TypeAdapter(dict).dump_python(raw,mode='json')
    return AdalinkSchedule(**raw,schedule_sha256=stable_sha256(shape))

def _chunks(payload,rank_count):
    base=payload//rank_count;tail=payload-base*(rank_count-1);offset=0;out=[]
    for rank in range(rank_count):
        length=tail if rank==rank_count-1 else base;out.append(CollectiveChunk(chunk_id=f'chunk{rank}',offset_bytes=offset,length_bytes=length,owner_rank=rank));offset+=length
    return tuple(out)

def _handoff(rank_count,pe_count):
    actions=[];waits=[]
    for rank in range(rank_count):
        counter=f'rank{rank}.pe-complete'
        for pe in range(pe_count):actions.append(AdalinkAction(action_id=f'rank{rank}.handoff.pe{pe}',kind='signal',phase='handoff',rank=rank,participant=f'PE{pe}',counter_id=counter,delta=1))
        wait=f'rank{rank}.handoff.wait';actions.append(AdalinkAction(action_id=wait,kind='wait',phase='handoff',rank=rank,counter_id=counter,threshold=pe_count));waits.append(wait)
    return actions,tuple(waits)

def _workload_hash(workload_sha256,fixture_only,schedule_id,rank_count,payload_bytes_per_rank,pe_completion_count_per_rank):
    if workload_sha256 is not None:return workload_sha256
    if not fixture_only:raise ValueError('production ADALINK schedule requires workload_sha256')
    return stable_sha256(dict(fixture_only=True,schedule_id=schedule_id,rank_count=rank_count,
      payload_bytes_per_rank=payload_bytes_per_rank,pe_completion_count_per_rank=pe_completion_count_per_rank))

def build_ring_schedule(schedule_id,rank_count,payload_bytes_per_rank,*,fixture_only,source_refs,pe_completion_count_per_rank=32,workload_sha256=None):
    chunks=_chunks(payload_bytes_per_rank,rank_count);actions,handoffs=_handoff(rank_count,pe_completion_count_per_rank);rs_done=[]
    for chunk in chunks:
        path=tuple((chunk.owner_rank+i)%rank_count for i in range(1,rank_count+1));previous=None
        for step,(source,dest) in enumerate(zip(path,path[1:])):
            tid=f'rs.{chunk.chunk_id}.step{step}.send';deps=tuple(dict.fromkeys(x for x in ((previous,handoffs[source],handoffs[dest]) if previous else (handoffs[source],handoffs[dest])) if x))
            actions.append(AdalinkAction(action_id=tid,kind='transfer',phase='reduce_scatter',rank=source,peer_rank=dest,chunk_id=chunk.chunk_id,step_index=step,work_bytes=chunk.length_bytes,depends_on=deps))
            rid=f'rs.{chunk.chunk_id}.step{step}.reduce';actions.append(AdalinkAction(action_id=rid,kind='local_reduce',phase='reduce_scatter',rank=dest,chunk_id=chunk.chunk_id,step_index=step,work_bytes=chunk.length_bytes,depends_on=(tid,)));previous=rid
        signal=f'rs.{chunk.chunk_id}.done';actions.append(AdalinkAction(action_id=signal,kind='signal',phase='barrier',rank=chunk.owner_rank,chunk_id=None,counter_id='rs.done',delta=1,depends_on=(previous,)));rs_done.append(signal)
    barrier='rs.barrier';actions.append(AdalinkAction(action_id=barrier,kind='wait',phase='barrier',counter_id='rs.done',threshold=len(chunks),depends_on=tuple(rs_done)))
    gather_done=[]
    for chunk in chunks:
        previous=barrier
        for step in range(rank_count-1):
            source=(chunk.owner_rank+step)%rank_count;dest=(source+1)%rank_count;tid=f'ag.{chunk.chunk_id}.step{step}.send'
            actions.append(AdalinkAction(action_id=tid,kind='transfer',phase='all_gather',rank=source,peer_rank=dest,chunk_id=chunk.chunk_id,step_index=step,work_bytes=chunk.length_bytes,depends_on=(previous,)));previous=tid
        signal=f'ag.{chunk.chunk_id}.done';actions.append(AdalinkAction(action_id=signal,kind='signal',phase='completion',rank=chunk.owner_rank,counter_id='collective.done',delta=1,depends_on=(previous,)));gather_done.append(signal)
    completion='collective.complete';actions.append(AdalinkAction(action_id=completion,kind='wait',phase='completion',counter_id='collective.done',threshold=len(chunks),depends_on=tuple(gather_done)))
    return freeze_schedule(schedule_id=schedule_id,algorithm='ring_reduce_scatter_all_gather',rank_count=rank_count,payload_bytes_per_rank=payload_bytes_per_rank,
      pe_completion_count_per_rank=pe_completion_count_per_rank,chunks=chunks,actions=tuple(actions),completion_action_id=completion,
      workload_sha256=_workload_hash(workload_sha256,fixture_only,schedule_id,rank_count,payload_bytes_per_rank,pe_completion_count_per_rank),source_refs=tuple(source_refs),fixture_only=fixture_only)

def build_direct_schedule(schedule_id,rank_count,payload_bytes_per_rank,*,fixture_only,source_refs,pe_completion_count_per_rank=32,workload_sha256=None):
    chunks=_chunks(payload_bytes_per_rank,rank_count);actions,handoffs=_handoff(rank_count,pe_completion_count_per_rank);rs_done=[]
    for chunk in chunks:
        reductions=[]
        for step,source in enumerate(rank for rank in range(rank_count) if rank!=chunk.owner_rank):
            tid=f'rs.{chunk.chunk_id}.rank{source}.send';actions.append(AdalinkAction(action_id=tid,kind='transfer',phase='reduce_scatter',rank=source,peer_rank=chunk.owner_rank,
              chunk_id=chunk.chunk_id,step_index=step,work_bytes=chunk.length_bytes,depends_on=(handoffs[source],handoffs[chunk.owner_rank])))
            rid=f'rs.{chunk.chunk_id}.rank{source}.reduce';actions.append(AdalinkAction(action_id=rid,kind='local_reduce',phase='reduce_scatter',rank=chunk.owner_rank,
              chunk_id=chunk.chunk_id,step_index=step,work_bytes=chunk.length_bytes,depends_on=(tid,)));reductions.append(rid)
        signal=f'rs.{chunk.chunk_id}.done';actions.append(AdalinkAction(action_id=signal,kind='signal',phase='barrier',rank=chunk.owner_rank,counter_id='rs.done',delta=1,depends_on=tuple(reductions)));rs_done.append(signal)
    barrier='rs.barrier';actions.append(AdalinkAction(action_id=barrier,kind='wait',phase='barrier',counter_id='rs.done',threshold=len(chunks),depends_on=tuple(rs_done)))
    gather_done=[]
    for chunk in chunks:
        sends=[]
        for step,dest in enumerate(rank for rank in range(rank_count) if rank!=chunk.owner_rank):
            tid=f'ag.{chunk.chunk_id}.rank{dest}.send';actions.append(AdalinkAction(action_id=tid,kind='transfer',phase='all_gather',rank=chunk.owner_rank,peer_rank=dest,
              chunk_id=chunk.chunk_id,step_index=step,work_bytes=chunk.length_bytes,depends_on=(barrier,)));sends.append(tid)
        signal=f'ag.{chunk.chunk_id}.done';actions.append(AdalinkAction(action_id=signal,kind='signal',phase='completion',rank=chunk.owner_rank,counter_id='collective.done',delta=1,depends_on=tuple(sends)));gather_done.append(signal)
    completion='collective.complete';actions.append(AdalinkAction(action_id=completion,kind='wait',phase='completion',counter_id='collective.done',threshold=len(chunks),depends_on=tuple(gather_done)))
    return freeze_schedule(schedule_id=schedule_id,algorithm='direct_reduce_all_gather',rank_count=rank_count,payload_bytes_per_rank=payload_bytes_per_rank,
      pe_completion_count_per_rank=pe_completion_count_per_rank,chunks=chunks,actions=tuple(actions),completion_action_id=completion,
      workload_sha256=_workload_hash(workload_sha256,fixture_only,schedule_id,rank_count,payload_bytes_per_rank,pe_completion_count_per_rank),source_refs=tuple(source_refs),fixture_only=fixture_only)

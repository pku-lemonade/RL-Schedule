"""Calibrated M06 named Vector engine formulas."""
from typing import Literal
from pydantic import Field,model_validator
from .workload import FrozenRecord
from .component_service import ComponentAction
import math

class RepeatedVectorSlice(FrozenRecord):
    engine: Literal['VECTOR_READ','VECTOR_CAL','VECTOR_WRITE']
    subevent: int = Field(ge=0)
    first_start_cycles: float = Field(ge=0)
    first_duration_cycles: float = Field(ge=0)
    transient_start_cycles: tuple[float,...]
    transient_duration_cycles: tuple[float,...]
    steady_interval_cycles: float = Field(gt=0)
    steady_duration_cycles: float = Field(ge=0)
    parameter_path: str
    @model_validator(mode='after')
    def valid(self):
        values=(self.first_start_cycles,self.first_duration_cycles,self.steady_interval_cycles,self.steady_duration_cycles,
                *self.transient_start_cycles,*self.transient_duration_cycles)
        if not self.transient_start_cycles or len(self.transient_start_cycles)!=len(self.transient_duration_cycles):raise ValueError('Vector transient prefix required')
        if not all(math.isfinite(v) and v>=0 for v in values) or not self.parameter_path:raise ValueError('invalid Vector timing')
        return self
    def timing(self,item):
        if type(item) is not int or item<0:raise ValueError('invalid item')
        if item==0:return self.first_start_cycles,self.first_duration_cycles
        prefix=len(self.transient_start_cycles)
        if item<=prefix:return self.transient_start_cycles[item-1],self.transient_duration_cycles[item-1]
        return self.transient_start_cycles[-1]+(item-prefix)*self.steady_interval_cycles,self.steady_duration_cycles

class RepeatedVectorFormula(FrozenRecord):
    kind: Literal['repeated']='repeated'
    component: Literal['M06']='M06'
    profile: str
    operation: str
    model_revision: str
    events_per_item: int = Field(gt=0)
    slices: tuple[RepeatedVectorSlice,...]
    calibration_sha256: str
    source_refs: tuple[str,...]
    applicability: str
    @model_validator(mode='after')
    def complete(self):
        expected={(engine,sub) for engine in ('VECTOR_READ','VECTOR_CAL','VECTOR_WRITE') for sub in range(self.events_per_item)}
        if {(s.engine,s.subevent) for s in self.slices}!=expected or not self.source_refs:raise ValueError('incomplete repeated Vector formula')
        return self
    def actions(self,work_id,pe_id,items,participants):
        if type(items) is not int or items<0:raise ValueError('invalid work item count')
        out=[]
        for item in range(items):
            for stage in self.slices:
                start,duration=stage.timing(item)
                out.append(ComponentAction(action_id=f'{work_id}.pe{pe_id}.item{item}.sub{stage.subevent}.{stage.engine}',stage=stage.engine,
                    component='M06',resource_id=f'pe{pe_id}-{stage.engine}',work=1,work_unit='source-shaped-vector-launch',duration_cycles=duration,
                    duration_source='M06 repeated transient/steady '+stage.parameter_path,calibration_sha256=self.calibration_sha256,
                    earliest_start=start,participants=tuple(participants),status='calibrated'))
        return tuple(out)

class BulkVectorSlice(FrozenRecord):
    engine: Literal['VECTOR_READ','VECTOR_CAL','VECTOR_WRITE']
    start_base_cycles: float
    start_per_item_cycles: float
    duration_base_cycles: float
    duration_per_item_cycles: float
    parameter_path: str
    @model_validator(mode='after')
    def finite(self):
        if not all(math.isfinite(v) for v in (self.start_base_cycles,self.start_per_item_cycles,self.duration_base_cycles,self.duration_per_item_cycles)):
            raise ValueError('invalid bulk Vector timing')
        if not self.parameter_path:raise ValueError('parameter path required')
        return self
    def timing(self,items):
        if type(items) is not int or items<=0:raise ValueError('bulk items must be positive')
        start=self.start_base_cycles+self.start_per_item_cycles*items;duration=self.duration_base_cycles+self.duration_per_item_cycles*items
        if start<0 or duration<0:raise ValueError('bulk formula outside nonnegative domain')
        return start,duration

class BulkVectorFormula(FrozenRecord):
    kind: Literal['bulk']='bulk'
    component: Literal['M06']='M06'
    profile: str
    operation: str
    model_revision: str
    slices: tuple[BulkVectorSlice,...]
    calibration_sha256: str
    source_refs: tuple[str,...]
    applicability: str
    @model_validator(mode='after')
    def complete(self):
        if {s.engine for s in self.slices}!={'VECTOR_READ','VECTOR_CAL','VECTOR_WRITE'} or len(self.slices)!=3 or not self.source_refs:
            raise ValueError('incomplete bulk Vector formula')
        return self
    def actions(self,work_id,pe_id,items,participants):
        if type(items) is not int or items<0:raise ValueError('invalid work item count')
        if items==0:return ()
        out=[]
        for stage in self.slices:
            start,duration=stage.timing(items)
            out.append(ComponentAction(action_id=f'{work_id}.pe{pe_id}.bulk.{stage.engine}',stage=stage.engine,component='M06',
                resource_id=f'pe{pe_id}-{stage.engine}',work=items,work_unit='valid-selection',duration_cycles=duration,
                duration_source='M06 bulk affine '+stage.parameter_path,calibration_sha256=self.calibration_sha256,
                earliest_start=start,participants=tuple(participants),status='calibrated'))
        return tuple(out)

class PiecewiseBulkVectorSlice(FrozenRecord):
    engine: Literal['VECTOR_READ','VECTOR_CAL','VECTOR_WRITE']
    singleton_start_cycles: float = Field(ge=0)
    singleton_duration_cycles: float = Field(ge=0)
    multi_start_base_cycles: float
    multi_start_per_item_cycles: float
    multi_duration_base_cycles: float
    multi_duration_per_item_cycles: float
    parameter_path: str
    @model_validator(mode='after')
    def finite(self):
        values=(self.singleton_start_cycles,self.singleton_duration_cycles,self.multi_start_base_cycles,self.multi_start_per_item_cycles,
                self.multi_duration_base_cycles,self.multi_duration_per_item_cycles)
        if not all(math.isfinite(v) for v in values) or not self.parameter_path:raise ValueError('invalid piecewise Vector timing')
        return self
    def timing(self,items):
        if type(items) is not int or items<=0:raise ValueError('bulk items must be positive')
        if items==1:return self.singleton_start_cycles,self.singleton_duration_cycles
        start=self.multi_start_base_cycles+self.multi_start_per_item_cycles*items
        duration=self.multi_duration_base_cycles+self.multi_duration_per_item_cycles*items
        if start<0 or duration<0:raise ValueError('piecewise formula outside nonnegative domain')
        return start,duration

class PiecewiseBulkVectorFormula(FrozenRecord):
    kind: Literal['piecewise_bulk']='piecewise_bulk'
    component: Literal['M06']='M06'
    profile: str
    operation: Literal['silu_score_quant']='silu_score_quant'
    model_revision: str
    slices: tuple[PiecewiseBulkVectorSlice,...]
    calibration_sha256: str
    source_refs: tuple[str,...]
    applicability: str
    @model_validator(mode='after')
    def complete(self):
        if {s.engine for s in self.slices}!={'VECTOR_READ','VECTOR_CAL','VECTOR_WRITE'} or len(self.slices)!=3 or len(self.source_refs)<3:
            raise ValueError('incomplete piecewise Vector formula')
        return self
    def actions(self,work_id,pe_id,items,participants):
        if type(items) is not int or items<0:raise ValueError('invalid work item count')
        if items==0:return ()
        out=[]
        for stage in self.slices:
            start,duration=stage.timing(items)
            out.append(ComponentAction(action_id=f'{work_id}.pe{pe_id}.bulk.{stage.engine}',stage=stage.engine,component='M06',
                resource_id=f'pe{pe_id}-{stage.engine}',work=items,work_unit='valid-selection',duration_cycles=duration,
                duration_source='M06 singleton/multi-item branch '+stage.parameter_path,calibration_sha256=self.calibration_sha256,
                earliest_start=start,participants=tuple(participants),status='calibrated'))
        return tuple(out)

class TpcRoundAnchor(FrozenRecord):
    round_index: int = Field(gt=0)
    first_start_cycles: float = Field(ge=0)
    first_duration_cycles: float = Field(ge=0)
    third_start_cycles: float = Field(ge=0)
    third_duration_cycles: float = Field(ge=0)
    full_start_cycles: float = Field(ge=0)
    full_duration_cycles: float = Field(ge=0)

class TpcBatchVectorSlice(FrozenRecord):
    engine: Literal['VECTOR_READ','VECTOR_CAL','VECTOR_WRITE']
    tpc_count: Literal[4]=4
    rounds: tuple[TpcRoundAnchor,...]
    parameter_path: str
    @model_validator(mode='after')
    def complete(self):
        if tuple(r.round_index for r in self.rounds)!=tuple(range(1,len(self.rounds)+1)) or not self.parameter_path:
            raise ValueError('contiguous TPC round anchors required')
        return self
    def timing(self,items):
        if type(items) is not int or items<=0:raise ValueError('bulk items must be positive')
        round_index=(items+self.tpc_count-1)//self.tpc_count
        if round_index>len(self.rounds):raise ValueError('TPC batch formula outside calibrated rounds')
        anchor=self.rounds[round_index-1];remainder=(items-1)%self.tpc_count+1
        if remainder==1:return anchor.first_start_cycles,anchor.first_duration_cycles
        if remainder==2:return ((anchor.first_start_cycles+anchor.third_start_cycles)/2,
                                (anchor.first_duration_cycles+anchor.third_duration_cycles)/2)
        if remainder==3:return anchor.third_start_cycles,anchor.third_duration_cycles
        return anchor.full_start_cycles,anchor.full_duration_cycles

class TpcBatchVectorFormula(FrozenRecord):
    kind: Literal['tpc_batch']='tpc_batch'
    component: Literal['M06']='M06'
    profile: str
    operation: Literal['table','silu_score_quant']
    model_revision: str
    slices: tuple[TpcBatchVectorSlice,...]
    calibration_sha256: str
    source_refs: tuple[str,...]
    applicability: str
    @model_validator(mode='after')
    def complete(self):
        if {s.engine for s in self.slices}!={'VECTOR_READ','VECTOR_CAL','VECTOR_WRITE'} or len(self.slices)!=3 or not self.source_refs:
            raise ValueError('incomplete TPC batch Vector formula')
        if len({len(s.rounds) for s in self.slices})!=1:raise ValueError('engine round domains differ')
        return self
    def actions(self,work_id,pe_id,items,participants):
        if type(items) is not int or items<0:raise ValueError('invalid work item count')
        if items==0:return ()
        out=[]
        for stage in self.slices:
            start,duration=stage.timing(items)
            out.append(ComponentAction(action_id=f'{work_id}.pe{pe_id}.bulk.{stage.engine}',stage=stage.engine,component='M06',
                resource_id=f'pe{pe_id}-{stage.engine}',work=items,work_unit='valid-selection',duration_cycles=duration,
                duration_source='M06 four-TPC round/remainder '+stage.parameter_path,calibration_sha256=self.calibration_sha256,
                earliest_start=start,participants=tuple(participants),status='calibrated'))
        return tuple(out)

class LongRepeatedVectorSlice(FrozenRecord):
    engine: Literal['VECTOR_READ','VECTOR_CAL','VECTOR_WRITE']
    subevent: int = Field(ge=0)
    first_start_cycles: float = Field(ge=0)
    first_duration_cycles: float = Field(ge=0)
    prefix_start_cycles: tuple[float,...]
    prefix_duration_cycles: tuple[float,...]
    tail_interval_cycles: float = Field(gt=0)
    tail_duration_cycles: float = Field(ge=0)
    parameter_path: str
    @model_validator(mode='after')
    def valid(self):
        if not self.prefix_start_cycles or len(self.prefix_start_cycles)!=len(self.prefix_duration_cycles) or not self.parameter_path:
            raise ValueError('long repeated prefix required')
        return self
    def timing(self,item):
        if type(item) is not int or item<0:raise ValueError('invalid item')
        if item==0:return self.first_start_cycles,self.first_duration_cycles
        if item<=len(self.prefix_start_cycles):return self.prefix_start_cycles[item-1],self.prefix_duration_cycles[item-1]
        return (self.prefix_start_cycles[-1]+(item-len(self.prefix_start_cycles))*self.tail_interval_cycles,self.tail_duration_cycles)

class LongRepeatedVectorFormula(FrozenRecord):
    kind: Literal['long_repeated']='long_repeated'
    component: Literal['M06']='M06'
    profile: str
    operation: str
    model_revision: str
    events_per_item: int = Field(gt=0)
    slices: tuple[LongRepeatedVectorSlice,...]
    calibration_sha256: str
    source_refs: tuple[str,...]
    applicability: str
    @model_validator(mode='after')
    def complete(self):
        expected={(engine,sub) for engine in ('VECTOR_READ','VECTOR_CAL','VECTOR_WRITE') for sub in range(self.events_per_item)}
        if {(s.engine,s.subevent) for s in self.slices}!=expected or not self.source_refs:raise ValueError('incomplete long repeated formula')
        return self
    def actions(self,work_id,pe_id,items,participants):
        if type(items) is not int or items<0:raise ValueError('invalid work item count')
        out=[]
        for item in range(items):
            for stage in self.slices:
                start,duration=stage.timing(item)
                out.append(ComponentAction(action_id=f'{work_id}.pe{pe_id}.item{item}.sub{stage.subevent}.{stage.engine}',stage=stage.engine,component='M06',
                    resource_id=f'pe{pe_id}-{stage.engine}',work=1,work_unit='source-shaped-vector-launch',duration_cycles=duration,
                    duration_source='M06 long-prefix averaged-tail '+stage.parameter_path,calibration_sha256=self.calibration_sha256,
                    earliest_start=start,participants=tuple(participants),status='calibrated'))
        return tuple(out)

class RecurringTpcVectorSlice(FrozenRecord):
    engine: Literal['VECTOR_READ','VECTOR_CAL','VECTOR_WRITE']
    prefix_rounds: tuple[TpcRoundAnchor,...]
    recurrence_round_start: Literal[4]=4
    round4_start_by_remainder: tuple[float,float,float,float]
    round4_duration_by_remainder: tuple[float,float,float,float]
    start_delta_per_round: tuple[float,float,float,float]
    duration_delta_per_round: tuple[float,float,float,float]
    parameter_path: str
    def timing(self,items):
        if type(items) is not int or items<=0:raise ValueError('bulk items must be positive')
        round_index=(items+3)//4;remainder=(items-1)%4+1
        if round_index<4:
            if remainder==2:raise ValueError('low-round remainder2 excluded after failed validation')
            anchor=self.prefix_rounds[round_index-1]
            if remainder==1:return anchor.first_start_cycles,anchor.first_duration_cycles
            if remainder==3:return anchor.third_start_cycles,anchor.third_duration_cycles
            return anchor.full_start_cycles,anchor.full_duration_cycles
        offset=round_index-self.recurrence_round_start;index=remainder-1
        return (self.round4_start_by_remainder[index]+offset*self.start_delta_per_round[index],
                self.round4_duration_by_remainder[index]+offset*self.duration_delta_per_round[index])

class RecurringTpcVectorFormula(FrozenRecord):
    kind: Literal['recurring_tpc']='recurring_tpc'
    component: Literal['M06']='M06'
    profile: str
    operation: Literal['table','silu_score_quant']
    model_revision: str
    slices: tuple[RecurringTpcVectorSlice,...]
    calibration_sha256: str
    source_refs: tuple[str,...]
    applicability: str
    @model_validator(mode='after')
    def complete(self):
        if {s.engine for s in self.slices}!={'VECTOR_READ','VECTOR_CAL','VECTOR_WRITE'} or len(self.slices)!=3 or not self.source_refs:
            raise ValueError('incomplete recurring TPC formula')
        return self
    def actions(self,work_id,pe_id,items,participants):
        if type(items) is not int or items<0:raise ValueError('invalid work item count')
        if items==0:return ()
        out=[]
        for stage in self.slices:
            start,duration=stage.timing(items)
            if start<0 or duration<0:raise ValueError('recurring TPC formula outside nonnegative domain')
            out.append(ComponentAction(action_id=f'{work_id}.pe{pe_id}.bulk.{stage.engine}',stage=stage.engine,component='M06',resource_id=f'pe{pe_id}-{stage.engine}',
                work=items,work_unit='valid-selection',duration_cycles=duration,duration_source='M06 per-remainder TPC round recurrence '+stage.parameter_path,
                calibration_sha256=self.calibration_sha256,earliest_start=start,participants=tuple(participants),status='calibrated'))
        return tuple(out)

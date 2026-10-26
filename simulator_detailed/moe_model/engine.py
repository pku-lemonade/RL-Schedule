"""Named per-engine pipeline formulas bound to source-visible work/shape domains."""
from pydantic import Field,model_validator
from .workload import FrozenRecord
from .component_service import ComponentAction
import math

class EngineSlice(FrozenRecord):
    engine: str
    start_offset_cycles: float = Field(ge=0)
    duration_cycles: float = Field(ge=0)
    repeat_start_offset_cycles: float = Field(ge=0)
    repeat_interval_cycles: float = Field(gt=0)
    repeat_duration_cycles: float = Field(ge=0)
    parameter_path: str
    @model_validator(mode='after')
    def finite(self):
        values=(self.start_offset_cycles,self.duration_cycles,self.repeat_start_offset_cycles,
                self.repeat_interval_cycles,self.repeat_duration_cycles)
        if not all(math.isfinite(v) for v in values):raise ValueError('nonfinite engine timing')
        if not self.parameter_path:raise ValueError('engine parameter path required')
        return self
    def timing(self,item_index):
        if type(item_index) is not int or item_index<0:raise ValueError('invalid engine item index')
        if item_index==0:return self.start_offset_cycles,self.duration_cycles
        return (self.repeat_start_offset_cycles+(item_index-1)*self.repeat_interval_cycles,
                self.repeat_duration_cycles)

class EngineFormula(FrozenRecord):
    component: str
    operation: str
    shape_key: str
    model_revision: str
    slices: tuple[EngineSlice,...]
    calibration_sha256: str
    source_refs: tuple[str,...]
    applicability: str
    @model_validator(mode='after')
    def stages(self):
        if not self.slices or not self.source_refs or not self.model_revision:raise ValueError('pipeline formula and evidence required')
        if len({s.engine for s in self.slices})!=len(self.slices):raise ValueError('engine stages must be unique')
        return self
    def actions(self,work_id,pe_id,items,participants):
        if type(items) is not int or items<0:raise ValueError('invalid work item count')
        out=[]
        for i in range(items):
            for s in self.slices:
                start,duration=s.timing(i)
                out.append(ComponentAction(action_id=f'{work_id}.pe{pe_id}.item{i}.{s.engine}',stage=s.engine,
                    component=self.component,resource_id=f'pe{pe_id}-{s.engine}',work=1.0,work_unit='shape-matched-task',
                    duration_cycles=duration,duration_source='component engine warmup/steady slice '+s.parameter_path,
                    calibration_sha256=self.calibration_sha256,earliest_start=start,
                    participants=tuple(participants),status='calibrated'))
        return tuple(out)

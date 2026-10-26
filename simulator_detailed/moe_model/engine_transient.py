"""M05 Matrix engine timing with a calibrated transient prefix and steady tail."""
from pydantic import Field,model_validator
from .workload import FrozenRecord
from .component_service import ComponentAction
import math

class TransientEngineSlice(FrozenRecord):
    engine: str
    first_start_cycles: float = Field(ge=0)
    first_duration_cycles: float = Field(ge=0)
    transient_start_cycles: tuple[float,...]
    transient_duration_cycles: tuple[float,...]
    steady_interval_cycles: float = Field(gt=0)
    steady_duration_cycles: float = Field(ge=0)
    parameter_path: str
    @model_validator(mode='after')
    def valid_timing(self):
        values=(self.first_start_cycles,self.first_duration_cycles,self.steady_interval_cycles,
                self.steady_duration_cycles,*self.transient_start_cycles,*self.transient_duration_cycles)
        if not self.transient_start_cycles or len(self.transient_start_cycles)!=len(self.transient_duration_cycles):
            raise ValueError('transient prefix required')
        if not all(math.isfinite(v) and v>=0 for v in values):raise ValueError('invalid transient engine timing')
        if not self.parameter_path:raise ValueError('engine parameter path required')
        return self
    def timing(self,item_index):
        if type(item_index) is not int or item_index<0:raise ValueError('invalid engine item index')
        if item_index==0:return self.first_start_cycles,self.first_duration_cycles
        prefix=len(self.transient_start_cycles)
        if item_index<=prefix:
            return self.transient_start_cycles[item_index-1],self.transient_duration_cycles[item_index-1]
        return (self.transient_start_cycles[-1]+(item_index-prefix)*self.steady_interval_cycles,
                self.steady_duration_cycles)

class TransientEngineFormula(FrozenRecord):
    component: str
    operation: str
    shape_key: str
    model_revision: str
    slices: tuple[TransientEngineSlice,...]
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
        for index in range(items):
            for stage in self.slices:
                start,duration=stage.timing(index)
                out.append(ComponentAction(action_id=f'{work_id}.pe{pe_id}.item{index}.{stage.engine}',stage=stage.engine,
                    component=self.component,resource_id=f'pe{pe_id}-{stage.engine}',work=1.0,work_unit='shape-matched-task',
                    duration_cycles=duration,duration_source='component engine transient/steady slice '+stage.parameter_path,
                    calibration_sha256=self.calibration_sha256,earliest_start=start,
                    participants=tuple(participants),status='calibrated'))
        return tuple(out)

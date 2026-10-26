"""R3 packet/transaction queue model for transformer-fused residual preseed.

One controller owns eight FIFO-ordered GM receive transactions.  Every
transaction is decomposed into an admission wait, source UPLOAD packet service,
receive drain, and the observed gap before the next controller task.  Four
controllers execute independently and the action completes at their maximum.
"""
from typing import Literal
import json,math
from pydantic import Field,TypeAdapter,model_validator

from .binding import SuppliedBinding
from .component_service import ComponentAction,execute_actions
from .timebase import TimebaseBundle,TimebaseDomain,convert_time_value,freeze_timebase_bundle
from .workload import FrozenRecord,stable_sha256

PACKET_BYTES=128
R3_BOUNDARY="four GM_WDMA active begins -> max eighth CHANNEL1 engine completion with matched PE UPLOAD1 stages"
R3_OWNER="R4.residual_preseed.r3_packet_queue_four_controller_max"
R3_BUFFER="tl_sram.prelude.residual_out-fp32-to-tl_gsram.moe.output"

class PreseedPacketWork(FrozenRecord):
    profile:Literal['transformer-fused']='transformer-fused'
    consumer_stage:Literal['R4']='R4'
    operation:Literal['residual_preseed']='residual_preseed'
    payload_bytes_per_pe:int=Field(gt=0,le=16384)
    pe_count:Literal[32]=32
    controller_count:Literal[4]=4
    transactions_per_controller:Literal[8]=8
    channel:Literal[1]=1
    packet_bytes:Literal[128]=128
    controller_by_pe:tuple[int,...]=tuple(pe%4 for pe in range(32))
    buffer_binding:Literal[R3_BUFFER]=R3_BUFFER
    boundary:Literal[R3_BOUNDARY]=R3_BOUNDARY
    timing_ownership:Literal[R3_OWNER]=R3_OWNER
    source_commit:Literal['094bcd25e985a4c836fd5521cccd61b14d911aac']='094bcd25e985a4c836fd5521cccd61b14d911aac'
    source_callsite:Literal['router_gemm.hpp:33-38,272-281,407-410,560-562']='router_gemm.hpp:33-38,272-281,407-410,560-562'
    @model_validator(mode='after')
    def exact(self):
        if self.payload_bytes_per_pe%4 or self.controller_by_pe!=tuple(pe%4 for pe in range(32)):raise ValueError('preseed R3 payload/controller mapping mismatch')
        return self
    @property
    def full_packets(self):return self.payload_bytes_per_pe//self.packet_bytes
    @property
    def tail_bytes(self):return self.payload_bytes_per_pe%self.packet_bytes
    @property
    def wire_packets(self):return self.full_packets+(self.tail_bytes>0)
    @property
    def total_bytes(self):return self.payload_bytes_per_pe*self.pe_count
    @property
    def controller_bytes(self):return self.payload_bytes_per_pe*self.transactions_per_controller

class TransactionStageEvent(FrozenRecord):
    controller:int=Field(ge=0,lt=4)
    ordinal:int=Field(ge=0,lt=8)
    source_pe:int=Field(ge=0,lt=32)
    channel_start_ns:float=Field(ge=0)
    upload_start_ns:float=Field(ge=0)
    upload_end_ns:float=Field(ge=0)
    channel_end_ns:float=Field(ge=0)
    @model_validator(mode='after')
    def order(self):
        if self.source_pe!=self.ordinal*4+self.controller or not self.channel_start_ns<=self.upload_start_ns<=self.upload_end_ns<=self.channel_end_ns:raise ValueError('preseed R3 transaction identity/order mismatch')
        return self
    @property
    def admission_wait_ns(self):return self.upload_start_ns-self.channel_start_ns
    @property
    def upload_service_ns(self):return self.upload_end_ns-self.upload_start_ns
    @property
    def drain_ns(self):return self.channel_end_ns-self.upload_end_ns

def validate_event_inventory(events):
    events=tuple(TransactionStageEvent.model_validate_json(event.model_dump_json()) for event in events)
    keys=[(event.controller,event.ordinal) for event in events]
    if len(events)!=32 or len(set(keys))!=32 or set(keys)!={(controller,ordinal) for controller in range(4) for ordinal in range(8)}:raise ValueError('preseed R3 duplicate/missing controller transaction event')
    for controller in range(4):
        rows=sorted((event for event in events if event.controller==controller),key=lambda event:event.ordinal)
        if any(rows[index+1].channel_start_ns<rows[index].channel_end_ns for index in range(7)):raise ValueError('preseed R3 controller queue order overlap')
    return events

class PreseedPacketQueueFormula(FrozenRecord):
    revision:Literal['M04-fused-residual-preseed-packet-r3']='M04-fused-residual-preseed-packet-r3'
    source_unit:Literal['reported_ns']='reported_ns'
    packet_bytes:Literal[128]=128
    startup_intercept_ns:tuple[float,...]
    startup_full_packet_ns:tuple[float,...]
    startup_tail_packet_ns:tuple[float,...]
    admission_wait_ns:tuple[tuple[float,...],...]
    queue_gap_ns:tuple[tuple[float,...],...]
    upload_intercept_ns:tuple[tuple[float,...],...]
    upload_full_packet_ns:tuple[tuple[float,...],...]
    upload_tail_packet_ns:tuple[tuple[float,...],...]
    drain_intercept_ns:tuple[tuple[float,...],...]
    drain_full_packet_ns:tuple[tuple[float,...],...]
    drain_tail_packet_ns:tuple[tuple[float,...],...]
    calibration_payloads:tuple[int,...]
    calibration_scene_ids:tuple[str,...]
    applicability:str
    formula_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def valid(self):
        vectors=(self.startup_intercept_ns,self.startup_full_packet_ns,self.startup_tail_packet_ns)
        matrices=(self.admission_wait_ns,self.queue_gap_ns,self.upload_intercept_ns,self.upload_full_packet_ns,self.upload_tail_packet_ns,self.drain_intercept_ns,self.drain_full_packet_ns,self.drain_tail_packet_ns)
        if any(len(vector)!=4 for vector in vectors) or any(len(matrix)!=4 for matrix in matrices):raise ValueError('preseed R3 controller coefficient shape')
        eight=(self.admission_wait_ns,self.upload_intercept_ns,self.upload_full_packet_ns,self.upload_tail_packet_ns,self.drain_intercept_ns,self.drain_full_packet_ns,self.drain_tail_packet_ns)
        if any(len(row)!=7 for row in self.queue_gap_ns) or any(any(len(row)!=8 for row in matrix) for matrix in eight):raise ValueError('preseed R3 ordinal coefficient shape')
        values=[value for vector in vectors for value in vector]+[value for matrix in matrices for row in matrix for value in row]
        nonnegative=(*self.startup_intercept_ns,*self.startup_full_packet_ns,*self.startup_tail_packet_ns,*[value for matrix in (self.admission_wait_ns,self.queue_gap_ns,self.upload_intercept_ns,self.upload_full_packet_ns,self.upload_tail_packet_ns) for row in matrix for value in row])
        if any(not math.isfinite(value) for value in values) or any(value<0 for value in nonnegative):raise ValueError('preseed R3 coefficient invalid')
        if self.calibration_payloads!=(8064,8068,8320) or len(self.calibration_scene_ids)!=3 or not self.applicability:raise ValueError('preseed R3 frozen packet-boundary calibration identity')
        if stable_sha256(self.model_dump(mode='json',exclude={'formula_sha256'}))!=self.formula_sha256:raise ValueError('preseed R3 formula hash drift')
        return self
    def parameter_paths(self):
        return tuple(f'gm.controllers.gm_wdma{controller}.residual_preseed_r3.{name}' for controller in range(4) for name in ('startup_ns','full_packet_service_ns','tail_packet_service_ns','queue_gap_ns'))
    def _override(self,controller,name,baseline,changes):
        path=f'gm.controllers.gm_wdma{controller}.residual_preseed_r3.{name}'
        value=changes.get(path,baseline)
        if isinstance(value,bool) or type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('preseed R3 local parameter invalid')
        return value
    def controller_timeline(self,work,controller,changes=None):
        work=PreseedPacketWork.model_validate_json(work.model_dump_json());changes=changes or {}
        if controller not in range(4) or not set(changes)<=set(self.parameter_paths()):raise ValueError('preseed R3 controller/path outside domain')
        if any(not path.startswith(f'gm.controllers.gm_wdma{controller}.') for path in changes):
            # Other-controller mutations are ignored by this controller but must be declared.
            changes={path:value for path,value in changes.items() if path.startswith(f'gm.controllers.gm_wdma{controller}.')}
        full=work.full_packets;tail=work.tail_bytes>0
        baseline_start=self.startup_intercept_ns[controller]+full*self.startup_full_packet_ns[controller]+tail*self.startup_tail_packet_ns[controller]
        startup=self._override(controller,'startup_ns',baseline_start,changes)
        full_path=f'gm.controllers.gm_wdma{controller}.residual_preseed_r3.full_packet_service_ns';tail_path=f'gm.controllers.gm_wdma{controller}.residual_preseed_r3.tail_packet_service_ns';gap_path=f'gm.controllers.gm_wdma{controller}.residual_preseed_r3.queue_gap_ns'
        full_override=None if full_path not in changes else self._override(controller,'full_packet_service_ns',0.0,changes)
        tail_override=None if tail_path not in changes else self._override(controller,'tail_packet_service_ns',0.0,changes)
        gap_override=None if gap_path not in changes else self._override(controller,'queue_gap_ns',0.0,changes)
        rows=[];cursor=startup
        for ordinal in range(8):
            admission=self.admission_wait_ns[controller][ordinal]
            baseline_full=self.upload_full_packet_ns[controller][ordinal];baseline_tail=self.upload_tail_packet_ns[controller][ordinal];full_service=baseline_full if full_override is None else full_override;tail_service=baseline_tail if tail_override is None else tail_override
            upload=self.upload_intercept_ns[controller][ordinal]+full*full_service+tail*tail_service
            drain_candidate=self.drain_intercept_ns[controller][ordinal]+full*(full_service+self.drain_full_packet_ns[controller][ordinal]-baseline_full)+tail*(tail_service+self.drain_tail_packet_ns[controller][ordinal]-baseline_tail)
            drain=max(admission,drain_candidate)
            duration=admission+upload+drain
            rows.append(dict(controller=controller,ordinal=ordinal,source_pe=ordinal*4+controller,full_packets=full,tail_bytes=work.tail_bytes,admission_wait_ns=admission,upload_service_ns=upload,drain_service_ns=drain,transaction_service_ns=duration,queue_start_ns=cursor,queue_end_ns=cursor+duration))
            cursor+=duration
            if ordinal<7:cursor+=self.queue_gap_ns[controller][ordinal] if gap_override is None else gap_override
        return dict(controller=controller,startup_ns=startup,queue_gap_ns=list(self.queue_gap_ns[controller]) if gap_override is None else gap_override,transactions=rows,terminal_ns=cursor)
    def duration(self,work,controller,changes=None):return self.controller_timeline(work,controller,changes)['terminal_ns']

class PreseedPacketDomain(FrozenRecord):
    work:PreseedPacketWork
    formula:PreseedPacketQueueFormula
    timebase:TimebaseDomain
    observer_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    counter_callsite:int=Field(ge=0)
    source_refs:tuple[str,...]
    domain_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def valid(self):
        t=self.timebase
        if self.work.payload_bytes_per_pe!=8192 or self.work.tail_bytes or t.component_ids!=('M04',) or t.operation!=self.work.operation or t.profile!=self.work.profile or t.boundary_id!=self.work.boundary or t.source_unit!=self.formula.source_unit or t.accepted_work_values!=(8192,) or t.status!='validated_simulation' or not self.source_refs:raise ValueError('preseed R3 exact production domain mismatch')
        if stable_sha256(self.model_dump(mode='json',exclude={'domain_sha256'}))!=self.domain_sha256:raise ValueError('preseed R3 domain hash drift')
        return self

class PreseedPacketBinding(FrozenRecord):
    binding:SuppliedBinding
    resources:dict[str,str]
    domain_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    buffer_binding:Literal[R3_BUFFER]=R3_BUFFER
    timing_owner:Literal[R3_OWNER]=R3_OWNER
    source_refs:tuple[str,...]
    binding_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    @model_validator(mode='after')
    def valid(self):
        expected={f'GM_WDMA{i}' for i in range(4)};units={row['unit_id'] for row in self.binding.graph['execution_units']}
        if set(self.resources)!=expected or len(set(self.resources.values()))!=4 or not set(self.resources.values())<=units or not self.source_refs:raise ValueError('preseed R3 binding incomplete')
        if stable_sha256(self.model_dump(mode='json',exclude={'binding_sha256'}))!=self.binding_sha256:raise ValueError('preseed R3 binding hash drift')
        return self

def _freeze(cls,field,raw):
    raw=TypeAdapter(dict).dump_python(raw,mode='json');raw[field]=stable_sha256(raw);return cls.model_validate_json(json.dumps(raw))
def freeze_preseed_packet_formula(**kwargs):return _freeze(PreseedPacketQueueFormula,'formula_sha256',dict(revision='M04-fused-residual-preseed-packet-r3',source_unit='reported_ns',packet_bytes=128,**kwargs))
def freeze_preseed_packet_domain(**kwargs):return _freeze(PreseedPacketDomain,'domain_sha256',kwargs)
def freeze_preseed_packet_binding(**kwargs):return _freeze(PreseedPacketBinding,'binding_sha256',dict(buffer_binding=R3_BUFFER,timing_owner=R3_OWNER,**kwargs))

def resolve_preseed_packet(domains,*,work,model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite,physical=False):
    work=PreseedPacketWork.model_validate_json(work.model_dump_json());matches=[]
    for raw in domains:
        domain=PreseedPacketDomain.model_validate_json(raw.model_dump_json())
        if not physical and domain.work==work and (domain.timebase.model_library_sha256,domain.timebase.systemc_library_sha256,domain.observer_sha256,domain.counter_callsite)==(model_library_sha256,systemc_library_sha256,observer_sha256,counter_callsite):matches.append(domain)
    if len(matches)!=1:raise ValueError(f'preseed R3 requires one exact domain, found {len(matches)}')
    return matches[0]

def preseed_packet_actions(domain,binding,*,before=('R4.quant_upload.complete',),changes=None):
    domain=PreseedPacketDomain.model_validate_json(domain.model_dump_json());binding=PreseedPacketBinding.model_validate_json(binding.model_dump_json())
    if binding.domain_sha256!=domain.domain_sha256:raise ValueError('preseed R3 binding/domain mismatch')
    actions=[]
    for controller in range(4):
        node=f'GM_WDMA{controller}';actions.append(ComponentAction(action_id=f'R4.residual_preseed_r3.{node}',stage='R4.residual_preseed_r3',component='M04',resource_id=binding.resources[node],work=float(domain.work.controller_bytes),work_unit='source-byte-per-controller',duration_cycles=domain.formula.duration(domain.work,controller,changes),duration_source=R3_OWNER,calibration_sha256=domain.formula.formula_sha256,dependencies=tuple(before),bytes=domain.work.controller_bytes,participants=tuple(f'PE{ordinal*4+controller}' for ordinal in range(8))+(node,),status='calibrated'))
    actions.append(ComponentAction(action_id='R4.residual_preseed_r3.complete',stage='R4.residual_preseed_r3',component='M04',resource_id=binding.resources['GM_WDMA0'],work=1,work_unit='four-controller-max-terminal',duration_cycles=0,duration_source='zero-cost after-all; controller packet queues already owned',calibration_sha256=domain.domain_sha256,dependencies=tuple(action.action_id for action in actions),participants=tuple(f'PE{i}' for i in range(32))+tuple(f'GM_WDMA{i}' for i in range(4)),status='calibrated'))
    return tuple(actions)

def execute_preseed_packet(domain,binding,hardware,*,before=('R4.quant_upload.complete',),changes=None,extra_actions=(),production=True,cancel=None):
    domain=PreseedPacketDomain.model_validate_json(domain.model_dump_json());binding=PreseedPacketBinding.model_validate_json(binding.model_dump_json())
    if production and binding.binding.fixture_only:raise ValueError('production preseed R3 requires supplied binding')
    if any(action.stage=='R4.residual_preseed_r3' or action.action_id.startswith('R4.residual_preseed_r3') for action in extra_actions):raise ValueError('preseed R3 duplicate timing owner')
    actions=preseed_packet_actions(domain,binding,before=before,changes=changes);result=execute_actions(actions+tuple(extra_actions),binding.binding,hardware,production=production,cancel=cancel)
    timelines=[domain.formula.controller_timeline(domain.work,controller,changes) for controller in range(4)]
    result.receipt.update(preseed_r3_domain_sha256=domain.domain_sha256,preseed_r3_binding_sha256=binding.binding_sha256,timing_owner=R3_OWNER,logical_source_bytes=domain.work.total_bytes,endpoint_delivered_bytes=domain.work.total_bytes,logical_transactions=32,packet_bytes=128,full_packets_per_transaction=domain.work.full_packets,tail_bytes_per_transaction=domain.work.tail_bytes,controller_timelines=timelines,parameter_paths=list(domain.formula.parameter_paths()),simulation_seconds=convert_time_value(domain.timebase,max(action.duration_cycles for action in actions[:4])),excluded_cost_owners=['R4.quant_upload','R10.WRITE_SUM','DataNoC','Matrix','Vector'],physical_publishable=False,model_cycle_mapping=False)
    if result.result.status=='complete':
        from .adalink_execute import _critical_to_completion
        result.receipt['event_resource_critical_path']=_critical_to_completion(result.receipt['causal_graph'],'R4.residual_preseed_r3.complete')
    return result

def append_preseed_packet_timebase(base,domain):
    base=TimebaseBundle.model_validate_json(base.model_dump_json());domain=PreseedPacketDomain.model_validate_json(domain.model_dump_json())
    if domain.timebase.domain_id in {row.domain_id for row in base.domains}:raise ValueError('duplicate preseed R3 timebase')
    return freeze_timebase_bundle(bundle_id='T81-C09-plus-fused-preseed-packet-r3',fixture_only=False,domains=base.domains+(domain.timebase,),source_refs=base.source_refs+domain.source_refs)

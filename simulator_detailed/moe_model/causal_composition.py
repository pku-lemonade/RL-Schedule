"""Deterministic source-cut composition; never raw-spec production admission."""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
import simpy

from .binding import freeze_binding
from .component_calibration import ComponentCalibrationBundle
from .component_service import ComponentAction, _conditional_contexts, _validate_conditional_schedule
from .count_fill import CountFillDomain, count_actions, count_resources, freeze_count_binding
from .count_token_handoff import CountTokenHandoffDomain, count_token_handoff_actions, freeze_count_token_binding
from .e0_e1_handoff import E0E1HandoffDomain, e0_e1_handoff_actions, freeze_e0_e1_binding, role_for_pe
from .gate_up_domain import GateUpDomain
from .gate_up_matrix import ENGINES, gate_up_actions, issue_ready, resource
from .hardware import freeze_hardware_parameters
from .production_inputs import require_production_action_provenance
from .r9_count_handoff import R9CountHandoffDomain, freeze_r9_count_binding, r9_count_handoff_actions
from .receipt import ResourceObserver
from .workload import FrozenRecord, stable_sha256
from ..configs.schemas.generic_transactions import GenericTransactionBatch
from ..configs.schemas.system_spec import SystemSpec
from ..runtime_context import RuntimeContext
from ..system_compile import compile_system


CONTRACT = 'b5-causal-composition-r1/source-contract.json'
CONTRACT_SHA = '7269910790ebf466527274141907882885273448df13a5f510a6649fc055a483'
BUNDLE = 'c07-bridge-commid-r3/component-calibration-bundle.json'
DOMAIN_FILES = {
    'e0_e1': ('m07-source-fused-E0-E1-local-handoff-V64', 'c07-e0-e1-handoff-r1/tsim/e0-e1-handoff-domain.json', E0E1HandoffDomain),
    'r9_count': ('m07-source-fused-R9-count-local-handoff-rank0-V64', 'c07-r9-count-handoff-r1/tsim/r9-count-handoff-domain.json', R9CountHandoffDomain),
    'count': ('m06-count-fill-rank0-V64', 'c06-count-fill-ranks-r1/tsim/domain-rank0.json', CountFillDomain),
    'count_token': ('m07-source-fused-count-token-local-handoff-rank0-V64', 'c07-count-token-handoff-r1/tsim/count-token-handoff-domain.json', CountTokenHandoffDomain),
}


def file_sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class Intervention(FrozenRecord):
    path: str
    value: float = Field(ge=0, allow_inf_nan=False)


class BusyWindow(FrozenRecord):
    resource: str
    duration_ns: float = Field(gt=0, allow_inf_nan=False)


class CompositionRequest(FrozenRecord):
    schema_id: Literal['t81.b5.request.v1'] = 't81.b5.request.v1'
    chain: Literal['e0_e1', 'router_count']
    profile: Literal['transformer-fused'] = 'transformer-fused'
    rank: Literal[0] = 0
    V: Literal[64] = 64
    binding_kind: Literal['source_bound_cpu'] = 'source_bound_cpu'
    locality: Literal['paired', 'spread'] = 'paired'
    producer_ready_ns: tuple[float, ...] = (0.,) * 32
    consumer_ready_ns: tuple[float, ...] = (0.,) * 32
    issue_interval_ns: float = Field(default=0., ge=0, allow_inf_nan=False)
    busy: tuple[BusyWindow, ...] = ()
    cpu_fifo: bool = False
    fifo_slots: int = Field(default=1, ge=1, le=2)
    packet_payloads: tuple[int, int] = (24, 8)
    intervention: Intervention | None = None
    cancel_action: str | None = None
    cancel_at_ns: float = Field(default=0., ge=0, allow_inf_nan=False)
    max_ns: float = Field(default=10000000., gt=0, allow_inf_nan=False)

    @model_validator(mode='after')
    def exact(self):
        for values in (self.producer_ready_ns, self.consumer_ready_ns):
            if len(values) != 32 or any(not math.isfinite(value) or value < 0 for value in values):
                raise ValueError('exact per-PE readiness required')
        if len({window.resource for window in self.busy}) != len(self.busy):
            raise ValueError('duplicate busy resource owner')
        if any(payload <= 0 for payload in self.packet_payloads):
            raise ValueError('positive CPU packet work required')
        if self.chain == 'e0_e1' and self.cpu_fifo:
            raise ValueError('B1 conditional E0 intervals cannot compose with network/FIFO')
        if not self.cpu_fifo and (self.fifo_slots != 1 or self.packet_payloads != (24, 8)):
            raise ValueError('unused FIFO fields cannot change')
        if self.chain == 'router_count' and (self.locality != 'paired' or self.issue_interval_ns):
            raise ValueError('unused E0 fields cannot change')
        if self.cancel_action is None and self.cancel_at_ns:
            raise ValueError('cancellation requires an exact action')
        return self


def load_catalog(root, source_root):
    root, source_root = Path(root), Path(source_root)
    if file_sha(root / CONTRACT) != CONTRACT_SHA:
        raise ValueError('source contract identity drift')
    contract = json.loads((root / CONTRACT).read_text())
    for name, digest in contract['artifacts'].items():
        if file_sha(root / name) != digest:
            raise ValueError('accepted artifact identity drift: ' + name)
    for name, digest in contract['source_files'].items():
        if file_sha(source_root / name) != digest:
            raise ValueError('source identity drift: ' + name)
    bundle = ComponentCalibrationBundle.model_validate_json((root / BUNDLE).read_text())
    entries = {entry.entry_id: entry for entry in bundle.entries}
    if len(entries) != len(bundle.entries):
        raise ValueError('unique accepted entry required')
    domains = {}
    for key, (identity, name, model) in DOMAIN_FILES.items():
        domain = model.model_validate_json((root / name).read_text())
        if entries[identity].formula != domain.formula.model_dump(mode='json'):
            raise ValueError('accepted formula mismatch')
        dimensions = entries[identity].domain.input_dimensions
        if domain.work.model_dump(mode='json') not in [value for key, value in dimensions.items() if key.endswith('_work')]:
            raise ValueError('accepted work mismatch')
        domains[key] = (entries[identity], domain)
    for locality in ('paired', 'spread'):
        entry = entries[f'm05-source-gate-up-rank0-{locality}-V64']
        domains[locality] = (entry, GateUpDomain.model_validate_json(json.dumps(entry.formula)))
    for entry, domain in domains.values():
        matches = [timebase for timebase in bundle.timebases.domains if timebase.domain_id == domain.timebase.domain_id]
        if matches != [domain.timebase] or domain.timebase.seconds_per_source_unit != 1e-9:
            raise ValueError('exact accepted reported-ns timebase required')
    return contract, bundle, domains


def _graph(request):
    nodes = [dict(node_id=f'rank0.PE{pe}', x=pe % 4, y=pe // 4, role='compute') for pe in range(32)]
    units = {resource(0, pe, engine): f'rank0.PE{pe}' for pe in range(32) for engine in ENGINES} if request.chain == 'e0_e1' else {
        name: '.'.join(name.split('.')[:2]) for name in count_resources(0)}
    sync = {pe: f'rank0.PE{pe}.SYNC' for pe in range(32)}
    units.update({name: f'rank0.PE{pe}' for pe, name in sync.items()})
    units['cpu.independent'] = 'rank0.PE0'
    units['cpu.after_packet'] = 'rank0.PE0'
    graph = dict(kind='generic_system_graph', schema_version=1, system_id='b5-source-bound-cpu', nodes=nodes,
                 networks=[dict(network_id='local')], ports=[], links=[], execution_units=[], dma_endpoints=[], memory_resources=[], static_routes=[])
    for identity, node in sorted(units.items()):
        graph['ports'].append(dict(port_id=identity, node_id=node, network_id='local', kind='local'))
        graph['execution_units'].append(dict(unit_id=identity, node_id=node, network_id='local', port_id=identity))
    timing = [dict(network_id='local', link=dict(bytes_per_cycle=1, hop_cycles=0, credit_return_cycles=0, buffer_slots=1))]
    if request.cpu_fifo:
        graph['networks'].append(dict(network_id='cpu.noc'))
        for index in range(3):
            node = f'cpu.noc.{index}'
            graph['nodes'].append(dict(node_id=node, x=index, y=9, role='compute'))
            graph['ports'].extend(dict(port_id=port, node_id=node, network_id='cpu.noc', kind='local' if port == 'local' else 'network') for port in ('in', 'out', 'local'))
        for identity, node in (('cpu.sender', 'cpu.noc.0'), ('cpu.receiver', 'cpu.noc.2')):
            graph['execution_units'].append(dict(unit_id=identity, node_id=node, network_id='cpu.noc', port_id='local'))
        graph['links'] = [dict(link_id=f'cpu.link.{hop}', network_id='cpu.noc', src_node=f'cpu.noc.{hop}', src_port='out', dst_node=f'cpu.noc.{hop + 1}', dst_port='in') for hop in range(2)]
        graph['static_routes'] = [dict(network_id='cpu.noc', source='cpu.sender', destination='cpu.receiver', link_ids=['cpu.link.0', 'cpu.link.1'])]
        def fifo(hop):
            return dict(mode='latency_fifo', stage='ingress' if hop == 0 else 'egress', packet_bytes=8,
                        fifo_slots=request.fifo_slots, channel_slots=request.fifo_slots, issue_cycles=1,
                        input_latency_cycles=0., channel_latency_cycles=0. if hop == 0 else 6.,
                        input_resource_id=f'cpu.in.{hop}', output_resource_id=f'cpu.out.{hop}', channel_resource_id=f'cpu.channel.{hop}')
        timing.append(dict(network_id='cpu.noc', link=fifo(0), overrides=[dict(link_id='cpu.link.1', timing=fifo(1))]))
    return graph, timing, sync


@dataclass(frozen=True)
class LoweredComposition:
    request: CompositionRequest
    spec: SystemSpec
    plan: object
    actions: tuple
    provenance: tuple
    identities: dict


def lower_composition(request, root, source_root, *, production=False):
    if production:
        require_production_action_provenance(None)
    request = CompositionRequest.model_validate_json(request.model_dump_json())
    contract, bundle, domains = load_catalog(root, source_root)
    graph, timing, sync = _graph(request)
    binding = freeze_binding(binding_id='b5-source-bound-cpu', fixture_only=True, graph=graph, timing=tuple(timing),
        endpoints=tuple(dict(role_id=unit['unit_id'], endpoint_id=unit['unit_id'], kind='pe') for unit in graph['execution_units']),
        parameters=(), source_refs=(CONTRACT,), calibration_sha256=bundle.bundle_sha256)
    hardware = freeze_hardware_parameters('b5-source-bound-cpu', 'reported_ns', (), (CONTRACT,))
    selected = (request.locality, 'e0_e1') if request.chain == 'e0_e1' else ('r9_count', 'count', 'count_token')
    changes = {}
    if request.intervention:
        paths = [path for key in selected if key in ('e0_e1', 'r9_count', 'count_token') for path in domains[key][1].formula.parameter_paths()]
        if request.intervention.path not in paths:
            raise ValueError('only identified local synchronization parameters may change')
        changes = {request.intervention.path: request.intervention.value}
    actions, owners = [], {}
    def own(batch, owner):
        for action in batch:
            if action.action_id in owners:
                raise ValueError('duplicate cost ownership')
            actions.append(action)
            owners[action.action_id] = owner
    def fixture(identity, pe=0, duration=0., dependencies=(), ready=0., unit=None, reason='explicit unmeasured boundary'):
        own((ComponentAction(action_id=identity, stage='cpu_boundary', component='CPU_ONLY', resource_id=unit or sync[pe],
             work=1, work_unit='fixture-event', duration_cycles=duration, duration_source=reason,
             calibration_sha256=CONTRACT_SHA, dependencies=dependencies, earliest_start=ready,
             participants=(f'rank0.PE{pe}',), status='calibrated'),), 'CPU_ONLY')
    if request.chain == 'e0_e1':
        gate_entry, gate = domains[request.locality]
        handoff_entry, handoff = domains['e0_e1']
        bound = freeze_e0_e1_binding(binding=binding, resources={f'PE{pe}.{role_for_pe(pe)}': sync[pe] for pe in range(32)}, domain_sha256=handoff.domain_sha256, source_refs=(CONTRACT,))
        own(gate_up_actions(gate.work, gate.formula, binding), gate_entry.entry_id)
        own(e0_e1_handoff_actions(handoff, bound, changes=changes), handoff_entry.entry_id)
        for pe in range(32):
            activation = f'rank0.E0.pe{pe}.activation_download1_ready'
            fixture(activation, pe, ready=request.producer_ready_ns[pe])
            for ordinal in range(64):
                selection = f'rank0.E0.pe{pe}.call{ordinal}.selection_ready'
                fixture(selection, pe, ready=ordinal * request.issue_interval_ns)
                for index, engine in enumerate(ENGINES):
                    previous = (issue_ready(pe, ordinal, ENGINES[index - 1]),) if index else ()
                    fixture(issue_ready(pe, ordinal, engine), pe, dependencies=(activation, selection) + previous)
        terminals = [f'E0_E1.handoff.pe{pe}.{role_for_pe(pe)}' for pe in range(32)]
    else:
        resources = {f'PE{pe}.local_vector_ready': sync[pe] for pe in range(32)}
        for key, freezer, generator in (('r9_count', freeze_r9_count_binding, r9_count_handoff_actions), ('count_token', freeze_count_token_binding, count_token_handoff_actions)):
            entry, domain = domains[key]
            bound = freezer(binding=binding, resources=resources, domain_sha256=domain.domain_sha256, source_refs=(CONTRACT,))
            local = {path: value for path, value in changes.items() if path in domain.formula.parameter_paths()}
            own(generator(domain, bound, changes=local), entry.entry_id)
        entry, domain = domains['count']
        bound = freeze_count_binding(binding=binding, resources={name: name for name in count_resources(0)}, domain_sha256=domain.domain_sha256, source_refs=(CONTRACT,))
        own(count_actions(domain, bound), entry.entry_id)
        for pe in range(32):
            dependencies = (f'cpu.packet.{pe}',) if request.cpu_fifo and pe < 2 else ()
            fixture(f'R9.rank0.pe{pe}.filter_write.complete', pe, dependencies=dependencies, ready=request.producer_ready_ns[pe])
        terminals = [f'COUNT_TOKEN.handoff.pe{pe}' for pe in range(32)]
    for index, window in enumerate(request.busy):
        if window.resource not in sync.values():
            raise ValueError('unidentified engine backpressure: only local sync busy fixtures are supported')
        pe = next(pe for pe, name in sync.items() if name == window.resource)
        fixture(f'cpu.busy.{index}', pe, unit=window.resource, duration=window.duration_ns, reason='explicit CPU resource competitor')
    for pe, terminal in enumerate(terminals):
        fixture(f'cpu.consumer_ready.{pe}', pe, ready=request.consumer_ready_ns[pe])
        fixture(f'source.next_ready.{pe}', pe, dependencies=(terminal, f'cpu.consumer_ready.{pe}'), reason='zero-cost next source-stage boundary; no successor service')
    fixture('cpu.independent', unit='cpu.independent', duration=11., reason='independent CPU service hypothesis')
    if request.cpu_fifo:
        fixture('cpu.after_packet', unit='cpu.after_packet', duration=7., dependencies=('cpu.packet.0', 'cpu.packet.1'), reason='CPU compute terminal after packet completion')
    fixture('composition.complete', dependencies=tuple(f'source.next_ready.{pe}' for pe in range(32)) + ('cpu.independent',) + (('cpu.after_packet',) if request.cpu_fifo else ()))
    fixture('composition.terminal', dependencies=('composition.complete',), reason='zero-duration terminal after all source-cut successors')
    _conditional_contexts(actions)
    rows, counters, provenance = [], [], []
    unit_nodes = {unit['unit_id']: unit['node_id'] for unit in graph['execution_units']}
    entry_by_id = {entry.entry_id: (entry, domain) for key in selected for entry, domain in (domains[key],)}
    for action in actions:
        row = dict(transaction_id=action.action_id, depends_on=list(action.dependencies), start_cycles=action.earliest_start)
        if action.duration_cycles == 0:
            counter = action.action_id + '.counter'
            counters.append(dict(counter_id=counter, initial_value=0))
            row.update(kind='signal', counter_id=counter, delta=1)
        else:
            row.update(kind='compute', unit_id=action.resource_id, duration_cycles=action.duration_cycles)
        owner = owners[action.action_id]
        domain = entry_by_id[owner][1] if owner != 'CPU_ONLY' else None
        provenance.append(dict(action_id=action.action_id, cost_key=action.action_id, owner=owner,
            formula_sha256=domain.formula.formula_sha256 if domain else CONTRACT_SHA,
            source_work_sha256=stable_sha256(domain.work.model_dump(mode='json')) if domain else stable_sha256(request.model_dump(mode='json', exclude={'intervention'})),
            work=action.work, work_unit=action.work_unit, bytes=action.bytes, unit='reported_ns',
            resource=action.resource_id, node=unit_nodes[action.resource_id], rank=0,
            stage=action.stage, participants=list(action.participants), dependencies=list(action.dependencies),
            source_contract=CONTRACT_SHA, native_sha256=stable_sha256(row)))
        rows.append(row)
    if request.cpu_fifo:
        for index, payload in enumerate(request.packet_payloads):
            row = dict(kind='transfer', transaction_id=f'cpu.packet.{index}', network_id='cpu.noc', source='cpu.sender', destination='cpu.receiver', payload_bytes=payload, start_cycles=0., depends_on=[])
            rows.append(row)
            provenance.append(dict(action_id=row['transaction_id'], cost_key=row['transaction_id'], owner='CPU_FIFO_ONLY',
                formula_sha256=stable_sha256(timing[1]), source_work_sha256=stable_sha256(request.packet_payloads), work=payload,
                work_unit='CPU-fixture-byte', bytes=payload, unit='reported_ns', resource=['cpu.link.0', 'cpu.link.1'],
                node=['cpu.noc.0', 'cpu.noc.2'], rank=None, stage='CPU_FIFO_ONLY', participants=['cpu.sender', 'cpu.receiver'],
                dependencies=[], source_contract=CONTRACT_SHA, native_sha256=stable_sha256(row)))
    rows.sort(key=lambda row: row['transaction_id'])
    provenance.sort(key=lambda row: row['action_id'])
    counters.sort(key=lambda row: row['counter_id'])
    if request.cancel_action is not None and request.cancel_action not in {row['transaction_id'] for row in rows}:
        raise ValueError('unknown cancellation action')
    batch = GenericTransactionBatch.model_validate_json(json.dumps(dict(kind='generic_transaction_batch', schema_version=1,
        batch_id='b5-source-cut', timing=timing, counters=counters, transactions=rows, max_cycles=request.max_ns,
        operation_envelopes=[dict(operation_id='b5.source_cut', member_transactions=[row['transaction_id'] for row in rows], boundary_ref='CPU-source-cut-not-full-decode')])))
    native = {row.transaction_id: row.model_dump(mode='json') for row in batch.transactions}
    for row in provenance:
        row['native_sha256'] = stable_sha256(native[row['action_id']])
    spec = SystemSpec.model_validate_json(json.dumps(dict(kind='system_spec', schema_version=1, spec_id='b5-source-cut', graph=graph, batch=batch.model_dump(mode='json'))))
    identities = dict(source_contract_sha256=CONTRACT_SHA, bundle_file_sha256=contract['artifacts'][BUNDLE], bundle_sha256=bundle.bundle_sha256,
        source_files=contract['source_files'], artifacts=contract['artifacts'], source_commit=contract['source_commit'],
        entries={entry.entry_id: dict(entry_sha256=entry.entry_sha256, formula_sha256=domain.formula.formula_sha256, work=domain.work.model_dump(mode='json'), timebase=domain.timebase.model_dump(mode='json')) for entry, domain in entry_by_id.values()},
        binding_sha256=binding.binding_sha256, hardware=hardware.model_dump(mode='json'), graph_sha256=stable_sha256(graph),
        effective_timing=timing, missing_real_bindings=contract['missing_real_bindings'],
        communication=dict(scope='local source synchronization', channel=1 if request.chain == 'e0_e1' else None, comm_id=None, controller=None, physical_route=None, buffer_addresses=None),
        fixture_only=True, production_admitted=False, physical_publishable=False)
    return LoweredComposition(request, spec, compile_system(spec), tuple(actions), tuple(provenance), identities)


def lowering_identity(lowered):
    return dict(request=lowered.request.model_dump(mode='json'), spec_sha256=stable_sha256(lowered.spec.model_dump(mode='json')),
                plan_sha256=stable_sha256(lowered.plan.model_dump(mode='json')), provenance_sha256=stable_sha256(lowered.provenance),
                component_actions_sha256=stable_sha256([action.model_dump(mode='json') for action in lowered.actions]), identities=lowered.identities)


def execute_composition(lowered, root, source_root):
    expected = lower_composition(lowered.request, root, source_root)
    if lowering_identity(lowered) != lowering_identity(expected):
        raise ValueError('native-action provenance/ownership closure mismatch')
    runtime = RuntimeContext(expected.plan)
    observer = ResourceObserver(runtime)
    cancel_process = None
    if expected.request.cancel_action:
        def cancel_later():
            try:
                yield runtime.env.timeout(expected.request.cancel_at_ns)
                runtime.cancel(expected.request.cancel_action)
            except simpy.Interrupt:
                return
        cancel_process = runtime.env.process(cancel_later())
    try:
        result = runtime.run()
    finally:
        if cancel_process is not None and cancel_process.is_alive:
            cancel_process.interrupt()
            runtime.env.run(until=cancel_process)
    spans = {span.transaction_id: span for span in result.transactions}
    if result.status == 'complete':
        _validate_conditional_schedule(_conditional_contexts(expected.actions), spans)
    resources = observer.summary()
    graph = observer.causal_graph(expected, spans)
    if not runtime.registry.all_released() or any(process.is_alive for process in runtime._background) or (cancel_process is not None and cancel_process.is_alive):
        raise ValueError('native runtime cleanup failed')
    return dict(result=result.model_dump(mode='json'), result_sha256=stable_sha256(result.model_dump(mode='json')),
                resources=resources, causal_graph=graph, cleanup=True, fixture_only=True,
                production_admitted=False, physical_publishable=False)

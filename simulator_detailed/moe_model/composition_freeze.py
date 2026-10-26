"""Executable B5 receipts with external trust anchors and resource-local proofs."""
import inspect
import importlib
import json
from pathlib import Path
import sys
from types import CodeType

from . import causal_composition
from .causal_composition import CompositionRequest, execute_composition, file_sha, lower_composition, lowering_identity
from .workload import stable_sha256


def seal(body):
    if 'sha256' in body:
        raise ValueError('cannot reseal a sealed document')
    return dict(body, sha256=stable_sha256(body))


def check_seal(document, expected):
    body = {key: value for key, value in document.items() if key != 'sha256'}
    if document.get('sha256') != expected or stable_sha256(body) != expected:
        raise ValueError('trusted receipt/freeze identity mismatch')
    return body


def _code_identity(code):
    def constant(value):
        if isinstance(value, CodeType):
            return _code_identity(value)
        if isinstance(value, tuple):
            return [constant(item) for item in value]
        if isinstance(value, frozenset):
            return sorted(repr(item) for item in value)
        return repr(value)
    return dict(bytecode=code.co_code.hex(), constants=[constant(value) for value in code.co_consts],
                names=code.co_names, variables=code.co_varnames, free=code.co_freevars,
                cells=code.co_cellvars, flags=code.co_flags, arguments=code.co_argcount,
                positional=code.co_posonlyargcount, keyword=code.co_kwonlyargcount,
                exceptions=code.co_exceptiontable.hex())


def executable_manifest():
    import pydantic
    import pydantic_core
    import simpy
    import simulator_detailed
    files, loaded = {}, {}
    runtime = Path(simulator_detailed.__file__).parent
    overlay = Path(causal_composition.__file__).parent
    packages = [('runtime', runtime), ('moe_model', overlay), ('simpy', Path(simpy.__file__).parent),
                ('pydantic', Path(pydantic.__file__).parent), ('pydantic_core', Path(pydantic_core.__file__).parent)]
    for label, directory in packages:
        for path in sorted(directory.rglob('*')):
            relative = path.relative_to(directory)
            if path.suffix not in ('.py', '.so') or any(part in ('__pycache__', 'tests') for part in relative.parts):
                continue
            if label == 'runtime' and 'moe_model' in relative.parts:
                continue
            files[label + '/' + relative.as_posix()] = file_sha(path)
    files['python-executable'] = file_sha(sys.executable)
    native = ('runtime_context', 'system_compile', 'latency_fifo', 'latency_multicast', 'operation_envelope')
    components = ('causal_composition', 'composition_freeze', 'binding', 'component_calibration',
                  'component_service', 'count_fill', 'count_token_handoff', 'e0_e1_handoff',
                  'gate_up_domain', 'gate_up_matrix', 'hardware', 'production_inputs',
                  'r9_count_handoff', 'receipt', 'timebase', 'workload')
    names = ['simulator_detailed.' + name for name in native]
    names += ['simulator_detailed.moe_model.' + name for name in components]
    for name in sorted(names):
        module = importlib.import_module(name)
        for key, value in sorted(vars(module).items()):
            if inspect.isfunction(value) and value.__module__.startswith('simulator_detailed.'):
                loaded[name + '.' + key] = stable_sha256(_code_identity(value.__code__))
            if inspect.isclass(value) and value.__module__.startswith('simulator_detailed.'):
                for method, function in sorted(vars(value).items()):
                    if isinstance(function, (staticmethod, classmethod)):
                        function = function.__func__
                    if inspect.isfunction(function):
                        loaded[name + '.' + key + '.' + method] = stable_sha256(_code_identity(function.__code__))
    return dict(files=files, loaded_code=loaded, python=sys.version, schema='t81.b5.executable.v1')


def freeze_composition(lowered, root, source_root):
    expected = lower_composition(lowered.request, root, source_root)
    if lowering_identity(lowered) != lowering_identity(expected):
        raise ValueError('cannot freeze broken provenance/ownership closure')
    return seal(dict(schema='t81.b5.freeze.v1', lowering=lowering_identity(expected),
        executable=executable_manifest(), graph=expected.spec.graph.model_dump(mode='json'),
        action_count=len(expected.provenance),
        dependencies_sha256=stable_sha256([(row['action_id'], row['dependencies']) for row in expected.provenance]),
        reconstruction='exact deterministic lower_composition(request); graph and all actions sealed by lowering hashes',
        fixture_only=True, production_admitted=False, physical_publishable=False))


def run_frozen(freeze, expected_sha256, root, source_root):
    check_seal(freeze, expected_sha256)
    request = CompositionRequest.model_validate_json(json.dumps(freeze['lowering']['request']))
    lowered = lower_composition(request, root, source_root)
    if freeze_composition(lowered, root, source_root) != freeze:
        raise ValueError('executable freeze reconstruction mismatch')
    execution = execute_composition(lowered, root, source_root)
    if executable_manifest() != freeze['executable']:
        raise ValueError('executable changed during execution')
    result = execution['result']
    terminals = [span for span in result['transactions'] if span['transaction_id'].startswith(('composition.', 'source.next_ready.', 'cpu.after_packet', 'cpu.independent'))]
    receipt = seal(dict(schema='t81.b5.execution-receipt.v1', freeze_sha256=expected_sha256,
        result_sha256=execution['result_sha256'], execution_sha256=stable_sha256(execution),
        provenance_sha256=freeze['lowering']['provenance_sha256'],
        resources_sha256=stable_sha256(execution['resources']), causal_graph_sha256=stable_sha256(execution['causal_graph']),
        action_count=freeze['action_count'], status=result['status'], terminals=terminals,
        cleanup=execution['cleanup'], fixture_only=True, production_admitted=False, physical_publishable=False))
    return receipt, execution


def verify_receipt(receipt, receipt_sha256, freeze, freeze_sha256, root, source_root):
    check_seal(receipt, receipt_sha256)
    replay, execution = run_frozen(freeze, freeze_sha256, root, source_root)
    if replay != receipt:
        raise ValueError('runtime result receipt replay mismatch')
    return execution


def prove_intervention(base_freeze, base_sha256, changed_freeze, changed_sha256, root, source_root):
    base_receipt, base_execution = run_frozen(base_freeze, base_sha256, root, source_root)
    changed_receipt, changed_execution = run_frozen(changed_freeze, changed_sha256, root, source_root)
    if base_receipt['status'] != 'complete' or changed_receipt['status'] != 'complete':
        raise ValueError('invariance requires complete executions')
    base_request = dict(base_freeze['lowering']['request'])
    changed_request = dict(changed_freeze['lowering']['request'])
    if base_request.pop('intervention') is not None:
        raise ValueError('baseline must have accepted parameters')
    intervention = changed_request.pop('intervention')
    if not intervention or base_request != changed_request:
        raise ValueError('intervention changed workload or binding')
    for field in ('identities',):
        if base_freeze['lowering'][field] != changed_freeze['lowering'][field]:
            raise ValueError('intervention changed accepted evidence or hardware')
    for field in ('executable', 'graph', 'dependencies_sha256', 'action_count'):
        if base_freeze[field] != changed_freeze[field]:
            raise ValueError('intervention changed executable or topology')
    base = lower_composition(CompositionRequest.model_validate_json(json.dumps(base_freeze['lowering']['request'])), root, source_root)
    changed = lower_composition(CompositionRequest.model_validate_json(json.dumps(changed_freeze['lowering']['request'])), root, source_root)
    before = {action.action_id: action for action in base.actions}
    after = {action.action_id: action for action in changed.actions}
    if before.keys() != after.keys():
        raise ValueError('intervention changed action inventory')
    direct = []
    for identity, action in before.items():
        old, new = action.model_dump(mode='json'), after[identity].model_dump(mode='json')
        old_duration, new_duration = old.pop('duration_cycles'), new.pop('duration_cycles')
        if old != new:
            raise ValueError('intervention changed non-cost action fields')
        if old_duration != new_duration:
            direct.append(identity)
    if len(direct) != 1:
        raise ValueError('local synchronization intervention must change exactly one service')
    owned_resource = before[direct[0]].resource_id
    if not owned_resource.endswith('.SYNC'):
        raise ValueError('intervention is not synchronization-local')
    affected = set(direct)
    while True:
        extended = affected | {row['action_id'] for row in base.provenance if affected.intersection(row['dependencies'])}
        if extended == affected:
            break
        affected = extended
    old_spans = {span['transaction_id']: span for span in base_execution['result']['transactions']}
    new_spans = {span['transaction_id']: span for span in changed_execution['result']['transactions']}
    shifted = sorted(identity for identity in old_spans if old_spans[identity] != new_spans[identity])
    if any(identity not in affected for identity in shifted):
        raise ValueError('unrelated schedule changed outside causal descendants')
    return seal(dict(schema='t81.b5.invariance-receipt.v1', base_freeze_sha256=base_sha256,
        changed_freeze_sha256=changed_sha256, base_receipt_sha256=base_receipt['sha256'],
        changed_receipt_sha256=changed_receipt['sha256'], intervention=intervention,
        owned_resource=owned_resource, direct_cost_actions=direct, causal_descendants=sorted(affected - set(direct)),
        shifted_spans=shifted, unrelated_costs_unchanged=True, unrelated_schedules_unchanged=True,
        workload_sha256=stable_sha256(base_request), evidence_sha256=stable_sha256(base.identities),
        fixture_only=True, production_admitted=False, physical_publishable=False))

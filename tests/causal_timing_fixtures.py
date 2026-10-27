"""Invented local ticks and opaque labels only; no captured inputs."""
from copy import deepcopy


def model():
    domain = dict(timebase='unit_tick', convention='synthetic_work', participants=['clock-a'],
        engine_scope='abstract_engine', rate_unit='work_per_tick')
    actions = [dict(id=name, clock='clock-a', interval=interval, source_cost_ticks=None)
        for name, interval in [('left', [0, 4]), ('right', [4, 9]), ('overlap', [2, 6])]]
    bindings = [dict(action=name, status='exact', identity=name + '-identity', candidates=[], unknown=None)
        for name in ('left', 'right')]
    bindings.append(dict(action='overlap', status='unresolved', identity=None, candidates=['possible-action'],
        unknown='dynamic_identity_missing'))
    atoms = []
    for index, (interval, active, conservative, inclusive) in enumerate([
            ([0, 2], ['left'], 1, 1), ([2, 4], ['left', 'overlap'], 1, 3),
            ([4, 6], ['right', 'overlap'], 2, 3), ([6, 9], ['right'], 2, 2), ([9, 11], [], 0, 0)]):
        atoms.append(dict(id='atom-' + str(index), clock='clock-a', interval=interval,
            duration_ticks=interval[1] - interval[0], active_actions=active, accounting_owner='owner-' + str(index),
            owner_kind='residual' if not active else 'exclusive_observation' if len(active) == 1 else 'shared_observation',
            exclusive_causal_owner=None, family_mask_conservative=conservative, family_mask_inclusive=inclusive))
    return dict(version='caller-sensitivity-model-v1', mode='causal_timing',
        identity_hooks={key: 'invented-' + key for key in ('workload', 'source', 'routes', 'overlap', 'timebase_contract')},
        timebase='unit_tick', useful_flops=1, peak_domain=domain,
        peak=dict(kind='unavailable', domain=None, parameter=None, interval=None, evidence=None, missing_input='peak_missing'),
        evidence_identity='invented-input', unavailable=[], clocks=[dict(id='clock-a', rank=0, window=[0, 11])],
        join=dict(actions=actions, bindings=bindings, dependencies=[
            dict(id='ordered', before='left', after='right', status='exact', basis='happens_before',
                identity='dependency-identity', unknown=None),
            dict(id='not-ordered', before='right', after='overlap', status='unresolved', basis='correlation',
                identity=None, unknown='causality_missing')]),
        partition=dict(atoms=atoms, causal_latency_ticks=None),
        families=[dict(id=name, law='inverse_partition_response', authority='caller_hypothesis') for name in ('alpha', 'beta')],
        scenarios=[dict(id=membership + '-' + policy, membership=membership, overlap=policy)
            for membership in ('conservative', 'inclusive') for policy in ('shared_serial', 'co_bottleneck')])


def request(instance):
    result = instance.template()
    result.update(study_id='invented-grid', grid_budget=4,
        axes=[dict(id=name, family=name, unit='dimensionless_service_scale', domain=['1', '2'], grid=['1', '2'],
            exposure=dict(identity=name + '-exposure', interval=['1', '1'])) for name in ('alpha', 'beta')])
    return result


def add_clock(spec, offset=31, rank=0):
    original = deepcopy(spec)
    spec['peak_domain']['participants'].append('clock-b')
    spec['clocks'].append(dict(id='clock-b', rank=rank, window=[offset, offset + 11]))
    for action in original['join']['actions']:
        action.update(id='second-' + action['id'], clock='clock-b', interval=[value + offset for value in action['interval']])
        spec['join']['actions'].append(action)
    for binding in original['join']['bindings']:
        binding['action'] = 'second-' + binding['action']
        spec['join']['bindings'].append(binding)
    for atom in original['partition']['atoms']:
        atom.update(id='second-' + atom['id'], clock='clock-b', interval=[value + offset for value in atom['interval']],
            active_actions=['second-' + name for name in atom['active_actions']], accounting_owner='second-' + atom['accounting_owner'])
        spec['partition']['atoms'].append(atom)

"""Invented fixtures, including independent primes chosen to cross old caps."""
from copy import deepcopy

import causal_timing_fixtures as legacy


def model():
    spec = legacy.model()
    spec.update(version='caller-causal-full-analysis-v1', mode='causal_full_analysis')
    spec['clocks'][0]['scope'] = 'layer-scope'
    spec['useful_flops'] = 17
    spec['peak'].update(kind='symbolic', domain=deepcopy(spec['peak_domain']), parameter='synthetic-peak', missing_input=None)
    spec['source_nodes'] = [dict(id='static-unexpanded', clock=None, expansion_status='unresolved',
        invocation_status='unresolved', cost_status='unresolved', source_cost_ticks=None, cost_authority=None,
        exclusive_causal_owner=None, metadata_json='{"candidate":"opaque-source","alias":null}')]
    spec['join']['dependencies'].append(dict(id='source-relation', before='static-unexpanded', after='left',
        status='unresolved', basis='source_protocol', identity=None, unknown='expansion_missing'))
    spec['scenarios'] = [dict(id='scenario-' + str(index), membership=membership, sharing=sharing,
        priority=[1, 0] if sharing == 'priority' else None, exposure_mask=mask)
        for index, (membership, sharing, mask) in enumerate([
            ('inclusive', 'priority', 3), ('inclusive', 'shared_serial', 3),
            ('inclusive', 'co_bottleneck', 3), ('conservative', 'priority', 1),
            ('inclusive', 'shared_serial', 0)])]
    spec['composition'] = dict(ticks_per_ns='3', peak_rate_time_unit='tick', participants=[dict(clock='clock-a', scope='layer-scope',
        release_offset_ticks=2, attention_offset_ticks=7, baseline_layer_ticks=13)],
        baseline_layer_ticks=13, peak_participants=['clock-a'])
    spec['capacity'] = dict(records={key: 100 for key in ('clocks', 'actions', 'bindings', 'dependencies', 'source_nodes',
        'atoms', 'families', 'scenarios', 'active_memberships', 'binding_candidates', 'priority_entries', 'metadata_bytes')},
        validation_work=10000)
    return spec


def request(instance):
    result = instance.template()
    result.update(study_id='synthetic-full-study',
        axes=[dict(id=name, family=name, unit='dimensionless_service_scale', domain=['1', '4'], grid=['1', '2', '4']) for name in ('alpha', 'beta')],
        budget={key: 10000000 for key in result['budget']}, comparisons=dict(policy='all', pairs=[]),
        surfaces=[dict(id=name, axes=axes, domain=['1', '4'], extrapolation='inverse_scale_to_infinity')
            for name, axes in [('alpha-oat', ['alpha']), ('diagonal', ['alpha', 'beta'])]])
    return result


def large_model():
    spec = model()
    count = 50021
    spec['clocks'][0]['window'] = [0, count]
    spec['join'] = dict(actions=[], bindings=[], dependencies=[])
    spec['partition']['atoms'] = []
    for index in range(count):
        name = 'synthetic-action-' + str(index)
        spec['join']['actions'].append(dict(id=name, clock='clock-a', interval=[index, index + 1], source_cost_ticks=None))
        spec['join']['bindings'].append(dict(action=name, status='exact', identity='synthetic-binding-' + str(index), candidates=[], unknown=None))
        spec['partition']['atoms'].append(dict(id='synthetic-atom-' + str(index), clock='clock-a', interval=[index, index + 1],
            duration_ticks=1, active_actions=[name], accounting_owner='synthetic-owner-' + str(index),
            owner_kind='exclusive_observation', exclusive_causal_owner=None,
            family_mask_conservative=1 + index % 2, family_mask_inclusive=3))
    spec['composition']['participants'][0]['baseline_layer_ticks'] = count + 2
    spec['composition']['baseline_layer_ticks'] = count + 2
    spec['scenarios'] = [dict(id='synthetic-scenario-' + str(index), membership='inclusive',
        sharing=('priority', 'shared_serial', 'co_bottleneck')[index % 3],
        priority=[index % 2, 1 - index % 2] if index % 3 == 0 else None, exposure_mask=index % 4) for index in range(31)]
    spec['capacity'] = dict(records={key: 300000 for key in spec['capacity']['records']}, validation_work=10000000)
    return spec


def large_request(instance):
    result = request(instance)
    result['comparisons'] = dict(policy='selected', pairs=[[0, 1], [0, 360]])
    result['surfaces'] = []
    for axis in result['axes']:
        axis.update(domain=['1', '19'], grid=[str(value) for value in range(1, 20)])
    return result

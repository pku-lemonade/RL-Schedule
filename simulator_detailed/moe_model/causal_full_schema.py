"""Closed, versioned contracts with caller-declared record and work limits."""
from copy import deepcopy

if __package__:
    from . import design_space_schema as wire
else:
    import design_space_schema as wire

MODE = 'causal_full_analysis'
VERSION = 'caller-causal-full-analysis-v1'
SUMMARY_VERSION = 'caller-causal-full-analysis-summary-v1'
obj, label, enum = wire._object, wire._label, wire._enum
integer = dict(type='integer', minimum=0)
null = dict(type='null')
boolean = dict(type='boolean')
RECORDS = ('clocks', 'actions', 'bindings', 'dependencies', 'source_nodes', 'atoms',
    'families', 'scenarios', 'active_memberships', 'binding_candidates', 'priority_entries', 'metadata_bytes')
WORK = ('designs', 'scenario_evaluations', 'response_terms', 'comparison_evaluations',
    'analysis_units', 'surface_work', 'output_records')


def array(items, minimum=0, unique=False):
    return dict(type='array', items=items, minItems=minimum, uniqueItems=unique)


def rational():
    return obj(dict(numerator=dict(type='integer'),
        denominator=dict(type='integer', minimum=1)))


def interval():
    return wire._array(rational(), 2, 2)


def fixed(value):
    return dict(type='boolean', const=value)


def model_schema():
    schema = wire.causal_timing_schema()
    fields = schema['properties']
    fields['version'] = enum(VERSION)
    fields['mode'] = enum(MODE)
    fields['clocks']['items']['properties']['scope'] = label()
    fields['clocks']['items']['required'].append('scope')
    for key in ('clocks', 'families', 'scenarios'):
        fields[key].pop('maxItems')
    for item in fields['join']['properties'].values():
        item.pop('maxItems')
    fields['partition']['properties']['atoms'].pop('maxItems')
    fields['partition']['properties']['atoms']['items']['properties']['active_actions'].pop('maxItems')
    fields['join']['properties']['bindings']['items']['properties']['candidates'].pop('maxItems')
    fields['join']['properties']['dependencies']['items']['properties']['basis'] = enum(
        'happens_before', 'logical', 'correlation', 'source_protocol')
    fields['scenarios'] = array(obj(dict(id=label(), membership=enum('conservative', 'inclusive'),
        sharing=enum('priority', 'shared_serial', 'co_bottleneck'),
        priority=wire._nullable(array(integer, unique=True)), exposure_mask=integer)), 1)
    fields['source_nodes'] = array(obj(dict(id=label(), clock=wire._nullable(label()),
        expansion_status=enum('known', 'unresolved'), invocation_status=enum('known', 'unresolved'),
        cost_status=enum('known', 'unresolved'), source_cost_ticks=wire._nullable(integer),
        cost_authority=wire._nullable(label()), exclusive_causal_owner=null,
        metadata_json=dict(type='string', minLength=2, maxLength=1000000, pattern=r'^[\s\S]*$'))))
    fields['capacity'] = obj(dict(records=obj({key: integer for key in RECORDS}), validation_work=integer))
    fields['composition'] = obj(dict(ticks_per_ns=wire._decimal(), peak_rate_time_unit=enum('tick', 'ns'),
        participants=array(obj(dict(clock=label(), scope=label(), release_offset_ticks=integer,
            attention_offset_ticks=integer, baseline_layer_ticks=integer)), 1),
        baseline_layer_ticks=integer, peak_participants=array(label(), 1, True)))
    fields['peak_domain']['properties']['participants'].pop('maxItems')
    fields['peak']['properties']['domain']['properties']['participants'].pop('maxItems')
    schema['required'] = list(fields)
    schema['title'] = 'Caller finite causal full analysis model'
    return deepcopy(schema)


def output_version(detail='full'):
    wire.need(type(detail) is str and detail in ('full', 'summary'), 'OUTPUT_DETAIL', '$', 'explicit full or summary detail required')
    return SUMMARY_VERSION if detail == 'summary' else VERSION


def checked_version(version):
    wire.need(type(version) is str and version in (VERSION, SUMMARY_VERSION), 'OUTPUT_VERSION', '$/version', 'known explicit full-analysis version required')
    return version


def request_schema(version=VERSION):
    return obj(dict(version=enum(checked_version(version)), mode=enum(MODE), input_identity=label(), study_id=label(),
        claim=enum('conditional_not_physical_truth'), budget=obj({key: integer for key in WORK}),
        axes=array(obj(dict(id=label(), family=label(), unit=enum('dimensionless_service_scale'),
            domain=wire._span(), grid=array(wire._decimal(), 2, True))), 1),
        comparisons=obj(dict(policy=enum('selected', 'all'), pairs=array(wire._array(integer, 2, 2), unique=True))),
        surfaces=array(obj(dict(id=label(), axes=array(label(), 1, True), domain=wire._span(),
            extrapolation=enum('forbidden', 'inverse_scale_to_infinity'))))))


def result_schema(version=VERSION):
    checked_version(version)
    terminal = obj(dict(clock=label(), rank=integer, branch=enum('attention', 'release_plus_moe')))
    bounds = obj(dict(interval=interval(), lower_scenarios=array(label(), 1), upper_scenarios=array(label(), 1)))
    mfu = obj(dict(coefficient=bounds, numeric_interval=wire._nullable(interval()),
        status=enum('numeric', 'symbolic', 'unavailable'), parameter=wire._nullable(label()), peak_to_per_ns=rational()))
    scenario = obj(dict(id=label(), latency_ns=rational(), moe_ns=rational(), mfu_coefficient=rational(),
        clocks=array(obj(dict(clock=label(), rank=integer, moe_ticks=rational(),
            layer_ticks=wire._nullable(rational()))), 1), terminals=array(terminal, 1)))
    point = obj(dict(index=integer, rates=array(rational(), 1), scenarios=array(scenario, 1),
        latency_ns=bounds, moe_ns=bounds, mfu=mfu))
    paired = obj(dict(left=integer, right=integer, delta_ns=interval(),
        faster=integer, equal=integer, slower=integer,
        deltas=array(obj(dict(scenario=label(), delta_ns=rational())), 1)))
    dominance = obj(dict(left=integer, right=integer, robust=boolean, opportunity=boolean,
        guaranteed=boolean, latency_guaranteed_strict=boolean))
    branch = obj(dict(inverse_scale_interval=interval(), layer_intercept=rational(), layer_slope=rational(),
        moe_intercept=rational(), moe_slope=rational(), terminals=array(terminal, 1)))
    surface = obj(dict(id=label(), scenario=label(), axes=array(label(), 1), domain=wire._span(),
        extrapolation=enum('forbidden', 'inverse_scale_to_infinity'), segments=array(branch, 1),
        knots=array(obj(dict(inverse_scale=rational(), latency_ns=rational(), terminals=array(terminal, 1))), 1),
        baseline_break_even_scale_intervals=array(interval()),
        finite_saturation_scale=wire._nullable(rational()), asymptotic_latency_ns=wire._nullable(rational())))
    comparison = deepcopy(paired)
    if version == SUMMARY_VERSION:
        del comparison['properties']['deltas']
        comparison['required'].remove('deltas')
    return obj(dict(version=enum(version), mode=enum(MODE), input_identity=label(), request_identity=label(),
        output_identity=label(), evaluation_status=enum('evaluated'),
        model=model_schema(), request=request_schema(version), work=obj({key: integer for key in WORK}),
        baseline_index=integer, grid=array(point, 1), oat=array(paired), comparisons=array(comparison),
        joint_minus_oat=array(obj(dict(index=integer, interval=interval(),
            scenarios=array(obj(dict(scenario=label(), gain_ns=rational())), 1)))),
        pareto=obj(dict(coverage=enum('complete', 'selected_pairs'), edges=array(dominance),
            robust_frontier=wire._nullable(array(integer)), opportunity_frontier=wire._nullable(array(integer)),
            guaranteed_frontier=wire._nullable(array(integer)),
            latency_only_outer_set=wire._nullable(array(integer)))),
        migrations=array(obj(dict(left=integer, right=integer, scenario=label(),
            before=array(terminal, 1), after=array(terminal, 1), changed=boolean))), surfaces=array(surface),
        unavailable=obj(dict(source_costs=enum('caller_metadata_only_not_timing'), exclusive_causal_owner=null,
            causal_latency_ns=null, causal_mfu=null, physical_promotion=fixed(False), hardware_investment_break_even=null,
            numeric_identity_status=enum('unavailable_without_physical_binding'), no_public_evaluation=fixed(False)))))

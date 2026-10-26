"""Strict, architecture-neutral wire contracts for caller-owned sensitivity."""
from copy import deepcopy
import json
import re

MODEL_VERSION = 'caller-sensitivity-model-v1'
REQUEST_VERSION = 'caller-design-space-request-v1'
RESULT_VERSION = 'caller-design-space-result-v1'
COUPLING = 'shared_box_and_scenario_across_grid'


class Rejected(ValueError):
    def __init__(self, code, path, reason):
        self.decision = dict(status='rejected', code=code, path=path, reason=reason)
        super().__init__(code + ' at ' + path + ': ' + reason)


def need(condition, code, path, reason):
    if not condition:
        raise Rejected(code, path, reason)


def _object(properties):
    return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)


def _array(items, minimum=0, maximum=50000, unique=False):
    return dict(type='array', items=items, minItems=minimum, maxItems=maximum, uniqueItems=unique)


def _enum(*values):
    return dict(enum=list(values))


def _label():
    return dict(type='string', minLength=1, maxLength=240, pattern=r'^[A-Za-z0-9][A-Za-z0-9_./:-]*$')


def _decimal():
    return dict(type='string', minLength=1, maxLength=120, pattern=r'^-?(0|[1-9][0-9]*)(\.[0-9]+)?$')


def _span():
    return _array(_decimal(), 2, 2)


def _domain():
    return _object(dict(timebase=_label(), convention=_label(), participants=_array(_label(), 1, 512, True),
        engine_scope=_label(), rate_unit=_label()))


def _nullable(schema):
    result = deepcopy(schema)
    result['type'] = [result['type'], 'null']
    return result


def _peak():
    return _object(dict(kind=_enum('symbolic', 'accepted', 'hypothesis', 'unavailable'),
        domain=_nullable(_domain()), parameter=_nullable(_label()), interval=_nullable(_span()),
        evidence=_nullable(_label()), missing_input=_nullable(_label())))


def _unavailable():
    return _array(_object(dict(output=_label(), missing_input=_label())), 0, 512)


def _common_model():
    return dict(version=_enum(MODEL_VERSION), identity_hooks=_object({name: _label() for name in
        ('workload', 'source', 'routes', 'overlap', 'timebase_contract')}), timebase=_label(),
        useful_flops=dict(type='integer', minimum=1), peak_domain=_domain(), peak=_peak(),
        evidence_identity=_label(), unavailable=_unavailable())


def model_schema(mode):
    need(mode in ('hypothetical_joint', 'evidence_portfolio'), 'MODE', '$/mode', 'supported explicit mode required')
    cost = _object(dict(interval=_span(), reference=_decimal()))
    node = _object(dict(id=_label(), clock=_label(), dependencies=_array(_label(), 0, 512, True),
        owner=_label(), resources=_array(_label(), 1, 512, True), service=cost, residual=cost, observed_interval=_span()))
    event = _object(dict(id=_label(), owner=_label(), clock=_label(), interval=_span()))
    partition = _object(dict(event=_label(), node=_nullable(_label()),
        role=_enum('service_evidence_only', 'context_only')))
    scenario = _object(dict(id=_label(), assignments=_array(_object(dict(resource=_label(),
        bank=_label(), port=_label(), route=_label())), 1, 512),
        route_contention=_array(_object(dict(route=_label(), choice=_enum('no_added_order', 'serialize'),
            node_order=_array(_label(), 0, 512, True))), 1, 512)))
    joint = _object(dict(_common_model(), mode=_enum('hypothetical_joint'), nodes=_array(node, 1, 512),
        boundaries=_array(_object(dict(clock=_label(), interval=_span(), terminals=_array(_label(), 1, 512, True))), 1, 512),
        event_inventory=_array(event, 0), event_partition=_array(partition, 0), scenarios=_array(scenario, 1, 16),
        families=_array(_object(dict(id=_label(), law=_enum('inverse_exclusive_service'),
            nodes=_array(_label(), 1, 512, True), authority=_enum('caller_hypothesis'))), 1, 8)))
    portfolio = _object(dict(_common_model(), mode=_enum('evidence_portfolio'),
        terminals=_array(_object(dict(id=_label(), full=_span(), fixed=_span(), responsive=_span())), 1, 512),
        families=_array(_object(dict(id=_label(), law=_enum('inverse_response_cap'), cap=_decimal(),
            binding_identity=_label(), authority=_enum('caller_evidence'))), 1, 8)))
    selected = joint if mode == 'hypothetical_joint' else portfolio
    return deepcopy(dict(selected, title='Caller sensitivity model', **{'$schema': 'https://json-schema.org/draft/2020-12/schema'}))


def request_schema(mode):
    need(mode in ('hypothetical_joint', 'evidence_portfolio'), 'MODE', '$/mode', 'supported explicit mode required')
    axis = _object(dict(id=_label(), family=_label(), unit=_enum('dimensionless_service_scale'), domain=_span(),
        grid=_array(_decimal(), 2, 16, True), exposure=_object(dict(identity=_label(), interval=_span()))))
    uncertainty = dict(coupling=_enum(COUPLING))
    if mode == 'hypothetical_joint':
        uncertainty.update(costs=_enum('model_independent_box'), sharing=_enum('model_scenarios'))
    else:
        uncertainty.update(costs=_enum('unresolved_not_instantiated'), sharing=_enum('fixed_observed_overlap'),
            readiness=_object(dict(mode=_enum('shared', 'bounded'), radius=_decimal(),
                path_count=dict(type='integer', minimum=0, maximum=512))))
    return deepcopy(dict(_object(dict(version=_enum(REQUEST_VERSION), mode=_enum(mode), input_identity=_label(), study_id=_label(),
        claim=_enum('conditional_not_physical_truth'), grid_budget=dict(type='integer', minimum=1, maximum=256),
        axes=_array(axis, 1, 8), uncertainty=_object(uncertainty))),
        **{'$schema': 'https://json-schema.org/draft/2020-12/schema'}, title='Explicit joint parameter grid'))


def mode_of(value):
    need(type(value) is dict, 'SCHEMA_TYPE', '$', 'object required')
    mode = value.get('mode')
    need(mode in ('hypothetical_joint', 'evidence_portfolio'), 'MODE', '$/mode', 'supported explicit mode required')
    return mode


def validate(value, schema, path='$'):
    if 'enum' in schema:
        need(type(value) is str and value in schema['enum'], 'SCHEMA_ENUM', path, 'unsupported contract value')
    kind = schema.get('type')
    if type(kind) is list:
        if value is None:
            return
        kind = next(item for item in kind if item != 'null')
    if kind == 'object':
        need(type(value) is dict, 'SCHEMA_TYPE', path, 'object required')
        need(set(value) == set(schema['properties']), 'SCHEMA_FIELDS', path,
            'missing=' + ','.join(sorted(set(schema['properties']) - set(value))) +
            '; unexpected=' + ','.join(sorted(set(value) - set(schema['properties']))))
        for key, child in schema['properties'].items():
            validate(value[key], child, path + '/' + key)
    elif kind == 'array':
        need(type(value) is list and schema['minItems'] <= len(value) <= schema['maxItems'],
            'SCHEMA_SIZE', path, 'bounded array required')
        if schema['uniqueItems']:
            need(len({json.dumps(item, sort_keys=True) for item in value}) == len(value), 'SCHEMA_UNIQUE', path, 'unique items required')
        for index, item in enumerate(value):
            validate(item, schema['items'], path + '/' + str(index))
    elif kind == 'string':
        need(type(value) is str and schema['minLength'] <= len(value) <= schema['maxLength'] and
            re.fullmatch(schema['pattern'], value) is not None, 'SCHEMA_STRING', path, 'strict bounded string grammar required')
    elif kind == 'integer':
        need(type(value) is int and value >= schema['minimum'] and value <= schema.get('maximum', value),
            'SCHEMA_INTEGER', path, 'integer within contract limits required')
    elif kind == 'null':
        need(value is None, 'SCHEMA_NULL', path, 'null required')


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'DUPLICATE_JSON_KEY', '$', 'unique object keys required')
            result[key] = value
        return result
    def nonfinite(value):
        raise Rejected('NONFINITE_JSON', '$', 'finite JSON required')
    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=nonfinite)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise Rejected('JSON_SYNTAX', '$', 'valid UTF-8 JSON required') from error

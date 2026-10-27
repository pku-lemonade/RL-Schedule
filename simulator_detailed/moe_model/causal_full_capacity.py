"""Admission-bound canonical byte upper bounds; no grid or numerical evaluation."""
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import tempfile

if __package__:
    from . import causal_full_analysis as full, causal_full_schema as schema
    from . import causal_full_stream as stream, counterfactual as formulas, design_space_schema as wire
else:
    import causal_full_analysis as full
    import causal_full_schema as schema
    import causal_full_stream as stream
    import counterfactual as formulas
    import design_space_schema as wire

VERSION = 'caller-summary-capacity-v1'
LIMIT_VERSION = 'caller-summary-capacity-limits-v1'
RESERVE = 1073741824


def limits_schema():
    natural = dict(type='integer', minimum=0)
    return wire._object(dict(version=wire._enum(LIMIT_VERSION),
        filesystem=dict(type='string', minLength=1, maxLength=1048576, pattern=r'^[\s\S]+$'),
        device=natural, frozen_available_bytes=natural,
        output_limits_bytes=schema.array(dict(type='integer', minimum=1)),
        reserve_bytes=dict(type='integer', const=RESERVE, minimum=RESERVE),
        logs_and_gate_bytes=dict(type='integer', minimum=1048576), additional_temporary_bytes=natural))


def digits(value):
    value = abs(value)
    estimate = max(1, (value.bit_length() * 30103) // 100000)
    while value >= 10 ** estimate:
        estimate += 1
    while estimate > 1 and value < 10 ** (estimate - 1):
        estimate -= 1
    return estimate


class RationalBound:
    def __init__(self, numerator, denominator):
        wire.need(type(numerator) is int and numerator >= 0 and type(denominator) is int and denominator >= 1,
            'CAPACITY_WIDTH', '$', 'proved integer magnitude bounds required')
        self.numerator, self.denominator = numerator, denominator

    def divide(self, other):
        return RationalBound(self.numerator * other.denominator, self.denominator * max(1, other.numerator))

    def multiply(self, other):
        return RationalBound(self.numerator * other.numerator, self.denominator * other.denominator)

    def widths(self):
        return dict(numerator_digits=digits(self.numerator), numerator_sign_bytes=1,
            denominator_digits=digits(self.denominator))

    def size(self, depth):
        return object_size(dict(numerator=digits(self.numerator) + 1, denominator=digits(self.denominator)), depth)


def literal(value):
    return lambda depth: sum(len(token) for token in stream._tokens(value, depth))


def string_size(value):
    return len(json.dumps(value, allow_nan=False).encode())


def object_size(fields, depth):
    if not fields:
        return 2
    return sum(fields.values()) + sum(string_size(key) for key in fields) + len(fields) * (2 * depth + 6) + 2 * depth + 2


def array_size(count, item_bytes, depth):
    return item_bytes + count * (2 * depth + 4) + 2 * depth + 2 if count else 2


def obj(fields):
    return lambda depth: object_size({key: child(depth + 1) for key, child in fields.items()}, depth)


def repeated(count, item):
    return lambda depth: array_size(count, count * item(depth + 1), depth)


def known(items):
    return lambda depth: array_size(len(items), sum(item(depth + 1) for item in items), depth)


def integer(maximum):
    return lambda depth: digits(maximum)


def _widths(spec, timing, request):
    inverse_rates = [Fraction(1)] + [1 / Fraction(rate) for axis in request['axes'] for rate in axis['grid']]
    sharing = math.lcm(*range(1, len(spec['families']) + 1))
    common = math.lcm(*(rate.denominator for rate in inverse_rates)) * sharing
    factor = max(inverse_rates)
    duration = max(sum(row['duration_ticks'] for row in clock['histogram'][membership])
        for clock in timing['clocks'] for membership in ('conservative', 'inclusive'))
    maximum = duration * factor * common
    wire.need(maximum.denominator == 1, 'CAPACITY_WIDTH', '$', 'common denominator must cover every response mean')
    offset = max(max(row['release_offset_ticks'], row['attention_offset_ticks']) for row in spec['composition']['participants'])
    local = RationalBound(maximum.numerator + offset * common, common)
    ticks = Fraction(spec['composition']['ticks_per_ns'])
    latency = RationalBound(local.numerator * ticks.denominator, common * ticks.numerator)
    coefficient = RationalBound(spec['useful_flops'] * latency.denominator, max(1, latency.numerator))
    peak_to_ns = ticks if spec['composition']['peak_rate_time_unit'] == 'tick' else Fraction(1)
    numeric = coefficient
    if spec['peak']['kind'] in ('accepted', 'hypothesis'):
        peaks = [Fraction(value) * peak_to_ns for value in spec['peak']['interval']]
        numeric = coefficient.divide(RationalBound(max(value.numerator for value in peaks), max(value.denominator for value in peaks)))
    curve = RationalBound((duration + offset) * sharing * ticks.denominator, sharing * ticks.numerator)
    endpoint_numerators, endpoint_denominators = [1, 2 * curve.numerator], [1, 2 * curve.numerator]
    for surface in request['surfaces']:
        for value in surface['domain']:
            inverse = 1 / Fraction(value)
            endpoint_numerators.append(inverse.numerator)
            endpoint_denominators.append(inverse.denominator)
    endpoint = RationalBound(max(endpoint_numerators), max(endpoint_denominators))
    knot_latency = RationalBound(curve.numerator * (endpoint.denominator + endpoint.numerator),
        curve.denominator * endpoint.denominator)
    return dict(local=local, latency=latency, coefficient=coefficient, numeric=numeric,
        delta=RationalBound(2 * latency.numerator, latency.denominator),
        joint=RationalBound(2 * (len(request['axes']) + 1) * latency.numerator, latency.denominator),
        curve=curve, endpoint=endpoint, scale=RationalBound(endpoint.denominator, endpoint.numerator),
        knot_latency=knot_latency)


def _string_lengths(value):
    if type(value) is str:
        yield string_size(value)
    elif type(value) is dict:
        for key in sorted(value):
            yield string_size(key)
            yield from _string_lengths(value[key])
    elif type(value) is list:
        for child in value:
            yield from _string_lengths(child)


def estimate(spec, timing, request, work):
    widths = _widths(spec, timing, request)
    designs, scenarios = work['designs'], len(spec['scenarios'])
    axes = len(request['axes'])
    pairs = designs * (designs - 1) if request['comparisons']['policy'] == 'all' else len(request['comparisons']['pairs'])
    oat_count = sum(len(axis['grid']) - 1 for axis in request['axes'])
    neighbours = sum((len(axis['grid']) - 1) * (designs // len(axis['grid'])) for axis in request['axes'])
    participants = {row['clock'] for row in spec['composition']['participants']}
    terminal_items = [literal(dict(clock=clock['id'], rank=clock['rank'], branch=branch))
        for clock in spec['clocks'] if clock['id'] in participants for branch in ('attention', 'release_plus_moe')]
    terminals = known(terminal_items)
    scenario_ids = known([literal(row['id']) for row in spec['scenarios']])
    def interval(bound):
        return repeated(2, bound.size)
    def bounds(bound):
        return obj(dict(interval=interval(bound), lower_scenarios=scenario_ids, upper_scenarios=scenario_ids))
    local_rows = known([obj(dict(clock=literal(clock['id']), rank=literal(clock['rank']),
        moe_ticks=widths['local'].size, layer_ticks=widths['local'].size if clock['id'] in participants else literal(None)))
        for clock in spec['clocks']])
    grid_rows = known([obj(dict(id=literal(row['id']), latency_ns=widths['latency'].size, moe_ns=widths['latency'].size,
        mfu_coefficient=widths['coefficient'].size, clocks=local_rows, terminals=terminals)) for row in spec['scenarios']])
    ticks = Fraction(spec['composition']['ticks_per_ns']) if spec['composition']['peak_rate_time_unit'] == 'tick' else Fraction(1)
    mfu = obj(dict(coefficient=bounds(widths['coefficient']), status=literal('unavailable'), parameter=literal(spec['peak']['parameter']),
        numeric_interval=interval(widths['numeric']) if spec['peak']['kind'] in ('accepted', 'hypothesis') else literal(None),
        peak_to_per_ns=literal(dict(numerator=ticks.numerator, denominator=ticks.denominator))))
    rates = known([RationalBound(max(Fraction(value).numerator for value in axis['grid']),
        max(Fraction(value).denominator for value in axis['grid'])).size for axis in request['axes']])
    point = obj(dict(index=integer(designs - 1), rates=rates, scenarios=grid_rows, latency_ns=bounds(widths['latency']),
        moe_ns=bounds(widths['latency']), mfu=mfu))
    paired_fields = dict(left=integer(designs - 1), right=integer(designs - 1), delta_ns=interval(widths['delta']),
        faster=integer(scenarios), equal=integer(scenarios), slower=integer(scenarios))
    comparison = obj(paired_fields)
    oat = obj(dict(paired_fields, deltas=known([obj(dict(scenario=literal(row['id']), delta_ns=widths['delta'].size))
        for row in spec['scenarios']])))
    edge = obj(dict(left=integer(designs - 1), right=integer(designs - 1), robust=literal(False), opportunity=literal(False),
        guaranteed=literal(False), latency_guaranteed_strict=literal(False)))
    joint = obj(dict(index=integer(designs - 1), interval=interval(widths['joint']),
        scenarios=known([obj(dict(scenario=literal(row['id']), gain_ns=widths['joint'].size)) for row in spec['scenarios']])))
    migration_rows = [obj(dict(left=integer(designs - 1), right=integer(designs - 1), scenario=literal(row['id']),
        before=terminals, after=terminals, changed=literal(False))) for row in spec['scenarios']]
    migrations = lambda depth: array_size(neighbours * scenarios,
        neighbours * sum(row(depth + 1) for row in migration_rows), depth)
    participant_count = len(participants)
    segment_count = 2 * (math.comb(participant_count, 2) + math.comb(2 * participant_count, 2) + 1)
    segment = obj(dict(inverse_scale_interval=interval(widths['endpoint']), layer_intercept=widths['curve'].size,
        layer_slope=widths['curve'].size, moe_intercept=widths['curve'].size, moe_slope=widths['curve'].size, terminals=terminals))
    def surface_item(surface, scenario):
        return obj(dict(id=literal(surface['id']), scenario=literal(scenario['id']), axes=literal(surface['axes']),
            domain=literal(surface['domain']), extrapolation=literal(surface['extrapolation']), segments=repeated(segment_count, segment),
            knots=repeated(segment_count + 1, obj(dict(inverse_scale=widths['endpoint'].size,
                latency_ns=widths['knot_latency'].size, terminals=terminals))),
            baseline_break_even_scale_intervals=repeated(segment_count, interval(widths['scale'])),
            finite_saturation_scale=widths['scale'].size, asymptotic_latency_ns=widths['curve'].size))
    surfaces = lambda depth: array_size(len(request['surfaces']) * scenarios,
        sum(surface_item(surface, scenario)(depth + 1) for surface in request['surfaces'] for scenario in spec['scenarios']), depth)
    frontier = repeated(designs, integer(designs - 1)) if pairs == designs * (designs - 1) else literal(None)
    pareto = obj(dict(coverage=literal('selected_pairs'), edges=repeated(pairs, edge), robust_frontier=frontier,
        opportunity_frontier=frontier, guaranteed_frontier=frontier, latency_only_outer_set=frontier))
    fields = dict(version=literal(schema.SUMMARY_VERSION), mode=literal(schema.MODE), input_identity=literal('0' * 64),
        request_identity=literal('0' * 64), output_identity=literal('0' * 64), evaluation_status=literal('evaluated'),
        model=literal(spec), request=literal(request), work=literal(work), baseline_index=integer(designs - 1),
        grid=repeated(designs, point), oat=repeated(oat_count, oat), comparisons=repeated(pairs, comparison),
        joint_minus_oat=repeated(designs, joint), pareto=pareto, migrations=migrations, surfaces=surfaces,
        unavailable=literal(dict(source_costs='caller_metadata_only_not_timing', exclusive_causal_owner=None,
            causal_latency_ns=None, causal_mfu=None, physical_promotion=False, hardware_investment_break_even=None,
            numeric_identity_status='unavailable_without_physical_binding', no_public_evaluation=False)))
    ledger = {key: item(1) for key, item in fields.items()}
    result_bytes = object_size(ledger, 0) + 1
    comparisons_bytes, edges_bytes = repeated(pairs, comparison)(1), repeated(pairs, edge)(2)
    lengths = list(_string_lengths(spec)) + list(_string_lengths(request))
    return dict(result_bytes=result_bytes, comparison_spool_bytes=comparisons_bytes, edge_spool_bytes=edges_bytes,
        other_spool_bytes=0, groups=ledger, root_framing_and_newline_bytes=result_bytes - sum(ledger.values()),
        cardinalities=dict(designs=designs, scenarios=scenarios, directed_pairs=pairs, oat=oat_count,
            joint=designs, neighbour_scenarios=neighbours * scenarios,
            surfaces=len(request['surfaces']) * scenarios, segments_per_surface=segment_count, knots_per_surface=segment_count + 1)), \
        {name: bound.widths() for name, bound in widths.items()}, dict(count=len(lengths), total_bytes=sum(lengths),
            ordered_lengths_sha256=formulas.identity(lengths), scenario_bytes=sum(string_size(row['id']) for row in spec['scenarios']),
            clock_bytes=sum(string_size(row['id']) for row in spec['clocks']))


def _source_bindings():
    directory = Path(__file__).resolve().parent
    names = ('causal_full_analysis.py', 'causal_full_schema.py', 'design_space_schema.py', 'design_space.py', 'causal_full_capacity.py')
    hashes = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in names}
    serializers = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for name in ('causal_full_stream.py', 'counterfactual.py')}
    return dict(source_identity=formulas.identity(hashes),
        serializer_identity=formulas.identity(serializers),
        schema_identity=formulas.identity(schema.result_schema(schema.SUMMARY_VERSION)))


def _proof(model, request, limits):
    wire.validate(limits, limits_schema())
    wire.validate(request, schema.request_schema(schema.SUMMARY_VERSION))
    wire.need(model._mode == schema.MODE and formulas.identity(model._specification) == model.identity == request['input_identity'],
        'CAPACITY_IDENTITY', '$', 'admitted unchanged model and explicit summary request required')
    wire.validate(model._specification, schema.model_schema())
    timing, baseline = full.admit(model._specification, admission_only=True)
    wire.need(formulas.identity(timing) == formulas.identity(model._timing), 'CAPACITY_IDENTITY', '$', 'unchanged admitted timing required')
    grids, work = full._prepare(model._specification, timing, request)
    return _certificate(model, request, limits, timing, work, formulas.identity(request), formulas.identity(timing))


def _certificate(model, request, limits, timing, work, request_identity, timing_identity):
    bounds, widths, lengths = estimate(model._specification, timing, request, work)
    bounds['peak_additional_disk_bytes'] = (2 * bounds['result_bytes'] + bounds['comparison_spool_bytes'] + bounds['edge_spool_bytes']
        + limits['additional_temporary_bytes'] + limits['logs_and_gate_bytes'] + limits['reserve_bytes'])
    result = dict(version=VERSION, status='PASS', output_version=schema.SUMMARY_VERSION, input_identity=model.identity,
        request_identity=request_identity, timing_identity=timing_identity, work=work,
        limits=deepcopy(limits), limits_identity=formulas.identity(limits), bounds=bounds, digit_bounds=widths,
        identifier_lengths=lengths, proof='common-denominator-response-and-affine-envelope-v1', **_source_bindings())
    result['proof_identity'] = formulas.identity(result)
    wire.need(4 * (len(formulas.encoded(result)) + 1024) <= limits['logs_and_gate_bytes'],
        'CAPACITY_ARTIFACTS', '$', 'gate and two completion receipts must fit artifact allowance')
    return result


def _available(limits, sink=None):
    directory = Path(limits['filesystem'])
    wire.need(directory.is_dir() and directory.stat().st_dev == limits['device'] == Path(tempfile.gettempdir()).stat().st_dev,
        'CAPACITY_FILESYSTEM', '$', 'frozen output and temporary directory must share the declared filesystem')
    if sink is not None:
        target = sink.sink if isinstance(sink, stream.TextSink) else sink
        try:
            status = os.fstat(target.fileno())
        except (AttributeError, OSError, ValueError):
            wire.need(False, 'CAPACITY_FILESYSTEM', '$', 'summary sink must be a regular file on the declared filesystem')
        wire.need(stat.S_ISREG(status.st_mode) and status.st_dev == limits['device'],
            'CAPACITY_FILESYSTEM', '$', 'summary sink must be a regular file on the declared filesystem')
    status = os.statvfs(directory)
    return min(limits['frozen_available_bytes'], status.f_bavail * status.f_frsize)


def _gate(certificate, sink=None):
    bounds, limits = certificate['bounds'], certificate['limits']
    wire.need(all(bounds['result_bytes'] <= limit for limit in limits['output_limits_bytes']),
        'SUMMARY_OUTPUT_CAPACITY', '$', 'certified canonical output exceeds a frozen byte limit')
    wire.need(bounds['peak_additional_disk_bytes'] <= _available(limits, sink),
        'SUMMARY_DISK_CAPACITY', '$', 'certified retained outputs, spools, artifacts and reserve exceed available disk')


def certify(model, request, limits):
    certificate = _proof(model, request, limits)
    _gate(certificate)
    return certificate


def verify(model, request, certificate, *, sink=None):
    wire.need(type(certificate) is dict and type(certificate.get('limits')) is dict,
        'CAPACITY_CERTIFICATE', '$', 'mandatory admission-bound summary certificate required')
    expected = _proof(model, request, certificate['limits'])
    wire.need(formulas.encoded(certificate) == formulas.encoded(expected), 'CAPACITY_CERTIFICATE', '$',
        'exact current input, schema, source, limits and bound binding required')
    _gate(expected, sink)
    return expected


def validate_receipt(model, request, receipt):
    wire.need(type(receipt) is dict and set(receipt) == {'output_identity', 'bytes_written', 'sha256', 'capacity', 'receipt_identity'},
        'CAPACITY_RECEIPT', '$', 'closed summary completion receipt required')
    for key in ('output_identity', 'sha256', 'receipt_identity'):
        wire.need(type(receipt[key]) is str and len(receipt[key]) == 64 and set(receipt[key]) <= set('0123456789abcdef'),
            'CAPACITY_RECEIPT', '$', 'canonical SHA-256 identity required')
    certificate = receipt['capacity']
    wire.need(type(certificate) is dict and type(certificate.get('limits')) is dict,
        'CAPACITY_RECEIPT', '$', 'bound capacity certificate required')
    expected = _proof(model, request, certificate['limits'])
    wire.need(formulas.encoded(certificate) == formulas.encoded(expected), 'CAPACITY_RECEIPT', '$', 'exact capacity binding required')
    wire.need(type(receipt['bytes_written']) is int and 0 <= receipt['bytes_written'] <= expected['bounds']['result_bytes'],
        'CAPACITY_RECEIPT', '$', 'certified canonical byte count required')
    wire.need(formulas.identity({key: value for key, value in receipt.items() if key != 'receipt_identity'}) == receipt['receipt_identity'],
        'CAPACITY_RECEIPT', '$', 'complete result and capacity receipt identity required')

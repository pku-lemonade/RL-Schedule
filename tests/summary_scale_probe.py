"""Public invented structural workload; no captured input or private file reads."""
from copy import deepcopy
import argparse
import itertools
import json
import os
from pathlib import Path
import resource
import sys
import time

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'simulator_detailed/moe_model')]
import causal_full_analysis as full
import causal_full_fixtures as fixtures
import causal_full_capacity as capacity
import design_space as api


def specification(clocks=4):
    spec = fixtures.model()
    spec['families'].extend(dict(id='public-family-' + str(index), law='inverse_partition_response',
        authority='caller_hypothesis') for index in range(2, 26))
    original = deepcopy(spec)
    for index in range(1, clocks):
        suffix = '-public-' + str(index)
        clock = 'clock-a' + suffix
        spec['clocks'].append(dict(original['clocks'][0], id=clock, rank=index))
        for action in original['join']['actions']:
            spec['join']['actions'].append(dict(action, id=action['id'] + suffix, clock=clock))
        for binding in original['join']['bindings']:
            spec['join']['bindings'].append(dict(binding, action=binding['action'] + suffix,
                identity=None if binding['identity'] is None else binding['identity'] + suffix))
        for atom in original['partition']['atoms']:
            spec['partition']['atoms'].append(dict(atom, id=atom['id'] + suffix, clock=clock,
                active_actions=[name + suffix for name in atom['active_actions']], accounting_owner=atom['accounting_owner'] + suffix))
        spec['composition']['participants'].append(dict(original['composition']['participants'][0], clock=clock))
        spec['composition']['peak_participants'].append(clock)
        spec['peak_domain']['participants'].append(clock)
    spec['peak']['domain'] = deepcopy(spec['peak_domain'])
    for clock_index, clock in enumerate(spec['clocks']):
        for atom in spec['partition']['atoms']:
            if atom['clock'] != clock['id']:
                continue
            for field in ('family_mask_conservative', 'family_mask_inclusive'):
                mask = atom[field]
                atom[field] = sum(1 << ((index + clock_index) % 4) for index in range(4) if mask & (1 << index)) if mask else 1 << (4 + clock_index)
    policies = [('priority', list(order) + list(range(4, 26))) for order in itertools.permutations(range(4))]
    policies.extend([('shared_serial', None), ('co_bottleneck', None)])
    spec['scenarios'] = [dict(id='public-scenario-' + str(index), membership=membership,
        sharing=sharing, priority=priority, exposure_mask=mask)
        for index, (membership, (sharing, priority), mask) in enumerate(itertools.product(
            ('inclusive', 'conservative'), policies, range(16)))]
    spec['capacity']['records'] = dict.fromkeys(spec['capacity']['records'], 1000000)
    spec['capacity']['validation_work'] = 10**12
    return spec


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--width', type=int, default=7)
    parser.add_argument('--clocks', type=int, default=4)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    started_cpu, started_wall = time.process_time(), time.monotonic()
    phases = []
    def record(name):
        phases.append(dict(phase=name, cpu_seconds=time.process_time() - started_cpu,
            wall_seconds=time.monotonic() - started_wall, max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))
        print(json.dumps(phases[-1]), flush=True)
    for name in ('_comparisons', '_build_result', 'validate_result'):
        original = getattr(full, name)
        def measured(*args, original=original, name=name, **kwargs):
            record(name + '_begin')
            value = original(*args, **kwargs)
            record(name + '_end')
            return value
        setattr(full, name, measured)
    model = api.DesignSpace(specification(arguments.clocks), admission_only=True)
    request = fixtures.request(model)
    request['version'] = model.template(output_detail='summary')['version']
    request['axes'] = [dict(id=row['id'], family=row['id'], unit='dimensionless_service_scale',
        domain=['1', str(arguments.width)], grid=[str(index) for index in range(1, arguments.width + 1)])
        for row in model._specification['families'][:4]]
    request['surfaces'] = [dict(id='public-curve-' + str(index), axes=axes,
        domain=['1', str(arguments.width)], extrapolation='inverse_scale_to_infinity')
        for index, axes in enumerate([[axis['id']] for axis in request['axes']] + [[axis['id'] for axis in request['axes']]])]
    request['budget'] = dict.fromkeys(request['budget'], 10**30)
    directory = arguments.output.parent.resolve()
    limits = dict(version=capacity.LIMIT_VERSION, filesystem=str(directory), device=directory.stat().st_dev,
        frozen_available_bytes=os.statvfs(directory).f_bavail * os.statvfs(directory).f_frsize,
        output_limits_bytes=[], reserve_bytes=capacity.RESERVE, logs_and_gate_bytes=1048576, additional_temporary_bytes=0)
    certificate = model.summary_capacity(request, limits)
    record('admission_capacity')
    output = arguments.output.with_suffix('.stream')
    with output.open('w+b') as sink:
        receipt = model.evaluate_to(request, sink, capacity=certificate)
    record('complete')
    capacity.validate_receipt(model, request, receipt)
    evidence = dict(public_synthetic_only=True, width=arguments.width, clocks=arguments.clocks,
        designs=arguments.width**4, scenarios=len(model._specification['scenarios']),
        directed_pairs=arguments.width**4 * (arguments.width**4 - 1), phases=phases, receipt=receipt,
        limits=dict(cpu_seconds=480, wall_seconds=600, address_space_bytes=16 << 30))
    arguments.output.write_text(json.dumps(evidence, sort_keys=True, indent=2) + '\n')
    output.unlink()


if __name__ == '__main__':
    main()

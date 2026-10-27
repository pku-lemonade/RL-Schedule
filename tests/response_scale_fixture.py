"""Public seeded masks and durations, unrelated to captured workloads."""
from copy import deepcopy
import random

from summary_scale_probe import specification as low_entropy


def specification(clocks=4, scenarios=832, seed=177):
    spec = low_entropy(clocks)
    randomizer = random.Random(seed)
    for clock_index, clock in enumerate(spec['clocks']):
        atoms = [atom for atom in spec['partition']['atoms'] if atom['clock'] == clock['id']]
        boundary = 0
        for atom in atoms:
            duration = randomizer.randrange(101, 997)
            atom.update(interval=[boundary, boundary + duration], duration_ticks=duration)
            boundary += duration
            conservative = randomizer.randrange(1, 1 << 26)
            inclusive = conservative | randomizer.randrange(1, 1 << 26)
            if not atom['active_actions']:
                conservative = inclusive = 1 << (clock_index % 4)
            atom.update(family_mask_conservative=conservative, family_mask_inclusive=inclusive)
        clock['window'] = [0, boundary]
        for action in spec['join']['actions']:
            if action['clock'] == clock['id']:
                covered = [atom['interval'] for atom in atoms if action['id'] in atom['active_actions']]
                action['interval'] = [min(interval[0] for interval in covered), max(interval[1] for interval in covered)]
        participant = spec['composition']['participants'][clock_index]
        participant.update(release_offset_ticks=11 + clock_index, attention_offset_ticks=123,
            baseline_layer_ticks=boundary + 11 + clock_index)
    spec['composition']['baseline_layer_ticks'] = max(row['baseline_layer_ticks'] for row in spec['composition']['participants'])
    spec['scenarios'] = []
    for index in range(scenarios):
        sharing = ('shared_serial', 'priority', 'co_bottleneck')[index % 3]
        priority = list(range(26))
        randomizer.shuffle(priority)
        spec['scenarios'].append(dict(id='entropy-scenario-' + str(index),
            membership=('inclusive', 'conservative')[index % 2], sharing=sharing,
            priority=priority if sharing == 'priority' else None,
            exposure_mask=randomizer.randrange(1 << 26)))
    return deepcopy(spec)


def request(model, width=3):
    result = model.template(output_detail='summary')
    result.update(study_id='public-response-entropy', comparisons=dict(policy='all', pairs=[]),
        axes=[dict(id=row['id'], family=row['id'], unit='dimensionless_service_scale',
            domain=['1', str(width)], grid=[str(index) for index in range(1, width + 1)])
            for row in model._specification['families'][:4]],
        budget=dict.fromkeys(result['budget'], 10**30),
        surfaces=[dict(id='entropy-surface-' + str(index), axes=axes, domain=['1', str(width)],
            extrapolation='inverse_scale_to_infinity') for index, axes in enumerate(
                [[row['id']] for row in model._specification['families'][:4]] +
                [[row['id'] for row in model._specification['families'][:4]]])])
    return result

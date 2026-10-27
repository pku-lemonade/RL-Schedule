"""Invented structural complexity, without captured workloads or padding blobs."""
from copy import deepcopy
import json

import response_scale_fixture as entropy


def specification(scenarios=832, seed=179, sources=2048, splits=24):
    spec = entropy.specification(scenarios=scenarios, seed=seed)
    prototype = spec['source_nodes'][0]
    for index in range(sources):
        name = 'public-setup-source-' + str(index)
        metadata = dict(public_synthetic=True, stage=index % 17,
            alternatives=[dict(route='invented-route-' + str(branch), operations=list(range(branch, branch + 8)),
                constraints=dict(logical_only=True, unknown_cost=None)) for branch in range(5)])
        spec['source_nodes'].append(dict(prototype, id=name, clock=spec['clocks'][index % 4]['id'],
            metadata_json=json.dumps(metadata, sort_keys=True)))
        if index:
            spec['join']['dependencies'].append(dict(id='public-setup-edge-' + str(index),
                before='public-setup-source-' + str(index - 1), after=name, status='unresolved',
                basis='source_protocol', identity=None, unknown='invented-logical-relation-no-timing-credit'))
    atoms = []
    for atom in spec['partition']['atoms']:
        begin, end = atom['interval']
        pieces = min(splits, end - begin)
        for index in range(pieces):
            left = begin + (end - begin) * index // pieces
            right = begin + (end - begin) * (index + 1) // pieces
            atoms.append(dict(deepcopy(atom), id=atom['id'] + '-piece-' + str(index),
                accounting_owner=atom['accounting_owner'] + '-piece-' + str(index),
                interval=[left, right], duration_ticks=right - left))
    spec['partition']['atoms'] = atoms
    spec['capacity']['records'] = dict.fromkeys(spec['capacity']['records'], 10**30)
    spec['capacity']['validation_work'] = 10**30
    return spec


request = entropy.request

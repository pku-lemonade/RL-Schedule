"""Invented complete sweeps through the public sink, bounded by 256 MiB AS."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import resource
import sys
import tracemalloc
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'simulator_detailed/moe_model'))
import causal_full_analysis as full
import causal_full_stream as stream
import causal_full_fixtures as fixtures
import design_space as api


class Points:
    def __init__(self, designs, scenarios):
        self.designs, self.scenarios = designs, scenarios

    def __len__(self):
        return self.designs

    def __getitem__(self, index):
        return dict(index=index, rates=[full.rational(index + 1)],
            scenarios=(dict(id='probe-' + str(scenario), latency_ns=full.rational((index + scenario) % 7 + 1))
                for scenario in range(self.scenarios)))


def comparison_phase(designs, scenarios, block_size):
    properties = full.result_schema()['properties']
    tracemalloc.start()
    with stream.ArraySpool(1, properties['comparisons']['items'], full._check_rationals) as comparisons, \
            stream.ArraySpool(2, properties['pareto']['properties']['edges']['items'], full._check_rationals) as edges:
        pareto = full._comparisons(Points(designs, scenarios), dict(policy='all', pairs=[]), comparisons, edges, block_size)
        comparisons.finish()
        edges.finish()
        result = dict(comparisons=comparisons, pareto=pareto)
        identity = stream.identity(result)
        sink = Sink()
        for data in stream.chunks(result):
            stream.write_all(sink, data)
        assert identity == sink.digest.hexdigest()
        assert comparisons.count == edges.count == designs * (designs - 1)
    remaining, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 1000000 + block_size * 30000 + designs * 512
    return dict(peak_traced_bytes=peak, remaining_traced_bytes=remaining,
        bound_bytes=1000000 + block_size * 30000 + designs * 512,
        canonical_sha256=identity, bytes=sink.bytes)


class Sink:
    def __init__(self):
        self.digest = hashlib.sha256()
        self.bytes = 0

    def write(self, data):
        self.digest.update(data)
        self.bytes += len(data)
        return len(data)


def run(designs, scenarios, block_size):
    spec = fixtures.model()
    spec['scenarios'] = [dict(deepcopy(spec['scenarios'][index % 5]), id='probe-' + str(index)) for index in range(scenarios)]
    spec['capacity']['records']['scenarios'] = scenarios
    spec['capacity']['records']['priority_entries'] = scenarios * 2
    model = api.DesignSpace(spec)
    request = fixtures.request(model)
    request['surfaces'] = []
    request['axes'][0].update(domain=['1', str(designs)], grid=[str(index + 1) for index in range(designs)])
    request['axes'] = request['axes'][:1]
    request['budget'] = full._prepare(model._specification, model._timing, request)[1]
    counts = dict(comparisons=0, deltas=0, edges=0, maximum_block=0, passes=0)
    producer, append, chunks = full._comparison_blocks, stream.ArraySpool.append, stream.ArraySpool.chunks
    def blocks(*args, **kwargs):
        for block in producer(*args, **kwargs):
            counts['maximum_block'] = max(counts['maximum_block'], len(block))
            yield block
    def record(spool, item):
        if spool.depth == 1:
            counts['comparisons'] += 1
            counts['deltas'] += item['deltas'].count
        elif spool.depth == 2:
            counts['edges'] += 1
        return append(spool, item)
    def consume(spool):
        if spool.depth in (1, 2):
            counts['passes'] += 1
        yield from chunks(spool)
    sink = Sink()
    tracemalloc.start()
    with patch.object(full, '_comparison_blocks', blocks), patch.object(stream.ArraySpool, 'append', record), \
            patch.object(stream.ArraySpool, 'chunks', consume), patch.object(full, '_scenario', wraps=full._scenario) as scenario:
        receipt = model.evaluate_to(request, sink, block_size=block_size)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert counts['comparisons'] == counts['edges'] == designs * (designs - 1)
    assert counts['deltas'] == designs * (designs - 1) * scenarios
    assert counts['maximum_block'] <= block_size and counts['passes'] == 2
    assert scenario.call_count == designs * scenarios
    assert request['budget']['comparison_evaluations'] == counts['deltas'] * 2
    assert receipt['bytes_written'] == sink.bytes and receipt['sha256'] == sink.digest.hexdigest()
    assert peak < 1000000 + designs * scenarios * 6000 + block_size * scenarios * 3000 + designs * 10000
    return dict(designs=designs, scenarios=scenarios, block_size=block_size, counts=counts,
        peak_traced_bytes=peak, remaining_traced_bytes=current, work=request['budget'], receipt=receipt,
        comparison_only=comparison_phase(designs, scenarios, block_size))


if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    cases = [run(designs, scenarios, block) for designs, scenarios, block in
        ((16, 8, 1), (32, 8, 1), (64, 8, 1), (16, 32, 1), (16, 128, 1), (17, 31, 7))]
    print(json.dumps(dict(bounded=True, address_space_bytes=256 * 1024 * 1024, cases=cases,
        max_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss), sort_keys=True))

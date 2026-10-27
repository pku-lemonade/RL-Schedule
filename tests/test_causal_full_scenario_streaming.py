"""Independent live-record probes: spool bytes are not temporary Python records."""
from copy import deepcopy
from fractions import Fraction
import gc
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import weakref

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'simulator_detailed/moe_model'))
import causal_full_analysis as full
import causal_full_stream as stream
import causal_full_fixtures as fixtures
import causal_full_materialized_oracle as oracle
import counterfactual as formulas
import design_space as api
import design_space_schema as wire

EVIDENCE = dict(live_records=[], online_emission=None)


class Points:
    def __init__(self, scenarios, progress=None):
        self.scenarios = scenarios
        self.progress = progress
        self.reads = 0

    def __len__(self):
        return 19

    def __getitem__(self, index):
        def rows():
            for scenario in range(self.scenarios):
                if self.progress is not None and self.progress['emitted'] % self.scenarios < scenario:
                    raise AssertionError('next scenario requested before previous delta reached file sink')
                self.reads += 1
                yield dict(id='scenario-' + str(scenario),
                    latency_ns=dict(numerator=17 + (index + scenario) % 11, denominator=7))
        return dict(index=index, rates=[dict(numerator=index + 1, denominator=3)], scenarios=rows())


class HashSink:
    def __init__(self):
        self.digest = hashlib.sha256()
        self.count = 0

    def write(self, data):
        self.digest.update(data)
        self.count += len(data)
        return len(data)


def schemas():
    paired = full.result_schema()['properties']['comparisons']['items']
    edge = full.result_schema()['properties']['pareto']['properties']['edges']['items']
    return paired, edge, paired['properties']['deltas']['items']


def retained_delta_rows(value):
    if isinstance(value, dict):
        return int(set(value) == {'scenario', 'delta_ns'}) + sum(retained_delta_rows(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return sum(retained_delta_rows(child) for child in value)
    return 0


class ScenarioStreamingTests(unittest.TestCase):
    def measure(self, scenarios, block_size):
        counts = dict(live=0, maximum_live=0, constructed=0, emitted=0, opens=0, maximum_open=0,
            row_sink_maximum=0, maximum_chunk=0)
        rational, append = full.rational, stream.ArraySpool.append
        initialize, close = stream.ArraySpool.__init__, stream.ArraySpool.__exit__
        chunks = stream.ArraySpool.chunks
        class TrackedRational(dict):
            pass
        def release():
            counts['live'] -= 1
        def tracked(value):
            result = TrackedRational(rational(value))
            counts['live'] += 1
            counts['constructed'] += 1
            counts['maximum_live'] = max(counts['maximum_live'], counts['live'])
            weakref.finalize(result, release)
            return result
        def observe(spool, item):
            if spool.depth == 3:
                counts['row_sink_maximum'] = max(counts['row_sink_maximum'], counts['live'])
                item = dict(item, delta_ns=dict(item['delta_ns']))
                counts['emitted'] += 1
            elif spool.depth == 1:
                item = dict(item, delta_ns=[dict(value) for value in item['delta_ns']])
            return append(spool, item)
        def opened(spool, *args, **kwargs):
            initialize(spool, *args, **kwargs)
            counts['opens'] += 1
            counts['maximum_open'] = max(counts['maximum_open'], counts['opens'])
        def closed(spool, *args):
            try:
                return close(spool, *args)
            finally:
                counts['opens'] -= 1
        def bounded_read(spool):
            for data in chunks(spool):
                counts['maximum_chunk'] = max(counts['maximum_chunk'], len(data))
                self.assertLessEqual(len(data), stream.CHUNK_BYTES)
                yield data
        paired_schema, edge_schema, delta_schema = schemas()
        points = Points(scenarios)
        with patch.object(full, 'rational', tracked), patch.object(stream.ArraySpool, 'append', observe), \
                patch.object(stream.ArraySpool, '__init__', opened), patch.object(stream.ArraySpool, '__exit__', closed), \
                patch.object(stream.ArraySpool, 'chunks', bounded_read):
            producer = full._comparison_blocks(points, dict(policy='all', pairs=[]), block_size)
            try:
                block = next(producer)
                self.assertEqual(retained_delta_rows(block), 0)
                self.assertTrue(all(isinstance(comparison['deltas'], stream.ArraySpool) for comparison, edge in block))
                self.assertEqual(counts['live'], 2 * block_size)
                delta_spool_bytes = sum(comparison['deltas'].file.seek(0, 2) for comparison, edge in block)
                with stream.ArraySpool(1, paired_schema, full._check_rationals) as comparisons, \
                        stream.ArraySpool(2, edge_schema, full._check_rationals) as edges:
                    for comparison, edge in block:
                        comparisons.append(comparison)
                        edges.append(edge)
                    del comparison, edge
                    comparisons.finish()
                    edges.finish()
                    result = dict(comparisons=comparisons, pareto=dict(edges=edges))
                    identity = stream.identity(result)
                    self.assertEqual(stream.identity(result), identity)
                    sink = HashSink()
                    for data in stream.chunks(result):
                        stream.write_all(sink, data)
                    self.assertEqual(sink.digest.hexdigest(), identity)
            finally:
                producer.close()
            del block, producer
        gc.collect()
        self.assertEqual(counts['live'], 0)
        self.assertEqual(counts['opens'], 0)
        self.assertEqual(counts['emitted'], scenarios * block_size)
        self.assertEqual(counts['constructed'], block_size * (scenarios + 2))
        self.assertEqual(counts['maximum_live'], 2 * block_size)
        self.assertEqual(counts['row_sink_maximum'], 2 * (block_size - 1) + 1)
        self.assertEqual(counts['maximum_open'], block_size + 2)
        self.assertEqual(points.reads, 2 * block_size * scenarios)
        return dict(designs=19, scenarios=scenarios, block_size=block_size, counts=counts,
            retained_delta_rows=0, delta_spool_bytes=delta_spool_bytes, final_bytes=sink.count,
            canonical_sha256=identity, scenario_reads=points.reads)

    def test_live_records_are_scenario_independent_through_consumers(self):
        for block_size in (1, 3):
            measurements = [self.measure(scenarios, block_size) for scenarios in (8, 64, 512, 4096)]
            EVIDENCE['live_records'].extend(measurements)
            self.assertEqual({row['counts']['maximum_live'] for row in measurements}, {2 * block_size})
            self.assertEqual({row['counts']['maximum_open'] for row in measurements}, {block_size + 2})
            sizes = [row['delta_spool_bytes'] for row in measurements]
            self.assertEqual(sizes, sorted(set(sizes)))

    def test_delta_reaches_validated_file_sink_before_next_scenario(self):
        progress = dict(emitted=0)
        points = Points(128, progress)
        append = stream.ArraySpool.append
        def observe(spool, item):
            before = spool.file.tell()
            result = append(spool, item)
            if spool.depth == 3:
                self.assertGreater(spool.file.tell(), before)
                progress['emitted'] += 1
            return result
        with patch.object(stream.ArraySpool, 'append', observe):
            producer = full._comparison_blocks(points, dict(policy='all', pairs=[]), 3, reverse=True)
            try:
                block = next(producer)
                self.assertEqual(retained_delta_rows(block), 0)
                self.assertEqual(progress['emitted'], 384)
            finally:
                producer.close()
        EVIDENCE['online_emission'] = dict(scenarios=128, pairs=3, rows_written=384,
            next_scenario_guard=True, reverse_schedule=True)

    def test_small_oracle_bytes_with_varied_scenario_counts(self):
        for scenarios in (1, 8, 64):
            spec = fixtures.model()
            original = spec['scenarios']
            spec['scenarios'] = [dict(deepcopy(original[index % len(original)]), id='case-' + str(index)) for index in range(scenarios)]
            model = api.DesignSpace(spec)
            request = fixtures.request(model)
            request['surfaces'] = []
            request['axes'] = request['axes'][:1]
            request['axes'][0].update(domain=['0.5', '2'], grid=['0.5', '1', '2'])
            expected = formulas.encoded(oracle.evaluate(model, request))
            self.assertEqual(formulas.encoded(model.evaluate(request)), expected)
            for block_size in (1, 3, 7):
                sink = io.BytesIO()
                receipt = model.evaluate_to(request, sink, block_size=block_size)
                self.assertEqual(sink.getvalue(), expected)
                self.assertEqual(receipt['sha256'], hashlib.sha256(expected).hexdigest())

    def test_pair_segments_close_on_mid_scenario_failure_and_cancel(self):
        initialize, append = stream.ArraySpool.__init__, stream.ArraySpool.append
        opened = []
        emitted = []
        def observe(spool, *args, **kwargs):
            initialize(spool, *args, **kwargs)
            opened.append(spool)
        def fail(spool, item):
            if spool.depth == 3:
                emitted.append(True)
                if len(emitted) == 7:
                    raise OSError('injected delta-spool write failure')
            return append(spool, item)
        model = api.DesignSpace(fixtures.model())
        request = fixtures.request(model)
        sink = HashSink()
        with patch.object(stream.ArraySpool, '__init__', observe), patch.object(stream.ArraySpool, 'append', fail):
            with self.assertRaisesRegex(OSError, 'injected'):
                model.evaluate_to(request, sink, block_size=3)
        self.assertEqual(sink.count, 0)
        self.assertTrue(all(spool.file.closed for spool in opened))
        opened.clear()
        with patch.object(stream.ArraySpool, '__init__', observe):
            producer = full._comparison_blocks(Points(512), dict(policy='all', pairs=[]), 3)
            next(producer)
            producer.close()
        self.assertEqual(len(opened), 3)
        self.assertTrue(all(spool.file.closed for spool in opened))

    def test_delta_segments_and_envelopes_use_existing_validation(self):
        paired_schema, edge_schema, delta_schema = schemas()
        for mutation, code in ((lambda row: row.update(extra=1), 'SCHEMA_FIELDS'),
                (lambda row: row.update(scenario=None), 'SCHEMA_STRING'),
                (lambda row: row.update(delta_ns=dict(numerator=2, denominator=2)), 'OUTPUT_RATIONAL')):
            with stream.ArraySpool(3, delta_schema, full._check_rationals) as deltas:
                row = dict(scenario='case', delta_ns=full.rational(Fraction(1, 3)))
                mutation(row)
                with self.assertRaises(wire.Rejected) as caught:
                    deltas.append(row)
                self.assertEqual(caught.exception.decision['code'], code)
                self.assertEqual(deltas.file.tell(), 0)
        points = Points(2)
        producer = full._comparison_blocks(points, dict(policy='all', pairs=[]))
        try:
            comparison, edge = next(producer)[0]
            with stream.ArraySpool(1, paired_schema, full._check_rationals) as output:
                with self.assertRaises(wire.Rejected) as caught:
                    output.append(dict(comparison, extra=1))
                self.assertEqual(caught.exception.decision['code'], 'SCHEMA_FIELDS')
                self.assertEqual(output.file.tell(), 0)
        finally:
            producer.close()

    def test_empty_unfinished_or_wrong_schema_segments_reject(self):
        paired_schema, edge_schema, delta_schema = schemas()
        for state in ('unfinished', 'empty', 'wrong_schema'):
            with stream.ArraySpool(3, delta_schema, full._check_rationals) as deltas, \
                    stream.ArraySpool(1, paired_schema, full._check_rationals) as output:
                if state != 'empty':
                    deltas.append(dict(scenario='case', delta_ns=full.rational(0)))
                if state != 'unfinished':
                    deltas.finish()
                if state == 'wrong_schema':
                    deltas.item_schema = edge_schema
                item = dict(left=0, right=1, delta_ns=[full.rational(0), full.rational(0)],
                    faster=0, equal=1, slower=0, deltas=deltas)
                with self.assertRaises(wire.Rejected):
                    output.append(item)
                self.assertEqual(output.file.tell(), 0)


if __name__ == '__main__':
    unittest.main()

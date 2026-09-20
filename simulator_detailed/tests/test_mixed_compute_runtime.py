"""Causal two-round mixed compute on the real shared session."""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from simulator_detailed.mixed_compute_runtime import (
    LocalOperationCapability,
    MixedComputeComponent,
)
from simulator_detailed.multicast_memory_runtime import MulticastMemoryRuntime
from simulator_detailed.tests.test_multicast_inventory import (
    compile_document,
    scalar_document,
)
from simulator_detailed.tests.test_multicast_memory_runtime import time_of


def pipeline_document():
    document, graph = scalar_document()
    template = json.loads(Path('simulator_detailed/configs/compute_workloads/generic_matmul.json').read_text())
    for endpoint in document['memory']['endpoints']:
        endpoint['roles'] = ['initiator', 'target', 'response_sink']
    for buffer in document['memory']['buffers']:
        if buffer['buffer_id'].startswith('b-'):
            buffer['initially_ready'] = True
    broadcast = document['writes'][0]
    broadcast.update(operation_id='distribute0', completion='write_posted', size_bytes=32)
    broadcast['source']['size_bytes'] = 32
    broadcast['rectangle'].update(start={'x': 0, 'y': 1}, end={'x': 1, 'y': 1}, include_source=False, major_axis='y')
    broadcast['destinations'] = [d for d in broadcast['destinations'] if d['endpoint_id'] in {'ep-t0_1', 'ep-t1_1'}]
    second = copy.deepcopy(broadcast)
    second['operation_id'] = 'distribute1'
    document['writes'].append(second)
    document['gates'] = [{'operation_id': 'distribute1', 'after_waits': ['collect0']}]
    document['increments'], document['waits'] = [], []
    compute = {'dtypes': template['dtypes'], 'rates': template['rates'], 'workers': [], 'streams': []}
    compute['rates'][0]['work_per_native_cycle'] = 8
    operation = template['streams'][0]['jobs'][0]['operation']
    operation['a']['shape'] = operation['c']['shape'] = [1, 4, 4]
    operation['b']['shape'] = [4, 4]
    for worker_index, tile in enumerate(('t0_1', 't1_1')):
        worker = copy.deepcopy(template['workers'][0])
        worker.update(tile_id=tile, l1_resource_id=f'r-{tile}', endpoint_ids=[f'ep-{tile}'])
        compute['workers'].append(worker)
        def extent(offset, tile=tile):
            return {'buffer_id': f'b-ep-{tile}', 'offset_bytes': offset, 'size_bytes': 32}
        slot = {'slot_id': f'slot-{tile}', 'a': extent(128), 'b': extent(256), 'c': extent(384)}
        stream = {'stream_id': f'stream-{tile}', 'worker_tile_id': tile, 'slots': [slot], 'jobs': []}
        for round_index in range(2):
            job_id = f'job-{worker_index}-{round_index}'
            output = {'mode': 'local', 'destination': extent(384)} if worker_index == 0 else {
                'mode': 'write_acknowledged', 'fabric_id': 0,
                'destination': {'buffer_id': 'b-ep-t0_0', 'offset_bytes': 640, 'size_bytes': 32}}
            stream['jobs'].append({'job_id': job_id, 'operation': copy.deepcopy(operation),
                'a': {'access': extent(128), 'version': {'kind': 'producer', 'producer_id': f'distribute{round_index}'}},
                'b': {'access': extent(256), 'version': {'kind': 'initial'}}, 'output': output})
            document['increments'].append({'operation_id': f'signal-{worker_index}-{round_index}', 'source_endpoint_id': f'ep-{tile}',
                'fabric_id': 0, 'counter_id': 'count', 'completion': 'atomic_posted', 'depends_on': [job_id]})
        compute['streams'].append(stream)
    for round_index in range(2):
        document['waits'].append({'wait_id': f'collect{round_index}', 'endpoint_id': 'ep-t0_0', 'counter_id': 'count',
            'threshold': 2 * (round_index + 1), 'producer_operations': [f'signal-{w}-{r}' for r in range(round_index + 1) for w in range(2)]})
    document['compute'] = compute
    document['operations'] = [{'operation_id': 'ordinary', 'initiator_id': 'ep-t0_0', 'fabric_id': 0, 'kind': 'write_acknowledged',
        'source': {'buffer_id': 'b-ep-t0_0', 'offset_bytes': 32, 'size_bytes': 32},
        'destination': {'buffer_id': 'b-ep-t1_1', 'offset_bytes': 512, 'size_bytes': 32}}]
    return document, graph


class MixedComputeTests(unittest.TestCase):
    def test_two_rounds_slot_reuse_shared_service_and_causal_collection(self):
        doc, graph = pipeline_document()
        plan = compile_document(doc, graph)
        runtime = MulticastMemoryRuntime(plan)
        result = runtime.run()
        self.assertEqual(result.status, 'complete')
        self.assertEqual(result.counters[0].value, 4)
        self.assertGreaterEqual(time_of(result, 'distribute1', 'acceptance', segment=0), time_of(result, 'collect0', 'wait_release'))
        self.assertEqual((result.source_useful_bytes, result.destination_useful_bytes), (416, 352))
        compute = result.compute
        self.assertFalse(compute.pending)
        self.assertTrue(all(s.slots[0].generation == 2 and not s.occupied for s in compute.slots))
        for job in plan.record.compute.jobs:
            stages = {e.action: e.time_aci_cycles for e in compute.stages if e.job_id == job.job_id}
            self.assertEqual(stages['math_end'] - stages['math_start'], job.cost.service_aci_cycles)
            self.assertGreaterEqual(time_of(result, job.job_id, 'complete'), stages['output_ready'])
            worker, round_index = job.job_id.split('-')[1:]
            self.assertGreaterEqual(time_of(result, f'signal-{worker}-{round_index}', 'acceptance', segment=0), stages['writer_complete'])
        # Two independent physical workers overlap real arithmetic.
        math = {j.job_id: {e.action: e.time_aci_cycles for e in compute.stages if e.job_id == j.job_id} for j in plan.record.compute.jobs}
        self.assertLess(max(math['job-0-0']['math_start'], math['job-1-0']['math_start']), min(math['job-0-0']['math_end'], math['job-1-0']['math_end']))
        self.assertEqual(len(runtime.memory.resources), len(doc['memory']['resources']))

    def test_resume_retains_compute_slot_and_no_duplicate_capacity(self):
        doc, graph = pipeline_document()
        plan = compile_document(doc, graph)
        expected = MulticastMemoryRuntime(plan).run()
        start = next(e.time_aci_cycles for e in expected.compute.stages if e.action == 'math_start')
        runtime = MulticastMemoryRuntime(plan)
        partial = runtime.run(max_aci_cycles=start + 1)
        self.assertEqual(partial.status, 'incomplete')
        self.assertTrue(any(s.occupied for s in partial.compute.slots))
        self.assertTrue(any(r.occupied for r in partial.compute.resources))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            MixedComputeComponent(runtime, plan.record.compute)
        with self.assertRaisesRegex(ValueError, 'remote'):
            runtime.local_operation('r-t0_1', 'distribute0')
        with self.assertRaisesRegex(ValueError, 'forged'):
            runtime.local_completion(LocalOperationCapability('job-0-0', 'r-t0_1'))
        self.assertEqual(runtime.run(), expected)
        self.assertIs(runtime.finalize(), runtime.finalize())

    def test_configured_shared_worker_contexts_across_streams(self):
        doc, graph = pipeline_document()
        doc.update(writes=[], counters=[], increments=[], waits=[], gates=[], operations=[])
        compute = doc['compute']
        compute['workers'] = compute['workers'][:1]
        first = compute['streams'][0]
        first['jobs'] = first['jobs'][:1]
        for name in ('a', 'b'):
            first['jobs'][0][name]['version'] = {'kind': 'initial'}
        second = copy.deepcopy(first)
        second['stream_id'] = 'second-stream'
        second['slots'][0]['slot_id'] = 'second-slot'
        second['jobs'][0]['job_id'] = 'second-job'
        for access in (second['slots'][0]['a'], second['slots'][0]['b'], second['slots'][0]['c'],
                       second['jobs'][0]['a']['access'], second['jobs'][0]['b']['access'], second['jobs'][0]['output']['destination']):
            access['offset_bytes'] += 32
        compute['streams'] = [first, second]
        serial = MulticastMemoryRuntime(compile_document(doc, graph)).run()
        compute['workers'][0]['compute_contexts'] = 2
        overlap = MulticastMemoryRuntime(compile_document(doc, graph)).run()
        self.assertLess(overlap.elapsed_aci_cycles, serial.elapsed_aci_cycles)
        self.assertEqual([r.peak_occupied for r in serial.compute.resources if r.kind == 'compute'], [1])
        self.assertEqual([r.peak_occupied for r in overlap.compute.resources if r.kind == 'compute'], [2])
        self.assertEqual(len(overlap.compute.resources), 3)

    def test_published_profile_opposite_and_slow_clock_fixtures_resume(self):
        root = Path('simulator_detailed/configs/multicast_workloads')
        for name, cycles, physical in (('mixed_two_rounds.json', 154, 2464), ('mixed_slow_clock.json', 500, 2464),
                ('mixed_opposite_fabric.json', 137, 2080), ('wormhole_b0_mixed_assumed.json', 287, 6912)):
            with self.subTest(name=name):
                doc = json.loads((root / name).read_text())
                source = doc['memory']['source']
                graph = json.loads((root / source.get('graph_path', source.get('profile_path'))).read_text())
                plan = compile_document(doc, graph)
                expected = MulticastMemoryRuntime(plan).run()
                self.assertEqual((expected.status, expected.elapsed_aci_cycles, expected.physical_channel_bytes), ('complete', cycles, physical))
                runtime = MulticastMemoryRuntime(plan)
                for horizon in (1, 15, 35, 60, 80):
                    early = runtime.run(max_aci_cycles=horizon)
                    self.assertLessEqual(early.elapsed_aci_cycles, horizon)
                    self.assertFalse(early.teardown_complete)
                self.assertEqual(runtime.run(), expected)

    def test_phase_stale_generation_and_slot_cycles_rejected_before_allocation(self):
        for failure in ('phase', 'stale', 'cycle', 'remote', 'capacity'):
            doc, graph = pipeline_document()
            if failure == 'phase': doc['gates'] = []
            if failure == 'stale': doc['compute']['streams'][0]['jobs'][1]['a']['version']['producer_id'] = 'distribute0'
            if failure == 'cycle': doc['compute']['streams'][0]['jobs'][0]['depends_on'] = ['job-0-1']
            if failure == 'remote': doc['compute']['streams'][0]['jobs'][0]['a']['access']['buffer_id'] = 'b-ep-t1_1'
            if failure == 'capacity': doc['compute']['workers'].append(copy.deepcopy(doc['compute']['workers'][0]))
            with self.subTest(failure=failure), patch('simpy.Environment', side_effect=AssertionError('allocated')), self.assertRaises(ValueError):
                compile_document(doc, graph)


if __name__ == '__main__': unittest.main()

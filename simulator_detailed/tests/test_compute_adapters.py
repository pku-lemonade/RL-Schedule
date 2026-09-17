"""Explicit FC provenance, immutable import and existing consumer boundaries."""

from __future__ import annotations

import copy
import json
import math
import pickle
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import simpy

from simulator_detailed.compute_adapters import (
    LegacyFCSidecar,
    LegacyFCSnapshot,
    import_legacy_fc,
)
from simulator_detailed.compute_memory import ComputeMemoryPlan
from simulator_detailed.compute_runtime import ComputeOverlapRuntime
from simulator_detailed.configs.schemas.arch_config import ArchConfig
from simulator_detailed.configs.schemas.compute_workload import ComputeWorkload
from simulator_detailed.embedding.hw_encoder import build_hardware_graph
from simulator_detailed.predictor.predict import detect
from simulator_detailed.tests.test_compute_buffers import lower
from simulator_detailed.tests.test_compute_contracts import compute_documents
from simulator_detailed.topology import content_digest
from simulator_detailed.topology_compatibility import legacy_event_rows
from simulator_detailed.utils.definitions import DimSlice, OperatorType
from simulator_detailed.utils.dfg import DFG, DFGNode
from simulator_detailed.utils.task import Task


def fc_chain(*, jobs=2, local=False):
    document, graph = compute_documents(jobs=jobs, slots=2, local=local)
    document["rates"][0]["key"]["operation"] = "fc"
    dfg, chains = DFG(), []
    for i, job in enumerate(document["streams"][0]["jobs"]):
        job["operation"].update(kind="fc", flattening="already_flattened_bmk_v1")
        # Explicit reshape: legacy slice axes need not have B/M/K meanings.
        a, b, c = ([DimSlice(start=3, end=3 + math.prod(job["operation"][name]["shape"]))] for name in ("a", "b", "c"))
        first = i * 4
        dfg.add_node(first, OperatorType.LOAD_FEAT, 97, input_size=a)
        dfg.add_node(first + 1, OperatorType.LOAD_WGT, 97, weight_size=b)
        dfg.add_node(first + 2, OperatorType.FC, 97, input_size=a, weight_size=b, output_size=c)
        dfg.add_node(first + 3, OperatorType.STORE, 97, output_size=c)
        dfg.add_edge(first, first + 2)
        dfg.add_edge(first + 1, first + 2)
        dfg.add_edge(first + 2, first + 3)
        chains.append({"job_id": job["job_id"], "load_feat": first, "load_wgt": first + 1, "fc": first + 2, "store": first + 3})
    sidecar = {"kind": "legacy_fc_chain_v1", "schema_version": 1,
               "shape_interpretation": "explicit_element_count_reshape_v1",
               "workers": [{"core_id": 97, "worker_tile_id": "t0_0"}], "chains": chains, "workload": document}
    return dfg, sidecar, graph


class ComputeAdapterTests(unittest.TestCase):
    def test_import_matches_direct_costs_stages_and_all_memory_evidence(self):
        for local in (False, True):
            dfg, document, graph = fc_chain(local=local)
            dfg.nodes[2].received_input = 7
            dfg.nodes[2].ready = True
            original = pickle.dumps(dfg)
            sidecar = LegacyFCSidecar.model_validate(document)
            with patch("simpy.Environment", side_effect=AssertionError("import allocated runtime")):
                imported = import_legacy_fc(dfg, sidecar, graph)
            direct = lower(document["workload"], graph)
            adapted = ComputeMemoryPlan.compile(imported, direct.session.execution.settings)
            self.assertEqual(imported.record.jobs, direct.workload.record.jobs)
            self.assertEqual(imported.graph, direct.workload.graph)
            self.assertNotEqual(imported.record.configuration_sha256, direct.workload.record.configuration_sha256)
            self.assertNotEqual(adapted.plan_sha256, direct.plan_sha256)
            provenance = imported.config.legacy_import
            self.assertEqual(provenance.dfg_sha256, content_digest(LegacyFCSnapshot.capture(dfg).model_dump(mode="json")))
            self.assertEqual(provenance.sidecar_sha256, content_digest(sidecar.model_dump(mode="json")))
            actual = ComputeOverlapRuntime(adapted).run(max_aci_cycles=10000)
            expected = ComputeOverlapRuntime(direct).run(max_aci_cycles=10000)
            self.assertEqual(actual.model_dump(exclude={"plan", "execution_plan_sha256"}),
                             expected.model_dump(exclude={"plan", "execution_plan_sha256"}))
            self.assertEqual(pickle.dumps(dfg), original)
            # Import captures values, not mutable DFG/readiness references.
            dfg.nodes[2].input_size[0].end += 1
            self.assertEqual(imported.revalidate(), imported)
            self.assertEqual(sidecar, LegacyFCSidecar.model_validate_json(sidecar.model_dump_json()))

    def test_missing_or_unmatched_sidecar_metadata_rejected_before_allocation(self):
        dfg, document, graph = fc_chain()
        invalids = []
        for key in ("workers", "chains", "shape_interpretation", "workload"):
            invalid = copy.deepcopy(document)
            del invalid[key]
            invalids.append(invalid)
        for key in ("dtypes", "rates", "workers", "streams", "memory"):
            invalid = copy.deepcopy(document)
            del invalid["workload"][key]
            invalids.append(invalid)
        invalid = copy.deepcopy(document)
        invalid["chains"].pop()
        invalids.append(invalid)
        invalid = copy.deepcopy(document)
        invalid["workers"][0]["worker_tile_id"] = "t1_0"
        invalids.append(invalid)
        invalid = copy.deepcopy(document)
        invalid["chains"][1]["fc"] = 2
        invalids.append(invalid)
        invalid = copy.deepcopy(document)
        invalid["workload"]["streams"][0]["jobs"][0]["depends_on"] = ["job1"]
        invalids.append(invalid)
        with patch("simpy.Environment", side_effect=AssertionError("allocated")):
            for invalid in invalids:
                with self.subTest(document=invalid), self.assertRaises(ValueError):
                    import_legacy_fc(dfg, LegacyFCSidecar.model_validate(invalid), graph)

    def test_every_edge_and_extent_must_belong_to_one_complete_chain(self):
        original, document, graph = fc_chain()
        mutations = (
            lambda d: d.add_edge(0, 6),  # shared input/fanout
            lambda d: d.add_edge(3, 4),  # extra cross-chain edge
            lambda d: d.nodes[2].parent.pop(),  # asymmetric/unmatched load
            lambda d: d.nodes[0].child.append(2),  # duplicate edge
            lambda d: d.nodes[3].child.append(2),  # cycle
            lambda d: setattr(d.nodes[2], "core_id", 0),  # no inferred mesh-id mapping
            lambda d: setattr(d.nodes[1], "weight_size", [DimSlice(start=3, end=4)]),  # partial weights
            lambda d: setattr(d.nodes[2], "output_size", [DimSlice(start=0, end=1)]),
            lambda d: setattr(d.nodes[0], "output_size", [DimSlice(start=0, end=1)]),
            lambda d: setattr(d.nodes[2], "index", 30),
            lambda d: d.add_node(8, OperatorType.LOAD_FEAT, 97),  # ignored work forbidden
        )
        for mutation in mutations:
            dfg = copy.deepcopy(original)
            mutation(dfg)
            before = pickle.dumps(dfg)
            with self.assertRaises(ValueError):
                import_legacy_fc(dfg, LegacyFCSidecar.model_validate(document), graph)
            self.assertEqual(pickle.dumps(dfg), before)
        for operation in (OperatorType.CONV, OperatorType.POOL, OperatorType.SEND, OperatorType.RECV):
            dfg = copy.deepcopy(original)
            dfg.nodes[2].operation = operation
            with self.assertRaises(ValueError):
                import_legacy_fc(dfg, LegacyFCSidecar.model_validate(document), graph)
        # Matching all three edges is insufficient when normalized dimensions
        # invent a different element count or unsupported conversion.
        invalid = copy.deepcopy(document)
        invalid["workload"]["streams"][0]["jobs"][0]["operation"]["a"]["shape"] = [1, 1, 1]
        with self.assertRaisesRegex(ValueError, "element count"):
            import_legacy_fc(original, LegacyFCSidecar.model_validate(invalid), graph)

    def test_legacy_fc_fails_instead_of_completing_without_work(self):
        env = simpy.Environment()
        task = Task(DFGNode(1, OperatorType.FC, 0))
        with self.assertRaisesRegex(NotImplementedError, "legacy_fc_chain_v1"):
            env.run(until=env.process(task.execute(SimpleNamespace(env=env))))
        self.assertEqual(env.now, 0)

    def test_compute_models_documents_and_stages_reject_legacy_consumers(self):
        document, graph = compute_documents()
        config = ComputeWorkload.model_validate(document)
        result = ComputeOverlapRuntime(lower(document, graph)).run(max_aci_cycles=1000)
        for value in (config, document, result, result.model_dump(mode="json"), result.plan,
                      [event.model_dump(mode="json") for event in result.stages]):
            with self.assertRaises(TypeError):
                build_hardware_graph(value)
            with self.assertRaises(TypeError):
                detect(1, 1, value, [], [])
            for compute, communication in ((value, []), ([], value)):
                with self.assertRaises((TypeError, ValueError)):
                    detect(1, 1, ArchConfig(), compute, communication)
            for stream in ("compute", "communication"):
                with self.assertRaises((TypeError, ValueError)):
                    legacy_event_rows(value, stream)

    def test_actual_public_consumer_guards_run_without_optional_model_imports(self):
        script = '''
import importlib.abc
import json
import sys
class NoTensors(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"torch", "torch_geometric"}:
            raise AssertionError("tensor import before admission")
sys.meta_path.insert(0, NoTensors())
from simulator_detailed.configs.schemas.arch_config import ArchConfig
from simulator_detailed.embedding.hw_encoder import build_hardware_graph
from simulator_detailed.predictor.predict import detect
for doc in json.loads(sys.stdin.read()):
    for guard, args in ((build_hardware_graph, (doc,)), (detect, (1, 1, doc, [], [])),
                        (detect, (1, 1, ArchConfig(), doc, [])), (detect, (1, 1, ArchConfig(), [], doc))):
        try:
            guard(*args)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("compute input accepted")
'''
        document, graph = compute_documents()
        result = ComputeOverlapRuntime(lower(document, graph)).run(max_aci_cycles=1000)
        check = subprocess.run([sys.executable, "-c", script], input=json.dumps([document, result.model_dump(mode="json")]),
                               text=True, capture_output=True, check=False)
        self.assertEqual(check.returncode, 0, check.stderr)

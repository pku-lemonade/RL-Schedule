"""Direct compatibility and fault-sensitive, independent observation oracles."""

import copy
import json
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

from simulator_detailed.configs.schemas.validation import (
    ClockDomain,
    Metadata,
    NormalizedObservations,
    TimePoint,
)
from simulator_detailed.replay_compute import run_workload
from simulator_detailed.replay_memory import run_replay as memory_replay
from simulator_detailed.replay_topology import run_replay as topology_replay
from simulator_detailed.tests.validation_fixtures import observations
from simulator_detailed.validation.adapters import admit
from simulator_detailed.validation.audits import UnsupportedAudit, audit
from simulator_detailed.validation.identity import content_digest
from simulator_detailed.validation.matching import functional_match
from simulator_detailed.validation.normalize import converted_seconds, normalize

CONFIGS = Path(__file__).resolve().parents[1] / "configs"


class AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {}
        for name, adapter, path in (
            ("v1", "topology_replay_v1", "replays/heterogeneous_unicast.json"),
            ("torus", "torus_replay_v2", "replays/torus_small_v2.json"),
            ("wormhole", "torus_replay_v2", "replays/wormhole_transport_v2.json"),
            ("memory", "memory_replay_v1", "memory_replays/generic_ordered.json"),
            ("wormhole_memory", "memory_replay_v1", "memory_replays/wormhole_ordered.json"),
            ("incomplete", "memory_replay_v1", "memory_replays/generic_incomplete.json"),
            ("compute", "compute_workload_v1", "compute_workloads/streaming_depth2.json"),
            ("depth1", "compute_workload_v1", "compute_workloads/streaming_depth1.json"),
            ("generic_compute", "compute_workload_v1", "compute_workloads/generic_matmul.json"),
            ("wormhole_compute", "compute_workload_v1", "compute_workloads/wormhole_bf16_matmul.json"),
        ):
            admitted = admit(adapter, CONFIGS / path)
            cls.cases[name] = (admitted, admitted.execute(())[0])

    def test_direct_results_are_unchanged(self):
        for name, run in (("v1", topology_replay), ("torus", topology_replay), ("memory", memory_replay), ("compute", run_workload)):
            admitted, raw = self.cases[name]
            self.assertEqual(raw, run(admitted.path).model_dump(mode="json"))
            self.assertEqual(normalize(admitted, raw).source_result_sha256, content_digest(raw))

    def test_inspection_is_not_execution(self):
        for adapter, path in (("profile_inspection_v1", "profiles/wormhole_b0_n150_assumed.json"), ("topology_inspection_v1", "topologies/memory_small.json")):
            admitted = admit(adapter, CONFIGS / path)
            observation = normalize(admitted, admitted.execute(())[0])
            self.assertEqual(observation.execution, "inspected")
            self.assertFalse(observation.metrics)

    def test_all_applicable_audits_across_geometries_widths_clocks(self):
        count = 0
        for name, (admitted, raw) in self.cases.items():
            normalize(admitted, raw)
            for check in ("routing", "packet_accounting", "memory_service", "ownership", "compute_work", "drain"):
                with self.subTest(case=name, check=check):
                    try:
                        audit(check, admitted, raw)
                        count += 1
                    except UnsupportedAudit:
                        self.assertTrue(check in ("compute_work", "memory_service", "ownership") or name == "incomplete")
        self.assertGreaterEqual(count, 45)

    def test_pending_never_becomes_complete_throughput(self):
        admitted, raw = self.cases["incomplete"]
        observation = normalize(admitted, raw)
        self.assertEqual(observation.execution, "incomplete")
        self.assertTrue(observation.pending)
        self.assertTrue(all(m.completion_scope == "partial" for m in observation.metrics))
        self.assertNotIn("payload_throughput", [m.metric_id for m in observation.metrics])
        with self.assertRaises(UnsupportedAudit):
            audit("drain", admitted, raw)

    def test_integrated_pipeline_golden(self):
        self.assertEqual(self.cases["depth1"][1]["elapsed_aci_cycles"], 45)
        self.assertEqual(self.cases["compute"][1]["elapsed_aci_cycles"], 33)

    def test_resume_preserves_direct_events(self):
        for name in ("memory", "compute"):
            admitted, raw = self.cases[name]
            before, after = admitted.execute((5.0,))
            self.assertEqual(before["status"], "incomplete")
            self.assertEqual(after, raw)
            normalize(admitted, before)
            audit("ownership", admitted, before)

    def test_unknown_clock_and_exact_large_counter_conversion(self):
        cycles = 2**60 + 3
        point = TimePoint(value=cycles, unit="cycles", clock_domain="device")
        clock = ClockDomain(domain_id="device", hz=Metadata[float](state="known", value=250000000.0))
        self.assertEqual(converted_seconds(point, (clock,)), Fraction(cycles, 250000000))
        self.assertEqual(point.value, cycles)
        with self.assertRaisesRegex(ValueError, "explicit known clock"):
            converted_seconds(point, ())

    def test_budget_rejects_without_running(self):
        with self.assertRaisesRegex(ValueError, "exceeds case budget"):
            admit("compute_workload_v1", self.cases["compute"][0].path, horizon=1)

    def test_explicit_mapping_partial_order_and_large_integer(self):
        document = observations()
        reference = NormalizedObservations.model_validate_json(json.dumps(document))
        document["events"].reverse()
        actual = NormalizedObservations.model_validate_json(json.dumps(document))
        self.assertEqual(actual.events[0].counters[0].value, 2**53 + 3)
        entities = {"memory": "memory", "ep": "ep"}
        events = {"begin": "begin", "end": "end"}
        functional_match(actual, reference, entities, events)
        with self.assertRaisesRegex(ValueError, "mapping"):
            functional_match(actual, reference, {}, events)
        with self.assertRaisesRegex(ValueError, "one-to-one"):
            functional_match(actual, reference, {"memory": "memory", "ep": "memory"}, events)
        document["causal_edges"] = []
        with self.assertRaisesRegex(ValueError, "causal"):
            functional_match(NormalizedObservations.model_validate_json(json.dumps(document)), reference, entities, events)

    def corrupted(self, name, check, mutate):
        admitted, original = self.cases[name]
        raw = copy.deepcopy(original)
        mutate(raw)
        with self.assertRaises((ValueError, KeyError), msg=f"{name}/{check} accepted corruption"):
            audit(check, admitted, raw)

    def test_corrupted_wrap_hop(self):
        self.corrupted("torus", "routing", lambda r: r["plan"]["routes"][0]["hops"][1].update(dst_router="t2_1"))

    def test_corrupted_trace_route(self):
        def mutate(raw):
            event = next(e for e in raw["trace"] if e["action"] == "link_launch")
            event["channel"]["identity"] = "not-a-hop"
        self.corrupted("torus", "packet_accounting", mutate)

    def test_missing_flit_with_unchanged_summary(self):
        self.corrupted("torus", "packet_accounting", lambda r: r["trace"].remove(next(e for e in r["trace"] if e["action"] == "link_launch")))

    def test_duplicate_flit_with_unchanged_summary(self):
        self.corrupted("torus", "packet_accounting", lambda r: r["trace"].append(copy.deepcopy(next(e for e in r["trace"] if e["action"] == "link_launch"))))

    def test_missing_or_duplicate_packet(self):
        self.corrupted("memory", "packet_accounting", lambda r: r["wire_packets"].pop())
        self.corrupted("memory", "packet_accounting", lambda r: r["wire_packets"].append(copy.deepcopy(r["wire_packets"][0])))

    def test_alias_bandwidth_cannot_multiply(self):
        def mutate(raw):
            first = raw["chunks"][0]
            second = next(c for c in raw["chunks"][1:] if c["resource_id"] == first["resource_id"])
            second["start_aci_cycles"] = first["start_aci_cycles"]
            second["end_aci_cycles"] = second["start_aci_cycles"] + second["service_aci_cycles"]
        self.corrupted("memory", "memory_service", mutate)

    def test_unpaid_posted_effect(self):
        def mutate(raw):
            effect = next(e for e in raw["ownership_trace"] if e["action"] == "publish" and e["version"]["producer_id"] == "posted")
            effect["time_aci_cycles"] = 0
        self.corrupted("memory", "memory_service", mutate)

    def test_descriptor_release_corruption(self):
        self.corrupted("memory", "ownership", lambda r: r["descriptor_trace"].append(next(e for e in r["descriptor_trace"] if e["action"] == "release")))

    def test_credit_return_without_release(self):
        self.corrupted("torus", "ownership", lambda r: r["trace"].remove(next(e for e in r["trace"] if e["action"] == "credit_release")))

    def test_slot_release_and_generation_corruption(self):
        self.corrupted("compute", "ownership", lambda r: r["slot_events"].append(next(e for e in r["slot_events"] if e["action"] == "release")))
        self.corrupted("compute", "ownership", lambda r: r["slot_events"][0].update(generation=10))

    def test_premature_math_and_publication(self):
        for action in ("math_start", "output_ready"):
            self.corrupted("compute", "compute_work", lambda r, selected=action: next(e for e in r["stages"] if e["action"] == selected).update(time_aci_cycles=0))

    def test_false_complete(self):
        self.corrupted("incomplete", "drain", lambda r: r.update(status="complete", pending=[]))

    def test_local_only_has_zero_network_bytes(self):
        admitted, _ = self.cases["memory"]
        document = json.loads(admitted.path.read_text())
        document["source"]["graph_path"] = str(admitted.inputs["source.json"])
        document["operations"] = [document["operations"][0]]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.json"
            path.write_text(json.dumps(document))
            local = admit("memory_replay_v1", path)
            raw = local.execute(())[0]
            self.assertEqual(raw["channel_bytes"], 0)
            audit("packet_accounting", local, raw)
            self.assertGreater(raw["memory_service_bytes"], 0)


if __name__ == "__main__":
    unittest.main()

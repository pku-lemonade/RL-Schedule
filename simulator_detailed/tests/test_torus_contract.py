"""Contract admission tests; no torus runtime or hardware conformance is implied."""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from simulator_detailed.configs.schemas.topology import (
    CanonicalTopology,
    TopologyReplay,
)
from simulator_detailed.configs.schemas.torus_replay import (
    RouteRecord,
    TorusReplay,
    TransportEnvelope,
)
from simulator_detailed.torus_contract import PreparedTorusContract, ResolvedQuantity
from simulator_detailed.torus_records import (
    EffectivePlanRecord,
    PacketResult,
    TorusReplayResult,
    TransportTraceEvent,
)
from simulator_detailed.utils.definitions import Flit

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = {"status": "assumed", "description": "Synthetic contract-test setting; no hardware measurement."}


def literal(value, unit):
    return {"kind": "literal", "value": value, "unit": unit, "evidence": copy.deepcopy(EVIDENCE)}


def service(cycles=1, timebase="noc"):
    return {"cycles": cycles, "timebase": timebase, "evidence": copy.deepcopy(EVIDENCE)}


def link():
    return {
        "wire_bits_per_noc_cycle": literal(128, "bits_per_cycle"),
        "payload_bits_per_noc_cycle": literal(128, "bits_per_cycle"),
        "launch_interval_noc_cycles": 2, "propagation_noc_cycles": 1,
        "credit_return_noc_cycles": 1, "lane_capacity_flits": 1,
        "staging_capacity_flits": 1, "arbitration_quantum_flits": 2,
        "evidence": copy.deepcopy(EVIDENCE),
    }


def fabric(fabric_id):
    return {
        "fabric_id": fabric_id, "noc_clock": literal(1_000_000_000, "Hz"),
        "flit": {"physical_flit_bytes": literal(32, "bytes"), "payload_capacity_bytes": 24,
                 "header_bytes": 8, "evidence": copy.deepcopy(EVIDENCE)},
        "router": {"rc_noc_cycles": 1, "sa_noc_cycles": 1, "transfer_noc_cycles": 2,
                   "transfer_initiation_interval_noc_cycles": 1, "transfer_capacity_flits": 2,
                   "arbitration_quantum_flits": 2, "evidence": copy.deepcopy(EVIDENCE)},
        "network_link": link(), "local_link": link(),
    }


def endpoint(fabric_id, name, roles):
    injects = "request_source" in roles or "responder" in roles
    receives = any(role in roles for role in ("request_sink", "response_sink", "responder"))
    return {
        "endpoint_id": f"{name}{fabric_id}", "fabric_id": fabric_id, "roles": roles,
        "inject_port": "local" if injects else None,
        "eject_port": "local" if receives else None,
        "injection_queue_capacity_packets": 1 if injects else None,
        "sink_service": service() if receives else None,
        "response_queue_capacity_packets": 1 if "responder" in roles else None,
        "response_service": service(3) if "responder" in roles else None,
        "evidence": copy.deepcopy(EVIDENCE),
    }


def document(fabric_ids=(0, 7)):
    return {
        "kind": "topology_replay", "schema_version": 2, "policy": "dimension_dateline_v1",
        "source": {"kind": "canonical_graph", "graph_path": "graph.json"},
        "aci_clock": literal(500_000_000, "Hz"),
        "binding": {
            "fabrics": [{"fabric_id": f, "topology_policy": "torus_2d_positive",
                         "routing_policy": "dimension_order_xy" if f == 0 else "dimension_order_yx",
                         "dateline": {"x": 2, "y": 1},
                         "router_default": {"enabled": True, "evidence": copy.deepcopy(EVIDENCE)},
                         "link_default": {"enabled": True, "evidence": copy.deepcopy(EVIDENCE)},
                         "evidence": copy.deepcopy(EVIDENCE)} for f in fabric_ids],
            "endpoints": [e for f in fabric_ids for e in (
                endpoint(f, "src", ["request_source", "response_sink", "request_sink"]),
                endpoint(f, "dst", ["responder", "request_sink"]),
            )],
        },
        "fabrics": [fabric(f) for f in fabric_ids],
        "traffic": [{"kind": "request_response", "transfer_id": f"read:{f}", "fabric_id": f,
                     "source": f"src{f}", "destination": f"dst{f}", "payload_bytes": 25,
                     "start_aci_cycles": 0, "burst_quantum_flits": 2,
                     "response_payload_bytes": 49, "response_burst_quantum_flits": 3} for f in fabric_ids],
        "slowdowns": [{"failure_id": "slow-wrap", "fabric_id": fabric_ids[0], "link_id": "wrap-x",
                       "start_aci_cycles": 5, "end_aci_cycles": 10, "factor": 2}],
        "max_aci_cycles": 1000,
    }


def graph_document():
    return json.loads((ROOT / "configs/topologies/heterogeneous_example.json").read_text())


def profile_documents():
    replay = document((0, 1))
    replay["source"] = {"kind": "hardware_profile", "profile_path": "profile.json"}
    for item in replay["fabrics"]:
        item["noc_clock"] = {"kind": "profile_parameter", "parameter": "ai_clock", "unit": "Hz"}
        item["flit"]["physical_flit_bytes"] = {"kind": "profile_parameter", "parameter": "flit_bytes", "unit": "bytes"}
        for key in ("network_link", "local_link"):
            for width in ("wire_bits_per_noc_cycle", "payload_bits_per_noc_cycle"):
                item[key][width] = {"kind": "profile_parameter", "parameter": "interface_width", "unit": "bits_per_cycle"}
    profile = json.loads((ROOT / "configs/profiles/wormhole_b0_n150_assumed.json").read_text())
    return replay, profile


class TorusContractTests(unittest.TestCase):
    def test_round_trip_and_nested_immutability(self):
        data = document()
        config = TorusReplay.model_validate(data)
        self.assertEqual(config, TorusReplay.model_validate_json(config.model_dump_json()))
        data["binding"]["endpoints"][0]["roles"].clear()
        self.assertIn("request_source", config.binding.endpoints[0].roles)
        with self.assertRaises(ValidationError):
            config.binding.endpoints[0].sink_service.cycles = 99
        with self.assertRaises(TypeError):
            config.fabrics[0] = config.fabrics[1]
        self.assertEqual(config.fabrics[0].network_link.lane_capacity_flits, 1)

    def test_invalid_configuration_is_rejected(self):
        mutations = [
            ("version string", lambda d: d.update(schema_version="2")),
            ("version bool", lambda d: d.update(schema_version=True)),
            ("wrong version", lambda d: d.update(schema_version=1)),
            ("wrong kind", lambda d: d.update(kind="hardware_profile")),
            ("missing assumptions", lambda d: d["binding"]["fabrics"][0].pop("router_default")),
            ("unknown policy", lambda d: d.update(policy="hardware_buddy")),
            ("multicast", lambda d: d["traffic"][0].update(kind="multicast")),
            ("nested response", lambda d: d["traffic"][0].update(response={"kind": "request_response"})),
            ("independent response", lambda d: d["traffic"][0].update(traffic_class="response")),
            ("wrong units", lambda d: d["aci_clock"].update(unit="bytes")),
            ("bool width", lambda d: d["fabrics"][0]["network_link"]["wire_bits_per_noc_cycle"].update(value=True)),
            ("float capacity", lambda d: d["fabrics"][0]["network_link"].update(lane_capacity_flits=1.0)),
            ("zero capacity", lambda d: d["fabrics"][0]["router"].update(transfer_capacity_flits=0)),
            ("unsafe launch", lambda d: d["fabrics"][0]["network_link"].update(launch_interval_noc_cycles=1)),
            ("fractional flit", lambda d: d["fabrics"][0]["flit"]["physical_flit_bytes"].update(value=32.5)),
            ("oversize payload", lambda d: d["fabrics"][0]["flit"].update(payload_capacity_bytes=33)),
            ("zero clock", lambda d: d["aci_clock"].update(value=0)),
            ("infinite clock", lambda d: d["aci_clock"].update(value=float("inf"))),
            ("NaN timing", lambda d: d["fabrics"][0]["router"].update(rc_noc_cycles=float("nan"))),
            ("missing response storage", lambda d: d["binding"]["endpoints"][1].update(response_queue_capacity_packets=None)),
            ("blocked sink", lambda d: d["binding"]["endpoints"][0]["sink_service"].update(cycles=0)),
            ("missing return sink", lambda d: d["binding"]["endpoints"][0].update(roles=["request_source", "request_sink"])),
            ("missing source role", lambda d: d["traffic"][0].update(source="dst0", destination="src0")),
            ("cross fabric", lambda d: d["traffic"][0].update(destination="dst7")),
            ("unknown endpoint", lambda d: d["traffic"][0].update(destination="missing")),
            ("duplicate traffic", lambda d: d["traffic"].append(copy.deepcopy(d["traffic"][0]))),
            ("duplicate fabric", lambda d: d["fabrics"].append(copy.deepcopy(d["fabrics"][0]))),
            ("duplicate role", lambda d: d["binding"]["endpoints"][0]["roles"].append("request_source")),
            ("fabric mismatch", lambda d: d["fabrics"].pop()),
            ("undocumented source", lambda d: d["aci_clock"]["evidence"].update(status="documented")),
            ("unknown endpoint mode", lambda d: d["binding"]["endpoints"][0].update(vc_linked=True)),
        ]
        for label, mutate in mutations:
            data = document()
            mutate(data)
            with self.subTest(label=label), self.assertRaises(ValueError):
                TorusReplay.model_validate(data)

    def test_slowdown_identity_intervals_and_local_overrides(self):
        data = document()
        failure = data["slowdowns"][0]
        data["slowdowns"].append({**failure, "failure_id": "next", "start_aci_cycles": 10, "end_aci_cycles": 15})
        data["slowdowns"].append({**failure, "failure_id": "other-fabric", "fabric_id": 7})
        TorusReplay.model_validate(data)
        for patch in ({"start_aci_cycles": 9}, {"factor": 0.5}, {"factor": float("inf")},
                      {"factor": True}, {"end_aci_cycles": 10}, {"fabric_id": 9}, {"failure_id": "slow-wrap"}):
            altered = copy.deepcopy(data)
            altered["slowdowns"][1].update(patch)
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                TorusReplay.model_validate(altered)
        data["local_overrides"] = [{"endpoint_id": "src0", "direction": "inject", "settings": link()}]
        TorusReplay.model_validate(data)
        data["local_overrides"][0]["endpoint_id"] = "not-bound"
        with self.assertRaises(ValueError):
            TorusReplay.model_validate(data)

    def test_preparation_does_not_claim_topology_or_runtime_admission(self):
        # This source is deliberately a chain, not a torus. Part 2 owns that check.
        prepared = PreparedTorusContract.prepare(TorusReplay.model_validate(document()), graph_document())
        self.assertEqual(prepared.export()["validation_stage"], "configuration_only")
        self.assertFalse(prepared.export()["can_execute"])
        self.assertIn("topology_binding", prepared.export()["pending_validation"])
        self.assertEqual(len(prepared.quantities), 13)
        corrupted = prepared.config.model_copy(update={"max_aci_cycles": -1})
        with self.assertRaises(ValueError):
            PreparedTorusContract.prepare(corrupted, graph_document())

    def test_identity_reordering_paths_and_semantic_changes(self):
        data, graph = document(), graph_document()
        data["aci_clock"]["evidence"] = {
            "status": "documented", "description": "Synthetic pinned citation records, not verified measurements.",
            "sources": [{"url": "https://example.com/a", "sha256": "a" * 64},
                        {"url": "https://example.com/b", "revision": "b" * 40}],
        }
        first = PreparedTorusContract.prepare(TorusReplay.model_validate(data), graph)
        data["aci_clock"]["evidence"]["sources"].reverse()
        for key in ("fabrics", "traffic"):
            data[key].reverse()
        for key in ("fabrics", "endpoints"):
            data["binding"][key].reverse()
        for item in data["binding"]["endpoints"]:
            item["roles"].reverse()
        for key in ("tiles", "routers", "links", "attachments"):
            graph[key].reverse()
        data["source"]["graph_path"] = "/relocated/graph.json"
        reordered = PreparedTorusContract.prepare(TorusReplay.model_validate(data), graph)
        self.assertEqual(first.contract_sha256, reordered.contract_sha256)
        data["fabrics"][0]["network_link"]["lane_capacity_flits"] = 2
        changed = PreparedTorusContract.prepare(TorusReplay.model_validate(data), graph)
        self.assertNotEqual(first.contract_sha256, changed.contract_sha256)
        graph["resources"][0]["capacity_bytes"] += 1
        self.assertNotEqual(changed.source_sha256, PreparedTorusContract.prepare(TorusReplay.model_validate(data), graph).source_sha256)

    def test_profile_quantity_resolution_and_override_evidence(self):
        data, profile = profile_documents()
        prepared = PreparedTorusContract.prepare(TorusReplay.model_validate(data), profile)
        values = {q.field_path: q for q in prepared.quantities}
        width = values["fabrics.0.network_link.wire_bits_per_noc_cycle"]
        self.assertEqual((width.source_value, width.source_unit, width.value, width.unit),
                         (32, "bytes_per_cycle", 256, "bits_per_cycle"))
        self.assertEqual(width.source_clock_parameter, "ai_clock")
        self.assertEqual(values["fabrics.0.flit.physical_flit_bytes"].value, 32)
        self.assertIn("sources", json.loads(width.source_evidence_json))
        data["fabrics"][0]["noc_clock"]["override"] = {
            "value": 750_000_000, "reason": "Counterfactual lower clock for sensitivity test", "evidence": copy.deepcopy(EVIDENCE)}
        overridden = PreparedTorusContract.prepare(TorusReplay.model_validate(data), profile)
        clock = next(q for q in overridden.quantities if q.field_path == "fabrics.0.noc_clock")
        self.assertEqual((clock.source_value, clock.value), (1_000_000_000, 750_000_000))
        self.assertIn("Counterfactual", clock.override_reason)
        self.assertNotEqual(prepared.contract_sha256, overridden.contract_sha256)
        self.assertEqual(profile["parameters"]["ai_clock"]["value"], 1_000_000_000)
        self.assertNotIn("overrides", profile["parameters"]["ai_clock"])
        for patch in ({"source_evidence_json": "[]"}, {"source_evidence_json": '{"noncanonical": true}'},
                      {"override_reason": " "}, {"override_evidence_json": None},
                      {"override_evidence_json": "not JSON"}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                ResolvedQuantity.model_validate({**clock.model_dump(mode="json"), **patch})

    def test_profile_resolution_errors_and_timing_extremes(self):
        for patch in ({"parameter": "missing"}, {"parameter": "flit_bytes"},
                      {"kind": "literal", "value": 1_000_000_000, "unit": "Hz", "evidence": EVIDENCE}):
            data, profile = profile_documents()
            data["fabrics"][0]["noc_clock"] = {**data["fabrics"][0]["noc_clock"], **patch} if patch.get("kind") != "literal" else patch
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                PreparedTorusContract.prepare(TorusReplay.model_validate(data), profile)
        for change in ("small_flit", "narrow_width", "clock_overflow", "clock_underflow", "slowdown_overflow"):
            data, profile = profile_documents()
            if change == "small_flit":
                profile["parameters"]["flit_bytes"]["value"] = 16
            elif change == "narrow_width":
                profile["parameters"]["interface_width"]["value"] = 1
            elif change == "clock_overflow":
                data["aci_clock"]["value"] = 1e-300
                data["fabrics"][0]["noc_clock"]["override"] = {"value": 1e300, "reason": "Extreme test", "evidence": EVIDENCE}
            elif change == "clock_underflow":
                data["aci_clock"]["value"] = 1e300
                data["fabrics"][0]["noc_clock"]["override"] = {"value": 1e-300, "reason": "Extreme test", "evidence": EVIDENCE}
            else:
                data["slowdowns"][0]["factor"] = 1e308
            with self.subTest(change=change), self.assertRaises(ValueError):
                PreparedTorusContract.prepare(TorusReplay.model_validate(data), profile)
        data = document()
        data["fabrics"][0]["noc_clock"] = {"kind": "profile_parameter", "parameter": "ai_clock", "unit": "Hz"}
        with self.assertRaises(ValueError):
            PreparedTorusContract.prepare(TorusReplay.model_validate(data), graph_document())

    def test_relative_loading_version_one_and_cli_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "graph.json").write_text(json.dumps(graph_document()))
            (root / "config.json").write_text(json.dumps(document()))
            loaded = PreparedTorusContract.load(root / "config.json")
            self.assertEqual(loaded.contract_sha256, PreparedTorusContract.prepare(TorusReplay.model_validate(document()), graph_document()).contract_sha256)
            output = root / "result.json"
            run = subprocess.run([str(ROOT.parent / ".venv/bin/python"), "-m", "simulator_detailed.replay_topology",
                                  "--replay", str(root / "config.json"), "--output", str(output)], capture_output=True, text=True, check=False)
            self.assertEqual(run.returncode, 1)
            self.assertEqual(run.stdout, "")
            self.assertFalse(output.exists())
        legacy = TopologyReplay.model_validate_json((ROOT / "configs/replays/heterogeneous_unicast.json").read_text())
        self.assertEqual(legacy.schema_version, 1)
        with self.assertRaises(ValueError):
            TopologyReplay.model_validate(document())
        with self.assertRaises(ValueError):
            TorusReplay.model_validate(legacy.model_dump(mode="json"))
        self.assertNotIn("lane", Flit.model_fields)
        self.assertNotIn("plan_sha256", Flit.model_fields)


def route_document():
    return {
        "fabric_id": 0, "source": "src0", "destination": "src0", "traffic_class": "request",
        "hops": [
            {"lane": {"channel": {"fabric_id": 0, "kind": "inject", "identity": "src0"},
                      "traffic_class": "request", "dateline_phase": None},
             "src_router": None, "dst_router": "a", "src_port": "local", "dst_port": "local", "axis": None, "rank": 0},
            {"lane": {"channel": {"fabric_id": 0, "kind": "eject", "identity": "src0"},
                      "traffic_class": "request", "dateline_phase": None},
             "src_router": "a", "dst_router": None, "src_port": "local", "dst_port": "local", "axis": None, "rank": 9},
        ],
    }


def launch_document(hop_index=0):
    lane = route_document()["hops"][hop_index]["lane"]
    return {
        "action": "link_launch", "time_aci_cycles": hop_index,
        "fabric_id": 0, "router_id": None, "port_id": None,
        "channel": lane["channel"], "lane": lane,
        "packet": {"transfer_id": "local", "traffic_class": "request"},
        "flit_index": 0, "token_id": "token-0", "failure_id": None,
        "physical_bytes": 32, "payload_bytes": 9, "duration_aci_cycles": 1, "launch_factor": 1,
    }


class TorusRecordTests(unittest.TestCase):
    def plan(self):
        data = document((0,))
        data["traffic"] = [{"kind": "one_way", "transfer_id": "local", "fabric_id": 0,
                            "source": "src0", "destination": "src0", "payload_bytes": 9,
                            "start_aci_cycles": 0, "burst_quantum_flits": 2}]
        prepared = PreparedTorusContract.prepare(TorusReplay.model_validate(data), graph_document())
        return EffectivePlanRecord(
            policy=prepared.config.policy, source_sha256=prepared.source_sha256,
            contract_sha256=prepared.contract_sha256, source_json=prepared.source_json,
            configuration_json=prepared.configuration_json, quantities=prepared.quantities,
            graph=CanonicalTopology.model_validate(graph_document()), routes=(RouteRecord.model_validate(route_document()),),
        )

    def test_envelope_route_and_plan_identity_contracts(self):
        route = RouteRecord.model_validate(route_document())
        data = {"plan_sha256": "a" * 64, "packet": {"transfer_id": "read:0", "traffic_class": "request"},
                "fabric_id": 0, "source": "src0", "destination": "src0", "hop_index": 0,
                "lane": route.hops[0].lane.model_dump(mode="json"), "flit_index": 0, "flit_count": 1,
                "payload_bytes": 9, "physical_bytes": 32, "burst_quantum_flits": 2}
        envelope = TransportEnvelope.model_validate(data)
        self.assertTrue(envelope.is_head and envelope.is_tail)
        large = 2**53 + 1
        large_envelope = TransportEnvelope.model_validate({**data, "payload_bytes": large, "physical_bytes": large + 1})
        self.assertEqual(TransportEnvelope.model_validate_json(large_envelope.model_dump_json()).payload_bytes, large)
        with self.assertRaises(ValidationError):
            envelope.lane.traffic_class = "response"
        for field, value in (("fabric_id", 7), ("flit_index", 1), ("payload_bytes", 33), ("plan_sha256", "bad")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                TransportEnvelope.model_validate({**data, field: value})
        for mutate in (lambda d: d["hops"][0]["lane"].update(dateline_phase=0),
                       lambda d: d["hops"][1].update(rank=0),
                       lambda d: d["hops"][1].update(src_router="other"),
                       lambda d: d["hops"][1]["lane"].update(traffic_class="response")):
            bad = route_document()
            mutate(bad)
            with self.assertRaises(ValueError):
                RouteRecord.model_validate(bad)
        plan = self.plan()
        self.assertEqual(plan.plan_sha256, EffectivePlanRecord.model_validate_json(plan.model_dump_json()).plan_sha256)
        with self.assertRaises(ValueError):
            EffectivePlanRecord.model_validate({**plan.model_dump(mode="json"), "source_json": "{}"})
        changed = plan.model_dump(mode="json")
        changed["configuration_json"] = changed["configuration_json"].replace('"max_aci_cycles":1000.0', '"max_aci_cycles":1001.0')
        with self.assertRaises(ValueError):
            EffectivePlanRecord.model_validate(changed)

    def test_trace_launch_credit_and_failure_identity(self):
        launch = launch_document()
        event = TransportTraceEvent.model_validate(launch)
        self.assertEqual(TransportTraceEvent.model_validate_json(event.model_dump_json()), event)
        for patch in ({"fabric_id": 7}, {"lane": None}, {"packet": None}, {"flit_index": None},
                      {"physical_bytes": 0}, {"payload_bytes": 33}, {"launch_factor": None},
                      {"launch_factor": float("inf")}, {"time_aci_cycles": float("nan")},
                      {"packet": {"transfer_id": "local", "traffic_class": "response"}},
                      {"channel": {"fabric_id": 0, "kind": "inject", "identity": "other"}}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                TransportTraceEvent.model_validate({**launch, **patch})
        credit = {**launch, "action": "credit_return", "physical_bytes": 0, "payload_bytes": 0,
                  "packet": None, "flit_index": None, "launch_factor": None}
        TransportTraceEvent.model_validate(credit)
        for patch in ({"token_id": None}, {"lane": None}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                TransportTraceEvent.model_validate({**credit, **patch})
        failure = {**credit, "action": "failure_start", "lane": None, "token_id": None,
                   "failure_id": "slow-wrap", "channel": {"fabric_id": 0, "kind": "network", "identity": "wrap-x"}}
        TransportTraceEvent.model_validate(failure)
        for patch in ({"failure_id": None}, {"channel": None}, {"channel": launch["channel"]}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                TransportTraceEvent.model_validate({**failure, **patch})

    def test_result_counts_credit_drain_and_packet_boundaries(self):
        plan = self.plan()
        packet = {"packet": {"transfer_id": "local", "traffic_class": "request"}, "fabric_id": 0,
                  "source": "src0", "destination": "src0", "expected_payload_bytes": 9, "received_payload_bytes": 9,
                  "expected_flits": 1, "received_flits": 1, "physical_bytes": 32,
                  "first_injection_aci_cycles": 0, "first_ejection_aci_cycles": 2, "completion_aci_cycles": 3}
        resource = {"resource_id": "local-request", "kind": "lane", "fabric_id": 0, "unit": "flits",
                    "lane": route_document()["hops"][1]["lane"], "capacity": 1, "available": 0,
                    "occupied": 0, "pending_returns": 1, "peak_occupied": 1, "owners": []}
        data = {"kind": "topology_replay_result", "schema_version": 2, "plan": plan.model_dump(mode="json"),
                "plan_sha256": plan.plan_sha256, "runtime_indices": [], "status": "incomplete", "reason": "cycle_limit",
                "elapsed_aci_cycles": 3, "expected_payload_bytes": 9, "received_payload_bytes": 9,
                "packet_physical_bytes": 32, "transmitted_channel_bytes": 64, "packets": [packet],
                "resources": [resource], "trace": [launch_document(), launch_document(1),
                    {**launch_document(1), "action": "serialization_end", "time_aci_cycles": 2}],
                "execution": "torus_unicast_byte_transport",
                "niu_transactions": "unsupported", "memory_service": "unsupported",
                "compute_execution": "unsupported", "silicon_timing": "unvalidated"}
        result = TorusReplayResult.model_validate(data)
        self.assertFalse(result.resources[0].is_drained)
        # Two physical sends; their byte-bearing stage records are not extra sends.
        self.assertEqual(result.transmitted_channel_bytes, 64)
        with self.assertRaises(ValueError):
            TorusReplayResult.model_validate({**data, "status": "complete", "reason": "drained"})
        resource.update(available=1, pending_returns=0)
        complete = TorusReplayResult.model_validate({**data, "status": "complete", "reason": "drained"})
        self.assertTrue(complete.resources[0].is_drained)
        with self.assertRaises(ValidationError):
            complete.resources[0].available = 0
        for patch in ({"first_ejection_aci_cycles": None}, {"first_ejection_aci_cycles": 4},
                      {"received_flits": 0}, {"received_payload_bytes": 10}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                PacketResult.model_validate({**packet, **patch})
        for patch in ({"transmitted_channel_bytes": 32}, {"received_payload_bytes": 8}, {"plan_sha256": "f" * 64}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                TorusReplayResult.model_validate({**data, **patch})
        future_packet = {**packet, "first_injection_aci_cycles": 4, "first_ejection_aci_cycles": None,
                         "completion_aci_cycles": None}
        with self.assertRaises(ValueError):
            TorusReplayResult.model_validate({**data, "packets": [future_packet]})
        mapping = {"kind": "router", "fabric_id": 0, "identity": "a", "router_id": None, "runtime_index": 0}
        with self.assertRaises(ValueError):
            TorusReplayResult.model_validate({**data, "runtime_indices": [mapping, {**mapping, "identity": "b"}]})


if __name__ == "__main__":
    unittest.main()

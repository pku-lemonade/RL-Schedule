"""Executable graph transport, independent byte/timing oracles and CLI boundaries."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from simulator_detailed.configs.schemas.topology import (
    CanonicalTopology,
    ReplayResult,
    TopologyReplay,
)
from simulator_detailed.replay_topology import inspect_topology
from simulator_detailed.topology import Topology
from simulator_detailed.transport import ReplayPlan, ReplayRuntime

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "configs/replays/heterogeneous_unicast.json"
GRAPH = ROOT / "configs/topologies/heterogeneous_example.json"
PROFILE = ROOT / "configs/profiles/wormhole_b0_n150_assumed.json"


def documents():
    return json.loads(GRAPH.read_text()), json.loads(EXAMPLE.read_text())


def plan_for(graph, replay):
    return ReplayPlan.compile(Topology.compile(CanonicalTopology.model_validate(graph)), TopologyReplay.model_validate(replay))


class TopologyReplayTests(unittest.TestCase):
    def test_real_example_independent_routes_bytes_resources_and_trace_identity(self):
        plan = ReplayPlan.load(EXAMPLE)
        runtime = ReplayRuntime(plan)
        result = runtime.run()
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.reason, "drained")
        self.assertEqual(result.pending, ())
        self.assertEqual(result.expected_payload_bytes, 59)
        self.assertEqual(result.received_payload_bytes, 59)
        self.assertEqual(result.packet_physical_bytes, (3 + 3 + 1) * 16)
        self.assertEqual(result.transmitted_channel_bytes, (3 * 6 + 3 * 6 + 1 * 2) * 16)
        self.assertEqual(result.graph["unique_memory_bytes"], 1088)
        self.assertEqual(len(runtime.routers), 10)
        self.assertEqual(result.instantiated["compute_services"], [])
        self.assertEqual(result.instantiated["memory_services"], [])
        self.assertEqual(ReplayResult.model_validate_json(result.model_dump_json()), result)
        for transfer, fabric in (("forward", 0), ("other-fabric", 7)):
            sends = [e.link_id for e in result.trace if e.transfer_id == transfer and e.action == "LINK_SEND"
                     and e.channel_kind == "network" and e.is_tail]
            self.assertEqual(sends, ["a-ram", "ram-off", "off-hop", "hop-z"])
            routers = [e.router_id for e in result.trace if e.transfer_id == transfer and e.action == "ROUTER_RC"]
            self.assertEqual(routers, ["a", "ram", "off", "hop", "z"])
            self.assertTrue(all(e.fabric_id == fabric for e in result.trace if e.transfer_id == transfer))
        self.assertFalse(any(a.router_id == "off" for a in plan.topology.graph.attachments))
        self.assertFalse(any(l.src_router == "ram" and l.dst_router == "a" for l in plan.topology.graph.links))
        self.assertEqual(plan.topology.link_indices[(0, "a-ram")], plan.topology.link_indices[(7, "a-ram")])
        for event in result.trace:
            if event.channel_id is not None:
                self.assertIn(event.channel_id, runtime.links)
                channel = plan.channels[event.channel_id]
                self.assertEqual(event.fabric_id, channel.fabric_id)
                self.assertEqual(event.channel_kind, channel.kind)
            if event.router_id is not None:
                router = runtime.routers[(event.fabric_id, event.router_id)]
                self.assertEqual(router.id, plan.topology.router_indices[(event.fabric_id, event.router_id)])
                self.assertIn((event.fabric_id, event.router_id, event.port_id), plan.topology.port_indices)
        self.assertTrue(all(link.is_drained for link in runtime.links.values()))
        self.assertTrue(all(router.is_drained for router in runtime.routers.values()))
        with self.assertRaisesRegex(RuntimeError, "once"):
            runtime.run()

    def test_small_buffers_competing_bursts_slow_sink_drain(self):
        graph, replay = documents()
        off = next(r for r in graph["routers"] if r["fabric_id"] == 0 and r["router_id"] == "off")
        off["ports"].append({"port_id": "terminal", "kind": "local"})
        graph["attachments"].append({"endpoint_id": "middle", "fabric_id": 0, "router_id": "off",
                                      "role": "network", "enabled": True, "replay_enabled": True,
                                      "permissions_resolved": True, "inject_port": "terminal"})
        replay["routes"].append({"fabric_id": 0, "source": "middle", "destination": "sink", "link_ids": ["off-hop", "hop-z"]})
        first = replay["traffic"][0]
        first["payload_bytes"] = 97
        replay["traffic"].append({**first, "transfer_id": "competitor", "source": "middle",
                                   "payload_bytes": 113, "burst_quantum_flits": 3})
        replay["sink_service_aci_cycles_per_flit"] = 11
        runtime = ReplayRuntime(plan_for(graph, replay))
        result = runtime.run()
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.received_payload_bytes, 240)
        self.assertEqual(result.pending, ())
        actions = {e.action for e in result.trace}
        self.assertIn("STALL_CREDIT", actions)
        self.assertIn("STALL_SA", actions)
        self.assertTrue(all(link.in_flight_flits == 0 for link in runtime.links.values()))
        for link in runtime.links.values():
            events = [e for e in result.trace if e.channel_id == link.graph_channel_id]
            self.assertEqual(sum(e.action == "LINK_SEND" for e in events), sum(e.action == "CREDIT_RETURN" for e in events))

    def test_parallel_edge_and_disabled_inventory(self):
        graph, replay = documents()
        for name in ("a", "ram"):
            next(r for r in graph["routers"] if r["fabric_id"] == 0 and r["router_id"] == name)["ports"].append(
                {"port_id": "parallel", "kind": "network"})
        graph["links"].append({**graph["links"][0], "link_id": "parallel", "src_port": "parallel", "dst_port": "parallel"})
        graph["links"][0]["enabled"] = False
        replay["routes"][0]["link_ids"][0] = "parallel"
        runtime = ReplayRuntime(plan_for(graph, replay))
        result = runtime.run()
        self.assertEqual(result.status, "complete")
        self.assertFalse(any(e.link_id == "a-ram" and e.fabric_id == 0 for e in result.trace))
        self.assertTrue(any(e.link_id == "parallel" and e.fabric_id == 0 for e in result.trace))
        self.assertEqual(len(result.graph["graph"]["links"]), 9)
        self.assertEqual(sum(c.kind == "network" for c in runtime.plan.channels.values()), 8)

    def test_same_router_packet_boundaries_and_analytical_clocks_widths(self):
        for width, ratio in ((64, 2), (32, 1)):
            for size in (1, 12, 13, 36, 37):
                graph, replay = documents()
                replay["traffic"] = [{**replay["traffic"][2], "payload_bytes": size}]
                replay["sink_service_aci_cycles_per_flit"] = 0.25
                serialization = (128 // width) / ratio
                for fabric in replay["fabrics"]:
                    fabric["noc_clock_mhz"] = 1000 * ratio
                    for name in ("effective_rc_aci_cycles", "effective_sa_aci_cycles", "effective_st_aci_cycles"):
                        fabric[name] = 0
                    for name in ("network_link", "local_link"):
                        fabric[name].update(wire_bits_per_noc_cycle=width, payload_bits_per_noc_cycle=width,
                                            launch_interval_aci_cycles=serialization, effective_link_stage_aci_cycles=2,
                                            sync_credit_return_aci_cycles=1, effective_in_flight_window_flits=4)
                result = ReplayRuntime(plan_for(graph, replay)).run()
                count = (size + 11) // 12
                with self.subTest(width=width, ratio=ratio, bytes=size):
                    self.assertEqual(result.status, "complete")
                    self.assertEqual(result.packet_physical_bytes, count * 16)
                    self.assertEqual(result.transmitted_channel_bytes, count * 32)
                    self.assertAlmostEqual(result.transfers[0].completion_aci_cycles,
                                           2 * (serialization + 2) + (count - 1) * serialization + 0.25)

    def test_plan_identity_reordering_paths_and_effective_overrides(self):
        graph, replay = documents()
        first = plan_for(graph, replay)
        for key in ("routers", "tiles", "links", "attachments"):
            graph[key].reverse()
        for key in ("fabrics", "traffic", "routes"):
            replay[key].reverse()
        replay["graph_path"] = "/arbitrary/relocated/path.json"
        reordered = plan_for(graph, replay)
        self.assertEqual(first.routing.plan_id, reordered.routing.plan_id)
        self.assertEqual(ReplayRuntime(first).run(), ReplayRuntime(reordered).run())
        settings = copy.deepcopy(replay["fabrics"][0]["network_link"])
        settings["effective_link_stage_aci_cycles"] = 2
        settings["effective_in_flight_window_flits"] = 3
        replay["network_overrides"] = [{"fabric_id": 0, "link_id": "a-ram", "settings": settings}]
        replay["local_overrides"] = [{"endpoint_id": "source", "direction": "inject", "settings": settings}]
        changed = plan_for(graph, replay)
        self.assertNotEqual(changed.routing.plan_id, first.routing.plan_id)
        self.assertEqual(ReplayRuntime(changed).run().status, "complete")
        with self.assertRaises(TypeError):
            changed.channels["new"] = None

    def test_preconstruction_invalid_configuration(self):
        cases = [
            lambda g, r: r.update(schema_version=True),
            lambda g, r: r.update(schema_version=2),
            lambda g, r: r.update(kind="canonical_topology"),
            lambda g, r: r.update(memory_service={}),
            lambda g, r: r.update(aci_clock_mhz=float("inf")),
            lambda g, r: r.update(sink_service_aci_cycles_per_flit=0),
            lambda g, r: r["fabrics"][0].update(noc_clock_mhz=0),
            lambda g, r: r["fabrics"][0]["network_link"].pop("wire_bits_per_noc_cycle"),
            lambda g, r: r["fabrics"][0]["network_link"].update(payload_bits_per_noc_cycle=65),
            lambda g, r: r["fabrics"][0]["network_link"].update(launch_interval_aci_cycles=1),
            lambda g, r: r["fabrics"][0]["network_link"].update(effective_in_flight_window_flits=1),
            lambda g, r: r["fabrics"].pop(),
            lambda g, r: r["traffic"][0].update(destination="source-7"),
            lambda g, r: r["traffic"].append(r["traffic"][0]),
            lambda g, r: r["traffic"][0].update(payload_bytes=0),
            lambda g, r: r["traffic"][0].update(trans_type="FIXPATH"),
            lambda g, r: r.update(network_overrides=[{"fabric_id": 0, "link_id": "missing", "settings": r["fabrics"][0]["network_link"]}]),
            lambda g, r: g.update(connectivity_state="unresolved"),
            lambda g, r: g["routers"][2].update(enabled=False),
            lambda g, r: g["fabrics"][0].update(topology_policy="torus"),
        ]
        for index, change in enumerate(cases):
            graph, replay = documents()
            change(graph, replay)
            with self.subTest(case=index), self.assertRaises((ValueError, NotImplementedError)):
                plan_for(graph, replay)
        with self.assertRaisesRegex(ValueError, "inventory only"):
            ReplayPlan.compile(inspect_topology(PROFILE), TopologyReplay.model_validate(documents()[1]))

    def test_timeout_and_premature_idle_report_pending(self):
        for deadline in (1, 10, 43):
            graph, replay = documents()
            replay["max_aci_cycles"] = deadline
            result = ReplayRuntime(plan_for(graph, replay)).run()
            self.assertEqual(result.reason, "cycle_limit")
            self.assertEqual(result.status, "incomplete")
            self.assertEqual(result.elapsed_aci_cycles, deadline)
            self.assertTrue(result.pending)
        runtime = ReplayRuntime(ReplayPlan.load(EXAMPLE))
        # Inject a runtime fault: withhold every injection credit permanently.
        # This verifies diagnosis; valid admitted replay never uses this behavior.
        for identity, link in runtime.links.items():
            if runtime.plan.channels[identity].kind == "inject":
                link.in_flight_credits.get(link.in_flight_credits.capacity)
        result = runtime.run()
        self.assertEqual(result.reason, "idle_with_pending")
        self.assertEqual(result.status, "incomplete")
        self.assertTrue(result.pending)

    def test_cli_json_inspect_replay_and_failure_files(self):
        for path in (GRAPH, PROFILE, ROOT / "configs/instances/mesh_example.json"):
            run = subprocess.run([sys.executable, "-m", "simulator_detailed.replay_topology", "--inspect", str(path)],
                                 capture_output=True, text=True, check=False)
            self.assertEqual(run.returncode, 0, run.stderr)
            report = json.loads(run.stdout)
            self.assertEqual(report["kind"], "topology_inspection")
            if path == PROFILE:
                self.assertIsNone(report["counts"]["directed_links"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "result.json"
            args = [sys.executable, "-m", "simulator_detailed.replay_topology", "--replay", str(EXAMPLE), "--output", str(output)]
            run = subprocess.run(args, capture_output=True, text=True, check=False)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout), json.loads(output.read_text()))
            _, replay = documents()
            replay["graph_path"] = str(GRAPH)
            replay["routes"][0]["link_ids"] = ["nonexistent"]
            bad = root / "invalid.json"
            bad.write_text(json.dumps(replay))
            output.unlink()
            args[4] = str(bad)
            run = subprocess.run(args, capture_output=True, text=True, check=False)
            self.assertEqual(run.returncode, 1)
            self.assertFalse(output.exists())
            self.assertEqual(run.stdout, "")
            replay["routes"] = documents()[1]["routes"]
            replay["max_aci_cycles"] = 1
            bad.write_text(json.dumps(replay))
            run = subprocess.run(args, capture_output=True, text=True, check=False)
            self.assertEqual(run.returncode, 2, run.stderr)
            self.assertEqual(json.loads(output.read_text())["status"], "incomplete")

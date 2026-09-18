"""Hand-enumerated tree admission cases, including reversed fabric coordinates."""

from __future__ import annotations

import copy
import unittest
from typing import Any

from simulator_detailed.configs.schemas.multicast_sync import MulticastSyncWorkload
from simulator_detailed.configs.schemas.topology import CanonicalTopology
from simulator_detailed.multicast_plan import MulticastSyncPlan
from simulator_detailed.tests.test_multicast_sync import workload_document


def compile_document(document: dict[str, Any], graph: dict[str, Any]) -> MulticastSyncPlan:
    return MulticastSyncPlan.compile(
        MulticastSyncWorkload.model_validate(document), CanonicalTopology.model_validate(graph)
    )


def dual_fabric_document() -> tuple[dict[str, Any], dict[str, Any]]:
    document, graph = workload_document()
    fabric = copy.deepcopy(graph["fabrics"][0])
    fabric["fabric_id"] = 1
    graph["fabrics"].append(fabric)
    for router in list(graph["routers"]):
        other = copy.deepcopy(router)
        other["fabric_id"] = 1
        other["coordinate"] = {"x": 2 - router["coordinate"]["x"], "y": 1 - router["coordinate"]["y"]}
        graph["routers"].append(other)
    coordinates = {(r["coordinate"]["x"], r["coordinate"]["y"]): r["router_id"]
                   for r in graph["routers"] if r["fabric_id"] == 1}
    for (x, y), source in coordinates.items():
        for axis, destination, wrap in (
            ("x", coordinates[(x + 1) % 3, y], x == 2),
            ("y", coordinates[x, (y + 1) % 2], y == 1),
        ):
            graph["links"].append({
                "fabric_id": 1, "link_id": f"{source}/{axis}+", "src_router": source,
                "dst_router": destination, "src_port": f"{axis}+", "dst_port": f"{axis}-",
                "direction": "right" if axis == "x" else "down", "wrap": wrap, "enabled": True,
            })
    for attachment in list(graph["attachments"]):
        other = copy.deepcopy(attachment)
        other["fabric_id"] = 1
        other["endpoint_id"] += "-f1"
        graph["attachments"].append(other)
    document["memory"]["fabrics"] = [0, 1]
    for endpoint in list(document["memory"]["endpoints"]):
        endpoint["roles"] = ["initiator", "target", "response_sink"]
        other = copy.deepcopy(endpoint)
        other["fabric_id"] = 1
        other["endpoint_id"] += "-f1"
        document["memory"]["endpoints"].append(other)
    return document, graph


class MulticastTreeAdmissionTests(unittest.TestCase):
    def test_y_major_approach_is_one_shared_prefix(self) -> None:
        document, graph = workload_document(major_axis="y", include_source=False)
        write = document["writes"][0]
        write["rectangle"].update(start={"x": 0, "y": 1}, end={"x": 2, "y": 1})
        write["destinations"] = [d for d in write["destinations"] if d["endpoint_id"].endswith("_1")]
        tree = compile_document(document, graph).record.writes[0].tree
        self.assertEqual([(e.src_router, e.dst_router, e.kind) for e in tree.edges], [
            ("t0_0", "t0_1", "approach"), ("t0_1", "t1_1", "spine"), ("t1_1", "t2_1", "spine"),
        ])

    def test_translated_bounds_preserve_physical_recipients_and_qualified_edges(self) -> None:
        document, graph = dual_fabric_document()
        first = document["writes"][0]
        first["rectangle"].update(start={"x": 1, "y": 0}, end={"x": 2, "y": 1}, include_source=False)
        first["destinations"] = [d for d in first["destinations"] if d["endpoint_id"] in {
            "ep-t1_0", "ep-t1_1", "ep-t2_1"}]
        opposite = copy.deepcopy(first)
        opposite.update(operation_id="opposite", fabric_id=1, source_endpoint_id="ep-t2_1-f1",
                        source={"buffer_id": "b-ep-t2_1", "offset_bytes": 0, "size_bytes": 96})
        opposite["rectangle"].update(start={"x": 0, "y": 0}, end={"x": 1, "y": 1}, include_source=True)
        for destination in opposite["destinations"]:
            destination["endpoint_id"] += "-f1"
        document["writes"].append(opposite)
        for axis in ("x", "y"):
            with self.subTest(axis=axis):
                opposite["rectangle"]["major_axis"] = axis
                plans = compile_document(document, graph).record.writes
                self.assertEqual({r.tile_id for r in plans[0].tree.recipients}, {"t1_0", "t1_1", "t2_1"})
                self.assertEqual({r.tile_id for r in plans[1].tree.recipients}, {"t1_0", "t1_1", "t2_1"})
                self.assertEqual([(e.src_router, e.dst_router, e.kind) for e in plans[0].tree.edges], [
                    ("t0_0", "t1_0", "approach"), ("t1_0", "t1_1", "spine"),
                    ("t1_0", "t2_0", "branch"), ("t1_1", "t2_1", "branch"),
                ])
                expected = ([ ("t2_1", "t2_0", "spine"), ("t2_1", "t1_1", "branch"),
                              ("t2_0", "t1_0", "branch") ] if axis == "x" else
                            [ ("t2_1", "t1_1", "spine"), ("t2_1", "t2_0", "branch"),
                              ("t1_1", "t1_0", "branch") ])
                self.assertEqual([(e.src_router, e.dst_router, e.kind) for e in plans[1].tree.edges], expected)
                self.assertTrue(all(e.fabric_id == 1 for e in plans[1].tree.edges))
                recipient = next(r for r in plans[1].tree.recipients if r.tile_id == "t1_0")
                self.assertEqual((recipient.coordinate.x, recipient.coordinate.y), (1, 1))
                terminal = next(n for n in plans[0].tree.nodes if n.router_id == "t2_0")
                self.assertTrue(terminal.terminal)
                self.assertIsNone(terminal.recipient_endpoint_id)

    def test_local_and_degenerate_trees_have_exact_stage_inventory(self) -> None:
        for end, recipients, edges in (
            ({"x": 0, "y": 0}, {"ep-t0_0"}, []),
            ({"x": 2, "y": 0}, {"ep-t0_0", "ep-t1_0"}, [("t0_0", "t1_0"), ("t1_0", "t2_0")]),
            ({"x": 0, "y": 1}, {"ep-t0_0", "ep-t0_1"}, [("t0_0", "t0_1")]),
        ):
            for axis in ("x", "y"):
                with self.subTest(end=end, axis=axis):
                    document, graph = workload_document(major_axis=axis)
                    write = document["writes"][0]
                    write["rectangle"]["end"] = end
                    write["destinations"] = [d for d in write["destinations"] if d["endpoint_id"] in recipients]
                    tree = compile_document(document, graph).record.writes[0].tree
                    self.assertEqual([(e.src_router, e.dst_router) for e in tree.edges], edges)
                    self.assertEqual({n.recipient_endpoint_id for n in tree.nodes if n.recipient_endpoint_id}, recipients)
                    self.assertEqual(len(tree.nodes), len(edges) + 1)
                    self.assertEqual(sum(n.incoming_edge_id is None for n in tree.nodes), 1)

    def test_unavailable_workers_and_transit_cannot_silently_disappear(self) -> None:
        for failure in ("missing_endpoint", "disabled_endpoint", "unknown_permission", "disabled_worker", "disabled_transit"):
            with self.subTest(failure=failure):
                document, graph = workload_document()
                endpoint = next(e for e in graph["attachments"] if e["endpoint_id"] == "ep-t1_0")
                if failure in {"missing_endpoint", "disabled_worker"}:
                    graph["attachments"].remove(endpoint)
                elif failure == "disabled_endpoint":
                    endpoint.update(enabled=False, replay_enabled=False)
                elif failure == "unknown_permission":
                    endpoint.update(permissions_resolved=False, replay_enabled=False, inject_port=None, eject_port=None)
                if failure == "disabled_worker":
                    graph["enabled_worker_ids"].remove("t1_0")
                    graph["logical_workers"] = [w for w in graph["logical_workers"] if w["tile_id"] != "t1_0"]
                    document["writes"][0]["destinations"] = [d for d in document["writes"][0]["destinations"]
                                                               if d["endpoint_id"] != "ep-t1_0"]
                if failure == "disabled_transit":
                    next(r for r in graph["routers"] if r["router_id"] == "t2_0")["enabled"] = False
                with self.assertRaises(ValueError):
                    compile_document(document, graph)

    def test_duplicate_alias_is_rejected_instead_of_choosing_by_list_order(self) -> None:
        document, graph = workload_document()
        original = next(e for e in graph["attachments"] if e["endpoint_id"] == "ep-t1_0")
        alias = copy.deepcopy(original)
        alias.update(endpoint_id="alias", inject_port="alias", eject_port="alias")
        graph["attachments"].append(alias)
        next(r for r in graph["routers"] if r["router_id"] == "t1_0")["ports"].append({"port_id": "alias", "kind": "local"})
        with self.assertRaisesRegex(ValueError, "duplicate compute aliases"):
            compile_document(document, graph)

    def test_common_absolute_address_allows_different_buffer_bases(self) -> None:
        document, graph = workload_document()
        buffer = next(b for b in document["memory"]["buffers"] if b["buffer_id"] == "b-ep-t1_0")
        buffer["base_address"] = 64
        binding = next(d for d in document["writes"][0]["destinations"] if d["endpoint_id"] == "ep-t1_0")
        binding["offset_bytes"] = 64
        tree = compile_document(document, graph).record.writes[0].tree
        self.assertEqual(next(r.offset_bytes for r in tree.recipients if r.endpoint_id == "ep-t1_0"), 64)
        binding["offset_bytes"] = 128
        with self.assertRaisesRegex(ValueError, "common target address"):
            compile_document(document, graph)

    def test_overlap_is_physical_even_with_distinct_buffer_ids(self) -> None:
        document, graph = workload_document()
        alias = copy.deepcopy(document["memory"]["buffers"][0])
        alias.update(buffer_id="alias", base_address=32)
        document["memory"]["buffers"].append(alias)
        write = document["writes"][0]
        write["target_offset_bytes"] = 64
        for destination in write["destinations"]:
            destination["offset_bytes"] = 64
            if destination["endpoint_id"] == "ep-t0_0":
                destination.update(buffer_id="alias", offset_bytes=32)
        with self.assertRaisesRegex(ValueError, "overlap"):
            compile_document(document, graph)

    def test_posted_targets_need_only_ejection_but_acknowledgments_need_injection(self) -> None:
        document, graph = workload_document(include_source=False)
        for endpoint in graph["attachments"]:
            if endpoint["endpoint_id"] != "ep-t0_0":
                endpoint["inject_port"] = None
        document["writes"][0]["completion"] = "write_posted"
        compile_document(document, graph)
        document["writes"][0]["completion"] = "write_acknowledged"
        with self.assertRaisesRegex(ValueError, "injection permission"):
            compile_document(document, graph)

    def test_wrapped_bounds_invalid_source_entry_and_empty_target_are_rejected(self) -> None:
        for rectangle in (
            {"start": {"x": 2, "y": 0}, "end": {"x": 0, "y": 1}},
            {"end": {"x": 3, "y": 1}},
            {"start": {"x": 0, "y": 1}},
            {"end": {"x": 0, "y": 0}, "include_source": False},
        ):
            with self.subTest(rectangle=rectangle):
                document, graph = workload_document()
                document["writes"][0]["rectangle"].update(rectangle)
                with self.assertRaises(ValueError):
                    compile_document(document, graph)

"""Pure compilation tests for SystemSpec -> ImmutablePlan."""

import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from simulator_detailed.configs.schemas.system_spec import ImmutablePlan, SystemSpec
from simulator_detailed.system_compile import compile_system
from simulator_detailed.tests.test_generic_runtime import batch as batch_doc
from simulator_detailed.tests.test_generic_runtime import transfer

ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "configs" / "generic_graphs" / "synthetic_grid_2d.json"


def make_spec(transactions, counters=(), max_cycles=1000.0, mutate_graph=None):
    graph = json.loads(GRAPH.read_text())
    if mutate_graph is not None:
        mutate_graph(graph)
    document = {
        "kind": "system_spec",
        "schema_version": 1,
        "spec_id": "compile-test",
        "graph": graph,
        "batch": batch_doc(transactions, counters, max_cycles),
    }
    return SystemSpec.model_validate(document)


class TestCompileDeterminism(unittest.TestCase):
    def test_identical_input_identical_plan(self):
        spec = make_spec([transfer("t_a", "eu_00", "mem_b_ep")])
        before = spec.model_dump(mode="json")
        first = compile_system(spec)
        second = compile_system(spec)
        self.assertEqual(first.plan_sha256, second.plan_sha256)
        self.assertEqual(first.model_dump(mode="json"), second.model_dump(mode="json"))
        # The input object is not mutated by compilation.
        self.assertEqual(spec.model_dump(mode="json"), before)

    def test_reordered_graph_records_same_digest(self):
        def shuffle(graph):
            for key in (
                "nodes", "networks", "ports", "links",
                "dma_endpoints", "execution_units", "memory_resources", "static_routes",
            ):
                graph[key] = list(reversed(graph[key]))
        reference = compile_system(make_spec([transfer("t_a", "eu_00", "mem_b_ep")]))
        reordered = compile_system(make_spec([transfer("t_a", "eu_00", "mem_b_ep")], mutate_graph=shuffle))
        self.assertEqual(reference.plan_sha256, reordered.plan_sha256)
        self.assertEqual(reference.spec_sha256, reordered.spec_sha256)

    def test_plan_is_immutable_and_digest_checked(self):
        plan = compile_system(make_spec([transfer("t_a", "eu_00", "mem_b_ep")]))
        with self.assertRaises(ValidationError):
            plan.content = plan.content.model_copy(update={"max_cycles": 1.0})
        dumped = plan.model_dump(mode="json")
        dumped["content"]["max_cycles"] = 1.0
        with self.assertRaises(ValidationError):
            ImmutablePlan.model_validate(dumped)


class TestPlanContents(unittest.TestCase):
    def test_resources_counters_and_order(self):
        spec = make_spec(
            [
                transfer("t_a", "eu_00", "mem_b_ep"),
                {
                    "transaction_id": "s_1", "kind": "signal", "counter_id": "barrier",
                    "delta": 1, "depends_on": [], "start_cycles": 5.0,
                },
                {
                    "transaction_id": "s_2", "kind": "signal", "counter_id": "barrier",
                    "delta": 1, "depends_on": [], "start_cycles": 10.0,
                },
                {
                    "transaction_id": "w_1", "kind": "wait", "counter_id": "barrier",
                    "threshold": 2, "depends_on": [], "start_cycles": 0.0,
                },
            ],
            counters=[{"counter_id": "barrier", "initial_value": 0}],
        )
        plan = compile_system(spec)
        self.assertEqual(
            [t.transaction_id for t in plan.content.transactions],
            ["t_a", "s_1", "s_2", "w_1"],
        )
        self.assertEqual(len(plan.content.resources), 23)
        kinds = {r.kind for r in plan.content.resources}
        self.assertEqual(kinds, {"link", "execution_unit"})
        counters = {c.counter_id: c for c in plan.content.counters}
        self.assertEqual(counters["barrier"].upper_bound, 2)
        waits = [t for t in plan.content.transactions if t.kind == "wait"]
        self.assertEqual(waits[0].reachable, 2)
        moved = plan.content.transactions[0]
        self.assertEqual(len(moved.hops), 3)
        self.assertEqual(moved.hops[0].timing.bytes_per_cycle, 10)
        self.assertIsNone(moved.terminal)

    def test_terminal_classification(self):
        plan = compile_system(make_spec([
            transfer("t_bad", "eu_00", "eu_10"),  # net_alpha, no declared route: unreachable
            transfer("t_big", "eu_00", "mem_b_ep", payload=2097153),
        ]))
        bad, big = plan.content.transactions
        self.assertEqual(bad.terminal, "route_unreachable")
        self.assertEqual(big.terminal, "capacity_exceeded")
        self.assertEqual(bad.hops, ())


class TestCompileFailures(unittest.TestCase):
    def test_unknown_endpoint(self):
        with self.assertRaises(ValueError):
            compile_system(make_spec([transfer("t_x", "eu_00", "no_such_ep")]))

    def test_unknown_execution_unit(self):
        with self.assertRaises(ValueError):
            compile_system(make_spec([{
                "transaction_id": "c_bad", "kind": "compute", "unit_id": "eu_99",
                "duration_cycles": 1.0, "depends_on": [], "start_cycles": 0.0,
            }]))

    def test_unknown_timing_network(self):
        spec_doc = make_spec([transfer("t_a", "eu_00", "mem_b_ep")]).model_dump(mode="json")
        spec_doc["batch"]["timing"].append({
            "network_id": "net_gamma",
            "link": {
                "bytes_per_cycle": 1, "hop_cycles": 1.0,
                "credit_return_cycles": 1.0, "buffer_slots": 1,
            },
            "overrides": [],
        })
        with self.assertRaises(ValueError):
            compile_system(SystemSpec.model_validate(spec_doc))

    def test_unknown_override_link(self):
        spec_doc = make_spec([transfer("t_a", "eu_00", "mem_b_ep")]).model_dump(mode="json")
        spec_doc["batch"]["timing"][0]["overrides"] = [{
            "link_id": "no_such_link",
            "timing": {
                "bytes_per_cycle": 1, "hop_cycles": 1.0,
                "credit_return_cycles": 1.0, "buffer_slots": 1,
            },
        }]
        with self.assertRaises(ValueError):
            compile_system(SystemSpec.model_validate(spec_doc))

    def test_spec_schema_rejections(self):
        document = make_spec([transfer("t_a", "eu_00", "mem_b_ep")]).model_dump(mode="json")
        document["kind"] = "generic_transaction_batch"
        with self.assertRaises(ValidationError):
            SystemSpec.model_validate(document)
        document = make_spec([transfer("t_a", "eu_00", "mem_b_ep")]).model_dump(mode="json")
        document["batch"]["transactions"] = [
            {
                "transaction_id": "a", "kind": "compute", "unit_id": "eu_00",
                "duration_cycles": 1.0, "depends_on": ["b"], "start_cycles": 0.0,
            },
            {
                "transaction_id": "b", "kind": "compute", "unit_id": "eu_00",
                "duration_cycles": 1.0, "depends_on": ["a"], "start_cycles": 0.0,
            },
        ]
        with self.assertRaises(ValidationError):
            SystemSpec.model_validate(document)


if __name__ == "__main__":
    unittest.main()

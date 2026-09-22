"""Generic transaction runtime tests; synthetic batches only, no hardware claims."""

import unittest
from pathlib import Path

from pydantic import ValidationError

from simulator_detailed.configs.schemas.generic_transactions import (
    GenericTransactionBatch,
)
from simulator_detailed.generic_graph import load_generic_system
from simulator_detailed.generic_runtime import run_generic_batch
from simulator_detailed.topology import content_digest

ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "configs" / "generic_graphs" / "synthetic_grid_2d.json"

SYSTEM = None


def system():
    global SYSTEM
    if SYSTEM is None:
        SYSTEM = load_generic_system(GRAPH)
    return SYSTEM


def timing():
    return [
        {
            "network_id": "net_alpha",
            "link": {
                "bytes_per_cycle": 10,
                "hop_cycles": 1.0,
                "credit_return_cycles": 1.0,
                "buffer_slots": 4,
            },
            "overrides": [],
        },
        {
            "network_id": "net_beta",
            "link": {
                "bytes_per_cycle": 5,
                "hop_cycles": 2.0,
                "credit_return_cycles": 1.0,
                "buffer_slots": 2,
            },
            "overrides": [],
        },
    ]


def batch(transactions, counters=(), max_cycles=1000.0, timing_doc=None):
    return {
        "kind": "generic_transaction_batch",
        "schema_version": 1,
        "batch_id": "synthetic-batch",
        "graph_path": "synthetic_grid_2d.json",
        "timing": timing_doc if timing_doc is not None else timing(),
        "counters": list(counters),
        "transactions": list(transactions),
        "max_cycles": max_cycles,
    }


def run(transactions, counters=(), max_cycles=1000.0):
    parsed = GenericTransactionBatch.model_validate(
        batch(transactions, counters, max_cycles)
    )
    return run_generic_batch(system(), parsed), parsed


def transfer(tx_id, source, destination, payload=100, network="net_alpha", **extra):
    return {
        "transaction_id": tx_id, "kind": "transfer", "network_id": network,
        "source": source, "destination": destination, "payload_bytes": payload,
        "depends_on": [], "start_cycles": 0.0, **extra,
    }


def spans_by_id(result):
    return {span.transaction_id: span for span in result.transactions}


class TestGenericRuntimeAcceptance(unittest.TestCase):
    def test_same_link_queueing(self):
        result, _ = run([
            transfer("t_a", "eu_00", "mem_b_ep"),
            transfer("t_b", "dma_a", "mem_b_ep"),
        ])
        self.assertEqual(result.status, "complete")
        spans = spans_by_id(result)
        a, b = spans["t_a"], spans["t_b"]
        # Both routes share all three alpha links; FIFO order is declaration order.
        for hop_index in range(3):
            a_hop, b_hop = a.hops[hop_index], b.hops[hop_index]
            self.assertLessEqual(b_hop.serialization_start_cycles, b_hop.serialization_end_cycles)
            self.assertGreaterEqual(
                b_hop.serialization_start_cycles,
                a_hop.serialization_end_cycles,
                f"serialization overlap on hop {hop_index}",
            )
        self.assertEqual(a.hops[0].serialization_start_cycles, 0.0)
        self.assertEqual(a.hops[0].serialization_end_cycles, 10.0)
        self.assertEqual(b.hops[0].serialization_start_cycles, 10.0)
        self.assertEqual(a.end_cycles, 33.0)
        self.assertEqual(b.end_cycles, 43.0)

    def test_disjoint_links_overlap(self):
        result, _ = run([
            transfer("t_c", "eu_11", "eu_00"),
            transfer("t_d", "eu_00", "mem_b_ep"),
        ])
        self.assertEqual(result.status, "complete")
        spans = spans_by_id(result)
        c, d = spans["t_c"], spans["t_d"]
        self.assertEqual(c.hops[0].serialization_start_cycles, 0.0)
        self.assertEqual(d.hops[0].serialization_start_cycles, 0.0)
        self.assertEqual(c.hops[0].link_id, "al_11n10")
        self.assertEqual(d.hops[0].link_id, "al_00e10")
        self.assertEqual(c.end_cycles, 22.0)
        self.assertEqual(d.end_cycles, 33.0)

    def test_compute_overlaps_transfer(self):
        result, _ = run([
            {
                "transaction_id": "c_00", "kind": "compute", "unit_id": "eu_00",
                "duration_cycles": 25.0, "depends_on": [], "start_cycles": 0.0,
            },
            transfer("t_x", "eu_11", "eu_00"),
        ])
        self.assertEqual(result.status, "complete")
        spans = spans_by_id(result)
        compute, moved = spans["c_00"], spans["t_x"]
        overlap = min(compute.end_cycles, moved.end_cycles) - max(
            compute.start_cycles, moved.start_cycles
        )
        self.assertGreater(overlap, 0.0)
        self.assertEqual(moved.end_cycles, 22.0)
        self.assertEqual(result.completion_cycles, 25.0)
        units = {r.resource_id: r for r in result.resources if r.kind == "execution_unit"}
        self.assertEqual(units["eu_00"].busy_cycles, 25.0)
        self.assertEqual(units["eu_00"].utilization, 1.0)
        links = {r.resource_id: r for r in result.resources if r.kind == "link"}
        self.assertEqual(links["net_alpha/al_11n10"].busy_cycles, 10.0)

    def test_wait_completes_only_after_signals(self):
        result, _ = run(
            [
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
        self.assertEqual(result.status, "complete")
        spans = spans_by_id(result)
        wait = spans["w_1"]
        self.assertEqual(wait.start_cycles, 0.0)
        self.assertEqual(wait.end_cycles, 10.0)
        self.assertGreater(wait.end_cycles, spans["s_1"].end_cycles)
        self.assertEqual(wait.end_cycles, spans["s_2"].end_cycles)
        counters = {c.counter_id: c for c in result.counters}
        self.assertEqual(counters["barrier"].value, 2)
        self.assertEqual(counters["barrier"].updates, 2)

    def test_dma_transfer_to_memory_endpoint(self):
        result, _ = run([transfer("dma_1", "dma_a", "mem_b_ep", payload=50)])
        self.assertEqual(result.status, "complete")
        span = spans_by_id(result)["dma_1"]
        self.assertEqual(span.end_cycles, 18.0)
        self.assertEqual([hop.link_id for hop in span.hops], ["al_00e10", "al_10s11", "al_11e21"])
        links = {r.resource_id: r for r in result.resources}
        self.assertEqual(links["net_alpha/al_00e10"].service_count, 1)
        self.assertEqual(links["net_alpha/al_00e10"].busy_cycles, 5.0)

    def test_dependency_orders_transactions(self):
        result, _ = run([
            transfer("first", "eu_00", "mem_b_ep"),
            transfer("second", "dma_a", "mem_b_ep", depends_on=["first"]),
        ])
        spans = spans_by_id(result)
        self.assertEqual(spans["first"].end_cycles, 33.0)
        self.assertGreaterEqual(spans["second"].start_cycles, 33.0)
        self.assertEqual(spans["second"].end_cycles, 66.0)


class TestGenericRuntimeResults(unittest.TestCase):
    def test_result_contract(self):
        result, parsed = run([
            transfer("t_a", "eu_00", "mem_b_ep"),
            {
                "transaction_id": "c_00", "kind": "compute", "unit_id": "eu_10",
                "duration_cycles": 7.0, "depends_on": [], "start_cycles": 0.0,
            },
        ])
        self.assertEqual(result.kind, "generic_simulation_result")
        self.assertEqual(result.schema_version, 1)
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.reason, "drained")
        self.assertEqual(result.graph_sha256, system().generic_sha256)
        self.assertEqual(
            result.batch_sha256, content_digest(parsed.model_dump(mode="json"))
        )
        self.assertEqual(
            [span.transaction_id for span in result.transactions], ["t_a", "c_00"]
        )
        # 14 alpha links + 6 beta links + 3 execution units are all reported.
        self.assertEqual(len(result.resources), 23)
        self.assertEqual(result.completion_cycles, 33.0)
        self.assertEqual(result.execution, "generic_packet_transport")
        self.assertEqual(result.silicon_timing, "unvalidated")

    def test_deterministic_repeat_runs(self):
        first, _ = run([transfer("t_a", "eu_00", "mem_b_ep")])
        second, _ = run([transfer("t_a", "eu_00", "mem_b_ep")])
        self.assertEqual(first.model_dump(mode="json"), second.model_dump(mode="json"))

    def test_unsatisfiable_wait_is_incomplete(self):
        result, _ = run(
            [
                {
                    "transaction_id": "w_9", "kind": "wait", "counter_id": "barrier",
                    "threshold": 5, "depends_on": [], "start_cycles": 0.0,
                },
            ],
            counters=[{"counter_id": "barrier", "initial_value": 0}],
            max_cycles=50.0,
        )
        self.assertEqual(result.status, "incomplete")
        self.assertEqual(result.reason, "transactions_incomplete")
        span = spans_by_id(result)["w_9"]
        self.assertEqual(span.status, "incomplete")
        self.assertEqual(span.reason, "cycle_limit")
        self.assertIsNone(span.end_cycles)
        self.assertIsNone(result.completion_cycles)

    def test_failed_dependency_is_reported(self):
        result, _ = run(
            [
                {
                    "transaction_id": "w_3", "kind": "wait", "counter_id": "barrier",
                    "threshold": 5, "depends_on": [], "start_cycles": 0.0,
                },
                transfer("t_x", "eu_00", "mem_b_ep", depends_on=["w_3"]),
            ],
            counters=[{"counter_id": "barrier", "initial_value": 0}],
            max_cycles=50.0,
        )
        spans = spans_by_id(result)
        self.assertEqual(spans["w_3"].reason, "cycle_limit")
        self.assertEqual(spans["t_x"].reason, "dependency_unsatisfied")

    def test_cycle_limit_marks_late_transactions(self):
        result, _ = run(
            [transfer("t_late", "eu_00", "mem_b_ep", start_cycles=500.0)],
            max_cycles=100.0,
        )
        span = spans_by_id(result)["t_late"]
        self.assertEqual(span.status, "incomplete")
        self.assertEqual(span.reason, "cycle_limit")


class TestGenericBatchAdmission(unittest.TestCase):
    def expect_invalid(self, mutate):
        document = batch([transfer("t_a", "eu_00", "mem_b_ep")])
        mutate(document)
        with self.assertRaises(ValidationError):
            GenericTransactionBatch.model_validate(document)

    def test_unknown_kind_rejected(self):
        def mutate(document):
            document["transactions"][0]["kind"] = "teleport"
        self.expect_invalid(mutate)

    def test_undeclared_counter_rejected(self):
        def mutate(document):
            document["transactions"][0] = {
                "transaction_id": "w_1", "kind": "wait", "counter_id": "ghost",
                "threshold": 1, "depends_on": [], "start_cycles": 0.0,
            }
        self.expect_invalid(mutate)

    def test_unknown_dependency_rejected(self):
        self.expect_invalid(lambda document: document["transactions"][0].update(
            {"depends_on": ["ghost"]}))

    def test_dependency_cycle_rejected(self):
        def mutate(document):
            document["transactions"] = [
                {
                    "transaction_id": "a", "kind": "compute", "unit_id": "eu_00",
                    "duration_cycles": 1.0, "depends_on": ["b"], "start_cycles": 0.0,
                },
                {
                    "transaction_id": "b", "kind": "compute", "unit_id": "eu_00",
                    "duration_cycles": 1.0, "depends_on": ["a"], "start_cycles": 0.0,
                },
            ]
        self.expect_invalid(mutate)

    def test_transfer_without_timing_rejected(self):
        self.expect_invalid(lambda document: document["transactions"][0].update(
            {"network_id": "net_gamma"}))

    def test_empty_batch_rejected(self):
        self.expect_invalid(lambda document: document.update({"transactions": []}))

    def test_device_vocabulary_rejected(self):
        self.expect_invalid(lambda document: document["transactions"][0].update(
            {"transaction_id": "wormhole-tx"}))

    def test_unknown_fields_rejected(self):
        self.expect_invalid(lambda document: document["transactions"][0].update(
            {"vendor_clock_mhz": 1000}))

    def test_runtime_rejections(self):
        # No route exists from eu_00 to dma_b in net_alpha.
        with self.assertRaises(ValueError):
            run([transfer("t_bad", "eu_00", "dma_b")])
        # Unknown execution unit.
        with self.assertRaises(ValueError):
            run([{
                "transaction_id": "c_bad", "kind": "compute", "unit_id": "eu_99",
                "duration_cycles": 1.0, "depends_on": [], "start_cycles": 0.0,
            }])
        # Timing override for an unknown link.
        timing_doc = timing()
        timing_doc[0]["overrides"] = [{
            "link_id": "no_such_link",
            "timing": {
                "bytes_per_cycle": 1, "hop_cycles": 1.0,
                "credit_return_cycles": 1.0, "buffer_slots": 1,
            },
        }]
        parsed = GenericTransactionBatch.model_validate(
            batch([transfer("t_a", "eu_00", "mem_b_ep")], timing_doc=timing_doc)
        )
        with self.assertRaises(ValueError):
            run_generic_batch(system(), parsed)
        # Timing declared for an unknown network (net_alpha stays timed).
        timing_doc = timing()
        timing_doc.append({
            "network_id": "net_gamma",
            "link": {
                "bytes_per_cycle": 1, "hop_cycles": 1.0,
                "credit_return_cycles": 1.0, "buffer_slots": 1,
            },
            "overrides": [],
        })
        document = batch([transfer("t_a", "eu_00", "mem_b_ep")], timing_doc=timing_doc)
        parsed = GenericTransactionBatch.model_validate(document)
        with self.assertRaises(ValueError):
            run_generic_batch(system(), parsed)


if __name__ == "__main__":
    unittest.main()

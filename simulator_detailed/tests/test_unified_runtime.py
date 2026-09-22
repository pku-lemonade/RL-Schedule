"""Public acceptance suite for the unified compile/runtime pipeline.

Independently designed synthetic cases only; no device-era data. Items map to
the published acceptance list: parallelism, deterministic queueing,
wait-after-signal, deadlock freedom, release after cancellation, compile-time
reference checks, never-satisfiable waits, byte-identical repeats and the
unchanged phase-1 suite (executed separately in full).
"""

import json
import unittest
from pathlib import Path

from simulator_detailed.configs.schemas.system_spec import SystemSpec
from simulator_detailed.runtime_context import RuntimeContext
from simulator_detailed.system_compile import compile_system

ROOT = Path(__file__).resolve().parents[1]


def ring_graph():
    """Three-node directed ring with two-hop routes in cyclic order."""
    return {
        "kind": "generic_system_graph",
        "schema_version": 1,
        "system_id": "synthetic-ring-3",
        "nodes": [
            {"node_id": "r0", "x": 0, "y": 0, "role": "compute"},
            {"node_id": "r1", "x": 1, "y": 0, "role": "compute"},
            {"node_id": "r2", "x": 2, "y": 0, "role": "compute"},
        ],
        "networks": [{"network_id": "ring"}],
        "ports": [
            {"port_id": "out0", "node_id": n, "network_id": "ring", "kind": "network"}
            for n in ("r0", "r1", "r2")
        ] + [
            {"port_id": "in0", "node_id": n, "network_id": "ring", "kind": "network"}
            for n in ("r0", "r1", "r2")
        ] + [
            {"port_id": "loc0", "node_id": n, "network_id": "ring", "kind": "local"}
            for n in ("r0", "r1", "r2")
        ],
        "links": [
            {"link_id": "l01", "network_id": "ring", "src_node": "r0", "src_port": "out0", "dst_node": "r1", "dst_port": "in0"},
            {"link_id": "l12", "network_id": "ring", "src_node": "r1", "src_port": "out0", "dst_node": "r2", "dst_port": "in0"},
            {"link_id": "l20", "network_id": "ring", "src_node": "r2", "src_port": "out0", "dst_node": "r0", "dst_port": "in0"},
        ],
        "dma_endpoints": [],
        "execution_units": [
            {"unit_id": "eu0", "node_id": "r0", "network_id": "ring", "port_id": "loc0"},
            {"unit_id": "eu1", "node_id": "r1", "network_id": "ring", "port_id": "loc0"},
            {"unit_id": "eu2", "node_id": "r2", "network_id": "ring", "port_id": "loc0"},
        ],
        "memory_resources": [],
        "static_routes": [
            {"network_id": "ring", "source": "eu0", "destination": "eu2", "link_ids": ["l01", "l12"]},
            {"network_id": "ring", "source": "eu1", "destination": "eu0", "link_ids": ["l12", "l20"]},
            {"network_id": "ring", "source": "eu2", "destination": "eu1", "link_ids": ["l20", "l01"]},
        ],
    }


def ring_batch(transactions, counters=(), max_cycles=100.0, bytes_per_cycle=10, credit_return=5.0):
    return {
        "kind": "generic_transaction_batch",
        "schema_version": 1,
        "batch_id": "ring-batch",
        "graph_path": None,
        "timing": [{
            "network_id": "ring",
            "link": {
                "bytes_per_cycle": bytes_per_cycle,
                "hop_cycles": 0.0,
                "credit_return_cycles": credit_return,
                "buffer_slots": 1,
            },
            "overrides": [],
        }],
        "counters": list(counters),
        "transactions": list(transactions),
        "max_cycles": max_cycles,
    }


def make_context(transactions, counters=(), max_cycles=100.0, **timing):
    spec = SystemSpec.model_validate({
        "kind": "system_spec",
        "schema_version": 1,
        "spec_id": "ring-spec",
        "graph": ring_graph(),
        "batch": ring_batch(transactions, counters, max_cycles, **timing),
    })
    return RuntimeContext(compile_system(spec))


def transfer(tx_id, source, destination, payload=10, **extra):
    return {
        "transaction_id": tx_id, "kind": "transfer", "network_id": "ring",
        "source": source, "destination": destination, "payload_bytes": payload,
        "depends_on": [], "start_cycles": 0.0, **extra,
    }


def spans_by_id(result):
    return {span.transaction_id: span for span in result.transactions}


class TestParallelismAndQueueing(unittest.TestCase):
    def test_transfer_and_compute_on_distinct_resources_overlap(self):
        ctx = make_context([
            transfer("t_move", "eu0", "eu2"),
            {
                "transaction_id": "c_work", "kind": "compute", "unit_id": "eu2",
                "duration_cycles": 30.0, "depends_on": [], "start_cycles": 0.0,
            },
        ])
        result = ctx.run()
        self.assertEqual(result.status, "complete")
        spans = spans_by_id(result)
        overlap = min(spans["t_move"].end_cycles, spans["c_work"].end_cycles) - max(
            spans["t_move"].start_cycles, spans["c_work"].start_cycles
        )
        self.assertGreater(overlap, 0.0)

    def test_two_transfers_queue_deterministically(self):
        ctx = make_context([
            transfer("t_first", "eu0", "eu2"),
            transfer("t_second", "eu0", "eu2"),
        ])
        result = ctx.run()
        self.assertEqual(result.status, "complete")
        spans = spans_by_id(result)
        first, second = spans["t_first"], spans["t_second"]
        self.assertEqual(first.hops[0].serialization_start_cycles, 0.0)
        self.assertGreaterEqual(
            second.hops[0].serialization_start_cycles,
            first.hops[0].serialization_end_cycles,
        )
        self.assertLess(first.end_cycles, second.end_cycles)

    def test_wait_completes_after_signal(self):
        ctx = make_context(
            [
                {
                    "transaction_id": "s_go", "kind": "signal", "counter_id": "gate",
                    "delta": 1, "depends_on": [], "start_cycles": 8.0,
                },
                {
                    "transaction_id": "w_gate", "kind": "wait", "counter_id": "gate",
                    "threshold": 1, "depends_on": [], "start_cycles": 0.0,
                },
            ],
            counters=[{"counter_id": "gate", "initial_value": 0}],
        )
        result = ctx.run()
        self.assertEqual(result.status, "complete")
        spans = spans_by_id(result)
        self.assertEqual(spans["w_gate"].end_cycles, 8.0)
        self.assertGreaterEqual(spans["w_gate"].end_cycles, spans["s_go"].end_cycles)


class TestDeadlockFreedom(unittest.TestCase):
    def test_ring_transfers_complete_without_deadlock(self):
        # buffer_slots=1 and delayed credit return create a cyclic
        # hold-and-wait; time-triggered releases break it structurally.
        ctx = make_context(
            [
                transfer("t_a", "eu0", "eu2"),
                transfer("t_b", "eu1", "eu0"),
                transfer("t_c", "eu2", "eu1"),
            ],
            max_cycles=500.0,
        )
        result = ctx.run()
        self.assertEqual(result.status, "complete")
        spans = spans_by_id(result)
        for tx_id in ("t_a", "t_b", "t_c"):
            self.assertEqual(spans[tx_id].status, "complete")
            self.assertEqual(spans[tx_id].reason, "completed")

    def test_sorted_multi_resource_acquisition(self):
        ctx = make_context([transfer("t_idle", "eu0", "eu2", start_cycles=400.0)])
        order: list[str] = []

        def user(name, ids, hold):
            granted = yield from ctx.registry.acquire_all(tuple(ids))
            order.append(f"{name}-acquired")
            yield ctx.env.timeout(hold)
            ctx.registry.release_all(granted)
            order.append(f"{name}-released")

        ctx.env.process(user("u1", ("eu2", "eu1"), 10.0))
        ctx.env.process(user("u2", ("eu1", "eu2"), 10.0))
        ctx.env.run(until=100.0)
        # Both users acquire eu1 before eu2 regardless of declaration order,
        # so u1 finishes before u2 can start and nobody waits forever.
        self.assertEqual(
            order,
            ["u1-acquired", "u1-released", "u2-acquired", "u2-released"],
        )
        self.assertTrue(ctx.registry.all_released())


class TestCancellationReleases(unittest.TestCase):
    def test_interrupted_transfer_releases_everything(self):
        ctx = make_context(
            [transfer("t_huge", "eu0", "eu2", payload=100000)],
            max_cycles=10.0,
            bytes_per_cycle=1,
        )
        result = ctx.run()
        span = spans_by_id(result)["t_huge"]
        self.assertEqual(span.status, "incomplete")
        self.assertEqual(span.reason, "cycle_limit")
        self.assertTrue(ctx.registry.all_released())
        cancels = [event for event in result.trace if event.action == "cancel"]
        self.assertEqual([event.transaction_id for event in cancels], ["t_huge"])

    def test_interrupted_wait_leaves_no_pending(self):
        ctx = make_context(
            [
                {
                    "transaction_id": "w_stuck", "kind": "wait", "counter_id": "gate",
                    "threshold": 9, "depends_on": [], "start_cycles": 0.0,
                },
            ],
            counters=[{"counter_id": "gate", "initial_value": 0}],
            max_cycles=10.0,
        )
        ctx.run()
        self.assertTrue(ctx.registry.all_released())


class TestExplicitFailures(unittest.TestCase):
    def test_illegal_reference_fails_at_compile(self):
        with self.assertRaises(ValueError):
            make_context([transfer("t_ghost", "eu0", "no_such_ep")])
        with self.assertRaises(ValueError):
            make_context([{
                "transaction_id": "c_ghost", "kind": "compute", "unit_id": "eu_99",
                "duration_cycles": 1.0, "depends_on": [], "start_cycles": 0.0,
            }])

    def test_never_satisfiable_wait_reports_reason(self):
        ctx = make_context(
            [
                {
                    "transaction_id": "s_small", "kind": "signal", "counter_id": "gate",
                    "delta": 1, "depends_on": [], "start_cycles": 1.0,
                },
                {
                    "transaction_id": "w_big", "kind": "wait", "counter_id": "gate",
                    "threshold": 5, "depends_on": [], "start_cycles": 0.0,
                },
            ],
            counters=[{"counter_id": "gate", "initial_value": 0}],
            max_cycles=20.0,
        )
        result = ctx.run()
        span = spans_by_id(result)["w_big"]
        self.assertEqual(span.status, "incomplete")
        self.assertEqual(span.reason, "cycle_limit")
        entry = next(e for e in result.errors if e.transaction_id == "w_big")
        self.assertIn("can never be satisfied", entry.message)
        self.assertIn("5", entry.message)
        self.assertIn("1", entry.message)


class TestByteIdenticalRepeats(unittest.TestCase):
    def test_repeated_runs_are_identical(self):
        transactions = [
            transfer("t_a", "eu0", "eu2"),
            transfer("t_b", "eu0", "eu2"),
            {
                "transaction_id": "s_1", "kind": "signal", "counter_id": "gate",
                "delta": 1, "depends_on": [], "start_cycles": 3.0,
            },
            {
                "transaction_id": "w_1", "kind": "wait", "counter_id": "gate",
                "threshold": 1, "depends_on": [], "start_cycles": 0.0,
            },
        ]
        counters = [{"counter_id": "gate", "initial_value": 0}]
        first_spec = SystemSpec.model_validate({
            "kind": "system_spec", "schema_version": 1, "spec_id": "ring-spec",
            "graph": ring_graph(), "batch": ring_batch(transactions, counters),
        })
        second_spec = SystemSpec.model_validate(json.loads(json.dumps(first_spec.model_dump(mode="json"))))
        first_plan = compile_system(first_spec)
        second_plan = compile_system(second_spec)
        self.assertEqual(first_plan.plan_sha256, second_plan.plan_sha256)
        first = RuntimeContext(first_plan).run()
        second = RuntimeContext(second_plan).run()
        self.assertEqual(first.model_dump(mode="json"), second.model_dump(mode="json"))
        self.assertEqual(
            [t.transaction_id for t in first.transactions],
            ["t_a", "t_b", "s_1", "w_1"],
        )
        self.assertEqual(
            [(e.sequence, e.time_cycles, e.action) for e in first.trace],
            [(e.sequence, e.time_cycles, e.action) for e in second.trace],
        )


if __name__ == "__main__":
    unittest.main()

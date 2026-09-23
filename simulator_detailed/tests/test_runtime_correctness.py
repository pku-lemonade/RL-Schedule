"""Correctness regression suite: atomic acquisition, cancellation/drain,
runtime abort and endpoint network membership.

Independently designed synthetic graphs only. Each test maps to a published
acceptance case of the generic-runtime-correctness change; every one of the
defect-targeted tests fails against the unfixed runtime.
"""

import unittest

import simpy

from simulator_detailed.configs.schemas.system_spec import SystemSpec
from simulator_detailed.runtime_context import RuntimeContext
from simulator_detailed.system_compile import compile_system


def cluster_graph(command=1.0, latency=2.0, chan_rate=1, stripe=64):
    """Two compute nodes with one unit each plus one hierarchical memory.

    The port->channel binding is crossed on purpose: stripes 0/2 map to
    bank_0 with channels ch0/ch1 and stripes 1/3 map to bank_1 with
    ch1/ch0, so independent bank/channel contention is expressible.
    """
    return {
        "kind": "generic_system_graph",
        "schema_version": 1,
        "system_id": "synthetic-cluster-rc",
        "nodes": [
            {"node_id": "n0", "x": 0, "y": 0, "role": "compute"},
            {"node_id": "n1", "x": 1, "y": 0, "role": "compute"},
            {"node_id": "m0", "x": 2, "y": 0, "role": "memory"},
        ],
        "networks": [{"network_id": "net"}],
        "ports": [
            {"port_id": "e", "node_id": "n0", "network_id": "net", "kind": "network"},
            {"port_id": "w", "node_id": "n0", "network_id": "net", "kind": "network"},
            {"port_id": "e", "node_id": "n1", "network_id": "net", "kind": "network"},
            {"port_id": "w", "node_id": "n1", "network_id": "net", "kind": "network"},
            {"port_id": "e", "node_id": "m0", "network_id": "net", "kind": "network"},
            {"port_id": "w", "node_id": "m0", "network_id": "net", "kind": "network"},
            {"port_id": "la", "node_id": "n0", "network_id": "net", "kind": "local"},
            {"port_id": "lb", "node_id": "n1", "network_id": "net", "kind": "local"},
            {"port_id": "lm", "node_id": "m0", "network_id": "net", "kind": "local"},
        ],
        "links": [
            {"link_id": "l_am", "network_id": "net", "src_node": "n0", "src_port": "e", "dst_node": "m0", "dst_port": "w"},
            {"link_id": "l_ma", "network_id": "net", "src_node": "m0", "src_port": "w", "dst_node": "n0", "dst_port": "w"},
            {"link_id": "l_bm", "network_id": "net", "src_node": "n1", "src_port": "e", "dst_node": "m0", "dst_port": "e"},
            {"link_id": "l_mb", "network_id": "net", "src_node": "m0", "src_port": "e", "dst_node": "n1", "dst_port": "w"},
        ],
        "dma_endpoints": [],
        "execution_units": [
            {"unit_id": "eu_a", "node_id": "n0", "network_id": "net", "port_id": "la"},
            {"unit_id": "eu_b", "node_id": "n1", "network_id": "net", "port_id": "lb"},
        ],
        "memory_resources": [
            {
                "resource_id": "mem_x", "owner_node": "m0", "capacity_bytes": 8192,
                "endpoint_id": "mem_x_ep", "network_id": "net", "port_id": "lm",
                "hierarchy": {
                    "banks": 2,
                    "stripe_bytes": stripe,
                    "latency_cycles": latency,
                    "ports": [
                        {"port_id": "p0", "channel_id": "ch0", "command_cycles": command},
                        {"port_id": "p1", "channel_id": "ch1", "command_cycles": command},
                        {"port_id": "p2", "channel_id": "ch1", "command_cycles": command},
                        {"port_id": "p3", "channel_id": "ch0", "command_cycles": command},
                    ],
                    "channels": [
                        {"channel_id": "ch0", "bytes_per_cycle": chan_rate},
                        {"channel_id": "ch1", "bytes_per_cycle": chan_rate},
                    ],
                },
            },
        ],
        "static_routes": [
            {"network_id": "net", "source": "eu_a", "destination": "mem_x_ep", "link_ids": ["l_am"]},
            {"network_id": "net", "source": "mem_x_ep", "destination": "eu_a", "link_ids": ["l_ma"]},
            {"network_id": "net", "source": "eu_b", "destination": "mem_x_ep", "link_ids": ["l_bm"]},
            {"network_id": "net", "source": "mem_x_ep", "destination": "eu_b", "link_ids": ["l_mb"]},
        ],
    }


def cluster_batch(transactions, max_cycles=1000.0, credit_return=1.0):
    return {
        "kind": "generic_transaction_batch",
        "schema_version": 1,
        "batch_id": "cluster-batch",
        "graph_path": None,
        "timing": [{
            "network_id": "net",
            "link": {
                "bytes_per_cycle": 10, "hop_cycles": 1.0,
                "credit_return_cycles": credit_return, "buffer_slots": 4,
            },
            "overrides": [],
        }],
        "counters": [],
        "transactions": list(transactions),
        "max_cycles": max_cycles,
    }


def make_cluster_ctx(transactions, max_cycles=1000.0, credit_return=1.0, **graph_kw):
    spec = SystemSpec.model_validate({
        "kind": "system_spec", "schema_version": 1, "spec_id": "cluster-spec",
        "graph": cluster_graph(**graph_kw),
        "batch": cluster_batch(transactions, max_cycles, credit_return),
    })
    return RuntimeContext(compile_system(spec))


def write(tx_id, address, payload=64, **extra):
    return {
        "transaction_id": tx_id, "kind": "transfer", "network_id": "net",
        "source": "eu_a", "destination": "mem_x_ep", "payload_bytes": payload,
        "address": address, "depends_on": [], "start_cycles": 0.0, **extra,
    }


def compute(tx_id, unit, duration, **extra):
    return {
        "transaction_id": tx_id, "kind": "compute", "unit_id": unit,
        "duration_cycles": duration, "depends_on": [], "start_cycles": 0.0, **extra,
    }


def idle_ctx():
    """A context whose only transaction parks far in the future."""
    return make_cluster_ctx(
        [compute("t_park", "eu_b", 1.0, start_cycles=900.0)], max_cycles=950.0,
    )


def spans_by_id(result):
    return {span.transaction_id: span for span in result.transactions}


class TestAtomicAcquisition(unittest.TestCase):
    """Fix 1: all-or-nothing multi-resource acquisition and validation."""

    def test_waiting_set_holds_nothing(self):
        ctx = idle_ctx()
        events: list[tuple[float, str]] = []

        def holder():
            # Holds the alphabetically-later unit, so a sequential acquirer
            # would grab eu_a and then wait on eu_b while holding eu_a.
            granted = yield from ctx.registry.acquire_all(("eu_b",))
            events.append((float(ctx.env.now), "holder-acquired"))
            yield ctx.env.timeout(100.0)
            ctx.registry.release_all(granted)
            events.append((float(ctx.env.now), "holder-released"))

        def pair():
            yield ctx.env.timeout(1.0)
            granted = yield from ctx.registry.acquire_all(("eu_a", "eu_b"))
            events.append((float(ctx.env.now), "pair-acquired"))
            ctx.registry.release_all(granted)

        def solo():
            yield ctx.env.timeout(2.0)
            # eu_a is free the whole time; a partial holder of eu_a would
            # block us until the holder's long service ends.
            granted = yield from ctx.registry.acquire_all(("eu_a",))
            events.append((float(ctx.env.now), "solo-acquired"))
            ctx.registry.release_all(granted)

        ctx.env.process(holder())
        ctx.env.process(pair())
        ctx.env.process(solo())
        ctx.env.run(until=200.0)
        self.assertIn((2.0, "solo-acquired"), events)
        self.assertIn((100.0, "pair-acquired"), events)
        self.assertTrue(ctx.registry.all_released())

    def test_unknown_identity_rejected_before_any_request(self):
        ctx = idle_ctx()
        errors: list[str] = []

        def bad():
            try:
                yield from ctx.registry.acquire_all(("eu_a", "no_such_resource"))
            except ValueError as exc:
                errors.append(str(exc))

        ctx.env.process(bad())
        ctx.env.run(until=50.0)
        self.assertEqual(len(errors), 1)
        self.assertTrue(ctx.registry.all_released())

    def test_duplicate_identity_rejected(self):
        ctx = idle_ctx()
        errors: list[str] = []

        def bad():
            try:
                yield from ctx.registry.acquire_all(("eu_a", "eu_a"))
            except ValueError as exc:
                errors.append(str(exc))

        ctx.env.process(bad())
        ctx.env.run(until=50.0)
        self.assertEqual(len(errors), 1)
        self.assertTrue(ctx.registry.all_released())

    def test_empty_set_rejected(self):
        ctx = idle_ctx()
        errors: list[str] = []

        def bad():
            try:
                yield from ctx.registry.acquire_all(())
            except ValueError as exc:
                errors.append(str(exc))

        ctx.env.process(bad())
        ctx.env.run(until=50.0)
        self.assertEqual(len(errors), 1)
        self.assertTrue(ctx.registry.all_released())

    def test_cancel_while_waiting_leaves_nothing(self):
        ctx = idle_ctx()

        def holder():
            granted = yield from ctx.registry.acquire_all(("eu_b",))
            yield ctx.env.timeout(100.0)
            ctx.registry.release_all(granted)

        def waiter():
            try:
                granted = yield from ctx.registry.acquire_all(("eu_a", "eu_b"))
                ctx.registry.release_all(granted)
            except simpy.Interrupt:
                pass

        ctx.env.process(holder())
        pending = ctx.env.process(waiter())
        ctx.env.run(until=5.0)
        pending.interrupt()
        ctx.env.run(until=200.0)
        self.assertTrue(ctx.registry.all_released())

    def test_memory_waiter_holds_no_bank(self):
        """X holds ch1 via bank_1; Y wants {bank_0, ch1}; Z wants {bank_0, ch0}.

        Y must wait for X's channel without holding bank_0, so Z — which
        needs bank_0 but not ch1 — completes before X's long service ends.
        """
        ctx = make_cluster_ctx(
            [
                write("x_long", 64, payload=256),            # bank_1 + ch1
                write("y_blocked", 128, payload=64),         # bank_0 + ch1
                write("z_free", 0, payload=64, start_cycles=30.0),  # bank_0 + ch0
            ],
            max_cycles=2000.0,
        )
        result = ctx.run()
        self.assertEqual(result.status, "complete")
        spans = spans_by_id(result)
        self.assertEqual(spans["x_long"].end_cycles, 286.0)
        self.assertEqual(spans["y_blocked"].end_cycles, 352.0)
        self.assertEqual(spans["z_free"].end_cycles, 108.0)
        self.assertLess(spans["z_free"].end_cycles, spans["x_long"].end_cycles)


class TestMemoryCancellationAndDrain(unittest.TestCase):
    """Fix 2: interrupts propagate, timeouts are not success, drain is real."""

    def test_interrupt_during_command_is_incomplete(self):
        ctx = make_cluster_ctx(
            [write("w_cmd", 0, payload=64)], max_cycles=40.0, command=50.0,
            chan_rate=10,
        )
        result = ctx.run()
        self.assertEqual(result.status, "incomplete")
        span = spans_by_id(result)["w_cmd"]
        self.assertEqual(span.status, "incomplete")
        self.assertEqual(span.reason, "cycle_limit")
        self.assertIsNone(span.end_cycles)
        self.assertTrue(ctx.registry.all_released())
        ends = [e for e in result.trace if e.action == "memory_service_end"]
        self.assertEqual(ends, [])

    def test_interrupt_during_data_wait_and_service(self):
        ctx = make_cluster_ctx(
            [write("x_hold", 64, payload=256), write("y_wait", 128, payload=64)],
            max_cycles=100.0,
        )
        result = ctx.run()
        self.assertEqual(result.status, "incomplete")
        spans = spans_by_id(result)
        for tx_id in ("x_hold", "y_wait"):
            self.assertEqual(spans[tx_id].status, "incomplete")
            self.assertEqual(spans[tx_id].reason, "cycle_limit")
            self.assertIsNone(spans[tx_id].end_cycles)
        self.assertTrue(ctx.registry.all_released())

    def test_simultaneous_port_timeout_leaves_nothing(self):
        ctx = make_cluster_ctx(
            [write("w_first", 0, payload=64), write("w_second", 0, payload=64)],
            max_cycles=40.0,
            command=50.0,
            chan_rate=10,
        )
        result = ctx.run()
        self.assertEqual(result.status, "incomplete")
        spans = spans_by_id(result)
        for tx_id in ("w_first", "w_second"):
            self.assertEqual(spans[tx_id].status, "incomplete")
        self.assertTrue(ctx.registry.all_released())

    def test_delayed_credits_drain_before_return(self):
        ctx = make_cluster_ctx(
            [{
                "transaction_id": "t_plain", "kind": "transfer", "network_id": "net",
                "source": "eu_a", "destination": "mem_x_ep", "payload_bytes": 10,
                "depends_on": [], "start_cycles": 0.0,
            }],
            credit_return=50.0,
        )
        result = ctx.run()
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.reason, "drained")
        # Business completion keeps its meaning: serialization 1 cycle plus
        # 1 hop cycle, unaffected by the long credit return delay.
        self.assertEqual(result.completion_cycles, 2.0)
        releases = [
            e for e in result.trace
            if e.action == "credit_release" and e.resource_id == "net/l_am"
        ]
        self.assertEqual([e.time_cycles for e in releases], [52.0])
        self.assertTrue(ctx.registry.all_released())

    def test_runtime_fault_aborts_and_cleans_up(self):
        class FaultyContext(RuntimeContext):
            def _compute(self, transaction):
                if transaction.transaction_id == "c_bad":
                    yield self.env.timeout(5.0)
                    raise RuntimeError("injected fault")
                yield from super()._compute(transaction)

        spec = SystemSpec.model_validate({
            "kind": "system_spec", "schema_version": 1, "spec_id": "fault-spec",
            "graph": cluster_graph(),
            "batch": cluster_batch(
                [
                    compute("c_bad", "eu_a", 1.0),
                    compute("c_long", "eu_b", 500.0),
                    write("w_flight", 0, payload=256),
                ],
                max_cycles=2000.0,
            ),
        })
        ctx = FaultyContext(compile_system(spec))
        with self.assertRaises(RuntimeError) as raised:
            ctx.run()
        self.assertIn("injected fault", str(raised.exception.__cause__))
        self.assertTrue(ctx.registry.all_released())


def dual_graph(net_b_routing="shortest_path", extra_routes=()):
    """Two networks sharing one node; sharing a node is not bridging."""
    networks = [{"network_id": "net_a"}]
    if net_b_routing == "static_table":
        networks.append({"network_id": "net_b"})
    else:
        networks.append({"network_id": "net_b", "routing": net_b_routing})
    return {
        "kind": "generic_system_graph",
        "schema_version": 1,
        "system_id": "synthetic-dual-net",
        "nodes": [
            {"node_id": "sh", "x": 0, "y": 0, "role": "compute"},
            {"node_id": "a1", "x": 1, "y": 0, "role": "compute"},
            {"node_id": "b1", "x": 0, "y": 1, "role": "compute"},
        ],
        "networks": networks,
        "ports": [
            {"port_id": "pa", "node_id": "sh", "network_id": "net_a", "kind": "network"},
            {"port_id": "la", "node_id": "sh", "network_id": "net_a", "kind": "local"},
            {"port_id": "pb", "node_id": "sh", "network_id": "net_b", "kind": "network"},
            {"port_id": "lb", "node_id": "sh", "network_id": "net_b", "kind": "local"},
            {"port_id": "pa", "node_id": "a1", "network_id": "net_a", "kind": "network"},
            {"port_id": "la", "node_id": "a1", "network_id": "net_a", "kind": "local"},
            {"port_id": "pb", "node_id": "b1", "network_id": "net_b", "kind": "network"},
            {"port_id": "lb", "node_id": "b1", "network_id": "net_b", "kind": "local"},
        ],
        "links": [
            {"link_id": "l_a", "network_id": "net_a", "src_node": "sh", "src_port": "pa", "dst_node": "a1", "dst_port": "pa"},
            {"link_id": "l_b", "network_id": "net_b", "src_node": "sh", "src_port": "pb", "dst_node": "b1", "dst_port": "pb"},
        ],
        "dma_endpoints": [],
        "execution_units": [
            {"unit_id": "eu_a0", "node_id": "sh", "network_id": "net_a", "port_id": "la"},
            {"unit_id": "eu_b0", "node_id": "sh", "network_id": "net_b", "port_id": "lb"},
            {"unit_id": "eu_a1", "node_id": "a1", "network_id": "net_a", "port_id": "la"},
            {"unit_id": "eu_b1", "node_id": "b1", "network_id": "net_b", "port_id": "lb"},
        ],
        "memory_resources": [],
        "static_routes": [
            {"network_id": "net_a", "source": "eu_a0", "destination": "eu_a1", "link_ids": ["l_a"]},
            *extra_routes,
        ],
    }


def dual_batch(transactions, max_cycles=500.0):
    return {
        "kind": "generic_transaction_batch",
        "schema_version": 1,
        "batch_id": "dual-batch",
        "graph_path": None,
        "timing": [
            {
                "network_id": "net_a",
                "link": {"bytes_per_cycle": 10, "hop_cycles": 1.0,
                         "credit_return_cycles": 1.0, "buffer_slots": 2},
                "overrides": [],
            },
            {
                "network_id": "net_b",
                "link": {"bytes_per_cycle": 10, "hop_cycles": 1.0,
                         "credit_return_cycles": 1.0, "buffer_slots": 2},
                "overrides": [],
            },
        ],
        "counters": [],
        "transactions": list(transactions),
        "max_cycles": max_cycles,
    }


def dual_spec(graph, transactions):
    return SystemSpec.model_validate({
        "kind": "system_spec", "schema_version": 1, "spec_id": "dual-spec",
        "graph": graph, "batch": dual_batch(transactions),
    })


class TestEndpointNetworkMembership(unittest.TestCase):
    """Fix 3: routes and transfers may only name attached endpoints."""

    def test_cross_network_static_route_rejected(self):
        graph = dual_graph(net_b_routing="static_table", extra_routes=[
            # eu_a0 is attached to net_a only, yet the route lives on net_b.
            {"network_id": "net_b", "source": "eu_a0", "destination": "eu_b1",
             "link_ids": ["l_b"]},
        ])
        with self.assertRaises(ValueError):
            SystemSpec.model_validate({
                "kind": "system_spec", "schema_version": 1, "spec_id": "bad-route",
                "graph": graph,
                "batch": dual_batch([compute("c_ok", "eu_a0", 1.0)]),
            })

    def test_cross_network_transfer_rejected_dynamic(self):
        graph = dual_graph(net_b_routing="shortest_path")
        spec = dual_spec(graph, [{
            "transaction_id": "t_x", "kind": "transfer", "network_id": "net_b",
            "source": "eu_a0", "destination": "eu_b1", "payload_bytes": 10,
            "depends_on": [], "start_cycles": 0.0,
        }])
        with self.assertRaises(ValueError):
            compile_system(spec)

    def test_cross_network_transfer_rejected_static(self):
        graph = dual_graph(net_b_routing="static_table", extra_routes=[
            {"network_id": "net_b", "source": "eu_b0", "destination": "eu_b1",
             "link_ids": ["l_b"]},
        ])
        spec = dual_spec(graph, [{
            "transaction_id": "t_x", "kind": "transfer", "network_id": "net_b",
            "source": "eu_a0", "destination": "eu_b1", "payload_bytes": 10,
            "depends_on": [], "start_cycles": 0.0,
        }])
        with self.assertRaises(ValueError):
            compile_system(spec)

    def test_same_network_transfers_still_pass(self):
        graph = dual_graph(net_b_routing="shortest_path")
        spec = dual_spec(graph, [
            {
                "transaction_id": "t_a", "kind": "transfer", "network_id": "net_a",
                "source": "eu_a0", "destination": "eu_a1", "payload_bytes": 10,
                "depends_on": [], "start_cycles": 0.0,
            },
            {
                "transaction_id": "t_b", "kind": "transfer", "network_id": "net_b",
                "source": "eu_b0", "destination": "eu_b1", "payload_bytes": 10,
                "depends_on": [], "start_cycles": 0.0,
            },
        ])
        result = RuntimeContext(compile_system(spec)).run()
        self.assertEqual(result.status, "complete")
        spans = spans_by_id(result)
        self.assertEqual(spans["t_a"].end_cycles, 2.0)
        self.assertEqual(spans["t_b"].end_cycles, 2.0)


if __name__ == "__main__":
    unittest.main()

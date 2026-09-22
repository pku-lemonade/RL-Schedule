"""Addressed memory acceptance tests; independently designed synthetic cases."""

import unittest
from pathlib import Path

from simulator_detailed.configs.schemas.system_spec import SystemSpec
from simulator_detailed.runtime_context import RuntimeContext
from simulator_detailed.system_compile import compile_system

ROOT = Path(__file__).resolve().parents[1]


def memory_graph(banks=2, ports=(("p0", "ch0"), ("p1", "ch1")), channels=("ch0", "ch1"),
                 stripe=64, latency=2.0, command=1.0, rate=10, flat=False, capacity=4096):
    hierarchy = None
    if not flat:
        hierarchy = {
            "banks": banks,
            "stripe_bytes": stripe,
            "latency_cycles": latency,
            "ports": [
                {"port_id": port_id, "channel_id": channel_id, "command_cycles": command}
                for port_id, channel_id in ports
            ],
            "channels": [
                {"channel_id": channel_id, "bytes_per_cycle": rate}
                for channel_id in channels
            ],
        }
    return {
        "kind": "generic_system_graph",
        "schema_version": 1,
        "system_id": "synthetic-mem-2node",
        "nodes": [
            {"node_id": "c0", "x": 0, "y": 0, "role": "compute"},
            {"node_id": "m0", "x": 1, "y": 0, "role": "memory"},
        ],
        "networks": [{"network_id": "net"}],
        "ports": [
            {"port_id": "e", "node_id": "c0", "network_id": "net", "kind": "network"},
            {"port_id": "w", "node_id": "c0", "network_id": "net", "kind": "network"},
            {"port_id": "e", "node_id": "m0", "network_id": "net", "kind": "network"},
            {"port_id": "w", "node_id": "m0", "network_id": "net", "kind": "network"},
            {"port_id": "eu0", "node_id": "c0", "network_id": "net", "kind": "local"},
            {"port_id": "mem0", "node_id": "m0", "network_id": "net", "kind": "local"},
        ],
        "links": [
            {"link_id": "l_cm", "network_id": "net", "src_node": "c0", "src_port": "e", "dst_node": "m0", "dst_port": "w"},
            {"link_id": "l_mc", "network_id": "net", "src_node": "m0", "src_port": "e", "dst_node": "c0", "dst_port": "w"},
        ],
        "dma_endpoints": [],
        "execution_units": [
            {"unit_id": "eu_s", "node_id": "c0", "network_id": "net", "port_id": "eu0"},
        ],
        "memory_resources": [
            dict(
                {
                    "resource_id": "mem_x", "owner_node": "m0", "capacity_bytes": capacity,
                    "endpoint_id": "mem_x_ep", "network_id": "net", "port_id": "mem0",
                },
                **({"hierarchy": hierarchy} if hierarchy is not None else {}),
            ),
        ],
        "static_routes": [
            {"network_id": "net", "source": "eu_s", "destination": "mem_x_ep", "link_ids": ["l_cm"]},
            {"network_id": "net", "source": "mem_x_ep", "destination": "eu_s", "link_ids": ["l_mc"]},
        ],
    }


def memory_batch(transactions, max_cycles=1000.0):
    return {
        "kind": "generic_transaction_batch",
        "schema_version": 1,
        "batch_id": "mem-batch",
        "graph_path": None,
        "timing": [{
            "network_id": "net",
            "link": {
                "bytes_per_cycle": 10, "hop_cycles": 1.0,
                "credit_return_cycles": 1.0, "buffer_slots": 4,
            },
            "overrides": [],
        }],
        "counters": [],
        "transactions": list(transactions),
        "max_cycles": max_cycles,
    }


def make_spec(graph, transactions, max_cycles=1000.0):
    return SystemSpec.model_validate({
        "kind": "system_spec", "schema_version": 1, "spec_id": "mem-spec",
        "graph": graph, "batch": memory_batch(transactions, max_cycles),
    })


def run(graph, transactions, max_cycles=1000.0):
    return RuntimeContext(compile_system(make_spec(graph, transactions, max_cycles))).run()


def write(tx_id, address, payload=100, **extra):
    return {
        "transaction_id": tx_id, "kind": "transfer", "network_id": "net",
        "source": "eu_s", "destination": "mem_x_ep", "payload_bytes": payload,
        "address": address, "depends_on": [], "start_cycles": 0.0, **extra,
    }


def read(tx_id, address, payload=100, **extra):
    return {
        "transaction_id": tx_id, "kind": "transfer", "network_id": "net",
        "source": "mem_x_ep", "destination": "eu_s", "payload_bytes": payload,
        "address": address, "depends_on": [], "start_cycles": 0.0, **extra,
    }


def spans_by_id(result):
    return {span.transaction_id: span for span in result.transactions}


class TestReadWriteSemantics(unittest.TestCase):
    def test_write_completes_after_network_plus_service(self):
        result = run(memory_graph(), [write("w_0", 0)])
        self.assertEqual(result.status, "complete")
        span = spans_by_id(result)["w_0"]
        self.assertEqual(span.end_cycles, 24.0)
        service = span.service
        self.assertEqual(service.direction, "write")
        self.assertEqual(service.bank_id, "mem_x/bank_0")
        self.assertEqual(service.port_id, "mem_x/port_p0")
        self.assertEqual(service.channel_id, "mem_x/channel_ch0")
        self.assertEqual(service.command_start_cycles, 11.0)
        self.assertEqual(service.command_end_cycles, 12.0)
        self.assertEqual(service.service_start_cycles, 12.0)
        self.assertEqual(service.service_end_cycles, 24.0)

    def test_read_services_before_network(self):
        result = run(memory_graph(), [read("r_0", 0)])
        self.assertEqual(result.status, "complete")
        span = spans_by_id(result)["r_0"]
        self.assertEqual(span.end_cycles, 24.0)
        service = span.service
        self.assertEqual(service.direction, "read")
        self.assertEqual(service.command_start_cycles, 0.0)
        self.assertEqual(service.service_end_cycles, 13.0)
        self.assertEqual(span.hops[0].queued_cycles, 13.0)
        self.assertEqual(span.hops[0].serialization_start_cycles, 13.0)

    def test_channel_rate_bounds_service_time(self):
        result = run(memory_graph(), [write("w_big", 0, payload=200)])
        service = spans_by_id(result)["w_big"].service
        self.assertEqual(
            service.service_end_cycles - service.service_start_cycles, 22.0
        )


class TestMemoryContention(unittest.TestCase):
    def test_different_banks_overlap(self):
        result = run(memory_graph(), [
            write("w_a", 0, payload=10),
            write("w_b", 64, payload=10),
        ])
        spans = spans_by_id(result)
        a, b = spans["w_a"].service, spans["w_b"].service
        self.assertEqual(a.bank_id, "mem_x/bank_0")
        self.assertEqual(b.bank_id, "mem_x/bank_1")
        overlap = min(a.service_end_cycles, b.service_end_cycles) - max(
            a.service_start_cycles, b.service_start_cycles
        )
        self.assertGreater(overlap, 0.0)

    def test_same_bank_serializes(self):
        result = run(memory_graph(), [
            write("w_a", 0, payload=10),
            write("w_b", 128, payload=10),
        ])
        spans = spans_by_id(result)
        a, b = spans["w_a"].service, spans["w_b"].service
        self.assertEqual(a.bank_id, b.bank_id)
        self.assertGreaterEqual(b.service_start_cycles, a.service_end_cycles)

    def test_single_port_serializes_commands(self):
        graph = memory_graph(banks=4)
        result = run(graph, [
            write("w_a", 0, payload=10),
            write("w_b", 128, payload=10),
        ])
        spans = spans_by_id(result)
        a, b = spans["w_a"].service, spans["w_b"].service
        # Different banks, one shared port: commands must not overlap.
        self.assertNotEqual(a.bank_id, b.bank_id)
        self.assertEqual(a.port_id, b.port_id)
        self.assertGreaterEqual(b.command_start_cycles, a.command_end_cycles)

    def test_shared_channel_serializes_data(self):
        graph = memory_graph(ports=(("p0", "ch0"), ("p1", "ch0")), channels=("ch0",))
        result = run(graph, [
            write("w_a", 0, payload=10),
            write("w_b", 64, payload=10),
        ])
        spans = spans_by_id(result)
        a, b = spans["w_a"].service, spans["w_b"].service
        self.assertNotEqual(a.bank_id, b.bank_id)
        self.assertEqual(a.channel_id, b.channel_id)
        self.assertGreaterEqual(b.service_start_cycles, a.service_end_cycles)

    def test_memory_utilization_reported(self):
        result = run(memory_graph(), [write("w_0", 0)])
        kinds = {r.resource_id: r for r in result.resources}
        self.assertEqual(kinds["mem_x/bank_0"].kind, "memory_bank")
        self.assertEqual(kinds["mem_x/bank_0"].busy_cycles, 12.0)
        self.assertEqual(kinds["mem_x/port_p0"].busy_cycles, 1.0)
        self.assertEqual(kinds["mem_x/channel_ch0"].busy_cycles, 12.0)
        self.assertEqual(kinds["mem_x/bank_1"].busy_cycles, 0.0)


class TestAddressValidation(unittest.TestCase):
    def test_address_on_non_memory_pair_fails(self):
        with self.assertRaises(ValueError):
            run(memory_graph(), [{
                "transaction_id": "t_bad", "kind": "transfer", "network_id": "net",
                "source": "eu_s", "destination": "eu_s", "payload_bytes": 10,
                "address": 0, "depends_on": [], "start_cycles": 0.0,
            }])

    def test_address_on_flat_memory_fails(self):
        with self.assertRaises(ValueError):
            run(memory_graph(flat=True), [write("w_bad", 0)])

    def test_address_range_exceeding_capacity_fails(self):
        with self.assertRaises(ValueError):
            run(memory_graph(), [write("w_bad", 4000, payload=200)])
        # Exactly at capacity compiles.
        plan = compile_system(make_spec(memory_graph(), [write("w_ok", 3996, payload=100)]))
        moved = plan.content.transactions[0]
        self.assertIsNotNone(moved.memory_service)

    def test_plan_mapping_is_deterministic(self):
        first = compile_system(make_spec(memory_graph(), [write("w_0", 64)]))
        second = compile_system(make_spec(memory_graph(), [write("w_0", 64)]))
        self.assertEqual(first.plan_sha256, second.plan_sha256)
        service = first.content.transactions[0].memory_service
        self.assertEqual(service.bank_id, "mem_x/bank_1")
        self.assertEqual(service.port_id, "mem_x/port_p1")
        self.assertEqual(service.channel_id, "mem_x/channel_ch1")

    def test_plan_resources_include_hierarchy(self):
        plan = compile_system(make_spec(memory_graph(), [write("w_0", 0)]))
        kinds = {r.resource_id: r.kind for r in plan.content.resources}
        self.assertEqual(kinds["mem_x/bank_0"], "memory_bank")
        self.assertEqual(kinds["mem_x/port_p0"], "memory_port")
        self.assertEqual(kinds["mem_x/channel_ch0"], "memory_channel")


class TestFlatCompatibility(unittest.TestCase):
    def test_flat_memory_has_no_service_stage(self):
        result = run(memory_graph(flat=True), [
            {
                "transaction_id": "t_flat", "kind": "transfer", "network_id": "net",
                "source": "eu_s", "destination": "mem_x_ep", "payload_bytes": 100,
                "depends_on": [], "start_cycles": 0.0,
            },
        ])
        span = spans_by_id(result)["t_flat"]
        self.assertIsNone(span.service)
        self.assertEqual(span.end_cycles, 11.0)
        kinds = {r.kind for r in result.resources}
        self.assertEqual(kinds, {"link", "execution_unit"})

    def test_repeated_runs_are_identical(self):
        transactions = [write("w_a", 0), read("r_b", 64)]
        first = run(memory_graph(), transactions)
        second = run(memory_graph(), transactions)
        self.assertEqual(first.model_dump(mode="json"), second.model_dump(mode="json"))


if __name__ == "__main__":
    unittest.main()

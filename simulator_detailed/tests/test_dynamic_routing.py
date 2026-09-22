"""Dynamic routing acceptance tests; independently designed synthetic cases."""

import unittest
from pathlib import Path

from pydantic import ValidationError

from simulator_detailed.configs.schemas.generic_graph import GenericSystemGraph
from simulator_detailed.configs.schemas.system_spec import SystemSpec
from simulator_detailed.runtime_context import RuntimeContext
from simulator_detailed.system_compile import compile_system

ROOT = Path(__file__).resolve().parents[1]


def diamond_graph(policy="shortest_path", backward=False, with_memory=False):
    if backward:
        links = [
            {"link_id": "l_ba", "network_id": "dyn", "src_node": "b", "src_port": "out0", "dst_node": "a", "dst_port": "in0"},
            {"link_id": "l_ca", "network_id": "dyn", "src_node": "c", "src_port": "out0", "dst_node": "a", "dst_port": "in1"},
            {"link_id": "l_db", "network_id": "dyn", "src_node": "d", "src_port": "out0", "dst_node": "b", "dst_port": "in0"},
            {"link_id": "l_dc", "network_id": "dyn", "src_node": "d", "src_port": "out1", "dst_node": "c", "dst_port": "in0"},
        ]
    else:
        links = [
            {"link_id": "l_ab", "network_id": "dyn", "src_node": "a", "src_port": "out0", "dst_node": "b", "dst_port": "in0"},
            {"link_id": "l_ac", "network_id": "dyn", "src_node": "a", "src_port": "out1", "dst_node": "c", "dst_port": "in0"},
            {"link_id": "l_bd", "network_id": "dyn", "src_node": "b", "src_port": "out0", "dst_node": "d", "dst_port": "in0"},
            {"link_id": "l_cd", "network_id": "dyn", "src_node": "c", "src_port": "out0", "dst_node": "d", "dst_port": "in1"},
        ]
    nodes = [
        {"node_id": "a", "x": 0, "y": 0, "role": "compute"},
        {"node_id": "b", "x": 1, "y": 0, "role": "compute"},
        {"node_id": "c", "x": 1, "y": 1, "role": "compute"},
        {"node_id": "d", "x": 2, "y": 0, "role": "memory" if with_memory else "compute"},
    ]
    ports = [
        {"port_id": port, "node_id": node, "network_id": "dyn", "kind": "network"}
        for node in ("a", "b", "c", "d")
        for port in ("out0", "out1", "in0", "in1")
    ] + [
        {"port_id": "loc0", "node_id": "a", "network_id": "dyn", "kind": "local"},
        {"port_id": "loc0", "node_id": "d", "network_id": "dyn", "kind": "local"},
    ]
    memory_resources = []
    if with_memory:
        ports.append({"port_id": "mem0", "node_id": "d", "network_id": "dyn", "kind": "local"})
        memory_resources.append({
            "resource_id": "mem_d", "owner_node": "d", "capacity_bytes": 4096,
            "endpoint_id": "mem_d_ep", "network_id": "dyn", "port_id": "mem0",
            "hierarchy": {
                "banks": 2, "stripe_bytes": 64, "latency_cycles": 2.0,
                "ports": [{"port_id": "p0", "channel_id": "ch0", "command_cycles": 1.0}],
                "channels": [{"channel_id": "ch0", "bytes_per_cycle": 10}],
            },
        })
    return {
        "kind": "generic_system_graph",
        "schema_version": 1,
        "system_id": "synthetic-diamond",
        "nodes": nodes,
        "networks": [{"network_id": "dyn", "routing": policy}],
        "ports": ports,
        "links": links,
        "dma_endpoints": [],
        "execution_units": [
            {"unit_id": "eu_a", "node_id": "a", "network_id": "dyn", "port_id": "loc0"},
        ] + (
            []
            if with_memory
            else [{"unit_id": "eu_d", "node_id": "d", "network_id": "dyn", "port_id": "loc0"}]
        ),
        "memory_resources": memory_resources,
        "static_routes": [],
    }


def diamond_batch(transactions, max_cycles=1000.0):
    return {
        "kind": "generic_transaction_batch",
        "schema_version": 1,
        "batch_id": "dyn-batch",
        "graph_path": None,
        "timing": [{
            "network_id": "dyn",
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
        "kind": "system_spec", "schema_version": 1, "spec_id": "dyn-spec",
        "graph": graph, "batch": diamond_batch(transactions, max_cycles),
    })


def run(graph, transactions, max_cycles=1000.0):
    return RuntimeContext(compile_system(make_spec(graph, transactions, max_cycles))).run()


def transfer(tx_id, source="eu_a", destination="eu_d", payload=10, **extra):
    return {
        "transaction_id": tx_id, "kind": "transfer", "network_id": "dyn",
        "source": source, "destination": destination, "payload_bytes": payload,
        "depends_on": [], "start_cycles": 0.0, **extra,
    }


def spans_by_id(result):
    return {span.transaction_id: span for span in result.transactions}


class TestRoutingPolicyValidation(unittest.TestCase):
    def test_static_route_on_dynamic_network_rejected(self):
        document = diamond_graph(policy="adaptive")
        document["static_routes"] = [{
            "network_id": "dyn", "source": "eu_a", "destination": "eu_d",
            "link_ids": ["l_ab", "l_bd"],
        }]
        with self.assertRaises(ValidationError):
            GenericSystemGraph.model_validate(document)

    def test_unknown_policy_rejected(self):
        document = diamond_graph()
        document["networks"] = [{"network_id": "dyn", "routing": "random_walk"}]
        with self.assertRaises(ValidationError):
            GenericSystemGraph.model_validate(document)


class TestShortestPath(unittest.TestCase):
    def test_deterministic_path_and_exact_times(self):
        result = run(diamond_graph("shortest_path"), [transfer("t_go")])
        self.assertEqual(result.status, "complete")
        span = spans_by_id(result)["t_go"]
        self.assertEqual([hop.link_id for hop in span.hops], ["l_ab", "l_bd"])
        self.assertEqual(span.end_cycles, 4.0)
        selects = [e for e in result.trace if e.action == "route_select"]
        self.assertEqual([e.resource_id for e in selects], ["dyn/l_ab", "dyn/l_bd"])
        self.assertEqual([e.detail for e in selects], ["a", "b"])

    def test_compile_distance_tables(self):
        plan = compile_system(make_spec(diamond_graph("shortest_path"), [transfer("t_go")]))
        self.assertEqual(len(plan.content.dynamic_networks), 1)
        table = plan.content.dynamic_networks[0]
        self.assertEqual(table.routing, "shortest_path")
        distances = {
            entry.node: entry.distance
            for entry in table.distances[0].distances
        }
        self.assertEqual(distances, {"a": 2, "b": 1, "c": 1, "d": 0})
        self.assertEqual(table.distances[0].destination_node, "d")
        self.assertEqual(len(table.links), 4)
        moved = plan.content.transactions[0]
        self.assertEqual(moved.routing, "shortest_path")
        self.assertEqual(moved.source_node, "a")
        self.assertEqual(moved.destination_node, "d")

    def test_static_network_has_no_dynamic_tables(self):
        graph = diamond_graph("static_table")
        # Static network without routes: the transfer classifies unreachable.
        result = run(graph, [transfer("t_go")])
        span = spans_by_id(result)["t_go"]
        self.assertEqual(span.reason, "route_unreachable")
        plan = compile_system(make_spec(graph, [transfer("t_go")]))
        self.assertEqual(plan.content.dynamic_networks, ())


class TestAdaptive(unittest.TestCase):
    def test_congested_branch_is_avoided(self):
        result = run(diamond_graph("adaptive"), [
            transfer("t_first"),
            transfer("t_second"),
        ])
        self.assertEqual(result.status, "complete")
        spans = spans_by_id(result)
        self.assertEqual(
            [hop.link_id for hop in spans["t_first"].hops], ["l_ab", "l_bd"]
        )
        self.assertEqual(
            [hop.link_id for hop in spans["t_second"].hops], ["l_ac", "l_cd"]
        )
        selects = [
            (e.transaction_id, e.resource_id)
            for e in result.trace
            if e.action == "route_select"
        ]
        self.assertEqual(selects[0], ("t_first", "dyn/l_ab"))
        self.assertEqual(selects[1], ("t_second", "dyn/l_ac"))

    def test_repeated_adaptive_runs_are_identical(self):
        transactions = [transfer("t_first"), transfer("t_second"), transfer("t_third")]
        first = run(diamond_graph("adaptive"), transactions)
        second = run(diamond_graph("adaptive"), transactions)
        self.assertEqual(first.model_dump(mode="json"), second.model_dump(mode="json"))


class TestReachabilityAndCombination(unittest.TestCase):
    def test_unreachable_dynamic_pair_is_terminal(self):
        result = run(diamond_graph("shortest_path", backward=True), [transfer("t_go")])
        span = spans_by_id(result)["t_go"]
        self.assertEqual(span.status, "incomplete")
        self.assertEqual(span.reason, "route_unreachable")
        entry = next(e for e in result.errors if e.transaction_id == "t_go")
        self.assertEqual(entry.code, "route_unreachable")

    def test_dynamic_path_with_addressed_write(self):
        result = run(diamond_graph("adaptive", with_memory=True), [
            transfer("w_0", destination="mem_d_ep", payload=100, address=0),
        ])
        self.assertEqual(result.status, "complete")
        span = spans_by_id(result)["w_0"]
        self.assertEqual(span.service.direction, "write")
        self.assertEqual(span.service.bank_id, "mem_d/bank_0")
        # Dynamic hops precede the write service window.
        self.assertTrue(span.hops)
        self.assertGreaterEqual(
            span.service.command_start_cycles, span.hops[-1].arrival_cycles
        )


if __name__ == "__main__":
    unittest.main()

"""Instruction program compile tests; independently designed synthetic cases.

Part 1 covers document validation and pure compilation: program structure
rules, operation reference checks, namespaced expansion, terminal
classification, counter bounds and digest determinism. Runtime behavior is
covered by the sequencer tests in Part 2 (same file, later classes).
"""

import tempfile
import unittest
from pathlib import Path

from simulator_detailed.configs.schemas.system_spec import SystemSpec
from simulator_detailed.generic_adapter import scan_forbidden_tokens
from simulator_detailed.runtime_context import RuntimeContext
from simulator_detailed.system_compile import compile_system


def small_graph():
    """Three-node line: two compute units and one flat memory endpoint."""
    return {
        "kind": "generic_system_graph",
        "schema_version": 1,
        "system_id": "synthetic-ip-line",
        "nodes": [
            {"node_id": "n0", "x": 0, "y": 0, "role": "compute"},
            {"node_id": "n1", "x": 1, "y": 0, "role": "compute"},
            {"node_id": "n2", "x": 2, "y": 0, "role": "memory"},
        ],
        "networks": [{"network_id": "net"}],
        "ports": [
            {"port_id": "out0", "node_id": "n0", "network_id": "net", "kind": "network"},
            {"port_id": "in0", "node_id": "n0", "network_id": "net", "kind": "network"},
            {"port_id": "out0", "node_id": "n1", "network_id": "net", "kind": "network"},
            {"port_id": "in0", "node_id": "n1", "network_id": "net", "kind": "network"},
            {"port_id": "in0", "node_id": "n2", "network_id": "net", "kind": "network"},
            {"port_id": "loc0", "node_id": "n0", "network_id": "net", "kind": "local"},
            {"port_id": "loc0", "node_id": "n1", "network_id": "net", "kind": "local"},
            {"port_id": "loc0", "node_id": "n2", "network_id": "net", "kind": "local"},
        ],
        "links": [
            {"link_id": "l01", "network_id": "net", "src_node": "n0", "src_port": "out0", "dst_node": "n1", "dst_port": "in0"},
            {"link_id": "l12", "network_id": "net", "src_node": "n1", "src_port": "out0", "dst_node": "n2", "dst_port": "in0"},
        ],
        "dma_endpoints": [],
        "execution_units": [
            {"unit_id": "eu0", "node_id": "n0", "network_id": "net", "port_id": "loc0"},
            {"unit_id": "eu1", "node_id": "n1", "network_id": "net", "port_id": "loc0"},
        ],
        "memory_resources": [
            {
                "resource_id": "mem_x", "owner_node": "n2", "capacity_bytes": 1024,
                "endpoint_id": "mem_ep", "network_id": "net", "port_id": "loc0",
            },
        ],
        "static_routes": [
            {"network_id": "net", "source": "eu0", "destination": "eu1", "link_ids": ["l01"]},
            {"network_id": "net", "source": "eu1", "destination": "mem_ep", "link_ids": ["l12"]},
        ],
    }


def batch(transactions=(), programs=(), counters=(), max_cycles=1000.0):
    return {
        "kind": "generic_transaction_batch",
        "schema_version": 1,
        "batch_id": "ip-batch",
        "graph_path": None,
        "timing": [{
            "network_id": "net",
            "link": {
                "bytes_per_cycle": 10, "hop_cycles": 1.0,
                "credit_return_cycles": 1.0, "buffer_slots": 2,
            },
            "overrides": [],
        }],
        "counters": list(counters),
        "transactions": list(transactions),
        "programs": list(programs),
        "max_cycles": max_cycles,
    }


def op_compute(op_id, unit="eu0", duration=5.0, **extra):
    return {
        "op_id": op_id, "kind": "compute", "unit_id": unit,
        "duration_cycles": duration, **extra,
    }


def op_transfer(op_id, source="eu0", destination="eu1", payload=10, **extra):
    return {
        "op_id": op_id, "kind": "transfer", "network_id": "net",
        "source": source, "destination": destination, "payload_bytes": payload,
        **extra,
    }


def op_wait(op_id, counter="gate", threshold=1, **extra):
    return {
        "op_id": op_id, "kind": "wait", "counter_id": counter,
        "threshold": threshold, **extra,
    }


def op_signal(op_id, counter="gate", delta=1, **extra):
    return {
        "op_id": op_id, "kind": "signal", "counter_id": counter,
        "delta": delta, **extra,
    }


def program(pid, ops, unit="eu0", start=0.0):
    return {
        "program_id": pid, "unit_id": unit, "start_cycles": start,
        "ops": list(ops),
    }


def compile_batch(batch_doc, graph=None):
    spec = SystemSpec.model_validate({
        "kind": "system_spec", "schema_version": 1, "spec_id": "ip-spec",
        "graph": graph if graph is not None else small_graph(),
        "batch": batch_doc,
    })
    return compile_system(spec)


def idle_tx(tx_id="t_idle"):
    return {
        "transaction_id": tx_id, "kind": "compute", "unit_id": "eu1",
        "duration_cycles": 1.0, "depends_on": [], "start_cycles": 0.0,
    }


class TestProgramValidation(unittest.TestCase):
    def test_programs_only_batch_compiles(self):
        plan = compile_batch(batch(
            programs=[program("p0", [op_compute("o1")])],
        ))
        self.assertEqual(len(plan.content.programs), 1)
        self.assertEqual(plan.content.programs[0].unit_id, "eu0")

    def test_empty_batch_rejected(self):
        with self.assertRaises(ValueError):
            compile_batch(batch())

    def test_duplicate_program_identity_rejected(self):
        with self.assertRaises(ValueError):
            compile_batch(batch(programs=[
                program("p0", [op_compute("a")]),
                program("p0", [op_compute("b")], unit="eu1"),
            ]))

    def test_program_transaction_identity_collision_rejected(self):
        with self.assertRaises(ValueError):
            compile_batch(batch(
                transactions=[idle_tx("p0")],
                programs=[program("p0", [op_compute("a")])],
            ))

    def test_duplicate_op_identity_rejected(self):
        with self.assertRaises(ValueError):
            compile_batch(batch(
                programs=[program("p0", [op_compute("a"), op_compute("a")])],
            ))

    def test_non_earlier_dependencies_rejected(self):
        # Forward reference, self reference and unknown operation all fail.
        with self.assertRaises(ValueError):
            compile_batch(batch(programs=[program("p0", [
                op_compute("a", depends_on=["b"]), op_compute("b"),
            ])]))
        with self.assertRaises(ValueError):
            compile_batch(batch(programs=[program("p0", [
                op_compute("a", depends_on=["a"]),
            ])]))
        with self.assertRaises(ValueError):
            compile_batch(batch(programs=[program("p0", [
                op_compute("a", depends_on=["ghost"]),
            ])]))

    def test_undeclared_counter_op_rejected(self):
        with self.assertRaises(ValueError):
            compile_batch(batch(programs=[program("p0", [op_signal("s")])]))
        with self.assertRaises(ValueError):
            compile_batch(batch(
                programs=[program("p0", [op_wait("w")])],
                counters=[{"counter_id": "other", "initial_value": 0}],
            ))

    def test_untimed_network_op_rejected(self):
        with self.assertRaises(ValueError):
            compile_batch(batch(programs=[program("p0", [
                op_transfer("x", network_id="net_ghost"),
            ])]))

    def test_unknown_fields_rejected(self):
        with self.assertRaises(ValueError):
            compile_batch(batch(programs=[program("p0", [
                op_compute("a", decode_width=4),
            ])]))


class TestProgramCompilation(unittest.TestCase):
    def test_unknown_sequencer_unit_rejected(self):
        with self.assertRaises(ValueError):
            compile_batch(batch(
                programs=[program("p0", [op_compute("a")], unit="eu_ghost")],
            ))

    def test_operation_endpoint_references_checked(self):
        with self.assertRaises(ValueError):
            compile_batch(batch(programs=[program("p0", [
                op_transfer("x", destination="no_such_ep"),
            ])]))

    def test_terminal_operations_classified(self):
        plan = compile_batch(batch(
            transactions=[idle_tx()],
            programs=[program("p0", [
                op_transfer("no_route", destination="mem_ep"),       # eu0->mem_ep: none
                op_transfer("too_big", source="eu1", destination="mem_ep", payload=2048),
            ])],
        ))
        ops = {t.transaction_id: t for t in plan.content.transactions}
        self.assertEqual(ops["p0/no_route"].terminal, "route_unreachable")
        self.assertEqual(ops["p0/too_big"].terminal, "capacity_exceeded")

    def test_expansion_namespacing_and_default_dependencies(self):
        plan = compile_batch(batch(programs=[program("p0", [
            op_compute("a"), op_transfer("b"), op_signal("c"),
        ],)], counters=[{"counter_id": "gate", "initial_value": 0}]))
        ids = [t.transaction_id for t in plan.content.transactions]
        self.assertEqual(ids, ["p0/a", "p0/b", "p0/c"])
        self.assertEqual(plan.content.transactions[0].depends_on, ())
        self.assertEqual(plan.content.transactions[1].depends_on, ("p0/a",))
        self.assertEqual(plan.content.transactions[2].depends_on, ("p0/b",))
        prog = plan.content.programs[0]
        self.assertEqual(
            [(op.op_id, op.transaction_id) for op in prog.ops],
            [("a", "p0/a"), ("b", "p0/b"), ("c", "p0/c")],
        )

    def test_explicit_dependencies_override_default(self):
        plan = compile_batch(batch(programs=[program("p0", [
            op_compute("a"), op_compute("b", depends_on=[]), op_compute("c"),
        ])]))
        ops = {t.transaction_id: t for t in plan.content.transactions}
        self.assertEqual(ops["p0/b"].depends_on, ())
        self.assertEqual(ops["p0/c"].depends_on, ("p0/b",))

    def test_counter_bounds_include_program_signals(self):
        plan = compile_batch(batch(
            counters=[{"counter_id": "gate", "initial_value": 0}],
            transactions=[{
                "transaction_id": "s_ext", "kind": "signal", "counter_id": "gate",
                "delta": 2, "depends_on": [], "start_cycles": 0.0,
            }],
            programs=[program("p0", [op_signal("s", delta=3)])],
        ))
        counter = plan.content.counters[0]
        self.assertEqual(counter.upper_bound, 5)

    def test_compilation_deterministic(self):
        doc = batch(
            counters=[{"counter_id": "gate", "initial_value": 1}],
            transactions=[idle_tx()],
            programs=[
                program("p0", [op_compute("a"), op_transfer("b")]),
                program("p1", [op_wait("w"), op_compute("c", unit="eu1")], unit="eu1"),
            ],
        )
        first = compile_batch(doc)
        second = compile_batch(doc)
        self.assertEqual(first.plan_sha256, second.plan_sha256)
        self.assertEqual(first.spec_sha256, second.spec_sha256)

    def test_program_free_batch_has_no_program_records(self):
        plan = compile_batch(batch(transactions=[idle_tx()]))
        self.assertEqual(plan.content.programs, ())
        self.assertEqual(
            [t.transaction_id for t in plan.content.transactions], ["t_idle"],
        )


class TestProgramRuntime(unittest.TestCase):
    """Sequencer runtime: issue occupancy, overlap, termination, cleanup."""

    def run_batch(self, batch_doc):
        plan = compile_batch(batch_doc)
        return RuntimeContext(plan).run()

    @staticmethod
    def spans(result):
        return {s.transaction_id: s for s in result.transactions}

    def test_serial_chain_timing(self):
        result = self.run_batch(batch(programs=[program("p0", [
            op_compute("a", duration=5.0), op_compute("b", duration=5.0),
        ])]))
        spans = self.spans(result)
        self.assertEqual(spans["p0/a"].status, "complete")
        self.assertEqual(spans["p0/b"].status, "complete")
        self.assertEqual((spans["p0/a"].start_cycles, spans["p0/a"].end_cycles), (0.0, 5.0))
        self.assertEqual((spans["p0/b"].start_cycles, spans["p0/b"].end_cycles), (5.0, 10.0))
        prog = result.programs[0]
        self.assertEqual((prog.status, prog.reason), ("complete", "completed"))
        self.assertEqual((prog.start_cycles, prog.end_cycles), (0.0, 10.0))

    def test_explicit_overlap_inside_program(self):
        # Sequencer on eu1; the long compute occupies eu0 so issue is free.
        result = self.run_batch(batch(programs=[program("p0", [
            op_compute("a", duration=10.0),
            op_compute("b", duration=1.0, unit="eu1", depends_on=[]),
            op_compute("c", duration=1.0, unit="eu1"),
        ], unit="eu1")]))
        spans = self.spans(result)
        self.assertEqual(spans["p0/a"].start_cycles, 0.0)
        self.assertEqual(spans["p0/b"].start_cycles, 0.0)
        self.assertEqual(spans["p0/b"].end_cycles, 1.0)
        self.assertEqual((spans["p0/c"].start_cycles, spans["p0/c"].end_cycles), (1.0, 2.0))
        self.assertEqual(result.programs[0].end_cycles, 10.0)
        self.assertEqual(result.status, "complete")

    def test_issue_occupancy_never_overlaps_on_shared_unit(self):
        result = self.run_batch(batch(programs=[
            program("p0", [
                op_compute("a", duration=1.0, issue_cycles=2.0),
                op_compute("c", duration=1.0, issue_cycles=2.0),
            ]),
            program("p1", [op_compute("b", duration=1.0, issue_cycles=2.0)]),
        ]))
        self.assertEqual(result.status, "complete")
        holder = None
        acquisitions = 0
        for event in result.trace:
            if event.action == "issue_acquire":
                self.assertIsNone(holder)
                holder = event.transaction_id
                acquisitions += 1
            elif event.action == "issue_release":
                self.assertEqual(holder, event.transaction_id)
                holder = None
        self.assertEqual(acquisitions, 3)
        self.assertIsNone(holder)

    def test_wait_signal_across_programs(self):
        result = self.run_batch(batch(
            counters=[{"counter_id": "gate", "initial_value": 0}],
            programs=[
                program("w", [op_wait("w1"), op_compute("c", duration=2.0)], unit="eu1"),
                program("s", [op_signal("s1")], unit="eu0", start=7.0),
            ],
        ))
        spans = self.spans(result)
        self.assertEqual(spans["s/s1"].status, "complete")
        self.assertEqual(spans["w/w1"].status, "complete")
        self.assertGreaterEqual(spans["w/w1"].end_cycles, spans["s/s1"].end_cycles)
        self.assertEqual(
            (spans["w/c"].start_cycles, spans["w/c"].end_cycles),
            (spans["w/w1"].end_cycles, spans["w/w1"].end_cycles + 2.0),
        )
        progs = {p.program_id: p for p in result.programs}
        self.assertEqual(progs["w"].status, "complete")
        self.assertEqual(progs["s"].status, "complete")

    def test_terminal_operation_stops_program(self):
        result = self.run_batch(batch(programs=[program("p0", [
            op_compute("before", duration=1.0),
            op_transfer("no_route", destination="mem_ep"),
            op_compute("after", duration=1.0),
        ])]))
        spans = self.spans(result)
        self.assertEqual(spans["p0/before"].status, "complete")
        self.assertEqual(spans["p0/no_route"].reason, "route_unreachable")
        self.assertEqual(spans["p0/after"].reason, "dependency_unsatisfied")
        prog = result.programs[0]
        self.assertEqual(prog.status, "incomplete")
        self.assertEqual(prog.reason, "route_unreachable")
        self.assertEqual(prog.start_cycles, 0.0)
        self.assertIsNone(prog.end_cycles)

    def test_cycle_limit_interrupts_program_and_releases(self):
        result = self.run_batch(batch(
            max_cycles=10.0,
            programs=[program("p0", [
                op_compute("long", duration=100.0),
                op_compute("never"),
            ])],
        ))
        spans = self.spans(result)
        self.assertEqual(spans["p0/long"].status, "incomplete")
        self.assertEqual(spans["p0/long"].reason, "cycle_limit")
        self.assertEqual(spans["p0/never"].reason, "dependency_unsatisfied")
        prog = result.programs[0]
        self.assertEqual((prog.status, prog.reason), ("incomplete", "cycle_limit"))
        self.assertIsNone(prog.start_cycles)
        self.assertIsNone(prog.end_cycles)
        self.assertTrue(any(
            event.action == "cancel" and event.transaction_id == "p0"
            for event in result.trace
        ))


class TestProgramAcceptance(unittest.TestCase):
    """Acceptance: batch contention, addressed memory, determinism."""

    def run_batch(self, batch_doc, graph=None):
        plan = compile_batch(batch_doc, graph)
        return RuntimeContext(plan).run()

    @staticmethod
    def spans(result):
        return {s.transaction_id: s for s in result.transactions}

    def test_issue_contention_with_batch_transactions(self):
        # Batch compute holds eu0 for 8 cycles; the program's issue waits.
        result = self.run_batch(batch(
            transactions=[{
                "transaction_id": "t0", "kind": "compute", "unit_id": "eu0",
                "duration_cycles": 8.0, "depends_on": [], "start_cycles": 0.0,
            }],
            programs=[program("p0", [
                op_compute("a", unit="eu1", duration=1.0, issue_cycles=2.0),
            ])],
        ))
        spans = self.spans(result)
        self.assertEqual(
            (spans["t0"].start_cycles, spans["t0"].end_cycles), (0.0, 8.0)
        )
        self.assertEqual(
            (spans["p0/a"].start_cycles, spans["p0/a"].end_cycles), (10.0, 11.0)
        )
        acquire = next(
            event for event in result.trace
            if event.action == "issue_acquire" and event.transaction_id == "p0"
        )
        self.assertEqual(acquire.time_cycles, 8.0)
        self.assertEqual(result.status, "complete")

    def test_addressed_memory_composition(self):
        graph = small_graph()
        graph["memory_resources"][0]["hierarchy"] = {
            "banks": 2, "stripe_bytes": 64, "latency_cycles": 3.0,
            "ports": [
                {"port_id": "mp0", "channel_id": "mc0", "command_cycles": 2.0},
            ],
            "channels": [{"channel_id": "mc0", "bytes_per_cycle": 8}],
        }
        result = self.run_batch(batch(programs=[program("p0", [
            op_transfer(
                "w", source="eu1", destination="mem_ep", payload=16, address=128
            ),
            op_compute("c", unit="eu1", duration=2.0),
        ])]), graph)
        spans = self.spans(result)
        write = spans["p0/w"]
        self.assertEqual(write.status, "complete")
        # eu1->mem_ep over l12: serialize 2 (16 B at 10 B/cycle) + 1 hop,
        # then command 2 and data service latency 3 + 2 (16 B at 8 B/cycle).
        self.assertEqual(write.end_cycles, 10.0)
        service = write.service
        self.assertEqual(service.direction, "write")
        self.assertEqual(service.command_start_cycles, 3.0)
        self.assertEqual(service.service_end_cycles, 10.0)
        self.assertEqual(
            (spans["p0/c"].start_cycles, spans["p0/c"].end_cycles), (10.0, 12.0)
        )
        self.assertEqual(result.programs[0].end_cycles, 12.0)
        self.assertEqual(result.status, "complete")

    def test_repeat_runs_byte_identical(self):
        doc = batch(
            counters=[{"counter_id": "gate", "initial_value": 0}],
            programs=[
                program("p0", [
                    op_compute("a", duration=3.0), op_transfer("t", payload=20),
                ]),
                program("p1", [op_signal("s"), op_wait("w")], unit="eu1"),
            ],
        )
        first = self.run_batch(doc)
        second = self.run_batch(doc)
        self.assertEqual(first.status, "complete")
        self.assertEqual(first.model_dump_json(), second.model_dump_json())

    def test_program_free_digest_stability(self):
        absent = batch(transactions=[idle_tx()])
        absent.pop("programs")
        present_empty = batch(transactions=[idle_tx()])
        plan_absent = compile_batch(absent)
        plan_empty = compile_batch(present_empty)
        self.assertEqual(plan_absent.content.programs, ())
        self.assertEqual(plan_empty.content.programs, ())
        self.assertEqual(plan_absent.plan_sha256, plan_empty.plan_sha256)
        self.assertEqual(plan_absent.spec_sha256, plan_empty.spec_sha256)
        # A program-free result carries an empty programs tuple.
        result = RuntimeContext(plan_absent).run()
        self.assertEqual(result.programs, ())
        self.assertEqual(result.status, "complete")


class TestProgramNeutralVocabulary(unittest.TestCase):
    """IP-07: denylist enforcement covers program identities and modules."""

    def test_forbidden_tokens_rejected_in_program_identities(self):
        with self.assertRaises(ValueError):
            compile_batch(batch(programs=[
                program("prog_galaxy", [op_compute("a")]),
            ]))
        with self.assertRaises(ValueError):
            compile_batch(batch(programs=[
                program("p0", [op_compute("op_galaxy")]),
            ]))

    def test_new_public_modules_carry_no_forbidden_tokens(self):
        root = Path(__file__).resolve().parents[1]
        files = [
            root / "configs" / "schemas" / "generic_transactions.py",
            root / "configs" / "schemas" / "system_spec.py",
            root / "system_compile.py",
            root / "runtime_context.py",
            root / "docs" / "generic_simulation.md",
        ]
        self.assertEqual(scan_forbidden_tokens(files), ())

    def test_seeded_violation_detected_in_new_modules(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "runtime_context.py").read_text()
        with tempfile.TemporaryDirectory() as tmp:
            seeded = Path(tmp) / "runtime_context.py"
            seeded.write_text(source + "\n# galaxy\n")
            findings = scan_forbidden_tokens([seeded])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].token, "galaxy")


if __name__ == "__main__":
    unittest.main()

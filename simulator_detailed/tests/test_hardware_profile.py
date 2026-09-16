"""Offline contract tests; synthetic cases are independent of Wormhole dimensions."""

import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, chdir, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from pydantic import ValidationError

from simulator_detailed import run
from simulator_detailed.architecture import Arch
from simulator_detailed.configs.schemas.arch_config import ArchConfig
from simulator_detailed.configs.schemas.failure_configs import FailSlow
from simulator_detailed.configs.schemas.hardware_profile import (
    HardwareProfileConfig,
    resolve_parameters,
)
from simulator_detailed.hardware_profile import (
    UnsupportedHardwareProfileError,
    inspect_profile,
    load_architecture_document,
    override_parameter,
    require_executable_architecture,
)

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "simulator_detailed/configs/profiles/wormhole_b0_n150_assumed.json"
REFERENCE = Path(__file__).parent / "fixtures/wormhole_b0_profile_reference.json"


def evidence(status="assumed"):
    return {
        "status": status,
        "locator": "synthetic test fixture",
        "conditions": "Contract tests only; no hardware evidence.",
        **(
            {"rationale": "Exercise a small configurable device."}
            if status == "assumed"
            else {}
        ),
    }


def literal(value, unit, clock=None):
    return {
        "kind": "literal",
        "value": value,
        "unit": unit,
        "evidence": evidence(),
        **({"clock_parameter": clock} if clock else {}),
    }


def derived(operation, operands, unit):
    return {
        "kind": "derived",
        "operation": operation,
        "operands": operands,
        "unit": unit,
        "evidence": evidence("derived"),
    }


def small_document():
    tiles = [
        {"tile_id": "w0", "x": 0, "y": 0, "role": "worker"},
        {"tile_id": "w1", "x": 1, "y": 0, "role": "worker"},
        {"tile_id": "m0", "x": 0, "y": 1, "role": "memory"},
        {"tile_id": "t0", "x": 1, "y": 1, "role": "transit"},
    ]
    return {
        "kind": "hardware_profile",
        "schema_version": 1,
        "profile_id": "small-assumed",
        "profile_revision": "1",
        "architecture": "synthetic",
        "architecture_revision": "A",
        "asic_id": "chip0",
        "sources": {},
        "parameters": {
            "clock": literal(1_000_000_000, "Hz"),
            "width": literal(32, "bytes_per_cycle", "clock"),
            "delay": literal(9, "cycles", "clock"),
            "l1": literal(64, "bytes"),
            "dram": literal(1024, "bytes"),
            "rate": derived("rate_from_clock", ["width", "clock"], "bytes_per_second"),
            "duration": derived("duration_from_cycles", ["delay", "clock"], "seconds"),
            "total": derived("sum_bytes", ["l1", "dram"], "bytes"),
        },
        "layout": {
            "extent": {"width": 2, "height": 2},
            "tiles": tiles,
            "evidence": evidence(),
        },
        "worker_selection": {
            "enabled_worker_ids": ["w0"],
            "logical_workers": [
                {"worker_index": 0, "logical_x": 0, "logical_y": 0, "tile_id": "w0"}
            ],
            "evidence": evidence(),
        },
        "fabrics": [
            {
                "fabric_id": fabric,
                "extent": {"width": 2, "height": 2},
                "clock_parameter": "clock",
                "topology_policy": "torus_2d",
                "routing_policy": "dimension_order_xy",
                "coordinates": {
                    tile["tile_id"]: {"x": tile["x"], "y": tile["y"]} for tile in tiles
                },
                "evidence": evidence(),
            }
            for fabric in [0, 3]
        ],
        "memory": {
            "resources": [
                {
                    "resource_id": "l0",
                    "kind": "local_sram",
                    "capacity_parameter": "l1",
                    "owner_tile_id": "w0",
                },
                {
                    "resource_id": "l1",
                    "kind": "local_sram",
                    "capacity_parameter": "l1",
                    "owner_tile_id": "w1",
                },
                {"resource_id": "d0", "kind": "dram", "capacity_parameter": "dram"},
            ],
            "evidence": evidence(),
        },
        "attachments": {
            "endpoints": [
                {
                    "endpoint_id": f"{tile['tile_id']}-f{fabric}",
                    "tile_id": tile["tile_id"],
                    "fabric_id": fabric,
                    "resource_ids": [
                        {"w0": "l0", "w1": "l1"}.get(tile["tile_id"], "d0")
                    ],
                }
                for tile in tiles
                for fabric in [0, 3]
            ],
            "evidence": evidence(),
        },
    }


class ProfileSchemaTests(unittest.TestCase):
    def invalid(self, document, pattern):
        with self.assertRaisesRegex(ValidationError, pattern):
            HardwareProfileConfig.model_validate(document)

    def test_generic_profile_has_no_runtime_defaults(self):
        profile = HardwareProfileConfig.model_validate(small_document())
        self.assertFalse(hasattr(profile, "noc"))
        self.assertEqual(profile.layout.extent.width, 2)
        self.assertEqual(len(profile.layout.tiles), 4)
        self.assertEqual([f.fabric_id for f in profile.fabrics], [0, 3])

    def test_identity_versions_strict_ids_and_board_selection(self):
        cases = [
            ("schema_version", 2),
            ("schema_version", True),
            ("schema_version", 1.0),
            ("kind", "other"),
            ("core", {}),
            ("profile_id", " "),
            ("asics", ["a", "b"]),
            ("support", {"can_execute": True}),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                doc = small_document()
                doc[key] = value
                self.invalid(doc, key)
        doc = small_document()
        doc["board"] = {"product": "dual", "asic_count": 2, "selected_asic_index": 1}
        profile = HardwareProfileConfig.model_validate(doc)
        self.assertEqual(len(profile.layout.tiles), 4)
        for index in [2, -1, True, 0.5]:
            doc["board"]["selected_asic_index"] = index
            self.invalid(doc, "selected_asic_index")
        del doc["board"]["selected_asic_index"]
        self.invalid(doc, "selected_asic_index")

    def test_literal_units_numeric_constraints_and_evidence(self):
        for value in [True, float("nan"), float("inf"), -1, "10", 0]:
            doc = small_document()
            doc["parameters"]["clock"]["value"] = value
            self.invalid(doc, "parameters.clock")
        for unit, value in [
            ("bytes", 1.5),
            ("count", 2.0),
            ("bytes_per_cycle", 1.5),
            ("MB", 10),
        ]:
            doc = small_document()
            doc["parameters"]["l1"].update(unit=unit, value=value)
            self.invalid(doc, "parameters.l1")
        doc = small_document()
        del doc["parameters"]["clock"]["evidence"]
        self.invalid(doc, "evidence")
        for status, expected in [
            ("documented", "source_ids"),
            ("calibrated", "measurement"),
            ("derived", "recipe"),
        ]:
            doc = small_document()
            doc["parameters"]["clock"]["evidence"] = evidence(status)
            self.invalid(doc, expected)
        doc = small_document()
        del doc["worker_selection"]["evidence"]["rationale"]
        self.invalid(doc, "rationale")

    def test_source_pins_and_all_evidence_references(self):
        doc = small_document()
        source = {
            "title": "fixture",
            "url": "https://example.com/facts",
            "accessed_on": "2026-09-15",
            "applicability": "tests",
        }
        doc["sources"] = {"s": source}
        self.invalid(doc, "immutable")
        source["revision"] = "main"
        self.invalid(doc, "revision")
        source["revision"] = "a" * 40
        doc["layout"]["evidence"] = {**evidence("documented"), "source_ids": ["s"]}
        HardwareProfileConfig.model_validate(doc)
        doc["attachments"]["endpoints"][0]["evidence"] = {
            **evidence("documented"),
            "source_ids": ["missing"],
        }
        self.invalid(doc, "unknown source_id missing")

    def test_layout_and_selection_reject_invalid_inventory(self):
        for field, value in [
            ("x", 2),
            ("x", True),
            ("x", 0.5),
            ("tile_id", "w1"),
            ("role", "unknown"),
        ]:
            doc = small_document()
            doc["layout"]["tiles"][0][field] = value
            self.invalid(doc, "layout")
        doc = small_document()
        doc["layout"]["tiles"][0]["x"] = 1
        self.invalid(doc, "duplicate")
        doc = small_document()
        doc["layout"]["tiles"].pop()
        self.invalid(doc, "complete extent")
        for worker in ["m0", "unknown"]:
            doc = small_document()
            doc["worker_selection"]["enabled_worker_ids"] = [worker]
            doc["worker_selection"]["logical_workers"][0]["tile_id"] = worker
            self.invalid(doc, "not a physical worker")
        doc = small_document()
        doc["worker_selection"]["enabled_worker_ids"].append("w0")
        self.invalid(doc, "duplicate")
        doc = small_document()
        doc["worker_selection"]["logical_workers"] = []
        self.invalid(doc, "bijectively")

    def test_fabric_resource_and_endpoint_references(self):
        mutations = [
            (lambda d: d["fabrics"][0]["coordinates"].pop("w0"), "complete extent"),
            (
                lambda d: d["fabrics"][0]["coordinates"].update(w0={"x": 1, "y": 0}),
                "duplicate",
            ),
            (
                lambda d: d["fabrics"][0]["coordinates"].update(w0={"x": 3, "y": 0}),
                "outside extent",
            ),
            (lambda d: d["fabrics"][0].update(clock_parameter="l1"), "clock_parameter"),
            (lambda d: d["fabrics"][1].update(fabric_id=0), "duplicate"),
            (
                lambda d: d["memory"]["resources"][0].update(capacity_parameter="rate"),
                "positive bytes",
            ),
            (
                lambda d: d["memory"]["resources"][0].update(owner_tile_id="missing"),
                "owner_tile_id",
            ),
            (
                lambda d: d["attachments"]["endpoints"][0].update(
                    resource_ids=["missing"]
                ),
                "unknown resource",
            ),
            (
                lambda d: d["attachments"]["endpoints"][0].update(resource_ids=["l1"]),
                "another tile",
            ),
            (
                lambda d: d["attachments"]["endpoints"][0].update(fabric_id=99),
                "unknown tile/fabric",
            ),
            (
                lambda d: d["attachments"]["endpoints"][0].update(tile_id="missing"),
                "unknown tile/fabric",
            ),
            (
                lambda d: d["attachments"]["endpoints"][1].update(fabric_id=0),
                "duplicate tile/fabric",
            ),
        ]
        for mutate, error in mutations:
            with self.subTest(error=error):
                doc = small_document()
                mutate(doc)
                self.invalid(doc, error)

    def test_typed_derivations_and_clock_constraints(self):
        profile = HardwareProfileConfig.model_validate(small_document())
        values = resolve_parameters(profile.parameters)
        self.assertEqual(values["rate"], 32_000_000_000)
        self.assertEqual(values["duration"], 9e-9)
        self.assertEqual(values["total"], 1088)
        mutations = [
            (lambda p: p["width"].pop("clock_parameter"), "clock_parameter"),
            (lambda p: p["width"].update(clock_parameter="l1"), "must reference Hz"),
            (lambda p: p["rate"].update(operation="eval"), "operation"),
            (lambda p: p["rate"].update(value=42), "value"),
            (
                lambda p: p["rate"].update(operands=["width", "missing"]),
                "unknown dependency",
            ),
            (lambda p: p["total"].update(operands=["total"]), "cyclic dependency"),
            (lambda p: p["total"].update(operands=["l1", "rate"]), "sum_bytes"),
            (lambda p: p["rate"].update(operands=["clock", "width"]), "recipe units"),
        ]
        for mutate, error in mutations:
            with self.subTest(error=error):
                doc = small_document()
                mutate(doc["parameters"])
                self.invalid(doc, error)
        doc = small_document()
        doc["parameters"]["other_clock"] = literal(1_000_000_000, "Hz")
        doc["parameters"]["rate"]["operands"][1] = "other_clock"
        self.invalid(doc, "incompatible clock references")
        doc["parameters"]["width"]["clock_parameter"] = "other_clock"
        doc["parameters"]["other_clock"]["value"] = 1e308
        self.invalid(doc, "finite")


class ProfileInspectionTests(unittest.TestCase):
    def setUp(self):
        self.profile = HardwareProfileConfig.model_validate(small_document())

    def test_unique_capacities_and_disabled_worker_inventory(self):
        report = inspect_profile(self.profile)
        self.assertEqual(report.counts.physical_tiles, 4)
        self.assertEqual(report.counts.enabled_workers, 1)
        self.assertEqual(report.counts.disabled_workers, 1)
        self.assertEqual(report.counts.endpoints, 8)
        self.assertEqual(report.memory.unique_dram_bytes, 1024)
        self.assertEqual(report.memory.physical_worker_l1_bytes, 128)
        self.assertEqual(report.memory.enabled_worker_l1_bytes, 64)
        self.assertEqual(len(report.memory.resources["d0"].attachment_ids), 4)
        self.assertEqual(len(report.memory.resources["l1"].attachment_ids), 2)
        doc = small_document()
        doc["board"] = {
            "product": "two ASICs",
            "asic_count": 2,
            "selected_asic_index": 1,
        }
        selected = inspect_profile(HardwareProfileConfig.model_validate(doc))
        self.assertEqual(selected.memory, report.memory)

    def test_overrides_preserve_provenance_and_recompute(self):
        before = self.profile.model_dump(mode="json")
        original_report = inspect_profile(self.profile)
        updated = override_parameter(
            self.profile, "clock", 800_000_000, "Use an illustrative lower clock"
        )
        report = inspect_profile(updated)
        self.assertEqual(report.resolved_parameters["rate"].value, 25_600_000_000)
        self.assertEqual(report.resolved_parameters["duration"].value, 9 / 800_000_000)
        self.assertEqual(report.resolved_parameters["rate"].evidence.status, "derived")
        self.assertEqual(
            report.resolved_parameters["rate"].dependency_statuses["clock"], "assumed"
        )
        self.assertEqual(self.profile.model_dump(mode="json"), before)
        self.assertNotEqual(report.profile_sha256, original_report.profile_sha256)
        history = report.resolved_parameters["clock"].overrides
        self.assertEqual(history[0].previous_value, 1_000_000_000)
        self.assertEqual(
            history[0].previous_evidence, self.profile.parameters["clock"].evidence
        )
        self.assertEqual(history[0].reason, "Use an illustrative lower clock")
        updated = override_parameter(updated, "clock", 600_000_000, "second override")
        history = inspect_profile(updated).resolved_parameters["clock"].overrides
        self.assertEqual(
            [item.previous_value for item in history], [1_000_000_000, 800_000_000]
        )
        # Neither a returned report nor an effective profile shares source dictionaries.
        report.profile.fabrics[0].coordinates.clear()
        updated.layout.tiles.clear()
        self.assertEqual(self.profile.model_dump(mode="json"), before)

    def test_override_rejections_and_documented_origin(self):
        for key, value, reason, error in [
            ("rate", 1, "reason", TypeError),
            ("missing", 1, "reason", ValueError),
            ("clock", 0, "reason", ValidationError),
            ("clock", True, "reason", ValidationError),
            ("clock", 1, " ", ValidationError),
            ("l1", 12.5, "reason", ValidationError),
        ]:
            with self.subTest(key=key, value=value), self.assertRaises(error):
                override_parameter(self.profile, key, value, reason)
        example = load_architecture_document(EXAMPLE)
        updated = override_parameter(
            example, "worker_l1_capacity", 1024, "Capacity sensitivity scenario"
        )
        parameter = inspect_profile(updated).resolved_parameters["worker_l1_capacity"]
        self.assertEqual(parameter.evidence.status, "assumed")
        self.assertEqual(parameter.overrides[0].previous_evidence.status, "documented")
        self.assertEqual(parameter.overrides[0].previous_value, 1_499_136)

    def test_deterministic_normalization_across_paths_and_table_order(self):
        doc = small_document()
        shuffled = copy.deepcopy(doc)
        for table in [
            shuffled["layout"]["tiles"],
            shuffled["fabrics"],
            shuffled["memory"]["resources"],
            shuffled["attachments"]["endpoints"],
        ]:
            table.reverse()
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / "first.json", Path(tmp) / "second.json"
            first.write_text(json.dumps(doc))
            second.write_text(json.dumps(shuffled, sort_keys=True))
            left = inspect_profile(load_architecture_document(first))
            right = inspect_profile(load_architecture_document(second))
        self.assertEqual(left.model_dump(), right.model_dump())
        self.assertEqual(
            self.profile.model_dump(),
            HardwareProfileConfig.model_validate(doc).model_dump(),
        )

    def test_capabilities_are_implementation_owned_and_not_bypassable(self):
        report = inspect_profile(self.profile)
        self.assertFalse(report.can_execute)
        self.assertEqual(report.silicon_timing, "unvalidated")
        states = {feature.feature_id: feature.state for feature in report.features}
        self.assertEqual(states["profile_inspection"], "executable")
        self.assertEqual(states["physical_layout"], "represented_only")
        self.assertEqual(states["torus_transport_binding"], "executable")
        self.assertEqual(states["torus_unicast_transport"], "abstract")
        self.assertEqual(states["niu_transactions"], "unsupported")
        transport = [feature for feature in report.features if feature.scope == "opt-in version-2 transport"]
        self.assertTrue(transport)
        self.assertTrue(all(not feature.required_for_execution for feature in transport))
        self.assertEqual(report.manifest_version, "hardware-profile-2")
        ids = [blocker.feature_id for blocker in report.blockers]
        self.assertEqual(ids, sorted(ids))
        self.assertIn("shared_memory_service", ids)
        self.assertIn("profile_runtime_adapter", ids)
        doc = small_document()
        doc["requested_features"] = ["self_declared_available"]
        doc["fabrics"][0]["topology_policy"] = "my_executable_policy"
        unknown = inspect_profile(HardwareProfileConfig.model_validate(doc))
        self.assertTrue(
            {
                "self_declared_available",
                "topology:my_executable_policy",
                "profile_runtime_adapter",
            }
            <= {blocker.feature_id for blocker in unknown.blockers}
        )
        del doc["requested_features"]
        with self.assertRaises(UnsupportedHardwareProfileError):
            require_executable_architecture(HardwareProfileConfig.model_validate(doc))

    def test_calibration_metadata_is_not_reproduced_measurement(self):
        doc = small_document()
        doc["parameters"]["clock"]["evidence"] = {
            **evidence("calibrated"),
            "measurement": {
                "artifact": "external://supplied-record",
                "method": "user supplied",
                "conditions": "test metadata only",
            },
        }
        report = inspect_profile(HardwareProfileConfig.model_validate(doc))
        self.assertEqual(
            report.resolved_parameters["clock"].evidence.status, "calibrated"
        )
        self.assertEqual(
            report.evidence_verification,
            "metadata_only_no_source_fetch_or_measurement_reproduction",
        )
        self.assertEqual(report.silicon_timing, "unvalidated")

    def test_shared_derivation_graph_retains_transitive_assumptions(self):
        doc = small_document()
        previous = "l1"
        for index in range(30):
            key = f"total_{index}"
            doc["parameters"][key] = derived("sum_bytes", [previous, previous], "bytes")
            previous = key
        report = inspect_profile(HardwareProfileConfig.model_validate(doc))
        total = report.resolved_parameters[previous]
        self.assertEqual(total.value, 64 * 2**30)
        self.assertEqual(total.dependency_statuses["l1"], "assumed")
        self.assertEqual(len(total.dependency_statuses), 30)

    def test_cli_json_failure_diagnostics_and_no_side_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            path.write_text(json.dumps(small_document()))
            environment = {**os.environ, "PYTHONPATH": str(ROOT)}
            command = [
                sys.executable,
                "-m",
                "simulator_detailed.inspect_profile",
                str(path),
            ]
            result = subprocess.run(
                command,
                cwd=tmp,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertFalse(json.loads(result.stdout)["can_execute"])
            self.assertEqual(list(Path(tmp).iterdir()), [path])
            path.write_text('{"kind":"hardware_profile","schema_version":99}')
            result = subprocess.run(
                command,
                cwd=tmp,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertIn("schema_version", result.stderr)
            self.assertEqual(list(Path(tmp).iterdir()), [path])
        with patch(
            "simulator_detailed.architecture.simpy.Environment",
            side_effect=AssertionError("constructed runtime"),
        ):
            inspect_profile(self.profile)


class ProfileExecutionGateTests(unittest.TestCase):
    def test_classification_never_falls_back_to_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.json"
            for doc in [
                {"kind": "hardware_profile"},
                {"schema_version": 1},
                {**small_document(), "noc": {}},
                {**small_document(), "schema_version": 99},
            ]:
                path.write_text(json.dumps(doc))
                with self.assertRaises(ValidationError):
                    load_architecture_document(path)
            path.write_text("{}")
            legacy = load_architecture_document(path)
            self.assertIsInstance(legacy, ArchConfig)
            self.assertIs(require_executable_architecture(legacy), legacy)
            path.write_text(json.dumps(small_document()))
            with self.assertRaises(UnsupportedHardwareProfileError) as raised:
                run.arch_analyzer(str(path))
            self.assertEqual(raised.exception.profile_id, "small-assumed")
            self.assertIn(
                "profile_runtime_adapter",
                {item.feature_id for item in raised.exception.blockers},
            )
        with self.assertRaises(ValidationError):
            ArchConfig.model_validate(small_document())
        with self.assertRaises(TypeError):
            require_executable_architecture({})

    def test_direct_constructor_gate_and_positive_legacy_construction(self):
        profile = HardwareProfileConfig.model_validate(small_document())
        with (
            patch(
                "simulator_detailed.architecture.simpy.Environment",
                side_effect=AssertionError("created environment"),
            ),
            self.assertRaises(UnsupportedHardwareProfileError),
        ):
            Arch(profile, UntouchableMapper(), object())
        mapper = Mock()
        mapper.zero_degree.return_value = []
        mapper.all_tasks_completed.return_value = True
        config = ArchConfig.model_validate({"noc": {"x": 2, "y": 2}})
        arch = Arch(config, mapper, FailSlow(router=[], link=[], lsu=[], tpu=[]))
        self.assertIs(arch.config, config)
        self.assertEqual(len(arch.cores), 4)
        self.assertTrue(arch.nocs)
        arch.execute()

    def test_simulate_gates_before_mapper_failure_logs_resources_and_detector(self):
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp), ExitStack() as stack:
            for name in [
                "fail_analyzer",
                "log_timing",
                "setup_logging",
                "Arch",
                "detect",
            ]:
                stack.enter_context(
                    patch.object(
                        run, name, side_effect=AssertionError(f"called {name}")
                    )
                )
            with self.assertRaises(UnsupportedHardwareProfileError):
                run.simulate(str(EXAMPLE), "missing-failure.json", UntouchableMapper())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_simulate_old_gates_before_workload_and_output(self):
        with tempfile.TemporaryDirectory() as tmp, chdir(tmp), ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    sys,
                    "argv",
                    ["simulate", "--arch", str(EXAMPLE), "--mapping", "missing.json"],
                )
            )
            for name in [
                "fail_analyzer",
                "parse_mapping",
                "NetworkMapper",
                "setup_logging",
                "Arch",
                "detect",
            ]:
                stack.enter_context(
                    patch.object(
                        run, name, side_effect=AssertionError(f"called {name}")
                    )
                )
            with (
                redirect_stdout(io.StringIO()),
                self.assertRaises(UnsupportedHardwareProfileError),
            ):
                run.simulate_old()
            self.assertEqual(list(Path(tmp).iterdir()), [])


class UntouchableMapper:
    def __getattribute__(self, name):
        raise AssertionError(f"mapper accessed before gate: {name}")


class WormholeConformanceTests(unittest.TestCase):
    def setUp(self):
        self.reference = json.loads(REFERENCE.read_text())
        self.profile = load_architecture_document(EXAMPLE)
        self.report = inspect_profile(self.profile)

    def test_source_pins_and_physical_role_coordinates(self):
        ref = self.reference
        self.assertEqual(
            [self.profile.layout.extent.width, self.profile.layout.extent.height],
            ref["extent"],
        )
        self.assertEqual(self.report.counts.roles, ref["role_counts"])
        actual = {
            role: {
                (tile.x, tile.y)
                for tile in self.profile.layout.tiles
                if tile.role == role
            }
            for role in ref["role_counts"]
        }
        expected = {
            "worker": {
                (x, y) for x in ref["worker_columns"] for y in ref["worker_rows"]
            },
            "ethernet": {
                (x, y) for x in ref["worker_columns"] for y in ref["ethernet_rows"]
            },
            "memory": {tuple(xy) for group in ref["dram_alias_groups"] for xy in group},
            "transit": {tuple(xy) for xy in ref["transit"]},
            "pcie": {tuple(ref["pcie"])},
            "management": {tuple(ref["management"])},
        }
        self.assertEqual(actual, expected)
        self.assertEqual(set(self.profile.sources), set(ref["sources"]))
        for sid, pin in ref["sources"].items():
            source = self.profile.sources[sid]
            self.assertEqual(
                (source.url, source.revision, source.sha256),
                (pin["url"], pin["revision"], pin["sha256"]),
            )

    def test_raw_fabric_maps_and_explicit_assumed_mask(self):
        ref = self.reference
        self.assertEqual(
            [fabric.fabric_id for fabric in self.profile.fabrics], ref["fabric_ids"]
        )
        self.assertEqual(
            [fabric.routing_policy for fabric in self.profile.fabrics],
            ref["routing_policies"],
        )
        for tile in self.profile.layout.tiles:
            self.assertEqual(
                self.profile.fabrics[0].coordinates[tile.tile_id].pair(),
                (tile.x, tile.y),
            )
            self.assertEqual(
                self.profile.fabrics[1].coordinates[tile.tile_id].pair(),
                (9 - tile.x, 11 - tile.y),
            )
        selection = self.profile.worker_selection
        self.assertEqual(selection.evidence.status, ref["mask"]["status"])
        self.assertIn("y=11", selection.evidence.locator)
        enabled = sorted(
            (tile.y, tile.x, tile.tile_id)
            for tile in self.profile.layout.tiles
            if tile.role == "worker" and tile.y != ref["mask"]["disabled_worker_row"]
        )
        self.assertEqual(
            set(selection.enabled_worker_ids), {tile_id for _, _, tile_id in enabled}
        )
        self.assertEqual(
            self.report.counts.enabled_workers, ref["mask"]["enabled_workers"]
        )
        for index, (_, _, tile_id) in enumerate(enabled):
            worker = selection.logical_workers[index]
            self.assertEqual(
                (
                    worker.worker_index,
                    worker.logical_x,
                    worker.logical_y,
                    worker.tile_id,
                ),
                (index, index % 8, index // 8, tile_id),
            )
        self.assertEqual(self.report.counts.endpoints, ref["endpoint_count"])
        self.assertEqual(
            {(ep.tile_id, ep.fabric_id) for ep in self.profile.attachments.endpoints},
            {
                (tile.tile_id, fabric)
                for tile in self.profile.layout.tiles
                for fabric in [0, 1]
            },
        )

    def test_memory_alias_groups_and_exact_capacity_totals(self):
        ref = self.reference
        tiles = {tile.tile_id: tile for tile in self.profile.layout.tiles}
        local_resources = [
            resource
            for resource in self.profile.memory.resources
            if resource.kind == "local_sram"
        ]
        self.assertEqual(len(self.profile.memory.resources), 86)
        self.assertCountEqual(
            [resource.owner_tile_id for resource in local_resources],
            [tile.tile_id for tile in tiles.values() if tile.role == "worker"],
        )
        groups = []
        for resource in self.profile.memory.resources:
            inventory = self.report.memory.resources[resource.resource_id]
            if resource.kind == "dram":
                groups.append(
                    {
                        tiles[ep.tile_id].pair()
                        for ep in self.profile.attachments.endpoints
                        if resource.resource_id in ep.resource_ids
                    }
                )
                self.assertEqual(inventory.capacity_bytes, ref["dram_group_bytes"])
                self.assertEqual(len(inventory.attachment_ids), 6)
            else:
                self.assertEqual(inventory.capacity_bytes, ref["worker_l1_bytes"])
        self.assertCountEqual(
            groups, [{tuple(xy) for xy in group} for group in ref["dram_alias_groups"]]
        )
        self.assertEqual(self.report.memory.unique_dram_bytes, ref["dram_total_bytes"])
        self.assertEqual(
            self.report.memory.physical_worker_l1_bytes, ref["physical_worker_l1_bytes"]
        )
        self.assertEqual(
            self.report.memory.enabled_worker_l1_bytes, ref["enabled_worker_l1_bytes"]
        )

    def test_packet_reference_quantities_and_example_override(self):
        values = self.report.resolved_parameters
        for key in [
            "flit_bytes",
            "max_data_flits",
            "header_flits",
            "max_payload_bytes",
        ]:
            self.assertEqual(values[key].value, self.reference[key])
            self.assertEqual(values[key].evidence.status, "documented")
        self.assertEqual(
            values["interface_width"].value, self.reference["interface_bytes_per_cycle"]
        )
        self.assertEqual(values["interface_rate"].value, 32_000_000_000)
        self.assertEqual(values["ai_clock"].evidence.status, "assumed")
        updated = inspect_profile(
            override_parameter(
                self.profile, "ai_clock", 800_000_000, "Illustrative 800 MHz"
            )
        )
        self.assertEqual(
            updated.resolved_parameters["interface_rate"].value, 25_600_000_000
        )
        self.assertFalse(updated.can_execute)
        self.assertEqual(updated.silicon_timing, "unvalidated")


if __name__ == "__main__":
    unittest.main()

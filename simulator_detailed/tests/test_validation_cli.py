"""Real command modes, atomic publication, scoped claims and pre-ML guards."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import TypeAdapter, ValidationError

from simulator_detailed.configs.schemas.validation import (
    ValidationDocument,
    ValidationReport,
)
from simulator_detailed.tests.validation_fixtures import (
    calibration_plan,
    calibration_result,
    reference_document,
    report_document,
    suite_document,
)
from simulator_detailed.topology_compatibility import (
    legacy_event_rows,
    require_legacy_nocs,
)
from simulator_detailed.validate_wormhole import atomic_output
from simulator_detailed.validation.runner import run_suite

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "simulator_detailed/configs/validation"


class ValidationCLITests(unittest.TestCase):
    def cli(self, *args, cwd=ROOT):
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        return subprocess.run([sys.executable, "-m", "simulator_detailed.validate_wormhole", *map(str, args)],
                              cwd=cwd, env=env, text=True, capture_output=True, timeout=60, check=False)

    def one_case(self, directory):
        document = json.loads((ASSETS / "offline.json").read_text())
        document["cases"] = [document["cases"][0]]
        document["cases"][0]["input_path"] = str(ASSETS / document["cases"][0]["input_path"])
        document["gates"] = []
        path = Path(directory) / "suite.json"
        path.write_text(json.dumps(document))
        return path, document

    def test_suite_stdout_output_and_other_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            result = self.cli("--suite", ASSETS / "synthetic_reference_suite.json", "--output", output, cwd=directory)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, output.read_text())
            report = ValidationReport.model_validate_json(result.stdout)
            self.assertEqual(report.status, "pass")
            self.assertEqual(report.silicon_timing, "unvalidated")
            self.assertEqual(report.functional_reference, "unvalidated")
            self.assertIn("Suite pass", result.stderr)
            self.assertTrue(report.case_capabilities[0].effective_plan_sha256)
            self.assertEqual(report.reference_documents[0].provenance.classification, "synthetic")
            self.assertTrue(report.reference_documents[0].provenance.raw_artifact.sha256)
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), ["report.json"])

    def test_import_functional_and_profiler_modes(self):
        for name in ("synthetic_functional", "synthetic_profiler"):
            result = self.cli("--import-reference", ASSETS / "references" / (name + ".json"))
            self.assertEqual(result.returncode, 0, result.stderr)
            document = json.loads(result.stdout)
            self.assertEqual(document["kind"], "validation_reference")
            self.assertEqual(document["provenance"]["classification"], "synthetic")
            self.assertIn("not model validation", result.stderr)

    def test_real_calibration_mode(self):
        result = self.cli("--calibrate", ASSETS / "calibration/memory_plan.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(result.stdout)
        self.assertEqual(document["kind"], "calibration_result")
        self.assertEqual(document["evidence_scope"], "synthetic_demonstration")
        self.assertEqual(document["selection"]["values"][0]["value"], 4)

    def test_invalid_input_preserves_existing_output_and_empty_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output.json"
            output.write_text("previous complete output")
            result = self.cli("--import-reference", ASSETS / "references/negative_hash.json", "--output", output)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn("hash mismatch", result.stderr)
            self.assertEqual(output.read_text(), "previous complete output")

    def test_output_does_not_create_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "absent/report.json"
            result = self.cli("--import-reference", ASSETS / "references/synthetic_functional.json", "--output", output)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(output.parent.exists())
            self.assertEqual(result.stdout, "")

    def test_cannot_overwrite_input_or_raw_artifact_even_by_hardlink(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "synthetic_functional.json"
            raw = Path(directory) / "synthetic_functional_raw.json"
            source.write_bytes((ASSETS / "references" / source.name).read_bytes())
            raw.write_bytes((ASSETS / "references" / raw.name).read_bytes())
            alias = Path(directory) / "alias.json"
            os.link(raw, alias)
            before = raw.read_bytes()
            for output in (source, raw, alias):
                result = self.cli("--import-reference", source, "--output", output)
                self.assertEqual(result.returncode, 2)
                self.assertIn("declared input", result.stderr)
            self.assertEqual(raw.read_bytes(), before)

    def test_interrupted_atomic_publish_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            output.write_text("old")
            with patch("simulator_detailed.validate_wormhole.os.replace", side_effect=OSError("interrupted")), self.assertRaises(OSError):
                atomic_output(output, "new")
            self.assertEqual(output.read_text(), "old")
            self.assertEqual(list(Path(directory).iterdir()), [output])

    def test_failed_observation_returns_one(self):
        with tempfile.TemporaryDirectory() as directory:
            path, document = self.one_case(directory)
            document["cases"][0]["expected_execution"] = "incomplete"
            path.write_text(json.dumps(document))
            result = self.cli("--suite", path)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "fail")

    def test_missing_required_evidence_returns_three_optional_allows_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            path, document = self.one_case(directory)
            document["references"] = [{"reference_id": "missing", "document": {"path": "absent.json", "sha256": "a" * 64}}]
            external = {"check_id": "external", "check": "silicon_timing", "required": True,
                        "tier": "silicon_timing", "requirements": ["VA-D07"], "reference_id": "missing",
                        "metrics": [{"metric_id": "elapsed", "unit": "cycles", "boundary": "simulation_start_to_snapshot",
                                     "absolute_tolerance": 0, "relative_tolerance": 0, "rationale": "Exact synthetic check"}]}
            document["cases"][0]["checks"].append(external)
            for required, code in ((True, 3), (False, 0)):
                external["required"] = required
                path.write_text(json.dumps(document))
                result = self.cli("--suite", path)
                self.assertEqual(result.returncode, code, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report["silicon_timing"], "unvalidated")
                self.assertEqual(report["cases"][0]["checks"][-1]["outcome"], "blocked")

    def test_modes_are_mutually_exclusive_and_reject_wrong_kind(self):
        for args in ((), ("--suite", "x", "--calibrate", "y"),
                     ("--suite", ASSETS / "calibration/memory_plan.json")):
            result = self.cli(*args)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertTrue(result.stderr)

    def test_fault_labels_and_requirement_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            path, document = self.one_case(directory)
            document["cases"][0]["input_path"] = str(ASSETS / "wrap_latency.json")
            path.write_text(json.dumps(document))
            report = run_suite(path)
            capability = report.case_capabilities[0]
            self.assertTrue(capability.simulation_fault_experiment)
            self.assertIn("simulation experiments", " ".join(capability.assumptions))
            coverage = {c.requirement_id: c for c in report.coverage}
            self.assertEqual(coverage["VA-D05"].status, "partial")
            self.assertEqual(coverage["VA-04"].status, "partial")
            for name in ("pending_multicast", "pending_synchronization"):
                self.assertEqual(coverage[name].status, "pending")
            self.assertFalse(any(c.status == "implemented_and_checked" for c in report.coverage))
            altered = report.model_dump(mode="json")
            altered["case_capabilities"][0]["effective_plan_sha256"] = "a" * 64
            with self.assertRaises(ValidationError):
                ValidationReport.model_validate_json(json.dumps(altered))


class ValidationConsumerGuardTests(unittest.TestCase):
    def documents(self):
        return [f() for f in (suite_document, reference_document, report_document, calibration_plan, calibration_result)]

    def test_typed_and_json_documents_rejected_as_legacy_data(self):
        adapter = TypeAdapter(ValidationDocument)
        for document in self.documents():
            for value in (document, adapter.validate_json(json.dumps(document))):
                with self.assertRaisesRegex(TypeError, "validation evidence"):
                    require_legacy_nocs(value)
                for stream in ("compute", "communication"):
                    with self.assertRaisesRegex(TypeError, "validation evidence"):
                        legacy_event_rows(value, stream)

    def test_actual_public_predictor_and_encoder_reject_before_ml_import(self):
        script = '''
import importlib.abc, json, sys
class NoTensors(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"torch", "torch_geometric"}:
            raise AssertionError("optional model import before admission")
sys.meta_path.insert(0, NoTensors())
from simulator_detailed.configs.schemas.arch_config import ArchConfig
from simulator_detailed.embedding.hw_encoder import build_hardware_graph
from simulator_detailed.predictor.predict import detect
for doc in json.loads(sys.stdin.read()):
    for function, args in ((build_hardware_graph, (doc,)), (detect, (1, 1, doc, [], [])),
                           (detect, (1, 1, ArchConfig(), doc, [])), (detect, (1, 1, ArchConfig(), [], doc))):
        try:
            function(*args)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("validation evidence admitted to legacy consumer")
'''
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, input=json.dumps(self.documents()),
                                text=True, capture_output=True, check=False, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

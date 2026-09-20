"""External campaign admission is strict, portable and dependency-free."""

import ast
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from simulator_detailed.configs.schemas.external_validation import (
    ExternalArtifactIdentity,
    ExternalCaptureBundle,
    ExternalValidationCampaign,
    ExternalValidationDocumentAdapter,
    ExternalValidationReport,
)
from simulator_detailed.topology_compatibility import (
    legacy_event_rows,
    require_legacy_nocs,
)
from simulator_detailed.validation.external import (
    admit_external_campaign,
    admit_external_capture,
    load_external_document,
)

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "simulator_detailed/configs/validation/external"


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())


class ExternalValidationContractTests(unittest.TestCase):
    def test_three_document_kinds_round_trip_with_exact_typed_identities(self):
        paths = (
            ASSETS / "campaign.valid.json",
            ASSETS / "capture.valid.json",
            ASSETS / "capture.unavailable.json",
            ASSETS / "report.blocked.json",
        )
        for path in paths:
            with self.subTest(path=path.name):
                record = load_external_document(path)
                round_trip = ExternalValidationDocumentAdapter.validate_json(record.model_dump_json())
                self.assertEqual(round_trip, record)
                with self.assertRaises(ValidationError):
                    record.schema_version = 2
        campaign = ExternalValidationCampaign.model_validate_json(paths[0].read_bytes())
        self.assertIsInstance(campaign.cases[0].simulator_input, ExternalArtifactIdentity)
        capture = ExternalCaptureBundle.model_validate_json(paths[1].read_bytes())
        self.assertEqual(capture.counters[0].value, 2**53 + 3)
        self.assertIsInstance(capture.counters[0].value, int)
        unavailable = ExternalCaptureBundle.model_validate_json(paths[2].read_bytes())
        self.assertEqual(unavailable.environment.device.state, "unknown")
        self.assertEqual(unavailable.environment.device.reason, "no named Wormhole worker was supplied")

    def test_fixture_artifact_hashes_and_sizes_are_exact(self):
        campaign = admit_external_campaign(ASSETS / "campaign.valid.json")
        self.assertEqual(len(campaign.input_paths), 3)
        report = ExternalValidationReport.model_validate_json((ASSETS / "report.blocked.json").read_bytes())
        for artifact in (report.campaign, *report.bundles):
            data = (ASSETS / artifact.logical_path).read_bytes()
            self.assertEqual(len(data), artifact.size_bytes)
            self.assertEqual(hashlib.sha256(data).hexdigest(), artifact.sha256)

    def test_campaign_schema_rejects_unknown_mutable_unbounded_and_executable_inputs(self):
        base = read_json(ASSETS / "campaign.valid.json")
        documents: list[dict[str, object]] = []

        document = copy.deepcopy(base)
        document["producers"][0]["adapter"] = "python_eval_v1"  # type: ignore[index]
        documents.append(document)
        document = copy.deepcopy(base)
        document["builds"][0]["revision"] = "main"  # type: ignore[index]
        documents.append(document)
        document = copy.deepcopy(base)
        document["cases"][0]["budget"]["repetitions"] = 0  # type: ignore[index]
        documents.append(document)
        document = copy.deepcopy(base)
        document["cases"][0]["budget"]["timeout_seconds"] = float("inf")  # type: ignore[index]
        documents.append(document)
        document = copy.deepcopy(base)
        document["max_invocations"] = 1
        documents.append(document)
        document = copy.deepcopy(base)
        document["shell"] = "touch should-never-run"
        documents.append(document)
        document = copy.deepcopy(base)
        document["builds"][0]["configuration"]["text"] = '{"shell":"echo bad"}'  # type: ignore[index]
        documents.append(document)
        document = copy.deepcopy(base)
        document["cases"][1]["case_id"] = document["cases"][0]["case_id"]  # type: ignore[index]
        documents.append(document)
        document = copy.deepcopy(base)
        document["cases"][0]["simulator_input"]["logical_path"] = "../escape.json"  # type: ignore[index]
        documents.append(document)
        document = copy.deepcopy(base)
        document["cases"][0]["producers"][0]["outputs"][1]["artifact_id"] = "noc-ttsim-functional"  # type: ignore[index]
        documents.append(document)

        for index, document in enumerate(documents):
            with self.subTest(index=index), self.assertRaises(ValidationError):
                ExternalValidationCampaign.model_validate_json(json.dumps(document))

    def test_checked_adversarial_fixtures_fail_without_side_effects(self):
        marker = ASSETS / "should-never-run"
        marker.unlink(missing_ok=True)
        for name in ("campaign.adversarial.json", "capture.adversarial.json"):
            with (
                self.subTest(name=name),
                patch("subprocess.run", side_effect=AssertionError("process launched")),
                self.assertRaises(ValidationError),
            ):
                load_external_document(ASSETS / name)
        self.assertFalse(marker.exists())

    def test_campaign_preflight_resolves_from_document_and_never_runs_a_producer(self):
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                with patch("subprocess.run", side_effect=AssertionError("process launched")), patch(
                    "os.system", side_effect=AssertionError("shell launched")
                ):
                    admitted = admit_external_campaign(ASSETS / "campaign.valid.json")
            finally:
                os.chdir(old_cwd)
        self.assertTrue(all(path.is_relative_to(ASSETS) for path in admitted.input_paths))
        self.assertTrue(all(path.is_relative_to(ASSETS / "generated") for path in admitted.output_paths))
        self.assertFalse((ASSETS / "generated").exists())

    def test_failed_preflight_preserves_existing_output_from_another_working_directory(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as other:
            copied = Path(directory) / "external"
            shutil.copytree(ASSETS, copied)
            output = copied / "generated/noc/ttsim-functional.json"
            output.parent.mkdir(parents=True)
            output.write_bytes(b"existing output")
            (copied / "inputs/noc_ack_roundtrip.json").write_bytes(b"tampered input")
            old_cwd = Path.cwd()
            try:
                os.chdir(other)
                with self.assertRaisesRegex(ValueError, "size mismatch"):
                    admit_external_campaign(copied / "campaign.valid.json")
            finally:
                os.chdir(old_cwd)
            self.assertEqual(output.read_bytes(), b"existing output")

    def test_resolved_output_alias_with_an_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "external"
            shutil.copytree(ASSETS, copied)
            document = read_json(copied / "campaign.valid.json")
            document["output_directory"] = "inputs"
            document["cases"][0]["producers"][0]["outputs"][0]["logical_path"] = "noc_ack_roundtrip.json"  # type: ignore[index]
            path = copied / "collision.json"
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, "cannot replace"):
                admit_external_campaign(path)

    def test_capture_preflight_verifies_lineage_build_and_raw_bytes(self):
        valid = admit_external_capture(ASSETS / "capture.valid.json")
        self.assertEqual(valid.document.outcome.outcome, "pass")
        self.assertEqual(len(valid.artifact_paths), 2)
        unavailable = admit_external_capture(ASSETS / "capture.unavailable.json")
        self.assertEqual(unavailable.document.outcome.outcome, "blocked")
        self.assertEqual(len(unavailable.artifact_paths), 1)
        with tempfile.TemporaryDirectory() as directory:
            copied = Path(directory) / "external"
            shutil.copytree(ASSETS, copied)
            (copied / "raw/functional_capture.json").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "size mismatch"):
                admit_external_capture(copied / "capture.valid.json")
        document = read_json(ASSETS / "capture.valid.json")
        document["build"]["revision"] = "f" * 40  # type: ignore[index]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "forged.json"
            path.write_text(json.dumps(document))
            shutil.copy2(ASSETS / "campaign.valid.json", Path(directory) / "campaign.valid.json")
            shutil.copytree(ASSETS / "raw", Path(directory) / "raw")
            with self.assertRaisesRegex(ValueError, "source/build identity"):
                admit_external_capture(path)


class ExternalValidationCompatibilityTests(unittest.TestCase):
    def documents(self) -> list[dict[str, object]]:
        return [
            read_json(ASSETS / "campaign.valid.json"),
            read_json(ASSETS / "capture.valid.json"),
            read_json(ASSETS / "report.blocked.json"),
        ]

    def test_external_documents_are_rejected_by_legacy_guards(self):
        for document in self.documents():
            typed = ExternalValidationDocumentAdapter.validate_json(json.dumps(document))
            for value in (document, typed):
                with self.assertRaisesRegex(TypeError, "validation evidence"):
                    require_legacy_nocs(value)
                for stream in ("compute", "communication"):
                    with self.assertRaisesRegex(TypeError, "validation evidence"):
                        legacy_event_rows(value, stream)

    def test_public_consumers_reject_before_optional_ml_import_or_checkpoint_loading(self):
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
    calls = ((build_hardware_graph, (doc,)), (detect, (1, 1, doc, [], [])),
             (detect, (1, 1, ArchConfig(), doc, [])), (detect, (1, 1, ArchConfig(), [], doc)))
    for function, args in calls:
        try:
            function(*args)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("external validation document reached a legacy consumer")
'''
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            input=json.dumps(self.documents()),
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_existing_feature_checkpoint_and_root_action_shapes_are_unchanged(self):
        predictor_tree = ast.parse((ROOT / "simulator_detailed/predictor/predictor.py").read_text())
        in_dims = [
            ast.literal_eval(node.value)
            for node in ast.walk(predictor_tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "in_dims" for target in node.targets)
        ]
        self.assertIn({"core": 7, "link": 7}, in_dims)
        predict_tree = ast.parse((ROOT / "simulator_detailed/predictor/predict.py").read_text())
        model_paths = [
            ast.literal_eval(node.value)
            for node in ast.walk(predict_tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "model_path" for target in node.targets)
        ]
        self.assertEqual(model_paths, ["models/best_model.pth"])
        encoder_tree = ast.parse((ROOT / "simulator_detailed/embedding/hw_encoder.py").read_text())
        feature_widths = [
            len(node.args[0].elts)
            for node in ast.walk(encoder_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "node_features"
            and node.func.attr == "append"
            and node.args
            and isinstance(node.args[0], ast.List)
        ]
        self.assertEqual(feature_widths, [4, 4])
        action_tree = ast.parse((ROOT / "rl_agent/envs/rl_env.py").read_text())
        action_widths = [
            len(node.args[0].elts)
            for node in ast.walk(action_tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "MultiDiscrete"
            and node.args
            and isinstance(node.args[0], ast.List)
        ]
        self.assertEqual(action_widths, [4])

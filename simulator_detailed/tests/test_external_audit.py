"""Independent external-report lineage and tamper-audit tests."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from simulator_detailed.configs.schemas.external_validation import (
    ExternalOutcome,
    ExternalValidationReport,
)
from simulator_detailed.configs.schemas.validation import (
    ArtifactReference,
    Metadata,
    ReferenceProvenance,
    ValidationReference,
)
from simulator_detailed.tests.test_external_campaign import (
    _capture_pair,
    _functional_observations,
)
from simulator_detailed.validation.external_audit import audit_external_report
from simulator_detailed.validation.external_campaign import (
    ReportArtifactInput,
    write_external_report,
)
from simulator_detailed.validation.identity import bytes_digest, content_digest


def _reference(root: Path, name: str, capture) -> Path:
    observation = _functional_observations()
    raw_document = observation.model_dump(
        mode="json", exclude={"observation_id", "source_result_sha256"}
    )
    raw_data = json.dumps(
        raw_document, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    reference_root = root / name
    reference_root.mkdir(parents=True)
    raw_path = reference_root / "raw.json"
    raw_path.write_bytes(raw_data)
    reference = ValidationReference(
        kind="validation_reference",
        schema_version=1,
        reference_id=f"audit:{name}",
        format="normalized_functional_v1",
        provenance=ReferenceProvenance(
            classification=capture.document.intended_classification,
            producer=f"{capture.document.adapter}:{capture.document.producer_id}",
            source_url=Metadata[str](
                state="known", value=capture.document.build.source_url
            ),
            revision=Metadata[str](
                state="known", value=capture.document.build.revision
            ),
            snapshot_sha256=Metadata[str](
                state="known", value=capture.document.build.source_snapshot_sha256
            ),
            raw_artifact=ArtifactReference(
                path="raw.json", sha256=bytes_digest(raw_data)
            ),
            extractor="wormhole_reference_import",
            extractor_version="1",
            original_units=("bytes",),
            normalized_units=("bytes",),
        ),
        conditions=capture.document.conditions,
        observations=None,
    )
    path = reference_root / "reference.json"
    path.write_text(reference.model_dump_json(indent=2) + "\n")
    return path


def _report_identity(document: ExternalValidationReport) -> str:
    return "external-report:" + content_digest(
        {
            "campaign": document.campaign.model_dump(mode="json"),
            "bundles": [item.model_dump(mode="json") for item in document.bundles],
            "artifacts": [item.model_dump(mode="json") for item in document.artifacts],
            "boundaries": [item.model_dump(mode="json") for item in document.boundaries],
            "outcomes": [item.model_dump(mode="json") for item in document.outcomes],
            "claim_scope": document.claim_scope,
            "limitations": document.limitations,
        }
    )


def _reseal_bundle(report_path: Path, index: int, change) -> None:
    report_document = json.loads(report_path.read_text())
    identity = report_document["bundles"][index]
    bundle_path = report_path.parent / identity["logical_path"]
    bundle_document = json.loads(bundle_path.read_text())
    change(bundle_document)
    bundle_data = (
        json.dumps(bundle_document, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    bundle_path.write_bytes(bundle_data)
    identity["sha256"] = bytes_digest(bundle_data)
    identity["size_bytes"] = len(bundle_data)
    parsed = ExternalValidationReport.model_validate_json(
        json.dumps(report_document)
    )
    report_document["report_id"] = _report_identity(parsed)
    report_path.write_text(json.dumps(report_document, indent=2) + "\n")


def _write_complete_report(root: Path):
    campaign, ttsim, silicon = _capture_pair(
        root / "captures",
        case_id="compute-bf16-32",
        narrow_campaign=True,
    )
    ttsim_reference = _reference(root, "ttsim-reference", ttsim)
    silicon_reference = _reference(root, "silicon-reference", silicon)
    model = root / "model.json"
    model.write_text(_functional_observations().model_dump_json(indent=2) + "\n")
    comparison = root / "comparison.json"
    comparison.write_text('{"comparison":"independent audit fixture"}\n')
    comparison_id = "comparison:compute"
    outcomes = (
        ExternalOutcome(
            stage="functional",
            required=True,
            outcome="pass",
            reason="synthetic audit fixture",
            executed=True,
            case_id="compute-bf16-32",
            producer_id="ttsim-functional",
            artifact_ids=(comparison_id,),
        ),
        ExternalOutcome(
            stage="timing",
            required=True,
            outcome="pass",
            reason="synthetic audit fixture",
            executed=True,
            case_id="compute-bf16-32",
            producer_id="wormhole-profiler",
            boundary_id="compute-service-window",
            artifact_ids=(comparison_id,),
        ),
    )
    written = write_external_report(
        campaign=campaign,
        bundle_paths=(ttsim.document_path, silicon.document_path),
        artifacts=(
            ReportArtifactInput(
                artifact_id="reference:ttsim",
                path=ttsim_reference,
                derived_from=(f"bundle:{ttsim.document.bundle_id}",),
                transform="functional_reference_import",
            ),
            ReportArtifactInput(
                artifact_id="reference:silicon",
                path=silicon_reference,
                derived_from=(f"bundle:{silicon.document.bundle_id}",),
                transform="functional_reference_import",
            ),
            ReportArtifactInput(
                artifact_id="model:compute",
                path=model,
                derived_from=("campaign",),
                transform="model_execution",
            ),
            ReportArtifactInput(
                artifact_id=comparison_id,
                path=comparison,
                derived_from=(
                    "reference:ttsim",
                    "reference:silicon",
                    "model:compute",
                ),
                transform="interval_comparison",
            ),
        ),
        outcomes=outcomes,
        claim_scope=("synthetic audit mechanics only",),
        limitations=("not external or measured evidence",),
        output_directory=root / "report",
    )
    return written, campaign, ttsim


class ExternalAuditTests(unittest.TestCase):
    def test_complete_report_reconstructs_raw_reference_model_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            written, _, _ = _write_complete_report(root)
            audited = audit_external_report(written.document_path)
            self.assertEqual(len(audited.captures), 2)
            self.assertEqual(
                audited.reference_ids,
                ("audit:ttsim-reference", "audit:silicon-reference"),
            )
            self.assertEqual(audited.model_artifact_ids, ("model:compute",))
            self.assertEqual(
                audited.comparison_artifact_ids, ("comparison:compute",)
            )
            self.assertEqual(
                audited.covered_case_producers,
                (
                    ("compute-bf16-32", "ttsim-functional"),
                    ("compute-bf16-32", "wormhole-profiler"),
                ),
            )

    def test_changed_and_missing_evidence_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            written, _, _ = _write_complete_report(root)
            changed = root / "changed"
            missing = root / "missing"
            shutil.copytree(written.document_path.parent, changed)
            shutil.copytree(written.document_path.parent, missing)

            changed_audit = audit_external_report(changed / "report.json")
            raw_path = changed_audit.captures[0].artifact_paths[1]
            raw_path.write_bytes(raw_path.read_bytes() + b"changed")
            with self.assertRaisesRegex(ValueError, "hash|identity|size"):
                audit_external_report(changed / "report.json")

            missing_document = ExternalValidationReport.model_validate_json(
                (missing / "report.json").read_bytes()
            )
            reference_identity = next(
                item
                for item in missing_document.artifacts
                if item.artifact_id == "reference:ttsim"
            )
            reference_path = missing / reference_identity.logical_path
            reference = ValidationReference.model_validate_json(reference_path.read_bytes())
            (reference_path.parent / reference.provenance.raw_artifact.path).unlink()
            with self.assertRaises(FileNotFoundError):
                audit_external_report(missing / "report.json")

    def test_resealed_build_hardware_and_fidelity_changes_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            written, _, _ = _write_complete_report(root)
            build_changed = root / "build-changed"
            fidelity_changed = root / "fidelity-changed"
            shutil.copytree(written.document_path.parent, build_changed)
            shutil.copytree(written.document_path.parent, fidelity_changed)

            _reseal_bundle(
                build_changed / "report.json",
                0,
                lambda document: document["build"].update(revision="f" * 40),
            )
            with self.assertRaisesRegex(ValueError, "build identity"):
                audit_external_report(build_changed / "report.json")

            def change_fidelity(document):
                workload = json.loads(
                    document["conditions"]["workload"]["value"]["text"]
                )
                workload["fidelity"] = "lofi"
                document["conditions"]["workload"]["value"]["text"] = json.dumps(
                    workload, sort_keys=True, separators=(",", ":")
                )

            _reseal_bundle(
                fidelity_changed / "report.json", 0, change_fidelity
            )
            with self.assertRaisesRegex(ValueError, "workload"):
                audit_external_report(fidelity_changed / "report.json")

    def test_missing_case_producer_coverage_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, campaign, ttsim = _write_complete_report(root)
            incomplete = write_external_report(
                campaign=campaign,
                bundle_paths=(ttsim.document_path,),
                artifacts=(),
                outcomes=(
                    ExternalOutcome(
                        stage="collection",
                        required=True,
                        outcome="blocked",
                        reason="injected missing hardware capture",
                        executed=False,
                        case_id="compute-bf16-32",
                        producer_id="wormhole-profiler",
                    ),
                ),
                claim_scope=("synthetic audit mechanics only",),
                limitations=("incomplete by construction",),
                output_directory=root / "incomplete-report",
            )
            with self.assertRaisesRegex(ValueError, "coverage"):
                audit_external_report(incomplete.document_path)


if __name__ == "__main__":
    unittest.main()

"""Portable kit generation and producer-record conversion tests."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

from simulator_detailed.configs.schemas.external_validation import (
    ArtifactLineage,
    CaptureCounter,
    CaptureEnvironment,
    ExternalArtifactIdentity,
    ExternalCaptureBundle,
    ExternalOutcome,
    ProducerFunctionalRecord,
    TTSimCaptureInvocation,
    TTSimCaptureKitManifest,
)
from simulator_detailed.configs.schemas.validation import CanonicalJSON, Metadata
from simulator_detailed.validation.external import (
    AdmittedExternalCampaign,
    admit_external_campaign,
    admit_external_capture,
)
from simulator_detailed.validation.external_capture import (
    ASSET_ROOT,
    _effective_conditions,
    _expected_actions,
    _expected_entities,
    _expected_sentinel,
    collect_ttsim_capture,
    convert_functional_capture,
    generate_ttsim_capture_kit,
)
from simulator_detailed.validation.identity import bytes_digest, content_digest
from simulator_detailed.validation.references import import_reference

ROOT = Path(__file__).resolve().parents[2]
EXTERNAL = ROOT / "simulator_detailed/configs/validation/external"
TTSIM_EVIDENCE = (
    EXTERNAL / "evidence/ttsim-wormhole-external-validation-v1-20260920"
)


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _counter(name: str, value: int, unit: str, scope: str) -> dict[str, object]:
    return {"name": name, "value": value, "unit": unit, "scope": scope}


def _functional_record(campaign_path: Path, case_id: str) -> ProducerFunctionalRecord:
    campaign = admit_external_campaign(campaign_path)
    case = next(item for item in campaign.document.cases if item.case_id == case_id)
    producer = next(
        item
        for item in campaign.document.producers
        if item.adapter == "ttsim_tt_metal_v1"
    )
    build = next(
        item for item in campaign.document.builds if item.build_id == producer.build_id
    )
    workload_record = case.conditions.workload.value
    mapping_record = case.conditions.mapping.value
    layout_record = case.conditions.enabled_layout.value
    instrumentation_record = case.conditions.instrumentation.value
    if any(
        item is None
        for item in (
            workload_record,
            mapping_record,
            layout_record,
            instrumentation_record,
        )
    ):
        raise AssertionError("test campaign must carry complete effective conditions")
    assert workload_record is not None
    assert mapping_record is not None
    assert layout_record is not None
    assert instrumentation_record is not None
    workload = json.loads(workload_record.text)
    mapping = json.loads(mapping_record.text)
    actions = _expected_actions(case)
    boundary = case.boundary_maps[0].simulator_interval
    boundary_indexes = {
        "noc_ack_roundtrip": {0: boundary.start_event_id, 2: boundary.end_event_id},
        "dram_read_return": {1: boundary.start_event_id, 2: boundary.end_event_id},
        "compute_service": {0: boundary.start_event_id, 2: boundary.end_event_id},
    }[case.family]
    counters: tuple[tuple[dict[str, object], ...], ...]
    if case.family == "noc_ack_roundtrip":
        counters = (
            (_counter("bytes", workload["bytes"], "bytes", "planned"),),
            (_counter("bytes", workload["bytes"], "bytes", "observed"),),
            (_counter("acknowledgements", 1, "count", "observed"),),
        )
        effect = (
            "destination",
            "destination-l1",
            workload["destination_address"],
            workload["bytes"],
            1,
        )
        visibility_index = 1
    elif case.family == "dram_read_return":
        counters = (
            (_counter("bytes", workload["bytes"], "bytes", "planned"),),
            (),
            (),
            (_counter("bytes", workload["bytes"], "bytes", "observed"),),
        )
        effect = (
            "destination",
            "dram",
            workload["address"],
            workload["bytes"],
            workload["count"],
        )
        visibility_index = 3
    else:
        counters = (
            (_counter("work", workload["work"], "work", "planned"),),
            (_counter("bytes", workload["output_bytes"], "bytes", "observed"),),
            (_counter("work", workload["work"], "work", "observed"),),
        )
        effect = (
            "worker",
            "output-l1",
            workload["output_address"],
            workload["output_bytes"],
            1,
        )
        visibility_index = 1
    payload = _expected_sentinel(case.family, workload)
    repetitions: list[dict[str, object]] = []
    for repetition_id in case.boundary_maps[0].samples.repetition_ids:
        prefix = f"repetition:{repetition_id}:"
        events = [
            {
                "event_id": prefix + action,
                "action": action,
                "subject_id": "operation",
                "sequence": index,
                "simulator_event_id": prefix
                + boundary_indexes.get(index, f"external:{case.case_id}:{action}"),
                "counters": counters[index],
            }
            for index, action in enumerate(actions)
        ]
        repetitions.append(
            {
                "repetition_id": repetition_id,
                "status": "pass",
                "completion_marker": "WORMHOLE_EXTERNAL_COMPLETE_V1",
                "sentinel_algorithm": "sha256",
                "sentinel_payload_hex": payload.hex(),
                "sentinel_sha256": bytes_digest(payload),
                "events": events,
                "effects": [
                    {
                        "effect_id": prefix + "effect",
                        "destination_id": effect[0],
                        "resource_id": effect[1],
                        "offset_bytes": effect[2],
                        "size_bytes": effect[3],
                        "count": effect[4],
                        "visibility_event": events[visibility_index]["event_id"],
                        "simulator_effect_id": prefix + f"effect:{case.case_id}",
                    }
                ],
            }
        )
    return ProducerFunctionalRecord.model_validate_json(
        json.dumps(
            {
                "kind": "tt_metal_functional_record",
                "schema_version": 1,
                "case_id": case.case_id,
                "case_family": case.family,
                "producer_id": producer.producer_id,
                "adapter": producer.adapter,
                "build_id": build.build_id,
                "input_artifact": case.simulator_input.model_dump(mode="json"),
                "binary_artifacts": [
                    item.model_dump(mode="json") for item in build.artifacts
                ],
                "workload": workload_record.model_dump(mode="json"),
                "mapping": mapping_record.model_dump(mode="json"),
                "enabled_layout": layout_record.model_dump(mode="json"),
                "instrumentation": instrumentation_record.model_dump(mode="json"),
                "entities": [
                    item.model_dump(mode="json")
                    for item in _expected_entities(case, workload, mapping)
                ],
                "repetitions": repetitions,
            }
        )
    )


def _capture(root: Path, case_id: str, raw_document: dict[str, object]) -> Path:
    campaign_path = root / "campaign.valid.json"
    campaign = admit_external_campaign(campaign_path)
    case = next(item for item in campaign.document.cases if item.case_id == case_id)
    producer = next(
        item
        for item in campaign.document.producers
        if item.adapter == "ttsim_tt_metal_v1"
    )
    build = next(
        item for item in campaign.document.builds if item.build_id == producer.build_id
    )
    binding = next(
        item for item in case.producers if item.producer_id == producer.producer_id
    )
    functional = next(
        item for item in binding.outputs if item.role == "functional_record"
    )
    raw_bytes = (
        json.dumps(raw_document, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    raw_path = root / f"raw/{case_id}.json"
    raw_path.parent.mkdir(exist_ok=True)
    raw_path.write_bytes(raw_bytes)
    raw_identity = ExternalArtifactIdentity(
        artifact_id=functional.artifact_id,
        logical_path=f"raw/{case_id}.json",
        sha256=bytes_digest(raw_bytes),
        size_bytes=len(raw_bytes),
    )
    campaign_bytes = campaign_path.read_bytes()
    document = ExternalCaptureBundle(
        kind="external_capture_bundle",
        schema_version=1,
        bundle_id=f"test:{case_id}",
        campaign=ExternalArtifactIdentity(
            artifact_id="campaign",
            logical_path="campaign.valid.json",
            sha256=bytes_digest(campaign_bytes),
            size_bytes=len(campaign_bytes),
        ),
        case_id=case.case_id,
        producer_id=producer.producer_id,
        adapter=producer.adapter,
        build=build,
        intended_classification="functional_capture",
        environment=CaptureEnvironment(
            host=Metadata[CanonicalJSON](
                state="known", value=CanonicalJSON(text='{"kind":"test"}')
            ),
            device=Metadata[str](state="known", value="ttsim-test-double"),
            software=Metadata[str](state="known", value="test-only-record-builder"),
            firmware=Metadata[str](
                state="unknown", reason="ttsim fixture has no firmware identity"
            ),
            clocks=Metadata[tuple[CanonicalJSON, ...]](state="known", value=()),
            enabled_layout=case.conditions.enabled_layout,
        ),
        conditions=case.conditions,
        outcome=ExternalOutcome(
            stage="collection",
            required=True,
            outcome="pass",
            reason="synthetic unit-test record only",
            executed=True,
            case_id=case.case_id,
            producer_id=producer.producer_id,
            artifact_ids=(functional.artifact_id,),
        ),
        raw_artifacts=(raw_identity,),
        counters=(
            CaptureCounter(
                name="repetitions", value=case.budget.repetitions, unit="count"
            ),
        ),
        lineage=(
            ArtifactLineage(
                artifact_id=functional.artifact_id,
                derived_from=("campaign",),
                transform="synthetic_test_record_builder",
            ),
        ),
        diagnostics=("unit-test data; not external evidence",),
    )
    path = root / f"capture-{case_id}.json"
    path.write_text(document.model_dump_json(indent=2) + "\n")
    return path


class TTSimCaptureKitTests(unittest.TestCase):
    def test_committed_kit_seals_worker_patch_and_runtime_binaries(self):
        manifest = TTSimCaptureKitManifest.model_validate_json(
            (TTSIM_EVIDENCE / "kit/capture-kit.json").read_bytes()
        )
        source_data = (
            TTSIM_EVIDENCE / "kit/wormhole_external_sources.json"
        ).read_bytes()
        source_manifest = json.loads(source_data)
        source_identity = next(
            item
            for item in manifest.binary_manifest
            if item.artifact_id == "producer-sources"
        )
        self.assertEqual(
            (bytes_digest(source_data), len(source_data)),
            (source_identity.sha256, source_identity.size_bytes),
        )
        patch = ASSET_ROOT / "patches/tt-metal-ttsim-single-rank.patch"
        patch_sha256 = bytes_digest(patch.read_bytes())
        self.assertEqual(
            source_manifest["tt_metal_worker_patch_sha256"], patch_sha256
        )
        self.assertEqual(
            manifest.build.source_snapshot_sha256,
            content_digest(
                {
                    "tt_metal_base_tree_sha256": source_manifest[
                        "tt_metal_base_tree_sha256"
                    ],
                    "worker_patch_sha256": patch_sha256,
                }
            ),
        )
        self.assertEqual(
            {item.artifact_id for item in manifest.binary_manifest},
            {
                "host-program",
                "hwloc-runtime",
                "metalium-runtime",
                "producer-sources",
                "tracy-runtime",
                "tt-stl-runtime",
                "ttsim-runtime",
                "umd-runtime",
            },
        )

    def test_kit_is_deterministic_portable_bounded_and_fixed(self):
        campaign = admit_external_campaign(EXTERNAL / "campaign.valid.json")
        with (
            tempfile.TemporaryDirectory() as directory,
            tempfile.TemporaryDirectory() as other,
        ):
            first, second = Path(directory) / "first", Path(directory) / "second"
            old_cwd = Path.cwd()
            try:
                os.chdir(other)
                first_manifest = generate_ttsim_capture_kit(campaign, first)
                second_manifest = generate_ttsim_capture_kit(campaign, second)
            finally:
                os.chdir(old_cwd)
            self.assertEqual(first_manifest, second_manifest)
            self.assertEqual(_tree_bytes(first), _tree_bytes(second))
            repository_path = str(ROOT).encode()
            self.assertFalse(
                any(repository_path in data for data in _tree_bytes(first).values())
            )
            self.assertEqual(len(first_manifest.invocations), 3)
            self.assertTrue(
                all(
                    item.argv[0] == "bin/wormhole_external_validation"
                    for item in first_manifest.invocations
                )
            )
            self.assertTrue(
                all(
                    len(item.repetition_ids) == 2 for item in first_manifest.invocations
                )
            )
            for item in first_manifest.files:
                data = (first / item.logical_path).read_bytes()
                self.assertEqual(
                    (len(data), hashlib.sha256(data).hexdigest()),
                    (item.size_bytes, item.sha256),
                )

    def test_legacy_ttsim_environment_remains_readable(self):
        campaign = admit_external_campaign(EXTERNAL / "campaign.valid.json")
        with tempfile.TemporaryDirectory() as directory:
            manifest = generate_ttsim_capture_kit(campaign, Path(directory) / "kit")
        invocation = manifest.invocations[0].model_dump(mode="json")
        invocation["environment"] = [
            item
            for item in invocation["environment"]
            if item["name"] != "LD_LIBRARY_PATH"
        ]
        parsed = TTSimCaptureInvocation.model_validate_json(json.dumps(invocation))
        self.assertNotIn(
            "LD_LIBRARY_PATH", {item.name for item in parsed.environment}
        )

        invocation["environment"].append(invocation["environment"][0])
        with self.assertRaisesRegex(ValueError, "environment variable"):
            TTSimCaptureInvocation.model_validate_json(json.dumps(invocation))

    def test_failed_generation_preserves_existing_output(self):
        campaign = admit_external_campaign(EXTERNAL / "campaign.valid.json")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "kit"
            output.mkdir()
            marker = output / "keep"
            marker.write_bytes(b"existing")
            with self.assertRaisesRegex(ValueError, "already exists"):
                generate_ttsim_capture_kit(campaign, output)
            self.assertEqual(marker.read_bytes(), b"existing")

    def test_assets_and_inputs_record_finite_operation_parameters(self):
        sources = {path.name: path.read_text() for path in ASSET_ROOT.rglob("*.cpp")}
        self.assertIn("kMaximumBytes", sources["noc_ack_roundtrip.cpp"])
        self.assertIn("noc_async_write_barrier", sources["noc_ack_roundtrip.cpp"])
        self.assertIn("kCompletionValue", sources["dram_read_return.cpp"])
        self.assertIn("kMaximumTilesPerAxis", sources["compute_service.cpp"])
        for name in ("noc_ack_roundtrip", "dram_read_return", "compute_service"):
            document = json.loads((EXTERNAL / f"inputs/{name}.json").read_text())
            self.assertIn("completion", document)
        self.assertEqual(
            json.loads((EXTERNAL / "inputs/noc_ack_roundtrip.json").read_text())[
                "destination_address"
            ],
            8192,
        )
        compute = json.loads((EXTERNAL / "inputs/compute_service.json").read_text())
        self.assertEqual(
            (compute["input_layout"], compute["output_layout"], compute["fidelity"]),
            ("tile", "tile", "hifi2"),
        )

    def test_compute_recipe_rejects_shapes_outside_the_implemented_tile(self):
        compute = json.loads((EXTERNAL / "inputs/compute_service.json").read_text())
        compute["shape"] = [64, 32, 32]
        compute["work"] = 131072
        compute["output_bytes"] = 4096
        with self.assertRaisesRegex(ValueError, "one 32x32x32 tile"):
            _effective_conditions("compute_service", compute)


class TTSimCollectorTests(unittest.TestCase):
    def _prepared_worker(
        self, root: Path, case_id: str
    ) -> tuple[Path, Path, AdmittedExternalCampaign]:
        campaign_root = root / "campaign"
        shutil.copytree(EXTERNAL, campaign_root)
        document = json.loads((campaign_root / "campaign.valid.json").read_text())
        binaries = {
            "host_binary": b"bounded ttsim test host",
            "device_binary": b"bounded ttsim test kernels",
        }
        for artifact in document["builds"][0]["artifacts"]:
            data = binaries[artifact["role"]]
            artifact["sha256"] = bytes_digest(data)
            artifact["size_bytes"] = len(data)
        campaign_path = campaign_root / "campaign.valid.json"
        campaign_path.write_text(json.dumps(document, indent=2) + "\n")
        admitted = admit_external_campaign(campaign_path)
        kit = root / "kit"
        manifest = generate_ttsim_capture_kit(admitted, kit)
        for artifact in manifest.binary_manifest:
            path = kit / artifact.logical_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(binaries[artifact.role])
        return campaign_path, kit, admitted

    def test_missing_binary_blocks_before_process_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, kit, admitted = self._prepared_worker(root, "noc-64b")
            (kit / "bin/wormhole_external_validation").unlink()
            with patch(
                "simulator_detailed.validation.external_capture.subprocess.run",
                side_effect=AssertionError("producer must not launch"),
            ):
                capture = collect_ttsim_capture(
                    admitted, "noc-64b", kit, root / "blocked"
                )
            self.assertEqual(capture.document.outcome.outcome, "blocked")
            self.assertEqual(capture.document.raw_artifacts, ())

    def test_fixed_worker_outputs_are_captured_and_convert(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            campaign_path, kit, admitted = self._prepared_worker(root, "noc-64b")
            invocation = next(
                item
                for item in json.loads((kit / "capture-kit.json").read_text())["invocations"]
                if item["case_id"] == "noc-64b"
            )

            def run_worker(
                command: list[str], **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                self.assertEqual(command[1:], invocation["argv"][1:])
                self.assertEqual(kwargs["cwd"], kit.resolve())
                environment = kwargs["env"]
                self.assertEqual(environment["TT_METAL_SIMULATOR"], "runtime/ttsim/libttsim_wh.so")
                record = _functional_record(campaign_path, "noc-64b")
                functional_data = (record.model_dump_json(indent=2) + "\n").encode()
                outputs = {item["role"]: item for item in invocation["outputs"]}
                functional_path = kit / outputs["functional_record"]["logical_path"]
                functional_path.parent.mkdir(parents=True, exist_ok=True)
                functional_path.write_bytes(functional_data)
                manifest_data = {
                    "case_id": "noc-64b",
                    "completion_marker": "WORMHOLE_EXTERNAL_COMPLETE_V1",
                    "functional_sha256": bytes_digest(functional_data),
                    "input_sha256": record.input_artifact.sha256,
                    "kind": "tt_metal_capture_manifest",
                    "max_output_bytes": invocation["max_output_bytes"],
                    "recipe": invocation["recipe"],
                    "repetitions": len(invocation["repetition_ids"]),
                    "schema_version": 1,
                    "status": "pass",
                    "timeout_seconds": int(invocation["timeout_seconds"]),
                    "warmup_repetitions": len(invocation["warmup_repetition_ids"]),
                }
                capture_manifest_path = (
                    kit / outputs["capture_manifest"]["logical_path"]
                )
                capture_manifest_path.parent.mkdir(parents=True, exist_ok=True)
                capture_manifest_path.write_text(
                    json.dumps(manifest_data, indent=2) + "\n"
                )
                return subprocess.CompletedProcess(command, 0)

            with patch(
                "simulator_detailed.validation.external_capture.subprocess.run",
                side_effect=run_worker,
            ) as launched:
                capture = collect_ttsim_capture(
                    admitted, "noc-64b", kit, root / "capture"
                )
            launched.assert_called_once()
            self.assertEqual(capture.document.outcome.outcome, "pass")
            self.assertEqual(capture.document.intended_classification, "functional_capture")
            self.assertEqual(len(capture.document.raw_artifacts), 2)
            conversion = convert_functional_capture(capture, root / "reference")
            self.assertEqual(
                import_reference(conversion.reference_path).provenance.classification,
                "functional_capture",
            )


class FunctionalCaptureConversionTests(unittest.TestCase):
    def test_committed_ttsim_evidence_readmits_and_reproduces_references(self):
        for case_id in ("noc-64b", "dram-read-256b", "compute-bf16-32"):
            with (
                self.subTest(case_id=case_id),
                tempfile.TemporaryDirectory() as directory,
            ):
                capture = admit_external_capture(
                    TTSIM_EVIDENCE / f"captures/{case_id}/bundle.json"
                )
                self.assertEqual(capture.document.outcome.outcome, "pass")
                self.assertEqual(
                    capture.document.intended_classification, "functional_capture"
                )
                self.assertEqual(capture.document.conditions.measurement.state, "unknown")
                reason = capture.document.conditions.measurement.reason
                self.assertIsNotNone(reason)
                assert reason is not None
                self.assertIn(
                    "excluded from silicon evidence",
                    reason,
                )

                regenerated = Path(directory) / case_id
                conversion = convert_functional_capture(capture, regenerated)
                expected = TTSIM_EVIDENCE / f"references/{case_id}"
                self.assertEqual(_tree_bytes(regenerated), _tree_bytes(expected))
                imported = import_reference(conversion.reference_path)
                self.assertEqual(imported.provenance.classification, "functional_capture")
                self.assertEqual(imported.observations.execution, "complete")  # type: ignore[union-attr]
                self.assertEqual(len(imported.observations.effects), 2)  # type: ignore[union-attr]

    def _root(self, directory: str) -> Path:
        root = Path(directory) / "external"
        shutil.copytree(EXTERNAL, root)
        return root

    def test_three_case_families_convert_to_hash_verified_v1_references(self):
        for case_id in ("noc-64b", "dram-read-256b", "compute-bf16-32"):
            with (
                self.subTest(case_id=case_id),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = self._root(directory)
                raw = _functional_record(
                    root / "campaign.valid.json", case_id
                ).model_dump(mode="json")
                capture = admit_external_capture(_capture(root, case_id, raw))
                converted = convert_functional_capture(
                    capture, root / f"converted/{case_id}"
                )
                imported = import_reference(converted.reference_path)
                self.assertEqual(
                    imported.provenance.classification, "functional_capture"
                )
                self.assertEqual(imported.observations.execution, "complete")  # type: ignore[union-attr]
                self.assertEqual(len(imported.observations.effects), 2)  # type: ignore[union-attr]
                self.assertTrue(converted.mappings.event_mappings)
                self.assertEqual(
                    converted.mappings.source_artifact_sha256,
                    capture.document.raw_artifacts[0].sha256,
                )

    def test_runtime_metadata_is_preserved_without_weakening_workload_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            raw = _functional_record(
                root / "campaign.valid.json", "noc-64b"
            ).model_dump(mode="json")
            capture_path = _capture(root, "noc-64b", raw)
            document = json.loads(capture_path.read_text())
            document["conditions"]["device"] = {
                "state": "known",
                "value": "runtime-selected-device",
            }
            document["conditions"]["firmware"] = {
                "state": "known",
                "value": "runtime-firmware-v1",
            }
            document["conditions"]["clocks"]["value"][0]["hz"]["value"] = 900000000
            capture_path.write_text(json.dumps(document))
            converted = convert_functional_capture(
                admit_external_capture(capture_path), root / "converted/runtime"
            )
            imported = import_reference(converted.reference_path)
            self.assertEqual(
                converted.reference.conditions.device.value,
                "runtime-selected-device",
            )
            self.assertEqual(
                converted.reference.conditions.firmware.value,
                "runtime-firmware-v1",
            )
            self.assertEqual(
                imported.observations.clocks[0].hz.value,  # type: ignore[union-attr]
                900000000,
            )

    def test_payload_marker_address_count_and_path_corruptions_fail(self):
        variants: list[tuple[str, Callable[[dict[str, object]], None], str]] = []

        def payload(document: dict[str, object]) -> None:
            repetition = document["repetitions"][0]  # type: ignore[index]
            repetition["sentinel_payload_hex"] = "00" * 64  # type: ignore[index]
            repetition["sentinel_sha256"] = hashlib.sha256(bytes(64)).hexdigest()  # type: ignore[index]

        def marker(document: dict[str, object]) -> None:
            document["repetitions"][0].pop("completion_marker")  # type: ignore[index]

        def address(document: dict[str, object]) -> None:
            document["repetitions"][0]["effects"][0]["offset_bytes"] = 8196  # type: ignore[index]

        def count(document: dict[str, object]) -> None:
            document["repetitions"][0]["effects"][0]["count"] = 2  # type: ignore[index]

        def simulator_path(document: dict[str, object]) -> None:
            document["simulator_path"] = "/tmp/private-ttsim"

        variants.extend(
            (
                ("payload", payload, "sentinel payload"),
                ("marker", marker, "completion_marker"),
                ("address", address, "address/size"),
                ("count", count, "effect count"),
                ("path", simulator_path, "simulator-specific paths"),
            )
        )
        for name, mutate, reason in variants:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = self._root(directory)
                raw = _functional_record(
                    root / "campaign.valid.json", "noc-64b"
                ).model_dump(mode="json")
                mutate(raw)
                admitted = admit_external_capture(_capture(root, "noc-64b", raw))
                with self.assertRaisesRegex(ValueError, reason):
                    convert_functional_capture(admitted, root / f"converted/{name}")

    def test_post_admission_payload_change_is_detected_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self._root(directory)
            raw = _functional_record(
                root / "campaign.valid.json", "noc-64b"
            ).model_dump(mode="json")
            admitted = admit_external_capture(_capture(root, "noc-64b", raw))
            admitted.artifact_paths[1].write_bytes(b"changed")
            output = root / "converted/tampered"
            with self.assertRaisesRegex(ValueError, "changed after capture admission"):
                convert_functional_capture(admitted, output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()

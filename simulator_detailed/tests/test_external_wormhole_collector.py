"""Wormhole collection-plan, blocked-capture and profiler conversion tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import cast
from unittest.mock import patch

from simulator_detailed.configs.schemas.external_validation import (
    ArtifactLineage,
    CaptureCounter,
    CaptureEnvironment,
    ExternalArtifactIdentity,
    ExternalCaptureBundle,
    ExternalOutcome,
    ExternalValidationCase,
    WormholeCollectionPlan,
    WormholeDeviceSelection,
    WormholeWorkerResult,
)
from simulator_detailed.configs.schemas.validation import (
    CanonicalJSON,
    MeasurementWindow,
    Metadata,
    ProfilerSelection,
    ReferenceConditions,
    TimePoint,
)
from simulator_detailed.validation.external import (
    AdmittedExternalCapture,
    admit_external_campaign,
    admit_external_capture,
)
from simulator_detailed.validation.external_capture import generate_ttsim_capture_kit
from simulator_detailed.validation.external_collector import (
    collect_wormhole_capture,
    convert_profiler_capture,
    plan_wormhole_collection,
    write_unavailable_wormhole_capture,
)
from simulator_detailed.validation.identity import bytes_digest, canonical_record
from simulator_detailed.validation.references import (
    PINNED_CSV_HEADER,
    import_reference,
)

ROOT = Path(__file__).resolve().parents[2]
EXTERNAL = ROOT / "simulator_detailed/configs/validation/external"
ASSETS = ROOT / "simulator_detailed/validation/capture_assets/ttsim_tt_metal_v1"
PROFILER_COUNTER_BASE = 2**53 + 101


def _zone_line(path: Path, zone: str) -> int:
    matches = [
        number
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if f'DeviceZoneScopedN("{zone}")' in line
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one {zone} profiler zone")
    return matches[0]


def _profiler_csv(selection: ProfilerSelection) -> bytes:
    rows = [
        "ARCH: wormhole_b0, CHIP_FREQ[MHz]: 1000, Max Compute Cores: 64",
        ",".join(PINNED_CSV_HEADER),
    ]
    for occurrence, begin, end in (
        (0, PROFILER_COUNTER_BASE, PROFILER_COUNTER_BASE + 19),
        (1, PROFILER_COUNTER_BASE + 100, PROFILER_COUNTER_BASE + 137),
    ):
        common = [
            selection.device,
            str(selection.core_x),
            str(selection.core_y),
            selection.risc,
            "7",
        ]
        suffix = [
            str(PROFILER_COUNTER_BASE + 1000 + occurrence),
            str(700 + occurrence),
            str(900 + occurrence),
            str(1100 + occurrence),
            selection.zone,
            "",
            str(selection.source_line),
            selection.source_file,
            "{runtime:pinned;sample:" + str(occurrence) + "}",
        ]
        begin_row = [*common, str(begin), *suffix]
        begin_row[11] = "ZONE_START"
        end_row = [*common, str(end), *suffix]
        end_row[11] = "ZONE_END"
        rows.extend((",".join(begin_row), ",".join(end_row)))
    return ("\n".join(rows) + "\n").encode()


def _noc_selection(case: ExternalValidationCase) -> ProfilerSelection:
    boundary = case.boundary_maps[0]
    return ProfilerSelection(
        device="0",
        core_x=0,
        core_y=0,
        risc="BRISC",
        zone=boundary.producer_zone,
        source_file="kernels/noc_ack_roundtrip.cpp",
        source_line=_zone_line(
            ASSETS / "kernels/noc_ack_roundtrip.cpp", "NOC_ACK_ROUNDTRIP"
        ),
        clock_domain=boundary.clock_domain,
        metric_id=boundary.simulator_interval.metric_id,
        boundary=boundary.simulator_boundary,
        run_ids=(0, 1),
        warmup_run_ids=(0,),
        aggregation="none",
    )


def _captured_conditions(
    case: ExternalValidationCase, selection: ProfilerSelection
) -> ReferenceConditions:
    measurement = MeasurementWindow(
        boundary=selection.boundary,
        start=TimePoint(
            value=PROFILER_COUNTER_BASE + 100,
            unit="cycles",
            clock_domain=selection.clock_domain,
        ),
        end=TimePoint(
            value=PROFILER_COUNTER_BASE + 137,
            unit="cycles",
            clock_domain=selection.clock_domain,
        ),
        excluded_warmups=("0",),
        repetitions=1,
        aggregation="none",
    )
    return case.conditions.model_copy(
        update={
            "device": Metadata[str](state="known", value="0000:01:00.0"),
            "software": Metadata[str](state="known", value="pinned-test-build"),
            "firmware": Metadata[str](state="known", value="test-firmware"),
            "measurement": Metadata[MeasurementWindow](state="known", value=measurement),
        }
    )


def _hardware_capture(
    root: Path,
    profiler_transform: Callable[[bytes], bytes] = lambda value: value,
) -> tuple[AdmittedExternalCapture, Path]:
    shutil.copytree(EXTERNAL, root, dirs_exist_ok=True)
    campaign_path = root / "campaign.valid.json"
    campaign = admit_external_campaign(campaign_path)
    case = next(item for item in campaign.document.cases if item.case_id == "noc-64b")
    producer = next(
        item
        for item in campaign.document.producers
        if item.adapter == "wormhole_tt_metal_profiler_v1"
    )
    build = next(
        item for item in campaign.document.builds if item.build_id == producer.build_id
    )
    binding = next(
        item for item in case.producers if item.producer_id == producer.producer_id
    )
    selection = _noc_selection(case)
    payloads = {
        "functional_record": b'{"test_only":"functional placeholder"}\n',
        "profiler_csv": profiler_transform(_profiler_csv(selection)),
        "capture_manifest": b'{"test_only":"worker manifest placeholder"}\n',
    }
    artifacts: list[ExternalArtifactIdentity] = []
    profiler_path: Path | None = None
    for declaration in binding.outputs:
        data = payloads[declaration.role]
        path = root / declaration.logical_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        artifacts.append(
            ExternalArtifactIdentity(
                artifact_id=declaration.artifact_id,
                logical_path=declaration.logical_path,
                sha256=bytes_digest(data),
                size_bytes=len(data),
            )
        )
        if declaration.role == "profiler_csv":
            profiler_path = path
    if profiler_path is None:
        raise AssertionError("test campaign omitted profiler output")
    conditions = _captured_conditions(case, selection)
    campaign_data = campaign_path.read_bytes()
    document = ExternalCaptureBundle(
        kind="external_capture_bundle",
        schema_version=1,
        bundle_id="test-wormhole-capture",
        campaign=ExternalArtifactIdentity(
            artifact_id="campaign",
            logical_path="campaign.valid.json",
            sha256=bytes_digest(campaign_data),
            size_bytes=len(campaign_data),
        ),
        case_id=case.case_id,
        producer_id=producer.producer_id,
        adapter=producer.adapter,
        build=build,
        intended_classification="hardware_capture",
        environment=CaptureEnvironment(
            host=Metadata[CanonicalJSON](
                state="known", value=canonical_record({"worker_id": "test-worker"})
            ),
            device=Metadata[str](state="known", value="0000:01:00.0"),
            software=Metadata[str](state="known", value="pinned-test-build"),
            firmware=Metadata[str](state="known", value="test-firmware"),
            clocks=Metadata[tuple[CanonicalJSON, ...]](
                state="known",
                value=(canonical_record({"domain_id": "tensix", "hz": 1_000_000_000}),),
            ),
            enabled_layout=case.conditions.enabled_layout,
        ),
        conditions=conditions,
        outcome=ExternalOutcome(
            stage="collection",
            required=True,
            outcome="pass",
            reason="synthetic unit-test bytes exercising the hardware import contract",
            executed=True,
            case_id=case.case_id,
            producer_id=producer.producer_id,
            artifact_ids=tuple(item.artifact_id for item in artifacts),
        ),
        raw_artifacts=tuple(artifacts),
        profiler_selections=(selection,),
        counters=(CaptureCounter(name="repetitions", value=2, unit="count"),),
        lineage=tuple(
            ArtifactLineage(
                artifact_id=item.artifact_id,
                derived_from=("campaign",),
                transform="synthetic_wormhole_collector_test",
            )
            for item in artifacts
        ),
        diagnostics=("unit-test data; not external evidence",),
    )
    bundle_path = root / "bundle.json"
    bundle_path.write_text(document.model_dump_json(indent=2) + "\n")
    return admit_external_capture(bundle_path), profiler_path


class WormholeCollectorTests(unittest.TestCase):
    def test_plan_has_fixed_arguments_explicit_device_and_finite_budget(self):
        campaign = admit_external_campaign(EXTERNAL / "campaign.valid.json")
        selection = WormholeDeviceSelection(
            worker_id="worker-1",
            device_index=2,
            pcie_slot="0000:03:00.0",
            architecture="wormhole_b0",
        )
        plan = plan_wormhole_collection(campaign, "noc-64b", selection)
        self.assertEqual(plan.selection, selection)
        self.assertEqual(plan.argv[20:], ("2", "--pcie-slot", "0000:03:00.0"))
        self.assertEqual(
            {item.name for item in plan.environment},
            {"TT_METAL_DEVICE_PROFILER", "TT_METAL_SLOW_DISPATCH_MODE"},
        )
        self.assertEqual(
            {item.role for item in plan.outputs},
            {"functional_record", "profiler_csv", "capture_manifest"},
        )
        malformed = plan.model_dump(mode="json")
        malformed["argv"][20] = "1"
        with self.assertRaisesRegex(ValueError, "device selection"):
            WormholeCollectionPlan.model_validate_json(json.dumps(malformed))

    def test_unavailable_capture_is_atomic_and_does_not_launch_processes(self):
        campaign = admit_external_campaign(EXTERNAL / "campaign.valid.json")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "capture"
            other = root / "other"
            other.mkdir()
            old_cwd = Path.cwd()
            try:
                os.chdir(other)
                with patch("subprocess.run", side_effect=AssertionError("unexpected process")):
                    capture = write_unavailable_wormhole_capture(
                        campaign, "noc-64b", output
                    )
            finally:
                os.chdir(old_cwd)
            self.assertEqual(capture.document.outcome.outcome, "blocked")
            self.assertEqual(capture.document.raw_artifacts, ())
            for field in (
                "device",
                "software",
                "firmware",
                "clocks",
                "enabled_layout",
            ):
                self.assertEqual(getattr(capture.document.environment, field).state, "unknown")
            with self.assertRaisesRegex(ValueError, "already exists"):
                write_unavailable_wormhole_capture(campaign, "noc-64b", output)

    def test_collector_blocks_before_launch_when_explicit_worker_is_missing(self):
        campaign = admit_external_campaign(EXTERNAL / "campaign.valid.json")
        selection = WormholeDeviceSelection(
            worker_id="missing-worker",
            device_index=0,
            pcie_slot="0000:01:00.0",
            architecture="wormhole_b0",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "simulator_detailed.validation.external_collector.subprocess.run",
                side_effect=AssertionError("producer must not launch"),
            ):
                capture = collect_wormhole_capture(
                    campaign,
                    "noc-64b",
                    selection,
                    root / "missing-worker",
                    root / "blocked-capture",
                )
            self.assertEqual(capture.document.outcome.outcome, "blocked")
            self.assertIn("prerequisites unavailable", capture.document.outcome.reason)

    def test_collector_verifies_fixed_worker_outputs_and_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            campaign_root = root / "campaign"
            worker = root / "worker"
            shutil.copytree(EXTERNAL, campaign_root)
            worker.mkdir()
            document = json.loads((campaign_root / "campaign.valid.json").read_text())
            binaries = {
                "host_binary": b"bounded test host binary",
                "device_binary": b"bounded test device binary",
            }
            for artifact in document["builds"][0]["artifacts"]:
                data = binaries[artifact["role"]]
                artifact["sha256"] = bytes_digest(data)
                artifact["size_bytes"] = len(data)
                path = worker / artifact["logical_path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            campaign_path = campaign_root / "campaign.valid.json"
            campaign_path.write_text(json.dumps(document, indent=2) + "\n")
            campaign = admit_external_campaign(campaign_path)
            campaign_data = campaign_path.read_bytes()
            (worker / "campaign.json").write_bytes(campaign_data)
            case = next(
                item for item in campaign.document.cases if item.case_id == "noc-64b"
            )
            kit_input = worker / "inputs/noc-64b.json"
            kit_input.parent.mkdir()
            kit_input.write_bytes(
                (campaign_root / case.simulator_input.logical_path).read_bytes()
            )
            device = WormholeDeviceSelection(
                worker_id="worker-1",
                device_index=0,
                pcie_slot="0000:01:00.0",
                architecture="wormhole_b0",
            )
            plan = plan_wormhole_collection(campaign, case.case_id, device)
            profiler = _noc_selection(case)
            conditions = _captured_conditions(case, profiler)
            environment = CaptureEnvironment(
                host=Metadata[CanonicalJSON](
                    state="known", value=canonical_record({"worker_id": "worker-1"})
                ),
                device=Metadata[str](state="known", value=device.pcie_slot),
                software=Metadata[str](state="known", value="pinned-test-build"),
                firmware=Metadata[str](state="known", value="test-firmware"),
                clocks=Metadata[tuple[CanonicalJSON, ...]](
                    state="known",
                    value=(
                        canonical_record(
                            {"domain_id": "tensix", "hz": 1_000_000_000}
                        ),
                    ),
                ),
                enabled_layout=case.conditions.enabled_layout,
            )
            worker_result = WormholeWorkerResult(
                kind="wormhole_worker_result",
                schema_version=1,
                plan_id=plan.plan_id,
                campaign_id=plan.campaign_id,
                case_id=plan.case_id,
                producer_id=plan.producer.producer_id,
                build_id=plan.build.build_id,
                selection=device,
                environment=environment,
                conditions=conditions,
                profiler_selections=(profiler,),
                counters=(CaptureCounter(name="repetitions", value=2, unit="count"),),
                diagnostics=("unit-test worker result",),
            )
            wrong_device = worker_result.model_dump(mode="json")
            wrong_device["profiler_selections"][0]["device"] = "0000:02:00.0"
            with self.assertRaisesRegex(ValueError, "device selection"):
                WormholeWorkerResult.model_validate_json(json.dumps(wrong_device))

            def run_worker(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                self.assertEqual(command[1:], list(plan.argv[1:]))
                self.assertEqual(kwargs["cwd"], worker.resolve())
                process_environment = cast(dict[str, str], kwargs["env"])
                self.assertEqual(process_environment["TT_METAL_DEVICE_PROFILER"], "1")
                payloads = {
                    "functional_record": b'{"test_only":"functional placeholder"}\n',
                    "profiler_csv": _profiler_csv(profiler),
                    "capture_manifest": (
                        worker_result.model_dump_json(indent=2) + "\n"
                    ).encode(),
                }
                for declaration in plan.outputs:
                    path = worker / declaration.logical_path
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(payloads[declaration.role])
                return subprocess.CompletedProcess(command, 0)

            output = root / "capture"
            with patch(
                "simulator_detailed.validation.external_collector.subprocess.run",
                side_effect=run_worker,
            ) as launched:
                capture = collect_wormhole_capture(
                    campaign, case.case_id, device, worker, output
                )
            launched.assert_called_once()
            self.assertEqual(capture.document.outcome.outcome, "pass")
            self.assertEqual(capture.document.profiler_selections, (profiler,))
            self.assertEqual(len(capture.document.raw_artifacts), 3)
            converted = convert_profiler_capture(capture, root / "references")[0]
            self.assertIsNotNone(converted.sample_statistics)
            if converted.sample_statistics is None:
                raise AssertionError("profiler conversion omitted sample statistics")
            self.assertEqual(
                converted.sample_statistics.durations_cycles,
                (37,),
            )

    def test_sparse_zones_are_unique_and_both_producers_share_one_build(self):
        admitted = admit_external_campaign(EXTERNAL / "campaign.valid.json")
        campaign = admitted.document
        self.assertEqual(
            {item.build_id for item in campaign.producers},
            {"tt-metal-wormhole-pinned"},
        )
        expected = {
            "noc_ack_roundtrip": ("noc_ack_roundtrip.cpp", "NOC_ACK_ROUNDTRIP"),
            "dram_read_return": ("dram_read_return.cpp", "DRAM_READ_RETURN"),
            "compute_service": ("compute_service.cpp", "COMPUTE_SERVICE"),
        }
        identities: set[tuple[str, int, str]] = set()
        with tempfile.TemporaryDirectory() as directory:
            kit = generate_ttsim_capture_kit(admitted, Path(directory) / "kit")
            for case in campaign.cases:
                filename, zone = expected[case.family]
                line = _zone_line(ASSETS / "kernels" / filename, zone)
                self.assertEqual(case.boundary_maps[0].producer_zone, zone)
                identities.add((filename, line, zone))
                plan = plan_wormhole_collection(
                    admitted,
                    case.case_id,
                    WormholeDeviceSelection(
                        worker_id="worker-1",
                        device_index=0,
                        pcie_slot="0000:01:00.0",
                        architecture="wormhole_b0",
                    ),
                )
                self.assertEqual(plan.build, kit.build)
                self.assertEqual(plan.binary_manifest, kit.binary_manifest)
                self.assertEqual(plan.conditions, case.conditions)
        self.assertEqual(len(identities), len(expected))

    def test_verified_hardware_csv_converts_with_exact_integer_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "capture"
            root.mkdir()
            admitted, _ = _hardware_capture(root)
            output = Path(directory) / "references"
            references = convert_profiler_capture(admitted, output)
            self.assertEqual(len(references), 1)
            reference = references[0]
            self.assertEqual(reference.provenance.classification, "hardware_capture")
            self.assertIsNotNone(reference.sample_statistics)
            self.assertIsNotNone(reference.observations)
            if reference.sample_statistics is None or reference.observations is None:
                raise AssertionError("profiler conversion omitted normalized evidence")
            self.assertEqual(reference.sample_statistics.durations_cycles, (37,))
            event = reference.observations.events[0]
            self.assertIsNotNone(event.time)
            self.assertIsNotNone(event.details)
            if event.time is None or event.details is None:
                raise AssertionError("pinned profiler event omitted raw timing details")
            self.assertGreater(event.time.value, 2**53)
            self.assertEqual(
                {counter.name: counter.value for counter in event.counters},
                {
                    "core_x": 0,
                    "core_y": 0,
                    "timer_id": 7,
                    "data": PROFILER_COUNTER_BASE + 1000,
                    "run_host_id": 700,
                    "source_line": _zone_line(
                        ASSETS / "kernels/noc_ack_roundtrip.cpp",
                        "NOC_ACK_ROUNDTRIP",
                    ),
                    "max_compute_cores": 64,
                    "trace_id": 900,
                    "trace_id_counter": 1100,
                },
            )
            self.assertEqual(
                json.loads(event.details.text)["meta_data"],
                "{runtime:pinned;sample:0}",
            )
            self.assertEqual(
                import_reference(output / "reference-0/reference.json"), reference
            )
            with self.assertRaisesRegex(ValueError, "already exists"):
                convert_profiler_capture(admitted, output)

    def test_conversion_rechecks_raw_hash_after_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "capture"
            root.mkdir()
            admitted, profiler = _hardware_capture(root)
            profiler.write_bytes(profiler.read_bytes() + b"changed\n")
            with self.assertRaisesRegex(ValueError, "changed after capture admission"):
                convert_profiler_capture(admitted, Path(directory) / "references")

    def test_pinned_profiler_dialect_rejects_unsafe_pairing_and_format_drift(self):
        def replace(old: bytes, new: bytes) -> Callable[[bytes], bytes]:
            return lambda raw: raw.replace(old, new, 1)

        def reorder_nested(raw: bytes) -> bytes:
            lines = raw.splitlines()
            return b"\n".join((lines[0], lines[1], lines[2], lines[4], lines[3], lines[5])) + b"\n"

        def move_last_marker(raw: bytes, field: int, value: bytes) -> bytes:
            lines = raw.splitlines()
            row = lines[-1].split(b",")
            row[field] = value
            lines[-1] = b",".join(row)
            return b"\n".join(lines) + b"\n"

        corruptions = (
            (
                "header",
                replace(b"run host ID", b"runtime ID"),
                "columns/order",
            ),
            (
                "phase",
                replace(b"ZONE_START", b"TS_DATA"),
                "phase",
            ),
            ("nested", reorder_nested, "nested/ambiguous"),
            (
                "architecture",
                replace(b"ARCH: wormhole_b0", b"ARCH: grayskull"),
                "architecture contradicts",
            ),
            (
                "frequency",
                replace(b"CHIP_FREQ[MHz]: 1000", b"CHIP_FREQ[MHz]: 999"),
                "frequency contradicts",
            ),
            (
                "other-core-end",
                lambda raw: move_last_marker(raw, 1, b"1"),
                "missing selected profiler zone end",
            ),
            (
                "other-device-end",
                lambda raw: move_last_marker(raw, 0, b"0000:02:00.0"),
                "missing selected profiler zone end",
            ),
            (
                "different-timer-end",
                lambda raw: move_last_marker(raw, 4, b"8"),
                "counter identities disagree",
            ),
        )
        for name, transform, message in corruptions:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / "capture"
                root.mkdir()
                admitted, _ = _hardware_capture(root, transform)
                with self.assertRaisesRegex(ValueError, message):
                    convert_profiler_capture(
                        admitted,
                        Path(directory) / "references",
                    )

    def test_capture_admission_rejects_wrong_zone_and_missing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "capture"
            root.mkdir()
            admitted, _ = _hardware_capture(root)
            document = admitted.document.model_dump(mode="json")
            document["profiler_selections"][0]["zone"] = "UNDECLARED_ZONE"
            path = root / "wrong-zone.json"
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, "omits a declared profiler zone"):
                admit_external_capture(path)
            document = admitted.document.model_dump(mode="json")
            document["raw_artifacts"] = document["raw_artifacts"][:-1]
            document["outcome"]["artifact_ids"] = document["outcome"]["artifact_ids"][:-1]
            document["lineage"] = document["lineage"][:-1]
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(ValueError, "every declared output role"):
                admit_external_capture(path)


if __name__ == "__main__":
    unittest.main()

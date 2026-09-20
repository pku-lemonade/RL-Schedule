"""Fixed Wormhole collection plans and safe unavailable capture bundles."""

from __future__ import annotations

import os
import platform
import subprocess
import tempfile
from pathlib import Path

from ..configs.schemas.external_validation import (
    ArtifactLineage,
    BuildArtifactIdentity,
    CaptureEnvironment,
    ExternalArtifactIdentity,
    ExternalCaptureBundle,
    ExternalOutcome,
    ExternalValidationCampaign,
    WormholeCollectionPlan,
    WormholeDeviceSelection,
    WormholeProfilerEnvironmentVariable,
    WormholeWorkerResult,
)
from ..configs.schemas.validation import (
    ArtifactReference,
    CanonicalJSON,
    Metadata,
    ReferenceProvenance,
    ValidationReference,
)
from .data import require
from .external import (
    AdmittedExternalCampaign,
    AdmittedExternalCapture,
    admit_external_capture,
)
from .external_capture import RECIPE_BY_FAMILY
from .identity import bytes_digest, canonical_record, content_digest
from .references import EXTRACTOR, EXTRACTOR_VERSION, import_reference


def _worker_path(worker_root: Path, logical_path: str) -> Path:
    root = worker_root.resolve()
    path = (root / logical_path).resolve()
    if not path.is_relative_to(root):
        raise ValueError("worker artifact escapes the explicit worker root")
    return path


def _verified_bytes(
    path: Path, identity: ExternalArtifactIdentity | BuildArtifactIdentity
) -> bytes:
    data = path.read_bytes()
    require(
        len(data) == identity.size_bytes and bytes_digest(data) == identity.sha256,
        f"worker artifact identity mismatch: {identity.logical_path}",
    )
    return data


def plan_wormhole_collection(
    admitted: AdmittedExternalCampaign,
    case_id: str,
    selection: WormholeDeviceSelection,
) -> WormholeCollectionPlan:
    """Build the only accepted argv/environment for one explicit Wormhole device."""
    document = admitted.document
    case = next((item for item in document.cases if item.case_id == case_id), None)
    if case is None:
        raise ValueError(f"unknown Wormhole collection case: {case_id}")
    producers = [
        item
        for item in document.producers
        if item.adapter == "wormhole_tt_metal_profiler_v1"
    ]
    require(len(producers) == 1, "campaign requires exactly one named Wormhole profiler producer")
    producer = producers[0]
    build = next(item for item in document.builds if item.build_id == producer.build_id)
    host = next((item for item in build.artifacts if item.role == "host_binary"), None)
    if host is None:
        raise ValueError("Wormhole build omits its host binary")
    binding = next(item for item in case.producers if item.producer_id == producer.producer_id)
    outputs = tuple(
        item.model_copy(update={"logical_path": "outputs/" + item.logical_path})
        for item in binding.outputs
    )
    functional = next(item for item in outputs if item.role == "functional_record")
    profiler = next(item for item in outputs if item.role == "profiler_csv")
    manifest = next(item for item in outputs if item.role == "capture_manifest")
    input_path = f"inputs/{case.case_id}.json"
    argv = (
        host.logical_path,
        "--recipe", RECIPE_BY_FAMILY[case.family],
        "--input", input_path,
        "--functional-output", functional.logical_path,
        "--profiler-output", profiler.logical_path,
        "--manifest-output", manifest.logical_path,
        "--repetitions", str(case.budget.repetitions),
        "--warmup-repetitions", str(case.budget.warmup_repetitions),
        "--timeout-seconds", format(case.budget.timeout_seconds, "g"),
        "--max-output-bytes", str(case.budget.max_output_bytes),
        "--device-index", str(selection.device_index),
        "--pcie-slot", selection.pcie_slot,
    )
    campaign_data = admitted.document_path.read_bytes()
    identity = {
        "campaign_sha256": bytes_digest(campaign_data),
        "case_id": case.case_id,
        "selection": selection.model_dump(mode="json"),
        "conditions": case.conditions.model_dump(mode="json"),
        "argv": argv,
        "outputs": [item.model_dump(mode="json") for item in outputs],
    }
    return WormholeCollectionPlan(
        kind="wormhole_collection_plan",
        schema_version=1,
        plan_id="wormhole-plan:" + content_digest(identity),
        campaign=ExternalArtifactIdentity(
            artifact_id="campaign", logical_path="campaign.json",
            sha256=bytes_digest(campaign_data), size_bytes=len(campaign_data),
        ),
        campaign_id=document.campaign_id,
        case_id=case.case_id,
        producer=producer,
        build=build,
        binary_manifest=build.artifacts,
        selection=selection,
        conditions=case.conditions,
        argv=argv,
        environment=(
            WormholeProfilerEnvironmentVariable(name="TT_METAL_DEVICE_PROFILER", value="1"),
            WormholeProfilerEnvironmentVariable(name="TT_METAL_SLOW_DISPATCH_MODE", value="1"),
        ),
        repetition_ids=case.boundary_maps[0].samples.repetition_ids,
        warmup_repetition_ids=case.boundary_maps[0].samples.warmup_repetition_ids,
        timeout_seconds=case.budget.timeout_seconds,
        max_output_bytes=case.budget.max_output_bytes,
        outputs=outputs,
    )


def write_unavailable_wormhole_capture(
    admitted: AdmittedExternalCampaign,
    case_id: str,
    output_directory: Path,
    *,
    reason: str = "no explicit Wormhole device selection and worker root were supplied",
) -> AdmittedExternalCapture:
    """Record a blocked result without examining device nodes or launching tools."""
    document = admitted.document
    case = next((item for item in document.cases if item.case_id == case_id), None)
    if case is None:
        raise ValueError(f"unknown Wormhole collection case: {case_id}")
    producer = next(
        item for item in document.producers if item.adapter == "wormhole_tt_metal_profiler_v1"
    )
    build = next(item for item in document.builds if item.build_id == producer.build_id)
    campaign_data = admitted.document_path.read_bytes()
    conditions = case.conditions.model_copy(update={
        "device": case.conditions.device.model_copy(update={"state": "unknown", "value": None, "reason": reason}),
        "enabled_layout": case.conditions.enabled_layout.model_copy(
            update={"state": "unknown", "value": None, "reason": "device layout was not queried"}
        ),
        "clocks": case.conditions.clocks.model_copy(
            update={"state": "unknown", "value": None, "reason": "device clocks were not queried"}
        ),
        "software": case.conditions.software.model_copy(
            update={"state": "unknown", "value": None, "reason": "worker software was not queried"}
        ),
        "firmware": case.conditions.firmware.model_copy(
            update={"state": "unknown", "value": None, "reason": "device firmware was not queried"}
        ),
        "instrumentation": case.conditions.instrumentation.model_copy(
            update={"state": "unknown", "value": None, "reason": "device profiler was not started"}
        ),
        "measurement": case.conditions.measurement.model_copy(
            update={"state": "unknown", "value": None, "reason": "no profiler interval exists"}
        ),
        "capture_group": case.conditions.capture_group.model_copy(
            update={"state": "unknown", "value": None, "reason": "collection did not start"}
        ),
    })
    bundle = ExternalCaptureBundle(
        kind="external_capture_bundle",
        schema_version=1,
        bundle_id="wormhole-unavailable:" + content_digest({
            "campaign": bytes_digest(campaign_data), "case": case.case_id, "reason": reason,
        }),
        campaign=ExternalArtifactIdentity(
            artifact_id="campaign", logical_path="campaign.json",
            sha256=bytes_digest(campaign_data), size_bytes=len(campaign_data),
        ),
        case_id=case.case_id,
        producer_id=producer.producer_id,
        adapter=producer.adapter,
        build=build,
        intended_classification="hardware_capture",
        environment=CaptureEnvironment(
            host=Metadata[CanonicalJSON](state="known", value=canonical_record({
                "machine": platform.machine(), "system": platform.system(),
            })),
            device=Metadata[str](state="unknown", reason=reason),
            software=Metadata[str](state="unknown", reason="worker software was not queried"),
            firmware=Metadata[str](state="unknown", reason="device firmware was not queried"),
            clocks=Metadata[tuple[CanonicalJSON, ...]](
                state="unknown", reason="device clocks were not queried"
            ),
            enabled_layout=Metadata[CanonicalJSON](
                state="unknown", reason="device layout was not queried"
            ),
        ),
        conditions=conditions,
        outcome=ExternalOutcome(
            stage="collection", required=True, outcome="blocked", reason=reason, executed=True,
            case_id=case.case_id, producer_id=producer.producer_id, artifact_ids=(),
        ),
        raw_artifacts=(), counters=(), lineage=(),
        diagnostics=(reason, "blocked before device access; no producer process launched"),
    )
    if output_directory.exists():
        raise ValueError("Wormhole capture output directory already exists")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".wormhole-unavailable-", dir=output_directory.parent) as temporary:
        staged = Path(temporary) / "capture"
        staged.mkdir()
        (staged / "campaign.json").write_bytes(campaign_data)
        (staged / "bundle.json").write_bytes((bundle.model_dump_json(indent=2) + "\n").encode())
        staged.rename(output_directory)
    return admit_external_capture(output_directory / "bundle.json")


def collect_wormhole_capture(
    admitted: AdmittedExternalCampaign,
    case_id: str,
    selection: WormholeDeviceSelection,
    worker_root: Path,
    output_directory: Path,
) -> AdmittedExternalCapture:
    """Run one fixed collection plan or emit a blocked bundle on unavailable workers."""
    plan = plan_wormhole_collection(admitted, case_id, selection)
    if output_directory.exists():
        raise ValueError("Wormhole capture output directory already exists")
    root = worker_root.resolve()
    try:
        campaign_path = _worker_path(root, "campaign.json")
        _verified_bytes(campaign_path, plan.campaign)
        input_identity = next(
            item
            for item in admitted.document.cases
            if item.case_id == case_id
        ).simulator_input
        input_path = _worker_path(root, plan.argv[4])
        _verified_bytes(input_path, input_identity)
        for artifact in plan.binary_manifest:
            _verified_bytes(_worker_path(root, artifact.logical_path), artifact)
        output_paths = {
            item.artifact_id: _worker_path(root, item.logical_path)
            for item in plan.outputs
        }
        if any(path.exists() for path in output_paths.values()):
            raise ValueError("worker output already exists; refusing to replace evidence")
    except (OSError, ValueError) as exc:
        return write_unavailable_wormhole_capture(
            admitted,
            case_id,
            output_directory,
            reason=f"explicit Wormhole worker prerequisites unavailable ({type(exc).__name__}: {exc})",
        )

    environment = os.environ.copy()
    environment.update({item.name: item.value for item in plan.environment})
    command = [str(_worker_path(root, plan.argv[0])), *plan.argv[1:]]
    try:
        result = subprocess.run(
            command,
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=plan.timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return write_unavailable_wormhole_capture(
            admitted,
            case_id,
            output_directory,
            reason=f"Wormhole producer did not complete ({type(exc).__name__})",
        )
    if result.returncode != 0:
        return write_unavailable_wormhole_capture(
            admitted,
            case_id,
            output_directory,
            reason=f"Wormhole producer exited with status {result.returncode}",
        )

    try:
        output_data = {
            artifact_id: path.read_bytes() for artifact_id, path in output_paths.items()
        }
        require(
            sum(len(data) for data in output_data.values())
            <= plan.max_output_bytes * len(plan.repetition_ids),
            "Wormhole producer exceeded its output-byte budget",
        )
        manifest_declaration = next(
            item for item in plan.outputs if item.role == "capture_manifest"
        )
        worker_result = WormholeWorkerResult.model_validate_json(
            output_data[manifest_declaration.artifact_id]
        )
        require(
            (
                worker_result.plan_id,
                worker_result.campaign_id,
                worker_result.case_id,
                worker_result.producer_id,
                worker_result.build_id,
                worker_result.selection,
            )
            == (
                plan.plan_id,
                plan.campaign_id,
                plan.case_id,
                plan.producer.producer_id,
                plan.build.build_id,
                plan.selection,
            ),
            "worker manifest identity disagrees with the admitted collection plan",
        )
    except (OSError, ValueError) as exc:
        return write_unavailable_wormhole_capture(
            admitted,
            case_id,
            output_directory,
            reason=f"Wormhole producer outputs are inadmissible ({type(exc).__name__}: {exc})",
        )

    declaration_by_id = {item.artifact_id: item for item in plan.outputs}
    artifacts = tuple(
        ExternalArtifactIdentity(
            artifact_id=artifact_id,
            logical_path=declaration_by_id[artifact_id].logical_path.removeprefix(
                "outputs/"
            ),
            sha256=bytes_digest(data),
            size_bytes=len(data),
        )
        for artifact_id, data in output_data.items()
    )
    campaign_data = admitted.document_path.read_bytes()
    bundle = ExternalCaptureBundle(
        kind="external_capture_bundle",
        schema_version=1,
        bundle_id="wormhole-capture:"
        + content_digest(
            {
                "campaign_sha256": admitted.document_sha256,
                "case_id": case_id,
                "selection": selection.model_dump(mode="json"),
                "artifacts": [item.model_dump(mode="json") for item in artifacts],
            }
        ),
        campaign=ExternalArtifactIdentity(
            artifact_id="campaign",
            logical_path="campaign.json",
            sha256=bytes_digest(campaign_data),
            size_bytes=len(campaign_data),
        ),
        case_id=case_id,
        producer_id=plan.producer.producer_id,
        adapter=plan.producer.adapter,
        build=plan.build,
        intended_classification="hardware_capture",
        environment=worker_result.environment,
        conditions=worker_result.conditions,
        outcome=ExternalOutcome(
            stage="collection",
            required=True,
            outcome="pass",
            reason="fixed Wormhole producer completed and all declared outputs were captured",
            executed=True,
            case_id=case_id,
            producer_id=plan.producer.producer_id,
            artifact_ids=tuple(item.artifact_id for item in artifacts),
        ),
        raw_artifacts=artifacts,
        profiler_selections=worker_result.profiler_selections,
        counters=worker_result.counters,
        lineage=tuple(
            ArtifactLineage(
                artifact_id=item.artifact_id,
                derived_from=("campaign",),
                transform="wormhole_tt_metal_profiler_v1",
            )
            for item in artifacts
        ),
        diagnostics=worker_result.diagnostics,
    )
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".wormhole-capture-", dir=output_directory.parent
    ) as temporary:
        staged = Path(temporary) / "capture"
        staged.mkdir()
        (staged / "campaign.json").write_bytes(campaign_data)
        for artifact in artifacts:
            destination = staged / artifact.logical_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(output_data[artifact.artifact_id])
        (staged / "bundle.json").write_bytes(
            (bundle.model_dump_json(indent=2) + "\n").encode()
        )
        staged.rename(output_directory)
    return admit_external_capture(output_directory / "bundle.json")


def convert_profiler_capture(
    admitted: AdmittedExternalCapture,
    output_directory: Path,
) -> tuple[ValidationReference, ...]:
    """Convert verified Wormhole CSV artifacts through the strict v1 importer."""
    capture = admitted.document
    if (
        capture.adapter != "wormhole_tt_metal_profiler_v1"
        or capture.intended_classification != "hardware_capture"
        or capture.outcome.outcome != "pass"
    ):
        raise ValueError("profiler conversion requires a successful Wormhole capture")
    campaign_path = admitted.artifact_paths[0]
    campaign = admitted.document.campaign
    campaign_document = admitted.document_path.parent / campaign.logical_path
    if campaign_document.resolve() != campaign_path:
        raise ValueError("admitted campaign path changed before profiler conversion")
    campaign_data = campaign_path.read_bytes()
    require(
        len(campaign_data) == campaign.size_bytes
        and bytes_digest(campaign_data) == campaign.sha256,
        "campaign artifact changed after capture admission",
    )
    specification = ExternalValidationCampaign.model_validate_json(campaign_data)
    case = next(item for item in specification.cases if item.case_id == capture.case_id)
    binding = next(item for item in case.producers if item.producer_id == capture.producer_id)
    profiler_output = next(item for item in binding.outputs if item.role == "profiler_csv")
    raw_index = {
        artifact.artifact_id: (artifact, path)
        for artifact, path in zip(
            capture.raw_artifacts, admitted.artifact_paths[1:], strict=True
        )
    }
    profiler_artifact, profiler_path = raw_index[profiler_output.artifact_id]
    if output_directory.exists():
        raise ValueError("profiler reference output directory already exists")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    references: list[ValidationReference] = []
    with tempfile.TemporaryDirectory(
        prefix=".wormhole-profiler-", dir=output_directory.parent
    ) as temporary:
        staged = Path(temporary) / "references"
        staged.mkdir()
        for index, selection in enumerate(capture.profiler_selections):
            reference_root = staged / f"reference-{index}"
            raw_root = reference_root / "raw"
            raw_root.mkdir(parents=True)
            raw_name = Path(profiler_artifact.logical_path).name
            raw_relative = f"raw/{raw_name}"
            raw_data = profiler_path.read_bytes()
            require(
                len(raw_data) == profiler_artifact.size_bytes
                and bytes_digest(raw_data) == profiler_artifact.sha256,
                "profiler artifact changed after capture admission",
            )
            (reference_root / raw_relative).write_bytes(raw_data)
            reference = ValidationReference(
                kind="validation_reference",
                schema_version=1,
                reference_id="wormhole-profiler:" + content_digest(
                    {
                        "bundle_sha256": admitted.document_sha256,
                        "raw_sha256": profiler_artifact.sha256,
                        "selection": selection.model_dump(mode="json"),
                    }
                ),
                format="tt_metal_device_profiler_csv_v1",
                provenance=ReferenceProvenance(
                    classification="hardware_capture",
                    producer=capture.producer_id,
                    source_url=Metadata[str](state="known", value=capture.build.source_url),
                    revision=Metadata[str](state="known", value=capture.build.revision),
                    snapshot_sha256=Metadata[str](
                        state="known", value=capture.build.source_snapshot_sha256
                    ),
                    raw_artifact=ArtifactReference(
                        path=raw_relative, sha256=profiler_artifact.sha256
                    ),
                    extractor=EXTRACTOR,
                    extractor_version=EXTRACTOR_VERSION,
                    original_units=("cycles",),
                    normalized_units=("cycles",),
                    origin_authentication=capture.origin_authentication,
                ),
                conditions=capture.conditions,
                observations=None,
                profiler=selection,
                sample_statistics=None,
            )
            sidecar = reference_root / "reference.json"
            sidecar.write_bytes((reference.model_dump_json(indent=2) + "\n").encode())
            imported = import_reference(sidecar)
            sidecar.write_bytes((imported.model_dump_json(indent=2) + "\n").encode())
            references.append(imported)
        staged.rename(output_directory)
    return tuple(references)

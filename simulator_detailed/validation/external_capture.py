"""Portable ttsim capture kits and functional-record conversion."""

from __future__ import annotations

import json
import os
import platform
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from ..configs.schemas.external_validation import (
    TTSIM_PINNED_REVISION,
    TTSIM_SOURCE_SNAPSHOT_SHA256,
    TTSIM_SOURCE_URL,
    ArtifactLineage,
    CaptureCounter,
    CaptureEnvironment,
    CaptureEnvironmentVariable,
    CaptureKitFile,
    CaptureKitFileRole,
    ExternalArtifactIdentity,
    ExternalCaptureBundle,
    ExternalOutcome,
    ExternalValidationCase,
    FunctionalMappingManifest,
    PinnedSourceIdentity,
    ProducerFunctionalEntity,
    ProducerFunctionalRecord,
    TTSimCaptureInvocation,
    TTSimCaptureKitManifest,
    TTSimRecipe,
)
from ..configs.schemas.validation import (
    AddressedEffect,
    ArtifactReference,
    CanonicalJSON,
    CausalEdge,
    IdentifierMapping,
    Metadata,
    NormalizedObservations,
    ObservationCounter,
    ObservationEntity,
    ObservationEvent,
    ReferenceProvenance,
    ValidationReference,
)
from .data import Data, array, integer, obj, require, text
from .external import (
    AdmittedExternalCampaign,
    AdmittedExternalCapture,
    admit_external_campaign,
    admit_external_capture,
)
from .identity import bytes_digest, canonical_record, content_digest

ASSET_ROOT = Path(__file__).resolve().parent / "capture_assets/ttsim_tt_metal_v1"
ASSET_ROLES = {
    "BUILD.md": "build_file",
    "CMakeLists.txt": "build_file",
    "host/wormhole_external_validation.cpp": "host_source",
    "host/sha256.hpp": "host_source",
    "kernels/noc_ack_roundtrip.cpp": "device_source",
    "kernels/dram_read_return.cpp": "device_source",
    "kernels/compute_service.cpp": "device_source",
    "kernels/compute_reader.cpp": "device_source",
    "kernels/compute_writer.cpp": "device_source",
    "patches/tt-metal-ttsim-single-rank.patch": "build_file",
    "runtime/ttsim/soc_descriptor.yaml": "runtime_config",
}
RECIPE_BY_FAMILY: dict[str, TTSimRecipe] = {
    "noc_ack_roundtrip": "noc_ack_roundtrip_v1",
    "dram_read_return": "dram_read_return_v1",
    "compute_service": "compute_service_v1",
}


@dataclass(frozen=True)
class FunctionalReferenceConversion:
    reference: ValidationReference
    mappings: FunctionalMappingManifest
    reference_path: Path
    observations_path: Path
    mappings_path: Path


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def _case_data(case: ExternalValidationCase, path: Path) -> Data:
    value = obj(json.loads(path.read_bytes()))
    require(
        value.get("case_family") == case.family,
        "case input family disagrees with the campaign",
    )
    workload, mapping = _effective_conditions(case.family, value)
    require(
        case.conditions.workload.value is not None,
        "capture case requires known workload conditions",
    )
    require(
        case.conditions.mapping.value is not None,
        "capture case requires known mapping conditions",
    )
    require(
        case.conditions.enabled_layout.value is not None,
        "capture case requires a known enabled layout",
    )
    require(
        case.conditions.instrumentation.value is not None,
        "capture case requires known instrumentation",
    )
    require(
        canonical_record(workload) == case.conditions.workload.value,
        "case input workload disagrees with campaign conditions",
    )
    require(
        canonical_record(mapping) == case.conditions.mapping.value,
        "case input mapping disagrees with campaign conditions",
    )
    return value


def _required(value: Data, names: tuple[str, ...]) -> None:
    require(
        set(value) == set(names),
        "case input must record every supported operation parameter exactly",
    )


def _coordinate(value: object, label: str) -> list[int]:
    values = array(value)
    require(len(values) == 2, f"{label} requires two logical coordinates")
    result = [integer(item) for item in values]
    require(
        all(item >= 0 for item in result), f"{label} coordinates must be nonnegative"
    )
    return result


def _positive(value: object, label: str) -> int:
    result = integer(value)
    require(result > 0, f"{label} must be positive")
    return result


def _effective_conditions(family: str, value: Data) -> tuple[Data, Data]:
    if family == "noc_ack_roundtrip":
        _required(
            value,
            (
                "bytes",
                "case_family",
                "completion",
                "count",
                "destination",
                "destination_address",
                "destination_core",
                "fabric_id",
                "pattern_seed",
                "pattern_stride",
                "source",
                "source_core",
            ),
        )
        byte_count = _positive(value["bytes"], "NoC byte count")
        count = _positive(value["count"], "NoC effect count")
        address = integer(value["destination_address"])
        fabric = integer(value["fabric_id"])
        seed, stride = (
            integer(value["pattern_seed"]),
            _positive(value["pattern_stride"], "NoC pattern stride"),
        )
        require(
            address >= 0 and fabric >= 0 and 0 <= seed <= 255,
            "NoC numeric parameters are out of range",
        )
        require(
            value["completion"] == "returned_acknowledgement",
            "NoC completion marker is unsupported",
        )
        workload: Data = {
            "bytes": byte_count,
            "completion": "returned_acknowledgement",
            "count": count,
            "destination_address": address,
            "operation": "remote_l1_write",
            "pattern_seed": seed,
            "pattern_stride": stride,
        }
        mapping: Data = {
            "destination": text(value["destination"]),
            "destination_core": cast(
                JsonValue, _coordinate(value["destination_core"], "NoC destination")
            ),
            "fabric_id": fabric,
            "source": text(value["source"]),
            "source_core": cast(
                JsonValue, _coordinate(value["source_core"], "NoC source")
            ),
        }
        return workload, mapping
    if family == "dram_read_return":
        _required(
            value,
            (
                "address",
                "bytes",
                "case_family",
                "completion",
                "count",
                "destination",
                "destination_core",
                "dram_bank",
                "pattern_seed",
                "pattern_stride",
                "resource_id",
            ),
        )
        address = integer(value["address"])
        byte_count = _positive(value["bytes"], "DRAM byte count")
        count = _positive(value["count"], "DRAM effect count")
        bank = integer(value["dram_bank"])
        seed, stride = (
            integer(value["pattern_seed"]),
            _positive(value["pattern_stride"], "DRAM pattern stride"),
        )
        require(
            address >= 0 and bank >= 0 and 0 <= seed <= 255,
            "DRAM numeric parameters are out of range",
        )
        require(
            value["completion"] == "worker_visible",
            "DRAM completion marker is unsupported",
        )
        workload = {
            "address": address,
            "bytes": byte_count,
            "completion": "worker_visible",
            "count": count,
            "operation": "dram_read",
            "pattern_seed": seed,
            "pattern_stride": stride,
        }
        mapping = {
            "destination": text(value["destination"]),
            "destination_core": cast(
                JsonValue, _coordinate(value["destination_core"], "DRAM destination")
            ),
            "dram_bank": bank,
            "resource_id": text(value["resource_id"]),
        }
        return workload, mapping
    if family == "compute_service":
        _required(
            value,
            (
                "case_family",
                "completion",
                "data_type",
                "fidelity",
                "input_layout",
                "output_address",
                "output_bytes",
                "output_layout",
                "sentinel_elements",
                "sentinel_seed",
                "shape",
                "work",
                "worker_core",
            ),
        )
        shape = [integer(item) for item in array(value["shape"])]
        require(
            len(shape) == 3 and all(item > 0 for item in shape),
            "compute shape must contain three positive axes",
        )
        require(
            shape == [32, 32, 32],
            "initial compute recipe requires one 32x32x32 tile",
        )
        work = _positive(value["work"], "compute work")
        require(
            work == 2 * shape[0] * shape[1] * shape[2],
            "compute work must equal the declared matmul work",
        )
        require(
            value["completion"] == "resource_release",
            "compute completion marker is unsupported",
        )
        require(
            value["data_type"] == "bf16" and value["fidelity"] == "hifi2",
            "compute type/fidelity is outside the initial recipe",
        )
        require(
            value["input_layout"] == "tile" and value["output_layout"] == "tile",
            "compute layout is outside the initial recipe",
        )
        output_address = integer(value["output_address"])
        output_bytes = _positive(value["output_bytes"], "compute output bytes")
        sentinel_elements = _positive(
            value["sentinel_elements"], "compute sentinel elements"
        )
        sentinel_seed = integer(value["sentinel_seed"])
        require(
            output_address >= 0 and sentinel_seed >= 0,
            "compute address/seed must be nonnegative",
        )
        require(
            output_bytes == shape[0] * shape[1] * 2,
            "compute output bytes disagree with BF16 shape",
        )
        require(
            sentinel_elements <= shape[0] * shape[1],
            "compute sentinel exceeds the output",
        )
        workload = {
            "data_type": "bf16",
            "fidelity": "hifi2",
            "input_layout": "tile",
            "operation": "matmul",
            "output_address": output_address,
            "output_bytes": output_bytes,
            "output_layout": "tile",
            "sentinel_elements": sentinel_elements,
            "sentinel_seed": sentinel_seed,
            "shape": cast(JsonValue, shape),
            "work": work,
        }
        mapping = {
            "output_resource": "worker-0-l1",
            "resource": "worker-0-tensix",
            "worker": "worker-0",
            "worker_core": cast(
                JsonValue, _coordinate(value["worker_core"], "compute worker")
            ),
        }
        return workload, mapping
    raise ValueError(f"unsupported capture-kit case family: {family}")


def _environment() -> tuple[CaptureEnvironmentVariable, ...]:
    return (
        CaptureEnvironmentVariable(name="TT_METAL_HOME", value="vendor/tt-metal"),
        CaptureEnvironmentVariable(
            name="TT_METAL_SIMULATOR", value="runtime/ttsim/libttsim_wh.so"
        ),
        CaptureEnvironmentVariable(name="TT_METAL_SLOW_DISPATCH_MODE", value="1"),
        CaptureEnvironmentVariable(name="TT_METAL_DISABLE_SFPLOADMACRO", value="1"),
    )


def generate_ttsim_capture_kit(
    admitted: AdmittedExternalCampaign,
    output_directory: Path,
) -> TTSimCaptureKitManifest:
    """Materialize a deterministic kit without executing a producer or fetching dependencies."""
    document = admitted.document
    producers = [
        item for item in document.producers if item.adapter == "ttsim_tt_metal_v1"
    ]
    require(len(producers) == 1, "campaign requires exactly one named ttsim producer")
    producer = producers[0]
    build = next(item for item in document.builds if item.build_id == producer.build_id)
    input_by_path = {path.resolve(): path for path in admitted.input_paths}
    files: dict[str, tuple[bytes, str, str]] = {
        "campaign.json": (admitted.document_path.read_bytes(), "campaign", "campaign"),
    }
    invocations: list[TTSimCaptureInvocation] = []
    for case in document.cases:
        original_input = input_by_path[
            (
                admitted.document_path.parent / case.simulator_input.logical_path
            ).resolve()
        ]
        _case_data(case, original_input)
        kit_input = f"inputs/{case.case_id}.json"
        files[kit_input] = (
            original_input.read_bytes(),
            "case_input",
            f"input:{case.case_id}",
        )
        binding = next(
            item for item in case.producers if item.producer_id == producer.producer_id
        )
        outputs = tuple(
            item.model_copy(update={"logical_path": "outputs/" + item.logical_path})
            for item in binding.outputs
        )
        functional = next(item for item in outputs if item.role == "functional_record")
        capture_manifest = next(
            item for item in outputs if item.role == "capture_manifest"
        )
        recipe = RECIPE_BY_FAMILY[case.family]
        invocations.append(
            TTSimCaptureInvocation(
                invocation_id=f"ttsim:{case.case_id}",
                recipe=recipe,
                case_id=case.case_id,
                argv=(
                    "bin/wormhole_external_validation",
                    "--recipe",
                    recipe,
                    "--input",
                    kit_input,
                    "--functional-output",
                    functional.logical_path,
                    "--manifest-output",
                    capture_manifest.logical_path,
                    "--repetitions",
                    str(case.budget.repetitions),
                    "--warmup-repetitions",
                    str(case.budget.warmup_repetitions),
                    "--timeout-seconds",
                    format(case.budget.timeout_seconds, "g"),
                    "--max-output-bytes",
                    str(case.budget.max_output_bytes),
                ),
                environment=_environment(),
                repetition_ids=case.boundary_maps[0].samples.repetition_ids,
                warmup_repetition_ids=case.boundary_maps[
                    0
                ].samples.warmup_repetition_ids,
                timeout_seconds=case.budget.timeout_seconds,
                max_output_bytes=case.budget.max_output_bytes,
                outputs=outputs,
            )
        )
    for logical_path, role in ASSET_ROLES.items():
        data = (ASSET_ROOT / logical_path).read_bytes()
        kit_path = logical_path if role == "runtime_config" else f"producer/{logical_path}"
        files[kit_path] = (
            data,
            role,
            "producer:" + logical_path.replace("/", ":"),
        )
    file_manifest = tuple(
        CaptureKitFile(
            artifact_id=artifact_id,
            logical_path=logical_path,
            sha256=bytes_digest(data),
            size_bytes=len(data),
            role=cast(CaptureKitFileRole, role),
        )
        for logical_path, (data, role, artifact_id) in sorted(files.items())
    )
    campaign_bytes = files["campaign.json"][0]
    identity = {
        "campaign_sha256": bytes_digest(campaign_bytes),
        "files": [item.model_dump(mode="json") for item in file_manifest],
        "invocations": [item.model_dump(mode="json") for item in invocations],
        "ttsim_revision": TTSIM_PINNED_REVISION,
    }
    manifest = TTSimCaptureKitManifest(
        kind="ttsim_capture_kit",
        schema_version=1,
        kit_id="ttsim-kit:" + content_digest(identity),
        campaign=ExternalArtifactIdentity(
            artifact_id="campaign",
            logical_path="campaign.json",
            sha256=bytes_digest(campaign_bytes),
            size_bytes=len(campaign_bytes),
        ),
        campaign_id=document.campaign_id,
        producer=producer,
        build=build,
        binary_manifest=build.artifacts,
        ttsim_source=PinnedSourceIdentity(
            source_url=TTSIM_SOURCE_URL,
            revision=TTSIM_PINNED_REVISION,
            source_snapshot_sha256=TTSIM_SOURCE_SNAPSHOT_SHA256,
        ),
        files=file_manifest,
        invocations=tuple(invocations),
        max_invocations=document.max_invocations,
        max_total_output_bytes=document.max_total_output_bytes,
    )
    if output_directory.exists():
        raise ValueError("capture-kit output directory already exists")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".capture-kit-", dir=output_directory.parent
    ) as temporary:
        staged = Path(temporary) / "kit"
        staged.mkdir()
        for logical_path, (data, _role, _artifact_id) in files.items():
            destination = staged / logical_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        (staged / "capture-kit.json").write_bytes(
            (manifest.model_dump_json(indent=2) + "\n").encode()
        )
        output_directory.parent.mkdir(parents=True, exist_ok=True)
        staged.rename(output_directory)
    return manifest


def _kit_path(kit_root: Path, logical_path: str) -> Path:
    root = kit_root.resolve()
    path = (root / logical_path).resolve()
    require(path.is_relative_to(root), "capture-kit path escapes its root")
    return path


def _verified_kit_bytes(
    kit_root: Path,
    identity: CaptureKitFile | ExternalArtifactIdentity,
) -> bytes:
    path = _kit_path(kit_root, identity.logical_path)
    data = path.read_bytes()
    require(
        len(data) == identity.size_bytes and bytes_digest(data) == identity.sha256,
        f"capture-kit artifact identity mismatch: {identity.logical_path}",
    )
    return data


def _write_ttsim_bundle(
    admitted: AdmittedExternalCampaign,
    case_id: str,
    output_directory: Path,
    bundle: ExternalCaptureBundle,
    raw_data: dict[str, bytes],
) -> AdmittedExternalCapture:
    if output_directory.exists():
        raise ValueError("ttsim capture output directory already exists")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".ttsim-capture-", dir=output_directory.parent
    ) as temporary:
        staged = Path(temporary) / "capture"
        staged.mkdir()
        (staged / "campaign.json").write_bytes(admitted.document_path.read_bytes())
        for case, source in zip(
            admitted.document.cases, admitted.input_paths, strict=True
        ):
            destination = staged / case.simulator_input.logical_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
        for artifact in bundle.raw_artifacts:
            destination = staged / artifact.logical_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw_data[artifact.artifact_id])
        (staged / "bundle.json").write_bytes(
            (bundle.model_dump_json(indent=2) + "\n").encode()
        )
        staged.rename(output_directory)
    return admit_external_capture(output_directory / "bundle.json")


def _unavailable_ttsim_capture(
    admitted: AdmittedExternalCampaign,
    case_id: str,
    output_directory: Path,
    reason: str,
) -> AdmittedExternalCapture:
    case = next(
        (item for item in admitted.document.cases if item.case_id == case_id), None
    )
    if case is None:
        raise ValueError(f"unknown ttsim collection case: {case_id}")
    producer = next(
        item
        for item in admitted.document.producers
        if item.adapter == "ttsim_tt_metal_v1"
    )
    build = next(
        item for item in admitted.document.builds if item.build_id == producer.build_id
    )
    campaign_data = admitted.document_path.read_bytes()
    conditions = case.conditions.model_copy(
        update={
            "device": Metadata[str](state="unknown", reason=reason),
            "software": Metadata[str](state="unknown", reason=reason),
            "firmware": Metadata[str](
                state="unknown", reason="ttsim has no device firmware identity"
            ),
            "measurement": case.conditions.measurement.model_copy(
                update={
                    "state": "unknown",
                    "value": None,
                    "reason": "ttsim timing is excluded from silicon evidence",
                }
            ),
        }
    )
    bundle = ExternalCaptureBundle(
        kind="external_capture_bundle",
        schema_version=1,
        bundle_id="ttsim-unavailable:"
        + content_digest(
            {
                "campaign": admitted.document_sha256,
                "case_id": case_id,
                "reason": reason,
            }
        ),
        campaign=ExternalArtifactIdentity(
            artifact_id="campaign",
            logical_path="campaign.json",
            sha256=bytes_digest(campaign_data),
            size_bytes=len(campaign_data),
        ),
        case_id=case_id,
        producer_id=producer.producer_id,
        adapter=producer.adapter,
        build=build,
        intended_classification="functional_capture",
        environment=CaptureEnvironment(
            host=Metadata[CanonicalJSON](
                state="known",
                value=canonical_record(
                    {"machine": platform.machine(), "system": platform.system()}
                ),
            ),
            device=Metadata[str](state="unknown", reason=reason),
            software=Metadata[str](state="unknown", reason=reason),
            firmware=Metadata[str](
                state="unknown", reason="ttsim has no device firmware identity"
            ),
            clocks=Metadata[tuple[CanonicalJSON, ...]](
                state="unknown", reason="ttsim timing is not admitted"
            ),
            enabled_layout=case.conditions.enabled_layout,
        ),
        conditions=conditions,
        outcome=ExternalOutcome(
            stage="collection",
            required=True,
            outcome="blocked",
            reason=reason,
            executed=True,
            case_id=case_id,
            producer_id=producer.producer_id,
            artifact_ids=(),
        ),
        raw_artifacts=(),
        counters=(),
        lineage=(),
        diagnostics=(reason, "no producer evidence was admitted"),
    )
    return _write_ttsim_bundle(admitted, case_id, output_directory, bundle, {})


def _validate_ttsim_outputs(
    record_data: bytes,
    manifest_data: bytes,
    invocation: TTSimCaptureInvocation,
    case: ExternalValidationCase,
    producer_id: str,
    build_id: str,
) -> None:
    record = ProducerFunctionalRecord.model_validate_json(record_data)
    require(
        (
            record.case_id,
            record.case_family,
            record.producer_id,
            record.adapter,
            record.build_id,
        )
        == (
            case.case_id,
            case.family,
            producer_id,
            "ttsim_tt_metal_v1",
            build_id,
        ),
        "ttsim functional output identity disagrees with its invocation",
    )
    require(
        tuple(item.repetition_id for item in record.repetitions)
        == invocation.repetition_ids,
        "ttsim functional output repetition identities disagree with the campaign",
    )
    manifest = obj(json.loads(manifest_data))
    _required(
        manifest,
        (
            "case_id",
            "completion_marker",
            "functional_sha256",
            "input_sha256",
            "kind",
            "max_output_bytes",
            "recipe",
            "repetitions",
            "schema_version",
            "status",
            "timeout_seconds",
            "warmup_repetitions",
        ),
    )
    require(
        (
            manifest["kind"],
            manifest["schema_version"],
            manifest["status"],
            manifest["completion_marker"],
            manifest["case_id"],
            manifest["recipe"],
            manifest["functional_sha256"],
            manifest["input_sha256"],
            manifest["repetitions"],
            manifest["warmup_repetitions"],
            manifest["timeout_seconds"],
            manifest["max_output_bytes"],
        )
        == (
            "tt_metal_capture_manifest",
            1,
            "pass",
            "WORMHOLE_EXTERNAL_COMPLETE_V1",
            case.case_id,
            invocation.recipe,
            bytes_digest(record_data),
            case.simulator_input.sha256,
            len(invocation.repetition_ids),
            len(invocation.warmup_repetition_ids),
            int(invocation.timeout_seconds),
            invocation.max_output_bytes,
        ),
        "ttsim capture manifest disagrees with the admitted invocation",
    )


def collect_ttsim_capture(
    admitted: AdmittedExternalCampaign,
    case_id: str,
    kit_root: Path,
    output_directory: Path,
) -> AdmittedExternalCapture:
    """Execute one verified fixed ttsim invocation and package its raw outputs."""
    if output_directory.exists():
        raise ValueError("ttsim capture output directory already exists")
    case = next(
        (item for item in admitted.document.cases if item.case_id == case_id), None
    )
    if case is None:
        raise ValueError(f"unknown ttsim collection case: {case_id}")
    root = kit_root.resolve()
    try:
        manifest_path = _kit_path(root, "capture-kit.json")
        manifest = TTSimCaptureKitManifest.model_validate_json(
            manifest_path.read_bytes()
        )
        campaign_data = _verified_kit_bytes(root, manifest.campaign)
        require(
            bytes_digest(campaign_data) == admitted.document_sha256,
            "capture-kit campaign disagrees with the admitted campaign",
        )
        expected_kit_id = "ttsim-kit:" + content_digest(
            {
                "campaign_sha256": manifest.campaign.sha256,
                "files": [item.model_dump(mode="json") for item in manifest.files],
                "invocations": [
                    item.model_dump(mode="json") for item in manifest.invocations
                ],
                "ttsim_revision": TTSIM_PINNED_REVISION,
            }
        )
        require(manifest.kit_id == expected_kit_id, "capture-kit identity is invalid")
        require(
            manifest.campaign_id == admitted.document.campaign_id,
            "capture-kit campaign identity is invalid",
        )
        for item in manifest.files:
            _verified_kit_bytes(root, item)
        producer = next(
            item
            for item in admitted.document.producers
            if item.adapter == "ttsim_tt_metal_v1"
        )
        build = next(
            item
            for item in admitted.document.builds
            if item.build_id == producer.build_id
        )
        require(
            manifest.producer == producer and manifest.build == build,
            "capture-kit producer/build identity disagrees with the campaign",
        )
        invocation = next(
            (item for item in manifest.invocations if item.case_id == case_id), None
        )
        require(invocation is not None, "capture kit omits the requested case")
        assert invocation is not None
        for artifact in manifest.binary_manifest:
            path = _kit_path(root, artifact.logical_path)
            data = path.read_bytes()
            require(
                len(data) == artifact.size_bytes
                and bytes_digest(data) == artifact.sha256,
                f"capture-kit binary identity mismatch: {artifact.logical_path}",
            )
        output_paths = {
            item.artifact_id: _kit_path(root, item.logical_path)
            for item in invocation.outputs
        }
        require(
            not any(path.exists() for path in output_paths.values()),
            "capture-kit output already exists; refusing to replace evidence",
        )
        executable = _kit_path(root, invocation.argv[0])
        require(executable.is_file(), "capture-kit host executable is unavailable")
    except (OSError, ValueError) as exc:
        return _unavailable_ttsim_capture(
            admitted,
            case_id,
            output_directory,
            f"ttsim worker prerequisites unavailable ({type(exc).__name__}: {exc})",
        )

    environment = os.environ.copy()
    environment.update({item.name: item.value for item in invocation.environment})
    command = [str(executable), *invocation.argv[1:]]
    try:
        result = subprocess.run(
            command,
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=invocation.timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _unavailable_ttsim_capture(
            admitted,
            case_id,
            output_directory,
            f"ttsim producer did not complete ({type(exc).__name__})",
        )
    if result.returncode != 0:
        return _unavailable_ttsim_capture(
            admitted,
            case_id,
            output_directory,
            f"ttsim producer exited with status {result.returncode}",
        )

    try:
        output_data = {
            artifact_id: path.read_bytes()
            for artifact_id, path in output_paths.items()
        }
        require(
            sum(len(data) for data in output_data.values())
            <= invocation.max_output_bytes * len(invocation.repetition_ids),
            "ttsim producer exceeded its output-byte budget",
        )
        declaration_by_role = {item.role: item for item in invocation.outputs}
        functional = declaration_by_role["functional_record"]
        capture_manifest = declaration_by_role["capture_manifest"]
        _validate_ttsim_outputs(
            output_data[functional.artifact_id],
            output_data[capture_manifest.artifact_id],
            invocation,
            case,
            producer.producer_id,
            build.build_id,
        )
    except (OSError, ValueError) as exc:
        return _unavailable_ttsim_capture(
            admitted,
            case_id,
            output_directory,
            f"ttsim producer outputs are inadmissible ({type(exc).__name__}: {exc})",
        )

    declaration_by_id = {item.artifact_id: item for item in invocation.outputs}
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
    campaign_bytes = admitted.document_path.read_bytes()
    conditions = case.conditions.model_copy(
        update={
            "device": Metadata[str](state="known", value="ttsim:wormhole_b0"),
            "software": Metadata[str](
                state="known",
                value=f"tt-metal:{build.revision};ttsim:{TTSIM_PINNED_REVISION}",
            ),
            "firmware": Metadata[str](
                state="unknown", reason="ttsim has no device firmware identity"
            ),
            "measurement": case.conditions.measurement.model_copy(
                update={
                    "state": "unknown",
                    "value": None,
                    "reason": "ttsim timing is excluded from silicon evidence",
                }
            ),
        }
    )
    bundle = ExternalCaptureBundle(
        kind="external_capture_bundle",
        schema_version=1,
        bundle_id="ttsim-capture:"
        + content_digest(
            {
                "kit_id": manifest.kit_id,
                "case_id": case_id,
                "artifacts": [item.model_dump(mode="json") for item in artifacts],
            }
        ),
        campaign=ExternalArtifactIdentity(
            artifact_id="campaign",
            logical_path="campaign.json",
            sha256=bytes_digest(campaign_bytes),
            size_bytes=len(campaign_bytes),
        ),
        case_id=case_id,
        producer_id=producer.producer_id,
        adapter=producer.adapter,
        build=build,
        intended_classification="functional_capture",
        environment=CaptureEnvironment(
            host=Metadata[CanonicalJSON](
                state="known",
                value=canonical_record(
                    {"machine": platform.machine(), "system": platform.system()}
                ),
            ),
            device=Metadata[str](state="known", value="ttsim:wormhole_b0"),
            software=Metadata[str](
                state="known",
                value=f"tt-metal:{build.revision};ttsim:{TTSIM_PINNED_REVISION}",
            ),
            firmware=Metadata[str](
                state="unknown", reason="ttsim has no device firmware identity"
            ),
            clocks=Metadata[tuple[CanonicalJSON, ...]](
                state="unknown", reason="ttsim timing is not admitted"
            ),
            enabled_layout=case.conditions.enabled_layout,
        ),
        conditions=conditions,
        outcome=ExternalOutcome(
            stage="collection",
            required=True,
            outcome="pass",
            reason="fixed pinned ttsim producer completed and outputs were admitted",
            executed=True,
            case_id=case_id,
            producer_id=producer.producer_id,
            artifact_ids=tuple(item.artifact_id for item in artifacts),
        ),
        raw_artifacts=artifacts,
        counters=(
            CaptureCounter(
                name="repetitions",
                value=len(invocation.repetition_ids),
                unit="count",
            ),
            CaptureCounter(
                name="warmup_repetitions",
                value=len(invocation.warmup_repetition_ids),
                unit="count",
            ),
        ),
        lineage=tuple(
            ArtifactLineage(
                artifact_id=item.artifact_id,
                derived_from=("campaign",),
                transform="ttsim_tt_metal_v1",
            )
            for item in artifacts
        ),
        diagnostics=(
            f"ttsim revision {TTSIM_PINNED_REVISION}",
            "functional evidence only; simulator timing is not silicon evidence",
        ),
    )
    return _write_ttsim_bundle(
        admitted, case_id, output_directory, bundle, output_data
    )


def _expected_sentinel(family: str, workload: Data) -> bytes:
    if family in ("noc_ack_roundtrip", "dram_read_return"):
        size = integer(workload["bytes"])
        seed = integer(workload["pattern_seed"])
        stride = integer(workload["pattern_stride"])
        return bytes((seed + stride * index) & 0xFF for index in range(size))
    elements = integer(workload["sentinel_elements"])
    seed = integer(workload["sentinel_seed"])
    result = bytearray()
    for index in range(elements):
        bits = struct.unpack(">I", struct.pack(">f", float(seed + index)))[0] >> 16
        result.extend(bits.to_bytes(2, "little"))
    return bytes(result)


def _expected_entities(
    case: ExternalValidationCase, workload: Data, mapping: Data
) -> tuple[ProducerFunctionalEntity, ...]:
    boundary = case.boundary_maps[0].simulator_interval
    if case.family == "noc_ack_roundtrip":
        source, destination = text(mapping["source"]), text(mapping["destination"])
        fabric = integer(mapping["fabric_id"])
        return (
            ProducerFunctionalEntity(
                entity_id="operation", role="transfer", simulator_id=boundary.subject_id
            ),
            ProducerFunctionalEntity(
                entity_id="source",
                role="endpoint",
                simulator_id=source,
                fabric_id=fabric,
            ),
            ProducerFunctionalEntity(
                entity_id="destination",
                role="endpoint",
                simulator_id=destination,
                fabric_id=fabric,
            ),
            ProducerFunctionalEntity(
                entity_id="destination-l1",
                role="resource",
                simulator_id=f"resource:{destination}:l1",
                physical_owner="destination",
                fabric_id=fabric,
            ),
        )
    if case.family == "dram_read_return":
        destination, resource = (
            text(mapping["destination"]),
            text(mapping["resource_id"]),
        )
        return (
            ProducerFunctionalEntity(
                entity_id="operation", role="transfer", simulator_id=boundary.subject_id
            ),
            ProducerFunctionalEntity(
                entity_id="destination", role="worker", simulator_id=destination
            ),
            ProducerFunctionalEntity(
                entity_id="dram",
                role="resource",
                simulator_id=boundary.resource_id or resource,
            ),
        )
    worker, resource, output_resource = (
        text(mapping["worker"]),
        text(mapping["resource"]),
        text(mapping["output_resource"]),
    )
    return (
        ProducerFunctionalEntity(
            entity_id="operation", role="job", simulator_id=boundary.subject_id
        ),
        ProducerFunctionalEntity(
            entity_id="worker", role="worker", simulator_id=worker
        ),
        ProducerFunctionalEntity(
            entity_id="compute-resource",
            role="resource",
            simulator_id=boundary.resource_id or resource,
            physical_owner="worker",
        ),
        ProducerFunctionalEntity(
            entity_id="output-l1",
            role="resource",
            simulator_id=output_resource,
            physical_owner="worker",
        ),
    )


def _expected_actions(case: ExternalValidationCase) -> tuple[str, ...]:
    if case.family == "noc_ack_roundtrip":
        return "submission", "remote_write_visible", "acknowledged_completion"
    if case.family == "dram_read_return":
        return (
            "submission",
            "memory_service_begin",
            "memory_service_end",
            "worker_visible_completion",
        )
    return "compute_resource_acquire", "result_visible", "compute_resource_release"


def _verify_repetition(
    case: ExternalValidationCase,
    repetition_id: str,
    record: ProducerFunctionalRecord,
    workload: Data,
    mapping: Data,
) -> None:
    repetition = next(
        item for item in record.repetitions if item.repetition_id == repetition_id
    )
    require(repetition.status == "pass", "producer functional repetition did not pass")
    payload = bytes.fromhex(repetition.sentinel_payload_hex)
    require(
        bytes_digest(payload) == repetition.sentinel_sha256,
        "producer sentinel digest is invalid",
    )
    require(
        payload == _expected_sentinel(case.family, workload),
        "producer sentinel payload disagrees with declared input",
    )
    actions = tuple(item.action for item in repetition.events)
    require(
        actions == _expected_actions(case),
        "producer events do not implement the required finite causal sequence",
    )
    prefix = f"repetition:{repetition_id}:"
    boundary = case.boundary_maps[0].simulator_interval
    boundary_indexes = {
        "noc_ack_roundtrip": {0: boundary.start_event_id, 2: boundary.end_event_id},
        "dram_read_return": {1: boundary.start_event_id, 2: boundary.end_event_id},
        "compute_service": {0: boundary.start_event_id, 2: boundary.end_event_id},
    }[case.family]
    if case.family == "noc_ack_roundtrip":
        counter_values = (
            (("bytes", integer(workload["bytes"]), "bytes", "planned"),),
            (("bytes", integer(workload["bytes"]), "bytes", "observed"),),
            (("acknowledgements", 1, "count", "observed"),),
        )
    elif case.family == "dram_read_return":
        counter_values = (
            (("bytes", integer(workload["bytes"]), "bytes", "planned"),),
            (),
            (),
            (("bytes", integer(workload["bytes"]), "bytes", "observed"),),
        )
    else:
        counter_values = (
            (("work", integer(workload["work"]), "work", "planned"),),
            (("bytes", integer(workload["output_bytes"]), "bytes", "observed"),),
            (("work", integer(workload["work"]), "work", "observed"),),
        )
    for index, event in enumerate(repetition.events):
        expected_simulator = boundary_indexes.get(
            index, f"external:{case.case_id}:{event.action}"
        )
        require(
            (
                event.event_id,
                event.subject_id,
                event.sequence,
                event.simulator_event_id,
                tuple(
                    (counter.name, counter.value, counter.unit, counter.scope)
                    for counter in event.counters
                ),
            )
            == (
                prefix + event.action,
                "operation",
                index,
                prefix + expected_simulator,
                counter_values[index],
            ),
            "producer event identity/counters/mapping disagree with the campaign",
        )
    require(
        len(repetition.effects) == 1,
        "producer repetition requires exactly one functional effect",
    )
    effect = repetition.effects[0]
    require(
        (effect.effect_id, effect.simulator_effect_id)
        == (prefix + "effect", prefix + f"effect:{case.case_id}"),
        "producer effect identity/mapping disagrees with the campaign",
    )
    require(
        effect.count == integer(workload.get("count", 1)),
        "producer effect count disagrees with declared input",
    )
    if case.family == "noc_ack_roundtrip":
        expected = (
            "destination",
            "destination-l1",
            integer(workload["destination_address"]),
            integer(workload["bytes"]),
        )
    elif case.family == "dram_read_return":
        expected = (
            "destination",
            "dram",
            integer(workload["address"]),
            integer(workload["bytes"]),
        )
    else:
        expected = (
            "worker",
            "output-l1",
            integer(workload["output_address"]),
            integer(workload["output_bytes"]),
        )
    require(
        (
            effect.destination_id,
            effect.resource_id,
            effect.offset_bytes,
            effect.size_bytes,
        )
        == expected,
        "producer effect address/size disagrees with declared input",
    )
    require(
        effect.visibility_event
        == repetition.events[-1 if case.family == "dram_read_return" else 1].event_id,
        "producer effect visibility marker is incorrect",
    )


def _reject_simulator_paths(value: JsonValue) -> None:
    if isinstance(value, dict):
        for name, child in value.items():
            if str(name).casefold() in {
                "simulator_path",
                "ttsim_path",
                "repository_path",
                "checkout_path",
            }:
                raise ValueError(
                    "functional record cannot declare simulator-specific paths"
                )
            _reject_simulator_paths(child)
    elif isinstance(value, list):
        for child in value:
            _reject_simulator_paths(child)
    elif isinstance(value, str) and (value.startswith("/") or "\\" in value):
        raise ValueError(
            "functional record cannot declare absolute or platform-specific paths"
        )


def convert_functional_capture(
    admitted: AdmittedExternalCapture,
    output_directory: Path,
) -> FunctionalReferenceConversion:
    """Convert one admitted producer record into ordinary v1 reference artifacts."""
    capture = admitted.document
    require(
        capture.outcome.outcome == "pass" and capture.outcome.executed,
        "only a successful executed capture can become a functional reference",
    )
    campaign = admit_external_campaign(admitted.artifact_paths[0])
    case = next(
        item for item in campaign.document.cases if item.case_id == capture.case_id
    )
    for field in (
        "architecture",
        "profile_version",
        "enabled_layout",
        "workload",
        "mapping",
        "instrumentation",
    ):
        require(
            getattr(capture.conditions, field) == getattr(case.conditions, field),
            f"capture {field} disagrees with the campaign",
        )
    producer = next(
        item
        for item in campaign.document.producers
        if item.producer_id == capture.producer_id
    )
    binding = next(
        item for item in case.producers if item.producer_id == capture.producer_id
    )
    functional_output = next(
        item for item in binding.outputs if item.role == "functional_record"
    )
    raw_identity = next(
        (
            item
            for item in capture.raw_artifacts
            if item.artifact_id == functional_output.artifact_id
        ),
        None,
    )
    if raw_identity is None:
        raise ValueError("capture omits its declared functional record")
    artifact_paths = dict(
        zip(
            (item.artifact_id for item in capture.raw_artifacts),
            admitted.artifact_paths[1:],
            strict=True,
        )
    )
    raw_path = artifact_paths[functional_output.artifact_id]
    raw_bytes = raw_path.read_bytes()
    require(
        bytes_digest(raw_bytes) == raw_identity.sha256,
        "functional record changed after capture admission",
    )
    raw_json = cast(JsonValue, json.loads(raw_bytes))
    _reject_simulator_paths(raw_json)
    record = ProducerFunctionalRecord.model_validate_json(raw_bytes)
    input_path = next(
        path
        for item, path in zip(
            campaign.document.cases, campaign.input_paths, strict=True
        )
        if item.case_id == case.case_id
    )
    case_data = _case_data(case, input_path)
    workload, mapping = _effective_conditions(case.family, case_data)
    require(
        (
            record.case_id,
            record.case_family,
            record.producer_id,
            record.adapter,
            record.build_id,
        )
        == (
            case.case_id,
            case.family,
            producer.producer_id,
            producer.adapter,
            producer.build_id,
        ),
        "producer record identity disagrees with the admitted capture",
    )
    require(
        record.input_artifact == case.simulator_input,
        "producer record input identity disagrees with the campaign",
    )
    require(
        record.binary_artifacts == capture.build.artifacts,
        "producer record binary manifest disagrees with the admitted build",
    )
    require(
        record.workload == canonical_record(workload)
        and record.mapping == canonical_record(mapping),
        "producer record effective operation/mapping disagrees with the campaign",
    )
    require(
        record.enabled_layout == case.conditions.enabled_layout.value,
        "producer record enabled layout disagrees with the campaign",
    )
    require(
        record.instrumentation == case.conditions.instrumentation.value,
        "producer record instrumentation disagrees with the campaign",
    )
    expected_repetitions = case.boundary_maps[0].samples.repetition_ids
    require(
        all(
            item.samples.repetition_ids == expected_repetitions
            for item in case.boundary_maps
        ),
        "case boundary maps disagree on functional repetitions",
    )
    require(
        tuple(item.repetition_id for item in record.repetitions)
        == expected_repetitions,
        "producer record repetitions disagree with the campaign",
    )
    expected_entities = _expected_entities(case, workload, mapping)
    require(
        record.entities == expected_entities,
        "producer entity identities/mappings disagree with the campaign",
    )
    for repetition_id in expected_repetitions:
        _verify_repetition(case, repetition_id, record, workload, mapping)

    events = tuple(
        ObservationEvent(
            event_id=event.event_id,
            action=event.action,
            subject_id=event.subject_id,
            time=None,
            counters=tuple(
                ObservationCounter.model_validate(item.model_dump())
                for item in event.counters
            ),
        )
        for repetition in record.repetitions
        for event in repetition.events
    )
    effects = tuple(
        AddressedEffect(
            effect_id=effect.effect_id,
            destination_id=effect.destination_id,
            resource_id=effect.resource_id,
            offset_bytes=effect.offset_bytes,
            size_bytes=effect.size_bytes,
            count=effect.count,
            visibility_event=effect.visibility_event,
        )
        for repetition in record.repetitions
        for effect in repetition.effects
    )
    observation = NormalizedObservations(
        observation_id="reference:" + raw_identity.sha256,
        source_result_sha256=raw_identity.sha256,
        execution="complete",
        clocks=capture.conditions.clocks.value or (),
        entities=tuple(
            ObservationEntity(
                entity_id=item.entity_id,
                role=item.role,
                physical_owner=item.physical_owner,
                fabric_id=item.fabric_id,
            )
            for item in record.entities
        ),
        events=events,
        effects=effects,
        causal_edges=tuple(
            CausalEdge(before=before.event_id, after=after.event_id)
            for repetition in record.repetitions
            for before, after in zip(repetition.events, repetition.events[1:])
        ),
        routes=(),
        intervals=(),
        metrics=(),
        pending=(),
        missing=(),
    )
    raw_observations = observation.model_dump(
        mode="json", exclude={"observation_id", "source_result_sha256"}
    )
    observations_bytes = _canonical_bytes(raw_observations)
    observations_sha256 = bytes_digest(observations_bytes)
    reference_id = f"external:{case.case_id}:{raw_identity.sha256}"
    reference = ValidationReference(
        kind="validation_reference",
        schema_version=1,
        reference_id=reference_id,
        format="normalized_functional_v1",
        provenance=ReferenceProvenance(
            classification=capture.intended_classification,
            producer=f"{capture.adapter}:{capture.producer_id}",
            source_url=Metadata[str](state="known", value=capture.build.source_url),
            revision=Metadata[str](state="known", value=capture.build.revision),
            snapshot_sha256=Metadata[str](
                state="known", value=capture.build.source_snapshot_sha256
            ),
            raw_artifact=ArtifactReference(
                path=f"{case.case_id}.normalized.json", sha256=observations_sha256
            ),
            extractor="wormhole_reference_import",
            extractor_version="1",
            original_units=("bytes", "count", "work"),
            normalized_units=("bytes", "count", "work"),
        ),
        conditions=capture.conditions,
        observations=None,
    )
    mappings = FunctionalMappingManifest(
        kind="functional_identifier_mappings",
        schema_version=1,
        reference_id=reference_id,
        source_artifact_sha256=raw_identity.sha256,
        entity_mappings=tuple(
            IdentifierMapping(reference=item.entity_id, simulator=item.simulator_id)
            for item in record.entities
        ),
        event_mappings=tuple(
            IdentifierMapping(
                reference=item.event_id, simulator=item.simulator_event_id
            )
            for repetition in record.repetitions
            for item in repetition.events
        ),
        effect_mappings=tuple(
            IdentifierMapping(
                reference=item.effect_id, simulator=item.simulator_effect_id
            )
            for repetition in record.repetitions
            for item in repetition.effects
        ),
    )
    if output_directory.exists():
        raise ValueError("functional conversion output directory already exists")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".functional-reference-", dir=output_directory.parent
    ) as temporary:
        staged = Path(temporary) / "reference"
        staged.mkdir()
        observations_path = staged / f"{case.case_id}.normalized.json"
        reference_path = staged / f"{case.case_id}.reference.json"
        mappings_path = staged / f"{case.case_id}.mappings.json"
        observations_path.write_bytes(observations_bytes)
        reference_path.write_bytes(
            (reference.model_dump_json(indent=2) + "\n").encode()
        )
        mappings_path.write_bytes((mappings.model_dump_json(indent=2) + "\n").encode())
        staged.rename(output_directory)
    return FunctionalReferenceConversion(
        reference=reference,
        mappings=mappings,
        reference_path=output_directory / f"{case.case_id}.reference.json",
        observations_path=output_directory / f"{case.case_id}.normalized.json",
        mappings_path=output_directory / f"{case.case_id}.mappings.json",
    )

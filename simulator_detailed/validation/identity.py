"""Portable content manifests with explicit diagnostic paths and bounded Git probes.

Callers supply the dependency roots and already admitted effective plan. Collection
does not infer hardware parameters, execute cases, import optional ML packages or
fetch reference data. New Python files under the declared roots are included even
when Git does not track them.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import TypeAdapter

from ..configs.schemas.topology import GraphRecord
from ..configs.schemas.validation import (
    ArtifactReference,
    CanonicalJSON,
    DependencyVersion,
    DiagnosticPath,
    EnvironmentIdentity,
    FileIdentity,
    Metadata,
    RunDiagnostics,
    RunIdentity,
    SeedState,
    SourceIdentity,
    ValidationDocument,
)

DEFAULT_DEPENDENCIES = (
    "annotated-types", "nodeenv", "numpy", "pydantic", "pydantic-core", "pyright",
    "ruff", "scipy", "simpy", "typing-extensions", "typing-inspection",
)


def bytes_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_record(value: object) -> CanonicalJSON:
    if isinstance(value, GraphRecord):
        value = value.model_dump(mode="json")
    return CanonicalJSON(text=json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False))


def content_digest(value: object) -> str:
    return bytes_digest(canonical_record(value).text.encode("utf-8"))


def resolve_asset(declaring_document: Path, asset_path: str) -> Path:
    """Nested documents resolve their own relative assets, independent of cwd."""
    path = Path(asset_path)
    return (declaring_document.resolve().parent / path).resolve()


def read_verified_artifact(declaring_document: Path, artifact: ArtifactReference) -> bytes:
    artifact = ArtifactReference.model_validate(artifact)
    data = resolve_asset(declaring_document, artifact.path).read_bytes()
    if bytes_digest(data) != artifact.sha256:
        raise ValueError(f"reference artifact hash mismatch: {artifact.path}")
    return data


def load_document(path: Path) -> ValidationDocument:
    """Parse only the five named contracts; full simulator admission is separate."""
    adapter: TypeAdapter[ValidationDocument] = TypeAdapter(ValidationDocument)
    return adapter.validate_json(path.read_bytes())


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True,
        text=True, timeout=10,
    ).stdout


def _file_identity(logical_path: str, path: Path, *, allow_deleted: bool = False) -> FileIdentity:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        if not allow_deleted:
            raise
        return FileIdentity(logical_path=logical_path, sha256=None, size_bytes=None)
    return FileIdentity(logical_path=logical_path, sha256=bytes_digest(data), size_bytes=len(data))


def collect_source_identity(
    checkout: Path,
    *,
    source_roots: Sequence[str] = ("simulator_detailed",),
    outputs: Sequence[Path] = (),
) -> SourceIdentity:
    root = checkout.resolve()
    excluded = {p.resolve() for p in outputs}
    if not source_roots or len(set(source_roots)) != len(source_roots):
        raise ValueError("source roots must be explicit, nonempty and unique")
    paths: set[str] = set()
    for name in source_roots:
        # Reuse portable path validation before walking or passing paths to Git.
        FileIdentity(logical_path=name, sha256=None, size_bytes=None)
        source = root / name
        if not source.exists():
            raise ValueError(f"source root is unavailable: {name}")
        files = (source,) if source.is_file() else source.rglob("*.py")
        for path in files:
            if path.suffix != ".py" or path.resolve() in excluded:
                continue
            if not path.resolve().is_relative_to(root):
                raise ValueError(f"source escapes checkout: {path}")
            paths.add(path.relative_to(root).as_posix())
    revision: Metadata[str]
    dirty: Metadata[bool]
    try:
        revision = Metadata[str](state="known", value=_git(root, "rev-parse", "HEAD").strip())
        tracked = {
            p for p in _git(root, "ls-files", "--cached", "-z", "--", *source_roots).split("\0")
            if p.endswith(".py") and (root / p).resolve() not in excluded
        }
        changed = set(_git(root, "diff", "--name-only", "-z", "HEAD", "--", *source_roots).split("\0"))
        dirty = Metadata[bool](state="known", value=bool((changed & (paths | tracked)) or (paths - tracked)))
        # Deleted tracked files remain explicit tombstones in the manifest.
        paths |= tracked
    except (OSError, subprocess.SubprocessError) as exc:
        reason = f"Git identity unavailable ({type(exc).__name__})"
        revision = Metadata[str](state="unknown", reason=reason)
        dirty = Metadata[bool](state="unknown", reason=reason)
    files = tuple(_file_identity(name, root / name, allow_deleted=True) for name in sorted(paths))
    return SourceIdentity(
        revision=revision, dirty=dirty, files=files,
        bundle_sha256=content_digest([file.model_dump(mode="json") for file in files]),
    )


def collect_environment(dependencies: Sequence[str] = DEFAULT_DEPENDENCIES) -> EnvironmentIdentity:
    normalized = tuple(name.lower().replace("_", "-").replace(".", "-") for name in dependencies)
    if len(set(normalized)) != len(normalized):
        raise ValueError("duplicate normalized dependency name")
    versions: list[DependencyVersion] = []
    for name in sorted(normalized):
        try:
            version = Metadata[str](state="known", value=importlib.metadata.version(name))
        except importlib.metadata.PackageNotFoundError:
            version = Metadata[str](state="unknown", reason="distribution is not installed")
        versions.append(DependencyVersion(name=name, version=version))
    return EnvironmentIdentity(
        python_version=platform.python_version(), python_implementation=platform.python_implementation(),
        system=platform.system(), release=platform.release(), machine=platform.machine(),
        dependencies=tuple(versions),
    )


def collect_run_identity(
    checkout: Path,
    *,
    inputs: Mapping[str, Path],
    effective_plan: object,
    selection: object,
    seed: SeedState,
    source_roots: Sequence[str] = ("simulator_detailed",),
    outputs: Sequence[Path] = (),
    dependencies: Sequence[str] = DEFAULT_DEPENDENCIES,
    recorded_at: str | None = None,
) -> tuple[RunIdentity, RunDiagnostics]:
    """Logical asset roles identify inputs; local filenames are diagnostics only.

    Effective plan/selection must be the caller's admitted semantic records, with
    diagnostic paths removed by that adapter (never guessed or stripped here).
    """
    excluded = {path.resolve() for path in outputs}
    if any(path.resolve() in excluded for path in inputs.values()):
        raise ValueError("an output cannot be an identity input")
    effective = canonical_record(effective_plan)
    selected = canonical_record(selection)
    identity = RunIdentity(
        source=collect_source_identity(checkout, source_roots=source_roots, outputs=outputs),
        inputs=tuple(_file_identity(name, path) for name, path in sorted(inputs.items())),
        effective_plan=effective, effective_plan_sha256=bytes_digest(effective.text.encode("utf-8")),
        selection=selected, selection_sha256=bytes_digest(selected.text.encode("utf-8")),
        environment=collect_environment(dependencies), seed=seed,
    )
    diagnostics = RunDiagnostics(
        recorded_at=recorded_at, checkout_path=str(checkout.resolve()),
        paths=tuple(DiagnosticPath(logical_path=name, local_path=str(path.resolve()))
                    for name, path in sorted(inputs.items())),
    )
    return identity, diagnostics

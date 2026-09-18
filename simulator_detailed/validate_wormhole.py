"""Finite offline validation, explicit reference import and bounded calibration."""

import argparse
import os
import sys
import tempfile
from pathlib import Path

from .configs.schemas.validation import (
    CalibrationPlan,
    ValidationReference,
    ValidationSuite,
)
from .validation.adapters import admit
from .validation.calibration import calibrate
from .validation.identity import load_document, resolve_asset
from .validation.references import import_reference
from .validation.runner import run_suite


def input_paths(path: Path, document: ValidationSuite | ValidationReference | CalibrationPlan) -> set[Path]:
    """Protect every declared input, including absent reference paths, from output."""
    paths = {path.resolve()}
    if isinstance(document, ValidationReference):
        paths.add(resolve_asset(path, document.provenance.raw_artifact.path))
        return paths
    cases = document.cases if isinstance(document, ValidationSuite) else (*document.fit_cases, *document.evaluation_cases)
    for case in cases:
        source = admit(case.adapter, resolve_asset(path, case.input_path), horizon=case.budget.max_aci_cycles)
        paths.update(source.inputs.values())
    bindings = document.references if isinstance(document, ValidationSuite) else tuple(c.reference for c in (*document.fit_cases, *document.evaluation_cases))
    for binding in bindings:
        reference_path = resolve_asset(path, binding.document.path)
        paths.add(reference_path)
        if reference_path.is_file():
            reference = ValidationReference.model_validate_json(reference_path.read_bytes())
            paths.add(resolve_asset(reference_path, reference.provenance.raw_artifact.path))
    return paths


def atomic_output(path: Path, payload: str) -> None:
    """Publish a complete serialized file; never create missing parent directories."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".validation-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate finite abstract Wormhole models with explicit evidence limits.")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--suite", type=Path)
    modes.add_argument("--import-reference", type=Path)
    modes.add_argument("--calibrate", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    path: Path = args.suite or args.import_reference or args.calibrate
    output: Path | None = args.output
    try:
        document = load_document(path)
        expected = ValidationSuite if args.suite else ValidationReference if args.import_reference else CalibrationPlan
        if not isinstance(document, expected):
            raise TypeError("document kind does not match the selected CLI mode")
        protected = input_paths(path, document)
        if output is not None:
            if not output.parent.is_dir():
                raise ValueError("output parent directory must already exist")
            if output.resolve() in protected or (output.exists() and any(p.exists() and output.samefile(p) for p in protected)):
                raise ValueError("output must not replace a declared input or reference artifact")
        if isinstance(document, ValidationReference):
            result = import_reference(path)
            code = 0
            diagnostic = "Reference imported; this is parsing/integrity evidence, not model validation."
        elif isinstance(document, ValidationSuite):
            result = run_suite(path)
            code = {"pass": 0, "fail": 1, "incomplete": 3}[result.status]
            diagnostic = f"Suite {result.status}; functional={result.functional_reference}; silicon={result.silicon_timing}."
        else:
            result = calibrate(path)
            code = {"pass": 0, "fail": 1, "incomplete": 3}[result.status]
            diagnostic = f"Calibration {result.status}; evidence scope={result.evidence_scope}."
        payload = result.model_dump_json(indent=2) + "\n"
        if output is not None:
            atomic_output(output, payload)
        sys.stdout.write(payload)
        print(diagnostic, file=sys.stderr)
        return code
    except (OSError, ValueError, TypeError, NotImplementedError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""JSON entry point for generic four-kind transaction batches.

Exit codes follow the compute replay convention: 0 complete, 1 incomplete,
2 invalid input. File-loaded batches require a `graph_path` resolved relative
to the batch document; library callers may pass documents directly instead.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from .configs.schemas.generic_transactions import (
    GenericSimulationResult,
    GenericTransactionBatch,
)
from .generic_graph import GenericSystem, load_generic_system
from .generic_runtime import run_generic_batch


def load_batch(path: str | Path) -> tuple[GenericSystem, GenericTransactionBatch]:
    """Load a batch document and its referenced system graph; nothing runs here."""
    path = Path(path)
    raw: object = json.loads(path.read_text())
    if not isinstance(raw, dict) or cast(dict[str, object], raw).get("kind") != (
        "generic_transaction_batch"
    ):
        raise ValueError("expected a generic_transaction_batch document")
    batch = GenericTransactionBatch.model_validate(raw)
    if batch.graph_path is None:
        raise ValueError("file-loaded batches require graph_path")
    system = load_generic_system(path.parent / batch.graph_path)
    return system, batch


def execute(path: str | Path) -> GenericSimulationResult:
    system, batch = load_batch(path)
    return run_generic_batch(system, batch)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Execute a generic transfer/compute/wait/signal batch."
    )
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = execute(args.batch)
    except (ValidationError, ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
        print(f"invalid: {exc}", file=sys.stderr)
        return 2
    payload = json.dumps(result.model_dump(mode="json"), indent=2, allow_nan=False)
    if args.output is not None:
        args.output.write_text(payload + "\n")
    else:
        print(payload)
    return 0 if result.status == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())

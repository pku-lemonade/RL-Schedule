"""Separate JSON-only inspection and synthetic transport entry point."""

import argparse
import json
import sys
from pathlib import Path
from typing import cast

from .configs.schemas.hardware_profile import HardwareProfileConfig
from .configs.schemas.topology import CanonicalTopology
from .hardware_profile import load_architecture_document
from .topology import Topology, topology_from_legacy, topology_from_profile
from .transport import ReplayPlan, ReplayRuntime


def inspect_topology(path: str | Path) -> Topology:
    document: object = json.loads(Path(path).read_text())
    if isinstance(document, dict) and cast(dict[str, object], document).get("kind") == "canonical_topology":
        return Topology.compile(CanonicalTopology.model_validate(document))
    architecture = load_architecture_document(path)
    if isinstance(architecture, HardwareProfileConfig):
        return topology_from_profile(architecture)
    return topology_from_legacy(architecture.noc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect topology inventory or replay admitted synthetic byte traffic.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inspect", type=Path)
    mode.add_argument("--replay", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        status = 0
        if args.inspect is not None:
            output = json.dumps(inspect_topology(args.inspect).export(), indent=2, allow_nan=False)
        else:
            plan = ReplayPlan.load(args.replay)
            result = ReplayRuntime(plan).run()
            output = result.model_dump_json(indent=2)
            status = 0 if result.status == "complete" else 2
        if args.output is not None:
            args.output.write_text(output + "\n")
        print(output)
        return status
    except (OSError, ValueError, TypeError, NotImplementedError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

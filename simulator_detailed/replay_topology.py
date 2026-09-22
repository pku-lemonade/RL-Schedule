"""Separate JSON-only inspection and synthetic transport entry point."""

import argparse
import json
import sys
from pathlib import Path
from typing import cast

from .configs.schemas.generic_graph import GenericSystemGraph
from .configs.schemas.hardware_profile import HardwareProfileConfig
from .configs.schemas.topology import CanonicalTopology, ReplayResult
from .configs.schemas.torus_replay import ProfileSource, TorusReplay
from .generic_graph import topology_from_generic
from .hardware_profile import load_architecture_document
from .topology import Topology, topology_from_legacy, topology_from_profile
from .torus import TorusPlan
from .torus_records import TorusReplayResult
from .torus_transport import TorusTransport
from .transport import ReplayPlan, ReplayRuntime


def inspect_topology(path: str | Path) -> Topology:
    document: object = json.loads(Path(path).read_text())
    if isinstance(document, dict) and cast(dict[str, object], document).get("kind") == "canonical_topology":
        return Topology.compile(CanonicalTopology.model_validate(document))
    if isinstance(document, dict) and cast(dict[str, object], document).get("kind") == "generic_system_graph":
        return topology_from_generic(GenericSystemGraph.model_validate(document))
    architecture = load_architecture_document(path)
    if isinstance(architecture, HardwareProfileConfig):
        return topology_from_profile(architecture)
    return topology_from_legacy(architecture.noc)


def run_replay(path: str | Path) -> ReplayResult | TorusReplayResult:
    """Dispatch exact input versions; source paths belong to the replay directory."""
    path = Path(path)
    document: object = json.loads(path.read_text())
    if not isinstance(document, dict):
        raise TypeError("replay requires a topology_replay document")
    header = cast(dict[str, object], document)
    version = header.get("schema_version")
    if header.get("kind") != "topology_replay" or type(version) is not int or version not in (1, 2):
        raise ValueError("replay requires kind=topology_replay and integer schema_version=1 or 2")
    if version == 1:
        return ReplayRuntime(ReplayPlan.load(path)).run()
    config = TorusReplay.model_validate(document)
    source_path = config.source.profile_path if isinstance(config.source, ProfileSource) else config.source.graph_path
    source: object = json.loads((path.parent / source_path).read_text())
    return TorusTransport(TorusPlan.compile(config, source)).run()


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
            result = run_replay(args.replay)
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

"""JSON-only command wrapper; importing the profile helper never runs this CLI."""

import argparse
import sys

from .configs.schemas.hardware_profile import HardwareProfileConfig
from .hardware_profile import inspect_profile, load_architecture_document


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect a hardware profile without running simulation."
    )
    parser.add_argument("profile", help="Hardware-profile JSON path")
    args = parser.parse_args()
    try:
        profile = load_architecture_document(args.profile)
        if not isinstance(profile, HardwareProfileConfig):
            raise TypeError(
                "inspection requires kind='hardware_profile'; input is a legacy runtime configuration"
            )
        print(inspect_profile(profile).model_dump_json(indent=2))
    except (OSError, ValueError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

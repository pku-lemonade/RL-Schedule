"""Private fixed-adapter subprocess entry point; no user-supplied executable code."""

import json
import sys
from pathlib import Path

from ..configs.schemas.validation import ValidationCase
from .adapters import admit


def main() -> None:
    case = ValidationCase.model_validate_json(sys.stdin.read())
    admitted = admit(case.adapter, Path(case.input_path), horizon=case.budget.max_aci_cycles)
    print(json.dumps(admitted.execute(case.resume_at_aci_cycles), allow_nan=False, separators=(",", ":")))


if __name__ == "__main__":
    main()

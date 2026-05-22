import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_local_remap import (  # noqa: E402
    BASELINE_SEQUENCE,
    CHANNEL_LAYER10_SEQUENCE,
    FAIL_PATH,
    INVALID_LAYER0_SEQUENCE,
    LAYER18_BAD_SEQUENCE,
    MIXED_LAYER5_LAYER19_SEQUENCE,
    SAFE_LAYER5_SEQUENCE,
    SMALL_LAYER19_SEQUENCE,
    run_sequence,
)


def main():
    cases = [
        ("baseline-no-action", BASELINE_SEQUENCE, FAIL_PATH, 240),
        ("safe-layer5-sequence", SAFE_LAYER5_SEQUENCE, FAIL_PATH, 180),
        ("channel-layer10-sequence", CHANNEL_LAYER10_SEQUENCE, FAIL_PATH, 240),
        ("layer18-bad-sequence-normal", LAYER18_BAD_SEQUENCE, FAIL_PATH, 300),
        ("small-layer19-sequence", SMALL_LAYER19_SEQUENCE, FAIL_PATH, 240),
        ("mixed-layer5-layer19-sequence", MIXED_LAYER5_LAYER19_SEQUENCE, FAIL_PATH, 240),
        ("invalid-layer0-sequence", INVALID_LAYER0_SEQUENCE, FAIL_PATH, 120),
    ]

    for index, (name, sequence, fail_path, timeout_seconds) in enumerate(cases, start=1):
        print(f"Case {index}: {name}")
        result = run_sequence(sequence, fail_path=fail_path, timeout_seconds=timeout_seconds)
        print(result)
        print()


if __name__ == "__main__":
    main()

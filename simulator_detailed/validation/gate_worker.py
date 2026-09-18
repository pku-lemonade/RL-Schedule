"""Structured unittest gate results, including individual skip reasons."""

import contextlib
import io
import json
import sys
import unittest

PATTERNS = {"detailed_unittest": "test_*.py", "compute_unittest": "test_compute_*.py",
            "memory_unittest": "test_memory_*.py", "torus_unittest": "test_torus*.py"}


def main() -> None:
    pattern = PATTERNS[sys.argv[1]]
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        suite = unittest.defaultTestLoader.discover("simulator_detailed/tests", pattern=pattern)
        result = unittest.TextTestRunner(stream=output).run(suite)
    print(json.dumps({"discovered": result.testsRun,
                      "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped) - len(result.expectedFailures) - len(result.unexpectedSuccesses),
                      "failed": len(result.failures) + len(result.errors) + len(result.unexpectedSuccesses),
                      "skipped": [(str(t), reason) for t, reason in result.skipped],
                      "expected_failures": len(result.expectedFailures), "diagnostic": output.getvalue()[-12000:]}))


if __name__ == "__main__":
    main()

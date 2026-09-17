"""Evidence policy, independent of simulation completion and report formatting."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ..configs.schemas.validation import CheckResult

CheckOutcome = Literal["pass", "fail", "blocked", "unsupported", "not_run"]
ReportStatus = Literal["pass", "fail", "incomplete"]
EvidenceTier = Literal[
    "architecture_protocol", "model_invariant", "functional_reference", "silicon_timing"
]
EvidenceStatus = Literal["validated", "failed", "unvalidated"]


def aggregate_status(checks: Sequence[CheckResult]) -> ReportStatus:
    """Optional failures matter; absent/optional-only evidence cannot pass."""
    if any(check.outcome == "fail" for check in checks):
        return "fail"
    required = [check for check in checks if check.required]
    if not required or any(check.outcome != "pass" for check in required):
        return "incomplete"
    return "pass"


def tier_status(checks: Sequence[CheckResult], tier: EvidenceTier) -> EvidenceStatus:
    """Only actual checks in this tier support its narrowly scoped claim."""
    selected = [check for check in checks if check.tier == tier]
    if any(check.outcome == "fail" for check in selected):
        return "failed"
    if any(check.required and check.outcome != "pass" for check in selected):
        return "unvalidated"
    return "validated" if any(check.outcome == "pass" for check in selected) else "unvalidated"


def test_gate_outcome(*, passed: int, failed: int, skipped: int) -> CheckOutcome:
    """A skipped prerequisite is unavailable evidence, including in mixed runs."""
    if any(type(count) is not int or count < 0 for count in (passed, failed, skipped)):
        raise ValueError("test counts must be nonnegative integers")
    if failed:
        return "fail"
    if skipped:
        return "blocked"
    return "pass" if passed else "not_run"

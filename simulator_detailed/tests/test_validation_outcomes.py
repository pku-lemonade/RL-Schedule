"""Evidence strength and completion are independently checked, never inferred."""

import copy
import itertools
import json
import unittest

from pydantic import ValidationError

from simulator_detailed.configs.schemas.validation import CheckResult, ValidationReport
from simulator_detailed.tests.validation_fixtures import (
    HASH,
    check_result,
    report_document,
)
from simulator_detailed.validation.outcomes import (
    aggregate_status,
    test_gate_outcome,
    tier_status,
)


def parse_check(**changes):
    return CheckResult.model_validate_json(json.dumps(check_result(**changes)))


def parse_report(document):
    return ValidationReport.model_validate_json(json.dumps(document))


class ValidationOutcomeTests(unittest.TestCase):
    def test_aggregate_all_required_optional_outcome_combinations(self):
        outcomes = ("pass", "fail", "blocked", "unsupported", "not_run")
        for required, optional in itertools.product(outcomes, repeat=2):
            checks = (parse_check(outcome=required, executed=required != "not_run"),
                      parse_check(check_id="optional", required=False, outcome=optional, executed=optional != "not_run"))
            expected = "fail" if "fail" in (required, optional) else "pass" if required == "pass" else "incomplete"
            with self.subTest(required=required, optional=optional):
                self.assertEqual(aggregate_status(checks), expected)

    def test_empty_and_optional_only_checks_never_pass(self):
        self.assertEqual(aggregate_status(()), "incomplete")
        self.assertEqual(aggregate_status((parse_check(required=False),)), "incomplete")
        document = report_document()
        document["cases"][0]["checks"][0]["required"] = False
        with self.assertRaisesRegex(ValidationError, "requires required checks"):
            parse_report(document)

    def test_optional_absent_reference_allows_only_offline_scope_to_pass(self):
        document = report_document()
        document["cases"][0]["checks"].append(check_result(
            check_id="timing", required=False, outcome="blocked", tier="silicon_timing",
            executed=False, observation_ids=[], reason="matched hardware capture unavailable"))
        report = parse_report(document)
        self.assertEqual(report.status, "pass")
        self.assertEqual(report.silicon_timing, "unvalidated")
        document["cases"][0]["checks"][-1]["required"] = True
        with self.assertRaisesRegex(ValidationError, "status disagrees"):
            parse_report(document)
        document["status"] = "incomplete"
        self.assertEqual(parse_report(document).status, "incomplete")

    def test_expected_incomplete_execution_can_pass_its_state_invariant(self):
        document = report_document()
        case = document["cases"][0]
        case["execution"] = "incomplete"
        case["observations"][0].update(execution="incomplete", pending=["posted-effects"])
        case["observations"][0]["metrics"] = []
        case["checks"][0]["reason"] = "expected charged pending state verified"
        report = parse_report(document)
        self.assertEqual(report.status, "pass")
        self.assertEqual(report.cases[0].execution, "incomplete")
        self.assertEqual(report.silicon_timing, "unvalidated")

    def test_inspection_and_expected_rejection_do_not_become_execution(self):
        for state in ("inspected", "rejected", "unavailable"):
            document = report_document()
            case = document["cases"][0]
            case.update(execution=state, observations=[])
            case["checks"][0]["observation_ids"] = []
            if state != "inspected":
                case["identity"] = None
            self.assertEqual(parse_report(document).cases[0].execution, state)

    def test_unexecuted_checks_cannot_pass_or_fail(self):
        for outcome in ("pass", "fail"):
            with self.assertRaisesRegex(ValidationError, "executed check"):
                parse_check(outcome=outcome, executed=False)
        with self.assertRaisesRegex(ValidationError, "not_run"):
            parse_check(outcome="not_run", executed=True)

    def test_synthetic_and_functional_evidence_cannot_be_promoted_to_silicon(self):
        for source in ("synthetic", "architecture_document", "functional_capture"):
            with self.subTest(source=source), self.assertRaisesRegex(ValidationError, "appropriate evidence"):
                parse_check(tier="silicon_timing", comparison_admitted=True,
                            evidence=[{"reference_id": "ref", "document_sha256": HASH, "classification": source}])
        for tier in ("architecture_protocol", "functional_reference"):
            with self.assertRaises(ValidationError):
                parse_check(tier=tier, comparison_admitted=True,
                            evidence=[{"reference_id": "ref", "document_sha256": HASH, "classification": "synthetic"}])
        model = parse_check(evidence=[{"reference_id": "ref", "document_sha256": HASH, "classification": "synthetic"}])
        self.assertEqual(tier_status((model,), "silicon_timing"), "unvalidated")

    def test_synthetic_disagreement_is_not_a_measured_failure(self):
        with self.assertRaisesRegex(ValidationError, "appropriate evidence"):
            parse_check(outcome="fail", tier="silicon_timing", comparison_admitted=True,
                        evidence=[{"reference_id": "ref", "document_sha256": HASH, "classification": "synthetic"}])
        failed = parse_check(outcome="fail")
        self.assertEqual(tier_status((failed,), "model_invariant"), "failed")
        self.assertEqual(tier_status((failed,), "silicon_timing"), "unvalidated")

    def test_external_pass_needs_admission_observations_and_correct_reference(self):
        evidence = [{"reference_id": "ref", "document_sha256": HASH, "classification": "hardware_capture"}]
        for changes in ({"comparison_admitted": False}, {"observation_ids": []}, {"evidence": []}):
            arguments = {"tier": "silicon_timing", "comparison_admitted": True, "evidence": evidence, **changes}
            with self.assertRaises(ValidationError):
                parse_check(**arguments)
        # This tests admissible record structure, not an actual measured comparison.
        measured = parse_check(tier="silicon_timing", comparison_admitted=True, evidence=evidence)
        self.assertEqual(tier_status((measured,), "silicon_timing"), "validated")

    def test_report_rejects_changed_reference_classification_or_unknown_observation(self):
        document = report_document()
        evidence = {"reference_id": "ref", "document_sha256": HASH, "classification": "hardware_capture"}
        document["references"] = [{**evidence, "classification": "synthetic"}]
        document["cases"][0]["checks"][0].update(tier="silicon_timing", comparison_admitted=True, evidence=[evidence])
        document["silicon_timing"] = "validated"
        with self.assertRaisesRegex(ValidationError, "classification"):
            parse_report(document)
        document = report_document()
        document["cases"][0]["checks"][0]["observation_ids"] = ["not-observed"]
        with self.assertRaisesRegex(ValidationError, "unknown observation"):
            parse_report(document)

    def test_status_and_tier_claims_cannot_be_forged(self):
        for field in ("silicon_timing", "functional_reference"):
            document = report_document()
            document[field] = "validated"
            with self.assertRaisesRegex(ValidationError, "claims disagree"):
                parse_report(document)
        document = report_document()
        document["cases"][0]["checks"][0]["outcome"] = "fail"
        with self.assertRaisesRegex(ValidationError, "status disagrees"):
            parse_report(document)

    def test_skips_are_visible_and_never_counted_as_passes(self):
        for counts, expected in (((0, 0, 0), "not_run"), ((3, 0, 0), "pass"),
                                 ((0, 0, 1), "blocked"), ((314, 0, 1), "blocked"),
                                 ((3, 1, 1), "fail")):
            self.assertEqual(test_gate_outcome(passed=counts[0], failed=counts[1], skipped=counts[2]), expected)
        for count in (-1, True, 1.5):
            with self.assertRaises(ValueError):
                test_gate_outcome(passed=count, failed=0, skipped=0)

    def test_coverage_requires_real_checks_and_implementing_commits(self):
        document = report_document()
        coverage = {"requirement_id": "VA-D05", "status": "implemented_and_checked",
                    "checks": [{"case_id": "compute", "check_id": "drain"}],
                    "child_commits": ["b" * 40], "reason": "synthetic coverage schema test"}
        document["coverage"] = [coverage]
        parse_report(document)
        for changes in ({"checks": []}, {"child_commits": []},
                        {"checks": [{"case_id": "compute", "check_id": "missing"}]}):
            broken = copy.deepcopy(document)
            broken["coverage"][0].update(changes)
            with self.assertRaises(ValidationError):
                parse_report(broken)
        document["status"] = "incomplete"
        document["cases"][0]["checks"][0]["outcome"] = "blocked"
        with self.assertRaisesRegex(ValidationError, "requires passed checks"):
            parse_report(document)
        document["coverage"][0]["status"] = "partial"
        parse_report(document)

    def test_bypassed_model_copy_is_revalidated_at_report_boundary(self):
        original = parse_report(report_document())
        forged = original.model_copy(update={"silicon_timing": "validated"})
        with self.assertRaisesRegex(ValidationError, "claims disagree"):
            ValidationReport.model_validate(forged)

    def test_gate_coverage_uses_scoped_ids_and_preserves_skips(self):
        document = report_document()
        document["gate_checks"] = [check_result(check_id="types", observation_ids=[])]
        document["coverage"] = [{"requirement_id": "VA-D06", "status": "implemented_and_checked",
                                 "checks": [{"case_id": None, "check_id": "types"}],
                                 "child_commits": ["b" * 40], "reason": "synthetic coverage fixture"}]
        parse_report(document)
        document["gate_checks"][0].update(outcome="blocked", reason="one optional test skipped")
        document["status"] = "incomplete"
        with self.assertRaisesRegex(ValidationError, "requires passed checks"):
            parse_report(document)

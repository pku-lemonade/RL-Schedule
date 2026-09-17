"""Admission defects must fail before any later simulator allocation or output."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from simulator_detailed.configs.schemas.validation import (
    CalibrationPlan,
    CalibrationResult,
    NormalizedObservations,
    ValidationDocument,
    ValidationReference,
    ValidationSuite,
)
from simulator_detailed.tests.validation_fixtures import (
    calibration_plan,
    calibration_result,
    observations,
    reference_document,
    report_document,
    suite_document,
)
from simulator_detailed.validation.identity import load_document


def parse(model, document):
    return model.model_validate_json(json.dumps(document))


class ValidationContractTests(unittest.TestCase):
    def test_five_document_round_trips_and_frozen_records(self):
        adapter = TypeAdapter(ValidationDocument)
        for fixture in (suite_document, reference_document, report_document, calibration_plan, calibration_result):
            with self.subTest(kind=fixture()["kind"]):
                record = adapter.validate_json(json.dumps(fixture()))
                self.assertEqual(adapter.validate_json(record.model_dump_json()), record)
                with self.assertRaises(ValidationError):
                    record.schema_version = 2

    def test_all_document_versions_unknown_fields_and_coercions_rejected(self):
        adapter = TypeAdapter(ValidationDocument)
        for fixture in (suite_document, reference_document, report_document, calibration_plan, calibration_result):
            for field, value in (("schema_version", 2), ("schema_version", True), ("schema_version", "1"),
                                 ("schema_version", 1.0), ("shell", "echo bad"), ("kind", "unknown")):
                document = fixture()
                document[field] = value
                with self.subTest(kind=fixture()["kind"], field=field, value=value), self.assertRaises(ValidationError):
                    adapter.validate_json(json.dumps(document))

    def test_empty_required_set_duplicate_cases_checks_and_budget_rejected(self):
        documents = []
        doc = suite_document()
        doc["cases"] = []
        documents.append(doc)
        doc = suite_document()
        doc["cases"][0]["checks"][0]["required"] = False
        documents.append(doc)
        doc = suite_document()
        doc["cases"] *= 2
        doc["max_cases"] = 2
        documents.append(doc)
        doc = suite_document()
        doc["cases"][0]["checks"] *= 2
        documents.append(doc)
        doc = suite_document()
        doc["cases"].append({**doc["cases"][0], "case_id": "second"})
        documents.append(doc)
        for doc in documents:
            with self.subTest(document=doc), self.assertRaises(ValidationError):
                parse(ValidationSuite, doc)

    def test_named_registries_do_not_accept_code_or_unknown_implementations(self):
        for field, value in (("adapter", "os.system"), ("adapter", "compute_workload_v9"),
                             ("shell", "rm -rf /tmp/fixture")):
            doc = suite_document()
            doc["cases"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                parse(ValidationSuite, doc)
        for check in ("lambda: True", "unregistered", "__import__('os')"):
            doc = suite_document()
            doc["cases"][0]["checks"][0]["check"] = check
            with self.assertRaises(ValidationError):
                parse(ValidationSuite, doc)
        doc = suite_document()
        doc["gates"] = [{"gate_id": "bad", "gate": "python -c print(1)", "required": True,
                         "wall_time_seconds": 10, "requirements": ["VA-D06"]}]
        with self.assertRaises(ValidationError):
            parse(ValidationSuite, doc)

    def test_nonfinite_and_nonpositive_budgets_rejected(self):
        for field in ("wall_time_seconds", "max_aci_cycles"):
            for value in (float("inf"), float("nan"), -1, 0, "10", True, None):
                doc = suite_document()
                doc["cases"][0]["budget"][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    parse(ValidationSuite, doc)

    def test_reference_bindings_and_metric_policy_required(self):
        doc = suite_document()
        check = doc["cases"][0]["checks"][0]
        check.update(check="functional_reference", tier="functional_reference", reference_id="missing")
        with self.assertRaisesRegex(ValidationError, "unknown reference"):
            parse(ValidationSuite, doc)
        doc["references"] = [{"reference_id": "missing", "document": {"path": "ref.json", "sha256": "a" * 64}}]
        parse(ValidationSuite, doc)
        check.update(check="silicon_timing", tier="silicon_timing")
        with self.assertRaisesRegex(ValidationError, "predeclared tolerances"):
            parse(ValidationSuite, doc)

    def test_unknown_metadata_is_explicit_and_never_filled_from_defaults(self):
        doc = reference_document()
        record = parse(ValidationReference, doc)
        self.assertIsNone(record.conditions.device.value)
        self.assertIsNone(record.conditions.clocks.value)
        for metadata in ({"state": "unknown"}, {"state": "known"},
                         {"state": "unknown", "value": "device", "reason": "absent"},
                         {"state": "known", "value": "device", "reason": "absent"}):
            invalid = copy.deepcopy(doc)
            invalid["conditions"]["device"] = metadata
            with self.assertRaises(ValidationError):
                parse(ValidationReference, invalid)

    def test_reference_requires_raw_identity_and_immutable_origin(self):
        doc = reference_document()
        doc["provenance"]["snapshot_sha256"] = {"state": "unknown", "reason": "missing"}
        with self.assertRaisesRegex(ValidationError, "immutable revision or snapshot"):
            parse(ValidationReference, doc)
        doc = reference_document()
        doc["provenance"]["raw_artifact"]["sha256"] = "not-a-hash"
        with self.assertRaises(ValidationError):
            parse(ValidationReference, doc)
        doc = reference_document("hardware_capture")
        doc["provenance"]["producer"] = "ttsim"
        with self.assertRaisesRegex(ValidationError, "not a silicon timing source"):
            parse(ValidationReference, doc)

    def test_large_counters_and_timestamps_survive_exactly(self):
        record = parse(NormalizedObservations, observations())
        roundtrip = NormalizedObservations.model_validate_json(record.model_dump_json())
        self.assertEqual(roundtrip.events[1].counters[0].value, 2**53 + 3)
        self.assertIsInstance(roundtrip.events[1].counters[0].value, int)
        self.assertEqual(roundtrip.events[1].time.value - roundtrip.events[0].time.value, 7)

    def test_observation_references_domains_and_partial_order_are_validated(self):
        changes = [
            ("events", [{"event_id": "e", "action": "x", "subject_id": "missing", "time": None}]),
            ("causal_edges", [{"before": "begin", "after": "missing"}]),
            ("causal_edges", [{"before": "begin", "after": "end"}, {"before": "end", "after": "begin"}]),
            ("pending", ["unfinished"]), ("clocks", []),
        ]
        for field, value in changes:
            doc = observations()
            doc[field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                parse(NormalizedObservations, doc)
        doc = observations()
        doc["events"].reverse()
        self.assertEqual(parse(NormalizedObservations, doc).causal_edges[0].before, "begin")

    def test_duplicate_observations_and_noninteger_counts_rejected(self):
        for field in ("entities", "events", "effects", "metrics", "clocks"):
            doc = observations()
            doc[field] *= 2
            with self.subTest(field=field), self.assertRaises(ValidationError):
                parse(NormalizedObservations, doc)
        for value in (1.0, True, "1", -1):
            doc = observations()
            doc["events"][1]["counters"][0]["value"] = value
            with self.assertRaises(ValidationError):
                parse(NormalizedObservations, doc)

    def test_incomplete_cannot_export_complete_run_throughput(self):
        doc = observations()
        doc["execution"] = "incomplete"
        with self.assertRaisesRegex(ValidationError, "complete-run metrics"):
            parse(NormalizedObservations, doc)
        doc["metrics"][0]["completion_scope"] = "partial"
        doc["pending"] = ["destination-effects"]
        self.assertEqual(parse(NormalizedObservations, doc).execution, "incomplete")

    def test_window_cannot_subtract_cross_domain_counters(self):
        for field, value in (("clock_domain", "other-core"), ("unit", "nanoseconds"), ("value", 0)):
            doc = observations()
            doc["metrics"][0]["window"]["end"][field] = value
            with self.assertRaises(ValidationError):
                parse(NormalizedObservations, doc)

    def test_nonfinite_metrics_and_unknown_resource_effects_rejected(self):
        for value in (float("nan"), float("inf"), float("-inf"), True, "7"):
            doc = observations()
            doc["metrics"][0]["value"] = value
            with self.subTest(value=value), self.assertRaises(ValidationError):
                parse(NormalizedObservations, doc)
        for field, value in (("destination_id", "missing"), ("resource_id", "ep"),
                             ("visibility_event", "unobserved")):
            doc = observations()
            doc["effects"][0][field] = value
            with self.assertRaises(ValidationError):
                parse(NormalizedObservations, doc)

    def test_reference_observations_are_bound_to_raw_artifact_identity(self):
        doc = reference_document()
        doc["observations"]["source_result_sha256"] = "b" * 64
        with self.assertRaisesRegex(ValidationError, "raw artifact"):
            parse(ValidationReference, doc)


class CalibrationContractTests(unittest.TestCase):
    def test_typed_targets_allow_only_four_effective_timing_fields(self):
        for kind, owner, fields in (("memory", "resource_id", ("bytes_per_cycle", "fixed_latency_cycles")),
                                    ("compute", "rate_id", ("work_per_native_cycle", "setup_native_cycles"))):
            for field in fields:
                doc = calibration_plan()
                doc["parameters"] = [{"parameter_id": "p", "target": {"kind": kind, owner: "owner", "field": field},
                                      "lower": 1, "upper": 2, "candidates": [1, 2]}]
                self.assertEqual(parse(CalibrationPlan, doc).parameters[0].target.field, field)
            for field in ("native_clock_hz", "capacity", "width", "topology", "enabled_workers", "json_path"):
                doc["parameters"][0]["target"]["field"] = field
                with self.subTest(field=field), self.assertRaises(ValidationError):
                    parse(CalibrationPlan, doc)

    def test_candidate_bounds_budgets_and_parameter_aliases_rejected(self):
        for field, value in (("lower", 0), ("lower", 9), ("upper", 0), ("upper", float("inf")),
                             ("candidates", []), ("candidates", [1, 1.0]), ("candidates", [9]),
                             ("candidates", [True]), ("candidates", [float("nan")])):
            doc = calibration_plan()
            doc["parameters"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                parse(CalibrationPlan, doc)
        for field, value in (("max_candidates", 5), ("max_case_runs", 6), ("max_candidates", 0)):
            doc = calibration_plan()
            doc[field] = value
            with self.assertRaises(ValidationError):
                parse(CalibrationPlan, doc)
        for same_id in (False, True):
            doc = calibration_plan()
            duplicate = copy.deepcopy(doc["parameters"][0])
            if not same_id:
                duplicate["parameter_id"] = "alias"
            doc["parameters"].append(duplicate)
            with self.assertRaisesRegex(ValidationError, "duplicate parameter"):
                parse(CalibrationPlan, doc)

    def test_renaming_cases_does_not_hide_split_leakage(self):
        for field in ("case_id", "semantic_sha256", "capture_group", "reference"):
            doc = calibration_plan()
            doc["evaluation_cases"][0][field] = doc["fit_cases"][0][field]
            with self.subTest(field=field), self.assertRaises(ValidationError):
                parse(CalibrationPlan, doc)

    def test_metrics_require_positive_scales_and_predeclared_tolerances(self):
        for field, value in (("scale", 0), ("weight", 0), ("absolute_tolerance", -1),
                             ("relative_tolerance", float("inf")), ("rationale", " ")):
            doc = calibration_plan()
            doc["metrics"][0][field] = value
            with self.assertRaises(ValidationError):
                parse(CalibrationPlan, doc)

    def test_contract_rejection_does_not_mutate_input_or_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "plan.json"
            output = Path(directory) / "report.json"
            doc = calibration_plan()
            doc["parameters"][0]["target"]["field"] = "capacity"
            source.write_text(json.dumps(doc))
            output.write_bytes(b"existing output")
            original = source.read_bytes()
            with self.assertRaises(ValidationError):
                load_document(source)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(output.read_bytes(), b"existing output")

    def test_invalid_candidates_cannot_have_fit_losses_or_frozen_winners(self):
        doc = calibration_result()
        doc["candidates"][0]["outcome"] = "rejected"
        with self.assertRaisesRegex(ValidationError, "selectable fit loss"):
            parse(CalibrationResult, doc)
        doc["candidates"][0]["fit_loss"] = None
        with self.assertRaisesRegex(ValidationError, "valid fitted candidate"):
            parse(CalibrationResult, doc)

    def test_frozen_selection_and_ties_must_match_fit_only_order(self):
        doc = calibration_result()
        second = copy.deepcopy(doc["candidates"][0])
        second["candidate_id"] = "candidate-1"
        doc["candidates"].append(second)
        with self.assertRaisesRegex(ValidationError, "tied minima"):
            parse(CalibrationResult, doc)
        doc["tied_candidate_ids"] = ["candidate-0", "candidate-1"]
        parse(CalibrationResult, doc)
        doc["selection"]["candidate_id"] = "candidate-1"
        with self.assertRaisesRegex(ValidationError, "first minimum-loss"):
            parse(CalibrationResult, doc)

    def test_failed_held_out_evaluation_stays_failed_despite_perfect_fit(self):
        doc = calibration_result()
        doc["evaluation_checks"][0]["outcome"] = "fail"
        with self.assertRaisesRegex(ValidationError, "held-out outcomes"):
            parse(CalibrationResult, doc)
        doc["status"] = "fail"
        result = parse(CalibrationResult, doc)
        self.assertEqual(result.selection.candidate_id, "candidate-0")
        self.assertEqual(result.candidates[0].fit_loss, 0)

    def test_no_selection_or_synthetic_data_cannot_claim_measured_success(self):
        doc = calibration_result()
        doc["evidence_scope"] = "measured_conditions"
        with self.assertRaisesRegex(ValidationError, "measured calibration"):
            parse(CalibrationResult, doc)
        doc = calibration_result()
        doc.update(candidates=[], selection=None, evaluation_checks=[], evidence_scope="unvalidated")
        with self.assertRaisesRegex(ValidationError, "no selection"):
            parse(CalibrationResult, doc)
        doc["status"] = "incomplete"
        self.assertIsNone(parse(CalibrationResult, doc).selection)

    def test_parameter_vectors_and_repeated_reference_ids_cannot_be_ambiguous(self):
        doc = calibration_result()
        second = copy.deepcopy(doc["candidates"][0])
        second["candidate_id"] = "candidate-1"
        second["values"][0]["parameter_id"] = "renamed-parameter"
        doc["candidates"].append(second)
        with self.assertRaisesRegex(ValidationError, "same ordered parameter IDs"):
            parse(CalibrationResult, doc)
        doc = calibration_plan()
        doc["evaluation_cases"][0]["reference"]["reference_id"] = "fit"
        with self.assertRaisesRegex(ValidationError, "ambiguous calibration reference"):
            parse(CalibrationPlan, doc)

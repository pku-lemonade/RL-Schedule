"""Small synthetic contracts, not vendor captures or hardware measurements."""

from simulator_detailed.validation.identity import content_digest

HASH = "a" * 64


def unknown():
    return {"state": "unknown", "reason": "not collected in this synthetic fixture"}


def window():
    return {
        "boundary": "transaction", "start": {"value": 2**53 + 1, "unit": "cycles", "clock_domain": "aci"},
        "end": {"value": 2**53 + 8, "unit": "cycles", "clock_domain": "aci"},
        "excluded_warmups": [], "repetitions": 1, "aggregation": "none",
    }


def observations():
    interval = window()
    return {
        "observation_id": "observed", "source_result_sha256": HASH, "execution": "complete",
        "clocks": [{"domain_id": "aci", "hz": {"state": "known", "value": 500_000_000}}],
        "entities": [{"entity_id": "memory", "role": "resource"},
                     {"entity_id": "ep", "role": "endpoint", "physical_owner": "memory"}],
        "events": [{"event_id": "begin", "action": "submit", "subject_id": "ep", "time": interval["start"]},
                   {"event_id": "end", "action": "visible", "subject_id": "ep", "time": interval["end"],
                    "counters": [{"name": "payload", "value": 2**53 + 3, "unit": "bytes", "scope": "observed"}]}],
        "effects": [{"effect_id": "write", "destination_id": "ep", "resource_id": "memory",
                     "offset_bytes": 0, "size_bytes": 64, "count": 1, "visibility_event": "end"}],
        "causal_edges": [{"before": "begin", "after": "end"}], "routes": [], "intervals": [],
        "metrics": [{"metric_id": "latency", "value": 7, "unit": "cycles", "clock_domain": "aci",
                     "numerator": "elapsed cycles", "denominator": "one transaction", "window": interval,
                     "completion_scope": "complete_run"}], "pending": [], "missing": [],
    }


def reference_document(classification="synthetic"):
    return {
        "kind": "validation_reference", "schema_version": 1, "reference_id": "reference",
        "format": "normalized_functional_v1",
        "provenance": {
            "classification": classification, "producer": "synthetic_fixture" if classification == "synthetic" else "test_declared_external_producer",
            "source_url": unknown(), "revision": unknown(),
            "snapshot_sha256": {"state": "known", "value": HASH},
            "raw_artifact": {"path": "capture.json", "sha256": HASH},
            "extractor": "normalized_json", "extractor_version": "1",
            "original_units": ["bytes", "cycles"], "normalized_units": ["bytes", "cycles"],
        },
        "conditions": {name: unknown() for name in (
            "architecture", "device", "device_scope", "profile_version", "enabled_layout", "clocks",
            "workload", "mapping", "software", "firmware", "instrumentation", "measurement", "capture_group",
        )},
        "observations": observations(),
    }


def suite_document():
    return {
        "kind": "validation_suite", "schema_version": 1, "suite_id": "offline", "max_cases": 1,
        "cases": [{"case_id": "compute", "adapter": "compute_workload_v1", "input_path": "workload.json",
                   "budget": {"wall_time_seconds": 10, "max_aci_cycles": 1000}, "expected_execution": "complete",
                   "checks": [{"check_id": "drain", "check": "drain", "required": True,
                               "tier": "model_invariant", "requirements": ["VA-D05"]}]}],
    }


def identity_document():
    files = [{"logical_path": "simulator/example.py", "sha256": HASH, "size_bytes": 10}]
    return {
        "source": {"revision": {"state": "known", "value": "b" * 40},
                   "dirty": {"state": "known", "value": False}, "files": files,
                   "bundle_sha256": content_digest(files)},
        "inputs": [{"logical_path": "workload.json", "sha256": HASH, "size_bytes": 20}],
        "effective_plan": {"text": '{"clock_hz":500000000,"rate":4}'},
        "effective_plan_sha256": content_digest({"clock_hz": 500000000, "rate": 4}),
        "selection": {"text": '{"check":"drain"}'}, "selection_sha256": content_digest({"check": "drain"}),
        "environment": {"python_version": "3.12.12", "python_implementation": "CPython",
                        "system": "synthetic", "release": "test", "machine": "test", "dependencies": []},
        "seed": {"mode": "deterministic"},
    }


def check_result(**changes):
    return {"check_id": "drain", "required": True, "outcome": "pass", "tier": "model_invariant",
            "reason": "synthetic contract check executed", "executed": True,
            "observation_ids": ["observed"], **changes}


def report_document():
    return {
        "kind": "validation_report", "schema_version": 1, "suite_sha256": HASH, "status": "pass",
        "cases": [{"case_id": "compute", "adapter": "compute_workload_v1", "execution": "complete",
                   "reason": "synthetic fixture", "identity": identity_document(),
                   "observations": [observations()], "checks": [check_result()]}],
        "coverage": [], "capabilities": ["abstract_compute_workload_v1"], "assumptions": ["synthetic fixture"],
        "limitations": ["no hardware measurement"], "functional_reference": "unvalidated",
        "silicon_timing": "unvalidated",
    }


def calibration_plan():
    def case(name, digest):
        return {"case_id": name, "adapter": "compute_workload_v1", "input_path": name + ".json",
                "reference": {"reference_id": name, "document": {"path": name + "-ref.json", "sha256": digest}},
                "budget": {"wall_time_seconds": 10, "max_aci_cycles": 1000},
                "semantic_sha256": digest, "capture_group": name}

    return {
        "kind": "calibration_plan", "schema_version": 1, "plan_id": "synthetic-fit",
        "parameters": [{"parameter_id": "bandwidth", "target": {"kind": "memory", "resource_id": "dram",
                        "field": "bytes_per_cycle"}, "lower": 1, "upper": 8, "candidates": [1, 4, 8]},
                       {"parameter_id": "setup", "target": {"kind": "compute", "rate_id": "matmul",
                        "field": "setup_native_cycles"}, "lower": 0, "upper": 2, "candidates": [0, 2]}],
        "max_candidates": 6, "max_case_runs": 7, "fit_cases": [case("fit", "a" * 64)],
        "evaluation_cases": [case("held-out", "b" * 64)],
        "metrics": [{"metric_id": "latency", "unit": "cycles", "boundary": "transaction",
                     "absolute_tolerance": 1, "relative_tolerance": 0, "rationale": "synthetic one-cycle fixture",
                     "weight": 1, "scale": 10}],
        "loss": "weighted_mean_absolute_scaled_error_v1", "tie_break": "declared_candidate_order",
        "evidence_scope": "synthetic_demonstration",
    }


def calibration_result():
    values = [{"parameter_id": "bandwidth", "value": 4}]
    return {
        "kind": "calibration_result", "schema_version": 1, "plan_sha256": HASH, "status": "pass",
        "reason": "synthetic schema fixture, no fitting executed by parsing",
        "candidates": [{"candidate_id": "candidate-0", "values": values, "outcome": "valid",
                        "reason": "synthetic fit", "fit_loss": 0, "configuration_sha256": HASH,
                        "fit_checks": [check_result()]}],
        "selection": {"candidate_id": "candidate-0", "values": values, "configuration_sha256": HASH,
                      "fit_evidence_sha256": HASH, "selection_sha256": HASH},
        "tied_candidate_ids": [], "evaluation_checks": [check_result()],
        "evidence_scope": "synthetic_demonstration", "references": [],
    }

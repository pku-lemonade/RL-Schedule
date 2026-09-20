"""Exact supported intervals, repetition policy and fail-closed comparison tests."""

from __future__ import annotations

import copy
import json
import unittest
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

from pydantic import ValidationError

from simulator_detailed.configs.schemas.external_validation import (
    ExternalValidationCampaign,
    ModelIntervalSelection,
    RepetitionAggregation,
)
from simulator_detailed.configs.schemas.validation import (
    ClockDomain,
    Metadata,
    MetricPolicy,
    NormalizedObservations,
)
from simulator_detailed.validation.adapters import Admission, admit
from simulator_detailed.validation.comparison import IncompatibleReference, metric_pair
from simulator_detailed.validation.external import execute_model_repetitions
from simulator_detailed.validation.normalize import normalize

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ROOT / "configs"
EXTERNAL = CONFIGS / "validation/external"


def interval_metrics(observations: NormalizedObservations, boundary: str):
    return tuple(metric for metric in observations.metrics if metric.window.boundary == boundary)


class IntervalNormalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.memory = admit("memory_replay_v1", CONFIGS / "memory_replays/generic_ordered.json")
        cls.compute = admit("compute_workload_v1", CONFIGS / "compute_workloads/streaming_depth2.json")
        cls.mixed = admit("multicast_sync_v1", CONFIGS / "multicast_workloads/mixed_two_rounds.json")

    def checked_normalize(self, admitted: Admission) -> tuple[dict[str, object], NormalizedObservations]:
        raw = admitted.execute(())[-1]
        preserved = copy.deepcopy(raw)
        observations = normalize(admitted, raw)
        self.assertEqual(raw, preserved)
        return raw, observations

    def assert_exact_intervals(self, observations: NormalizedObservations, boundary: str) -> None:
        events = {event.event_id: event for event in observations.events}
        metrics = interval_metrics(observations, boundary)
        self.assertTrue(metrics)
        for metric in metrics:
            self.assertEqual(metric.completion_scope, "interval")
            self.assertEqual(metric.clock_domain, "aci")
            self.assertIsNotNone(metric.interval)
            sample = metric.interval.samples[0]  # type: ignore[union-attr]
            start, end = events[sample.start_event_id], events[sample.end_event_id]
            self.assertEqual(metric.window.start, start.time)
            self.assertEqual(metric.window.end, end.time)
            self.assertEqual(metric.value, end.time.value - start.time.value)  # type: ignore[union-attr]

    def test_memory_operation_and_service_intervals_retain_exact_endpoints(self):
        raw, observations = self.checked_normalize(self.memory)
        operations = interval_metrics(observations, "operation_submission_to_acknowledged_completion")
        self.assertEqual(tuple(metric.metric_id for metric in operations), (
            'operation_submission_to_acknowledged_completion:"ack"',
        ))
        self.assertNotIn("posted", operations[0].metric_id)
        services = interval_metrics(observations, "memory_service_begin_to_end")
        self.assertEqual(len(services), len(raw["chunks"]))  # type: ignore[arg-type,index]
        self.assert_exact_intervals(observations, "operation_submission_to_acknowledged_completion")
        self.assert_exact_intervals(observations, "memory_service_begin_to_end")

    def test_compute_and_mixed_intervals_follow_configured_runtime_values(self):
        compute_raw, compute = self.checked_normalize(self.compute)
        compute_metrics = interval_metrics(compute, "compute_resource_acquire_to_release")
        self.assertEqual(len(compute_metrics), len({row["job_id"] for row in compute_raw["resource_events"] if row["kind"] == "compute"}))  # type: ignore[index,union-attr]
        self.assert_exact_intervals(compute, "compute_resource_acquire_to_release")

        _, mixed = self.checked_normalize(self.mixed)
        for boundary in (
            "operation_submission_to_acknowledged_completion",
            "memory_service_begin_to_end",
            "compute_resource_acquire_to_release",
        ):
            self.assert_exact_intervals(mixed, boundary)
        self.assertEqual(mixed.clocks[0].hz.value, self.mixed.configuration["memory"]["aci_clock_hz"])  # type: ignore[index]

    def test_endpoint_and_resource_identity_corruption_is_rejected(self):
        _, observations = self.checked_normalize(self.compute)
        document = observations.model_dump(mode="json")
        metric = next(item for item in document["metrics"] if item["window"]["boundary"] == "compute_resource_acquire_to_release")
        metric["interval"]["samples"][0]["end_event_id"] = "missing:event"
        with self.assertRaisesRegex(ValidationError, "unknown endpoint event"):
            NormalizedObservations.model_validate_json(json.dumps(document))
        document = observations.model_dump(mode="json")
        metric = next(item for item in document["metrics"] if item["window"]["boundary"] == "compute_resource_acquire_to_release")
        metric["interval"]["resource_id"] = None
        with self.assertRaisesRegex(ValidationError, "single-sample|resource"):
            NormalizedObservations.model_validate_json(json.dumps(document))


class IntervalCampaignAndComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admitted = admit("memory_replay_v1", CONFIGS / "memory_replays/generic_ordered.json")
        cls.actual = normalize(cls.admitted, cls.admitted.execute(())[-1])
        cls.metric = interval_metrics(cls.actual, "memory_service_begin_to_end")[0]
        identity = cls.metric.interval
        if identity is None:
            raise AssertionError("fixture did not expose an interval identity")
        sample = identity.samples[0]
        cls.selection = ModelIntervalSelection(
            metric_id=cls.metric.metric_id,
            boundary="memory_service_begin_to_end",
            subject_id=identity.subject_id,
            resource_id=identity.resource_id,
            start_event_id=sample.start_event_id,
            end_event_id=sample.end_event_id,
            semantic_scope="memory_service",
        )
        cls.policy = MetricPolicy(
            metric_id=cls.metric.metric_id,
            unit="cycles",
            boundary="memory_service_begin_to_end",
            absolute_tolerance=0.0,
            relative_tolerance=0.0,
            rationale="exact interval arithmetic",
        )

    def test_campaign_binds_typed_interval_and_sample_policies_to_budget(self):
        campaign = ExternalValidationCampaign.model_validate_json((EXTERNAL / "campaign.valid.json").read_bytes())
        for case in campaign.cases:
            mapping = case.boundary_maps[0]
            self.assertEqual(mapping.simulator_interval.boundary, mapping.simulator_boundary)
            self.assertEqual(mapping.simulator_interval.semantic_scope, mapping.completion_scope)
            self.assertEqual(len(mapping.samples.repetition_ids), case.budget.repetitions)
            self.assertEqual(len(mapping.samples.warmup_repetition_ids), case.budget.warmup_repetitions)
        document = campaign.model_dump(mode="json")
        document["cases"][0]["budget"]["warmup_repetitions"] = 0
        with self.assertRaisesRegex(ValidationError, "sample policy"):
            ExternalValidationCampaign.model_validate_json(json.dumps(document))

    def varying_admission(self, offsets: list[int]) -> Admission:
        source = self.admitted.execute(())[-1]
        calls = iter(offsets)

        def execute(_stops: tuple[float, ...]):
            raw = copy.deepcopy(source)
            raw["chunks"][0]["end_aci_cycles"] += next(calls)
            return (raw,)

        return replace(self.admitted, execute=execute)

    def test_model_repetitions_execute_warmups_and_compute_mean_median_dispersion(self):
        mean_policy = RepetitionAggregation(
            repetition_ids=("warmup", "one", "two", "three"),
            warmup_repetition_ids=("warmup",),
            aggregation="mean",
        )
        result = execute_model_repetitions(
            self.varying_admission([100, 1, 3, 7]), self.selection, mean_policy
        )
        self.assertEqual(len(result.repetitions), 4)
        self.assertEqual(result.sample_values, (4, 6, 10))
        self.assertAlmostEqual(result.observation.metrics[0].value, 20 / 3)
        self.assertAlmostEqual(result.mean_absolute_deviation, 20 / 9)
        self.assertEqual(result.observation.metrics[0].window.excluded_warmups, ("warmup",))
        self.assertEqual(result.observation.metrics[0].window.repetitions, 3)
        self.assertEqual(
            tuple(sample.repetition_id for sample in result.observation.metrics[0].interval.samples),  # type: ignore[union-attr]
            ("one", "two", "three"),
        )
        self.assertEqual(
            NormalizedObservations.model_validate_json(result.observation.model_dump_json()),
            result.observation,
        )

        median_policy = mean_policy.model_copy(update={"aggregation": "median"})
        median = execute_model_repetitions(
            self.varying_admission([100, 1, 3, 7]), self.selection, median_policy
        )
        self.assertEqual(median.observation.metrics[0].value, 6)

    def test_single_sample_cannot_represent_repeated_aggregate(self):
        aggregate = execute_model_repetitions(
            self.varying_admission([0, 0, 0]),
            self.selection,
            RepetitionAggregation(
                repetition_ids=("one", "two", "three"),
                aggregation="mean",
            ),
        ).observation
        with self.assertRaisesRegex(IncompatibleReference, "repetitions"):
            metric_pair(self.actual, aggregate, self.policy, {})

    def test_interval_comparison_registry_completion_and_clock_rules(self):
        left, right = metric_pair(self.actual, self.actual, self.policy, {})
        self.assertEqual(left, right)

        bad_completion = self.metric.model_copy(update={"completion_scope": "complete_run"})
        with self.assertRaisesRegex(IncompatibleReference, "incomplete|boundary"):
            metric_pair(
                self.actual,
                self.actual.model_copy(update={"metrics": (bad_completion,)}),
                self.policy,
                {},
            )
        ambiguous = self.metric.model_copy(update={"interval": None})
        with self.assertRaisesRegex(IncompatibleReference, "semantic identity"):
            metric_pair(
                self.actual,
                self.actual.model_copy(update={"metrics": (ambiguous,)}),
                self.policy,
                {},
            )
        changed_identity = self.metric.model_copy(update={
            "interval": self.metric.interval.model_copy(update={"subject_id": "transfer:other"}),  # type: ignore[union-attr]
        })
        changed_observation = self.actual.model_copy(update={"metrics": (changed_identity,)})
        with self.assertRaisesRegex(IncompatibleReference, "subject mapping"):
            metric_pair(self.actual, changed_observation, self.policy, {})
        metric_pair(
            self.actual,
            changed_observation,
            self.policy,
            {},
            {"transfer:other": self.metric.interval.subject_id},  # type: ignore[union-attr]
        )
        for boundary in (
            "host_dispatch_plus_kernel",
            "full_kernel",
            "cross_core_subtraction",
            "profiler_overhead_corrected",
        ):
            with self.subTest(boundary=boundary), self.assertRaisesRegex(IncompatibleReference, "unsupported measurement boundary"):
                metric_pair(
                    self.actual,
                    self.actual,
                    self.policy.model_copy(update={"boundary": boundary}),
                    {},
                )

    def test_seconds_conversion_requires_explicit_domain_map_and_cycles_require_equal_frequency(self):
        hz = self.actual.clocks[0].hz.value
        if hz is None:
            raise AssertionError("fixture clock is unknown")
        doubled = self.metric.model_copy(update={
            "value": self.metric.value * 2,
            "clock_domain": "tensix",
            "window": self.metric.window.model_copy(update={
                "start": self.metric.window.start.model_copy(update={"clock_domain": "tensix"}),
                "end": self.metric.window.end.model_copy(update={"clock_domain": "tensix"}),
            }),
        })
        reference = self.actual.model_copy(update={
            "clocks": (ClockDomain(domain_id="tensix", hz=Metadata[float](state="known", value=hz * 2)),),
            "metrics": (doubled,),
        })
        seconds_policy = self.policy.model_copy(update={"unit": "seconds"})
        with self.assertRaisesRegex(IncompatibleReference, "clock-domain"):
            metric_pair(self.actual, reference, seconds_policy, {})
        left, right = metric_pair(self.actual, reference, seconds_policy, {"tensix": "aci"})
        self.assertEqual(left, right)
        with self.assertRaisesRegex(IncompatibleReference, "equal known frequencies"):
            metric_pair(self.actual, reference, self.policy, {"tensix": "aci"})

    def test_large_cycle_conversion_remains_exact(self):
        cycles = 2**60 + 3
        clock = ClockDomain(domain_id="large", hz=Metadata[float](state="known", value=250000000.0))
        from simulator_detailed.configs.schemas.validation import TimePoint
        from simulator_detailed.validation.normalize import converted_seconds

        self.assertEqual(
            converted_seconds(TimePoint(value=cycles, unit="cycles", clock_domain="large"), (clock,)),
            Fraction(cycles, 250000000),
        )


if __name__ == "__main__":
    unittest.main()

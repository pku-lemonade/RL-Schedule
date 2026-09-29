"""CPU-only whole-operation contract tests using constant synthetic quotes."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from simulator_detailed.configs.schemas.generic_transactions import (
    GenericSimulationResult, GenericTransactionBatch,
)
from simulator_detailed.configs.schemas.operation_cost import (
    OperationCostContract, OperationCostQuote, OperationCycleConversion,
    RUNTIME_CYCLE_UNIT,
)
from simulator_detailed.configs.schemas.system_spec import ImmutablePlan, SystemSpec
from simulator_detailed.generic_adapter import GenericInputAdapter, run_generic_adapter
from simulator_detailed.generic_runtime import GenericRuntime, run_generic_batch
from simulator_detailed.operation_cost import OperationCostRegistry
from simulator_detailed.runtime_context import RuntimeContext
from simulator_detailed.system_compile import compile_system
from simulator_detailed.topology import content_digest
from simulator_detailed.tests.test_generic_runtime import batch, system, transfer


def contract_doc():
    return {
        "quantity": "service_duration",
        "unit": {"kind": "time", "name": "test_tick", "clock_domain": "test_clock"},
        "domain": {"operation_kind": "test_job", "domain_id": "test_case_v1"},
        "provenance": {
            "provider_ref": "constant_test", "revision": "v1", "evidence_ref": "synthetic_test",
        },
        "service_semantics": "isolated_service_duration",
    }


def conversion_doc():
    return {
        "conversion_ref": "test_scale", "source": contract_doc()["unit"],
        "target": RUNTIME_CYCLE_UNIT.model_dump(), "numerator": 3, "denominator": 2,
        "evidence_ref": "synthetic_conversion", "rounding": "ceil",
    }


def operation(tx_id="op", **extra):
    return {
        "transaction_id": tx_id, "kind": "whole_operation", "provider_ref": "constant_test",
        "domain": contract_doc()["domain"], **extra,
    }


class ConstantProvider:
    def __init__(self, mutate=None, value=4.25):
        self.calls = []
        self.mutate = mutate
        self.value = value

    def quote(self, request):
        self.calls.append(request)
        result = dict(contract_doc(), scope="whole_operation", operation_id=request.operation_id,
                      value=self.value)
        if self.mutate:
            self.mutate(result)
        return result


def registry(provider=None, contract=None, conversion=None):
    provider = provider if provider is not None else ConstantProvider()
    costs = OperationCostRegistry()
    costs.register(
        provider, contract=OperationCostContract.model_validate(contract or contract_doc()),
        conversion=OperationCycleConversion.model_validate(conversion or conversion_doc()),
    )
    return costs, provider


def spec(transactions=None, max_cycles=1000.0, timing_doc=None):
    return SystemSpec(
        kind="system_spec", schema_version=1, spec_id="operation_test",
        graph=system().document,
        batch=GenericTransactionBatch.model_validate(batch(
            transactions if transactions is not None else [operation()],
            max_cycles=max_cycles, timing_doc=timing_doc,
        )),
    )


def execute(document=None, costs=None):
    return RuntimeContext(compile_system(document or spec(), operation_costs=costs)).run()


class TestOperationCostSuccess(unittest.TestCase):
    def test_legacy_input_and_output_unchanged_with_unused_registry(self):
        costs, provider = registry()
        document = spec([transfer("old", "eu_00", "mem_b_ep")])
        before = document.model_dump(mode="json")
        plain = compile_system(document)
        registered = compile_system(document, operation_costs=costs)
        self.assertEqual(plain.model_dump(mode="json"), registered.model_dump(mode="json"))
        old = RuntimeContext(plain).run()
        new = RuntimeContext(registered).run()
        self.assertEqual(old.model_dump(mode="json"), new.model_dump(mode="json"))
        self.assertEqual(new.transactions[0].end_cycles, 33)
        self.assertEqual(len(new.transactions[0].hops), 3)
        self.assertNotIn("operation_cost", new.transactions[0].model_dump())
        self.assertEqual(provider.calls, [])
        self.assertEqual(document.model_dump(mode="json"), before)

    def test_quote_once_at_compile_and_never_at_runtime(self):
        costs, provider = registry()
        plan = compile_system(spec(), operation_costs=costs)
        self.assertEqual(len(provider.calls), 1)
        restored = ImmutablePlan.model_validate_json(plan.model_dump_json())
        provider.mutate = lambda _: self.fail("runtime must not quote")
        first = RuntimeContext(restored).run()
        second = RuntimeContext(restored).run()
        self.assertEqual(first.model_dump(mode="json"), second.model_dump(mode="json"))
        self.assertEqual(len(provider.calls), 1)

    def test_explicit_conversion_and_isolated_trace(self):
        costs, _ = registry()
        result = execute(spec([operation(start_cycles=2.5)], timing_doc=[]), costs)
        span = result.transactions[0]
        self.assertEqual((span.start_cycles, span.end_cycles), (2.5, 9.5))
        self.assertEqual(span.operation_cost.duration_cycles, 7)
        self.assertEqual(span.operation_cost.quote.value, 4.25)
        self.assertEqual(span.operation_cost.conversion.conversion_ref, "test_scale")
        self.assertEqual(span.operation_cost.conversion.target, RUNTIME_CYCLE_UNIT)
        self.assertEqual(span.operation_cost.provider_ref, "constant_test")
        self.assertEqual(span.hops, ())
        self.assertIsNone(span.service)
        self.assertEqual(result.execution, "generic_operation_service")
        self.assertEqual([e.action for e in result.trace], ["operation_start", "operation_end"])
        self.assertEqual([e.time_cycles for e in result.trace], [2.5, 9.5])
        self.assertTrue(all(e.resource_id is None and e.detail == "constant_test" for e in result.trace))
        self.assertTrue(all(r.service_count == 0 and r.busy_cycles == 0 for r in result.resources))
        self.assertEqual(GenericSimulationResult.model_validate_json(result.model_dump_json()), result)

    def test_explicit_identity_cycle_conversion(self):
        contract = contract_doc()
        contract["unit"] = RUNTIME_CYCLE_UNIT.model_dump()
        conversion = conversion_doc()
        conversion.update(source=contract["unit"], numerator=1, denominator=1)
        provider = ConstantProvider(lambda quote: quote.update(unit=contract["unit"]), value=9)
        costs, _ = registry(provider, contract, conversion)
        self.assertEqual(execute(costs=costs).completion_cycles, 9)

    def test_dependencies_mix_with_legacy_transfer(self):
        costs, provider = registry()
        result = execute(spec([
            operation(), transfer("after", "eu_00", "mem_b_ep", depends_on=["op"]),
            operation("last", depends_on=["after"]),
        ]), costs)
        spans = result.transactions
        self.assertEqual([(s.start_cycles, s.end_cycles) for s in spans], [(0, 7), (7, 40), (40, 47)])
        self.assertEqual(result.execution, "generic_mixed_service")
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(len(spans[1].hops), 3)

    def test_independent_services_overlap_without_resource_claims(self):
        costs, _ = registry()
        result = execute(spec([operation(), operation("other"), {
            "transaction_id": "compute", "kind": "compute", "unit_id": "eu_00",
            "duration_cycles": 8,
        }]), costs)
        self.assertEqual([(s.start_cycles, s.end_cycles) for s in result.transactions],
                         [(0, 7), (0, 7), (0, 8)])
        unit = next(r for r in result.resources if r.resource_id == "eu_00")
        self.assertEqual((unit.service_count, unit.busy_cycles), (1, 8))

    def test_cycle_limit_does_not_forge_completion(self):
        costs, _ = registry()
        result = execute(spec(max_cycles=3), costs)
        span = result.transactions[0]
        self.assertEqual((span.status, span.reason), ("incomplete", "cycle_limit"))
        self.assertIsNone(span.start_cycles)
        self.assertIsNone(span.end_cycles)
        self.assertEqual(span.operation_cost.duration_cycles, 7)
        self.assertEqual([e.action for e in result.trace], ["operation_start", "cancel"])

    def test_unsatisfied_dependency_does_not_start_operation(self):
        costs, provider = registry()
        result = execute(spec([
            transfer("missing", "eu_00", "eu_10"),
            operation(depends_on=["missing"]),
        ]), costs)
        self.assertEqual(result.transactions[1].reason, "dependency_unsatisfied")
        self.assertFalse(any(e.action == "operation_start" for e in result.trace))
        self.assertEqual(len(provider.calls), 1)  # quote is admission, not execution

    def test_function_class_and_input_adapter_forward_registry(self):
        document = spec()

        class Adapter(GenericInputAdapter):
            def load_system_graph(self):
                return document.graph

            def load_transactions(self):
                return document.batch

        costs, provider = registry()
        direct = run_generic_batch(system(), document.batch, operation_costs=costs)
        wrapped = GenericRuntime(system(), document.batch, operation_costs=costs).run()
        adapted = run_generic_adapter(Adapter(), operation_costs=costs)
        self.assertEqual(direct, wrapped)
        self.assertEqual(direct, adapted)
        self.assertEqual(len(provider.calls), 3)


class TestOperationCostAdmission(unittest.TestCase):
    def reject_quote(self, mutate):
        costs, provider = registry(ConstantProvider(mutate))
        with patch("simulator_detailed.runtime_context.simpy.Environment") as environment:
            with self.assertRaises(ValueError):
                execute(costs=costs)
            environment.assert_not_called()
        self.assertEqual(len(provider.calls), 1)

    def test_missing_registry_and_provider(self):
        for costs in (None, OperationCostRegistry()):
            with self.subTest(costs=costs), self.assertRaises(ValueError):
                compile_system(spec(), operation_costs=costs)

    def test_provider_ref_required_and_not_implicit_on_transfer(self):
        raw = operation()
        del raw["provider_ref"]
        with self.assertRaises(ValidationError):
            spec([raw])
        with self.assertRaises(ValidationError):
            spec([transfer("old", "eu_00", "mem_b_ep", provider_ref="constant_test")])

    def test_scope_and_operation_identity(self):
        for field, value in (("scope", "per_link"), ("operation_id", "other")):
            with self.subTest(field=field):
                self.reject_quote(lambda quote: quote.update({field: value}))

    def test_quantity_and_semantic_compatibility(self):
        for field, value in (("quantity", "operation_cost"),
                             ("service_semantics", "contended_elapsed")):
            with self.subTest(field=field):
                self.reject_quote(lambda quote: quote.update({field: value}))

    def test_unknown_and_mismatched_units(self):
        for field, value in (("kind", "opaque"), ("name", "other_tick"),
                             ("clock_domain", "other_clock")):
            with self.subTest(field=field):
                self.reject_quote(lambda quote: quote["unit"].update({field: value}))

    def test_request_outside_registered_domain_does_not_quote(self):
        costs, provider = registry()
        for field in ("operation_kind", "domain_id"):
            domain = contract_doc()["domain"]
            domain[field] = "outside"
            with self.subTest(field=field), self.assertRaises(ValueError):
                compile_system(spec([operation(domain=domain)]), operation_costs=costs)
        self.assertEqual(provider.calls, [])

    def test_response_domain_drift(self):
        for field in ("operation_kind", "domain_id"):
            with self.subTest(field=field):
                self.reject_quote(lambda quote: quote["domain"].update({field: "outside"}))

    def test_provenance_identity_revision_and_evidence(self):
        for field in ("provider_ref", "revision", "evidence_ref"):
            for value in ("other", "", " "):
                with self.subTest(field=field, value=value):
                    self.reject_quote(lambda quote: quote["provenance"].update({field: value}))

    def test_missing_and_extra_quote_fields(self):
        for field in ("quantity", "scope", "unit", "domain", "provenance", "value"):
            with self.subTest(missing=field):
                self.reject_quote(lambda quote: quote.pop(field))
        for field in (None, "unit", "domain", "provenance"):
            with self.subTest(extra=field):
                self.reject_quote(lambda quote: (quote if field is None else quote[field]).update(extra=1))

    def test_bool_and_invalid_cost_values(self):
        for value in (True, False, "4", 0, -1, float("inf"), float("nan"), 10**1000):
            with self.subTest(value_type=type(value).__name__):
                self.reject_quote(lambda quote: quote.update(value=value))

    def test_extra_transaction_fields_and_bool_start(self):
        for extra in ({"extra": 1}, {"duration_cycles": 3}, {"start_cycles": True}):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                spec([operation(**extra)])

    def test_conversion_requires_explicit_fields(self):
        for field in conversion_doc():
            raw = conversion_doc()
            del raw[field]
            with self.subTest(field=field), self.assertRaises(ValidationError):
                OperationCycleConversion.model_validate(raw)
        with self.assertRaises(TypeError):
            OperationCostRegistry().register(ConstantProvider(), contract=OperationCostContract(**contract_doc()))

    def test_conversion_target_source_and_rounding(self):
        for field, value in (("kind", "time"), ("clock_domain", "other"), ("name", "tick")):
            raw = conversion_doc()
            raw["target"][field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                OperationCycleConversion.model_validate(raw)
        raw = conversion_doc()
        raw["source"]["clock_domain"] = "other"
        with self.assertRaises(ValueError):
            registry(conversion=raw)
        raw = conversion_doc()
        raw["rounding"] = "floor"
        with self.assertRaises(ValidationError):
            OperationCycleConversion.model_validate(raw)

    def test_conversion_numbers_and_extra_fields(self):
        for field in ("numerator", "denominator"):
            for value in (True, 0, -1, 1.5, "2"):
                raw = conversion_doc()
                raw[field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    OperationCycleConversion.model_validate(raw)
        for field in (None, "source", "target"):
            raw = conversion_doc()
            (raw if field is None else raw[field])["extra"] = 1
            with self.subTest(extra=field), self.assertRaises(ValidationError):
                OperationCycleConversion.model_validate(raw)

    def test_registry_freezes_contract_and_rejects_duplicate_ids(self):
        raw = contract_doc()
        contract = OperationCostContract.model_validate(raw)
        costs, provider = registry(contract=raw)
        raw["domain"]["domain_id"] = "changed"
        with self.assertRaises(ValueError):
            costs.register(provider, contract=contract,
                           conversion=OperationCycleConversion(**conversion_doc()))
        self.assertEqual(execute(costs=costs).status, "complete")

    def test_typed_quote_revalidated(self):
        class Provider(ConstantProvider):
            def quote(self, request):
                raw = super().quote(request)
                return OperationCostQuote.model_construct(**dict(raw, value=True))
        costs, _ = registry(Provider())
        with self.assertRaises(ValidationError):
            compile_system(spec(), operation_costs=costs)

    def test_converted_overflow_rejected(self):
        costs, _ = registry(ConstantProvider(value=2**53))
        with self.assertRaises(ValueError):
            compile_system(spec(), operation_costs=costs)

    def test_provider_failure_precedes_environment(self):
        def fail(_):
            raise ValueError("synthetic provider failure")
        costs, _ = registry(ConstantProvider(fail))
        with patch("simulator_detailed.runtime_context.simpy.Environment") as environment:
            with self.assertRaisesRegex(ValueError, "synthetic provider failure"):
                execute(costs=costs)
            environment.assert_not_called()

    def test_runtime_revalidates_compiled_conversion(self):
        costs, _ = registry()
        plan = compile_system(spec(), operation_costs=costs)
        raw = plan.model_dump(mode="json")
        raw["content"]["transactions"][0]["operation_cost"]["duration_cycles"] += 1
        raw["plan_sha256"] = content_digest(raw["content"])
        with patch("simulator_detailed.runtime_context.simpy.Environment") as environment:
            with self.assertRaises(ValidationError):
                RuntimeContext(plan.model_copy(update={"content": raw["content"],
                                                       "plan_sha256": raw["plan_sha256"]}))
            environment.assert_not_called()

    def test_result_cannot_claim_hops_memory_or_wrong_execution(self):
        costs, _ = registry()
        result = execute(costs=costs).model_dump(mode="json")
        legacy = execute(spec([transfer("old", "eu_00", "mem_b_ep")])).model_dump(mode="json")
        raw = deepcopy(result)
        raw["transactions"][0]["hops"] = legacy["transactions"][0]["hops"]
        with self.assertRaises(ValidationError):
            GenericSimulationResult.model_validate(raw)
        raw = deepcopy(result)
        raw["transactions"][0]["service"] = {
            "resource_id": "memory", "direction": "read", "bank_id": "bank",
            "port_id": "port", "channel_id": "channel", "command_start_cycles": 0,
            "command_end_cycles": 1, "service_start_cycles": 1, "service_end_cycles": 7,
        }
        with self.assertRaises(ValidationError):
            GenericSimulationResult.model_validate(raw)
        raw = deepcopy(result)
        raw["execution"] = "generic_packet_transport"
        with self.assertRaises(ValidationError):
            GenericSimulationResult.model_validate(raw)
        raw = deepcopy(result)
        del raw["transactions"][0]["operation_cost"]
        with self.assertRaises(ValidationError):
            GenericSimulationResult.model_validate(raw)


if __name__ == "__main__":
    unittest.main()

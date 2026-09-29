"""Explicit, in-process provider registry used only during compilation.

No discovery, imports by ID, global registry or runtime callbacks. Registered
contracts are frozen snapshots. Providers must return an isolated service
DURATION, not a timestamp, rate, aggregate cost or contended execution time.
"""
from __future__ import annotations

from typing import Protocol

from .configs.schemas.operation_cost import (
    CompiledOperationCost,
    OperationCostContract,
    OperationCostQuote,
    OperationCostRequest,
    OperationCycleConversion,
)


class OperationCostProvider(Protocol):
    def quote(self, request: OperationCostRequest) -> OperationCostQuote | dict:
        """Return one quote for the complete requested operation."""
        ...


class OperationCostRegistry:
    """Bootstrap registration by ID; no callbacks until explicit compilation.

    Semantic adaptation is deliberately limited to isolated_service_duration
    -> isolated_completion with no shared-resource occupancy. No arbitrary
    plugin security or concurrent registration guarantees are provided.
    """

    def __init__(self) -> None:
        self._bindings: dict[str, tuple[
            OperationCostProvider, OperationCostContract, OperationCycleConversion,
        ]] = {}

    def register(
        self,
        provider: OperationCostProvider,
        *,
        contract: OperationCostContract,
        conversion: OperationCycleConversion,
    ) -> None:
        contract = OperationCostContract.model_validate(contract)
        conversion = OperationCycleConversion.model_validate(conversion)
        ref = contract.provenance.provider_ref
        if ref in self._bindings:
            raise ValueError(f"operation cost provider already registered: {ref}")
        if contract.unit != conversion.source:
            raise ValueError("registered unit does not match conversion source")
        self._bindings[ref] = provider, contract, conversion

    def compile(self, provider_ref: str, request: OperationCostRequest) -> CompiledOperationCost:
        try:
            provider, contract, conversion = self._bindings[provider_ref]
        except KeyError:
            raise ValueError(f"operation cost provider is not registered: {provider_ref}") from None
        request = OperationCostRequest.model_validate(request)
        if request.domain != contract.domain:
            raise ValueError("operation is outside the registered provider domain")
        raw = provider.quote(request)
        quote = OperationCostQuote.model_validate(raw)
        if quote.operation_id != request.operation_id:
            raise ValueError("quote scope does not match the requested operation")
        actual = OperationCostContract.model_validate({
            name: getattr(quote, name) for name in OperationCostContract.model_fields
        })
        if actual != contract:
            raise ValueError("quote does not match the registered cost contract")
        return CompiledOperationCost(
            provider_ref=provider_ref, quote=quote, conversion=conversion,
            duration_cycles=conversion.cycles(quote.value), semantics="isolated_completion",
        )

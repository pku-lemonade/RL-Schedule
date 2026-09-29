"""Phase-1 compatibility shims over the unified pipeline.

`run_generic_batch` and `GenericRuntime` keep their phase-1 signatures but
now delegate to `compile_system` + `RuntimeContext`; no second runtime path
exists. Numeric semantics, classifications and digests are unchanged.
"""

from __future__ import annotations

from .configs.schemas.generic_transactions import (
    GenericSimulationResult,
    GenericTransactionBatch,
)
from .configs.schemas.system_spec import SystemSpec
from .generic_graph import GenericSystem
from .operation_cost import OperationCostRegistry
from .runtime_context import RuntimeContext
from .system_compile import compile_system


def run_generic_batch(
    system: GenericSystem, batch: GenericTransactionBatch,
    *, operation_costs: OperationCostRegistry | None = None,
) -> GenericSimulationResult:
    """Compile the pair as one SystemSpec and execute it; errors precede simulation."""
    spec = SystemSpec(
        kind="system_spec",
        schema_version=1,
        spec_id=batch.batch_id,
        graph=system.document,
        batch=batch,
    )
    return RuntimeContext(compile_system(spec, operation_costs=operation_costs)).run()


class GenericRuntime:
    """Phase-1 class API retained as a shim over the unified pipeline."""

    def __init__(
        self, system: GenericSystem, batch: GenericTransactionBatch,
        *, operation_costs: OperationCostRegistry | None = None,
    ):
        self.operation_costs = operation_costs
        self.system = system
        self.batch = GenericTransactionBatch.model_validate(batch.model_dump(mode="json"))

    def run(self) -> GenericSimulationResult:
        return run_generic_batch(self.system, self.batch, operation_costs=self.operation_costs)

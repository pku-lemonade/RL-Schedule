"""Pure configurable FC/matmul normalization, storage and effective costs."""

from __future__ import annotations

import math
from collections.abc import Mapping
from fractions import Fraction

from .compute_records import ComputeCost, MatrixDimensions, TensorFootprint
from .configs.schemas.compute_workload import (
    ComputeOperation,
    ComputeRate,
    ComputeRateKey,
    ComputeTensor,
    StorageDtype,
)


def normalize_operation(operation: ComputeOperation) -> MatrixDimensions:
    operation = ComputeOperation.model_validate(operation.model_dump(mode="python"))
    if len(operation.a.shape) != 3 or len(operation.c.shape) != 3:
        raise ValueError("A and C require explicit [batch, rows, columns] shapes")
    batch, m, k = operation.a.shape
    output_batch, output_m, n = operation.c.shape
    expected_b = (k, n) if operation.shared_weights else (batch, k, n)
    if (output_batch, output_m) != (batch, m) or operation.b.shape != expected_b:
        raise ValueError("matmul/FC shapes disagree; implicit broadcasting is unsupported")
    if operation.a.layout != operation.b.layout or operation.a.layout != operation.c.layout:
        raise ValueError("compute requires compatible layouts without implicit conversion")
    return MatrixDimensions(batch=batch, m=m, n=n, k=k)


def rate_key(operation: ComputeOperation) -> ComputeRateKey:
    return ComputeRateKey(operation=operation.kind, a_dtype=operation.a.dtype,
                          b_dtype=operation.b.dtype, c_dtype=operation.c.dtype,
                          accumulator_precision=operation.accumulator_precision,
                          layout=operation.a.layout, fidelity=operation.fidelity)


def _round_up(value: int, quantum: int) -> int:
    return ((value + quantum - 1) // quantum) * quantum


def tensor_footprint(tensor: ComputeTensor, dtype: StorageDtype) -> TensorFootprint:
    tensor = ComputeTensor.model_validate(tensor.model_dump(mode="python"))
    dtype = StorageDtype.model_validate(dtype.model_dump(mode="python"))
    if tensor.dtype != dtype.dtype_id:
        raise ValueError("tensor storage dtype differs from its definition")
    batch = math.prod(tensor.shape[:-2])
    rows, columns = tensor.shape[-2:]
    useful = batch * rows * columns * dtype.bytes_per_element
    if tensor.layout.kind == "tiled":
        rows = _round_up(rows, tensor.layout.tile_rows)
        columns = _round_up(columns, tensor.layout.tile_columns)
    return TensorFootprint(useful_bytes=useful, storage_bytes=batch * rows * columns * dtype.bytes_per_element)


def checked_compute_finish(start_aci_cycles: float, duration_aci_cycles: float) -> float:
    """A finite positive service must advance the actual runtime clock."""
    if not math.isfinite(start_aci_cycles) or start_aci_cycles < 0:
        raise ValueError("invalid compute start time")
    if not math.isfinite(duration_aci_cycles) or duration_aci_cycles <= 0:
        raise ValueError("unrepresentable compute duration")
    finish = start_aci_cycles + duration_aci_cycles
    if not math.isfinite(finish) or finish <= start_aci_cycles:
        raise ValueError("compute duration cannot advance the simulation clock")
    return finish


def compute_cost(operation: ComputeOperation, rate: ComputeRate, dtypes: Mapping[str, StorageDtype],
                 *, native_clock_hz: float, aci_clock_hz: float) -> ComputeCost:
    operation = ComputeOperation.model_validate(operation.model_dump(mode="python"))
    dims = normalize_operation(operation)
    rate = ComputeRate.model_validate(rate.model_dump(mode="python"))
    if rate.key != rate_key(operation):
        raise ValueError("no exact effective compute rate for operation/dtype/layout/precision/fidelity")
    if any(not math.isfinite(v) or v <= 0 for v in (native_clock_hz, aci_clock_hz)):
        raise ValueError("compute clocks must be positive and finite")
    try:
        footprints = tuple(tensor_footprint(t, dtypes[t.dtype]) for t in (operation.a, operation.b, operation.c))
    except KeyError as exc:
        raise ValueError("undefined storage dtype") from exc
    useful = 2 * dims.batch * dims.m * dims.n * dims.k
    executed = (2 * dims.batch * _round_up(dims.m, rate.block.m)
                * _round_up(dims.n, rate.block.n) * _round_up(dims.k, rate.block.k))
    # Exact decimal arithmetic avoids float overflow before division and ceil
    # errors at quantum boundaries. Only the final SimPy durations become floats.
    quantum = Fraction(str(rate.quantum_native_cycles))
    quanta = math.ceil(Fraction(executed) / (Fraction(str(rate.work_per_native_cycle)) * quantum))
    native = Fraction(str(rate.setup_native_cycles)) + max(Fraction(str(rate.minimum_native_cycles)), quanta * quantum)
    aci = native * Fraction(str(aci_clock_hz)) / Fraction(str(native_clock_hz))
    try:
        native_float, aci_float = float(native), float(aci)
    except OverflowError as exc:
        raise ValueError("unrepresentable compute duration") from exc
    checked_compute_finish(0.0, native_float)
    checked_compute_finish(0.0, aci_float)
    return ComputeCost(rate_id=rate.rate_id, dimensions=dims, useful_work=useful, executed_work=executed,
                       a=footprints[0], b=footprints[1], c=footprints[2],
                       service_native_cycles=native_float, service_aci_cycles=aci_float)

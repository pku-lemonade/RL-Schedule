from __future__ import annotations

from enum import IntEnum, auto
from functools import reduce
from operator import mul
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _ceil(a: int, b: int) -> int:
    return (a + b - 1) // b


class ElementType(IntEnum):
    INT8 = auto()
    INT16 = auto()
    INT32 = auto()
    FP8_E4M3 = auto()
    FP16 = auto()
    BF16 = auto()
    FP32 = auto()
    TF32 = auto()
    INT64 = auto()
    FP8_E5M2 = auto()
    UINT4 = auto()
    UINT4X2 = auto()


ELEM_BITS = {
    ElementType.INT8: 8,
    ElementType.INT16: 16,
    ElementType.INT32: 32,
    ElementType.FP8_E4M3: 8,
    ElementType.FP16: 16,
    ElementType.BF16: 16,
    ElementType.FP32: 32,
    ElementType.TF32: 32,
    ElementType.INT64: 64,
    ElementType.FP8_E5M2: 8,
    ElementType.UINT4: 4,
    ElementType.UINT4X2: 8,
}


class ReorderMode(IntEnum):
    NONE = auto()
    BMM = auto()
    CONV2D = auto()
    AXIS_INTERLEAVE = auto()
    CONV3D = auto()


class MaskAxis(BaseModel):
    axis: int
    num: int


class AxisAlignment(BaseModel):
    base_axis_first: int = -1
    unalign_axis_first: int = -1
    num_first: int = 0
    base_axis_second: int = -1
    unalign_axis_second: int = -1
    num_second: int = 0


class GatherScatter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    table_addr: int = 0
    addr_offset: int = 0
    num_entries: int = 0
    row_bytes: int = 0
    per_row_overhead: int = 0
    entry_bytes: int = Field(default=1, gt=0)

    @field_validator("num_entries", "row_bytes", "per_row_overhead")
    @classmethod
    def _nonneg(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"gather field must be non-negative, got {v}")
        return v


class TensorLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loop_cnt: List[int] = []
    loop_stride: List[int] = []
    dtype: int = ElementType.INT8
    mask_first: Optional[MaskAxis] = None
    mask_last: Optional[MaskAxis] = None
    pad_value: int = 0
    alignment: Optional[AxisAlignment] = None
    transpose: bool = False
    reorder: int = ReorderMode.NONE
    transpose_overhead: int = 0
    gather: Optional[GatherScatter] = None
    is_global: bool = False
    max_dimensions: int | None = Field(default=None, ge=0)
    pad_value_bits: int | None = Field(default=None, gt=0)

    @field_validator("loop_cnt", "loop_stride")
    @classmethod
    def _list_nonneg(cls, v: List[int]) -> List[int]:
        for x in v:
            if x < 0:
                raise ValueError(f"loop counts/strides must be non-negative, got {x}")
        return v

    @field_validator("dtype")
    @classmethod
    def _check_dtype(cls, v: int) -> int:
        if v not in ELEM_BITS:
            raise ValueError(f"unknown dtype {v}")
        return v

    @field_validator("reorder")
    @classmethod
    def _check_reorder(cls, v: int) -> int:
        if v not in tuple(r.value for r in ReorderMode):
            raise ValueError(f"unknown reorder mode {v}")
        return v

    @field_validator("transpose_overhead")
    @classmethod
    def _check_overhead(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"transpose_overhead must be non-negative, got {v}")
        return v

    @field_validator("pad_value")
    @classmethod
    def _check_pad(cls, v: int) -> int:
        if v < 0:
            raise ValueError("pad_value must be nonnegative")
        return v

    @model_validator(mode="after")
    def _check_layout(self) -> "TensorLayout":
        if len(self.loop_cnt) != len(self.loop_stride):
            raise ValueError(
                f"loop_cnt and loop_stride length mismatch: "
                f"{len(self.loop_cnt)} vs {len(self.loop_stride)}"
            )
        ndim = len(self.loop_cnt)
        max_ndim = self.max_dimensions
        if max_ndim is not None and ndim > max_ndim:
            raise ValueError(
                f"ndim {ndim} exceeds max {max_ndim} for "
                f"{'global' if self.is_global else 'local'} side"
            )
        if self.pad_value_bits is not None and (
            self.pad_value_bits <= 0 or self.pad_value >= (1 << self.pad_value_bits)
        ):
            raise ValueError("pad_value exceeds configured width")
        for c in self.loop_cnt:
            if c < 1:
                raise ValueError(f"loop count must be >= 1, got {c}")

        elem_bits = ELEM_BITS[self.dtype]

        def validate_mask(m: Optional[MaskAxis], name: str):
            if m is None:
                return
            if m.axis < 0 or m.axis >= ndim:
                raise ValueError(f"{name}.axis {m.axis} out of range for ndim {ndim}")
            if m.num < 0:
                raise ValueError(f"{name}.num must be non-negative, got {m.num}")
            if m.num > self.loop_cnt[m.axis]:
                raise ValueError(
                    f"{name}.num {m.num} > cnt[{m.axis}] = {self.loop_cnt[m.axis]}"
                )

        validate_mask(self.mask_first, "mask_first")
        validate_mask(self.mask_last, "mask_last")
        if (
            self.mask_first is not None
            and self.mask_last is not None
            and self.mask_first.axis == self.mask_last.axis
        ):
            axis = self.mask_first.axis
            total = self.mask_first.num + self.mask_last.num
            if total > self.loop_cnt[axis]:
                raise ValueError(
                    f"mask first+last num {total} > cnt[{axis}] "
                    f"= {self.loop_cnt[axis]} on shared axis"
                )

        if self.alignment is not None:
            for name in (
                "base_axis_first",
                "unalign_axis_first",
                "base_axis_second",
                "unalign_axis_second",
            ):
                a = getattr(self.alignment, name)
                if a != -1 and (a < 0 or a >= ndim):
                    raise ValueError(
                        f"alignment.{name} {a} out of range for ndim {ndim}"
                    )
            for name in ("num_first", "num_second"):
                if getattr(self.alignment, name) < 0:
                    raise ValueError(f"alignment.{name} must be non-negative")

        if ndim == 0 and elem_bits < 8:
            raise ValueError("sub-byte dtype requires at least one dimension")
        return self

    def ndim(self) -> int:
        return len(self.loop_cnt)

    def elem_bits(self) -> int:
        return ELEM_BITS[self.dtype]

    def element_count_raw(self) -> int:
        if not self.loop_cnt:
            return 0
        return reduce(mul, self.loop_cnt, 1)

    def _removed_elements(self) -> int:
        removed = 0
        if self.mask_first is not None:
            removed += self._mask_scalar_count(self.mask_first)
        if self.mask_last is not None:
            removed += self._mask_scalar_count(self.mask_last)
        return removed

    def _mask_scalar_count(self, m: MaskAxis) -> int:
        inner = 1
        for j in range(m.axis):
            inner *= self.loop_cnt[j]
        return m.num * inner

    def effective_element_count(self) -> int:
        if self.gather is not None:
            return 0
        return self.element_count_raw() - self._removed_elements()

    def payload_bytes(self) -> int:
        if self.gather is not None:
            return self.gather.num_entries * self.gather.row_bytes
        bits = self.elem_bits()
        return _ceil(self.effective_element_count() * bits, 8)

    def footprint_bytes(self) -> int:
        if not self.loop_cnt:
            return 0
        if self.gather is not None:
            return (
                self.gather.num_entries * self.gather.entry_bytes
                + self.gather.num_entries * self.gather.row_bytes
            )
        bits = self.elem_bits()
        elem_bytes = _ceil(bits, 8)
        if bits < 8:
            inner = _ceil(self.loop_cnt[0] * bits, 8)
        else:
            inner = (self.loop_cnt[0] - 1) * self.loop_stride[0] + elem_bytes
        outer = 0
        for i in range(1, len(self.loop_cnt)):
            outer += (self.loop_cnt[i] - 1) * self.loop_stride[i]
        return inner + outer

    def endpoint_overhead_cycles(self) -> int:
        cycles = 0
        if self.transpose:
            cycles += self.transpose_overhead
        if self.gather is not None:
            cycles += self.gather.num_entries * self.gather.per_row_overhead
        return cycles

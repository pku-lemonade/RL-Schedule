"""Configuration-based simulator regressions with synthetic data."""

import unittest
from pydantic import ValidationError
from profiling_sim.layout import ElementType, GatherScatter, MaskAxis, TensorLayout


class LayoutTests(unittest.TestCase):
    def test_element_widths_and_masks(self):
        for dtype, width in (
            (ElementType.INT8, 8),
            (ElementType.INT16, 16),
            (ElementType.UINT4, 4),
        ):
            layout = TensorLayout(
                dtype=dtype,
                loop_cnt=[6],
                loop_stride=[1],
                mask_first=MaskAxis(axis=0, num=2),
            )
            self.assertEqual(layout.payload_bytes(), 4 * width // 8)

    def test_layout_limits_are_explicit(self):
        layout = TensorLayout(
            loop_cnt=[1] * 13, loop_stride=[1] * 13, pad_value=1 << 40
        )
        self.assertEqual(layout.ndim(), 13)
        with self.assertRaises(ValidationError):
            TensorLayout(loop_cnt=[1] * 5, loop_stride=[1] * 5, max_dimensions=4)
        with self.assertRaises(ValidationError):
            TensorLayout(pad_value=17, pad_value_bits=4)

    def test_gather_entry_width_is_configured(self):
        for width in (3, 7):
            layout = TensorLayout(
                loop_cnt=[1],
                loop_stride=[1],
                gather=GatherScatter(num_entries=5, row_bytes=9, entry_bytes=width),
            )
            self.assertEqual(layout.payload_bytes(), 45)
            self.assertEqual(layout.footprint_bytes(), 45 + 5 * width)

    def test_malformed_layout_is_rejected(self):
        for fields in (
            {"loop_cnt": [1], "loop_stride": []},
            {"loop_cnt": [0], "loop_stride": [1]},
        ):
            with self.assertRaises(ValidationError):
                TensorLayout(**fields)

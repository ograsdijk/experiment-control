"""Resolution of declared stream axes against run metadata."""

from __future__ import annotations

import unittest

import numpy as np

from experiment_control.stream_axis import (
    IDENTITY_AXIS,
    axis_values,
    resolve_stream_axis,
)
from experiment_control.types import StreamAxis

PXIE_META = {
    "sample_rate_hz": 100000.0,
    "trigger_delay_s": 0.8e-3,
    "samples_per_record": 4096,
}


class StreamAxisDeclarationTests(unittest.TestCase):
    def test_rejects_two_increment_sources(self) -> None:
        with self.assertRaises(ValueError):
            StreamAxis(increment=1e-5, rate_from="sample_rate_hz")

    def test_rejects_two_origin_sources(self) -> None:
        with self.assertRaises(ValueError):
            StreamAxis(origin=0.0, origin_from="trigger_delay_s")

    def test_rejects_zero_and_nonfinite_increment(self) -> None:
        with self.assertRaises(ValueError):
            StreamAxis(increment=0.0)
        with self.assertRaises(ValueError):
            StreamAxis(increment=float("inf"))

    def test_blank_strings_normalize_to_none(self) -> None:
        axis = StreamAxis(units="  ", label="   s  ")
        self.assertIsNone(axis.units)
        self.assertEqual(axis.label, "s")

    def test_metadata_keys_lists_declared_pointers(self) -> None:
        axis = StreamAxis(rate_from="sample_rate_hz", origin_from="trigger_delay_s")
        self.assertEqual(
            sorted(axis.metadata_keys()), ["sample_rate_hz", "trigger_delay_s"]
        )
        self.assertEqual(StreamAxis(units="s").metadata_keys(), ())


class ResolveStreamAxisTests(unittest.TestCase):
    def test_undeclared_axis_is_identity(self) -> None:
        self.assertIs(resolve_stream_axis(None, PXIE_META), IDENTITY_AXIS)

    def test_rate_from_inverts_to_sample_period(self) -> None:
        axis = StreamAxis(
            units="s",
            label="time since trigger",
            rate_from="sample_rate_hz",
            origin_from="trigger_delay_s",
        )
        resolved = resolve_stream_axis(axis, PXIE_META)
        self.assertEqual(resolved.source, "run_metadata")
        self.assertIsNone(resolved.error)
        self.assertAlmostEqual(resolved.increment, 1e-5)
        self.assertAlmostEqual(resolved.origin, 0.8e-3)
        self.assertEqual(resolved.units, "s")
        self.assertEqual(resolved.label, "time since trigger")

    def test_increment_from_is_used_directly(self) -> None:
        axis = StreamAxis(units="s", increment_from="dt")
        resolved = resolve_stream_axis(axis, {"dt": 2.5e-6})
        self.assertAlmostEqual(resolved.increment, 2.5e-6)
        self.assertEqual(resolved.source, "run_metadata")

    def test_static_increment_needs_no_metadata(self) -> None:
        axis = StreamAxis(units="s", increment=1e-5, origin=1.0)
        resolved = resolve_stream_axis(axis, None)
        self.assertEqual(resolved.source, "declared")
        self.assertAlmostEqual(resolved.increment, 1e-5)
        self.assertAlmostEqual(resolved.origin, 1.0)

    def test_missing_key_degrades_to_identity_with_error(self) -> None:
        axis = StreamAxis(units="s", rate_from="absent")
        resolved = resolve_stream_axis(axis, PXIE_META)
        self.assertEqual(resolved.source, "unresolved")
        self.assertEqual(resolved.increment, 1.0)
        self.assertEqual(resolved.origin, 0.0)
        self.assertIsNotNone(resolved.error)
        self.assertIn("absent", str(resolved.error))

    def test_non_positive_or_nonfinite_rate_is_rejected(self) -> None:
        axis = StreamAxis(units="s", rate_from="rate")
        for bad in (0.0, -100.0, float("nan"), float("inf"), None, "fast", True):
            resolved = resolve_stream_axis(axis, {"rate": bad})
            self.assertEqual(resolved.source, "unresolved", msg=repr(bad))

    def test_missing_origin_defaults_to_zero_but_keeps_increment(self) -> None:
        axis = StreamAxis(
            units="s", rate_from="sample_rate_hz", origin_from="absent"
        )
        resolved = resolve_stream_axis(axis, PXIE_META)
        self.assertAlmostEqual(resolved.increment, 1e-5)
        self.assertEqual(resolved.origin, 0.0)
        self.assertIsNotNone(resolved.error)

    def test_empty_metadata_does_not_raise(self) -> None:
        axis = StreamAxis(units="s", rate_from="sample_rate_hz")
        self.assertEqual(resolve_stream_axis(axis, {}).source, "unresolved")
        self.assertEqual(resolve_stream_axis(axis, None).source, "unresolved")


class AxisValuesTests(unittest.TestCase):
    def test_identity_axis_reproduces_sample_index(self) -> None:
        values = axis_values(IDENTITY_AXIS, 5)
        np.testing.assert_allclose(values, np.arange(5, dtype=np.float64))

    def test_scaled_axis(self) -> None:
        axis = resolve_stream_axis(
            StreamAxis(
                units="s", rate_from="sample_rate_hz", origin_from="trigger_delay_s"
            ),
            PXIE_META,
        )
        values = axis_values(axis, 3)
        np.testing.assert_allclose(values, [0.8e-3, 0.81e-3, 0.82e-3])

    def test_zero_and_negative_lengths_are_empty(self) -> None:
        self.assertEqual(axis_values(IDENTITY_AXIS, 0).size, 0)
        self.assertEqual(axis_values(IDENTITY_AXIS, -3).size, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

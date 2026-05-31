# ruff: noqa: E402
"""Regression tests for the Group G fixes.

Three independent fixes are pinned here:

1. SEM with n=1 returns NaN (not 0). Previously `std/sqrt(1)` evaluated
   to 0/1=0, which falsely communicated "perfectly known mean" to
   downstream consumers (UI error bars, fit weights). Both the 1D
   BinStatsState and the 2D BinStatsState2D variants are covered.

2. SequencerRuntime._resolve_value raises on failed device call from
   the `{"call": ...}` form. Previously it silently returned the
   `resp.get("result")` (typically None) on failure, corrupting any
   sequencer step that consumed the value. Mirrors the
   `_sample_adaptive_call` pattern at runtime.py:1960.

3. shm_ring.ShmRingWriter.create() FileExistsError recovery uses
   try/except (not try/finally) so a failed close() doesn't prevent
   the subsequent unlink(), and a failed unlink() doesn't prevent the
   retry SharedMemory(create=True).
"""

from __future__ import annotations

import math
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.stream_analysis import (
    Bin2DStatsState,
    BinStatsState,
)
from experiment_control.sequencer.runtime import SequencerRuntime
from experiment_control.shm.shm_ring import ShmRingWriter


# ---------------------------------------------------------------------------
# G.27 — SEM with n=1 returns NaN, not 0
# ---------------------------------------------------------------------------


def _sem_is_undefined(value: object) -> bool:
    """`_sanitize_json` converts numpy NaN to Python None for the wire
    payload, while raw `payload(last_sample=...)` returns numpy NaN
    via `.tolist()`. Treat both as 'undefined'."""
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return False


class BinStatsSemAtNEqualOneTests(unittest.TestCase):
    def test_1d_sem_nan_for_single_sample_bins(self) -> None:
        state = BinStatsState.from_params(
            {"auto_range": True, "bin_count": 3}
        )
        # Three distinct x values, one sample each -> all bins have n=1.
        state.update_sample(0.0, 10.0)
        state.update_sample(1.0, 20.0)
        state.update_sample(2.0, 30.0)

        payload = state.payload(last_sample=None)
        self.assertEqual(payload["count"], [1, 1, 1])
        self.assertEqual(payload["std"], [0.0, 0.0, 0.0])
        for value in payload["sem"]:
            self.assertTrue(
                _sem_is_undefined(value),
                f"n=1 bin must report undefined SEM, got {value!r}",
            )

    def test_1d_sem_finite_for_multi_sample_bins(self) -> None:
        state = BinStatsState.from_params(
            {"auto_range": True, "bin_count": 2}
        )
        # Two samples in the same bin -> SEM well-defined.
        state.update_sample(1.0, 10.0)
        state.update_sample(1.0, 14.0)
        # Single sample in second bin -> SEM undefined for that bin.
        state.update_sample(2.0, 30.0)

        payload = state.payload(last_sample=None)
        self.assertEqual(payload["count"], [2, 1])
        sem = payload["sem"]
        # First bin (n=2) must be finite.
        self.assertIsNotNone(sem[0])
        self.assertIsInstance(sem[0], float)
        self.assertFalse(math.isnan(sem[0]))
        self.assertGreater(sem[0], 0.0)
        # Second bin (n=1) must be undefined.
        self.assertTrue(_sem_is_undefined(sem[1]))

    def test_1d_sem_undefined_for_empty_bins(self) -> None:
        # Auto-range start: no samples yet -> active_bin_count==0.
        # Drive a fixed-range state with explicit edges so we can have
        # populated AND empty bins coexist.
        state = BinStatsState.from_params(
            {
                "auto_range": False,
                "x_min": 0.0,
                "x_max": 4.0,
                "bin_count": 4,
            }
        )
        state.update_sample(0.5, 10.0)  # bin 0
        state.update_sample(2.5, 20.0)  # bin 2 (bins 1 and 3 empty)
        payload = state.payload(last_sample=None)
        self.assertEqual(payload["count"], [1, 0, 1, 0])
        sem = payload["sem"]
        # Empty bins have no samples — SEM undefined (was already NaN
        # before this fix; verify the fix didn't regress this).
        for value in sem:
            self.assertTrue(_sem_is_undefined(value), f"got {value!r}")

    def test_2d_sem_nan_for_single_sample_bins(self) -> None:
        state = Bin2DStatsState.from_params(
            {
                "x_auto_range": False,
                "x_min": 0.0,
                "x_max": 2.0,
                "x_bin_count": 2,
                "y_auto_range": False,
                "y_min": 0.0,
                "y_max": 2.0,
                "y_bin_count": 2,
            }
        )
        # Single sample per bin across the grid.
        state.update_sample(0.5, 0.5, 10.0)
        state.update_sample(1.5, 0.5, 20.0)
        state.update_sample(0.5, 1.5, 30.0)
        state.update_sample(1.5, 1.5, 40.0)

        payload = state.payload(last_sample=None)
        sem_2d = payload["sem"]
        # 2x2 grid, all n=1.
        for row in sem_2d:
            for value in row:
                self.assertTrue(
                    _sem_is_undefined(value),
                    f"2D n=1 bin must report undefined SEM, got {value!r}",
                )


# ---------------------------------------------------------------------------
# G.28 — sequencer _resolve_value raises on failed {"call": ...}
# ---------------------------------------------------------------------------


def _make_runtime_with_call(
    call_impl,
) -> SequencerRuntime:
    return SequencerRuntime(
        call_device=call_impl,
        get_telemetry=lambda _d, _s: None,
        set_stream_context=lambda *_args, **_kwargs: None,
    )


class SequencerResolveValueCallTests(unittest.TestCase):
    def test_failed_call_raises_runtime_error(self) -> None:
        def _failing_call(_device, _action, _params):
            return {"ok": False, "error": "device offline"}

        runtime = _make_runtime_with_call(_failing_call)
        with self.assertRaises(RuntimeError) as ctx:
            runtime._resolve_value(  # noqa: SLF001
                {"call": {"device": "psu", "action": "read_v", "params": {}}}
            )
        msg = str(ctx.exception)
        self.assertIn("psu.read_v", msg)
        self.assertIn("device offline", msg)

    def test_successful_call_returns_result(self) -> None:
        def _ok_call(_device, _action, _params):
            return {"ok": True, "result": 42.0}

        runtime = _make_runtime_with_call(_ok_call)
        value = runtime._resolve_value(  # noqa: SLF001
            {"call": {"device": "psu", "action": "read_v", "params": {}}}
        )
        self.assertEqual(value, 42.0)

    def test_successful_call_with_extract_returns_extracted(self) -> None:
        def _ok_call(_device, _action, _params):
            return {"ok": True, "result": {"voltage": 3.14, "current": 0.5}}

        runtime = _make_runtime_with_call(_ok_call)
        value = runtime._resolve_value(  # noqa: SLF001
            {
                "call": {
                    "device": "psu",
                    "action": "status",
                    "params": {},
                    "extract": {"kind": "key", "ref": "voltage"},
                }
            }
        )
        self.assertEqual(value, 3.14)

    def test_failed_call_with_extract_still_raises(self) -> None:
        # Pre-fix behaviour: the extract logic would silently
        # short-circuit because `resp.get("result")` was None and the
        # extractor returned None, masking the call failure entirely.
        def _failing_call(_device, _action, _params):
            return {"ok": False, "error": {"code": "timeout"}}

        runtime = _make_runtime_with_call(_failing_call)
        with self.assertRaises(RuntimeError):
            runtime._resolve_value(  # noqa: SLF001
                {
                    "call": {
                        "device": "psu",
                        "action": "read_v",
                        "params": {},
                        "extract": {"kind": "key", "ref": "voltage"},
                    }
                }
            )


# ---------------------------------------------------------------------------
# G.30 — shm_ring FileExistsError recovery uses try/except
# ---------------------------------------------------------------------------


class ShmRingFileExistsRecoveryTests(unittest.TestCase):
    def test_creating_over_stale_segment_reclaims_name(self) -> None:
        # Live happy-path roundtrip: create, simulate "stale" by leaving
        # the segment alive, recreate with the same name. The FileExistsError
        # path must succeed via the recovery branch.
        name = f"ec_test_g30_{uuid.uuid4().hex}"
        dtype = np.dtype([("seq", "u8"), ("value", "f8")])
        first = ShmRingWriter.create(
            name=name,
            dtype=dtype,
            shape=(),
            slot_count=4,
            layout_version=1,
        )
        try:
            # Don't unlink — simulate the "previous run crashed without
            # unlinking" scenario by leaving the segment behind.
            first.close()

            # Recreate with the same name. Pre-fix the try/finally
            # ordering meant a flaky close()/unlink() could swallow
            # state; with the fix we expect a clean recreate.
            second = ShmRingWriter.create(
                name=name,
                dtype=dtype,
                shape=(),
                slot_count=4,
                layout_version=1,
            )
            try:
                # Sanity: the new writer can write to a fresh segment.
                arr = np.asarray((1, 7.0), dtype=dtype).reshape(())
                seq = second.write(arr, t0_mono_ns=0, t0_wall_ns=0)
                self.assertEqual(seq, 1)
            finally:
                second.close()
                second.unlink()
        finally:
            # Ensure cleanup; ignore errors if already unlinked.
            try:
                first.unlink()
            except FileNotFoundError:
                pass
            except Exception:
                pass

    def test_recovery_proceeds_when_stale_close_raises(self) -> None:
        # The previous try/finally swallowed exceptions from close(),
        # but if close() raised AFTER unlink() the recovery would still
        # fall through with the stale segment lingering. The new
        # try/except wraps each step independently. Simulate with mock.
        name = f"ec_test_g30_close_{uuid.uuid4().hex}"
        dtype = np.dtype([("v", "f8")])

        # Build a real segment so the FileExistsError path is entered.
        seed = ShmRingWriter.create(
            name=name,
            dtype=dtype,
            shape=(),
            slot_count=2,
            layout_version=1,
        )
        try:
            seed.close()  # leave the OS segment behind so create() will
                          # hit FileExistsError below

            # Patch SharedMemory so that the "stale" handle's close()
            # raises but unlink() still runs. The retry create=True
            # below must succeed.
            from multiprocessing import shared_memory as sm_mod
            original_close = sm_mod.SharedMemory.close
            close_calls = {"n": 0}

            def _flaky_close(self):
                close_calls["n"] += 1
                # Only fail for the recovery-path close (the second
                # close on this name), which corresponds to the stale
                # handle. In practice close_calls["n"] >= 1 on the
                # recovery path; raise unconditionally for the test.
                raise OSError("simulated close failure")

            with mock.patch.object(sm_mod.SharedMemory, "close", _flaky_close):
                # This MUST succeed despite the simulated close failure
                # in the recovery path.
                recovered = ShmRingWriter.create(
                    name=name,
                    dtype=dtype,
                    shape=(),
                    slot_count=2,
                    layout_version=1,
                )
            # Restore real close for teardown.
            sm_mod.SharedMemory.close = original_close
            try:
                self.assertEqual(recovered.layout.slot_count, 2)
            finally:
                recovered.close()
                recovered.unlink()
        finally:
            try:
                seed.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()

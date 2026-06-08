# ruff: noqa: E402
"""Regression tests for HdfWriter lock-coverage contract.

The bg flush thread takes `_h5_lock` for the full write cycle. Every
helper that mutates `_h5`, a cached dataset handle, or stream-state
read by the bg thread MUST be invoked with `_h5_lock` held. Prior to
this PR, three main-thread paths bypassed the lock:

- _append_measurement_note_row (RPC handler -> ds.resize + ds[old] = row)
- _rpc_hdf_devices_toggle      (RPC handler -> self._h5.attrs[...] = ...)
- _configure_active_file       (file rotate/start -> self._h5 = h5)

These tests pin the new contract by:

1. Asserting that the locked entry points (e.g. _append_measurement_note_row)
   actually hold the lock during the dataset mutation, using a sentinel
   that detects whether _h5_lock is held inside the mutation.

2. Asserting that _assert_h5_locked() fires when invoked from a thread
   that doesn't hold the lock, but ONLY while the bg flush thread is
   alive (production gating; single-threaded test code doesn't need the
   lock).

3. Asserting that HdfWriter.close() now calls super().close(), which
   was previously skipped (leaking ManagerClient + heartbeat sockets).

4. Asserting that _rotate_file cleans up a freshly-created .h5 file
   from disk if _configure_active_file raises mid-rotation.
"""

import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import h5py

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.hdf_writer import HdfWriter


def _make_minimal_writer(td: str) -> HdfWriter:
    return HdfWriter(
        out_dir=td,
        filename=None,
        manager_rpc="tcp://127.0.0.1:65551",
        manager_pub="tcp://127.0.0.1:65552",
        rpc_timeout_ms=2000,
        timezone="America/Chicago",
        rcvhwm=1000,
        write_every_s=1.0,
        buffer_max_messages=1000,
        flush_every_n=10,
        flush_every_s=1.0,
        disabled_devices=[],
        bg_join_timeout_s=0.5,
    )


class AssertH5LockedGatesOnBgThreadAliveTests(unittest.TestCase):
    """The assertion only fires when the bg thread is actually alive."""

    def test_assertion_does_not_fire_when_bg_thread_never_started(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            writer = _make_minimal_writer(td)
            # No bg thread, no lock held; assertion must NOT fire because
            # single-threaded code paths don't need the lock.
            writer._assert_h5_locked()  # noqa: SLF001 — should not raise

    def test_assertion_does_not_fire_after_bg_thread_exited(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            writer = _make_minimal_writer(td)
            writer._start_bg_thread()  # noqa: SLF001
            writer._shutdown_bg_thread()  # noqa: SLF001
            # Bg thread is no longer alive; assertion must not fire.
            writer._assert_h5_locked()  # noqa: SLF001 — should not raise

    def test_assertion_fires_when_bg_thread_live_and_lock_not_held(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            writer = _make_minimal_writer(td)
            writer._start_bg_thread()  # noqa: SLF001
            try:
                with self.assertRaises(AssertionError) as ctx:
                    writer._assert_h5_locked()  # noqa: SLF001
                self.assertIn("_h5_lock", str(ctx.exception))
            finally:
                writer._shutdown_bg_thread()  # noqa: SLF001

    def test_assertion_does_not_fire_when_bg_thread_live_and_lock_held(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            writer = _make_minimal_writer(td)
            writer._start_bg_thread()  # noqa: SLF001
            try:
                with writer._h5_lock:  # noqa: SLF001
                    writer._assert_h5_locked()  # noqa: SLF001 — must not raise
            finally:
                writer._shutdown_bg_thread()  # noqa: SLF001


class AppendMeasurementNoteRowAcquiresLockTests(unittest.TestCase):
    """_append_measurement_note_row now takes _h5_lock for the duration."""

    def test_dataset_mutation_runs_under_h5_lock(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            writer = _make_minimal_writer(td)
            with h5py.File(Path(td) / "test.h5", "w") as h5:
                with writer._h5_lock:  # noqa: SLF001
                    writer._configure_active_file(  # noqa: SLF001
                        h5,
                        write_every_s=1.0,
                        load_manager_state=False,
                        measurement_meta=writer._build_measurement_metadata(  # noqa: SLF001
                            profile_id=None,
                            values=None,
                            require_profile=False,
                        ),
                    )

                # Now invoke from another thread without taking the lock
                # externally; the method must acquire it internally.
                observed_owned: list[bool] = []
                real_resize = writer._measurement_notes_ds.resize  # noqa: SLF001

                def _spy_resize(*args, **kwargs):
                    observed_owned.append(writer._h5_lock._is_owned())  # noqa: SLF001
                    return real_resize(*args, **kwargs)

                writer._measurement_notes_ds.resize = _spy_resize  # type: ignore[method-assign]

                def _call_from_other_thread() -> None:
                    writer._append_measurement_note_row(  # noqa: SLF001
                        author="op",
                        kind="note",
                        message="x",
                        payload_json="{}",
                    )

                t = threading.Thread(target=_call_from_other_thread)
                t.start()
                t.join(timeout=2.0)
                self.assertFalse(t.is_alive())
                self.assertEqual(len(observed_owned), 1)
                self.assertTrue(
                    observed_owned[0],
                    "_append_measurement_note_row must acquire _h5_lock before "
                    "mutating the dataset",
                )


class CloseCallsSuperCloseTests(unittest.TestCase):
    """HdfWriter.close() now delegates to super().close() so the ManagerClient
    and base-class heartbeat/RPC resources are torn down instead of leaked."""

    def test_super_close_is_invoked(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            writer = _make_minimal_writer(td)
            # Stub the inner socket teardowns so close() runs without
            # needing a real zmq context spin-up.
            sentinel = MagicMock()
            with patch.object(
                HdfWriter.__mro__[1], "close", sentinel  # ManagedProcessBase
            ):
                writer.close()
            sentinel.assert_called_once()


class RotateFileCleansUpOnConfigureFailureTests(unittest.TestCase):
    """_rotate_file must close AND delete the freshly-created .h5 if
    _configure_active_file raises, so a same-name rotate next round isn't
    blocked by _ensure_output_path_unused."""

    def test_failed_configure_unlinks_new_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            writer = _make_minimal_writer(td)
            # Bootstrap an active file so _rotate_file follows the
            # "swap-existing" path (not the "_start_writing_file" branch).
            bootstrap_h5 = h5py.File(Path(td) / "initial.h5", "w")
            try:
                with writer._h5_lock:  # noqa: SLF001
                    writer._configure_active_file(  # noqa: SLF001
                        bootstrap_h5,
                        write_every_s=1.0,
                        load_manager_state=False,
                        measurement_meta=writer._build_measurement_metadata(  # noqa: SLF001
                            profile_id=None, values=None, require_profile=False
                        ),
                    )

                # Swap _configure_active_file so the next call (inside
                # _rotate_file) raises after the new h5 file has been
                # opened on disk. The rotate path must close + unlink
                # the new file before re-raising so a same-name rotate
                # next round isn't blocked by _ensure_output_path_unused.
                def _boom_configure(*_args, **_kwargs):
                    raise RuntimeError("simulated configure failure")

                writer._configure_active_file = _boom_configure  # type: ignore[method-assign]  # noqa: SLF001

                rotated_path = Path(td) / "rotated.h5"
                with self.assertRaises(RuntimeError):
                    writer._rotate_file(filename="rotated.h5")  # noqa: SLF001
                self.assertFalse(
                    rotated_path.exists(),
                    "_rotate_file failed to clean up the freshly-created .h5 "
                    "after _configure_active_file raised; a same-name rotate "
                    "next round will be blocked by _ensure_output_path_unused",
                )
            finally:
                try:
                    # Close the in-memory bootstrap handle so the tempdir
                    # cleanup can unlink it on Windows.
                    if writer._h5 is bootstrap_h5:  # noqa: SLF001
                        writer._h5 = None  # noqa: SLF001
                    bootstrap_h5.close()
                except Exception:
                    pass


if __name__ == "__main__":
    unittest.main()

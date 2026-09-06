"""The stream x axis, from device resolution through to a fit's parameters."""

from __future__ import annotations

import unittest

import numpy as np

from experiment_control.processes.stream_analysis import (
    SAMPLE_INDEX_INPUT_TOKEN,
    StreamAnalysisProcess,
)
from experiment_control.processes.stream_analysis_fit import _model_reciprocal_normal
from experiment_control.stream_axis import IDENTITY_AXIS, ResolvedStreamAxis

SAMPLE_RATE_HZ = 100_000.0
TRIGGER_DELAY_S = 0.8e-3
N_SAMPLES = 512
TRUE_TAU0 = 6.7e-3
TRUE_R = 0.2

PXIE_AXIS = ResolvedStreamAxis(
    units="s",
    label="time since YAG trigger",
    increment=1.0 / SAMPLE_RATE_HZ,
    origin=TRIGGER_DELAY_S,
    source="run_metadata",
)

DEVICE_CONFIG = [
    {
        "device_id": "pxie5171",
        "stream_calls": [
            {
                "method": "read_waveform_frame",
                "outputs": [
                    {
                        "stream": "waveforms",
                        "dtype": "float64",
                        "shape": [4096],
                        "units": "ADC",
                        "x_axis": {
                            "units": "s",
                            "label": "time since YAG trigger",
                            "rate_from": "sample_rate_hz",
                            "origin_from": "trigger_delay_s",
                        },
                    }
                ],
            }
        ],
    }
]

RUN_METADATA = {
    "sample_rate_hz": SAMPLE_RATE_HZ,
    "trigger_delay_s": TRIGGER_DELAY_S,
}


class _FakeManager:
    """Manager stub answering the two RPCs the axis resolver makes."""

    def __init__(
        self,
        *,
        configs: object = DEVICE_CONFIG,
        run_metadata: object = RUN_METADATA,
        command_envelope: str = "runner",
        raise_on_command: bool = False,
    ) -> None:
        self.configs = configs
        self.run_metadata = run_metadata
        self.command_envelope = command_envelope
        self.raise_on_command = raise_on_command
        self.calls: list[str] = []

    def call(self, payload: dict, *, timeout_ms: int | None = None) -> dict | None:
        kind = str(payload.get("type"))
        self.calls.append(kind)
        if kind == "device.config.list":
            return {"ok": True, "result": self.configs}
        if kind == "command":
            if self.raise_on_command:
                raise TimeoutError("device offline")
            if self.command_envelope == "manager":
                return {"ok": True, "result": self.run_metadata}
            # The real driver-runner envelope, which carries no "ok" key.
            return {"id": 1, "status": "OK", "result": self.run_metadata}
        return {"ok": False, "error": "unexpected"}


def _process(manager: _FakeManager | None) -> StreamAnalysisProcess:
    proc = StreamAnalysisProcess.__new__(StreamAnalysisProcess)
    proc._manager = manager  # noqa: SLF001
    proc._stream_axis_cache = {}  # noqa: SLF001
    proc._stream_axis_ttl_s = 60.0  # noqa: SLF001
    proc._axis_rpc_timeout_ms = 1500  # noqa: SLF001
    proc._workspaces = {}  # noqa: SLF001
    return proc


class ResolveStreamAxisFromDeviceTests(unittest.TestCase):
    def test_resolves_sample_period_from_the_hardware_readback(self) -> None:
        proc = _process(_FakeManager())
        axis = proc._resolve_stream_axis(("pxie5171", "waveforms"))  # noqa: SLF001
        self.assertEqual(axis.source, "run_metadata")
        self.assertAlmostEqual(axis.increment, 1e-5)
        self.assertAlmostEqual(axis.origin, TRIGGER_DELAY_S)
        self.assertEqual(axis.units, "s")

    def test_accepts_the_manager_envelope_shape_too(self) -> None:
        """Device commands return the runner envelope; manager calls do not."""
        proc = _process(_FakeManager(command_envelope="manager"))
        axis = proc._resolve_stream_axis(("pxie5171", "waveforms"))  # noqa: SLF001
        self.assertEqual(axis.source, "run_metadata")
        self.assertAlmostEqual(axis.increment, 1e-5)

    def test_stream_without_declared_axis_is_identity_and_skips_the_device(
        self,
    ) -> None:
        manager = _FakeManager(
            configs=[
                {
                    "device_id": "pxie5171",
                    "stream_calls": [
                        {
                            "method": "read_waveform_frame",
                            "outputs": [
                                {
                                    "stream": "waveforms",
                                    "dtype": "float64",
                                    "shape": [4096],
                                }
                            ],
                        }
                    ],
                }
            ]
        )
        proc = _process(manager)
        axis = proc._resolve_stream_axis(("pxie5171", "waveforms"))  # noqa: SLF001
        self.assertIs(axis, IDENTITY_AXIS)
        self.assertNotIn("command", manager.calls)

    def test_offline_device_degrades_instead_of_raising(self) -> None:
        proc = _process(_FakeManager(raise_on_command=True))
        axis = proc._resolve_stream_axis(("pxie5171", "waveforms"))  # noqa: SLF001
        self.assertEqual(axis.source, "unresolved")
        self.assertEqual(axis.increment, 1.0)
        self.assertIsNotNone(axis.error)

    def test_missing_run_metadata_degrades(self) -> None:
        proc = _process(_FakeManager(run_metadata={"unrelated": 1.0}))
        axis = proc._resolve_stream_axis(("pxie5171", "waveforms"))  # noqa: SLF001
        self.assertEqual(axis.source, "unresolved")
        self.assertIsNotNone(axis.error)

    def test_unknown_stream_is_identity(self) -> None:
        proc = _process(_FakeManager())
        axis = proc._resolve_stream_axis(("pxie5171", "absent"))  # noqa: SLF001
        self.assertIs(axis, IDENTITY_AXIS)

    def test_result_is_cached_per_stream(self) -> None:
        manager = _FakeManager()
        proc = _process(manager)
        for _ in range(3):
            proc._resolve_stream_axis(("pxie5171", "waveforms"))  # noqa: SLF001
        self.assertEqual(manager.calls.count("device.config.list"), 1)
        self.assertEqual(manager.calls.count("command"), 1)

    def test_no_manager_degrades_to_identity(self) -> None:
        proc = _process(None)
        self.assertIs(
            proc._resolve_stream_axis(("pxie5171", "waveforms")),  # noqa: SLF001
            IDENTITY_AXIS,
        )


class FitAgainstResolvedAxisTests(unittest.TestCase):
    """The sample-index token must honour the workspace's resolved axis."""

    def _run_fit(self, axis: ResolvedStreamAxis) -> dict:
        from experiment_control.processes.stream_analysis_fit import FitCurve1DState

        proc = _process(None)
        x_true = TRIGGER_DELAY_S + np.arange(N_SAMPLES) / SAMPLE_RATE_HZ
        y = _model_reciprocal_normal(x_true, 2.0, TRUE_TAU0, TRUE_R)

        workspace = type(
            "_WS",
            (),
            {"x_axis": axis, "node_state": {"fit": FitCurve1DState.from_params(
                {"model": "reciprocal_normal"}
            )}},
        )()
        node = type(
            "_Node",
            (),
            {
                "inputs": {"x": SAMPLE_INDEX_INPUT_TOKEN, "y": "src"},
                "params": {"model": "reciprocal_normal"},
            },
        )()
        result = StreamAnalysisProcess._execute_workspace_fit_curve_1d(  # noqa: SLF001
            proc,
            workspace=workspace,
            node=node,
            node_id="fit",
            values={"src": y},
        )
        self.assertIsNotNone(result)
        return result

    def test_resolved_axis_gives_parameters_in_seconds(self) -> None:
        result = self._run_fit(PXIE_AXIS)
        params = result["params"]
        self.assertAlmostEqual(params["tau0"], TRUE_TAU0, places=6)
        self.assertAlmostEqual(params["r"], TRUE_R, places=4)
        self.assertEqual(params["x_units"], "s")
        self.assertAlmostEqual(params["x_increment"], 1e-5)
        self.assertAlmostEqual(params["x_origin"], TRIGGER_DELAY_S)
        self.assertEqual(params["x_axis_source"], "run_metadata")
        np.testing.assert_allclose(result["x"][:2], [TRIGGER_DELAY_S, 0.81e-3])

    def test_identity_axis_reproduces_the_previous_sample_index_behaviour(
        self,
    ) -> None:
        result = self._run_fit(IDENTITY_AXIS)
        np.testing.assert_allclose(
            result["x"], np.arange(N_SAMPLES, dtype=np.float64)
        )
        # tau0 comes back in samples, not seconds.
        self.assertGreater(result["params"]["tau0"], 1.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class RetryUnresolvedStreamAxesTests(unittest.TestCase):
    """An axis resolved while the digitizer was off must recover on its own.

    Otherwise a workspace applied before power-up silently fits in samples
    rather than seconds for the rest of the session.
    """

    def _workspace(self, proc: StreamAnalysisProcess, axis) -> object:
        from types import SimpleNamespace

        workspace = SimpleNamespace(
            compiled=SimpleNamespace(
                workspace_id="w1", stream_key=("pxie5171", "waveforms")
            ),
            x_axis=axis,
        )
        proc._workspaces["w1"] = workspace  # noqa: SLF001
        return workspace

    def test_an_offline_device_leaves_the_axis_unresolved(self) -> None:
        proc = _process(_FakeManager(raise_on_command=True))
        axis = proc._resolve_stream_axis(("pxie5171", "waveforms"))  # noqa: SLF001
        self.assertEqual(axis.source, "unresolved")
        self.assertEqual(axis.increment, 1.0)

    def test_retry_recovers_the_axis_once_the_device_answers(self) -> None:
        manager = _FakeManager(raise_on_command=True)
        proc = _process(manager)
        stale = proc._resolve_stream_axis(("pxie5171", "waveforms"))  # noqa: SLF001
        workspace = self._workspace(proc, stale)

        manager.raise_on_command = False
        proc._retry_unresolved_stream_axes()  # noqa: SLF001

        self.assertEqual(workspace.x_axis.source, "run_metadata")
        self.assertAlmostEqual(workspace.x_axis.increment, 1e-5)
        self.assertAlmostEqual(workspace.x_axis.origin, TRIGGER_DELAY_S)

    def test_retry_ignores_workspaces_whose_axis_already_resolved(self) -> None:
        manager = _FakeManager()
        proc = _process(manager)
        good = proc._resolve_stream_axis(("pxie5171", "waveforms"))  # noqa: SLF001
        self.assertEqual(good.source, "run_metadata")
        self._workspace(proc, good)

        before = len(manager.calls)
        proc._retry_unresolved_stream_axes()  # noqa: SLF001
        self.assertEqual(len(manager.calls), before, "resolved axes must cost no RPC")

    def test_retry_survives_a_device_that_is_still_offline(self) -> None:
        manager = _FakeManager(raise_on_command=True)
        proc = _process(manager)
        workspace = self._workspace(
            proc, proc._resolve_stream_axis(("pxie5171", "waveforms"))  # noqa: SLF001
        )
        proc._retry_unresolved_stream_axes()  # noqa: SLF001
        self.assertEqual(workspace.x_axis.source, "unresolved")

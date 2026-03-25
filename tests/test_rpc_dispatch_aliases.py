from __future__ import annotations

import unittest

from experiment_control.processes.hdf_writer import HdfWriter
from experiment_control.processes.influx_writer import InfluxWriterProcess
from experiment_control.processes.interlock import InterlockProcess
from experiment_control.processes.stream_analysis import StreamAnalysisProcess
from experiment_control.processes.watchdog import WatchdogProcess
from experiment_control.sequencer.sequencer import SequencerProcess


class RpcDispatchAliasTests(unittest.TestCase):
    def test_hdf_writer_aliases_canonicalize(self) -> None:
        writer = object.__new__(HdfWriter)
        registry = writer._build_rpc_registry()  # type: ignore[attr-defined]
        self.assertEqual(registry.canonical_action("hdf.get_status"), "hdf.status")
        self.assertEqual(
            registry.canonical_action("hdf.get_measurement_schema"),
            "hdf.measurement.schema.get",
        )
        self.assertEqual(
            registry.canonical_action("hdf.rotate_file"),
            "hdf.rotate",
        )

    def test_influx_writer_aliases_canonicalize(self) -> None:
        writer = object.__new__(InfluxWriterProcess)
        registry = writer._build_rpc_registry()  # type: ignore[attr-defined]
        self.assertEqual(
            registry.canonical_action("influx.get_status"),
            "influx.status",
        )

    def test_interlock_aliases_canonicalize(self) -> None:
        proc = object.__new__(InterlockProcess)
        registry = proc._build_rpc_registry()  # type: ignore[attr-defined]
        self.assertEqual(
            registry.canonical_action("interlock.get_status"),
            "interlock.status",
        )

    def test_sequencer_aliases_canonicalize(self) -> None:
        proc = object.__new__(SequencerProcess)
        registry = proc._build_rpc_registry()  # type: ignore[attr-defined]
        self.assertEqual(registry.canonical_action("sequencer.run"), "sequencer.start")
        self.assertEqual(
            registry.canonical_action("sequencer.get_status"),
            "sequencer.status",
        )
        self.assertEqual(
            registry.canonical_action("sequencer.library.get"),
            "sequencer.library.load",
        )

    def test_stream_analysis_aliases_canonicalize(self) -> None:
        proc = object.__new__(StreamAnalysisProcess)
        registry = proc._build_rpc_registry()  # type: ignore[attr-defined]
        self.assertEqual(
            registry.canonical_action("stream_analysis.get_status"),
            "stream_analysis.status",
        )
        self.assertEqual(
            registry.canonical_action("stream_analysis.workspace.upsert"),
            "stream_analysis.workspace.put",
        )
        self.assertEqual(
            registry.canonical_action("stream_analysis.workspace_store.persist"),
            "stream_analysis.workspace_store.save",
        )

    def test_watchdog_aliases_canonicalize(self) -> None:
        proc = object.__new__(WatchdogProcess)
        registry = proc._build_rpc_registry()  # type: ignore[attr-defined]
        self.assertEqual(
            registry.canonical_action("watchdog.get_status"),
            "watchdog.status",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

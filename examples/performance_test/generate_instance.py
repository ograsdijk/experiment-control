from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Any

import yaml


EXAMPLE_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXAMPLE_DIR.parents[1]
GENERATED_ROOT = EXAMPLE_DIR / ".generated"
PRESET_NAMES = ("light", "baseline", "heavy", "stress")
LIBRARY_SEQUENCES = 24


def _load_presets() -> dict[str, dict[str, Any]]:
    raw = yaml.safe_load((EXAMPLE_DIR / "performance_presets.yaml").read_text())
    return dict(raw["presets"])


def _telemetry_outputs(signal_count: int) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for index in range(max(0, signal_count - 4)):
        outputs.append(
            {
                "signal": f"float_{index:02d}",
                "kind": "key",
                "ref": f"float_{index:02d}",
                "units": "arb",
                "dtype": "float64",
            }
        )
    tail = [
        ("counter", "int64"),
        ("device_index", "int64"),
        ("toggle_slow", "bool"),
        ("toggle_fast", "bool"),
    ]
    for signal, dtype in tail[-min(4, signal_count) :]:
        outputs.append(
            {"signal": signal, "kind": "key", "ref": signal, "dtype": dtype}
        )
    return outputs


class _IndentedListDumper(yaml.SafeDumper):
    """Indents list items under their key (`steps:` / `  - for:`), as hand-written
    sequences do; PyYAML's default puts them in the key's column."""

    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow, False)


def _write_yaml(path: Path, value: object) -> None:
    path.write_text(
        yaml.dump(value, Dumper=_IndentedListDumper, sort_keys=False), encoding="utf-8"
    )


def _benchmark_sequence(telemetry_devices: int, blocks: int = 40) -> dict[str, Any]:
    """A sequence the size of a real measurement script, for the editor.

    It only reads device metadata and sleeps, so loading it (or running it)
    changes nothing on the performance devices.
    """
    steps: list[dict[str, Any]] = []
    for block in range(blocks):
        device = f"perf_tel_{block % max(1, telemetry_devices):02d}"
        steps.append(
            {
                "for": {
                    "bind": {"value": "level", "index": "level_idx"},
                    "in": {"gen": {"linspace": {"start": 0.0, "stop": "${max_level}", "num": "${n_levels}"}}},
                    "do": [
                        {"call": {"device": device, "action": "device_metadata"}},
                        {"set_context": {"fields": {"level": "${level}", "block": block}}},
                        {
                            "repeat": {
                                "times": "${n_repeats}",
                                "do": [{"sleep": "${settle_s}"}],
                            }
                        },
                    ],
                }
            }
        )
    return {
        "version": 1,
        "meta": {"description": f"Benchmark sequence, {blocks} sweep blocks."},
        "context_columns": {"level": "float64", "block": "int64"},
        "vars": {"max_level": 1.0, "n_levels": 3, "n_repeats": 2, "settle_s": 0.01},
        "steps": steps,
    }


def _write_ui_processes(target: Path, processes_dir: Path, telemetry_devices: int) -> list[str]:
    """Sequencer (autoloading the benchmark sequence) and an idle HDF writer.

    They make the Sequencer and HDF modals reachable for UI benchmarks; the
    writer only writes once a run is started from the UI.
    """
    # Process paths resolve against the instance directory (the stack root).
    _write_yaml(target / "sequence_benchmark.yaml", _benchmark_sequence(telemetry_devices))
    # A library of smaller sequences, for searching and switching files in the modal.
    sequences_dir = target / "sequences"
    sequences_dir.mkdir()
    for index in range(LIBRARY_SEQUENCES):
        blocks = 10 + (index * 7) % 31
        _write_yaml(
            sequences_dir / f"bench_scan_{index:02d}.yaml",
            _benchmark_sequence(telemetry_devices, blocks=blocks),
        )
    _write_yaml(
        target / "sequence_library.yaml",
        {"version": 1, "autoload_dirs": [{"dir": "sequences", "pattern": "*.yaml"}]},
    )
    _write_yaml(
        processes_dir / "sequencer.yaml",
        {
            "version": 1,
            "process_id": "sequencer",
            "process": {
                "file": "src/experiment_control/sequencer/sequencer.py",
                "class_name": "SequencerProcess",
            },
            "init_kwargs": {
                "rpc_timeout_ms": 2000,
                "autoload_path": "sequence_benchmark.yaml",
                "sequence_library_path": "sequence_library.yaml",
            },
            "restart_policy": "NEVER",
            "heartbeat_timeout_s": 3.0,
            "shutdown_timeout_s": 3.0,
        },
    )
    _write_yaml(
        processes_dir / "hdf_writer.yaml",
        {
            "version": 1,
            "process_id": "hdf_writer",
            "process": {
                "file": "src/experiment_control/processes/hdf_writer.py",
                "class_name": "HdfWriter",
            },
            "init_kwargs": {
                "out_dir": "data",
                "filename": None,
                "timezone": "America/Chicago",
                "rpc_timeout_ms": 2000,
                "rcvhwm": 10000,
                "write_every_s": 1.0,
                "buffer_max_messages": 200000,
                "flush_every_n": 200,
                "flush_every_s": 2.0,
                "disabled_devices": [],
                "event_log_mode": "failures_only",
            },
            "restart_policy": "NEVER",
            "heartbeat_timeout_s": 3.0,
            "shutdown_timeout_s": 3.0,
        },
    )
    return ["sequencer", "hdf_writer"]


def generate(preset_name: str, *, ui_processes: bool = False) -> Path:
    presets = _load_presets()
    if preset_name not in presets:
        raise ValueError(f"unknown preset {preset_name!r}; choose from {PRESET_NAMES}")
    preset = presets[preset_name]
    target = (GENERATED_ROOT / preset_name).resolve()
    if target.parent != GENERATED_ROOT.resolve():
        raise RuntimeError("refusing to generate outside .generated")
    if target.exists():
        shutil.rmtree(target)
    devices_dir = target / "devices"
    processes_dir = target / "processes"
    devices_dir.mkdir(parents=True)
    processes_dir.mkdir()

    telemetry_devices = int(preset["telemetry_devices"])
    telemetry_signals = int(preset["telemetry_signals"])
    telemetry_hz = float(preset["telemetry_hz"])
    for index in range(telemetry_devices):
        device_id = f"perf_tel_{index:02d}"
        _write_yaml(
            devices_dir / f"{device_id}.yaml",
            {
                "version": 1,
                "device_id": device_id,
                "telemetry_period_s": 1.0 / telemetry_hz,
                "driver": {
                    "module": "examples.performance_test.drivers.performance_telemetry_driver",
                    "class_name": "PerformanceTelemetryDriver",
                },
                "init_kwargs": {
                    "device_index": index,
                    "signal_count": telemetry_signals,
                },
                "telemetry_calls": [
                    {"method": "read_all", "outputs": _telemetry_outputs(telemetry_signals)}
                ],
            },
        )

    trace_devices = int(preset["trace_devices"])
    trace_channels = int(preset["trace_channels"])
    trace_points = int(preset["trace_points"])
    trace_hz = float(preset["trace_hz"])
    for index in range(trace_devices):
        device_id = f"perf_trace_{index:02d}"
        _write_yaml(
            devices_dir / f"{device_id}.yaml",
            {
                "version": 1,
                "device_id": device_id,
                "driver": {
                    "module": "examples.performance_test.drivers.performance_trace_driver",
                    "class_name": "PerformanceTraceDriver",
                },
                "init_kwargs": {
                    "device_index": index,
                    "channels": trace_channels,
                    "points": trace_points,
                    "dtype": "float32",
                },
                "stream_calls": [
                    {
                        "method": "acquire_trace",
                        "period_s": 1.0 / trace_hz,
                        "outputs": [
                            {
                                "stream": "trace",
                                "dtype": "float32",
                                "shape": [trace_channels, trace_points],
                                "units": "arb",
                                "ring_slots": 256,
                            }
                        ],
                    }
                ],
                "stream_metadata": {
                    "trace": {
                        "channel_descriptions": [
                            f"Performance channel {channel}"
                            for channel in range(trace_channels)
                        ],
                        "channel_units": ["arb"] * trace_channels,
                        "x_axis": "sample_index",
                        "x_units": "sample",
                    }
                },
            },
        )

    workspace_target = target / "stream_workspaces.yaml"
    shutil.copyfile(EXAMPLE_DIR / "stream_workspaces.yaml", workspace_target)
    process = yaml.safe_load((EXAMPLE_DIR / "processes/stream_analysis.yaml").read_text())
    process["init_kwargs"]["workspace_store_path"] = str(
        workspace_target.relative_to(REPO_ROOT)
    ).replace("\\", "/")
    _write_yaml(processes_dir / "stream_analysis.yaml", process)
    extra_processes = (
        _write_ui_processes(target, processes_dir, telemetry_devices) if ui_processes else []
    )

    port_offset = PRESET_NAMES.index(preset_name) * 20
    stack = {
        "version": 1,
        "instance_id": f"performance-test-{preset_name}",
        "manager": {
            "bind_host": "127.0.0.1",
            "external": {"rpc_port": 6400 + port_offset, "pub_port": 6401 + port_offset},
            "internal_ports": {
                "registry": 6455 + port_offset,
                "rpc": 6402 + port_offset,
                "heartbeat_base": 7600 + port_offset,
            },
            "auto_connect_on_register": True,
            "interceptor_rpc_timeout_ms": 500,
        },
        "devices": {"dirs": ["devices"], "glob": "*.yaml"},
        "processes": {"dirs": ["processes"], "glob": "*.yaml"},
        "tui": {"enabled": False},
        "startup": {
            "start_devices": True,
            "start_processes": True,
            "process_order": ["stream_analysis", *extra_processes],
            "wait_for_registered": True,
            "wait_for_online": True,
            "timeout_s": 30.0,
        },
    }
    _write_yaml(target / "stack.yaml", stack)
    _write_yaml(
        target / "preset.yaml",
        {"name": preset_name, **preset, "ui_processes": ui_processes},
    )
    return target


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preset", choices=PRESET_NAMES, default="baseline")
    parser.add_argument(
        "--ui-processes",
        action="store_true",
        help="also add a sequencer and an idle HDF writer (Sequencer/HDF modals)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    result = generate(args.preset, ui_processes=args.ui_processes)
    print(result)

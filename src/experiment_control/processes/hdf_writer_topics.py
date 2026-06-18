from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..contracts.messages import ChunkReadyMessage, DeviceScopedMessage

Json = dict[str, Any]
TopicHandler = Callable[[Json], None]


def build_hdf_topic_handlers(writer: Any) -> dict[str, TopicHandler]:
    return {
        "manager.telemetry_update": lambda msg: _handle_manager_telemetry_update(
            writer, msg
        ),
        "manager.process_telemetry_update": (
            lambda msg: _handle_manager_process_telemetry_update(writer, msg)
        ),
        "manager.chunk_ready": lambda msg: _handle_manager_chunk_ready(writer, msg),
        "manager.device_config": lambda msg: writer._handle_device_config(msg),  # noqa: SLF001
        "manager.run_metadata": lambda msg: _handle_manager_run_metadata(writer, msg),
        "manager.command": lambda msg: _handle_manager_command(writer, msg),
        "manager.log": lambda msg: _handle_manager_log(writer, msg),
        "sequencer.lifecycle": lambda msg: writer._handle_sequencer_lifecycle(msg),  # noqa: SLF001
    }


def _enabled_device_id(writer: Any, msg: Json) -> str | None:
    parsed = DeviceScopedMessage.parse(msg)
    if parsed is None:
        return None
    if not writer._is_device_enabled(parsed.device_id):  # noqa: SLF001
        return None
    return parsed.device_id


def _handle_manager_telemetry_update(writer: Any, msg: Json) -> None:
    if _enabled_device_id(writer, msg) is None:
        return
    writer._buffer_append(topic="manager.telemetry_update", msg=msg)  # noqa: SLF001


def _handle_manager_process_telemetry_update(writer: Any, msg: Json) -> None:
    # Process telemetry rides the device telemetry write path: stamp the
    # process_id into device_id so _write_buffered_rows_batch routes the row to
    # the pre-created /process_telemetry/<process_id> dataset (see
    # _ingest_process_schema). Distinct group + source_kind attr keep the
    # device/process distinction in the file.
    process_id = str(msg.get("process_id", "")).strip()
    if not process_id or not writer._is_device_enabled(process_id):  # noqa: SLF001
        return
    row = dict(msg)
    row["device_id"] = process_id
    writer._buffer_append(topic="manager.telemetry_update", msg=row)  # noqa: SLF001


def _handle_manager_chunk_ready(writer: Any, msg: Json) -> None:
    parsed = ChunkReadyMessage.parse(msg)
    if parsed is None:
        return
    writer._handle_chunk_ready(msg, parsed=parsed)  # noqa: SLF001


def _handle_manager_run_metadata(writer: Any, msg: Json) -> None:
    if _enabled_device_id(writer, msg) is None:
        return
    writer._handle_run_metadata(msg)  # noqa: SLF001


def _handle_manager_command(writer: Any, msg: Json) -> None:
    if _enabled_device_id(writer, msg) is None:
        return
    if not writer._should_keep_event(topic="manager.command", msg=msg):  # noqa: SLF001
        return
    writer._buffer_event(topic="manager.command", msg=msg)  # noqa: SLF001


def _handle_manager_log(writer: Any, msg: Json) -> None:
    if not writer._should_keep_event(topic="manager.log", msg=msg):  # noqa: SLF001
        return
    writer._buffer_event(topic="manager.log", msg=msg)  # noqa: SLF001

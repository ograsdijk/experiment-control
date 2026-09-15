from __future__ import annotations

import unittest
from types import SimpleNamespace

from experiment_control._manager.models import ProcessHandle, ProcessSpec, RestartPolicy
from experiment_control._manager.route_handlers import route_process_config_get
from experiment_control.utils.config_redaction import REDACTED_VALUE, redact_config


class _Hub:
    def __init__(self, processes: list[dict] | None = None) -> None:
        self._processes = processes or []

    def list_processes_snapshot(self) -> list[dict]:
        return self._processes


class ProcessConfigTests(unittest.TestCase):
    def test_recursive_redaction(self) -> None:
        value = {
            "port": "COM4",
            "auth": {"apiKey": "abc", "username": "operator"},
            "db-password": "def",
        }
        self.assertEqual(
            redact_config(value),
            {
                "port": "COM4",
                "auth": {"apiKey": REDACTED_VALUE, "username": "operator"},
                "db-password": REDACTED_VALUE,
            },
        )

    def test_class_process_config_is_structured_and_safe(self) -> None:
        spec = ProcessSpec(
            process_id="writer",
            argv=["python", "-m", "experiment_control.cli.start_process"],
            process_class_path="writer.py",
            process_class_name="Writer",
            init_kwargs={"port": "COM4", "nested": {"token": "secret"}},
            env={"VISIBLE_NAME": "also-sensitive"},
            restart_policy=RestartPolicy.ON_FAILURE,
        )
        manager = SimpleNamespace(
            _processes={"writer": ProcessHandle(spec=spec)},
            _federation_hub=_Hub(),
        )

        response = route_process_config_get(manager, {"process_id": "writer"})

        self.assertTrue(response["ok"])
        result = response["result"]
        self.assertEqual(result["launch"]["class_name"], "Writer")
        self.assertEqual(result["init_kwargs"]["port"], "COM4")
        self.assertEqual(result["init_kwargs"]["nested"]["token"], REDACTED_VALUE)
        self.assertEqual(result["env"]["VISIBLE_NAME"], REDACTED_VALUE)
        self.assertNotIn("argv", result)

    def test_explicit_argv_hides_arguments(self) -> None:
        spec = ProcessSpec(
            process_id="external",
            argv=["external.exe", "--password", "secret"],
        )
        manager = SimpleNamespace(
            _processes={"external": ProcessHandle(spec=spec)},
            _federation_hub=_Hub(),
        )

        result = route_process_config_get(manager, {"process_id": "external"})["result"]

        self.assertEqual(result["launch"]["executable"], "external.exe")
        self.assertEqual(result["launch"]["arguments"], REDACTED_VALUE)
        self.assertNotIn("secret", str(result))

    def test_federated_process_config_reports_unavailable(self) -> None:
        manager = SimpleNamespace(
            _processes={},
            _federation_hub=_Hub([{"process_id": "remote", "is_remote": True}]),
        )

        response = route_process_config_get(manager, {"process_id": "remote"})

        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "process_config_unavailable")


if __name__ == "__main__":
    unittest.main()

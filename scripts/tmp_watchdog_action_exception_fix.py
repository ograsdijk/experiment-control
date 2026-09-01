from pathlib import Path


def rep(path: str, old: str, new: str) -> None:
    p = Path(path)
    s = p.read_text(encoding="utf-8")
    if new in s:
        return
    if old not in s:
        raise RuntimeError(f"anchor missing in {path}: {old[:120]!r}")
    p.write_text(s.replace(old, new, 1), encoding="utf-8")


rep(
    "src/experiment_control/processes/watchdog.py",
    '''                attempt_started_mono = time.monotonic()
                resp = self._require_manager().call(req, timeout_ms=timeout_ms)
                duration_ms = (time.monotonic() - attempt_started_mono) * 1000.0
                if resp is not None and _resp_ok(resp):
''',
    '''                attempt_started_mono = time.monotonic()
                call_error: str | None = None
                try:
                    resp = self._require_manager().call(req, timeout_ms=timeout_ms)
                except Exception as exc:
                    # A transport/client exception from one target must be handled
                    # like a failed attempt, not abort the remaining safety actions.
                    resp = None
                    call_error = repr(exc)
                duration_ms = (time.monotonic() - attempt_started_mono) * 1000.0
                if call_error is None and resp is not None and _resp_ok(resp):
''',
)
rep(
    "src/experiment_control/processes/watchdog.py",
    '''                error = resp if resp is not None else "timeout"
''',
    '''                error = (
                    call_error
                    if call_error is not None
                    else (resp if resp is not None else "timeout")
                )
''',
)

rep(
    "tests/test_watchdog_process_action.py",
    '''        self, responses: list[dict[str, Any] | None]
    ) -> tuple[WatchdogProcess, list[tuple[str, dict[str, Any]]]]:
''',
    '''        self, responses: list[dict[str, Any] | None | BaseException]
    ) -> tuple[WatchdogProcess, list[tuple[str, dict[str, Any]]]]:
''',
)
rep(
    "tests/test_watchdog_process_action.py",
    '''            def call(self, _req: dict, timeout_ms: int | None = None) -> dict | None:
                del timeout_ms
                return remaining.pop(0)
''',
    '''            def call(self, _req: dict, timeout_ms: int | None = None) -> dict | None:
                del timeout_ms
                response = remaining.pop(0)
                if isinstance(response, BaseException):
                    raise response
                return response
''',
)

marker = '''    def test_completed_action_chain_is_persisted_in_rule_state(self) -> None:
'''
addition = '''    def test_manager_exception_does_not_block_later_shutdown_actions(self) -> None:
        proc, events = self._make_proc_with_responses(
            [
                {"status": "OK"},
                RuntimeError("manager socket failed"),
                {"status": "OK"},
                {"status": "OK"},
            ]
        )
        rule = _rule_with_actions(
            [
                CommandAction("hipace_rc", "stop", {}, 1.5, 0),
                CommandAction("hipace_eql", "stop", {}, 1.5, 0),
                CommandAction("hipace_det", "stop", {}, 1.5, 0),
                CommandAction("hipace_spb", "stop", {}, 1.5, 0),
            ]
        )

        summary = proc._execute_actions(
            watchdog_id="vacuum-cryo_watchdog",
            rule=rule,
            trip_id="trip-exception",
        )

        started = [
            payload["command"]["device_id"]
            for topic, payload in events
            if topic == "manager.watchdog.action_started"
        ]
        self.assertEqual(
            started,
            ["hipace_rc", "hipace_eql", "hipace_det", "hipace_spb"],
        )
        self.assertFalse(summary["success"])
        self.assertEqual(summary["succeeded_actions"], 3)
        self.assertEqual(summary["failed_actions"], 1)
        failed = [result for result in summary["actions"] if not result["ok"]]
        self.assertEqual(failed[0]["command"]["device_id"], "hipace_eql")
        self.assertIn("manager socket failed", str(failed[0]["error"]))

'''
p = Path("tests/test_watchdog_process_action.py")
s = p.read_text(encoding="utf-8")
if "test_manager_exception_does_not_block_later_shutdown_actions" not in s:
    if marker not in s:
        raise RuntimeError("test insertion marker missing")
    p.write_text(s.replace(marker, addition + marker, 1), encoding="utf-8")

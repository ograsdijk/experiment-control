from __future__ import annotations

import threading
import time
import unittest
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import yaml

from experiment_control.sequencer.ast import parse_sequence
from experiment_control.sequencer.runtime import (
    ParallelBranchPlan,
    ParallelBranchResult,
    SequencerRuntime,
    parallel_branch_operations,
    run_parallel_branch,
)
from experiment_control.sequencer.sequencer import _ParallelWorkerPool


@dataclass
class _Dispatch:
    futures: list[tuple[ParallelBranchPlan, Future[ParallelBranchResult]]]

    def cancel(self) -> None:
        for _plan, future in self.futures:
            future.cancel()


class _ParallelHarness:
    def __init__(self, *, delay_s: float = 0.03, max_workers: int = 8) -> None:
        self.delay_s = delay_s
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.calls: list[tuple[str, str, float, float]] = []

    def close(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=True)

    def call_device(
        self, device: str, action: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        del params
        started = time.monotonic()
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delay_s)
            if action == "fail":
                return {"ok": False, "error": {"code": "failed", "message": "boom"}}
            return {"ok": True, "result": 7}
        finally:
            ended = time.monotonic()
            with self.lock:
                self.active -= 1
                self.calls.append((device, action, started, ended))

    def call_process(
        self, process: str, action: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        return self.call_device(f"process:{process}", action, params)

    def begin(self, plans: list[ParallelBranchPlan]) -> _Dispatch:
        return _Dispatch(
            [
                (
                    plan,
                    self.executor.submit(
                        run_parallel_branch,
                        plan,
                        call_device=self.call_device,
                        call_process=self.call_process,
                    ),
                )
                for plan in plans
            ]
        )

    @staticmethod
    def poll(
        state: _Dispatch,
    ) -> tuple[bool, list[ParallelBranchResult] | None]:
        if any(not future.done() for _plan, future in state.futures):
            return False, None
        return True, [future.result() for _plan, future in state.futures]


def _runtime(harness: _ParallelHarness, yaml_text: str) -> SequencerRuntime:
    runtime = SequencerRuntime(
        call_device=harness.call_device,
        call_process=harness.call_process,
        get_telemetry=lambda _device, _signal: None,
        get_process_telemetry=lambda _process, _signal: None,
        set_stream_context=lambda *_args: None,
        begin_parallel=harness.begin,
        poll_parallel=harness.poll,
    )
    runtime.load(parse_sequence(yaml.safe_load(yaml_text)))
    runtime.start()
    return runtime


def _run_to_terminal(runtime: SequencerRuntime, timeout_s: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_s
    while runtime.state == "RUNNING" and time.monotonic() < deadline:
        runtime.tick()
        time.sleep(0.001)
    if runtime.state == "RUNNING":
        raise AssertionError("runtime did not reach a terminal state")


class SequencerParallelTests(unittest.TestCase):
    def test_distinct_calls_overlap(self) -> None:
        harness = _ParallelHarness(delay_s=0.06)
        try:
            runtime = _runtime(
                harness,
                """
version: 1
steps:
  - parallel:
      do:
        - call: {device: a, action: go}
        - call: {device: b, action: go}
""",
            )
            started = time.monotonic()
            _run_to_terminal(runtime)
            elapsed = time.monotonic() - started
            self.assertEqual(runtime.state, "STOPPED")
            self.assertGreaterEqual(harness.max_active, 2)
            self.assertLess(elapsed, 0.11)
        finally:
            harness.close()

    def test_atomic_branches_overlap_but_each_branch_stays_ordered(self) -> None:
        harness = _ParallelHarness(delay_s=0.035)
        try:
            runtime = _runtime(
                harness,
                """
version: 1
steps:
  - parallel:
      do:
        - atomic:
            name: left
            do:
              - call: {device: a, action: first}
              - call: {device: a, action: second}
        - atomic:
            name: right
            do:
              - set: {device: b, name: enabled, value: true}
              - call: {device: b, action: fire}
""",
            )
            _run_to_terminal(runtime)
            self.assertEqual(runtime.state, "STOPPED")
            by_device = {
                device: [item for item in harness.calls if item[0] == device]
                for device in {"a", "b"}
            }
            self.assertEqual([item[1] for item in by_device["a"]], ["first", "second"])
            self.assertEqual([item[1] for item in by_device["b"]], ["set", "fire"])
            self.assertGreaterEqual(harness.max_active, 2)
        finally:
            harness.close()

    def test_repeat_branches_overlap_and_each_branch_stays_ordered(self) -> None:
        harness = _ParallelHarness(delay_s=0.02)
        try:
            runtime = _runtime(
                harness,
                """
version: 1
vars: {n: 3}
steps:
  - parallel:
      do:
        - repeat:
            times: "${n}"
            do:
              - call: {device: pxie, action: read_frame}
        - repeat:
            times: "${n}"
            do:
              - call: {device: fs740, action: read_timestamp}
""",
            )
            _run_to_terminal(runtime)
            self.assertEqual(runtime.state, "STOPPED")
            self.assertGreaterEqual(harness.max_active, 2)
            self.assertEqual(
                [call[1] for call in harness.calls if call[0] == "pxie"],
                ["read_frame"] * 3,
            )
            self.assertEqual(
                [call[1] for call in harness.calls if call[0] == "fs740"],
                ["read_timestamp"] * 3,
            )
        finally:
            harness.close()

    def test_repeat_branch_plan_size_is_independent_of_repeat_count(self) -> None:
        spec = parse_sequence(
            yaml.safe_load(
                """
version: 1
steps:
  - parallel:
      do:
        - repeat:
            times: "${n}"
            do:
              - call: {device: a, action: read}
"""
            )
        )
        parallel = spec.steps[0]
        branch = parallel.body[0]  # type: ignore[union-attr]
        operations = parallel_branch_operations(branch, {"n": 1_000_000})
        self.assertEqual(len(operations), 1)

    def test_repeat_branch_failure_reports_iteration(self) -> None:
        spec = parse_sequence(
            yaml.safe_load(
                """
version: 1
steps:
  - parallel:
      do:
        - repeat:
            times: 3
            do:
              - call: {device: a, action: read}
"""
            )
        )
        parallel = spec.steps[0]
        branch = parallel.body[0]  # type: ignore[union-attr]
        operations = parallel_branch_operations(branch, {})
        calls = 0

        def call_device(
            _device: str, _action: str, _params: dict[str, Any]
        ) -> dict[str, Any]:
            nonlocal calls
            calls += 1
            if calls == 2:
                return {"ok": False, "error": {"message": "boom"}}
            return {"ok": True}

        result = run_parallel_branch(
            ParallelBranchPlan(
                index=0,
                operations=operations,
                env={},
                path="steps[0].parallel.do[0]",
                repeat_count=3,
            ),
            call_device=call_device,
            call_process=None,
        )
        self.assertFalse(result.ok)
        self.assertIn("iteration 2/3", str(result.path))

    def test_repeat_branch_rejects_unsupported_body_and_invalid_count(self) -> None:
        cases = (
            """
version: 1
steps:
  - parallel:
      do:
        - repeat:
            times: 2
            do:
              - sleep: 0.1
        - call: {device: b, action: go}
""",
            """
version: 1
steps:
  - parallel:
      do:
        - repeat:
            times: 0
            do:
              - call: {device: a, action: go}
        - call: {device: b, action: go}
""",
        )
        for yaml_text in cases:
            with self.subTest(yaml=yaml_text):
                harness = _ParallelHarness(delay_s=0.001)
                try:
                    runtime = _runtime(harness, yaml_text)
                    _run_to_terminal(runtime)
                    self.assertEqual(runtime.state, "ERROR")
                    self.assertEqual(harness.calls, [])
                finally:
                    harness.close()

    def test_branch_local_output_can_feed_later_atomic_operation(self) -> None:
        harness = _ParallelHarness(delay_s=0.005)
        try:
            runtime = _runtime(
                harness,
                """
version: 1
steps:
  - parallel:
      do:
        - atomic:
            do:
              - call: {device: a, action: read}
                extract: {kind: scalar}
                save_as: measured
              - set: {device: a, name: target, value: "${measured}"}
        - call: {device: b, action: read}
          extract: {kind: scalar}
          save_as: other
""",
            )
            _run_to_terminal(runtime)
            self.assertEqual(runtime.state, "STOPPED")
            self.assertEqual(runtime._env["measured"], 7)
            self.assertEqual(runtime._env["other"], 7)
        finally:
            harness.close()

    def test_target_and_output_conflicts_fail_before_dispatch(self) -> None:
        cases = [
            """
version: 1
steps:
  - parallel:
      do:
        - call: {device: same, action: one}
        - set: {device: same, name: value, value: 1}
""",
            """
version: 1
steps:
  - parallel:
      do:
        - call: {device: a, action: one}
          save_as: result
        - call: {device: b, action: two}
          save_as: result
""",
        ]
        for yaml_text in cases:
            with self.subTest(yaml=yaml_text):
                harness = _ParallelHarness(delay_s=0.001)
                try:
                    runtime = _runtime(harness, yaml_text)
                    _run_to_terminal(runtime)
                    self.assertEqual(runtime.state, "ERROR")
                    self.assertEqual(harness.calls, [])
                finally:
                    harness.close()

    def test_failure_waits_for_siblings_and_does_not_merge_outputs(self) -> None:
        harness = _ParallelHarness(delay_s=0.03)
        try:
            runtime = _runtime(
                harness,
                """
version: 1
steps:
  - parallel:
      do:
        - call: {device: a, action: fail}
        - call: {device: b, action: read}
          save_as: successful_result
""",
            )
            _run_to_terminal(runtime)
            self.assertEqual(runtime.state, "ERROR")
            self.assertEqual(len(harness.calls), 2)
            self.assertNotIn("successful_result", runtime._env)
            self.assertIn("branch 0", str(runtime.status().get("error")))
        finally:
            harness.close()

    def test_executor_caps_active_branches_at_eight(self) -> None:
        harness = _ParallelHarness(delay_s=0.02, max_workers=8)
        try:
            branches = "\n".join(
                f"        - call: {{device: d{index}, action: go}}"
                for index in range(12)
            )
            runtime = _runtime(
                harness,
                f"version: 1\nsteps:\n  - parallel:\n      do:\n{branches}\n",
            )
            _run_to_terminal(runtime)
            self.assertEqual(runtime.state, "STOPPED")
            self.assertEqual(harness.max_active, 8)
            self.assertEqual(len(harness.calls), 12)
        finally:
            harness.close()

    def test_pause_is_deferred_until_parallel_join(self) -> None:
        harness = _ParallelHarness(delay_s=0.05)
        try:
            runtime = _runtime(
                harness,
                """
version: 1
steps:
  - parallel:
      do:
        - atomic:
            do:
              - call: {device: a, action: one}
              - call: {device: a, action: two}
        - call: {device: b, action: one}
""",
            )
            runtime.tick()
            runtime.request_pause()
            runtime.tick()
            self.assertEqual(runtime.state, "RUNNING")
            deadline = time.monotonic() + 2.0
            while runtime.state == "RUNNING" and time.monotonic() < deadline:
                runtime.tick()
                time.sleep(0.001)
            self.assertEqual(runtime.state, "PAUSED")
            self.assertEqual([call[1] for call in harness.calls if call[0] == "a"], ["one", "two"])
        finally:
            harness.close()

    def test_stop_is_deferred_until_parallel_join(self) -> None:
        harness = _ParallelHarness(delay_s=0.04)
        try:
            runtime = _runtime(
                harness,
                """
version: 1
steps:
  - parallel:
      do:
        - atomic:
            do:
              - call: {device: a, action: one}
              - call: {device: a, action: two}
        - call: {device: b, action: one}
  - call: {device: after, action: must_not_run}
""",
            )
            runtime.tick()
            runtime.request_stop()
            runtime.tick()
            self.assertEqual(runtime.status().get("state"), "STOP_REQUESTED")
            deadline = time.monotonic() + 2.0
            while runtime.state == "RUNNING" and time.monotonic() < deadline:
                runtime.tick()
                time.sleep(0.001)
            self.assertEqual(runtime.state, "STOPPED")
            self.assertEqual(
                [call[1] for call in harness.calls if call[0] == "a"],
                ["one", "two"],
            )
            self.assertFalse(any(call[0] == "after" for call in harness.calls))
        finally:
            harness.close()

    def test_external_fault_waits_for_parallel_join_before_finally(self) -> None:
        harness = _ParallelHarness(delay_s=0.025)
        try:
            runtime = _runtime(
                harness,
                """
version: 1
steps:
  - try:
      do:
        - parallel:
            do:
              - atomic:
                  do:
                    - call: {device: a, action: one}
                    - call: {device: a, action: two}
              - call: {device: b, action: one}
      finally:
        - call: {device: cleanup, action: safe}
""",
            )
            runtime.tick()
            runtime.fail("external fault")
            self.assertEqual(runtime.state, "RUNNING")
            _run_to_terminal(runtime)
            self.assertEqual(runtime.state, "ERROR")
            actions = [(device, action) for device, action, _start, _end in harness.calls]
            self.assertGreater(actions.index(("cleanup", "safe")), actions.index(("a", "two")))
        finally:
            harness.close()

    def test_parallel_inside_atomic_is_rejected(self) -> None:
        harness = _ParallelHarness(delay_s=0.001)
        try:
            runtime = _runtime(
                harness,
                """
version: 1
steps:
  - atomic:
      do:
        - parallel:
            do:
              - call: {device: a, action: one}
              - call: {device: b, action: one}
""",
            )
            _run_to_terminal(runtime)
            self.assertEqual(runtime.state, "ERROR")
            self.assertEqual(harness.calls, [])
            self.assertIn("not allowed inside an atomic", str(runtime.status().get("error")))
        finally:
            harness.close()

    def test_disabled_atomic_child_keeps_original_error_index(self) -> None:
        harness = _ParallelHarness(delay_s=0.001)
        try:
            runtime = _runtime(
                harness,
                """
version: 1
steps:
  - parallel:
      do:
        - atomic:
            do:
              - call: {device: a, action: skipped}
                disabled: true
              - call: {device: a, action: fail}
        - call: {device: b, action: one}
""",
            )
            _run_to_terminal(runtime)
            self.assertEqual(runtime.state, "ERROR")
            self.assertIn("atomic.do[1]", str(runtime.status().get("error")))
        finally:
            harness.close()


class ParallelWorkerPoolTests(unittest.TestCase):
    def test_worker_reuses_and_thread_closes_its_client(self) -> None:
        created: list[_FakeParallelClient] = []
        execute_thread_ids: list[int] = []

        def factory():  # noqa: ANN202
            client = _FakeParallelClient()
            created.append(client)
            return client

        def execute(
            plan: ParallelBranchPlan, client: _FakeParallelClient
        ) -> ParallelBranchResult:
            execute_thread_ids.append(threading.get_ident())
            client.uses += 1
            return ParallelBranchResult(index=plan.index, ok=True, outputs={})

        pool = _ParallelWorkerPool(
            worker_count=1,
            client_factory=factory,
            execute=execute,  # type: ignore[arg-type]
        )
        plans = [
            ParallelBranchPlan(index=index, operations=(), env={}, path=f"branch[{index}]")
            for index in range(2)
        ]
        try:
            results = [pool.submit(plan).result(timeout=2.0) for plan in plans]
            self.assertEqual([result.index for result in results], [0, 1])
        finally:
            pool.close()
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].uses, 2)
        self.assertEqual(len(set(execute_thread_ids)), 1)
        self.assertEqual(created[0].closed_thread_id, execute_thread_ids[0])


class _FakeParallelClient:
    def __init__(self) -> None:
        self.uses = 0
        self.closed_thread_id: int | None = None

    def close(self) -> None:
        self.closed_thread_id = threading.get_ident()


if __name__ == "__main__":
    unittest.main()

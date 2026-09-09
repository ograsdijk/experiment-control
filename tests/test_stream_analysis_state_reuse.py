# ruff: noqa: E402

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.processes.stream_analysis import (
    BinStatsState,
    StreamAnalysisProcess,
    compile_workspace_graph,
)


def _workspace(
    *,
    fit_every_n: int = 1,
    bin_count: int = 8,
    bin_x_input: str = "ctx_x",
    extra_nodes: list[dict] | None = None,
    outputs: list[dict] | None = None,
) -> dict:
    """A `source -> bin_stats -> fit` graph with the usual side branches.

    Mirrors the shape of the dummy demo workspace: a stream source, a
    context-derived x, a binning accumulator, and a fit hanging off it.
    """
    nodes: list[dict] = [
        {
            "node_id": "src",
            "op": "source.stream",
            "params": {"device_id": "trace1", "stream": "trace"},
        },
        {
            "node_id": "alt_x",
            "op": "source.context_field",
            "params": {"field": "other_hz"},
        },
        {
            "node_id": "ctx_x",
            "op": "source.context_field",
            "params": {"field": "freq_hz"},
        },
        {
            "node_id": "integral",
            "op": "trace.integrate",
            "inputs": {"trace": "src"},
            "params": {},
        },
        {
            "node_id": "bin",
            "op": "aggregate.bin_stats",
            "inputs": {"x": bin_x_input, "y": "integral"},
            "params": {"bin_count": bin_count, "x_min": 0.0, "x_max": 10.0},
        },
        {
            "node_id": "fit",
            "op": "fit.from_hist_agg",
            "inputs": {"hist": "bin"},
            "params": {"model": "gaussian", "every_n": fit_every_n},
        },
    ]
    nodes.extend(extra_nodes or [])
    return {
        "workspace_id": "ws",
        "enabled": True,
        "graph": {"nodes": nodes},
        "publish": {
            # Compilation prunes every node no published output depends on,
            # and a pruned node never accumulates anything — so the fit has
            # to be published for these tests to exercise it at all.
            "outputs": outputs
            if outputs is not None
            else [
                {"output_id": "bin_stats", "node_id": "bin"},
                {"output_id": "fit_1d", "node_id": "fit"},
            ]
        },
    }


def _dirty(old: dict, new: dict) -> set[str]:
    return StreamAnalysisProcess._workspace_dirty_nodes(  # noqa: SLF001
        compile_workspace_graph(old),
        compile_workspace_graph(new),
    )


class WorkspaceDirtyNodeTests(unittest.TestCase):
    """A node's accumulator survives iff it and all its ancestors are unchanged.

    Edits downstream of an accumulator, or off on a sibling branch, cannot
    change the meaning of what it has already collected.
    """

    def test_unchanged_graph_dirties_nothing(self) -> None:
        self.assertEqual(_dirty(_workspace(), _workspace()), set())

    def test_downstream_edit_leaves_the_accumulator_clean(self) -> None:
        """The reported bug: bumping the fit's every_n blanked the histogram."""
        dirty = _dirty(_workspace(), _workspace(fit_every_n=25))
        self.assertEqual(dirty, {"fit"})
        self.assertNotIn("bin", dirty)

    def test_accumulator_edit_dirties_its_descendants_too(self) -> None:
        dirty = _dirty(_workspace(), _workspace(bin_count=32))
        self.assertEqual(dirty, {"bin", "fit"})

    def test_unrelated_new_branch_dirties_only_itself(self) -> None:
        dirty = _dirty(
            _workspace(),
            _workspace(
                extra_nodes=[
                    {
                        "node_id": "peak",
                        "op": "trace.integrate",
                        "inputs": {"trace": "bg"},
                        "params": {},
                    },
                    {
                        "node_id": "bg",
                        "op": "trace.subtract_background",
                        "inputs": {"trace": "src"},
                        "params": {"bg_start_idx": 0, "bg_stop_idx": 10},
                    },
                ],
                outputs=[
                    {"output_id": "bin_stats", "node_id": "bin"},
                    {"output_id": "fit_1d", "node_id": "fit"},
                    {"output_id": "peak", "node_id": "peak"},
                ],
            ),
        )
        self.assertEqual(dirty, {"peak", "bg"})

    def test_publish_only_edit_dirties_nothing(self) -> None:
        """Adding an output or a label must not disturb the graph at all."""
        dirty = _dirty(
            _workspace(),
            _workspace(
                outputs=[
                    {
                        "output_id": "bin_stats",
                        "node_id": "bin",
                        "label": "Resonance vs frequency",
                    },
                    {"output_id": "fit_1d", "node_id": "fit"},
                    {"output_id": "pulse", "node_id": "integral"},
                ]
            ),
        )
        self.assertEqual(dirty, set())

    def test_rewiring_inputs_with_identical_params_is_dirty(self) -> None:
        """Guards the `inputs` comparison.

        Repointing the bin node's x source leaves its params byte-identical,
        so a params-only check would keep a histogram binned on the old
        quantity while labelling it with the new one.
        """
        dirty = _dirty(_workspace(), _workspace(bin_x_input="alt_x"))
        self.assertEqual(dirty, {"bin", "fit"})

    def test_renamed_node_is_treated_as_new(self) -> None:
        old = _workspace()
        new = _workspace()
        for node in new["graph"]["nodes"]:
            if node["node_id"] == "bin":
                node["node_id"] = "bin2"
            if node["node_id"] == "fit":
                node["inputs"] = {"hist": "bin2"}
        new["publish"]["outputs"] = [
            {"output_id": "bin_stats", "node_id": "bin2"},
            {"output_id": "fit_1d", "node_id": "fit"},
        ]
        dirty = _dirty(old, new)
        self.assertIn("bin2", dirty)
        self.assertIn("fit", dirty)


class WorkspaceReusedNodeStateTests(unittest.TestCase):
    """`_workspace_reused_node_state` is the last gate before a live
    accumulator is carried into a new revision, so it re-checks the node
    itself rather than trusting the dirty set it is handed."""

    def _runtime(self, config: dict):
        proc = _make_process()
        runtime, _reset = proc._put_workspace_from_config(  # noqa: SLF001
            config, expected_revision=None, mark_dirty=False, publish=False
        )
        return proc, runtime

    def test_reuse_is_refused_for_a_changed_node_even_if_called_clean(self) -> None:
        old_compiled = compile_workspace_graph(_workspace())
        new_compiled = compile_workspace_graph(_workspace(bin_count=32))
        proc, runtime = self._runtime(_workspace())
        runtime.node_state["bin"] = BinStatsState.from_params(
            {"bin_count": 8, "x_min": 0.0, "x_max": 10.0}
        )
        reused = StreamAnalysisProcess._workspace_reused_node_state(  # noqa: SLF001
            existing=runtime,
            compiled=new_compiled,
            dirty_nodes=set(),
        )
        self.assertEqual(
            old_compiled.nodes["bin"].inputs, new_compiled.nodes["bin"].inputs
        )
        self.assertNotIn("bin", reused)


def _make_process() -> StreamAnalysisProcess:
    """A process with just enough wired up to install workspaces.

    Follows `test_stream_analysis_memory_bounds`: bypass `__init__` and set
    the handful of attributes the apply path touches, stubbing the
    publish/index side effects.
    """
    proc = StreamAnalysisProcess.__new__(StreamAnalysisProcess)
    proc._workspaces = {}  # noqa: SLF001
    proc._latest_output_payloads = {}  # noqa: SLF001
    proc._workspace_store_path = None  # noqa: SLF001
    proc._workspace_store_dirty = False  # noqa: SLF001
    proc.published_status = []
    # No manager: the axis resolver degrades to the sample index without
    # attempting any device RPC, which is what an offline unit harness wants.
    proc._manager = None  # noqa: SLF001
    proc._stream_axis_cache = {}  # noqa: SLF001
    proc._stream_axis_ttl_s = 60.0  # noqa: SLF001
    proc._axis_rpc_timeout_ms = 1500  # noqa: SLF001
    proc._rebuild_stream_index = lambda: None  # noqa: SLF001
    proc._reconcile_trace_writers = lambda: None  # noqa: SLF001
    proc._publish_workspace_status = (  # noqa: SLF001
        lambda workspace_id, *, status, details=None: proc.published_status.append(
            (workspace_id, status, details)
        )
    )
    return proc


class PutWorkspaceStateReuseTests(unittest.TestCase):
    def _put(self, proc: StreamAnalysisProcess, config: dict):
        return proc._put_workspace_from_config(  # noqa: SLF001
            config, expected_revision=None, mark_dirty=False, publish=True
        )

    def _filled_process(self) -> tuple[StreamAnalysisProcess, BinStatsState]:
        proc = _make_process()
        runtime, reset = self._put(proc, _workspace())
        # Every stateful node is reset on first install — there is nothing
        # to carry over — and the histogram starts empty.
        self.assertIn("bin", reset)
        state = runtime.node_state["bin"]
        for x_value, y_value in [(1.0, 10.0), (2.0, 20.0), (3.0, 30.0)]:
            state.update_sample(x_value, y_value)
        return proc, state

    def test_downstream_edit_keeps_the_same_bin_state_object(self) -> None:
        proc, state = self._filled_process()
        before = state.payload(last_sample=None)["count"]

        runtime, reset = self._put(proc, _workspace(fit_every_n=25))

        self.assertEqual(reset, ["fit"])
        self.assertIs(runtime.node_state["bin"], state)
        self.assertEqual(runtime.node_state["bin"].payload(last_sample=None)["count"], before)
        self.assertEqual(sum(before), 3)

    def test_publish_only_edit_keeps_every_accumulator(self) -> None:
        proc, state = self._filled_process()
        runtime, reset = self._put(
            proc,
            _workspace(
                outputs=[
                    {"output_id": "bin_stats", "node_id": "bin", "label": "Resonance"},
                    {"output_id": "fit_1d", "node_id": "fit"},
                    {"output_id": "pulse", "node_id": "integral"},
                ]
            ),
        )
        self.assertEqual(reset, [])
        self.assertIs(runtime.node_state["bin"], state)

    def test_accumulator_edit_resets_it_and_its_descendants(self) -> None:
        proc, state = self._filled_process()
        runtime, reset = self._put(proc, _workspace(bin_count=32))

        self.assertEqual(reset, ["bin", "fit"])
        self.assertIsNot(runtime.node_state["bin"], state)
        self.assertEqual(
            sum(runtime.node_state["bin"].payload(last_sample=None)["count"]), 0
        )

    def test_reset_node_ids_are_published_on_the_status_event(self) -> None:
        proc, _state = self._filled_process()
        proc.published_status.clear()
        self._put(proc, _workspace(bin_count=32))

        workspace_id, status, details = proc.published_status[-1]
        self.assertEqual((workspace_id, status), ("ws", "updated"))
        self.assertEqual(details["state_reset_node_ids"], ["bin", "fit"])

    def test_reset_node_drops_its_snapshot_but_a_kept_node_keeps_one(self) -> None:
        """A reconnecting client must not hydrate a pre-apply histogram."""
        proc, _state = self._filled_process()
        proc._latest_output_payloads[("ws", "bin_stats")] = {  # noqa: SLF001
            "workspace_id": "ws",
            "output_id": "bin_stats",
            "node_id": "bin",
            "kind": "hist_agg",
        }

        self._put(proc, _workspace(fit_every_n=25))
        self.assertIn(("ws", "bin_stats"), proc._latest_output_payloads)  # noqa: SLF001

        self._put(proc, _workspace(fit_every_n=25, bin_count=32))
        self.assertNotIn(("ws", "bin_stats"), proc._latest_output_payloads)  # noqa: SLF001

    def test_revision_advances_across_a_graph_change(self) -> None:
        """Optimistic concurrency depends on a monotonic revision.

        The old all-or-nothing gate dropped `existing` entirely on any graph
        edit, restarting the revision at 1 and making the client's next
        `expected_revision` disagree with the runtime.
        """
        proc = _make_process()
        first, _ = self._put(proc, _workspace())
        second, _ = self._put(proc, _workspace(bin_count=32))
        third, _ = self._put(proc, _workspace(bin_count=64))

        self.assertEqual(
            [first.revision, second.revision, third.revision], [1, 2, 3]
        )
        self.assertEqual(third.etag, "ws:3")


if __name__ == "__main__":
    unittest.main()

# ruff: noqa: E402

import sys
import types
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.adaptive.controller import create_adaptive_controller


class _FakeLearner1D:
    instances: list["_FakeLearner1D"] = []

    def __init__(
        self,
        func,
        bounds,
        *,
        loss_per_interval=None,
    ) -> None:
        self.func = func
        self.bounds = bounds
        self.loss_per_interval = loss_per_interval
        self.loss_value = 1.0
        self.last_tell = None
        type(self).instances.append(self)

    def ask(self, n: int, tell_pending: bool = False):
        del n, tell_pending
        return [0.5]

    def tell(self, x, y) -> None:
        self.last_tell = (x, y)

    def loss(self) -> float:
        return self.loss_value


class _FakeAverageLearner1D(_FakeLearner1D):
    instances: list["_FakeAverageLearner1D"] = []

    def __init__(self, func, bounds, *, loss_per_interval=None, **kwargs) -> None:
        super().__init__(func, bounds, loss_per_interval=loss_per_interval)
        self.extra_kwargs = dict(kwargs)

    def ask(self, n: int, tell_pending: bool = False):
        del n, tell_pending
        return [(0, 0.5)]


class _FakeAdaptiveModule:
    def __init__(self) -> None:
        self.curvature_calls: list[dict[str, float]] = []
        learner1d_ns = types.SimpleNamespace(
            curvature_loss_function=self._curvature_loss_function
        )
        self.learner = types.SimpleNamespace(learner1D=learner1d_ns)
        self.Learner1D = _FakeLearner1D
        self.AverageLearner1D = _FakeAverageLearner1D

    def _curvature_loss_function(self, **kwargs):
        self.curvature_calls.append(dict(kwargs))

        def _loss(*args, **kw):
            del args, kw
            return 0.0

        return _loss


class AdaptiveControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeLearner1D.instances.clear()
        _FakeAverageLearner1D.instances.clear()
        self._orig_adaptive = sys.modules.get("adaptive")
        self._fake_module = _FakeAdaptiveModule()
        sys.modules["adaptive"] = self._fake_module

    def tearDown(self) -> None:
        if self._orig_adaptive is None:
            sys.modules.pop("adaptive", None)
        else:
            sys.modules["adaptive"] = self._orig_adaptive

    def _make_controller(self, *, repeats: int = 1, config: dict | None = None):
        controller_spec = {"kind": "adaptive.adaptive_grid_1d"}
        if config is not None:
            controller_spec["config"] = config
        return create_adaptive_controller(
            controller_spec=controller_spec,
            space={
                "x": {
                    "type": "float",
                    "min": 0.0,
                    "max": 1.0,
                }
            },
            repeats=repeats,
        )

    def test_controller_defaults_to_curvature_loss(self) -> None:
        controller = self._make_controller()
        self.assertEqual(controller.status()["loss_kind"], "curvature")
        self.assertEqual(controller.status()["learner_kind"], "learner1d")
        self.assertEqual(len(self._fake_module.curvature_calls), 1)
        self.assertEqual(self._fake_module.curvature_calls[0], {})
        learner = _FakeLearner1D.instances[-1]
        self.assertIsNotNone(learner.loss_per_interval)

    def test_controller_can_use_default_loss(self) -> None:
        controller = self._make_controller(config={"loss": {"kind": "default"}})
        self.assertEqual(controller.status()["loss_kind"], "default")
        self.assertEqual(self._fake_module.curvature_calls, [])
        learner = _FakeLearner1D.instances[-1]
        self.assertIsNone(learner.loss_per_interval)

    def test_repeats_default_to_average_learner(self) -> None:
        controller = self._make_controller(repeats=3)
        self.assertEqual(controller.status()["learner_kind"], "average_learner1d")
        learner = _FakeAverageLearner1D.instances[-1]
        self.assertEqual(learner.extra_kwargs["min_samples"], 3)
        self.assertEqual(learner.extra_kwargs["max_samples"], 3)

    def test_controller_passes_curvature_params(self) -> None:
        self._make_controller(
            config={
                "loss": {
                    "kind": "curvature",
                    "params": {
                        "area_factor": 2.0,
                        "euclid_factor": 0.1,
                        "horizontal_factor": 0.05,
                    },
                }
            }
        )
        self.assertEqual(
            self._fake_module.curvature_calls[-1],
            {
                "area_factor": 2.0,
                "euclid_factor": 0.1,
                "horizontal_factor": 0.05,
            },
        )

    def test_min_loss_triggers_convergence(self) -> None:
        controller = self._make_controller(config={"min_loss": 0.1})
        learner = _FakeLearner1D.instances[-1]
        learner.loss_value = 0.05
        controller.tell(
            {"params_raw": {"x": 0.5}, "params": {"x": 0.5}, "meta": {}},
            {"ok": True, "params": {"x": 0.5}, "score": 1.23},
        )
        self.assertTrue(controller.should_stop())
        status = controller.status()
        self.assertEqual(status["stop_reason"], "min_loss")
        self.assertEqual(status["loss"], 0.05)

    def test_min_loss_does_not_trigger_when_above_threshold(self) -> None:
        controller = self._make_controller(config={"min_loss": 0.1})
        learner = _FakeLearner1D.instances[-1]
        learner.loss_value = 0.25
        controller.tell(
            {"params_raw": {"x": 0.5}, "params": {"x": 0.5}, "meta": {}},
            {"ok": True, "params": {"x": 0.5}, "score": 1.23},
        )
        self.assertFalse(controller.should_stop())
        self.assertEqual(controller.status()["loss"], 0.25)

    def test_average_controller_uses_average_learner_and_updates_loss(self) -> None:
        controller = self._make_controller(repeats=3, config={"min_loss": 0.2})
        learner = _FakeAverageLearner1D.instances[-1]
        learner.loss_value = 0.1
        controller.tell(
            {
                "params_raw": {"x": 0.5},
                "params": {"x": 0.5},
                "meta": {"seed": 7},
            },
            {
                "ok": True,
                "params": {"x": 0.5},
                "metrics": {"signal": 2.0},
                "replicates": {"signal": [1.0, 2.0, 3.0]},
                "score": 2.0,
            },
        )
        self.assertEqual(
            learner.last_tell,
            ((9, 0.5), 3.0),
        )
        self.assertTrue(controller.should_stop())

    def test_invalid_loss_config_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._make_controller(config={"loss": {"kind": "unknown"}})
        with self.assertRaises(ValueError):
            self._make_controller(
                config={"loss": {"kind": "curvature", "params": {"area_factor": -1.0}}}
            )
        with self.assertRaises(ValueError):
            self._make_controller(config={"learner_kind": "bad"})
        with self.assertRaises(ValueError):
            self._make_controller(
                repeats=3, config={"min_samples": 5, "max_samples": 4}
            )
        with self.assertRaises(ValueError):
            self._make_controller(config={"min_loss": -0.1})


if __name__ == "__main__":
    unittest.main()

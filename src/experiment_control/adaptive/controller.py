from __future__ import annotations

import inspect
import math
from typing import Any


def create_adaptive_controller(
    *,
    controller_spec: dict[str, Any],
    space: dict[str, Any],
    repeats: int,
) -> Any:
    kind = str(controller_spec.get("kind", "") or "").strip()
    if not kind:
        raise TypeError("adaptive.controller.kind is required")
    if kind != "adaptive.adaptive_grid_1d":
        raise NotImplementedError(f"adaptive controller {kind!r} is not supported in v1")
    return _AdaptiveGrid1DController(
        controller_spec=controller_spec,
        space=space,
        repeats=repeats,
    )


class _AdaptiveGrid1DController:
    def __init__(
        self,
        *,
        controller_spec: dict[str, Any],
        space: dict[str, Any],
        repeats: int,
    ) -> None:
        config_raw = controller_spec.get("config", {}) or {}
        if not isinstance(config_raw, dict):
            raise TypeError("adaptive.controller.config must be a dict")
        if len(space) != 1:
            raise ValueError("adaptive.adaptive_grid_1d requires exactly one space parameter")
        self._param_name, self._param_spec = next(iter(space.items()))
        if not isinstance(self._param_spec, dict):
            raise TypeError("adaptive.adaptive_grid_1d parameter spec must be a dict")
        param_type = str(self._param_spec.get("type", "") or "").strip().lower()
        if param_type not in {"float", "int"}:
            raise ValueError(
                "adaptive.adaptive_grid_1d requires a numeric space parameter (float or int)"
            )
        try:
            lower = float(self._param_spec.get("min"))
            upper = float(self._param_spec.get("max"))
        except Exception as exc:
            raise TypeError(
                "adaptive.adaptive_grid_1d requires numeric min/max bounds"
            ) from exc
        if upper <= lower:
            raise ValueError("adaptive.adaptive_grid_1d requires max > min")
        self._bounds = (lower, upper)
        self._repeats = max(1, int(repeats))
        self._learner_kind = self._parse_learner_kind(config_raw, self._repeats)
        self._uses_average = self._learner_kind == "average_learner1d"
        self._average_kwargs = self._parse_average_kwargs(config_raw, self._repeats)
        self._loss_kind, self._loss_params = self._parse_loss_config(config_raw)
        self._min_loss = self._parse_min_loss(config_raw)
        self._trial_index = 0
        self._last_loss: float | None = None
        self._converged = False
        self._stop_reason: str | None = None
        self._learner = self._build_learner()

    def _build_learner(self) -> Any:
        try:
            import adaptive as adaptive_mod
        except ImportError as exc:
            raise RuntimeError(
                "adaptive.adaptive_grid_1d requires the optional 'adaptive' package"
            ) from exc

        loss_per_interval = self._build_loss_per_interval(adaptive_mod)

        if self._uses_average:
            def _placeholder(seed_x: tuple[int, float]) -> float:
                del seed_x
                return 0.0

            return self._construct_learner(
                adaptive_mod.AverageLearner1D,
                _placeholder,
                self._bounds,
                loss_per_interval=loss_per_interval,
                **self._average_kwargs,
            )

        def _placeholder(x: float) -> float:
            del x
            return 0.0

        return self._construct_learner(
            adaptive_mod.Learner1D,
            _placeholder,
            self._bounds,
            loss_per_interval=loss_per_interval,
        )

    @staticmethod
    def _parse_learner_kind(config: dict[str, Any], repeats: int) -> str:
        raw = config.get("learner_kind")
        if raw is None:
            return "average_learner1d" if repeats > 1 else "learner1d"
        kind = str(raw).strip().lower()
        if kind not in {"learner1d", "average_learner1d"}:
            raise ValueError(
                "adaptive.controller.config.learner_kind must be 'learner1d' or "
                "'average_learner1d'"
            )
        return kind

    @staticmethod
    def _parse_average_kwargs(config: dict[str, Any], repeats: int) -> dict[str, Any]:
        if repeats <= 1:
            return {}
        params: dict[str, Any] = {
            "min_samples": repeats,
            "max_samples": repeats,
        }
        for name in ("min_samples", "max_samples"):
            if name not in config:
                continue
            try:
                value = int(config[name])
            except Exception as exc:
                raise TypeError(
                    f"adaptive.controller.config.{name} must be a positive integer"
                ) from exc
            if value < 1:
                raise ValueError(
                    f"adaptive.controller.config.{name} must be a positive integer"
                )
            params[name] = value
        for name in ("delta", "alpha", "neighbor_sampling", "min_error"):
            if name not in config:
                continue
            try:
                value = float(config[name])
            except Exception as exc:
                raise TypeError(
                    f"adaptive.controller.config.{name} must be finite numeric"
                ) from exc
            if not math.isfinite(value):
                raise ValueError(
                    f"adaptive.controller.config.{name} must be finite numeric"
                )
            params[name] = value
        if params["max_samples"] < params["min_samples"]:
            raise ValueError(
                "adaptive.controller.config.max_samples must be >= min_samples"
            )
        return params

    @staticmethod
    def _parse_min_loss(config: dict[str, Any]) -> float | None:
        raw = config.get("min_loss")
        if raw is None:
            return None
        try:
            value = float(raw)
        except Exception as exc:
            raise TypeError("adaptive.controller.config.min_loss must be numeric") from exc
        if value < 0:
            raise ValueError("adaptive.controller.config.min_loss must be >= 0")
        return value

    @staticmethod
    def _parse_loss_config(config: dict[str, Any]) -> tuple[str, dict[str, float]]:
        raw = config.get("loss")
        if raw is None:
            return "curvature", {}
        if not isinstance(raw, dict):
            raise TypeError("adaptive.controller.config.loss must be a dict")
        kind = str(raw.get("kind", "curvature") or "curvature").strip().lower()
        if kind not in {"default", "curvature"}:
            raise ValueError(
                f"unsupported adaptive.controller.config.loss.kind {kind!r}"
            )
        params_raw = raw.get("params", {}) or {}
        if not isinstance(params_raw, dict):
            raise TypeError("adaptive.controller.config.loss.params must be a dict")
        params: dict[str, float] = {}
        if kind == "curvature":
            for name in ("area_factor", "euclid_factor", "horizontal_factor"):
                if name not in params_raw:
                    continue
                try:
                    value = float(params_raw[name])
                except Exception as exc:
                    raise TypeError(
                        f"adaptive.controller.config.loss.params.{name} must be numeric"
                    ) from exc
                if value < 0:
                    raise ValueError(
                        f"adaptive.controller.config.loss.params.{name} must be >= 0"
                    )
                params[name] = value
        elif params_raw:
            raise ValueError(
                "adaptive.controller.config.loss.params is only supported for curvature loss in v1"
            )
        return kind, params

    def _build_loss_per_interval(self, adaptive_mod: Any) -> Any:
        if self._loss_kind == "default":
            return None
        curvature_factory = self._resolve_curvature_loss_factory(adaptive_mod)
        try:
            return curvature_factory(**self._loss_params)
        except Exception as exc:
            raise RuntimeError(
                "failed to construct adaptive curvature loss function"
            ) from exc

    @staticmethod
    def _resolve_curvature_loss_factory(adaptive_mod: Any) -> Any:
        learner_ns = getattr(adaptive_mod, "learner", None)
        learner1d_ns = getattr(learner_ns, "learner1D", None)
        factory = getattr(learner1d_ns, "curvature_loss_function", None)
        if not callable(factory):
            raise RuntimeError(
                "adaptive package does not expose learner.learner1D.curvature_loss_function"
            )
        return factory

    @staticmethod
    def _construct_learner(
        factory: Any,
        placeholder: Any,
        bounds: tuple[float, float],
        *,
        loss_per_interval: Any,
        **extra_kwargs: Any,
    ) -> Any:
        kwargs = dict(extra_kwargs)
        if loss_per_interval is None:
            return factory(placeholder, bounds, **kwargs)
        try:
            params = inspect.signature(factory).parameters
        except Exception:
            params = {}
        if "loss_per_interval" in params:
            kwargs["loss_per_interval"] = loss_per_interval
        return factory(placeholder, bounds, **kwargs)

    @staticmethod
    def _unwrap_candidates(raw: Any) -> list[Any]:
        if isinstance(raw, tuple) and len(raw) == 2 and isinstance(raw[0], list):
            return list(raw[0])
        if isinstance(raw, list):
            return list(raw)
        return [raw]

    def suggest(self) -> dict[str, Any]:
        raw = self._learner.ask(1, tell_pending=False)
        candidates = self._unwrap_candidates(raw)
        if not candidates:
            raise RuntimeError("adaptive learner returned no candidates")
        candidate = candidates[0]
        meta: dict[str, Any] = {"trial_index": self._trial_index}
        if self._last_loss is not None:
            meta["adaptive_loss"] = self._last_loss
        if self._uses_average:
            if not isinstance(candidate, tuple) or len(candidate) != 2:
                raise RuntimeError(
                    "AverageLearner1D returned an unexpected candidate shape"
                )
            seed, x_value = candidate
            meta["seed"] = int(seed)
        else:
            x_value = candidate
        self._trial_index += 1
        return {
            "params_raw": {self._param_name: float(x_value)},
            "meta": meta,
        }

    def tell(self, proposal: dict[str, Any], trial: dict[str, Any]) -> None:
        if not bool(trial.get("ok")):
            return
        params = trial.get("params") or {}
        if not isinstance(params, dict):
            return
        if self._param_name not in params:
            return
        try:
            x_value = float(params[self._param_name])
        except Exception:
            return

        if self._uses_average:
            metric_name = self._primary_metric_name(trial)
            values = self._replicate_values(trial, metric_name)
            if not values:
                score = trial.get("score")
                if score is not None:
                    try:
                        values = [float(score)]
                    except Exception:
                        values = []
            if not values:
                return
            meta = proposal.get("meta") or {}
            seed_start = 0
            if isinstance(meta, dict) and "seed" in meta:
                try:
                    seed_start = int(meta["seed"])
                except Exception:
                    seed_start = 0
            for offset, value in enumerate(values):
                self._learner.tell((seed_start + offset, x_value), value)
            self._update_convergence_from_loss()
            return

        score = trial.get("score")
        if score is None:
            return
        try:
            y_value = float(score)
        except Exception:
            return
        self._learner.tell(x_value, y_value)
        self._update_convergence_from_loss()

    def should_stop(self) -> bool:
        return self._converged

    def status(self) -> dict[str, Any]:
        return {
            "loss": self._last_loss,
            "converged": self._converged,
            "stop_reason": self._stop_reason,
            "loss_kind": self._loss_kind,
            "learner_kind": self._learner_kind,
        }

    def _update_convergence_from_loss(self) -> None:
        loss_fn = getattr(self._learner, "loss", None)
        if not callable(loss_fn):
            return
        try:
            raw_loss = loss_fn()
        except Exception:
            return
        try:
            loss_value = float(raw_loss)
        except Exception:
            return
        if not math.isfinite(loss_value):
            return
        self._last_loss = loss_value
        if self._min_loss is not None and loss_value <= self._min_loss:
            self._converged = True
            self._stop_reason = "min_loss"

    @staticmethod
    def _primary_metric_name(trial: dict[str, Any]) -> str | None:
        metrics = trial.get("metrics") or {}
        if isinstance(metrics, dict) and metrics:
            return next(iter(metrics.keys()))
        replicates = trial.get("replicates") or {}
        if isinstance(replicates, dict) and replicates:
            return next(iter(replicates.keys()))
        return None

    @staticmethod
    def _replicate_values(trial: dict[str, Any], metric_name: str | None) -> list[float]:
        if not metric_name:
            return []
        replicates = trial.get("replicates") or {}
        if not isinstance(replicates, dict):
            return []
        raw_values = replicates.get(metric_name)
        if not isinstance(raw_values, list):
            return []
        out: list[float] = []
        for item in raw_values:
            if item is None:
                continue
            try:
                out.append(float(item))
            except Exception:
                continue
        return out

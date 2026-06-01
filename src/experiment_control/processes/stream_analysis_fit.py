from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from typing import Any

import numpy as np

try:
    curve_fit = getattr(import_module("scipy.optimize"), "curve_fit")
except Exception:
    curve_fit = None

Json = dict[str, Any]


def _normalize_int(raw: Any) -> int | None:
    try:
        return int(raw)
    except Exception:
        return None


def _normalize_float(raw: Any) -> float | None:
    try:
        value = float(raw)
    except Exception:
        return None
    if not math.isfinite(value):
        return None
    return value


def _coerce_trace(trace_raw: Any) -> np.ndarray | None:
    if trace_raw is None:
        return None
    arr = np.asarray(trace_raw)
    if arr.ndim == 0:
        arr = arr.reshape(1)
    arr = arr.reshape(-1)
    try:
        arr = arr.astype(np.float64, copy=False)
    except Exception:
        return None
    if arr.size <= 0:
        return np.asarray([], dtype=np.float64)
    return arr


def _gate_open(gate_raw: Any, *, default: bool = True) -> bool:
    if gate_raw is None:
        return bool(default)
    if isinstance(gate_raw, bool):
        return gate_raw
    gate = _normalize_float(gate_raw)
    if gate is None:
        return False
    return bool(gate != 0.0)


def _parse_fit_model(raw: Any) -> str:
    model = str(raw if raw is not None else "gaussian").strip().lower()
    if model in {"gaussian", "lorentzian"}:
        return model
    raise ValueError("fit.curve_1d model must be one of gaussian, lorentzian")


def _parse_fit_baseline_mode(raw: Any) -> str:
    mode = str(raw if raw is not None else "none").strip().lower()
    if mode in {"none", "constant", "linear"}:
        return mode
    raise ValueError("fit.curve_1d baseline_mode must be one of none, constant, linear")


def _validate_fit_curve_params(
    params: Json,
) -> tuple[str, str, int, float | None, int | None]:
    model = _parse_fit_model(params.get("model"))
    baseline_mode = _parse_fit_baseline_mode(params.get("baseline_mode"))
    every_n = _normalize_int(params.get("every_n"))
    if every_n is None:
        every_n = 1
    if every_n <= 0:
        raise ValueError("fit.curve_1d requires every_n >= 1")
    sigma_y = _normalize_float(params.get("sigma_y"))
    if sigma_y is not None and sigma_y <= 0:
        raise ValueError("fit.curve_1d requires sigma_y > 0 when provided")
    dense_eval_points = _normalize_int(params.get("dense_eval_points"))
    if dense_eval_points is not None and dense_eval_points < 2:
        raise ValueError("fit.curve_1d requires dense_eval_points >= 2 when provided")
    return model, baseline_mode, int(every_n), sigma_y, dense_eval_points


@dataclass
class FitCurve1DState:
    model: str
    baseline_mode: str
    every_n: int
    sigma_y: float | None = None
    dense_eval_points: int | None = None
    sample_count: int = 0
    last_fit: dict[str, Any] | None = None
    last_fit_attempt_ts_mono: float | None = None
    last_fit_success_ts_mono: float | None = None

    @classmethod
    def from_params(cls, params: Json) -> FitCurve1DState:
        model, baseline_mode, every_n, sigma_y, dense_eval_points = (
            _validate_fit_curve_params(params)
        )
        return cls(
            model=model,
            baseline_mode=baseline_mode,
            every_n=every_n,
            sigma_y=sigma_y,
            dense_eval_points=dense_eval_points,
        )

    def reset(self) -> None:
        self.sample_count = 0
        self.last_fit = None
        self.last_fit_attempt_ts_mono = None
        self.last_fit_success_ts_mono = None

    def mark_fit_attempt(self) -> None:
        self.last_fit_attempt_ts_mono = time.monotonic()
        self._refresh_last_fit_metadata()

    def mark_fit_success(self, fit_result: dict[str, Any]) -> None:
        self.last_fit_success_ts_mono = time.monotonic()
        self.last_fit = fit_result
        self._refresh_last_fit_metadata()

    def _refresh_last_fit_metadata(self) -> None:
        if self.last_fit is None:
            return
        self.last_fit["last_fit_attempt_ts_mono"] = self.last_fit_attempt_ts_mono
        self.last_fit["last_fit_success_ts_mono"] = self.last_fit_success_ts_mono


def _fit_curve_build_models(
    *,
    model: str,
    baseline_mode: str,
) -> tuple[Any, Any, list[str]]:
    def _model_gaussian(x: np.ndarray, amp: float, center: float, sigma: float) -> np.ndarray:
        sigma_eff = max(abs(float(sigma)), 1e-18)
        z = (x - float(center)) / sigma_eff
        return float(amp) * np.exp(-0.5 * z * z)

    def _model_lorentzian(x: np.ndarray, amp: float, center: float, gamma: float) -> np.ndarray:
        gamma_eff = max(abs(float(gamma)), 1e-18)
        d = x - float(center)
        return float(amp) * (gamma_eff * gamma_eff) / (d * d + gamma_eff * gamma_eff)

    core: Callable[..., np.ndarray]
    if model == "gaussian":
        core = _model_gaussian
        names = ["amplitude", "center", "sigma"]
    else:
        core = _model_lorentzian
        names = ["amplitude", "center", "gamma"]

    if baseline_mode == "none":
        return core, core, names

    if baseline_mode == "constant":
        def constant_func(x: np.ndarray, *p: float) -> np.ndarray:
            return core(x, float(p[0]), float(p[1]), float(p[2])) + float(p[3])

        return constant_func, constant_func, names + ["baseline_const"]

    def linear_func(x: np.ndarray, *p: float) -> np.ndarray:
        return (
            core(x, float(p[0]), float(p[1]), float(p[2]))
            + float(p[3])
            + float(p[4]) * x
        )

    return linear_func, linear_func, names + ["baseline_const", "baseline_slope"]


def _fit_curve_initial_guess(
    *,
    x: np.ndarray,
    y: np.ndarray,
    model: str,
    baseline_mode: str,
) -> np.ndarray:
    x_min = float(np.min(x))
    x_max = float(np.max(x))
    span = max(x_max - x_min, 1e-12)
    y_med = float(np.median(y))
    y_max = float(np.max(y))
    y_min = float(np.min(y))
    amp_guess = y_max - y_med
    if abs(amp_guess) < 1e-12:
        amp_guess = y_max - y_min
    if abs(amp_guess) < 1e-12:
        amp_guess = float(np.max(np.abs(y))) if y.size > 0 else 1.0
    if abs(amp_guess) < 1e-12:
        amp_guess = 1.0
    center_guess = float(x[int(np.argmax(y))])
    width_guess = span / 8.0 if model == "gaussian" else span / 10.0
    width_guess = max(width_guess, 1e-9)
    if baseline_mode == "none":
        return np.asarray([amp_guess, center_guess, width_guess], dtype=np.float64)
    if baseline_mode == "constant":
        return np.asarray([amp_guess, center_guess, width_guess, y_med], dtype=np.float64)
    slope_guess = (float(y[-1]) - float(y[0])) / span if y.size >= 2 else 0.0
    return np.asarray(
        [amp_guess, center_guess, width_guess, y_med, slope_guess], dtype=np.float64
    )


def _fit_curve_prepare_xy(
    *, x_raw: Any, y_raw: Any
) -> tuple[np.ndarray, np.ndarray] | None:
    x = _coerce_trace(x_raw)
    y = _coerce_trace(y_raw)
    if x is None or y is None:
        return None
    if int(x.size) != int(y.size):
        return None
    if int(x.size) < 4:
        return None
    x_arr = np.asarray(x, dtype=np.float64).reshape(-1)
    y_arr = np.asarray(y, dtype=np.float64).reshape(-1)
    mask = np.isfinite(x_arr) & np.isfinite(y_arr)
    if not np.any(mask):
        return None
    x_arr = x_arr[mask]
    y_arr = y_arr[mask]
    if int(x_arr.size) < 4:
        return None
    return x_arr, y_arr


def _fit_curve_dense_eval(
    *,
    eval_func: Callable[..., np.ndarray],
    popt: np.ndarray,
    x: np.ndarray,
    dense_eval_points: int | None,
) -> tuple[np.ndarray | None, np.ndarray | None]:
    if dense_eval_points is None or dense_eval_points < 2:
        return None, None
    x_dense = np.linspace(
        float(np.min(x)),
        float(np.max(x)),
        int(dense_eval_points),
        dtype=np.float64,
    )
    yhat_dense = np.asarray(eval_func(x_dense, *popt), dtype=np.float64).reshape(-1)
    return x_dense, yhat_dense


def _fit_curve_param_stderr(
    *, pcov: Any, param_names: list[str]
) -> dict[str, float]:
    stderr: dict[str, float] = {}
    if not isinstance(pcov, np.ndarray) or pcov.ndim != 2:
        return stderr
    diag = np.diag(pcov)
    for i, name in enumerate(param_names):
        if i >= int(diag.size):
            break
        var = float(diag[i])
        if math.isfinite(var) and var >= 0.0:
            stderr[name] = float(math.sqrt(var))
    return stderr


def _fit_curve_reduced_chi2(
    *,
    y: np.ndarray,
    yhat: np.ndarray,
    param_count: int,
    sigma_y: float | None,
    sigma_trace_raw: Any,
) -> float | None:
    if y.size <= 0 or y.size != yhat.size:
        return None
    dof = int(y.size) - int(param_count)
    if dof <= 0:
        return None
    resid = y - yhat
    sigma_vec: np.ndarray | None = None
    sigma_trace = _coerce_trace(sigma_trace_raw)
    if sigma_trace is not None and int(sigma_trace.size) == int(y.size):
        sigma_vec = np.asarray(sigma_trace, dtype=np.float64).reshape(-1)
    elif sigma_y is not None and sigma_y > 0:
        sigma_vec = np.full(int(y.size), float(sigma_y), dtype=np.float64)
    if sigma_vec is None:
        return None
    valid = np.isfinite(sigma_vec) & (sigma_vec > 0)
    if not np.any(valid):
        return None
    w_resid = resid[valid] / sigma_vec[valid]
    if w_resid.size <= 0:
        return None
    chi2 = float(np.sum(w_resid * w_resid, dtype=np.float64))
    if not math.isfinite(chi2):
        return None
    return float(chi2 / float(dof))


def _fit_curve_run(
    *,
    x_raw: Any,
    y_raw: Any,
    model: str,
    baseline_mode: str,
    sigma_y: float | None = None,
    sigma_trace_raw: Any = None,
    dense_eval_points: int | None = None,
) -> dict[str, Any] | None:
    xy = _fit_curve_prepare_xy(x_raw=x_raw, y_raw=y_raw)
    if xy is None:
        return None
    x, y = xy
    if curve_fit is None:
        return None

    fit_func, eval_func, param_names = _fit_curve_build_models(
        model=model,
        baseline_mode=baseline_mode,
    )
    p0 = _fit_curve_initial_guess(
        x=x,
        y=y,
        model=model,
        baseline_mode=baseline_mode,
    )
    try:
        popt, pcov = curve_fit(fit_func, x, y, p0=p0, maxfev=8000)
    except Exception:
        return None
    yhat = np.asarray(eval_func(x, *popt), dtype=np.float64).reshape(-1)
    x_dense, yhat_dense = _fit_curve_dense_eval(
        eval_func=eval_func,
        popt=popt,
        x=x,
        dense_eval_points=dense_eval_points,
    )
    params: dict[str, Any] = {
        name: float(val)
        for name, val in zip(param_names, popt.tolist(), strict=False)
    }
    stderr = _fit_curve_param_stderr(pcov=pcov, param_names=param_names)
    reduced_chi2 = _fit_curve_reduced_chi2(
        y=y,
        yhat=yhat,
        param_count=len(param_names),
        sigma_y=sigma_y,
        sigma_trace_raw=sigma_trace_raw,
    )
    if reduced_chi2 is not None:
        params["reduced_chi2"] = reduced_chi2
    params["model"] = model
    params["baseline_mode"] = baseline_mode
    out: dict[str, Any] = {
        "x": x,
        "yhat": yhat,
        "params": params,
        "stderr": stderr,
    }
    if x_dense is not None and yhat_dense is not None and x_dense.size == yhat_dense.size:
        out["x_dense"] = x_dense
        out["yhat_dense"] = yhat_dense
    return out


def execute_fit_curve_1d(
    *,
    state: FitCurve1DState,
    x_raw: Any,
    y_raw: Any,
    gate_raw: Any,
) -> dict[str, Any] | None:
    if not _gate_open(gate_raw, default=True):
        return state.last_fit
    state.sample_count += 1
    every_n = max(1, int(state.every_n))
    should_fit = state.sample_count == 1 or (state.sample_count % every_n == 0)
    if not should_fit:
        return state.last_fit
    state.mark_fit_attempt()
    fit_result = _fit_curve_run(
        x_raw=x_raw,
        y_raw=y_raw,
        model=state.model,
        baseline_mode=state.baseline_mode,
        sigma_y=state.sigma_y,
        dense_eval_points=state.dense_eval_points,
    )
    if fit_result is not None:
        state.mark_fit_success(fit_result)
    else:
        state._refresh_last_fit_metadata()
    return state.last_fit


def execute_fit_yhat(fit_raw: Any) -> np.ndarray | None:
    if not isinstance(fit_raw, dict):
        return None
    return _coerce_trace(fit_raw.get("yhat"))


def execute_fit_xhat(fit_raw: Any) -> np.ndarray | None:
    if not isinstance(fit_raw, dict):
        return None
    return _coerce_trace(fit_raw.get("x"))


def execute_fit_yhat_dense(fit_raw: Any) -> np.ndarray | None:
    if not isinstance(fit_raw, dict):
        return None
    return _coerce_trace(fit_raw.get("yhat_dense"))


def execute_fit_xhat_dense(fit_raw: Any) -> np.ndarray | None:
    if not isinstance(fit_raw, dict):
        return None
    return _coerce_trace(fit_raw.get("x_dense"))


def _normalize_fit_param_name(raw: Any) -> str:
    text = str(raw if raw is not None else "center").strip().lower()
    aliases = {
        "amp": "amplitude",
        "a": "amplitude",
        "mu": "center",
        "x0": "center",
        "sigma": "sigma",
        "gamma": "gamma",
        "width": "width",
        "fwhm": "fwhm",
        "baseline": "baseline_const",
        "offset": "baseline_const",
        "slope": "baseline_slope",
    }
    return aliases.get(text, text)


def execute_fit_param(fit_raw: Any, params: Json) -> float | None:
    if not isinstance(fit_raw, dict):
        return None
    field = str(params.get("field", "value")).strip().lower()
    if field in {"", "value"}:
        fit_params = fit_raw.get("params")
    elif field in {"stderr", "error", "std_err", "stddev"}:
        fit_params = fit_raw.get("stderr")
    else:
        return None
    if not isinstance(fit_params, dict):
        return None
    name = _normalize_fit_param_name(params.get("name", "center"))
    return _normalize_float(fit_params.get(name))


def execute_fit_params(fit_raw: Any) -> dict[str, dict[str, float | None]] | None:
    if not isinstance(fit_raw, dict):
        return None
    params_raw = fit_raw.get("params")
    stderr_raw = fit_raw.get("stderr")
    params_map_raw = params_raw if isinstance(params_raw, dict) else None
    stderr_map_raw = stderr_raw if isinstance(stderr_raw, dict) else None
    if params_map_raw is None and stderr_map_raw is None:
        return None
    params_map = (
        {str(key): value for key, value in params_map_raw.items()}
        if params_map_raw is not None
        else None
    )
    stderr_map = (
        {str(key): value for key, value in stderr_map_raw.items()}
        if stderr_map_raw is not None
        else None
    )
    keys: set[str] = set()
    if params_map is not None:
        keys.update(params_map.keys())
    if stderr_map is not None:
        keys.update(stderr_map.keys())
    out: dict[str, dict[str, float | None]] = {}
    for key in sorted(keys):
        if not key:
            continue
        value = (
            _normalize_float(params_map.get(key))
            if params_map is not None
            else None
        )
        stderr = (
            _normalize_float(stderr_map.get(key))
            if stderr_map is not None
            else None
        )
        if value is None and stderr is None:
            continue
        out[key] = {
            "value": float(value) if value is not None else None,
            "stderr": float(stderr) if stderr is not None else None,
        }
    return out or None


def _parse_fit_hist_y_source(raw: Any) -> str:
    source = str(raw if raw is not None else "mean").strip().lower()
    if source in {"mean", "std", "sem", "count"}:
        return source
    raise ValueError("fit.from_hist_agg y_source must be one of mean, std, sem, count")


def _parse_fit_hist_sigma_source(raw: Any) -> str:
    source = str(raw if raw is not None else "sem").strip().lower()
    if source in {"sem", "std", "none"}:
        return source
    raise ValueError("fit.from_hist_agg chi2_sigma_source must be one of sem, std, none")


def _validate_fit_from_hist_params(
    params: Json,
) -> tuple[
    str,
    str,
    int,
    float | None,
    int | None,
    str,
    str,
    int,
    float | None,
    float | None,
]:
    model, baseline_mode, every_n, _sigma_y, dense_eval_points = (
        _validate_fit_curve_params(params)
    )
    y_source = _parse_fit_hist_y_source(params.get("y_source", "mean"))
    chi2_sigma_source = _parse_fit_hist_sigma_source(
        params.get("chi2_sigma_source", "sem")
    )
    min_count = _normalize_int(params.get("min_count"))
    if min_count is None:
        min_count = 1
    if min_count < 0:
        raise ValueError("fit.from_hist_agg requires min_count >= 0")
    x_min = _normalize_float(params.get("x_min"))
    x_max = _normalize_float(params.get("x_max"))
    if x_min is not None and x_max is not None and not (x_max > x_min):
        raise ValueError("fit.from_hist_agg requires x_max > x_min when both are set")
    return (
        model,
        baseline_mode,
        every_n,
        _sigma_y,
        dense_eval_points,
        y_source,
        chi2_sigma_source,
        int(min_count),
        x_min,
        x_max,
    )


def _hist_agg_to_xy(
    hist_raw: Any,
    *,
    y_source: str,
    chi2_sigma_source: str,
    min_count: int,
    x_min: float | None,
    x_max: float | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None] | None:
    if not isinstance(hist_raw, dict):
        return None
    x_raw = hist_raw.get("x_bins")
    y_raw = hist_raw.get(y_source)
    c_raw = hist_raw.get("count")
    sigma_raw = _hist_agg_sigma_source(hist_raw, chi2_sigma_source=chi2_sigma_source)
    if not isinstance(x_raw, list) or not isinstance(y_raw, list) or not isinstance(c_raw, list):
        return None
    n = min(len(x_raw), len(y_raw), len(c_raw))
    if n <= 0:
        return None
    x_vals: list[float] = []
    y_vals: list[float] = []
    sigma_vals: list[float] = [] if chi2_sigma_source != "none" else []
    for i in range(n):
        row = _hist_agg_parse_row(
            x_raw=x_raw,
            y_raw=y_raw,
            c_raw=c_raw,
            sigma_raw=sigma_raw,
            index=i,
            chi2_sigma_source=chi2_sigma_source,
            min_count=min_count,
            x_min=x_min,
            x_max=x_max,
        )
        if row is None:
            continue
        x_value, y_value, sigma_value = row
        x_vals.append(x_value)
        y_vals.append(y_value)
        if sigma_value is not None:
            sigma_vals.append(sigma_value)
    if len(x_vals) < 4:
        return None
    x_arr, y_arr, order = _hist_agg_sorted_xy(x_vals=x_vals, y_vals=y_vals)
    sigma_arr: np.ndarray | None = None
    if chi2_sigma_source != "none":
        sigma_arr = np.asarray(sigma_vals, dtype=np.float64)[order]
    return x_arr, y_arr, sigma_arr


def _hist_agg_sigma_source(
    hist_raw: dict[str, Any],
    *,
    chi2_sigma_source: str,
) -> Any:
    if chi2_sigma_source not in {"sem", "std"}:
        return None
    return hist_raw.get(chi2_sigma_source)


def _hist_agg_parse_row(
    *,
    x_raw: list[Any],
    y_raw: list[Any],
    c_raw: list[Any],
    sigma_raw: Any,
    index: int,
    chi2_sigma_source: str,
    min_count: int,
    x_min: float | None,
    x_max: float | None,
) -> tuple[float, float, float | None] | None:
    x = _normalize_float(x_raw[index])
    y = _normalize_float(y_raw[index])
    c = _normalize_float(c_raw[index])
    if x is None or y is None or c is None:
        return None
    if int(c) < int(min_count):
        return None
    if x_min is not None and x < x_min:
        return None
    if x_max is not None and x > x_max:
        return None
    sigma = _hist_agg_parse_sigma_at(
        sigma_raw=sigma_raw,
        index=index,
        chi2_sigma_source=chi2_sigma_source,
    )
    if chi2_sigma_source != "none" and sigma is None:
        return None
    return float(x), float(y), sigma


def _hist_agg_parse_sigma_at(
    *,
    sigma_raw: Any,
    index: int,
    chi2_sigma_source: str,
) -> float | None:
    if chi2_sigma_source == "none":
        return None
    if not isinstance(sigma_raw, list) or index >= len(sigma_raw):
        return None
    sigma = _normalize_float(sigma_raw[index])
    if sigma is None or sigma <= 0:
        return None
    return float(sigma)


def _hist_agg_sorted_xy(
    *,
    x_vals: list[float],
    y_vals: list[float],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x_arr = np.asarray(x_vals, dtype=np.float64)
    y_arr = np.asarray(y_vals, dtype=np.float64)
    order = np.argsort(x_arr)
    return x_arr[order], y_arr[order], order


def execute_fit_from_hist_agg(
    *,
    state: FitCurve1DState,
    hist_raw: Any,
    gate_raw: Any,
    y_source: str,
    chi2_sigma_source: str,
    min_count: int,
    x_min: float | None,
    x_max: float | None,
) -> dict[str, Any] | None:
    if not _gate_open(gate_raw, default=True):
        return state.last_fit
    state.sample_count += 1
    every_n = max(1, int(state.every_n))
    should_fit = state.sample_count == 1 or (state.sample_count % every_n == 0)
    if not should_fit:
        return state.last_fit
    state.mark_fit_attempt()
    xy = _hist_agg_to_xy(
        hist_raw,
        y_source=y_source,
        chi2_sigma_source=chi2_sigma_source,
        min_count=min_count,
        x_min=x_min,
        x_max=x_max,
    )
    if xy is None:
        state._refresh_last_fit_metadata()
        return state.last_fit
    x_arr, y_arr, sigma_arr = xy
    fit_result = _fit_curve_run(
        x_raw=x_arr,
        y_raw=y_arr,
        model=state.model,
        baseline_mode=state.baseline_mode,
        sigma_y=state.sigma_y,
        sigma_trace_raw=sigma_arr,
        dense_eval_points=state.dense_eval_points,
    )
    if fit_result is not None:
        state.mark_fit_success(fit_result)
    else:
        state._refresh_last_fit_metadata()
    return state.last_fit

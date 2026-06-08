from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

import numpy as np

from ..types import StreamCall, StreamOut


class _StreamPublisher(Protocol):
    _device: object

    def publish_stream(self, stream: str, arr: np.ndarray) -> dict[str, Any]: ...


def _ensure_shot_shape(arr: np.ndarray, out: StreamOut) -> np.ndarray:
    expected_dtype = out.numpy_dtype()
    if arr.dtype != expected_dtype:
        raise ValueError(
            f"Stream {out.stream!r} dtype mismatch: got {arr.dtype}, expected {expected_dtype}"
        )
    if tuple(arr.shape) != tuple(out.shape):
        raise ValueError(
            f"Stream {out.stream!r} shot shape mismatch: got {arr.shape}, expected {out.shape}"
        )
    if not arr.flags["C_CONTIGUOUS"]:
        return np.ascontiguousarray(arr)
    return arr


def _as_single_shot(value: Any, out: StreamOut) -> list[np.ndarray]:
    arr = np.asarray(value)
    return [_ensure_shot_shape(arr, out)]


def _as_record_shots(value: Any, out: StreamOut) -> list[np.ndarray]:
    dtype = out.numpy_dtype()
    arr = np.asarray(value, dtype=dtype)
    if arr.shape == ():
        return [np.asarray(arr, dtype=dtype).reshape(())]
    if arr.ndim != 1:
        raise ValueError(
            f"Record stream {out.stream!r} expected scalar record or 1D record batch, got {arr.shape}"
        )
    return [np.asarray(arr[idx], dtype=dtype).reshape(()) for idx in range(arr.shape[0])]


def _as_batch_shots(value: np.ndarray, out: StreamOut, *, n_batch: int) -> list[np.ndarray]:
    if not (
        value.ndim >= 1
        and value.shape[0] == n_batch
        and tuple(value.shape[1:]) == tuple(out.shape)
    ):
        raise ValueError(
            f"Stream {out.stream!r} batched shape mismatch: got {value.shape}, expected ({n_batch}, {out.shape})"
        )
    out_list: list[np.ndarray] = []
    for idx in range(n_batch):
        out_list.append(_ensure_shot_shape(np.asarray(value[idx]), out))
    return out_list


def _as_sequence_shots(
    value: list[Any] | tuple[Any, ...],
    out: StreamOut,
    *,
    expected_len: int,
) -> list[np.ndarray]:
    if len(value) != expected_len:
        raise ValueError(
            f"Stream {out.stream!r} list length {len(value)} != {expected_len}"
        )
    out_list: list[np.ndarray] = []
    for item in value:
        out_list.append(_ensure_shot_shape(np.asarray(item), out))
    return out_list


def _as_shot_list(
    value: Any,
    out: StreamOut,
    *,
    n_batch: int,
    allow_batch: bool,
) -> list[np.ndarray]:
    if out.kind == "records":
        return _as_record_shots(value, out)
    if isinstance(value, np.ndarray):
        if tuple(value.shape) == tuple(out.shape):
            return [_ensure_shot_shape(value, out)]
        if allow_batch:
            return _as_batch_shots(value, out, n_batch=n_batch)
        raise ValueError(
            f"Stream {out.stream!r} shot shape mismatch: got {value.shape}, expected {out.shape}"
        )
    if isinstance(value, (list, tuple)):
        expected_len = n_batch if allow_batch else 1
        return _as_sequence_shots(value, out, expected_len=expected_len)
    if n_batch == 1:
        return _as_single_shot(value, out)
    raise TypeError(
        f"Stream {out.stream!r} expected ndarray or list/tuple for n_batch={n_batch}"
    )


def _resolve_stream_callable(runner: _StreamPublisher, stream_call: StreamCall) -> Callable[..., Any]:
    func = getattr(runner._device, stream_call.method, None)
    if func is None or not callable(func):
        raise NotImplementedError(f"Stream method {stream_call.method!r} not found")
    return func


def _invoke_stream_callable(
    *,
    func: Callable[..., Any],
    stream_call: StreamCall,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[Any, int, bool]:
    call_kwargs = dict(stream_call.kwargs or {})
    call_kwargs.update(kwargs)
    n_batch_provided = "n_batch" in call_kwargs
    n_batch = int(call_kwargs.pop("n_batch", 1))
    if n_batch < 1:
        raise ValueError("n_batch must be >= 1")
    if not n_batch_provided:
        return func(*args, **call_kwargs), n_batch, False
    try:
        return func(*args, n_batch=n_batch, **call_kwargs), n_batch, True
    except TypeError as e:
        if "n_batch" in str(e) or "unexpected keyword" in str(e):
            raise TypeError(
                f"Stream method {stream_call.method!r} does not support n_batch"
            ) from e
        raise


def _publish_single_output(
    *,
    runner: _StreamPublisher,
    output: StreamOut,
    ret: Any,
    n_batch: int,
    n_batch_provided: bool,
) -> list[dict[str, Any]]:
    shots = _as_shot_list(
        ret,
        output,
        n_batch=n_batch,
        allow_batch=n_batch_provided,
    )
    return [runner.publish_stream(output.stream, shot) for shot in shots]


def _collect_multi_output_shots(
    *,
    outputs: list[StreamOut],
    ret: Any,
    n_batch: int,
    n_batch_provided: bool,
) -> dict[str, list[np.ndarray]]:
    if any(out.kind == "records" for out in outputs):
        raise ValueError(
            "Record streams currently require a single output per stream call"
        )
    if not isinstance(ret, dict):
        raise TypeError(
            "Stream call with multiple outputs must return dict[str, ndarray|list]"
        )
    shot_lists: dict[str, list[np.ndarray]] = {}
    for out in outputs:
        if out.stream not in ret:
            raise KeyError(f"Missing stream output {out.stream!r} in return dict")
        shot_lists[out.stream] = _as_shot_list(
            ret[out.stream],
            out,
            n_batch=n_batch,
            allow_batch=n_batch_provided,
        )
    return shot_lists


def _publish_multi_output(
    *,
    runner: _StreamPublisher,
    outputs: list[StreamOut],
    shot_lists: dict[str, list[np.ndarray]],
    n_batch: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for i in range(n_batch):
        descs: dict[str, Any] = {}
        for out in outputs:
            descs[out.stream] = runner.publish_stream(out.stream, shot_lists[out.stream][i])
        results.append(descs)
    return results


def build_stream_wrapper(
    *,
    runner: _StreamPublisher,
    stream_call: StreamCall,
) -> Callable[..., Any]:
    outputs = list(stream_call.outputs or [])

    def _wrapper(*args: Any, **kwargs: Any) -> Any:
        func = _resolve_stream_callable(runner, stream_call)
        ret, n_batch, n_batch_provided = _invoke_stream_callable(
            func=func,
            stream_call=stream_call,
            args=args,
            kwargs=kwargs,
        )
        if len(outputs) == 1:
            return _publish_single_output(
                runner=runner,
                output=outputs[0],
                ret=ret,
                n_batch=n_batch,
                n_batch_provided=n_batch_provided,
            )
        shot_lists = _collect_multi_output_shots(
            outputs=outputs,
            ret=ret,
            n_batch=n_batch,
            n_batch_provided=n_batch_provided,
        )
        return _publish_multi_output(
            runner=runner,
            outputs=outputs,
            shot_lists=shot_lists,
            n_batch=n_batch,
        )

    return _wrapper

from __future__ import annotations

from collections import deque

import numpy as np


class TraceAggregator:
    def __init__(self, *, rolling_window: int, trace_average_mode: str) -> None:
        self.rolling_window = int(rolling_window)
        self.trace_average_mode = str(trace_average_mode)
        self.rolling_buf: deque[np.ndarray] = deque()
        self.rolling_sum: np.ndarray | None = None
        self.block_sum: np.ndarray | None = None
        self.block_count = 0
        self.pending_msg: dict[str, object] | None = None

    def reset(self) -> None:
        self.rolling_buf.clear()
        self.rolling_sum = None
        self.block_sum = None
        self.block_count = 0
        self.pending_msg = None

    def add_frame(self, trace: np.ndarray) -> np.ndarray | None:
        if self.rolling_window <= 1 or trace.size <= 0:
            return trace
        if self.trace_average_mode == "rolling":
            if self.rolling_sum is None or int(self.rolling_sum.size) != int(trace.size):
                self.rolling_buf.clear()
                self.rolling_sum = np.zeros(int(trace.size), dtype=np.float64)
            incoming = trace.astype(np.float64, copy=True)
            if len(self.rolling_buf) >= self.rolling_window:
                oldest = self.rolling_buf.popleft()
                self.rolling_sum -= oldest
            self.rolling_buf.append(incoming)
            self.rolling_sum += incoming
            return self.rolling_sum / float(max(1, len(self.rolling_buf)))
        if self.block_sum is None or int(self.block_sum.size) != int(trace.size):
            self.block_sum = np.zeros(int(trace.size), dtype=np.float64)
            self.block_count = 0
        self.block_sum += trace.astype(np.float64, copy=False)
        self.block_count += 1
        if self.block_count < self.rolling_window:
            return None
        out = self.block_sum / float(self.block_count)
        self.block_sum.fill(0.0)
        self.block_count = 0
        return out

    def flush(self) -> dict[str, object] | None:
        msg = self.pending_msg
        self.pending_msg = None
        return msg

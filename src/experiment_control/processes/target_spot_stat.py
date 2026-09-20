from __future__ import annotations

from collections import deque

Json = dict[str, object]


class AbsorptionStatTracker:
    """Rolling or block mean over a scalar sample stream (e.g. `abs_integral`).

    Modeled on `fastapi/_trace_aggregator.py`'s `TraceAggregator` rolling
    (deque + running sum) and block (accumulate-then-reset) math, but scalar
    instead of `np.ndarray`, and exposing an always-readable `current_value`
    every tick (not just on block completion) since a control loop needs to
    know "what do I currently believe the mean is" continuously, not just
    once per completed block.
    """

    def __init__(self, *, mode: str, window: int) -> None:
        self._mode = str(mode)
        if self._mode not in ("rolling", "block"):
            raise ValueError(f"mode must be 'rolling' or 'block', got {mode!r}")
        self._window = max(1, int(window))
        self._rolling_buf: deque[float] = deque()
        self._rolling_sum = 0.0
        self._block_sum = 0.0
        self._block_count = 0
        self._last_block_mean: float | None = None
        self._total_samples = 0

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def window(self) -> int:
        return self._window

    @property
    def sample_count(self) -> int:
        return self._total_samples

    def reconfigure(self, *, mode: str, window: int) -> None:
        """Change mode/window and reset all accumulated state."""
        mode = str(mode)
        if mode not in ("rolling", "block"):
            raise ValueError(f"mode must be 'rolling' or 'block', got {mode!r}")
        self._mode = mode
        self._window = max(1, int(window))
        self.reset()

    def reset(self) -> None:
        self._rolling_buf.clear()
        self._rolling_sum = 0.0
        self._block_sum = 0.0
        self._block_count = 0
        self._last_block_mean = None
        self._total_samples = 0

    def add_sample(self, value: float) -> None:
        value = float(value)
        self._total_samples += 1
        if self._mode == "rolling":
            if len(self._rolling_buf) >= self._window:
                oldest = self._rolling_buf.popleft()
                self._rolling_sum -= oldest
            self._rolling_buf.append(value)
            self._rolling_sum += value
            return
        # block mode: accumulate until `window` samples arrive, then emit the
        # mean and reset for the next block.
        self._block_sum += value
        self._block_count += 1
        if self._block_count >= self._window:
            self._last_block_mean = self._block_sum / float(self._block_count)
            self._block_sum = 0.0
            self._block_count = 0

    @property
    def current_value(self) -> float | None:
        """The tracker's current estimate, or None if no estimate is available yet.

        Rolling mode: the mean of whatever samples are currently buffered
        (available as soon as the first sample arrives). Block mode: the mean
        of the last *completed* block, held unchanged until the next block of
        `window` samples completes (None before the first block completes).
        """
        if self._mode == "rolling":
            if not self._rolling_buf:
                return None
            return self._rolling_sum / float(len(self._rolling_buf))
        return self._last_block_mean

    def status_payload(self) -> Json:
        return {
            "mode": self._mode,
            "window": self._window,
            "current_value": self.current_value,
            "sample_count": self._total_samples,
        }

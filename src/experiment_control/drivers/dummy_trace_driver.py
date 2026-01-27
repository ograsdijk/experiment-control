import numpy as np

from experiment_control.types import StreamCall, StreamOut

DEFAULT_STREAM_CALLS_DUMMYTRACEDRIVER = [
    StreamCall(
        method="acquire_trace",
        outputs=[
            StreamOut(
                stream="trace",
                dtype="float64",
                shape=(5, 10_000),
                units="counts",
                ring_slots=256,
            )
        ],
    )
]


class DummyTraceDriver:
    def __init__(self, port: int) -> None:
        self.port = port

    def connect(self) -> None:
        print(f"Connecting to dummy traced device on port {self.port}")

    def disconnect(self) -> None:
        print(f"Disconnecting from dummy traced device on port {self.port}")

    def acquire_trace(self, n_batch: int | None = None) -> np.ndarray:
        _ = n_batch
        return np.random.randn(5, 10_000).astype(np.float64)

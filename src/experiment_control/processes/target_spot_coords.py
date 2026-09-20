from __future__ import annotations

from typing import Any

from ..sequencer.ranges import apply_sample, generate_scan2d_from_spec

Json = dict[str, Any]


class SpotCoordinateGenerator:
    """Draws mirror coordinates from a range, reusing the sequencer's own
    grid/sample primitives (`ranges.generate_scan2d_from_spec` /
    `ranges.apply_sample`) rather than reimplementing grid/random logic.

    `strategy == "grid"` cycles the generated grid in order (honoring
    `grid_pattern`, e.g. serpentine/row_major). `strategy == "random"` draws
    one point per call via the same sample-with-replacement modifier a
    `for: {gen: scan2d, sample: {...}}` sequence step uses.
    """

    def __init__(
        self,
        *,
        range_spec: Json,
        strategy: str,
        grid_pattern: str = "serpentine",
    ) -> None:
        self._range_spec = dict(range_spec)
        self._strategy = str(strategy)
        self._grid_pattern = str(grid_pattern)
        self._grid: list[Json] = []
        self._cursor = 0
        self._rebuild()

    @property
    def strategy(self) -> str:
        return self._strategy

    @property
    def grid_pattern(self) -> str:
        return self._grid_pattern

    @property
    def range_spec(self) -> Json:
        return dict(self._range_spec)

    def reconfigure(
        self,
        *,
        range_spec: Json | None = None,
        strategy: str | None = None,
        grid_pattern: str | None = None,
    ) -> None:
        if range_spec is not None:
            self._range_spec = dict(range_spec)
        if strategy is not None:
            self._strategy = str(strategy)
        if grid_pattern is not None:
            self._grid_pattern = str(grid_pattern)
        self._rebuild()

    def _rebuild(self) -> None:
        spec = dict(self._range_spec)
        # `pattern` only affects grid ordering; scan2d validates it against
        # the same enum ("serpentine"/"row_major"/"random"/...) the sequencer
        # already uses for `gen: {scan2d: {...}}`.
        spec["pattern"] = self._grid_pattern if self._strategy == "grid" else "random"
        self._grid = generate_scan2d_from_spec(spec)
        self._cursor = 0

    def next_spot(self) -> Json:
        if not self._grid:
            raise RuntimeError("coordinate range produced no candidate spots")
        if self._strategy == "grid":
            record = self._grid[self._cursor % len(self._grid)]
            self._cursor += 1
            return dict(record)
        drawn = apply_sample(self._grid, {"count": 1, "replace": True}, {})
        return dict(drawn[0])

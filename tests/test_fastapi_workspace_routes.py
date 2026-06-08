# ruff: noqa: E402
"""Regression test for the FastAPI workspace route surface.

The duplicate PATCH /api/stream/workspaces/{workspace_id} endpoint was
removed; the React UI only ever issued PUT, and the PATCH handler was
byte-identical to the PUT one (no merge semantics). This test asserts
PUT remains and PATCH is absent so a future revival of the route is a
deliberate decision (with merge semantics or another contract).
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from experiment_control.fastapi.app import app


class WorkspaceRouteSurfaceTests(unittest.TestCase):
    def _methods_for(self, path: str) -> set[str]:
        methods: set[str] = set()
        for route in app.routes:
            if getattr(route, "path", None) == path:
                methods |= set(getattr(route, "methods", []) or set())
        return methods

    def test_put_workspace_route_present(self) -> None:
        methods = self._methods_for("/api/stream/workspaces/{workspace_id}")
        self.assertIn("PUT", methods)

    def test_patch_workspace_route_absent(self) -> None:
        methods = self._methods_for("/api/stream/workspaces/{workspace_id}")
        self.assertNotIn(
            "PATCH",
            methods,
            "PATCH /api/stream/workspaces/{workspace_id} was removed because it "
            "duplicated PUT verbatim. If you re-add it, give it merge semantics "
            "(stream_analysis.workspace.merge RPC) and update this assertion.",
        )


if __name__ == "__main__":
    unittest.main()

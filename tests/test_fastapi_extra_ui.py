from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture()
def fastapi_app_module(monkeypatch: pytest.MonkeyPatch):
    module = importlib.import_module("experiment_control.fastapi.app")

    monkeypatch.setenv("EXPERIMENT_CONTROL_SERVE_UI", "1")
    yield module


def _dist(root: Path) -> Path:
    path = root / "dist"
    path.mkdir()
    (path / "index.html").write_text("<html></html>", encoding="utf-8")
    return path


def test_resolve_extra_ui_specs_accepts_valid_entries(
    fastapi_app_module, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dist = _dist(tmp_path)
    monkeypatch.setenv(
        "EXPERIMENT_CONTROL_EXTRA_UI_JSON",
        json.dumps(
            [
                {
                    "slug": "rc-microwave-control",
                    "label": "RC Microwave Control",
                    "dist": str(dist),
                }
            ]
        ),
    )

    specs = fastapi_app_module._resolve_extra_ui_specs()

    assert len(specs) == 1
    assert specs[0].slug == "rc-microwave-control"
    assert specs[0].label == "RC Microwave Control"
    assert specs[0].href == "/instance-ui/rc-microwave-control/"


def test_resolve_extra_ui_specs_rejects_invalid_slug(
    fastapi_app_module, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dist = _dist(tmp_path)
    monkeypatch.setenv(
        "EXPERIMENT_CONTROL_EXTRA_UI_JSON",
        json.dumps([{"slug": "../bad", "label": "Bad", "dist": str(dist)}]),
    )

    assert fastapi_app_module._resolve_extra_ui_specs() == []


def test_resolve_extra_ui_specs_requires_index_html(
    fastapi_app_module, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    monkeypatch.setenv(
        "EXPERIMENT_CONTROL_EXTRA_UI_JSON",
        json.dumps([{"slug": "missing-index", "label": "Missing", "dist": str(dist)}]),
    )

    assert fastapi_app_module._resolve_extra_ui_specs() == []

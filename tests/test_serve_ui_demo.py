"""The serve-UI dogfood's inner config stays in step with the web demo it mirrors."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_DEMOS = Path(__file__).resolve().parents[1] / "demos"


def _web_target(config: Path) -> dict[str, Any]:
    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    target: dict[str, Any] = dict(cfg["targets"]["web"])
    # The inner config deliberately re-anchors the scenarios dir to the web demo's own.
    target.pop("paths", None)
    return target


def test_inner_web_target_mirrors_the_web_demo() -> None:
    inner = _web_target(_DEMOS / "serve-ui" / "inner.config.yaml")
    demo = _web_target(_DEMOS / "web" / "demo.config.yaml")
    assert inner == demo

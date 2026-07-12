"""Report formatting primitives.

The inline rich-text Part type, byte/line/duration helpers, artifact lookup, and the action
display metadata. Dependency-free so every other report module can import it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bajutsu.evidence import Artifact
from bajutsu.orchestrator import RunResult

# How many trailing log lines / body chars to embed inline (the full file is linked).
_LOG_MAX_LINES = 2000
_BODY_MAX = 4000

# An inline rich-text fragment: (token-class, text). An empty class means plain text.
Part = tuple[str, str]


# --- shared helpers (artifacts, files, formatting) ---


def _artifact(r: RunResult, kind: str) -> Artifact | None:
    return next((a for a in r.artifacts if a.kind == kind), None)


def _read_lines(run_dir: Path, name: str, max_lines: int) -> tuple[list[str] | None, int]:
    try:
        text = (run_dir / name).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, 0
    lines = text.splitlines()
    total = len(lines)
    return (lines[-max_lines:] if total > max_lines else lines), total


def _read_json(run_dir: Path, name: str) -> Any:
    try:
        return json.loads((run_dir / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _gnum(v: Any) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def _as_float(v: Any) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _truncate(body: str) -> str:
    return body if len(body) <= _BODY_MAX else body[:_BODY_MAX] + "\n… (truncated)"


def _fmt_duration(seconds: float) -> str:
    """A compact human duration: '0.8s', '12.3s', or '1m 23s' once it passes a minute."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(round(seconds), 60)
    return f"{minutes}m {secs}s"


def _status_class(status: Any) -> str:
    if isinstance(status, int) and not isinstance(status, bool):
        if 200 <= status < 400:
            return "ok"
        if status >= 400:
            return "ng"
    return ""


# action key (alias-cased, as dumped) -> (display label, color class)
_ACTION_META = {
    "tap": ("tap", "act-tap"),
    "doubleTap": ("double-tap", "act-tap"),
    "longPress": ("long-press", "act-tap"),
    "type": ("type", "act-type"),
    "swipe": ("swipe", "act-move"),
    "pinch": ("pinch", "act-move"),
    "rotate": ("rotate", "act-move"),
    "drag": ("drag", "act-move"),
    "wait": ("wait", "act-wait"),
    "assert": ("assert", "act-assert"),
    "relaunch": ("relaunch", "act-wait"),
}

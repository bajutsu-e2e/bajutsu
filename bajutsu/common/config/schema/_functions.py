"""Read a config file into the schema, coercing the shapes YAML admits."""

from __future__ import annotations

from typing import Any


def _as_list(v: Any) -> Any:
    return [v] if isinstance(v, str) else v

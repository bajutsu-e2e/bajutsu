"""Minimal .env loader: read KEY=VALUE lines into the environment.

A gitignored .env keeps secrets (e.g. ANTHROPIC_API_KEY for record and
--dismiss-alerts) out of the shell profile and out of version control. The
parser is pure and unit-tested; loading never overrides a variable already set
in the real environment, so the file is a fallback, not an override.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import MutableMapping
from pathlib import Path

# Overridable so a project can point elsewhere without a CLI flag.
DEFAULT_PATH = os.environ.get("BAJUTSU_DOTENV", ".env")


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines; skip blanks, comments, and malformed lines."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key:
            continue  # a line without '=' or with an empty key is not an assignment
        out[key] = _unquote(value.strip())
    return out


def load_dotenv(
    path: str | Path = DEFAULT_PATH,
    environ: MutableMapping[str, str] | None = None,
) -> list[str]:
    """Load `path` into `environ` without overriding existing vars; return keys set."""
    environ = environ if environ is not None else os.environ
    file = Path(path)
    if not file.exists():
        return []
    applied: list[str] = []
    for key, value in parse_dotenv(file.read_text(encoding="utf-8")).items():
        if not environ.get(key):  # fill when unset or empty; a real value always wins
            environ[key] = value
            applied.append(key)
    return applied


def dotenv_path(base: str | Path = ".") -> Path:
    """Resolve the .env path: ``DEFAULT_PATH`` as-is if absolute, else under *base*."""
    p = Path(DEFAULT_PATH)
    return p if p.is_absolute() else (Path(base) / p)


def upsert_dotenv(key: str, value: str | None, path: str | Path = DEFAULT_PATH) -> None:
    """Set ``key=value`` in the .env at *path*, preserving other lines and comments;
    ``value=None`` removes the assignment.  Creates the file when a value is given."""
    file = Path(path)
    if value is None and not file.exists():
        return
    lines = file.read_text(encoding="utf-8").splitlines() if file.exists() else []
    out: list[str] = []
    written = False
    for raw in lines:
        stripped = raw.strip().removeprefix("export ").strip()
        if stripped.partition("=")[0].strip() == key:
            if value is not None and not written:
                out.append(f"{key}={value}")  # replace the first assignment in place
                written = True
            continue  # drop the old assignment (and any later duplicates)
        out.append(raw)
    if value is not None and not written:
        out.append(f"{key}={value}")
    text = "\n".join(out)
    file.write_text(text + "\n" if text else "", encoding="utf-8")
    # A .env stores secrets in clear text by design — it is gitignored and the loader reads it
    # verbatim as KEY=VALUE, so there is no key to encrypt against. Restrict it to the owner so
    # other local accounts can't read the secret (best effort; chmod is a no-op on some FSes).
    with contextlib.suppress(OSError):
        file.chmod(0o600)

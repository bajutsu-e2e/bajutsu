"""Durable store for the serve AI provider settings (BE-0184).

The serve Web UI's provider choice, model, and reasoning effort lived only in the serve
process's environment, so every restart reset them to the launch environment and the operator
re-entered them by hand. This store persists them so a saved choice survives a restart, the way
the Claude API key already does through the write-once secret store (BE-0136).

Unlike that secret store, these values are **not secrets**: they are read back and displayed for
editing, so the store is plainly readable and unencrypted — deliberately *not* the write-once,
no-reveal shape of `serve.secrets` (see BE-0184 *Alternatives considered*). Only the local,
file-backed shape lives here; the per-organization, DB-backed shape a hosted deployment needs
is deferred until serve resolves these settings per organization rather than process-globally.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from bajutsu.serve.state import ProviderSettings


class ProviderSettingsError(ValueError):
    """A persisted provider-settings file exists but its contents are malformed.

    Raised instead of guessing at a partial value: the boot path turns it into a visible
    warning and falls back to the env-derived defaults, so a corrupt file never silently
    resets the operator's choice (determinism-first).
    """


@dataclass(frozen=True)
class PersistedProviderSettings:
    """A snapshot of the serve AI provider settings, as saved to and loaded from a store.

    Mirrors what the Web UI last saved: the active provider plus the per-provider model/effort/
    region map (BE-0183), so switching back to a provider left behind restores its settings too.
    """

    provider: str
    settings: dict[str, ProviderSettings] = field(default_factory=dict)


class ProviderSettingsStore(Protocol):
    """Reads and writes the persisted provider settings for one serve deployment.

    Readable by design — the opposite of the write-once `serve.secrets.SecretStore`.
    """

    def load(self) -> PersistedProviderSettings | None:
        """Return the persisted snapshot, or None when nothing has been saved yet.

        Raises:
            ProviderSettingsError: A snapshot exists but is malformed.
        """
        ...

    def save(self, data: PersistedProviderSettings) -> None:
        """Persist *data*, replacing any earlier snapshot."""
        ...


class LocalProviderSettingsStore:
    """A `ProviderSettingsStore` backed by a single JSON file — the local-serve shape.

    The file is a sibling of serve's run directory; a hosted deployment wires a different,
    per-organization store instead of this one.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> PersistedProviderSettings | None:
        if not self._path.exists():
            return None
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ProviderSettingsError(f"cannot read {self._path}: {e}") from e
        return _decode(raw, self._path)

    def save(self, data: PersistedProviderSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "provider": data.provider,
            "settings": {
                name: {"model": s.model, "effort": s.effort, "region": s.region}
                for name, s in data.settings.items()
            },
        }
        # Write-then-replace so a crash mid-write never leaves a half-written file that load()
        # would reject on the next boot.
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)


def _decode(raw: object, path: Path) -> PersistedProviderSettings:
    if not isinstance(raw, dict):
        raise ProviderSettingsError(f"{path}: expected a JSON object, got {type(raw).__name__}")
    provider = raw.get("provider")
    settings_raw = raw.get("settings", {})
    if not isinstance(provider, str) or not isinstance(settings_raw, dict):
        raise ProviderSettingsError(f"{path}: 'provider' must be a string and 'settings' a map")
    settings: dict[str, ProviderSettings] = {}
    for name, slot in settings_raw.items():
        if not isinstance(slot, dict):
            raise ProviderSettingsError(f"{path}: settings[{name!r}] must be a map")
        settings[name] = ProviderSettings(
            model=_str_field(slot, "model", name, path),
            effort=_str_field(slot, "effort", name, path),
            region=_str_field(slot, "region", name, path),
        )
    return PersistedProviderSettings(provider=provider, settings=settings)


def _str_field(slot: dict[str, object], key: str, name: str, path: Path) -> str:
    # Reject a non-string leaf rather than coercing it (`str(123)` → "123"): the module's contract is
    # to fail on a malformed value, not guess at a partial one.
    value = slot.get(key, "")
    if not isinstance(value, str):
        raise ProviderSettingsError(f"{path}: settings[{name!r}].{key} must be a string")
    return value

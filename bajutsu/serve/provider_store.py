"""Durable store for the serve AI provider settings (BE-0184).

The serve Web UI's provider choice, model, and reasoning effort lived only in the serve
process's environment, so every restart reset them to the launch environment and the operator
re-entered them by hand. This store persists them so a saved choice survives a restart, the way
the Claude API key already does through the write-once secret store (BE-0136).

Unlike that secret store, these values are **not secrets**: they are read back and displayed for
editing, so the store is plainly readable and unencrypted — deliberately *not* the write-once,
no-reveal shape of `serve.secrets` (see BE-0184 *Alternatives considered*). The local, file-backed
shape lives here; the per-organization, DB-backed shape a hosted deployment needs lives in
`serve.server.provider_store.DbProviderSettingsStore` (behind the `db` extra), landed by BE-0229
once serve resolves these settings per organization rather than process-globally.
"""

from __future__ import annotations

import json
import os
import tempfile
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
        return decode(raw, str(self._path))

    def save(self, data: PersistedProviderSettings) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"provider": data.provider, "settings": encode_settings(data.settings)}
        # Write-then-replace so a crash mid-write never leaves a half-written file that load()
        # would reject on the next boot. The temp name is unique per call (mkstemp) — serve is a
        # ThreadingHTTPServer, so a fixed `<path>.tmp` suffix would let two concurrent saves clobber
        # each other's in-flight write before either os.replace() lands.
        fd, tmp_name = tempfile.mkstemp(
            dir=self._path.parent, prefix=self._path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(payload, indent=2))
            os.replace(tmp_name, self._path)
        except OSError:
            os.unlink(tmp_name)
            raise


def decode(raw: object, where: str) -> PersistedProviderSettings:
    """Validate a decoded ``{provider, settings}`` mapping into a `PersistedProviderSettings`.

    Shared by both stores — the local file store passes the file path as *where*, the DB store its
    ``provider_settings[<org>]`` label — so a malformed value (a non-object payload, a non-string
    leaf) fails identically whichever backend it came from, rather than the DB store trusting its
    own possibly-hand-edited rows.
    """
    if not isinstance(raw, dict):
        raise ProviderSettingsError(f"{where}: expected a JSON object, got {type(raw).__name__}")
    provider = raw.get("provider")
    settings_raw = raw.get("settings", {})
    if not isinstance(provider, str) or not isinstance(settings_raw, dict):
        raise ProviderSettingsError(f"{where}: 'provider' must be a string and 'settings' a map")
    settings: dict[str, ProviderSettings] = {}
    for name, slot in settings_raw.items():
        if not isinstance(slot, dict):
            raise ProviderSettingsError(f"{where}: settings[{name!r}] must be a map")
        settings[name] = ProviderSettings(
            model=_str_field(slot, "model", name, where),
            effort=_str_field(slot, "effort", name, where),
            region=_str_field(slot, "region", name, where),
        )
    return PersistedProviderSettings(provider=provider, settings=settings)


def _str_field(slot: dict[str, object], key: str, name: str, where: str) -> str:
    # Reject a non-string leaf rather than coercing it (`str(123)` → "123"): the module's contract is
    # to fail on a malformed value, not guess at a partial one.
    value = slot.get(key, "")
    if not isinstance(value, str):
        raise ProviderSettingsError(f"{where}: settings[{name!r}].{key} must be a string")
    return value


def encode_settings(settings: dict[str, ProviderSettings]) -> dict[str, dict[str, str]]:
    """The JSON-friendly slot map both stores serialize (the shape a ``settings`` value takes)."""
    return {
        name: {"model": s.model, "effort": s.effort, "region": s.region}
        for name, s in settings.items()
    }

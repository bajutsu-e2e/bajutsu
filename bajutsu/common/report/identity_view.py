"""The provenance stamp's build identity as the CTRF export and the HTML header show it (BE-0506).

Both read a stored manifest, which may predate the keys or hold anything, so every level is
checked rather than assumed. A gap is shown with its reason, never dropped.
"""

from __future__ import annotations

from collections.abc import Mapping

_SHORT = 12


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _subset(group: object, keys: tuple[str, ...]) -> dict[str, object]:
    """The *keys* of one stamp group, with the reasons for whichever of them are gaps."""
    source = _mapping(group)
    out: dict[str, object] = {k: source[k] for k in keys if k in source}
    if gaps := {k: v for k, v in _mapping(source.get("unavailable")).items() if k in keys}:
        out["unavailable"] = gaps
    return out


def identity_extra(provenance: Mapping[str, object] | None) -> dict[str, object]:
    """CTRF's `environment.extra`: the build identity of each target and of the tool."""
    prov = _mapping(provenance)
    targets: dict[str, object] = {}
    for name, stamp in _mapping(prov.get("targets")).items():
        groups = {
            "app": _subset(_mapping(stamp).get("app"), ("hash", "version")),
            # A bare commit reads as a clean tree, so the dirtiness travels with it.
            "appSource": _subset(_mapping(stamp).get("appSource"), ("commit", "uncommitted")),
        }
        if present := {key: group for key, group in groups.items() if group}:
            targets[name] = present
    extra: dict[str, object] = {"targets": targets} if targets else {}
    if tool := _subset(prov.get("toolSource"), ("commit", "uncommitted", "source")):
        extra["toolSource"] = tool
    return extra


def _shown(group: Mapping[str, object], key: str, label: str) -> str:
    value = group.get(key)
    if isinstance(value, str):
        digest = value.removeprefix("sha256:")
        short = digest[:_SHORT] if key in ("hash", "commit") else digest
        return f"{label} {short}"
    reason = _mapping(group.get("unavailable")).get(key)
    return f"{label} unknown ({reason})" if reason else f"{label} unknown"


def identity_lines(provenance: Mapping[str, object] | None) -> list[dict[str, object]]:
    """One header line per target that recorded anything: version, hash, source, and dirtiness."""
    lines: list[dict[str, object]] = []
    for name, stamp in _mapping(_mapping(provenance).get("targets")).items():
        app = _mapping(_mapping(stamp).get("app"))
        source = _mapping(_mapping(stamp).get("appSource"))
        parts = [_shown(app, "version", "version"), _shown(app, "hash", "hash")] if app else []
        if source:
            parts.append(_shown(source, "commit", "source"))
            # An unreadable status is its own gap; saying nothing would read as a clean tree.
            if source.get("uncommitted") is None and source.get("commit") is not None:
                parts.append(_shown(source, "uncommitted", "changes"))
        if parts:
            lines.append(
                {"target": name, "parts": parts, "uncommitted": source.get("uncommitted") is True}
            )
    return lines

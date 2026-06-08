"""Identifier recovery for backends that don't expose accessibilityIdentifier.

idb's `describe-all` carries `AXUniqueId` (= accessibilityIdentifier), so id-first
selectors resolve directly. RocketSim's agent protocol exposes only
role / label / value / frame — there is no identifier anywhere in its output. An
`IdMap` recovers each on-screen element's accessibilityIdentifier from a per-app
table of matchers (role / label / value, exact or regex), so the same id-first
scenarios run on RocketSim too.

The map is authored per app (or generated from idb, which knows both the id and
the label). A matcher must resolve to exactly one on-screen element: an ambiguous
or absent match leaves the identifier unset, so a selector then fails with a clear
"no match" rather than silently hitting the wrong element. Dynamic text (a counter
label like "Count: 3") is matched with a regex (`labelMatches: "^Count:"`) since
its exact label changes while its identifier does not.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from bajutsu import _yaml
from bajutsu.drivers import base


class Matcher(BaseModel):
    """How to recognize one element on a no-identifier backend. Provided fields are
    combined with AND; at least one must be set."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    role: str | None = None  # must appear in the element's traits, e.g. "button"
    label: str | None = None  # exact label match
    label_matches: str | None = Field(default=None, alias="labelMatches")  # regex over label
    value: str | None = None  # exact value match
    value_matches: str | None = Field(default=None, alias="valueMatches")  # regex over value

    def matches(self, el: base.Element) -> bool:
        if self.role is not None and self.role not in el["traits"]:
            return False
        if self.label is not None and el["label"] != self.label:
            return False
        if self.label_matches is not None and not (
            el["label"] is not None and re.search(self.label_matches, el["label"]) is not None
        ):
            return False
        if self.value is not None and el["value"] != self.value:
            return False
        if self.value_matches is not None and not (
            el["value"] is not None and re.search(self.value_matches, el["value"]) is not None
        ):
            return False
        return True


# accessibilityIdentifier -> matcher
IdMap = dict[str, Matcher]


def load_idmap(text: str) -> IdMap:
    """Parse an idmap YAML: `<identifier>: { role?, label?, labelMatches?, value?, valueMatches? }`."""
    data: Any = _yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("idmap must be a mapping of identifier -> matcher")
    return {str(k): Matcher.model_validate(v or {}) for k, v in data.items()}


def apply(elements: list[base.Element], idmap: IdMap) -> list[base.Element]:
    """Return a copy of `elements` with identifiers filled in from `idmap`.

    Only elements whose identifier is currently unset are touched (a backend that
    already provides identifiers is unaffected), and a matcher is applied only when
    it resolves to exactly one such element — ambiguity is left unresolved so the
    selector layer reports it rather than guessing.
    """
    if not idmap:
        return elements
    out: list[base.Element] = [dict(e) for e in elements]  # type: ignore[misc]
    for ident, matcher in idmap.items():
        hits = [e for e in out if e["identifier"] is None and matcher.matches(e)]
        if len(hits) == 1:
            hits[0]["identifier"] = ident
    return out

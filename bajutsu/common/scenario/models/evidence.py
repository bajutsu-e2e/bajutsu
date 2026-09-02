"""Evidence-rule models: capturePolicy trigger/rule, redaction config, and per-scenario network-filter settings."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from bajutsu.common.scenario.models._base import _exactly_one, _Model, _validate_capture


class Trigger(_Model):
    """A `capturePolicy` trigger that fires its `CaptureRule` when a condition holds.

    The condition is exactly one of `action` / `event` / `result`; `idMatches` narrows an
    `action` trigger to a specific element ID.
    """

    action: str | None = None
    id_matches: str | None = Field(default=None, alias="idMatches")
    event: Literal["screenChanged"] | None = None
    result: Literal["error"] | None = None

    @model_validator(mode="after")
    def _one(self) -> Self:
        _exactly_one(self, ("action", "event", "result"), "§9 A")
        if self.id_matches is not None and self.action is None:
            raise ValueError("idMatches requires action (§9 A)")
        return self


class CaptureRule(_Model):
    """A `capturePolicy` rule — capture the artifacts in `capture` when its `on` trigger fires."""

    on: Trigger
    capture: list[str]
    # Provenance (BE-0044): the instruction this evidence rule was normalized from. Authoring
    # metadata only — `run` never reads it.
    from_: str | None = Field(default=None, alias="from")

    @field_validator("capture")
    @classmethod
    def _cap(cls, v: list[str]) -> list[str]:
        return _validate_capture(v)


class Redact(_Model):
    """Redaction config — element `labels`, network `headers`, and JSON `fields` to scrub from evidence.

    Each list names items that are zeroed out before evidence is written to the report.
    A standard set of credential-bearing headers (`authorization`, `cookie`, `set-cookie`, …)
    is masked by default (BE-0130); `unmask_headers` is the explicit, visible opt-out that
    releases a specific default — turning off protection is never the mere absence of `redact:`.

    Two element-level defaults join that header set (BE-0331) and take the same shape of opt-out:
    a field the platform itself marks secret, and a field whose identifier or label names a
    credential. Both cover cases a caller should never have to configure, so each releases only
    through its own explicit flag.
    """

    labels: list[str] = Field(default_factory=list)
    headers: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    unmask_headers: list[str] = Field(default_factory=list, alias="unmaskHeaders")
    unmask_secure_fields: bool = Field(default=False, alias="unmaskSecureFields")
    unmask_credential_names: bool = Field(default=False, alias="unmaskCredentialNames")


class NetworkFilter(_Model):
    """Which observed requests to interleave into the report's Steps timeline.

    With `domains` set, only exchanges whose URL host matches one of them — exactly or as a
    parent suffix (`example.com` matches `api.example.com`) — appear in Steps; empty /
    unset shows every captured exchange. The Network tab always lists them all.
    """

    domains: list[str] = Field(default_factory=list)


class Network(_Model):
    """Per-scenario network settings.

    `filter` scopes which observed requests are interleaved into the report's Steps timeline.
    """

    filter: NetworkFilter | None = None

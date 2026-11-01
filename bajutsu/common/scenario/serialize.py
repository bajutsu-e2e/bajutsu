"""Serialize scenarios back to YAML / JSON (round-trips through load.py)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from bajutsu.common import _yaml
from bajutsu.common.scenario import interp
from bajutsu.common.scenario.models import Mock, Scenario

# Placeholder a literal `totp.secret` seed is masked with in an evidence snapshot (BE-0152).
_TOTP_PLACEHOLDER = "<redacted>"


def _mask_totp_secrets(node: Any) -> Any:
    """Deep-copy a serialized scenario, masking any literal `totp.secret` seed (BE-0152).

    Walks nested step lists (`if` / `forEach` / `web`) so a `totp` anywhere is reached. A
    `${...}` reference is kept — it is not the seed, and its resolved value is scrubbed by the
    run-level secret pass — while any literal seed becomes the fixed placeholder.
    """
    if isinstance(node, dict):
        masked = {key: _mask_totp_secrets(value) for key, value in node.items()}
        totp = masked.get("totp")
        if isinstance(totp, dict):
            secret = totp.get("secret")
            if isinstance(secret, str) and not interp.is_reference(secret):
                totp["secret"] = _TOTP_PLACEHOLDER
        return masked
    if isinstance(node, list):
        return [_mask_totp_secrets(item) for item in node]
    return node


def redact_totp_secrets(scenario: Scenario) -> Scenario:
    """A copy of `scenario` with literal `totp.secret` seeds masked, for on-disk evidence (BE-0152).

    The executed scenario is snapshotted into the run's artifacts; a literal base32 seed there is
    durable credential material, so it is replaced with a placeholder before the snapshot is
    written. A `${secrets.*}` reference is left intact (its resolved value never reaches the
    snapshot — BE-0032). Round-trips through the model so the result stays a valid scenario.

    `exclude_defaults` is what keeps that round-trip total: a field validator may reject a value the
    author can never write but a dump still emits — a `default_factory`'s own empty list, which
    `exclude_none` does not drop — so re-validating a model's own dump would fail on a field nobody
    declared (BE-0401 met this on the since-removed `labels` of the alert-guard declaration). Excluding
    default-valued fields hands `model_validate` only what was declared, and re-validation restores
    each default, so the returned model is field-for-field the input.
    """
    data = scenario.model_dump(by_alias=True, exclude_none=True, exclude_defaults=True)
    return Scenario.model_validate(_mask_totp_secrets(data))


def scenario_dict(scenario: Scenario) -> dict[str, Any]:
    """An alias-keyed dict of one scenario, with unset fields dropped (for the rich report view).

    Drops default-valued fields for the same reason `redact_totp_secrets` does: a model dump that
    emits every default is not reloadable, because a validator reading `model_fields_set` cannot tell
    a default the dump added from a value the author wrote. `VisualMatch._engine_fields` is the
    case — it rejects `colorTolerance` / `antialiasing` alongside `compare: exact`, and both carry
    non-None defaults that `exclude_none` keeps — so the `scenario.yaml` written beside a run's
    results failed to reload, against `dump_scenarios`' own round-trip contract. Excluding them also
    keeps the snapshot as terse as the author wrote it, which is what `dump_block` already does.

    Nothing here touches a value once `model_dump` has produced it: a step's own fieldless action
    (`back: {}`) must survive verbatim or reloading fails the one-of-N-required invariant (§6.2 /
    §6.4), and a free-form `dict[str, Any]` field (`push.payload`) is author data pydantic's own
    `exclude_none`/`exclude_defaults` never look inside — a generic post-pass dropping `None` /
    `[]` there would silently change what a re-run sends, which used to be this function's bug.
    """
    return scenario.model_dump(mode="json", by_alias=True, exclude_none=True, exclude_defaults=True)


def dump_scenarios(scenarios: list[Scenario]) -> str:
    """Serialize scenarios back to YAML (round-trips through load_scenarios)."""
    return _yaml.safe_dump([scenario_dict(s) for s in scenarios])


def dump_scenario_file(scenarios: list[Scenario], description: str | None = None) -> str:
    """Serialize a scenario file.

    With a file-level `description`, emits the `{description, scenarios}` mapping form; otherwise the
    bare list (round-trips through `load_scenario_file`).
    """
    body = [scenario_dict(s) for s in scenarios]
    if description:
        return _yaml.safe_dump({"description": description, "scenarios": body})
    return _yaml.safe_dump(body)


def dump_block(items: Sequence[BaseModel]) -> str:
    """Serialize models as a `- …` YAML sequence block — one alias-keyed item each.

    Alias keying matches `scenario_dict`, but a scoped block also drops default-valued fields
    (`exclude_defaults`), so a single spliced step / assertion stays as terse as the author wrote it
    rather than sprouting `submit: false` and other model defaults. Used by the Author editor's
    scoped round-trip edits (BE-0261) to re-serialize just the changed step / expect block.
    """
    return _yaml.safe_dump(
        [
            item.model_dump(mode="json", by_alias=True, exclude_none=True, exclude_defaults=True)
            for item in items
        ]
    )


def dump_mocks(mocks: list[Mock]) -> str:
    """Serialize a scenario's mocks to the compact JSON BajutsuKit reads from `BAJUTSU_MOCKS`.

    Alias keys, omitting unset fields.
    """
    import json

    return json.dumps([m.model_dump(by_alias=True, exclude_none=True) for m in mocks])

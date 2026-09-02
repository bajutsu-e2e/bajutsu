"""Deterministic crash-repro scenarios from a crawl (BE-0038).

A crash records the exact action path that collapsed the app UI. This turns that path back into a
runnable `Scenario` — a pure, deterministic, model-free function of the `ScreenMap`: no device, no
LLM, never a verdict. The emitted scenario is something `run` can replay to reproduce the crash, so
a discovered crash becomes a regression test rather than a one-off observation.

`scenario_from_actions` is the shared crawl-path → scenario converter: crash repros build on it
here, and candidate flows (`flows.py`) reuse the same faithful conversion.

A path that taps a normalized coordinate (`tap_point`) has no selector to address, so it cannot be
faithfully replayed; such a path emits no scenario rather than a lossy one (the prime directive:
faithful or nothing).
"""

from __future__ import annotations

from collections.abc import Sequence

from bajutsu.common.drivers import base
from bajutsu.common.scenario.models import Scenario, Selector, Step, TypeText
from bajutsu.common.scenario.serialize import dump_scenario_file
from bajutsu.crawl.core import Action, Crash, ScreenMap, value_for_field
from bajutsu.evidence.redaction import Redactor
from bajutsu.evidence.sink import RunArtifactWriter


def _selector(action: Action) -> Selector | None:
    """The element selector for an id- or label-addressed action, or None when it has neither.

    Id preferred; otherwise label (+ index). An action carrying no addressing condition can't be
    targeted, so it has no faithful selector — the caller treats that as an unsupported repro.
    """
    if action.target:
        return Selector(id=action.target)
    if action.label is not None:
        return Selector(label=action.label, index=action.index)
    return None


def _masks(redactor: Redactor, action: Action) -> bool:
    """Whether a BE-0331 default masks what this action entered.

    Keyed exactly as `Redactor.redact_screen_map` keys it — the action's own target, label and
    `secure` flag — so a value the screen map masks is never the one a flow beside it records
    verbatim.
    """
    return redactor.masks_by_default(
        identifier=action.target,
        label=action.label,
        traits=[base.Trait.SECURE_TEXT_FIELD] if action.secure else [],
    )


def _text(action: Action, redactor: Redactor, *, hint: str, value: str, masked: bool) -> str:
    """The text one emitted `type` step enters, standing the dummy in for a masked value.

    Emitting the placeholder would leave a scenario that types `[REDACTED]` into the field it was
    written to satisfy, so the deterministic dummy takes its place — the convention
    `Action._replay_value` already established for a warm start reading the same masked map back.
    """
    return value_for_field(hint, action.secure) if masked else redactor.redact_text(value)


def _steps(action: Action, redactor: Redactor) -> list[Step] | None:
    """Faithful step(s) for one action, or None when it has no replayable scenario form.

    A `fill` expands to one `type` step per field (mirroring how the crawl performs it); a
    `tap_point` is a coordinate the scenario schema can't address, and an action with no selector
    can't be targeted — both return None.
    """
    if action.kind == "tap":
        sel = _selector(action)
        return [Step(tap=sel)] if sel is not None else None
    if action.kind == "type":
        sel = _selector(action)
        if sel is None:
            return None
        text = _text(
            action,
            redactor,
            hint=f"{action.target} {action.label or ''}",
            value=action.value or "",
            masked=_masks(redactor, action),
        )
        return [Step(type=TypeText(text=text, into=sel))]
    if action.kind == "fill":
        if not action.fields or any(not fid for fid, _ in action.fields):
            return None
        # A fill's `secure` flag is the OR across its fields, so one masked input masks the whole
        # action — the same over-masking `Redactor._redact_field` applies to the map it records.
        whole = _masks(redactor, action)
        return [
            Step(
                type=TypeText(
                    text=_text(
                        action,
                        redactor,
                        hint=fid,
                        value=val,
                        masked=whole or redactor.masks_by_default(identifier=fid, label=None),
                    ),
                    into=Selector(id=fid),
                )
            )
            for fid, val in action.fields
        ]
    return None


def scenario_from_actions(
    actions: Sequence[Action], name: str, redactor: Redactor | None = None
) -> Scenario | None:
    """Build a runnable scenario from a recorded crawl action path.

    The shared converter behind both crash repros and candidate flows. Returns None when the path is
    empty or contains an action with no faithful scenario form (a `tap_point`): a partial replay
    wouldn't reach the target screen, so no scenario is better than a lossy one.

    `redactor` governs the values the steps carry; omitting it applies BE-0331's two defaults alone,
    which is what a crawl (carrying no `redact:` of its own) gets anyway.
    """
    red = redactor if redactor is not None else Redactor(None)
    steps: list[Step] = []
    for action in actions:
        produced = _steps(action, red)
        if produced is None:
            return None
        steps.extend(produced)
    if not steps:
        return None
    return Scenario(name=name, steps=steps)


def crash_scenario(crash: Crash, name: str, redactor: Redactor | None = None) -> Scenario | None:
    """Build a runnable repro scenario from a crash's recorded action path.

    A thin wrapper over `scenario_from_actions`; None when the crash path has no faithful form.
    """
    return scenario_from_actions(crash.actions, name, redactor)


def write_repros(writer: RunArtifactWriter, screen_map: ScreenMap) -> list[str]:
    """Write one repro scenario file per faithfully reproducible crash, returning the names written.

    Files land under `crashes/crash-NNN.yaml` (1-based, in crash order). A crash whose path
    can't be faithfully replayed is skipped, so the numbering tracks the crash list, not the files.

    A repro replays the values the crawl typed, so it goes through the sink like every other run
    artifact (BE-0331) — and through the run's redactor before serialization, since a value the
    defaults mask is a value no artifact recording that action may carry.
    """
    written: list[str] = []
    for i, crash in enumerate(screen_map.crashes, start=1):
        name = f"crash-{i:03d}"
        scenario = crash_scenario(crash, name, writer.redactor)
        if scenario is None:
            continue
        artifact = f"crashes/{name}.yaml"
        writer.write_text(
            artifact, dump_scenario_file([scenario], description=f"Crash repro from crawl: {name}")
        )
        written.append(artifact)
    return written

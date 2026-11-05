"""The per-run inputs the context-bearing assertion kinds read, bundled as one value (BE-0250)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from bajutsu.common.assertions.schema import SchemaContext
from bajutsu.common.assertions.visual import VisualContext
from bajutsu.common.scenario import AttributeMatch

from .golden_context import GoldenContext


@dataclass(frozen=True)
class AttributeRead:
    """One on-demand attribute read (BE-0466): the value, None when absent, or why it failed.

    `error` is the selector failure (no match, ambiguous) or the backend's refusal, kept as a reason
    rather than raised so the assertion fails with it and the poll reads again on the next tick.
    """

    value: str | None = None
    error: str | None = None


def attribute_key(m: AttributeMatch) -> tuple[str, str]:
    """The key an `attribute` assertion's read is stored under: its interpolated selector and name.

    Keyed by content because the evaluator receives the assertion alone, never its block position.
    """
    return json.dumps(m.sel.as_selector(), sort_keys=True), m.name


@dataclass(frozen=True)
class EvalContext:
    """The per-run inputs the context-bearing assertion kinds need, bundled as one value (BE-0250).

    Each field feeds exactly one kind: `visual` the screenshot/baseline paths, `schema` the
    JSON-Schema directory, `golden` the goldens directory, and `clipboard` the device pasteboard
    text already read for the block. Bundling replaces the four loose keyword-only parameters that
    were threaded in lockstep through `evaluate` -> `evaluate_one` -> `run_scenario` ->
    `_run_step_body` -> the runner, so a new context-bearing kind adds a field here instead of a
    parameter at every layer. `clipboard` stays a resolved value, not a reader: the read is gated
    and performed once per block by the runner's `_clipboard_for`, so bundling never turns it into a
    per-step read. `attributes` is the same kind of resolved value: the runner reads each
    `attribute` assertion's element beside every tree it polls (`_attributes_for`, BE-0466).
    """

    visual: VisualContext | None = None
    schema: SchemaContext | None = None
    golden: GoldenContext | None = None
    clipboard: str | None = None
    attributes: Mapping[tuple[str, str], AttributeRead] | None = None

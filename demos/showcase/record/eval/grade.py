"""Deterministic structural grading of a recorded scenario against an expected-operation spec.

`record` is non-deterministic (a live LLM authors the scenario), so grading its output can't be a
byte-for-byte diff against a golden YAML. Instead each evaluation case declares the *operations* it
expects — an ordered list of "tap this thing / type that text / assert this state" — and this module
checks, purely mechanically, whether the recorded scenario contains those operations in order.

The match is intentionally tolerant of what `record` legitimately varies:

  * Incidental steps (settle `wait`s, an extra observe) between the required ops are skipped — the
    expected ops are matched as an ordered *subsequence*, not a contiguous block.
  * A target is matched by the human-readable text on the recorded selector (`label` / `value`),
    case-insensitively, since the `-noax` app carries no stable ids for an exact selector compare.

It stays honest about what structural grading *cannot* verify. When `record` falls all the way down
the stability ladder (DESIGN §5) to a coordinate / index-only selector, the recorded step has no
text to match, so we can't confirm it hit the intended element from the YAML alone. That op is
graded `COORD` (unverifiable), distinct from `MISS` (nothing plausible there at all) — the coord
ratio is itself a record-quality signal, not a pass.

Pure and I/O-free on purpose: it takes already-parsed `Scenario` + spec objects and returns a
report, so the same grader validates the offline demo output (`selfcheck.py`) and the real
on-device recordings (`run_eval.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from bajutsu.scenario import Assertion, Scenario, Selector, Step


class Grade(StrEnum):
    """The outcome of matching one expected op against the recorded scenario."""

    MATCH = "MATCH"  # a recorded step/assertion matched the expected op by its readable text
    COORD = "COORD"  # a plausible step was there but addressed by coordinate/index — unverifiable
    MISS = "MISS"  # nothing in the recording plausibly satisfies the expected op


# One entry in a case's `expect` list, normalized from cases.yaml. Exactly one of the action fields
# is set; `texts` are the accepted human-readable fragments for the target (any-of, case-folded).
@dataclass(frozen=True)
class ExpectedOp:
    kind: str  # "tap" | "type" | "swipe" | "assert"
    texts: tuple[str, ...] = ()  # accepted target-text fragments (tap/assert) — any-of
    type_text: str | None = None  # the literal expected in a `type` step
    into_texts: tuple[str, ...] = ()  # accepted field-text fragments for a `type` target
    direction: str | None = None  # a `swipe` direction, when pinned

    def describe(self) -> str:
        if self.kind == "type":
            into = f" into {'/'.join(self.into_texts)}" if self.into_texts else ""
            return f"type {self.type_text!r}{into}"
        if self.kind == "swipe":
            return f"swipe {self.direction or 'any'}"
        return f"{self.kind} {'|'.join(self.texts)}"


@dataclass
class OpResult:
    op: ExpectedOp
    grade: Grade
    detail: str = ""


@dataclass
class CaseReport:
    case_id: str
    goal: str
    results: list[OpResult] = field(default_factory=list)
    recorded_steps: int = 0

    @property
    def passed(self) -> bool:
        """Every expected op matched by readable text (a COORD or MISS fails the case)."""
        return bool(self.results) and all(r.grade is Grade.MATCH for r in self.results)

    @property
    def matched(self) -> int:
        return sum(r.grade is Grade.MATCH for r in self.results)

    @property
    def coord(self) -> int:
        return sum(r.grade is Grade.COORD for r in self.results)

    @property
    def missed(self) -> int:
        return sum(r.grade is Grade.MISS for r in self.results)


# --- Normalizing a recorded step into what the matcher compares against ---


@dataclass(frozen=True)
class _Move:
    """A recorded step reduced to what grading needs: its action kind, target texts, and whether it
    fell to a coordinate/index-only address (no readable text to verify)."""

    kind: str  # "tap" | "type" | "swipe" | "wait" | "other"
    texts: tuple[str, ...]  # readable target texts (selector label/value), case-folded
    type_text: str | None = None
    into_texts: tuple[str, ...] = ()
    direction: str | None = None
    coord_only: bool = False


def _selector_texts(sel: Selector | None) -> tuple[str, ...]:
    """The matchable text a selector carries, case-folded.

    Includes `id` / `idMatches` as well as `label` / `value`: the `-noax` target has no ids so a
    recording there matches on label/value, but an id-bearing recording (the a11y twin) matches on
    the dotted id — e.g. an expected `favorite` finds the `horse.favorite` id — so the same grader
    scores both sides of the accessibility A/B.
    """
    if sel is None:
        return ()
    return tuple(t.casefold() for t in (sel.id, sel.id_matches, sel.label, sel.value) if t)


def _selector_has_text(sel: Selector | None) -> bool:
    """Whether a selector can be matched by text (label/value) or an exact/regex id."""
    if sel is None:
        return False
    return any((sel.id, sel.id_matches, sel.label, sel.label_matches, sel.value))


def _move_of(step: Step) -> _Move:
    """Reduce a recorded step to a `_Move`. Taps, double-taps and long-presses grade as taps."""
    tap_sel = step.tap or step.double_tap or (step.long_press.sel if step.long_press else None)
    if tap_sel is not None:
        return _Move("tap", _selector_texts(tap_sel), coord_only=not _selector_has_text(tap_sel))
    if step.tap_point is not None:
        # A vision-located tap (e.g. a tab-bar tab): a real tap, but with no readable target text,
        # so it grades COORD — structurally unverifiable, the honest verdict for a coordinate tap.
        return _Move("tap", (), coord_only=True)
    if step.type is not None:
        return _Move(
            "type",
            _selector_texts(step.type.into),
            type_text=step.type.text,
            into_texts=_selector_texts(step.type.into),
            coord_only=not _selector_has_text(step.type.into),
        )
    if step.swipe is not None:
        direction = getattr(step.swipe, "direction", None)
        return _Move("swipe", (), direction=direction, coord_only=direction is None)
    if step.wait is not None:
        return _Move("wait", ())
    return _Move("other", ())


_TAP_KINDS = {"tap"}


def _any_contains(haystacks: tuple[str, ...], needles: tuple[str, ...]) -> bool:
    """Whether any expected fragment is a substring of any recorded text (both already case-folded)."""
    lowered = tuple(n.casefold() for n in needles)
    return any(n in h for h in haystacks for n in lowered)


# --- Assertion text extraction (for grading an `assert` op against scenario.expect) ---


def _assertion_texts(a: Assertion) -> tuple[str, ...]:
    """Every readable fragment an assertion exposes: its compared text and its selector's text."""
    out: list[str] = []
    for tm in (a.value, a.label):
        if tm is not None:
            out += [s for s in (tm.equals, tm.contains, tm.matches) if s]
            out += list(_selector_texts(tm.sel))
    if a.exists is not None:
        out += list(_selector_texts(a.exists.sel))
    for state in (a.enabled, a.disabled, a.selected):
        if state is not None:
            out += list(_selector_texts(state))
    if a.clipboard is not None:
        out += [s for s in (a.clipboard.equals, a.clipboard.matches) if s]
    return tuple(t.casefold() for t in out)


# --- The matcher ---


def _grade_step_op(op: ExpectedOp, moves: list[_Move], start: int) -> tuple[Grade, int, str]:
    """Match a tap/type/swipe op against `moves[start:]`; return (grade, next-pointer, detail).

    Ordered subsequence: scan forward for the first compatible move whose readable text satisfies the
    op. If none matches by text but a compatible coordinate-only move sits there, grade COORD and
    consume it (record produced the action but only positionally). Otherwise MISS, pointer unmoved.
    """
    want_kinds = _TAP_KINDS if op.kind == "tap" else {op.kind}
    first_coord: int | None = None
    for i in range(start, len(moves)):
        move = moves[i]
        if move.kind not in want_kinds:
            continue
        if _text_satisfies(op, move):
            return Grade.MATCH, i + 1, ", ".join(move.texts) or move.direction or ""
        if move.coord_only and first_coord is None:
            first_coord = i
    if first_coord is not None:
        return Grade.COORD, first_coord + 1, "addressed by coordinate/index (no text to verify)"
    return Grade.MISS, start, "no matching step recorded"


def _text_satisfies(op: ExpectedOp, move: _Move) -> bool:
    """Whether a recorded move's readable text satisfies the expected op."""
    if op.kind == "swipe":
        return op.direction is None or move.direction == op.direction
    if op.kind == "type":
        if op.type_text is not None and not _type_text_ok(op.type_text, move.type_text):
            return False
        return not op.into_texts or _any_contains(move.into_texts, op.into_texts)
    return _any_contains(move.texts, op.texts)


def _type_text_ok(expected: str, recorded: str | None) -> bool:
    """A typed literal matches if equal (case-folded) or the recorded text contains it — tolerant of
    a secret rewritten to a `${secrets.X}` token, which we can't compare to a literal, so accept it."""
    if recorded is None:
        return False
    if recorded.strip().startswith("${secrets."):
        return True
    return expected.casefold() in recorded.casefold()


def _grade_assert_op(op: ExpectedOp, scenario: Scenario) -> OpResult:
    """Match an `assert` op against the scenario's final `expect` assertions by readable text."""
    for a in scenario.expect:
        if _any_contains(_assertion_texts(a), op.texts):
            return OpResult(op, Grade.MATCH, "matched a final assertion")
    if not scenario.expect:
        return OpResult(op, Grade.MISS, "scenario recorded no assertions")
    return OpResult(op, Grade.MISS, "no assertion matched the expected text")


def grade(case_id: str, goal: str, expected: list[ExpectedOp], scenario: Scenario) -> CaseReport:
    """Grade a recorded `scenario` against a case's ordered `expected` ops."""
    moves = [_move_of(s) for s in scenario.steps]
    report = CaseReport(case_id=case_id, goal=goal, recorded_steps=len(scenario.steps))
    ptr = 0
    for op in expected:
        if op.kind == "assert":
            report.results.append(_grade_assert_op(op, scenario))
            continue
        grade_, ptr, detail = _grade_step_op(op, moves, ptr)
        report.results.append(OpResult(op, grade_, detail))
    return report

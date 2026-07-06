"""Prove the grader deterministically — no Simulator, no API key, no LLM.

`run_eval.py` grades *real* on-device recordings, which needs a device and a live model. This
self-check exercises the same grader (`grade.py`) against fixtures that need neither: the offline
`generate_from_nl.py` demo output (a real record loop driven by a deterministic keyword stand-in)
plus a few hand-built scenarios that pin the COORD / MISS / subsequence rules. It's what you run to
trust the grading logic before spending device time — and it runs anywhere `make check` does.

    uv run python demos/showcase/record/eval/selfcheck.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Import the sibling grader and the parent-dir offline demo without installing them as packages.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

from generate_from_nl import author  # noqa: E402  (parent-dir offline demo)
from grade import ExpectedOp, Grade, grade  # noqa: E402

from bajutsu.scenario import Scenario, Step  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail and not ok else ""))
    return ok


def _step(spec: dict[str, object]) -> Step:
    return Step.model_validate(spec)


def check_offline_demo_passes() -> bool:
    """The offline Stable→Favorite recording satisfies an equivalent expected-op spec, end to end."""
    scenario = author("Tap Horse, tap Favorite, then check the favorite shows on")
    spec = [
        ExpectedOp(kind="tap", texts=("Horse", "stable.row")),
        ExpectedOp(kind="tap", texts=("favorite",)),
        ExpectedOp(kind="assert", texts=("on",)),
    ]
    report = grade("offline-demo", "", spec, scenario)
    return _check(
        "offline demo grades PASS with all ops matched",
        report.passed and report.matched == 3,
        f"passed={report.passed} matched={report.matched} coord={report.coord} miss={report.missed}",
    )


def check_missing_op_is_missed() -> bool:
    """An expected op the recording never performs grades MISS and fails the case."""
    scenario = author("Tap Horse, tap Favorite, then check the favorite shows on")
    spec = [
        ExpectedOp(kind="tap", texts=("Horse",)),
        ExpectedOp(kind="type", type_text="never typed", into_texts=("note",)),
    ]
    report = grade("missing", "", spec, scenario)
    miss = report.results[1].grade is Grade.MISS
    return _check("a never-performed op grades MISS (case fails)", not report.passed and miss)


def check_coordinate_tap_is_unverifiable() -> bool:
    """A tap addressed only by index (no readable target) grades COORD, not MATCH or MISS."""
    scenario = Scenario(name="coord", steps=[_step({"tap": {"index": 0}})], expect=[])
    spec = [ExpectedOp(kind="tap", texts=("Favorite",))]
    report = grade("coord", "", spec, scenario)
    r = report.results[0]
    return _check(
        "a coordinate/index-only tap grades COORD (unverifiable, not a pass)",
        r.grade is Grade.COORD and not report.passed,
        f"grade={r.grade}",
    )


def check_subsequence_skips_incidental_steps() -> bool:
    """Incidental steps between the required ops (a settle wait, an unrelated tap) are skipped."""
    scenario = Scenario(
        name="subseq",
        steps=[
            _step({"tap": {"label": "Log"}}),
            _step({"wait": {"for": {"label": "Note"}, "timeout": 5}}),  # incidental
            _step({"type": {"text": "morning ride", "into": {"label": "Note field"}}}),
            _step({"tap": {"label": "Submit"}}),
        ],
        expect=[],
    )
    spec = [
        ExpectedOp(kind="tap", texts=("Log",)),
        ExpectedOp(kind="type", type_text="morning ride", into_texts=("note",)),
        ExpectedOp(kind="tap", texts=("Submit",)),
    ]
    report = grade("subseq", "", spec, scenario)
    return _check(
        "required ops match as an ordered subsequence past incidental steps", report.passed
    )


def check_secret_token_type_is_accepted() -> bool:
    """A typed value rewritten to a `${secrets.X}` token still satisfies a `type` op (can't compare
    a literal to a masked token, so we accept the token)."""
    scenario = Scenario(
        name="secret",
        steps=[_step({"type": {"text": "${secrets.PASSWORD}", "into": {"label": "Password"}}})],
        expect=[],
    )
    spec = [ExpectedOp(kind="type", type_text="hunter2", into_texts=("password",))]
    report = grade("secret", "", spec, scenario)
    return _check("a tokenized secret satisfies a type op", report.passed)


def main() -> int:
    print("record eval grader self-check (offline, deterministic):\n")
    checks = [
        check_offline_demo_passes(),
        check_missing_op_is_missed(),
        check_coordinate_tap_is_unverifiable(),
        check_subsequence_skips_incidental_steps(),
        check_secret_token_type_is_accepted(),
    ]
    passed = sum(checks)
    print(f"\n{passed}/{len(checks)} checks passed.")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())

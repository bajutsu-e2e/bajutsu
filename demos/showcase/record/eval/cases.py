"""Load the evaluation cases (representative goal + expected operations) from `cases.yaml`.

The I/O layer over the pure grader (`grade.py`): it parses the on-disk case spec into `Case`
objects the runner drives and the grader scores. Kept separate so `grade.py` stays I/O-free.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from grade import ExpectedOp  # local module (this dir is on sys.path when run directly)

from bajutsu import _yaml

CASES_YAML = Path(__file__).with_name("cases.yaml")


@dataclass(frozen=True)
class Case:
    id: str
    goal: str
    expected: list[ExpectedOp]


def _as_texts(value: object) -> tuple[str, ...]:
    """A scalar or list of accepted text fragments → a tuple of strings."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(str(v) for v in value)
    raise ValueError(f"expected a string or list of strings, got {value!r}")


def _op_of(entry: dict[str, object]) -> ExpectedOp:
    """Parse one `expect` entry (exactly one of tap / type / swipe / assert) into an `ExpectedOp`."""
    if "tap" in entry:
        return ExpectedOp(kind="tap", texts=_as_texts(entry["tap"]))
    if "assert" in entry:
        return ExpectedOp(kind="assert", texts=_as_texts(entry["assert"]))
    if "swipe" in entry:
        spec = entry["swipe"]
        direction = spec.get("direction") if isinstance(spec, dict) else None
        return ExpectedOp(kind="swipe", direction=direction)
    if "type" in entry:
        spec = entry["type"]
        if not isinstance(spec, dict) or "text" not in spec:
            raise ValueError(f"a `type` op needs a `text` field: {entry!r}")
        return ExpectedOp(
            kind="type",
            type_text=str(spec["text"]),
            into_texts=_as_texts(spec.get("into")),
        )
    raise ValueError(f"unknown op (need one of tap/type/swipe/assert): {entry!r}")


def load_cases(path: Path = CASES_YAML) -> list[Case]:
    """Parse `cases.yaml` into `Case` objects."""
    data = _yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"{path} must be a list of cases")
    cases: list[Case] = []
    for raw in data:
        expected = [_op_of(e) for e in raw["expect"]]
        cases.append(Case(id=str(raw["id"]), goal=str(raw["goal"]), expected=expected))
    return cases

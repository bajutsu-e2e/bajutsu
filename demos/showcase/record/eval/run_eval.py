"""Drive real `bajutsu record` against a showcase `-noax` app and grade its output.

This is the on-device accuracy/stability harness (heavy path — a Mac + booted Simulator + built
`-noax` app + `ANTHROPIC_API_KEY`, exactly like `make -C demos/showcase record`). For each case in
`cases.yaml` it runs `record` K times, structurally grades every recording against the case's
expected operations (`grade.py`), and reports:

  * per recording — MATCH / COORD / MISS for each expected op, and whether the case passed;
  * per case across K runs — the pass rate (the *stability* number: how often `record` produces the
    right operations for the same goal);
  * overall — cases passed and the aggregate op-match rate.

`record` is non-deterministic, so a single run is a data point, not a verdict — use `--reps N` (e.g.
5) to measure stability, not just accuracy. This never runs in the deterministic gate (it needs a
device and calls an LLM); it's an opt-in measurement, like the on-device E2E path.

    make -C demos/showcase runner-build   # the XCUITest runner (once); + a booted Simulator with the -noax app built
    export ANTHROPIC_API_KEY=...
    make -C demos/showcase swiftui-noax-build
    uv run python demos/showcase/record/eval/run_eval.py --reps 3

Grade the offline demo instead (no device, no key) with `selfcheck.py`, which proves the grader.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

# This script's own dir is on sys.path when run directly, so the sibling modules import bare.
from cases import Case, load_cases
from grade import CaseReport, Grade, OpResult, grade

from bajutsu.scenario import Scenario, load_scenario_file

_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_CONFIG = _ROOT / "demos" / "showcase" / "showcase.config.yaml"

_MARK = {Grade.MATCH: "✓", Grade.COORD: "≈", Grade.MISS: "✗"}


def _record_once(case: Case, out: Path, args: argparse.Namespace) -> Scenario:
    """Invoke `bajutsu record` for one goal, returning the parsed scenario it wrote to `out`."""
    cmd = [
        sys.executable, "-m", "bajutsu", "record",
        "--target", args.target,
        "--backend", args.backend,
        "--config", str(args.config),
        "--goal", case.goal,
        "--out", str(out),
    ]  # fmt: skip
    if args.udid:
        cmd += ["--udid", args.udid]
    if not args.erase:
        cmd += ["--no-erase"]
    # Let record's progress stream to our stderr so a watcher sees the live authoring; record writes
    # its one result line to stdout, which we ignore in favor of parsing the YAML file it produced.
    proc = subprocess.run(cmd, cwd=_ROOT, stdout=subprocess.DEVNULL, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"`record` exited {proc.returncode} for case {case.id!r}")
    if not out.exists():
        raise RuntimeError(f"`record` wrote no scenario for case {case.id!r} (expected {out})")
    scenarios = load_scenario_file(out.read_text(encoding="utf-8")).scenarios
    if not scenarios:
        raise RuntimeError(f"`record` wrote an empty scenario file for case {case.id!r}")
    return scenarios[0]


def _print_report(report: CaseReport, rep: int, reps: int) -> None:
    tag = f"[{report.case_id}]" + (f" run {rep}/{reps}" if reps > 1 else "")
    verdict = "PASS" if report.passed else "FAIL"
    print(f"\n{tag}  {verdict}  ({report.recorded_steps} steps recorded)")
    for r in report.results:
        _print_op(r)


def _print_op(r: OpResult) -> None:
    detail = f"  — {r.detail}" if r.detail else ""
    print(f"    {_MARK[r.grade]} {r.op.describe()}{detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--target", default="showcase-swiftui-noax", help="the -noax target to author against"
    )
    parser.add_argument("--backend", default="ios")
    parser.add_argument("--config", type=Path, default=_DEFAULT_CONFIG)
    parser.add_argument("--udid", default="", help="Simulator udid (default: the booted one)")
    parser.add_argument("--reps", type=int, default=1, help="runs per case — >1 measures stability")
    parser.add_argument(
        "--case", action="append", default=[], help="only these case ids (repeatable)"
    )
    parser.add_argument(
        "--no-erase",
        dest="erase",
        action="store_false",
        help="don't erase the device between runs (faster, less clean)",
    )
    parser.add_argument(
        "--keep", action="store_true", help="keep the recorded YAML files for inspection"
    )
    args = parser.parse_args(argv)

    cases = [c for c in load_cases() if not args.case or c.id in args.case]
    if not cases:
        print("no matching cases", file=sys.stderr)
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="record-eval-"))
    passes: dict[str, int] = {c.id: 0 for c in cases}
    ran: dict[str, int] = {c.id: 0 for c in cases}
    total_ops = total_match = total_coord = 0

    for case in cases:
        for rep in range(1, args.reps + 1):
            out = tmp / f"{case.id}-{rep}.yaml"
            try:
                scenario = _record_once(case, out, args)
            except RuntimeError as e:
                print(f"\n[{case.id}] run {rep}/{args.reps}  ERROR — {e}", file=sys.stderr)
                ran[case.id] += 1
                continue
            report = grade(case.id, case.goal, case.expected, scenario)
            _print_report(report, rep, args.reps)
            ran[case.id] += 1
            passes[case.id] += int(report.passed)
            total_ops += len(report.results)
            total_match += report.matched
            total_coord += report.coord

    print("\n" + "=" * 60)
    print("Stability (pass rate per case across runs):")
    for case in cases:
        n = ran[case.id]
        rate = f"{passes[case.id]}/{n}" if n else "0/0 (all errored)"
        print(f"  {case.id:<22} {rate}")
    cases_passed = sum(passes[c.id] == ran[c.id] and ran[c.id] > 0 for c in cases)
    print(f"\nCases fully passing: {cases_passed}/{len(cases)}")
    if total_ops:
        print(
            f"Op accuracy: {total_match}/{total_ops} matched, {total_coord} coordinate-only (unverifiable)"
        )

    if args.keep:
        print(f"\nRecorded scenarios kept in {tmp}")
    return 0 if cases_passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())

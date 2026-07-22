"""manifest.json (the run's single source of truth) and JUnit XML."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import asdict
from xml.etree import ElementTree as ET

from bajutsu import __version__
from bajutsu.orchestrator import RunResult, scenario_slug


def git_revision() -> str | None:
    """The current git commit, or None when the run isn't inside a git checkout.

    Best-effort run provenance (BE-0049): any failure — not a repo, git absent — yields None so the
    stamp simply omits the revision rather than aborting the run.
    """
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 — git resolved on PATH; any failure → None below
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    # A shimmed/aliased `git` could exit 0 with blank stdout; treat that as "unknown", not an empty stamp.
    return out.stdout.strip() or None


def _run_backend(results: list[RunResult]) -> str:
    """The actuator(s) that drove the run, joined ordered-unique.

    Usually a single name. BE-0240 resolves the actuator per scenario, so a multi-actuator platform
    could mix distinct per-scenario backends, which are joined here (e.g. ``"a, b"``); iOS is a
    single actuator today (XCUITest), so an iOS run reports one name.
    """
    names = dict.fromkeys(r.backend for r in results if r.backend)  # ordered-unique
    return ", ".join(names)


# The render model's version. Bump when a field the report needs is added, so an older run can be
# detected and its newer-only sections shown as "not captured" rather than failing (BE-0068).
# v2 (BE-0005): optional top-level "idb" version provenance — retired with idb (BE-0290); no longer
#   written, but old manifests may still carry it (an unknown top-level key is ignored on load).
# v3 (BE-0049): optional top-level "provenance" block (scenario hash + tool/git version).
# v4 (BE-0076): optional top-level "matrix" block (engine x scenario aggregate of per-engine verdicts).
SCHEMA_VERSION = 4


def _matrix(results: list[RunResult]) -> dict[str, object] | None:
    """Aggregate engine-tagged results into the engine x scenario pass/fail matrix (BE-0076).

    Pure aggregation of the verdicts already in `results`: it derives the engine and scenario axes
    (each in first-seen order) and a `cells[scenario][engine]` view of every per-engine verdict, so
    a scenario green on one engine and red on another is the machine-detected incompatibility. None
    for a single-engine / iOS run (no result carries an `engine`), so that path keeps the v1 shape.
    """
    if not any(r.engine for r in results):
        return None
    engines = list(dict.fromkeys(r.engine for r in results if r.engine))  # ordered-unique
    scenarios = list(dict.fromkeys(r.scenario for r in results))
    cells: dict[str, dict[str, dict[str, object]]] = {s: {} for s in scenarios}
    for r in results:
        # The runner stamps `sid` with the dir it actually wrote (`NN-slug`), so the cell links to
        # the real `<engine>/<sid>` evidence; fall back to the slug only for a sid-less result.
        sid = r.sid or scenario_slug(r.scenario)
        cells[r.scenario][r.engine] = {
            "ok": r.ok,
            "sid": f"{r.engine}/{sid}",
            "failure": r.failure,
        }
    return {"engines": engines, "scenarios": scenarios, "cells": cells}


def run_provenance(
    scenario_yaml: str,
    *,
    git_revision: str | None,
    config_source: dict[str, str] | None = None,
) -> dict[str, object]:
    """Stamp identifying the executed scenario and the tooling, for the longitudinal flakiness view.

    A stable fingerprint of the executed scenario plus the tool (and git) version lets accumulated
    runs be grouped by identity, so a verdict that flips while the fingerprint is unchanged is true
    flakiness — not an edited scenario (BE-0049). Pure metadata: it never enters a verdict.

    Args:
        scenario_yaml: The executed scenario's serialized form (the evidence snapshot). Its content
            is what the hash fingerprints — the logical scenario, so two runs of the same scenario
            share a hash and group together. The snapshot masks a literal `totp.secret` seed
            (BE-0152) and keeps `${secrets.*}` references, so identity stays stable regardless of
            which secrets a run resolved; the run-level secret-value scrub runs afterward and does
            not affect the fingerprint.
        git_revision: The current git revision (the working tree's HEAD), or None when the run isn't
            under git (the key is then omitted rather than recorded as null).
        config_source: When the config came from a Git source (BE-0063), the repo + resolved commit
            (`host` / `owner` / `repo` / `ref` / `sha`), so a branch-based run states the exact commit
            it executed. None for a local config (the key is then omitted).
    """
    prov: dict[str, object] = {
        "scenarioHash": "sha256:" + hashlib.sha256(scenario_yaml.encode("utf-8")).hexdigest(),
        "toolVersion": __version__,
    }
    if git_revision is not None:
        prov["gitRevision"] = git_revision
    if config_source is not None:
        prov["configSource"] = config_source
    return prov


def manifest_dict(
    run_id: str,
    results: list[RunResult],
    *,
    source_name: str | None = None,
    provenance: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build the manifest — the run's canonical, versioned render model (BE-0068).

    RunResult and its parts are dataclasses, so asdict() captures step/expect outcomes verbatim.
    `backend` is the actuator that drove the run (each scenario also carries its own `backend`);
    `sourceName` is the label the report's YAML toggle shows, persisted here so a re-render can
    recover it.

    `provenance` is the run-identity stamp from `run_provenance` (BE-0049), never part of the verdict.
    """
    manifest: dict[str, object] = {
        "schemaVersion": SCHEMA_VERSION,
        "runId": run_id,
        "ok": all(r.ok for r in results),
        "backend": _run_backend(results),
        "sourceName": source_name,
        "scenarios": [asdict(r) for r in results],
    }
    if provenance:
        manifest["provenance"] = provenance
    # The engine x scenario matrix for a `--browsers` run (BE-0076), a pure aggregation of the
    # per-engine verdicts already in `scenarios`. Omitted for a single-engine / iOS run, which keeps
    # the v1 shape; `ok` above already aggregates every engine x scenario verdict (all-must-pass).
    if (matrix := _matrix(results)) is not None:
        manifest["matrix"] = matrix
    return manifest


def _details(r: RunResult) -> str:
    lines: list[str] = []
    for s in r.steps:
        status = "ok" if s.ok else "FAIL"
        lines.append(f"step {s.index} {s.action}: {status} {s.reason}".rstrip())
    for a in r.expect_results:
        status = "ok" if a.ok else "FAIL"
        lines.append(f"expect {a.kind}: {status} {a.reason}".rstrip())
    return "\n".join(lines)


def junit_xml(results: list[RunResult]) -> str:
    """One testcase per scenario; a failing scenario gets a <failure>.

    On a `--browsers` cross-engine run each result carries its `engine`, so the case is keyed by it
    (`classname="bajutsu.<engine>"`) — CI then sees `chromium.login` and `webkit.login` as distinct
    cases and attributes a per-engine failure without reading the manifest (BE-0076). A single-engine
    result has no `engine`, so its classname stays `bajutsu`.
    """
    failures = sum(0 if r.ok else 1 for r in results)
    suite = ET.Element("testsuite", name="bajutsu", tests=str(len(results)), failures=str(failures))
    for r in results:
        classname = f"bajutsu.{r.engine}" if r.engine else "bajutsu"
        case = ET.SubElement(suite, "testcase", name=r.scenario, classname=classname)
        if not r.ok:
            failure = ET.SubElement(case, "failure", message=r.failure or "failed")
            failure.text = _details(r)
    return ET.tostring(suite, encoding="unicode")

"""manifest.json (the run's single source of truth) and JUnit XML."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import asdict
from xml.etree import ElementTree as ET

from bajutsu import __version__
from bajutsu.common.assertions import AssertionResult
from bajutsu.common.orchestrator import RunResult, StepOutcome, scenario_slug

# A run-history label longer than this is rejected at the boundary rather than truncated (BE-0404
# unit 2), so an operator learns the label was refused instead of finding a silently shortened one.
MAX_LABEL_LENGTH = 120


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
    names = dict.fromkeys(
        # A multi-target scenario leaves `backend` empty — no one actuator drove it — and names one
        # per declared target instead (BE-0428), so the run-level answer joins those rather than
        # reporting nothing at all for a run every one of whose scenarios was multi-target.
        backend
        for r in results
        for backend in (
            [r.backend] if r.backend else [d.backend for d in r.target_devices.values()]
        )
        if backend
    )  # ordered-unique
    return ", ".join(names)


# The render model's version. Bump when a field the report needs is added, so an older run can be
# detected and its newer-only sections shown as "not captured" rather than failing (BE-0068).
# v2 (BE-0005): optional top-level "idb" version provenance — retired with idb (BE-0290); no longer
#   written, but old manifests may still carry it (an unknown top-level key is ignored on load).
# v3 (BE-0049): optional top-level "provenance" block (scenario hash + tool/git version).
# v4 (BE-0076): optional top-level "matrix" block (engine x scenario aggregate of per-engine verdicts).
# v5: per-step "actuations" (and per-scenario "expect_actuations") — what the driver actually did to the
#   screen, the coordinate/geometry half of the `actionLog` evidence kind. An older run simply has none,
#   so the report shows no actuation row for it rather than failing to load.
# v6 (BE-0348): "started_at" (and network.json's "startedAt") are absolute wall-clock epoch seconds
#   rather than video-relative offsets, and the anchor to subtract from them — "video_anchor_s", now
#   itself absolute — is persisted instead of dropped. A v5-or-older run carries no anchor, so a
#   reader derives 0.0 for it and its already-relative timestamps render unchanged.
# v7 (BE-XXXX): an actuation may carry "substitution" — why the element actuated is not the one the
#   driver's default rule would have named. Absent on the ordinary path and on every older run, which
#   is the same thing a reader sees either way: no substitution happened.
# v8 (BE-0377): a step may carry "generated" — the value a `generate` step produced for `vars.*`.
#   Absent on every other action and on every older run, so a reader shows no generated-value row
#   for it rather than failing to load.
# v9: an artifact may carry "depicts" — which screen the file shows, as "<driver>:<moment>". A
#   reader pairs a step's screenshot with its element tree by comparing the two values, and draws no
#   element frames when they differ. Absent on every older run, where nothing recorded which side of
#   the action an artifact was taken on, so a reader falls back to the pre-field choice
#   (`evidence.step_view`) rather than dropping frames a stored run has always shown.
# v10 (BE-0404): optional top-level "target" and "label" — the target the run ran (so "the Android
#   target passes while the iOS target fails" is computable from stored data) and the run-history
#   partition an operator may set. Absent on every older run, which reads as "this run named
#   neither": it drops out of a per-target comparison and stays visible under any label filter.
# v11 (BE-0428): a step may carry "target" — which declared target ran it — and a scenario may
#   carry "target_devices", one device row per target a multi-target scenario declared, with the
#   singular "backend"/"device"/"device_name"/"device_runtime" left empty there because none of them
#   describes the whole scenario any more. Absent on every older run and on every single-target run,
#   where the singular fields still say everything there is to say.
SCHEMA_VERSION = 11


def _matrix(results: list[RunResult]) -> dict[str, object] | None:
    """Aggregate engine-tagged results into the engine x scenario pass/fail matrix (BE-0076).

    Pure aggregation of the verdicts already in `results`: it derives the engine axis (first-seen
    order) and a `cells[scenario][engine]` view of every per-engine verdict, so a scenario green on
    one engine and red on another is the machine-detected incompatibility. None for a single-engine
    / iOS run (no result carries an `engine`), so that path keeps the v1 shape.

    A `.name` is not guaranteed unique across a multi-file run (`run/cli.py`'s
    `_visual_asserting_scenarios` hits the same trap and avoids it by keying on `id()` instead), so
    keying `cells` on the raw name would let a second same-named scenario silently overwrite the
    first's cell. Two same-named scenarios are told apart by their position among same-named results
    *within each engine's own ordering* — a `--browsers` pass runs every engine over the identical
    ordered scenario list, so the Nth "login" on chromium and the Nth "login" on webkit are the same
    source scenario. The first occurrence of a name keeps the bare name as its row label; a later
    occurrence gets a "(N)" suffix, bumped past any label already taken — including one a *different*
    scenario's literal name happens to equal — so it never collides with an already-assigned row.
    """
    if not any(r.engine for r in results):
        return None
    engines = list(dict.fromkeys(r.engine for r in results if r.engine))  # ordered-unique

    occurrence: dict[tuple[str, str], int] = {}  # (engine, name) -> next occurrence index
    label_of_occurrence: dict[tuple[str, int], str] = {}  # (name, occurrence index) -> row label
    used_labels: set[str] = set()  # every label already handed out, so a synthesized "(N)" suffix
    # can't collide with another scenario whose literal name happens to equal that suffixed string
    scenarios: list[str] = []
    cells: dict[str, dict[str, dict[str, object]]] = {}
    for r in results:
        idx = occurrence.get((r.engine, r.scenario), 0)
        occurrence[(r.engine, r.scenario)] = idx + 1
        occurrence_key = (r.scenario, idx)
        label = label_of_occurrence.get(occurrence_key)
        if label is None:
            label = r.scenario
            n = 1
            while label in used_labels:
                n += 1
                label = f"{r.scenario} ({n})"
            used_labels.add(label)
            label_of_occurrence[occurrence_key] = label
            scenarios.append(label)
            cells[label] = {}
        # The runner stamps `sid` with the dir it actually wrote (`NN-slug`), so the cell links to
        # the real `<engine>/<sid>` evidence; fall back to the slug only for a sid-less result.
        sid = r.sid or scenario_slug(r.scenario)
        cells[label][r.engine] = {
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
    target: str | None = None,
    label: str | None = None,
) -> dict[str, object]:
    """Build the manifest — the run's canonical, versioned render model (BE-0068).

    RunResult and its parts are dataclasses, so `_scenario_dict` captures step/expect outcomes
    verbatim — minus `wall_offset_s`, a same-process-only conversion constant with no meaning once
    persisted (BE-0348 kept `video_anchor_s`, an absolute instant, but not this one — see
    `_scenario_dict`). `backend` is the actuator that drove the run (each scenario also carries its
    own `backend`); `sourceName` is the label the report's YAML toggle shows, persisted here so a
    re-render can recover it.

    `provenance` is the run-identity stamp from `run_provenance` (BE-0049), never part of the verdict.
    `target` is the target the run ran (BE-0404 unit 3), recorded rather than injected: the runner
    already resolved it into the `Effective` config the run executed against. It is a run-level
    value, not a per-scenario one — one run resolves one target — so it sits at the top level
    beside `backend` rather than on each scenario. `label` is the run-history partition (unit 2),
    opaque operator free-text: never parsed, never matched against config, never authorized on.
    """
    manifest: dict[str, object] = {
        "schemaVersion": SCHEMA_VERSION,
        "runId": run_id,
        "ok": all(r.ok for r in results),
        "backend": _run_backend(results),
        "sourceName": source_name,
        "scenarios": [_scenario_dict(r) for r in results],
    }
    if target:
        manifest["target"] = target
    if label:
        manifest["label"] = label
    if provenance:
        manifest["provenance"] = provenance
    # The engine x scenario matrix for a `--browsers` run (BE-0076), a pure aggregation of the
    # per-engine verdicts already in `scenarios`. Omitted for a single-engine / iOS run, which keeps
    # the v1 shape; `ok` above already aggregates every engine x scenario verdict (all-must-pass).
    if (matrix := _matrix(results)) is not None:
        manifest["matrix"] = matrix
    return manifest


def _scenario_dict(r: RunResult) -> dict[str, object]:
    """`asdict(r)`, minus `wall_offset_s`.

    `wall_offset_s` is `scenario_wall_start - scenario_start` — a delta that converts *this run's*
    `time.monotonic()` instants to wall-clock ones. It exists only for `pipeline.py` to carry a
    network exchange's monotonic receive time onto the same absolute footing as `video_anchor_s`
    while the run is still in-process; no monotonic instant survives into the manifest for a later
    reader to convert with it, so persisting it would be noise at best. `video_anchor_s`, by
    contrast, is itself already an absolute instant (BE-0348) and stays.
    """
    d = asdict(r)
    d.pop("wall_offset_s", None)
    return d


def _details(r: RunResult) -> str:
    """The failure body: every phase's outcomes in the order they ran.

    The lifecycle phases (BE-0392) are labeled rather than merged into the numbered `steps` — each
    counts from zero, so an unlabeled line would read as a second `step 0`. Without them a run that
    failed only in `before` or `after` would carry an empty `<failure>` body, since neither phase's
    outcomes live in `steps`.
    """

    def step_line(prefix: str, s: StepOutcome) -> str:
        return f"{prefix}step {s.index} {_step_label(s)}: {_status(s.ok)} {s.reason}".rstrip()

    def expect_line(a: AssertionResult) -> str:
        kind = f"{a.kind}@{a.target}" if a.target else a.kind
        return f"expect {kind}: {_status(a.ok)} {a.reason}".rstrip()

    return "\n".join(
        [
            *(step_line("before ", s) for s in r.before_outcomes),
            *(step_line("", s) for s in r.steps),
            *(expect_line(a) for a in r.expect_results),
            *(step_line("after ", s) for s in r.after_outcomes),
        ]
    )


def _status(ok: bool) -> str:
    return "ok" if ok else "FAIL"


def _step_label(s: StepOutcome) -> str:
    """The step's action, qualified by the target that ran it on a multi-target scenario (BE-0428).

    A `<failure>` body a CI log shows is often all a reviewer of a cross-platform scenario sees, so
    it has to say which platform the step ran on; a scenario declaring no targets is unchanged.
    """
    return f"{s.action}@{s.target}" if s.target else s.action


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

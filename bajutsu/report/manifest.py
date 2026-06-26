"""manifest.json (the run's single source of truth) and JUnit XML."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from xml.etree import ElementTree as ET

from bajutsu import __version__
from bajutsu.idb_version import IdbVersions
from bajutsu.orchestrator import RunResult


def _run_backend(results: list[RunResult]) -> str:
    """The actuator that drove the run.

    One actuator is fixed per run, so this is normally a single name; if scenarios somehow differ,
    they are joined.
    """
    names = dict.fromkeys(r.backend for r in results if r.backend)  # ordered-unique
    return ", ".join(names)


# The render model's version. Bump when a field the report needs is added, so an older run can be
# detected and its newer-only sections shown as "not captured" rather than failing (BE-0068).
# v2 (BE-0005): optional top-level "idb" version provenance.
# v3 (BE-0049): optional top-level "provenance" block (scenario hash + tool/git version).
SCHEMA_VERSION = 3


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
        scenario_yaml: The executed scenario's serialized form. Its content is what the hash
            fingerprints — the logical scenario, so two runs of the same scenario share a hash and
            group together (the fingerprint is taken before any evidence redaction, which keeps the
            identity stable regardless of which secrets a run resolved).
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
    idb_versions: IdbVersions | None = None,
    provenance: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build the manifest — the run's canonical, versioned render model (BE-0068).

    RunResult and its parts are dataclasses, so asdict() captures step/expect outcomes verbatim.
    `backend` is the actuator that drove the run (each scenario also carries its own `backend`);
    `sourceName` is the label the report's YAML toggle shows, persisted here so a re-render can
    recover it.

    `idb_versions`, when the run used the idb backend, records the `idb_companion` / client versions
    it was driven against — provenance only, so it never enters `ok` (BE-0005). `provenance` is the
    run-identity stamp from `run_provenance` (BE-0049), likewise never part of the verdict.
    """
    manifest: dict[str, object] = {
        "schemaVersion": SCHEMA_VERSION,
        "runId": run_id,
        "ok": all(r.ok for r in results),
        "backend": _run_backend(results),
        "sourceName": source_name,
        "scenarios": [asdict(r) for r in results],
    }
    # Only record the block when at least one version is known: a `{companion: null, client: null}`
    # block carries no provenance and is indistinguishable from "not captured", so omit it.
    if idb_versions is not None and (
        idb_versions.companion is not None or idb_versions.client is not None
    ):
        manifest["idb"] = {"companion": idb_versions.companion, "client": idb_versions.client}
    if provenance:
        manifest["provenance"] = provenance
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
    """One testcase per scenario; a failing scenario gets a <failure>."""
    failures = sum(0 if r.ok else 1 for r in results)
    suite = ET.Element("testsuite", name="bajutsu", tests=str(len(results)), failures=str(failures))
    for r in results:
        case = ET.SubElement(suite, "testcase", name=r.scenario, classname="bajutsu")
        if not r.ok:
            failure = ET.SubElement(case, "failure", message=r.failure or "failed")
            failure.text = _details(r)
    return ET.tostring(suite, encoding="unicode")

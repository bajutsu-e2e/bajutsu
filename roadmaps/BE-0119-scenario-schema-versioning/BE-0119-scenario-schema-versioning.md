**English** · [日本語](BE-0119-scenario-schema-versioning-ja.md)

# BE-0119 — Version the scenario schema for cross-version reads

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0119](BE-0119-scenario-schema-versioning.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0119") |
| Topic | Configuration sourcing |
<!-- /BE-METADATA -->

## Introduction

Scenario YAML has no version marker, and its pydantic models reject unknown fields outright, so an
older `bajutsu` reading a newer scenario fails with an opaque validation error instead of a clear
message. This proposal adds an explicit `schema` field to scenarios and a version check with an
actionable error, following the precedent already set by the report manifest's `schemaVersion`.

## Motivation

`bajutsu/scenario/models/_base.py:49` defines the shared base every scenario model inherits:
`model_config = ConfigDict(populate_by_name=True, extra="forbid")`. `extra="forbid"` is the right
choice for catching typos and unsupported fields in scenario authoring (a silently-ignored typo in
a scenario is worse than a loud rejection), but it also means a scenario written for a newer
`bajutsu` — one that adds a new top-level or step-level field — cannot be parsed at all by an older
`bajutsu`. The failure is a generic pydantic `ValidationError` ("Extra inputs are not permitted"),
which does not tell the user that the actual problem is a version mismatch, let alone what to do
about it.

This gap does not matter much today, when a scenario file and the `bajutsu` reading it almost always
come from the same checkout. It matters once scenario reads become routinely cross-version: BE-0063
(the Git config source) fetches a config and its scenario tree from a Git ref that may be pinned to
an older or newer commit than the local `bajutsu` install, making a schema mismatch between the
tool and the scenario tree a normal occurrence rather than an edge case.

The fix is cheap now and gets more expensive to retrofit the longer scenario files exist without a
version marker (every unversioned scenario ever written becomes an ambiguous "version 0" the
migration logic must special-case). The report manifest already solved the same problem for its own
schema — `bajutsu/report/manifest.py:29` defines `SCHEMA_VERSION = 4`, written into every manifest
(`manifest.py:111`), and `bajutsu/report/load.py:8` documents the read-side discipline this proposal
wants for scenarios too: "a missing field (an older `schemaVersion`) falls back to its default, and
an unknown newer field is ignored — so an older run still renders." Severity: Medium — not an active
bug, but a foreseeable one that BE-0063 makes routine, and cheapest to fix before that traffic exists.

## Detailed design

1. **Add a `schema` field to the scenario file's top level.** Add `schema: int` (default `1`) to
   `ScenarioFile` (`bajutsu/scenario/models/scenario.py`), analogous to the manifest's
   `SCHEMA_VERSION`. A scenario file with no `schema` key is treated as schema `1` (every existing
   scenario file in the wild is implicitly version 1 — no migration needed for the current corpus).
2. **Check the version before validating the rest of the document.** In
   `bajutsu/scenario/load.py`'s `load_scenario_file`, read `schema` out of the raw parsed YAML
   *before* handing the full document to `ScenarioFile.model_validate`, and compare it against the
   current `bajutsu` scenario schema constant. A newer `schema` than this `bajutsu` understands
   raises a clear, actionable `ValueError` (e.g. "scenario file uses schema 2, but this bajutsu
   understands up to schema 1 — upgrade bajutsu or pin an older scenario/config version") instead of
   letting the raw field validation fail first with a confusing `extra="forbid"` error.
3. **Keep `extra="forbid"` for same-version documents.** The version check only changes the failure
   *mode* for a version mismatch; it does not loosen `extra="forbid"` for scenarios declaring a
   schema this `bajutsu` supports — a real typo in an in-support scenario must keep failing loudly,
   unchanged from today.
4. **Document the bump discipline.** Add a short note (near `_base.py`'s `_Model` or in
   `docs/`) stating when `schema` must be bumped: any change that removes a previously required
   field's meaning, or that an older `bajutsu` would misinterpret rather than merely reject, bumps
   the constant; a purely additive optional field does not necessarily need a bump if older tooling
   simply lacks the new behavior (to be decided case by case, mirroring how the manifest's
   `SCHEMA_VERSION` is bumped only for load-breaking changes per `report/load.py`'s discipline).

This is scoped to parsing (directive 2's "fail loudly" concern): a version mismatch fails
immediately and clearly instead of running (or silently misbehaving) on stale assumptions. Nothing
here touches `run`'s pass/fail logic or introduces any AI dependency, so directives 1 and 3 are
unaffected.

## Alternatives considered

- **Do nothing until BE-0063 ships and cross-version reads actually cause visible failures.**
  Rejected — by then, an unknown number of scenario files will exist with no `schema` field, making
  the "everything unversioned is implicitly version 1" convention this proposal wants to establish
  harder to justify retroactively (some of those files may already assume newer, undocumented
  behavior). Versioning before the traffic exists costs one field and one check; versioning after
  costs an audit of every scenario file in the wild.
- **Infer the version from the `bajutsu` version that wrote the file (embed a tool version, not a
  schema version).** Rejected — a tool version conflates unrelated releases with schema changes
  (many releases touch no scenario field at all) and forces every reader to maintain a
  version-range-to-schema-shape table; an explicit integer `schema` field, incremented only on
  schema-relevant changes, is the same technique the manifest already uses successfully.
- **Loosen `extra="forbid"` to `extra="ignore"` so newer fields are silently dropped instead of
  erroring.** Rejected on its own — it would fix cross-version reads at the cost of the loud-typo
  protection `extra="forbid"` exists for (a typo'd field would vanish silently, the opposite of
  "fail loudly"). A version check that fails clearly on real mismatches, while keeping
  `extra="forbid"` for same-version documents, gets both properties.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Add a `schema` field (default `1`) to `ScenarioFile`
- [ ] Check `schema` in `load_scenario_file` before full validation, with a clear upgrade-path error
- [ ] Keep `extra="forbid"` unchanged for same-version documents
- [ ] Document the schema-bump discipline for scenario models

No PR has landed yet.

## References

- `bajutsu/scenario/models/_base.py:49` — `_Model`'s `extra="forbid"` config, shared by every
  scenario model
- `bajutsu/scenario/models/scenario.py` — `ScenarioFile`, where the new `schema` field lands
- `bajutsu/scenario/load.py:9` — `load_scenario_file`, where the version check lands
- `bajutsu/report/manifest.py:29,111` — `SCHEMA_VERSION`, the existing precedent for a versioned,
  evolvable schema
- `bajutsu/report/load.py:8` — the read-side compatibility discipline ("a missing field falls back
  to its default, an unknown newer field is ignored") this proposal mirrors for scenarios
- Related: BE-0063 (git config source — makes cross-version reads routine), BE-0068 (regenerable
  reports — the manifest `schemaVersion` precedent), BE-0033 (scenario variables/control flow)
- Originates from the 2026-07-02 codebase-analysis report (design).

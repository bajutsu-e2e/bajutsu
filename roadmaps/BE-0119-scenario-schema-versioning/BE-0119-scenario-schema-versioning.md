**English** · [日本語](BE-0119-scenario-schema-versioning-ja.md)

# BE-0119 — Version the scenario schema for cross-version reads

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0119](BE-0119-scenario-schema-versioning.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0119") |
| Implementing PR | [#663](https://github.com/bajutsu-e2e/bajutsu/pull/663) |
| Topic | Configuration sourcing |
<!-- /BE-METADATA -->

## Introduction

Scenario YAML has no version marker, and its pydantic models reject unknown fields outright. An older `bajutsu` that reads a newer scenario therefore fails with an opaque validation error rather than a clear message. This proposal adds an explicit `schema` field to scenarios and a version check that reports an actionable error. The report manifest's `schemaVersion` already sets the precedent.

## Motivation

`bajutsu/scenario/models/_base.py:49` defines the shared base that every scenario model inherits: `model_config = ConfigDict(populate_by_name=True, extra="forbid")`. The `extra="forbid"` setting is the right choice for catching typos and unsupported fields while authoring a scenario. A typo that is ignored without notice is worse than a loud rejection.

The same setting has a cost. A newer `bajutsu` may add a top-level or step-level field. An older `bajutsu` cannot parse a scenario that uses it. The failure is a generic pydantic `ValidationError` ("Extra inputs are not permitted"). The error never says that the problem is a version mismatch. It says nothing about what to do next.

Today the gap matters little, because a scenario file and the `bajutsu` that reads it almost always come from the same checkout. It matters once scenario reads routinely cross versions. BE-0063 (the Git config source) fetches a config and its scenario tree from a Git ref. The ref can sit at an older or newer commit than the local `bajutsu` install. A schema mismatch between the tool and the scenario tree then becomes a normal occurrence, not an edge case.

The fix is cheap now, and it grows more expensive to retrofit the longer scenario files exist without a version marker. Every unversioned scenario ever written becomes an ambiguous "version 0" that the migration logic must special-case.

The report manifest already solved the same problem for its own schema. `bajutsu/report/manifest.py:29` defines `SCHEMA_VERSION = 4`, and `manifest.py:111` writes it into every manifest. `bajutsu/report/load.py:8` documents the read-side discipline that this proposal wants for scenarios too: "a missing field (an older `schemaVersion`) falls back to its default, and an unknown newer field is ignored — so an older run still renders."

The severity is Medium. The gap is not an active bug, but BE-0063 will make it routine. Fixing it before that traffic exists is cheapest.

## Detailed design

1. **Add a `schema` field to the scenario file's top level.** Add `schema: int` (default `1`) to `ScenarioFile` in `bajutsu/scenario/models/scenario.py`, analogous to the manifest's `SCHEMA_VERSION`. A scenario file with no `schema` key counts as schema `1`. Every existing scenario file in the wild is implicitly version 1, so the current corpus needs no migration.
2. **Check the version before validating the rest of the document.** In `load_scenario_file` in `bajutsu/scenario/load.py`, read `schema` out of the raw parsed YAML. Do this *before* handing the full document to `ScenarioFile.model_validate`. Compare the value against the current `bajutsu` scenario schema constant.
   A `schema` newer than this `bajutsu` understands raises a clear, actionable `ValueError`. An example message: "scenario file uses schema 2, but this bajutsu understands up to schema 1 — upgrade bajutsu or pin an older scenario/config version". Without the check, the raw field validation fails first with a confusing `extra="forbid"` error.
3. **Keep `extra="forbid"` for same-version documents.** The version check changes the failure *mode* for a version mismatch, and nothing else. It does not loosen `extra="forbid"` for scenarios that declare a schema this `bajutsu` supports. A real typo in an in-support scenario must keep failing loudly, as it does today.
4. **Document the bump discipline.** Add a short note, near `_Model` in `_base.py` or in `docs/`, that states when `schema` must be bumped. Two kinds of change bump the constant:
   a change that removes the meaning of a previously required field, and a change that an older `bajutsu` would misinterpret rather than reject.
   A purely additive optional field needs no bump when older tooling lacks the new behavior. That case is decided one change at a time. The manifest's `SCHEMA_VERSION` follows the same rule: it is bumped for load-breaking changes alone, per the read-side discipline `report/load.py` documents.

This change covers parsing, which is the "fail loudly" concern of prime directive 2. A version mismatch fails at once, with a clear message. Otherwise the run would proceed, or misbehave without notice, on stale assumptions. Nothing here touches `run`'s pass/fail logic or adds an AI dependency. Prime directives 1 and 3 stay as they are.

## Alternatives considered

- **Do nothing until BE-0063 ships and cross-version reads cause visible failures.** Rejected. By then, an unknown number of scenario files will have no `schema` field. This proposal wants to establish that everything unversioned is implicitly version 1. That convention is harder to justify after the fact, because some of those files may already assume newer, undocumented behavior.
  Versioning before the traffic exists costs one field and one check. Versioning afterward costs an audit of every scenario file in the wild.
- **Infer the version from the `bajutsu` version that wrote the file (embed a tool version, not a schema version).** Rejected. A tool version conflates unrelated releases with schema changes, since many releases touch no scenario field. It also forces every reader to maintain a table from version range to schema shape. An explicit integer `schema` field, incremented on schema-relevant changes alone, is the technique the manifest already uses.
- **Loosen `extra="forbid"` to `extra="ignore"` so newer fields are dropped instead of erroring.** Rejected on its own. The change would fix cross-version reads, but it would give up the typo protection that `extra="forbid"` exists for. A mistyped field would vanish, the opposite of "fail loudly".
  A version check that fails on real mismatches, together with `extra="forbid"` for same-version documents, provides both properties.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add a `schema` field (default `1`) to `ScenarioFile`
- [x] Check `schema` in `load_scenario_file` before full validation, with a clear upgrade-path error
- [x] Keep `extra="forbid"` unchanged for same-version documents
- [x] Document the schema-bump discipline for scenario models

- [#663](https://github.com/bajutsu-e2e/bajutsu/pull/663) — Add the `SCHEMA_VERSION` constant and aliased `schema` field to `ScenarioFile`, a
  pre-validation version gate in `load_scenario_file` (rejects a too-new or below-1 schema with an
  actionable error, leaves `extra="forbid"` untouched for in-support documents), and the schema-bump
  discipline in `docs/scenarios.md` (+ `docs/ja/`).

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

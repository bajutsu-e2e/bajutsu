**English** · [日本語](BE-0005-idb-companion-version-monitoring-ja.md)

# BE-0005 — idb_companion version monitoring

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0005](BE-0005-idb-companion-version-monitoring.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0005") |
| Implementing PR | [#227](https://github.com/bajutsu-e2e/bajutsu/pull/227) |
| Topic | Platform support |
| Superseded by | [BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md) |
<!-- /BE-METADATA -->

## Introduction

Keep up with idb's own maintenance cadence and compatibility with the latest runtimes. Pin/monitor the version in CI.

## Motivation

idb is the only real backend today (`bajutsu/backends.py`), so the whole on-device path rests on the behaviour of one external dependency: the `idb` Python client and the `idb_companion` binary. Both are maintained on their own cadence, independent of Bajutsu. Two kinds of breakage follow from that. First, a new iOS or Xcode release can ship a runtime that an older `idb_companion` cannot drive, so a green CI run yesterday becomes "no available actuator" today even though nothing in Bajutsu changed. Second, an `idb_companion` upgrade can quietly change the `ui describe-all` JSON schema — the key names (`AXLabel` / `AXValue` / `AXUniqueId`, etc.) that `parse_describe_all` depends on — and our element normalization silently degrades.

DESIGN §11 already lists "idb's own maintenance cadence and latest-runtime compatibility" as an open risk, and `docs/drivers.md` notes that the describe-all key names are validated on-device against a specific fb-idb version and should be re-checked when idb changes. Today that re-check is a manual, easy-to-forget step. The point of this proposal is to make the version a tracked, observable quantity rather than whatever happens to be installed on a given machine, so a compatibility break is caught by CI on the day it appears rather than discovered as a confusing run failure later.

## Detailed design

The version idb is run against becomes an explicit, recorded input to a run rather than an ambient fact.

- **Record the version in the manifest.** Each run captures the `idb_companion` version (and the `idb` client version) and writes it into `manifest.json` alongside the existing run metadata, so any artifact set states exactly which idb produced it. This is provenance, not a gate — it never affects pass/fail, keeping the Tier-2 run/CI gate deterministic and LLM-free.
- **Declare an expected version per environment.** A known-good version range lives in config so a team can pin what it validates against. Because this is environment-level, not app-level, it sits next to the other backend settings rather than under `apps.<name>` — the pin is the same regardless of which app a scenario targets, preserving the app-agnostic rule. `doctor` reads it and reports the installed version against the expected one, so the existing pre-flight surface gains a clear "idb_companion 1.2.3 installed, expected >= 1.2.x" line instead of a downstream failure.
- **A dedicated monitoring job in CI.** A scheduled job (separate from the per-PR gate, which must stay fast and Simulator-free) runs the on-device smoke path against the latest available `idb_companion` and the current Simulator runtimes, and fails loudly if the schema or behaviour drifts. This is the automated form of the manual re-check `docs/drivers.md` describes: it surfaces a needed bump as a CI signal, on a cadence we control, rather than as an ad-hoc discovery.
- **Schema assertions, not just a version string.** The smoke path asserts that `describe-all` still yields the fields the `Element` normalization needs, so an upgrade that keeps the version-compat story but reshapes the JSON is still caught. This keeps the guarantee about behaviour, not just a number.

Nothing here introduces fixed sleeps or non-deterministic selection: the monitoring job reuses the existing condition-waited smoke scenarios, and the version pin is a static comparison.

## Alternatives considered

- **Pin idb to one exact version and never move.** Reproducible, but it freezes Bajutsu behind the iOS/Xcode release train: a new Simulator runtime that only a newer `idb_companion` can drive would be unreachable. We want to *track* the cadence, not opt out of it, so a monitored range is preferred over a hard freeze.
- **Rely on the per-PR e2e workflow alone.** The existing on-device e2e run already exercises idb, but it runs on whatever `idb_companion` the runner has and only when a PR touches that path. A drift caused purely by an upstream idb or runtime release would go unnoticed until someone happened to open such a PR. A scheduled monitoring job decouples the check from PR activity.
- **Detect schema breakage at run time and adapt.** Tolerating multiple describe-all schemas in `parse_describe_all` would hide the problem rather than surface it, and adds branching to the determinism-critical normalization path. Catching the change in CI and updating the parser deliberately keeps that code single-purpose and reviewed.

## Progress

- [x] Shipped — see the *Implementing PR* above.
- [x] Superseded by [BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md): idb was retired, so the `idb_companion` version monitor it added (the `idb_version` module, the manifest `idb` provenance block, and the `defaults.idbVersion` pin) was removed. Nothing produces those versions once idb is gone.

## References

[DESIGN §11](../../DESIGN.md)

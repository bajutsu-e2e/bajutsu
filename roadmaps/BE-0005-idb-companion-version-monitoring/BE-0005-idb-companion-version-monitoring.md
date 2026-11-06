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

This item keeps Bajutsu in step with idb's own maintenance cadence and with the latest runtimes. CI pins and monitors the idb version.

## Motivation

idb is the only real backend today (`bajutsu/backends.py`). The whole on-device path therefore rests on one external dependency: the `idb` Python client and the `idb_companion` binary. Their maintainers publish releases on their own cadence, independent of Bajutsu. Two kinds of breakage follow.

- **A runtime the companion cannot drive.** A new iOS or Xcode release can ship a runtime that an older `idb_companion` cannot drive. A CI run that was green yesterday then reports "no available actuator" today, although nothing in Bajutsu changed.
- **A changed describe-all schema.** An `idb_companion` upgrade can quietly change the `ui describe-all` JSON schema. `parse_describe_all` depends on its key names (`AXLabel`, `AXValue`, `AXUniqueId`, and others). When they change, our element normalization silently degrades.

DESIGN §11 already lists "idb's own maintenance cadence and latest-runtime compatibility" as an open risk. `docs/drivers.md` notes that the describe-all key names were validated on a device against one specific fb-idb version, and that they should be re-checked when idb changes. Today that re-check is a manual step, and it is easy to forget.

This proposal turns the version into a tracked, observable quantity, instead of whatever happens to be installed on a given machine. CI then catches a compatibility break on the day it appears. Without the proposal, the break surfaces later as a confusing run failure.

## Detailed design

The idb version becomes an explicit, recorded input to a run, not an ambient fact.

- **Record the version in the manifest.** Each run captures the `idb_companion` version and the `idb` client version. It writes both into `manifest.json`, next to the existing run metadata, so any artifact set states which idb produced it. The version is provenance, not a gate. It never affects pass/fail, so the Tier-2 run/CI gate stays deterministic and free of LLMs.
- **Declare an expected version per environment.** A known-good version range lives in config, so a team can pin the version it validates against. The range is environment-level, not app-level. It therefore sits next to the other backend settings, not under `apps.<name>`. The pin is the same whichever app a scenario targets, which preserves the app-agnostic rule.

  `doctor` reads the range and compares the installed version with it. The existing pre-flight surface then gains a clear line such as "idb_companion 1.2.3 installed, expected >= 1.2.x", in place of a downstream failure.
- **A dedicated monitoring job in CI.** A scheduled job runs the on-device smoke path against the latest available `idb_companion` and the current Simulator runtimes. The job fails loudly if the schema or the behavior drifts. It is separate from the per-PR gate, which must stay fast and Simulator-free.

  The job automates the manual re-check that `docs/drivers.md` describes. A needed version bump surfaces as a CI signal, on a cadence we control, instead of as an ad-hoc discovery.
- **Schema assertions, not just a version string.** The smoke path asserts that `describe-all` still yields the fields that the `Element` normalization needs. An upgrade that keeps version compatibility but reshapes the JSON is therefore still caught. The guarantee covers behavior, not merely a number.

Nothing here introduces fixed sleeps or non-deterministic selection. The monitoring job reuses the existing condition-waited smoke scenarios, and the version pin is a static comparison.

## Alternatives considered

- **Pin idb to one exact version and never move.** A single pinned version is reproducible, but it freezes Bajutsu behind the iOS and Xcode release train. A new Simulator runtime that only a newer `idb_companion` can drive would be unreachable. We want to *track* the cadence, not opt out of it. A monitored range is therefore preferred over a hard freeze.
- **Rely on the per-PR e2e workflow alone.** The existing on-device e2e run already exercises idb. It runs on whatever `idb_companion` the runner has, and only when a PR touches that path. A drift caused purely by an upstream idb or runtime release would go unnoticed until someone happened to open such a PR. A scheduled monitoring job decouples the check from PR activity.
- **Detect schema breakage at run time and adapt.** Tolerating several describe-all schemas in `parse_describe_all` would hide the problem instead of surfacing it. It would also add branching to the normalization path, which is critical for determinism. Catching the change in CI and updating the parser deliberately keeps that code single-purpose and reviewed.

## Progress

- [x] Shipped — see the *Implementing PR* above.
- [x] Superseded by [BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend.md): idb was retired, so the `idb_companion` version monitor that this item added was removed. The removed parts are the `idb_version` module, the manifest `idb` provenance block, and the `defaults.idbVersion` pin. Nothing produces those versions once idb is gone.

## References

[DESIGN §11](../../DESIGN.md)

**English** · [日本語](BE-0126-per-platform-effective-config-ja.md)

# BE-0126 — Split Effective into per-platform configs

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0126](BE-0126-per-platform-effective-config.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0126") |
| Implementing PR | [#666](https://github.com/bajutsu-e2e/bajutsu/pull/666) |
| Topic | Driver & backend architecture |
| Related | [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md), [BE-0042](../BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md), [BE-0057](../BE-0057-rename-apps-to-targets/BE-0057-rename-apps-to-targets.md), [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md) |
<!-- /BE-METADATA -->

## Introduction

`bajutsu/config.py` resolves each target's YAML into one frozen dataclass, `Effective`
(`config.py:420`). The runner and every backend read `Effective` as the single source of truth for
that target's settings. Today `Effective` carries every field any platform might need, regardless of
which platform the target actually runs on. This proposal splits `Effective` into a small common core
plus a per-platform sub-config, so a target exposes only the knobs that its own platform uses.

## Motivation

`Effective` has grown to 35 fields (`config.py:420-487`) as iOS, and then web, were added. It unions
all of them onto one type. Concretely:

- `browser` (`config.py:471`, default `chromium`) and `headless` (`config.py:468`, default `True`) are
  Playwright (web) knobs. They still exist, readable and settable, on an `Effective` instance for an
  iOS target.
- `xcuitest` (`config.py:478`) and `idb_version` (`config.py:481`) are iOS-only. They are equally
  present on a web target's `Effective`.
- `package` (`config.py:464`) is reserved for the not-yet-built Android backend ([BE-0007]). It is
  currently always `""` for every iOS and web target.

Nothing in the type system stops a caller from reading `eff.browser` on an iOS run or `eff.xcuitest`
on a web run. The only guard is runtime discipline at call sites such as `environment.py:266`
(`if not eff.base_url: raise ...`) and `environment.py:437` (`xcfg = eff.xcuitest`). Those sites check
a value before using it, and the type system does not rule the field out.

This is a **medium-severity** correctness and maintainability risk. It does not fail a run today. It
does erode the "app-agnostic, platform is a backend" boundary (prime directive 3) at the type level,
and every new platform-specific field further dilutes what "a valid `Effective`" means.

Android ([BE-0007]) is the forcing function. Adding `adb`-specific fields (device selection,
`am`/`pm` invocation knobs, and so on) on top of the existing union would push the field count well
past 35. Every target's config would gain a fourth axis of always-irrelevant fields. The dataclass
would then get harder to read, review, and keep correct as each new platform lands.

## Detailed design

The fix is a **discriminated union** keyed by the existing `platform` field (`config.py:462`, already
`ios | android | web`, already validated by `_check_platform` at `config.py:122`). The work breaks
down MECE by field-ownership group:

1. **Common core.** Keep on `Effective` only the fields that every platform genuinely shares:
   `target`, `backend`, `device`, `locale`, `launch_env`, `launch_args`, `id_namespaces`,
   `reserved_namespaces`, `mock_server`, `setup`, `capture`, `redact`, `secrets`, `ai`, `mailbox`,
   `scenarios`, `baselines`, `schemas`, `goldens`, `launch_server`, `ready_when`,
   `doctor_ok_coverage`, `doctor_fail_coverage`, `notify`, and the `platform` discriminator itself.
   The list is not final. `device` and `locale` may turn out to be narrower in practice, so the
   implementer re-checks each field's actual cross-platform usage against current call sites before
   assigning it.
2. **iOS sub-config.** Move `bundle_id`, `deeplink_scheme`, `app_path`, `build`, `xcuitest`, and
   `idb_version` into an `IosConfig` (or similar) attached to `Effective` only when
   `platform == "ios"`.
3. **Web sub-config.** Move `base_url`, `headless`, and `browser` into a `WebConfig` attached only
   when `platform == "web"`.
4. **Android placeholder.** Move `package` into an `AndroidConfig` attached only when
   `platform == "android"`. [BE-0007] can then extend it without touching the other two configs.
5. **`resolve()` and `rebased()` updates.** `resolve()` is the `Config` → `Effective` builder.
   `Effective.rebased()` (`config.py:489`) rebases path fields such as `xcuitest.testRunner` for a
   Git-sourced config (BE-0063). Both need updating to build and rebase through the new sub-configs
   instead of flat fields.
6. **Call-site migration.** Every read of a moved field (for example `environment.py:266-273` and
   `environment.py:437`) changes from `eff.base_url` to something like `eff.web.base_url`, reachable
   only when `eff.platform == "web"`. Today's runtime `if not eff.base_url` guard thus becomes
   something that mypy strict can check statically, such as a `match eff.platform` or `isinstance`
   pattern against the sub-config. No field is left present but meaningless on the wrong platform.

The change stays at the config level. The `targets.<name>` YAML shape, the runner, and the drivers
stay unchanged (prime directive 3, app-agnostic). The attribute name that a caller uses to reach a
platform-specific field does change (for example `eff.browser` → `eff.web.browser`). Every current
call site enumerated above therefore needs a matching update in the same change. Under mypy strict,
each one becomes a compile-time check instead of a runtime surprise.

## Alternatives considered

- **Do nothing until Android lands.** This defers the pain and compounds it. A third platform's
  fields would land on the same flat dataclass, and the eventual split would have three platforms'
  call sites to migrate instead of two. Doing the split now, with only iOS and web, is the smaller
  change.
- **`Optional` fields with a naming convention (for example an `ios_`/`web_` prefix) instead of a
  real split.** Everything stays on one flat type, so the diff is smaller. The measure is cosmetic,
  though. Nothing stops `eff.web_browser` from being read on an iOS target, and mypy strict cannot
  narrow on a naming convention the way it can on a discriminated union.
- **One sub-config field per platform, all present unconditionally on `Effective`**
  (`ios: IosConfig | None`, `web: WebConfig | None`, and `android: AndroidConfig | None` together).
  This is marginally safer than today, because a `None` check is required before use. A caller can
  still branch on the wrong platform's optional config and get a *type-correct* `None`, instead of a
  clear "this target isn't web" error. Tying the sub-config's presence to the `platform`
  discriminator turns an accidental cross-platform read into a type error, where the alternative
  leaves a `None` to check defensively.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Common core: trim `Effective` to genuinely shared fields.
- [x] iOS sub-config (`IosConfig`): `bundle_id`, `deeplink_scheme`, `app_path`, `build`, `xcuitest`, `idb_version`.
- [x] Web sub-config (`WebConfig`): `base_url`, `headless`, `browser`.
- [x] Android placeholder sub-config (`AndroidConfig`): `package`.
- [x] Update `resolve()` and `Effective.rebased()` to build/rebase through the sub-configs.
- [x] Migrate all call sites (`platform_lifecycle.py`, the CLI commands, serve, runner) to the new per-platform accessors.

- Reshaped `Effective` into a common core plus a `platform_config: IosConfig | WebConfig | AndroidConfig`
  discriminated union, with a derived `platform` property; added `require_ios` / `require_web` narrowing
  helpers for platform-committed call sites and `isinstance` narrowing elsewhere, so reading a platform's
  knobs on the wrong platform is a `mypy` error. Migrated every call site and its tests; `make check` green.

## References

- `bajutsu/config.py:420-487` — the `Effective` dataclass and its 35 fields.
- `bajutsu/config.py:462,471,468,478,481,464` — the `platform`, `browser`, `headless`, `xcuitest`,
  `idb_version`, and `package` field declarations.
- `bajutsu/config.py:489-529` — `Effective.rebased()`, which rebases path fields including
  `xcuitest.testRunner`.
- `bajutsu/config.py:122-136,557-587` — `_check_platform` and `_effective_platform`, the existing
  `platform` discriminator and its derivation from `backend`.
- `bajutsu/environment.py:266-273,437` — call sites that guard a platform-specific field at runtime
  instead of the type system ruling it out.
- Related roadmap items: [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md)
  (cross-platform abstractions), [BE-0042](../BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md)
  (platform backend registry), [BE-0057](../BE-0057-rename-apps-to-targets/BE-0057-rename-apps-to-targets.md)
  (rename `apps` → `targets`), [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)
  (Android backend).
- Originates from the 2026-07-02 codebase-analysis report (design).

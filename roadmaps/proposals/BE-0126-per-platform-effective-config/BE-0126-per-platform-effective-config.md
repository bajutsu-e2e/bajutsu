**English** · [日本語](BE-0126-per-platform-effective-config-ja.md)

# BE-0126 — Split Effective into per-platform configs

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0126](BE-0126-per-platform-effective-config.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Platform expansion (Android / Web / Flutter) |
| Related | [BE-0009](../../implemented/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md), [BE-0042](../../implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md), [BE-0057](../../implemented/BE-0057-rename-apps-to-targets/BE-0057-rename-apps-to-targets.md), [BE-0007](../../proposals/BE-0007-android-backend/BE-0007-android-backend.md) |
<!-- /BE-METADATA -->

## Introduction

`bajutsu/config.py` resolves each target's YAML into one frozen dataclass, `Effective`
(`config.py:420`), which the runner and every backend read as the single source of truth for that
target's settings. Today `Effective` carries every field any platform might need, regardless of
which platform the target actually runs on. This proposal splits `Effective` into a small common
core plus a per-platform sub-config, so a target only ever exposes the knobs its own platform uses.

## Motivation

`Effective` has grown to 35 fields (`config.py:420-487`) as iOS, then web, were added, and it unions
all of them onto one type. Concretely:

- `browser` (`config.py:471`, default `chromium`) and `headless` (`config.py:468`, default `True`) are Playwright (web) knobs —
  but they exist, readable and settable, on an `Effective` instance for an iOS
  target.
- `xcuitest` (`config.py:478`) and `idb_version` (`config.py:481`) are iOS-only, yet are equally
  present on a web target's `Effective`.
- `package` (`config.py:464`) is reserved for the not-yet-built Android backend ([BE-0007]) and is
  currently always `""` for every iOS and web target.

Nothing in the type system stops a caller from reading `eff.browser` on an iOS run or `eff.xcuitest`
on a web run; the only guard is runtime discipline in call sites such as `environment.py:266`
(`if not eff.base_url: raise ...`) and `environment.py:437` (`xcfg = eff.xcuitest`), which check a
value before using it rather than the type system ruling the field out. This is a **medium-severity**
correctness/maintainability risk: it doesn't fail a run today, but it erodes the "app-agnostic,
platform is a backend" boundary (prime directive 3) at the type level, and every new platform-specific
field further dilutes what "a valid `Effective`" means. Android ([BE-0007]) is the forcing function:
adding `adb`-specific fields (device selection, `am`/`pm` invocation knobs, etc.) on top of the
existing union would push the field count well past 35 and add a fourth axis of always-irrelevant
fields to every target's config, making the dataclass harder to read, review, and keep correct as
each new platform lands.

## Detailed design

The fix is a **discriminated union** keyed by the existing `platform` field (`config.py:462`,
already `ios | android | web`, already validated by `_check_platform` at `config.py:122`). The work
breaks down MECE by field-ownership group:

1. **Common core.** Keep on `Effective` only the fields every platform genuinely shares: `target`,
   `backend`, `device`, `locale`, `launch_env`, `launch_args`, `id_namespaces`,
   `reserved_namespaces`, `mock_server`, `setup`, `capture`, `redact`, `secrets`, `ai`, `mailbox`,
   `scenarios`, `baselines`, `schemas`, `goldens`, `launch_server`, `ready_when`,
   `doctor_ok_coverage`, `doctor_fail_coverage`, `notify`, and the `platform` discriminator itself.
   (`device`/`locale` may turn out to be narrower in practice; the implementer re-checks each
   field's actual cross-platform usage against current call sites before assigning it, rather than
   taking this list as final.)
2. **iOS sub-config.** Move `bundle_id`, `deeplink_scheme`, `app_path`, `build`, `xcuitest`,
   `idb_version` into an `IosConfig` (or similar) attached to `Effective` only when
   `platform == "ios"`.
3. **Web sub-config.** Move `base_url`, `headless`, `browser` into a `WebConfig` attached only when
   `platform == "web"`.
4. **Android placeholder.** Move `package` into an `AndroidConfig` attached only when
   `platform == "android"`, ready for [BE-0007] to extend without touching the other two configs.
5. **`resolve()` and `rebased()` updates.** `resolve()` (the `Config` → `Effective` builder) and
   `Effective.rebased()` (`config.py:489`, which rebases path fields such as `xcuitest.testRunner`
   for a Git-sourced config, BE-0063) both need updating to build and rebase through the new
   sub-config rather than flat fields.
6. **Call-site migration.** Every read of a moved field (e.g. `environment.py:266-273`,
   `environment.py:437`) changes from `eff.base_url` to something like `eff.web.base_url`, reachable
   only when `eff.platform == "web"` — turning today's runtime `if not eff.base_url` guard into
   something mypy strict can check statically (e.g. a `match eff.platform` / `isinstance` pattern
   against the sub-config), instead of an always-present-but-sometimes-meaningless field.

This is a config-level change only: `targets.<name>` YAML shape, the runner, and the drivers stay
unchanged (prime directive 3, app-agnostic). The attribute name a caller uses to reach a
platform-specific field changes (e.g. `eff.browser` → `eff.web.browser`), so every current call site
enumerated above needs a matching update in the same change; mypy strict turns each one into a
compile-time check rather than a runtime surprise.

## Alternatives considered

- **Do nothing until Android lands.** Defers the pain but compounds it — a third platform's fields
  would land on the same flat dataclass, and the eventual split would have three platforms' call
  sites to migrate instead of two. Doing it now, with only iOS and web, is the smaller change.
- **`Optional` fields with a naming convention (e.g. an `ios_`/`web_` prefix) instead of a real
  split.** Keeps everything on one flat type, so it's a smaller diff, but it's cosmetic: nothing
  stops `eff.web_browser` from being read on an iOS target, and mypy strict cannot narrow on a naming
  convention the way it can on a discriminated union.
- **One sub-config field per platform, all present unconditionally on `Effective`**
  (`ios: IosConfig | None`, `web: WebConfig | None`, `android: AndroidConfig | None` together).
  Marginally safer than today (a `None` check is required before use), but still lets a caller
  branch on the wrong platform's optional config and get a *type-correct* `None` instead of a clear
  "this target isn't web" error; tying the sub-config's presence to the `platform` discriminator
  makes an accidental cross-platform read a type error rather than a `None` to defensively check.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Common core: trim `Effective` to genuinely shared fields.
- [ ] iOS sub-config (`IosConfig`): `bundle_id`, `deeplink_scheme`, `app_path`, `build`, `xcuitest`, `idb_version`.
- [ ] Web sub-config (`WebConfig`): `base_url`, `headless`, `browser`.
- [ ] Android placeholder sub-config (`AndroidConfig`): `package`.
- [ ] Update `resolve()` and `Effective.rebased()` to build/rebase through the sub-configs.
- [ ] Migrate all call sites (e.g. `environment.py`) to the new per-platform accessors.

No PR has landed yet.

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
- Related roadmap items: [BE-0009](../../implemented/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md)
  (cross-platform abstractions), [BE-0042](../../implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md)
  (platform backend registry), [BE-0057](../../implemented/BE-0057-rename-apps-to-targets/BE-0057-rename-apps-to-targets.md)
  (rename `apps` → `targets`), [BE-0007](../../proposals/BE-0007-android-backend/BE-0007-android-backend.md)
  (Android backend).
- Originates from the 2026-07-02 codebase-analysis report (design).

**English** · [日本語](BE-XXXX-web-device-mode-emulation-ja.md)

# BE-XXXX — Web device mode (desktop / mobile emulation)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-web-device-mode-emulation.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform expansion (Android / Web / Flutter) |
<!-- /BE-METADATA -->

## Introduction

The web (Playwright) backend always drives a desktop browser: every context is a plain
`browser.new_context(reduced_motion="reduce")` with the default desktop viewport, no touch input,
and `is_mobile` false. A web app under test therefore only ever sees its desktop layout and
desktop input, even when the behaviour a team wants to test is the mobile one. This proposal adds
a target-level **device mode** — desktop or mobile emulation (viewport, `is_mobile`, `has_touch`,
device scale, user agent) — so a web target can be driven as either, using Playwright's built-in
device emulation.

## Motivation

Responsive web apps behave differently on a phone: a different layout, a hamburger menu instead of
a top nav, touch scrolling and touch-only gestures, a mobile user agent. Bajutsu can drive the web
today, but only its desktop face — there is no way to say "test this target as an iPhone". That
leaves the mobile experience, often the primary one, outside the tool's reach on a backend that
otherwise supports it.

It also blocks a correct fix elsewhere. The companion **web-swipe-scroll-fidelity** item makes
`swipe` scroll by branching on the browser's input mode — a wheel scroll for a desktop pointer
context, a touch drag for a touch context. That branch keys off `has_touch`, but with no way to
configure a touch context, only the desktop path is ever exercised. This item supplies the mobile
(touch) mode that lets the touch path actually run, so the two together give web `swipe` the right
behaviour on both desktop and mobile.

Playwright already ships the mechanism: `playwright.devices["iPhone 13"]` (and the rest) are
preset descriptors of viewport, `device_scale_factor`, `is_mobile`, `has_touch`, and `user_agent`,
applied at `new_context`. The work is to surface that as deterministic, app-agnostic config and
thread it through the backend's context creation — not to invent emulation.

A scope boundary worth stating up front: this is emulation in a desktop-class browser
(Chromium / Firefox / WebKit) with a mobile viewport and touch input, the same thing Chrome
DevTools' device toolbar does. It is **not** a real mobile browser on a real device or a device
cloud — that stays out of scope per [DESIGN §1](../../DESIGN.md) and the roadmap's "cloud device
farm" non-goal. For a real mobile operating system, the Android backend (BE-0007) is the path;
this item is specifically about the web target's rendering and input mode.

## Detailed design

Add a **device mode** to a web target's configuration and apply it when the Playwright backend
creates a browser context.

**Configuration.** A web target gains an optional device setting, resolved through the existing
config layering (`bajutsu/config.py`). The natural shape follows Playwright's own model:

- a **named device preset** (e.g. `device: "iPhone 13"`), resolved against `playwright.devices`,
  which expands to viewport / `device_scale_factor` / `is_mobile` / `has_touch` / `user_agent`; or
- an explicit **desktop / mobile mode** with an optional viewport, for teams that want a plain
  desktop or a generic mobile context without pinning a specific phone.

The default is **desktop** — unchanged from today, so existing web scenarios keep running exactly
as they do now. The exact key name and whether presets and explicit fields share one setting is an
implementation detail to settle in the PR; the constraint is that it is target-level
(app-agnostic) config, not per-scenario or per-step.

**Context creation.** `PlaywrightDriver._new_context` (and the starter's initial context) merge
the resolved device descriptor into the `new_context(**kwargs)` call, alongside the existing
`reduced_motion="reduce"`. A relaunch (BE-0077) and a `reset_context` (the crawl's clean start)
rebuild the same descriptor, so the mode is stable across the browser's whole lifecycle — the same
invariant `reduced_motion` and the selected engine (BE-0076) already hold.

**Interaction with input.** Once a context is created with `has_touch` true, the
web-swipe-scroll-fidelity dispatch routes `swipe` through the CDP (Chrome DevTools Protocol) touch
drag automatically — no
further change is needed there. This item's responsibility ends at producing a correctly
configured context; how gestures are realised in it is that companion item's.

**Capabilities / preflight.** The backend's capability set and preflight (BE-0082) are reviewed so
a mobile context advertises what it can actually do; no change to the run/CI verdict path.

Work breakdown (MECE):

1. **Config surface** — add the device-mode setting to the web target config
   (`bajutsu/config.py`) with desktop as the default, validated (unknown preset → clear error).
2. **Descriptor resolution** — resolve a preset name against `playwright.devices` (or build a
   descriptor from an explicit desktop/mobile + viewport), lazily so importing config never
   imports Playwright.
3. **Context wiring** — apply the descriptor in `_new_context` and the starter's initial context,
   and preserve it across `reset_context` / `relaunch`.
4. **Capability / preflight review** — confirm the advertised capabilities and preflight are
   correct for a mobile context.
5. **Tests** — cover preset resolution, the desktop default (no behaviour change), context kwargs
   carrying the descriptor, and stability across reset / relaunch.
6. **Docs** — document the device mode in `docs/configuration.md` / `docs/drivers.md` (and the ja
   mirrors), including that mobile mode is desktop-browser emulation, not a real device.

This stays within the prime directives:

- **Determinism.** A device descriptor is fixed data applied at context creation; nothing about it
  consults a model, and the run/CI gate is untouched. Emulation is as reproducible as the fixed
  desktop context is today, and pinning viewport and scale to fixed numbers makes the rendered
  layout reproducible run-to-run — more so than a real device whose window size can vary.
- **App-agnostic.** The mode is target-level config, exactly where per-target differences belong
  (`targets.<name>`); the driver and runner are unchanged in shape.

## Alternatives considered

- **Per-scenario or per-step viewport.** Letting a scenario switch device mode mid-run would make
  the same scenario mean different things on different steps and blur the app-agnostic boundary
  (device is a property of how the target is driven, not of an individual action). Rejected:
  device mode is target-level config, matching how the config already models per-target
  differences.
- **A dedicated `setViewport` / `emulate` step.** This turns an environment property into an
  imperative action and invites non-deterministic mid-scenario resizes. Rejected for the same
  reason; if a team needs both faces, they run the scenario under two targets.
- **Only expose named presets (no explicit desktop/mobile + viewport).** Presets cover common
  phones but not a team's specific breakpoint; supporting an explicit viewport too keeps the
  common case one word while not boxing out custom sizes. Kept both.
- **Fold this into web-swipe-scroll-fidelity as one item.** Considered, but the two are separable:
  making `swipe` scroll correctly on the desktop path is valuable on its own and ships without
  emulation, while emulation is a broader capability (layout + input + user agent) with its own
  config and tests. Split so each stays small and independently reviewable.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Config surface (device-mode setting, desktop default, validation)
- [ ] Descriptor resolution (`playwright.devices` preset / explicit desktop-mobile + viewport)
- [ ] Context wiring (`_new_context` + starter; stable across reset / relaunch)
- [ ] Capability / preflight review
- [ ] Tests (preset resolution, desktop default, context kwargs, reset / relaunch stability)
- [ ] Docs (`configuration.md` / `drivers.md`, both languages)

## References

- [Playwright — device emulation](https://playwright.dev/python/docs/emulation)
- [configuration.md](../../docs/configuration.md)
- [drivers.md](../../docs/drivers.md)
- [BE-0076 — Selectable browser engines & cross-browser matrix](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines.md)
- Companion item: **web-swipe-scroll-fidelity** (branches `swipe` on the input mode this item
  configures)

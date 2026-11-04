**English** · [日本語](BE-0228-web-device-mode-emulation-ja.md)

# BE-0228 — Web device mode (desktop / mobile emulation)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0228](BE-0228-web-device-mode-emulation.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0228") |
| Implementing PR | [#943](https://github.com/bajutsu-e2e/bajutsu/pull/943) |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

The web (Playwright) [backend](../../docs/glossary.md#driver-backend-actuator-platform) always drives
a desktop browser. Every context is a plain `browser.new_context(reduced_motion="reduce")` with the
default desktop viewport, no touch input, and `is_mobile` false. A web app under test therefore sees
only its desktop layout and desktop input, even when the team wants to test the mobile behaviour.

This proposal adds a target-level **device mode**: desktop or mobile emulation, covering viewport,
`is_mobile`, `has_touch`, device scale, and user agent. A web
[target](../../docs/glossary.md#target-app-device) can then be driven as either, using Playwright's
built-in device emulation.

## Motivation

Responsive web apps behave differently on a phone. They show a different layout, a hamburger menu
instead of a top nav, touch scrolling and touch-only gestures, and a mobile user agent. Bajutsu can
drive the web today, but only its desktop face. There is no way to say "test this target as an
iPhone". The mobile experience, often the primary one, stays outside the tool's reach on a backend
that otherwise supports it.

The gap also blocks a correct fix elsewhere. The companion **web-swipe-scroll-fidelity** item makes
`swipe` scroll by branching on the browser's input mode: a wheel scroll for a desktop pointer
context, and a touch drag for a touch context. That branch keys off `has_touch`. With no way to
configure a touch context, only the desktop path is ever exercised.

This item supplies the mobile (touch) mode that lets the touch path actually run. Together, the two
items give web `swipe` the right behaviour on both desktop and mobile.

Playwright already ships the mechanism. `playwright.devices["iPhone 13"]` (and the rest) are preset
descriptors of viewport, `device_scale_factor`, `is_mobile`, `has_touch`, and `user_agent`, applied
at `new_context`. The work is to surface that mechanism as deterministic, app-agnostic config and to
thread it through the backend's context creation. It does not invent emulation.

A scope boundary is worth stating up front. This is emulation in a desktop-class browser (Chromium,
Firefox, or WebKit) with a mobile viewport and touch input. Chrome DevTools' device toolbar does the
same thing. It is **not** a real mobile browser on a real device or a device cloud. That stays out of
scope per [DESIGN §1](../../DESIGN.md) and the roadmap's "cloud device farm" non-goal. For a real
mobile operating system, the Android backend (BE-0007) is the path. This item is specifically about
the web target's rendering and input mode.

## Detailed design

Add a **device mode** to a web target's configuration, and apply it when the Playwright backend
creates a browser context.

**Configuration.** A web target gains an optional device setting, resolved through the existing
config layering (`bajutsu/config.py`). The natural shape follows Playwright's own model. It takes
one of two forms:

- a **named device preset** (for example `device: "iPhone 13"`), resolved against
  `playwright.devices`. The preset expands to viewport, `device_scale_factor`, `is_mobile`,
  `has_touch`, and `user_agent`.
- an explicit **desktop / mobile mode** with an optional viewport, for teams that want a plain
  desktop or a generic mobile context without pinning a specific phone.

The default is **desktop**. It is unchanged from today, so existing web scenarios keep running
exactly as they do now.

The exact key name is an implementation detail to settle in the PR. So is whether presets and
explicit fields share one setting. The constraint is that the setting is target-level (app-agnostic)
config, not per-scenario or per-step.

**Context creation.** `PlaywrightDriver._new_context` and the starter's initial context merge the
resolved device descriptor into the `new_context(**kwargs)` call, alongside the existing
`reduced_motion="reduce"`. A relaunch (BE-0077) and a `reset_context` (the crawl's clean start)
rebuild the same descriptor. The mode is therefore stable across the browser's whole lifecycle. That
is the same invariant `reduced_motion` and the selected engine (BE-0076) already hold.

**Interaction with input.** Once a context is created with `has_touch` true, the
web-swipe-scroll-fidelity dispatch routes `swipe` through the Chrome DevTools Protocol (CDP) touch
drag automatically. No further change is needed there. This item's responsibility ends at producing
a correctly configured context. How gestures are realised in that context belongs to the companion
item.

**Capabilities / preflight.** The backend's capability set and preflight (BE-0082) are reviewed, so
that a mobile context advertises what it can actually do. The run/CI verdict path does not change.

Work breakdown (MECE):

1. **Config surface.** Add the device-mode setting to the web target config (`bajutsu/config.py`)
   with desktop as the default. Validate it, so that an unknown preset gives a clear error.
2. **Descriptor resolution.** Resolve a preset name against `playwright.devices`, or build a
   descriptor from an explicit desktop/mobile mode plus viewport. Resolve lazily, so importing
   config never imports Playwright.
3. **Context wiring.** Apply the descriptor in `_new_context` and the starter's initial context.
   Preserve it across `reset_context` and `relaunch`.
4. **Capability / preflight review.** Confirm the advertised capabilities and preflight are correct
   for a mobile context.
5. **Tests.** Cover preset resolution, the desktop default (no behaviour change), context kwargs
   carrying the descriptor, and stability across reset and relaunch.
6. **Docs.** Document the device mode in `docs/configuration.md` and `docs/drivers.md` (and the ja
   mirrors). State that mobile mode is desktop-browser emulation, not a real device.

This stays within the prime directives:

- **Determinism.** A device descriptor is fixed data applied at context creation. Nothing about it
  consults a model, and the run/CI gate is untouched. Emulation is as reproducible as the fixed
  desktop context is today. Pinning viewport and scale to fixed numbers makes the rendered layout
  reproducible run-to-run, more so than a real device whose window size can vary.
- **App-agnostic.** The mode is target-level config, exactly where per-target differences belong
  (`targets.<name>`). The driver and runner are unchanged in shape.

## Alternatives considered

- **Per-scenario or per-step viewport.** Letting a scenario switch device mode mid-run would make the
  same scenario mean different things on different steps. It would also blur the app-agnostic
  boundary, because device is a property of how the target is driven, not of an individual action.
  Rejected: device mode is target-level config, matching how the config already models per-target
  differences.
- **A dedicated `setViewport` / `emulate` step.** This turns an environment property into an
  imperative action and invites non-deterministic mid-scenario resizes. Rejected for the same
  reason. A team that needs both faces runs the scenario under two targets.
- **Only expose named presets (no explicit desktop/mobile + viewport).** Presets cover common phones
  but not a team's specific breakpoint. Supporting an explicit viewport too keeps the common case to
  one word without boxing out custom sizes. Kept both.
- **Fold this into web-swipe-scroll-fidelity as one item.** Considered, but the two are separable.
  Making `swipe` scroll correctly on the desktop path is valuable on its own and ships without
  emulation. Emulation is a broader capability (layout, input, and user agent) with its own config
  and tests. Split so each stays small and independently reviewable.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Config surface (device-mode setting, desktop default, validation)
- [x] Descriptor resolution (`playwright.devices` preset / explicit desktop-mobile + viewport)
- [x] Context wiring (`_new_context` + starter; stable across reset / relaunch)
- [x] Capability / preflight review
- [x] Tests (preset resolution, desktop default, context kwargs, reset / relaunch stability)
- [x] Docs (`configuration.md` / `drivers.md`, both languages)

**Log**

- [#943](https://github.com/bajutsu-e2e/bajutsu/pull/943) — implemented as a target-level `deviceMode` string: `"desktop"` (default, no
  emulation) or a Playwright device preset name (e.g. `"iPhone 13"`). The explicit
  desktop/mobile-plus-viewport structured variant floated in *Detailed design* / *Alternatives* was
  **descoped** — the string form covers the named-preset case that motivated the item, and a custom
  breakpoint can be added later without a config break (a string stays forward-compatible with a
  future object form). `device_mode` lives on the web target only (`TargetConfig` / `WebConfig`),
  alongside `headless` / `browser`, and is distinct from the top-level `device` (the iOS Simulator
  name). The preset is resolved **lazily** in `PlaywrightDriver` (`_device_context_kwargs` against
  `playwright.devices`) so config load never imports Playwright; an unknown preset fails loudly with
  a `ValueError` at driver start, and the resolved descriptor is memoized so `reset_context` /
  `relaunch` rebuild an identical context. Threaded through `make_driver` /
  `WebEnvironment.start` / `doctor.probe_screen`. **Capability / preflight review:** no change — a
  mobile context's touch input is already covered by the existing `multiTouch` capability, and
  device mode is a context-creation config knob, not a per-scenario capability, so nothing new is
  gated on the deterministic run/CI path.

## References

- [Playwright — device emulation](https://playwright.dev/python/docs/emulation)
- [configuration.md](../../docs/configuration.md)
- [drivers.md](../../docs/drivers.md)
- [BE-0076 — Selectable browser engines & cross-browser compatibility matrix (web backend)](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines.md)
- Companion item: **web-swipe-scroll-fidelity** (branches `swipe` on the input mode this item
  configures)

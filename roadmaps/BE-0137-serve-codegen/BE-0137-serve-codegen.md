**English** · [日本語](BE-0137-serve-codegen-ja.md)

# BE-0137 — Generate native test code from the serve Web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0137](BE-0137-serve-codegen.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0137") |
| Implementing PR | [#692](https://github.com/bajutsu-e2e/bajutsu/pull/692), [#696](https://github.com/bajutsu-e2e/bajutsu/pull/696) |
| Topic | Surfacing CLI features in the serve Web UI |
<!-- /BE-METADATA -->

## Introduction

This item surfaces `codegen` in the `serve` Web UI. From a scenario that the user just authored or ran,
the UI generates the equivalent native test (XCUITest in Swift, or Playwright). The user copies or
downloads the result in the browser. The mapping is deterministic and structural, with no AI, no device,
and no verdict.

## Motivation

`codegen` turns a Bajutsu scenario into a native test in the idiom of a destination framework. The
command is `bajutsu codegen --emit xcuitest|playwright`. The code lives in `bajutsu/codegen.py`, and the
Playwright target from [BE-0062](../BE-0062-playwright-codegen/BE-0062-playwright-codegen.md) lives in
`bajutsu/codegen_playwright.py`. It is the bridge for teams whose canonical suite is XCUITest or
Playwright but who want to author with Bajutsu.

Today the terminal is the sole way to get that output. Yet the browser is where the user just authored
the scenario (Record) and confirmed that it passes (Replay). A user who wants "give me this as a
Playwright test" must leave the UI, reconstruct the config, target, and scenario path, and run the
command. Putting codegen one click away from a green scenario closes that gap and makes the bridge
discoverable.

## Detailed design

The feature is Tier-1 and deterministic. The UI shells out to the existing command and does nothing
more.

- **A "Generate code" action** on a scenario, in the editor and the Replay view, with an emit selector
  (XCUITest or Playwright, the destinations `codegen` already supports). The action posts
  `{target, scenario, emit}` to `POST /api/codegen`, which runs codegen and returns the generated source.
- **The result** renders in a read-only code viewer with copy-to-clipboard and download. The filename
  derives from the scenario and the destination, for example `LoginTest.swift` or `login.spec.ts`.
- **Deterministic and AI-free.** codegen is a structural mapping from the scenario model to target
  syntax. Nothing here runs a device or a model, and the feature never touches a verdict.
- **Honest about limits.** The emit options track the codegen targets available for the selected backend
  (XCUITest for iOS, Playwright for web), mirroring `--emit`. The limits on unsupported syntax belong
  to codegen itself
  ([BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md)). The UI
  surfaces codegen's existing diagnostics instead of hiding them.
- **App-agnostic.** The target and scenario path resolve from config (`targets.<name>`).

## Alternatives considered

* **Leave codegen CLI-only.** Rejected. The moment a user most wants native code is right after a
  scenario goes green in the browser. Making the user switch to a terminal loses that moment.
* **Auto-generate code on every passing run.** Rejected as noise. codegen is an occasional export, not a
  per-run artifact. An explicit action keeps runs lean: the report and `--zip` stay the run's artifacts.
* **Write the generated file into the repo from the UI.** Deferred. The first cut returns the code for
  copy and download. Writing into a destination tree touches file-layout decisions that are better made
  explicitly, and the feature can come later.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add the `POST /api/codegen` endpoint (`{target, scenario, emit}`) that runs codegen and returns
      the generated source
- [x] Add the "Generate code" action in the editor and Replay view, with the XCUITest / Playwright
      emit selector
- [x] Render the result in a read-only code viewer with copy-to-clipboard and download (filename
      derived from the scenario and destination)
- [x] Offer only the emit targets valid for the selected backend and surface codegen's existing
      diagnostics

- [#692](https://github.com/bajutsu-e2e/bajutsu/pull/692) — the `POST /api/codegen` endpoint and
  the editor-side "Generate code" action (emit selector, read-only code viewer with copy /
  download), with the emit dispatch shared with the CLI (`bajutsu/codegen_emit.py`).
- [#696](https://github.com/bajutsu-e2e/bajutsu/pull/696) — the same "Generate code" action on the
  Replay view, reusing the endpoint through a shared client-side `makeCodegen` factory; completes the
  item (Status → Implemented).

## References

* `bajutsu/codegen.py`, `bajutsu/codegen_playwright.py`, `bajutsu/cli/commands/codegen.py` — the
  generator this surfaces.
* [BE-0062 — Playwright codegen target](../BE-0062-playwright-codegen/BE-0062-playwright-codegen.md),
  [BE-0026 — Shrink unsupported syntax](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md),
  [BE-0025 — Coordinate swipe generation](../BE-0025-coordinate-swipe-generation/BE-0025-coordinate-swipe-generation.md)
  — the codegen coverage this exposes and its known limits.
* [BE-0011 — Local web UI (`bajutsu serve`)](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md),
  [BE-0072 — Responsive serve Web UI](../BE-0072-responsive-web-ui/BE-0072-responsive-web-ui.md)
  — the UI this extends and the small-screen layout it inherits.
* [codegen.md](../../docs/codegen.md); [CLAUDE.md](../../CLAUDE.md), [DESIGN §2](../../DESIGN.md)
  — codegen is structural and AI-free, so this surface adds no LLM and computes no verdict.

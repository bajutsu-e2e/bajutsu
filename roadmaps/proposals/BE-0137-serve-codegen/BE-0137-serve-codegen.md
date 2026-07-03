**English** · [日本語](BE-0137-serve-codegen-ja.md)

# BE-0137 — Generate native test code from the serve Web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0137](BE-0137-serve-codegen.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Track | [Proposals](../../README.md#proposals) |
| Topic | Surfacing CLI features in the serve Web UI |
<!-- /BE-METADATA -->

## Introduction

Surface `codegen` in the `serve` Web UI: from a scenario you just authored or ran, generate the
equivalent native test (XCUITest in Swift, or Playwright) and copy or download it in the browser.
The mapping is deterministic and structural — no AI, no device, no verdict.

## Motivation

`codegen` turns a Bajutsu scenario into a native test in a destination framework's idiom
(`bajutsu codegen --emit xcuitest|playwright`; `bajutsu/codegen.py` and the Playwright target from
[BE-0062](../../implemented/BE-0062-playwright-codegen/BE-0062-playwright-codegen.md),
`bajutsu/codegen_playwright.py`). It is the bridge for teams whose canonical suite is XCUITest or
Playwright but who want to author with Bajutsu. Yet the only way to get that output is the terminal
— even though the browser is where the scenario was just authored (Record) and confirmed to pass
(Replay). A user who wants "give me this as a Playwright test" must leave the UI, reconstruct the
config / target / scenario path, and run the command. Putting codegen one click away from a green
scenario closes that gap and makes the bridge discoverable.

## Detailed design

Tier-1, deterministic; the UI only shells out to the existing command.

- **A "Generate code" action** on a scenario, in the editor and the Replay view, with an emit
  selector (XCUITest / Playwright — the destinations `codegen` already supports). It posts to
  `POST /api/codegen` (`{target, path, emit}`), which runs codegen and returns the generated source.
- **The result** renders in a read-only code viewer with copy-to-clipboard and download (the
  filename derived from the scenario and destination, e.g. `LoginTest.swift` / `login.spec.ts`).
- **Deterministic and AI-free.** codegen is a structural mapping from the scenario model to target
  syntax; nothing here runs a device or a model, and it never touches a verdict.
- **Honest about limits.** The emit options offered track the codegen targets available for the
  selected backend (XCUITest for iOS, Playwright for web), mirroring `--emit`; unsupported-syntax
  limits are codegen's own
  ([BE-0026](../../implemented/BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md)),
  and the UI surfaces codegen's existing diagnostics rather than hiding them.
- **App-agnostic.** The target and scenario path resolve from config (`targets.<name>`).

## Alternatives considered

* **Leave codegen CLI-only.** Rejected: the moment a user most wants native code is right after a
  scenario goes green in the browser; making them switch to a terminal loses that moment.
* **Auto-generate code on every passing run.** Rejected as noise: codegen is an occasional export,
  not a per-run artifact; an explicit action keeps runs lean (the report and `--zip` stay the run's
  artifacts).
* **Write the generated file into the repo from the UI.** Deferred: the first cut returns the code
  for copy / download; writing into a destination tree touches file-layout decisions better made
  explicitly, and can come later.

## References

* `bajutsu/codegen.py`, `bajutsu/codegen_playwright.py`, `bajutsu/cli/commands/codegen.py` — the
  generator this surfaces.
* [BE-0062 — Playwright codegen target](../../implemented/BE-0062-playwright-codegen/BE-0062-playwright-codegen.md),
  [BE-0026 — Shrink unsupported syntax](../../implemented/BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md),
  [BE-0025 — Coordinate swipe generation](../../implemented/BE-0025-coordinate-swipe-generation/BE-0025-coordinate-swipe-generation.md)
  — the codegen coverage this exposes and its known limits.
* [BE-0011 — Local web UI (`bajutsu serve`)](../../implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md),
  [BE-0072 — Responsive serve Web UI](../../implemented/BE-0072-responsive-web-ui/BE-0072-responsive-web-ui.md)
  — the UI this extends and the small-screen layout it inherits.
* [codegen.md](../../../docs/codegen.md); [CLAUDE.md](../../../CLAUDE.md), [DESIGN §2](../../../DESIGN.md)
  — codegen is structural and AI-free, so this surface adds no LLM and computes no verdict.

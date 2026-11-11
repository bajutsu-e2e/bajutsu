**English** · [日本語](BE-0214-web-only-beginner-tutorial-ja.md)

# BE-0214 — Web-only beginner tutorial (no Xcode/Simulator required)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0214](BE-0214-web-only-beginner-tutorial.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0214") |
| Implementing PR | [#860](https://github.com/bajutsu-e2e/bajutsu/pull/860) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

[`docs/getting-started.md`](../../docs/getting-started.md) calls itself "the tutorial" for Bajutsu.
Its Steps 4 through 6 build the showcase app, run a scenario on a Simulator, and read the report.
They require macOS, Xcode, and the idb backend. Anyone without a Mac completes only half the
tutorial and never reaches a finished `run`.

This proposal adds a tutorial track that completes the same install → scenario → run → report loop.
The track uses the web (Playwright) [backend](../../docs/glossary.md#driver-backend-actuator-platform)
alone, which already runs deterministically on Linux.

## Motivation

Steps 1 through 3 of the current tutorial (install, run the unit suite, read a scenario) are already
platform-agnostic. From Step 4 onward, the walkthrough dead-ends into an iOS-only device flow. Today
only a reader with a Mac can *finish* the tutorial.

[`demos/web`](../../demos/web/README.md) shows that the content already exists. It runs the same
deterministic `run` loop, on the same scenario schema, against a static web app. It does so inside
the same gate that CI uses on Linux. But it is a demo runbook (commands to copy), not a tutorial
narrative. It does not explain each step to a newcomer the way `getting-started.md` does for iOS.

Bajutsu's own headline claim is "a platform is a backend". The deterministic core, the scenario
format, and the reporter stay the same whichever backend actuates the UI. An onboarding path that
finishes only on iOS undercuts that claim in practice, even though the claim holds in the code.

Fixing the path matters most for the readers who are locked out of finishing it today. They use a
non-Mac laptop, a Linux CI runner, or a fresh container with no Simulator available (this
proposal's own drafting environment among them).

## Detailed design

1. **A new tutorial track.** Either a new page (`docs/getting-started-web.md`, + `docs/ja/`
   mirror) or a restructuring of `docs/getting-started.md`. In the restructuring, Steps 1–3 stay
   shared and Steps 4–6 fork into an "iOS track" and a "web track". The exact file layout is an
   implementation-time decision. This proposal fixes the requirement, a Mac-free path to the same
   finish line, and not a particular file shape.
2. **Content.** The content mirrors the existing Step 4–6 shape, with `demos/web` replacing the
   showcase iOS app. It covers serving the static app (`app-serve`) and running its bundled scenario
   through `--backend web` (no Xcode, no idb, no Simulator). It also covers reading the same three
   report formats (`manifest.json` / `junit.xml` / `report.html`) that the iOS track produces.
3. **Cross-links.** `docs/overview.md`'s reading order and `README.md`'s Setup/Demos sections point
   a non-Mac reader at this track. The pointer ideally comes before the Xcode-first track, not
   after, so the platform-agnostic path is not presented as a fallback.
4. **Bilingual pass.** The `docs/ja/` mirror, written as natural Japanese per the
   [`japanese-tech-writing`](../../.claude/skills/japanese-tech-writing/) skill.

This tutorial track should draw its vocabulary from the "glossary and documentation structure
review" proposal (a sibling item drafted alongside this one). It should not phrase `backend` /
`actuator` / `target` differently on its own. Landing that proposal first keeps this one from
introducing yet another ad hoc phrasing of the same terms.

## Alternatives considered

- **Extend `getting-started.md` in place, branching mid-page by platform, instead of a separate
  page.** This is a plausible shape. It is left as an implementation-time choice rather than settled
  here, because this proposal's requirement is the Mac-free finish line, not the file layout that
  delivers it.
- **Point readers at `demos/web/README.md` alone, without a dedicated tutorial track.** Rejected.
  The README is a demo runbook, the commands to run. It is not a tutorial that explains *why* each
  step matters to a newcomer, and that explanation is the part of `getting-started.md` that carries
  the onboarding value.
- **Leave the tutorial iOS-only.** Rejected. It contradicts "a platform is a backend" as a lived
  onboarding experience. It also disadvantages exactly the environments (Linux CI, non-Mac machines)
  that the web backend was built to support.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] 1. Decide the tutorial track's file layout (new page vs. forked `getting-started.md`)
- [x] 2. Write the web-track content (serve → run --backend web → read the report)
- [x] 3. Cross-link from `docs/overview.md` and `README.md`
- [x] 4. Bilingual pass, per the `japanese-tech-writing` skill

### Log

- [#860](https://github.com/bajutsu-e2e/bajutsu/pull/860) — Added the web track as a new self-contained page (`docs/getting-started-web.md` +
  `docs/ja/` mirror): the same install → scenario → run → report loop against the Playwright backend,
  no Mac required. Cross-linked from `docs/overview.md`, `docs/index.md`, `README.md` (Setup / Demos),
  the iOS `getting-started.md`, and `mkdocs.yml` nav — in both languages.

## References

- [`docs/getting-started.md`](../../docs/getting-started.md) — the tutorial this proposal extends
- [`demos/web/README.md`](../../demos/web/README.md) — the runnable content this tutorial packages as a walkthrough
- [`docs/multi-platform.md`](../../docs/multi-platform.md) — the platform-agnostic architecture this tutorial demonstrates
- [BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) — the Playwright backend this track drives
- "glossary and documentation structure review" (sibling proposal drafted alongside this one) — the vocabulary this tutorial should reuse rather than re-phrase

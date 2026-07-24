**English** · [日本語](CONTRIBUTING.ja.md)

# Contributing to Bajutsu

Thanks for your interest in contributing. This page is the entry point for human
contributors. The detailed working agreement that both humans and AI agents follow lives in
[`CLAUDE.md`](CLAUDE.md) and its long form [`docs/ai-development.md`](docs/ai-development.md);
this page orients you and links there rather than repeating the rules, so the page and the
working agreement never drift apart.

New to the project? Read [`README.md`](README.md) for what Bajutsu is, and the
[getting-started tutorial](docs/getting-started/index.md) for a hands-on walkthrough of *running*
Bajutsu. When you are ready to make your first change, the
[contributor workflow tutorial](docs/contributor-workflow-tutorial.md) walks one idea from
`/ideation` to a merged proposal and on through `/implement-be` to a merged PR — start there before
the reference pages below.

## Set up your environment

Bajutsu's logic core is Python **3.13**, managed with [uv](https://docs.astral.sh/uv/).

```bash
uv sync --group dev   # .venv + dependencies + dev tools
make setup            # the above, plus wiring the tracked git hooks (run once on a fresh clone)
```

Only the AI paths (`record`, `run --alert-handling`) need an API key: copy
[`.env.example`](.env.example) to `.env` (gitignored) and set `ANTHROPIC_API_KEY`. The
deterministic gate below needs no secrets and no Simulator.

## The gate (this is the contract)

The Python core needs no Simulator, so the gate is fast and runs anywhere, Linux included. Run
it **before you call a change done and again before you push**:

```bash
make check   # the deterministic gate (full step list in CLAUDE.md)
```

It mirrors [CI](.github/workflows/ci.yml) exactly, so "green locally" predicts "green in CI".
The tracked [pre-push hook](.githooks/pre-push) runs `make check` and refuses a red push. When
you change behavior, change a test with it — the suite is the regression net that protects every
other contributor's work.

On-device E2E (macOS + Simulator) is a separate, heavier path and is **not** part of this gate:
`make -C demos/showcase run-swiftui` (after `make deps`). Do not block core work on it.

## Branches, commits, and pull requests

- **One topic per branch.** Branch off `main` as `<user>/<topic>` (agents use `claude/<topic>`).
  Keep each branch small and single-purpose — small diffs merge fast and rarely conflict.
- **Rebase, don't drift.** Before pushing, `git fetch origin && git rebase origin/main`, then
  re-run `make check`.
- **Commit messages** are imperative and scoped: `feat(run): …`, `fix(record): …`, `docs: …`.
- **Pull request titles and bodies are always in English**, regardless of the language used while
  working, so the history stays readable for everyone.
- **PR titles and bodies follow one shape** — a thorough body, never a one-line restatement of the
  title, plus the `[BE-NNNN]` prefix and a back-link when the PR implements a roadmap item. Full
  convention: [`docs/ai-development.md`](docs/ai-development.md#pull-requests-title-and-body).
- **Answer reviews comment by comment, with the grounds for each resolution** — never a single
  summary reply. Full rule: [`docs/ai-development.md`](docs/ai-development.md#responding-to-pr-review-comments).
- Several sessions work this repo in parallel. For worktrees, the `uv.lock` merge driver, and the
  rest of the parallel-work model, see [`docs/ai-development.md`](docs/ai-development.md).

## Roadmap items (BE IDs)

Larger features are tracked as **Bajutsu Evolution** items under [`roadmaps/`](roadmaps/README.md).
Follow the exact procedure — directory layout, ID allocation, both language files, format — in
[`docs/ai-development.md`](docs/ai-development.md#roadmap-items-be-ids-strict).

## Documentation

Documentation is bilingual: English under [`docs/`](docs/README.md) with a Japanese mirror under
[`docs/ja/`](docs/ja/README.md). **Update both** when you change a documented behavior. Full
guidance: [documentation style](docs/ai-development.md#documentation-style-every-document-both-languages).

## Principles to preserve (do not violate)

These are the design invariants the whole project rests on; the full list is in
[`CLAUDE.md`](CLAUDE.md#prime-directives-do-not-violate).

1. **AI is the author and the failure investigator, never the judge.** A `run` is fully
   deterministic — pass/fail comes only from machine-checkable assertions. Never introduce an LLM
   call into the Tier-2 run/CI gate.
2. **Determinism first.** No fixed `sleep` (condition waits only); an ambiguous selector fails
   immediately rather than tapping whatever matched first.
3. **App-agnostic.** Per-app differences live in config (`targets.<name>`); the tool, drivers, and
   runner stay unchanged across targets.

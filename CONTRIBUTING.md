**English** · [日本語](CONTRIBUTING.ja.md)

# Contributing to Bajutsu

Thanks for your interest in contributing. This page is the entry point for human
contributors. The detailed working agreement that both humans and AI agents follow lives in
[`CLAUDE.md`](CLAUDE.md) and its long form [`docs/ai-development.md`](docs/ai-development.md);
this page orients you and links there rather than repeating the rules, so the two never drift
apart.

New to the project? Read [`README.md`](README.md) for what Bajutsu is, and the
[getting-started tutorial](docs/getting-started.md) for a hands-on walkthrough.

## Set up your environment

Bajutsu's logic core is Python **3.13**, managed with [uv](https://docs.astral.sh/uv/).

```bash
uv sync --group dev   # .venv + dependencies + dev tools
make setup            # the above, plus wiring the tracked git hooks (run once on a fresh clone)
```

Only the AI paths (`record`, `run --dismiss-alerts`) need an API key: copy
[`.env.example`](.env.example) to `.env` (gitignored) and set `ANTHROPIC_API_KEY`. The
deterministic gate below needs no secrets and no Simulator.

## The gate (this is the contract)

The Python core needs no Simulator, so the gate is fast and runs anywhere, Linux included. Run
it **before you call a change done and again before you push**:

```bash
make check   # lock-check + format-check + lint + lint-sh + lint-actions + typecheck + test
```

It mirrors [CI](.github/workflows/ci.yml) exactly, so "green locally" predicts "green in CI".
The tracked [pre-push hook](.githooks/pre-push) runs `make check` and refuses a red push. When
you change behavior, change a test with it — the suite is the regression net that protects every
other contributor's work.

On-device E2E (macOS + Simulator) is a separate, heavier path and is **not** part of this gate:
`make -C demos/features e2e` (after `make deps`). Don't block core work on it.

## Branches, commits, and pull requests

- **One topic per branch.** Branch off `main` as `<user>/<topic>` (agents use `claude/<topic>`).
  Keep each branch small and single-purpose — small diffs merge fast and rarely conflict.
- **Rebase, don't drift.** Before pushing, `git fetch origin && git rebase origin/main`, then
  re-run `make check`.
- **Commit messages** are imperative and scoped: `feat(run): …`, `fix(record): …`, `docs: …`.
- **Pull request titles and bodies are always in English**, regardless of the language used while
  working, so the history stays readable for everyone.
- **Write a thorough PR body — not a one-line restatement of the title.** A reviewer should
  understand the change from the body without reconstructing it from the diff: *what* changed and
  *why* (the motivation/context), a short summary of the key changes (grouped by area when the diff
  is large), how you verified it (e.g. `make check`), and the relevant links (roadmap item, issue)
  and call-outs (trade-offs, follow-ups, anything to look at closely). This holds for humans and AI
  alike. Lead with `## Summary` and close with the `make check` verification (the green numbers),
  adding `What changed` / `Prime-directive compliance` / `Scope` / `Notes` as the change warrants;
  the full title-and-body template is in
  [`docs/ai-development.md`](docs/ai-development.md#pull-requests-title-and-body).
- When a PR implements a roadmap item, **prefix the title with the ID** in brackets — e.g.
  `[BE-0017] feat(mcp): add MCP server` — and add a link to the PR in the item's markdown (both
  language files). PRs with no roadmap item keep the plain scoped title.
- **Answer reviews comment by comment.** When a reviewer (a human, or an AI reviewer like Copilot)
  leaves comments, resolve them all and **reply to each comment individually** — never a single
  summary reply. Each reply states that the comment is addressed *and* the grounds: the concrete
  change that resolves it (cite the commit or file/line), or, when you make no change, the specific
  reason it does not apply. A bare "done" or 👍 is not enough. This is expected of everyone, human
  and AI alike — see [`docs/ai-development.md`](docs/ai-development.md#responding-to-pr-review-comments).
- Several sessions work this repo in parallel. For worktrees, the `uv.lock` merge driver, and the
  rest of the parallel-work model, see [`docs/ai-development.md`](docs/ai-development.md).

## Roadmap items (BE IDs)

Larger features are tracked as **Bajutsu Evolution** items under
[`roadmaps/`](roadmaps/README.md): one directory `roadmaps/<implemented|proposals>/BE-NNNN-<slug>/`
per item (shipped items under `implemented/`, the rest under `proposals/`),
holding an English file and its Japanese version, in Swift-Evolution proposal format. IDs are
permanent and monotonically increasing; the index tables are generated, not hand-edited. Follow
the exact procedure (ID allocation, both language files, `make roadmap-index`) in
[`docs/ai-development.md`](docs/ai-development.md#roadmap-items-be-ids-strict).

## Documentation

Documentation is bilingual: English under [`docs/`](docs/README.md) with a Japanese mirror under
[`docs/ja/`](docs/ja/README.md). **Update both** when you change a documented behavior. Write
natural prose in each language, use established technical terms (no coined vocabulary or forced
translations), and keep each page self-contained. Full guidance:
[documentation style](docs/ai-development.md#documentation-style-every-document-both-languages).

## Principles to preserve (do not violate)

These are the design invariants the whole project rests on; the full list is in
[`CLAUDE.md`](CLAUDE.md#prime-directives-do-not-violate).

1. **AI is the author and the failure investigator, never the judge.** A `run` is fully
   deterministic — pass/fail comes only from machine-checkable assertions. Never introduce an LLM
   call into the Tier-2 run/CI gate.
2. **Determinism first.** No fixed `sleep` (condition waits only); an ambiguous selector fails
   immediately rather than tapping whatever matched first.
3. **App-agnostic.** Per-app differences live in config (`apps.<name>`); the tool, drivers, and
   runner stay unchanged across apps.

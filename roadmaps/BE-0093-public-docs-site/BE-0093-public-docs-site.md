**English** · [日本語](BE-0093-public-docs-site-ja.md)

# BE-0093 — Public project website & documentation portal (GitHub Pages)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0093](BE-0093-public-docs-site.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0093") |
| Implementing PR | [#326](https://github.com/bajutsu-e2e/bajutsu/pull/326) |
| Topic | Contributor workflow |
<!-- /BE-METADATA -->

## Introduction

Bajutsu has no public-facing website. The project's story — what it is, why determinism-first
matters, how to get started — is told only in the repository `README` and the `docs/` tree, which a
reader reaches by browsing GitHub. The one thing that *is* published as a site is the generated API
reference ([BE-0065](../BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference.md)),
and even that is not live yet: GitHub Pages is not enabled on the repository and its deploy job is
gated to a manual run.

This item proposes a **public project website** at the project's GitHub Pages URL: a landing page
that introduces Bajutsu and its principles, plus the existing bilingual `docs/` tree published as a
browseable documentation portal. It **extends the mkdocs-material site that already exists**
([`mkdocs.yml`](../../mkdocs.yml)) rather than introducing a second toolchain, and turns on the
deploy path that is already wired but dormant ([`.github/workflows/docs.yml`](../../.github/workflows/docs.yml)).

## Motivation

- **The material is already written, but locked to GitHub.** `docs/` holds 20-plus pages with a
  full Japanese mirror under `docs/ja/`, plus `README`, [`vision.md`](../../docs/vision.md), and
  `DESIGN.md`. None of it is discoverable outside the repository, and GitHub's Markdown rendering
  gives no site search, no navigation, no language switch, and no canonical landing page a reader
  can be pointed at.
- **A pre-alpha project still needs a front door.** A single page that states the value
  proposition (natural-language-driven E2E, a backend-agnostic driver, determinism-first) and the
  honest status (iOS idb validated on-device, web Playwright landed, Android next) lets someone
  evaluate Bajutsu in a minute without reconstructing it from the source tree.
- **The infrastructure is 90 % built and idle.** A mkdocs-material site, a `--strict` CI build, and
  a least-privilege Pages deploy workflow all exist. What is missing is the content scope (a landing
  page + the `docs/` portal, not just the API reference) and the one-time enablement (turn Pages on,
  flip the deploy guard). Shipping the site is mostly *connecting* existing parts.
- **Prime-directive fit.** A website is docs-only: it puts no LLM into the Tier-2 `run`/CI gate, and
  it does not touch the deterministic core. The `--strict` build is itself in the project's spirit —
  a broken reference or dead link fails the build instead of publishing silently, the same
  "the gate catches breakage" stance the code gate takes.

## Detailed design

### Scope

Extend the existing mkdocs-material site so it serves three things from one build: a **landing
page**, the **bilingual `docs/` portal**, and the **generated API reference** that ships today. No
second static-site toolchain is introduced.

### 1. Landing page

A home page that opens the site, built from material already in `README` / `vision.md` / `DESIGN.md`:

1. **Hero** — name, logo (`assets/icons/logo.png`), a one-line value proposition, and primary
   calls to action (Get started, GitHub).
2. **The core stance** — "AI is the author and the failure investigator, never the judge"; the two
   tiers; determinism-first; "a platform is a backend."
3. **Status** — pre-alpha; iOS (idb) validated end-to-end on a Simulator; web (Playwright) landed;
   Android next.
4. **Quickstart** — install with uv, then `record` → `run`.
5. **Feature highlights** — `record` / `crawl`, the deterministic runner, the evidence subsystem,
   self-healing triage, codegen, MCP, the `serve` web UI.
6. **Backends / platforms** and a **links section** (docs, roadmap, DESIGN, API reference, GitHub).

mkdocs-material renders a hero either through a theme override (`overrides/home.html`, the Material
"splash" template) or a styled `index.md`. The override gives a real landing visual; the choice is a
detail to settle in implementation, not a blocker.

### 2. Publish the `docs/` tree as a portal

Today `docs_dir` is `docs/api`, so only the API reference is built. Widen the site to the whole
`docs/` tree (with the API reference nested under it) and author a `nav` that groups the pages
(getting started, concepts, scenarios, selectors, drivers, evidence, reporting, CI, multi-platform,
self-hosting, the API reference, …).

Two issues this raises, to be designed for explicitly:

- **Bilingual rendering.** The repo's layout is English at `docs/foo.md` with a Japanese mirror at
  `docs/ja/foo.md`. The site should expose a language switch via `mkdocs-static-i18n`, mapping the
  existing layout (default language at the docs root, Japanese under `docs/ja/`) to the plugin's
  structure. The bilingual rule the project already follows (every documented behavior updated in
  both languages) keeps the two sides in step.
- **Links under `--strict`.** Many `docs/` pages link to paths *outside* `docs/` —
  `../roadmaps/…`, `../DESIGN.md`, `../README.md`, `../demos/…`. mkdocs `--strict` treats an
  unresolvable internal link as an error, so these must be handled: rewrite cross-repo links to
  absolute GitHub URLs (e.g. via a small macro/variable for the repo base), or bring the referenced
  trees into the build. This link reconciliation is the **main migration cost** and should be scoped
  before the nav is finalized.

### 3. Turn on publishing

The deploy path exists but is dormant. To go live:

1. **Enable GitHub Pages** — Settings → Pages → source "GitHub Actions". This is a one-time
   repository-admin action and cannot be done from code.
2. **Flip the deploy guards** in [`docs.yml`](../../.github/workflows/docs.yml): drop the
   `workflow_dispatch`-only guard on the artifact upload and the `deploy` job so a push to `main`
   publishes (the comments in the file already mark exactly what to remove).
3. **Widen the workflow's path filter** so a change under `docs/**` (not only `bajutsu/**` /
   `docs/api/**` / `mkdocs.yml`) triggers a rebuild now that the whole tree is the site.
4. **Site metadata & SEO basics** — `site_url`, description, social/OpenGraph card, sitemap; an
   optional custom domain via a `CNAME` file.

### Build & gate boundary

Keep the site build **out of `make check`**, exactly as today — it is a heavier, separate path like
on-device E2E. The CI `--strict` build stays the regression net that catches a broken site before it
publishes. No LLM, no Simulator, Linux-only — consistent with the existing docs workflow.

## Alternatives considered

- **A separate static-site generator (Docusaurus / Astro / plain HTML).** A richer marketing hero is
  easier in a dedicated framework, but it adds a second build, CI step, and deploy path to maintain,
  duplicating what mkdocs-material + `docs.yml` already provide. Rejected in favor of extending the
  existing site (the chosen direction).
- **Landing page only, no docs portal.** Lower effort, but it leaves the already-written bilingual
  `docs/` unpublished — the largest source of value here. Rejected; the portal is the point.
- **Make the site the canonical home for docs (move `docs/` into a site-only structure).** A larger
  restructuring that would break the GitHub-readable source layout and the bilingual mirror
  convention. The chosen path *publishes* `docs/` while keeping the in-repo source readable.
- **Reuse the BE-0015 / BE-0016 hosting work.** Those items host the `serve` *application* (a
  control-plane ⇄ macOS-worker service), which is unrelated to a static project website. Distinct
  concern; no overlap.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- [BE-0065 — Docstring standard & generated API reference](../BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference.md) — the existing mkdocs-material site this extends.
- [`mkdocs.yml`](../../mkdocs.yml) — current site config (API reference only).
- [`.github/workflows/docs.yml`](../../.github/workflows/docs.yml) — the dormant build/deploy workflow.
- [`docs/vision.md`](../../docs/vision.md) · [`README`](../../README.md) — source material for the landing page.
- [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) · [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) — hosting the `serve` *app* (distinct from this static site).
- [mkdocs-static-i18n](https://github.com/ultrabug/mkdocs-static-i18n) — bilingual rendering for the portal.

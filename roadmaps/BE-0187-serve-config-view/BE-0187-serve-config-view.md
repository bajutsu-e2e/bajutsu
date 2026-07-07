**English** · [日本語](BE-0187-serve-config-view-ja.md)

# BE-0187 — View the loaded config in the serve Web UI (raw YAML, structured tree, Git provenance)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0187](BE-0187-serve-config-view.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0187") |
| Implementing PR | [#734](https://github.com/bajutsu-e2e/bajutsu/pull/734) |
| Topic | Configuration sourcing |
| Related | [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md) |
<!-- /BE-METADATA -->

## Introduction

The serve Web UI binds one **active config** that every tab (Record / Replay / Crawl / …) runs
against. Today the header shows only that config's **path** beside "Open config". This proposal adds
a **View** button that opens the bound config for inspection: its content as a collapsible key/value
tree (with a raw-YAML toggle) and, when it came from a Git source, the commit it was materialized
from. It is a read-only affordance — it never edits or re-binds the config.

## Motivation

Once a config is bound you cannot see, from the UI, *what* you are actually running against. The
path is shown, but for a **Git-sourced config** ([BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md))
that path is an opaque content-addressed cache location
(`…/gitsrc/<host>/<owner>/<repo>/<sha>/…`). Two problems follow:

- **You cannot confirm the content.** To check which targets, `baseUrl`, or scenarios the tabs will
  use, you have to leave the UI and open the file on disk (or the repo) yourself.
- **You cannot confirm the commit.** A Git source is bound at a `ref` (a branch, tag, or SHA); the
  ref is resolved to an immutable commit at bind time (BE-0063's determinism anchor). The cache path
  does contain that SHA, but reading a 40-character hash out of a long path is not a usable "which
  commit am I on?" answer, and a branch ref gives no hint at all of what it resolved to.

The information already exists server-side — the bound path, the file's bytes, and (for a Git bind)
the `source_provenance` stamp BE-0063 computes and already returns from the bind — it is simply never
surfaced *in the UI* afterward. Surfacing it would turn "trust that the right thing is bound" into
"see that the right thing is bound", which matters most for the Git path this builds on.

## Detailed design

A read-only serve endpoint plus a viewer in the UI. No `run`/CI path is touched (prime directive 1);
this is a Tier‑1 convenience surface.

- **Persist Git provenance on the bound state.** `ServeState` would gain a `config_provenance` field
  holding BE-0063's `source_provenance` stamp (host / owner / repo / requested ref / resolved SHA)
  when the active config came from a Git source, or `None` for a local file or an uploaded bundle.
  It is set on a runtime Git bind and cleared on a local-file / bundle bind; a Git `--config` at
  startup threads its provenance through into the state too, so a startup-bound Git config also
  reports its commit.
- **A config-content serve operation + endpoint.** A read operation returns the bound config's
  verbatim text, its path, its parsed structure (loaded with the project's restricted YAML loader, so
  an `on:` key stays the string `"on"` rather than being coerced to a boolean, then coerced to
  JSON-safe types), and the provenance (or `None`). It is exposed as `GET /api/config/content` on both
  serve transports (the stdlib handler and the FastAPI app) and returns 404 when no config is bound.
  Because the full config body is a wider disclosure than the path-only `/api/config`, this read is
  gated to the `admin` role (it is ungated only on a local, tokenless deployment).
- **Verbatim about placeholders, not a secret redactor.** The text (and parsed structure) is the file
  as bound: `${secrets.*}` placeholders appear as written and are never resolved, so the view adds no
  disclosure beyond the file already committed to Git or shipped in the bundle. It is *not* a redactor,
  though — a secret written **literally** into a local or uploaded config would be shown as-is. Keeping
  secrets out of the file (as `${secrets.*}` refs) remains the contract; the `admin` gate above bounds
  who can read the body regardless.
- **UI: a "View" button + a viewer modal.** A **View** button beside the config name (revealed only
  once a config is bound) opens a modal showing the provenance line (for a Git source), the path, and
  the config in two switchable views: a **Structured** collapsible key/value tree (nested objects and
  lists are toggles, the first couple of levels open by default, scalars colored by type) and a
  **Raw** toggle showing the verbatim YAML. An unparseable config falls back to the raw view.
- **Docs.** `docs/web-ui.md` and its `docs/ja/web-ui.md` mirror describe the View button, the two
  views, and the provenance line.

## Alternatives considered

- **Show the full YAML inline in the header, no modal.** A config is long, and the header is shared
  with the tab bar; a click-to-open modal keeps the default UI uncluttered.
- **Only reformat the header path (show `owner/repo@ref` instead of the cache path), no content
  view.** This solves the "which commit" half but not the "what content" half; an operator still
  cannot confirm the bound targets/scenarios without leaving the UI. The content view subsumes it.
- **Resolve `${secrets.*}` for display.** Rejected outright — it would turn a read-only inspection
  surface into a secret-disclosure vector. The view is deliberately verbatim.
- **Make it editable (bind an edited config from the viewer).** Out of scope: binding already has its
  own sources (file browser / Git / upload). This item is inspection only.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Persist Git provenance on `ServeState` (set on Git bind, cleared on local/bundle bind, threaded from startup).
- [x] Config-content read operation returning content + path + parsed structure + provenance.
- [x] `GET /api/config/content` on both transports (stdlib handler + FastAPI app).
- [x] UI: View button + viewer modal with Structured / Raw toggle.
- [x] Docs (en + ja) describe the viewer and provenance line.

Log:

- [#734](https://github.com/bajutsu-e2e/bajutsu/pull/734) — shipped the whole item: provenance on
  `ServeState`, the `config_content` operation and `GET /api/config/content` (both transports, gated
  to `admin`), the View button and Structured/Raw viewer modal, and the bilingual docs.

## References

- [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md) — the Git config source whose
  resolved-commit provenance this would surface.
- [`docs/web-ui.md`](../../docs/web-ui.md) — the Web UI guide ("Choosing the active config").

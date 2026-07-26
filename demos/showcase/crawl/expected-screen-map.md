**English** · [日本語](expected-screen-map.ja.md)

# Showcase crawl — the screen map it should produce

> `crawl` ([BE-0038](../../../roadmaps/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md))
> is a **proposal**, not yet implemented. This file is forward-looking *test data*: the graph a
> correct breadth-first crawl of the showcase a11y apps should discover, so the crawl can be
> validated against a known-good map the day it lands. Run it then with:
>
> ```bash
> bajutsu crawl --target showcase-swiftui --config demos/showcase/showcase.config.yaml \
>     --seed showcaseswiftui://permissions
> ```

The showcase is built to be a genuinely branchy crawl target: 5 tabs × navigation pushes ×
4 modal styles. Because every identifier is data-derived and stable (SPEC §5), the id-based
**state fingerprint** (the sorted set of on-screen identifiers, hashed —
[BE-0038](../../../roadmaps/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md))
is stable across runs.

## Nodes (reachable screens)

| Node | Reached from | Fingerprint anchor (representative id) |
|---|---|---|
| Stable (list) | launch / `stable` tab | `stable.title`, `stable.row.*` |
| Horse Detail | Stable row tap / `…://horse/<id>` | `horse.title` |
| Search | `search` tab | `search.field` |
| Search (empty) | Search + no-match query | `search.results-empty` |
| Log | `log` tab | `log.submit` |
| Log → Filter sheet | `log.openFilter` | `log.sheet.title` |
| Log → Gallery cover | `log.openGallery` | `log.cover.title` |
| Log → Delete dialog | `log.openDelete` | `log.dialog.delete` |
| Notices (list) | `notices` tab | `notice.title`, `notice.row.*` |
| Notice Detail | Notices row / `…://notice/<id>` | `notice.detail.title` |
| Permissions | `permissions` tab / `…://permissions` | `perm.title` |

## Edges worth noting

- **Tab switches** are five edges out of every main node (the tab bar is always present).
- **Modals** are reached-and-dismissed cycles off the Log node — a crawl should record the
  open edge and recognize the returned-to state by fingerprint rather than re-exploring it.
- **Permissions** is the one node whose actions raise out-of-process OS alerts (SPEC §7).
  A crawl reaching it should rely on the alert guard (`--alert-handling`) exactly as `run` does;
  the alert is not a crawl-discoverable screen.

## What the no-a11y variant should show instead

Run against `showcase-swiftui-noax` the crawl must fall back to the structural fingerprint
(`(traits, frame-bucket)` — BE-0038) because there are no identifiers. The map should come out
coarser and flagged as low-confidence — the same accessibility-debt signal `doctor` reports,
seen from the exploration side.

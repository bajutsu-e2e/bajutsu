# docs-site: E2E against the live Bajutsu docs

[日本語](README.ja.md)

This directory holds a Bajutsu config that drives the **public docs site**
(<https://bajutsu-e2e.github.io/bajutsu/>) with the **Playwright (web) backend**. Unlike
[`demos/web`](../web/README.md), there is no local app to serve. The target is a live public
URL, so the backend just navigates to it.

## Run

```bash
uv sync --extra web                 # the Playwright python package
uv run playwright install chromium  # the Chromium binary (once)

# whole suite
uv run bajutsu run --target docs --backend web --config demos/docs-site/docs-site.config.yaml
# one scenario
uv run bajutsu run --scenario demos/docs-site/scenarios/smoke.yaml \
  --target docs --backend web --config demos/docs-site/docs-site.config.yaml
```

`run` exits 0 when every scenario passes and 1 on any failure. Each run writes
`runs/<runId>/{manifest.json,junit.xml,report.html}`.

## Scenarios

| File | What it checks |
|---|---|
| [`smoke.yaml`](scenarios/smoke.yaml) | The landing page loads and shows its "Get started" / "GitHub" hero buttons. |
| [`search.yaml`](scenarios/search.yaml) | Typing into the header search box surfaces a matching result link (text entry + async condition wait). |

The site is Material for MkDocs and carries no `data-testid` ids. The scenarios address
elements by visible text (`label` / `labelMatches`) and kind (`traits: [link]` / `[button]`)
instead of by `id`. `exists` tolerates multiple matches (find_all ≥ 1), while the single-element
assertions still require a unique match. The comments in each scenario note why its selector
resolves uniquely.

## Known limitation: cross-page link navigation

A scenario that **clicks a link causing a full-page load** (for example, the "Get started" hero
leading to the getting-started page) currently wedges the web backend. After the coordinate click,
the run loop's condition wait re-queries the DOM while the navigation is still in flight, and
Chromium destroys the execution context (`Execution context was destroyed, most likely because of a
navigation`). `base.wait_until` does not treat that transient fault as "not matched yet", so the
run aborts instead of settling on the new page.

The gap sits in the backend and run loop, not in a scenario. The driver re-queries by polling
`query()` instead of using Playwright's native navigation settling. This suite stays within
same-document interactions (the search scenario), which the current backend drives deterministically.
A roadmap (BE) item is worth filing: have `wait_until` (or the web driver's `wait_for`) swallow a
transient navigation wedge and keep polling to the deadline.

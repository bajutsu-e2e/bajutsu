# docs-site — E2E against the live Bajutsu docs

[日本語](README.ja.md)

A Bajutsu config that drives the **public docs site**
(<https://bajutsu-e2e.github.io/bajutsu/>) with the **Playwright (web) backend**. Unlike
[`demos/web`](../web/README.md), there is no local app to serve — the target is a live public
URL, so the backend just navigates to it.

## Run

```bash
uv sync --extra web                 # the Playwright python package
uv run playwright install chromium  # the Chromium binary (once)

# whole suite
uv run bajutsu run --target docs --backend web --config demos/docs-site/bajutsu.config.yaml
# one scenario
uv run bajutsu run --scenario demos/docs-site/scenarios/smoke.yaml \
  --target docs --backend web --config demos/docs-site/bajutsu.config.yaml
```

`run` exits 0 when every scenario passes, 1 on any failure; each run writes
`runs/<runId>/{manifest.json,junit.xml,report.html}`.

## Scenarios

| File | What it checks |
|---|---|
| [`smoke.yaml`](scenarios/smoke.yaml) | The landing page loads and shows its "Get started" / "GitHub" hero buttons. |
| [`search.yaml`](scenarios/search.yaml) | Typing into the header search box surfaces a matching result link (text entry + async condition wait). |

The site is Material for MkDocs and carries no `data-testid` ids, so scenarios address elements
by visible text (`label` / `labelMatches`) and kind (`traits: [link]` / `[button]`) rather than
`id`. `exists` tolerates multiple matches (find_all ≥ 1); the single-element assertions still
require a unique match, so the comments note why each selector resolves uniquely.

## Known limitation — cross-page link navigation

A scenario that **clicks a link causing a full-page load** (e.g. the "Get started" hero → the
getting-started page) currently wedges the web backend: after the coordinate click, the run
loop's condition wait re-queries the DOM while the navigation is still in flight, and Chromium
destroys the execution context (`Execution context was destroyed, most likely because of a
navigation`). `base.wait_until` does not treat that transient fault as "not matched yet", so the
run aborts instead of settling on the new page.

This is a backend/run-loop gap, not a scenario bug — the driver re-queries by polling
`query()` rather than using Playwright's native navigation settling — so this suite stays within
same-document interactions (the search scenario) that the current backend drives deterministically.
Worth a roadmap (BE) item: have `wait_until` (or the web driver's `wait_for`) swallow a transient
navigation wedge and keep polling to the deadline.

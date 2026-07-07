# Crawl-history fixture (BE-0181)

A committed, deterministic **past crawl run** the serve-UI dogfood points the inner serve's
`--runs` at, so the Crawl tab's **History** list has a real entry to reopen without depending on
host state.

`20260101-000000/screenmap.json` is a hand-authored screen map (two screens, one transition, one
pruned global control) that stops on a budget (`stop_reason: max_screens`) with one screen left in
its `plan` — a non-empty **frontier**. That is what lets
[`scenarios/crawl-history.yaml`](../../scenarios/crawl-history.yaml) assert both:

- **BE-0180** — selecting a past run reopens its map read-only (the *past crawl* badge), and
- **BE-0181** — a run with a remaining frontier offers the *continue exploring* control.

It is named like a real run id (a timestamp) but is fixed data, not generated output — hence it
lives under `fixtures/` (not the git-ignored `runs/`). The dogfood never writes here.

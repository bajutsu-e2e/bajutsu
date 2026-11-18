# Crawl-history fixture (BE-0181)

This directory holds a committed, deterministic **past crawl run**. The serve-UI dogfood points the
inner serve's `--runs` at it, so the Crawl tab's **History** list has a real entry to reopen without
depending on host state.

`20260101-000000/screenmap.json` is a hand-authored screen map with two screens, one transition, and
one pruned global control. It stops on a budget (`stop_reason: max_screens`) with one screen left in
its `plan`, which makes the **frontier** non-empty. That frontier lets
[`scenarios/crawl-history.yaml`](../../scenarios/crawl-history.yaml) assert both:

- **BE-0180**: selecting a past run reopens its map read-only (the *past crawl* badge).
- **BE-0181**: a run with a remaining frontier offers the *continue exploring* control.

The directory is named like a real run id (a timestamp), but it holds fixed data, not generated
output. That is why it lives under `fixtures/` and not under the git-ignored `runs/`. The dogfood
never writes here.

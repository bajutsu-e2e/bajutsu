**English** · [日本語](BE-0029-visual-regression-assertions-ja.md)

# BE-0029 — Visual-regression assertions

* Proposal: [BE-0029](BE-0029-visual-regression-assertions.md)
* Status: **Implemented**
* Implementing PR: [#30](https://github.com/bajutsu-e2e/bajutsu/pull/30), [#34](https://github.com/bajutsu-e2e/bajutsu/pull/34)
* Track: [Accepted](../README.md#accepted)
* Topic: Candidates from competitive research (MagicPod / Autify)
* Origin: Both

## Introduction

A new assertion type that diffs a screenshot against a baseline. Supports exclusion regions and per-device / per-locale baselines. Because it is a deterministic machine check rather than AI, it fits "pass/fail by machine assertions only".

## Motivation

TBD.

## Detailed design

Implemented as a deterministic `visual` assertion kind in the scenario-level `expect:` block.
A captured screenshot is diffed against a stored baseline image; exclude regions mask dynamic
content (clock, status bar) and a `threshold` allows a tolerated diff percentage (default `0` =
exact match). When images differ, a diff image is written alongside the run for inspection.

```yaml
expect:
  - visual:
      baseline: counter-2.png     # relative to the baselines directory
      threshold: 0.1              # allowed diff % (default 0 = exact match)
      exclude:                    # mask dynamic regions
        - { x: 0, y: 0, w: 390, h: 54 }
```

- Schema: `ExcludeRegion` / `VisualMatch` models and the `Assertion.visual` field in
  `bajutsu/scenario.py`.
- Comparison engine: `bajutsu/visual.py` — pixel-level diff via Pillow `ImageChops.difference`,
  exclude-region masking, threshold tolerance, diff-image output. Pillow is an optional dependency
  (`pip install bajutsu[visual]`).
- Evaluation: `VisualContext` (screenshot path, baselines dir, diff output dir) and `_eval_visual`
  in `bajutsu/assertions.py`.
- Orchestration: the screenshot is captured before expect evaluation and the `VisualContext` is
  threaded through `run_scenario` (`bajutsu/orchestrator.py`, `bajutsu/runner.py`).

Because evaluation is a pure machine check with no AI involved, it strengthens the prime
directives rather than straining them.

## Alternatives considered

A Python-level pixel loop was rejected in favor of `ImageChops.difference` for C-level speed.

## References

[PR #30](https://github.com/bajutsu-e2e/bajutsu/pull/30), `bajutsu/visual.py`,
`bajutsu/assertions.py`, `bajutsu/scenario.py`, [DESIGN §6.4](../../../DESIGN.md),
[evidence.md](../../evidence.md)

### Follow-ups (not yet implemented)

- `--update-baselines` CLI flag to save current screenshots as new baselines
- HTML report rendering of the visual diff (baseline / actual / diff side-by-side)
- `baselines_dir` configuration in `bajutsu.config.yaml`
- Per-device / per-locale baseline variants

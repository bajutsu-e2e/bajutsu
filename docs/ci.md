# Continuous integration

Two distinct things, kept separate:

1. **CI for this repo** — guard the tool itself (`.github/workflows/`).
2. **Running bajutsu in *your* app's CI** — a composite action + recipe you reuse.

## This repo's CI

| Workflow | Runner | When | What |
|---|---|---|---|
| [`ci.yml`](../.github/workflows/ci.yml) | Linux | push to `main`, every PR | `ruff` + `mypy` + `pytest` on Python 3.13 (the logic layer needs no Simulator — fast, cheap) |
| [`e2e.yml`](../.github/workflows/e2e.yml) | macOS | manual + PRs touching the app/SDK/runtime | two jobs: **smoke (idb)** builds the sample, boots a Simulator, and runs `smoke.yaml` through the idb backend (driver + simctl + idb); **xcuitest (codegen)** generates a native XCUITest from a scenario (`make -C demos/features ui-test`) and runs it with `xcodebuild` (no bajutsu / idb / AI at test time) |

The dev tools live in the `dev` extra, so the Linux job runs `uv sync --extra dev` then
`uv run --no-sync …` (plain `uv run` would re-sync to the default set and drop them).

## Running bajutsu in your app's CI

> bajutsu is pre-release (unpublished). Until it's on PyPI, vendor it (a submodule or a
> checkout) and run the action from that checkout — the action runs `uv sync` against
> bajutsu's `pyproject.toml`.

bajutsu produces CI-ready output already: a `junit.xml`, a self-contained `report.html`,
a `0` / `1` exit code, and — inside Actions — failure **annotations** + a job **summary**
(see below). On a macOS runner:

1. **Build and install your app** onto a booted Simulator (this varies per app, so it
   stays yours — `xcodebuild` + `xcrun simctl install`).
2. **Run bajutsu** with the [`bajutsu-e2e`](../.github/actions/bajutsu-e2e/action.yml)
   composite action — it installs `idb_companion`, syncs deps, runs an optional `doctor`
   preflight, runs your scenarios, and uploads the run (report + screenshots + video +
   `network.json`) as an artifact.

```yaml
jobs:
  e2e:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4
      - uses: maxim-lobanov/setup-xcode@v1
        with: { xcode-version: latest-stable }
      - uses: astral-sh/setup-uv@v6
        with: { enable-cache: true }

      # --- build + install your app (your build, on a booted Simulator) ---
      - run: xcodebuild -scheme MyApp -destination 'generic/platform=iOS Simulator' -derivedDataPath dd build
      - id: sim
        run: |
          udid=$(xcrun simctl create ci "iPhone 16")
          xcrun simctl boot "$udid"; xcrun simctl bootstatus "$udid" -b
          echo "udid=$udid" >> "$GITHUB_OUTPUT"
      - run: xcrun simctl install "${{ steps.sim.outputs.udid }}" dd/Build/Products/Debug-iphonesimulator/MyApp.app

      # --- run bajutsu ---
      - uses: your-org/bajutsu/.github/actions/bajutsu-e2e@main
        with:
          scenarios: e2e/*.yaml
          app: myapp
          udid: ${{ steps.sim.outputs.udid }}
```

### Failure annotations + job summary

When `GITHUB_ACTIONS` is set, `bajutsu run` emits a `::error::` annotation per failed
scenario (shown inline on the PR) and appends a PASS/FAIL table to `$GITHUB_STEP_SUMMARY`.
No flag needed — it auto-detects the Actions environment.

### Notes

- **JUnit**: `junit.xml` is written next to the report; feed it to a test-reporter action
  (e.g. `dorny/test-reporter`) for an inline test view.
- **Determinism**: use scenario [`mocks`](network.md#deterministic-mocks) to stub the
  network so runs don't depend on a live server.
- **`doctor`**: today it is a convention *score* (non-blocking preflight); a hard
  env/permission runnability gate (idb / idb_companion / Xcode presence) is future work.

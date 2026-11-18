# Android visual baselines (committed)

This folder holds the baseline PNGs for the Android visual regression check
(`../visual_android.yaml`, BE-0208 unit 4).

The iOS VRT demo (`../../visual.yaml`) keeps baselines that are device-specific and **gitignored**
(regenerated locally). These Android baselines are instead **committed**, and the `e2e-visual`
target in `demos/showcase/android/Makefile` reads them in CI.

A pixel baseline is host-specific: the CI x86_64 software renderer (swiftshader) and a local arm64
emulator diverge per pixel. The baseline must be **captured on the CI lane**, not locally:

1. The `Android E2E (emulator)` workflow (`.github/workflows/android-e2e.yml`) runs `e2e-visual` in its
   `visual (adb)` job on the pinned pixel_6 x86_64 emulator. On the first run, before a baseline
   exists, the `visual` check fails with "baseline not found". The workflow uploads the captured
   screenshot in the `android-e2e-visual-run` artifact (`runs/`).
2. Download that artifact and promote the captured screenshot to a baseline here with
   `bajutsu approve --baselines demos/showcase/scenarios/visual/baselines_android <run-dir>`, then
   commit the resulting `stable.png`.
3. Re-run the lane. The comparison now passes.

Regenerate the baseline the same way whenever a deliberate UI change moves pixels.

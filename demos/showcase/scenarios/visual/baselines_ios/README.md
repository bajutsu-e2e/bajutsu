# iOS visual baselines (committed)

Baseline PNGs for the iOS visual regression check (`../visual_ios.yaml`) — the committed pixel-snapshot
twin of the element-tree golden (`../../golden/golden.yaml`) and of the Android VRT
(`baselines_android/`), giving iOS the same committed VRT coverage.

Unlike the multi-screen iOS VRT demo (`../../visual.yaml`), whose per-toolkit baselines are
device-specific and **gitignored** (regenerated locally), this baseline is **committed** and read by
the `e2e-visual` target in `demos/showcase/Makefile`, which the path-gated, non-required
`ios-e2e.yml` workflow (the iOS twin of `android-e2e.yml`'s `e2e-visual`) runs on XCUITest.

A pixel baseline is host-specific: the Simulator's renderer varies by Xcode / device / OS. `stable.png`
here was recorded on the `iPhone 17 · iOS 26.5` Simulator, and `ios-e2e.yml` pins that model
(`device-type: iPhone 17` on the `boot-simulator` action, the iOS twin of the Android lane's
`profile: pixel_6`) so the device geometry stays fixed across runs. If the runtime still drifts
(a different Xcode / OS renderer):

1. `ios-e2e.yml` runs `make -C demos/showcase e2e-visual` and, on a mismatch, uploads the captured
   screenshot in the `ios-e2e-visual-run` artifact (`runs/`).
2. Download that artifact and promote the captured screenshot to a baseline here with
   `bajutsu approve <run-dir> --baselines demos/showcase/scenarios/visual/baselines_ios`, then commit
   the resulting `stable.png`. (Or run `make -C demos/showcase e2e-visual-approve` on that Simulator.)
3. Re-run the monitor; the comparison now passes.

The scenario masks the volatile chrome (the top status bar and the bottom "Liquid Glass" tab bar), so
only the catalog content is compared. Regenerate the same way whenever a deliberate UI change moves pixels.

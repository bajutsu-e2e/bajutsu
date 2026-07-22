**English** · [日本語](BE-XXXX-doctor-provision-real-environment-verification-ja.md)

# BE-XXXX — Real-environment verification for the onboarding gate (doctor, preflight, provision)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-doctor-provision-real-environment-verification.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | doctor / onboarding |
<!-- /BE-METADATA -->

## Introduction

The onboarding gate — `provision.py` (installs backend tooling), `preflight.py`/`requirements.py`
(the runnability gate `doctor` reports), and `simctl.py`'s JSON parsers (`booted_udids`,
`device_catalog`) that back both — is tested entirely through injected fakes: mocked
`subprocess.run` calls asserting the right command string, monkeypatched `shutil.which`, and
hand-typed JSON literals standing in for `xcrun simctl list devices -j`. None of it is ever run for
real in CI. This item wires `bajutsu doctor` and `bajutsu provision` into an existing E2E lane so the
whole onboarding path gets exercised against a genuinely real, and genuinely known-broken,
environment at least once.

## Motivation

Three gaps share one root cause and one fix. `provision.py`'s tests assert that a call like
`("brew", "install", "facebook/fb/idb-companion")` was *built*, never that a real `brew` accepted it
— and every real E2E workflow (`ios-e2e.yml`, `web-e2e.yml`, `android-e2e.yml`) hand-rolls the
equivalent install commands directly rather than calling `bajutsu provision`, so the installer's own
command-construction code is never the thing that actually runs anywhere. `preflight.py`'s tests
inject `which`/`booted_count`/`web_pkg` callables by hand, so no test confirms the gate actually
*fails* in a genuinely broken environment (no Xcode license accepted, Playwright installed without
its Chromium download) rather than merely reacting correctly to a hand-fed boolean. And
`simctl.py`'s `booted_udids`/`device_catalog` parsers are tested against JSON literals typed by hand,
never a captured real `xcrun simctl list devices -j` payload — a schema change in a future Xcode
version would silently break `doctor` and the device pool's labeling. `.github/actions/bajutsu-e2e/action.yml`
already runs `bajutsu doctor` on the iOS lane, but only as a non-blocking convention check
(`|| echo "doctor: non-blocking (convention score only)"`) that skips `--json` and asserts no verdict,
so the real `simctl` parsing it exercises is never actually checked; Android and web have no doctor
step at all.

A single real invocation of `bajutsu doctor --json` (and, separately, `bajutsu provision`) inside an
existing on-device lane closes most of these gaps at once: it exercises the real `simctl` JSON shape
through the real parser, the real tool-presence checks through the real gate, and — paired with a
deliberately broken variant — proves the gate actually distinguishes ready from not-ready.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **Run `bajutsu doctor --json` for real inside an existing E2E lane.** Add a step to `ios-e2e.yml`
  (and, separately, `android-e2e.yml`/`web-e2e.yml`) that runs the real command against the lane's
  real environment and asserts a Ready/Partial verdict, exercising `simctl.py`'s real JSON parsing
  and `preflight.py`'s real tool checks in one pass.
- **Add one deliberately-broken-environment case.** In a job (or job step) with a tool intentionally
  absent or misconfigured (e.g. `PATH` without `idb`), assert `doctor`/`preflight` correctly reports
  Blocked — the fail side no injected-fake test can prove today.
- **Run `bajutsu provision` for real in a fresh environment.** Add a job (a bare container, or a
  fresh step before the rest of a lane's setup) that runs the real installer end-to-end and asserts
  the tool it targets becomes available, closing the "command string was right" vs. "the real package
  manager accepted it" gap.
- **Capture a real `simctl list devices -j` payload as a fixture.** Replace (or supplement) the
  hand-typed JSON literals in `tests/test_simctl.py` with one captured from a real invocation, so a
  future Xcode schema drift has a chance of being caught before it reaches `doctor` or the pool.

## Alternatives considered

- **Leave the E2E lanes hand-rolling install commands, since the real tools do end up installed.**
  This proves the *tools* work, not that `bajutsu provision`'s own command-construction code is
  correct — the installer could regress with no CI signal at all under this arrangement, which is the
  actual gap.
- **Add more injected-fake unit tests instead.** More fakes covering more hand-typed scenarios would
  still not catch a real `xcrun simctl` schema change or a real broken toolchain; the fakes are
  already internally consistent, which is precisely what makes them unable to observe drift from
  reality.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Run `bajutsu doctor --json` for real inside the iOS, Android, and web E2E lanes, asserting a
  Ready/Partial verdict.
- [ ] Add a deliberately-broken-environment case asserting a Blocked verdict.
- [ ] Run `bajutsu provision` for real in a fresh environment.
- [ ] Capture a real `simctl list devices -j` payload as a test fixture.

## References

- [BE-0164 — Config-aware environment installer](../BE-0164-config-aware-environment-installer/BE-0164-config-aware-environment-installer.md)
- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- `bajutsu/provision.py`, `bajutsu/preflight.py`, `bajutsu/requirements.py`, `bajutsu/simctl.py`,
  `tests/test_provision.py`, `tests/test_preflight.py`, `tests/test_requirements.py`,
  `tests/test_simctl.py`, `.github/actions/bajutsu-e2e/action.yml`,
  `.github/actions/boot-simulator/action.yml`, `.github/workflows/ios-e2e.yml`,
  `.github/workflows/web-e2e.yml`, `.github/workflows/android-e2e.yml`

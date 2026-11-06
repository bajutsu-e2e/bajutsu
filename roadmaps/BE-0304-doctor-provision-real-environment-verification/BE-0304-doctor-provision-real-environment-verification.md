**English** · [日本語](BE-0304-doctor-provision-real-environment-verification-ja.md)

# BE-0304 — Real-environment verification for the onboarding gate (doctor, preflight, provision)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0304](BE-0304-doctor-provision-real-environment-verification.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0304") |
| Implementing PR | [#1367](https://github.com/bajutsu-e2e/bajutsu/pull/1367), [#1370](https://github.com/bajutsu-e2e/bajutsu/pull/1370) |
| Topic | doctor / onboarding |
<!-- /BE-METADATA -->

## Introduction

The onboarding gate has three parts:

- `provision.py`, which installs backend tooling.
- `preflight.py` and `requirements.py`, the runnability gate that `doctor` reports.
- The JSON parsers in `simctl.py` (`booted_udids`, `device_catalog`), which back both.

Injected fakes test all three. Mocked `subprocess.run` calls assert the right command string.
`shutil.which` is monkeypatched. Hand-typed JSON literals stand in for `xcrun simctl list devices -j`.
CI never runs any of it for real.

This item wires `bajutsu doctor` and `python -m bajutsu.provision` into an existing E2E lane. The whole
onboarding path then runs at least once against a genuinely real environment, and against a genuinely
known-broken one.

## Motivation

Three gaps share one root cause and one fix.

**Provision.** `provision.py`'s tests assert that a call like
`("brew", "install", "facebook/fb/idb-companion")` was *built*. They never assert that a real `brew`
accepted it. Every real E2E workflow (`ios-e2e.yml`, `web-e2e.yml`, `android-e2e.yml`) hand-rolls the
equivalent install commands instead of calling `python -m bajutsu.provision`. The installer's own
command-construction code therefore never runs anywhere.

**Preflight.** `preflight.py`'s tests inject `which`, `booted_count`, and `web_pkg` callables by hand.
They show that the gate reacts correctly to a hand-fed boolean. No test confirms that the gate *fails*
in a genuinely broken environment, such as one with no Xcode license accepted, or with Playwright
installed without its Chromium download.

**Simctl parsing.** The `booted_udids` and `device_catalog` parsers in `simctl.py` are tested against
hand-typed JSON literals, never against a captured real `xcrun simctl list devices -j` payload. A
schema change in a future Xcode version would silently break `doctor` and the device pool's labeling.

`.github/actions/bajutsu-e2e/action.yml` already runs `bajutsu doctor` on the iOS lane. But the run is
a non-blocking convention check (`|| echo "doctor: non-blocking (convention score only)"`). It discards
the exit code and never asserts anything from the rendered output, so the real `simctl` parsing it
exercises is never checked. Android and web have no doctor step at all.

A single real invocation of `bajutsu doctor` (and, separately, `python -m bajutsu.provision`) inside
an existing on-device lane closes most of these gaps at once:

- It exercises the real `simctl` JSON shape through the real parser.
- It exercises the real tool-presence checks through the real gate.
- Paired with a deliberately broken variant, it proves that the gate distinguishes ready from
  not-ready.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **Run `bajutsu doctor` for real inside an existing E2E lane.** Add a step to `ios-e2e.yml` (and,
  separately, `android-e2e.yml` and `web-e2e.yml`). The step runs the real command against the lane's
  real environment. It asserts that the rendered `environment:` section reports no `✗`, which means the
  environment gate itself does not exit 1 or 2. One pass exercises `simctl.py`'s real JSON parsing and
  `preflight.py`'s real tool checks.
- **Add one deliberately-broken-environment case.** Use a job (or job step) with a tool intentionally
  absent or misconfigured, e.g. a `PATH` without `idb`. Assert that `doctor` / `preflight` exits
  non-zero with an `✗` in the rendered `environment:` section. No injected-fake test can prove this
  fail side today.
- **Run `python -m bajutsu.provision` for real in a fresh environment.** Add a job that runs the real
  installer end to end. The job is either a bare container or a fresh step before the rest of a lane's
  setup. It asserts that the tool the installer targets becomes available. This closes the gap between
  "the command string was right" and "the real package manager accepted it".
- **Capture a real `simctl list devices -j` payload as a fixture.** Replace (or supplement) the
  hand-typed JSON literals in `tests/test_simctl.py` with one captured from a real invocation. A
  future Xcode schema drift then has a chance of being caught before it reaches `doctor` or the pool.

## Alternatives considered

- **Leave the E2E lanes hand-rolling install commands, since the real tools do end up installed.**
  That proves the *tools* work. It does not prove that `python -m bajutsu.provision`'s own
  command-construction code is correct. Under this arrangement the installer could regress with no CI
  signal at all, and that is the actual gap.
- **Add more injected-fake unit tests instead.** More fakes covering more hand-typed scenarios would
  still not catch a real `xcrun simctl` schema change or a real broken toolchain. The fakes are
  already internally consistent, and that is precisely what makes them unable to observe drift from
  reality.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Run `bajutsu doctor` for real inside the iOS, Android, and web E2E lanes, asserting the
  `environment:` section reports no `✗`.
- [x] Add a deliberately-broken-environment case asserting a non-zero exit with an `✗` in the
  `environment:` section.
- [x] Run `python -m bajutsu.provision` for real in a fresh environment.
- [x] Capture a real `simctl list devices -j` payload as a test fixture.

### Log

- **web-lane onboarding slice** (this PR): a new non-gating `onboarding (doctor / provision)` job in
  `web-e2e.yml` runs the onboarding gate end to end against a real environment. It runs three checks
  in order.
  - On the browserless host, `doctor` must fail loudly with a `✗`. This is the broken-environment
    case.
  - `python -m bajutsu.provision --backend web` installs Chromium for real.
  - `doctor`'s environment gate then passes.

  Separately, `tests/test_simctl.py` gains a real captured `simctl list devices available -j` payload
  (`tests/data/simctl_list_devices_available.json`). The `device_catalog` / `booted_udids` parsers are
  checked against it, so an Xcode schema drift has a chance of being caught. The iOS and Android
  `doctor` steps (the first checklist item's remaining two lanes) are a later slice, which is why the
  item stays *In progress*.
- **iOS + Android onboarding slice** (this PR): the first checklist item's remaining two lanes land,
  completing the item.
  - The `smoke` job of `ios-e2e.yml` (Simulator booted) and the `smoke` job of `android-e2e.yml`
    (inside the emulator session) each run `bajutsu doctor` for real. Each asserts that the
    `environment:` section reports no `✗`. The assertion exercises the real device-count parsing of
    `simctl.py` and `adb`, and the real tool checks of `preflight.py`, through the real actuator.
  - A new `bajutsu doctor --environment-only` flag stops doctor at the runnability gate. The assertion
    is then scoped to the environment gate, and it never pays for the screen probe or flakes on it. On
    iOS, the screen probe is the resident XCUITest runner.
  - `scripts/assert_doctor_env.py` replaces the fragile inline `awk` / `grep` that the web slice first
    shipped, across all three lanes. Its section parser is unit-tested
    (`tests/test_assert_doctor_env.py`). This is the same "untested shell → unit-tested Python" move
    that `scripts/e2e_changes.py` made.
  - Both smoke jobs gate on the assertion, so a `✗` fails the required `E2E` check. The environment
    gate is strictly weaker than the smoke run each job already gates on, so it adds no flakiness.
    The web slice's job differs: it stays non-gating because it re-runs `provision`, which carries a
    `playwright install` network flake.

## References

- [BE-0024 — doctor / onboarding](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding.md) — the topic-anchor item this proposal's onboarding-gate work extends.
- [BE-0164 — Config-aware environment installer](../BE-0164-config-aware-environment-installer/BE-0164-config-aware-environment-installer.md)
- [BE-0282 — Real-backend network capture, mock, and assertion coverage in CI](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage.md)
- `bajutsu/provision.py`, `bajutsu/preflight.py`, `bajutsu/requirements.py`, `bajutsu/simctl.py`,
  `tests/test_provision.py`, `tests/test_preflight.py`, `tests/test_requirements.py`,
  `tests/test_simctl.py`, `.github/actions/bajutsu-e2e/action.yml`,
  `.github/actions/boot-simulator/action.yml`, `.github/workflows/ios-e2e.yml`,
  `.github/workflows/web-e2e.yml`, `.github/workflows/android-e2e.yml`

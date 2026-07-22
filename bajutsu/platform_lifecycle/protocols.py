"""The `Environment` seam's Protocols and the readiness result it hands back (BE-0009 Phase 0).

The deterministic core never names a platform; only three seams are platform-specific — the
actuator (`drivers/*.py`), the **environment** (bring the app to a fresh, launched state), and the
stable-id convention. This package owns the second: an `Environment` Protocol whose `start` runs one
platform's whole per-run startup sequence and returns a ready-to-poll driver, and whose lease-shaping
methods (`relauncher` / `controller` / `teardown` / the network-observation strategy) let the runner
drive every platform through one interface instead of branching on the actuator name. The iOS
(`simctl`) sequence, the web (browser-context) sequence, and the Android (`adb`, [BE-0007]) sequence
live behind the same interface, and a further platform slots in the same way. Each concrete
implementer lives in `environments/`; the factories (`environment_for`, the relaunchers) sit at the
package root, and the two hand-rolled readiness loops in `readiness.py`.

## Two lease surfaces (BE-0197)

The seam serves two commands, so its Protocol is split by command rather than carried as one flat
surface: `RunEnvironment` is the `run` lease (`start`, `device_catalog`, `relauncher`, `controller`,
`teardown`, `hook_collector`, `bridge_collector`, the run predicates, and the two device-identity
queries `resolve_device` / `captures_video`); `CrawlEnvironment` is the `crawl` lease (`has_devices`,
`plan_lanes`, and the `crawl_*` methods). Every concrete platform implements both, and `Environment`
is their union — the full surface a platform class satisfies and `environment_for` returns. The
`run` pipeline (`runner/pool.py`, `runner/launch.py`) holds its environment as a `RunEnvironment`
and the `crawl` command (`cli/commands/crawl.py`) as a `CrawlEnvironment`, so each reader sees only
the methods its command calls and mypy keeps the two from drifting into each other.

## Declining a method (the "not applicable" contract)

A method a platform has no use for is declined in exactly one of three ways, chosen per method
(never ad hoc), and each method's docstring states which it is:

- **First-class null / empty** — for a method the caller *always* invokes and interprets a null
  answer from: `controller` → `None` (no device control), `device_catalog` → `{}` (no devices),
  `crawl_aliveness` / `crawl_recover` / `crawl_dialog_clearer` → `None` (no such behavior here).
  The null value *is* the platform's answer, not an unimplemented stub — so a declining platform
  returns it rather than raising.
- **Gated raise** — only for a method the caller invokes *solely when* a predicate is true:
  `hook_collector`, which the runner calls only after `observes_network_via_driver()`. A platform
  that returns `False` from the predicate may leave `hook_collector` raising `NotImplementedError`,
  because the check makes the raise unreachable. This is the *only* method that may raise.
- **No-op implementation** — for a method whose return type is not itself optional (the caller
  invokes the value it gets back, so there is no null to hand it): `bridge_collector`, whose iOS/web
  decline is `lambda: None` — a real, callable teardown thunk that does nothing — rather than `None`
  or a raise, because the caller always calls the returned thunk unconditionally at release.

This taxonomy governs a *capability method a platform has no use for*, so two members sit outside it
rather than inventing a fourth idiom. A **predicate** answers rather than declines: `has_reusable_resident`
/ `has_devices` returning `False` is the query's answer (see "Predicate → capability pairing" below),
not a not-applicable stub. A method with a **meaningful default** is likewise not a decline:
`end_lease`'s default delegates to `teardown` — the full, real release every platform without a warm
resident already performs — not a null, a gated raise, or a no-op.

## Predicate → capability pairing

Three run predicates each gate one capability method, honored at a single runner call site. A fourth
predicate, `has_devices`, is a `crawl`-side flag that shapes the lane-prep message — it gates
nothing (`plan_lanes` is called unconditionally); a fifth, `captures_video`, is a `record`-side
query with no gate here (the CLI reads it to decide whether to record during authoring):

| Predicate                     | Role                                            | Honored at                 |
|-------------------------------|-------------------------------------------------|----------------------------|
| `observes_network_via_driver` | gates `hook_collector` (may gated-raise if F)   | `runner/pool.py` (`lease`) |
| `records_video_up_front`      | gates `start`'s `record_video_dir` wiring       | `runner/pool.py` (`lease`) |
| `has_reusable_resident`       | gates the pool's warm-runner cache (BE-0291)    | `runner/pool.py` (`lease`) |
| `has_devices`                 | shapes the crawl lane-prep message (not a gate) | `cli/commands/crawl.py`    |
| `captures_video`              | whether `record` captures video while authoring | `cli/commands/record.py`   |

## Adding a platform

A new `Environment` (extend `environment_for`) must, at minimum:

1. Implement the full `RunEnvironment` surface: `start` (the per-run bring-up returning a launched
   driver), `relauncher`, `controller` (return `None` if none), `teardown`, `device_catalog`
   (return `{}` if none), `resolve_device`, `captures_video`, `prestarted_intervals` (the captures
   `start` began before launch for the sink to adopt; `[]` if none — the pool calls it every lease),
   and the two run predicates (`observes_network_via_driver`, `records_video_up_front`).
   `hook_collector` may gated-raise
   unless `observes_network_via_driver()` returns `True`. `bridge_collector` returns a real teardown
   thunk if the platform's device needs the host collector tunneled to it (Android); `lambda: None`
   otherwise (a Simulator shares the host loopback, and a driver-observed platform never reaches it).
   `has_reusable_resident` / `end_lease` (BE-0291) default to "no warm resident" (`False` / delegate
   to `teardown`); implement them only for a platform whose `start` spawns an expensive resident
   worth amortizing across leases (XCUITest's `xcodebuild` runner).
2. Implement `CrawlEnvironment` as well: `has_devices`, `plan_lanes`, `crawl_reset`, and the three
   `crawl_*` health methods (return `None` from each the platform lacks). `environment_for` returns
   the union `Environment`, so a platform class must satisfy both surfaces — but the crawl half is
   cheap: the health methods are first-class `None`, and a run-first platform can mirror its
   `relauncher` in `crawl_reset` and its device pooling in `plan_lanes`. Consumers still narrow to
   the one surface they use (`RunEnvironment` in the run pipeline, `CrawlEnvironment` in `crawl`);
   the union is what a *new class* provides, not what either *reader* depends on.

Follow the "not applicable" contract above for every method the platform declines; do not invent a
third idiom.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from bajutsu.config import Effective
from bajutsu.crawl import AliveCheck, ClearBlocking, Recover, Reset
from bajutsu.drivers import base
from bajutsu.evidence import intervals
from bajutsu.evidence.network import Collector
from bajutsu.orchestrator import DeviceControl, RelaunchFn
from bajutsu.scenario import Preconditions, Scenario


@dataclass(frozen=True)
class ProvisionProfile:
    """What a `DeviceProvider` already did to the device the lease hands over.

    Carries the state the provider set up before the lease reached the environment (BE-0236). A
    locally-attached device (the default `local` provider) boots and installs the app itself, so
    both flags are False and `start` runs the full adb/simctl bring-up unchanged. A device-cloud
    provider that hands over an already-booted device carrying the build sets them, letting the
    environment skip the boot-readiness wait and/or the install — the one difference a cloud target
    needs, expressed as data on the lease rather than a branch in the runner. Pure config carried
    through the seam; never a verdict input (prime directive 1).
    """

    boot_ready: bool = False
    app_preinstalled: bool = False


@dataclass(frozen=True)
class ReadinessResult:
    """The outcome of the post-launch readiness gate, for the wait-timeout diagnostic (BE-0231).

    When the first scenario `wait` times out, this says whether the gate had declared the app ready
    and on which signal — the evidence that separates "the gate returned before the content the
    scenario needs" from "the content rendered, then the awaited element didn't". Pure diagnosis: it
    never enters a verdict (prime directive 1).
    """

    ready: bool
    signal: Literal["readyWhen", "namespace", "count", "timeout"]
    elapsed_s: float


@runtime_checkable
class RunEnvironment(Protocol):
    """The `run` lease surface: produce a freshly-launched app and drive its per-lease shape.

    `start` owns the entire per-run startup for a platform, so the caller need not know whether that
    means a `simctl` device sequence or a fresh browser context — it gets back a driver bound to the
    launched app (not yet polled for readiness; the runner does that). `permissions` (BE-0276) is
    applied before the app process starts, so a known permission's runtime prompt never appears; a
    platform without a mechanism for it (web, fake) raises `UnsupportedAction` if asked to apply
    one — preflight already rejects a scenario naming an unsupported service before `start` is ever
    called, so this is only the runtime backstop for a caller that bypasses it. The remaining methods describe
    the differences the pool used to branch on the actuator name for: how the device handle resolves,
    how network is observed, whether video can be captured, whether video must be wired before launch,
    and the per-scenario relaunch / device control / teardown. This is the narrower surface the `run`
    pipeline (`runner/pool.py`, `runner/launch.py`) holds; the module docstring's "not applicable"
    contract governs how a platform declines each method.
    """

    def start(
        self,
        eff: Effective,
        pre: Preconditions,
        *,
        extra_env: Mapping[str, str] | None = None,
        record_video_dir: Path | None = None,
        permissions: Mapping[str, str] | None = None,
    ) -> base.Driver: ...

    def resolve_device(self, udid: str) -> str:
        """Resolve *udid* to a concrete device handle for this platform.

        The seam that replaces the CLI/doctor layer testing the actuator string to pick a resolver
        (BE-0256): the iOS family resolves through `simctl`, Android through `adb`, and web has no
        device so it returns *udid* unchanged. The environment already *is* its platform, so it needs
        no actuator argument — that is exactly the string this method removes from its callers.
        """

    def captures_video(self) -> bool:
        """Whether `record` captures a scenario-wide screen video while authoring on this platform.

        A distinct axis from `records_video_up_front` (which asks *when* capture is wired, not
        *whether* the platform can capture at all): the simctl-backed device platform (xcuitest)
        records via a simctl interval, web captures by other means during replay, and the fake
        backend has no device to record. The `record` command reads this instead of a
        per-actuator name test, which historically special-cased the coordinate backend and silently
        never captured under xcuitest (BE-0256).
        """

    def device_catalog(self) -> dict[str, dict[str, str]]:
        """Static device metadata (model / OS) keyed by udid.

        Returns `{}` for a platform with no device (web) — a first-class "no devices", not an
        unimplemented stub; the caller always invokes this and reads the empty map as the answer.
        """

    def observes_network_via_driver(self) -> bool:
        """Whether network is observed by hooking the live driver (web) rather than an external
        receiver the app reports to (the device backends). Gates `hook_collector`."""

    def records_video_up_front(self) -> bool:
        """Whether video capture must be wired before launch — so the app's cold start is recorded —
        rather than on demand after launch. True for web (its context records at creation) and for
        the idb and Android backends (they start recording before the app launches). Gates `start`'s
        `record_video_dir` handling, and thus whether `prestarted_intervals` can be non-empty."""

    def prestarted_intervals(self) -> list[intervals.Interval]:
        """Interval captures `start` began before the app launched, for the sink to adopt and finalize.

        A device backend starts the scenario video before launch so the cold start is recorded, then
        hands the running capture over here for the sink to adopt (`intervals.adopt`) rather than
        start a fresh one on demand. Empty on a backend that records on demand or wires its up-front
        recording through the driver instead (web binds it to the browser context)."""

    def hook_collector(self, driver: base.Driver, scenario: Scenario) -> Collector:
        """The page-hooked collector for a driver-observed platform, with this scenario's mocks wired
        in.

        Gated raise: the runner calls this *only when* `observes_network_via_driver()` is `True`, so a
        platform that returns `False` there may leave this raising `NotImplementedError` — the check
        makes the raise unreachable. This is the only Protocol method permitted to raise.
        """

    def bridge_collector(self, port: int) -> Callable[[], None]:
        """Make the host's network collector on `127.0.0.1:port` reachable from the leased device.

        No-op implementation: the return type carries no null, so a platform with nothing to bridge
        returns a real, callable no-op thunk (`lambda: None`) rather than `None` or a raise.

        Called only on the external-receiver path (a `NetworkCollector` was pre-started and its URL
        injected), right before launch, and the returned thunk is invoked when the lease releases. The
        iOS Simulator shares the Mac's loopback, so most platforms need nothing and return a no-op; the
        Android emulator's loopback is its own, so `AndroidEnvironment` tunnels the port back with
        `adb reverse` (BE-0283). Returns the teardown thunk (removes the tunnel), never `None`.
        """

    def relauncher(
        self,
        eff: Effective,
        scenario: Scenario,
        driver: base.Driver,
        *,
        extra_env: Mapping[str, str] | None = None,
    ) -> RelaunchFn:
        """The scenario's `relaunch` function (app restart on a device; re-navigate on web)."""

    def controller(self, eff: Effective) -> DeviceControl | None:
        """Device control for the leased device.

        Returns `None` on a platform without one (web) — a first-class "no device control" the runner
        interprets, not an unimplemented stub.
        """

    def teardown(self, driver: base.Driver, eff: Effective) -> None:
        """Per-release app teardown: terminate the app (device) or close the browser (web).

        The full teardown, including any resident process the environment owns (XCUITest's
        `xcodebuild` runner). The pool calls this at the moments it owns runner termination
        (BE-0291): a run-set's end, an actuator switch on a device, and a warm resident that failed
        to resume — as well as the ordinary per-lease release of a platform with no warm resident.
        """

    def has_reusable_resident(self) -> bool:
        """Whether `start` left a resident process the pool should keep warm across leases (BE-0291).

        A predicate read *after* `start`: `True` means this environment holds a resident (XCUITest's
        `xcodebuild test-without-building` runner on a Simulator) whose cold startup is worth
        amortizing, so the pool caches this environment for the device and reuses it — a later
        same-actuator lease resumes the resident via `start` (app relaunch only) instead of spawning
        a new one, and the lease releases through `end_lease` rather than `teardown`. Default `False`
        (no resident to reuse — every platform but the Simulator XCUITest backend), so the pool's
        cache never activates and the per-lease teardown is unchanged.
        """

    def end_lease(self, driver: base.Driver, eff: Effective) -> None:
        """Release one lease while keeping a warm resident alive (BE-0291).

        Called instead of `teardown` when the pool is keeping this environment's resident warm for
        the next lease on the device: it does the per-scenario cleanup (terminate the app) but leaves
        the resident running. Default: delegate to `teardown` — a platform with no warm resident
        (`has_reusable_resident()` is `False`) is never kept warm, so its `end_lease` and `teardown`
        are the same release.
        """


@runtime_checkable
class CrawlEnvironment(Protocol):
    """The `crawl` lease surface: the lane shape and health seams the CLI used to branch on the
    actuator for.

    This is the narrower surface the `crawl` command (`cli/commands/crawl.py`) holds; the concrete
    platform classes satisfy it alongside `RunEnvironment`. The three `crawl_*` health methods follow
    the module docstring's first-class-null contract — a platform without a given behavior returns
    `None` rather than raising.
    """

    def has_devices(self) -> bool:
        """Whether this platform drives real devices (web has none). Sizes the crawl's lane-prep
        message and distinguishes the web browser-lane sizing from a device pool."""

    def plan_lanes(self, udid_arg: str, workers: int) -> list[str]:
        """The crawl's lane udids. A device pool resolves *udid_arg* and caps to *workers*; web has no
        device, so *workers* alone sizes the browser-lane set (each lane one browser)."""

    def crawl_reset(self, eff: Effective) -> Reset:
        """A crawl `reset` to a clean start on this lane: relaunch the app (device) or open a fresh
        browser context (web), then wait until the first screen renders."""

    def crawl_aliveness(self) -> AliveCheck | None:
        """The crawl's crash signal for a driver-observed platform (web reads pageerror / HTTP status
        / blank DOM).

        Returns `None` for the device backends (the engine reads the accessibility tree) — a
        first-class "no such signal here", not an unimplemented stub.
        """

    def crawl_recover(self) -> Recover | None:
        """Heal a wedged lane (relaunch a crashed/hung browser) on web.

        Returns `None` where the platform has no in-lane recovery (the device backends) — a
        first-class "no recovery here", not an unimplemented stub.
        """

    def crawl_dialog_clearer(self) -> ClearBlocking | None:
        """Report blocking dialogs auto-cleared this step (web JS dialogs the driver dismisses).

        Returns `None` on platforms with no such auto-clear — a first-class "nothing auto-cleared
        here", not an unimplemented stub.
        """


@runtime_checkable
class Environment(RunEnvironment, CrawlEnvironment, Protocol):
    """One platform's whole app lifecycle: the union of the `run` and `crawl` lease surfaces.

    Every concrete platform class satisfies this combined surface, and `environment_for` returns it;
    each consumer then narrows to the one it needs (`RunEnvironment` for the run pipeline,
    `CrawlEnvironment` for the crawl command). See the module docstring for the "not applicable"
    contract and the "adding a platform" checklist.
    """

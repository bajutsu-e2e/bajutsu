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
`teardown`, `hook_collector`, the run predicates, and the two device-identity queries
`resolve_device` / `captures_video`); `CrawlEnvironment` is the `crawl` lease (`has_devices`,
`plan_lanes`, and the `crawl_*` methods). Every concrete platform implements both, and `Environment`
is their union — the full surface a platform class satisfies and `environment_for` returns. The
`run` pipeline (`runner/pool.py`, `runner/launch.py`) holds its environment as a `RunEnvironment`
and the `crawl` command (`cli/commands/crawl.py`) as a `CrawlEnvironment`, so each reader sees only
the methods its command calls and mypy keeps the two from drifting into each other.

## Declining a method (the "not applicable" contract)

A method a platform has no use for is declined in exactly one of two ways, chosen per method (never
ad hoc), and each method's docstring states which it is:

- **First-class null / empty** — for a method the caller *always* invokes and interprets a null
  answer from: `controller` → `None` (no device control), `device_catalog` → `{}` (no devices),
  `crawl_aliveness` / `crawl_recover` / `crawl_dialog_clearer` → `None` (no such behavior here).
  The null value *is* the platform's answer, not an unimplemented stub — so a declining platform
  returns it rather than raising.
- **Gated raise** — only for a method the caller invokes *solely when* a predicate is true:
  `hook_collector`, which the runner calls only after `observes_network_via_driver()`. A platform
  that returns `False` from the predicate may leave `hook_collector` raising `NotImplementedError`,
  because the check makes the raise unreachable. This is the *only* method that may raise.

## Predicate → capability pairing

Two run predicates each gate one capability method, honored at a single runner call site. A third
predicate, `has_devices`, is a `crawl`-side flag that shapes the lane-prep message — it gates
nothing (`plan_lanes` is called unconditionally); a fourth, `captures_video`, is a `record`-side
query with no gate here (the CLI reads it to decide whether to record during authoring):

| Predicate                     | Role                                            | Honored at                 |
|-------------------------------|-------------------------------------------------|----------------------------|
| `observes_network_via_driver` | gates `hook_collector` (may gated-raise if F)   | `runner/pool.py` (`lease`) |
| `records_video_up_front`      | gates `start`'s `record_video_dir` wiring       | `runner/pool.py` (`lease`) |
| `has_devices`                 | shapes the crawl lane-prep message (not a gate) | `cli/commands/crawl.py`    |
| `captures_video`              | whether `record` captures video while authoring | `cli/commands/record.py`   |

## Adding a platform

A new `Environment` (extend `environment_for`) must, at minimum:

1. Implement the full `RunEnvironment` surface: `start` (the per-run bring-up returning a launched
   driver), `relauncher`, `controller` (return `None` if none), `teardown`, `device_catalog`
   (return `{}` if none), `resolve_device`, `captures_video`, and the two run predicates
   (`observes_network_via_driver`, `records_video_up_front`). `hook_collector` may gated-raise
   unless `observes_network_via_driver()` returns `True`.
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

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from bajutsu.config import Effective
from bajutsu.crawl import AliveCheck, ClearBlocking, Recover, Reset
from bajutsu.drivers import base
from bajutsu.evidence.network import Collector
from bajutsu.orchestrator import DeviceControl, RelaunchFn
from bajutsu.scenario import Preconditions, Scenario


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
        *whether* the platform can capture at all): the simctl-backed device platforms (idb,
        xcuitest) record via a simctl interval, web captures by other means during replay, and the
        fake backend has no device to record. The `record` command reads this instead of spelling
        out `actuator == "idb"`, which silently never captured under xcuitest (BE-0256).
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
        """Whether video capture must be wired before launch (web's context records at creation)
        rather than on demand (simctl). Gates `start`'s `record_video_dir` handling."""

    def hook_collector(self, driver: base.Driver, scenario: Scenario) -> Collector:
        """The page-hooked collector for a driver-observed platform, with this scenario's mocks wired
        in.

        Gated raise: the runner calls this *only when* `observes_network_via_driver()` is `True`, so a
        platform that returns `False` there may leave this raising `NotImplementedError` — the check
        makes the raise unreachable. This is the only Protocol method permitted to raise.
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
        """Per-release app teardown: terminate the app (device) or close the browser (web)."""


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

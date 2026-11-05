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
`teardown`, `hook_collector`, `bridge_collector`, `collector_host`, `reach_device_port`, the run
predicates, `replaced_device` for a lease
that moved to another device, and the two device-identity queries `resolve_device` /
`captures_video`); `CrawlEnvironment` is the `crawl` lease (`has_devices`,
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
  because the check makes the raise unreachable. This is the *only* method that may raise to
  decline; a real failure (`collector_host` with no address to offer, `reach_device_port` with no
  bridge) still raises `DeviceError`, as `start` does.
- **No-op implementation** — for a method whose return type is not itself optional (the caller
  invokes the value it gets back, so there is no null to hand it): `bridge_collector`, whose iOS/web
  decline is `lambda: None` — a real, callable teardown thunk that does nothing — rather than `None`
  or a raise, because the caller always calls the returned thunk unconditionally at release.
  `reach_device_port` declines the same way, handing back the port itself with such a thunk, and
  `collector_host` with the loopback `CollectorHost()`.

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
   `collector_host` returns `CollectorHost()` (the loopback) and `reach_device_port` returns the port
   itself with `lambda: None`, unless the device shares no loopback with the host (a real iOS device).
   `has_reusable_resident` / `end_lease` (BE-0291) default to "no warm resident" (`False` / delegate
   to `teardown`); implement them only for a platform whose `start` spawns an expensive resident
   worth amortizing across leases (XCUITest's `xcodebuild` runner). `replaced_device` defaults to
   `None` ("the leased device is the one that ran"); implement it only for a platform whose `start`
   can move the lease to a different device, which today means the XCUITest Simulator replacing one
   CoreSimulator has stopped listing — the pool re-keys every per-device structure off what it
   returns.
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

from .collector_host import LOOPBACK, CollectorHost
from .crawl_environment import CrawlEnvironment
from .environment import Environment
from .provision_profile import ProvisionProfile
from .readiness_result import ReadinessResult
from .run_environment import RunEnvironment

__all__ = [
    "LOOPBACK",
    "CollectorHost",
    "CrawlEnvironment",
    "Environment",
    "ProvisionProfile",
    "ReadinessResult",
    "RunEnvironment",
]

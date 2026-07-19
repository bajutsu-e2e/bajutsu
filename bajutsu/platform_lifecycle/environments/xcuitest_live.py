"""The live-route XCUITest lifecycle: open a WebDriver session, no simctl / xcodebuild (BE-0238).

The local XCUITest environment runs the simctl device-prep and an `xcodebuild test-without-building`
subprocess. A reserved device behind an Appium / WebDriver endpoint needs neither: the grid already
holds the device booted with its build installed (the BE-0236 `ProvisionProfile` the `appium`
provider reports), so this environment simply opens a WebDriver session against the endpoint, hands
the run an `XcuitestLiveDriver` bound to it, and closes the session on teardown.

`environment_for` selects this environment when the xcuitest actuator's udid spec is an `http(s)://`
endpoint (`is_webdriver_endpoint`). The URL scheme is the routing signal: it is exactly the value the
shared `device_id` policy rejects, so recognizing it up front both selects the live path and keeps
the endpoint clear of the simctl / xcodebuild udid machinery, which structurally cannot carry a URL.
That same reason forces the simctl-backed seam methods off: `resolve_device`, the device catalog, the
`DeviceControl` controller, and the relauncher all build a `simctl.Env(udid)` that would reject the
URL, so each is overridden to the live route's shape (pass the endpoint through, no catalog, no
controller, relaunch deferred to Slice B).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from bajutsu.config import Effective, require_ios
from bajutsu.crawl import Reset
from bajutsu.drivers import base
from bajutsu.drivers.xcuitest_live import (
    WdTransportFn,
    WebDriverClient,
    XcuitestLiveDriver,
    _raw_wd_transport,
)
from bajutsu.orchestrator import DeviceControl, RelaunchFn
from bajutsu.platform_lifecycle.environments.ios import _DeviceEnvironment
from bajutsu.scenario import Preconditions, Scenario


def is_webdriver_endpoint(udid: str) -> bool:
    """Whether *udid* is a WebDriver endpoint (an `http(s)://` URL) rather than a device id.

    The routing signal for the live route: a URL is exactly what the shared `device_id` policy
    rejects, so it can never collide with a real simctl / device udid.
    """
    return udid.startswith(("http://", "https://"))


class XcuitestLiveEnvironment(_DeviceEnvironment):
    """Drive a reserved iOS device over a WebDriver endpoint, off the simctl / xcodebuild path.

    Shares the device-style lease surface (`_DeviceEnvironment`) but overrides every method that would
    reach simctl with the URL endpoint. The transport is injectable (`transport_factory`) so the
    session lifecycle is exercised against a fake WebDriver endpoint with no grid on the gate, the same
    way the local driver injects its wire.
    """

    def __init__(
        self,
        actuator: str,
        endpoint: str,
        *,
        transport_factory: Callable[[str], WdTransportFn] = _raw_wd_transport,
    ) -> None:
        # The endpoint stands in for the udid on the shared base; every simctl-touching method it backs
        # is overridden below, so the base's `env_run` is never exercised.
        super().__init__(actuator, endpoint)
        self._endpoint = endpoint
        self._transport_factory = transport_factory
        self._client: WebDriverClient | None = None

    def start(
        self,
        eff: Effective,
        pre: Preconditions,
        *,
        extra_env: Mapping[str, str] | None = None,
        record_video_dir: Path | None = None,
        permissions: Mapping[str, str] | None = None,
    ) -> base.Driver:
        # A reserved cloud device is booted with its build installed (the provider's ProvisionProfile),
        # so there is no simctl bring-up here — the endpoint never reaches the udid machinery. The
        # bundle id tells Appium which installed app to drive.
        #
        # Simctl-backed and not-yet-wired preconditions fail loudly rather than silently no-op'ing
        # (determinism first — the scenario author must know the option had no effect).
        if pre.erase:
            raise base.UnsupportedAction(
                "erase is a simctl operation and does not apply to the live WebDriver route"
            )
        if permissions:
            raise base.UnsupportedAction(
                "permission grants use simctl and do not apply to the live WebDriver route"
            )
        if extra_env:
            raise base.UnsupportedAction(
                "extra_env is not yet wired on the live WebDriver route (BE-0238 Slice B)"
            )
        ios = require_ios(eff)
        self._client = WebDriverClient(self._transport_factory(self._endpoint))
        self._client.new_session(
            {
                "platformName": "iOS",
                "appium:automationName": "XCUITest",
                "appium:bundleId": ios.bundle_id,
            }
        )
        driver = XcuitestLiveDriver(self._client)
        driver.await_ready()
        return driver

    def teardown(self, driver: base.Driver, eff: Effective) -> None:
        # Close the WebDriver session; the grid, not this run, owns the device, so there is no simctl
        # terminate (the base's teardown) to run.
        if self._client is not None:
            self._client.delete_session()
            self._client = None

    def resolve_device(self, udid: str) -> str:
        # The endpoint is already the concrete handle; passing it through simctl would reject the URL.
        return udid

    def device_catalog(self) -> dict[str, dict[str, str]]:
        # No simctl catalog for a remote grid — a first-class empty map, like a driver-observed
        # platform, so the report simply omits the device model / OS.
        return {}

    def controller(self, eff: Effective) -> DeviceControl | None:
        # simctl device control cannot reach a cloud device; the preflight (Slice C) narrows the
        # device-control capabilities away, and here the runner reads `None` as "no device control".
        return None

    def crawl_reset(self, eff: Effective) -> Reset:
        # Crawl reset terminates and relaunches the app via simctl in the base; simctl cannot reach a
        # cloud device. Raise clearly from the closure rather than letting `simctl.Env(endpoint)` crash
        # with a confusing DeviceError (live-route crawl support is a follow-on slice).
        def reset(driver: base.Driver) -> None:
            raise base.UnsupportedAction(
                "crawl_reset is not yet wired on the live WebDriver route (BE-0238 Slice B)"
            )

        return reset

    def relauncher(
        self,
        eff: Effective,
        scenario: Scenario,
        driver: base.Driver,
        *,
        extra_env: Mapping[str, str] | None = None,
    ) -> RelaunchFn:
        # Building the relaunch fn must not touch simctl (the base builds a `simctl.Env(endpoint)`).
        # Relaunch over WebDriver (recreate the session) is Slice B; until then a scenario that
        # actually relaunches fails loudly rather than silently no-op'ing (determinism first).
        def relaunch(opts: object) -> None:
            raise base.UnsupportedAction(
                "relaunch is not yet wired on the live WebDriver route (BE-0238 Slice B)"
            )

        return relaunch

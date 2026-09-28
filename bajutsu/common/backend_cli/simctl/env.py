"""The thin simctl front end for one Simulator device."""

from __future__ import annotations

import contextlib
import json
import plistlib
import subprocess
import tempfile
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from ._functions import (
    _probe_timed_out,
    addmedia_cmd,
    boot_cmd,
    child_env,
    clear_location_cmd,
    erase_cmd,
    export_globals_cmd,
    foreground_cmd,
    get_app_container_cmd,
    home_cmd,
    install_cmd,
    keychain_reset_cmd,
    language_of,
    launch_cmd,
    openurl_cmd,
    pbcopy_cmd,
    pbpaste_cmd,
    privacy_cmd,
    push_cmd,
    real_run,
    screenshot_cmd,
    set_location_cmd,
    shutdown_cmd,
    status_bar_clear_cmd,
    status_bar_override_cmd,
    system_locale_cmds,
    terminate_cmd,
    uninstall_cmd,
    validated_locale,
    validated_udid,
)
from ._shared import _LANGUAGES_KEY, _LOCALE_KEY, RunFn
from .device_error import DeviceError
from .device_timeout import DeviceTimeout

# The one permission-vocabulary service (BE-0276) with no simctl privacy TCC (Transparency,
# Consent, and Control) equivalent — iOS notification authorization is not part of TCC. Every other
# vocabulary entry names its own TCC service (`base.PERMISSION_SERVICES`'s spelling matches
# `simctl privacy`'s service names 1:1), so no separate service->TCC-name map is needed.
_NO_TCC_SERVICE = "notifications"

# simctl's host<->Simulator pasteboard sync (`pbcopy`) intermittently times out — simctl
# exits 60 (ETIMEDOUT), or the call hangs past a reasonable bound — which is transient: a
# re-run clears it. Retry a bounded number of times so a genuine fault still surfaces. The
# budget is deliberately generous (linear backoff, ~15s over five attempts): CI has been
# seen wedged past three quick tries (~1.5s), and this recovery path is only paid when a
# timeout actually occurs, so widening it costs nothing on the healthy path.
_PBCOPY_MAX_ATTEMPTS = 5
_PBCOPY_RETRY_DELAY_S = 1.5
_PBCOPY_TIMEOUT_S = 30.0
_PBCOPY_TIMEOUT_EXIT = 60  # simctl's ETIMEDOUT — the one transient exit worth retrying


class Env:
    """Thin simctl front end for one device."""

    def __init__(self, udid: str, run: RunFn = real_run) -> None:
        # Validate at construction so a bad --udid fails fast at the object boundary (the builders
        # below also validate, so this is belt-and-suspenders — the same posture the device drivers
        # take for their own udid).
        self.udid = validated_udid(udid)
        self._run = run

    def erase(self) -> None:
        self._run(erase_cmd(self.udid), None)

    # The four suppressions below absorb the ordinary "already in that state" failure — shutting a
    # device down that is already off, uninstalling an app that was never installed. Each keys on
    # `CalledProcessError` alone, so a `DeviceTimeout` propagates instead (BE-0363): a hung
    # `shutdown` is not that ordinary outcome, it is the wedge the recovery ladder needs to hear
    # about. Widening any of them to `DeviceError` would put the silence back.

    def shutdown(self) -> None:
        with contextlib.suppress(subprocess.CalledProcessError):
            self._run(shutdown_cmd(self.udid), None)

    def boot(self) -> None:
        with contextlib.suppress(subprocess.CalledProcessError):
            self._run(boot_cmd(self.udid), None)

    def system_locale_matches(self, locale: str) -> bool | None:
        """Whether the device's global domain already renders the language `pin_system_locale` writes.

        `None` distinguishes "could not read the domain" from a definite mismatch, so a caller can
        act on what it actually observed: skipping the write needs a positive match, while failing
        the run needs a positive *mis*match — an unreadable device is neither. A domain that reads
        back fine but carries no pinned language is a *mismatch*, not an unknown: it is positive
        evidence that nothing pinned it.

        A match is one language with **nothing queued behind it** whose subtag is the one we would
        write, plus an exact `AppleLocale`. Comparing the subtag rather than the whole entry matters
        for the common case: a freshly created Simulator inherits the host's language-region tag
        (`en-US`), which selects the same language as the bare `en` this writes, so an exact string
        comparison would rewrite and reboot every device that was already right. A second language
        behind the first is still a mismatch — SpringBoard can fall back to it for a string the first
        lacks, which is the "matched by accident" behaviour this exists to remove.

        Raises:
            DeviceError: `locale` is not safe to place on a `defaults` argv — checked up front, so a
                malformed one never costs a subprocess round trip first.
        """
        checked = validated_locale(locale)
        try:
            exported = plistlib.loads(self._run(export_globals_cmd(self.udid), None).encode())
        except DeviceTimeout as exc:
            _probe_timed_out(exc, "an unreadable global domain")
            return None
        except (subprocess.CalledProcessError, plistlib.InvalidFileException, ValueError):
            return None
        if not isinstance(exported, dict):
            return None
        languages = exported.get(_LANGUAGES_KEY)
        if not isinstance(languages, list) or len(languages) != 1:
            return False  # absent, or a fallback queued behind the first
        return (
            language_of(str(languages[0])) == language_of(checked)
            and exported.get(_LOCALE_KEY) == checked
        )

    def pin_system_locale(self, locale: str) -> bool:
        """Write the device's system-wide language and locale unless already exact; True if it wrote.

        The caller reboots the Simulator when this returns True — a running SpringBoard does not pick
        a global-domain write up live (BE-0320). Skipping the write on a device that already carries
        the value is what keeps the common case (a Simulator pinned by an earlier spawn, or already
        on the configured locale) at the cost of one read instead of a second boot cycle. Only a
        positive match skips the write; an unreadable domain (`None`) writes, since nothing was
        observed to already be right.
        """
        if self.system_locale_matches(locale) is True:
            return False
        for cmd in system_locale_cmds(self.udid, locale):
            self._run(cmd, None)
        return True

    def is_installed(self, bundle_id: str) -> bool:
        try:
            self._run(get_app_container_cmd(self.udid, bundle_id), None)
        except DeviceTimeout as exc:
            _probe_timed_out(exc, "not installed")
            return False
        except subprocess.CalledProcessError:
            return False
        else:
            return True

    def install(self, app_path: str) -> None:
        self._run(install_cmd(self.udid, app_path), None)

    def uninstall(self, bundle_id: str) -> None:
        with contextlib.suppress(subprocess.CalledProcessError):
            self._run(uninstall_cmd(self.udid, bundle_id), None)

    def launch(
        self,
        bundle_id: str,
        args: Sequence[str] = (),
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._run(launch_cmd(self.udid, bundle_id, args), child_env(env or {}))

    def terminate(self, bundle_id: str) -> None:
        with contextlib.suppress(subprocess.CalledProcessError):
            self._run(terminate_cmd(self.udid, bundle_id), None)

    def openurl(self, url: str) -> None:
        self._run(openurl_cmd(self.udid, url), None)

    def screenshot(self, path: str) -> None:
        self._run(screenshot_cmd(self.udid, path), None)

    def set_location(self, lat: float, lon: float) -> None:
        self._run(set_location_cmd(self.udid, lat, lon), None)

    def clear_location(self) -> None:
        self._run(clear_location_cmd(self.udid), None)

    def reset_permissions(self, bundle_id: str) -> None:
        """Reset every TCC grant/revoke this bundle carries back to "ask on next use" (`simctl
        privacy reset all`).

        `simctl install`/`uninstall` do not touch TCC.db — verified on-device (BE-0407 follow-up):
        a grant survives both a plain reinstall and an uninstall-then-install of the same bundle
        id, only `simctl erase` clears it. A caller that means to hand a scenario a clean slate
        (mirroring `adb.Env.clear`'s permission reset on Android) must reset explicitly rather than
        relying on either install path to do it.
        """
        self._run(privacy_cmd(self.udid, "reset", "all", bundle_id), None)

    def apply_permissions(self, bundle_id: str, permissions: Mapping[str, str]) -> None:
        """Grant or revoke each `service: grant|revoke` entry in `permissions` up front, so a
        runtime prompt never blocks the run (`simctl privacy`, BE-0276).

        Every entry's service and action are validated before any `simctl privacy` call runs, so
        an unsupported service or an unrecognized action fails before the device is touched at all
        — never partway through, leaving some services already mutated (preflight/schema normally
        reject this before any device work; this validation is the runtime backstop for a caller
        that bypasses both).

        Raises:
            DeviceError: a service has no TCC equivalent (`notifications`), or an action is neither
                `grant` nor `revoke`.
        """
        for service, action in permissions.items():
            if service == _NO_TCC_SERVICE:
                raise DeviceError(f"permissions.{service} has no simctl privacy equivalent on iOS")
            if action not in ("grant", "revoke"):
                raise DeviceError(
                    f"unknown simctl privacy action: {action!r} (expected grant|revoke)"
                )
        for service, action in permissions.items():
            self._run(privacy_cmd(self.udid, action, service, bundle_id), None)

    def clear_keychain(self) -> None:
        self._run(keychain_reset_cmd(self.udid), None)

    def clear_clipboard(self) -> None:
        # pbcopy reads from stdin, which RunFn doesn't support. Use subprocess
        # directly but route through a class-level attribute so tests can patch it.
        self._run_pbcopy(pbcopy_cmd(self.udid))

    def set_clipboard(self, text: str) -> None:
        # Same simctl pbcopy as clearing, but with the seed text on stdin.
        self._run_pbcopy(pbcopy_cmd(self.udid), text)

    @staticmethod
    def _run_pbcopy(cmd: list[str], text: str = "") -> None:
        # pbcopy is idempotent — re-feeding the same stdin is safe — so retry the transient
        # simctl pasteboard timeout (see `_PBCOPY_*`) rather than fail the whole scenario on it.
        last: subprocess.CalledProcessError | subprocess.TimeoutExpired | None = None
        for attempt in range(_PBCOPY_MAX_ATTEMPTS):
            try:
                subprocess.run(
                    cmd,
                    input=text,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=_PBCOPY_TIMEOUT_S,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                last = exc
                # Only the transient exit-60 timeout (and a Python-side hang, which has no
                # returncode) is worth retrying; a genuine simctl failure — an un-booted device,
                # a bad UDID — won't clear on a re-run, so surface it now rather than after the
                # full backoff budget.
                if (
                    isinstance(exc, subprocess.CalledProcessError)
                    and exc.returncode != _PBCOPY_TIMEOUT_EXIT
                ):
                    raise
                if attempt + 1 < _PBCOPY_MAX_ATTEMPTS:
                    time.sleep(_PBCOPY_RETRY_DELAY_S * (attempt + 1))
            else:
                return
        assert last is not None  # the loop runs at least once, so a failure sets `last`
        raise last

    def get_clipboard(self) -> str:
        # pbpaste returns the pasteboard content on stdout; RunFn already yields stdout.
        return self._run(pbpaste_cmd(self.udid), None)

    def home(self) -> None:
        self._run(home_cmd(self.udid), None)

    def foreground(self, bundle_id: str) -> None:
        self._run(foreground_cmd(self.udid, bundle_id), None)

    def override_status_bar(self, **kwargs: str | int) -> None:
        self._run(status_bar_override_cmd(self.udid, **kwargs), None)

    def clear_status_bar(self) -> None:
        self._run(status_bar_clear_cmd(self.udid), None)

    def push(self, bundle_id: str, payload: dict[str, object]) -> None:
        """Deliver a simulated push: write the APNs payload to a temp file, then push it."""
        with tempfile.NamedTemporaryFile("w", suffix=".apns", delete=False, encoding="utf-8") as f:
            json.dump(payload, f)
            path = f.name
        try:
            self._run(push_cmd(self.udid, bundle_id, path), None)
        finally:
            Path(path).unlink()

    def add_media(self, media_paths: Iterable[str]) -> None:
        """Seed the photo library with each path, one `simctl addmedia` call per path, in order.

        One call per path rather than one call for the whole list: `simctl addmedia` accepts several
        paths at once, but nothing documents the relative order it assigns them, and a caller that
        needs a reproducible grid order (`seed_photos`, `Preconditions`) can only rely on the order
        of *separate* invocations, each landing before the next starts.
        """
        for path in media_paths:
            self._run(addmedia_cmd(self.udid, path), None)

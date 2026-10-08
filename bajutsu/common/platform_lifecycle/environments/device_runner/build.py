"""Build, cache, and look up the per-user signed device runner (BE-0456).

The build is an authoring-side step, never part of a run's verdict: ``bajutsu runner build
--device`` produces the products once, and a run only looks them up. Every input that changes the
signed products — the runner sources, the signing fields, the Xcode build, and for manual signing
the exact certificate and profiles — feeds the cache key, so a stale build is a miss rather than a
silently reused runner. The toolchain sits behind ``Toolchain`` so every rule here is testable on a
host without Xcode.
"""

from __future__ import annotations

import hashlib
import json
import platform
import plistlib
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from xml.parsers.expat import ExpatError

from bajutsu.common.platform_lifecycle.environments import _bundled_runner

from .errors import DeviceRunnerError
from .signing import SigningConfig, describe_lookup, find_signing_file, load_signing
from .staging import PROJECT_NAME, runner_source_root, stage

RUNNER_NAME = "BajutsuRunner.xctestrun"
BUILD_COMMAND = "bajutsu runner build --device"
_CACHE_DIR_NAME = "xcuitest-runner-device"
# Bump when staging or spec rendering changes what a build produces from the same inputs, so an
# entry built by the older logic becomes a miss instead of being reused.
_BUILD_FORMAT = 1
_PARTIAL_MARKER = ".partial-"
# A signing build legitimately runs for many minutes (package resolution plus two signed products),
# so a partial is presumed abandoned only after a full day, never while a concurrent build is live.
_STALE_PARTIAL_AGE_SECONDS = 24 * 60 * 60
# Where Xcode keeps installed profiles: the long-standing location and the one Xcode 16 moved to.
_PROFILE_DIRS = (
    Path("Library/MobileDevice/Provisioning Profiles"),
    Path("Library/Developer/Xcode/UserData/Provisioning Profiles"),
)
# `security find-identity -v -p codesigning` prints `  1) <SHA-1> "<name>"` per valid identity.
_IDENTITY_RE = re.compile(r'^\s*\d+\)\s+([0-9A-F]{40})\s+"(.+)"\s*$', re.MULTILINE)


def _capture(argv: list[str]) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError as exc:
        # The exit status alone says nothing; stderr is where `security` / `xcodebuild` explain.
        detail = (exc.stderr or "").strip()
        raise DeviceRunnerError(
            f"command failed (exit {exc.returncode}): {' '.join(argv)}"
            + (f": {detail}" if detail else "")
        ) from exc
    except OSError as exc:
        raise DeviceRunnerError(f"command failed: {' '.join(argv)}: {exc}") from exc


def _execute(argv: list[str]) -> None:
    # Inherits stdio: an `xcodebuild` signing build runs for minutes and its own log is the
    # diagnostic a user needs when it fails, so it streams rather than being captured.
    try:
        subprocess.run(argv, check=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        raise DeviceRunnerError(f"command failed: {' '.join(argv)}: {exc}") from exc


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Toolchain:
    """The host seams a build and a lookup touch: commands, tool discovery, platform, home, clock."""

    capture: Callable[[list[str]], str] = _capture
    execute: Callable[[list[str]], None] = _execute
    which: Callable[[str], str | None] = shutil.which
    system: Callable[[], str] = platform.system
    home: Callable[[], Path] = Path.home
    now: Callable[[], datetime] = field(default=_utc_now)


@dataclass(frozen=True)
class Profile:
    """The fields of a provisioning profile this module reads."""

    path: Path
    name: str
    uuid: str
    expires: datetime


def read_profile(path: Path) -> Profile:
    """Parse a ``.mobileprovision`` file.

    The file is a CMS envelope around a plain XML property list; slicing that plist out avoids a
    ``security cms -D`` round trip and the host dependency it would carry.

    Raises:
        DeviceRunnerError: The file holds no readable property list.
    """
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise DeviceRunnerError(f"unreadable provisioning profile {path}: {exc}") from exc
    start, end = data.find(b"<?xml"), data.find(b"</plist>")
    if start < 0 or end < 0:
        raise DeviceRunnerError(
            f"unreadable provisioning profile {path}: no embedded property list"
        )
    try:
        info = plistlib.loads(data[start : end + len(b"</plist>")], aware_datetime=True)
        name, uuid, expires = str(info["Name"]), str(info["UUID"]), info["ExpirationDate"]
    except (ValueError, KeyError, TypeError, ExpatError, plistlib.InvalidFileException) as exc:
        raise DeviceRunnerError(f"unreadable provisioning profile {path}: {exc}") from exc
    if not isinstance(expires, datetime):
        raise DeviceRunnerError(
            f"unreadable provisioning profile {path}: ExpirationDate is no date"
        )
    return Profile(path, name, uuid, expires)


def _installed_profiles(toolchain: Toolchain) -> tuple[list[Profile], list[str]]:
    """Every readable installed profile, plus a ``"<path>: <reason>"`` line per unreadable one."""
    profiles: list[Profile] = []
    unreadable: list[str] = []
    for rel in _PROFILE_DIRS:
        directory = toolchain.home() / rel
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.mobileprovision")):
            try:
                profiles.append(read_profile(path))
            except DeviceRunnerError as exc:
                # One corrupt download must not hide the profile the build names; the caller
                # reports these skips when the name then matches nothing.
                unreadable.append(str(exc))
    return profiles, unreadable


def find_profile(name_or_uuid: str, toolchain: Toolchain) -> Profile | None:
    """The installed profile a manual build names, matched by name or UUID like Xcode's specifier.

    The same file copied under both profile directories counts once. Beyond that, more than one
    unexpired profile under one name is ambiguous: Xcode resolves the specifier by its own rules, so
    picking one here could fingerprint a different profile than the build signs with. A renewal
    usually leaves the old profile expired beside the new one, which is not ambiguous. When every
    match has expired, the latest one is returned for the caller to report.

    Raises:
        DeviceRunnerError: Several distinct unexpired profiles share the name.
    """
    profiles, _ = _installed_profiles(toolchain)
    by_digest: dict[str, Profile] = {}
    for profile in profiles:
        if name_or_uuid in (profile.name, profile.uuid):
            by_digest.setdefault(hashlib.sha256(profile.path.read_bytes()).hexdigest(), profile)
    matches = list(by_digest.values())
    now = toolchain.now()
    valid = [p for p in matches if p.expires > now]
    if len(valid) > 1:
        uuids = ", ".join(sorted(p.uuid for p in valid))
        raise DeviceRunnerError(
            f"provisioning profile {name_or_uuid!r} is ambiguous (unexpired UUIDs {uuids}); "
            "name one by its UUID in the signing file"
        )
    if valid:
        return valid[0]
    return max(matches, key=lambda p: (p.expires, str(p.path)), default=None)


def identity_hash(identity: str, toolchain: Toolchain) -> str | None:
    """The SHA-1 of the one valid codesigning identity named *identity* (by name or hash), or ``None``.

    Raises:
        DeviceRunnerError: More than one distinct certificate carries that name; ``codesign`` would
            reject the build as ambiguous anyway, so it fails here with the hashes to choose from.
    """
    listing = toolchain.capture(["security", "find-identity", "-v", "-p", "codesigning"])
    hashes = sorted({sha for sha, name in _IDENTITY_RE.findall(listing) if identity in (name, sha)})
    if len(hashes) > 1:
        raise DeviceRunnerError(
            f"signing identity {identity!r} is ambiguous (certificates {', '.join(hashes)}); "
            "name one by its hash in the signing file"
        )
    return hashes[0] if hashes else None


def manual_fingerprints(signing: SigningConfig, toolchain: Toolchain) -> dict[str, str]:
    """The certificate hash and profile digests a manual build signs with; empty for automatic.

    A certificate or profile renewed under the same name changes these, so it changes the cache key.

    Raises:
        DeviceRunnerError: The identity or a named profile is not installed.
    """
    manual = signing.manual_signing
    if manual is None:
        return {}
    problems = _manual_problems(signing, toolchain)
    if problems:
        raise DeviceRunnerError("; ".join(problems))
    fingerprints = {"identity": identity_hash(manual.identity, toolchain) or ""}
    for role, name in (("host", manual.host_profile()), ("runner", manual.runner_profile())):
        profile = find_profile(name, toolchain)
        assert profile is not None  # guaranteed by _manual_problems
        fingerprints[f"profile:{role}"] = hashlib.sha256(profile.path.read_bytes()).hexdigest()
    return fingerprints


def _manual_problems(signing: SigningConfig, toolchain: Toolchain) -> list[str]:
    manual = signing.manual_signing
    if manual is None:
        return []
    problems: list[str] = []
    if identity_hash(manual.identity, toolchain) is None:
        problems.append(
            f"signing identity {manual.identity!r} is not in `security find-identity -v -p "
            "codesigning` — install its certificate and private key in the login Keychain"
        )
    _, unreadable = _installed_profiles(toolchain)
    for name in dict.fromkeys((manual.host_profile(), manual.runner_profile())):
        try:
            profile = find_profile(name, toolchain)
        except DeviceRunnerError as exc:
            problems.append(str(exc))
            continue
        if profile is None:
            skipped = f" ({len(unreadable)} unreadable skipped: {'; '.join(unreadable)})"
            problems.append(
                f"provisioning profile {name!r} is not installed — download it in Xcode "
                f"(Settings > Accounts) or copy it into ~/{_PROFILE_DIRS[0]}"
                + (skipped if unreadable else "")
            )
        elif profile.expires <= toolchain.now():
            problems.append(
                f"provisioning profile {name!r} expired on {profile.expires:%Y-%m-%d} — renew it "
                "in the Apple Developer portal and install the new one"
            )
    return problems


def preflight(signing: SigningConfig, toolchain: Toolchain) -> None:
    """Check every host prerequisite before any ``xcodebuild`` call, reporting all gaps at once.

    Raises:
        DeviceRunnerError: Naming each missing item and its fix.
    """
    if toolchain.system() != "Darwin":
        raise DeviceRunnerError("a device runner build needs macOS with Xcode")
    problems: list[str] = []
    if toolchain.which("xcodebuild") is None:
        problems.append("xcodebuild is missing — install Xcode")
    if toolchain.which("xcodegen") is None:
        problems.append("xcodegen is missing — run 'make deps' or 'brew install xcodegen'")
    if not problems:
        problems.extend(_manual_problems(signing, toolchain))
    if problems:
        raise DeviceRunnerError("cannot build the device runner: " + "; ".join(problems))


def xcode_version(toolchain: Toolchain) -> str:
    """The ``xcodebuild -version`` text, whose build number pins the toolchain in the cache key."""
    return toolchain.capture(["xcodebuild", "-version"]).strip()


def cache_key(
    signing: SigningConfig, *, source_hash: str, xcode: str, fingerprints: Mapping[str, str]
) -> str:
    """A short digest of every input that changes the signed products.

    The manual block of an automatic-signing file is inert, so it is left out: editing it cannot
    change what the build produces and must not invalidate the cache. The XcodeGen version is left
    out too: the key is recomputed on every run, which must not require XcodeGen, and the products
    are pinned by the Xcode build and ``_BUILD_FORMAT`` instead.
    """
    manual = signing.manual_signing
    payload = {
        "format": _BUILD_FORMAT,
        "sources": source_hash,
        "teamId": signing.team_id,
        "signing": signing.signing,
        "host": signing.host_bundle_id,
        "uitests": signing.uitests_bundle_id,
        "manual": manual.model_dump(mode="json") if manual else None,
        "xcode": xcode,
        "assets": dict(sorted(fingerprints.items())),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def compute_key(signing: SigningConfig, source_root: Path, toolchain: Toolchain) -> str:
    """The cache key for *signing* against the pristine sources at *source_root*."""
    try:
        source_hash = _bundled_runner.source_hash(root=source_root)
    except FileNotFoundError as exc:
        raise DeviceRunnerError(f"incomplete runner sources at {source_root}: {exc}") from exc
    return cache_key(
        signing,
        source_hash=source_hash,
        xcode=xcode_version(toolchain),
        fingerprints=manual_fingerprints(signing, toolchain),
    )


def default_cache_root() -> Path:
    """``<Bajutsu cache>/xcuitest-runner-device``, beside the Simulator runner's cache."""
    return _bundled_runner.bajutsu_cache_root() / _CACHE_DIR_NAME


def cached_xctestrun(key: str, cache_root: Path) -> Path | None:
    """The published ``.xctestrun`` for *key*, or ``None`` when no complete build exists.

    The file is the last thing a build puts in place, so a directory without it — an interrupted
    build — is a miss, never a half-populated hit.
    """
    runner = cache_root / key / "Products" / RUNNER_NAME
    return runner if runner.is_file() else None


def expired_profiles(products: Path, now: datetime) -> list[Profile]:
    """The profiles embedded in *products* that have expired by *now*."""
    profiles = (read_profile(p) for p in sorted(products.rglob("embedded.mobileprovision")))
    return [profile for profile in profiles if profile.expires <= now]


def xcodegen_argv(spec: Path) -> list[str]:
    """Generate the Xcode project beside the staged *spec*."""
    return ["xcodegen", "generate", "--spec", str(spec), "--project", str(spec.parent)]


def xcodebuild_argv(project: Path, derived_data: Path, signing: SigningConfig) -> list[str]:
    """``build-for-testing`` for a generic iOS device.

    ``-skipPackagePluginValidation`` lets the ``OpenAPIGenerator`` build plugin run without an
    interactive trust prompt. ``-allowProvisioningUpdates`` lets Xcode mint profiles, which only
    automatic signing wants; a manual build must use exactly the profiles it names.
    """
    argv = [
        "xcodebuild",
        "build-for-testing",
        "-project",
        str(project),
        "-scheme",
        PROJECT_NAME,
        "-destination",
        "generic/platform=iOS",
        "-derivedDataPath",
        str(derived_data),
        "-skipPackagePluginValidation",
    ]
    if signing.signing == "automatic":
        argv.append("-allowProvisioningUpdates")
    return argv


def _sweep_partials(cache_root: Path) -> None:
    now = time.time()
    for stale in cache_root.glob(f"*{_PARTIAL_MARKER}*"):
        try:
            age = now - stale.stat().st_mtime
        except OSError:
            continue  # a concurrent sweep already removed it
        if age > _STALE_PARTIAL_AGE_SECONDS:
            shutil.rmtree(stale, ignore_errors=True)


def _publish(staged: Path, final: Path, *, replace: bool) -> None:
    """Rename *staged* onto *final*; a concurrent winner's complete directory is kept instead."""
    if replace and final.exists():
        # A directory cannot be renamed onto a non-empty one, so move the old build aside first;
        # its name carries the partial marker, so a crash before the rmtree leaves it sweepable.
        retired = Path(tempfile.mkdtemp(dir=final.parent, prefix=f"{final.name}{_PARTIAL_MARKER}"))
        final.replace(retired / "old")
        shutil.rmtree(retired, ignore_errors=True)
    try:
        staged.replace(final)
    except OSError:
        # Keeping a concurrent winner is right for a fresh build, but a replacement that fails must
        # not hand back the stale build it was meant to supersede.
        if not replace and (final / "Products" / RUNNER_NAME).is_file():
            return
        raise


def build_device_runner(
    signing: SigningConfig,
    *,
    source_root: Path,
    toolchain: Toolchain | None = None,
    cache_root: Path | None = None,
    force: bool = False,
) -> Path:
    """Build the signed device runner for *signing*, or reuse a complete cached build.

    The sources are staged into a scratch tree beside the cache entry, so the checkout is never
    touched, and the products are published by one rename once ``BajutsuRunner.xctestrun`` exists.
    A cached build whose embedded profile has expired is rebuilt as though *force* were set.

    Args:
        signing: The validated signing file.
        source_root: The pristine runner sources (``runner_source_root()``).
        toolchain: Host seams; defaults to the real host.
        cache_root: Override the cache location (tests inject a ``tmp_path``).
        force: Rebuild even when a matching complete build is cached.

    Returns:
        The cached ``BajutsuRunner.xctestrun``; its test bundles sit beside it in ``Products/``.

    Raises:
        DeviceRunnerError: A prerequisite is missing, a tool fails, or the build yields no runner.
    """
    toolchain = toolchain or Toolchain()
    preflight(signing, toolchain)
    root = cache_root or default_cache_root()
    key = compute_key(signing, source_root, toolchain)
    cached = cached_xctestrun(key, root)
    if cached is not None and not force:
        try:
            stale = bool(expired_profiles(cached.parent, toolchain.now()))
        except DeviceRunnerError:
            stale = True  # an unreadable embedded profile must not block the rebuild that fixes it
        if not stale:
            return cached
        force = True

    root.mkdir(parents=True, exist_ok=True)
    _sweep_partials(root)
    tmp = Path(tempfile.mkdtemp(dir=root, prefix=f"{key}{_PARTIAL_MARKER}"))
    try:
        spec = stage(source_root, tmp / "src", signing)
        toolchain.execute(xcodegen_argv(spec))
        toolchain.execute(
            xcodebuild_argv(spec.parent / f"{PROJECT_NAME}.xcodeproj", tmp / "dd", signing)
        )
        products = tmp / "dd" / "Build" / "Products"
        built = sorted(p for p in products.glob("*.xctestrun") if p.name != RUNNER_NAME)
        if len(built) != 1:
            raise DeviceRunnerError(
                f"expected exactly one .xctestrun in {products}, found {len(built)}"
            )
        shutil.copy2(built[0], products / RUNNER_NAME)
        publish = tmp / "publish"
        publish.mkdir()
        products.replace(publish / "Products")
        final = root / key
        # A directory left without its `.xctestrun` (an interrupted delete) is a miss, yet it would
        # block the rename forever, so it is replaced like a stale build. A complete directory a
        # concurrent build published meanwhile is kept instead.
        if force or cached_xctestrun(key, root) is None:
            _publish(publish, final, replace=force or final.exists())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    runner = cached_xctestrun(key, root)
    if runner is None:
        raise DeviceRunnerError(f"the device runner build did not publish {root / key}")
    return runner


def resolve_device_runner(
    *,
    env: Mapping[str, str] | None = None,
    toolchain: Toolchain | None = None,
    cache_root: Path | None = None,
) -> Path:
    """The cached device runner a run with no ``xcuitest.testRunner`` uses.

    Never builds: a signing build is slow, can raise a Keychain prompt, and can register identifiers
    with Apple, none of which belongs in the middle of a pooled run. A miss names the exact command
    that fills the cache instead.

    Raises:
        DeviceRunnerError: No signing file, no runner sources, no matching build, or an expired or
            unreadable one; the last two name the ``--force`` rebuild.
    """
    toolchain = toolchain or Toolchain()
    path = find_signing_file(None, env)
    if path is None:
        raise DeviceRunnerError(
            "xcuitest.deviceType: device with no xcuitest.testRunner needs a signing file at "
            f"{describe_lookup(env)} (then run `{BUILD_COMMAND}`)"
        )
    signing = load_signing(path)
    source_root = runner_source_root()
    if source_root is None:
        raise DeviceRunnerError(
            "this install ships no XCUITest runner sources, so no device runner can be keyed; "
            "set xcuitest.testRunner to a signed runner"
        )
    key = compute_key(signing, source_root, toolchain)
    runner = cached_xctestrun(key, cache_root or default_cache_root())
    if runner is None:
        raise DeviceRunnerError(
            f"no device runner is built for signing file {path} with the current sources and "
            f"Xcode; run `{BUILD_COMMAND}`"
        )
    try:
        expired = expired_profiles(runner.parent, toolchain.now())
    except DeviceRunnerError as exc:
        raise DeviceRunnerError(
            f"{exc}; rebuild the device runner with `{BUILD_COMMAND} --force`"
        ) from exc
    if expired:
        names = ", ".join(sorted({p.name for p in expired}))
        raise DeviceRunnerError(
            f"the cached device runner's provisioning profile has expired ({names}); "
            f"rebuild it with `{BUILD_COMMAND} --force`"
        )
    return runner

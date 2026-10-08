"""Building, caching, and resolving the signed device runner (BE-0456), with a fake toolchain."""

from __future__ import annotations

import plistlib
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bajutsu.common.platform_lifecycle.environments import _bundled_runner
from bajutsu.common.platform_lifecycle.environments.device_runner import build, staging
from bajutsu.common.platform_lifecycle.environments.device_runner.errors import DeviceRunnerError
from bajutsu.common.platform_lifecycle.environments.device_runner.signing import (
    SIGNING_FILE_ENV,
    SigningConfig,
)

_REPO = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 1, tzinfo=UTC)
SHA_A = "A" * 40
SHA_B = "B" * 40

AUTOMATIC = SigningConfig.model_validate({"bundleIdPrefix": "com.acme", "teamId": "TEAM1"})
MANUAL = SigningConfig.model_validate(
    {
        "bundleIdPrefix": "com.acme",
        "teamId": "TEAM1",
        "signing": "manual",
        "manual": {"identity": "Apple Development: Jane", "profiles": {"host": "H", "runner": "R"}},
    }
)


def _profile_bytes(name: str, uuid: str, expires: datetime) -> bytes:
    body = plistlib.dumps({"Name": name, "UUID": uuid, "ExpirationDate": expires})
    # A real profile wraps the plist in a binary CMS envelope; the parser must slice it out.
    return b"\x30\x82CMS-header" + body + b"\x00signature"


def _write_sources(root: Path) -> None:
    for rel in _bundled_runner.HASH_SOURCE_PATHS:
        target = root / rel
        if rel.endswith((".swift", ".yml")):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text((_REPO / rel).read_text())
        else:
            target.mkdir(parents=True, exist_ok=True)
            (target / "File.swift").write_text(f"// {rel}\n")


class FakeHost:
    """Records commands; ``xcodebuild build-for-testing`` lays out the products a real build makes."""

    def __init__(
        self, home: Path, *, identities: str = "", expires: datetime | None = None
    ) -> None:
        self.home = home
        self.identities = identities
        self.expires = expires or NOW + timedelta(days=300)
        self.xcode = "Xcode 26.0\nBuild version 17A324"
        self.executed: list[list[str]] = []
        self.tools = {"xcodebuild", "xcodegen"}
        self.system = "Darwin"

    def capture(self, argv: list[str]) -> str:
        if argv[:2] == ["xcodebuild", "-version"]:
            return self.xcode
        if argv[0] == "security":
            return self.identities
        raise AssertionError(f"unexpected capture: {argv}")

    def execute(self, argv: list[str]) -> None:
        self.executed.append(argv)
        if argv[:2] == ["xcodebuild", "build-for-testing"]:
            dd = Path(argv[argv.index("-derivedDataPath") + 1])
            products = dd / "Build" / "Products"
            app = products / "Debug-iphoneos" / "BajutsuRunnerHost.app"
            app.mkdir(parents=True)
            (app / "embedded.mobileprovision").write_bytes(
                _profile_bytes("Auto", "U1", self.expires)
            )
            (products / "BajutsuRunner_iphoneos26.0-arm64.xctestrun").write_text("<plist/>")

    def toolchain(self) -> build.Toolchain:
        return build.Toolchain(
            capture=self.capture,
            execute=self.execute,
            which=lambda tool: f"/usr/bin/{tool}" if tool in self.tools else None,
            system=lambda: self.system,
            home=lambda: self.home,
            now=lambda: NOW,
        )


def _install_profile(home: Path, filename: str, name: str, uuid: str, expires: datetime) -> Path:
    directory = home / "Library/MobileDevice/Provisioning Profiles"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_bytes(_profile_bytes(name, uuid, expires))
    return path


def _manual_host(home: Path) -> FakeHost:
    _install_profile(home, "h.mobileprovision", "H", "UH", NOW + timedelta(days=100))
    _install_profile(home, "r.mobileprovision", "R", "UR", NOW + timedelta(days=100))
    return FakeHost(
        home, identities=f'  1) {SHA_A} "Apple Development: Jane"\n     1 valid identities found\n'
    )


@pytest.fixture
def sources(tmp_path: Path) -> Path:
    root = tmp_path / "sources"
    _write_sources(root)
    return root


def test_read_profile_slices_the_plist_out_of_the_envelope(tmp_path: Path) -> None:
    path = _install_profile(tmp_path, "p.mobileprovision", "P", "U", NOW)
    profile = build.read_profile(path)
    assert (profile.name, profile.uuid, profile.expires) == ("P", "U", NOW)


def test_read_profile_rejects_a_file_without_a_plist(tmp_path: Path) -> None:
    bad = tmp_path / "bad.mobileprovision"
    bad.write_bytes(b"garbage")
    with pytest.raises(DeviceRunnerError, match="unreadable provisioning profile"):
        build.read_profile(bad)


def test_find_profile_matches_name_or_uuid_and_skips_an_expired_predecessor(tmp_path: Path) -> None:
    _install_profile(tmp_path, "old.mobileprovision", "P", "U-OLD", NOW - timedelta(days=1))
    _install_profile(tmp_path, "new.mobileprovision", "P", "U-NEW", NOW + timedelta(days=9))
    (tmp_path / "Library/MobileDevice/Provisioning Profiles/broken.mobileprovision").write_bytes(
        b"x"
    )
    tc = FakeHost(tmp_path).toolchain()
    found = build.find_profile("P", tc)
    assert found is not None and found.uuid == "U-NEW"
    by_uuid = build.find_profile("U-OLD", tc)
    assert by_uuid is not None and by_uuid.uuid == "U-OLD"
    assert build.find_profile("missing", tc) is None


def test_two_unexpired_profiles_under_one_name_are_ambiguous(tmp_path: Path) -> None:
    _install_profile(tmp_path, "a.mobileprovision", "P", "U-A", NOW + timedelta(days=1))
    _install_profile(tmp_path, "b.mobileprovision", "P", "U-B", NOW + timedelta(days=9))
    with pytest.raises(DeviceRunnerError, match=r"ambiguous.*U-A, U-B.*UUID"):
        build.find_profile("P", FakeHost(tmp_path).toolchain())


def test_the_same_profile_in_both_directories_is_not_ambiguous(tmp_path: Path) -> None:
    path = _install_profile(tmp_path, "p.mobileprovision", "P", "U", NOW + timedelta(days=9))
    xcode16 = tmp_path / "Library/Developer/Xcode/UserData/Provisioning Profiles"
    xcode16.mkdir(parents=True)
    (xcode16 / "p.mobileprovision").write_bytes(path.read_bytes())
    found = build.find_profile("P", FakeHost(tmp_path).toolchain())
    assert found is not None and found.uuid == "U"


def test_all_expired_returns_the_latest_for_the_caller_to_report(tmp_path: Path) -> None:
    _install_profile(tmp_path, "a.mobileprovision", "P", "U-A", NOW - timedelta(days=9))
    _install_profile(tmp_path, "b.mobileprovision", "P", "U-B", NOW - timedelta(days=1))
    found = build.find_profile("P", FakeHost(tmp_path).toolchain())
    assert found is not None and found.uuid == "U-B"


def test_read_profile_rejects_malformed_xml_and_a_non_date_expiry(tmp_path: Path) -> None:
    bad_xml = tmp_path / "bad.mobileprovision"
    bad_xml.write_bytes(b'junk<?xml version="1.0"?><plist><dict><key>Name</dict></plist>junk')
    with pytest.raises(DeviceRunnerError, match="unreadable"):
        build.read_profile(bad_xml)
    no_date = tmp_path / "nodate.mobileprovision"
    no_date.write_bytes(plistlib.dumps({"Name": "P", "UUID": "U", "ExpirationDate": "soon"}))
    with pytest.raises(DeviceRunnerError, match="no date"):
        build.read_profile(no_date)
    with pytest.raises(DeviceRunnerError, match="unreadable"):
        build.read_profile(tmp_path / "missing.mobileprovision")


def test_preflight_reports_skipped_unreadable_profiles_and_an_expired_one(tmp_path: Path) -> None:
    host = _manual_host(tmp_path)
    _install_profile(tmp_path, "h.mobileprovision", "H", "UH", NOW - timedelta(days=1))
    directory = tmp_path / "Library/MobileDevice/Provisioning Profiles"
    (directory / "r.mobileprovision").write_bytes(b'<?xml version="1.0"?><plist><broken</plist>')
    with pytest.raises(DeviceRunnerError) as info:
        build.preflight(MANUAL, host.toolchain())
    message = str(info.value)
    assert "profile 'H' expired on" in message
    assert "profile 'R' is not installed" in message
    assert "1 unreadable skipped" in message and "r.mobileprovision" in message


def test_capture_reports_stderr_and_a_missing_tool() -> None:
    script = "import sys; sys.stderr.write('boom'); sys.exit(3)"
    with pytest.raises(DeviceRunnerError, match=r"exit 3.*boom"):
        build._capture([sys.executable, "-c", script])
    with pytest.raises(DeviceRunnerError, match="command failed"):
        build._capture(["/nonexistent/tool"])
    assert build._capture([sys.executable, "-c", "print('ok')"]) == "ok\n"


def test_identity_hash_matches_by_name_or_hash_and_rejects_ambiguity(tmp_path: Path) -> None:
    host = FakeHost(
        tmp_path, identities=f'  1) {SHA_A} "Jane"\n  2) {SHA_B} "Jane"\n  3) {SHA_A} "Jane"\n'
    )
    tc = host.toolchain()
    with pytest.raises(DeviceRunnerError, match="ambiguous"):
        build.identity_hash("Jane", tc)
    assert build.identity_hash(SHA_B, tc) == SHA_B
    assert build.identity_hash("Nobody", tc) is None


def test_preflight_reports_every_missing_tool_at_once(tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    host.tools = set()
    with pytest.raises(DeviceRunnerError) as info:
        build.preflight(AUTOMATIC, host.toolchain())
    assert "xcodebuild is missing" in str(info.value)
    assert "xcodegen is missing" in str(info.value)


def test_preflight_requires_macos(tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    host.system = "Linux"
    with pytest.raises(DeviceRunnerError, match="needs macOS"):
        build.preflight(AUTOMATIC, host.toolchain())


def test_preflight_names_a_missing_identity_and_profile(tmp_path: Path) -> None:
    host = FakeHost(tmp_path)
    _install_profile(tmp_path, "h.mobileprovision", "H", "UH", NOW + timedelta(days=1))
    with pytest.raises(DeviceRunnerError) as info:
        build.preflight(MANUAL, host.toolchain())
    message = str(info.value)
    assert "'Apple Development: Jane' is not in `security find-identity" in message
    assert "profile 'R' is not installed" in message
    assert "profile 'H'" not in message


def test_xcodebuild_argv_allows_provisioning_updates_only_for_automatic(tmp_path: Path) -> None:
    auto = build.xcodebuild_argv(tmp_path / "P.xcodeproj", tmp_path / "dd", AUTOMATIC)
    manual = build.xcodebuild_argv(tmp_path / "P.xcodeproj", tmp_path / "dd", MANUAL)
    assert auto[:2] == ["xcodebuild", "build-for-testing"]
    assert "generic/platform=iOS" in auto and "-skipPackagePluginValidation" in auto
    assert "-allowProvisioningUpdates" in auto
    assert manual == [
        "xcodebuild",
        "build-for-testing",
        "-project",
        str(tmp_path / "P.xcodeproj"),
        "-scheme",
        "BajutsuRunner",
        "-destination",
        "generic/platform=iOS",
        "-derivedDataPath",
        str(tmp_path / "dd"),
        "-skipPackagePluginValidation",
    ]
    spec = tmp_path / "BajutsuKit/Runner/project.yml"
    assert build.xcodegen_argv(spec) == [
        "xcodegen",
        "generate",
        "--spec",
        str(spec),
        "--project",
        str(spec.parent),
    ]


def test_cache_key_is_stable_and_sensitive_to_every_input() -> None:
    base = build.cache_key(AUTOMATIC, source_hash="s", xcode="x", fingerprints={})
    assert base == build.cache_key(AUTOMATIC, source_hash="s", xcode="x", fingerprints={})
    other_team = SigningConfig.model_validate({"bundleIdPrefix": "com.acme", "teamId": "TEAM2"})
    other_ids = SigningConfig.model_validate(
        {"bundleIds": {"host": "a.h", "uitests": "a.u"}, "teamId": "TEAM1"}
    )
    variants = {
        build.cache_key(AUTOMATIC, source_hash="s2", xcode="x", fingerprints={}),
        build.cache_key(AUTOMATIC, source_hash="s", xcode="x2", fingerprints={}),
        build.cache_key(other_team, source_hash="s", xcode="x", fingerprints={}),
        build.cache_key(other_ids, source_hash="s", xcode="x", fingerprints={}),
        build.cache_key(MANUAL, source_hash="s", xcode="x", fingerprints={"identity": SHA_A}),
    }
    assert base not in variants and len(variants) == 5


def test_inert_manual_block_does_not_change_an_automatic_key() -> None:
    with_block = SigningConfig.model_validate(
        {
            "bundleIdPrefix": "com.acme",
            "teamId": "TEAM1",
            "manual": {"identity": "I", "profile": "P"},
        }
    )
    assert build.cache_key(with_block, source_hash="s", xcode="x", fingerprints={}) == (
        build.cache_key(AUTOMATIC, source_hash="s", xcode="x", fingerprints={})
    )


def test_checkout_and_wheel_layouts_share_a_key(tmp_path: Path, sources: Path) -> None:
    wheel = tmp_path / "wheel"
    staging.copy_runner_sources(sources, wheel)
    tc = FakeHost(tmp_path).toolchain()
    assert build.compute_key(AUTOMATIC, sources, tc) == build.compute_key(AUTOMATIC, wheel, tc)


def test_a_renewed_profile_under_the_same_name_changes_the_key(
    tmp_path: Path, sources: Path
) -> None:
    host = _manual_host(tmp_path / "home")
    before = build.compute_key(MANUAL, sources, host.toolchain())
    # A renewal downloaded over the old file: same name, new contents.
    _install_profile(tmp_path / "home", "r.mobileprovision", "R", "UR2", NOW + timedelta(days=400))
    assert build.compute_key(MANUAL, sources, host.toolchain()) != before


def test_build_publishes_a_complete_directory_and_reuses_it(tmp_path: Path, sources: Path) -> None:
    host = FakeHost(tmp_path)
    cache = tmp_path / "cache"
    runner = build.build_device_runner(
        AUTOMATIC, source_root=sources, toolchain=host.toolchain(), cache_root=cache
    )
    assert runner.name == build.RUNNER_NAME and runner.is_file()
    assert runner.parent.name == "Products"
    assert [argv[0] for argv in host.executed] == ["xcodegen", "xcodebuild"]
    # Only the published entry remains: the scratch tree and derived data are gone.
    assert [p.name for p in cache.iterdir()] == [runner.parent.parent.name]
    # The checkout is never touched.
    assert ".testTarget" in (sources / "Package.swift").read_text()

    again = build.build_device_runner(
        AUTOMATIC, source_root=sources, toolchain=host.toolchain(), cache_root=cache
    )
    assert again == runner
    assert len(host.executed) == 2  # a cache hit runs no tool


def test_force_rebuilds_over_a_cached_entry(tmp_path: Path, sources: Path) -> None:
    host = FakeHost(tmp_path)
    cache = tmp_path / "cache"
    first = build.build_device_runner(
        AUTOMATIC, source_root=sources, toolchain=host.toolchain(), cache_root=cache
    )
    second = build.build_device_runner(
        AUTOMATIC, source_root=sources, toolchain=host.toolchain(), cache_root=cache, force=True
    )
    assert second == first and second.is_file()
    assert len(host.executed) == 4
    assert [p.name for p in cache.iterdir()] == [first.parent.parent.name]


def test_an_expired_cached_build_is_rebuilt(tmp_path: Path, sources: Path) -> None:
    host = FakeHost(tmp_path, expires=NOW - timedelta(days=1))
    cache = tmp_path / "cache"
    build.build_device_runner(
        AUTOMATIC, source_root=sources, toolchain=host.toolchain(), cache_root=cache
    )
    host.expires = NOW + timedelta(days=30)
    runner = build.build_device_runner(
        AUTOMATIC, source_root=sources, toolchain=host.toolchain(), cache_root=cache
    )
    assert len(host.executed) == 4
    assert not build.expired_profiles(runner.parent, NOW)


def test_a_build_without_an_xctestrun_publishes_nothing(tmp_path: Path, sources: Path) -> None:
    host = FakeHost(tmp_path)
    host.execute = host.executed.append  # type: ignore[method-assign,assignment]
    cache = tmp_path / "cache"
    with pytest.raises(DeviceRunnerError, match=r"exactly one \.xctestrun"):
        build.build_device_runner(
            AUTOMATIC, source_root=sources, toolchain=host.toolchain(), cache_root=cache
        )
    assert list(cache.iterdir()) == []


def test_an_incomplete_cache_directory_is_rebuilt_without_force(
    tmp_path: Path, sources: Path
) -> None:
    host = FakeHost(tmp_path)
    cache = tmp_path / "cache"
    key = build.compute_key(AUTOMATIC, sources, host.toolchain())
    (cache / key / "Products").mkdir(parents=True)
    runner = build.build_device_runner(
        AUTOMATIC, source_root=sources, toolchain=host.toolchain(), cache_root=cache
    )
    assert runner == cache / key / "Products" / build.RUNNER_NAME and runner.is_file()


def test_an_unreadable_embedded_profile_triggers_a_rebuild(tmp_path: Path, sources: Path) -> None:
    host = FakeHost(tmp_path)
    cache = tmp_path / "cache"
    runner = build.build_device_runner(
        AUTOMATIC, source_root=sources, toolchain=host.toolchain(), cache_root=cache
    )
    next(runner.parent.rglob("embedded.mobileprovision")).write_bytes(b"corrupt")
    build.build_device_runner(
        AUTOMATIC, source_root=sources, toolchain=host.toolchain(), cache_root=cache
    )
    assert len(host.executed) == 4
    assert not build.expired_profiles(runner.parent, NOW)


def test_incomplete_runner_sources_fail_as_a_device_runner_error(
    tmp_path: Path, sources: Path
) -> None:
    shutil.rmtree(sources / "BajutsuKit" / "Runner" / "Host")
    with pytest.raises(DeviceRunnerError, match="incomplete runner sources"):
        build.compute_key(AUTOMATIC, sources, FakeHost(tmp_path).toolchain())


def test_an_incomplete_directory_is_a_miss(tmp_path: Path) -> None:
    (tmp_path / "KEY" / "Products").mkdir(parents=True)
    assert build.cached_xctestrun("KEY", tmp_path) is None
    (tmp_path / "KEY" / "Products" / build.RUNNER_NAME).write_text("x")
    assert (
        build.cached_xctestrun("KEY", tmp_path) == tmp_path / "KEY" / "Products" / build.RUNNER_NAME
    )


def test_a_losing_concurrent_publish_keeps_the_winner(tmp_path: Path) -> None:
    final = tmp_path / "KEY"
    (final / "Products").mkdir(parents=True)
    (final / "Products" / build.RUNNER_NAME).write_text("winner")
    staged = tmp_path / "staged"
    (staged / "Products").mkdir(parents=True)
    (staged / "Products" / build.RUNNER_NAME).write_text("loser")
    build._publish(staged, final, replace=False)
    assert (final / "Products" / build.RUNNER_NAME).read_text() == "winner"


def test_a_failed_replacement_raises_rather_than_keeping_the_stale_build(tmp_path: Path) -> None:
    final = tmp_path / "KEY"
    (final / "Products").mkdir(parents=True)
    (final / "Products" / build.RUNNER_NAME).write_text("stale")
    staged = tmp_path / "missing-staged"  # the rename fails: nothing to move
    with pytest.raises(OSError, match="missing-staged"):
        build._publish(staged, final, replace=True)


def test_a_failed_publish_without_a_winner_raises(tmp_path: Path) -> None:
    final = tmp_path / "KEY"
    (final / "junk").mkdir(parents=True)
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "file").write_text("x")
    with pytest.raises(OSError, match="KEY"):
        build._publish(staged, final, replace=False)


def test_stale_partials_are_swept_but_fresh_ones_kept(tmp_path: Path) -> None:
    import os

    old = tmp_path / f"KEY{build._PARTIAL_MARKER}old"
    fresh = tmp_path / f"KEY{build._PARTIAL_MARKER}fresh"
    old.mkdir()
    fresh.mkdir()
    os.utime(old, (0, 0))
    build._sweep_partials(tmp_path)
    assert not old.exists() and fresh.exists()


def _signing_env(tmp_path: Path, text: str) -> dict[str, str]:
    path = tmp_path / "signing.yaml"
    path.write_text(text)
    return {SIGNING_FILE_ENV: str(path), "XDG_CONFIG_HOME": str(tmp_path / "xdg")}


def test_resolve_finds_the_cached_build(
    tmp_path: Path, sources: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build, "runner_source_root", lambda: sources)
    host = FakeHost(tmp_path)
    cache = tmp_path / "cache"
    built = build.build_device_runner(
        AUTOMATIC, source_root=sources, toolchain=host.toolchain(), cache_root=cache
    )
    env = _signing_env(tmp_path, "bundleIdPrefix: com.acme\nteamId: TEAM1\n")
    assert (
        build.resolve_device_runner(env=env, toolchain=host.toolchain(), cache_root=cache) == built
    )


def test_resolve_without_a_signing_file_names_both_locations(tmp_path: Path) -> None:
    env = {"XDG_CONFIG_HOME": str(tmp_path)}
    with pytest.raises(DeviceRunnerError) as info:
        build.resolve_device_runner(env=env, toolchain=FakeHost(tmp_path).toolchain())
    assert SIGNING_FILE_ENV in str(info.value)
    assert str(tmp_path / "bajutsu" / "signing.yaml") in str(info.value)


def test_resolve_without_a_build_names_the_command(
    tmp_path: Path, sources: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build, "runner_source_root", lambda: sources)
    env = _signing_env(tmp_path, "bundleIdPrefix: com.acme\nteamId: TEAM1\n")
    with pytest.raises(DeviceRunnerError, match="run `bajutsu runner build --device`"):
        build.resolve_device_runner(
            env=env, toolchain=FakeHost(tmp_path).toolchain(), cache_root=tmp_path / "cache"
        )


def test_resolve_without_runner_sources_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build, "runner_source_root", lambda: None)
    env = _signing_env(tmp_path, "bundleIdPrefix: com.acme\nteamId: TEAM1\n")
    with pytest.raises(DeviceRunnerError, match="ships no XCUITest runner sources"):
        build.resolve_device_runner(env=env, toolchain=FakeHost(tmp_path).toolchain())


def test_resolve_names_the_force_rebuild_for_an_unreadable_embedded_profile(
    tmp_path: Path, sources: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build, "runner_source_root", lambda: sources)
    host = FakeHost(tmp_path)
    cache = tmp_path / "cache"
    runner = build.build_device_runner(
        AUTOMATIC, source_root=sources, toolchain=host.toolchain(), cache_root=cache
    )
    next(runner.parent.rglob("embedded.mobileprovision")).write_bytes(b"corrupt")
    env = _signing_env(tmp_path, "bundleIdPrefix: com.acme\nteamId: TEAM1\n")
    with pytest.raises(DeviceRunnerError, match=r"unreadable.*--force"):
        build.resolve_device_runner(env=env, toolchain=host.toolchain(), cache_root=cache)


def test_resolve_rejects_an_expired_build_with_the_force_hint(
    tmp_path: Path, sources: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build, "runner_source_root", lambda: sources)
    host = FakeHost(tmp_path)
    cache = tmp_path / "cache"
    runner = build.build_device_runner(
        AUTOMATIC, source_root=sources, toolchain=host.toolchain(), cache_root=cache
    )
    embedded = next(runner.parent.rglob("embedded.mobileprovision"))
    embedded.write_bytes(_profile_bytes("Auto", "U1", NOW - timedelta(seconds=1)))
    env = _signing_env(tmp_path, "bundleIdPrefix: com.acme\nteamId: TEAM1\n")
    with pytest.raises(DeviceRunnerError, match="--force"):
        build.resolve_device_runner(env=env, toolchain=host.toolchain(), cache_root=cache)


def test_manual_build_runs_with_the_installed_assets(tmp_path: Path, sources: Path) -> None:
    host = _manual_host(tmp_path / "home")
    runner = build.build_device_runner(
        MANUAL, source_root=sources, toolchain=host.toolchain(), cache_root=tmp_path / "cache"
    )
    assert runner.is_file()
    assert "-allowProvisioningUpdates" not in host.executed[1]


def test_manual_fingerprints_fail_when_assets_are_missing(tmp_path: Path) -> None:
    with pytest.raises(DeviceRunnerError, match="not installed"):
        build.manual_fingerprints(MANUAL, FakeHost(tmp_path).toolchain())


def test_default_cache_root_sits_beside_the_simulator_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert build.default_cache_root() == tmp_path / "bajutsu" / "xcuitest-runner-device"
    assert _bundled_runner._cache_root() == tmp_path / "bajutsu" / "xcuitest-runner"

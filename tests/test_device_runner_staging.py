"""Staging the runner sources and rendering the per-user project spec (BE-0456)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from bajutsu.common import _yaml
from bajutsu.common.platform_lifecycle.environments import _bundled_runner
from bajutsu.common.platform_lifecycle.environments.device_runner import staging
from bajutsu.common.platform_lifecycle.environments.device_runner.signing import SigningConfig

_REPO = Path(__file__).resolve().parents[1]

AUTOMATIC = SigningConfig.model_validate({"bundleIdPrefix": "com.acme", "teamId": "TEAM1"})
MANUAL = SigningConfig.model_validate(
    {
        "bundleIds": {"host": "com.acme.h", "uitests": "com.acme.u"},
        "teamId": "TEAM1",
        "signing": "manual",
        "manual": {"identity": "Apple Development: Jane", "profiles": {"host": "H", "runner": "R"}},
    }
)


def _write_sources(root: Path) -> None:
    """A minimal mirror of every path ``HASH_SOURCE_PATHS`` names, with the committed spec/manifest."""
    for rel in _bundled_runner.HASH_SOURCE_PATHS:
        target = root / rel
        if rel.endswith((".swift", ".yml")):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text((_REPO / rel).read_text())
        else:
            target.mkdir(parents=True, exist_ok=True)
            (target / "File.swift").write_text(f"// {rel}\n")


def test_stripping_the_committed_manifest_keeps_every_non_test_target() -> None:
    # The guard the design names: a target added to Package.swift later must still reach the
    # device build, which only holds while the staged manifest is derived, not hand-copied.
    committed = (_REPO / "Package.swift").read_text()
    staged = staging.strip_test_targets(committed)
    assert staging.declared_targets(staged) == staging.declared_targets(committed)
    assert staging.declared_targets(committed) >= {"BajutsuKit", "BajutsuRunner"}
    assert ".testTarget" not in staged
    assert "BajutsuKit/Tests" not in staged


def test_stripping_handles_a_multi_line_test_target() -> None:
    manifest = (
        "targets: [\n"
        '    .target(name: "A", path: "a"),\n'
        "    .testTarget(\n"
        '        name: "ATests",\n'
        '        dependencies: [.product(name: "X", package: "x")],\n'
        '        path: "t"\n'
        "    ),\n"
        '    .target(name: "B", path: "b"),\n'
        "]\n"
    )
    assert staging.strip_test_targets(manifest) == (
        'targets: [\n    .target(name: "A", path: "a"),\n    .target(name: "B", path: "b"),\n]\n'
    )


def test_stripping_ignores_parentheses_in_strings_and_comments() -> None:
    manifest = (
        "targets: [\n"
        '    .testTarget(name: "T", path: "Tests (iOS)/a\\")"), // keep (this\n'
        '    /* ( */ .target(name: "A", path: "a"),\n'
        "]\n"
    )
    stripped = staging.strip_test_targets(manifest)
    assert ".testTarget" not in stripped
    assert staging.declared_targets(stripped) == {"A"}


def test_stripping_an_unbalanced_test_target_fails() -> None:
    with pytest.raises(staging.StagingError, match="unbalanced"):
        staging.strip_test_targets('.testTarget(name: "A"')


def _committed_spec() -> dict[str, Any]:
    spec = _yaml.safe_load((_REPO / staging.PROJECT_SPEC).read_text())
    assert isinstance(spec, dict)
    return spec


def _base(spec: dict[str, Any], target: str) -> dict[str, Any]:
    settings: dict[str, Any] = spec["targets"][target]["settings"]["base"]
    return settings


def test_render_automatic_signing_sets_per_target_identifiers(tmp_path: Path) -> None:
    spec = _committed_spec()
    staging.render_project_spec(spec, AUTOMATIC, tmp_path)
    host = _base(spec, staging.HOST_TARGET)
    uitests = _base(spec, staging.UITESTS_TARGET)
    assert host["PRODUCT_BUNDLE_IDENTIFIER"] == "com.acme.bajutsu.runner-host"
    assert uitests["PRODUCT_BUNDLE_IDENTIFIER"] == "com.acme.bajutsu.runner-uitests"
    for settings in (host, uitests):
        assert settings["DEVELOPMENT_TEAM"] == "TEAM1"
        assert settings["CODE_SIGNING_ALLOWED"] == "YES"
        assert settings["CODE_SIGN_STYLE"] == "Automatic"
        assert "CODE_SIGN_IDENTITY" not in settings
        assert "PROVISIONING_PROFILE_SPECIFIER" not in settings
    assert spec["packages"]["BajutsuKit"]["path"] == str(tmp_path)


def test_render_manual_signing_pins_identity_and_per_product_profiles(tmp_path: Path) -> None:
    spec = _committed_spec()
    staging.render_project_spec(spec, MANUAL, tmp_path)
    host = _base(spec, staging.HOST_TARGET)
    uitests = _base(spec, staging.UITESTS_TARGET)
    assert (host["PRODUCT_BUNDLE_IDENTIFIER"], uitests["PRODUCT_BUNDLE_IDENTIFIER"]) == (
        "com.acme.h",
        "com.acme.u",
    )
    assert host["CODE_SIGN_STYLE"] == uitests["CODE_SIGN_STYLE"] == "Manual"
    assert host["CODE_SIGN_IDENTITY"] == uitests["CODE_SIGN_IDENTITY"] == "Apple Development: Jane"
    # The UI-test target signs the `.xctrunner` app, so it takes the `runner` profile.
    assert (host["PROVISIONING_PROFILE_SPECIFIER"], uitests["PROVISIONING_PROFILE_SPECIFIER"]) == (
        "H",
        "R",
    )


def test_render_fails_loudly_when_a_target_is_gone(tmp_path: Path) -> None:
    spec = _committed_spec()
    del spec["targets"][staging.UITESTS_TARGET]
    with pytest.raises(staging.StagingError, match="expected shape"):
        staging.render_project_spec(spec, AUTOMATIC, tmp_path)


def test_stage_copies_rewrites_and_leaves_the_source_untouched(tmp_path: Path) -> None:
    src, dest = tmp_path / "src", tmp_path / "dest"
    _write_sources(src)
    before = _bundled_runner.source_hash(root=src)

    spec_path = staging.stage(src, dest, AUTOMATIC)

    assert _bundled_runner.source_hash(root=src) == before
    assert ".testTarget" not in (dest / "Package.swift").read_text()
    rendered = _yaml.safe_load(spec_path.read_text())
    assert rendered["packages"]["BajutsuKit"]["path"] == str(dest)
    host = rendered["targets"][staging.HOST_TARGET]["settings"]["base"]
    assert host["PRODUCT_BUNDLE_IDENTIFIER"] == "com.acme.bajutsu.runner-host"
    # Settings the rewrite does not own survive the load/dump round trip as strings.
    assert rendered["settings"]["base"]["CODE_SIGNING_ALLOWED"] == "NO"


def test_copy_fails_on_a_missing_source_path(tmp_path: Path) -> None:
    with pytest.raises(staging.StagingError, match="missing"):
        staging.copy_runner_sources(tmp_path / "empty", tmp_path / "dest")


def test_copied_sources_hash_like_the_original(tmp_path: Path) -> None:
    # `make runner-source` relies on this: the wheel's copy must key the cache like the checkout.
    src, dest = tmp_path / "src", tmp_path / "dest"
    _write_sources(src)
    staging.copy_runner_sources(src, dest)
    assert _bundled_runner.source_hash(root=dest) == _bundled_runner.source_hash(root=src)


def test_runner_source_root_prefers_the_checkout_then_the_wheel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout, wheel = tmp_path / "checkout", tmp_path / "wheel"
    monkeypatch.setattr(_bundled_runner, "repo_root", lambda: checkout)
    monkeypatch.setattr(staging, "WHEEL_SOURCE_DIR", wheel)
    monkeypatch.setattr(
        _bundled_runner,
        "runner_source_present",
        lambda *, root=None: ((root or checkout) / staging.PROJECT_SPEC).is_file(),
    )
    assert staging.runner_source_root() is None

    _write_sources(wheel)
    assert staging.runner_source_root() == wheel

    _write_sources(checkout)
    assert staging.runner_source_root() == checkout

"""The per-user signing file for the device runner build (BE-0456): validation and lookup order."""

from __future__ import annotations

from pathlib import Path

import pytest

from bajutsu.common.platform_lifecycle.environments.device_runner.signing import (
    SIGNING_FILE_ENV,
    SigningError,
    default_signing_path,
    find_signing_file,
    load_signing,
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_prefix_form_derives_both_identifiers(tmp_path: Path) -> None:
    cfg = load_signing(
        _write(tmp_path / "s.yaml", "bundleIdPrefix: com.acme\nteamId: ABCDE12345\n")
    )
    assert cfg.host_bundle_id == "com.acme.bajutsu.runner-host"
    assert cfg.uitests_bundle_id == "com.acme.bajutsu.runner-uitests"
    assert cfg.signing == "automatic"
    assert cfg.manual_signing is None


def test_explicit_bundle_ids_are_used_verbatim(tmp_path: Path) -> None:
    cfg = load_signing(
        _write(
            tmp_path / "s.yaml",
            "bundleIds: {host: com.acme.e2e.host, uitests: com.acme.e2e.tests}\nteamId: T\n",
        )
    )
    assert (cfg.host_bundle_id, cfg.uitests_bundle_id) == (
        "com.acme.e2e.host",
        "com.acme.e2e.tests",
    )


def test_manual_signing_with_one_wildcard_profile(tmp_path: Path) -> None:
    cfg = load_signing(
        _write(
            tmp_path / "s.yaml",
            "bundleIdPrefix: com.acme\nteamId: T\nsigning: manual\n"
            "manual: {identity: 'Apple Development: Jane', profile: Wild}\n",
        )
    )
    manual = cfg.manual_signing
    assert manual is not None
    assert (manual.host_profile(), manual.runner_profile()) == ("Wild", "Wild")


def test_manual_signing_with_per_product_profiles(tmp_path: Path) -> None:
    cfg = load_signing(
        _write(
            tmp_path / "s.yaml",
            "bundleIdPrefix: com.acme\nteamId: T\nsigning: manual\n"
            "manual:\n  identity: I\n  profiles: {host: H, runner: R}\n",
        )
    )
    manual = cfg.manual_signing
    assert manual is not None
    assert (manual.host_profile(), manual.runner_profile()) == ("H", "R")


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("bundleIdPrefix: com.acme\n", "teamId"),
        ("bundleIdPrefix: com.acme\nteamId: T\nteamName: x\n", "teamName"),
        ("teamId: T\n", "exactly one of `bundleIdPrefix` or `bundleIds`"),
        (
            "bundleIdPrefix: a\nbundleIds: {host: a.h, uitests: a.u}\nteamId: T\n",
            "exactly one of `bundleIdPrefix` or `bundleIds`",
        ),
        ("bundleIds: {host: a.h}\nteamId: T\n", "uitests"),
        ("bundleIds: {host: a.x, uitests: a.x}\nteamId: T\n", "must differ"),
        ("bundleIdPrefix: a\nteamId: T\nsigning: manual\n", "needs a `manual` block"),
        ("bundleIdPrefix: a\nteamId: T\nsigning: manual\nmanual: {profile: P}\n", "identity"),
        (
            "bundleIdPrefix: a\nteamId: T\nsigning: manual\nmanual: {identity: I}\n",
            "exactly one of `profile` or `profiles`",
        ),
        (
            "bundleIdPrefix: a\nteamId: T\nsigning: manual\n"
            "manual: {identity: I, profile: P, profiles: {host: H, runner: R}}\n",
            "exactly one of `profile` or `profiles`",
        ),
        ("bundleIdPrefix: a\nteamId: T\nsigning: sometimes\n", "signing"),
    ],
)
def test_invalid_files_fail_validation(tmp_path: Path, text: str, fragment: str) -> None:
    with pytest.raises(SigningError, match="invalid signing file") as info:
        load_signing(_write(tmp_path / "s.yaml", text))
    assert fragment in str(info.value)


def test_non_mapping_and_unparsable_files_fail(tmp_path: Path) -> None:
    with pytest.raises(SigningError, match="must be a YAML mapping"):
        load_signing(_write(tmp_path / "list.yaml", "- a\n"))
    with pytest.raises(SigningError, match="cannot read"):
        load_signing(_write(tmp_path / "bad.yaml", "a: [\n"))
    with pytest.raises(SigningError, match="cannot read"):
        load_signing(tmp_path / "missing.yaml")


def test_default_path_honors_xdg_config_home(tmp_path: Path) -> None:
    assert default_signing_path({"XDG_CONFIG_HOME": str(tmp_path)}) == (
        tmp_path / "bajutsu" / "signing.yaml"
    )
    assert default_signing_path({}) == Path.home() / ".config" / "bajutsu" / "signing.yaml"


def test_lookup_order_explicit_then_env_then_default(tmp_path: Path) -> None:
    default = tmp_path / "xdg" / "bajutsu" / "signing.yaml"
    default.parent.mkdir(parents=True)
    default.write_text("x")
    from_env = _write(tmp_path / "env.yaml", "x")
    explicit = _write(tmp_path / "explicit.yaml", "x")
    env = {"XDG_CONFIG_HOME": str(tmp_path / "xdg"), SIGNING_FILE_ENV: str(from_env)}

    assert find_signing_file(explicit, env) == explicit
    assert find_signing_file(None, env) == from_env
    assert find_signing_file(None, {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}) == default


def test_a_default_path_that_is_not_a_file_fails(tmp_path: Path) -> None:
    (tmp_path / "bajutsu" / "signing.yaml").mkdir(parents=True)
    with pytest.raises(SigningError, match="not a file"):
        find_signing_file(None, {"XDG_CONFIG_HOME": str(tmp_path)})


def test_absent_default_means_no_file(tmp_path: Path) -> None:
    assert find_signing_file(None, {"XDG_CONFIG_HOME": str(tmp_path)}) is None


def test_named_paths_that_do_not_exist_fail_instead_of_falling_through(tmp_path: Path) -> None:
    with pytest.raises(SigningError, match="from --signing"):
        find_signing_file(tmp_path / "nope.yaml", {})
    with pytest.raises(SigningError, match=f"from \\${SIGNING_FILE_ENV}"):
        find_signing_file(None, {SIGNING_FILE_ENV: str(tmp_path / "nope.yaml")})

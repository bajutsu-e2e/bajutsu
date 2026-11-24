"""Tests for the operator's group-notification sink file (BE-0500 unit 1).

`serve` reads the sinks once at startup and refuses to start on a file it cannot honour, so a
misconfiguration surfaces where the operator is looking rather than on the first group.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from bajutsu.serve.group_notify import GroupNotifyConfigError, GroupSink, load_group_sinks

_PEM = (
    rsa.generate_private_key(public_exponent=65537, key_size=2048)
    .private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    .decode("utf-8")
)
_APP = {"BAJUTSU_GITHUB_APP_ID": "1", "BAJUTSU_GITHUB_APP_PRIVATE_KEY": _PEM}


def _file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "sinks.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_github_check_sink_loads_with_the_default_check_name(tmp_path: Path) -> None:
    path = _file(tmp_path, "groupNotify:\n  - name: github\n    format: github-check\n")

    sinks = load_group_sinks(path, _APP)

    assert sinks == {"github": GroupSink(name="github", format="github-check")}
    assert sinks["github"].check_name_for("demo") == "bajutsu / demo"


def test_a_sink_names_its_own_check(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        "groupNotify:\n  - name: gh\n    format: github-check\n    checkName: 'e2e ({target})'\n",
    )

    assert load_group_sinks(path, _APP)["gh"].check_name_for("ios") == "e2e (ios)"


def test_an_unknown_format_names_its_sink(tmp_path: Path) -> None:
    path = _file(tmp_path, "groupNotify:\n  - name: hook\n    format: webhook\n")

    with pytest.raises(GroupNotifyConfigError, match=r"hook.*webhook"):
        load_group_sinks(path, _APP)


def test_two_sinks_with_one_name_are_refused(tmp_path: Path) -> None:
    sink = "  - name: gh\n    format: github-check\n"
    path = _file(tmp_path, "groupNotify:\n" + sink + sink)

    with pytest.raises(GroupNotifyConfigError, match="gh"):
        load_group_sinks(path, _APP)


@pytest.mark.parametrize(
    "env",
    [{}, {"BAJUTSU_GITHUB_APP_ID": "1"}, {"BAJUTSU_GITHUB_APP_PRIVATE_KEY": "pem"}],
    ids=["nothing", "no key", "no id"],
)
def test_a_github_check_sink_needs_the_github_app(tmp_path: Path, env: dict[str, str]) -> None:
    path = _file(tmp_path, "groupNotify:\n  - name: github\n    format: github-check\n")

    with pytest.raises(GroupNotifyConfigError, match="BAJUTSU_GITHUB_APP_ID"):
        load_group_sinks(path, env)


def test_a_key_file_satisfies_the_app_check(tmp_path: Path) -> None:
    path = _file(tmp_path, "groupNotify:\n  - name: github\n    format: github-check\n")
    key = tmp_path / "app.pem"
    key.write_text(_PEM, encoding="utf-8")
    env = {"BAJUTSU_GITHUB_APP_ID": "1", "BAJUTSU_GITHUB_APP_PRIVATE_KEY_FILE": str(key)}

    assert set(load_group_sinks(path, env)) == {"github"}


def test_a_key_that_cannot_sign_is_refused_at_startup(tmp_path: Path) -> None:
    path = _file(tmp_path, "groupNotify:\n  - name: github\n    format: github-check\n")
    env = {"BAJUTSU_GITHUB_APP_ID": "1", "BAJUTSU_GITHUB_APP_PRIVATE_KEY": "not a pem"}

    with pytest.raises(GroupNotifyConfigError, match="cannot sign"):
        load_group_sinks(path, env)


def test_a_key_file_that_does_not_exist_is_refused_at_startup(tmp_path: Path) -> None:
    path = _file(tmp_path, "groupNotify:\n  - name: github\n    format: github-check\n")
    env = {"BAJUTSU_GITHUB_APP_ID": "1", "BAJUTSU_GITHUB_APP_PRIVATE_KEY_FILE": "/absent.pem"}

    with pytest.raises(GroupNotifyConfigError, match=r"absent\.pem"):
        load_group_sinks(path, env)


@pytest.mark.parametrize(
    "body",
    ["- not a mapping\n", "groupNotify: {}\n", "groupNotify:\n  - format: github-check\n", ": :\n"],
    ids=["list", "not a list", "no name", "bad yaml"],
)
def test_a_malformed_file_is_refused(tmp_path: Path, body: str) -> None:
    with pytest.raises(GroupNotifyConfigError):
        load_group_sinks(_file(tmp_path, body), _APP)


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(GroupNotifyConfigError, match="cannot read"):
        load_group_sinks(tmp_path / "absent.yaml", _APP)

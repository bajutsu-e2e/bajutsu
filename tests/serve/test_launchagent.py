"""Tests for the LaunchAgent plist emitter (BE-0016 Tier A self-hosting).

`bajutsu serve --emit-launchagent` prints a launchd plist that runs the (token-authenticated)
serve as a per-user LaunchAgent. The plist is generated with plistlib, so this is a pure
string-generation unit — no macOS needed.
"""

from __future__ import annotations

import plistlib

from bajutsu import serve as srv


def _plist_of(text: str) -> dict[str, object]:
    data = plistlib.loads(text.encode("utf-8"))
    assert isinstance(data, dict)
    return data


def _plist(*, host: str, port: int, config: str | None, token: str | None) -> dict[str, object]:
    return _plist_of(srv.launchagent_plist(host=host, port=port, config=config, token=token))


# A plist is a heterogeneous document, so `plistlib.loads` hands back untyped values. These narrow
# one out of it with a real check, which also pins the emitted type (BE-0388).


def _args(pl: dict[str, object]) -> list[str]:
    """The plist's `ProgramArguments` — the emitter always writes a string array."""
    args = pl["ProgramArguments"]
    assert isinstance(args, list)
    return args


def _text(pl: dict[str, object], key: str) -> str:
    """One string value out of the plist."""
    value = pl[key]
    assert isinstance(value, str)
    return value


def _mapping(pl: dict[str, object], key: str) -> dict[str, object]:
    """One nested dictionary out of the plist."""
    value = pl[key]
    assert isinstance(value, dict)
    return value


def test_plist_runs_serve_with_host_port_config() -> None:
    pl = _plist(host="127.0.0.1", port=8765, config="bajutsu.common.config.yaml", token=None)
    assert pl["Label"] == "com.bajutsu.serve"
    args = _args(pl)
    assert args[1:4] == ["-m", "bajutsu", "serve"]
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert args[args.index("--port") + 1] == "8765"
    assert args[args.index("--config") + 1] == "bajutsu.common.config.yaml"


def test_plist_carries_non_default_upload_exec() -> None:
    # A non-default upload-exec policy must reach the installed daemon (BE-0090); the default
    # `sandbox` is omitted so the common case keeps the plist clean.
    deny = _args(
        _plist_of(
            srv.launchagent_plist(
                host="127.0.0.1", port=8765, config=None, token=None, upload_exec="deny"
            )
        )
    )
    assert deny[deny.index("--upload-exec") + 1] == "deny"
    default = _args(
        _plist_of(
            srv.launchagent_plist(
                host="127.0.0.1", port=8765, config=None, token=None, upload_exec="sandbox"
            )
        )
    )
    assert "--upload-exec" not in default  # default omitted


def test_plist_keepalive_and_logs() -> None:
    pl = _plist(host="127.0.0.1", port=8765, config=None, token=None)
    assert pl["RunAtLoad"] is True
    assert pl["KeepAlive"] is True
    # launchd does not expand `~`, so the log paths must be absolute and tilde-free.
    for key in ("StandardOutPath", "StandardErrorPath", "WorkingDirectory"):
        assert not _text(pl, key).startswith("~")
    assert _text(pl, "StandardOutPath").startswith("/")
    assert _text(pl, "StandardOutPath").endswith(".log")
    assert _text(pl, "StandardErrorPath").endswith(".log")
    assert "--config" not in _args(pl)  # omitted when no config


def test_token_goes_in_environment_not_argv() -> None:
    pl = _plist(host="127.0.0.1", port=8765, config=None, token="s3cret")
    # the token must not appear in the process argv (visible in `ps`); it rides EnvironmentVariables
    assert "s3cret" not in _args(pl)
    assert _mapping(pl, "EnvironmentVariables")["BAJUTSU_SERVE_TOKEN"] == "s3cret"


def test_no_environment_block_without_token() -> None:
    pl = _plist(host="127.0.0.1", port=8765, config=None, token=None)
    assert "EnvironmentVariables" not in pl

"""Tests for the LaunchAgent plist emitter (BE-0016 Tier A self-hosting).

`bajutsu serve --emit-launchagent` prints a launchd plist that runs the (token-authenticated)
serve as a per-user LaunchAgent. The plist is generated with plistlib, so this is a pure
string-generation unit — no macOS needed.
"""

from __future__ import annotations

import plistlib

from bajutsu import serve as srv


def _plist(*, host: str, port: int, config: str | None, token: str | None) -> dict[str, object]:
    text = srv.launchagent_plist(host=host, port=port, config=config, token=token)
    return plistlib.loads(text.encode("utf-8"))


def test_plist_runs_serve_with_host_port_config() -> None:
    pl = _plist(host="127.0.0.1", port=8765, config="bajutsu.config.yaml", token=None)
    assert pl["Label"] == "com.bajutsu.serve"
    args = pl["ProgramArguments"]
    assert args[1:4] == ["-m", "bajutsu", "serve"]
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert args[args.index("--port") + 1] == "8765"
    assert args[args.index("--config") + 1] == "bajutsu.config.yaml"


def test_plist_carries_non_default_upload_exec() -> None:
    # A non-default upload-exec policy must reach the installed daemon (BE-0090); the default
    # `sandbox` is omitted so the common case keeps the plist clean.
    deny = plistlib.loads(
        srv.launchagent_plist(
            host="127.0.0.1", port=8765, config=None, token=None, upload_exec="deny"
        ).encode("utf-8")
    )["ProgramArguments"]
    assert deny[deny.index("--upload-exec") + 1] == "deny"
    default = plistlib.loads(
        srv.launchagent_plist(
            host="127.0.0.1", port=8765, config=None, token=None, upload_exec="sandbox"
        ).encode("utf-8")
    )["ProgramArguments"]
    assert "--upload-exec" not in default  # default omitted


def test_plist_keepalive_and_logs() -> None:
    pl = _plist(host="127.0.0.1", port=8765, config=None, token=None)
    assert pl["RunAtLoad"] is True
    assert pl["KeepAlive"] is True
    # launchd does not expand `~`, so the log paths must be absolute and tilde-free.
    for key in ("StandardOutPath", "StandardErrorPath", "WorkingDirectory"):
        assert not pl[key].startswith("~")
    assert pl["StandardOutPath"].startswith("/") and pl["StandardOutPath"].endswith(".log")
    assert pl["StandardErrorPath"].endswith(".log")
    assert "--config" not in pl["ProgramArguments"]  # omitted when no config


def test_token_goes_in_environment_not_argv() -> None:
    pl = _plist(host="127.0.0.1", port=8765, config=None, token="s3cret")
    # the token must not appear in the process argv (visible in `ps`); it rides EnvironmentVariables
    assert "s3cret" not in pl["ProgramArguments"]
    assert pl["EnvironmentVariables"]["BAJUTSU_SERVE_TOKEN"] == "s3cret"


def test_no_environment_block_without_token() -> None:
    pl = _plist(host="127.0.0.1", port=8765, config=None, token=None)
    assert "EnvironmentVariables" not in pl

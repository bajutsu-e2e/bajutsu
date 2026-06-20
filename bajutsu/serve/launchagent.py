"""Emit a launchd LaunchAgent plist that runs `bajutsu serve` (BE-0016 Tier A self-hosting).

The Simulator needs a GUI login session, so serve must run as a per-user **LaunchAgent** (not a
LaunchDaemon). `bajutsu serve --emit-launchagent` prints this plist, matching the serve flags you
pass, so a single Mac can keep a token-authenticated serve up across reboots behind a tailnet.
The token rides EnvironmentVariables (never the argv, so it's not visible in `ps`).
"""

from __future__ import annotations

import plistlib
import sys
from pathlib import Path

LABEL = "com.bajutsu.serve"


def launchagent_plist(
    *,
    host: str,
    port: int,
    config: str | None,
    token: str | None,
    python: str | None = None,
    working_dir: str | None = None,
    log_dir: str = "~/Library/Logs",
) -> str:
    """Build the LaunchAgent plist (XML) for a `bajutsu serve` invocation.

    `python` defaults to the current interpreter (the venv's), so the agent uses the same
    environment. `working_dir` defaults to the current directory so a relative `bajutsu.config.yaml`
    resolves. A token, if given, is placed in EnvironmentVariables, not the program arguments.
    Path values are expanded (`~` → home): launchd does not perform shell expansion, so the plist
    must carry absolute paths or the agent's logs land nowhere.
    """
    args = [
        python or sys.executable,
        "-m",
        "bajutsu",
        "serve",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if config:
        args += ["--config", config]

    logs = Path(log_dir).expanduser()
    plist: dict[str, object] = {
        "Label": LABEL,
        "ProgramArguments": args,
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(Path(working_dir).expanduser()) if working_dir else str(Path.cwd()),
        "StandardOutPath": str(logs / "bajutsu-serve.out.log"),
        "StandardErrorPath": str(logs / "bajutsu-serve.err.log"),
    }
    if token:
        plist["EnvironmentVariables"] = {"BAJUTSU_SERVE_TOKEN": token}

    return plistlib.dumps(plist, fmt=plistlib.PlistFormat.FMT_XML).decode("utf-8")

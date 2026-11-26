"""A stand-in lease broker command for the tests of BE-0511: a script, no broker product."""

from __future__ import annotations

import sys
from pathlib import Path

# Behaves as the contract asks, or breaks it in the one way `mode` names. `log` records what it saw.
_STUB = """\
import json, os, signal, sys, time

log, mode, *rest = sys.argv[1:]

def note(text):
    with open(log, "a") as f:
        f.write(text + "\\n")

note("argv " + " ".join(rest))
platform = rest[1] if len(rest) > 1 else "ios"

def stop(*_):
    note("terminated " + platform)
    sys.exit(0)

signal.signal(signal.SIGTERM, signal.SIG_IGN if mode == "stubborn" else stop)

if mode == "decline":
    sys.stderr.write("no device free\\n")
    sys.exit(3)
if mode == "silent":
    time.sleep(30)
if mode == "queued":
    sys.stderr.write("3 requests ahead\\n")
    sys.stderr.flush()
    time.sleep(30)
if mode == "dies":
    print(json.dumps({"device": {"address": "AAAA-1111"}}), flush=True)
    sys.stderr.write("revoked\\n")
    sys.exit(7)
if mode == "garbage":
    print("not json", flush=True)
    time.sleep(30)
if mode.startswith("limit:"):
    # Lend `limit` devices in all, then decline: a counter file stands for the pool.
    limit = int(mode.split(":")[1])
    with open(log + ".count", "a+") as f:
        f.seek(0)
        taken = len(f.read())
        if taken >= limit:
            sys.stderr.write("pool exhausted\\n")
            sys.exit(3)
        f.write("x")
    address = platform + "-" + str(taken)
else:
    address = "emulator-5588" if platform == "android" else "AAAA-1111"
environment = {"STUB_PORT": "5038" if mode != "per-device-env" else str(os.getpid())}
print(json.dumps({"device": {"address": address}, "environment": environment}), flush=True)
if mode == "chatty":
    for _ in range(4000):
        print("x" * 200, flush=True)
    note("logged past its answer")
sys.stdin.read()
note("stdin closed " + platform)
"""


def lease_command(tmp_path: Path, mode: str = "ok") -> tuple[list[str], Path]:
    """The stub's argv with its placeholders, and the file it records its life in."""
    script = tmp_path / "broker_stub.py"
    script.write_text(_STUB, encoding="utf-8")
    log = tmp_path / "broker.log"
    command = [
        sys.executable,
        str(script),
        str(log),
        mode,
        "--platform",
        "{platform}",
        "--device",
        "{device}",
    ]
    return command, log


def events(log: Path) -> list[str]:
    """What the stub has recorded so far."""
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

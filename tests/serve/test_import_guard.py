"""Containment guardrail for the BE-0015 server phase.

The hosted server backend (FastAPI / Redis / SQLAlchemy / object storage / OAuth) is being added
behind optional dependency groups and a `bajutsu/serve/server/` subpackage, imported only when a
server backend is explicitly selected. This test locks the invariant *before* those heavy deps
exist: importing the default `bajutsu serve` / CLI path must pull in **none** of them, so the
default install and the Linux gate stay server-free and `make serve` stays single-process.

It runs in a clean child interpreter so the result can't be contaminated by other tests in the
session that may import server packages.
"""

from __future__ import annotations

import subprocess
import sys

# Top-level packages that only the (future) server backend may import — never the default path.
FORBIDDEN = sorted(
    {"redis", "rq", "fastapi", "uvicorn", "sqlalchemy", "alembic", "authlib", "boto3", "psycopg"}
)


def test_default_serve_and_cli_import_no_server_deps() -> None:
    # Importing bajutsu.cli runs the command scan (every commands/<name>.py), so a command that
    # imported a server dep at module load — instead of lazily — would surface here too.
    code = (
        "import sys\n"
        "import bajutsu.serve\n"
        "import bajutsu.cli\n"
        f"forbidden = set({FORBIDDEN!r})\n"
        "leaked = sorted(m for m in sys.modules if m.split('.')[0] in forbidden)\n"
        "sys.stdout.write(','.join(leaked))\n"
        "sys.exit(1 if leaked else 0)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    # exit 1 = server deps leaked (listed on stdout); any other non-zero = the import itself failed
    # (traceback on stderr) — surface both so a failure is actionable rather than just "non-zero".
    assert result.returncode == 0, (
        "importing the default serve/CLI path failed the server-dep guard "
        f"(exit {result.returncode}).\n"
        f"leaked server deps: {result.stdout.strip() or '(none)'}\n"
        f"stderr: {result.stderr.strip() or '(none)'}\n"
        "Keep server imports lazy / inside bajutsu/serve/server/ behind the backend selection."
    )

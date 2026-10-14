"""Containment guardrail for heavy dependencies on the default import path.

Every opt-in subsystem — the hosted server backend's cloud clients (Redis / object storage / the
PostgreSQL driver / OAuth), the web backend's Playwright, and the AI SDK (`anthropic`, BE-0111) —
must stay off the default `bajutsu serve` / CLI / `run` path, imported only when that subsystem is
explicitly used. The server stack itself (FastAPI, uvicorn, SQLAlchemy, Alembic) is a base
dependency since BE-0449, so a base install can start the local server shape, but it is still
imported only by the `serve` paths that use it: the CLI (`bajutsu run` and the rest) must start
without loading it. These tests lock that invariant: importing the default path must pull in
**none** of those packages, so CLI startup stays fast and `make serve` stays single-process.

Each test runs in a clean child interpreter so the result can't be contaminated by other tests in
the session that may import those packages.
"""

from __future__ import annotations

import subprocess
import sys

# Top-level packages the default path never imports: the server stack (installed with the base
# since BE-0449, but loaded only when `serve` builds a server state or the FastAPI app), the
# server's optional cloud clients, and the web backend's Playwright (a heavy dep loaded only when a
# browser is actually started; see bajutsu/common/drivers/playwright.py).
FORBIDDEN = sorted(
    {
        "redis",
        "rq",
        "fastapi",
        "uvicorn",
        "sqlalchemy",
        "alembic",
        "cryptography",
        "starlette",
        "authlib",
        "joserfc",
        "boto3",
        "psycopg",
        "playwright",
    }
)


def _run_in_child(code: str) -> subprocess.CompletedProcess[str]:
    """Run `code` in a clean child interpreter so no in-session import can contaminate the result."""
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=False
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
    result = _run_in_child(code)
    # exit 1 = server deps leaked (listed on stdout); any other non-zero = the import itself failed
    # (traceback on stderr) — surface both so a failure is actionable rather than just "non-zero".
    assert result.returncode == 0, (
        "importing the default serve/CLI path failed the server-dep guard "
        f"(exit {result.returncode}).\n"
        f"leaked server deps: {result.stdout.strip() or '(none)'}\n"
        f"stderr: {result.stderr.strip() or '(none)'}\n"
        "Keep server imports lazy / inside bajutsu/serve/server/ behind the backend selection."
    )


def test_worker_command_path_stays_lean() -> None:
    """Importing the `worker` command path must not pull in the control plane / cloud / AI stacks.

    A slim web-worker container (BE-0173) carries only the worker's true runtime closure
    (`bajutsu[worker-web]` — the `web` backend + `visual` + `schema`); its weight above the Chromium
    binary is exactly the deps it imports. This locks that a stray top-level import in the worker
    entry — or anything it reaches (`bajutsu.serve.server.worker_job`) — can never silently
    re-inflate the image or slow the worker's cold start by dragging in `fastapi`/`uvicorn`,
    `sqlalchemy`, `boto3`/GCS, or `anthropic`. The worker talks to the control plane and object store
    over plain HTTP (BE-0106/BE-0160), so none of those belong on its import closure.
    """
    forbidden = sorted(
        {
            "fastapi",
            "uvicorn",
            "starlette",
            "sqlalchemy",
            "alembic",
            "psycopg",
            "authlib",  # OAuth (server/oauth extra) — control-plane only
            "boto3",
            "botocore",
            "google",  # google-cloud-storage
            "anthropic",
            "redis",
            "rq",
        }
    )
    code = (
        "import sys\n"
        "import bajutsu.serve.cli.worker\n"
        f"forbidden = set({forbidden!r})\n"
        "leaked = sorted(m for m in sys.modules if m.split('.')[0] in forbidden)\n"
        "sys.stdout.write(','.join(leaked))\n"
        "sys.exit(1 if leaked else 0)\n"
    )
    result = _run_in_child(code)
    assert result.returncode == 0, (
        "importing the worker command path pulled in a dep outside the worker runtime closure "
        f"(exit {result.returncode}).\n"
        f"leaked deps: {result.stdout.strip() or '(none)'}\n"
        f"stderr: {result.stderr.strip() or '(none)'}\n"
        "Keep the control-plane / cloud / AI imports lazy so `bajutsu[worker-web]` stays slim "
        "(BE-0173): the worker reaches the control plane and object store over HTTP, not an SDK."
    )


def test_default_path_does_not_import_anthropic() -> None:
    """The AI SDK (`anthropic`, BE-0111) must stay off the default path.

    Importing the CLI, serve, and the deterministic run pipeline must not pull in `anthropic` — the
    SDK is reached only lazily on the Tier-1 authoring / investigation paths, so the base (AI-free)
    install carries no AI SDK.
    """
    code = (
        "import sys\n"
        "import bajutsu.cli\n"
        "import bajutsu.serve\n"
        "import bajutsu.common.runner.pipeline\n"
        "leaked = sorted(m for m in sys.modules if m.split('.')[0] == 'anthropic')\n"
        "sys.stdout.write(','.join(leaked))\n"
        "sys.exit(1 if leaked else 0)\n"
    )
    result = _run_in_child(code)
    assert result.returncode == 0, (
        "importing the default CLI/serve/run path pulled in the AI SDK "
        f"(exit {result.returncode}).\n"
        f"leaked AI modules: {result.stdout.strip() or '(none)'}\n"
        f"stderr: {result.stderr.strip() or '(none)'}\n"
        "Keep `import anthropic` lazy (inside the function that reaches the model) so the base "
        "install stays AI-free."
    )


def test_default_path_runs_with_anthropic_absent() -> None:
    """A base install (no `ai` / `bedrock` extra) imports and runs the deterministic subset.

    The gate's venv has `anthropic` installed (via the dev group), so we simulate the base install
    by blocking `import anthropic` in a child interpreter, then confirm the CLI imports and a real
    deterministic assertion evaluates — proving the default path never needs the SDK. No LLM is
    involved; the check is fully static / deterministic (BE-0111).
    """
    code = (
        "import sys\n"
        "import importlib.abc\n"
        "class _Blocker(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path, target=None):\n"
        "        if name == 'anthropic' or name.startswith('anthropic.'):\n"
        "            raise ModuleNotFoundError(f'blocked (BE-0111 base-install sim): {name}')\n"
        "        return None\n"
        "sys.meta_path.insert(0, _Blocker())\n"
        "import bajutsu.cli\n"
        "from bajutsu.common.assertions import evaluate\n"
        "assert evaluate([], []) == [], 'deterministic no-op assertion should return []'\n"
        "assert 'anthropic' not in sys.modules, 'anthropic must stay unimported'\n"
    )
    result = _run_in_child(code)
    assert result.returncode == 0, (
        "the default path failed with the AI SDK absent "
        f"(exit {result.returncode}).\n"
        f"stderr: {result.stderr.strip() or '(none)'}\n"
        "The base (AI-free) install must import and run the deterministic subset without `anthropic`."
    )


def test_the_server_stack_is_a_base_dependency_and_the_old_extras_still_resolve() -> None:
    """The server shape needs no extra (BE-0449), and an existing install command still works.

    FastAPI, uvicorn, SQLAlchemy, Alembic, and `cryptography` are base dependencies, so a plain
    install can run the local start. The PostgreSQL driver stays in `db`, since SQLite ships with
    Python, and boto3 stays in `server`. Both extras keep their names, so `bajutsu[server,db]` keeps
    resolving.
    """
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    base = {dep.split(">")[0].split("=")[0].split("[")[0] for dep in project["dependencies"]}
    assert {"fastapi", "uvicorn", "sqlalchemy", "alembic", "cryptography"} <= base
    assert "psycopg" not in base
    extras = project["optional-dependencies"]
    assert {dep.split(">")[0].split("[")[0] for dep in extras["db"]} == {"psycopg"}
    assert {dep.split(">")[0].split("[")[0] for dep in extras["server"]} == {"boto3"}

"""Shared helpers for the project-hub CLI (BE-0225 unit 5).

The `project` subcommands and `run --project` both read the same on-disk project store `serve`
exposes over HTTP, so opening it and translating between a `--config` spec and the stored
config-source record lives here — cross-command, so it stays out of any one `commands/<name>.py`.
`source_from_config` and `config_from_source` are inverses: the first is what `project add` records,
the second is what `run --project` feeds back to the ordinary run path.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bajutsu.serve.project_registry import ProjectRegistry


def open_registry(runs: str) -> ProjectRegistry:
    """The registry the CLI edits, sharing `serve`'s store.

    The DB `Repository` when `BAJUTSU_DATABASE_URL` is set, else the on-disk JSON store beside *runs*
    (the same store a local `serve` uses). Imported lazily to keep `bajutsu --help` off the serve
    import path.

    The CLI is local-first: unlike the API's `register_project`, it does not apply the deployment
    source-kind allowlist (BE-0108). That allowlist is `serve`-state policy guarding a browser
    client from the server's filesystem, whereas `file` is the CLI's primary source and its operator
    already has that access — so pointing the CLI at a hosted DB is an operator responsibility, not a
    screened path.
    """
    from bajutsu.serve.project_registry import LocalProjectRegistry, SqlProjectRegistry
    from bajutsu.serve.server.db import repository_from_env

    repository = repository_from_env()
    if repository is not None:
        return SqlProjectRegistry(repository)
    return LocalProjectRegistry(Path(runs).parent / "projects.json")


def source_from_config(config: str) -> dict[str, object]:
    """A config-source record (`kind` + `locator`) for *config*.

    A Git spec becomes a `git` source, anything else a local `file` source — the same discriminated
    shape unit 1 stores and `serve` auto-registers (`launch_project_identity`), so a CLI-registered
    project round-trips through run.
    """
    from bajutsu.config_source import parse_config_spec

    spec = parse_config_spec(config)
    if spec is None:
        return {"kind": "file", "locator": {"path": str(config)}}
    locator: dict[str, str] = {"host": spec.host, "owner": spec.owner, "repo": spec.repo}
    if spec.ref:
        locator["ref"] = spec.ref
    if spec.path:
        locator["path"] = spec.path
    return {"kind": "git", "locator": locator}


def config_from_source(source: object) -> str:
    """Reconstruct a `--config` spec from a stored config-source record.

    The inverse of `source_from_config`, so `run --project` drives the ordinary run path. A `git`
    source rebuilds the `github:` / `git+https://` spec, preferring the resolved `sha` (an immutable
    pin the launch auto-register stamps) over a moving `ref`. A `file` source is its path. An
    `upload` source is a hosted-only bundle with no local checkout, so it has no CLI equivalent.
    """
    if not isinstance(source, dict):
        raise ValueError(f"config source is not a record: {source!r}")
    kind = source.get("kind")
    locator = source.get("locator")
    if not isinstance(locator, dict):
        raise ValueError(f"config source has no locator: {source!r}")
    if kind == "file":
        path = locator.get("path")
        if path is None:
            raise ValueError(f"config source has no path: {source!r}")
        return str(path)
    if kind == "git":
        return _git_spec(locator)
    raise ValueError(f"cannot run a {kind!r} config source from the CLI (only git or file)")


def _git_spec(locator: dict[str, object]) -> str:
    """A `github:` / `git+https://` spec from a git locator, pinning `sha` when present."""
    missing = [k for k in ("host", "owner", "repo") if k not in locator]
    if missing:
        raise ValueError(f"git config source locator is missing {missing}: {locator!r}")
    host, owner, repo = locator["host"], locator["owner"], locator["repo"]
    ref = locator.get("sha") or locator.get("ref")
    path = locator.get("path")
    if host == "github.com":
        spec = f"github:{owner}/{repo}"
        if ref:
            spec += f"@{ref}"
        if path:
            spec += f":{path}"
        return spec
    spec = f"git+https://{host}/{owner}/{repo}"
    if ref:
        spec += f"@{ref}"
    if path:
        spec += f"#{path}"
    return spec

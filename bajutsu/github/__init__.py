"""GitHub integration, split by layer (BE-0257).

`actions` emits `bajutsu run`'s CI annotations + job summary (periphery — never on the verdict
path); `app` mints a GitHub App installation token for the private-repo config source; `errors`
holds the access error both `app` and `config_source` share. Only `GitHubAccessError` is re-exported
here — `actions` is imported directly by its one CLI caller so importing the package stays free of
the `orchestrator` it pulls in, keeping the package usable from the deterministic core (the
`crawl/__init__.py` pattern, which re-exports `core`/`serialize` but never `guide`).
"""

from __future__ import annotations

from bajutsu.github.errors import GitHubAccessError

__all__ = ["GitHubAccessError"]

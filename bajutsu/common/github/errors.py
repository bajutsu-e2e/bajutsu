"""The private-repo access error shared across the GitHub integration (BE-0224, BE-0257).

Its own module so both the App token path (`github.app`) and the config-source materialization
(`config_source`) import it without either importing the other — the cycle the flat
`config_source` ↔ `github_app` layout routed around with a deferred import.
"""

from __future__ import annotations


class GitHubAccessError(ValueError):
    """A private-repo credential could not be acquired, or a GitHub API call was rejected (BE-0224).

    Covers both a rejected API call (auth / rate-limit / SSO / not-found) and a failure to *mint* a
    credential (a broken GitHub App config, a missing key file, the `githubapp` extra absent). A
    `ValueError` so callers already catching fetch failures (`serve.bind_git_config`, the `--config`
    diagnostics) surface its message unchanged instead of a raw traceback.
    """

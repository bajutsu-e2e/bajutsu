"""Wire group-event delivery into a running `serve` (BE-0500)."""

from __future__ import annotations

import threading
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from .delivery import SnapshotFor, start_delivery_loop
from .github_check import GitHubCheckAdapter
from .group_sink import GITHUB_CHECK
from .snapshot import group_snapshot

if TYPE_CHECKING:
    from bajutsu.serve.server.db import GroupEventClaim
    from bajutsu.serve.state import ServeState


def start_group_notify(state: ServeState, env: Mapping[str, str]) -> threading.Thread | None:
    """Start delivering *state*'s group events; None when it defines no sink or has no store.

    Args:
        state: The server state holding the repository, the sinks, and the artifact stores.
        env: The environment holding the GitHub App credentials `load_group_sinks` checked.
    """
    if state.repository is None or not state.group_sinks:
        return None
    adapters = {
        GITHUB_CHECK: GitHubCheckAdapter(
            token_for=lambda repo: _app_token(env, repo), app_id=env.get("BAJUTSU_GITHUB_APP_ID")
        )
    }
    return start_delivery_loop(
        state.repository, state.group_sinks, adapters, snapshot_reader(state)
    )


def snapshot_reader(state: ServeState) -> SnapshotFor:
    """Build a `completed` snapshot from the group's jobs and its runs' manifests in *state*."""
    from bajutsu.serve.jobs import read_run_manifest

    def snapshot_for(claim: GroupEventClaim) -> dict[str, object]:
        assert state.repository is not None  # start_group_notify started no loop without one
        return group_snapshot(
            state.repository.group_jobs(claim.group_id, include_spec=True),
            lambda run_id: read_run_manifest(state, run_id, claim.org_id),
        )

    return snapshot_for


def _app_token(env: Mapping[str, str], repository: str) -> str:
    from bajutsu.common.github.app import repository_installation_token

    key = env.get("BAJUTSU_GITHUB_APP_PRIVATE_KEY") or Path(
        env["BAJUTSU_GITHUB_APP_PRIVATE_KEY_FILE"]
    ).read_text(encoding="utf-8")
    # Resolved per repository rather than from BAJUTSU_GITHUB_APP_INSTALLATION_ID, which pins the
    # config source's installation: a check may go to a repository of another installation.
    return repository_installation_token(env["BAJUTSU_GITHUB_APP_ID"], key, repository)

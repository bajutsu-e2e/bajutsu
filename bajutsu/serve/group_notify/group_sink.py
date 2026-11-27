"""The operator's group-notification sinks, read once when `serve` starts (BE-0500)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# The adapters this server knows; a sink naming any other format stops `serve` at startup.
GITHUB_CHECK = "github-check"
KNOWN_FORMATS = (GITHUB_CHECK,)


class GroupNotifyConfigError(Exception):
    """The sink file cannot be honoured, so `serve` refuses to start."""


class GroupSink(BaseModel):
    """One named destination for a group's events, and the format whose adapter delivers them.

    Attributes:
        name: What a `run-set` request names in its `notify` list.
        format: The adapter that turns an event into calls to one service.
        check_name: A `github-check` sink's check run name; `{target}` expands to the group's target.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    format: str
    check_name: str = Field(
        default="bajutsu / {target}", validation_alias="checkName", min_length=1
    )

    def check_name_for(self, target: str) -> str:
        """The check run name for a group of *target*."""
        return self.check_name.replace("{target}", target)


class _SinkFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groupNotify: list[GroupSink]  # noqa: N815 — the file's own key


def load_group_sinks(path: Path, env: Mapping[str, str]) -> dict[str, GroupSink]:
    """The sinks *path* defines, by name, checked against what this server can deliver.

    Args:
        path: The file `--group-notify-config` names.
        env: The environment holding the GitHub App credentials a `github-check` sink needs.

    Raises:
        GroupNotifyConfigError: The file is unreadable or malformed, names a format this server has
            no adapter for, repeats a name, or defines a `github-check` sink with no GitHub App.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise GroupNotifyConfigError(f"cannot read the group-notify config {path}: {e}") from e
    except yaml.YAMLError as e:
        raise GroupNotifyConfigError(f"the group-notify config {path} is not YAML: {e}") from e
    try:
        sinks = _SinkFile.model_validate(raw).groupNotify
    except ValidationError as e:
        raise GroupNotifyConfigError(f"the group-notify config {path} is malformed: {e}") from e
    by_name: dict[str, GroupSink] = {}
    for sink in sinks:
        if sink.format not in KNOWN_FORMATS:
            raise GroupNotifyConfigError(
                f"group-notify sink {sink.name!r} names the unknown format {sink.format!r} "
                f"(known: {', '.join(KNOWN_FORMATS)})"
            )
        if sink.name in by_name:
            raise GroupNotifyConfigError(f"group-notify sink {sink.name!r} is defined twice")
        by_name[sink.name] = sink
    if any(s.format == GITHUB_CHECK for s in sinks):
        _check_github_app(env)
    return by_name


def _check_github_app(env: Mapping[str, str]) -> None:
    key_file = env.get("BAJUTSU_GITHUB_APP_PRIVATE_KEY_FILE")
    if not env.get("BAJUTSU_GITHUB_APP_ID") or not (
        env.get("BAJUTSU_GITHUB_APP_PRIVATE_KEY") or key_file
    ):
        raise GroupNotifyConfigError(
            "a github-check sink needs the GitHub App: set BAJUTSU_GITHUB_APP_ID and "
            "BAJUTSU_GITHUB_APP_PRIVATE_KEY or BAJUTSU_GITHUB_APP_PRIVATE_KEY_FILE"
        )
    # Read and sign now, so a key that is missing, malformed, passphrase-protected, or not RSA (or a
    # missing `githubapp` extra) stops startup rather than every delivery.
    from bajutsu.common.github import GitHubAccessError
    from bajutsu.common.github.app import check_app_key

    key = env.get("BAJUTSU_GITHUB_APP_PRIVATE_KEY")
    if not key:
        try:
            key = Path(str(key_file)).read_text(encoding="utf-8")
        except OSError as e:
            raise GroupNotifyConfigError(
                f"BAJUTSU_GITHUB_APP_PRIVATE_KEY_FILE={key_file!r} names no readable file"
            ) from e
    try:
        check_app_key(env["BAJUTSU_GITHUB_APP_ID"], key)
    except GitHubAccessError as e:
        raise GroupNotifyConfigError(
            f"a github-check sink cannot sign with the configured GitHub App: {e}"
        ) from e

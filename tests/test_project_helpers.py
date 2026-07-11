"""BE-0225 unit 5: the shared project-CLI helpers in `bajutsu.cli._projects`.

`config_from_source` is the inverse of `source_from_config` — it reconstructs the `--config` spec a
`run --project` invocation feeds the ordinary run path from a project's stored config-source record.
The round-trip cases pin that the two stay each other's inverse.
"""

from __future__ import annotations

import pytest

from bajutsu.cli._projects import config_from_source, source_from_config


def test_file_source_round_trips_to_its_path() -> None:
    assert config_from_source(source_from_config("shop.config.yaml")) == "shop.config.yaml"


def test_github_shorthand_round_trips() -> None:
    spec = "github:acme/shop@main:configs/e2e.yaml"
    assert config_from_source(source_from_config(spec)) == spec


def test_github_bare_repo_round_trips() -> None:
    assert config_from_source(source_from_config("github:acme/shop")) == "github:acme/shop"


def test_git_url_host_round_trips() -> None:
    spec = "git+https://gitlab.example.com/acme/shop@v1#configs/e2e.yaml"
    assert config_from_source(source_from_config(spec)) == spec


def test_git_source_prefers_the_pinned_sha_over_a_moving_ref() -> None:
    # A launch-auto-registered git source stores the resolved `sha` (the provenance stamp) alongside
    # the moving `ref`; running it must pin the immutable commit, not the branch.
    source = {
        "kind": "git",
        "locator": {
            "host": "github.com",
            "owner": "acme",
            "repo": "shop",
            "ref": "main",
            "sha": "a" * 40,
        },
    }
    assert config_from_source(source) == f"github:acme/shop@{'a' * 40}"


def test_upload_source_cannot_be_resolved_on_the_cli() -> None:
    source = {"kind": "upload", "locator": {"path": "bundle.zip"}}
    with pytest.raises(ValueError, match="upload"):
        config_from_source(source)


def test_file_source_without_a_path_raises_value_error() -> None:
    # Nothing upstream validates the locator's shape (the API's `_validate_source` only screens
    # `kind`), so a hand-edited store can persist `{"kind": "file", "locator": {}}`. `run --project`
    # only catches ValueError, so a raw KeyError here would crash it with an unhandled traceback.
    with pytest.raises(ValueError, match="path"):
        config_from_source({"kind": "file", "locator": {}})


def test_git_source_missing_a_locator_field_raises_value_error() -> None:
    with pytest.raises(ValueError, match="missing"):
        config_from_source({"kind": "git", "locator": {"owner": "acme", "repo": "shop"}})

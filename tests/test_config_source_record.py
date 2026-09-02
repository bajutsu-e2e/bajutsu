"""The `{kind, locator}` record naming where a bound configuration came from (BE-0393 unit 6).

The record is written by every bind and read back to restore an org's configuration into a session,
so the two directions have to be an exact pair: a record that cannot be turned back into a
`--config` value is a record nothing can restore from. It reaches the org row from a client-shaped
bind, so the reader treats every field as untrusted and answers None rather than raising — its
caller is a best-effort restore that must degrade to "no binding", never fail the request.
"""

from __future__ import annotations

import pytest

from bajutsu.common.config_source import (
    config_source_record,
    config_spec_from_record,
    parse_config_spec,
)


@pytest.mark.parametrize(
    "spec",
    [
        "github:acme/shop",
        "github:acme/shop@main",
        "github:acme/shop@main:web.yaml",
        "github:acme/shop:apps/web.yaml",
        "git+https://git.example.com/acme/shop@v1:a/b.yaml",
    ],
)
def test_a_git_spec_round_trips_through_the_record(spec: str) -> None:
    record = config_source_record(parse_config_spec(spec), spec)
    assert record["kind"] == "git"
    assert config_spec_from_record(record) == spec


def test_a_git_record_keeps_the_ref_rather_than_a_resolved_commit() -> None:
    # Restoring an org later should follow the branch the member chose, which is what a moving ref
    # means — so the record carries `ref`, never the sha the bind resolved it to.
    record = config_source_record(
        parse_config_spec("github:acme/shop@main"), "github:acme/shop@main"
    )
    assert record["locator"] == {
        "host": "github.com",
        "owner": "acme",
        "repo": "shop",
        "ref": "main",
    }


def test_a_local_path_round_trips_as_a_file_record() -> None:
    record = config_source_record(None, "/apps/checkout.yaml")
    assert record == {"kind": "file", "locator": {"path": "/apps/checkout.yaml"}}
    assert config_spec_from_record(record) == "/apps/checkout.yaml"


@pytest.mark.parametrize(
    "source",
    [
        None,
        "not a record",
        {},
        {"kind": "git"},  # no locator
        {"kind": "git", "locator": "not a mapping"},
        {"kind": "git", "locator": {}},  # no host/owner/repo
        {"kind": "git", "locator": {"host": "github.com", "owner": "acme"}},  # no repo
        {"kind": "git", "locator": {"host": 1, "owner": "acme", "repo": "shop"}},  # not a string
        {"kind": "file", "locator": {}},  # no path
        {"kind": "file", "locator": {"path": ""}},  # empty path
        {"kind": "elsewhere", "locator": {"path": "/x.yaml"}},  # a kind with no spec form
    ],
)
def test_a_record_that_names_no_spec_answers_none(source: object) -> None:
    assert config_spec_from_record(source) is None


def test_an_upload_record_answers_none_because_it_has_no_spec_form() -> None:
    # An uploaded bundle is restored by digest, not by a `--config` value — the restore branches on
    # `kind` before it ever asks here, and this is the floor under that.
    assert config_spec_from_record({"kind": "upload", "sha256": "a" * 64}) is None

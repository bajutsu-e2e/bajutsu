"""Tests for the pure parts of the refresh-PR opener (BE-0222).

The git/``gh`` glue (fetching the remote tip, force-pushing the rolling branch, creating/editing the
Draft PR) isn't covered here — it calls the network and never runs inside ``make check``, the same
carve-out ``check_stale_roadmap_prs.py`` documents. These pin the parts that decide *what* the PR may
contain and *whether* it is safe to write: porcelain parsing, allowlist partitioning, the clobber
decision, and the body text's gate line.
"""

from __future__ import annotations

from scripts.refresh_pr import (
    Change,
    is_bot_authored,
    parse_porcelain,
    partition,
    pr_body,
)


def test_parse_porcelain_reads_modified_added_and_untracked() -> None:
    out = " M roadmaps/BE-0001-x/BE-0001-x.md\nA  roadmaps/README.md\n?? docs/new.md\n"
    assert parse_porcelain(out) == [
        Change("roadmaps/BE-0001-x/BE-0001-x.md", untracked=False),
        Change("roadmaps/README.md", untracked=False),
        Change("docs/new.md", untracked=True),
    ]


def test_parse_porcelain_takes_the_destination_of_a_rename() -> None:
    out = "R  roadmaps/old.md -> roadmaps/new.md\n"
    assert parse_porcelain(out) == [Change("roadmaps/new.md", untracked=False)]


def test_parse_porcelain_reads_a_deletion_as_tracked() -> None:
    # A deletion must be tracked so _restore re-materializes it with `git checkout`, not `unlink`.
    assert parse_porcelain(" D roadmaps/gone.md\n") == [Change("roadmaps/gone.md", untracked=False)]


def test_parse_porcelain_ignores_blank_lines() -> None:
    assert parse_porcelain("\n   \n") == []


def test_partition_splits_by_glob_allowlist() -> None:
    changes = [
        Change("roadmaps/BE-0001-x/BE-0001-x.md", untracked=False),
        Change("docs/architecture.md", untracked=False),
        Change("bajutsu/driver.py", untracked=False),
    ]
    allowed, disallowed = partition(changes, ["roadmaps/**"])
    assert [c.path for c in allowed] == ["roadmaps/BE-0001-x/BE-0001-x.md"]
    assert [c.path for c in disallowed] == ["docs/architecture.md", "bajutsu/driver.py"]


def test_partition_matches_any_of_several_patterns() -> None:
    changes = [
        Change("docs/ja/architecture.md", untracked=False),
        Change("DESIGN.md", untracked=False),
        Change("README.md", untracked=False),
    ]
    allowed, disallowed = partition(changes, ["docs/**", "DESIGN.md"])
    assert [c.path for c in allowed] == ["docs/ja/architecture.md", "DESIGN.md"]
    assert [c.path for c in disallowed] == ["README.md"]


def test_partition_everything_disallowed_leaves_no_allowed() -> None:
    changes = [Change("bajutsu/run.py", untracked=False)]
    allowed, disallowed = partition(changes, ["roadmaps/**"])
    assert allowed == []
    assert [c.path for c in disallowed] == ["bajutsu/run.py"]


def test_partition_empty_allowlist_disallows_everything() -> None:
    # A misconfigured workflow with no --allow globs must be fail-safe: nothing is ever allowed,
    # so the run can only ever be a no-op — it can never open a PR carrying arbitrary paths.
    changes = [Change("roadmaps/x.md", untracked=False), Change("docs/y.md", untracked=True)]
    allowed, disallowed = partition(changes, [])
    assert allowed == []
    assert disallowed == changes


def test_is_bot_authored_true_when_branch_absent() -> None:
    # A branch that doesn't exist yet (None tip) is safe to create.
    assert is_bot_authored(None, "app[bot]@users.noreply.github.com") is True


def test_is_bot_authored_true_for_the_bots_own_tip() -> None:
    email = "app[bot]@users.noreply.github.com"
    assert is_bot_authored(email, email) is True


def test_is_bot_authored_false_for_a_human_tip() -> None:
    assert is_bot_authored("dev@example.com", "app[bot]@users.noreply.github.com") is False


def test_pr_body_reports_gate_pass_and_fail_distinctly() -> None:
    passed = pr_body("roadmap", "main", "pass", [])
    failed = pr_body("roadmap", "main", "fail", [])
    assert "passed" in passed and "failed" not in passed
    assert "failed" in failed
    # The body must make the human-merged, no-LLM-on-the-gate contract explicit (prime directive 1).
    assert "only a human" in passed.lower()
    assert "no llm is on the `run`/ci verdict path" in passed.lower()


def test_pr_body_surfaces_discarded_strays_to_the_reviewer() -> None:
    # A repeated attempt to reach product code is worth showing the human who is the sole merger.
    clean = pr_body("docs", "main", "pass", [])
    strayed = pr_body("docs", "main", "pass", ["bajutsu/run.py", "CLAUDE.md"])
    assert "attempted edits outside" not in clean
    assert "attempted edits outside" in strayed
    assert "`bajutsu/run.py`" in strayed and "`CLAUDE.md`" in strayed

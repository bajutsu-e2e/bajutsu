"""Tests for the pure parts of the prose companion-PR opener (BE-0343).

The git/``gh`` glue (fetching the remote tip, force-pushing the companion branch, opening the PR,
replying to and resolving threads) isn't covered here — it calls the network and never runs inside
``make check``, the same carve-out ``test_refresh_pr.py`` documents. These pin the parts that decide
*whether a finding may be applied at all* — the author gate, the path allowlist — and *whether it
still fits the file*, plus the file rewrite itself and the text the job posts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from scripts.prose_companion_pr import (
    APPLIED_ALREADY,
    Finding,
    _apply_findings,
    apply_to_lines,
    extract_suggestion,
    findings_from_comments,
    is_allowed_path,
    parse_diff_hunk,
    pr_body,
    pr_number_from_url,
    reply_body,
)

_HUNK = "@@ -10,3 +10,4 @@ context\n old line\n+これは文です。\n-gone\n unchanged\n"


def _comment(**overrides: Any) -> dict[str, Any]:
    """A `(non-blocking, prose)` inline comment from the reviewer, overridable field by field."""
    base: dict[str, Any] = {
        "id": 501,
        "user": {"login": "github-actions[bot]", "type": "Bot"},
        "path": "docs/ja/guide.md",
        "line": 11,
        "start_line": None,
        "original_line": 11,
        "original_start_line": None,
        "side": "RIGHT",
        "in_reply_to_id": None,
        "diff_hunk": _HUNK,
        "body": (
            "🤖 **Claude Code** — suggestion (non-blocking, prose): 敬体に揃えます。\n"
            "```suggestion\nこれは文です。\n```"
        ),
    }
    base.update(overrides)
    return base


def test_parse_diff_hunk_numbers_the_post_image_and_drops_removed_lines() -> None:
    assert parse_diff_hunk(_HUNK) == {10: "old line", 11: "これは文です。", 12: "unchanged"}


def test_parse_diff_hunk_ignores_the_no_newline_marker() -> None:
    hunk = "@@ -1 +1,2 @@\n a\n+b\n\\ No newline at end of file\n"
    assert parse_diff_hunk(hunk) == {1: "a", 2: "b"}


def test_extract_suggestion_reads_the_replacement_lines() -> None:
    body = "note\n```suggestion\nfirst\nsecond\n```\ntrailing"
    assert extract_suggestion(body) == ("first", "second")


def test_extract_suggestion_reads_an_empty_block_as_a_deletion() -> None:
    # An empty tuple (delete these lines) must stay distinct from None (nothing to apply at all).
    assert extract_suggestion("drop it\n```suggestion\n```\n") == ()


def test_extract_suggestion_keeps_a_blank_line_distinct_from_a_deletion() -> None:
    # A block holding one empty line replaces the span with a blank line; it does not delete it.
    assert extract_suggestion("blank it\n```suggestion\n\n```\n") == ("",)


def test_extract_suggestion_is_none_without_a_block() -> None:
    assert extract_suggestion("🤖 **Claude Code** — question (non-blocking): why?") is None


def test_extract_suggestion_is_none_with_more_than_one_block() -> None:
    # Which block the finding meant is a judgment call, and guessing is what this must not do.
    body = "a\n```suggestion\nA\n```\nb\n```suggestion\nB\n```"
    assert extract_suggestion(body) is None


@pytest.mark.parametrize(
    "path",
    ["docs/architecture.md", "docs/ja/guide.md", "roadmaps/BE-0001-x/BE-0001-x-ja.md"],
)
def test_is_allowed_path_accepts_documentation_and_roadmap_prose(path: str) -> None:
    assert is_allowed_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "bajutsu/driver.py",  # product code — a mismarked finding must not land mechanically
        "CLAUDE.md",  # contract surface, deliberately out
        ".github/workflows/ci.yml",
        "docs/../../etc/passwd.md",  # traversal that fnmatch alone would let through
        "/etc/passwd.md",
    ],
)
def test_is_allowed_path_refuses_everything_else(path: str) -> None:
    assert not is_allowed_path(path)


def test_findings_from_comments_accepts_a_marked_finding_from_the_reviewer() -> None:
    findings, refused = findings_from_comments([_comment()])
    assert findings == [
        Finding(
            comment_id=501,
            path="docs/ja/guide.md",
            start_line=11,
            end_line=11,
            expected=("これは文です。",),
            replacement=("これは文です。",),
        )
    ]
    assert refused == []


def test_findings_from_comments_rejects_a_lookalike_from_another_account() -> None:
    # The prefix and the marker are text anyone can type: authorship is the gate, not the body.
    impostor = _comment(user={"login": "random-person", "type": "User"})
    assert findings_from_comments([impostor]) == ([], [])


def test_findings_from_comments_rejects_a_lookalike_from_a_non_bot_of_the_same_name() -> None:
    assert findings_from_comments(
        [_comment(user={"login": "github-actions[bot]", "type": "User"})]
    ) == ([], [])


def test_findings_from_comments_refuses_a_path_outside_the_allowlist() -> None:
    findings, refused = findings_from_comments([_comment(path="bajutsu/driver.py")])
    assert findings == []
    assert [(r.comment_id, r.path) for r in refused] == [(501, "bajutsu/driver.py")]


def test_findings_from_comments_spans_a_multi_line_finding() -> None:
    found, _ = findings_from_comments(
        [_comment(start_line=10, original_start_line=10, line=11, original_line=11)]
    )
    assert found[0].start_line == 10
    assert found[0].expected == ("old line", "これは文です。")


def test_findings_from_comments_accepts_a_comment_with_no_side_field() -> None:
    # The inline-comment tool omits `side` on a single-line comment, so this is the common shape.
    found, _ = findings_from_comments([_comment(side=None)])
    assert len(found) == 1


def test_findings_from_comments_refuses_a_span_that_no_longer_matches_the_posted_span() -> None:
    # `expected` is built from the original span; a current span of a different length could never
    # match the file, so emitting the finding would report it as drift forever.
    findings, refused = findings_from_comments([_comment(start_line=10, original_start_line=11)])
    assert findings == []
    assert len(refused) == 1


def test_findings_from_comments_rejects_an_unmarked_finding() -> None:
    # The plain `(non-blocking)` decoration is every other lens — design, security, correctness.
    unmarked = _comment(
        body="🤖 **Claude Code** — issue (non-blocking): fix\n```suggestion\nx\n```"
    )
    assert findings_from_comments([unmarked]) == ([], [])


def test_findings_from_comments_refuses_a_marked_finding_without_a_suggestion() -> None:
    marked = _comment(body="🤖 **Claude Code** — suggestion (non-blocking, prose): reword")
    findings, refused = findings_from_comments([marked])
    assert findings == []
    assert "suggestion" in refused[0].reason


def test_findings_from_comments_rejects_a_reply() -> None:
    assert findings_from_comments([_comment(in_reply_to_id=42)]) == ([], [])


def test_findings_from_comments_refuses_an_outdated_comment() -> None:
    # GitHub nulls `line` once the comment's position no longer exists in the diff.
    findings, refused = findings_from_comments([_comment(line=None)])
    assert findings == []
    assert "outdated" in refused[0].reason


def test_findings_from_comments_rejects_a_left_side_comment() -> None:
    assert findings_from_comments([_comment(side="LEFT")]) == ([], [])


def test_findings_from_comments_refuses_a_span_its_hunk_does_not_cover() -> None:
    # Without the posting-time text there is nothing to verify the file against, so it cannot be
    # applied safely — refuse rather than splice blind.
    findings, refused = findings_from_comments([_comment(line=99, original_line=99)])
    assert findings == []
    assert len(refused) == 1


def _finding(**overrides: Any) -> Finding:
    base: dict[str, Any] = {
        "comment_id": 1,
        "path": "docs/guide.md",
        "start_line": 2,
        "end_line": 2,
        "expected": ("old",),
        "replacement": ("new",),
    }
    base.update(overrides)
    return Finding(**base)


def test_apply_to_lines_splices_a_matching_finding() -> None:
    lines, applied, refused = apply_to_lines(["a", "old", "b"], [_finding()])
    assert lines == ["a", "new", "b"]
    assert [f.comment_id for f in applied] == [1]
    assert refused == []


def test_apply_to_lines_refuses_a_finding_whose_line_has_changed() -> None:
    lines, applied, refused = apply_to_lines(["a", "edited since", "b"], [_finding()])
    assert lines == ["a", "edited since", "b"]
    assert applied == []
    assert "edited that line since" in refused[0].reason


def test_apply_to_lines_names_an_already_applied_finding_as_such_not_as_drift() -> None:
    # The steady state after a companion PR merges: the fix is on the source branch already.
    _, applied, refused = apply_to_lines(["a", "new", "b"], [_finding()])
    assert applied == []
    assert refused[0].reason == APPLIED_ALREADY


def test_apply_to_lines_refuses_a_finding_past_the_end_of_the_file() -> None:
    _, applied, refused = apply_to_lines(["a"], [_finding(start_line=9, end_line=9)])
    assert applied == []
    assert "past the file's end" in refused[0].reason


def test_apply_to_lines_applies_bottom_up_so_earlier_findings_keep_their_line_numbers() -> None:
    # The upper finding grows the file by a line; applied top-down it would shift the lower one.
    findings = [
        _finding(comment_id=1, start_line=1, end_line=1, expected=("a",), replacement=("a1", "a2")),
        _finding(comment_id=2, start_line=3, end_line=3, expected=("c",), replacement=("C",)),
    ]
    lines, applied, refused = apply_to_lines(["a", "b", "c"], findings)
    assert lines == ["a1", "a2", "b", "C"]
    assert sorted(f.comment_id for f in applied) == [1, 2]
    assert refused == []


def test_apply_to_lines_refuses_an_overlapping_finding_rather_than_corrupting_the_file() -> None:
    findings = [
        _finding(comment_id=1, start_line=2, end_line=3, expected=("b", "c"), replacement=("X",)),
        _finding(comment_id=2, start_line=3, end_line=3, expected=("c",), replacement=("Y",)),
    ]
    lines, applied, refused = apply_to_lines(["a", "b", "c"], findings)
    assert lines == ["a", "b", "Y"]  # the lower finding won; the overlapping one was refused
    assert [f.comment_id for f in applied] == [2]
    assert [r.comment_id for r in refused] == [1]


def test_apply_to_lines_applies_a_deletion() -> None:
    lines, applied, _ = apply_to_lines(["a", "old", "b"], [_finding(replacement=())])
    assert lines == ["a", "b"]
    assert len(applied) == 1


def test_apply_findings_rewrites_the_file_and_keeps_its_trailing_newline(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    target = tmp_path / "docs" / "guide.md"
    target.write_text("a\nold\nb\n", encoding="utf-8")
    applied, refused = _apply_findings(str(tmp_path), [_finding()])
    assert [f.comment_id for f in applied] == [1]
    assert refused == []
    assert target.read_text(encoding="utf-8") == "a\nnew\nb\n"


def test_apply_findings_leaves_the_file_untouched_when_every_finding_is_refused(
    tmp_path: Path,
) -> None:
    # A phantom rewrite here would make the caller's `git status` check commit a no-op change.
    (tmp_path / "docs").mkdir()
    target = tmp_path / "docs" / "guide.md"
    target.write_text("a\nedited since\nb\n", encoding="utf-8")
    applied, refused = _apply_findings(str(tmp_path), [_finding()])
    assert applied == []
    assert len(refused) == 1
    assert target.read_text(encoding="utf-8") == "a\nedited since\nb\n"


def test_apply_findings_refuses_a_path_missing_from_the_head(tmp_path: Path) -> None:
    applied, refused = _apply_findings(str(tmp_path), [_finding()])
    assert applied == []
    assert "not in the pull request's head" in refused[0].reason


def test_apply_findings_groups_findings_across_several_files(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("a\nold\nb", encoding="utf-8")
    (tmp_path / "docs" / "other.md").write_text("x\nold\ny", encoding="utf-8")
    findings = [_finding(), _finding(comment_id=2, path="docs/other.md")]
    applied, refused = _apply_findings(str(tmp_path), findings)
    assert sorted(f.comment_id for f in applied) == [1, 2]
    assert refused == []
    assert (tmp_path / "docs" / "other.md").read_text(encoding="utf-8") == "x\nnew\ny"


def test_pr_body_names_the_source_pr_and_the_applied_findings() -> None:
    body = pr_body(77, [_finding()], [])
    assert "#77" in body
    assert "`docs/guide.md`:2" in body
    assert "No LLM ran to produce this." in body
    assert "Not applied" not in body


def test_pr_body_records_a_refused_finding_rather_than_hiding_it() -> None:
    from scripts.prose_companion_pr import Refused

    body = pr_body(77, [_finding()], [Refused(2, "docs/other.md", "the file is not in the head")])
    assert "Not applied" in body
    assert "`docs/other.md`" in body
    assert "the file is not in the head" in body


def test_pr_body_does_not_report_an_already_applied_finding_as_a_problem() -> None:
    from scripts.prose_companion_pr import Refused

    body = pr_body(77, [_finding()], [Refused(2, "docs/other.md", APPLIED_ALREADY)])
    assert "Not applied" not in body


def test_reply_body_names_the_companion_pr() -> None:
    assert "#312" in reply_body(312)


def test_pr_number_from_url_reads_ghs_create_output() -> None:
    assert pr_number_from_url("https://github.com/bajutsu-e2e/bajutsu/pull/1234\n") == 1234


def test_pr_number_from_url_raises_rather_than_guessing() -> None:
    with pytest.raises(ValueError, match="could not read a PR number"):
        pr_number_from_url("something went wrong")

"""Tests for the pure parts of the prose companion-PR opener (BE-0343).

The git/``gh`` glue (fetching the remote tip, force-pushing the companion branch, opening the PR,
replying to and resolving threads) isn't covered here — it calls the network and never runs inside
``make check``, the same carve-out ``test_refresh_pr.py`` documents. These pin the parts that decide
*which* findings may be applied and *whether* each one still fits the file: the marker filter, the
suggestion block, the hunk post-image, the apply/skip decision, and the body text.
"""

from __future__ import annotations

from typing import Any

from scripts.prose_companion_pr import (
    Finding,
    apply_to_lines,
    extract_suggestion,
    findings_from_comments,
    parse_diff_hunk,
    pr_body,
)

_HUNK = "@@ -10,3 +10,4 @@ context\n old line\n+これは文です。\n-gone\n unchanged\n"


def _comment(**overrides: Any) -> dict[str, Any]:
    """A `(non-blocking, prose)` inline comment, overridable field by field."""
    base: dict[str, Any] = {
        "id": 501,
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


def test_extract_suggestion_is_none_without_a_block() -> None:
    assert extract_suggestion("🤖 **Claude Code** — question (non-blocking): why?") is None


def test_findings_from_comments_accepts_a_marked_finding_with_a_suggestion() -> None:
    assert findings_from_comments([_comment()]) == [
        Finding(
            comment_id=501,
            path="docs/ja/guide.md",
            start_line=11,
            end_line=11,
            expected=("これは文です。",),
            replacement=("これは文です。",),
        )
    ]


def test_findings_from_comments_spans_a_multi_line_finding() -> None:
    found = findings_from_comments(
        [_comment(start_line=10, original_start_line=10, line=11, original_line=11)]
    )
    assert found[0].start_line == 10
    assert found[0].expected == ("old line", "これは文です。")


def test_findings_from_comments_rejects_an_unmarked_finding() -> None:
    # The plain `(non-blocking)` decoration is every other lens — design, security, correctness.
    unmarked = _comment(
        body="🤖 **Claude Code** — issue (non-blocking): fix\n```suggestion\nx\n```"
    )
    assert findings_from_comments([unmarked]) == []


def test_findings_from_comments_rejects_a_comment_from_another_author() -> None:
    human = _comment(body="Please reword this.\n```suggestion\nx\n```")
    assert findings_from_comments([human]) == []


def test_findings_from_comments_rejects_a_marked_finding_without_a_suggestion() -> None:
    assert (
        findings_from_comments(
            [_comment(body="🤖 **Claude Code** — suggestion (non-blocking, prose): reword")]
        )
        == []
    )


def test_findings_from_comments_rejects_a_reply() -> None:
    assert findings_from_comments([_comment(in_reply_to_id=42)]) == []


def test_findings_from_comments_rejects_an_outdated_comment() -> None:
    # GitHub nulls `line` once the comment's position no longer exists in the diff.
    assert findings_from_comments([_comment(line=None)]) == []


def test_findings_from_comments_rejects_a_left_side_comment() -> None:
    assert findings_from_comments([_comment(side="LEFT")]) == []


def test_findings_from_comments_rejects_a_span_its_hunk_does_not_cover() -> None:
    # Without the posting-time text there is nothing to verify the file against, so it cannot be
    # applied safely — skip rather than splice blind.
    assert findings_from_comments([_comment(line=99, original_line=99)]) == []


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
    lines, applied, skipped = apply_to_lines(["a", "old", "b"], [_finding()])
    assert lines == ["a", "new", "b"]
    assert [f.comment_id for f in applied] == [1]
    assert skipped == []


def test_apply_to_lines_skips_a_finding_whose_line_has_changed() -> None:
    lines, applied, skipped = apply_to_lines(["a", "edited since", "b"], [_finding()])
    assert lines == ["a", "edited since", "b"]
    assert applied == []
    assert [f.comment_id for f in skipped] == [1]


def test_apply_to_lines_skips_a_finding_past_the_end_of_the_file() -> None:
    _, applied, skipped = apply_to_lines(["a"], [_finding(start_line=9, end_line=9)])
    assert applied == []
    assert len(skipped) == 1


def test_apply_to_lines_applies_bottom_up_so_earlier_findings_keep_their_line_numbers() -> None:
    # The upper finding grows the file by a line; applied top-down it would shift the lower one.
    findings = [
        _finding(comment_id=1, start_line=1, end_line=1, expected=("a",), replacement=("a1", "a2")),
        _finding(comment_id=2, start_line=3, end_line=3, expected=("c",), replacement=("C",)),
    ]
    lines, applied, skipped = apply_to_lines(["a", "b", "c"], findings)
    assert lines == ["a1", "a2", "b", "C"]
    assert sorted(f.comment_id for f in applied) == [1, 2]
    assert skipped == []


def test_apply_to_lines_applies_a_deletion() -> None:
    lines, applied, _ = apply_to_lines(["a", "old", "b"], [_finding(replacement=())])
    assert lines == ["a", "b"]
    assert len(applied) == 1


def test_pr_body_names_the_source_pr_and_the_applied_findings() -> None:
    body = pr_body(77, [_finding()], [])
    assert "#77" in body
    assert "`docs/guide.md`:2" in body
    assert "No LLM ran to produce this." in body
    assert "Not applied" not in body


def test_pr_body_records_a_skipped_finding_rather_than_hiding_it() -> None:
    body = pr_body(77, [_finding()], [_finding(comment_id=2, path="docs/other.md", start_line=9)])
    assert "Not applied" in body
    assert "`docs/other.md`:9" in body

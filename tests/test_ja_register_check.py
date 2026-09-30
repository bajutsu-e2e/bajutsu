"""Tests for the 敬体 register-drift detector (BE-0434).

The dev environment never installs the morphological analyzer — the script pulls it in through its
PEP 723 block — so these drive the pre-scan exclusion, the sentence split, and the candidate logic
through a fake tokenizer that knows a handful of endings.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ja_register_check import (
    Morpheme,
    collect_paths,
    find_candidates,
    in_scope,
    is_keitai,
    main,
    prose_blocks,
    split_sentences,
    strip_trailing,
)


def _noun(surface: str) -> Morpheme:
    return Morpheme(surface, surface, surface, ("名詞", "普通名詞"))


def _aux(surface: str, lemma: str) -> Morpheme:
    return Morpheme(surface, lemma, lemma, ("助動詞", "*"))


def _verb(surface: str, lemma: str, normalized: str | None = None) -> Morpheme:
    return Morpheme(surface, lemma, normalized or lemma, ("動詞", "非自立可能"))


_FINAL_KA = Morpheme("か", "か", "か", ("助詞", "終助詞"))
_PERIOD = Morpheme("。", "。", "。", ("補助記号", "句点"))

# Longest ending first, so ました is not read as an unrelated た.
_ENDINGS: list[tuple[str, list[Morpheme]]] = [
    ("ください", [_verb("ください", "くださる", "下さる")]),
    ("ました", [_aux("まし", "ます"), _aux("た", "た")]),
    ("します", [_verb("し", "する"), _aux("ます", "ます")]),
    ("である", [_aux("で", "だ"), _verb("ある", "ある")]),
    ("です", [_aux("です", "です")]),
    ("する", [_verb("する", "する")]),
]


def _fake(text: str) -> list[Morpheme]:
    for ending, tail in _ENDINGS:
        if text.endswith(ending):
            head = text[: -len(ending)]
            return ([_noun(head)] if head else []) + tail
    return [_noun(text)]


# --- is_keitai ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("morphemes", "expected"),
    [
        # ました splits into まし + た: the ます lemma sits one morpheme before the end.
        ([_noun("変更"), _verb("し", "する"), _aux("まし", "ます"), _aux("た", "た")], True),
        ([_noun("変更"), _verb("し", "する"), _aux("ませ", "ます"), _aux("ん", "ぬ")], True),
        ([_noun("結果"), _aux("です", "です"), _FINAL_KA], True),
        ([_noun("登録"), _verb("し", "する"), _aux("ます", "ます"), _PERIOD], True),
        ([_verb("読ん", "読む"), _verb("ください", "くださる", "下さる")], True),
        # The plain くださる shares the lemma but is 常体.
        ([_noun("先生"), _verb("くださる", "くださる", "下さる")], False),
        ([_noun("結果"), _aux("で", "だ"), _verb("ある", "ある")], False),
        ([_noun("変更"), _verb("する", "する")], False),
        ([_noun("変更"), _verb("し", "する"), _aux("た", "た")], False),
        ([_noun("ラベル")], False),
        ([], False),
    ],
)
def test_is_keitai(morphemes: list[Morpheme], expected: bool) -> None:
    assert is_keitai(morphemes) is expected


def test_is_keitai_only_reads_the_trailing_auxiliary_chain() -> None:
    # A ます earlier in the sentence does not make a 常体 ending 敬体.
    sentence = [_aux("ます", "ます"), _noun("場合"), _verb("する", "する")]
    assert not is_keitai(sentence)


# --- strip_trailing -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("登録します（BE-0089）。", "登録します"),
        ("登録します（外側（内側））", "登録します"),
        ("登録します (see below).", "登録します"),
        ("登録します（注）（BE-0089）。", "登録します"),
        ("**登録します**。", "**登録します"),
        ("[リンク](https://example.com)を参照します。", "リンクを参照します"),
        ("![図](a.png)図を参照します。", "図を参照します"),
        ("閉じていない）", "閉じていない）"),
        # A sentence that is wholly a parenthetical is judged on its content, not dropped.
        ("（これは補足である。）", "これは補足である"),
        ("**（補足です）**。", "補足です"),
        ("。", ""),
    ],
)
def test_strip_trailing(sentence: str, expected: str) -> None:
    assert strip_trailing(sentence) == expected


# --- prose_blocks ---------------------------------------------------------------------------


def _texts(markdown: str) -> list[str]:
    return [b.text for b in prose_blocks(markdown)]


def test_prose_blocks_drop_everything_the_register_rule_exempts() -> None:
    markdown = "\n".join(
        [
            "[English](a.md) · **日本語**",
            "# 見出し",
            "| 表 | 値 |",
            "> 引用です",
            "```",
            "コードである",
            "```",
            "~~~yaml",
            "key: 値",
            "~~~",
            "<!-- 複数行の",
            "コメント -->",
            "<!-- 一行のコメント -->",
            "<details>",
            "<summary>ソース</summary>",
            '<p align="center"><img src="a.png"></p>',
            "[ref]: https://example.com",
            "本文です。",
        ]
    )
    assert _texts(markdown) == ["本文です。"]


def test_prose_blocks_keep_prose_that_only_looks_structural() -> None:
    markdown = "\n".join(
        [
            "Issue",
            "#1842 で計測した。",
            "<https://example.com> を参照する。",
            "<kbd>Ctrl</kbd> を押す。",
            "<!-- 注 --> 本文である。",
        ]
    )
    assert _texts(markdown) == [
        "Issue #1842 で計測した。<https://example.com> を参照する。<kbd>Ctrl</kbd> を押す。",
        "本文である。",
    ]


def test_comments_spanning_or_inside_a_line_hide_only_themselves() -> None:
    markdown = "\n".join(
        [
            "<!-- 複数行の",
            "注 --> 閉じた後の本文である。",
            "",
            "<!-- a --> 前の本文である。<!-- b",
            "まだコメント",
            "-->",
            "",
            "文中 <!-- c --> の本文である。",
        ]
    )
    assert _texts(markdown) == [
        "閉じた後の本文である。",
        "前の本文である。",
        "文中  の本文である。",
    ]


def test_a_comment_opener_inside_inline_code_is_text() -> None:
    markdown = "コメントは `<!--` で始めます。\n\n後の本文である。\n"
    assert _texts(markdown) == ["コメントは `<!--` で始めます。", "後の本文である。"]


def test_text_inside_block_html_is_still_prose() -> None:
    found = find_candidates("<p>段落の本文である。</p>\n\n<br>続きである。\n", _fake)
    assert [c.sentence for c in found] == ["<p>段落の本文である。", "<br>続きである。"]


def test_the_untracked_fallback_also_splits_inside_balanced_groups() -> None:
    # A known over-flag, pinned so a change to it is deliberate.
    assert _split("`stray（注。補足）の文である。") == ["`stray（注。", "補足）の文である。"]


def test_a_fence_closes_only_on_a_bare_run_at_least_as_long() -> None:
    markdown = "\n".join(["````md", "```", "中のコードである", "```", "````", "外の本文である。"])
    assert _texts(markdown) == ["外の本文である。"]


def test_a_wrapped_hash_line_does_not_end_the_progress_section() -> None:
    markdown = "## 進捗\n\n前置きの\n#12 を参照します。\n\n- [ ] 検出器を追加する\n"
    assert _texts(markdown) == ["前置きの#12 を参照します。"]


def test_prose_blocks_join_hard_wraps_and_keep_the_source_line() -> None:
    blocks = prose_blocks("前置き\n\n一行目の文は\n二行目で終わります。\nuses `make`\ncheck here\n")
    assert [b.text for b in blocks] == [
        "前置き",
        # Japanese joins with no space; only two ASCII chars meeting across a wrap get one.
        "一行目の文は二行目で終わります。uses `make` check here",
    ]
    second = blocks[1]
    assert second.line_of[0] == 3
    assert second.line_of[second.text.index("二行目")] == 4


def test_prose_blocks_start_a_block_per_list_item() -> None:
    markdown = "- 一つ目です。\n  続きです。\n- 二つ目です。\n1. 番号付きです。\n"
    assert _texts(markdown) == ["一つ目です。続きです。", "二つ目です。", "番号付きです。"]


def test_progress_checklist_bullets_are_exempt_but_other_bullets_are_not() -> None:
    markdown = "\n".join(
        [
            "## 進捗",
            "- [ ] 検出器を追加する",
            "      継続行も対象外",
            "- [x] 変換した",
            "- ログ：本文として読む。",
            "",
            "### 小見出し",
            "- [X] 小見出しの下もまだ進捗",
            "",
            "## 参考",
            "- [ ] 進捗の外のチェックボックス",
        ]
    )
    assert _texts(markdown) == ["ログ：本文として読む。", "[ ] 進捗の外のチェックボックス"]


def test_english_progress_heading_is_exempt_too() -> None:
    assert _texts("## Progress\n\n- [ ] Add the detector\n") == []


# --- split_sentences ------------------------------------------------------------------------


def _split(text: str) -> list[str]:
    (block,) = prose_blocks(text)
    return [s.sentence for s in split_sentences(block)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("一文目です。二文目です！三文目？", ["一文目です。", "二文目です！", "三文目？"]),
        ("体言止めのラベル", ["体言止めのラベル"]),
        ("「引用。の中」では分けません。", ["「引用。の中」では分けません。"]),
        ("括弧（注。補足）の中も分けません。", ["括弧（注。補足）の中も分けません。"]),
        ("`a。b` の中も分けません。", ["`a。b` の中も分けません。"]),
        ("**強調します。** 次の文です。", ["**強調します。**", "次の文です。"]),
        ("全角ピリオドである．次の文です。", ["全角ピリオドである．", "次の文です。"]),
        # A stray backtick or bracket must not fold the rest of the block into one sentence.
        ("`a を使う。次に b を使う。最後です。", ["`a を使う。", "次に b を使う。", "最後です。"]),
        ("（注 を使う。次に b を使う。", ["（注 を使う。", "次に b を使う。"]),
    ],
)
def test_split_sentences(text: str, expected: list[str]) -> None:
    assert _split(text) == expected


def test_split_sentences_report_the_line_each_sentence_starts_on() -> None:
    (block,) = prose_blocks("一文目です。二文目は\n次の行へ続きます。三文目は\n三行目です。")
    assert [s.line for s in split_sentences(block)] == [1, 1, 2]


# --- find_candidates ------------------------------------------------------------------------


def test_find_candidates_flags_only_non_keitai_japanese_sentences() -> None:
    markdown = "\n".join(
        [
            "登録します（BE-0089）。結果である。",
            "変更しました。",
            "",
            "- 体言止めのラベル",
            "- 読んでください",
            "",
            "English only sentence.",
            "",
            "## 進捗",
            "- [ ] 検出器を追加する",
        ]
    )
    found = find_candidates(markdown, _fake)
    assert [(c.line, c.sentence) for c in found] == [
        (1, "結果である。"),
        (4, "体言止めのラベル"),
    ]


# --- paths and CLI --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("roadmaps/BE-0089-x/BE-0089-x-ja.md", True),
        ("roadmaps/BE-0089-x/BE-0089-x.md", False),
        ("docs/ja/cli.md", True),
        ("docs/cli.md", False),
        ("docs/ja/notes.txt", False),
    ],
)
def test_in_scope(path: str, expected: bool) -> None:
    assert in_scope(Path(path)) is expected


def test_collect_paths_expands_directories_to_in_scope_markdown(tmp_path: Path) -> None:
    item = tmp_path / "roadmaps" / "BE-0001-x"
    item.mkdir(parents=True)
    (item / "BE-0001-x.md").write_text("en", encoding="utf-8")
    ja = item / "BE-0001-x-ja.md"
    ja.write_text("ja", encoding="utf-8")
    explicit = tmp_path / "any.md"
    explicit.write_text("x", encoding="utf-8")

    # An explicit file is scanned as given, even outside the directory scope.
    assert collect_paths([str(item), str(explicit)]) == [ja, explicit]
    with pytest.raises(FileNotFoundError):
        collect_paths([str(tmp_path / "missing")])


def test_collect_paths_defaults_to_the_whole_ja_corpus() -> None:
    paths = collect_paths([])
    assert paths
    assert all(in_scope(p) for p in paths)
    assert any(p.name == "BE-0089-merge-time-be-id-allocation-ja.md" for p in paths)


def test_main_reports_candidates_per_file_and_total(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    drifted = tmp_path / "a-ja.md"
    drifted.write_text("結果である。\n\n登録します。\n", encoding="utf-8")
    clean = tmp_path / "b-ja.md"
    clean.write_text("登録します。\n", encoding="utf-8")

    assert main([str(drifted), str(clean)], tokenize=_fake) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].endswith("a-ja.md:1: 結果である。")
    assert out[1].endswith("a-ja.md: 1 candidate(s)")
    assert out[2] == "Total: 1 candidate(s) in 1 of 2 file(s)"

    assert main(["--summary", str(drifted)], tokenize=_fake) == 0
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2
    assert out[0].endswith("a-ja.md: 1 candidate(s)")


def test_main_rejects_a_missing_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(tmp_path / "missing")], tokenize=_fake) == 2
    assert "no such file or directory" in capsys.readouterr().err

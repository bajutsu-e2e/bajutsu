---
name: japanese-document-writing
model: sonnet
description: 日本語の技術文書を執筆、翻訳、推敲するときに、Bajutsuの日本語文章規範を適用する。document-writingと併用する。
---

<!--
このスキルは k16shikano 氏の gist を元に、本リポジトリ向けに改変したものです。
出典: https://gist.github.com/k16shikano/fd287c3133457c4fd8f5601d34aa817d
-->

# 日本語の技術文書の文章規範

日本語で技術的な原稿（書籍の章、記事、解説文）を書く・推敲するときは、本スキルの規範に従う。

本スキルは、言語に依存しない執筆規範 [`document-writing`](../../../.apm/skills/document-writing/SKILL.md)
の下位に位置する日本語レイヤーである。同じ傘の下には、対になる英語レイヤー
[`english-document-writing`](../../../.apm/skills/english-document-writing/SKILL.md) がある。日本語の散文を書くときは
`document-writing` と本スキルの両方を適用し、重なる部分（冗語、重複、演出の抑制）は本スキルの記述を
優先する。上位規範は、上から下への推敲、文の強調位置、主語と述語の近接、能動態といった、言語に
依存しない技法を定める。

## 規範の読み込み

規範の本体は [`references/`](references/) に置いた3つのファイルに分かれている。**執筆、翻訳、推敲の
いずれであっても、書き始める前に3つとも読む。**

- [`references/formatting.md`](references/formatting.md)：整形と見出しの付け方
- [`references/argument.md`](references/argument.md)：段落の構成、論証の厳密さ、読み手の負荷の管理
- [`references/expression.md`](references/expression.md)：視点と語り、演出の抑制、LLM 口調の排除、冗長の排除、読者への誠実さ

必要になった段階で1つずつ読む形は採らない。どの手順がどの規範を必要とするかという対応が規範の側に
なく、一部だけを適用したまま規範に従ったつもりになれてしまうからである。

文章を書き上げたら、規範による推敲に加えて、[textlint](https://github.com/textlint/textlint) による
機械的な検証を**必ず**通す。手順と、指摘がすべて消えるまで推敲を繰り返す約束は、末尾の
[「推敲後の textlint 検証（必須）」](#推敲後の-textlint-検証必須)に置く。
相反する規範については、textlint のルールを優先する。

## 文体（敬体・常体）

文書の種別で文体を選ぶ。このリポジトリのドキュメント（`docs/ja/`）とロードマップ項目（`*-ja.md`）は、
敬体（ですます調）で書く。常体（だ・である調）は使わない。書籍・記事の原稿は、その媒体の慣例に従う
（多くは常体）。どちらの場合も、1つの文書のなかで敬体と常体を混在させない。敬体にするのは文末の述語だけで、
連体修飾節や接続・条件の形（「〜する場合」「〜すると」「〜であり」）は常体のままにする。見出しや純粋な
体言止めのラベルには繋辞を付けない。

## 箇条書きも敬体を崩さない

箇条書き・番号付きリストの項目も、文書本体と同じ文体で書く。`docs/ja/`とロードマップ項目では敬体である。
`preset-ja-technical-writing`の`no-mix-dearu-desumasu`は、リスト項目を常体で書くことをデフォルトで
要求する。このルールは、このリポジトリの敬体統一方針と両立しない。そのため
[`tools/textlint/.textlintrc.json`](../../../tools/textlint/.textlintrc.json)で無効化してある。理由は
[`tools/textlint/README.md`](../../../tools/textlint/README.md)に書いてある。**指摘が出ないからといって、
リストだけ常体に変えてはならない。** 常体を避けるという本スキルの規範が、このルールより優先する。

## 一文を短く、読点を絞る

このリポジトリのtextlintは、1文の長さと読点の数にも上限を課す。`ja-technical-writing/sentence-length`は、
1文が100字前後を超えると指摘する。`ja-technical-writing/max-ten`は、1文の読点が4つ以上だと指摘する。
上から下へ推敲を進めるときは、最初から短い一文で書く。読点も少なく抑える。「〜であり、〜のため、〜する」
のように節をいくつもつなぐ文は、分割の対象である。複数の文に分けるか、箇条書きに置き換える。

同じ助詞を1文の中で2回使うと、`ja-technical-writing/no-doubled-joshi`が指摘する。「に」「も」「にも」が
典型である。「AとBのどちらにも」のように言い換えれば、並列強調の意味を保ったまま指摘を避けられることが
多い。

## 推敲後の textlint 検証（必須）

textlint による機械的な検証は、英語・日本語のどちらの文章にも共通の実行環境として
[`document-writing`](../../../.apm/skills/document-writing/SKILL.md#mandatory-textlint-verification-after-drafting)
スキルに一本化されている。インストール、実行コマンド、`--fix` の扱い、ルールの変え方は、すべて
そちらに書いてある。

書き上げた日本語は、最後に必ず [textlint](https://github.com/textlint/textlint) にかけ、指摘が
ゼロになるまで推敲と再実行を繰り返す。指摘が残ったまま完了としない。textlint はあくまで機械的な
下限であって、これを通しただけで上の規範を満たしたことにはならない。規範と textlint が相反する
ときは textlint を優先し、指摘は設定を緩めてではなく散文を直して消す。設定ファイル
[`tools/textlint/.textlintrc.json`](../../../tools/textlint/.textlintrc.json) は、
日本語の技術文書向けの定番プリセット `textlint-rule-preset-ja-technical-writing` を含む、日本語
向けのルールを有効にしている。

[English](BE-0286-glossary-link-requirement.md) · **日本語**

# BE-0286 — 用語集の用語を初出時にリンクする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0286](BE-0286-glossary-link-requirement-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0286") |
| 実装 PR | [#1179](https://github.com/bajutsu-e2e/bajutsu/pull/1179) |
| トピック | コントリビューターワークフロー |
| 関連 | [BE-0213](../BE-0213-glossary-and-docs-structure/BE-0213-glossary-and-docs-structure-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本提案は、次のルールを `CLAUDE.md` と `docs/ai-development.md` の恒常的な規約にします。BE
ロードマップ項目や `docs/` 配下のページが [`docs/glossary.md`](../../docs/glossary.md)（日本語版は
[`docs/ja/glossary.md`](../../docs/ja/glossary.md)）の用語を Bajutsu 固有の意味で使うときは、
そのページで最初に実質的に言及する箇所を、用語の説明をその場で書き直すのではなく、用語集の該当項目
へのリンクにする、というルールです。このルール自体は目新しいものではありません。
[BE-0213](../BE-0213-glossary-and-docs-structure/BE-0213-glossary-and-docs-structure-ja.md) が
用語集を作った際に一度だけ文章にしていたものの、恒常的な規約としては機能してきませんでした
（なぜ機能してこなかったかは次の「動機」節で説明します）。

具体的には、この規約を `CLAUDE.md` と `docs/ai-development.md` の「文書の文体」節に追加し、両
ドキュメントがすでに持つバイリンガル文書の規約や DESIGN.md 整合性の規約と並べます。あわせて、
用語集の項目を説明していながらまだリンクを貼っていない `docs/` 配下のページを埋め戻します。

## 動機

「はじめに」のルールが機能してこなかった理由は、それが書かれていた場所にあります。BE-0213 は
用語集そのものを作った際、その詳細設計の中でこう書いていました。「既存のページは、用語が最初に
登場する箇所を用語集の該当項目にリンクし、その場で説明を繰り返さないようにします」。この一文は、用語集を作った PR
1回限りの編集判断を述べたものであり、以後の投稿者が従うべきルールとしては書かれていませんでした。
このプロジェクトが実際に運用する規約は `CLAUDE.md` と `docs/ai-development.md` に集まっており、
この一文はそのどちらにもなく、1つの項目の詳細設計の中に置かれていたためです。

その結果、`docs/` 側の欠落はほぼ全面的です。トップレベルページ27件（`glossary.md` 自身を除く）
のうち、`glossary.md` にリンクしているのは `overview.md`、`concepts.md`、`index.md`、
`README.md` の4件だけです。ある
用語クラスタを実質的に説明しているページに限ると、リンクは1件もありません。`drivers.md` は
driver/backend/actuator/platform を説明しています。`cli.md` は target/app/device と CLI の
各動詞を説明しています。`scenarios.md` は scenario/step/precondition/expect を説明しています。
`evidence.md` は evidence と `capturePolicy` を、`recording.md` は Tier 1 と `goal` を、
`selectors.md` は selector/identifier を説明しています。いずれのページも、それらの語を定義する
ページを指していません。`architecture.md` や `vision.md`、両方の `getting-started` ページも同様で、
`docs/ja/` 側も用語ごとに同じ欠落を映しています。

ロードマップ側の欠落はさらに徹底しています。英語と日本語それぞれ277件の BE 項目のうち、用語集を作った
BE-0213 自身を除いて、どの項目も `glossary.md` を参照していません。driver/backend/actuator/platform、
target/app/device、`trace`/`triage`、`capturePolicy` のいずれかの用語を使っている他の項目も、
その定義へのリンクを持たないまま書かれています。

ただし、この規約をすでに実践しているページが1つだけあります。
[`getting-started/web.md`](../../docs/getting-started/web.md) は語彙の初出で `glossary.md` に
リンクしており、本提案が求める書き方そのものです。ルールさえ明文化されていれば従うのは難しくないこと
を、このページが示しています。

それでも、ルールが書かれていない以上、このまま放置すれば同じ種類のずれが繰り返されます。BE-0213
自身の動機は、ページを横並びで棚卸ししたときに見つかった具体的なずれでした。`getting-started.md`
はシナリオを「test」と呼びました。`scenarios.md` は同じものを「scenario」と呼びました。両者の
食い違いを止める用語集は、当時まだなかったのです。公開ドキュメントサイト
（[BE-0093](../BE-0093-public-docs-site/BE-0093-public-docs-site.md)）はすでに公開済みで、ページや
ロードマップ項目は今後も増え続けます。新しいページが `capturePolicy` や
driver/backend/actuator/platform のクラスタをその都度自分の言葉で説明し直せば、用語集がすでに
定めた説明から少しずつずれる機会がそのたびに増えます。投稿者やエージェントがすでに探しに来る執筆
ルールの置き場所にこの規約を書いておくことが、この欠落を今後閉じる方法です。

## 詳細設計

作業は独立した2つの単位に分かれます。

1. **`CLAUDE.md` の「Conventions」一覧と、`docs/ai-development.md` の「Documentation style (every
   document, both languages)」節への規約追加。** 日本語版は `docs/ja/ai-development.md` の対応節
   「ドキュメントの書き方（全ドキュメント、両言語に適用）」に追加します。`CLAUDE.md` 側では、すでに
   隣接して並んでいる既存のバイリンガル文書の規約と
   [BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md) の DESIGN.md 整合性の
   規約のすぐ隣に置きます。`docs/ai-development.md` 側では、その節がすでに持つ自然な文章、独自の造語
   の禁止、略語の展開、敬体といった既存の規則と並べて追加します（この節自体にはバイリンガル文書の
   規約と BE-0113 の隣接関係はなく、その隣接は `CLAUDE.md` 側だけのものです）。文言は次の趣旨とします。
   「BE ロードマップ項目や `docs/` 配下のページの文章が `docs/glossary.md` で定義された用語を Bajutsu
   固有の意味で使うときは、そのページで最初に実質的に言及する箇所を、用語をその場で説明し直すのでは
   なく、用語集の該当項目（`glossary.md#アンカー` または `docs/ja/glossary.md#アンカー`）へのリンクに
   します」。あわせて、これが CI のゲートではなくレビュー時の規約であることを明記します。隣接する
   `CLAUDE.md` の2つの規約がそうである理由と同じで、「step」「target」のような普通の英単語の使用が
   Bajutsu 固有の意味を指しているかどうかの判断には人間の判断が必要であり、prime directive 1 が
   意味的な判断を `run` / CI のゲートから遠ざけているためです。
2. **上記の棚卸しで特定した `docs/` ページの埋め戻し**（両言語）。`drivers.md`、`cli.md`、`scenarios.md`、
   `evidence.md`、`recording.md`、`selectors.md`、`architecture.md`、`vision.md`、
   `getting-started/index.md`、`getting-started/ios.md` と、その `docs/ja/` 対訳版が対象です。編集は
   エージェントが1ページずつ手作業で行うのではなく、簡単な使い捨てスクリプトで機械的に適用し、機械的な
   作業部分のコストを抑えます。本提案の棚卸しからそのまま導ける「ファイル、初出箇所の厳密な文字列、
   用語集アンカー」の組を1ページにつき1件ずつ列挙したマニフェストを用意し、各エントリについて、
   ファイル中でその文字列が最初に現れた箇所を `glossary.md#アンカー`
   （または `docs/ja/glossary.md#アンカー`）への Markdown リンクで囲む短いスクリプトを書きます。
   「どの言及が実質的な言及か」という判断はマニフェストを書く一度きりの作業に集約し、それを適用する
   作業はページごとの判断ではなく決定的な文字列置換にします。用語の綴りがページ内でそれより前にも
   出てくる場合、厳密な文字列一致が誤った箇所に一致することがあるため、適用後の差分は必ず目視で
   確認します。このスクリプトは今回の埋め戻し作業のための使い捨ての実装手段であり、`make check` への
   恒久的な追加ではありません。適用する規約自体は、単位1で述べたレビュー時の規約のままです。

一方で、意図的にスコープ外とするのは、既存のロードマップ項目277件です。ほぼ全項目を英語と日本語の
両方で修正すると、大量のファイルを変更することになります。そのほとんどは完了済みで `Implemented`
になっている項目です。文書としての体裁を整えるだけの理由でそこまでの規模の変更をするのは、得られる
効果に見合いません。単位1の規約は今後作成される項目に適用します。`implement-be` の実行やステータス
変更など、他の理由ですでに編集対象になっている項目であれば適用するのが自然ですが、用語集リンクを
追加するためだけに既存項目を編集することはしません。

## 検討した代替案

- **`make check` に組み込んだ lint スクリプトでこのルールを機械的に強制する。** 見送りました。
  `step`、`target`、`app`、`platform` のように、用語集の語の多くは普通の英単語でもあります。機械的な
  文字列照合では、Bajutsu 固有の意味で使われていない文章にも次々と誤検知し、人間のレビュアが許容できる
  以上のノイズを生みます。規約だけでは実際に忘れられがちだとわかった段階で、lint 化はそれ自体を独立した
  提案としてスコープすればよいと考えます。
- **既存のロードマップ項目277件を、両言語ともいま一斉に修正する。** 見送りました。得られる効果
  （ほとんどが完了済みの過去記録である項目内の相互参照が明確になること）は、それだけの規模の差分を
  正当化しません。新規項目と、他の理由ですでに編集中の項目には規約を適用し、それ以外はそのままにします。
- **新しい項目を起こす代わりに、この内容を BE-0213 への追記として扱う。** 見送りました。BE-0213 は
  `Implemented` であり、5つの作業単位からなる完了済みの進捗チェックリストを持っています。今後のすべての
  文書に恒常的に適用される規約は、すでにチェックリストが閉じている提案への後付けの追記としてよりも、
  独立した項目として書くほうが読み手にとって明確です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 1. `CLAUDE.md` と `docs/ai-development.md` への恒常的な規約の追加（両言語）
- [x] 2. 特定した `docs/` ページ（英語版 + `docs/ja/` 対訳版）に、各ページが説明する用語の初出箇所への
      用語集リンクを埋め戻す

**ログ**

- 2026-07-17 — 2 つの単位をともに実装しました。単位 1 は、この規約をレビュー時の規約として
  `CLAUDE.md` の *Conventions* 一覧（バイリンガル文書の規約と BE-0113 の規約の隣）と、
  `docs/ai-development.md` および対訳の `docs/ja/ai-development.md` の *Documentation style* 節に
  追加します。単位 2 は、`drivers.md`、`cli.md`、`scenarios.md`、`evidence.md`、`recording.md`、
  `selectors.md`、`architecture.md`、`vision.md`、`getting-started/index.md`、`getting-started/ios.md`
  と、そのすべての `docs/ja/` 対訳版に、初出箇所の用語集リンクを埋め戻します。

## 参考

- [BE-0213](../BE-0213-glossary-and-docs-structure/BE-0213-glossary-and-docs-structure-ja.md)：
  `docs/glossary.md` を作り、初出箇所をリンクするというルールを最初に述べた項目
- [`docs/glossary.md`](../../docs/glossary.md) ·
  [`docs/ja/glossary.md`](../../docs/ja/glossary.md)：本提案のすべてのリンクが指す先のページ
- [`docs/ai-development.md`](../../docs/ai-development.md)：バイリンガル文書の規約や DESIGN.md
  整合性の規約と並べて、本提案の規約を追加する「文書の文体」節
- [BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md)：本提案の運用
  区分（CI のゲートではなくレビュー時の規約）が従う DESIGN.md 整合性の規約
- [`docs/getting-started/web.md`](../../docs/getting-started/web.md)：この規約をすでに実践している
  唯一のページであり、埋め戻し作業の手本

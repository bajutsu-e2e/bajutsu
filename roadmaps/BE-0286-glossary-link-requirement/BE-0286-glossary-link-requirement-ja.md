[English](BE-0286-glossary-link-requirement.md) · **日本語**

# BE-0286 — 用語集の用語は初出で定義へリンクする

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

本提案は、次のルールを `CLAUDE.md` と `docs/ai-development.md` の恒常的な規約にします。

> BE ロードマップ項目や `docs/` 配下のページが、[`docs/glossary.md`](../../docs/glossary.md)（日本語版は [`docs/ja/glossary.md`](../../docs/ja/glossary.md)）の用語を Bajutsu 固有の意味で使うとします。そのページで最初に実質的に言及する箇所は、用語の説明をその場で書き直さず、用語集の該当項目へのリンクにします。

このルール自体は、目新しいものではありません。[BE-0213](../BE-0213-glossary-and-docs-structure/BE-0213-glossary-and-docs-structure-ja.md) が用語集を作った際に、一度だけ文章にしていました。しかし、恒常的な規約としては機能してきませんでした。その理由は、次の「動機」で説明します。

具体的には、この規約を `CLAUDE.md` と、`docs/ai-development.md` の「文書の文体」節に追加します。`CLAUDE.md` では、すでにあるバイリンガル文書の規約や DESIGN.md 整合性の規約と並べます。あわせて、用語集の項目を説明していながら、リンクを貼っていない `docs/` 配下のページを埋め戻します。

## 動機

「はじめに」のルールが機能してこなかった理由は、ルールが書かれていた場所にあります。BE-0213 は用語集を作った際に、詳細設計でこう書いていました。「既存のページは、用語が最初に登場する箇所を用語集の該当項目にリンクし、その場で説明を繰り返さないようにします」。この一文は、用語集を作った PR 1 回限りの編集判断を述べたものでした。以後の投稿者が従うべきルールとしては、書かれていませんでした。

このプロジェクトが実際に運用する規約は、`CLAUDE.md` と `docs/ai-development.md` に集まっています。この一文はどちらにもなく、1 つの項目の詳細設計の中に置かれていました。そのため、将来の投稿者には、見つけて従えるルールがありませんでした。

その結果、欠落はほぼ全面的に及んでいます。数か所の見落としではありません。根拠は、2 つのコーパスにあります。

**`docs/` 側。** `docs/` のトップレベルページは、`glossary.md` 自身を除いて 27 件です。そのうち `glossary.md` にリンクしているのは、`overview.md`、`concepts.md`、`index.md`、`README.md` の 4 件だけです。用語のまとまりを実質的に説明しているページには、用語集へのリンクが 1 件もありません。

- `drivers.md` は、driver、backend、actuator、platform を説明しています。
- `cli.md` は、target、app、device と、CLI の各動詞を説明しています。
- `scenarios.md` は、scenario、step、precondition、expect を説明しています。
- `evidence.md` は、evidence と `capturePolicy` を説明しています。
- `recording.md` は、Tier 1 と `goal` を説明しています。
- `selectors.md` は、selector と identifier を説明しています。

いずれのページも、それらの語を定義するページを指していません。`architecture.md`、`vision.md`、`getting-started/index.md`、`getting-started/ios.md` も同様です。`docs/ja/` 側も、用語ごとに同じ欠落を映しています。

**ロードマップ側。** ロードマップ側の欠落は、さらに徹底しています。BE 項目は英語と日本語それぞれ 277 件あります。用語集を作った BE-0213 自身を除いて、どの項目も用語集を参照していません。driver、backend、actuator、platform のまとまりの用語を使っている項目は、定義へのリンクなしで書かれています。target、app、device のまとまり、`trace` と `triage`、`capturePolicy` についても同様です。

この規約をすでに実践しているページが、1 つだけあります。[`getting-started/web.md`](../../docs/getting-started/web.md) は、語彙の初出で `glossary.md` にリンクしています。本提案が、ほかのすべてのページに求める書き方そのものです。ルールさえ明文化されていれば、従うのは難しくないことを、このページが示しています。

放置すれば、同じ種類のずれが繰り返されます。BE-0213 自身も、ページを横並びで棚卸しして見つけた、具体的なずれから始まりました。`getting-started.md` はシナリオを「test」と呼び、`scenarios.md` は同じものを「scenario」と呼んでいました。この食い違いを、公開前に止める用語集は、まだありませんでした。

公開ドキュメントサイト（[BE-0093](../BE-0093-public-docs-site/BE-0093-public-docs-site.md)）は、これらのページをすでに公開しています。ページやロードマップ項目は、今後も増え続けます。新しいページが `capturePolicy` や、driver、backend、actuator、platform のまとまりを、自分の言葉で説明し直すとします。そのページは、用語集がすでに定めた説明から、説明がずれる機会を新たに作ります。用語集の説明にリンクするページは、その機会を作りません。

投稿者やエージェントがすでに執筆ルールを探しに来る場所に、この規約を書きます。そうすれば、今後はこの欠落を閉じられます。

## 詳細設計

作業は、独立した 2 つの単位からなります。

1. **`CLAUDE.md` と `docs/ai-development.md` への規約追加。** 規約は、`CLAUDE.md` の「Conventions」一覧と、`docs/ai-development.md` の「Documentation style (every document, both languages)」節に追加します。日本語版は、`docs/ja/ai-development.md` の対応節「ドキュメントの書き方（全ドキュメント、両言語に適用）」に追加します。
   - `CLAUDE.md` 側では、すでに隣接して並んでいる、バイリンガル文書の規約と [BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md) の DESIGN.md 整合性の規約のすぐ隣に置きます。
   - `docs/ai-development.md` 側では、その節がすでに持つ規則と並べて追加します。自然な文章、独自の造語の禁止、略語の展開、敬体という規則です。この節自体には、バイリンガル文書の規約と BE-0113 の隣接関係がありません。その隣接は `CLAUDE.md` 側だけのものです。
   - 文言は、次の趣旨とします。「BE ロードマップ項目や `docs/` 配下のページの文章が `docs/glossary.md` で定義された用語を Bajutsu 固有の意味で使うときは、そのページで最初に実質的に言及する箇所を、用語をその場で説明し直すのではなく、用語集の該当項目（`glossary.md#アンカー` または `docs/ja/glossary.md#アンカー`）へのリンクにします」。
   - あわせて、これが CI のゲートではなく、レビュー時の規約であることを明記します。理由は、`CLAUDE.md` で隣接する 2 つの規約と同じです。「step」や「target」のような普通の英単語が、Bajutsu 固有の意味を指しているかどうかの判断には、人間の判断が必要です。prime directive 1 は、意味的な判断を `run` と CI のゲートから遠ざけています。
2. **上記の棚卸しで特定した `docs/` ページの埋め戻し**（両言語）。対象は、`drivers.md`、`cli.md`、`scenarios.md`、`evidence.md`、`recording.md`、`selectors.md`、`architecture.md`、`vision.md`、`getting-started/index.md`、`getting-started/ios.md` と、その `docs/ja/` 対訳版です。
   - 編集は、エージェントが 1 ページずつ手作業で行わず、簡単な使い捨てスクリプトで機械的に適用します。機械的な作業部分のコストを抑えるためです。
   - まず、マニフェストを用意します。本提案の棚卸しからそのまま導ける、「ファイル、初出箇所の厳密な文字列、用語集アンカー」の組を、1 ページにつき 1 件ずつ列挙します。
   - 次に、短いスクリプトを走らせます。スクリプトは、マニフェストの各エントリについて、ファイル中でその文字列が最初に現れた箇所を、`glossary.md#アンカー`（または `docs/ja/glossary.md#アンカー`）への Markdown リンクで囲みます。
   - 「どの言及が実質的な言及か」という判断は、マニフェストを書く一度きりの作業に集約します。マニフェストを適用する作業は、ページごとの判断ではなく、決定的な文字列置換にします。
   - 適用後の差分は、必ず目視で確認します。用語の綴りがページ内でそれより前にも出てくる場合、厳密な文字列一致が誤った箇所に一致することがあるためです。
   - このスクリプトは、今回の埋め戻し作業のための使い捨ての実装手段です。`make check` への恒久的な追加ではありません。適用する規約自体は、単位 1 で述べたレビュー時の規約のままです。

**スコープ外とするのは、既存のロードマップ項目 277 件です。** ほぼ全項目を両言語で修正すると、大量のファイルを変更することになります。そのほとんどは、完了済みで `Implemented` になっている項目です。文書の体裁を整えるだけの利点は、その差分の規模に見合いません。単位 1 の規約は、今後作成される項目に適用します。

`implement-be` の実行やステータス変更など、ほかの理由ですでに編集対象になっている項目には、規約を適用するのが自然です。ただし、用語集リンクを遡って追加するためだけに、既存項目を編集することはしません。

## 検討した代替案

- **`make check` に組み込んだ lint スクリプトで、このルールを機械的に強制する。** 見送りました。`step`、`target`、`app`、`platform` のように、用語集の語の多くは普通の英単語でもあります。機械的な文字列照合は、Bajutsu 固有の意味で使われていない文章にも、次々と誤検知します。そのノイズは、チェックが節約する手間より、多くのレビュアの手間を奪います。まず規約だけで試します。規約だけでは実際に忘れられがちだとわかった段階で、lint 化をそれ自体として独立した提案にスコープすればよいと考えます。
- **既存のロードマップ項目 277 件を、両言語ともいま一斉に修正する。** 見送りました。コーパスのほとんどは、完了済みの過去記録です。そうした項目の中で相互参照が明確になっても、それだけの規模の差分は正当化されません。新規項目と、ほかの理由ですでに編集中の項目には規約を適用し、それ以外はそのままにします。
- **新しい項目を起こさず、この内容を BE-0213 への追記として扱う。** 見送りました。BE-0213 は `Implemented` で、5 つの作業単位からなる完了済みの進捗チェックリストを持っています。今後のすべての文書に恒常的に適用される規約は、チェックリストがすでに閉じている提案への後付けの追記より、独立した項目のほうが、読み手にとって明確です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 1. `CLAUDE.md` と `docs/ai-development.md` への恒常的な規約の追加（両言語）
- [x] 2. 特定した `docs/` ページ（英語版 + `docs/ja/` 対訳版）に、各ページが説明する用語の初出箇所への
      用語集リンクを埋め戻す

**ログ**

- 2026-07-17: 2 つの単位をともに実装しました。
  - 単位 1 では、この規約をレビュー時の規約として、`CLAUDE.md` の *Conventions* 一覧に追加しました。バイリンガル文書の規約と BE-0113 の規約の隣です。`docs/ai-development.md` と、対訳の `docs/ja/ai-development.md` の *Documentation style* 節にも追加しました。
  - 単位 2 では、`drivers.md`、`cli.md`、`scenarios.md`、`evidence.md`、`recording.md`、`selectors.md`、`architecture.md`、`vision.md`、`getting-started/index.md`、`getting-started/ios.md` と、そのすべての `docs/ja/` 対訳版に、初出箇所の用語集リンクを埋め戻しました。

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

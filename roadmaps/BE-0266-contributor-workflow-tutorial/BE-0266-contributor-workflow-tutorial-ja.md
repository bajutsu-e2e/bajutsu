[English](BE-0266-contributor-workflow-tutorial.md) · **日本語**

# BE-0266 — コントリビューションワークフローチュートリアル: ideation・implement-be・propose-and-build を実際に使うガイド

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0266](BE-0266-contributor-workflow-tutorial-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0266") |
| 実装 PR | [#1072](https://github.com/bajutsu-e2e/bajutsu/pull/1072) |
| トピック | コントリビューターワークフロー |
<!-- /BE-METADATA -->

## はじめに

[`docs/roadmap-workflow.md`](../../docs/roadmap-workflow.md) は `ideation` から `implement-be`
への流れが何であるかを説明し、[`docs/ai-development.md`](../../docs/ai-development.md) は
その仕組み（BE-ID のライフサイクル、モデルの割り当て、`propose-and-build` を含む三つのスキルの
関係）を詳細に説明しています。ただし、どちらも実際に手を動かす形式の文書ではありません。
[`docs/getting-started.md`](../../docs/getting-started.md) が Bajutsu の実行を一歩ずつ案内する
のに対し、`/ideation` の起票から提案の merge、`/implement-be` の実行から実装 PR の merge まで
を一本の道筋として案内するページは、今のところ存在しません。この提案では、そのチュートリアルに
加えて、初めてのコントリビュータが実際に求める二つのもの、良い提案と粗い提案を対比させた実例、
`ideation`・`implement-be` と釣り合う分量の `propose-and-build` の解説を追加します。

## 動機

「コントリビューションのルールや手順をまとめたドキュメントが欲しい」という要望を受けて既存文書を
確認したところ、内容自体はすでに3つのファイルに分かれて存在していました。そのうえで、次の3点の
不足が見つかりました。

1つ目は、一本化されたオンボーディング導線がないことです。`CONTRIBUTING.md` は人間のコントリビ
ュータ向けの入口として各文書へのリンクをまとめ、`docs/roadmap-workflow.md` は `ideation` と
`implement-be` のループを図解し、`docs/ai-development.md` は BE-ID の仕組みやモデルの割り当て、
PR テンプレートといった詳細なルールを保持しています。しかし「最初の変更を出荷するには、何を、
どの順番で入力すればよいか」という実際の手順は、方向づけ・概念説明・リファレンスというそれぞれ
異なる目的で書かれた3つのページから、読者自身が組み立てる必要があります。
[`docs/getting-started.md`](../../docs/getting-started.md) は、Bajutsu を実行するときの同じ
問題を、インストールからシナリオ、実行、レポートまでを一本の順序立ったページとしてすでに解決して
います。この提案は、コントリビュートする側に対して同じことを行います。

2つ目は、`propose-and-build` の扱いが手薄なことです。`docs/roadmap-workflow.md` の図と説明は
`ideation` と `implement-be` の二つのスキルによるループだけを扱っており、`propose-and-build`
は `docs/ai-development.md` の「Authoring and shipping roadmap items」節に一段落あるのみです
（`propose-and-build` 自体の仕組みは
[BE-0216](../BE-0216-propose-and-build-parallel-skill/BE-0216-propose-and-build-parallel-skill.md)
が説明していますが、これはコントリビュータがいつ直列の流れより `propose-and-build` を選ぶべきか
を説明するものではありません）。小さく範囲の定まった機能をどう始めるか迷うコントリビュータに対し、
現状は一行の目安以上の、チュートリアルとして辿れる案内がありません。

3つ目は、良い提案・粗い提案を対比させた実例がないことです。既存のどの文書も、提案が備えるべき
「形」（メタデータブロック、MECE な `Detailed design`、`Progress` チェックリスト）は説明していま
すが、曖昧な一行のアイデアが範囲の定まった機械的に検証可能な提案へと整形される、具体的な before /
after を示したものはありません。これは、初めての読者が実際の PR で一度失敗する前に「どこまで
範囲を絞ればよいか」を体で覚える、もっとも早い方法です。

この3点を埋めることで、初めてのコントリビュータ（本プロジェクトの取り決めにおいては人間・エージ
ェントのどちらも該当します）が、メンテナーにその場で付き添ってもらわなくても、最初の提案の merge
と最初の実装の merge にたどり着けるようになります。

## 詳細設計

1. **新しいチュートリアルページ**を `docs/contributor-workflow-tutorial.md`（`docs/ja/` の
   対訳を含む）として追加します。`docs/getting-started.md` と同じチュートリアルの文体（「各機能
   が何をするか」ではなく「この手順を、この順番で行う」）で書き、BE-ID のライフサイクル、モデルの
   割り当て、PR テンプレートは再説明せず、`docs/ai-development.md` と `docs/roadmap-workflow.md`
   へのリンクに委ねます。ページ自体は、一つの具体的なアイデアを次の流れで最後まで案内します。
   - `/ideation` を起票し、既存のロードマップを踏まえて `BE-0266` の提案を書き上げるまで。
   - 提案の PR を作成し、CI（`roadmap-id`）が実際の `BE-NNNN` を割り当てるところまで。
   - merge 済みの提案に対して `/implement-be BE-NNNN` を実行し、計画の確認、実装、ゲート、
     実装 PR の merge までを辿るところまで。
2. **良い提案・粗い提案を対比させた実例**をチュートリアルページに組み込みます。「flaky なステップ
   にリトライを入れる」といった、意図的に範囲を絞っていない短いアイデアを出発点とし、`ideation`
   が実際に整形するとどうなるか（範囲の限定、どの層に触れるか、機械的に検証可能な帰結、prime
   directive との緊張があればそれを明示すること）を示します。仮のアイデアを新たに作るのではなく、
   すでに merge 済みの実際の BE 項目（ドキュメント寄りの実例として
   [BE-0214](../BE-0214-web-only-beginner-tutorial/BE-0214-web-only-beginner-tutorial.md)、
   コード寄りの実例をもう一つ）を「良い例」の参照として使います。
3. **`ideation`・`implement-be` と釣り合う分量の `propose-and-build` の節**を設けます。直列の
   流れではなくこちらを選ぶべき場面（`docs/ai-development.md` にある既存の目安を、このチュート
   リアルで扱ったアイデア自身を「もし小さく設計が固まっていたら」という想定で流し直して補います）
   と、提案の merge から実際の `BE-NNNN` の割り当て、実装ブランチの rebase・retarget という
   受け渡しが、コントリビュータの手元からはどう見えるかを説明します。
4. **既存文書への導線であり、重複ではありません。** `CONTRIBUTING.md`（日本語版を含む）から、
   初めてのコントリビュータをリファレンス文書より先にこのチュートリアルへ案内します。
   `docs/roadmap-workflow.md` と `docs/ai-development.md` の冒頭には「初めての方はまずチュート
   リアルから」という導線を追加します。`docs/README.md` / `docs/overview.md` の読む順序と
   `mkdocs.yml` の nav にも、`Getting started` と並ぶ形でこの新しいページを含めます。
5. **日本語版の作成。** [`japanese-tech-writing`](../../.claude/skills/japanese-tech-writing/)
   スキルに従って自然な日本語で書きます。英語版の手順を機械的に訳したものにはしません。

## 検討した代替案

- **新しいページを作らず、`docs/roadmap-workflow.md` にチュートリアルを組み込む案**:見送りま
  した。このページは意図的に、ループの図解や「なぜ二つのスキルに分けたか」を説明する概念解説の
  文体で書かれています。そこに一歩ずつのチュートリアルの文章を混在させると、両方の役割がぼやけ
  てしまいます。製品そのものについて `getting-started.md` と `overview.md` を別ページに分けて
  いるのと同じ理由です。
- **`docs/ai-development.md` にある既存の `propose-and-build` の段落をその場で拡張するだけに
  とどめ、チュートリアルページは作らない案**:2つ目の不足だけを埋め、1つ目（一本化された導線が
  ない）と3つ目（実例がない）は残ったままになります。この提案の詳細設計の3番目では、拡張した
  `propose-and-build` の解説をリファレンスページに孤立させず、チュートリアルの中に組み込んで
  います。
- **良い例として既存の merge 済み BE 項目を使わず、仮のアイデアからチュートリアルを書き起こす
  案**:見送りました。仮のアイデアでは、読者に実際の提案ファイルや実際の PR、実際の `Progress`
  ログを見せることができません。BE-0214 のようにすでに merge されている項目を使えば、読者は
  自分の最初の提案の形を信頼する前に、実物をクリックして確かめられます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 1. `docs/contributor-workflow-tutorial.md` を書く（ideation の起票 → 提案の merge →
      implement-be の実行 → 実装 PR の merge までの一本のチュートリアル）
- [x] 2. 実際の merge 済み BE 項目を参照した、良い提案・粗い提案の対比実例を追加する
- [x] 3. `ideation`・`implement-be` と釣り合う分量まで `propose-and-build` の解説を拡張する
- [x] 4. `CONTRIBUTING.md`、`docs/roadmap-workflow.md`、`docs/ai-development.md`、
      `docs/README.md` / `docs/overview.md`、`mkdocs.yml` の nav から導線を接続する
- [x] 5. `japanese-tech-writing` スキルに従った日本語版の作成

**ログ**

- （本 PR）`docs/contributor-workflow-tutorial.md`（および `docs/ja/` 版）を追加し、`CONTRIBUTING.md`/
  `.ja`、`docs/roadmap-workflow.md`、`docs/ai-development.md`、`docs/README.md`、`docs/overview.md`
  （両言語）、`mkdocs.yml` の nav から導線を接続した。

## 参考

- [`docs/getting-started.md`](../../docs/getting-started.md)：この提案がコントリビューション
  ワークフローに適用するチュートリアルの文体と構成
- [`docs/roadmap-workflow.md`](../../docs/roadmap-workflow.md)：このチュートリアルが重複させず
  リンクする概念解説
- [`docs/ai-development.md`](../../docs/ai-development.md)：BE-ID の仕組み、モデルの割り当て、
  PR テンプレート、三つのスキルの関係など、このチュートリアルが重複させずリンクする詳細なルール
- [`CONTRIBUTING.md`](../../CONTRIBUTING.md)：このチュートリアルへの導線元となる、人間のコント
  リビュータ向けの入口
- [BE-0216](../BE-0216-propose-and-build-parallel-skill/BE-0216-propose-and-build-parallel-skill.md)：
  このチュートリアルが釣り合う分量の解説を与える `propose-and-build` スキル
- [BE-0214](../BE-0214-web-only-beginner-tutorial/BE-0214-web-only-beginner-tutorial.md)：同じ
  トピックにおけるチュートリアル形式のロードマップ項目の先例であり、良い提案・粗い提案の対比実例
  の候補

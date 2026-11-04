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

`ideation` から `implement-be` へ続くループを説明するページは、すでに 2 つあります。どちらも、手を動かして辿る形式の文書ではありません。

- [`docs/roadmap-workflow.md`](../../docs/roadmap-workflow.md) は、ループが*何であるか*を説明します。
- [`docs/ai-development.md`](../../docs/ai-development.md) は、仕組みを詳細に説明します。BE-ID のライフサイクル、モデルの割り当て、`propose-and-build` を含む 3 つのスキルの関係です。

初めてのコントリビュータが、`/ideation` の起票から提案の merge、`/implement-be` の実行から実装 PR の merge まで、一本の道筋で辿れるページは、今のところありません。[`docs/getting-started.md`](../../docs/getting-started.md) は、Bajutsu の実行について、そうした案内を一歩ずつ行っています。

本提案は、コントリビューションについて同じ案内をするチュートリアルを追加します。あわせて、初めてのコントリビュータが求めるのに、現在のリファレンスのページにはない 2 つのものも追加します。

- 良い提案と粗い提案を対比させた実例
- `ideation` や `implement-be` の節と釣り合う分量の、`propose-and-build` の解説

## 動機

コントリビューションの手順をまとめたドキュメントがすでにあるかを確認したところ、内容自体は、3 つのファイルに分かれて存在していました。確認の過程で、次の 3 点の不足が見つかりました。

1. **一本化されたオンボーディング導線がないこと。**
   - `CONTRIBUTING.md` は、人間のコントリビュータ向けの入口として、各文書へのリンクをまとめています。
   - `docs/roadmap-workflow.md` は、`ideation` と `implement-be` のループを図解します。
   - `docs/ai-development.md` は、BE-ID の仕組み、モデルの割り当て、PR テンプレートといった、詳細なルールを保持しています。

   「最初の変更を出荷するには、何を、どの順番で入力すればよいか」という実際の手順は、読者自身が組み立てる必要があります。3 つのページは、方向づけ、概念説明、リファレンスという、それぞれ異なる目的で書かれているためです。[`docs/getting-started.md`](../../docs/getting-started.md) は、Bajutsu の*実行*について、同じ問題をすでに解決しています。インストール、シナリオ、実行、レポートを、一本の順序立ったページにしています。本提案は、Bajutsu への*コントリビュート*について、同じことを行います。
2. **`propose-and-build` の扱いが手薄なこと。**
   - `docs/roadmap-workflow.md` の図と説明は、`ideation` と `implement-be` の 2 つのスキルによるループだけを扱っています。
   - `propose-and-build` は、`docs/ai-development.md` の「Authoring and shipping roadmap items」節に、一段落あるだけです。
   - `propose-and-build` 自体の仕組みは、[BE-0216](../BE-0216-propose-and-build-parallel-skill/BE-0216-propose-and-build-parallel-skill.md) が説明しています。ただし、コントリビュータがいつ直列の流れより `propose-and-build` を選ぶべきかは、説明していません。

   小さく範囲の定まった機能をどう始めるか迷うコントリビュータには、その選択についての一行の目安があるだけです。チュートリアルとして辿れる案内はありません。
3. **良い提案と粗い提案を対比させた実例がないこと。** 既存のどの文書も、提案が備えるべき「形」を説明しています。メタデータブロック、MECE な `Detailed design`、`Progress` チェックリストです。しかし、曖昧な一行のアイデアが、範囲の定まった機械的に検証可能な提案へと整形される、具体的な before と after を示した文書はありません。初めての読者が、実際の PR で一度失敗する前に、「どこまで範囲を絞ればよいか」を身につけるには、before と after の実例がもっとも早い方法です。

この 3 点を埋めると、初めてのコントリビュータが、メンテナーにその場で付き添ってもらわなくても、最初の提案の merge と最初の実装の merge にたどり着けます。コントリビュータは、本プロジェクトの取り決めにおいて、人間とエージェントのどちらも該当します。

## 詳細設計

1. **新しいチュートリアルページ**を、`docs/contributor-workflow-tutorial.md`（`docs/ja/` の対訳を含む）として追加します。`docs/getting-started.md` と同じチュートリアルの文体で書きます。「各機能が何をするか」ではなく、「この手順を、この順番で行う」という文体です。BE-ID のライフサイクル、モデルの割り当て、PR テンプレートは、再説明しません。これらは `docs/ai-development.md` と `docs/roadmap-workflow.md` が引き続き担い、ページはそれらへのリンクに委ねます。ページは、1 つの具体的なアイデアを、次の 3 段階で最後まで案内します。
   - `/ideation` を起票し、既存のロードマップを踏まえて、`BE-0266` の提案を書き上げるまで。
   - 提案の PR を作成し、CI（`roadmap-id`）が実際の `BE-NNNN` を割り当てるところまで。
   - merge 済みの提案に対して `/implement-be BE-NNNN` を実行し、計画の確認、実装、ゲート、実装 PR の merge までを辿るところまで。
2. **良い提案と粗い提案を対比させた実例**を、チュートリアルページに組み込みます。
   - 出発点は、「flaky なステップにリトライを入れる」といった、意図的に範囲を絞っていない短いアイデアです。
   - 続けて、`ideation` が実際に整形するとどうなるかを示します。範囲の限定、どの層に触れるか、機械的に検証可能な帰結、プライムディレクティブとの緊張があれば、それを明示することです。
   - 仮のアイデアを新たに作らず、すでに merge 済みの実際の BE 項目を、「良い例」の参照として使います。ドキュメント寄りの実例は、[BE-0214](../BE-0214-web-only-beginner-tutorial/BE-0214-web-only-beginner-tutorial.md) です。コード寄りの実例も、もう 1 つ使います。
3. **`ideation` や `implement-be` と釣り合う分量の、`propose-and-build` の節**を設けます。
   - 直列の流れではなく、`propose-and-build` を選ぶべき場面を説明します。`docs/ai-development.md` にある既存の目安から始めます。チュートリアルで扱ったアイデア自身を、「もし小さく設計が固まっていたら」という想定で流し直して、目安を補います。
   - 提案の merge、実際の `BE-NNNN` の割り当て、実装ブランチの rebase と retarget という受け渡しが、コントリビュータの手元からはどう見えるかを説明します。
4. **既存文書への導線を張ります。重複はさせません。**
   - `CONTRIBUTING.md`（日本語版を含む）から、初めてのコントリビュータを、リファレンス文書より先にこのチュートリアルへ案内します。
   - `docs/roadmap-workflow.md` と `docs/ai-development.md` の冒頭に、「初めての方はまずチュートリアルから」という導線を追加します。
   - `docs/README.md` と `docs/overview.md` の読む順序、および `mkdocs.yml` の nav にも、`Getting started` と並ぶ形で、この新しいページを含めます。
5. **日本語版を作成します。** [`japanese-tech-writing`](../../.claude/skills/japanese-tech-writing/) スキルに従って、自然な日本語で書きます。英語版の手順を機械的に訳したものにはしません。

## 検討した代替案

- **新しいページを作らず、`docs/roadmap-workflow.md` にチュートリアルを組み込む案。** 見送りました。このページは、意図的に、リファレンスの文体で書かれた概念解説です。ループの図解と、「なぜ 1 つではなく 2 つのスキルに分けたか」を説明しています。一歩ずつのチュートリアルの文章を混在させると、両方の役割がぼやけます。製品そのものについて、`getting-started.md` と `overview.md` を別ページに分けているのも、同じ理由です。
- **`docs/ai-development.md` にある既存の `propose-and-build` の段落をその場で拡張するだけにとどめ、チュートリアルページは作らない案。** 2 つ目の不足だけを埋めます。1 つ目（一本化された導線がない）と 3 つ目（実例がない）は、残ったままになります。本提案の詳細設計の 3 番目は、拡張した `propose-and-build` の解説を、チュートリアルの中に組み込みます。リファレンスページに孤立させません。
- **良い例として、merge 済みの既存の BE 項目を使わず、仮のアイデアからチュートリアルを書き起こす案。** 見送りました。仮のアイデアでは、読者に、実際の提案ファイル、実際の PR、実際の `Progress` ログを見せられません。BE-0214 のように、すでに merge されている項目を使えば、読者は、実物をクリックして確かめられます。初めての読者には、自分の最初の提案の形を信頼する前に、その確認が必要です。

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

- （本 PR）`docs/contributor-workflow-tutorial.md`（および `docs/ja/` 版）を追加しました。`CONTRIBUTING.md` とその日本語版、`docs/roadmap-workflow.md`、`docs/ai-development.md`、`docs/README.md`、`docs/overview.md`（両言語）、`mkdocs.yml` の nav から、導線を接続しました。

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

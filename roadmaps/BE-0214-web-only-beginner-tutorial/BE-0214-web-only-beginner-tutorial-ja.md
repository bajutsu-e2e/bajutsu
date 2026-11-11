[English](BE-0214-web-only-beginner-tutorial.md) · **日本語**

# BE-0214 — Xcode も Simulator も不要な、Web 版のみで完結する初学者向けチュートリアル

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0214](BE-0214-web-only-beginner-tutorial-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0214") |
| 実装 PR | [#860](https://github.com/bajutsu-e2e/bajutsu/pull/860) |
| トピック | コントリビューターワークフロー |
<!-- /BE-METADATA -->

## はじめに

[`docs/getting-started.md`](../../docs/getting-started.md) は Bajutsu の「チュートリアル」を
名乗っています。ところが Step 4 から Step 6 は、ショーケースアプリのビルド、Simulator 上での
シナリオ実行、レポートの閲覧という内容で、macOS、Xcode、idb backend を前提にしています。Mac を
持たない読者は前半しか進められず、実行結果（`run`）まで一度もたどり着けません。

この提案では、同じ「インストール → シナリオ → 実行 → レポート」の流れを完結させるチュートリアルの
導線を追加します。導線が使うのは Web（Playwright）
[backend](../../docs/glossary.md#driver-backend-actuator-platform) だけです。この backend は
Linux 上でもすでに決定的に動きます。

## 動機

現行のチュートリアルの Step 1 から Step 3（インストール、ユニットテストの実行、シナリオを読む）は、
すでにプラットフォームを問いません。ところが Step 4 以降は iOS 実機向けの手順に一本化されています。
今のチュートリアルを最後まで終えられるのは、Mac を持つ読者だけです。

[`demos/web`](../../demos/web/README.md) は、この内容がすでに存在することを示しています。
同じシナリオ形式と同じ決定的な `run` ループを、静的な Web アプリに対して動かしており、しかも CI が
Linux で使うゲートの中で動いています。ただし現状は、チュートリアルの物語としては書かれていません。
コマンドを並べただけのデモの実行手順です。`getting-started.md` が iOS 向けに提供している、各ステップの
意味を初学者に説明する案内は、まだ Web 側にはありません。

Bajutsu 自身の中心的な主張は「プラットフォームは backend にすぎない」というものです。決定的なコア、
シナリオ形式、レポーターは、どの backend が UI を操作するかによらず同じです。それにもかかわらず、
オンボーディングの導線が iOS でしか完結しないなら、コード上では成り立っているこの主張が、体験として
は裏切られてしまいます。

この導線を直す価値が最も大きいのは、今まさに最後までたどり着けずにいる読者です。Mac を持たない
マシン、Simulator のない Linux CI ランナーやコンテナで作業する読者が当たります（この提案を書いている
環境自体もその一つです）。

## 詳細設計

1. **新しいチュートリアルの導線**：新規ページ（`docs/getting-started-web.md` と `docs/ja/` の対訳）を
   追加する案と、`docs/getting-started.md` を再構成する案があります。再構成する場合は、Step 1〜3 を
   共有のままにして、Step 4〜6 を「iOS 版」と「Web 版」に分岐させます。具体的なファイル構成は実装時に
   決めます。この提案が定めるのは、Mac なしで最後までたどり着けるという要件です。特定のファイル構成は
   定めません。
2. **内容**：既存の Step 4〜6 と同じ構成を、ショーケースの iOS アプリの代わりに `demos/web` を使って
   組み立てます。静的アプリの起動（`app-serve`）と、`--backend web` でのシナリオ実行（Xcode も idb も
   Simulator も不要）を扱います。さらに、iOS 版と同じ3種類のレポート形式（`manifest.json` /
   `junit.xml` / `report.html`）の閲覧までを扱います。
3. **導線の接続**：`docs/overview.md` の読む順序と `README.md` のセットアップとデモの節から、Mac を
   持たない読者をこの導線へ案内します。案内は、Xcode 版より先に置くことが望ましいところです。後に置く
   と、プラットフォーム非依存の導線が代替手段のように見えてしまいます。
4. **日本語版の作成**：[`japanese-tech-writing`](../../.claude/skills/japanese-tech-writing/) スキルに
   従って自然な日本語で書いた、`docs/ja/` の対訳を用意します。

このチュートリアルの導線は、並行して起票した姉妹提案「用語集とドキュメント構成の見直し」が決着させる
語彙を使うべきです。`backend`、`actuator`、`target` といった語を、独自に言い換えてはいけません。
そちらの提案を先に着地させておけば、この提案が同じ語の言い回しをもう一つ増やさずに済みます。

## 検討した代替案

- **`getting-started.md` をそのまま拡張し、ページの途中でプラットフォームごとに分岐させる案**：
  実現可能な形の一つです。ここでは決めずに、実装時の判断に委ねます。この提案が定める要件は Mac なしで
  最後までたどり着けることであり、それをどのファイル構成で届けるかではないからです。
- **専用のチュートリアル導線を作らず、`demos/web/README.md` だけを案内する案**：見送りました。この
  README はコマンドを並べたデモの実行手順です。各ステップがなぜ重要かを初学者に説明するチュートリアル
  ではありません。しかも、`getting-started.md` がオンボーディングの価値として担っているのは、まさに
  その説明の部分です。
- **チュートリアルを iOS 専用のままにしておく案**：見送りました。「プラットフォームは backend に
  すぎない」という主張を、体験として裏切ることになります。さらに、Web backend が本来支えるはずの環境
  （Linux CI、Mac を持たないマシン）を、まさに不利な立場に置いてしまいます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 1. チュートリアル導線のファイル構成を決める(新規ページか `getting-started.md` の分岐か)
- [x] 2. Web 版の内容を書く(起動 → `run --backend web` → レポートの閲覧)
- [x] 3. `docs/overview.md` と `README.md` からの導線を接続する
- [x] 4. `japanese-tech-writing` スキルに従った日本語版の作成

### ログ

- [#860](https://github.com/bajutsu-e2e/bajutsu/pull/860)：Web 版の導線を、新規の自己完結ページ（`docs/getting-started-web.md` と `docs/ja/` の
  対訳）として追加しました。Playwright backend に対して、インストール → シナリオ → 実行 → レポートの
  同じループを、Mac なしで辿ります。`docs/overview.md`、`docs/index.md`、`README.md`（セットアップと
  デモ）、iOS 版の `getting-started.md`、`mkdocs.yml` の nav から、英日の両方で導線を接続しました。

## 参考

- [`docs/getting-started.md`](../../docs/getting-started.md)：この提案が拡張するチュートリアル
- [`demos/web/README.md`](../../demos/web/README.md)：このチュートリアルが物語として仕立て直す実行可能な内容
- [`docs/multi-platform.md`](../../docs/multi-platform.md)：このチュートリアルが体験として示すプラットフォーム非依存の設計
- [BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)：この導線が使う Playwright backend
- 「用語集とドキュメント構成の見直し」(並行して起票した姉妹提案)：このチュートリアルが独自に言い換えず流用すべき語彙

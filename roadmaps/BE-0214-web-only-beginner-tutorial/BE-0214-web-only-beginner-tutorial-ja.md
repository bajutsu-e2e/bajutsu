[English](BE-0214-web-only-beginner-tutorial.md) · **日本語**

# BE-0214 — Web 版のみで完結する初学者向けチュートリアル(Xcode・Simulator 不要)

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
名乗っていますが、Step 4 から Step 6(ショーケースアプリのビルド、Simulator 上でのシナリオ実行、
レポートの閲覧)は macOS・Xcode・idb backend を前提にしています。Mac を持たない読者は前半しか
進められず、実行結果(`run`)まで一度もたどり着けません。この提案では、同じ「インストール →
シナリオ → 実行 → レポート」の一連の流れを、Linux 上でもすでに決定的に動く Web(Playwright)
backend だけで完結させるチュートリアルの導線を追加します。

## 動機

現行のチュートリアルの Step 1 から Step 3(インストール、ユニットテストの実行、シナリオを読む)
は、すでにプラットフォームを問いません。ところが Step 4 以降は iOS 実機向けの手順に一本化されて
おり、今のチュートリアルを最後まで終えられるのは Mac を持つ読者だけです。
[`demos/web`](../../demos/web/README.md) は、この内容がすでに存在することを示しています。
同じシナリオ形式・同じ決定的な `run` ループを、静的な Web アプリに対して、CI と同じゲートの中で
Linux 上で動かしているからです。ただし現状はチュートリアルの物語としては書かれておらず、コマンド
を並べただけのデモの実行手順にとどまっています。`getting-started.md` が iOS 向けに提供している、
各ステップの意味を初学者に説明する案内は、まだ Web 側にはありません。

Bajutsu 自身の中心的な主張は「プラットフォームは backend にすぎない」というものです。決定的な
コア、シナリオ形式、レポーターは、どの backend が UI を操作するかによらず同じです。それにもかか
わらず、オンボーディングの導線が iOS でしか完結しないなら、コード上では成り立っているこの主張が、
体験としては裏切られてしまいます。この導線を直すことは、今まさに最後までたどり着けずにいる読者
(Mac を持たないマシン、Simulator のない Linux CI ランナーやコンテナ。この提案を書いている環境
自体もその一つです)にとって、最も価値があります。

## 詳細設計

1. **新しいチュートリアルの導線**:新規ページ(`docs/getting-started-web.md` と `docs/ja/` の対
   訳)を追加する案と、`docs/getting-started.md` を再構成して Step 1〜3 は共有のまま、Step 4〜6
   を「iOS 版」と「Web 版」に分岐させる案があります。具体的なファイル構成は実装時に決めればよく、
   この提案が定めるのは Mac なしで最後までたどり着けることという要件であり、特定のファイル構成
   ではありません。
2. **内容**:既存の Step 4〜6 と同じ構成を、ショーケースの iOS アプリの代わりに `demos/web` を
   使って組み立てます。静的アプリの起動(`app-serve`)、`--backend web` でのシナリオ実行(Xcode
   も idb も Simulator も不要)、iOS 版と同じ3種類のレポート形式(`manifest.json` /
   `junit.xml` / `report.html`)の閲覧までを扱います。
3. **導線の接続**:`docs/overview.md` の読む順序と `README.md` のセットアップ・デモの節から、
   Mac を持たない読者をこの導線へ案内します。Web 版を Xcode 版の代替として後回しにするのではな
   く、先に案内することが望ましいところです。
4. **日本語版の作成**:[`japanese-tech-writing`](../../.claude/skills/japanese-tech-writing/)
   スキルに従って自然な日本語で書いた `docs/ja/` の対訳を用意します。

このチュートリアルの導線は、`backend`・`actuator`・`target` といった語を独自に言い換えるので
はなく、並行して起票した姉妹提案「用語集とドキュメント構成の見直し」が決着させる語彙をそのまま
使うべきです。そちらの提案を先に着地させておくことで、この提案が独自の言い回しを別立てで作らず
に済みます。

## 検討した代替案

- **`getting-started.md` をそのまま拡張し、ページの途中でプラットフォームごとに分岐させる案**:
  実現可能な形の一つですが、ここでは決めずに実装時の判断に委ねます。この提案が定める要件は Mac
  なしで最後までたどり着けることであり、それをどのファイル構成で届けるかではないからです。
- **専用のチュートリアル導線を作らず、`demos/web/README.md` だけを案内する案**:見送りました。
  `demos/web/README.md` はコマンドを並べたデモの実行手順であり、各ステップがなぜ重要かを初学者
  に説明するチュートリアルではありません。`getting-started.md` がオンボーディングの価値として
  担っているのは、まさにその説明の部分です。
- **チュートリアルを iOS 専用のままにしておく案**:見送りました。「プラットフォームは backend
  にすぎない」という主張を、体験として裏切ることになります。しかも、Web backend が本来支える
  はずの環境(Linux CI、Mac を持たないマシン)を、まさに不利な立場に置いてしまいます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 1. チュートリアル導線のファイル構成を決める(新規ページか `getting-started.md` の分岐か)
- [x] 2. Web 版の内容を書く(起動 → `run --backend web` → レポートの閲覧)
- [x] 3. `docs/overview.md` と `README.md` からの導線を接続する
- [x] 4. `japanese-tech-writing` スキルに従った日本語版の作成

### ログ

- [#860](https://github.com/bajutsu-e2e/bajutsu/pull/860) — Web 版の導線を、新規の自己完結ページ（`docs/getting-started-web.md` と `docs/ja/` の
  対訳）として追加しました。Playwright backend に対して、インストール → シナリオ → 実行 → レポートの
  同じループを、Mac なしで辿ります。`docs/overview.md`、`docs/index.md`、`README.md`（セットアップと
  デモ）、iOS 版の `getting-started.md`、`mkdocs.yml` の nav から、英日の両方で導線を接続しました。

## 参考

- [`docs/getting-started.md`](../../docs/getting-started.md)：この提案が拡張するチュートリアル
- [`demos/web/README.md`](../../demos/web/README.md)：このチュートリアルが物語として仕立て直す実行可能な内容
- [`docs/multi-platform.md`](../../docs/multi-platform.md)：このチュートリアルが体験として示すプラットフォーム非依存の設計
- [BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)：この導線が使う Playwright backend
- 「用語集とドキュメント構成の見直し」(並行して起票した姉妹提案)：このチュートリアルが独自に言い換えず流用すべき語彙

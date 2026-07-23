[English](BE-0093-public-docs-site.md) · **日本語**

# BE-0093 — 公式サイトとドキュメントポータルの公開（GitHub Pages）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0093](BE-0093-public-docs-site-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0093") |
| 実装 PR | [#326](https://github.com/bajutsu-e2e/bajutsu/pull/326) |
| トピック | コントリビューターワークフロー |
<!-- /BE-METADATA -->

## はじめに

Bajutsu には公開向けのウェブサイトがありません。このプロジェクトが何であり、なぜ決定性を最優先するのか、どう使い始めるのか。その説明はリポジトリの `README` と `docs/` にしかなく、読み手は GitHub を辿って読むしかありません。サイトとして公開されているのは自動生成の API リファレンス（[BE-0065](../BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference-ja.md)）だけで、それさえもまだ稼働していません。リポジトリで GitHub Pages が有効になっておらず、デプロイのジョブが手動実行に限定されているからです。

この項目では、プロジェクトの GitHub Pages の URL に**公式サイト**を立てることを提案します。Bajutsu とその原則を紹介するランディングページに加え、すでにバイリンガルで揃っている `docs/` を、閲覧できるドキュメントポータルとして公開します。**すでに存在する mkdocs-material のサイト**（[`mkdocs.yml`](../../mkdocs.yml)）を拡張する形を取り、二つ目のツールチェーンは導入しません。配線済みのまま眠っているデプロイ経路（[`.github/workflows/docs.yml`](../../.github/workflows/docs.yml)）を起こします。

## 動機

- **素材はすでにあるのに、GitHub に閉じている。** `docs/` には20本を超えるページがあり、`docs/ja/` に日本語のミラーが完全に揃っています。`README`、[`vision.md`](../../docs/vision.md)、`DESIGN.md` も同様です。これらはリポジトリの外からは見つけられません。GitHub の Markdown 表示には、サイト内検索もナビゲーションも言語切り替えもなく、読み手に示せる正規のランディングページもありません。
- **pre-alpha のプロジェクトにも入口は要る。** 価値の提案（自然言語駆動の E2E、バックエンド非依存のドライバ、決定性の最優先）と、ありのままの状況（iOS の idb は実機検証済み、web の Playwright は着地、Android は次）を1ページで示せれば、ソースツリーから組み立て直さなくても、訪れた人がすぐに Bajutsu を評価できます。
- **基盤は9割できていて、止まっている。** mkdocs-material のサイト、`--strict` での CI ビルド、最小権限の Pages デプロイ workflow は、いずれもすでにあります。足りないのは、載せる範囲（API リファレンスだけでなく、ランディングページと `docs/` ポータル）と、一度きりの有効化（Pages を有効にし、デプロイのガードを外す）です。サイトの公開は、ほとんど既存の部品を**つなぐ**作業です。
- **基本原則との整合。** ウェブサイトはドキュメントだけの変更です。ティア2の `run`/CI ゲートに LLM を持ち込みませんし、決定性のコアにも触れません。`--strict` ビルド自体がこのプロジェクトの精神に沿っています。参照の破損やリンク切れがあればビルドが失敗し、黙って公開されることはありません。コードのゲートが取る「ゲートが破損を捕まえる」という姿勢と同じです。

## 詳細設計

### 範囲

既存の mkdocs-material のサイトを拡張し、一度のビルドで三つを配信します。**ランディングページ**、**バイリンガルの `docs/` ポータル**、そして今日すでに出力している**自動生成の API リファレンス**です。二つ目の静的サイトツールチェーンは導入しません。

### 1. ランディングページ

サイトの入口となるトップページを置きます。素材は `README`／`vision.md`／`DESIGN.md` にあるものから作れます。

1. **ヒーロー**：名前、ロゴ（`assets/icons/logo.png`）、一行の価値提案、主要な導線（Get started、GitHub）。
2. **核となる姿勢**：「AI は著者であり失敗の調査役であって、判定者では決してない」、二つのティア、決定性の最優先、「プラットフォームは一つのバックエンドである」。
3. **状況**：pre-alpha、iOS（idb）はシミュレータで end-to-end に検証済み、web（Playwright）は着地、Android は次。
4. **クイックスタート**：uv で導入し、`record` から `run` へ。
5. **機能のハイライト**：`record`／`crawl`、決定的なランナー、evidence サブシステム、自己修復の triage、codegen、MCP、`serve` の web UI。
6. **バックエンドとプラットフォーム**の一覧と、**リンク集**（docs、roadmap、DESIGN、API リファレンス、GitHub）。

mkdocs-material では、テーマのオーバーライド（`overrides/home.html`、Material の splash テンプレート）か、整えた `index.md` のどちらかでヒーローを描けます。オーバーライドにすると本格的なランディングの見た目になります。どちらを採るかは実装時に決める細部であって、進行を妨げるものではありません。

### 2. `docs/` をポータルとして公開する

今は `docs_dir` が `docs/api` なので、API リファレンスしかビルドされません。サイトの対象を `docs/` 全体に広げ（API リファレンスはその下に入れ子にします）、ページをまとめた `nav` を用意します（getting started、concepts、scenarios、selectors、drivers、evidence、reporting、CI、multi-platform、self-hosting、API リファレンスなど）。

ここで生じる二つの論点は、設計として明示的に扱います。

- **バイリンガル表示。** リポジトリの構成は、英語が `docs/foo.md`、日本語のミラーが `docs/ja/foo.md` です。サイトでは `mkdocs-static-i18n` で言語切り替えを出し、既存の構成（既定の言語を `docs/` の直下に、日本語を `docs/ja/` の下に）をプラグインの構造へ対応づけます。プロジェクトがすでに守っているバイリンガルの規約（文書化した挙動は両言語で同時に更新する）が、両者を歩調を合わせて保ちます。
- **`--strict` でのリンク。** `docs/` の多くのページは、`docs/` の**外**にあるパス（`../roadmaps/…`、`../DESIGN.md`、`../README.md`、`../demos/…`）へリンクしています。mkdocs の `--strict` は解決できない内部リンクをエラーとして扱うため、これらに手を打つ必要があります。リポジトリ外へのリンクを GitHub の絶対 URL に書き換える（たとえばリポジトリのベースを表す小さなマクロや変数を使う）か、参照先のツリーをビルドに取り込むかです。このリンクの調整が**移行の主なコスト**であり、`nav` を確定させる前に範囲を見積もるべきです。

### 3. 公開を有効にする

デプロイ経路はありますが、眠っています。公開するには次を行います。

1. **GitHub Pages を有効にする**：Settings → Pages → source を「GitHub Actions」に。これは一度きりのリポジトリ管理者の操作で、コードからは実行できません。
2. **デプロイのガードを外す**：[`docs.yml`](../../.github/workflows/docs.yml) で、アーティファクトのアップロードと `deploy` ジョブにかかっている `workflow_dispatch` 限定のガードを外し、`main` への push で公開されるようにします（ファイル内のコメントが、外すべき箇所をそのまま示しています）。
3. **workflow のパスフィルタを広げる**：サイトの対象がツリー全体になったので、`docs/**` 配下の変更でも（`bajutsu/**`／`docs/api/**`／`mkdocs.yml` だけでなく）再ビルドが走るようにします。
4. **サイトのメタデータと SEO の基本**：`site_url`、説明、ソーシャル／OpenGraph カード、サイトマップ。独自ドメインを使うなら `CNAME` ファイルを置きます。

### ビルドとゲートの境界

サイトのビルドは、今と同じく **`make check` の外**に置きます。実機 E2E と同じく、重く独立した経路だからです。CI の `--strict` ビルドが、壊れたサイトを公開前に捕まえる回帰の網であり続けます。LLM もシミュレータも使わず、Linux だけで動きます。既存の docs workflow と一貫しています。

## 検討した代替案

- **別の静的サイトジェネレータ（Docusaurus／Astro／素の HTML）。** 凝ったマーケティング向けのヒーローは専用フレームワークのほうが作りやすいものの、保守すべきビルド、CI ステップ、デプロイ経路がもう一つ増え、mkdocs-material と `docs.yml` がすでに提供しているものを重複させます。既存サイトの拡張（採用した方針）を優先し、これは見送りました。
- **ランディングページだけで、ポータルは作らない。** 工数は小さいものの、すでに書かれているバイリンガルの `docs/` を公開しないままにします。ここで最も価値の大きい資産です。ポータルこそが要点なので、見送りました。
- **サイトを docs の正規の置き場にする（`docs/` をサイト専用の構造へ移す）。** より大きな再構成で、GitHub で読めるソースの構成とバイリンガルのミラー規約を壊します。採用した方針は、リポジトリ内のソースを読める形に保ったまま `docs/` を**公開**します。
- **BE-0015／BE-0016 のホスティングの成果を流用する。** これらの項目は `serve` という**アプリケーション**（コントロールプレーンと macOS ワーカーに分かれたサービス）をホストするもので、静的な公式サイトとは無関係です。別の関心事であり、重なりはありません。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

- [BE-0065 — Docstring 標準と生成 API リファレンス](../BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference-ja.md)：拡張の対象である既存の mkdocs-material サイト。
- [`mkdocs.yml`](../../mkdocs.yml)：現在のサイト設定（API リファレンスのみ）。
- [`.github/workflows/docs.yml`](../../.github/workflows/docs.yml)：眠っているビルド／デプロイ workflow。
- [`docs/vision.md`](../../docs/vision.md) · [`README`](../../README.md)：ランディングページの素材。
- [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) · [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)：`serve` という**アプリ**のホスティング（この静的サイトとは別物）。
- [mkdocs-static-i18n](https://github.com/ultrabug/mkdocs-static-i18n)：ポータルのバイリンガル表示。

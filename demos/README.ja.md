[English](README.md) · **日本語**

# Bajutsu のデモ

実行できるデモ群です。下の `make -C demos` 一式は実機 iOS Simulator を操作し、別に web デモがブラウザを Linux 上で操作します（下記）。同じ中心的な物語を、それぞれ違う深さで見せます。その物語とは、シナリオが決定論的なハブであり、実行（run）の合否は機械的なアサーションだけで決まる（LLM は判定に関与しない）、というものです。iOS のデモはどれも `make -C demos <target>` という一つの入口で実行します。

```bash
make -C demos            # メニュー（make -C demos help と同じ）
make -C demos tour       # 決定論（APIキー不要）: 実機で run -> 改変 -> 診断
make -C demos features   # 決定論（APIキー不要）: シナリオ機能のショーケース
make -C demos offline    # 上記2つの決定論デモをまとめて実行
make -C demos webui      # Web UI ツアー（あらゆる証跡）
make -C demos record     # 本物の Claude による著作、続けて改変 + triage
```

iOS のデモはどれも同じフィクスチャ、showcase 群（[`showcase/`](showcase/README.ja.md)）を対象にします。

| デモ | コマンド | 何を証明するか | 何が必要か |
|---|---|---|---|
| **[tour](tour/README.ja.md)** | `make -C demos tour` | ライフサイクル全体（run → 改変 → 診断（`triage`））を実機 Simulator 上で完全に決定論的に | macOS + Xcode + Simulator。**APIキー不要** |
| **[features](features/README.ja.md)** | `make -C demos features` | シナリオ著作の機能（タグ、パラメータ化された共有ステップ、シークレット）を実機 Simulator 上で | macOS + Simulator。**APIキー不要** |
| **[webui](showcase/WEBUI.ja.md)** | `make -C demos webui` | **Web UI** が Simulator を操作し、あらゆる証跡を収集: スクリーンショット、動画、ログ、通信（観測＋モック）、ビジュアルリグレッション、システムアラート突破 | macOS + Simulator。APIキーはシステムアラート部分のみ |
| **[record](showcase/README.ja.md)** | `make -C demos record` | 起動中アプリに対する**本物の Claude** による著作、続けて改変 → 自己修復ループ | macOS + Simulator + Claude（CLI または APIキー） |

> **Mac / Simulator が無い場合は？** `tour` の物語全体は、メモリ上の仮想デバイスに対しても動きます。
> Simulator も Xcode も APIキーも要らず、Linux/CI で数秒です: `uv run python demos/tour/tour.py`（機能
> ショーケースは `uv run python demos/showcase/run_demo.py`）。[`tour/README.ja.md`](tour/README.ja.md)
> を参照してください。これらは素早い最初の一歩で、上記の `make` ターゲットが実機での本番です。

> **web（Playwright）バックエンド。** 別のデモが小さな静的 web アプリをブラウザで操作します。Mac も
> Simulator も不要で、Linux 上で動きます: `make -C demos/web e2e`（[`web/README.md`](web/README.md)）。
> シナリオ形式も決定的ランナーも同じで、**違うのは backend だけ**です。backend を差し替えればプラット
> フォームが変わる、という要点をそのまま示します。

> **公開 URL を対象にした web バックエンド。** [`docs-site/`](docs-site/README.ja.md) は、ローカルの
> フィクスチャではなく公開中の Bajutsu ドキュメントサイト（<https://bajutsu-e2e.github.io/bajutsu/>）に
> Playwright backend を向けます。配信するサーバはなく、backend はその URL へ直接ナビゲートします:
> `uv run bajutsu run --target docs --backend web --config demos/docs-site/docs-site.config.yaml`。
> このサイトは `data-testid` の id を持たないため、シナリオは可視テキストと種別で要素を指定します。また
> `smoke` はライブのコピーに対してアサートするので、コードではなくドキュメントの改修で壊れることがあります。

## どれを実行すればいい？

- **実機での最初の一歩？** → [`tour`](tour/README.ja.md)。コマンド一つで showcase カタログの馬を開いて
  Favorite をオンにする流れを Simulator 上で実行し、続けて2通りに壊して、決定論的なチェックと `triage`
  の自己修復を見せます（APIキー不要）。
- **証跡一式をブラウザで見たい？** → [`webui`](showcase/WEBUI.ja.md)。Web UI ツアーがデバイスを起動し、
  スクリーンショット、動画、通信、ビジュアルリグレッション、システムアラート処理を見せます。iOS 開発者
  向けの目玉デモです。
- **AI 著作をコマンドラインで見たい？** → [`record`](showcase/README.ja.md)。

## 二層構造を具体的に

どのデモも同じ二層設計（[README](../README.ja.md#中核原則)）の一例です。

- **Tier 1：AI は著者であり、失敗の調査役。** シナリオを*書き*（`record`、Web UI の Record タブ）、
  失敗を*調査*し（`triage`）、実行中のシステムアラートを処理します。合否は決して判定しません。
- **Tier 2：決定論的な実行がゲート。** `run` は AI なしでシナリオを再生し、合否は機械的に検証できる
  アサーションだけで決まり、それを裏付ける証跡が添付されます。

`tour` と `features` は Tier 2 だけを動かします（決定論、APIキー不要）。`record` は Tier 1 の著作を
加え、`webui` は両方を一つのウィンドウで見せます。仮想デバイスの `tour.py` は両層をメモリ上のドライバ
に対して動かすので、何もインストールせずに境目が見えます。

## アプリ

- **`showcase`**（[`showcase/`](showcase/README.ja.md)）。唯一の iOS フィクスチャです。*同じ*アプリを UIKit と
  SwiftUI で書き、各々をアクセシビリティ有/無の変種にしたものです（2 コードベースで 4 プロダクト）。
  `record`、`crawl`、`run` を一度に行使するために作られています。上記の iOS デモ（`tour`、`features`、
  `webui`、`record`）と実機 CI ワークフローは、すべてこれを対象にします。
- **`web`**（[`web/`](web/README.md)）。**Playwright** バックエンドが Linux 上で操作する小さな静的 web
  アプリです（Mac / Simulator 不要）。コアが backend 非依存であることを示すクロスプラットフォームのデモです。

> 旧来の単一変種のフィクスチャ（`demo`・`sample`・`sample2`）は showcase に置き換えられ、退役・削除
> されました（BE-0079）。デモのメニューも CI も、これらを対象にしていません。

生成シナリオと作業コピー（`*/generated.yaml`、`tour/scenario.yaml`）、Xcode プロジェクト、実行成果物
（`runs/`）は gitignore されています。デモが再生成します。

[English](README.md) · **日本語**

# Bajutsu のデモ

実行できるデモ群で、どれも実機 iOS Simulator を操作します。どれも同じ中心的な物語 —
**シナリオが決定論的なハブであり、実行（run）の合否は機械的なアサーションだけで決まる（LLM は判定に
関与しない）** — を、それぞれ違う深さで見せます。どのデモも `make -C demos <target>` という一つの入口で
実行します:

```bash
make -C demos            # メニュー（make -C demos help と同じ）
make -C demos tour       # 決定論（APIキー不要）: 実機で run -> 改変 -> 診断
make -C demos features   # 決定論（APIキー不要）: シナリオ機能のショーケース
make -C demos offline    # 上記2つの決定論デモをまとめて実行
make -C demos webui      # Web UI ツアー — あらゆる証跡
make -C demos record     # 本物の Claude による著作、続けて改変 + triage
```

| デモ | コマンド | 何を証明するか | 何が必要か |
|---|---|---|---|
| **[tour](tour/README.ja.md)** | `make -C demos tour` | ライフサイクル全体 — run → 改変 → 診断（`triage`）— を実機 Simulator 上で完全に決定論的に | macOS + Simulator（idb は自動インストール）。**APIキー不要** |
| **[features](features/README.ja.md)** | `make -C demos features` | シナリオ著作の機能（タグ、パラメータ化された共有ステップ、シークレット）を実機 Simulator 上で | macOS + Simulator。**APIキー不要** |
| **[webui](features/WEBUI.ja.md)** | `make -C demos webui` | **Web UI** が Simulator を操作し、あらゆる証跡を収集: スクリーンショット・動画・ログ・通信（観測＋モック）・ビジュアルリグレッション・システムアラート突破 | macOS + Simulator。APIキーはシステムアラート部分のみ |
| **[record](record/README.ja.md)** | `make -C demos record` | 起動中アプリに対する**本物の Claude** による著作、続けて改変 → 自己修復ループ | macOS + Simulator + Claude（CLI または APIキー） |

> **Mac / Simulator が無い場合は？** `tour` の物語全体は、メモリ上の仮想デバイスに対しても動きます —
> Simulator も idb も APIキーも要らず、Linux/CI で数秒: `uv run python demos/tour/tour.py`（機能
> ショーケースは `uv run python demos/features/run_demo.py`）。[`tour/README.ja.md`](tour/README.ja.md)
> を参照。これらは素早い最初の一歩で、上記の `make` ターゲットが実機での本番です。

## どれを実行すればいい？

- **実機での最初の一歩？** → [`tour`](tour/README.ja.md)。コマンド一つで onboarding → login →
  カウンターの流れを Simulator 上で実行し、続けて2通りに壊して、決定論的なチェックと `triage` の自己
  修復を見せます — APIキー不要。
- **証跡一式をブラウザで見たい？** → [`webui`](features/WEBUI.ja.md)。Web UI ツアーがデバイスを起動し、
  スクリーンショット・動画・通信・ビジュアルリグレッション・システムアラート処理を見せます。iOS 開発者
  向けの目玉デモです。
- **AI 著作をコマンドラインで見たい？** → [`record`](record/README.ja.md)。

## 二層構造を具体的に

どのデモも同じ二層設計（[README](../README.ja.md#中核原則)）の一例です:

- **Tier 1 — AI は著者であり、失敗の調査役。** シナリオを*書き*（`record`、Web UI の Record タブ）、
  失敗を*調査*し（`triage`）、実行中のシステムアラートを処理します。合否は決して判定しません。
- **Tier 2 — 決定論的な実行がゲート。** `run` は AI なしでシナリオを再生し、合否は機械的に検証できる
  アサーションだけで決まり、それを裏付ける証跡が添付されます。

`tour` と `features` は Tier 2 だけを動かします（決定論・APIキー不要）。`record` は Tier 1 の著作を
加え、`webui` は両方を一つのウィンドウで見せます。仮想デバイスの `tour.py` は両層をメモリ上のドライバ
に対して動かすので、何もインストールせずに境目が見えます。

## アプリ

- **`demo`**（[`app/`](app/README.ja.md)）— onboarding → login → カウンターに絞った、物語全体を語る最小の
  アプリ。`tour` と `features` デモがこれを対象にします。
- **`sample`**（[`features/app/`](features/app/README.md)）— サポートする*あらゆる*操作と証跡の種類を
  網羅するために作られた SwiftUI のフィクスチャ。`webui` ツアーと実機 CI ワークフローがこれを対象に
  します。
- **`sample2`**（[`record/app/`](record/README.ja.md)）— `record` の AI 著作デモが使う、最小限の
  onboarding → login → カウンターのアプリ。

生成シナリオ・作業コピー（`*/generated.yaml`、`tour/scenario.yaml`）、Xcode プロジェクト、実行成果物
（`runs/`）は gitignore されています — デモが再生成します。

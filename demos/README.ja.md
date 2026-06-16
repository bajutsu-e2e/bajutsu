[English](README.md) · **日本語**

# Bajutsu のデモ

必要なセットアップの軽い順に、実行できるデモが3本あります。どれも同じ中心的な物語 —
**自然言語の目標が決定論的なシナリオになり、実行（run）の合否は機械的なアサーションだけで決まる（LLM
は判定に関与しない）** — を、それぞれ違う深さで見せます。上から順に、手元のセットアップで届く範囲まで
辿ってください。

どのデモも同じ方法 — `make -C demos <target>` という一つの入口 — で実行できます:

```bash
make -C demos            # メニュー（make -C demos help と同じ）
make -C demos tour       # セットアップ不要: ライフサイクル全体を仮想デバイスで
make -C demos features   # セットアップ不要: シナリオ機能のショーケース
make -C demos offline    # 上記2つ（セットアップ不要のデモ）をまとめて実行
make -C demos webui      # 実機: Web UI ツアー（macOS + Simulator）
make -C demos record     # 実機: AI 著作 -> 実行 -> 改変 -> triage
```

| デモ | コマンド | 何を証明するか | 何が必要か |
|---|---|---|---|
| **[tour](tour/README.ja.md)** | `make -C demos tour` | ライフサイクル全体（著作 → 実行 → 改変 → 診断）を本物のパイプラインで端から端まで | **何も不要** — Simulator も idb も API キーも要らず、Linux/CI で数秒 |
| **[features](features/WEBUI.ja.md)** | `make -C demos webui` | **Web UI** が実機 Simulator を操作し、あらゆる証跡を収集: スクリーンショット・動画・ログ・通信（観測＋モック）・ビジュアルリグレッション・システムアラート突破 | macOS + Simulator（idb は自動インストール）。API キーはシステムアラート部分のみ |
| **[record](record/README.ja.md)** | `make -C demos record` | 起動中アプリに対する**本物の Claude** による著作と、改変 → 自己修復（`triage`）ループを CLI で | macOS + Simulator + idb + Claude（CLI または API キー） |

[features](features/README.ja.md) フォルダには**セットアップ不要の機能ショーケース**もあります —
シナリオ著作の機能（タグ、共有ステップ、データ駆動、シークレット、デバイス制御）を FakeDriver で動かします:
`make -C demos features`。

## どれを実行すればいい？

- **Bajutsu が何かをまず理解したい？** → [`tour`](tour/README.ja.md)。コマンド一つ・セットアップ不要で、
  ブラウザで開ける本物の `report.html` も生成します。以降すべての入口になります。
- **Simulator を持つ iOS 開発者で、実際に動かして見たい？** → [`features`](features/WEBUI.ja.md)。
  Web UI ツアーがデバイスを起動し、サンプルアプリを動かし、証跡一式をブラウザで見せます。目玉のデモです。
- **AI 著作 + 自己修復ループをコマンドラインで見たい？** → [`record`](record/README.ja.md)。

## 二層構造を具体的に

どのデモも同じ二層設計（[README](../README.ja.md#中核原則)）の一例です:

- **Tier 1 — AI は著者であり、失敗の調査役。** シナリオを*書き*（`record`、Web UI の Record タブ）、
  失敗を*調査*し（`triage`）、実行中のシステムアラートを処理します。合否は決して判定しません。
- **Tier 2 — 決定論的な実行がゲート。** `run` は AI なしでシナリオを再生し、合否は機械的に検証できる
  アサーションだけで決まり、それを裏付ける証跡が添付されます。

`tour` は両層を仮想デバイスに対して動かすので、何もインストールせずに境目が見えます。`features` と
`record` は実機 Simulator に対して動かします。

## サンプルアプリ

- **`sample`**（[`features/app/`](features/app/README.md)）— サポートする*あらゆる*操作と証跡の種類を
  網羅するために作られた SwiftUI のフィクスチャ。`features` の Web UI ツアーと実機 CI ワークフローが
  これを対象にします。
- **`sample2`**（[`record/app/`](record/README.ja.md)）— `record` ライフサイクルデモが使う、最小限の
  オンボーディング → ログイン → カウンターのアプリ。`tour` はこの同じ流れをメモリ上で模倣します。

生成されたシナリオ（`*/generated.yaml`）と実行成果物（`runs/`）は gitignore されています — デモを実行
すれば再生成されます。

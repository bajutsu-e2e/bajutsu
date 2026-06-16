[English](README.md) · **日本語**

# 機能サンプル

> **Web UI ツアーをお探しですか？** ブラウザから実機 Simulator を操作し、あらゆる証跡（スクリーン
> ショット、動画、ログ、通信、ビジュアルリグレッション、システムアラート）を収集するには、
> **[WEBUI.ja.md](WEBUI.ja.md)** を参照してください — iOS 開発者向けの目玉デモです。すべてのデモの地図は
> [`demos/README.ja.md`](../README.ja.md) にあります。

シナリオ著作の機能（タグ、パラメータ化された共有ステップ、データ駆動の実行、シークレット変数、
デバイス制御）の、実行できるデモです。

## Simulator なしで実行（FakeDriver）

本物の `load → expand → run` パイプラインをメモリ上の FakeDriver に対して動かし、各機能が何をしたかを
出力します — Simulator も idb も不要です:

```bash
make -C demos features               # または直接:
uv run python demos/features/run_demo.py
```

読み込む機能ごとのシナリオファイル: [`tags.yaml`](tags.yaml)、
[`shared_steps.yaml`](shared_steps.yaml)（＋[`_components/login.yaml`](_components/login.yaml)）、
[`data_driven.yaml`](data_driven.yaml)、[`secrets.yaml`](secrets.yaml)、
[`device.yaml`](device.yaml)。

## 実機 Simulator で実行（idb バックエンド）

[`sample_features.yaml`](sample_features.yaml) は同梱の `BajutsuSample` アプリに対して動きます。共有
ステップ＋シークレット入力＋デバイス制御を、すべて**落ち着いた Home 画面**（`SAMPLE_LOGGED_IN=1`）で
行うので、アサーションは決定論的です。

```bash
# 先にサンプルをビルド（make -C demos/features sample-build; demos/features/app/README.md を参照）してから:
PASSWORD='s3cr3t' uv run bajutsu run --scenario demos/features/sample_features.yaml \
  --app sample --config demos/features/demo.config.yaml --no-erase --no-network
```

[`demo.config.yaml`](demo.config.yaml) は `secrets: [PASSWORD]` を宣言しているので、
`${secrets.PASSWORD}` が環境変数から解決され、その実際の値はすべての実行成果物でマスクされます。

> **Home 画面についての注記:** idb の `describe-all` は、画面遷移中（例: ログイン送信直後）に一瞬空の
> アクセシビリティツリーを返すことがあり、遷移先での `wait`/`expect` が不安定になり得ます — 上記の機能
> とは無関係です。落ち着いた画面でアサーションすればこれを避けられます。空ツリーで再試行するよう idb
> ドライバを堅牢化することは M1 の実機作業として追跡しています。

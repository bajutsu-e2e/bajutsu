[English](README.md) · **日本語**

# 機能サンプル

> **Web UI ツアーをお探しですか？** ブラウザから実機 Simulator を操作し、あらゆる証跡（スクリーン
> ショット、動画、ログ、通信、ビジュアルリグレッション、システムアラート）を収集するには、
> iOS 開発者向けの目玉デモである **[demos/showcase/WEBUI.ja.md](../showcase/WEBUI.ja.md)** を参照して
> ください。すべてのデモの地図は [`demos/README.ja.md`](../README.ja.md) にあります。

シナリオ著作の機能（タグ、パラメータ化された共有ステップ、データ駆動の実行、シークレット変数、
デバイス制御）を3通りで見せます。

## 実機 Simulator で（`make -C demos features`）

機能シナリオを、[showcase](../showcase/README.ja.md) アプリに対して起動中の Simulator 上で idb 経由で
実行します。タグ、パラメータ化された共有コンポーネント、シークレット変数を使います。決定論的で、
**APIキー不要**です:

```bash
make -C demos features
```

これは [`demos/showcase/scenarios/menu/features.yaml`](../showcase/scenarios/menu/features.yaml) の
`smoke` タグ付きシナリオ（`slow` のものを除く）を実行し、共有の「ナビゲート＋シード」コンポーネント
[`_components/search_for.yaml`](../showcase/scenarios/menu/_components/search_for.yaml) を展開し、
`${secrets.PASSWORD}` を環境変数から解決します（実際の値はすべての実行成果物でマスク）。`--tag` /
`--exclude` で別の部分集合を選べます。

## Mac が無い場合は？ 全カタログを仮想デバイスで（FakeDriver）

本物の `load → expand → run` パイプラインをメモリ上の FakeDriver に対して動かし、各機能が何をしたかを
出力します。Simulator も idb も不要で、**全種類**（タグ、共有ステップ、データ駆動、シークレット、
デバイス制御）を網羅します:

```bash
uv run python demos/features/run_demo.py
```

読み込む機能ごとのシナリオファイル: [`tags.yaml`](tags.yaml)、
[`shared_steps.yaml`](shared_steps.yaml)（＋[`_components/login.yaml`](_components/login.yaml)）、
[`data_driven.yaml`](data_driven.yaml)、[`secrets.yaml`](secrets.yaml)、
[`device.yaml`](device.yaml)。

> 上記のオフラインカタログは FakeDriver による機能リファレンスで、実機での機能ツアーは本ページ冒頭の
> `make -C demos features`（showcase 実行）です。（このディレクトリにある旧来の `sample` アプリ向け機能
> シナリオは showcase に置き換えられ、削除を予定しています。BE-0079。）

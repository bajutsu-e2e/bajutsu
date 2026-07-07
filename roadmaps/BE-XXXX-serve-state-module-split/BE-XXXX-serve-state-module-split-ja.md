[English](BE-XXXX-serve-state-module-split.md) · **日本語**

# BE-XXXX — serve のジョブ状態をジョブ実行から分離する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-serve-state-module-split-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

`bajutsu/serve/jobs.py`（763 行）には、性質の異なる 2 つの責務が同居しています。serve パッケージの
大半が読む**状態コンテナ**（`ServeState`、`Job`、`StoreBundle`、`CaptureSession`）と、3 モジュール
しか触らない**ジョブ実行エンジン**（`run_job`、`cancel_job`、プロセス起動、デバイスの boot、アプリの
ビルド）です。本項目では状態側を `bajutsu/serve/state.py` に分離し、同じ凝集度の改善として、
`serve/helpers.py` に紛れている CLI コマンドビルダー群を `bajutsu/serve/commands.py` に切り出します。
どちらも挙動を変えない移動です。

## 動機

このモジュールの docstring 自身が、二重の役割を認めています（「Job lifecycle: **state**, spawning,
cancellation, device boot, and app build」）。2 つの半身の関係は一方向です。実行側の関数は
`ServeState` のフィールドを読み `Job` を書き換えますが、状態側から実行側を呼ぶ箇所はありません。
`serve/jobs.py` を import する約 28 モジュール（`handler.py`、`authz.py`、`operations/*.py` の全部、
`server/app.py` など）の大多数は `ServeState`（ときに `Job`、`_scenarios_dir_for`、`_DEFAULT_ORG`）
だけを使い、`run_job` や `cancel_job` に触るのは `serve/executor.py`、`serve/server/worker_job.py`、
`serve/__init__.py` の 3 つだけです。

状態と実行を 1 モジュールに置いていることは、コメントで明示された 2 箇所の lazy import による回避策
も強いています。`serve/executor.py:30`（「local import breaks the jobs↔executor cycle」）と、
`ServeState._env_var_for_secret` 内の `operations.config` の遅延 import です。この循環は、実行
エンジンを import すると状態まで一緒に付いてくる現状の構造が生んでいます。状態が独立した
モジュールになれば、`executor` は状態をトップレベルで import でき、遅延させるのは `run_job` だけで
済みます。

`serve/helpers.py`（656 行）にも、規模は小さいものの同じ形の問題があります。コマンドビルダー群
（`run_command`、`record_command`、`crawl_command`、`triage_command` と `_int` の約 240 行）は
ファイル内の他の何も参照せず、依存は `serve/_cli_flags.flag_args` だけで、利用者も
`operations/dispatch.py`、`operations/triage.py`、`serve/__init__.py` の 3 つに限られます。
docstring が「クエリと検証のヘルパ」と説明するファイルの中に、自己完結した別の単位が
紛れている状態です。

## 詳細設計

どちらの移動も `bajutsu.serve` の内側で完結するため、import 境界の契約（BE-0112）には影響しません。
移行には BE-0127 が確立した再エクスポートのファサードパターンを使います。旧モジュールが
再エクスポートを保持したまま利用側を移行し、最後にシムを取り除きます。

1. `bajutsu/serve/state.py` を新設し、`Job`、`StoreBundle`、`CaptureSession`、`ServeState`、
   `_scenarios_dir_for`、`_DEFAULT_ORG` を移します（シグネチャ変更のない純粋な移動）。
2. `serve/jobs.py` には実行側の関数（`run_job`、`cancel_job`、`send_response`、`_spawn_env`、
   `_boot_devices`、`_build_app`、`_persist_run` など）を残し、`state` を import します
   （一方向で循環なし）。移行期間中は移した名前を再エクスポートします。
3. 約 28 の利用側を `serve.state` に移行し、再エクスポートのシムを取り除きます。
4. 分離で不要になる lazy import を解消します。`serve/executor.py` は状態モジュールをトップレベルで
   import し、`_env_var_for_secret` の `operations.config` 遅延 import も見直します。
5. `serve/helpers.py` から `run_command`、`record_command`、`crawl_command`、`triage_command`、
   `_int` を `bajutsu/serve/commands.py` に切り出し、3 つの利用側を更新します。
6. serve のモジュール一覧が対象モジュールに言及している箇所について、`docs/architecture.md`
   （と `docs/ja/architecture.md`）を更新します。

## 検討した代替案

- **現状維持。** 状態しか使わない利用側が、プロセス起動や Simulator まわりの機構と名目上結合した
  ままになり、2 箇所の lazy import 回避策も構造を支え続けます。hosted モードの状態フィールドと
  実行系の機能は独立に増えるので、ファイルは両方の軸で育ち続けます。
- **serve パッケージ全体の再レイヤリング。** トランスポート間で共有される操作本体は BE-0127 が
  すでに分割済みで、この 1 つの継ぎ目のためにパッケージ配置全体を再検討するのは、根拠に対して
  過剰な変更です。
- **`ServeState` を `serve/__init__.py` に移す。** そこには合成ルート（`_build_server_state`）が
  すでにあり、クラス本体まで置くと状態モジュールに名前を与える代わりにパッケージの窓口が
  肥大します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `serve/state.py` を新設し状態コンテナを移動（純粋な移動）
- [ ] `serve/jobs.py` を実行エンジンに縮小（一時的な再エクスポート付き）
- [ ] 利用側を移行し、再エクスポートのシムを削除
- [ ] 分離で不要になった lazy import 回避策を解消
- [ ] `serve/commands.py` を `serve/helpers.py` から切り出し、利用側を更新
- [ ] `docs/architecture.md`（en/ja）を更新

## 参考

- [`bajutsu/serve/jobs.py`](../../bajutsu/serve/jobs.py) · [`bajutsu/serve/helpers.py`](../../bajutsu/serve/helpers.py)
- [BE-0127](../BE-0127-split-serve-operations-module/BE-0127-split-serve-operations-module-ja.md) — 本項目が再利用するファサードパターンを確立した serve operations の分割
- [BE-0112](../BE-0112-layer-boundary-enforcement/BE-0112-layer-boundary-enforcement-ja.md) — 移動が守るべき import 境界の契約

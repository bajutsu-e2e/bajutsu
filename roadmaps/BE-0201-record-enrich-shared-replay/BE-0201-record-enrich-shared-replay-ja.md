[English](BE-0201-record-enrich-shared-replay.md) · **日本語**

# BE-0201 — record と enrich の重複したリプレイヘルパを統合する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0201](BE-0201-record-enrich-shared-replay-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0201") |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

`bajutsu/enrich.py` は、`bajutsu/record.py` のヘルパ 2 つを簡略化した複製として再実装しています。
ステップのリプレイのディスパッチ（`record._execute` と `enrich._execute_step`）と、観測前の
アラート除去（両ファイルの `_clear_blocking`）です。本項目では各ペアを 1 つの実装に統合し、
わずかな挙動差は 2 つ目の複製ではなくパラメータとして表現します。

## 動機

どちらのペアも、同じロジックを二重に保守している状態です。

- **ステップのリプレイ**（`record.py:266-274` と `enrich.py:26-37`）：同じ `_action_of` の
  ディスパッチ（wait、`assert_` の no-op、`_do_action`）です。違いは wait 失敗時の扱いだけで、
  enrich は `_wait` の `(ok, reason)` を検査して `_ReplayFailed` を送出し、record は戻り値を
  無視します。
- **アラート除去**（両ファイルの `_clear_blocking`）：同じ
  `for _ in range(max_tries)`、`shows_app_ui`、`guard(driver)`、`clock.sleep(0.5)` のループと、
  似た「screen … blocked …」のメッセージ（record: "the app screen looks blocked …"、enrich:
  "screen blocked …"）です。`record.py` 側が豊かな実装（dismiss したラベルを返し、各 dismiss を
  報告する）で、`enrich.py` 側は guard の戻り値を捨てる簡略版の複製です。

独立に保守される複製は、時間とともに互いにずれていきます。スクリーンショットのヘルパが
まさに同じ経緯をたどり、BE-0132 が `record._screenshot_bytes` への統合で解消しました
（`enrich.py` はすでにこれを import しています）。本項目は、同じファイルペアに残る 2 ペアに
ついて同じ統合を完了させるものです。

## 詳細設計

1. wait 失敗のフックを備えた共有のステップリプレイ実行器を 1 つ用意します。`record.py` から
   エクスポートする（または両者の脇の小さな共有モジュールに置く）形とし、`on_wait_failure`
   コールバック（または戻り値のステータス）によって、enrich は `_ReplayFailed` を送出し、
   record は従来どおり結果を無視できるようにします。
2. `_clear_blocking` は報告機能を持つ `record.py` 側の 1 実装に統一し、`enrich.py` はそれを
   呼んで不要な戻り値を捨てます。
3. 今日異なっている 2 つの挙動（enrich は wait 失敗でリプレイを失敗させる、record は続行する）
   をユニットテストで固定し、統合が両者を保存することを証明します。

## 検討した代替案

- **複製のまま残す。** 既知のずれのパターン（BE-0132 の動機）が繰り返されます。アラート除去の
  ループやディスパッチへの修正が、片側だけに着地します。
- **共有ヘルパを `orchestrator` に移す。** レイヤが誤りです。これらのヘルパは AI による
  オーサリングと enrich の経路（Tier 1）のためのもので、決定的コアに周縁向けのフックを
  生やすわけにはいきません（BE-0112 は「コア ← 周縁」の方向を守ります）。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] wait 失敗フック付きの共有ステップリプレイ実行器を導入し、両呼び出し側を移行
- [x] `_clear_blocking` を 1 実装（record の報告版）に統一し、enrich を移行
- [x] 異なる 2 つの wait 失敗挙動を固定するユニットテスト

**ログ**

- `record._execute` に `on_wait_failure` フックを追加し、enrich のリプレイをこれに移行しました
  （重複していた `_execute_step` は削除）。`enrich._clear_blocking` は record の報告版を import する
  形に置き換えました。wait タイムアウト時に record は先へ記録を進め、enrich のフックは
  `_ReplayFailed` を送出する、という 2 つの挙動を固定するユニットテストを追加しました。

## 参考

- [`bajutsu/record.py`](../../bajutsu/record.py) · [`bajutsu/enrich.py`](../../bajutsu/enrich.py)
- [BE-0132](../BE-0132-dedupe-crawl-screenshot-helpers/BE-0132-dedupe-crawl-screenshot-helpers-ja.md) — 同じファイルペアに対して本項目が完了させる、先行した統合

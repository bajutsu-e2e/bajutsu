[English](BE-0200-run-id-contract.md) · **日本語**

# BE-0200 — run id のフォーマットを 1 つの名前付き契約にする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0200](BE-0200-run-id-contract-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0200") |
| 実装 PR | [#808](https://github.com/bajutsu-e2e/bajutsu/pull/808) |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

run id は `datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")` として、4 箇所で独立に刻印されています。
`cli/commands/run.py`、`cli/commands/crawl.py`、`serve/operations/dispatch.py`、そして `audit-`
変種を持つ `cli/commands/audit.py` です。このフォーマットは局所的な詳細ではありません。別のコードが
それをパースし直して開始時刻を導出し、Web UI はそれで履歴を並べ替えています。それにもかかわらず、
コードベースのどこにも名前がありません。本項目では `new_run_id()` ヘルパ（フォーマット定数と
タイムスタンプ形状のパターンを含む）を 1 つ導入し、刻印箇所とタイムスタンプの利用箇所をそこに
向けます。

## 動機

このタイムスタンプのフォーマットは、複数の独立した利用者を持つ横断的な契約です。

- `serve/jobs.py:47` は CLI の出力から run id を正規表現でパースし直します（タイムスタンプ形状
  ではなく、安全なセグメントのパターンです）。
- `bajutsu/report/ctrf.py` は `YYYYmmdd-HHMMSS` の形状を `strptime` でパースし、UTC の開始時刻を
  導出します。
- `serve/helpers.py` の `valid_run_id` はパス安全性のチェックです（安全な 1 パスセグメント
  `^[A-Za-z0-9][A-Za-z0-9._-]*$` かどうかを見て、`runs_dir / run_id` が外へ抜けないようにする）。
  タイムスタンプ形式の検証ではなく、タイムスタンプでない、クライアント由来の id も意図的に受け入れます。
- Web UI は実行履歴を辞書順で並べ替えます。これが時系列順になるのは、id がまさにこの形の
  ゼロ埋め UTC タイムスタンプだからです。

今日この契約は慣習だけで成り立っています。4 つの呼び出し箇所が `strftime` のパターンを繰り返し、
`report/ctrf.py` がパースし直すときにその形状を繰り返しています。どこか 1 箇所への善意の変更
（タイムゾーンの調整、精度の引き上げ、区切り文字の変更）が、他の場所のタイムスタンプのパースや
履歴の並び順を静かに壊します。契約に一度だけ名前を与えれば、依存関係が見えるようになり、
フォーマットは 1 箇所で変更できます。

## 詳細設計

1. 決定的コアの小さなヘルパ（たとえば `bajutsu/run_id.py`）を追加します。フォーマット定数
   （`RUN_ID_FORMAT = "%Y%m%d-%H%M%S"`）、`new_run_id()` ファクトリ、そしてタイムスタンプの意味を
   必要とする利用者向けのタイムスタンプ形状パターンを置きます。これは `valid_run_id` とは分けて
   おき、`valid_run_id` はパス安全性のチェックのまま残します。
2. 4 つの run id 刻印箇所を `new_run_id()` に置き換えます。`cli/commands/run.py`、
   `cli/commands/crawl.py`、`serve/operations/dispatch.py`、そして `cli/commands/audit.py` の
   `audit-` 変種です。
3. タイムスタンプ形状の利用者である `report/ctrf.py` の `strptime` パースを共有のフォーマットや
   パターンに向け、フォーマット変更が 1 箇所で済むようにします。`serve/jobs.py` の出力パース正規表現と
   `valid_run_id` はそのまま残します。どちらもタイムスタンプ形状を検証しておらず、`valid_run_id` を
   厳格化すると正当なクライアント由来 id を拒否してしまうからです。
4. 契約を固定するユニットテストを追加します（刻印 → パース → 辞書順が時系列順に一致）。

## 検討した代替案

- **4 つの複製のまま残す。** 契約は慣習だけで成り立ち続けます。失敗の形は静かで、履歴の並び順や
  タイムスタンプのパースが、原因を名指しするテストのないまま壊れます。
- **ランダムな id や UUID にする。** UI が依存する性質（辞書順 = 時系列順）と、人が読める run
  ディレクトリ名を失います。1 ホストあたり秒粒度で衝突は実用上起きないので、得るものが
  ありません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] ヘルパモジュールを追加（`bajutsu/run_id.py`: `RUN_ID_FORMAT`、`new_run_id()`、`parse_run_id_timestamp()`）
- [x] 4 つの run id 刻印箇所を移行（`audit-` 変種を含む）
- [x] タイムスタンプ形状の利用者（`report/ctrf.py`）を共有フォーマットに向ける
- [x] 契約を固定するユニットテストを追加（`tests/test_run_id.py`）

## 参考

- [`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py) · [`bajutsu/cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py) · [`bajutsu/cli/commands/audit.py`](../../bajutsu/cli/commands/audit.py) · [`bajutsu/serve/operations/dispatch.py`](../../bajutsu/serve/operations/dispatch.py) · [`bajutsu/serve/jobs.py`](../../bajutsu/serve/jobs.py) · [`bajutsu/report/ctrf.py`](../../bajutsu/report/ctrf.py) · [`bajutsu/serve/helpers.py`](../../bajutsu/serve/helpers.py)

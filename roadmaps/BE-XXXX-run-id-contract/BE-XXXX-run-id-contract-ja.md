[English](BE-XXXX-run-id-contract.md) · **日本語**

# BE-XXXX — run id のフォーマットを 1 つの名前付き契約にする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-run-id-contract-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

run id は `datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")` として、5 箇所（`cli/commands/run.py`、
`cli/commands/crawl.py`、`serve/operations/dispatch.py`、`serve/helpers.py`、そして `audit-`
変種を持つ `cli/commands/audit.py`）で独立に刻印されています。このフォーマットは局所的な詳細では
なく、別のコードがそれをパースし直し、検証し、それで並べ替えています。それにもかかわらず、
コードベースのどこにも名前がありません。本項目では `new_run_id()` ヘルパ（とフォーマット定数）を
1 つ導入し、刻印、検証、パースの各箇所をそこに向けます。

## 動機

このタイムスタンプのフォーマットは、独立した 3 つの利用者を持つ横断的な契約です。

- `serve/jobs.py:47` は CLI の出力から run id を正規表現でパースし直します。
- `serve/helpers.py` の `valid_run_id` は API 境界で id を検証します。
- Web UI は実行履歴を辞書順で並べ替えます。これが時系列順になるのは、id がまさにこの形の
  ゼロ埋め UTC タイムスタンプだからです。

今日この契約は慣習だけで成り立っています。5 つの呼び出し箇所が `strftime` のパターンを繰り返し、
正規表現と検証器がその形を繰り返しています。どこか 1 箇所への善意の変更（タイムゾーンの調整、
精度の引き上げ、区切り文字の変更）が、他の場所のパースや履歴の並び順を静かに壊します。契約に
一度だけ名前を与えれば、依存関係が見えるようになり、フォーマットは 1 箇所で変更できます。

## 詳細設計

1. 決定的コアに小さなヘルパ（例: `bajutsu/run_id.py`）を追加します。フォーマット定数、`audit-`
   変種も扱う `new_run_id(prefix="")`、対応する検証パターンを置き、docstring に上記 3 つの
   利用者を明記します。
2. 5 つの刻印箇所を `new_run_id()` に置き換えます。
3. `valid_run_id` と `serve/jobs.py` のパース用正規表現を共有パターンに向けます。
4. 契約を固定するユニットテストを追加します（刻印 → 検証 → 辞書順が時系列順に一致）。

## 検討した代替案

- **5 つの複製のまま残す。** 契約は慣習だけで成り立ち続けます。失敗の形は静かで、履歴の並び順や
  run id のパースが、原因を名指しするテストのないまま壊れます。
- **ランダムな id や UUID にする。** UI が依存する性質（辞書順 = 時系列順）と、人が読める run
  ディレクトリ名を失います。1 ホストあたり秒粒度で衝突は実用上起きないので、得るものが
  ありません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `new_run_id()`、フォーマット定数、検証パターンを 1 モジュールに用意
- [ ] 5 つの刻印箇所を移行
- [ ] `valid_run_id` と jobs.py のパース用正規表現がパターンを共有
- [ ] 契約のユニットテスト（刻印 → 検証 → 並び順）

## 参考

- [`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py) · [`bajutsu/cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py) · [`bajutsu/cli/commands/audit.py`](../../bajutsu/cli/commands/audit.py) · [`bajutsu/serve/operations/dispatch.py`](../../bajutsu/serve/operations/dispatch.py) · [`bajutsu/serve/helpers.py`](../../bajutsu/serve/helpers.py) · [`bajutsu/serve/jobs.py`](../../bajutsu/serve/jobs.py)

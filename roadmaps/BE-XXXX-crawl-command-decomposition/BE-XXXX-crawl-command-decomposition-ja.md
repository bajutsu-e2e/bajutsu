[English](BE-XXXX-crawl-command-decomposition.md) · **日本語**

# BE-XXXX — crawl コマンドを run と同じ形に分解する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-crawl-command-decomposition-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

`bajutsu/cli/commands/crawl.py` の `crawl` 関数は、オプション宣言を除いて約 250 行あります。
まったく同じ問題を `run` コマンドについて BE-0143 がすでに解決し、出荷しています（frozen な
プランレコードと小さな `_resolve_*` ヘルパ群で、本体を薄く読める列に変える形）。本項目は、
確立済みのその型を `crawl` にも適用します。挙動は変えません。

## 動機

本体には、自己完結したいくつもの段階が 1 つの関数スコープを共有しています。

- **ウォームスタートの解決**（約 190〜232 行目）：`_load_base_map` と resume / continue の
  分岐で `(base_map, seed_path, seed_ops)` を作る部分です。入出力が明確で、固有のエラー処理を
  持ちます。
- **レーン計画**（約 234〜247 行目）、**進捗と永続化のコールバック**（約 295〜310 行目）、
  **alert guard の配線**（約 327〜339 行目）：それぞれ名前が自然に付く小さな単位です。

BE-0143 後の `run.py` は、目指す形と制約の両方を示しています。約 135 行の typer オプション
シグネチャはインラインのまま残します（serve は BE-0134 のフラグミラーで typer のメタデータを
そのまま参照し、`serve/helpers.py` の `crawl_command` は run と同じ方法で
`python -m bajutsu crawl …` の argv を組み立てます）。一方、本体はプランレコードとヘルパの列に
なります。ここでも同じ制約と同じ解法が当てはまり、分解の大部分は再設計ではなく命名と移動の
作業です。

## 詳細設計

1. 各段階が受け渡す解決済み入力を保持する frozen な `_CrawlPlan` レコードを導入します。
2. resume / continue のブロック（base map、seed path、seed ops）を `_resolve_warm_start(…)`
   に切り出します。
3. レーン計画、コールバック、guard 配線のヘルパを 1 つずつ切り出します（入出力は素朴な
   データ）。
4. `crawl` はオプションとヘルパの薄い列になります。typer のシグネチャには触れません
   （BE-0134 のミラー制約）。
5. 切り出したヘルパにユニットテストを付けます（素朴なデータを取るので Simulator は不要）。
   合成後のコマンドは既存の CLI テストが引き続き覆います。

## 検討した代替案

- **現状のまま。** 約 250 行の本体には、BE-0143 が run について記録したのと同じコストがあります。
  最初から最後まで読み通すのが難しく、コマンド全体の粒度でしかテストできず、どの 1 行の編集も
  何百行も前に積み上げた状態に触れうるため変更のリスクが高い、という点です。
- **`run.py` とプランの機構を共有する。** 2 つのプランに共通のフィールドはほとんどなく、共有
  抽象は無理に作る形になります。共有すべきは型（パターン）であってコードではありません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `_CrawlPlan` レコード
- [ ] `_resolve_warm_start` の切り出し
- [ ] レーン計画 / コールバック / guard 配線のヘルパ
- [ ] `crawl` 本体をオプションと薄い列に縮小（typer シグネチャは不変）
- [ ] 切り出したヘルパのユニットテスト

## 参考

- [`bajutsu/cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py)
- [BE-0143](../BE-0143-run-command-decomposition/BE-0143-run-command-decomposition-ja.md) — 本項目が crawl に適用する、出荷済みの型
- [BE-0134](../BE-0134-serve-cli-flag-mirror-drift/BE-0134-serve-cli-flag-mirror-drift-ja.md) — typer オプションシグネチャをインラインに保つ理由

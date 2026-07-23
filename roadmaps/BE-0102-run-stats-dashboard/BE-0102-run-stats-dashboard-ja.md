[English](BE-0102-run-stats-dashboard.md) · **日本語**

# BE-0102 — 実行結果の集計ダッシュボード

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0102](BE-0102-run-stats-dashboard-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0102") |
| 実装 PR | [#652](https://github.com/bajutsu-e2e/bajutsu/pull/652), [#654](https://github.com/bajutsu-e2e/bajutsu/pull/654) |
| トピック | オーサリング体験 |
<!-- /BE-METADATA -->

## はじめに

多数の run を 1 つのビューに集計して見せる、読み取り専用のダッシュボードです。表示するのは、時系列での pass 率、run 単位とシナリオ単位の所要時間、もっとも頻繁に失敗するシナリオやステップ、そして
[BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)
から取り込む各シナリオの flaky 分類です。今日のところ、各 run はそれぞれ単独では手厚く可視化されています（`report.html`）。しかし run をまたいだ推移を描くものは何もありません。このダッシュボードは、runner がすでに書き出しているデータ、すなわち run ごとの `manifest.json` と serve の run レコードを決定的に集計するだけのもので、LLM を使わず、いかなる判定（verdict）にも影響しません。カバレッジマップ（[BE-0050](../BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md)）や flaky 監査（BE-0049）と同じ系統に属します。

## 動機

1 つの run が答えるのは「この run は通ったか、どこで失敗したか」です。E2E を継続的に回しているチームには次の問いが生まれます。「スイート全体の傾向はどうなっているか」です。ところが Bajutsu には、それに答える面（surface）がありません。素材はすでに揃っていて、しかも欠落なく残っています。

- すべての run は `manifest.json`（[reporting.md](../../docs/reporting.md)）を書き出し、そこには最上位の判定、各シナリオの `ok` と `failure`、各ステップの `duration_s`、そして（BE-0049 による）`scenarioHash` と `toolVersion` と `gitRevision` を刻んだ `provenance` ブロックが含まれます。したがって `runs/` ディレクトリは、pass 率と所要時間と失敗履歴をすでに符号化しています。ただ集計されていないだけです。
- serve バックエンドは、終了した run を `runs` テーブル（`status`、`ok`、`created_at`、`summary` の JSONB）へ BE-0015 の 7c-4 のもとで記録します。ホスト構成のインスタンスは、同じ履歴をクエリしやすいストアに蓄積していきます。

足りないのは、この山を 1 枚の絵に変える層です。すなわち pass 率の推移線、もっとも遅いシナリオともっとも flaky なシナリオのランキング、そしてもっとも頻繁に落ちるステップやアサーションの一覧です。これは、Bajutsu がすでに備える 2 つの分析レポートに対する運用面の対になります。カバレッジ（BE-0050）は「どの面をテストしているか」に答え、監査（BE-0049）は「あるシナリオは再現可能か」に答えます。本項目が答えるのは「スイート全体は時間とともにどう推移しているか」です。run 単位の `report.html` と serve の run 一覧に対する自然な相棒でもあります。後者は今日のところ個々の run を見せるだけで、集計を持ちません。

prime directive と衝突しない理由を述べます。カバレッジや監査と同じく、どの数値もすでに取得済みのアーティファクトに対する決定的なカウントや集計であり、モデルも判断の介在もありません。ダッシュボードは判定を書き換えませんし、CI ゲートの一部にもなりません（チームがその数値を参考情報として追跡するのは構いません。BE-0050 が許すのと同じです）。アプリ非依存でもあります。`manifest.json` や run レコードを読むだけで、アプリ固有の作り込みはありません。集計のキーは BE-0049 の `scenarioHash` なので、描く推移は、真の flaky（content hash が変わらないまま判定が反転する場合）と、単にシナリオが編集された場合とを切り分けます。その provenance を再導出せず、そのまま消費します。

## 詳細設計

提案の粒度です。設計を縛る制約は、ダッシュボードが純粋に観測専用であることです。作業は次の 5 単位で MECE に分かれます。

- **1. 集計器（決定的な中核）**：run の manifest 群を走査し、後述のメトリクス集合を計算する純関数 `aggregate_runs(runs) -> Stats` です。シナリオを `(scenarioHash, name)`、すなわち BE-0049 の同一性でグルーピングします。これにより、無関係なシナリオが現れたり消えたりしても同じシナリオの履歴は 1 本の系列になり、内容の編集は古い系列を壊さず新しい系列を始めます。manifest を欠落なく読むために `report.load`（`results_from_manifest`）を再利用し、flaky 分類は BE-0049 の分類器を再利用して作り直しません。デバイスもネットワークも要らず、`FakeDriver` が生成した manifest を使って Linux ゲート上でテストできます。
- **2. メトリクス集合（何を計算するか）**：すでに存在する manifest フィールドに対する MECE な集合です。
  - **時系列の pass 率**：run と各シナリオの pass/fail を run 単位（および日単位）でバケットし、`backend`、target、tag で任意にグルーピングします。
  - **所要時間と性能**：run 全体の所要時間とシナリオ単位の所要時間（`steps[].duration_s` から）の分布、もっとも遅いシナリオのランキング、そして退行が見えるよう各シナリオの所要時間推移を示します。
  - **失敗ホットスポット**：もっとも頻繁に失敗するシナリオ、ステップ、アサーションを頻度順に並べ、繰り返し現れる `failure` の理由を浮かび上がらせます。
  - **flaky 度**：各シナリオの BE-0049 分類（`flaky`、`deterministic`、`unproven`）を取り込み、「再現するか」の軸を「どれくらいの割合で通るか」の軸の隣に置きます。
  - **ボリューム**：時系列の run 数を backend や target ごとに分け、各種の率を読む際の分母とします。
- **3. CLI の面（主）**：`bajutsu stats` です。`bajutsu coverage`（BE-0050）に倣い、`--runs <dir>` で run の集合を選び、`--html` で**自己完結した** HTML ダッシュボードを出力し（スタイルはインラインで、チャート描画は BE-0050 の精神に従って最小限かつ依存ライブラリなしに保ちます）、既定のテキスト／JSON 出力で同じ数値をスクリプトからも CI 公開からも扱えるようにします。これが要となるスライスです。Linux ゲート上で完結し、serve もデータベースも要らず、チームはカバレッジレポートとまったく同じように CI からこの HTML を公開できます。
- **4. serve の面（後続）**：`bajutsu serve` に **Stats** タブを設け、同じ集計器を run 履歴に対して呼び出してライブに描画します。ホスト構成でもローカルでも、UI は既存の run 単位レポート一覧の隣に推移ビューを得ます。読み出しは既存の serve の継ぎ目（seam）を通します。データベースが配線されていれば DB の `Repository` の run レコード（BE-0015 の 7c-4）を、そうでなければ `ArtifactStore` の `manifest.json` を読みます。したがって「DB があれば DB、なければアーティファクトストア」という確立済みのフォールバックに従い、新たな永続化は要りません（ローカルの serve と stdlib 経路はデータベースなしでそのまま動きます）。
- **5. スコープの番人（何ではないか）**：アサーションを再実行せず判定を再計算しません。記録済みの結果を集計するだけです（古い manifest に新しいフィールドが欠けていれば「未取得」として描画します。BE-0068 の再描画の規律に従います）。LLM を一切持ち込みません。CI ゲートではありません。チームがその数値に設けるしきい値は、チーム自身の参考的なチェックであり、Bajutsu の判定の外にあります。

**未確定のスコープ選択（確定ではなく、レビュー用に明示します）**。スコープ詰めの一巡で決めるべきだった問いです。括弧内に推奨を、そして現実的な代替案を併記します。

- 面の順序：まず CLI の `stats --html` を出し、serve タブは後続スライスとして加える（推奨：はい。CLI を中核に据えてから serve）か、どちらか一方だけを作るか。
- データソース：ローカルの `runs/` ディレクトリの manifest を主とし、serve の DB を、存在する場合の長期履歴のソースとする（推奨：両対応で、DB があれば DB、なければ `runs/`）か、単一ソースに絞るか。
- チャート描画：BE-0050 のカバレッジに倣って依存ライブラリなしで最小限に保つ（推奨。インライン SVG と CSS、JS チャートライブラリなし）か、より豊かな対話性のためにチャート依存を引き込むか。

## 検討した代替案

- **新しい面を設けず、run 単位の `report.html` を拡張する**。却下しました。run 単位レポートは意図的に *1 つの run* を対象としています（BE-0068 はこれを 1 つの run ディレクトリの純粋な再描画にしています）。run をまたぐ集計は、入力（run の *集合*）も粒度も異なるので、独立したコマンドやタブに属します。カバレッジ（BE-0050）や監査（BE-0049）がそれぞれ独立した面であるのと同じです。
- **`bajutsu audit` に取り込む**。住処としては却下しつつ、依存先としては残します。監査（BE-0049）はシナリオの *再現性を判定*します（repeat-and-diff と flaky/deterministic の分類）。本項目はスイート全体にわたる *運用上の推移を可視化*します（pass 率、所要時間、失敗ホットスポット、ボリューム）。両者は `scenarioHash` のグルーピングを共有し、このダッシュボードは監査の分類を *消費*しますが、成果物は別物です。両者を統合すると 1 つのコマンドに荷を負わせすぎます。
- **LLM に推移を要約させる**。却下しました。非決定的であり、不要でもあります。どの数値も manifest に対する厳密なカウントや集計であり、チームが CI を信じる前に一瞥しうるレポート経路に LLM を置くことは、prime directive が排除しているまさにその結合です。
- **生成済みのダッシュボードページをコミットする（ロードマップダッシュボード BE-0094 の方式）**。モデルとしては却下しました。あのページはリポジトリ内のメタデータから導出されますが、run 履歴はリポジトリ外に存在する実行時データです。したがってダッシュボードは、ドキュメントサイトに焼き込むのではなく、その都度 run 集合から（CLI や serve で）生成しなければなりません。
- **何もしない（現状維持）**。許容はできます。ただしデータがすでにディスク上にあるにもかかわらず、スイートの推移は見えないままです。「自分たちはどう推移しているか」に答えるには、チームが手で `manifest.json` をパースするスクリプトを書くことになります。アーティファクトがすでに存在することを思えば、この空白は安く埋まります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 1. run 集合に対する集計器（`aggregate_runs`）。BE-0049 の `scenarioHash` でグルーピングし、BE-0049 の flaky 分類器（`audit.longitudinal`）を再利用する。集計器が必要とするフィールドはわずかなので、`report.load` で `RunResult` を丸ごと復元せず、監査と同じように manifest のマッピングをそのまま読む。
- [x] 2. メトリクス集合。時系列の pass 率、所要時間と性能、失敗ホットスポット、flaky 度、ボリューム。
- [x] 3. CLI の面。`--runs` と `--html`（自己完結）とテキスト／JSON 出力を備えた `bajutsu stats`。
- [x] 4. 既存の serve の継ぎ目を通して集計器を再利用する serve の **Stats** タブ。run-id の一覧は、データベースを配線しているときは system of record から org スコープで、そうでなければ artifact store から取得し、各 run の full な `manifest.json` はどちらの場合も artifact store から読む（DB の `summary` は compact な履歴一覧の形しか持たないため）。
- [x] 5. スコープの番人。読み取り専用、判定を変えない、LLM なし、CI ゲートではない。

**ログ**

- [#652](https://github.com/bajutsu-e2e/bajutsu/pull/652) — CLI 先行のスライスを出荷しました。決定的な集計器 `bajutsu/stats.py`（`aggregate_runs` とテキスト／JSON／HTML のレンダラ。flaky 度は BE-0049 の longitudinal 監査から再利用）と、`bajutsu stats --runs/--json/--html` コマンドを追加し、Linux ゲートで動く `tests/test_stats.py` と `tests/test_cli_stats.py`、および `cli.md` の二言語ドキュメントを備えます。serve の Stats タブ（作業単位 4）は後続へ先送りしました。
- [#654](https://github.com/bajutsu-e2e/bajutsu/pull/654) — serve の **Stats** タブ（作業単位 4）を追加しました。新しい `stats_html` serve オペレーションが、既存の継ぎ目（id 一覧は DB-else-artifact、manifest は ArtifactStore）を通して org の run 履歴に集計器を再利用します。stdlib と FastAPI の両バックエンドで `GET /stats` として配信し、SPA の新しい Stats タブで表示します。`tests/serve/test_stats_tab.py` で検証します。

## 参考

[`docs/reporting.md`](../../docs/reporting.md)（`manifest.json`、`report.load`、`results_from_manifest`）、`bajutsu/report/`、`bajutsu/serve/helpers.py`（`list_runs`）、
[BE-0049 — 決定性／flaky 監査](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)（このダッシュボードが消費する `scenarioHash` の provenance と flaky 分類）、
[BE-0050 — E2E カバレッジマップ](../BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map-ja.md)（本項目が倣う、自己完結した HTML レポートの姉妹）、
[BE-0068 — 再生成可能なレポート](../BE-0068-regenerable-reports/BE-0068-regenerable-reports-ja.md)（「再実行せず記録済みの結果を再提示する」規律）、
[BE-0015 — Web UI の一般公開ホスティング](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)（7c-4。serve タブが読む DB 永続化の run レコードと、アーティファクトストアへのフォールバック）、
[DESIGN §2 / §10](../../DESIGN.md)

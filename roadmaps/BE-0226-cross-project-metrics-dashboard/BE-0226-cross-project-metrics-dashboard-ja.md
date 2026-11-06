[English](BE-0226-cross-project-metrics-dashboard.md) · **日本語**

# BE-0226 — プロジェクト横断のメトリクス比較ダッシュボード

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0226](BE-0226-cross-project-metrics-dashboard-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0226") |
| 実装 PR | [#936](https://github.com/bajutsu-e2e/bajutsu/pull/936), [#940](https://github.com/bajutsu-e2e/bajutsu/pull/940), [#942](https://github.com/bajutsu-e2e/bajutsu/pull/942) |
| トピック | オーサリング体験 |
| 関連 | [BE-0102](../BE-0102-run-stats-dashboard/BE-0102-run-stats-dashboard-ja.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md), [BE-0220](../BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix-ja.md) |
| 無効化 | [BE-0404](../BE-0404-collapse-project-layer/BE-0404-collapse-project-layer-ja.md) |
<!-- /BE-METADATA -->

## はじめに

実行統計ダッシュボード（[BE-0102](../BE-0102-run-stats-dashboard/BE-0102-run-stats-dashboard-ja.md)）は、
「*この* config の調子はどうか」に答えます。単一の active config の実行履歴を集計します。集計するのは、
pass 率の推移、実行時間、もっとも失敗するシナリオとステップ、flakiness の分類です。

`serve` が複数の config をプロジェクトとして登録するハブになると（姉妹提案「config project hub」）、
*比較の*問いが自然に立ちます。「プロジェクトどうしを比べるとどうか、どれに手を入れるべきか」です。

本提案は**プロジェクト横断の比較ビュー**を足します。登録済みのプロジェクトを並べて順位付けし、図示する
ダッシュボードを 1 つ用意します。プロジェクトごとの pass 率、flaky 率、実行時間を示します。複数の config を
管理するチームは、どのプロジェクトが劣化していて、どれが flaky で、どれが遅いかを、一目で把握できます。
各プロジェクトの単一 config ダッシュボードを順に開く必要はありません。

## 動機

BE-0102 は意図的に単一 config です。算出するどのメトリクスも、今バインドされている config の実行履歴に
スコープされており、2 つの config を並べて見せる画面はありません。1 つのアプリなら、これでちょうどよい
のです。

しかし、config project hub の要点は、チームが*複数の*プロジェクトを同時に抱えるところにあります。
プロジェクト単位のダッシュボードだけでは、1 つずつ順に読むしかありません。もっとも調子の悪いプロジェクトを
見つけるには、それぞれに切り替えて数値を読み、比較を頭の中で保持することになります。

比較ビューは、この負担を取り除きます。config project hub の
項目が BE-0015 の宙吊りの外部キーを配線するので、ハブは `project_id` 付きで実行を記録することになります。実行履歴は*プロジェクト単位に*分割できるように
なります。BE-0102 がすでに算出している集計を、プロジェクトごとに 1 度ずつ回して並べられます。

価値は、プロジェクトをまたいだ順位とトレンドの線にあります。たとえば「プロジェクト *checkout* は今週 pass
率が 98% から 71% に落ちた」「プロジェクト *search* がもっとも flaky だ」といった見え方です。単一 config の
ダッシュボードには出せません。

本項目は、[BE-0220](../BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix-ja.md)
の分析面での補完です。BE-0220 は、実行をまたいだ履歴を掘り、個々の flaky なシナリオを*順位付けして修正を
提案*します。1 つの実行履歴の中で flaky なシナリオを見つけるのが BE-0220 です。本項目は*プロジェクトを
またいで*、それらの総体的な健全性を比較します。

両者は下地となる実行ストアを共有し、重なるのではなく補い合います。BE-0220 はシナリオ単位で修正を提案し、本項目は
プロジェクト単位で比較します。

本項目は BE-0102 と同じく、プライムディレクティブの範囲に収まります。保存済みの実行データに対する
**読み取り専用の集計**です。図示する判定は、すでに `run` が決定的に下したもので、計算に LLM は入りません。
各プロジェクトの config はハブを通じて再利用するので、app-agnostic を保ちます。登録されているプロジェクトを
比較するだけで、特定のアプリに関することは何も持ちません。

## 詳細設計

作業は 3 つの単位に MECE に分かれます。集計、API、UI です。読み取り対象となるプロジェクト単位の実行履歴を、
config project hub が着地させていることが前提です。

### 1. プロジェクト横断の集計（BE-0102 の計算を再利用する）

BE-0102 の既存の config 単位の集計を、作り直さずに再利用します。BE-0102 は、1 つの config の実行履歴について
次の値を算出します。

- pass 率の推移
- 実行時間
- もっとも失敗するシナリオとステップ
- flakiness の分類

この計算を、実行の集合を入力に取る形に切り出します。そして、**登録済みプロジェクトごとに 1 度ずつ**、
そのプロジェクトの `project_id` スコープの実行に対して回します。プロジェクトごとの結果を、比較モデルに
組み立てます。モデルは、各プロジェクトについて次の 4 つを持ちます。

- 直近の pass 率
- flaky 率
- 実行時間の中央値と 95 パーセンタイル
- 短いトレンド系列

BE-0102 の flakiness の出力は、シナリオごとの*分類*です。各シナリオが期間内で flaky かどうかのラベルが
付きます。比較では、順位付けの基準になるスカラーがプロジェクトごとに 1 つ必要です。それを丸め上げたのが
**flaky 率**です。期間内に BE-0102 が flaky と分類したシナリオの割合を指します（flaky と分類された
シナリオ数 ÷ 総シナリオ数）。

flaky 率は、BE-0102 の既存のラベルを数えるだけです。新しい flakiness のヒューリスティックは足さないので、
「BE-0102 の計算を再利用する」が文字どおりに保たれます。順位付けの問い（「もっとも flaky な面を抱える
プロジェクトはどれか」）に、そのまま答えます。

集計は純粋で決定的です。Python コア（Simulator の経路の外）に置き、フィクスチャの実行データに対して
Linux のゲートでテストできます。

### 2. API

- `GET /api/metrics/projects`：比較モデル。登録済みプロジェクトごとに 1 行を返し、主要メトリクス（pass 率、
  flaky 率、実行時間のパーセンタイル）と、指定した期間のトレンド系列を持ちます。ハブのエンドポイントと
  同じく org スコープで、ローカルでは `default` に解決します。

このエンドポイントは、BE-0102 の単一 config 向けメトリクスエンドポイントを置き換えず、その隣に並びます。
1 つのプロジェクトの詳細がほしい呼び出し側は、今までどおり BE-0102 のビューを使います。

### 3. UI

serve に**比較ダッシュボード**ビューを設けます。構成は次の 2 つです。

- プロジェクトのソート可能な表。pass 率、flaky 率、実行時間でソートすると、もっとも問題のあるものが
  浮かび上がります。
- スモールマルチプル。期間内の pass 率について、プロジェクトごとに 1 つのトレンドスパークラインを並べます。
  劣化や flakiness が、集合全体にわたって一目で際立ちます。

プロジェクトの行を選ぶと、そのプロジェクトの既存の BE-0102 単一 config ダッシュボードへディープリンクします。
ハブのプロジェクトスイッチャーで切り替わります。比較ビューが入口で、BE-0102 がドリルダウンの先です。

## 検討した代替案

- **BE-0102 をその場で拡張してプロジェクトフィルタを取らせる。** 主たる位置づけとしては却下しました。
  BE-0102 は*単一 config*の詳細ビューで、そのままで有用です。ここではドリルダウンの先にもなります。
  比較は別の画面で、プロジェクトをまたいだ順位とスモールマルチプルのレイアウトです。1 つの config の
  ダッシュボードにフィルタをかけた変種ではありません。したがって、BE-0102 の中で育てるのではなく、その
  計算を*再利用する*独立した項目にします。
- **プロジェクト横断の比較を config project hub の項目に取り込む。** 各項目を単独で着地できるように保つため、
  却下しました。ハブは、レジストリとプロジェクト単位の実行まわりの配線です。比較ビューがなくても価値が
  あります。比較ダッシュボードは、その上に載る分析の層です。分けておけば、ハブが先に着地できます。
  本項目は、ハブが生む `project_id` スコープの履歴の上に立てます。
- **[BE-0220](../BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix-ja.md)
  と統合する。** 却下しました。BE-0220 はシナリオ単位で動作し（flaky なシナリオを順位付けし、修正を提案する）、
  その修正提案の経路は AI 支援です。本項目はプロジェクト単位で比較を行い、AI を含まない純粋な読み取り
  専用の集計です。両者は実行ストアを共有し、`関連` で結びます。ただし、出力も想定利用者も異なります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 1. プロジェクト横断の集計：BE-0102 の config 単位の計算を実行集合を取る形に切り出し、プロジェクトごとに回し、シナリオ単位の flakiness 分類をプロジェクト単位の flaky 率スカラーに丸め上げ、比較モデルを組み立てる。
- [x] 2. API：org スコープの `GET /api/metrics/projects`。BE-0102 の単一 config エンドポイントの隣に並べる。
- [x] 3. UI：ソート可能な比較表とプロジェクトごとのトレンドスパークライン。BE-0102 の単一 config ダッシュボードへディープリンクする。

**ログ**

- [#936](https://github.com/bajutsu-e2e/bajutsu/pull/936) — Unit 1。`stats.project_metrics` が
  `aggregate_runs` の結果をプロジェクト単位の見出し（pass 率、flaky と分類されたシナリオの割合としての
  flaky 率、実行単位の中央値／p95 実行時間、日次 pass 率のトレンド）へ丸め上げます。
  `serve.operations.project_comparison.compare_projects` が `ProjectRegistry` を走査し、
  `run_set_manifests` の継ぎ目で各プロジェクトの実行を読み取ります。純粋・読み取り専用で、
  run／CI の判定経路には乗りません。
- [#940](https://github.com/bajutsu-e2e/bajutsu/pull/940) — Unit 2。`project_metrics_view` が
  `compare_projects` を `GET /api/metrics/projects` の JSON として両トランスポート（stdlib ハンドラと
  FastAPI）で公開します。org スコープで、ハブが未配線のときは空リストを返します。読み取りの `GET`
  なので RBAC のゲートはありません。窓は BE-0102 と同じ固定の `_STATS_RUN_LIMIT` にそろえています。
  単一 config の `/stats` を置き換えず、その隣に並びます。
- [#942](https://github.com/bajutsu-e2e/bajutsu/pull/942) — Unit 3。serve の **Metrics** タブが比較モデルをクライアント側で描画します。ソート可能な
  表（pass 率、flaky 率、p50／p95 実行時間）にプロジェクトごとの pass 率スパークラインを添え、`/stats` の
  SVG polyline トレンドと同じ形を再利用します。行をクリックすると、ハブのスイッチャー経由でそのプロジェクトへ
  貼り替え（`switchProject(..., {goStats:true})`）、BE-0102 の単一 config ダッシュボードを開きます。比較が
  入り口、プロジェクトごとの表示がドリルダウンです。タブはスイッチャーと同じく登録が 2 件を超えるときだけ
  現れるので、単一 config の serve には出ません。これで項目が実装済みになります。

## 参考

`bajutsu/serve/`、[reporting](../../docs/ja/reporting.md)、[architecture](../../docs/ja/architecture.md)。
[BE-0102](../BE-0102-run-stats-dashboard/BE-0102-run-stats-dashboard-ja.md)（再利用し、ドリルダウンの
先にする単一 config の集計）、
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)（プロジェクト単位の
分割が依拠する `projects` / `runs.project_id` スキーマ）、
[BE-0220](../BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix-ja.md)
（シナリオ単位の flaky 掘り起こしという補完）、そして本ダッシュボードが集計する `project_id` スコープの
実行履歴を記録する姉妹提案「config project hub」。

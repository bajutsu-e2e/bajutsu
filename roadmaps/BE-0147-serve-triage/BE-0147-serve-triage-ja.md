[English](BE-0147-serve-triage.md) · **日本語**

# BE-0147 — serve Web UI で失敗 run をトリアージする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0147](BE-0147-serve-triage-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0147") |
| 実装 PR | _pending_ |
| トピック | serve Web UI への CLI 機能の取り込み |
<!-- /BE-METADATA -->

## はじめに

`triage`（失敗調査）を `serve` Web UI に持ち込みます。run が Replay／履歴ビューで失敗したとき、端末に降りずに
ブラウザのまま原因を診断し、提案された修正を差分で確認できるようにします。ヒューリスティックのトリアージが
既定で、完全に決定的です。Claude（`--ai`）の経路はオプトインで、しかも調査役に徹します。修正の適用は、差分を
確認したうえでの明示的な人手の操作です。ゲートに LLM は入らず、run の合否を計算し直すこともありません。

## 動機

失敗が最初に目に入るのはブラウザです。Replay ビューは run をストリーミングし、赤くなったレポートを埋め込み
ます。ところが UI はちょうどそこで止まります。シナリオが失敗したことは示すのに、なぜ失敗したのかを理解する
ための次の一手を出しません。その能力は CLI にはすでにあります。`bajutsu triage` は失敗した run の文脈（落ちた
ステップ、そのアサーション、捕捉した要素ツリー／スクリーンショット／ネットワーク）を組み立て、
`TriageAgent` に渡します。既定はルールベースで決定的な `HeuristicTriageAgent`（`bajutsu/triage.py`）で、
`--ai` を付ければ失敗時のスクリーンショットも読む Claude 版（`bajutsu/claude_triage.py`）になります。
エージェントは*構造化された*修正（`renameId` / `addIndex` / `raiseTimeout`）を提案でき、`--apply` /
`--write` がシナリオ原本にパッチを当て（差分プレビュー、オプトイン）、`--rerun` で確認します。いまはそのすべ
てが端末専用なので、ブラウザで赤いレポートを見ている人は、run id をコピーしてシェルに切り替え、調査を手で
実行し直すしかありません。失敗がすでに画面に出ているその場にトリアージを出せば、このループが閉じます。赤い
run のあとに利用者が最も多く望む操作です。

## 詳細設計

Tier 1 の助言であり、UI は既存のコマンドを起動するだけです。

- **入口。** 失敗した run に対する「Triage」操作を、Replay のレポートと履歴一覧に置き、新しい
  `POST /api/triage`（`{runId, scenario, ai?}`）を叩きます。トリアージを serve の*ジョブ*として実行し、
  run／record／crawl がすでに使っているジョブ＋SSE ログストリーム＋キャンセルの仕組み
  （`bajutsu/serve/jobs.py`、`/api/jobs/{id}/events` ストリーム）をそのまま再利用します。
- **結果。** 診断（原因の要約と該当するステップ／アサーション）をパネルに表示し、エージェントが修正を
  提案したときはそれをシナリオ原本に対する**差分プレビュー**として見せます。CLI の `--apply` が出すのと
  同じ差分です。適用は別の明示的なクリックで、既存のシナリオ保存経路（`POST /api/scenario`、
  `load_scenario_file` で検証済み）を通して書き込みます。「適用して再実行」を選べば既存の run ジョブに
  連結します。
- **AI はオプトインで、調査役に徹する。** ヒューリスティックが既定で決定的です。`--ai` 経路は `record` /
  `crawl` / アラートガードと同じく設定済みの AI プロバイダを使います（プロバイダ／キーの選択は Settings
  モーダルにすでにあります）。プロバイダ未設定のときは AI トグルを無効にしてヒントを出し、ヒューリスティック
  のトリアージは引き続き動きます。
- **アプリ非依存。** トリアージは run ディレクトリに保存されたアーティファクトと、config（`targets.<name>`）
  から解決したシナリオ原本の上で動きます。ツール側にアプリ固有の分岐はありません。

これはプライムディレクティブを構造上守ります。合否は決定的な run が出したものを読み戻すだけで計算し直しま
せん。AI は（使うときも）調査するだけです。そして修正は常に人が受け入れる提案であり、自動の編集ではありませ
ん。

## 検討した代替案

* **トリアージを CLI 専用のままにする（現状）。** 失敗はブラウザに出るのに、調査は端末へ移らねばなりません。
  不採用です。「なぜ落ちたのか」を問う最も摩擦の少ない場所はレポートビューであり、それを載せるジョブ／
  ストリーム／保存の土台はすでにあります。
* **提案された修正を自動適用する。** 不採用です。自己修復のガード規則（AI は提案し、人がオプトインで適用す
  る。[BE-0039](../BE-0039-self-healing-propose-optin/BE-0039-self-healing-propose-optin-ja.md)）
  と衝突し、テストを黙って緩めてしまう危険があります
  （[BE-0023](../BE-0023-self-healing-guards/BE-0023-self-healing-guards-ja.md)）。修正は
  常に利用者が受け入れる差分です。
* **トリアージをジョブにせずリクエスト内でその場実行する。** 不採用です。AI のトリアージは数秒以上かかること
  があり、他の長い操作と同じくキャンセル可能でストリーム可能であるべきです。ジョブモデルを再利用すれば UX が
  一貫し、サーバも応答し続けます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 既存のジョブ／SSE／キャンセルの仕組みを再利用してトリアージを serve のジョブとして実行する
      `POST /api/triage`（`{runId, scenario, ai?}`）エンドポイントを追加する
- [x] 失敗した run の Replay レポートと履歴一覧に「Triage」操作を追加し、提案された修正には差分プレビューを
      添える
- [x] AI（`--ai`）経路をオプトインで組み込み、既定は決定的なヒューリスティックエージェントのままにする

- _pending_ — BE-0147 を出荷しました。`triage --json` が機械可読の結果を run ディレクトリへ書き出し、
  新しい `POST /api/triage` がそれをジョブ（ジョブ／SSE／キャンセルの仕組みを再利用）として実行します。
  Replay レポートと履歴一覧に「Triage」操作が加わり、差分プレビューを表示します。Apply は検証済みの
  `POST /api/scenario` 保存経路を通して修正を書き戻します。AI はオプトインで調査のみを行い、既定は
  ヒューリスティックエージェント、合否には LLM を一切関与させません。

## 参考

* `bajutsu/triage.py`、`bajutsu/claude_triage.py`、`bajutsu/cli/commands/triage.py`（ここで露出する
  調査機能）。
* `bajutsu/serve/`（`jobs.py`、`operations.py`、`handler.py`。再利用するジョブ／ストリーム／保存の土台）。
* [BE-0021 — AI トリアージ](../BE-0021-ai-triage/BE-0021-ai-triage-ja.md)、
  [BE-0022 — 構造化された修正](../BE-0022-update-structured-fixes/BE-0022-update-structured-fixes-ja.md)、
  [BE-0039 — 提案＋オプトイン適用](../BE-0039-self-healing-propose-optin/BE-0039-self-healing-propose-optin-ja.md)、
  [BE-0023 — テストを緩めないためのガード](../BE-0023-self-healing-guards/BE-0023-self-healing-guards-ja.md)
  （この UI 面が再利用する、トリアージ機能と自己修復のガード）。
* [BE-0011 — ローカル Web UI（`bajutsu serve`）](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)
  （拡張する UI）、
  [BE-0072 — serve Web UI のレスポンシブ対応](../BE-0072-responsive-web-ui/BE-0072-responsive-web-ui-ja.md)
  （パネルが引き継ぐ小さい画面向けレイアウト）。
* [CLAUDE.md](../../../CLAUDE.md)、[DESIGN §2](../../../DESIGN.md)（AI は判定しない、決定性ファースト。
  トリアージは助言にとどまり、合否は読み戻すだけで計算し直しません）。

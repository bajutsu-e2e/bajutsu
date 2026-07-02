[English](BE-0099-webhook-run-notifications.md) · **日本語**

# BE-0099 — 実行結果の Webhook 通知

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0099](BE-0099-webhook-run-notifications-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| 実装 PR | [#414](https://github.com/bajutsu-e2e/bajutsu/pull/414) |
| トピック | 外部サービスとの連携 |
| 由来 | 外部サービスとの連携 |
<!-- /BE-METADATA -->

## はじめに

`run` が終わって判定が確定したあと、その実行の要約を、ターゲット設定に書いた Webhook へ送ります。
まずは Slack を対象にし、実行が終わった瞬間にチームのチャンネルへ「✅ 12 件成功」や「❌ login 失敗」
といったメッセージが届くようにします。Webhook が発火するのは決定的な判定の**あと**であり、すでに確定した
結果を運ぶだけです。判定には一切関与しません。LLM を呼ばず、終了コードを動かさず、配信に失敗しても記録は
残しますが実行を失敗させることはありません。メッセージは `manifest.json`
（[BE-0068](../../implemented/BE-0068-regenerable-reports/BE-0068-regenerable-reports-ja.md)）から
作る**書式非依存の要約モデル**を素にして組み立てます。Slack はその最初のレンダラであり、署名付きの汎用
JSON POST や Teams、Discord は、同じモデルに対する後続のレンダラとして残しておきます。各送信先は、必要な
イベントだけを購読します。購読できるのは完了（毎回／失敗時のみ／判定の反転）と、任意で実行の開始です。
メッセージには、その実行が `serve` のベース URL を知っているとき、ホスト型レポートへのリンクを添えます。

## 動機

ロードマップは現在、この領域全体を **Not adopting**（採用しない）に置いています。「スケジューリング／Slack
／TestRail 連携は CI・通知レイヤーの領分である」という判断です。この判断は、CI パイプライン内で走る実行に
ついては正しいものです。GitHub Actions はすでに、終了コードと `junit.xml`、チェックの注釈
（[BE-0003](../../implemented/BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci-ja.md)）を、
チームが組んだ任意の Slack メッセージへと変換します。通知は CI レイヤーが持つものであり、Bajutsu がそれを
作り直すべきではありません。

この判断が見落としていたのは、CI パイプラインの**外**で起こる実行です。そこには終了コードを解釈する
ラッパーがなく、通知レイヤーそのものがありません。

1. **ホスト型 `serve` から起動した実行。** `serve` を共有ホスト上で動かす場合
   （[BE-0015](../../in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)、
   [BE-0016](../../in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)）、
   Web UI のボタンから始めた実行は誰の CI にも乗っていません。結果はサーバー上で完成していますが、起動した
   本人はすでにタブを閉じています。終わったこと、あるいは壊れたことを伝える経路がありません。ここがもっとも
   明確に「CI レイヤーの仕事」ではない穴です。CI レイヤーが存在しないからです。
2. **スケジュール実行とその場の実行。** Mac mini 上の夜間実行や、リリース前の手動の `bajutsu run` は、
   どちらも通知を送るプラットフォームを備えたパイプラインの上にはありません。
3. **長時間の自律クロール。** クロール
   （[BE-0038](../../in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)）は
   何分も走ることがあります。「開始」と「完了」の通知があるかどうかで、張り付いて見ているか、離れていられるかが
   変わります。

もとの原則を保ったままの、範囲を限った捉え直しはこうです。Bajutsu は CI オーケストレータにはなりません。
すでに算出している実行の要約を受け取り、それを URL へ POST するだけの、薄くて決定的な**送信路**を持ちます。
チャンネルの振り分け、警告のエスカレーション、ダッシュボードといった「通知の業務ロジック」は、引き続き受け手の
レイヤーが持ちます。Bajutsu はイベントを届けるだけです。これを安く安全に作れる理由が二つあります。実行の正準
モデルはすでに `manifest.json`（BE-0068）として存在するので、配信内容は既存データの射影であって新たな記録ではない
こと。そして配信は判定後の純粋な副作用なので、決定性优先の契約の外に構造上完全に収まることです。

## 詳細設計

### 設定

設定に新しいトップレベルの `notify:` リストを置きます（送信先ごとに一項目とし、一回の実行で複数チャンネルへ
扇状に送れるようにします）。`targets.<name>.notify` での、ターゲット単位の上書きも任意で添えます。App-agnostic
の原則どおり、ターゲットがどの Webhook を使うかはコードではなく設定で決めます。

```yaml
notify:
  - format: slack                              # 最初の（本項目では唯一の）レンダラ
    url: ${SLACK_WEBHOOK_URL}                   # secret 経由で解決し、直書きしない
    on: [failure, change]                       # この送信先が欲しいイベント
    targets: [checkout, login]                  # 任意: このシナリオ/タグだけに絞る（BE-0034 を再利用）
```

`url` は既存の secret の仕組み
（[BE-0032](../../implemented/BE-0032-secret-variables/BE-0032-secret-variables-ja.md)）を通して解決します。
Webhook の URL 自体が Incoming Webhook の資格情報なので、設定ファイルにも生成物にも残らないようにするためです。

### いつ発火するか（イベントモデル）

各送信先は、欲しいイベントを `on:` で宣言します。四つすべてが一級です。`on:` を省いたときの既定は
`failure` です。「何かが壊れたときだけ呼んでほしい」が支配的な用途だからです。

| イベント | 発火条件 | 備考 |
|---|---|---|
| `failure` | 実行の判定が `ok == false` | 既定。もっとも静か |
| `change` / `recovery` | 同じソースの前回実行と判定が異なる | 赤から緑、緑から赤。前回実行の参照が要る |
| `always` | 成否によらず毎回 | ダッシュボードや監査用チャンネル |
| `start` | 実行の開始時 | 任意。長いクロール向けで、後続の完了イベントと対にする |

`change` / `recovery` は、同じシナリオソースの前回実行の判定を実行履歴（ローカルでは `runs/`、ホスト型では
実行ストア）から読みます。前回実行が存在しないとき、最初の実行は変化として扱い、基準値を黙って飲み込むのでは
なく通知します。

### 何を送るか（要約モデル）

`manifest.json` から射影した、書式非依存の `RunNotification` 要約です。200 シナリオの実行が 200 行の
メッセージにならないよう、意図して**上限を設け**ます。

- **実行の同定**: `runId`、ツールのバージョン、`sourceName`、`backend` / `engine`、トリガ源
  （`cli` / `serve` / `crawl`）、そして設定が git ソース由来のときの git provenance（ブランチ、コミット。
  [BE-0044](../../implemented/BE-0044-scenario-provenance/BE-0044-scenario-provenance-ja.md)、
  [BE-0063](../../implemented/BE-0063-git-config-source/BE-0063-git-config-source-ja.md)）。
- **判定**: 全体の `ok`、件数（合計／成功／失敗／スキップ）、総所要時間。
- **シナリオの要約**: 失敗は列挙し（名前、所要時間、失敗理由の先頭行、落ちた step）、成功は件数に畳みます。
  失敗の列挙には上限を設け、「ほか N 件」の末尾を付けて、メッセージがチャットカードに収まるようにします。
- **レポートリンク**: ホスト型レポートへの `reportUrl` は、その実行が `serve` のベース URL を知っているとき
  （ホスト型・サーブされた実行）**だけ**載せます。純粋にローカルな `bajutsu run` は、死んだ `file://` リンクを
  出すのではなく省きます。レポートは既存の `--zip` エクスポート
  （[BE-0060](../../implemented/BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md)）で
  これまでどおり持ち運べます。
- **失敗の証跡ポインタ**: 列挙した各失敗について、その失敗スクリーンショットへのリンク。これもベース URL に
  よってリンクが解決できるときだけ載せます。

要約は実行の redaction
（[BE-0047](../../implemented/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md)）を継承します。
すでにマスク済みの manifest から作るので、生の secret が Webhook に届くことはありません。Slack のレンダラは
このモデルを Block Kit メッセージに変えます。汎用 JSON や Teams、Discord を後から足すのは、同じモデルに対する
新しいレンダラであって、実行ロジックには触れません。

### 決定性と prime directive

この機能はまるごと判定のあとに置かれます。それが契約の内側に留まる理由です。

- **LLM は決して使わない。** 要約は `manifest.json` の機械的な射影であり、AI が書いたメッセージはありません。
  ここにある何も Tier-2 のゲートに触れません。
- **配信が判定や終了コードを変えることはない。** POST は `run` が成否を決めて manifest を書いたあとに起こります。
  2xx 以外の応答、タイムアウト、到達不能なホストは、運用ログ
  （[BE-0055](../../implemented/BE-0055-operational-logging/BE-0055-operational-logging-ja.md)）を通して記録し、
  警告として見せますが、実行の結果と終了コードはすでに確定していて動きません。
- **有界で、ブロックしない配信。** 短いタイムアウトと、バックオフを伴う小さく有界なリトライを用い、遅い
  Webhook が実行を止めたり吊らせたりしないようにします。（実行経路に固定 `sleep` は入れません。リトライの
  バックオフは決定的な実行の外側にある配信側の都合であって、実行内の待機条件ではありません。）
- **App-agnostic。** 送信先、書式、イベントのフィルタは設定です。runner、driver、レポートのコードは
  変わりません。

### どこに接続するか

CLI の実行経路で manifest を書いたあとの一点（`bajutsu/cli/commands/run.py` の既存の `github.emit(...)`
呼び出しの隣）から送信し、serve から起動した実行も同じ仕組みを再利用して、ホスト型の実行も同じように通知します。
`start` は、実行の入口で最初のシナリオの前に発火します。

## 検討した代替案

- **すべてを CI レイヤーに任せる（現状維持の「採用しない」立場）。** CI パイプラインの実行については正しい
  ものですが、CI のないあらゆる面（ホスト型 `serve`、スケジュール、クロール）を、通知経路のないまま取り残します。
  本項目は、有界な汎用送信路の部分だけを採り、振り分けやエスカレーションは受け手に残すので、もとの原則は
  生き残ります。
- **まず汎用の署名付き JSON POST を据え、Slack を一つの adapter にする。** 長期的にはこちらが素直な形であり、
  要約モデルを書式非依存に切ってあるのは、まさにこの道を開けておくためです。Slack から始めるのは、それが
  具体的で即座に役立つ対象であり、イベント、上限付き要約、レポートリンクという経路全体を端から端まで通すからです。
  汎用、Teams、Discord は、その実証済みモデルに対するレンダラであって、作り直しではありません。
- **失敗を LLM が書いた「賢い」要約にする。** きっぱり退けます。実行完了の経路に LLM を載せることになり、
  判定のように読まれる危険があります。メッセージは manifest の決定的な射影です。（失敗の AI による*調査*は
  `triage` の経路に留まり、通知には入りません。）
- **シナリオ単位のストリーミング通知（各シナリオが終わるたびの ping）。** チャットチャンネルには騒がしすぎる
  ため見送ります。チームが実際に欲しい単位は完了の要約です。ダッシュボード向けの送信先用に、任意機能として後で
  戻ってくる余地はあります。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

- [BE-0068 — 再生成可能なレポート](../../implemented/BE-0068-regenerable-reports/BE-0068-regenerable-reports-ja.md): 要約が射影する正準な実行モデルとしての `manifest.json`。
- [BE-0060 — 実行レポートを zip でダウンロード／エクスポート](../../implemented/BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md): `reportUrl` が無いとき、ローカルの実行でも完全なレポートを持ち運ぶ手段。
- [BE-0055 — ホスト型 serve の運用ログ](../../implemented/BE-0055-operational-logging/BE-0055-operational-logging-ja.md): 配信失敗を記録する場所。
- [BE-0015](../../in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) / [BE-0016](../../in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md): ホスト型 `serve`。実行に `reportUrl` を与え、もっとも帯域外通知を必要とする面。
- [BE-0044 — シナリオの来歴](../../implemented/BE-0044-scenario-provenance/BE-0044-scenario-provenance-ja.md) / [BE-0063 — git 設定ソース](../../implemented/BE-0063-git-config-source/BE-0063-git-config-source-ja.md): 配信内容が運ぶブランチ／コミットの同定。
- [BE-0032 — secret 変数](../../implemented/BE-0032-secret-variables/BE-0032-secret-variables-ja.md): Webhook URL を secret として解決する。
- [BE-0034 — タグ／選択実行](../../implemented/BE-0034-tags-selective-runs/BE-0034-tags-selective-runs-ja.md): 送信先を特定のシナリオに絞るために再利用するセレクタ。
- `roadmaps/README.md` の「Not adopting」→ *Scheduling / Slack / TestRail integration*: 本項目が範囲を限って一部覆す、これまでの判断。

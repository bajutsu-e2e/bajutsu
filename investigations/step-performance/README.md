# ステップ実行の高速化：調査報告と Mac 側への引き継ぎ

> 対象は iOS（XCUITest）と Android（adb と常駐 UIAutomator サーバー）です。Web は対象外です。
> 目標は、証拠の取得まで含めた end-to-end で 1 ステップ 250〜500 ms です。
> 調査は Linux 上の Claude Code セッションで行いました。Simulator と emulator がないため、
> ドライバ側の数値はコード読解と、ロードマップ項目に記録済みの実測値から積み上げた推定です。
> 端末での実測は、この資料の[第 7 節](#7-mac-側で続けるための手順)の手順で Mac 上で続けてください。

## 結論の要約

現状の 1 ステップは、推定で iOS が 0.7〜1.7 秒、Android の常駐サーバー経路が 2〜3 秒です。
Android の `uiautomator dump` 経路は 8〜12 秒です。
目標の 250〜500 ms に対して、iOS は 2〜4 倍、Android は 5〜10 倍の短縮が必要です。

ステップの時間は、ほとんどが「ホストと端末の往復」と「証拠の取得」で消えています。
orchestrator 自身の計算は 1 ステップあたり 1 ms 未満で、ボトルネックではありません。
プレーンな `tap` 1 ステップで、ホストは端末に 5〜7 回の往復を発行します。
その内訳は、スクリーンショット 2 枚、木の読み取り 2 回、タップ本体、割り込みの回収です。
タップの意味的な仕事はそのうち 1 回だけです。

短縮の道筋は 3 段階に分かれます。

| 段階 | 内容 | 期待できる 1 ステップの時間 | 設計の変更 |
|---|---|---|---|
| A | 証拠と読み取りの重複を消す（`before.png`、`elements.json` の二重書き、ステップ後の再読み取り） | iOS 0.4〜0.9 秒、Android 1.2〜2 秒 | なし（orchestrator 内） |
| B | 通信と端末側の無駄を消す（keep-alive、属性読みの一括化、dump の二重実行の解消） | iOS 0.3〜0.6 秒、Android 0.6〜1.2 秒 | ドライバとランナーの内部 |
| C | ステップのループを端末側の実行機に移す（waits と assert を端末側で評価する） | iOS 0.2〜0.35 秒、Android 0.15〜0.3 秒 | プロトコルの追加 |

段階 A と B だけでは目標に届きません。
iOS では、XCUITest の固定費（snapshot 約 35 ms とタップ合成 100〜300 ms）を除いても足りません。
ポーリングのたびにホストの往復を払う構造が残るからです。
目標に届くのは段階 C です。
「Swift でシナリオをそのまま実行する」案は、この段階 C にあたります。
ただし実行機を置く場所は、アプリ内の SDK（ソフトウェア開発キット、BajutsuKit）ではなく XCTest ランナー内を勧めます。
理由は[第 5 節](#5-目標に届く設計案フェーズ-c)に述べます。

## 1. 調査の方法と、この環境でわかること

この環境で実測できたのは次の 2 つです。

- orchestrator 自身のオーバーヘッドと、ステップ種別ごとのドライバ呼び出し回数です。
  `bench_orchestrator.py` が fake driver 上で計測します。
- 実行時トレーサー `trace_run.py` の動作確認です。fake backend の run で、ステップごとの内訳を出力できました。

ドライバ側は 3 つの読解報告に基づきます。報告は英語で、`reports/` に置きました。

- [`reports/orchestrator.md`](reports/orchestrator.md)：ステップ 1 つの共通の骨格と、ステップ種別ごとの呼び出し列です。
- [`reports/ios.md`](reports/ios.md)：Python ドライバ、Swift の HTTP サーバー、XCUITest の要素プロバイダ、BajutsuKit の現状です。
- [`reports/android.md`](reports/android.md)：adb ドライバ、常駐サーバーの Kotlin 側、settle と catch-up の仕組みです。

推定値には「est」と書き添えてあります。
ロードマップ項目に記録済みの実測値は、出典の BE 番号を添えてあります。

## 2. この環境での実測結果

### 2.1 orchestrator のオーバーヘッドと往復回数

`bench_orchestrator.py` は、300 要素の画面を持つ fake driver に対して各ステップ種別を 20 回実行します。
ドライバの遅延を 0 にした計測（`--model zero`）の結果を示します。

| ステップ種別 | sink | 1 ステップの壁時計 | 1 ステップの driver 呼び出し |
|---|---|---|---|
| `tap` | NullSink | 0.4 ms | tap 1、drain_actuations 1、drain_interruptions 1 |
| `tap` | FileSink | 11.7 ms | 上に加えて screenshot 2、query 1.05 |
| `tap` と `wait for` の対 | FileSink | 12.1 ms | screenshot 2、query 1.02、tap 0.5 |
| `wait until: settled` | NullSink | 100.6 ms | query 3 |
| `wait until: settled` | FileSink（guard あり） | 113.5 ms | query 3.05、screenshot 2、system_alert_labels 1 |
| `assert` | FileSink | 10.5 ms | query 1.05、screenshot 2 |
| `type`（`into` あり） | FileSink | 11.3 ms | tap 1、type_text 1、screenshot 2、query 1.05 |

この表から 4 つのことが読めます。

- orchestrator の計算コストは 1 ms 未満です。FileSink の 11 ms は、300 要素の `elements.json` を 2 回書く CPU と I/O です。
- 通常の run（FileSink）では、どのステップも**スクリーンショットを 2 枚**撮ります。
  `before.png` は BE-0341 の事前証拠で、`after.png` はステップの必須証拠です。
- `wait until: settled` の木差分経路は、条件が即座に成立しても最低 100 ms かかります。
  `_SETTLE_POLLS = 2` と `_POLL = 0.05` の積です（`bajutsu/common/orchestrator/waits.py:31-32`）。
  読み取りも最低 3 回です。
- `drain_interruptions` は毎ステップ呼ばれます。iOS ではこれが HTTP 往復です（`bajutsu/common/drivers/xcuitest.py:1170-1176`）。

同じシナリオを iOS の仮の遅延モデルで投影しました（query 250 ms、tap 150 ms、screenshot 120 ms）。
`tap` 1 ステップは FileSink で約 700 ms、`wait until: settled` は約 1.2 秒になります。
このモデルの数値は仮定です。Mac 側の実測で差し替えてください（[第 7 節](#7-mac-側で続けるための手順)）。

### 2.2 トレーサーの動作確認

`trace_run.py` は製品コードを変更せずに、driver 呼び出し、HTTP 往復、subprocess、証拠取得の所要時間を
ステップに紐づけて記録します。fake backend での出力例を示します。

```text
== per step (seconds) ==
step                      wall  driver   evid.  subproc  driver-call counts
0:wait                   1.006   0.000   0.002    0.000  query=22, screenshot=2, system_alert_labels=1, drain_interruptions=1
1:assert                 0.002   0.000   0.002    0.000  screenshot=2, query=1, drain_interruptions=1
```

空の画面に対する `wait until: settled` が 1 秒のタイムアウトまでに 22 回 query した様子が見えます。
iOS ではこの 1 回ごとに snapshot と HTTP 往復が乗ります。

## 3. 現状の 1 ステップの内訳

### 3.1 両バックエンドに共通の骨格（orchestrator）

`_handle_action`（`bajutsu/common/orchestrator/loop.py:1249-1730`）が、どのステップにも次の順で処理を行います。

| 順 | 処理 | 端末への往復 | 場所 |
|---|---|---|---|
| 1 | 事前証拠 `before.png` と `elements.before` | screenshot 1 回。初回ステップだけ、sink 内部で隠れた query が 1 回 | `loop.py:1274-1329`、`evidence/core.py:170` |
| 2 | `screenChanged` ポリシー用の事前 query | ポリシーがある場合のみ 1 回 | `loop.py:1366-1376` |
| 3 | interrupt guard の事前 query | `interrupts` を宣言した場合のみ 1 回 | `loop.py:1404-1412` |
| 4 | 本体（tap、wait、assert） | 種別による（下の表） | `loop.py:368-473` |
| 5 | `drain_actuations` と `drain_interruptions` | iOS は HTTP 1 回。Android はメモリ内 | `loop.py:1576-1581` |
| 6 | 必須の `after.png` | screenshot 1 回 | `loop.py:1601` |
| 7 | ステップ後の木の読み取り（`elements.json` 用） | 変化を起こすステップは query 1 回。assert と wait は自分の読み取りを流用（BE-0259） | `loop.py:1640-1727` |

本体の往復は種別で異なります。

| 種別 | 最低の往復 | 備考 |
|---|---|---|
| `tap` | 1（ドライバ内部で iOS は 2 HTTP、Android は 3 HTTP と 4 dump） | 不可視なら scroll 復旧で query と is_tappable と scroll を反復 |
| `type`（`into`） | tap 1 と type_text 1 | |
| `wait for` | 条件が成立するまで 50 ms ごとに query | guard があれば 1 秒ごとに `system_alert_labels` を追加 |
| `wait until: settled`（木差分） | query 3 | 100 ms の下限 |
| `wait until: settled`（BE-0310 遷移シグナル） | 遷移後 0.3 秒の静止を待つ間、50 ms ごとに query | 読み取りは guard のためだけに使われる（`waits.py:1042-1061`） |
| `assert` | query 1 | |

証拠の書き込みは同期です。`elements.json` は事前と事後で同じファイルに 2 回書かれ、
毎回、秘匿処理、JSON 整形、正規表現の走査を通ります（`evidence/sink.py:161-210`）。

### 3.2 iOS

タップ 1 ステップの推定内訳です。出典は [`reports/ios.md`](reports/ios.md) です。

| 局面 | 推定 | 原因 | 場所 |
|---|---|---|---|
| `before.png` | 150〜400 ms | `app.screenshot().pngRepresentation` をフル解像度 PNG で送る。runner の直列キューを占有する | `XcuitestElementProvider.swift:330`、`APIHandler.swift:27-51` |
| `/elements` | 45〜120 ms | `app.snapshot()` 約 34 ms（BE-0105 実測）に加え、毎回 `safariViewService.state` の XPC（プロセス間通信）と、BajutsuKit 連携アプリでは `/zorder` の第 2 往復 | `XcuitestElementProvider.swift:49-50`、`drivers/zorder.py:36-106` |
| タップ本体 | 300〜650 ms | 位置パスから生きた要素を復元した後、属性を 1 つずつ読む。`exists`、identifier、label、type、enabled、selected、frame ×2、isHittable で約 11 回の XCUI 解決。その後にタップ合成 | `XcuitestElementProvider.swift:127-151、410-430` |
| `drain_interruptions` | 2〜5 ms | 毎回新規 TCP 接続 | `xcuitest.py:591、1170` |
| `after.png` | 150〜400 ms | 上と同じ | |
| 事後の query | 45〜120 ms | `elements.json` のため | |
| 合計 | 0.7〜1.7 秒 | | |

加えて、次の固定費があります。

- stale 再試行の固定 sleep です。初回 0.5 秒、2 回目 1.0 秒です（`xcuitest.py:141-149`）。
  runner はアクチュエーション中のあらゆる例外を `stale` に写像するため、アニメーション中の一時的な失敗でも 0.5 秒を払います。
- 文字入力は `app.typeText` で 1 文字あたり約 50 ms（est）です。
- `system_alert_labels` は SpringBoard の snapshot で 100〜500 ms（est）です。guard 付きの wait 中は 1 秒ごとに走ります。
- 4 シナリオごとに runner を再起動します（`_MAX_WARM_REUSES = 3`、`environments/xcuitest.py:272`）。冷起動は 15〜40 秒（est）です。

`wait_for` は runner 側の条件待ちではなく、ホストの 50 ms ポーリングです。
`docs/drivers.md` には「runner のネイティブな条件待ちを使う」とあります。
「screenshot は `simctl io screenshot`」ともあります。どちらも現在の実装と一致していません。

### 3.3 Android

常駐サーバー経路のタップ 1 ステップの推定内訳です。出典は [`reports/android.md`](reports/android.md) です。

| 局面 | 推定 | 原因 | 場所 |
|---|---|---|---|
| `before.png` | 200〜600 ms | `adb exec-out screencap -p` の subprocess | `backend_cli/adb.py:975-990` |
| `_settle` | 400〜600 ms | アクチュエーション後は最低 2 回の読み取りと 0.1 秒の sleep | `drivers/adb.py:983-1047` |
| 読み取り 1 回の端末側 | 100〜300 ms（BE-0245 実測） | `waitForIdle` と `dumpWindowHierarchy` を最低 2 回。さらに毎回 `nativeZ` のための全ノード走査 | `ResidentServerTest.kt:354-393、482-490、427-457` |
| `/clock` と `/act` | 600〜900 ms | 端末側で再度 2〜4 回の dump。公開待ち `ACT_PUBLISH_BUDGET_MS = 500` | `ResidentServerTest.kt:171-229、625` |
| `after.png` | 200〜600 ms | 上と同じ | |
| 事後の query | 200〜300 ms | フレームが動かないタップの後は `?since=` で最大 2 秒ブロック | `adb_resident.py:105-146` |
| 合計 | 2〜3 秒 | | |

読み取りのたびに新規 TCP 接続を張り、サーバーは `Connection: close` で返します（`adb_resident.py:125`、`ResidentServerTest.kt:544`）。
ホスト側では同じ XML を 2 回パースします（`adb_resident.py:78-102`、`drivers/adb.py:404-437`）。
シナリオごとの固定費もあります。常駐サーバーの APK（Android アプリケーションパッケージ）を毎回入れ直します。
その後に instrumentation を起動し直します（`adb_resident.py:383-399`）。推定で 6〜12 秒です。

`uiautomator dump` 経路は 1 読み取り 2.3〜2.5 秒（BE-0234 実測）です。
サーバー APK が未ビルドか、チャネルが故障すると、黙ってこの経路に落ちます。

## 4. ボトルネックの順位と対策

期待値は 1 ステップあたりの短縮です。「決定性」の列は、prime directive（固定 sleep の禁止、判定の決定性、アプリ非依存）への影響です。

### 4.1 両バックエンドに共通（段階 A）

| 順位 | 対策 | 期待値 | 決定性 | 変更箇所 |
|---|---|---|---|---|
| 1 | `before.png` を廃止するか、直前ステップの `after.png` を流用する。間にアクチュエーションがないので画面は同じ | iOS 150〜400 ms、Android 200〜600 ms | 影響なし。証拠のみ | `loop.py:1274-1329` |
| 2 | `after.png` を臨界パスから外す。取得は非同期スレッド、書き込みも非同期にする。JPEG か縮小も検討 | iOS 100〜300 ms、Android 100〜400 ms | 影響なし | `loop.py:1601`、`evidence/core.py:174-186` |
| 3 | `elements.json` を事後の 1 回だけ書く。事後の query はポリシーで要求された場合のみ発行する | iOS 45〜120 ms、Android 200〜300 ms | 影響なし | `loop.py:1712-1727`、`evidence_rules.py:190-194` |
| 4 | 初回ステップの sink 内部の隠れた query を消す | シナリオごとに 1 読み取り | 影響なし | `evidence/core.py:170` |
| 5 | BE-0310 の遷移シグナル経路で、静止待ちの間の query を止める。guard か interrupt があるときだけ読む | iOS 200〜300 ms（settled 1 回あたり） | 影響なし。判定はシグナルのまま | `waits.py:1042-1061` |
| 6 | `drain_interruptions` を `/tap` や `/elements` の応答に同乗させる | iOS 2〜5 ms | 影響なし | `xcuitest.py:1170`、`APIHandler.swift` |

### 4.2 iOS（段階 B）

| 順位 | 対策 | 期待値 | 決定性 | 変更箇所 |
|---|---|---|---|---|
| 1 | タップ時の属性読みを `el.snapshot()` 1 回にまとめる。`app.frame` はキャッシュする | 150〜300 ms | 影響なし | `XcuitestElementProvider.swift:410-430、122-125` |
| 2 | さらに進めて、記録済みの frame の中心を `app.coordinate` でタップする。identity 検証は stale が疑われるときだけ行う | 追加で 50〜150 ms | 要検討。座標タップは BE-0396 が Safari ですでに採用 | 同上 |
| 3 | `safariViewService.state` の確認を、snapshot にリモートビューの境界ノードがあるときだけにする | query 1 回あたり 5〜50 ms | 影響なし | `XcuitestElementProvider.swift:50` |
| 4 | `/zorder` を遅延評価にする。セレクタの z 順の曖昧さ解消が必要なときだけ呼ぶ | query 1 回あたり 5〜30 ms | 影響なし | `xcuitest.py:744-768` |
| 5 | HTTP keep-alive を両側で有効にする | 往復 1 回あたり 1〜3 ms | 影響なし | `xcuitest.py:591`、`HTTPServer.swift:326` |
| 6 | stale の初回は sleep せずに即再問い合わせする | 0.5 秒（発生時） | 影響なし。再問い合わせ自体が待ち | `xcuitest.py:146、848-853` |
| 7 | SpringBoard の probe は `alerts.firstMatch.exists` を先に見る。ボタンの列挙は存在時だけ | 100〜400 ms（probe 1 回あたり） | 影響なし | `XcuitestElementProvider.swift:302-320` |
| 8 | `BAJUTSU_XCUITEST_MAX_WARM_REUSES` を上げる。再インストールは digest 一致時に省く | シナリオごとに 4〜10 秒 | 影響なし | `environments/xcuitest.py:259-306` |
| 9 | 文字入力は `simctl pbcopy` と `typeKey("v", .command)` に置き換える | 1 文字あたり 40 ms | 要検討。ペースト非対応の入力欄がある | `simctl.py:842-878` |

### 4.3 Android（段階 B）

| 順位 | 対策 | 期待値 | 決定性 | 変更箇所 |
|---|---|---|---|---|
| 1 | `settledDump` の 2 回目の dump を、1 回目の前後で a11y イベントが来ていないときは省く。`ReadMark` がすでに知っている | 100〜200 ms（読み取り 1 回あたり） | 影響なし。判定条件は同じ | `ResidentServerTest.kt:482-490、558-587` |
| 2 | `nativeZ` の全ノード走査を、アプリがオプトインしたときだけにする | 20〜100 ms（読み取り 1 回あたり） | 影響なし | `ResidentServerTest.kt:427-457` |
| 3 | `/act` の応答に、端末側で settle 済みの木を同乗させ、ホストの `_settle` が最初の読み取りを省けるようにする | 400〜600 ms | 影響なし。端末側で 2 回一致した木 | `drivers/adb.py:1015-1031、1341`、`ResidentServerTest.kt:516-524` |
| 4 | フレームが動かないタップの barrier をイベント種別で判定する。`VIEW_CLICKED` のみでレイアウトイベントがなければ即答する | 500 ms〜2 秒（該当時） | 影響なし | `ResidentServerTest.kt:625、642`、`drivers/adb.py:536` |
| 5 | スクリーンショットを `UiAutomation.takeScreenshot()` で常駐サーバーから返す。subprocess を消す | 100〜400 ms（1 枚あたり） | 影響なし | `backend_cli/adb.py:975-990` |
| 6 | keep-alive。サーバーの `Connection: close` をやめ、同じソケットで `handle` を回す | 30〜150 ms | 影響なし | `adb_resident.py:125、183、269`、`ResidentServerTest.kt:83、544` |
| 7 | 常駐サーバーを run 全体で使い回す。APK の再インストールは署名とバージョンの一致時に省く | シナリオごとに 6〜12 秒 | 影響なし | `adb_resident.py:395-399`、`environments/android.py:367` |
| 8 | XML の二重パースをやめる | 10〜40 ms（読み取り 1 回あたり） | 影響なし | `adb_resident.py:78-102`、`drivers/adb.py:404-437` |
| 9 | `/act` に `swipe` を追加し、パンにも公開確認を与える | 70 ms と barrier 2〜4 秒（該当時） | 影響なし | `drivers/adb.py:1477-1552` |

## 5. 目標に届く設計案（フェーズ C）

### 5.1 判定の境界

prime directive 1 は「AI は判定しない」であって、「ホストが判定する」ではありません。
端末側の実行機が決定的にセレクタを解決し、条件待ちと assert を評価することは、この原則に反しません。
Python は次の 3 つを担い続けます。

- シナリオを展開し、端末側で実行できるステップの列に変換して送ります。
- 端末側から返る証拠（要素の木、座標、スクリーンショット、読み取りの時刻）を受け取り、`manifest.json` と HTML レポートに書きます。
- pass/fail を確定します。端末側の評価結果は入力であり、最終の verdict はホストが同じ決定的規則で確認します。

端末側に移すのは次のものです。

- セレクタ解決です。
- `wait for`、`until: gone`、`until: settled` です。
- `assert` のうち画面に閉じた種類（`exists`、`label`、`value`、`count`、`enabled`）です。
- `tap`、`type`、`swipe`、`scroll` です。

`http`、`email`、`generate`、`visual`、`golden`、`request` 系の assert はホストに残します。

### 5.2 iOS：XCTest ランナー内のステップ実行機

「Swift でシナリオをそのまま実行する」を、XCTest ランナーのプロセス内で行います。
アプリ内（BajutsuKit）ではありません。理由は 3 つです。

- BajutsuKit には、イベント注入、キーボード入力、frame 付きの accessibility 木、スクリーンショットのいずれもありません
  （[`reports/ios.md`](reports/ios.md) 第 5 節）。`BajutsuTouch` は観測用の swizzle で、合成はしません。
- SpringBoard の権限ダイアログ、システムキーボード、`SFSafariViewService` の中身は、アプリ内からは触れません。
- プロセス境界があるからこそ、アプリのクラッシュを観測できます。

ランナー内の実行機は、現在の `APIHandler` の上に `POST /scenario`（ステップの列）を足す形で作れます。
実行機は次の 4 つをネイティブに行います。

1. `app.snapshot()` を 1 回取り、セレクタをランナー内で解決します。現在の Python 側の `resolve_unique` を Swift に移植します。
2. タップは、解決した要素の frame 中心に `app.coordinate` で行います。属性の再読は行いません。
3. 条件待ちは、snapshot のループ（30〜40 ms 周期）で行います。BajutsuKit の画面遷移シグナル（BE-0310）は、
   現在 Python の collector にだけ届きます。ランナーにも届くようにして、`settled` の判定に直接使います。
4. スクリーンショットと要素の木は、ステップの結果と一緒に非同期で返します（chunked transfer で 1 ステップごとに送ります）。

タップ 1 ステップの推定は、snapshot 35 ms、座標タップ 100〜200 ms、settle 判定 35〜70 ms で、合計 200〜350 ms です。
スクリーンショットは臨界パスの外です。

XCUITest は、イベントの前後でアプリの静止（quiescence）を待ちます。
このタップ合成の 100〜200 ms が残る場合は、WebDriverAgent と同じ private API の利用が選択肢になります。
`XCUIApplicationProcess` の `waitForQuiescenceIncludingAnimationsIdle:` を無効にする方法です。
テストバンドル内の変更なのでアプリには影響しませんが、Xcode の更新で壊れうる点は受け入れる必要があります。

### 5.3 Android：instrumentation サーバー内のステップ実行機

常駐サーバーはすでに instrumentation として動き、`UiAutomation` と a11y イベントのリスナーを持っています
（[`reports/android.md`](reports/android.md) 第 6 節）。実行機に必要なのは次の 4 つです。

1. 木の読み取りを `dumpWindowHierarchy` の XML から、`AccessibilityNodeInfo` の直接走査に置き換えます。
   `nativeZ` のためにすでに同じ走査をしています。
2. `wait` と `settled` を、`TYPE_WINDOW_CONTENT_CHANGED` と `WINDOWS_CHANGED` のイベント駆動にします。
   2 回の dump が一致するまで待つ現在の方式を、イベントの静止で置き換えます。
3. タップとパンは `UiAutomation.injectInputEvent` で行い、公開確認もイベント種別で判定します。
4. スクリーンショットは `UiAutomation.takeScreenshot()` で取り、結果と一緒に返します。

タップ 1 ステップの推定は、木の走査 20〜50 ms、注入 50 ms、イベントによる settle 数十 ms で、合計 150〜300 ms です。

### 5.4 プロトコルの案と段階的な導入

一度にすべてを移す必要はありません。次の順で導入すると、各段階が単独で効きます。

1. `wait for` と `until: gone` を端末側にします（`POST /wait` にセレクタとタイムアウト）。50 ms ポーリングの往復が消えます。
2. `settled` を端末側にします。iOS は遷移シグナルの直接受信、Android は a11y イベントです。
3. `assert` の画面に閉じた種類を端末側にします。
4. ステップの列をまとめて送る `POST /scenario` にします。ここで 1 ステップ 1 往復以下になります。

セレクタの意味論は、`bajutsu/common/drivers/base.py` の `find_all` と `resolve_unique` が定義しています。
移植する対象は、`within`、`idMatches`、`labelMatches`、trait の派生です。
Android の `_derived_label`（`drivers/adb.py:251-282`）も含みます。
Swift と Kotlin に移植し、既存の driver conformance suite（BE-0114）で同値性を検査します。
曖昧なセレクタは端末側でも同じ文言で失敗させます。

### 5.5 アプリ内実行機を採らない判断

アプリ内で完結する実行機は、往復を最小にできる点で魅力があります。
それでも採らないのは、上に挙げた 3 つの理由に加えて、アプリ非依存という原則（prime directive 3）に反するからです。
アプリごとに SDK の組み込みが必須になり、SDK のないアプリでは動きません。
アプリ内 SDK の役割は、画面遷移シグナルのような「観測の補助」に留めるのが妥当です。

## 6. 実装の順序の提案

| 段階 | 内容 | 目安 | BE 項目の候補 |
|---|---|---|---|
| 0 | Mac で実測し、この資料の推定を実測値に置き換える | 1 日 | なし（この資料の更新） |
| A | 第 4.1 節の 1〜6 | 1〜2 週 | 証拠取得の非同期化と重複排除（1 件） |
| B-iOS | 第 4.2 節の 1、3、4、5、6 | 1〜2 週 | XCUITest タップ経路の属性読み一括化（1 件）、keep-alive（両 OS で 1 件） |
| B-Android | 第 4.3 節の 1、2、3、5、6、7 | 2〜3 週 | 常駐サーバーの読み取り最適化（1 件）、サーバー経由のスクリーンショットと再利用（1 件） |
| C | 第 5.4 節の 1〜4 | 4〜8 週 | 端末側ステップ実行機（プロトコル 1 件、iOS 1 件、Android 1 件） |

段階 A は他の段階と独立で、最初に効きます。
段階 C の 1（`wait` の端末側化）は、段階 B と並行して始められます。

## 7. Mac 側で続けるための手順

### 7.1 前提

```bash
make deps                                  # 初回のみ
make -C demos/showcase swiftui-build runner-build
make -C demos/showcase/android compose-build   # Android を測る場合
make -C BajutsuAndroidUIAutomatorServer build  # 常駐サーバーの APK
```

### 7.2 iOS の実測

`demos/showcase/Makefile` の `run-swiftui` と同じ引数を、トレーサー経由で渡します。

```bash
SIM=$(xcrun simctl list devices booted | grep -oE '[0-9A-F-]{36}' | head -1)
uv run python investigations/step-performance/trace_run.py --out /tmp/ios-trace.json -- \
  run --target showcase-swiftui --udid "$SIM" --backend ios \
  --exclude xcuitest,systemalert,visual,android,browser \
  --config demos/showcase/showcase.config.yaml \
  --scenario demos/showcase/scenarios/controls.yaml
```

終了時に、ステップごとの内訳と、呼び出しごとの回数と平均が表示されます。
`--out` の JSON には、1 呼び出しずつの記録が入っています。

### 7.3 Android の実測

```bash
uv run python investigations/step-performance/trace_run.py --out /tmp/android-trace.json -- \
  run --target showcase-compose --udid booted --backend android \
  --config demos/showcase/showcase.config.yaml \
  --scenario demos/showcase/scenarios/controls.yaml
```

常駐サーバー経路に乗っているかは、`transport:GET /source` の行が出るかで判ります。
`subprocess:adb exec-out uiautomator` が出ていれば dump 経路です。

### 7.4 埋めるべき数値

次の表の「実測」の列を埋めてください。この資料の推定と大きく違う行が、優先順位を変えます。

| 項目 | 読む行 | 推定 | 実測 |
|---|---|---|---|
| iOS `/elements` 1 回 | `transport:GET /elements` の平均 | 45〜120 ms | |
| iOS `/tap` 1 回 | `transport:POST /tap` の平均 | 300〜650 ms | |
| iOS `/screenshot` 1 回 | `transport:GET /screenshot` の平均 | 150〜400 ms | |
| iOS `/interruptionPolicy/drain` | 同 | 2〜5 ms | |
| iOS `wait until: settled` 1 ステップ | `per step` の該当行 | 0.3〜0.6 秒 | |
| Android `GET /source` 1 回 | `transport:GET /source` の平均 | 100〜300 ms | |
| Android `POST /act` 1 回 | 同 | 600〜900 ms | |
| Android screencap 1 回 | `subprocess:adb exec-out screencap` の平均 | 200〜600 ms | |
| Android シナリオ固定費 | ステップ外の `subprocess` 合計 | 10〜20 秒 | |

### 7.5 推定の再投影

`bench_orchestrator.py` の `MODELS` に実測値を入れて再実行すると、
ステップ種別ごとの end-to-end を、往復回数から投影し直せます。

```bash
uv run python investigations/step-performance/bench_orchestrator.py --model ios --steps 20
```

### 7.6 トレーサーの制限

- 1 プロセス内のスレッドだけを追います。run が別プロセスを fork する構成は追えません。
- ドライバ内部の sleep（stale の 0.5 秒など）は、その driver 呼び出しの時間に含まれます。
- `subprocess.run` と `subprocess.check_output` を包みます。`Popen` を直接使う interval capture（video、logcat）は数えません。
- 現在のトレーサーは `driver` の分類に fake driver も含めます。fake backend で自己検証するためです。

## 8. 未確認の事項とリスク

- iOS のタップ合成（XCUITest の quiescence 待ちを含む）の時間は未実測です。段階 C の下限を決める数値です。
- `app.snapshot()` はアプリが静止するまで待ちます。アニメーション中は 34 ms より大きく伸びます。
  端末側実行機でも、`settled` の下限はアニメーションの長さに縛られます。
- Android の `waitForIdle` は、a11y イベントが止まらない画面で最大 10 秒待ちます。イベント駆動化の設計時に上限を決める必要があります。
- `docs/drivers.md` の iOS の記述 2 点（条件待ちとスクリーンショットの経路）が実装と一致していません。
  修正は `record-issue` で Issue にするのが適切です。
- 段階 C は `Driver` インタフェースに「ステップの列を実行する」経路を足します。
  横断的な変更なので、着手前に BE 項目として設計を合意する必要があります。

## 付録

- [`bench_orchestrator.py`](bench_orchestrator.py)：fake driver 上のオーケストレータ計測です。
- [`trace_run.py`](trace_run.py)：実行時トレーサーです。
- [`reports/orchestrator.md`](reports/orchestrator.md)、[`reports/ios.md`](reports/ios.md)、[`reports/android.md`](reports/android.md)：読解報告（英語）です。

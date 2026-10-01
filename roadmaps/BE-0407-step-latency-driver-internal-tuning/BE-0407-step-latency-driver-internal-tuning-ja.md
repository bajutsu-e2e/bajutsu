[English](BE-0407-step-latency-driver-internal-tuning.md) · **日本語**

# BE-0407 — 証拠読み取りの重複排除とドライバ内部の調整による、ステップ実行の高速化（iOS と Android）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0407](BE-0407-step-latency-driver-internal-tuning-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0407") |
| 実装 PR | [#1897](https://github.com/bajutsu-e2e/bajutsu/pull/1897)（グループ 1、作業単位 1、3〜5）、[#1912](https://github.com/bajutsu-e2e/bajutsu/pull/1912)（グループ 1 の作業単位 6、グループ 2 の作業単位 7、9、10、11、12、13、そして 14 の半分）、[#1925](https://github.com/bajutsu-e2e/bajutsu/pull/1925)（グループ 1 の作業単位 2。グループ 1 の完了）、[#1938](https://github.com/bajutsu-e2e/bajutsu/pull/1938)（グループ 3 の作業単位 16、18、19、21、22、23）、[#2117](https://github.com/bajutsu-e2e/bajutsu/pull/2117)（作業単位 9 の差し戻し）、[#1944](https://github.com/bajutsu-e2e/bajutsu/pull/1944)（作業単位 8、14 の半分、15、17、20、24 を判断によって閉じ、項目を完了） |
| トピック | Platform support |
| 関連 | [BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query-ja.md)、[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)、[BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance-ja.md)、[BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse-ja.md)、[BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md)、[BE-0341](../BE-0341-pre-action-evidence-capture/BE-0341-pre-action-evidence-capture-ja.md)、[BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md)、[BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md)、[BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor-ja.md)、[BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md) |
<!-- /BE-METADATA -->

## はじめに

この項目自身のディレクトリ配下、[`misc/step-performance/`](misc/step-performance/README.md)
に記録した実機測定は、両バックエンドの `tap` ステップ 1 回を end-to-end で計測しました。
iOS シミュレータで 0.95〜1.07 秒、Android エミュレータの常駐サーバー経路で 3.25〜3.32 秒です。
目標の 250〜500 ミリ秒に対して、大きく上回っています。この時間のほとんどはタップ自体の
処理ではありません。orchestrator と両ドライバが毎ステップ払っている、ホストと端末間の
冗長な往復です。しかも、`Driver` プロトコルにも、どちらのバックエンドが話す通信形式にも、
今日時点で手を加えずに削減できます。この項目は、その削減を提案するものです。証拠取得の
重複排除とドライバ内部の調整からなる MECE（相互に排他的で、全体として網羅的）なリストで、
低コストで独立して着手できる対策から段階的に進めます。目標値は、iOS の `tap` ステップで
1 回あたり 0.3〜0.6 秒、Android で 0.6〜1.2 秒です。着実な前進にはなりますが、250〜500
ミリ秒という目標には届きません。残るギャップを埋めるには端末側ステップ実行機が必要で、
これはプロトコルレベルの変更としてこの項目の対象外とし、別途、より大規模な提案として
追跡します。

## 動機

実測されたステップが遅い理由は、タップそのものが遅いからではありません。iOS の
`POST /tap` は平均 690 ミリ秒、Android の `POST /act` は平均 2204 ミリ秒です
（どちらも[`demos/showcase/scenarios/controls.yaml`](../../demos/showcase/scenarios/controls.yaml)
に対して、2026-09-03 に 3 回ずつ計測）。それでも、単純な `tap` ステップ 1 回で、
orchestrator が次のステップに進むまでに端末との往復が iOS で 5 回、Android で 7 回発生します。
スクリーンショット 2 枚とタップ本体はどちらの OS にも共通です。iOS はこれに木の読み取り
1 回と割り込みの drain 1 回が加わって 5 回、割り込みの drain を持たない Android は木の
読み取りが 4 回加わって 7 回になります。このうちステップの意味的な仕事をしているのは
1 回だけです。

この回数の上に、さらに 3 つのコストが重なります。第 1 に、証拠の取得は同期的で、
しかもしばしば重複しています。`before.png` は、直前のステップの `after.png` が残した
状態から何も変化していないにもかかわらず、毎回撮り直されます
（[`bajutsu/common/orchestrator/loop.py:1274-1329`](../../bajutsu/common/orchestrator/loop.py)）。
`elements.json` も、アクションの前後で毎ステップ 2 回、シリアライズと秘匿処理を経て
書き込まれます（[`bajutsu/common/evidence/sink.py:161-210`](../../bajutsu/common/evidence/sink.py)）。
第 2 に、`wait until: settled` は、待ちを始めた時点で画面がすでに静止していても、
100 ミリ秒の下限を払います。これは
[`bajutsu/common/orchestrator/waits.py:31-32`](../../bajutsu/common/orchestrator/waits.py)
にある 3 回の読み取りの積です。第 3 に、各バックエンドはそれぞれ、ステップの大半を占める
単一の往復の内側に、独自の内部コストを抱えています。iOS は `POST /tap` の内側で、
約 11 個の要素属性を 1 つずつ解決します
（[`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift:127-151,410-430`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)）。
Android の常駐サーバーは、`POST /act` の大半を、`POSTDATE_BUDGET_MS` という固定の待ちに
費やしているとみられます。この待ちは、画面がすでに静止していると満たしようがありません
（[`BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt:184-188,642`](../../BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt)、
[`bajutsu/common/drivers/adb.py:812-831,1299`](../../bajutsu/common/drivers/adb.py)）。

以上はいずれも、決定性の契約には触れません。以下の削減はどれも、冗長な読み取りの除去、
冗長な書き込みの除去、あるいは画面がすでに静止していれば満たしようのない固定の待ちの
除去にとどまります。
条件待ちをポーリングから固定の `sleep` に変えるものはなく、セレクタの解決方法を変えるものも
ありません。この変更が実測どおりに効いたかどうかは、後から読む人が自分で確かめられます。
[`trace_run.py`](misc/step-performance/trace_run.py) を同じシナリオに対して
再実行し、iOS の `tap` ステップがこの項目の目標である 0.3〜0.6 秒の帯に、Android が
0.6〜1.2 秒の帯に収まることを確認すればよく、これは調査が今日の基準値 0.95〜1.07 秒と
3.25〜3.32 秒を測ったのと同じ手順です。

## 詳細設計

**実装の順番。** この項目は、関連する 4 項目の厳密な順番のうち最初です。この項目、
端末側プロトコルの項目、iOS 実行機の項目、Android 実行機の項目の順に進みます。後続の
どの項目も、直前の項目が出荷されるまで着手してはいけません。プロトコルの項目は、この
項目が出荷し実測した基準値に対して設計するものであり、まだ動いている数値を対象には
設計できないためです。また、この項目は、後続の項目が自分の成果を確認するために使う
トレーサー（[`trace_run.py`](misc/step-performance/trace_run.py)）を実証する役割も
兼ねています。この項目にはこの順番の中で先行する項目がなく、ただちに着手できます。

作業は、両バックエンド共通、iOS 専用、Android 専用という独立した 3 つのグループに
分かれています。それぞれが、[`trace_run.py`](misc/step-performance/trace_run.py)
がすでに確立した共通の物差しに対して、単独の Pull Request として出荷できます。以下のどの
作業単位も、`Driver` プロトコルやシナリオの YAML 形式、条件待ちが何をポーリングするかを
変更しません。それぞれが、冗長な呼び出しの削除か、固定コストの内部処理の並べ替えであり、
driver conformance suite
（[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)）と
トレーサーの再実行で検証します。

**グループ 1 は完了です。** 作業単位 1 と 3〜6 は記述どおりに着地しました。作業単位 2 は
`after.png` の側だけが着地しています。`elements.json` の側は取り下げました。その根拠となる
実測は、下の進捗に記録しています。

### グループ 1 — 両バックエンド共通（orchestrator）

1. **直前のステップの `after.png` を、次のステップの `before.png` として使い回す。**
   1 つのステップの終わりから次のステップの始まりまでの間には、アクチュエーションが
   発生しません。したがって両者は端末の同一状態を観測しています。シナリオ最初のステップ
   だけは、これまでどおり 1 回読み取ります。ステップごとにスクリーンショット 1 枚分を
   節約できます（iOS で 90 ミリ秒、Android で 103 ミリ秒）——
   [`loop.py:1274-1329`](../../bajutsu/common/orchestrator/loop.py)。
2. **`after.png` と `elements.json` の書き込みを臨界パスから外す。** ステップ自体の結果が
   確定した時点で、両方とも非同期に取得して書き込み、次のステップが書き込み完了を
   待たないようにします——
   [`loop.py:1601`](../../bajutsu/common/orchestrator/loop.py)、
   [`evidence/core.py:174-186`](../../bajutsu/common/evidence/core.py)。
3. **`elements.json` はステップ後に 1 回だけ書く。** アクション前の読み取りは、
   キャプチャポリシーが実際に要求する場合を除いて取りやめます——
   [`loop.py:1712-1727`](../../bajutsu/common/orchestrator/loop.py)、
   [`evidence_rules.py:190-194`](../../bajutsu/common/orchestrator/evidence_rules.py)。
4. **証拠 sink 内部に隠れた初回ステップの読み取りを消す。** sink は、シナリオの最初の
   ステップが走る前に独自の query を発行しています。この読み取りは、そのステップ自身の
   通常の読み取りと合流させ、二重の支払いを避けます——
   [`evidence/core.py:170`](../../bajutsu/common/evidence/core.py)。
5. **BE-0310 の遷移シグナルによる静止待ちの間、ポーリングを止める。** 画面遷移シグナル
   （[BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md)）
   が発火した後の 0.3 秒の静止確認期間は、そのステップに guard か interrupt ハンドラが
   登録されている場合だけ読み取りを続ければよく、それ以外はシグナル自体を条件とします——
   [`waits.py:1042-1061`](../../bajutsu/common/orchestrator/waits.py)。
6. **`drain_interruptions` を、専用の往復ではなく `/tap` か `/elements` の応答に
   同乗させる。** iOS は毎ステップ、これだけのために新規 TCP 接続を張っています
   （[`xcuitest.py:591,1170`](../../bajutsu/common/drivers/xcuitest.py)）。Android は
   すでにメモリ内で保持しているため、この項目は iOS 限定です。

### グループ 2 — iOS ドライバ内部

7. **タップ経路の属性読みを、`el.snapshot()` 1 回にまとめる。** `POST /tap` は現状、
   位置パスから要素を解決した後、`exists`、`identifier`、`label`、`type`、`enabled`、
   `selected`、`frame`（2 回）、`isHittable` という約 11 個の属性を、タップの合成前に
   1 つずつ読んでいます
   （[`XcuitestElementProvider.swift:127-151,410-430`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)）。
   `app.frame` は呼び出しをまたいでキャッシュし、都度の再読み取りをやめます。期待される
   削減量は 150〜300 ミリ秒です。
8. **キャッシュした frame の中心を座標でタップし、identity の再検証は stale が疑われる
   ときだけ行う。**
   [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md)
   は Safari のコンテンツに対してすでにこの方式を採っており、この作業単位はそれを
   一般化します。着手前に検討が必要です。座標タップは、要素が移動していた場合の
   正しさというリスクをレイテンシと引き換えにするため、identity 再検証へのフォール
   バックを正しく設計する必要があります。期待される削減量はさらに 50〜150 ミリ秒です。
9. **`safariViewService.state` のプロセス間確認は、スナップショットにリモートビューの
   境界ノードがあるときだけ行う。** 現状は、対象アプリが Web ビューを埋め込んでいるか
   どうかにかかわらず、すべての query がこの XPC（プロセス間通信）による確認を
   払っています——
   [`XcuitestElementProvider.swift:50`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)。
   期待される削減量は query 1 回あたり 5〜50 ミリ秒です。この作業単位は
   [#2117](https://github.com/bajutsu-e2e/bajutsu/pull/2117) で差し戻しました。iOS 26 では、境界ノードがサービス側の
   スナップショットにだけ現れ、アプリ側には現れません。そのため、この条件は成り立ちません。
10. **`/zorder` の第 2 往復を遅延評価にする。** セレクタが実際に曖昧で、その解決が
    必要なときだけ z 順を取得します——
    [`xcuitest.py:744-768`](../../bajutsu/common/drivers/xcuitest.py)。期待される
    削減量は、現状無条件に走っている query 1 回あたり 5〜30 ミリ秒です。
11. **XCUITest チャネルの両端で HTTP keep-alive を有効にする。**——
    [`xcuitest.py:591`](../../bajutsu/common/drivers/xcuitest.py)、
    [`HTTPServer.swift:326`](../../BajutsuKit/Sources/BajutsuRunner/HTTPServer.swift)。
    期待される削減量は往復 1 回あたり 1〜3 ミリ秒ですが、シナリオ中の多数の読み取りに
    積み重なります。
12. **stale 要素への初回リトライでは、sleep を挟まず即座に再問い合わせする。** runner は
    現状、初回リトライ前に固定 0.5 秒、2 回目の前に固定 1.0 秒スリープします
    （[`xcuitest.py:141-149,146,848-853`](../../bajutsu/common/drivers/xcuitest.py)）。
    これは、アクチュエーション中のあらゆる例外が `stale` に写像され、アニメーション中の
    一時的な失敗も含んでしまうためです。再問い合わせ自体がひとつの待ちなので、この
    sleep を取り除いてもリトライの決定性は弱まりません。
13. **SpringBoard のアラートボタンを列挙する前に、`alerts.firstMatch.exists` を先に
    確認する。** 現状のプローブは、確認のたびに全ボタンを列挙します。まず存在有無だけを
    確認し、実際にアラートがあるときだけ列挙します——
    [`XcuitestElementProvider.swift:302-320`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)。
    期待される削減量はプローブ 1 回あたり 100〜400 ミリ秒です。
14. **`BAJUTSU_XCUITEST_MAX_WARM_REUSES` を現状の 3 から引き上げ、アプリバンドルの
    digest が変わっていなければ再インストールを省く。** シナリオの境界ごとに 4〜10
    秒を節約します——
    [`environments/xcuitest.py:259-306`](../../bajutsu/common/platform_lifecycle/environments/xcuitest.py)。
15. **文字入力を、1 文字ごとの `typeText` ではなく `simctl pbcopy` とペーストの
    キー入力に置き換える。** 着手前に検討が必要です。ペーストは一部の入力ハンドラを
    経由しないため、ペーストを受け付けない入力欄向けのフォールバックが要ります——
    [`simctl.py:842-878`](../../bajutsu/common/backend_cli/simctl.py)。期待される
    削減量は、入力 1 文字あたり約 40 ミリ秒です。

### グループ 3 — Android ドライバ内部

16. **画面がすでに静止していれば満たしようのない `POSTDATE_BUDGET_MS` の待ちを、
    確認したうえで取り除く。** このグループの最優先の作業単位です。調査の実測では、
    2204 ミリ秒という `POST /act` の平均のほとんど（固定予算の 2000 ミリ秒相当）を、
    この単一の固定予算に帰着させており、この提案全体のどの単一項目よりも影響が
    大きいとみています。`respondAct` は
    タップを注入する前に、ホストがリクエスト送信直前に取った `since` マークより後の
    アクセシビリティイベントを待ちます
    （[`ResidentServerTest.kt:184-188,642`](../../BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt)、
    [`drivers/adb.py:812-831,1299`](../../bajutsu/common/drivers/adb.py)）。
    `controls.yaml` のすべてのタップ対象のように画面がすでに静止していれば、
    このマークより後に、タップ自身の注入より先に届くイベントは存在しません。
    したがってこの待ちは、本当に必要なときだけでなく毎回、予算を使い切ります。
    **対策の実装より前に、この機構を常駐サーバー自身のログで確認する必要があります。**
    調査時点の説明は、コード読解と実測の相関から組み立てたもので、ログによる直接の
    追跡ではありません。確認が取れたら、対策（たとえば、レイアウトイベントを伴わない
    `VIEW_CLICKED` だけが見込まれる場合は即座に応答する、など）は、同じ postdate マークが
    守っている read-lag barrier（`_READ_LAG_S`、
    [`adb.py:536`](../../bajutsu/common/drivers/adb.py)）への影響も検討が必要です。
    このバリアが現状ふさいでいる競合を、対策が再び開けてしまわないようにするためです。
17. **`settledDump` の 2 回目の読み取りを、1 回目との間にアクセシビリティイベントが
    来ていなければ省く。** `ReadMark` はすでにイベントの有無を把握しています——
    [`ResidentServerTest.kt:482-490,558-587`](../../BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt)。
    期待される削減量は読み取り 1 回あたり 100〜200 ミリ秒です。
18. **`nativeZ` の全ノード走査を、毎回ではなくアプリごとのオプトインにする。**——
    [`ResidentServerTest.kt:427-457`](../../BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt)。
    期待される削減量は読み取り 1 回あたり 20〜100 ミリ秒です。
19. **`/act` の応答自体に、すでに静止した木を同乗させる。** サーバーが端末側で木の一致を
    2 回すでに確認しているときは、ホストの `_settle` が最初の読み取りを省けるように
    します——
    [`adb.py:1015-1031,1341`](../../bajutsu/common/drivers/adb.py)、
    [`ResidentServerTest.kt:516-524`](../../BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt)。
    期待される削減量は 400〜600 ミリ秒です。
20. **スクリーンショットを、常駐サーバーから `UiAutomation.takeScreenshot()` で
    取得する。** `adb exec-out screencap` の subprocess 起動（実測 103 ミリ秒）を
    取り除きます——
    [`backend_cli/adb.py:975-990`](../../bajutsu/common/backend_cli/adb.py)。
21. **常駐サーバーのソケットを読み取りのたびに閉じず、開いたまま使い回す。** 現状は
    読み取りごとに `Connection: close` で応答しています——
    [`adb_resident.py:125,183,269`](../../bajutsu/common/backend_cli/adb_resident.py)、
    [`ResidentServerTest.kt:83,544`](../../BajutsuAndroidUIAutomatorServer/server/src/androidTest/java/dev/bajutsu/android/server/ResidentServerTest.kt)。
    期待される削減量は読み取り 1 回あたり 30〜150 ミリ秒です。
22. **常駐サーバーの Android Application Package（APK）を、run 全体で使い回す。**
    署名とバージョンがすでにインストール済みのものと一致していれば、再インストールを
    省きます——
    [`adb_resident.py:395-399`](../../bajutsu/common/backend_cli/adb_resident.py)、
    [`environments/android.py:367`](../../bajutsu/common/platform_lifecycle/environments/android.py)。
    期待される削減量はシナリオの境界ごとに 6〜12 秒です。調査で実測した 1.5 秒は、
    すでに起動済みでパッケージも導入済みのエミュレータへの再インストール分であり、
    コールドスタートの数値ではありません。
23. **ホスト側での階層 XML の二重パースをやめる。**——
    [`adb_resident.py:78-102`](../../bajutsu/common/backend_cli/adb_resident.py)、
    [`drivers/adb.py:404-437`](../../bajutsu/common/drivers/adb.py)。期待される
    削減量は読み取り 1 回あたり 10〜40 ミリ秒です。
24. **`/act` に、タップと同じ公開確認を持つ `swipe` 版を追加する。** 調査では `scroll`
    ステップを 7.1 秒と実測しており、タップより重くなっています。これは、swipe 自体と
    確認用の読み取りがそれぞれ別に、作業単位 16 と同じ固定予算を使い切っているためです
    ——[`drivers/adb.py:1477-1552`](../../bajutsu/common/drivers/adb.py)。この作業単位は
    作業単位 16 と根本原因を共有しており、しかも画面外のタップ復旧経路のコストも
    合わせて下げます。タップ対象が画面外から始まったとき、その復旧は内部で `scroll` を
    再試行するためです
    （[`bajutsu/common/orchestrator/loop.py`](../../bajutsu/common/orchestrator/loop.py)
    のタップ復旧処理）。したがって、復旧が必要な `tap` ステップも、`scroll` ステップ
    だけでなくこの作業単位の恩恵を受けます。

## 検討した代替案

- **端末側ステップ実行機を待ち、この段階的な対策は行わない。** 却下します。実行機は
  プロトコルレベルの変更であり、別途、より大規模な提案として追跡します。その間も、
  すべてのシナリオ実行は今日の冗長な読み取りと固定の待ちを払い続けます。上記の作業
  単位はその作業とは独立に出荷でき、競合しません。証拠取得の重複排除や keep-alive の
  変更など、いくつかは実行機が実現した後も有用であり続けます。端末側実行機が導入されても、
  証拠は結局同じチャネルでホストに返るからです。
- **現在の境界を調整せず、実行機に直接進む。**
  [BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance-ja.md) が自らの
  対策を段階分けしたのと同じ理由で却下します。内部調整の作業単位はリスクが低く、
  小さな独立した Pull Request として出荷でき、後の実行機の項目が自分の効果を示す
  ためにも依存する測定ツール——
  [`trace_run.py`](misc/step-performance/trace_run.py) と
  [`bench_orchestrator.py`](misc/step-performance/bench_orchestrator.py)
  ——を実証する役割も果たします。
- **Android の `POSTDATE_BUDGET_MS`（作業単位 16）は、サーバーログでの確認が
  先に要るため、別の項目に切り出す。** 検討しましたが、現時点では見送ります。
  この項目のほかの Android の作業単位と、ドライバとランナーの範囲、「プロトコルは
  変更しない」というスコープ、そして測定の物差しを共有しているためです。下の
  進捗チェックリストでは、これを完了ではなく未確認と明記しており、確認より前に
  黙って出荷されることはありません。確認が取れた後に対策自体が独立した設計の議論を
  要すると判明した場合は、その時点で切り出す余地を残します。
- **作業単位ごとに 1 つのロードマップ項目を立てる。** 24 個の作業単位は、今日実測
  したステップのレイテンシという 1 つの動機と、トレーサーが示す前後の数値という
  1 つの物差しを共有しているため、これほど細分化するのは過剰だと判断し却下します。
  各作業単位は、それでも独立した焦点を持つ Pull Request として出荷します。1 つの
  項目にまとめることで、実質的に 1 つの取り組みであるものに対して、ほぼ同じ動機の
  節と相互リンクを 20 数個のファイルにまたがって保守する手間を避けられます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

**順番の状態：先行する項目はなく、ただちに着手できます**（詳細設計の「実装の順番」を参照）。

- [x] 基準値の測定と物差しの構築——両バックエンドの実機トレース（2026-09-03）を、
  この項目自身のディレクトリ配下の
  [`misc/step-performance/`](misc/step-performance/README.md)
  に記録済みです。
- [x] グループ 1、作業単位 1、3、4、5——前のステップの `after.png` をこのステップの
  `before.png` として再利用します（作業単位 1）。ステップが動作する前に `elements.json` を
  書き込むのをやめます（作業単位 3〜4）。ガードや割り込みハンドラが登録されていないときは、
  BE-0310 の静止待ち区間中にデバイスをポーリングしません（作業単位 5）。
- [x] グループ 1、作業単位 2——ステップ必須の `after.png` を、ステップ後のツリー読み取りと
  重ね合わせます。詳細設計の字面からは 2 点ずれていますが、どちらも議論ではなく実測で決めました。
  まず `elements.json` の側は取り下げました。この書き込みは実機のツリーで 0.36 ミリ秒、
  100〜1200 要素の合成ツリーでも 0.9〜8.7 ミリ秒にとどまります。1 ステップの 2％ に満たない量を
  削るために、秘匿処理の経路へスレッドプールを持ち込む価値はありません。次に `after.png` の側は、
  書き込みではなくデバイスとの往復でした。そのため非同期化ではなく重ね合わせとして実装しました。
  しかも、バックエンド自身のチャネルが 2 つ目の呼び出しを同時に受け付けると宣言した場合に限ります
  （新設の `base.BackgroundScreenshotProvider`）。adb は受け付けますが、XCUITest は意図的に
  受け付けません。`APIHandler.serialized` があらゆる XCUITest 操作をランナーのメインスレッドへ
  流し込んでおり、BE-0323 が記録した再入不可能性に反するからです。見送りの理由だった 3 つの設計課題は、
  撮影が自分のステップを越えないことで解けました。ステップが戻る前の `finally` で待ち合わせるので、
  シナリオの境界で取り消すものも、レポート生成の前に待つものもありません。
- [x] グループ 1、作業単位 6——iOS の `drain_interruptions` を `/tap` 自身の応答へ畳み込みます。
  詳細設計が挙げていた「`/tap` または `/elements`」のうち、頻度の高い前者だけに絞りました。
  ドライバは tap の応答がすでに運んできた内容を蓄積します。その間に別の呼び出し（query や
  stale リトライの再解決など）が挟まった場合は、明示的な `/interruptionPolicy/drain` と
  合流させます。速い経路が取れないときでも、tap の応答が捉えた内容は黙って失われません。
- [x] グループ 2、作業単位 7——タップ経路の属性読みを `el.snapshot()` 1 回にまとめます。
  `app.frame` は常駐リースの生存期間中キャッシュします（一時的な `.zero` の読み取りを
  キャッシュしないよう保護しています）。
- [x] ~~グループ 2、作業単位 8。BE-0396 の座標タップを Safari 以外にも一般化する。~~
  - 着手したうえでレビューを受けて差し戻しました。`el.isHittable` は XCUITest 自身が計算
    するヒットポイントを確認します。このヒットポイントはカスタムの
    `accessibilityActivationPoint` を尊重し、部分的な遮蔽下では frame の幾何学的中心とは
    異なる点になりえます。座標タップは一方で、常にその幾何学的中心に着地します。実際の
    ヒットポイントが空いている要素でも、サイレントな誤タップを起こしかねません。その後の
    調査では、XCUITest の公開 API が解決済みのヒットポイントも、点から要素への
    ヒットテストも提供しないことを確認しました。誤タップのリスクを受け入れるか、タップの
    たびにツリーを読み直す（この作業単位が削ろうとしていた往復そのものを復活させて
    しまいます）かのどちらかしか道がなく、2 つの点を整合させる設計は成り立ちません。
    Safari 限定の経路を維持し、この項目内ではこれ以上追わないと判断しました。
- [x] ~~グループ 2、作業単位 9。`safariViewService.state` の XPC プローブをアプリ側の
  スナップショットで条件付ける。~~
  - #1912 で着地させたあと、[#2117](https://github.com/bajutsu-e2e/bajutsu/pull/2117) で差し戻しました。
    iOS 26.5 の Simulator では、
    境界ノードはサービス側のスナップショットにだけ現れ、アプリ側には現れません。そのため
    条件が一度も成り立たず、`SFSafariViewController` の要素がすべてツリーから消えました。
    シナリオはアプリ内ブラウザを操作できず、ガードもその上に出る Save Password アラートを
    見つけられませんでした。より安価な条件にするには、iOS 26 のアプリ側スナップショットが
    実際に持つ手がかりが必要ですが、まだ見つかっていません。
- [x] グループ 2、作業単位 10——`/zorder` を遅延評価にします。詳細設計の記述からの逸脱：
  `nativeZ` は診断専用の値で、セレクタの曖昧性解決には一切使われていません
  （`resolve_unique` の `_collapse_identical_duplicates` はこのキーを意図的に除外して
  います）。そのため「セレクタが曖昧なときだけ」という条件は、現在のコードに対しては
  文字どおりには発火しません。代わりに「木を即座に破棄する内部専用の解決クエリでは
  省略し、証拠取得や `serve` が実際に消費する公開の `query()` 経路では維持する」という
  形で実装しました。古い記述が本来意図していたことは、これだと判断しています。
- [x] グループ 2、作業単位 11——チャネルの両端で HTTP keep-alive を有効にします。1 つの
  永続接続をドライバのリース全体で使い回します。破棄して再接続する条件は 3 つあります。
  1 つは能動的な生存確認（ゼロタイムアウトの `select` と、消費しない peek）がすでに閉じ
  られていると判定したときです。もう 1 つは実際の失敗が起きたときです。もう 1 つはアイド
  ル状態がランナー自身のタイムアウトの一定割合を超えて続いたときです。peek で確認してか
  ら実際に送信するまでの間には、ランナー側のタイムアウトが発火する余地は残ります。この余
  地は、アイドル時間に上限を設けることで塞ぎました。
  `HTTPServer.swift` 側にも終了条件が 3 つあります。1 つは相手がアイドルになったときで
  す。もう 1 つは不正な入力を送ってきたときです。もう 1 つは応答の書き込みを最後まで終え
  られなかったときです。書き込みが途中で終わると、ストリームが食い違ったまま次のリクエス
  トを処理してしまいます。この場合は接続をその場で終了し、次のリクエストを処理しません。
  この 3 つのいずれかに当たるまで、1 接続あたりループし続けます。
- [x] グループ 2、作業単位 12——stale ハンドルへの初回リトライでは即座に再問い合わせします。
  2 回目のリトライは 1.0 秒のバックオフを維持します。
- [x] グループ 2、作業単位 13——SpringBoard のアラートボタンを列挙する前に
  `alerts.firstMatch.exists` を確認します。
- [x] グループ 2、作業単位 14。半分だけ出荷しました。アプリバンドルの digest が
  変わっていなければ再インストールを省くほうは着地しました。`reinstall: overwrite`
  に限定しています。`clean` は、アンインストールしてから再インストールするという
  意図的なデータ消去なので、digest チェックがこれを省いてはいけません。
  ~~`BAJUTSU_XCUITEST_MAX_WARM_REUSES` を 3 より上げる~~
  - こちらは見送りました。3 というデフォルト値は、常駐ランナーがクラッシュし始める境目
    として BE-0291 が実測で決めたものです。今回はそれより多く再利用に耐える端末を測定
    しておらず、動かす根拠がありません。この項目内ではこれ以上追いません。
- [x] ~~グループ 2、作業単位 15。文字入力を `simctl pbcopy` とペーストのキー入力に
  置き換える。~~
  - 着手したうえで、`text_editing.yaml` の実機実行後に差し戻しました。
    `app.typeKey("v", modifierFlags: .command)` は、ペーストのたびに iOS のクロスアプリ
    ペースト許可アラート（「"BajutsuRunnerUITests-Runner" would like to paste from
    "Showcase SwiftUI" — Do you want to allow this」の文言）を誘発します。このアラートは
    ランナーのメインスレッドを無期限にブロックします。割り込みモニターが応答する
    ボタンは登録されておらず、`POST /type` をタイムアウトさせてランナーをクラッシュ
    させます。その後の調査では、この呼び出しの最中に割り込んでこのアラートを
    閉じる手段がコードベースのどこにもないことを確認しました。既存のアラート処理は
    すべて、XCUITest の呼び出しとステップの間、または XCUITest が次の操作を合成する
    直前にしか動作せず、XCUITest 自身の再入不可能性（BE-0323）が、ブロックされた
    `typeKey` の呼び出しがメインスレッドを握っている間に別スレッドから同時にアラートを
    閉じることを禁じています。ペーストボードの確認に `simctl privacy` 相当の抑制手段も
    存在しません。1 文字ずつの `typeText` を維持し、この項目内ではこれ以上追わないと
    判断しました。
- [x] グループ 3、作業単位 16。機構は直接の計測で確認でき、対策は設計のやり直しではなく
  1 箇所の訂正で済みました。`_device_act` は 1 つの値を 2 つの用途に兼用していました。
  端末自身が注入前に行う読み取りが後追いすべきマークと、ジェスチャの後に張るバリアの
  アンカーです。どちらもリクエストを組み立てた時点のクロックを取っていたため、端末は
  「リクエスト自身より新しいイベント」を待たされていました。静止した画面が生み出しよう
  のないイベントです。API 34 のエミュレータで、静止した画面に対してチャネルを直接叩いて
  計測しました。`POST /act` はそのマークを送ると 2217 ミリ秒と 2042 ミリ秒、送らないと
  64 ミリ秒と 62 ミリ秒でした。待ちは stale 判定より前に置かれているので、stale の応答も
  同じ待ちを払っていました。`since` には保留中のバリア自身のマークを渡すようにし
  （`_settle` がたいてい先に消化しています）、バリアはこれまでどおりクロックを
  アンカーにします。read-lag バリアが守っているものは何も動きません。ホストは送信前に
  settle しますし、端末も自身の bounds 読み取りを settle します。
- [x] グループ 3、作業単位 18。`nativeZ` の走査は、毎回の読み取りで走るのではなく、
  `targets.<name>.android` の `nativeZ: true` で要求するものになりました。
- [x] グループ 3、作業単位 19。`/act` が追いついたツリーを載せて返すので、ホストが
  `_settle` の冒頭で行う読み取りはすでに済んでいます。publish の確認が取れたときだけ
  載せます。その確認こそが安全性の根拠だからです。注入より後のイベントが存在する以上、
  そのダンプがジェスチャ前の画面であることはありえません。ドライバ側はさらに、ツリー
  自身のマークがジェスチャを後追いしているかを検査するので、ヘッダを1つ取り違えた
  サーバーが古い画面を種として渡すことはできません。
- [x] グループ 3、作業単位 21。リース 1 つにつき 1 本の接続を両端で保ちます。破棄の判断は、
  作業単位 11 が XCUITest チャネルで使っているのと同じ、送信前の能動的な staleness 検査です。
- [x] グループ 3、作業単位 22。常駐 APK の対は 1 回の実行のリース間で再利用します。判定は
  2 つの APK 自身のダイジェスト（先に uninstall する規則が守っていた署名鍵も、これで
  固定されます）と、端末が両方をまだ持っていることを確かめる `pm path` です。
- [x] グループ 3、作業単位 23。常駐チャネルは、装飾ウィンドウを取り除くために自分が
  すでにパースしたツリーをドライバへ渡します。narrowing 後のボディの直列化は、`rawTree`
  の取得が要求したときにだけ行います。
- [x] ~~グループ 3、作業単位 17。2 回の読み取りの間にアクセシビリティイベントが発火して
  いなければ `settledDump` の 2 回目を省く。~~
  - 実装しましたが、計測の結果、取り下げました。前提自体は単体では正しいものです。
    ノードの bounds はアクセシビリティイベントを経てダンプに届くので、イベントが
    なければ変化もありません。見落としていたのは、その確認用のダンプが、サーバー自身の
    リスナーへイベントを流し続ける役割も担っていたことでした。ホストの read-lag
    バリアが解放の根拠にしているのは、そのリスナーが保つ read mark です。2 回目の
    ダンプをやめると mark が止まります。API 34 のエミュレータで `controls.yaml` の
    scroll ステップは 7.5 秒、7.7 秒、10.0 秒になりました。ダンプを戻すと 5.7 秒、
    5.9 秒、6.0 秒です。伸びない mark を相手に、pan のバリアが予算を使い切っていた
    ためです。その後の調査では、ダンプに頼らず mark を生かし続けられる、より
    軽量な代替手段が見つかりませんでした。サーバー自身のコメントは、`waitForIdle`
    がリスナーの観測しているのと同じイベントストリームに依存していると述べています。
    ダンプ自体がそのストリームにどう作用しているかの代替は、示していません。
    2 回のダンプが一致することで settled と判定する現状の実装を維持し、この項目内
    ではこれ以上追わないと判断しました。
- [x] ~~グループ 3、作業単位 20。常駐サーバーの `UiAutomation.takeScreenshot()` で
  スクリーンショットを撮る。~~
  - 実装していません。この設計は作業単位 2 より前のもので、作業単位 2 は前日に着地し
    （[#1925](https://github.com/bajutsu-e2e/bajutsu/pull/1925)）、`adb exec-out screencap`
    のサブプロセスをツリー読み取りと重ねて臨界パスの外へすでに出しています。撮影を
    常駐チャネルへ移すと、その重なりを深めるどころか壊します。サーバーは一度に 1 本の
    接続しか処理しないので、いま並走している読み取りの後ろに直列化されるからです。
    作業単位 2 はこの組を、直列で 3361 ミリ秒、重ねて 2182 ミリ秒と計測しています。
    この作業単位が狙うサブプロセス起動は 103 ミリ秒です。並行処理するサーバーが
    入口になりますが、`UiAutomation` がダンプと並行するスクリーンショットを許すかは
    文書化されていません。性能ではなく決定性の問題です。作業単位 2 がこの作業単位の
    狙いをすでに埋めているため、この項目内ではこれ以上追いません。
- [x] ~~グループ 3、作業単位 24。`/act` に swipe 版を追加する。~~
  - 2 通り実装し、いずれも実機計測の結果、取り下げました。`UiDevice.swipe` を使うと
    `controls.yaml` の scroll ステップは 12.8 秒になり、座標経路の 4.2 秒に対して
    大幅に遅くなりました。UiAutomator はドラッグを `UiAutomation.executeAndWaitForEvent`
    経由で刻んでおり、この API は見えたイベントを消費します。pan のイベントはすべて
    ドラッグの最中に発生するので、read mark には 1 つも届かず、応答は publish を
    確認できず、ホストのバリアが 4 秒を使い切りました。ドラッグを `MotionEvent` から
    組み立て直すと 7.5 秒まで下がりました。同じ理由で `injectDoubleTap` がすでに
    採っている形です。それでも座標経路より遅く、サーバー側に仕込んだプローブは、
    pan の全区間で read mark が動いていないことを示しました。そのドラッグはコンテンツ
    も動かしていなかったことになります。この作業単位が挙げていた根本原因は
    作業単位 16 の固定予算であり、作業単位 16 はそれを取り除きました。scroll ステップに
    残るのは、BE-0400 が意図的に固定しているドラッグ自身の所要時間と、確認の読み取り
    1 回です。根本原因はすでに解消しているため、この項目内ではこれ以上追いません。
- [x] 各グループの出荷後に、`controls.yaml` に対して
  [`trace_run.py`](misc/step-performance/trace_run.py) を再実行し、結果のステップごとの
  壁時計をここに記録する。iOS 側は、グループ 1 の作業単位 6 と、出荷したグループ 2 の
  分について実施済み（2026-09-06、iPhone 17 Pro Simulator、`controls.yaml`）：
  `POST /tap` の平均は基準値の 690 ミリ秒から 446 ミリ秒へ、タップ 3 回の計測で下がり、
  `drain_interruptions` の呼び出し 9 回のうち 6 回だけがワイヤに到達しました（残りは
  tap 自身の畳み込みから回答）。基準値に対する実測の前進ではありますが、作業単位 8 と
  15 を見送った現状では、この項目自身が掲げる tap 1 回あたり 0.3〜0.6 秒という目標には
  届いていません。Android 側は、グループ 3 の出荷後に実施しました（2026-09-07、API 34
  arm64 エミュレータ、`controls.yaml`、起動し直した端末で実行を直列化し、前後それぞれ
  3 回）。壁時計はトレーサーではなく実行マニフェストのステップ時間から取りました。この
  項目の目標がステップ単位で述べられていることと、エミュレータ自身のばらつきが呼び出し
  単位の削減を覆い隠すためです。`tap` ステップは 3.46〜5.67 秒から 2.32〜4.57 秒へ、
  中央値でおよそ 3 分の 1 減りました。`scroll` ステップは 5.99〜6.02 秒から 5.97〜8.24 秒
  で、ばらつきの範囲内で変わりません。シナリオ全体は 25.3〜28.1 秒から 19.2〜26.7 秒です。tap のおよそ 3 分の 1 を削った形で、
  この項目が Android に掲げる 0.6〜1.2 秒には届いていません。残るギャップを埋めるのは、
  端末側実行機の
  [BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md)
  です。
- [x] この項目と端末側プロトコル、iOS 実行機、Android 実行機の各項目との間で `関連` の
  相互リンクを補いました（両言語とも）。採番前は新規項目どうしを `BE-XXXX` で相互参照
  できないため、`roadmap-id` ワークフローが `main` 上で 4 項目の ID を採番したあとに
  実施しています。
- [x] 「関連項目」と書いている各箇所を、採番済みの項目へのリンクに置き換える（両言語とも）。

ログ：

- [#1897](https://github.com/bajutsu-e2e/bajutsu/pull/1897) — グループ 1、作業単位 1、3〜5。ステップ前の
  baseline が書いていた `elements.json` の書き込みをやめました。動作後の取得が結局は書き直すためです。
  唯一この動作後の取得へ到達しない経路（`handleSystemAlert` のロケールが未対応で失敗するステップ）では、
  代わりにツリーを明示的に書き込みます。ガードや `interrupts` ハンドラが登録されていないときは、
  BE-0310 の静止待ち区間中にデバイスをポーリングするのをやめました。前のステップから何も操作していなけ
  れば、前のステップの `after.png` を次のステップの `before.png` として再利用するようにしました。回復
  ステップ、`handleSystemAlert` ステップ、`interrupts` を宣言したシナリオのどのステップでも、非同期の
  割り込み画面が現れている可能性があります。この場合は常に新たに撮影します。作業単位 2（証跡書き込みの
  非同期化）と 6（iOS の `drain_interruptions` の畳み込み）は、後続の PR に残しています。
- [#1912](https://github.com/bajutsu-e2e/bajutsu/pull/1912) — グループ 1 の作業単位 6 と、グループ 2 の作業単位 7、9、10、11、12、13です。作業単位 14 は
  半分（digest スキップのほう）だけです。割り込みの drain を `/tap` 自身の応答へ畳み込み、
  ドライバが tap の畳み込みをすでに運んできた内容として蓄積するようにしました。その間に
  別の呼び出しが挟まったときは、明示的な drain と合流させます。タップ経路の属性読みを
  `el.snapshot()` 1 回にまとめ、`app.frame` をリースの生存期間中キャッシュしました。
  `safariViewService.state` の XPC プローブと `/zorder` の往復を条件付きにしました
  （後者は発火条件を作り直しています。詳細は作業単位 10 に関する進捗の注記を参照）。
  1 つの永続 HTTP 接続をリースごとに両端で使い回すようにし、失敗の例外種別から推測する
  のではなく、再利用前の能動的な生存確認で
  裏付けました。stale ハンドルへの初回リトライ前の固定 sleep と、SpringBoard アラート
  プローブの列挙優先の経路を取り除きました。アプリバンドルの digest が変わっていなければ、
  warm resume 時の再インストールを省くようにしました（`reinstall: overwrite` に限定）。
  iPhone 17 Pro Simulator 上で `smoke`、`controls`、`alert`、`text_editing`、
  `permission_system_alert` を通しで実行して検証済みです。`controls.yaml` に対する
  `trace_run.py` の実行では、`POST /tap` の平均が基準値の 690 ミリ秒から 446 ミリ秒へ下がり、
  `drain_interruptions` の呼び出し 9 回のうち 6 回が tap 自身の畳み込みから回答し、ワイヤへは
  到達しませんでした。作業単位 8（座標タップの一般化）と 15（ペースト経由の文字入力）は
  着手したうえで、レビューと実機検証の両方で設計として安全でないと判明し差し戻しました。
  詳細はどちらも進捗の注記を参照してください。作業単位 14 の `MAX_WARM_REUSES` のほう、
  作業単位 16、作業単位 17〜24、そしてすでに見送っているグループ 1 の 2 つの作業単位は、
  後続の PR に残しています。
- [#1925](https://github.com/bajutsu-e2e/bajutsu/pull/1925) — グループ 1 の作業単位 2 です。
  これでグループ 1 は完了しました。書き込みを非同期化するのではなく、ステップ必須の `after.png` を
  ステップ後のツリー読み取りと重ね合わせました。実測したところ、費用はデバイスとの往復のほうにあり、
  書き込みは実機のツリーで 0.36 ミリ秒にすぎなかったためです。重ね合わせは、両側それぞれの狭い
  オプトインで守っています。ドライバ側が `base.BackgroundScreenshotProvider`、シンク側が
  `DeferredScreenshotSink` です。実行ループはバックエンド名で分岐せず、この 2 つを問い合わせます。
  どちらも宣言しないバックエンドは、これまでどおり同期的に撮影します。ドライバ側を宣言するのは adb
  だけです。iOS のランナーはあらゆる XCUITest 操作をメインスレッドへ直列化するので、重ねる余地が
  ありません。保留中の撮影は、ステップが戻る前の `finally` で待ち合わせます。これが、この作業単位が
  見送られていた 3 つの設計課題への答えです。詳細設計が挙げていなかった 3 つの帰結も、テストで
  固定しました。撮影の失敗が、その原因となった失敗の身代わりになることはありません。予約したファイルは、
  記録側が書き込む前に権限を絞ります。そして一度も書き込まれなかった撮影は、0 バイトの残骸を残しません。
  API 34 のエミュレータ上で実際のドライバとシンクを通して計測したところ、撮影してから読み取るまでが
  3361 ミリ秒から 2182 ミリ秒へ下がりました。
- [#1938](https://github.com/bajutsu-e2e/bajutsu/pull/1938) — グループ 3、作業単位 16、18、19、21、22、23。リクエスト自身より新しいイベントを端末に
  待たせるのをやめました。静止した画面が生み出しようのないイベントで、そのためにどのジェスチャも
  サーバーの 2 秒の postdate 予算を丸ごと払っていました。`nativeZ` のノード走査は、Android の
  すべての読み取りが払うものではなく、ターゲットが自身の設定で要求するものにしました。`/act` には、
  端末がジェスチャの publish を確認できたときに限って、そのとき取ったツリーを載せて返させます。
  ホストが `_settle` の冒頭で行う読み取りが、これで済んでいることになります。確認が取れたことと、
  ツリー自身のマークがジェスチャを後追いしていることの両方を条件にしているので、確認のない応答が
  古い画面を種として渡すことはありません。HTTP 接続はリース 1 つにつき 1 本を両端で保ちます。
  常駐 APK の対は、その実行がすでに同じバイト列をその端末へ置いていて、`pm path` が両方をまだ
  見つけられるあいだは、リースごとに入れ直しません。階層 XML のホスト側の二重パースをやめ、
  narrowing 後のボディの直列化は `rawTree` 取得の入口へ移したので、取得しない実行は代金を払いません。
  API 34 arm64 のエミュレータで、Android の e2e レーンが回す 18 本のシナリオすべてを通して検証し、
  すべて成功しました。前後それぞれ 3 回の計測で、`tap` ステップは 3.46〜5.67 秒から 2.32〜4.57 秒へ
  下がり、`scroll` はエミュレータのばらつきの範囲内で変わりません。作業単位 17 と 24 はいずれも
  実装したうえで、置き換える対象より遅いことが計測でわかったため取り下げました。作業単位 20 は
  実装していません。3 つとも進捗の該当項目に理由を記しています。
- [#1944](https://github.com/bajutsu-e2e/bajutsu/pull/1944) — 作業単位 8、14（`MAX_WARM_REUSES` の半分）、15、17、20、24 を、未決のまま残すのでは
  なく、対応しないと判断して閉じました。それぞれをあらためて調査した結果、安全に着地
  させる道が見つからなかったためです。作業単位 8 は、座標タップの着地点と `isHittable`
  のヒットポイントを整合させられません。XCUITest の公開 API が、解決済みのヒット
  ポイントも、点から要素へのヒットテストも提供しないからです。作業単位 15 のペースト
  許可アラートは、呼び出しの最中には閉じられません。XCUITest の再入不可能性（BE-0323）
  が、ブロックされた呼び出しがメインスレッドを握っている間の、別スレッドからの同時
  操作を禁じているためです。作業単位 17 のダンプ省略には、Android の read mark を
  進ませ続けている何かに対する、より軽量な代替手段が見つかっていません。作業単位 14 の
  再利用上限はデータ不足によって、20 は作業単位 2 によって、24 は作業単位 16 によって、
  それぞれすでに決着がついています。進捗チェックリストはこれで全項目にチェックが
  入りました。設計どおりに着地した作業単位はそのまま残し、判断によって閉じた作業単位は
  取り消し線を引いて理由をその下にネストしました。状態を実装済みに移します。この項目
  自身が掲げるレイテンシ目標への残りのギャップは、BE-0408 から BE-0410 で追跡している
  端末側実行機の役割です。
- [#2117](https://github.com/bajutsu-e2e/bajutsu/pull/2117) — 実機での二分探索で iOS 26 の退行の原因を作業単位 9 と特定し、差し戻しました。
  条件があるあいだは、アプリ内ブラウザの要素がツリーに届かず、`save_password_browser.yaml`
  が毎回失敗していました。ツリーの読み取りは #1912 以前と同じく、query のたびに
  `safariViewService.state` を確認します。

## 参考

[BE-0105 — XCUITest の要素取得を単一スナップショット化する](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query-ja.md)、
[BE-0114 — backend 非依存の挙動を検査する driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)、
[BE-0234 — adb のシナリオ実行を高速化する](../BE-0234-adb-run-performance/BE-0234-adb-run-performance-ja.md)、
[BE-0259 — assert と extract のステップでの query スナップショット再利用](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse-ja.md)、
[BE-0310 — アクセシビリティの画面遷移通知による readiness 判定の精度向上](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md)、
[BE-0341 — ステップの動作前にレポート証跡を取得する](../BE-0341-pre-action-evidence-capture/BE-0341-pre-action-evidence-capture-ja.md)、
[BE-0396 — SFSafariViewController の要素ツリーをそれを描くプロセスから読む](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md)、
[`misc/step-performance/README.md`](misc/step-performance/README.md)——
この項目が要約している測定の全文、
[`bajutsu/common/orchestrator/loop.py`](../../bajutsu/common/orchestrator/loop.py)、
[`bajutsu/common/drivers/xcuitest.py`](../../bajutsu/common/drivers/xcuitest.py)、
[`bajutsu/common/drivers/adb.py`](../../bajutsu/common/drivers/adb.py)、
[`bajutsu/common/backend_cli/adb_resident.py`](../../bajutsu/common/backend_cli/adb_resident.py)

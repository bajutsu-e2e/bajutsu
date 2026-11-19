[English](BE-0496-android-heads-up-notification-dismiss.md) · **日本語**

# BE-0496 — Android の heads-up 通知を報告し、スワイプで消す

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0496](BE-0496-android-heads-up-notification-dismiss-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0496") |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)、[BE-0416](../BE-0416-ios-notification-banner-swipe-dismiss/BE-0416-ios-notification-banner-swipe-dismiss-ja.md)、[BE-0478](../BE-0478-android-device-control-parity/BE-0478-android-device-control-parity-ja.md) |
<!-- /BE-METADATA -->

## はじめに

iOS では、テスト対象のアプリに重なったフォアグラウンドの通知バナーを Bajutsu が消します
（[BE-0416](../BE-0416-ios-notification-banner-swipe-dismiss/BE-0416-ios-notification-banner-swipe-dismiss-ja.md)）。
ドライバーが通知バナーの枠を報告します。それを受けて、オーケストレーターにあるバックエンド非依存の
ガードが通知バナーを上へスワイプし、消えるまで待ちます。一方、Android（adb）のドライバーは通知バナーを
一切報告しません。Android の heads-up 通知は、実行中のアプリの上に SystemUI が重ねて表示するバナーです。
heads-up 通知はステップの操作対象に重なり、そのステップのタップを奪うことがあります。

本提案では、adb ドライバーが heads-up 通知の枠を報告し、`handleNotificationBanner` ケーパビリティを
宣言するようにします。これで既存のガードが、Android 固有のコードなしに Android でも通知バナーを消します。さらに、
iOS には不要な点検を1つ加えます。adb ドライバーは、解決した操作対象または座標の指定による操作の直前に、
その操作点を覆う heads-up 通知を先に消します。

## 動機

heads-up 通知は、実行中のどの時点でも現れることがあります。サーバーは自分の都合でプッシュ通知を送ります。アプリの
ローカル通知も、ステップではなく実時間に沿って届きます。heads-up 通知がステップの操作対象に重なると、
`adb shell input tap` はタップを最前面のウィンドウ、つまり SystemUI の heads-up ウィンドウに届けます。
タップは通知元のアプリを開き、シナリオが検証していた画面から実行を連れ去ります。実行はその後のステップで
失敗しますが、レポートには通知バナーを示す記録が何も残りません。この不具合は、不安定なテストに見えます。

欠けているのは仕組みではなく、宣言1つと読み取り1つです。オーケストレーターの通知バナーのガードは、
`handleNotificationBanner` を宣言したどのバックエンドに対してもすでに動きます。ガードの本体は
`bajutsu/common/orchestrator/loop/_functions.py` の `_clear_notification_banner` です。ガードは枠を読み、
上向きのスワイプを計算してスワイプし、通知バナーが消えるまでポーリングします。ところが adb ドライバーの
`notification_banner_frame` は `None` を返し、`AdbDriver.CAPABILITIES` もこのケーパビリティを含みません。
そのため、ガードは Android を毎回素通りします。

ドライバーが必要とするデータは、resident channel を通じてすでにホストへ届いています。resident server は
読み取りに `UiDevice.dumpWindowHierarchy()` で答え、この関数は SystemUI を含むすべてのウィンドウをたどります。
その後、`narrowed_root` が `com.android.systemui` のウィンドウをすべて捨てます（定義は
`bajutsu/common/backend_cli/adb_resident/_functions.py`）。目的は、resident の読み取りを
`uiautomator dump` と同じ要素に揃えることです。heads-up ウィンドウも、ステータスバーと一緒に捨てられます。adb ドライバーの
`notification_banner_frame` に付いたコメントは「heads-up 通知はアプリ自身のツリーに届く」と述べています。
resident の絞り込みは、この記述と矛盾します。

本提案が実現したかどうかは、Android エミュレーターのレーンで動く showcase のシナリオ1本で確かめられます。
シナリオはローカル通知を出し、続いて heads-up 通知に覆われたコントロールをタップします。本提案の前は、
タップが通知を開き、次のアサーションが失敗します。本提案の後は、タップがコントロールに届きます。
どちらの経路で消した場合も、実行のレポートには種別 `notificationBanner` の通知バナーの消去が記録されます。

## 詳細設計

作業は8つの単位に分かれます。Unit 1 は端末を実測します。Units 2〜5 はホスト側を変えます。Units 6〜8 は
showcase、テスト、ドキュメントを扱います。

### Unit 1：heads-up 通知のツリーの実測

API 33、34、35 のエミュレーターで heads-up 通知の階層を実測し、結果を本項目に記録します。Unit 2 は
この結果を手がかりにするので、実測を最初に行います。記録する事実は次の7つです。

- heads-up 通知を収める SystemUI のウィンドウはどれか。
- 通知の行とその枠を示す resource id は何か。
- 同じ行を描く展開済みの通知シェードと、heads-up 通知をどう見分けるか。
- 行の中心から上へ、Unit 4 が計算する距離だけスワイプすると、通知が消えるか。
- 通知のタイトルと本文を持つ resource id は何か。
- resident channel で階層を1回読み取るのに、どれだけ時間がかかるか。
- 消去のスワイプから通知バナーが消えるまで、読み取り1回を含めてどれだけ時間がかかるか。

BE-0416 の Unit 1 は iOS で同じことを行い、その実測が後続のすべての単位を形作りました。推測した識別子では、
Unit 2 が誤ったノードに一致するか、どのノードにも一致しなくなります。

### Unit 2：resident のダンプから読む枠

`narrowed_root` は SystemUI のウィンドウを引き続き捨てるので、アプリの要素は変わりません。捨てる前に、
Unit 1 が特定した heads-up の行の枠を取り出し、絞り込んだツリーと一緒に返します。adb ドライバーは、
読み取ったツリーと一緒にその枠を保持します。同じ取り出しの処理で、
行のテキストのノード（Unit 1 が測った resource id）から通知のタイトルと本文も取り出します。
ドライバーは、1回の読み取りから枠とテキストの両方を保持します。

`notification_banner_frame` は、ドライバーが持つツリーが現行である間は、そのツリーから答えます。
ツリーが現行かどうかは `adb_driver.py` の `_tree_current` が示し、操作を行うたびに無効になります。
現行でなければ、新たに読み取ります。オーケストレーターのガードは、それでも通知バナーが消えるのを
見届けられます。ガードのスワイプは操作なので、スワイプの後のポーリングはすべて新たな読み取りに
なるからです。resident channel では、読み取り1回が往復1回分（400〜600 ms）かかります。常に新たに
読み取ると、Android のステップごとに約0.5秒が加わります。現行のツリーから答えれば、前回の読み取り
以降に操作がないとき、この費用を避けられます。この選択の代償は、操作がない間に現れた通知バナーが、
次の読み取りまで見えないことです。

Unit 5 のドライバー内の点検は、枠を読み取りのたびに追加の費用なしで得られます。読み取りはもともと
SystemUI のウィンドウを含み、取り出しの処理は `narrowed_root` がすでにたどるウィンドウだけを見るからです。
新しいエンドポイントも、resident server の Kotlin のコードへの変更も要りません。

フォールバックの `uiautomator dump` はアクティブなウィンドウだけを読むので、heads-up ウィンドウは
見えません。この経路では `notification_banner_frame` は `None` を返し、ドライバーはリースごとに一度だけ
警告をログに出します。警告は、resident channel が戻るまで通知バナーのガードが効かないことを伝えます。
フォールバックはもともと縮退して動くことを警告しており、新しい警告はその影響をもう1つ伝えます。
ドライバーは、フォールバックで読んだ事実を実行の `manifest.json` にも記録します。これにより、
Unit 6 の確認は、ガードが効かなかった実行を見分けられます。
`handleNotificationBanner` はどちらの経路でも宣言したままにします。リースの途中で宣言が変わると、
静的な対応表が崩れるからです。この対応表は `docs/platform-parity.md` にあり、
`tests/test_platform_parity.py` が検証しています。

この単位では、`bajutsu/common/drivers/adb/adb_driver.py` の `notification_banner_frame` に付いたコメント
も直します。heads-up 通知がアプリ自身のツリーに届く、という記述は誤りです。

### Unit 3：ケーパビリティ

`AdbDriver.CAPABILITIES` に `Capability.HANDLE_NOTIFICATION_BANNER` を加えます。これで、オーケストレーター
のステップごとの点検と `visual` の撮影前の点検が、オーケストレーターに Android 固有の分岐を加えずに Android でも動きます。

### Unit 4：端末の単位に合わせたスワイプ距離

`bajutsu/common/drivers/base/_functions.py` の `notification_banner_swipe_points` は、通知バナーの上端より
20 単位上でスワイプを終え、画面の上端から 8 単位より近づけません。BE-0416 はどちらの距離も iOS の point
で測りました。adb ドライバーは生のディスプレイの pixel で動くので、高密度の画面では 20 pixel のフリックは
短すぎて何も消せません。

この関数に `scale` 引数を加えます。1 point あたりの端末の単位数を表し、デフォルト値は `1.0` です。
`bajutsu/common/drivers/base/` には、`ViewportProvider` と同じ形の狭い Protocol を新設し、バックエンドが
この倍率を報告できるようにします。adb ドライバーは `wm density` を 160 で割った値を報告します。これは
密度非依存ピクセル（dp）1 つあたりの pixel 数です。値は `wm size` と同じく、実行の間キャッシュします。
iOS の point と Android の dp はほぼ同じ物理的な長さを表すので、BE-0416 が測った距離をそのまま使えます。
オーケストレーターは報告された倍率を渡し、Protocol を持たないバックエンドには `1.0` を渡します。
iOS の振る舞いは以前とまったく同じです。

### Unit 5：操作を奪う通知バナーの操作前の消去

iOS では、操作を妨げる通知バナーを XCUITest の割り込み監視が捕まえます。Android にはそのような監視が
ありません。オーケストレーターのステップごとの点検は、ステップの操作の前ではなく後に、ポーリング間隔ごとに高々1回動きます。
そのため、前のステップの点検の後に現れた通知バナーは、このステップのタップを奪います。

adb ドライバーは、操作対象を解決した後に、同じ読み取りに含まれる heads-up の枠を確かめます。
解決する箇所は、resident の経路では `_device_act` の解決ループ、座標の経路では
`_resolve_frame_and_screen` です。その枠が、これから触れる操作点を含むなら、ドライバーは先に
通知バナーを消します。Unit 4 の点でスワイプし、通知バナーが消えるまでポーリングします。
通知バナーの下で画面が動いた可能性があるので、その後に操作対象を解決し直します。
枠はツリーと一緒に届くので、この点検は読み取りを増やしません。

この点検は、座標の操作にも及びます。対象は、座標を直接指定した `swipe` と `drag` です。
これらはセレクターを解決せずに `driver.swipe` を呼びます（`gestures.py` の座標の形式）。ドライバーは、
操作の開始点を同じ方法で枠と照らします。通知バナーを消すためにドライバー自身が行うスワイプは、
この点検を通りません。通知バナーを意図して狙う操作だからです。

ドライバーの消去待ちには、専用の名前付きの上限を設けます。オーケストレーターの
`_BANNER_CLEARANCE_TIMEOUT`（2秒）は使い回しません。この値は、そこで通知バナーが残っても失うのは
スクリーンショット1枚なので、短く設計されています。操作の前に通知バナーが残ると、ステップが
失敗します。このバックエンドでは、不在の確認1回が階層の読み取り1回に当たります（`AdbDriver.query`
の往復1回あたり 400〜600 ms）。2秒では、ポーリングが3〜4回しかできません。Unit 1 がエミュレーターの
レーンで、消去のアニメーションと読み取り1回の時間を測り、その実測に余裕を加えて上限を決めます。

まだ滑り込んでいる途中の heads-up 通知は、枠が定まっていません。`notification_banner_swipe_points` は
この枠に `None` を返します。そのときドライバーは同じ上限の中で読み直し、枠が定まって
からスワイプします。ドライバーは、通知バナーが操作点を覆ったままでは操作を進めません。この点が、
警告をログに出して先へ進むオーケストレーターの点検とは異なります。点検が守るのはスクリーンショット
であり、通知バナーが残っても失うのは画像1枚です。ここで通知バナーが残ると、タップが奪われ、通知元の
アプリが開きます。待ちが上限に達しても通知バナーが操作点を覆っていれば、枠が定まったかどうかに
かかわらず、ドライバーは新しい独立した例外 `NotificationBannerBlocking` を送出します。
この例外は `bajutsu/common/drivers/base/` に定義します。ステップは、意図しない画面に移った後で
失敗するのではなく、通知バナーを名指しした理由をレポートに残して失敗します。

この型は、意図して `ElementNotTappable` のサブクラスにしません。`_tap_with_recovery`
（`bajutsu/common/orchestrator/actions/handlers/gestures.py`）は `ElementNotTappable` を捕まえ、
上、下の順に上限付きのスクロールで応じます。SystemUI の heads-up ウィンドウは画面に固定されているので、
スクロールでは消せず、テスト対象のアプリをスクロールするだけです。回復の途中で通知バナーが自動で
消えれば、やり直したタップが、シナリオがスクロールを求めていない画面で成功してしまいます。型を分ければ、
スクロールによる回復はこの失敗を目にしません。

操作点を覆わない通知バナーは、オーケストレーターの点検に任せます。操作のたびにすべての通知バナーを
消すと、何も妨げていない通知バナーにまでスワイプと条件待ちを費やすことになります。
採用した点検は、通知バナーが操作点を覆うすべての操作を対象とし、それ以外は対象としません。

消去はどれもレポートに届く必要があります。`bajutsu/common/drivers/base/` に狭い Protocol をもう1つ
新設し、メソッドは `drain_banner_dismissals()` だけにします。このメソッドは、前回の呼び出し以降に
ドライバーが消した通知バナーの通知テキストを返します。このテキストは Unit 2 が取り出したものです。
そのため、ドライバーによる消去の `AlertEvent` の label は通知自身のテキストになり、
`bajutsu/common/orchestrator/types/alert_event.py` にある `AlertEvent` の契約に一致します。
オーケストレーターは各項目を、種別 `notificationBanner` の `AlertEvent` として記録します。
iOS が残すのと同じ記録です。

オーケストレーターは、この Protocol を `InterruptionPolicyTarget` とは独立に確かめます。
`bajutsu/common/orchestrator/types/_functions.py` の `drain_interruptions` は、`InterruptionPolicyTarget`
ではないドライバーに空の結果を返します。新しい取り出しは、この条件の内側には置けません。
adb ドライバーは `InterruptionPolicyTarget` を実装しません。この Protocol は、
XCUITest だけが適用するボタンのポリシーも運ぶからです。取り出しは `active_driver` ではなく
`cfg.driver`（ネイティブのドライバー）から、同じステップごとの時点で行います。これにより、
`web` ブロックのステップ中の消去が、ネイティブのドライバーの保管場所に取り残されません。
`_step_runner.py` は、点検自身の操作にすでに同じ規則を適用しています。

オーケストレーター自身の点検も、消去を記録するようにします。現在の `_clear_notification_banner` は
操作の記録にスワイプを残すだけなので、点検が消した通知バナーには `notificationBanner` の記録が
残りません。この関数は、スワイプで消えたことを確かめたときに、種別 `notificationBanner` の
`AlertEvent` を返すようにします。点検は通知のテキストを読まないので、label は空です。この label により、
レポートでは2つの経路を見分けられます。呼び出し元は、
すでにアラートを保持している場所へこの記録を加えます。

- ステップごとの点検は、そのステップの `outcome.alerts` に加えます。
- ステップの `visual` の撮影前の点検も、同じステップの `outcome.alerts` に加えます。
- `expect` の `visual` の撮影前の点検は、`RunResult.expect_alerts` に加えます。

この変更は iOS にも及びます。点検が通知バナーを消した iOS の実行にも、割り込み監視が残すのと同じ記録が
加わります。これで、どちらのプラットフォームでも、どちらの経路でも、消去はすべて同じ種別の記録として
レポートに届きます。

### Unit 6：showcase の fixture と端末上での検証

Android の showcase アプリの両方（Views 版と Compose 版）に「Post notification」ボタンを加えます。
ボタンは重要度の高いチャネルでローカル通知を出し、Android はこの通知を heads-up 通知として表示します。
どちらのアプリも `POST_NOTIFICATIONS` をすでに持っています。

`demos/showcase/scenarios/notification_banner_android.yaml` を加えます。シナリオはボタンをタップし、
続いて Unit 1 が測った heads-up の枠の内側にあるコントロールをタップします。シナリオは、2回目の
タップがコントロールに届いたことを確かめます。

タップだけでは、通知バナーが現れたことを証明できません。通知が表示されなかった実行も、タップだけで
合格し、何も検証しないまま終わります。実行の `AlertEvent` を読むシナリオのアサーションはないので、
証明はシナリオの後で行います。新しいスクリプト `scripts/assert_notification_banner_evidence.py` は、
終わった実行の `manifest.json` を読みます。シナリオに種別 `notificationBanner` の消去がなければ
失敗します。`scripts/assert_app_crash_evidence.py` も、同じ方法でクラッシュの証拠を確かめています。
resident channel が使える間は、Unit 5 によって、この確認は決定的になります。2つの経路のどちらも
消去を記録するので、現れた通知バナーは、どちらの経路で消えても記録を残します。

この確認は、resident channel が使えることを前提にします。ドライバーが `uiautomator dump` の
フォールバックで読んだ実行では、manifest に Unit 2 の印が残ります。スクリプトはその原因を名指しして
失敗します（「resident channel が使えず、通知バナーのガードが効かなかった」）。消去の欠落とは報告しません。
ガードを確かめられなかった実行は、合格しません。

Android エミュレーターのレーンは、シナリオを実行した後にこのスクリプトを実行します。このレーンは
[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md) が加えたものです。
端末を使わないゲートでは、本物の heads-up 通知がスワイプで消えることを証明できません。

### Unit 7：テスト

Unit 1 で記録したダンプの fixture を使い、高速な Python のテストスイートでホスト側を検証します。

- `narrowed_root` が heads-up の枠を取り出し、なおかつ SystemUI のウィンドウをすべて捨てること。
- 同じ行を表示した展開済みのシェードからは、枠が出てこないこと。
- フォールバックの経路が `None` を返し、リースごとに一度だけ警告すること。
- 取り出しが、枠と一緒に通知のテキストも返すこと。
- `notification_banner_swipe_points` が2つの距離を `scale` で伸縮すること。
- `notification_banner_frame` が、現行のツリーからは読み取りなしで答え、操作の後は新たに読み取ること。
- adb ドライバーが、操作点を覆う通知バナーをタップの前に消し、覆わない通知バナーは残し、スワイプの後に
  操作対象を解決し直すこと。
- 開始点が通知バナーの下にある座標のスワイプが、先に通知バナーを消すこと。消去のためのスワイプ自体は、
  再び点検されないこと。
- 滑り込み途中の通知バナーは、枠が定まってからスワイプされること。待ちの上限までに枠が定まらないか
  消えない通知バナーは、通知バナーを名指しした `NotificationBannerBlocking` で操作を失敗させること。
  `_tap_with_recovery` がこの例外ではスクロールしないこと。
- ドライバーによる消去のそれぞれが、通知のテキストを label に持つ、種別 `notificationBanner` の
  `AlertEvent` として取り出されること。
- `InterruptionPolicyTarget` ではないドライバーの消去も、`web` ブロックのステップ中を含め、
  ステップのアラートに届くこと。
- オーケストレーターの3つの点検箇所が、確かめたスワイプには種別 `notificationBanner` の
  `AlertEvent` を記録し、確かめられなかったスワイプには記録しないこと。
- `assert_notification_banner_evidence.py` が、記録を含む manifest では合格し、含まない manifest では
  失敗すること。また、フォールバックの印を持つ manifest では、resident channel を原因として失敗すること。

`tests/test_adb.py` の `test_notification_banner_frame_is_none` は、これらのテストに置き換えます。

### Unit 8：ドキュメント

`docs/platform-parity.md` と `docs/drivers.md` のケーパビリティ表で、Android の
`handleNotificationBanner` の印を切り替えます。`docs/ja/` の対訳も合わせて直します。
heads-up 通知がツリーに現れるとする対応表の注記を書き換え、`docs/drivers.md` の行から「iOS only」を外します。
`docs/architecture.md` とその日本語版の能動的な点検の説明も直します。`HANDLE_NOTIFICATION_BANNER`
を宣言するのは XCUITest だけという記述を外し、点検が種別 `notificationBanner` の `AlertEvent`
を記録することと、adb ドライバーが操作の前に通知バナーを消すことを書き加えます。
`tests/test_platform_parity.py` は、2つの表が新しい宣言と一致するまで失敗します。`docs/scenarios.md`
とその日本語版が通知バナーのガードを説明している箇所には、Android での振る舞いとフォールバック時の
制約を書き加えます。

### 維持される prime directive

- **AI は判定しません。** 通知バナーの識別は Unit 1 が測った resource id の比較であり、消去はスワイプです。本項目は
  モデルの呼び出しを追加しません。
- **決定論が最優先です。** ドライバーは、通知バナーが不在であることを上限付きでポーリングして、消去を
  確かめます。通知自身のタイムアウトを待つステップはありません。
- **アプリに依存しません。** 取り出しはどのアプリでも同じ SystemUI 自身の識別子を手がかりにします。
  アプリ固有のコードも設定も追加しません。

## 検討した代替案

| 代替案 | 採らなかった理由 |
|---|---|
| `UiAutomation.getWindows()` で heads-up ウィンドウを読む、resident の専用エンドポイントを設ける | iOS の `/notificationBanner/query` と同じ形になりますが、Kotlin のコードとサーバーのテストが増え、点検のたびに往復が1回増えます。ドライバーがすでに行う読み取りが、同じウィンドウを運んでいます。フォールバックの経路が見えないことは、どちらでも変わりません。 |
| `dumpsys`（`HeadsUpManager` の状態）で通知バナーを検出し、枠は画面上部の固定領域とみなす | どちらの読み取り経路でも動きますが、枠が実測ではなく推測になり、prime directive 2 に反します。 |
| フォールバックの経路で読んでいる間は、`handleNotificationBanner` を宣言から外す | 宣言と実態は一致しますが、リースの途中で宣言が変わります。静的な対応表とそのテストは、宣言が固定であることを前提にしています。 |
| スワイプの点をドライバーごとに計算させる | プラットフォームごとに距離を調整できますが、`Driver` インタフェースが広がり、iOS ドライバーも変わります。1つの関数を密度で伸縮すれば、BE-0416 が測った距離を使い回せます。 |
| オーケストレーターのステップごとの点検だけに頼る | 変更は最小ですが、点検はステップの操作の後に動きます。前のステップの点検の後に現れた通知バナーは、このステップのタップを奪います。 |
| 操作点を覆うかどうかにかかわらず、操作のたびに通知バナーを消す | 規則は単純ですが、何も妨げていない通知バナーにまで、操作ごとにスワイプと条件待ちを費やします。採用した設計は、座標の指定を含め、通知バナーが操作点を覆うすべての操作を対象にします。 |
| adb ドライバーに `InterruptionPolicyTarget` を実装して消去を報告する | 新しい型は要りませんが、`set_interruption_policy` が空のメソッドになります。この Protocol は XCUITest だけが必要とする前提で作られており、オーケストレーターもその前提で分岐しています。 |
| 消去を操作の記録に、ただのスワイプとして残す | 変更は最小ですが、レポートには理由のわからないスワイプが並びます。通知バナーが現れたことを、読み手は知るすべがありません。現在のオーケストレーターの点検はまさにこの記録を残しており、Unit 5 がそれを改めます。 |
| 通知バナーの出現をアプリ側の合図で証明し、タップだけをアサートする | BE-0416 の iOS のシナリオはこの方法で、ホスト側の変更は要りません。ただし合図が証明するのは、アプリが通知を出したことです。SystemUI が表示したことも、Bajutsu が消したことも証明しません。 |
| 実行の `AlertEvent` を読むシナリオのアサーション種別を加える | 証明をシナリオの中に置けますが、このテスト基盤だけが必要とする事実のために、シナリオのスキーマが広がります。シナリオは通知バナーを観測できないので、このアサーションを必要とするシナリオの作者はいません。 |
| テスト用の端末で heads-up 通知を無効にする（`settings put global heads_up_notifications_enabled 0`） | 単純ですが、実際の利用者が目にする振る舞いを扱わずに隠すことになります。 |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] Unit 1：API 33〜35 で heads-up 通知のツリー、読み取りの費用、消去の所要時間を実測する
- [ ] Unit 2：resident のダンプから枠とテキストを取り出し、フォールバックの経路では警告して manifest に印を残す
- [ ] Unit 3：adb ドライバーで `handleNotificationBanner` を宣言する
- [ ] Unit 4：スワイプ距離をディスプレイの密度で伸縮する
- [ ] Unit 5：操作点を覆う通知バナーを、座標の操作を含めて操作の前に消し、オーケストレーターの点検を含むすべての消去を記録する
- [ ] Unit 6：Android の両アプリの showcase ボタン、通知バナーのシナリオ、manifest の確認
- [ ] Unit 7：heads-up のダンプの fixture を使うユニットテスト
- [ ] Unit 8：ドキュメント（英語版と日本語版）

## 参考

- [Issue #2396](https://github.com/bajutsu-e2e/bajutsu/issues/2396)：本項目が埋める対応差。
- [BE-0416](../BE-0416-ios-notification-banner-swipe-dismiss/BE-0416-ios-notification-banner-swipe-dismiss-ja.md)：
  iOS の通知バナーのガード、実測したスワイプ距離、レポートの `notificationBanner` の記録。
- [`docs/ja/platform-parity.md`](../../docs/ja/platform-parity.md)：本項目が更新する対応表。
- [`BajutsuAndroidUIAutomatorServer/README.ja.md`](../../BajutsuAndroidUIAutomatorServer/README.ja.md)：
  resident のダンプが SystemUI のウィンドウを含む理由と、ホストがそれを捨てる理由。

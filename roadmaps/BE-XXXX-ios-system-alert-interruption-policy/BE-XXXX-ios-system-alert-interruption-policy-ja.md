[English](BE-XXXX-ios-system-alert-interruption-policy.md) · **日本語**

# BE-XXXX — 割り込んできたシステムアラートに答えるのはシナリオの方針だけにする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-ios-system-alert-interruption-policy-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | [#1808](https://github.com/bajutsu-e2e/bajutsu/pull/1808) |
| トピック | Platform support |
| 関連 | [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md), [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md), [BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules-ja.md), [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Bajutsu の反応的なシステムアラートガードは、アプリ自身のアクセシビリティツリーからは見えない OS の
プロンプトを片付けます。片付け方は、[シナリオ](../../docs/ja/glossary.md#シナリオのオーサリング)が
定めた方針に沿って、プロンプトのボタンのどれかをタップすることです。iOS では、こうしたプロンプトが
同時に 2 つ画面に出ることがあります。サインインのあとに iOS が出す「パスワードを保存」のアラートと、
通知の認可要求です。

iOS 18.6、26.3、26.4、26.5 の Simulator で実測したところ、2 つが同時に出た状況ではガードの動作が
決定的でなくなりました。その原因は、Bajutsu 自身の方針にはありません。XCUITest は、自分の操作に
割り込んできたアラートを、その操作を合成する**前に**解決します。何も入れていなければ、アラート自身の
デフォルトボタンで答えてしまいます。

本提案はその判断を取り戻します。runner は、シナリオの `systemAlertHandling` が名指ししたボタンを押す
割り込み監視を入れます。ラベルはオーケストレータ側で解決して runner へ渡すので、方針の置き場所は 1 つ
のままで、runner は適用するだけです。押した結果は報告されるので、この経路での消去が黙って起きることも
なくなります。あわせて待機中のゲートは、システムアラートが出ていないことをそのポーリングで確かめて
いない限り、ツリー内の消去タップを撃たなくなります。これで 2 つのプロンプトに答える順序が決まります。
まずシステム側のアラートをシナリオの方針で答え、そのあとでアプリ側のパスワード保存アラートを片付け
ます。

## 動機

2 つのプロンプトは同じ面には住んでおらず、システムアラートなのは片方だけです。アプリ内ブラウザで
サインインするアプリに対して実測すると、パスワード保存アラートは iOS が**アプリ自身のプロセス**へ
出し、通知の要求は別プロセスの SpringBoard のアラートでした。

```
springboard buttons = ["Don't Allow", "Allow"]
app.alerts          = "Save Password" / "Never for This Website" / "Not Now"
snapshot button id='' label='Not Now' frame=(61.7, 517.0, 270.0, 44.0)
```

この住み分けが、どちらのプロンプトをどのコードが処理するかを決めます。
[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md) の
ネイティブ経路は `system_alert_labels()` を通じて `springboard.alerts` を読みます。そのためパスワード
保存アラートは、この経路には永久に `"absent"` としか見えません。これを片付ける経路は
`bajutsu/orchestrator/waits.py` の `_dismiss_from_tree` ただ 1 つです。ポーリングが取得したツリーから
識別子を持たないラベル付きのボタンを見つけ、通常の `Driver.tap` でタップします。

XCUITest が介入するのは、そのタップの地点です。runner の活動ログには一部始終が残っていました。

```
t = 8.11s Tap "Not Now" Button
t = 8.14s     Check for interrupting elements affecting "Not Now" Button
t = 8.17s     Found 1 interrupting element:
t = 8.19s     Invoking UI interruption monitors for "…Would Like to Send You Notifications" Alert
t = 8.29s Default interruption handler attempting to dismiss alert by tapping "Allow" Button.
t = 9.92s Confirmed successful handling of interrupting element
t = 9.92s     Synthesize event
```

組み込みハンドラが出す答えは、ガードが適用しようとしている方針の逆です。
`bajutsu/orchestrator/types.py` の `DEFAULT_DISMISSIVE_LABELS` は "Don't Allow" を先頭に置いています。
シナリオが何も言っていない権限の要求を、許可ではなく拒否で終わらせるためです。
[BE-0382](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules-ja.md) は、
通知の要求に `choice: deny` と書けるようにしました。それでも XCUITest が先に答えるので、`deny` と
書いたシナリオが権限を許可した状態で終わります。ステップは失敗せず、警告も出ず、`AlertEvent` も
レポートに届きません。権限が許可されたという事実は、実行結果のどこにも残らないのです。

この許可は常時ではなく散発的に起こるので、不安定さとして現れます。`_observe_native` が SpringBoard を
調べるのは `guard.poll_interval`（デフォルトで 1 秒）に 1 回までです。ところが `_dismiss_from_tree` を
呼ぶのは `_POLL`（0.05 秒）ごとでした。つまり 20 回のポーリングのうち 19 回は、システムアラートの
有無を把握しないままアプリへのタップを撃っていたことになります。

設計を決めたのは、次の実測です。**割り込みを断るという答えは選べません。** 何も押さずにアラートを
引き取る監視は、確かに組み込みハンドラを抑止します。しかし XCUITest は割り込みが解消したかを検証し、
アラートが残っていれば次の操作でまた監視を呼びます。常駐 runner はクエリを撃ち続けますし、割り込み
チェックはタップだけでなくアプリ全体のクエリでも走ります。結果は無限ループで、実測したすべての試行で
runner が実行中に落ちました。監視を外した対照実行では生き残ったので、原因はここで確定です。監視は
実際に答えるほかなく、残る問いはどのボタンを押すかだけです。

変更が届いたかどうかは、あとから読む人にも確かめられます。iOS のパスワード保存アラートが出ている
状態で、通知の要求に `deny` で答えるシナリオを走らせます。そのうえで、アプリ自身が公開している認可の
状態を検証します。変更前は `authorized` に落ち着き、変更後は `denied` に落ち着きます。本提案が追加
する showcase のシナリオはまさにその検証をし、4 つの iOS バージョンで green を実測しました。

## 詳細設計

### ユニット 1 — 割り込んできたアラートにシナリオの方針で答える

runner に UI 割り込み監視を 1 つ入れます。アラートのボタンのラベルを読み、`InterruptionPolicy` に
沿って 1 つを選び、押し、そのラベルを記録して `true` を返します。選び方は `match_alert_rule` と
`pick_alert_label` の小さな移植です。規則の同定ラベルがそれぞれちょうど 1 回ずつ載っていれば、その
規則が勝ちます。そうでなければ、ちょうど 1 回だけ載っている最初の候補が勝ちます。

そこに入るラベルはすべて、シナリオの `systemAlertHandling` からオーケストレータ側で解決し、新設の
`POST /interruptionPolicy` で渡します。判断の置き場所は 1 つのままで、runner は適用するだけです。

方針はリースごとではなくシナリオごとに渡します。常駐 runner はシナリオより長生きするからです。ガードを
無効にしたシナリオは、呼び出しを飛ばすのではなく**空の方針**を渡すので、前のシナリオの答えを引き継ぎ
ません。`POST /interruptionPolicy/drain` は前回以降に押したラベルを返し、実行ループがそれをステップの
`AlertEvent` に畳み込みます。この経路での消去が黙って起きなくなるのは、この報告があるからです。

方針がどのボタンも名指ししないアラートには、断る道が残ります。監視は `false` を返し、XCUITest 自身の
ハンドラが引き取ります。この監視が無かったときと同じ挙動です。そしてループしない唯一の結末でも
あります。そのハンドラは実際にアラートを消すからです。

### ユニット 2 — アラートがないと確かめた直後のポーリングだけでツリー内消去を走らせる

`_observe_native` にローカルなフラグを 1 つ足します。そのポーリングでネイティブ探査が実際に走り、
なおかつ `"absent"` を返したかどうかを記録するフラグです。`_dismiss_from_tree` を呼ぶのは、そのフラグ
が立っているときだけにします。これでツリー内消去は `_POLL` ではなく `poll_interval` の間隔で動きます。
そのタップはいずれも、同じポーリングのなかで何も見つけなかった SpringBoard のクエリを直前に伴います。
2 つのプロンプトが同時に出たときの順序が決定的になるのは、この対応づけによります。

### ユニット 3 — タップ不能時の打ち切り上限をポーリング数ではなく秒で定める

`_TREE_DISMISS_MAX_DECLINES` は、連続した `ElementNotTappable` の回数を縛る上限でした。その値 20 は
「`_POLL` で約 1 秒」と導出されています。ユニット 2 は、この上限が数えている間隔を変えます。しかも
`poll_interval` はシナリオ、ターゲット、フラグごとに設定できる
（[BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config-ja.md)）ので、
どんな回数に置き換えても一定の時間には対応しません。そこで上限を時計に基づくものへ変えます。隣にある
`_TREE_RETAP_DELAY` は、同じ理由ですでにその選択をしています。

### ユニット 4 — `gone` の待ちをガード対象にする

`gone` の待ちはガード対象外でした。理由は「妨げるプロンプトはツリーを潰し、潰れたツリーはすでに
`gone` を満たす」というものです。これは SpringBoard のプロンプトにだけ当てはまります。アプリ自身の
プロセスに描かれるプロンプトはツリーを潰さず、逆にそのボタンをツリーへ**足します**。そのため、その
ボタンを対象にした `gone` の待ちは、片付ける者がいないままタイムアウトまで居座ります。実機では、
パスワード保存アラートが 60 秒の待ちを丸ごと生き延びました。この分岐も `for` と同じようにゲートへ
ツリーを渡します。渡すのは自身の条件判定のあとなので、すでに満たされた待ちが操作を起こすことは
ありません。

### ユニット 5 — ブラウザと統合したツリーで、アプリ側のアラートを 1 回だけ報告する

`SFSafariViewController` が出ているあいだ、`queryElements` はアプリの走査とブラウザサービスの走査を
連結します
（[BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md)）。
アプリがブラウザの**上に**出したアラートはアプリが描き、サービス側のツリーにも映るので、ボタンが 2 回
ずつ報告されていました。識別子の接頭辞による刈り込みでは届きません。このアラートはブラウザのクロム
ではないからです。

こうした重複は、曖昧さではなく 1 つのコントロールが 2 回見えている状態です。しかしツリーの側でも
そう言わなければなりません。放置すると対が `resolve_unique` を通らず、ツリー内消去は当然のように
推測を拒み、アラートは永久に消えません。マージは、アプリ側がすでに報告した要素と、報告されている
同一性がフレームまで一致するサービス側のノードを落とすようにします。

### ユニット 6 — showcase で本物のプロンプトを出す

showcase のアプリ内ブラウザが、既存のブラウザ用フィクスチャの隣に置いたサインインページを読み込み
ます。これを送信すると iOS が本物の「パスワードを保存」アラートを出します。ブラウザ経路が必要とするものは
ありません。associated domain、entitlement、Hypertext Transfer Protocol Secure（HTTPS）のサーバの
いずれも不要です。iOS は
Safari と同じく、web フォームのオリジンに対して資格情報の保存を申し出るからです。アプリ自身の
ログイン画面は iOS がこのアラートを出すもう 1 つの経路ですが、実測ではその 3 つすべてを必要とします。
このレーンの常時の前提条件にはしたくないので、後続の課題として残します。

通知の要求は、ブラウザが開いたあとにアプリが遅延で上げます（`SHOWCASE_NOTIF_AFTER_BROWSER`）。重なった
状態へシナリオがタップで持ち込むことはできないからです。システムアラートが出ている最中の要素タップは、
監視が先に答えてしまいます。そのうえでシナリオは、順序ではなく結果を待ちます。順序はバージョンで動く
からです。実測では、通知の要求は iOS 26.4 と 26.5 ではサインインの入力中に届き、18.6 と 26.3 では入力
のあとに届きました。26.4 では保存アラート自体が、ページの遷移よりあとに届きました。

### ユニット 7 — オーケストレータ側を決定的なテストで覆う

fake アクチュエータで、オーケストレータが渡す方針を覆います。ガード自身の規則と候補、シナリオが何も
名指ししないときの無害なデフォルト、ガードを無効にしたときの空の方針、そしてオプトインを持たないバックエンド
では何も渡さないことの 4 点です。あわせて次の 4 点も覆います。drain が `AlertEvent` になること。ツリー内消去のゲートが 3 つの場合
それぞれで働くこと。打ち切りが時計に基づくこと。`gone` の待ちがアプリ側のプロンプトを片付けること。runner 側は Swift でこのゲートの外に着地するので、ユニット 6 のシナリオが
検証を担います。

## 検討した代替案

**監視には断らせ、アラートはガードのネイティブ経路に任せる。** 最初の実装がこれで、誤りでした。
XCUITest は引き取ったと申告された割り込みが解消したかを検証し、アラートが残っていれば以後の操作の
たびに監視を呼び直します。実測では runner が死ぬまでループしました。監視を外した対照実行は生き延びた
ので、原因はここです。監視は答えるほかありません。

**Python 側のゲートだけを変え、XCUITest のデフォルトハンドラは残す。** 採らない理由は、ゲートの効き目
が呼び出し側 1 か所にとどまるからです。シナリオが行う他のあらゆる要素へのタップでは、XCUITest が黙って
権限を許可する状態が残ります。実測でも、iOS 26.4 と 26.5 では通常の入力ステップに要求が割り込みました。

**アラートの文面から Swift 側でボタンを解決する。** 採らない理由は、ボタンの方針が 2 つの言語に分かれて
しまうからです。順序付きの `instruction` の候補、BE-0382 のプロンプトごとの `rules`、BE-0320 のロケール
別のラベル表は、いずれも Python にあります。解決済みのラベルを渡す形なら、判断の置き場所は 1 つのまま
で、runner には仕組みだけが残ります。

**新しい探査ではなく、直近のネイティブ状態を覚えてゲートにする。** 採らない理由は、保持した
`"absent"` が最大で `poll_interval` 分だけ古くなりうるからです。その間隔こそが、今回の欠陥が住んで
いる窓です。

**`_POLL` ごとに SpringBoard を探査し、ツリー内の間隔は変えない。** BE-0315 がすでに記録した実測を
根拠に採りません。ポーリングごとの SpringBoard のクエリは、runner の単一のメインスレッドにかかる負荷を
おおよそ 2 倍にします。

**ネイティブの `springboard.alerts` のクエリで保存アラートに手を伸ばす。** 実測を根拠に採りません。
このアラートはアプリのプロセスへ出るので、そこには決して現れません。

**ブラウザではなくアプリ自身のログイン画面から出す。** 却下ではなく先送りです。本当に別経路であり、
覆う価値もあります。実測では `webcredentials:` の associated domain が要り、entitlement の宣言
だけでは足りませんでした。`apple-app-site-association` を配る HTTPS のサーバと、Simulator のキーチェインに
入れた認証局が揃うまで、プロンプトはまったく出ませんでした。この仕掛けは、今日はビルド済みのアプリ
だけで足りているレーンにとって常時の前提条件になるので、独立した変更として扱います。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] ユニット 1 — オーケストレータが渡す方針で割り込みアラートに答え、結果を報告する
- [x] ユニット 2 — そのポーリングで走って `"absent"` を返したネイティブ探査を条件に `_dismiss_from_tree` を制限する
- [x] ユニット 3 — タップ不能時の打ち切り上限をポーリング数ではなく秒で定め直す
- [x] ユニット 4 — `gone` の待ちをガード対象にし、アプリ側のプロンプトを片付けられるようにする
- [x] ユニット 5 — ブラウザと統合したツリーで、アプリ側のアラートを 1 回だけ報告する
- [x] ユニット 6 — showcase で iOS 本物の保存アラートを出し、2 つのプロンプトに答える
- [x] ユニット 7 — オーケストレータ側を決定的なテストで覆う
- [ ] 後続 — ネイティブのログイン経路と、それが必要とする associated domain の仕掛け

## 参考

- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) — `_AlertGuardGate`。ネイティブ
  探査とツリー内消去、ツリーが潰れたことを手がかりにする経路の順序を決める `_observe_native` の置き場所
- [`bajutsu/orchestrator/types.py`](../../bajutsu/orchestrator/types.py) — `AlertGuardConfig` と
  `push_interruption_policy`、`drain_interruptions`、および `DEFAULT_DISMISSIVE_LABELS` の方針
- [`BajutsuKit/Sources/BajutsuRunner/InterruptionPolicy.swift`](../../BajutsuKit/Sources/BajutsuRunner/InterruptionPolicy.swift)
  — 渡された方針と、監視が適用する選び方
- [`BajutsuKit/Runner/Sources/RunnerUITest.swift`](../../BajutsuKit/Runner/Sources/RunnerUITest.swift)
  — 監視を入れる場所
- [BE-0269 — 待機の途中で介入する](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md)
  — ステップが失敗したあとではなく待機の途中でガードを動かす理由
- [BE-0315 — ネイティブのシステムアラート処理](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md)
  — ネイティブ経路と `poll_interval`、および本提案が再び払わずに済ませるポーリングごとのクエリの費用
- [BE-0382 — プロンプトごとの規則](../BE-0382-system-alert-per-prompt-rules/BE-0382-system-alert-per-prompt-rules-ja.md)
  — 黙った許可が上書きしてしまう、プロンプトごとの `choice`
- [Apple — About the Password AutoFill workflow](https://developer.apple.com/documentation/security/about-the-password-autofill-workflow)
  — 先送りにしたネイティブログイン経路の背景にある、associated domain の要件

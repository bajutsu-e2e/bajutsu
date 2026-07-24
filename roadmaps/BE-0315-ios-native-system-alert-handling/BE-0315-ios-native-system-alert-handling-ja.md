[English](BE-0315-ios-native-system-alert-handling.md) · **日本語**

# BE-0315 — リアクティブなアラートガードを、BE-0316 の SpringBoard 経路を再利用して決定論的・ネイティブにする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0315](BE-0315-ios-native-system-alert-handling-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0315") |
| 実装 PR | [#PLACEHOLDER](https://github.com/bajutsu-e2e/bajutsu/pull/PLACEHOLDER) |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config-ja.md), [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md), [BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md), [BE-0308](../BE-0308-alerts-guard-real-model-verification/BE-0308-alerts-guard-real-model-verification-ja.md), [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers-ja.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md) |
<!-- /BE-METADATA -->

> **実装中にマージされた BE-0316 との整合。** BE-0316 がネイティブの SpringBoard プリミティブを実装しました。`handleSystemAlert` *ステップ*、`handle_system_alert` ドライバメソッド、`/systemAlert/query` と `/systemAlert/tap` の runner ルート、`HANDLE_SYSTEM_ALERT` capability です。BE-0316 は `dismissAlerts` をリアクティブな vision guard のまま残すことを意図的に選び、「成功するシナリオはモデルを呼ばない」を保つために決定論的な機構をリアクティブにはしない、と記録しました。BE-0315 はまさにそのリアクティブな対の一方であり、その緊張を解消します。ネイティブの SpringBoard 照会は**モデル呼び出しではない**ため、この不変条件は保たれます。したがって本実装は、並立する API を追加するのではなく、BE-0316 のプリミティブを**再利用**します（既存の `/systemAlert/query` を読む薄い非ブロッキングの `system_alert_labels()` だけを足します）。その貢献は、自動的なリアクティブガード、決定論的なボタン方針、ポーリング間隔ノブ、そして vision の fallback への降格です。

## はじめに

Bajutsu は、オペレーティングシステムのアラート（「通知を許可」の権限ダイアログや App Tracking
Transparency（ATT）のように、アプリスコープのアクセシビリティツリーからは見えない SpringBoard の
プロンプト）を、Claude vision にどこを押すか尋ねる方法でしか閉じられません。本提案は、iOS の
XCUITest バックエンドに決定論的なネイティブ経路を代わりに設けます。SpringBoard プロセスのアラートを
照会してプロンプトが表示されているか否かと、そのボタンの構成とを確実に把握し、ボタンをその label で
指定して押して閉じます。ネイティブ経路はスクリーンショットもモデルへの往復も使わないため、vision
guard が費やす数秒ではなく、0.1 秒を大きく下回る時間でプロンプトを閉じます。さらに、大規模言語モデル
（LLM）を一切呼ばないため、認証情報がなければ何もしない vision guard とは異なり、`ANTHROPIC_API_KEY`
がなくても動作します。本提案の貢献は 2 つあります。runner はこれまで持ち得なかった「システムアラートが
表示されているか」という決定論的な信号を得て、実行時に頻出するプロンプトが AI 経路から外れ、ネイティブ
経路が名指しできない場合の fallback として vision が残ります。

## 動機

システムアラートの guard は AI vision の呼び出しであり、その遅延が表面化しています。SpringBoard の
プロンプトが wait の途中で現れると、[BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md)
のゲートはアプリの要素ツリーが崩れたことを検知して `SystemAlertGuard.dismiss`
（`bajutsu/agents/alerts.py`）を呼び出します。この処理はスクリーンショットを撮り、`claude-sonnet-5` に
送り、モデルが返した座標を押します。1 回の dismiss がモデルへの 1 往復に相当し、ゲートは 2 回まで発火し、
ステップ末尾の再試行がもう 1 回加わり、試行の間には cooldown が挟まります。そのため、ネイティブの
タップなら即座に閉じられるプロンプトが、画面上に数秒とどまり得ます。さらに悪いことに、guard は既定で
動きますが、認証情報に依存します。`ANTHROPIC_API_KEY` が設定されていなければ黙って何もせず、プロンプトは
wait のタイムアウトが丸ごと経過するまで実行を止めます。

ゲートが拠り所とする信号は、事実そのものではなく代理です。`shows_app_ui(elements)`
（`bajutsu/elements.py`）はアプリ自身の UI ツリーに操作可能な内容があるかを返し、ゲートはそこから
システムのオーバーレイが画面を塞いでいると推論します。推論は相関にすぎません。画面遷移中の一過性の
空フレームは、アラートが存在しないのに塞がっていると読まれ、識別可能なアプリ要素を画面に残す
オーバーレイは、実際にはプロンプトが出ているのに塞がっていないと読まれます。runner は「システム
アラートが表示されているか」に直接答えられたためしがありません。

guard を書いた当時、このギャップは避けられませんでした。しかし今は違います。BE-0269 は
バックエンドにアラートの有無を直接尋ねる案を検討し、退けています。idb のアクセシビリティ照会は前面の
アプリにスコープされていたため、SpringBoard のプロンプトは idb からは見えず、崩れたツリーという代理が
得られる最善の信号でした。その後
[BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md) が idb を
撤去し、XCUITest を iOS の唯一のバックエンドにしました。XCUITest は 2 つ目の
`XCUIApplication(bundleIdentifier: "com.apple.springboard")` を構築し、プロセス境界を越えて
`springboard.alerts` を照会できます。プロセス境界を越える照会こそ、idb に欠けていた能力です。BE-0269 が
前提とした条件はもはや成り立たず、決定論的な presence 信号が今日到達可能なのは、この前提が変わった
ためです。

決定論的な dismiss が必要になるのは、決定論的な事前抑止が届かない箇所です。
[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) は `simctl
privacy` を通じて権限の状態を事前設定するため、位置情報、写真、カメラ、マイク、連絡先、カレンダーの
プロンプトは現れません。列挙した権限は、オペレーティングシステムの Transparency, Consent, and
Control（TCC）データベースに支えられており、`simctl privacy` がそのデータベースに書き込みます。頻出する
プロンプトのうち 2 つは TCC の外にあり、BE-0276 の手が届きません。通知の許可は TCC のサービスではなく
（`simctl privacy` にその名前はなく、`bajutsu/simctl.py` はまさにこの理由で `notifications` を拒否
します）、ATT は `simctl` のトグルをそもそも持ちません。通知と ATT はどちらも実行時にはなお現れ、今日
それを閉じられるのは vision guard だけです。通知と ATT の 2 つのプロンプトこそ、ネイティブの dismiss が
扱うべき具体的な対象です。

頻出するアラート経路から LLM を取り除くことは、prime directive 1 に直接かないます。guard は実行を
進めるために存在し、実行を判定するためではないため、モデルの呼び出しをネイティブの照会と label 指定の
タップに置き換えても正しさは失われず、決定論性と速度、そして認証情報からの独立が得られます。

## 詳細設計

作業は、ネイティブの presence 照会、ネイティブの dismiss 操作、どのボタンを押すかの決定論的な方針、
ネイティブ経路を vision より優先する配線、そして実機での検証に分かれます。各ユニットはインターフェースの
水準ではバックエンド非依存で、XCUITest の実装においてのみ iOS 固有です。したがって prime directive 3
（アプリごと、プラットフォームごとの差異は driver インターフェースの背後にとどめる）が全体を通じて
保たれます。

> **実装（BE-0316 との整合後）。** 下記のユニット 1・2 は、BE-0316 が同じ SpringBoard の配管を実装する*前*の、presence 照会と dismiss 操作についての提案時の計画です。実装ではそれらを新設せず BE-0316 を再利用しました（冒頭の整合の但し書きを参照）。実際に入ったのは、BE-0316 の `/systemAlert/query` を読む薄い `Driver.system_alert_labels()`（ユニット 1）と、`HANDLE_SYSTEM_ALERT` capability のもとで BE-0316 の `handle_system_alert` を通じて tap するガード（ユニット 2）です。新しい runner ルート、dismiss 操作、capability トークンは追加していません。ユニット 3〜5 は記載どおり実装しました。

1. **`Driver` インターフェースを通じて公開する、決定論的なシステムアラートの presence 照会。**
   システムアラートが表示されているか、表示されているときはそのボタンの label を返す、バックエンド
   非依存の driver メソッドを追加します。*（実装: 新ルートを足す代わりに、`system_alert_labels()` が
   BE-0316 の既存の `/systemAlert/query`——BE-0316 がすでに保持する 2 つ目の
   `com.apple.springboard` の `XCUIApplication`——を読み、ボタンの label を返します。アラートが
   なければ `[]` です。）* この信号は事実を報告するだけで合否を決めないため、prime directive 1 から
   外れています。

2. **label 指定の決定論的な dismiss 操作。** 現在のシステムアラートを、名前で指定したボタンを押して
   閉じます。determinism first（prime directive 2）を保つため、指定された label をちょうど 1 つのボタンに
   解決し、0 個または複数一致したときは声高に失敗します（driver の `resolve_unique` /
   `AmbiguousSelector` の契約）。クエリが最初に解決したボタンを押す振る舞いにはしません。*（実装:
   ガードは `HANDLE_SYSTEM_ALERT` capability のもとで BE-0316 の `handle_system_alert`——
   `resolve_unique` で解決する label セレクタと `/systemAlert/tap`——を通じて tap するため、並立する
   dismiss ルートや capability トークンは追加していません。）*

3. **既存の `DismissAlerts.instruction` を決定論的なボタン方針へ発展させる。** `dismissAlerts` シナリオ
   フィールドには、押すボタンを名指しする能力がすでにあります。`DismissAlerts.instruction`
   （`bajutsu/scenario/models/scenario.py`）は `"tap Allow"` のような自由記述の文字列で、*vision* の locator が
   解釈します。したがってここでの作業は、ボタン選択をゼロから加えることではなく、既存のこのノブを、ネイティブ
   経路がモデルなしで解決する決定論的な形へ発展させることです。たとえば順に試す候補 label の並び（「Allow」、
   次に「OK」）を与えれば、権限を許可するシナリオと、それを閉じるシナリオの双方を引き続き表現できます。何も
   名指ししないときは、文書化された既定の方針を適用します。既存フィールドを名指しすることが、実装者に
   `instruction` を発展させ、その隣に 2 つ目の label フィールドを足して本ユニットが警告する余分な語彙を生む
   のを避けさせます。したがって突き合わせは 2 者ではなく 3 者です。既存の自由記述 `instruction`、本提案が示す
   決定論的な label の形、そしてマージ済みの interrupt-handlers 提案
   （[BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers-ja.md)）が予測しにくい
   タイミングの割り込み画面のために導入した別の `interrupts` フィールドです。実装するセッションは、この 3 つを
   1 つの文法に収束させ、3 つの語彙を並立させたり 4 つ目を増やしたりしないようにします。

4. **ネイティブの照会を独立した間隔でポーリングし、最初の陽性で dismiss し、vision は fallback として
   のみ残す。** バックエンドが presence と dismiss の capability を広告しているとき、BE-0269 の
   `_AlertGuardGate` はネイティブの presence 照会を独自の実時間間隔（デフォルト 1 秒）で発行し、wait の
   `_POLL` 条件周期から切り離します。そして、あるポーリングが表示中のアラートを見つけ、それが方針の名指し
   するボタンを備えていれば直ちに閉じます。適切な周期はヒューリスティックであり、検知レイテンシと runner の
   負荷とのトレードオフで、単一の値がすべてのアプリに合うわけではありません。そこで間隔はハードコードせず
   ノブとして公開します。デフォルトは 1 秒で、`dismissAlerts` 設定がすでに従う優先順位（flag > scenario >
   target > default、BE-0177）で上書きできます。毎 tick のネイティブ照会は意図的に避けます。XCUITest runner は
   単一の main thread で照会を処理し（`onMainCatching`）、SpringBoard は別プロセスなので、その照会は無料で
   取得済みアプリツリーを再利用する `shows_app_ui` 代理とは違って再利用できません。したがって 50ms ごとの
   アプリスナップショットに 2 本目の cross-process 照会を並べると、runner の負荷がおよそ倍になり、不安定化や
   クラッシュを招きかねません。間隔で区切れば、追加負荷は 1 間隔ごとの SpringBoard 照会 1 本に収まり、小さく
   有界な検知レイテンシ（最大でも 1 間隔）と引き換えに runner の安定を得ます。それでも、置き換える vision
   経路よりはるかに早くプロンプトを消せます。debounce は要りません。`springboard.alerts.firstMatch.exists`
   は崩れたツリーという代理の相関ではなく事実を返すため、一過性フレームの偽陽性がないからです。cooldown や
   max-attempts も要りません。固定間隔がすでに照会をレート制限しているからです。vision guard が動くのは、
   ネイティブ経路が行動できないときだけです。すなわち、バックエンドが capability を持たないか、阻害している
   面が列挙可能な `springboard.alerts` のアラートでないか、アラートが方針の名指しするボタンを備えないときです。
   したがって頻出する iOS のプロンプト（通知、ATT、方針が label を名指しする任意のアラート）は vision に
   届かず、想定外のプロンプトには安全網が残ります。capability を持たないバックエンドは今日の「崩れたツリーの
   代理と vision」の挙動をそのまま保つため、orchestrator はバックエンド非依存のままで、既存の経路は 1 つも
   退行しません。vision guard は今日でも pass/fail の判定パス上にありません（prime directive 1）。そのため
   fallback として残しても、結果の決定論性を損ないません。

5. **実機で検証する。** 実際の SpringBoard アラートに対するネイティブなタップは、Simulator を使わない
   ゲートでは証明できません。そのため XCUITest の実装を載せるユニットは、起動済みの Simulator 上で実際の
   通知プロンプトか ATT プロンプトを出し、presence 照会と label 指定の dismiss の双方を確かめる必要が
   あります。バックエンド非依存の配線（ユニット 3 と 4）は、driver の capability をスタブする実機不要の
   テストで覆います。

## 検討した代替案

- **XCUITest 組み込みの割り込みハンドラ `addUIInterruptionMonitor`。** 却下します。この監視は非決定論的な
  タイミング（テストハーネスが次にアプリへ操作を加えたときに限る）で発火するため、照会可能な presence
  信号を提供せず、wait のポーリング周期で駆動できません。明示的な `springboard.alerts` の照会は検査可能で
  決定論的であり、本提案が必要とする性質です。
- **vision のみの guard を保ち、BE-0269 のタイミングをさらに調整する。** 却下します。遅延の下限はモデルへの
  往復そのものであり、どんな cooldown や debounce の調整でも取り除けず、vision 経路は認証情報がなければ
  なお何もしません。ネイティブ経路は、遅延と認証情報への依存を切り詰めるのではなく、両方を取り除きます。
- **BE-0276 を広げて通知と ATT を事前設定する。** 却下します。通知の許可は TCC のサービスではないため
  `simctl privacy` では書き込めず、ATT は `simctl` のトグルをそもそも持ちません。どちらのプロンプトも
  事前には抑止できないため、実行時に反応して閉じる経路が避けられません。
- **ネイティブな要素タップではなく座標タップ（`tap_point`）を決め打ちのオフセットで再利用する。** 却下
  します。固定オフセットは端末のサイズ、ボタン配置、動的なテキストをまたぐと壊れ、座標を別の方法で選ぶ
  には本提案が取り除こうとしている vision の呼び出しが要ります。ネイティブの
  `springboard.alerts.buttons[label]` の要素は、位置によらずボタンをその label で解決するため、安定した
  プリミティブです。
- **ネイティブの照会を間隔ではなく wait の毎 tick でポーリングする。** 却下します。XCUITest runner は単一の
  main thread で照会を処理するため、毎 tick のアプリスナップショットに毎 tick の SpringBoard 照会を重ねると
  負荷がおよそ倍になり、runner の不安定化やクラッシュを招きかねません。得られるレイテンシの改善は高々
  1 間隔ぶんです。有界な間隔（デフォルト 1 秒）なら runner を安定に保ちつつ、置き換える vision 経路より
  はるかに早くプロンプトを消せます。無料の `shows_app_ui` の崩れ代理でネイティブ照会をゲートする（アプリツリーが塞がって見えるときだけ
  照会する）変種も検討しましたが、独立した間隔を選びました。代理の偽陰性の盲点、すなわちアプリ要素を画面に
  残すオーバーレイでは照会が一度も発火しない、という弱点を受け継がないためです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] ユニット 1 — 決定論的なシステムアラートの presence 照会。**BE-0316 から再利用**: 新ルートではなく、
      薄い非ブロッキングの `Driver.system_alert_labels()` が BE-0316 の既存の `/systemAlert/query`
      （BE-0316 がすでに保持する 2 つ目の `com.apple.springboard` の `XCUIApplication`）を読み、アラートの
      ボタン label を返す。アラートがなければ `[]`。
- [x] ユニット 2 — label 指定の決定論的な dismiss 操作。**BE-0316 から再利用**: ガードは
      `HANDLE_SYSTEM_ALERT` capability のもとで BE-0316 の `handle_system_alert`（label セレクタ ＋
      `/systemAlert/tap`、`resolve_unique` で解決）を通じて tap するため、並立する dismiss ルートや
      capability は追加していない。
- [x] ユニット 3 — `DismissAlerts.instruction` を vision 解釈の文字列から `str | list[str]` へ発展させる。
      候補 label のリストが決定論的なネイティブ形、自由文字列が vision 形。既存の `instruction`、BE-0314 の
      `interrupts`、BE-0316 の `handleSystemAlert` セレクタに突き合わせ、語彙を増やさない。
- [x] ユニット 4 — `_AlertGuardGate` で独立した間隔（デフォルト 1 秒、`dismissAlerts` の優先順位で上書き
      可能、BE-0177）でネイティブの照会をポーリングし、最初の陽性で dismiss する（`_POLL` から切り離す。
      毎 tick 照会はせず、debounce/cooldown/max-attempts も付けない）。vision guard を fallback に降格し、
      capability を持たないバックエンドは変えない。
- [x] ユニット 5 — 実際の通知プロンプトに対する実機検証（ネイティブでの許可、認証情報なし）。ガード方針、
      ゲートのネイティブ経路、`system_alert_labels` チャネル、config 配線は実機不要のテストで覆う。

## 参考

- [`bajutsu/agents/alerts.py`](../../bajutsu/agents/alerts.py) — `SystemAlertGuard.dismiss` と
  `ClaudeAlertLocator`。本提案が fallback に降格する vision 経路です。
- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) — `_AlertGuardGate`。ユニット 4 が
  ネイティブ経路を優先するよう配線し直す、wait 途中のゲートです。
- [`bajutsu/elements.py`](../../bajutsu/elements.py) — `shows_app_ui`。ネイティブの presence 信号が置き換え、
  あるいは裏付ける、崩れたツリーの代理です。
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — 新しい照会と操作が拡張する `Driver`
  インターフェースと capability トークンです。
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) — presence と dismiss のメソッドを得る
  Python の XCUITest driver です。
- [`BajutsuKit/Sources/BajutsuRunner/Router.swift`](../../BajutsuKit/Sources/BajutsuRunner/Router.swift)
  — 新しいルートが加わる、ループバック HTTP/JSON のコマンド分岐です。
- [`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)
  と [`RunnerUITest.swift`](../../BajutsuKit/Runner/Sources/RunnerUITest.swift) — 今日は単一のアプリ
  スコープの `XCUIApplication` を保持し、2 つ目の SpringBoard 用を保持することになる provider です。
- [`bajutsu/simctl.py`](../../bajutsu/simctl.py) — `apply_permissions`。`notifications` が TCC 外のサービス
  として拒否されること（本提案が実行時に反応して埋めるギャップ）を示します。
- [`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py) —
  `DismissAlerts.instruction`。ユニット 3 が決定論的な label へ発展させる、既存の vision 解釈のボタン文字列です。
- [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config-ja.md) — ポーリング
  間隔のノブが乗る `dismissAlerts` の優先順位（flag > scenario > target > default）です。
- [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md)
  — wait 途中の guard。その「バックエンドに直接尋ねる案（そんな信号は存在しない）」という代替案を、idb が
  なくなった今、本提案が再び開きます。
- [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) — TCC に支えられた
  プロンプトを抑止する決定論的な権限の事前設定。通知と ATT を本提案に残します。
- [BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md) — プロセスを
  またぐ `springboard.alerts` の照会を到達可能にした、idb の撤去です。
- [BE-0308](../BE-0308-alerts-guard-real-model-verification/BE-0308-alerts-guard-real-model-verification-ja.md)
  — vision guard の実モデル検証。本提案が残す fallback にとって引き続き関係します。
- [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers-ja.md) — マージ済みの
  interrupt-handlers 提案。ユニット 3 のボタン方針が突き合わせるべき、別の `interrupts` フィールドです。

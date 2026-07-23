[English](BE-0315-ios-native-system-alert-handling.md) · **日本語**

# BE-0315 — ネイティブの XCUITest で iOS のシステムアラートを決定論的に検知して閉じる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0315](BE-0315-ios-native-system-alert-handling-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0315") |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config-ja.md), [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md), [BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md), [BE-0308](../BE-0308-alerts-guard-real-model-verification/BE-0308-alerts-guard-real-model-verification-ja.md), [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers-ja.md) |
<!-- /BE-METADATA -->

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

1. **`Driver` インターフェースを通じて公開する、決定論的なシステムアラートの presence 照会。**
   システムアラートが表示されているか、表示されているときはそのボタンの label を返す、バックエンド
   非依存の driver メソッドを追加します。XCUITest バックエンドは、2 つ目の
   `XCUIApplication(bundleIdentifier: "com.apple.springboard")`（起動ではなく proxy なので安価です）を
   保持し、`springboard.alerts.firstMatch` を読むことで答えます。`.exists` が表示の有無という決定論的な
   答えを与え、アラートの子孫のボタンが label を与えます。コマンドは既存のループバック HTTP/JSON
   トランスポート（`BajutsuRunner/Router.swift`）で Swift runner に届き、`(method, path)` で分岐します。
   照会はルートを 1 つと `ElementProviding` メソッドを 1 つ追加します。この信号は事実を報告するだけで、
   合否を決めることは決してないため、prime directive 1 から外れています。

2. **label 指定の決定論的な dismiss 操作。** 現在のシステムアラートを、名前で指定したボタンを押して
   閉じる driver メソッドを追加し、XCUITest では `springboard.alerts.buttons[label].tap()` として実装
   します。dismiss 操作は、新しい runner ルート、新しい `ElementProviding` メソッド、Python の
   `XcuitestDriver` メソッド、そして `bajutsu/drivers/base.py` の既存のトークンと並ぶ新しい capability
   トークンからなります。label 指定のネイティブなタップが必要なのは、決定論的な dismiss が自力でタップ
   座標を選べないためです。座標選びこそ、既存の座標タップ（`tap_point`）が vision の呼び出しに委ねている
   部分であり、固定オフセットは端末のサイズやボタン配置をまたぐと壊れます。ネイティブの
   `springboard.alerts.buttons[label]` の要素は、ボタンがどこにあってもその label で解決します。
   determinism first（prime directive 2）を保つため、
   dismiss 操作は指定された label をちょうど 1 つのボタンに解決し、0 個または複数一致したときは声高に
   失敗しなければなりません。driver の `resolve_unique` / `AmbiguousSelector` の契約に倣い、クエリが最初に
   解決したボタンを押す振る舞いにはしません。

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

- [ ] ユニット 1 — `Driver` インターフェースを通じた決定論的なシステムアラートの presence 照会。XCUITest の
      実装は 2 つ目の `com.apple.springboard` の `XCUIApplication` と `springboard.alerts` による。
- [ ] ユニット 2 — label 指定の決定論的な dismiss 操作（runner ルート、`ElementProviding` メソッド、Python
      driver メソッド、capability トークン）。
- [ ] ユニット 3 — `DismissAlerts.instruction` を vision 解釈の文字列から決定論的な label の形へ発展させる。
      既存の `instruction` と BE-0314 の `interrupts` を含む 3 者で突き合わせ、語彙を並立させず 1 つの文法へ
      収束させる。
- [ ] ユニット 4 — `_AlertGuardGate` で独立した間隔（デフォルト 1 秒、`dismissAlerts` の優先順位で上書き
      可能、BE-0177）でネイティブの照会をポーリングし、最初の陽性で dismiss する（`_POLL` から切り離す。
      毎 tick 照会はせず、debounce/cooldown/max-attempts も付けない）。vision guard を fallback に降格し、
      capability を持たないバックエンドは変えない。
- [ ] ユニット 5 — 実際の通知プロンプトか ATT プロンプトに対する実機検証。バックエンド非依存の配線は実機
      不要のテストで覆う。

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

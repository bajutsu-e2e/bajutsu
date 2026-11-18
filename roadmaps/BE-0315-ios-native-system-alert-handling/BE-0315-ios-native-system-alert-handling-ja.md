[English](BE-0315-ios-native-system-alert-handling.md) · **日本語**

# BE-0315 — リアクティブなアラートガードを、BE-0316 の SpringBoard 経路を再利用して決定論的・ネイティブにする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0315](BE-0315-ios-native-system-alert-handling-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0315") |
| 実装 PR | [#1330](https://github.com/bajutsu-e2e/bajutsu/pull/1330) |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config-ja.md), [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md), [BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md), [BE-0308](../BE-0308-alerts-guard-real-model-verification/BE-0308-alerts-guard-real-model-verification-ja.md), [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers-ja.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md), [BE-0394](../BE-0394-ai-provider-none-kill-switch/BE-0394-ai-provider-none-kill-switch-ja.md) |
<!-- /BE-METADATA -->

> **実装中にマージされた BE-0316 との整合。** BE-0316 がネイティブの SpringBoard プリミティブを実装しました。内容は、`handleSystemAlert` *ステップ*、`handle_system_alert` ドライバメソッド、`/systemAlert/query` と `/systemAlert/tap` の runner ルート、`HANDLE_SYSTEM_ALERT` capability です。
>
> BE-0316 は `dismissAlerts` をリアクティブな vision guard のまま残すことを意図的に選びました。決定論的な機構をリアクティブにすると、「成功するシナリオはモデルを呼ばない」という原則が崩れるからだと記録しています。BE-0315 はそのリアクティブな対の一方であり、この緊張を解消します。ネイティブの SpringBoard 照会は**モデル呼び出しではない**ため、原則は保たれます。
>
> したがって本実装は、並立する API を追加せず、BE-0316 のプリミティブを**再利用**します。足すのは、既存の `/systemAlert/query` を読む薄い非ブロッキングの `system_alert_labels()` だけです。BE-0315 の貢献は、自動的なリアクティブガード、決定論的なボタン方針、ポーリング間隔ノブ、vision の fallback への降格です。

## はじめに

Bajutsu は、オペレーティングシステムのアラートを、Claude vision にどこを押すか尋ねる方法でしか閉じられません。ここでいうアラートは、アプリスコープのアクセシビリティツリーから見えない SpringBoard のプロンプトです。たとえば「通知を許可」の権限ダイアログや、App Tracking Transparency（ATT）が該当します。

本提案は、iOS の XCUITest バックエンドに決定論的なネイティブ経路を代わりに設けます。この経路は、SpringBoard プロセスのアラートを照会します。照会によって、プロンプトが表示されているか否かと、そのボタンの構成を確実に把握できます。そのうえで、ボタンを label で指定して押し、プロンプトを閉じます。

ネイティブ経路は、スクリーンショットもモデルへの往復も使いません。vision guard が数秒かけるところを、0.1 秒を大きく下回る時間でプロンプトを閉じます。大規模言語モデル（LLM）も呼ばないため、`ANTHROPIC_API_KEY` がなくても動作します。一方の vision guard は、認証情報がなければ何もしません。

本提案の貢献は 2 つあります。1 つめは、runner がこれまで持ち得なかった「システムアラートが表示されているか」という決定論的な信号を得ることです。2 つめは、実行時に頻出するプロンプトが AI 経路から外れることです。ネイティブ経路が名指しできない場合の fallback として、vision は残ります。

## 動機

システムアラートの guard は AI vision の呼び出しであり、その遅延が表面化しています。SpringBoard のプロンプトが wait の途中で現れた場合を考えます。
[BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md)
のゲートは、アプリの要素ツリーが崩れたことを検知して `SystemAlertGuard.dismiss`（`bajutsu/agents/alerts.py`）を呼び出します。このメソッドはスクリーンショットを撮り、`claude-sonnet-5` に送り、モデルが返した座標を押します。

1 回の dismiss は、モデルへの 1 往復に相当します。ゲートは 2 回まで発火し、ステップ末尾の再試行がもう 1 回加わり、試行の間には cooldown が挟まります。ネイティブのタップなら即座に閉じられるプロンプトが、画面上に数秒とどまり得ます。

guard は認証情報にも依存します。guard は既定で動きますが、`ANTHROPIC_API_KEY` が設定されていなければ黙って何もしません。その場合、プロンプトは wait のタイムアウトが丸ごと経過するまで実行を止めます。

ゲートが拠り所とする信号は、事実そのものではなく代理です。`shows_app_ui(elements)`（`bajutsu/elements.py`）は、アプリ自身の UI ツリーに操作可能な内容があるかを返します。ゲートはその結果から、システムのオーバーレイが画面を塞いでいると*推論*します。

この推論は相関にすぎず、どちらの向きにも外れます。画面遷移中の一過性の空フレームは、アラートが存在しないのに塞がっていると読まれます。識別可能なアプリ要素を画面に残すオーバーレイは、実際にはプロンプトが出ているのに塞がっていないと読まれます。runner は、システムアラートが表示されているかどうかを自力で判定できたためしがありません。

guard を書いた当時、このギャップは避けられませんでした。BE-0269 は、バックエンドにアラートの有無を直接尋ねる案を検討し、退けています。idb のアクセシビリティ照会は前面のアプリにスコープされており、SpringBoard のプロンプトは idb から見えませんでした。崩れたツリーという代理が、得られる最善の信号でした。

その後、[BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md) が idb を撤去し、XCUITest を iOS の唯一のバックエンドにしました。XCUITest は 2 つ目の `XCUIApplication(bundleIdentifier: "com.apple.springboard")` を構築し、プロセス境界を越えて `springboard.alerts` を照会できます。プロセス境界を越える照会は、idb に欠けていた能力です。BE-0269 が前提とした条件はもはや成り立たないため、決定論的な presence 信号が今日は到達可能です。

決定論的な dismiss が必要になるのは、決定論的な事前抑止が届かない箇所です。[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) は `simctl privacy` を通じて権限の状態を事前設定します。そのため、位置情報、写真、カメラ、マイク、連絡先、カレンダーのプロンプトは現れません。これらのプロンプトは、オペレーティングシステムの Transparency, Consent, and Control（TCC）データベースに支えられており、`simctl privacy` がそのデータベースに書き込みます。

頻出するプロンプトのうち 2 つは TCC の外にあり、BE-0276 の手が届きません。

- 通知の許可は TCC のサービスではありません。`simctl privacy` にその名前はなく、`bajutsu/simctl.py` はまさにこの理由で `notifications` を拒否します。
- ATT は `simctl` のトグルをそもそも持ちません。

通知と ATT のプロンプトは、どちらも実行時になお現れます。今日それを閉じられるのは vision guard だけです。この 2 つのプロンプトが、ネイティブの dismiss が扱うべき具体的な対象です。

頻出するアラート経路から LLM を取り除くことは、prime directive 1 に直接かないます。guard は実行を進めるために存在し、実行を判定するためには存在しません。モデルの呼び出しをネイティブの照会と label 指定のタップに置き換えても、正しさは失われません。決定論性と速度、認証情報からの独立が得られます。

## 詳細設計

作業は、ネイティブの presence 照会、ネイティブの dismiss 操作、どのボタンを押すかの決定論的な方針、ネイティブ経路を vision より優先する配線、実機での検証に分かれます。各ユニットはインターフェースの水準ではバックエンド非依存であり、iOS 固有になるのは XCUITest の実装だけです。したがって prime directive 3（アプリごと、プラットフォームごとの差異は driver インターフェースの背後にとどめる）が全体を通じて保たれます。

> **実装（BE-0316 との整合後）。** 下記のユニット 1 と 2 は、BE-0316 が同じ SpringBoard の配管を実装する*前*の、presence 照会と dismiss 操作についての提案時の計画です。実装ではそれらを新設せず、BE-0316 を再利用しました（冒頭の整合の但し書きを参照）。
>
> 実際に入ったのは 2 点です。BE-0316 の `/systemAlert/query` を読む薄い `Driver.system_alert_labels()`（ユニット 1）と、`HANDLE_SYSTEM_ALERT` capability のもとで BE-0316 の `handle_system_alert` を通じてタップするガード（ユニット 2）です。新しい runner ルート、dismiss 操作、capability トークンは追加していません。ユニット 3 から 5 は記載どおり実装しました。

1. **`Driver` インターフェースを通じて公開する、決定論的なシステムアラートの presence 照会。**
   バックエンド非依存の driver メソッドを追加します。このメソッドは、システムアラートが表示されているかを返します。表示されているときは、そのボタンの label も返します。*（実装: 新ルートを足す代わりに、`system_alert_labels()` が BE-0316 の既存の `/systemAlert/query` を読みます。このルートは、BE-0316 がすでに保持する 2 つ目の `com.apple.springboard` の `XCUIApplication` を使います。メソッドはボタンの label を返し、アラートがなければ `[]` を返します。）* この信号は事実を報告するだけで合否を決めないため、prime directive 1 から外れています。

2. **label 指定の決定論的な dismiss 操作。** 現在のシステムアラートを、名前で指定したボタンを押して閉じます。この操作は、指定された label をちょうど 1 つのボタンに解決します。0 個または複数が一致したときは、`resolve_unique` / `AmbiguousSelector` の契約に従って声高に失敗します（prime directive 2）。最初に一致したボタンを押す振る舞いにはしません。*（実装: ガードは `HANDLE_SYSTEM_ALERT` capability のもとで、BE-0316 の `handle_system_alert` を通じてタップします。`handle_system_alert` は、`resolve_unique` で解決する label セレクタと `/systemAlert/tap` を使います。並立する dismiss ルートや capability トークンは追加していません。）*

3. **既存の `DismissAlerts.instruction` を決定論的なボタン方針へ発展させる。** `dismissAlerts` シナリオフィールドには、押すボタンを名指しする能力がすでにあります。`DismissAlerts.instruction`（`bajutsu/scenario/models/scenario.py`）は `"tap Allow"` のような自由記述の文字列で、*vision* の locator が解釈します。

   ここでの作業は、ボタン選択をゼロから加えることではありません。既存のノブを、ネイティブ経路がモデルなしで解決する決定論的な形へ発展させます。たとえば、順に試す候補 label の並び（「Allow」、次に「OK」）を与えます。こうすれば、権限を許可するシナリオと、プロンプトを閉じるだけのシナリオの双方を引き続き表現できます。何も名指ししないときは、文書化された既定の方針を適用します。

   既存フィールドを名指しすると、実装者は `instruction` を発展させます。そうしなければ、`instruction` の隣に 2 つ目の label フィールドが足され、本ユニットが警告する余分な語彙が生まれます。

   突き合わせる相手は 2 者ではなく 3 者です。

   - 既存の自由記述 `instruction`
   - 本提案が示す決定論的な label の形
   - マージ済みの interrupt-handlers 提案（[BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers-ja.md)）が、予測しにくいタイミングの割り込み画面のために導入した別の `interrupts` フィールド

   実装するセッションは、この 3 つを 1 つの文法に収束させます。3 つの語彙を並立させたり、4 つ目を増やしたりしてはいけません。

4. **ネイティブの照会を独立した間隔でポーリングし、最初の陽性で dismiss し、vision は fallback としてのみ残す。** このユニットは 4 つの要素からなります。

   - **間隔**：バックエンドが presence と dismiss の capability を広告しているとき、BE-0269 の `_AlertGuardGate` はネイティブの presence 照会を独自の実時間間隔（デフォルト 1 秒）で発行します。この間隔は、wait の `_POLL` 条件周期から切り離します。ゲートは、ポーリングが表示中のアラートを見つけ、そのアラートが方針の名指しするボタンを備えていれば、直ちに閉じます。
   - **ノブ**：適切な周期はヒューリスティックです。検知レイテンシと runner の負荷とのトレードオフであり、単一の値がすべてのアプリに合うわけではありません。そこで間隔はハードコードせず、ノブとして公開します。デフォルトは 1 秒で、`dismissAlerts` 設定がすでに従う優先順位（flag > scenario > target > default、BE-0177）で上書きできます。
   - **毎 tick の照会はしない**：毎 tick のネイティブ照会は、意図的に避けます。XCUITest runner は単一の main thread で照会を処理します（`onMainCatching`）。SpringBoard は別プロセスなので、その照会は、無料の `shows_app_ui` 代理のようには取得済みのアプリツリーを再利用できません。50ms ごとのアプリスナップショットに 2 本目の cross-process 照会を並べると、runner の負荷がおよそ倍になり、不安定化やクラッシュを招きかねません。間隔で区切れば、追加負荷は 1 間隔ごとに SpringBoard 照会 1 本に収まります。小さく有界な検知レイテンシ（最大でも 1 間隔）と引き換えに、runner の安定を得ます。それでも、置き換える vision 経路よりはるかに早くプロンプトを消せます。
   - **debounce、cooldown、max-attempts は不要**：`springboard.alerts.firstMatch.exists` は、崩れたツリーという代理の相関ではなく事実を返します。一過性フレームの偽陽性がないため、debounce は要りません。固定間隔がすでに照会をレート制限しているため、cooldown や max-attempts も要りません。

   vision guard が動くのは、ネイティブ経路が行動できない次の 3 つの場合です。

   - バックエンドが capability を持たない場合です。
   - 阻害している面が、列挙可能な `springboard.alerts` のアラートではない場合です。
   - アラートが、方針の名指しするボタンを備えていない場合です。

   頻出する iOS のプロンプト（通知、ATT、方針が label を名指しする任意のアラート）は vision に届きません。想定外のプロンプトには、安全網が残ります。capability を持たないバックエンドは、今日の「崩れたツリーの代理と vision」の挙動をそのまま保ちます。そのため orchestrator はバックエンド非依存のままで、既存の経路は 1 つも退行しません。vision guard は、今日でも pass/fail の判定パス上にありません（prime directive 1）。fallback として残しても、結果の決定論性は損なわれません。

5. **実機で検証する。** 実際の SpringBoard アラートに対するネイティブなタップは、Simulator を使わないゲートでは証明できません。そのため、XCUITest の実装を載せるユニットは、起動済みの Simulator 上で実際の通知プロンプトか ATT プロンプトを出す必要があります。そのプロンプトで、presence 照会と label 指定の dismiss の双方を確かめます。バックエンド非依存の配線（ユニット 3 と 4）は、driver の capability をスタブする実機不要のテストで覆います。

6. **showcase と CI でネイティブなリアクティブ許可を実現する。** showcase の通知シナリオを、ネイティブ経路を動かすように変えます。`demos/showcase/scenarios/permission.yaml` は、リスト形の `dismissAlerts: { instruction: ["Allow"] }` で許可します。既定となった XCUITest backend（BE-0290）では、ガードは vision fallback を通らず、正確な label のネイティブ照会で「Allow」を押します。

   許可が決定論的かつ認証情報なしで動くようになったため、このシナリオは `ai` タグを外し、必須の XCUITest ゲートに加わります（`ios-e2e.yml` は `--exclude ai` を落とします）。このシナリオは、同じゲート上にある BE-0316 のプロアクティブな `permission_system_alert.yaml` に対する、リアクティブなネイティブの対応物です。このユニットによって、ネイティブな許可は、実機不要のテストだけが覆う capability にとどまらず、ゲートで担保される実証済みのフローになります。

## 検討した代替案

- **XCUITest 組み込みの割り込みハンドラ `addUIInterruptionMonitor`。** 却下します。この監視は、テストハーネスが次にアプリへ操作を加えたときに発火するため、タイミングが非決定論的です。照会可能な presence 信号を提供せず、wait のポーリング周期で駆動することもできません。明示的な `springboard.alerts` の照会は、検査可能で決定論的です。本提案が必要とするのは、この性質です。
- **vision のみの guard を保ち、BE-0269 のタイミングをさらに調整する。** 却下します。遅延の下限はモデルへの往復そのものであり、どんな cooldown や debounce の調整でもこの下限は取り除けません。vision 経路は、認証情報がなければなお何もしません。ネイティブ経路は、遅延と認証情報への依存を切り詰めるのではなく、両方を取り除きます。
- **BE-0276 を広げて通知と ATT を事前設定する。** 却下します。通知の許可は TCC のサービスではないため、`simctl privacy` では書き込めません。ATT は `simctl` のトグルをそもそも持ちません。どちらのプロンプトも事前には抑止できないため、実行時に反応して閉じる経路が避けられません。
- **ネイティブな要素タップではなく、座標タップ（`tap_point`）を決め打ちのオフセットで再利用する。** 却下します。固定オフセットは、端末のサイズ、ボタン配置、動的なテキストをまたぐと壊れます。座標を別の方法で選ぶには、本提案が取り除こうとしている vision の呼び出しが要ります。ネイティブの `springboard.alerts.buttons[label]` の要素は、位置によらずボタンを label で解決するため、安定したプリミティブです。
- **ネイティブの照会を間隔ではなく wait の毎 tick でポーリングする。** 却下します。XCUITest runner は単一の main thread で照会を処理します。毎 tick のアプリスナップショットに毎 tick の SpringBoard 照会を重ねると、負荷がおよそ倍になり、runner の不安定化やクラッシュを招きかねません。得られるレイテンシの改善は、高々 1 間隔ぶんです。有界な間隔（デフォルト 1 秒）なら、runner を安定に保ちつつ、置き換える vision 経路よりはるかに早くプロンプトを消せます。

  無料の `shows_app_ui` の崩れ代理でネイティブ照会をゲートする変種も検討しました。この変種は、アプリツリーが塞がって見えるときだけ照会します。検討の結果、独立した間隔を選びました。独立した間隔は、代理の偽陰性という盲点を受け継がないからです。アプリ要素を画面に残すオーバーレイでは、ゲートした照会が一度も発火しません。

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
- [x] ユニット 6 — showcase と CI でネイティブなリアクティブ許可を実現する。`demos/showcase/scenarios/permission.yaml`
      の通知シナリオはリスト形の `dismissAlerts: { instruction: ["Allow"] }` で許可するようにし、既定と
      なった XCUITest backend（BE-0290）でネイティブ経路が決定論的に「Allow」を押す。自由文字列はネイティブの
      label を埋めず、既定の無害な label 群にフォールバックして「Don't Allow」を押し、許可を反転させてしまう。
      許可がネイティブかつ鍵不要になったため、このシナリオは `ai` タグを外して必須の XCUITest ゲートに加わる
      （`ios-e2e.yml`、`--exclude ai` は不要）。同じゲート上にある BE-0316 のプロアクティブな
      `permission_system_alert.yaml` に対する、リアクティブな対応物である。

## 参考

- [`bajutsu/agents/alerts.py`](../../bajutsu/agents/alerts.py)：`SystemAlertGuard.dismiss` と `ClaudeAlertLocator` です。本提案が fallback に降格する vision 経路にあたります。
- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py)：`_AlertGuardGate` です。ユニット 4 がネイティブ経路を優先するよう配線し直す、wait 途中のゲートにあたります。
- [`bajutsu/elements.py`](../../bajutsu/elements.py)：`shows_app_ui` です。ネイティブの presence 信号が置き換え、あるいは裏付ける、崩れたツリーの代理にあたります。
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py)：新しい照会と操作が拡張する `Driver` インターフェースと capability トークンです。
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py)：presence と dismiss のメソッドを得る Python の XCUITest driver です。
- [`BajutsuKit/Sources/BajutsuRunner/Router.swift`](../../BajutsuKit/Sources/BajutsuRunner/Router.swift)：新しいルートが加わる、ループバック HTTP/JSON のコマンド分岐です。
- [`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift) と [`RunnerUITest.swift`](../../BajutsuKit/Runner/Sources/RunnerUITest.swift)：今日は単一のアプリスコープの `XCUIApplication` を保持し、2 つ目の SpringBoard 用も保持することになる provider です。
- [`bajutsu/simctl.py`](../../bajutsu/simctl.py)：`apply_permissions` です。`notifications` が TCC 外のサービスとして拒否されること（本提案が実行時に反応して埋めるギャップ）を示します。
- [`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)：`DismissAlerts.instruction` です。ユニット 3 が決定論的な label へ発展させる、既存の vision 解釈のボタン文字列にあたります。
- [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config-ja.md)：ポーリング間隔のノブが従う `dismissAlerts` の優先順位（flag > scenario > target > default）です。
- [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md)：wait 途中の guard です。その「バックエンドに直接尋ねる案（そんな信号は存在しない）」という代替案を、idb がなくなった今、本提案が再び開きます。
- [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md)：TCC に支えられたプロンプトを抑止する、決定論的な権限の事前設定です。通知と ATT を本提案に残します。
- [BE-0290](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md)：プロセスをまたぐ `springboard.alerts` の照会を到達可能にした、idb の撤去です。
- [BE-0308](../BE-0308-alerts-guard-real-model-verification/BE-0308-alerts-guard-real-model-verification-ja.md)：vision guard の実モデル検証です。本提案が残す fallback にとって、引き続き関係します。
- [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers-ja.md)：マージ済みの interrupt-handlers 提案です。ユニット 3 のボタン方針が突き合わせるべき、別の `interrupts` フィールドを持ちます。

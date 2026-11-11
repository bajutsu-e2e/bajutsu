[English](BE-0316-ios-permission-alert-step.md) · **日本語**

# BE-0316 — iOS の権限プロンプトを明示的に操作する mid-flow ステップ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0316](BE-0316-ios-permission-alert-step-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0316") |
| 実装 PR | [#1324](https://github.com/bajutsu-e2e/bajutsu/pull/1324) |
| トピック | シナリオ記述機能 |
| 関連 | [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md)、[BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight-ja.md)、[BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md)、[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本項目は `handleSystemAlert` を追加します。これは、iOS の権限プロンプトのボタンを決定論的にタップする、新しいシナリオステップです。ステップは、プロンプトそのものへのネイティブなアクセシビリティクエリでボタンを解決します。スクリーンショットを視覚モデルに判断させることはありません。ステップが動くのは、シナリオの中で著者がプロンプトの出現を見込むまさにその地点です。

iOS は、位置情報、カメラ、連絡先などの権限リクエストを、SpringBoard レベルのアラートとして表示します。SpringBoard は iOS のホーム画面を担うプロセスで、この種のプロンプトを含むシステム全体の UI も所有しています。そのためアラートは、テスト対象アプリ自身のプロセスにも、そのアクセシビリティツリーにも属しません。

こうしたプロンプトを扱う手段は、現状すでに 2 つあります。どちらも、本項目が狙うケースには合いません。狙うケースとは、シナリオが権限リクエスト自体を発生させ、その場でプロンプトを決定論的に操作したいというものです。`handleSystemAlert` はこの隙間を埋めます。

## 動機

Bajutsu はすでに権限プロンプトを 2 つの方法で扱っており、それぞれ別の場面で役割を持っています。

- **視覚アラートガード**：ガード（`dismissAlerts`、`bajutsu/agents/alerts.py`）は、シナリオが予見できないプロンプトのためのものです。ステップがブロックされると、スクリーンショットを撮り、Claude にどこを押すか尋ねて、プロンプトを片付けます。この復旧は反応的で、ベストエフォートです。Claude の API キーが必要です。視覚モデルの呼び出しは参照ではなく判断なので、毎回同じ座標に着地するとは限りません。
- **`permissions`**：このフィールド（[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md)）は、シナリオが事前に把握している権限のためのものです。アプリのプロセスが起動する前に `simctl privacy` で権限を付与または剥奪するので、プロンプト自体が一切現れません。

この 2 つの間には、どちらもうまく扱えないケースが残っています。シナリオが request フロー自体をテストしたい場合です。権限リクエストを発生させるボタンをタップし、続いて現れるプロンプトを許可または拒否します。このテストは、AI 呼び出しなしで決定論的に動かしたいものです。

事前設定の `permissions` は、このケースを覆えません。リクエストはアプリがそれを発行した瞬間、つまりシナリオの途中で初めて発生するからです。その瞬間に残る手段は、視覚ガードの `dismissAlerts: { instruction: "tap Allow" }` だけでした。

BE-0276 自身も、mid-flow の権限操作を将来の方向性として挙げています。ただし、そこで見送られたのは別の仕組みです。見送られたのは、命令的な `setPermissions` ステップです。このステップは、`simctl privacy` 経由で Transparency, Consent, and Control（TCC）の状態を mid-flow で設定または剥奪します。拒否済みの経路を revoke して再実行するためのものです。プロンプトをタップするステップではありません。

`handleSystemAlert` は、同じ mid-flow の場面に逆の側から応えます。権限の状態をプログラムで設定するのではありません。リクエストが上げる可視の SpringBoard プロンプトをタップします。

シナリオの著者は、その時点でどのプロンプトが現れ、どのボタンが必要かをすでに知っています。BE-0276 が、事前に判明している権限を視覚ガードから切り離した論理が、ここでもそのまま成り立ちます。事前に判明している mid-flow のプロンプトを、AI 呼び出しに通す理由はありません。ネイティブなアクセシビリティクエリなら、同じボタンをより低コストで決定論的に解決でき、機械的に判定できる結果を返します。ステップは、唯一の一致するボタンをタップするか、明確に失敗するかのどちらかです。ボタンが動いていても黙って外れる座標の当て推量にはなりません（第一原則：AI が合否を判定することはない。第二原則：決定性を最優先する）。

## 詳細設計

提案のレベル感はこの粒度で揃えます。以下のユニットで MECE に分解します。

request-and-grant フローをテストするシナリオは、次のように書けます。OS の権限リクエストを発生させてから、SpringBoard のプロンプトのボタンを、決定論的に、視覚モデルなしでタップします。

```yaml
- name: grant the notification prompt mid-flow
  steps:
    - tap: { id: perm.requestNotif }                              # OS の権限リクエストを発生させる
    - handleSystemAlert: { sel: { label: "Allow" }, timeout: 5 }  # プロンプトのボタンをラベルでタップ
    - wait: { for: { id: perm.notif.authorized }, timeout: 5 }    # 許可され、アプリの状態が更新される
```

許可ではなく却下したいときは、同じステップで却下側のボタンを指定します（`handleSystemAlert: { sel: { label: "Don't Allow" }, timeout: 5 }`）。

- **シナリオスキーマ**：新しいステップアクション `handleSystemAlert: { sel: <Selector>, timeout: <秒> }` を追加します。形は、`longPress` や `pinch` がすでに使っている `sel:` で包む形にならいます。`sel` は、`Selector` のうちラベルベースのフィールド（`label`、`labelMatches`、`index`）を受け付けます。`id`、`idMatches`、`traits`、`value`、`within` は、パース時に拒否します。SpringBoard のアラートボタンには、アプリが割り当てたアクセシビリティ識別子、トレイト、値がありません。持つのは見えているテキストだけなので、拒否するフィールドは決して一致し得ません。`timeout` は `wait` と同様に必須にします。条件待ちには明示的な上限が要ります。共有シナリオのタイムアウト下限は、ランナーの他のあらゆる待ちと同じく、このステップにも適用されます。
- **iOS（XCUITest）ランナー側の対応**：`BajutsuKit/Runner/Sources/RunnerUITest.swift` は、テスト対象アプリのために `XCUIApplication(bundleIdentifier:)` のハンドルをすでに構築しています。`handleSystemAlert` ステップは、SpringBoard 自身のバンドル識別子である `com.apple.springboard` 用のハンドルを、オンデマンドで追加します。ランナーがこのハンドルを照会するのは、このステップの実行時だけです。他のあらゆるセレクタとクエリの経路は、今までどおりテスト対象アプリにスコープされたままです。ステップは、SpringBoard のアラート要素の中からラベルでボタンを解決します。そのボタンを、アプリ内の要素に対して XCUITest がすでに使っているのと同じ、ネイティブなアクセシビリティタップでタップします。スクリーンショットも視覚モデルも使いません。
- **一致がゼロまたは複数のときは即座に失敗させる**：ステップは、通常のセレクタがすでに従っている規則（[selectors](../../docs/selectors.md)）に従います。この規則を、アプリの要素ではなくアラートのボタンに適用します。
  - `timeout` 以内にアラートが現れなければ、明確なメッセージとともにステップを失敗させます。
  - ラベルに一致するボタンが複数あれば、`index` が n 番目を選ぶ場合を**除いて**、曖昧として失敗させます。

  `index` は、どの要素とも同じくアラートでも最後の手段です。通常は、ラベルでプロンプトのボタンが一意に定まります。
- **ケーパビリティトークンと preflight**：このステップを表すケーパビリティを、既存の operation 単位の preflight パターン（[BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight-ja.md)）にならって宣言します。宣言するのは、iOS（XCUITest）の[バックエンド](../../docs/ja/glossary.md#driver-backend-actuator-platform)だけです。

  Android（adb）バックエンドは、最前面のウィンドウをすでにダンプ対象にしており、システムの権限ダイアログもそこに含まれます。通常の `tap` ステップが、今日すでにそのダイアログへ届きます。web（Playwright）バックエンドには、そもそも OS レベルの権限プロンプトがありません。`handleSystemAlert` をどちらかのバックエンドに対して指定したシナリオは、デバイスへの操作が始まる前の preflight で失敗します。失敗ではステップが個別に名指しされ、実行時まで持ち越されることはありません。
- **codegen**：iOS の XCUITest codegen は、ネイティブなイディオムをそのまま生成します。生成されるテストは、`XCUIApplication(bundleIdentifier: "com.apple.springboard").buttons["Allow"]` の存在をステップ自身の `timeout` を上限に待ってから、タップします。手で書いた XCUITest テストが、SpringBoard のアラートを片付ける書き方そのものです。生成される待ちには、ステップが必須とする `timeout` を引き継ぐので、値を取りこぼしません。

  Android と web の codegen は、ラベル付きの `// TODO` を生成します。前例は、[BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md) と BE-0276 です。どちらのバックエンドのネイティブなテストフレームワークも表現できないフィールドが対象でした。`permissions` は違います。bajutsu 自身が、起動前にすべてのバックエンドでこのフィールドを適用します。`handleSystemAlert` は mid-flow のステップで、そのネイティブなイディオムを表現できるのは iOS（XCUITest）のフレームワークだけです。両者の扱いが分かれるのは、このためです。
- **ドキュメントとフィクスチャ**：[`docs/scenarios.md`](../../docs/scenarios.md)、その日本語版、DSL 文法に、このステップを記載します。

  `docs/scenarios.md` には、すでに「dismissAlerts（システムアラートガード）」の節があります。新しいステップの記述では、読み手が両者を混同しないよう、役割の違いを対比します。
  - `dismissAlerts` は、反応的で AI 駆動の*ガード*です。ガードが発火するのは、ステップがすでにブロックされたときだけです。
  - `handleSystemAlert` は、決定論的でネイティブな*ステップ*です。著者が、プロンプトを見込む地点に置きます。

  どちらも、同じ種類の OS レベルのアラートを、正反対の仕組みで操作します。反応的なガードと決定論的なステップという対比を述べておくと、読み手が誤って一方に手を伸ばさずに済みます。

  フィクスチャは、独立した新しいシナリオファイル（`demos/showcase/scenarios/permission_system_alert.yaml`）として追加します。既存の `permission.yaml` に新しいシナリオを足してはいけません。`permission.yaml` の通知シナリオはすでに `ai` タグを持ち、iOS の smoke レーンはそれを `--exclude ai` で除外できます。一方、Android の `smoke (adb)` ジョブは、このファイルのシナリオを**一切変えずに**すべて実行します。同じファイルに `handleSystemAlert` を使うシナリオを足すと、Android の必須ジョブが preflight で落ちます。このケーパビリティを宣言するのは iOS（XCUITest）バックエンドだけだからです。

  ファイルを分ければこの失敗を避けられ、境界は 1 つのタグではなく仕組みで分かれます。Android の `smoke (adb)` ジョブは、`demos/showcase/android/Makefile` の**固定のシナリオ一覧**を実行します。その一覧が名指ししないファイルは、そこで拾われません。フィクスチャを Android レーンから外すのは、タグではなくファイル分離です。

  フィクスチャは、ローカルの一括実行 `run-swiftui` と `run-uikit` からも外す必要があります。この 2 つの実行は、すべてのシナリオファイルを走査し、権限状態をシナリオごとにリセットしません。そのため、ステップが必要とする `notDetermined` のプロンプトが出るとは限りません。フィクスチャには**専用**の除外タグ（例：`systemalert`）を付け、この 2 つのターゲットの `--exclude` に加えます。`tabs.yaml` の `xcuitest` タグを流用してはいけません。

  `xcuitest` タグは、退役した idb バックエンドに `[ios]` が解決し得た時代に由来する、legacy な切り分けです（`tabs.yaml` のヘッダと `demos/showcase/Makefile` を参照）。文書上も撤去候補です。このタグに相乗りすると、タグが撤去された日に、このフィクスチャが静かに除外解除されてしまいます。目的を表すタグにすれば、フィクスチャの除外は、legacy な切り分けの撤去から独立します。
- **CI への組み込み**：タグを付けるだけでは、CI のジョブにファイルは追加されません。`ios-e2e.yml` の各ジョブは、実行するシナリオファイルをすべて名指しで指定しています。ディレクトリを走査する仕組みも、タグで対象を選ぶ仕組みも、CI のどこにもありません。`xcuitest (multi-touch)` ジョブに、`scenarios: demos/showcase/scenarios/permission_system_alert.yaml` を指定するステップを明示的に加えます。このジョブは、すでに `permission.yaml` を専用のステップで実行しています。新しいシナリオに必要な Simulator、アプリ、ランナーのビルドは、このジョブですでに済んでいます。新しいジョブを立てずに、`bajutsu run` をもう 1 回走らせるだけで済みます。
- **テスト**：テストは 4 つの領域を覆います。
  - スキーマのパースと検証：ラベルベースのフィールドは受け入れ、`id`、`idMatches`、`traits`、`value`、`within` は明確なメッセージ付きで拒否します。
  - サブセットだけを宣言するバックエンドに対する preflight：iOS は通過し、Android と web は個別に名指しされて即座に失敗します。
  - XCUITest ランナーの SpringBoard 解決：一致がゼロ、1 つ、複数のそれぞれのボタン一覧に対して検証します。
  - codegen のスニペット。

## 検討した代替案

- **`dismissAlerts` を任意のタイミングで mid-step に発火させられるようにする。** 却下しました。`dismissAlerts` の反応的な設計は、ステップがすでにブロックされたときにだけ発火します。通過するシナリオでは、一度もモデルを呼び出しません（[`docs/scenarios.md`](../../docs/scenarios.md)）。この不変条件自体が、`dismissAlerts` の価値の一部です。著者が選んだ地点で発火させようとすると、2 つの代償のどちらかを払います。1 つは、ブロックされたステップを偽装してガードを起動する代償です。もう 1 つは、ガードを使うすべてのシナリオで「通過するシナリオではモデルを一切呼ばない」という保証を崩す代償です。専用のステップにすれば、`dismissAlerts` 側の保証はそのまま保たれます。`handleSystemAlert` 自身も、より単純な契約を持てます。決定論的で、`steps` の中に現れた地点で無条件に働く契約です。
- **一般の `Selector` に `system: true` のようなスコープを足し、`tap` / `wait` / `assert` が直接 SpringBoard へ届くようにする。** 本項目としては却下しました。あらゆるステップとアサーションは、1 つの共有された `Selector` 解決経路を通じて解決されます。SpringBoard とアプリの区別をそのすべてに通すと、シナリオの中の 1 箇所だけで生じるニーズのために、必要性に見合わない範囲まで変更が広がります。専用の `handleSystemAlert` アクションなら、変更を 1 つの新しいステップ実装だけに閉じ込められます。他のあらゆるステップが依存しているセレクタ解決経路に、リスクを持ち込みません。
- **権限プロンプトに限らず、SpringBoard レベルのアラート全般（パスワード保存の確認、ペーストの許可、クラッシュシートなど）を対象とする。** 見送りました。権限プロンプトは、request フローをテストするシナリオの著者が実際に直面する、具体的でよくあるケースです。SpringBoard へのクエリという仕組み自体は、アラートの出どころを区別しません。後から `handleSystemAlert` を任意の SpringBoard アラートへ広げる道を、本項目が閉ざすわけではありません。最初のバージョンを権限プロンプトに絞ることで、この提案をレビューして一度に取り込める大きさに保ちます。
- **視覚ガードが求めた座標を 1 回だけキャッシュして使い回す。** 却下しました。この方法でも、スクリーンショットとモデルの往復が最低 1 回は必要です。Claude の API キーも要ります。キャッシュした座標が正しいままである保証もありません。デバイスの向き、ロケール、iOS のバージョンが変われば、アラートのボタンは動き得るからです。ネイティブなアクセシビリティクエリなら、そうした前提を置かずに、そのつどボタンの現在位置を解決できます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] シナリオスキーマ — `handleSystemAlert` ステップ、ラベルベースの `Selector` サブセット、必須の `timeout`。
- [x] iOS（XCUITest）ランナー側の対応 — オンデマンドの SpringBoard `XCUIApplication` ハンドル、ネイティブタップ。
- [x] アラートボタンの一致がゼロまたは複数のときに即座に失敗させる。
- [x] ケーパビリティトークン + preflight（iOS のみが宣言）。
- [x] codegen — iOS ではネイティブな XCUITest のイディオム、Android と web ではラベル付き `// TODO`。
- [x] ドキュメント（scenarios.md + 日本語版、DSL 文法）と、新しい iOS 専用の showcase フィクスチャ
      ファイル（`permission_system_alert.yaml`）。Android の `smoke (adb)` ジョブがそのまま実行する既存の
      `permission.yaml` には決して足しません。専用の除外タグ（例：`systemalert`）で、legacy な `xcuitest`
      タグに相乗りせずローカルの一括実行 `run-swiftui`/`run-uikit` から外します。
- [x] CI への組み込み — `ios-e2e.yml` の `xcuitest (multi-touch)` ジョブに、新しいフィクスチャ用の
      明示的な `scenarios:` ステップを追加します。タグを付けるだけでは CI のジョブには何も加わりません。
- [x] テスト — スキーマ、preflight、SpringBoard 解決（ゼロ/1つ/複数の一致）、codegen のスニペット。

**設計メモ（解決は Python 側に残す）。** ランナーは 2 つのエンドポイントを公開します。`POST /systemAlert/query` は、SpringBoard のアラートのボタンをハンドル付きでスナップショットします。`POST /systemAlert/tap` は、ハンドルで 1 つのボタンをタップします。Python ドライバは、この query をステップの `timeout` までポーリングし、ラベルベースのセレクタを共有の `resolve_unique` に通します。したがって解決は、あらゆるセレクタが従う、ゼロ、曖昧、index の規則をそのまま使います。シミュレータなしの高速な Python ゲートが、フェイクのボタン列に対して解決を検証します。実機上の Swift 経路は、`xcuitest (multi-touch)` の CI ジョブが動かします。

## 参考

- [BE-0276：シナリオ単位の宣言的な権限状態](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md)は、反応的なガードに対する、起動前の決定論的な対の仕組みです。BE-0276 は、mid-flow の命令的な状態設定ステップ（`simctl privacy`）を見送っています。これは、本項目のプロンプトのタップとは別の仕組みです。
- [BE-0128：デバイス制御ステップの preflight ゲート](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight-ja.md)は、本項目が従う operation 単位の preflight パターンです。
- [BE-0026：非対応構文の縮小](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md)は、バックエンドのネイティブなフレームワークが表現できないステップに対する、ラベル付き `// TODO` という codegen の前例です。
- `bajutsu/agents/alerts.py` と `docs/scenarios.md`（`dismissAlerts` の節）は、本項目が補完する、既存の反応的な視覚ガードです。
- `BajutsuKit/Runner/Sources/RunnerUITest.swift` は、本項目が SpringBoard スコープの第 2 のハンドルで拡張する、既存の `XCUIApplication(bundleIdentifier:)` の構築部分です。
- 対の提案 `rename-dismiss-alerts-to-alert-handling`（`alertHandling`）は、反応的な `dismissAlerts` ガードを改名する提案です。本項目はこのガードと並び、終始参照します。ここで `dismissAlerts` を使う読み手は、この名前が非推奨になり `alertHandling` へ改名されることを知っておくべきです。2 つの提案のドキュメントは、反応的なガードと明示的なステップを 1 箇所で対比します。

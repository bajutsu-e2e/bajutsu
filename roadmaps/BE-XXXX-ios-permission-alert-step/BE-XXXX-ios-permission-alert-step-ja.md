[English](BE-XXXX-ios-permission-alert-step.md) · **日本語**

# BE-XXXX — iOS の権限プロンプトを明示的に操作する mid-flow ステップ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-ios-permission-alert-step-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | シナリオ記述機能 |
| 関連 | [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md)、[BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight-ja.md)、[BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本項目は `handleSystemAlert` を追加します。これは、シナリオの中でプロンプトが現れるとまさにその瞬間に、
iOS の権限プロンプトのボタンを決定的にタップする新しいステップです。スクリーンショットを視覚モデルに
判断させるのではなく、プロンプトそのものへのネイティブなアクセシビリティクエリでボタンを解決します。
iOS は位置情報・カメラ・連絡先などの権限リクエストを、SpringBoard レベルのアラートとして表示します。
SpringBoard は iOS のホーム画面を担うプロセスで、この種のプロンプトを含むシステム全体の UI もあわせて
所有しているため、アラートはテスト対象アプリ自身のプロセスにも、そのアクセシビリティツリーにも属し
ません。こうしたプロンプトを扱う手段は現状すでに2つありますが、どちらも本項目が狙うケース、すなわち
「シナリオが権限リクエスト自体を発生させ、その場でプロンプトを決定的に操作したい」というケースには
合いません。`handleSystemAlert` はこの隙間を埋めます。

## 動機

Bajutsu はすでに権限プロンプトを2つの方法で扱っており、それぞれ別の場面で役割を持っています。視覚
アラートガード（`dismissAlerts`、`bajutsu/agents/alerts.py`）は、シナリオが予見できないプロンプトの
ためのものです。ステップがブロックされると、スクリーンショットを撮り、Claude にどこを押すか尋ねて
プロンプトを片付けます。この反応的な仕組みは Claude の API キーを必要とし、視覚モデルの判断は毎回同じ
座標に着地するとは限りません。`permissions`
（[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md)）は、
シナリオが事前に把握している権限のためのものです。アプリのプロセスが起動する前に `simctl privacy` で
権限を付与・剥奪するので、プロンプト自体が一切現れません。

この2つの間には、どちらもうまく扱えないケースが残っています。権限リクエストを発生させるボタンを
タップし、続いて現れるプロンプトを許可または拒否する——この request フロー自体を、AI 呼び出しなしで
決定的にテストしたいというケースです。事前設定の `permissions` はこのケースをカバーできません。リクエスト
はアプリがそれを発行した瞬間、つまりシナリオの途中で初めて発生するからです。残る手段は視覚ガードの
`dismissAlerts: { instruction: "tap Allow" }` だけでした。BE-0276 自身も mid-flow の権限操作を将来の
方向性として挙げていますが、そこで見送られたのは別の仕組みです。それは `simctl privacy` 経由で TCC
（Transparency, Consent, and Control）の状態を mid-flow で設定・剥奪する命令的な `setPermissions`
ステップ（拒否済みの経路を revoke して再実行するためのもの）であり、プロンプトを tap するものでは
ありません。`handleSystemAlert` は同じ mid-flow の場面に逆の側から応えます。権限の状態をプログラムで
設定するのではなく、リクエストが上げる可視の SpringBoard プロンプトを tap します。シナリオの著者はその
時点でどのプロンプトが現れ、どのボタンが必要かをすでに知っているので、BE-0276 が事前に判明している権限を
視覚ガードから切り離した論理がここでも
そのまま成り立ちます。事前に判明している mid-flow のプロンプトを AI 呼び出しに通す理由はなく、ネイティブ
なアクセシビリティクエリの方が同じボタンをより低コストかつ決定的に解決でき、機械的に判定できる結果を
返します。唯一の一致するボタンをタップするか、明確に失敗するかのどちらかであり、ボタンが動いていても
黙って外れる座標の当て推量にはなりません（第一原則：AI が合否を判定することはない。第二原則：決定性を
最優先する）。

## 詳細設計

提案のレベル感はこの粒度で揃えます。以下のユニットで MECE に分解します。

request-and-grant フローをテストするシナリオは次のように書けます。OS の権限リクエストを発生させてから、
SpringBoard のプロンプトのボタンを、決定的に、視覚モデルなしでタップします。

```yaml
- name: grant the notification prompt mid-flow
  steps:
    - tap: { id: perm.requestNotif }                              # OS の権限リクエストを発生させる
    - handleSystemAlert: { sel: { label: "Allow" }, timeout: 5 }  # プロンプトのボタンをラベルでタップ
    - wait: { for: { id: perm.notif.authorized }, timeout: 5 }    # 許可され、アプリの状態が更新される
```

許可ではなく却下したいときは、同じステップで却下側のボタンを指定します
（`handleSystemAlert: { sel: { label: "Don't Allow" }, timeout: 5 }`）。

- **シナリオスキーマ。** 新しいステップアクション `handleSystemAlert: { sel: <Selector>, timeout: <秒> }`
  を追加します。`longPress` や `pinch` がすでに使っている `sel:` で包む形にならいます。`sel` は
  `Selector` のうちラベルベースのフィールド——`label`、`labelMatches`、`index`——だけを受け付け、
  `id`、`idMatches`、`traits`、`value`、`within` はパース時に拒否します。SpringBoard のアラート
  ボタンには、アプリが割り当てたアクセシビリティ識別子・トレイト・値のいずれもなく、見えている
  テキストしか持たないため、これらのフィールドは決して一致し得ないからです。`timeout` は `wait` と同様に必須にします。条件待ちには
  明示的な上限が要り、共有シナリオのタイムアウト下限は、ランナーの他のあらゆる待ちと同じくここにも
  適用されます。
- **iOS（XCUITest）ランナー側の対応。** `BajutsuKit/Runner/Sources/RunnerUITest.swift` は、テスト
  対象アプリのために `XCUIApplication(bundleIdentifier:)` のハンドルをすでに構築しています。
  `handleSystemAlert` ステップは、SpringBoard 自身のバンドル識別子である `com.apple.springboard` 用の
  ハンドルを、このステップの実行時にだけオンデマンドで追加します。これにより、他のあらゆるセレクタ・
  クエリの経路は今までどおりテスト対象アプリにスコープされたままです。このステップは、SpringBoard の
  アラート要素の中からラベルでボタンを解決し、アプリ内の要素に対して XCUITest がすでに使っているのと
  同じネイティブなアクセシビリティタップでそれをタップします。スクリーンショットも視覚モデルも使い
  ません。
- **一致がゼロまたは複数のときは即座に失敗させる。** `timeout` 以内にアラートが現れなければ、明確な
  メッセージとともにステップを失敗させます。ラベルに一致するボタンが複数あれば、曖昧として失敗させ
  ます。これは通常のセレクタがすでに従っているのと同じ規則（[selectors](../../docs/selectors.md)）
  を、アプリの要素ではなくアラートのボタンに適用したものです。
- **ケーパビリティトークンと preflight。** このステップを表すケーパビリティを、既存の operation
  単位の preflight パターン
  （[BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight-ja.md)）
  にならって宣言し、iOS（XCUITest）[バックエンド](../../docs/ja/glossary.md#driver-backend-actuator-platform)だけがそれを宣言します。Android（adb）バックエンドは
  最前面のウィンドウをすでにダンプ対象にしており、システムの権限ダイアログもそこに含まれるため、
  通常の `tap` ステップですでにそこへ届きます。web（Playwright）バックエンドには、そもそも OS レベル
  の権限プロンプト自体がありません。`handleSystemAlert` をこのどちらかのバックエンドに対して指定したシナリオ
  は、デバイスへの操作が始まる前の preflight の時点で、個別に名指しされて失敗します。
- **codegen。** iOS の XCUITest codegen は、ネイティブなイディオムをそのまま生成します。
  `XCUIApplication(bundleIdentifier: "com.apple.springboard").buttons["Allow"]` の存在をステップ
  自身の `timeout` を上限に待ってからタップする、という形です。手で書いた XCUITest テストがすでに
  SpringBoard のアラートを片付けている書き方そのものであり、ステップが必須とする `timeout` を
  生成される待ちにも引き継ぐので、値を取りこぼしません。Android と web の codegen は、
  [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md) と
  BE-0276 の前例に合わせ、ラベル付きの `// TODO` を生成します。どちらのバックエンドのネイティブな
  テストフレームワークも表現できないフィールドだからです。`permissions` は bajutsu 自身が起動前に
  すべてのバックエンドでフィールドを適用しますが、`handleSystemAlert` は mid-flow のステップであり、その
  ネイティブなイディオムを表現できるのは iOS（XCUITest）のフレームワークだけです。両者の扱いが分かれる
  のはこのためです。
- **ドキュメントとフィクスチャ。** [`docs/scenarios.md`](../../docs/scenarios.md) とその日本語版、
  DSL 文法にこのステップを記載します。`docs/scenarios.md` にはすでに「dismissAlerts（システムアラート
  ガード）」の節があるので、新しいステップの記述では、読み手が両者を混同しないよう役割の違いを明示的に
  対比します。`dismissAlerts` はステップがすでにブロックされたときにだけ発火する反応的で AI 駆動の
  *ガード*であり、`handleSystemAlert` は著者がプロンプトを見込む地点に置く決定的でネイティブな*ステップ*です。
  どちらも同じ種類の OS レベルのアラートを正反対の仕組みで操作するので、その対比（反応的なガードと
  決定的なステップ）を述べて、読み手が誤って一方に手を伸ばさないようにします。フィクスチャは既存の
  `permission.yaml` に足すのではなく、
  独立した新しいシナリオファイル（`demos/showcase/scenarios/permission_system_alert.yaml`）として
  追加します。`permission.yaml` の通知シナリオはすでに `ai` タグを持ち、iOS の smoke レーンはそれを
  `--exclude ai` で除外できますが、Android の `smoke (adb)` ジョブはこのファイルのシナリオを一切
  除外せずそのまま実行します。同じファイルに `handleSystemAlert` を使うシナリオを足すと、iOS（XCUITest）
  バックエンドしかこのケーパビリティを宣言していないため、Android の必須ジョブが preflight で
  落ちてしまいます。ファイルを分ければこれを避けられ、境界は1つのタグではなく仕組みで分かれます。
  Android の `smoke (adb)` ジョブは `demos/showcase/android/Makefile` の**固定のシナリオ一覧**を
  実行するので、その一覧が名指ししないファイルはそこで拾われません。フィクスチャを Android レーンから
  外すのはタグではなくファイル分離です。新しいシナリオは通知の認可を mid-flow でリクエストし
  `handleSystemAlert` 経由で "Allow" をタップするものです。これはローカルの一括実行 `run-swiftui`/
  `run-uikit`（すべてのシナリオファイルを走査し、権限状態をシナリオごとにリセットしないので、ステップが
  必要とする `notDetermined` のプロンプトが出るとは限りません）からも外す必要があります。そのためには、
  `tabs.yaml` の `xcuitest` タグを流用するのではなく、**専用**の除外タグ（例：`systemalert`）を付け、
  この2つのターゲットの `--exclude` に加えます。`xcuitest` は、退役した idb バックエンドに `[ios]` が
  解決し得た時代に由来する legacy な切り分け（`tabs.yaml` のヘッダと `demos/showcase/Makefile` を参照）で、
  文書上も撤去候補です。これに相乗りすると、そのタグが撤去された日にこのフィクスチャが静かに除外解除
  されてしまいます。目的を表すタグにすれば、フィクスチャの除外はこの legacy split の撤去から独立します。
- **CI への組み込み。** タグを付けるだけでは、CI のジョブにファイルが追加されるわけではありません。
  `ios-e2e.yml` の各ジョブは、実行するシナリオファイルをすべて名指しで指定しており、ディレクトリを
  走査したりタグで対象を選んだりする仕組みは CI のどこにもありません。すでに `permission.yaml` を
  専用のステップで実行している `xcuitest (multi-touch)` ジョブに、
  `scenarios: demos/showcase/scenarios/permission_system_alert.yaml` を指定する新しいステップを
  明示的に加えます。このジョブの Simulator・アプリ・ランナーのビルドはすでに済んでいるので、新しい
  ジョブを立てるのではなく `bajutsu run` をもう1回走らせるだけで済みます。
- **テスト。** スキーマのパース・検証（ラベルベースのフィールドは受け入れ、`id`/`idMatches`/`traits`/
  `value`/`within` は明確なメッセージ付きで拒否する）。サブセットだけを宣言するバックエンドに対する preflight
  （iOS は通過し、Android と web は個別に名指しされて即座に失敗する）。XCUITest ランナーの SpringBoard
  解決を、一致がゼロ・1つ・複数のそれぞれのボタン一覧に対して検証する。codegen のスニペット。

## 検討した代替案

- **`dismissAlerts` を任意のタイミングで mid-step に発火させられるようにする。** 却下しました。
  `dismissAlerts` の反応的な設計は、ステップがすでにブロックされたときにだけ発火し、通過するシナリオ
  では一度もモデルを呼び出しません（[`docs/scenarios.md`](../../docs/scenarios.md)）。この不変条件
  自体が `dismissAlerts` の価値の一部です。著者が選んだ地点で発火させようとすると、ブロックされた
  ステップを偽装するか、それを使うすべてのシナリオで「通過するシナリオではモデルを一切呼ばない」
  という保証を崩すかのいずれかです。専用のステップにすれば、`dismissAlerts` 側の保証はそのまま
  保たれ、`handleSystemAlert` 自身も決定的で、`steps` の中に現れた地点で無条件に働くという、より単純な契約を
  持てます。
- **一般の `Selector` に `system: true` のようなスコープを足し、`tap` / `wait` / `assert` が直接
  SpringBoard へ届くようにする。** 本項目としては却下しました。あらゆるステップとアサーションは、1つの
  共有された `Selector` 解決経路を通じて解決されます。シナリオの中の1箇所だけで生じるニーズのために、
  SpringBoard とアプリの区別をそのすべてに通すと、必要性に見合わない範囲まで変更が広がります。専用の
  `handleSystemAlert` アクションであれば、変更を1つの新しいステップ実装だけに閉じ込められ、他のあらゆる
  ステップがすでに依存しているセレクタ解決経路には影響しません。
- **権限プロンプトに限らず、SpringBoard レベルのアラート全般（パスワード保存の確認、ペーストの許可、
  クラッシュシートなど）を対象とする。** 見送りました。権限プロンプトは、request フローをテストする
  シナリオの著者が実際に直面する、具体的でよくあるケースです。SpringBoard へのクエリという仕組み自体
  は、アラートの出どころを区別しないので、後から `handleSystemAlert` を任意の SpringBoard アラートへ広げる
  道を本項目が閉ざすわけではありません。最初のバージョンを権限プロンプトに絞ることで、この提案を
  レビューして一度に取り込める大きさに保ちます。
- **視覚ガードが求めた座標を1回だけキャッシュして使い回す。** 却下しました。それでも最低1回はスクリー
  ンショットとモデルの往復が必要で、Claude の API キーも要ります。キャッシュした座標が正しいままである
  保証もありません。デバイスの向き、ロケール、iOS のバージョンが変わればアラートのボタンは動きえます。
  ネイティブなアクセシビリティクエリなら、そうした前提を置かずに、そのつどボタンの現在位置を解決でき
  ます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] シナリオスキーマ — `handleSystemAlert` ステップ、ラベルベースの `Selector` サブセット、必須の `timeout`。
- [ ] iOS（XCUITest）ランナー側の対応 — オンデマンドの SpringBoard `XCUIApplication` ハンドル、ネイティブタップ。
- [ ] アラートボタンの一致がゼロまたは複数のときに即座に失敗させる。
- [ ] ケーパビリティトークン + preflight（iOS のみが宣言）。
- [ ] codegen — iOS ではネイティブな XCUITest のイディオム、Android と web ではラベル付き `// TODO`。
- [ ] ドキュメント（scenarios.md + 日本語版、DSL 文法）と、新しい iOS 専用の showcase フィクスチャ
      ファイル（`permission_system_alert.yaml`）。Android の `smoke (adb)` ジョブがそのまま実行する既存の
      `permission.yaml` には決して足しません。専用の除外タグ（例：`systemalert`）で、legacy な `xcuitest`
      タグに相乗りせずローカルの一括実行 `run-swiftui`/`run-uikit` から外します。
- [ ] CI への組み込み — `ios-e2e.yml` の `xcuitest (multi-touch)` ジョブに、新しいフィクスチャ用の
      明示的な `scenarios:` ステップを追加します。タグを付けるだけでは CI のジョブには何も加わりません。
- [ ] テスト — スキーマ、preflight、SpringBoard 解決（ゼロ/1つ/複数の一致）、codegen のスニペット。

## 参考

- [BE-0276 — シナリオ単位の宣言的な権限状態](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) ——
  反応的なガードに対する起動前の決定的な対の仕組み。BE-0276 は mid-flow の命令的な状態設定ステップ
  （`simctl privacy`）を見送っており、これは本項目のプロンプト tap とは別の仕組みです。
- [BE-0128 — デバイス制御ステップの preflight ゲート](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight-ja.md) ——
  本項目が従う operation 単位の preflight パターン。
- [BE-0026 — 非対応構文の縮小](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md) ——
  バックエンドのネイティブなフレームワークが表現できないステップに対する、ラベル付き `// TODO` という codegen の前例。
- `bajutsu/agents/alerts.py`、`docs/scenarios.md`（`dismissAlerts` の節） —— 本項目が補完する、既存の反応的な視覚ガード。
- `BajutsuKit/Runner/Sources/RunnerUITest.swift` —— 本項目が SpringBoard スコープの第2のハンドルで拡張する、既存の `XCUIApplication(bundleIdentifier:)` の構築部分。
- 対の提案 `rename-dismiss-alerts-to-alert-handling`（`alertHandling`） —— 本項目が並ぶ相手であり、本項目が終始参照する反応的な `dismissAlerts` ガードを改名する提案。ここで `dismissAlerts` を使う読み手は、この名前が非推奨になり `alertHandling` へ改名されることを知っておくべきです。2つの提案のドキュメントは、反応的なガードと明示的なステップを1箇所で対比します。

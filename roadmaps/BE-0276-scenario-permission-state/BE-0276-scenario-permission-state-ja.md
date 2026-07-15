[English](BE-0276-scenario-permission-state.md) · **日本語**

# BE-0276 — シナリオ単位で宣言する権限状態（simctl privacy / pm grant）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0276](BE-0276-scenario-permission-state-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0276") |
| トピック | シナリオ記述機能 |
| 関連 | [BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md)、[BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities-ja.md)、[BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md) |
| 由来 | Maestro |
<!-- /BE-METADATA -->

## はじめに

OS の権限状態を、アプリの起動前にシナリオ単位で宣言的に設定する仕組みです。あらかじめ権限を許可
または拒否しておけば、実行時のプロンプトそのものが出ません。iOS では `simctl privacy` を、Android では
`pm grant` / `pm revoke` を駆動します。既存の視覚ベースの**アラートガード**（[`dismissAlerts`](../../docs/ja/scenarios.md)）
に対して、決定論的で AI を使わない補完手段になります。事前に分かっている権限を設定しておけば、消すべき
プロンプトが最初から現れません。

## 動機

権限プロンプト（位置情報、カメラ、連絡先など）は SpringBoard やシステム UI に属します。プロセスの外に
あるため、idb のアプリスコープな query からは見えず、タップもできません。現在 iOS でこれを捌く手段は
「アラートガード」だけです。ステップがブロックされると、`run` は画面のスクリーンショットを撮り、どこを
タップするかを Claude に尋ねてプロンプトを消します（[`bajutsu/alerts.py`](../../bajutsu/alerts.py)）。これは
`run` に残る唯一の AI 経路であり、プロンプトが想定外のときにこそ適した道具です。権限があらかじめ分かって
いる場合は、その状態を先に設定するほうが優れています。

- **決定論的な経路から LLM 呼び出しを取り除けます。** 事前の付与は `simctl` や `adb` の単純な副作用で、
  結果は機械的に検証できます。スクリーンショットもモデルも `ANTHROPIC_API_KEY` も要りません。第二の AI 面を
  増やすのではなく、「アラートガード」への依存を減らす方向に働きます（prime directive 1）。
- **タップだけでなく状態そのものを決定論的にできます。** 「アラートガード」は現れたプロンプトに反応することしか
  できません。拒否経路のフローを試すために権限を revoke することはできず、アプリが既知の権限状態から始まる
  保証も与えられません。事前設定はどちらも実現します。
- **シナリオ単位で指定できます。** あるシナリオは要求と許可のフロー自体を検証し（権限は未設定のまま、
  「アラートガード」か実際のタップで許可する）、別のシナリオは権限を許可済みにして検証対象の機能へ直接進みたい、
  という違いが生じます。この違いはシナリオの性質なので、シナリオファイルに置くのが自然です。

競合（Maestro）との対比という点でも位置づけられます。Maestro は `setPermissions` を標準で備えており、
[BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md)
は device-state ファミリの残りを出荷する際、これを table-stakes のギャップとしてすでに記していました。
本項目はそのギャップを埋めます。差別化はその実現方法にあります。単一の宣言的なクロスプラットフォームの
フィールドを、各 backend が自分のネイティブな機構へマッピングし、決定論性とアプリ非依存性を保ちます。

クロスプラットフォームの土台もすでにあります。Android は lease 時に適用する config レベルの
`grantPermissions` リストで、実行時権限を事前付与しています
（[BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md)。呼び出し箇所は
`bajutsu/platform_lifecycle/environments/android.py`、実装は `bajutsu/adb.py` の `Android.grant_permissions`
です）。ただしこれはアプリ単位でシナリオ単位ではなく、Android 専用です。本項目は
両プラットフォームを束ねるシナリオ単位のフィールドを一つ導入します。対象が iOS シミュレータでも Android
エミュレータでも、同じフィールドが動きます。

## 詳細設計

新しいシナリオレベルのフィールドです（起動前に一度だけ適用する宣言的なもので、フロー途中のステップでは
ありません。`dismissAlerts` と同じ形です）。想定する記法は次のとおりです（最終的な名前は採用時に確定します）。

```yaml
scenario:
  name: "profile — camera already granted"
  permissions:
    camera: grant
    location: grant
    contacts: revoke
  steps:
    - ...
```

各エントリは `<service>: grant | revoke` です。このフィールドは runner がアプリプロセスの起動前に適用
します。そのため、アプリが最初の要求を出す前に権限状態が整います。事前付与でプロンプトを防げるのは、この
時点だけです。

### 共通語彙と backend ネイティブへのマッピング

シナリオが指定する対象は、backend 非依存の小さな語彙にまとめた**権限項目**です（`location`、`camera`、
`microphone`、`contacts`、`photos`、`calendar`、`notifications` など。YAML 上のプレースホルダは `<service>`
と書きます）。各 backend はこの権限項目を自分のネイティブな識別子へマッピングし、どれを扱えるかを宣言
します。マッピングのない権限項目は、実行時ではなく preflight で明快に失敗します（後述）。

- **iOS** → `simctl privacy <udid> <grant|revoke> <tcc-service> <bundle>`。iOS はこれを
  TCC（Transparency, Consent, and Control。プライバシー許可を保持する OS 側のデータベースです）経由で
  操作します。たとえば `location → location`、`camera → camera`、`contacts → contacts` とマッピングします。
- **Android** → `pm grant|revoke <package> <android.permission.*>`。既存の config レベル `grantPermissions` の
  裏側の仕組みを再利用します。

### カバレッジの正直さ（通知）

`simctl privacy` には通知に対応する権限項目がありません（iOS の通知認可は TCC の対象ではないためです）。
そのため iOS の backend は `notifications` を非対応と宣言します。これを列挙したシナリオは preflight で
失敗し、通知プロンプトの経路として `dismissAlerts` を指し示す明快なメッセージを出します。Android の
`POST_NOTIFICATIONS` は実行時権限（`pm grant`、API 33 以降）なので、Android は対応します。共通語彙は、
提供できない一様な面を装うのではなく、この非対称を正直に扱います。

### 作業分解（MECE）

1. **シナリオスキーマと語彙**：`permissions` フィールド（権限項目から `grant`/`revoke` へのマップ）を
   追加し、parse と検証を行い、共通の権限項目の enum を定義します。未知の権限項目や、`grant`/`revoke`
   以外の値は parse 時に拒否します。
2. **capability トークンと preflight**：
   [BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities-ja.md)
   の型に倣い `deviceControl.permissions` トークンを追加します。`bajutsu/capability_preflight.py` でフィールドを
   このトークンへマッピングし、要求された各権限項目をゲートします。非対応の権限項目（たとえば iOS の
   `notifications`）は、
   [BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight-ja.md)
   がすでに集約する preflight メッセージのなかで個別に名指しされます。
3. **iOS backend**：`simctl privacy` の純粋なコマンドビルダー（既存の `setLocation` / `push` のビルダーと
   同様）を用意し、注入可能な `RunFn` 経由で実行します。権限項目から TCC へのマップと、対応する権限項目
   （`notifications` を除く全部）の宣言も含みます。
4. **Android backend**：`pm grant|revoke` へマッピングし、`grantPermissions` の機構
   （`bajutsu/adb.py` の `Android.grant_permissions`。呼び出し箇所は
   `bajutsu/platform_lifecycle/environments/android.py`）を再利用します。対応する権限項目
   （`notifications` を含む）を宣言します。
5. **起動前適用の配線**：run-loop / lease 経路で、アプリプロセスの起動前にシナリオごとに一度、このフィールドを
   適用します。固定 sleep は入れません。効果はコマンドの終了と同期します。
6. **codegen**：アプリレベルの XCUITest / Espresso に相当物がないため、codegen はフィールド名を記した
   `// TODO` を出します。
   [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md) や BE-0052 と同じ扱いです。
7. **ドキュメントとフィクスチャ**：[`docs/scenarios.md`](../../docs/scenarios.md) と日本語ミラー、DSL 文法に
   フィールドを記載し、Permissions タブが本来プロンプトを出す権限を事前付与する showcase シナリオを追加します。
8. **テスト**：スキーマの parse と検証、部分対応の backend に対する preflight（対応する権限項目は通り、
   非対応は名指しで即座に失敗する）、fake `RunFn` に対するコマンドビルダーの検証、未知の権限項目での
   明快な失敗を確認します。

### prime directive の維持

- **決定論性。** 機械的に検証できる結果を伴う決定論的なデバイス変更です。settle 用の `sleep` も LLM も
  ありません。run / CI ゲートは AI を含まないままで、しかもこの経路は「アラートガード」がしていた LLM
  呼び出しを減らします。
- **アプリ非依存。** アプリごとのコードはありません。権限項目の語彙とマッピングはツールと backend に置き、
  どの権限を設定するかというシナリオごとの選択はシナリオに置きます。アプリのコードには置きません。
- **fail fast。** 非対応の権限項目は run の途中ではなく preflight で捕まえます。

## 検討した代替案

- **config レベルのみ（Android の `grantPermissions` に倣う）。** 主たる形としては却下します。許可するか
  未設定にするかの選択は、同じアプリのシナリオ間で異なります（一方は要求フローを検証し、他方は事前付与を
  求める）。そのためアプリ単位の config 値では表現できません。既存の Android の config フィールドはアプリ全体の
  既定として残り、本項目のシナリオ単位フィールドはその上に重なります。
- **`dismissAlerts` を拡張して「Allow」をタップする。** `dismissAlerts: { instruction: "tap Allow" }` はすでに
  存在しますが、これは AI の視覚ベースの経路です（決定論的でなく、キーが要る）。プロンプトが現れた後にしか
  反応できず、revoke も、起動前の既知状態の確立もできません。却下します。本項目が LLM を経路から外そうと
  しているのに、それを経路に残してしまうからです。
- **アプリごとの launch env やデバッグ用 deeplink。** アプリの内側から状態を近似すると、その負担が対象アプリ
  すべてに乗り、アプリ非依存性を壊します。BE-0052 がこれを却下したのと同じ理由です。launch env は真にアプリ
  固有のセットアップ向けに残ります。
- **iOS 専用のプリミティブ。** 検討しましたが、device-control の層はもともと backend 非依存に設計されており、
  Android には `pm grant` の下地がすでにあります。単一のクロスプラットフォームフィールドのほうが、二つに
  分かれた面よりも長期的に小さく収まります。
- **命令的な `setPermissions` ステップ（フロー途中）。** 見送ります。TCC や権限の状態はアプリの最初の要求より
  前に設定する必要があるため、起動前の宣言的フィールドが要になります。フロー途中のステップ（一つのシナリオ内で
  revoke して拒否経路を再度たどる、など）は将来の拡張候補であり、本項目には含めません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] シナリオスキーマと共通の権限項目の語彙（`permissions` フィールド、parse / 検証）。
- [ ] capability トークン `deviceControl.permissions` と権限項目単位の preflight マッピング。
- [ ] iOS backend：`simctl privacy` コマンドビルダー、権限項目から TCC へのマップ、対応する権限項目の宣言。
- [ ] Android backend：`grantPermissions` を再利用した `pm grant|revoke` マッピング、対応する権限項目の宣言。
- [ ] run-loop / lease 経路での起動前適用の配線。
- [ ] フィールド向けの codegen ラベル付き TODO。
- [ ] ドキュメント（scenarios.md と日本語版、DSL 文法）と showcase フィクスチャ。
- [ ] テスト：スキーマ、部分対応 backend の preflight、コマンドビルダー、未知の権限項目での明快な失敗。

## 参考

[BE-0052 — Device-state primitives](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md)
が記した `setPermissions` のパリティギャップを埋めます。
[BE-0212 — 粗い deviceControl 能力を操作単位のトークンに分割する](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities-ja.md)
の操作単位の capability トークンと、
[BE-0128 — Preflight-gate device-control steps](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight-ja.md)
の preflight ゲートの上に築きます。Android 側は
[BE-0210 — Android actuation fidelity](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md)
の `grantPermissions` / `pm grant` の機構を再利用します。視覚ベースの「アラートガード」（`bajutsu/alerts.py`、
[`dismissAlerts`](../../docs/ja/scenarios.md)）を補完します。
[DESIGN §6.1](../../DESIGN.md)、`bajutsu/orchestrator/actions/handlers/device.py`

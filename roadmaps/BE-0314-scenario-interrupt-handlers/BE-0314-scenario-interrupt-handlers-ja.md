[English](BE-0314-scenario-interrupt-handlers.md) · **日本語**

# BE-0314 — 出現タイミングの読めない割り込み画面を決定論的に処理する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0314](BE-0314-scenario-interrupt-handlers-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0314") |
| 実装 PR | _未定_ |
| トピック | シナリオ記述機能 |
| 関連 | [BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md)、[BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md)、[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md)、[BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`interrupts` は、実行のどの時点で現れるか予測できない画面（オンボーディングの案内、チュートリアルの
オーバーレイ、アクセシビリティツリーから見える許諾ダイアログなど）と、それを解消するための手順を宣言的に
登録するフィールドです。config 側でアプリ全体の既定として設定し、シナリオ側でさらに追加できます。ランナーは
各エントリの `condition` を、`if`（[BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md)）
がすでに使っているのと同じ決定論的なアサーション DSL（ドメイン固有言語）で、ステップ実行のために取得済みの
要素ツリーへ機会的に判定します。ステップ列のどこで割り込み画面が現れても捕捉できるため、著者は「どのステップの
直後に確認を置くか」を予測する必要がなくなります。

## 動機

Bajutsu にはすでに、現在の画面で分岐する仕組みがあります。`if`（`_run_if`、
[`bajutsu/orchestrator/loop.py:472`](../../bajutsu/orchestrator/loop.py)）は `condition` を1回の
`driver.query()` に対して評価し、`then` か `else` を実行します。この一発判定は、対象の画面がどのステップの
直後に現れるかを著者があらかじめ知っている場合には十分です。しかし、画面の出現が特定のステップに紐付いて
いない場合には向きません。許諾ダイアログやアプリ固有のオンボーディング画面、プロモーションのオーバーレイは、
アカウントの状態やネットワークの応答時間、A/B の割り当てによって出現タイミングが揺れます。想定より数ステップ
早く出ることもあれば、遅く出ることも、まったく出ないこともあります。特定のステップの後に `if` を1つ置くだけでは、
割り込み画面がたまたまそこに現れたときしか捕捉できません。それ以外のタイミングでは素通りしてしまい、以降の
シナリオは想定していない画面に対して失敗します。

このギャップは仮説ではなく、既存のロードマップ項目がすでに具体的に記録しています。
[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) は、アプリの
初回リクエストより前に OS の権限を許可・拒否しておく決定論的な `permissions` フィールドを追加しましたが、
その仕組みが届かない権限を自ら明記しています。iOS の通知許諾には `simctl privacy` に対応する TCC
（Transparency, Consent, and Control。iOS のプライバシー許諾を管理する仕組み）サービスが存在しません。
そのため BE-0276 は `dismissAlerts`（視覚ベースの「アラートガード」、[`bajutsu/agents/alerts.py`](../../bajutsu/agents/alerts.py)）
に頼るしかないと述べています。「アラートガード」は `run` に意図的に残された唯一の AI 呼び出しであり
（prime directive 1: AI は調査するが判定はしない）、ステップがすでに失敗したあと、または待機がすでに
ブロックされて見えるときにしか働きません。アクセシビリティツリーから実際に見える画面を決定論的かつ機械的に
検証可能な方法で処理したくても、画面の出現がステップに紐付いていないという理由だけで、今はその処理を置く
場所がありません。

この修正の形は、すでに2つの既存の仕組みが別の層で示しています。`dismissAlerts` 自体が、config 側の既定値と
シナリオ側の上書きの両方を持つフィールドです（`dismiss_alerts`、
[`bajutsu/config/schema.py:368`](../../bajutsu/config/schema.py) と
[`bajutsu/scenario/models/scenario.py:104`](../../bajutsu/scenario/models/scenario.py)）。そのため、
「このアプリは常に画面 X を出す」というアプリ全体の想定に、シナリオ固有の追加を重ねられます。また
[BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md)
は、ポーリングのために取得済みのツリーに対する画面崩壊チェックを、追加のクエリなしに乗せられることをすでに
示しています。回数上限を設けた介入が、待機を打ち切らずに再開できることも同時に示しています。この codebase の
外でも、XCUITest 自身が同じパターンに独立して行き着いています。`addUIInterruptionMonitor` は、テストのどの行が
きっかけであっても、画面操作がブロックされるたびにテストフレームワークが参照するハンドラを登録する仕組みです。
本項目は、この「config・シナリオ側で登録し、1箇所の決められた地点ではなく継続的にチェックする」という形を、
Bajutsu の driver がすでに見えているツリーに対して持ち込みます。チェック自体はアサーション DSL による決定論的な
ものにとどめ、AI の経路を新たに増やしません。

## 詳細設計

### `interrupts` フィールド

```yaml
# config.yaml — アプリ全体の既定: このアプリのオンボーディング/チュートリアル画面
targets:
  myapp:
    interrupts:
      - condition: { exists: { id: onboarding.skip } }
        steps:
          - tap: { id: onboarding.skip }
```

```yaml
# scenario.yaml — このシナリオ固有の追加(config 側のリストに連結される)
scenario:
  interrupts:
    - condition: { exists: { id: att.dialog } }        # トラッキング許諾(ATT)ダイアログ
      steps:
        - tap: { id: att.allow }
  steps:
    - tap: { id: login.button }
    - wait: { for: { id: home.title }, timeout: 10 }
```

各エントリは、`if` がすでに使っている `condition`（[アサーション DSL](../../docs/ja/scenarios.md) の
`exists` や `value` など）と、条件が一致したときに実行する `steps` の組です。config レベルの `interrupts`
はアプリ全体の既定で、シナリオ側の `interrupts` はそこへ追加されます（config 側のエントリを先に評価します）。
これは、`dismissAlerts` がすでに config 側の既定をシナリオ側の値の下に重ねているのと同じ構造です。エントリの
`steps` は、`if` の `then`/`else` と同様、シナリオ本体と同じ `vars.*` を共有します。

### 作業分解（MECE）

1. **シナリオと config のスキーマ。** `Config`（[`bajutsu/config/schema.py`](../../bajutsu/config/schema.py)）
   と `Scenario`（[`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)）の
   両方に `interrupts: list[Interrupt]` を追加します。各エントリは
   `{ condition: Assertion, steps: list[Step] }` として検証します。
   [`bajutsu/scenario/models/steps.py:71`](../../bajutsu/scenario/models/steps.py) の `If` がすでに
   使っている `Assertion` と `Step` のモデルをそのまま再利用します。実行時に有効となるリストは、config 側の
   エントリのあとにシナリオ側のエントリを連結したものです。これは `_alert_guard_factory` の `_enabled` が
   `dismiss_alerts` に対してすでに適用している、config 優先でシナリオ側が上書きするのと同じ順序です
   （[`bajutsu/cli/commands/run.py:339`](../../bajutsu/cli/commands/run.py)）。
2. **取得済みのツリーに乗せる機会的チェック。** 有効化した `interrupts` リストを `_run_steps`
   （[`bajutsu/orchestrator/loop.py:513`](../../bajutsu/orchestrator/loop.py)）に渡し、各エントリの
   `condition` を、ループがすでに保持しているツリーに対して評価します。`screenChanged` ポリシーのために
   すでに取得している `before`/`after` のクエリ、あるいは `_wait`
   （[`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py)）の各ポーリング tick で
   すでに取得済みのポーリング結果です。キャプチャポリシーがなくツリーを取得しない種類のステップ（素の
   `tap`/`type` など）だけは、act の直前に追加で1回 `query()` を行います。この追加コストは `interrupts`
   を1件以上宣言したシナリオにのみ発生し、`if` が自身の一発判定のために今すでに払っているコストと同じです。
3. **一致したらエントリの `steps` を実行し、元のステップを再開する。** `if` と `forEach` がすでに閉じ込めて
   いる同じ `_ExecSteps` 呼び出し可能オブジェクト
   （[`bajutsu/orchestrator/loop.py:452`](../../bajutsu/orchestrator/loop.py)）を再利用し、一致したエントリの
   `steps` を実行します。実行後は、割り込まれたステップを中断した地点からそのまま再開します。`wait` は元の
   `deadline` に向けてポーリングを続けます（BE-0269 が「アラートガード」向けに確立した「タイムアウトを立て
   直さずに再開する」という契約と同じです）。act 系のステップは自身の動作を1回だけ再試行します。エントリ自身の
   `steps` によって `condition` が真から偽に変わった場合（割り込み画面を実際に解消できた通常のケース）は、
   再開後にそのまま普段どおり進行します。
4. **再入回数の上限。** あるエントリの `steps` を実行しても、その `condition` が実際には解消されない場合
   （セレクタの誤りや、同じ画面が再描画され続ける場合）に、実行全体が無限ループに陥ってはなりません。同一
   エントリが1つのステップの解決の中で連続して発火できる回数に小さな固定上限を設けます。上限に達したら、
   元のステップの通常の結果（成功・失敗・タイムアウト）にそのままフォールバックします。BE-0269 の
   `_GUARD_MAX_ATTEMPTS` / `_GUARD_COOLDOWN` と同じ形を踏襲することで、設定を誤った条件は実行を止まらせず、
   そのステップを明確に失敗させます。
5. **codegen。** 「このシナリオ全体を通じて条件を機会的にチェックし続ける」という挙動に対応するネイティブな
   XCUITest・Espresso・Playwright の構文はありません。
   [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md) と BE-0276 が
   すでにアプリレベルの対応物を持たないフィールドに対して行っているのと同じ形で、フィールド名と各 `condition`
   を明記したラベル付きの `// TODO` を出力します。
6. **ドキュメントとフィクスチャ。** [`docs/ja/scenarios.md`](../../docs/ja/scenarios.md) とその英語版に、
   `dismissAlerts` や `permissions` と並べて `interrupts` を記載します。`if`（出現地点が既知）、`interrupts`
   （出現地点が読めず、ツリーから見える）、`dismissAlerts`（プロセス外、AI vision）、`permissions`（プロンプト
   そのものを出さない）のどれを使うべきかを対比する表を添えます。config にオンボーディング画面の割り込みを
   宣言し、それがフローの途中で現れる場合を演習する showcase フィクスチャを追加します。
7. **テスト。** 両階層でのスキーマの読み込み・検証、config が先でシナリオが後という連結順序、`wait` の
   ポーリング途中と素の `tap` の途中それぞれで条件を真に切り替える fake driver、再入上限が発火してきれいに
   フォールバックすること、シナリオ本体とエントリの `steps` の間で `vars.*` が共有されることを検証します。

### prime directive の遵守

- **AI は判定しない。** `condition` は既存のアサーション DSL であり、機械的に検証可能な述語であって、
  モデル呼び出しではありません。本項目は新しい AI 面を増やすのではなく、本来であれば視覚ベースの
  `dismissAlerts` に頼るしかなかったケースに、決定論的な経路を与えます。
- **決定論を優先する。** 固定の `sleep` は使いません。チェックはループや `wait` がすでに行っている tick に
  乗せるだけで、再入回数の上限が、設定を誤ったエントリを実行のハングではなく明確な失敗にとどめます。
- **アプリ非依存を保つ。** `interrupts` のリストそのものは config・シナリオ側のデータであり、ランナーと
  driver 側に増えるのは1つの汎用的な仕組みだけで、アプリ固有のコードは増えません。

## 検討した代替案

- **`if` に `timeout` を足し、分岐前にポーリングさせる。** 検討の出発点となった案で、`if` を「待ってから
  分岐する」仕組みに変えることはできます。しかしこの案でも、著者はステップ列のどこか1箇所にその `if` を
  置く必要が残り、本来の問題を解決しません。ステップ列に対する出現タイミングが予測できない割り込み画面には、
  そもそも正しい設置場所が存在しないからです。この理由により主案としては採用せず、設置場所が実際にわかって
  いる場合向けに、`if` は現状のより単純な一発判定のままとします。
- **複数の候補条件をレースさせる、ただし依然としてステップ列の1箇所に置く新しい `switch` 系ステップ。** 素の
  `if` よりは近いアプローチで、複数の画面のうちどれかが現れうる地点を著者が具体的に把握している場合には
  有用です。しかし設置場所の問題は `if` の拡張と同じく残ります。著者が正しい地点を推測する必要が依然として
  あるからです。見送りとし、本項目が提案する config・シナリオ単位の `interrupts` の仕組みに、局所的な補完
  として後から追加することを妨げるものではありません。
- **`dismissAlerts` をツリーから見える画面にも対応させる。** 見送りとします。`dismissAlerts` は、
  アクセシビリティツリーからまったく見えない画面のためにこそ意図的に残された、`run` 唯一の AI vision 経路
  です。ツリーから見える画面には `condition` を `query()` に対して評価するという決定論的な信号がすでに
  あります。それでも視覚ガードを経由させれば、本項目が取り除こうとしているコストと非決定性をわざわざ持ち込む
  ことになります。
- **著者による復旧手順を伴わない、アプリ単位の「想定される追加画面」の無言の許可リスト。** 見送りとします。
  一致した画面を（著者が指定した `steps` を実行する代わりに）ただ無視するだけでは、画面を実際に解消する手段を
  放棄することになります。許可リストが想定外の何かを覆い隠していた場合に、何が起きたかを説明する証跡も
  残りません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] Unit 1 — シナリオと config のスキーマ（`interrupts: list[Interrupt]`）、config 側を先にする
      連結順序。
- [x] Unit 2 — `_run_steps` と `_wait` で、取得済みのツリーに乗せる機会的な条件チェック。
- [x] Unit 3 — 一致時に既存の `_ExecSteps` の仕組みでエントリの `steps` を実行し、割り込まれたステップを
      再開する（wait は同じ deadline へ、act 系は1回だけ再試行）。
- [x] Unit 4 — エントリごとの再入回数上限と、ステップの通常の結果へのきれいなフォールバック。
- [x] Unit 5 — codegen のラベル付き TODO。
- [x] Unit 6 — ドキュメント（scenarios.md および英語版）に `if`・`interrupts`・`dismissAlerts`・
      `permissions` の対比を加え、showcase フィクスチャを追加する。
- [x] Unit 7 — テスト（スキーマ、連結順序、wait と act それぞれでの一致、再入上限、`vars.*` の共有）。

## 参考

- [`bajutsu/orchestrator/loop.py:472`](../../bajutsu/orchestrator/loop.py) — `_run_if`。本項目が
  置き換えるのではなく補完する、既存の一発判定の条件分岐です。
- [`bajutsu/scenario/models/steps.py:71`](../../bajutsu/scenario/models/steps.py) — `If`。
  `condition`/`then`/`else` のモデルで、本項目の `interrupts` エントリはその `condition` の形を
  再利用します。
- [`bajutsu/config/schema.py:368`](../../bajutsu/config/schema.py) と
  [`bajutsu/scenario/models/scenario.py:104`](../../bajutsu/scenario/models/scenario.py) — 既存の
  「config 側の既定にシナリオ側の上書きを重ねる」構造（`dismiss_alerts`）で、本項目の `interrupts`
  フィールドはこれを踏襲します。
- [`bajutsu/agents/alerts.py`](../../bajutsu/agents/alerts.py) — `SystemAlertGuard`。プロセス外のシステムアラートに
  対する視覚ベースの反応的なガードで、`interrupts` は意図的にこれを置き換えません。
- [BE-0033 — シナリオ変数 + 軽い制御フロー](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md) —
  `if`/`forEach` と、アサーション DSL を条件として使うパターンを導入した項目で、本項目はこれを基盤とします。
- [BE-0269 — wait ステップ中のシステムアラートガードの介入を早める](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention-ja.md) —
  追加のクエリなしで、デバウンスと上限付きの介入を待機の途中に差し込む形で、本項目の Unit 2〜4 は
  この形をヒューリスティック（ツリーの崩壊）から明示的な `condition` へと転用します。
- [BE-0276 — シナリオ単位で宣言する権限状態（simctl privacy / pm grant）](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) —
  `dismissAlerts` を補う、起動前の決定論的な仕組みです。iOS の通知許諾には `simctl privacy` が届かないと
  明記されたそのギャップが、本項目の具体的な動機になっています。
- Apple の [`XCTestCase.addUIInterruptionMonitor(withDescription:handler:)`](https://developer.apple.com/documentation/xctest/xctestcase/adduiinterruptionmonitor(withdescription:handler:))
  — 継続的にチェックされる登録ベースの割り込みハンドラという形の外部の先例として挙げるものであり、
  本項目が依存する仕組みではありません。
- [BE-0310 — アクセシビリティの画面遷移通知で readiness と settled 待ちの判定をより正確にする](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md) —
  関連はしますが別の改善です。画面遷移が完了したことを検知する精度を高める項目であり、本項目は
  特定の割り込み画面をどこに現れても認識し処理することが主題です。

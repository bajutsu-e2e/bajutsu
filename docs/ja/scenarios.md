[English](../scenarios.md) · **日本語**

# シナリオ仕様（オーサリングリファレンス）

[シナリオ](glossary.md#シナリオのオーサリング)は Bajutsu が永続化する **唯一の成果物**です。プレーンな YAML で書き、git でバージョン管理し、PR でレビューできます。最初の 1 回は `record`（AI）が書き、以後は人間が所有して編集します。`run` はこの構造を AI なしで実行します。

実装: `bajutsu/scenario/`（`models/` 配下の pydantic モデル。`extra="forbid"` で未知キーを拒否）。

すべての生成規則、型、既定値、検証規則を定めた **規範的な文法**は [dsl-grammar](dsl-grammar.md) にあります。このページはオーサリングガイドであり、例を使ってシナリオの書き方を示します。

関連: [cookbook](cookbook.md)（実例集） · [dsl-grammar](dsl-grammar.md)（形式文法） · [selectors](selectors.md)（セレクタとアサーションの評価方法） · [evidence](evidence.md)（証跡） · [run-loop](run-loop.md)（実行）

---

## ファイルの形

1 ファイルは **シナリオの配列**です。ファイルレベルの説明を付けたい場合は `{ description, scenarios }` のマッピングにします。`load_scenarios()` はどちらの形式も受け付けます（どちらでもないトップレベルは拒否されます）。

```yaml
- name: ...        # scenario 1
  steps: [...]
- name: ...        # scenario 2
  steps: [...]
```

ファイルレベルの説明（および任意の per-scenario `description`）を付ける場合は次のようにします。

```yaml
description: What this file covers.
scenarios:
  - name: ...
    description: What this scenario checks.
    steps: [...]
```

ファイルの説明と各シナリオの `description` は、`report.html`（サマリーヘッダーと各シナリオカード）および `bajutsu serve` の UI に表示されます。

### スキーマバージョン

マッピング形式では、トップレベルに整数の `schema` を置いて、シナリオスキーマのバージョンを示せます。`schema` を省いたファイルはバージョン 1 として扱うため、既存のシナリオはそのままで有効です。

```yaml
schema: 1
scenarios:
  - name: ...
    steps: [...]
```

実行中の `bajutsu` が理解できるものより新しい `schema` をシナリオが宣言している場合、読み込みは「未知のフィールド」という分かりにくいエラーではなく、明快なアップグレード手順のメッセージで失敗します。これは、バージョンをまたいでシナリオツリーを読み込むとき（たとえば、固定した Git の ref から config を取得したとき）に生じる状況です。現在のバージョンは `bajutsu/scenario/models/scenario.py` の `SCHEMA_VERSION` です。版上げは読み込みを壊す変更のときだけ行います。以前必須だったフィールドの意味を取り除く変更や、古い `bajutsu` が単に拒否するのではなく誤解してしまう変更が該当します。純粋に追加的なオプションフィールドは、版上げを必要としません。

## トップレベル構造（`Scenario`）

| キー | 型 | 既定 | 説明 |
|---|---|---|---|
| `name` | str | 必須 | シナリオ名（レポート / JUnit testcase / codegen のメソッド名に使う） |
| `description` | str | なし | 任意の説明文。シナリオの report カードと serve UI に表示 |
| `from` | str | なし | **来歴（provenance）**：`record` がこのシナリオを書き起こした元の自然言語ゴール（[来歴](#from来歴)）。オーサリング用のメタデータで、`run` は読みません |
| `tags` | list[str] | `[]` | 選択ラベル。CLI の `--tag` / `--exclude` で実行対象を絞る（[再利用とデータ駆動とタグ](#再利用とデータ駆動とタグ)） |
| `data` / `dataFile` | list / str | なし | データ駆動の行。インライン `data` か `dataFile`（CSV パス）で指定する。1 行 1 run に展開し `${row.col}` を置換する。両者は排他（[再利用とデータ駆動とタグ](#再利用とデータ駆動とタグ)） |
| `preconditions` | object | `{}` | テスト前の環境準備（下記） |
| `steps` | list | 必須 | アクションの並び（下記） |
| `expect` | list | `[]` | 全ステップ成功後の最終アサーション（[selectors](selectors.md#アサーション評価)） |
| `capturePolicy` | list | `[]` | 繰り返し発火する証跡ルール（[evidence](evidence.md#a-capturepolicyルール方式)） |
| `network` | object | なし | `{ filter: { domains: [...] } }`。`filter.domains` は、レポートの Steps タイムラインに差し込む通信を URL ホストで絞る（親ドメインはサブドメインに一致）。未指定なら全件を表示する。Network タブは常に全件を表示する（[reporting](reporting.md#reporthtml)） |
| `mocks` | list | `[]` | 決定的なネットワークスタブ。一致する送信リクエストには、ネットワークへ行かず定型レスポンスを返す（[ネットワークモック](#ネットワークモック決定的スタブ)） |
| `redact` | object | なし | 証跡を書き出す前に適用するマスク（[evidence](evidence.md#マスキングredact)） |
| `dismissAlerts` | bool / object | なし（ON） | 視覚ベースの **アラートガード**。iOS バックエンドから見えない OS プロンプトを片付ける。既定は ON。`false` で無効化し、`{ instruction: "tap Allow" }` なら ON のまま指定したボタンを押す。CLI の `--dismiss-alerts`/`--no-dismiss-alerts` が上書きする（[下記](#dismissalertsシステムアラートガード)） |
| `permissions` | dict | `{}` | 宣言的な OS 権限の状態（`{ <service>: grant \| revoke }`）。**アプリの起動前**に適用する（[下記](#permissions起動前の権限状態)） |

```yaml
- name: filter narrows the catalog
  preconditions:
    launchEnv: { SHOWCASE_UITEST: "1" }
  steps:
    - tap: { label: "Search", traits: [button] }
    - wait: { for: { id: search.field }, timeout: 10 }
    - type: { text: "Horse 3", into: { id: search.field } }
    - wait: { for: { id: search.row.3 }, timeout: 5 }
  expect:
    - count: { sel: { idMatches: "search.row.*" }, equals: 1 }
    - value: { sel: { id: search.count }, equals: "1" }
```

（[`demos/showcase/scenarios/search.yaml`](../../demos/showcase/scenarios/search.yaml) 実物）

## preconditions（環境準備）

実装: `scenario/models/scenario.py` の `Preconditions`。runner の `launch_driver` がこれを読んで起動手順を組み立てます（[run-loop](run-loop.md#runner実行パイプライン)）。

| キー | 型 | 既定 | 説明 | 配線 |
|---|---|---|---|---|
| `erase` | bool | `false` | 各テスト前にシミュレータ全体を wipe する（`simctl erase`。アプリ、データ、設定を消去する）。既定はオフ。`reinstall` が全 wipe なしでアプリを新規状態に保つので、まっさらなデバイスが必要なテストだけ `true` にする | ✅ |
| `reinstall` | `clean` \| `overwrite` | `clean` | config が `appPath` を指定したとき、各 run の前にアプリをどう再インストールするか。`clean` は uninstall してから install する（アプリとデータを fresh にする）。`overwrite` は既存アプリに上書き install する（データコンテナは保持する） | ✅ |
| `launchArgs` | list[str] | `[]` | 起動引数（config の `launchArgs` に追記する） | ✅ |
| `launchEnv` | dict | `{}` | 起動 env（`SIMCTL_CHILD_*` で注入する。config の `launchEnv` にマージする） | ✅ |
| `deeplink` | str | なし | 起動後に `simctl openurl` で開く | ✅ |
| `locale` | str | なし | 起動時に locale/言語を強制する（`-AppleLocale`/`-AppleLanguages`）。app/config の既定を上書きする | ✅ |
| `setup` | str | なし | 再利用する前段シナリオファイル（このシナリオからの相対で解決）。その steps を本編の前に実行する | ✅ |

> `launchEnv` の解決順は **config の `launchEnv` < preconditions の `launchEnv`** です（テストに近い方が優先）。`launch_driver` は `{**eff.launch_env, **pre.launch_env}` でマージします。

## dismissAlerts（システムアラートガード）

iOS バックエンドは **SpringBoard レベルのプロンプト**（iOS の "Save Password?"、権限リクエスト、"Allow Paste" など）を見ることも tap することもできません。これらのプロンプトはアプリを覆って要素ツリーを潰し、ステップを静かにブロックします。**アラートガード**は視覚ベースのフォールバック（`alerts.py`）です。ステップがブロックされるとスクリーンショットを撮り、Claude にどこを tap するか尋ね、プロンプトを片付けてからそのステップを 1 回再試行します（[詳細](recording.md#システムアラートの自動対処)）。`wait` ステップ（`for`/`settled`/`screenChanged`）では、ガードはすでにポーリング済みの画面も監視し、ツリーが潰れて見えた時点で **wait の途中でも**発火します（デバウンスとクールダウンを挟み、1 回の wait につき最大 2 回まで）。wait 自体のタイムアウトを待たず、ステップが失敗する前に回復できます（BE-0269）。

これは **既定で ON** で、**ステップ（または `expect`）がブロックされたとき、あるいはガード対象の `wait` でポーリング中の画面がブロックされて見えたとき**に発火します。そのため、成功するシナリオはモデルを呼びません。`ANTHROPIC_API_KEY` が必要ですが、無くても no-op になるだけで run には影響しません。シナリオごとに動作を変えるには `dismissAlerts` を使います。

| 形 | 意味 |
|---|---|
| （省略） | ON。**最も無害な**ボタンを押す（"Not Now" / "Don't Allow" / "Cancel"） |
| `dismissAlerts: false` | このシナリオでは無効 |
| `dismissAlerts: { instruction: "tap Allow" }` | ON のまま、instruction が指すボタンを押す。たとえば権限を**許可**する場合 |
| `dismissAlerts: { enabled: false }` | 無効（`false` を明示的なオブジェクトで書いた形） |

```yaml
- name: grant notification permission
  dismissAlerts: { instruction: "tap Allow" }   # accept the prompt instead of dismissing it
  steps:
    - tap:  { id: sys.requestNotif }
    - wait: { for: { id: sys.notif.authorized }, timeout: 4 }   # the guard taps Allow, then this passes
```

CLI の `--dismiss-alerts` / `--no-dismiss-alerts` フラグは**全シナリオを上書き**します（無指定ならシナリオごとの既定が使われます）。`--alert-instruction` は既定のボタン指示を設定するもので、シナリオ自身の `instruction` が優先されます。（[`demos/showcase/scenarios/permission.yaml`](../../demos/showcase/scenarios/permission.yaml) 実物）

## permissions（起動前の権限状態）

`dismissAlerts` は権限プロンプトが**現れた後**にしか反応せず、できるのは tap だけです。権限を**取り消す**ことも、アプリが既知の状態から起動することを保証することもできません。権限があらかじめわかっている場合、`permissions` を使えば**アプリのプロセスが起動する前**にその状態を設定できるため、プロンプトはそもそも現れません。モデルを一切呼ばない、決定的でマシンチェック可能なデバイス操作です（[BE-0276](../../roadmaps/BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md)）。

```yaml
- name: profile — camera already granted
  permissions:
    camera: grant
    location: grant
    contacts: revoke
  steps:
    - tap: { id: profile.avatar.upload }   # no camera-permission prompt — already granted
```

各エントリは `<service>: grant | revoke` の形を取ります。`<service>` はバックエンドに依存しない小さな語彙で、`location`、`camera`、`microphone`、`contacts`、`photos`、`calendar`、`notifications` のいずれかです。各バックエンドは、この語彙をそれぞれのネイティブな仕組みにマップします。

- **iOS** は `simctl privacy <udid> <grant|revoke> <tcc-service> <bundle>` を実行します。SpringBoard の権限プロンプトが参照するのと同じ TCC（Transparency, Consent, and Control）データベースです。
- **Android** は `pm grant` / `pm revoke` を実行し、config レベルの `grantPermissions` リスト（[drivers](drivers.md)）を支える仕組みを再利用します。シナリオの `permissions` は、この config レベルの既定の上に重なり、config が許可した権限を取り消すこともできます。

**iOS には `notifications` に対応する TCC サービスがありません**（iOS の通知許可は TCC の管轄外です）。そのため、iOS をターゲットにしたシナリオが `notifications` を指定すると、デバイスを一切操作する前の **preflight** が対応していない権限として個別に名指しして失敗します。そのプロンプト自体への対処は、引き続き `dismissAlerts` が担います。Android の `POST_NOTIFICATIONS` は実行時権限です（API 33 以降）。そのため Android は語彙のすべてに対応します。選んだバックエンドが対応しないサービスの組み合わせも、同じように preflight が個別に名指しして失敗させます。

`permissions` に対応する XCUITest / Espresso 側のコードはないため、`codegen` はコードを生成する代わりに、サービスごとにラベル付きの `// TODO` を出力します。フィールド自体は、生成したテストの起動処理より前に bajutsu 自身が適用します。

## セレクタ（要素の指定）

セレクタは、操作またはアサーションの対象となる **どの要素か** を指定します。1 つ以上のフィールドを与え、複数指定したフィールドは **AND**（すべて一致）で評価し、最低 1 つは必須です。セレクタが一意の要素にどう絞られるか、また曖昧なセレクタが最初の一致を選ばず失敗する理由は [selectors](selectors.md) を参照してください。形式的な形は [dsl-grammar](dsl-grammar.md#2-文法の全体像) にあります。

| フィールド | 型 | 説明 |
|---|---|---|
| `id` | str \| list[str] | 完全一致の `accessibilityIdentifier`。**第一候補**（安定していてローカライズされない）。リストは候補の **OR** で、要素の id が*いずれか*に一致すればよい |
| `idMatches` | str \| list[str] | id へのグロブ（例 `"list.row.*"`。複数一致を前提とする）。リストは*いずれか*のグロブに一致すればよい |
| `label` | str | 完全一致の `accessibilityLabel`（可視テキスト）。補助や曖昧性解消に使う |
| `labelMatches` | str | label への正規表現 / 部分一致（`re.search`） |
| `traits` | list[str] | アクセシビリティ trait で絞る（部分集合判定、例 `[button]`） |
| `value` | str | 完全一致の accessibility value |
| `within` | Selector | コンテナに限定する。一致要素は、ネストしたセレクタが解決する要素の内側になければならない（入れ子可） |
| `index` | int | 複数一致の k 番目を選ぶ（負数可）。最終手段であり、順序に依存する |

```yaml
- tap: { id: counter.increment }                               # by id (recommended)
- tap: { id: [stable.refresh, stable_refresh] }                # id 候補の OR（下記参照）
- tap: { label: "Delete" }                                     # by visible label (e.g. an alert button)
- tap: { id: row.action, within: { id: list.row.3 } }          # scoped to a container's subtree
- tap: { labelMatches: "^Item ", traits: [button], index: 0 }  # first matching button, fields AND-ed
```

> まず `id` を使います。要素の集合（count / 存在確認）には `idMatches` を使います。`index` は最終手段です。順序が変わると壊れます。解決の完全な意味論は [selectors](selectors.md) にあります。

### プラットフォームをまたぐ id：候補のリスト（BE-0221）

シナリオがプラットフォーム間で共有できるのは、セレクタが `id` を使う範囲までであり、その `id` をアプリ側のどの属性が満たすかはドライバ内に閉じています。ただし SPEC の id を**そのまま**再現できないプラットフォームがあります。Android の `android:id`（Views toolkit）は `.` も `-` も許さないので、`stable.refresh` は `stable_refresh`、`search.results-empty` は `search_results_empty` として現れます。**1 つ**のシナリオをどこでもそのまま走らせるため、`id` / `idMatches` に**候補のリスト**を与えると、照合はその OR になります。

```yaml
- wait: { for: { id: [stable.refresh, stable_refresh] }, timeout: 10 }
- count: { sel: { idMatches: [stable.row.*, stable_row_*] }, equals: 5 }
```

ドット形は iOS と Android Compose（どちらもそのまま再現します）に、アンダースコア形は Android Views に一致します。あるアプリの画面に現れる形は常に一方だけなので、決定的なままです。仮に両方の形が同時に画面にあれば、そのセレクタは曖昧として即座に失敗します。OR が 2 件以上の一致を暗黙に 1 つへ絞ることはありません。これにより id 規約はシナリオに**明示的に**残り、別々の id を取り違えかねないドライバ側の暗黙の `.`↔`_` 書き換えに頼りません。showcase の共有シナリオはこれを使い、`showcase-swiftui` / `showcase-compose` / `showcase-views` が同じファイルで走ります。

## ステップ文法（`steps`）

各ステップは **ちょうど 1 アクション** と、任意の修飾子（`capture:` / `name:`）からなります。1 ステップに 2 アクション以上を書くと検証エラーになります（`scenario/models/steps.py` の `_one_action`）。

| アクション | 形 | 説明 |
|---|---|---|
| `tap` | `tap: <Selector>` | 一意解決を要求する（曖昧なら失敗） |
| `doubleTap` | `doubleTap: <Selector>` | 解決した要素を 2 回素早くタップする |
| `longPress` | `longPress: { sel: <Selector>, duration: <sec> }` | 長押し |
| `type` | `type: { text: "...", into?: <Selector>, submit?: <bool> }` | `into` 指定時は先にフォーカスする |
| `clear` | `clear: { into: <Selector> }` | フィールドをフォーカスして現在の内容をすべて削除する。web コンテキストは非対応 |
| `delete` | `delete: { into: <Selector>, count: <int> }` | フィールドをフォーカスして末尾から `count` 文字削除する（`count > 0`）。web コンテキストは非対応 |
| `select` | `select: { into: <Selector>, mode?: "all" }` | フィールドをフォーカスして内容を選択する（`mode` 既定 `all`）。web コンテキストは非対応。iOS（XCUITest）バックエンドはネイティブに対応し、codegen もネイティブの等価物を出力する |
| `copy` | `copy: {}` | 選択中の内容をクリップボードへコピーする。事前の `select` が必要。web コンテキストは非対応。iOS（XCUITest）バックエンドはネイティブに対応する |
| `selectOption` | `selectOption: { sel: <Selector>, option: "..." }` | web の `<select>` をこの value を持つ option に合わせる。web 専用（iOS / Android は失敗する） |
| `swipe` | `swipe: { on: <Selector>, direction: up\|down\|left\|right }` または `swipe: { from: [x,y], to: [x,y] }` | セレクタ形と座標形は混在できない。方向指定形式は**スクロール**する |
| `drag` | `drag: { on: <Selector>, direction: up\|down\|left\|right, amount?: <frac> }` | 要素そのものを**ドラッグ**する（ハンドル／仕切り／スライダー）。スクロールではない |
| `pinch` | `pinch: { sel: <Selector>, scale: <num> }` | 2 本指の拡縮。`scale > 0`（`>1` で拡大, `<1` で縮小） |
| `rotate` | `rotate: { sel: <Selector>, radians: <num> }` | 2 本指の回転。`>0` で時計回り |
| `wait` | `wait: { for\|until: ..., timeout: <sec> }` | 条件待機（下記） |
| `assert` | `assert: [ <Assertion>... ]` | ステップ途中の中間検証 |
| `relaunch` | `relaunch: { env?: {...}, args?: [...] }` | アプリを terminate + 再起動し（launch env/args を再適用し、指定分で上書き）、ready まで待つ |
| `setLocation` | `setLocation: { lat: <num>, lon: <num> }` | シミュレータの GPS 位置を上書きする（`simctl location set`） |
| `push` | `push: { payload: {...} }` | この APNs（Apple Push Notification service）ペイロードで疑似プッシュ通知を配信する（`simctl push`） |
| `http` | `http: { method?, url, headers?, body?, status?, saveBody? }` | HTTP リクエストを送る（テストデータ準備 / Webhook / API）。`status` を検証し、ボディを `${vars.<saveBody>}` に保存する |
| `totp` | `totp: { secret, into: { var } }` | RFC 6238 の時刻ベースワンタイムパスワード（2FA）をローカルで生成し `${vars.<var>}` に入れる |
| `email` | `email: { match: { to?, subject?, subjectMatches? }, extract: { var, bodyMatches }, timeout }` | 設定したメールボックスを一致するメッセージが届くまでポーリングし、コードを `${vars.<var>}` に取り出す |
| `manual` | `manual: { label: "...", bypass?: "..." }` | `record` 中に記録される人による操作の引き取り（BE-0185）。決定的な実行時の等価物がないため、`run` 時に**明示的に失敗する**——合格を偽装しない |
| `background` | `background: {}` | アプリをバックグラウンドへ送る（Home ボタン） |
| `foreground` | `foreground: {}` | バックグラウンドのアプリを前面へ復帰する（`simctl launch`。settle 用の sleep なし） |
| `clearKeychain` | `clearKeychain: {}` | Simulator のキーチェーンをリセットする（保存済みパスワード / 証明書） |
| `clearClipboard` | `clearClipboard: {}` | Simulator のペーストボードをクリアする |
| `setClipboard` | `setClipboard: { text: "..." }` | ペースト操作のため Simulator のペーストボードにテキストを投入する |
| `overrideStatusBar` | `overrideStatusBar: { time?, batteryLevel?, batteryState?, cellularBars?, wifiBars? }` | 決定的なスクリーンショットのためステータスバーを上書きする |
| `clearStatusBar` | `clearStatusBar: {}` | ステータスバーの上書きを解除する（ライブ表示に戻す） |
| `use` | `use: { component: <file>, with?: {...} }` | 再利用コンポーネントの steps を展開する。コンパイル時マクロ（[再利用](#再利用とデータ駆動とタグ)） |

修飾子:

- `capture: [<token>...]`：このステップだけの証跡（[evidence](evidence.md#b-インライン証跡)）。
- `name: <str>`：ステップ ID（証跡の出力先ディレクトリ名やレポート表示に使う）。省略時は `step<i>`。
- `from: <str>`：**来歴**（[後述](#from来歴)）。このステップを記録した元のフレーズ。オーサリング用のメタデータで、`run` は読みません。

### `tap`

```yaml
- tap: { id: counter.increment }      # exact id (recommended)
- tap: { label: "Delete" }            # exact label (for an in-app alert etc. with no id)
```

### `type`

```yaml
- type: { text: "a@b.com", into: { id: auth.email } }   # focus, then type
- type: { text: "hello", submit: true }                 # submit appends a newline / confirm (uses current focus)
```

> 実装上は、`into` を指定すると内部で対象を `tap` してから `type_text` します（`orchestrator/actions/` の `_do_action`）。

### `selectOption`

```yaml
- selectOption: { sel: { id: nav.theme-picker }, option: midnight }   # value が "midnight" の option に <select> を合わせる
```

ネイティブの HTML `<select>` は、ドロップダウンがページの要素ツリーに含まれないため、座標タップでは値を決定的に切り替えられません。`selectOption` は、ほかのアクションと同じ一意解決のコアで `<select>` を解決したうえで、表示ラベルではなく option の **value** を指定して値を設定し、`change` イベントを発火します。これにより、ユーザーが選んだときと同じようにページが反応します。指定する value は `value` アサーションが `<select>` から読み取る値と一致するので、選択結果はそのまま検証できます。これは web 専用のアクションです。`<select>` は iOS や Android にネイティブの対応物がないため、これらのバックエンドは何もせずに済ませるのではなく、「サポート外のアクション」という明確な理由でステップを失敗させます。

### `swipe`

```yaml
- swipe: { on: { id: comp.swipearea }, direction: left }   # frame 中心 → 方向へ画面に対する割合分（既定 0.125）
- swipe: { from: [100, 400], to: [100, 200] }              # raw coordinates (last resort)
```

`{on,direction}` と `{from,to}` は、**どちらか一方だけ**でなければなりません（混在や片側の欠落は検証エラーになります）。

**方向指定**形式の意味は「スクロール」であり、各バックエンドは実際にスクロールを起こすプリミティブで実現します。iOS や Android では OS の本物のドラッグで、web ではマウスドラッグがページをスクロールしないため wheel イベント（デスクトップ）かタッチドラッグ（モバイルの [`deviceMode`](drivers.md#playwrightweb)）で実現します（BE-0227）。**座標**形式は、それ自体を目的とする素のポインタドラッグ（canvas やマップのパン、ドラッグハンドル）であり、どのバックエンドでも素のドラッグの最終手段です。

### `drag`

```yaml
- drag: { on: { id: replay.divider }, direction: right }             # 掴んだハンドルをドラッグする
- drag: { on: { id: volume.slider }, direction: up, amount: 0.3 }    # 画面に対する割合で
```

`drag` は要素アンカーの**ポインタドラッグ**です。要素そのものを掴んで方向へ動かすもので、リサイズ用の仕切り、スライダーのつまみ、並べ替えハンドルなど、スクロールではなくドラッグする操作に使います。方向指定 `swipe` と同じジオメトリを共有し（`amount` は画面に対する割合で `0 < amount ≤ 1`、省略時は小さな既定値）、方向指定 `swipe` が**スクロール**するのに対して `drag` は本物のポインタドラッグを行います。差が出るのは web だけです。web では方向指定 `swipe` が wheel スクロールになり、掴んだハンドルを動かせないため、その場合は `drag` を使います。iOS / Android では OS の本物のドラッグがスクロールもハンドル移動も兼ねるので、両者は一致します。

### `doubleTap` / `pinch` / `rotate`（ジェスチャ）

```yaml
- doubleTap: { id: gest.doubletap }                    # two quick taps
- pinch:  { sel: { id: gest.pinch },  scale: 2.0 }     # >1 zooms in, 0<scale<1 zooms out
- rotate: { sel: { id: gest.rotate }, radians: 1.57 }  # >0 clockwise (radians)
```

`scale` は **> 0** が必須です（違反は検証エラー）。`pinch` / `rotate` はマルチタッチが必要で、iOS（XCUITest）バックエンドと生成された XCUITest（`pinch(withScale:)` / `rotate(_:)`）のどちらも備えています。マルチタッチのないバックエンドは "needs multiTouch" の理由で失敗します。`doubleTap` はどこでも動作します（2 回タップ）。（[`demos/showcase/scenarios/gestures.yaml`](../../demos/showcase/scenarios/gestures.yaml) 実物）

### `wait`（条件待機）

固定 sleep はサポートしていません。**`timeout` は必須**です（無限待ちはできません）。

```yaml
- wait: { for: { id: home.title }, timeout: 5 }            # until an element appears
- wait: { until: { gone: { id: home.spinner } }, timeout: 15 }  # until an element disappears
- wait: { until: screenChanged, timeout: 5 }              # until query() changes
- wait: { until: settled, timeout: 3 }                    # until the screen stops changing
- wait: { until: { request: { method: GET, path: /items, status: 200 } }, timeout: 8 }  # until a matching request is observed
```

`for` と `until` は排他です（片方のみ）。`until` の値は `screenChanged` / `settled` / `{ gone: <Selector> }` / `{ request: <RequestMatch> }` のいずれかです。`request` 形式はネットワーク collector（[evidence](evidence.md)、`--network` 実行フラグ）をポーリングし、観測した通信が 1 件でも一致するまで待ちます。マッチャは [`request` アサーション](#requestネットワークアサーション)と同じで、`method` / `url` / `urlMatches` / `path` / `pathMatches` / `status` / `bodyMatches` を AND で評価し、`count` で閾値を上げられます。エンドポイントは `url`（完全一致の URL）か `urlMatches`（正規表現/部分一致）、または `path` だけで指定します。タイムアウトの扱いは種別で異なります（[run-loop](run-loop.md#待機条件待機)）。`for` / `gone` / `screenChanged` / `request` はタイムアウトするとステップ失敗になります。`settled` は安定化のヒントなので、タイムアウトしても現在の画面で続行し、失敗にはなりません。

### `assert`（中間検証）

ステップ途中での検証です。DSL（ドメイン固有言語）は `expect` と同一です（次節）。

```yaml
- assert:
    - disabled: { id: auth.submit }
```

### `setLocation` / `push`（デバイス制御）

```yaml
- setLocation: { lat: 35.681, lon: 139.767 }              # simctl location set
- push: { payload: { aps: { alert: "You have mail" } } }  # simctl push (APNs payload)
```

どちらも `simctl` 経由で Simulator を操作し、デバイスごとの制御チャネルが必要です。そのため fake ドライバや並列実行では使えず、その場合ステップはクリーンに失敗します（クラッシュはしません）。`push` は `payload` を APNs JSON として対象アプリに配信します。

### `http`（テストデータ準備用のリクエスト）

```yaml
- http: { method: POST, url: "https://api.test/seed", body: '{"n":1}', status: 200 }   # status が 200 以外なら失敗
- http: { url: "https://api.test/token", saveBody: token }   # vars.token ← レスポンスボディのテキスト
- assert:
    - exists: { id: home.title }
```

`http` はリクエストを runner から HTTP で送ります。UI ドライバは経由しません。そのため `status` の不一致はステップ失敗になり、`saveBody` はレスポンスボディのテキストを `${vars.<name>}` に保存して後続ステップで使えます。デバイスに触れない、ここで唯一のデバイス非依存アクションです。

### `totp`（二要素認証のワンタイムパスワード）

```yaml
- totp: { secret: "${secrets.TOTP_SEED}", into: { var: code } }   # vars.code ← 現在の 6 桁 OTP
- type: { text: "${vars.code}", into: { id: auth.code } }
```

`totp` は [RFC 6238](https://datatracker.ietf.org/doc/html/rfc6238) の時刻ベースワンタイムパスワードを、共有 `secret`（base32。YAML に直書きせず `${secrets.*}` に置く）と現在時刻からローカルで計算し、現在のコードを `${vars.<var>}` に保存します。後続の `type` / `assert` で使えます。スクリプトのエスケープハッチも LLM も使わずに 2FA サインインを自動化でき、値は secret と時刻の決定的な関数です（[BE-0046](../../roadmaps/BE-0046-otp-email-steps/BE-0046-otp-email-steps-ja.md)）。

### `email`（メールで届くコードをメールボックスから取得）

```yaml
- email:
    match: { to: "test@example.com", subjectMatches: "verification" }   # どのメッセージを待つか
    extract: { var: code, bodyMatches: "[0-9]{6}" }                     # vars.code ← 最初のキャプチャグループ
    timeout: 30
- type: { text: "${vars.code}", into: { id: auth.otp } }
```

`email` はメールで届く 2FA / 検証コードを待ちます。汎用 HTTP メールボックス（`targets.<name>.mailbox` で設定。[configuration](configuration.md#mailbox-emailステップ) 参照）をポーリングし、**ステップ開始後に届いた**メッセージのうち `match` を満たすものが現れるまで待って、その本文から `bodyMatches` の正規表現（最初のキャプチャグループ、無ければマッチ全体）で値を `${vars.<var>}` に取り出します。待機は **`timeout` 必須の条件待機**です（固定 sleep なし）。タイムアウト、本文に正規表現が当たらない一致メッセージ、到達不能 / 2xx 以外のメールボックスは、いずれもクリーンなステップ失敗で、黙って誤った値を返すことはありません。対象はステップ開始より新しいメールだけ（メッセージ id で判定するので、以前の run の古いコードには一致しません）で、新着の一致が複数あれば最新を採ります。決定的で LLM 非依存、エンドポイントと認証情報は config 参照の `${secrets.*}` に置くのでシナリオはアプリ非依存のままです（[BE-0046](../../roadmaps/BE-0046-otp-email-steps/BE-0046-otp-email-steps-ja.md)）。

### `manual`

`record` 中に記録される人による操作の引き取りです。

```yaml
- manual: { label: "ログインの CAPTCHA を解く" }                          # 決定的な等価物なし（本物の CAPTCHA）
- manual: { label: "Face ID を許可する", bypass: "device-control の生体認証マッチ（BE-0052）" }   # 作者が配線できる橋渡しを名指し
```

`record` は、詰まりが AI に実行できない**操作**そのもの——CAPTCHA、生体認証のプロンプト、AI が繰り返し解けないジェスチャ——であるとき `manual` ステップを出します。人が実際のデバイスを操作して制御を返し（`acted` ハンドオフ、[recording](recording.md#human-in-the-loop-ハンドオフbe-0179)）、ステップは生のジェスチャではなく観測した遷移のマーカーを記録します。`bypass` を設定すると、そのステップを再生可能にするために作者が配線できるテストビルド用のフラグ、あるいは device-control / device-state プリミティブ（BE-0035 / BE-0052）を名指しします。省略すると、そうした等価物のない引き取り（本物の CAPTCHA）であることを示します。どの codegen ターゲットもこれをラベル付きの `// TODO` として描画します。`manual` ステップは**決して黙って合格しません**。決定的な実行時の等価物がないため、`run` 時には `label` と bypass のヒントを示して `ManualStepRequired` で明示的に失敗します（原則 1・2）。名指しした `bypass` を配線し——そのうえで `manual` ステップを決定的なアクションに置き換え——ることが、作者にとって再生可能なシナリオへの道です（[BE-0185](../../roadmaps/BE-0185-record-human-takeover-step/BE-0185-record-human-takeover-step-ja.md)）。

### デバイス / システム制御（iOS）

```yaml
- background: {}                                                        # Home ボタン（SpringBoard 経由でバックグラウンド化。終了はしない）
- foreground: {}                                                        # バックグラウンドのアプリを前面へ復帰（simctl launch）
- clearKeychain: {}                                                     # 保存済みパスワード / 証明書をリセット
- clearClipboard: {}                                                    # ペーストボードをクリア
- setClipboard: { text: "COUPON123" }                                   # ペーストボードに投入（ペースト操作用）
- overrideStatusBar: { time: "9:41", batteryLevel: 100, wifiBars: 3 }   # ステータスバーを固定
- clearStatusBar: {}                                                    # ライブのステータスバーに戻す
```

`setLocation` / `push` と同様、これらは `simctl` 経由で Simulator を操作するため、デバイスごとの制御チャネルが必要で、fake ドライバや並列実行ではクリーンに失敗します。`overrideStatusBar` は、スクリーンショットや `visual` アサーションの直前に時計や電波表示を固定して画像を安定させる用途に向きます。`background` / `foreground` はバックグラウンド/フォアグラウンド遷移の対で、`foreground` は settle 用の sleep を入れずに復帰するので、必要なら直後に具体的な要素を待ってください。`setClipboard` はペースト操作のためペーストボードに値を投入します（[BE-0052](../../roadmaps/BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md)）。

## アサーション DSL

`expect`（最終検証）と `assert`（中間検証）で共通です。リスト内はすべて **AND** で評価し、1 つでも失敗するとステップ失敗になります。評価の仕組み（要素の解決と比較）は [selectors](selectors.md#アサーション評価) にあります。

| アサーション | 意味 | 例 |
|---|---|---|
| `exists` | 一致要素が存在する（`negate: true` で不在を検証） | `exists: { id: home.title }` / `exists: { id: settings.banner, negate: true }` |
| `value` | accessibility value の一致 | `value: { sel: { id: counter.value }, equals: "2" }` |
| `label` | label の完全一致 / 部分一致 / 正規表現 | `label: { sel: { id: settings.status }, contains: "done" }` |
| `count` | 一致要素数 | `count: { sel: { idMatches: "list.row.*" }, equals: 5 }` |
| `enabled` / `disabled` | 操作可否（`notEnabled` trait） | `disabled: { id: auth.submit }` |
| `selected` | 選択 / トグル状態（`selected` trait） | `selected: { id: tab.home }` |
| `request` | 一致するネットワーク通信が観測された（`--network` が必要） | `request: { method: POST, path: /login, status: 200, count: 1 }` |
| `event` | アプリが送った分析 / テレメトリイベント。エンドポイント＋JSON ボディのフィールドを count とともに検証（`--network` が必要） | `event: { url: "https://t.example.com/track", body: { name: purchase_completed }, count: { equals: 1 } }` |
| `requestSequence` | 複数のマッチャがこの順序で観測されたか検証（`--network` が必要） | `requestSequence: [ { urlMatches: "/auth/refresh" }, { urlMatches: "/api/account" } ]` |
| `responseSchema` | 捕捉したレスポンスボディが JSON Schema に適合するか検証（`--network` が必要） | `responseSchema: { request: { urlMatches: "/api/items" }, schema: items.json }` |
| `visual` | 画面が baseline 画像に一致する（ビジュアルリグレッション） | `visual: { baseline: home.png, threshold: 0.02 }` |
| `clipboard` | デバイスのペーストボードが一致する（`simctl pbpaste` で読み戻す） | `clipboard: { equals: "COUPON123" }` / `clipboard: { matches: "\\d{6}" }` |

- `exists` はセレクタを **インラインで**書きます（`{ id: ... }` を直書き）。`negate` は任意です。
- `value` / `label` は `sel:` と、`equals` / `contains` / `matches` の **いずれか 1 つ**を指定します。
- `count` は `sel:` と、`equals` / `atLeast` / `atMost` の **いずれか 1 つ**を指定します。
- `enabled` / `disabled` / `selected` はセレクタを直書きします。
- `request` は **観測されたネットワーク通信**に一致するか検証します（[下記](#requestネットワークアサーション)）。`--network` 実行フラグが必要です。
- `event` は **アプリが送った分析 / テレメトリイベント**に一致するか検証します（[下記](#eventイベントアサーション)）。`--network` 実行フラグが必要です。
- `requestSequence` は複数の request マッチャが **順序どおりに観測された**かを検証します（[下記](#requestsequence順序付きリクエスト)）。`--network` 実行フラグが必要です。
- `responseSchema` は捕捉した **レスポンスボディが JSON Schema に適合する**かを検証します（[下記](#responseschemaレスポンスの-json-schema)）。`--network` 実行フラグが必要です。
- `visual` はスクリーンショットを baseline 画像とピクセル比較します（[下記](#visualビジュアルリグレッション)）。
- `clipboard` はデバイスのペーストボードを `simctl pbpaste` で読み戻し、`equals` / `matches`（正規表現）の **いずれか 1 つ**を検証します。`setClipboard` の読み戻し側で、「コピー」操作の検証に使います。デバイスごとの制御チャネルが必要なため、fake ドライバや並列実行では利用できず、その場合はクリーンに失敗します（[BE-0052](../../roadmaps/BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md)）。

> **ロケール注意**: `label`/`value` の文字列比較や、可視テキストを見るアサーションは翻訳で壊れます。これらは config の固定 locale を前提に書き、セレクタ自体は `id` で書いてください。

### `request`（ネットワークアサーション）

`request` は、run のネットワーク collector が **一致する HTTP 通信を観測した**ことを表明します（`--network` 実行フラグと、アプリ内の BajutsuKit が必要）。同じマッチャを `until: { request: ... }` の wait と `mocks`（下記）で共有します。マッチフィールドは最低 1 つ必須で、列挙したフィールドは **AND** で評価します。

| フィールド | 型 | 説明 |
|---|---|---|
| `method` | str | HTTP メソッド（`GET`, `POST`, …） |
| `url` | str | 完全一致の URL（エンドポイント） |
| `urlMatches` | str | URL への正規表現 / 部分一致（クエリ文字列はここに含める） |
| `path` | str | 完全一致のパス（クエリは無視） |
| `pathMatches` | str | パスへの正規表現 |
| `status` | int | レスポンスのステータスコード |
| `bodyMatches` | str | **リクエストボディ**への正規表現 / 部分一致 |
| `count` | int | 一致した通信数。アサーションでは **厳密**、`wait` では **下限** |

```yaml
- assert:
    - request: { method: POST, path: /login, status: 200, count: 1 }
    - request: { urlMatches: "/search", bodyMatches: "apple" }   # match on the request body
```

> `count` はマッチフィールド **ではありません**。`method` / `url` / `urlMatches` / `path` /
> `pathMatches` / `status` / `bodyMatches` の少なくとも 1 つが必要です。
> （[`demos/showcase/scenarios/network_mock.yaml`](../../demos/showcase/scenarios/network_mock.yaml) 実物）

### `event`（イベントアサーション）

`event` は、画面には現れない振る舞い、すなわちアプリが**送った**分析 / テレメトリイベントを表明します
（[BE-0048](../../roadmaps/BE-0048-behavioral-protocol-assertions/BE-0048-behavioral-protocol-assertions-ja.md)）。
`request` が読むのと同じ観測済み通信に対する純粋な検査なので（`--network` 実行フラグが必要）、判定は機械のみで LLM は介在しません。
イベントの**エンドポイント**（`request` と同じ `method` / `url` / `urlMatches` / `path` / `pathMatches` マッチャ）でタイムラインを絞り、
続けて構造化した**リクエストボディのフィールド**で絞り、残った通信数を count 演算子と突き合わせます。

| フィールド | 型 | 説明 |
|---|---|---|
| `method` / `url` / `urlMatches` / `path` / `pathMatches` | str | エンドポイントのマッチャ（AND）。意味は `request` と同じ |
| `body` | map | 各 `key: value` が JSON リクエストボディに存在し、その値と等しいこと。テキストとして比較する（`amount: "300"` は JSON の数値 `300` に一致。JSON の真偽値 / null は `"true"` / `"false"` / `"null"` に一致） |
| `count` | object | 期待する多重度。`equals` / `atLeast` / `atMost` の **いずれか 1 つ**。省略時は **1 件以上** |

```yaml
expect:
  # 購入イベントが正しい金額でちょうど 1 回発火したこと
  - event:
      url: "https://t.example.com/track"
      body: { name: purchase_completed, amount: "300" }
      count: { equals: 1 }
```

> エンドポイントのフィールドか `body` の少なくとも一方が必須で、イベントは必ず何かを特定します。JSON でない、
> オブジェクトでない、あるいは存在しないリクエストボディは `body` 条件に一致しません（推測せず失敗します）。
> ボディの値は DSL の他の箇所と同じく `${vars.*}` / `${secrets.*}` トークンを使えます。

### `requestSequence`（順序付きリクエスト）

`requestSequence` は、複数のリクエストが **指定した順序で**起きたことを表明します。たとえば保護された
呼び出しの*前に*トークンリフレッシュが起きたこと、といった検証です
（[BE-0048](../../roadmaps/BE-0048-behavioral-protocol-assertions/BE-0048-behavioral-protocol-assertions-ja.md)）。
観測済みタイムラインに対する純粋な検査なので（`--network` 実行フラグが必要）、判定は機械のみです。空でない
[`request` マッチャ](#requestネットワークアサーション)のリスト（同じフィールド）を取り、**順序を保った部分列**
として照合します。各マッチャは、直前のマッチより厳密に後ろの位置にある別々の通信に一致しなければなりません。
間に無関係な通信が**挟まってもよい**のでノイズに強く、同じマッチャを2回並べれば順序を保った2件の出現を要求します。

```yaml
expect:
  - requestSequence:
      - { method: POST, urlMatches: ".*/auth/refresh" }
      - { method: GET,  urlMatches: ".*/api/account" }
```

> 各マッチャは `request` と同じフィールド（`method` / `url` / `urlMatches` / `path` / `pathMatches` /
> `status` / `bodyMatches`）を使います。マッチャ自身の `count` はここでは無視されます。シーケンスの役割は
> **順序**だからです。純粋な多重度の検査には `request` の `count` を使ってください。

### `responseSchema`（レスポンスの JSON Schema）

`responseSchema` は、捕捉した **レスポンスボディが JSON Schema に適合する**ことを表明します。画面では
表現できない契約の検査です（[BE-0048](../../roadmaps/BE-0048-behavioral-protocol-assertions/BE-0048-behavioral-protocol-assertions-ja.md)）。
観測済みタイムラインと保存済みのスキーマファイルに対する純粋で決定的な検査なので（`--network` 実行フラグが
必要）、判定は機械のみです。`request`（同じマッチャフィールド）で検証対象の交信を選び、`schema` はターゲットの
**スキーマディレクトリ**（`--schemas` フラグ、config の `targets.<name>.schemas`、またはシナリオ脇の
`schemas/`）内で解決するファイルパスです。検証には `jsonschema` ライブラリを使うので、`schema` extra を
インストールしてください（`pip install bajutsu[schema]`）。

```yaml
expect:
  - responseSchema:
      request: { method: GET, urlMatches: ".*/api/items" }
      schema: items.json        # スキーマディレクトリ内で解決
```

> 検証するのは**最初に**一致した交信のレスポンスです。一致する交信がない、スキーマファイルが無い、
> レスポンスにボディが無い、JSON でない、あるいは適合しない場合は（推測せず）失敗します。スキーマ
> ディレクトリの解決順は `visual` の `--baselines` と同じです。

### `visual`（ビジュアルリグレッション）

```yaml
- assert:
    - visual: { baseline: "home.png", threshold: 0.02, exclude: [{ x: 0, y: 0, w: 390, h: 47 }] }
    - visual: { baseline: "detail.png", compare: pixelmatch, colorTolerance: 0.1, antialiasing: true }
    - visual: { baseline: "summary-card.png", element: { id: "summary-card" } }  # 1 要素だけ比較
    - visual: { baseline: "home.png", exclude: [{ selector: { label: "last updated" } }] }  # 要素でマスク
```

`visual` はスクリーンショットを取得し、`baseline`（run の baselines ディレクトリ内の PNG。`--baselines`、またはシナリオ脇の `baselines/`）と比較します。

比較エンジンは `compare` で選択できます（BE-0165）。

| エンジン | 説明 | 既定 |
|---|---|---|
| `exact` | ピクセル完全一致。いずれかのチャネルが異なればそのピクセルは「差分」として計上されます。 | はい（後方互換） |
| `pixelmatch` | 知覚的 YIQ 色差 + アンチエイリアシング検出。サブピクセルレンダリングノイズや 1 ピクセルのエッジシフトを許容します。 | いいえ |

`compare` を省略すると、ターゲットの `visualCompare` 設定（`defaults:` または `targets.<name>` で指定）にフォールバックし、さらに未設定なら `exact` になります。

`threshold` は許容する差分ピクセルの割合（既定 `0.0` = 完全一致）で、すべてのエンジン共通です。`colorTolerance`（0–1、既定 `0.1`）は `pixelmatch` のピクセル単位の知覚的色差許容値、`antialiasing`（既定 `true`）はアンチエイリアスされたピクセルを差分から除外します。`exclude` は比較前にマスクする領域のリストで、ステータスバーや時計などに使います。各要素は、スクリーンショットのピクセル座標で表す矩形（`{ x, y, w, h }`）か、マスク対象の要素を指す `{ selector: <Selector> }`（BE-0171）のどちらかです。後者は評価時に要素のフレームへ解決されます。baseline は `approve` コマンド（[cli](cli.md#approve)）か `serve` UI で作成・更新します。baseline が無いとアサーションは失敗します。`overrideStatusBar` と併用すると時計やバッテリーを固定できます。差分は `report.html` に表示されます。`pixelmatch` では、除外されなかった（非 AA）ピクセルのみが差分画像に表示されます。

**要素スコープ比較（BE-0171）。** `visual` は既定で画面全体を比較するため、無関係な変化（バナー、行が増えたリストなど）があるたびにアサーションが失敗し、baseline が揺れます。`element: <Selector>` を指定すると、**その要素だけ**を比較します。スクリーンショットは要素のフレームにクロップされ、baseline はそのクロップ画像になるので、フレームの外側の変化は無視されます。セレクタは通常の一意解決規則で解決し、**曖昧なセレクタは最初の一致をクロップせず即座に失敗します**。何にも一致しないセレクタも失敗します。`approve` は要素スコープの baseline も画面全体の baseline と同じ手順で昇格します（baseline は単に小さい画像になるだけです）。

**セレクタによるマスク（BE-0171）。** `exclude` のピクセル矩形は、レイアウトが変わったりデバイスの解像度が変わったりした瞬間にずれます。代わりに要素を指定すると（`{ selector: { label: "last updated" } }`）、そうした変化に強くなります。要素をフレームへ解決し、矩形と同じ方法でマスクするからです。何にも一致しないマスクセレクタは何もしません（画面上に隠すものが無いため）。曖昧なセレクタは、決定論の原則どおり失敗します。セレクタと矩形は一つの `exclude` リストに混在でき、どちらも要素スコープ比較と併用できます（クロップした要素の内側のマスクは、クロップの座標系へ変換されます）。

## ネットワークモック（決定的スタブ）

`mocks` はテストをライブサーバから独立させます。送信リクエストが一致すると、BajutsuKit はネットワークへ行かずに定型レスポンスを返します。各モックは `{ match, respond }` です。

- **`match`** は[リクエストマッチャ](#requestネットワークアサーション)の **リクエスト側**フィールドを再利用します（`method` / `url` / `urlMatches` / `path` / `pathMatches` / `bodyMatches`）。`status` / `count` はモックの `match` には **適用されません**。
- **`respond`** は定型の返答です。`status`（既定 `200`）、`headers`（既定 `{}`）、`body`（文字列）、`delayMs`（人工的な遅延）を指定します。`respond` を省くと空の `200` を返します。

```yaml
- name: GET answered by a mock stub
  mocks:
    - match: { method: GET, urlMatches: "example.com" }
      respond:
        status: 418                       # real example.com returns 200; 418 proves the stub served it
        headers: { Content-Type: text/plain }
        body: "stubbed by bajutsu"
  steps:
    - tap:  { id: net.fetch }
    - wait: { until: { request: { method: GET, urlMatches: "example.com", status: 418 } }, timeout: 6 }
  expect:
    - request: { method: GET, urlMatches: "example.com", status: 418 }
```

モックは `BAJUTSU_MOCKS` env で BajutsuKit に渡されます（`dump_mocks`, `scenario/serialize.py`）。形式的な形は [dsl-grammar](dsl-grammar.md#2-文法の全体像) にあります。

## 再利用とデータ駆動とタグ

コア文法の周りには、小さなテンプレートとマクロの層があります。これはロード時、決定的 run の **前**に実行されるため、ランナーは常に展開済みのプレーンなシナリオだけを見ます。展開順、`${ns.key}` 補間、深さ制限といった規範的な規則は [dsl-grammar](dsl-grammar.md#6-テンプレートとマクロ層) にあります。ここではオーサリングの視点から説明します。

### コンポーネント（`use` → 再利用ステップ）

**コンポーネント**は別ファイルで、`params` のリストと、それを `${params.<name>}` で参照する `steps` のリストからなります。`use` ステップが `with` で params を束縛して呼び出します。`use` は **コンパイル時マクロ**であり、`expand_components`（`scenario/expand.py`）が run の前に、コンポーネントの置換済みステップへ置き換えます。展開は再帰的で、コンポーネントが別のコンポーネントを `use` でき、深さは 25 までです。params 不足、未知の params、未宣言を指す残留 `${params.*}`、循環参照ではエラーになります。`use` は run に残らないため、決定性には影響しません。

```yaml
# login.component.yaml: コンポーネントファイル（単一マッピング。別ファイルとして読み込む）
params: [user, pass]
steps:
  - type: { text: "${params.user}", into: { id: auth.user } }
  - type: { text: "${params.pass}", into: { id: auth.pass } }
  - tap:  { id: auth.submit }
```

```yaml
# シナリオ側: 上の 3 ステップに params を置換して展開される
steps:
  - use: { component: login.component.yaml, with: { user: alice, pass: hunter2 } }
  - tap: { id: home.tab }
```

### データ駆動シナリオ（`data` / `dataFile`）

`data`（インライン行）か `dataFile`（CSV パス。両者は **排他**）を持つシナリオは、`${row.<column>}` を置換して **1 行 1 シナリオ**に展開されます（`expand_data`, `scenario/expand.py`）。派生シナリオは `"<name> [row N: col=val, …]"` に改名され、元の preconditions を保ちます。そのため各行ともアプリを fresh に再インストールし、テンプレートの `erase` / `reinstall` を継承します。

```yaml
- name: search returns a result
  data:
    - { q: dog, expect: "1 result" }
    - { q: cat, expect: "2 results" }
  steps:
    - type: { text: "${row.q}", into: { id: search.field }, submit: true }
  expect:
    - label: { sel: { id: home.status }, equals: "${row.expect}" }
```

> **ちょうど 1 トークン**だけの文字列（`"${row.qty}"`）は **生**の値になります（数値は数値のまま）。大きな
> 文字列に **埋め込まれた**トークンは、テキストとして差し込まれます（`"item-${row.id}"`）。

CSV の `dataFile` は、列名を与えるヘッダ行を持ち、以降の各行が 1 シナリオになります。

> **参照はスイートの中にとどまります。** `use` コンポーネントと `dataFile` のパスは、シナリオファイルを
> 起点に解決します。解決後のファイルは、読み込みを始めた scenarios ディレクトリ（スイートのルート）の中に
> とどまらなければなりません。ルートの外へ出る参照、すなわち絶対パス、ルートを抜ける `../` の連鎖、外を
> 指すシンボリックリンクは、明確なエラーで拒まれ、読み込まれることはありません。そのため、シナリオが
> ローダに自分の木の外のファイルを開かせることはできません（[BE-0174](../../roadmaps/BE-0174-scenario-ref-path-containment/BE-0174-scenario-ref-path-containment-ja.md)）。
> ルートの中にとどまる相対参照は、今までどおり動きます。たとえば同じ階層の `components/shared.yaml` や、
> サブディレクトリにあるシナリオからルートより上に出ない `../shared.yaml` です。

### タグと選択

`tags` はシナリオにラベルを付けます。CLI の `--tag` / `--exclude` フラグで実行対象を絞ります。シナリオは、少なくとも 1 つの `--tag` を持ち（または `--tag` 未指定で）、**かつ** `--exclude` のタグを 1 つも持たない場合に実行対象として残ります。`--exclude` が `--tag` より優先されます（`select_scenarios`, `scenario/select.py`）。両フラグともカンマ区切りで複数指定できます。

```yaml
- name: checkout smoke
  tags: [smoke, checkout]
  steps:
    - tap: { id: cart.checkout }
```

```bash
uv run bajutsu run --target showcase-swiftui --tag smoke --exclude wip   # run @smoke, skip anything @wip (across the app's scenarios dir)
```

### シークレット（`${secrets.X}`）

シークレットの環境変数名を config で宣言します（`secrets: [API_TOKEN, ...]`）。宣言した各名 `X` は環境から解決され、**アクション時**に実行ステップへ `${secrets.X}` として置換されます。シナリオファイルは **トークン**を保持し、実値は持ちません。さらにリテラル値は証跡で **自動マスク**されるため、シークレットはコミットしてもレビューしても安全です。`${params.*}` / `${row.*}`（ロード時の展開）と異なり、この名前空間は run ループが解決します。

```yaml
# config が宣言: secrets: [API_TOKEN]
steps:
  - type: { text: "${secrets.API_TOKEN}", into: { id: auth.token } }   # real value typed; token kept in the report
```

### ランタイム変数 (`${vars.*}`)

ステップの `extract` 修飾子は、ステップ実行後に UI 要素のプロパティを `vars.*` に取り込みます。後続のステップやシナリオレベルの `expect` で、`${vars.<name>}` として参照できます。

```yaml
steps:
  - tap: { id: counter.inc }
    extract:
      count: { sel: { id: counter.value } }          # vars.count ← element's value (default)
      heading: { sel: { id: header }, prop: label }   # vars.heading ← element's label
  - assert:
      - value: { sel: { id: other.field }, equals: "${vars.count}" }
```

各 `extract` エントリは、`sel`（セレクタ、`resolve_unique` で一意解決）と、省略可能な `prop`（`value` | `label` | `identifier`、既定 `value`）を指定します。セレクタが一意に解決できない場合や、プロパティが `None` の場合、ステップは失敗します。

### 条件分岐 (`if`)

ステップでアサーション DSL と同じ条件を評価し、分岐できます。

```yaml
steps:
  - if:
      condition: { exists: { id: dialog.alert } }
      then:
        - tap: { id: dialog.dismiss }
      else:
        - tap: { id: home.start }
```

条件は現在の要素ツリーに対して評価されます（`${...}` 補間あり）。条件が成立すれば `then` のステップ群が、そうでなければ `else` のステップ群が実行されます（`else` 省略時は何もしません）。ネストしたステップは、外側のシナリオと同じ `vars.*` バインディングを共有します。`capture` / `extract` 修飾子は `if` ステップでは使えません。

### 要素のイテレーション (`forEach`)

セレクタに一致する全要素に対して、ステップを繰り返し実行できます。

```yaml
steps:
  - forEach:
      sel: { idMatches: "item.*" }
      as: current
      steps:
        - tap: { id: "${vars.current}" }
```

要素リストはループ開始時に 1 回スナップショットされます。各要素の `identifier` が `vars.<as>` に格納され、ネストしたステップで参照できます。`identifier` のない要素はステップを失敗させます。0 件一致は no-op（成功）です。セレクタは `${...}` 補間に対応しています。`capture` / `extract` 修飾子は `forEach` ステップでは使えません。

## capture トークン文法

`capture:`（ステップ単体）と `capturePolicy[].capture`（ルール）で共通です。形は `<kind>[.<modifier>]` です。

- **種別**: `screenshot` / `elements` / `actionLog` / `deviceLog` / `network` / `video` / `appTrace`
- **修飾子**: `before` / `after` / `around` / `onError`

検証は、種別と修飾子の集合に対して行われます（`scenario/models/_base.py` の `_validate_capture`）。種別ごとの取得タイミングと、どれが取得されるかは [evidence](evidence.md#証跡種別と取得タイミング) にあります。

## YAML の注意点

PyYAML（YAML 1.1）は `on`/`off`/`yes`/`no` を真偽値に解決します。`capturePolicy` のトリガーキー `on:` が `True` になるのを防ぐため、Bajutsu の YAML ローダ（`_yaml.py`）は **`true`/`false` だけを真偽値**として扱い、`on`/`off`/`yes`/`no` は文字列のまま読みます。

## `from`（来歴）

`from:` は、**ある構成要素がどの自然言語フレーズから記録されたか**を残します（BE-0044）。任意の文字列で、シナリオ（元のゴール）、各ステップ、各 `expect` アサーション、各 `capturePolicy` ルールという 4 つのレベルに付きます。これにより、レビュアーは各部分が*なぜ*存在するのかを見て、`record` が意図を忠実に正規化できているかを判断できます。

```yaml
- name: 設定を開いて再生成する
  from: "設定を開いて、再インデックスして、正規化設定が消えていることを確認して"   # 元のゴール
  steps:
    - tap: { id: settings.open }
      from: "設定を開く"
  expect:
    - exists: { label: "正規化設定が変更されています", negate: true }
      from: "正規化設定が消えていること"
  capturePolicy:
    - on: { action: tap, idMatches: "*.submit" }
      capture: [screenshot.after, network]
      from: "送信を押すたびにスクショとネットワークログを残して"
```

- **書き込むのは `record`（Tier 1、AI）だけです。** ゴールを構造化シナリオへ正規化する際に `from:` を埋めます。手書きのシナリオは単に省略でき、書き出した YAML も汚れません（未設定の `from:` は間引かれます）。
- **`run`（Tier 2）は一切読みません。** 来歴はオーサリング用のメタデータで、オーケストレータは参照しないので、ゲートに AI を加えず、pass/fail にも影響しません。
- **グルーピングは創発的です。** 1 つの発話が複数ステップを生むとき、それらは**同じ** `from:` 文字列を持ちます。範囲（span）構文はありません。`lint` は来歴カバレッジ（`from:` を持つステップ数）を advisory として報告しますが、run を落とすことはありません。
- **`trace` とレポートに表示します。** [`bajutsu trace`](cli.md#trace) は各ステップのフレーズを `← "<フレーズ>"` としてインライン表示し、`report.html` はステップの下に表示します。どちらも同じフレーズの連続を 1 つのラベルにまとめ、タイムラインを「自然言語 ↔ 操作」の対応図にします。
- フレーズは、著者が書いた言語のまま**逐語的**に保ちます（翻訳しません）。

## ラウンドトリップ（読込 ⇄ 書出）

- `load_scenarios(text) -> list[Scenario]`: YAML 文字列 → 検証済みモデル。
- `dump_scenarios(scenarios) -> str`: モデル → YAML（`None` / 空リスト / 空辞書を間引いて読みやすくします）。

`record` の出力はこの `dump_scenarios` を通ります。生成された YAML は `load_scenarios` でそのまま読み戻せます。

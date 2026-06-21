[English](../scenarios.md) · **日本語**

# シナリオ仕様（オーサリングリファレンス）

シナリオは Bajutsu が永続化する **唯一の成果物**です。プレーンな YAML で書き、git でバージョン管理し、PR でレビューできます。最初の 1 回は `record`（AI）が書き、以後は人間が所有して編集します。`run` はこの構造を AI なしで実行します。

実装: `bajutsu/scenario/`（`models/` 配下の pydantic モデル。`extra="forbid"` で未知キーを拒否）。

すべての生成規則、型、既定値、検証規則を定めた **規範的な文法**は [dsl-grammar](dsl-grammar.md) にあります。このページはオーサリングガイドであり、例を使ってシナリオの書き方を示します。

関連: [dsl-grammar](dsl-grammar.md)（形式文法）・[selectors](selectors.md)（セレクタとアサーションの評価方法）・[evidence](evidence.md)（証跡）・[run-loop](run-loop.md)（実行）

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

## トップレベル構造（`Scenario`）

| キー | 型 | 既定 | 説明 |
|---|---|---|---|
| `name` | str | 必須 | シナリオ名（レポート / JUnit testcase / codegen のメソッド名に使う） |
| `description` | str | なし | 任意の説明文。シナリオの report カードと serve UI に表示 |
| `tags` | list[str] | `[]` | 選択ラベル。CLI の `--tag` / `--exclude` で実行対象を絞る（[再利用とデータ駆動とタグ](#再利用とデータ駆動とタグ)） |
| `data` / `dataFile` | list / str | なし | データ駆動の行。インライン `data` か `dataFile`（CSV パス）で指定する。1 行 1 run に展開し `${row.col}` を置換する。両者は排他（[再利用とデータ駆動とタグ](#再利用とデータ駆動とタグ)） |
| `preconditions` | object | `{}` | テスト前の環境準備（下記） |
| `steps` | list | 必須 | アクションの並び（下記） |
| `expect` | list | `[]` | 全ステップ成功後の最終アサーション（[selectors](selectors.md#アサーション評価)） |
| `capturePolicy` | list | `[]` | 繰り返し発火する証跡ルール（[evidence](evidence.md#a-capturepolicyルール方式)） |
| `network` | object | なし | `{ filter: { domains: [...] } }`。`filter.domains` は、レポートの Steps タイムラインに差し込む通信を URL ホストで絞る（親ドメインはサブドメインに一致）。未指定なら全件を表示する。Network タブは常に全件を表示する（[reporting](reporting.md#reporthtml)） |
| `mocks` | list | `[]` | 決定的なネットワークスタブ。一致する送信リクエストには、ネットワークへ行かず定型レスポンスを返す（[ネットワークモック](#ネットワークモック決定的スタブ)） |
| `redact` | object | なし | 証跡を書き出す前に適用するマスク（[evidence](evidence.md#マスキングredact)） |
| `dismissAlerts` | bool / object | なし（ON） | 視覚ベースの **アラートガード**。idb から見えない OS プロンプトを片付ける。既定は ON。`false` で無効化し、`{ instruction: "tap Allow" }` なら ON のまま指定したボタンを押す。CLI の `--dismiss-alerts`/`--no-dismiss-alerts` が上書きする（[下記](#dismissalertsシステムアラートガード)） |

```yaml
- name: onboard, log in, and increment the counter
  preconditions:
    launchEnv: { SAMPLE_UITEST: "1" }
  steps:
    - tap: { id: onboarding.start }
    - type: { text: "a@b.com", into: { id: auth.email } }
    - type: { text: "pw", into: { id: auth.password } }
    - tap: { id: auth.submit }
    - wait: { for: { id: home.title }, timeout: 5 }
    - tap: { id: counter.increment }
    - tap: { id: counter.increment }
  expect:
    - exists: { id: home.title }
    - value: { sel: { id: counter.value }, equals: "2" }
```

（[`demos/features/app/scenarios/smoke.yaml`](../../demos/features/app/scenarios/smoke.yaml) 実物）

## preconditions（環境準備）

実装: `scenario/models/scenario.py` の `Preconditions`。runner の `launch_driver` がこれを読んで起動手順を組み立てます（[run-loop](run-loop.md#runner実行パイプライン)）。

| キー | 型 | 既定 | 説明 | 配線 |
|---|---|---|---|---|
| `erase` | bool | `false` | 各テスト前にシミュレータ全体を wipe する（`simctl erase`。アプリ、データ、設定を消去する）。既定はオフ。`reinstall` が全 wipe なしでアプリを fresh に保つので、まっさらなデバイスが必要なテストだけ `true` にする | ✅ |
| `reinstall` | `clean` \| `overwrite` | `clean` | config が `appPath` を指定したとき、各 run の前にアプリをどう再インストールするか。`clean` は uninstall してから install する（アプリとデータを fresh にする）。`overwrite` は既存アプリに上書き install する（データコンテナは保持する） | ✅ |
| `launchArgs` | list[str] | `[]` | 起動引数（config の `launchArgs` に追記する） | ✅ |
| `launchEnv` | dict | `{}` | 起動 env（`SIMCTL_CHILD_*` で注入する。config の `launchEnv` にマージする） | ✅ |
| `deeplink` | str | なし | 起動後に `simctl openurl` で開く | ✅ |
| `locale` | str | なし | 起動時に locale/言語を強制する（`-AppleLocale`/`-AppleLanguages`）。app/config の既定を上書きする | ✅ |
| `setup` | str | なし | 再利用する前段シナリオファイル（このシナリオからの相対で解決）。その steps を本編の前に実行する | ✅ |

> `launchEnv` の解決順は **config の `launchEnv` < preconditions の `launchEnv`** です（テストに近い方が優先）。`launch_driver` は `{**eff.launch_env, **pre.launch_env}` でマージします。

## dismissAlerts（システムアラートガード）

idb は **SpringBoard レベルのプロンプト**（iOS の "Save Password?"、権限リクエスト、"Allow Paste" など）を見ることも tap することもできません。これらのプロンプトはアプリを覆って要素ツリーを潰し、ステップを静かにブロックします。**アラートガード**は視覚ベースのフォールバック（`alerts.py`）です。ステップがブロックされるとスクリーンショットを撮り、Claude にどこを tap するか尋ね、プロンプトを片付けてからそのステップを 1 回再試行します（[詳細](recording.md#システムアラートの自動対処)）。

これは **既定で ON** で、**ステップ（または `expect`）がブロックされたときだけ**発火します。そのため、成功するシナリオはモデルを呼びません。`ANTHROPIC_API_KEY` が必要ですが、無くても no-op になるだけで run には影響しません。シナリオごとに動作を変えるには `dismissAlerts` を使います。

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

CLI の `--dismiss-alerts` / `--no-dismiss-alerts` フラグは**全シナリオを上書き**します（無指定ならシナリオごとの既定が使われます）。`--alert-instruction` は既定のボタン指示を設定するもので、シナリオ自身の `instruction` が優先されます。（[`demos/features/app/scenarios/permission.yaml`](../../demos/features/app/scenarios/permission.yaml) 実物）

## セレクタ（要素の指定）

セレクタは、操作またはアサーションの対象となる **どの要素か** を指定します。1 つ以上のフィールドを与え、複数指定したフィールドは **AND**（すべて一致）で評価し、最低 1 つは必須です。セレクタが一意の要素にどう絞られるか、また曖昧なセレクタが最初の一致を選ばず失敗する理由は [selectors](selectors.md) を参照してください。形式的な形は [dsl-grammar](dsl-grammar.md#2-文法の全体像) にあります。

| フィールド | 型 | 説明 |
|---|---|---|
| `id` | str | 完全一致の `accessibilityIdentifier`。**第一候補**（安定していてローカライズされない） |
| `idMatches` | str | id へのグロブ（例 `"list.row.*"`。複数一致を前提とする） |
| `label` | str | 完全一致の `accessibilityLabel`（可視テキスト）。補助や曖昧性解消に使う |
| `labelMatches` | str | label への正規表現 / 部分一致（`re.search`） |
| `traits` | list[str] | アクセシビリティ trait で絞る（部分集合判定、例 `[button]`） |
| `value` | str | 完全一致の accessibility value |
| `within` | Selector | コンテナに限定する。一致要素は、ネストしたセレクタが解決する要素の内側になければならない（入れ子可） |
| `index` | int | 複数一致の k 番目を選ぶ（負数可）。最終手段であり、順序に依存する |

```yaml
- tap: { id: counter.increment }                               # by id (recommended)
- tap: { label: "Delete" }                                     # by visible label (e.g. an alert button)
- tap: { id: row.action, within: { id: list.row.3 } }          # scoped to a container's subtree
- tap: { labelMatches: "^Item ", traits: [button], index: 0 }  # first matching button, fields AND-ed
```

> まず `id` を使います。要素の集合（count / 存在確認）には `idMatches` を使います。`index` は最終手段です。順序が変わると壊れます。解決の完全な意味論は [selectors](selectors.md) にあります。

## ステップ文法（`steps`）

各ステップは **ちょうど 1 アクション** と、任意の修飾子（`capture:` / `name:`）からなります。1 ステップに 2 アクション以上を書くと検証エラーになります（`scenario/models/steps.py` の `_one_action`）。

| アクション | 形 | 説明 |
|---|---|---|
| `tap` | `tap: <Selector>` | 一意解決を要求する（曖昧なら失敗） |
| `doubleTap` | `doubleTap: <Selector>` | 解決した要素を 2 回素早くタップする |
| `longPress` | `longPress: { sel: <Selector>, duration: <sec> }` | 長押し |
| `type` | `type: { text: "...", into?: <Selector>, submit?: <bool> }` | `into` 指定時は先にフォーカスする |
| `swipe` | `swipe: { on: <Selector>, direction: up\|down\|left\|right }` または `swipe: { from: [x,y], to: [x,y] }` | セレクタ形と座標形は混在できない |
| `pinch` | `pinch: { sel: <Selector>, scale: <num> }` | 2 本指の拡縮。`scale > 0`（`>1` で拡大, `<1` で縮小） |
| `rotate` | `rotate: { sel: <Selector>, radians: <num> }` | 2 本指の回転。`>0` で時計回り |
| `wait` | `wait: { for\|until: ..., timeout: <sec> }` | 条件待機（下記） |
| `assert` | `assert: [ <Assertion>... ]` | ステップ途中の中間検証 |
| `relaunch` | `relaunch: { env?: {...}, args?: [...] }` | アプリを terminate + 再起動し（launch env/args を再適用し、指定分で上書き）、ready まで待つ |
| `setLocation` | `setLocation: { lat: <num>, lon: <num> }` | シミュレータの GPS 位置を上書きする（`simctl location set`） |
| `push` | `push: { payload: {...} }` | この APNs（Apple Push Notification service）ペイロードで疑似プッシュ通知を配信する（`simctl push`） |
| `http` | `http: { method?, url, headers?, body?, status?, saveBody? }` | HTTP リクエストを送る（テストデータ準備 / Webhook / API）。`status` を検証し、ボディを `${vars.<saveBody>}` に保存する |
| `background` | `background: {}` | アプリをバックグラウンドへ送る（Home ボタン） |
| `clearKeychain` | `clearKeychain: {}` | Simulator のキーチェーンをリセットする（保存済みパスワード / 証明書） |
| `clearClipboard` | `clearClipboard: {}` | Simulator のペーストボードをクリアする |
| `overrideStatusBar` | `overrideStatusBar: { time?, batteryLevel?, batteryState?, cellularBars?, wifiBars? }` | 決定的なスクリーンショットのためステータスバーを上書きする |
| `clearStatusBar` | `clearStatusBar: {}` | ステータスバーの上書きを解除する（ライブ表示に戻す） |
| `use` | `use: { component: <file>, with?: {...} }` | 再利用コンポーネントの steps を展開する。コンパイル時マクロ（[再利用](#再利用とデータ駆動とタグ)） |

修飾子:

- `capture: [<token>...]`：このステップだけの証跡（[evidence](evidence.md#b-インライン証跡)）。
- `name: <str>`：ステップ ID（証跡の出力先ディレクトリ名やレポート表示に使う）。省略時は `step<i>`。

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

> 実装上は、`into` を指定すると内部で対象を `tap` してから `type_text` します（`orchestrator.py` の `_do_action`）。

### `swipe`

```yaml
- swipe: { on: { id: comp.swipearea }, direction: left }   # frame center → 100pt in a direction
- swipe: { from: [100, 400], to: [100, 200] }              # raw coordinates (last resort)
```

`{on,direction}` と `{from,to}` は、**どちらか一方だけ**でなければなりません（混在や片側の欠落は検証エラーになります）。

### `doubleTap` / `pinch` / `rotate`（ジェスチャ）

```yaml
- doubleTap: { id: gest.doubletap }                    # two quick taps
- pinch:  { sel: { id: gest.pinch },  scale: 2.0 }     # >1 zooms in, 0<scale<1 zooms out
- rotate: { sel: { id: gest.rotate }, radians: 1.57 }  # >0 clockwise (radians)
```

`scale` は **> 0** が必須です（違反は検証エラー）。`pinch` / `rotate` はマルチタッチが必要で、idb バックエンドでは "needs multiTouch" の理由で失敗します。主な実行先は生成された XCUITest（`pinch(withScale:)` / `rotate(_:)`）です。`doubleTap` は idb で動作します（2 回タップ）。（[`demos/features/app/scenarios/gestures.yaml`](../../demos/features/app/scenarios/gestures.yaml) 実物）

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

### デバイス / システム制御（iOS）

```yaml
- background: {}                                                        # Home ボタン（simctl ui home）
- clearKeychain: {}                                                     # 保存済みパスワード / 証明書をリセット
- clearClipboard: {}                                                    # ペーストボードをクリア
- overrideStatusBar: { time: "9:41", batteryLevel: 100, wifiBars: 3 }   # ステータスバーを固定
- clearStatusBar: {}                                                    # ライブのステータスバーに戻す
```

`setLocation` / `push` と同様、これらは `simctl` 経由で Simulator を操作するため、デバイスごとの制御チャネルが必要で、fake ドライバや並列実行ではクリーンに失敗します。`overrideStatusBar` は、スクリーンショットや `visual` アサーションの直前に時計や電波表示を固定して画像を安定させる用途に向きます。

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
| `visual` | 画面が baseline 画像に一致する（ビジュアルリグレッション） | `visual: { baseline: home.png, threshold: 0.02 }` |

- `exists` はセレクタを **インラインで**書きます（`{ id: ... }` を直書き）。`negate` は任意です。
- `value` / `label` は `sel:` と、`equals` / `contains` / `matches` の **いずれか 1 つ**を指定します。
- `count` は `sel:` と、`equals` / `atLeast` / `atMost` の **いずれか 1 つ**を指定します。
- `enabled` / `disabled` / `selected` はセレクタを直書きします。
- `request` は **観測されたネットワーク通信**に一致するか検証します（[下記](#requestネットワークアサーション)）。`--network` 実行フラグが必要です。
- `visual` はスクリーンショットを baseline 画像とピクセル比較します（[下記](#visualビジュアルリグレッション)）。

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
> （[`demos/features/app/scenarios/network_mock.yaml`](../../demos/features/app/scenarios/network_mock.yaml) 実物）

### `visual`（ビジュアルリグレッション）

```yaml
- assert:
    - visual: { baseline: "home.png", threshold: 0.02, exclude: [{ x: 0, y: 0, w: 390, h: 47 }] }
```

`visual` はスクリーンショットを取得し、`baseline`（run の baselines ディレクトリ内の PNG。`--baselines`、またはシナリオ脇の `baselines/`）とピクセル比較します。`threshold` は許容する差分ピクセルの割合（既定 `0.0` = 完全一致）、`exclude` は比較前にマスクする矩形（スクリーンショットのピクセル座標）のリストで、ステータスバーや時計などに使います。baseline は `approve` コマンド（[cli](cli.md#approve)）か `serve` UI で作成 / 更新します。baseline が無いとアサーションは失敗します。`overrideStatusBar` と併用すると時計 / バッテリーを固定できます。差分は `report.html` に表示されます。

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

### タグと選択

`tags` はシナリオにラベルを付けます。CLI の `--tag` / `--exclude` フラグで実行対象を絞ります。シナリオは、少なくとも 1 つの `--tag` を持ち（または `--tag` 未指定で）、**かつ** `--exclude` のタグを 1 つも持たない場合に実行対象として残ります。`--exclude` が `--tag` より優先されます（`select_scenarios`, `scenario/select.py`）。両フラグともカンマ区切りで複数指定できます。

```yaml
- name: checkout smoke
  tags: [smoke, checkout]
  steps:
    - tap: { id: cart.checkout }
```

```bash
uv run bajutsu run --app sample --tag smoke --exclude wip   # run @smoke, skip anything @wip (across the app's scenarios dir)
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

## ラウンドトリップ（読込 ⇄ 書出）

- `load_scenarios(text) -> list[Scenario]`: YAML 文字列 → 検証済みモデル。
- `dump_scenarios(scenarios) -> str`: モデル → YAML（`None` / 空リスト / 空辞書を間引いて読みやすくします）。

`record` の出力はこの `dump_scenarios` を通ります。生成された YAML は `load_scenarios` でそのまま読み戻せます。

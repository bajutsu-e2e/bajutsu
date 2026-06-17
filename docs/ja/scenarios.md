[English](../scenarios.md) · **日本語**

# シナリオ仕様（オーサリングリファレンス）

シナリオは Bajutsu の **唯一の永続物**です。プレーンな YAML で、git でバージョン管理し、PR でレビューできます。`record`（AI）は最初の 1 回だけ書き、以後は人間が所有・編集します。`run` はこの構造を AI なしで実行します。

実装: `bajutsu/scenario.py`（pydantic、`extra="forbid"` で未知キーを拒否）。

**規範的な文法**（すべての生成規則・型・既定値・検証規則）は [dsl-grammar](dsl-grammar.md) にあります。このページはオーサリングガイドとして、例を使ってシナリオの書き方を示します。

関連: [dsl-grammar](dsl-grammar.md)（形式文法）・[selectors](selectors.md)（セレクタとアサーション評価の仕組み）・[evidence](evidence.md)（証跡）・[run-loop](run-loop.md)（実行）

---

## ファイルの形

1 ファイルは **シナリオの配列**、またはファイルレベル説明を付けたい場合は `{ description, scenarios }` マッピングです。`load_scenarios()` はどちらの形式も受け付けます（どちらでもないトップレベルは拒否されます）。

```yaml
- name: ...        # シナリオ 1
  steps: [...]
- name: ...        # シナリオ 2
  steps: [...]
```

ファイルレベル説明（および任意の per-scenario `description`）を付ける場合:

```yaml
description: このファイルが何を扱うか。
scenarios:
  - name: ...
    description: このシナリオが何を確認するか。
    steps: [...]
```

ファイル説明と各シナリオの `description` は `report.html`（サマリーヘッダーと各シナリオカード）および `bajutsu serve` の UI に表示されます。

## トップレベル構造（`Scenario`）

| キー | 型 | 既定 | 説明 |
|---|---|---|---|
| `name` | str | 必須 | シナリオ名（レポート / JUnit testcase / codegen のメソッド名に使う） |
| `description` | str | なし | 任意の説明文。シナリオの report カードと serve UI に表示 |
| `tags` | list[str] | `[]` | 選択ラベル。CLI の `--tag` / `--exclude` で実行対象を絞る（[再利用とデータ駆動とタグ](#再利用とデータ駆動とタグ)） |
| `data` / `dataFile` | list / str | なし | データ駆動の行 —— インライン `data` か `dataFile`（CSV パス）。1 行 1 run に展開し `${row.col}` を置換。排他（[再利用とデータ駆動とタグ](#再利用とデータ駆動とタグ)） |
| `preconditions` | object | `{}` | テスト前の環境準備（下記） |
| `steps` | list | 必須 | アクションの並び（下記） |
| `expect` | list | `[]` | 全ステップ成功後の最終アサーション（[selectors](selectors.md#アサーション評価)） |
| `capturePolicy` | list | `[]` | 繰り返し発火する証跡ルール（[evidence](evidence.md#a-capturepolicyルール方式)） |
| `network` | object | なし | `{ filter: { domains: [...] } }` — `filter.domains` でレポートの Steps タイムラインに差し込む通信を URL ホストで絞る（親ドメインはサブドメインに一致）。未指定は全件表示。Network タブは常に全件（[reporting](reporting.md#reporthtml)） |
| `mocks` | list | `[]` | 決定的なネットワークスタブ —— 一致する送信リクエストにネットワークへ行かず定型レスポンスを返す（[ネットワークモック](#ネットワークモック決定的スタブ)） |
| `redact` | object | なし | 証跡保存前のマスク対象（[evidence](evidence.md#マスキングredact)） |
| `dismissAlerts` | bool / object | なし（ON） | 視覚**アラートガード** —— idb から見えない OS プロンプトを片付ける。既定 ON。`false` で無効化、`{ instruction: "tap Allow" }` で ON のままボタンを指定。CLI `--dismiss-alerts`/`--no-dismiss-alerts` が上書き（[下記](#dismissalertsシステムアラートガード)） |

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

実装: `scenario.py` `Preconditions`。runner の `launch_driver` がこれを読んで起動手順を組みます（[run-loop](run-loop.md#runner実行パイプライン)）。

| キー | 型 | 既定 | 説明 | 配線 |
|---|---|---|---|---|
| `erase` | bool | `false` | 各テスト前にシム全体を wipe（`simctl erase` —— アプリ/データ/設定）。既定はオフ。`reinstall` が全 wipe なしでアプリを fresh に保つので、まっさらなデバイスが必要なテストだけ `true` に | ✅ |
| `reinstall` | `clean` \| `overwrite` | `clean` | config が `appPath` を指定したとき各 run 前にどう再インストールするか: `clean` = uninstall してから install（アプリ + データを fresh に）。`overwrite` = 既存アプリに上書き install（データコンテナは保持） | ✅ |
| `launchArgs` | list[str] | `[]` | 起動引数（config の `launchArgs` に追記） | ✅ |
| `launchEnv` | dict | `{}` | 起動 env（`SIMCTL_CHILD_*` で注入。config の `launchEnv` にマージ） | ✅ |
| `deeplink` | str | なし | 起動後に `simctl openurl` で開く | ✅ |
| `locale` | str | なし | 起動時に locale/言語を強制（`-AppleLocale`/`-AppleLanguages`）。app/config の既定を上書き | ✅ |
| `setup` | str | なし | 再利用する前段シナリオファイル（このシナリオからの相対）。その steps を本編の前に実行 | ✅ |

> `launchEnv` の解決順は **config の `launchEnv` < preconditions の `launchEnv`**（テストに近い方が優先）です。`launch_driver` は `{**eff.launch_env, **pre.launch_env}` でマージします。

## dismissAlerts（システムアラートガード）

idb は **SpringBoard レベルのプロンプト**（iOS の "Save Password?"、権限リクエスト、"Allow Paste" など）を見ることも tap することもできません。これらはアプリを覆って要素ツリーを潰し、ステップをブロックします。**アラートガード**は視覚ベースのフォールバック（`alerts.py`）で、ブロックされたステップでスクリーンショットを撮り、Claude にどこを tap するか尋ね、プロンプトを片付けてからそのステップを 1 回再試行します（[詳細](recording.md#システムアラートの自動対処)）。

既定で **ON** であり、**ステップ（または `expect`）がブロックされたときだけ**発火するため、成功するシナリオはモデルを呼びません。`ANTHROPIC_API_KEY` が必要ですが、無くても no-op になるだけで run への影響はありません。シナリオごとに動作を変えるには `dismissAlerts` を使います。

| 形 | 意味 |
|---|---|
| （省略） | ON。**最も無害な**ボタンを押す（"Not Now" / "Don't Allow" / "Cancel"） |
| `dismissAlerts: false` | このシナリオでは無効 |
| `dismissAlerts: { instruction: "tap Allow" }` | ON のまま、instruction が指すボタンを押す —— 例えば権限を**許可**する |
| `dismissAlerts: { enabled: false }` | 無効（`false` の明示的なオブジェクト形） |

```yaml
- name: grant notification permission
  dismissAlerts: { instruction: "tap Allow" }   # dismiss ではなくプロンプトを許可する
  steps:
    - tap:  { id: sys.requestNotif }
    - wait: { for: { id: sys.notif.authorized }, timeout: 4 }   # ガードが Allow を押し、これが通る
```

CLI の `--dismiss-alerts` / `--no-dismiss-alerts` は**全シナリオを上書き**します（無指定ならシナリオごとの既定が使われます）。`--alert-instruction` は既定のボタン指示で、シナリオ自身の `instruction` が優先されます。（[`demos/features/app/scenarios/permission.yaml`](../../demos/features/app/scenarios/permission.yaml) 実物）

## セレクタ（要素の指定）

セレクタは操作・検証の対象となる **どの要素か** を指定します。1 つ以上のフィールドを与え、複数指定は **AND**（すべて一致）で、最低 1 つは必須です。セレクタが一意の要素にどう絞られるか、また曖昧なセレクタが最初の一致を選ばず失敗する理由は [selectors](selectors.md) を参照してください。形式的な定義は [dsl-grammar](dsl-grammar.md#2-文法の全体像) にあります。

| フィールド | 型 | 説明 |
|---|---|---|
| `id` | str | 完全一致の `accessibilityIdentifier` —— **第一候補**（安定・非ローカライズ） |
| `idMatches` | str | id へのグロブ（例 `"list.row.*"`。複数一致を前提） |
| `label` | str | 完全一致の `accessibilityLabel`（可視テキスト）—— 補助 / 曖昧性解消 |
| `labelMatches` | str | label への正規表現 / 部分一致（`re.search`） |
| `traits` | list[str] | アクセシビリティ trait で絞る（部分集合判定、例 `[button]`） |
| `value` | str | 完全一致の accessibility value |
| `within` | Selector | コンテナに限定 —— 一致要素はネストしたセレクタが解決する要素の内側にある必要がある（入れ子可） |
| `index` | int | 複数一致の k 番目を選ぶ（負数可）—— 最終手段・順序依存 |

```yaml
- tap: { id: counter.increment }                               # id で（推奨）
- tap: { label: "Delete" }                                     # 可視 label で（例: アラートのボタン）
- tap: { id: row.action, within: { id: list.row.3 } }          # コンテナの部分木に限定
- tap: { labelMatches: "^Item ", traits: [button], index: 0 }  # 最初に一致する button、フィールドは AND
```

> まず `id` を使います。要素の集合（count / 存在確認）には `idMatches` を使います。`index` は最終手段です。順序が変わると壊れます。解決の完全な意味論は [selectors](selectors.md) にあります。

## ステップ文法（`steps`）

各ステップは **ちょうど 1 アクション** + 任意の修飾子（`capture:` / `name:`）です。1 ステップに 2 アクション以上を書くと検証エラーになります（`scenario.py` `_one_action`）。

| アクション | 形 | 説明 |
|---|---|---|
| `tap` | `tap: <Selector>` | 一意解決を要求（曖昧なら失敗） |
| `doubleTap` | `doubleTap: <Selector>` | 解決した要素を 2 回素早くタップ |
| `longPress` | `longPress: { sel: <Selector>, duration: <sec> }` | 長押し |
| `type` | `type: { text: "...", into?: <Selector>, submit?: <bool> }` | `into` 指定時は先にフォーカス |
| `swipe` | `swipe: { on: <Selector>, direction: up\|down\|left\|right }` または `swipe: { from: [x,y], to: [x,y] }` | セレクタ形と座標形は混在不可 |
| `pinch` | `pinch: { sel: <Selector>, scale: <num> }` | 2 本指の拡縮。`scale > 0`（`>1` 拡大, `<1` 縮小） |
| `rotate` | `rotate: { sel: <Selector>, radians: <num> }` | 2 本指の回転。`>0` で時計回り |
| `wait` | `wait: { for\|until: ..., timeout: <sec> }` | 条件待機（下記） |
| `assert` | `assert: [ <Assertion>... ]` | ステップ途中の中間検証 |
| `relaunch` | `relaunch: { env?: {...}, args?: [...] }` | アプリを terminate + 再起動（launch env/args を再適用＋上書き）し、ready まで待つ |
| `setLocation` | `setLocation: { lat: <num>, lon: <num> }` | シミュレータの GPS 位置を上書き（`simctl location set`） |
| `push` | `push: { payload: {...} }` | この APNs ペイロードで疑似プッシュ通知を配信（`simctl push`） |
| `use` | `use: { component: <file>, with?: {...} }` | 再利用コンポーネントの steps を展開 —— コンパイル時マクロ（[再利用](#再利用とデータ駆動とタグ)） |

修飾子:

- `capture: [<token>...]` — このステップだけの証跡（[evidence](evidence.md#b-インライン証跡)）。
- `name: <str>` — ステップ ID（証跡の出力先ディレクトリ名・レポート表示）。省略時は `step<i>`。

### `tap`

```yaml
- tap: { id: counter.increment }      # id 完全一致（推奨）
- tap: { label: "Delete" }            # label 完全一致（in-app アラート等、id が無い時）
```

### `type`

```yaml
- type: { text: "a@b.com", into: { id: auth.email } }   # フォーカスしてから入力
- type: { text: "hello", submit: true }                 # submit で改行 / 確定（フィールドは現在のフォーカス）
```

> 実装上、`into` 指定時は内部で対象を `tap` してから `type_text` します（`orchestrator.py` `_do_action`）。

### `swipe`

```yaml
- swipe: { on: { id: comp.swipearea }, direction: left }   # frame 中心 → 方向へ 100pt
- swipe: { from: [100, 400], to: [100, 200] }              # 生座標（最終手段）
```

`{on,direction}` と `{from,to}` は **完全にどちらか一方**でなければなりません（混在・片側欠落は検証エラーになります）。

### `doubleTap` / `pinch` / `rotate`（ジェスチャ）

```yaml
- doubleTap: { id: gest.doubletap }                    # 2 回素早くタップ
- pinch:  { sel: { id: gest.pinch },  scale: 2.0 }     # >1 拡大, 0<scale<1 縮小
- rotate: { sel: { id: gest.rotate }, radians: 1.57 }  # >0 時計回り（ラジアン）
```

`scale` は **> 0** が必須です（違反は検証エラー）。`pinch` / `rotate` はマルチタッチが必要で、idb バックエンドでは "needs multiTouch" のエラーで失敗します。主な実行先は生成された XCUITest（`pinch(withScale:)` / `rotate(_:)`）です。`doubleTap` は idb で動作します（2 回タップ）。（[`demos/features/app/scenarios/gestures.yaml`](../../demos/features/app/scenarios/gestures.yaml) 実物）

### `wait`（条件待機）

固定 sleep はサポートされていません。**`timeout` は必須**（無限待ちは不可）。

```yaml
- wait: { for: { id: home.title }, timeout: 5 }            # 要素が現れるまで
- wait: { until: { gone: { id: home.spinner } }, timeout: 15 }  # 要素が消えるまで
- wait: { until: screenChanged, timeout: 5 }              # query() が変化するまで
- wait: { until: settled, timeout: 3 }                    # 画面が安定する（変化が止まる）まで
- wait: { until: { request: { method: GET, path: /items, status: 200 } }, timeout: 8 }  # 一致する通信が観測されるまで
```

`for` と `until` は排他（片方のみ）です。`until` の値は `screenChanged` / `settled` / `{ gone: <Selector> }` / `{ request: <RequestMatch> }` のいずれかです。`request` 形式はネットワーク collector（[evidence](evidence.md)、`--network` 実行フラグ）をポーリングし、観測した通信が 1 件でも一致するまで待ちます（マッチャは [`request` アサーション](#requestネットワークアサーション)と同じです: `method` / `url` / `urlMatches` / `path` / `pathMatches` / `status` / `bodyMatches` を AND で評価し、`count` で閾値を上げられます）。エンドポイントは `url`（完全一致）か `urlMatches`（正規表現/部分一致）、または `path` で指定します。タイムアウトの扱いは種別で異なります（[run-loop](run-loop.md#待機条件待機)）。
`for` / `gone` / `screenChanged` / `request` はタイムアウトするとステップ失敗になります。`settled` は安定化ヒントなので、タイムアウトしても現在画面で続行し、失敗にはなりません。

### `assert`（中間検証）

ステップ途中での検証。DSL（ドメイン固有言語）は `expect` と同一（次節）。

```yaml
- assert:
    - disabled: { id: auth.submit }
```

### `setLocation` / `push`（デバイス制御）

```yaml
- setLocation: { lat: 35.681, lon: 139.767 }              # simctl location set
- push: { payload: { aps: { alert: "You have mail" } } }  # simctl push（APNs ペイロード）
```

どちらも `simctl` 経由で Simulator を操作し、デバイスごとの制御チャネルが必要です。fake ドライバや並列実行では使えず、その場合ステップはクリーンに失敗します（クラッシュしません）。`push` は `payload` を APNs JSON として対象アプリに配信します。

## アサーション DSL

`expect`（最終検証）と `assert`（中間検証）で共通です。リスト内はすべて **AND** で評価され、1 つでも失敗するとステップ失敗になります。評価の仕組み（要素解決・比較）は [selectors](selectors.md#アサーション評価) にあります。

| アサーション | 意味 | 例 |
|---|---|---|
| `exists` | 一致要素が存在（`negate: true` で不在検証） | `exists: { id: home.title }` / `exists: { id: settings.banner, negate: true }` |
| `value` | accessibility value の一致 | `value: { sel: { id: counter.value }, equals: "2" }` |
| `label` | label の一致 / 部分一致 / 正規表現 | `label: { sel: { id: settings.status }, contains: "完了" }` |
| `count` | 一致要素数 | `count: { sel: { idMatches: "list.row.*" }, equals: 5 }` |
| `enabled` / `disabled` | 操作可否（traits の `notEnabled`） | `disabled: { id: auth.submit }` |
| `selected` | 選択 / トグル状態（trait `selected`） | `selected: { id: tab.home }` |
| `request` | 一致するネットワーク通信が観測された（`--network` が必要） | `request: { method: POST, path: /login, status: 200, count: 1 }` |

- `exists` はセレクタを **インラインで**書きます（`{ id: ... }` 直書き）。`negate` は任意です。
- `value` / `label` は `sel:` + `equals` / `contains` / `matches` の **いずれか 1 つ**を指定します。
- `count` は `sel:` + `equals` / `atLeast` / `atMost` の **いずれか 1 つ**を指定します。
- `enabled` / `disabled` / `selected` はセレクタを直書きします。
- `request` は **観測されたネットワーク通信**に一致するか検証します（[下記](#requestネットワークアサーション)）。`--network` 実行フラグが必要です。

> **ロケール注意**: `label`/`value` の文字列比較や可視テキストを見るアサーションは翻訳で壊れます。これらは config の固定 locale を前提として書き、セレクタ自体は `id` で書いてください。

### `request`（ネットワークアサーション）

`request` は run のネットワーク collector が **一致する HTTP 通信を観測した**ことを表明します（`--network` 実行フラグとアプリ内の BajutsuKit が必要）。同じマッチャが `until: { request: ... }` の wait と `mocks`（下記）で共有されます。マッチフィールドは最低 1 つ必須で、列挙したフィールドは **AND** で評価されます。

| フィールド | 型 | 説明 |
|---|---|---|
| `method` | str | HTTP メソッド（`GET`, `POST`, …） |
| `url` | str | 完全一致の URL（エンドポイント） |
| `urlMatches` | str | URL への正規表現 / 部分一致（クエリはここ） |
| `path` | str | 完全一致パス（クエリ無視） |
| `pathMatches` | str | パスへの正規表現 |
| `status` | int | レスポンスのステータスコード |
| `bodyMatches` | str | **リクエストボディ**への正規表現 / 部分一致 |
| `count` | int | 一致した通信数 —— アサーションでは **厳密**、wait では **下限** |

```yaml
- assert:
    - request: { method: POST, path: /login, status: 200, count: 1 }
    - request: { urlMatches: "/search", bodyMatches: "apple" }   # リクエストボディで一致
```

> `count` はマッチフィールド **ではない** —— `method` / `url` / `urlMatches` / `path` / `pathMatches` /
> `status` / `bodyMatches` の少なくとも 1 つが必要。
> （[`demos/features/app/scenarios/network_mock.yaml`](../../demos/features/app/scenarios/network_mock.yaml) 実物）

## ネットワークモック（決定的スタブ）

`mocks` はテストをライブサーバから独立させます。送信リクエストが一致すると、BajutsuKit はネットワークへ行かずに定型レスポンスを返します。各モックは `{ match, respond }` です。

- **`match`** は[リクエストマッチャ](#requestネットワークアサーション)の **リクエスト側**フィールドを再利用します（`method` / `url` / `urlMatches` / `path` / `pathMatches` / `bodyMatches`）。`status` / `count` はモックの `match` には **適用されません**。
- **`respond`** は定型の返答です: `status`（既定 `200`）・`headers`（既定 `{}`）・`body`（文字列）・`delayMs`（人工遅延）。`respond` を省くと空の `200` を返します。

```yaml
- name: GET answered by a mock stub
  mocks:
    - match: { method: GET, urlMatches: "example.com" }
      respond:
        status: 418                       # 実 example.com は 200 を返す。418 はスタブが応答した証拠
        headers: { Content-Type: text/plain }
        body: "stubbed by bajutsu"
  steps:
    - tap:  { id: net.fetch }
    - wait: { until: { request: { method: GET, urlMatches: "example.com", status: 418 } }, timeout: 6 }
  expect:
    - request: { method: GET, urlMatches: "example.com", status: 418 }
```

モックは `BAJUTSU_MOCKS` env で BajutsuKit に渡されます（`dump_mocks`, `scenario.py:638`）。形式的な定義は [dsl-grammar](dsl-grammar.md#2-文法の全体像) にあります。

## 再利用とデータ駆動とタグ

コア文法の周りに小さなテンプレートとマクロ層があります。これはロード時、決定的 run の **前**に実行されるため、ランナーは常に展開済みのプレーンなシナリオだけを見ます。規範的な規則（展開順・`${ns.key}` 補間・深さ制限）は [dsl-grammar](dsl-grammar.md#6-テンプレートとマクロ層) にあります。ここではオーサリング視点から説明します。

### コンポーネント（`use` → 再利用ステップ）

**コンポーネント**は別ファイルで、`params` のリストと、それを `${params.<name>}` で参照する `steps` のリストからなります。`use` ステップが `with` で params を束縛して呼び出します。`use` は **コンパイル時マクロ**であり、`expand_components`（`scenario.py:474`）が run の前にコンポーネントの置換済みステップに置き換えます。展開は再帰的で、コンポーネントが別のコンポーネントを `use` でき、深さは 25 までです。params 不足・未知 params・未宣言を指す残留 `${params.*}`・循環参照ではエラーになります。`use` は run に残らないため決定性に影響しません。

```yaml
# login.component.yaml —— コンポーネントファイル（単一マッピング、別ロード）
params: [user, pass]
steps:
  - type: { text: "${params.user}", into: { id: auth.user } }
  - type: { text: "${params.pass}", into: { id: auth.pass } }
  - tap:  { id: auth.submit }
```

```yaml
# シナリオ側 —— 上の 3 ステップに params を置換して展開される
steps:
  - use: { component: login.component.yaml, with: { user: alice, pass: hunter2 } }
  - tap: { id: home.tab }
```

### データ駆動シナリオ（`data` / `dataFile`）

`data`（インライン行）か `dataFile`（CSV パス。両者は **排他**）を持つシナリオは、`${row.<column>}` を置換して **1 行 1 シナリオ**に展開されます（`expand_data`, `scenario.py:537`）。派生シナリオは `"<name> [row N: col=val, …]"` に改名され、元の preconditions を保ちます。各行ともアプリは fresh に再インストールされ、テンプレートの `erase` / `reinstall` を継承します。

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

> **ちょうど 1 トークン**だけの文字列（`"${row.qty}"`）は **生**の値になる（数値は数値のまま）。大きな
> 文字列に **埋め込まれた**トークンはテキストとして差し込まれる（`"item-${row.id}"`）。

CSV の `dataFile` は列名を与えるヘッダ行を持ち、以降の各行が 1 シナリオになります。

### タグと選択

`tags` はシナリオにラベルを付けます。CLI の `--tag` / `--exclude` で実行対象を絞ります。シナリオは少なくとも 1 つの `--tag` を持つ（または `--tag` 未指定）、かつ `--exclude` のタグを 1 つも持たない場合に実行対象になります。`--exclude` が `--tag` より優先されます（`select_scenarios`, `scenario.py:560`）。両フラグはカンマ区切りで複数指定できます。

```yaml
- name: checkout smoke
  tags: [smoke, checkout]
  steps:
    - tap: { id: cart.checkout }
```

```bash
uv run bajutsu run --app sample --tag smoke --exclude wip   # @smoke を実行、@wip は除外（アプリのシナリオディレクトリ全体に対して）
```

### シークレット（`${secrets.X}`）

シークレットの環境変数名を config で宣言します（`secrets: [API_TOKEN, ...]`）。宣言した各名 `X` は環境から解決され、**アクション時**に実行ステップへ `${secrets.X}` として置換されます。シナリオファイルは **トークン**を保持し、実値は持ちません。リテラル値は証跡で **自動マスク**されるため、シークレットはコミット・レビューしても安全です。`${params.*}` / `${row.*}`（ロード時展開）と異なり、この名前空間は run ループが解決します。

```yaml
# config が宣言: secrets: [API_TOKEN]
steps:
  - type: { text: "${secrets.API_TOKEN}", into: { id: auth.token } }   # 実値が入力され、レポートにはトークンが残る
```

### ランタイム変数 (`${vars.*}`)

ステップの `extract` 修飾子は、ステップ実行後に UI 要素のプロパティを `vars.*` に取り込みます。後続のステップやシナリオレベルの `expect` で `${vars.<name>}` として参照できます。

```yaml
steps:
  - tap: { id: counter.inc }
    extract:
      count: { sel: { id: counter.value } }          # vars.count ← 要素の value（既定）
      heading: { sel: { id: header }, prop: label }   # vars.heading ← 要素の label
  - assert:
      - value: { sel: { id: other.field }, equals: "${vars.count}" }
```

各 `extract` エントリは `sel`（セレクタ、`resolve_unique` で一意解決）と、省略可能な `prop`（`value` | `label` | `identifier`、既定 `value`）を指定します。セレクタが一意に解決できない場合やプロパティが `None` の場合、ステップは失敗します。

## capture トークン文法

`capture:`（ステップ単体）と `capturePolicy[].capture`（ルール）で共通です。形は `<種別>[.<修飾子>]` です。

- **種別**: `screenshot` / `elements` / `actionLog` / `deviceLog` / `network` / `video` / `appTrace`
- **修飾子**: `before` / `after` / `around` / `onError`

検証は種別・修飾子の集合に対して行われます（`scenario.py` `_validate_capture`）。種別ごとの取得タイミングと取得可否は [evidence](evidence.md#証跡種別と取得タイミング) にあります。

## YAML の注意点

PyYAML（YAML 1.1）は `on`/`off`/`yes`/`no` を真偽値に解決します。`capturePolicy` のトリガーキー `on:` が `True` になるのを防ぐため、Bajutsu の YAML ローダ（`_yaml.py`）は **`true`/`false` のみを真偽値**として扱い、`on`/`off`/`yes`/`no` は文字列のまま読みます。

## ラウンドトリップ（読込 ⇄ 書出）

- `load_scenarios(text) -> list[Scenario]`: YAML 文字列 → 検証済みモデル。
- `dump_scenarios(scenarios) -> str`: モデル → YAML（`None` / 空リスト / 空辞書を間引いて読みやすくします）。

`record` の出力はこの `dump_scenarios` を通ります。生成された YAML は `load_scenarios` でそのまま読み戻せます。

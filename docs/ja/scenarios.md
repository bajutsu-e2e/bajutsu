[English](../scenarios.md) · **日本語**

# シナリオ仕様（オーサリングリファレンス）

> シナリオは Bajutsu の **唯一の永続物**。プレーンな YAML で、git でバージョン管理し、PR で
> レビューできる。`record`（AI）は最初の 1 回だけ書き、以後は人間が所有・編集する。`run` は
> この構造を **AI 非依存**で実行する。
>
> 実装: `bajutsu/scenario.py`（pydantic、`extra="forbid"` で未知キーを拒否）。

関連: [selectors](selectors.md)（セレクタとアサーション評価の仕組み）・[evidence](evidence.md)（証跡）・[run-loop](run-loop.md)（実行）

---

## ファイルの形

1 ファイル = **シナリオの配列**。`load_scenarios()` はトップレベルがリストでなければ拒否する。

```yaml
- name: ...        # シナリオ 1
  steps: [...]
- name: ...        # シナリオ 2
  steps: [...]
```

## トップレベル構造（`Scenario`）

| キー | 型 | 既定 | 説明 |
|---|---|---|---|
| `name` | str | 必須 | シナリオ名（レポート / JUnit testcase / codegen のメソッド名に使う） |
| `preconditions` | object | `{}` | テスト前の環境準備（下記） |
| `steps` | list | 必須 | アクションの並び（下記） |
| `expect` | list | `[]` | 全ステップ成功後の最終アサーション（[selectors](selectors.md#アサーション評価)） |
| `capturePolicy` | list | `[]` | 繰り返し発火する証跡ルール（[evidence](evidence.md#a-capturepolicyルール方式)） |
| `redact` | object | なし | 証跡保存前のマスク対象（[evidence](evidence.md#マスキングredact)） |

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

（[`sample/scenarios/smoke.yaml`](../../sample/scenarios/smoke.yaml) 実物）

## preconditions（環境準備）

実装: `scenario.py` `Preconditions`。runner の `launch_driver` がこれを読んで起動手順を組む
（[run-loop](run-loop.md#runner実行パイプライン)）。

| キー | 型 | 既定 | 説明 | 配線 |
|---|---|---|---|---|
| `erase` | bool | `true` | 各テスト前に `simctl erase`（クリーン環境） | ✅ |
| `launchArgs` | list[str] | `[]` | 起動引数（config の `launchArgs` に追記） | ✅ |
| `launchEnv` | dict | `{}` | 起動 env（`SIMCTL_CHILD_*` で注入。config の `launchEnv` にマージ） | ✅ |
| `deeplink` | str | なし | 起動後に `simctl openurl` で開く | ✅ |
| `locale` | str | なし | （**未配線**: 値は持つが起動時に適用していない） | ⚠️ |
| `setup` | str | なし | 再利用する前段シナリオ（**未配線**: スキーマのみ） | ⚠️ |

> `launchEnv` の解決順は **config の `launchEnv` < preconditions の `launchEnv`**（テストに近い方が
> 勝つ）。`launch_driver` で `{**eff.launch_env, **pre.launch_env}` とマージする。

## ステップ文法（`steps`）

各ステップは **ちょうど 1 アクション** + 任意の修飾子（`capture:` / `name:`）。1 ステップに
2 アクション以上を書くと検証エラー（`scenario.py` `_one_action`）。

| アクション | 形 | 説明 |
|---|---|---|
| `tap` | `tap: <Selector>` | 一意解決を要求（曖昧なら失敗） |
| `longPress` | `longPress: { sel: <Selector>, duration: <sec> }` | 長押し |
| `type` | `type: { text: "...", into?: <Selector>, submit?: <bool> }` | `into` 指定時は先にフォーカス |
| `swipe` | `swipe: { on: <Selector>, direction: up\|down\|left\|right }` または `swipe: { from: [x,y], to: [x,y] }` | セレクタ形と座標形は混在不可 |
| `wait` | `wait: { for\|until: ..., timeout: <sec> }` | 条件待機（下記） |
| `assert` | `assert: [ <Assertion>... ]` | ステップ途中の中間検証 |
| `relaunch` | `relaunch: { env?: {...}, args?: [...] }` | **未実装**（`NotImplementedError`） |

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

> 実装上、`into` 指定時は内部で対象を `tap` してから `type_text` する（`orchestrator.py` `_do_action`）。

### `swipe`

```yaml
- swipe: { on: { id: comp.swipearea }, direction: left }   # frame 中心 → 方向へ 100pt
- swipe: { from: [100, 400], to: [100, 200] }              # 生座標（最終手段）
```

`{on,direction}` と `{from,to}` は **完全にどちらか一方**でなければならない（混在・片側欠落は検証エラー）。

### `wait`（条件待機）

固定 sleep は文法として存在しない。**`timeout` は必須**（無限待ち禁止）。

```yaml
- wait: { for: { id: home.title }, timeout: 5 }            # 要素が現れるまで
- wait: { until: { gone: { id: home.spinner } }, timeout: 15 }  # 要素が消えるまで
- wait: { until: screenChanged, timeout: 5 }              # query() が変化するまで
- wait: { until: settled, timeout: 3 }                    # 画面が安定する（変化が止まる）まで
```

`for` と `until` は排他（片方のみ）。`until` の値は `screenChanged` / `settled` / `{ gone: <Selector> }`。
タイムアウトの扱いは種別で異なる（[run-loop](run-loop.md#待機条件待機)）:
`for` / `gone` / `screenChanged` はタイムアウト = ステップ失敗。`settled` は安定化ヒントなので
タイムアウトしても現在画面で続行（失敗にしない）。

### `assert`（中間検証）

ステップ途中での検証。DSL は `expect` と同一（次節）。

```yaml
- assert:
    - disabled: { id: auth.submit }
```

## アサーション DSL

`expect`（最終検証）と `assert`（中間検証）で共通。リスト内は全て **AND**、1 つでも失敗ならステップ失敗。
評価の仕組み（要素解決・比較）は [selectors](selectors.md#アサーション評価)。

| アサーション | 意味 | 例 |
|---|---|---|
| `exists` | 一致要素が存在（`negate: true` で不在検証） | `exists: { id: home.title }` / `exists: { id: settings.banner, negate: true }` |
| `value` | accessibility value の一致 | `value: { sel: { id: counter.value }, equals: "2" }` |
| `label` | label の一致 / 部分一致 / 正規表現 | `label: { sel: { id: settings.status }, contains: "完了" }` |
| `count` | 一致要素数 | `count: { sel: { idMatches: "list.row.*" }, equals: 5 }` |
| `enabled` / `disabled` | 操作可否（traits の `notEnabled`） | `disabled: { id: auth.submit }` |
| `selected` | 選択 / トグル状態（trait `selected`） | `selected: { id: tab.home }` |

- `exists` はセレクタを **インラインで**書く（`{ id: ... }` 直書き）。`negate` は任意。
- `value` / `label` は `sel:` + `equals` / `contains` / `matches` の **いずれか 1 つ**。
- `count` は `sel:` + `equals` / `atLeast` / `atMost` の **いずれか 1 つ**。
- `enabled` / `disabled` / `selected` はセレクタを直書き。

> **ロケール注意**: `label`/`value` の文字列比較や可視テキストを見るアサーションは翻訳で壊れる。
> これらは config の固定 locale 前提で書き、セレクタ自体は `id` で書く。

## capture トークン文法

`capture:`（ステップ単体）と `capturePolicy[].capture`（ルール）で共通。形は `<種別>[.<修飾子>]`。

- **種別**: `screenshot` / `elements` / `actionLog` / `deviceLog` / `network` / `video` / `appTrace`
- **修飾子**: `before` / `after` / `around` / `onError`

検証は種別・修飾子の集合に対して行われる（`scenario.py` `_validate_capture`）。種別ごとの
取得タイミングと現状の取得可否（`network`/`appTrace` は未実装）は [evidence](evidence.md#証跡種別と取得タイミング)。

## YAML の注意点

PyYAML（YAML 1.1）は `on`/`off`/`yes`/`no` を真偽値に解決してしまう。`capturePolicy` の
トリガーキー `on:` が `True` になるのを防ぐため、Bajutsu の YAML ローダ（`_yaml.py`）は
**`true`/`false` のみを真偽値**として扱い、`on`/`off`/`yes`/`no` は文字列のまま読む。

## ラウンドトリップ（読込 ⇄ 書出）

- `load_scenarios(text) -> list[Scenario]`: YAML 文字列 → 検証済みモデル。
- `dump_scenarios(scenarios) -> str`: モデル → YAML（`None` / 空リスト / 空辞書を間引いて読みやすく）。

`record` の出力はこの `dump_scenarios` を通る。生成された YAML は `load_scenarios` でそのまま読み戻せる。

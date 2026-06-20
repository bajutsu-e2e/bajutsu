[English](../run-loop.md) · **日本語**

# 実行ループ（Orchestrator）と実行パイプライン

> Tier 2 の決定的ランナーです。各ステップを **act → (wait) → verify** で処理し、合否は機械アサーション
> のみで決めます。AI は関与しません。最初の失敗で停止します。
>
> 実装: `bajutsu/orchestrator.py`（ループ本体）・`bajutsu/runner.py`（実機起動 + レポート連結）。

関連: [scenarios](scenarios.md) ・ [selectors](selectors.md) ・ [evidence](evidence.md) ・ [reporting](reporting.md)

---

## `run_scenario`（1 シナリオの実行）

```python
def run_scenario(driver, scenario, clock=None, sink=None, on_blocked=None) -> RunResult
```

- `driver`: `base.Driver`（実ドライバ or `FakeDriver`）。ループはこれにしか依存しません。
- `clock`: 時刻 / sleep の注入（テストで待機を決定化）。既定 `RealClock`（`time.monotonic` / `time.sleep`）。
- `sink`: 証跡の出力先（既定 `NullSink` = 何も書かない）（[evidence](evidence.md)）。
- `on_blocked`: ステップ失敗時に「ブロッカー（システムアラート等）を片付けたら True」を返すハンドラです。True を返した場合、**そのステップを 1 回だけ再試行します**（[recording の alert guard](recording.md#システムアラートの自動対処)）。

### 1 ステップの流れ

各ステップ `i` について（`orchestrator.py` 内）:

1. `kind = _action_of(step)`：どのアクションか判定します。
2. `step_id = step.name or f"step{i}"`：証跡の出力単位です。
3. （`capturePolicy` に `screenChanged` トリガーがあれば）操作前の `query()` を控えます。
4. **区間証跡を開始**します（`video` / `deviceLog` のうち、操作前から始める必要があるもの）。`_pre_intervals` は「ステップ自身から判定可能なトリガー」だけを拾います（`screenChanged`/`error` は遅すぎるため対象外）。
5. `_run_step_body` で **act**（or wait / assert）を実行し、`(ok, reason, assertion_results)` を得ます。
6. 失敗かつ `on_blocked` がブロッカーを片付けた場合は **1 回再試行します**。
7. **区間証跡を停止**します（ステップが落ち着いてから）。アーティファクトを記録します。
8. **瞬時証跡**（`screenshot` / `elements`）を取得します（`_collect_captures` の発火結果）。
9. `StepOutcome` を積みます。失敗なら `failure` を設定して **break** します。

### `_run_step_body`（act / wait / assert の分岐）

- `wait` → `_wait`（条件待機、下記）。
- `assert_` → `assertions.evaluate(driver.query(), ...)` を評価し AND を取ります。
- それ以外（tap/longPress/type/swipe/relaunch）→ `_do_action`。
- `SelectorError` / `NotImplementedError` を捕捉して `(False, 理由, [])` に変換します（例外を上に投げません）。

### `_do_action`（操作の実体）

| アクション | 実体 |
|---|---|
| `tap` | `driver.tap(sel)` |
| `longPress` | `driver.long_press(sel, duration)` |
| `type` | `into` があれば先に `driver.tap(into)` → `driver.type_text(text)` |
| `swipe` | `{from,to}` ならそのまま `driver.swipe`。`{on,direction}` なら対象を `resolve_unique` → frame 中心から方向へ 100pt（`_SWIPE_DIST`） |
| `relaunch` | runner が注入する relauncher でアプリを terminate + 再起動します（launch env/args 再適用＋上書き）。ready まで待ちます |

## 待機（条件待機）

`_wait(driver, w, clock) -> (ok, reason)`。固定 sleep はありません。`query()` を `_POLL = 0.05s` 間隔でポーリングし、条件成立か `timeout` 到達まで繰り返します。

| 形 | 成立条件 | タイムアウト時 |
|---|---|---|
| `for: <sel>` | 一致要素が現れる | **失敗** |
| `until: { gone: <sel> }` | 一致要素が消える | **失敗** |
| `until: screenChanged` | `query()` が初期値から変化 | **失敗** |
| `until: settled` | 画面が安定（連続 2 回 `query()` 不変、かつ id を持つ要素がある） | **続行（失敗にしない）** |

> `settled` は「遷移 / アニメーションが落ち着くのを待つ」安定化ヒントであり、正しさのアサーションではありません。空 / 崩壊したツリー（描画途中やシステムアラートで覆われた状態）は settled と見なしません。タイムアウトしても現在画面で先に進みます。

## 証跡ルールの発火

`capturePolicy` の各ルールがこのステップで発火するかを判定します（[evidence](evidence.md#a-capturepolicyルール方式)）。

- `_rule_fires`: `on.action`（+ 任意の `idMatches`）/ `on.event == screenChanged` / `on.result == error` のいずれかに一致するかを確認します。アクション名は DSL（ドメイン固有言語）名へ変換します（`long_press`→`longPress`、`assert_`→`assert`）。
- `_collect_captures`: インライン `step.capture` + 発火したルールの capture を集めて重複排除します。
- 瞬時種別（screenshot/elements）は sink の `capture()` で取得し、区間種別（video/deviceLog）は事前に `start_intervals()` で開始済みのものを停止して回収します。

`primary_id` は「ステップの主対象セレクタの `id`」です（tap なら tap 先、type なら `into`、swipe なら `on`）。`idMatches` トリガーはこの `id` に対して `fnmatch` します。

## 実行結果（データ構造）

```python
@dataclass
class StepOutcome:
    index: int
    action: str                  # "tap" / "wait" / ...
    ok: bool
    reason: str                  # 失敗理由
    duration_s: float            # 計時（actionLog 相当）
    assertion_results: list[AssertionResult]
    artifacts: list[Artifact]    # このステップで取れた証跡

@dataclass
class RunResult:
    scenario: str
    ok: bool
    steps: list[StepOutcome]
    expect_results: list[AssertionResult]  # 最終 expect の評価
    failure: str | None          # 例: "step 3 (tap): 一致なし: {...}"
```

`expect` は全ステップ成功後にのみ評価されます。`on_blocked` があれば expect も 1 回だけ再評価します。これらはそのまま `report.py` の `manifest.json` / JUnit / HTML になります（[reporting](reporting.md)）。

## runner（実行パイプライン）

実装: `bajutsu/runner.py`。orchestrator を実機と接続し、レポートまで連結します。

### `launch_driver`（アプリを起動して準備済みドライバを返す）

`preconditions` に従って `simctl` で環境を構築します:

```
erase（pre.erase なら shutdown → erase） → boot → terminate(bundle)（クリーンな起動状態に）
  → launch(bundle, [launchArgs, *locale_args(locale)], {**config.launchEnv, **pre.launchEnv})
  → openurl(deeplink)（あれば） → make_driver(actuator, udid)
  → _await_ready（query() が 2 要素以上返すまで最大 10s ポーリング）
```

> `_await_ready` は「アプリが UI を描画した（ルート要素より多い）」ことをポーリングで待ちます。`locale` は launch 時に **適用されます**（シナリオの `preconditions.locale` が config 既定を上書きし、`env.locale_args` で launch 引数として渡ります）。simctl の launch 手順は `make -C demos/features e2e` ＋ `e2e.yml` CI ワークフローで実機（iPhone 17 Pro）検証済みです。

### `device_factory` / `run_all` / `run_and_report`

- `device_factory(udid, backends, ...)`: actuator を選び、シナリオごとに `launch_driver` する factory を返します。
- `run_all(eff, scenarios, factory, ...)`: 各シナリオを **新しいドライバで** 実行します（クリーン分離）。
- `run_and_report(...)`: `run_all` の結果を `write_report(runs_dir/run_id, ...)` で書き出し、`(results, manifest_path)` を返します。

CLI の `run` はこの `run_and_report` を呼びます（[cli](cli.md#run)）。

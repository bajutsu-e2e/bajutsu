[English](../run-loop.md) · **日本語**

# 実行ループ（Orchestrator）と実行パイプライン

> Tier 2 の決定的ランナー。各ステップを **act → (wait) → verify** で回し、合否は機械アサーション
> のみで決める。AI は関与しない。最初の失敗で停止する。
>
> 実装: `bajutsu/orchestrator.py`（ループ本体）・`bajutsu/runner.py`（実機起動 + レポート連結）。

関連: [scenarios](scenarios.md) ・ [selectors](selectors.md) ・ [evidence](evidence.md) ・ [reporting](reporting.md)

---

## `run_scenario`（1 シナリオの実行）

```python
def run_scenario(driver, scenario, clock=None, sink=None, on_blocked=None) -> RunResult
```

- `driver`: `base.Driver`（実ドライバ or `FakeDriver`）。ループはこれにしか依存しない。
- `clock`: 時刻 / sleep の注入（テストで待機を決定化）。既定 `RealClock`（`time.monotonic` / `time.sleep`）。
- `sink`: 証跡の出力先（既定 `NullSink` = 何も書かない）（[evidence](evidence.md)）。
- `on_blocked`: ステップ失敗時に「ブロッカー（システムアラート等）を片付けたら True」を返すハンドラ。
  返ったら **そのステップを 1 回だけ再試行**する（[recording の alert guard](recording.md#システムアラートの自動対処)）。

### 1 ステップの流れ

各ステップ `i` について（`orchestrator.py` 内）:

1. `kind = _action_of(step)` — どのアクションか判定。
2. `step_id = step.name or f"step{i}"` — 証跡の出力単位。
3. （`capturePolicy` に `screenChanged` トリガーがあれば）操作前の `query()` を控える。
4. **区間証跡を開始**（`video` / `deviceLog` のうち、操作前から始める必要があるもの）。
   `_pre_intervals` が「ステップ自身から判定可能なトリガー」だけを拾う（`screenChanged`/`error` は遅すぎる）。
5. `_run_step_body` で **act**（or wait / assert）を実行 → `(ok, reason, assertion_results)`。
6. 失敗かつ `on_blocked` がブロッカーを片付けたら **1 回再試行**。
7. **区間証跡を停止**（ステップが落ち着いてから）。アーティファクトを記録。
8. **瞬時証跡**（`screenshot` / `elements`）を取得（`_collect_captures` の発火結果）。
9. `StepOutcome` を積む。失敗なら `failure` を設定して **break**。

### `_run_step_body`（act / wait / assert の分岐）

- `wait` → `_wait`（条件待機、下記）。
- `assert_` → `assertions.evaluate(driver.query(), ...)` を評価し AND。
- それ以外（tap/longPress/type/swipe/relaunch）→ `_do_action`。
- `SelectorError` / `NotImplementedError` を捕捉して `(False, 理由, [])` に変換（例外を上に投げない）。

### `_do_action`（操作の実体）

| アクション | 実体 |
|---|---|
| `tap` | `driver.tap(sel)` |
| `longPress` | `driver.long_press(sel, duration)` |
| `type` | `into` があれば先に `driver.tap(into)` → `driver.type_text(text)` |
| `swipe` | `{from,to}` ならそのまま `driver.swipe`。`{on,direction}` なら対象を `resolve_unique` → frame 中心から方向へ 100pt（`_SWIPE_DIST`） |
| `relaunch` | **`NotImplementedError`**（env 統合後の予定） |

## 待機（条件待機）

`_wait(driver, w, clock) -> (ok, reason)`。固定 sleep は無い。`query()` を `_POLL = 0.05s` 間隔で
ポーリングし、条件成立か `timeout` 到達まで回す。

| 形 | 成立条件 | タイムアウト時 |
|---|---|---|
| `for: <sel>` | 一致要素が現れる | **失敗** |
| `until: { gone: <sel> }` | 一致要素が消える | **失敗** |
| `until: screenChanged` | `query()` が初期値から変化 | **失敗** |
| `until: settled` | 画面が安定（連続 2 回 `query()` 不変、かつ id を持つ要素がある） | **続行（失敗にしない）** |

> `settled` は「遷移 / アニメーションが落ち着くのを待つ」安定化ヒントであり、正しさのアサーション
> ではない。空 / 崩壊したツリー（描画途中やシステムアラートで覆われた状態）は settled と見なさない。
> タイムアウトしても現在画面で先に進む。

## 証跡ルールの発火

`capturePolicy` の各ルールがこのステップで発火するかを判定する（[evidence](evidence.md#a-capturepolicyルール方式)）。

- `_rule_fires`: `on.action`（+ 任意の `idMatches`）/ `on.event == screenChanged` / `on.result == error`
  のいずれかに一致するか。アクション名は DSL 名へ写像（`long_press`→`longPress`、`assert_`→`assert`）。
- `_collect_captures`: インライン `step.capture` + 発火したルールの capture を集めて重複排除。
- 瞬時種別（screenshot/elements）は sink の `capture()` で取得、区間種別（video/deviceLog）は
  事前に `start_intervals()` で開始済みのものを停止して回収。

`primary_id` は「ステップの主対象セレクタの `id`」（tap なら tap 先、type なら `into`、swipe なら `on`）。
`idMatches` トリガーはこの `id` に対して `fnmatch` する。

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

`expect` は全ステップ成功後にのみ評価される。`on_blocked` があれば expect も 1 回だけ再評価する。
これらはそのまま `report.py` の `manifest.json` / JUnit / HTML になる（[reporting](reporting.md)）。

## runner（実行パイプライン）

実装: `bajutsu/runner.py`。orchestrator を実機と接続し、レポートまで連結する。

### `launch_driver`（アプリを起動して準備済みドライバを返す）

`preconditions` に従って `simctl` で環境を作る:

```
erase（pre.erase なら） → boot → terminate(bundle)（クリーンな起動状態に）
  → launch(bundle, launchArgs, {**config.launchEnv, **pre.launchEnv})
  → openurl(deeplink)（あれば） → make_driver(actuator, udid)
  → _await_ready（query() が 2 要素以上返すまで最大 10s ポーリング）
```

> `_await_ready` は「アプリが UI を描画した（ルート要素より多い）」ことをポーリングで待つ。
> ⚠️ `locale` と `setup` は `Effective` / `Preconditions` に値があっても **ここで適用していない**
> （未配線）。simctl の手順自体も best-effort で実機要確認。

### `device_factory` / `run_all` / `run_and_report`

- `device_factory(udid, backends, ...)`: actuator を選び、シナリオごとに `launch_driver` する
  factory を返す。
- `run_all(eff, scenarios, factory, ...)`: 各シナリオを **新しいドライバで** 実行（クリーン分離）。
- `run_and_report(...)`: `run_all` の結果を `write_report(runs_dir/run_id, ...)` で書き出し、
  `(results, manifest_path)` を返す。

CLI の `run` はこの `run_and_report` を呼ぶ（[cli](cli.md#run)）。

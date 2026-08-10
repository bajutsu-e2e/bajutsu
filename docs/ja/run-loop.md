[English](../run-loop.md) · **日本語**

# 実行ループ（Orchestrator）と実行パイプライン

> Tier 2 の決定的ランナーです。各ステップを **act → (wait) → verify** で処理し、合否は機械アサーション
> のみで決めます。AI は関与しません。最初の失敗で停止します。
>
> 実装: `bajutsu/orchestrator/`（ループ本体。package: `loop` / `waits` / `substitution` /
> `evidence_rules` / `actions`）、`bajutsu/runner/`（実機起動 + レポート連結。package: `pipeline` /
> `pool` / `launch`）。

関連: [scenarios](scenarios.md) · [selectors](selectors.md) · [evidence](evidence.md) · [reporting](reporting.md)

---

## `run_scenario`（1 シナリオの実行）

```python
def run_scenario(driver, scenario, clock=None, sink=None, alert_guard=None, ...) -> RunResult
```

- `driver`: `base.Driver`（実ドライバ or `FakeDriver`）。ループはこれにしか依存しません。
- `clock`: 時刻 / sleep の注入（テストで待機を決定化）。既定 `RealClock`（`time.monotonic` / `time.sleep`）。
- `sink`: 証跡の出力先（既定 `NullSink` は何も書かない）。詳細は [evidence](evidence.md)。
- `alert_guard`: ステップ失敗時に「ブロッカー（システムアラート等）を片付けたら、その片付けたイベントを返す」ハンドラです。イベントを返した場合、**そのステップを 1 回だけ再試行します**（[recording の alert guard](recording.md#システムアラートの自動対処)）。`wait` ステップ（`for`/`settled`/`screenChanged`）では同じハンドラが **wait の途中でも**待ち構えています（BE-0269）。すでにポーリング済みの画面のツリーが崩壊して見えた時点で発火します（デバウンスとクールダウンを挟み、1 回の wait につき最大 2 回まで）。末尾の再試行とは独立に、wait 自体のタイムアウトを待たず回復できます。

### 1 ステップの流れ

各ステップ `i` について（`orchestrator/loop.py` 内）:

1. `kind = _action_of(step)`：どのアクションか判定します。
2. `step_id = step.name or f"step{i}"`：証跡の出力単位です。
3. （`capturePolicy` に `screenChanged` トリガーがあれば）操作前の `query()` を控えます。
4. **区間証跡を開始**します（`video` / `deviceLog` のうち、操作前から始める必要があるもの）。`_pre_intervals` は「ステップ自身から判定可能なトリガー」だけを拾います（`screenChanged`/`error` は遅すぎるため対象外）。
5. `_run_step_body` で **act**（or wait / assert）を実行し、`(ok, reason, assertion_results)` を得ます。
6. 失敗かつ `alert_guard` がブロッカーを片付けた場合は **1 回再試行します**。
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
| `swipe` | `{from,to}` ならそのまま `driver.swipe`。`{on,direction}` なら対象を `resolve_unique` → frame 中心から方向へ画面に対する割合分（`_SWIPE_FRACTION`、既定 0.125。`amount` で上書き）。固定量ではなく割合にすることで、frame の単位が異なる backend 間（iOS はポイント、Android はピクセル）でもスクロール到達量が揃います |
| `relaunch` | runner が注入する relauncher でアプリを terminate + 再起動します（launch env/args 再適用＋上書き）。ready まで待ちます |

## 待機（条件待機）

`_wait(driver, w, clock) -> (ok, reason)`。固定 sleep はありません。`query()` を `_POLL = 0.05s` 間隔でポーリングし、条件成立か `timeout` 到達まで繰り返します。

| 形 | 成立条件 | タイムアウト時 |
|---|---|---|
| `for: <sel>` | 一致要素が現れる | **失敗** |
| `until: { gone: <sel> }` | 一致要素が消える | **失敗** |
| `until: screenChanged` | `query()` が初期値から変化 | **失敗** |
| `until: settled` | iOS でアプリが画面遷移イベントを報告している場合（BE-0310）：短い静止の窓のあいだ、それ以上の遷移が報告されない。それ以外：画面が安定（連続 2 回 `query()` 不変、かつ id を持つ要素がある） | **続行（失敗にしない）** |

> `settled` は「遷移 / アニメーションが落ち着くのを待つ」安定化ヒントであり、正しさのアサーションではありません。空 / 崩壊したツリー（描画途中やシステムアラートで覆われた状態）は、ツリー差分の経路では settled と見なしません。タイムアウトしても現在画面で先に進みます。
> 画面遷移シグナル（BE-0310）は「最後の遷移が終わり、新しい遷移が始まっていない」という肯定的な判定であり、読み取り専用でオプトインです（アプリが `BajutsuKit` の observer を組み込んでいる場合）。このシグナルを報告しないターゲットでは、ツリー差分の挙動をそのまま保ちます。

## 証跡ルールの発火

`capturePolicy` の各ルールがこのステップで発火するかを判定します（[evidence](evidence.md#a-capturepolicyルール方式)）。

- `_rule_fires`: `on.action`（+ 任意の `idMatches`）/ `on.event == screenChanged` / `on.result == error` のいずれかに一致するかを確認します。アクション名は DSL（ドメイン固有言語）名へ変換します（`long_press`→`longPress`、`assert_`→`assert`）。
- `_collect_captures`: インライン `step.capture`、発火したルールの capture、config の
  `defaults.capture`（他の2つと異なり常に適用される最低保証）を集めて重複排除します。
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
    duration_s: float            # 計時
    actuations: list[Actuation]  # ドライバが実際に行ったこと（送った座標・ジェスチャ）
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

`expect` は全ステップ成功後にのみ評価されます。`alert_guard` があれば expect も 1 回だけ再評価します。これらはそのまま `report/` の `manifest.json` / JUnit / HTML になります（[reporting](reporting.md)）。

## runner（実行パイプライン）

実装: `bajutsu/runner/`。orchestrator を実機と接続し、レポートまで連結します。

### `launch_driver`（アプリを起動して準備済みドライバを返す）

`preconditions` に従って `simctl` で環境を構築します:

```
erase（pre.erase なら shutdown → erase） → boot → terminate(bundle)（クリーンな起動状態に）
  → launch(bundle, [launchArgs, *locale_args(locale)], {**config.launchEnv, **pre.launchEnv})
  → openurl(deeplink)（あれば） → make_driver(actuator, udid)
  → _await_ready（query() が 2 要素以上返すまで最大 10s ポーリング）
```

> `_await_ready` は、利用できる中で最も強いレディネスシグナルを順に探してポーリングします。明示的な `readyWhen` セレクタ、次にアプリが報告する画面遷移イベント（[BE-0310](../../roadmaps/BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md)。`BajutsuKit` 経由のオプトイン）、次に宣言済みの `idNamespaces` に属する id を持つ要素、そしてどれも無ければ「アプリが UI を描画した（ルート要素より多い）」ことへとフォールバックし、最大 10s まで待ちます（各段の詳細は [configuration](configuration.md) を参照）。`locale` は launch 時に **適用されます**（シナリオの `preconditions.locale` が config 既定を上書きし、`env.locale_args` で launch 引数として渡ります）。simctl の launch 手順は `make -C demos/showcase run-swiftui` ＋ `ios-e2e.yml` CI ワークフローで実機（iPhone 17 Pro）検証済みです。

### `device_pool` / `run_all` / `run_and_report`

- `device_pool(udids, backends, ...)`: actuator を選び、`(lease, shutdown)` の組を返します。`lease(eff, scenario)` は空いているデバイスをリースし、シナリオごとに `launch_driver` します。
- `run_all(eff, scenarios, lease, ...)`: 各シナリオを **新しくリースした、新しいドライバで** 実行します（クリーン分離）。
- `run_and_report(...)`: `run_all` の結果を `write_report(runs_dir/run_id, ...)` で書き出し、`(results, manifest_path)` を返します。

CLI の `run` はこの `run_and_report` を呼びます（[cli](cli.md#run)）。

> **ウォームな XCUITest ランナー（BE-0291）。** 各シナリオは今も新しく起動したアプリと新しいドライバで実行します（クリーンな分離）。ただし XCUITest バックエンドの常駐 `xcodebuild` ランナーは、そのコールド起動が最大の固定コストなので、**デバイスごとにリースをまたいで常駐**させ、シナリオの切り替えではアプリだけを再起動します。これにより、スイートが払うコールド起動はシナリオごと 1 回ではなくデバイスごと 1 回で済みます。プールはウォームランナーを `(udid, actuator)` をキーに保持します。別の actuator に解決されるリース（BE-0240）や、デバイスを `erase` するシナリオはランナーをティアダウンして起動し直し、境界の定まった `/health` プローブに失敗したランナーはキャッシュミスとして扱います（コールド起動 1 回分の追加で済み、run を失いません）。idb など他のバックエンドはこの常駐プロセスを持たず、挙動は変わりません。
>
> 常駐ランナーは `app.launch()` を数回繰り返すとクラッシュします（XCTest セッションの制約。`docs/architecture.md` を参照）。そのためウォーム再利用には**上限**を設けています（BE-0287）。`BAJUTSU_XCUITEST_MAX_WARM_REUSES` 回（既定 3）再利用したら、次の起動でランナーがクラッシュする前に**先回りしてコールド再生成**します。クラッシュがシナリオの途中で起きて run を失うのを防ぐためです。上記の `/health` プローブは「すでにクラッシュしたランナー」を検知する事後的なものにすぎないので、この先回りの再生成こそが長いスイートをクラッシュから守ります。より早くクラッシュするデバイスでは、この値を `0` にするとウォーム再利用を完全に無効化できます（毎リースをコールドにします）。

> **バックエンドクラッシュからの復旧。** 上記の先回りの再生成はクラッシュの窓を狭めるだけで、完全には閉じません。バックエンドがそれでもシナリオの途中でクラッシュした場合、`_ScenarioRunner.run_one` はバックエンドに依存しない `base.BackendCrashError`（XCUITest に限らず、どのドライバも送出できます）を捕まえ、死んだリースを破棄し、新しいリースを取得し（プールは死んだウォームランナーを捨てるため、これはコールド再生成になります）、シナリオ全体を最初からやり直します。上限はリトライ回数（`crash_retries`、既定 1、つまり最初のクラッシュ後に 1 回だけ再試行。環境変数 `BAJUTSU_CRASH_RETRIES` で上書き可能）と、再生成に費やす合計時間の任意の wall-clock 上限（`crash_recovery_budget`、既定は無制限。環境変数 `BAJUTSU_CRASH_RECOVERY_BUDGET`（秒単位）で上書き可能）の 2 つです。この予算があるのは、回数の上限だけでは時間を制限できないためです。復旧しないランナーを相手にすると、`crash_retries` の試行のたびにコールド起動の上限いっぱいまで時間を費やし、はっきりした失敗ではなく黙ったジョブのハングになりかねません。試行のたびにクラッシュするシナリオはどちらかの予算を使い切り、黙って合格扱いにするのではなく、はっきりと失敗させることで、本当にクラッシュを誘発するシナリオが flaky の温床に紛れ込むのを防ぎます。2つ目の run 単位の予算（`run_crash_recovery_budget`、こちらも既定は無制限。環境変数 `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET` で上書き可能）は、この run 内のすべてのシナリオを通じて**累積した**復旧時間に上限を設けます。`crash_recovery_budget` だけではシナリオが変わるたびにリセットされてしまい、劣化し続けるデバイスはこの予算を何度も払い続け、最終的には run 自体がはっきりと失敗する代わりに、外部の CI タイムアウトが診断不能な形でジョブを打ち切ることになるためです。XCUITest バックエンドでは特に、コールド再生成それ自体の起動が、自動操作を受け付けなくなった Simulator の再起動または置き換えというデバイス修復の代金も払うことがあります。`CrashRecoveryBudget.on_crash` はクラッシュとクラッシュのあいだでしか参照されず、進行中の起動を短く切り上げることはできないため、どちらの予算もそのデバイス修復を止められません。修復自体の上限（`BAJUTSU_XCUITEST_RECOVERY_TIMEOUT`、および上限のない再準備）だけが、これを制限します。
>
> 再試行はまた、シナリオが `erase: true` を宣言したときにすでに得られるのと同じ `erase` の precondition を強制します。XCUITest では Simulator 自体の再起動、adb ではアプリレベルのクリーンな状態化であり、直前にクラッシュしたそのデバイスへのその場での respawn ではありません。ただし、シナリオが `reinstall: overwrite` を宣言している場合、またはその経路自体が `erase` の precondition をそもそも受け付けない場合（実機、`xcuitest.deviceType: device`、あるいは live WebDriver エンドポイント）は例外です。単なる `erase: false` はこの強制をスキップしません。CLI（`bajutsu/cli/commands/run.py` の `_filter_scenarios`）が、パイプラインに届く前にどのシナリオの `erase` も具体的な bool 値（多くの場合 `false`）へすでに解決してしまっているため、その値だけを見るガードでは、本項目がまさに対象としている実運用の経路そのもので強制再試行を無効にしてしまいます。シナリオのデータを本当に守るのは `reinstall: overwrite` だけです。`reinstall` の既定値（`clean`）は、`erase` の値にかかわらずデータを消去してしまうからです。再生成されたアプリのデータは既定で消去されるため、安全なのはクラッシュ地点まで冪等なシナリオに限られます。クラッシュ前にサーバー側書き込みのような永続的な副作用を伴うシナリオや、同じシナリオの前のステップがすでに用意した状態に依存するシナリオは、再実行時に失敗するか、誤った状態のまま合格することがあります。この判定ロジックは `bajutsu/runner/recovery.py` にあり、オンデバイスのドライバ適合性スイートと共有しています。これにより、Simulator のインフラ障害が起きても、無関係な PR で必須チェックを赤くすることなく同じように復旧します（BE-0334）。一方、明示的な `bajutsu run --no-erase` は尊重されます。CLI はこのフラグの解決前の値（`erase is not False`）を `force_erase_on_retry` として run に渡すため、シナリオ自身の `erase: false` では止められない強制再試行を、オペレーターの明示的な選択だけは止められます。強制 erase 用のリース自体がデバイスレベルの不具合（`simctl.DeviceError`/`adb.DeviceError`。`BackendCrashError` とは別系統の型であり、その派生ではありません）で失敗したときは、この不具合をこのループの外まで伝播させて run 全体を中断させるのではなく、同じその場での respawn に切り替えます。

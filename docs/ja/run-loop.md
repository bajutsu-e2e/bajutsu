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

### ステップループを挟む 2 つのライフサイクルフェーズ

シナリオの `before` / `after`（[scenarios](scenarios.md#before--afterセットアップとティアダウンのフェーズ)）は、ステップループの前後にもう 2 つのフェーズを足します。どちらも同じステップランナーが動かすので、フックのステップは他のステップとまったく同じことができ、run の `vars.*` バインディングを共有します。

1. **`before` が最初に走ります。** ここで失敗すると `failure = "before: …"` を立て、`steps` と `expect` をまったく実行しません。`before` はシナリオ内部のステップではなく、シナリオの前提条件だからです。
2. **`steps` と `expect` はそのまま走り**、判定はこれまでどおり計算されます。
3. **判定が出てから `after` が走ります。** 成功した経路も、失敗した経路も、キャンセルされた経路も含め、`steps` を抜けるすべての経路で到達します。`on` が `always` かその判定に一致するルールが、宣言順に走ります。ルール自身の失敗は、run が成功していたならその run の failure になり、そうでなければ既存の failure の後ろに追記されます。

キャンセルされた run では、キャンセル用のソースで `after` フェーズを縛れません。ソースはラッチされた `threading.Event.is_set` です。フェーズ内での最初の読み取りがふたたび `RunCancelled` を投げ、後片付けのステップが 1 つも走らなくなります。代わりにフェーズは専用の期限のもとで走ります。期限は `grace_seconds()` の 1 割で、これを過ぎると残りのルールを打ち切ります。後片付けに限られた実行機会を与えつつ、シャットダウンの後始末が `serve` の無条件 kill までの猶予時間を超えないようにするためです。

各フェーズはステップを 0 から数え直し、それぞれ専用の `RunResult` のリストに記録します。そのためレポートは、シナリオの連番のステップとは別のブロックとして Before と After を表示します。

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
- `_collect_captures`: 先頭に `elements` を置いたうえで、インライン `step.capture`、発火したルールの
  capture、config の `defaults.capture`（他の2つと異なり常に適用される最低保証）を集めて重複排除します。
  先頭の `elements` があるため、3つの取得元が何を要求したかによらず、どのステップにも動作後のツリーが
  残ります。`elements.json` のファイル名は 1 つなので、この取得は、ステップ前の baseline が書いた動作前の
  ツリーを置き換えます。
- 対になるもう一方の `after.png` は、この一覧にはありません。`_handle_action` が、ステップの動作の直後に
  自分で撮ります。ここでツリーを読みうる処理（`screenChanged` の比較、`for` wait のタイムアウト診断、
  `extract`）よりも先に撮るためです。そのうえで、上記の一覧から `screenshot.after` を取り除きます。
  どの取得元から来た `screenshot` 単体のトークンも先に `screenshot.after` へ正規化するのは、このためです。
  同じ 1 枚を二重に撮ることはありません。ただし、この撮影より前に読まれるツリーが 1 つあります。
  動作しないステップ（`assert`、`wait`）は、自身が評価に使ったツリーを読み直さずに再利用します
  （BE-0259）。この 2 種類では、`elements.json` は `after.png` の直後ではなく直前の時点のものになります。
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
    before_outcomes: list[StepOutcome]  # before フェーズ自身のステップ
    after_outcomes: list[StepOutcome]   # after フェーズ自身のステップ
    after_verdict: str           # "success" / "error" — どの after ルールが走ったか。なければ ""
```

`expect` は全ステップ成功後にのみ評価されます。`alert_guard` があれば expect も 1 回だけ再評価します。これらはそのまま `report/` の `manifest.json` / JUnit / HTML になります（[reporting](reporting.md)）。

## runner（実行パイプライン）

実装: `bajutsu/runner/`。orchestrator を実機と接続し、レポートまで連結します。

### `launch_driver`（アプリを起動して準備済みドライバを返す）

`preconditions` に従って `simctl` で環境を構築します:

```
erase（pre.erase なら shutdown → erase） → boot → bootstatus -b（起動の完了を待つ）
  → terminate(bundle)（クリーンな起動状態に）
  → launch(bundle, [launchArgs, *locale_args(locale)], {**config.launchEnv, **pre.launchEnv})
  → openurl(deeplink)（あれば） → make_driver(actuator, udid)
  → await_ready（query() が 2 要素以上返すまで最大 10s ポーリング）
```

> `await_ready` は、利用できる中で最も強いレディネスシグナルを順に探してポーリングします。明示的な `readyWhen` セレクタ、次にアプリが報告する画面遷移イベント（[BE-0310](../../roadmaps/BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md)。`BajutsuKit` 経由のオプトイン）、次に宣言済みの `idNamespaces` に属する id を持つ要素、そしてどれも無ければ「アプリが UI を描画した（ルート要素より多い）」ことへとフォールバックし、最大 10s まで待ちます（各段の詳細は [configuration](configuration.md) を参照）。どの段で決まった場合も、ゲートは続けて画面が**動かなくなる**のを待ちます。連続する2回のクエリが同じ要素の識別子と frame を返した時点で返ります。どの段も答えているのは「アプリの最初の内容が画面に出た」ことであり、遷移の途中の画面もこれを満たします。そして動いている画面に合成されたタッチは Simulator が取りこぼすことがあり、操作は配送済みとして報告される一方、失敗はずっと後の無関係なステップの `wait` タイムアウトとして現れます。この確認は通常1回のポーリングで済み、上限は3回です。上限に達した場合は、静止しない画面を待ち続けるのではなく ready を返し、最初の wait の診断に `settled: false` を記録します。ready なアプリをタイムアウトに変えることはありません。`locale` は launch 時に **適用されます**（シナリオの `preconditions.locale` が config 既定を上書きし、`env.locale_args` で launch 引数として渡ります）。`simctl boot` は起動を要求した時点で返るため、boot に続く手順はいずれも `bootstatus` で起動の完了を待ってから進みます。システムロケールの固定が行うもう1回の起動も同じです（[BE-0359](../../roadmaps/BE-0359-xcuitest-boot-completion-wait/BE-0359-xcuitest-boot-completion-wait-ja.md)）。simctl の launch 手順は `make -C demos/showcase run-swiftui` ＋ `ios-e2e.yml` CI ワークフローで実機（iPhone 17 Pro）検証済みです。

### `device_pool` / `run_all` / `run_and_report`

- `device_pool(udids, backends, ...)`: actuator を選び、`(lease, shutdown)` の組を返します。`lease(eff, scenario)` は空いているデバイスをリースし、シナリオごとに `launch_driver` します。
- `run_all(eff, scenarios, lease, ...)`: 各シナリオを **新しくリースした、新しいドライバで** 実行します（クリーン分離）。
- `run_and_report(...)`: `run_all` の結果を `write_report(runs_dir/run_id, ...)` で書き出し、`(results, manifest_path)` を返します。

CLI の `run` はこの `run_and_report` を呼びます（[cli](cli.md#run)）。

> **ウォームな XCUITest ランナー（BE-0291）。** 各シナリオは今も新しく起動したアプリと新しいドライバで実行します（クリーンな分離）。ただし XCUITest バックエンドの常駐 `xcodebuild` ランナーは、そのコールド起動が最大の固定コストなので、**デバイスごとにリースをまたいで常駐**させ、シナリオの切り替えではアプリだけを再起動します。これにより、スイートが払うコールド起動はシナリオごと 1 回ではなくデバイスごと 1 回で済みます。プールはウォームランナーを `(udid, actuator)` をキーに保持します。別の actuator に解決されるリース（BE-0240）や、デバイスを `erase` するシナリオはランナーをティアダウンして起動し直し、境界の定まった `/health` プローブに失敗したランナーはキャッシュミスとして扱います（コールド起動 1 回分の追加で済み、run を失いません）。adb と Playwright の各バックエンドはこの常駐プロセスを持たず、挙動は変わりません。
>
> 常駐ランナーは `app.launch()` を数回繰り返すとクラッシュします（XCTest セッションの制約。`docs/architecture.md` を参照）。そのためウォーム再利用には**上限**を設けています（BE-0287）。`BAJUTSU_XCUITEST_MAX_WARM_REUSES` 回（既定 3）再利用したら、次の起動でランナーがクラッシュする前に**先回りしてコールド再生成**します。クラッシュがシナリオの途中で起きて run を失うのを防ぐためです。上記の `/health` プローブは「すでにクラッシュしたランナー」を検知する事後的なものにすぎないので、この先回りの再生成こそが長いスイートをクラッシュから守ります。より早くクラッシュするデバイスでは、この値を `0` にするとウォーム再利用を完全に無効化できます（毎リースをコールドにします）。

> **バックエンドクラッシュからの復旧。** 上記の先回りの再生成はクラッシュの窓を狭めるだけで、完全には閉じません。バックエンドがそれでもシナリオの途中でクラッシュした場合、`_ScenarioRunner.run_one` はバックエンドに依存しない `base.BackendCrashError`（XCUITest に限らず、どのドライバも送出できます）を捕まえ、死んだリースを破棄し、新しいリースを取得し（プールは死んだウォームランナーを捨てるため、これはコールド再生成になります）、シナリオ全体を最初からやり直します。上限はリトライ回数（`crash_retries`、既定 1、つまり最初のクラッシュ後に 1 回だけ再試行。環境変数 `BAJUTSU_CRASH_RETRIES` で上書き可能）と、再生成に費やす合計時間の任意の wall-clock 上限（`crash_recovery_budget`、既定は無制限。環境変数 `BAJUTSU_CRASH_RECOVERY_BUDGET`（秒単位）で上書き可能）の 2 つです。この予算があるのは、回数の上限だけでは時間を制限できないためです。復旧しないランナーを相手にすると、`crash_retries` の試行のたびにコールド起動の上限いっぱいまで時間を費やし、はっきりした失敗ではなく黙ったジョブのハングになりかねません。試行のたびにクラッシュするシナリオはどちらかの予算を使い切り、黙って合格扱いにするのではなく、はっきりと失敗させることで、本当にクラッシュを誘発するシナリオが flaky の温床に紛れ込むのを防ぎます。2つ目の run 単位の予算（`run_crash_recovery_budget`、こちらも既定は無制限。環境変数 `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET` で上書き可能）は、この run 内のすべてのシナリオを通じて**累積した**復旧時間に上限を設けます。`crash_recovery_budget` だけではシナリオが変わるたびにリセットされてしまい、劣化し続けるデバイスはこの予算を何度も払い続け、最終的には run 自体がはっきりと失敗する代わりに、外部の CI タイムアウトが診断不能な形でジョブを打ち切ることになるためです。最終的に成功する復旧にこの run 単位の予算を使い切っただけでは、何もラッチされません。それはデバイスがまだ機能していることを示しているにすぎないからです。あるシナリオ自身のクラッシュ再試行ループが、この予算を主因として実際に失敗した後は、`run_one` はそれ以降のどのシナリオでも、その先頭で（クラッシュ再試行ループ自身の `except` 節の中だけでなく）この状態を確認します。そのシナリオ自身の最初のリースすらまだ試みていない時点でです。これにより、すでに復旧できないと分かっているデバイスに対して、残る全シナリオが同じ打ち切りに至るまでにコールド起動の試行を1回ずつ余計に払わずに済みます。XCUITest バックエンドでは特に、コールド再生成それ自体の起動が、自動操作を受け付けなくなった Simulator の再起動または置き換えというデバイス修復の代金も払うことがあります。`CrashRecoveryBudget.on_crash` はクラッシュとクラッシュのあいだでしか参照されず、進行中の起動を短く切り上げることはできないため、どちらの予算もそのデバイス修復を止められません。修復自体の上限（`BAJUTSU_XCUITEST_RECOVERY_TIMEOUT`、および上限のない再準備）だけが、これを制限します。
>
> 再試行はまた、シナリオが `erase: true` を宣言したときにすでに得られるのと同じ `erase` の precondition を強制します。XCUITest では Simulator 自体の再起動、adb ではアプリレベルのクリーンな状態化であり、直前にクラッシュしたそのデバイスへのその場での respawn ではありません。ただし、シナリオが `reinstall: overwrite` を宣言している場合、またはその経路自体が `erase` の precondition をそもそも受け付けない場合（実機、`xcuitest.deviceType: device`、あるいは live WebDriver エンドポイント）は例外です。単なる `erase: false` はこの強制をスキップしません。CLI（`bajutsu/cli/commands/run.py` の `_filter_scenarios`）が、パイプラインに届く前にどのシナリオの `erase` も具体的な bool 値（多くの場合 `false`）へすでに解決してしまっているため、その値だけを見るガードでは、本項目がまさに対象としている実運用の経路そのもので強制再試行を無効にしてしまいます。シナリオのデータを本当に守るのは `reinstall: overwrite` だけです。`reinstall` の既定値（`clean`）は、`erase` の値にかかわらずデータを消去してしまうからです。再生成されたアプリのデータは既定で消去されるため、安全なのはクラッシュ地点まで冪等なシナリオに限られます。クラッシュ前にサーバー側書き込みのような永続的な副作用を伴うシナリオや、同じシナリオの前のステップがすでに用意した状態に依存するシナリオは、再実行時に失敗するか、誤った状態のまま合格することがあります。この判定ロジックは `bajutsu/runner/recovery.py` にあり、オンデバイスのドライバ適合性スイートと共有しています。これにより、Simulator のインフラ障害が起きても、無関係な PR で必須チェックを赤くすることなく同じように復旧します（BE-0334）。一方、明示的な `bajutsu run --no-erase` は尊重されます。CLI はこのフラグの解決前の値（`erase is not False`）を `force_erase_on_retry` として run に渡すため、シナリオ自身の `erase: false` では止められない強制再試行を、オペレーターの明示的な選択だけは止められます。強制 erase 用のリース自体がデバイスレベルの不具合（`simctl.DeviceError`/`adb.DeviceError`。`BackendCrashError` とは別系統の型であり、その派生ではありません）で失敗したときは、この不具合をこのループの外まで伝播させて run 全体を中断させるのではなく、同じその場での respawn に切り替えます。
>
> 強制 erase のさらに上に、Simulator の XCUITest 経路にかぎって**デバイスの置き換え**という段があります（BE-0354）。erase が消すのはデバイスのデータであり、アプリのデータ破損はそれで復旧しますが、画面キャプチャの機構が固まった Simulator は復旧しません。CI では、erase したデバイスが固まったまま戻り、再試行が 1 回目の試行をそのまま再現しました。そこで、強制 erase 付きで走った再試行がまたクラッシュした場合、次のリースはそのリースの環境に対して、何も実行したことのないデバイスを要求します。作成の手順は、消えたデバイスを置き換えるときと同じものを使います。劣化したデバイスはシャットダウンし、以後プールに戻しません。試行が残した映像の証跡から、この段を直接選ぶこともできます。録画が書き込みを始めたことを確認できないまま終わった場合、それが機構の固まりの最初の兆候なので、**1 回目**のクラッシュで置き換えへ進み、この兆候がすでに否定した erase を飛ばします。置き換えの試行では強制 erase を外します。これから作成するデバイスには消すものがないからです。この段が働くのは、udid を固定しておらず `appPath` を持つ run にかぎります（`--udid` はオペレーターが名指ししたデバイスであり、置き換えれば run が黙ってそのデバイスから離れてしまいます。まっさらなデバイスには入れるアプリも要ります）。udid を固定しない run はデバイス 1 台をワーカー 1 つで扱うので、置き換えを要求した試行の次のリースは必ず同じデバイスを取り直します。また、置き換えが消すものは erase より多いので、erase の段が尊重する 2 つの離脱指定、`reinstall: overwrite` と `bajutsu run --no-erase` も同じように尊重します。ほかの経路は要求を無視し、それぞれが今持つもっとも強い再試行を保ちます。どちらの兆候も選ぶのは**段**だけで、判定には触れません。クラッシュし続けるシナリオは予算を使い切り、はっきりと失敗します。

> **協調的なキャンセル。** キャンセルされた run は、その場で死ぬのではなく自分の手順で終わり、失敗した run として run 履歴に残ります（BE-0370）。`bajutsu run` は `SIGTERM` を受けると、Python の既定動作（即座のプロセス終了）に任せる代わりにイベントを立てます。パイプラインはそのイベントを 3 つの安全な境界で読みます。`run_all` のディスパッチループでシナリオを始める直前、1 つのシナリオのステップとステップのあいだ、そして条件待ちを支える既存のポーリングループの内側です。キャンセル要求が届いたシナリオは `RunResult(ok=False, failure="cancelled")` になります。これはバックエンドのクラッシュや preflight の失敗がすでに作るのと同じ形なので、`run_all` は今までどおり宣言順にシナリオ 1 件あたり 1 件の結果を返し、`run_and_report` は `manifest.json`、`report.html`、JUnit XML を、ほかの失敗した run とまったく同じように書きます。長いスイートの早い段階でキャンセルしたオペレーターには、実行に至らなかったシナリオも失敗として数えられて見えます。キャンセルされた run を失敗として扱うこと自体から出てくる帰結として、これを受け入れます。イベントを読む場所は、この 3 つのほかにもう 1 つあります。バックエンドのクラッシュがコールド再生成を新たに始めてしまう地点（上記の再試行）です。その立ち上げは下記の猶予窓を越えることがあり、run は何も書かないまま終わらせられてしまいます。
>
> シャットダウンは、外側で待っている者がいるかどうかに関わらず有界です。1 回のドライバ呼び出しの内側で止まっているシナリオは、その呼び出しが返るまでキャンセル要求に気付きません。XCUITest の HTTP リクエスト、`adb` のサブプロセス、Playwright の呼び出しは、いずれもその長さだけシナリオを保持します。そのため協調的な経路には猶予窓を与えます。猶予窓は `BAJUTSU_CANCEL_GRACE` で決めます。既定は 90 秒で、一過性リトライに乗る読み取りが保持しうる 60 秒に、その後ろのシャットダウンの尻尾（残るシナリオを失敗させ、レポートを書き、合否の行を出力する分）30 秒を足した値です。呼び出しと同じ長さの窓では、この尻尾に何も残りません。その猶予窓よりさらに後ろに置いたハンドラ自身の期限を過ぎると、ハンドラは `SIGTERM` の既定動作を復元して再送出します。これにより、本当に固まったランナーは、ハンドラがなかった場合とまったく同じように死にます。ハンドラは `serve` Web UI の Cancel ボタンだけでなく、あらゆる送り手に応えます。`docker stop`、systemd のユニット停止、CI ジョブのキャンセルも同じハンドラに届きます。`serve` の側は、無条件の kill に上げる前に同じ猶予窓を自前のタイマーで待ち、その窓を起動する run にも渡します。run 側の内部期限は、独立に選んだ定数ではなく `serve` が実際に待っている窓に結び付きます。
>
> クロスブラウザの matrix run では、エンジンのパスとパスのあいだでもイベントを読みます。各パスはまず `device_pool` を丸ごと立ち上げます（環境の解決、デバイスカタログの読み取り、デバイスごとのコレクタの起動）。この確認がないと、最初のエンジンの途中でキャンセルしても、run が終わるまでに残りのエンジンすべての立ち上げと片付けを支払うことになります。走らなかったエンジンのシナリオは、レポートから落とすのではなくキャンセルとして失敗させます。そのため manifest の `matrix` ブロックは要求されたエンジンをすべて並べ、`cancelled` と表示されたセルが「その軸は要求されたが実行されなかった」ことをそのまま示します。落としてしまうと、`ok` は実際に走ったパスだけを集約することになり、最初のエンジンが全件合格した後に届いたキャンセルは、走り切っていない run を `PASS` として記録してしまいます。

[English](BE-0415-driver-call-trace-per-scenario.md) · **日本語**

# BE-0415 — Python とドライバ間の呼び出しを、シナリオ単位で恒久的にトレースする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0415](BE-0415-driver-call-trace-per-scenario-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0415") |
| 実装 PR | [#1966](https://github.com/bajutsu-e2e/bajutsu/pull/1966) |
| トピック | Driver & backend architecture |
| 関連 | [BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md)は、iOS シミュレータと Android エミュレータで `tap` ステップが遅い理由を調べました。この調査には、単独のラッパースクリプト [`trace_run.py`](../BE-0407-step-latency-driver-internal-tuning/misc/step-performance/trace_run.py) を使いました。このスクリプトは、`bajutsu run` を起動する前に、ドライバの各クラス、通信用の関数、`subprocess.run` へモンキーパッチを当てます。計測結果は、実行1回につき1つの JavaScript Object Notation（JSON）ファイルへまとめて書き出します。

このスクリプトで BE-0407 の調査自体は達成できました。しかし、次の2つの限界があります。

- `bajutsu run` の代わりに、誰かが思い出して実行しなければならないスクリプトのままです。
- 出力がシナリオ単位に分かれていません。複数シナリオを1回で実行すると、すべての呼び出しが1つの平坦なリストに混ざります。

この項目は、同じ種類の計測を `bajutsu run` の恒久的なオプション機能にします。`--trace-driver` を渡すと、実行のたびに各シナリオの `driver_trace.json` が、そのシナリオの他の証拠と並んで書き出されます。記録するのは、Python とドライバ間のすべての呼び出しです。各レコードには、次の内容を記録します。

- 呼び出されたドライバのメソッド名。
- そのメソッド1回がホストと端末の間で何往復したか。
- Android に限り、その往復のうちどれが subprocess 起動へフォールバックしたか。
- 各呼び出しの開始時刻と所要時間。
- 呼び出しが発生したステップ。

`trace_run.py` と異なり、この機能はモンキーパッチを一切使いません。合成するのは、orchestrator がすでに持っている依存性注入の差し込み口です。その大半は、テスト向けに追加されたか（BE-0143）、BE-0407 自身のドレイン追跡用ラッパーとして用意されていたものです。それに加えて、ステップとの対応づけのために、この項目自身が小さなスレッドローカルなトレース文脈を1つだけ持ち込みます。

### やらないこと

- **Web バックエンド（Playwright）は対象外とします。** この項目の動機が指す「シミュレータ・エミュレータ」には当たりません。ブラウザとの通信は、Playwright 自身の Python バインディング内部で完結しています。XCUITest や adb の往復と違い、bajutsu のドライバ側が起点になるものではないからです。
- **スクリーンショットや要素ツリーの証拠書き込み（`FileSink.capture`）の計測は対象外とします。** 証拠取得は、内部でドライバ呼び出し（`screenshot()`、`query()`）を行います。これは、すでに `driver` カテゴリで見えています。証拠書き込みが追加で持ち込むのは、ファイルの Input/Output（I/O）とマスク処理のオーバーヘッドです。このオーバーヘッドは、動機である「シミュレータ・エミュレータを操作する処理」の外側にあります。
- **シナリオの合否には一切影響しません。** トレースファイルは、現在の `--score` や `--zip` と同じく診断用の証拠です。プライム・ディレクティブ 1 は、あらゆる AI と診断機構を判定経路から締め出しています。この項目もそれに従います。
- **`transport` レコードの `response` フィールドは、小さな構造化された結果だけです。レスポンス本文まるごとは記録しません。** GET（読み取り）は `response` を持ちません。POST が持つのは、iOS の `_Reply.status` か、Android の `ActOutcome.acted`／`.published_mark` だけです。`/elements` や `/act` の応答が持ちうる要素ツリーは、ここに含めません。その内容は、ランの証拠（`elements.json`）としてすでに記録済みです。`driver_trace.json` にも複製すると、診断用の成果物自体がラン本体と同じか、それ以上の大きさになりかねません。

## 動機

BE-0407 は、ステップの遅延を大きく削減しました。ただし、その数値は iOS の `tap` ステップで1回あたり 0.3〜0.6 秒、Android で 0.6〜1.2 秒です。目標の 250〜500 ミリ秒には、まだ届いていません。残りを埋めるには、端末側のステップ実行機というより大きな変更が必要です。これは別項目（[BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md)〜[BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md)）として追跡しています。

この後続作業に着手する人は、BE-0407 の調査が使ったのと同じ、呼び出し単位の内訳を必要とします。実行時間が悪化した別のシナリオを調査する人も同じです。しかし現状では、その内訳を得るために、次の手間がかかります。

- ロードマップ項目の `misc/` ディレクトリの奥から、`trace_run.py` を見つけ出すこと。
- 起動方法が、`bajutsu` 自身の引数の前にラッパーを1つ挟んだものだと思い出すこと。
- 1つにまとまった JSON ファイルを、手作業でシナリオごとに分け直すこと。

そのどれもが、恒久的なコマンドラインインタフェース（CLI）フラグ1つで消える手間です。

これを単発のスクリプトのままにしておくコストは、想像上のものではありません。BE-0407 の「はじめに」自身が述べているとおり、ドライバ内部の遅延に関する調査は BE-0407 で終わっていません。CI で誰かが気づいた実行時間の悪化も、新しいバックエンドの最初の性能検証も、この機能なしでは毎回ゼロから始まります。

この機能は、一度だけ作っておきます。シナリオ単位に区切り、`run` がすでに書き出しているランディレクトリに統合する形です。そうすれば、次の調査は、問題を再現できるスイートに対して `bajutsu run --trace-driver` を実行するだけで済みます。別のスクリプトを用意する必要も、どの呼び出しがどのシナリオに属するかを手作業で対応づける必要もありません。

## 詳細設計

### 計測する3つのカテゴリ

1ステップの中でホストと端末の往復回数を実際に左右しているものに合わせて、カテゴリを3つに分けます。

- **`driver`**：`Driver` プロトコルの各メソッド呼び出し（`tap`、`query`、`screenshot`、`wait_for` など）1回につき、1レコードです。加えて、`trace_run.py` も計測していた `drain_interruptions` と `settled_query` の2つも対象とします。この2つは、対応する能力プロトコル（後述）を実装しているドライバにだけ存在します。orchestrator は、そのドライバへ直接呼び出します。
- **`transport`**：1回のドライバメソッド呼び出しの**内部**で起きるホストと端末の往復1回につき、1レコードです。`/zorder` も取得する `query()` 呼び出しは、1つの `driver` レコードの下に `transport` レコードを2つ持ちます。きれいに解決する `tap()` 呼び出しなら、1つだけです。
  - POST の往復（読み取りではなく操作）のレコードには、端末がもともと返している小さな構造化された結果も追加で持たせます。iOS なら `_Reply.status`（`bajutsu/common/drivers/xcuitest/_reply.py:22`、たとえば `"ok"`／`"stale"`）です。Android なら `ActOutcome.acted`／`.published_mark`（`bajutsu/common/drivers/adb/act_outcome.py:25-26`）です。
  - レスポンス本文まるごとは含めません。`_Reply.raw` や、`act()` の応答が `want_tree` 次第で持ちうる要素ツリーも含めません。トレースをランの証拠取得の複製にせず、呼び出し単位の小さなログのまま保つためです。
- **`subprocess`**：Android 限定です。常駐サーバー経由に乗らない [`adb_driver.py:457`](../../bajutsu/common/drivers/adb/adb_driver.py) の `uiautomator dump` フォールバックを指します。同じ経路を通る `id_u`／`getevent`／`wm size` の `adb shell` プローブ（`adb_driver.py:1148,1159,1190`）も指します。iOS の `simctl` と `xcodebuild` のプロセス起動は、デバイスのリース時に一度きり発生します。それはどのシナリオのステップも始まる前です。ステップ単位の手がかりを持たないため、計測対象にしません。

各レコードは `{category, step, name, started_at, elapsed_s, attempt}` を持ちます。

- `step` は、その呼び出しが発生したステップの番号と種別です。形式は `trace_run.py` と同じ `f"{i:02d}:{種別}"` です。
- `started_at` は、壁時計の Unix エポック秒です。`device.log` や、`manifest.json` 内のステップ自身の `started_at` と突き合わせられます。
- `elapsed_s` は、その呼び出し自体の所要時間です。
- `attempt` は、クラッシュリカバリの何回目の試行で呼び出されたかを示します。最初の試行なら1です。

1つの `driver_trace.json` は、シナリオの全試行にまたがります。そのため、ステップキーだけでは、クラッシュした試行の記録とリカバリした試行の記録を区別できません。`steps` の各エントリも、同じ理由で `attempt` を持ちます。

POST の往復を表す `transport` レコードだけは、もう1つ `response` フィールドを持ちます。これが前述の小さな構造化結果です。GET（読み取り）のレコードは、これを持ちません。

### モンキーパッチではなく合成で実現する

`trace_run.py` がモンキーパッチに頼っているのは、`bajutsu` 自身のソースを編集できない外部スクリプトだからです。この項目は、そのソースを編集できます。そして orchestrator は、同種の計測を恒久機能とするために必要な差し込み口を、すでに持っています。どれも新設するものではありません。テスト向け、あるいは BE-0407 自身が、すでに追加していたものです。

1. **`TracingDriver`（新規）**：`base.Driver` を満たす委譲プロキシです。ただし、実装するのは `__getattr__` **だけ**です。プロトコルのメソッドを自前で1つも宣言しません。
   - 属性を読むたびに、まず素の `getattr` で、包んでいるドライバが実際にその属性を持っているかを確認します。持たないときは、ラップしていないドライバと同じように例外が上がります。
   - 計測対象の一覧に載っている名前であれば、素の属性の代わりに、タイミング計測付きのラッパーを返します。それ以外は透過的に渡すだけです。
   - この設計は、`tap`／`query`／`screenshot` だけの問題ではありません。`drain_interruptions` と `settled_query` は、「ドライバ固有の余分なメソッド」ではありません。`@runtime_checkable` な能力プロトコルに属しています。プロトコルは `base.InterruptionPolicyTarget` と `base.SettledReadProvider` です（[`bajutsu/common/drivers/base/`](../../bajutsu/common/drivers/base/)）。orchestrator は、`hasattr` ではなく `isinstance` でこれを判定しています。判定箇所は、`gestures.py:212`、`waits/_functions.py:273`、`loop/_step_runner.py:166` です。
   - プロキシがこの2つを実メソッドとして宣言してしまうと、包んでいる相手が誰であろうと、`isinstance` が常に真を返します。`settled_query` を持たない `FakeDriver` や、どちらも持たない `PlaywrightDriver` を包んだ場合も同様です。能力があると誤判定された分岐へ進んだ先で、プロキシ自身の `__getattr__` が失敗します。
   - `TracingDriver` が何も自前で宣言しないからこそ、どの能力プロトコルに対する `isinstance` も、包んでいるドライバの本当の能力をそのまま反映します。
2. **注入するのはパラメータではなく共有トレース文脈**：ステップが実際に操作するドライバは、`--trace-driver` の値が見える場所（作業単位5）から何段も奥で構築されます。代替案は、`backends.make_driver` に引数を1つ足し、`launch_driver`・`RunEnvironment`・具象環境クラスそれぞれのコンストラクタへ順に通す方法です。しかしそれでは、診断機能のためだけに、各バックエンドのライフサイクルクラスすべてへ手を入れることになります。
   - 代わりに、`make_driver` と `AdbDriver` の subprocess 経路（作業単位4）は、同じスレッドローカルなトレース文脈を読みます。作業単位6が、ステップとの対応づけのためにもともと必要とするものです。
   - このスレッドローカルは、`--trace-driver` のとき `_ScenarioRunner.run_one` が一度だけセットします。
   - アンビエントな状態をスレッドローカルから読む方式は、`RunEnvironment` の各サブクラスへコンストラクタ引数を足すよりも、変更範囲が小さくて済みます。代わりに、差し込み口が明示的な引数ではなく暗黙のものになります（詳細は「検討した代替案」）。
3. **iOS の `transport`**：BE-0407 作業単位 6 のドレイン追跡を組み込むために、すでに `self._transport` を委譲クロージャ `_tracking_transport` で1段ラップしています。ラップしているのは、[`XcuitestDriver.__init__`](../../bajutsu/common/drivers/xcuitest/xcuitest_driver.py) です。
   この項目では、同じ場所にもう1段、計測用のラップを重ねるだけで済みます。
   - 追加するのは、新しい任意引数1つです。たとえば `on_transport_call: Callable[[str, float, float, str | None], None] | None` です。最後の引数は、POST なら `_Reply.status`、GET なら `None` です。
   - `backends.make_driver` の `xcuitest` 分岐が、作業単位2のトレース文脈がトレース有効を示しているときに、この引数を渡します。
   - `_raw_http_transport` にも他のモジュールにも、一切手を入れません。
4. **Android の `transport` と `subprocess` は、同じ差し込み口を通る**：対象は、[`backends.make_driver`](../../bajutsu/common/backends.py) の adb 分岐です。この分岐は、`fetch_hierarchy`、`fetch_clock`、`act` という呼び出し可能オブジェクトを、呼び出し元から受け取ります。
   それをそのまま `AdbDriver(...)` へ渡しているだけです。
   - トレースが有効なときは、`AdbDriver(...)` を構築する前に、`make_driver` の内部でこの3つを計測用にラップします（`transport`）。
   - `act` のラップは、戻り値の `ActOutcome`（`.acted`、`.published_mark`）もレコードへ読み込みます。Android 側で唯一 POST にあたる往復だからです。
   - 同じ分岐は、`subprocess` も同じ考え方で扱います。`AdbDriver.__init__` は、すでに `run: RunFn = adb.real_run`（[`adb_driver.py:125`](../../bajutsu/common/drivers/adb/adb_driver.py)）を受け取っています。`AdbDriver` が発行する adb 向けの subprocess 呼び出しは、すべて `self._run` を経由します。経由するのは、次の3つです。
     - `uiautomator dump` フォールバック（`:457`）。
     - `id_u`／`getevent`／`wm size` の各プローブ（`:1148,1159,1190`）。
     - `adb.Env(self.serial, run=self._run)`（`:1400,1453`）。
   - `make_driver` は、同じ条件のもとで、計測用にラップした `adb.real_run` を `run=` として渡します。したがって、どちらのカテゴリにも、どのモジュールの `subprocess` にもモンキーパッチは要りません。
   - この経路が唯一届かない呼び出しが、`AdbDriver._run_text`（`:1381`、`typeText` の経路）です。タイプした秘密情報が adb プロセスの引数列に載らないよう、あえて `self._run` の外に置かれています。もともとのコメントが「テストがパッチできるようクラスレベルの属性経由にしてある」と述べているとおり、すでに差し替え可能です。このモジュールも、同じ条件のもとで同じ属性を差し替えます。
5. **`driver` カテゴリのラップは、実際にシナリオのドライバを組み立てる場所で行う**：シナリオのドライバは、`device_pool` の注入可能な `make_driver` 引数からは来ません。引数の型は `Callable[..., base.Driver] = _make_driver` です。
   - この引数が読まれるのは、[`pool.py:387`](../../bajutsu/common/runner/pool.py) の1箇所だけです。自身のアクチュエータがドライバ経由でネットワークを観測しないランがフォールバックする、読み取り専用のネットワーク証拠プロバイダ（BE-0020）のためのものです。シナリオのステップが実際に操作するドライバを組み立てることは、一度もありません。
   - そのドライバは、[`pool.py:408`](../../bajutsu/common/runner/pool.py) の `launch_driver(...)` から来ます。`launch_driver` は `RunEnvironment.start(...)` を呼びます。各環境は、そこで自ら `backends.make_driver` を呼び出します（[`xcuitest_environment.py:772`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py)、[`android_environment.py:166`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py)）。
   - `device_pool` の `lease()` クロージャは、`launch_driver(...)` が返す値をその場で `TracingDriver` に包んでから、`Lease.driver` へ格納します。これは、`lease_env` のテアダウンが後で受け取るのと同じオブジェクトです。
   - このテアダウン呼び出し（`lease_env.end_lease`／`teardown`）は、一見すると選択を迫るように見えます。テアダウン側に渡す値だけ生のドライバのままにするか、`TracingDriver` の透過性がテアダウンにも及ぶと確認するかです。しかし実際には、その必要がありません。どの環境の `teardown` も、`driver` 引数をインタフェースの形を揃えるためだけに受け取り、中身を一切読んでいません。いずれも `# noqa: ARG002  # Environment shape` という注記付きです（[`environments/ios.py:88`](../../bajutsu/common/platform_lifecycle/environments/ios.py)、[`android_environment.py:363`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py)）。
   - テアダウンが読むのは、環境自身が持つ状態（`self._resident`、`self._serial`、ランナーのプロセスなど）だけです。したがって、`Lease.driver` は一律に `TracingDriver` でラップしてよく、テアダウン用とステップループ用とで値を使い分ける必要はありません。
6. **シナリオとステップの境界**：こちらは自分たちで直接編集するファイルなので、モンキーパッチではなく明示的な呼び出しを2箇所加えます。
   - [`pipeline.py`](../../bajutsu/common/runner/pipeline.py) の `_ScenarioRunner.run_one` は、`--trace-driver` が有効なときだけ、作業単位2で導入したスレッドローカルなトレース文脈を開きます。文脈の鍵は、そのシナリオの `sid` です。無効な実行では、一度もセットしません。そのため、`make_driver` や `AdbDriver` の `run=` 側が読む「トレースなし」の判定は、追加コストなしで終わります。
   - `run_one` は `finally` で、蓄積したレコードを `f"{sid}/driver_trace.json"` へ書き出します。書き出しは `self._artifacts()`（[`RunArtifactWriter.write_json`](../../bajutsu/common/evidence/sink.py)）を経由します。この経路は、ラン全体で唯一の書き込み境界（BE-0331）です。現状どのレコードも、シナリオが書いた文字列を含んでいません。それでも、秘密情報のマスク処理がこの経路に乗ることで、無料で効きます。
   - [`_step_runner.py`](../../bajutsu/common/orchestrator/loop/_step_runner.py) の `_StepRunner._run_one` は、現在のステップの鍵を、同じスレッドローカルな文脈へ出し入れします。
   - スレッドローカルで足りる理由は、`_ScenarioRunner.run_one` が、1ワーカースレッド上で1シナリオを最初から最後まで実行するからです。ワーカースレッドは `ThreadPoolExecutor`（`pipeline.py` の `run_all`）のものです。あるスレッドが同時に2つのシナリオの途中にいることはありません。「このスレッドで今アクティブなトレース文脈」は、常に「このシナリオの、今この瞬間」を指します。しかもこれは、そのシナリオのリースのために `launch_driver`／`make_driver` が動くのと同じスレッドです。ドライバ構築の最中に作業単位2がこの文脈を読んでも、正しいシナリオの文脈を読むことになります。
7. **CLI フラグ**：`bajutsu run --trace-driver`（`bajutsu/run/cli.py`）を追加します。このフラグは、`_RunPlan` を経由して `_ScenarioRunner` まで渡します。`_ScenarioRunner` のフィールドが、作業単位6の開始と書き出しを制御します。既存の `--score` や `--zip` と同じ「診断専用で、デフォルトは無効」というパターンを踏襲します。

### 出力形式

トレースは、`runs/<run_id>/<sid>/driver_trace.json` に書き出します。

```json
{
  "scenario": "<シナリオ名>",
  "steps": [{"step": "00:tap", "wall_s": 1.02, "attempt": 1}],
  "records": [
    {"category": "driver", "step": "00:tap", "name": "tap", "started_at": 1234567890.1, "elapsed_s": 0.69, "attempt": 1},
    {"category": "transport", "step": "00:tap", "name": "POST /tap", "started_at": 1234567890.1, "elapsed_s": 0.62, "response": {"status": "ok"}, "attempt": 1}
  ]
}
```

`--trace-driver` を渡さないシナリオ実行では、このファイルを書き出しません。フラグが無効な経路のコストは、フラグの判定自体を除けば発生しません。

## 検討した代替案

| 代替案 | 採らなかった理由 |
|---|---|
| `trace_run.py` 自身のモンキーパッチ方式を、そのまま `bajutsu` 内部の恒久モジュールへ昇格させる | `_raw_http_transport` や `adb_resident.ResidentServer.start` といった、非公開の内部実装に踏み込む方式です。今日これらは `trace_run.py` 以外の誰にも依存されていないので、自由に形を変えられます。これを恒久的な契約にすると、使い捨てのスクリプトが持っていた脆さが、保守し続けなければならない脆さに変わります。 |
| `XcuitestDriver` と `AdbDriver` の各メソッド本体に、直接タイミング計測コードを書き込む | 実際のアクチュエーション処理を持つ2つのファイルの、約20個のメソッドすべてに手を入れることになります。差分は、両バックエンドがすでに経由して構築している唯一の差し込み口（`make_driver`）で合成する場合よりも、はるかに大きく、リスクも高くなります。 |
| `backends.make_driver` に明示的な `trace` 引数を足し、`launch_driver`・`RunEnvironment`・各具象環境のコンストラクタへ順に通す（スレッドローカルの代わりに） | より一般的な依存性注入の形です。この項目の初期案は、`device_pool` 自身の `make_driver` 引数がすでにこの形を与えていると誤って前提にしていました（実際には与えていません。作業単位5を参照）。実際にこの形で配線すると、`XcuitestEnvironment`・`AndroidEnvironment`・`WebEnvironment`・`FakeEnvironment` という `RunEnvironment` の各サブクラスの公開コンストラクタすべてに、診断機能のためだけに手を入れることになります。作業単位2が使うスレッドローカルの方が、変更範囲が小さくて済みます。代わりに、差し込み口が明示的な引数ではなく暗黙のものになります。 |
| `trace_run.py` と同様に、証拠書き込み（`FileSink.capture`）の計測を4つ目のカテゴリとして加える | 証拠取得が内部で行うドライバ呼び出し（`screenshot`、`query`）自体は、すでに `driver` カテゴリで見えています。残るのはファイル I/O とマスク処理のオーバーヘッドです。これは、「シミュレータ・エミュレータを操作する処理」というこの項目の動機の外側にあります。証拠書き込み自体の遅延が疑われる事態になれば、そのとき別項目として追加します。 |
| アーキテクチャ上の対称性のために、Web（Playwright）バックエンドも対象に含める | この項目の動機は、シミュレータ・エミュレータの操作遅延に限定されています。Playwright のブラウザとの通信は、自身の Python バインディング内部で完結しています。bajutsu のドライバ側が起点となる往復ではありません。`TracingDriver` が包む `Driver` プロトコルの境界自体は、将来 `PlaywrightDriver` を対象に加えたくなっても、そのまま機能します。 |
| `BAJUTSU_LOG_LEVEL` や `BAJUTSU_STALL_DIAGNOSTICS` に倣い、環境変数（`BAJUTSU_DRIVER_TRACE=1`）で有効化する | 既存の2つの opt-in 診断機構との一貫性を考慮して検討しました。今回は代わりに CLI フラグを選びました。有効・無効の判断が、オペレータが把握しておくべき環境変数の中ではなく、実行そのもののコマンド行や CI の設定の中に見えるようにするためです。 |
| `transport` の POST レコードに、小さな構造化結果ではなくレスポンス本文まるごとを記録する | 1つのトレースファイルで、より多くの疑問に答えられます。たとえば、`/tap` がどのフォールドを畳み込んだかまで正確にわかります。採らなかった理由は、`/elements` や `/act` の応答が持ちうる本文が、ランの証拠（`elements.json`）としてすでに記録されているからです。`driver_trace.json` にも複製すると、診断用の成果物がラン本体より大きくなりかねません。この項目の動機（往復回数と所要時間）には、その内容までは要りません。 |

## 進捗

> 作業が進むごとに、この節を最新の状態に保ちます。チェックリストは「詳細設計」の
> MECE な作業分解を写したもの（作業単位1つにつき1つのチェックボックス）で、
> ログには変更内容と日時を古い順に記録し、関連する PR へリンクします。

- [x] 作業単位 1 — `base.Driver` を満たす委譲プロキシ `TracingDriver`。ラップ対象のドライバが
      実際に満たす能力プロトコルの全メンバーを、`typing.get_protocol_members` で導出します。
      それをインスタンスの実属性として設定します。`__getattr__` はそれ以外の呼び出しへの
      フォールバックとしてのみ残しています。本項目の当初の前提は、`__getattr__` のみに
      頼るプロキシでした。しかしこの方式では、`@runtime_checkable` プロトコルへの肯定的な
      `isinstance` 判定を絶対に通せません。`isinstance()` は `inspect.getattr_static` を
      介して解決され、`__getattr__` を一切呼ばないためです。そのため、能力プロトコル
      （`InterruptionPolicyTarget`、`SettledReadProvider` など）への `isinstance` の判定結果は、
      true であれ false であれ、包んでいるドライバの本当の能力をそのまま反映します。
- [x] 作業単位 2 — `XcuitestDriver` と `AdbDriver` 自身のラップが読む、共有のトレース文脈。
      作業単位6の `_ScenarioRunner.run_one` フックがセットします。`threading.local` ではなく
      `contextvars.ContextVar` とし、本リポジトリ既存のアンビエント状態
      （`analytics/ledger`、`serve/oplog`）の慣例に合わせ、`ThreadPoolExecutor` の
      ワーカー再利用の下でも正しく動作します。
- [x] 作業単位 3 — iOS側の `transport` カテゴリの計測。`XcuitestDriver.__init__` 自身の
      内部で（構築時に一度だけ、トレースがすでに開いているかを確認したうえで）行います。
      既存の `_tracking_transport` ラップと並べて `self._transport` をもう1段ラップし、
      POST なら `_Reply.status` を記録します。
- [x] 作業単位 4 — Android側の `transport` と `subprocess` カテゴリの計測。
      `AdbDriver.__init__` 自身の内部で（作業単位3と同じ構築時チェックで）行います。
      `fetch_hierarchy`、`fetch_clock`、`act` をラップし（`transport`。`act` のラップは
      `ActOutcome.acted`／`.published_mark` も記録します）、`run` コールバックもラップします
      （`subprocess`）。`_run_text`（単なる `@staticmethod` のため同じ方法ではラップできません）は、
      代わりにアンビエントなトレース文脈を直接読みます。どのモジュールにも `subprocess` への
      モンキーパッチは行いません。
- [x] 作業単位 5 — `driver` カテゴリのラップ。`_ScenarioRunner._run_one_impl` は、各リース
      呼び出しの成功直後にこれを行います。戻ってきた `Lease.driver` を、その場で
      `TracingDriver` に書き換えます。`device_pool` の `lease()` クロージャの内部ではありません。そのクロージャのウォーム
      ドライバキャッシュは生のドライバを別に保持しており、内部でラップすると再利用時に
      二重ラップになるためです。
- [x] 作業単位 6 — `_ScenarioRunner.run_one` と `_StepRunner._run_one` への
      シナリオ・ステップ境界フックの追加。トレース文脈（作業単位2）の開始・書き出しと、
      `RunArtifactWriter.write_json` 経由での `driver_trace.json` 書き出しを、`finally` の
      中で行います。これにより、シナリオが例外を送出しても部分的なトレースがディスクに残ります。
      その書き出しを囲む `except OSError` が、失敗を例外にせず警告として記録するので、書き出しの
      失敗も run を止めません（`--zip`／`--evidence-store` と同様の診断専用の扱いです）。
      `TraceContext` はクラッシュリカバリの再試行ごとにスタンプされる
      `attempt` 番号も持ち、リカバリしたシナリオのトレースが、クラッシュした試行の記録を
      成功した試行と同じステップキーへ混ぜてしまわないようにしています。
- [x] 作業単位 7 — `bajutsu run --trace-driver` という CLI フラグの追加と、
      `_RunPlan` から `_ScenarioRunner` への配線。
- [x] 作業単位 8 — テスト。`FakeDriver` に対する `TracingDriver` のユニットテスト（ラップしても
      能力プロトコルの否定側が保たれること、たとえば
      `isinstance(traced, base.SettledReadProvider)` が偽のままであることを含む）、スタブの
      `transport` を渡した `XcuitestDriver` に対して往復ごとに `transport` レコードが1件できることを
      確認するテスト、疑似の `run` を渡した `AdbDriver` に対して adb 呼び出しごとに `subprocess`
      レコードが1件できることを確認するテスト、`fake` バックエンドに対して `--trace-driver` を
      付けて実行し出力ファイルの形を検証するテスト、フラグなしの実行では `driver_trace.json` が
      書き出されないことを検証するテスト。`fake` バックエンドだけでは `transport` も
      `subprocess` もレコードが生まれないため、作業単位3〜4（iOSとAndroidのラップ）が高速な
      テストスイートで検証されないまま残ってしまいます。
- [x] 作業単位 9 — ドキュメント。`bajutsu run` のフラグ一覧に、両言語で
      `--trace-driver` を追記する。

ログ：

- [#1966](https://github.com/bajutsu-e2e/bajutsu/pull/1966) — 全9作業単位を完了し、本項目を完成させました。
  `TracingDriver`（作業単位1）は、ラップ対象のドライバが実際に満たす能力プロトコルの全メンバーを、
  `typing.get_protocol_members` で導出したうえで実インスタンス属性としてインストールします。
  本項目の本文が説明する、`__getattr__` のみに頼るプロキシ方式は採用していません。
  `@runtime_checkable` プロトコルへの `isinstance()` は `inspect.getattr_static` 経由で解決され、
  `__getattr__` を一切呼ばないためです。その方式では、どのプロトコルに対しても肯定的な
  `isinstance` 判定を通せませんでした。共有トレース文脈（作業単位2）は `threading.local` ではなく
  `contextvars.ContextVar` とし、本リポジトリ既存のアンビエント状態の慣例に合わせました。
  `driver` カテゴリのラップ（作業単位5）は、`pool.py` の `device_pool().lease()` クロージャの
  内部ではなく、`pipeline.py` の `_run_one_impl` で戻り値の `Lease.driver` を書き換える形にしました。
  そのクロージャのウォームドライバキャッシュは、生のドライバを別に保持しています。内部でラップすると、
  次回再利用時にラップ済みドライバが紛れ込み、二重ラップになるためです。作業単位3〜4の
  transport／subprocess 計測は、`backends.make_driver` の各分岐ではなく、`XcuitestDriver`／
  `AdbDriver` 自身のコンストラクタ内に置きました。

## 参考

- [BE-0407 — 証拠読み取りの重複排除とドライバ内部の調整による、ステップ実行の高速化（iOS と Android）](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md)
- [`trace_run.py`](../BE-0407-step-latency-driver-internal-tuning/misc/step-performance/trace_run.py) — この項目が恒久的なシナリオ単位の機能へ引き上げる、調査用スクリプト

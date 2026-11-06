[English](BE-0424-app-crash-diagnostics.md) · **日本語**

# BE-0424 — テスト対象アプリのクラッシュを検知し、スタックトレースをシナリオの証跡として収集する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0424](BE-0424-app-crash-diagnostics-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0424") |
| 実装 PR | [#2012](https://github.com/bajutsu-e2e/bajutsu/pull/2012) |
| トピック | Platform support |
| 関連 | [BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact-ja.md)、[BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)、[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md)、[BE-0066](../BE-0066-web-crawl/BE-0066-web-crawl-ja.md) |
<!-- /BE-METADATA -->

## はじめに

テスト対象アプリ自体がシナリオの途中で異常終了すると、`bajutsu run` は要素が見つからないときと同じ種類の失敗を報告します。報告の中身は `ElementNotFound`（またはそれに類するドライバの失敗）で、見つからなかったセレクタの名前だけを告げます。その失敗メッセージは、アプリが落ちたことを一言も述べません。

シナリオ自身の run ディレクトリ（`runs/<run_id>/<sid>/`）にも、OS 自身が書き出したレポートは届きません。そのレポートは、プラットフォームごとに次のものです。

- **iOS**：異常終了したプロセスに対して macOS の `ReportCrash` が書き出す `.ips` ファイルです。
- **Android**：未捕捉の例外に対して `logcat` が出力する Java のスタックトレースです。または、端末が `/data/tombstones` 配下に書き出すネイティブクラッシュのレポートです。

赤くなったシナリオを調べる開発者は、まず消去法でパターンに気づく必要があります。本来なら存在するはずのセレクタが存在しない、という形です。そのあとで初めて、`bajutsu` 自身が生成したものに頼らず、OS 自身のレポートを手で探しに行くことになります。

本項目は、この検知と収集の両方を追加します。あるステップでアプリが落ちたシナリオは、その事実を名指しする専用の失敗メッセージを得ます。その run ディレクトリは、他の証跡の隣に `app-crash/` サブディレクトリを新たに持ちます。このサブディレクトリが保持するのは、プラットフォーム自身のレポート、またはそのプラットフォームが提供するもっとも近い代替物です。

本項目はまず `bajutsu run` を、iOS（XCUITest）と Android（adb）の両バックエンドで対象にします。バックエンドごとに範囲が決まっています。

- **iOS**：Simulator に限ります。実機では、「iOS：`app.state`」で述べる証跡の欠落があるため、`app_crash_signal()` が `None` を返します。シグナルをまったく持たないバックエンドが返すのと同じ「確認できない」という答えです。
- **Android**：API 30 以上に限ります。裏付けのシグナルである `ApplicationExitInfo`（「Android：`logcat` のクラッシュ用バッファをまず読み……」が頼ります）は、それより前の API レベルには存在しません。そのため、確認できないシグナルをポーリングし続ける代わりに、そこでも `app_crash_signal()` が `None` を返します。

同じ収集ロジックを、`bajutsu crawl` がすでに持つ同種の検知にも拡張します。web（Playwright）バックエンドとその独自シグナルは、後続の項目に委ねます。理由は「検討した代替案」に記します。

## 動機

`bajutsu` はすでに、自分自身が引き起こしたのではない種類の失敗を区別しています。`base.BackendCrashError` は、バックエンド自身のドライバプロセスが落ちたことを名指します。対象は、常駐する XCUITest ランナーのホスト、adb の常駐サーバ、ブラウザプロセスです
（[`bajutsu/common/drivers/base/backend_crash_error.py`](../../bajutsu/common/drivers/base/backend_crash_error.py)）。

実行パイプラインは、この失敗から回復します。死んだリースを破棄し、新しいデバイス上でシナリオ全体をリトライします。
[BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact-ja.md)
（実装済み、PR [#1999](https://github.com/bajutsu-e2e/bajutsu/pull/1999)）は、落ちたランナー自身のログと、iOS では `.ips` レポートを、失敗したシナリオの証跡へすでにコピーしています。どちらも*バックエンド*側の不具合を名指しており、チームがテストしているアプリ側ではありません。

*テスト対象アプリ*に対しては、今日これに相当する仕組みが何もありません。AI 主導の探索経路である `bajutsu crawl` は、この事象をすでに検知しています。`is_app_alive`
（[`bajutsu/crawl/core/_functions.py:281`](../../bajutsu/crawl/core/_functions.py)）は、要素ツリーがアプリ自身の UI をまだ示しているかどうかを確認します。崩壊を見つけたクロールは `Crash`
（[`bajutsu/crawl/core/crash.py`](../../bajutsu/crawl/core/crash.py)）を記録します。`Crash` が持つのは崩壊に至った操作パスで、[`bajutsu/crawl/repro.py`](../../bajutsu/crawl/repro.py) がそれを決定的な再現シナリオへ変換します。

ただし、この検知は UI 側のヒューリスティックにすぎず、背後にプラットフォーム側の証跡を持ちません。`Crash` レコードが運ぶのは崩壊を生んだタップの列だけです。死んだプロセスについて OS 自身が書き出したレポートは運びません。あらゆる CI ゲートが依存する決定的な経路である `bajutsu run` には、これに相当する確認が一切ありません。シナリオ途中のアプリクラッシュは、次のアクションかクエリがたまたま送出する一般的な失敗としてしか現れません。

この隙間は、本物のアプリの不具合を前にした開発者の時間を実際に奪います。本項目が対象とするのは、まさにこのケースです。`BackendCrashError` 自身の回復は、ドライバや環境側の不具合をすでに扱っており、本項目はその経路に手を加えません。今日のレポートでは、アプリが落ちて失敗したシナリオは、他の2つの失敗と見分けがつきません。1つはセレクタの `id` がリネームされた失敗、もう1つは画面が読み込まれなかった失敗です。どれも同じ `ElementNotFound` で、同じ「スクリーンショットを見て推測する」という出発点になります。

本項目が生み出す観測可能な違いは次の点です。着地すれば、アプリがクラッシュしたシナリオは、その事象を名指しするメッセージで失敗します。`runs/<run_id>/<sid>/app-crash/` ディレクトリは、プラットフォーム自身の証跡を保持します。

- **iOS**：`ReportCrash` が書き出した `.ips` レポートを、その名前のまま保持します。
- **Android**：`logcat-crash.txt` を保持します。端末が許せば `tombstone.txt` も保持します。

開発者は、2つの誤った説明を消去法で潰してから探し当てるのではなく、最初から正しいファイルを開けるようになります。

## 詳細設計

### 新しい専用の失敗型 `AppCrashedError`

`bajutsu/common/drivers/base/app_crashed_error.py`（新規ファイル）に次を定義します。

```python
class AppCrashedError(RuntimeError):
    """The app under test itself terminated abnormally mid-scenario.

    Distinct from BackendCrashError: the backend's own driver process (the resident XCUITest
    runner, an adb resident server) is healthy and answering — only the app being tested is gone.
    Raised only where a driver has positively confirmed this event, never inferred from an ordinary
    ElementNotFound. Constructed and consumed entirely within the reactive check described below,
    for its message text alone: it never escapes to `pipeline.py`, so it carries none of
    `BackendCrashError`'s recovery semantics and needs no shared base class with it.
    """
```

本項目の初期の草案は、`AppCrashedError` を `run_scenario` の外へ送出していました。さらに `pipeline.py` に、`BackendCrashError` のクラッシュリトライ処理を真似た新しい `except` 節を加えていました。レビューでこの形は却下されています（「検討した代替案」を参照）。

バックエンドがクラッシュしたシナリオには、救える状態が何も残っていません。一方、*アプリ*がクラッシュしたシナリオには、ドライバ、バックエンドプロセス、動作中のビデオ録画がすべて残っています。落ちているのはアプリだけです。このようなシナリオを、`run_scenario` 自身の `RunResult` 組み立てを迂回する別経路で終わらせると、3つとも捨ててしまいます。アプリが落ちた理由とは無関係な事情で捨てることになります。

以下の設計は、代わりに分類を経路の内側にとどめます。`AppCrashedError` は、それを送出する唯一の呼び出し箇所から外へ出ません。その呼び出し箇所は、エラーをふつうの終端ステップ失敗に変えます。既存のパイプラインは、この形ならすでに正しく仕上げられます。同時に、`StepOutcome`
（[`bajutsu/common/orchestrator/types/step_outcome.py`](../../bajutsu/common/orchestrator/types/step_outcome.py)）に新しく加える `app_crashed: bool = False` フィールドを `True` にします。

`pipeline.py` は、このフィールドをあとから読み、アプリ自身の証跡を取り込むかどうかを判断します（「収集をつなぐ」を参照）。フィールドを見つけるのは、そこで説明する `before_outcomes`、`steps`、`after_outcomes` にわたる走査です。`RunResult.steps[-1]` から読むのではありません。

### 検知の方式：ステップがすでに失敗した時点で確認する1つのシグナル

毎ステップ、アプリがまだ動いているかを確認する方式は、グリーンな実行を含むあらゆるシナリオにクエリを1つ追加します。構造上まれな失敗モードを捉えるための代償としては重すぎます。そこで本項目は、狭い opt-in のケイパビリティプロトコルを定義します。一部のバックエンドだけが持つドライバの機能に対して、このリポジトリがすでに確立している形を踏襲します。先例は
[`InterruptionPolicyTarget`](../../bajutsu/common/drivers/base/interruption_policy_target.py) と
[`SettledReadProvider`](../../bajutsu/common/drivers/base/settled_read_provider.py) です。

```python
@runtime_checkable
class AppCrashSignal(Protocol):
    """A backend that can positively confirm the app under test has crashed.

    A narrow opt-in, like `InterruptionPolicyTarget`: a backend that does not implement it is simply
    never asked, and the run is otherwise unchanged. Only XCUITest and adb need it today.
    """

    def app_crash_signal(self) -> str | None:
        """A short description of the app's crash, if this driver can confirm one right now.

        Called only once a step's own action, wait, or assertion has already failed — never polled
        proactively. Answers `None` when the driver cannot tell "the app went down" from "the app is
        merely not showing what was expected".
        """
        ...
```

このシグナルは、巨大な `Driver` プロトコル
（[`bajutsu/common/drivers/base/driver.py`](../../bajutsu/common/drivers/base/driver.py)）の新しいメンバーではありません。`Driver` は `@runtime_checkable` なので、そのメンバーはすべて `isinstance(x, base.Driver)` が `True` を返すための必須条件です。このリポジトリには、`Driver` を完全に実装するものが今日すでに4つあります。加えて、テスト用の fake backend もあります。

- `XcuitestDriver`
- `AdbDriver`
- `PlaywrightDriver`
- [`XcuitestLiveDriver`](../../bajutsu/common/drivers/xcuitest_live/xcuitest_live_driver.py)：BE-0238 が追加した4つ目のバックエンドです。実機の iOS 端末を W3C WebDriver 経由で操作します。

さらに、`Driver` の一部だけを意図的に実装する、より狭いラッパーもあります。一例は
[`WebContextDriver`](../../bajutsu/common/drivers/webview/web_context_driver.py) で、`web` ブロックへ入るときに実行ループが差し替えるドライバです。

`Driver` へ必須メンバーを加えると、これらすべてがスタブを持つ必要に迫られます。`WebContextDriver` も例外ではありません。本項目は、`web` ブロックの中で確認するつもりがそもそもないのにです。`AppCrashSignal` は、この問題を回避します。実装するのは `XcuitestDriver` と `AdbDriver` だけで、後述の呼び出し箇所は `isinstance` で問い合わせます。BE-0406 がすでに `InterruptionPolicyTarget` に対して行っているのと同じ方式です。

`TracingDriver`
（[`bajutsu/common/drivers/tracing.py`](../../bajutsu/common/drivers/tracing.py)）は、`--trace-driver` が使うプロキシです。それぞれのプロトコルを、自身の `_PROTOCOLS` タプルの中で実インスタンス属性として設置します。設置は `isinstance(wrapped, protocol)` で判定しており、そのおかげでプロキシ自身に対する `isinstance` も正しく読めます。`AppCrashSignal` は、`InterruptionPolicyTarget` がすでにそうしているのと同じように、このタプルに加わります。

- `XcuitestDriver` や `AdbDriver` をラップすると、プロトコルが設置されます。
- それ以外をラップしても、設置されません。対象は `PlaywrightDriver`、fake backend、`XcuitestLiveDriver`、`WebContextDriver` です。
- プロキシが追跡する他のケイパビリティは、どちらの場合でも影響を受けません。

`bajutsu/common/orchestrator/loop/_step_runner.py` は、どのステップも種類ごとに4つのハンドラのどれかへ振り分けます。ハンドラは `_handle_if`、`_handle_for_each`、`_handle_web`、`_handle_action` です。最後の1つが `wait`、`assert`、`handleSystemAlert`、あらゆるアクションステップを引き受けます。4つはそれぞれ異なる形で自分の `outcome.ok` を確定させます。

`_handle_action` 自身の `outcome.ok, outcome.reason, outcome.assertion_results = ok, reason, results` という行は、tip の解除とアラートガードの再試行が終わったあとにあります。それでも、この行はそのステップの最後の言葉ではありません。宣言されていない割り込みや失敗した `extract` が、同じハンドラの内側で、そのあとも `outcome.ok` を `False` へ反転させ得ます。

本項目の初期の草案は、確認をこの1行だけにつなぎました。この形には2つの問題があります。`_handle_action` 自身の終端失敗を遅れて見ます。さらに、次の2つの失敗にはまったく届きません。どちらもこの行を通らないからです。

- `_handle_if` や `_handle_for_each` の条件クエリの失敗
- `_handle_web` の `within` セレクタの失敗

4つのハンドラが実際に共有しているのは `self.state.outcomes.append(outcome)` です。ただし、呼ぶ回数は1つずつではありません。`_handle_if`、`_handle_for_each`、`_handle_web` は、`outcome` に最後に行う処理として、返る直前にちょうど1回だけ呼びます。`_handle_action` は2回呼びます。1回目は自身の終端です。2回目は `UncoveredSystemAlertLocale` を受けた早期リターン（`_step_runner.py:457`）で、そこでは `outcome.ok` を `False` にしてから、終端よりずっと手前で返ります。

本項目の初期の草案は、ハンドラごとに1つの呼び出し箇所だけを見る設計でした。この設計は、`_handle_action` の2つ目の呼び出しを見逃します。それは、ファイル自身のコメントがすでに名指ししている出口です。他のあらゆる事後処理を同じ理由で飛ばしてしまう出口にあたります。

本項目は、共有のステップをもう1つ加えます。`self._finish_outcome(active_driver, outcome)` です。5つの呼び出し箇所すべてで、素の `append` の代わりにこれを呼びます。他の3つのハンドラは1回ずつ、`_handle_action` は2回です。append 自体もこの中で行います。そのため、あとから加わるステップの種類がここへの配線を必要としない点は変わりません。`_drain_step_interruptions` が、同じ4つのハンドラに対して割り込みの確認にすでに与えているのと同じ性質です。

あとから加わる6つ目の確定箇所が同じ抜け穴を静かに開け直さないよう、高速スイートに1つテストを加えます。このテストは、あらゆる種類の失敗するステップをループへ実際に通します。そして、確定するあらゆる outcome が `_finish_outcome` を通ったことを確認します。振る舞いベースの固定であり、`_step_runner.py` の中で `self.state.outcomes.append` という文字列そのものを検索するテストではありません。そのため、`insert`、`extend`、`+=` という綴りの確定箇所や、ローカルな別名経由の確定箇所も見逃しません。

`_finish_outcome` は、その append の直前で `isinstance(active_driver, base.AppCrashSignal)` と `outcome.ok is False` の両方を確認します。加えて、アプリ自身を意図的に終了させる、たった1つのアクションのために、シナリオスコープの除外があります。次の段落で説明します。

入れ子になったステップの失敗は、最初にそれを観測したハンドラだけでなく、構造上何度も `_finish_outcome` に届きます。`_run_if` と `_run_for_each` は、どちらも自身の本体を `self.exec_steps` で走らせます。割り込みの回復処理も再入する、同じループです。

たとえば、`if` の中の `forEach` の中のアクションという3段の入れ子でクラッシュが起きたとします。すると、アクション自身、`forEach`、`if` という3つの外側の outcome が順に確定します。それぞれが独立して `_finish_outcome` を呼びます。

`after` フェーズも、すでに落ちたアプリに対して走るあとかたづけステップごとに、もう1回ずつ呼び出しを加えます。直前の `relaunch` の失敗が*原因で*走るステップも含みます。そのステップは、`relaunch` 自身が終了させたばかりのアプリに対して走ります。この dispatch は、`_dispatch_after` 自身の `run_phase(steps, after_outcomes, "after", …)` 呼び出し
（[`_functions.py:824-828`](../../bajutsu/common/orchestrator/loop/_functions.py)）を通ります。本体ステップのループとは別の経路で、自前の新しい `StepLoopState` を組み立てます（`_functions.py:994`）。

`after` フェーズは、`_run_recovery`
（[`_step_runner.py:70`](../../bajutsu/common/orchestrator/loop/_step_runner.py)）を通りません。`_run_recovery` は、BE-0314 の*割り込み*の回復ステップのための再入経路です。同じ `exec_steps` を `self.state.running_recovery = True` で囲みます（`_interrupt_guard.py:70`）。

`_run_recovery` は、確認の回数を増やす3つ目の独立した要因であり、この設計は独立に勘定に入れる必要があります。すでに落ちたアプリに対して回復ステップが走る割り込みでは、回復ステップごとに1つの outcome が確定します。それぞれが、どちらのラッチも抑えない、ふつうの（`relaunch` でない）失敗です。次の段落がすでに織り込む、入れ子の深さと `after` ルールのコストと同じものが、3つ目の発生源からも生じます。

除外を `outcome.action` だけに結びつけると、まさにこのケースを見逃します。失敗した `relaunch` 自身の outcome は確認を飛ばします。ところが、それを包む `forEach` や `if` の outcome と、その後に発火する `after: on: error` のステップは、別の `outcome.action` を持ちます。そのため、いずれも新規に確認してしまい、`app.state` の正直な `notRunning` を、確定したばかりの新しいクラッシュと読み違えます。

したがって除外は、1つの outcome の性質ではなく、*シナリオ*の性質でなければなりません。除外は、可変オブジェクトに持たせます。`run_scenario` がこのオブジェクトを1回だけ作り、クロージャを通して `run_phase` の呼び出しのたびに渡します。`run_scenario` は `live_bindings` をすでに同じ方法でフェーズ間で共有しています（`_functions.py:690`、`:699`）。オブジェクトは `bindings` の隣、`StepLoopState` に置きます。`_LoopConfig` には置きません。

`_LoopConfig` は `@dataclass(frozen=True)` であり、「ステップループが読むだけで決して変更しない、run 不変の入力」と自ら文書化しています。`mailbox` と `progress` はループが呼び出すだけのコールバックで、ループがそこを通じて書き込む状態ではありません。そのため、ラッチの前例にはなりません。`StepLoopState` こそが前例です。`_run_steps` はフェーズごとに新しいインスタンスを組み立てますが、`run_scenario` が1回だけ作ったその同じ `bindings` オブジェクトを、どのインスタンスにも渡します。これがまさに、このラッチに必要なシナリオスコープの共有です。コストは、`bindings` の隣に引数を1つ加えるだけです。可変値を持たないと文書化されたクラスに、新しいフィールドを加える必要はありません。

`_finish_outcome` は、`outcome.action == "relaunch"` かつ `outcome.ok is False` を見た瞬間に、このラッチを立てます。`app_crash_signal()` を呼ぶより前に立てます。そして以後のあらゆる呼び出しで、まずラッチを確認します。一度立てば、同じシナリオの中でそれ以降のどの outcome も確認しません。それを包む outcome も `after` フェーズのステップも例外ではありません。どれも、確認自身が引き起こしたクラッシュと読み違えられることはありません。

同じオブジェクトは、*確定した*クラッシュも記憶します。`_finish_outcome` が `AppCrashedError` を送出して捕まえた最初の時点で、記憶します。同じ伝播の中であとから確定する outcome は、`app_crash_signal()` をもう一度呼びません。すでにわかっているシグナルを、自身の `outcome.reason` へ折り込むだけです。

`outcome.app_crashed = True` を立て、`app_crash_artifacts` を収集するのは、最初に確定した outcome だけです。あとから決着する `if` や `forEach` の外側の outcome は、ラッチがすでに立っているのを見ます。自身の `reason` へメッセージを折り込みはします。開発者が失敗を読むための情報だからです。しかし自身の `app_crashed` はデータクラスの既定値（`False`）のままにして、`capture_app_crash` もどこからも呼びません。

これにより、`pipeline.py` のあとの走査（「収集をつなぐ」）が曖昧になりません。1つのシナリオが運ぶ `app_crashed` の outcome は、構造上たかだか1つです。走査が複数の候補から選ぶ必要は、決して生じません。

確認できなかった答えでは、このラッチを立てません。この非対称は意図的です。あるステップの `None` は、*次の*ステップの失敗がクラッシュかどうかについて何も教えてくれません。そこでラッチを立てると、本物のクラッシュを見逃す危険を冒すことになります。

この理由から、ここで得られた抑制は「シナリオごとに1回」よりも狭いものです。上に挙げた relaunch と確定済みクラッシュのケースでは効きます。クラッシュを伴わないふつうの失敗には効きません。ふつうの失敗は、確定する outcome ごとに1回の確認を払い続けます。その回数は、入れ子の深さに、失敗する `after` ステップ1つにつき1回を足したものです。本項目の初期の草案は、このコストをまるごと避けられると主張していました。

このコストは、シナリオがどれだけ深く入れ子になり、失敗時にいくつの `after` ルールを発火させるかで抑えられます。どちらの数字も、実際には小さいことが多いです。しかも、確認が走るのはすでに失敗したステップに対してだけです。「検討した代替案」が却下する、事前のステップごとのポーリングとは違います。あちらは、グリーンな実行すべてにコストを足します。

「実際には小さい」という見積もりは、確認1回がラウンドトリップ1回で済む前提です。これは iOS では成り立ちますが、Android では成り立ちません。Android の確認できなかった確認は、1回の呼び出しで答えず、数秒の上限つきポーリングになります。後述の「Android：`logcat` のクラッシュバッファを先に……」が、このコストをシナリオごとに抑えます。

この反応的な形には、あとで見つかるより先に名指ししておくべき、構造上の死角が1つあります。シナリオ自身の*最後の*ステップが原因でクラッシュが起き、そのステップ自身の操作がそれでも `ok=True` を報告する場合です。例は2つあります。アプリが落ちる前にランナーが届けた `tap`。少し前に捉えたツリーを読んだ `assert`。このステップ自身の outcome は、確認をしません。

そのあと、シナリオが結局分類されるか、本項目が受け入れる隙間のままグリーンになるか。それは、そのあとに何が走るかで決まります。そのあとに走るものが、必ず失敗して `_finish_outcome` に届く保証はありません。

- `scenario.after` は「`steps` と `expect` から抜けるあらゆる経路で」到達します
  （[`_functions.py:821`](../../bajutsu/common/orchestrator/loop/_functions.py)）。取り消された経路も含みます。ただし、シナリオがそれを宣言しているときにだけ存在します。
- `scenario.expect` が走るのは、それを宣言しており、かつ `failure is None` のときだけです。

どちらも宣言しないシナリオは、本項目の意図どおりに振る舞います。クラッシュを起こしたステップのあとに何も走らないため、`app_crashed` outcome も `app-crash/` 証跡もないままグリーンです。このようなシナリオすべてについてこの隙間を閉じるには、シナリオ終了時の無条件の確認が要ります。それは、この反応的な設計が避けようとしている、グリーンな実行すべてへのコストです。そのため本項目は、確認のトリガーを広げず、この隙間を設計上の前提として受け入れます。

残る2つの形は、同じ「グリーンのまま」という結末をたどりません。それぞれ単独で名指しする価値があります。

- シナリオレベルの `expect` は、死んだアプリに対してであっても、最後のステップのあとに走ります。`_evaluate_expect` が生成するのは `StepOutcome` ではなく `AssertionResult` です。そのため、`_finish_outcome` にはそもそも届きません
  （[`_functions.py:741-752`](../../bajutsu/common/orchestrator/loop/_functions.py)）。シナリオは**赤くなりますが、未分類のまま**です。`app_crashed` outcome も `app-crash/` 証跡もない、ふつうの `expect:` 失敗として扱われ、グリーンにはなりません。これは1つ目の死角の変種ではなく、本項目が同じく設計上の前提として受け入れる、もう1つ別の死角です。失敗を読んだ開発者の目には、クラッシュではなくアサーションの不一致として映ります。
- トリガーが失敗ではない `after` ルール（`on: always` または `on: success`）は、それでも発火します。そして死んだアプリに対して操作を続けます。そのあとかたづけステップ自身の outcome は、`_dispatch_after` が呼ぶふつうの `run_phase` の経路を通って `_finish_outcome` に*届きます*。この形は結局分類され、`app_crashed` が立ち、`app-crash/` も書き込まれます。クラッシュは、トリガーとなったステップより1ステップ遅れて捉えられるだけで、見逃されるわけではありません。

この信号を*実際に検証する*シナリオは、クラッシュを引き起こすステップのあとに、もう1ステップを置いて終えます。`expect` や無関係な `after` ルールに頼って、クラッシュを表面化させることはしません。Unit 11 の showcase シナリオは、どちらもそうしています。クラッシュのトリガーをタップしたあと、死んだアプリに対してもう1ステップを進めます。失敗して確認されるのは、タップ自身ではなく、そのあとのステップです。クラッシュを起こしたステップが本当に最後のステップであり、`expect` も、あとに走る非失敗トリガーの `after` ルールも持たないシナリオだけが、この設計自身の構造によりグリーンのままです。

`_finish_outcome` が `active_driver.app_crash_signal()` を呼ぶのは、どちらのラッチも立っていないときだけです。`None` でない答えを得ると、その場で `base.AppCrashedError(signal)` を送出し、同じ式の中で捕まえます。これは、本項目が意図して残す唯一の `try` 内 `raise` です。`CLAUDE.md` のインラインコメント規則が求める、理由つきの `# noqa: TRY301` を持ちます。送出を組み替えて避けるのではなく、この形を選ぶのは、`AppCrashedError` の存在理由そのものが、この1組の送出と捕捉に名前を与えることだからです（上記のクラス自身のドキュメント文字列がそう述べています）。

捕捉では、メッセージを `outcome.reason` へ折り込みます。そして `outcome.app_crashed` と確定済みクラッシュのラッチの両方を `True` にします。同じ catch の中で、`self.cfg.capture_app_crash` が `None` でなければ、その呼び出し可能オブジェクトもその場で同期的に呼びます。結果は、新しい `outcome.app_crash_artifacts: tuple[tuple[str, bytes], ...]` フィールドに保存します。

シナリオが終わったあとではなく、確認のまさにこの瞬間に収集すると、あとに続くあとかたづけステップが、証跡を掃引の足元からすり替えるのを防げます（「iOS：`.ips` レポートの照合」と「収集をつなぐ」を参照）。例外自身は、この1点より先へ伝播しません。

`active_driver` は、そのステップを実際に操作したドライバです。`web` ブロックの内側のステップと、そのブロック自身を包む outcome とでは、答えが異なります。`_handle_web` は、ブロック用に組み立てた `WebContextDriver` の上で `step.web.steps` を走らせます（`self.exec_steps(step.web.steps, web_driver)`）。そのため、内側のステップ自身の `_finish_outcome` 呼び出し（`_handle_action` 経由）は `active_driver = web_driver` を見ます。ここでは `isinstance` が `False` を返し、確認は本物の no-op になります。これは、「はじめに」で述べた本項目の web バックエンドに対する範囲と一致します。

`_handle_web` 自身を包む outcome は、`_finish_outcome` への5つ目の別の呼び出しです。意図的に、ブロック自身の `active_driver` 引数を渡します
（[`_step_runner.py:287-290`](../../bajutsu/common/orchestrator/loop/_step_runner.py)、「内側の `web_driver` ではない」）。この引数は、ブロックがコンテキストを切り替える前に有効だった、*ネイティブの*ドライバです。

ネイティブホストのクラッシュが、包む `web` ステップ自身の失敗として表面化することがあります。`WebView` ブリッジの足元でネイティブホストが落ちたために、内側のステップが失敗する場合です。この場合は no-op ではありません。`_finish_outcome` は、他のあらゆる失敗ステップとまったく同じように、ネイティブのドライバを確認します。

### iOS：要素ツリーではなく `app.state` を使う

XCUITest は、対象アプリのプロセス状態を `XCUIApplication.state` としてすでに公開しています。この enum の `notRunning` というケースが、「本当に落ちているか」という問いに直接答えます。`crawl` の `is_app_alive` は、同じ事実を空の、または予期しない要素ツリーから推測しています。この推測には、誤検知の実際のリスクがあります。システムアラートがアプリの UI を覆っている場合や、シナリオが意図的に `background` ステップを実行した場合です。`app.state` は、どちらも回避します。バックグラウンドへ回っただけで生きているアプリは、`runningBackgroundSuspended` または `runningBackgroundActive` を返し、`notRunning` にはなりません。

`notRunning` という答えだけでは、あらゆるプラットフォームでクラッシュを証明できません。実機では、OS によるメモリ不足時の強制終了や、起動未完了も、同じ答えを返し得ます。どちらも Simulator には当てはまりません。ただし、XCUITest バックエンドは Simulator 専用ではありません。

`xcuitest.deviceType: device`
（[`xcuitest_config.py:20`](../../bajutsu/common/config/schema/xcuitest_config.py)）は、同じ `XcuitestDriver` と `XcuitestEnvironment` を通じて実機の iPhone を操作します。そこでは Simulator 自身の制約が成り立ちません。実機は、メモリ不足時にフォアグラウンドのアプリを実際に jetsam で終了させます。後述の `.ips` 掃引にも、読むものがそもそもありません。理由は2つです。

- 実機のクラッシュは、ホスト自身の `~/Library/Logs/DiagnosticReports` には決して届きません。
- 実機では `self._bundle_id` が `None` になります
  （[`xcuitest_environment.py:319`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py)）。ビルドは `_prepare_simulator` の経路を通さず、帯域外でインストールされるからです。本項目の `Info.plist` 読み取りは、まさにその経路に依存しています。

本項目は、web backend をすでに後続の項目へ切り出しているのと同じように、Simulator だけを対象にします。`XcuitestDriver.app_crash_signal()` は、実機に対してその場で `None` を返します。シグナルをまったく持たないバックエンドが返すのと同じ「確認できない」という答えです。実機自身の OS による強制終了や、`deviceType: device` ターゲット自身が抱える証跡の欠落を、確定したアプリクラッシュと読み違えないためです。

シナリオのステップのうち1つ、`relaunch` だけは、どちらのデバイス種別でもアプリを意図的に終了させます。その iOS 側の経路は、`device_relauncher` の `e.terminate(bundle_id)` に続けて `e.launch(...)` を呼ぶものです
（[`relaunchers.py:64`、`:78`](../../bajutsu/common/platform_lifecycle/relaunchers.py)）。`xcuitest_environment.py:855-856` の、見た目の似た組ではありません。あちらは `_resume_warm` で、BE-0291 の*リースをまたぐ*ウォーム再利用の経路です。

`XcuitestEnvironment.relauncher()` は `_DeviceEnvironment` の実装をオーバーライドしています。理由は無関係で、`app_launched_at` を再記録するためです（「iOS：`.ips` レポートの照合」を参照）。このオーバーライドが包むのは、`device_relauncher` がすでに返す `RelaunchFn` だけです。`_resume_warm` は一切呼びません。`_resume_warm` は、リースをまたぐ別経路のままで、この `relaunch` ステップは触れません。

ただし、`_resume_warm` は、この節自身の `app_launched_at` に関する懸念から除外されるわけではありません。`_resume_warm` それ自身が4つ目の起動箇所であり、再記録の手当ても別に必要です。後述します。

そこで `_finish_outcome` は、`relaunch` ステップ自身が失敗した時点で確認を飛ばします。このシナリオがそれ以降に行うはずだった確認も、すべて抑えます（「検知の方式」を参照）。ただし、この防御は見た目ほど効いていません。理由は4つあります。

1. `relaunch` のクロージャ自身が、`readiness.await_ready(...)`
   （[`relaunchers.py:79`](../../bajutsu/common/platform_lifecycle/relaunchers.py)）を副作用のためだけに呼び、返ってくる `ReadinessResult` を握りつぶします。
2. `await_ready` 自体は、例外を送出しません。タイムアウトした待機も、成功した待機が `True` を返すのと同じく、`ReadinessResult(False, "timeout", …)` を返すだけです
   （[`readiness.py:170`](../../bajutsu/common/platform_lifecycle/readiness.py)）。
3. そのため、新しい起動中に発生する `fatalError()` は、`relaunch` ステップをまったく失敗させません。これは Unit 11 の showcase 用の仕掛けが、まさに引き起こすケースです。`await_ready` は静かにタイムアウトし、クロージャはそのまま返り、ステップは `ok=True` を報告します。
4. 周辺のツール群も、これを代わりに失敗として扱いはしません。`e.terminate` は自身の `CalledProcessError` を丸ごと握りつぶします
   （[`env.py:176`](../../bajutsu/common/backend_cli/simctl/env.py)）。まれに `e.launch` 自身が `CalledProcessError` を送出するデバイス・ツール側の失敗が起きても、`_run_step_body` の例外捕捉はそれを名指ししていません。その例外は、そのステップの `outcome.ok` を `False` にする代わりに `run_scenario` 全体から抜け出します。`_finish_outcome` にすら届きません。

`relaunch` ステップ自身の結果が実際に `ok=False` になる経路は、今日ではアラートガード・待機中回復失敗の経路だけです（「検知の方式」の入れ子になった失敗についての段落を参照）。この経路はアプリの健全性とは本当に無関係で、まさにこの除外が存在する理由そのものです。

新しい起動中のクラッシュが失われるわけではなく、1ステップ遅れて帰属先が変わるだけです。クラッシュは、アプリに触れる次のステップで失敗として現れます。その結果は `relaunch` ではないふつうの結果なので、どちらのラッチも飛ばす理由がありません。`_finish_outcome` は、他の失敗と同じようにそれを確認します。

それ以外の確認は、ステップがすでに失敗した後にしか走りません。しかも、同じシナリオの手前のすべてのステップでアプリが動作していると確認できた*あと*にしか走りません。ただし、この条件だけでは、起動が完了しなかったケースを完全には除外できません。シナリオの最初に失敗するステップには、手前のステップが存在しません。そのステップは、`before` の最初のステップ、または `before` が宣言されていなければ `steps` の最初のステップです。アプリが動作していると確認する機会自体がありません。`env.start` からそのステップまでのあいだに、アプリが実際にフォアグラウンドへ到達したことを確認する手段もありません。

`launch_driver` は `await_ready` を呼び、その `ReadinessResult` を、例外を送出せずにそのまま持ち越します
（[`launch.py:99`](../../bajutsu/common/runner/launch.py)）。これは、上で見た `relaunch` 自身のクロージャと同じです。実際に `readiness.ready is False` に到達するのは、`readyWhen` や名前空間のシグナルが、すでに消えてしまったプロセスに対していつまでも解決しないターゲットだけです。

どちらも宣言しないターゲットは、`await_ready` のもっとも弱い最終手段へ落ち込みます。素朴な `len(elements) >= 2` という要素数だけの判定です
（[`readiness.py:151-152`](../../bajutsu/common/platform_lifecycle/readiness.py)）。この関数自身のドキュメント文字列が、すでにこの条件に触れています。コールドブートが遅いと、SpringBoard のアイコンだけでこの条件を満たしてしまい、アプリ自身がフォアグラウンドへ来る前に成立し得るのです。不適切な `launchEnv` でアプリが終了してしまうターゲットは、そのため `ready=True, signal="count"` に着地し得ます。ターゲットのアプリ自身が実際に描画されたという証拠は、ありません。

この隙間は、4つの手当てで塞ぎます。

**1. readiness の結果をリースに持たせる。** `Lease`
（[`bajutsu/common/runner/types.py:44`](../../bajutsu/common/runner/types.py)）に `readiness: ReadinessResult | None = None` フィールドを加えます。`Lease(...)` のコンストラクタ
（[`pool.py:563`](../../bajutsu/common/runner/pool.py)）で、`sink=sink` と並べて設定します。値は、同じクロージャの手前で `launch_driver` がすでに返している `readiness` です（`pool.py:408`）。今日この値が届くのは、`FileSink` の起動待ちタイムアウト診断だけです。足りないのは `Lease` 側の写しであり、値の最初の取得ではありません。

**2. 3つ目のラッチフィールドを仕込む。** `run_scenario` にも、同じ形の `readiness: ReadinessResult | None = None` 引数を加えます。`relaunch=lz.relaunch` がすでにそうしているのと同じように、`lz.readiness` から注入します
（[`pipeline.py:898`](../../bajutsu/common/runner/pipeline.py)）。`run_scenario` は、この値をシナリオスコープのラッチオブジェクト（下の Unit 7）に、3つ目のフィールドとして仕込みます。

仕込むのは、`run_scenario` がそのオブジェクトを作る瞬間に、一度だけです。フェーズごとに新しく組み立てられる `StepLoopState` の側では仕込みません。`StepLoopState` は、他の2つのラッチがすでにそうしているのと同じように、クロージャを通してそのオブジェクトを*共有する*だけです。そのため、あるフェーズでの成功は、次のフェーズへ持ち越されます。

`readiness is None or not readiness.ready or readiness.signal == "count"` のとき、起動は未確認と判定されます。素朴な要素数判定は、SpringBoard とアプリ自身を見分けられません。その判定による `ready` という答えも、アプリがフォアグラウンドへ来た証拠にはなりません。それを証拠として扱うと、本項目が取り除こうとしている誤診断そのものが、逆向きに起きるケースが生じます。

**3. 肯定的な観測があったときだけフラグを解除する。** `count` の判定は、まれなケースではなく*ふつう*の答えです。`launch_driver` は `id_namespaces` を `await_ready` へ渡さないため
（[`launch.py:99`](../../bajutsu/common/runner/launch.py)）、この経路では `namespace` が成立することが一度もありません。`readyWhen` を宣言しないターゲットはすべて、あらゆる起動で `count` に落ち着きます。このリポジトリ自身の `showcase-*-noax` ターゲットも、その一例です
（[`demos/showcase/showcase.config.yaml:90`](../../demos/showcase/showcase.config.yaml)、`idNamespaces: []` で `readyWhen` もなし。どちらもシナリオ一式を丸ごと持ちます）。

`relaunch` のフラグと同じように、無条件でシナリオの残りをずっと抑え続けると、本項目はそうしたターゲットのクラス全体に対して丸ごと無効化されます。最初のステップだけを守るはずが、効きすぎてしまいます。`_finish_outcome` は代わりに、その成功がアプリの応答を要求した、確定した outcome を見た時点で、このフラグを解除します。

`if` や `forEach` を包む outcome は、フラグを決して解除しません。その成功自体が何も証明しないためです。

- `_run_if` は、条件が一致しなければ空の `else` 分岐を取り、その場で `ok=True` を確定します
  （[`_functions.py:922-926`](../../bajutsu/common/orchestrator/loop/_functions.py)）。
- `_run_for_each` も、一致した要素がゼロのとき、同じように確定します。
- どちらも、問い合わせのその先のアプリが死んでいても、例外を送出しません。`driver.query()` は何かしら答え続けます（iOS なら SpringBoard 自身のツリーです）。
- そのため、一度もフォアグラウンドへ来ていないアプリを持ち、最初のステップがまさにそうした `if` や `forEach` であるターゲットは、アプリが死んでいる*からこそ*フラグを解除してしまいます。フラグが守ろうとしている、まさにそのターゲットのクラスで裏目に出ます。

アプリにまったく届かない種類のステップでも、同じことが起こります。対象は `http`、`generate`、`totp`、`email`、`push` です。そのため、解除は除外ではなく、肯定的に条件づけます。アプリの応答を要求する確定は、2種類だけです。1つは、`relaunch` を除く、本物のアクチュエーションのステップ。もう1つは、一致した `wait` や `assert` です。この2つが、シナリオの最初のステップに欠けていた、肯定的な観測にあたります。

`relaunch` を除外する理由は、この節がすでに述べたとおりです。そのクロージャは `await_ready` の結果を握りつぶし、`await_ready` 自体は例外を送出しません。そのため、確定した `relaunch` の `ok=True` は、アプリについて何も語りません。確定した `relaunch` は、このフラグを解除せず、*立て直します*。`relaunch` は、アプリを「確認できない」状態へちょうど戻したところだからです。確認できない起動も、同じ状態を残します。

これにより、最初に失敗するステップへの保護が保たれます。同時に、たまたま readiness の判定が弱いターゲットで、以降のあらゆる確認が黙らされることもありません。空振りの `if` や `forEach`、アプリにまったく届かないステップ、確認しようのない `relaunch` が、肯定的な観測になりすますこともありません。

**4. それまでは確認を飛ばす。** その最初の成功が来るまでのあいだ、`_finish_outcome` は起動未確認を、失敗した `relaunch` と同じ扱いにします。確認を飛ばします。本項目がまだ起動の完了を確認できていない場合、シナリオは「アプリの状態がまだわからない」という状況に置かれます。これは、失敗した `relaunch` がシナリオの残り全体に対して置く状況と同じです。

`ReadinessResult` は、自身のドキュメント文字列で「Pure diagnosis: it never enters a verdict (prime directive 1)」と述べています
（[`protocols/readiness_result.py:15-16`](../../bajutsu/common/platform_lifecycle/protocols/readiness_result.py)、`signal` については `:27` に同じ記述があります）。この記述は、本項目より前のすべての利用者について真でした。いずれも、起動待ちタイムアウトの診断表示に使うだけでした。本項目は、この値から振る舞いを決める最初の利用者です。メッセージの文言を決めるだけの利用ではありません。そのため、`Lease.readiness` を加える同じ変更で、このドキュメント文字列も更新し、新しい用途を書き加えます。不変条件が古びる前に行います。

それ以外の場面での `notRunning` という答えは、直前まで動いていたアプリが今は動いていないことを意味します。この Simulator という環境には、それ以外にそうなる経路がありません。

新しいルートを
[`BajutsuKit/Sources/BajutsuRunner/openapi.yaml`](../../BajutsuKit/Sources/BajutsuRunner/openapi.yaml)
へ加えます。BE-0316 の `/systemAlert/query` と同じ形で、リクエストボディを受け取り、状態を運ぶ JSON を返します。生成された `APIHandler`
（[`BajutsuKit/Sources/BajutsuRunner/APIHandler.swift`](../../BajutsuKit/Sources/BajutsuRunner/APIHandler.swift)）に、対応するメソッドが加わります。そのプロバイダ実装が、ランナー自身の `XCUIApplication` に `.state` を問い合わせます。

今日、実際にリクエストを処理しているのは `Router.swift` ではなく `RunnerServer.swift` です。`APIHandler` を組み立て、その生成済みルートを登録しているのは後者です。`Router.swift` はパリティテストのためだけに残っており、実際の実行では何も答えません。本項目の初期の草案は、ルートを `Router.swift` にだけ加える指定でした。その場合、`XcuitestDriver` のリクエストは実サーバに対して404になります。

`XcuitestDriver`
（[`bajutsu/common/drivers/xcuitest/xcuitest_driver.py`](../../bajutsu/common/drivers/xcuitest/xcuitest_driver.py)）に、`is_real_device: bool = False` というコンストラクタ引数を加えます。`device_os`（BE-0358）をすでに通しているのと同じ方法で、`make_driver` から通します。`app_crash_signal()` はまずこれを確認します。`True` であれば、ルートを呼ぶ前にその場で `None` を返します。上で決めた、Simulator だけを対象にする範囲です。

それ以外では、`app_crash_signal()` はルートを1回呼びます。`notRunning` という答えが、そのままシグナルの文字列になります。それ以外の状態はすべて `None` を返します。

この呼び出しに届いたチャンネルエラーは、`None` へ握りつぶしません。それは既存の `XcuitestRunnerCrashError` で、`BackendCrashError` の一種です。本項目は、これをすでに受け持つ回復経路へ、そのまま伝播させます。ステップ自身のセレクタ失敗の直後にルートが失敗するのは、ふつうの競合です。チャンネルがこのステップと無関係だという根拠にはなりません。

### iOS：`.ips` レポートの照合、BE-0421 が着地させた掃引を再利用する

[BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact-ja.md)
は**実装済み**です（PR [#1999](https://github.com/bajutsu-e2e/bajutsu/pull/1999)）。ランナー自身の `xcodebuild` プロセスが異常終了したとき、macOS が `~/Library/Logs/DiagnosticReports` へ書き出したものの中から正しい `.ips` ファイルを見つける処理は、すでに着地しています。担うのは、
[`bajutsu/common/platform_lifecycle/environments/xcuitest/_functions.py`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/_functions.py)
の2つの関数です。

- `_diagnostic_reports_dir()`
- `_reports_since(reports_dir, pattern, since)`：名前と時刻だけによる汎用的な照合で、シグネチャ自体に `xcodebuild` を持ち込みません。

レポートのヘッダが解析できるときは、3つ目の関数 `_reported_pid()` がさらに絞り込みます。

本項目は、同じディレクトリに対して2つ目の掃引を書く代わりに、`_reports_since` を、テスト対象アプリ自身のバイナリに対してそのまま再利用します。掃引が2つあると、照合ルールへの修正が入ったとき、もう一方は古い実装のまま気づかれずに残ります。例は、新しい `.ips` ヘッダ形式や、`DiagnosticReports` の移動です。

プロセス ID（PID）による絞り込みは、BE-0421 自身のレポートに対してと同じようには使えません。XCTest が公開する `XCUIApplication` の表面には PID を読む手段がなく、`BajutsuKit/` の中にも、今日それを読む箇所はありません。本項目は代わりに、Simulator の一意なデバイス識別子（UDID）で絞り込みます。

Simulator 上のアプリの `.ips` レポートは、実行ファイルのフルパスを*ペイロード*に持ちます。ペイロードは、このファイルが持つ2つの JSON ドキュメントのうち2つ目です。1行だけのヘッダには、フルパスがありません。
[`_reported_pid`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/_functions.py)
自身のドキュメント文字列が、この分担を説明しています。

- ヘッダが運ぶのは `app_name`、`bundleID`、`timestamp`、`os_version`、`incident_id` です。安価で安定していますが、インストールパスは持ちません。
- PID も、本項目の udid も、ペイロードにあります。`_reported_pid` は、まさにその理由でペイロードを解析しています。

インストールパスの形は `.../CoreSimulator/Devices/<udid>/data/Containers/Bundle/Application/…` で、クラッシュしたプロセスが動いていた Simulator 自身を名指します。

`Lease`
（[`bajutsu/common/runner/types.py`](../../bajutsu/common/runner/types.py)）は、リースしたデバイス自身の `udid` をすでに記録しています。そこで、新しい `_app_crash_reports(pattern, launched_at, udid)` が絞り込みを担います。これは、BE-0421 自身の `XcuitestEnvironment._crash_reports(spawned_at, pid)` の姉妹にあたります。後者が `pattern` 引数を必要としないのは、自身の既知のプロセス向けに `"xcodebuild-*.ips"` を決め打ちしているためにすぎません。

`_app_crash_reports` は、`_reports_since` が返す候補を1件ずつ読みます。この関数は `list[Path]` を返し、レポート自身のファイル名に udid は現れません。実行ファイルのパスがペイロードの中で同じ `udid` を名指しているものだけを、受け入れます。`_reported_pid` による確認の代わりです。

ヘッダ自身が持つ `bundleID` フィールドは、より安価で安定した絞り込みです。ペイロードの確認を置き換えるのではなく、併用する価値があります。`XcuitestEnvironment` は、この値をすでに `self._bundle_id` として保持しています。

この絞り込みにより、同一の対象バイナリを動かす2台の Simulator が、同じ時間帯に存在する場合でも区別できます。並列実行の CI ホスト（`--workers 2`）で、このケースが生じ得ます。PID は一切要りません。

`_reports_since(reports_dir, pattern, since)` は、それでもファイル名で照合します。`.ips` レポートのファイル名が名指すのはクラッシュしたプロセスであり（`Showcase-2026-…ips`）、バンドル ID ではありません。BE-0421 は、自分自身の既知のプロセスに対して、リテラルの `"xcodebuild-*.ips"` を渡しています。

`XcuitestEnvironment` は `ios.bundle_id`（`self._bundle_id`、`com.example.Showcase`）を保持しています。この値はプロセス名ではなく、レポートのファイル名と一致することはありません。本項目は代わりに、インストール済みアプリ自身の `Info.plist`（`Path(self._app_path) / "Info.plist"`）から `CFBundleExecutable` を読み取ります。読み取りは `app_crash_artifacts()` 自身の中で行います。あらゆる iOS バンドルが宣言を義務づけられている、このキーから、掃引のパターンを組み立てます。

`app_crash_artifacts()` は、`RunEnvironment` プロトコルの形どおり、引数を取らないメソッドです（「iOS：`.ips` レポートの照合」の冒頭を参照）。そのため `eff` がスコープになく、`ios.app_path` を生きたまま読むことはできません。`self._app_path` は新しいフィールドで、`start()` で保存します。`self._bundle_id`（`self._bundle_id = ios.bundle_id if device_type != "device" else None`、`xcuitest_environment.py:319`）のすぐ隣に、同じ `ios` から、同じ方法で保存します。

`ios.app_path` 自体は任意です
（`str | None`、
[`target_config.py:107`](../../bajutsu/common/config/schema/target_config.py)）。`bundle_id` だけを名指す `deviceType: simulator` ターゲットで、すでにアプリがインストール済みの Simulator に対しては、これを設定しません。`_prepare_simulator` 自身の install も、同じケースでゲートしています（`if ios.app_path:`、`xcuitest_environment.py:902`）。置き換えデバイスの経路は、設定済みだと決めつけず、自前の専用エラーを送出します（`xcuitest_environment.py:627-632`）。

実行ファイル名を代わりに導く PID アクセサがないため、`Info.plist` を読んでいます。したがって、組み立てる代替パターンはありません。この収集は、`appPath` を設定したターゲットに限った範囲です。その範囲は、読み取りより*前に*明示的に確認します。`self._app_path is None` は、その場で `[]` へ解決します。非 macOS ホストと同じ、名前のついた事前確認です。`Path(None)` が数段落あとの広い `except Exception` の中で送出され、握りつぶされて、説明のつかない見落としとして読めてしまう、という流れにはしません。

`XcuitestEnvironment`
（[`bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py)）は、自身の `udid` をすでに知っています。ここに `app_launched_at` というタイムスタンプを加えます。

コールド起動そのものには、目印を隣に記録できる Python 側の `e.launch(...)` 呼び出しがありません。`_spawn_cold` は、アプリをインストールして `xcodebuild` を起動するだけです。アプリ自身の起動は、ランナー自身の `XCUIApplication.launch()` によって、ランナーの*内側*で行われます。このコードベース自身のコメントがそう明言しています（`xcuitest_environment.py:322-324`）。その周りの Python 側の呼び出しは、`_prepare_simulator`、`_launch_params`、`_spawn_cold_with_retry` だけです。

目印を `_spawn_cold_with_retry` が返ったあとに記録すると、目印が、それが指す起動よりも遅れてしまいます。コールド起動の最中にクラッシュしたアプリを考えます。ランナー自身の `/health` はまだ答えるため、spawn 自体は成功します。`.ips` の mtime は、spawn 呼び出しが返ってから初めて記録される目印より前になります。`_reports_since` の `mtime >= since` は、そのレポートを拒みます。まさに本項目が取り除こうとしている、「レポートは存在するのに `bajutsu` が見つけられない」という結果です。

そこで `app_launched_at` は、`_spawn_cold` の中、`_spawn_cold_with_retry` の呼び出しの*直前*に記録します。これが、2つの選択肢のうち安全な方です。早めに記録した目印は、過剰に収集するだけで済みます。`udid` と実行ファイル名の確認が、すでに窓を絞り込んでいるからです。遅く記録した目印と違って、捕まえるべきクラッシュを取り逃すことはありません。

`relaunch` ステップ自身の起動は、この節がたった今 `_resume_warm` と区別したものです。`device_relauncher` のクロージャを通じて走り、`(udid, run, extra_env)` を閉じ込めるだけです。目印を更新すべき `XcuitestEnvironment` が、その場にありません。放っておくと、`app_launched_at` は、リースの最初のコールド起動のまま凍りつきます。シナリオ途中の `relaunch` に続くクラッシュは、その `relaunch` 以前にまで遡る `since` で `DiagnosticReports` を掃引することになります。その窓は、`relaunch` 自身がすでに置き換えたクラッシュの `.ips` を、添付しかねないほど広いものです。

そこで `XcuitestEnvironment` は `relauncher()` をオーバーライドします。`_DeviceEnvironment` 自身の実装は、それ以外そのままです。`device_relauncher` が返す `RelaunchFn` を包み、それを呼ぶ*前に* `app_launched_at` を記録し直します。順序は、`_spawn_cold` がすでに使っているものと同じです。

`AndroidEnvironment` には、対応するオーバーライドは要りません。すでに自前で `relauncher()` をオーバーライドしています
（[`android_environment.py:326`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py)）。そこでの `e.launch` は、Unit 5 がすでに名指す3か所の起動箇所のうちの1つです。

`_DeviceEnvironment.crawl_reset()`
（[`ios.py:124`](../../bajutsu/common/platform_lifecycle/environments/ios.py)）は、まったく同じ形の、iOS の3つ目の起動箇所です。`e.terminate(bundle_id)` に続けて `e.launch(...)` を呼びます。`crawl` 自身の `reset` 呼び出し可能オブジェクトが、フロンティアを再訪するたびに、これを走らせます（[`cli.py:300`](../../bajutsu/crawl/cli.py)）。`relauncher()` はまったく経由しません。

手を入れなければ、`relauncher()` のオーバーライドがいましがた閉じたのと同じ古びが、この経路で再び開いてしまいます。クロールは、1回の実行で複数のクラッシュを記録します（`current_fp = None; continue`、
[`_functions.py:668-669`](../../bajutsu/crawl/core/_functions.py)）。2件目のクラッシュの掃引は、自身の `crawl_reset` より前にまで遡ります。そして、1件目のクラッシュの `.ips` レポートを受け入れてしまいます。誤ったクラッシュのレポートが、レポートを正しく紐づけることこそが存在意義のディレクトリに入り込みます。

`XcuitestEnvironment` は、`crawl_reset()` もオーバーライドします。`relauncher()` のオーバーライドと同じ形です。ただし、`crawl_reset(eff)` 自体は*ファクトリ*です。`_build_lane` はこれを1回だけ呼び、返ってきた `Reset` を保持し続けます（[`cli.py:300`](../../bajutsu/crawl/cli.py)）。そのため、オーバーライドは、ファクトリ呼び出しの隣で記録するのではなく、返ってきた `Reset` を包みます。`_DeviceEnvironment` の `Reset` を呼ぶ*前に*、フロンティアを再訪するたびに一度、`app_launched_at` を記録します。

`AndroidEnvironment` には、ここでも対応するオーバーライドは要りません。自前の `crawl_reset()` の `e.launch`
（[`android_environment.py:410`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py)）が、Unit 5 がすでに名指す3か所のうちの3つ目だからです。

`_resume_warm` は、BE-0291 の*リースをまたぐ*ウォーム再利用の起動です
（[`xcuitest_environment.py:855-856`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py)）。iOS の4つ目の起動箇所で、リースをまたいで生き続ける同じ `XcuitestEnvironment` インスタンス上にあります。ランナーが再利用可能なときは、`start()` がここを通って返ります（`:297`）。`XcuitestEnvironment` は `has_reusable_resident()` をオーバーライドしているので、BE-0291 はこの環境を破棄せず、リースをまたいで保ちます。この経路でも、`app_launched_at` を再記録するものはありません。

手を入れなければ、他の2つのオーバーライドが閉じたのと同じ古びが、ここで再び開きます。しかも状況は、もっと悪くなります。2つの絞り込みが、そろって効かなくなるためです。流れは次のとおりです。

1. シナリオ1が、デバイス X でコールド起動します（目印 = T0）。そのアプリがクラッシュして、`Showcase-…ips` を残します。
2. シナリオ2から5が、同じデバイスでウォーム再開します。目印は T0 のままです。
3. シナリオ5のアプリがクラッシュし、`since = T0` で `DiagnosticReports` を掃引します。
4. シナリオ1のレポートは、実行ファイル名のパターン（同じアプリ）と `udid` の確認（同じ Simulator）の*両方*に一致します。`_app_crash_reports` はそれを受け入れ、シナリオ5の `app-crash/` にシナリオ1のクラッシュが入ります。

手当ては、他の2つと同じ形です。`XcuitestEnvironment` が、`_resume_warm` の中で、自身の `e.launch` の*直前*に `app_launched_at` を記録します。これで目印は、アプリが実際に動いている起動のものを追うようになります。

新しい `app_crash_artifacts() -> list[tuple[str, bytes]]` を、`RunEnvironment` プロトコル
（[`bajutsu/common/platform_lifecycle/protocols/run_environment.py`](../../bajutsu/common/platform_lifecycle/protocols/run_environment.py)）に加えます。`take_crash_snapshot()` の隣に置きますが、こちらはより単純です。`take_crash_snapshot()` は*サンク*を返します。バックエンドのクラッシュは、最初に観測した場所で捕捉し、プールがリースを解放するまで遅らせます。そうすれば、同じウォーム環境での並行ワーカーの次の起動が、凍結した照合条件を先に上書きすることがありません。

本項目の収集にも、同じ問題のより狭い版があります。ただし並行ワーカーではなく、*同じ*シナリオの内側で起きます。シナリオの途中で確定したクラッシュのあとに、あとかたづけの `relaunch` のような、ごくふつうの `after` ルールが続くことがあります。この `relaunch` は、この節自身の `relauncher()` オーバーライドを通じて `app_launched_at` を再記録します。シナリオの `RunResult` が組み立てられるより前に、です。

それ以降のどの時点で目印を生きたまま読んでも、あとかたづけがすでにクラッシュの先へ進めた `since` で掃引することになります。以前の草案が呼び出し箇所に選んだ、`pipeline.py` 自身の返ったあとの走査も含みます。

そこで `app_crash_artifacts()` は、`_finish_outcome` の中で同期的に呼びます。クラッシュを確認した瞬間に呼び、同じシナリオの後続ステップが走るより前です（「検知の方式」を参照）。結果は `StepOutcome` に載せて運び、あとで読み直しません。サンクは使いません。目印が動き得る時点より先へ遅らせるものが、ここにはないからです。呼び出し自体が、反応的な確認がすでにある場所へ、前倒しになるだけです。

`RunEnvironment` は構造的プロトコルで、具象クラスはこれを継承しません。そのため、`take_crash_snapshot()` にも継承される既定実装がありません。`WebEnvironment`、`AndroidEnvironment`、`_DeviceEnvironment`（`FakeEnvironment` が継承します）は、それぞれすでに、そのための1行の `return` を自前で宣言しています。

`app_crash_artifacts()` も同じ形に従います。ただし Android は例外です。

- `WebEnvironment` と `_DeviceEnvironment`（`FakeEnvironment` が継承します）は、それぞれ自前の `return []` を加えます。
- `XcuitestEnvironment` と `AndroidEnvironment` は、それぞれこの節と次の節が説明する本物の収集で、メソッドをオーバーライドします。

どちらかの宣言が、リースされるすべての環境に必要です。`pool.py` の `lease()` は、クラッシュしたリースだけでなく、あらゆるリースで、リースされるすべての環境からこのメソッドを読むためです。

同じプロトコルに、もう1つ、より狭いメソッドを加えます。理由は「Android：まず `logcat` のクラッシュバッファ、次に root 権限に依存する tombstone 取得」に詳しく書いています。`app_crash_tombstone() -> list[tuple[str, bytes]]` です。呼ぶのは `pipeline.py` の返ったあとの走査だけで、`_finish_outcome` からは呼びません。

このメソッドがあるのは、Android だけが収集を、タイミングの制約が異なる2つの呼び出し箇所に分けるためです。他のバックエンドには、2つ目のメソッドは要りません。ただし、`pool.py` の `lease()` のクロージャは、`app_crash_artifacts()` を読むのと同じ無条件の方法で、これもリースされるすべての環境から読みます。構造的プロトコルのメンバーは、実際の処理がある場所だけでなく、あらゆる場所で宣言が必要です。

`WebEnvironment` と `_DeviceEnvironment`（`FakeEnvironment` と `XcuitestEnvironment` がどちらも継承する基底です）は、それぞれ対応する1行の `return []` を加えます。`app_crash_artifacts()` の no-op、またはオーバーライドの隣に置きます。`XcuitestEnvironment` は、この no-op を繰り返さずに継承します。tombstone 取得そのものでオーバーライドするのは、`AndroidEnvironment` だけです。

`ReportCrash` は、アプリが死んだ瞬間にレポートを書き終えているとは限りません。`_app_crash_reports` は、`~/Library/Logs/DiagnosticReports` を最大数秒ポーリングします。探すのは、ターゲットの実行ファイル名とこの環境自身の `udid` に一致し、`app_launched_at` 以降に更新されたレポートです。

このポーリングは、このメソッド1つの内側での短く上限つきの待機であり、シナリオのリトライではありません。掃引が何かを見つけたかどうかにかかわらず、シナリオは即座に1回だけ失敗します。

掃引は、最後の書き込みだけでなく、本体全体が1つの `try`/`except Exception` で包まれたままです。ディレクトリの走査や読み取りの失敗は、レポートが見つからないことと同じく、アプリ自身のクラッシュの判定を変えられません。そのため、同じく空のリストへ解決します。非 macOS ホストも、同じようにその場で `[]` へ解決します。

### Android：まず `logcat` のクラッシュバッファ、次に root 権限に依存する tombstone 取得

`AdbDriver`
（[`bajutsu/common/drivers/adb/adb_driver.py`](../../bajutsu/common/drivers/adb/adb_driver.py)）は、`app_crash_signal()` を `adb shell pidof <package>` で実装します。`AdbDriver` は今日、シリアルと、注入されたいくつかの呼び出し可能オブジェクトから構築されます。パッケージ名は持っていません。それを組み立てる `backends.make_driver` も同様です。

ターゲットのパッケージ名は、すでに `AndroidEnvironment` には届いています。`install`、`pm clear`、`force_stop`、`launch` の各呼び出しが、ターゲット自身の設定（`targets.<name>.android.package`）から取っています。ドライバには届いていません。

`make_driver`（[`bajutsu/common/backends.py`](../../bajutsu/common/backends.py)）に、`package: str | None = None` キーワードを加えます。`fetch_clock` や `act` をすでに通しているのと同じ方法で、`AdbDriver.__init__` へ通します。これは `Driver` のメンバーではなく、ふつうのコンストラクタ引数です。`Driver` は `@runtime_checkable` で、共有の基底クラスを持ちません。そこにデータメンバーを置くと、すべてのバックエンドと、すべてのインラインのテストダブルが、その宣言を繰り返す必要が生じます。

`package` は、同じ境界で `None` を既定値にします。ここでの `None` は、iOS 側の `ios.app_path is None`（「iOS：`.ips` レポートの照合」）のようには、安全側に倒れません。`dumpsys activity exit-info` にパッケージ引数を渡さなくても、エラーにはなりません。端末上の*すべての*パッケージについて `ApplicationExitInfo` を報告します。

そのため、`package=None` で構築した `AdbDriver` は、引数なしの `pidof`（出力は空）を実行します。続いて、端末全体の exit-info 履歴を読みます。その最新エントリは、別のプロセスの `CRASH` かもしれません。テスト対象アプリには起きていないクラッシュを、確認済みにしてしまいます。

シナリオ実行用の `AdbDriver` は、すべて `AndroidEnvironment.start()` の中で構築され、そこではパッケージが常にスコープにあります。設計どおりなら、`run` の経路ではこのケースに到達しません。ただし、`make_driver` には、`package` も `launched_at` も渡さない呼び出し元が他にあります。

- [`bajutsu/serve/operations/_common.py:92`](../../bajutsu/serve/operations/_common.py)
- [`bajutsu/common/doctor/_functions.py:194,210`](../../bajutsu/common/doctor/_functions.py)

黙って `None` を既定値にすると、将来それらのどれかが安全でない答えを引き継ぎかねません。

そこで `app_crash_signal()` は、どちらの `adb` 呼び出しよりも前に、まず `package is None` を確認します。`launched_at is None` と、`launched_at()` 自身が `None` を返す場合も確認します。いずれの場合も、その場で `None` へ解決します。`ios.app_path is None` がすでにそうである、名前のついた事前確認と同じです。

空の `pidof` の返答は、実際には空文字列として Python に届きません。`AdbDriver` の既定の `RunFn` は `adb.real_run`
（[`adb/_functions.py:111-112`](../../bajutsu/common/backend_cli/adb/_functions.py)）で、素の `subprocess.run(..., check=True)` です。toybox の `pidof` は、一致なしのとき終了コード1で終わります。これは、この確認全体が観測したい、ふつうの想定された結果です。

そこで `app_crash_signal()` は、`pidof` の呼び出しを `except subprocess.CalledProcessError as exc` で包みます。これは、失敗が例外的ではなく想定内である端末コマンドについての、このリポジトリ自身の先例に合わせた形です。先例は、`AdbDriver._rooted()` が `adb shell id -u` を包む `except (subprocess.CalledProcessError, OSError)` です
（[`adb_driver.py:1234-1235`](../../bajutsu/common/drivers/adb/adb_driver.py)）。

- 空の `stdout` での終了は、空の `real_run` の戻り値が返った場合とまったく同じく、「プロセスなし」と読みます。
- それ以外の `CalledProcessError`、または `adb` 自身が不調なときの `OSError` は、`None` へ解決します。`package is None` や古すぎる `api_level` に対して、この節がすでに返している「確認できない」という答えと同じです。確認が捕まえるべき最初の本物のクラッシュで、未処理の障害として外へ抜け出すことはありません。

同じ包みが、後述の `dumpsys activity exit-info` 呼び出しにも及びます。ポーリングの途中で通信が一時的に乱れた場合に備えます。

アプリがまだプロセスを持っているはずなのに `pidof` の答えが空である、という事実は、必要条件ですが十分条件ではありません。起動が完了しなかった場合にも一致します。シナリオ上の原因が本項目に見当たらない終了にも、一致します。Android には、通常のテスト条件では jetsam のような OS による強制終了がありません。それでも、ふつうのプロセス終了は、クラッシュと同じ答えを `pidof` に返します。

`adb shell dumpsys activity exit-info <package>` は、そのパッケージについてプラットフォーム自身の `ApplicationExitInfo` 履歴を報告します。各エントリには時刻が付いています。`CRASH` または `CRASH_NATIVE` の理由を、`ANR`、`LOW_MEMORY`、`USER_REQUESTED` から区別できます。

これが使えるのは API 30 以降です。このリポジトリの CI がすでに起動する API 34 の AVD では使えます。ただし、このコードベースは今日、端末の API レベルを追跡していません。そこで `AdbDriver` に、`api_level: int | None = None` キーワードを加えます。`package` と同じ方法で通します。値は、`AndroidEnvironment.start()` で `adb shell getprop ro.build.version.sdk` により一度だけ読み、`self._package` の隣に保存します。

`app_crash_signal()` は、`api_level is None or api_level < 30` を、`package is None` と同じ事前確認に畳み込みます。同じ名前のついた形で安全側に倒れ、古い端末やエミュレータイメージでは存在しえないシグナルのポーリングは行いません。本項目の「はじめに」が、Android の exit-info による裏付けについて名指している範囲は、iOS の Simulator 限定という範囲と同じ考え方です。

ただし、exit-info の履歴はプロセスの寿命をまたいで残ります。そのため、最新のエントリ1つだけでは信頼できません。現在のプロセスが無関係な理由で終了し、まだ `ApplicationExitInfo` が記録されていない場合を考えます。そのとき `dumpsys` が報告する最新エントリは、同じパッケージの以前のシナリオのクラッシュのままです。

そこで `AdbDriver` に、`launched_at: Callable[[], tuple[float, str] | None] | None = None` というコンストラクタ引数を加えます。これは、`AndroidEnvironment` の起動の目印を、生きたまま読む呼び出し可能オブジェクトで、外から注入します。目印は、エポックと、その隣の `'%Y-%m-%d %H:%M:%S'` 形式の端末時計表記の両方を持ちます。構築時に凍結した値ではなく、呼び出しごとに読む点は、`fetch_clock` がすでに使っているのと同じ接合部です。

`pidof` が空を返した瞬間に1回だけ exit-info を読むと、確認しようとしているそのクラッシュと競合します。`pidof` は、プロセスが死んだ瞬間に空を返します。一方、`system_server` が対応する `ApplicationExitInfo` を記録するのは、死を回収してからです。ネイティブクラッシュなら、`crash_dump` が終わってから、さらに後になります。新しいクラッシュを確認するはずのその読み取りで、最新のエントリが、この起動より前の古いままということが起こり得ます。

そこで `app_crash_signal()` は、exit-info の履歴を、短く上限つきでポーリングします。iOS で `_app_crash_reports` が `DiagnosticReports` をすでにポーリングしているのと同じ形です。最大数秒かけて、*最新*のエントリが次の2条件を満たすまで読み直します。

- `CRASH` または `CRASH_NATIVE` を報告している。
- そのエントリ自身の `timestamp=` フィールドが、この起動以降を指している。このフィールドは、`ApplicationExitInfo` が*端末の*タイムゾーンで出す壁時計表記です。エポックではなく、オフセットも付いていません。

比較は、`launched_at()` の表記側と行います。つまり、タプルの2つ目の要素である、`'%Y-%m-%d %H:%M:%S'` 形式の端末時計表記です。起動箇所ごとにエポックと並べて記録されています。エポック側とは決して比べません。ホスト上で素の `timestamp=` を解決すると、*ホストの*タイムゾーンを通ります。UTC のエミュレータをそれ以外のゾーンのホストから動かすと、新しいクラッシュがすべて目印より何時間も前に置かれてしまいます。シグナルは、本物のクラッシュのたびに `None` を返すことになります。

ポーリングは2通りで終わります。条件を満たすエントリが現れる場合は、この起動より前の古いエントリではないと確かめられます。上限が尽きる場合は、`None` を返します。最新エントリが両方の条件を満たさないままだった場合も、`None` を返します。シグナルをまったく持たないバックエンドが返すのと同じ「確認できない」という答えです。

このポーリングは、iOS で `app.state` の `notRunning` が、Simulator 自身の制約から無償で得ている裏付けにあたります。Android では、プラットフォームが報告する、時刻に結びついた終了理由が、adb に同等の肯定的な確認を与えます。ただし、書き込み側が必要とするのと同じだけの猶予を、読み取り側にも与えることが条件です。

もっとも、フルの上限が必要なのは、シナリオ内で*最初*のポーリングだけです。最初の確認が、`system_server` の回収と競合するものだからです。最初の確認が上限を使い切っても一致しなかった場合、同じシナリオの後の確認には、新たに待つものがありません。後の確認の例は、`if` や `forEach` の中の3段の入れ子で起きるクラッシュと、失敗する `after` ステップすべてです（上の「検知の方式」を参照）。

そこで `AdbDriver` に、`self._exit_info_exhausted: bool = False` というインスタンスフィールドを加えます。既存の `_act_warned` と `_act_unavailable` のラッチと同じ形です。上限つきのポーリングが一致するエントリなしで終わった最初の時点で立てます。

`run` では、ドライバインスタンスが1つのシナリオより長く生きることはありません。`AndroidEnvironment.has_reusable_resident()` が無条件に `False` を返すためです（BE-0291 のリースをまたぐウォーム再利用は XCUITest 専用です。上の「iOS：`.ips` レポートの照合」を参照）。そのため、リースごとに新しい `AdbDriver` が構築されます。

`crawl` は事情が異なります。`crawl` は、ウォーク*全体*で1つの `AdbDriver` を作ります。`_build_lane` が `launch_driver` を1回呼び、`crawl()` は決して作り直しません（[`crawl/cli.py:283-300`](../../bajutsu/crawl/cli.py)）。後述の「`crawl` 自身のクラッシュ記録を拡張する」は、このウォークに沿ったすべての検知に、同じ確認をつなぎます。`run` だけが無償で得られる暗黙のリセットでは足りません。最初に確認できなかったクロールの検知のあと、ラッチが立ったままになります。そのラッチは、実行の残りの間、*本物の*クラッシュのポーリングをすべて止めてしまいます。

リセットを `invalidate_settled_cache()` に畳み込むのは、誤った接合部です。このメソッド自身の契約は、「画面が、このドライバ自身のアクチュエータ以外の何かによって変わった」ことを表します。`_act`、`_device_act`、`type_text` は、ふつうのジェスチャのたびにこれを呼びます
（[`adb_driver.py:352`](../../bajutsu/common/drivers/adb/adb_driver.py)、`:1119`、`:1145`、`:1462`）。ラッチをここへ畳み込むと、ほぼすべての確認の前に、抑制するはずのフラグが解除されます。本項目が確認をつなぐ2つのバックエンドの両方で、そうなります。

新しい `system_server` の回収を待つ価値が生まれるのは、本当に新しい起動のときです。「画面が動いた」ときではありません。そこで `AdbDriver` に、起動のための2つ目の狭いメソッドを加えます。`reset_exit_info_poll()` で、隣にある `SettledCacheInvalidator` と同じ形です。

呼び出すのは、`invalidate_settled_cache()` を*別の*理由ですでに呼んでいる、次の2つのクロージャです。

- `AndroidEnvironment.relauncher()` の `relaunch()`：`e.launch(...)` の直後
  （[`android_environment.py:339-351`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py)）
- `crawl_reset()` の `reset()`：自身の `e.launch(...)` の直後

`AdbDriver` 自身のアクチュエータの内側からは、決して呼びません。

ラッチが立っている間（まだ解除されていない間）は、後の呼び出しが上限つきのポーリングを飛ばします。exit-info の履歴をちょうど1回だけ読み、その1回が一致を見つけたかどうかにかかわらず、すぐに答えます。これが、本項目自身の勘定がすでに前提としている、確認できなかった確認のコストです。このラッチがなければ Android のシグナルが実際にかかる、数秒のポーリングではありません。

`AndroidEnvironment`
（[`bajutsu/common/platform_lifecycle/environments/android/android_environment.py`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py)）にも、iOS の環境と同じ `app_launched_at` の追跡を加えます。3つの起動の呼び出し箇所（`e.launch(package, launch_env)`）の*直前*に目印を記録します。直後には記録しません。

`e.launch` は `am start -W` です。`-W` フラグは、起動の完了を待ちます
（[`adb/_functions.py:634-637`](../../bajutsu/common/backend_cli/adb/_functions.py)）。戻ったあとに目印を記録すると、起動時のクラッシュはもう起きたあとです。そのクラッシュ自身の `logcat -t` ブロックと `exit-info` エントリは、どちらも目印より前になり、拒まれます。「レポートは存在するのに `bajutsu` が見つけられない」という、`_spawn_cold` の理由づけが iOS ですでに避けているのと同じ結果です。早めに記録した目印は、`start()` 自身の `force_stop` と `pm clear` がすでに絞り込んだ窓を広げるだけです。捕まえるべきクラッシュを取り逃すことは決してありません。

Unit 11 の Android フィクスチャは、シナリオの途中でトリガーをタップします。そのため、この順序は検証されません。問題になるのは起動時のクラッシュ、つまり `am start -W` が戻る前に死ぬアプリです。Unit 13 が、これを直接固定します。

目印は、ホストではなく端末自身の時計を、起動時に読んで作ります。端末時計の後続の読み取りとだけ比べる起動の目印は、ホストと端末の時計の調整を、まったく必要としません。`app_launched_at` 自身は、その読み取りのエポックのフィールド（`adb shell date +%s`）です。後述の tombstone の mtime の比較は、この値を直接使います。エポックと比べるからです。

他の2つの利用者は、同じエポックの値を使えません。理由は2つに分かれますが、どちらも1つの規則に行き着きます。端末側の表記を、ホスト上でエポックに解決してはなりません。その変換は、端末ではなく*ホストの*タイムゾーンを通ります。UTC のエミュレータをそれ以外のゾーンのホストから動かすと、新しいタイムスタンプがすべて目印から何時間もずれます。この設計がそれ以外では避けている、ホストと端末の時計の調整を、黙って呼び戻してしまいます。

- **exit-info のポーリング。** `dumpsys activity exit-info` の `timestamp=` フィールドは、`ApplicationExitInfo` 自身の壁時計表記で、端末のタイムゾーンを使い、オフセットを持ちません。そのため、上の exit-info のポーリングは、同じ起動の瞬間を表す2つ目の端末時計表記（`'+%Y-%m-%d %H:%M:%S'`）と比べます。この表記は、`AdbDriver` が受け取る `launched_at()` のタプルの表記側であり、エポック側ではありません。
- **`logcat -t`。** こちらもエポックの値を使えませんが、理由はより地味です。`adb logcat -t` は多義的です。整数の引数は*行数*（「直近の N 行」）として読まれ、時刻の境界としては読まれません。時刻の境界として読まれるのは、引用符で囲んだ `'MM-DD hh:mm:ss.mmm'` 形式の文字列だけです。これは exit-info の表記とは別の形式です。`logcat` の表記には年がなく、exit-info の表記には年があります。どちらも他方の代わりにはなりません。

そのため、各起動箇所は **1回の** `adb shell "date '+%s|%Y-%m-%d %H:%M:%S|%m-%d %H:%M:%S.000'"` の読み取りを行います。書式は*端末の*シェル向けに引用符で囲んでいます。囲まないと、`|` をパイプと読み、残りを単語に分割してしまうからです。3つのフィールドの分割は、ホスト側で行います。単なる文字列の分割であり、タイムゾーンの解決ではないので、上の規則は守られます。

`date` を3回別々に呼ぶと、3つの異なる時刻に記録されます。最後に記録される `logcat` の目印が、その隙間で起きたクラッシュを除外してしまいます。1回の読み取りは、これを避けます。

3つ目のフィールドは、`app_launched_at` の隣に保存し、`logcat -t` だけが使います。エポックをそのまま渡すと、リングバッファ全体が、時刻の境界なしに黙って返ってきます。後述のプロセスの絞り込みはパッケージだけで行うため、同じパッケージの以前のシナリオのクラッシュが、このシナリオのものとして通り抜けてしまいます。

本項目の Android の収集は、両方を収集するという範囲の決定どおり、2つの層を取ります。ただし、2つの層は同じ呼び出しを共有せず、同じ時点でも走りません。

- `logcat` は、クラッシュを確認した瞬間、つまりシナリオの途中の `_finish_outcome` の中で読んでも安全です（「検知の方式」）。昇格した権限が要らず、後のステップがまだ必要とするチャンネルにも触れません。
- tombstone の取得には、昇格した権限が必要です。その時点で実行するのは、積極的に有害です（後述）。そのため、もとの返ったあとの経路に残します。

`app_crash_artifacts()` は `logcat` の層だけを返します。2つ目の別メソッド `app_crash_tombstone()` が tombstone の層を返し、あとで呼ばれます。各層は独立に包まれていて、一方の失敗がもう一方を落とすことはありません。

1. **`logcat` の `crash` バッファ。** 常に試み、昇格した権限は要りません。`logcat -b crash` は、端末全体のリングバッファです。起動をまたぎ、同じ端末を共有するシナリオをまたいで残ります。
   [`scripts/collect_android_diagnostics.sh`](../../scripts/collect_android_diagnostics.sh) は、失敗した CI ジョブの最後に、これをまるごとダンプします（`-b main,system,crash,events,radio`）。起動ごとにバッファを消去すると、この終了時の掃引がまだ必要とする証跡を壊します。

   本項目は、バッファを消去しません。あとで行う `adb logcat -b crash -d -t "<MM-DD hh:mm:ss.mmm 形式の起動の目印>"` のダンプは、上で記録した `logcat` 形式の表記を、`logcat` 自身の時刻フィルタとして使います。エポックの `app_launched_at` は使いません。そのため、ダンプに含まれるのは、このシナリオが走っている起動以降の内容だけです。同じ端末を共有した以前の実行で、同じパッケージが残した古いクラッシュは含まれません。目印より前の内容は、終了時の掃引が見つけられるように、そのまま残ります。

   ダンプの解析は2通りです。`logcat` のクラッシュバッファが実際に持つ、2つの形式に対応します。

   - `FATAL EXCEPTION` ブロック：マネージドコード（Java/Kotlin）のクラッシュ向けです。
   - 見つからないときは、ネイティブクラッシュバッファ自身の `Fatal signal <n>` ヘッダ行：NDK のクラッシュ向けです。

   どちらの解析も、時間窓だけでなく、テスト対象アプリ自身のプロセスに絞ります。このバッファは、起動をまたぐだけでなく、プロセスをまたぐ端末全体のバッファだからです。マネージドのブロックを受け入れるのは、その `Process: <package>` 行がターゲットの `self._package` を名指すときだけです。ネイティブのブロックを受け入れるのは、その `>>> <process> <<<` ヘッダが名指すときだけです。同じ時間帯に落ちたシステムサービスや別のアプリが、このシナリオの証跡として書かれることはありません。

   1回だけ、すぐにダンプしても、ネイティブクラッシュでは空になることがあります。`crash_dump` が `>>> <process> <<<` ブロックを書くのは、`pidof` がすでに検知の入口として報告した死のあとだからです。これは、上の `app_crash_signal()` 自身の exit-info のポーリングが閉じようとしているのと同じ非同期です。そこで `app_crash_artifacts()` は、同じ短い上限つきの形で、最大数秒、ダンプを繰り返します。最初の `-d` のスナップショットを信用せず、一致が現れるか、上限が尽きるまで繰り返します。

   一致したブロックを取り出し、`logcat-crash.txt` として書き出します。これは、adb バックエンドがすでに到達できるあらゆる AVD や実機で、利用できることが保証された唯一の証跡です。

2. **tombstone の取得。** ベストエフォートで、root 権限に依存し、`logcat` の層のあとに最後に走ります。次の4つの段落が、シナリオのあとに走らせる理由、ゲートと取得の中身、root の復元、`crawl` で使わない理由を述べます。

   **シナリオのあとに走らせる理由。** `adb root` は、このバックエンドが対象とするエミュレータイメージでは、すでに日常的な操作です。ただし、`adbd` を再起動します。`scripts/collect_android_diagnostics.sh:98-102` は、`adb root` の直後の自前の `adb wait-for-device` で、これにすでに対処しています。理由は「adbd restarting as root」と注記されています。再起動には、2つの影響があります。

   - 常駐サーバ自身の `am instrument -w` セッションを、`adb forward` のマッピングだけでなく丸ごと殺します。`instrument_cmd` の `-w` は、「`UiAutomation` セッションを保持して、インストルメンテーションをアタッチしたままにする」ものです
     （[`adb/_functions.py:604-616`](../../bajutsu/common/backend_cli/adb/_functions.py)）。
   - BE-0283 のネットワークコレクタの `adb reverse` トンネル（`android_environment.py:298-306`）も、同じように落とします。

   もう存在しないセッションにポートを張り直しても、回復しません。回復する唯一の方法は常駐サーバ自体の再起動で、これはこの層が試みない、より重い操作です。

   この取得をシナリオの途中で、`logcat` の層と並べて `_finish_outcome` の中から走らせると、単に無駄なだけでなく、積極的に有害です。次のものが、同じ常駐サーバを通じて、まだ操作とスクリーンショットを行うからです。

   - まだ確定中の、`if` や `forEach` を包む outcome
   - シナリオレベルの `expect`（`_functions.py:741-752`）
   - シナリオが発火させる `after` ルールすべて（`_functions.py:821-831`）

   そこでのチャンネルエラーは、`BackendCrashError` を送出します。`run_scenario` は、これを失敗に折り込まず、意図的に再送出します（`_functions.py:839-846`）。エラーは `pipeline.py` のクラッシュリトライのループへまっすぐ入ります。そして、本項目が生み出すためにある `RunResult` を捨てます。`app_crashed` と `app_crash_artifacts` も含みます。

   そこで `app_crash_tombstone()` は、2つ目の別メソッドとし、`pipeline.py` の返ったあとの走査だけが呼びます（「収集をつなぐ」）。今度は、シナリオが本当に終わっています。終わったと信じているだけではありません。`lz.release()` の前に残る処理は、すべて `lz.collector` 自身がすでに捕捉したデータから読み、ドライバを通すことは決してありません。対象は、ネットワークスナップショットの書き込みと進捗の行です。

   `AndroidEnvironment.start()` は、リースのたびに、常駐サーバとリバーストンネルをゼロから作り直します（`_begin_resident`、`bridge_collector`）。これは、この設計がすでに前提としている、ふつうの「ウォームな常駐を保たない」破棄と再構築です。この端末での次のリースは、この層が壊したものの影響を受けません。

   **ゲートと取得の中身。** `adb root` の後には、端末に対して何かを実行する前に `adb wait-for-device` を置きます。`collect_android_diagnostics.sh:99-103` が、自前の `adb root` と `adb pull` の間に置いているのと同じゲートです。このゲートがないと、以降のすべてが `adbd` の再起動と競合します。

   次に、起動の目印以降に更新された、最新の `/data/tombstones/tombstone_NN` を取得します。比較は端末相対のタイムスタンプで行うため、ここでも時計の調整は要りません。

   目印をこれほど遅く読んでも、本項目の他の箇所（「iOS：`.ips` レポートの照合」）で、あとかたづけの `relaunch` が引き起こす iOS の `.ips` や `logcat` の誤った紐づけのリスクは生じません。あとかたづけは、`app_launched_at` を*前へ*進めるだけです。この境界が起こし得る最悪の事態は、すでにあった tombstone を取り逃すことです。古い tombstone を誤った名前で添付することは、ありません。

   実機、user ビルド、`adb root` の拒否のいずれでも、この層は黙って飛ばします。事象は報告され、`logcat-crash.txt` も書き出されます。root を拒む端末が失うのは、ネイティブフレームの詳細だけです。マネージドコードのクラッシュは、もともとそれを必要としません。

   **root の復元。** `adb root` には、`adb unroot`（と2回目の `adb wait-for-device`）を対にして、取得が終わったあとに、同じベストエフォートの包みの中で実行します。これで、端末は、他のすべてのリースがすでに前提としている権限レベルに戻ります。そうしないと、`adb root` は端末全体で、`adb unroot` か再起動まで続きます。このリポジトリには、それを元に戻すものが他にありません。

   既存の `adb root` の呼び出し元は1つだけです
   （[`scripts/collect_android_diagnostics.sh:101`](../../scripts/collect_android_diagnostics.sh)）。ジョブの終わりに走り、そのあとに実行するものを意図的に残していません。この層のシナリオ途中の取得は、その立場にありません。

   元に戻さないと、この端末を共有する後のシナリオは、ふつうの `adb shell` ではなく root の `adb shell` を通って動きます。`AndroidEnvironment.start()` 自身の `install`、`pm clear`、`force_stop`、`launch` の呼び出しも含みます
   （[`android_environment.py:122-146`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py)）。切り替わりは黙って起こります。端末の状態は、以前の無関係なシナリオがたまたまクラッシュしたかどうかで変わります。shell の uid より下流のものも変わります。たとえば、取得したファイルの所有者です。これは、決定的なコアが締め出すためにある、実行順への依存そのものです。

   漏れはさらに深刻です。`AdbDriver._rooted()` は、ドライバごとに一度 `adb shell id -u` を問い合わせ、答えをキャッシュします
   （[`adb_driver.py:1230-1237`](../../bajutsu/common/drivers/adb/adb_driver.py)）。2つの操作の決定が、これを読みます。

   - `double_tap` は、答えが `True` のとき、`input tap` ではなく生の `sendevent` の経路を選びます。
   - 2本指のジェスチャは、答えが `False` のとき `base.UnsupportedAction` を送出し、`True` のとき実行します。

   root が漏れると、この2つが黙って反転します。root 化されていない端末では `UnsupportedAction` で大きく失敗すべき2本指ジェスチャのシナリオが、実行されて通ってしまいます。それを決めるのは、以前の無関係なシナリオがたまたまクラッシュしたかどうかです。これは同じ種類の実行順への依存ですが、ファイルの所有者だけでなく、シナリオの合否の判定に直接及びます。

   `adb unroot` 自身の失敗を、同じベストエフォートの包みの中で握りつぶすと、これが目に見えないまま起こります。そのため、復元は試みるだけでなく*検証*します。2回目の `adb wait-for-device` のあとに `adb shell id -u` を読み直し、まだ `0` と答えたら、大きく記録します。診断の収集が、この端末での後続のすべてのシナリオの操作の仕方を、黙って変えてはなりません。そのため、この失敗は、最初の取得自身の失敗のようには握りつぶしません。これで、以降のシナリオは、どれも、もともと得られたはずのものと同じだけ新しい常駐サーバと `adbd` を得ます。

   **`crawl` で使わない理由。** 「この時点以降、どちらのチャンネルも要らない」という正当化は、`run` のシナリオごとのリースに固有です。そこでは、環境がリースのたびに破棄され、ゼロから作り直されます（`pool.py`）。`crawl` は、この正当化に当てはまりません。

   「`crawl` 自身のクラッシュ記録を拡張する」は、`env.app_crash_artifacts` をクロールのループへ通します。これは `logcat` の層だけです。`crawl` は `app_crash_tombstone()` をまったく呼びません。ところが、クロールのレーンは、ウォーク全体で*1つの*環境を保持します（`_build_lane`、[`cli.py:294-300`](../../bajutsu/crawl/cli.py)）。フロンティアの途中でクラッシュが起きても、ウォークは終わっていません（`current_fp = None; continue`、
   [`_functions.py:668-669`](../../bajutsu/crawl/core/_functions.py)）。最初に確認された Android のクラッシュで tombstone を取得すると、同じレーンの以降のクエリがすべて壊れます。クロール自身が終わるまで、常駐サーバやリバーストンネルを作り直すものがありません。

   そのため、`crawl` の Android の収集は、構造的に `logcat` だけです。`app_crash_tombstone()` にそもそも配線しないだけで、そうなります。本項目の以前の草案は、`run` だけのクロールレーンのフラグを `environment_for` に通していました。そのフラグは、この目的にはもう要りません。`crawl` が呼ぶメソッドで切り分けるだけで十分です。

### 失敗したシナリオの run ディレクトリへ収集をつなぐ

反応的な確認は、他のあらゆる終端の失敗がすでに通るのと同じステップループの内側で、経路の中に収まって走ります。そのため、`run_scenario` のふつうの `RunResult` 組み立ては、確認のあとも変わらず走ります。次のものは、同じステップで `ElementNotFound` が失敗した場合とまったく同じように着地します。

- 失敗したステップ自身のスクリーンショット
- すでに完了したステップ
- シナリオレベルの `after: on: error` ルール

シナリオ全体にわたってすでに動いているビデオ録画も、同じように停止し、添付されます。

`AppCrashedError` は、送出された例外として `run_scenario` の外へ出ません。`pipeline.py` の既存のクラッシュリトライのループは、外へ出た `BackendCrashError` にだけ反応します。このエラーは、ループの目に触れません。シナリオは、他の終端のステップ失敗がすでにそうしているのと同じように、1回だけ失敗します。リトライを防ぐための特別扱いは要りません。

証跡の*収集*は、経路の中の `_finish_outcome` に残します（「検知の方式」を参照）。証跡のディスクへの*コピー*は、その外に残します。BE-0421 自身のコピーがそうしているのと同じ理由です。`_step_runner` の sink
（[`bajutsu/common/orchestrator/loop/_loop_config.py`](../../bajutsu/common/orchestrator/loop/_loop_config.py)）は `EvidenceSink` です。その表面は、`capture`、`wait_diagnostic`、区間の開始と終了のペアだけで、任意の名前付き書き込みはありません。しかも、動いているシナリオにスコープされています。クラッシュの証跡には、`pipeline.py` がすでに持つ、run スコープの `RunArtifactWriter` と `sid` が必要です。

`Lease`（[`bajutsu/common/runner/types.py`](../../bajutsu/common/runner/types.py)）に、対応する2つの呼び出し可能オブジェクトを加えます。

- `app_crash_artifacts: Callable[[], list[tuple[str, bytes]]]`
- `app_crash_tombstone: Callable[[], list[tuple[str, bytes]]]`

どちらも、`crash_artifacts` がすでにそうしているのと同じように、モジュールレベルの専用 no-op を既定値にします。どちらも `pool.py` の `lease()` のクロージャで、`crash_artifacts` の隣に配線し、環境の2つのメソッドを直接読みます。

`pipeline.py` 自身より先へ運ばれるのは、1つ目だけです。`lz.app_crash_tombstone` は、`_run_on_lease` 自身の返ったあとの走査から直接呼びます（下を参照）。ステップループには一切通しません。そこで実行するのは、本項目の Android の節が排除しているタイミングそのものだからです。

1つ目の呼び出し可能オブジェクトは、`relaunch` がすでに通っているのと同じ方法で、ステップループに届きます。`pipeline.py:898` が `relaunch=lz.relaunch` を `run_scenario` に渡し、`run_scenario` がそれを `_LoopConfig.relaunch` に通します。本項目は、同じ形のフィールド `capture_app_crash: Callable[[], list[tuple[str, bytes]]] | None` を加えます。同じ方法で `lz.app_crash_artifacts` から通します。呼ぶのは `_finish_outcome` であり、`pipeline.py` ではありません。

本項目の以前の草案では、`pipeline.py` 自身が `lz.app_crash_artifacts()` を呼んでいました。`run_scenario` がすでに返ったあとの、返ったあとの走査からです。この草案は、`app_launched_at` を、シナリオ自身の `after` フェーズがすでに先へ進めてしまった時点で、生きたまま読みます。`after` フェーズは、`run_scenario` が返る前の*内側*で走ります。これが生む、あとかたづけの `relaunch` の失敗は、「iOS：`.ips` レポートの照合」で説明しています。

呼び出しを `_finish_outcome` の内側に置けば、この問題は閉じます。掃引はクラッシュを確認した瞬間に走り、同じシナリオの後続のステップがどれも目印に触れる前です。あとかたづけも含みます。結果は `StepOutcome` に載せて運び、あとで読み直しません。

`pipeline.py` の `_run_on_lease` は、`run_scenario` が返った直後に、クラッシュした outcome を求めて `result` を読みます。まだリースしたのと同じリースを保持していて、自身の `finally` が解放するより前です。`app_crashed` を求めて `(*result.before_outcomes, *result.steps, *result.after_outcomes)` を走査します。`result.steps[-1]` は読みません。理由は2つあります。

- 入れ子のクラッシュは、それを包む `if`・`forEach` の outcome を、クラッシュした outcome の*あとに*確定させます（`_step_runner.py:220`、`:238`）。クラッシュした outcome が最後とは限りません。
- `result.steps` だけでは、`before` と `after` のフェーズがまったく含まれません。`RunResult.steps` は主フェーズ自身のリストです（`_functions.py:851`）。`before` のステップが失敗した場合は、まるごと `[]` になります（`_functions.py:732-740` が、その場合は主ステップを飛ばします）。

走査が1つ見つけると、新しい `_write_app_crash_artifacts(lz, outcome, s, sid)` が書き込みを行います。これは `_write_crash_artifacts`（BE-0421、`pipeline.py:803`）とほぼ同じ形ですが、姉妹関数が必要としない引数を1つ多く取ります。姉妹関数は、すべての証跡を `lz.crash_artifacts()` から直接読みます。新しい関数は、見つけた `outcome` 自身の `app_crash_artifacts` から始めます。それは確認の時点ですでに収集済みで、ここで掃引し直しません。そのため、走査が見つけた outcome は、暗黙のままにせず、引数として渡す必要があります。

関数は次に、そのリストを無条件に、もう1回の呼び出し `lz.app_crash_tombstone()` で拡張します。この呼び出しが行われるのは、ここが初めてです。これは、本項目が `_finish_outcome` の内側では収集*しない*、唯一の証跡です。理由は「Android：まず `logcat` のクラッシュバッファ、次に root 権限に依存する tombstone 取得」にあります。取得には `adb root` が必要で、それをシナリオの途中で走らせると、外へ抜ける `BackendCrashError` によって `RunResult` 全体を失う危険があります。

ここで呼ぶのは、本当に安全です。`run_scenario` はすでに返っているので、この呼び出しが再起動しようとする常駐サーバを通じて、同じシナリオの後続のものが操作を続けている可能性はありません。

`_run_on_lease` が保持するのは `Lease` であり、バックエンドの識別子ではありません。そのため、この呼び出しは `result.backend == "adb"` で分岐せず、無条件のままにします。分岐すると、決定的なコアにバックエンドごとの知識が入ります。「プラットフォームはバックエンドである」という接合部（[`CLAUDE.md`](../../CLAUDE.md)）が、コアから締め出しているものです。無条件の呼び出しなら、2つ目のバックエンドが tombstone に相当するものを持つ日に、編集する箇所も1つ少なく済みます。

書き出されるのは、合わせたリストです。`logcat` が先で、tombstone のエントリがあとに加わります。iOS、web、fake では、`app_crash_tombstone()` が no-op なので、後者は空です。

- `(name, content)` の各組は、`writer.write_text(f"{sid}/app-crash/{name}", content.decode(errors="replace"))` で書き出します。これは `write_bytes` ではなく、機密情報をマスクするテキストの経路です。BE-0421 自身のコピーも、同じ理由でこれを使います。クラッシュレポートは、クラッシュしたアプリが機密情報を書き込み得るテキストだからです。
- ディレクトリを名指すトレーラーを `result.failure` に追記します。`_write_crash_artifacts` が `pipeline.py` の追記用に返すのと同じ形です。

書き込みの問題や、`app_crash_tombstone()` 自身の失敗は、記録するだけで送出しません。これは、BE-0421 のコピーと同じ姿勢です。診断の収集が、すでに決まった失敗を別の失敗に変えてはなりません。tombstone を失っても、すでに outcome にある `logcat` の層を失ってはなりません。

これは、返ったあとの単純な確認であり、新しい `except` 節ではありません。上で述べたシナリオ自身のリトライの振る舞いは、この確認が走る時点で、すでに決まっています。

`StepOutcome.app_crash_artifacts` は、上のようにディスクへコピーされるだけでなく、outcome 上にも載ります。この配置は、本項目が勘定に入れる必要のある、2つ目の利用者を生みます。`manifest_dict` の `_scenario_dict(r)`
（[`bajutsu/common/report/manifest.py`](../../bajutsu/common/report/manifest.py)）は、素の `asdict(r)` です。そのため、`steps`、`before_outcomes`、`after_outcomes` から到達できる `StepOutcome` はすべて、クラッシュしたものも含めて、そのままマニフェストの dict に入ります。`write_json` の `json.dumps` は `default=` を持ちません（[`bajutsu/common/evidence/sink.py`](../../bajutsu/common/evidence/sink.py)）。

生の `bytes` には JSON のエンコードがありません。最初のアプリクラッシュのシナリオが、`manifest.json` を書くときに `TypeError` を送出します。クラッシュが正しく分類されたあとに起こり、run 全体のマニフェストと HTML レポートを巻き込んで落とします。

本項目は、`crawl` 自身の `Crash.artifacts` については、すでに正しく逆の判断をしています（後述の「`crawl` 自身のクラッシュ記録を拡張する」）。同じ理由で、`serialize.py` の往復から意図的に除いています。`_scenario_dict` にも、同じ危険の `run` 側に対応する除外が必要です。すでに行っている `wall_offset_s` の取り除き
（[`bajutsu/common/report/manifest.py`](../../bajutsu/common/report/manifest.py)）の隣で、`steps`、`before_outcomes`、`after_outcomes` の各 outcome の dict をたどり、そこから `app_crash_artifacts` も取り除きます。`wall_offset_s` は `RunResult` のトップレベルのフィールドで、1回の取り除きで届きます。`app_crash_artifacts` は、3つのリストが運ぶすべての `StepOutcome` の中に入れ子になっているため、除外はそれらをたどる必要があります。

`report/load.py` の逆変換には、対応する変更が要りません。`_step` の `_kw(StepOutcome, d)` は、`d` に欠けているフィールドを、すでにデータクラスの既定値で再構築します。`RunResult` の `wall_offset_s` に対しても同じことをしています。取り除かれた `app_crash_artifacts` は、ふつうのステップがもともと持つ空の既定値と同じく、`()` として再構築されるだけです。

クラッシュは、それでも完全に報告されます。`outcome.reason` と `outcome.app_crashed`（素の `bool` で、シリアライズの危険はありません）は、これまでどおり往復します。この節がすでに書き出す、`{sid}/app-crash/` 配下のマスク済みコピーが、レポートの読み手がたどる永続的なコピーです。outcome 上のメモリ内のバイト列は、その書き込みに届くためだけにあり、マニフェストに届くためにはありません。

`_kw` 自身のドキュメント文字列コメント（[`load.py:34-41`](../../bajutsu/common/report/load.py)）は、現在、`wall_offset_s` を、往復の保証が及ばない「意図的な唯一の例外」として名指しています。本項目自身の2つ目の除外は、その主張が着地した瞬間に古びさせます。このコメントは、見落としに見える除外を将来の開発者が「直す」のを止めるために、まさに存在しています。

何も書かなければ、コメントが防ごうとしたのと同じ危険が、`app_crash_artifacts` 自身で再発します。開発者が、このフィールドが往復で残らないことに気づき、「*唯一の*意図的な例外」という記述を読むとします。その開発者が、バグ修正として `_scenario_dict` の取り除きを外すと、この節が避けるためにある `manifest.json` の `TypeError` が再び起きます。しかも `test_round_trip_through_manifest_is_lossless` 自身のフィクスチャは、このフィールドを既定値のままにします。そのテストも、この退行を捕まえません。同じコメントの中で `app_crash_artifacts` を `wall_offset_s` と並べて名指しすることは、本項目自身の範囲に含まれます。後続の作業にはしません。

### `crawl` 自身のクラッシュ記録を拡張する

`crawl()`（[`bajutsu/crawl/core/_functions.py`](../../bajutsu/crawl/core/_functions.py)）自体は、環境を保持しません。受け取るのは、注入された呼び出し可能オブジェクトだけです。`driver`、`reset`、`is_alive`、`recover`、`on_event` などです。これが、クロールのコアを `platform_lifecycle` から切り離し、同じループですべてのバックエンドを動かせるようにしています。

本項目の以前の草案は、`crawl()` の中へ環境の参照を持ち込むことを提案していました。そうすると、すべてのバックエンドと、高速スイートのすべての fake が、その参照を持つ必要に迫られます。しかも、その参照のきっかけにできるシグナルが、そもそもありません。`is_alive` が何も与えないためです。`ios.py` と `android_environment.py` は、どちらも `crawl_aliveness()` に `None` を返します。両者自身のコメントは、「エンジンがデバイスのクラッシュ検知のためにアクセシビリティツリーを読む」と述べています。そのため、`crawl()` は、本項目が対象とするどちらのバックエンドでも、`is_alive` コールバックなしで動きます。

[`bajutsu/crawl/cli.py`](../../bajutsu/crawl/cli.py) には、単一の `CrawlEnvironment` がスコープにあります（`plan.environment`）。ただし、これは上のヘルスの接合部を配線するためだけに、空の `udid` で構築されています（`environment_for(actuator, "")`）。クラッシュが実際に起きたデバイスではありません。

クロールは代わりに、レーンごとに1つの環境を動かします。`_build_lane` が、そのレーン自身の `driver` と `reset` の隣で、`--udid` ごとに1つ構築します（BE-0064）。マルチレーンのクロールで `plan.environment` を使うと、誤ったデバイスの診断を収集するか、何も収集しません。

そのため、収集は `driver` や `reset` がすでに通っているのと同じ方法で通します。`_build_lane` に3つ目の戻り値を加えます。そのレーン自身の `env.app_crash_artifacts` です。これを、レーンのドライバとリセットと並べて運びます。追加のレーンには `WorkerFactory`（`bajutsu/crawl/core/_functions.py`）を通し、最初のレーンには `crawl()` 自身の主レーンの引数を通します。

`record_crash` の呼び出し箇所（`bajutsu/crawl/core/_functions.py:667`）は、すでに `crashed = not alive(d, landed)` を、コーディネータのロックの外で計算しています。そのコメント自身が、これを「pure deterministic reads, off-lock」と呼んでいます。計算は、`coord.record_crash(path)` を呼ぶ直前に行われます。

`alive` は、本項目が対象とする両方のバックエンドで、UI ツリーのヒューリスティックである `is_app_alive` に戻ります。このヒューリスティックのリスクは、本項目自身の「はじめに」がすでに名指ししています。システムアラートや意図的な `background` ステップが、誤検知として読まれます。

`run` 自身の上限つきの `.ips`・tombstone のポーリングが待つ価値を持つのは、`app.state == notRunning` か、`pidof` と exit-info の確認が、先に事象を確認しているからです。`crawl` には、そうした確認がありません。ここでの誤検知は、発生のたびに上限いっぱいまでポーリングします。これは稀なケースではなく、ふつうのケースです。`crawl` はクラッシュを記録したあとも止まらず（`record_crash` の直後に `current_fp = None; continue`、`_functions.py:668-669`）、1回の実行で同じ誤検知に何度も入り込み得るからです。

そこで、本項目の収集呼び出しは、同じロック外の窓にある既存の確認に加わります。そのワーカー自身のレーンスコープの `env.app_crash_artifacts()` を呼ぶのは、ドライバが先に事象を肯定的に確認できるときだけです。確認の条件は、`isinstance(d, base.AppCrashSignal)` かつ、`None` でない `d.app_crash_signal()` です。`run` のために両方のバックエンドがすでに実装している、同じケイパビリティです。

`crawl` 自身の検知は、どちらの場合も変わりません。`Crash` は、これまでどおり UI ツリーのヒューリスティックだけで記録されます。ゲートされるのは、証跡の掃引だけです。確認できなかったクラッシュは、`artifacts` なしの `Crash` を記録します。覆いかぶさるアラートかもしれないものに対して、上限いっぱいのポーリングを払うことはありません。

その `d.app_crash_signal()` の呼び出しは、専用の `except (base.BackendCrashError, OSError, subprocess.CalledProcessError): pass` で包みます。これはチャンネルエラーを、確認できなかった答えと同じに扱います。`crawl` は `Crash` を記録して、`artifacts` なしでウォークを続けます。

ここでより広く捕まえるのは、呼び出し側の2つ目の防御です。「Android：まず `logcat` のクラッシュバッファ……」で述べた `app_crash_signal()` 自身の修正と並んで置きます。その修正の代わりにはなりません。`AdbDriver` の `RunFn` は `subprocess.run(..., check=True)` です
（[`adb/_functions.py:111`](../../bajutsu/common/backend_cli/adb/_functions.py)）。そのため、素の `except base.BackendCrashError` だけでは、握りつぶされずにこの呼び出し箇所へ届いた `pidof` や `exit-info` の失敗を見逃します。`backend_cli/adb/` の下には、`BackendCrashError` を送出するものが一切ありません。

「iOS：要素ツリーではなく `app.state` を使う」は、`run` については、チャンネルエラーを意図的に握りつぶしません。エラーは `XcuitestRunnerCrashError` として、`pipeline.py` がすでに持つ回復経路へ伝播します。`crawl` にはそうした経路がありません。`bajutsu/crawl/` の下に、`BackendCrashError` を扱うものはありません。

この呼び出しは、`_walk` 自身の `try`（`_functions.py:597-655`、`action.perform` と `_observe` にスコープされています）の外にもあります。「pure deterministic reads, off-lock」の窓の中にあります。この窓は、本項目が最初の例外を送出し得る往復を加えるまでは、純粋な Python でした。

ここで握りつぶさないと、エラーは次の経路をたどります。

1. `_run` の `except Exception: coord.note_failure(exc)` を通って伝播します。
2. join のあと、メインスレッドで再送出されます。
3. `cli.py` の素の `_execute(...)` から `_finish(...)` へ届きます。この間に `try`/`finally` はありません。

そのため `_finish` が走りません。`write_repros` は `screen_map.crashes` を一度もたどりません。本項目がウォーク*全体*にわたって溜めた、すべての再現とすべての証跡が捨てられます。しかも、本項目が証跡を収集するためにあるまさにその失敗で、そうなります。

ここで収集することは、正しさだけでなく、並行性にも関わります。`record_crash` は、本体全体にわたってコーディネータの `self._cond` を保持します。同じロックが、`on_event`（`_coordinator.py` の `_emit`）と、他のすべてのワーカー自身の `record_crash` と `record_edge` の呼び出しも直列化します。ロックの*内側*で数秒かかる `.ips` のポーリングや tombstone の取得を走らせると、他のすべてのクロールレーンが、その間止まります。先に走らせ、解決済みの値として渡せば、ふつうのリストへの追加以上のコストを、ロックに負わせません。

`Crash`（[`bajutsu/crawl/core/crash.py`](../../bajutsu/crawl/core/crash.py)）に、`artifacts: tuple[tuple[str, bytes], ...] = ()` フィールドを加えます。`record_crash` は、これを `path` と並べて受け取り、保存します。

[`bajutsu/crawl/repro.py`](../../bajutsu/crawl/repro.py) の `write_repros` は、すでに `screen_map.crashes` を順にたどって、各クラッシュの `crashes/crash-NNN.yaml` の再現を書き出しています。`NNN` は、そのクラッシュ自身の、リスト内でのゼロ詰めで1始まりの番号です（`enumerate(screen_map.crashes, start=1)`、`repro.py:154`）。`cli.py` の `_finish` は、`write_repros` を呼んで件数を出力するだけです。

番号を持っているのは `write_repros` のループなので、証跡を書き出す手順もそこに加えます。`_finish` が同じ番号を別のファイルで導き直すと、ロジックが重複します。番号の付け方がどちらか一方だけで変わると、2つの写しは黙ってずれていきます。

`write_repros` は、忠実に再生できない経路を持つクラッシュについても、`continue` で飛ばし、`.yaml` を書きません。たとえば `tap_point` アクションは、指し示すセレクタを持ちません。証跡の書き込みは、その `continue` の*前*に置きます。再生できないクラッシュは、代わりに実行できる再現がないため、プラットフォームのレポートの価値がもっとも高い種類です。再現とともに証跡まで失うことはありません。

手順は、空でない `artifacts` を `crashes/crash-NNN/app-crash/` の下に書き出します。`run` 自身のコピーが使い、`write_repros` が隣の再現 `.yaml` のためにすでに使っている、機密情報をマスクする `writer.write_text(…, content.decode(errors="replace"))` の経路を通ります。このディレクトリは、再現ファイルの兄弟であり、同名のトップレベルのディレクトリではありません。そのため、2つは一緒に見つかり、一緒に並びます。

クロール自身の検知は、これまでどおり UI ツリーのヒューリスティックのままです。`run` と違い、`crawl` には反応的な確認をぶら下げるシナリオのステップがないからです。収集を2つの入口が共有するのは、iOS です。Android では、`logcat` の層だけを共有します。root 権限に依存する tombstone の取得は `run` 専用だからです（「Android：まず `logcat` のクラッシュバッファ……」を参照）。検知は、どちらのバックエンドでも共有しません。

`Crash` は、[`serialize.py`](../../bajutsu/crawl/serialize.py) が JSON へ往復させる型でもあります。`screenmap_dict` が `screen_map.crashes` を書き出し（`:131-134`）、`screenmap_from_dict` が再構築します（`:92-98`）。生の `bytes` には JSON のエンコードがありません。そのため、`artifacts` は、他のすべてのフィールドの書き出しを base64 で広げる代わりに、両方向とも意図的に除いてあります。

`artifacts` は、それ自身の事情からも、すでにメモリ内だけのフィールドです。`on_event` 自身の `_write_screenmap` 呼び出し（`cli.py:186`）は、記録されたクラッシュのたびに、その書き出しを生きたまま行います。これは `write_repros` が走るよりずっと前です。`write_repros` が、完成した `screen_map.crashes` をたどるのは、正常に完了したクロールの最後の1回だけです（`cli.py:405`）。その1回の締めくくりの呼び出しの前に、このフィールドを永続化するものはありません。書き出しがあってもなくても同じです。

書き出しから除いても、`--resume` や `--continue-crawl` での再読み込みは、何も失いません。どちらの場合も同じです。

- **正常に完了した以前のクロール。** バイト列は、すでにディスクに書かれています。`write_repros` が走っているので、以前の run 自身の `crashes/crash-NNN/app-crash/` の下にあります。
- **中断した以前のクロール。** どちらにしても、バイト列は保持できません。`write_repros` に届く前にプロセスが死ぬので、バイト列はそもそもディスクに届きません。`--resume` は、以前のクロール自身の書かれなかったレポートについて、同じ損失をすでに受け入れています。

`screenmap_from_dict` は、引き継いだ `Crash` をすべて `artifacts=()`、つまりデータクラスの既定値で再構築します。クラッシュがこのフィールドを持つ前に保存されたマップの `actions` に対して、すでに行っているのと同じ扱いです。

### 本物のクラッシュで、スタブだけでなく証明する

単体テストは、`~/Library/Logs/DiagnosticReports` や、偽の `logcat`・tombstone の取得をスタブにできます。どちらも、本項目が依存する下のプラットフォームの仕組みが、上の設計の前提どおりに今も振る舞うことを証明しません。その仕組みとは、`ReportCrash` 自身の `.ips` の書き出しや、`logcat` の `crash` バッファのことです。

そこで、showcase アプリ（[`demos/showcase/`](../../demos/showcase)）に「クラッシュを起こす」仕掛けを加えます。ゲートするのは起動環境変数のフラグで、デバッグビルドではありません。

- showcase の iOS ソースには、`#if DEBUG` がどこにもありません。
- もっとも近い既存の先例は
  [`ConformanceView.swift`](../../demos/showcase/ios/swiftui/Sources/ConformanceView.swift) です。これ自身、起動環境変数 `SHOWCASE_CONFORMANCE` が設定されたときにだけ到達します
  （[`AppModel.swift:92`](../../demos/showcase/ios/swiftui/Sources/AppModel.swift)、
  [`RootView.swift:7`](../../demos/showcase/ios/swiftui/Sources/RootView.swift)）。BE-0114 のドライバ適合性画面であり、デバイス上の診断ではありません。

ビルド構成でゲートすると、仕掛けの存在自体が、iOS レーン自身の `build (app + runner)` ジョブがコンパイルする構成に左右されます。Release ビルドは、これをコンパイルから外してしまいます。新しい失敗を期待するシナリオは、クラッシュではなく、セレクタの欠落で失敗します。本項目が取り除こうとしている誤診断そのものです。

起動環境変数のフラグなら、ビルド構成についての前提が要りません。シナリオスキーマ自身の `preconditions.launchEnv` に乗ります。`SHOWCASE_CONFORMANCE` がすでに乗っているのと同じ接合部です。仕掛けは、iOS では `fatalError()` を呼び、Android ではメインスレッドで未捕捉の例外を投げます。プラットフォームごとに新しいシナリオを1つ加え、それがこの仕掛けをタップします。

どちらの新しいシナリオも、失敗する*ことが期待されます*。それを包む CI のラッパーは、2点を検証します。失敗が新しいアプリクラッシュの分類を持つこと。`app-crash/` が期待するファイルを持つこと。`fault-injection (xcuitest)` が、グリーンの実行ではなく診断された失敗の形を検証しているのと同じ方法です（[`docs/ci.md`](../../docs/ci.md#the-ios-lane)）。

このシナリオは、`ios-e2e.yml` と `android-e2e.yml` に、ゲートしない PR ごとのシグナルとして着地します。`actuation (xcuitest)` と `golden (adb)` の隣です。どちらも、レーンの必須の集約から意図的に外してあります。新しく配線したデバイス上のカバレッジは、必須の `E2E` チェックに昇格する前に、まずここで安定性を得ます。これらのレーンの他の新しいシグナルは、すべて同じ道をたどってきました。

### web backend と fake backend への影響

これは2つの別々の接合部であり、どちらのバックエンドも、どちらの半分にも意味のある作業を必要としません。

- **ドライバの接合部。** `PlaywrightDriver` は `AppCrashSignal` を実装しません。そのため、反応的な確認の `isinstance` による問い合わせは `False` を返し、確認を飛ばします。このプロトコルを宣言しない他のドライバと同じ扱いです。
- **環境の接合部。** `WebEnvironment`
  （[`bajutsu/common/platform_lifecycle/environments/web.py`](../../bajutsu/common/platform_lifecycle/environments/web.py)）は、自前の `app_crash_artifacts()` と `app_crash_tombstone()` を宣言し、どちらも `[]` を返します。`take_crash_snapshot()` のために、すでに持っている同一の宣言の隣に、1行ずつ加えるだけです。`RunEnvironment` は、具象クラスが継承しない構造的プロトコルなので、3つのメソッドのどれも、フォールスルーできる継承された既定値を持ちません。

`FakeEnvironment`
（[`bajutsu/common/platform_lifecycle/environments/fake.py`](../../bajutsu/common/platform_lifecycle/environments/fake.py)）は、同じ no-op を `_DeviceEnvironment` から継承します。fake のテスト用ドライバも、`AppCrashSignal` の接合部では同じように振る舞います。本項目は、web や fake バックエンドの実行が収集するものを、何も変えません。

## 検討した代替案

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| 既存の `BackendCrashError` 回復ループでリトライする | クラッシュ検知をバックエンドクラッシュと同じ扱いにし、リースを破棄してリトライする | 壁打ちで却下しました。この事象は、一時的なインフラの不調ではなく、アプリ自身の不具合である可能性が高いためです。再起動してのリトライは、再び失敗する見込みが高いシナリオにクラッシュ回復の予算を費やし、本物の不具合を flakiness として吸収してしまう危険があります（BE-0049）。 |
| 毎ステップの前に `app.state`・プロセスの生存を事前にポーリングする | 各ステップの実行前に、アプリがまだ動作しているかを確認する | 壁打ちで却下しました。構造上まれな失敗モードを捉えるためだけに、グリーンな実行を含むあらゆるシナリオのあらゆるステップへドライバの往復を1回追加してしまうためです。事後確認の設計でも、その事象が起きたまさにそのステップで捕まえられます。そのステップ自身のアクションかクエリが、すでに失敗しているからです。 |
| `AppCrashedError` を `run_scenario` の外へ送出し、`pipeline.py` に `BackendCrashError` を真似た専用の `except` 節を設ける | バックエンドクラッシュと同じ扱いで、リトライループへ入れる | レビューで却下しました。バックエンドがクラッシュしたシナリオと違い、アプリがクラッシュしたシナリオでは、ドライバもバックエンドプロセスも、動作中のビデオ録画も残ります。落ちているのはアプリだけです。別経路の例外は、`run_scenario` 自身の組み立てが他のあらゆる終端失敗に対してすでに生み出している、ステップ・証跡・`after: on: error` の発火を、ゼロから作り直す終端 `RunResult` で捨ててしまいます。 |
| `app_crash_signal()` を `Driver` プロトコルの必須メンバーにする | 個別の opt-in プロトコルではなく、`Driver` 自身にメソッドを1つ加える | レビューで却下しました。`Driver` は `@runtime_checkable` であり、あらゆる実装（`XcuitestDriver`、`AdbDriver`、`PlaywrightDriver`、`XcuitestLiveDriver`、fake backend、`WebContextDriver` のような狭いラッパー）がスタブを持つ必要に迫られます。本項目が確認するつもりのないバックエンドも例外ではありません。このコードベースがすでに `InterruptionPolicyTarget` や `SettledReadProvider` に使っている、狭い opt-in のケイパビリティプロトコルであれば、必要とする2つのバックエンドだけに届きます。 |
| Android で `logcat` のクラッシュバッファだけを使い、tombstone は取得しない | ネイティブクラッシュの取得を諦め、常に取得できる `logcat` だけに頼る | 却下しました。ネイティブ（NDK）クラッシュの完全なバックトレースを失い、`logcat` 自身が出力する簡略化された要約しか残らないためです。常に取得できる基盤としては残し、tombstone の取得はそれを置き換えるのではなく、端末が許す場合により豊かな詳細を上乗せします。 |
| Android で root 権限に依存する tombstone 取得だけを使い、`logcat` へのフォールバックを持たない | tombstone の取得だけに頼り、`logcat` の抽出は実装しない | 却下しました。実機、user ビルド、`adb root` を拒むエミュレータイメージでは、何も取得できなくなってしまうためです。`logcat` のクラッシュバッファは昇格した権限を必要とせず、よくあるマネージドコードのクラッシュについてすでに完全なスタックトレースを運びます。 |
| 新しいシナリオアサーション（例：`assert: appCrashed: false`）を追加する | シナリオ作者が明示的に「アプリがクラッシュしていないこと」を検証できるようにする | 却下しました。この事象は、ステップ自身のアクションかクエリの失敗によって、すでにシナリオを終わらせているためです。それを確認するアサーションが後から走れる時点は、シナリオの中に残っていません。showcase 自身のテスト用シナリオは、代わりに、失敗の*形*を run の外側から検証します。`fault-injection (xcuitest)` がすでに採っている方式と同じです。 |
| `video`・`deviceLog` と同様、`capturePolicy` の opt-in ルールの背後に収集を隠す | 明示的な指定がない限り収集を行わない | 却下しました。BE-0421 が自身の証跡について挙げた理由と同じです。この収集は、すでに失敗が確定したシナリオに対して一度だけ走ります。コストは、範囲の定まった掃引かログの読み取り1回であり、明示的な要求の背後へ隠すべき定常的なステップごとの負荷ではありません。 |
| BE-0421 がバックエンドクラッシュ自身の収集を遅らせているのと同じ形で、照合条件を凍結し `take_crash_snapshot()` 風のサンクで掃引を遅延させる | 収集をその場で実行せず、リース解放のタイミングまで遅らせる | 却下しました。BE-0421 のサンクが存在するのは、プールがリースを解放するより前に、同じ温まったプールされた環境を別のワーカーが再利用してしまう事態を切り抜けるためです。本項目自身の競合はこれより狭く、同じシナリオの内側で起きます。`after` フェーズのあとかたづけ `relaunch` が、`run_scenario` の戻る前に `app_launched_at` を記録し直してしまいかねません。これを閉じるには逆向きの手当てが要ります。`app_crash_artifacts()` を確認の時点で `_finish_outcome` の内側から同期的に、より*早く*呼ぶことであり、目印がすでに動きうる時点より先へ遅らせることではありません。 |
| Android の tombstone の層も `logcat` と同じ、確認の時点で収集する。独立した2つ目の `app_crash_tombstone()` メソッドへ分けない | `app_crash_artifacts()` 1つに tombstone も含め、`_finish_outcome` から同期的に呼ぶ | 却下しました。本項目の初期の草案はまさにこれを採っていましたが、上の確認時点の移動が閉じようとした競合そのものを再発させます。`adb root` は `adbd` を再起動し、レジデントサーバの `am instrument -w` セッションをシナリオの途中で終わらせます。同じシナリオにまだ残る `if`・`forEach` の outcome、`expect`、`after` ルールのいずれかがそのあと `BackendCrashError` を送出すれば、`run_scenario` はそれを失敗へ畳み込む代わりに意図的に再送出し、`RunResult` 全体——すでに収集した `logcat` の層と `app_crashed` を含みます——を捨ててしまいます。tombstone の取得が抱えるリスクは `logcat` のそれと非対称です。あとかたづけの `relaunch` は `app_launched_at` を常に*前へ*しか動かさないため、`pipeline.py` の本当の事後走査から読んでも、リスクは取り逃しにとどまり、`logcat`・`.ips` を先に確認時点へ動かした理由だった誤帰属にはなりません。tombstone の層だけが、もとの事後タイミングにとどまる利益を得ます。 |

## 進捗

> 作業の進行に合わせて最新の状態に保ってください。チェックリストは「どう実現するか」の
> MECE な作業分解をそのまま反映します（作業の単位ごとに1項目）。ログは変更内容とその日時を
> 古い順に記録し、PR にリンクします。

- [x] Unit 1 — `base.AppCrashedError`（新規ファイル）。`Driver` プロトコルとは別に設ける、
      `base.AppCrashSignal` というケイパビリティプロトコル（`app_crash_signal() -> str |
      None`）。新しい `StepOutcome.app_crashed: bool = False` フィールド。`ruff` の `TRY`
      系列は `TRY003` だけを無視して選択されているため、Unit 7 の送出・捕捉（後述）には
      `CLAUDE.md` のインラインコメント規則が求める理由つきの独自の `# noqa: TRY301` が
      要ります。`AppCrashedError` を `signal` から組み立てるただの文字列にせず、実際に
      送出する型のまま残すかどうかを決める箇所なので、ここに名指しておきます。
- [x] Unit 2 — iOS：`XCUIApplication.state` を読む新しい `openapi.yaml` のルートと、
      生成された `APIHandler` のメソッド。`Router.swift` ではなく `RunnerServer` から
      配信します。`XcuitestDriver.app_crash_signal()` が `AppCrashSignal` を実装し、
      `notRunning` をシグナルとして分類し、チャンネルエラーは `XcuitestRunnerCrashError`
      としてそのまま伝播させます。新しい `is_real_device` コンストラクタ引数を
      `device_os` と同じ方法で `make_driver` から通し、`deviceType: device` では
      `app_crash_signal()` がその場で `None` を返すようにします。本項目は Simulator だけを
      対象にします。
- [x] Unit 3 — iOS：`_spawn_cold` の中、`xcodebuild` の spawn の*直前*に記録する
      `XcuitestEnvironment.app_launched_at`。コールド起動を実際に行うのはランナー自身
      （`XCUIApplication.launch()`、`xcuitest_environment.py:323`）であり、この環境では
      ないため、目印を隣に記録できる Python 側の起動呼び出しがありません。
      `_spawn_cold_with_retry` が戻ったあとに記録すれば、それが指す起動よりも
      すでに遅れており、まさにその起動の最中にクラッシュしたアプリの `.ips` を拒んで
      しまいます。新しい `XcuitestEnvironment.relauncher()` オーバーライドが `device_relauncher` の
      `RelaunchFn` を包み、`relaunch` ステップ自身の起動の*前に*これを記録し直します
      （`_DeviceEnvironment` が継承する `relauncher()` には、それを更新すべき環境がその場に
      ない、まさにその呼び出し箇所）。対応する `XcuitestEnvironment.crawl_reset()`
      オーバーライドも同じ形でこれを3回目記録し直します。`crawl` 自身のフロンティア再訪ごとの
      relaunch の*前に*、`_DeviceEnvironment` が継承する `crawl_reset()` にも、それを更新
      すべき環境がその場にない、もう1つの呼び出し箇所です。`_resume_warm`
      （BE-0291 のリースをまたぐウォーム再利用の起動、`xcuitest_environment.py:855-856`。
      `start()` が再利用可能なたびに戻る、同じ長命の `XcuitestEnvironment` インスタンスの
      上で走ります）自身の中でも、その `e.launch` の*直前*に4回目記録し直します。そうしなければ、
      ウォーム再利用されたリースのクラッシュが、目印がたまたま古いタイムスタンプを共有する
      どこか前のリースの `.ips` レポートと一致してしまいかねません。新しい `self._app_path`
      フィールドを `start()` で `self._bundle_id` の隣に保存します（`ios.app_path`、
      `self._bundle_id` がすでに読んでいるのと同じ `ios` からです）。`app_crash_artifacts()`
      は引数を取らないため、これが値へ届く唯一の経路です。掃引自身の照合パターンのために
      `Path(self._app_path) / "Info.plist"` から `CFBundleExecutable` を読みます
      （`ios.bundle_id` はこの名前ではありません）。`app_crash_artifacts()` の、名前と `udid` に
      よる `.ips` 掃引。候補となる各レポートをパスでもヘッダでもなく*ペイロード*まで
      読んで確認します
      （レポートのファイル名にもヘッダにも udid は現れず、`XCUIApplication` には PID を
      読む手段もありません）。
      `ReportCrash` の非同期な書き込みに対する上限つきの待機を含み、失敗はすべて `[]` へ
      解決するよう包みます。
- [x] Unit 4 — Android：`backends.make_driver` から `AdbDriver.__init__` へ、`fetch_clock` と
      `act` と同じ方法で通す `package` キーワード。同じ方法で通す `api_level: int | None =
      None` キーワードも加えます。`AndroidEnvironment.start()` の中で `adb shell getprop
      ro.build.version.sdk` を一度読み、`self._package` の隣に保存します。このコードベースは
      今日、端末の API レベルをどこでも追跡していないためです。`app_crash_signal()` は
      `package is None` か、`launched_at` が未設定または `None` を返す場合か、
      `api_level is None or api_level < 30` を最初に確認し、その場で `None` へ
      解決します。パッケージなしの `dumpsys activity exit-info` は端末上のあらゆる
      パッケージを報告してしまうため、黙った `None` の既定値は別のプロセスのクラッシュを
      確定させかねません。`ApplicationExitInfo` 自体が API 30 より前には存在しないため、
      古い端末やエミュレータイメージでポーリングすれば、同じ名前のついた形でフェイル
      クローズするのではなく、あらゆるクラッシュでタイムアウトするだけになります。
      `pidof` と `dumpsys activity exit-info` の両方の呼び出しを `except
      (subprocess.CalledProcessError, OSError)` で包みます。`AdbDriver._rooted()` が
      `adb shell id -u` の周りにすでに持つのと同じ形です（`adb_driver.py:1234-1235`）。
      `real_run` の既定の `RunFn` は `check=True` であり、toybox の `pidof` は一致が
      ないとき、この確認がまさに観測しようとしているふだんの想定どおりのケースで、
      空の `stdout` を返す代わりに終了コード 1 を返すためです。`stdout` が空のまま
      終了する `pidof` は「プロセスなし」として読み、それ以外の失敗は `None` へ解決
      します。
      `launched_at: Callable[[], tuple[float, str] | None] | None = None` という注入された
      コールバック。`AndroidEnvironment` の起動の目印を、エポックだけでなく Unit 5 自身の
      組み合わせ読み取りが作る exit-info 用の表記も含めてその場で読みます。下の
      exit-info の比較には表記が要り、`float` ではそれを運べないためです。
      `adb shell pidof <package>` による
      `AdbDriver.app_crash_signal()` を、時刻で絞り込んだ `adb shell dumpsys activity
      exit-info <package>`（最新の項目のみ。その `timestamp=` フィールド——端末のタイムゾーンによる
      ウォールクロックの表記であり、エポックではありません——を、`launched_at()`
      タプルの表記側と比較します。エポック側とは比較しません。`timestamp=` を
      ホスト側でエポックへ解決すれば、ホスト自身のタイムゾーンを通ってしまい、この設計がそもそも
      避けているはずの時計のすり合わせを呼び戻してしまいます）で裏付けます。iOS の
      `.ips` 掃引と同じ短い上限つきの方法でポーリングします。一度だけ読むのではありません。
      `ApplicationExitInfo` は `system_server` がその死を回収したあとにしか記録されない
      からです——ただし、上限まで待つのは最初のポーリングだけです。新しい
      `self._exit_info_exhausted: bool = False` インスタンスフィールド（既存の
      `_act_warned`・`_act_unavailable` という2つのラッチと同じ形）を、上限つき
      ポーリングが一致なしで終わった最初の時点で立て、あとの確認では
      履歴を一度だけ読んで即座に答えるようにします。`run` ではこのリセットは無償で
      得られます。`AndroidEnvironment.has_reusable_resident()` は無条件に `False` を
      返すため（BE-0291 のリースをまたぐ再利用は XCUITest だけの仕組みです）、この
      バックエンドではリースごとに新しい `AdbDriver` が組み立てられるからです。しかし
      `crawl` は巡回全体を通じて1つの `AdbDriver` を使い続けるため
      （`crawl/cli.py:283-300`）、この専用のリセットが必要です。
      `invalidate_settled_cache()` への折り込みではありません。あのメソッドの契約は
      「画面が変わった」であり、`AdbDriver` はふつうのアクチュエータ（`_act`・
      `_device_act`・`type_text`）からもすでにこれを呼んでいるため、折り込めば、この
      ラッチが抑えようとしているほぼすべての確認の手前でフラグを解除してしまいます。
      代わりに、2つ目の狭い専用メソッド（`reset_exit_info_poll()`。隣の
      `SettledCacheInvalidator` と同じ形です）を、`AndroidEnvironment.relauncher()` の
      `relaunch()` クロージャで `e.launch(...)` の直後に
      （`android_environment.py:339-351`）、`crawl_reset()` の `reset()` クロージャで
      自身の `e.launch(...)` の直後に呼びます。これらは、ポーリングの上限を再び払う
      価値が生まれる、文字どおり新しい起動の瞬間です。`AdbDriver` 自身のアクチュエータの
      中からは決して呼びません。
- [x] Unit 5 — Android：各起動の箇所の `e.launch(...)` 呼び出しの*直前*に記録する
      `AndroidEnvironment.app_launched_at`。あとではありません。`e.launch` は起動の完了を
      待つ `am start -W` であり、返ったあとに立てた目印はすでに起動時のクラッシュを
      取り逃しています。
      `adb shell "date '+%s|%Y-%m-%d %H:%M:%S|%m-%d %H:%M:%S.000'"` という**1回**の組み合わせ
      読み取りから得ます——この書式は*端末*側のシェルのために引用符で囲んでおり、そうしなければ
      `|` がパイプとして読まれ、残りが単語分割されてしまいます。そのパイプ区切りの3つのフィールドをホスト側で分割します
      （プレーンな文字列分割であり、タイムゾーンの解決ではありません）——エポック
      （`app_launched_at` 自身）、exit-info ポーリング向けの表記（Unit 4）、
      `logcat -t` 向けの表記です。3回別々の `date` 呼び出しからではなく、まとめて
      保存します。3回に分ければ3つの異なる瞬間を刻んでしまい、（もし最後に読めば）
      `logcat` のフィールドが読み取りの間隙に着地したクラッシュをフィルタで除いて
      しまいかねません。
      `logcat -t` は整数の引数を行数として読み、時刻の境界としては読まないため、
      エポック値をそのまま通すことはできません。`dumpsys activity exit-info` 自身の
      `timestamp=` もまた別の表記です（端末のタイムゾーンによるもので、`logcat` の形式には
      ない年を持ちます）。どちらも互いの代わりにはなりません。新しい `self._package` フィールドを
      `start()` で両方の隣に保存します
      （`android.package`。起動の目印がすでに読んでいるのと同じ
      `android = require_android(eff)` からです）。`app_crash_artifacts()` は引数を取らない
      ため、これが自身の `logcat` プロセス絞り込みに必要な値へ届く唯一の経路です。
      `app_crash_artifacts()` の、常に試みる `logcat` 抽出（マネージドコードと
      ネイティブの両形式）は、クラッシュバッファをクリアする代わりに `logcat` 用に
      表記した目印を `-t` の時刻フィルタとして使い、
      `scripts/collect_android_diagnostics.sh` 自身のジョブ終了時
      掃引がそれ以前の内容を引き続き見られるようにします。`self._package`
      にも絞り込みます（マネージドのブロックなら `Process: <package>` 行、ネイティブなら
      `>>> <process> <<<` 見出し）。このバッファは起動だけでなくプロセスもまたいで端末全体で
      共有されるからです。exit-info のポーリングと同じ理由で、その場の1回の `-d` スナップ
      ショットを信用せず、同じ短い上限つきの方法で取り直します。`crash_dump` が、
      `pidof` がすでに報告した死のあとにネイティブのブロックを書き込むからです。
      ベストエフォートで root 権限に依存する tombstone 取得は、独立した
      `app_crash_tombstone()` メソッドにします。`adb root` がレジデントサーバの
      `am instrument -w` セッションと BE-0283 の `adb reverse` トンネルをまるごと
      終わらせることを受け入れます。再確立はしません。このリースのこれ以降の処理は
      どちらのチャネルも必要とせず、プールが次のリースで両方を一から組み立て直すからです。
      `app_crash_artifacts()` からも `_finish_outcome` の呼び出し箇所からも外します。
      `adb root` をシナリオの途中で発火させれば、「Android：`logcat` のクラッシュ用
      バッファをまず読み、root 権限があるときだけ tombstone を取得する」節が述べる、
      エスケープする `BackendCrashError` の危うさを招くからです。取得が終わったあと、
      同じベストエフォートのラッパーの内側で `adb unroot`（と2回目の
      `adb wait-for-device`）を対にします。`adb root` の権限レベルはこのリポジトリの
      他のどこにも戻す処理がなく、このリースを越えて後続の無関係なシナリオ自身の
      `install`・`pm clear`・`force_stop`・`launch` の各呼び出しへ漏れ出さないようにする
      ためです。`crawl` は、この層を
      自身の長寿命レーンから外すのに別立てのフラグを必要としません。`app_crash_tombstone()`
      を一度も呼ばないだけで足ります（Unit 10）。それぞれ独立して失敗を `[]` へ解決する
      よう包みます。
- [x] Unit 6 — `RunEnvironment.app_crash_artifacts()` と `RunEnvironment.app_crash_tombstone()`
      のプロトコルの形（どちらも `list[tuple[str, bytes]]` を返します）。`WebEnvironment`と
      `_DeviceEnvironment`（`FakeEnvironment` が継承）に、`app_crash_artifacts()` の1行の
      `return []`、この2クラスに `app_crash_tombstone()` の1行の `return []`。
      `take_crash_snapshot()` が `WebEnvironment`・`AndroidEnvironment`・`_DeviceEnvironment`
      にすでに持つ no-op 宣言と同じ形です。`AndroidEnvironment` はどちらのメソッドにも
      no-op を持ちません。`app_crash_artifacts()`（Unit 5 の `logcat` の層）と
      `app_crash_tombstone()`（Unit 5 の tombstone の層）の両方を本物の収集で
      オーバーライドしているからです。`XcuitestEnvironment` も `app_crash_artifacts()` には
      no-op を持ちません。それだけを自前の本物の収集（Unit 3）でオーバーライドし、
      `app_crash_tombstone()` は `_DeviceEnvironment` の no-op をそのまま継承します。
      `Lease.app_crash_artifacts` と `Lease.app_crash_tombstone` の両方を、
      `pool.py` の `lease()` クロージャを通して `crash_artifacts` の隣へ配線します。
      このさきへ通るのは `Lease.app_crash_artifacts` だけです。`Lease.relaunch` がすでに
      `relaunch` へ通っているのと同じ方法で `_LoopConfig.capture_app_crash` へ通し、
      `pipeline.py` からではなくステップループの内側から呼ぶことで、同じシナリオの
      あとかたづけが `app_launched_at` を動かすより前に掃引を終わらせます（Unit 7、
      「iOS：`.ips` レポートの照合」を参照）。`lz.app_crash_tombstone` 自体は
      `pipeline.py`（Unit 8）から直接呼び、`_LoopConfig` へは一切通しません。
- [x] Unit 7 — `run_scenario` / `_step_runner.py`：新しい `_finish_outcome` ヘルパーを、
      `self.state.outcomes.append(outcome)` の5つの呼び出し箇所すべて（`_handle_if` /
      `_handle_for_each` / `_handle_web` はそれぞれ1回、`_handle_action` は自身の終端と
      `UncoveredSystemAlertLocale` の早期リターンの2回）で、素の append の代わりに呼ぶよう
      にし、あらゆる種類のステップの本当の最終結果を覆います。シナリオスコープのオブジェクト
      （`run_scenario` が1回だけ作り、`live_bindings` と同じ方法であらゆる `run_phase`
      呼び出しに共有し、凍結された `_LoopConfig` ではなく `bindings` の隣、
      `StepLoopState` に置くことで `before`・本体ステップ・発火するあらゆる `after` の
      規則をまたいで生き残る）が、3つのラッチを
      運びます。1つは意図的終了フラグであり、`outcome.action == "relaunch"` かつ
      `outcome.ok is False` を見た瞬間（確認より前に）立ち、それ以降の同じシナリオの
      あらゆる確認を抑えます。抑える範囲は `relaunch` ステップ自身の outcome だけには
      とどまらず、それを包む `if`・`forEach` の outcome や、発火する `after: on: error`
      の後片付けも含みます。もう1つは起動未確認フラグであり、新しい
      `readiness: ReadinessResult | None = None` 引数（`Lease(...)` のコンストラクタ、
      `pool.py:563`、で `sink=sink` と並べて設定する新しい `Lease.readiness` フィールドから
      注入。値は同じクロージャの手前で `launch_driver` がすでに返しており、`pool.py:408`、
      今日は `FileSink` の起動待ちタイムアウト診断にしか届いていません——足りないのは
      `Lease` 側の写しであって、値の最初の取得ではありません）から構築時に一度だけ
      立ち、`readiness is None or not readiness.ready or readiness.signal == "count"` のとき
      真になります——素朴な要素数判定（`readiness.py:151-152`）は SpringBoard とアプリ自身を
      見分けられないため、その判定による `ready` という答えもまた、アプリがフォアグラウンドへ
      来た証拠にはならず、`readyWhen` を宣言しないターゲットにとっては（`launch_driver` が
      `id_namespaces` を渡さないため `namespace` は一度も成立せず）これが*ふつう*の判定でも
      あります——シナリオの
      最初に失敗するステップには、アプリが動作していると確認する手前のステップが存在せず、
      フォアグラウンドへ一度も到達しなかったアプリが、そのままでは確定したクラッシュとして
      読まれてしまうという抜け穴を塞ぎますが、意図的終了フラグが使う、シナリオの残り全体を
      無条件に抑え続ける形は使いません——そうすると、そのクラスのターゲットすべてに対して
      以降のあらゆる確認まで黙らせてしまうからです。`_finish_outcome` は代わりに、その成功が
      アプリの応答を要求した、確定した outcome を見た時点でこのフラグを解除します——
      `if`・`forEach` だけを除外するのではなく、肯定的に条件づけます。`if`・`forEach`
      自身の、その本体を包む outcome は除きます。空の `else` 分岐やゼロ件の
      一致でも `ok=True` は確定してしまい、その先のアプリが問い合わせに答えなくても
      送出しないため、死んだアプリ自身の SpringBoard だけのツリーが、まさにそうあっては
      ならない理由でこのフラグを解除してしまいます。アプリにまったく届かない種類のステップ
      （`http`・`generate`・`totp`・`email`・`push`）でも同じことが起こります。`relaunch`
      についても同様です。ふつうの `_handle_action` の経路を通って配信されるため、単に
      「アクチュエーションのステップ」という規則なら数えてしまいますが、そのクロージャは
      `await_ready` の `ReadinessResult` を握りつぶし、`await_ready` 自体は例外を送出しない
      ため、確定した `relaunch` の `ok=True` はアプリについて何も語りません。この Unit は
      `relaunch` を明示的に除外し、確定した `relaunch` に対してこのフラグを立て直します
      ——アプリを、確認できない状態へちょうど戻したばかりだからです。本物の
      アクチュエーション（`relaunch` を除きます）・`wait`・
      `assert` のステップだけが供給する、その肯定的な観測をもってです。この Unit では `ReadinessResult` 自身の
      ドキュメント文字列（`protocols/readiness_result.py:15-16`、`:27`）も更新します。現在は
      「Pure diagnosis: it never enters a verdict（判定材料でしかなく、それ自体が判定を
      下すことはない。prime directive 1）」と述べており、このフラグより前のすべての利用者に
      ついては真でした（いずれも起動待ちタイムアウトの診断表示に使うだけでした）。この
      フラグは振る舞いを決める初めての利用者になるため、不変条件が古びる前に、この新しい
      用途をドキュメント文字列へ書き加えます。もう1つは確定済みクラッシュのラッチであり、`_finish_outcome`
      が `AppCrashedError` を送出し捕まえた最初の時点で立ち、同じ伝播の中であとに続く
      outcome が、そのすでにわかっているシグナルを自身の `outcome.reason` へ折り込む
      だけにする——`app_crash_signal()` の確認を relaunch・起動未確認・確定済みクラッシュの
      ケースに限って抑えるものであり、クラッシュを伴わないふつうの失敗は確定する outcome ごとに
      1回の確認を払い続けます。その1点で `AppCrashedError` を送出し捕まえます——理由つきの
      独自の `# noqa: TRY301`（Unit 1）を持ちます——そのメッセージを
      `outcome.reason` へ折り込み、新しい `StepOutcome.app_crashed` フィールドと確定済み
      クラッシュのラッチを立てます。同じ catch の中で、`self.cfg.capture_app_crash` が
      設定されていれば同期的に呼び、結果を新しい `StepOutcome.app_crash_artifacts`
      フィールドに保存します——あとではなく確認のその瞬間にです。同じシナリオの
      あとかたづけステップが `app_launched_at` を掃引の足元から動かせないようにする
      ためです。あとに続く outcome は、すでにラッチされたシグナルを `reason` へ折り込む
      だけにとどめ、自身の `app_crashed` はデータクラスの既定値のままにし、
      `capture_app_crash` を二度と呼びません。入れ子になったクラッシュの、外側を包む
      `if`・`forEach` の outcome が、`pipeline.py` のあとの走査で唯一の確定した outcome と
      競合しないようにするためです。あらゆる種類の失敗するステップをループへ実際に通し、確定するあらゆる
      outcome が `_finish_outcome` を通ったことを確認する、振る舞いベースの高速
      スイートのテストを加えます（`self.state.outcomes.append` という文字列を
      検索するのではありません）。
- [x] Unit 8 — `pipeline.py`：`_run_on_lease` が `run_scenario` の直後、まだ同じリースを
      保持したまま `(*result.before_outcomes, *result.steps, *result.after_outcomes)` を
      `app_crashed` で走査します（`result.steps[-1]` ではありません）。`_write_crash_artifacts`
      （BE-0421、`pipeline.py:803`）を真似た新しい
      `_write_app_crash_artifacts(lz, outcome, s, sid)`
      が、見つけた outcome 自身の `app_crash_artifacts`（Unit 7 が確認の時点ですでに収集
      済みで、ここで再び掃引するのではありません）から始め、条件なしに
      `lz.app_crash_tombstone()` で拡張します——ここで初めて、本当に事後に呼びます。
      Android 以外では他の3つが no-op を宣言しているため空のままです（Unit 6）——
      組み合わさったリストをマスキングを行う `writer.write_text` の経路で書き込み、
      ディレクトリを名指しする一節を `result.failure` へ追記します。
      `lz.app_crash_tombstone()` 自身の失敗はログに記録するだけで、
      すでに outcome にある `logcat` の層を巻き添えにしません。
      `bajutsu/common/report/manifest.py` の `_scenario_dict` が、既存の `wall_offset_s` の
      pop の隣で、`steps`・`before_outcomes`・`after_outcomes` それぞれの outcome の辞書から
      `app_crash_artifacts` を pop し、本ユニットが `StepOutcome` に加える生の `bytes` が
      `json.dumps` に届かないようにします。`report/load.py` は、`_kw` 自身への変更は
      要りません。既存の欠落フィールド処理がすでにその項目を既定値で組み立て直すため
      です。ただし `load.py:34-41` 自身のコメントは同じ変更の中で更新し、
      `app_crash_artifacts` を `wall_offset_s` と並べて、2つ目の意図的な往復の例外として
      名指します。このコメントは現在「*唯一の*意図的な例外」と読めるため、そのままでは
      本項目自身のこの pop を、あとから見落としとして「直させて」しまいかねません。
- [x] Unit 9 — `TracingDriver`：`base.AppCrashSignal` を `_PROTOCOLS` へ加え、
      `--trace-driver` がそれを実装したドライバに対してだけ実属性として設置するようにします。
- [x] Unit 10 — `crawl` 自身の統合。`_build_lane` のレーンごとの `app_crash_artifacts`
      （`app_crash_tombstone` は決して通しません。この第2の呼び出し可能オブジェクトを
      どこへも通さないことだけで、Android の tombstone 取得の層を巡回全体で止められ、
      別立てのフラグは要りません（Unit 5））を、
      `driver`・`reset` と同じ方法で `WorkerFactory` と `crawl()` の主レーン向けパラメータへ
      通します。収集呼び出しを `record_crash` の既存のロック外クラッシュ確認へ加えます。
      ドライバが事象を積極的に確認したとき（`isinstance`/`app_crash_signal()`）にだけ収集する
      ようゲートし、UI ツリーの誤検知が全タイムアウト分の掃引を払わないようにします。自前の
      `except (base.BackendCrashError, OSError, subprocess.CalledProcessError): pass` で包み、
      `run` と違って `crawl` には伝播した
      `XcuitestRunnerCrashError` を受け持つ回復経路がなく、この呼び出しは `_walk` 自身の
      `try` の外にあります。この広い捕捉は Unit 4 自身の `app_crash_signal()` への修正に
      並ぶ2つ目のガードです。`backend_cli/adb/` の下には `BackendCrashError` を送出する
      場所がなく、その `RunFn` は `subprocess.run(..., check=True)` だからです。`Crash` の
      新しい `artifacts` フィールド(生の `bytes` には JSON 表現がないため `serialize.py` の
      `screenmap_dict`/`screenmap_from_dict` の往復からはあえて除き、引き継がれる `Crash` は
      常に `artifacts=()` で組み立て直されます)。`repro.py` の `write_repros` が、空でない証跡を、
      `run` 自身のコピーが使うのと同じマスキングを行う `writer.write_text` 経路を通じて、
      再現できないクラッシュをスキップする自身の `continue` より前で
      `crashes/crash-NNN/app-crash/` へ、そのクラッシュ自身の `crashes/crash-NNN.yaml`
      再現ファイルの隣に書き込みます。
- [x] Unit 11 — showcase の準備。ビルド構成ではなく起動時環境変数のフラグで隠す
      「強制的にクラッシュさせる」操作を iOS（SwiftUI）と Android（Compose）それぞれに用意し、
      `preconditions.launchEnv` を通じてそれを起動する各プラットフォーム1本のシナリオ——
      クラッシュのトリガーで終わらせず、死んだアプリに対してもう1ステップを置きます。
      最後のステップがクラッシュのトリガーであるシナリオは、本項目自身の反応的な確認
      設計（「検知の方式」を参照）によりそもそも失敗しないからです——を、
      `ios-e2e.yml` / `android-e2e.yml` へのゲートしない PR ごとのシグナルとして配線します。
- [x] Unit 12 — ドキュメント。`docs/evidence.md`（および `docs/ja/`）にこの証跡の種類を追加します。
      `docs/ci.md`（および `docs/ja/`）に showcase のシグナルレーンを追記します。
      `docs/architecture.md`（および `docs/ja/`）に、既存のバックエンドクラッシュのリトライ
      節と、この項目のリトライなしの経路を相互参照させます。
- [x] Unit 13 — テスト。起動未確認フラグ——Unit 7 が加える3つ目のラッチであり、本項目が
      持ち込む新しいロジックの中でもっとも状態を持ち、それ以外では検証がまったくない
      ——のための3本の固定テスト。「アプリが一度もフォアグラウンドへ来ていないので確認
      しない」と「最初に失敗するステップで確定させる」（このフラグが取り除こうとしている
      誤診断そのもの）を見分けるテストも、その逆の失敗（`count` 判定のターゲットのクラス
      全体に対してシナリオを通してフラグが立ちっぱなしになり、本項目が無効化されてしまう
      こと）を捉えるテストも、なければ本項目は無検証で出荷されてしまいます。最初のステップ
      が失敗するシナリオを `readiness.signal == "count"` のリースで走らせ、確認が一度も
      走らず `app_crashed` も立たないことを確認するテスト。同じ形を `readiness.signal ==
      "readyWhen"` のリースで走らせ、確認が*実際に*走ることを確認するテスト。そして、
      一致しない `if` と `http` ステップはどちらもフラグを立てたままにする一方、成功した
      `tap` はフラグを解除し、確定した `relaunch` はフラグを立て直すことを確認する、解除
      経路のテスト。両バックエンドで、ふつうの `ElementNotFound` や `wait`・`assert` の
      失敗、そして `app.state` の答えに関わらず `deviceType: device` に対して
      `app_crash_signal()` が `None` を返すこと（誤検知しないこと）。`package=None` または
      未設定の `launched_at` で組み立てた `AdbDriver` が即座に `None` を返し、`pidof` にも
      `exit-info` にもまったく届かないこと（別のプロセスのクラッシュを確定させません）。
      クラッシュしたステップのあとに `after: on: error` の `relaunch` が続くシナリオでも、
      確定したクラッシュ自身の `.ips`/`logcat` レポートがそのまま添付されること——あと
      かたづけ自身の `relauncher()` の再記録のあとに生きたまま読み直せば得られたはずの
      空の結果ではないことです。`app_crash_artifacts()` が `_finish_outcome` の内側、
      確認の時点で一度だけ収集され、`pipeline.py` から再び掃引されるのではなく outcome
      に載って運ばれることを固定します。
      `relaunch` ステップ
      自身の起動を過ぎて、`crawl` が駆動する `crawl_reset()` 自身の起動を過ぎて、また
      `_resume_warm` のクロスリース再利用自身の起動を過ぎても
      `XcuitestEnvironment.app_launched_at` が進むこと、それぞれの `.ips` 掃引が、
      もっとも新しいその起動のレポートだけを見つけ、以前のシナリオやクラッシュのもの
      ではないこと。偽の `AndroidEnvironment.app_crash_tombstone()` が `_finish_outcome`
      からもステップループのどこからも一度も呼ばれないこと——`_LoopConfig`・
      `StepLoopState` へのソースレベルの確認ではなく、その偽の実装が受け取ったあらゆる
      呼び出しを記録する形で振る舞いとして確認します。`capture_app_crash` は不透明な
      `Callable` であり、`_LoopConfig` も `StepLoopState` も、それがどの環境メソッドに
      由来するかを記録しないためです——tombstone の層の呼び出し箇所が
      `pipeline.py` の事後走査だけにとどまることを固定します。その走査自身の
      `_write_app_crash_artifacts` が、クラッシュした `RunResult` ごとに
      `lz.app_crash_tombstone()` をちょうど1回呼び、outcome の `app_crash_artifacts`
      単独ではなく `logcat` と tombstone を組み合わせたリストを書き込むこと。
      クロールレーンの `AndroidEnvironment` は、クラッシュを確定しても
      root 権限に依存する tombstone 取得をまったく試みないこと（`logcat` の層は走る）。
      理由は `crawl` が `app_crash_tombstone()` を一度も呼ばないからです。
      `pipeline.py` の事後走査を経由して `run` でリースされた方は引き続き試みること。`crawl` 自身のゲート内で
      `d.app_crash_signal()` が送出する `BackendCrashError` が外へ漏れないこと。`Crash` は
      `artifacts` なしで記録され続け、巡回は続くこと。`appPath` を設定していないターゲットで
      `app_crash_artifacts()` がその場で `[]` へ解決し、`Info.plist` の読み取りにまったく
      届かないこと。再現できないクラッシュに対して `write_repros` が
      `crashes/crash-NNN/app-crash/` を書き込むこと——証跡の書き込みがループ自身の
      `continue` より前にあることと、`write_bytes` ではなく `write_text` を通してマスキング
      済みで書き込まれることの両方を固定します——スキップされた再現ファイルが持つはずだった
      その同じ `NNN` の下にです。失敗した
      `relaunch` ステップ自身が `app_crash_signal()` をまったく確認しないこと、それを包む
      `if`・`forEach` の outcome も、終了させられたアプリに対して失敗する
      `after: on: error` のステップも同様であること。割り込みの回復ステップ
      （`after` フェーズとは別物の、BE-0314 の `_run_recovery`）が終了させられた
      アプリに対して失敗する場合も、回復ステップごとに1回確認し、どちらのラッチも
      それを抑えないこと。3段の入れ子のふつうの（`relaunch`
      でも確定済みクラッシュでもない）失敗が、確定する outcome ごとに1回の確認を払い
      続け、ラッチがこのケースを抑えないことを固定するテスト。スタブしたディレクトリと
      スタブした `adb` の出力に対する、iOS の `.ips` 掃引と Android の `logcat`・
      tombstone 収集（Android の exit-info による裏付けを含みます）。スタブした `adb` の
      連続に対して、`app_crash_tombstone()` が pull の成功後に `adb unroot`（と2回目の
      `adb wait-for-device`）を呼ぶこと、そして pull 自体が失敗した場合でも同じく呼ぶことを
      固定するテスト。`adb root` の権限レベルが、それを要求した1回のリースを越えて
      持続しないことを固定します。スタブした `adb` の連続を用意し、`unroot` のあとの
      `id -u` がそれでも `0` を答える——`unroot` が拒まれたケース——場合に、いま求められる
      ログを大きく記録することを固定するテスト。`unroot` が*呼ばれた*ことだけを固定すれば、
      試みたのに効かなかった復元を見逃してしまいます。起動の目印の時間窓には
      収まるものの、別のパッケージの `FATAL EXCEPTION`/`>>> <process> <<<` ブロックしか
      運ばない `logcat` ダンプが何も抽出しないこと。プロセスによる絞り込みが実在し、
      時刻によるものだけではないことを固定します。スタブした exit-info の連続と、
      スタブした `logcat` ダンプの連続を用意し、どちらも1回目の読み取りでは空/不一致を
      返し、上限内の後続の読み取りで初めて一致する項目を返すようにして、
      `app_crash_signal()` と `app_crash_artifacts()` の両方が1回きりの読み取りを
      信用せずポーリングすること、そして一致する項目が一度も現れない場合は上限自体が
      満了することを確認します。起動時クラッシュを模したスタブ列——`e.launch` の前に立てた
      目印と、`e.launch` のあとに立てたはずの目印のあいだにタイムスタンプを持つ `logcat`・
      exit-info の項目——を用意し、Android の目印が `e.launch` の*前*に立つこと（あとでは
      ないこと）を確認します。これは、Unit 11 自身のタップで引き起こすフィクスチャでは
      実機上で検証できない唯一のケースであり、ここで固定しなければ、before/after の決定は
      どちらのレーンでも未検証のまま出荷されてしまいます。事後確認が経路の内側に
      とどまること、`app_crashed` フィールドを立てること、入れ子になった `if`・`forEach`
      の失敗をまたいで確定済みクラッシュのラッチが保たれることを検証する
      `_step_runner.py` のテスト——同じ入れ子のクラッシュのあとに `after: on: error`
      ステップも失敗させ、`app_crashed` が確定した outcome ただ1つだけで `True` になり、
      それを包む `if`・`forEach` の outcome にも `after` のステップにも立たないことも
      固定します。`pipeline.py` のあとの走査が複数の候補から選ぶ必要が決して生じない
      ようにするためです。`app-crash/` がマスキング済みのテキストを保持すること、
      `steps` だけでなく `before_outcomes`・`after_outcomes` の中のクラッシュも走査が
      見つけること、パイプラインレベルのクラッシュリトライが起きないことを検証する
      `pipeline.py` のテスト。`await_ready` をスタブしてタイムアウトさせ、新しい起動が
      ready にならなかった `relaunch` ステップ自身は `ok=True` を報告すること(除外自身の
      前提が成り立つことの検証)、そしてそのクラッシュは死んだアプリに対する次のステップで
      `app_crashed=True` として捕まることを検証するテスト。web backend・fake backend で
      `isinstance` が `False` を返し、何も変わらないことを検証するテスト
      （web ブロックではなく、単体の Playwright/fake ターゲット）。XCUITest/adb
      バックエンドでの `_handle_web` のテスト。内側の web ステップ自身の失敗
      （`active_driver = web_driver`）はまったく確認しないが、それを包む `web`
      ステップ自身の失敗（`active_driver` はネイティブのドライバ）は確認すること。
      シナリオの*最後の*ステップがクラッシュしても `ok=True` を報告し、`expect` も
      非失敗トリガーの `after` ルールも宣言しない場合、`app_crashed` outcome も
      `app-crash/` 証跡も持たずにグリーンのまま終わること——文書化した死角をバグとして
      再発見される前に固定するテスト。同じ形でシナリオレベルの `expect` を宣言した
      場合は、代わりに `expect:` のアサーション不一致で赤く失敗すること——未分類のまま、
      `app_crashed` も `app-crash/` もないこと——を固定するテスト。グリーンになる死角とは
      別物であり、その変種ではないことを確認します。同じ形にさらに、死んだアプリに
      対しても失敗する `on: always` の `after` ルールを加えた場合は、こちらは結局
      分類され、`app_crashed=True` が立ち `app-crash/` が書き込まれることを固定する
      テスト。そのあとかたづけステップ自身の outcome が、ふつうの
      `_dispatch_after`・`run_phase` の経路を通って `_finish_outcome` に届くからです。
      `manifest.py` のテスト。`steps`・`before_outcomes`・`after_outcomes` それぞれの
      outcome に `StepOutcome.app_crash_artifacts` を持たせたクラッシュ済み `RunResult` を
      `manifest_dict` と `default=` のない素の `json.dumps` へ往復させ、何も送出しないこと
      ——本ユニット自身の `_scenario_dict` 除外が防ぐ回帰そのものです——を固定します。
      `report/load.py` が同じ `RunResult` を、影響を受けたすべての outcome で
      `app_crash_artifacts` を `()` の既定値に戻し、`app_crashed` と `reason` はそのまま
      保って組み立て直すことも固定します。

ログ：

- [#2012](https://github.com/bajutsu-e2e/bajutsu/pull/2012) — Unit 1〜13。項目全体を出荷しました。
  出荷した範囲は、ステップループ内でのインバンドな分類とそのシナリオスコープの3つのラッチ、iOS の
  `.ips` スイープ、Android の `logcat`・exit-info・tombstone シグナル、pipeline の書き出しと
  manifest からの除外、`crawl` との統合、showcase のフィクスチャとそのゲートしない CI レーン、
  二言語のドキュメントです。この PR を開く前の3ラウンドのセルフレビューでは、Android のパースに
  関するバグを2件見つけました。1件は実際の `dumpsys` 出力と一致しないフィールド順序の想定、もう
  1件は隣接プロセスのクラッシュを取り込みかねない `logcat` クラッシュブロックの一致範囲です。
  加えて、プロトコルの isinstance に関する不備も見つけました。`TracingDriver` を通しても
  exit-info ポーリングのリセットが機能し続けるよう、`AppCrashPollResettable` を追加して直して
  います。詳しい経緯は PR 本文を参照してください。
- [#2012](https://github.com/bajutsu-e2e/bajutsu/pull/2012) — iOS の証跡の代替経路。CI の
  `app-crash (xcuitest)` ジョブは、毎回クラッシュを分類できていたのに、レポートを一度も取得できて
  いませんでした。ジョブに追加した一覧ステップで、原因がわかりました。GitHub がホストする macOS
  ランナーでは、`ReportCrash` がシミュレータのアプリについて `.ips` を一切書きません。一覧ステップは
  2つの `DiagnosticReports` と、デバイス自身の CoreSimulator のログとデータのディレクトリを調べました。
  `.ips` の掃引で何も見つからないとき、`XcuitestEnvironment` は代わりに `unified-log.txt` を添付する
  ようになりました。中身は、アプリの起動以降についてシミュレータ自身が記録したユニファイドログです。
  `simctl spawn … log show` でアプリのプロセスと、バンドル ID を含む行に絞り、末尾 256 KB に制限します。
  xcresult を使わなかったのは、`xcodebuild` が終了して初めて作られ、クラッシュの確定よりずっと後に
  なるからです。

## 参考

- [BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact-ja.md)
  （実装済み、PR [#1999](https://github.com/bajutsu-e2e/bajutsu/pull/1999)） —
  本項目が補完し、直接再利用するランナー自身のクラッシュレポート収集。再利用先は
  [`xcuitest/_functions.py`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/_functions.py)
  の `_reports_since()` と、`pipeline.py` の
  `_write_crash_artifacts()`（`_CRASH_DIAGNOSTICS_DIR`）
- [BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md) —
  本項目の `crawl` 統合が土台とする、クロールの `Crash` レコードと `is_app_alive` の
  ヒューリスティック
- [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md) —
  本項目のリトライなしという設計が意図的に外れている、既存のバックエンドクラッシュのリトライの
  仕組み
- [BE-0066](../BE-0066-web-crawl/BE-0066-web-crawl-ja.md) — web（Playwright）バックエンド。
  この事象に対する独自のシグナル（レンダラーの `crash` イベントや、未捕捉の `pageerror`）は、
  後続の項目に委ねます
- [`bajutsu/common/drivers/base/backend_crash_error.py`](../../bajutsu/common/drivers/base/backend_crash_error.py) —
  `BackendCrashError`。本項目の `AppCrashedError` が基底クラスを共有しない、隣接する不具合
- [`bajutsu/common/drivers/base/interruption_policy_target.py`](../../bajutsu/common/drivers/base/interruption_policy_target.py) —
  本項目の `AppCrashSignal` が踏襲する、狭い opt-in のケイパビリティプロトコルという形
- [`bajutsu/common/drivers/tracing.py`](../../bajutsu/common/drivers/tracing.py) —
  `TracingDriver`。本項目の `AppCrashSignal` が加わる `_PROTOCOLS` タプルを持ちます
- [`bajutsu/common/orchestrator/loop/_step_runner.py`](../../bajutsu/common/orchestrator/loop/_step_runner.py) —
  ステップごとのループ。その4つのステップ種別ハンドラが共有する新しい `_finish_outcome`
  ヘルパーに、本項目の唯一の事後確認が置かれます
- [`bajutsu/common/orchestrator/loop/_functions.py`](../../bajutsu/common/orchestrator/loop/_functions.py) —
  `run_scenario` の `run_phase` クロージャが `live_bindings` を `before`・本体ステップ・
  あらゆる `after` の規則をまたいで共有している前例。本項目のフェーズをまたぐクラッシュ
  ラッチが踏襲します
- [`bajutsu/common/orchestrator/loop/step_loop_state.py`](../../bajutsu/common/orchestrator/loop/step_loop_state.py) —
  `StepLoopState`。ラッチが `bindings` の隣に置かれる場所。`bindings` はすでに、
  フェーズごとに新しく組み立てられる `StepLoopState` へ、シナリオスコープの可変
  オブジェクトを運び込んでいるフィールド
- [`bajutsu/common/runner/pipeline.py`](../../bajutsu/common/runner/pipeline.py) —
  `_run_on_lease`。まだリースを保持したまま `result` の `before_outcomes` / `steps` /
  `after_outcomes` を `app_crashed` で走査します。この新しい `_write_app_crash_artifacts`
  が真似る `_write_crash_artifacts`（BE-0421）
- [`bajutsu/common/runner/types.py`](../../bajutsu/common/runner/types.py) —
  `Lease.crash_artifacts`。`Lease.app_crash_artifacts` が踏襲する前例
- [`bajutsu/common/report/manifest.py`](../../bajutsu/common/report/manifest.py) —
  `_scenario_dict`。既存の `wall_offset_s` の pop が、本項目自身の `app_crash_artifacts` 除外の
  踏襲する前例
- [`bajutsu/common/report/load.py`](../../bajutsu/common/report/load.py) — `_kw` の欠落
  フィールド処理。すでに `wall_offset_s` 自身の組み立て直し経路として文書化されています。
  `_kw` 自体は変更不要ですが、`load.py:34-41` のコメントには `app_crash_artifacts` が
  2つ目の意図的な往復の例外として加わります（Unit 8）
- [`bajutsu/common/platform_lifecycle/protocols/run_environment.py`](../../bajutsu/common/platform_lifecycle/protocols/run_environment.py) —
  `app_crash_artifacts()` と `app_crash_tombstone()` が加わるプロトコル
- [`bajutsu/common/platform_lifecycle/relaunchers.py`](../../bajutsu/common/platform_lifecycle/relaunchers.py) —
  `device_relauncher`。`relaunch` ステップの実際の iOS 起動経路であり、`_resume_warm` の
  リースをまたぐ経路とは別物
- [`bajutsu/common/platform_lifecycle/environments/ios.py`](../../bajutsu/common/platform_lifecycle/environments/ios.py) —
  `_DeviceEnvironment.crawl_reset()`。`app_launched_at` を最新に保つため、`relauncher()` と
  並んで `XcuitestEnvironment` がオーバーライドする、iOS の3つ目の起動箇所
- [`bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py) —
  `_resume_warm`。BE-0291 の、iOS の4つ目の起動箇所。リースをまたぐ同じ長命の環境の上で走り、
  本項目はここでも `app_launched_at` を記録します
- [`bajutsu/crawl/core/_functions.py`](../../bajutsu/crawl/core/_functions.py) —
  `record_crash` のロック外クラッシュ確認。本項目のクロール側の収集呼び出しが加わる場所
- [`bajutsu/crawl/cli.py`](../../bajutsu/crawl/cli.py) — `_build_lane`。本項目の収集が
  読むレーンごとの環境
- [`bajutsu/crawl/repro.py`](../../bajutsu/crawl/repro.py) — `write_repros`。すでに
  `screen_map.crashes` を歩き、収集した証跡を書き込む `crash-NNN` の番号づけを所有します
- [`bajutsu/crawl/serialize.py`](../../bajutsu/crawl/serialize.py) — `screenmap_dict` /
  `screenmap_from_dict`。`Crash` の新しい `artifacts` フィールドをあえて除く JSON の往復
- [`bajutsu/common/platform_lifecycle/readiness.py`](../../bajutsu/common/platform_lifecycle/readiness.py) —
  `await_ready`。タイムアウトしても送出せず返すだけであり、`relaunch` ステップ自身の
  readiness 待機がそのステップを失敗させない理由
- [`bajutsu/common/backend_cli/simctl/env.py`](../../bajutsu/common/backend_cli/simctl/env.py) —
  `Env.terminate`/`Env.launch`。その `CalledProcessError` の扱いが、relaunch の除外で
  `outcome.ok is False` になり得る経路がアラートガード側だけであり、アプリの健全性とは
  無関係である理由
- [`bajutsu/common/evidence/sink.py`](../../bajutsu/common/evidence/sink.py) —
  `write_text`（マスキングする）と `write_bytes`（シンクの検査できない内容向けで、
  マスキングしない）の違い。本項目の証跡は、先にテキストへデコードすることでこれに従います
- [`scripts/collect_android_diagnostics.sh`](../../scripts/collect_android_diagnostics.sh) —
  ジョブ終了時の Android 診断掃引。本項目の `logcat` フィルタリングが従う前例であり、
  自身の `adb wait-for-device` が `adb root` の壊す対象を名指ししています
- [`bajutsu/common/backend_cli/adb_resident/resident_server.py`](../../bajutsu/common/backend_cli/adb_resident/resident_server.py) —
  `AdbDriver` 自身の読み取りチャネル。tombstone 取得の `adb root` にまるごと終わらされ、
  再起動はしません
- [`bajutsu/common/backend_cli/adb/_functions.py`](../../bajutsu/common/backend_cli/adb/_functions.py) —
  `instrument_cmd`。その `-w` フラグこそが、tombstone 取得の `adb root` が実際に終わらせるもの
- [`bajutsu/common/backends.py`](../../bajutsu/common/backends.py) — `make_driver`。adb 分岐が
  既存の `fetch_clock`・`act` キーワードを通している形が、本項目の `package` キーワードの
  先例になっています
- [`docs/ci.md`](../../docs/ci.md#the-ios-lane) — `actuation (xcuitest)` と `golden (adb)`。本項目の
  showcase シナリオが従う、ゲートしないという配置。`fault-injection (xcuitest)` は、本項目の
  CI ラッパーが従う失敗の形を検証するという振る舞いの方であり、その配置（今日ではすでに
  必須アグリゲータの一部です）ではありません

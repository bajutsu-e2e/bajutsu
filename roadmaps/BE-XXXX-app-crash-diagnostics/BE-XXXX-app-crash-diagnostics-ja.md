[English](BE-XXXX-app-crash-diagnostics.md) · **日本語**

# BE-XXXX — テスト対象アプリのクラッシュを検知し、スタックトレースをシナリオの証跡として収集する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-app-crash-diagnostics-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Platform support |
| 関連 | [BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact-ja.md)、[BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)、[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md)、[BE-0066](../BE-0066-web-crawl/BE-0066-web-crawl-ja.md) |
<!-- /BE-METADATA -->

## はじめに

テスト対象アプリ自体がシナリオの途中で異常終了したとき、`bajutsu run` は要素が見つからないときと
同じ種類の失敗を報告します。`ElementNotFound`（またはそれに類するドライバの失敗）が、見つからな
かったセレクタの名前だけを告げます。その失敗メッセージは、アプリが落ちたことを一言も述べません。
シナリオ自身の run ディレクトリ（`runs/<run_id>/<sid>/`）にも、OS 自身が書き出したレポートは
届きません。iOS では、異常終了したプロセスに対して macOS の `ReportCrash` が書き出す `.ips`
ファイルです。Android では、未捕捉の例外に対して `logcat` が出力する Java のスタックトレース、
または端末が `/data/tombstones` 配下に書き出すネイティブクラッシュのレポートです。赤くなった
シナリオを調べる開発者は、まず消去法でパターンに気づく必要があります。本来なら存在するはずの
セレクタが存在しない、という形です。そのあとで初めて、`bajutsu` 自身が生成した何物にも頼らず、
OS 自身のレポートを手で探しに行くことになります。

本項目は、この検知と収集の両方を追加します。あるステップでアプリが落ちたシナリオは、その事実を
名指しする専用の失敗メッセージを得ます。その run ディレクトリは、他の証跡の隣に `app-crash/`
サブディレクトリを新たに持ち、プラットフォーム自身のレポート、またはそのプラットフォームが提供する
もっとも近い代替物を保持します。

本項目はまず `bajutsu run` を、iOS（XCUITest）と Android（adb）の両バックエンドで対象にします。
iOS は Simulator に限ります。実機自身が抱える証跡の欠落（「iOS：`app.state`」を参照）ゆえに、
実機では `app_crash_signal()` が `None` を返します。シグナルをまったく持たないバックエンドが
返すのと同じ「確認できない」という答えです。同じ収集ロジックを、`bajutsu crawl` がすでに持つ
同種の検知にも拡張します。web（Playwright）バックエンドとその独自シグナルは、後続の項目に
委ねます。理由は「検討した代替案」に記します。

## 動機

`bajutsu` はすでに、自分自身が引き起こしたのではない種類の失敗を区別しています。
`base.BackendCrashError` は、バックエンド自身のドライバプロセス（常駐する XCUITest ランナーの
ホスト、adb の常駐サーバ、ブラウザプロセス）が落ちたことを名指します
（[`bajutsu/common/drivers/base/backend_crash_error.py`](../../bajutsu/common/drivers/base/backend_crash_error.py)）。
実行パイプラインはそこから回復します。死んだリースを破棄し、新しいデバイス上でシナリオ全体を
リトライします。
[BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact-ja.md)
（実装済み、PR [#1999](https://github.com/bajutsu-e2e/bajutsu/pull/1999)）は、その落ちた
ランナー自身のログと、iOS では `.ips` レポートを、失敗したシナリオの証跡へすでにコピーして
います。どちらも、*バックエンド*側の不具合を名指しています。チームがテストしているアプリ側
ではありません。

*テスト対象アプリ*に対しては、今日これに相当する仕組みが何もありません。AI 主導の探索経路である
`bajutsu crawl` は、この事象をすでに検知しています。`is_app_alive`
（[`bajutsu/crawl/core/_functions.py:281`](../../bajutsu/crawl/core/_functions.py)）は、要素ツリーが
アプリ自身の UI をまだ示しているかどうかを確認します。崩壊を見つけたクロールは `Crash`
（[`bajutsu/crawl/core/crash.py`](../../bajutsu/crawl/core/crash.py)）を記録します。崩壊に至った
操作パスであり、[`bajutsu/crawl/repro.py`](../../bajutsu/crawl/repro.py) によって決定的な再現
シナリオへ変換されます。ただし、その検知は UI 側のヒューリスティックにすぎません。背後に
プラットフォーム側の証跡を持ちません。`Crash` レコードが運ぶのは崩壊を生んだタップの列だけで
あり、死んだプロセスについて OS 自身が書き出したレポートではありません。あらゆる CI ゲートが
依存する決定的な経路である `bajutsu run` には、これに相当する確認が一切ありません。シナリオ
途中のアプリクラッシュは、次のアクションかクエリがたまたま送出する一般的な失敗としてしか
現れません。

この隙間は、本物のアプリの不具合を前にした開発者の時間を、実際に奪います。本項目が対象とするのは、
まさにこのケースです。`BackendCrashError` 自身の回復は、ドライバや環境側の不具合をすでに扱って
おり、本項目はその経路に手を加えません。アプリが落ちて失敗したシナリオは、今日のレポートでは、
セレクタの `id` がリネームされた、または画面が読み込まれなかったために失敗したシナリオと見分けが
つきません。同じ `ElementNotFound`、同じ「スクリーンショットを見て推測する」という出発点です。

本項目が生み出す観測可能な違いは次の点です。着地すれば、アプリがクラッシュしたシナリオは、その
事象を名指しするメッセージで失敗します。`runs/<run_id>/<sid>/app-crash/` ディレクトリは、
プラットフォーム自身の証跡を保持します。iOS では `ReportCrash` が書き出した `.ips` レポート
をその名前のまま、Android では `logcat-crash.txt`（端末が許せば `tombstone.txt` も）です。
開発者は、2つの誤った説明を消去する前に、正しいファイルを最初に開けるようになります。

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

本項目の初期の草案は、`AppCrashedError` を `run_scenario` の外へ送出し、`pipeline.py` に
`BackendCrashError` のクラッシュリトライ処理を真似た新しい `except` 節を加える設計でした。
レビューでこの形は却下されています（「検討した代替案」を参照）。バックエンドがクラッシュした
シナリオには、救える状態が何も残っていません。一方、*アプリ*がクラッシュしたシナリオには、
ドライバ、バックエンドプロセス、動作中のビデオ録画は、すべて残っています。落ちているのは
アプリだけです。そのようなシナリオを、`run_scenario` 自身の `RunResult` 組み立てを迂回する
別経路で終わらせれば、この3つを、アプリが落ちた理由とは無関係な事情で捨ててしまいます。

以下の設計は、代わりに分類を経路の内側にとどめます。`AppCrashedError` は、それを送出する
唯一の呼び出し箇所から外へ出ません。その呼び出し箇所は、既存のパイプラインがすでに正しく
仕上げ方を知っている、ふつうの終端ステップ失敗として扱い、`StepOutcome`
（[`bajutsu/common/orchestrator/types/step_outcome.py`](../../bajutsu/common/orchestrator/types/step_outcome.py)）
に新しく加える `app_crashed: bool = False` フィールドを `True` にします。これが、アプリ自身の
証跡を取り込むかどうかを `pipeline.py` があとから読んで判断する合図です（「収集をつなぐ」を
参照）。シナリオがどう終わったかについてレポートが必要とする他のあらゆる情報を、
`RunResult.steps[-1]` がすでに運んでいるのと同じ形です。

### 検知の方式：ステップがすでに失敗した時点で確認する1つのシグナル

毎ステップ、アプリがまだ動いているかを確認する方式は、あらゆるシナリオに、グリーンな実行を含めて
クエリを1つ追加してしまいます。構造上まれな失敗モードを捉えるための代償としては重すぎます。
そこで本項目は、一部のバックエンドだけが持つドライバの機能に対して、このリポジトリがすでに
確立している形を踏襲します。
[`InterruptionPolicyTarget`](../../bajutsu/common/drivers/base/interruption_policy_target.py) や
[`SettledReadProvider`](../../bajutsu/common/drivers/base/settled_read_provider.py) と同じ、
狭い opt-in のケイパビリティプロトコルです。

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

`Driver`
（[`bajutsu/common/drivers/base/driver.py`](../../bajutsu/common/drivers/base/driver.py)）という
巨大なプロトコルへ新しいメンバーを加えるわけではありません。`Driver` は `@runtime_checkable`
なので、そのメンバーはすべて `isinstance(x, base.Driver)` が `True` を返すための必須条件です。
このリポジトリには、`Driver` を完全に実装するものが今日すでに4つあります。`XcuitestDriver`、
`AdbDriver`、`PlaywrightDriver`、そして
[`XcuitestLiveDriver`](../../bajutsu/common/drivers/xcuitest_live/xcuitest_live_driver.py)です。
`XcuitestLiveDriver` は、BE-0238 が追加した4つ目のバックエンドで、実機の iOS 端末を W3C
WebDriver 経由で操作します。加えて、テスト用の fake backend もあります。さらに、
[`WebContextDriver`](../../bajutsu/common/drivers/webview/web_context_driver.py) のように、
`Driver` の一部だけを意図的に実装する、より狭いラッパーもあります。`WebContextDriver` は、
`web` ブロックへ入るときに実行ループが差し替えるドライバです。`Driver` へ必須メンバーを
加えれば、これらすべてがスタブを持つ必要に迫られます。本項目がそもそも `web` ブロックの中で
確認するつもりのない `WebContextDriver` も例外ではありません。`AppCrashSignal` はこの全体を
回避します。実装するのは `XcuitestDriver` と `AdbDriver` だけであり、後述の呼び出し箇所は
`isinstance` で問い合わせます。BE-0406 がすでに `InterruptionPolicyTarget` に対して行っている
のと同じ方式です。

`TracingDriver`
（[`bajutsu/common/drivers/tracing.py`](../../bajutsu/common/drivers/tracing.py)）は、
`--trace-driver` が使うプロキシです。それぞれのプロトコルを、自身の `_PROTOCOLS` タプルの
中で実インスタンス属性として設置します。設置は `isinstance(wrapped, protocol)` で判定して
おり、そのおかげでプロキシ自身に対する `isinstance` も正しく読めます。`AppCrashSignal` は
このタプルに加わります。`InterruptionPolicyTarget` がすでにそうしているのと同じです。
`XcuitestDriver` や `AdbDriver` をラップすれば設置され、`PlaywrightDriver`・fake backend・
`XcuitestLiveDriver`・`WebContextDriver` をラップしても設置されません。プロキシが追跡する
他のケイパビリティは、どちらの場合でも影響を受けません。

`bajutsu/common/orchestrator/loop/_step_runner.py` は、どのステップも種類ごとに4つの
ハンドラのどれかへ振り分けます。`_handle_if`、`_handle_for_each`、`_handle_web`、そして
`_handle_action` です。最後の1つが `wait`・`assert`・`handleSystemAlert`・あらゆるアクション
ステップを引き受けます。4つはそれぞれ異なる形で自分の `outcome.ok` を確定させます。
`_handle_action` 自身の
`outcome.ok, outcome.reason, outcome.assertion_results = ok, reason, results` という行は、
tip の解除やアラートガードの再試行が終わったあとにありますが、それでもそのステップの
最後の言葉ではありません。宣言されていない割り込みや、失敗した `extract` が、同じ
ハンドラの内側でそのあとも `outcome.ok` を `False` へ反転させ得ます。この1行だけに
確認をつなぐ設計は、本項目の初期の草案が採った形ですが、`_handle_action` 自身の終端失敗
すら遅れて見るうえ、`_handle_if` や `_handle_for_each` の条件クエリの失敗、
`_handle_web` の `within` セレクタの失敗にはまったく届きません。どちらもこの行を
通らないからです。

4つのハンドラが実際に共有しているのは `self.state.outcomes.append(outcome)` ですが、
呼ぶ回数は1つずつではありません。`_handle_if`・`_handle_for_each`・`_handle_web` は、
返る直前にそれぞれちょうど1回だけ呼びます。`_handle_action` は2回呼びます。自身の
終端でも呼びますが、それより前、`UncoveredSystemAlertLocale` を受けた早期リターン
（`_step_runner.py:457`）でも呼んでおり、そちらはその時点で `outcome.ok` を `False`
にしてから返ります。ハンドラごとに1つの呼び出し箇所だけを見る設計は、本項目の初期の
草案が採った形ですが、その `_handle_action` の2つ目の呼び出しを見逃します。ファイル
自身のコメントがすでに名指ししている、他のあらゆる事後処理を同じ理由で個別に飛ばして
しまう、まさにその出口です。本項目は、この5つの呼び出し箇所すべてで、素の `append`
の代わりに呼ぶ共有のステップを1つ加えます。`self._finish_outcome(active_driver,
outcome)` です。他の3つのハンドラは1回ずつ、`_handle_action` は2回、計5箇所です。
append 自体もこの中で行うため、あとから加わるステップの種類がここへの配線を必要と
しない点は変わりません。`_drain_step_interruptions` がすでに同じ4つのハンドラへ
割り込みの確認について与えているのと同じ性質です。あとから加わる6つ目の呼び出し
箇所が同じ抜け穴を静かに開け直さないよう、`_finish_outcome` の外に
`self.state.outcomes.append` が残っていないことを検証する高速スイートのテストも
加えます。`_finish_outcome` は、その append の直前で
`isinstance(active_driver, base.AppCrashSignal)` と `outcome.ok is False` の両方を
確認します。加えて、アプリ自身を意図的に終了させる、たった1つのアクションのための
シナリオスコープの除外があります。次の段落で説明します。

入れ子になったステップの失敗は、最初にそれを観測したハンドラだけでなく、構造上
何度も `_finish_outcome` に届きます。`_run_if` と `_run_for_each` は、どちらも
自身の本体を `self.exec_steps`（割り込みの回復処理も再入する、同じループ）を通じて
走らせます。したがって、`if` の中の `forEach` の中のアクションという3段の入れ子で
クラッシュが起きると、アクション自身、`forEach`、`if` という3つの外側の outcome が
順に確定し、それぞれが独立して `_finish_outcome` を呼びます。`after` フェーズも、
すでに落ちたアプリに対して走るあとかたづけステップごとに、もう1回ずつ加わります。
直前の `relaunch` の失敗が*原因で*走り、その `relaunch` 自身が終了させたばかりの
アプリに対して確認してしまうステップも含まれます。この dispatch は `_dispatch_after`
自身の `run_phase(steps, after_outcomes, "after", …)` 呼び出し
（[`_functions.py:824-828`](../../bajutsu/common/orchestrator/loop/_functions.py)）を
通ります。本体ステップのループとは別の経路であり、自前の新しい `StepLoopState` を
組み立てます（`_functions.py:994`）。`_run_recovery`
（[`_step_runner.py:70`](../../bajutsu/common/orchestrator/loop/_step_runner.py)）を
通るのではありません。こちらは BE-0314 の*割り込み*の回復ステップのための再入経路
です（同じ `exec_steps` を囲む `self.state.running_recovery = True`、
`_interrupt_guard.py:70`）。`after` フェーズではありません。`_run_recovery` は、この
段落が別途勘定に入れる必要のある、3つ目の独立した確認の倍加要因です。すでに落ちた
アプリに対して走る割り込みの回復ステップは、確定する outcome ごとに1回の確認を
払います。これはどちらのラッチも抑えない、ふつうの（`relaunch` でない）失敗です。
除外を `outcome.action` だけに
結びつけると、まさにこのケースを見逃します。失敗した `relaunch` 自身の outcome は
確認を飛ばしますが、それを包む `forEach`・`if` の outcome や、その後に発火する
`after: on: fail` のステップは、それぞれ別の `outcome.action` を持つため、いずれも
新規に確認してしまい、`app.state` の正直な `notRunning` を、確定したばかりの新しい
クラッシュと読み違えます。したがって除外は、1つの outcome の性質ではなく*シナリオ*
の性質でなければなりません。`run_scenario` が1回だけ作り、`live_bindings` をすでに
フェーズ間で共有しているのと同じ方法（`_functions.py:690`、`:699`）で `run_phase`
の呼び出しのたびにそのクロージャへ渡す、同じ可変オブジェクトです。`bindings` の隣、
`StepLoopState` に置きます。`_LoopConfig` ではありません。`_LoopConfig` は
`@dataclass(frozen=True)` であり、「ステップループが読むだけで決して変更しない、
run 不変の入力」と自ら文書化しています。`mailbox` と `progress` はループが呼び出す
だけのコールバックであり、ループがそこを通じて書き込む状態ではないため、可変
オブジェクトの前例にはなりません。`StepLoopState` こそがその前例です。`_run_steps`
はフェーズごとに新しいインスタンスを組み立てますが、`run_scenario` が1回だけ作った
その同じ `bindings` オブジェクトを、どのインスタンスにも渡します。これこそが、
このラッチに必要なシナリオスコープの共有であり、`bindings` の隣にもう1つ引数を
加えるだけのコストで済みます。決して可変値を持たないと文書化されたクラスへ新しい
フィールドを加えるよりも軽い変更です。`_finish_outcome`
は、`outcome.action == "relaunch"` かつ `outcome.ok is False` を見た瞬間、
`app_crash_signal()` を一度も呼ぶ前にこれを立てます。そして以後のあらゆる呼び出しで
まずこれを確認します。一度立てば、同じシナリオの中でそれ以降のどの outcome も
確認しません。それを包む outcome も `after` フェーズのステップも例外ではなく、
どれも確認自身が引き起こしたクラッシュと読み違えられることはありません。

同じオブジェクトは、*確定した*クラッシュも記憶します。`_finish_outcome` が
`AppCrashedError` を送出し捕まえた最初の時点で立て、同じ伝播の中であとから確定する
outcome は、`app_crash_signal()` をもう一度呼ぶことなく、そのすでにわかっている
シグナルを自身の `outcome.reason` へ折り込むだけになります。ただし、確認できなかった
答えではこれを立てません。これは意図的な非対称です。ある1つのステップの `None` は、
*次の*ステップの失敗がクラッシュかどうかについて何も教えてくれないため、そこで
立ててしまうと、本物のクラッシュを見逃す危険を冒すことになります。この理由から、
ここで買えた抑制は「シナリオごとに1回」よりも狭いものです。上に挙げた relaunch と
確定済みクラッシュのケースでは効きますが、クラッシュを伴わないふつうの失敗には
効きません。ふつうの失敗は、これまで通り、確定する outcome ごとに1回の確認を払い
続けます（入れ子の深さに、失敗する `after` の規則1つにつき1回を足したぶんです）。
本項目の初期の草案がまるごと避けられると主張していたのと同じコストです。それでも
そのコストは、シナリオがどれだけ深く入れ子になり、失敗時にいくつの `after` 規則を
発火させるかで抑えられており、実際には小さい数字であることが多く、しかもすでに
失敗したステップに対してしか走りません。後述の「検討した代替案」がグリーンな実行
すべてにコストを足すとして却下する、事前のステップごとのポーリングとは違います。

この反応的な形には、あとで見つかるより先に名指しておくべき、構造上の死角が1つ
あります。シナリオ自身の*最後の*ステップが原因のクラッシュで、そのステップ自身の
操作がそれでも `ok=True` を報告する場合です。アプリが落ちる前にランナーが届けた
`tap`、少し前に捉えたツリーを読んだ `assert` などです。これより後に走るものがない
ため、失敗して `_finish_outcome` に届くステップが存在しません。シナリオレベルの
`expect` もこの隙間を閉じません。`_evaluate_expect` が生成するのは `StepOutcome`
ではなく `AssertionResult` であるため、`_finish_outcome` にはそもそも届きません
（[`_functions.py:742-752`](../../bajutsu/common/orchestrator/loop/_functions.py)）。
これを閉じるには、シナリオ終了時の無条件の確認が要ります。まさにこの反応的な設計が
避けようとしているグリーンな実行すべてへのコストです。したがって本項目は、確認の
トリガーを広げるのではなく、この隙間を設計上の前提として受け入れます。かわりに、
この信号を*実際に検証する*シナリオには、クラッシュを引き起こすステップの後にもう
1ステップを置くことを求めます（Unit 11 の showcase シナリオはどちらもそうしています。
クラッシュのトリガーをタップしたあと、死んだアプリに対してもう1ステップを進め、
タップ自身ではなく、その後のステップの方が失敗して確認される仕組みです）。最後の
ステップがクラッシュのトリガーであるシナリオは、この設計自身の構造によりグリーンの
ままです。

`_finish_outcome` が `active_driver.app_crash_signal()` を呼ぶのは、どちらのラッチも
立っていないときだけです。`None` でない答えは、その場で `base.AppCrashedError(signal)`
を送出し、同じ式の中で捕まえ、そのメッセージを `outcome.reason` へ折り込み、
`outcome.app_crashed` と確定済みクラッシュのラッチの両方を `True` にします。この1点
より先へ伝播することはありません。`active_driver` は、そのステップを実際に操作した
ドライバです。`web` ブロックの内側のステップと、そのブロック自身を包む outcome とでは
答えが異なります。`_handle_web` はそのブロック用に組み立てた `WebContextDriver` の上で
`step.web.steps` を走らせるため（`self.exec_steps(step.web.steps, web_driver)`）、その
内側のステップ自身の `_finish_outcome` 呼び出し（`_handle_action` 経由）は
`active_driver = web_driver` を見ます。ここでは `isinstance` が `False` を返し、確認は
本物の no-op になります。「はじめに」で述べた本項目の web バックエンドに対する範囲と
一致します。`_handle_web` 自身を包む outcome は、`_finish_outcome` への5つ目の別の
呼び出しであり、意図的にブロック自身の `active_driver` 引数を渡します
（[`_step_runner.py:287-290`](../../bajutsu/common/orchestrator/loop/_step_runner.py)、
「内側の `web_driver` ではない」）。ブロックがコンテキストを切り替える前に有効だった、
*ネイティブの*ドライバです。したがって、ネイティブホストのクラッシュが包む `web`
ステップ自身の失敗として表面化した場合（`WebView` ブリッジの足元でネイティブホストが
落ちたために内側のステップが失敗する場合）は no-op ではありません。`_finish_outcome`
は、他のあらゆる失敗ステップとまったく同じように、ネイティブのドライバを確認します。

### iOS：要素ツリーではなく `app.state` を使う

XCUITest は、対象アプリのプロセス状態を `XCUIApplication.state` としてすでに公開しています。
`notRunning` というケースが、「本当に落ちているか」という問いに直接答えます。`crawl` の
`is_app_alive` は、同じ事実を空、または予期しない要素ツリーから推測しています。この推測には
誤検知の実際のリスクがあります。システムアラートがアプリの UI を覆っている場合や、シナリオが
意図的に `background` ステップを実行した場合です。`app.state` はどちらも回避します。
バックグラウンドへ回っただけで生きているアプリは `runningBackgroundSuspended` または
`runningBackgroundActive` を返し、`notRunning` にはなりません。

`notRunning` という答えだけでは、あらゆるプラットフォームでクラッシュを証明できません。
実機では、OS によるメモリ不足時の強制終了や、起動未完了も同じ答えを返し得ます。どちらも
Simulator には当てはまりませんが、XCUITest バックエンドは Simulator 専用ではありません。
`xcuitest.deviceType: device`
（[`xcuitest_config.py:20`](../../bajutsu/common/config/schema/xcuitest_config.py)）は、
同じ `XcuitestDriver` と `XcuitestEnvironment` を通じて実機の iPhone を操作します。そこでは
Simulator 自身の制約が成り立ちません。実機はメモリ不足時にフォアグラウンドのアプリを
実際に jetsam で終了させます。しかも以下の `.ips` 掃引にはそもそも読むものがありません。
実機のクラッシュは、ホスト自身の `~/Library/Logs/DiagnosticReports` には決して届かず、
実機では `self._bundle_id` が `None` になります
（[`xcuitest_environment.py:319`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py)）。
そのビルドは `_prepare_simulator` の経路を通さず、帯域外でインストールされるからです。
本項目の `Info.plist` 読み取りは、まさにその経路に依存しています。本項目は、web backend
をすでに後続の項目へ切り出しているのと同じように、Simulator だけを対象にします。
`XcuitestDriver.app_crash_signal()` は実機に対してその場で `None` を返します。シグナルを
まったく持たないバックエンドが返すのと同じ「確認できない」という答えです。実機自身の
OS による強制終了や、`deviceType: device` ターゲット自身が抱える証跡の欠落を、確定した
アプリクラッシュと読み違えないためです。シナリオのステップのうち1つ、`relaunch` だけは、
どちらのデバイス種別でもアプリを意図的に終了させます。その iOS 側の経路は
`device_relauncher` の `e.terminate(bundle_id)` に続けて `e.launch(...)` を呼ぶという
ものです（[`relaunchers.py:64`、`:78`](../../bajutsu/common/platform_lifecycle/relaunchers.py)）。
`xcuitest_environment.py:855-856` の見た目の似た組ではありません。あちらは `_resume_warm`、
BE-0291 の*リースをまたぐ*ウォーム再利用の経路です。`XcuitestEnvironment.relauncher()` は
`_DeviceEnvironment` の実装をオーバーライドしています（「iOS：`.ips` レポートの照合」を
参照してください。理由はこれとは無関係で、`app_launched_at` を再記録するためです）が、
そのオーバーライドが包むのは `device_relauncher` がすでに返す `RelaunchFn` だけであり、
`_resume_warm` を一切呼びません。`_resume_warm` は、この経路とは無関係な、リースをまたぐ
別経路のままです。そこで `_finish_outcome` は、`relaunch` ステップ自身が失敗した時点で
確認を飛ばし、このシナリオがそれ以降に行うはずだった確認もすべて抑えます（「検知の方式」
を参照）。ただし、この防御が実際に見た目ほど効いているわけではありません。`relaunch` の
クロージャ自身は、`readiness.await_ready(...)`
（[`relaunchers.py:79`](../../bajutsu/common/platform_lifecycle/relaunchers.py)）を
副作用のためだけに呼び、返ってくる `ReadinessResult` を握りつぶします。しかも
`await_ready` 自体は例外を送出しません。タイムアウトした待機も、成功した待機と同じく
`ReadinessResult(False, "timeout", …)` を返すだけです
（[`readiness.py:170`](../../bajutsu/common/platform_lifecycle/readiness.py)）。したがって、
新しい起動中に発生する `fatalError()`——Unit 11 の showcase 用の仕掛けがまさに引き起こす
ケースです——は、`relaunch` ステップを失敗させません。`await_ready` は静かにタイムアウト
し、クロージャはそのまま返り、ステップは `ok=True` を報告します。周辺のツール群も
これを代わりに失敗として扱いはしません。`e.terminate` は自身の `CalledProcessError` を
丸ごと握りつぶし（[`env.py:176`](../../bajutsu/common/backend_cli/simctl/env.py)）、
まれに `e.launch` 自身が `CalledProcessError` を送出するデバイス・ツール側の失敗が
起きても、`_run_step_body` の例外捕捉はそれを名指ししていません。そのステップの
`outcome.ok = False` になる代わりに `run_scenario` 全体から抜け出してしまうため、
`_finish_outcome` にすら届きません。`relaunch` ステップ自身の結果が実際に `ok=False`
になる経路は、今日ではアラートガード・待機中回復失敗の経路（「検知の方式」のネストした
失敗についての段落を参照）だけです。これはアプリの健全性とは本当に無関係であり、まさに
この除外が存在する理由そのものです。新しい起動中のクラッシュが失われるわけではなく、
1ステップ遅れて帰属先が変わるだけです。アプリに触れる次のステップで失敗し、その結果は
`relaunch` ではない通常の結果であるため、どちらのラッチも飛ばす理由がなく、
`_finish_outcome` は他の失敗と同じようにそれを確認します。それ以外の
確認は、ステップがすでに失敗した後、しかも同じシナリオの手前のすべてのステップで
アプリが動作していると確認できた*あと*にしか走りません。したがって、`relaunch` 以外の
ステップの下で起動が完了しなかったケースは、この事後確認が出会う場面ではありません。
それ以外の場面での `notRunning` という答えは、直前まで動いていたアプリが今は動いて
いないことを意味し、この Simulator という環境には、それ以外にそうなる経路がありません。

新しいルートを
[`BajutsuKit/Sources/BajutsuRunner/openapi.yaml`](../../BajutsuKit/Sources/BajutsuRunner/openapi.yaml)
へ加えます。BE-0316 の `/systemAlert/query` と同じ形です。リクエストボディを受け取り、
状態を運ぶ JSON を返します。生成された `APIHandler`
（[`BajutsuKit/Sources/BajutsuRunner/APIHandler.swift`](../../BajutsuKit/Sources/BajutsuRunner/APIHandler.swift)）
に対応するメソッドが加わり、そのプロバイダ実装がランナー自身の `XCUIApplication` に
`.state` を問い合わせます。今日、実際にリクエストを処理している
のは `Router.swift` ではなく `RunnerServer.swift` です。`APIHandler` を組み立て、その
生成済みルートを登録しているのは後者です。`Router.swift` はパリティテストのためだけに
残っており、実際の実行では何も答えません。本項目の初期の草案が指定したように、ルートを
`Router.swift` にだけ加えると、`XcuitestDriver` のリクエストは実サーバに対して404に
なってしまいます。

`XcuitestDriver`
（[`bajutsu/common/drivers/xcuitest/xcuitest_driver.py`](../../bajutsu/common/drivers/xcuitest/xcuitest_driver.py)）
に、`is_real_device: bool = False` というコンストラクタ引数を加えます。`device_os`
（BE-0358）をすでに通しているのと同じ方法で `make_driver` から通します。
`app_crash_signal()` はまずこれを確認し、`True` であればルートを呼ぶ前にその場で
`None` を返します。上で決めた、Simulator だけを対象にする範囲です。それ以外では、
このルートを1回呼ぶことで `app_crash_signal()` を実装します。`notRunning` という
答えがそのままシグナルの文字列になります。それ以外の状態はすべて `None` を返します。
この呼び出しに届いたチャンネルエラーは `None` へ
握りつぶしません。それは既存の `XcuitestRunnerCrashError` であり `BackendCrashError` の
一種です。本項目は、これをすでに受け持つ回復経路へそのまま伝播させます。ステップ自身の
セレクタ失敗の直後にルートが失敗するのは、ふつうの競合であり、チャンネルがこのステップと
無関係だという根拠にはならないからです。

### iOS：`.ips` レポートの照合、BE-0421 が着地させた掃引を再利用する

[BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact-ja.md)
は**実装済み**です（PR [#1999](https://github.com/bajutsu-e2e/bajutsu/pull/1999)）。ランナー
自身の `xcodebuild` プロセスが異常終了したとき、macOS が `~/Library/Logs/DiagnosticReports`
へ書き出したものの中から正しい `.ips` ファイルを見つける処理は、すでに着地しています。
[`bajutsu/common/platform_lifecycle/environments/xcuitest/_functions.py`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/_functions.py)
の `_diagnostic_reports_dir()` と `_reports_since(reports_dir, pattern, since)` がその照合を
担います。両者とも名前と時刻だけによる汎用的な照合であり、シグネチャ自体に `xcodebuild` を
持ち込みません。レポートのヘッダが解析できるときは `_reported_pid()` がさらに絞り込みます。
本項目は、同じディレクトリに対して2つ目の掃引を書く代わりに、`_reports_since` をテスト
対象アプリ自身のバイナリに対してそのまま再利用します。新しい `.ips` ヘッダ形式や
`DiagnosticReports` の移動といった照合ルールへの修正が入ったとき、再利用していなければ、
もう一方は古い実装のまま気づかれずに残ってしまいます。

PID による絞り込みは、BE-0421 自身のレポートに対してと同じようには使えません。XCTest が
公開する `XCUIApplication` の表面には PID を読む手段がなく、`BajutsuKit/` の中にも今日
それを読む箇所はありません。本項目は代わりに Simulator の UDID で絞り込みます。
Simulator 上のアプリの `.ips` レポートは、そのヘッダに実行ファイルのフルパス
（`.../CoreSimulator/Devices/<udid>/data/Containers/Bundle/Application/…`）を運んでおり、
そこにクラッシュしたプロセスが動いていた Simulator 自身が現れます。`Lease`
（[`bajutsu/common/runner/types.py`](../../bajutsu/common/runner/types.py)）はすでに、
リースしたデバイス自身の `udid` を記録しています。そこで、BE-0421 自身の
`_crash_reports(spawned_at, pid)` の姉妹にあたる新しい `_app_crash_reports(launched_at, udid)`
を同じモジュールに加え、`_reported_pid` による確認の代わりに、`_reports_since` が返す
レポートのうち、パスがその同じ `udid` を名指ししているものだけを受け入れます。これにより、
同一の対象バイナリを動かす Simulator が並列実行の CI ホスト（`--workers 2`）で2台、同じ
時間帯に存在する場合でも、PID を使わずに区別できます。

`_reports_since(reports_dir, pattern, since)` は、それでもファイル名で照合します。`.ips`
レポートのファイル名が名指すのはクラッシュしたプロセスであり（`Showcase-2026-…ips`）、
バンドル ID ではありません。BE-0421 は自分自身の既知のプロセスに対して、リテラルの
`"xcodebuild-*.ips"` を渡しています。`XcuitestEnvironment` が保持する `ios.bundle_id`
（`self._bundle_id`、`com.example.Showcase`）はその名前ではなく、レポートのファイル名と
一致することはありません。本項目は代わりに、インストール済みアプリ自身の `Info.plist`
（`Path(ios.app_path) / "Info.plist"`）から `CFBundleExecutable` を、各起動時に一度
読み取ります。あらゆる iOS バンドルが宣言を義務づけられているこの1つのプロパティリスト
キーから、掃引のパターンを組み立てます。`e.install` がインストール元とする、その同じ
`ios.app_path` がすでに名指すバンドルからの読み取りです。

`XcuitestEnvironment`
（[`bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py)）
は、対象アプリをすでに起動しており、自身の `udid` もすでに知っています。ここに
`app_launched_at` というタイムスタンプを、その起動の直後に記録するよう拡張します。ただし、
`relaunch` ステップ自身の起動——この節がたった今 `_resume_warm` と区別したもの——は
`device_relauncher` のクロージャを通じて走り、`(udid, run, extra_env)` を閉じ込めるだけで、
目印を更新すべき `XcuitestEnvironment` がその場にありません。放っておけば、
`app_launched_at` はリースの最初のコールド起動のまま凍りついてしまい、シナリオ途中の
`relaunch` に続くクラッシュは、その `relaunch` 以前にまで遡る `since` で
`DiagnosticReports` を掃引することになります。`relaunch` 自身がすでに置き換えたはずの
クラッシュの `.ips` を、逆に添付しかねないほど広い範囲です。`XcuitestEnvironment` は
`relauncher()` をオーバーライドします（それ以外は `_DeviceEnvironment` 自身の実装の
ままです）。`device_relauncher` が返す `RelaunchFn` を包み、それを呼んだあとで、
コールド起動の箇所がすでにしているのと同じ方法で `app_launched_at` を記録し直します。
`AndroidEnvironment` には対応するオーバーライドは要りません。すでに自前で
`relauncher()` をオーバーライドしており
（[`android_environment.py:326`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py)）、
そこでの `e.launch` は Unit 5 がすでに名指す3か所の起動箇所のうちの1つだからです。

`_DeviceEnvironment.crawl_reset()`
（[`ios.py:124`](../../bajutsu/common/platform_lifecycle/environments/ios.py)）は、まったく
同じ形——`e.terminate(bundle_id)` に続けて `e.launch(...)`——を持つ、iOS の3つ目の起動箇所
です。`relauncher()` をまったく経由せず、`crawl` 自身の `reset` 呼び出し可能オブジェクトが、
フロンティアを再訪するたびにこれを走らせます
（[`cli.py:300`](../../bajutsu/crawl/cli.py)）。手を入れなければ、`relauncher()` の
オーバーライドがいましがた閉じたのと同じ古びが、この経路で再び開いてしまいます。クロールは
1回の実行で複数のクラッシュを記録するため（`current_fp = None; continue`、
[`_functions.py:668-669`](../../bajutsu/crawl/core/_functions.py)）、2件目のクラッシュの
掃引は自身の `crawl_reset` より前にまで遡り、1件目のクラッシュの `.ips` レポートをそのまま
受け入れてしまいます。誤ったクラッシュのレポートを、レポートを正しく紐づけることこそが
存在意義のディレクトリへ持ち込むことになります。`XcuitestEnvironment` は `crawl_reset()`
もオーバーライドします。`relauncher()` のオーバーライドと同じ形です。`_DeviceEnvironment`
の実装を呼んでから、その隣で `app_launched_at` を記録します。`AndroidEnvironment` には、
ここでも対応するオーバーライドは要りません。自前の `crawl_reset()` の `e.launch`
（[`android_environment.py:410`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py)）
が、Unit 5 がすでに名指す3か所のうちの3つ目だからです。

`_resume_warm`（BE-0291 の*リースをまたぐ*ウォーム再利用の起動、
`xcuitest_environment.py:855-856`）は、iOS の4つ目の起動箇所です。同じ長命の
`XcuitestEnvironment` インスタンスの上で走ります。`start()` はランナーが再利用可能な
たびにここへ戻り（`:297`）、`XcuitestEnvironment` は `has_reusable_resident()` を
オーバーライドしているため、BE-0291 はこの環境をリースをまたいで保持します。この
経路では何も `app_launched_at` を記録し直しません。放っておけば、これまで閉じてきた
のと同じ古びが、両方の目印が同時に壊れるぶんだけさらに悪い形で戻ってきます。デバイス
X 上でシナリオ1がコールド起動し（目印 = T0）、そのアプリがクラッシュして
`Showcase-…ips` を残します。シナリオ2〜5は同じデバイス上でウォーム再開しますが、
目印はまだ T0 のままです。シナリオ5のアプリがクラッシュし、`since = T0` で
`DiagnosticReports` を掃引すると、シナリオ1のレポートは実行ファイル名のパターンにも
（同じアプリ）、`udid` の確認にも（同じ Simulator）一致してしまい、
`_app_crash_reports` はそれを受け入れます。シナリオ5の `app-crash/` には、シナリオ1の
クラッシュが入ってしまいます。他の2つと同じ形の直し方です。`_resume_warm` の中で、
自身の `e.launch` の隣で `app_launched_at` を記録し直します。目印は、アプリが実際に
走っている、そのときどきの起動を追いかけ続けます。

新しく `app_crash_artifacts() -> list[tuple[str, bytes]]` を `RunEnvironment` プロトコル
（[`bajutsu/common/platform_lifecycle/protocols/run_environment.py`](../../bajutsu/common/platform_lifecycle/protocols/run_environment.py)）
に、`take_crash_snapshot()` の隣に加えます。ただし、それよりも素直な形です。
`take_crash_snapshot()` が返すのは*サンク*です。バックエンドクラッシュは最初に観測された
時点で捕捉し、プールがリースを解放するまで確定を遅らせます。そうしなければ、同じ温まった
環境を再利用する別のワーカーの次の起動が、凍結したはずの照合条件を先に上書きしてしまいます。
本項目の収集には、そうして遅らせるべき競合がありません。`pipeline.py` がこれを直接呼び出す
時点で（後述の「収集をつなぐ」を参照）、この同じシナリオ自身のリースをまだ保持しており、
そのリースが解放されるよりずっと前だからです。したがって `app_crash_artifacts()` は
`app_launched_at` をその場で生きたまま読み、完成したリストをそのまま返します。サンクは
要りません。`RunEnvironment` は、どの具象クラスも継承しない構造的プロトコルであるため、
`take_crash_snapshot()` にも継承された既定値はありません。`WebEnvironment`・
`AndroidEnvironment`・`_DeviceEnvironment`（`FakeEnvironment` がこれを継承します）は、
すでにそれぞれ自前の1行の `return` を宣言しています。`app_crash_artifacts()` も同じ形を
踏襲しますが、Android だけは違います。`WebEnvironment` と `_DeviceEnvironment`
（`FakeEnvironment` がこれを継承します）はそれぞれ自前の `return []` を新たに持ちますが、
`XcuitestEnvironment` と `AndroidEnvironment` は、この節と次の節が説明する本物の収集で
それぞれオーバーライドします。`pool.py` の `lease()` がこのメソッドをリースしたあらゆる
環境から、クラッシュしたときだけでなくあらゆるリースのたびに読むため、リースされる
環境はすべて、このどちらか一方を持つ必要があります。

`ReportCrash` は、アプリが落ちたその瞬間にはまだレポートを書き終えていないことがあります。
`_app_crash_reports` は `~/Library/Logs/DiagnosticReports` を、対象の実行ファイル名と
この環境自身の `udid` に一致し、更新時刻が `app_launched_at` 以降であるレポートを求めて、
数秒を上限にポーリングします。これはこの1つのメソッドの中だけの、短く上限のある待機であり、
シナリオそのもののリトライではありません。シナリオは、掃引の結果にかかわらず一度だけ、
すぐに失敗します。この掃引と、その中の処理はすべて、最後の書き込みだけでなく本体全体を
1つの `try`/`except Exception` で包みます。ディレクトリの走査や読み取りの失敗は、
レポートが見つからない場合と同じく、アプリ自身のクラッシュという判定を変える力を
持ちません。したがって同じように空リストへ解決します。macOS 以外のホストも、同じく
即座に `[]` へ解決します。

### Android：まず `logcat` のクラッシュバッファ、次に root 権限に依存する tombstone 取得

`AdbDriver`
（[`bajutsu/common/drivers/adb/adb_driver.py`](../../bajutsu/common/drivers/adb/adb_driver.py)）は、
`adb shell pidof <package>` によって `app_crash_signal()` を実装します。`AdbDriver` は今日、
serial といくつかの注入されたコールバックだけから構築されます。パッケージ名は持って
おらず、それを組み立てる `backends.make_driver` も持っていません。対象のパッケージ名は
すでに `AndroidEnvironment` に届いています。その `install` / `pm clear` / `force_stop` /
`launch` の各呼び出しは、いずれもターゲットの設定（`targets.<name>.android.package`）から
それを受け取っていますが、ドライバには届いていません。`make_driver`
（[`bajutsu/common/backends.py`](../../bajutsu/common/backends.py)）に `package: str |
None = None` というキーワードを加え、`device_os`（BE-0358）をすでに通しているのと同じ
方法で `AdbDriver.__init__` へ通します。`Driver` は `@runtime_checkable` で共通の基底
クラスを持たないため、そこにデータメンバーを置けば、あらゆるバックエンドとあらゆる
インラインのテストダブルが、同じ宣言を繰り返すことになります。そこで、素の
コンストラクタ引数として渡すほうを選びます。

空の `pidof` という答えは、アプリがまだプロセスを保持しているはずの場面では、必要条件
ではあっても十分条件ではありません。起動が完了しなかった場合や、本項目がシナリオレベル
の原因を持たない終了とも一致します。Android には、通常のテスト条件下で jetsam のような
OS による強制終了はありませんが、ふつうのプロセス終了もクラッシュと同じ答えを `pidof`
に返させます。`adb shell dumpsys activity exit-info <package>` は、そのパッケージの
`ApplicationExitInfo` の履歴を、各項目にタイムスタンプを付けて報告し、`CRASH` や
`CRASH_NATIVE` を `ANR`・`LOW_MEMORY`・`USER_REQUESTED` から区別できます。これは API 30
以降で利用でき、このリポジトリの CI がすでに起動している API 34 の AVD でも利用できます。
ただし、この履歴はプロセスの生存期間をまたいで残るため、直近の1件だけでは信頼できません。
今回の終了が無関係な理由で `ApplicationExitInfo` をまだ記録していない場合、`dumpsys` の
返す最新の項目が、以前のシナリオのクラッシュのままということがあり得ます。`AdbDriver`
に `launched_at: Callable[[], float | None] | None = None` というコンストラクタ引数を
加えます。これは `AndroidEnvironment.app_launched_at` をその場で読む注入されたコール
バックであり、`fetch_clock` がすでに使っている、構築時に固定せず呼び出しのたびに読む
のと同じ仕組みです。`app_crash_signal()` は、`pidof` が空を返した直後に一度だけこの
履歴を読み、その*最新*の項目が `CRASH` か `CRASH_NATIVE` を報告し、*かつ*その項目
自身のタイムスタンプが `launched_at()` 以降であるときにだけ事象を確定します。今回の
起動より前の古い項目を除外するためです。どちらかが成り立たなければ `None` を返します。
シグナルをまったく持たないバックエンドが返すのと同じ、「確認できない」という答えです。
これは、`app.state` の `notRunning` が Simulator 自身の制約から無償で得ている裏付けに
相当します。Android では、プラットフォーム自身が報告する、時刻で絞り込んだ終了理由が、
adb にとって同じ役割の積極的な確認を与えます。

`AndroidEnvironment`
（[`bajutsu/common/platform_lifecycle/environments/android/android_environment.py`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py)）
に、iOS の環境と同じ `app_launched_at` の記録を、3か所ある起動の呼び出し箇所
（`e.launch(package, launch_env)`）それぞれの直後に加えます。ホストの時計ではなく、
端末自身の時計（`adb shell date`）から起動時刻を読みます。起動の目印を、あとで
端末自身の時計による読み取りとだけ比較するのであれば、ホストと端末の時計を
すり合わせる必要はありません。`app_crash_artifacts()` は2つの層で実装します。
両方を取得するという、本項目自身が決めた対象範囲に合わせたものであり、片方が
失敗しても他方を巻き込まないよう、それぞれ独立して例外を捕まえます。

1. **`logcat` のクラッシュバッファ**は、常に試みます。昇格した権限は要りません。
   `logcat -b crash` は端末全体で1つの、起動をまたいで残るリングバッファです。
   [`scripts/collect_android_diagnostics.sh`](../../scripts/collect_android_diagnostics.sh)
   は、失敗した CI ジョブの終了時に、これをまるごと（`-b main,system,crash,events,radio`）
   ダンプします。したがって起動ごとにクリアすれば、そのジョブ終了時の掃引が必要とする
   証跡を壊してしまいます。ここでは何もクリアしません。あとで行う
   `adb logcat -b crash -d -t "<起動の目印>"` というダンプは、前述の端末相対な起動の
   目印を `logcat` 自身の時刻フィルタとして使うため、このシナリオが実行している起動
   より前の内容を含めず——同じパッケージが同じ端末上の以前の実行で残した古いクラッシュ
   を拾うこともなく——それでいて、その目印より前の内容はジョブ終了時の掃引がそのまま
   見つけられるように残します。ダンプは2通りの方法で解析します。マネージド
   コード（Java・Kotlin）のクラッシュを示す `FATAL EXCEPTION` ブロックと、それが
   見つからない場合に、NDK クラッシュを示すネイティブクラッシュバッファ自身の
   `Fatal signal <n>` という見出し行です。`logcat` のクラッシュバッファが実際に
   運ぶ2つの形式です。一致したほうを抽出し、`logcat-crash.txt` として書き出します。
   これは、この adb バックエンドが到達できるどの AVD や実機でも保証される唯一の
   証跡です。
2. **tombstone の取得**は、ベストエフォートで root 権限に依存し、上の `logcat` の層より
   あとに、最後に走ります。`adb root` は、このバックエンドが対象とするエミュレータ
   イメージに対しては、すでに日常的な操作ですが、`adbd` を再起動します
   （[`scripts/collect_android_diagnostics.sh:98-102`](../../scripts/collect_android_diagnostics.sh)
   はすでに `adb root` の直後に自身の `adb wait-for-device` を置いており、その理由を
   「adbd restarting as root」と述べています）。その再起動は、単なる `adb forward` の
   対応づけだけでなく、レジデントサーバ自身の `am instrument -w` セッションそのものを
   まるごと終わらせます（`instrument_cmd` の `-w` は「インストルメンテーションを装着したまま
   にし……`UiAutomation` のセッションを温存する」ためのフラグです、
   [`adb/_functions.py:604-616`](../../bajutsu/common/backend_cli/adb/_functions.py)）。
   BE-0283 のネットワークコレクタの `adb reverse` トンネル
   （`android_environment.py:298-306`）も同じように落とします。もう存在しないセッション
   へポートを forward し直しても、それを回復することにはなりません。レジデントサーバ
   自体を再起動することだけが唯一の直し方であり、この層はそこまでは行いません。ただし、
   `_run_on_lease` のこの時点より後には、どちらのチャネルも必要とするものが何もありません。
   シナリオはすでに終わっており、`lz.release()` までに残るのはネットワークのスナップ
   ショット書き込みと進捗行だけで、どちらも `lz.collector` がすでに取り終えたデータを
   読むだけで、ドライバを経由し直すことはありません。`AndroidEnvironment.start()` は、
   どのリースでもレジデントサーバと reverse トンネルを一から組み立て直します
   （`_begin_resident`、`bridge_collector`）。「温存されたレジデントは残さない」という、
   本項目がすでに前提としているふだんの解体・再構築です。したがって、この層が壊した
   ものによって、この端末上の次のリースが影響を受けることはありません。そのあとで
   もっとも新しい `/data/tombstones/tombstone_NN` のうち、更新時刻が前述の起動の目印
   以降のものを1件取得します。比較は端末自身の相対的な時刻どうしで行うため、ここでも
   時計のすり合わせは不要です。実機、user ビルド、`adb root` を拒む状態のいずれでも、
   この層は黙ってスキップします。事象自体は報告済みであり、`logcat-crash.txt` はすでに
   届いています。root を拒む端末が失うのはマネージドコードのクラッシュがそもそも必要と
   しないネイティブフレームの詳細だけであり、このシナリオより後のあらゆるシナリオは、
   もともとそうであったのと変わらず新しいレジデントサーバを得ます。

   この「この時点より後にはどちらのチャネルも必要とするものがない」という正当化は、
   `run` の1シナリオごとのリースに限った話です。リースは毎回まっさらに解体・再構築
   されます（`pool.py`）。`crawl` はこれに当てはまりません。「`crawl` 自身のクラッシュ
   記録を拡張する」節は、この同じ `env.app_crash_artifacts` をクロールのループへその
   まま通しますが、クロールのレーンは自身の巡回全体を通じて*1つ*の環境しか持ちません
   （`_build_lane`、[`cli.py:294-300`](../../bajutsu/crawl/cli.py)）。フロンティアの
   途中でクラッシュが着地しても巡回は終わっていません
   （`current_fp = None; continue`、
   [`_functions.py:668-669`](../../bajutsu/crawl/core/_functions.py)）。したがって、
   最初に確定した Android のクラッシュで tombstone を取得すれば、同じレーンのそれ以降の
   あらゆる確認が壊れ、しかもクロール自身が終わるまでレジデントサーバや reverse
   トンネルを組み立て直すものが何も残りません。この層はそこで `run` エントリポイント
   だけにゲートします。`AndroidEnvironment` はコンストラクタ時のフラグを持ちます
   （`environment_for` を通じて渡し、`_build_lane` のクロールレーン構築だけがこれを
   立てます）。`app_crash_artifacts()` は root 権限に依存する取得を試みる前にこれを
   確認し、立っていれば `logcat` の層の結果だけへそのまま進みます。`adb root` も、
   セッションを終わらせることもありません。`crawl` 自身の Android 収集は `logcat`
   だけになり、`run` は両方の層を持ち続けます。

### 失敗したシナリオの run ディレクトリへ収集をつなぐ

事後確認は経路の内側で動作し、他のあらゆる終端失敗がすでに通る同じステップループの
内側にとどまります。したがって `run_scenario` のふつうの `RunResult` 組み立ては、この
確認のあともそのまま変わらず走ります。失敗したステップ自身のスクリーンショット、すでに
完了したステップ、シナリオレベルの `after: on: fail` ルールは、すべて `ElementNotFound`
が同じステップを失敗させた場合とまったく同じように残ります。シナリオ全体に対してすでに
動作しているビデオ録画も、同じように停止して添付されます。`AppCrashedError` は送出される
例外として `run_scenario` の外へ出ないため、エスケープした `BackendCrashError` に対して
だけ発火する `pipeline.py` 既存のクラッシュリトライループは、これを一度も見ません。
シナリオは、他のあらゆる終端ステップ失敗と同じように、リトライを止めるための特別扱いを
何も要らずに一度だけ失敗します。

ただし、証跡のコピーはその経路の内側にとどめません。BE-0421 自身のコピーが経路の外に
あるのと同じ理由からです。`_step_runner` のシンク
（[`bajutsu/common/orchestrator/loop/_loop_config.py`](../../bajutsu/common/orchestrator/loop/_loop_config.py)）
は `EvidenceSink` であり、その表面全体は `capture` / `wait_diagnostic` / インターバルの
開始・終了の組だけです。任意の名前での書き込みを持ちません。しかも実行中のシナリオに
スコープされており、run スコープの `RunArtifactWriter` と、`pipeline.py` がすでに保持
する `sid` を必要とするクラッシュの証跡には向きません。`Lease`
（[`bajutsu/common/runner/types.py`](../../bajutsu/common/runner/types.py)）に
`app_crash_artifacts: Callable[[], list[tuple[str, bytes]]]` を加え、`crash_artifacts`
がすでにそうしているのと同じように、モジュールレベルの no-op を既定値にして `pool.py`
の `lease()` クロージャでその隣に配線します。環境のメソッドを直接読み、スナップショットと
サンクによる間接参照を挟みません。バックエンドクラッシュと違い、この呼び出しより前に、
この環境が解体されたり別のリースへ渡されたりすることがないからです。

`pipeline.py` の `_run_on_lease` は、`run_scenario` が返った直後、自身の `finally` が
リースを解放するよりも前、まだそのリースを保持したまま
`(*result.before_outcomes, *result.steps, *result.after_outcomes)` を `app_crashed` で
走査します。`result.steps[-1]` を読むのではありません。入れ子になったクラッシュは、
その外側の `if`・`forEach` の outcome を、クラッシュした本人の outcome より*あとで*
確定させるため（`_step_runner.py:220`、`:238`）、クラッシュした outcome が最後にある
とは限りません。また `result.steps` だけでは `before` と `after` の両フェーズを
まるごと見落とします。`RunResult.steps` は本体フェーズ自身のリストにすぎず
（`_functions.py:851`）、`before` のステップが失敗すると本体ステップの実行自体が
スキップされるため（`_functions.py:732-740`）、`[]` のままになります。本項目の初期の
草案が、解放後に証跡を読むことで残していた競合は、これで閉じます。走査が見つけた
とき、新しい `_write_app_crash_artifacts(lz, s, sid)` が
`_write_crash_artifacts`（BE-0421、`pipeline.py:803`）をほぼそのまま真似ます。
`lz.app_crash_artifacts()` を呼び、返ってきた `(name, content)` の組をそれぞれ
`writer.write_text(f"{sid}/app-crash/{name}", content.decode(errors="replace"))` という、
マスキングを行うテキスト側の経路で書き込みます。`write_bytes` ではありません。BE-0421
自身のコピーがそちらを使う理由と同じです。クラッシュレポートはテキストであり、
クラッシュしたアプリがそこへ秘密の値を反映させることもあります。そして
`_write_crash_artifacts` が `pipeline.py` に追記させるために返すのと同じ形で、
ディレクトリを名指しする一節を `result.failure` へ追記します。書き込みの問題は
ログに記録するだけで、送出しません。同じ姿勢に合わせたものです。診断のための収集が、
すでに確定した失敗を別の失敗へすり替えてはなりません。これは素の事後確認であり、
新しい `except` 節ではありません。シナリオ自身のリトライの挙動は、これが走る時点で
すでに確定しています。

### `crawl` 自身のクラッシュ記録を拡張する

`crawl()`（[`bajutsu/crawl/core/_functions.py`](../../bajutsu/crawl/core/_functions.py)）
自身は環境を持ちません。受け取るのは注入されたコールバックだけです。`driver`、
`reset`、`is_alive`、`recover`、`on_event`、その他です。これが、クロールのコアを
`platform_lifecycle` から切り離し、同じループであらゆるバックエンドを駆動できる
ようにしています。本項目の初期の草案が提案したように、そこへ環境への参照を持ち
込めば、あらゆるバックエンドと、高速なテストスイートのあらゆる fake がそれを1つ
持つ必要に迫られます。そもそも `is_alive` には、その参照を紐づけるべきシグナルが
ありません。`ios.py` と `android_environment.py` はどちらも `crawl_aliveness()` に
`None` を返しており、そのコメントいわく「デバイスのクラッシュ検知にはエンジンが
アクセシビリティツリーを読む」ためです。つまり本項目が対象とする両バックエンドで
は、`crawl()` に `is_alive` コールバックがそもそも渡りません。
[`bajutsu/crawl/cli.py`](../../bajutsu/crawl/cli.py) には `CrawlEnvironment` が1つ
（`plan.environment`）スコープ内にありますが、これは前述の健全性確認の配線だけの
ために空の `udid`（`environment_for(actuator, "")`）で構築されており、実際に
クラッシュが起きたデバイスではありません。クロールはレーンごとに1つの環境を
持ちます。`--udid` ごとに（BE-0064）、その `driver`・`reset` と一緒に `_build_lane`
で組み立てられます。複数レーンのクロールで `plan.environment` を使えば、誤った
デバイスの診断情報を集めるか、何も集められません。

そこで収集は、`driver` や `reset` とすでに同じ方法で配線します。`_build_lane` が
3つ目の戻り値として、そのレーン自身の `env.app_crash_artifacts` を返すようにし、
各追加レーンでは `WorkerFactory`（`bajutsu/crawl/core/_functions.py`）を通じて、
最初のレーンでは `crawl()` 自身の主レーン向けパラメータを通じて、driver・reset と
一緒に運びます。`record_crash` の呼び出し箇所（`bajutsu/crawl/core/_functions.py:667`）
は、すでにコーディネータのロックの外で `crashed = not alive(d, landed)` を計算して
います。その `alive` は、本項目が対象とする両バックエンドで UI ツリーのヒューリスティック
`is_app_alive` へ落ち、その誤検知のリスクは「はじめに」ですでに述べたとおりです。
システムアラートや、意図的な `background` ステップです。`run` 自身の上限つき
`.ips`・tombstone のポーリングがその待ちに値するのは、`app.state == notRunning` や
`pidof` とexit-info の確認が先に事象を確定させているからです。`crawl` にはそうした
確認がありません。したがってここでの誤検知は毎回、全タイムアウト分ポーリングします。
しかもそれは稀なケースではなくよくあるケースです。`crawl` は `record_crash` の直後、
`current_fp = None; continue`（`_functions.py:668-669`）でクラッシュを記録したあとも
止まらないため、同じ誤検知に1回のクロールの中で繰り返し踏み込みかねません。そこで
本項目の収集呼び出しは、同じロック外の窓の中でその既存の確認に加わりますが、ドライバ
が事象を積極的に確認したときにだけ、そのワーカー自身のレーンに紐づく
`env.app_crash_artifacts()` を呼びます。`isinstance(d, base.AppCrashSignal)` と、
`d.app_crash_signal()` が `None` でないこと、どちらも両バックエンドがすでに `run`
向けに実装しているケイパビリティです。`crawl` 自身の検知はどちらの場合も変わりません。
`Crash` は UI ツリーのヒューリスティックだけで記録され続けます。ゲートされるのは収集
だけです。したがって未確認のクラッシュは、全タイムアウト分のポーリングを払う代わりに、
`artifacts` を持たない `Crash` を記録します。ここで収集する理由は、正しさだけでなく
並行性のためでもあります。`record_crash` は本体全体にわたって、コーディネータの
`self._cond` を保持し続けます。その同じロックは `on_event`（`_coordinator.py` の
`_emit`）と、他のあらゆるワーカー自身の `record_crash` / `record_edge` 呼び出しも
直列化します。数秒かかる `.ips` のポーリングや tombstone 取得を、そのロックの*内側*
で走らせれば、その間、他のあらゆるクロールレーンを止めてしまいます。先に走らせ、
すでに解決済みの結果を渡せば、収集がロックに費やすコストはふつうのリスト追加と
変わりません。

`Crash`（[`bajutsu/crawl/core/crash.py`](../../bajutsu/crawl/core/crash.py)）に
`artifacts: tuple[tuple[str, bytes], ...] = ()` というフィールドを加え、`record_crash`
はそれを `path` と一緒に受け取って保持します。
[`bajutsu/crawl/repro.py`](../../bajutsu/crawl/repro.py) の `write_repros` が
——それを呼んで件数を echo するだけの `cli.py` の `_finish` ではなく——すでに
`screen_map.crashes` を順に歩き、それぞれの `crashes/crash-NNN.yaml` 再現シナリオを
書き出しています。`NNN` は、そのリストの中でのそのクラッシュ自身の、0埋めで1始まりの
番号です（`enumerate(screen_map.crashes, start=1)`、`repro.py:154`）。その番号づけを
所有しているのはこのループなので、証跡を書き込む手順もここに加えます。`_finish` が
同じ番号を別のファイルで再導出するのではありません。番号づけの一方だけを変えれば
静かにずれてしまう、重複したロジックの形になってしまうからです。`write_repros` は
また、忠実に再現できないパス（`tap_point` アクションには対応するセレクタがない）を
`continue` で飛ばし、`.yaml` を書き出しません。証跡の書き込みはその `continue` より
*前*に置きます。そうしなければ、再現できないクラッシュ——再現シナリオの代わりが
ないぶん、プラットフォームのレポートがもっとも価値を持つ種類のクラッシュ——が、
自身の再現ファイルと一緒に証跡まで失ってしまいます。空でない `artifacts` は
`crashes/crash-NNN/app-crash/` の下へ書き込みます。このパスは再現ファイルの隣にあり、
同じ名前を持つ最上位のディレクトリではないため、2つは並んで見つかり、並んで
ソートされます。クロール自身の検知は、すでに使っているUI ツリーのヒューリスティック
のままです。`crawl` には、`run` と違って、事後確認をぶら下げるシナリオステップが
ありません。2つの入口のあいだで共有されるのは、iOS では収集です。Android では
`logcat` の層だけが共有され、root 権限に依存する tombstone の取得は `run` 限定です
（「Android：まず `logcat` のクラッシュバッファ、次に……」を参照）。検知はどちらの
場合も共有しません。

`Crash` は、[`serialize.py`](../../bajutsu/crawl/serialize.py) が JSON へ往復させる型でも
あります。`screenmap_dict` が `screen_map.crashes` を書き出し(`:131-134`)、
`screenmap_from_dict` がそれを組み立て直します(`:92-98`)。生の `bytes` には JSON 表現が
ないため、他のすべてのフィールドの書き出しを base64 で膨らませるのではなく、`artifacts`
をあえてどちらの方向からも除きます。`on_event` 自身の `_write_screenmap` 呼び出し
(`cli.py:186`)は、クラッシュを記録するたびにその書き出しをその場で実行します。
`write_repros` はこれよりずっとあとに、一度だけ走ります。完走したクロールの終わりで、
確定した `screen_map.crashes` を一度だけ歩きます(`cli.py:405`)。つまり `artifacts` は、
書き出しのあるなしにかかわらず、そもそもこの一度きりの呼び出しより前には何も永続化
されないインメモリだけのフィールドです。書き出しから除いても、正常に完走した先行
クロールがすでに失うものはありません(`write_repros` が走ったので、そのバイト列は
その先行クロールの `crashes/crash-NNN/app-crash/` の下にすでにディスクへ書かれて
います)。中断されたクロールが失っていたはずのものもありません(プロセスは
`write_repros` へ届く前に落ちており、そのバイト列はそもそもディスクへ届いて
いません。これは `--resume` がその先行クロール自身の書かれなかったレポートについて
すでに受け入れている損失と同じです)。`screenmap_from_dict` は、引き継がれるすべての
`Crash` を `artifacts=()` というデータクラスの既定値で組み立て直します。クラッシュが
まだ `actions` を持たなかった時代に保存されたマップに対して、すでに `actions` へ
行っているのと同じ扱いです。

### 本物のクラッシュで、スタブだけでなく証明する

ユニットテストは `~/Library/Logs/DiagnosticReports` や、疑似的な `logcat`・tombstone 取得をスタブ
できます。しかし、この設計が前提とする基盤側の仕組み、`ReportCrash` 自身の `.ips` 書き出しや
`logcat` のクラッシュバッファが、実際の Simulator やエミュレータ上でも同じように振る舞う
ことまでは証明しません。showcase アプリ
（[`demos/showcase/`](../../demos/showcase)）に、「強制的にクラッシュさせる」操作を追加します。
ビルド構成ではなく、起動時の環境変数フラグでゲートします。showcase の iOS ソースには
`#if DEBUG` がどこにもなく、もっとも近い既存の先例
[`ConformanceView.swift`](../../demos/showcase/ios/swiftui/Sources/ConformanceView.swift)
自体も、`SHOWCASE_CONFORMANCE` という起動時環境変数が設定されているときにしか到達しません
（[`AppModel.swift:92`](../../demos/showcase/ios/swiftui/Sources/AppModel.swift)、
[`RootView.swift:7`](../../demos/showcase/ios/swiftui/Sources/RootView.swift)）。オンデバイス
診断ではなく、BE-0114 のドライバ適合性画面です。ビルド構成でゲートしてしまうと、この操作が
そもそも存在するかどうかを、iOS レーン自身の `build (app + runner)` ジョブがどちらの構成で
ビルドするかに委ねてしまいます。Release ビルドはこれをコンパイルから除外してしまい、新しい
「失敗することが期待値」のシナリオは、クラッシュではなくセレクタが見つからないという理由で
失敗することになります。まさに本項目が取り除こうとしている誤診断そのものです。起動時環境変数
のフラグであれば、ビルド構成についての前提を必要とせず、`SHOWCASE_CONFORMANCE` がすでに
乗っているのと同じ、シナリオスキーマ自身の `preconditions.launchEnv` に乗ります。この操作は
iOS では `fatalError()` を、Android では main スレッドで未捕捉の例外を送出します。各
プラットフォームに1本、これをタップする新しいシナリオを追加します。

新しい2本のシナリオは、どちらも*失敗することが期待値*です。それらを囲む CI のラッパーが、
失敗が新しいアプリクラッシュの分類を運んでいること、`app-crash/` が期待どおりのファイルを
保持していることを検証します。`fault-injection (xcuitest)` がすでに、グリーンな実行ではなく
診断済みの失敗の形を検証しているのと同じ方式です
（[`docs/ci.md`](../../docs/ci.md#the-ios-lane)）。`ios-e2e.yml` / `android-e2e.yml` の中で、
`fault-injection (xcuitest)` や `network (adb)` の隣に、ゲートしない PR ごとのシグナルとして
配置します。オンデバイスで新しく配線されたカバレッジは、必須の `E2E` チェックへ昇格する前に、
まずそこで安定性を得ます。両レーンの他のあらゆる新しいシグナルが辿ってきたのと同じ道です。

### web backend と fake backend への影響

これらは別々の2つの継ぎ目であり、どちらのバックエンドもどちらの半分にも意味のある
作業は要りません。`PlaywrightDriver` は `AppCrashSignal` を実装しないため、事後確認の
`isinstance` による問い合わせは `False` を返し、プロトコルを一切宣言しない他のあらゆる
ドライバと同じように読み飛ばされます。`WebEnvironment`
（[`bajutsu/common/platform_lifecycle/environments/web.py`](../../bajutsu/common/platform_lifecycle/environments/web.py)）
は自前の `app_crash_artifacts()` を宣言し、`[]` を返します。すでに `take_crash_snapshot()`
に対して持っている宣言の隣に加わる1行です。`RunEnvironment` はどの具象クラスも継承しない
構造的プロトコルであり、どちらのメソッドも継承によって落ちる先となる既定値を持たない
ためです。`FakeEnvironment`
（[`bajutsu/common/platform_lifecycle/environments/fake.py`](../../bajutsu/common/platform_lifecycle/environments/fake.py)）
は `_DeviceEnvironment` から同じ no-op を継承し、テスト用の fake ドライバも
`AppCrashSignal` の継ぎ目では同じ振る舞いをします。本項目によって、web backend や
fake backend の実行が収集する内容は変わりません。

## 検討した代替案

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| 既存の `BackendCrashError` 回復ループでリトライする | クラッシュ検知をバックエンドクラッシュと同じ扱いにし、リースを破棄してリトライする | 壁打ちで却下しました。この事象は、一時的なインフラの不調ではなく、アプリ自身の不具合である可能性が高いためです。再起動してのリトライは、再び失敗する見込みが高いシナリオにクラッシュ回復の予算を費やし、本物の不具合を flakiness として吸収してしまう危険があります（BE-0049）。 |
| 毎ステップの前に `app.state`・プロセスの生存を事前にポーリングする | 各ステップの実行前に、アプリがまだ動作しているかを確認する | 壁打ちで却下しました。構造上まれな失敗モードを捉えるためだけに、グリーンな実行を含むあらゆるシナリオのあらゆるステップへドライバの往復を1回追加してしまうためです。事後確認の設計でも、その事象が起きたまさにそのステップで捕まえられます。そのステップ自身のアクションかクエリが、すでに失敗しているからです。 |
| `AppCrashedError` を `run_scenario` の外へ送出し、`pipeline.py` に `BackendCrashError` を真似た専用の `except` 節を設ける | バックエンドクラッシュと同じ扱いで、リトライループへ入れる | レビューで却下しました。バックエンドがクラッシュしたシナリオと違い、アプリがクラッシュしたシナリオでは、ドライバもバックエンドプロセスも、動作中のビデオ録画も残ります。落ちているのはアプリだけです。別経路の例外は、`run_scenario` 自身の組み立てが他のあらゆる終端失敗に対してすでに生み出している、ステップ・証跡・`after: on: fail` の発火を、ゼロから作り直す終端 `RunResult` で捨ててしまいます。 |
| `app_crash_signal()` を `Driver` プロトコルの必須メンバーにする | 個別の opt-in プロトコルではなく、`Driver` 自身にメソッドを1つ加える | レビューで却下しました。`Driver` は `@runtime_checkable` であり、あらゆる実装（`XcuitestDriver`、`AdbDriver`、`PlaywrightDriver`、`XcuitestLiveDriver`、fake backend、`WebContextDriver` のような狭いラッパー）がスタブを持つ必要に迫られます。本項目が確認するつもりのないバックエンドも例外ではありません。このコードベースがすでに `InterruptionPolicyTarget` や `SettledReadProvider` に使っている、狭い opt-in のケイパビリティプロトコルであれば、必要とする2つのバックエンドだけに届きます。 |
| Android で `logcat` のクラッシュバッファだけを使い、tombstone は取得しない | ネイティブクラッシュの取得を諦め、常に取得できる `logcat` だけに頼る | 却下しました。ネイティブ（NDK）クラッシュの完全なバックトレースを失い、`logcat` 自身が出力する簡略化された要約しか残らないためです。常に取得できる基盤としては残し、tombstone の取得はそれを置き換えるのではなく、端末が許す場合により豊かな詳細を上乗せします。 |
| Android で root 権限に依存する tombstone 取得だけを使い、`logcat` へのフォールバックを持たない | tombstone の取得だけに頼り、`logcat` の抽出は実装しない | 却下しました。実機、user ビルド、`adb root` を拒むエミュレータイメージでは、何も取得できなくなってしまうためです。`logcat` のクラッシュバッファは昇格した権限を必要とせず、よくあるマネージドコードのクラッシュについてすでに完全なスタックトレースを運びます。 |
| 新しいシナリオアサーション（例：`assert: appCrashed: false`）を追加する | シナリオ作者が明示的に「アプリがクラッシュしていないこと」を検証できるようにする | 却下しました。この事象は、ステップ自身のアクションかクエリの失敗によって、すでにシナリオを終わらせているためです。それを確認するアサーションが後から走れる時点は、シナリオの中に残っていません。showcase 自身のテスト用シナリオは、代わりに、失敗の*形*を run の外側から検証します。`fault-injection (xcuitest)` がすでに採っている方式と同じです。 |
| `video`・`deviceLog` と同様、`capturePolicy` の opt-in ルールの背後に収集を隠す | 明示的な指定がない限り収集を行わない | 却下しました。BE-0421 が自身の証跡について挙げた理由と同じです。この収集は、すでに失敗が確定したシナリオに対して一度だけ走ります。コストは、範囲の定まった掃引かログの読み取り1回であり、明示的な要求の背後へ隠すべき定常的なステップごとの負荷ではありません。 |
| BE-0421 がバックエンドクラッシュ自身の収集を遅らせているのと同じ形で、照合条件を凍結し `take_crash_snapshot()` 風のサンクで掃引を遅延させる | 収集をその場で実行せず、リース解放のタイミングまで遅らせる | 却下しました。その間接参照が存在するのは、プールがリースを解放するより前に、同じ温まったプールされた環境を別のワーカーが再利用してしまう事態を切り抜けるためだけです。本項目自身の収集は、クラッシュの起きたまさにそのリースを `pipeline.py` がまだ保持したまま同期的に走ります。解放や再利用よりずっと前であり、遅らせるべき競合がそもそもありません。 |

## 進捗

> 作業の進行に合わせて最新の状態に保ってください。チェックリストは「どう実現するか」の
> MECE な作業分解をそのまま反映します（作業の単位ごとに1項目）。ログは変更内容とその日時を
> 古い順に記録し、PR にリンクします。

- [ ] Unit 1 — `base.AppCrashedError`（新規ファイル）。`Driver` プロトコルとは別に設ける、
      `base.AppCrashSignal` というケイパビリティプロトコル（`app_crash_signal() -> str |
      None`）。新しい `StepOutcome.app_crashed: bool = False` フィールド。
- [ ] Unit 2 — iOS：`XCUIApplication.state` を読む新しい `openapi.yaml` のルートと、
      生成された `APIHandler` のメソッド。`Router.swift` ではなく `RunnerServer` から
      配信します。`XcuitestDriver.app_crash_signal()` が `AppCrashSignal` を実装し、
      `notRunning` をシグナルとして分類し、チャンネルエラーは `XcuitestRunnerCrashError`
      としてそのまま伝播させます。新しい `is_real_device` コンストラクタ引数を
      `device_os` と同じ方法で `make_driver` から通し、`deviceType: device` では
      `app_crash_signal()` がその場で `None` を返すようにします。本項目は Simulator だけを
      対象にします。
- [ ] Unit 3 — iOS：コールド起動の直後に記録する `XcuitestEnvironment.app_launched_at`。
      新しい `XcuitestEnvironment.relauncher()` オーバーライドが `device_relauncher` の
      `RelaunchFn` を包み、`relaunch` ステップ自身の起動のあとにこれを記録し直します
      （`_DeviceEnvironment` が継承する `relauncher()` には、それを更新すべき環境がその場に
      ない、まさにその呼び出し箇所）。対応する `XcuitestEnvironment.crawl_reset()`
      オーバーライドも同じ形でこれを3回目記録し直します。`crawl` 自身のフロンティア再訪ごとの
      relaunch のあとで、`_DeviceEnvironment` が継承する `crawl_reset()` にも、それを更新
      すべき環境がその場にない、もう1つの呼び出し箇所です。`_resume_warm`
      （BE-0291 のリースをまたぐウォーム再利用の起動、`xcuitest_environment.py:855-856`。
      `start()` が再利用可能なたびに戻る、同じ長命の `XcuitestEnvironment` インスタンスの
      上で走ります）自身の中でも、その `e.launch` の隣で4回目記録し直します。そうしなければ、
      ウォーム再利用されたリースのクラッシュが、目印がたまたま古いタイムスタンプを共有する
      どこか前のリースの `.ips` レポートと一致してしまいかねません。掃引自身の照合パターンのために
      `Path(ios.app_path) / "Info.plist"` から `CFBundleExecutable` を読みます
      （`ios.bundle_id` はこの名前ではない）。`app_crash_artifacts()` の、名前と `udid` に
      よる `.ips` 掃引（`XCUIApplication` には PID を読む手段がない）。`ReportCrash` の
      非同期な書き込みに対する上限つきの待機を含み、失敗はすべて `[]` へ解決するよう包みます。
- [ ] Unit 4 — Android：`backends.make_driver` から `AdbDriver.__init__` へ、`device_os` と
      同じ方法で通す `package` キーワード。`AndroidEnvironment.app_launched_at` を読む、
      注入された `launched_at` コールバック。`adb shell pidof <package>` による
      `AdbDriver.app_crash_signal()` を、時刻で絞り込んだ `adb shell dumpsys activity
      exit-info <package>`（最新の項目のみ、`launched_at()` 以降）で裏付けます。
- [ ] Unit 5 — Android：各起動の箇所での `AndroidEnvironment.app_launched_at`（端末自身の
      時計）。`app_crash_artifacts()` の、常に試みる `logcat` 抽出（マネージドコードと
      ネイティブの両形式）は、クラッシュバッファをクリアする代わりに `-t "<起動の目印>"`
      という時刻フィルタを使い、`scripts/collect_android_diagnostics.sh` 自身のジョブ終了時
      掃引がそれ以前の内容を引き続き見られるようにします。ベストエフォートで root 権限に
      依存する tombstone 取得は最後に走り、`adb root` がレジデントサーバの `am instrument -w`
      セッションと BE-0283 の `adb reverse` トンネルをまるごと終わらせることを受け入れます。
      再確立はしません。このリースのこれ以降の処理はどちらのチャネルも必要とせず、プールが
      次のリースで両方を一から組み立て直すからです。ただし、この層は `run` エントリ
      ポイント限定でゲートします。`environment_for` を通じて渡す、`AndroidEnvironment` の
      コンストラクタ時フラグによってです。クロールのレーンは組み立て直す次のリースを
      持たないからです。それぞれ独立して失敗を `[]` へ解決するよう包みます。
- [ ] Unit 6 — `RunEnvironment.app_crash_artifacts()` のプロトコルの形（`list[tuple[str,
      bytes]]` を返し、この環境の解放前にスナップショットとサンクによる間接参照なしでその場
      で読みます）。`WebEnvironment` と `_DeviceEnvironment`(`FakeEnvironment` が継承)に加える
      1行の `return []`。`take_crash_snapshot()` がこの3クラスすべてにすでに持つ no-op 宣言と
      同じ形ですが、Android だけは対応する no-op を持ちません。`AndroidEnvironment` は
      `app_crash_artifacts()` を本物の収集(Unit 5)でオーバーライドしており no-op ではないから
      です。`XcuitestEnvironment` が自前の本物の収集(Unit 3)でオーバーライドするのと同じ
      理由です。`pool.py` の `lease()` クロージャを通した、`crash_artifacts` の隣への
      `Lease.app_crash_artifacts` の配線。
- [ ] Unit 7 — `run_scenario` / `_step_runner.py`：新しい `_finish_outcome` ヘルパーを、
      `self.state.outcomes.append(outcome)` の5つの呼び出し箇所すべて（`_handle_if` /
      `_handle_for_each` / `_handle_web` はそれぞれ1回、`_handle_action` は自身の終端と
      `UncoveredSystemAlertLocale` の早期リターンの2回）で、素の append の代わりに呼ぶよう
      にし、あらゆる種類のステップの本当の最終結果を覆います。シナリオスコープのオブジェクト
      （`run_scenario` が1回だけ作り、`live_bindings` と同じ方法であらゆる `run_phase`
      呼び出しに共有し、凍結された `_LoopConfig` ではなく `bindings` の隣、
      `StepLoopState` に置くことで `before`・本体ステップ・発火するあらゆる `after` の
      規則をまたいで生き残る）が、2つのラッチを
      運びます。1つは意図的終了フラグであり、`outcome.action == "relaunch"` かつ
      `outcome.ok is False` を見た瞬間（確認より前に）立ち、それ以降の同じシナリオの
      あらゆる確認を抑えます。抑える範囲は `relaunch` ステップ自身の outcome だけには
      とどまらず、それを包む `if`・`forEach` の outcome や、発火する `after: on: fail`
      の後片付けも含みます。もう1つは確定済みクラッシュのラッチであり、`_finish_outcome`
      が `AppCrashedError` を送出し捕まえた最初の時点で立ち、同じ伝播の中であとに続く
      outcome が、そのすでにわかっているシグナルを自身の `outcome.reason` へ折り込む
      だけにする——`app_crash_signal()` の確認を relaunch と確定済みクラッシュのケースに
      限って抑えるものであり、クラッシュを伴わないふつうの失敗は確定する outcome ごとに
      1回の確認を払い続けます。その1点で `AppCrashedError` を送出し捕まえ、そのメッセージを
      `outcome.reason` へ折り込み、新しい `StepOutcome.app_crashed` フィールドと確定済み
      クラッシュのラッチを立てます。`_finish_outcome` の外に `self.state.outcomes.append`
      が残っていないことを検証する高速スイートのテストを加えます。
- [ ] Unit 8 — `pipeline.py`：`_run_on_lease` が `run_scenario` の直後、まだ同じリースを
      保持したまま `(*result.before_outcomes, *result.steps, *result.after_outcomes)` を
      `app_crashed` で走査します（`result.steps[-1]` ではない）。`_write_crash_artifacts`
      （BE-0421、`pipeline.py:803`）を真似た新しい `_write_app_crash_artifacts(lz, s, sid)`
      が、各証跡をマスキングを行う `writer.write_text` の経路で書き込み、ディレクトリを
      名指しする一節を `result.failure` へ追記します。
- [ ] Unit 9 — `TracingDriver`：`base.AppCrashSignal` を `_PROTOCOLS` へ加え、
      `--trace-driver` がそれを実装したドライバに対してだけ実属性として設置するようにします。
- [ ] Unit 10 — `crawl` 自身の統合。`_build_lane` のレーンごとの `app_crash_artifacts` を、
      `driver`・`reset` と同じ方法で `WorkerFactory` と `crawl()` の主レーン向けパラメータへ
      通します。`_build_lane` の `environment_for` 呼び出しに新しいクロールレーンフラグを
      渡し、Android の tombstone 取得の層を巡回全体で止めておきます（Unit 5）。収集呼び出しを `record_crash` の既存のロック外クラッシュ確認へ加えます。
      ドライバが事象を積極的に確認したとき（`isinstance`/`app_crash_signal()`）にだけ収集する
      ようゲートし、UI ツリーの誤検知が全タイムアウト分の掃引を払わないようにします。`Crash` の
      新しい `artifacts` フィールド(生の `bytes` には JSON 表現がないため `serialize.py` の
      `screenmap_dict`/`screenmap_from_dict` の往復からはあえて除き、引き継がれる `Crash` は
      常に `artifacts=()` で組み立て直されます)。`repro.py` の `write_repros` が、空でない証跡を、
      再現できないクラッシュをスキップする自身の `continue` より前で
      `crashes/crash-NNN/app-crash/` へ、そのクラッシュ自身の `crashes/crash-NNN.yaml`
      再現ファイルの隣に書き込みます。
- [ ] Unit 11 — showcase の準備。ビルド構成ではなく起動時環境変数のフラグで隠す
      「強制的にクラッシュさせる」操作を iOS（SwiftUI）と Android（Compose）それぞれに用意し、
      `preconditions.launchEnv` を通じてそれを起動する各プラットフォーム1本のシナリオ——
      クラッシュのトリガーで終わらせず、死んだアプリに対してもう1ステップを置きます。
      最後のステップがクラッシュのトリガーであるシナリオは、本項目自身の反応的な確認
      設計（「検知の方式」を参照）によりそもそも失敗しないからです——を、
      `ios-e2e.yml` / `android-e2e.yml` へのゲートしない PR ごとのシグナルとして配線します。
- [ ] Unit 12 — ドキュメント。`docs/evidence.md`（および `docs/ja/`）にこの証跡の種類を追加します。
      `docs/ci.md`（および `docs/ja/`）に showcase のシグナルレーンを追記します。
      `docs/architecture.md`（および `docs/ja/`）に、既存のバックエンドクラッシュのリトライ
      節と、この項目のリトライなしの経路を相互参照させます。
- [ ] Unit 13 — テスト。両バックエンドで、ふつうの `ElementNotFound` や `wait`・`assert` の
      失敗、そして `app.state` の答えに関わらず `deviceType: device` に対して
      `app_crash_signal()` が `None` を返すこと（誤検知しないこと）。`relaunch` ステップ
      自身の起動を過ぎて、`crawl` が駆動する `crawl_reset()` 自身の起動を過ぎて、また
      `_resume_warm` のクロスリース再利用自身の起動を過ぎても
      `XcuitestEnvironment.app_launched_at` が進むこと、それぞれの `.ips` 掃引が、
      もっとも新しいその起動のレポートだけを見つけ、以前のシナリオやクラッシュのもの
      ではないこと。クロールレーンの `AndroidEnvironment` は、クラッシュを確定しても
      root 権限に依存する tombstone 取得をまったく試みないこと（`logcat` の層は走る）。
      `run` でリースされた方は引き続き試みること。失敗した
      `relaunch` ステップ自身が `app_crash_signal()` をまったく確認しないこと、それを包む
      `if`・`forEach` の outcome も、終了させられたアプリに対して失敗する
      `after: on: fail` のステップも同様であること。割り込みの回復ステップ
      （`after` フェーズとは別物の、BE-0314 の `_run_recovery`）が終了させられた
      アプリに対して失敗する場合も、回復ステップごとに1回確認し、どちらのラッチも
      それを抑えないこと。3段の入れ子のふつうの（`relaunch`
      でも確定済みクラッシュでもない）失敗が、確定する outcome ごとに1回の確認を払い
      続け、ラッチがこのケースを抑えないことを固定するテスト。スタブしたディレクトリと
      スタブした `adb` の出力に対する、iOS の `.ips` 掃引と Android の `logcat`・
      tombstone 収集（Android の exit-info による裏付けを含みます）。事後確認が経路の内側に
      とどまること、`app_crashed` フィールドを立てること、入れ子になった `if`・`forEach`
      の失敗をまたいで確定済みクラッシュのラッチが保たれることを検証する
      `_step_runner.py` のテスト。`app-crash/` がマスキング済みのテキストを保持すること、
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
      シナリオの*最後の*ステップがクラッシュしても `ok=True` を報告する場合、
      `app_crashed` outcome も `app-crash/` 証跡も持たずにグリーンのまま終わること
      ——文書化した死角をバグとして再発見される前に固定するテスト。

## 参考

- [BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact-ja.md)
  （実装済み、PR [#1999](https://github.com/bajutsu-e2e/bajutsu/pull/1999)） —
  本項目が補完し、直接再利用するランナー自身のクラッシュレポート収集。再利用先は
  [`xcuitest/_functions.py`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/_functions.py)
  の `_reports_since()`・`_reported_pid()` と、`pipeline.py` の
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
- [`bajutsu/common/platform_lifecycle/protocols/run_environment.py`](../../bajutsu/common/platform_lifecycle/protocols/run_environment.py) —
  `app_crash_artifacts()` が加わるプロトコル
- [`bajutsu/common/platform_lifecycle/relaunchers.py`](../../bajutsu/common/platform_lifecycle/relaunchers.py) —
  `device_relauncher`。`relaunch` ステップの実際の iOS 起動経路であり、`_resume_warm` の
  リースをまたぐ経路とは別物
- [`bajutsu/common/platform_lifecycle/environments/ios.py`](../../bajutsu/common/platform_lifecycle/environments/ios.py) —
  `_DeviceEnvironment.crawl_reset()`。`app_launched_at` を最新に保つため、`relauncher()` と
  並んで `XcuitestEnvironment` がオーバーライドする、iOS の3つ目の起動箇所
- [`bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py) —
  `_resume_warm`。BE-0291 の、iOS の4つ目の起動箇所。リースをまたぐ同じ長命の環境の上で走り、
  本項目はここでも `app_launched_at` を記録します
- [`bajutsu/common/platform_lifecycle/factories.py`](../../bajutsu/common/platform_lifecycle/factories.py) —
  `environment_for`。`run` のプールと `crawl` の `_build_lane` がどちらも呼ぶ共有ファクトリ。
  新しいクロールレーンフラグを通し、Android の tombstone 取得を `run` 限定にゲートします
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
- [`bajutsu/common/backends.py`](../../bajutsu/common/backends.py) — `make_driver`。既存の
  `device_os` キーワードが、本項目の `package` キーワードの先例になっています
- [`docs/ci.md`](../../docs/ci.md#the-ios-lane) — `fault-injection (xcuitest)`。本項目の
  showcase シナリオが従う、ゲートしない・失敗の形を検証するという配置

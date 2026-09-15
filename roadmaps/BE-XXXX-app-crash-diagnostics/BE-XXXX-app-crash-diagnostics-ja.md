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
返すのと同じ「確認できない」という答えです。Android も同じ形で API 30 以上に限ります。
「Android：`logcat` のクラッシュ用バッファをまず読み……」が頼る裏付けのシグナル
`ApplicationExitInfo` は、それより前の API レベルには存在しないため、確認できないシグナルを
ポーリングし続けるのではなく、そこでも `app_crash_signal()` が `None` を返します。同じ収集
ロジックを、`bajutsu crawl` がすでに持つ同種の検知にも拡張します。web（Playwright）バックエンド
とその独自シグナルは、後続の項目に委ねます。理由は「検討した代替案」に記します。

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
開発者は、2つの誤った説明を消去法で潰してから探し当てるのではなく、最初から正しい
ファイルを開けるようになります。

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
参照）——そこで説明する `before_outcomes`・`steps`・`after_outcomes` にわたる走査が見つけるの
であり、`RunResult.steps[-1]` から読むのではありません。

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
割り込みの確認について与えているのと同じ性質です。あとから加わる6つ目の確定箇所が
同じ抜け穴を静かに開け直さないよう、高速スイートに1つテストを加えます。あらゆる種類の
失敗するステップをループへ実際に通し、確定するあらゆる outcome が `_finish_outcome`
を通ったことを確認する、振る舞いベースの固定です。`_step_runner.py` の中で
`self.state.outcomes.append` という文字列そのものを検索するテストではありません。
`insert`・`extend`・`+=` という綴りや、ローカルな別名経由でも見逃しません。
`_finish_outcome` は、その append の直前で
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
段落が別途勘定に入れる必要のある、確認の回数を増やす3つ目の独立した要因です。すでに落ちた
アプリに対して走る割り込みの回復ステップは、確定する outcome ごとに1回の確認を
払います。これはどちらのラッチも抑えない、ふつうの（`relaunch` でない）失敗です。
除外を `outcome.action` だけに
結びつけると、まさにこのケースを見逃します。失敗した `relaunch` 自身の outcome は
確認を飛ばしますが、それを包む `forEach`・`if` の outcome や、その後に発火する
`after: on: error` のステップは、それぞれ別の `outcome.action` を持つため、いずれも
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
シグナルを自身の `outcome.reason` へ折り込むだけになります。ただし
`outcome.app_crashed = True` を立て `app_crash_artifacts` を収集するのは、その最初の
確定した outcome だけです。あとから決着する `if`・`forEach` の外側の outcome は、
ラッチがすでに立っているのを見て、自身の `reason` へメッセージを折り込みはしますが、
自身の `app_crashed` はデータクラスの既定値（`False`）のままにし、
`capture_app_crash` もどこからも呼びません。これが `pipeline.py` のあとの走査
(「収集をつなぐ」)を曖昧にしない理由です。1つのシナリオが運ぶ `app_crashed` の
outcome は、この構造によりたかだか1つであり、走査が複数の候補から選ぶ必要は
決して生じません。ただし、確認できなかった
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
「実際には小さい」という見積もりは、確認できなかった1回の確認がラウンドトリップ
1回で済む前提です。これは iOS では成り立ちますが、Android では成り立ちません。
Android の確認できなかった確認は、1回のラウンドトリップではなく数秒の上限つき
ポーリングになるためです（後述の「Android：`logcat` のクラッシュバッファを先に……」
が、このコストをシナリオごとに抑えます）。

この反応的な形には、あとで見つかるより先に名指しておくべき、構造上の死角が1つ
あります。シナリオ自身の*最後の*ステップが原因のクラッシュで、そのステップ自身の
操作がそれでも `ok=True` を報告する場合です。アプリが落ちる前にランナーが届けた
`tap`、少し前に捉えたツリーを読んだ `assert` などです。そのステップ自身の outcome が
これを確認することはありません。そのあとシナリオが結局分類されるか、本項目が
受け入れるこの隙間のままグリーンになるかは、そのあとに何が走るかで決まります。
そのあとに走るものが必ず失敗して `_finish_outcome` に届く保証はありません。
`scenario.after` は「`steps`・`expect` から抜けるあらゆる経路で」到達します
（[`_functions.py:821`](../../bajutsu/common/orchestrator/loop/_functions.py)。
取り消された経路も含みます）。したがって、シナリオがそれを宣言しているときにだけ
存在します。`scenario.expect` が走るのも、それを宣言しており、かつ
`failure is None` のときだけです。どちらも宣言しないシナリオは、まさに本項目の
意図どおりに振る舞います。クラッシュを起こしたステップのあとに何も走らないため、
`app_crashed` outcome も `app-crash/` 証跡もないままグリーンです。こうした
あらゆるシナリオについてこの隙間を閉じるには、シナリオ終了時の無条件の確認が要ります。
まさにこの反応的な設計が避けようとしているグリーンな実行すべてへのコストです。
したがって本項目は、確認のトリガーを広げるのではなく、この隙間を設計上の前提として
受け入れます。ただし、残る2つの形は同じ「グリーンのまま」という結末をたどらず、
それぞれ単独で名指す価値があります。

- シナリオレベルの `expect` は、死んだアプリに対してであっても最後のステップの
  あとに走ります。`_evaluate_expect` が生成するのは `StepOutcome` ではなく
  `AssertionResult` であるため、`_finish_outcome` にはそもそも届きません
  （[`_functions.py:741-752`](../../bajutsu/common/orchestrator/loop/_functions.py)）。
  シナリオは**赤くなりますが、未分類のまま**です。`app_crashed` outcome も
  `app-crash/` 証跡もない、ふつうの `expect:` 失敗として扱われ、グリーンには
  なりません。これは1つ目の死角の変種ではなく、本項目が同じく設計上の前提として
  受け入れる、もう1つ別の死角です。読んだ開発者の目には、クラッシュではなく
  アサーションの不一致として映ります。
- トリガーが失敗ではない `after` ルール（`on: always` または `on: success`）は、
  それでも発火し、死んだアプリに対して操作を続けます。そのあとかたづけステップ
  自身の outcome は、`_dispatch_after` が呼ぶふつうの `run_phase` の経路を通って
  `_finish_outcome` に*届きます*。したがってこの形は結局分類され、`app_crashed`
  が立ち、`app-crash/` も書き込まれます。クラッシュはトリガーとなったステップより
  1ステップ遅れて捉えられるだけで、見逃されるわけではありません。

この信号を*実際に検証する*シナリオには、`expect` や無関係な `after` ルールに
頼るのではなく、クラッシュを引き起こすステップの後にもう1ステップを置くことを
求めます（Unit 11 の showcase シナリオはどちらもそうしています。クラッシュの
トリガーをタップしたあと、死んだアプリに対してもう1ステップを進め、タップ自身
ではなく、その後のステップの方が失敗して確認される仕組みです）。クラッシュを
起こしたステップが本当に最後のステップであり、`expect` も、あとに走る
非失敗トリガーの `after` ルールも持たないシナリオだけが、この設計自身の構造により
グリーンのままです。

`_finish_outcome` が `active_driver.app_crash_signal()` を呼ぶのは、どちらのラッチも
立っていないときだけです。`None` でない答えは、その場で `base.AppCrashedError(signal)`
を送出し、同じ式の中で捕まえます——本項目が意図して残す唯一の `try` 内 `raise` であり、
`CLAUDE.md` のインラインコメント規則が求める理由つきの `# noqa: TRY301` を持ちます。
送出を組み替えて避けるのではなくこちらを選ぶのは、`AppCrashedError` の存在理由そのものが
この1組の送出・捕捉に名前を与えることだからです（上記のクラス自身のドキュメント文字列が
そう述べています）。そのメッセージを `outcome.reason` へ折り込み、
`outcome.app_crashed` と確定済みクラッシュのラッチの両方を `True` にします。同じ
catch の中で、`self.cfg.capture_app_crash` が設定されていれば、その呼び出し可能
オブジェクトもその場で同期的に呼び、結果を新しい
`outcome.app_crash_artifacts: tuple[tuple[str, bytes], ...]` フィールドに保存します。
シナリオが終わったあとではなく、まさにこの確認の瞬間に収集することが、あとに続く
あとかたづけステップが証跡を足元からすり替えてしまうのを防ぎます（「iOS：`.ips`
レポートの照合」と「収集をつなぐ」を参照）。この1点
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
`_resume_warm` を一切呼びません。`_resume_warm` は、この経路とは別の、リースをまたぐ
別経路のままです。ただし、`_resume_warm` はこの節自身の `app_launched_at` に関する
懸念から除外されるわけではありません。`_resume_warm` それ自身が4つ目の起動箇所であり、
再記録の手当ても別に必要です。後述します。そこで `_finish_outcome` は、`relaunch` ステップ自身が失敗した時点で
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
起きても、`_run_step_body` の例外捕捉はそれを名指ししていません。その例外は、そのステップの
`outcome.ok` を `False` にする代わりに `run_scenario` 全体から抜け出してしまうため、
`_finish_outcome` にすら届きません。`relaunch` ステップ自身の結果が実際に `ok=False`
になる経路は、今日ではアラートガード・待機中回復失敗の経路（「検知の方式」のネストした
失敗についての段落を参照）だけです。これはアプリの健全性とは本当に無関係であり、まさに
この除外が存在する理由そのものです。新しい起動中のクラッシュが失われるわけではなく、
1ステップ遅れて帰属先が変わるだけです。アプリに触れる次のステップで失敗し、その結果は
`relaunch` ではない通常の結果であるため、どちらのラッチも飛ばす理由がなく、
`_finish_outcome` は他の失敗と同じようにそれを確認します。それ以外の
確認は、ステップがすでに失敗した後、しかも同じシナリオの手前のすべてのステップで
アプリが動作していると確認できた*あと*にしか走りません。ただし、これだけでは起動が
完了しなかったケースを完全には除外できません。シナリオの最初に失敗するステップ——
`before` の最初のステップ、`before` が宣言されていなければ `steps` の最初のステップ
——には手前のステップが存在せず、アプリが動作していると確認する機会自体がありません。
`env.start` からそのステップまでのあいだに、アプリが実際にフォアグラウンドへ到達した
ことを確認する手段もありません。`launch_driver` は `await_ready` を呼び、その
`ReadinessResult` を、例外を送出せずそのまま持ち越します
（[`launch.py:99`](../../bajutsu/common/runner/launch.py)）。これは上で見た `relaunch`
自身のクロージャと同じです。ただし、実際に `readiness.ready is False` に到達するのは、
`readyWhen`・名前空間シグナルが、すでに消えてしまったプロセスに対していつまでも解決
しないターゲットだけです。どちらも宣言しないターゲットは、`await_ready` のもっとも弱い
最終手段——素朴な `len(elements) >= 2` という要素数だけの判定
（[`readiness.py:151-152`](../../bajutsu/common/platform_lifecycle/readiness.py)）——へ
落ち込みます。この判定自身のドキュメント文字列が、コールドブートが遅いときに
SpringBoard のアイコンだけでこの条件を満たしてしまい、アプリ自身がフォアグラウンドへ
来る前に成立し得ることをすでに述べています。したがって、不適切な `launchEnv` でアプリが
終了してしまうターゲットは、`ready=True, signal="count"` のまま起動そのものが確認
できていないケースになり得ます——ターゲットのアプリ自身が実際に描画されたという証拠には
なりません。`Lease`
（[`bajutsu/common/runner/types.py:44`](../../bajutsu/common/runner/types.py)）に
`readiness: ReadinessResult | None = None` フィールドを加え、`Lease(...)` の
コンストラクタ（[`pool.py:563`](../../bajutsu/common/runner/pool.py)）で `sink=sink` と
並べて設定します。値は同じクロージャの手前で `launch_driver` がすでに返しており
（`pool.py:408`）、今日は `FileSink` の起動待ちタイムアウト診断にしか届いていません
——足りないのは `Lease` 側の写しであって、値の最初の取得ではありません。`run_scenario`
にも同じ形の
`readiness: ReadinessResult | None = None` 引数を加え、`relaunch=lz.relaunch` が
すでにそうしているのと同じように `lz.readiness` から注入します
（[`pipeline.py:898`](../../bajutsu/common/runner/pipeline.py)）。この値は、シナリオスコープの
ラッチオブジェクト（下の Unit 7）へ3つ目のフィールドとして加わり、`run_scenario` がその
オブジェクトを作る瞬間に一度だけ立ちます——フェーズごとに新しく組み立てられる
`StepLoopState` の側ではありません。`StepLoopState` は他の2つのラッチがすでにそうして
いるのと同じように、クロージャを通してそのオブジェクトを*共有する*だけであり、そのため
あるフェーズでの成功は次のフェーズへ持ち越されます。`readiness is None or not readiness.ready or readiness.signal ==
"count"` のとき未確認と判定されます——素朴な要素数判定は SpringBoard とアプリ自身を
見分けられないため、その判定による `ready` という答えもまた、アプリがフォアグラウンドへ
来た証拠にはならず、この項目が取り除こうとしている誤診断そのものが逆向きに起きている
ケースを塞ぎます。この判定は、まれなケースではなく*ふつう*の答えでもあります。
`launch_driver` は `id_namespaces` を `await_ready` へ渡さないため
（[`launch.py:99`](../../bajutsu/common/runner/launch.py)）、この経路では `namespace` が
成立することは一度もなく、`readyWhen` を宣言しないターゲットはすべて、あらゆる起動で
`count` に落ち着きます——このリポジトリ自身の `showcase-*-noax` ターゲット
（[`demos/showcase/showcase.config.yaml:90`](../../demos/showcase/showcase.config.yaml)、
`idNamespaces: []` で `readyWhen` もなし。どちらもシナリオ一式を丸ごと持ちます）もその
一例です。意図的終了フラグと同じ無条件のままシナリオの残りをずっと抑え続ければ、この
項目はそうしたターゲットのクラス全体に対して丸ごと無効化されてしまい、最初のステップだけを
守るはずが効きすぎてしまいます。`_finish_outcome` は代わりに、その成功がアプリの応答を
要求した、確定した outcome を見た時点でこのフラグを解除します。`if`・`forEach`
だけを除外するのではなく、肯定的に条件づけます。`if`・`forEach`
自身の、その本体を包む outcome は決して数えません——その成功自体が何も証明しないためです。
`_run_if` は条件が一致しなければ空の `else` 分岐を取り、その場で `ok=True` を確定します
（[`_functions.py:922-926`](../../bajutsu/common/orchestrator/loop/_functions.py)）。
`_run_for_each` も一致した要素がゼロのとき同じように確定し、どちらも、問い合わせの
その先のアプリが死んでいても送出しません——`driver.query()` は何かしら答え続けます
（iOS なら SpringBoard 自身のツリーです）。したがって、一度もフォアグラウンドへ来て
いないアプリを持ち、最初のステップがまさにそうした `if`・`forEach` であるターゲットは、
アプリが死んでいる*からこそ*このフラグを解除してしまい、このフラグが守ろうとしている
まさにそのターゲットのクラスで裏目に出ます。アプリにまったく届かない種類のステップ
（`http`・`generate`・`totp`・`email`・`push`）でも同じことが起こります。`relaunch`
そのものも同じです——ふつうの `_handle_action` の経路を通って配信されるため、単に
「アクチュエーションのステップ」というだけの規則なら数えてしまいますが、そのクロージャは
`await_ready` の `ReadinessResult` を握りつぶし、`await_ready` 自体は例外を送出しないため、
確定した `relaunch` の `ok=True` はアプリについて何も語りません。そこで `relaunch` を
明示的に除外し、代わりに確定した `relaunch` に対してこのフラグを*立て直します*——
アプリを、確認できない状態へちょうど戻したばかりだからです。本物の
アクチュエーションのステップ（`relaunch` を除きます）、
あるいは一致した `wait`・`assert` こそが、シナリオの最初のステップに欠けていたまさに
その肯定的な観測です。これにより、最初に失敗するステップへの保護を保ったまま、たまたま
弱い readiness の判定しか持たない
ターゲットで以降のあらゆる確認を黙らせてしまうことも、空振りの `if`・`forEach`、
アプリにまったく届かないステップ、確認しようのない `relaunch` を
その観測になりすまさせてしまうことも、どちらも避けられます。その最初の成功が
来るまでのあいだ、`_finish_outcome` は起動未確認を、失敗した `relaunch` と同じ扱いに
します——確認を飛ばします。この項目がまだ起動の完了を確認できていない場合、シナリオは
失敗した `relaunch` がシナリオの残り全体に対して置くのとまったく同じ「アプリの状態がまだ
わからない」という状況に置かれるからです。`ReadinessResult` は自身のドキュメント
文字列で「Pure diagnosis: it never enters a verdict（判定材料でしかなく、それ自体が
判定を下すことはない。prime directive 1）」と述べています
（[`protocols/readiness_result.py:15-16`](../../bajutsu/common/platform_lifecycle/protocols/readiness_result.py)、
`signal` については `:27` に同じ記述があります）。これはこのフラグより前のすべての
利用者について真であり、いずれも起動待ちタイムアウトの診断表示に使うだけでした。この
項目は、この値から振る舞いを決める初めての利用者になるため、`Lease.readiness` を加える
同じ変更でこのドキュメント文字列も更新し、不変条件が古びる前に、この新しい用途を
書き加えます。それ以外の場面での `notRunning` という答えは、直前まで動いていたアプリが今は動いて
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
Simulator 上のアプリの `.ips` レポートは、実行ファイルのフルパスを*ペイロード*——
このファイルが持つ2つの JSON ドキュメントのうち2つ目であり、1行だけのヘッダでは
ありません（[`_reported_pid`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/_functions.py)
自身のドキュメント文字列のとおり、ヘッダが運ぶのは `app_name`・`bundleID`・
`timestamp`・`os_version`・`incident_id` などであり、安価で安定していますが
インストールパスは持ちません。PID も、本項目の udid も、ペイロードにあり、
`_reported_pid` がまさにその理由でペイロードを解析しています）——に運んでおり
（`.../CoreSimulator/Devices/<udid>/data/Containers/Bundle/Application/…`）、
そこにクラッシュしたプロセスが動いていた Simulator 自身が現れます。`Lease`
（[`bajutsu/common/runner/types.py`](../../bajutsu/common/runner/types.py)）はすでに、
リースしたデバイス自身の `udid` を記録しています。そこで、BE-0421 自身の
`XcuitestEnvironment._crash_reports(spawned_at, pid)`（自身の既知のプロセス向けに
`"xcodebuild-*.ips"` を決め打ちしているため `pattern` 引数を必要としません）の姉妹にあたる
新しい `_app_crash_reports(pattern, launched_at, udid)`
を加え、`_reports_since` が返す候補（これは `list[Path]` を返し、レポート自身の
ファイル名に udid は現れません)を1件ずつ読み、その*実行ファイル*のパスがペイロードの中でその
同じ `udid` を名指しているものだけを、`_reported_pid` による確認の代わりに受け入れます。ヘッダ
自身が持つ `bundleID` フィールド（`XcuitestEnvironment` がすでに `self._bundle_id` として
保持しています）は、より安価で安定した絞り込みであり、ペイロードの確認を置き換えるのではなく
併用する価値があります。これにより、
同一の対象バイナリを動かす Simulator が並列実行の CI ホスト（`--workers 2`）で2台、同じ
時間帯に存在する場合でも、PID を使わずに区別できます。

`_reports_since(reports_dir, pattern, since)` は、それでもファイル名で照合します。`.ips`
レポートのファイル名が名指すのはクラッシュしたプロセスであり（`Showcase-2026-…ips`）、
バンドル ID ではありません。BE-0421 は自分自身の既知のプロセスに対して、リテラルの
`"xcodebuild-*.ips"` を渡しています。`XcuitestEnvironment` が保持する `ios.bundle_id`
（`self._bundle_id`、`com.example.Showcase`）はその名前ではなく、レポートのファイル名と
一致することはありません。本項目は代わりに、インストール済みアプリ自身の `Info.plist`
（`Path(self._app_path) / "Info.plist"`）から `CFBundleExecutable` を、`app_crash_artifacts()`
自身の中で読み取ります。あらゆる iOS バンドルが宣言を義務づけられているこの1つのプロパティリスト
キーから、掃引のパターンを組み立てます。`app_crash_artifacts()` は「iOS：`.ips` レポートの
照合」の冒頭が述べる `RunEnvironment` プロトコルの形どおり、引数を取らないメソッドです。
そのため `eff` がスコープになく、`ios.app_path` を生きたまま読むことはできません。
`self._app_path` は新しいフィールドであり、`start()` で
`self._bundle_id`（`self._bundle_id = ios.bundle_id if device_type != "device" else None`、
`xcuitest_environment.py:319`）のすぐ隣に、同じ `ios` から同じ方法で保存します。
`ios.app_path` 自体は任意です
（`str | None`、
[`target_config.py:107`](../../bajutsu/common/config/schema/target_config.py)）。
`bundle_id` だけを名指す `deviceType: simulator` ターゲットで、すでにアプリが
インストール済みの Simulator に対しては、これを設定しません。同じケースを
`_prepare_simulator` 自身の install もすでにゲートしており（`if ios.app_path:`、
`xcuitest_environment.py:902`）、置き換えデバイスの経路は、設定済みだと決めつける
のではなく自前の専用エラーを送出します（`xcuitest_environment.py:627-632`）。
実行ファイル名を代わりに導く PID アクセサもないため（そもそも `Info.plist` を
読む理由そのものです）、組み立てる代替パターンがありません。この収集は `appPath`
を設定したターゲットに限った範囲であり、その範囲は読み取りより*前に*明示的に
確認します。`self._app_path is None` はその場で `[]` へ解決します。非 macOS ホストと
同じ、名前のついた事前確認です。読み取り側で `Path(None)` が送出し、数段落あとの
広い `except Exception` に握りつぶされ、説明のつかない見落としとして読めてしまう
のではありません。

`XcuitestEnvironment`
（[`bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py)）
は、自身の `udid` をすでに知っています。ここに
`app_launched_at` というタイムスタンプを加えます。ただしコールド起動そのものには、
目印を隣に記録できる Python 側の `e.launch(...)` 呼び出しがありません。`_spawn_cold` は
アプリをインストールして `xcodebuild` を起動するだけで、アプリ自身の起動はランナーの
*内側*で、ランナー自身の `XCUIApplication.launch()` によって行われます——このコードベース
自身のコメントがそう明言しており（`xcuitest_environment.py:322-324`）、その周りの
Python 側の呼び出しは `_prepare_simulator`・`_launch_params`・`_spawn_cold_with_retry`
だけです。目印を `_spawn_cold_with_retry` が返ったあとに記録すれば、それが指す起動よりも
遅れてしまいます。自身のコールド起動の最中にクラッシュしたアプリ——ランナー自身の
`/health` はまだ答えるため、spawn 自体は成功します——は、spawn 呼び出しが返ってから
初めて記録される目印よりも前の mtime を持つ `.ips` を残し、`_reports_since` の
`mtime >= since` がそれを拒みます。まさに本項目が取り除こうとしている「レポートは
存在するのに `bajutsu` が見つけられない」という結果そのものです。そこで
`app_launched_at` は `_spawn_cold` の中、`_spawn_cold_with_retry` の呼び出しの*直前*に
記録します。これが2つの選択肢のうち安全な方です。早めに記録した目印は過剰に収集する
だけで済み（`udid` と実行ファイル名の確認がすでに絞り込みます）、遅く記録した目印と
違って、捕まえるべきクラッシュを取り逃すことはありません。ただし、
`relaunch` ステップ自身の起動——この節がたった今 `_resume_warm` と区別したもの——は
`device_relauncher` のクロージャを通じて走り、`(udid, run, extra_env)` を閉じ込めるだけで、
目印を更新すべき `XcuitestEnvironment` がその場にありません。放っておけば、
`app_launched_at` はリースの最初のコールド起動のまま凍りついてしまい、シナリオ途中の
`relaunch` に続くクラッシュは、その `relaunch` 以前にまで遡る `since` で
`DiagnosticReports` を掃引することになります。`relaunch` 自身がすでに置き換えたはずの
クラッシュの `.ips` を、逆に添付しかねないほど広い範囲です。`XcuitestEnvironment` は
`relauncher()` をオーバーライドします（それ以外は `_DeviceEnvironment` 自身の実装の
ままです）。`device_relauncher` が返す `RelaunchFn` を包み、それを呼ぶ*前に*、
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
もオーバーライドします。`relauncher()` のオーバーライドと同じ形ですが、`crawl_reset(eff)`
自体は*ファクトリ*です。`_build_lane` はこれを1回だけ呼び、返ってきた `Reset` を保持し
続けます（[`cli.py:300`](../../bajutsu/crawl/cli.py)）。したがってこのオーバーライドは、
ファクトリ呼び出しの隣で記録するのではなく、その返ってきた `Reset` を包みます。
`_DeviceEnvironment` の `Reset` を呼ぶ*前に*、`app_launched_at` をフロンティアの再訪
のたびに記録します。`AndroidEnvironment` には、
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
自身の `e.launch` の*直前*に `app_launched_at` を記録し直します。目印は、アプリが実際に
走っている、そのときどきの起動を追いかけ続けます。

新しく `app_crash_artifacts() -> list[tuple[str, bytes]]` を `RunEnvironment` プロトコル
（[`bajutsu/common/platform_lifecycle/protocols/run_environment.py`](../../bajutsu/common/platform_lifecycle/protocols/run_environment.py)）
に、`take_crash_snapshot()` の隣に加えます。ただし、それよりも素直な形です。
`take_crash_snapshot()` が返すのは*サンク*です。バックエンドクラッシュは最初に観測された
時点で捕捉し、プールがリースを解放するまで確定を遅らせます。そうしなければ、同じ温まった
環境を再利用する別のワーカーの次の起動が、凍結したはずの照合条件を先に上書きしてしまいます。
本項目の収集には、これよりも狭い、同じ形の問題があります。並行するワーカーからではなく、
*同じ*シナリオの内側からやってきます。シナリオの途中で確定したクラッシュのあとに、
あとかたづけの `relaunch` のようなふつうの `after` 規則が続くことがあり、それは
まさにこの節自身の `relauncher()` オーバーライドを通じて、シナリオの `RunResult` が
組み立てられるよりも前に `app_launched_at` を記録し直してしまいます。そのあと——
`pipeline.py` 自身の戻ったあとの走査、本項目の初期の草案が選んでいた呼び出し箇所を
含みます——のどの時点で目印を生きたまま読んでも、あとかたづけがすでにクラッシュより
先へ進めてしまった `since` で掃引することになります。そこで `app_crash_artifacts()` は
`_finish_outcome` の内側で、クラッシュが確定したその瞬間、同じシナリオの後続のどの
ステップよりも確実に前に、同期的に呼び出します（「検知の方式」を参照）。その結果は
`StepOutcome` に載せて運び、あとで読み直すことはありません。サンクは要りません。何かを
目印が動きうる時点より先へ遅らせているわけではなく、呼び出しそのものを、事後確認が
すでにある場所へ早めているだけだからです。`RunEnvironment` は、どの具象クラスも継承しない構造的プロトコルであるため、
`take_crash_snapshot()` にも継承された既定値はありません。`WebEnvironment`・
`AndroidEnvironment`・`_DeviceEnvironment`（`FakeEnvironment` がこれを継承します）は、
すでにそれぞれ自前の1行の `return` を宣言しています。`app_crash_artifacts()` も同じ形を
踏襲しますが、Android だけは違います。`WebEnvironment` と `_DeviceEnvironment`
（`FakeEnvironment` がこれを継承します）はそれぞれ自前の `return []` を新たに持ちますが、
`XcuitestEnvironment` と `AndroidEnvironment` は、この節と次の節が説明する本物の収集で
それぞれオーバーライドします。`pool.py` の `lease()` がこのメソッドをリースしたあらゆる
環境から、クラッシュしたときだけでなくあらゆるリースのたびに読むため、リースされる
環境はすべて、このどちらか一方を持つ必要があります。

同じプロトコルに、もう1つ狭いメソッドが加わります。「Android：`logcat` のクラッシュ用
バッファをまず読み、root 権限があるときだけ tombstone を取得する」節が理由を
詳しく述べます。`app_crash_tombstone() -> list[tuple[str, bytes]]` です。
`_finish_outcome` からは呼ばず、`pipeline.py` の戻ったあとの走査だけから呼びます。
Android だけが収集を2つの呼び出し箇所へ分け、それぞれ異なるタイミングの制約を
抱えるためにこのメソッドが要ります。ほかのバックエンドには2つ目のメソッドは
要りませんが、`pool.py` の `lease()` クロージャは `app_crash_artifacts()` と同じ
無条件の形で、リースしたあらゆる環境からこのメソッドも読みます。構造的プロトコルの
メンバーは、実体のある処理を持つ場所だけでなく、あらゆる場所で宣言が要ります。
`WebEnvironment` と `_DeviceEnvironment`（`FakeEnvironment` と `XcuitestEnvironment` がどちらも
継承する基底）は、それぞれ `app_crash_artifacts()` の no-op またはオーバーライドの隣に、
同じ1行の `return []` を新たに持ちます。`XcuitestEnvironment` はこれを繰り返さず、
その no-op をそのまま継承します。tombstone の取得そのものでこれを
オーバーライドするのは `AndroidEnvironment` だけです。

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
None = None` というキーワードを加え、`fetch_clock` と `act` をすでに通しているのと同じ
方法で `AdbDriver.__init__` へ通します。`Driver` は `@runtime_checkable` で共通の基底
クラスを持たないため、そこにデータメンバーを置けば、あらゆるバックエンドとあらゆる
インラインのテストダブルが、同じ宣言を繰り返すことになります。そこで、素の
コンストラクタ引数として渡すほうを選びます。

`package` は同じ境界で `None` を既定値とします。iOS 側の `ios.app_path is None`
（「iOS：`.ips` レポートの照合」）とは違い、ここでの `None` はフェイルクローズしません。
`dumpsys activity exit-info` はパッケージ引数なしでもエラーにはなりません。端末上の
*あらゆる*パッケージの `ApplicationExitInfo` を報告するだけです。したがって
`package=None` で組み立てられた `AdbDriver` は、引数なしの `pidof`（出力は空)を走らせた
あと、端末全体の exit-info 履歴を読み、その最新の項目がたまたま別のプロセスの `CRASH`
だったとしても、テスト対象アプリが一度も経験していないクラッシュを確定させてしまいます。
シナリオを実行するすべての `AdbDriver` は `AndroidEnvironment.start()` の中で組み立てられ、
そこでは常にパッケージがスコープ内にあるため、設計上 `run` の経路ではこの `None` に
届きません。しかし `make_driver` には `package` も `launched_at` も渡さない他の呼び出し元が
あり（[`bajutsu/serve/operations/_common.py:92`](../../bajutsu/serve/operations/_common.py)、
[`bajutsu/common/doctor/_functions.py:194,210`](../../bajutsu/common/doctor/_functions.py)）、
黙った `None` の既定値は、そのうちの1つがのちにこの安全でない答えを黙って受け継ぐ経路その
ものです。そこで `app_crash_signal()` は、`adb` の呼び出しに入る前にまず `package is None`
（および `launched_at is None`、あるいは `launched_at()` 自身が `None` を返す場合)を確認し、
その場で `None` へ即座に解決します。`ios.app_path is None` がすでに持つのと同じ、名前のついた
事前確認です。

空の `pidof` の応答は、実際には空の文字列として Python まで届きません。`AdbDriver` の既定の
`RunFn` は `adb.real_run`
（[`adb/_functions.py:111-112`](../../bajutsu/common/backend_cli/adb/_functions.py)）——ただの
`subprocess.run(..., check=True)`——であり、toybox の `pidof` は一致がないとき、この確認
そのものが観測しようとしているまさにその、ふだんの想定どおりの結果として終了コード 1 を
返します。そこで `app_crash_signal()` は `pidof` の呼び出しを `except
subprocess.CalledProcessError as exc` で包みます。これは、期待どおりの失敗を例外的なもの
としてではなく扱う、このリポジトリ自身の先例と同じ形です（`AdbDriver._rooted()` が
`adb shell id -u` を包む `except (subprocess.CalledProcessError, OSError)`、
[`adb_driver.py:1234-1235`](../../bajutsu/common/drivers/adb/adb_driver.py)）。`stdout` が
空のまま終了すれば、空の `real_run` の戻り値がそうだったのとまったく同じ「プロセスなし」
として読みます。それ以外の `CalledProcessError`、あるいは `adb` 自身の不調による
`OSError` は `None` へ解決します。この節がすでに `package is None` や古すぎる `api_level`
に与えているのと同じ「確認できない」という答えであり、この確認がまさに捉えようとしている
本物のクラッシュの最初の1回で、処理されない不具合として抜け出してしまうことはありません。
同じ包み方を、下の `dumpsys activity exit-info` の呼び出しにも、ポーリング中の伝送の不調に
対して適用します。

空の `pidof` という答えは、アプリがまだプロセスを保持しているはずの場面では、必要条件
ではあっても十分条件ではありません。起動が完了しなかった場合や、本項目がシナリオレベル
の原因を持たない終了とも一致します。Android には、通常のテスト条件下で jetsam のような
OS による強制終了はありませんが、ふつうのプロセス終了もクラッシュと同じ答えを `pidof`
に返させます。`adb shell dumpsys activity exit-info <package>` は、そのパッケージの
`ApplicationExitInfo` の履歴を、各項目にタイムスタンプを付けて報告し、`CRASH` や
`CRASH_NATIVE` を `ANR`・`LOW_MEMORY`・`USER_REQUESTED` から区別できます。これは API 30
以降でしか利用できず、このリポジトリの CI がすでに起動している API 34 の AVD では利用でき
ますが、このコードベースは今日、端末の API レベルをどこでも追跡していません。そこで
`AdbDriver` は `package` と同じ方法で通す `api_level: int | None = None` キーワードを加え、
`AndroidEnvironment.start()` の中で `adb shell getprop ro.build.version.sdk` を一度読み、
`self._package` の隣に保存します。`app_crash_signal()` は `api_level is None or api_level <
30` を `package is None` と同じ事前確認へ折り込み、まだ存在しないシグナルをポーリングし
続けるのではなく、同じ名前のついた形でフェイルクローズします。本項目自身の「はじめに」
は、iOS の Simulator 限定の範囲をすでに名指しているのと同じように、Android のこの
exit-info の裏付けについても同じ範囲を名指します。ただし、この履歴はプロセスの生存期間を
またいで残るため、直近の1件だけでは信頼できません。
今回の終了が無関係な理由で `ApplicationExitInfo` をまだ記録していない場合、`dumpsys` の
返す最新の項目が、以前のシナリオのクラッシュのままということがあり得ます。`AdbDriver`
に `launched_at: Callable[[], tuple[float, str] | None] | None = None` というコンストラクタ
引数を加えます。これは `AndroidEnvironment` の起動の目印——エポックと、その隣の
`'%Y-%m-%d %H:%M:%S'` という端末時計による表記の両方——をその場で読む注入された
コールバックであり、`fetch_clock` がすでに使っている、構築時に固定せず呼び出しのたびに
読むのと同じ仕組みです。`pidof` が空を返した直後に一度だけこの履歴を読むと、まさに裏付け
ようとしているそのクラッシュと競合します。プロセスが落ちた瞬間に `pidof` は空を返し
ますが、`system_server` が対応する `ApplicationExitInfo` を記録するのは、その死を
回収したあとです。ネイティブクラッシュならさらに遅く、`crash_dump` が完了したあとです。
したがって、新しいクラッシュを確定させるはずのその読み取りでも、最新の項目がこの起動
より前の古いものにとどまり得ます。そこで `app_crash_signal()` は、iOS の
`_app_crash_reports` がすでに `DiagnosticReports` をポーリングしているのと同じ短い
上限つきの方法で、この履歴をポーリングします。数秒を上限に、その*最新*の項目が
`CRASH` か `CRASH_NATIVE` を報告し、*かつ*その項目自身の `timestamp=` フィールド
——`ApplicationExitInfo` 自身の、端末のタイムゾーンによるウォールクロックの表記であり、
オフセットを持ちません——が、`launched_at()` というタプルの2つ目の要素——エポックの
隣に記録された `'%Y-%m-%d %H:%M:%S'` という端末時計による表記——以降であるまで
読み直します。エポック側とは比較しません。生の `timestamp=` をホスト側でエポックへ解決すれば、
ホスト自身のタイムゾーンを通ってしまい、ホストと別のタイムゾーンで動く端末では常識的な構成のもとで
すべてのタイムスタンプを目印から何時間もずらしてしまいます——この設計がそもそも
避けているはずの時計のすり合わせを黙って呼び戻すことになります。ポーリングは、この条件を
満たす項目が現れた時点——今回の起動より前の古い項目を除外できた時点——か、上限に達した
時点のどちらかで終わります。上限に達した場合も、最新の項目が両方の条件を満たさなかった
場合も、`None` を返します。シグナルをまったく持たないバックエンドが返すのと
同じ、「確認できない」という答えです。これは、`app.state` の `notRunning` が Simulator
自身の制約から無償で得ている裏付けに相当します。Android では、プラットフォーム自身が
報告する、時刻で絞り込んだ終了理由が、adb にとって同じ役割の積極的な確認を与えます。
読み取り側に、書き込み側が必要とするのと同じだけの追いつく猶予を与えれば、ですが。
ただし、この上限つきポーリングが必要なのは、1つのシナリオのうち*最初*の1回だけです。
最初の確認だけが `system_server` の回収と本当に競合しているのであり、同じシナリオの
あとの確認——`if`・`forEach` の3段階深く入れ子になったクラッシュや、失敗する
`after` ステップ1つひとつ、「検知の方式」で前述した通りのもの——は、最初の確認が
すでに上限まで待って一致を見つけられなかったのであれば、それ以上待つべき新しい
理由を持ちません。そこで `AdbDriver` に、`self._exit_info_exhausted: bool = False`
という新しいインスタンスフィールドを加えます。既存の `_act_warned`・
`_act_unavailable` という2つのラッチと同じ形であり、上限つきポーリングが一致
なしで終わった最初の時点で立てます。`run` ではドライバのインスタンスが1つのシナリオより
長生きすることはありません——`AndroidEnvironment.has_reusable_resident()`
は無条件に `False` を返すため（前述の「iOS：`.ips` レポートの照合」を参照。
BE-0291 のリースをまたぐ再利用は XCUITest だけの仕組みです）、リースごとに新しい
`AdbDriver` が組み立てられます。しかし `crawl` は、その巡回全体を通じて1つの
`AdbDriver` を使い続けます（`_build_lane` は `launch_driver` を一度だけ呼び、`crawl()`
はそれを組み立て直しません。[`crawl/cli.py:283-300`](../../bajutsu/crawl/cli.py)）。
下の「`crawl` 自身のクラッシュ記録を拡張する」は、この同じ確認を巡回中のあらゆる検知へ
配線するため、`run` が無償で得ているこの暗黙のリセットがないままでは、確認できなかった
最初の crawl の検知のあとフラグが立ちっぱなしになり、その巡回の残り全体で*本物の*
クラッシュを無ポーリングにしてしまいます。このリセットを `invalidate_settled_cache()` へ
折り込むのは、場所として間違っています。あのメソッド自身の契約は「画面が変わった——この
ドライバ自身のアクチュエータ以外の何かによって」であり、`AdbDriver` はふつうのジェスチャ
1回1回でもすでにこれを呼んでいます（`_act`・`_device_act`・`type_text`、
[`adb_driver.py:352`](../../bajutsu/common/drivers/adb/adb_driver.py)、`:1119`、`:1145`、
`:1462`）。そのため、このラッチをそこへ折り込めば、このラッチが抑えようとしている
ほぼすべての確認の手前でフラグを解除してしまいます。両方のバックエンドで、この確認を
配線している以上です。文字どおり新しい起動こそが——「画面が変わった」ではなく——新しい
`system_server` の回収を待つ価値が再び生まれる出来事なので、`AdbDriver` は代わりに
2つ目の、狭い専用メソッドを持ちます（`reset_exit_info_poll()`。隣の
`SettledCacheInvalidator` と同じ形です）。これは、`invalidate_settled_cache()` をすでに
*別の*理由で呼んでいる同じ2つのクロージャから呼びます——`AndroidEnvironment.relauncher()`
の `relaunch()` は `e.launch(...)` の直後
（[`android_environment.py:339-351`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py)）、
`crawl_reset()` の `reset()` は自身の `e.launch(...)` の直後です。`AdbDriver` 自身の
アクチュエータの中からは決して呼びません。
一度立ってしまえば（まだリセットされていなければ）、あとの呼び出しは上限つきポーリングを飛ばし、exit-info の履歴を
一度だけ読んで、その読み取りが一致を見つけたかどうかにかかわらず即座に答えます
——本項目自身の見積もりがもともと想定していた、確認できなかった確認のコストであり、
このラッチなしでは Android のシグナルが実際に取っている数秒のポーリングではありません。

`AndroidEnvironment`
（[`bajutsu/common/platform_lifecycle/environments/android/android_environment.py`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py)）
に、iOS の環境と同じ `app_launched_at` の記録を、3か所ある起動の呼び出し箇所
（`e.launch(package, launch_env)`）それぞれの*直前*に加えます。あとではありません。
`e.launch` は `am start -W` であり、その `-W` フラグは起動の完了を待ちます
（[`adb/_functions.py:634-637`](../../bajutsu/common/backend_cli/adb/_functions.py)）。
そのため、これが返ったあとに立てた目印は、起動時のクラッシュがすでに起きたあとに
立てた目印になってしまいます——そのクラッシュ自身の `logcat -t` の該当ブロックも
`exit-info` の項目も目印より前になり、どちらも拒まれてしまいます。上の
`_spawn_cold` に関する議論がすでに iOS について避けている、「レポートは存在するのに
`bajutsu` が見つけられない」という同じ結果です（早めに記録した目印は `start()` 自身の
`force_stop`・`pm clear` がすでに絞り込んだ窓を広げるだけで済み、遅く記録した目印と
違って、捕まえるべきクラッシュを取り逃すことはありません）。Unit 11 の Android 用
フィクスチャは、その引き金をシナリオの途中でタップして起こすため、この順序を検証
しません。ここで問題になっているのは起動時のクラッシュ——`am start -W` が返る前に
死ぬアプリ——であり、これは Unit 13 が直接固定します。
ホストの時計ではなく、
端末自身の時計から起動時刻を読みます。起動の目印を、
あとで端末自身の時計による読み取りとだけ比較するのであれば、ホストと端末の時計を
すり合わせる必要はありません——`app_launched_at` 自身は、その読み取りのエポックの
フィールド（`adb shell date +%s`）であり、下の tombstone の更新時刻比較がそのまま
エポックとして消費する値です。残る2つの消費者はどちらもこの同じエポック値を使えません。
理由はどちらも同じ1つの規則に行き着きます。端末側の表記をホスト側でエポックへ
解決してはなりません。その変換は端末のタイムゾーンではなくホストのタイムゾーンを通るため、ホストとは
別のタイムゾーンで動く UTC のエミュレータを駆動すれば、あらゆる新しいタイムスタンプが目印から
何時間も離れた位置へずれてしまい、この設計がそもそも避けているはずの時計のすり合わせを
黙って呼び戻してしまいます。`dumpsys activity exit-info` の `timestamp=` フィールドは
`ApplicationExitInfo` 自身の、端末のタイムゾーンによるウォールクロックの表記であり、
オフセットを持ちません。そこで上の exit-info ポーリングは、これを同じ起動の瞬間の
2つ目の表記（`'+%Y-%m-%d %H:%M:%S'`）と比較します。これは `AdbDriver` が
受け取る `launched_at()` タプルの表記側であり、エポック側ではありません。
`logcat -t` もこの同じエポック値を消費できませんが、こちらはもっと単純な
理由からです。`adb logcat -t` はオーバーロードされており、整数の引数は
「もっとも新しい N 行」という*行数*として読まれ、時刻の境界としては読まれません。
`'MM-DD hh:mm:ss.mmm'` という引用符付きの文字列だけが時刻として読まれ、これは exit-info の
表記とも別物です（`logcat` 側には年がなく、exit-info 側にはあります。どちらも
互いの代わりにはなりません）。そこで各起動
箇所は、`adb shell "date '+%s|%Y-%m-%d %H:%M:%S|%m-%d %H:%M:%S.000'"` という**1回**の
読み取りを取ります——この書式は*端末*側のシェルのために引用符で囲んでおり、そうしなければ
`|` がパイプとして読まれ、残りが単語分割されてしまいます。そのパイプ区切りの3つのフィールドをホスト側で分割します（プレーンな
文字列分割であり、タイムゾーンの解決ではないため、上の規則はそのまま成り立ちます）。3回別々に
`date` を呼ぶのではありません。3回に分ければ3つの異なる瞬間を刻んでしまい、
`logcat` 用のフィールドを（もし最後に読めば）読み取りの間隙に着地したクラッシュを
フィルタで除いてしまいかねません。3つ目のフィールドを
`app_launched_at` の隣に、`logcat -t` だけが消費するものとして保存します。エポック値を
そのまま通せば、時刻の境界がまったくないまま黙ってリングバッファ全体を返してしまいます。
以下のプロセス絞り込みはパッケージだけによるため、同じパッケージの以前のシナリオの
クラッシュも、このシナリオ自身のものとしてすり抜けてしまいます。本項目の Android 側の収集は、
2つの層を取ります。両方を取得するという、本項目自身が
決めた対象範囲に沿ったものです。ただし、同じ呼び出しから取るのでも、同じ時点で取るのでもありません。
`logcat` は、クラッシュが確定したその瞬間に、シナリオの途中で `_finish_outcome` の内側
から読んでも安全です（「検知の方式」）。昇格した権限を必要とせず、後続のステップがまだ
必要とするチャネルにも触れないからです。tombstone の取得は権限を必要とし、それほど早く
発火させればかえって実害があります（後述）。したがってこちらは、もとの事後の経路に
とどめます。`app_crash_artifacts()` が返すのは `logcat` の層だけであり、tombstone の層は
独立した2つ目の `app_crash_tombstone()` があとから返します。片方が失敗しても他方を
巻き込まないよう、それぞれ独立して例外を捕まえます。

1. **`logcat` のクラッシュバッファ**は、常に試みます。昇格した権限は要りません。
   `logcat -b crash` は端末全体で1つの、起動をまたいで残るリングバッファです。
   [`scripts/collect_android_diagnostics.sh`](../../scripts/collect_android_diagnostics.sh)
   は、失敗した CI ジョブの終了時に、これをまるごと（`-b main,system,crash,events,radio`）
   ダンプします。したがって起動ごとにクリアすれば、そのジョブ終了時の掃引が必要とする
   証跡を壊してしまいます。ここでは何もクリアしません。あとで行う
   `adb logcat -b crash -d -t "<MM-DD hh:mm:ss.mmm 形式の起動の目印>"` というダンプは、
   前述の `logcat` 用に表記した目印を（エポックの `app_launched_at` ではなく）
   `logcat` 自身の時刻フィルタとして使うため、このシナリオが実行している起動
   より前の内容を含めず——同じパッケージが同じ端末上の以前の実行で残した古いクラッシュ
   を拾うこともなく——それでいて、その目印より前の内容はジョブ終了時の掃引がそのまま
   見つけられるように残します。ダンプは2通りの方法で解析します。マネージド
   コード（Java・Kotlin）のクラッシュを示す `FATAL EXCEPTION` ブロックと、それが
   見つからない場合に、NDK クラッシュを示すネイティブクラッシュバッファ自身の
   `Fatal signal <n>` という見出し行です。`logcat` のクラッシュバッファが実際に
   運ぶ2つの形式です。どちらも、時刻の窓だけでなく、テスト対象アプリ自身の
   プロセスにも絞り込みます。このバッファは起動をまたぐだけでなく、プロセスも
   またいで端末全体で共有されるからです。マネージドのブロックは、自身の
   `Process: <package>` 行が対象の `self._package` を名指すときにだけ受け入れ、
   ネイティブのブロックは、自身の `>>> <process> <<<` という見出しが名指すときに
   だけ受け入れます。同じ時間帯にシステムサービスや別のアプリがクラッシュしても、
   このシナリオ自身の証跡としては書き込みません。その場で取った1回のダンプは、
   ネイティブクラッシュに対してはそれでも空になり得ます。`crash_dump` が
   `>>> <process> <<<` ブロックを書き込むのは、確認ゲートがそもそも検知の根拠にした、
   `pidof` がすでに報告した死のあとです。`app_crash_signal()` 自身の exit-info
   ポーリングが閉じようとしているのと同じ非同期性です。そこで `app_crash_artifacts()`
   も、最初の `-d` スナップショットを信用するのではなく、同じ短い上限つきの方法
   ——数秒を上限に——一致するかその上限に達するまでダンプを取り直します。一致した
   ほうを抽出し、`logcat-crash.txt` として書き出します。これは、この adb バック
   エンドが到達できるどの AVD や実機でも保証される唯一の証跡です。
2. **tombstone の取得**は、ベストエフォートで root 権限に依存し、上の `logcat` の層より
   あとに走ります。`adb root` は、このバックエンドが対象とするエミュレータ
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
   自体を再起動することだけが唯一の直し方であり、この層はそこまでは行いません。
   `_finish_outcome` の内側で `logcat` の層と一緒にこれを走らせれば、単に無駄なだけでは
   済みません。実害があります。外側の `if`・`forEach` の outcome はまだ確定していません。
   シナリオレベルの `expect`（`_functions.py:741-752`）も、シナリオが発送する
   `after` ルール（`_functions.py:821-831`）も、すべて同じレジデントサーバを経由して
   まだ操作やスクリーンショットを続けます。そこでチャネルの異常が起これば
   `BackendCrashError` が上がり、`run_scenario` は失敗へ畳み込む代わりにそれを
   意図的に再送出します（`_functions.py:839-846`）。そのまま `pipeline.py` の
   クラッシュリトライループへ抜け、本項目が生み出すはずだった `RunResult` そのもの
   ——`app_crashed` と `app_crash_artifacts` を含みます——が失われます。そこで
   `app_crash_tombstone()` を、`pipeline.py` の戻ったあとの走査（「収集を run ディレクトリへ
   つなぐ」）だけから呼ぶ、独立した2つ目のメソッドにします。そこはシナリオが本当に
   終わったあとです。信じ込んでいるだけではありません。`lz.release()` までに残る処理は
   すべて、ネットワークのスナップショット書き込みと進捗行だけであり、どちらも
   `lz.collector` がすでに取り終えたデータを読むだけで、ドライバを経由し直すことは
   ありません。`AndroidEnvironment.start()` は、どのリースでもレジデントサーバと
   reverse トンネルを一から組み立て直します（`_begin_resident`、`bridge_collector`）。
   「温存されたレジデントは残さない」という、本項目がすでに前提としているふだんの
   解体・再構築です。したがって、この層が壊したものによって、この端末上の次のリースが
   影響を受けることはありません。`adb root` のあとには、端末に対して他の何かを行う前に
   `adb wait-for-device` を続けます。`collect_android_diagnostics.sh:99-103` が自身の
   `adb root` と `adb pull` の間に置いているのと同じゲートであり、これがなければ
   以降の処理はすべて `adbd` の再起動と競合します。そのあとでもっとも新しい
   `/data/tombstones/tombstone_NN`
   のうち、更新時刻が前述の起動の目印（エポックの `app_launched_at`）以降のものを1件
   取得します。比較は端末自身の
   相対的な時刻どうしで行うため、ここでも時計のすり合わせは不要です。目印をこれほど
   遅く読んでも、本項目の他の箇所であとかたづけの `relaunch` が引き起こす iOS の
   `.ips`・`logcat` の誤帰属のような危うさはありません（「iOS：`.ips` レポートの照合」）。
   あとかたづけは `app_launched_at` を常に*前へ*しか動かさないため、この読み取りの
   タイミングが最悪でもたらすのは、すでにあった tombstone を取り逃すことだけで、
   古いものを誤って今回のクラッシュとして結びつけることはありません。実機、
   user ビルド、`adb root` を拒む状態のいずれでも、この層は黙ってスキップします。
   事象自体は報告済みであり、`logcat-crash.txt` はすでに届いています。root を拒む
   端末が失うのはマネージドコードのクラッシュがそもそも必要としないネイティブ
   フレームの詳細だけです。`adb root` には、取得が終わったあと、同じベストエフォートの
   ラッパーの内側で `adb unroot`（と2回目の `adb wait-for-device`）を対にします。
   これにより、端末は他のあらゆるリースがすでに前提としている権限レベルへ戻ります。
   `adb root` はそうしなければ端末全体で持続し、`adb unroot` か再起動までそのままです。
   このリポジトリの他のどこにもそれを戻す処理はありません。既存の唯一の `adb root` の
   呼び出し元（[`scripts/collect_android_diagnostics.sh:101`](../../scripts/collect_android_diagnostics.sh)）
   はジョブの終わりで走り、意図してそのあとに何も走らせません。この層の run 途中の pull は
   そうではありません。戻さないままだと、この端末を共有する後続のシナリオ——
   `AndroidEnvironment.start()` 自身の `install`・`pm clear`・`force_stop`・`launch` の各
   呼び出し
   （[`android_environment.py:122-146`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py)）
   ——は、ふつうの `adb shell` ではなく root の `adb shell` を通って走ってしまいます。しかも
   黙ってです。端末の状態は（そして pull したアーティファクトのファイル所有権のような
   shell の uid に依存する下流のあらゆるものも）、無関係な前のシナリオがたまたま
   クラッシュしたかどうかの関数になってしまいます。決定的な中核がそもそも締め出そうと
   している実行順序依存そのものです。さらに深刻なのは次の点です。`AdbDriver._rooted()` は
   `adb shell id -u` をドライバごとに1回だけ確認してキャッシュし
   （[`adb_driver.py:1230-1237`](../../bajutsu/common/drivers/adb/adb_driver.py)）、
   2つのアクチュエーション判断がこれを読みます——`double_tap` はこれが `True` を返すとき
   `input tap` の代わりに素の `sendevent` 経路を選びますし、二本指ジェスチャはこれが
   `False` を返すとき `base.UnsupportedAction` を送出し、`True` を返すときは実行します。
   root が漏れていれば、この両方が黙って裏返ります。本来なら root のない端末で
   `UnsupportedAction` を送出して失敗するはずの二本指ジェスチャのシナリオが、代わりに
   実行されて成功してしまい、これを決めるのは無関係な前のシナリオがたまたまクラッシュ
   したかどうかです——同じ種類の実行順序依存でありながら、ファイル所有権だけにとどまらず、
   シナリオの合否判定そのものに直接届いてしまいます。`adb unroot` 自身の失敗を、同じ
   ベストエフォートのラッパーの内側で握りつぶしてしまうことこそが、これを見えないままに
   してしまう原因です。そこで、この復元は*検証*します。試みるだけでは済ませません。
   2回目の `adb wait-for-device` のあと、`adb shell id -u` を読み直し、それでもなお `0` を
   答えるなら大きくログへ記録します（診断のための取得が、以降のあらゆるシナリオの
   アクチュエーションの仕方を黙って変えてしまうことは、決してあってはなりません）。
   pull 自身の失敗と同じように握りつぶすのではありません。このシナリオより後のあらゆるシナリオは、もともと
   そうであったのと変わらず新しいレジデントサーバと、新しい `adbd` を得ます。

   この「この時点より後にはどちらのチャネルも必要とするものがない」という正当化は、
   `run` の1シナリオごとのリースに限った話です。リースは毎回まっさらに解体・再構築
   されます（`pool.py`）。`crawl` はこれに当てはまりません。「`crawl` 自身のクラッシュ
   記録を拡張する」節は、この同じ `env.app_crash_artifacts`（`logcat` の層だけです。
   `crawl` は `app_crash_tombstone()` を一度も呼びません）をクロールのループへその
   まま通しますが、クロールのレーンは自身の巡回全体を通じて*1つ*の環境しか持ちません
   （`_build_lane`、[`cli.py:294-300`](../../bajutsu/crawl/cli.py)）。フロンティアの
   途中でクラッシュが着地しても巡回は終わっていません
   （`current_fp = None; continue`、
   [`_functions.py:668-669`](../../bajutsu/crawl/core/_functions.py)）。したがって、
   最初に確定した Android のクラッシュで tombstone を取得すれば、同じレーンのそれ以降の
   あらゆる確認が壊れ、しかもクロール自身が終わるまでレジデントサーバや reverse
   トンネルを組み立て直すものが何も残りません。`crawl` の Android 収集は、`logcat`
   だけを構造的に使います。`app_crash_tombstone()` へ一度も配線されないだけで
   十分だからです。本項目の初期の草案が `environment_for` へ通していた、`run` 専用の
   クロールレーンフラグは、この目的にはもう要りません。どちらのメソッドを呼ぶかで
   ゲートするだけで足ります。

### 失敗したシナリオの run ディレクトリへ収集をつなぐ

事後確認は経路の内側で動作し、他のあらゆる終端失敗がすでに通る同じステップループの
内側にとどまります。したがって `run_scenario` のふつうの `RunResult` 組み立ては、この
確認のあともそのまま変わらず走ります。失敗したステップ自身のスクリーンショット、すでに
完了したステップ、シナリオレベルの `after: on: error` ルールは、すべて `ElementNotFound`
が同じステップを失敗させた場合とまったく同じように残ります。シナリオ全体に対してすでに
動作しているビデオ録画も、同じように停止して添付されます。`AppCrashedError` は送出される
例外として `run_scenario` の外へ出ないため、エスケープした `BackendCrashError` に対して
だけ発火する `pipeline.py` 既存のクラッシュリトライループは、これを一度も見ません。
シナリオは、他のあらゆる終端ステップ失敗と同じように、リトライを止めるための特別扱いを
何も必要とせずに一度だけ失敗します。

証跡の*収集*は経路の内側にとどまります（`_finish_outcome` の中、「検知の方式」を参照）。
ただし、証跡のディスクへの*コピー*はその経路の外に置きます。BE-0421 自身のコピーが経路の
外にあるのと同じ理由からです。`_step_runner` のシンク
（[`bajutsu/common/orchestrator/loop/_loop_config.py`](../../bajutsu/common/orchestrator/loop/_loop_config.py)）
は `EvidenceSink` であり、その表面全体は `capture` / `wait_diagnostic` / インターバルの
開始・終了の組だけです。任意の名前での書き込みを持ちません。しかも実行中のシナリオに
スコープされており、run スコープの `RunArtifactWriter` と、`pipeline.py` がすでに保持
する `sid` を必要とするクラッシュの証跡には向きません。`Lease`
（[`bajutsu/common/runner/types.py`](../../bajutsu/common/runner/types.py)）に、
対になる2つの呼び出し可能オブジェクトを加えます。
`app_crash_artifacts: Callable[[], list[tuple[str, bytes]]]` と
`app_crash_tombstone: Callable[[], list[tuple[str, bytes]]]` です。`crash_artifacts`
がすでにそうしているのと同じように、それぞれ自前のモジュールレベルの no-op を既定値にして
`pool.py` の `lease()` クロージャでその隣に配線し、環境の2つのメソッドを直接読みます。
このさきへ通るのは前者だけです。`lz.app_crash_tombstone` は、`_run_on_lease` 自身の
戻ったあとの走査（後述）から直接呼び、ステップループへは一切通しません。そこへ通すことは、
本項目の Android の節がまさに退けたタイミングそのものだからです。

その呼び出し可能オブジェクトがステップループへ届く経路は、`relaunch` がすでに使っている
のと同じです。`pipeline.py:898` は `relaunch=lz.relaunch` を `run_scenario` へ渡し、
それが `_LoopConfig.relaunch` へ通ります。本項目はこれと対になる
`capture_app_crash: Callable[[], list[tuple[str, bytes]]] | None` フィールドを加え、
`lz.app_crash_artifacts` から同じ方法で通します。これを呼ぶのは `_finish_outcome` で
あり、`pipeline.py` ではありません。本項目の初期の草案では `pipeline.py` が
`lz.app_crash_artifacts()` を、`run_scenario` がすでに戻ったあとの走査から自身で
呼んでいました。それは、シナリオ自身の `after` フェーズ（`run_scenario` の内側で、
戻る前に走ります）がすでに目印を動かしてしまえる時点で `app_launched_at` を生きたまま
読むことになります——あとかたづけの `relaunch` がこれをどう壊すかは「iOS：`.ips`
レポートの照合」を参照してください。代わりに `_finish_outcome` の内側で呼べば、これを
閉じます。掃引はクラッシュが確定したまさにその瞬間に走り、同じシナリオの後続のどの
ステップ——あとかたづけを含みます——よりも確実に前に終わり、その結果は `StepOutcome`
に載って運ばれ、あとで読み直されることはありません。

`pipeline.py` の `_run_on_lease` は、`run_scenario` が返った直後、自身の `finally` が
リースを解放するよりも前、まだそのリースを保持したまま
`(*result.before_outcomes, *result.steps, *result.after_outcomes)` を `app_crashed` で
走査します。`result.steps[-1]` を読むのではありません。入れ子になったクラッシュは、
その外側の `if`・`forEach` の outcome を、クラッシュした本人の outcome より*あとで*
確定させるため（`_step_runner.py:220`、`:238`）、クラッシュした outcome が最後にある
とは限りません。また `result.steps` だけでは `before` と `after` の両フェーズを
まるごと見落とします。`RunResult.steps` は本体フェーズ自身のリストにすぎず
（`_functions.py:851`）、`before` のステップが失敗すると本体ステップの実行自体が
スキップされるため（`_functions.py:732-740`）、`[]` のままになります。走査が見つけた
とき、新しい `_write_app_crash_artifacts(lz, outcome, s, sid)` が
`_write_crash_artifacts`（BE-0421、`pipeline.py:803`）をほぼそのまま真似ます。ただし
兄弟が要らないパラメータが1つ増えています。兄弟はあらゆる証跡を `lz.crash_artifacts()`
から直接読みますが、こちらは見つけた
outcome 自身の `app_crash_artifacts`——確認の時点ですでに収集済みで、ここで再び掃引する
のではありません——から始めるため、走査が見つけたその outcome を暗黙のままにせず、
引数として渡す必要があります。そこから、条件なしにもう1つの呼び出し、
`lz.app_crash_tombstone()` でそれを拡張します。ここで初めて呼びます。これは本項目が
`_finish_outcome` の内側では収集しない、ただ1件の証跡です。理由は「Android：`logcat` の
クラッシュ用バッファをまず読み、root 権限があるときだけ tombstone を取得する」節が
述べるとおりです。取得には `adb root` が要り、それをシナリオの途中で発火させれば、
上がってきた `BackendCrashError` に `RunResult` そのものを奪われかねません。ここで
呼ぶのは本当に安全です。`run_scenario` はすでに戻っており、同じシナリオの後続の
どのステップも、これから再起動しようとしているレジデントサーバを経由して操作を
続けることはもうありません。`_run_on_lease` が持つのは `Lease` であってバックエンドの
識別子ではないため、この呼び出しは `result.backend == "adb"` のような分岐にせず、
条件なしのままにします。バックエンドごとの知識を決定的な中核から
締め出す「1つのプラットフォームは1つのバックエンド」という継ぎ目
（[`CLAUDE.md`](../../CLAUDE.md)）を保ち、2つ目のバックエンドが将来 tombstone 相当の
機能を持ったときに直す箇所も1つ減ります。組み合わさったリスト——`logcat` が先、tombstone の
項目があとから加わり、iOS・web・fake では `app_crash_tombstone()` が no-op なので
空のまま——を書き込みます。返ってきた `(name, content)` の組をそれぞれ
`writer.write_text(f"{sid}/app-crash/{name}", content.decode(errors="replace"))` という、
マスキングを行うテキスト側の経路で書き込みます。`write_bytes` ではありません。BE-0421
自身のコピーがそちらを使う理由と同じです。クラッシュレポートはテキストであり、
クラッシュしたアプリがそこへ秘密の値を反映させることもあります。そして
`_write_crash_artifacts` が `pipeline.py` に追記させるために返すのと同じ形で、
ディレクトリを名指しする一節を `result.failure` へ追記します。書き込みの問題も、
`app_crash_tombstone()` 自身の失敗も、ログに記録するだけで送出しません。同じ姿勢に
合わせたものです。診断のための収集が、すでに確定した失敗を別の失敗へすり替えては
なりません。tombstone を失っても、outcome にすでに載っている `logcat` の層を
巻き添えにしてはなりません。これは素の事後確認であり、新しい `except` 節では
ありません。シナリオ自身のリトライの挙動は、これが走る時点ですでに確定しています。

`StepOutcome.app_crash_artifacts` は outcome に載ったまま、上のディスクへのコピーだけでなく
もう1つ消費者を抱えます。`manifest_dict` の `_scenario_dict(r)`
（[`bajutsu/common/report/manifest.py`](../../bajutsu/common/report/manifest.py)）は素の
`asdict(r)` であるため、`steps`・`before_outcomes`・`after_outcomes` を通じて届くあらゆる
`StepOutcome`——クラッシュした本人も含みます——がそのままマニフェストの辞書に載り、
`write_json` の `json.dumps` は `default=` を持ちません
（[`bajutsu/common/evidence/sink.py`](../../bajutsu/common/evidence/sink.py)）。生の `bytes`
には JSON 表現がないため、最初の app-crash シナリオが `manifest.json` を書き込む際に
`TypeError` を送出してしまいます。クラッシュが正しく分類された*あとで*、run 全体の
マニフェストと HTML レポートを道連れにする形です。本項目はすでに `crawl` 自身の
`Crash.artifacts`（後述の「`crawl` 自身のクラッシュ記録を拡張する」）については逆向きの
判断を正しく下しています。同じ理由で `serialize.py` の往復からあえて除いています。
`_scenario_dict` にも、`run` 側の同じ危うさに対応する、対になる除外が要ります。すでに
行っている `wall_offset_s` の pop
（[`bajutsu/common/report/manifest.py`](../../bajutsu/common/report/manifest.py)）の隣に、
`steps`・`before_outcomes`・`after_outcomes` それぞれの outcome の辞書を歩いて
`app_crash_artifacts` も pop します。`wall_offset_s` はトップレベルの `RunResult` フィールドで
1回の pop が届きますが、こちらはその3つのリストが運ぶあらゆる `StepOutcome` の内側に
ネストされているため、除外もそれらを歩く必要があります。`report/load.py` 側の逆変換に
対応する変更は要りません。`_step` の `_kw(StepOutcome, d)` は、`d` にないフィールドを
すでにデータクラスの既定値で組み立て直します。`RunResult` の `wall_offset_s` に対してすでに
行っているのと同じ仕組みです。取り除かれた `app_crash_artifacts` は、ふつうのステップが
すでに持つ空の既定値とまったく同じように `()` として組み立て直されるだけです。クラッシュは
それでも完全に報告されます。`outcome.reason` と `outcome.app_crashed`（シリアライズの
危うさのない、ただの `bool` です）はそのまま往復し、この節がすでに `{sid}/app-crash/` の下へ
書き込んでいるマスキング済みのコピーこそが、レポートの読み手がたどる恒久的なコピーです。
outcome 上のメモリ内の bytes は、その書き込みに届くためだけに存在し、マニフェストに届く
ためのものではありません。`_kw` 自身のドキュメントコメント
（[`load.py:34-41`](../../bajutsu/common/report/load.py)）は現在、`wall_offset_s` を
往復保証の対象外となる「唯一の意図的な例外」と名指しています。この主張は、本項目自身の
2つ目の除外が着地した瞬間に古びてしまいます。そのコメントはまさに、見落としに見える
除外を将来の寄稿者が「直して」しまわないよう警告するために存在します。ここで述べて
おかなければ、そのコメントが防ごうとしていたのと同じ危険が `app_crash_artifacts` 自身の
上で繰り返されます。往復しないことに気づいた寄稿者が「*唯一の*意図的な例外」を読み、
`_scenario_dict` の pop を不具合修正として取り除いてしまえば、この節がそもそも避けようと
している `manifest.json` の `TypeError` を再び開いてしまいます。しかも
`test_round_trip_through_manifest_is_lossless` 自身のフィクスチャはこのフィールドを既定値
のままにしているため、この回帰をそのテストは捉えられません。`app_crash_artifacts` を
`wall_offset_s` と並べて同じコメントへ書き加えることは、本項目自身の作業範囲であり、
あとまわしの作業ではありません。

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
確認がありません。そのためここでの誤検知は毎回、全タイムアウト分のポーリングを払いかねません。
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
`artifacts` を持たない `Crash` を記録します。

この `d.app_crash_signal()` 呼び出しは、自前の `except (base.BackendCrashError, OSError,
subprocess.CalledProcessError): pass` で
包みます。チャンネルエラーを未確認の答えと同じ扱いにします。ここでの広い捕捉は、
「Android：`logcat` のクラッシュバッファを先に……」が `app_crash_signal()` 自身に施す
修正の代わりではなく、それに並ぶ2つ目の、呼び出し側のガードです。`AdbDriver` の
`RunFn` は `subprocess.run(..., check=True)`
（[`adb/_functions.py:111`](../../bajutsu/common/backend_cli/adb/_functions.py)）であり、
`backend_cli/adb/` の下には `BackendCrashError` を送出する場所がどこにもないため、
`except base.BackendCrashError` だけでは、この呼び出し箇所まで握りつぶされずに届いた
`pidof`・`exit-info` の失敗をなお見逃してしまいます。`crawl` は `Crash` を記録して
歩き続け、`artifacts` は持ちません。「iOS：`app.state`」は `run` に対して、チャンネル
エラーをあえて `None` へ握りつぶさず、`pipeline.py` がすでに持つ回復経路へ
`XcuitestRunnerCrashError` としてそのまま伝播させると決めています。しかし `crawl` には
そうした経路がありません。`bajutsu/crawl/` の下には `BackendCrashError` を扱う場所が
どこにもなく、しかもこの呼び出しは `_walk` 自身の `try`（`_functions.py:597-655`、
`action.perform`/`_observe` だけを覆います）の外にあります。「ロック外の、純粋な
決定的読み取り」というこの窓は、本項目がここへ最初の送出しうる往復を持ち込むまでは
ただの Python でした。ここで握りつぶさなければ、その例外は `_run` の
`except Exception: coord.note_failure(exc)` を通り、join のあとメインスレッドで
再送出され、`cli.py` の素の `_execute(...)` から `_finish(...)` へ——両者のあいだに
`try`/`finally` はありません——届きます。つまり `_finish` は一度も走らず、`write_repros`
は `screen_map.crashes` を一度も歩かず、本項目がこの巡回*全体*にわたってメモリに
蓄えてきた再現シナリオと証跡のすべてが、まさに本項目が証跡を残すために存在する、
その失敗そのもので失われてしまいます。

ここで収集する理由は、正しさだけでなく
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
自身の再現ファイルと一緒に証跡まで失ってしまいます。空でない `artifacts` は、`run` 自身の
コピーが使うのと同じマスキングを行う `writer.write_text(…, content.decode(errors="replace"))`
経路——その隣の再現 `.yaml` に `write_repros` がすでに使っている経路でもあります——を通じて
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
`actuation (xcuitest)` や `golden (adb)`——どちらも両レーンの必須アグリゲータから意図的に
除外されています——の隣に、ゲートしない PR ごとのシグナルとして
配置します。オンデバイスで新しく配線されたカバレッジは、必須の `E2E` チェックへ昇格する前に、
まずそこで安定性を得ます。両レーンの他のあらゆる新しいシグナルが辿ってきたのと同じ道です。

### web backend と fake backend への影響

これらは別々の2つの継ぎ目であり、どちらのバックエンドもどちらの半分にも意味のある
作業は要りません。`PlaywrightDriver` は `AppCrashSignal` を実装しないため、事後確認の
`isinstance` による問い合わせは `False` を返し、プロトコルを一切宣言しない他のあらゆる
ドライバと同じように読み飛ばされます。`WebEnvironment`
（[`bajutsu/common/platform_lifecycle/environments/web.py`](../../bajutsu/common/platform_lifecycle/environments/web.py)）
は自前の `app_crash_artifacts()` と `app_crash_tombstone()` を宣言し、どちらも `[]` を
返します。すでに `take_crash_snapshot()` に対して持っている宣言の隣に加わる、2行の
追加です。`RunEnvironment` はどの具象クラスも継承しない構造的プロトコルであり、3つの
メソッドのいずれも継承によって落ちる先となる既定値を持たないためです。`FakeEnvironment`
（[`bajutsu/common/platform_lifecycle/environments/fake.py`](../../bajutsu/common/platform_lifecycle/environments/fake.py)）
は `_DeviceEnvironment` から同じ no-op を継承し、テスト用の fake ドライバも
`AppCrashSignal` の継ぎ目では同じ振る舞いをします。本項目によって、web backend や
fake backend の実行が収集する内容は変わりません。

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

- [ ] Unit 1 — `base.AppCrashedError`（新規ファイル）。`Driver` プロトコルとは別に設ける、
      `base.AppCrashSignal` というケイパビリティプロトコル（`app_crash_signal() -> str |
      None`）。新しい `StepOutcome.app_crashed: bool = False` フィールド。`ruff` の `TRY`
      系列は `TRY003` だけを無視して選択されているため、Unit 7 の送出・捕捉（後述）には
      `CLAUDE.md` のインラインコメント規則が求める理由つきの独自の `# noqa: TRY301` が
      要ります。`AppCrashedError` を `signal` から組み立てるただの文字列にせず、実際に
      送出する型のまま残すかどうかを決める箇所なので、ここに名指しておきます。
- [ ] Unit 2 — iOS：`XCUIApplication.state` を読む新しい `openapi.yaml` のルートと、
      生成された `APIHandler` のメソッド。`Router.swift` ではなく `RunnerServer` から
      配信します。`XcuitestDriver.app_crash_signal()` が `AppCrashSignal` を実装し、
      `notRunning` をシグナルとして分類し、チャンネルエラーは `XcuitestRunnerCrashError`
      としてそのまま伝播させます。新しい `is_real_device` コンストラクタ引数を
      `device_os` と同じ方法で `make_driver` から通し、`deviceType: device` では
      `app_crash_signal()` がその場で `None` を返すようにします。本項目は Simulator だけを
      対象にします。
- [ ] Unit 3 — iOS：`_spawn_cold` の中、`xcodebuild` の spawn の*直前*に記録する
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
- [ ] Unit 4 — Android：`backends.make_driver` から `AdbDriver.__init__` へ、`fetch_clock` と
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
- [ ] Unit 5 — Android：各起動の箇所の `e.launch(...)` 呼び出しの*直前*に記録する
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
- [ ] Unit 6 — `RunEnvironment.app_crash_artifacts()` と `RunEnvironment.app_crash_tombstone()`
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
- [ ] Unit 7 — `run_scenario` / `_step_runner.py`：新しい `_finish_outcome` ヘルパーを、
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
- [ ] Unit 8 — `pipeline.py`：`_run_on_lease` が `run_scenario` の直後、まだ同じリースを
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
- [ ] Unit 9 — `TracingDriver`：`base.AppCrashSignal` を `_PROTOCOLS` へ加え、
      `--trace-driver` がそれを実装したドライバに対してだけ実属性として設置するようにします。
- [ ] Unit 10 — `crawl` 自身の統合。`_build_lane` のレーンごとの `app_crash_artifacts`
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
- [ ] Unit 13 — テスト。起動未確認フラグ——Unit 7 が加える3つ目のラッチであり、本項目が
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
  フィールド処理。すでに `wall_offset_s` 自身の組み立て直し経路として文書化されており、
  本項目では変更しません
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

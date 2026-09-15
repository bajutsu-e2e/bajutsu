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
同じ収集ロジックを、`bajutsu crawl` がすでに持つ同種の検知にも拡張します。web（Playwright）
バックエンドとその独自シグナルは、後続の項目に委ねます。理由は「検討した代替案」に記します。

## 動機

`bajutsu` はすでに、自分自身が引き起こしたのではない種類の失敗を区別しています。
`base.BackendCrashError` は、バックエンド自身のドライバプロセス（常駐する XCUITest ランナーの
ホスト、adb の常駐サーバ、ブラウザプロセス）が落ちたことを名指します
（[`bajutsu/common/drivers/base/backend_crash_error.py`](../../bajutsu/common/drivers/base/backend_crash_error.py)）。
実行パイプラインはそこから回復します。死んだリースを破棄し、新しいデバイス上でシナリオ全体を
リトライします。
[BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact-ja.md)
は、その落ちたランナー自身のログと、iOS では `.ips` レポートを、失敗したシナリオの証跡へコピー
することを提案しています。どちらも、*バックエンド*側の不具合を名指しています。チームがテストして
いるアプリ側ではありません。

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

この隙間は、本物のアプリの不具合に対して開発者の実時間を奪います。本項目が対象とするのは、
まさにこのケースです。`BackendCrashError` 自身の回復は、ドライバや環境側の不具合をすでに扱って
おり、本項目はその経路に手を加えません。アプリが落ちて失敗したシナリオは、今日のレポートでは、
セレクタの `id` がリネームされた、または画面が読み込まれなかったために失敗したシナリオと見分けが
つきません。同じ `ElementNotFound`、同じ「スクリーンショットを見て推測する」という出発点です。

本項目が生み出す観測可能な違いは次の点です。着地すれば、アプリがクラッシュしたシナリオは、その
事象を名指しするメッセージで失敗します。`runs/<run_id>/<sid>/app-crash/` ディレクトリは、
プラットフォーム自身の証跡を保持します。iOS では `crash-<bundle>-<pid>.ips`、Android では
`logcat-crash.txt`（端末が許せば `tombstone.txt` も）です。開発者は、2つの誤った説明を消去する
前に、正しいファイルを最初に開けるようになります。

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
仕上げ方を知っている、ふつうの終端ステップ失敗として扱います。

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

確認そのものは1か所にあります。`bajutsu/common/orchestrator/loop/_step_runner.py` の
ステップごとのループで、そのステップの最終的な `outcome.ok` を確定させた直後です
（`outcome.ok, outcome.reason, outcome.assertion_results = ok, reason, results`）。
どのステップの結果も、tip の解除やアラートガードの再試行が終わったあと、必ずこの1点へ
収束します。この地点は、あらゆる種類のステップの終端失敗を等しく見ます。アクションの
`ElementNotFound`、失敗した `wait`、失敗した `assert`、失敗した `handleSystemAlert` の
どれもです。これより上にある3つの再試行は、例外を送出せず `bool` と理由の組を返します。
確認をもっと手前、`_run_step_body` 自身の例外の受け皿の中に置いていた初期の草案では、
アクション例外のケースしか見えず、残り3つを取りこぼしていました。

この地点で `outcome.ok` が `False` であり、かつ `isinstance(active_driver,
base.AppCrashSignal)` が成り立つとき、ループは `active_driver.app_crash_signal()` を
呼びます。`None` でない答えは、その場で `base.AppCrashedError(signal)` を送出し、同じ式の
中で捕まえ、そのメッセージを `outcome.reason` へ折り込みます。この1点より先へ伝播すること
はありません。`active_driver` は、そのステップを実際に操作したドライバです。ネイティブの
ドライバであることも、`web` ブロックの中では `WebContextDriver` であることもあります。
後者に対しては `isinstance` が `False` を返すため、確認は no-op になります。「はじめに」で
述べた本項目の web バックエンドに対する範囲と一致します。

### iOS：要素ツリーではなく `app.state` を使う

XCUITest は、対象アプリのプロセス状態を `XCUIApplication.state` としてすでに公開しています。
`notRunning` というケースが、「本当に落ちているか」という問いに直接答えます。`crawl` の
`is_app_alive` は、同じ事実を空、または予期しない要素ツリーから推測しています。この推測には
誤検知の実際のリスクがあります。システムアラートがアプリの UI を覆っている場合や、シナリオが
意図的に `background` ステップを実行した場合です。`app.state` はどちらも回避します。
バックグラウンドへ回っただけで生きているアプリは `runningBackgroundSuspended` または
`runningBackgroundActive` を返し、`notRunning` にはなりません。

`notRunning` という答えだけでは、あらゆるプラットフォームでクラッシュを証明できません。
実機では、OS によるメモリ不足時の強制終了や、起動未完了も同じ答えを返し得ます。どちらも、このバックエンドが操作する Simulator には当てはまりません。Simulator の
ホストはデスクトップ級のメモリを持ち、実機のようにフォアグラウンドのアプリを jetsam で
終了させません。シナリオのスキーマにも、テスト対象アプリを終了させるステップがそもそも
存在しません。加えて、この確認はステップがすでに失敗した後、しかも同じシナリオの手前の
すべてのステップでアプリが動作していると確認できた*あと*にしか走りません。したがって、
起動が完了しなかったケースは、この事後確認が出会う場面ではありません。ここでの
`notRunning` という答えは、直前まで動いていたアプリが今は動いていないことを意味し、
この Simulator という環境には、それ以外にそうなる経路がありません。

新しいルートを
[`BajutsuKit/Sources/BajutsuRunner/openapi.yaml`](../../BajutsuKit/Sources/BajutsuRunner/openapi.yaml)
へ加えます。BE-0316 の `/systemAlert/query` と同じ形です。リクエストボディを受け取り、
状態を運ぶ JSON を返します。生成された `APIHandler`
（[`BajutsuKit/Sources/BajutsuRunner/APIHandler.swift`](../../BajutsuKit/Sources/BajutsuRunner/APIHandler.swift)）
に対応するメソッドが加わり、そのプロバイダ実装がランナー自身の `XCUIApplication` に
`.state` と `.processIdentifier` を問い合わせます。今日、実際にリクエストを処理している
のは `Router.swift` ではなく `RunnerServer.swift` です。`APIHandler` を組み立て、その
生成済みルートを登録しているのは後者です。`Router.swift` はパリティテストのためだけに
残っており、実際の実行では何も答えません。本項目の初期の草案が指定したように、ルートを
`Router.swift` にだけ加えると、`XcuitestDriver` のリクエストは実サーバに対して404に
なってしまいます。

`XcuitestDriver`
（[`bajutsu/common/drivers/xcuitest/xcuitest_driver.py`](../../bajutsu/common/drivers/xcuitest/xcuitest_driver.py)）
は、このルートを1回呼ぶことで `app_crash_signal()` を実装します。`notRunning` という
答えがシグナルの文字列になり、同じ応答が運ぶプロセス ID も一緒に運びます。それ以外の
状態はすべて `None` を返します。この呼び出しに届いたチャンネルエラーは `None` へ
握りつぶしません。それは既存の `XcuitestRunnerCrashError` であり `BackendCrashError` の
一種です。本項目は、これをすでに受け持つ回復経路へそのまま伝播させます。ステップ自身の
セレクタ失敗の直後にルートが失敗するのは、ふつうの競合であり、チャンネルがこのステップと
無関係だという根拠にはならないからです。

### iOS：`.ips` レポートの照合、BE-0421 自身の手法を適用する

[BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact-ja.md)
も、本項目と同じく状態はまだ**提案**であり、着地していません。ランナー自身の `xcodebuild`
プロセスについて、macOS が `~/Library/Logs/DiagnosticReports` へ書き出したものの中から
正しい `.ips` ファイルを見つける課題を、すでに検討ずみです。その照合は名前と時刻による
もので、レポート自身のヘッダが解析できれば PID で絞り込み、`ReportCrash` が異常終了した
プロセスの消滅後に非同期でシンボル化するため、その完了を待ってから探します。本項目は、
BE-0421 のメソッドがすでに存在すると仮定するのではなく、名前・PID・時刻という同じ3点の
照合を、テスト対象アプリ自身のバイナリに対して `xcodebuild` の代わりに採用します。どちら
かが先に着地した時点で、もう一方はその `RunEnvironment` のメソッドを共有し、独立した
2つ目の掃引を持たないようにするべきです。

`XcuitestEnvironment`
（[`bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py)）
は、対象アプリをすでに起動し、再起動もしています。ここに `app_launched_at` という
タイムスタンプと、`app_launched_pid`（`XCUIApplication.processIdentifier` を起動時に
読んだもので、前述の新しいランナーのルートが事後確認で読むのと同じ呼び出しです）を、
各起動の直後に記録するよう拡張します。名前と時刻だけの照合では、ある Simulator の
クラッシュレポートを別の Simulator のものと区別するには不十分です。`DiagnosticReports`
は、同じ Mac 上で動くすべての Simulator が共有する単一のディレクトリです。並列実行の
CI ホスト（`--workers 2`）では、同一の対象バイナリを動かす Simulator が2台、同じ時間帯に
存在し得ます。起動時に記録した PID が、実際にクラッシュしたそのプロセス1つへ照合を
絞り込みます。BE-0421 がランナー自身のレポートに対してすでに使っている絞り込みと
同じです。

新しく `app_crash_artifacts(signal: str) -> list[tuple[str, str]]` を `RunEnvironment`
プロトコル
（[`bajutsu/common/platform_lifecycle/protocols/run_environment.py`](../../bajutsu/common/platform_lifecycle/protocols/run_environment.py)）
に加えます。既定は `[]` です。多くの環境が必要としないメソッドに対して
`request_device_replacement()`（BE-0354）がすでに確立している no-op の既定値と同じ形です。
このメソッドは、後述する `_step_runner.py` の事後確認の内側で同期的に実行されます。
この環境を保持するリースがまだチェックアウトされたままの状態で、`pipeline.py` がそれを
解放するよりずっと前です。したがって、このメソッドが読む照合の条件（`app_launched_at`、
`app_launched_pid`）はその場で生きたまま読まれ、環境を再利用する別のワーカーの、後の
起動によって上書きされることがありません。

`ReportCrash` は、アプリが落ちたその瞬間にはまだレポートを書き終えていないことがあります。
掃引は `~/Library/Logs/DiagnosticReports` を、対象の実行ファイル名と PID に一致し、
更新時刻が `app_launched_at` 以降であるレポートを求めて、数秒を上限にポーリングします。
これはこの1つのメソッドの中だけの、短く上限のある待機であり、シナリオそのもののリトライ
ではありません。シナリオは、掃引の結果にかかわらず一度だけ、すぐに失敗します。この掃引と、その中の処理はすべて、最後の書き込みだけでなく本体全体を
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
インラインのテストダブルが繰り返すことになる宣言です。素のコンストラクタ引数として
渡すほうを選びます。

空の `pidof` という答えは、アプリがまだプロセスを保持しているはずの場面では、必要条件
ではあっても十分条件ではありません。起動が完了しなかった場合や、本項目がシナリオレベル
の原因を持たない終了とも一致します。Android には、通常のテスト条件下で jetsam のような
OS による強制終了はありませんが、ふつうのプロセス終了もクラッシュと同じ答えを `pidof`
に返させます。`adb shell dumpsys activity exit-info <package>` は、プロセスの直近の
終了について、プラットフォーム自身が記録した `ApplicationExitInfo` の理由を報告します。
`CRASH` や `CRASH_NATIVE` を、`ANR`・`LOW_MEMORY`・`USER_REQUESTED` から区別できます。
これは API 30 以降で利用でき、このリポジトリの CI がすでに起動している API 34 の AVD
でも利用できます。`app_crash_signal()` は、`pidof` が空を返した直後に一度だけこれを
読み、`CRASH` か `CRASH_NATIVE` のときにだけ事象を確定します。それ以外の理由では
`None` を返します。シグナルをまったく持たないバックエンドが返すのと同じ、「確認
できない」という答えです。これは、`app.state` の `notRunning` が Simulator 自身の
制約から無償で得ている裏付けに相当します。Android では、プラットフォーム自身の
報告する終了理由が、adb にとって同じ役割の積極的な確認を与えます。

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
   各起動の直後に `adb logcat -b crash -c` でバッファをクリアします。これにより、
   あとで行う `adb logcat -b crash -d` のダンプは、このシナリオが実行している起動
   より前の内容を含めなくなります。同じパッケージが同じ端末上の以前の実行で残した
   古いクラッシュを拾うことがありません。ダンプは2通りの方法で解析します。マネージド
   コード（Java・Kotlin）のクラッシュを示す `FATAL EXCEPTION` ブロックと、それが
   見つからない場合に、NDK クラッシュを示すネイティブクラッシュバッファ自身の
   `Fatal signal <n>` という見出し行です。`logcat` のクラッシュバッファが実際に
   運ぶ2つの形式です。一致したほうを抽出し、`logcat-crash.txt` として書き出します。
   これは、この adb バックエンドが到達できるどの AVD や実機でも保証される唯一の
   証跡です。
2. **tombstone の取得**は、ベストエフォートで root 権限に依存します。`adb root`
   は、このバックエンドが対象とするエミュレータイメージに対しては、すでに日常的な
   操作です。これに続けて、もっとも新しい `/data/tombstones/tombstone_NN` のうち、
   更新時刻が前述の起動の目印以降のものを1件取得します。比較は端末自身の相対的な
   時刻どうしで行うため、ここでも時計のすり合わせは不要です。実機、user ビルド、
   `adb root` を拒む状態のいずれでも、この層は黙ってスキップします。事象自体は
   報告済みであり、`logcat-crash.txt` はすでに届いています。root を拒む端末が
   失うのは、マネージドコードのクラッシュがそもそも必要としないネイティブフレーム
   の詳細だけです。

### 失敗したシナリオの run ディレクトリへ収集をつなぐ

`Lease`（[`bajutsu/common/runner/types.py`](../../bajutsu/common/runner/types.py)）に
`app_crash_artifacts: Callable[[str], list[tuple[str, str]]]` を加えます。
`request_device_replacement` と同じくモジュールレベルの no-op をデフォルトにし、
`pool.py` の `lease()` クロージャで同じように配線します。`pipeline.py` の
`_run_on_lease` は、`lz.app_crash_artifacts` を `run_scenario`
（[`bajutsu/common/orchestrator/loop/_functions.py`](../../bajutsu/common/orchestrator/loop/_functions.py)）
へ、`relaunch` と同じくもう1つの任意のコールバックとして渡します。`run_scenario` は
これを、前述の `_step_runner.py` のループまで通します。

この事後確認は、クラッシュを確定させた直後にこれを呼び、そのシグナルを
`outcome.reason` へ折り込みます。呼ぶ時点で、`_run_on_lease` が取得したのと同じリースを
まだ保持しています。`pipeline.py` 自身の `finally` がそれを解放するよりも前です。
本項目の初期の草案が、解放後に証跡を読むことで残していた競合を、これで閉じます。
返ってきた `(name, text)` の組はそれぞれ、
`sink.write_text(f"app-crash/{name}", text)`
（[`bajutsu/common/evidence/sink.py`](../../bajutsu/common/evidence/sink.py)）という、
マスキングを行うテキスト側の経路で書き込みます。`write_bytes` ではありません。
`write_bytes` 自身のドキュメントコメントは、これがシンクの検査できない内容のため
であり、マスキングせずに記録すると明言しています。クラッシュレポートはテキストで
あり、クラッシュしたアプリがそこへ秘密の値を反映させることもあります。それは
まさに `write_text` のマスキングが捉えるべきものです。そこで `app_crash_artifacts`
は、生のバイトをそのまま返す代わりに、各レポートを `str` へデコードしてから
（まれな非 UTF-8 のバイトには `errors="replace"` を使い）返します。書き込みの
問題はログに記録するだけで、例外は送出しません。BE-0421 自身の姿勢と同じです。
診断のための収集が、すでに確定した失敗を別の失敗へすり替えてはなりません。

この確認は、他のあらゆる終端失敗がすでに通る同じステップループの内側で、経路の
内側にとどまったまま行われます。したがって `run_scenario` のふつうの `RunResult`
組み立ては、この確認のあとも変わらず走ります。失敗したステップ自身のスクリーン
ショット、すでに完了したステップ、シナリオレベルの `after: on: fail` ルールは、
すべて `ElementNotFound` が同じステップを失敗させた場合とまったく同じように残り
ます。シナリオ全体に対してすでに動作しているビデオ録画も、同じように停止して
添付されます。`pipeline.py` には、この事象のための新しい `except` 節も、リトライ
を抑える特別な処理も一切必要ありません。`AppCrashedError` は送出される例外として
`run_scenario` の外へ出ないため、エスケープした `BackendCrashError` に対してだけ
発火する `pipeline.py` 既存のクラッシュリトライループは、これを一度も見ません。
シナリオは、他のあらゆる終端ステップ失敗と同じように、リトライを止めるための特別
扱いを何も要らずに一度だけ失敗します。

### `crawl` 自身のクラッシュ記録を拡張する

`bajutsu/crawl/core/_coordinator.py` の `record_crash` は、`is_app_alive` が崩壊を
報告したときに、コーディネータ自身のロックを保持したまま、すでに `Crash` を追加し、
そのロックを解放する前にクロールの `on_event` コールバックを呼びます。`crawl()`
（[`bajutsu/crawl/core/_functions.py`](../../bajutsu/crawl/core/_functions.py)）自身は、
環境も証跡の書き込み先も持ちません。受け取るのは注入されたコールバックだけです。
`driver`、`reset`、`is_alive`、`recover`、`on_event`、その他です。これが、クロール
のコアを `platform_lifecycle` から切り離し、同じループであらゆるバックエンドを
駆動できるようにしています。本項目の初期の草案が提案したように、そこへ環境への
参照を持ち込めば、あらゆるバックエンドと、高速なテストスイートのあらゆる fake が
それを1つ持つ必要に迫られます。

代わりに、収集は `on_event` を通じて `crawl` へ届きます。すでに
[`bajutsu/crawl/cli.py`](../../bajutsu/crawl/cli.py) が配線しており、環境と実行の
証跡書き込み先の両方がそこにすでに存在します。`on_event` は同期的に発火し、しかも
`record_crash` 自身のロックの内側で、新しい `Crash` が追加された直後に呼ばれます。
したがって、その瞬間の `len(screen_map.crashes)` は、`crawl` 自身のマルチワーカー
（`extra_workers`）のもとでも安定した、競合のないその事象の番号です。同じロックの
内側に2つの `record_crash` 呼び出しが同時に入ることはありません。CLI の既存の
`on_event` 関数はその番号を読み、新しく見つかったクラッシュに対して
`environment.app_crash_artifacts(signal)` を呼びます。シグナルの文字列は、
`is_alive` の答え（それ自身は素の `bool` です）から CLI 自身が組み立てます。
本項目が `run` 向けに加えるのと同一の収集ロジックを再利用し、2つ目の実装は
作りません。結果は `crashes/crash-NNN/app-crash/` の下に書き込みます。`NNN` は
[`bajutsu/crawl/repro.py`](../../bajutsu/crawl/repro.py) がそのクラッシュ自身の
`crashes/crash-NNN.yaml` という再現シナリオにすでに使っている、同じ0埋めの
1始まりの番号です。このパスは再現ファイルの隣にあり、同じ名前を持つ最上位の
ディレクトリではないため、2つは並んで見つかり、並んでソートされます。クロール
自身の検知は、すでに使っている UI ツリーのヒューリスティックのままです。
`crawl` には、`run` と違って、事後確認をぶら下げるシナリオステップがありません。
2つの入口のあいだで共有されるのは収集であり、検知は共有しません。

### 実機での本物のクラッシュで、スタブだけでなく証明する

ユニットテストは `~/Library/Logs/DiagnosticReports` や、疑似的な `logcat`・tombstone 取得をスタブ
できます。しかし、この設計が前提とする基盤側の仕組み、`ReportCrash` 自身の `.ips` 書き出しや
`logcat` のクラッシュバッファが、実機の Simulator やエミュレータ上でも同じように振る舞う
ことまでは証明しません。showcase アプリ
（[`demos/showcase/`](../../demos/showcase)）に、デバッグ専用の「強制的にクラッシュさせる」
操作を追加します。
[`ConformanceView.swift`](../../demos/showcase/ios/swiftui/Sources/ConformanceView.swift) が
自身のオンデバイス診断にすでに使っているのと同じ、デバッグビルド限定の作法でゲートされたボタン
です。iOS では `fatalError()` を、Android では main スレッドで未捕捉の例外を送出します。各
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

`PlaywrightDriver` とテスト用の fake backend は、`AppCrashSignal` と `app_crash_artifacts()`
のどちらも実装しません。その必要もありません。どちらも opt-in のケイパビリティであり、事後確認は
`isinstance` と no-op の既定値だけでそれを問い合わせます。どちらのバックエンドにも、
実装必須のメンバーとしてスタブを持たせる必要はありません。本項目によって、web backend や
fake backend の実行が収集する内容は変わりません。

## 検討した代替案

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| 既存の `BackendCrashError` 回復ループでリトライする | クラッシュ検知をバックエンドクラッシュと同じ扱いにし、リースを破棄してリトライする | 壁打ちで却下しました。この事象は、一時的なインフラの不調ではなく、アプリ自身の不具合である可能性が高いためです。再起動してのリトライは、再び失敗する見込みが高いシナリオにクラッシュ回復の予算を費やし、本物の不具合を flakiness として吸収してしまう危険があります（BE-0049）。 |
| 毎ステップの前に `app.state`・プロセスの生存を事前にポーリングする | 各ステップの実行前に、アプリがまだ動作しているかを確認する | 壁打ちで却下しました。構造上まれな失敗モードを捉えるためだけに、グリーンな実行を含むあらゆるシナリオのあらゆるステップへドライバの往復を1回追加してしまうためです。事後確認の設計でも、その事象が起きたまさにそのステップで捕まえられます。そのステップ自身のアクションかクエリが、すでに失敗しているからです。 |
| `AppCrashedError` を `run_scenario` の外へ送出し、`pipeline.py` に `BackendCrashError` を真似た専用の `except` 節を設ける | レビューで却下しました。バックエンドがクラッシュしたシナリオと違い、アプリがクラッシュしたシナリオでは、ドライバもバックエンドプロセスも、動作中のビデオ録画も残ります。落ちているのはアプリだけです。別経路の例外は、`run_scenario` 自身の組み立てが他のあらゆる終端失敗に対してすでに生み出している、ステップ・証跡・`after: on: fail` の発火を、ゼロから作り直す終端 `RunResult` で捨ててしまいます。 |
| `app_crash_signal()` を `Driver` プロトコルの必須メンバーにする | レビューで却下しました。`Driver` は `@runtime_checkable` であり、あらゆる実装（`XcuitestDriver`、`AdbDriver`、`PlaywrightDriver`、`XcuitestLiveDriver`、fake backend、`WebContextDriver` のような狭いラッパー）がスタブを持つ必要に迫られます。本項目が確認するつもりのないバックエンドも例外ではありません。このコードベースがすでに `InterruptionPolicyTarget` や `SettledReadProvider` に使っている、狭い opt-in のケイパビリティプロトコルであれば、必要とする2つのバックエンドだけに届きます。 |
| Android で `logcat` のクラッシュバッファだけを使い、tombstone は取得しない | ネイティブクラッシュの取得を諦め、常に取得できる `logcat` だけに頼る | 却下しました。ネイティブ（NDK）クラッシュの完全なバックトレースを失い、`logcat` 自身が出力する簡略化された要約しか残らないためです。常に取得できる基盤としては残し、tombstone の取得はそれを置き換えるのではなく、端末が許す場合により豊かな詳細を上乗せします。 |
| Android で root 権限に依存する tombstone 取得だけを使い、`logcat` へのフォールバックを持たない | tombstone の取得だけに頼り、`logcat` の抽出は実装しない | 却下しました。実機、user ビルド、`adb root` を拒むエミュレータイメージでは、何も取得できなくなってしまうためです。`logcat` のクラッシュバッファは昇格した権限を必要とせず、よくあるマネージドコードのクラッシュについてすでに完全なスタックトレースを運びます。 |
| 新しいシナリオアサーション（例：`assert: appCrashed: false`）を追加する | シナリオ作者が明示的に「アプリがクラッシュしていないこと」を検証できるようにする | 却下しました。この事象は、ステップ自身のアクションかクエリの失敗によって、すでにシナリオを終わらせているためです。それを確認するアサーションが後から走れる時点は、シナリオの中に残っていません。showcase 自身のテスト用シナリオは、代わりに、失敗の*形*を run の外側から検証します。`fault-injection (xcuitest)` がすでに採っている方式と同じです。 |
| `video`・`deviceLog` と同様、`capturePolicy` の opt-in ルールの背後に収集を隠す | 明示的な指定がない限り収集を行わない | 却下しました。BE-0421 が自身の証跡について挙げた理由と同じです。この収集は、すでに失敗が確定したシナリオに対して一度だけ走ります。コストは、範囲の定まった掃引かログの読み取り1回であり、明示的な要求の背後へ隠すべき定常的なステップごとの負荷ではありません。 |

## 進捗

> 作業の進行に合わせて最新の状態を保つ。チェックリストは「どう実現するか」の MECE な作業分解を
> そのまま反映する（作業の単位ごとに1項目）。ログは変更内容とその日時を古い順に記録し、PR に
> リンクする。

- [ ] Unit 1 — `base.AppCrashedError`（新規ファイル）。`Driver` プロトコルとは別に設ける、
      `base.AppCrashSignal` というケイパビリティプロトコル（`app_crash_signal() -> str |
      None`）。
- [ ] Unit 2 — iOS：`XCUIApplication.state` と `.processIdentifier` を読む新しい
      `openapi.yaml` のルートと、生成された `APIHandler` のメソッド。`Router.swift` ではなく
      `RunnerServer` から配信する。`XcuitestDriver.app_crash_signal()` が `AppCrashSignal`
      を実装し、`notRunning` をシグナルとして分類し、チャンネルエラーは
      `XcuitestRunnerCrashError` としてそのまま伝播させる。
- [ ] Unit 3 — iOS：各アプリ起動・再起動の直後に記録する `XcuitestEnvironment.app_launched_at`
      と `app_launched_pid`。`app_crash_artifacts()` の名前・PID・時刻による `.ips` 掃引。
      `ReportCrash` の非同期な書き込みに対する上限つきの待機を含み、失敗はすべて `[]` へ
      解決するよう包む。
- [ ] Unit 4 — Android：`backends.make_driver` から `AdbDriver.__init__` へ、`device_os` と
      同じ方法で通す `package` キーワード。`adb shell pidof <package>` による
      `AdbDriver.app_crash_signal()` を `adb shell dumpsys activity exit-info <package>`
      で裏付ける。
- [ ] Unit 5 — Android：各起動の箇所での `AndroidEnvironment.app_launched_at`（端末自身の
      時計）と、その直後の `logcat` クラッシュバッファのクリア。`app_crash_artifacts()` の
      常に試みる `logcat` 抽出（マネージドコードとネイティブの両形式）と、ベストエフォート
      で root 権限に依存する tombstone 取得。それぞれ独立して失敗を `[]` へ解決するよう包む。
- [ ] Unit 6 — `RunEnvironment.app_crash_artifacts()` のプロトコルの形（バイト列ではなく
      デコード済みのテキストを返す）と no-op のデフォルト値。`pool.py` の `lease()`
      クロージャを通した `Lease.app_crash_artifacts` の配線。
- [ ] Unit 7 — `run_scenario` / `_step_runner.py`：`relaunch` と同じ方法で `_run_on_lease`
      のリースから通す、新しい任意の `app_crash_artifacts` コールバック。事後確認の
      呼び出し箇所を、あらゆる種類のステップを覆う `outcome.ok` の収束点に置く。証跡の
      書き込みは `sink.write_text(f"app-crash/{name}", text)` で行う。
- [ ] Unit 8 — `TracingDriver`：`base.AppCrashSignal` を `_PROTOCOLS` へ加え、
      `--trace-driver` がそれを実装したドライバに対してだけ実属性として設置するようにする。
- [ ] Unit 9 — `crawl` 自身の統合。`cli.py` の既存の `on_event` コールバックが、新しく
      見つかったクラッシュに対して `environment.app_crash_artifacts()` を呼び、そのクラッシュ
      自身の `crashes/crash-NNN.yaml` 再現ファイルの隣にある `crashes/crash-NNN/app-crash/`
      へ書き込む。
- [ ] Unit 10 — showcase の準備。iOS（SwiftUI）と Android（Compose）それぞれのデバッグ専用
      「強制的にクラッシュさせる」操作、各プラットフォーム1本のそれを起動するシナリオ、
      `ios-e2e.yml` / `android-e2e.yml` へのゲートしない PR ごとのシグナルとしての配線。
- [ ] Unit 11 — ドキュメント。`docs/evidence.md`（および `docs/ja/`）にこの証跡の種類を追加する。
      `docs/ci.md`（および `docs/ja/`）に showcase のシグナルレーンを追記する。
      `docs/architecture.md`（および `docs/ja/`）に、既存のバックエンドクラッシュのリトライ
      節と、この項目のリトライなしの経路を相互参照させる。
- [ ] Unit 12 — テスト。両バックエンドで、ふつうの `ElementNotFound` や `wait`・`assert` の
      失敗に対して `app_crash_signal()` が `None` を返すこと（誤検知しないこと）。スタブした
      ディレクトリとスタブした `adb` の出力に対する、iOS の `.ips` 掃引と Android の
      `logcat`・tombstone 収集（Android の exit-info による裏付けを含む）。事後確認が経路の
      内側にとどまること、`app-crash/` がマスキング済みのテキストを保持すること、パイプライン
      レベルのリトライが起きないことを検証する `_step_runner.py` のテスト。web backend・
      fake backend で `isinstance` が `False` を返し、何も変わらないことを検証するテスト。

## 参考

- [BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact-ja.md) —
  本項目が補完するランナー自身のクラッシュレポート収集。本項目がテスト対象アプリ向けに適用する
  名前・PID・時刻による `.ips` 照合の手法の出典。こちらもまだ提案であり、どちらかが先に着地
  した時点で1つのメソッドへ収束するべき
- [BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md) —
  本項目の `crawl` 統合が土台とする、クロールの `Crash` レコードと `is_app_alive` の
  ヒューリスティック
- [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md) —
  本項目のリトライなしという設計が意図的に外れている、既存のバックエンドクラッシュのリトライの
  仕組み
- [BE-0066](../BE-0066-web-crawl/BE-0066-web-crawl-ja.md) — web（Playwright）バックエンド。
  この事象に対する独自のシグナル（レンダラーの `crash` イベントや、未捕捉の `pageerror`）は、
  後続の項目に委ねる
- [`bajutsu/common/drivers/base/backend_crash_error.py`](../../bajutsu/common/drivers/base/backend_crash_error.py) —
  `BackendCrashError`。本項目の `AppCrashedError` が基底クラスを共有しない、隣接する不具合
- [`bajutsu/common/drivers/base/interruption_policy_target.py`](../../bajutsu/common/drivers/base/interruption_policy_target.py) —
  本項目の `AppCrashSignal` が踏襲する、狭い opt-in のケイパビリティプロトコルという形
- [`bajutsu/common/drivers/tracing.py`](../../bajutsu/common/drivers/tracing.py) —
  `TracingDriver`。本項目の `AppCrashSignal` が加わる `_PROTOCOLS` タプルを持つ
- [`bajutsu/common/orchestrator/loop/_step_runner.py`](../../bajutsu/common/orchestrator/loop/_step_runner.py) —
  ステップごとのループ。その `outcome.ok` の収束点が、本項目の唯一の事後確認である
- [`bajutsu/common/orchestrator/loop/_functions.py`](../../bajutsu/common/orchestrator/loop/_functions.py) —
  `run_scenario`。新しい `app_crash_artifacts` コールバックをステップループまで通す
- [`bajutsu/common/platform_lifecycle/protocols/run_environment.py`](../../bajutsu/common/platform_lifecycle/protocols/run_environment.py) —
  `app_crash_artifacts()` が加わるプロトコル
- [`bajutsu/crawl/core/_coordinator.py`](../../bajutsu/crawl/core/_coordinator.py) —
  `record_crash`。そのロックを保持したままの `on_event` 呼び出しが、本項目のクロール側の
  クラッシュ番号を競合なく保つ
- [`bajutsu/crawl/cli.py`](../../bajutsu/crawl/cli.py) — 既存の `on_event` コールバック。
  本項目のクロール側の収集はここに加わり、環境と証跡の書き込み先はすでにここにある
- [`bajutsu/common/evidence/sink.py`](../../bajutsu/common/evidence/sink.py) —
  `write_text`（マスキングする）と `write_bytes`（シンクの検査できない内容向けで、
  マスキングしない）の違い。本項目の証跡は、先にテキストへデコードすることでこれに従う
- [`bajutsu/common/backends.py`](../../bajutsu/common/backends.py) — `make_driver`。既存の
  `device_os` キーワードが、本項目の `package` キーワードの先例になっている
- [`docs/ci.md`](../../docs/ci.md#the-ios-lane) — `fault-injection (xcuitest)`。本項目の
  showcase シナリオが従う、ゲートしない・失敗の形を検証するという配置

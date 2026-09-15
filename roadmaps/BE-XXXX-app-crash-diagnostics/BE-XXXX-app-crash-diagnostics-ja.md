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
届きません。iOS では、フォルトしたプロセスに対して macOS の `ReportCrash` が書き出す `.ips`
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
    ElementNotFound. The run pipeline treats it as a likely defect in the app itself, not backend
    infrastructure: it fails the scenario right away, with no retry, rather than respawning and
    re-running the way it does for a BackendCrashError (BE-0049 — retrying a crash-inducing defect
    risks turning a real failure into an absorbed flake).
    """
```

`AppCrashedError` は `BackendCrashError` と基底クラスを共有しません。両者は無関係な不具合を
名指しています。アプリ側とテストインフラ側です。パイプラインが両方を1つの種類として捕まえる
必要はありません。

### 検知の方式：事前のポーリングではなく、事後のリアクティブな確認

毎ステップ、アプリがまだ動いているかを確認する方式は、あらゆるシナリオに、グリーンな実行を含めて
クエリを1つ追加してしまいます。構造上まれな失敗モードを捉えるための代償としては重すぎます。
そこで `Driver`
（[`bajutsu/common/drivers/base/driver.py`](../../bajutsu/common/drivers/base/driver.py)）に、
新しいメソッドを1つだけ加えます。ステップ自身のアクションかクエリが*すでに*失敗しかけている、
その瞬間にだけ呼ばれます。

```python
def app_crash_signal(self) -> str | None:
    """A short description of the app's crash, if this driver can confirm one right now, else None.

    Called reactively — only when a step's action or query has already raised a failure the caller
    is about to surface as the step's terminal exception — never polled proactively. A backend that
    cannot distinguish "the app went down" from "the app is merely not showing what was expected"
    returns None unconditionally; the caller then raises the original failure unchanged.
    """
    ...
```

この事後確認の呼び出し箇所は1か所です。`bajutsu/common/orchestrator/loop/_functions.py` の
`_run_step_body` で、ドライバの `base.ElementNotFound` やそれに類するアクション・クエリの
失敗が、ステップ自身の終端例外になる場所です。`wait_for` のポーリングループの内側には置きません。
そちらはすでに例外を送出せず、素の `bool` を返しているからです。その例外が伝播する直前に、
ステップランナーが `driver.app_crash_signal()` を呼びます。`None` でない値が返れば、元の例外を
`base.AppCrashedError(signal) from original` に置き換えます。`from` で連鎖させるので、元の失敗は
トレースバックを読む人のために残ります。呼び出し箇所を1つに集約することで、検知はバックエンド
非依存のままです（第3の指導原則）。各バックエンド独自のシグナルは、そのバックエンド自身の
`app_crash_signal()` の内側だけに存在します。まだシグナルを持たないバックエンド、現時点では
web backend と fake backend は、常に `None` を返します。オーケストレータ側の呼び出し箇所は、
両者に対して何もしません。

### iOS：要素ツリーではなく `app.state` を使う

XCUITest は、対象アプリのプロセス状態を `XCUIApplication.state` としてすでに公開しています。
`notRunning` というケースが、「本当に落ちているか」という問いに直接答えます。`crawl` の
`is_app_alive` は、同じ事実を空、または予期しない要素ツリーから推測しています。この推測には
誤検知の実際のリスクがあります。システムアラートがアプリの UI を覆っている場合や、シナリオが
意図的に `background` ステップを実行した場合です。`app.state` はどちらも回避します。
バックグラウンドへ回っただけで生きているアプリは `runningBackgroundSuspended` または
`runningBackgroundActive` を返し、`notRunning` にはなりません。

`BajutsuKit/Sources/BajutsuRunner/Router.swift` に、新しいルートを1つ追加します。BE-0316 の
アラートボタン読み取りがすでに使っている `/systemAlert/query` と同じ形です。ランナー自身が保持する
`XCUIApplication` インスタンスに `.state` を問い合わせ、JSON として返します。`XcuitestDriver`
（[`bajutsu/common/drivers/xcuitest/xcuitest_driver.py`](../../bajutsu/common/drivers/xcuitest/xcuitest_driver.py)）
は、このルートを1回呼ぶことで `app_crash_signal()` を実装します。`notRunning` という答えが
シグナルの文字列になります。それ以外の状態、または既存の `XcuitestRunnerCrashError` 分類が
すでに受け持つチャンネルエラーは、`None` を返します。

### iOS：`.ips` レポートの照合、BE-0421 自身の手法を一般化する

[BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact-ja.md)
は、ランナー自身の `xcodebuild` プロセスについて、macOS が `~/Library/Logs/DiagnosticReports`
へ書き出したものの中から正しい `.ips` ファイルを見つける課題をすでに解決しています。その照合は
名前と時刻によるもので、レポート自身のヘッダが解析できれば `pid` で絞り込み、リトライループが
諦めたあとまで遅らせます。`ReportCrash` の非同期なシンボル化に時間を与えるためです。本項目は、
この手法をそのままテスト対象アプリ自身のバイナリ名に転用します。`xcodebuild-*` の代わりです。

`XcuitestEnvironment`
（[`bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py`](../../bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py)）
は、対象アプリをすでに起動し、再起動もしています。各起動の直後にタイムスタンプ
`app_launched_at` を記録するよう拡張します。BE-0421 が `_runner_proc` の隣に
`_runner_spawned_at` を記録しているのと同じ形です。`RunEnvironment` プロトコル
（[`bajutsu/common/platform_lifecycle/protocols/run_environment.py`](../../bajutsu/common/platform_lifecycle/protocols/run_environment.py)）
に、新しく `app_crash_artifacts(signal: str) -> list[tuple[str, bytes]]` を加えます。既定は `[]`
です。`crash_artifacts()`（BE-0421）や `request_device_replacement()`（BE-0354）がすでに確立して
いる、同じ no-op の形です。このメソッドは `~/Library/Logs/DiagnosticReports` を掃引し、対象の
実行ファイル名を持ち、更新時刻が `app_launched_at` 以降のレポートを探します。掃引は最後まで
ベストエフォートです。レポートが見つからない場合、ディレクトリが読めない場合、macOS 以外の
ホストである場合は、すべて空リストに解決します。例外は送出しません。

### Android：まず `logcat` のクラッシュバッファ、次に root 権限に依存する tombstone 取得

`AdbDriver`
（[`bajutsu/common/drivers/adb/adb_driver.py`](../../bajutsu/common/drivers/adb/adb_driver.py)）は、
`adb shell pidof <package>` によって `app_crash_signal()` を実装します。アプリがまだプロセスを
保持しているはずの場面で空の答えが返れば、root 権限もログ行の解析も要らずに、この事象を確認
できます。対象のパッケージ名は、ドライバのインストールと起動呼び出しがすでに使っている識別子と
同じものを、シナリオの設定からすでに知っています。

`AndroidEnvironment`
（[`bajutsu/common/platform_lifecycle/environments/android/android_environment.py`](../../bajutsu/common/platform_lifecycle/environments/android/android_environment.py)）
は、`app_crash_artifacts()` を2つの層で実装します。両方を取得するという、壁打ちで決めた方針に
合わせたものです。

1. **`logcat` のクラッシュバッファ**は、常に試みます。昇格した権限は要りません。`adb logcat -b
   crash -d` がリングバッファの保持内容をダンプします。対象パッケージを名指しする
   `FATAL EXCEPTION` のブロックを抽出し、`logcat-crash.txt` として書き出します。これは、この
   adb バックエンドが到達できるどの AVD や実機でも保証される唯一の証跡であり、マネージドコードの
   クラッシュ、つまりよくあるケースについてすでに完全な Java のスタックトレースを運びます。
2. **tombstone の取得**は、ベストエフォートで root 権限に依存します。`adb root`（このバックエンドが
   対象とするエミュレータイメージに対しては、すでに日常的な操作です）に続けて、もっとも新しい
   `/data/tombstones/tombstone_NN` のうち、更新時刻がアプリの最終起動時刻以降のものを1件取得
   します。照合は、iOS の `.ips` 掃引と同じ名前・時刻の方式です。実機、user ビルド、`adb root`
   を拒む状態のいずれでも、この層は黙ってスキップします。事象自体は報告済みであり、
   `logcat-crash.txt` はすでに届いています。root を拒む端末が失うのは、マネージドコードの
   クラッシュがそもそも必要としないネイティブフレームの詳細だけです。

### 失敗したシナリオの run ディレクトリへ収集をつなぐ

`Lease`（[`bajutsu/common/runner/types.py`](../../bajutsu/common/runner/types.py)）に
`app_crash_artifacts: Callable[[str], list[tuple[str, bytes]]]` を加えます。`crash_artifacts`
（BE-0421）と同じくモジュールレベルの no-op をデフォルトにし、`pool.py` の `lease()` クロージャで
同じように配線します。

`pipeline.py` の `_run_one_impl` に、新しい `except base.AppCrashedError as crash:` 節を加えます。
既存の `except BackendCrashError as crash:` 節（`pipeline.py:600`）より前に置きます。両者は互いに
素な不具合を名指すため、どちらか一方が他方を誤って捕まえることはありません。既存の節と異なり、
この節はクラッシュリトライループに一切入りません。その場でシナリオの終端 `RunResult(ok=False,
failure=...)` を組み立て、`lz.app_crash_artifacts(str(crash))` を呼び、返ってきた `(name,
content)` の組をそれぞれ `writer.write_text(f"{sid}/app-crash/{name}", ...)` で書き込みます。
BE-0421 と
[BE-0415](../BE-0415-driver-call-trace-per-scenario/BE-0415-driver-call-trace-per-scenario-ja.md)
がすでに使っている、シナリオ単位の書き込みと同じ形です。失敗文字列は、そのサブディレクトリを
直接名指しします。読む開発者は、その存在をあらかじめ知っている必要がなくなります。書き込みの
問題はログに記録するだけで、例外は送出しません。BE-0421 自身の姿勢と同じです。診断のための
収集が、すでに確定した失敗を別の失敗へすり替えてはなりません。クラッシュしたリース自体は、
変更せずにプールへ返します。ここで不具合を持つのは*デバイス*ではなく、その上で動いていた
アプリのプロセスだけです。バックエンドのクラッシュと違い、デバイスの交換を強制する理由は
ありません。

### `crawl` 自身のクラッシュ記録を拡張する

`bajutsu/crawl/core/_coordinator.py` の `record_crash` は、`is_app_alive` が崩壊を報告したときに
すでに `Crash` を追加しています。その呼び出し元
（[`bajutsu/crawl/core/_functions.py:662-667`](../../bajutsu/crawl/core/_functions.py)）は、
クロールループが操作している環境への生きた参照をすでに保持しています。ここへ、呼び出しをもう1つ
加えます。`coord.record_crash(path)` の隣に、本項目が `run` 向けに追加するのと同じ
`environment.app_crash_artifacts(signal)` への呼び出しです。同一の収集ロジックを再利用し、
2つ目の実装は作りません。クロールの CLI 自身の `RunArtifactWriter`
（[`bajutsu/crawl/cli.py`](../../bajutsu/crawl/cli.py)）が、結果を `crash-<n>/app-crash/` の下に
書き込みます。`<n>` は `ScreenMap.crashes` の中でのそのクラッシュ自身の番号であり、
[`bajutsu/crawl/repro.py`](../../bajutsu/crawl/repro.py) が同じパスに対してすでに生成している再現
シナリオの隣に置かれます。クロール自身の検知は、すでに使っている UI ツリーのヒューリスティック
のままです。`crawl` には、`run` と違って、事後確認をぶら下げるシナリオステップがありません。
2つの入口のあいだで共有されるのは収集だけであり、検知は共有しません。

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
失敗が新しい `AppCrashedError` の分類を運んでいること、`app-crash/` が期待どおりのファイルを
保持していることを検証します。`fault-injection (xcuitest)` がすでに、グリーンな実行ではなく
診断済みの失敗の形を検証しているのと同じ方式です
（[`docs/ci.md`](../../docs/ci.md#the-ios-lane)）。`ios-e2e.yml` / `android-e2e.yml` の中で、
`fault-injection (xcuitest)` や `network (adb)` の隣に、ゲートしない PR ごとのシグナルとして
配置します。オンデバイスで新しく配線されたカバレッジは、必須の `E2E` チェックへ昇格する前に、
まずそこで安定性を得ます。両レーンの他のあらゆる新しいシグナルが辿ってきたのと同じ道です。

### web backend と fake backend への影響

`PlaywrightDriver` と、テスト用の fake backend は、`app_crash_signal()` を常に `None`、
`app_crash_artifacts()` を常に `[]` として実装します。事後確認の呼び出し箇所も、パイプラインの
書き込み処理も、両者に対しては no-op のままです。今日と変わりません。本項目によって、web
backend や fake backend の実行が収集する内容は変わりません。

## 検討した代替案

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| 既存の `BackendCrashError` 回復ループでリトライする | クラッシュ検知をバックエンドクラッシュと同じ扱いにし、リースを破棄してリトライする | 壁打ちで却下しました。この事象は、一時的なインフラの不調ではなく、アプリ自身の不具合である可能性が高いためです。再起動してのリトライは、再び失敗する見込みが高いシナリオにクラッシュ回復の予算を費やし、本物の不具合を flakiness として吸収してしまう危険があります（BE-0049）。 |
| 毎ステップの前に `app.state`・プロセスの生存を事前にポーリングする | 各ステップの実行前に、アプリがまだ動作しているかを確認する | 壁打ちで却下しました。構造上まれな失敗モードを捉えるためだけに、グリーンな実行を含むあらゆるシナリオのあらゆるステップへドライバの往復を1回追加してしまうためです。事後確認の設計でも、その事象が起きたまさにそのステップで捕まえられます。そのステップ自身のアクションかクエリが、すでに失敗しているからです。 |
| Android で `logcat` のクラッシュバッファだけを使い、tombstone は取得しない | ネイティブクラッシュの取得を諦め、常に取得できる `logcat` だけに頼る | 却下しました。ネイティブ（NDK）クラッシュの完全なバックトレースを失い、`logcat` 自身が出力する簡略化された要約しか残らないためです。常に取得できる基盤としては残し、tombstone の取得はそれを置き換えるのではなく、端末が許す場合により豊かな詳細を上乗せします。 |
| Android で root 権限に依存する tombstone 取得だけを使い、`logcat` へのフォールバックを持たない | tombstone の取得だけに頼り、`logcat` の抽出は実装しない | 却下しました。実機、user ビルド、`adb root` を拒むエミュレータイメージでは、何も取得できなくなってしまうためです。`logcat` のクラッシュバッファは昇格した権限を必要とせず、よくあるマネージドコードのクラッシュについてすでに完全なスタックトレースを運びます。 |
| 新しいシナリオアサーション（例：`assert: appCrashed: false`）を追加する | シナリオ作者が明示的に「アプリがクラッシュしていないこと」を検証できるようにする | 却下しました。この事象は、ステップ自身のアクションかクエリの失敗によって、すでにシナリオを終わらせているためです。それを確認するアサーションが後から走れる時点は、シナリオの中に残っていません。showcase 自身のテスト用シナリオは、代わりに、失敗の*形*を run の外側から検証します。`fault-injection (xcuitest)` がすでに採っている方式と同じです。 |
| `video`・`deviceLog` と同様、`capturePolicy` の opt-in ルールの背後に収集を隠す | 明示的な指定がない限り収集を行わない | 却下しました。BE-0421 が自身の証跡について挙げた理由と同じです。この収集は、すでに失敗が確定したシナリオに対して一度だけ走ります。コストは、範囲の定まった掃引かログの読み取り1回であり、明示的な要求の背後へ隠すべき定常的なステップごとの負荷ではありません。 |

## 進捗

> 作業の進行に合わせて最新の状態を保つ。チェックリストは「どう実現するか」の MECE な作業分解を
> そのまま反映する（作業の単位ごとに1項目）。ログは変更内容とその日時を古い順に記録し、PR に
> リンクする。

- [ ] Unit 1 — `base.AppCrashedError`（新規ファイル）。`Driver.app_crash_signal()` をプロトコル
      の形に追加し、web backend と fake backend では常に `None` を返す。
- [ ] Unit 2 — iOS：`XCUIApplication.state` を読む新しい BajutsuRunner のルート。
      `XcuitestDriver.app_crash_signal()` がそれを事後確認として呼び、`notRunning` をシグナルと
      して分類する。
- [ ] Unit 3 — iOS：各アプリ起動・再起動の直後に記録する `XcuitestEnvironment.app_launched_at`。
      `app_crash_artifacts()` の名前・時刻による `.ips` 掃引。BE-0421 自身の手法を、対象アプリの
      バイナリ名へ一般化したもの。
- [ ] Unit 4 — Android：`adb shell pidof <package>` による `AdbDriver.app_crash_signal()`。
- [ ] Unit 5 — Android：`AndroidEnvironment.app_crash_artifacts()`。常に試みる `logcat`
      クラッシュバッファの抽出と、ベストエフォートで root 権限に依存する tombstone 取得。
- [ ] Unit 6 — `RunEnvironment.app_crash_artifacts()` のプロトコルの形と no-op のデフォルト値。
      `pool.py` の `lease()` クロージャを通した `Lease.app_crash_artifacts` の配線。
- [ ] Unit 7 — `bajutsu/common/orchestrator/loop/` の中の事後確認の呼び出し箇所。ステップ自身の
      アクション・クエリの失敗を捕まえ、`driver.app_crash_signal()` に問い合わせ、答えが返れば
      `AppCrashedError` として再送出する。
- [ ] Unit 8 — `pipeline.py` の新しい `except base.AppCrashedError` 節。リトライなし、クラッシュ
      したリースは変更せず解放、証跡は `f"{sid}/app-crash/"` の下へ書き込み、失敗文字列はその
      サブディレクトリを名指しする。
- [ ] Unit 9 — `crawl` 自身の統合。`record_crash` の隣に加える `environment.app_crash_artifacts()`
      の追加呼び出し。クロール自身の `RunArtifactWriter` による `crash-<n>/app-crash/` への
      書き込み。
- [ ] Unit 10 — showcase の準備。iOS（SwiftUI）と Android（Compose）それぞれのデバッグ専用
      「強制的にクラッシュさせる」操作、各プラットフォーム1本のそれを起動するシナリオ、
      `ios-e2e.yml` / `android-e2e.yml` へのゲートしない PR ごとのシグナルとしての配線。
- [ ] Unit 11 — ドキュメント。`docs/evidence.md`（および `docs/ja/`）にこの証跡の種類を追加する。
      `docs/ci.md`（および `docs/ja/`）に showcase のシグナルレーンを追記する。
      `docs/architecture.md`（および `docs/ja/`）に、既存のバックエンドクラッシュのリトライ
      節と、この項目のリトライなしの経路を相互参照させる。
- [ ] Unit 12 — テスト。両バックエンドで、ふつうの `ElementNotFound` に対して
      `app_crash_signal()` が `None` を返すこと（ふつうの未検出セレクタで誤検知しないこと）。
      スタブしたディレクトリとスタブした `adb` の出力に対する、iOS の `.ips` 掃引と Android の
      `logcat`・tombstone 収集。リトライなし、`app-crash/` ディレクトリ、失敗文字列の形を検証
      する `pipeline.py` のテスト。web backend・fake backend の no-op のデフォルト値が変わらないこと
      を検証するテスト。

## 参考

- [BE-0421](../BE-0421-xcuitest-crash-report-scenario-artifact/BE-0421-xcuitest-crash-report-scenario-artifact-ja.md) —
  本項目が補完するランナー自身のクラッシュレポート収集。本項目がテスト対象アプリ向けに転用する
  名前・時刻による `.ips` 照合の手法の出典
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
  `BackendCrashError`。本項目の `AppCrashedError` が意図的にそのサブクラスにしない、隣接する
  不具合
- [`bajutsu/common/drivers/base/driver.py`](../../bajutsu/common/drivers/base/driver.py) —
  `app_crash_signal()` が加わる `Driver` プロトコル
- [`bajutsu/common/orchestrator/loop/_functions.py`](../../bajutsu/common/orchestrator/loop/_functions.py) —
  `_run_step_body`。そのアクション・クエリの失敗の境界が、本項目の唯一の事後確認の呼び出し箇所
  である
- [`bajutsu/common/runner/pipeline.py`](../../bajutsu/common/runner/pipeline.py) —
  `_run_one_impl`。既存の `BackendCrashError` 節の隣に、新しい `except base.AppCrashedError`
  節が加わる
- [`bajutsu/common/platform_lifecycle/protocols/run_environment.py`](../../bajutsu/common/platform_lifecycle/protocols/run_environment.py) —
  `app_crash_artifacts()` が加わるプロトコル。`crash_artifacts()`（BE-0421）の隣
- [`bajutsu/crawl/core/_coordinator.py`](../../bajutsu/crawl/core/_coordinator.py) —
  `record_crash`。本項目のクロール側の収集呼び出しがその隣に加わる
- [`bajutsu/common/evidence/sink.py`](../../bajutsu/common/evidence/sink.py) —
  `RunArtifactWriter`。本項目の証跡が通過する、唯一の書き込み境界（BE-0331）
- [`docs/ci.md`](../../docs/ci.md#the-ios-lane) — `fault-injection (xcuitest)`。本項目の
  showcase シナリオが従う、ゲートしない・失敗の形を検証するという配置

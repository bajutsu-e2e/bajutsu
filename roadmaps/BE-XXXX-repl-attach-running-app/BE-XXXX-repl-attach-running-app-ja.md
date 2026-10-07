[English](BE-XXXX-repl-attach-running-app.md) · **日本語**

# BE-XXXX — 起動済みの Simulator アプリへ REPL を接続する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-repl-attach-running-app-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | オーサリング体験 |
<!-- /BE-METADATA -->

## はじめに

`bajutsu repl --target <name> --attach` は、起動済みの iOS Simulator で動作中のアプリに手動シェルを接続し、そのアプリを一切変更しません。端末の erase、ビルドの install、アプリの再起動をいずれも行いません。操作者は、すでに見ていた画面を `tree` で読み、`tap <id>` で操作できます。

変更が要るのは XCUITest ランナーです。現在のランナーは起動時に `app.launch()` を呼ぶため、起動のたびに動作中のアプリが新しいプロセスに置き換わります。本項目では、動作中のプロセスに対する `XCUIApplication` を作り、起動を省く attach モードをランナーへ追加します。Python 側には、`--attach` フラグ、アプリが動作中かの判定、端末全体に及ぶ手順をすべて省く起動経路を加えます。対象は iOS Simulator（`xcuitest`）backend のみです。

## 動機

[BE-0423](../../roadmaps/BE-0423-cli-repl-inspect-actuate/BE-0423-cli-repl-inspect-actuate-ja.md) により、操作者はシナリオを書かずに要素ツリーを読み、id を tap できるようになりました。ただし、このシェルは最初にアプリを起動します。この起動が問題です。`--udid booted` を指定しても、既定の `erase` が Simulator の shutdown、erase、boot、ビルドの install、アプリの起動までを行います。`--no-erase` を指定すれば消去は省けますが、ランナーが `app.launch()`（[`RunnerUITest.swift:214`](../../BajutsuKit/Runner/Sources/RunnerUITest.swift)）を呼ぶため、動作中のプロセスは新しいプロセスに置き換わります。

この置き換えは、操作者が調べに来た対象を捨てます。手で画面に到達した開発者や、Xcode からデバッガを接続して実行している開発者は、シェルを起動した瞬間に、画面の状態、ログイン済みのセッション、デバッガの接続を失います。深い画面で失敗するセレクタを調べるには、接続のたびにその画面へ戻る操作が必要です。この負担は再接続のたびに繰り返され、しかもシェルの本来の用途である「画面に到達しにくく、入力した id が解決しない」場面に集中します。

本項目が出荷されると、Simulator でアプリを開いたままの操作者は `bajutsu repl --target <name> --attach` を実行でき、最初の `tree` は前面にあった画面をそのまま出力します。観測できる違いは、接続の前後で Simulator 上のアプリのプロセス ID が変わらず、Xcode から接続したデバッガが接続されたままになることです。出荷後のコマンドに対して、`xcrun simctl spawn <udid> launchctl list` で確かめられます。

## 詳細設計

### CLI の表面

`repl` に `--attach` を 1 つ加えます。既定の動作は BE-0423 の出荷時のままなので、フラグを付けないコマンドは従来と同じに動きます。フラグは `--target` と `--udid` に組み合わせられ、`--udid` の既定は `booted` のままです。`--target` は引き続き必須です。bundle id と id の名前空間は、画面ではなく target の設定から得るためです。

| 組み合わせ | 結果 |
|---|---|
| `xcuitest` backend のローカル Simulator で `--attach` | 後述の attach 経路 |
| `playwright`、`adb`、`--udid https://…` のライブ経路、または `xcuitest.deviceType: device` の target で `--attach` | 終了コード 2 で `repl: --attach is only supported on the local iOS Simulator (xcuitest)` |
| `--attach --erase` | 2 つのフラグが矛盾することを示して終了コード 2 |
| `--erase` なしの `--attach` | `erase` を無効にする（ローカルの既定は有効） |

検査は `bajutsu/repl/cli.py` で、actuator を選んだ直後かつ `resolve_device` の前に置きます。既存のライブ経路の `--erase` 検査は `resolve_device` の後にあるため、その隣に置くと、非対応の `adb` が先に端末を検索し、adb のエラーで終了しかねません。検査には、生の `--udid` の値（URL かどうか）と target の設定（`deviceType`）だけを使います。これで、非対応の組み合わせはすべて、どの端末にも触れる前に終了コード 2 で失敗します。

### Python 側：判定してから接続または起動する

`XcuitestEnvironment`（`bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py`）に attach 用の起動経路を加えます。端末の解決後、spawn の前に、次の順で実行します。

1. 端末が boot 済みかを確認します。boot 済みの端末が 1 台もなければ、起動経路が `simctl.DeviceError`（「no booted Simulator; boot one first」）を送出します。`repl` の既存の `except DeviceError` がこれを表示し、終了コード 2 に変えます。udid を指定した端末が shutdown 状態なら、その udid を示す同じエラーを送出します。boot は端末全体に及ぶ手順なので、attach は端末を boot しません。コマンド名の接頭辞と終了コードは環境に持たせません。`run`、`record`、`crawl` が同じ環境を共有するためです。
2. target の bundle id にプロセスが動作中かを判定します。新設する `simctl.Env.is_app_running(bundle_id)` が `xcrun simctl spawn <udid> launchctl list` を読み、`UIKitApplication:<bundle id>` の項目を探します。このコマンドは他の `simctl` 呼び出しと同じ注入可能な `RunFn` を通るので、テストは出力を差し替えられます。
3. アプリが動作中なら、`BAJUTSU_ATTACH=1` を付けてランナーを spawn し、`_prepare_simulator` を丸ごと省きます。erase、boot、ロケールの固定、install、権限の付与、ディープリンクはいずれも行いません。
4. アプリが動作中でなければ、この変数なしでランナーを spawn し、環境は結果（`attached` か `launched`）を呼び出し元へ返します。`repl` が、`launched` のときに `<bundle id> was not running; launching it` と表示します。ランナーは従来どおりアプリを起動します。この起動も、erase、install、その他の端末全体の手順を省きます。spawn の前に、install 済みかを新設の `simctl.Env.app_container_exists(bundle_id)` で確かめ、未 install なら bundle id を示す `DeviceError` で失敗して終了コード 2 で終わります。ランナー自身の `app.launch()` は未 install の bundle id をきれいに失敗させないため、この確認を `app.launch()` に任せることはできません。

既存の `simctl.Env.is_installed` は流用しません。`DeviceTimeout` のときも `False` を返すため、応答が止まった Simulator を「未 install」と誤って報告してしまうからです。`app_container_exists` は、アプリのコンテナがないこと（`get_app_container` が `CalledProcessError` で終わること）だけを `False` とし、`DeviceTimeout` と端末のエラーはそのまま送出します。

どちらの経路も、ランナーを `attempts=1` かつ回復処理なし（`_no_recovery`）で spawn します。`_no_recovery` が止めるのは回復処理だけで、`_spawn_cold_with_retry` は既定で 2 回試行するため、`attempts=1` も必要です。コールドスポーンの再試行は、失敗した試行ごとに `_discard_runner` で対象アプリを terminate します。回復の段階は、端末の再起動と準備のやり直しを行います。どちらも、操作者が残したいアプリを壊します。attach の spawn が失敗したときは、ランナーのログ末尾を添えて一度だけはっきり失敗し、対象アプリを terminate しません。そのため、失敗した試行の破棄は、対象アプリの terminate を省く `_discard_runner` の新しい引数 `keep_app` を使います。

target 設定の起動環境変数と起動引数は、fallback の経路でだけ渡します。attach したアプリはすでに起動時の環境で動いており、シェルから後で変えることはできません。

attach の経路では readiness の待機も省きます。`readyWhen` は起動直後の画面を指すのに対し、アプリは操作者が離れた位置にあるため、その画面を待つとタイムアウトします。ただし `launch_driver`（`bajutsu/common/runner/launch.py`）は、`env.start` の直後に `await_ready(..., ready_sel=eff.ready_when)` を必ず呼びます。そこで `launch_driver` にキーワード引数 `skip_readiness: bool = False` を加え、`repl` の attach の経路だけが `True` を渡します。ほかの呼び出し元は既定のままなので、挙動は変わりません。`skip_readiness` が `True` のとき、`launch_driver` は readiness の結果として「待機を省いた」ことを示す既存の型の値を返します。

### ランナー側：attach モード

`RunnerUITest.testServeUntilTornDown`（`BajutsuKit/Runner/Sources/RunnerUITest.swift`）は、`BAJUTSU_ATTACH` に対応する Swift 側の新しい `RunnerServer.forwardedAttach`（`BajutsuKit/Sources/BajutsuRunner/RunnerServer.swift`）を読みます。この値が設定されているとき、ランナーは渡された bundle id の `XCUIApplication(bundleIdentifier:)` を作り、`app.launch()` と起動ウォッチドッグを省いて、動作中のプロセスに対してサーバを開始します。何も起動しないので、起動環境変数と起動引数は適用しません。

残りを作る前に、1 つの前提をスパイクで確かめる必要があります。ランナーが `launch()` を呼んでいない `XCUIApplication` でも、すでに動作中のプロセスの要素ツリーを読み、要素を tap できるかという前提です。先に前面化が要るとわかった場合、attach モードは `launch()` の代わりに `app.activate()` を呼びます。activate はプロセスを置き換えずにアプリを前面へ出すため、先に述べた観測可能な結果は保たれます。ただし、バックグラウンドにあったアプリは前面に出ます。

ランナー自体は、boot 済みの端末へ install して起動する必要があります。`xcodebuild test-without-building` は、端末を shutdown せず、他のアプリにも触れずにその両方を行います。したがって、ランナーの起動が操作者の動作中のアプリに影響することはありません。

### 終了とウォームリユース

シェルを抜けてもアプリが生き続ける作りは、すでにあります。`bajutsu/repl/cli.py` の `_close_owned_session` は、ローカルの `xcuitest` の teardown を意図的に実行しません。その teardown は対象アプリを terminate するためです。

ただし、現在の終了処理はランナーを破棄しません。`_spawn_runner` は `xcodebuild` を別のセッション（`start_new_session`）で起動するため、シェルの終了だけではランナーが残り、端末の自動化セッションを握り続けて孤児になりかねません。そこで attach のセッションには、ランナーだけを止める終了処理を新設します。`XcuitestEnvironment.release_runner()` が、`_discard_runner(keep_app=True)` を呼び、`xcodebuild` のプロセスグループとランナーアプリを止めます。`_terminate_app_under_test` は呼びません。`_close_owned_session` は、attach のセッションでだけこの `release_runner()` を呼びます。attach でない既存の経路は変わりません。

attach の起動は、アプリを terminate して再起動するウォームリユース経路（`_resume_warm`）に入りません。attach 経路は新しい driver を返し、リースがアプリを所有していないことを記録します。そのため、`_discard_runner` を `keep_app=True` で呼ぶ上の経路以外から、アプリが terminate されることはありません。

### スコープ外

- 実機（`xcuitest.deviceType: device`）と `--udid https://…` のライブ経路。実機はもともと attach に近い形であり、ライブ経路は常駐ランナーではなく WebDriver セッションを操作します。
- `adb` と `playwright`。どちらも、XCUITest ランナーのように driver を起動した副作用でアプリを再起動することがなく、本項目が解く問題がそこには存在しません。
- `--target` なしで前面のアプリを検出すること。id の名前空間と `readyWhen` は target の設定が持ち、`tree` の出力はそれらに照らして初めて役に立ちます。
- `run`、`record`、`crawl` への attach。これらの決定性の約束は、クリーンで既知の開始状態であり、attach はそれを意図的に手放します。

### 検証

高速テストは、Python 側を fake で覆います。対象は、`resolve_device` の前に行うフラグと `deviceType` の検証、boot 済みの確認、記録した `launchctl list` の出力に対する判定の解析、attach と fallback の分岐、未 install の確認（`DeviceTimeout` を「未 install」にしないことを含む）、`skip_readiness` の分岐、終了時にランナーだけを止めること、そして spawn が失敗した後も含め、どちらの経路も erase、install、terminate、回復の段階を呼ばないことです。Swift のランナーの変更は `xcodebuild` を使う E2E ジョブでしかコンパイルされません。そのためこのジョブに、アプリを起動し、attach して tree を読み、前後のプロセス ID を比べるケースを加えます。手動の確認は専用の Simulator で行い、他の作業に使用中の Simulator は使いません。

## 検討した代替案

| 案 | 採らなかった理由 |
|---|---|
| `erase` だけを省く（`--no-erase` を既定にする） | ランナーが `app.launch()` を呼ぶのでアプリは再起動し、画面状態は失われます。動機となる問題が解けません。 |
| 無害な種アプリでランナーを起動し、`/app/target` で切り替える | 既存の経路で Swift の変更は要りませんが、種アプリの起動が前面を奪います。接続の瞬間に操作者が見ていた画面が変わり、目的に反します。 |
| XCUITest ランナーを使わず `simctl` だけで tree を読む | シェルが backend 非依存の `Driver` から外れ、`tap` の挙動が `run` と食い違います。セレクタが解決するかの確認がシェルの価値であり、この案はそれを手放します。 |
| `--target` なしで前面のアプリを検出して接続する | id の名前空間と `readyWhen` は設定が持ち、他のコマンドも target を前提にしています。本フラグの上に後から足せます。 |
| アプリが動作中でなければ失敗にする | 操作者が頼んでいない起動を決してしない点で、より厳格な選択です。fallback を選んだのは、アプリを閉じていてもシェルが使えるほうが有用であり、起動は表示され、何も消去しないためです。 |
| `--udid booted` を attach の意味にする | BE-0423 の既定の意味が変わり、出荷済みの動作を壊します。 |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] スパイク：ランナーが `launch()` を呼んでいない `XCUIApplication` が動作中のアプリを読み、tap できるか（または `activate()` が要るか）を、専用の Simulator で確かめる。
- [ ] ランナーの attach モード（`BAJUTSU_ATTACH`、`RunnerServer.forwardedAttach`、省略した起動）。
- [ ] `simctl.Env.is_app_running` と `simctl.Env.app_container_exists`（`DeviceTimeout` を送出する厳密な判定）の新設、およびそのテスト。
- [ ] `XcuitestEnvironment` の attach 起動経路：boot 済みの確認、判定、install 済みの確認、attach または fallback の起動、`attempts=1` かつ回復処理なしの spawn、`_discard_runner(keep_app=True)`。
- [ ] `launch_driver` の `skip_readiness` と、`XcuitestEnvironment.release_runner()` による終了時のランナー破棄。
- [ ] `bajutsu repl` の `--attach` フラグ、`resolve_device` 前の組み合わせの検査（実機を含む）、fallback の通知。
- [ ] attach の前後でプロセス ID を比べる E2E ケース。
- [ ] `docs/cli.md` と `docs/ja/cli.md` のフラグ説明、および `docs/architecture.md` の `repl` の記述。

## 参考

- [BE-0423](../../roadmaps/BE-0423-cli-repl-inspect-actuate/BE-0423-cli-repl-inspect-actuate-ja.md)：本項目が拡張する対話シェル。
- [BE-0429](../../roadmaps/BE-0429-repl-tui-and-target-selectors/BE-0429-repl-tui-and-target-selectors-ja.md)：attach したセッションが共有する端末 UI。
- [BE-0447](../../roadmaps/BE-0447-install-app-step/BE-0447-install-app-step-ja.md)：install-app ステップとそのデバイスグループ。`/app/target` を加えた、現存するもっとも近いランナーの宛先切り替えの仕組み。
- `bajutsu/repl/cli.py`、`BajutsuKit/Runner/Sources/RunnerUITest.swift`、`bajutsu/common/platform_lifecycle/environments/xcuitest/xcuitest_environment.py`

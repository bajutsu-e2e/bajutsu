# 継続的インテグレーション（CI）

2 つの別々のトピックを扱います。

1. **このリポの CI**。ツール自体をガードします（`.github/workflows/`）。
2. **あなたのアプリの CI で bajutsu を回す**。再利用できる composite action とレシピです。

## このリポの CI

| Workflow | ランナー | タイミング | 内容 |
|---|---|---|---|
| [`ci.yml`](../../.github/workflows/ci.yml)（`check` ジョブ） | Linux | `main` への push、全 PR（プルリクエスト） | Python 3.13 上で `make check` ゲート一式を実行します。ロックの鮮度（`uv lock --check`）、整形（`ruff format --check`）、lint（`ruff`）、シェル lint（`shellcheck`）、ワークフロー lint（`actionlint`）、型（`mypy bajutsu demos scripts`）、カバレッジ下限つきの `pytest`（`--cov-fail-under=89`）です。ロジック層はシミュレータ不要なので速く安価です |
| [`mcp-wire.yml`](../../.github/workflows/mcp-wire.yml) | Linux | 手動 + `bajutsu/mcp/**`・`bajutsu mcp` CLI・wire テストに触れる PR | **MCP ワイヤ round-trip** のレーン（BE-0301）で、ジョブは **wire (stdio)** の 1 つです。`bajutsu mcp --transport stdio` を実際のサブプロセスとして起動し、`mcp` SDK の `fastmcp.Client` で stdio 越しに駆動します（`pytest tests/test_mcp_wire.py -m mcp_wire -n0`）。in-process の `tests/test_mcp.py` が触れない JSON-RPC のフレーミング、ツールスキーマの広告、リソース URI のエンコーディングを検証します。ツール一覧を取得し、`bajutsu_doctor` / `bajutsu_run` の呼び出しを round-trip し、run 証跡のリソースを読み取ります。いずれも `fake` バックエンドに対して行います（`bajutsu_doctor` はサーバ内でデバイス不要の実ロジックを走らせ、`bajutsu_run` は実際の `bajutsu run` を起動しますが、その verdict は環境依存なので、round-trip では verdict 行が transport を往復することだけを確認します）。`mcp_wire` マーカーがこのスイートを高速ゲートから外します（pyproject の `addopts` の `not mcp_wire`）。実サブプロセス + stdio IPC は in-process 呼び出しより時間に敏感なので、まず**ゲート対象外**の PR ごとのシグナルとして着地させ、安定を確認してから必須化します。BE-0282 の `network (playwright)` が示した前例です |
| [`web-e2e.yml`](../../.github/workflows/web-e2e.yml) | Linux | 手動 + 全 PR + マージキュー（必須の `E2E (web)` チェック） | **web（Playwright）バックエンド**のレーン（BE-0279）で、ヘッドレス Chromium に対する 6 ジョブから成ります。**smoke (playwright)** は `demos/web` のシナリオを実行し（`make -C demos/web e2e`）、**dogfood (serve UI)** は Bajutsu 自身の serve SPA を駆動し（BE-0058）、**conformance (playwright)** は BE-0114 のドライバコントラクトを実ブラウザに対して実行します。**network (playwright)** は実ネットワーク経路（`page.route` の介入、`requestfinished` のキャプチャ、`mocked` フラグ、実際にキャプチャした証拠の redaction）を動かします（`make -C demos/web e2e-network`、BE-0282）。**codegen (playwright)** はシナリオからネイティブな Playwright テストを生成し、実際の `@playwright/test` ランナーで実行します（`make -C demos/web codegen-e2e`、BE-0293）。`ios-e2e.yml` の `codegen (xcuitest)` の web 版です。**onboarding (doctor / provision)** は、ブラウザが本当に入っていないホストに対して `bajutsu.provision` を実行し、provision 後に `doctor` の環境ゲートを再確認します（BE-0304）。**Mac / Simulator 不要**で、コアがプラットフォーム非依存であることを示します。6 つとも同じ `changes` 検出ジョブ（`scripts/e2e_changes.py`、`E2E_LANE=web`）でパスゲートします。**onboarding** を除く、決定論的で実行環境に依存しない 5 つのジョブが、常に結果を報告する `E2E (web)` ジョブ（必須チェック）に集約され、**onboarding** は PR ごとの参考シグナルにとどまります（BE-0304）。web 経路に影響しえない PR はブラウザジョブを飛ばし、ゲートは合格します。**network (playwright)** と **codegen (playwright)** はいずれもまず PR ごとのシグナルとして着地させましたが、CI で安定を確認できたので、現在は `E2E (web)` の `needs:` に昇格しています。**network** は android-e2e.yml ですでにゲート入りしている `network (adb)` の web 版、**codegen** は `ios-e2e.yml` ですでにゲート入りしている `codegen (xcuitest)` の web 版にあたります |
| [`dependency-audit.yml`](../../.github/workflows/dependency-audit.yml) | Linux | 手動 + 週次 + `pyproject.toml` / `uv.lock` を触る `main` への push / PR | ロックした依存グラフ（`uv export` → `pip-audit --no-deps`）を脆弱性 DB に照合します。結果はロックファイルと DB だけで決まるので、依存の変更時と、変わらない pin に対して新たに公表された脆弱性を拾う週次スケジュールで実行します |
| [`swift.yml`](../../.github/workflows/swift.yml) | macOS | `main` への push + `BajutsuKit/**` を触る PR | [BajutsuKit](../../BajutsuKit) の `swift build` + `swift test` を実行します。純 Foundation のロジック（リクエスト照合 / モック解析）をシミュレータ無しで単体テストします。実機でのインターセプトそのものは `ios-e2e.yml` がカバーします |
| [`ios-e2e.yml`](../../.github/workflows/ios-e2e.yml) | macOS | 手動 + 全 PR + マージキュー（必須の `E2E (iOS)` チェック） | **iOS（XCUITest）バックエンド**のレーンで、showcase に対する 9 個の macOS ジョブから成り、いずれも XCUITest バックエンド（常駐の BajutsuRunner）で走ります。**build (app + runner)** ジョブが、showcase の 2 つのアプリ（SwiftUI + UIKit）と常駐ランナーを一度だけビルドし、1 つの `ios-build` アーティファクトとしてアップロードします。consumer のジョブは、ビルドし直すのではなくこの成果物をダウンロードしてインストールするので、コールドの Swift ビルドはジョブごとにではなく一度だけ支払われます。**run (xcuitest)** は同型のゲート担当レーンで、かつては別々の `smoke` / `gestures` / `permission` / `runner-actuation` ジョブだったシナリオを、1 回の `bajutsu run` にまとめたものです。常駐ランナーをシナリオ間で warm 再利用します（BE-0291）。ただしこのレーンでは再利用を 1 回に制限しているため（`BAJUTSU_XCUITEST_MAX_WARM_REUSES: 1`。混雑した CI ホストでの run 中のランナークラッシュを抑えるためです）、実際に走る 10 個のシナリオドキュメント（6 個のファイルが保持する全シナリオです。BE-0315 が通知許可をネイティブにしたため、除外する `ai` タグ付きのシナリオはありません）は、コールドの `xcodebuild test-without-building` の起動を一度ではなく 5 回支払います。これは置き換えた 4 個の個別ジョブの合計より 1 回多いのですが、その 4 個のジョブはそれぞれ自前の Swift ビルドと起動も支払っていたので、レーン全体で一度だけ支払う共有の `build` ジョブの分だけ、正味のコストは今も下がっています。内訳は、Stable カタログ（`smoke.yaml`）、ピンチ / 回転の 2 本指ジェスチャ（`gestures_multitouch.yaml`、BE-0019）、常駐ランナーチャネルの `/type`（`search.yaml`）と `/swipe` + `/back`（`notices.yaml`、BE-0281）、決定論的な `permissions` フィールドと、BE-0315 以降はネイティブなリアクティブ `systemAlertHandling` の通知許可（`permission.yaml`、BE-0276 の location と BE-0315 の通知。どちらもゲート上で走ります）、プロアクティブな `handleSystemAlert` ステップ（`permission_system_alert.yaml`、BE-0316。SpringBoard の通知許可プロンプトの「許可」ボタンを、視覚モデルなしのアクセシビリティクエリでタップします）です。`run` は各シナリオを個別の verdict まで実行し、どれかが失敗すればゲートを赤くするので、シナリオごとの合否はジョブ名ではなく run のレポートに移ります。**actuation (xcuitest)** は、このレーンでゲート対象外の受け皿となるジョブです。まだ `run` に昇格していない実機の XCUITest アクチュエーション（BE-0281）として、Stable の起動タブ上で `back`（`navigation.yaml`）とデバイス制御（`setLocation` / クリップボード / `push`、`device.yaml` + `push.yaml`）を実行し、あわせてテキスト編集の 4 つのアクチュエータ（`text_editing.yaml`）も実行します。さらに、アクチュエートできる 3 つ目のバックエンドとして、シナリオ作成機能（BE-0285）も駆動します。Log タブのライブなカウンタ値を取り込む `extract`（`extract.yaml`）、ループ本体で詳細画面を push / pop して反復のあいだにツリーを変化させながら Stable の 5 行を反復する `forEach`（`foreach.yaml`）、data-driven の行（`data_driven.yaml`）、`relaunch`（`relaunch.yaml`）の 4 つです。この 4 つは showcase の共有シナリオファイルをそのまま使い（BE-0221）、デバイス制御のシナリオより前に並べています。配信された通知や OS レベルの許可が、この 4 つの前に来ないようにするためです。**golden (xcuitest)** は BE-0006 の要素ツリー golden を XCUITest で実行します（`golden.yaml`）。`android-e2e.yml` の `golden (adb)` に対応する iOS 版です。**codegen (xcuitest)** はシナリオからネイティブ XCUITest を生成し（`make -C demos/showcase ui-test`）、`xcodebuild` で実行します（テスト時に bajutsu / AI は不要です）。UITests スキームをコンパイルするジョブで、常駐ランナーとは別の成果物なので、自前でビルドし、`build` アーティファクトの consumer にはなりません。**bundled-runner (xcuitest)** は、config の `testRunner` ではなく wheel バンドルから解決したランナーで、SwiftUI と UIKit 両方の a11y アプリに対して smoke シナリオを実行し（BE-0292）、ランナーがアプリに依存しないことを確認します。**conformance (xcuitest)** はドライバ conformance スイート（BE-0114）を XCUITest バックエンドに対して実機で実行します。スイート実行中の Simulator インフラ障害（常駐ランナーのクラッシュ、`base.BackendCrashError`）は、`bajutsu run` と同じように復旧します。新しいデバイスを取り直して該当テストを再実行し、上限はテストごとに再試行回数と壁時計の再起動予算（`BAJUTSU_CRASH_RETRIES` / `BAJUTSU_CRASH_RECOVERY_BUDGET`）で区切ります。これによりこの必須チェックは、ホストのフレーキーではなく本物の契約違反で赤くなり、毎回クラッシュする場合は今までどおり明確に失敗します。各再起動は、アップロードする `conformance-recovery-report` アーティファクトに数え上げられます（BE-0334）。**fault-injection (xcuitest)** は、健全なランナーを駆動する代わりに、ランナー自身のホストプロセスにシグナルを送ります（BE-0305）。短い `SIGSTOP` は、固まったランナーと同じようにチャネルをハングさせるので、一過性リトライ（BE-0207）が吸収しなければなりません。リトライ予算を超える凍結は、crash-recovery（BE-0287）が乗り越えなければなりません。`SIGKILL` では、無関係なタイムアウトではなく実行中のランナー障害を名指しするクラッシュ診断で読み取りが終わらなければなりません。各ケースは、検証対象の層に到達したというチャネル自身のログ記録を合図に障害を解除し、推測した待ち時間には頼りません。**visual (xcuitest)** は Stable カタログをコミット済みの `baselines_ios/` ベースラインとピクセル比較します（`make -C demos/showcase e2e-visual`）。ステータスバーと「Liquid Glass」タブバーはマスクします。`e2e-visual` は自前でアプリをビルドし直すため、`codegen` と同様に `build` アーティファクトの consumer にはなりません。共有の `setup-ios-toolchain` コンポジットアクションが、macOS の各ジョブが繰り返す Xcode / uv / xcodegen / シミュレータ起動の手順をまとめています（`build` ジョブはシミュレータ不要なので `boot: false` を渡します）。9 個のジョブはすべて同じ `changes` 検出ジョブでパスゲートされ、そのうち `build`、`run`、`codegen`、`bundled-runner`、`conformance` は、常に結果を報告する単一の `E2E (iOS)` ジョブ（必須チェック）に集約されます。`actuation`、`golden`、`visual`、`fault-injection` も同じ検出ジョブでパスゲートされますが、`E2E (iOS)` の `needs:` には意図的に含めていません。新しく配線した XCUITest のアクチュエーションは、まずは参考シグナルとして着地させ（Simulator レーンにはフレーキーの実績があるため、BE-0218）、安定を確認してからゲートに昇格させます。要素ツリーの `golden` は決定論的で実行環境に依存しないため、そのドリフトを参考シグナルとして出し、`visual` のピクセルのベースラインはホスト依存（Simulator のレンダラが Xcode / デバイス / OS で変わります）で、`fault-injection` は意図的にランナーを壊すため、BE-0282 と同じ道筋でまずシグナルとして着地させるからです。いずれもドリフトやフレーキーを PR のブロックではなく単独ジョブのシグナルとして出します（`visual` が採取したスクリーンショットは `ios-e2e-visual-run` としてアップロードし、ベースラインの再採取に使います） |
| [`android-e2e.yml`](../../.github/workflows/android-e2e.yml) | Linux | 手動 + 全 PR + マージキュー（必須の `E2E (android)` チェック） | **Android（adb）バックエンド**のレーン（BE-0208）で、iOS や web のレーンのジョブ分割にならった観点ごとの 7 ジョブから成り、各ジョブが自前の x86_64 API 34 AVD を KVM のもとで起動します（`reactivecircus/android-emulator-runner`）。**smoke (adb)** は Compose と Views の showcase APK をビルドし、Stable タブのシナリオ（コアの id/tap/type/value のフローに、詳細画面への push と pop で戻る back ナビゲーションを加えたもの）を `--backend android` で実行します（`make -C demos/showcase/android e2e`）。**golden (adb)** は Compose の Stable カタログの golden 要素ツリーを実機でチェックし（`make -C demos/showcase/android e2e-golden`、BE-0006 / BE-0208 ユニット 4）、続いて resident チャネルを切って再実行して（`make -C demos/showcase/android e2e-fallback`、BE-0245）両方の読み取り経路が一致することを確かめます。**network (adb)** は BE-0283 のネットワークキャプチャの検証で、`request` ステップが、BajutsuAndroid のインターセプタが `adb reverse` の collector ブリッジ越しに報告する実際のエミュレータ通信を観測します。**conformance (adb)** はドライバ conformance スイート（BE-0114）を実 adb バックエンドに対して実行します。`ios-e2e.yml` の `conformance (xcuitest)` の Android 版です。**fault-injection (adb)** はディスプレイをスリープさせて、実際の読み取り元が本当に空の要素ツリーを返す状態を作り、`CoordinateTreeDriver` の transient-empty リトライが、高速スイートのテストでは組み立てるしかない実際の条件を乗り越えることと、リトライ予算を超えて空が続いた場合に明確に失敗することを検証します（`make -C demos/showcase/android e2e-fault-injection`、BE-0305）。**visual (adb)** はピクセル VRT を実行します（後述）。**Mac / Simulator 不要**で、iOS と web の e2e レーンに並ぶ 3 つ目のバックエンドの Linux 版です。`changes` 検出ジョブ（`scripts/e2e_changes.py`、`E2E_LANE=android`）でパスゲートし、必須の集約ジョブ `E2E (android)` に集約します（BE-0279）。AVD は（ローカル検証の arm64 ではなく）x86_64 にして、x86_64 ランナー上で KVM が加速できるようにしています。golden のベースラインは arm64 で採取していますが、比較がフィールド単位で frame は健全性チェックだけのため、x86_64 でも通ります。sheet/cover のフロー（`components`、`modals`）も、このレーンに限って条件待ちの上限を引き上げることで含めています。`make -C demos/showcase/android e2e` が `BAJUTSU_MIN_WAIT_TIMEOUT`（既定 15 秒）を各待ちのタイムアウトの下限として渡すためです。ソフトウェアレンダリングのエミュレータは、共有シナリオの 5 秒の待ちに収まらないほど遅くモーダルを描画します。条件待ちは条件が満たされた瞬間に返るので、上限を広げても固定の待ち時間にはならず安全な上限にとどまり、共有シナリオには手を入れません（`timeout: 5` はどのバックエンドでも同じです）。深いスクロールのフロー（`controls`、`notices`）もこのレーンに加えました。`controls` はボタン群の下にある segmented control の値ノードを、`notices` は折り返しよりだいぶ下にある一覧行を、それぞれ `scroll`（BE-0326）で画面内に入れます。`scroll` は非慣性でツリーを再クエリするステップで、対象の中心が画面内に入った時点で止まります。固定距離のスワイプ連鎖は、密度の高い Android の画面（2400px）では iOS（約 900pt）に比べて画面のごく一部しか進まず、バックエンドごとの距離調整が要りましたが、`scroll` の再クエリはその調整を不要にします（BE-0208 ユニット 5）。単一タッチのジェスチャのフロー（`gestures`）もこのレーンに加えました。adb ドライバが、root 化したエミュレータでは生の `sendevent` によるタッチ列でダブルタップを実行するようになったためです（`e2e` ターゲットが先に `adb root` を実行します）。2 回のタップが 1 回の `adb shell` のなかで発火するので、タップごとに `input` の JVM を起動していたときには超過していたプラットフォームのダブルタップの受付時間に収まります。root 化していないデバイスでは、従来どおり `input tap` にフォールバックします。対象を画面内に入れるためのスワイプは、ユニット 5 のもう一方の仕組みに支えられています。既定の方向スワイプは画面サイズに対する一定の割合（`_SWIPE_FRACTION`、`bajutsu/orchestrator/actions/handlers/gestures.py`）だけ進むので、密度の高い Android の画面（2400px）でも iOS の画面（約 900pt）でも同じ割合をカバーします。固定距離では、そうはいきませんでした（BE-0208 ユニット 5）。マルチタッチのジェスチャのフロー（`gestures_multitouch`）もこのレーンに加えました（BE-0232）。adb ドライバが、root 化したエミュレータでピンチ / 回転を生の 2 スロットの `sendevent` スイープ（2 つの接点が複数フレームにわたって一緒に動きます）として実行するので、iOS が XCUITest で動かす共有シナリオが Android でもそのまま動きます。単一タッチのダブルタップと違って `input` へのフォールバックはなく（2 本指のジェスチャは近似できません）、root を要し、なければ明確に失敗します。ランタイム権限のフロー（`permission`）もこのレーンに加えました（BE-0208 ユニット 6。BE-0210 の事前付与を検証します）。これは iOS レーンが走らせるのと同じ `permission.yaml` です。付与の仕組みはシナリオではなく config にあるので、1 つのファイルで両方をまかなえます。`showcase-compose` が `POST_NOTIFICATIONS` を事前に付与するため（`grantPermissions` により lease 時に `pm grant` を実行します）、Android の `RequestPermission` コントラクトはダイアログを出さずに付与済みとして即座に返り、シナリオの `systemAlertHandling` ガードはここでは発火しません。よってこのレーンでもフローは決定的なまま（LLM も固定の待ち時間もなし）に保たれます（通知を事前付与できない iOS では、このガードが代わりに「Allow」をタップします）。デバイス制御のフロー（`device`）もこのレーンに加えました（BE-0208 ユニット 5）。GPS の位置を上書きし（`emu geo fix`）、クリップボードを書き込んで読み戻し、落ち着いた画面を再度確認します。これは iOS レーンが走らせるのと同じ `device.yaml` です。`setLocation` もクリップボードも両プラットフォームで宣言されるので、一つのファイルが iOS でも Android でも動きます（iOS 専用の `push` は `push.yaml` に分けました）。Stable の起動タブ上で、デバイス制御ファミリのうち `setLocation` と `clipboard` の両方を動かしています。`cmd clipboard` は実機では黙って何もせず、Android 10 以降はフォアグラウンドのアプリしかクリップボードを触れないため、クリップボードは showcase が `BajutsuAndroid` から組み込むアプリ内レシーバを経由します（BE-0233）。この書き込みと読み戻しは、強い assertion です。割り込みハンドラのフロー（`interrupts`）もこのレーンに加えました（BE-0314）。これは実際のアプリでは発火しない構文デモで、`interrupts` エントリの `condition` は画面上の何にも一致しないため、その裏で走るのは `firstlook` と同じ Stable→Horse→お気に入りのフローです。アプリや config に追加の変更は要りません。`firstlook` に対してこのフローが加えるのは、生きたツリーに対する BE-0314 の確認経路です。`wait` は自分のポーリング用ツリーに相乗りするので追加のコストはかかりませんが、素の act ステップは（このシナリオが `screenChanged` ポリシーを宣言していないため）ガード用のクエリを 1 回追加で払います。こうして、一度も発火しないハンドラが実機で何も乱さないこと、act 1 回につき読み取り 1 回分で収まることを、高速スイートの fake では示せない形で証明します。Views 版のレーンにも加えました。id はドット区切りの SPEC 形式とアンダースコア区切りの Android Views 形式の両方を持ち（BE-0221）、`firstlook` がすでに Views ツリーで実証済みの同じ id なので、そのまま動きます。`visual (adb)` ジョブは Compose の Stable カタログのピクセル視覚回帰チェックを実行します（`make -C demos/showcase/android e2e-visual`、BE-0208 ユニット 4）。要素ツリーの golden とは異なり、ピクセルのベースラインはホスト依存です。x86_64 のソフトウェアレンダラ（swiftshader）とローカルの arm64 エミュレータはピクセル単位で食い違うため、このベースラインは arm64 ではなくこの x86_64 レーンで採取してコミットします（`demos/showcase/scenarios/visual/baselines_android/`）。上部のステータスバーはマスクするので、時計が比較を揺らすことはありません。`uiautomator (codegen)` ジョブは codegen の出力経路です（`make -C demos/showcase/android e2e-codegen`、BE-0294）。`ios-e2e.yml` の `codegen (xcuitest)` の Android 版で、`codegen_android.yaml` からネイティブ UI Automator（Kotlin）テストを再生成し、Gradle の `connectedAndroidTest` が Compose の a11y アプリと計装 APK をビルドして両方をインストールし、生成テストをエミュレータに対して実行します（テスト時に bajutsu / adb ドライバ / AI は不要です）。ビルド前に再生成するので、古いチェックインがエミッタや `androidx.test.uiautomator` API のドリフトを覆い隠すことはありません。決定論的で実行環境に依存しないジョブ、すなわち `smoke (adb)`、`conformance (adb)`、`network (adb)` を、常に結果を報告する集約ジョブ `E2E (android)`（必須チェック、BE-0279）に集約します。`golden (adb)`、`visual (adb)`、`uiautomator (codegen)`、`fault-injection (adb)` はその `needs:` から意図的に外し、参考シグナルにとどめます（要素ツリーの golden は上流依存の変化で赤くなりえ、ピクセルのベースラインはホスト依存で、codegen と fault-injection のレーンは BE-0282 の前例にならいまずシグナルとして着地させるためです）。これは iOS の `E2E (iOS)` が引くのと同じ判断基準です |
| [`devicefarm.yml`](../../.github/workflows/devicefarm.yml) | Linux | **手動のみ**（`workflow_dispatch`） | **AWS Device Farm へのバッチ投入**（BE-0235）。showcase の Compose APK をビルドし、Bajutsu と config とシナリオをパッケージ化して [`scripts/devicefarm_submit.py`](../../scripts/devicefarm_submit.py) に渡します。このスクリプトが、`bajutsu run --backend adb` を Device Farm のホストで実行するカスタム環境のテスト仕様をアップロードし、実行をポーリングし、成果物をダウンロードして、**Bajutsu 自身の manifest 判定**（Device Farm の分類ではありません）を表示します。決定的なコアの外側の CI 側のグルーなので、判定に LLM は触れません。起動は `workflow_dispatch` のみで（push / PR では動かず、必須チェックにもなりません）、認証は GitHub OIDC から発行する短命の AWS 認証情報（`AWS_DEVICEFARM_ROLE_ARN`）を `devicefarm` の Environment にスコープし、プロジェクトとデバイスプールの ARN はリポジトリ変数に置きます。いずれかが未設定ならジョブは緑の no-op になり、運用者がアカウントを接続するまで休止します。実アカウントでのシリアル解決の実証は文書化した手動手順（[AWS Device Farm](devicefarm.md) を参照）とし、決定的ゲートからは意図的に外しています |
| [`ai-smoke.yml`](../../.github/workflows/ai-smoke.yml) | Linux | **手動のみ**（`workflow_dispatch`） | AI バックエンドアダプタ向けの**実 API 契約スモーク**のレーン（BE-0300）で、ジョブは **smoke (direct Anthropic API)** の 1 つです。Bajutsu 自身のアダプタコード（`bajutsu.ai.anthropic.AnthropicBackend`）を通して実プロバイダを些細な強制ツールのプロンプトで呼び出し、返り値がベンダー中立な `MessageResponse` として空でなくパースできることだけを検証します（`pytest tests/test_ai_backend_live_smoke.py -m live -n0`）。ほかのアダプタテストはいずれも手書きの `FakeAnthropic` を駆動するので、実 API の実際の形を再観測するものはこれ以外にありません。これはトランスポートとスキーマの検証であってモデルの品質検証ではないため、`run` / CI の判定に LLM は触れません（prime directive 1）。`live` マーカーがこのスイートを高速ゲートから外します（pyproject の `addopts` の `not live`）。まず**ゲート対象外**のシグナルとして着地させます（BE-0282 の前例）。起動は `workflow_dispatch` のみで、push / PR では動かないため、フォークからの実行が認証情報を目にすることはありません（`devicefarm.yml` が引く境界と同じです）。認証は `ai-smoke` の Environment にスコープした `ANTHROPIC_API_KEY` のリポジトリ secret です。未設定ならテストが自身をスキップし、ジョブは緑の no-op になるので、運用者が secret を接続するまでレーンは休止します。配線したのは直接 Anthropic API アダプタのみです。Bedrock は稼働中の AWS ロールを、`ant` はサインイン済みの OAuth CLI シートを要し、いずれも現実的には CI の secret にできないためで、それらの `-m live` テストはローカルや手動でなら実行できます |

### マージを止める E2E チェックはどれか（BE-0279）

各バックエンドのレーン、すなわち iOS（`E2E (iOS)`）、Android（`E2E (android)`）、web（`E2E (web)`）は、
常に結果を報告する集約ジョブを 1 つずつ持ち、それがそのレーンの必須チェックになります。バックエンドごとに
集約ジョブを分けることで切り分けが保たれ、赤いチェックが壊れたバックエンドを名指しします。**あるチェックが
マージを止めるのは、それが決定論的で実行環境に依存しない場合に限ります。** 結果が実行環境や上流依存に左右される
チェックは参考シグナルにとどめます。実行はされ、ドリフトを自分のジョブ上に出しますが、マージは止めません。

- **ピクセル視覚回帰（VRT）**、すなわち `visual` ジョブ。ピクセルのベースラインはホスト依存で（Simulator や
  エミュレータのレンダラは OS・デバイス・ツールチェインで変わります）、そのドリフトは Bajutsu 側の変更と
  無関係です。集約ジョブの `needs:` から外します。
- **要素ツリーの golden**、すなわち iOS / Android の `golden` ジョブ。決定論的ですが、Android ではツリーを
  上流の実機側サーバを通して読み取るため、その変化が Bajutsu 側の変更と無関係に赤くしえます。そのため
  golden のドリフトは、マージのブロックではなく PR ごとの参考シグナルとして出すのが適切です。集約ジョブの `needs:` から外します。

必須チェックが `paths:` フィルタで飛ばされると、いつまでも保留のままマージを止めてしまいます。そのため、
どのレーンもトリガでのパスゲートはしません。各レーンは全 PR（とマージキュー）で走り、代わりに `changes`
ジョブが重いジョブをパスゲートします。`changes` ジョブは [`scripts/e2e_changes.py`](../../scripts/e2e_changes.py)
を `E2E_LANE=ios|android|web` で実行します（レーンごとの関連度フィルタで、`tests/test_e2e_changes.py`
で単体テストしています）。集約ジョブは `if: always()` で走るので、パスによる省略は合格として報告され、無関係な
PR は走りもブロックもされません。新しい必須の集約ジョブを `main` のブランチ保護の規則に登録するのはリポジトリ外の
管理作業で、正確なチェック名を用いて管理者が行います。

このフィルタは `bajutsu/` の既定値を反転しています（BE-0333）。ファイルが「見てもらう」ために加わる必要のある
手書きのポジティブリスト（未掲載のものは黙って何も発火しなかった）ではなく、共有のコアがパッケージ *全体* を
一括して拾い、明示的に分類したものだけを差し引きます。すなわち、どのレーンも実行しない周辺（`_PERIPHERY_EXCLUSIONS`。
各項目が除外の理由を持ちます — analytics / analysis スタック、MCP サーバ、AI アダプタ、GitHub と cloud の連携、
そして混在パッケージ `agents` / `crawl` / `cli.commands` の個々の周辺モジュール）と、各レーンが再主張する
backend 固有のリーフ（`_LANE_CLAIMED`）です。どちらにも名指しされないファイルは一括で拾われ、誰かが分類するまで
全 3 レーンを発火させます — 無駄なジョブという安全側で、ポジティブリストが生んでいた黙った過小発火の代わりです。
この既定値が、ミスの一クラスをまとめて退治します。トップレベルのモジュールがパッケージへ分割されたケース
（`bajutsu/config` と `bajutsu/platform_lifecycle` はどちらもこのずれを起こし、レーンがまさに動かすためにある
コードを変更してもレーン全体が飛ばされていました）、新しいトップレベルモジュールや CLI コマンド、そして run パスが
import しているのにリストが一度も名指ししなかったファイル — 毎 run が書き込むマニフェスト書き出し `bajutsu/report/`
がその筆頭です — が、パッケージとモジュールが一括の拾い上げで等しく照合されるようになったため、いずれも発火します。

分類の健全性は、いくつかのテストが保ちます。`test_run_path_closure_is_gated_or_excluded` は run パスの静的な `ast`
import 閉包を辿り、到達したファイルがゲート対象でも分類済み周辺項目でもなければ落とします。run パスが *新たに*
import し始めた未分類のモジュールが、数か月後に「謎めいた緑の必須チェック」として表面化するのではなく、`make check`
で落ちるようにするためです。`test_periphery_exclusion_paths_exist` と `test_every_plain_literal_path_in_the_filter_exists`
は、各除外項目と各素のパスをツリーへ解決するので、リネームや削除は、パターンが何も照合しないまま残る代わりに、
ゲートの失敗として現れます。さらに共有のコアは、バックエンドごとの2つのディレクトリ（`bajutsu/drivers/`、
`bajutsu/platform_lifecycle/environments/`）を、各レーンが名指しするリーフを除いて一括して拾います。そして、
どちらの配下にも「1つもレーンを発火させないファイル」がないことを確認するテストと、各 `_LANE_CLAIMED` のリーフが
少なくとも1つのレーンに再主張されることを確認するテストがあります。よって、新しく足したバックエンドのモジュールは
見落とされるのではなく過剰に発火し、除外だけされて誰も主張しないリーフはゲートを落とします。反転した既定値の
過剰発火のコストは、出荷前に計測しました。直近 80 件のマージ済みプルリクエストでは、旧来のポジティブリストと
3 レーンすべてで同一に発火しました（`scripts/e2e_overfire_report.py`）。

`changes` ジョブは、安全だと証明できる唯一の場合について、もう一段だけ絞り込みます（BE-0322）。変更がレーンの
シナリオファイルだけに閉じているときは、レーン全体ではなく、変更されたシナリオを宣言しているジョブだけを発火させ
ます。フィルタは各 iOS ジョブがワークフローで宣言している `scenarios:` を読みます。加えて `codegen` ジョブと `visual`
ジョブについては、シナリオをワークフロー入力ではなく Makefile ターゲットで名指ししているため、`demos/showcase/Makefile`
のターゲットが走らせるシナリオを読みます（BE-0338）。こうして、変更されたシナリオからそれを読み込むジョブへの対応が、
ジョブの実際の実行内容からずれることはありません。そして `relevant` に加えて、各ジョブが条件に使う `shared` フラグと
`affected` ジョブ配列を出力します。テストが、Makefile から読んだ対応づけをそのターゲットに固定するので、ターゲットが
シナリオを増減させると、対応づけも一緒に動かない限りゲートが落ちます。それ以外の場合はすべて、従来どおりレーン全体を
発火させます。共有コードの変更（任意のシナリオに影響しうるドライバ / ランナー / アプリ / ワークフローのコード）、シナリオ
の部分集合を宣言しない次元ジョブ `conformance`（ドライバ適合性ハーネス全体を駆動します）、そしてジョブがシナリオで
キーづけられていないレーン（Android と web）です。この判断はレーン全体の側に倒すので、変更が壊しえたジョブを取りこぼす
ことはありません。そして `git` の差分、ワークフロー自身の宣言、そしてその Makefile ターゲットだけを読むので、経路に
LLM は入らず、実行の合否判定にも一切影響しません。

dev ツールは `dev` 依存グループにあるため、Linux ジョブは `uv sync --group dev` → `uv run
--no-sync …` で実行します（素の `uv run` はデフォルト集合に再同期して落としてしまいます）。
このゲートは [`make check`](../../Makefile) と [`pre-push`](../../.githooks/pre-push) フックを
段ごとにミラーしており、`actionlint`（CI が導入する単体バイナリ）以外は新規クローンでも `uv`
だけで同一に走ります。これが「ローカルで緑」＝「CI で緑」を担保します。

## あなたのアプリの CI で回す

> bajutsu はプレリリース（未公開）です。PyPI 公開までは vendor（submodule / checkout）して、その
> checkout からアクションを実行してください。アクションは bajutsu の `pyproject.toml` に対して
> `uv sync` を実行します。

bajutsu は CI 向けの出力を生成します。`junit.xml`、自己完結の `report.html`、`0`/`1` 終了
コード、そして Actions 内では失敗**アノテーション** + ジョブ**サマリ**です。macOS ランナーでは次のようにします。

1. 起動済みのシミュレータに **アプリ（と XCUITest ランナー）をビルドしてインストール**します。これはアプリごとに異なるためあなたの担当です（`xcodebuild`
   + `xcrun simctl install`）。
2. [`bajutsu-e2e`](../../.github/actions/bajutsu-e2e/action.yml) composite action で **bajutsu を実行**します。
   このアクションは依存の同期、シナリオの実行（`run --score` を付けるので、ログに入口画面の規約グレードが残ります）、そして
   成果物（report、スクリーンショット、動画、`network.json`）のアップロードを行います。XCUITest バックエンドは pip の extra を必要としません。ランナーは HTTP 越しに駆動し、`xcodebuild` はランナー上の Xcode に付属するためです。

```yaml
jobs:
  e2e:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4
      - uses: maxim-lobanov/setup-xcode@v1
        with: { xcode-version: latest-stable }
      - uses: astral-sh/setup-uv@v6
        with: { enable-cache: true }
      # --- アプリのビルド + インストール（あなたのビルド、起動済みシミュレータへ） ---
      - run: xcodebuild -scheme MyApp -destination 'generic/platform=iOS Simulator' -derivedDataPath dd build
      - id: sim
        run: |
          udid=$(xcrun simctl create ci "iPhone 16")
          xcrun simctl boot "$udid"; xcrun simctl bootstatus "$udid" -b
          echo "udid=$udid" >> "$GITHUB_OUTPUT"
      - run: xcrun simctl install "${{ steps.sim.outputs.udid }}" dd/Build/Products/Debug-iphonesimulator/MyApp.app
      # --- bajutsu 実行 ---
      - uses: your-org/bajutsu/.github/actions/bajutsu-e2e@main
        with:
          scenarios: e2e/*.yaml
          app: myapp
          udid: ${{ steps.sim.outputs.udid }}
```

### アノテーション + ジョブサマリ

`GITHUB_ACTIONS` がセットされていると、`bajutsu run` は失敗シナリオごとに `::error::` アノテー
ション（PR にインライン表示）を出し、`$GITHUB_STEP_SUMMARY` に PASS/FAIL 表を追記します。フラグ
不要で、Actions 環境を自動検出します。

### 補足

- **JUnit**：`junit.xml` はレポートの隣に書き出されます。これを test-reporter 系アクション（例 `dorny/test-reporter`）に渡すと、テスト結果をインライン表示できます。
- **決定性**：シナリオの [`mocks`](../network.md#deterministic-mocks) で通信をスタブし、ライブサーバへの依存をなくします。
- **規約スコア**：composite action は `run --score` を渡すので、run が実行時の最初の起動から算出した入口画面の規約グレード（`Ready` / `Partial` / `Blocked`、`doctor` が報告するスコアと同じもの）を stderr に出力します。この出力は診断だけを目的としており、合否の判定には一切関与しません。スコアを run に畳み込むことで、2 つめの XCUITest ランナーをコールド起動する別立ての `doctor` ステップを避けられます。実行可能性や capability のチェックを含むより広いプリフライトを行うには、ローカルで `bajutsu doctor` を実行してください。CI での env や権限の実行可能性ゲート（`xcodebuild` / Xcode の存在チェック）は今後の課題です。
- **影響ステップの選択**：[`bajutsu impact`](cli.md#impact) は `git` の diff がどのシナリオステップに影響しうるかを報告するので、パイプラインは素早いフィードバックのためにそれらのステップを先に並べられます。安全な既定は加算的で、スイート全体は流したまま、影響のあるものを先に置くだけです。マージ前の run を影響集合まで絞り込むのは opt-in で、健全なのは `impact` が示す2つの安全策があるときに限ります。レポートが**不完全**なとき（`--json` の `complete` が `false`、すなわち diff がどの参照 id にも対応しない変更を含むとき）はフルスイートへ退避すること、そして決定的なスイート全体を粗い cadence（マージ時、夜間、あるいはリリース時）で依然として流すことです。合否の判定は常に `run` に残ります。

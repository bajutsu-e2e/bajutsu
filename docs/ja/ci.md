# 継続的インテグレーション（CI）

2 つの別々のトピックを扱います。

1. **このリポの CI**。ツール自体をガードします（`.github/workflows/`）。
2. **あなたのアプリの CI で bajutsu を回す**。再利用できる composite action とレシピです。

## このリポの CI

| Workflow | ランナー | タイミング | 内容 |
|---|---|---|---|
| [`ci.yml`](../../.github/workflows/ci.yml)（`check` ジョブ） | Linux | `main` への push、全 PR（プルリクエスト） | Python 3.13 上で `make check` ゲート一式を実行します。ロックの鮮度（`uv lock --check`）、整形（`ruff format --check`）、lint（`ruff`）、シェル lint（`shellcheck`）、ワークフロー lint（`actionlint`）、型（`mypy bajutsu demos scripts`）、カバレッジ下限つきの `pytest`（`--cov-fail-under=89`）です。ロジック層はシミュレータ不要なので速く安価です |
| [`web-e2e.yml`](../../.github/workflows/web-e2e.yml) | Linux | 手動 + 全 PR + マージキュー（必須の `E2E (web)` チェック） | **web（Playwright）バックエンド**のレーン（BE-0279）で、ヘッドレス Chromium に対する 4 ジョブから成ります。**smoke (playwright)** は `demos/web` のシナリオを実行し（`make -C demos/web e2e`）、**dogfood (serve UI)** は Bajutsu 自身の serve SPA を駆動し（BE-0058）、**conformance (playwright)** は BE-0114 のドライバコントラクトを実ブラウザに対して実行します。**network (playwright)** は実ネットワーク経路（`page.route` の介入、`requestfinished` のキャプチャ、`mocked` フラグ、実際にキャプチャした証拠の redaction）を動かします（`make -C demos/web e2e-network`、BE-0282）。**Mac / Simulator 不要**で、コアがプラットフォーム非依存であることを示します。4 つとも同じ `changes` 検出ジョブ（`scripts/e2e_changes.py`、`E2E_LANE=web`）でパスゲートし、いずれも決定論的で実行環境に依存しないため、常に結果を報告する `E2E (web)` ジョブ（必須チェック）に集約します。web 経路に影響しえない PR はブラウザジョブを飛ばし、ゲートは合格します。**network (playwright)** はまず PR ごとのシグナルとして着地させましたが、CI で安定を確認できたので、現在は `E2E (web)` の `needs:` に昇格しています（BE-0282）。android-e2e.yml で既にゲート入りしている `network (adb)` の web 版にあたります |
| [`dependency-audit.yml`](../../.github/workflows/dependency-audit.yml) | Linux | 手動 + 週次 + `pyproject.toml` / `uv.lock` を触る `main` への push / PR | ロックした依存グラフ（`uv export` → `pip-audit --no-deps`）を脆弱性 DB に照合します。結果はロックファイルと DB だけで決まるので、依存の変更時と、変わらない pin に対して新たに公表された脆弱性を拾う週次スケジュールで実行します |
| [`swift.yml`](../../.github/workflows/swift.yml) | macOS | `main` への push + `BajutsuKit/**` を触る PR | [BajutsuKit](../../BajutsuKit) の `swift build` + `swift test` を実行します。純 Foundation のロジック（リクエスト照合 / モック解析）をシミュレータ無しで単体テストします。実機でのインターセプトそのものは `ios-e2e.yml` がカバーします |
| [`ios-e2e.yml`](../../.github/workflows/ios-e2e.yml) | macOS | 手動 + 全 PR + マージキュー（必須の `E2E` チェック） | **iOS（XCUITest）バックエンド**のレーンで、showcase に対する 10 個のジョブから成り、いずれも XCUITest バックエンド（常駐の BajutsuRunner）で走ります。**smoke (xcuitest)** は showcase をビルドしてシミュレータを起動し、XCUITest バックエンドで `smoke.yaml` を実行します（driver + simctl + 常駐ランナー + 実アクチュエーション）。**actuation (xcuitest)** は conformance ジョブの `tap` を超えて、実機で XCUITest を実際にアクチュエートします（BE-0281）。Stable の起動タブ上で、`back`（`navigation.yaml`）とデバイス制御（`setLocation` / クリップボード / `push`、`device.yaml` と `push.yaml`）を実行します。**golden (xcuitest)** は BE-0006 の要素ツリー golden を XCUITest で実行します（`golden.yaml`）。`android-e2e.yml` の `golden (adb)` に対応する iOS 版です。**codegen (xcuitest)** はシナリオからネイティブ XCUITest を生成し（`make -C demos/showcase ui-test`）、`xcodebuild` で実行します（テスト時に bajutsu / AI は不要です）。**gestures (xcuitest)** は pinch/rotate シナリオを XCUITest バックエンド上で `bajutsu run` の経路そのまま実行し（BE-0019）、2 本指ジェスチャを確認します。撤去済みの idb バックエンドが唯一実行できなかったアクチュエーションの種類です。**permission (xcuitest)** は決定論的な `permissions` フィールド（BE-0276、location のみ）と `handleSystemAlert` ステップ（BE-0316。SpringBoard の通知許可プロンプトの「許可」ボタンを、視覚モデルなしのアクセシビリティクエリでタップします）を実行します。**runner-actuation (xcuitest)** は常駐ランナーチャネルの `/type`（`search.yaml`）と `/swipe` + `/back`（`notices.yaml`）のエンドポイントを実行します（BE-0281）。どちらもタブをまたぐシナリオです。**bundled-runner (xcuitest)** は、config の `testRunner` ではなく wheel バンドルから解決したランナーで、SwiftUI と UIKit 両方の a11y アプリに対して smoke シナリオを実行し（BE-0292）、ランナーがアプリに依存しないことを確認します。**conformance (xcuitest)** はドライバ conformance スイート（BE-0114）を XCUITest バックエンドに対して実機で実行します。**visual (xcuitest)** は Stable カタログをコミット済みの `baselines_ios/` ベースラインとピクセル比較します（`make -C demos/showcase e2e-visual`）。ステータスバーと「Liquid Glass」タブバーはマスクします。`gestures`、`permission`、`runner-actuation`、`bundled-runner` はかつて 1 つのジョブ（`xcuitest (multi-touch)`）でしたが、本来の multi-touch という名前を越えて関心事が積み重なったため、それぞれ独立してシミュレータの起動とビルドを行うジョブに分割しました。共有の `setup-ios-toolchain` コンポジットアクションが、macOS の各ジョブが繰り返す Xcode / uv / xcodegen / シミュレータ起動の手順をまとめています。10 個のジョブはすべて同じ `changes` 検出ジョブでパスゲートされ、そのうち smoke、codegen、gestures、permission、runner-actuation、bundled-runner、conformance は常に結果を報告する単一の `E2E` ジョブ（必須チェック）に集約されます。`actuation`、`golden`、`visual` も同じ検出ジョブでパスゲートされますが、`E2E` の `needs:` には意図的に含めていません。新しく配線した XCUITest のアクチュエーションは、まずは参考シグナルとして着地させ（Simulator レーンにはフレーキーの実績があるため、BE-0218）、安定を確認してからゲートに昇格させます。要素ツリーの `golden` は決定論的で実行環境に依存しないため、そのドリフトを参考シグナルとして出し、`visual` のピクセルのベースラインはホスト依存（Simulator のレンダラが Xcode / デバイス / OS で変わります）だからです。いずれもドリフトやフレーキーを PR のブロックではなく単独ジョブのシグナルとして出します（`visual` が採取したスクリーンショットは `ios-e2e-visual-run` としてアップロードし、ベースラインの再採取に使います） |
| [`android-e2e.yml`](../../.github/workflows/android-e2e.yml) | Linux | 手動 + 全 PR + マージキュー（必須の `E2E (android)` チェック） | **Android（adb）バックエンド**のレーン（BE-0208）で、iOS や web のレーンのジョブ分割にならった観点ごとの 6 ジョブから成り、各ジョブが自前の x86_64 API 34 AVD を KVM のもとで起動します（`reactivecircus/android-emulator-runner`）。**smoke (adb)** は Compose と Views の showcase APK をビルドし、Stable タブのシナリオ（コアの id/tap/type/value のフローに、詳細画面への push と pop で戻る back ナビゲーションを加えたもの）を `--backend android` で実行します（`make -C demos/showcase/android e2e`）。**golden (adb)** は Compose の Stable カタログの golden 要素ツリーを実機でチェックし（`make -C demos/showcase/android e2e-golden`、BE-0006 / BE-0208 ユニット 4）、続いて resident チャネルを切って再実行して（`make -C demos/showcase/android e2e-fallback`、BE-0245）両方の読み取り経路が一致することを確かめます。**conformance (adb)** はドライバ conformance スイート（BE-0114）を実 adb バックエンドに対して実行します。`ios-e2e.yml` の `conformance (xcuitest)` の Android 版です。**visual (adb)** はピクセル VRT を実行します（後述）。**Mac / Simulator 不要**で、iOS と web の e2e レーンに並ぶ 3 つ目のバックエンドの Linux 版です。`changes` 検出ジョブ（`scripts/e2e_changes.py`、`E2E_LANE=android`）でパスゲートし、必須の集約ジョブ `E2E (android)` に集約します（BE-0279）。AVD は（ローカル検証の arm64 ではなく）x86_64 にして、x86_64 ランナー上で KVM が加速できるようにしています。golden のベースラインは arm64 で採取していますが、比較がフィールド単位で frame は健全性チェックだけのため、x86_64 でも通ります。sheet/cover のフロー（`components`、`modals`）も、このレーンに限って条件待ちの上限を引き上げることで含めています。`make -C demos/showcase/android e2e` が `BAJUTSU_MIN_WAIT_TIMEOUT`（既定 15 秒）を各待ちのタイムアウトの下限として渡すためです。ソフトウェアレンダリングのエミュレータは、共有シナリオの 5 秒の待ちに収まらないほど遅くモーダルを描画します。条件待ちは条件が満たされた瞬間に返るので、上限を広げても固定の待ち時間にはならず安全な上限にとどまり、共有シナリオには手を入れません（`timeout: 5` はどのバックエンドでも同じです）。深いスクロールのフロー（`controls`、`notices`）もこのレーンに加えました。既定の方向スワイプが固定の座標量ではなく画面に対する割合分を移動するようになったので、密度の高い Android の画面（2400px）でも iOS（約 900pt）と同じ割合まで届きます。固定量では Android のスクロールがはるかに小さく、遠くの対象（segmented control の値ノードや一覧下端の行）を表示できませんでした（`bajutsu/orchestrator/actions/handlers/gestures.py`、BE-0208 ユニット 5）。単一タッチのジェスチャのフロー（`gestures`）もこのレーンに加えました。adb ドライバが、root 化したエミュレータでは生の `sendevent` によるタッチ列でダブルタップを実行するようになったためです（`e2e` ターゲットが先に `adb root` を実行します）。2 回のタップが 1 回の `adb shell` のなかで発火するので、タップごとに `input` の JVM を起動していたときには超過していたプラットフォームのダブルタップの受付時間に収まります。root 化していないデバイスでは、従来どおり `input tap` にフォールバックします（BE-0208 ユニット 5）。マルチタッチのジェスチャのフロー（`gestures_multitouch`）もこのレーンに加えました（BE-0232）。adb ドライバが、root 化したエミュレータでピンチ / 回転を生の 2 スロットの `sendevent` スイープ（2 つの接点が複数フレームにわたって一緒に動きます）として実行するので、iOS が XCUITest で動かす共有シナリオが Android でもそのまま動きます。単一タッチのダブルタップと違って `input` へのフォールバックはなく（2 本指のジェスチャは近似できません）、root を要し、なければ明確に失敗します。ランタイム権限のフロー（`permission`）もこのレーンに加えました（BE-0208 ユニット 6。BE-0210 の事前付与を検証します）。これは iOS レーンが走らせるのと同じ `permission.yaml` です。付与の仕組みはシナリオではなく config にあるので、1 つのファイルで両方をまかなえます。`showcase-compose` が `POST_NOTIFICATIONS` を事前に付与するため（`grantPermissions` により lease 時に `pm grant` を実行します）、Android の `RequestPermission` コントラクトはダイアログを出さずに付与済みとして即座に返り、シナリオの `dismissAlerts` ガードはここでは発火しません。よってこのレーンでもフローは決定的なまま（LLM も固定の待ち時間もなし）に保たれます（通知を事前付与できない iOS では、このガードが代わりに「Allow」をタップします）。デバイス制御のフロー（`device`）もこのレーンに加えました（BE-0208 ユニット 5）。GPS の位置を上書きし（`emu geo fix`）、クリップボードを書き込んで読み戻し、落ち着いた画面を再度確認します。これは iOS レーンが走らせるのと同じ `device.yaml` です。`setLocation` もクリップボードも両プラットフォームで宣言されるので、一つのファイルが iOS でも Android でも動きます（iOS 専用の `push` は `push.yaml` に分けました）。Stable の起動タブ上で、デバイス制御ファミリのうち `setLocation` と `clipboard` の両方を動かしています。`cmd clipboard` は実機では黙って何もせず、Android 10 以降はフォアグラウンドのアプリしかクリップボードを触れないため、クリップボードは showcase が `BajutsuAndroid` から組み込むアプリ内レシーバを経由します（BE-0233）。この書き込みと読み戻しは、強い assertion です。`visual (adb)` ジョブは Compose の Stable カタログのピクセル視覚回帰チェックを実行します（`make -C demos/showcase/android e2e-visual`、BE-0208 ユニット 4）。要素ツリーの golden とは異なり、ピクセルのベースラインはホスト依存です。x86_64 のソフトウェアレンダラ（swiftshader）とローカルの arm64 エミュレータはピクセル単位で食い違うため、このベースラインは arm64 ではなくこの x86_64 レーンで採取してコミットします（`demos/showcase/scenarios/visual/baselines_android/`）。上部のステータスバーはマスクするので、時計が比較を揺らすことはありません。`uiautomator (codegen)` ジョブは codegen の出力経路です（`make -C demos/showcase/android e2e-codegen`、BE-0294）。`ios-e2e.yml` の `codegen (xcuitest)` の Android 版で、`codegen_android.yaml` からネイティブ UI Automator（Kotlin）テストを再生成し、Gradle の `connectedAndroidTest` が Compose の a11y アプリと計装 APK をビルドして両方をインストールし、生成テストをエミュレータに対して実行します（テスト時に bajutsu / adb ドライバ / AI は不要です）。ビルド前に再生成するので、古いチェックインがエミッタや `androidx.test.uiautomator` API のドリフトを覆い隠すことはありません。決定論的で実行環境に依存しないジョブ、すなわち `smoke (adb)` と `conformance (adb)` を、常に結果を報告する集約ジョブ `E2E (android)`（必須チェック、BE-0279）に集約します。`golden (adb)`、`visual (adb)`、`uiautomator (codegen)` はその `needs:` から意図的に外し、参考シグナルにとどめます（要素ツリーの golden は上流依存の変化で赤くなりえ、ピクセルのベースラインはホスト依存で、codegen レーンは BE-0282 の前例にならいまずシグナルとして着地させるためです）。これは iOS の `E2E` が引くのと同じ判断基準です |
| [`devicefarm.yml`](../../.github/workflows/devicefarm.yml) | Linux | **手動のみ**（`workflow_dispatch`） | **AWS Device Farm へのバッチ投入**（BE-0235）。showcase の Compose APK をビルドし、Bajutsu と config とシナリオをパッケージ化して [`scripts/devicefarm_submit.py`](../../scripts/devicefarm_submit.py) に渡します。このスクリプトが、`bajutsu run --backend adb` を Device Farm のホストで実行するカスタム環境のテスト仕様をアップロードし、実行をポーリングし、成果物をダウンロードして、**Bajutsu 自身の manifest 判定**（Device Farm の分類ではありません）を表示します。決定的なコアの外側の CI 側のグルーなので、判定に LLM は触れません。起動は `workflow_dispatch` のみで（push / PR では動かず、必須チェックにもなりません）、認証は GitHub OIDC から発行する短命の AWS 認証情報（`AWS_DEVICEFARM_ROLE_ARN`）を `devicefarm` の Environment にスコープし、プロジェクトとデバイスプールの ARN はリポジトリ変数に置きます。いずれかが未設定ならジョブは緑の no-op になり、運用者がアカウントを接続するまで休止します。実アカウントでのシリアル解決の実証は文書化した手動手順（[AWS Device Farm](devicefarm.md) を参照）とし、決定的ゲートからは意図的に外しています |

### マージを止める E2E チェックはどれか（BE-0279）

各バックエンドのレーン、すなわち iOS（`E2E`）、Android（`E2E (android)`）、web（`E2E (web)`）は、
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
を `E2E_LANE=ios|android|web` で実行します（レーンごとの関連パスの許可リストで、`tests/test_e2e_changes.py`
で単体テストしています）。集約ジョブは `if: always()` で走るので、パスによる省略は合格として報告され、無関係な
PR は走りもブロックもされません。新しい必須の集約ジョブを `main` のブランチ保護の規則に登録するのはリポジトリ外の
管理作業で、正確なチェック名を用いて管理者が行います。

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
   このアクションは依存の同期、任意の `doctor` プリフライト（非ブロッキング）、シナリオの実行、そして
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
- **`doctor`**：現状は規約の*スコア*（非ブロッキングのプリフライト）です。env や権限の実行可能性を判定するゲート（`xcodebuild` / Xcode の存在チェック）は今後の課題です。

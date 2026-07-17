# 継続的インテグレーション（CI）

2 つの別々のトピックを扱います。

1. **このリポの CI**。ツール自体をガードします（`.github/workflows/`）。
2. **あなたのアプリの CI で bajutsu を回す**。再利用できる composite action とレシピです。

## このリポの CI

| Workflow | ランナー | タイミング | 内容 |
|---|---|---|---|
| [`ci.yml`](../../.github/workflows/ci.yml)（`check` ジョブ） | Linux | `main` への push、全 PR（プルリクエスト） | Python 3.13 上で `make check` ゲート一式を実行します。ロックの鮮度（`uv lock --check`）、整形（`ruff format --check`）、lint（`ruff`）、シェル lint（`shellcheck`）、ワークフロー lint（`actionlint`）、型（`mypy bajutsu demos scripts`）、カバレッジ下限つきの `pytest`（`--cov-fail-under=89`）です。ロジック層はシミュレータ不要なので速く安価です |
| [`web-e2e.yml`](../../.github/workflows/web-e2e.yml) | Linux | 手動 + コアの実行経路、web バックエンド、web デモ、依存を触る `main` への push / PR | **web（Playwright）バックエンド**の smoke。`playwright install chromium` の後、`make -C demos/web e2e` が `demos/web` のシナリオをブラウザに対して決定的に実行します。**Mac / Simulator 不要**で、コアがプラットフォーム非依存であることを示します。Chromium の導入とブラウザ実行は重いので、全 PR では走らせず、`ios-e2e.yml` と同じ判断基準でパスフィルタを掛けています |
| [`dependency-audit.yml`](../../.github/workflows/dependency-audit.yml) | Linux | 手動 + 週次 + `pyproject.toml` / `uv.lock` を触る `main` への push / PR | ロックした依存グラフ（`uv export` → `pip-audit --no-deps`）を脆弱性 DB に照合します。結果はロックファイルと DB だけで決まるので、依存の変更時と、変わらない pin に対して新たに公表された脆弱性を拾う週次スケジュールで実行します |
| [`swift.yml`](../../.github/workflows/swift.yml) | macOS | `main` への push + `BajutsuKit/**` を触る PR | [BajutsuKit](../../BajutsuKit) の `swift build` + `swift test` を実行します。純 Foundation のロジック（リクエスト照合 / モック解析）をシミュレータ無しで単体テストします。実機でのインターセプトそのものは `ios-e2e.yml` がカバーします |
| [`ios-e2e.yml`](../../.github/workflows/ios-e2e.yml) | macOS | 手動 + 全 PR + マージキュー（必須の `E2E` チェック） | **iOS（idb / XCUITest）バックエンド**のレーンで、showcase に対する 6 つのジョブから成ります。**smoke (idb)** は showcase をビルドしてシミュレータを起動し、idb バックエンドで `smoke.yaml` を実行します（driver + simctl + idb）。**golden (idb)** は BE-0006 の要素ツリー golden を idb で実行します（`golden.yaml`）。`android-e2e.yml` の `golden (adb)` に対応する iOS 版です。**xcuitest (codegen)** はシナリオからネイティブ XCUITest を生成し（`make -C demos/showcase ui-test`）、`xcodebuild` で実行します（テスト時に bajutsu / idb / AI は不要です）。**xcuitest (multi-touch)** は pinch/rotate シナリオを XCUITest バックエンド上で `bajutsu run` の経路そのまま実行し（BE-0019）、idb では actuate できない 2 本指ジェスチャを確認します。**conformance (idb + xcuitest)** はドライバ conformance スイート（BE-0114）を両バックエンドに対して実機で実行します。**visual (idb)** は Stable カタログをコミット済みの `baselines_ios/` ベースラインとピクセル比較します（`make -C demos/showcase e2e-visual`）。ステータスバーと「Liquid Glass」タブバーはマスクします。smoke、xcuitest (codegen)、xcuitest (multi-touch)、conformance の 4 ジョブは `changes` 検出ジョブでパスゲートされ、常に結果を報告する単一の `E2E` ジョブ（必須チェック）に集約されます。`golden` と `visual` も同じ検出ジョブでパスゲートされますが、`E2E` の `needs:` には意図的に含めていません。要素ツリーの `golden` は upstream の `idb_companion` に対して走り、そのドリフトが Bajutsu 側の変更と無関係に赤くしうるためであり、`visual` のピクセルのベースラインはホスト依存（Simulator のレンダラが Xcode / デバイス / OS で変わります）だからです。どちらもドリフトを PR のブロックではなく単独ジョブのシグナルとして出します（`visual` が採取したスクリーンショットは `ios-e2e-visual-run` としてアップロードし、ベースラインの再採取に使います）。要素ツリーの golden は週次の idb-monitor でも、最新の `idb_companion` に対して走ります |
| [`android-e2e.yml`](../../.github/workflows/android-e2e.yml) | Linux | 手動 + adb バックエンド、Android showcase、アプリ側 SDK（`BajutsuAndroid`）、共有シナリオ、依存を触る `main` への push / PR | **Android（adb）バックエンド**のレーン（BE-0208）で、iOS や web のレーンのジョブ分割にならった観点ごとの 3 ジョブから成り、各ジョブが自前の x86_64 API 34 AVD を KVM のもとで起動します（`reactivecircus/android-emulator-runner`）。**smoke (adb)** は Compose と Views の showcase APK をビルドし、Stable タブのシナリオ（コアの id/tap/type/value のフローに、詳細画面への push と pop で戻る back ナビゲーションを加えたもの）を `--backend android` で実行します（`make -C demos/showcase/android e2e`）。**golden (adb)** は Compose の Stable カタログの golden 要素ツリーを実機でチェックし（`make -C demos/showcase/android e2e-golden`、BE-0006 / BE-0208 ユニット 4）、続いて resident チャネルを切って再実行して（`make -C demos/showcase/android e2e-fallback`、BE-0245）両方の読み取り経路が一致することを確かめます。**visual (adb)** はピクセル VRT を実行します（後述）。**Mac / Simulator 不要**で、idb と web の e2e レーンに並ぶ 3 つ目のバックエンドの Linux 版です。`web-e2e.yml` と同じくパスゲートし、必須チェックにはしません。AVD は（ローカル検証の arm64 ではなく）x86_64 にして、x86_64 ランナー上で KVM が加速できるようにしています。golden のベースラインは arm64 で採取していますが、比較がフィールド単位で frame は健全性チェックだけのため、x86_64 でも通ります。sheet/cover のフロー（`components`、`modals`）も、このレーンに限って条件待ちの上限を引き上げることで含めています。`make -C demos/showcase/android e2e` が `BAJUTSU_MIN_WAIT_TIMEOUT`（既定 15 秒）を各待ちのタイムアウトの下限として渡すためです。ソフトウェアレンダリングのエミュレータは、共有シナリオの 5 秒の待ちに収まらないほど遅くモーダルを描画します。条件待ちは条件が満たされた瞬間に返るので、上限を広げても固定の待ち時間にはならず安全な上限にとどまり、共有シナリオには手を入れません（`timeout: 5` はどのバックエンドでも同じです）。深いスクロールのフロー（`controls`、`notices`）もこのレーンに加えました。既定の方向スワイプが固定の座標量ではなく画面に対する割合分を移動するようになったので、密度の高い Android の画面（2400px）でも iOS（約 900pt）と同じ割合まで届きます。固定量では Android のスクロールがはるかに小さく、遠くの対象（segmented control の値ノードや一覧下端の行）を表示できませんでした（`bajutsu/orchestrator/actions/handlers/gestures.py`、BE-0208 ユニット 5）。単一タッチのジェスチャのフロー（`gestures`）もこのレーンに加えました。adb ドライバが、root 化したエミュレータでは生の `sendevent` によるタッチ列でダブルタップを実行するようになったためです（`e2e` ターゲットが先に `adb root` を実行します）。2 回のタップが 1 回の `adb shell` のなかで発火するので、タップごとに `input` の JVM を起動していたときには超過していたプラットフォームのダブルタップの受付時間に収まります。root 化していないデバイスでは、従来どおり `input tap` にフォールバックします（BE-0208 ユニット 5）。マルチタッチのジェスチャのフロー（`gestures_multitouch`）もこのレーンに加えました（BE-0232）。adb ドライバが、root 化したエミュレータでピンチ / 回転を生の 2 スロットの `sendevent` スイープ（2 つの接点が複数フレームにわたって一緒に動きます）として実行するので、iOS が XCUITest で動かす共有シナリオが Android でもそのまま動きます。単一タッチのダブルタップと違って `input` へのフォールバックはなく（2 本指のジェスチャは近似できません）、root を要し、なければ明確に失敗します。ランタイム権限のフロー（`permission`）もこのレーンに加えました（BE-0208 ユニット 6。BE-0210 の事前付与を検証します）。これは idb レーンが走らせるのと同じ `permission.yaml` です。付与の仕組みはシナリオではなく config にあるので、1 つのファイルで両方をまかなえます。`showcase-compose` が `POST_NOTIFICATIONS` を事前に付与するため（`grantPermissions` により lease 時に `pm grant` を実行します）、Android の `RequestPermission` コントラクトはダイアログを出さずに付与済みとして即座に返り、シナリオの `dismissAlerts` ガードはここでは発火しません。よってこのレーンでもフローは決定的なまま（LLM も固定の待ち時間もなし）に保たれます（通知を事前付与できない iOS では、このガードが代わりに「Allow」をタップします）。デバイス制御のフロー（`device`）もこのレーンに加えました（BE-0208 ユニット 5）。GPS の位置を上書きし（`emu geo fix`）、クリップボードを書き込んで読み戻し、落ち着いた画面を再度確認します。これは idb レーンが走らせるのと同じ `device.yaml` です。`setLocation` もクリップボードも両プラットフォームで宣言されるので、一つのファイルが iOS でも Android でも動きます（iOS 専用の `push` は `push.yaml` に分けました）。Stable の起動タブ上で、デバイス制御ファミリのうち `setLocation` と `clipboard` の両方を動かしています。`cmd clipboard` は実機では黙って何もせず、Android 10 以降はフォアグラウンドのアプリしかクリップボードを触れないため、クリップボードは showcase が `BajutsuAndroid` から組み込むアプリ内レシーバを経由します（BE-0233）。この書き込みと読み戻しは、強い assertion です。`visual (adb)` ジョブは Compose の Stable カタログのピクセル視覚回帰チェックを実行します（`make -C demos/showcase/android e2e-visual`、BE-0208 ユニット 4）。要素ツリーの golden とは異なり、ピクセルのベースラインはホスト依存です。x86_64 のソフトウェアレンダラ（swiftshader）とローカルの arm64 エミュレータはピクセル単位で食い違うため、このベースラインは arm64 ではなくこの x86_64 レーンで採取してコミットします（`demos/showcase/scenarios/visual/baselines_android/`）。上部のステータスバーはマスクするので、時計が比較を揺らすことはありません。3 つのジョブは集約ジョブを持たず独立しています（iOS の `E2E` ゲートと違い、Android は非必須です） |

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

1. 起動済みのシミュレータに **アプリをビルドしてインストール**します。これはアプリごとに異なるためあなたの担当です（`xcodebuild`
   + `xcrun simctl install`）。
2. [`bajutsu-e2e`](../../.github/actions/bajutsu-e2e/action.yml) composite action で **bajutsu を実行**します。
   このアクションは `idb_companion` の導入、依存の同期、任意の `doctor` プリフライト（非ブロッキング）、シナリオの実行、そして
   成果物（report、スクリーンショット、動画、`network.json`）のアップロードを行います。

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
- **`doctor`**：現状は規約の*スコア*（非ブロッキングのプリフライト）です。env や権限の実行可能性を判定するゲート（idb /
  idb_companion / Xcode の存在チェック）は今後の課題です。

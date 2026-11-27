# 継続的インテグレーション（CI）

このページは 2 つの別々のトピックを扱います。

| トピック | 内容 |
|---|---|
| **このリポの CI** | ツール自体をガードする workflow（`.github/workflows/`） |
| **あなたのアプリの CI で bajutsu を回す** | 再利用できる composite action とレシピ |

## このリポの CI

次の表は、各 workflow のランナーとタイミングをまとめたものです。表のあとの節で、同じ順に各 workflow の内容を説明します。

| Workflow | ランナー | タイミング |
|---|---|---|
| [`ci.yml`](../../.github/workflows/ci.yml)（`check` ジョブ） | Linux | `main` への push、全 PR（プルリクエスト） |
| [`mcp-wire.yml`](../../.github/workflows/mcp-wire.yml) | Linux | 手動 + `bajutsu/mcp/**`、`bajutsu mcp` の Command Line Interface（CLI）、wire テストに触れる PR |
| [`web-e2e.yml`](../../.github/workflows/web-e2e.yml) | Linux | 手動 + 全 PR + マージキュー（必須の `E2E (web)` チェック） |
| [`dependency-audit.yml`](../../.github/workflows/dependency-audit.yml) | Linux | 手動 + 週次 + `pyproject.toml` / `uv.lock` を触る `main` への push / PR |
| [`swift.yml`](../../.github/workflows/swift.yml) | macOS | `main` への push + `BajutsuKit/**` を触る PR |
| [`ios-e2e.yml`](../../.github/workflows/ios-e2e.yml) | macOS | 手動 + 全 PR + マージキュー（必須の `E2E (iOS)` チェック） |
| [`android-e2e.yml`](../../.github/workflows/android-e2e.yml) | Linux | 手動 + 全 PR + マージキュー（必須の `E2E (android)` チェック） |
| [`devicefarm.yml`](../../.github/workflows/devicefarm.yml) | Linux | **手動のみ**（`workflow_dispatch`） |
| [`ai-smoke.yml`](../../.github/workflows/ai-smoke.yml) | Linux | **手動のみ**（`workflow_dispatch`） |

### `ci.yml`：Python のゲート

`check` ジョブは、Python 3.13 上で `make check` ゲート一式を実行します。内容は次のとおりです。

- ロックの鮮度（`uv lock --check`）
- 整形（`ruff format --check`）
- lint（`ruff`）
- シェル lint（`shellcheck`）
- ワークフロー lint（`actionlint`）
- 型（`mypy bajutsu demos scripts`、およびテストスイートに対する `mypy --allow-untyped-defs tests`。BE-0388）
- カバレッジ下限つきの `pytest`（`--cov-fail-under=89`）

ロジック層はシミュレータ不要なので、このジョブは速く安価です。

### `mcp-wire.yml`：MCP ワイヤ round-trip

この workflow は **MCP ワイヤ round-trip** のレーンです（BE-0301）。MCP は Model Context Protocol の略です。ジョブは **wire (stdio)** の 1 つです。

このジョブは、`bajutsu mcp --transport stdio` を実際のサブプロセスとして起動します。駆動には、`mcp` の Software Development Kit（SDK）にある `fastmcp.Client` を使います。サブプロセスとは stdio 越しにやり取りします。コマンドは `pytest tests/test_mcp_wire.py -m mcp_wire -n0` です。

in-process の `tests/test_mcp.py` が触れない次の 3 点を検証します。

- `JSON-RPC` のフレーミング
- ツールスキーマの広告
- リソースの Uniform Resource Identifier（URI）のエンコーディング

具体的には、ツール一覧を取得し、`bajutsu_doctor` / `bajutsu_run` の呼び出しを round-trip します。run 証跡のリソースも読み取ります。いずれも `fake` バックエンドに対して行います。

`bajutsu_doctor` は、サーバ内でデバイス不要の実ロジックを走らせます。`bajutsu_run` は実際の `bajutsu run` を起動しますが、その verdict は環境に依存します。そのため round-trip では、verdict 行が transport を往復することだけを確認します。

`mcp_wire` マーカーが、このスイートを高速ゲートから外します（pyproject の `addopts` の `not mcp_wire`）。実サブプロセスと stdio のプロセス間通信（Inter Process Communication、IPC）は、in-process 呼び出しより時間に敏感です。そこで、まず**ゲート対象外**の PR ごとのシグナルとして着地させ、安定を確認してから必須化します。BE-0282 の `network (playwright)` が示した前例です。

### `web-e2e.yml`：web（Playwright）バックエンドのレーン

この workflow は **web（Playwright）バックエンド**のレーンです（BE-0279）。ヘッドレス Chromium に対する 6 ジョブから成ります。

**smoke (playwright)** は、`demos/web` のシナリオを実行します（`make -C demos/web e2e`）。

**dogfood (serve UI)** は、Bajutsu 自身の serve が配信するシングルページアプリケーションを駆動します（BE-0058）。

**conformance (playwright)** は、BE-0114 のドライバコントラクトを実ブラウザに対して実行します。

**network (playwright)** は、実ネットワーク経路を動かします（`make -C demos/web e2e-network`、BE-0282）。対象は次のとおりです。

- `page.route` の介入
- `requestfinished` のキャプチャ
- `mocked` フラグ
- 実際にキャプチャした証拠のマスク

**codegen (playwright)** は、シナリオからネイティブな Playwright テストを生成します（`make -C demos/web codegen-e2e`、BE-0293）。生成したテストは、実際の `@playwright/test` ランナーで実行します。`ios-e2e.yml` の `codegen (xcuitest)` の web 版です。

**onboarding (doctor / provision)** は、`bajutsu.common.provisioning.provision` を実行します。対象は、ブラウザが本当に入っていないホストです。provision 後には、`doctor` の環境ゲートを再確認します（BE-0304）。

このレーンは **Mac / Simulator 不要**で、コアがプラットフォーム非依存であることを示します。

6 つのジョブは、同じ `changes` 検出ジョブでパスゲートします（`scripts/e2e_changes.py`、`E2E_LANE=web`）。**onboarding** を除く 5 つのジョブは、決定論的で実行環境に依存しません。この 5 つが、常に結果を報告する `E2E (web)` ジョブ（必須チェック）に集約されます。**onboarding** は PR ごとの参考シグナルにとどまります（BE-0304）。web 経路に影響しえない PR はブラウザジョブを飛ばし、ゲートは合格します。

**network (playwright)** と **codegen (playwright)** は、いずれもまず PR ごとのシグナルとして着地させました。CI で安定を確認できたので、現在はどちらも `E2E (web)` の `needs:` に昇格しています。**network** は、`android-e2e.yml` ですでにゲート入りしている `network (adb)` の web 版です。**codegen** は、`ios-e2e.yml` ですでにゲート入りしている `codegen (xcuitest)` の web 版にあたります。

### `dependency-audit.yml`：依存関係の監査

この workflow は、ロックした依存グラフ（`uv export` → `pip-audit --no-deps`）を脆弱性 DB に照合します。

結果はロックファイルと DB だけで決まります。そのため、依存の変更時に実行します。変わらない pin に対して新たに公表された脆弱性を拾うため、週次のスケジュールでも実行します。

### `swift.yml`：BajutsuKit のユニットテスト

この workflow は、[BajutsuKit](../../BajutsuKit) の `swift build` と `swift test` を実行します。純 Foundation のロジック（リクエスト照合 / モック解析）を、シミュレータ無しでユニットテストします。実機でのインターセプトそのものは `ios-e2e.yml` がカバーします。

### `ios-e2e.yml`：iOS（XCUITest）バックエンドのレーン

この workflow は **iOS（XCUITest）バックエンド**のレーンです。showcase に対する 10 個の macOS ジョブから成ります。いずれも XCUITest バックエンド（常駐の BajutsuRunner）で走ります。

#### iOS のビルドとセットアップ

**build (app + runner)** ジョブは、showcase の 2 つのアプリ（SwiftUI + UIKit）と常駐ランナーを一度だけビルドします。ビルド結果は、1 つの `ios-build` アーティファクトとしてアップロードします。

consumer のジョブは、ビルドし直すのではなく、この成果物をダウンロードしてインストールします。コールドの Swift ビルドは、ジョブごとにではなく一度だけ支払われます。

各 consumer は、この成果物を複合アクション [`download-ios-build`](../../.github/actions/download-ios-build/action.yml) で取得します。このアクションは、短い間隔を置いてダウンロードを最大 2 回再試行します。アーティファクトサービスへの一時的なネットワークエラーが 1 回起きただけで、シナリオが走る前にジョブが失敗することはなくなりました。

共有の `setup-ios-toolchain` コンポジットアクションが、macOS の各ジョブが繰り返す手順をまとめています。対象は Xcode / uv / xcodegen / シミュレータ起動の手順です。`build` ジョブはシミュレータ不要なので、`boot: false` を渡します。

#### iOS のジョブ

**run (xcuitest)** は、同型のゲート担当レーンです。かつては別々の `smoke` / `gestures` / `permission` / `runner-actuation` ジョブだったシナリオを、1 回の `bajutsu run` にまとめました。常駐ランナーをシナリオ間で warm 再利用します（BE-0291）。

ただしこのレーンでは、再利用を 1 回に制限しています（`BAJUTSU_XCUITEST_MAX_WARM_REUSES: 1`）。混雑した CI ホストでの run 中のランナークラッシュを抑えるためです。

実際に走る 12 個のシナリオドキュメントは、7 個のファイルが保持する全シナリオです。BE-0315 が通知許可をネイティブにしたため、除外する `ai` タグ付きのシナリオはありません。これらは、コールドの `xcodebuild test-without-building` の起動を一度ではなく 6 回支払います。

6 回は、置き換えた 4 個の個別ジョブの合計より 2 回多い回数です。ただし、その 4 個のジョブはそれぞれ自前の Swift ビルドと起動も支払っていました。レーン全体で一度だけ支払う共有の `build` ジョブの分だけ、正味のコストは今も下がっています。

シナリオの内訳を次の表にまとめます。

| シナリオファイル | 内容 |
|---|---|
| `smoke.yaml` | Stable カタログ |
| `gestures_multitouch.yaml` | ピンチ / 回転の 2 本指ジェスチャ（BE-0019） |
| `search.yaml`、`notices.yaml` | 常駐ランナーチャネルの `/type`（`search.yaml`）と `/swipe` + `/back`（`notices.yaml`、BE-0281） |
| `alert.yaml` | 提示された `UIAlertController` の各ボタンのタップ |
| `permission.yaml` | 決定論的な `permissions` フィールドと、BE-0315 以降はネイティブなリアクティブ `systemAlertHandling` の通知許可 |
| `permission_system_alert.yaml` | プロアクティブな `handleSystemAlert` ステップ（BE-0316） |

`alert.yaml` は、`uniquelyIdentifiedElement` の重複グループの分岐に到達する唯一のシナリオです。この分岐は `swift test` ではコンパイルされません。`permission.yaml` は BE-0276 の location と BE-0315 の通知を対象とし、どちらもゲート上で走ります。`permission_system_alert.yaml` は、視覚モデルなしのアクセシビリティクエリで、SpringBoard の通知許可プロンプトの「許可」ボタンをタップします。

`run` は各シナリオを個別の verdict まで実行し、どれかが失敗すればゲートを赤くします。シナリオごとの合否は、ジョブ名ではなく run のレポートに移ります。

**actuation (xcuitest)** は、このレーンでゲート対象外の受け皿となるジョブです。まだ `run` に昇格していない実機の XCUITest アクチュエーション（BE-0281）を駆動します。

- `back`（`navigation.yaml`）
- デバイス制御（`setLocation` / クリップボード / `push`、`device.yaml` + `push.yaml`）
- 割り込みモニタが消すフォアグラウンドの通知バナー（`push_banner.yaml`、BE-0416）
- テキスト編集の 4 つのアクチュエータ（`text_editing.yaml`）

デバイス制御のシナリオは、Stable の起動タブ上で実行します。

さらに、アクチュエートできる 3 つ目のバックエンドとして、シナリオ作成機能（BE-0285）も駆動します。

- Log タブのライブなカウンタ値を取り込む `extract`（`extract.yaml`）
- Stable の 5 行を反復する `forEach`（`foreach.yaml`）
- data-driven の行（`data_driven.yaml`）
- `relaunch`（`relaunch.yaml`）

`forEach` のループ本体は、詳細画面を開いては戻り、反復のあいだにツリーを変化させます。

この 4 つは showcase の共有シナリオファイルをそのまま使います（BE-0221）。デバイス制御のシナリオより前に並べます。配信された通知や OS レベルの許可が、この 4 つの前へ来ないようにするためです。

リストの最後には、次の 2 つを置きます。

`system_alert_handling.yaml` は、`systemAlertHandling.rules` の宣言だけで各プロンプトに応答できることを確かめます。`handleSystemAlert` ステップは使いません。内訳は、通知の拒否に加えて、トラッキングとペーストそれぞれの許可と拒否で、計 5 本です。

拒否のシナリオは、XCUITest のデフォルトのハンドラではなく rule が応答したことを示します。デフォルトのハンドラはデフォルトのボタンを押すため、拒否の状態を作れないからです。このファイルも実機の新しいカバレッジなので、次のファイルと同じ理由でここから始めます。

`paste_system_alert.yaml`（BE-0369）は、プロセスをまたぐペーストにプロアクティブなステップで応答するシナリオです。`setClipboard` がアプリの外からペーストボードに値を書くため、アプリ自身の読み取りが iOS のペースト同意プロンプトを上げます。`handleSystemAlert: { prompt: paste }` が、ネイティブなアクセシビリティ照会でそのプロンプトに応答します。

許可と拒否のそれぞれを、対象の locale のもとと `locale: ja_JP` のもとで実行します。日本語の 2 本は、日本語で描画される SpringBoard に対してラベルの対応表を確かめます。実機の新しいカバレッジなので、`run` へ昇格させる前にここで安定性を確かめます。

**golden (xcuitest)** は、BE-0006 の要素ツリー golden を XCUITest で実行します（`golden.yaml`）。`android-e2e.yml` の `golden (adb)` に対応する iOS 版です。

**codegen (xcuitest)** は、シナリオからネイティブ XCUITest を生成し（`make -C demos/showcase ui-test`）、`xcodebuild` で実行します。テスト時に bajutsu / AI は不要です。UITests スキームをコンパイルするジョブで、常駐ランナーとは別の成果物です。そのため自前でビルドし、`build` アーティファクトの consumer にはなりません。

**bundled-runner (xcuitest)** は、config の `testRunner` ではなく wheel バンドルから解決したランナーを使います。SwiftUI と UIKit 両方の a11y アプリに対して smoke シナリオを実行し（BE-0292）、ランナーがアプリに依存しないことを確認します。

**conformance (xcuitest)** は、ドライバ conformance スイート（BE-0114）を XCUITest バックエンドに対して実機で実行します。

スイート実行中の Simulator インフラ障害（常駐ランナーのクラッシュ、`base.BackendCrashError`）は、`bajutsu run` と同じように復旧します。新しいデバイスを取り直して、該当テストを再実行します。上限はテストごとに、再試行回数と壁時計の再起動予算（`BAJUTSU_CRASH_RETRIES` / `BAJUTSU_CRASH_RECOVERY_BUDGET`）で区切ります。

これによりこの必須チェックは、ホストのフレーキーではなく本物の契約違反で赤くなります。毎回クラッシュする場合は、今までどおり明確に失敗します。各再起動は、アップロードする `conformance-recovery-report` アーティファクトに数え上げられます（BE-0334）。

固まった CoreSimulator はホスト側の障害ですが、デバイスを作り直しても直りません。作り直しの手順そのものが、いま停滞したのと同じ `simctl` 呼び出しでできているうえ、その停滞は放っておいても解消するからです。そこでスイートは再試行せず、ジョブログでホスト障害として名指しし、同じアーティファクトに数え上げます。

スイートは、アプリのデータコンテナの解決をテストごとではなくリースごとに行います。その 1 回の読み取りが期限を超えたときだけ、もう一度試します。2 回目で解消した停滞は何も赤くしないので、先行指標として同じアーティファクトに数え上げます。あとでチェックを固まらせるのは、悪化していくこの同じホストだからです（BE-0378）。

**fault-injection (xcuitest)** は、健全なランナーを駆動する代わりに、ランナー自身のホストプロセスにシグナルを送ります（BE-0305）。

短い `SIGSTOP` は、固まったランナーと同じようにチャネルをハングさせるので、一過性リトライ（BE-0207）が吸収しなければなりません。リトライ予算を超える凍結は、crash-recovery（BE-0287）が乗り越えなければなりません。`SIGKILL` では、無関係なタイムアウトではなく実行中のランナー障害を名指しするクラッシュ診断で、読み取りが終わらなければなりません。

各ケースは、検証対象の層に到達したというチャネル自身のログ記録を合図に障害を解除します。推測した待ち時間には頼りません。

**visual (xcuitest)** は、Stable カタログをコミット済みの `baselines_ios/` ベースラインとピクセル比較します。コマンドは `make -C demos/showcase e2e-visual` です。ステータスバーと「Liquid Glass」タブバーはマスクします。`e2e-visual` は自前でアプリをビルドし直します。そのため `codegen` と同様に、`build` アーティファクトの consumer にはなりません。

**network (xcuitest)** は、iOS の transport の上でネットワークの実経路を動かします（BE-0282）。コマンドは `make -C demos/showcase e2e-network` です。`network (playwright)`（`web-e2e.yml`）と `network (adb)`（`android-e2e.yml`）にあたる iOS 版です。

BajutsuKit のアプリ内 `URLProtocol` が、モックされた `POST /post` に 201 で応答します（実サーバなら 200 を返します）。各 exchange はループバック上の collector へ報告します。続く 2 本目のシナリオは、スタブを介さないカタログの `GET` を発行します。観測されたという事実だけをアサートします。

そのうえで `demos/showcase/network/assert_network_evidence.py` が、永続化された `network.json` をアサートします。確認する条件は次の 3 つです。

- モックされた exchange の `Authorization` ヘッダと `password` のフィールドがマスクされていること
- 生の秘密情報がファイルのどこにも残っていないこと
- スタブを介さない exchange が `mocked` を false として記録されていること

最後の条件により、マッチ範囲の広すぎるモックが、何もスタブしていない通信を自分のものだと主張できなくなります。

このうちマスクの確認が、BE-0282 が埋めようとしたギャップです。純粋なマスクのテストはすべて、手で組み立てた exchange をアルゴリズムに渡します。そのため、実際にキャプチャした資格情報が出荷される証跡でマスクされるかは、iOS 上ではここ以外で観測できません。このジョブは必要なものを自前でビルドする `make` ターゲットを動かします。そのため `codegen` や `visual` と同様に、`build` アーティファクトの consumer にはなりません。

このレーンには、デバイスを 2 台使う **pool (xcuitest)** ジョブ（BE-0298）がありましたが、取り下げました。2 台目の Simulator を起動し、1 回の `bajutsu run --workers 2` に対してデバイスプールの分離の主張を検証するジョブでした。その判定を返せたのは、5 回のうち 1 回だけです。

残る 4 回は、プールの検査ではなくホストで崩れました。

- `SimRenderServer` が自身のディスパッチキューでクラッシュする
- 固まった CoreSimulator に対して `simctl uninstall` がタイムアウトする
- runner のチャネルが run の途中で到達不能になる

赤くなるたびに、10 倍課金のランナーを 30 分ほど使いました。動画記録とタッチマーカーとシナリオの半分を削っても、崩れる位置が早まっただけでした。実際の並行デバイスに対するこの主張の検証は、いま Android レーンの `pool (adb)` だけが担います。このレーンでは、高速スイートの記帳の証明（`tests/runner/test_pool.py`）だけが残ります。

#### iOS のマージゲート

10 個のジョブはすべて、同じ `changes` 検出ジョブでパスゲートされます。そのうち次の 5 つは、常に結果を報告する単一の `E2E (iOS)` ジョブ（必須チェック）に集約されます。

- `build`
- `run`
- `codegen`
- `bundled-runner`
- `conformance`

`actuation`、`golden`、`visual`、`network` も、同じ検出ジョブでパスゲートされます。ただし `E2E (iOS)` の `needs:` には、意図的に含めていません。理由はジョブごとに異なります。

| ジョブ | `needs:` に含めない理由 |
|---|---|
| `actuation` | 新しく配線した XCUITest のアクチュエーションは、まず参考シグナルとして着地させる。Simulator レーンにはフレーキーの実績があるので（BE-0218）、安定を確認してからゲートに昇格させる |
| `golden` | 要素ツリーの golden は決定論的で実行環境に依存しないので、そのドリフトを参考シグナルとして出す |
| `visual` | ピクセルのベースラインはホスト依存。Simulator のレンダラが Xcode / デバイス / OS で変わる |
| `network` | 実機の新しいカバレッジなので、同じくまずシグナルとして着地させる。web 版が `E2E (web)` へ加わる前にたどったのと同じ道筋 |

`fault-injection` は、BE-0282 と同じ道筋をたどりました。まずシグナルとして着地しました。レーンを赤くしていたのは、runner の Hypertext Transfer Protocol サーバの欠陥です。その欠陥が直ったあと、失敗のない 64 回の実行を根拠に `needs:` へ入りました（BE-0305）。

いずれのジョブも、ドリフトやフレーキーを PR のブロックではなく、単独ジョブのシグナルとして出します。`visual` が採取したスクリーンショットは `ios-e2e-visual-run` としてアップロードし、ベースラインの再採取に使います。

### `android-e2e.yml`：Android（adb）バックエンドのレーン

この workflow は **Android（adb）バックエンド**のレーンです（BE-0208）。iOS や web のレーンのジョブ分割にならった、観点ごとの 12 ジョブから成ります。各ジョブは、自前の Android Virtual Device（AVD）を起動します。AVD は x86_64 で、Application Programming Interface（API）レベルは 34 です。起動は Kernel Virtual Machine（KVM）のもとで行います。起動には `reactivecircus/android-emulator-runner` を使います。これに加えて、AVD を起動しない13番目の **warm gradle cache (adb)** ジョブがあります。

このレーンは **Mac / Simulator 不要**です。iOS と web の e2e レーンに並ぶ、3 つ目のバックエンドの Linux 版にあたります。`changes` 検出ジョブがパスゲートします（`scripts/e2e_changes.py`、`E2E_LANE=android`）。ジョブは、必須の集約ジョブ `E2E (android)` に集約します（BE-0279）。

#### Android エミュレータのパッケージ

AVD を起動する各ジョブは、最初のエミュレータのステップより前に、Android Emulator とシステムイメージをインストールします。使うのは [`install-android-emulator-sdk`](../../.github/actions/install-android-emulator-sdk/action.yml) の composite action です。

`sdkmanager` は、ダウンロードしたアーカイブを展開できなくても警告を出し、終了コード 0 で終わります。そのため、壊れたダウンロードは後続の `adb` の `Connection refused` としてしか現れませんでした。この action はインストールを 1 回だけ再試行します。再び失敗したら、パッケージ名を示してステップを失敗させます（`scripts/install_android_sdk_packages.sh`）。

#### Android の Gradle キャッシュ先行ビルド

**warm gradle cache (adb)** ジョブは、Compose と resident server の Android Package Kit（APK）を先に一括でビルドします。`smoke`、`golden`、`conformance`、`fault-injection`、`pool` が、そろってこの APK をビルドするためです。この5つのジョブは、その分だけ依存解決とコンパイルを5回コールドでやり直さずに済みます。温めておいた Gradle の依存キャッシュとビルドキャッシュを復元して、ビルドを走らせます。

Views は、ここではビルドしません。Views を必要とするのは `smoke` だけです。ここで温めると、残り4つのジョブが（ジョブレベルの `needs:` のため）使いもしない Views のコンパイルを待つことになります。`smoke` 自身の Views ビルドは、このジョブができる前と同じく温めないままです。

`network`、`visual`、`codegen` は、意図してこの `needs:` から外しています。`codegen` はさらに Compose の AndroidTest（計装）バリアントを必要としますが、warm gradle cache (adb) はこれをビルドしません。ジョブレベルの `needs:` は、Gradle と無関係な AVD キャッシュのステップまで巻き込みます。ビルドが終わるまで、その軽いジョブ自身を丸ごと止めてしまいます。キャッシュヒットで得られる以上の待ち時間を強いることになるからです。

同一ラン内で並行実行するジョブどうしは、保存前のキャッシュを `actions/cache` で共有できません。そのため warm gradle cache (adb) を先に走らせ、先ほどの5つが `needs: warm-gradle-cache` を宣言します。`cache-read-only` はどのジョブも明示的には渡さず、composite action 側の安全なデフォルト（`true`、読み取り専用）に任せています。`gradle/actions` が文書化している、マルチジョブでのキャッシュ共有パターンです。

warm gradle cache (adb) が外れる、あるいは失敗しても、失うのは時間だけで、正しさは変わりません。そのため下流の各ジョブの `if:` は、`always()` ではなく `!cancelled()` を使います。`always()` では、`android-e2e.yml` 自身の `concurrency: cancel-in-progress` で打ち切られたはずの実行でも、ジョブが動き続けてしまうためです。

`!cancelled()` は、warm gradle cache (adb) の成功に対する `needs` の暗黙の依存を、明示的な許容条件で吸収します。実行そのものが打ち切られた場合は、そこで止まります。このジョブは `E2E (android)` の `needs:` にも加えていません。

#### Android のジョブ

**smoke (adb)** は、Compose と Views の showcase APK をビルドします。Stable タブのシナリオを `--backend android` で実行します（`make -C demos/showcase/android e2e`）。シナリオは、コアの id/tap/type/value のフローに、詳細画面へ進んでから戻る back ナビゲーションを加えたものです。

**golden (adb)** は、Compose の Stable カタログの golden 要素ツリーを実機でチェックします（BE-0006 / BE-0208 ユニット 4）。コマンドは `make -C demos/showcase/android e2e-golden` です。続いて resident チャネルを切って再実行します（BE-0245）。コマンドは `make -C demos/showcase/android e2e-fallback` です。両方の読み取り経路が一致することを確かめます。

**network (adb)** は、BE-0283 のネットワークキャプチャの検証です。`request` ステップが、実際のエミュレータ通信を観測します。通信は、BajutsuAndroid のインターセプタが `adb reverse` の collector ブリッジ越しに報告します。

**conformance (adb)** は、ドライバ conformance スイート（BE-0114）を実 adb バックエンドに対して実行します。`ios-e2e.yml` の `conformance (xcuitest)` の Android 版です。

**fault-injection (adb)** は、ディスプレイをスリープさせます（BE-0305）。コマンドは `make -C demos/showcase/android e2e-fault-injection` です。実際の読み取り元が、本当に空の要素ツリーを返す状態になります。このジョブは 2 つを検証します。

- `CoordinateTreeDriver` の transient-empty リトライが、実際の条件を乗り越えること。高速スイートのテストでは、この条件を組み立てるしかありません
- リトライ予算を超えて空が続いた場合に、明確に失敗すること

**visual (adb)** はピクセルの Visual Regression Testing（VRT）を実行します（後述）。**pool (adb)** は、このレーンで唯一エミュレータを 2 台使うジョブです（BE-0298。後述）。**uiautomator (codegen)** は codegen の出力経路です（後述）。

**pool (adb)** は、キャッシュした AVD の 2 台目のインスタンスを `-read-only` で起動します。起動は、emulator-runner アクション自身のステップの内側から行います（`scripts/android_pool_e2e.sh`）。スクリプトが必要なのは、このアクションにエミュレータを 2 台使うモードがないからです。アクションが起動するエミュレータは、そのステップのあいだしか生きていません。

ジョブは、状態に依存しないシナリオ 4 本を 1 回の `bajutsu run --workers 2` で走らせます（`make -C demos/showcase/android e2e-pool`）。分離の主張は `scripts/assert_pool_isolation.py` で検証します。

2 台とも `-memory 3072 -cores 1` と `-read-only` で動かします。`-read-only` は、2 つのエミュレータプロセスが 1 つの AVD を共有できるようにするフラグです。ジョブ自身のエミュレータにも、このフラグを渡します。AVD を読み書きで掴んだインスタンスは AVD をロックし、2 台目をそのまま拒否するからです。

`-read-only` はスナップショットの読み込みも無効にします。このジョブは、このレーンで唯一、常に 2 台ともコールドブートします。自前の AVD キャッシュエントリが買うのは、resume ではなく AVD の作成です。変更フィルタも、レーン全体のシグナルではなく、より狭い `pool` 出力を使います。

`visual (adb)` ジョブは、Compose の Stable カタログのピクセル視覚回帰チェックを実行します（BE-0208 ユニット 4）。コマンドは `make -C demos/showcase/android e2e-visual` です。要素ツリーの golden とは異なり、ピクセルのベースラインはホスト依存です。x86_64 のソフトウェアレンダラ（swiftshader）とローカルの arm64 エミュレータは、ピクセル単位で食い違います。

そのためこのベースラインは、arm64 ではなくこの x86_64 レーンで採取してコミットします。置き場は `demos/showcase/scenarios/visual/baselines_android/` です。上部のステータスバーはマスクするので、時計が比較を揺らすことはありません。

同じターゲットは続けて `status_bar_android.yaml` も実行します（BE-0478 ユニット 6）。Home キーで `background` と `foreground` を行い、System UI のデモモードで `overrideStatusBar` を適用します。ステータスバーの帯だけを専用のコミット済みベースラインと比較し、アプリ単位のロケール `ja_JP` がアプリ自身の構成に届いたことを読み取って確かめます。

`uiautomator (codegen)` ジョブは、codegen の出力経路です（`make -C demos/showcase/android e2e-codegen`、BE-0294）。`ios-e2e.yml` の `codegen (xcuitest)` の Android 版です。このジョブは、`codegen_android.yaml` からネイティブ UI Automator（Kotlin）テストを再生成します。

続いて Gradle の `connectedAndroidTest` が、Compose の a11y アプリと計装 APK をビルドして両方をインストールします。生成テストは、エミュレータに対して実行します。テスト時に bajutsu / adb ドライバ / AI は不要です。ビルド前に再生成するので、古いチェックインがエミッタや `androidx.test.uiautomator` API のドリフトを覆い隠すことはありません。

#### Android のエミュレータとシナリオの範囲

AVD は、ローカル検証の arm64 ではなく x86_64 にしています。x86_64 ランナー上で KVM が加速できるようにするためです。golden のベースラインは arm64 で採取していますが、x86_64 でも通ります。比較がフィールド単位で、frame は健全性チェックだけだからです。

sheet/cover のフロー（`components`、`modals`）も、このレーンに限って条件待ちの上限を引き上げることで含めています。`make -C demos/showcase/android e2e` が、`BAJUTSU_MIN_WAIT_TIMEOUT`（デフォルト 15 秒）を各待ちのタイムアウトの下限として渡すためです。ソフトウェアレンダリングのエミュレータは、共有シナリオの 5 秒の待ちに収まらないほど遅くモーダルを描画します。

条件待ちは、条件が満たされた瞬間に返ります。上限を広げても固定の待ち時間にはならず、安全な上限にとどまります。共有シナリオには手を入れません（`timeout: 5` はどのバックエンドでも同じです）。

深いスクロールのフロー（`controls`、`notices`）もこのレーンに加えました。`controls` はボタン群の下にある segmented control の値ノードを、`notices` は一覧のずっと下のほうにある行を、それぞれ `scroll`（BE-0326）で画面内に入れます。`scroll` は非慣性でツリーを再クエリするステップで、対象の中心が画面内に入った時点で止まります。

固定距離のスワイプ連鎖は、密度の高い Android の画面（2400px）では iOS（約 900pt）に比べて画面のごく一部しか進みません。そのため、バックエンドごとの距離調整が要りました。`scroll` の再クエリは、その調整を不要にします（BE-0208 ユニット 5）。

`system` と `modals` も、この変更で同じように `scroll` で遠くの対象を画面内に入れます。対象は、Permissions タブの `sys.paste.value` と Log タブの `log.dialog.value` です。これでこのレーンの 4 つのフローが `scroll` に依存します。

単一タッチのジェスチャのフロー（`gestures`）もこのレーンに加えました。adb ドライバが、root 化したエミュレータでは生の `sendevent` によるタッチ列でダブルタップを実行するようになったためです（`e2e` ターゲットが先に `adb root` を実行します）。2 回のタップが 1 回の `adb shell` のなかで発火します。タップごとに `input` の Java Virtual Machine（JVM）を起動していたときには超過していた、プラットフォームのダブルタップの受付時間に収まります。root 化していないデバイスでは、従来どおり `input tap` にフォールバックします。

対象を画面内に入れるためのスワイプは、ユニット 5 のもう一方の仕組みに支えられています。デフォルトの方向スワイプは、画面サイズに対する一定の割合だけ進みます。定数は `bajutsu/common/orchestrator/actions/handlers/gestures.py` の `_SWIPE_FRACTION` です。そのため、密度の高い Android の画面（2400px）でも iOS の画面（約 900pt）でも、同じ割合をカバーします。固定距離では、そうはいきませんでした（BE-0208 ユニット 5）。

マルチタッチのジェスチャのフロー（`gestures_multitouch`）もこのレーンに加えました（BE-0232）。adb ドライバが、root 化したエミュレータでピンチ / 回転を生の 2 スロットの `sendevent` スイープとして実行します。2 つの接点が、複数フレームにわたって一緒に動きます。iOS が XCUITest で動かす共有シナリオが、Android でもそのまま動きます。単一タッチのダブルタップと違って `input` へのフォールバックはなく（2 本指のジェスチャは近似できません）、root を要し、なければ明確に失敗します。

ランタイム権限のフロー（`permission`）もこのレーンに加えました（BE-0208 ユニット 6。BE-0210 の事前付与を検証します）。これは iOS レーンが走らせるのと同じ `permission.yaml` です。付与の仕組みはシナリオではなく config にあるので、1 つのファイルで両方をまかなえます。

`showcase-compose` は、`POST_NOTIFICATIONS` を事前に付与します（`grantPermissions` により lease 時に `pm grant` を実行します）。そのため Android の `RequestPermission` コントラクトは、ダイアログを出さずに付与済みとして即座に返ります。シナリオの `systemAlertHandling` ガードはここでは発火しません。よってこのレーンでもフローは決定的なまま（Large Language Model（LLM）も固定の待ち時間もなし）に保たれます。通知を事前付与できない iOS では、このガードが代わりに「Allow」をタップします。

デバイス制御のフロー（`device`）もこのレーンに加えました（BE-0208 ユニット 5）。Global Positioning System（GPS）の位置を上書きし（`emu geo fix`）、クリップボードを書き込んで読み戻します。落ち着いた画面を再度確認します。これは iOS レーンが走らせるのと同じ `device.yaml` です。`setLocation` とクリップボードは両プラットフォームで宣言されます。そのため 1 つのファイルが、iOS でも Android でも動きます（`push` は `push.yaml` に分けました）。smoke ジョブは `push.yaml` と `push_android.yaml` も実行します。BajutsuAndroid のプッシュレシーバ経由でプッシュを届け、アプリのハンドラが表示したタイトルを確認します（BE-0498）。

フローは Stable の起動タブ上で、デバイス制御ファミリのうち `setLocation` と `clipboard` の両方を動かします。`cmd clipboard` は実機では黙って何もせず、Android 10 以降はフォアグラウンドのアプリしかクリップボードを触れません。そのためクリップボードは、showcase が `BajutsuAndroid` から組み込むアプリ内レシーバを経由します（BE-0233）。この書き込みと読み戻しは、強い assertion です。

位置のフロー（`location_android`）もこのレーンに加わります（BE-0481）。`e2e` はエミュレータ自身の serial でこのフローを走らせ、その経路は `emu geo fix` を使います。別の **location over adb connect (adb)** ジョブが、同じフローを走らせ直します。このジョブは `e2e-location-connect` を使い、`adb connect` で接続したエミュレータで走ります。

このジョブは、実行履歴がたまるまで `E2E (android)` の `needs:` に含めない PR ごとのシグナルです。`golden` や `app-crash` と同じ扱いです。`<host>:<port>` の serial には、adb から届くコンソールがありません。そのため `setLocation` は、BajutsuAndroid のアプリ内の位置レシーバを通ります。実機が通るのと同じ経路です。

割り込みハンドラのフロー（`interrupts`）もこのレーンに加えました（BE-0314）。これは実際のアプリでは発火しない構文デモです。`interrupts` エントリの `condition` は画面上の何にも一致しないため、その裏で走るのは `firstlook` と同じ Stable→Horse→お気に入りのフローです。アプリや config に追加の変更は要りません。

`firstlook` に対してこのフローが加えるのは、生きたツリーに対する BE-0314 の確認経路です。`wait` は自分のポーリング用ツリーに相乗りするので、追加のコストはかかりません。素の act ステップは、このシナリオが `screenChanged` ポリシーを宣言していないため、ガード用のクエリを 1 回追加で払います。

こうして、一度も発火しないハンドラが実機で何も乱さないこと、act 1 回につき読み取り 1 回分で収まることを、高速スイートの fake では示せない形で証明します。Views 版のレーンにも加えました。id はドット区切りの仕様書の形式とアンダースコア区切りの Android Views 形式の両方を持ちます（BE-0221）。`firstlook` がすでに Views ツリーで実証済みの同じ id なので、そのまま動きます。

#### Android のマージゲート

決定論的で実行環境に依存しない次のジョブを、常に結果を報告する集約ジョブ `E2E (android)`（必須チェック、BE-0279）に集約します。

- `smoke (adb)`
- `conformance (adb)`
- `network (adb)`
- `fault-injection (adb)`

`golden (adb)`、`visual (adb)`、`uiautomator (codegen)`、`pool (adb)` は、その `needs:` から意図的に外し、参考シグナルにとどめます。理由は次の 3 点です。

| 対象 | 参考シグナルにとどめる理由 |
|---|---|
| 要素ツリーの golden | 上流依存の変化で赤くなりうる |
| ピクセルのベースライン | ホスト依存 |
| codegen とエミュレータ 2 台のレーン | BE-0282 の前例にならい、まずシグナルとして着地させる |

この判断は iOS の `E2E (iOS)` が引くのと同じ判断基準です。

`fault-injection (adb)` は、BE-0282 の道筋を最後まで歩みました。73 回の実行で失敗は 1 回でした。しかもそれは、レーンが検証している内容ではなく、エミュレータが起動しなかったことによるものです。同じ期間の `conformance (adb)` は、67 回中 4 回失敗しています。

この結果が、`fault-injection (adb)` をシグナルの地位から集約ジョブの `needs:` へ押し上げました。リトライの実際の検出に入った回帰は、マージが無視できる赤ではなく、マージを止める赤になりました（BE-0305）。

`tests/test_e2e_gate_needs.py` は高速スイートで、次の 3 点を固定します。

- 各集約ジョブの構成
- ゲートが `needs:` に挙げたジョブは、それぞれの結果が読まれるという不変条件
- それぞれの結果が本当にチェックを赤にすること（verdict のスクリプト自体を実行して確認）

依存の失敗が、集約ジョブが green と数えるパススキップとして通ってはならないからです。

### `devicefarm.yml`：AWS Device Farm へのバッチ投入

この workflow は **AWS Device Farm へのバッチ投入**です（BE-0235）。AWS は Amazon Web Services の略です。workflow は showcase の Compose APK をビルドし、Bajutsu、config、シナリオをパッケージ化します。パッケージは [`scripts/devicefarm_submit.py`](../../scripts/devicefarm_submit.py) に渡します。このスクリプトの処理は、次の 4 段階です。

1. カスタム環境のテスト仕様のアップロード（`bajutsu run --backend adb` を Device Farm のホストで実行する仕様）
2. 実行のポーリング
3. 成果物のダウンロード
4. **Bajutsu 自身の manifest 判定**の表示（Device Farm の分類ではない）

この workflow は決定的なコアの外側の CI 側のグルーなので、判定に LLM は触れません。起動は `workflow_dispatch` のみで、push / PR では動かず、必須チェックにもなりません。

認証は、GitHub の OpenID Connect から発行する短命の AWS 認証情報（`AWS_DEVICEFARM_ROLE_ARN`）です。この認証情報を `devicefarm` の Environment にスコープします。プロジェクトとデバイスプールの Amazon Resource Name（ARN）は、リポジトリ変数に置きます。いずれかが未設定ならジョブは緑の no-op になり、運用者がアカウントを接続するまで休止します。

実アカウントでのシリアル解決の実証は、文書化した手動手順です（[AWS Device Farm](devicefarm.md) を参照）。決定的ゲートからは意図的に外しています。

### `ai-smoke.yml`：実モデルのレーン

この workflow は**実モデル**のレーンです。ジョブは 2 つで、どちらも**ゲート対象外**のシグナルです（BE-0282 の前例）。

**smoke (direct Anthropic API)**（BE-0300）は、実プロバイダを呼び出します。呼び出しには、Bajutsu 自身のアダプタコード `bajutsu.ai.anthropic.AnthropicBackend` を通します。プロンプトは些細な強制ツールのものです。返り値がベンダー中立な `MessageResponse` として、空でなくパースできることだけを検証します。コマンドは `pytest tests/test_ai_backend_live_smoke.py -m live -n0` です。

ほかのアダプタテストはいずれも、手書きの `FakeAnthropic` を駆動します。実 API の実際の形を再観測するものは、これ以外にありません。このジョブはトランスポートとスキーマの検証であって、モデルの品質検証ではありません。

**accuracy (system-alert guard)**（BE-0308）は、実モデルに対するもう一方の問いを立てます。トランスポートが往復するかではなく、返ってきた答えが正しいかどうかです。このジョブは、ショーケースアプリから捕捉した本物の iOS システムダイアログを、視覚アラートロケータ（`bajutsu.agents.alerts.ClaudeAlertLocator`）に見せます。

返ってきた座標が、正しい dismiss コントロールの frame 内に収まることを検証します。コマンドは `pytest tests/test_real_model_alerts.py -m live -n0` です。これがこのガード自身の安全性の主張です。ほかのガードのテストはいずれも、テスト作者が打ち込んだ `AlertDecision` を渡すだけです。実モデルに本物のアラートを見せて判断させるテストは、1 つもありません。

ダイアログは、`tests/fixtures/be0308/` にコミット済みの捕捉物です。次の 4 つがあります。

- 通知プロンプト
- 位置情報プロンプト
- iOS のプロセス間ペースト同意
- ショーケースアプリ自身の削除確認（ガードがその破壊的なボタンに手を伸ばしてはならないケース）

捕捉物に Simulator は要らないので、ライブなのはモデル呼び出しだけです。各捕捉物の整合性を確かめる決定的な検証は、認証情報なしで毎回のゲートを走ります。

どちらのジョブも、`run` / CI の判定に LLM を置きません（prime directive 1）。いずれも AI の authoring 周縁だけを動かします。`live` マーカーが両方のスイートを高速ゲートから外します（pyproject の `addopts` の `not live`）。

起動はどちらも `workflow_dispatch` のみで、push / PR では動きません。そのため、フォークからの実行が認証情報を目にすることはありません（`devicefarm.yml` が引く境界と同じです）。認証は、`ai-smoke` の Environment にスコープした `ANTHROPIC_API_KEY` のリポジトリ secret です。未設定ならテストが自身をスキップし、どちらのジョブも緑の no-op になります。運用者が secret を接続するまで、レーンは休止します。

配線したのは、直接 Anthropic API アダプタのみです。Bedrock は稼働中の AWS ロールを、`ant` はサインイン済みの OAuth CLI シートを要します。いずれも現実的には CI の secret にできないためです。それらの `-m live` テストは、ローカルや手動でなら実行できます。

### マージを止める E2E チェックはどれか（BE-0279）

各バックエンドのレーンは、常に結果を報告する集約ジョブを 1 つずつ持ちます。iOS は `E2E (iOS)`、Android は `E2E (android)`、web は `E2E (web)` です。この集約ジョブが、そのレーンの必須チェックになります。バックエンドごとに集約ジョブを分けると切り分けが保たれ、赤いチェックが壊れたバックエンドを名指しします。

**あるチェックがマージを止めるのは、そのチェックが決定論的で、実行環境から独立している場合だけです。** 結果が実行環境や上流依存に左右されるチェックは、参考シグナルにとどめます。参考シグナルのジョブは実行され、ドリフトを自分のジョブ上に出しますが、マージは止めません。次の 2 種類が該当し、どちらも集約ジョブの `needs:` から外します。

| チェック | 参考シグナルにとどめる理由 |
|---|---|
| **ピクセル視覚回帰（VRT）**、すなわち `visual` ジョブ | ピクセルのベースラインはホスト依存で、そのドリフトは Bajutsu 側の変更と無関係。Simulator やエミュレータのレンダラは、OS、デバイス、ツールチェインで変わる |
| **要素ツリーの golden**、すなわち iOS / Android の `golden` ジョブ | 決定論的だが、Android ではツリーを上流の実機側サーバを通して読み取る。サーバの変化が、Bajutsu 側の変更と無関係にチェックを赤くしうる。golden のドリフトは、マージのブロックではなく PR ごとの参考シグナルとして出すのが適切 |

必須チェックが `paths:` フィルタで飛ばされると、いつまでも保留のままマージを止めてしまいます。そのため、どのレーンもトリガでのパスゲートはしません。各レーンは全 PR（とマージキュー）で走り、代わりに `changes` ジョブが重いジョブをパスゲートします。

`changes` ジョブは、[`scripts/e2e_changes.py`](../../scripts/e2e_changes.py) を `E2E_LANE=ios|android|web` で実行します。このスクリプトはレーンごとの関連度フィルタで、`tests/test_e2e_changes.py` がユニットテストしています。集約ジョブは `if: always()` で走るので、パスによる省略は合格として報告されます。無関係な PR では重いジョブが走らず、マージもブロックされません。新しい必須の集約ジョブを `main` のブランチ保護の規則に登録する作業は、リポジトリの外で行います。正確なチェック名を用いて、管理者が登録します。

このフィルタは、`bajutsu/` のデフォルトの扱いを反転しています（BE-0333）。以前は手書きのポジティブリストで、ファイルは「見てもらう」ためにリストへ加わる必要がありました。リストに載らないファイルは、黙って何も発火させませんでした。いまは共有のコアがパッケージ *全体* を一括して拾い、明示的に分類したものだけを差し引きます。差し引く分類は次の 2 つです。

| 分類 | 内容 |
|---|---|
| どのレーンも実行しない周辺（`_PERIPHERY_EXCLUSIONS`） | 各項目が除外の理由を持つ。対象は analytics / analysis スタック、MCP サーバ、AI アダプタ、GitHub と cloud の連携、混在パッケージ `agents` / `crawl` / `cli.commands` の個々の周辺モジュール |
| 各レーンが再主張する backend 固有のリーフ（`_LANE_CLAIMED`） | レーンごとに名指しするファイル |

どちらにも名指しされないファイルは一括で拾われ、誰かが分類するまで全 3 レーンを発火させます。この失敗の仕方は、無駄なジョブという安全側に倒れます。ポジティブリストが生んでいたのは、黙った過小発火でした。

反転したデフォルトは、ミスの一群をまとめて退治します。以前のリストでは、次のどの変更でもレーンが飛ばされえました。いまはパッケージとモジュールを一括の拾い上げで等しく照合するので、どれもレーンを発火させます。

| 以前に飛ばされえた変更 | 例 |
|---|---|
| トップレベルのモジュールをパッケージへ分割した変更 | `bajutsu/config` と `bajutsu/platform_lifecycle` はどちらもこのずれを起こした。レーンが動かすためにあるコードを変えても、レーン全体が飛ばされていた |
| 新しいトップレベルモジュールや CLI コマンド | 新しく足したどのファイルも、リストに載るまで拾われなかった |
| run パスが import しているのに、リストが一度も名指ししなかったファイル | 筆頭は、run ごとに書き込むマニフェストの書き出し `bajutsu/common/report/` |

分類の健全性は、いくつかのテストが保ちます。`test_run_path_closure_is_gated_or_excluded` は、run パスの静的な `ast` の import 閉包を辿ります。到達したファイルがゲート対象でも分類済みの周辺項目でもなければ、テストを落とします。run パスが *新たに* import し始めた未分類のモジュールは、`make check` で落ちます。数か月後に「謎めいた緑の必須チェック」として表面化することはありません。

別の 2 つのテストは、各除外項目と各素のパスをツリーへ解決します。テスト名は `test_periphery_exclusion_paths_exist` と `test_every_plain_literal_path_in_the_filter_exists` です。そのためリネームや削除は、何も照合しないパターンとして残らず、ゲートの失敗として現れます。

さらに共有のコアは、バックエンドごとの 2 つのディレクトリを、各レーンが名指しするリーフを除いて一括して拾います。2 つのディレクトリとは、`bajutsu/common/drivers/` と `bajutsu/platform_lifecycle/environments/` です。これを支えるテストが 2 つあります。1 つは、どちらの配下にも「1 つもレーンを発火させないファイル」がないことを確認します。もう 1 つは、各 `_LANE_CLAIMED` のリーフが少なくとも 1 つのレーンに再主張されることを確認します。新しく足したバックエンドのモジュールは、見落とされずに過剰に発火します。除外だけされて誰も主張しないリーフは、ゲートを落とします。

反転したデフォルトの過剰発火のコストは、出荷前に計測しました。直近 80 件のマージ済みプルリクエストでは、旧来のポジティブリストと 3 レーンすべてで同一に発火しました。計測には `scripts/e2e_overfire_report.py` を使いました。

`changes` ジョブは、安全だと証明できる唯一の場合について、もう一段だけ絞り込みます（BE-0322）。変更がレーンのシナリオファイルだけに閉じているときは、レーン全体を発火させません。発火させるのは、変更されたシナリオを宣言しているジョブだけです。フィルタは、各 iOS ジョブがワークフローで宣言している `scenarios:` を読みます。

`codegen` ジョブと `visual` ジョブは、シナリオをワークフロー入力ではなく Makefile ターゲットで名指ししています。そこでフィルタは、`demos/showcase/Makefile` のターゲットが走らせるシナリオも読みます（BE-0338）。こうして、変更されたシナリオからそのシナリオを読み込むジョブへの対応は、ジョブの実際の実行内容からずれません。フィルタは `relevant` に加えて、各ジョブが条件に使う `shared` フラグと `affected` ジョブ配列を出力します。Makefile から読んだ対応づけは、テストがそのターゲットに固定します。ターゲットがシナリオを増減させても対応づけが一緒に動かなければ、ゲートが落ちます。

それ以外の場合は、すべて従来どおりレーン全体を発火させます。該当するのは次の 3 つです。

| 場合 | 補足 |
|---|---|
| 共有コードの変更 | 任意のシナリオに影響しうるドライバ / ランナー / アプリ / ワークフローのコード |
| 次元ジョブ `conformance` | シナリオの部分集合を宣言せず、ドライバ適合性ハーネス全体を駆動する |
| ジョブがシナリオでキーづけられていないレーン | Android と web |

この判断はレーン全体の側に倒すので、変更が壊しえたジョブを取りこぼしません。読むのは `git` の差分、ワークフロー自身の宣言、Makefile ターゲットだけです。経路に LLM は入らず、実行の合否判定にも一切影響しません。

dev ツールは `dev` 依存グループにあります。そのため Linux ジョブは、`uv sync --group dev` → `uv run --no-sync …` の順で実行します。素の `uv run` は、デフォルトの集合に再同期して dev ツールを落としてしまいます。

このゲートは、[`make check`](../../Makefile) と [`pre-push`](../../.githooks/pre-push) フックを段ごとにミラーしています。`actionlint` と `gitleaks` を除けば、新規クローンでも `uv` だけで同一に走ります。この 2 つは CI が個別に導入するツールで、手元にないときは各段が通知を出して飛ばします。この同一性が、「ローカルで緑」＝「CI で緑」を担保します。スキルのずれの検査は例外ではなく、`uv` の側にあります。`apm-cli` は `dev` の依存なので（BE-0390）、`make lint-skills` にスキップの分岐はありません。

### すべてのジョブが宣言するタイムアウト

[`.github/workflows/`](../../.github/workflows/) のジョブは、すべて `timeout-minutes` を設定します。
GitHub が代わりに当てるデフォルトは 360 分だからです。そのまま止まったジョブは、赤いチェックが出るまで
ランナーを占有し、Actions の実行時間を消費し続けます。実際に web レーンでそれが起きました。
`conformance (playwright)` と `codegen (playwright)` は、成功するときには 2 分から 7 分で終わります。
それにもかかわらず、どちらも 4 時間近くでキャンセルされました。

上限がそのジョブ自身の見積もりから決まる場合は、独自の値を設定し、根拠を隣のコメントに書きます。
独自の値を持つジョブは次のとおりです。

| ジョブ | 上限 |
|---|---|
| AWS Device Farm への投入 | 180 分 |
| iOS のシナリオジョブ | 60 分 |
| Android の `smoke` | 30 分 |
| Android のその他のシナリオジョブ | 25 分 |
| `claude-review` | 20 分 |
| Android の Gradle キャッシュを温めるジョブ | 15 分 |
| `ai-smoke` の 2 つの実モデルジョブ | それぞれ 15 分 |
| `mcp-wire` | 10 分 |

そうした根拠を持たないジョブは、リポジトリのデフォルトである **30 分**を取ります。計測できた成功ジョブのうち、
もっとも長いのは CodeQL の Swift 解析の約 20 分です。30 分なら余裕をもって収まります。それでいて、
止まったジョブは 30 分以内に明確な失敗として現れます。

`timeout-minutes` を持たないジョブが 2 つだけあります。[`docs-refresh.yml`](../../.github/workflows/docs-refresh.yml)
と [`roadmap-refresh.yml`](../../.github/workflows/roadmap-refresh.yml) にある、再利用可能ワークフロー
[`refresh.yml`](../../.github/workflows/refresh.yml) を呼び出すジョブです。呼び出し側のジョブに
`timeout-minutes` を書くと、GitHub が受け付けないためです。代わりに `refresh.yml` 自身のジョブが持つ
30 分の上限が、両方の呼び出し側を覆います。

### E2E ジョブが失敗したときに集まる証跡（BE-0361、BE-0367）

E2E でもっとも厄介な失敗は、何もクラッシュしない失敗です。iOS では常駐 XCUITest ランナーが
`Timed out while requesting screenshot` を報告し、テストメソッドを終えます。これを受けて Python 側が
実行中クラッシュを宣言します。常駐 XCUITest ランナーとは、`bajutsu` が `xcodebuild test-without-building` で起動する
XCTest ホストのことです。`bajutsu` はこのホストを、ループバックの Hypertext Transfer
Protocol のチャネルで駆動します。Android の失敗はもう少し多様です。adb バックエンドが要素
ツリーを読む常駐 UI Automator サーバが応答しなくなる、エミュレータ自身の描画が固まる、ジョブが
`timeout-minutes` を使い切る、のいずれかです。どの場合もプロセスは1つも死んでいないので、クラッシュレポートの走査は何も見つけません。そして、デバイスの描画サービスが固まったのか、ホストがデバイスから資源を奪ったのかを
言い当てる状態は、人がジョブを開く頃には消えています。

両レーンとも、この問いに同じ形で答えます。何を見られるかによって3層に分かれます。第1層は `bajutsu`
の内側です。ストールがいつ起きたかを知っているのは、実行中のプロセスだけだからです。第2層は CI から
見たデバイスです。デバイスを保持しているものにしか読めません。第3層は時系列で見たホストです。ジョブと
並んで走るサンプラにしか記録できません。成果物は1つを除いてすべて `runs/diagnostics/` の下に置かれます。
この場所は各ジョブがすでにアップロードしています。例外は後述するシナリオ単位のクラッシュ証跡で、
失敗したシナリオ自身の証跡ディレクトリに置かれます。ジョブ全体ではなく、その1つのシナリオに属する
証跡だからです。集めたものが判定に入ることはありません。収集はファイルを書くだけで、合否は従来どおり
決定的なアサーションが決めます。

第1層は、両バックエンドとも1つの環境変数で有効になります。`BAJUTSU_STALL_DIAGNOSTICS` は捕捉の
書き込み先ディレクトリを指し、バックエンドの接頭辞をあえて持ちません。操作者が1つ設定すれば、ジョブが
どのバックエンドを駆動していてもフックが有効になります。一方、捕捉が何を**読む**かはバックエンドごとです。
両者が共有するコマンドは1つもないからです。Simulator は `simctl` に、macOS ホストは `vm_stat` に
答え、エミュレータは `adb` に、Linux ホストは `top` に答えます。そこで各バックエンドは自分のプローブ
一式だけを持ち寄り、共通の捕捉がそれ以外を引き受けます。オプトインの門、1回の捕捉あたりの実時間の
予算、トリガーごとに2回という回数、そして各プローブの結果を書き出すサマリです。上限をトリガーごとに
分けているのは、頻発する1つのトリガーが、説明したい対象であるクラッシュの届く前に共有の上限を
使い切ってしまわないようにするためです。頻発していたのは映像のトリガーでした。発火のもとになる確認
処理が誤った信号を見ていたため、このランナーでは緑の実行を含む全シナリオで発火していました。いまは
そうなりませんが、クラッシュを繰り返す実行では依然として複数のトリガーが競合するので、分けたままに
しています。この2レーンの外では未設定で、
未設定ならフックは何もしません。

#### iOS レーン

`codegen` を除き、Simulator を起動する iOS ジョブはすべて3層を `runs/` 配下へ集めます。`codegen` が
対象外なのは、このジョブが `.xcresult` だけをアップロードし、収集物を載せる `runs/` の成果物を
持たないからです。

**`bajutsu` の内側。** `BAJUTSU_XCUITEST_RESULT_BUNDLES` は、ランナーの起動ごとに
`-resultBundlePath` を与えます。こうして `runs/runner-logs/result-<udid>-<port>.xcresult` が、
testmanagerd 自身の観測を記録します。記録するのは、正確な XCTest の失敗とそのタイムスタンプです。
捕捉した標準出力が伝えるのは、その言い換えにすぎません。

同じ引数列で、`-collect-test-diagnostics never` も固定します。デフォルトの `on-failure` のままだと、
`xcodebuild` は Simulator の `system.logarchive` を result bundle へ埋め込みます。計測では、
1回の起動ぶんで 163 MB になりました。この埋め込みは、以下の狙いを絞った抽出が置き換えるはずの、
丸ごと収集そのものです。

ストール時の捕捉は、2 つの瞬間に発火します。チャネルが実行中クラッシュを宣言した瞬間と、
`recordVideo` が1バイトも生まなかった瞬間です。捕捉は次のものを
`runs/diagnostics/stalls/stall-NN-<理由>-<pid>/` へ書きます。

| 捕捉物 | 内容 |
|---|---|
| スクリーンショット | 所要時間を計測した `simctl` のスクリーンショット |
| `sample` | 描画を担うプロセスへの `sample` |
| スナップショット | `ps` と `vm_stat` のスナップショット |

**`bajutsu` の内側で、シナリオ単位、オプトインなし**（BE-0421）。クラッシュリカバリのリトライを
すべて使い切って失敗したシナリオには、`crash-diagnostics/` ディレクトリが作られます。置き場は、
そのシナリオ自身の証跡ディレクトリ `runs/<run_id>/<sid>/` の下です。中身は次の 2 つです。

| 中身 | 補足 |
|---|---|
| クラッシュしたランナーが捕捉した出力の末尾 | クラッシュ警告が折り込む20行よりはるかに多く、それでも上限つき |
| `.ips` クラッシュレポート | 障害を起こした `xcodebuild` プロセスについて、macOS がレポートを書いていた場合に限る |

シナリオの失敗文字列がこのディレクトリを名指しするので、存在を先に知っている必要はありません。

`crash-diagnostics/` は、次に挙げる走査のローカル実行版であり、シナリオ単位版でもあります。走査のほうは、
ジョブ全体のレポート置き場をまとめてアーカイブにします。そのため、複数のシナリオが走ったジョブでは、
どのクラッシュがどのシナリオのものかを言えません。しかも走査は CI でしか走りません。ローカルの
`bajutsu run` でクラッシュを再現しようとする開発者は、走査から何も得られませんでした。

どちらの証跡も best-effort です。`ReportCrash` が書かなかったレポートは、その項目が欠けるだけです。
`DiagnosticReports` ディレクトリをそもそも持たないホストでも同じで、何も失敗しません。

**テスト対象アプリ**のクラッシュは、原因の異なる別ディレクトリ `app-crash/` に入ります。`app-crash/` は、
run の途中でアプリが落ちたシナリオに対して書かれます。中身は、プラットフォーム自身がそのクラッシュに
対して書いたレポートです（[`docs/ja/evidence.md`](evidence.md#アプリのクラッシュ証跡be-0424)、BE-0424）。
2つが重なることはありません。`crash-diagnostics/` が名指すのはテスト基盤側の障害であり、
`app-crash/` が名指すのはテスト対象アプリ自体の不具合です。アプリクラッシュの経路は、意図して再試行を
行いません。

iOS と Android の両レーンには、`app-crash` ジョブがゲート対象外で追加されています。このジョブは、
要求に応じてクラッシュする showcase の仕掛けを動かします。位置づけは `actuation (xcuitest)` や
`golden (adb)` と同じです。実機で新たに配線したカバレッジは、必須の集約チェックへ昇格する前に
PR 単位のシグナルとして安定性を示します。これらのレーンで新しいシグナルがたどってきた道筋と同じです。

`crash-diagnostics/` がランナーの出力を集めるのは、ランナー自身の生存信号が名指しする 2 つの
クラッシュ形に限ります。1 つは `xcodebuild` が終了した場合です。もう 1 つは、捕捉した出力が終了を
示しているのに、プロセスが居残っている場合です。プロセスが動き続け、そうしたマーカーも書かないまま、
チャネルだけが応答しなくなる形もあります。この形は、BE-0354 がデバイス差し替えへ引き上げる
Simulator の固着です。この形では `crash-diagnostics/` は空のままになります。

**CI から見た Simulator と CoreSimulator。** この層は複合アクション
[`collect-ios-diagnostics`](../../.github/actions/collect-ios-diagnostics/action.yml) が担います。
Simulator を駆動する各ジョブが、このアクションを呼びます。
軽い段は毎回走り、次のものを集めます。

| 収集物 | 内容 |
|---|---|
| `CoreSimulator.log` | ファイルの末尾 |
| CoreSimulator のログディレクトリ | 起動済みデバイス自身のディレクトリ |
| クラッシュレポートの走査 | `.ips` / `.crash` の先へ広げ、この失敗が実際に残すハング、スピン、jetsam のレポートまで含む |
| ホストのスナップショット | `sw_vers`、`sysctl`、`xcodebuild -version`、`simctl list`。実行をまたいで、失敗をランナーイメージとハードウェア世代に結びつける |

重い段は、そのジョブの実行ステップ自身が失敗したときだけ走ります。中身は `xcrun simctl diagnose` と、
対象を絞った2つの unified log の抽出です。

**時系列で見たホスト。** 同じアクションの `start` フェーズが担います。バックグラウンドのサンプラが
20秒ごとに `top`、`vm_stat`、`memory_pressure` を `runs/diagnostics/host-telemetry.log` へ
追記します。一発の描画プローブが、スクリーンショットに何秒かかるか、5秒の `recordVideo` が
そもそも1バイトでも生むかを記録します。サンプラはあらゆる実行ループの外にいる観測者なので、
その間隔は採取の周期であって、判定が依存する待ち合わせではありません。各サンプルには、プロセスの表が
2つ入ります。

**常駐サイズの順**：1回だけ採取する `top` の表です。1回の採取には差分を取る区間がありません。
そのため全プロセスが、Central Processing Unit（CPU）の使用率を 0.0％ と報告します。CPU で並べると
定数で並べることになります。最初の収集では、CPU 順に残った行に試験対象のアプリが一度も入りませんでした。

**直近1秒の CPU の順**：2回採取する `top` の表です。メモリは小さくても CPU を使うプロセスは、
1つ目の表に入りません。ホストの負荷の出どころは、この表でわかります。

**ホストの負荷を下げる工夫。** macOS のランナーは3コアです。`actuation` ジョブでは、成功した回も
含めて、ホストの CPU の空きがジョブを通して0％でした。次の4つの負荷源を減らします。

`setup-ios-toolchain` が、シミュレータの起動前に Spotlight の索引作成を止めます。索引を作る
`mds_stores` がシミュレータと CPU を取り合っていました。ジョブの中で索引を検索する処理はありません。

すべての `deviceLog` の取得を、デバッグではなく info レベルで流します
（[evidence](evidence.md)）。

`actuation` ジョブは、シナリオの `deviceLog` の取得を、アプリと SpringBoard、`runningboardd` に
絞ります。絞り込みの条件は `bajutsu-e2e` の `log-predicate` 入力で渡します。絞らない場合、`navigation.yaml` だけで80秒に
455,715行を流し、その3分の2は1つのシステムデーモンの出力でした。ほかの iOS ジョブは、引き続き
全プロセスのログを取得します。

`run` と `actuation` のジョブが、どのシナリオも使わないシミュレータのサービスを止めます。
止めるのは [`reduce-simulator-services`](../../.github/actions/reduce-simulator-services/action.yml)
アクションです（[BE-0517](../../roadmaps/BE-0517-ios-ci-simulator-service-reduction/BE-0517-ios-ci-simulator-service-reduction-ja.md)）。
アクションは起動後に、一覧の各ラベルに `launchctl disable` を実行してからシミュレータを再起動します。
一覧はラベルをカテゴリに分け、カテゴリごとに止めてよい理由を書いています。各ジョブは開始時と終了時に、
止めたサービスの数とシミュレータのプロセス数をログに出します。手動で実行するときは
`simulator-services: stock` を選べます。この場合は何も止めずに再起動だけを行います。
手動で実行するときは `lanes: measure` も選べます。この場合は build、`run`、`actuation` のジョブだけが
走ります。実行ごとに別の concurrency group を使うので、同時に走る 2 つの実行が互いをキャンセルしません。

**何も走る前のホスト。** これも `start` が担います。上限付きの `ps aux` と `vm_stat` を一度だけ
`runs/diagnostics/ps-baseline.txt` へ書きます。ストール時の捕捉が撮る `ps` は、壊れた時点で何が
常駐していたかには答えますが、それが平常と違うのかには答えられません。最初の収集では、この問いを
計測ではなく起動環境の引数から判断せざるをえませんでした。これと描画プローブはジョブごとに一度だけ
書くので、ジョブが2度目の `start` を呼んでも、実行前の値が実行中の値で上書きされることはありません。

**合成したタッチごとに、実行中のログから**：これも `start` で開始し、成功したジョブでも残します。
[`scripts/synthesis_stream_record.py`](../../scripts/synthesis_stream_record.py) がゲストの
`log stream` を購読します。対象は、runner が `bajutsu.runner` に書くマーカーと、`backboardd` および
`testmanagerd` の行です。各イベントはホストに届いた時刻を付けて
`runs/diagnostics/synthesis-stream.ndjson` へ書き、`collect` で記録を止めます。デバイスの再起動などで
ジョブの途中にストリームが終わったときは、購読をやり直します。購読の開始と終了には `meta` レコードを
書きます。そのため、空の記録や途中で切れた記録は、何も起きなかった実行とは区別され、集計で警告として現れます。

ジョブの後に `log show` で取り出したログには、到着時刻がありません。そのため、マーカーがホストに届くまでの
時間はわかりません。`backboardd` の行が、自分より後の時刻の runner の行より遅れて届くことがあるかどうかも
わかりません。この 2 つは、
[BE-0462](../../roadmaps/BE-0462-ios-dropped-touch-retry/BE-0462-ios-dropped-touch-retry-ja.md) の
タッチ取りこぼし監視が必要とする値です。成果物をダウンロードして
[`scripts/synthesis_stream_report.py`](../../scripts/synthesis_stream_report.py) にかけると、次のものが得られます。

| 得られる値 | 内容 |
|---|---|
| 到着遅延 | パーセンタイル |
| 到着順の逆転 | 時刻と到着の順が食い違った行 |
| 取りこぼしの対応 | 各取りこぼし行と、その行を含むタッチの区間の対応 |

記録は観測するだけで、何も判定しません。

unified log の抽出を2つに分けているのには理由があります。自然な想定はむしろ逆なので、ここに
書いておきます。スクリーンショットを担うゲストプロセス、すなわち `SpringBoard`、`backboardd`、
`testmanagerd` は、ホストの unified log に**書き込みません**。起動済みデバイスで
実測したところ、これらのプロセス名で絞ったホスト側の `log show` は、見出し行だけを返しました。
ゲストのエントリはデバイス自身のログストアにあるため、アクションは `simctl spawn` を通して
ゲストの内側で2つ目の `log show` を走らせます。ホスト側の抽出は、実際にそこへ書き込む
CoreSimulator のサービスプロセス向けに残しています。ホスト側の抽出1つで済ませていたら、空のファイルが
できていました。この収集が置き換えた、構造上必ず空になる成果物と同じものです。

#### Android レーン

同じ3層を集めるのは、Android Virtual Device（AVD）を起動するジョブすべてです。`uiautomator
(codegen)` も含みます。むしろこのジョブこそ収集が効きます。このジョブがアップロードする経路は、
どれも Gradle が生成テストを**実行して**初めて生まれるからです。その手前で落ちる原因は、AVD の boot の
固まり、codegen やビルドの失敗、ジョブ自身の `timeout-minutes` の発火です。いずれの場合も
`if-no-files-found: ignore` が成果物を黙って捨てていました。自分のテストの外側で落ちたジョブは、1つも
アップロードできていなかったのです。ただし `bajutsu run` を回す6ジョブと違い、このジョブはテスト時に
adb ドライバを走らせないので、階層読み取りのトリガーはここでは発火しません。第1層のフックは、ホスト側の
レコーダ（[`screenrecord.py`](../../demos/showcase/android/screenrecord.py)）が録画のバイト増加を
確認する経路です。描画が固まった状態、つまりこのレーンの既知のフレークでは、この確認が通りません。

**`bajutsu` の内側。** ストール時の捕捉の発火点は2つだけで、どちらも意図的に絞ってあります。
1つめは常駐チャネルの階層読み取りのフォールバックで、ドライバがチャネルを諦めてリース残りを
`uiautomator dump` のサブプロセスに切り替える地点です。読み取りチャネルが一時的に不安定なので
はなく、失われたことを意味する地点にあたります。2つめは、デバイス側の `screenrecord` が実際に
バイトを吐いているかを見る確認です。動いているだけでは足りません。この確認が、描画が固まった状態と、
そもそも録画が始まっていない状態とを切り分けます。どちらの瞬間も、捕捉は
`runs/diagnostics/stalls/stall-NN-<理由>-<pid>/` へ次のものを書きます。

| 捕捉物 | 内容 |
|---|---|
| スナップショット | ホストの `ps` と `top` のスナップショット |
| `dumpsys SurfaceFlinger --latency` | 描画の遅延 |
| `logcat` | 末尾 |
アクチュエーションの経路は意図的に
除いてあります。常駐チャネルのエラーのうち2つは、まったく健全な実行でも発火するからです。
アクチュエーション用のエンドポイントを持たない古いサーバと、二重に操作しないようドライバが
「届いた」と扱う応答の喪失の2つです。ここで捕捉すると、本当のストールが使うべきトリガーごとの
上限を先に使い切ってしまいます。

**CI から見たエミュレータ。**
[`scripts/collect_android_diagnostics.sh`](../../scripts/collect_android_diagnostics.sh) が
担います。この半分が複合アクションではなくスクリプトなのは、Android レーンが iOS レーンから
もっとも鋭く分かれる制約のためです。このレーンでデバイスに触るステップは、すべて
`reactivecircus/android-emulator-runner` ステップの**内側**で走ります。このステップは AVD を
起動し、`script:` を実行し、終わりに AVD を落とします。したがって、その後ろに置いた通常の
ステップは、デバイスが1台も繋がっていない状態で走り、何も集められません。ステップレベルの
`if: failure()` による制御も同じです。そこで掃き出しは各ジョブ自身の `script:` の末尾から呼び、実行コマンド
自身の終了コードで段を切り替えます。この形は、このレーンの `poll_cpuinfo` ポーラが失敗する実行を
生き延びるためにすでに使っている形と同じです。軽い段は毎回走ります。集めるのは、全バッファの `logcat -d` ダンプと環境スナップショットです。
環境スナップショットは `dumpsys meminfo`、`getprop`、`adb devices -l` で取ります。
環境スナップショットは、失敗した実行を API レベルと Application Binary Interface（ABI）に結びつけます。
全バッファのダンプが読むのは、デバイス自身が保持するリングバッファです。シナリオごとの `deviceLog`
インターバル証拠とは読む先が違います。そのため、どのシナリオの取得も始まる前に落ちたジョブについても、
示せるものが残ります。

重い段は、実行自身が失敗したときだけ走ります。集めるものは次の 3 つです。

| 収集物 | 内容 |
|---|---|
| `adb bugreport` | Android 自身の総合収集ツールの出力 |
| `/data/tombstones` | root 権限で取るネイティブクラッシュレポート |
| `/data/anr/` | Application Not Responding（ANR）トレース |

この2種類のクラッシュレポートは、iOS ならホスト自身の診断レポートから、何もしなくても入手できます。
Android では、デバイス側にあります。

掃き出しは、コマンドごとだけでなく掃き出し全体にも上限を持ちます。理由は、プロセス内の取得が
プローブごとのタイムアウトの上にさらに予算を持つのと同じです。この掃き出しが記録しようとしている
デバイスの固着では、`adb` の読み取りはどれも上限いっぱいまで固まります。そのためコマンドごとの
上限だけでは、収集がジョブの `timeout-minutes` の大半を使い切ってしまいます。報告できたはずの失敗が、
途中で切れた収集物だけを抱えたキャンセル済みジョブに変わります。期限で打ち切った読み取りは、
その旨を自分のファイルに書き残します。「予算を使い切った」と「この読み取りは何も見つけなかった」は、
デバイスについて別のことを述べているからです。

**時系列で見たホスト。** 複合アクション
[`collect-android-diagnostics`](../../.github/actions/collect-android-diagnostics/action.yml) が
担います。各ジョブのエミュレータステップの前に置く `start` フェーズが、バックグラウンドのサンプラを
起動します。サンプラは `top` と `free` の出力を約20秒ごとに
`runs/diagnostics/host-telemetry.log` へ追記します。後ろに置く `collect` フェーズが、その
サンプラを止めます。読むのは Linux ランナーだけなので、この層はステップとして成立します。そして
エミュレータステップを外側から挟むからこそ、エミュレータステップごと落ちたジョブもカバーできます。

このホストのサンプラは、レーンの既存の `poll_cpuinfo` 入力を置き換えず、並存します。両者は別の側から
別のものを測っているからです。`poll_cpuinfo` は adb 越しにゲスト側の見え方を読み、サンプラはホスト
自身の負荷を読みます。この違いは、`poll_cpuinfo` がオプトインであるのに対してサンプラが常時オンで
ある理由でもあります。ホスト側で20秒ごとに取るサンプルは adb のトラフィックを一切発生させず、頻度は
10分の1です。`poll_cpuinfo` をデフォルトで無効にしている観測者効果を持ちません。

2つのレーンが複合アクションを分けたままなのは意図的です。`collect-ios-diagnostics` は `simctl`、
CoreSimulator、macOS の unified log を読みます。`collect-android-diagnostics` が読むのは Linux
ホストです。
足回りのツールに共通の面はないので、1つのアクションにまとめても、共通のロジックではなくバックエンド
での分岐になります。両者が共有しているのは設計、すなわち軽い段と重い段の分割、`start` と `collect` の
テレメトリのフェーズです。そして `bajutsu` の内側では、捕捉そのものを共有しています。

**赤くなったレーンが集めた証跡の読み方。** ここまでの証跡は `gh run view --log-failed` には現れません。
失敗したジョブ自身の `runs/` アーティファクトの中にアップロードされます。そのため、ログだけで
診断するセッションからは見えないままです。

エージェントスキル
[`investigate-ci-failure`](../../.apm/skills/investigate-ci-failure/SKILL.md) は、そのアーティファクト
を取得します。そして中身を
[`known-ci-failure-patterns.md`](../../.apm/skills/investigate-ci-failure/references/known-ci-failure-patterns.md)
に記録された症状と突き合わせます。そのうえで、失敗を既知のインフラ由来の flaky、実際の欠陥、未分類の
いずれかに分類します。[`pr-followup`](../../.apm/skills/pr-followup/SKILL.md) は修正に手をつける前に
これを実行します。ホスト由来の flaky にコード修正で答える事態を避け、回帰を flaky として見送る事態も
避けるためです。このスキルは報告するだけで、編集や push や再実行はしません。
### 不安定なレーンを実行をまたいで計測する

ここまでの収集は、1 つの失敗したジョブを説明します。しかし、繰り返し失敗するジョブに共通する要因は
示せません。どの証拠も、1 つのジョブの artifact に入った自由形式のテキストだからです。ジョブごとの
計測記録がこの隙間を埋めます。計測記録は実行をまたいで証拠をそろえ、失敗したジョブと成功したジョブを
比べられるようにします。

**各ジョブが書くもの。** 各レーンの診断アクションの `collect` フェーズは、最後に
[`scripts/ci_telemetry.py`](../../scripts/ci_telemetry.py) の `summarize` を実行します。成功したジョブ
でも失敗したジョブでも実行します。このステップはジョブがすでに書いたファイルを読み直すだけで、デバイスや
ネットワークは呼ばないので、1 秒もかかりません。書き出すのは
`runs/diagnostics/ci-telemetry/ci-telemetry.json` と、すべての run の manifest の写しです。アクションは
このディレクトリを `ci-telemetry-<job>-<attempt>-<接尾辞>` という名前の小さな独立した artifact として
アップロードします。そのため、実行をまたぐ集計で動画をダウンロードすることはありません。記録は
4 種類の情報を持ちます。

- ジョブを並べてそろえるキー（ワークフロー、ジョブ、run の ID、試行回数、runner 名、runner image と
  そのバージョン）
- ホストの情報（OS のバージョン、ハードウェアのモデル、CPU 数、メモリー量、iOS では Xcode のバージョン）
- 計測値（`host-telemetry.log` から解析したホストの負荷、描画プローブ、トリガーごとに数えた
  stall capture）
- 各シナリオの判定、失敗したアクション、所要時間、最初の `wait`、最長の `wait`

両方のホストサンプラーは、2 回分のサンプルを取る `top` から CPU の行を 1 行追加し、`Interval` という
接頭辞を付けます。1 回だけの `top` が報告する CPU 時間は起動以降の平均です。その時点でホストがどれだけ
空いていたかを示すのは、区間を測った行だけです。

**要因の候補。** 兆候があれば、ジョブの成否を問わず次のラベルを付けます。ラベルは
計測値に対する固定の閾値の規則です。どこを見るべきかを示すヒントであり、判定には一切触れません。

| 要因の候補 | 兆候と閾値 |
|---|---|
| `host-resources` | CPU 1 個あたりのロードが 1.5 超、区間で測ったアイドル率が 5％ 未満、空きメモリーが 10％ 未満、I/O 待ちが 20％ 超、またはハイパーバイザーによる steal が 10％ 超 |
| `device-slow-or-wedged` | デバイスの起動や待機のステップが 240 秒超、実行前のスクリーンショットが止まったか 10 秒超、または `runner-crash` の stall capture |
| `channel-timeout` | ジョブのログにある runner channel のリトライか、実行中の channel のクラッシュ |
| `video-start` | ログにある録画開始の未確認、描画プローブの録画が 0 バイト、または `video-no-bytes` か `screenrecord-no-growth` の stall capture |
| `network-or-deps` | ログにあるネットワークエラー、またはセットアップ、インストール、ダウンロードだけを行うステップの失敗 |
| `runner-contention` | 300 秒を超えてキューで待ったジョブ |
| `app-wait-timeout` | 上のどの兆候もない、失敗した `wait` ステップ |
| `test-assertion` | 上のどの兆候もない、それ以外の失敗したステップ |

最後の 2 つは残余のラベルです。インフラの兆候を伴わない失敗には、本当の不具合や短すぎる待機が
現れます。ただし、ログを読めなかった失敗ジョブには、代わりに `log-signals-unknown` を付けます。
ログの兆候を確かめていないからです。時間帯と runner image はラベルにしません。代わりに、報告がそれぞれを比較の軸として扱います。

**分析の手順。** 以下の手順は、GitHub のコマンドラインツール `gh` がリポジトリにサインインした
手元の環境で実行します。

**手順 1：直近の実行を取得する。**

あるワークフローの直近の実行を取得します。コマンドは完了した直近 30 回の実行を一覧し、それぞれの
計測記録の artifact をダウンロードします。各記録を、API から得たジョブの結論、キュー待ち時間、
ステップの所要時間と結合します。各ジョブのログからは、次の 6 種類の警告を数えます。

| 警告 | 数える対象 |
|---|---|
| channel のリトライ | runner channel の再試行 |
| channel のクラッシュ | 再試行の上限を超えて runner に届かなくなった回数 |
| 録画開始の未確認 | 開始を確認できなかった録画 |
| 復旧の再起動 | runner の起動に失敗したあとのシミュレータの再起動 |
| デバイスの交換 | 復旧できずに新しいシミュレータへ切り替えた回数 |
| ネットワークエラー | 名前解決の失敗や接続のリセットなど |

```bash
python3 scripts/ci_telemetry.py fetch --workflow ios-e2e.yml --limit 30 --out tmp/ci-telemetry
```

コマンドは `N job record(s) → tmp/ci-telemetry/records.jsonl` と出力します。計測記録の artifact
を持たないジョブにも API だけから記録を作るので、早い段階で止まったジョブも件数に残ります。
キャンセルされたジョブは除きます。E2E のワークフローは新しい push が来ると古い実行をキャンセル
するので、その実行はコードとマシンのどちらについても何も示さないからです。`timeout-minutes` で
打ち切られたジョブもキャンセルとして報告されます。コマンドはジョブに付くタイムアウトの注記で
両者を見分け、打ち切られたジョブは失敗として数えます。

**手順 2：報告を出力する。**

報告は次のコマンドで出力します。

```bash
python3 scripts/ci_telemetry.py report tmp/ci-telemetry
```

報告には 4 つの表があります。1 つ目は、要因の候補ごとの、失敗したジョブと成功したジョブでの件数
です。2 つ目は、指標ごとの、失敗したジョブと成功したジョブでの中央値です。3 つ目は、ジョブ、
runner image、OS、Xcode のバージョン、ハードウェア、4 時間ごとの時間帯ごとの失敗率です。最後は、
ラベルを 1 つも持たない失敗したジョブの一覧です。スクリプトで扱うときは `--json` を付けます。

**手順 3：シナリオを順位付けする。**

シナリオそのものを順位付けします。取得結果にはすべての manifest が入っており、
`bajutsu flakiness` がそのまま読めます。

```bash
uv run bajutsu flakiness --history tmp/ci-telemetry/manifests
```

**報告の読み方。** 直す価値のある要因とは、失敗したジョブでの件数が多く、成功したジョブでの件数が
少ない要因です。成功したジョブでも同じくらい現れる要因は、失敗の説明になりません。特定の runner image
やハードウェアのモデルで失敗率が跳ね上がるなら、疑うべきはコードではなくホストです。ラベルが 1 つも
付かなかった失敗したジョブは、
[`investigate-ci-failure`](../../.apm/skills/investigate-ci-failure/SKILL.md) に回します。このスキルは、
そのジョブの `runs/` artifact 全体を読みます。

### 止めたシミュレータのサービスを残すかを決める

`run` と `actuation` のジョブは、どのシナリオも使わないシミュレータのサービスを止めています
（[BE-0517](../../roadmaps/BE-0517-ios-ci-simulator-service-reduction/BE-0517-ios-ci-simulator-service-reduction-ja.md)）。
この変更を残す基準は 4 つあり、ロードマップ項目が計測の前に固定しました。`adoption` サブコマンドは、
手動実行の 2 つの群にその基準を当てはめます。

| 群 | シミュレータの状態 |
|---|---|
| reduced | 一覧のサービスを止めた状態 |
| stock | 同じ再起動を経て、すべてのサービスが動いている状態 |

手順は 3 段階です。最初に、`main` で各群を 10 回ずつ実行します。2 つの群は同じ時間帯で交互に走らせます。
最初の実行の時刻を控えておきます。報告はその時刻より古い実行を数えません。

```bash
gh workflow run ios-e2e.yml --ref main -f lanes=measure -f simulator-services=reduced
```

```bash
gh workflow run ios-e2e.yml --ref main -f lanes=measure -f simulator-services=stock
```

次に、20 回の実行がすべて終わってから結果を取得します。`--event` を付けると、同じワークフローの
pull request の実行が一覧から外れます。

```bash
python3 scripts/ci_telemetry.py fetch --workflow ios-e2e.yml --event workflow_dispatch --limit 30 --out tmp/be-0517
```

最後に、基準を当てはめます。

```bash
python3 scripts/ci_telemetry.py adoption tmp/be-0517 --since 2026-12-01T00:00:00Z
```

出力の 1 行目に判定が出ます。その下の表に、群ごとの件数と基準ごとの結果が並びます。

サブコマンドは、ジョブのログに出たモードでそのジョブの群を決めます。数えるのは、`main` の手動実行が起動した
`run` と `actuation` のジョブのうち、`--since` 以降に始まり、ログを読めたものだけです。判定は次の 4 つのいずれかになります。

| 判定 | 意味 |
|---|---|
| `adopt` | すべての基準を満たした |
| `reject` | 満たさない基準が 1 つ以上ある |
| `trace` | 件数の基準は満たしたが、変更を入れた側で通らなかったジョブがある |
| `incomplete` | どちらかの群のジョブがちょうど 20 件ではない |

`trace` の判定は人の確認を要します。一覧に出たシナリオを、疑わしいサービスを動かしたまま再実行します。
そこで通れば、その結果は変更に不利な証拠として数えます。報告には、ログに群が出ていない `run` と
`actuation` のジョブも並びます。ログを読めなかったジョブと、サービスを止めるステップが群を出力する前に
止まったジョブです。そうしたジョブはどちらの群にも入らないので、群が 20 件に届かないことがあります。

## あなたのアプリの CI で回す

> bajutsu はプレリリース（未公開）です。PyPI 公開までは vendor（submodule / checkout）して、その
> checkout からアクションを実行してください。アクションは bajutsu の `pyproject.toml` に対して
> `uv sync` を実行します。

bajutsu は CI 向けの出力を生成します。出力は次の 4 つです。

| 出力 | 内容 |
|---|---|
| `junit.xml` | JUnit 形式のテスト結果 |
| `report.html` | 自己完結のレポート |
| 終了コード | `0` または `1` |
| 失敗**アノテーション**とジョブ**サマリ** | Actions 内で実行したときに限る |

macOS ランナーでの手順は 2 段階です。

**手順 1：アプリをビルドしてインストールする。** 起動済みのシミュレータに、**アプリと XCUITest ランナーをビルドしてインストール**します。
この手順はアプリごとに異なるので、アプリの開発者が担当します。使うコマンドは `xcodebuild` と `xcrun simctl install` です。

**手順 2：bajutsu を実行する。** [`bajutsu-e2e`](../../.github/actions/bajutsu-e2e/action.yml) composite action で **bajutsu を実行**します。
このアクションは、依存の同期、シナリオの実行、成果物のアップロードを行います。シナリオの実行には `run --score` を付けるので、
ログに入口画面の規約グレードが残ります。アップロードする成果物は、report、スクリーンショット、動画、`network.json` です。

XCUITest バックエンドは、pip の extra を必要としません。ランナーはループバックの Hypertext Transfer Protocol 越しに駆動し、
`xcodebuild` はランナー上の Xcode に付属するためです。

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

`GITHUB_ACTIONS` がセットされていると、`bajutsu run` は失敗シナリオごとに `::error::` アノテーションを出します。
アノテーションは PR にインラインで表示されます。さらに `$GITHUB_STEP_SUMMARY` へ、合否の表を追記します。
フラグは要らず、Actions 環境を自動で検出します。

### 補足

**JUnit**：`junit.xml` はレポートの隣に書き出されます。`junit.xml` を test-reporter 系アクション（例 `dorny/test-reporter`）に渡すと、テスト結果をインライン表示できます。

**決定性**：シナリオの [`mocks`](../network.md#deterministic-mocks) で通信をスタブし、ライブサーバへの依存をなくします。

**規約スコア**：composite action は `run --score` を渡します。そのため run は、入口画面の規約グレードを stderr に出力します。グレードは `Ready` / `Partial` / `Blocked` のいずれかです。run が実行時の最初の起動から算出し、`doctor` が報告するスコアと同じものです。

この出力は診断だけを目的としており、合否の判定には一切関与しません。スコアを run に畳み込むことで、別立ての `doctor` ステップを避けられます。別立てにすると、2 つめの XCUITest ランナーをコールド起動することになります。

実行可能性や capability のチェックを含む、より広いプリフライトを行うには、ローカルで `bajutsu doctor` を実行してください。CI での env や権限の実行可能性ゲートは、今後の課題です。このゲートは、`xcodebuild` / Xcode の存在チェックにあたります。

**影響ステップの選択**：[`bajutsu impact`](cli.md#impact) は、`git` の diff がどのシナリオステップに影響しうるかを報告します。パイプラインは、素早いフィードバックのためにそれらのステップを先に並べられます。安全なデフォルトは加算的です。スイート全体は流したまま、影響のあるものを先に置くだけです。

マージ前の run を影響集合まで絞り込むのは opt-in です。絞り込みが健全なのは、`impact` が示す次の 2 つの安全策があるときに限ります。

| 安全策 | 内容 |
|---|---|
| フルスイートへの退避 | レポートが**不完全**なときはフルスイートへ退避する。不完全とは、`--json` の `complete` が `false` のとき、すなわち diff がどの参照 id にも対応しない変更を含むとき |
| 粗い cadence での全体実行 | 決定的なスイート全体を、マージ時、夜間、リリース時のような粗い cadence で依然として流す |

合否の判定は常に `run` に残ります。

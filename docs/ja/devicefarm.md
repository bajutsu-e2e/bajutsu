# AWS Device Farm で実行する

[AWS Device Farm](https://docs.aws.amazon.com/devicefarm/latest/developerguide/welcome.html) は、Amazon がホストする実機と仮想デバイスに対してテストを実行するデバイスクラウドです。Bajutsu は Device Farm の**カスタムテスト環境**を通じてここで動作し、予約されたデバイスに対して Android（adb）バックエンドを駆動します。決定的なコアには変更を加えません。

## バッチモデルと、ドライバーではなくサブミッターである理由

Device Farm はライブデバイスのプロバイダーではなく、**バッチ**サービスです。ネットワーク越しに操作するデバイスを貸し出すのではなく、Device Farm 側のホスト上で利用者のコマンドを実行します。そのホストには、予約されたデバイスに接続された `adb` がすでに用意されています。したがって、ここで提供するのは実行時のドライバー（取得すべきデバイスは存在しません）ではなく、**CI 側のサブミッター**です。Bajutsu とシナリオをパッケージ化し、`bajutsu run --backend adb` を実行するテスト仕様とともにアップロードして、Device Farm にホスト上で実行させ、成果物を回収します。

サブミッターの中核は決定的なコアの外側、[`bajutsu/cloud/devicefarm.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/bajutsu/cloud/devicefarm.py)（`render_test_spec` / `build_package` / `submit_and_collect` と `DeviceFarmClient` / `Transfer` の seam、BE-0336）にすべて収まっており、他の呼び出し元からも再利用できます。[`scripts/devicefarm_submit.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/scripts/devicefarm_submit.py) は、引数解析と実際の boto3 / urllib アダプタだけを持つ薄いラッパーになりました。アップロード、ポーリング、ダウンロードのしくみは、`run` や CI の判定経路に一切触れません。Bajutsu はローカルと同じように Device Farm の内側で動作し、同じ決定的なコアと、機械的に検査できるアサーションによる同じ合否を用います。そのため、サブミッターが報告する判定は **Bajutsu 自身の `manifest.json`** に由来し、Device Farm 独自の PASSED / FAILED 分類に依存しません。

## バッチトポロジーの注意点

以下は Device Farm 自体の性質です。将来 Device Farm 側が変わってもフローが気付かないうちに壊れないよう、サブミッターはこれらを文書化して扱います。

- **生の adb は副産物であって保証ではありません。** Device Farm がカスタム環境で第一級に扱う経路は Appium です。予約されたデバイスに対する `adb` は、ホストのツールチェーンに含まれるために利用できるのであって、Amazon が約束する契約ではありません。Bajutsu の Android バックエンドが必要とするのは「デバイスに接続された `adb` を備えたホスト」だけで、カスタム環境はそれを提供しますが、将来のプラットフォーム変更で失われうる副産物として扱ってください。
- **150 分のハードキャップ。** カスタム環境の 1 回の実行は 150 分が上限です。サブミッターはそれを超えてポーリングせず、CI ジョブを無限にブロックするのではなく、明示的に失敗させます。
- **APK のみ。** Device Farm の Android アプリのアップロードは `.apk` を受け付け、`.aab` は受け付けません。実行にはデバッグ APK をビルドしてください。
- **Appium コマンドごとのタイムアウト。** Device Farm の Appium コマンドごとのタイムアウトは、Bajutsu が用いる生の adb 経路には適用されません。実効的な上限は 150 分の実行キャップです。

Device Farm の iOS は実機で動作するため、下記の [iOS: 再署名と実機のケーパビリティ](#ios-再署名と実機のケーパビリティ) の 2 つの制約が加わります。

## iOS: 再署名と実機のケーパビリティ

実機で動作させることにより、Bajutsu が前もって織り込む点が 2 つあります（BE-0238）。いずれも Device Farm の分類ではなく物理デバイスそのものの性質なので、XCUITest バックエンドが駆動する実機（`xcuitest.deviceType: device`）であれば、Device Farm でもローカル接続のデバイスでも同じように当てはまります。

- **再署名でエンタイトルメントが剥がれます。** Device Farm はアップロードされた `.ipa` を自前のプロビジョニングプロファイルで再署名し、予約されたデバイスにインストールできるようにします。この再署名では、新しいプロファイルが持たないエンタイトルメント（多くは Push（`aps-environment`）と App Groups（`com.apple.security.application-groups`））が落ちます。剥がれたエンタイトルメントに依存するアプリの機能（リモートプッシュの登録、App Group の共有コンテナ）は再署名後のビルドでは動作しないため、そうした機能をアサートするシナリオは、App Store 版ではなく再署名後の挙動を前提にしてください。
- **simctl のデバイス制御と権限付与は適用されません。** Bajutsu の iOS デバイス制御（`setLocation`、クリップボード系のステップ、`push`、`clearKeychain`、`background` / `foreground`、ステータスバーの上書き）と権限付与は、いずれも `simctl` に支えられています。`simctl` が届くのはシミュレータだけで、物理デバイスには届きません。そのため実機では XCUITest バックエンドはこれらを宣言せず、いずれかを使うシナリオは、デバイス操作を始める前に **preflight でスキップ**され（BE-0082）、実行の途中で `simctl` エラーとして遅れて失敗する代わりに、明確な理由とともに弾かれます。XCTest ランナー自身が駆動する実機側のケーパビリティ（query、elements、スクリーンショット、タップ、2 本指ジェスチャー）は影響を受けません。

## テスト仕様

Device Farm は[カスタム環境のテスト仕様](https://docs.aws.amazon.com/devicefarm/latest/developerguide/custom-test-environment-test-spec.html)から実行を駆動します。これは `install`、`pre_test`、`test`、`post_test` の各フェーズにシェルコマンドを記した YAML ファイルです。サブミッターは、次のような仕様を生成します。

1. **install**：uv で Python 3.13 を用意し、アップロードしたテストパッケージから Bajutsu をその venv へインストールします。Device Farm のホストは Python が最大 3.12 まで（`devicefarm-cli use python` は Amazon があらかじめ用意したランタイムしか選べません）で、Bajutsu は 3.13 を要求するため、ホストの標準 pip で uv を入れ、uv にスタンドアロンの 3.13 を取得させて、その venv へインストールします。これは暫定的な回避策で、Device Farm が 3.13 を提供したら取り除きます（サブミッターの `_python_bootstrap_commands` を参照）。adb バックエンドはサブプロセスだけで動くため、追加の extra を入れないベースのインストールで足ります。
2. **pre_test**：`adb devices` を実行し、予約されたデバイスが見えていること（シリアル解決の確認）を示します。
3. **test**：シナリオごとに 1 回ずつ `bajutsu run --backend adb --udid booted` を実行します。あるシナリオが失敗しても、残りのシナリオの manifest は残ります。
4. **post_test**：`runs/` ツリー全体を `$DEVICEFARM_LOG_DIR` にコピーし、成果物が回収できるようにします。

showcase の 2 つのシナリオ向けにコミット済みの参照用仕様が [`demos/showcase/devicefarm/testspec.yml`](https://github.com/bajutsu-e2e/bajutsu/blob/main/demos/showcase/devicefarm/testspec.yml) にあります。

## サブミッターの使い方

オプションの extra をインストールします。

```bash
uv sync --extra aws        # boto3 を導入します
```

AWS の認証情報なしで、パッケージと仕様をローカルにビルドできます（アップロードされる内容を確認するのに便利です）。

```bash
uv run python scripts/devicefarm_submit.py \
  --scenario scenarios/firstlook.yaml --scenario scenarios/controls.yaml \
  --target showcase-compose --config showcase.devicefarm.config.yaml \
  --app-apk app-debug.apk \
  --package .=. \
  --package demos/showcase/devicefarm/showcase.devicefarm.config.yaml=showcase.devicefarm.config.yaml \
  --package demos/showcase/scenarios=scenarios \
  --package-only
```

実行には `showcase.devicefarm.config.yaml` を使います。これは `showcase-compose` ターゲットの Device Farm 版で、**`appPath` を持ちません**。Device Farm はアップロードした APK を予約デバイスへ自分でインストールするため、adb バックエンドはローカルの APK を `adb install` するのではなく、インストール済みのアプリを起動します（`app_path` が None のときの、既存の「インストール済みアプリに対して実行する」経路です）。これは環境ごとの違いなので、ツールではなく設定に置きます。

`--package SRC=ARCNAME` はそれぞれ、ファイルまたはディレクトリを `ARCNAME` の位置でテストパッケージに追加します（arcname に `.` を指定すると、ディレクトリをパッケージのルートに展開します）。渡すシナリオや設定のパスは、パッケージの**内側**でのパスです。`--package .=.` で Bajutsu をパッケージすると、`pyproject.toml` と `tests/` ディレクトリがルートに置かれ、サブミッターは空の `requirements.txt` をルートに合成します。これにより、実際のインストールはテスト仕様が行いつつ、アップロードは Device Farm の APPIUM_PYTHON_TEST_PACKAGE 検証を満たします。`--package-only` を外して `--project-arn` と `--device-pool-arn` を加えれば（環境に AWS の認証情報を設定したうえで）、実行を投入し、完了までポーリングし、成果物をダウンロードして、Bajutsu の判定を表示します。プロセスの終了コードが `0` になるのは、すべてのシナリオが合格したときだけです。

iOS ショーケースの場合は `--platform ios` を渡し、XCUITest バックエンドと iOS アプリのアップロード種別を選択します。先にデバイス署名済みの `.ipa` とランナーをビルドします（BE-0288）。

```bash
make -C demos/showcase swiftui-ipa-device    DEVELOPMENT_TEAM=<10 文字の Team ID>
make -C demos/showcase runner-build-device   DEVELOPMENT_TEAM=<10 文字の Team ID>
```

次にサブミッターを実行します（`--package-only` でドライランになります。投入する場合は外して `--project-arn` と `--device-pool-arn` を追加してください）。

```bash
uv run python scripts/devicefarm_submit.py \
  --platform ios \
  --scenario scenarios/firstlook.yaml \
  --target showcase-swiftui \
  --config showcase.devicefarm.ios.config.yaml \
  --app demos/showcase/ios/swiftui/build/export-device/BajutsuShowcaseSwiftUI.ipa \
  --package .=. \
  --package demos/showcase/devicefarm/showcase.devicefarm.ios.config.yaml=showcase.devicefarm.ios.config.yaml \
  --package demos/showcase/scenarios=scenarios \
  --package BajutsuKit/Runner/build/dd-device/Build/Products=. \
  --package-only
```

`showcase.devicefarm.ios.config.yaml` は `xcuitest.deviceType: device`（XCUITest バックエンドがシミュレーターではなく実機を操作します）を設定し、`appPath` を持ちません（Device Farm がアップロードした `.ipa` を予約デバイスへ自分でインストールします）。`--package BajutsuKit/Runner/build/dd-device/Build/Products=.` は、デバイス署名済みの `.xctestrun` とテストバンドルをパッケージのルートに配置します。これは設定の `testRunner: BajutsuRunner.xctestrun` が参照する場所です。再署名と simctl に関する注意事項は[上記](#ios-再署名と実機のケーパビリティ)をご参照ください。

## GitHub Actions ワークフロー

[`.github/workflows/devicefarm.yml`](https://github.com/bajutsu-e2e/bajutsu/blob/main/.github/workflows/devicefarm.yml) は、サブミッターを**手動でオプトインする**ワークフローとしてラップします。これは `workflow_dispatch` のみで起動し、push や pull request では動きません。したがってマージ経路には乗らず、必須チェックにもなりません。ワークフローの GitHub OIDC トークンを `AWS_DEVICEFARM_ROLE_ARN` のロールと交換して短命の AWS 認証情報を発行し（静的なキーは使いません）、`devicefarm` という Environment にスコープします。プロジェクトとデバイスプールの ARN は、リポジトリ変数 `DEVICEFARM_PROJECT_ARN` と `DEVICEFARM_DEVICE_POOL_ARN` から読み取ります。この 3 つのいずれかが未設定のとき、ジョブは緑の no-op（`::notice::` を出すだけで、赤にはなりません）になります。運用者がアカウントを接続するまで、休止したままです。

## serve からのディスパッチ

上記のサブミッターとワークフローはヘッドレスの経路で、1 回の実行が一式をまとめてパッケージ化し、そのシナリオを 1 台の予約デバイス上で順番に実行します。`bajutsu serve`（BE-0336）は、運用者がすでに実行のディスパッチと履歴の確認に使っている画面に、2 つ目の経路を加えます。そしてその形を変えます。1 回に直列化した実行ではなく、serve は**シナリオごとに 1 つの Device Farm 実行へと一式を展開（fan-out）**します。これによりシナリオは並列に走り、1 台のデバイスではなくデバイス予算で上限が決まります。

`targets.<name>` の下にある 2 つのターゲット単位の設定項目がこれを駆動します。環境ごとの違いを設定に置き、ツール自体は変えないという方針（prime directive 3）に沿います。

- **`cloudBatch: devicefarm`** は、ターゲットのクラウド実行が使うバッチプロバイダーを選びます。これを持たないターゲットはローカルターゲットであり、クラウドの fan-out は拒否します。
- **`cloudBatchBudget: K`** はデバイス予算を定めます。そのプロバイダーの実行のうち、同時にデバイスを予約できるのは最大 `K` 件です。この上限は競合資源であるデバイスプール、すなわちプロバイダーでキーされるため、1 つのターゲットの取り分ではなく、そのプロバイダーを共有するすべてのターゲットと組織にまたがります。Device Farm 自体の `maxDevices` は実行あたり 1 に固定したままなので、同時に確保するデバイス数はこの Bajutsu 側の予算だけが決めます。

```yaml
targets:
  showcase-compose:
    platform: android
    package: com.example.showcase
    scenarios: demos/showcase/scenarios
    appPath: app-debug.apk        # Device Farm が予約デバイスへインストールする APK
    cloudBatch: devicefarm        # このターゲットのクラウド実行を Device Farm 経由でディスパッチする
    cloudBatchBudget: 4           # プール全体で同時に最大 4 台まで予約する
```

serve は AWS へ設定ファイルではなくプロセスの環境変数を通じて接続します。そのため、コミットされるターゲットに ARN や認証情報が残りません。serve を起動する前に **`DEVICEFARM_PROJECT_ARN`** を設定すると、そのプロセスでクラウドバッチのディスパッチが有効になります。AWS の認証情報は、boto3 が通常たどる探索順序（インスタンスロール、`AWS_*` 変数、名前付きプロファイル）から取得します。デフォルトのリージョンは `us-west-2` です。別のリージョンに固定したアカウントでは、**`DEVICEFARM_REGION`** で上書きします。`DEVICEFARM_PROJECT_ARN` を設定しないと、`devicefarm` プロバイダーは登録されないままになります。そのため、誤ってクラウドバッチをディスパッチしても、その時点で明示的に失敗します。ジョブが黙って消えることはありません。配線が済んでいるとき、serve は起動時に `cloud-batch dispatch enabled: devicefarm` を表示します。ヘッドレスのサブミッターと異なり、serve はデバイス選択フィルターで実行あたり 1 台のデバイスを予約するため、デバイスプールの ARN は不要です。

> **設定ファイルとシナリオの配置。** Bajutsu のチェックアウトから serve を起動した場合、クラウドバッチのパッケージは `pyproject.toml` と `tests/` を持つソースツリーをルートとして構築されます。そのため、クラウドバッチのターゲットが参照する設定ファイルと `scenarios` ディレクトリは、そのソースツリーの内側に置く必要があります。ソースツリーの外（例：`/home/me/myproject/bajutsu.config.yaml`）に置かれた設定ファイルでは、`POST /api/run-set` が 400 エラーを返し、パッケージルートのパスを示します。インストール済み（チェックアウト外）の Bajutsu から起動した場合は、設定ファイル自身のディレクトリがパッケージルートとなりますが、同じ制約が適用されます。

`POST /api/run-set` リクエストがターゲットを fan-out します。ボディには `target` を、任意で `scenarios` の部分集合と `deviceBudget` を指定します。

```json
{ "target": "showcase-compose", "scenarios": ["firstlook.yaml", "controls.yaml"], "deviceBudget": 2 }
```

`scenarios` を省くと、ターゲットのディレクトリにあるすべてのシナリオを実行します。要求された名前はいずれも、ディスパッチの**前**にシナリオへ解決されるため、未知の名前が 1 つあると、正しいものだけを fan-out して誤ったものでエラーにするのではなく、リクエスト全体を閉じて失敗させます。リクエスト単位の `deviceBudget` はターゲットの予算を*下げる*ことしかできず、上げることはできません。リクエストは共有資源を絞るのであって、より多くを与えるわけではないからです。予算がすでに埋まっている場合、余剰のシナリオは拒否ではなく**保留**されます。部分的なディスパッチは、この上限が司る通常の結果であり、レスポンスは開始できたジョブを列挙します。ただし serve は、そもそも要求を満たせないときには明示的に拒否します。すなわち、web ターゲット（予約すべきデバイスがありません）、`cloudBatch` プロバイダーや `appPath` を持たないターゲット、あるいはプロバイダーがパッケージ化する実行ディレクトリの外にある設定やシナリオです。

ホストされたデータベース背後のバックエンドでは、実行は **serve の再起動をまたいで永続**します。serve は Device Farm が実行を受け付けた瞬間にその実行の ARN をチェックポイントとして保存するため、再起動後にジョブを再リースしたワーカーは、その同じ実行のポーリングを再開します。再アップロードも再スケジュールもありません。150 分のポーリングの間、予約されたデバイスが取り残されることはありません。ローカルの単一プロセスのバックエンドは、より薄いベストエフォートの経路を保ちます。そこでは再起動でどのみちインメモリの状態が失われるため、チェックポイントは保存しません。

これらはいずれも判定には触れません。各 Device Farm 実行は、ローカル実行とまったく同じく、Bajutsu 自身の `manifest.json` から合否を報告します。serve はダウンロードした実行を自身の runs ディレクトリの下に収め、レポートビューアと履歴がほかの実行と同じように描画します。投入とポーリングのしくみは `run` / CI の判定経路の外にとどまり、そこに大規模言語モデル（LLM）の呼び出しは載りません（prime directive 1）。

## シリアル解決の実証（手動）

唯一の経験的な未知は、Bajutsu の Android バックエンドが Device Farm ホストの予約されたデバイスを拾えるかどうかです。`pre_test` の `adb devices` にそのデバイスが並び、`bajutsu run --udid booted` がそれを解決するはずです。これをエンドツーエンドで実証するには、実際の AWS アカウント、認証情報、Device Farm プロジェクト、そして課金が必要です。そのため、これは決定的な `make check` ゲート（クラウドのアカウントなしでどこでも動く必要があります）には**含めません**。実施は手動の手順とします。

1. `us-west-2` に Device Farm のプロジェクトとデバイスプールを作成し、それぞれの ARN を控えます。
2. showcase の Compose デバッグ APK をビルドします（`make -C demos/showcase/android compose-build`）。
3. サブミッター（またはワークフロー）を 1 つのシナリオ、たとえば `scenarios/firstlook.yaml` に対して実行します。
4. ダウンロードした成果物から、`adb devices` に予約されたデバイスが並んだこと、そしてシナリオが期待どおりの判定を持つ `manifest.json` を生成したことを確認します。サブミッターは CUSTOMER_ARTIFACT の zip（マニフェストを含む `runs/` ツリー）を保存先に展開し、Device Farm のファイル形式の成果物（`adb devices` の出力を含むデバイスログやテスト仕様のログ）はその隣の `logs/` サブディレクトリに書き出します。

自分のアカウントで確認できたら、ワークフローはより広いシナリオ一式を必要に応じて実行できます。

## iOS のデバイス署名の実証（手動）

iOS には、Android の経路にはない未知が加わります。バッチのアップロードが**署名済みのデバイスビルド**を伴わなければならない点です。Device Farm はアプリを物理デバイスへインストールするため、シミュレーター用レーンが出力する未署名の `.app` ではなく、デバイス用の `.ipa` が必要になります。さらに XCUITest ランナーは、あらかじめデバイスで有効な署名を持っていなければなりません。Device Farm はアプリを再署名しますが、ランナーは再署名しないためです（BE-0288）。したがって iOS の経路をエンドツーエンドで実証するには、ビルドに署名するための **Apple Developer アカウント**と、**AWS Device Farm アカウント**の両方が必要です。そのため、これは決定的な `make check` ゲート（未署名のまま、Apple や AWS のアカウントなしでどこでも動きます）には**含めません**。実施は手動の手順とします。

1. `us-west-2` に Device Farm のプロジェクトと iOS デバイスのデバイスプールを作成し、それぞれの ARN を控えます。
2. 署名済みのデバイス成果物を 2 つビルドします。10 文字の Apple Team ID を渡してください（その team を Xcode にサインインさせておくと、`-allowProvisioningUpdates` が開発用プロファイルを発行できます）。

   ```bash
   make -C demos/showcase swiftui-ipa-device    DEVELOPMENT_TEAM=<10 文字の Team ID>
   make -C demos/showcase runner-build-device   DEVELOPMENT_TEAM=<10 文字の Team ID>
   ```

   1 つ目はアプリの `.ipa` を `demos/showcase/ios/swiftui/build/export-device/BajutsuShowcaseSwiftUI.ipa` に出力します。2 つ目はデバイス署名済みの `BajutsuRunner.xctestrun` を `BajutsuKit/Runner/build/dd-device/Build/Products` の下に出力します。`DEVELOPMENT_TEAM` が未設定のままデバイスビルドを走らせると、未署名の成果物を作る代わりに、明確なメッセージとともに早期に失敗します。
3. iOS プラットフォームを選択して、1 つのシナリオ（たとえば `scenarios/firstlook.yaml`）を投入します。使うのは上記の [サブミッターの使い方](#サブミッターの使い方) と同じ `--platform ios` のコマンドで、そのドライラン用の `--package-only` を外し、代わりに `--project-arn <project-arn> --device-pool-arn <device-pool-arn>` を加えて実行を投入します。そのコマンドはランナーの `Products` ディレクトリをまるごとパッケージ（`--package BajutsuKit/Runner/build/dd-device/Build/Products=.`）するため、`.xctestrun` がその隣で `__TESTROOT__` として参照するテストバンドルも一緒にアップロードされます。ファイル単体では足りません。`--platform ios` は、XCUITest バックエンド、`IOS_APP` のアップロード種別、そして予約デバイスが解決に使う `--udid "$DEVICEFARM_DEVICE_UDID"` 引数を選択します。
4. ダウンロードした成果物から、シナリオが期待どおりの判定を持つ `manifest.json` を生成したことを確認します。Android の経路と同じく、サブミッターは CUSTOMER_ARTIFACT の zip（マニフェストを含む `runs/` ツリー）を保存先に展開し、Device Farm のファイル形式の成果物はその隣の `logs/` サブディレクトリに書き出します。判定は Bajutsu 自身の `manifest.json` から読み取り、Device Farm の PASSED/FAILED の分類からは読み取りません。落とされたエンタイトルメントに依存する機能では再署名後の挙動を、`simctl` に支えられたデバイス制御や権限付与を使うシナリオでは preflight によるスキップを、それぞれ見込んでください。いずれも上記の[実機の注意事項](#ios-再署名と実機のケーパビリティ)の 2 点です。

自分のアカウントで確認できたら、Android と同じように、ワークフローはより広い iOS のシナリオ一式を必要に応じて実行できます。

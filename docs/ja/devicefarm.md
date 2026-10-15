# AWS Device Farm で実行する

[AWS Device Farm](https://docs.aws.amazon.com/devicefarm/latest/developerguide/welcome.html) は、Amazon がホストする実機と仮想デバイスに対してテストを実行するデバイスクラウドです。Bajutsu は Device Farm の**カスタムテスト環境**を通じてここで動作し、予約されたデバイスに対して Android（adb）バックエンドを駆動します。決定的なコアには変更を加えません。

## バッチモデルと、ドライバーではなくサブミッターである理由

Device Farm はライブデバイスのプロバイダーではなく、**バッチ**サービスです。ネットワーク越しに操作するデバイスを貸し出すのではなく、Device Farm 側のホスト上で利用者のコマンドを実行します。そのホストには、予約されたデバイスに接続された `adb` がすでに用意されています。したがって、ここで提供するのは実行時のドライバー（取得すべきデバイスは存在しません）ではなく、**CI 側のサブミッター**です。Bajutsu とシナリオをパッケージ化し、`bajutsu run --backend adb` を実行するテスト仕様とともにアップロードして、Device Farm にホスト上で実行させ、成果物を回収します。

サブミッターの中核は決定的なコアの外側、[`bajutsu/common/cloud/devicefarm.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/bajutsu/common/cloud/devicefarm.py)（`render_test_spec` / `build_package` / `submit_and_collect` と `DeviceFarmClient` / `Transfer` の seam、BE-0336）にすべて収まっており、他の呼び出し元からも再利用できます。[`scripts/devicefarm_submit.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/scripts/devicefarm_submit.py) は、引数解析と実際の boto3 / urllib アダプタだけを持つ薄いラッパーになりました。アップロード、ポーリング、ダウンロードのしくみは、`run` や CI の判定経路に一切触れません。Bajutsu はローカルと同じように Device Farm の内側で動作し、同じ決定的なコアと、機械的に検査できるアサーションによる同じ合否を用います。そのため、サブミッターが報告する判定は **Bajutsu 自身の `manifest.json`** に由来し、Device Farm 独自の PASSED / FAILED 分類に依存しません。

## バッチトポロジーの注意点

以下は Device Farm 自体の性質です。将来 Device Farm 側が変わってもフローが気付かないうちに壊れないよう、サブミッターはこれらを文書化して扱います。

- **生の adb は副産物であって保証ではありません。** Device Farm がカスタム環境で第一級に扱う経路は Appium です。予約されたデバイスに対する `adb` は、ホストのツールチェーンに含まれるために利用できるのであって、Amazon が約束する契約ではありません。Bajutsu の Android バックエンドが必要とするのは「デバイスに接続された `adb` を備えたホスト」だけで、カスタム環境はそれを提供しますが、将来のプラットフォーム変更で失われうる副産物として扱ってください。
- **150 分のハードキャップ。** カスタム環境の 1 回の実行は 150 分が上限です。サブミッターはそれを超えてポーリングせず、CI ジョブを無限にブロックするのではなく、明示的に失敗させます。
- **APK のみ。** Device Farm の Android アプリのアップロードは `.apk` を受け付け、`.aab` は受け付けません。実行にはデバッグ APK をビルドしてください。
- **Appium コマンドごとのタイムアウト。** Device Farm の Appium コマンドごとのタイムアウトは、Bajutsu が用いる生の adb 経路には適用されません。実効的な上限は 150 分の実行キャップです。

Device Farm の iOS は実機で動作するため、下記の [iOS: 再署名と実機のケーパビリティ](#ios-再署名と実機のケーパビリティ) の 2 つの制約が加わります。

## iOS: 再署名と実機のケーパビリティ

実機で動作させることにより、Bajutsu が前もって織り込む点が 2 つあります（BE-0238）。いずれも Device Farm の分類ではなく物理デバイスそのものの性質なので、XCUITest バックエンドが駆動する実機（`runsOn.kind: device`）であれば、Device Farm でもローカル接続のデバイスでも同じように当てはまります。

- **再署名でエンタイトルメントが剥がれます。** Device Farm はアップロードされた `.ipa` を自前のプロビジョニングプロファイルで再署名し、予約されたデバイスにインストールできるようにします。この再署名では、新しいプロファイルが持たないエンタイトルメント（多くは Push（`aps-environment`）と App Groups（`com.apple.security.application-groups`））が落ちます。剥がれたエンタイトルメントに依存するアプリの機能（リモートプッシュの登録、App Group の共有コンテナ）は再署名後のビルドでは動作しないため、そうした機能をアサートするシナリオは、App Store 版ではなく再署名後の挙動を前提にしてください。
- **simctl のデバイス制御と権限付与は適用されません。** Bajutsu の iOS デバイス制御（`setLocation`、クリップボード系のステップ、`push`、`clearKeychain`、`background` / `foreground`、ステータスバーの上書き）と権限付与は、いずれも `simctl` に支えられています。`simctl` が届くのはシミュレータだけで、物理デバイスには届きません。そのため実機では XCUITest バックエンドはこれらを宣言せず、いずれかを使うシナリオは、デバイス操作を始める前に **preflight でスキップ**され（BE-0082）、実行の途中で `simctl` エラーとして遅れて失敗する代わりに、明確な理由とともに弾かれます。XCTest ランナー自身が駆動する実機側のケーパビリティ（query、elements、スクリーンショット、タップ、2 本指ジェスチャー）は影響を受けません。
- **ホストとアプリは 2 つの経路でつながります。** 物理デバイスは自分専用のループバックを持ちます。Bajutsu は runner とアプリ内の応答器に usbmuxd で接続します。アプリは、ネットワークのやり取りをホストアドレスへ報告します。このアドレスは実行ごとに渡すか、`BAJUTSU_HOST_ADDRESS` で指定します。`bajutsu run` の前に、Device Farm のホストで doctor を実行してください。コマンドは `bajutsu doctor --udid "$DEVICEFARM_DEVICE_UDID" --environment-only` です。usbmuxd が予約した実機を列挙しない場合、doctor は失敗します。アプリが受け取るホストアドレスの一覧も示します。新しくインストールしたアプリには、2 つ目の経路が必要とする iOS のローカルネットワークの許可もありません。どちらの経路も、Device Farm ではまだ誰も検証していません（[iOS を実機とデバイスクラウドで動かす](ios-device-cloud.md)を参照してください）。

## テスト仕様

Device Farm は[カスタム環境のテスト仕様](https://docs.aws.amazon.com/devicefarm/latest/developerguide/custom-test-environment-test-spec.html)から実行を駆動します。これは `install`、`pre_test`、`test`、`post_test` の各フェーズにシェルコマンドを記した YAML ファイルです。サブミッターは、次のような仕様を生成します。

1. **install**：uv で Python 3.13 を用意し、アップロードしたテストパッケージから Bajutsu をその venv へインストールします。Device Farm のホストは Python が最大 3.12 まで（`devicefarm-cli use python` は Amazon があらかじめ用意したランタイムしか選べません）で、Bajutsu は 3.13 を要求するため、ホストの標準 pip で uv を入れ、uv にスタンドアロンの 3.13 を取得させて、その venv へインストールします。これは暫定的な回避策で、Device Farm が 3.13 を提供したら取り除きます（サブミッターの `_python_bootstrap_commands` を参照）。adb バックエンドはサブプロセスだけで動くため、追加の extra を入れないベースのインストールで足ります。
2. **pre_test**：`adb devices` を実行し、予約されたデバイスが見えていること（シリアル解決の確認）を示します。呼び出し側は、`render_test_spec` の `pre_test_commands` パラメータを使って、独自のデバイス側セットアップをここに追加できます。渡したコマンドはプローブの後に、そのままの文字列で、指定した順序で差し込まれます。あるデプロイのバックエンドが実行前に必要とするもの（ネットワークリレーや VPN クライアントなど）を配置するための拡張点です。`build_package` の `extra_texts`（セットアップスクリプトをパッケージへ同梱するパラメータ）と組み合わせることで、そのデプロイ固有のセットアップを `bajutsu/` の外に完全に置いておけます。これは Python API 専用のフックです。`serve` エンドポイント・config フィールド・バッチリクエストのフィールドのいずれからも配線されないため、クライアント由来の値が、実行時の AWS ロール認証情報を持つホスト上のシェルに到達することはありません。
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

実行には `showcase.devicefarm.config.yaml` を使います。これは `showcase-compose` ターゲットの Device Farm 版で、**`app.path` を持ちません**。Device Farm はアップロードした APK を予約デバイスへ自分でインストールするため、adb バックエンドはローカルの APK を `adb install` するのではなく、インストール済みのアプリを起動します（`app_path` が None のときの、既存の「インストール済みアプリに対して実行する」経路です）。これは環境ごとの違いなので、ツールではなく設定に置きます。

`--package SRC=ARCNAME` はそれぞれ、ファイルまたはディレクトリを `ARCNAME` の位置でテストパッケージに追加します（arcname に `.` を指定すると、ディレクトリをパッケージのルートに展開します）。渡すシナリオや設定のパスは、パッケージの**内側**でのパスです。`--package .=.` で Bajutsu をパッケージすると、`pyproject.toml` と `tests/` ディレクトリがルートに置かれ、サブミッターは空の `requirements.txt` をルートに合成します。これにより、実際のインストールはテスト仕様が行いつつ、アップロードは Device Farm の APPIUM_PYTHON_TEST_PACKAGE 検証を満たします。`--package-only` を外して `--project-arn` と `--device-pool-arn` を加えれば（環境に AWS の認証情報を設定したうえで）、実行を投入し、完了までポーリングし、成果物をダウンロードして、Bajutsu の判定を表示します。プロセスの終了コードが `0` になるのは、すべてのシナリオが合格したときだけです。

iOS ショーケースの場合は `--platform ios` を渡し、XCUITest バックエンドと iOS アプリのアップロード種別を選択します。先にデバイス署名済みの `.ipa` とランナーをビルドします（BE-0288）。ランナーのビルドは、ユーザーごとの[署名ファイル](ios-device-cloud.md#署名ファイル)を読みます（BE-0456）。

```bash
make -C demos/showcase swiftui-ipa-device    DEVELOPMENT_TEAM=<10 文字の Team ID>
make -C demos/showcase runner-build-device
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

`showcase.devicefarm.ios.config.yaml` は `runsOn.kind: device`（XCUITest バックエンドがシミュレーターではなく実機を操作します）を設定し、`app.path` を持ちません（Device Farm がアップロードした `.ipa` を予約デバイスへ自分でインストールします）。`--package BajutsuKit/Runner/build/dd-device/Build/Products=.` は、デバイス署名済みの `.xctestrun` とテストバンドルをパッケージのルートに配置します。これは設定の `driver.runner.testRunner: BajutsuRunner.xctestrun` が参照する場所です。再署名と simctl に関する注意事項は[上記](#ios-再署名と実機のケーパビリティ)をご参照ください。

Device Farm のジョブでは、`runsOn.model` や `runsOn.os` のようなデバイスの条件を使えません（[configuration](configuration.md#デバイスの条件runson)）。Bajutsu は run の前に読み取れるデバイスに対して条件を照合しますが、Device Farm が予約するデバイスには、事前に読み取るものがありません。そのため provider は、ターゲットかシナリオが条件を宣言しているジョブを、アップロードの前に拒否し、該当するフィールドを示します。`runsOn.locale` と `runsOn.seedPhotos` は条件ではなくデバイスの状態なので、引き続き使えます。

## GitHub Actions ワークフロー

[`.github/workflows/devicefarm.yml`](https://github.com/bajutsu-e2e/bajutsu/blob/main/.github/workflows/devicefarm.yml) は、サブミッターを**手動でオプトインする**ワークフローとしてラップします。これは `workflow_dispatch` のみで起動し、push や pull request では動きません。したがってマージ経路には乗らず、必須チェックにもなりません。ワークフローの GitHub OIDC トークンを `AWS_DEVICEFARM_ROLE_ARN` のロールと交換して短命の AWS 認証情報を発行し（静的なキーは使いません）、`devicefarm` という Environment にスコープします。プロジェクトとデバイスプールの ARN は、リポジトリ変数 `DEVICEFARM_PROJECT_ARN` と `DEVICEFARM_DEVICE_POOL_ARN` から読み取ります。この 3 つのいずれかが未設定のとき、ジョブは緑の no-op（`::notice::` を出すだけで、赤にはなりません）になります。運用者がアカウントを接続するまで、休止したままです。

## serve からのディスパッチ

上記のサブミッターとワークフローはヘッドレスの経路で、1 回の実行が一式をまとめてパッケージ化し、そのシナリオを 1 台の予約デバイス上で順番に実行します。`bajutsu serve`（BE-0336）は、運用者がすでに実行のディスパッチと履歴の確認に使っている画面に、2 つ目の経路を加えます。そしてその形を変えます。1 回に直列化した実行ではなく、serve は**シナリオごとに 1 つの Device Farm 実行へと一式を展開（fan-out）**します。これによりシナリオは並列に走ります。同時に走る数の上限を決めるのは 1 台のデバイスではなく、Device Farm ワーカーのジョブ予算です。

ターゲットはバッチプロバイダーを指定しません。どこで実行するかはターゲットの性質ではなく実行ごとの選択なので、fan-out のリクエストがプロバイダーを `environment` として指定します（[BE-0452](../../roadmaps/BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md)）。ターゲットに要るのは、Device Farm が予約デバイスへインストールするバイナリを指す `app.path` だけです。

```yaml
targets:
  showcase-compose:
    platform: android
    app:
      id: com.example.showcase
      path: app-debug.apk         # Device Farm が予約デバイスへインストールする APK
    paths:
      scenarios: demos/showcase/scenarios
```

サーバはこれらの実行を自分では投入しません（BE-0448）。fan-out の各ジョブは、キューで Device Farm ワーカーを待ちます。Device Farm ワーカーとは、ケーパビリティファイル `worker.yaml` に `environment: devicefarm` を設定したワーカーです。このワーカーが実行をパッケージ化して投入し、ポーリングして判定をサーバへ返します。AWS のロールはワーカーの環境変数に置かれ、サーバの環境変数には置かれません。サーバとワーカーは、1 台のマシン上の 2 つのプロセスとして同居できます。ワーカーは、このファイルを指定し、`--platform` を指定せずに起動します。

```yaml
# devicefarm.worker.yaml
version: 1
environment: devicefarm
maxJobConcurrency: 4    # Device Farm の実行を同時に最大 4 件まで持つ
drivers: [adb, xcuitest]
```

```bash
DEVICEFARM_PROJECT_ARN=arn:aws:devicefarm:us-west-2:123456789012:project:abc \
  bajutsu worker --worker-config devicefarm.worker.yaml --server-url http://localhost:8765
```

ワーカーは AWS へ、設定ファイルではなく自身の環境変数を通じて接続します。コミットされるターゲットに ARN や認証情報は残りません。**`DEVICEFARM_PROJECT_ARN`** はプロジェクトを指定します。AWS の認証情報は、boto3 が自分で探します（インスタンスロール、`AWS_*` 変数、名前付きプロファイル）。デフォルトのリージョンは `us-west-2` です。別のリージョンに固定したアカウントでは、**`DEVICEFARM_REGION`** で上書きします。ワーカーは起動時に次の 3 つの条件を確かめ、1 つでも満たさなければ理由を示して終了します。

- 環境変数 `DEVICEFARM_PROJECT_ARN` が設定されていて、プロバイダーとそのフックを読み込めます。
- ワーカーが Bajutsu のソースのチェックアウトから起動しています。各実行のパッケージはチェックアウトの `pyproject.toml`、`tests/`、`bajutsu/` を含みます。インストール済みの Bajutsu には、これらがありません。
- コマンドラインで `--platform` を指定していません。端末は Device Farm のホストに属するので、ワーカーは自分のデバイスを操作しません。

登録が済むと、ワーカーは `provider registered: devicefarm` を表示し、ルーティング用のトークン `environment:devicefarm` を広告します。どのワーカーも自分のマシンを表す `host:<os>` を広告するので、このトークンも並びます。fan-out のジョブが要求するトークンは、`environment:devicefarm` 1 つだけです。このトークンを広告するワーカーは、ほかのジョブをリースしません。そのため、ローカルのジョブと Device Farm のジョブが互いのワーカーに届くことはありません。1 台のマシンで両方を扱うには、ワーカーが 2 つ要ります。ワーカーに `environment:` という接頭辞のトークンを付けられるのは、この登録だけです。`--capabilities` と `$BAJUTSU_WORKER_CAPABILITIES` は、起動時にこの接頭辞を拒否します。

どのワーカーも扱えない fan-out のジョブは、ほかのジョブと同じくキューに残り、ルーティング不能として数えられます。キューを持たないサーバには fan-out を渡せるワーカーがないので、`POST /api/run-set` を拒否します。

ヘッドレスのサブミッターと異なり、ワーカーはデバイス選択フィルターで実行あたり 1 台のデバイスを予約します。そのため、デバイスプールの ARN は不要です。

> **設定ファイルとシナリオの配置。** ワーカーはジョブごとに別々のパッケージを組み立てます。パッケージには、ソースのチェックアウトの 3 つの部分と、`bundle/` の下に置いたジョブのバンドルディレクトリが入ります。バンドルディレクトリは、次のいずれかです。設定ファイル自身のディレクトリ、アップロードされたバンドル、シナリオストアがジョブに同梱した設定ファイルとシナリオです。リクエストは、設定ファイルとシナリオをこのディレクトリからの相対パスで指定します。設定ファイルのディレクトリの外にあるシナリオでは、`POST /api/run-set` が 400 エラーを返します。ターゲットの `app.path` は書かれたとおりに渡されます。相対パスであれば、ワーカーが同じディレクトリを基準に解決します。バイナリが見つからなければ、ワーカーは何もアップロードせずにジョブを失敗させます。
>
> **アプリケーションの配送。** 設定ファイルとシナリオを同梱したジョブにも、バイナリは必要です。アップロードされたバンドルがバイナリを含まないとき、サーバは fan-out の時点で `app.path` が指すファイルをオブジェクトストアに保存します。リースは、そのファイルの署名付きダウンロードリンクを運びます。ワーカーは、ジョブのディレクトリ内の同じ `app.path` にバイナリをダウンロードします。そのうえで、ファイルのダイジェストがジョブの指定と一致するかを確かめます。この場合の `app.path` は設定ファイルのディレクトリ内のファイルを指す必要があり、サーバにはオブジェクトストアが要ります。条件を満たさなければ、`POST /api/run-set` は 400 エラーを返します。

### ジョブ予算

予算を持つのはワーカーです。ワーカーの `worker.yaml` の最上位にある `maxJobConcurrency` が、同時に処理できるジョブ数の上限です。ジョブは、リースした時点からワーカーが結果を送るまで数えます。枠がすべて埋まっている間、ワーカーはリースの要求を一切送りません。その間も実行中の各ジョブのハートビートがワーカーを稼働中として登録し続けるので、待ちが溜まっても、キューの Device Farm ジョブがルーティング不能と表示されることはありません。結果を送り終え、証跡をアップロードしているだけのジョブは数えないので、ワーカーは次のジョブをリースできます。

処理中のジョブは、それぞれ Device Farm のデバイスを 1 台使います。そのため、ワーカーが 1 つなら `maxJobConcurrency` がデバイス数の正確な上限になります。ワーカーが複数あるときに使うデバイスの数は、各ワーカーの処理中のジョブ数の合計です。運用者は、各ワーカーの `maxJobConcurrency` の合計をアカウントのクォータ内に収めます。`maxJobConcurrency` を省くと、ワーカーはジョブを 1 つずつ処理します。ワーカー 1 つが使うデバイスは 1 台になり、Device Farm のクォータに対して安全なデフォルトです。`maxJobConcurrency: N` のワーカー 1 つの代わりに、ジョブを 1 つずつ処理するワーカーを `N` 個動かす構成も引き続き使えます。ただし、AWS の環境変数、ソースのチェックアウト、プロセスがそれぞれ `N` 個ずつ要ります。

`maxJobConcurrency` が 1 を超えるワーカーは、各ジョブを**スロット**で処理します。スロットとは、リースしたジョブ 1 つとそのハートビートを受け持つスレッドです。スロットは、ワーカーの作業ディレクトリ内の `.slots/<n>/` にある作業ディレクトリ、アップロードするパッケージ、ジョブのログも受け持ちます。スロット同士が変化する状態を共有することはありません。スロットの作業ディレクトリは、証跡のアップロードが済んでからプールへ戻ります。そのため、前のジョブがまだアップロードしているディレクトリを、次のジョブが使うことはありません。ディスクは、スロットごとのアプリケーションのバイナリ 1 つ分と、アップロード中のスロットの実行ツリーの分を見込んで用意します。

ワーカーは、何かをアップロードする前に、リースした各シナリオを自分の `worker.yaml` と照らし合わせて検査します。この検査は、`bajutsu run --worker-config` が行う検査と同じものです（[ドライバー](drivers.md#worker-の能力検査be-0450)）。どのターゲットも、Device Farm のホストが実行するドライバーで判定します。Android のリクエストなら `adb`、iOS のリクエストなら `xcuitest` です。そのドライバーが `drivers` にないとき、またはシナリオが `maxTargetsPerJob` を超える数のターゲットを宣言しているとき、シナリオは検査に通りません。ワーカーが読めないシナリオファイルも通りません。このときジョブは `worker-capability-unsupported:` で始まる理由とともに失敗します。その結果は通常の結果の経路で送られ、Device Farm には何も届きません。保存済みの実行を再開するジョブは、以前の試行がその実行をすでに投入しているので、検査を省きます。

サーバは独自の予算を持ちません。ターゲットの `cloudBatchBudget` と `run-set` のリクエストの `deviceBudget` は、ターゲット設定の再構成（BE-0452）で取り除きました。`deviceBudget` をまだ指定するリクエストには、`maxJobConcurrency` を案内する 400 エラーを返します。サーバの全体、ユーザーごと、組織ごとの同時実行ジョブ数の上限はキューに積まれたジョブも数えるので、大きな fan-out を途中で打ち切ることがあります。

### ターゲットの fan-out

`POST /api/run-set` リクエストがターゲットを fan-out します。ボディには `target` と、バッチプロバイダーを表す `environment`（現在は `devicefarm`）を指定し、任意で `scenarios` の部分集合を指定します。

```json
{ "target": "showcase-compose", "environment": "devicefarm", "scenarios": ["firstlook.yaml", "controls.yaml"] }
```

`scenarios` を省くと、ターゲットのディレクトリにあるすべてのシナリオを実行します。要求された名前はいずれも、ディスパッチの**前**にシナリオへ解決されるため、未知の名前が 1 つあると、正しいものだけを fan-out して誤ったものでエラーにするのではなく、リクエスト全体を閉じて失敗させます。serve はシナリオごとに 1 つのジョブをキューに積み、Device Farm ワーカーが予算の許す範囲でリースします。serve は、要求をそもそも満たせないときには明示的に拒否します。

- `environment` がない、またはバッチプロバイダーを指さないリクエスト。`appium` も拒否します。グリッドには通常の実行リクエストで到達します（[セルフホスティング](self-hosting.md)）
- web ターゲット（予約すべきデバイスがありません）
- `app.path` を持たないターゲット
- 設定ファイルのディレクトリの外にある設定やシナリオ（ワーカーがパッケージにするディレクトリの外です）

### 再起動とリースの喪失

実行は、サーバやワーカーの**再起動をまたいで永続**します。Device Farm は、スケジュールした実行ごとに Amazon リソースネーム（ARN）を付けます。ワーカーは、Device Farm が実行を受け付けるとすぐに、その ARN を HTTP でサーバへ送ります。サーバは、ジョブのリースを保持しているワーカーからの ARN だけを保存し、ほかのワーカーからの ARN は拒否します。すでに保存した ARN と同じ値の再送は、どのワーカーからでも成功します。通信の失敗や `5xx` のとき、ワーカーは最長 6 分まで送信を繰り返します。あとでジョブをリースしたワーカーは、保存された ARN をリースと一緒に受け取ります。そのワーカーは同じ実行のポーリングを再開し、再アップロードも再スケジュールもしません。そのため、150 分のポーリングの間、ジョブが使うデバイスは 1 台のままです。このためにデータベースへの接続を持つワーカーは要りません。

各実行は、Device Farm 上でジョブ ID を名前として持ちます。運用者は、ジョブ ID から実行を探せます。Device Farm が実行をスケジュールしたあと、サーバが ARN を保存する前にワーカーが止まることもあります。その実行には持ち主がいなくなります。ジョブはもう一度投入され、最初の実行は Device Farm が終わらせるまでデバイスを使い続けます。

ワーカーが実行を手放す場合は、次の 5 つです。

| 出来事 | 実行の扱い | ワーカーが送る結果 |
|---|---|---|
| サーバがジョブの取り消しをワーカーへ中継したか、`maxJobConcurrency` が 1 のワーカーに停止シグナルが届いた | ワーカーが Device Farm の `StopRun` 呼び出しで実行を止める | 取り消されたジョブ |
| `maxJobConcurrency` が 1 を超えるワーカーに停止シグナルが届いた | サーバが ARN を保存した実行は残し、次にジョブをリースしたワーカーが再開する。サーバが ARN を持たない実行は、誰も再開できないのでワーカーが止める | なし（リースは期限切れになり、次のワーカーが結果を送る） |
| サーバが ARN を保存したあとでリースを失った | 実行は続き、次にジョブをリースしたワーカーが再開する | なし（次のワーカーが結果を送る） |
| リースを失ったためにサーバが ARN に `409` を返した | 再開できるワーカーがいないため、ワーカーが実行を止める | 失敗の結果（リースが移っているので、サーバは無視する） |
| サーバがそれ以外の `4xx`（サーバか設定の誤り）で ARN を拒否したか、6 分の再送が尽きた | 再開の手がかりとなる ARN をサーバが持たないため、ワーカーが実行を止め、誤りをログに残す | その誤りを添えた、失敗したジョブ |

アップロードの途中で取り消しが届いた場合、実行はスケジュールされません。スロットを使うワーカーは、停止シグナルを取り消しではなく終了の合図として扱います。シグナルを受けるとリースの要求をやめ、処理中のリースはすべて期限切れに任せます。それらのジョブは、再起動したワーカーかほかのワーカーが引き継ぎます。ワーカーは、各スロットが実行を手放し、始まっていたアップロードが終わるのを待ってから終了します。2 回目の SIGINT を受けると、待たずに終了します。同じ `worker.yaml` を指定した `bajutsu worker --once <spec.json>` は、サーバなしでバッチの仕様を 1 つ投入します。その `batch.app_path` はそのマシン上のパスであり、ARN は保存しません。SIGINT、SIGTERM、SIGHUP は、取り消しと同じようにスケジュール済みの実行を止めます。

これらはいずれも判定には触れません。各 Device Farm 実行は、ローカル実行とまったく同じく、Bajutsu 自身の `manifest.json` から合否を報告します。ワーカーはダウンロードした実行を収めてアップロードします。serve のレポートビューアと履歴は、その実行をほかの実行と同じように描画します。判定が出る前に投入が失敗した場合は、その理由をジョブのエラーとして送ります。投入とポーリングのしくみは `run` / CI の判定経路の外にとどまり、そこに大規模言語モデル（LLM）の呼び出しは載りません（prime directive 1）。

## バッチライフサイクルフック（BE-0435）

`render_test_spec` の `pre_test_commands` フック（BE-0432）は**デバイスホスト側のセットアップ**を担います。Device Farm ホスト上でシェルとして実行されるコマンドです。しかし、そこでは実行できないセットアップがあります。実行を投入するワーカーの中で、パッケージのビルド前に実行し、その結果をアプリの launch 環境変数として届ける必要があるものです（たとえば、短命な per-run クレデンシャルの発行）。

`BatchLifecycleHook` はそのギャップを埋めます。フックは、環境が `devicefarm` のワーカーの中で、`DeviceFarmBatchProvider.submit` の前後に実行されます。サーバはフックを実行しません。

- **`before_submit(ctx)`** — パッケージのビルド前に呼ばれます。`ctx.launch_env` にキーと値を追加することで、パッケージされる config の `targets.<target>.app.launch.env` にマージされ、アプリが launch 環境変数として受け取ります。
- **`after_run(ctx, verdict)`** — 判定収集後（または判定前に失敗した場合は `verdict=None`）に `finally` の中で逆順に呼ばれます。あるフックの `after_run` が例外を送出しても、残りのフックは実行されます。判定（verdict）を収集できた場合、`submit` はティアダウンの失敗をすべてログに出力し、1つも送出しません。クレデンシャルの解放に失敗しても、完了した実行が破棄されることはありません。判定が得られる前に失敗した場合は、失敗したフックのうち最後に登録されたものの例外が伝播し、残りを `submit` がログに出力します。

`BatchContext` には実行の `request`、`work_dir`、`job_id`（再起動をまたいで安定した per-job 識別子）、および変更可能な `launch_env` 辞書が含まれます。

worker によるディスパッチ（BE-0448）以降、`work_dir` はジョブのバンドルディレクトリであり、リクエストのパスはこのディレクトリからの相対パスです。`request.config` と `request.scenario` は常に相対パスです。`request.app_path` はターゲットの `app.path` を書かれたとおりに運びます。バイナリを読むフックは、相対パスを自分の作業ディレクトリではなく `ctx.work_dir` を基準に解決します。ソースのチェックアウトからパッケージを組み立てる worker では、バンドルはパッケージ内の `bundle/` の下に置かれます。そのため、Device Farm のホスト上で設定ファイルからの相対パスを指す `pre_test_commands` のコマンドには、`bundle/` の接頭辞が必要です。

### フックの配線

フックは**プロセス起動時にのみ**配線されます。環境変数 `BAJUTSU_BATCH_HOOKS`（`module:factory` パスのカンマ区切りリスト）を通じてです。Bajutsu は各モジュールをインポートし、指定されたファクトリ関数を呼び出してフックインスタンスを生成します。`maxJobConcurrency` が 1 を超える worker では、すべてのスロットでこのインスタンスを共有するので、フックは同時に呼ばれることがあります。そのため、フックはスレッドセーフに作り、実行ごとの状態は `ctx.job_id` をキーにして持ちます。

```bash
BAJUTSU_BATCH_HOOKS=myapp.hooks:make_proxy_hook,myapp.hooks:make_audit_hook \
  bajutsu worker --worker-config devicefarm.worker.yaml
```

デプロイ側はフックモジュールをワーカーのイメージに同梱し、その変数で名前を指定します。読み込めないフックがあると、ワーカーは起動時に終了します。いかなる `serve` エンドポイント、config フィールド、バッチリクエストフィールドもフックを選択・設定してはなりません——フックの同一性はデプロイ時の環境からのみ与えられます（`pre_test_commands` と同じ信頼境界）。

```python
# myapp/hooks.py
from bajutsu.serve.batch_provider import BatchContext, BatchLifecycleHook

class ProxyHook(BatchLifecycleHook):
    def before_submit(self, ctx: BatchContext) -> None:
        psk = _mint_session_key(ctx.job_id)
        ctx.launch_env["PROXY_HOST"] = "proxy.internal"
        ctx.launch_env["PROXY_PSK"] = psk

    def after_run(self, ctx: BatchContext, verdict) -> None:
        _revoke_session_key(ctx.job_id)

def make_proxy_hook() -> ProxyHook:
    return ProxyHook()
```

> **セキュリティ上の注意。** `ctx.launch_env` 経由で注入された値はパッケージされる run config に載り、デプロイ自身の Device Farm プロジェクトにアクセスできるプリンシパルから見えます。秘密値を注入するフックは、長命な値ではなく**短命な per-run 値**を発行してください。

### キー衝突ガード

デバイス上のマージ順序は `{**target_env, **preconditions.app.launch.env}` です——シナリオの `preconditions.app.launch.env` が target レベルの値より優先されます。フックが注入したキーとシナリオの preconditions が衝突する場合、注入された値が暗黙的に上書きされます。これを早期に表面化させるため、`submit` はシナリオファイルを確認し、衝突があれば**投入時点でシナリオ名・キー・ファイル名を明示してエラー**を発生させます。アップロードは開始されません。

### checkpoint 再開時の動作

再起動によってワーカーが再リースされた場合、`before_submit` はスキップされます（実行は既にスケジュール済みでパッケージ済みです）。しかし、再開した実行の判定収集後に `after_run` は実行されます。`before_submit` でクレデンシャルを発行するフックは、`ctx.job_id` をキーとして永続化しておくことで、`after_run` が再起動後に取得・解放できるようにしてください。

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
2. 署名済みのデバイス成果物を 2 つビルドします。アプリには 10 文字の Apple Team ID を渡してください。その team を Xcode にサインインさせておくと、`-allowProvisioningUpdates` が開発用プロファイルを発行できます。ランナーは代わりに[署名ファイル](ios-device-cloud.md#署名ファイル)を読みます（BE-0456）。ファイルを明示するときは `SIGNING=<path>` を渡します。

   ```bash
   make -C demos/showcase swiftui-ipa-device    DEVELOPMENT_TEAM=<10 文字の Team ID>
   make -C demos/showcase runner-build-device
   ```

   1 つ目はアプリの `.ipa` を `demos/showcase/ios/swiftui/build/export-device/BajutsuShowcaseSwiftUI.ipa` に出力します。2 つ目はデバイス署名済みの `BajutsuRunner.xctestrun` を `BajutsuKit/Runner/build/dd-device/Build/Products` の下に出力します。未署名の成果物を作ることはありません。`DEVELOPMENT_TEAM` が未設定のアプリのビルドと、署名ファイルがないランナーのビルドは、明確なメッセージとともに早期に失敗します。
3. iOS プラットフォームを選択して、1 つのシナリオ（たとえば `scenarios/firstlook.yaml`）を投入します。使うのは上記の [サブミッターの使い方](#サブミッターの使い方) と同じ `--platform ios` のコマンドで、そのドライラン用の `--package-only` を外し、代わりに `--project-arn <project-arn> --device-pool-arn <device-pool-arn>` を加えて実行を投入します。そのコマンドはランナーの `Products` ディレクトリをまるごとパッケージ（`--package BajutsuKit/Runner/build/dd-device/Build/Products=.`）するため、`.xctestrun` がその隣で `__TESTROOT__` として参照するテストバンドルも一緒にアップロードされます。ファイル単体では足りません。`--platform ios` は、XCUITest バックエンド、`IOS_APP` のアップロード種別、そして予約デバイスが解決に使う `--udid "$DEVICEFARM_DEVICE_UDID"` 引数を選択します。
4. ダウンロードした成果物から、シナリオが期待どおりの判定を持つ `manifest.json` を生成したことを確認します。Android の経路と同じく、サブミッターは CUSTOMER_ARTIFACT の zip（マニフェストを含む `runs/` ツリー）を保存先に展開し、Device Farm のファイル形式の成果物はその隣の `logs/` サブディレクトリに書き出します。判定は Bajutsu 自身の `manifest.json` から読み取り、Device Farm の PASSED/FAILED の分類からは読み取りません。落とされたエンタイトルメントに依存する機能では再署名後の挙動を、`simctl` に支えられたデバイス制御や権限付与を使うシナリオでは preflight によるスキップを、それぞれ見込んでください。いずれも上記の[実機の注意事項](#ios-再署名と実機のケーパビリティ)の 2 点です。

自分のアカウントで確認できたら、Android と同じように、ワークフローはより広い iOS のシナリオ一式を必要に応じて実行できます。

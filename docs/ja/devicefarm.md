# AWS Device Farm で実行する

[AWS Device Farm](https://docs.aws.amazon.com/devicefarm/latest/developerguide/welcome.html) は、Amazon がホストする実機と仮想デバイスに対してテストを実行するデバイスクラウドです。Bajutsu は Device Farm の**カスタムテスト環境**を通じてここで動作し、予約されたデバイスに対して Android（adb）バックエンドを駆動します。決定的なコアには変更を加えません。

## バッチモデルと、ドライバーではなくサブミッターである理由

Device Farm はライブデバイスのプロバイダーではなく、**バッチ**サービスです。ネットワーク越しに操作するデバイスを貸し出すのではなく、Device Farm 側のホスト上で利用者のコマンドを実行します。そのホストには、予約されたデバイスに接続された `adb` がすでに用意されています。したがって、ここで提供するのは実行時のドライバー（取得すべきデバイスは存在しません）ではなく、**CI 側のサブミッター**です。Bajutsu とシナリオをパッケージ化し、`bajutsu run --backend adb` を実行するテスト仕様とともにアップロードして、Device Farm にホスト上で実行させ、成果物を回収します。

サブミッターは決定的なコアの外側、[`scripts/devicefarm_submit.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/scripts/devicefarm_submit.py) にすべて収まっています。アップロード、ポーリング、ダウンロードのしくみは、`run` や CI の判定経路に一切触れません。Bajutsu はローカルと同じように Device Farm の内側で動作し、同じ決定的なコアと、機械的に検査できるアサーションによる同じ合否を用います。そのため、サブミッターが報告する判定は **Bajutsu 自身の `manifest.json`** に由来し、Device Farm 独自の PASSED / FAILED 分類に依存しません。

## バッチトポロジーの注意点

以下は Device Farm 自体の性質です。将来 Device Farm 側が変わってもフローが気付かないうちに壊れないよう、サブミッターはこれらを文書化して扱います。

- **生の adb は副産物であって保証ではありません。** Device Farm がカスタム環境で第一級に扱う経路は Appium です。予約されたデバイスに対する `adb` は、ホストのツールチェーンに含まれるために利用できるのであって、Amazon が約束する契約ではありません。Bajutsu の Android バックエンドが必要とするのは「デバイスに接続された `adb` を備えたホスト」だけで、カスタム環境はそれを提供しますが、将来のプラットフォーム変更で失われうる副産物として扱ってください。
- **150 分のハードキャップ。** カスタム環境の 1 回の実行は 150 分が上限です。サブミッターはそれを超えてポーリングせず、CI ジョブを無限にブロックするのではなく、明示的に失敗させます。
- **APK のみ。** Device Farm の Android アプリのアップロードは `.apk` を受け付け、`.aab` は受け付けません。実行にはデバッグ APK をビルドしてください。
- **Appium コマンドごとのタイムアウト。** Device Farm の Appium コマンドごとのタイムアウトは、Bajutsu が用いる生の adb 経路には適用されません。実効的な上限は 150 分の実行キャップです。

Device Farm のカスタムモードにおける iOS には追加の制約があり、ここではなく別項目（*ios-device-cloud-execution* の姉妹項目）で扱います。

## テスト仕様

Device Farm は[カスタム環境のテスト仕様](https://docs.aws.amazon.com/devicefarm/latest/developerguide/custom-test-environment-test-spec.html)から実行を駆動します。これは `install`、`pre_test`、`test`、`post_test` の各フェーズにシェルコマンドを記した YAML ファイルです。サブミッターは、次のような仕様を生成します。

1. **install**：Python ランタイムを選び（`devicefarm-cli use python …`）、アップロードしたテストパッケージから Bajutsu を `pip install` します。adb バックエンドはサブプロセスだけで動くため、追加の extra を入れないベースのインストールで足ります。
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
  --target showcase-compose --config showcase.config.yaml \
  --app-apk app-debug.apk \
  --package .=bajutsu \
  --package demos/showcase/showcase.config.yaml=showcase.config.yaml \
  --package demos/showcase/scenarios=scenarios \
  --package-only
```

`--package SRC=ARCNAME` はそれぞれ、ファイルまたはディレクトリを `ARCNAME` の位置でテストパッケージに追加します。渡すシナリオや設定のパスは、パッケージの**内側**でのパスです。`--package-only` を外して `--project-arn` と `--device-pool-arn` を加えれば（環境に AWS の認証情報を設定したうえで）、実行を投入し、完了までポーリングし、成果物をダウンロードして、Bajutsu の判定を表示します。プロセスの終了コードが `0` になるのは、すべてのシナリオが合格したときだけです。

## GitHub Actions ワークフロー

[`.github/workflows/devicefarm.yml`](https://github.com/bajutsu-e2e/bajutsu/blob/main/.github/workflows/devicefarm.yml) は、サブミッターを**手動でオプトインする**ワークフローとしてラップします。これは `workflow_dispatch` のみで起動し、push や pull request では動きません。したがってマージ経路には乗らず、必須チェックにもなりません。ワークフローの GitHub OIDC トークンを `AWS_DEVICEFARM_ROLE_ARN` のロールと交換して短命の AWS 認証情報を発行し（静的なキーは使いません）、`devicefarm` という Environment にスコープします。プロジェクトとデバイスプールの ARN は、リポジトリ変数 `DEVICEFARM_PROJECT_ARN` と `DEVICEFARM_DEVICE_POOL_ARN` から読み取ります。この 3 つのいずれかが未設定のとき、ジョブは緑の no-op（`::notice::` を出すだけで、赤にはなりません）になります。運用者がアカウントを接続するまで、休止したままです。

## シリアル解決の実証（手動）

唯一の経験的な未知は、Bajutsu の Android バックエンドが Device Farm ホストの予約されたデバイスを拾えるかどうかです。`pre_test` の `adb devices` にそのデバイスが並び、`bajutsu run --udid booted` がそれを解決するはずです。これをエンドツーエンドで実証するには、実際の AWS アカウント、認証情報、Device Farm プロジェクト、そして課金が必要です。そのため、これは決定的な `make check` ゲート（クラウドのアカウントなしでどこでも動く必要があります）には**含めません**。実施は手動の手順とします。

1. `us-west-2` に Device Farm のプロジェクトとデバイスプールを作成し、それぞれの ARN を控えます。
2. showcase の Compose デバッグ APK をビルドします（`make -C demos/showcase/android compose-build`）。
3. サブミッター（またはワークフロー）を 1 つのシナリオ、たとえば `scenarios/firstlook.yaml` に対して実行します。
4. ダウンロードした成果物から、`adb devices` に予約されたデバイスが並んだこと、そしてシナリオが期待どおりの判定を持つ `manifest.json` を生成したことを確認します。

自分のアカウントで確認できたら、ワークフローはより広いシナリオ一式を必要に応じて実行できます。

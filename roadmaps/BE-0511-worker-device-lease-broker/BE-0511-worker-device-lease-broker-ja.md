[English](BE-0511-worker-device-lease-broker.md) · **日本語**

# BE-0511 — worker が job ごとに、リースブローカーから端末を借りる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0511](BE-0511-worker-device-lease-broker-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0511") |
| 実装 PR | [#2565](https://github.com/bajutsu-e2e/bajutsu/pull/2565) |
| トピック | Device-cloud execution |
| 関連 | [BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction-ja.md)、[BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md)、[BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md)、[BE-0484](../BE-0484-job-group-lease/BE-0484-job-group-lease-ja.md) |
<!-- /BE-METADATA -->

## はじめに

worker が、job に必要な端末を**リースブローカー**から借ります。リースブローカーは、シミュレーター、エミュレーター、実機を貸し出し、同じ端末を二人が借りないように管理するプログラムです。worker は job を claim した後で端末を借り、run に渡し、run が終わったら返します。

借りる処理は、run のために端末を確保する既存の device provider の枠組みに、新しい provider `leased` を足して担わせます。ブローカーは operator が `worker.yaml` に宣言し、シナリオと target は変えません。

job に必要な端末をブローカーがすべては貸せないとき、worker は 1 台も持たずに job を queue に戻します。辞退ウィンドウが開いている間、job は queue で待つだけで、その待ち時間が job の verdict に残ることはありません。

## 動機

worker は、他の人やエージェントと端末を共有するホストで動きます。いまの worker には、端末を要求する手段がありません。共有端末を使って run したい operator は、手で借ります。

ブローカーでリースを始め、端末のアドレスを `--udid` に写し、ブローカーが返した環境変数を export します。環境変数の例は、エミュレーターを持つ Android Debug Bridge（adb）サーバーの port です。さらに、run が続く間はリースのプロセスを生かし続け、終わったら止めます。target が複数ある job や、`--workers` で複数のレーンを使う job では、この操作を端末の数だけ繰り返します。

worker にはこの操作が 1 つもできません。そのため、共有ホストの worker が操作できるのは、他の誰も触らない端末だけです。

この制限には 2 つの代償があります。自分のものではない端末を worker が操作すると、他の借り手と衝突します。衝突はアプリと無関係なシナリオ失敗として現れます。端末を worker に専有させるホストでは、job の合間に端末が遊びます。他の借り手が使えないからです。

効果は、次の違いで確かめられます。共有ホストの worker が、queue の job を、自分で借りた端末を使って実行します。operator は `--udid` を打たず、ブローカーの環境変数も export しません。端末を 3 台必要とする job は、動いている間に 3 つの端末リースを持ちます。job が終わると、ブローカーの一覧にその worker のリースは残りません。

ブローカーがすべての端末を貸せないとき、job は queue に残り、worker は再び poll します。job の辞退ウィンドウが開いている間は、端末が足りないことを理由に job の結果が失敗になることはありません。ウィンドウはデフォルトで 1 時間です。

iOS と Android の両方の端末が要る job は、`leased` worker でも動きます。両方の端末を持つ local worker で動くのと同じです。

worker が run の途中で止まっても、ブローカーが貸し直した端末を操作し続ける run は残りません。

serve の control plane では、*リース*という語がすでにもう 1 つの意味を持ちます。worker は server から *job をリース*し、worker の Heartbeat が途絶えると server が job を取り戻します。この提案では、ブローカーによる貸し出しを「端末リース」、server による貸し出しを「job リース」と呼び分けます。

## 詳細設計

### 借りる処理の置き場所

枠組みはすでにあります。`DeviceProvider`（[BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction-ja.md)）は `acquire(eff, requested_udid)` という 1 つのメソッドを持ち、`DeviceLease` を返します。`DeviceLease` は、run が操作する udid spec と、run が `finally` で呼ぶ `release` を持ちます。provider は `bajutsu/common/runner/device_provider/_functions.py` に `kind` で登録します。現在の登録は `local`、`appium`、`devicefarm-host` の 3 つです。

この提案は 4 つ目の kind `leased` を登録します。`_LeasedProvider` はブローカーのリースコマンドを実行し、応答から端末を読み、`release` でリースを終える `DeviceLease` を返します。`bajutsu run` はすでに `acquire_device` で端末を取得し、`finally` で返します。

run のパイプラインが変わるのは 1 か所です。`_acquire_targets` が、target の端末を解決する前に、各リースの `env` をプロセスの環境変数に適用します。

端末を取得してリースを捨てる呼び出し元が、ほかに 2 つあります。1 つは `_device_udid_or_exit` で、`record`、`crawl`、`repl`、`doctor`、`audit` が共有します。もう 1 つは、`_probe_device` を通して `acquire_device` を呼ぶ Model Context Protocol（MCP）のツールです。`leased` environment のもとでは、どちらもリースコマンドを動かしたまま残します。そこで両方とも、コマンドが終わるときにリースを返します。MCP サーバーは長く動き続けるので、MCP のツールは、ツールの呼び出しが返るときにリースを返します。両方とも、アドレスを使う前に、リースの `env` をプロセスの環境変数に適用します。

worker の environment を `local` と直接比べる既存の検査が 2 つあります。`_register_environment_or_exit` と `environment_refusal` で、`is_local` を変えるだけではそこに届きません。`is_local` を読む検査のうち 2 つは、`leased` を別に扱う必要があります。capability の token はプラットフォームをファイルから取り、`_device_reader` は `leased` を飛ばします。どれも下の表に行を足します。

| 層 | 変更 |
|---|---|
| `bajutsu/common/runner/device_provider/` | 新規の `_leased_provider.py` を追加し、`_ensure_builtins` で `leased` として登録します |
| 同じパッケージ | プラットフォーム付きの `--udid` 一覧を分けるヘルパーを新設します。`_LeasedProvider` と `_reject_unshareable_groups` が呼びます |
| `DeviceLease`（`device_lease.py`） | 新しい `env` フィールドを足します。run の端末ツールに見せる環境変数の対応表です |
| `bajutsu/common/config/schema/device_provider.py` | `DeviceProvider` に、リースコマンド、取得タイムアウト、返却タイムアウトのフィールドを足します |
| `bajutsu/common/capability/worker_capability.py` | `lease` ブロックと `platforms` の一覧を持つ新しい environment `leased` を足します |
| 同じファイル | `device_provider_for` は、`platforms` に挙げたプラットフォームには、それらのフィールドを持つ `leased` provider を返します。ほかのプラットフォーム（web を含む）には None（local）を返します |
| 同じファイル | `is_local` が `leased` でも真になり、local worker と同じ 1 job の上限が掛かります |
| `bajutsu/serve/cli/worker.py` | capability の token、route の token、`--machine-id` が `leased` を `local` と同じに扱います |
| `bajutsu/serve/cli/worker.py`（`_register_environment_or_exit`） | `leased` は `local` と同じく、batch provider を登録しません |
| `bajutsu/serve/server/one_shot.py`（`environment_refusal`） | `leased` は `local` と同じく、通常の run spec を受け、batch を拒否します |
| `bajutsu/serve/cli/worker.py` | `_device_reader` は `leased` を飛ばします。実機はブローカーのものだからです |
| `bajutsu/serve/cli/worker.py`（`_run_with_heartbeat`） | `claim_check` が通り、bundle の取得が終わった後に、Heartbeat を送る run のスレッドで job の端末を借ります。`lost` が立っていれば run を始めずに戻ります。借りる時間の合計には、job リースのタイムアウトより短い予算で上限を掛けます。端末は `finally` で返します |
| `bajutsu/serve/server/worker_job.py` | `leased` worker では、spec が持ち込んだ `--udid` を取り除き、spec の `udids` を起動しません |
| `bajutsu/serve/cli/worker.py` と `bajutsu/run/cli.py` が共に import する新規モジュール | `_pool_demand` をここへ移します。worker と run が 1 つの関数で端末を数えます |
| `bajutsu/run/cli.py`（`_acquire_targets`） | target の端末を解決する前に、各リースの `env` をプロセスの環境変数に適用します |
| `bajutsu/run/cli.py`（`_reject_unshareable_groups`） | プラットフォーム付きの `--udid` から、target のプラットフォームのエントリーを読みます |
| `bajutsu/cli/_shared.py`（`_device_udid_or_exit`）と `bajutsu/mcp/tools.py`（`_probe_device`） | `env` を適用し、コマンドが終わるとき、またはツールの呼び出しが返るときにリースを返します |
| serve の route とデータベース | 新規の `POST /api/worker/decline` を足します。job ごとに、辞退の回数、最初の辞退時刻、最後の理由、`not_before` の時刻、辞退した worker の environment を保存します。リースのリクエストは worker の environment を名指しします |
| `bajutsu/serve/server/db/sql_repository.py`（`_lease_candidates`） | リースの走査は、`not_before` を過ぎるまで、辞退した environment の worker に対してだけ、queue の job を飛ばします |
| server の設定 | 新規の `BAJUTSU_DECLINE_WINDOW_SECONDS` を足します。デフォルトは 1 時間です |
| `bajutsu/serve/jobs.py`（`_run_job`） | pipe 経由の経路で起動する `run` job に、`_run_inherited` が one-shot の呼び出し元に渡すのと同じ親 pipe を渡します |

### operator が宣言するもの

`worker.yaml`（[BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md)）はホストを記述し、ブローカーはホストに属します。`appium` environment はすでにそこでグリッドを名指ししています。`leased` environment も、同じ形でブローカーを名指しします。

```yaml
environment: leased
platforms: [ios, android]
lease:
  command: ["broker-cli", "lease", "--platform", "{platform}", "--device", "{device}"]
  acquireTimeout: 60s
  releaseTimeout: 10s
```

`command` はシェル文字列ではなく引数のリストです。値がシェルを通ることはありません。worker は `{platform}` を、借りる端末のプラットフォーム（`ios`、`android`）に置き換えます。`{device}` は、job の `runsOn` が名指しする端末モデルに置き換えます。worker は、`-` で始まるモデルを、argv を組む前に拒否します。`runsOn.ios.model` と `runsOn.android.avd` は自由な文字列で、job と共に届きます。フラグの形をした値は、端末名ではなくオプションとして operator のコマンドに届いてしまいます。job が端末を名指ししないときは、`{device}` の要素と、その直前の要素を取り除きます。

`acquireTimeout` は、応答を待つ長さの上限です。`releaseTimeout` は、終了シグナルの後にコマンドの終了を待つ長さの上限です。`platforms` は、ブローカーが扱うプラットフォームの一覧です。スキーマが `platforms` に受け付けるのは `ios` と `android` だけです。

空の `command` や未知のプレースホルダーは、`worker.yaml` を読み込む時点でスキーマが拒否します。`{device}` をより長い要素の中に置くことも拒否します。`{device}` を最初の要素にすることも、別のプレースホルダーの後に置くことも拒否します。`{device}` は、フラグの直後に来る要素全体でなければなりません。

ほかの environment の下に置いた `platforms` も拒否します。打ち間違いは、最初の job ではなく起動時に失敗します。

### ブローカーとの契約

リポジトリは特定のブローカーを名指ししません。契約はリースコマンドの振る舞いであり、この振る舞いをするブローカーなら何でも使えます。

**1 点目。** 端末を借りている間、コマンドは**動き続けます**。プロセスを終えるとリースが終わります。Bajutsu は、プロセスに終了シグナルを送ります。プロセスが終了するまで、最長で `releaseTimeout` だけ待ちます。その後も生きているコマンドは強制的に終えます。1 つの固まったコマンドが、job の `finally` を開いたままにすることはありません。

**2 点目。** `acquireTimeout` 以内に、コマンドは標準出力へ **1 行**だけ出力します。その行にはデータオブジェクトが 1 つあります。worker はその行を読み、後から標準出力に書かれるものは捨てます。答えの後もログを書くブローカーは、いっぱいになった pipe で止まりません。

```json
{"device": {"address": "emulator-5588"}, "environment": {"ANDROID_ADB_SERVER_PORT": "5038"}}
```

`device.address` が udid spec になります。`environment` の各エントリーは `DeviceLease.env` のエントリーになります。

**3 点目。** その行を出す前にコマンドが終了したら、**辞退**です。非ゼロの終了コードと標準エラーのメッセージは、理由つきの辞退です。worker はその理由をログに残します。`acquireTimeout` 以内に行が出ない場合も辞退です。このとき worker が先にコマンドを終えます。

**4 点目。** 標準入力が終端に達したら、コマンドはリースを終えます。worker はこの入力を開いたままにするので、worker がどのように終わっても、OS が入力を閉じます。

provider は端末の中身を調べません。読むのは 2 つのフィールドです。`device.address` が欠けていれば、そのフィールド名を挙げた `ValueError` を出します。run が操作できないアドレスは、最初のタップではなく、解決の時点で失敗させるべきだからです。

4 点目は、worker が止まったときにリースを終えます。端末を操作し続ける run までは止めません。この隙間は、run の寿命の節で塞ぎます。

### 1 つの job の端末

1 つの job が、複数の端末を必要とすることがあります。`bajutsu run` は target ごとに `acquire_device` を呼びます。同じプラットフォームの target は 1 つの端末プールを共有し、シナリオはその target ごとに端末を 1 台持ちます。

worker は、job の target が使うプラットフォームのプールごとに借ります。web の target はブラウザーのコンテキストを操作し、端末を要しないので、worker は web のために借りません。

需要は 2 つに分かれます。**必須の需要**は、プールごとに、1 つのシナリオが宣言する target の最大数です。これより少ない端末では、1 つのシナリオも動きません。

**追加のレーン**は `--workers` から来ます。`--workers` は複数のレーンを求め、レーンごとに専用の端末が要ります。run は `--workers` を、スループットを上げるための値として扱います。`_resolve_multi_target_workers` は、端末が許すレーン数まで値を切り詰めます。完走できるだけの端末がある run を、拒否することはありません。

デバイスグループは 1 台と数えます。worker は、必須の需要を run が使う関数 `_pool_demand` で数えます。この関数は、worker と run が共に import するモジュールへ移します。関数が受け取るのは、`_TargetSetup` ではなく、target ごとの actuator です。そのため worker は、端末が 1 台もない時点でも呼べます。run も開始前に同じ需要を計算するので、run がプール不足に出会うことはありません。

借りるたびに、ブローカーへ端末を 1 台求めます。worker は、必須の需要を先に借り、追加のレーンを後で借ります。`{platform}` はプールのプラットフォームです。`{device}` は、プールの `runsOn` が名指しするモデルです。iOS では `runsOn.ios.model`、Android では `runsOn.android.avd` を使います。`model` の一覧は run を掛け算し（[BE-0468](../BE-0468-runson-family-and-any-of/BE-0468-runson-family-and-any-of-ja.md)）、各 run はプールから自分のモデルの端末を取ります。そこで worker は、一覧のモデルごとにプールの台数を借り、モデルを順に当てはめます。`family` の一覧は run のときに family を選び、モデルを名指ししないので、worker は `{device}` の組を取り除きます。

worker は、借りたアドレスを 1 つの `--udid` の一覧にして渡します。run はすでに `--udid` をカンマ区切りの一覧として読みます。各エントリーは、プールのプラットフォームを接頭辞（`ios=` または `android=`）として持ちます。

```
--udid ios=AAAA-1111,ios=BBBB-2222,android=emulator-5588
```

ブローカーが返すアドレスは、すべてこの接頭辞を通ります。`192.0.2.7:5555` のようにコロンを含むアドレスも、そのまま残ります。接頭辞は最初の `=` で終わるからです。

`leased` provider の「すでに借りた」経路は、target のプラットフォームを読みます。そのプラットフォームの接頭辞を持つエントリーと、接頭辞のないエントリーを残します。接頭辞を外し、残りをカンマで結びます。こうして target には、自分のプラットフォームのアドレスだけが見えます。同じプラットフォームの 2 つの target には、同じ一覧が見えます。そのため `_reject_incompatible_actuator_sharing` は、端末の spec が 1 つだと判断して通します。

未知の接頭辞のエントリーは `ValueError` です。プラットフォームのエントリーが 1 つもない target も `ValueError` です。メッセージはどちらもプラットフォームを挙げます。接頭辞のないエントリーはすべてのプラットフォームに効くので、手で書いた `--udid` も動き続けます。

`_reject_unshareable_groups` は、worker の端末を使い始める前に動きます。生の `--udid` を `capabilities_for_run` に渡します。同じヘルパーを通して、target のプラットフォームのエントリーを読みます。

限界が 1 つ残ります。接頭辞を読むのは `leased` provider だけです。別の provider を持つ target には、生の一覧が見えます。`leased` worker でそうなるのは web の target だけです。web 以外で `platforms` にないプラットフォームを要する job は、この worker に届かないからです。web の target のレーンは `--workers` から決まり、この一覧を読みません（`_resolve_lanes`）。

すべての端末リースの `environment` は、子プロセス用の 1 つの対応表にまとめます。同じキーに別の値を返す 2 つのリースは、1 つのプロセスの環境変数を共有できません。例は、エミュレーターごとに違う adb サーバーの port です。その場合、worker は持っているリースをすべて返し、キー名を挙げたインフラ起因の理由で job をすぐ失敗させます。辞退はしません。再試行しても同じ衝突になるからです。

必須の需要の借り方は all-or-nothing です。どれか 1 つの取得が辞退したら、worker は借りた分を返してから、job を辞退します。job は、必須の需要をすべて持つか、1 台も持ちません。

追加のレーンは、借りられる範囲で借ります。`--workers` の倍数を上限に、レーンを 1 つずつ借ります。レーンごとの借り方は、プールをまたいで all-or-nothing です。ブローカーが貸さないレーンに当たったら、追加の借り入れを終えます。worker は持っている分を保ちます。足りなくても job は辞退しません。

worker は `--workers` をそのまま渡します。run は、受け取った端末の数までレーンを切り詰めます。端末が少ない local worker と同じです。

借りる処理は、`_run_with_heartbeat` が Heartbeat を送るスレッドで動きます。`claim_check` が通り、bundle の取得が終わってから始めます。それより早く借りると、ダウンロードの間も端末を握ることになります。

Heartbeat が始まる前に借りると、借りている最中に server が job を取り戻します。2 つの worker が同時に run を動かすことになり、bundle の取得がすでに踏んだ不具合と同じです。

worker は、借りる合間に `lost` を確かめます。フラグが立っていたら、持っている端末を返し、取得のときと同じく、run を始めずに戻ります。

借りる時間の合計にも上限を掛けます。端末 3 台の job は、そのままでは `acquireTimeout` の 3 倍を待ちえます。これは job リースのタイムアウト（`DEFAULT_LEASE_TIMEOUT_SECONDS`、デフォルトは 120 秒）を超えます。

worker は、job 全体で 90 秒の借りる予算を持ちます。各取得は、`acquireTimeout` と残りの予算のうち短いほうだけ待ちます。予算が尽きたとき、必須の需要が満たされていなければ job を辞退します。そうでなければ、追加の借り入れを終えます。

### run が端末を使う流れ

worker は job ごとに子プロセスで run を動かし、子プロセスは worker の環境変数を継承します。そこで worker は、まとめた `environment` を子プロセスに渡し、`--udid <アドレスの一覧>` も渡します。現在の `_with_worker_config` と同じく、worker 自身の `--worker-config` も子プロセスに引き渡します。

job の spec は、すでに端末を名指ししていることがあります。run リクエストの `udid` は、argv に `--udid` を入れ、同じ端末を spec の `udids` に入れます。`_boot_devices` は、子プロセスを起動する前に `udids` を起動します。`leased` worker で、これらの端末を借りたわけではありません。そこで worker が、spec が持ち込んだ `--udid` を argv から取り除きます。`_with_worker_config` が `--worker-config` を取り除くのと同じです。`udids` も起動しません。子プロセスに届くのは、借りたアドレスだけです。

`leased` provider は、`requested_udid` が空でも `booted` でもなければ、すでに借りた端末として扱います。target のプラットフォームのエントリーを、何もしない `release` つきの通常のリースとして返します。子プロセスは `leased` provider を解決してアドレスを見つけ、何も借りません。端末リースはすべて worker が持ちます。

`--udid` なしの `bajutsu run` の直接実行は、自分で借ります。target ごとにブローカーへ端末を 1 台求めます。そのため、プラットフォームごとに target が 1 つまでのシナリオにしか使えません。同じプラットフォームの 2 つの target は別々の端末を解決し、`_reject_incompatible_actuator_sharing` が終了コード 2 で run を止めます。

複数のレーンが要る直接実行は、手で借りて `--udid` に渡します。同じプラットフォームに target が 2 つある run も同じです。

ブローカーの adb サーバー port は、`adb` がすでに読む経路である環境変数を通って届きます。adb の層に新しい設定は要りません。

### run の寿命

worker は子プロセスが動く間、job リースの Heartbeat をすでに送っています。そのため、端末リースは job リースと同じ長さで生き続けます。子プロセスがクラッシュしても、制御は worker に戻ります。worker は、run の証跡をアップロードするのと同じ `finally` で端末を返します。

worker が止まると、契約の 4 点目がすべてのリースを終えます。ただし、それだけでは足りません。worker は、自分から切り離して子プロセスを起動します（`_run_job` の `start_new_session=True`）。子プロセスは、worker への `SIGKILL` を生き延びます。すると、孤児になった run がまだ操作している端末を、ブローカーが貸し直してしまいます。

そこで子プロセスは、親を見張ります。one-shot の呼び出し元には、この仕組みがすでにあります。`_run_inherited` は、子プロセスが動く間、pipe の書き込み側を持ちます。子プロセスは、`PARENT_FD_ENV` が指す反対側を `_watch_parent`（`bajutsu/run/internal.py`）で読みます。終端に達したら、呼び出し元が終了したということです。子プロセスは自分に `SIGTERM` を送ります。

worker が通るのは `_run_job` の pipe 経由の経路で、いまはそこに pipe がありません。この提案は、その経路の `run` job に同じ pipe を渡します。worker が書き込み側を持ち、子プロセスが終わったら閉じます。子プロセス側は変えません。

止まり方は、`SIGTERM` がすでに意味する cancel です（[BE-0370](../BE-0370-graceful-run-cancel/BE-0370-graceful-run-cancel-ja.md)）。run は安全な境界で巻き戻り、通常の後始末を動かします。cancel のときと同じく、manifest と証跡を書きます。届いたシナリオは `failure="cancelled"` で終わります。この停止は、どのステップにも pass や fail を与えません。止まった worker は何もアップロードしません。server は、job リースが失効したときに job を取り戻し、もう一度 queue に入れます。

限界が 1 つ残ります。後始末は、それ自身の期限まで続きます。ブローカーは、worker が止まった時点でリースを終えます。その間、run は、ブローカーが貸し出しうる端末をまだ操作できます。この間隔には上限がありますが、なくなりはしません。

### ブローカーに端末がないとき

worker は、ブローカーに端末があるかを知る前に、server から job を claim します。claim を省くことはできません。どの worker がどの job を取るかは server が決めるからです。claim を持ち、端末を持たない worker は、claim を返す必要があります。

いまの server が job を取り戻すのは、worker の Heartbeat が途絶え、job リースのタイムアウト（デフォルトは 120 秒）が過ぎたときだけです。辞退のたびにこれだけ待つと、後ろに並ぶ job すべての時間を無駄にします。しかも job リースの失効は job の試行回数（3 回）を 1 回消費するため、3 回目の辞退で job が失敗します。

そこで `POST /api/worker/decline` を追加します。リクエストは、job と、worker が持つ job リースと、理由を名指しします。server は、claim されなかったものとして job を queue に戻します。`/api/worker/result` と同じように、server はリースの持ち主を検証します。別の worker の job は辞退できません。

辞退した job は、再試行の回数も job リースの試行回数も消費しません。シナリオは一度も動いていないからです。server は、辞退の回数と、最初の辞退の時刻と、最新の理由を記録します。辞退ウィンドウが過ぎたら、job を queue に戻すのをやめます。ウィンドウは server の設定 `BAJUTSU_DECLINE_WINDOW_SECONDS` で、`BAJUTSU_LEASE_TIMEOUT_SECONDS` と同じ流儀です。デフォルトは 1 時間です。

混雑したブローカーが、ウィンドウの中で job を失敗させることはありません。ウィンドウを過ぎた job は、ブローカーの最後のメッセージを挙げたインフラ起因の理由で失敗します。この上限があるので、「端末がいつまでも空かない」状況が見える失敗になります。誤設定のブローカーが job を queue に縛り続けることもありません。

辞退した job は、元の `created_at` を保ちます。リースの走査 `_lease_candidates` は、古い順に job を並べます。何もしなければ、すべての `leased` worker が poll のたびにその job を claim し直します。若い job はすべて、その後ろで待たされます。

そこで、辞退の route は、job の `not_before` へ時刻を書き込みます。あわせて、リクエストが名指しする、辞退した worker の environment を記録します。

リースの走査は、`not_before` を過ぎるまで、queue の job を飛ばします。飛ばす相手は、記録した environment の worker だけです。走査は、いまの広告 token と同じく、リースのリクエストからこの environment を読みます。空いている `local` worker は、すぐにその job を取れます。

遅延は 2 秒から始まります。2 秒は、デフォルトの poll 間隔です。辞退が重なるごとに 2 倍へ増え、60 秒で止まります。

辞退した job が待つ間も、若い job は動きます。端末のない worker が、claim と辞退を高速で繰り返すことはありません。ウィンドウいっぱい辞退され続けた job は、約 65 回の claim を費やします。2 秒の固定間隔で再試行すれば約 1,800 回です。claim のたびに、リースコマンドを 1 回動かします。

### 広告する内容

`leased` worker は、local worker と同じ経路で routing されます。`worker.yaml` の `platforms` が挙げるプラットフォームの token を広告します。`environment` を持たない通常の job が、この worker に届きます。`maxJobConcurrency` は local worker と同じく 1 のままで、`--machine-id` も local worker と同じく受け取ります。違うのは device provider だけです。

`appium` worker は異なります。プラットフォームの token を広告せず、`environment: appium` を名指しする job だけを受けます。グリッドが端末とそのホストを持つからです。`leased` worker は、自分のホストで、ふつうのシミュレーターとエミュレーターに対して driver を動かします。job から見て端末は local と区別がつきません。job が見つけるために `environment` を要することはありません。

worker は、プラットフォームを `--platform` ではなくファイルから取ります。`leased` worker は、`appium` worker と同じく `--platform` を拒否します。worker は、job が届く前にブローカーの持ち物を尋ねることができません。ファイルに挙げていないプラットフォームを要求する job は、この worker に届きません。

### 境界

**verdict。** 端末は run への入力であり、run への判定ではありません。辞退、ブローカーのエラー、端末の待ちは、どのステップの pass/fail も生みません。worker が止まって停止した run は、cancel として終わります。この停止が、ステップに verdict を与えることはありません。prime directive 1 は保たれます。

**決定性。** 端末の待ちは queue の待ちであり、シナリオ内の停止ではありません。worker は既存の間隔で server を poll し、端末を待って sleep するステップはありません。prime directive 2 は保たれます。

**アプリ非依存。** ブローカーは、ホストを記述する `worker.yaml` に名指しします。シナリオ、target、driver、runner は変わりません。prime directive 3 は保たれます。

**リポジトリにブローカーを持ち込まない。** リポジトリはブローカーを同梱せず、`bajutsu/` と `docs/` のどのファイルもブローカーを名指ししません。上の契約がインタフェースのすべてです。

### 作業分解（MECE）

**作業単位 1：** **`worker.yaml` の leased environment。** `leased` environment、`lease` ブロック、`platforms` の一覧、その検証を追加します。

`DeviceProvider` のスキーマに、リースコマンド、`acquireTimeout`、`releaseTimeout` を足します。`device_provider_for` を更新し、`is_local` を `leased` でも真にします。

worker の capability token、route token、`_device_reader` を、上の routing に合わせます。`--machine-id` の検査と `--platform` の拒否も加えます。

`_register_environment_or_exit` と `environment_refusal` が、`leased` を `local` と同じように受け付けるようにします。

**作業単位 2：** **provider と端末リースの環境変数。** `DeviceLease` の `env` フィールドと `_LeasedProvider` を、すでに借りた端末の経路も含めて追加し、登録します。

プラットフォーム付きの `--udid` 一覧を分けるヘルパーを追加します。provider と `_reject_unshareable_groups` から呼びます。

`_acquire_targets`、`_device_udid_or_exit`、`_probe_device` が、リースの `env` を適用するようにします。後の 2 つは、コマンドが終わるとき、またはツールの呼び出しが返るときに、リースを返します。スタブのリースコマンドでテストします。

**作業単位 3：** **辞退 route。** `POST /api/worker/decline` と、job ごとに保存する 5 つの値を追加します。

値は、辞退の回数、最初の辞退時刻、最後の理由です。残る 2 つは、`not_before` と、辞退した worker の environment です。辞退ウィンドウの設定も追加します。

リースのリクエストが worker の environment を名指しするようにします。`_lease_candidates` が、`not_before` の前の job を、記録した environment の worker に対して飛ばすようにします。ウィンドウを過ぎたときのインフラ起因の失敗も追加します。

**作業単位 4：** **worker への組み込み。** run が使うのと同じ関数で、job が必要とする端末を数えます。

`_pool_demand` を、worker と run が共に import するモジュールへ移します。2 つ目の複製は作りません。

プラットフォームのプールごとに、必須の需要を all-or-nothing で借ります。続けて、`--workers` の追加のレーンを借りられる範囲で借り、`--workers` はそのまま渡します。

借りる処理は Heartbeat を送るスレッドで動かし、借りる合間に `lost` を確かめ、合計を借りる予算の内に収めます。

環境変数をまとめ、接頭辞つきのアドレスを `--udid` に結合します。`finally` で返します。spec が持ち込んだ `--udid` と `udids` を取り除きます。

必須の取得が辞退したときは、job を辞退します。環境変数が衝突したときは、job をすぐ失敗させます。

**作業単位 5：** **孤児になった run の停止。** `_run_job` の pipe 経由の経路で起動する `run` job へ、`_run_inherited` の親 pipe を渡します。

書き込み側が閉じたとき、子プロセスが自分を cancel することをテストします。run が manifest を書くこともテストします。

**作業単位 6：** **ドキュメントと日本語ミラー。** `docs/`（worker の設定、ブローカーの契約）、`docs/architecture.md`、`docs/ja/` のミラーを更新します。

## 検討した代替案

| 代替案 | 採用しなかった理由 |
|---|---|
| worker の起動時に一度借りて、端末を持ち続ける | job の合間に端末が遊び、他の借り手を止めます。共有プールの目的に反します。job を何時間も待つ worker に端末を縛ることにもなります。端末を持ち続けるモードは、後から追加できます。 |
| ブローカーに端末がなければ job を失敗させる | 混雑したホストでは、queue の混雑がシナリオ失敗に見える赤い job に変わります。人が両者を見分けなければならず、失敗の原因はアプリの落ち度でもありません。 |
| 一定回数の辞退で打ち切る | 回数は時間と結びつきません。午後いっぱい混むブローカーでは job が赤くなり、poll が遅ければ同じ回数で何日も待ちます。壁時計のウィンドウなら、許せる待ち時間をそのまま表せます。 |
| 辞退 route を作らず、job リースを失効させる | 新しい route は要りません。ただし、辞退のたびに job がタイムアウト（デフォルトは 120 秒）を待ちます。job リースの失効のたびに、job の試行回数（3 回）も 1 回消費します。 |
| 辞退した job の queue 先頭の位置を、遅延なしで保つ | すべての `leased` worker が、poll のたびにその job を claim し直します。まだどの worker も扱えない job の後ろで、若い job が待たされます。 |
| 辞退した worker にだけ、その job の遅延を掛ける | ほかの `leased` worker は、poll のたびにその job を claim します。job は、それらの worker にとって queue の先頭に居座ります。job ごとの `not_before` なら、worker ごとの状態は要りません。 |
| `not_before` を、種類を問わずすべての worker に掛ける | environment を持たない job は、`local` worker にも `leased` worker にも届きます。混んだブローカー 1 つが、空いている local worker なら始められる job を、辞退のたびに最長 60 秒止めます。走査に件数の上限を設けないのは、取れる若い job を隠さないためです（BE-0166）。辞退した environment を記録すれば、辞退しうる worker にだけ job を隠せます。 |
| 2 つのプラットフォームを要する job を辞退する | 両方の端末を持つ local worker なら、その job を動かせます。辞退はブローカー側の都合による見かけの制限です。`--udid` の接頭辞つきエントリーで、この制限をなくせます。 |
| 2 つのリースが同じ環境変数のキーに別の値を返したとき、job を辞退する | 再試行しても同じ衝突になります。job はウィンドウいっぱい待つだけで、何も変わりません。キー名を挙げてすぐ失敗させるほうが、早い答えです。 |
| worker が止まったとき、契約の 4 点目だけに任せる | run の子プロセスは worker から切り離されて起動されるので、worker を生き延びます。ブローカーは、孤児がまだ操作している端末を貸し出します。親 pipe が子プロセスを止めます。 |
| worker の隣に、孤児を止める別の見張りプロセスを置く | 自分で止まりうるプロセスが 1 つ増えます。pipe は、worker が止まれば理由を問わず閉じるので、追加のプロセスは要りません。 |
| ブローカーの Web インタフェースと直接やり取りする | ブローカーごとに、クライアント、認証情報、スキーマが要り、コードに製品名が入ります。コマンドの契約なら、1 行を出して動き続けられるブローカーなら何でも使えます。認証情報もブローカー自身のコマンドの中に残ります。Web のインタフェースは、2 つ目の provider kind として後から追加できます。 |
| 子プロセスの run の中で、`run` 自身の provider 呼び出しだけで借りる | worker は、始められない job の claim を持ったまま、claim を返す手段を持ちません。辞退を受けて動けるのは worker なので、借りる処理は worker が辞退に対処できる場所に置きます。 |
| 必須の端末ごとに別々に辞退できる手順で借りる | job が端末の一部を持ったまま、残りを待つことになります。2 つの job が、互いの必要分の半分ずつを握り合うこともあります。必須の需要を all-or-nothing で借りれば、このデッドロックを防げます。追加のレーンは、なくても job が動くので、この対象に入りません。 |
| `--workers` の倍数を all-or-nothing の需要に含める | スループットのための値が、必須の条件に変わります。混んだブローカーでは、`--workers 8` の job が辞退ウィンドウいっぱい辞退され、最後に失敗します。端末 2 台を持つ worker なら、2 レーンで動かせた job です。run はすでに `--workers` を手元の端末数に切り詰め、拒否はしません（`_resolve_multi_target_workers`）。 |
| `local` provider に、任意のリースコマンドを足す | 1 つの kind が 2 つの振る舞いを持ち、`local` が「このホストに接続された端末」を意味しなくなります。kind を分ければ、登録簿の「1 つの kind に 1 つの振る舞い」という規則を保てます。 |

## 進捗

> 作業の進行に合わせて、このセクションを最新に保ちます。チェックリストは、*詳細設計*の MECE な
> 作業分解（作業単位ごとに 1 項目）に対応します。ログには、何がいつ変わったかを古い順に、PR
> へのリンクつきで記録します。

- [x] `worker.yaml` の leased environment
- [x] provider と端末リースの環境変数
- [x] 辞退 route
- [x] worker への組み込み
- [x] 孤児になった run の停止
- [x] ドキュメントと日本語ミラー

ログ：

[#2565](https://github.com/bajutsu-e2e/bajutsu/pull/2565) で 6 つの作業単位をすべて実装しました。実装が設計と異なる点は、次の表の
とおりです。

| 対象 | 実装した内容と理由 |
|---|---|
| 台数の数え方 | worker は job のディレクトリで `python -m bajutsu.run.demand` を実行。相対パスを run と同じように解決するため |
| 混在するモデル指定 | モデル指定と並んで family や OS の条件があれば、broker が選ぶ 1 台を追加 |
| fake ドライバ | `leased` の run でも借用なし |
| `record`、`crawl`、`--once` | 自分で借り、辞退の対象外 |
| 追加レーンの値の衝突 | そのレーンを返して job を実行。`--workers` はスループットの指定にすぎないため |
| lease の途中終了 | job のログに記録し、run は継続 |

## 参考

- `bajutsu/common/runner/device_provider/`：provider の枠組みと、組み込みの 3 つの kind。
- `bajutsu/common/capability/worker_capability.py`：`device_provider_for` と `worker.yaml` のスキーマ。
- `bajutsu/common/config/schema/device_provider.py`：リースコマンドを足す `DeviceProvider` のスキーマ。
- `bajutsu/serve/cli/worker.py`：job リース、Heartbeat、capability の token。
- `bajutsu/serve/server/one_shot.py`：`environment_refusal`。
- `bajutsu/serve/server/db/sql_repository.py`：リースの走査 `_lease_candidates`。
- `bajutsu/serve/jobs.py`：子プロセスを起動し、親 pipe を持つ `_run_job` と `_run_inherited`。
- `bajutsu/run/internal.py`：その pipe の子プロセス側 `_watch_parent`。
- `bajutsu/serve/server/worker_job.py`：worker のファイルを子プロセスに引き渡す `_with_worker_config`。
- `bajutsu/run/cli.py`：`_acquire_targets`、レーンの解決、プールの需要。
- [BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction-ja.md)：device provider の抽象化。
- [BE-0370](../BE-0370-graceful-run-cancel/BE-0370-graceful-run-cancel-ja.md)：`SIGTERM` が始める穏やかな cancel。
- [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md)、[BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md)：`worker.yaml` と、そこで宣言する environment。
- [BE-0468](../BE-0468-runson-family-and-any-of/BE-0468-runson-family-and-any-of-ja.md)：`runsOn` の一覧が run を掛け算する仕組みと、family を選ぶ仕組み。
- [BE-0484](../BE-0484-job-group-lease/BE-0484-job-group-lease-ja.md)：job のグループに対して worker が持つ job リース。

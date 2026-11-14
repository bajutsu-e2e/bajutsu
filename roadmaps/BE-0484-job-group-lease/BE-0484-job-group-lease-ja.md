[English](BE-0484-job-group-lease.md) · **日本語**

# BE-0484 — job をグループとして登録し、worker がグループ単位でまとめて lease する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0484](BE-0484-job-group-lease-ja.md) |
| 提案者 | [@paihu](https://github.com/paihu) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0484") |
| 実装 PR | [#2417](https://github.com/bajutsu-e2e/bajutsu/pull/2417)（単位 1）、[#2426](https://github.com/bajutsu-e2e/bajutsu/pull/2426)（単位 2）、[#2431](https://github.com/bajutsu-e2e/bajutsu/pull/2431)（単位 3）、[#2443](https://github.com/bajutsu-e2e/bajutsu/pull/2443)（単位 4）、[#2445](https://github.com/bajutsu-e2e/bajutsu/pull/2445)（単位 5） |
| トピック | Device-cloud execution |
| 関連 | [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out-ja.md), [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch-ja.md), [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md), [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md), [BE-0465](../BE-0465-serve-ui-device-farm-fan-out/BE-0465-serve-ui-device-farm-fan-out-ja.md), [BE-0468](../BE-0468-runson-family-and-any-of/BE-0468-runson-family-and-any-of-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本項目は、`run-set` の役割と、worker が `run-set` を消費する方法を変えます。`run-set` は job の**グループ**を登録するだけにします。worker は同じグループの job を1回の要求でまとめて lease します。共有する package とアプリのバイナリは1回だけ upload し、job は並列に実行します。1つのグループを複数の worker が分担でき、各 worker は空きスロットの数だけ job を取ります。要求元が指定するのはプラットフォーム（`ios`、`android`、`web`）だけで、実行環境は指定しません。サーバは、そのプラットフォームを担当できる worker に job を回します。worker がローカルのデバイスを操作するか、デバイスクラウドへ submit するかは問いません。

現在の `run-set` も、target をシナリオごとに1つの job へ展開します。ただし各 job は互いに独立しています。Device Farm の worker は job を1件ずつ lease し、job ごとに package を作り、同じバイナリを毎回 upload します。要求元も `environment: devicefarm` を明示しなければなりません。本項目はシナリオごとの job と、job ごとの結果を残します。job の上にグループという単位を足し、job が共有する作業を worker ごとに1回で済ませます。

## 動機

[BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out-ja.md) は、シナリオを並列に走らせるため、スイートをシナリオごとの Device Farm run に分けました。[BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch-ja.md) は、そのディスパッチを worker に移し、worker が最大 `maxJobConcurrency` 件の job を lease するようにしました。この分割で並列性は得られましたが、次の4つのコストが残っています。

- **共有できる作業が job ごとに繰り返されます。** Device Farm provider は、job ごとに package を作り、アプリのパスを渡して `submit_and_collect` を呼びます。1つの target に対する40シナリオの `run-set` は、同じバイナリを40回 upload します。package に入る config とシナリオも、毎回同じ bundle です。
- **要求元が実行場所を知っている必要があります。** 現在の要求は、バッチ provider を指す `environment` を必須とします。GitHub Actions の job のような呼び出し元は、「これらを Android で走らせたい」とだけ言い、配置は運用者に任せたいはずです。[BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md) は環境を run ごとの選択にしましたが、その選択がすべての呼び出し元に降りかかっています。
- **大きな集まりは登録の時点で途中までになります。** サーバ全体、ユーザーごと、org ごとの同時実行上限は、待機中の job を数えます。展開が上限に達すると、サーバは登録をやめ、そこまでの job を status 200 で返します。残りのシナリオはキューに入りません。
- **job の集まりに識別子がありません。** レスポンスは `jobIds` を返すだけで、集まりを指す名前がありません。呼び出し元は集まりの状態を問い合わせられません。再試行された要求は同じシナリオを二重に登録しますが、重複を検出する手段もありません。集まりを pull request の1つの check として公開したい場合も、結びつけるキーがありません。

出荷後に確認できる違いは4つあります。設定と binary が同じ N シナリオの `run-set` では、Device Farm の worker が同じグループの job を保持し続けているあいだ、binary を upload するのは1回だけです。現在は N 回です。`run-set` の要求は環境を指定せず、そのプラットフォームを担当する worker が実行します。同時実行上限を超える `run-set` は、全シナリオを登録して順番に実行します。同じグループキーを持つ2回目の要求は、既存のグループを返し、新しい job を登録しません。

## 詳細設計

### 用語

- **グループ**：1回の `run-set` 要求が登録する job の集まりです。グループは、呼び出し元が選べる**グループキー**と、サーバが割り当てる**グループ id** を持ちます。
- **実行意図**：job が何を走らせたいかを、場所と切り離して表したものです。target、シナリオ、config、アプリの参照、プラットフォームを指します。
- **互換キー**：worker が job 間で共有できるものを表すダイジェストです。config のバイト列、アプリ binary のダイジェスト、プラットフォーム、target 名を含みます。job ごとに異なるシナリオは含みません。1回の要求がこの4つの入力をすべて決めるので、1つのグループが持つ互換キーは1つです。
- **ルートキー**：job の `capabilities` のリストのダイジェストです。ルートキーが同じ2つの job は、`can_serve` の判定結果が同じになります。
- **基底キー**：route トークン（`cap:` と `setting:`）を除いて取った同じダイジェストです。route を確かめない worker は、基底キーが同じ2つの job に同じ判定結果を返します。
- **バンドル**：1つの worker が1回の要求で lease する job です。バンドルの job はすべて同じグループに属します。1つのグループは、複数の worker にまたがる複数のバンドルに分かれることがあります。

### グループの登録

`POST /api/run-set` の body は、2点を除いて変わりません。`environment` を受け付けなくなり、任意のフィールド `groupKey` が1つ増えます。レスポンスは `jobIds` を残し、`groupId` を加えます。

- グループキーは、`[A-Za-z0-9._/:-]` の文字からなる128文字以内の文字列です。呼び出し元の org の中で一意です。呼び出し元がキーを送らない場合は、サーバが生成します。
- `environment` を送った要求は、status 400 で拒否します。現在の `_run_set_refusal` が `deviceBudget` や override のフィールドを拒否するのと同じ扱いです。エラーには本項目を示します。どの worker が job を実行するかは、要求が指定することではありません。
- 登録は不可分です。要求の全シナリオが待機中の job になるか、要求が拒否されてグループが1件も作られないかのどちらかになります。グループの行とすべての job の行は、シナリオのファイルを保存したあとに、store の1つのトランザクションで書きます。store のエラーが起きると、書き込み全体をロールバックし、そのエラーで要求に応答します。そのため、途中まで書かれたファンアウトは存在せず、同じキーでの再試行が、それを完全なグループと取り違えることはありません。グループの job は `try_register` を通りません。id はトランザクションの前に採番し、どのグループの job も commit の前に registry へ入りません。同時実行上限はグループを途中で打ち切りません。後の節で述べるとおり、上限は lease の時点で効くからです。同じシナリオを2回以上挙げた要求は、status 400 で拒否します。重複は、要求の文字列ではなく、各名前を解決したシナリオのファイルで判定します。`runnable` はファイル名だけを残すので、`a.yaml` と `x/a.yaml` は同じシナリオを指します。そのため、グループの job の数は、target が持つ異なるシナリオの数を超えません。
- キューを持たないサーバは、現在どおりすべての `run-set` を拒否します。1台のマシンでの起動はデフォルトでキューを持つので、この拒否が及ぶのはキューなしで起動したサーバだけです。
- 同じキーと同じ要求ダイジェストを持つ要求を繰り返すと、既存のグループを status 200 で返し、job は登録しません。同じキーで異なるダイジェストの要求は、status 409 で拒否します。ダイジェストは、target、解決したシナリオ名、config のダイジェスト、アプリのダイジェスト、run のラベルを含みます。
- GitHub Actions の呼び出し元は、`GITHUB_RUN_ID`、`GITHUB_RUN_ATTEMPT`、`GITHUB_JOB`、`strategy.job-index`（matrix の leg の0から始まる番号）からキーを組み立てます。matrix の値そのものは、空白やオブジェクトを含むことがあり、キーの文字種が受け付けません。`GITHUB_RUN_ID` だけでは足りません。workflow を re-run しても run id は変わらず、attempt だけが変わるためです。`GITHUB_JOB` だけでも足りません。`strategy.matrix` のどの leg も同じ値を持つからです。matrix でファンアウトすると、leg ごとにダイジェストの違う同じキーを送ることになり、最初の leg 以外はすべて 409 になります。このキーが冪等にするのは、1回の attempt の中で再試行された要求です。意図して re-run した場合は、新しいグループを求めたものとして、新しいグループが作られます。
- 新しいテーブル `job_groups` に、org、キー、要求ダイジェスト、作成時刻を持たせ、org とキーに一意インデックスを張ります。単位 1 が org、キー、作成時刻の列を作り、単位 3 がダイジェストの列と一意インデックスを加えます。job レコードには `group_id`、`compat_key`、`route_key`、`base_key` の列を足します。他の経路で登録した job は、4つとも `NULL` にします。後述の window 関数は `group_id` を持つ行にだけ適用するので、通常の job が1つの組にまとめられることはありません。lease のレスポンスは `compat_key` を返し、worker はそれを upload のキャッシュのキーに使います（単位 4）。
- GitHub Actions の呼び出し元は machine のプリンシパルです（BE-0414）。`forbidden_for_machine` は、名前を挙げていないルートをすべて拒否します。単位 1 で、`POST /api/run-set` を `_MACHINE_PATHS` に加えます。`GET /api/run-set/<groupId>` は、`_is_machine_job_route` が `/api/jobs/<id>` を通すのと同じ形の照合で加えます。グループのキャンセルは、job のキャンセルと同じく machine には閉じたままにします。今は machine が job をキャンセルできないからです。開く2つのルートは、`POST /api/run` と同じく、リクエストの文脈から `machine_org` を受け取ります。登録のルートは provenance のために `ci_job` も受け取ります。グループの org と、参照での org の照合は、`state.org_for` で決めます。`machine_org` がないと、machine の呼び出しはすべて `default` の org に解決され、別のテナントのグループに届いてしまいます。
- `GET /api/run-set/<groupId>` は、呼び出し元自身の org のグループについてだけ、グループの job とその状態を返します。別の org のグループには、403 ではなく 404 を返します。BE-0015 に従い、他の org 単位の読み取りと同様、存在を漏らさないためです。報告側が poll する読み取り経路です。結果を GitHub の check として公開する処理は別の関心事であり、範囲外とします。
- `POST /api/run-set/<groupId>/cancel` は、現在の job 単位のキャンセルを、グループの未完了の job すべてに適用します。待機中の job はその場で `cancelled` になり、どの worker も lease しません。lease 済みの job にはキャンセルの印が付き、worker が次の heartbeat でそれを受け取って止めます。現在の job のキャンセルと同じ動きです。このルートは `POST /api/jobs/<id>/cancel` と同じ権限を求めます。`required_role` は `/api/jobs/` の接頭辞と `/cancel` の接尾辞でそのルートを判定するので、グループのキャンセルにも同じ形の規則が要ります。規則は `path.startswith("/api/run-set/") and path.endswith("/cancel")` です。完全一致で照合する `_EDITOR_PATHS` の集合に項目を足しても、具体的なグループ id には一致しません。規則がないと、viewer でもグループをキャンセルできてしまいます。呼び出し元自身の org のグループだけを扱い、別の org のグループには 404 を返します。未完了の job を持たないグループへのキャンセルは 200 を返し、何も変えません。

### どの環境でも実行できる job

現在の `run-set` の job は `BatchRequest` を持ち、登録の時点で provider を固定します。ローカルの `bajutsu run` の job は、代わりにコマンドラインとデバイスの引数を持ちます。どちらの形も、もう一方の種類の worker では実行できません。そこでグループの job は実行意図だけを持ち、job を lease した worker が実行意図を自分の形に変換します。

- ローカルの worker は、自分のデバイスでそのシナリオを走らせる `bajutsu run` の呼び出しを組み立てます。サーバが通常の run の job に使う組み立て関数（`run_command`）を呼びます。実行意図は、サーバだけが決められる値を運びます。値は次の4つです。
  - アップロードされた bundle の `launchServer` コマンドを制御する upload の実行ポリシー（BE-0090）
  - org に保存された baseline をワークスペースに展開するかどうか（BE-0073。upload された bundle では展開せず、`--baselines` も渡しません）
  - ラベル
  - provenance
- デバイスクラウドの worker は、自分の provider の種別で `BatchRequest` を組み立てて submit します。

アプリの参照は、[BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch-ja.md) がすでに求めているとおり論理的な参照のままにします。web の target はアプリを必要としないので、サーバは web の target に `subject.path` を求めません（単位 5）。Android または iOS の target には引き続き `subject.path` が必要で、持たない target は現在どおり status 400 で拒否します。そのプラットフォームを担当するデバイスクラウドの worker が lease すると、インストールするものがないからです。それ以外のプラットフォームの target は、現在どおり status 400 で拒否します。

### プラットフォームによるルーティング

グループの job は、通常の job と同じ方法で回ります。`capabilities` は all-of のリストで、worker はすべてのトークンを広告していれば job を担当できます。job は run に必要なものを挙げ、各 worker は自分が提供できるものを広告します。要求は配置を指定せず、サーバも配置を書きません。

グループの job は、次のトークンを要求します。

- プラットフォームのトークンです。`platform:ios`、`platform:android`、`platform:web` のいずれかです。
- iOS の target では、BE-0468 のデバイスファミリーのトークンです。
- iOS の target では、run が取る kind ごとの `kind:simulator` または `kind:device` のトークンです。ファミリーを指定しているかどうかは問いません。
- シナリオ自身の `cap:` と `setting:` のトークンです。シナリオの本文から読みます（BE-0467）。
- 実行意図のトークンです。本項目の worker だけが広告します。

グループの job は `machine:<id>` のトークンを持ちません。すべてのグループの job は、config のディレクトリの bundle か materials として、自分のファイルを運ぶので（BE-0465）、サーバ上のパスを指す job はありません。登録は、最初のシナリオだけでなく、job ごとにこれを確かめます。

`host:` のトークンは要求しません。driver が必要とするホストは、ローカルの worker の性質だからです。BE-0450 以降、ローカルの worker は自分のホストで動かせるプラットフォームだけを広告するので、プラットフォームのトークンがホストの条件も兼ねます。これは、1つのプラットフォームの actuator がすべて同じホストに結び付いていることを前提にしています。この前提を崩す actuator を足すときは、`host:` のトークンを戻す必要があります。非推奨の `--capabilities` の上書きにはまだ `platform:ios` を入れられるので、単位 5 でこの上書きに `platform:` と `kind:` を予約します。

kind のトークンは、target が宣言した kind 以外で target が走らないようにします。これがないと、ファミリーを指定しない iOS の target は kind について何も要求しません。その場合、Simulator 向けの target を、デバイスクラウドの worker が実機で走らせてしまいます。kind は、`_family_requirements` がすでに求めているものを使います。target が挙げる `kind` か、挙げない場合は target の driver から決まる kind（`driven_kind`）です。そのため、デバイスクラウド向けの iOS の target は、スカラー値の `kind: device` を宣言します。`driven_kind` が driver を通して読むのは、この kind です。スカラー値の kind は target の駆動の仕方を表すので、`unpack_conditions` は条件に数えません。リストで挙げた `ios.kind` は条件であり、デバイスクラウドは submit の時点で拒否します。そうした target のビルドは、もともと実機にインストールできなければならず、Simulator 向けのビルドではインストールできません。両方の kind を挙げる target は、両方を持つローカルの worker にだけ回ります。通常の job は kind のトークンを要求しません。デバイスクラウドは、ほかの `runsOn` の条件も submit の時点で拒否します（`_check_no_conditions`）。対象は `ios.model`、`ios.os`、`android.avd`、`android.apiLevel` です。スキーマは、実機の kind に対する `ios.model` と `ios.os` をすでに拒否しているので、iOS のデバイスクラウド向け target が宣言できる条件として残るのは、リストで挙げた `ios.kind` です。本項目は、これらの条件にトークンを足しません。そうした条件を宣言する target のグループの job は、デバイスクラウドの worker に回り、submit で失敗して試行を1回消費することがあります。現在も、そうした target の `run-set` は、要求が batch provider を指定する必要があるので、毎回この形で失敗します。本項目はこのケースを悪化させず、そうした要求の一部はローカルの worker で成功するようになります。

ローカルの worker は、インストール済みの Simulator の端末があれば `kind:simulator` を広告します。iPhone か iPad がつながっていて、`worker.yaml` が `realDevices: false` でないあいだは、`kind:device` を広告します。`kind:simulator` は、`iosNN` や `iphone` / `ipad` のトークンを得る起動時の読み取りと同じ読み取りで決めます。`kind:device` は、実機のトークンと同じく、lease のたびに読み直します。

デバイスクラウドの worker は、広告するものを変えます。現在は `platform:*` のトークンの代わりに `environment:<provider>` を広告しています（BE-0448）。本項目では、provider が担当するプラットフォームも広告します。デバイスファミリーのトークンは広告しません。provider のデバイスの選択は、プラットフォームだけで端末を絞り込むからです。そのため、ファミリーを指定する target（`runsOn.ios.family`）は、それを守れる worker にだけ回ります。現在は `environment: devicefarm` でそうした target も Device Farm の worker に渡り、worker は submit の時点で拒否します。どの pool も `runsOn` の条件に一致しないからです（`_check_no_conditions`）。ファミリーのトークンは、この失敗を routing に移すので、job は試行を消費せず、守れる worker を待ちます。デバイスクラウドの worker は、予約する端末がすべて実機なので、`kind:device` を広告します。

BE-0448 の環境の規則は、`environment:*` を広告する worker を、そのトークンを要求しないすべての job から遠ざけます。この規則は、実行意図のトークンを要求する job には適用しません。それ以外の job には引き続き適用するので、デバイスクラウドの worker が、ローカルのデバイスを起動する通常の run を lease することはありません。`appium` のグリッドの worker（BE-0452）は `platform:*` のトークンを広告しないので、グループの job を取りません。グリッドだけのデプロイは、通常の run の要求でシナリオを実行し続けます。

サーバは、通常の run と同じく、job ごとのリストに `route_refusal` を行います。稼働中の worker の capabilities は要求ごとに1回だけ読み、ルートの要求はシナリオの文面ごとに1回だけ計算します。それでも、異なるシナリオを1件ずつ解析するので、登録のコストはシナリオの数に比例します。このコストは受け入れます。コストがかかるのは1つの要求であり、行を待機に入れる前だからです。1件の job について次の3つがすべて成り立つと、要求全体を status 400 で拒否します。

- 稼働中の worker がその job の経路を担当しています。
- そのどの worker も、job の `cap:` または `setting:` のトークンのどれかを欠いています。
- 稼働中のどの worker も job を担当できません。

登録は不可分なので、この拒否はグループ全体を取り下げ、レスポンスはそのシナリオと足りないトークンを示します。それ以外の場合、稼働中のどの worker も担当できない job は、待機したまま残ります。

worker が job を担当できるかを判断する箇所は、次の5つです。

- `lease_job` の lease の絞り込み
- lease 時のスロットの計数
- routing できない job の計数
- worker 不在の通知（`lacks_live_worker`）
- `route_refusal`

5つとも `can_serve` を通り、`can_serve` の変更は上の例外だけです。そのため5つの判断は、BE-0468 が自身のトークンに求めるとおり一致し続けます。worker の claim 時の再確認（`claim_refusal`、BE-0467）は、同じリストを読むので、変更は要りません。ただし、バンドルの job ごとに1回ずつ実行します。route のトークンが、worker の広告から claim までの間に消えると、バンドルのすべての job が失敗します。後述の secret store の読み取りと同じです。この拒否が判定するのは job ではなく worker なので、どのスロットも同じ結果になり、失敗のたびにその job の試行が消費されます。

job を lease した worker が実行場所を決め、最初に lease した worker が勝ちます。このため、シナリオが走る場所は固定されず、同じシナリオがシミュレータでは通り、クラウドのデバイスでは落ちることがあります。原則2が扱うのは待機とセレクタであり、判定は引き続きアサーションだけで決まります。それでも残るのは再現性のコストです。そのため run は、実行した worker の環境を provenance に記録し、レポートがそれを表示します。呼び出し元は配置を固定できません。配置を固定したい運用者は、デプロイで動かす worker の構成で制御します。

### バンドルの lease

lease 要求に `maxJobs` を足します。worker の空きスロット数、つまり `maxJobConcurrency` から実行中の job 数を引いた値です。要求が `maxJobs` を持つ場合、レスポンスは lease した job のリストで、各要素は現在の job 1件分の形です。持たない場合、レスポンスは現在の単一のオブジェクトのままなので、本項目より前の worker も通常の job は lease できます。実行意図を持つ job は、本項目の worker だけが広告するトークンを追加で要求するので、古い worker は lease しません。古い worker が lease すると、job を待機に残さず失敗させてしまいます。デバイスクラウドの worker の `environment_refusal` は `batch` を持たない spec を拒否し（BE-0448）、ローカルの worker は実行する `cmd` を見つけられないからです。

`lease_job` は、まず現在と同じ方法でアンカーを選びます。worker が担当できる最も古い待機中の job です。アンカーがグループを持つ場合は、アンカーと続く待機中の job をまとめて、合わせて最大 `maxJobs` 件かつ上限が残す余裕の範囲で claim します。アンカーに続く各 job は、アンカーと同じ `group_id` とルートキーを持つものに限ります。route を確かめない worker では、ルートキーの代わりに基底キーで照合します。アンカーと判定結果が同じなので、worker はどれも担当できます。各 claim は条件付きの `UPDATE ... WHERE status = 'queued'` です。競合に負けても、その1件を飛ばすだけで lease は失敗しません。

アンカーは担当できる最も古い job のままなので、グループ間の順序は変わりません。後続の job は他のグループの古い job を追い越すことがあり、`maxJobs` がその幅を抑えます。これは意図したコストです。upload を省ける利益のほうが、キュー内の厳密な古さの順序より大きいと判断します。

アンカーを探す走査では、job ごとではなく、`group_id` とルートキーの組ごとに1件の候補を読みます。ルートキーは、登録時に job と一緒に保存します。現在の `lease_job` は、待機中の job を古い順にすべて読み込み、1件ずつ `can_serve` で判定します。そのため、500 シナリオのグループを担当できない worker は、担当できる job にたどり着くまでに 500件を判定します。本項目では、グループに属さない待機中の job は今までどおりすべて読みます。グループの job は、組ごとに、いちばん古い待機中の1件だけを読みます。この1件は次の window 関数で選びます。

```sql
ROW_NUMBER() OVER (PARTITION BY group_id, route_key ORDER BY created_at)
```

window 関数は SQLite と PostgreSQL の両方が対応しているので、SQLite のゲートでも同じクエリを検証できます。`(status, group_id, route_key, created_at)` に index を張り、選択をデータベースの中で済ませます。Python で行う `can_serve` の判定の数は、待機中の job の総数ではなく、通常の job の数と、グループごとのルートキーの数で決まるようになります。

1件の候補が組全体を代表できるのは、同じ組の job がすべて同じ判定結果になるからです。`can_serve` の判定が読むのは job の `capabilities` のリストで、ルートキーはそのダイジェストです。上限は org と actor に対して数えますが、この2つは1回の要求で決まります。

ルートキーが要るのは、グループの job がすべて同じ要求トークンを持つわけではないからです。シナリオ自身の `cap:` と `setting:` のトークンは、シナリオの本文から決まります（BE-0467）。同じグループの2つのシナリオが、異なるトークンを要求することがあります。そうしたグループは複数の組に分かれ、組ごとに `can_serve` を1回呼びます。すべてのシナリオが固有の要求を持つ場合、走査のコストは現在の走査と同じです。組の判定結果は1つなので、組のいちばん古い job が、worker の担当できる新しい job を隠すことはありません。

route を確かめない worker は、ルートキーの代わりに基底キーで組を作ります。worker が route トークンを1つも広告しないとき、`can_serve` は要求集合から route トークンをすべて除きます（`checks_routes`）。デバイスクラウドの worker は route トークンを広告しません。そのため、こうした worker にとっては、基底キーが同じグループの job は、`cap:` と `setting:` のトークンが違っても判定結果が1つです。こうした worker の走査は `group_id` と基底キーで分割し、アンカーに続く job も基底キーで照合します。この走査には、`(status, group_id, base_key, created_at)` の2つ目の index を使います。シナリオごとに異なる `cap:` のトークンを要求するスイートでも、Device Farm の worker では1つのバンドルにまとまります。lease は、claim した各 job を、アンカーではなくその job 自身の capabilities のリストで `unchecked_lease` と判定します。

不変条件は、ルートキーが判定結果を左右する job ごとの入力をすべて含むことです。キーは、行に保存されるリストを digest します。入力を足す変更は、それをキーに加えなければなりません。基底キーも、route トークン以外のすべての入力について同じ規則に従います。

候補を選ぶクエリは、現在と同じくロックを取りません。window 関数を含むので、PostgreSQL は `FOR UPDATE` を付けても拒否します。claim では、選んだ組だけを対象に、window 関数を含まない2つ目のクエリを実行します。このクエリは、アンカーとそれに続く job をまとめて claim するので、バンドル全体が計数と同じトランザクションに収まります。組の待機中の job を古い順に、`maxJobs` と3つの上限が残す余裕の小さいほうの件数まで取り、アンカーもその両方に数えます。余裕を決める計数は、claim と同じトランザクションの中で行います（上限の節）。PostgreSQL では `FOR UPDATE SKIP LOCKED` でロックし、SQLite では各 claim を条件付きの `UPDATE` で行います。そのため、同じ組を選んだ worker 同士は、いちばん古い1件を奪い合わず、組の job に分散します。バンドルの job は同じルートキー（route を確かめない worker では同じ基底キー）を持ち、そうしたキーが複数あるグループは、キーごとにバンドルを埋めていきます。

capability の判定は `can_serve` だけが行うので、5つの判断箇所は1つの定義を共有し続けます。待機中のグループの job の総数には上限を設けません。lease の走査で行う `can_serve` の判定の数は、シナリオの数ではなく、グループとそのルートキーの数で決まるからです。routing できない job の計数は、グループの行を lease の走査と同じくルートキーで、通常の行を lease 時の計数と同じく capabilities のリストで集計します。キーごとに、稼働中の各 worker に対して `can_serve` を1回行い、結果にそのキーの job 数を掛けます。そのため、metrics を読むたびのコストも、シナリオの数ではなくルートキーの数で決まります。データベースは、待機中のグループの行を引き続き index 経由ですべて読みます。

lease の途中でシークレットストアの読み取りに失敗すると、バンドルの job をすべて失敗させます。現在は1件だけが失敗します。バンドルの job は同じ org に属し、読み取りはどの job でも同じように失敗するからです。

ローカルの worker は、[BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md) のとおり `maxJobConcurrency` を1にしか設定できません。1件のバンドルを lease するだけで、グループ化から得るものはありません。

### 同時実行上限でのグループの分割

サーバ全体、ユーザーごと、org ごとの上限は意味を保ちます。1つのデプロイ、1人のユーザー、1つの org が同時に走らせてよい job の数です。グループの job については、待機中の job ではなく lease 済みの job を数えます。現在は2つの場所が job を数えているので、どちらがどの数を受け持つかを定めます。

- **lease 時の計数は jobs テーブルが受け持ちます。** サーバ自身は job を実行しないので、待機中の job はすべてすでに jobs テーブルにあります。`lease_job` は、スロットを占める job を、全体、org ごと、ユーザーごとに数えます。スロットを占めるのは、種類を問わない lease 済みの job と、グループに属さない待機中の job のうち稼働中の worker が担当できるものです。どの稼働中の worker も担当できない待機中の通常の job は、スロットを占めません。この job は期限切れにならないので、そのまま残ると、同じ org のすべてのグループを止め続けるからです。この判定は、待機中の通常の job の capabilities のリストの種類ごとに1回行います。担当できる待機中の通常の job は、グループの lease に対してスロットを保ちます。registry は登録時に未完了の通常の job をすべて数え、こちらのほうが厳しいので、上限の側に倒れます。`lease_job` は、3つの上限のどれかに達していればグループの job を飛ばし、同じ時点でバンドルへの後続の追加もやめます。計数の前に取るロックのもとで、計数と claim を1つのトランザクションで行います。SQLite は `BEGIN IMMEDIATE` でトランザクションを始め、計数の前に書き込みロックを取ります。SQLite では、claim は候補の走査とは別の session を開くので、走査が書き込みロックを持つことはありません。PostgreSQL では、claim の `FOR UPDATE SKIP LOCKED` と `UPDATE` は、claim のトランザクションを共有します。claim の session は、その接続での最初の文として、この文を `exec_driver_sql` で自分で発行します。engine は pysqlite の既定のトランザクション処理のままにします。`isolation_level=None` と `begin` ハンドラを使うと、共有する engine を通る他の経路すべての挙動が変わるからです。pysqlite は、トランザクションが開いている間は暗黙のトランザクションを開かないので、この文の効力は commit まで続きます。PostgreSQL は、上限を持つ範囲ごとに、トランザクション単位の advisory lock を取ります。範囲はデプロイ、org、ユーザーで、この順に取ります。候補の走査は1回だけ行い、ロックを取りません。lease は、その候補を順に、それぞれ別のトランザクションで試します。上限が候補を止めたとき、または claim が job を1件も取れなかったときは、トランザクションを終えてロックを手放してから、次の候補に進みます。そのため、lease が同時に持つロックは1件の候補の分だけであり、2つの lease が互いを待ってデッドロックすることはありません。各ロックは2つのキーを取る形を使い、1つ目のキーを範囲にします。そのため、範囲の違うキーが衝突することはありません。上限が無制限の範囲と、actor を持たない job のユーザーの範囲では、ロックを取りません。後から来た lease は先の claim が済んだあとに数えるので、lease 同士で上限を超えることはありません。ただし、登録の途中にある通常の job は上限を超えさせることがあります。registry は、その job が jobs テーブルへ入る前に受け付けるので、超える数は登録の途中にある件数までです。数えた時点で稼働中の worker が担当できなかった通常の job も、上限を超えさせることがあります。担当できる worker が戻ると、lease 時の判定なしに lease されるからです。
- **job レコードに `actor` 列を足します。** ユーザーごとの計数で spec の JSON を読まずに済ませるためです。現在、actor は spec の中にしかありません。
- **メモリ上の registry は、待機中のグループの job を数えません。** registry は登録済みで未完了の job をすべて数え、通常の job は登録時にその数で上限を判定されます。グループの job は、lease された時点でその数に入ります。そうしないと、200シナリオの待機中のグループが、どの worker も取る前に上限を占め、サーバがその org の通常の run をすべて拒否してしまいます。registry は、完了の確認ではなく、新しい読み取りで lease を知ります。完了の確認が調べるのは、registry がすでに数えている id だけで、待機中のグループの job はそこに含まれません。新しい読み取りは、デプロイ全体、org ごと、job の actor ごとに、`leased` のグループの行を数えます。この読み取りは、完了の確認と同じく `try_register` がロックを取る前に行うので、登録はロックの外でデータベースを待ちますが、ほかの登録をその後ろで止めることはありません。読み取りのあとに入った lease による超過は、上限の箇条書きで示した超過の範囲に収まります。2つの読み取りは、誤る向きが逆です。完了の確認を1回取り逃しても、job は数えられたままで、上限の側に倒れます。lease の読み取りを取り逃すと、job が数えられず、上限を超える側に倒れます。読み取りが失敗した場合は、status 429 ではなくそのエラーで登録を失敗させます。完了の確認が失敗した場合の現在の扱いと同じです。`in_flight_by_org` と `active_jobs` も registry の数を読むので、同じ lease の読み取りを足します。そのため、`bajutsu_in_flight_jobs` の gauge はグループの job を数え続けます。worker が実行しているグループは、現在の `run-set` と同じく org の上限を埋めることがあります。その場合、その org の通常の run は、グループの待機中の job が捌けるまで status 429 で拒否されます。空いたスロットは、たいてい worker の次の lease が先に取るからです。通常の job のために lease 時に余裕を確保するかどうかは別の選択であり、本項目は選びません。

グループの残りは待機中のまま残り、先の job が終わるたびに lease されます。

上限より大きなグループは、こうして順番に走る複数のバンドルに分かれます。バンドルは、別々の worker や別々の環境に渡ることがあります。各 worker が upload を共有するのは、自分のバンドルの job のあいだだけです。3つの Device Farm worker が担当するグループは binary を3回 upload します。現在はシナリオごとに1回です。

通常の run、record、crawl、triage の job は、現在の登録時の上限を保ちます。これらを移すのは別の作業です。

### Device Farm の worker での upload の共有

lease した各 job は、[BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch-ja.md) と同じく、自分のスロット、heartbeat、run 識別子のチェックポイント、結果の投稿を持ちます。スロットが共有するのは次の2つです。worker は、バンドル内の全シナリオの materials を、バンドル単位の1つのディレクトリに置き、各スロットがそれを読みます。各スロットは自分専用の workspace を持ち、そこには実行の出力だけを置きます。package アーカイブは、バンドル単位のディレクトリから1つ作り、1回だけ upload します。このディレクトリはバッチのライフサイクルフックからは読み取り専用なので、ある job のフックが別の job の package を変えることはありません。worker は、バンドルの最後のスロットが終わった時点でこのディレクトリを消します。job のバッチ用ディレクトリには他の job の materials を置かない、という現在の規則は、これに置き換わります。1回のアプリ upload が、binary を運びます。どちらの upload も、最初に必要としたスロットが行い、他のスロットはその upload の完了を待ちます。そのあと各スロットは、自分のシナリオと target を指す test spec を作り、自分の Device Farm run をスケジュールします。

バッチのライフサイクルフックは、package を job ごとに変えることがあります。`before_submit` が起動時の環境変数を設定すると、provider がそれを package 内の config に書き込むからです。そのため package を共有するのは、どのフックもその job の起動時の環境変数を設定しない場合に限ります。設定する場合は各 job が自分の package を作り、共有するのはアプリの upload だけになります。

worker は、その互換キーを持つ job が1件でも実行中のあいだ、アプリの upload の識別子を互換キーごとに保持します。スロットが空いて後続の job を lease したときは、保持した識別子を再利用します。worker は、組み立てた package も `group_id` ごとに保持します。互換キーはグループをまたぐので、間にシナリオを編集した2回の `run-set` は、同じキーを共有しても package は共有しません。config ディレクトリから作った package は target の全シナリオを含みますが、送られてきた materials から作った package は自分のバンドルのシナリオしか含みません。そのため、worker が package を再利用するのは、同じグループの job で、かつその job のシナリオがその package に入っている場合に限ります。入っていない場合、スロットは新しい package を作って upload します。そのキーを持つ job がなくなれば、worker はエントリを捨て、あとの lease では再び upload します。package のエントリは、そのグループの job が1件も実行中でなくなった時点で捨てます。

この設計は、複数の Device Farm run が1つの upload を参照できることを前提にします。単位4で、サービスに対してこの前提を確認してから依存します。run が upload を再利用できない場合、provider は job ごとに1回 upload する現在の動作へ戻ります。失うのは節約だけです。

共有 upload が失敗した場合、それを待っていた各スロットが、現在の upload 失敗と同じ経路で失敗します。その後、各 job は自分の試行回数に従います。

### 互換性

`run-set` は `environment` を受け付けなくなります。[BE-0465](../BE-0465-serve-ui-device-farm-fan-out/BE-0465-serve-ui-device-farm-fan-out-ja.md) の Replay タブは現在この値を送っています。同じ変更でタブはこの値を送るのをやめます。環境のセレクタは Run フォームをファンアウトに切り替える役も担っているので、セレクタはフォームから外れます（単位 5）。代わりに、明示的なファンアウトの操作がトリガーになります。タブはこの操作を web の target にも提供します。セレクタ自身のガードは web の target を拒んでいました。通常の `POST /api/run` は変更しません。

単位 5 は、非推奨の `--capabilities` の上書きに `platform:` と `kind:` を予約します。worker は、すでに `environment:`、`host:` などのルーティングの接頭辞を予約しています。`--capabilities platform:ios`、または同じ値を `$WORKER_CAPABILITIES` に入れた worker は、現在は起動しますが、単位 5 の後は起動を拒否します。拒否のメッセージは予約された接頭辞を示し、運用者がそのトークンを外します。警告を出して捨てる方法は採りません。黙って捨てると、グループの job が頼るルーティングから、その worker の容量が外れてしまうからです。

[BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md) は、`run-set` が環境を必須とし、欠落を 400 で拒否すると定めています。本項目はこのフィールド自体を取り除き、取り除くのは意図したことです。この規則があったのは、`run-set` の job が登録の時点からデバイスクラウド向けの `BatchRequest` を持っていたからです。本項目では、job は、そのプラットフォームを担当するどの worker でも実行できる実行意図を持ちます。

### 原則への適合

- **AI は判定しない。** 判定の計算方法には一切触れず、run の経路にモデル呼び出しは入りません。
- **決定性を優先する。** バンドルが変えるのは job の claim の仕方であり、job の待ち方や選択の仕方ではありません。新しく生じる変動要因は配置だけで、これは run の provenance に記録されます。
- **アプリに依存しない。** グループ、キー、バンドルのどれにも、特定のアプリの名前は現れません。互換キーは、target がすでに宣言している内容のダイジェストです。

### 作業分解（MECE）

1. **グループの登録。** `job_groups` テーブルの org、キー、作成時刻の列（サーバが採番するキーは構造上一意なので、一意インデックスはまだ要りません）、重複したシナリオ名の拒否、`group_id`、`compat_key`、`route_key`、`base_key`、`actor` の列、サーバが採番するグループキー、`GET /api/run-set/<groupId>`、`POST /api/run-set/<groupId>/cancel`、`required_role` でのその editor ゲート、登録と参照のルートに対する machine の許可リストの項目と、両方のルートへの `machine_org` の受け渡し。登録は、単位 3 が上限を lease 時に移すまで、現在どおり上限で打ち切ります。不可分な登録と、冪等性と競合の規則は、どちらも単位 3 とともに入ります。上限で打ち切られたグループを、要求全体への応答として返すわけにはいきません。同じキーを繰り返すと 200 が返り、上限が落としたシナリオは1件も待機に入らないからです。上限が登録時に効いているあいだに要求全体を拒否すると、上限が target のシナリオ数を下回るデプロイでは、`run-set` を実行できなくなります。
2. **実行意図。** 実行意図を持つ job の形、ローカルの worker でのローカル実行への変換とデバイスクラウドの worker での `BatchRequest` への変換、`batch` を持たない実行意図の spec を受け付ける `environment_refusal`、古い worker に lease させないトークン。web の経路は単位 5 まで待ちます。
3. **グループ単位の lease と上限。** lease 要求の `maxJobs`、`lease_job` の複数 claim、グループとルートキー（route を確かめない worker では基底キー）の組ごとに1件の候補を読む走査とその2つの index、リストのレスポンス、worker クライアントによる空きスロットの利用、jobs テーブルで数える lease 時の上限、registry から待機中のグループの job を外す処理、registry による jobs テーブルでの lease の確認、ルートキーで集計する routing できない job の計数、不可分な登録、`groupKey` フィールド、`job_groups` の要求ダイジェストの列と、org とキーの一意インデックス、冪等性と競合の規則。
4. **upload の共有。** Device Farm provider のアプリの upload を互換キーごとに保持する処理、同じグループ内で、job のシナリオを含む場合に限った package の再利用、バンドル単位の materials のディレクトリとその削除、起動時の環境変数を設定するフックの例外、job ごとの upload へのフォールバック。
5. **プラットフォームによるルーティング。** `host:` を含まないグループの job の要求トークン、iOS のグループの job の `kind:` のトークンとそれを広告する worker、プラットフォームを広告するデバイスクラウドの worker、`--capabilities` の上書きでの `platform:` と `kind:` の予約、登録時の job ごとの `scenario_route_needs` と `route_refusal`（worker の読み取りは要求ごとに1回、ルートの要求はシナリオの文面ごとに1回）、実行意図の job に対する環境の規則の例外、`environment` を送った要求への 400、Replay タブが `environment` を送らなくなる変更と、環境のセレクタに代わるファンアウトの操作、provenance の記録。web の経路もここで入ります。web の target は `subject.path` を要さず、どのデバイスクラウドの worker も担当しません。この単位が入るまで、`run-set` は、現在どおり web の target を拒否し、`environment` を必須とします。そのあいだ、グループの job は `environment:<provider>` と実行意図のトークンを要求し、現在どおりその環境に回ります。
6. **ドキュメント。** serve API、worker、ルーティングについての `docs/` と `docs/ja/` の各ページ、および `DESIGN.md` と `docs/architecture.md` の該当箇所。

範囲外とするのは、グループの結果を GitHub の check として公開すること、worker 間で upload を共有すること、ローカルのデバイスで job を並行実行すること、グループ外の job に lease 時の上限を適用すること、デバイスクラウドの worker での `cap:` と `setting:` の確認です。デバイスクラウドの worker は、現在 route トークンを計算しません（BE-0467）。lease はそうした job を未確認として記録し（`unchecked_lease`）、レポートにもそう表示します。グループの job を `appium` のグリッドに回すことと、デバイスクラウドでファミリーによって端末を選ぶことも範囲外です。デバイスクラウドが満たせない `runsOn` の条件（リストで挙げた `ios.kind`、`ios.model`、`ios.os`、`android.avd`、`android.apiLevel`）の routing も範囲外とします。

## 検討した代替案

- **グループごとに1つの Device Farm run を作り、シナリオを直列に実行する。** [BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter-ja.md) の形です。予約するデバイス数は最も少なくなりますが、壁時計時間はシナリオ数に比例して伸びます。BE-0336 はこの理由でこの形を離れたので、本項目は job ごとに1つの run を保ちます。
- **グループキーを持たず、互換キーだけでバンドルする。** config と binary が同じ job なら、どの経路で登録されたものでもバンドルされます。ただし、どの job が1つの集まりなのかを呼び出し元に伝える手段がなく、再試行された要求を重複として検出することもできません。グループキーがその両方を与えます。
- **登録時の上限を保ち、再試行で残りを登録する。** 同じキーでの再試行が、上限で漏れたシナリオを足します。グループに「未完了」という状態が増え、呼び出し元はグループが埋まるまで再試行を続けなければなりません。lease 時の上限なら、同じグループが再試行なしで最後まで走ります。
- **グループの package と upload をサーバに作らせる。** サーバがクラウドの認証情報を再び持つことになります。[BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch-ja.md) が意図して取り除いたものです。worker がどちらも持ち続けます。
- **capability の判定をデータベースのクエリに移す。** 走査は worker が担当できる job だけを読むようになります。代償として、`can_serve` の2つ目の実装を、Structured Query Language（SQL）の2つの dialect で持つことになります。そのクエリは、`can_serve` の2つの規則と上限の計数を含みます。
  - 環境の規則
  - 古い worker のルートトークンの規則
  - 上限

  残り4つの判断箇所は Python の実装を使い続けるので、2つの実装を常に一致させなければなりません。組ごとに1件の候補を読む走査なら、コストはグループとそのルートキーの数で抑えられ、実装も1つで済みます。
- **配置ごとのトークンリストをサーバに書かせる（`anyOf`）。** グループの job は、ローカルの代替と、provider ごとのデバイスクラウドの代替を持ちます。`can_serve` は、そのどれか1つを受け入れます。その場合、サーバはデプロイが持つすべての配置を知らなければなりません。job のトークンを読む箇所もすべて、代替を扱えるようにする必要があります。worker は自分が提供できるものをすでに知っています。worker がプラットフォームを広告すれば、`can_serve` は部分集合の判定のままで済み、要求も配置を持たずに済みます。
- **環境の明示を残し、グループ化だけを足す。** 変更は小さく、実行意図も要りません。しかし、すべての呼び出し元が配置を知らなければならないという2つ目のコストが残り、CI の workflow は `devicefarm` を埋め込み続けます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 単位1 — グループの登録
- [x] 単位2 — 実行意図
- [x] 単位3 — グループ単位の lease と上限
- [x] 単位4 — upload の共有
- [x] 単位5 — プラットフォームによるルーティング
- [x] 単位6 — ドキュメント

ログ：

- 2026-11-11：[#2417](https://github.com/bajutsu-e2e/bajutsu/pull/2417) で単位 1 を実装しました。`POST /api/run-set` は `job_groups` に行を書き、`groupId`
  を返します。リクエストの各ジョブは、グループ、互換性キー、ルートキー、ベースキーを保存します。すべての
  ジョブが actor も保存するようになりました。同じシナリオを2回指定したリクエストは拒否します。新しいルートは
  `GET /api/run-set/<groupId>` と `POST /api/run-set/<groupId>/cancel` の2つです。マシンは自分の組織の下で、
  グループを登録でき、読み出せます。設計とは次の5点で異なります。
  - グループの読み出しは、各ジョブの `exitCode` と `runId` も返します。グループ全体の `conclusion` も
    返し、値は `pending`、`success`、`failure`、`cancelled` のいずれかです。ジョブの状態 `done` は、
    終わったことしか示しません。終了コードがなければ、CI の呼び出し側は通ったグループと落ちたグループを
    区別できません。値の名前は、のちに GitHub の check へ結果を出す作業に備え、check run に合わせました。
  - 互換性キーには、バイナリが届く形のダイジェストを使います。保存したスナップショット、設定ファイルの
    ディレクトリのバンドル、アップロードされたバンドルのいずれかです。バンドルのダイジェストはバイナリ
    以外も含むので、キーは共有が減る側に寄ります。別のバイナリを共有してしまう側には寄りません。
  - グループの行はジョブの後に書き、ジョブが1つ以上登録できたときに限ります。上限で最初から拒否された
    リクエストは、空のグループを残しません。単位 3 がグループとジョブを1つのトランザクションで書くまで、
    `jobs.group_id` に外部キーは張りません。
  - 人がグループに届く範囲は、組織への所属で決めます。`POST /api/jobs/<id>/cancel` と同じ扱いで、人を組織で絞らない `GET /api/jobs/<id>` より厳しくなります。
    設計の「自分の組織だけ」では、組織を切り替えた人が、自分の始めたグループで 404 を受けます。
    そのグループの各ジョブには、届いたままです。取り消しには、グループの組織での editor 権限が要ります。
    マシンは自分の組織に限られたままです。
  - 新しいルートを説明する `docs/devicefarm.md` と `docs/self-hosting.md` の記述は、両言語ともこの単位で
    追加しました。ドキュメント全体の見直しは単位 6 に残します。
- 2026-11-11：[#2426](https://github.com/bajutsu-e2e/bajutsu/pull/2426) で単位 2 を実装しました。`run-set` のジョブは、バッチの代わりに実行意図を
  持ちます。中身はシナリオ、target、設定、プラットフォーム、アプリケーションのパス、アップロード実行の
  方針です。ジョブをリースしたワーカーは、ほかの処理が spec を読む前に、実行意図を自分の形に変換します。
  device cloud のワーカーは、自分の provider のバッチとして送ります。それ以外のワーカーは、通常の run
  ジョブと同じ `bajutsu run` のコマンドとして実行します。ジョブは `job:run-intent` を要求し、この変更以降の
  ワーカーはすべてこのトークンを広告します。単位 5 までは `environment:<provider>` も要求します。設計とは
  次の3点で異なります。
  - `appium` のワーカーもトークンを広告し、ローカルのワーカーと同じく実行意図を run のコマンドに変換します。
    グリッドへは、通常の run と同じく `--worker-config` を通じて届きます。ただし、グループのジョブは
    ルーティングで届きません。単位 5 までは `environment:<provider>` を要求するためです。単位 5 以降は、
    グリッドのワーカーが `platform:` のトークンを広告しないためです。単位 5 でグリッドに届けるかどうかは
    未定です。
  - ワーカーは `environment_refusal` の前に実行意図を変換するので、検査が見るのはこれまでどおりバッチか
    コマンドです。変換できない実行意図は、この検査が足りない項目を挙げて拒否します。拒否は取得の前に
    起きます。
  - ベースラインを取り込むかどうか、ラベル、来歴は、実行意図の項目ではなく、spec にすでにある
    `materialize_baselines`、`label`、`provenance` で伝えます。バッチに変換したジョブでは
    `materialize_baselines` を外すので、device cloud のワーカーはこれまでどおりベースラインを取り込みません。
- 2026-11-13：[#2431](https://github.com/bajutsu-e2e/bajutsu/pull/2431) で単位 3 を実装しました。`run-set` はグループとすべてのジョブを
  1つのトランザクションで書くので、リクエストはすべてのシナリオを待機に入れるか、1つも入れないかの
  どちらかになります。同時実行数の上限は、グループを途中で打ち切らなくなりました。上限はワーカーが
  グループのジョブをリースする時点で効きます。リースの要求は `maxJobs` を持てます。そのときの応答は、
  起点のジョブと同じ判定を受ける1つのグループのジョブをまとめたリストです。走査は、グループとルートキーの
  組ごとに候補を1つだけ読みます。ルートを検査しないワーカーには、ベースキーの組ごとに読みます。レジストリは、
  待機中のグループのジョブを数に含めず、リース済みのジョブを jobs テーブルから読みます。Device Farm の
  ワーカーは、空いているスロットをすべて1回の要求で求めます。呼び出し側は `groupKey` を送れます。
  同じリクエストを繰り返すとそのグループが返り、別のリクエストは 409 になります。設計とは次の5点で
  異なります。
  - リポジトリから `create_job_group` と、`enqueue_job` のグループ用の引数を取り除きました。グループと
    そのジョブは、1つの `register_job_group` が書きます。グループのジョブは executor を通らないので、
    ほかにグループの行を書く処理はありません。
  - 空のリースは、`maxJobs` の有無にかかわらず、空のリストではなく 204 を返します。どのワーカーも、
    204 を空のキューとしてすでに読んでいるからです。
  - リクエストのダイジェストは、シナリオ名を集合として扱います。順序を変えて並べた再送も、同じ
    リクエストとみなします。
  - SQLite では、上限が起点のジョブの範囲に効くときに限り、確保の処理が書き込みロック
    （`BEGIN IMMEDIATE`）を取ります。上限がなければ守るべき計数がなく、条件付きの `UPDATE` だけで
    1件ずつの確保を守れます。
  - `docs/devicefarm.md` から、上限が fan-out を打ち切るという記述を除き、`groupKey` の説明を
    加えました。`docs/self-hosting.md` には、fan-out のジョブは 429 で拒否せず、リースの時点で上限を
    適用することを記しました。どちらも両言語です。ドキュメント全体の見直しは単位 6 に残します。
- 2026-11-14：[#2443](https://github.com/bajutsu-e2e/bajutsu/pull/2443) で単位 4 を実装しました。1つのグループに属する Device Farm のスロットは、
  アップロードを共有するようになりました。アプリケーションは、互換キーを持つジョブが実行中である間、
  そのキーごとに1回だけアップロードします。パッケージは、グループごとに1回だけアップロードします。
  ジョブがそのパッケージを使い回すのは、パッケージにそのジョブのシナリオが入っていて、かつフックが
  起動時の環境変数を設定していない場合だけです。アップロード済みのバンドルを
  持たないグループは、ワーカーごとに1つのディレクトリにファイルを集めます。ワーカーは、グループの最後の
  スロットが終わるとそのディレクトリを削除します。実行結果は、これまでどおり各スロットの作業ディレクトリに
  置きます。設計とは次の4点で異なります。
  - アップロードを使い回せることは、AWS のドキュメントだけを根拠にしており、サービスに対して実行しては
    確かめていません。`ScheduleRun` は各アップロードを ARN で受け取り、ガイドはテストスペックの
    アップロードを後の実行でも使っています。使い回したアップロードを `ScheduleRun` が
    `ArgumentException` か `NotFoundException` で拒んだ場合、ジョブは自分の分をアップロードし直して
    1回だけ再試行します。その再試行が成功してから、ワーカーは再起動するまでジョブごとにアップロードします。
    同じエラーコードはほかの不正な引数にも返るので、拒否されただけで共有を止めることはありません。
  - グループ ID と互換キーは、ジョブのスペックではなくリースの応答が運びます。スロットを使うワーカーは、
    両方を実行するスペックに写します。
  - アップロード済みのバンドルを送るグループには、ファイルを集めるディレクトリは要りません。各スロットが
    持つバンドルの写しに、ターゲットのシナリオがすべて入っているからです。
  - フックがファイルを集めるディレクトリを読み取り専用として扱うことは、文書にした約束であり、ワーカーは
    強制しません。フックは、オペレーターがプロセスの起動時に組み込むコードです。

- 2026-11-14：[#2445](https://github.com/bajutsu-e2e/bajutsu/pull/2445) で単位 5 を実装しました。`run-set` のリクエストは環境を指定しません。
  `environment` を送るリクエストには、ステータス 400 を返します。グループのジョブはそれぞれ、ターゲットの
  プラットフォーム、実行意図のトークン、シナリオ自身の `cap:` と `setting:` のトークンを要求します。
  iOS のジョブは、さらにファミリーのトークンと、実行が取る種類ごとに1つの `kind:` トークンを要求します。
  `host:` トークンを要求するジョブはありません。稼働中のワーカーがジョブのルートを駆動していて、
  どのワーカーもそのジョブを処理できない場合、登録はリクエストを拒否します。web のターゲットには
  `subject.path` は要りません。Device Farm のワーカーは、自分のプラットフォームと `kind:device` を広告します。
  環境のルールは、実行意図のジョブには適用しません。ローカルのワーカーは、手元の端末の一覧から
  `kind:simulator` と `kind:device` を広告します。`--capabilities` の上書きでは、`platform:` と `kind:` を
  予約しました。Replay タブは環境を送らず、環境の選択欄の代わりに fan-out のチェックボックスを置きました。
  設計とは次の3点で異なります。
  - ワーカーは、ジョブを実行した場所を `workerEnvironment` として実行の来歴に書き込みます。レポートは、
    この項目を注記として表示します。BE-0467 の、ルートを確かめなかったことの注記と並べて示します。
  - Replay タブは `GET /api/environments` を読まなくなりました。API を消すかどうかは別の判断なので、
    ルート自体は残しています。
  - リクエストのダイジェストは、環境を含まなくなりました。この単位より前に登録したグループキーを、
    この単位の後で繰り返すと、ダイジェストが異なるのでステータス 409 を返します。
- 2026-11-14 — UNIT6PR で単位 6 を実装し、本項目を完了しました。serve の API、worker、ルーティングの
  ページを、単位 1 から 5 の内容にそろえました。どれも両言語です。
  - `docs/self-hosting.md` に、fan-out の job が必要とするトークンと、worker が広告する
    `job:run-intent`、`kind:`、Device Farm の `platform:` のトークンを記しました。`run-set` の
    ルートによる拒否と、BE-0484 のアップグレードの注意も加えました。
  - `docs/cli.md` に、`maxJobs`、バンドルの応答、lease が渡す実行意図を記しました。
  - `docs/architecture.md` に、job のグループと `run-set` の job のルーティングを記しました。
    マシンセッションが呼べる `run-set` の 2 つのルートも加えました。
  - `DESIGN.md` に、worker への fan-out と、`workerEnvironment` として記録する実行場所を記しました。
    `docs/reporting.md` には、その provenance の項目を加えました。
  - 古くなった次の 4 つの記述を取り除きました。
    - `groupKey` の比較に含まれていた環境
    - Replay の fan-out への 429 の応答
    - `appium` の worker が広告するトークンを 1 つとする記述
    - ホストで動くすべての job が `host:` のトークンを必要とするという記述

## 参考

- [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out-ja.md)：シナリオごとのファンアウト。
- [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch-ja.md)：Device Farm の worker とスロットモデル。
- [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md)：worker の capability と `maxJobConcurrency`。
- [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md)：run ごとの選択としての環境。
- [BE-0465](../BE-0465-serve-ui-device-farm-fan-out/BE-0465-serve-ui-device-farm-fan-out-ja.md)：Replay タブのファンアウト。
- [BE-0468](../BE-0468-runson-family-and-any-of/BE-0468-runson-family-and-any-of-ja.md)：`anyof:` トークンと、`can_serve` を使う4つの判断箇所。
- `bajutsu/serve/operations/dispatch.py`（`start_run_set`）、`bajutsu/serve/server/db/sql_repository.py`（`lease_job`）、`bajutsu/serve/state/job_registry.py`（`try_register`）：本項目が変更する登録と lease のコード。
- `bajutsu/serve/capabilities.py`（`can_serve`）、`bajutsu/serve/cli/worker.py`（`_advertised_capabilities`）、`bajutsu/serve/batch_provider/device_farm_batch_provider.py`：本項目が変更するルーティングと worker のコード。

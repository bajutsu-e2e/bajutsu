[English](BE-XXXX-job-group-lease.md) · **日本語**

# BE-XXXX — job をグループとして登録し、worker がグループ単位でまとめて lease する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-job-group-lease-ja.md) |
| 提案者 | [@paihu](https://github.com/paihu) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
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
- **バンドル**：1つの worker が1回の要求で lease する job です。バンドルの job はすべて同じグループに属します。1つのグループは、複数の worker にまたがる複数のバンドルに分かれることがあります。

### グループの登録

`POST /api/run-set` の body は、2点を除いて変わりません。`environment` を受け付けなくなり、任意のフィールド `groupKey` が1つ増えます。レスポンスは `jobIds` を残し、`groupId` を加えます。

- グループキーは、`[A-Za-z0-9._/:-]` の文字からなる128文字以内の文字列です。呼び出し元の org の中で一意です。呼び出し元がキーを送らない場合は、サーバが生成します。
- `environment` を送った要求は、status 400 で拒否します。現在の `_run_set_refusal` が `deviceBudget` や override のフィールドを拒否するのと同じ扱いです。エラーには本項目を示します。どの worker が job を実行するかは、要求が指定することではありません。
- 登録は不可分です。要求の全シナリオが待機中の job になるか、要求が拒否されてグループが1件も作られないかのどちらかになります。同時実行上限はグループを途中で打ち切りません。後の節で述べるとおり、上限は lease の時点で効くからです。同じシナリオを2回以上挙げた要求は、status 400 で拒否します。重複は、要求の文字列ではなく、各名前を解決したシナリオのファイルで判定します。`runnable` はファイル名だけを残すので、`a.yaml` と `x/a.yaml` は同じシナリオを指します。そのため、グループの job の数は、target が持つシナリオの種類の数を超えません。
- キューを持たないサーバは、現在どおりすべての `run-set` を拒否します。1台のマシンでの起動はデフォルトでキューを持つので、この拒否が及ぶのはキューなしで起動したサーバだけです。
- 同じキーと同じ要求ダイジェストを持つ要求を繰り返すと、既存のグループを status 200 で返し、job は登録しません。同じキーで異なるダイジェストの要求は、status 409 で拒否します。ダイジェストは、target、解決したシナリオ名、config のダイジェスト、アプリのダイジェスト、run のラベルを含みます。
- GitHub Actions の呼び出し元は、`GITHUB_RUN_ID`、`GITHUB_RUN_ATTEMPT`、`GITHUB_JOB` からキーを組み立てます。`GITHUB_RUN_ID` だけでは足りません。workflow を re-run しても run id は変わらず、attempt だけが変わるためです。このキーが冪等にするのは、1回の attempt の中で再試行された要求です。意図して re-run した場合は、新しいグループを求めたものとして、新しいグループが作られます。
- 新しいテーブル `job_groups` に、org、キー、要求ダイジェスト、作成時刻を持たせ、org とキーに一意インデックスを張ります。job レコードには `group_id`、`compat_key`、`route_key` の列を足します。他の経路で登録した job は、3つとも `NULL` にします。後述の window 関数は `group_id` を持つ行にだけ適用するので、通常の job が1つの組にまとめられることはありません。lease のレスポンスは `compat_key` を返し、worker はそれを upload のキャッシュのキーに使います（単位 4）。
- `GET /api/run-set/<groupId>` は、呼び出し元自身の org のグループについてだけ、グループの job とその状態を返します。別の org のグループには、403 ではなく 404 を返します。BE-0015 に従い、他の org 単位の読み取りと同様、存在を漏らさないためです。報告側が poll する読み取り経路です。結果を GitHub の check として公開する処理は別の関心事であり、範囲外とします。
- `POST /api/run-set/<groupId>/cancel` は、現在の job 単位のキャンセルを、グループの未完了の job すべてに適用します。待機中の job はその場で `cancelled` になり、どの worker も lease しません。lease 済みの job にはキャンセルの印が付き、worker が次の heartbeat でそれを受け取って止めます。現在の job のキャンセルと同じ動きです。このルートは `POST /api/jobs/<id>/cancel` と同じ権限を求めます。`required_role` は `/api/jobs/` の接頭辞だけでそのルートを判定するので、グループのキャンセルのパスも editor のパスとして加えます。加えないと、viewer でもグループをキャンセルできてしまいます。呼び出し元自身の org のグループだけを扱い、別の org のグループには 404 を返します。未完了の job を持たないグループへのキャンセルは 200 を返し、何も変えません。

### どの環境でも実行できる job

現在の `run-set` の job は `BatchRequest` を持ち、登録の時点で provider を固定します。ローカルの `bajutsu run` の job は、代わりにコマンドラインとデバイスの引数を持ちます。どちらの形も、もう一方の種類の worker では実行できません。そこでグループの job は実行意図だけを持ち、job を lease した worker が実行意図を自分の形に変換します。

- ローカルの worker は、通常の run の job と同じ方法で、自分のデバイスでそのシナリオを走らせる `bajutsu run` の呼び出しを組み立てます。
- デバイスクラウドの worker は、自分の provider の種別で `BatchRequest` を組み立てて submit します。

アプリの参照は、[BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch-ja.md) がすでに求めているとおり論理的な参照のままにします。web の target はアプリを必要としないので、サーバは web の target に `subject.path` を求めません。Android または iOS の target には引き続き `subject.path` が必要で、持たない target は現在どおり status 400 で拒否します。そのプラットフォームを担当するデバイスクラウドの worker が lease すると、インストールするものがないからです。それ以外のプラットフォームの target は、現在どおり status 400 で拒否します。

### プラットフォームによるルーティング

グループの job は、通常の job と同じ方法で回ります。`capabilities` は all-of のリストで、worker はすべてのトークンを広告していれば job を担当できます。job は run に必要なものを挙げ、各 worker は自分が提供できるものを広告します。要求は配置を指定せず、サーバも配置を書きません。

グループの job は、次のトークンを要求します。

- プラットフォームのトークンです。`platform:ios`、`platform:android`、`platform:web` のいずれかです。
- iOS の target では、BE-0468 のデバイスファミリーのトークンです。
- シナリオ自身の `cap:` と `setting:` のトークンです。シナリオの本文から読みます（BE-0467）。
- 実行意図のトークンです。本項目の worker だけが広告します。

`host:` のトークンは要求しません。driver が必要とするホストは、ローカルの worker の性質だからです。BE-0450 以降、ローカルの worker は自分のホストで動かせるプラットフォームだけを広告するので、プラットフォームのトークンがホストの条件も兼ねます。これは、1つのプラットフォームの actuator がすべて同じホストに結び付いていることを前提にしています。この前提を崩す actuator を足すときは、`host:` のトークンを戻す必要があります。非推奨の `--capabilities` の上書きにはまだ `platform:ios` を入れられるので、単位 5 でこの上書きに `platform:` を予約します。

デバイスクラウドの worker は、広告するものを変えます。現在は `platform:*` のトークンの代わりに `environment:<provider>` を広告しています（BE-0448）。本項目では、provider が担当するプラットフォームも広告します。デバイスファミリーのトークンは広告しません。provider のデバイスの選択は、プラットフォームだけで端末を絞り込むからです。そのため、ファミリーを指定する target（`runsOn.ios.family`）は、それを守れる worker にだけ回ります。現在は `environment: devicefarm` でそうした target も Device Farm に送られ、Device Farm はファミリーを無視します。

BE-0448 の環境の規則は、`environment:*` を広告する worker を、そのトークンを要求しないすべての job から遠ざけます。この規則は、実行意図のトークンを要求する job には適用しません。それ以外の job には引き続き適用するので、デバイスクラウドの worker が、ローカルのデバイスを起動する通常の run を lease することはありません。`appium` のグリッドの worker（BE-0452）は `platform:*` のトークンを広告しないので、グループの job を取りません。グリッドだけのデプロイは、通常の run の要求でシナリオを実行し続けます。

サーバは、通常の run と同じく、job ごとのリストに `route_refusal` を行います。1件の job について次の3つがすべて成り立つと、要求全体を status 400 で拒否します。

- 稼働中の worker がその job の経路を担当しています。
- そのどの worker も、job の `cap:` または `setting:` のトークンのどれかを欠いています。
- 稼働中のどの worker も job を担当できません。

それ以外の場合、稼働中のどの worker も担当できない job は、待機したまま残ります。登録は不可分であり、レスポンスはそのシナリオと足りないトークンを示します。

worker が job を担当できるかを判断する箇所は4つあります。`lease_job` の lease の絞り込み、routing できない job の計数、worker 不在の通知（`lacks_live_worker`）、`route_refusal` です。4つとも `can_serve` を通り、`can_serve` の変更は上の例外だけです。そのため4つの判断は、BE-0468 が自身のトークンに求めるとおり一致し続けます。worker の claim 時の再確認（`claim_refusal`、BE-0467）は、同じリストを読むので、変更は要りません。

job を lease した worker が実行場所を決め、最初に lease した worker が勝ちます。このため、シナリオが走る場所は固定されず、同じシナリオがシミュレータでは通り、クラウドのデバイスでは落ちることがあります。原則2が扱うのは待機とセレクタであり、判定は引き続きアサーションだけで決まります。それでも残るのは再現性のコストです。そのため run は、実行した worker の環境を provenance に記録し、レポートがそれを表示します。呼び出し元は配置を固定できません。配置を固定したい運用者は、デプロイで動かす worker の構成で制御します。

### バンドルの lease

lease 要求に `maxJobs` を足します。worker の空きスロット数、つまり `maxJobConcurrency` から実行中の job 数を引いた値です。要求が `maxJobs` を持つ場合、レスポンスは lease した job のリストで、各要素は現在の job 1件分の形です。持たない場合、レスポンスは現在の単一のオブジェクトのままなので、本項目より前の worker も通常の job は lease できます。実行意図を持つ job は、本項目の worker だけが広告するトークンを追加で要求するので、古い worker は lease しません。古い worker が lease すると、job を待機に残さず失敗させてしまいます。デバイスクラウドの worker の `environment_refusal` は `batch` を持たない spec を拒否し（BE-0448）、ローカルの worker は実行する `cmd` を見つけられないからです。

`lease_job` は、まず現在と同じ方法でアンカーを選びます。worker が担当できる最も古い待機中の job です。アンカーがグループを持つ場合は、最大 `maxJobs - 1` 件かつ上限が残す余裕の範囲で、続けて待機中の job を claim します。各 job は、アンカーと同じ `group_id` とルートキーを持つものに限ります。アンカーと判定結果が同じなので、worker はどれも担当できます。各 claim はアンカーの claim と同じ条件付き `UPDATE ... WHERE status = 'queued'` です。競合に負けても、その1件を飛ばすだけで lease は失敗しません。

アンカーは担当できる最も古い job のままなので、グループ間の順序は変わりません。後続の job は他のグループの古い job を追い越すことがあり、`maxJobs` がその幅を抑えます。これは意図したコストです。upload を省ける利益のほうが、キュー内の厳密な古さの順序より大きいと判断します。

アンカーを探す走査では、job ごとではなく、`group_id` とルートキーの組ごとに1件の候補を読みます。ルートキーは、登録時に job と一緒に保存します。現在の `lease_job` は、待機中の job を古い順にすべて読み込み、1件ずつ `can_serve` で判定します。そのため、500 シナリオのグループを担当できない worker は、担当できる job にたどり着くまでに 500件を判定します。本項目では、グループに属さない待機中の job は今までどおりすべて読みます。グループの job は、組ごとに、いちばん古い待機中の1件だけを読みます。この1件は次の window 関数で選びます。

```sql
ROW_NUMBER() OVER (PARTITION BY group_id, route_key ORDER BY created_at)
```

window 関数は SQLite と PostgreSQL の両方が対応しているので、SQLite のゲートでも同じクエリを検証できます。`(status, group_id, route_key, created_at)` に index を張り、選択をデータベースの中で済ませます。Python で行う `can_serve` の判定の数は、待機中の job の総数ではなく、通常の job の数と、グループごとのルートキーの数で決まるようになります。

1件の候補が組全体を代表できるのは、同じ組の job がすべて同じ判定結果になるからです。`can_serve` の判定が読むのは job の `capabilities` のリストで、ルートキーはそのダイジェストです。上限は org と actor に対して数えますが、この2つは1回の要求で決まります。

ルートキーが要るのは、グループの job がすべて同じ要求トークンを持つわけではないからです。シナリオ自身の `cap:` と `setting:` のトークンは、シナリオの本文から決まります（BE-0467）。同じグループの2つのシナリオが、異なるトークンを要求することがあります。そうしたグループは複数の組に分かれ、組ごとに `can_serve` を1回呼びます。すべてのシナリオが固有の要求を持つ場合、走査のコストは現在の走査と同じです。組の判定結果は1つなので、組のいちばん古い job が、worker の担当できる新しい job を隠すことはありません。

不変条件は、ルートキーが判定結果を左右する job ごとの入力をすべて含むことです。そうした入力を足す変更は、それをキーに加えなければなりません。

候補を選ぶクエリは、現在と同じくロックを取りません。window 関数を含むので、PostgreSQL は `FOR UPDATE` を付けても拒否します。claim では、選んだ組だけを対象に、window 関数を含まない2つ目のクエリを実行します。このクエリは、組の待機中の job を古い順に、`maxJobs` と3つの上限が残す余裕の小さいほうの件数まで取ります。余裕を決める計数は、claim と同じトランザクションの中で行います（上限の節）。PostgreSQL では `FOR UPDATE SKIP LOCKED` でロックし、SQLite では各 claim を条件付きの `UPDATE` で行います。そのため、同じ組を選んだ worker 同士は、いちばん古い1件を奪い合わず、組の job に分散します。バンドルの job は同じルートキーを持ち、ルートキーが複数あるグループは、キーごとにバンドルを埋めていきます。

capability の判定は `can_serve` だけが行うので、4つの判断箇所は1つの定義を共有し続けます。待機中のグループの job の総数には上限を設けません。Python で行う `can_serve` の判定の数は、シナリオの数ではなく、グループとそのルートキーの数で決まるからです。データベースは、待機中のグループの行を引き続き index 経由ですべて読みます。

lease の途中でシークレットストアの読み取りに失敗すると、バンドルの job をすべて失敗させます。現在は1件だけが失敗します。バンドルの job は同じ org に属し、読み取りはどの job でも同じように失敗するからです。

ローカルの worker は、[BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md) のとおり `maxJobConcurrency` を1にしか設定できません。1件のバンドルを lease するだけで、グループ化から得るものはありません。

### 同時実行上限でのグループの分割

サーバ全体、ユーザーごと、org ごとの上限は意味を保ちます。1つのデプロイ、1人のユーザー、1つの org が同時に走らせてよい job の数です。グループの job については、待機中の job ではなく lease 済みの job を数えます。現在は2つの場所が job を数えているので、どちらがどの数を受け持つかを定めます。

- **lease 時の計数は jobs テーブルが受け持ちます。** サーバ自身は job を実行しないので、待機中の job はすべてすでに jobs テーブルにあります。`lease_job` は、スロットを占める job を、全体、org ごと、ユーザーごとに数えます。スロットを占めるのは、種類を問わない lease 済みの job と、グループに属さない待機中の job です。registry も同じ定義で通常の job を受け付けるので、2つの数が食い違うことはなく、待機中の通常の job はグループの lease に対してスロットを保ちます。`lease_job` は、3つの上限のどれかに達していればグループの job を飛ばし、同じ時点でバンドルへの後続の追加もやめます。計数の前に取るロックのもとで、計数と claim を1つのトランザクションで行います。SQLite は `BEGIN IMMEDIATE` でトランザクションを始め、計数の前に書き込みロックを取ります。PostgreSQL は、上限を持つ範囲ごとに、トランザクション単位の advisory lock を取ります。範囲はデプロイ、org、ユーザーで、2つの lease が互いを待ってデッドロックしないよう、この順に取ります。各ロックは2つのキーを取る形を使い、1つ目のキーを範囲にします。そのため、範囲の違うキーが衝突することはありません。上限が無制限の範囲と、actor を持たない job のユーザーの範囲では、ロックを取りません。後から来た lease は先の claim が済んだあとに数えるので、lease 同士で上限を超えることはありません。ただし、登録の途中にある通常の job は上限を超えさせることがあります。registry は、その job が jobs テーブルへ入る前に受け付けるので、超える数は登録の途中にある件数までです。
- **job レコードに `actor` 列を足します。** ユーザーごとの計数で spec の JSON を読まずに済ませるためです。現在、actor は spec の中にしかありません。
- **メモリ上の registry は、待機中のグループの job を数えません。** registry は登録済みで未完了の job をすべて数え、通常の job は登録時にその数で上限を判定されます。グループの job は、lease された時点でその数に入ります。registry は、現在 job の完了を知るのと同じ方法で lease を知ります。数える前に jobs テーブルを確認します。そうしないと、200シナリオの待機中のグループが、どの worker も取る前に上限を占め、その org の通常の run をすべて拒否してしまいます。worker が実行しているグループは、現在の `run-set` と同じく org の上限を埋めることがあります。その場合、その org の通常の run は、グループの待機中の job が捌けるまで status 429 で拒否されます。空いたスロットは、たいてい worker の次の lease が先に取るからです。通常の job のために lease 時に余裕を確保するかどうかは別の選択であり、本項目は選びません。

グループの残りは待機中のまま残り、先の job が終わるたびに lease されます。

上限より大きなグループは、こうして順番に走る複数のバンドルに分かれます。バンドルは、別々の worker や別々の環境に渡ることがあります。各 worker が upload を共有するのは、自分のバンドルの job のあいだだけです。3つの Device Farm worker が担当するグループは binary を3回 upload します。現在はシナリオごとに1回です。

通常の run、record、crawl、triage の job は、現在の登録時の上限を保ちます。これらを移すのは別の作業です。

### Device Farm の worker での upload の共有

lease した各 job は、[BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch-ja.md) と同じく、自分のスロット、heartbeat、run 識別子のチェックポイント、結果の投稿を持ちます。スロットが共有するのは次の2つです。1つの package アーカイブが、bundle ディレクトリと、バンドル内の全シナリオの materials を運びます。1回のアプリ upload が、binary を運びます。最初にそれらを必要としたスロットが upload し、他のスロットはその upload の完了を待ちます。そのあと各スロットは、自分のシナリオと target を指す test spec を作り、自分の Device Farm run をスケジュールします。

バッチのライフサイクルフックは、package を job ごとに変えることがあります。`before_submit` が起動時の環境変数を設定すると、provider がそれを package 内の config に書き込むからです。そのため package を共有するのは、どのフックもその job の起動時の環境変数を設定しない場合に限ります。設定する場合は各 job が自分の package を作り、共有するのはアプリの upload だけになります。

worker は、その互換キーを持つ job が1件でも実行中のあいだ、アプリの upload の識別子を互換キーごとに保持します。スロットが空いて同じグループの後続の job を lease したときは、保持した識別子を再利用します。config ディレクトリから作った package は target の全シナリオを含みますが、送られてきた materials から作った package は自分のバンドルのシナリオしか含みません。そのため、worker が package を再利用するのは、後続の job のシナリオがその package に入っている場合に限ります。入っていない場合、スロットは新しい package を作って upload します。そのキーを持つ job がなくなれば、worker はエントリを捨て、あとの lease では再び upload します。

この設計は、複数の Device Farm run が1つの upload を参照できることを前提にします。単位4で、サービスに対してこの前提を確認してから依存します。run が upload を再利用できない場合、provider は job ごとに1回 upload する現在の動作へ戻ります。失うのは節約だけです。

共有 upload が失敗した場合、それを待っていた各スロットが、現在の upload 失敗と同じ経路で失敗します。その後、各 job は自分の試行回数に従います。

### 互換性

`run-set` は `environment` を受け付けなくなります。[BE-0465](../BE-0465-serve-ui-device-farm-fan-out/BE-0465-serve-ui-device-farm-fan-out-ja.md) の Replay タブは現在この値を送っています。同じ変更でタブは送るのをやめ、環境の選択肢はファンアウトから外れます（単位 5）。通常の `POST /api/run` は変更しません。

[BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md) は、`run-set` が環境を必須とし、欠落を 400 で拒否すると定めています。本項目はこのフィールド自体を取り除き、取り除くのは意図したことです。この規則があったのは、`run-set` の job が登録の時点からデバイスクラウド向けの `BatchRequest` を持っていたからです。本項目では、job は、そのプラットフォームを担当するどの worker でも実行できる実行意図を持ちます。

### 原則への適合

- **AI は判定しない。** 判定の計算方法には一切触れず、run の経路にモデル呼び出しは入りません。
- **決定性を優先する。** バンドルが変えるのは job の claim の仕方であり、job の待ち方や選択の仕方ではありません。新しく生じる変動要因は配置だけで、これは run の provenance に記録されます。
- **アプリに依存しない。** グループ、キー、バンドルのどれにも、特定のアプリの名前は現れません。互換キーは、target がすでに宣言している内容のダイジェストです。

### 作業分解（MECE）

1. **グループの登録。** `job_groups` テーブル、重複したシナリオ名の拒否、`group_id`、`compat_key`、`route_key`、`actor` の列、サーバが採番するグループキー、`GET /api/run-set/<groupId>`、`POST /api/run-set/<groupId>/cancel` と `required_role` でのその editor ゲート。登録は、単位 3 が上限を lease 時に移すまで、現在どおり上限で打ち切ります。不可分な登録と、冪等性と競合の規則は、どちらも単位 3 とともに入ります。上限で打ち切られたグループを、要求全体への応答として返すわけにはいきません。同じキーを繰り返すと 200 が返り、上限が落としたシナリオは1件も待機に入らないからです。上限が登録時に効いているあいだに要求全体を拒否すると、上限が target のシナリオ数を下回るデプロイでは、`run-set` を実行できなくなります。
2. **実行意図。** 実行意図を持つ job の形、ローカルの worker でのローカル実行への変換とデバイスクラウドの worker での `BatchRequest` への変換、`batch` を持たない実行意図の spec を受け付ける `environment_refusal`、古い worker に lease させないトークン、web の経路。
3. **グループ単位の lease と上限。** lease 要求の `maxJobs`、`lease_job` の複数 claim、グループとルートキーの組ごとに1件の候補を読む走査とその index、リストのレスポンス、worker クライアントによる空きスロットの利用、jobs テーブルで数える lease 時の上限、registry から待機中のグループの job を外す処理、registry による jobs テーブルでの lease の確認、不可分な登録、`groupKey` フィールド、要求ダイジェスト、org とキーの一意インデックス、冪等性と競合の規則。
4. **upload の共有。** Device Farm provider のアプリの upload を互換キーごとに保持する処理、job のシナリオを含む場合に限った package の再利用、起動時の環境変数を設定するフックの例外、job ごとの upload へのフォールバック。
5. **プラットフォームによるルーティング。** `host:` を含まないグループの job の要求トークン、プラットフォームを広告するデバイスクラウドの worker、`--capabilities` の上書きでの `platform:` の予約、登録時の job ごとの `scenario_route_needs` と `route_refusal`、実行意図の job に対する環境の規則の例外、`environment` を送った要求への 400、Replay タブが `environment` を送らなくなる変更、provenance の記録。この単位が入るまで、`run-set` は `environment` を必須とします。そのあいだ、グループの job は `environment:<provider>` と実行意図のトークンを要求し、現在どおりその環境に回ります。
6. **ドキュメント。** serve API、worker、ルーティングについての `docs/` と `docs/ja/` の各ページ、および `DESIGN.md` と `docs/architecture.md` の該当箇所。

範囲外とするのは、グループの結果を GitHub の check として公開すること、worker 間で upload を共有すること、ローカルのデバイスで job を並行実行すること、グループ外の job に lease 時の上限を適用すること、デバイスクラウドの worker での `cap:` と `setting:` の確認です。デバイスクラウドの worker は、現在 route トークンを計算しません（BE-0467）。lease はそうした job を未確認として記録し（`unchecked_lease`）、レポートにもそう表示します。グループの job を `appium` のグリッドに回すことと、デバイスクラウドでファミリーによって端末を選ぶことも範囲外です。

## 検討した代替案

- **グループごとに1つの Device Farm run を作り、シナリオを直列に実行する。** [BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter-ja.md) の形です。予約するデバイス数は最も少なくなりますが、壁時計時間はシナリオ数に比例して伸びます。BE-0336 はこの理由でこの形を離れたので、本項目は job ごとに1つの run を保ちます。
- **グループキーを持たず、互換キーだけでバンドルする。** config と binary が同じ job なら、どの経路で登録されたものでもバンドルされます。ただし、どの job が1つの集まりなのかを呼び出し元に伝える手段がなく、再試行された要求を重複として検出することもできません。グループキーがその両方を与えます。
- **登録時の上限を保ち、再試行で残りを登録する。** 同じキーでの再試行が、上限で漏れたシナリオを足します。グループに「未完了」という状態が増え、呼び出し元はグループが埋まるまで再試行を続けなければなりません。lease 時の上限なら、同じグループが再試行なしで最後まで走ります。
- **グループの package と upload をサーバに作らせる。** サーバがクラウドの認証情報を再び持つことになります。[BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch-ja.md) が意図して取り除いたものです。worker がどちらも持ち続けます。
- **capability の判定をデータベースのクエリに移す。** 走査は worker が担当できる job だけを読むようになります。代償として、`can_serve` の2つ目の実装を、Structured Query Language（SQL）の2つの dialect で持つことになります。そのクエリは、`can_serve` の2つの規則と上限の計数を含みます。
  - 環境の規則
  - 古い worker のルートトークンの規則
  - 上限

  残り3つの判断箇所は Python の実装を使い続けるので、2つの実装を常に一致させなければなりません。組ごとに1件の候補を読む走査なら、コストはグループとそのルートキーの数で抑えられ、実装も1つで済みます。
- **配置ごとのトークンリストをサーバに書かせる（`anyOf`）。** グループの job は、ローカルの代替と、provider ごとのデバイスクラウドの代替を持ちます。`can_serve` は、そのどれか1つを受け入れます。その場合、サーバはデプロイが持つすべての配置を知らなければなりません。job のトークンを読む箇所もすべて、代替を扱えるようにする必要があります。worker は自分が提供できるものをすでに知っています。worker がプラットフォームを広告すれば、`can_serve` は部分集合の判定のままで済み、要求も配置を持たずに済みます。
- **環境の明示を残し、グループ化だけを足す。** 変更は小さく、実行意図も要りません。しかし、すべての呼び出し元が配置を知らなければならないという2つ目のコストが残り、CI の workflow は `devicefarm` を埋め込み続けます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 単位1 — グループの登録
- [ ] 単位2 — 実行意図
- [ ] 単位3 — グループ単位の lease と上限
- [ ] 単位4 — upload の共有
- [ ] 単位5 — プラットフォームによるルーティング
- [ ] 単位6 — ドキュメント

## 参考

- [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out-ja.md)：シナリオごとのファンアウト。
- [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch-ja.md)：Device Farm の worker とスロットモデル。
- [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md)：worker の capability と `maxJobConcurrency`。
- [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md)：run ごとの選択としての環境。
- [BE-0465](../BE-0465-serve-ui-device-farm-fan-out/BE-0465-serve-ui-device-farm-fan-out-ja.md)：Replay タブのファンアウト。
- [BE-0468](../BE-0468-runson-family-and-any-of/BE-0468-runson-family-and-any-of-ja.md)：`anyof:` トークンと、`can_serve` を使う4つの判断箇所。
- `bajutsu/serve/operations/dispatch.py`（`start_run_set`）、`bajutsu/serve/server/db/sql_repository.py`（`lease_job`）、`bajutsu/serve/state/job_registry.py`（`try_register`）：本項目が変更する登録と lease のコード。
- `bajutsu/serve/capabilities.py`（`can_serve`）、`bajutsu/serve/cli/worker.py`（`_advertised_capabilities`）、`bajutsu/serve/batch_provider/device_farm_batch_provider.py`：本項目が変更するルーティングと worker のコード。

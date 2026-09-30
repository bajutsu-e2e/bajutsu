[English](BE-XXXX-github-check-run-reporting.md) · **日本語**

# BE-XXXX — CI から投入した run の集合を、GitHub の check run として PR に報告する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-github-check-run-reporting-ja.md) |
| 提案者 | [@paihu](https://github.com/paihu) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | 外部サービスとの連携 |
| 関連 | [BE-0170](../BE-0170-weighted-fair-org-dispatch/BE-0170-weighted-fair-org-dispatch-ja.md)、[BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity-ja.md)、[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md)、[BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth-ja.md)、[BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out-ja.md)、[BE-0431](../BE-0431-job-scoped-artifact-override/BE-0431-job-scoped-artifact-override-ja.md)、[BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications-ja.md)、[BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues-ja.md) |
<!-- /BE-METADATA -->

## はじめに

GitHub Actions の job は、自前でホストする `bajutsu serve` に run をすでに投入できます。job は
OpenID Connect（OIDC）トークンで認証し
（[BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity-ja.md)）、
ビルドをアップロードして、そのビルドに対して run を開始します
（[BE-0431](../BE-0431-job-scoped-artifact-override/BE-0431-job-scoped-artifact-override-ja.md)）。
しかし、pull request（PR）には serve から何も届きません。job は、すべての run が終わるまで
Actions の runner を占有したまま serve をポーリングするか、投入だけして PR に何も知らせないかの
どちらかを選ぶことになります。

本項目では、serve が外部の継続的インテグレーション（CI）システムとして結果を報告するようにします。
scenario の集合と commit を指定した1回の dispatch リクエストが、その commit 上の
**1つの GitHub check run** になります。serve は、job を queue に入れる前に check run を `queued`
で作成します。worker が最初の job を lease すると check run は `in_progress` に、すべての job が
終わると `completed` になります。conclusion は、各 job の決定的な判定を集約したものです。serve は、
デプロイがすでにログインに使っている GitHub App で check run を書き込みます
（[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md)）。serve からその App
を使うには、非公開リポジトリの config source のために
[BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth-ja.md)
が導入した App の資格情報の設定を用います。
集合を1回のリクエストで運ぶために、既存の `POST /api/run-set` を cloud-batch 専用
（[BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out-ja.md)）
から通常の worker の queue にも広げます。

## 動機

自前でホストする環境では、アプリをビルドした Actions の runner とは別に、自前の Mac や Linux の
worker で E2E テストを実行します。現状では、そのテストの判定は Actions の job を経由しなければ PR に
届きません。`POST /api/run` は job の id を返すだけなので、パイプラインは scenario ごとに
`GET /api/jobs/{id}` を終わるまでポーリングします。そのあいだ、Actions の runner は queue の待ち時間と
Simulator の実行時間を何もせずに過ごし、GitHub がホストする runner ではその時間にも課金されます。
待たずに終わるパイプラインでは PR に何の信号も残らないので、branch protection rule で必須にする
対象がありません。

外部システム向けに GitHub が用意している仕組みは Checks API です。check run は commit に付き、status と
conclusion を持ち、branch protection rule で必須の check として名前で指定できます。check run を作成
できるのは GitHub App だけです。ログインに GitHub App を使うデプロイは、その App をすでに登録して
います。App の資格情報から installation のトークンを発行する処理も、serve はすでに持っています
（BE-0224）。check run に
必要な状態も、serve はすでに把握しています。jobs テーブルがすべての job について `queued`、`leased`、
`done`、`failed` を記録しているからです
（[BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues-ja.md)）。
欠けているのは、jobs テーブルの状態を check run に運ぶ経路だけです。

その経路が正しく動くかどうかは、check run の単位で決まります。target ごとの check run は自然に
見えますが、完了を判断できません。`POST /api/run` は scenario を1つしか受け取らないので、
パイプラインは scenario ごとにリクエストを送ります。serve には、ある target に属するリクエストが
何本来るかを知る手段がありません。そのため、3本目の scenario が終わった時点で、それが最後なのかを
判断できません。scenario ごとの check run には逆の問題があります。branch protection rule に
すべての scenario を並べる必要があり、scenario を追加するたびに rule を編集することになります。
dispatch リクエストごとに1つの check run にすれば、どちらの問題も避けられます。リクエストが dispatch
の時点で構成する scenario を確定させるので、完了を判断できます。パイプラインは check run の名前を
一度だけ付けるので、branch protection rule も一度だけ名前を指定すれば済みます。

報告は判定の外側にとどまります。この境界は、webhook 通知について
[BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications-ja.md) が引いたものと
同じです。ロードマップの「採用しないもの」の一覧は、通知を「CI や通知の層の領分」としています。
本項目は Bajutsu をその層に移すものではありません。serve は、すでに計算した判定を、CI の層が読む
唯一の場所へ届けるだけです。大規模言語モデル（LLM）は関与せず、配信に失敗しても job の判定は
変わりません。

**検証できる成果。** `permissions: id-token: write` を宣言し、リポジトリの secret を持たない Actions
の workflow が、3つの scenario と PR の head commit を指定して `POST /api/run-set` を呼び、そのまま
終了します。PR の Checks タブには、直ちに `Bajutsu / <target>` が queued で表示されます。worker が最初の job を lease してからおよそ30秒以内に check run は in progress に
なり、3つのうち1つの scenario が失敗すると
`failure` で完了します。control plane が実行の途中で再起動しても、check run は `completed` に
到達します。`Bajutsu / <target>` を必須にした branch protection rule は、それまでマージを止めます。

## 詳細設計

作業は5つの単位に分かれ、互いに重複せず全体を漏れなく覆います（MECE）。集合を worker の queue に
dispatch すること、集合を記録すること、check run の状態を導くこと、その状態を GitHub に届けること、
テストとドキュメントの5つです。

### 単位1：`POST /api/run-set` が worker の queue に dispatch する

`bajutsu/serve/operations/dispatch.py` の `start_run_set` は、現状では cloud-batch の target だけを
扱います。`cloudBatch` を持たない target を拒否し、BE-0431 の artifact override のフィールドも
拒否します。この単位では cloud-batch の分岐をそのまま残し、それ以外のすべての target 向けに2つ目の
分岐を加えます。

| target | 構成する各 scenario がなるもの | artifact override |
|---|---|---|
| `cloudBatch` あり | 現状どおり cloud-batch の job | 現状どおり拒否 |
| `cloudBatch` なし | `start_run` が1つの scenario から作る job | `start_run` と同じく受け付ける |

worker 向けの分岐は、`start_run` の job 構築を scenario ごとに再利用し、コピーはしません。すべてを
検証してから1つ目を投入する既存の順序は保ちます。最初の job を登録する前にすべての scenario を
runnable に解決するので、未知の名前が1つあればリクエスト全体を拒否します。`scenariosArtifact` を指定したリクエストは、`scenarios` も列挙しなければならず、列挙がなければ 400 で拒否します。`scenarios` を省くと紐づいたストアを列挙するので、アップロードしたツリーにだけある scenario が黙って漏れるからです。

machine principal もこの endpoint を使えるようにします。`bajutsu/serve/gate.py` の `_MACHINE_PATHS`
に `("POST", "/api/run-set")` を加え、`start_run_set` は `start_run` と同じ `machine_org` 引数を
受け取ります。

### 単位2：check を伴うリクエストは、dispatch の前に集合を記録する

リクエストは `check` オブジェクトで報告を有効にします。`check` のないリクエストの動作は現状と
変わりません。

```json
{
  "target": "ios-app",
  "scenarios": ["login.yaml", "checkout.yaml", "search.yaml"],
  "binaryArtifact": "<sha256>",
  "check": { "headSha": "<40 hex characters>", "name": "Bajutsu / ios-app" }
}
```

- **`headSha`**：必須です。workflow は `github.event.pull_request.head.sha` を渡します。
  `pull_request` イベントの `GITHUB_SHA` は merge commit を指し、その commit に付けた check run は
  PR に表示されないからです。serve は値の形式を検査しますが、値の出どころは保証しません。誤った値は、
  後述するリポジトリの限定により、呼び出し側自身のリポジトリの中にとどまります。そこに残るリスクは
  「対象外とするもの」に明記します。
- **`name`**：既定値は `Bajutsu / <target>` です。1つの target を Actions の matrix で分割する
  パイプラインは、分割した部分ごとに名前を付けます。serve は、この区別を前提にせず強制します。
  未完了の集合のあいだは、`check_runs` の行が (リポジトリ, head SHA, 名前) で一意になるように、部分
  一意インデックスを張ります。同じ組を持つ check 付きの2つ目のリクエストは、1つ目の集合が完了する
  まで 409 で拒否します。集合が完了した後の再実行は、新しい行として記録します。行は、GitHub が `completed` を受け付けるか、拒否された送信が続いて閉じるまで、未完了のままです。lease のタイムアウトより古くなっても check run の id を持たない行は、作成の応答を受け取る前に control plane が止まったものです。完了させる check run がないので、掃き出しはその行を削除し、組を解放します。

**リポジトリはリクエスト本文ではなく machine session から取ります。** BE-0414 が発行する machine
session の identity は、検証済みの OIDC の `repository` claim が示すリポジトリを名指しています。
serve は、そのリポジトリにだけ check run を書き込みます。本文のフィールドでリポジトリを指定できる
ようにすると、ある workflow が、App の届く別のリポジトリに check を書き込めてしまいます。人間の
session はリポジトリを持たないので、人間が `check` を付けたリクエストは 400 で拒否します。`cloudBatch` の target に `check` を付けたリクエストも 400 で拒否します。machine session はデータベースを持つ serve にしか存在せず、cloud-batch の job はまだその worker の queue からは実行できないからです（`bajutsu/serve/jobs.py` の `_run_batch_job`）。

serve は、job を登録する前に、新しい `check_runs` テーブルに集合を記録します。行には、org、
リポジトリ、head SHA、名前、GitHub の check run の id を持たせます。構成する各 scenario は、
scenario 名と job の id の組として持たせます。job の id はこの時点ではまだ空です。テーブルは
`bajutsu/serve/server/migrations/versions/` の下の Alembic revision で作成します。

**check を伴う集合は、全体を dispatch するか、1つも dispatch しないかのどちらかです。**
現状の `start_run_set` は、同時実行数の上限が job を拒否すると途中で止まり、それまでに dispatch した
job だけを返します。そのような一部だけの集合で check run を完了させると、欠けた scenario を含まない
結論になります。パイプラインはすでに終了しているので、欠けた scenario を再試行する者もいません。
そこで、`check` を伴うリクエストは、構成するすべての scenario を一度に登録するか、1つも登録しない
かのどちらかにします。`bajutsu/serve/state/job_registry.py` の `try_register` は、すでに1つのロックの
中で数えて登録しています。集合向けの版は、同じロックの中で集合全体を各上限と照らし合わせます。
集合が上限に収まらない場合、serve は行を削除し、check run を作る前に 429 を返します。パイプラインは
再試行できます。ただし、集合の大きさが上限そのものを超える場合は、queue で待つ job も枠を占めるので、
いつまでも収まりません。この場合、serve は行を削除し、上限を名指しして 400 で拒否します。成功しえない
リクエストを、パイプラインが再試行し続けないようにするためです。400 のメッセージには、超えた上限の名前と、集合に必要な大きさを示します。
今の上限は低く設定されています。全体の上限（`--max-concurrent-runs`）の既定は4で、実行中の job
だけでなく queue で待つ job も数えます。そのため、既定のままでは5本の集合は収まりません。この上限は、
queue で待てる job の数と、同時に実行できる job の数という、2つの意味を兼ねています。データベースを使うデプロイでは、worker はそれぞれ一度に1つの job だけを lease するので、worker の数が後者をすでに決めます。実際に効くのは前者だけです。
投入時の queue の深さの上限と、worker が job を lease するときに判定する同時実行数の上限に分けることは、
[BE-0170](../BE-0170-weighted-fair-org-dispatch/BE-0170-weighted-fair-org-dispatch-ja.md)
に任せます。BE-0170 は、上限を超えた job を拒否せずに保留することを計画しています。それが入るまで、
より大きな集合を使いたい運用者は、`--max-concurrent-runs` と、設定している場合は
`BAJUTSU_MAX_CONCURRENT_PER_ORG` を、最大の集合の大きさ以上に上げてください。

dispatch の順序は、この規則から決まります。serve は、行を記録し、集合全体を登録し、check run を
作成し（単位4）、最後に job を queue に入れます。queue に入れる途中で control plane が止まると、
job の id を持たない scenario が行に残ります。この行は単位3で完了させます。

### 単位3：check run の状態を、構成する job から導く

serve は、check run の状態を、構成する job だけから導きます。ほかの経路でこの状態を設定することはありません。

| 構成する job | check run の `status` | `conclusion` |
|---|---|---|
| 猶予の後も job の id を持たない scenario がある | `completed` | `failure` |
| すべての scenario が `queued` か、まだ queue に入っていない | `queued` | — |
| 少なくとも1つの job が lease 済みか終了済みで、未完了の scenario がある | `in_progress` | — |
| すべての scenario の job が終了し、各 run が通過した | `completed` | `success` |
| すべての scenario の job が終了し、失敗した run がある | `completed` | `failure` |
| すべての scenario の job が終了し、run を残さずに失敗した job がある | `completed` | `failure` |

表は上から照合し、最初に当てはまる行で状態を決めます。まだ queue に入っていない scenario は、未完了として数えます。queue に入れる処理は1回の dispatch
リクエストの中で終わります。そのため、行が lease のタイムアウト（既定で120秒）より古いのに job の id
を持たない scenario が残っていれば、control plane がその処理の途中で止まったことになります。serve は
その行を `failure` で完了させ、summary に、queue に入らなかった scenario を名指しします。再投入も
待機もしません。パイプラインは集合を dispatch し直せて、この行が完了した後であれば、409 の規則にも
当たりません。すでに queue に入った job は実行を続け、その判定はレポートから見られます。

run を残さずに失敗した job の行は、lease の試行回数の上限を超えた job のように、`failed` の status
で終わった job を扱います。表のうち、失敗した run がある行と、run を残さずに失敗した job がある行の
2つは、現状の jobs テーブルでは区別できません。`bajutsu/serve/operations/worker.py` の
`worker_result` は、scenario が失敗した job を `fail_job` で記録します。これは、試行回数の上限を
超えて回収された job と同じ `failed` の status です。しかも `fail_job` はエラーだけを保存し、run の id
を捨てます。そこで本単位では、失敗した job の行にも worker が返した `runId` を残し、構成する job から
run とその判定を引けるようにします。summary では、この原因を scenario の失敗とは区別して書きます。
直し方が異なるからです。
表の入力はどれも、保存済みの job の status、保存済みの run の判定、行の経過時間のいずれかです。そのため導出は決定的で、LLM は関与しません。
serve は、導いた状態を保存しません。配信のたびに、構成する job の保存済みの行から状態を導き直すので、
再起動で失われるものは、後の導出で取り戻せます。ただし、job の遷移のうち1つだけは、フックを必要とします。
`worker_result` は、結果をコミットした後に、その集合の行を、同じ行のロックのもとで、一般の掃き出しを経ずに直接確保し、その行の状態を送ります。これがないと、worker のプールが空になる場合、たとえば queue が
空になると終了するオンデマンドの Mac では、最後の結果の後に lease のポーリングも heartbeat も
起きません。そのため `completed` の更新は、単位4の定期タスクを待つことになります。status が後戻りしないように、規則を
1つ置きます。`reclaim_expired_leases` が job を queue に戻すと、集合からは再び `queued` が導かれます。
そのため serve は、GitHub が最後に受け付けた status より前の status を送りません。

worker のなかに capability を満たすものがなく queue に残り続ける job は、check run を `queued`
のままにします。運用者への合図としては、BE-0166 がすでに出している unroutable job の信号を使います。
そのような check run をタイムアウトで完了させる仕組みは、後の課題とします。そのような集合が未完了のあいだ、同じ組の再 dispatch は 409 になります。必要な capability を持つ worker を起動すれば、集合は完了します。

### 単位4：serve が状態を GitHub に確実に届ける

**check run を書き込む GitHub App。** serve は、GitHub の資格情報を2つの設定群から読みます。現状では、
2つの設定群は互いに独立しています。

| 設定群 | 設定 | 現状の serve での用途 |
|---|---|---|
| ログイン（BE-0313） | `BAJUTSU_OAUTH_GITHUB_CLIENT_ID`、`_CLIENT_SECRET`、`_REDIRECT_URI` | 利用者をログインさせ、ユーザーのトークンで所属する org と team を読みます |
| App の資格情報（BE-0224） | `BAJUTSU_GITHUB_APP_ID`、その秘密鍵、任意の `BAJUTSU_GITHUB_APP_INSTALLATION_ID` | 非公開リポジトリの config source 用に installation のトークンを発行します |

check の書き込みには App の資格情報の設定群を使います。GitHub が check run を受け付けるのは、GitHub
App の installation のトークンからだけだからです。GitHub App の登録は、client ID と secret に加えて、
App ID と秘密鍵も持ちます。そのため、1つの登録で両方の設定群を埋められます。OAuth App が持つのは
client ID と secret だけなので、OAuth App が check run を書き込むことはできません。

本項目では、デプロイが GitHub App でログインしていることを前提にし、その App に check run を書き込ませ
ます。運用者は、ログイン用の App の App ID と秘密鍵を、App の資格情報として設定します。デプロイが
とりうる形態は次のとおりです。

| 形態 | ログインに使うもの | App の資格情報が指すもの | check run を書き込む App | 非公開の config source を読む App |
|---|---|---|---|---|
| **1つの App（本項目の前提）** | GitHub App A | A | A | A |
| OAuth App でログイン | OAuth App | GitHub App B（config source 用の App でもよい） | B | B |
| 2つの GitHub App | GitHub App A | config source 用の GitHub App B | B | B |

最後の行は制約を示しています。serve が持つ App の資格情報は1つだけなので、check run を書き込む App は
常に config source を読む App と同じになります。config source を別の App に任せたまま、ログイン用の
App で check run を書き込むには、2つ目の資格情報の設定群が必要です。本項目ではこれを扱いません。

**ログインの手順書では GitHub App を推奨します。** 現状の `docs/self-hosting.md` の「2. Add GitHub OAuth (optional)」節は、運用者に
OAuth App を作るよう案内しています。GitHub 自身の指針は、細かな権限、リポジトリ単位のアクセス、
短命なトークンを理由に、GitHub App を推奨しています。GitHub が OAuth App を勧めるのは enterprise
単位のリソースを扱う場合で、ログインはそのリソースを読みません。そこで本項目では、この節を、ログインに
GitHub App を登録する手順へ書き換え、OAuth App は引き続き使える代替として残します。こうすると、同じ
登録が config source と check run も担い、2つ目の App は要りません。

**App の資格情報を設定すると、config source も App 経由に切り替わります。**
`bajutsu/common/config_source/_functions.py` の config source は、`BAJUTSU_GITHUB_APP_ID` が設定されて
いれば、personal access token（PAT）より App の installation のトークンを優先します。現状 PAT で非公開の
config リポジトリを読んでいるデプロイは、そのリポジトリにも App A を `contents: read` でインストール
する必要があります。インストールしないと、App の資格情報を設定した時点で config source を解決できなく
なります。

1つの App の形態では、App A に次の権限とインストール先を持たせます。

| 用途 | リポジトリ権限 | インストール先 |
|---|---|---|
| ログイン：org と team の対応づけ | 不要（GitHub は `/user/orgs` と `/user/teams` に権限を定めていません） | `githubOrgs`、`githubTeams`、`editorTeams`、`BAJUTSU_OAUTH_ADMIN_TEAMS` が指すすべての org |
| config source | `contents: read` | config のリポジトリ |
| check run | `checks: write` | check を伴う run を dispatch する workflow を持つすべてのリポジトリ |

インストール済みの App に `checks: write` を加えると、各 installation の所有者が承認するまで、check の
書き込みは成功しません。

この前提が成り立つには、ログインの挙動を2つ、実際のデプロイで確かめる必要があります。ログインは
`/user/orgs` と `/user/teams` を読み、GitHub はどちらも GitHub App のユーザーのトークンで使えると
しています。一方で `/user/orgs` のリファレンスには、fine-grained なアクセストークンでは空のリストが
返るとあり、GitHub App のユーザーのトークンがそれに当たるかは書かれていません。さらに GitHub は、
このトークンが届く範囲を、App がインストールされたアカウントに限っています。著者のデプロイは GitHub App で
ログインしており、org と team の対応づけも働いているので、そこではどちらも確かめられています。OAuth
App から移るデプロイは、それでも自身の org に対して先に確かめる必要があります。org のリストが空になると、
誰もログインできなくなるからです。ログインはコールバックの後に GitHub のトークンを保持しません
（`bajutsu/serve/authz.py`）。そのため、GitHub App のユーザーのトークンが8時間で失効しても、ログインには
影響しません。

**トークン。** serve は、check の書き込み用のトークンを App の資格情報から発行します。check run を
付けるリポジトリは、config のリポジトリと異なることがあります。そのため serve は、config のリポジトリ
の installation を指す `BAJUTSU_GITHUB_APP_INSTALLATION_ID` の固定値を使わず、check run を付ける
リポジトリから installation を解決します。`bajutsu/common/github/app.py` の `installation_token` には、`permissions` と
`repositories` のリクエストフィールドを加えます。現状の `Fetch` の差し替え口はリクエスト本文を持たないので、JSON の本文を渡せるように広げます。check run の書き込みも同じ差し替え口を通し、App の JWT ではなく installation のトークンで認証します。check の書き込み用に発行するトークンは、対象の
1つのリポジトリに対する `checks: write` だけを持ちます。そのため App を使い回しても、どのトークンの
権限も広がりません。serve は、各トークンを1時間の有効期限の少し前までキャッシュします。

**作成は同期的に行い、失敗したらリクエストを拒否します。** serve は、dispatch リクエストの処理中に、
集合全体を登録した後、job を queue に入れる前に、check run を `queued` で作成します。GitHub の
応答から check run の id を得て、行に保存します。作成の呼び出しにも、掃き出しの中の GitHub への呼び出しと同じ短いタイムアウトを付けます。そのため作成は、lease のタイムアウトを過ぎて掃き出しが check run の id を持たない行を削除するより十分前に終わります。掃き出しは、check run の id をまだ持たない行には何も送りません。作成に失敗した場合、serve は登録を解放し、行を
削除して、リクエストを 502 で拒否します。そのため、job は1つも queue に入りません。GitHub の一時的な
エラーを含め、作成の失敗はすべてこの扱いになり、パイプラインは再試行できます。パイプラインが最も
知る必要がある失敗は、設定の誤りである次の2つです。App がリポジトリにインストールされていない場合と、
権限がまだ承認されていない場合です。502 によってどちらも直ちにわかるので、必須の check がいつまでも
現れない PR を後から見つける、という事態を避けられます。

**更新は、再起動を越えて順序どおりに届けます。** すべての更新は、行のロックのもとで、2種類の送り手のいずれかが届けます。
同じ掃き出しを、`lease_job` は `reclaim_expired_leases` と並べて、`heartbeat_job` は lease の更新をコミットした後に、それぞれ実行します。`worker_result` は、結果をコミットした後に、自分の集合の行だけを、掃き出しを経ずに直接送ります。データベースを使う control plane では、小さな定期タスクも、確認の間隔ごとに同じ掃き出しを実行します。
そのため、猶予の後の `failure`、check run の id を持たない行の削除、拒否された送信の再試行は、どの
worker もポーリングしないときにも実行されます。worker が引き金になる掃き出しは更新を速く届け、定期の
掃き出しは更新を確実に届けます。定期タスクは各 replica で動きますが、ほかの送り手と同じく、行の
ロックが送信の順序を保ちます。`check_runs` の行には、GitHub が最後に受け付けた状態と、次に試みる時刻を持たせます。

掃き出しは、試行時刻を迎えた未完了の行を、試行時刻の古い順にたどります。各行を
`SELECT … FOR UPDATE SKIP LOCKED` で確保し、その行の状態を導きます。訪れた行ごとに、掃き出しは
次の試行時刻を、確認の間隔（既定で数秒）だけ先に記録します。そのため、手の空いた worker が何台
ポーリングしても、動きのない行への訪問は間隔ごとに1回で済みます。状態が受け付け済みのものと同じ行、または status が受け付け済みの status より前の行にかかるのは、その1回の行の更新だけで、掃き出しは次の行へ進みます。`reclaim_expired_leases` が job を queue に戻した集合は、GitHub が `in_progress` を受け付けていても `queued` と導かれますが、単位3がその送信を禁じています。状態が異なる残りの最初の行には状態全体を送り、掃き出しはそこで止まります。その行を解放する前に、受け付けられた状態を記録します。
送信を拒否された場合は、間隔を空けた次の試行時刻を記録します。そのため、遅延は未完了の行の数では
なく、状態が変わった行の数に応じて伸びます。更新の負荷も、手の空いた worker の数ではなく、未完了の
行を確認の間隔で割った数に収まります。

行のロックによって、1つの行への送信は control plane の replica をまたいでも直列になります。別の
replica が送信中の行は、2つ目の replica が飛ばします。次の送り手はロックを取ってから状態を導くので、
最後に送られた状態より古い状態を送ることはありません。1回の掃き出しで送るのは1行までなので、
GitHub が障害を起こしても、worker の lease のポーリングや heartbeat が遅れるのは、上限のある送信
1回分までです。代償は遅延です。更新が GitHub に届くのは、job の遷移の時点ではなく、次の掃き出しの時点、つまり lease のポーリング、heartbeat、定期タスクのいずれかが引き金になる時点です。結果のコミットだけは例外で、`worker_result` が自分の行をすぐに送ります。確認の間隔は、その待ち時間に最大でその長さだけを足します。job を実行中の worker が heartbeat を送るのは既定で30秒ごとですが、すべての worker が実行中でも定期タスクが掃き出しを続けるので、更新の待ち時間は heartbeat の間隔ではなく、既定で数秒に収まります。掃き出しの中の GitHub への呼び出しには
短いタイムアウトを付けます。そのため、heartbeat の間隔と送信1回分を足しても、120秒の lease の
タイムアウトを十分に下回ります。

**内容。** summary には、構成する scenario ごとに1行を置きます。各行は scenario、判定、レポートへの
リンクです。レポートへのリンクには、serve の外部から見た base URL が必要です。この URL は新しい
デプロイ設定で与え、設定がなければリンクを省きます。`external_id` には行の id を入れます。web UI には
集合を表示するページがまだないので、`details_url` は設定しません。

GitHub が拒否し続ける配信は、リポジトリと check run の id を添えてログに残します。GitHub がクライアントエラーで拒否した送信が連続して上限（既定で10回）に達すると、その行は閉じます。レート制限の応答、5xx、タイムアウトは、間隔を空けて再試行しますが、回数には数えません。check run は GitHub が最後に見た状態のまま残り、組は解放されるので、`checks: write` の権限の取り消しや、force push で消えた head commit が、再 dispatch をいつまでも妨げることはありません。拒否が job の判定や run の記録に触れることはありません。

### 単位5：テストとドキュメント

- **テスト**：`app.py` の `fetch` の差し替え口を、偽の GitHub transport に置き換えます。BE-0224 の
  テストと同じ差し替え口です。テストでは、dispatch の2つの分岐と、単位3の表の各行、全体か無しかの
  登録を確かめます。リポジトリの限定、同じ組への2つ目のリクエストに対する 409、作成に失敗したときの 502 も確かめます。再起動のテストでは、
  送信を落とし、次の掃き出しで届くことを確かめます。worker のいないテストでは、定期タスクが行を完了させ、拒否された送信を再試行すること、および拒否の上限が行を閉じて組を解放することを確かめます。worker のプールが空になるテストでは、変化を待つほかの行が試行時刻を迎えていても、`worker_result` が自分の行を `completed` にすることを確かめます。送り手が2つあるテストは、SQLite では行がロックされないので Postgres の lane（BE-0309）で実行し、1つの行への送信が順序どおりに進むことを確かめます。
- **ドキュメント**：`docs/self-hosting.md` と `docs/ja/` のミラーでは、ログインの節を GitHub App
  推奨に書き換え、OAuth App を代替として残します。OAuth App から移る運用者には、切り替える前に
  `/user/orgs` と `/user/teams` が自身の org を返すことを確かめるよう案内し、`read:org` スコープの記述は
  OAuth App の経路に限ります。あわせて、GitHub App のとりうる形態、1つの App の
  形態での権限とインストールの手順、および workflow の例を加えます。`docs/architecture.md` には、`check_runs` テーブルと配信の
  経路を記録します。

### 対象外とするもの

- **GitHub からの webhook の受信**：`workflow_job` イベントで serve が run を開始することも、
  check run の *Re-run* ボタンで再実行することも、GitHub から serve に到達できることが前提になります。
  本項目では、serve と GitHub のあいだの通信を serve からの送信だけに限ります。
- **annotation**：失敗した step を scenario ファイルの行に対応させるには、リポジトリ内での scenario
  のパスが必要です。アップロードされた scenario のツリーは、そのパスを持っていません。
- **fork からの pull request**：fork の workflow には secret が渡らず、`GITHUB_TOKEN` も読み取り専用
  です。このような workflow が serve 向けの OIDC トークンを発行できるかは、まだ確認していません。
- **新しい push の後で、古い commit の check run を取り消すこと。**
- **`headSha` を workflow 自身の commit と照合すること。** 呼び出し側の workflow は信頼の境界の内側に
  あります。この立場は、[BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity-ja.md)
  が、受け入れるリポジトリについてとっているものと同じです。OIDC のトークンを取得できる workflow は、
  自身のリポジトリの任意の commit を名指しできます。そのため、意図した scenario を実行しないまま、別の
  pull request の必須 check を green にできます。呼び出し側は scenario の列挙も決められるので、SHA を
  照合しても、workflow が自身の commit に対して必ず通る集合を dispatch するのは防げません。serve は
  値の形式だけを検査します。このリスクを受け入れられないデプロイは、BE-0414 の `allowedRepositories` の項目を `job_workflow_ref` か `environment` で絞り込んでください。そうすると、トークンを取得できるのは、レビュー済みの workflow か、Environment のレビュアーが承認した job だけになります。

## 検討した代替案

| 代替案 | 採用しない理由 |
|---|---|
| target ごとに1つの check run | `POST /api/run` は1回に1つの scenario しか運ばないので、serve はどのリクエストが target の最後なのかを判断できず、check run を完了させられません。 |
| job ごとに1つの check run | branch protection rule にすべての scenario を並べる必要があり、scenario を追加するたびに rule を編集することになります。 |
| 複数のリクエストにまたがる group を開いて最後に閉じる | 閉じる前に workflow が落ちると check run が保留のまま残ります。後始末には、serve が知りえない、最も遅いパイプラインの dispatch の繰り返しに見合うタイムアウトが必要です。本項目の猶予は、1回の dispatch リクエストの長さで上限が決まります。 |
| check run を作らず、Actions の job が完了までポーリングする | serve の変更は不要ですが、queue の待ち時間のあいだ課金される runner を占有し、PR からのリンク先もレポートではなく Actions のログになります。 |
| Commit Status API | App は不要ですが、status には短い説明とリンクが1つずつしかなく、scenario ごとの summary を載せられません。 |
| check の書き込み専用に別の App を用意する | App の権限を分けられますが、serve が持つ App の資格情報は1つなので、その App が config source も引き受けることになります。`permissions` と `repositories` で絞ったトークンなら、運用する App を1つにしたまま、トークン単位の最小権限を得られます。 |
| `POST /api/run` を拡張してリストを受け取る | 集合を dispatch する前に全体を検証する `POST /api/run-set` がすでにあり、集合を扱う口が2つに増えます。 |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 単位1：`POST /api/run-set` が worker の queue に dispatch する
- [ ] 単位2：check を伴うリクエストは、dispatch の前に集合を記録する
- [ ] 単位3：check run の状態を、構成する job から導く
- [ ] 単位4：serve が状態を GitHub に確実に届ける
- [ ] 単位5：テストとドキュメント

## 参考

- GitHub Docs「[REST API endpoints for check runs](https://docs.github.com/en/rest/checks/runs)」：
  check run の作成には `checks` 権限を持つ GitHub App と `head_sha` が必要で、`details_url` と
  `external_id` は任意です。
- GitHub Docs「[Create an installation access token for an app](https://docs.github.com/en/rest/apps/apps#create-an-installation-access-token-for-an-app)」：
  `permissions` と `repositories` のフィールド、および1時間の有効期限。
- GitHub Docs「[Modifying a GitHub App registration](https://docs.github.com/en/apps/maintaining-github-apps/modifying-a-github-app-registration)」：
  新しく加えた権限は、各 installation が承認する必要があります。
- GitHub Docs「[Events that trigger workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)」：
  `pull_request` の `GITHUB_SHA` は merge commit で、fork の workflow には secret が渡りません。
- [BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity-ja.md)：machine
  session と、そのリポジトリの identity。
- [BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth-ja.md)：
  本項目が再利用する App の資格情報の設定。
- [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md)：GitHub のログインと、
  ログイン用の App が担う org と team の対応づけ。
- GitHub Docs「[Differences between GitHub Apps and OAuth apps](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/differences-between-github-apps-and-oauth-apps)」：
  「In general, GitHub Apps are preferred over OAuth apps」とあり、例外は enterprise 単位のリソースです。
- GitHub Docs「[Authenticating with a GitHub App on behalf of a user](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-with-a-github-app-on-behalf-of-a-user)」：
  ユーザーのトークンが届くのは、App がインストールされたアカウントだけです。
- GitHub Docs「[Endpoints available for GitHub App user access tokens](https://docs.github.com/en/rest/authentication/endpoints-available-for-github-app-user-access-tokens)」：
  `/user/orgs` と `/user/teams` が載っています。
- GitHub Docs「[List organizations for the authenticated user](https://docs.github.com/en/rest/orgs/orgs#list-organizations-for-the-authenticated-user)」：
  fine-grained なアクセストークンでは空のリストが返ります。
- [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out-ja.md)：
  `POST /api/run-set` の出自。
- [BE-0431](../BE-0431-job-scoped-artifact-override/BE-0431-job-scoped-artifact-override-ja.md)：
  CI の dispatch が運ぶ、job ごとの artifact override。

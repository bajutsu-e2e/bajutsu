[English](BE-XXXX-run-set-github-check.md) · **日本語**

# BE-XXXX — run-set のグループを GitHub の check run として公開する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-run-set-github-check-ja.md) |
| 提案者 | [@paihu](https://github.com/paihu) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | 外部サービスとの連携 |
| 関連 | [BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications-ja.md), [BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth-ja.md), [BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity-ja.md), [BE-0484](../BE-0484-job-group-lease/BE-0484-job-group-lease-ja.md) |
<!-- /BE-METADATA -->

## はじめに

この項目は、`serve` が `run-set` のグループを、プルリクエストが検証するコミットの上に1つの GitHub の check run として公開できるようにします。check はグループの進行に合わせて `queued`、`in_progress`、`completed` と遷移します。出力には、合格しなかった各シナリオの結果と理由を載せ、合格したシナリオの数を示します。

`run-set` は、ターゲットのシナリオごとに1つのジョブを、id を持つ**グループ**として登録します（[BE-0484](../BE-0484-job-group-lease/BE-0484-job-group-lease-ja.md)）。この項目は、3つの**グループイベント**（`queued`、`started`、`completed`）と、イベントを受け取る宛先である **sink** を追加します。sink はオペレーターが定義します。sink の `format` は、イベントを1つのサービスへの呼び出しに変換するアダプターを指します。最初の format は `github-check` です。後から汎用の署名付き Webhook のような format を足すときは、アダプターを1つ追加するだけで、イベントにも実行の経路にも手を入れません。

## 動機

GitHub Actions のジョブは、すでにグループを投入できます。ジョブは OpenID Connect（OIDC）のトークンで `serve` に認証し（[BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity-ja.md)）、`POST /api/run-set` を呼びます。結果をプルリクエストに届ける経路は、現在1つしかありません。ジョブがグループの結論が出るまで `GET /api/run-set/<groupId>` をポーリングし、それに合わせた状態で終了する経路です。そのあいだ、runner は何もせずに待ち続けます。デバイスクラウド上の40シナリオのグループは runner を1時間占有することがあり、何もしていない runner の時間にも課金されます。

check に必要な情報は、グループがすでに持っています。`GET /api/run-set/<groupId>` は、`pending`、`success`、`failure`、`cancelled` のいずれかを `conclusion` として返します。BE-0484 はこの値の名前を GitHub の check run の conclusion に合わせて選び、公開そのものは範囲外としました。欠けているのは、グループを見守って GitHub に書き込む部品です。グループを保持しているのは `serve` なので、待機する runner なしで check を書けるのは `serve` だけです。

既存の通知機能は、この用途に届きません。[BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications-ja.md) の `notify:` のリストは、1つの `bajutsu run` のプロセスの中で、実行ごとに一度発火し、URL に POST します。グループは多数のワーカー上の多数の実行にまたがり、そのライフサイクルは `serve` の上にしか存在しません。また、GitHub の check run は、URL への1回の POST では表現できません。呼び出し側は check run を作成して id を受け取り、後からその id を更新します。さらに、check run を作成できるのは GitHub App だけなので、GitHub App のインストールトークンが要ります。

観測できる違いは3つあります。プルリクエストの checks の一覧に `bajutsu / <target>` の check が出て、queued から in progress を経て結論へ遷移します。check の出力に、合格しなかったシナリオがその理由とともに載ります。グループを投入した Actions のジョブは、グループを待たずに投入の直後に終了します。

## 詳細設計

### 用語

- **グループイベント**：グループのライフサイクルの3つの時点です。`queued` はグループの登録時に発火します。`started` はワーカーがグループのジョブを初めてリースしたときに、`completed` はグループの `conclusion` が `pending` でなくなったときに発火します。各イベントは、グループと sink の組ごとに高々一度発火します。
- **sink**：オペレーターが定義する、名前の付いた宛先です。`name` と `format` に加えて、その format が必要とするフィールドを持ちます。
- **アダプター**：1つの `format` を担うコードです。イベント、グループのスナップショット、sink、その sink に対するリクエストの文脈、同じグループと sink の組で、それまでのイベントが返した最新の参照を受け取ります。そして次に保持する参照を返します。`github-check` では、参照は check run の id です。

### sink の定義

オペレーターは sink をファイルに列挙し、そのパスを `--group-notify-config` か環境変数 `BAJUTSU_GROUP_NOTIFY_CONFIG` で `serve` に渡します。このファイルは GitHub App の認証情報と同じくオペレーターの設定であり、どの org の config にも含まれません。

```yaml
groupNotify:
  - name: github
    format: github-check
    checkName: "bajutsu / {target}"
```

- `checkName` は check run の名前です。プレースホルダーは `{target}` だけで、グループのターゲットに展開されます。既定値は `bajutsu / {target}` です。
- この項目では、`format` が受け付けるのは `github-check` だけです。未知の format があると、`serve` は sink の名前を示すエラーで起動を中止します。
- `github-check` の sink には、Git の config の取得元がすでに読んでいる GitHub App が必要です（[BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth-ja.md)）。`BAJUTSU_GITHUB_APP_ID` に加えて、`BAJUTSU_GITHUB_APP_PRIVATE_KEY` か `BAJUTSU_GITHUB_APP_PRIVATE_KEY_FILE` を使います。`github-check` の sink があるのに App が設定されていなければ、`serve` は起動を拒否します。App には、公開先の各リポジトリで `checks: write` の権限が必要です。

リクエストが指定するのは sink の名前であって、URL ではありません。リクエストから URL を受け取ると、どの呼び出し側でも `serve` から自身のネットワークへリクエストを送らせることができ、サーバーサイドリクエストフォージェリ（SSRF）の経路になります。宛先をすべてオペレーターの設定で定義すれば、その経路は閉じます。

### リクエストでの sink の選択

`POST /api/run-set` は、省略可能なフィールド `notify` を1つ追加で受け取ります。sink の選択のリストです。

```json
{"notify": [{"sink": "github", "headSha": "<40 桁の 16 進数>"}]}
```

- `sink` は、オペレーターのファイルにある sink の名前でなければなりません。未知の名前にはステータス 400 を返します。
- `github-check` の選択には `headSha` が必要で、値は小文字の16進数40桁です。それ以外の値はステータス 400 で拒否します。
- リポジトリは、呼び出し側のマシンセッションから取ります。OIDC の交換で検証済みの `repository` クレームです。そのようなセッションを持たない呼び出し側（ブラウザーのセッションや共有トークン）が `github-check` の sink を指定すると、ステータス 400 を返します。こうしてワークフローは、自分のリポジトリにだけ check を書けます。
- リクエストは、トークンの `sha` クレームではなく `headSha` を受け取ります。`pull_request` イベントでは、`GITHUB_SHA` と `sha` クレームは、GitHub がその実行のために作るマージコミットを指します。そのコミットに付けた check run は、プルリクエストに表示されません。ワークフローは代わりに `github.event.pull_request.head.sha` を渡します。
- `notify` のリストは、リクエストダイジェストに加えます。マシンセッションが解決したリポジトリも加えます。そのリポジトリが、check の書き込み先を決めるからです。同じグループキーで、そのどちらかが異なるリクエストには、現在ほかの違いに対してそうしているのと同じく、ステータス 409 を返します。
- グループは、選択とリポジトリを `job_groups` の新しい列 `notify` に保存します。

### イベントの記録

グループイベントは、新しいテーブル `group_events` の1行です。行は、グループ、sink、イベント、配信の状態、試行回数、次の試行の時刻、アダプターの参照を持ちます。`completed` の行は、アダプターが描画するグループのスナップショットも持ちます。行は、イベントの原因となる変更と同じストアのトランザクションで書き込みます。

- `queued`：グループとそのジョブを登録するトランザクション
- `started`：グループの最初のジョブをリースするトランザクション
- `completed`：グループの最後の未完了のジョブを終端の状態に移すトランザクション。終端の経路は `complete_job`、`fail_job`、`reclaim_expired_leases` の3つで、取り消しでは `cancel_job` と `cancel_leased_job` も加わります。

イベントを同じトランザクションで書くので、ストアにすでに反映された変更のイベントが、クラッシュで失われることはありません。

check の出力は、文面を解析せずに、ワーカーの喪失と報告されたエラーを区別できなければなりません。現在、`reclaim_expired_leases` は `{"error": "lease expired after N attempts"}` だけを書き、`fail_job` はワーカーの自由な文面を同じキーに書きます。そこでこの項目は、回収の結果に、構造化された理由と試行回数を加えます。たとえば `{"reason": "lease-expired", "attempts": N}` です。アダプターはこのフィールドを読み、メッセージを照合しません。

`completed` のスナップショットは、行を最初に確保したときに一度だけ作り、行に保存します。長いバックオフの後の再試行でも、成果物を読み直すことはありません。シナリオの名前は、ジョブが保存した spec の実行意図から取ります。`failed` の行に示す失敗の行は、ジョブの run id が指す実行のマニフェストから取ります。マニフェストが読めないときは、終了コードと `no failure detail recorded` という注記を示します。

### イベントの配信

`serve` のプロセス上のループが、未配信の行を配信します。ループは、ワーカーがジョブを取得するのと同じく、期限付きの取得で行を確保します。そのため、1つのストアを共有する2つの `serve` のプロセスが、同じ行を同時に配信することはありません。確保は期限切れになるので、同じ行が2回以上配信されることがあります（at-least-once の配信）。作成の呼び出しが冪等でないアダプターは、2つめのオブジェクトを作らずに参照を回復しなければなりません。`github-check` のアダプターは、作成の前に、sink の check の名前で `head_sha` のすべての check run を一覧し（既定では最新のものしか返らないため `filter=all` を指定します）、`external_id` がグループの id と一致するものを採用します。

- 1つのグループと sink の組のイベントは、順番どおりに配信します。`started` の行は、同じグループと sink の `queued` の行が配信されるか放棄されるまで待ちます。
- プロセスは、自分のファイルが定義する sink の行だけを確保します。どのプロセスも定義していない sink の行は確保されないので、配信のループのどの回でもエラーを起こしません。そのような行は、バックオフの許す期間を過ぎると `abandoned` になり、その sink の名前が運用ログに記録されます。そのため、古いファイルのまま動いているレプリカが、更新済みのレプリカが新しく加えた sink の行を放棄することはありません。その format を示す定義がないので、放棄のメトリクスはこの行を `format="unknown"` で数えます。
- 配信に失敗した行は、上限のある指数バックオフで再試行します。最後の試行でも失敗した行は `abandoned` になります。放棄は運用ログに記録し、カウンターのメトリクス `bajutsu_group_events_abandoned_total` で format ごとに数えます。
- 前のイベントの参照を持たないアダプターは、最初からやり直します。`github-check` では、`queued` の行が放棄されていた場合、`completed` のイベントが完了状態の check run を新しく作成します。
- 配信は、ジョブ、グループ、`conclusion` のいずれも変えません。グループの判定は、`completed` の行ができる前に確定しています。

### `github-check` のアダプター

アダプターは、保存されたリポジトリに対する App のインストールを解決し、インストールトークンを発行します。現在の `bajutsu/common/github/app.py` は、Git の config の取得元のためにトークンを発行しています。アダプターには、リポジトリをキーにした変種が必要です。

| イベント | GitHub の呼び出し | フィールド |
|---|---|---|
| `queued` | `GET /repos/{owner}/{repo}/commits/{head_sha}/check-runs?check_name=…&filter=all` で、`external_id` がグループの id と一致する run を採用し、なければ `POST /repos/{owner}/{repo}/check-runs` | `name`、`head_sha`、`status: queued`、グループの id を入れた `external_id` |
| `started` | `PATCH /repos/{owner}/{repo}/check-runs/{id}` | `status: in_progress`、`started_at` |
| `completed` | `PATCH /repos/{owner}/{repo}/check-runs/{id}` | `status: completed`、`conclusion`、`completed_at`、`output` |

グループの `conclusion` は、同じ名前の check の `conclusion`（`success`、`failure`、`cancelled`）に対応させます。

### check の出力

`output.title` には、`3 of 40 scenarios did not pass` のような件数を書きます。`output.summary` には、合格しなかったシナリオごとに1行の表を置き、その後に合格したシナリオの数を書きます。各行は、シナリオ、結果、理由を示します。

| 結果 | 条件 | 示す理由 |
|---|---|---|
| failed | ジョブが `done` で、終了コードが 1 | 実行のマニフェストにある最初の失敗の行 |
| error | ジョブが `done` で、終了コードが 1 以外の非 0 | 終了コードとその意味。たとえば、使い方の誤りを示す 2 や、シグナルによる終了です |
| worker lost | ジョブが `failed` で、結果にリース期限切れの理由がある | 試行回数 |
| worker error | ワーカーが報告したエラーで、ジョブが `failed` | 報告されたエラーを1行に切り詰めたもの |
| cancelled | ジョブが取り消しで終わり、`cancelReason` が `failFast` でない | なし |
| skipped | ジョブが取り消しで終わり、`cancelReason` が `failFast` | なし（失敗の行が原因を示します） |

理由はすべて信頼できないテキストです。ワーカーは自分のエラーを報告し、マニフェストはシナリオ自身のメッセージを持つからです。アダプターは制御文字を取り除き、`|` をエスケープし、理由をコードスパンで囲みます。コードスパンの区切りは、中にあるバッククォートの連なりより長くします。そうすれば、報告されたどのエラーも、表の行を分割したり、check の中で Markdown として描画されたりしません。

取り消しで終わったジョブは、終了コードにかかわらず `cancelled` か `skipped` の行になります。

`skipped` の行は、`run-set` に `failFast` と `cancelReason` を追加する項目（スラッグは `run-set-fail-fast`）に依存します。その項目が入るまで、アダプターは `skipped` の行を書かず、取り消しで終わったジョブはすべて `cancelled` として示します。

GitHub は `summary` を 65,535 文字に制限しています。アダプターは上限に達する前に行の追加をやめ、省いた行の数で表を締めくくります。シナリオはプルリクエストの差分の中にファイルと行を持たないので、アダプターは annotation を書きません。

### 基本原則

- **判定にモデルを使いません。** グループの `conclusion` も出力の各行も、ジョブの状態、終了コード、マニフェストから作ります。check のどの部分も、言語モデルが書いたり読んだりすることはありません。
- **決定性を保ちます。** 配信は判定の後に行い、判定に影響を返しません。配信が一度も成功しなくても、グループ、そのジョブ、その実行は元のままです。
- **アプリに依存しません。** sink はオペレーターの設定です。ランナー、ドライバー、ワーカーは変わりません。

### ドキュメント

`docs/self-hosting.md` に、sink のファイル、App の権限、`headSha` を渡すワークフローの例を説明する節を加えます。`docs/devicefarm.md` に、`run-set` の `notify` フィールドを加えます。`docs/architecture.md` に、グループイベントと配信のループを加えます。どのページも両言語で更新します。

## 検討した代替案

- **Actions のジョブがポーリングして自分の状態を決める**：サーバーの変更は不要で、ジョブ自身の結果がすでに check として表示されます。しかし runner がグループ全体を待つことになり、それこそがこの項目で取り除くコストです。
- **リクエストで Webhook の URL を受け取る**：リクエストの URL を使えば、抽象化は単純な POST になります。しかし GitHub の check run はその形に収まりません。App のトークンが必要で、各更新は作成時に返された id を宛先にします。また、リクエストで渡される URL は、`serve` が動くネットワークへの SSRF の経路になります。
- **BE-0099 の `notify:` のリストを拡張する**：このリストはターゲットの config に属し、1つの `bajutsu run` の中で発火します。グループのライフサイクルは `serve` の上にしかないので、契機を実行のプロセスに置けません。新しい sink は、宛先ごとのアダプターという同じ考え方に `format` という語を再利用しています。
- **イベントを `hooks` と呼ぶ**：シナリオとターゲットの語彙では、`hooks` はすでにシナリオの前後に実行するステップを指しています。sink に配信するイベントはステップではなく通知なので、この項目は通知の語彙を使います。
- **シナリオごとに check run を公開する**：40シナリオなら、プルリクエストの checks の一覧に40件が加わります。表を持つ1つの check なら一覧が読みやすいままで、その結論を1つの必須チェックとして使えます。
- **GitHub の再実行ボタンに応える**：再実行の要求は、`rerequested` アクションの `check_run` Webhook として App に届きます。対応するには、`serve` に受信用の Webhook のエンドポイントと、グループを登録し直す仕組みが必要になります。ワークフローを再実行すれば、グループキーが `GITHUB_RUN_ATTEMPT` を含むので、新しいグループがすでに登録されます。この項目はボタンを範囲外にします。
- **キューのジョブに期限を設け、「ワーカーがいない」を結果にする**：`POST /api/run-set` は、稼働中のどのワーカーも扱えないシナリオをすでにステータス 400 で拒否します。その場合は投入した Actions のジョブが失敗し、そのログに足りないトークンが出ます。それでも、登録の後にワーカーがいなくなると、ジョブは結果に達しないまま待ち続けます。期限を設ければこの隙間は閉じます。しかし期限は、グループのジョブだけでなくすべてのジョブについてキューの意味を変えます。この項目は、期限を別の項目に委ねます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] sink のファイル、起動時の検証、App の確認
- [ ] リクエストのフィールド `notify`、その検証、その列、リクエストダイジェスト
- [ ] テーブル `group_events` と、登録、リース、取り消しを含む終端の経路が書き込む行
- [ ] 回収の結果に加える、構造化されたリース期限切れの理由
- [ ] `completed` のスナップショット（シナリオの名前と失敗の行を一度だけ作り、行に保存する）
- [ ] 配信のループ（確保、順序、再試行、放棄、メトリクス）
- [ ] `github-check` のアダプター（リポジトリによるインストールの解決、回復のための一覧と3つの呼び出し、出力）
- [ ] 両言語のドキュメント

## 参考

- [BE-0484 — job をグループとして登録し、worker がグループ単位でまとめて lease する](../BE-0484-job-group-lease/BE-0484-job-group-lease-ja.md)：グループ、その `conclusion`、グループキーを定めています。
- [BE-0099 — 実行結果の Webhook 通知](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications-ja.md)：実行ごとの `notify:` のリストと、その `format` フィールドを定めています。
- [BE-0414 — GitHub Actions の OIDC トークンで CI のジョブを serve に認証させる](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity-ja.md)：マシンセッションと、その `repository` クレームを定めています。
- [BE-0224 — GitHub を取得元とする config での private リポジトリへのアクセス権限の付与](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth-ja.md)：この項目が再利用する GitHub App の認証情報を定めています。
- [GitHub REST API: check runs](https://docs.github.com/en/rest/checks/runs)：作成、更新、一覧の呼び出し、状態と conclusion の値、`filter` の既定値、出力の上限を説明しています。
- [GitHub Actions: events that trigger workflows, `pull_request`](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request)：プルリクエストでの `GITHUB_SHA` がマージコミットを指すことを説明しています。

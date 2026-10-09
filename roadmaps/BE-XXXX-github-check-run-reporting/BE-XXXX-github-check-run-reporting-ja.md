[English](BE-XXXX-github-check-run-reporting.md) · **日本語**

# BE-XXXX — CI から登録した run の結果を1つの GitHub check として pull request に報告する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-github-check-run-reporting-ja.md) |
| 提案者 | [@paihu](https://github.com/paihu) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | 外部サービスとの連携 |
| 関連 | [BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity-ja.md), [BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications-ja.md), [BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth-ja.md), [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out-ja.md), [BE-0370](../BE-0370-graceful-run-cancel/BE-0370-graceful-run-cancel-ja.md) |
<!-- /BE-METADATA -->

## はじめに

GitHub Actions のワークフローは、シークレットを保存しなくても、ホストされた `bajutsu serve` へ run を
登録できます。[BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity-ja.md) の
OpenID Connect (OIDC) による machine セッションを使う経路です。ただし、ワークフローの job は serve が run を
受け付けた時点で終わるので、pull request は run の結果を知る手段を持ちません。

この項目では、そうして登録された run の結果を、serve が **1つの GitHub check run** として pull request に
報告します。ワークフローは複数の scenario を1回のリクエストで登録します。serve は pull request の head
コミットに check run を作り、run の実行中は `in_progress` に保ちます。最後の run の判定が確定した時点で、
check run を完了させます。check のページには全 scenario の判定が並び、失敗した scenario からは hosted
report へリンクします。check の名前は固定なので、リポジトリはこの check をマージに必須のステータス
チェックに指定できます。check の結論は決定論的な判定をそのまま写したもので、独自の判断は加えません。

## 動機

BE-0414 の登録経路は、意図的に投げっぱなしの設計になっています。パイプラインは OIDC トークンを交換し、
ビルドをアップロードしてから `POST /api/run` を呼びます。serve は job id を返し、その job を後で
worker 上で実行します。このとき、ワークフローの job が取れる道は2つあり、どちらも pull request には
向きません。

| ワークフローの job の動き | pull request に見えるもの | 代償 |
|---|---|---|
| 登録後すぐに終わる | run の結果と無関係に成功した job | 結果が pull request に届きません |
| すべての run が終わるまで serve をポーリングする | job 自身のステータス | run の間ずっと runner を占有し、失敗した scenario は job のログでしかわかりません |

1つ目の道では、マージを止める手段がそもそもありません。2つ目の道ならマージを止められますが、待つだけの
runner に費用を払ううえ、どの scenario が失敗したかはレビュアーがログを掘って探すことになります。
足りない材料は、どちらも serve がすでに持っています。serve は各 job の終了時点と判定を知っています。
また serve は、非公開の設定ソースのために GitHub App の installation token をすでに発行しています
([BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth-ja.md))。
GitHub の Checks API が受け付けるのは、まさにこの種類のトークンです。

結果の受け皿には、コミットステータスではなく check run を選びます。コミットステータスが持てるのは
状態とリンク1つだけです。check run はそれに加えて Markdown の要約を持てるので、scenario ごとの表と
レポートへのリンクを載せられます。

**検証できる成果。** ワークフローが複数の scenario を1回のリクエストで登録し、数秒で終わります。
その間、pull request には `bajutsu` という名前の check が進行中として表示されます。すべての scenario が
成功すると、check は緑になります。scenario が失敗すると check は赤になり、check のページに失敗した
scenario の名前と、その scenario のレポートを開くリンクが並びます。`bajutsu` を必須とする branch
protection のルールは、check が成功で完了するまでマージを止めます。

## 詳細設計

設計は4つの単位に分かれます。グループを登録するリクエスト、グループを追跡する記録、check run を書く
reporter、そしてテストとドキュメントです。

### 単位1 — 複数の scenario を1つのグループとして登録する

`POST /api/run-set` は、`target` と `scenarios` のリストを受け取り、scenario ごとに1つの job へ展開します
([BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out-ja.md))。
現在この展開はクラウドバッチのプロバイダ経由でしか動かず、成果物の上書き指定
(`binaryArtifact`、`scenariosArtifact`)も拒否します。この単位では、`cloudBatch` を宣言しない target に
ついて、2つの制限を外します。

- 各 scenario は、worker キューに載る通常の job になります。job は `POST /api/run` と同じコード経路で
  組み立てます。
- 上書き指定はセット内のすべての job に適用されます。パイプラインはビルドを1回アップロードするだけで、
  すべての scenario をそのビルドに対して実行できます。
- machine principal からも呼べるようにします。現在の `bajutsu/serve/gate.py` の machine 用許可リストは
  `POST /api/run` を許可していますが、セットは許可していません。そこへ `POST /api/run-set` を加えます。
  `start_run_set` は `start_run` と同じく machine の org を受け取り、セットはパイプライン自身の org で動きます。

リクエストには、省略可能なオブジェクト `githubCheck` を1つ加えます。

```json
{
  "target": "app",
  "scenarios": ["scenarios/login.yaml", "scenarios/checkout.yaml"],
  "binaryArtifact": "sha256:…",
  "githubCheck": { "headSha": "<pull request の head コミット>", "name": "bajutsu" }
}
```

- `githubCheck` の中で `headSha` は必須です。ワークフローは `github.event.pull_request.head.sha` を
  渡します。ワークフロー自身の `GITHUB_SHA` は使えません。`pull_request` イベントではテスト用の
  マージコミットを指し、そこに付けた check は pull request に表示されないからです。
- `name` のデフォルト値は `bajutsu` です。iOS 用と Android 用など、複数のワークフローから登録する
  リポジトリでは、ワークフローごとに別の名前を付けます。そうすれば、各ワークフローの check を個別に必須チェックへ指定できます。

serve は `githubCheck` を machine principal からのリクエストに限って受け付けます。check の書き込み先は、
その principal の identity (`repo:<owner>/<repo>`)が示すリポジトリです。この identity は、serve が
OIDC トークンの claim から検証したものです。リクエスト自体はリポジトリを指定しないので、ワークフローが
check を書けるのは自分のリポジトリだけです。人間のセッションから `githubCheck` 付きのリクエストが
来た場合は `400` を返します。

`githubCheck` 付きのリクエストは、全件を登録するか、1件も登録しないかのどちらかにします。
現在の `POST /api/run-set` は、同時実行数の上限(全体、ユーザーごと、org ごと)に達すると展開を打ち切ります。
そのうえで、短くなった job id のリストを `200` で返します。
この短いリストに対する check は、残りの scenario が一度も実行されないまま成功しかねません。
`githubCheck` があるときは、serve は job レジストリの lock の下でセット全体を一度に確保します。
上限の確認から最後の登録までの間に、並行する別の登録が枠を奪うことはありません。
上限がセット全体を受け入れられなければ、serve は何も登録せず `429` を返すので、ワークフローは目に見える形で失敗します。

### 単位2 — 最後の job が終わるまでグループを追跡する

グループとは、1回のリクエストが作った job id の集合です。
serve はグループを SQL の job ストアに、job と同じデータベースへ記録します。記録には次の内容を持たせます。

- job id の集合(リクエストを受け付けた時点で確定します)
- リポジトリ、head コミット、check の名前
- check run の作成の状態(作成待ち、GitHub が返した id を伴う作成済み、再試行を使い切った断念のいずれか)

インメモリのストアにはグループの記録を持たせません。
machine principal はデータベースを持つデプロイにしか存在しないので、
`githubCheck` 付きのリクエストがデータベースのないデプロイに届くことはないからです。

job id がリクエストの時点で確定するので、グループは最初から自分の大きさを知っています。
後から登録が加わることはないため、まだ登録待ちの scenario を残したまま check が完了することはありません。

worker はデータベースに直接アクセスしません。
worker は結果を serve へ送り、job の終了行を書くのは serve の結果受付ハンドラです。
結果が届かないまま終わる job もあります。
worker が落ちると、リースは期限切れになります。試行回数を使い切った job は、リースの回収処理が失敗にします。
終了行を書く replica がグループを登録した replica と同じとは限らないので、グループの完了は2段階で検知します。

1. 終了行を書くたびに、同じトランザクションの中で、グループ内のその job の枠を埋めます。
   書き手は2つあり、どちらも枠を埋めます。
   結果受付ハンドラ(`complete_job` と `fail_job`)と、試行回数を使い切った job を失敗にするリースの回収処理です。
2. serve は、すべての枠が埋まったグループを定期的に走査して拾います。
   報告の前にグループの行を claim するので、複数の serve の replica が同時に走査しても、check を完了させるのは1つだけです。

### 単位3 — serve から check run を書く

reporter は run のプロセスではなく serve の中で動かします。worker は長期の認証情報を持ちません
([BE-0160](../BE-0160-worker-credential-free-uploads/BE-0160-worker-credential-free-uploads-ja.md))。
しかもグループは複数の worker にまたがるので、グループ全体を見渡せる run のプロセスはありません。

reporter は Checks API を2回呼びます。どちらの呼び出しにも、`bajutsu/common/github/app.py` で発行した、
グループのリポジトリ向けの installation token を使います。

1. **登録時**に、`headSha` 上に `status: in_progress` の check run を作ります。`details_url` は serve の
   Web UI を指します。
2. **定期的な走査でグループの完了を見つけたとき**に、check run を `status: completed` に更新し、結論と `output.summary` を
   書きます。

結論は判定から機械的に決めます。

| グループの結果 | `conclusion` |
|---|---|
| すべての job が成功した | `success` |
| いずれかの job が失敗した、またはエラーで終わった | `failure` |

GitHub は `failure` を必須チェックの失敗として扱うので、マージは止まります。
現在、キャンセルはリモートの worker に届きません。
キャンセルは serve の中で job に印を付けるだけで、その job を実行している worker には何も送りません。
そのためグループの job は、必ず自身の判定か、リースの使い切りで終わります。
表に `cancelled` の行が要らないのはこのためです。
要約は scenario ごとに1行の Markdown の表で、各行に判定、所要時間、レポートへのリンクを
載せます。失敗した行を先に並べます。GitHub は `output.summary` を65,535文字までに制限しています。
上限を超えるときは、失敗した行をすべて残して成功した行を省き、省いた行数を書き添えます。

リンクを作るには、serve の公開オリジンが必要です。
現在の serve には、そのオリジンだけを指す設定がありません。
OAuth の redirect URI (`BAJUTSU_OAUTH_GITHUB_REDIRECT_URI`)がオリジンを含んでいるだけです。
[BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications-ja.md) の run 通知もレポートの URL を引数に取れますが、実際には渡されていません。
そこで新しい設定 `BAJUTSU_PUBLIC_URL` でオリジンを与えます。
この設定のデフォルト値は OAuth の redirect URI のオリジンです。
OAuth を設定済みのデプロイは、2つの設定を揃えて保つ必要がありません。
リンクを開けるのは、job の org にサインインできる閲覧者だけです。
この check はレポートへの導線を作るもので、レポートを公開するものではありません。

GitHub App には、報告先のリポジトリに対する `checks: write` 権限が必要です。App を設定していない
デプロイや、App にこの権限がないデプロイでは、`githubCheck` 付きのリクエストを登録時点で `400` として
拒否します。後になって失敗すると、check が黙って現れないままになるからです。

GitHub への呼び出しが失敗したときは、上限付きのバックオフで再試行し、それでも失敗すればログに残します。
登録時の作成の呼び出しが再試行を使い切って断念された場合は、完了時の呼び出しで check run を `completed` として
直接作ります。作成が失われても、結論は必ず書かれます。作成待ちのグループは走査の対象から外すので、
リトライ中の作成と完了時の作成が競合して、同じ名前の check run が2つできることはありません。
この失敗が判定を変えることはありません。serve が完了させられなかった check は `in_progress` のまま残り、必須チェックとしては未通過の扱いに
なります。つまり報告が失われると、マージは素通りせず止まります。

### 単位4 — テストとドキュメント

- テストは Checks API を HTTP の境界で偽装し、次の点を検証します。
  - 結論の表
  - head コミットとリポジトリの結び付け
  - 人間の principal の拒否
  - 要約の上限
  - ログで終わる再試行
  - セット全体を受け入れられないときの `429`
  - 2つの replica が同じ check を完了させないための claim
  - リースの使い切りで失敗した job により、グループが `failure` で完了すること
  - 作成待ちのグループを走査が飛ばすこと
  - 作成に失敗して完了時に回復する経路
- `docs/self-hosting.md` とその `docs/ja/` のミラーに、ワークフローの例を加えます。例は OIDC の交換、
  アップロード、`githubCheck` 付きの `POST /api/run-set` 1回で構成します。最後に、check を必須にする
  branch protection の設定を示します。
- `docs/architecture.md` に、グループの記録と reporter を記載します。

### 基本原則との関係

- **AI は判定しません。** 結論は決定論的な判定から計算します。この経路に大規模言語モデルは関与しません。
- **決定論を優先します。** 報告は判定が確定した後に行い、判定を変えることはありません。配信に失敗
  しても check は未完了のまま残り、緑にはなりません。
- **アプリに依存しません。** check の名前と head コミットはリクエストから受け取ります。特定のアプリに
  関する知識が、ツール、ドライバ、runner に入り込むことはありません。

## 検討した代替案

- **ワークフローの job が serve をポーリングし、job 自身を必須チェックにする。** GitHub App の権限は
  要りません。ただし、run の間ずっと runner を占有し、失敗した scenario はログでしか示せません。ポーリングは
  引き続き可能で、この項目はその必要をなくすだけです。
- **scenario ごとに check run を作る。** 失敗した scenario が Checks タブで目立ちます。しかし必須
  チェックは名前で指定するので、scenario を追加するたびに branch protection の設定を足す必要が
  あります。ある scenario が run から外れると、その必須チェックは永遠に保留のままです。名前を1つに
  固定し、scenario を要約に並べる形なら、scenario の増減に左右されません。
- **ワークフローの run id でまとめる、または open、追加、seal の手順でまとめる。** run id でまとめると、
  最後の run がいつ登録されたかを判断できません。そのため、check が早すぎる時点で完了しかねません。明示的な seal の
  呼び出しを設ければこの問題は解けますが、seal を忘れたワークフローでは check が保留のまま残ります。
  すべての scenario を1回のリクエストで指定すれば、グループの大きさが最初に確定し、呼び忘れる余分な
  呼び出しもありません。この性質は、単位1で述べたとおり、リクエストを部分的には登録しないことで成り立ちます。
- **check run の代わりにコミットステータスを使う。** コミットステータスは通常のトークンで扱えますが、
  状態とリンク1つしか持てません。失敗を探すレビュアーに必要な scenario ごとの要約を載せられません。
- **BE-0099 の通知と同じく run のプロセスから報告する。** run のプロセスが見るのは1つの job だけで、
  グループ全体は見えません。また worker は GitHub の認証情報を持ちません。

## 進捗

> 作業の進行に合わせて更新します。チェックリストは *詳細設計* の MECE な作業分解
> (作業単位ごとに1つのチェックボックス)に対応し、ログには変更内容と時期を
> (古い順に)PR へのリンク付きで記録します。

- [ ] 単位1 — `POST /api/run-set` を worker キューへ流し、machine の許可リストに加え、上書き指定と `githubCheck` を全件かゼロかで受け付ける
- [ ] 単位2 — SQL の job ストアにグループの記録を持たせ、終了行を書くたびに枠を埋め、serve が定期的に走査し、claim してから報告する
- [ ] 単位3 — Checks API の reporter、結論の対応付け、要約、`BAJUTSU_PUBLIC_URL`
- [ ] 単位4 — テストとドキュメント

## 参考

- GitHub REST API の check run:<https://docs.github.com/en/rest/checks/runs>
- GitHub の保護ブランチと必須ステータスチェック:
  <https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches>
- GitHub Actions のワークフローを起動するイベント(`pull_request` と `GITHUB_SHA`):
  <https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows#pull_request>
- [BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity-ja.md):この登録経路が乗る OIDC の machine セッションです。
- [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out-ja.md):この項目が一般化する、scenario セットの展開です。

[English](BE-XXXX-ci-job-audit-provenance.md) · **日本語**

# BE-XXXX — マシンプリンシパルの監査エントリに、操作した CI ジョブを記録する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-ci-job-audit-provenance-ja.md) |
| 提案者 | [@paihu](https://github.com/paihu) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | [#2135](https://github.com/bajutsu-e2e/bajutsu/pull/2135) |
| トピック | Web UI のホスティング |
| 関連 | [BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity-ja.md)、[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) |
<!-- /BE-METADATA -->

## はじめに

継続的インテグレーション（CI）のジョブは、ホストされた `bajutsu serve` への認証にマシンセッションを使います。
GitHub Actions が発行する OpenID Connect（OIDC）トークンを、短命のマシンセッションに交換する仕組みです
（[BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity-ja.md)）。
このセッションが書く監査エントリは、リポジトリまでしか名指ししません。本項目では、GitHub Actions の
ジョブも記録します。記録するのは、ワークフローの実行、その試行回数、その中のジョブです。
監査ログは現状、「このリポジトリのどれかのジョブ」までしか答えられません。本項目の後は、「どのジョブが
操作したか」まで答えられます。

## 動機

監査エントリの役割は、操作をその実行者までたどれるようにすることです。人間の呼び出し元なら、エントリの
`actor_id` がユーザーを名指しします。パイプラインにはユーザーの行がないので、BE-0414 は `actor_id` を
null のままにし、代わりにリポジトリをエントリの detail に書きます。しかし、1つのリポジトリが複数の
ワークフローを動かすようになると、リポジトリ単位では粗すぎて何もたどれません。並行するジョブ、失敗した
ジョブの再実行、main ブランチのビルドと並ぶプルリクエストのビルドは、どれも同じリポジトリを共有します。
これらのジョブが dispatch した run やアップロードした成果物の監査エントリを、運用者は区別できません。

ジョブが提示するトークンは、自分がどのジョブかをすでに示しています。GitHub Actions は、すべての OIDC トークンに
`run_id`、`run_attempt`、`check_run_id` を署名して含めます。BE-0414 が読む `repository` と同じ扱いです（[GitHub の claim リファレンス](https://docs.github.com/en/actions/reference/security/oidc)）。
`check_run_id` はジョブ自身の識別子で、ジョブのページの URL に現れる値です。`serve` は交換の時点でトークンの
署名を検証するので、これらの claim は `repository` と同じだけ信頼できます。それでも `serve` は、現状
これらの claim を捨てています。

後から確かめられる成果は、監査エントリそのものです。マシンセッションが `POST /api/run` で書くエントリは、
run を dispatch したジョブの実行 id と check run の id を持ちます。github.com では、この2つで
`https://github.com/<owner>/<repo>/actions/runs/<run_id>/job/<check_run_id>` のジョブのページを開けます。
同じリポジトリの2つのジョブは、区別できる2つのエントリを書きます。

## 詳細設計

変更は、ジョブの識別情報がすでにたどる経路に沿います。トークンの claim、マシンセッション、監査エントリの
順です。以下の単位もこの順に並べます。

### 単位 1 — 交換の時点でジョブの claim を読む

プロバイダに依存しない `WorkloadClaims`（`bajutsu/serve/oidc.py`）に、5つのフィールドを足します。
既存の任意フィールド（`environment`、`ref`、`workflow_ref`）の後に並べます。

| フィールド | GitHub Actions の claim | 意味 |
|---|---|---|
| `run_id` | `run_id` | ワークフローの実行 |
| `run_attempt` | `run_attempt` | その実行の何回目の試行か。再実行は同じ `run_id` を使います |
| `check_run_id` | `check_run_id` | 実行の中のジョブ |
| `sha` | `sha` | 実行がビルドしたコミット |
| `triggered_by` | `actor` | 実行を開始した GitHub アカウント |

`check_run_id` は、中立な `job_id` ではなく GitHub の名前のままにします。`POST /api/run` は
serve 自身の `jobId` を返すので、監査に同じ名前のキーがあると両者を結合したくなるからです。

どのフィールドも任意のままです。claim を出さないプロバイダや、claim が加わる前に発行された GitHub の
トークンでは、そのフィールドを `None` に写します。claim がないことを理由に、交換がトークンを拒むことは
ありません。これらのフィールドはジョブを記述するもので、認可には使わないからです。同じ理由で、3つの id は文字列に
加えて数値も受け付けます。GitHub はこれらの型を文書化しておらず、数値を捨てるとジョブが記録から黙って
欠けるからです。

`OidcProvider` は、`repository_claim` と同じように各 claim の名前を持ちます。

### 単位 2 — ジョブをマシンセッションに持たせる

交換は、claim を1つのジョブ記録にまとめます。ジョブ記録は camelCase のキーから文字列への写像で、値の
ないフィールドは省きます。キーは `runId`、`runAttempt`、`checkRunId`、`workflowRef`、`ref`、`sha`、
`environment`、`triggeredBy` です。リポジトリは監査エントリ自身が名指しするので、記録には入れません。

ジョブ記録は、id をジョブのページへのリンクにまとめず、個別の値のまま持ちます。リンクのホストはトークンに
含まれないからです。GitHub Enterprise Server は同じパスを自身のホストで提供するので、github.com 向けに
組み立てたリンクは別のリポジトリのページを開くか、何も開きません。片方の id だけで組み立てたリンクも、
誤ったページを開きます。個別の id はどちらの場合も正しいままで、リンクが必要な読み手はそこから組み立てられます。

セッションは、すでに持つ `org` と `kind` の隣にジョブ記録を保存します。

- **SQL ストア。** `sessions` に、null を許す JSON 列 `ci_job` を新しい migration で足します。
  migration 以前に書かれた行は、ジョブを持たないものとして読みます。
- **Redis ストア。** セッションの JSON 値に `ciJob` キーを足します。
- **インメモリストア。** `Principal` のフィールドとして持ちます。

`Principal` には `ci_job` フィールドが加わり、`Principal.from_stored` が読み出し時に型を絞ります。
文字列から文字列への写像でない値は、ないものとして読みます。`identity` と `org` も、すでに同じ規則で
絞っています。

ジョブ記録の運び手としてセッションが適切なのは、claim が交換の時点にしか存在しないからです。以降の
リクエストが提示するのはセッションの cookie で、トークンではありません。人間のセッションがジョブを
持つことはありません。

### 単位 3 — ジョブを監査エントリに書く

2つのリクエストバックエンドは、マシンプリンシパルの org をゲートで一度だけ解決し、マシンの許可リストの
背後にある操作へ `machine_org` として渡しています。ジョブ記録も同じ経路で運び、両バックエンドはゲートがすでに読んだプリンシパルから答えます。監査エントリを
書くマシンの呼び出しは、すべてジョブを運びます。

- **`POST /api/run` と成果物の存在確認。** どちらも `RequestCtx` を通るので、`RequestCtx` に `ci_job()` を足します。
- **3種類の成果物のアップロード。** 各バックエンドは、`RequestCtx` の外にある raw-body のハンドラで
  受け取ります。このハンドラが、`machine_org` と並べてジョブを `bind_artifact` に渡します。

`GET /api/runs` とジョブのポーリングも `machine_org` を受け取りますが、監査エントリを書かないので、
ジョブは要りません。

`_record_audit`（`bajutsu/serve/authz.py`）は、ジョブ記録を任意の引数 `ci_job` として受け取ります。
マシンプリンシパルについては、BE-0414 が文書化した `repository` を detail に書き続けたうえで、
ジョブ記録を `actor` キーの下に足します。`actor` というキー名は、この記録が何の代わりかを示します。
この記録は、人間について `actor_id` が担う役割を、パイプラインが持ち込める唯一の場所で担います。

```json
{
  "repository": "acme/app",
  "actor": {
    "runId": "123",
    "runAttempt": "1",
    "checkRunId": "456",
    "ref": "refs/heads/main",
    "sha": "...",
    "triggeredBy": "octocat"
  }
}
```

交換そのものも、action を `oidc.exchange`、target をリポジトリとし、同じ detail を持つ監査エントリを
書きます。現状の `serve` は、交換を運用ログにしか残しません。監査ログにエントリがあれば、後続の各エントリの
ジョブを、そのセッションが始まった時点へ結び付けられます。

### 単位 4 — テストとドキュメント

テストは各単位を確かめます。1つ目は、claim の写像を、claim がない場合も含めて確かめます。2つ目は、
各ストアでのセッションの往復を、migration 以前の行も含めて確かめます。3つ目は、マシンが dispatch した
run の監査 detail を確かめます。4つ目は `oidc.exchange` のエントリを確かめます。

`docs/self-hosting.md` の「An audit entry names the repository」の段落に、ジョブ記録の説明を加えます。
`docs/ja/self-hosting.md` にも同じ変更を反映します。

### 範囲外

- **run への列の追加。** `POST /api/run` の監査エントリは、すでに run を target として名指ししています。
  運用者はそのエントリを引けばジョブにたどり着けるので、run の列は同じ事実の重複になります。
- **Web UI での表示。** 現在、監査ログを読むページはありません。監査ログの閲覧画面は、それ自体が独立した
  機能になります。

## 検討した代替案

| 代替案 | 採らない理由 |
|---|---|
| ジョブの URL を `actor_id` に書く | `actor_id` は `users.id` への外部キーです。ジョブの URL を入れた INSERT は失敗します。ジョブごとに合成のユーザー行を作ると、`/api/orgs` が開示する名簿にパイプラインが混ざります。BE-0414 がこの列を null にした理由もこれです。 |
| マシンの actor 専用の監査列を設ける | 現在、監査ログを読み出すコードはないので、その列で絞り込む必要もまだありません。detail はすでにリポジトリを持っており、列を足すと1つの actor が2か所に分かれます。 |
| ジョブをセッションの identity に埋め込む（`repo:acme/app#123`） | 失効はセッションを identity で照合します。ジョブごとの identity にすると、「このリポジトリのセッションを失効する」操作が何にも一致しなくなります。 |
| 引数で渡さず、リクエスト単位のコンテキスト変数からジョブを読む | `machine_org` が `RequestCtx` を通して明示的に渡している依存を、コンテキスト変数は隠してしまいます。両バックエンドがリクエストごとにリセットする必要も生じます。 |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 単位 1 — 交換の時点でジョブの claim を読む
- [x] 単位 2 — ジョブをマシンセッションに持たせる
- [x] 単位 3 — ジョブを監査エントリに書く
- [x] 単位 4 — テストとドキュメント

## 参考

- [BE-0414 — GitHub Actions の OIDC トークンで CI のジョブを serve に認証させる](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity-ja.md)
- [GitHub Docs — OpenID Connect reference](https://docs.github.com/en/actions/reference/security/oidc) (the claim table, `check_run_id` included)
- [GitHub Docs — OpenID Connect](https://docs.github.com/en/actions/concepts/security/openid-connect) (an example token payload, `sha` included)
- `bajutsu/serve/oidc.py`、`bajutsu/serve/operations/oidc.py`、`bajutsu/serve/authz.py`

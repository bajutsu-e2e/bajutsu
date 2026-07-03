[English](BE-0056-web-ui-aws-sso-login.md) · **日本語**

# BE-0056 — Web UI からの AWS SSO サインイン（Bedrock 認証情報の取得）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0056](BE-0056-web-ui-aws-sso-login-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装中** |
| 実装 PR | [#166](https://github.com/bajutsu-e2e/bajutsu/pull/166) |
| トピック | AI プロバイダ設定 |
<!-- /BE-METADATA -->

## はじめに

Web UI（`bajutsu serve`）から **AWS SSO（IAM Identity Center）のサインイン**を開始し、Bedrock
プロバイダが必要とする AWS 認証情報を取得できるようにします。今は `serve` を起動する前に、シェルで
`aws sso login` を実行しておく必要があります。
[BE-0053](../../implemented/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider-ja.md) は Bedrock
を選択可能なプロバイダにし、認証は標準の AWS 認証情報チェーンに委ねました。認証情報の *取得* は環境側に
委ねたままです。Web UI の Settings パネルで選べるのはプロバイダ・モデル ID・リージョンだけで、認証情報
そのものは `serve` を起動したプロセスの環境であらかじめ解決できている必要があります。本項目はその隙間を
埋めます。Settings パネルからのサインインで SSO のデバイス認可フローを実行し、検証 URL とコードをブラウザに
表示し、承認が済めば、起動される `record` ／ `crawl` ジョブをその SSO セッションに向けます。

範囲は厳密に Tier-1 側に留まります。決定的な `run` ／ CI ゲートはモデルを呼ばず、影響を受けません
（[DESIGN §2 / §3.1](../../../DESIGN.md)）。ここで LLM 呼び出しがゲートに入ることはありません。

## 動機

[BE-0053](../../implemented/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider-ja.md) の動機は、
AWS に標準化したチームが、発行・ローテーションする `ANTHROPIC_API_KEY` ではなく「すでに運用している
IAM ロール／SSO」で認証することにありました。プロバイダの継ぎ目は CLI ではこれを実現しますが、多くの
ユーザーの入口であり、リモートのセルフホスト `serve`
（[BE-0016](../../in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) Tier A）では唯一の入口でも
ある Web UI からは、SSO が完全に蚊帳の外です。具体的な摩擦が 3 つあります。

- **シェルでの前提作業が UI から見えない。** ユーザーは `make serve` の *前* に `aws sso login` を実行し
  `AWS_PROFILE` を設定しておく、と知っていなければならず、その後は失敗が静かに起きるのを待つことになり
  ます。Bedrock 経路が未認証であることも、`record` ジョブが急に失敗する理由も、Web UI は何も伝えません。
- **SSO セッションは短命（数時間）。** トークンが切れると Bedrock ジョブは失敗し始めますが、UI には何の
  合図もなく、シェルに戻らなければ再認証もできません。最初のサインインだけでなく、「セッション状態の表示と
  再認証」を一級の操作として用意することが、本当の使い勝手の改善です。
- **リモートの `serve` では `aws sso login` がそもそも使えない。** このコマンドは **serve ホスト側** で
  ブラウザを開きます。ホストがユーザーの手元ではない（Tailscale 越しの Mac mini など、
  [BE-0016](../../in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)）と、ブラウザが開く場所が違い
  ます。SSO の **デバイス認可フロー**がこれを解きます。`verificationUriComplete` とユーザーコードを Web UI
  に表示し、ユーザーは *自分の* ブラウザで承認します。

BE-0053 が行った訂正は、設計を左右するので繰り返します。「SSO」は「認証が不要」を意味しません。Anthropic
のキーを **AWS 認証情報** に置き換えるだけです。本項目はその認証情報を UI から *取得する* のを助けるもので
あり、自前の認証情報を保管したり仲介したりはしません。

## 詳細設計

提案レベルの粒度です。既存の 3 つの継ぎ目の上に乗ります。プロバイダのクライアントファクトリ
（`bajutsu/anthropic_client.py`）、`os.environ` にしか書かない serve の設定ハンドラ
（`bajutsu/serve/operations.py` の `set_provider` ／ `set_api_key`）、そしてその環境を継承するジョブ起動
（`bajutsu/serve/jobs.py` の `_spawn_env`）です。

### スコープ（v1）

- **既存の SSO プロファイルを前提とする。** オペレーターは `aws configure sso` を一度実行済みで、
  `~/.aws/config` に `sso_session` ／ `sso_account_id` ／ `sso_role_name` を持つプロファイルがある状態です。
  Web UI はプロファイルを名前で選び、サインインを開始し、`AWS_PROFILE` を設定します。SSO 設定（start URL・
  アカウント・ロール）を UI から入力する案は将来の拡張とし、v1 には含めません。
- **ローカルとリモートの両 `serve`。** 検証 URL とコードをブラウザに表示するので、Tailscale 越しのホスト
  （[BE-0016](../../in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) Tier A）でもローカルと同様に
  動きます。
- **単一オペレーター。** serve プロセスは、到達できる全員に対して 1 つの SSO セッションを保持します。共有の
  マルチテナントサーバでのユーザー別 ID は明確に **範囲外** で、
  [BE-0015](../../in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) ／
  [BE-0016](../../in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)（OAuth/IdP、組織ごとに
  スコープした秘密情報）の領分です。

### 認証情報がジョブに届く仕組み（既存の継ぎ目を使う）

`record` ／ `crawl` ジョブは、serve プロセスの `os.environ` を **コピーして継承する子プロセス** として
起動されます（`bajutsu/serve/jobs.py` の `_spawn_env`）。したがって serve プロセスが `AWS_PROFILE`（および
BE-0053 のとおり Bedrock 推論リージョン用の `AWS_REGION`）を設定すれば、各ジョブの `AnthropicBedrock()` は
そのプロファイルと標準のトークンキャッシュ（`~/.aws/sso/cache`）から、botocore の SSO プロバイダ経由で認証
情報を解決します。

ここから使い勝手の肝となる性質が出ます。**再認証に `serve` の再起動は要りません。** 各ジョブは認証情報
チェーンを毎回解決し直す新しいプロセスなので、SSO トークンキャッシュが更新されれば、次のジョブがそれを拾い
ます。静的な一時キーをユーザーに貼らせるのではなく `AWS_PROFILE` を軸にするのは、まさにこのためです（静的
キーは起動時に固定され失効します）。`set_provider` ／ `set_api_key` と同じく、serve は `AWS_PROFILE` ／
`AWS_REGION` を `os.environ` に書くだけで、ディスクには書きません。SSO トークンは AWS 標準のキャッシュに
置かれ、Bajutsu の外で管理されます。

### サインインフロー（SSO デバイス認可）と 2 つのエンジン

両方式を支持する選択を受けています。どちらも結果は同じ（`AWS_PROFILE` 経由で到達できる、標準の SSO トークン
キャッシュが満たされた状態）で、自動選択します（CLI があればそれを優先、なければネイティブのフロー）。設定で
固定もできます。

1. **ネイティブ（boto3 `sso-oidc`）。** Bajutsu が `RegisterClient` → `StartDeviceAuthorization` を呼び、
   `verificationUriComplete` と `userCode` を Web UI に返します。続いてバックグラウンドで `CreateToken` を
   ポーリングし、承認されたらトークンを標準キャッシュ（`~/.aws/sso/cache/<sha1>.json`）に書いて botocore に
   消費させます。AWS CLI への依存はありません（boto3 は `anthropic[bedrock]` extra で既に入ります）。要検証
   点は、自動更新が効くよう botocore が期待する形式どおりにキャッシュを書くことです。
2. **CLI への委譲（`aws sso login --no-browser`）。** AWS CLI v2 があれば、それをシェル実行します（`make serve`
   が idb のためにシェル実行するのと同じ要領）。検証 URL とコードを出力し（UI へ中継）、トークンキャッシュと
   その更新は CLI が受け持ちます。CLI が存在する環境ではより堅牢です。

### serve エンドポイント（BE-0051 の作法に従う）

`provider_info` ／ `set_provider` の隣に新しいエンドポイントを置きます。いずれも
[BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)
に従います（全リクエストでトークン認証、トークンなしの非ループバック bind は拒否、状態変更 POST は Origin
チェック＋セッション Cookie ／ Bearer）。

- `GET /api/sso` — セッション状態。サインイン済みか、有効な `AWS_PROFILE`、期限、プロファイルが解決するか。
- `POST /api/sso/login` — ボディでプロファイルを指定し、デバイスフローを開始。`{ verificationUri, userCode,
  expiresIn }` を返す。
- `GET /api/sso/login/<id>` — ポーリング。完了したら `AWS_PROFILE`（指定があれば `AWS_REGION` も）を
  `os.environ` に設定して成功を返す。
- `POST /api/sso/logout` — `AWS_PROFILE` をクリア（任意でキャッシュ済みトークンを無効化）。

### Web UI（Settings パネル）

プロバイダが `bedrock` のとき、既存のモデル ID ／リージョンの操作の隣に **AWS SSO** ブロックを追加します。
プロファイルの選択（または名前入力）と **AWS SSO でサインイン** ボタンです。押すと検証 URL（新しいタブで
開く）とコピー可能なユーザーコードを「承認待ち…」の状態で表示し、完了するとセッション表示（プロファイルと
期限）に切り替わり、**再認証** と **サインアウト** を備えます。このブロックは `bedrock` のときだけ表示します。
API キーのブロックが `anthropic` のときだけ表示されるのと同じ作りです。

### 認証の詳細

ネイティブのフローでは、**SSO セッションのリージョン**（`sso-oidc` 呼び出し用。プロファイルの `sso_session`
から）と、**Bedrock 推論のリージョン**（`AWS_REGION`、BE-0053 のとおり）を区別します。ロールの認証情報自体は
`AWS_PROFILE` 経由で botocore の SSO プロバイダが解決し、Bajutsu はデバイスフローを起動してトークンキャッシュ
を満たすだけです。これは BE-0047 のフェイルクローズの方針と噛み合います。設定したプロバイダの認証情報が解決
しなければ、ジョブは明確なエラーで失敗し、別プロバイダへ暗黙にフォールバックはしません
（[BE-0047](../../implemented/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md)）。

### 依存

ネイティブのフローは boto3/botocore だけで足ります（`anthropic[bedrock]` extra に既に含まれます）。CLI への
委譲は AWS CLI v2 が必要です（任意・自動検出）。BE-0053 と同様、Bedrock 経路を使う時点で `uv sync --extra
bedrock` が前提です。`make serve` は idb の extra はオンデマンドで入れますが、Bedrock の extra は入れません。

### doctor（任意）

選択した SSO プロファイルが解決し、セッションが期限内かを確かめる決定的チェックを `doctor` に足せます。
BE-0053 が挙げたプロバイダ認証情報チェックと同じ趣旨です。本項目に含めるかは **TBD** です。

### 範囲外

- 共有サーバでのユーザー別 SSO ID（マルチテナント）→
  [BE-0015](../../in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) ／
  [BE-0016](../../in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)。
- 静的な AWS キーの貼り付けや `AWS_BEARER_TOKEN_BEDROCK` の UI からの管理 — 既存の環境変数 ／ `.env` 経路で
  足ります。本項目は SSO 体験に限ります。
- `claude-code` agent 経由の Bedrock — 別機構で、BE-0053 でも範囲外です。

## 検討した代替案

- **静的な一時 AWS キー（`AWS_ACCESS_KEY_ID` ／ `…SECRET…` ／ `…SESSION_TOKEN`）を貼る欄。** 却下。一時キーは
  設定時に serve プロセスへ固定され更新されないため、セッション途中で失効し再起動を強います。`AWS_PROFILE` を
  軸にした SSO セッションが避けるまさにその問題です（BE-0053 の議論）。
- **CLI 委譲のみ（ネイティブのフローを持たない）。** 単独では却下。serve ホストに AWS CLI v2 を必須化し、
  リモートホストではブラウザを誤った場所で開きます。両方を備えて自動選択します。
- **ネイティブのフローのみ（CLI 委譲を持たない）。** 単独では却下。SSO トークンキャッシュ形式を自前で再実装
  すると botocore 内部の形に結合します。CLI がある環境では委譲する方が堅牢です。ゆえに両エンジンです。
- **Bajutsu 自前の認証情報ストア。** 却下。BE-0047 ／ BE-0053 の方針どおり、認証情報はユーザーのもので、AWS
  標準の場所に置きます。serve は `AWS_PROFILE` でそこを指すだけです。
- **SSO 設定一式（start URL ／ アカウント ／ ロール）を UI から入力する。** v1 では見送り、既存の
  `aws configure sso` プロファイルを前提とします。将来の拡張として記録します。

## 進捗

- [x] **ネイティブエンジン**（`bajutsu/serve/sso.py`）。boto3 の `sso-oidc` デバイス認可フローを実装しました。
  トークンは botocore 自身の `SSOTokenLoader` 経由で永続化するため、認証情報チェーンが読み戻すキャッシュの
  キーや形式と一致します。
- [x] **CLI 委譲エンジン**（`CliSsoEngine`、`aws sso login --no-browser`）。AWS CLI v2 が存在する場合に
  そちらへシェル実行で委譲し、出力される検証 URL とコードを読み取って終了を待ち受けます。
  `default_sso_engine()` が、`aws` が `PATH` にあればこのエンジンを、なければネイティブエンジンを
  自動選択します。
- [x] **`ServeState.sso_engine` の継ぎ目と各操作**。`sso_info` ／ `sso_login_start` ／ `sso_login_poll` ／
  `sso_logout` を BE-0051 の RBAC で管理者権限に限定し、`_spawn_env` 経由で起動されるジョブにも引き継ぎます。
- [x] **stdlib と FastAPI 両サーバへのルート追加**。`/api/sso`、`/api/sso/login[/<handle>]`、
  `/api/sso/logout` です。
- [x] **Settings パネルの UI**（Bedrock のときのみ表示）。プロファイル入力とサインインボタン、検証 URL と
  ユーザーコード、完了までのポーリング、セッション状態表示とサインアウトを備えます。
- [ ] **実環境での検証**。ネイティブエンジンの AWS 呼び出しを、実際の IAM Identity Center 環境で確かめます。
  Linux の CI ゲートでは実行できないため、上記の配線は注入した fake エンジン経由でのみゲートで検証済みです。

[#166](https://github.com/bajutsu-e2e/bajutsu/pull/166) で、ネイティブエンジン、CLI 委譲エンジン、serve
側の配線、Settings パネルの UI を実装しました。

## 参考

`bajutsu/anthropic_client.py`（プロバイダのクライアントファクトリ）、`bajutsu/serve/operations.py`
（`set_provider` ／ `provider_info` ／ `set_api_key` — 環境にしか書かない設定ハンドラ）、
`bajutsu/serve/jobs.py`（`_spawn_env` — ジョブは serve の環境を継承する）、
`bajutsu/templates/serve.js`（Settings パネル）、
[DESIGN §2 / §3.1](../../../DESIGN.md)、
[BE-0053 — 差し替え可能な AI プロバイダとしての Amazon Bedrock](../../implemented/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider-ja.md)、
[BE-0051 — ホスティングのための serve ハードニング](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)、
[BE-0016 — Web UI のセルフホスティング](../../in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)、
[BE-0047 — AI データ主権](../../implemented/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md)、
AWS ドキュメント — IAM Identity Center のデバイス認可フロー、botocore の SSO 認証情報プロバイダ。

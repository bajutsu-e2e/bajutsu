[English](BE-0224-github-private-repo-config-auth.md) · **日本語**

# BE-0224 — GitHub を取得元とする config での private リポジトリへのアクセス権限の付与

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0224](BE-0224-github-private-repo-config-auth-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0224") |
| 実装 PR | [#956](https://github.com/bajutsu-e2e/bajutsu/pull/956) |
| トピック | config の取得元 |
| 関連 | [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md), [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md), [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md), [BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets-ja.md), [BE-0184](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md) により、`--config`（と serve
の config ピッカー）で GitHub リポジトリを ref 付きで指定できるようになりました。書式は
`github:<owner>/<repo>@<ref>:<path>` です。Bajutsu はそのサブツリーを実体化し、そこから config を読み込みます。
**public** リポジトリに認証情報は要りません。**private** リポジトリには要ります。

現状、その認証情報を解決するのは 1 つのヘルパー `github_token()`
（[`bajutsu/config_source.py:100`](../../bajutsu/config_source.py)）です。`GITHUB_TOKEN` と `GH_TOKEN`
を読み、無ければ `gh auth token` にフォールバックし、それも無ければ匿名になります。結果はプロセス全体で
共通の 1 つのトークンで、`Authorization: Bearer` として `api.github.com` へ送られます。

開発者が自分のマシンで自分の private リポジトリを読むだけなら、これで足ります。しかし、この機能が本来
ねらう場面には不十分です。その場面とは、**無人で稼働し、利用者が複数いることもあるセルフホストの `serve`**
（[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)）が、1 つ以上の private
テストリポジトリを読むことです。

本項目は **github.com** に限定し、問いを 1 つに絞ります。*private リポジトリへのアクセス権限を、GitHub を
取得元とする config にどう付与するか*、です。次の 4 点を整理します。

- 受け入れる認証情報の種類
- それぞれの供給経路とスコープ
- それぞれが必要とする最小権限
- 認証情報が欠けているか不足しているときの報告の仕方

変わるのは認証情報の取得だけです。スキーマ、runner、
[ドライバ](../../docs/ja/glossary.md#driver-backend-actuator-platform)、決定的なゲートは手つかずで、
大規模言語モデル (LLM) の呼び出しも加えません。

## 動機

現在の単一トークン方式には 3 つの欠落があります。config の取得元を BE-0016 が意図する形で使うと、
初めて現れます。

- **無人ホストのためのサービス識別子がない。** セルフホストの `serve` デーモン（launchd や systemd）に
  対話的な `gh` セッションはなく、`gh auth token` は使えません。運用者は、デーモンのレベルで個人
  アクセストークン (personal access token; PAT) を注入するしかありません。PAT は*人*に紐づきます。その人の
  アクセス権限をそのまま帯び、その人がローテーションしたり離任したりすると使えなくなります。private
  リポジトリを無人で読むサービスは、**それ自身として**認証すべきです。GitHub でそれにあたるのが **GitHub App
  のインストール**で、トークンは短命で、インストール単位に絞られ、監査もできます。Bajutsu には、それを
  発行して使う経路がありません。
- **スコープがない。すべてのリポジトリとすべての利用者で 1 つのトークン。** `github_token()` は、プロセス
  全体で 1 つのトークンを返します。どのリポジトリを要求されても、それを送ります。マルチ org のセルフホスト
  （BE-0016 はマルチ org 分離を出荷済み）では、これが問題になります。どのテナントの config 取得も、
  運用者の単一の識別子と全アクセス権限を借りてしまうからです。認証情報を特定の取得元（owner/repo）や org に束ねる手段は、ありません。
- **最小権限の文書化と誘導がない。** `repo` スコープの classic PAT は、その利用者の*すべての*
  private リポジトリへの読み書きを許可します。「このテストリポジトリを 1 つ読む」よりはるかに広い権限です。
  GitHub にはより狭い付与があります。特定リポジトリに **Contents: read** で絞った fine-grained PAT と、
  App のインストールです。しかし、それを説明するものも、運用者にそちらを選ばせるものもありません。
  結果として、もっとも抵抗の少ない道が、もっとも過剰な権限を持つ認証情報になります。

4 つ目の小さな欠落が、以上の 3 つをいっそう運用しづらくします。呼び出し元から見えない private
リポジトリは、403 ではなく **404** を返します。`_get` はその `HTTPError` をそのまま伝播させます
（[`config_source.py:126`](../../bajutsu/config_source.py)）。そのため、「アクセス権限を付与していない」が
「リポジトリが存在しない」に見えます。

Web UI は、これらの欠落をすべて際立たせます。serve の「Open config」ダイアログは、すでに「**From a Git
repository**」の取得元を提供しています（BE-0063）。バインドは、`bajutsu/serve/handler.py:318` の
`ops.bind_config` 経由です。しかし、ダイアログには**認証情報の欄がありません**。トークンは依然として serve
プロセスの環境からしか来ません。デーモンにトークンが無いマシンで、UI から private リポジトリを指した
利用者は、不透明な 404 を受け取ります。目の前の画面からアクセスを渡す手段はありません。

この利用者がセルフホスト運用者です。このダイアログは、認証情報をデーモンへ事前注入するのではなく、
*入力して保存できる*唯一の場所でもあります。

これらはいずれも prime directive に触れません。認証情報の解決は決定的でモデルを介さず、config を*取得*する
だけです。解決された SHA が、決定性のアンカーであり続けます。認証情報の差はすべて環境か config に置かれ、
ツール、ドライバ、runner のいずれにも入りません。

## 詳細設計

private リポジトリへのアクセスを、互いに排他で全体を覆う (MECE) 5 つに整理します。いずれも github.com
限定です。4 つ目は単独で出せます。

### 1. 受け入れる認証情報の種類

この節では、private リポジトリの読み取りを許す認証情報の集合を、定義して文書化します。無人サービスへの
適性が低いものから高いものへ並べます。

- **`gh auth token`（対話的、開発者向け）。** 自分のマシンで作業する開発者向けの、現状のフォールバック
  として残します。
- **`GITHUB_TOKEN` / `GH_TOKEN` 経由の個人アクセストークン。** 残します。classic の広い `repo` スコープの
  PAT よりも、対象リポジトリに **Contents: read** で絞った **fine-grained** な PAT を強く推奨する形で
  文書化します。
- **GitHub App のインストールトークン（セルフホストのサービスに推奨）。** 新規です。Bajutsu に App の id、
  秘密鍵、対象のインストール（またはリポジトリから解決したインストール）を与えます。Bajutsu は短命な
  インストールアクセストークンを発行し、それを Bearer として使います。

この App トークンが、サービス識別子への答えです。トークンは短命で、インストールのリポジトリに限られ、
人に紐づきません。

実質的なロジックが増えるのは、この部分だけです。JSON Web Token (JWT) の署名と、インストールトークンの
エンドポイント呼び出しです。同じ解決の継ぎ目の背後に置く任意の認証情報プロバイダとして設計します。
PAT だけを使う構成が、余分なものを引き込むことはありません。

### 2. 供給経路と優先順位

この節では、各認証情報が**どこから**来て、**どの順で**解決されるかを定めます。挙動が予測可能になり、
無人デーモンには明確な入り口ができます。

- 文書化された優先順位です。たとえば、明示的な App 認証情報、次に `GITHUB_TOKEN` / `GH_TOKEN`、次に
  `gh auth token` です。
- デーモンの供給経路を明記します。PAT は launchd の `EnvironmentVariables`、systemd の `Environment`、
  秘密情報ファイルのいずれかで渡します。App の経路では App の秘密鍵ファイルを使います。
- 起動時に一度だけ取り込まず、**取得のたびに**解決します。環境変数の経路では、トランスポートが
  `materialize` ごとに生成されるため、すでにそうなっています。ローテーションした秘密情報は、再起動なしで
  効きます。

### 3. 認証情報を取得元に束ねる

認証情報を、**config の取得元へ束ねられる**形にします。プロセス全体で共通の 1 つのトークンだけには頼りません。
束ねる単位は、特定の owner/repo か org です。マルチ org のセルフホストが、1 つの識別子をテナント間で
共有せずに済みます。

この節は、config の取得元を BE-0016 のマルチ org 分離と整合させる部分です。*ホスト*されたデプロイでの
表出は、
[BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md)
（ホストされた UI がそもそもどの config 取得元を提供するかを決めます）と、
[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)
の既存の認証で境界づけられます。

ここで詰めるべき主要な設計上の選択は、束ねを表す場所です。config で表すか、環境で表すか、serve の認証済み
API を通すかを決めます。認証情報はログに出さず、BE-0063 がすでに求めるとおり、秘匿化のデフォルト対象に加えます。

### 4. 最小権限のガイダンスと認証の診断

この節は単独で出せます。

- 各種類の**最小の付与を文書化**します。fine-grained PAT でも App のインストールでも、対象リポジトリへの
  Contents: read です。[docs/configuration.md](../../docs/configuration.md) と
  [docs/self-hosting.md](../../docs/self-hosting.md) に日英両方で書き、運用者を狭い付与へ誘導します。
- **認証の失敗を読み取れるようにします。** トランスポートの `HTTPError` を包み、ステータス*と*サブタイプで
  振り分けます。具体的なケースを先に調べ、常にアクセス不足のせいにせず、本当の原因をメッセージで名指し
  します。
  - **`X-RateLimit-Remaining: 0`（または `Retry-After` ヘッダ）を伴う 403** → レート制限または濫用検知の
    メッセージです（待つか、認証して上限を上げます）。アクセスのメッセージ*ではありません*。リポジトリ権限を
    増やしても、レート制限は解消しません。
  - **`X-GitHub-SSO` レスポンスヘッダを伴う 403**→ org は Security Assertion Markup Language
    (SAML) のシングルサインオン (SSO) による認可を求めているので、「この認証情報を org の SSO に対して
    認可してください」と伝えます。これも、リポジトリ権限の付与とは別です。
  - **401** →「渡されたトークンが拒否されました」。
  - **404**、またはその他の **403** →「リポジトリが見つからない、*または*アクセス権限が付与されて
    いません。`<owner>/<repo>` に Contents: read を持つ認証情報を渡してください」。最も不足していそうな
    付与を名指しすることが要点ですが、上記の具体的なケースの後の受け皿としてのみ用います。

  「Contents: read」を提案する前に 403 のサブタイプ（ヘッダ）を調べることで、運用者を誤った道へ送らずに
  済みます。この診断は認証情報プロバイダの変更を必要とせず、最初に出せます。

### 5. serve の Web UI の面

セルフホスト運用者が実際にアクセスを付与するのは Web UI です。そのため、上記の認証情報モデルは画面まで
届く必要があります。並行する仕組みを新設せず、serve の既存の継ぎ目を再利用します。

- **ダイアログの認証情報の受け口。** 「From a Git repository」ダイアログで private な Git ソースを
  バインドするとき、運用者は**保存済みの認証情報を選ぶ**か、**新しい認証情報を入力**できます。入力するのは
  fine-grained PAT か、1 つ目の節の App 認証情報です。ブラウザは値を送信まで保持し、保存された認証情報が読み返されることはありません。
  serve がすでに秘密情報を扱うのと同じく、ダイアログはマスク済みのプレビューを表示します。
- **新しいストアではなく既存の `SecretStore` の継ぎ目に保存する。** serve には、これに合う継ぎ目がすでに
  あります。local の単一利用者向けの `EnvSecretStore` と、hosted バックエンド向けの、org 単位で保存時暗号化
  される `DbSecretStore` です
  （[BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets-ja.md)、
  `bajutsu/serve/secrets.py`、`bajutsu/serve/server/secrets.py`）。UI で入力した Git 認証情報は、この継ぎ目を
  通ります。表示はマスク済みのプレビューのみで、HTTP ハンドラが平文を読み返すことはできません。永続性は、
  2 つのバックエンドがすでに持つものと同じです。
  - local serve では、**プロセスの生存期間だけ保持**されます。`EnvSecretStore` は認証情報をメモリに置く
    ので、local の単一利用者の認証情報は、現状の env 経路と同じく再起動をまたぎません。
  - hosted バックエンドでは、**org ごとに保存時暗号化して永続化**されます（`DbSecretStore`）。

  hosted の経路は、3 つ目の節の**取得元ごと／org ごとのスコープ**を具体化する部分でもあります。認証情報は
  すでに `org_id` でスコープされています。各テナントの保存済み Git 認証情報は、自然にそれぞれのものに
  なります。

  AI プロバイダ設定を保存する、読み取り可能な `provider_store`
  （[BE-0184](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings-ja.md)）
  は、serve の設定保存の面としての先例です。それでも、Git 認証情報が属するのは読み取り可能なストア
  ではなく、*書き込み一度きり*の `SecretStore` です。
- **[BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md)
  に沿ってデプロイを意識する。** 認証情報の欄は、Git ソースにのみ付随します。Git ソースは local と hosted
  の両バックエンドで提供されます。Git ソースを提供しないバックエンドには、何も新たに露出しません。hosted
  バックエンドでは、認証情報は上記のとおり org ごとです。local serve では、現状どおりデフォルトでプロセス環境
  なので、local の単一利用者の実行は変わりません。
- **診断をダイアログに表示する。** バインドが失敗したとき、4 つ目の節の認証メッセージを**バインド
  ダイアログにインラインで**表示します。404 と 403 のメッセージは「アクセス権限が付与されていません。
  `<owner>/<repo>` に Contents: read を持つ認証情報を渡してください」です。運用者は、サーバのログを読まずに
  その場で直せます。

### 変わらないもの

spec の構文、SHA でキー付けしたキャッシュ、`_load_effective` の継ぎ目、config のスキーマ、`resolve()` は
手つかずです。runner、ドライバ、アサーション評価器、決定的なゲートも同様です。変わるのは、*private な
github.com リポジトリ向けの認証情報の選び方、束ね方、診断のしかた*だけです。BE-0063 の
「取得だけが変わる」を、private アクセスの問いに絞ったものです。

## 検討した代替案

- **SSH の deploy key。** 却下します。BE-0063 のトランスポートは git-over-SSH ではなく、HTTPS の REST
  tarball エンドポイントです。そのため、deploy key は呼び出しを認証しません。対応するには `git clone` へ
  切り替えることになりますが、それは BE-0063 がすでに却下しています。`git` バイナリが要り、任意の SHA の
  浅いフェッチが一様でないからです。
- **classic PAT を推奨経路にする。** *推奨*としては却下します。入力としては残します。classic の `repo`
  スコープの PAT は過剰に付与し（すべての private リポジトリへの読み書き）、人に紐づきます。ガイダンスは
  fine-grained PAT か App のインストールへ誘導します。
- **プロセス全体で共通の単一トークンのままにする。** 却下します。単一チームのセルフホストには十分です。
  しかし、BE-0016 のマルチ org 分離には合いません。1 つの共有識別子がテナント境界を越えるからです。
- **認証情報を config ファイルやリポジトリに保存する。** 却下します。秘密情報はバージョン管理に置くべき
  ではありません。認証情報は、環境、鍵ファイル、または serve の秘密情報ストアに置きます。
- **serve 内での OAuth デバイスフロー。** 見送ります。より重く、環境と App の経路が無人の場合をすでに
  覆っています。対話的なセルフホストログインが欲しくなれば、デバイスフローは後から足せます。
- **App のみにして PAT の経路を落とす。** 却下します。App はサービスにとって正しい*既定*ですが、単一の
  開発者には過剰です。両方を残し、無人ホストには App を推奨します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] #4 最小権限の文書（configuration と self-hosting、日英両方）を書き、`config_source.py` に `HTTPError` 401/403/404 の認証診断を加えました（`github_http_error_message` がレート制限 / SSO / 401 / 404 を、特定のサブタイプを先に判定して振り分けます。`GitHubAccessError` は `ValueError` なので、原因を CLI には exit-2 で、serve にはインラインで伝えます）。
- [x] #1 GitHub App のインストールトークンプロバイダ（`bajutsu/github_app.py`。`githubapp` extra の `cryptography` による RS256 JWT ＋インストールトークンのエンドポイント）を、既存の PAT / `gh` の経路と並べて認証情報の継ぎ目の背後に遅延ロードで置きました。
- [x] #2 認証情報の優先順位（App → `GITHUB_TOKEN`/`GH_TOKEN` → `gh auth token` → 匿名、取得のたびに解決）を文書化し、無人デーモンの供給経路（launchd / systemd の環境変数、App の鍵ファイル）を用意しました。
- [x] #3 ホスト型の `DbSecretStore` により組織ごとに認証情報をスコープしました（各組織の保存した Git 認証情報はそれぞれ独立です）。BE-0108 / BE-0051 で境界づけます。取得元ごと（owner/repo）の束縛は今回は対象外とし、今後の後続にします。
- [x] #5 serve の「From a Git repository」ダイアログに認証情報欄を設け、`SecretStore` の継ぎ目（BE-0136。新しい `gitConfigToken` シークレットをローカルでは `GITHUB_TOKEN` に、ホスト型では組織ごとに暗号化して保存）に保存し、デプロイを意識し（BE-0108）、認証診断をインラインで表示します。

**ログ**

- #1〜#5 を一つの変更で実装しました。`config_source.py` / `github_app.py` の認証情報の継ぎ目と App プロバイダと診断、serve の `/api/gitcredential` エンドポイントと UI 欄、そして日英のドキュメントです。取得元ごと（owner/repo）のスコープは見送り（#3 は組織ごととして実装）。PR: [#956](https://github.com/bajutsu-e2e/bajutsu/pull/956)。

## 参考

- [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)：本項目が拡張する Git を
  取得元とする経路。[`bajutsu/config_source.py`](../../bajutsu/config_source.py)（`:100` の
  `github_token()`、`:124` の `_GitHubTransport` の bearer ヘッダ、`:133,137` の `api.github.com` への
  リクエスト、`:126` の `_get`）。
- [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)（セルフホストとそのマルチ org
  分離。取得元ごとのスコープが要る理由）、
  [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)（serve の
  ハードニング。認証情報の経路が守るトークン認証、秘匿化、パスの封じ込め）、
  [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md)
  （ホストされたデプロイが提供する config 取得元）、
  [BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets-ja.md)（UI で入力した
  認証情報を保存する `SecretStore` の継ぎ目）、
  [BE-0184](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings-ja.md)
  （serve の設定保存の先例）。
- serve：`bajutsu/serve/handler.py`（`bind_config`）、`bajutsu/serve/secrets.py` /
  `bajutsu/serve/server/secrets.py`（`SecretStore` の継ぎ目）、`bajutsu/serve/state.py`（アクティブな
  config の Git ソース来歴）。
- GitHub ドキュメント：個人アクセストークン（fine-grained と classic）、GitHub App とインストールアクセス
  トークン、リポジトリの `Contents` 権限。
- [docs/configuration.md](../../docs/configuration.md)、[docs/self-hosting.md](../../docs/self-hosting.md)。

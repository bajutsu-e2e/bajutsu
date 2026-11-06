[English](BE-0313-github-org-team-rbac.md) · **日本語**

# BE-0313 — GitHub Organization メンバーシップと Team ベースの RBAC を serve に導入

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0313](BE-0313-github-org-team-rbac-ja.md) |
| 提案者 | [@paihu](https://github.com/paihu) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0313") |
| 実装 PR | [#1325](https://github.com/bajutsu-e2e/bajutsu/pull/1325) |
| トピック | Web UI のホスティング |
| 関連 | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md), [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md), [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md) |
<!-- /BE-METADATA -->

## はじめに

この項目は、serve のロールベースアクセス制御（RBAC）が持つ2つの仕組みを、GitHub 自身の Organization と Team のメンバーシップに置き換えます。置き換える対象は、サインインを許可する GitHub login の許可リストと、admin、editor、viewer のロールを割り当てるログインリストです（[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) §7b〜7c）。

新しい規則は次のとおりです。

- サインインには、設定した GitHub Organization へのメンバーシップを必須とします。このメンバーシップが viewer ロールを付与します。
- 1つのフラットな GitHub Team へのメンバーシップは、editor への昇格をもたらします。
- それとは別に、サーバー全体で共通の1つの Team へのメンバーシップは、admin を付与します。

さらにこの項目は、共有トークン（`BAJUTSU_SERVE_TOKEN`、[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)）を、OAuth を設定した場合には worker と serve の間の通信専用に絞ります。トークンの他の用途は、認可の手段としてすべて廃止します。廃止するのは、人間による Cookie サインインと、他のエンドポイントへの直接の `Authorization: Bearer` 呼び出しです。

## 動機

serve の現在の RBAC（BE-0015 §7c）は、環境変数から3つの GitHub login リストを読みます。

- `BAJUTSU_OAUTH_ALLOWED_USERS` は、サインインそのものを許可します。
- `BAJUTSU_OAUTH_ADMINS` と `BAJUTSU_OAUTH_VIEWERS` は、admin と viewer のロールを割り当てます。
- 許可リストに載った残り全員は、デフォルトで editor になります。

それぞれのリストは、デプロイの運用者が手で保守する login の名簿です。同じ名簿を、GitHub がすでに Organization のメンバー一覧と Team として持っています。社員が入社し、退職し、異動するたびに、環境変数を編集して再デプロイしなければ、サーバー側のリストは実態と一致しません。

Bajutsu が想定するデプロイ先は、すでに GitHub 型の構造に沿っています。GitHub でサインインするエンジニアリング組織があり、その一部のメンバーが scenario を書いて保守します。serve のロールをこの構造から導けば、重複した名簿は不要になります。閲覧権限は、Organization のメンバーシップに従います。書き込み権限は、組織が scenario の保守担当者をまとめるためにすでに使っている Team のメンバーシップに従います。

BE-0015 §7c-2 は、ログインごとにロールを方針から再計算する仕組みをすでに備えています。この仕組みにより、方針を変更してもデータマイグレーションなしで反映されます。この項目は、その原則をそのまま保ちます。変えるのは、方針の読み出し元だけです。固定の環境変数リストに代えて、GitHub 自身が持つメンバーシップの記録を読みます。

共有トークンをサインイン手段から外すと、これとは別のもう1つの隙間も塞げます。この隙間は、ブラウザのサインインより広いものです。OAuth と共有トークンの両方を設定したデプロイでは、今日、ブラウザが `POST /api/login` を通じて共有トークンでサインインできます。この経路は、OAuth と、その上に立つ RBAC を迂回します。

これとは別に、どのクライアントでも、同じトークンをそのまま `Authorization: Bearer` ヘッダーとして渡せば、任意のエンドポイントにアクセスできます。`gate.is_authorized` は、ロールを確認すべき identity がないまま、これを許可します。この Bearer 直通の経路は、BE-0015 §7b がトークンについて定めた「operator credential としてフルアクセスを許す」経路です。

GitHub の Organization と Team のメンバーシップが権限を決めるようになったら、両方の経路を閉じる必要があります。ブラウザの経路だけを閉じると、ロールの強制にはトークン1つ分の穴が残ります。穴は、サインインフォームから、認証済みの任意の HTTP クライアントへ移るだけです。

## 詳細設計

### 用語：GitHub Organization、Bajutsu テナント、project

この項目は、GitHub 側の identity の出どころを「GitHub Organization」と呼びます。Bajutsu 自身のマルチテナントの単位は、「Bajutsu テナント」と呼びます。Bajutsu テナントは `orgs:` の1エントリで、コード上では「org（tenant）」と呼ばれています。2つの語は「org」という語を共有していますが、指すものは別です。以下の設計では、どちらを指すかをその都度明示します。

Bajutsu テナントの下には、もう1段細かい単位があります。**project**（[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)）は、1つのテナントの下に登録される config ソースへのバインディングです。多くの場合、1つの project は1つのリポジトリに対応します。1つのテナントは、複数の project を持てます（`ProjectRecord.org_id`、1対多）。

project 自体は、今日 RBAC を持ちません。どのリクエストも、actor が属する Bajutsu テナントだけからロールを解決します。1つのテナントの中でアクティブな project は、常に1つだけです。アクティブな project の切り替えは、admin 限定です（`activate_project`、[`bajutsu/serve/operations/projects.py`](../../bajutsu/serve/operations/projects.py)）。

この項目は、後述する `editorTeam` を、project ではなく Bajutsu テナントに付けます。1つのテナント配下のすべての project が同じ保守担当者の集合を共有するデプロイには、この付け方がそのまま当てはまります。

リポジトリごとに異なる書き込み担当者を持たせたいデプロイもあります。例えば、モバイル担当 Team が1つのリポジトリに、Web 担当 Team が別のリポジトリに書き込み、両方が同じ GitHub Organization の配下にあるケースです。このデプロイは、今日すでに実現できます。その GitHub Organization に一致する（あるいは `githubOrgs` が重なる）複数の Bajutsu テナントを宣言し、それぞれに異なる `editorTeam` を設定します。project 単位のアクセス制御は新設しません。詳しくは「検討した代替案」を参照してください。

### 閲覧権限は GitHub Organization のメンバーシップに従う

`orgs:` ブロックは、login を Bajutsu のテナントに対応づけています（`org_for_identity`、[`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)）。対応づけには、明示的な `members` の列挙、または `githubOrgs` リストと login が属する GitHub Organization との積集合を使います。

今日、この対応づけが決めているのは、すでに許可リストを通過した login が「どのテナントに属すか」だけです。どのテナントにも一致しない login は、締め出されずに、単一の `default` テナントに落ち着きます。サインインそのものを許可するかどうかは、別の `BAJUTSU_OAUTH_ALLOWED_USERS` 許可リストが決めているからです。

この項目は、この判断を同じ対応づけに折り込みます。デプロイに設定された `orgs:` ブロックについて、サインインが成功する login は次のどちらかです。

- `members` に明示的に列挙されている login。
- `githubOrgs` に挙げた GitHub Organization のメンバーである login。

それ以外の login は、`BAJUTSU_OAUTH_ALLOWED_USERS` が今日返しているのと同じ「許可されていない user」という応答で拒否します。サインインに成功した login には、最低でも viewer ロールを付与します。`BAJUTSU_OAUTH_ALLOWED_USERS` と `BAJUTSU_OAUTH_VIEWERS` は廃止します。別のリストに代えて、Organization 自身の名簿が許可リストになります。

「最低でも」という表現は、単なる言い回しではなく、今日の editor すべてにとって実質的な降格です。`role_for`（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）は、許可リストに載った login を、今日デフォルトで editor にします。例外は、`BAJUTSU_OAUTH_VIEWERS` に明示されている場合です。scenario の書き手は、今日デフォルトで editor です。

この項目は、この基底ロールを viewer へ反転させます。今日 editor である user は、`editorTeam` へあらかじめ登録されない限り、次回ログインで書き込み権限を失います。この対応は、後述する `orgs:` ブロックの宣言と同種の、採用時の対応です。

`org_for_identity` は、この拒否を単独では伝えられません。`org_for_identity` は `str` を返す関数です。正当に `default` へ解決する login と、一致しない login は、どちらも同じ `DEFAULT_ORG` の値になります（[`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)）。

そのため判定には、専用の一致チェックが必要です。一致チェックは、`org_for_identity` がすでに行っている `members` と `githubOrgs` の判定を、一致するかどうかという単純な形で取り出したものです。判定は、その login に対して `org_for_identity` を呼ぶ**前**に、一致チェックを走らせます。

- 判定が受け入れる login は、必ず何かに一致します。`org_for_identity` は、その login に対して `default` へ落ちません。
- 判定が拒否する login は、そもそも `org_for_identity` に届きません。

ここから、1つの帰結が直接導かれます。`orgs:` ブロックを持つどのデプロイでも、`default` テナントは OAuth サインインでは到達不能になります。今日の `default` は、一致しない login の受け皿です。そこへ落ちるはずだった login は、配置が計算される前に拒否されます。

`members` だけのテナントは、`orgs:` ブロックに `members` はあるが `githubOrgs` がないテナントです。このテナントは、`members` の列挙で初めてサインインを制御します。今日の `members` は、すでに許可された login がどのテナントに属すかを決めるだけです。サインインが成功するかどうかは、`BAJUTSU_OAUTH_ALLOWED_USERS` だけが決めています。

`orgs:` ブロック自体を持たないデプロイには、頼れる `members` の列挙がありません。そのため、`BAJUTSU_OAUTH_ALLOWED_USERS` を廃止すれば、すべての login が拒否されます。この項目を採用するデプロイは、`orgs:` ブロックを宣言しない限り、サインインの手段を失います。ブロックの中身は、`members` の列挙か `githubOrgs` のエントリです。

この判定は、無条件に動く必要があります。`_org_for_login` が今いる `if state.repository is not None:` ブロックの中だけで動かしてはなりません（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）。今日、`BAJUTSU_OAUTH_ALLOWED_USERS` による拒否は、`oauth_callback` の先頭にあり、このブロックより前です。そのため、OAuth を設定していてもデータベースを配線していないデプロイでも、サインインを制御できています。このデプロイは、`state.repository` の有無にかかわらず、`oauth_callback` の末尾で identity にセッションを発行します。

新しい org/Team の判定を、そのまま `_org_for_login` の中に折り込むと、`BAJUTSU_OAUTH_ALLOWED_USERS` を廃止した時点で、このデプロイが持つ唯一の判定が消えます。すべての GitHub user が通ってしまいます。そのため拒否は、同じ先頭の位置のまま行います。一致チェックを計算し、許可か拒否かを決めるのは、データベースのブロックより前です。ブロックの中にある `_org_for_login` の呼び出しは、すでに受け入れた login について、どのテナントに永続化するかだけを決めます。拒否するかどうかは決めません。

これにより、サインインそのものが GitHub の `/user/orgs` への呼び出しの成功に依存します。対象は、`members` に明示登録されていない login です（`_fetch_orgs`、[`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py)）。今日はこの依存がありません。サインインの可否を決めるのは `BAJUTSU_OAUTH_ALLOWED_USERS` だけで、GitHub の API 呼び出しに成功する必要がないからです。

`_fetch_orgs` は、非200応答や本文のパース失敗の場合に、すでに空の org 一覧へ fail open します。今日、この失敗が送るのは `default` テナントだけです。この項目のもとでは、同じ失敗によって、障害が続くあいだ、`githubOrgs` だけに頼る login のサインインそのものが失敗し続けます。

本物のネットワーク障害は、これとは別の既存のケースです。ネットワーク障害は、`_fetch_orgs` の中では捕捉されず、`fetch_identity` を経て `oauth_callback` 自身の例外処理まで伝播します。そこでは今日、すべての login に対して、交換全体がすでに 502 で失敗しています。この項目の影響は受けません。

この項目は、非200応答やパースエラーという新しいトレードオフを受け入れます。org メンバーシップ取得に、リトライやキャッシュの仕組みは足しません。理由は2つあります。

- `members` に明示登録された login は、影響を受けません。`fetch_identity` は、どの login でも `/user/orgs` を呼びます。ただし `org_for_identity` は、明示的な `members` の一致を見つけると、取得した一覧を見る前に値を返します。
- `githubOrgs` だけに頼る login は、GitHub の API へ再び到達できれば、もう一度サインインするだけで済みます。

### 書き込み権限は1つのフラットな GitHub Team のメンバーシップに従う

`OrgConfig`（[`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)）に、テナントごとの新しいフィールド `editorTeam` を1つ追加します。値は `"<GitHub organization>/<team slug>"` の形で、1つの GitHub Team を指します。

```yaml
orgs:
  acme:
    githubOrgs: [acme-gh]
    editorTeam: acme-gh/scenario-maintainers
```

ログイン時、login は前項の Organization メンバーシップの確認を通過します。その後、serve は GitHub の `GET /user/teams` を呼びます。この呼び出しは、OAuth フローがすでに要求している `read:org` スコープで読めます。serve は、応答に設定した org と Team の組が**直接の**メンバーシップとして含まれているかを確認します。一致すれば、その login は解決したテナントの中で editor に昇格します。一致しなければ、viewer のままです。

`/user/teams` は、子（ネストした）Team へのメンバーシップを、親とは別のメンバーシップとして返します。この項目は、設定した Team そのものだけを確認し、その下にネストした Team は確認しません。この判定は、ネストなしのフラットな構造になります。デプロイ側がすでに決めた、ネストした Team の解決を最初の対応から外すという決定と、一致します。

この照会は、org メンバーシップの取得と同じ方法でページングをたどります。`_fetch_orgs` がすでに使っている `Link` ヘッダーのページネーションです（[`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py)）。対象の Team が、多数の Team を含む一覧の後半のページにあっても、判定は正しく解決します。

呼び出しが失敗した場合は、実際に一致しなかった場合と同じ扱いにします。失敗には、非200応答、ネットワーク障害、パースできない本文のいずれもが含まれます。login は viewer として解決し、サインイン自体の失敗にはしません。

この失敗の方向は、`_fetch_orgs` の org メンバーシップ取得とは逆です。`_fetch_orgs` は、取得に失敗すると `default` テナントへ fail open します。取得の失敗が影響するのは、login が「どのテナントに属すか」だけだからです。Team の取得では、失敗が書き込み権限を誤って与える恐れがあります。そのため、権限の低いほうの結果へ fail closed します。

### admin はサーバー全体で1つの階層のまま

`_ADMIN_PATHS` と、admin を要求する `GET` エンドポイントは、今日インスタンス全体に対して強制されています（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）。`role_for`、`user_role`、`forbidden_for_role` は、どれも org を引数に取りません。1人の user が持つ admin ロールは、その user が属するテナントだけでなく、すべてのテナントの config、secrets、provider 設定に届きます。

テナントごとに独自の admin Team を持たせると、そのテナントの admin に、他のすべてのテナントに対するインスタンス全体の権限を、意図せず与えてしまいます。ロール判定より下流の処理が、その権限を1つのテナントに絞り込んでいないためです。

この項目は、その強制の範囲を変えません。したがって admin は、サーバー全体で共通の1つの階層のままとします。1つの Team が、`BAJUTSU_OAUTH_ADMINS` の login リストを置き換えます。デプロイ全体で1つだけ名付けた Team を、`BAJUTSU_OAUTH_ADMIN_TEAM` として持たせます。形は `editorTeam` と同じ `"<GitHub organization>/<team slug>"` で、確認方法も同じです。

admin 自体をテナントごとに絞り、その上にテナントを跨ぐ操作のためのさらに上位の階層を新設することは、これよりずっと大きな変更です。この項目には折り込まず、将来の別の提案に委ねます（「検討した代替案」を参照してください）。

admin Team の確認は、login が Organization メンバーシップのゲートを通過した後にだけ走ります。この順序は、「書き込み権限は1つのフラットな GitHub Team のメンバーシップに従う」が `editorTeam` について述べているのと同じです。

そのため、`BAJUTSU_OAUTH_ADMIN_TEAM` のメンバーであっても、注意が必要です。そのメンバーの GitHub Organization が、どのテナントの `githubOrgs` や `members` からも参照されていないとします。例えば、どの `orgs:` エントリにも載らない運用専用の GitHub Organization にいる場合です。この場合、admin Team が参照される前に、サインインそのもので拒否されます。admin になるはずが、サインインを失います。

この節は、admin Team をテナントと独立に名付けています。この名付け方が実際に機能するのは、その admin が設定済みのテナントのメンバーである場合です。所属は、`members` か `githubOrgs` のいずれかを通じます。この項目は、Organization メンバーシップのゲートを admin だけ迂回する経路を追加しません。この重なりを確保するのは、デプロイ側の責任です。

### 失権は次回ログインで反映される、これまでと変わらない

BE-0015 §7c-2 は、ログインごとにロールを方針から再計算する仕組みをすでに備えています。方針を変更しても、データマイグレーションは要りません。この項目は、その原則をそのまま保ちます。GitHub の Organization や Team から外れた効果は、次にその user がログインしたときに反映されます。今日 `BAJUTSU_OAUTH_ADMINS` から login を外したときも、同じ挙動です。新しい失権の仕組みは必要ありません。

### admin 限定の `GET` の例外は影響を受けない

`required_role()`（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）は、3つの読み取りに、すでに admin を要求しています。この3つは、パスが示す以上の情報を返す読み取りです。具体的には、`GET /api/config/content`、`GET /api/artifacts/exists`、`GET /api/version/checkout` です。この要求は、viewer や editor のロールがどう付与されたかに関わりません。

viewer に該当する範囲を広げても、これらの判定には影響しません。判定が確認するのは、解決したロールが固定の「admin」という要求を満たすかどうかです。そのロールがどの Organization や Team から来たかは、判定に関わりません。

### 共有トークンは OAuth を設定した場合に worker 向けの通信へ絞られる

`BAJUTSU_SERVE_TOKEN` には、今日互いに独立した3つの役割があります。2つではありません。

- worker が bearer token として使う認証。対象は、5つの `/api/worker/*` ルートです（`lease`、`heartbeat`、`result`、`artifact-urls`、`scenario-url`、[`bajutsu/serve/routes.py`](../../bajutsu/serve/routes.py)）。この認証は人間とは無関係で、この項目のどちらの場合にも影響を受けません。
- 人間のブラウザがトークンをセッション Cookie に交換できる経路。交換は `POST /api/login`（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py) の `login()`）を通じて行います。この経路は、GitHub OAuth と、その上に立つ RBAC を迂回します。
- 人間か自動化かを問わず、どのクライアントでも使える経路。トークンをそのまま `Authorization: Bearer` ヘッダーとして渡せば、**あらゆる**エンドポイントにアクセスできます。

3つ目の経路は、次のように働きます。`gate.is_authorized`（[`bajutsu/serve/gate.py`](../../bajutsu/serve/gate.py)）が、serve の2つの HTTP バックエンドのどちらでも、ヘッダーを受け入れます。

- stdlib ハンドラの `_gate()`（[`bajutsu/serve/handler.py`](../../bajutsu/serve/handler.py)）。
- FastAPI アプリの `_security_gate`（[`bajutsu/serve/server/app.py`](../../bajutsu/serve/server/app.py)）。

どちらのバックエンドも `is_authorized` を呼び、このリクエストについて `forbidden_for_role` の判定をスキップします。Bearer で認証されたリクエストには、ロールを確認すべき identity がないためです。これは、BE-0015 §7b が OAuth 導入時に定めた「CI などのための operator credential としてフルアクセスを許す」経路と同じものです。

3つ目の経路は、3つのうちもっとも広い経路です。ログインフォームだけでなく、RBAC で守られたあらゆるエンドポイントに届きます。Cookie 経由の経路だけを廃止しても、より大きなバイパスは手つかずのまま残ります。

この項目は、Cookie 経由の経路と、直接の Bearer 経路の両方を、OAuth を設定した場合に廃止します。OAuth の設定とは、`BAJUTSU_OAUTH_GITHUB_CLIENT_ID`、`_CLIENT_SECRET`、`_REDIRECT_URI` の3つの環境変数をすべて設定することです。変更は2つの部分からなります。

- `gate.is_authorized` の Bearer トークン判定は、両方のバックエンドで、`/api/worker/*` のルートに対してだけ有効になります。
- 人間は、`/api/oauth/login` だけでサインインします。

今日、トークンが持つ「operator credential」としての到達範囲を使っているデプロイもあります。worker 以外のエンドポイントへのスクリプトや CI アクセスです。そのデプロイは、OAuth を設定すると、このアクセス手段を失います。この項目は、その用途の代わりとなる仕組みを設計しません。worker 向けトークンとも人間の OAuth セッションとも異なる identity、つまりサービスアカウントや個人アクセストークンのようなものは、ずっと大きな追加です。将来の別提案に委ねます。

OAuth を設定していないデプロイでは、トークンが持つ3つの役割はどれも変わりません。BE-0051 は、このデプロイ、つまり single-Mac でプライベートネットワーク向けのデプロイを想定して、共有トークンを設計しました。このデプロイでは、RBAC 自体がそもそも適用されません。データベースを配線していないため、`state.repository is None` のときは常に `forbidden_for_role` が `False` を返します。このデプロイが持つサインインや自動化の経路のどれかを狭めれば、今より持ち物が減ってしまいます。

## 検討した代替案

- **identity-aware proxy（Identity Aware Proxy、IAP）を serve の手前に置く方法**。採用しません。Bajutsu にはすでに独自の GitHub OAuth の仕組み（BE-0015）があり、デプロイのユーザーはすでに GitHub でサインインしています。IAP を追加すると、すでにある仕組みを再利用せず、二重で冗長な identity の仕組みを持つことになります。
- **テナントごとの admin Team と、テナントを跨ぐ操作のための、さらに上位の階層を新設する方法**。前述のテナントごとの editor Team に対応する、admin 版の設計として検討しました。この項目には採用しません。`_ADMIN_PATHS` と admin 限定の読み取りは、今日インスタンス全体に対して強制されています。admin を1つのテナントに絞るには、まずその強制自体をテナントを意識したものに変える必要があります。この変更は、ロールの読み出し元を login リストから GitHub のメンバーシップへ差し替えることとは別の軸にあり、しかもより大きな変更です。将来の別提案に委ねます。
- **`editorTeam` を Bajutsu テナントではなく project（BE-0225）に付ける方法**。1つの GitHub Organization の下にある複数のリポジトリで、異なる書き込み担当者を持たせたいデプロイを想定して検討しました。この項目には採用しません。project は、今日アクセス制御を何も持ちません。`ProjectRecord` に該当フィールドがなく、`required_role` も project を見分けません。この方法を採用すると、BE-0225 が config ソースへのバインディングとして定義した概念に、新しいアクセス制御の軸を持ち込むことになります。この変更は、この項目がすでに Bajutsu テナントに対して行うロールの読み出し元の差し替えより、ずっと大きな変更です。リポジトリごとに異なる書き込み担当者を持たせたいデプロイは、すでに実現できます。複数の Bajutsu テナントを、それぞれ異なる `editorTeam` で宣言すれば、project 単位のアクセス制御を新設せずに済みます。
- **editor Team と admin Team に、ネストした Team のメンバーシップも認める方法**。GitHub の Team は、親子関係を持てます。`/user/teams` は、子 Team へのメンバーシップを親とは別に返します。この項目の最初の対応では、設定した Team そのものだけを確認する方法を採用します。デプロイ側がすでに決めたフラットな構造に一致するためです。ネストしたメンバーシップの解決は、デプロイの Team 階層がそれを必要とする段になれば、後から追加できます。
- **共有トークンを人間のサインイン手段から無条件に廃止する方法**。採用しません。BE-0051 は、single-Mac でプライベートネットワーク向けのデプロイを想定して、共有トークンを設計しました。このデプロイには、登録する OAuth app も、強制する RBAC もありません。データベースを配線していないためです。そこでトークンを廃止すれば、このデプロイが持つ唯一のサインイン手段を、代わりなしに取り除いてしまいます。
- **新しい Organization と Team の確認と並行して、既存のログインリストの許可リストを退避路として残す方法**。採用しません。もう1つの独立した付与経路を残すことは、GitHub 自身のメンバーシップの記録からロールを導くという、この項目の狙いを損ないます。この項目が取り除こうとしている名簿のずれの問題を、全員ではなく、退避路を通る一部の user のために作り直すことになります。
- **OAuth を設定した後も、worker 以外のエンドポイントに対するトークンの直接の `Authorization: Bearer` 経路をそのまま残す方法**。採用しません。この項目の前提は、OAuth を設定したら GitHub の Organization と Team のメンバーシップが権限を決める、というものです。生のトークンを渡すだけであらゆるエンドポイントに届き、ロールの確認を一切経ないクライアントが残れば、この前提は成り立ちません。`editorTeam` や `BAJUTSU_OAUTH_ADMIN_TEAM` をどれだけ注意深く設定しても、結果は変わりません。
- **セッションの Time To Live（TTL）を短くして失権までの猶予を縮める方法**。Team から外れた効果を、次回ログインを待たずに反映できるよう検討しました。見送ります。セッションの有効期限は、ロールの読み出し元がどこかとは独立した、セッションストア（BE-0015）が持つ性質です。今日のログインリストによる失権も、この項目が保つのと同じく、次回ログインを待っています。将来の別項目で、すべての失権経路に対してまとめてこの猶予を短くすることは考えられます。この項目だけでそれを行うことはしません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE
> （Mutually Exclusive Collectively Exhaustive）な作業分解（作業の単位ごとに1つ）に対応し、ログ
> には変更内容と時期（古い順）を PR へのリンクとともに記録します。

- [x] `OrgConfig` に `editorTeam` フィールドと、その config スキーマの説明を追加する。
- [x] `GET /user/teams` の照会（直接のメンバーシップのみ）と、環境変数 `BAJUTSU_OAUTH_ADMIN_TEAM` を
      追加する。
- [x] サインインの判定が呼ぶ、単純な一致するかどうかのチェックを追加する（`org_for_identity` が
      すでに行っている `members` や `githubOrgs` の判定と同じもの）。`org_for_identity` の `str`
      という戻り値だけでは、「何にも一致しない」ことと「正当に `default` へ解決した」ことを区別
      できません。拒否は `oauth_callback` の先頭（`if state.repository is not None:` ブロックより
      前）で行う。これにより、OAuth を設定していてもデータベースを配線していないデプロイで、
      サインインが素通りしないようにする。あわせて `BAJUTSU_OAUTH_ALLOWED_USERS` と
      `BAJUTSU_OAUTH_VIEWERS` を廃止する。
- [x] `role_for()` のログインリストによる判定を、上記の Organization と Team による判定に置き換え、
      `BAJUTSU_OAUTH_ADMINS` を廃止する。
- [x] トークンによる `POST /api/login` の Cookie 経路を、そのデプロイで「OAuth を設定していない」
      場合に限定する。
- [x] OAuth を設定した場合に、`gate.is_authorized` の Bearer トークン判定を `/api/worker/*` の
      ルートに絞る。対象ファイルは `bajutsu/serve/gate.py`。stdlib ハンドラと FastAPI アプリ、
      両方の呼び出し箇所を揃える。Cookie ログインの廃止と合わせて、他のすべてのエンドポイントに
      対する直接の Bearer RBAC バイパスを
      閉じる。
- [x] 設定とセルフホスティングのドキュメント、および BE-0015、BE-0016 からの相互参照（両言語）を
      更新する。ログインリストに代わる、Organization と Team に基づく RBAC の説明にする。
- [x] テストを追加する。1つ目は、Organization メンバーシップによるサインインの許可判定です。許可と
      拒否の両方を確認します。2つ目は、偽装した `Teams API` に対する editor Team と admin Team の
      判定です。ページネーションされた Team 一覧の場合と、照会が失敗して viewer に解決する場合を
      含みます。3つ目は、admin 限定の `GET` の例外が admin のままであることの確認です。4つ目は、
      OAuth の設定状況によって切り替わるトークンサインインの判定です。5つ目は、直接の Bearer
      トークン経路が worker 以外のエンドポイントで拒否されることの確認です。両方の `HTTP` バック
      エンドで確認します。5つすべての `/api/worker/*` ルートと、OAuth を設定していないデプロイ
      では、引き続き機能することも確認します。

## 参考

- [BE-0015 — Web UI のパブリックホスティング](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)
  の §7b〜7c と §8。この項目が置き換える GitHub OAuth サインインとログインリストによる RBAC、そして
  この項目が拡張する `orgs:` マルチテナントモデルを扱います。
- [BE-0225 — serve の config プロジェクトハブ（登録・一覧・切替・実行）](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)。
  「用語」の節が GitHub Organization と Bajutsu テナントから区別する project という概念です。
- [BE-0016 — Web UI のセルフホスティング](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)。
  この項目が変更しない、OAuth なしのデプロイ形態です。
- [BE-0051 — ホスティングに向けた serve の堅牢化](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)。
  この項目が、OAuth を設定した場合に worker 向けの通信へ絞る共有トークンです。
- [`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py)。`Identity` と org メンバー
  シップの取得を担う GitHub OAuth クライアントであり、この項目は Team メンバーシップの取得も加えて
  拡張します。
- [`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)。RBAC のロール判定と admin 限定の `GET`
  の例外であり、この項目がロールの読み出し元を変更します。
- [`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)。org 設定モデルであり、この項目が
  `editorTeam` を追加して拡張します。
- [`bajutsu/serve/gate.py`](../../bajutsu/serve/gate.py)。`is_authorized` の Bearer トークン判定で
  あり、OAuth を設定した場合に `/api/worker/*` のルートへ絞ります。
- [`bajutsu/serve/handler.py`](../../bajutsu/serve/handler.py)。`_gate()` です。Bearer で認証された
  リクエストに対して、`forbidden_for_role` の判定をスキップします（ロールを確認すべき identity が
  ないためです）。2つある呼び出し箇所の1つです。
- [`bajutsu/serve/server/app.py`](../../bajutsu/serve/server/app.py)。`_security_gate` です。FastAPI
  バックエンドの `is_authorized` 呼び出し箇所であり、`gate.py` が両バックエンドで揃えるよう求める、
  もう1つの箇所です。
- `GitHub REST API` の
  [List teams for the authenticated user](https://docs.github.com/en/rest/teams/teams#list-teams-for-the-authenticated-user)
  （`GET /user/teams`）。

[English](BE-0313-github-org-team-rbac.md) · **日本語**

# BE-0313 — GitHub Organization メンバーシップと Team ベースの RBAC を serve に導入

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0313](BE-0313-github-org-team-rbac-ja.md) |
| 提案者 | [@paihu](https://github.com/paihu) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0313") |
| トピック | Web UI のホスティング |
| 関連 | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md), [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md), [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md) |
<!-- /BE-METADATA -->

## はじめに

この項目は、serve のロールベースアクセス制御（RBAC）が持つ2つの仕組みを、GitHub の Organization と
Team のメンバーシップに置き換えます。置き換える対象は、サインインを許可する GitHub login の許可
リストと、admin・editor・viewer のロールを割り当てるログインリスト（BE-0015 §7b〜7c）です。サイン
インには、設定した GitHub Organization へのメンバーシップを必須とします。これによって viewer ロール
を付与します。1つのフラットな GitHub Team へのメンバーシップは、editor への昇格をもたらします。
それとは別に、サーバー全体で共通の1つの Team へのメンバーシップは admin を付与します。さらにこの
項目は、共有トークン（`BAJUTSU_SERVE_TOKEN`、BE-0051）を、OAuth を設定した場合には worker と serve
の間の通信専用に絞ります。人間による Cookie サインインと、他のエンドポイントへの直接の Bearer
認証を、どちらも認可の手段としては廃止します。

## 動機

serve の現在の RBAC（BE-0015 §7c）は、環境変数から3つの GitHub login リストを読みます。
`BAJUTSU_OAUTH_ALLOWED_USERS` は、サインインそのものを許可します。`BAJUTSU_OAUTH_ADMINS` と
`BAJUTSU_OAUTH_VIEWERS` は、admin と viewer のロールを割り当てます。許可リストに載った残り全員は、
デフォルトで editor になります。それぞれのリストは、デプロイの運用者が手で保守する login の名簿
です。この名簿は、GitHub がすでに持っている名簿、つまり Organization のメンバー一覧と Team を重複
させています。社員が入社し、退職し、異動するたびに環境変数を編集して再デプロイしなければ、サーバー
側のリストは実態と一致しなくなります。

Bajutsu が想定するデプロイ先は、すでに GitHub 型の構造に沿っています。そこには GitHub でサインイン
するエンジニアリング組織があり、その一部のメンバーが scenario を書き、保守します。serve のロールを
この構造から導けば、重複した名簿は不要になります。閲覧権限は、Organization のメンバーシップに従い
ます。書き込み権限は、その組織がすでに scenario の保守担当者をまとめるために使っている Team の
メンバーシップに従います。BE-0015 §7c-2 は、ログインごとにロールを方針から再計算する仕組みをすでに
備えています。この仕組みにより、方針を変更してもデータマイグレーションなしで反映されます。この項目
はその原則をそのまま保ち、方針の読み出し元だけを変えます。読み出し元は、固定の環境変数リストでは
なく、GitHub 自身が持つメンバーシップの記録です。

共有トークンをサインイン手段から外すことは、これとは別のもう1つの隙間を塞ぎます。ただし、この隙間
はブラウザのサインインより広いものです。OAuth と共有トークンの両方を設定したデプロイでは、今日、
ブラウザが `POST /api/login` を通じて共有トークンでサインインできます。この経路は、OAuth と、その
上に立つ RBAC を丸ごと迂回します。これとは別に、どのクライアントでも同じトークンをそのまま
`Authorization: Bearer` ヘッダーとして渡せば、任意のエンドポイントにアクセスできます。
`gate.is_authorized` は、ロールを確認すべき identity がないまま、これを許可します。この Bearer 直通
の経路は、BE-0015 §7b が定めた「operator credential としてフルアクセスを許す」経路です。GitHub の Organization と
Team のメンバーシップが権限を決めるようになったら、閉じるべきなのはブラウザの経路だけではありません。
両方を閉じなければ、ロールの強制にはトークン1つ分の穴が残ります。その穴は、サインインフォームから
任意の `HTTP` クライアントへ場所を移しただけです。

## 詳細設計

### 用語：GitHub Organization、Bajutsu テナント、project

この項目は、GitHub 側の identity の出どころを「GitHub Organization」と呼びます。Bajutsu 自身のマルチ
テナントの単位は「Bajutsu テナント」と呼びます。Bajutsu テナントは `orgs:` の1エントリであり、コード
上では「org（tenant）」と呼ばれています。この2つの語は、どちらも「org」という語を共有していますが、
指すものは別です。このため以下の設計では、どちらを指しているかをその都度明示します。

Bajutsu テナントの下には、もう1段細かい単位があります。**project**
（[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)）は、1つのテナントの下に
登録される config ソースへのバインディングです。多くの場合、1つの project は1つのリポジトリに対応
します。1つのテナントは、複数の project を持てます（`ProjectRecord.org_id`、1対多）。project 自体
は、今日ロールベースアクセス制御（RBAC）を持ちません。どのリクエストも、actor が属する Bajutsu
テナントだけからロールを解決します。1つのテナントの中でアクティブな project は、常に1つだけです。
その切り替え自体は、admin 限定です（`activate_project`、
[`bajutsu/serve/operations/projects.py`](../../bajutsu/serve/operations/projects.py)）。

この項目は、後述する `editorTeam` を project ではなく Bajutsu テナントに付けます。1つのテナント配下の
すべての project が同じ保守担当者の集合を共有するデプロイには、これがそのまま当てはまります。逆に、
リポジトリごとに異なる書き込み担当者を持たせたいデプロイもあります。例えば、モバイル担当 Team が
1つのリポジトリに、Web 担当 Team が別のリポジトリに書き込み、両方が同じ GitHub Organization の配下
にあるケースです。このようなデプロイは、project 単位のアクセス制御を新設しなくても実現できます。
その GitHub Organization に一致する（あるいは `githubOrgs` が重なる）複数の Bajutsu テナントを宣言
します。それぞれに異なる `editorTeam` を設定するだけで済みます。これは今日すでに実現できる方法です。
詳しくは「検討した代替案」を参照してください。

### 閲覧権限は GitHub Organization のメンバーシップに従う

`orgs:` ブロックは、login を Bajutsu の org に対応づけています（`org_for_identity`、
[`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)）。対応づけの方法は、明示的な `members` の
列挙、または `githubOrgs` リストと login が属する GitHub Organization との積集合です。今日この対応
づけが決めているのは、すでに許可リストを通過した login が「どの org に属すか」だけです。どの org に
も一致しない login は、締め出されるのではなく、単一の `default` org に落ち着きます。サインインその
ものを許可するかどうかは、別の `BAJUTSU_OAUTH_ALLOWED_USERS` 許可リストが決めているからです。

この項目は、この判断を同じ対応づけに折り込みます。サインインが成功する login は、次のどちらかです。
1つは、`orgs:` ブロックの `members` に明示的に列挙されている login です。もう1つは、`githubOrgs` に
挙げた GitHub Organization のメンバーである login です。それ以外の login は、`BAJUTSU_OAUTH_ALLOWED_USERS`
が今日返しているのと同じ「許可されていない user」という応答で拒否します。サインインに成功した login
には、最低でも viewer ロールを付与します。`BAJUTSU_OAUTH_ALLOWED_USERS` と `BAJUTSU_OAUTH_VIEWERS`
は廃止します。別のリストではなく、Organization 自身の名簿が許可リストになるからです。

「最低でも」という表現は、単なる言い回しではありません。今日の editor すべてにとって実質的な
降格です。`role_for`（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）は、許可リストに
載った login を今日デフォルトで editor にします。例外は、`BAJUTSU_OAUTH_VIEWERS` に明示されて
いる場合だけです。つまり、scenario の書き手は今日、デフォルトで editor です。この項目は、この
基底ロールを viewer へ反転させます。今日 editor である user は、`editorTeam` へあらかじめ
登録されない限り、次回ログインで書き込み権限を失います。これは、上で述べた `orgs:` ブロックの
宣言と同種の、採用時の対応です。

`org_for_identity` は、この拒否を単独では伝えられません。`str` を返す関数です。正当に `default`
へ解決する login と、一致しない login は、同じ `DEFAULT_ORG` の値になります
（[`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)）。そのため判定には、それ自身の一致
チェックが必要です。`org_for_identity` がすでに行っている `members` や `githubOrgs` の判定を、
一致するかどうかという単純な形で取り出したものです。このチェックは、その login に対して
`org_for_identity` を呼ぶ前に走らせます。判定が受け入れる login は、必ず何かに一致しています。
そのため `org_for_identity` は、その login に対して `default` へ落ちることがありません。判定が
拒否する login は、そもそも `org_for_identity` に届きません。ここから、1つの帰結が直接導かれます。
`orgs:` ブロックを持つどのデプロイでも、`default` org（今日は一致しない login の受け皿です）は、
OAuth サインインでは到達不能になります。そこへ落ちるはずだった login は、配置が計算される前に
拒否されるからです。

`members` だけのテナント（`orgs:` ブロックに `members` はあるが `githubOrgs` がない場合）は、この
`members` の列挙で初めてサインインを制御します。今日の `members` は、すでに許可された login がどの
org に属すかを決めるだけです。サインインが成功するかどうかは、`BAJUTSU_OAUTH_ALLOWED_USERS` だけが
決めています。`orgs:` ブロック自体を持たないデプロイには、頼れる `members` の列挙がありません。その
ため、`BAJUTSU_OAUTH_ALLOWED_USERS` を廃止すれば、すべての login が拒否されます。この項目を採用する
デプロイは、`orgs:` ブロックを宣言しない限り、サインインの手段を失います。ブロックの中身は、
`members` の列挙か `githubOrgs` のエントリです。

この判定は、無条件に動く必要があります。`_org_for_login` が今いる
`if state.repository is not None:` ブロックの中だけでは足りません
（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）。今日、`BAJUTSU_OAUTH_ALLOWED_USERS`
による拒否は `oauth_callback` の先頭、このブロックより前にあります。そのため、OAuth を設定していても
データベースを配線していないデプロイでも、サインインを制御できています。このデプロイは、
`state.repository` の有無にかかわらず、`oauth_callback` の末尾で identity にセッションを発行する
からです。新しい org/Team の判定を、そのまま `_org_for_login` の中に折り込むとします。
`BAJUTSU_OAUTH_ALLOWED_USERS` を廃止した時点で、このデプロイが持つ唯一の判定が消えます。すべての
GitHub user が通ってしまいます。そのため拒否は、同じ先頭の位置のまま行います。前段の一致チェックを
計算し、許可か拒否かを決めるのは、データベースのブロックより前です。ブロックの中にある
`_org_for_login` の呼び出しは、すでに受け入れた login について、どの org に永続化するかだけを
決めます。拒否するかどうかは決めません。

これにより、サインインそのものが GitHub の `/user/orgs` への呼び出しの成功に依存します。対象は、
`members` に明示登録されていない login です（`_fetch_orgs`、
[`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py)）。今日はこの依存がありません。
サインインの可否を決めるのは `BAJUTSU_OAUTH_ALLOWED_USERS` だけであり、GitHub の `API` 呼び出しに
成功する必要がないからです。`_fetch_orgs` は、失敗する場合（非200応答や本文のパース失敗など）でも、
すでに空の org 一覧へ fail open します。今日この失敗が送るのは `default` org だけです。この項目の
もとでは、同じ失敗によって、障害が続くあいだ、`githubOrgs` だけに頼る login のサインインそのものが
失敗し続けます。本物のネットワーク障害は、これとは別の既存のケースです。`_fetch_orgs` の中では
捕捉されず、`fetch_identity` を経て `oauth_callback` 自身の例外処理まで伝播します。そこでは今日、
すべての login に対してすでに交換全体が 502 で失敗しており、この項目の影響を受けません。この項目
は、この新しい非200応答やパースエラーのトレードオフを受け入れます。org
メンバーシップ取得にリトライやキャッシュの仕組みを足すことはしません。`members` に明示登録された
login も、影響を受けません。`fetch_identity` は、どの login でも `/user/orgs` を呼びます。ただし
`org_for_identity` は、明示的な `members` の一致を見つけると、取得した一覧を見る前に値を返します。
`githubOrgs` だけに頼る login は、
GitHub の `API` へ再び到達できれば、もう一度サインインするだけで済みます。

### 書き込み権限は1つのフラットな GitHub Team のメンバーシップに従う

`OrgConfig`（[`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)）に、org ごとの新しいフィールド
`editorTeam` を1つ追加します。値は `"<GitHub organization>/<team slug>"` の形で、1つの GitHub Team
を指します。

```yaml
orgs:
  acme:
    githubOrgs: [acme-gh]
    editorTeam: acme-gh/scenario-maintainers
```

ログイン時、login は前項の Organization メンバーシップの確認を通過します。その後、serve は GitHub
の `GET /user/teams` を呼びます。この呼び出しは、OAuth フローがすでに要求している `read:org`
スコープでそのまま読めます。そして、応答に設定した org と Team の組が**直接の**メンバーシップとして
含まれているかを確認します。一致すれば、その login は解決した org の中で editor に昇格します。一致
しなければ、viewer のままです。`/user/teams` は、親子関係を持つ Team のうち子（ネストした）Team へ
のメンバーシップを、親とは別のメンバーシップとして返します。この項目は、設定した Team そのものだけ
を確認し、その下にネストした Team を確認しません。これにより、この判定はネストなしのフラットな構造
になります。この構造は、デプロイ側がすでに決めたフラットな構造という決定と一致します。

この照会は、org メンバーシップの取得と同じ方法でページングをたどります。その方法とは、`_fetch_orgs`
がすでに使っている `Link` ヘッダーのページネーションです
（[`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py)）。そのため、対象の Team が
多数の Team を含む一覧の後半のページにあっても、判定はまさしく解決します。
呼び出しが失敗した場合、実際に一致しなかった場合と同じ扱いにします。失敗には、非200応答、ネット
ワーク障害、パースできない本文のいずれもが含まれます。login は viewer として解決し、サインイン
自体の失敗にはしません。これは、`_fetch_orgs` の org メンバーシップ取得とは逆の失敗の方向です。
`_fetch_orgs` は、取得に失敗すると `default` org へ fail open します。取得の失敗が影響するのは、
login が「どの org に属すか」だけだからです。ここでは、取得の失敗が書き込み権限を誤って与えてしまう
恐れがあります。そのため、代わりに権限の低いほうの結果へ fail closed します。

### admin はサーバー全体で1つの階層のまま

`_ADMIN_PATHS` と、admin を要求する `GET` エンドポイント
（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）は、今日インスタンス全体に対して強制され
ています。`role_for`、`user_role`、`forbidden_for_role` は、どれも org を引数に取りません。そのため、
1人の user が持つ admin ロールは、その user が属する org だけでなく、すべての org に届きます。その
届く範囲は、config、secrets、provider 設定です。org ごとに独自の admin Team を持たせると、その org
の admin に、他のすべての org に対するインスタンス全体の権限を意図せず与えてしまいます。ロール判定
より下流の処理が、その権限を1つの org に絞り込んでいないためです。

この項目は、その強制の範囲を変えません。したがって admin は、サーバー全体で共通の1つの階層のまま
とします。デプロイ全体で1つだけ名付けた Team を `BAJUTSU_OAUTH_ADMIN_TEAM` として持たせ、
`BAJUTSU_OAUTH_ADMINS` の login リストを置き換えます。`BAJUTSU_OAUTH_ADMIN_TEAM` は、`editorTeam`
と同じ `"<GitHub organization>/<team slug>"` の形であり、同じ方法で確認します。admin 自体を org
ごとに絞り、その上に org を跨ぐ操作のためのさらに上位の階層を新設することは、これよりずっと大きな
変更です。この項目には折り込まず、将来の別の提案に委ねます（「検討した代替案」を参照してください）。

admin Team の確認は、login が Organization メンバーシップのゲートを通過した後にのみ走ります。
これは「書き込み権限は…」が `editorTeam` について述べているのと同じ順序です。そのため、
`BAJUTSU_OAUTH_ADMIN_TEAM` のメンバーであっても注意が必要です。その GitHub Organization が、
どのテナントの `githubOrgs`/`members` からも参照されていないとします。例えば、どの `orgs:`
エントリにも載らない運用専用の GitHub Organization にいる場合です。この場合、admin Team が
参照される前に、サインインそのもので拒否されます。admin になるはずが、サインインを失います。
この節が述べるように、admin Team をテナント org と独立に名付けても、実際にはその admin が
設定済みのテナント org のメンバーである場合にしか機能しません。その所属は、`members` か
`githubOrgs` のいずれかを通じます。この項目は、Organization メンバーシップのゲートを admin
だけ迂回する経路を追加しません。そのため、この重なりを確保するのはデプロイ側の責任です。

### 失権は次回ログインで反映される、これまでと変わらない

BE-0015 §7c-2 は、方針を変更してもデータマイグレーションが要らないように、ログインごとにロールを
方針から再計算する仕組みをすでに備えています。この項目はその原則をそのまま保ちます。GitHub の
Organization や Team から外れた効果は、次にその user がログインしたときに反映されます。これは、
今日 `BAJUTSU_OAUTH_ADMINS` から login を外したときと同じ挙動です。新しい失権の仕組みは必要
ありません。

### admin 限定の `GET` の例外は影響を受けない

`required_role()`（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）は、3つの読み取りに
すでに admin を要求しています。この3つは、パスが示す以上の情報を返す読み取りです。具体的には、
`GET /api/config/content`、`GET /api/artifacts/exists`、`GET /api/version/checkout` です。この要求
は、viewer や editor のロールがどう付与されたかに関わりません。viewer に該当する範囲を広げても、
これらの判定には影響しません。判定が確認するのは、解決したロールが固定の「admin」という要求を満たす
かどうかだけです。そのロールがどの Organization や Team から来たかは、判定に関わりません。

### 共有トークンは OAuth を設定した場合に worker 向けの通信へ絞られる

`BAJUTSU_SERVE_TOKEN` には、今日互いに独立した3つの役割があります。2つではありません。1つは、
worker が bearer token として使う認証です。対象は、5つの `/api/worker/*` ルートです（一覧は
[`bajutsu/serve/routes.py`](../../bajutsu/serve/routes.py) を参照してください）。この認証は、人間
とは無関係であり、この項目のどちらの場合にも影響を受けません。もう1つは、人間のブラウザがこの
トークンをセッション Cookie に交換できる経路です。この交換は、
`POST /api/login`（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py) の `login()`）を通じて
行います。この経路は、GitHub OAuth と、その上に立つ RBAC を丸ごと迂回します。3つ目は、人間か
自動化かを問わず、どのクライアントでも使える経路です。このトークンをそのまま
`Authorization: Bearer` ヘッダーとして渡せば、あらゆるエンドポイントにアクセスできます。
`gate.is_authorized`（[`bajutsu/serve/gate.py`](../../bajutsu/serve/gate.py)）は、serve の2つの
`HTTP` バックエンドのどちらでもこれを受け入れます。stdlib ハンドラの
`_gate()`（[`bajutsu/serve/handler.py`](../../bajutsu/serve/handler.py)）が呼びます。FastAPI アプリの
`_security_gate`（[`bajutsu/serve/server/app.py`](../../bajutsu/serve/server/app.py)）も呼びます。
どちらも、この場合 `forbidden_for_role` の判定をまるごとスキップします。ロールを確認
すべき identity がないためです。これは、BE-0015 §7b が OAuth 導入時に定めた「operator credential
としてフルアクセスを許す」経路と同じものです。3つ目の経路は、3つのうちもっとも広いものです。
ログインフォームだけでなく、RBAC で守られたあらゆるエンドポイントに届くためです。したがって、
Cookie 経由の経路だけを廃止しても、より大きなバイパスは手つかずのまま残ります。

この項目は、Cookie 経由の経路と、直接の Bearer 経路の両方を廃止します。廃止するのは、OAuth を設定
した場合に限ります。OAuth の設定とは、`BAJUTSU_OAUTH_GITHUB_CLIENT_ID`、`_CLIENT_SECRET`、
`_REDIRECT_URI` の3つの環境変数をすべて設定することです。この場合、`gate.is_authorized` の Bearer
トークン判定は、`/api/worker/*` のルートに対してのみ、両方のバックエンドで有効になります。人間は
`/api/oauth/login` だけでサインインします。今日、トークンが持つ「operator credential」としての
到達範囲を、worker 以外の
エンドポイントへのスクリプトや CI アクセスに使っているデプロイもあります。そのデプロイは、OAuth を
設定すると、このアクセス手段を失います。この項目は、その用途の代わりとなる仕組みを設計しません。
worker 向けトークンとも、人間の OAuth セッションとも異なる identity が必要です。それは、サービス
アカウントや個人アクセストークンのようなものです。これはずっと大きな追加であり、将来の別提案に
委ねます。OAuth を
設定していないデプロイでは、トークンが持つ3つの役割はどれも変わりません。これは、BE-0051 が共有
トークンを想定して設計した、single-Mac でプライベートネットワーク向けのデプロイです。このデプロイで
役割が変わらないのは、RBAC 自体がそもそも適用されないためです。データベースを配線していないため、
RBAC は適用されません（`state.repository is None` のときは常に `forbidden_for_role` が `False` を
返します）。そのデプロイが持つサインインや自動化の経路のどれかを狭めれば、今より持ち物が減って
しまいます。

## 検討した代替案

- **identity-aware proxy（Identity Aware Proxy、IAP）を serve の手前に置く方法**。採用しません。
  Bajutsu にはすでに独自の GitHub OAuth の仕組み（BE-0015）があり、デプロイのユーザーはすでに
  GitHub でサインインしています。IAP を追加すると、すでにある仕組みを再利用する代わりに、二重で
  冗長な identity の仕組みを持つことになります。
- **org ごとの admin Team と、org を跨ぐ操作のための、さらに上位の階層を新設する方法**。前述の
  org ごとの editor Team に対応する、admin 版の設計として検討しました。この項目には採用しません。
  `_ADMIN_PATHS` と admin 限定の読み取りは、今日インスタンス全体に対して強制されています。admin を
  1つの org に絞るには、まずその強制自体を org を意識したものに変える必要があります。これは、
  ロールの読み出し元を login リストから GitHub のメンバーシップへ差し替えることとは別の軸にある
  変更です。しかもより大きな変更であるため、この項目では将来の別提案に委ねます。
- **`editorTeam` を Bajutsu テナントではなく project（BE-0225）に付ける方法**。1つの GitHub
  Organization の下にある複数のリポジトリで、異なる書き込み担当者を持たせたいデプロイを想定して
  検討しました。この項目には採用しません。project は、今日アクセス制御を何も持ちません。
  `ProjectRecord` に該当フィールドがなく、`required_role` も project を見分けません。この方法を
  採用すると、BE-0225 が config ソースへのバインディングとして定義した概念に、新しいアクセス制御の
  軸を持ち込むことになります。この変更は、この項目がすでに Bajutsu テナントに対して行うロールの
  読み出し元の差し替えより、ずっと大きな変更です。リポジトリごとに異なる書き込み担当者を持たせたい
  デプロイは、すでに実現できます。project 単位のアクセス制御を新設する代わりに、複数の Bajutsu
  テナントを、それぞれ異なる `editorTeam` で宣言すればよいからです。
- **editor Team と admin Team に、ネストした Team のメンバーシップも認める方法**。GitHub の Team
  は親子関係を持てます。`/user/teams` は、子 Team へのメンバーシップを親とは別に返します。この項目
  の最初の対応では、設定した Team そのものだけを確認する方法を採用します。デプロイ側がすでに決めた
  フラットな構造に一致するためです。ネストしたメンバーシップの解決は、後から追加できます。デプロイ
  の Team 階層がそれを必要とする段になれば、追加すればよいからです。
- **共有トークンを人間のサインイン手段から無条件に廃止する方法**。採用しません。BE-0051 が共有
  トークンを想定して設計したデプロイは、single-Mac でプライベートネットワーク向けです。このデプロイ
  には、登録する OAuth app も、強制する RBAC もありません。データベースを配線していないためです。
  そこでトークンを廃止すれば、そのデプロイが持つ唯一のサインイン手段を、代わりなしに取り除いて
  しまいます。
- **新しい Organization と Team の確認と並行して、既存のログインリストの許可リストを退避路として
  残す方法**。採用しません。もう1つの独立した付与経路を残すことは、GitHub 自身のメンバーシップの
  記録からロールを導くという、この項目の狙いを損ないます。この項目が取り除こうとしている名簿の
  ずれの問題を、退避路を通る一部の user のためだけに作り直すことになります。
- **OAuth を設定した後も、worker 以外のエンドポイントに対するトークンの直接の
  `Authorization: Bearer` 経路をそのまま残す方法**。採用しません。この項目の前提は、OAuth を
  設定したら GitHub の Organization と Team のメンバーシップが権限を決める、というものです。生の
  トークンを渡すだけであらゆるエンドポイントに届き、ロールの確認を一切経ないクライアントが残るとします。
  `editorTeam` や `BAJUTSU_OAUTH_ADMIN_TEAM` をどれだけ注意深く設定しても、この前提は成り立ちません。
- **セッションの Time To Live（TTL）を短くして失権までの猶予を縮める方法**。Team から外れた効果
  を、次回ログインを待たずに反映できるよう検討しました。見送ります。セッションの有効期限は、ロール
  の読み出し元がどこかとは独立した、セッションストア（BE-0015）が持つ性質です。今日のログインリスト
  による失権も、この項目が保つのと同じく次回ログインを待っています。将来の別項目で、すべての失権
  経路に対してまとめてこの猶予を短くすることは考えられます。しかし、この項目だけでそれを行うことは
  しません。

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

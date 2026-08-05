[English](BE-XXXX-admin-team-bootstrap-bypass.md) · **日本語**

# BE-XXXX — admin 用 GitHub Team の環境変数が Organization メンバーシップのサインインゲートを回避する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-admin-team-bootstrap-bypass-ja.md) |
| 提案者 | [@paihu](https://github.com/paihu) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | [#1485](https://github.com/bajutsu-e2e/bajutsu/pull/1485) |
| トピック | Web UI のホスティング |
| 関連 | [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) |
<!-- /BE-METADATA -->

## はじめに

この項目は、serve のサーバー全体で共通の admin 用 GitHub Team を、`orgs:` 設定にその admin 自身の
GitHub Organization のエントリがない場合でもサインインできるようにします。`orgs:` 自体が存在しない
場合も含みます。admin Team を指定する環境変数の名前を `BAJUTSU_OAUTH_ADMIN_TEAM` から
`BAJUTSU_OAUTH_ADMIN_TEAMS` に変更します（カンマ区切りのリストとし、1つのデプロイに複数の Team を
指定できるようにします）。そして、この環境変数への一致が、サインインゲートをすでに通過した login の
ロールを決めるだけでなく、そのゲート自体を直接通過させるようにします。

## 動機

[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) は、GitHub OAuth の
サインインを、`orgs:` の下に宣言した Bajutsu テナントへのメンバーシップ（`members` への明示的な
列挙、または `githubOrgs` に挙げた GitHub Organization のメンバー）でゲートします。このゲートを
通過した login だけが、`BAJUTSU_OAUTH_ADMIN_TEAM` との一致を確認され、admin ロールを得ます。BE-0313
自身の設計文書は、この結果として生じる隙間を名指ししています。admin Team のメンバーであっても、その
GitHub Organization がどのテナントの `githubOrgs` や `members` からも参照されていない場合、たとえば
どの `orgs:` エントリにも載らない運用専用の GitHub Organization に属する場合、admin Team が参照される
前にサインインそのもので拒否されます。admin になるはずが、サインインを失います。同じ拒否は、config に
`orgs:` ブロックがまったくない場合、あるいは `members`・`githubOrgs` のエントリがデプロイの運用者を
まだ網羅していない場合にも、すべての login に及びます。`identity_matches_org` は、空のテナント名簿に
対しては誰も通しません。そのため、`orgs:` ブロックがない状態、または運用者がまだ編集を終えていない
状態で起動した GitHub OAuth デプロイは、admin を含む全員を締め出します。

この失敗は、それを直すべき本人に降りかかります。壊れた、あるいは未完成の `orgs:` ブロックは、まさに
admin が存在する理由そのものである種類の設定ミスです。サーバーの向き先を修正済みの config ソースへ
張り替える（`POST /api/config`）、または修正済みのバンドルをアップロードする（`POST /api/upload`）ことで、
admin が直すべき対象です。しかし BE-0313 の設計は、ブロックがすでに壊れた状態になってしまうと、それを
直すためにサインインする経路を残していません。デプロイは、serve 自身の外側で環境変数や config ストアを
手作業で編集されるのを待つしかなく、Web UI から修正できる admin が誰もいない状態のまま残ります。

## 詳細設計

### admin 用環境変数をカンマ区切りのリストにし、その確認をより早い段階に移す

`BAJUTSU_OAUTH_ADMIN_TEAM`（単数形）を `BAJUTSU_OAUTH_ADMIN_TEAMS`（複数形）に改名します。値は
`"<GitHub organization>/<team slug>"` の形で書いた GitHub Team のカンマ区切りリストです。これにより、
1つのデプロイが複数の admin Team を指定できるようになります（例えば、運用対象の GitHub Organization
ごとに1つずつ）。新しい変数を追加せず改名するのは、admin Team の確認方法をこの項目が変えるため
（後述）です。これにより、2つの admin Team 用の環境変数が、サインイン時に異なる挙動を持つ状態を
残しません。改名しなければ、既存の単数形の変数にも同じ挙動の変更を、古い名前のまま加えることになり、
改名と挙動の変更が別々の2回の変更になってしまいます。

`oauth_callback`（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）は、Organization
メンバーシップのゲートを実行する前に、すでに login の GitHub Team メンバーシップを取得しています
（`identity.teams`、`fetch_identity` 経由）。この取得は、後段の `editorTeam` によるロール判定にも
使われているためです。ゲートと Team の取得自体は、これまで一緒に使われるよう順序づけられていな
かったにすぎません。この項目は、`identity_matches_org` と並ぶ確認を1つ追加します。login の
`identity.teams` が、設定した admin Team のリストと重なる場合、`identity_matches_org` の結果に
かかわらずサインインゲートを通過します。これにより、admin Team のメンバーは、自身の GitHub
Organization を挙げる `orgs:` エントリがなくても、あるいは `orgs:` そのものがなくてもサインイン
できます。どちらの確認にも一致しない login は、今日と同じく拒否されます。

### ロール判定の形は変わらず、名前だけが変わる

`role_for`（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）は、すでに単一の admin Team
用パラメータから、admin を editor より上、editor を viewer という基底ロールより上に位置づけています。
この項目は、そのパラメータを単一の Team からリストへ広げ、単一のメンバーシップ判定の代わりに
`identity.teams` との積集合を確認するだけです。上のサインインゲートが、迂回した login が一致する
Team を持つことをすでに確立しているため、`role_for` はその login を、今日の他の admin Team メンバー
と同じ方法で admin に解決します。迂回した場合のための別のロール経路は追加しません。

`role_for` と、後述する org の配置先の決定は、どちらも `oauth_callback` の
`if state.repository is not None:` ブロックの中でだけ動きます。これは、他の admin Team メンバーに
対して今日すでに動いているのと同じ範囲であり、この項目はどちらにも新しい呼び出しを追加しません。
データベースを配線していないデプロイでは、どちらも動きません。その場合、迂回が持つ効果はサインイン
ゲート自体だけです。`state.repository is None` によって `forbidden_for_role` がすでに短絡しているため
（BE-0313）、そのデプロイではサインインした user が誰でもロールを問わずフルアクセスを得ます。迂回した
login が得るのは、そのゲートが他のどの許可済み login にもすでに与えているサインインだけです。

### 迂回した admin の配置先

Organization メンバーシップではなく Team の迂回によってゲートを通過した admin は、`org_for_identity`
がすでに `members`・`githubOrgs` のどちらにも一致しない login を置いている先と同じ、共通の `default`
テナントに配置します。BE-0313 自身の記述は、`orgs:` ブロックを宣言すると `default` テナントは
「OAuth サインインでは到達不能になる」と述べています。そこへ落ちるはずだった login が、配置が計算
される前にすべて拒否されるためです。この項目は、その記述を無条件には成り立たなくします。迂回した
admin は、他のどのテナントにも自分の login が一致しないからこそ `default` に到達します。`default`
自体は、他のどのテナントにも属さない対象以上の特別な扱いを持たず、admin の `_ADMIN_PATHS` による
強制はすでに org を問わずインスタンス全体に及んでいます（BE-0313「admin はサーバー全体で1つの階層
のまま」）。そのため、この配置は、BE-0313 がすでにサーバー全体のものとした admin ロール以上の何かを、
迂回した admin に新たに与えるものではありません。

### Team 取得そのものの失敗の扱いは変わらない

`_fetch_teams`（[`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py)）は、非200
応答、ページネーション途中のネットワーク障害、パースできない本文のいずれに対しても、すでに空の
Team 一覧へ fail closed します。この項目は、GitHub への新しい呼び出しを追加しません。BE-0313 が
すでに取得している同じ `identity.teams` を読むだけです。そのため、GitHub の API 障害が起きている間は、
admin Team のメンバーであってもそのメンバーシップを証明できず、迂回を使えません。その場合、
Organization メンバーシップのゲート単独が与える結果に戻ります（`orgs:` からもその Organization に
到達できなければ拒否です）。この項目は、この API 障害のケースに対する login リストの代替経路を
追加しません。詳しくは「検討した代替案」を参照してください。

## 検討した代替案

- **Team ベースの迂回の代わりに、あるいはそれと並行して、login リスト形式の環境変数
  （`ADMIN_USERS` に相当するもの）を追加する方法**。login リストは、Team ベースの迂回では対処できない
  GitHub Teams API の障害を乗り越えられるため検討しました。この項目には採用しません。BE-0313 は、
  Organization と Team による確認と並行して login リストの許可リストを残すことを、すでに検討して
  退けています。もう1つの独立した付与経路を残すことは、GitHub 自身のメンバーシップの記録から
  ロールを導くという狙いを損ない、ロールベースアクセス制御（RBAC）が取り除こうとしている名簿のずれの
  問題を作り直すことになる、という理由です。その退けた対象は、すべてのロールに開かれた一般的な
  退避路でした。この項目が加える admin 限定の狭い迂回は、別の問題への対処です。対象は、壊れた、
  あるいは存在しない `orgs:` ブロックからの復旧です。admin をあくまで GitHub の Team メンバーシップ
  から導く点は変わらないため、BE-0313 が退けた名簿のずれの問題を作り直すことにはなりません。login
  リスト形式の変数は、Teams API 障害時の隙間が実際の問題だとわかれば、別の項目としてあらためて
  提案できます。
- **`BAJUTSU_OAUTH_ADMIN_TEAM` を単数形のまま残し、迂回専用の2つ目の環境変数を追加する方法**。
  採用しません。両方が存在するあいだ、サインイン時の挙動が異なる2つの admin Team 用変数が残ります。
  どちらを使っているかは、ソースコードを読まなければ運用者にはわかりません。改名した1つの変数の
  ほうが、挙動として説明し、判断する対象が1つで済みます。
- **`BAJUTSU_OAUTH_ADMIN_TEAMS` が未設定のとき、非推奨の代替として `BAJUTSU_OAUTH_ADMIN_TEAM` を読む
  方法**。既存デプロイが、環境変数を編集しなくてもアップグレード後の動作を保てるよう検討しました。
  採用しません。`BAJUTSU_OAUTH_ADMIN_TEAM` 自体、BE-0313 が加えたものです。この項目が
  引き続き発展させている、GitHub Team ベースの RBAC という設計の一部であり、広く導入済みの、長く
  安定してきた変数ではありません。BE-0313 は、ログインリスト形式の admin 名簿をこの同じ Team に
  置き換えたとき、`BAJUTSU_OAUTH_ADMINS` を退避路を残さず廃止しました。その直接の後継である変数に対して
  同じ完全な切り替えを保つほうが、この設計がもともと使い道を持たない退避路を新設するより一貫します。
  すでに `BAJUTSU_OAUTH_ADMIN_TEAM` を設定しているデプロイは、アップグレード時に
  `BAJUTSU_OAUTH_ADMIN_TEAMS` へ改名します。これは、BE-0313 自身の `orgs:` ブロック宣言の要求が、
  OAuth を使うすべてのデプロイに求めているのと同種の、採用時の対応です。
- **迂回を、データベースを配線していないデプロイ形態にだけ絞る方法**。BE-0313 は、その形態では
  Organization メンバーシップのゲートが唯一の防御線であることを、すでに文書化しています。採用しま
  せん。この項目が対象とする復旧の場面、つまり配線済みのサーバーバックエンドで `orgs:` ブロックが
  壊れている場合は、まさにデータベースを配線している形態です。迂回をそこから外せば、この項目が
  解決しようとしている場面そのものが未解決のまま残ります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の
> MECE（Mutually Exclusive Collectively Exhaustive）な作業分解（作業の単位ごとに 1 つ）に対応し、
> ログには変更内容と時期（古い順）を PR へのリンクとともに記録します。

- [x] `BAJUTSU_OAUTH_ADMIN_TEAM` を `BAJUTSU_OAUTH_ADMIN_TEAMS`（カンマ区切り）に改名し、そのリストを
      `SessionManager`、`role_for`、サーバーバックエンドの環境変数配線に通す。
- [x] `oauth_callback` のサインインゲートに、`identity_matches_org` と並ぶ admin Team の迂回を追加する。
      ロール判定のためにすでに取得している Team 一覧をそのまま使う。
- [x] セルフホスティングと設定のドキュメント（両言語）、`.env.example` を、改名した変数とこの迂回の
      説明に更新する。BE-0313 が述べていた「`default` テナントは OAuth サインインでは到達不能」という
      記述が、この項目によって成り立たなくなる点も反映する。
- [x] テストを追加する。`orgs:` に一致するエントリがない場合と、`orgs:` ブロック自体がない場合の
      両方で、admin Team のメンバーがサインインできることを確認する。どちらの場合も解決したロールが
      admin になることを確認する。Organization ゲートにも admin Team のリストにも一致しない login が
      引き続き拒否されることを確認する。改名した変数が複数の Team を持つリストとしてパースされることを
      確認する。迂回した admin が `default` テナントに配置されることを確認する。

## 参考

- [BE-0313 — GitHub Organization メンバーシップと Team ベースの RBAC を serve に導入](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md)。
  この項目が隙間を狭める、Organization と Team に基づくサインインゲートとロール判定です。この項目が
  塞ぐ隙間を名指ししている「admin はサーバー全体で1つの階層のまま」の節を含みます。
- [`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)。`oauth_callback` のサインインゲートと
  `role_for` のロール判定であり、この項目がどちらにも変更を加えます。
- [`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)。`identity_matches_org` と
  `org_for_identity` であり、admin Team の迂回はこれらと並んで動きます。
- [`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py)。`_fetch_teams` であり、
  GitHub API 障害時にすでに fail closed する挙動を、この項目の迂回はそのまま引き継ぎます。
- [`bajutsu/serve/state.py`](../../bajutsu/serve/state.py)。`SessionManager` であり、この項目は
  その `oauth_admin_team` フィールドをリストへ広げます。
- [`docs/ja/self-hosting.md`](../../docs/ja/self-hosting.md)。self-hosting ガイドの GitHub OAuth の節
  であり、この項目が塞ぐ隙間をすでに文書化しています（「まず上記のサインインのゲートを通過する
  必要があります」）。

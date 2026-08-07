[English](BE-XXXX-admin-team-bootstrap-bypass.md) · **日本語**

# BE-XXXX — admin 用 GitHub Team の環境変数が Organization メンバーシップのサインインゲートを迂回する

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
サインインを、`orgs:` の下に宣言した Bajutsu org へのメンバーシップ（`members` への明示的な
列挙、または `githubOrgs` に挙げた GitHub Organization のメンバー）でゲートします。このゲートを
通過した login だけが、`BAJUTSU_OAUTH_ADMIN_TEAM` との一致を確認され、admin ロールを得ます。BE-0313
自身の設計文書は、この結果として生じる隙間を名指ししています。admin Team のメンバーであっても、その
GitHub Organization がどの org の `githubOrgs` や `members` からも参照されていない場合、たとえば
どの `orgs:` エントリにも載らない運用専用の GitHub Organization に属する場合、admin Team が参照される
前にサインインの段階で拒否されます。admin になるどころか、サインインすらできません。同じ拒否は、config に
`orgs:` ブロックがまったくない場合、あるいは `members` や `githubOrgs` のエントリがデプロイの運用者を
まだ網羅していない場合にも、すべての login に及びます。`identity_matches_org` は、空の org 名簿に
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

この解析には、admin 全員を跡形もなく失うことを防ぐための起動時チェックを4つ追加します。そのうち
3つは GitHub OAuth が構成されていること（`oauth is not None`）に紐づき、1つは逆に紐づきます。
その4つ目のチェックが最初に来ます。`oauth` は `GitHubOAuthClient(...) if cid and secret and redirect
else None` として組み立てられるため、`BAJUTSU_OAUTH_GITHUB_*` のどれか1つでも未設定または誤りが
あると `None` に潰れ、下の3つのチェックからは、意図的にトークン認証だけを選んだデプロイと見分けが
つきません。`BAJUTSU_OAUTH_ADMIN_TEAMS` は正しく設定していても GitHub 側の変数を1つ書き間違えると、
運用者はこの3つのどこからも出力を得られず、GitHub のサインインはすべてそこで 404 になります。ただし、
これは締め出しではありません。`POST /api/login` は `oauth is None` のときにだけ有効なので、この
デプロイは置き換えたはずの共有トークンのログインに静かに戻ります。しかもトークンで発行したセッションには
identity が無いため `forbidden_for_role` が短絡し、そのセッションは全権を持ちます。運用者が GitHub
OAuth にゲートされていると思い込んだまま、サーバは共有トークンだけで開いたままになるということです。
この「戻り先がある」という前提は、トークンが構成されていることを仮定しています。`BAJUTSU_SERVE_TOKEN`
も未設定なら、戻り先すらありません。`SessionManager.check_token` は
`self.token is not None and secrets.compare_digest(...)` なので `POST /api/login` は 401 になり、
かつ両方の transport が同じ `token is None` を理由に認証と RBAC のゲートそのものをスキップします
（`handler.py` の `_gate`、`server/app.py` のミドルウェア）。`_ADMIN_PATHS` を含むすべてのエンドポイントが
認証なしで応答する、共有トークンへの後退よりもさらに悪い形です。この
チェックは、`oauth is None` かつ GitHub 側の3変数のうち少なくとも1つが設定されている場合、つまり
意図的なトークン認証専用デプロイではなく構成が半端な状態のときにだけ発火し、GitHub OAuth が部分的にしか
構成されていないこと、そして GitHub のサインインが 404 になることに加え、このデプロイが実際に
落ち込んだ2つの戻り先のどちらかを名指しして出力します（`token` は `_build_server_state` の既存の
引数なので、どちらの戻り先かを名指しできます）。この警告は、3変数すべてが設定されるまで出続けます。このメッセージは、`cid` / `secret` /
`redirect` のうちどれが未設定かも名指しし、3つ組をまるごとチェックリストとして出力するだけでは
ありません。変数の「名前」を書き間違えた運用者（`_REDIRECT_URI` を `_REDIRECT_URL` と書いた、など）は、
さもなければ `.env` をさっと読んだだけでは3つとも存在しているように見えてしまい、自分のファイルではなく
GitHub 側の OAuth app 登録を探しにいってしまいます。

`_build_server_state`（[`bajutsu/serve/__init__.py`](../../bajutsu/serve/__init__.py)）は、
GitHub OAuth を構成していて、廃止した `BAJUTSU_OAUTH_ADMIN_TEAM` がまだ設定されているとき、
`BAJUTSU_OAUTH_ADMIN_TEAMS` の設定有無を問わず常に stderr に警告を出します。ありがちな移行ミスは
新しい変数を未設定のまま残すことよりも、古い変数を削除せずに新しい変数を追加してしまうことです。
運用者は自分が追加している Team は覚えていても、古い単数形の変数を削除しなければならないことまでは
覚えていないものです。その結果、古い Team のメンバーは admin でなくなっているのに
`BAJUTSU_OAUTH_ADMIN_TEAMS` 自体は空ではないという状態が、静かに生まれます。この通知を「空である」
ケースだけに結び付けると、まさにこのミスを見逃してしまうため、新しいリストが何に解決されるかとは
無関係に、廃止した変数だけを見る独立したチェックとして分離しています。別に、解析後の
`oauth_admin_teams` が空になっているときも常に警告を出します。原因は、デプロイが
`BAJUTSU_OAUTH_ADMIN_TEAMS` を一度も設定していない場合と、廃止名だけが設定されている場合の
どちらでもあり得ます（後者は「検討した代替案」で述べる完全な切り替えのもとでは、そのデプロイが
admin Team を1つも持たなくなるケースです）。どちらにしても、他に見える症状は admin の操作すべてで
説明のない 403 が返ることだけで、かつ、このリストはいまサインインの資格情報でもあるため（後述）、
それを直しにサインインできる admin も残っていません。もう1つは、各エントリが
`"<GitHub organization>/<team slug>"` の組として正しい形かどうかの確認です
（`/` の個数ではなく、organization 側と slug 側のどちらかが空である場合や、内部に空白がある場合を
拒否する正規表現で判定します）。スペースやセミコロンで
区切ったリストは、実在の Team には一致しない、1つの
不正なエントリとしてパースされます。これは、別の間違いで到達する同じ「admin がいない、しかも原因が
見えない」失敗です。この正規表現は、どちらの側の大文字も拒否しません。`in_admin_team`
（後述）がメンバーシップ判定の両辺を case-fold するため、GitHub 自身が保存している大文字小文字と
異なるエントリ（たとえば GitHub の UI に表示される Team の表示名からそのまま書き起こした slug）でも
一致し続けます。ここで拒否してしまうと、すでに機能しているエントリを運用者に「直す」よう促すことに
なり、さらに悪いことに、本当に壊れたエントリが隠れている、まさにこのリストに対する警告を無視する
習慣を運用者に付けてしまいます。4つとも例外を発生させず警告だけを出します。config の書き間違いによって、
デプロイが起動できなくなるのではなく admin なしの状態に落ちるようにするためです。これは、この
module が読む他の運用者向け環境変数（`BAJUTSU_SESSION_TTL`、同時実行数の上限、
`BAJUTSU_RUN_RETENTION_DAYS`）とは意図的に異なる選択です。それらはいずれも不正な値では例外を
発生させます。起動できないサーバーは、admin がいないサーバーと同じくらい直しにくく、しかもここでの
間違いは、それらの場合とは異なり、外側から直せるものだからです。不正なエントリは、リストから
取り除かず残します。取り除いてしまうと、admin の名簿を構文的に正しいものだけへ静かに絞り込むことに
なり、すでに警告している失敗の上に、もう1つの静かな失敗を重ねてしまうからです。4つのうち3つの確認は、
GitHub OAuth を構成しているときにしか動きません。トークン認証だけのサーバーバックエンドでは
`BAJUTSU_OAUTH_ADMIN_TEAMS` は何も決めないため、そこに残った古い値や不正な値は、そのデプロイ形態が
持ったことのない admin ロールについて警告するのではなく、静かなままであるべきです。

この4つの確認は `_build_server_state` の中で、まだ何も構成されていない段階で動くため、その時点で
選べる手段は素の `print(..., file=sys.stderr)` しかありません。登録された `event` も無く、
correlation フィールドも redaction もない、構造化されていないテキストであり、ログが立ち上がった後の
JSON 形式の `oplog` の stdout 書き込みと混在します。これはまさに、ログ pipeline が捨ててしまうか
パースに失敗する形であり、しかも運用者が最も alert を組みたい状態、つまり「このデプロイには admin が
おらず、サインインして admin を得る手段もない」という状態そのものです。`_build_server_state` は、
出力する各メッセージを新しい `ServeState.startup_warnings` フィールドに集約するようになります。
これは裸のメッセージではなく `(check, msg)` の `tuple[tuple[str, str], ...]` です。4つの確認は
以下で述べる1つの event を共有するため、メッセージだけでは運用者の alert が、いつか誰かが言い回しを
変えると静かに壊れる自由文の部分一致に頼るしかなくなります。`check` は
（`"oauth_half_configured"`、`"admin_team_retired_name"`、`"admin_teams_empty"`、
`"admin_teams_malformed"`という）安定した識別子で、alert はこちらをキーにできます。これにより、
意図的に OAuth を admin Team なしで運用しているデプロイが、他の3つを黙らせずにこの1つだけを
抑制することもできるようになります。`_build_server_state` の内側にあるローカルな `_warn(check, msg)`
クロージャが、この対応を省略不能にします。4つの確認それぞれが `print(...)` と
`startup_warnings.append((check, msg))` をそのまま繰り返すのではなく、これを1回呼ぶだけにします。
これにより、後から追加される5つ目の確認が、警告を print だけして集約を忘れるということができなくなり
ます。そのような失敗は「印字されたか」という手動確認は通過しつつ、`_emit_startup_warnings` からは
静かに漏れ続けてしまうからです。
`serve()` は、`_configure_oplog` の直後に新しい `_emit_startup_warnings(state)` を呼びます。これは
`restore_persisted_provider_settings` がすでに使っている配置と同じであり、理由も同じです
（「malformed-file の警告が live のログシンクに届くように」）。この関数が、集約した各メッセージを
`oplog.log_event` を通じて、登録済みの `msg` と並ぶ1つのフィールドとして `check` を渡しながら、
`oplog.EVENTS` の新しいエントリ `"server.startup_warning"` として
再発行します。この関数は、その2つの隣人がすでに立てているパターンに合わせて、`serve()` 内の
インラインな loop ではなく、単独で呼び出せる boot seam です。`serve()` は実際にサーバの loop を
走らせる関数であり、高速なテストスイートでは実行されません。そのため、インラインな loop のテスト
網羅は「クラッシュしないこと」だけになってしまいます。`log_event` は、未登録の event に対しては
仕様として `ValueError` を発生させるため、後になって `"server.startup_warning"` が `oplog.EVENTS`
から改名または削除されると、テストスイート全体は green のままなのに、実際に再発行すべき起動時警告を
持つデプロイが起動時に、`_configure_oplog` の後、`restore_persisted_provider_settings` の前で
クラッシュします。まさにこの項目が助けようとしている構成ミスのデプロイが、admin を欠くだけでなく
まったく起動できなくなるということです。専用のテストが `_emit_startup_warnings` を直接動かし、
メッセージだけでなく各レコードの `check` フィールドも固定します。
`print` の呼び出しはそのまま残します。それらが動く時点では何も構成されていないため、store も
データベースも無いまったく壊れたデプロイが起動したときに、何かを見る手段は依然としてそれしかない
からです。[`docs/self-hosting.md`](../../docs/self-hosting.md)は、`"server.startup_warning"` と
`"oauth.denied"` の両方を、*運用ログ* 節の event 一覧だけでなく admin Team の移行手順の近くでも
名前で挙げます。移行手順を読む運用者が、「ログの最初の数行を読んでください」だけでなく alert を
組める経路も見られるようにするためです。

`oauth_callback`（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）は、Organization
メンバーシップのゲートを実行する前に、すでに login の GitHub Team メンバーシップを取得しています
（`identity.teams`、`fetch_identity` 経由）。この取得は、後段の `editorTeam` によるロール判定にも
使われているためです。ゲートと Team の取得自体は、これまで一緒に使われるよう順序づけられていな
かったにすぎません。この項目は、`identity_matches_org` と並ぶ確認を1つ追加します。login の
`identity.teams` が、設定した admin Team のリストと重なる場合、`identity_matches_org` の結果に
かかわらずサインインゲートを通過します。これにより、admin Team のメンバーは、自身の GitHub
Organization を挙げる `orgs:` エントリがなくても、あるいは `orgs:` そのものがなくてもサインイン
できます。どちらの確認にも一致しない login は、今日と同じ 403 で拒否されますが、もはや静かにでは
ありません。`oplog.log_event` を通じて記録されるようになり、`"oauth.login"` に折り込むのではなく
`oplog.EVENTS` の新しいエントリ `"oauth.denied"` として記録します（`"oauth.login"` は、後述の理由で
「ログイン件数」を意味するままにしておきます）。拒否は、この項目が回復可能にしようとしている、まさに
その失敗（壊れた、あるいは存在しない `orgs:` ブロックに、一致する admin Team もない状態）なので、
サインインの成功と同じ audit 相当の可視性が必要です。運用者が「サインインできない」というユーザの
報告と突き合わせる手がかりが何もない、素の 403 のままではいけません。この拒否メッセージは、下記の
迂回による許可の成功メッセージと同じ5つの形のうちどれが `orgs:` を不一致にしたのかを名指しします。
共有の `_unmatched_org_cause` helper が両方のために計算するため、この項目のある改訂が一時的に許して
しまったように、2つの記録がずれてしまうことはありません。拒否された login のほうこそ、「サインイン
できない」という報告の出どころになりやすいのに、迂回による許可が受けるのと少なくとも同じ triage を
受けられないというのはおかしいからです。文末の admin に関する句にも同じ注意を払います。「no admin
Team matched」は本物のメンバーシップの不一致のように読めますが、`admin_teams` が使えない状態（空、
あるいは全エントリが不正な形式）の場合にも同じ文言が出てはいけません。これは起動時の
`admin_teams_empty` / `admin_teams_malformed` チェックが警告する、まさにその状態であり、`orgs:` を
直しにサインインできる admin が誰も残っていない状態でもあるからです。そこでこの句は、下記のレベルと
同じ `admin_teams_unusable` という述語を条件にした1つの分岐で、「no usable admin Team is
configured」と名指しします。これにより、メッセージとレベルがずれてしまうことはありません。5つの形の
うち3つは `orgs` を `{}` に潰し、admin 以外の
すべての login を無条件に拒否します。config がまったく bind されていない場合、config の読み込みが
失敗した場合、そして `orgs:` ブロックをまったく宣言していない場合です。そのため、実際には読まれていない、
あるいはわざと空のままにされている org
名簿を非難するメッセージは、運用者を間違った修正に向かわせてしまいます。この
「orgs: ブロックがない」という形は例外的なケースではありません。この項目自身の見出しの状況その
ものです（*動機*: 「`orgs:` ブロックを持たずに起動する GitHub OAuth デプロイは……他の全員と一緒に
すべての admin を締め出します」）。`oauth_callback` には、サインインを成功させずに終える経路が5つ
あり、この項目はその5つすべてを記録するようにします。残りの4つ（OAuth が構成されていない場合。構成が
半端なデプロイでは `BAJUTSU_OAUTH_GITHUB_*` の3変数のどれか1つでも未設定だと GitHub のサインインは
すべてここで 404 になりますが、これは締め出しではありません。`login` の共有トークン経路はこの同じ
`oauth is None` のときにだけ有効に戻るため、デプロイは置き換えたはずの共有トークンのログインに静かに
戻り、そのセッションには identity が無いため `forbidden_for_role` が短絡して全権を持ちます。CSRF の
state 不一致、exchange が例外を発生させた場合、exchange が
identity を返さなかった場合）も、同じ `"oauth.denied"` イベントを記録します。上で述べた理由がそのまま
当てはまるためです（ユーザの「サインインできない」という報告と突き合わせる手がかりが無い、素の 404
や 403、502 になってしまう）。この4つの早い段階の記録はすべて `INFO` で発行します。どれも、実在する
GitHub account を証明する必要がありません。`oauth is None` はデプロイの静的な性質であり、request
ごとの信号ではありません。また
`state_param` と `state_cookie` はどちらも呼び出し側が与える値（query の値と、呼び出し側自身の
`Cookie:` header）なので、攻撃者は両方に同じ偽の値を送るだけで `secrets.compare_digest` を無料で
突破できます。これら4つのどれかに `WARNING` を与えると、それは匿名の呼び出し側が自分で決められる
request ごとの信号になり、この endpoint への loop が、この項目が追加しようとしている唯一の信号
（org ゲートの拒否）を、誰でも引き起こせる自分自身の雑音の下に埋めてしまいます。`INFO` で記録しても
記録自体は残ります（素の 404 や 403、502 のままにはなりません）。ただ、運用者の `WARNING` 別 alert が
除外しなければならない信号にはなりません。state の不一致が繰り返されるのは、期限切れの cookie という
より login-CSRF 攻撃の兆候であるという性質自体は変わりませんが、これは多数の記録についての *率* の
主張であり、記録1件ごとの性質ではありません。この性質の正しい住み処は、運用者が閾値を設定できる
counter であり、この event の log level ではありません。これら4つの早い段階では `login` がまだ
わからないため、その記録には `actor` フィールドが付きません。`actor` が付くのは、後段のゲート由来の
拒否と、すべての成功したサインインだけです。

org ゲートの拒否そのものも、無条件に `WARNING` になるわけではありません。`GET /api/oauth/login` は
認証不要であり、GitHub OAuth app はこのデプロイのメンバーだけでなく、GitHub の任意のユーザを認可します。
無料の account を持つだけの誰かも、上記の4つと同じくらい簡単にこの分岐へ届くので、「実際の GitHub
exchange が必要」という条件は、デプロイの運用者だけに絞る境界にはなりません。`WARNING` を取っておくのは、
実際にページングすべき唯一の形、つまり（`authz.py` の）`admin_teams_unusable` が真になる場合だけです。
これは `admin_teams` が空である場合、あるいは全エントリが整形式の判定に失敗する場合の両方で真になります。
`not admin_teams` だけでは足りません。空ではないが全体として不正なリスト（スペース区切りの値が
1つの `"a/b c/d"` エントリにパースされる場合など、この項目自身の malformed-entry テストがまさに
この形を使っています）は、`_build_server_state` の `admin_teams_empty` チェックが起動時に警告する
締め出しと、機能的には同じものです。この整形式判定のパターンは、いまや `authz.py` にある1つの
module レベルの定数（`ADMIN_TEAM_ENTRY_RE`）にまとめられ、`_build_server_state` 自身の
`admin_teams_malformed` チェックもこれを import して使います。`in_admin_team` と
`_unmatched_org_cause` がすでに防いでいるのと同じ、2つ目の正規表現リテラルが最初のものからずれていく
事態を避けるためです。通常の拒否（構成済みの admin Team に単にこの login が一致しなかった場合）も、
上記の4つの早い失敗と同じ理由で、依然として記録は残りますが、`INFO` で記録されます。
サインインが成功するたびに、`oauth_callback` はいまや `oplog.log_event`
（[`bajutsu/serve/oplog.py`](../../bajutsu/serve/oplog.py)）を通じて記録します。すでに予約済みの
`"oauth.login"` イベントとして、login 自体を `actor` の correlation フィールドに入れます。素の
logging 呼び出しではないため、この記録は serve の他のあらゆる運用上重要な記録と同じ、登録された
イベント名、redaction、correlation のフィールドを持ち、`event` で絞り込む運用者の alert からも
実際に見えます。`"oauth.login"` はこの項目より前から `oplog.EVENTS` に予約されていましたが、実際に
発行されたことは一度もありませんでした。この項目が初めてこのイベントを発行するようにし、しかも
迂回した login だけでなく、すべてのサインインで発行します。迂回だけを記録するイベントだと、
`event=oauth.login` は「ログイン件数」ではなく「迂回件数」を意味してしまい、その event 名で
alert を組む運用者が期待する意味とは逆になってしまいます。record ごとの `bypass` フィールド
（admin Team の迂回、`orgs:` ではない方が login を許可した場合にだけ `True`）と、それに応じて変わる
メッセージ（迂回の場合は「admin-Team bypass admitted …」という文言、それ以外は単純な「… signed in」
という文言）があります。レベルは `bypass` をそのまま映すわけではありません。この項目自身の指針が、
どの `orgs:` エントリにも載らない運用専用の GitHub Organization に属する admin Team を推奨しているため、
そこでは `bypass` が**すべての** admin サインインで、永続的に `True` になります。これは正しく構成された
デプロイの通常の運用状態であり、ページングすべきものではありません。`WARNING` が発火するのは、org
モデル自体が使えない場合、つまり `parsed is None`（config が bind されていない、あるいは読み込みに
失敗した）か、config は読み込めたが `orgs:` ブロックをまったく宣言していない場合だけです。これは、迂回が
admin Team のメンバー以外は現時点でサインインして直せないデプロイへ、まさに login を許可してしまった
形です。他のすべての迂回、そして通常のサインインはすべて `INFO` のままなので、このフィールドはすべての
記録で定数の `True` になるのではなく、実際の情報を運ぶようになります。迂回は、依然として `orgs:` が認可しなかった唯一の
サインイン経路であり、そのため、誰がいつサインインしたかを追跡したい運用者にとって、記録が何も
残らない唯一の経路でもあります。同じイベント streams がそれを通常の org ゲート済みログインと
区別できるようにするのが、この `bypass` フィールドです。迂回メッセージはさらに、`matched_org` が
`False` になる5つの経路のうちどれだったかも名指しします。config がまだ bind されていない、config
の読み込みが失敗した、config が `orgs:` ブロックを宣言していない、GitHub がこの login に対して org
を1つも返さなかった、あるいは `orgs:` のどのエントリにも一致しなかった、のいずれかです。`WARNING`
に反応した運用者は、org ゲートがこの login を認めなかったという事実だけでなく、その理由も知る必要が
あるためです。最初の2つは config 自体を調べるべきであり、残りの3つは org 名簿を調べるべきです。
`_unmatched_org_cause` は、この最初の2つを `state.config` そのものが `None` かどうかで見分けます。
`load_serve_config_file` は、config path がまったく bind されていない場合も、bind されているが
読み込みに失敗した場合も、同じ `None` を返すため、両方を「config の読み込みが失敗した」の1つに
まとめてしまうと、運用者はまだ存在しないはずのファイルのファイルシステムエラーや YAML の書き間違いを
探すことになります。この区別を、1つの固定された文言の裏に隠してしまわないようにしています。下記の
拒否経路も、同じ理由で同じ5つの形を名指しします。拒否された login のほうこそ、「サインインできない」
という報告の出どころになりやすいからです。そのため、共有のモジュールレベル helper
`_unmatched_org_cause` が両方の呼び出し元のために一度だけこれを計算します。これは、下記で
`in_admin_team` を切り出しているのと同じ理由です。同じ5分岐の独立した2つのコピーを後から別々に
編集すると、まさにこの分岐の組自体が一度そうなったようにずれていく可能性があります。6つ目の
形が片方のコピーにだけ入り、もう片方には入らないかもしれませんし、拒否された login が最初の5つの
うち古くなった1つを伝えられるかもしれません。

このゲートの判定が使うメンバーシップ判定（login の Team のどれかが設定済みの admin Team 一覧に
含まれるか）は、`role_for` が admin ロールを解決するときに使う判定と同じです。そのため、この項目は
同じ式を2箇所に書くのではなく、1つの共有ヘルパー `in_admin_team` にまとめます。約120行離れた2つの
関数に同じ規則が2つ存在すると、どちらか一方だけへの後からの独立した変更でずれが生じかねません。
そうしてずれた場合、ゲート側の判定だけが通した login は、role の判定を通らず viewer に解決されます。これは、`orgs:` が
一度も認可していない login のためのセッションであり、しかもその login を通した理由そのものである
admin 権限を持ちません。1つのヘルパーにまとめることで、このずれは構造的に起こり得なくなります。

`in_admin_team` は、このメンバーシップ判定の両辺を case-fold（大文字小文字を無視）して比較します。
GitHub は org の login と Team の slug をどちらも大文字小文字を無視して解決します。また、Team の
slug は常に小文字化される一方で、実在の GitHub org login は大文字混じりで保存されることがあります。
そのため、`admin_teams` の
エントリの organization 側が GitHub に保存されているどの大文字小文字であっても、login の
`identity.teams` の exact-case のメンバーシップと一致し続けなければならず、逆方向についても同様です。
この case-fold がなければ、この項目自身のサインイン迂回は潜在的な大文字小文字の罠を抱えてしまいます。
`admin_teams` のエントリは、たまたま大文字混じりで表示される GitHub の org ページから書き起こされたり、
org の rename で保存済みの大文字小文字が変わる前にコピーされたりした場合、許可するはずの login に
静かに一致しなくなってしまいます。これは、上の malformed-entry の warning がすでに防いでいる
「admin がいない、しかも見える原因がない」という同じ失敗であり、ただし大文字小文字の不一致は
構文的には整った形なので、その確認では届きません。`editorTeam`
（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py) の `role_for`）も同じ形をしており、
同じ GitHub 側の大文字小文字と比較されるため、同じ潜在的な罠を抱えています。ここで直さずに
残しているのは、その値がさらされている度合いが低いからではなく、スコープ上の判断です。
`editorTeam` にも case-fold を広げることは、この項目のサインイン復旧という範囲の外にある、ロール
判定そのものの変更になります。case-fold は、空の Team 名を一致に変えることは
ありません。`admin_teams` にはカンマ分割時の `t.strip()` によるフィルタがあるため `""` が
含まれることはないからです。また、下記の入れ子 Team に対する保証にも影響しません。それは
文字列としての完全一致に基づくものであり（`"acme-gh/parent/child"` は case-fold の有無を問わず
`"acme-gh/parent"` とは異なる文字列です）、区切り文字の個数には基づかないためです。

### ロール判定の形は変わらず、名前だけが変わる

`role_for`（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）は、すでに単一の admin Team
用パラメータから、admin を editor より上、editor を viewer という基底ロールより上に位置づけています。
この項目は、そのパラメータを単一の Team からリストへ広げ、単一のメンバーシップ判定の代わりに
（上記の共有ヘルパー `in_admin_team` を通じて）`identity.teams` との積集合を確認するだけです。上の
サインインゲートが、迂回した login が一致する Team を持つことをすでに確立しているため、`role_for` は
その login を、今日の他の admin Team メンバーと同じ方法で admin に解決します。迂回した場合のための
別のロール経路は追加しません。

`role_for` と、後述する org の配置先の決定は、どちらも `oauth_callback` の
`if state.repository is not None:` ブロックの中でだけ動きます。これは、他の admin Team メンバーに
対して今日すでに動いているのと同じ範囲であり、この項目はどちらにも新しい呼び出しを追加しません。
データベースを配線していないデプロイでは、どちらも動きません。その場合、迂回が持つ効果はサインイン
ゲート自体だけです。`state.repository is None` によって `forbidden_for_role` がすでに短絡しているため
（BE-0313）、そのデプロイではサインインした user が誰でもロールを問わずフルアクセスを得ます。迂回した
login が得るのは、そのゲートが他のどの許可済み login にもすでに与えているサインインだけです。

### 迂回した admin の配置先

Organization メンバーシップではなく Team の迂回によってゲートを通過した admin は、`org_for_identity`
がすでに `members` と `githubOrgs` のどちらにも一致しない login を置いている先と同じ、共通の `default`
org に配置します。BE-0313 自身の記述は、`orgs:` ブロックを宣言すると `default` org は
「OAuth サインインでは到達不能になる」と述べています。そこへ落ちるはずだった login が、配置が計算
される前にすべて拒否されるためです。この項目は、その記述を無条件には成り立たなくします。迂回した
admin は、他のどの org にも自分の login が一致しないからこそ `default` に到達します。`default`
自体は、他のどの org にも属さない[target](../../docs/ja/glossary.md#target-app-device)以上の
特別な扱いを持たず、admin の `_ADMIN_PATHS` による
強制はすでに org を問わずインスタンス全体に及んでいます（BE-0313「admin はサーバー全体で1つの階層
のまま」）。そのため、この配置は、BE-0313 がすでにサーバー全体のものとした admin ロール以上の何かを、
迂回した admin に新たに与えるものではありません。

「他のどの org にも自分の login が一致しない」というのは、この `default` が想定するケースであり、
迂回が login を許可する唯一の経路ではありません。config 自体の読み込みが失敗した場合も、
`identity_matches_org` には一致が見えなくなります。これは、実在の org が本当にその login を
名簿に持っている場合にも起こります（`load_serve_config_file` は、一時的なファイルシステムのエラーや
config の書き間違いに対して `None` へ fail closed し、`orgs` は `{}` に潰れます）。これは、この項目
自身が動機とする状況、つまり壊れた `orgs:` ブロックが、実際に生じさせる失敗の形そのものです。何の
補正もなければ、この一度きりの不調によって、実在の org メンバーが、そのたびに `default` へ移されて
しまいます。次のクリーンなログインで元に戻るまで、user 行、audit の帰属先、オブジェクトストレージの
プレフィックスがすべて移動したままです。これは、上記の配置ロジックが、`orgs:` がすでに認めている
login に対して意図した結果ではありません。`oauth_callback` は、この1つの特定の失敗の形に限って
これを避けます。`orgs:` ではなく迂回がその login を許可し、*かつ* config path が bind されている
のに読み込みに失敗した場合（`parsed is None and state.config is not None`）に限って、
`state.repository.user_org` にすでに記録されている org をそのまま使い、あらためて計算しません。
`org_for_identity` の `default` という結果に落ちるのは、事前の記録が存在しない場合、つまりこの節が
本来対象としている、正真正銘の初回の bootstrap の場合だけです。この guard の `state.config is not
None` の側にも独自の意味があります。`load_serve_config_file` は、config path がまったく bind
されていない場合も、まったく同じ `parsed is None` を返します。これは `serve()` 自身が通常の状態
として扱う起動時の状態であり、一時的な障害ではありません。admin が config を bind するまで、毎回の
ログインで `None` のままです。そのため、この保持を `parsed is None` だけに絞ると、そのような login
の org は永久に固定され、config がついに bind されても二度と再解決されません。config は読み込めたが
`orgs:` のどれにも一致しなかった login は、
このケースには当たりません。config が答えを返しているため、その login は正真正銘、誰にも属していません。
それが初回のサインインだからなのか、あるいは運用者がその後、設定済みのすべての org からその login を
外したからなのかを問いません。そのような login は、他のあらゆる login と同じく `org_for_identity`
を通じて再解決されます。これは BE-0015 7c-2 がすでに、ロール判定をどのログインでも同様に行うよう
求めているのと同じです。`orgs:` から外したことは、次のサインインで反映されなければならず、脱退済みの
メンバーが以前保持していた org に固定され続けてはいけません。

この保持ロジックは、`identity.orgs` が空である場合には意図的に結び付けていません。`/user/orgs` の
取得が失敗した場合も、これと同じ形（`_fetch_orgs` は `[]` へ fail closed します）を取りますが、
GitHub のどの org にも本当に属していない login（たとえば `members:` に列挙された bot や ops 専用の
account）にとっても、まったく同じ形になります。`_fetch_orgs` の `[]` からは、この2つを見分ける
手立てがありません。これを条件に含めてしまうと、より悪い問題に置き換わってしまいます。`members:`
から外された後、その login の org は永久に固定され続けます。将来のどのログインも二度と非空の
`identity.orgs` を報告できないため、このガードから逃れられないからです。これは、数行下にある
role に対する「毎回のログインで再計算する」という同じ原則と、静かに矛盾してしまう、永続的に誤った
状態です。`parsed is None` だけに絞ることで、より小さく、自己修復する代償を受け入れます。
`githubOrgs` だけでつながっている member が本物の `/user/orgs` の障害に遭遇した場合、そのログインに
限って `default` へ移され、次のクリーンなログインで元に戻ります。これは、正真正銘の un-claimed
login と同じ扱いです。この2つのケースを見分けられるようにするには、`_fetch_orgs` が失敗を `[]`
ではなく `None` として報告するように変える必要があり、それは `_paginate` の契約（`_fetch_teams`
とも共有）、`Identity.orgs` の型、テストスイート内のすべての fake `OAuthClient` を変更することに
なります。この項目が他に触れていないコードへの変更であるため、ここでは行わず、フォローアップとして
残します。

この配置は、org モデルがもともと抱えている落とし穴を引き継ぐだけで、新しい落とし穴を作るわけではありません。
`DEFAULT_ORG`（[`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)）は、`"default"` という文字列
そのものです。デプロイがこの同じキーで実在の org を宣言することを妨げるものは何もありません。
`targets_for_org` は、その org 自身の `targets:` の指定にかかわらず、target の所有権判定でこの文字列を
すでに特別扱いしています。したがって、org を文字どおり `default` と名付けたデプロイには、この項目
より前からすでに衝突があります。他のどの org にも一致しない迂回した admin は、同じキーの下に
配置されます。実在の `default` org を持つデプロイでは、その admin の user 行、audit エントリ、
オブジェクトストレージのプレフィックスは、中立な受け皿ではなく、その org の下に置かれます。同じ
衝突が、もう1つの呼び出し元に広がるだけです。admin Team の迂回に頼るデプロイは、target の所有権に関する
既存の理由に加えて、この理由からも、org を文字どおり `default` と名付けることを避けるべきです。

### 下層の Team 取得は fail closed のままだが、サインインそのものを失う場合がある

`_fetch_teams`（[`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py)）は、非200
応答、ページネーション途中のネットワーク障害、パースできない本文のいずれに対しても、すでに空の
Team 一覧へ fail closed します。この項目は、GitHub への新しい呼び出しを追加しません。BE-0313 が
すでに取得している同じ `identity.teams` を読むだけです。org のゲートがすでに通している login に
とっては、この失敗が及ぼす影響は今日と同じく editor と admin のロールだけです。一方、サインインその
ものが迂回だけに依存している login にとっては、同じ fail closed の挙動が、いまはサインインそのものを
失わせます。GitHub の API 障害が起きている間は admin Team のメンバーシップを証明できず、迂回を使えま
せん。その場合、Organization メンバーシップのゲート単独が与える結果に戻ります（`orgs:` からもその
Organization に到達できなければ拒否です）。この項目は、この API 障害のケースに対する login リストの
代替経路を追加しません。詳しくは「検討した代替案」を参照してください。

### admin Team の各エントリは、ロールの対応づけだけでなくサインインの資格情報にもなる

この項目より前は、デプロイが実際には管理していない GitHub organization を指すエントリ（
organization 側の書き間違い、あるいは後から名前が変わった Team の organization）は、単に誰にも
一致しませんでした。削除された Team の organization も同じで、GitHub は古い login を誰でも
再登録できるようにするため、そうした organization が実在することもあります。
`role_for` は、`identity_matches_org` が org 名簿にいない login をすでに拒否した後にしか、この値を
確認しなかったからです。この項目の後は、その同じエントリがサインインゲートの一部になります。その
organization を管理する人は誰でも、一致する slug の Team を作り、迂回を通じてサインインし、admin に
解決されます。到達できる範囲は、`_ADMIN_PATHS` のすべてのエンドポイントです。`GET /api/config/content`
も含まれ、これは config の本文をそのまま返すため、そこに書かれた literal な secrets が漏れることも
あります。「admin 用環境変数をカンマ区切りのリストにし」の節にある不正なエントリの警告は、これを検出
できません。誰も管理していない organization を指すエントリでも、形としては正しく整っているからです。
GitHub organization を誰が管理しているかをコードで確認する方法はありません。そのためこの項目が取れる
対策は運用面だけです。`deploy/self-host/.env.example` と self-hosting ガイド（両言語）は、各エントリ
が実際に自分が管理する GitHub organization を指す必要があると明記します。この値は、いまはロールの
対応づけだけでなく、サインインの資格情報そのものだからです。

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
      `SessionManager`、`role_for`、サーバーバックエンドの環境変数配線に通す。OAuth を構成している
      ときにだけ、起動時に大きく警告する対象は3つ。廃止した単数形がまだ設定されているとき（ありがちな
      部分的な改名ミスを見逃さないよう、新しい複数形の設定有無は問わない）、結果のリストが空になっている
      とき（未設定でも、廃止名だけが設定済みでも）、そして各エントリが正しい
      `"<GitHub organization>/<team slug>"` の組でないとき（空の側や内部の空白は対象だが、大文字は
      対象外とする。`in_admin_team` が case-fold するため、大文字小文字が異なるエントリでも
      一致し続けるからである）。
      admin を失う間違いをどれも見逃さず、かつトークン認証だけのデプロイに持っていない admin ロールに
      ついて警告しないようにします。逆方向にゲートされた4つ目の確認（`oauth is None` だが GitHub 側の
      変数が少なくとも1つ設定されている）は、構成が半端なデプロイに警告します。上の3つの確認はここには
      届きません。どれも `oauth is None` を「意図的にトークン認証専用」と読むためです。締め出しでは
      ありません。`POST /api/login` はこの同じ `oauth is None` のときにこそ有効になるので、このデプロイは
      置き換えたはずの共有トークンのログインへ静かに戻り、そのセッションはすべて全権を持ちます。ただし
      `BAJUTSU_SERVE_TOKEN` も未設定なら、両方の transport が認証と RBAC のゲートそのものをスキップし、
      すべてのエンドポイントが認証なしで応答します。メッセージは、このデプロイが実際に落ち込んだほうの
      戻り先を名指しします。運用者は
      GitHub OAuth にゲートされていると思い込んだまま構成が半端なことに気づかない、という危険であり、
      admin Team に着目した上の3つの確認からは推測できません。
- [x] `oauth_callback` のサインインゲートに、`identity_matches_org` と並ぶ admin Team の迂回を追加する。
      `Identity` は `TYPE_CHECKING` の下でインポートします（`from __future__ import annotations` が
      すでに有効なので、annotation だけの用途にランタイムのインポートは不要です）。モジュールレベルの
      インポートにすると、既定の `bajutsu.serve` / CLI パスに `bajutsu.serve.server` を引き込んで
      しまいます。これは `bajutsu/serve/server/__init__.py` が「起こらない」と明言している状態です。
      `state.py` はすでに同じモジュールに対してこの前例を確立しています。ロール判定のためにすでに
      取得している Team 一覧をそのまま使う。サインインが成功したときは
      すべて `oplog.log_event`（予約済みの `"oauth.login"` イベント、login を `actor` に、迂回だけで
      許可した場合にだけ `True` になる `bypass` フィールド）を通じて記録し、`orgs:` が認可しなかった
      唯一のサインイン経路にも、`event` で絞り込める記録を残す。この関数がサインインを成功させずに
      終える5つの経路すべてを、別の `"oauth.denied"` イベントで記録する。OAuth が構成されていない
      場合、CSRF の state 不一致、exchange が例外を発生させた場合、identity を返さなかった場合は
      すべて `INFO` で記録します。どれも実在する GitHub account を証明する必要がないためです。
      `oauth is None` はデプロイの静的な性質であり、攻撃者は `state_param` と自分自身の `Cookie:`
      header に同じ偽の値を送るだけで CSRF の確認を無料で突破できます。そしてどちらの確認にも一致しない
login（最後のものは、
      `orgs:` が一致しなかった5つの形、config がまだ bind されていない、config の読み込み失敗、
      config が `orgs:` ブロックを宣言していない、GitHub が org を返さなかった、実在するが一致しない
      名簿、のどれだったかも共有の `_unmatched_org_cause` helper で名指しする。最初の2つは、
      `state.config` 自体が `None` かどうかで見分けます。これにより、壊れた、あるいは存在しない
      `orgs:` ブロックに一致する admin Team もない状態が、突き合わせる手がかりの無い素の 404 や
      403、502 のままにならないようにします。identity を永続化する際、既存の login の記録済み org を
      `default` へ移すのではなくそのまま使うのは、config path が bind されているのに読み込みに
      失敗した場合に限ります。config がまったく bind されていない場合（一時的な障害ではなく通常の
      状態）や、`/user/orgs` が空を返した場合には適用しません。後者は、GitHub のどの org にも本当に
      属していない login と同じ形だからです。
- [x] `_build_server_state` の4つの起動時警告を、出力するだけでなく新しい
      `ServeState.startup_warnings` フィールドに集約するようにする。裸のメッセージではなく
      `(check, msg)` の `tuple[tuple[str, str], ...]` とする。4つとも下記の1つの event を共有する
      ため、`check`（`"oauth_half_configured"`、`"admin_team_retired_name"`、`"admin_teams_empty"`、
      `"admin_teams_malformed"`）という安定した識別子がなければ、運用者の alert は言い回しが変わると
      壊れる自由文の部分一致に頼るしかなくなります。新しい `_emit_startup_warnings`
      boot seam を追加する。`restore_persisted_provider_settings` や `register_launch_project` と
      並ぶ、単独で呼び出せる関数であり、`serve()` 内のインラインな loop ではありません。この関数が、集約
      した各警告を `oplog.log_event` を通じて、`check` を登録済みの `msg` と並ぶフィールドとして渡し
      ながら、`oplog.EVENTS` の新しいエントリ
      `"server.startup_warning"` として、`_configure_oplog` の直後に再発行する。これはその隣人たちが
      すでに使っている配置と同じです。これにより、運用者の `event` 別 alert は「admin がおらず、
      サインインして admin を得る手段もない」という状態も、運用者がたまたま生のブート出力から読んだ
      場合だけでなく、確実に見られるようになります。
- [x] セルフホスティングと設定のドキュメント（両言語）、`.env.example` を、改名した変数とこの迂回の
      説明に更新する。BE-0313 が述べていた「`default` org は OAuth サインインでは到達不能」という
      記述は、この項目の *詳細設計* の中で成り立たなくなります。`docs/` 配下のどのページもこの記述を
      述べていないため、ドキュメント側の修正は行いません。各エントリは実際に自分が管理する GitHub
      organization を指す必要があると明記する。この値はいまサインインの資格情報でもあるためです。
      起動時警告そのものを self-hosting ガイド（両言語）と `.env.example` に名指しし、
      アップグレードする運用者がログの最初の数行を確認するべきだとわかるようにする。加えて
      `event=server.startup_warning` と `event=oauth.denied` も、*運用ログ* 節の
      event 一覧だけでなく移行手順の近くと `.env.example` に名指しし、ガイド自体が alert を組める経路を
      示すようにする。`oauth.login` が携える `bypass` フィールドは、迂回による許可が残す唯一の記録
      なので、`BAJUTSU_OAUTH_ADMIN_TEAMS` をサインインの資格情報として使う場合は、これをアラートの
      対象にするよう明記する。
- [x] テストを追加する。`orgs:` に一致するエントリがない場合と、`orgs:` ブロック自体がない場合の
      両方で、admin Team のメンバーがサインインできることを確認します。どちらの場合も解決したロールが
      admin になることを確認します。Organization ゲートにも admin Team のリストにも一致しない login が
      引き続き拒否され、`orgs:` が一致しなかった5つの形（config がまったく bind されていない場合を、
      bind されたが読み込みに失敗した場合と区別することを含む）のうちどれだったかを名指しして
      `"oauth.denied"`
      を記録することを確認します。OAuth が構成されていない場合、実在する CSRF の state 不一致、state を
      まったく持たない probe、一致する偽の値で CSRF の確認を突破した場合、例外を発生させた
      exchange、identity を返さなかった exchange のそれぞれが `"oauth.denied"` を `INFO` で記録する
      ことを確認します。構成が半端な OAuth デプロイが、トークンが構成されているときは共有トークンへの
      戻り先を名指しして警告し、構成されていないときは別に「unauthenticated」を名指しして警告する
      こと（完全に未設定のデプロイはどちらも警告しない）ことを確認します。`_build_state` が、出力した
      内容と一致する `startup_warnings` を返すこと、各エントリの `check` フィールドが4つの確認のうち
      どれが発火したかを、メッセージだけでなく区別することを確認します。`_emit_startup_warnings` が、
      集約した各警告を実際に `oplog.log_event` を
      通じて `"server.startup_warning"` の下で、`check` を自身のフィールドとして携えたまま
      再発行することを確認します。これにより、後で
      `oplog.EVENTS` からその名前が改名または削除されると、boot 時の `ValueError` だけでなく、
      失敗するテストが存在するようになります。独立したテストが、手作業で組み立てた
      `ServeState.startup_warnings` に対して `_emit_startup_warnings` を直接動かすので、この網羅は
      どの起動時チェックがどう書かれているか、あるいはまだ存在するかとは無関係に残ります。もう1つの
      独立したテストが、警告すべきものが何もない場合の no-op 経路も固定します。改名した変数が
      複数の Team を持つリストとしてパースされることを
      確認します。迂回した admin が `default` org に配置されることを確認します。既存メンバーの記録済み org が
      config 自体の読み込み失敗を乗り越えて残る一方で、config がまったく bind されていない場合
      （guard の2つの側を証明します）や、実際に revoke されたメンバーの次回のサインインでは
      `default` に再解決され、固定され続けないことを確認します。`/user/orgs` の一時的な取得失敗に遭遇した
      `githubOrgs` だけの member も同様に `default` へ再解決されること（`[]` が正真正銘の zero-orgs
      login と見分けられないことの、受け入れた自己修復コスト）を確認します。廃止した単数形の変数が、新しい
      複数形の変数も設定されている場合でも警告することを確認します。
      HTTP transport を通じて end to end で確認します。`orgs:` のどのエントリにも一致せず、迂回だけで
      許可された login が、セッションを得るだけでなく、admin 限定のエンドポイント
      （`POST /api/apikey`）に実際に到達できることを確認します。

## 参考

- [BE-0313 — GitHub Organization メンバーシップと Team ベースの RBAC を serve に導入](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md)。
  この項目が隙間を狭める、Organization と Team に基づくサインインゲートとロール判定です。この項目が
  塞ぐ隙間を名指ししている「admin はサーバー全体で1つの階層のまま」の節を含みます。
- [`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)。`oauth_callback` のサインインゲートと
  `role_for` のロール判定であり、この項目がどちらにも変更を加えます。
- [`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)。`identity_matches_org` と
  `org_for_identity` であり、admin Team の迂回はこれらと並んで動きます。
- [`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py)。`_fetch_orgs` と
  `_fetch_teams` であり、この項目はどちらの docstring も更新します。GitHub API 障害時に同じ
  fail closed の挙動が、迂回だけで許可される login にとってはサインインそのものを失わせるように
  なるためです。
- [`bajutsu/serve/state.py`](../../bajutsu/serve/state.py)。`SessionManager` であり、この項目は
  その `oauth_admin_team` フィールドを `oauth_admin_teams` に改名し、Team の tuple へ広げます。
  `ServeState` には、この項目が新しい `startup_warnings` フィールドを追加します。
- [`bajutsu/serve/oplog.py`](../../bajutsu/serve/oplog.py)。`oplog.EVENTS` であり、この項目は
  `"oauth.denied"` と `"server.startup_warning"` を追加します。
- [`bajutsu/serve/__init__.py`](../../bajutsu/serve/__init__.py)。`_build_server_state` の4つの
  起動時チェックと、`serve()` が `_configure_oplog` の後にそれらを再発行する部分です。
- [`docs/ja/self-hosting.md`](../../docs/ja/self-hosting.md)。self-hosting ガイドの GitHub OAuth の節
  です。この項目が塞ぐ隙間は、この項目が取り除くまで、ここに「まず上記のサインインのゲートを
  通過する必要があります」として書かれていました。

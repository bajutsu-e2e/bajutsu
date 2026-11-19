[English](BE-0352-admin-team-bootstrap-bypass.md) · **日本語**

# BE-0352 — admin 用 GitHub Team の環境変数が Organization メンバーシップのサインインゲートを迂回する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0352](BE-0352-admin-team-bootstrap-bypass-ja.md) |
| 提案者 | [@paihu](https://github.com/paihu) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0352") |
| 実装 PR | [#1485](https://github.com/bajutsu-e2e/bajutsu/pull/1485) |
| トピック | Web UI のホスティング |
| 関連 | [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) |
<!-- /BE-METADATA -->

## はじめに

この項目は、serve のサーバー全体で共通の admin 用 GitHub Team を、`orgs:` 設定にその admin 自身の
GitHub Organization のエントリがない場合でも、サインインできるようにします。`orgs:` 自体が存在しない
場合も含みます。

admin Team を指定する環境変数の名前は、`BAJUTSU_OAUTH_ADMIN_TEAM` から `BAJUTSU_OAUTH_ADMIN_TEAMS` に
変更します。新しい変数はカンマ区切りのリストなので、1つのデプロイに複数の Team を指定できます。この
変数への一致は、GitHub OAuth のサインインゲートを直接通過させます。これまでは、ゲートをすでに通過した
login のロールを決めるだけでした。

## 動機

[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) は、GitHub OAuth の
サインインを、`orgs:` の下に宣言した Bajutsu org へのメンバーシップでゲートします。`members` への明示的な
列挙か、`githubOrgs` に挙げた GitHub Organization のメンバーであれば、ゲートを通過します。このゲートを
通過した login だけが、`BAJUTSU_OAUTH_ADMIN_TEAM` との一致を確認され、admin ロールを得ます。

BE-0313 自身の設計文書は、この結果として生じる隙間を名指ししています。admin Team のメンバーであっても、
その GitHub Organization がどの org の `githubOrgs` や `members` からも参照されていない場合を考えます。
どの `orgs:` エントリにも載らない、運用専用の GitHub Organization に属する場合がその例です。このメンバーは、
admin Team が参照される前に、サインインの段階で拒否されます。admin になるどころか、サインインすら
できません。

同じ拒否は、さらに2つの場合に、すべての login に及びます。1つ目は、config に `orgs:` ブロックが
まったくない場合です。2つ目は、`members` や `githubOrgs` のエントリが、デプロイの運用者をまだ網羅して
いない場合です。`identity_matches_org` は、空の org 名簿に対しては誰も通しません。そのため、`orgs:`
ブロックがない状態、または運用者がまだ編集を終えていない状態で起動した GitHub OAuth デプロイは、admin を
含む全員を締め出します。

この失敗は、それを直すべき本人に降りかかります。壊れた、あるいは未完成の `orgs:` ブロックは、admin が
直すべき種類の設定ミスです。admin は、サーバーの向き先を修正済みの config ソースへ張り替える
（`POST /api/config`）か、修正済みのバンドルをアップロードする（`POST /api/upload`）ことで直します。
しかし BE-0313 の設計は、ブロックがすでに壊れた状態になると、それを直すためにサインインする経路を
残していません。デプロイは、serve 自身の外側で環境変数や config ストアを手作業で編集されるのを待つしか
ありません。Web UI から修正できる admin は、誰もいません。

## 詳細設計

### admin 用環境変数をカンマ区切りのリストにし、その確認をより早い段階に移す

`BAJUTSU_OAUTH_ADMIN_TEAM`（単数形）を `BAJUTSU_OAUTH_ADMIN_TEAMS`（複数形）に改名します。値は
`"<GitHub organization>/<team slug>"` の形で書いた GitHub Team のカンマ区切りリストです。これにより、
1つのデプロイが複数の admin Team を指定できます（例えば、運用対象の GitHub Organization ごとに
1つずつ）。

新しい変数を追加せず改名するのは、サインイン時に異なる挙動を持つ2つの admin Team 用変数を残さない
ためです。この項目は、admin Team のメンバーシップがサインイン時に持つ意味を変えます（後述）。第2の
変数を追加すると、既存の単数形の変数にも、同じ挙動の変更を古い名前のまま加えることになります。改名
すれば、名前の変更と挙動の変更が、2回ではなく1回の変更で済みます。

#### 4つの起動時チェック

admin 全員を跡形もなく失うことを防ぐため、この解析には4つの起動時チェックを追加します。3つは
GitHub OAuth が構成されていること（`oauth is not None`）を条件とし、1つは逆の条件を持ちます。残りの
3つは `oauth` の組み立て方に依存するため、逆の条件を持つ4つ目から先に説明します。

**半端な OAuth 構成（`"oauth_half_configured"`）。** `oauth` は
`GitHubOAuthClient(...) if cid and secret and redirect else None` として組み立てられます。そのため、
`BAJUTSU_OAUTH_GITHUB_*` のどれか1つでも未設定または誤りがあると、`oauth` は `None` に潰れます。他の
3つのチェックから見ると、この状態は、変数を1つも設定していない意図的なトークン認証専用デプロイと
見分けがつきません。`BAJUTSU_OAUTH_ADMIN_TEAMS` は正しく設定しても、GitHub 側の変数を1つ書き間違えた
運用者を考えます。他の3つのチェックは何も出力せず、GitHub のサインインはすべて 404 になります。

この先は、共有トークンがあるかどうかで分かれます。

- トークンが設定されている場合、これは締め出しではありません。`POST /api/login` は `oauth is None`
  のときにだけ有効なので、デプロイは置き換えたはずの共有トークンのログインに静かに戻ります。トークンで
  発行したセッションには identity が無いため、`forbidden_for_role` が短絡し、そのセッションはすべて
  全権を持ちます。運用者は GitHub OAuth にゲートされていると思い込んだままです。
- `BAJUTSU_SERVE_TOKEN` も未設定の場合、戻り先すらありません。`SessionManager.check_token` は
  `self.token is not None and secrets.compare_digest(...)` なので、`POST /api/login` は 401 になります。
  さらに、両方の transport が同じ `token is None` を理由に、認証と RBAC のゲートそのものをスキップします
  （`handler.py` の `_gate`、`server/app.py` のミドルウェア）。`_ADMIN_PATHS` を含むすべての
  エンドポイントが、認証なしで応答します。共有トークンへの後退よりも、さらに悪い形です。

このチェックは、`oauth is None` かつ GitHub 側の3変数のうち少なくとも1つが設定されている場合にだけ
発火します。意図的なトークン認証専用デプロイではなく、構成が半端な状態です。メッセージは、GitHub
OAuth が部分的にしか構成されていないこと、そして3変数すべてが設定されるまで GitHub のサインインが
404 になることを出力します。このデプロイが実際に落ち込んだ2つの戻り先のどちらかも、名指しします
（`token` は `_build_server_state` の既存の引数です）。さらに、`cid` / `secret` / `redirect` のうち
どれが未設定かを、ちょうど名指しします。3つ組をまるごとチェックリストとして出力することはしません。
変数の「名前」を書き間違えた運用者（`_REDIRECT_URI` を `_REDIRECT_URL` と書いた、など）は、`.env`
をさっと読んだだけでは3つとも存在しているように見えます。その運用者は、自分のファイルではなく、GitHub
側の OAuth app 登録を探しにいってしまいます。

**廃止した名前がまだ設定されている（`"admin_team_retired_name"`）。** `_build_server_state`
（[`bajutsu/serve/__init__.py`](../../bajutsu/serve/__init__.py)）は、GitHub OAuth を構成していて、
廃止した `BAJUTSU_OAUTH_ADMIN_TEAM` がまだ設定されているとき、stderr に警告を出します。警告は、
`BAJUTSU_OAUTH_ADMIN_TEAMS` も設定されているかどうかを問わず出ます。

ありがちな移行ミスは、新しい変数を未設定のまま残すことよりも、古い変数を削除せずに新しい変数を追加
してしまうことです。運用者は、自分が追加している Team は覚えていても、古い単数形の変数を削除しなければ
ならないことまでは覚えていないものです。その結果、古い Team のメンバーは admin でなくなっているのに、
`BAJUTSU_OAUTH_ADMIN_TEAMS` 自体は空ではない状態が、静かに生まれます。この通知を「空である」ケースだけ
に結び付けると、このミスを見逃します。そのため、このチェックは廃止した変数だけを見る独立したチェック
とし、新しいリストが何に解決されるかとは無関係に動かします。

**リストが空（`"admin_teams_empty"`）。** 別の警告が、解析後の `oauth_admin_teams` が空になっている
ときに出ます。原因は、デプロイが `BAJUTSU_OAUTH_ADMIN_TEAMS` を一度も設定していない場合と、廃止名だけが
設定されている場合の、どちらでもあり得ます。後者では、「検討した代替案」で述べる完全な切り替えにより、
そのデプロイは admin Team を1つも持たなくなります。どちらの場合も、他に見える症状は、admin の操作
すべてで説明のない 403 が返ることだけです。このリストは、いまサインインの資格情報でもあるため（後述）、
それを直すためにサインインできる admin も残っていません。

**不正なエントリ（`"admin_teams_malformed"`）。** 3つ目の警告は、`"<GitHub organization>/<team slug>"`
の組として正しい形でないエントリに対して出ます。判定は、organization 側と slug 側のどちらかが空である
場合や、内部に空白がある場合を拒否する正規表現で行います。`/` の個数は数えません。スペースやセミコロンで
区切ったリストは、実在の Team には一致しない、1つの不正なエントリとしてパースされます。これは、別の
間違いで到達する、同じ「admin がいない、しかも原因が見えない」失敗です。

この正規表現は、どちらの側の大文字も拒否しません。`in_admin_team`（後述）がメンバーシップ判定の両辺を
case-fold するため、GitHub 自身が保存している大文字小文字と異なるエントリでも一致し続けます。GitHub の
UI に表示される Team の表示名からそのまま書き起こした slug が、その例です。ここで拒否すると、すでに
機能しているエントリを、運用者に「直す」よう促すことになります。さらに悪いことに、本当に壊れたエントリが
隠れている、まさにこのリストに対する警告を、無視する習慣を運用者に付けてしまいます。

**4つのチェックに共通する挙動。** 4つとも、例外を発生させず警告だけを出します。config の書き間違いに
よって、デプロイが起動できなくなるのではなく、admin なしの状態に落ちるようにするためです。これは、
この module が読む他の運用者向け環境変数とは、意図的に異なる選択です。`BAJUTSU_SESSION_TTL`、同時実行数の
上限、`BAJUTSU_RUN_RETENTION_DAYS` は、いずれも不正な値で例外を発生させます。起動できないサーバーは、
admin がいないサーバーと同じくらい直しにくいからです。ただし、ここでの間違いは、それらとは異なり、
外側から直せるものでもあります。

不正なエントリは、リストから取り除かず残します。取り除くと、admin の名簿を構文的に正しいものだけへ
静かに絞り込むことになります。それは、警告がすでに報告している失敗の上に、もう1つの静かな失敗を重ねる
ことです。

4つのうち3つのチェックは、GitHub OAuth を構成しているときにしか動きません。トークン認証だけのサーバー
バックエンドでは、`BAJUTSU_OAUTH_ADMIN_TEAMS` は何も決めません。そこに残った古い値や不正な値は、静かな
ままであるべきです。そのデプロイ形態が持ったことのない admin ロールについて、警告してはいけません。

#### 起動時警告の構造化した再発行

4つのチェックは、`_build_server_state` の中で、まだ何も構成されていない段階で動きます。その時点で選べる
手段は、素の `print(..., file=sys.stderr)` しかありません。出力は構造化されていないテキストです。登録
された `event` も、correlation フィールドも、redaction もなく、ログが立ち上がった後の JSON 形式の
`oplog` の stdout 書き込みと混在します。ログ pipeline が捨ててしまうか、パースに失敗するのは、この形
です。しかも、これは運用者が最も alert を組みたい状態、つまり「このデプロイには admin がおらず、
サインインして admin を得る手段もない」という状態そのものです。

- **集約。** `_build_server_state` は、出力する各メッセージを、新しい `ServeState.startup_warnings`
  フィールドに集約します。これは裸のメッセージではなく、`(check, msg)` の
  `tuple[tuple[str, str], ...]` です。4つのチェックは、以下で述べる1つの `"server.startup_warning"`
  event を共有します。メッセージだけでは、運用者の alert が、いつか誰かが言い回しを変えると静かに壊れる
  自由文の部分一致に頼るしかなくなります。
- **識別子 `check`。** `check` は、`"oauth_half_configured"`、`"admin_team_retired_name"`、
  `"admin_teams_empty"`、`"admin_teams_malformed"` という安定した値です。alert はこの値をキーにできます。
  意図的に OAuth を admin Team なしで運用しているデプロイは、他の3つを黙らせずに、この1つだけを
  抑制することもできます。
- **`_warn` クロージャ。** `_build_server_state` の内側にあるローカルな `_warn(check, msg)` クロージャが、
  この対応を省略不能にします。4つのチェックは、それぞれ `print(...)` と
  `startup_warnings.append((check, msg))` をそのまま繰り返す代わりに、これを1回呼ぶだけです。後から
  追加される5つ目のチェックが、警告を print だけして集約を忘れる、ということがなくなります。そのような
  失敗は「印字されたか」という手動確認を通過しつつ、`_emit_startup_warnings` からは静かに漏れ続けます。
- **再発行。** `serve()` は、`_configure_oplog` の直後に、新しい `_emit_startup_warnings(state)` を
  呼びます。これは `restore_persisted_provider_settings` がすでに使っている配置と同じで、理由も同じです
  （「malformed-file の警告が live のログシンクに届くように」）。この関数は、集約した各警告を
  `oplog.log_event` を通じて再発行し、登録済みの `msg` と並べて、`check` をフィールドとして渡します。
  event は、`oplog.EVENTS` の新しいエントリ `"server.startup_warning"` です。

`_emit_startup_warnings` は、単独で呼び出せる boot seam です。その2つの隣人がすでに立てているパターンに
合わせたもので、`serve()` 内のインラインな loop ではありません。`serve()` は実際にサーバーの loop を
走らせる関数であり、高速なテストスイートでは実行されません。インラインな loop だと、テストの網羅は
「クラッシュしないこと」だけになってしまいます。`log_event` は、未登録の event に対しては仕様として
`ValueError` を発生させます。後になって `"server.startup_warning"` が `oplog.EVENTS` から改名または
削除されても、テストスイート全体は green のままです。クラッシュが表面化するのは、再発行すべき起動時
警告を持つ、実際のデプロイだけです。そのデプロイは、起動時に `_configure_oplog` の後、
`restore_persisted_provider_settings` の前でクラッシュします。まさにこの項目が助けようとしている構成
ミスのデプロイが、admin を欠くだけでなく、まったく起動できなくなります。専用のテストが
`_emit_startup_warnings` を直接動かし、メッセージだけでなく各レコードの `check` フィールドも固定します。

`print` の呼び出しはそのまま残します。それらが動く時点では、何も構成されていません。store もデータ
ベースも無い、まったく壊れたデプロイが起動したときに、何かを見る手段は、依然としてそれしかありません。

[`docs/self-hosting.md`](../../docs/self-hosting.md)は、`"server.startup_warning"` と `"oauth.denied"`
の両方を、admin Team の移行手順の近くでも名前で挙げます。*運用ログ* 節の event 一覧に挙げるだけでは
ありません。移行手順を読む運用者が、「ログの最初の数行を読んでください」だけでなく、alert を組める
経路も見られるようにするためです。

#### サインインゲートと `"oauth.denied"` の記録

`oauth_callback`（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）は、Organization
メンバーシップのゲートを実行する前に、すでに login の GitHub Team メンバーシップを取得しています
（`identity.teams`、`fetch_identity` 経由）。この取得は、後段の `editorTeam` によるロール判定にも使われて
います。ゲートと Team の取得は、これまで一緒に使われるよう順序づけられていなかったにすぎません。

この項目は、`identity_matches_org` と並ぶ確認を1つ追加します。login の `identity.teams` が、設定した
admin Team のリストと重なる場合、`identity_matches_org` の結果にかかわらず、サインインゲートを通過
します。これにより、admin Team のメンバーは、自身の GitHub Organization を挙げる `orgs:` エントリが
なくても、あるいは `orgs:` そのものがなくても、サインインできます。

どちらの確認にも一致しない login は、今日と同じ 403 で拒否されます。ただし、もはや静かにではありません。
`oplog.log_event` を通じて記録されるようになり、`oplog.EVENTS` の新しいエントリ `"oauth.denied"` として
記録します。`"oauth.login"` には折り込みません。`"oauth.login"` は「ログイン件数」を意味するままに
しておきます（後述）。拒否は、この項目が回復可能にしようとしている失敗そのものです。壊れた、あるいは
存在しない `orgs:` ブロックに、一致する admin Team もない状態です。サインインの成功と同じ audit 相当の
可視性が必要です。素の 403 では、運用者が「サインインできない」というユーザの報告と突き合わせる手がかりが
何もありません。

**`orgs:` が不一致になった原因の名指し。** 拒否メッセージは、5つの形のうちどれが `orgs:` を不一致に
したのかを名指しします。下記の迂回による許可の成功メッセージも、同じ5つの形を名指しします。共有の
`_unmatched_org_cause` helper が両方のために原因を計算するので、この項目のある改訂が一時的に許して
しまったように、2つの記録がずれることはありません。拒否された login のほうこそ、「サインインできない」
という報告の出どころになりやすいものです。迂回による許可が受けるのと、少なくとも同じ triage を受ける
必要があります。

5つの形のうち3つは、`orgs` を `{}` に潰し、admin 以外のすべての login を無条件に拒否します。

- config がまったく bind されていない。
- config の読み込みが失敗した。
- config が `orgs:` ブロックをまったく宣言していない。

実際には読まれていない org 名簿や、わざと空のままにされている org 名簿を非難するメッセージは、運用者を
間違った修正に向かわせます。「`orgs:` ブロックがない」という形は、例外的なケースではありません。この
項目自身の見出しの状況そのものです（*動機*: 「`orgs:` ブロックを持たずに起動する GitHub OAuth
デプロイは……他の全員と一緒にすべての admin を締め出します」）。

**admin に関する句の名指し。** メッセージ末尾の admin に関する句にも、同じ注意を払います。「no admin
Team matched」は、本物のメンバーシップの不一致のように読めます。`admin_teams` が使えない状態、つまり
空であるか、全エントリが不正な形式である場合にも、同じ文言が出てはいけません。この状態は、起動時の
`admin_teams_empty` / `admin_teams_malformed` チェックが警告する状態そのものです。`orgs:` を直しに
サインインできる admin が、誰も残っていない状態でもあります。そこで、この形では、メッセージが「no usable
admin Team is configured」と名指しします。条件には、下記のレベルと同じ `admin_teams_unusable` という
述語を使います。メッセージとレベルがずれることはありません。

**サインインを成功させずに終える5つの経路。** `oauth_callback` には、サインインを成功させずに終える
経路が5つあり、この項目はその5つすべてを記録します。org ゲートの拒否が5つ目です。残りの4つも、
同じ `"oauth.denied"` イベントを記録します。上で述べた理由が、そのまま当てはまるためです。記録が
なければ、素の 404 や 403、502 では、ユーザの「サインインできない」という報告と突き合わせる手がかりが
ありません。

- OAuth が構成されていない。構成が半端なデプロイでは、GitHub のサインインがすべて 404 になり、共有
  トークンのログインが静かに再び有効になります。これは締め出しではありませんが、運用者が知る必要の
  ある危険です。
- CSRF の state 不一致。
- exchange が例外を発生させた。
- exchange が identity を返さなかった。

この4つの早い段階の記録は、すべて `INFO` で発行します。どれも、実在する GitHub account を示す情報を
必要としません。

- `oauth is None` は、デプロイの静的な性質であり、request ごとの信号ではありません。
- `state_param` と `state_cookie` は、どちらも呼び出し側が与える値です（query の値と、呼び出し側自身の
  `Cookie:` header）。攻撃者は、両方に同じ偽の値を送るだけで、`secrets.compare_digest` を無料で突破
  できます。そして、実際の認証なしに CSRF チェックを通過し、でたらめな `code` が引き起こす、いずれかの
  exchange 失敗の分岐に到達します。

これら4つのどれかに `WARNING` を与えると、匿名の呼び出し側が量を決められる、request ごとの信号に
なります。この endpoint への loop が、本物の拒否を、誰でも引き起こせる雑音の下に埋めてしまいます。
`INFO` で記録しても、記録自体は残ります（素の 404 や 403、502 のままにはなりません）。運用者の
`WARNING` 別 alert が除外しなければならない信号にもなりません。state の不一致が繰り返されるのは、
login-CSRF 攻撃の兆候として監視する価値があります。ただし、これは多数の記録についての *率* の主張で
あり、記録1件ごとの性質ではありません。その正しい住み処は、運用者が閾値を設定できる counter であり、
この event の log level ではありません。

これら4つの早い段階では、`login` がまだわかりません。そのため、記録には `actor` フィールドが付きません。
`actor` が付くのは、後段のゲート由来の拒否と、すべての成功したサインインだけです。

**org ゲートの拒否のレベル。** org ゲートの拒否そのものも、無条件に `WARNING` になるわけではありません。
`GET /api/oauth/login` は認証不要であり、GitHub OAuth app は、このデプロイのメンバーだけでなく、GitHub
の任意のユーザを認可します。無料の account を持つだけの誰かも、上記の4つと同じくらい簡単に、この分岐へ
届きます。「実際の GitHub exchange が必要」という条件は、量を account 1つ分に抑えるだけで、デプロイの
運用者だけには絞りません。

`WARNING` を取っておくのは、実際にページングすべき唯一の形、つまり `admin_teams_unusable`
（`authz.py`）が真になる場合だけです。これは、`admin_teams` が空である場合、あるいは全エントリが整形式の
判定に失敗する場合の、どちらでも真になります。`not admin_teams` だけでは足りません。空ではないが全体と
して不正なリストは、`_build_server_state` の `admin_teams_empty` チェックが起動時に警告する締め出しと、
機能的には同じものです。スペース区切りの値が1つの `"a/b c/d"` エントリにパースされる場合が、その例で、
この項目自身の malformed-entry テストがまさにこの形を使っています。

この整形式判定のパターンは、`authz.py` にある1つのモジュールレベルの定数（`ADMIN_TEAM_ENTRY_RE`）に
まとめます。`_build_server_state` は、自身の `admin_teams_malformed` チェックのために、これを import
します。2つ目の正規表現リテラルは、最初のものからずれていく可能性があります。`in_admin_team` と
`_unmatched_org_cause` を切り出したのも、同じ理由です。通常の拒否、つまり構成済みの admin Team に単に
この login が一致しなかった場合も、記録は残ります。上記の4つの早い失敗と同じ理由で、`INFO` で記録
します。

#### 成功したサインインの `"oauth.login"` 記録

サインインが成功するたびに、`oauth_callback` は `oplog.log_event`
（[`bajutsu/serve/oplog.py`](../../bajutsu/serve/oplog.py)）を通じて記録します。すでに予約済みの
`"oauth.login"` イベントを使い、login 自体を `actor` の correlation フィールドに入れます。素の logging
呼び出しではありません。この記録は、serve の他のあらゆる運用上重要な記録と同じ、登録されたイベント名、
redaction、correlation のフィールドを持ちます。`event` で絞り込む運用者の alert からも、実際に見えます。

`"oauth.login"` は、この項目より前から `oplog.EVENTS` に予約されていましたが、実際に発行されたことは
一度もありませんでした。この項目が初めてこのイベントを発行するようにします。しかも、迂回した login
だけでなく、すべてのサインインで発行します。迂回だけを記録するイベントだと、`event=oauth.login` は
「ログイン件数」ではなく「迂回件数」を意味してしまいます。その event 名で alert を組む運用者が期待する
意味とは、逆です。

record ごとの `bypass` フィールドは、admin Team の迂回が login を許可した場合にだけ `True` になります
（`orgs:` が許可した場合は `True` になりません）。メッセージもそれに応じて変わります。迂回の場合は
「admin-Team bypass admitted …」という文言で、それ以外は単純な「… signed in」という文言です。

**レベル。** レベルは `bypass` をそのまま映すわけではありません。この項目自身の指針は、どの `orgs:`
エントリにも載らない運用専用の GitHub Organization に属する admin Team を推奨しています。そこでは
`bypass` が**すべての** admin サインインで、永続的に `True` になります。これは正しく構成されたデプロイの
通常の運用状態であり、ページングすべきものではありません。`WARNING` が発火するのは、org モデル自体が
使えない場合だけです。

- `parsed is None`（config が bind されていない、あるいは読み込みに失敗した）。
- config は読み込めたが、`orgs:` ブロックをまったく宣言していない。

これらの形では、迂回が、admin Team のメンバー以外は現時点でサインインして直せないデプロイへ、login を
許可しています。他のすべての迂回と、通常のサインインは、すべて `INFO` のままです。`bypass` フィールドは、
すべての記録で定数の `True` になるのではなく、実際の情報を運びます。

**迂回メッセージが名指しする5つの形。** 迂回メッセージは、`matched_org` が `False` になる5つの経路の
うち、どれだったかを名指しします。

1. config がまだ bind されていない。
2. config の読み込みが失敗した。
3. config が `orgs:` ブロックを宣言していない。
4. GitHub がこの login に対して org を1つも返さなかった。
5. `orgs:` のどのエントリにも一致しなかった。

`WARNING` に反応した運用者は、org ゲートがこの login を認めなかったという事実だけでなく、その理由も
知る必要があります。最初の2つは、config を調べるべきです。残りの3つは、org 名簿を調べるべきです。1つの
固定された文言では、この区別が隠れます。

`_unmatched_org_cause` は、最初の2つを、`state.config` そのものが `None` かどうかで見分けます。
`load_serve_config_file` は、config path がまったく bind されていない場合に、同じ `None` をすぐに返し
ます。これは、`serve()` 自身が通常の状態として扱う、エラーのない起動時の状態です。bind されている path
の読み込みが失敗した場合にも、`None` を返します。両方を「config の読み込みが失敗した」の1つにまとめる
と、運用者は、まだ存在しないはずのファイルのファイルシステムエラーを探すことになります。

上記の拒否経路も、同じ理由で同じ5つの形を名指しします。共有のモジュールレベル helper
`_unmatched_org_cause` が、両方の呼び出し元のために、一度だけ原因を計算します。これは、下記で
`in_admin_team` を切り出しているのと同じ理由です。同じ5分岐の独立した2つのコピーを、後から別々に編集
すると、まさにこの分岐の組自体が一度そうなったように、ずれていく可能性があります。6つ目の形が片方の
コピーにだけ入り、もう片方には入らないかもしれません。拒否された login が、最初の5つのうち古くなった
1つを伝えられるかもしれません。

迂回は、依然として `orgs:` が認可しなかった唯一のサインイン経路です。誰がいつサインインしたかを追跡
したい運用者にとって、記録が何も残らない唯一の経路にもなりかねません。`bypass` フィールドは、同じ
イベント stream の中で、迂回を通常の org ゲート済みログインと区別できるようにします。

#### 共有の `in_admin_team` helper と case-fold

ゲートの確認が使うメンバーシップ判定は、login の Team のどれかが、設定済みの admin Team 一覧に含まれる
かどうかです。`role_for` も、admin ロールを解決するときに同じ判定を使います。この項目は、同じ式を
2箇所に書く代わりに、1つの共有ヘルパー `in_admin_team` にまとめます。

約120行離れた2つの関数に同じ規則が2つ存在すると、どちらか一方だけへの後からの独立した変更で、ずれが
生じかねません。ゲート側のコピーだけが通した login が、role 側のコピーを通らない場合を考えます。その
login はサインインしても、viewer に解決されます。`orgs:` が一度も認可していない login のためのセッション
であり、その login を通した理由そのものである admin 権限は持ちません。1つのヘルパーにまとめれば、この
ずれは構造的に起こり得ません。

`in_admin_team` は、このメンバーシップ判定の両辺を case-fold（大文字小文字を無視）して比較します。
GitHub は、org の login と Team の slug を、どちらも大文字小文字を無視して解決します。Team の slug は
常に小文字化されますが、実在の GitHub org login は大文字混じりで保存されることがあります。そのため、
`admin_teams` のエントリの organization 側が、GitHub に保存されているどの大文字小文字であっても、login
の `identity.teams` の exact-case のメンバーシップと一致し続けなければなりません。逆方向についても同様
です。

case-fold がなければ、この項目自身のサインイン迂回は、潜在的な大文字小文字の罠を抱えます。たとえば次の
2つです。`admin_teams` のエントリが、たまたま大文字混じりで表示される GitHub の org ページから書き
起こされた場合。org の rename で保存済みの大文字小文字が変わる前に、エントリがコピーされた場合。どちらも、
許可するはずの login に静かに一致しなくなります。これは、上の malformed-entry の警告がすでに防いでいる
「admin がいない、しかも見える原因がない」という失敗と同じです。大文字小文字の不一致は構文的には整った
形なので、その確認では届きません。

`editorTeam`（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py) の `role_for`）も、同じ形を
しており、同じ GitHub 側の大文字小文字と比較されます。同じ潜在的な罠を抱えています。この項目は、これを
直さずに残します。その値がさらされている度合いが低いからではなく、スコープ上の判断です。`editorTeam`
にも case-fold を広げることは、この項目のサインイン復旧という範囲の外にある、ロール判定そのものの変更に
なります。

case-fold は、空の Team 名を一致に変えることはありません。`admin_teams` には、カンマ分割時の
`t.strip()` によるフィルタがあるため、`""` が含まれることはないからです。また、下記の入れ子 Team に
対する保証にも影響しません。その保証は、区切り文字の個数ではなく、文字列としての完全一致に基づきます。
`"acme-gh/parent/child"` は、case-fold の有無を問わず、`"acme-gh/parent"` とは異なる文字列です。

### ロール判定の形は変わらず、名前だけが変わる

`role_for`（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）は、すでに単一の admin Team
用パラメータから、admin を editor より上、editor を viewer という基底ロールより上に位置づけています。
この項目は、そのパラメータを単一の Team からリストへ広げるだけです。単一のメンバーシップ判定の代わりに、
上記の共有ヘルパー `in_admin_team` を通じて、`identity.teams` との積集合を確認します。

上のサインインゲートが、迂回した login は一致する Team を持つことを、すでに確立しています。`role_for` は、
その login を、今日の他の admin Team メンバーと同じ方法で admin に解決します。迂回した場合のための別の
ロール経路は追加しません。

`role_for` と、後述する org の配置先の決定は、どちらも `oauth_callback` の
`if state.repository is not None:` ブロックの中でだけ動きます。他の admin Team メンバーに対して今日
すでに動いているのと同じ範囲であり、この項目はどちらにも新しい呼び出しを追加しません。データベースを
配線していないデプロイでは、どちらも動きません。その場合、迂回が影響するのは、サインインゲート自体
だけです。`state.repository is None` によって、`forbidden_for_role` がすでに短絡しています（BE-0313）。
そのため、そのデプロイでは、サインインした user が誰でもロールを問わずフルアクセスを得ます。迂回した
login が得るのは、そのゲートが他のどの許可済み login にもすでに与えているサインインだけです。

### 迂回した admin の配置先

Organization メンバーシップではなく Team の迂回によってゲートを通過した admin は、共通の `default` org
に配置します。`org_for_identity` が、`members` と `githubOrgs` のどちらにも一致しない login を置く先と、
同じ配置です。

BE-0313 自身の記述は、`orgs:` ブロックを宣言すると `default` org は「OAuth サインインでは到達不能に
なる」と述べています。そこへ落ちるはずだった login が、配置が計算される前にすべて拒否されるためです。
この項目は、その記述を無条件には成り立たなくします。迂回した admin は、他のどの org にも自分の login が
一致しないからこそ、`default` に到達します。`default` 自体は、他のどの org にも属さない
[target](../../docs/ja/glossary.md#target-app-device)以上の特別な扱いを持ちません。admin の
`_ADMIN_PATHS` による強制は、すでに org を問わずインスタンス全体に及んでいます（BE-0313「admin は
サーバー全体で1つの階層のまま」）。そのため、この配置は、BE-0313 がすでにサーバー全体のものとした
admin ロール以上の何かを、迂回した admin に新たに与えるものではありません。

#### 読み込みに失敗した config が既存メンバーを移動させてはならない

「他のどの org にも自分の login が一致しない」というのは、この `default` が想定するケースです。迂回が
login を許可する経路は、それだけではありません。config 自体の読み込みが失敗した場合も、
`identity_matches_org` には一致が見えなくなります。実在の org が本当にその login を名簿に持っている場合
にも、同じことが起こります。`load_serve_config_file` は、一時的なファイルシステムのエラーや config の
書き間違いに対して `None` へ fail closed し、`orgs` は `{}` に潰れます。これは、この項目自身が動機とする
状況、つまり壊れた `orgs:` ブロックが、実際に生じさせる失敗の形そのものです。

何の補正もなければ、この一度きりの不調によって、実在の org メンバーが、失敗のたびに `default` へ移され
ます。次のクリーンなログインで元に戻るまで、user 行、audit の帰属先、オブジェクトストレージの
プレフィックスが、すべて移動したままです。上記の配置ロジックは、`orgs:` がすでに認めている login に
対して、この結果を意図していません。

`oauth_callback` は、この1つの特定の失敗の形に限って、これを避けます。条件は2つの部分からなります。
`orgs:` ではなく迂回がその login を許可したこと、*かつ*、config path が bind されているのに読み込みに
失敗したこと（`parsed is None and state.config is not None`）です。この場合、あらためて org を計算せず、
`state.repository.user_org` にすでに記録されている org をそのまま使います。`org_for_identity` の `default`
という結果に落ちるのは、事前の記録が存在しない場合だけです。それが、この節が本来対象としている、正真
正銘の初回の bootstrap のケースです。

この guard の `state.config is not None` の側にも、独自の意味があります。`load_serve_config_file` は、
config path がまったく bind されていない場合にも、まったく同じ `parsed is None` を返します。これは
`serve()` 自身が通常の状態として扱う起動時の状態であり、一時的な障害ではありません。admin が config を
bind するまで、毎回のログインで `None` のままです。guard を `parsed is None` だけに絞ると、そのような
login の org は永久に固定され、config がついに bind されても、二度と再解決されません。

config は読み込めたが `orgs:` のどれにも一致しなかった login も、この保持のケースには当たりません。
config が答えを返しているため、その login は正真正銘、誰にも属していません。初回のサインインだからで
あっても、運用者がその後、設定済みのすべての org から外したからであっても、同じです。そのような login
は、他のあらゆる login と同じく、`org_for_identity` を通じて再解決されます。BE-0015 7c-2 が、ロール判定を
どのログインでも同様に行うよう、すでに求めているのと同じです。`orgs:` から外したことは、次のサイン
インで反映されなければなりません。脱退済みのメンバーが以前保持していた org に、固定され続けてはいけ
ません。

#### 保持ロジックを空の `identity.orgs` に結び付けない理由

この保持ロジックは、`identity.orgs` が空である場合には、意図的に結び付けていません。空の
`identity.orgs` は、`/user/orgs` の取得が失敗した場合の形でもあります（`_fetch_orgs` は `[]` へ fail
closed します）。同時に、GitHub のどの org にも本当に属していない login の形でもあります。`members:` に
列挙された bot や ops 専用の account が、その例です。`_fetch_orgs` の `[]` からは、この2つを見分ける
手立てがありません。

空の `identity.orgs` を条件に含めると、より悪い問題に置き換わります。そのような login が `members:` から
外された後、その org は永久に固定され続けます。将来のどのログインも、二度と非空の `identity.orgs` を
報告できないため、この guard から逃れられないからです。これは、数行下にある role に対する「毎回の
ログインで再計算する」という同じ原則と、静かに矛盾する、永続的に誤った状態です。

guard を `parsed is None` だけに絞ることで、より小さく、自己修復する代償を受け入れます。`githubOrgs`
だけでつながっている member が、本物の `/user/orgs` の障害に遭遇した場合、そのログインに限って `default`
へ移されます。次のクリーンなログインで元に戻ります。これは、正真正銘の un-claimed login と同じ扱いです。

この2つのケースを見分けられるようにするには、`_fetch_orgs` が失敗を `[]` ではなく `None` として報告する
ように変える必要があります。それは、`_paginate` の契約（`_fetch_teams` とも共有）、`Identity.orgs` の
型、テストスイート内のすべての fake `OAuthClient` を変更することになります。この項目が他に触れていない
コードへの変更であるため、ここでは行わず、フォローアップとして残します。

#### 引き継いだ落とし穴: `default` という名前の実在の org

この配置は、org モデルがもともと抱えている落とし穴を引き継ぐだけで、新しい落とし穴を作るわけでは
ありません。`DEFAULT_ORG`（[`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)）は、`"default"` と
いう文字列そのものです。デプロイがこの同じキーで実在の org を宣言することを、妨げるものは何もありません。
`targets_for_org` は、その org 自身の `targets:` の指定にかかわらず、target の所有権判定でこの文字列を
すでに特別扱いしています。org を文字どおり `default` と名付けたデプロイには、この項目より前から、すでに
衝突があります。この項目が作った衝突ではありません。

他のどの org にも一致しない迂回した admin は、同じキーの下に配置されます。実在の `default` org を持つ
デプロイでは、その admin の user 行、audit エントリ、オブジェクトストレージのプレフィックスは、中立な
受け皿ではなく、その org の下に置かれます。同じ衝突が、もう1つの呼び出し元に広がるだけです。admin
Team の迂回に頼るデプロイは、org を文字どおり `default` と名付けることを避けるべきです。target の所有権
に関する既存の理由に、この理由が加わります。

### 下層の Team 取得は fail closed のままだが、サインインそのものを失う場合がある

`_fetch_teams`（[`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py)）は、次の3つの
いずれに対しても、すでに空の Team 一覧へ fail closed します。

- 非200応答
- ページネーション途中のネットワーク障害
- パースできない本文

この項目は、GitHub への新しい呼び出しを追加しません。BE-0313 がすでに取得している、同じ
`identity.teams` を読むだけです。

org のゲートがすでに通している login にとって、この失敗が及ぼす影響は、今日と同じく editor と admin の
ロールだけです。サインインそのものが迂回だけに依存している login にとっては、同じ fail closed の挙動が、
いまはサインインそのものを失わせます。GitHub の API 障害が起きている間、その login は admin Team の
メンバーシップを証明できません。Organization メンバーシップのゲート単独が与える結果に戻ります。
`orgs:` からもその Organization に到達できなければ、拒否です。この項目は、この API 障害のケースに対する
login リストの代替経路を追加しません。詳しくは「検討した代替案」を参照してください。

### admin Team の各エントリは、ロールの対応づけだけでなくサインインの資格情報にもなる

この項目より前は、エントリがデプロイの管理していない GitHub organization を指していることがありました。
organization 側の書き間違いや、後から名前が変わった、あるいは削除された Team の organization がその
例です（GitHub は古い login を誰でも再登録できるようにします）。そのようなエントリは、単に誰にも一致
しませんでした。`role_for` は、`identity_matches_org` が org 名簿にいない login をすでに拒否した後に
しか、この値を確認しなかったからです。

この項目の後は、その同じエントリが、サインインゲートの一部になります。その organization の login を
管理する人は誰でも、一致する slug の Team を作り、迂回を通じてサインインし、admin に解決されます。
到達できる範囲は、`_ADMIN_PATHS` のすべてのエンドポイントです。`GET /api/config/content` も含まれ、
これは config の本文をそのまま返すため、そこに書かれた literal な secrets が漏れることもあります。

「admin 用環境変数をカンマ区切りのリストにし」の節にある不正なエントリの警告は、これを検出できません。
誰も管理していない organization を指すエントリでも、形としては正しく整っているからです。GitHub
organization を誰が管理しているかを、コードで確認する方法はありません。そのため、この項目が取れる対策は
運用面だけです。`deploy/self-host/.env.example` と self-hosting ガイド（両言語）は、各エントリが実際に
自分が管理する GitHub organization を指す必要があると、明記します。この値は、いまはロールの対応づけ
だけでなく、サインインの資格情報そのものだからです。

## 検討した代替案

- **Team ベースの迂回の代わりに、あるいはそれと並行して、login リスト形式の環境変数
  （`ADMIN_USERS` に相当するもの）を追加する方法**。login リストは、Team ベースの迂回では対処できない
  GitHub Teams API の障害を乗り越えられるため、検討しました。この項目には採用しません。BE-0313 は、
  Organization と Team による確認と並行して、login リストの許可リストを残すことを、すでに検討して
  退けています。もう1つの独立した付与経路を残すと、GitHub 自身のメンバーシップの記録を根拠とする
  ロールベースアクセス制御（RBAC）が取り除こうとしている、名簿のずれの問題を作り直すことになる、という
  理由です。その退けた対象は、すべてのロールに開かれた一般的な退避路でした。この項目が加える admin
  限定の狭い迂回は、別の問題への対処です。対象は、壊れた、あるいは存在しない `orgs:` ブロックからの
  復旧です。admin を、手作業で保守する名簿ではなく、GitHub の Team メンバーシップから導く点は変わりません。
  そのため、BE-0313 が退けた名簿のずれの問題を、作り直すことにはなりません。login リスト形式の変数は、
  Teams API 障害時の隙間が実際の問題だとわかれば、別の項目としてあらためて提案できます。
- **`BAJUTSU_OAUTH_ADMIN_TEAM` を単数形のまま残し、迂回専用の2つ目の環境変数を追加する方法**。
  採用しません。両方が存在するあいだ、サインイン時の挙動が異なる2つの admin Team 用変数が残ります。
  運用者は、どちらを使っているかを、ソースコードを読まなければ判断できません。改名した1つの変数のほうが、
  説明し、判断する挙動が1つで済みます。
- **`BAJUTSU_OAUTH_ADMIN_TEAMS` が未設定のとき、非推奨の代替として `BAJUTSU_OAUTH_ADMIN_TEAM` を読む
  方法**。既存デプロイが、環境変数を編集しなくてもアップグレード後の動作を保てるよう、検討しました。
  採用しません。`BAJUTSU_OAUTH_ADMIN_TEAM` 自体、BE-0313 が加えたものです。この項目が引き続き発展させて
  いる、GitHub Team ベースの RBAC という設計の一部であり、広く導入済みの、長く安定してきた変数では
  ありません。BE-0313 は、login リスト形式の admin 名簿をこの同じ Team に置き換えたとき、
  `BAJUTSU_OAUTH_ADMINS` を別名で残さず、そのまま廃止しました。その直接の後継である変数に対して、同じ
  完全な切り替えを保つことは、この設計がもともと使い道を持たない代替経路を新設するより、先例に沿います。
  すでに `BAJUTSU_OAUTH_ADMIN_TEAM` を設定しているデプロイは、アップグレード時に
  `BAJUTSU_OAUTH_ADMIN_TEAMS` へ改名します。これは、BE-0313 自身の `orgs:` ブロック宣言の要求が、OAuth
  を使うすべてのデプロイに求めているのと同種の、採用時の対応です。
- **迂回を、データベースを配線していないデプロイ形態にだけ絞る方法**。BE-0313 は、その形態では
  Organization メンバーシップのゲートが唯一の防御線であることを、すでに文書化しています。採用しません。
  この項目が対象とする復旧の場面、つまり配線済みのサーバーバックエンドで `orgs:` ブロックが壊れている
  場合は、まさにデータベースを配線している形態です。迂回をそこから外せば、この項目が解決しようとして
  いる場面そのものが、未解決のまま残ります。

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

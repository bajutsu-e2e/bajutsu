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
運用者はこの3つのどこからも出力を得られず、すべての login がそこで 404 になります。admin Team が
空のリストであるより、こちらはさらに悪い状態です。admin だけでなく、誰もサインインできません。この
チェックは、`oauth is None` かつ GitHub 側の3変数のうち少なくとも1つが設定されている場合、つまり
意図的なトークン認証専用デプロイではなく構成が半端な状態のときにだけ発火し、GitHub OAuth が部分的にしか
構成されていないこと、そして3変数すべてが設定されるまではすべての login が 404 になることを出力します。

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
出力する各メッセージを新しい `ServeState.startup_warnings` フィールドに集約するようになり、
`serve()` はそれらを `oplog.log_event` を通じて再発行します。`oplog.EVENTS` の新しいエントリ
`"server.startup_warning"` として、`_configure_oplog` を呼んだ直後に発行します。これは
`restore_persisted_provider_settings` がすでに使っている配置と同じであり、理由も同じです
（「malformed-file の警告が live のログシンクに届くように」）。`print` の呼び出しはそのまま残します。
それらが動く時点では何も構成されていないため、store もデータベースも無いまったく壊れたデプロイが
起動したときに、何かを見る手段は依然としてそれしかないからです。

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
報告と突き合わせる手がかりが何もない、素の 403 のままではいけません。この拒否メッセージは、config
レベルの2つの形のどちらかであれば、それがどちらだったかも名指しします。config の読み込みが失敗した
のか、config が `orgs:` ブロックをまったく宣言していないのか。これは、迂回による許可について成功
記録が運用者に与えるのと同じ triage であり、理由も同じです。どちらの形も `orgs` を `{}` に潰し、
admin 以外のすべての login を拒否します。そのため、実際には読まれていない、あるいはわざと空のまま
にされている org 名簿を非難するメッセージは、運用者を間違った修正に向かわせてしまいます。この
「orgs: ブロックがない」という形は例外的なケースではありません。この項目自身の見出しの状況その
ものです（*動機*: 「`orgs:` ブロックを持たずに起動する GitHub OAuth デプロイは……他の全員と一緒に
すべての admin を締め出します」）。`oauth_callback` には、サインインを成功させずに終える経路が5つ
あり、この項目はその5つすべてを記録するようにします。残りの4つ（OAuth が構成されていない場合。構成が
半端なデプロイでは `BAJUTSU_OAUTH_GITHUB_*` の3変数のどれか1つでも未設定だとすべての login がここで
404 になり、これも「config が壊れていて誰もサインインできない」という、この項目が可視化しようとして
いるのと同じ種類の失敗です。CSRF の state 不一致、exchange が例外を発生させた場合、exchange が
identity を返さなかった場合）も、同じ `"oauth.denied"` イベントを記録します。上で述べた理由がそのまま
当てはまるためです（ユーザの「サインインできない」という報告と突き合わせる手がかりが無い、素の 404
や 403、502 になってしまう）。CSRF の分岐は、別の理由でも自分自身の記録に値します。state の不一致が
繰り返されるのは、期限切れの cookie というより login-CSRF 攻撃の兆候であり、この項目の前は見えない
ままでした。ただし、state をまったく持たない callback（cookie も query の値も無い）は、この
認証不要のエンドポイントに対する、考えられる限り最も安価な request であり、提示された上で異なって
いたわけではないため、`WARNING` ではなく `INFO` で記録します。そうしないと、これに対する loop が
運用者が gate 由来の拒否を grep するのと同じ `event` 別 stream に、request ごとに1つの `WARNING`
を書き込んでしまい、誰でも引き起こせる自分自身の雑音の下に、この項目が追加しようとしている信号を
埋めてしまいます。これら4つの早い段階では `login` がまだわからないため、その記録には `actor`
フィールドが付きません。`actor` が付くのは、後段のゲート由来の拒否と、すべての成功したサインインだけ
です。
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
メッセージとレベル（迂回の場合は `WARNING` と「admin-Team bypass admitted …」という文言、それ以外は
`INFO` と単純な「… signed in」という文言）により、このフィールドはすべての記録で定数の `True`
になるのではなく、実際の情報を運ぶようになります。迂回は、依然として `orgs:` が認可しなかった唯一の
サインイン経路であり、そのため、誰がいつサインインしたかを追跡したい運用者にとって、記録が何も
残らない唯一の経路でもあります。同じイベント streams がそれを通常の org ゲート済みログインと
区別できるようにするのが、この `bypass` フィールドです。迂回メッセージはさらに、`matched_org` が
`False` になる4つの経路のうちどれだったかも名指しします。config の読み込み失敗、config が
`orgs:` ブロックを宣言していない、GitHub がこの login に対して org を1つも返さなかった、あるいは
`orgs:` のどのエントリにも一致しなかった、のいずれかです。`WARNING` に反応した運用者は、org ゲートが
この login を認めなかったという事実だけでなく、その理由も知る必要があるためです。最初の2つは config
自体を調べるべきであり、残りの2つは org 名簿を調べるべきです。この区別を、1つの固定された文言の裏に
隠してしまわないようにしています。

このゲートの判定が使うメンバーシップ判定（login の Team のどれかが設定済みの admin Team 一覧に
含まれるか）は、`role_for` が admin ロールを解決するときに使う判定と同じです。そのため、この項目は
同じ式を2箇所に書くのではなく、1つの共有ヘルパー `in_admin_team` にまとめます。約120行離れた2つの
関数に同じ規則が2つ存在すると、どちらか一方だけへの後からの独立した変更でずれが生じかねません。
ゲート側の判定だけが通す login は、role の判定は通らず viewer に解決されます。これは、`orgs:` が
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
これを避けます。`orgs:` ではなく迂回がその login を許可し、*かつ* config の読み込みが失敗した場合
（`parsed is None`。これは曖昧さのない手がかりです）に限って、`state.repository.user_org` に
すでに記録されている org をそのまま使い、あらためて計算しません。`org_for_identity` の `default`
という結果に落ちるのは、事前の記録が存在しない場合、つまりこの節が本来対象としている、正真正銘の
初回の bootstrap の場合だけです。config は読み込めたが `orgs:` のどれにも一致しなかった login は、
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
      ついて警告しないようにする。逆方向にゲートされた4つ目の確認（`oauth is None` だが GitHub 側の
      変数が少なくとも1つ設定されている）は、構成が半端なデプロイに警告する。上の3つの確認はここには
      届かない。どれも `oauth is None` を「意図的にトークン認証専用」と読むためであり、この状態は
      admin Team が空のリストであるより悪い。admin だけでなく、すべての login が 404 になる。
- [x] `oauth_callback` のサインインゲートに、`identity_matches_org` と並ぶ admin Team の迂回を追加する。
      ロール判定のためにすでに取得している Team 一覧をそのまま使う。サインインが成功したときは
      すべて `oplog.log_event`（予約済みの `"oauth.login"` イベント、login を `actor` に、迂回だけで
      許可した場合にだけ `True` になる `bypass` フィールド）を通じて記録し、`orgs:` が認可しなかった
      唯一のサインイン経路にも、`event` で絞り込める記録を残す。この関数がサインインを成功させずに
      終える5つの経路すべてを、別の `"oauth.denied"` イベントで記録する。OAuth が構成されていない
      場合、CSRF の state 不一致（callback が state をまったく持たない場合は `WARNING` ではなく
      `INFO` で記録する。これは提示された上で異なっていたわけではない匿名の probe であり、この
      認証不要のエンドポイントに対しては誰でも引き起こせるためである）、exchange が例外を発生させた
      場合、identity を返さなかった場合、そしてどちらの確認にも一致しない login（最後のものは、
      `orgs:` が一致しなかった4つの形、config の読み込み失敗、config が `orgs:` ブロックを宣言して
      いない、GitHub が org を返さなかった、実在するが一致しない名簿、のどれだったかも名指しする）。
      これにより、壊れた、あるいは存在しない `orgs:` ブロックに一致する admin Team もない状態が、
      突き合わせる手がかりの無い素の 404 や 403、502 のままにならないようにする。identity を
      永続化する際、既存の login の記録済み org を `default` へ移すのではなくそのまま使うのは、
      config 自体の読み込みが失敗した場合（曖昧さのない手がかり）に限る。`/user/orgs` が空を返した
      場合には適用しない。これは、GitHub のどの org にも本当に属していない login と同じ形だからである。
- [x] `_build_server_state` の4つの起動時警告を、出力するだけでなく新しい
      `ServeState.startup_warnings` フィールドに集約するようにする。`serve()` はそれぞれを
      `oplog.log_event` を通じて、`oplog.EVENTS` の新しいエントリ `"server.startup_warning"` として、
      `_configure_oplog` の直後に再発行する。これは `restore_persisted_provider_settings` がすでに
      使っている配置と同じである。これにより、運用者の `event` 別 alert は「admin がおらず、
      サインインして admin を得る手段もない」という状態も、運用者がたまたま生のブート出力から読んだ
      場合だけでなく、確実に見られるようになる。
- [x] セルフホスティングと設定のドキュメント（両言語）、`.env.example` を、改名した変数とこの迂回の
      説明に更新する。BE-0313 が述べていた「`default` org は OAuth サインインでは到達不能」という
      記述は、この項目の *詳細設計* の中で成り立たなくなる。`docs/` 配下のどのページもこの記述を
      述べていないため、ドキュメント側の修正は行わない。各エントリは実際に自分が管理する GitHub
      organization を指す必要があると明記する。この値はいまサインインの資格情報でもあるためである。
      起動時警告そのものを self-hosting ガイド（両言語）と `.env.example` に名指しし、
      アップグレードする運用者がログの最初の数行を確認するべきだとわかるようにする。
- [x] テストを追加する。`orgs:` に一致するエントリがない場合と、`orgs:` ブロック自体がない場合の
      両方で、admin Team のメンバーがサインインできることを確認する。どちらの場合も解決したロールが
      admin になることを確認する。Organization ゲートにも admin Team のリストにも一致しない login が
      引き続き拒否され、`orgs:` が一致しなかった4つの形のうちどれだったかを名指しして `"oauth.denied"`
      を記録することを確認する。OAuth が構成されていない場合、CSRF の state 不一致（両方のレベルで）、
      例外を発生させた exchange、identity を返さなかった exchange のそれぞれも `"oauth.denied"` を
      記録することを確認する。構成が半端な OAuth デプロイが警告し、完全に未設定のデプロイは警告しない
      ことを確認する。`_build_state` が、出力した内容と一致する `startup_warnings` を
      返すことを確認する。改名した変数が
      複数の Team を持つリストとしてパースされることを
      確認する。迂回した admin が `default` org に配置されることを確認する。既存メンバーの記録済み org が
      config 自体の読み込み失敗を乗り越えて残る一方で、実際に revoke されたメンバーは次回のサインインで
      `default` に再解決され、固定され続けないことを確認する。`/user/orgs` の一時的な取得失敗に遭遇した
      `githubOrgs` だけの member も同様に `default` へ再解決されること（`[]` が正真正銘の zero-orgs
      login と見分けられないことの、受け入れた自己修復コスト）を確認する。廃止した単数形の変数が、新しい
      複数形の変数も設定されている場合でも警告することを確認する。
      HTTP transport を通じて end to end で確認する。`orgs:` のどのエントリにも一致せず、迂回だけで
      許可された login が、セッションを得るだけでなく、admin 限定のエンドポイント
      （`POST /api/apikey`）に実際に到達できることを確認する。

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

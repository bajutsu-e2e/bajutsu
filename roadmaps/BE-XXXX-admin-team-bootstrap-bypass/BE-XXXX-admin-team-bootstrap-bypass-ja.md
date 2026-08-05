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
`orgs:` ブロックがまったくない場合、あるいは `members`・`githubOrgs` のエントリがデプロイの運用者を
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

この解析には、admin 全員を跡形もなく失うことを防ぐための起動時チェックを2つ追加します。
`_build_server_state`（[`bajutsu/serve/__init__.py`](../../bajutsu/serve/__init__.py)）は、
GitHub OAuth を構成しているのに、解析後の `oauth_admin_teams` が空になっているときは常に
stderr に警告を出します。原因は、デプロイが `BAJUTSU_OAUTH_ADMIN_TEAMS` を一度も設定していない
場合と、廃止した `BAJUTSU_OAUTH_ADMIN_TEAM` だけが設定済みの場合のどちらでもあり得ます（後者は
「検討した代替案」で述べる完全な切り替えのもとでは、そのデプロイが admin Team を1つも持たなくなる
ケースです）。どちらにしても、他に見える症状は admin の操作すべてで説明のない 403 が返ることだけで、
かつ、このリストはいまサインインの資格情報でもあるため（後述）、それを直しにサインインできる admin
も残っていません。警告は、実際に廃止名が原因のときだけその名前を挙げます。起きてもいない改名のせいに、
未設定のケースを誤って結び付けないようにするためです。もう1つは、各エントリが
`"<GitHub organization>/<team slug>"` の組として正しい形かどうかの確認です
（`/` の個数ではなく、organization 側と slug 側のどちらかが空である場合や、内部に空白がある場合を
拒否する正規表現で判定します）。スペースやセミコロンで
区切ったリストは、実在の Team には一致しない、1つの
不正なエントリとしてパースされます。これは、別の間違いで到達する同じ「admin がいない、しかも原因が
見えない」失敗です。この正規表現は、どちらの側の大文字も拒否しません。`in_admin_team`
（後述）がメンバーシップ判定の両辺を case-fold するため、GitHub 自身が保存している大文字小文字と
異なるエントリ――たとえば GitHub の UI に表示される Team の表示名からそのまま書き起こした slug――でも
一致し続けます。ここで拒否してしまうと、すでに機能しているエントリを運用者に「直す」よう促すことに
なり、さらに悪いことに、本当に壊れたエントリが隠れている、まさにこのリストに対する警告を無視する
習慣を運用者に付けてしまいます。どちらも例外を発生させず警告だけを出します。config の書き間違いによって、
デプロイが起動できなくなるのではなく admin なしの状態に落ちるようにするためです。これは、この
module が読む他の運用者向け環境変数（`BAJUTSU_SESSION_TTL`、同時実行数の上限、
`BAJUTSU_RUN_RETENTION_DAYS`）とは意図的に異なる選択です。それらはいずれも不正な値では例外を
発生させます。起動できないサーバーは、admin がいないサーバーと同じくらい直しにくく、しかもここでの
間違いは、それらの場合とは異なり、外側から直せるものだからです。不正なエントリは、リストから
取り除かず残します。取り除いてしまうと、admin の名簿を構文的に正しいものだけへ静かに絞り込むことに
なり、すでに警告している失敗の上に、もう1つの静かな失敗を重ねてしまうからです。どちらの確認も、
GitHub OAuth を構成しているときにしか動きません。トークン認証だけのサーバーバックエンドでは
`BAJUTSU_OAUTH_ADMIN_TEAMS` は何も決めないため、そこに残った古い値や不正な値は、そのデプロイ形態が
持ったことのない admin ロールについて警告するのではなく、静かなままであるべきです。

`oauth_callback`（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）は、Organization
メンバーシップのゲートを実行する前に、すでに login の GitHub Team メンバーシップを取得しています
（`identity.teams`、`fetch_identity` 経由）。この取得は、後段の `editorTeam` によるロール判定にも
使われているためです。ゲートと Team の取得自体は、これまで一緒に使われるよう順序づけられていな
かったにすぎません。この項目は、`identity_matches_org` と並ぶ確認を1つ追加します。login の
`identity.teams` が、設定した admin Team のリストと重なる場合、`identity_matches_org` の結果に
かかわらずサインインゲートを通過します。これにより、admin Team のメンバーは、自身の GitHub
Organization を挙げる `orgs:` エントリがなくても、あるいは `orgs:` そのものがなくてもサインイン
できます。どちらの確認にも一致しない login は、今日と同じく拒否されます。サインインが成功する
たびに、`oauth_callback` はいまや `oplog.log_event`
（[`bajutsu/serve/oplog.py`](../../bajutsu/serve/oplog.py)）を通じて記録します。すでに予約済みの
`"oauth.login"` イベントとして、login 自体を `actor` の correlation フィールドに入れます。素の
logging 呼び出しではないため、この記録は serve の他のあらゆる運用上重要な記録と同じ、登録された
イベント名、redaction、correlation のフィールドを持ち、`event` で絞り込む運用者の alert からも
実際に見えます。`"oauth.login"` はこの項目より前から `oplog.EVENTS` に予約されていましたが、実際に
発行されたことは一度もありませんでした。この項目が初めてこのイベントを発行するようにし、しかも
迂回した login だけでなく、すべてのサインインで発行します。迂回だけを記録するイベントだと、
`event=oauth.login` は「ログイン件数」ではなく「迂回件数」を意味してしまい、その event 名で
alert を組む運用者が期待する意味とは逆になってしまいます。record ごとの `bypass` フィールド
（admin Team の迂回、`orgs:` ではない方が login を許可した場合にだけ `True`)と、それに応じて変わる
メッセージ・レベル――迂回の場合は `WARNING` と「admin-Team bypass admitted …」という文言、それ以外は
`INFO` と単純な「… signed in」という文言――により、このフィールドはすべての記録で定数の `True`
になるのではなく、実際の情報を運ぶようになります。迂回は、依然として `orgs:` が認可しなかった唯一の
サインイン経路であり、そのため、誰がいつサインインしたかを追跡したい運用者にとって、記録が何も
残らない唯一の経路でもあります。同じイベント streams がそれを通常の org ゲート済みログインと
区別できるようにするのが、この `bypass` フィールドです。

このゲートの判定が使うメンバーシップ判定――login の Team のどれかが設定済みの admin Team 一覧に
含まれるか――は、`role_for` が admin ロールを解決するときに使う判定と同じです。そのため、この項目は
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
がすでに `members`・`githubOrgs` のどちらにも一致しない login を置いている先と同じ、共通の `default`
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
迂回が login を許可する唯一の経路ではありません。`/user/orgs` の取得が失敗した場合も、
`identity_matches_org` には一致が見えなくなります。これは、実在の org が本当にその login を
名簿に持っている場合にも起こります。何の補正もなければ、この一度きりの上流の不調によって、実在の
org メンバーが、そのたびに `default` へ移されてしまいます。次のクリーンなログインで元に戻るまで、
user 行、audit の帰属先、オブジェクトストレージのプレフィックスがすべて移動したままです。これは、
上記の配置ロジックが、`orgs:` がすでに認めている login に対して意図した結果ではありません。
`oauth_callback` はこれを避けますが、この特定の失敗の形に限られます。`orgs:` ではなく迂回がその
login を許可し、*かつ* `identity.orgs` が空で返ってきた場合――これは `_fetch_orgs` が取得エラー時に
残す特徴であり、例外を投げるのではなく `[]` へ fail closed するために生じます――に限って、
`state.repository.user_org` にすでに記録されている org をそのまま使い、あらためて計算しません。
`org_for_identity` の `default` という結果に落ちるのは、事前の記録が存在しない場合、つまりこの節が
本来対象としている、正真正銘の初回の bootstrap の場合だけです。`identity.orgs` が空でない値を返した
にもかかわらず `orgs:` のどれにも一致しなかった login は、このケースには当たりません。GitHub は
取得に応答しているため、その login は正真正銘、誰にも属していません。それが初回のサインインだから
なのか、あるいは運用者がその後、設定済みのすべての org からその login を外したからなのかを問いません。
そのような login は、他のあらゆる login と同じく `org_for_identity` を通じて再解決されます。これは
BE-0015 7c-2 がすでに、ロール判定をどのログインでも同様に行うよう求めているのと同じです。`orgs:`
から外したことは、次のサインインで反映されなければならず、脱退済みのメンバーが以前保持していた org
に固定され続けてはいけません。この2つのケースを分けているのが `not identity.orgs` という条件です。
この条件がなければ、脱退させられたメンバーの org は決して再解決されません。正真正銘 revoke された後、
`orgs:` に再び一致する将来のログインは存在し得ないためです。これは、数行下にある role に対する
「毎回のログインで再計算する」という同じ原則と、静かに矛盾してしまいます。

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
とっては、この失敗が及ぼす影響は今日と同じく editor・admin のロールだけです。一方、サインインその
ものが迂回だけに依存している login にとっては、同じ fail closed の挙動が、いまはサインインそのものを
失わせます。GitHub の API 障害が起きている間は admin Team のメンバーシップを証明できず、迂回を使えま
せん。その場合、Organization メンバーシップのゲート単独が与える結果に戻ります（`orgs:` からもその
Organization に到達できなければ拒否です）。この項目は、この API 障害のケースに対する login リストの
代替経路を追加しません。詳しくは「検討した代替案」を参照してください。

### admin Team の各エントリは、ロールの対応づけだけでなくサインインの資格情報にもなる

この項目より前は、デプロイが実際には管理していない GitHub organization を指すエントリ（
organization 側の書き間違い、あるいは後から名前が変わった、あるいは削除された Team の organization
――GitHub は古い login を誰でも再登録できるようにします――）は、単に誰にも一致しませんでした。
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
      ときにだけ、結果のリストが空になっているとき（未設定でも、廃止した単数形だけが設定済みでも）、
      および各エントリが正しい `"<GitHub organization>/<team slug>"` の組でないとき（空の側や内部の
      空白は対象だが、大文字は対象外――`in_admin_team` が case-fold するため、大文字小文字が異なる
      エントリでも一致し続ける）、それぞれ起動時に大きく警告する。admin を失う間違いをどれも見逃さず、
      かつトークン認証だけのデプロイに持っていない admin ロールについて警告しないようにする。
- [x] `oauth_callback` のサインインゲートに、`identity_matches_org` と並ぶ admin Team の迂回を追加する。
      ロール判定のためにすでに取得している Team 一覧をそのまま使う。サインインが成功したときは
      すべて `oplog.log_event`（予約済みの `"oauth.login"` イベント、login を `actor` に、迂回だけで
      許可した場合にだけ `True` になる `bypass` フィールド）を通じて記録し、`orgs:` が認可しなかった
      唯一のサインイン経路にも、`event` で絞り込める記録を残す。
      identity を永続化する際、`/user/orgs` の一時的な取得失敗によって `orgs:` ではなく迂回がその login
      を許可した場合は、`default` へ移すのではなく、その login にすでに記録されている org をそのまま
      使う。
- [x] セルフホスティングと設定のドキュメント（両言語）、`.env.example` を、改名した変数とこの迂回の
      説明に更新する。BE-0313 が述べていた「`default` org は OAuth サインインでは到達不能」という
      記述は、この項目の *詳細設計* の中で成り立たなくなる。`docs/` 配下のどのページもこの記述を
      述べていないため、ドキュメント側の修正は行わない。各エントリは実際に自分が管理する GitHub
      organization を指す必要があると明記する。この値はいまサインインの資格情報でもあるためである。
- [x] テストを追加する。`orgs:` に一致するエントリがない場合と、`orgs:` ブロック自体がない場合の
      両方で、admin Team のメンバーがサインインできることを確認する。どちらの場合も解決したロールが
      admin になることを確認する。Organization ゲートにも admin Team のリストにも一致しない login が
      引き続き拒否されることを確認する。改名した変数が複数の Team を持つリストとしてパースされることを
      確認する。迂回した admin が `default` org に配置されることを確認する。既存メンバーの記録済み org が
      `/user/orgs` の一時的な取得失敗を乗り越えて残る一方で、実際に revoke されたメンバーは次回の
      サインインで `default` に再解決され、固定され続けないことを確認する。HTTP transport を通じて
      end to end で確認する。`orgs:` のどのエントリにも一致せず、迂回だけで許可された login が、
      セッションを得るだけでなく、admin 限定のエンドポイント（`POST /api/apikey`）に実際に到達できる
      ことを確認する。

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
- [`docs/ja/self-hosting.md`](../../docs/ja/self-hosting.md)。self-hosting ガイドの GitHub OAuth の節
  です。この項目が塞ぐ隙間は、この項目が取り除くまで、ここに「まず上記のサインインのゲートを
  通過する必要があります」として書かれていました。

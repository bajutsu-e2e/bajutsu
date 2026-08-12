[English](BE-XXXX-serve-org-lifecycle-management.md) · **日本語**

# BE-XXXX — serve の org のライフサイクルとメンバーシップをデータベースで管理する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-serve-org-lifecycle-management-ja.md) |
| 提案者 | [@paihu](https://github.com/paihu) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Web UI のホスティング |
| 関連 | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)、[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md)、[BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass-ja.md)、[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)、[BE-0170](../BE-0170-weighted-fair-org-dispatch/BE-0170-weighted-fair-org-dispatch-ja.md) |
<!-- /BE-METADATA -->

## はじめに

この項目は、admin が serve の **org**（Bajutsu のマルチテナンシーの単位で、しばしば「テナント」と
呼ばれます。デプロイの設定ファイルの `orgs:` ブロックの1エントリとして宣言されています。
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)）を、Web UI とその
アプリケーションプログラミングインタフェース（API）から作成・削除・編集できるようにします。設定
ファイルを編集して再デプロイする代わりに、admin が
その場で操作できるようにするのが狙いです。移す対象は、org の**メンバーシップ**です。どの GitHub
login または GitHub Organization がその org としてサインインできるか（`members` / `githubOrgs`）と、
どの GitHub Team がその org の editor に属するか（`editorTeam`、
[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md)）です。移し先は、
データベースを一度配線した serve（`BAJUTSU_DATABASE_URL`）がすでに動かしているデータベースです。
データベースを持たないデプロイは、今のまま `orgs:` を設定ファイルから読みます。この項目は、その経路
に変更を加えません。

## 動機

[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) は、環境変数の
login リストを GitHub 自身の Organization と Team のメンバーシップに置き換えました。login リストが
GitHub がすでに保守している名簿を重複させ、実態から次第にずれていくためです。ただし、その項目は
1つの名簿を置き換えずに残しました。`orgs:` ブロック自体、つまりどの GitHub Organization や GitHub
Team をどの org に対応させ、誰をその editor とするかを決める名簿です。GitHub 側には Bajutsu の org
に対応するものが何もありません。org という境界は、このデプロイ自身が定義するものであり、GitHub
から読み出せる代わりが存在しません。したがって、これをデータとして持つこと自体は、BE-0313 が解決
した名簿の重複ではありません。ギャップがあるのは、そのデータの置き場所だけです。今日それは、運用者
が手で編集し、再デプロイまたは `POST /api/config` の再バインドで届けるファイルの中にあります。どの
アプリケーションを serve がテストするかを変える経路と、まったく同じ経路です。

その経路は、デプロイの設定リポジトリや CI/CD パイプラインに触れないまま、新しいテナントを迎え入れ
たい運用者や、あるチームの編集権限を別の GitHub Team へ移したい運用者には合いません。この項目が対象と
する種類の per-org なデータは、データベースを配線した時点でほぼすべて、すでにそちらへ移っています。
登録された project（[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)）・
AI provider の設定
（[BE-0229](../BE-0229-per-org-provider-settings-resolution/BE-0229-per-org-provider-settings-resolution-ja.md)）・
secret・audit log です。`orgs` テーブル自体はすでに存在します。`org_id` の外部キーを持つのは
`projects`・`provider_settings`・`secrets`・`audit_log` の各テーブルです
（[`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py)）。しかし今のところ、
これは設定ファイルの受動的な鏡でしかありません。
`ensure_org` は、その org での最初のログイン時に、id・slug・name だけを持つ行を作成し、
そこで止まります。`members`・`github_orgs`・`editor_team` のいずれも持ちません。データベースを配線した
デプロイでテナントを迎え入れるにも、データベースを持たないデプロイとまったく同じファイル編集と
再デプロイが要ります。そのデプロイには、まさにこの種の運用データのために置かれた、手つかずの
データベースがすでにあるにもかかわらずです。

## 詳細設計

### 用語

この項目は、「org」「GitHub Organization」「project」を、
[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) の *用語* 節が
区別した意味のままに使い、その区別をここで書き直しません。要点だけをまとめると、**org** は
Bajutsu 自身のマルチテナンシーの単位（`orgs:` の1エントリ）です。**GitHub Organization** は、
その org に誰が属するかを決める GitHub 側の identity の出どころです。**project** は、org の下に
登録される config ソースへのバインディングで、1つの org が複数の project を持てます。この項目が
対象とするのは org 自身、つまりその存在とメンバーシップです。org が対応する GitHub Organization
（変更なし）や、org が持つ project（変更なし）は対象としません。

### 1. メンバーシップはデータベースへ、target の所有はconfig のまま

`OrgConfig`（[`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)）は、4つのフィールドを
持ちます。`members`・`github_orgs`・`editor_team`、そして `targets`（このデプロイに設定された
[target](../../docs/ja/glossary.md#target-app-device)、つまりテスト対象のアプリケーションの
うち、その org が所有するもの）です。この項目は最初の3つ、まとめて org の**メンバーシップ**
（誰がその org としてサインインできるか、そのうち誰が書き込めるか）を、`Org` テーブル
（[`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py)）に新設する3つの
カラムへ移します。`members` と `github_orgs` は文字列の配列を持つ JSON カラムに、`editor_team`
は null を許容する文字列カラムにし、いずれも `OrgConfig` 自身の形をそのまま反映します。`targets`
は config に残します。target がどのアプリケーションを指し、どの backend とどの device で動くかは、
まさにプライム・ディレクティブ3（アプリケーション非依存のコア）が admin が編集できるストアの
外に置いている、アプリケーション固有の差分です。データベースを配線したデプロイには、アプリケー
ションを org に紐づけるための、より活発に保守されている手段がすでにあります。project を org の下に
登録することです（[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)）。
`targets` まで移せば、この手段を重複させるだけで、この項目のメンバーシップ管理という範囲を
完結させることにはなりません。

新設する `orgs_from_db(repository: Repository) -> dict[str, OrgConfig]` 関数は、同じモジュールの
既存の `parse_orgs` と並んで置きます。この関数は、すべての `Org` 行とそのメンバーシップカラムを
問い合わせ、`parse_orgs` が YAML から組み立てるのと同じ `dict[str, OrgConfig]` の形を組み立てます。
`targets` は常に空にします。この項目のもとでは、データベース由来の org はどの target も所有しない
ためです。データベース連動の辞書に切り替わるのは、`oauth_callback` 内のサインインとメンバーシップ
解決の呼び出し箇所だけです。`identity_matches_org` と `org_for_identity` の呼び出し、そして
`role_for` へ引数として渡す `editor_team` を、マッチした `OrgConfig` から取り出す箇所です
（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）。この切り替えは、`oauth_callback` 内で
同じ辞書を参照する診断・回復の経路にも及びます。`_unmatched_org_cause` が受け取る `orgs` 引数（拒否
ログと bypass ログの本文を組み立てるのに使います）、config の読み込み失敗時に既存の org をそのまま
維持するガード（`not matched_org and parsed is None and state.config is not None`）、そして bypass
によるサインインを WARNING で記録するかどうかを決める `parsed is None or not orgs` の条件です。これら
はいずれも、config 由来の辞書だけが取りうる形（`parsed is None`・`not orgs`）を根拠に原因を分類して
います。「config がまだバインドされていない」「config の読み込みに失敗した」「config が `orgs:`
ブロックを宣言していない」といった分類です。データベース連動のデプロイでは、切り替わった辞書が
`orgs_from_db` から得られる以上、これらの原因分類はデータベースという出どころが実際にどう失敗しうる
かに合わせて読み替える必要があり、config 向けの分類のままでは持ち越せません。target の解決は、config から解析した
辞書を変更なしで読み続けます。`targets_for_org` と、`authz.py` の `_target_forbidden` が呼ぶ
`org_for_target` は、どちらも org が実際に所有する target の情報を必要とします。ユニット1のとおり、
target の所有情報を持つのは config 由来の辞書だけです。データベース由来の `OrgConfig` は常に空の
`targets` を返すため、どちらのヘルパーも `orgs_from_db` の出力へ差し替えると、データベース連動
デプロイのすべての target の所有情報が壊れます。これは、
[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) 自身が login
リストを GitHub Team のメンバーシップへ置き換えたときに使った原則と同じです。方針は、サインイン
ごとにその出どころから再計算されるので、出どころを変えても、解決ロジック自体にはデータマイグレー
ションが要りません。

### 2. データベースのゲート：この項目が変えるデプロイの範囲

`oauth_callback`（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）は、repository を
配線している場合（`state.repository is not None`）に限り、config から解析した `orgs:` の辞書の
代わりに `orgs_from_db` を呼びます。この条件は、serve の他のデータベース連動の仕組みがすでに使って
いるものと同じです。データベースを持たないデプロイは、今日と変わらず設定ファイルに対して
`parse_orgs` を呼び続け、org 管理用の admin UI も持ちません。そのデプロイ形態は、そもそもローカルで
単一ユーザー向けに作られています
（[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md) 自身の動機が示す
とおりです）。管理すべきテナントの境界がなく、
[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) はすでに、そこでは
`forbidden_for_role` がロールに関係なくフルアクセスへ短絡すると述べています。データベースを持たない
デプロイの org モデルは、サインインそのものをゲートするためだけに存在し、ロールを付与するためでは
ありません。したがって、org 単位のメンバーシップストアからそのデプロイが得るものは、この項目の
設計には何もありません。

サインインゲートの位置づけは、この項目では変わりません。つまり、`org_for_identity` を呼ぶ前に走る
`members` / `github_orgs` の一致チェックは、`if state.repository is not None:` ブロックの手前で
行われ続けます（データベースを持たない OAuth 設定済みデプロイでもサインインをゲートするため、
[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md)）。変わるのは、
そのチェックの参照先だけが、データベースの配線有無で決まるようになる点で、
`oauth_callback` の中でチェック自体がどこで走るかは変わりません。

### 3. admin 用 API と UI：作成・削除・メンバーシップの編集

4つのエンドポイントを設けます。すべて admin 限定です。org のメンバーシップは、他に誰がサインイン
でき、誰が書き込めるかを決めるものであり、project の登録や config の再バインドに BE-0225 がすでに
与えている感度と同じ水準です。いずれの変更も、既存の `Repository.record_audit` を通じて記録します
（`org.create` / `org.delete` / `org.membership.update`、対象は org の slug）。

- `GET /api/orgs` — すべての org を一覧します。slug・name・メンバーと GitHub Organization の件数・
  editor Team が設定されているか・project の件数を返します。
- `POST /api/orgs` — `{slug, name}` から org を作成します。メンバーシップは空から始まります
  （member・GitHub Organization・editor Team のいずれもなし）。admin が追加するまで、新設した org は
  誰も受け入れません。作成した行には、ユニット4が一本化の際に立てるのと同じ引き込み済みマーカーを
  作成時点で立てます。この行はすでに一本化を終えたものとして扱われるため、以後どの `orgs:` エントリ
  もこの行を引き込むことはなく、admin がこの API で設定したメンバーシップを上書きすることもありません。
- `PUT /api/orgs/<slug>/membership` — org の `{members, githubOrgs, editorTeam}` を一括で
  置き換えます。エントリ単位の追加・削除エンドポイントに分けず、設定ファイルの編集がすでに持つのと
  同じ粒度にします。
- `DELETE /api/orgs/<slug>` — org を削除します。その org がまだ project を1つでも所有している間は
  409 で拒否します（`list_projects` が空でない）。admin は、先に org の project を登録解除する
  必要があります（BE-0225 の登録解除は run 履歴を残します）。削除は `default` org も拒否します。
  `default` は serve がコードにハードコードしたフォールバックの slug（`DEFAULT_ORG`、
  [`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)）であり、テーブルの状態に関わらず未マッチの
  bypass サインインが解決先とし続けます。削除してしまうと、bypass ユーザーが着地し続けるソフト
  デリート済みの org が残るだけになります。project を持たなくなった、`default` 以外の org の削除は、
  ソフトデリートです。`Org` 行を物理的に削除するのではなく、`deleted_at` カラムを立てて削除済みと
  記録します（run の trash 済み状態がすでに使っている、同じソフトデリートの形です）。物理削除で
  あれば、project をすべて登録解除した後も `users`・`runs`・`secrets`・`provider_settings`・
  `audit_log` がその org の id への外部キーを保持したままになり、整合性制約に反します
  （[`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py)）。削除の操作自身が記録
  する audit log のエントリも、そのときには指す先を失います。行を残すソフトデリートであれば、これら
  の外部キーはすべて有効なまま保たれます。削除後は、サインインの解決処理と `GET /api/orgs` が、その
  org を対象から外します。以後、その org としてマッチすることも一覧に出ることもありません。その org
  の下に残る user・secret・provider の設定は、行として残り参照はできますが、その org としての新規
  サインインはもう認めません。その org に紐づく run 履歴と audit log のエントリは、削除された org の
  id のもとに残ります。project の登録解除後も run 履歴が残るのと同じ考え方です
  （[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)）。admin の操作が
  取り去るのは、テナントがサインインして行動する能力であって、そのテナントが行ったことの記録では
  ありません。

ソフトデリート済みの org と同じ slug で org を作り直すことは、この項目の対象外です。`Org.slug` には
end-to-end で UNIQUE 制約があり
（[`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py)）、ソフトデリート済みの
行がすでにその slug を占有しているため、`POST /api/orgs` はその slug を専用の 409 エラーで拒否し、
行を復活させず、再利用もしません。再有効化が必要になる場合は、将来の項目に委ねる、別の
明示的な操作とします。ユニット4の引き込みも、`deleted_at` が立っている行は対象にしません。ソフト
デリート済みの org は未引き込みではなく廃止済みであるため、引き込まず、復活もさせません。

serve のシェルには、admin 限定の新しい **Orgs** ページを追加します。
[BE-0275](../BE-0275-serve-projects-management-page/BE-0275-serve-projects-management-page-ja.md)
の Projects ページと並ぶ位置づけです。org の一覧に作成・削除の操作を添え（org がまだ project を
持つ間は削除を無効にし、その件数を表示します）、org ごとのメンバーシップ編集フォーム（member・
GitHub Organization・editor Team）を、上記の `PUT` エンドポイントから呼びます。この API とページは
いずれも、ユニット2のゲートに従い、repository を配線している場合にのみ存在します。

### 4. 一度きりの引き込みと、その後の一本化

config だけでメンバーシップを管理していたデプロイをこの項目にアップグレードする場合、既存の
`orgs:` ブロックを、この項目の admin UI が表示・編集できる形でデータベースに反映する必要があり
ます。この引き込みは、バインドされた config がどちらの経路から届いても、単一の仕組みで行います。
対象となるタイミングは、起動時の `_build_server_state` と、`POST /api/config` が config を再バイン
ドする `bind_config` / `bind_git_config`
（[`bajutsu/serve/operations/config.py`](../../bajutsu/serve/operations/config.py)）の両方です。
serve は、バインドされた config が宣言する org のうち、永続化した行単位のマーカーが引き込み済みで
ないと示す org を選び、そのメンバーシップカラムを引き込みます。`orgs:` ブロックの各エントリについて、その
org 行にマーカーが立っていなければ、`ensure_org`（`slug` / `name` だけでなくメンバーシップの
フィールドも受け取るように拡張します）がそのエントリから `members` / `github_orgs` / `editor_team`
を作成または更新し、マーカーを立てます。起動時だけでなく再バインド時にも同じ引き込みを走らせるのは、
この項目自身の動機が挙げる、config が serve に届く2つの経路のどちらでも正しく動くようにするため
です。起動時にしか引き込まないと、再バインドで新しく宣言された org は永遠に引き込まれず、後述の
一本化がそのデプロイに効いた時点で、その org へのログインはすべて失敗します。マーカーは各行の引き
込み済み状態を直接記録し、メンバーシップカラムがすでに値を持つかどうかから推定することはしません。
マーカーがなければ、admin が意図的にメンバーシップを空にした引き込み済みの org と、まだ引き込んで
いない org を区別できません。区別できないと、後の起動や再バインドで設定ファイルの古い値により
上書きされ、admin が外したはずのサインインが復活してしまいます。`members` / `github_orgs` / `editor_team` の各カラム（と引き
込み済みマーカーのカラム）を追加する Alembic マイグレーションは、カラムを追加するだけです。データの
引き込み自体は行いません。引き込みは、起動時と再バインド時に serve 自身が行う仕事であり、マイグレー
ションにはできません。Alembic のマイグレーション環境
（[`bajutsu/serve/server/migrations/env.py`](../../bajutsu/serve/server/migrations/env.py)）は
`BAJUTSU_DATABASE_URL` しか読まず、バインドされた config の `orgs:` ブロックには一切アクセスできない
ためです。

ある org の行にマーカーが立った後は、`oauth_callback` はその org のメンバーシップをデータベースから
だけ読みます。以後、設定ファイルの `orgs:` ブロックのそのエントリを編集しても効果はありません。これ
は、
[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) と
[BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass-ja.md) の
どちらも、同じロールを決めるデータに対して2つの独立した出どころを持たせる案より、一本化を選んだ
判断に合わせています（*検討した代替案* を参照）。まったく新しい `orgs:` エントリが次の起動または
再バインドで通常どおり引き込まれるのは、その行がまだ存在しないか、マーカーのない受動的な行
（サインイン時に `ensure_org` が作成した、id・slug・name だけを持つ行）である場合に限られます。
ユニット3の API を通じて作成した org は、作成時点で引き込み済みマーカーが立つため（ユニット3を
参照）、同じ slug を指す後続の `orgs:` エントリが引き込まれることはありません。repository の配線・config
のバインド・引き込み済み org の `orgs:` エントリに `members` / `githubOrgs` / `editorTeam` のいずれ
かがまだ宣言されていること、の3条件がすべて成り立つとき、serve は実行のたびに1度だけ警告を出します。
`targets` だけを持つ `orgs:` エントリは、
カットオーバー後も正当であり、残り続けることが期待されます。ユニット1のとおり、target の所有情報は
config が持ち続けるためです。単に空でない `orgs:` ブロックだけを条件にすると、正しく設定された
デプロイで永遠に鳴り続けるか、警告を止めようと `orgs:` を空にするよう運用者を仕向け、target の
所有情報を失わせてしまいます。これは、
[BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass-ja.md) が
自らの廃止した環境変数についてすでに出している、「運用者がもう読まれていないことを忘れている」
という同種の警告です。

repository を最初の起動から配線している、config だけの履歴を持たない新規デプロイでは、その最初の
起動がバインドする `orgs:` ブロックを対象に引き込みが走ります（通常は空なので、引き込むものは
ありません）。その
デプロイが持つ org は、以後ユニット3の API を通じて admin が作成したものか、その後の起動または再
バインドで同じように引き込まれる新しい `orgs:` エントリのいずれかです。

### 5. admin Team のブートストラップ迂回が、テーブルが空の場合の答えになる

新設されたばかりのデータベース連動デプロイには、admin が作成するまで org が1つもありません。これは、
[BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass-ja.md) が
`orgs:` ブロックが壊れている、または存在しない場合について、すでに答えたのと同じブートストラップの
問いを引き起こします。最初の1つを作成するには、誰がサインインすればよいのでしょうか。
`BAJUTSU_OAUTH_ADMIN_TEAMS` は、この項目のデータベースへの移行から意図的に外したまま、環境変数の
ままにします。これは、org モデル自身が、どこに置かれていようと、まだ誰も受け入れられない状態を
回復するための経路だからです。設定した admin Team のメンバーは、`Org` 行がいくつ存在するかに関係
なくこの迂回を通じてサインインゲートを通過し、admin ロールでサインインします（この項目による変更
はありません。
[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) の「admin は
サーバー全体で1つの階層のまま」）。そのうえで、ユニット3の API を使って、このデプロイの最初の org
を作成します。鶏と卵の問題は起きません。この項目が意図的にデータベースの外に残した、テナンシーの
データのうちその1点こそが、`orgs` テーブルが空の状態を回復可能にしているからです。

## 検討した代替案

- **org のメンバーシップを設定ファイルに残し、外部で編集した後 `POST /api/config` の再バインドを
  admin に引かせる。** 却下します。これは今日の仕組みそのもので、まさにこの項目が取り除きたい依存
  関係です。テナントを1つ迎え入れるたび、あるチームを別の GitHub Team へ移すたびに、デプロイの
  設定リポジトリへの書き込み権限を持つ人と、再デプロイまたは再バインドが要ります。
- **データベース連動の経路と、ローカルのファイル連動の経路を両方持つレジストリのシームを、
  [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md) の
  `ProjectRegistry` に倣って設ける。これにより、データベースを持たない serve でも admin が org の
  メンバーシップを管理できるようにする。** 却下します。データベースを持たない serve は、そもそも
  ローカルで単一ユーザー向けに作られており、admin が管理すべきテナントの境界も、保護すべきロールの
  区別もありません（ユニット2）。そのデプロイ形態が使い道を持たない機能のために、もう1つの永続化
  経路を作るのは、余分な表面積を増やすだけです。`ProjectRegistry` とは対照的です。単一ユーザーの
  ローカルなハブには、複数の config を扱うという本物の必要があります（複数のテナントを扱う必要では
  ありません）。
- **`targets`（org の target 所有）も、`members`・`github_orgs`・`editor_team` と一緒に
  データベースへ移す。** この項目では却下します。`targets` は、target がどのアプリケーションを
  指し、どう動くかを名指すアプリケーション固有のデータです。プライム・ディレクティブ3が設定
  ファイルに残すものであり、admin が編集できる運用ストアに置くものではありません。データベース連動の
  デプロイには、アプリケーションを org に紐づけるための、形が異なりより活発に保守されている手段が
  すでにあります。[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md) の
  project レジストリです。
- **org 自体を削除するとき、その project・run・audit log のエントリをカスケード削除する。** 却下
  します。project を登録解除しても run 履歴を残す BE-0225 自身の判断を反映するのと同じ考え方です。
  audit log は、まさに admin の操作それ自体が記録を消す理由にならないように存在します。
  [BE-0170](../BE-0170-weighted-fair-org-dispatch/BE-0170-weighted-fair-org-dispatch-ja.md) の
  今後の org 単位の公平な dispatch も、そのデータを生み出した org が存在するかどうかに
  関係なく、過去のデータを問い合わせ続けられることを必要とします。
- **一度きりの引き込みとその後の一本化の代わりに、サインインのたびに設定ファイルとデータベースの
  両方を参照する（リクエストごとのフォールバック）にする。** 却下します。BE-0313 と BE-0352 は、
  どちらもすでに、同じサインインを左右するデータに対して2つの独立した出どころを持たせることを
  却下しています。BE-0313 自身の動機が述べる、名簿がずれていくという同じ理由です。この項目は、
  org のメンバーシップについてその判断を蒸し返さず、同じ前例に従います。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 1 — `Org` テーブルに `members` / `github_orgs` / `editor_team` カラムを追加する（Alembic
      マイグレーション）。`parse_orgs` が設定ファイルから組み立てるのと同じ `dict[str, OrgConfig]`
      の形を、`targets` を常に空にして組み立てる `orgs_from_db` を追加する。
- [ ] 2 — `state.repository is not None` の場合に `oauth_callback` を `orgs_from_db` へ切り替える。
      データベースを持たないデプロイの、設定ファイル由来の `orgs:` 経路は変更しない。
- [ ] 3 — 4つの `/api/orgs…` エンドポイント（admin 限定、いずれも `record_audit` で記録）と、
      Orgs 用の admin ページ（作成・空のときのみ削除・メンバーシップ編集）を、repository を
      配線している場合にのみ用意する。`POST /api/orgs` は作成時点で新しい行に引き込み済みマーカーを
      立て、以後どの `orgs:` エントリもその行を引き込まないようにする。削除はソフトデリート
      （`Org.deleted_at`）とし、行を削除せず外部キーを壊すこともなく、その org をサインインの
      解決処理と `GET /api/orgs` の対象から外す。
- [ ] 4 — repository を配線しており、かつ config をバインドしている状態になるたび（起動時と `POST /api/config` の再バインド時の両方）、
      バインドされた設定ファイルの `orgs:` ブロックを
      データベースへ引き込む処理を、`Org` 行ごとの永続化したマーカーで保証して追加する（メンバー
      シップカラムの空欄からの推定はしない）。Alembic マイグレーションはメンバーシップとマーカーの
      カラムを追加するだけで、引き込み自体は行わない。引き込み済みの org の `orgs:` エントリが
      `members` / `githubOrgs` / `editorTeam` を宣言している場合は、起動時・再バインド時に警告する
      （`targets` だけを持つエントリは警告の対象外とする）。
- [ ] 5 — テスト：`orgs_from_db` が、同等の `orgs:` ブロックについて `parse_orgs` と同じ解決結果を
      再現すること。データベースを持たない経路に影響がないこと。API を通じた org の作成・
      メンバーシップの置き換え・空でない org の削除拒否がいずれも admin 限定で記録されること。
      引き込みが一度だけ走り、その後の設定ファイルの `orgs:` 編集が効果を持たないこと。admin Team
      の迂回（[BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass-ja.md)）
      が、`orgs` テーブルが空の状態でもサインインを許可し、その admin がデプロイの最初の org を
      作成できること。

## 参考

- [BE-0015 — Web UI のパブリックホスティング](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) —
  この項目がデータベースへ移す `orgs:` マルチテナンシーモデルと、この項目の `Org` テーブルが
  すでに支えている `projects` / `runs` スキーマの `org_id` 外部キー。
- [BE-0313 — GitHub Organization メンバーシップと Team ベースの RBAC を serve に導入](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) —
  この項目が移す `members` / `githubOrgs` / `editorTeam` の各フィールド、この項目が引き継ぐ
  *用語* 節、そして `orgs_from_db` という新しい出どころが従う「サインインごとに再計算し、データ
  マイグレーションを要さない」という原則。
- [BE-0352 — admin 用 GitHub Team の環境変数が Organization メンバーシップのサインインゲートを迂回する](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass-ja.md) —
  この項目が変更せずに残す admin Team のブートストラップ迂回と、この項目の引き込み処理が従う
  一本化の前例。
- [BE-0225 — serve の config プロジェクトハブ（登録・一覧・切替・実行）](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md) —
  この項目の *検討した代替案* が、アプリケーションを org に紐づける既存の手段として挙げる project
  レジストリと、この項目の org 削除が従う「登録解除後も履歴を残す」前例。
- [BE-0275 — serve の projects 管理ページ](../BE-0275-serve-projects-management-page/BE-0275-serve-projects-management-page-ja.md) —
  この項目の Orgs ページが手本にする admin ページ。
- [BE-0170 — org 単位の公平な dispatch の重み付け](../BE-0170-weighted-fair-org-dispatch/BE-0170-weighted-fair-org-dispatch-ja.md) —
  **提案** の状態にある項目です。この項目によって admin が org を作成できるようになると、その
  意義は増します。動的に増えていくテナントの集合こそ、BE-0170 の公平性の仕組みが対象とする形だから
  です。
- [`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py) — `OrgConfig`・`parse_orgs`、そして
  この項目が変更せずに2つ目の生成元（`orgs_from_db`）を追加する解決用のヘルパー群。
- [`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py) — この項目がメンバー
  シップ用のカラムを追加する `Org` テーブル。
- [`bajutsu/serve/server/db.py`](../../bajutsu/serve/server/db.py) — この項目が拡張する
  `Repository.ensure_org` と、この項目の `OrgRecord` と、その作成・削除・メンバーシップ更新の
  各操作が手本にする `ProjectRecord` / `create_project` / `delete_project` の形。
- [`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py) — `oauth_callback` のサインインゲート。
  この項目が変えるのはそのデータの出どころであり、位置づけではありません。
- [`bajutsu/serve/operations/projects.py`](../../bajutsu/serve/operations/projects.py) — この
  項目の `/api/orgs…` エンドポイントが手本にするエンドポイントと、その権限判定の形。

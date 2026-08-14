[English](BE-0375-serve-org-lifecycle-management.md) · **日本語**

# BE-0375 — serve の org のライフサイクルとメンバーシップをデータベースで管理する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0375](BE-0375-serve-org-lifecycle-management-ja.md) |
| 提案者 | [@paihu](https://github.com/paihu) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0375") |
| 実装 PR | [#1636](https://github.com/bajutsu-e2e/bajutsu/pull/1636) |
| トピック | Web UI のホスティング |
| 関連 | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)、[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md)、[BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass-ja.md)、[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)、[BE-0170](../BE-0170-weighted-fair-org-dispatch/BE-0170-weighted-fair-org-dispatch-ja.md) |
<!-- /BE-METADATA -->

## はじめに

この項目は、admin が serve の **org** を Web UI とそのアプリケーションプログラミング
インタフェース（API）から作成・削除・編集できるようにします。org は Bajutsu のマルチテナンシーの
単位であり、しばしば「テナント」とも呼ばれます。今はデプロイの設定ファイルの `orgs:` ブロックの
1エントリとして宣言します
（[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)）。設定ファイルを
編集して再デプロイする代わりに、admin がその場で操作できるようにするのが狙いです。移す対象は、
org の**メンバーシップ**です。1つは、どの GitHub login または GitHub Organization がその org として
サインインできるかを決める `members` / `githubOrgs` です。もう1つは、その org の editor がどの
GitHub Team に属するかを決める `editorTeam` です
（[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md)）。移し先は、
データベースを一度配線した serve（`BAJUTSU_DATABASE_URL`）がすでに動かしているデータベースです。
そしてデータベースが org のメンバーシップを持つ以上、参照先はそのデータベースだけになります。
サインインゲート自体も設定ファイルを読まなくなるため、config の読み込みに失敗しても、すべての
サインインが拒否される事態は起きなくなります。あわせて、config に残す target の所有が、admin による
org 作成のもとで何を意味するのかも決めます。org の target は名前単位ではなく org 単位で解決します。
これにより、2つの org が同じ名前の target をそれぞれ主張できます。config の記述順が、どちらか一方に
黙って与えてしまうことはなくなります。データベースを持たないデプロイは、
今のまま `orgs:` を設定ファイルから読みます。この項目は、その経路に変更を加えません。

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

その経路は、新しいテナントを迎え入れたい運用者には合いません。あるチームの編集権限を別の
GitHub Team へ移したい運用者にも合いません。どちらも、デプロイの設定リポジトリや CI/CD
パイプラインには触れずに済ませたいからです。この項目が対象と
する種類の per-org なデータは、データベースを配線した時点でほぼすべて、すでにそちらへ移っています。
登録された project（[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)）・
AI provider の設定
（[BE-0229](../BE-0229-per-org-provider-settings-resolution/BE-0229-per-org-provider-settings-resolution-ja.md)）・
secret・audit log です。`orgs` テーブル自体はすでに存在します。`org_id` の外部キーを持つのは
`projects`・`provider_settings`・`secrets`・`audit_log` です
（[`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py)）。しかし今のところ、
これは設定ファイルの受動的な鏡でしかありません。
`ensure_org` は、その org での最初のログイン時に、id・slug・name だけを持つ行を作成し、
そこで止まります。`members`・`github_orgs`・`editor_team` のいずれも持ちません。データベースを配線した
デプロイでテナントを迎え入れるにも、データベースを持たないデプロイとまったく同じファイル編集と
再デプロイが要ります。そのデプロイには、まさにこの種の運用データのために置かれた、手つかずの
データベースがすでにあるにもかかわらずです。

このデータを移すと、設定ファイルが暗黙のうちに支えていた前提が2つ表に出てきます。どちらも、org が
数えるほどしかなく、人手で書かれているあいだしか成り立ちません。1つめは、そのファイルが常に読める
という前提です。`oauth_callback` はサインインを、解析済みの `orgs:` ブロックでゲートしており、解析に
失敗すると空の名簿へフェイルクローズします。つまり1行の書き間違いが、データベース側では誰が誰かを
すでに正確に把握しているデプロイで、admin 以外のすべてのユーザーを拒否します。2つめは、2つの org が
同じ target 名を主張することはない、という前提です。手で編集する1つのファイルなら、レビューで守れま
した。しかし admin が、他のテナントのエントリを見られない Web UI からテナントを作れるようになれば、
誰にも守れません。どちらも、org データの出どころを変えるこの機会に片づけるのがもっとも安く
済みます。あとから安くなることはありません。

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
持ちます。`members`・`github_orgs`・`editor_team`、そして `targets` です。`targets` は、この
デプロイに設定された [target](../../docs/ja/glossary.md#target-app-device)、つまりテスト対象の
アプリケーションのうち、その org が所有するものを指します。この項目は最初の3つを、`Org` テーブル
（[`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py)）に新設する3つのカラムへ
移します。3つをまとめて org の**メンバーシップ**と呼びます。誰がその org としてサインインでき、
そのうち誰が書き込めるかを決めるものです。`members` と `github_orgs` は文字列の配列を持つ JSON カラムにします。
`editor_team` は null を許容する文字列カラムにします。いずれも `OrgConfig` 自身の形をそのまま
反映します。`targets` は config に残します。target がどのアプリケーションを指し、どの backend と
どの device で動くかは、アプリケーション固有の差分だからです。プライム・ディレクティブ3
（アプリケーション非依存のコア）が、admin の編集できるストアの外に置いているものです。しかも
データベースを配線したデプロイには、アプリケーションを org に紐づけるための、より活発に保守されて
いる手段がすでにあります。project を org の下に登録することです
（[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)）。`targets` まで移せば、
この手段を重複させるだけです。この項目のメンバーシップ管理という範囲が、それで完結するわけでも
ありません。

`orgs_from_db(repository: Repository) -> dict[str, OrgConfig]` 関数を新設し、同じモジュールの
`parse_orgs` と並べて置きます。この関数は、すべての `Org` 行とそのメンバーシップカラムを
問い合わせ、`parse_orgs` が YAML から組み立てるのと同じ `dict[str, OrgConfig]` の形を組み立てます。
`targets` は常に空にします。この項目のもとでは、データベース由来の org はどの target も所有しない
ためです。repository を配線したあとは、`oauth_callback` 内でメンバーシップを参照するすべての箇所が、
このデータベース由来の辞書を読みます。例外はなく、config から解析した辞書への引き当て直しもありま
せん。対象は4つです。サインインゲートの `identity_matches_org`。サインインしたユーザーの所属を
決める `org_for_identity`。マッチした `OrgConfig` から取り出して `role_for` へ引数として渡す
`editor_team`（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）。そして、ユニット3が
読み替える拒否時の診断です。config を読み続ける参照箇所は、target の解決だけです。`targets_for_org`
と `org_for_target` は、どちらも org が実際に所有する target の情報を必要とします。後者を呼ぶのは
`authz.py` の `_target_forbidden` です。ところが、それを持つのは config 由来の辞書だけです。
その解決が何に変わるべきかは、ユニット4で扱います。メンバーシップを、同じ辞書の形をとる2つめの
生成元へ通すという考え方は、
[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) 自身が login
リストを GitHub Team のメンバーシップへ置き換えたときに使った原則と同じです。方針は、サインイン
ごとにその出どころから再計算されるので、出どころを変えても、解決ロジック自体にはデータマイグレー
ションが要りません。

### 2. デプロイごとに org の出どころは1つ、選ぶのは一度きり

`oauth_callback`（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）は、org モデルを関数の
先頭近くで一度だけ読み、その1つの辞書を以降のすべてのチェックへ渡します。今日それは、どのデプロイ
でも常に `load_serve_config_file(state.config)` が解析した `orgs:` ブロックです。この項目は、その
一度きりの読み取りを、デプロイの形態だけで分岐させます。repository を配線していれば `orgs_from_db`、
そうでなければ設定ファイルに対する `parse_orgs` です。分岐の条件は `state.repository is not None`
で、serve の他のデータベース連動の仕組みがすでに使っているものと同じです。データベースを
配線したデプロイにとっての帰結が、この項目の題名そのものです。誰がサインインできるかを決めるのは
データベースであり、設定ファイルはその判断に一切関与しなくなります。

サインインゲートの*位置づけ*は変わりません。`members` / `github_orgs` の一致チェックは、
`org_for_identity` を呼ぶ前に走ります。そして `if state.repository is not None:` ブロックの手前に
置かれたままです。データベースを持たない OAuth 設定済みデプロイでも、サインインをゲートし続ける
ためです
（[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md)）。変わるのは、
そのチェックの参照先だけが、データベースの配線有無で決まるようになる点で、
`oauth_callback` の中でチェック自体がどこで走るかは変わりません。

出どころをゲートの手前で選ぶことは、今日そこにある運用上の危険な結びつきも断ちます。
`load_serve_config_file` は `None` へフェイルクローズし、`orgs` は空のマッピングへ潰れ、
`identity_matches_org` は誰にもマッチしなくなります。その結果、データベースを配線したデプロイでも、
設定ファイルが読めない・壊れている・まだバインドされていないというだけで、admin 以外のすべての
サインインが拒否されます。そのユーザーも、所属する org も、ロールも、すべてデータベースに行として
あるにもかかわらずです。この項目のあとは、そのデプロイで config が読めないことによって止まるのは、
ユニット6の引き込みと、解決対象を失うユニット4の target 解決だけです。サインイン・org への割り当て・
ロールの付与は、データベースから動き続けます。

データベースを持たないデプロイは、今日と変わらず設定ファイルに対して
`parse_orgs` を呼び続け、org 管理用の admin UI も持ちません。そのデプロイ形態は、そもそもローカルで
単一ユーザー向けに作られています
（[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md) 自身の動機が示す
とおりです）。管理すべきテナントの境界がなく、
[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) はすでに、そこでは
`forbidden_for_role` がロールに関係なくフルアクセスへ短絡すると述べています。データベースを持たない
デプロイの org モデルは、サインインそのものをゲートするためだけに存在し、ロールを付与するためでは
ありません。したがって、org 単位のメンバーシップストアからそのデプロイが得るものは、この項目の
設計には何もありません。

### 3. 拒否時の診断を、データベースという出どころに合わせて読み替える

`_unmatched_org_cause`（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）は、ある login が
どの org にもマッチしなかった原因を、5つの形のいずれかとして名づけます。あわせてログレベルも
決めます。`parsed is None or not orgs` のとき、拒否も bypass による受け入れも WARNING で記録します。
config 側の不具合なら運用者が手を打つべきですが、単なる名簿の不一致にその必要はない、という判断
です。この5つのうち3つは、ファイルだけが取りうる形です。config がまだバインドされていない、
config の読み込みが失敗した、config が `orgs:` ブロックを宣言していない、という3つです。したがって
原因の分類とログレベルは、データベースという出どころへ合わせて読み替えない限り通用しません。
持ち越せば、もはや何も決めていないファイルを調べるよう運用者へ告げることになります。

データベースを配線したデプロイでは、この3つは1つに畳まれます。すなわち、この login にマッチする
有効な `Org` 行がテーブルにない、という形です。そして代わりに現れる失敗は、そもそも原因の分類器まで
届きません。この項目は `orgs_from_db` に、`load_serve_config_file` とは逆の失敗の仕方を与えます。
データベースのエラーは、空のマッピングへフェイルクローズせずにそのまま伝播します。serve が読めない
データベースは、GitHub のメンバーシップのせいだと読めるメッセージで全員を拒否するのではなく、
データベースを名指しする 5xx として応答します。したがって、そこで報告すべき原因は3つに絞られます。
`orgs` テーブルのどの行もまだメンバーシップを宣言していないこと。GitHub がこの login に対して
Organization を1つも返さなかったこと。そして、実在する名簿にマッチしなかったことです。1つめは、
何ひとつ作成も引き込みもされていない状態か、作成されていても誰も受け入れない状態であり、ユニット7
の迂回が存在するのはこの形のためです。WARNING は、運用者が手を打てる1つ、すなわち誰も所属していない名簿のために取っておきます。
行の有無ではなくメンバーシップを基準にするのは、この WARNING が一度きりで鳴らなくなるのを防ぐため
です。この WARNING が報告する迂回のサインインは、その途中で `ensure_org` を呼び、受動的な
`default` 行を残します。テーブルが空であることを基準にすると、admin Team のメンバー以外は誰も
サインインできないままなのに、2回目以降のサインインは INFO へ落ちてしまいます。

その隣にある回復用のガードも、翻訳して持ち込むのではなく取り除きます。`not matched_org and
parsed is None and state.config is not None` という条件のものです。config の読み込みが一度失敗した
ときに、
bypass で受け入れたユーザーを `default` へ移さず、記録済みの org のまま維持するものです。しかし
`if state.repository is not None:` ブロックの内側にあり、動いていたのはデータベースを配線した
デプロイだけでした。まさに、org の出どころがファイルでなくなるデプロイです。その動機となった失敗は
原因ごと消えます。org をデータベースが決めるユーザーを、config の読み込みが取り違えることはありま
せんし、`orgs_from_db` は空の名簿を黙って差し出す代わりに例外を投げるからです。データベースを持た
ないデプロイは、そもそもこのブロックに入らないため、失う保護もありません。

### 4. target の同一性を (org, target) にする

target の所有は config に残しますが（ユニット1）、その*解決の仕方*は、org を好きなだけ作れる admin
のもとでは成り立ちません。`org_for_target`
（[`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)）は、target 名だけを手がかりに、その名前を
`targets` に挙げている最初の org を返します。`_target_forbidden` はまさにそれを根拠として、他の
すべての org へその target を禁じます。つまり、`checkout` を挙げた org が2つあっても、それぞれが `checkout` を
持てるわけではありません。config の記述順が、先に来たほうへ黙って与えます。もう一方は、その
target への操作をすべて禁じられます。しかも、その org には `checkout` が見えています。`targets_for_org` は、org 自身の
エントリがその名前を挙げていれば一覧に出すからです。結果として、ある org の一覧には出るのに読み取り
はすべて拒否される target ができ、どちらの org が勝ったのかは設定ファイルのどこにも書かれません。

手で編集する `orgs:` ブロックであれば、この衝突は同じ差分の中でレビューが気づける間違いでした。
ユニット5によって admin が Web UI からテナントを作れるようになり、しかも他のテナントのエントリを
見られないとなれば、これは日常的に起こります。そして症状は、名前の衝突ではなく権限のバグに見えます。

そこでこの項目は、org の target 所有を、名前単位ではなく org 単位で解決するようにします。
`_target_forbidden` は、その名前がどの1つの org に解決されるかではなく、*その* org 自身の一覧に
その target が含まれるかを問います。これにより、`org_for_target` の「名前から org を引く」検索は
呼び出し元を失います。ただしこの問いは、`orgs:` エントリを直接読むのではなく `targets_for_org` を
経由して立てる必要があります。`targets_for_org` 自体に変更は要りません。一方でそのフォールバック
は、「どのエントリにも名指しされていない org」ではなく `DEFAULT_ORG` という**リテラルの slug** を
条件にしています。つまり、どのエントリも主張していない target をすべて得るのは `default` だけです。
ブロックに現れない他のすべての org は、`orgs.get(org) is None` の分岐に落ち、何も所有しません。
エントリを直接読めば、`default` は今日到達できているすべての target を禁じられます。デプロイは
`default` のエントリ自体を宣言しないのが通常だからです。`targets_for_org` を経由することで変わる
形が1つだけあり、その向きは整合の側です。`default` という名前の org を、独自の `targets:` 付きで
宣言しているデプロイは、そのエントリが挙げる target への認可を失います。`targets_for_org` が、
エントリを読むより先にリテラルの slug で `default` を判定するためです。失われるものは、使えていた
ものではありません。`targets_for_org` はもともとその org に対してそれらの target を一覧しなかった
ので、退役する名前ベースの解決は、同じデプロイが一度も見せなかった target を認可していたことに
なります。どのエントリも主張していない target を `default` が得るフォールバック自体は、そのままです。
以後、2つの org がそれぞれ `checkout` と
いう名前の target を主張でき、どちらもその target に対して認可されます。バインドされた config が
1つである以上、その名前が指す `targets:` の定義自体は共有されたままです。org ごとに定義を持たせるには、org ごとに config をバインドする必要が
あります。それは
[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md) の project ごとの config
バインディングが向かっている先であり、この項目が意図して立ち止まる地点です。先に*同一性*を
`(org, target)` にしておけば、その次の一歩で「その名前を誰が所有するのか」を決め直さずに済みます。
これは BE-0225 の project registry が project にすでに使っている鍵と同じです。その `add` と
`get` は `(org_id, name)` を鍵にしています
（[`bajutsu/serve/project_registry.py`](../../bajutsu/serve/project_registry.py)）。

同じ `orgs.get(org) is None` の分岐は、ユニット5の API で作成した org が何を所有するかも決めます。
そしてその答えは、何も所有しない、です。そうした org は `orgs:` エントリを持たないため、誰かが
エントリを追加するまで `targets_for_org` は空の一覧を返します。この項目は、これを塞がずに受け入れ、
そのことを明記します。admin が Web UI からテナントを迎え入れても、なお config の編集を待つ箇所が
ここだけ残るからです。org は作成された時点で存在し、メンバーを受け入れ、管理もできます。しかし
`orgs:` エントリが target を名指しするまで、どの target にも認可されません。これを塞ぐには2つの道が
あります。1つは、admin が設定できる target を org に与えることです（ユニット1が却下した、
データベースへ移す案です）。もう1つは、org ごとに config をバインドすることです（*検討した代替案*
に記録した先送りです）。どちらもこの項目より大きな変更であり、admin が作成した org が実在するように
なってから判断するほうが適切です。

### 5. admin 用 API と UI：作成・削除・メンバーシップの編集

4つのエンドポイントを設けます。すべて admin 限定です。org のメンバーシップは、他に誰がサインイン
でき、誰が書き込めるかを決めます。project の登録や config の再バインドに BE-0225 がすでに与えて
いる感度と、同じ水準です。いずれの変更も、既存の `Repository.record_audit` を通じて記録します。
記録するアクションは `org.create` / `org.delete` / `org.membership.update` で、対象は org の slug
です。

- `GET /api/orgs` — 生存しているすべての org を一覧します。slug、name、`members` / `githubOrgs` /
  `editorTeam` そのもの、project の件数を返します。件数ではなく名簿そのものを返すのは、下のメンバー
  シップフォームが3つのフィールドを一括で置き換えるため、現在の値から始める必要があるからです。この
  エンドポイントは admin 専用であり、admin はすでに `GET /api/config/content` から `orgs:` ブロック
  全体を読めます。
- `POST /api/orgs` — `{slug, name}` から org を作成します。メンバーシップは空から始まります
  （member・GitHub Organization・editor Team のいずれもなし）。admin が追加するまで、新設した org は
  誰も受け入れません。作成した行の `id` は、その slug とします。既存の書き込み側がすでにそう
  しているためです。`ensure_org(org, slug=org, name=org)` は、3つのカラムすべてに1つの文字列を
  入れます。その同じ文字列を、`upsert_user(org_id=…)`・`state.org_of()`・org 単位のストアが
  持ち回ります。
  したがって `orgs_from_db` は、`parse_orgs` が `orgs:` エントリ名を鍵にするのと同じように、その
  文字列を鍵にできます。slug と異なる id を生成すれば、これらすべての経路で slug から id を引く
  処理が要りますが、この項目はそれを導入しません。作成した行には、ユニット6が一本化の際に立てるのと同じ引き込み済みマーカーを
  作成時点で立てます。この行はすでに一本化を終えたものとして扱われるため、以後どの `orgs:` エントリ
  もこの行を引き込むことはなく、admin がこの API で設定したメンバーシップを上書きすることもありません。
- `POST /api/orgs/<slug>/membership` — org の `{members, githubOrgs, editorTeam}` を一括で
  置き換えます。エントリ単位の追加・削除エンドポイントに分けず、設定ファイルの編集がすでに持つのと
  同じ粒度にします。値全体を書き換える操作は通常 `PUT` で表しますが、ここでは `POST` を使います。
  `PUT` は、どちらのトランスポートも実装していない唯一の本文つきメソッドだからです。標準ライブラリ
  のハンドラは `GET` / `POST` / `DELETE` だけを提供し、FastAPI 側の生成器も本文を解析するのは
  `POST` に限られます。この1本のルートのために両方のトランスポートを広げるのは、この項目が他に必要と
  しない横断的な変更です。`serve` の値全体を書き換える他の操作も、すでにすべて `POST` です
  （`/api/projects/<name>/activate`、`/api/provider`）。
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
end-to-end で UNIQUE 制約があります
（[`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py)）。ソフトデリート済みの
行が、すでにその slug を占有しているからです。そのため `POST /api/orgs` は、その slug を専用の 409 エラーで
拒否します。行を復活させることも、再利用することもしません。再有効化が必要になる場合は、将来の
項目に委ねる、別の明示的な操作とします。ユニット6の引き込みも、`deleted_at` が立っている行は対象にしません。ソフト
デリート済みの org は未引き込みではなく廃止済みであるため、引き込まず、復活もさせません。

serve のシェルには、admin 限定の新しい **Orgs** ページを追加します。
[BE-0275](../BE-0275-serve-projects-management-page/BE-0275-serve-projects-management-page-ja.md)
の Projects ページと並ぶ位置づけです。org の一覧に、作成と削除の操作を添えます。org がまだ project
を持つ間は削除を無効にし、その件数を表示します。あわせて、org ごとのメンバーシップ編集フォーム
（member・GitHub Organization・editor Team）を置き、上記の `POST` エンドポイントを呼びます。この API とページは
いずれも、ユニット2のゲートに従い、repository を配線している場合にのみ存在します。

### 6. 一度きりの引き込みと、その後の一本化

config だけでメンバーシップを管理していたデプロイを、この項目にアップグレードする場合を考えます。
既存の `orgs:` ブロックを、admin UI が表示・編集できる形でデータベースへ反映する必要があります。
この引き込みは、バインドされた config がどちらの経路から届いても、単一の仕組みで行います。対象と
なるタイミングは2つです。起動時の `_build_server_state` と、再バインド時の
`bind_config` / `bind_git_config` です。後者を呼ぶのは `POST /api/config` です
（[`bajutsu/serve/operations/config.py`](../../bajutsu/serve/operations/config.py)）。
serve は、バインドされた config が宣言する org のうち、まだ引き込み済みでない org を選びます。
引き込み済みかどうかは、永続化した行単位のマーカーが示します。`orgs:` ブロックの各エントリに
ついて、その org 行にマーカーが立っていない場合を考えます。このとき専用の引き込みメソッドが、その
エントリから `members` / `github_orgs` / `editor_team` を作成または更新し、マーカーを立てます。
`ensure_org` ではありません。`oauth_callback` と run の完了処理は、すでにこれを
`ensure_org(org, slug=org, name=org)` という形で呼んでいます。サインインのたび、そして run の
完了のたびです。どちらも渡すメンバーシップを持ちません。データベースを配線したデプロイでは、ユニット2により出どころが
データベースになります。したがって、渡せるメンバーシップもありません。これを作成兼更新へ広げれば、
2つのうちどちらかが起きます。省略した引数によって1つのメソッドが2つの意味を持つか、次のサインイン
が、admin がユニット5のメンバーシップエンドポイントで設定したメンバーシップを消してしまうかです。後者は、このユニットのマーカーが
防ぐために存在する上書きが、別の入口から現れたものです。`ensure_org` は今日と同じ冪等な作成のまま
とし、メンバーシップへの書き込み口はユニット5の API とこの引き込みだけに保ちます。起動時だけでなく
再バインド時にも同じ引き込みを走らせるのは、config が serve に届く2つの経路のどちらでも正しく動く
ようにするためです。この2つは、この項目自身の動機が挙げたものです。起動時にしか引き込まなければ、
再バインドで新しく宣言された org は永遠に引き込まれません。そして後述の一本化がそのデプロイに効いた
時点で、その org へのログインはすべて失敗します。マーカーは各行の引き込み済み状態を直接記録します。
メンバーシップカラムがすでに値を持つかどうかから推定することはしません。マーカーがなければ、admin が
意図的にメンバーシップを空にした引き込み済みの org と、まだ引き込んでいない org を区別できません。
区別できなければ、後の起動や再バインドで設定ファイルの古い値に上書きされ、admin が外したはずの
サインインが復活してしまいます。`members` / `github_orgs` / `editor_team` の各カラムと引き込み済み
マーカーのカラムを追加する Alembic マイグレーションは、カラムを追加するだけです。データの引き込み
自体は行いません。引き込みは、起動時と再バインド時に serve 自身が行う仕事であり、マイグレーション
にはできません。Alembic のマイグレーション環境が読むのは `BAJUTSU_DATABASE_URL` だけだからです
（[`bajutsu/serve/server/migrations/env.py`](../../bajutsu/serve/server/migrations/env.py)）。
バインドされた config の `orgs:` ブロックには、一切アクセスできません。

ある org の行にマーカーが立った後は、`oauth_callback` はその org のメンバーシップをデータベースから
だけ読みます。以後、設定ファイルの `orgs:` ブロックのそのエントリを編集しても効果はありません。これ
は、
[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) と
[BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass-ja.md) の
どちらも、同じロールを決めるデータに2つの独立した出どころを持たせる案より、一本化を選びました
（*検討した代替案* を参照）。まったく新しい `orgs:` エントリが次の起動または再バインドで通常どおり
引き込まれるのは、2つの場合に限られます。その行がまだ存在しない場合と、マーカーのない受動的な行で
ある場合です。後者は、サインイン時に `ensure_org` が作成した、id・slug・name だけを持つ行を指します。
ユニット5の API を通じて作成した org には、作成時点で引き込み済みマーカーが立ちます（ユニット5を
参照）。したがって、同じ slug を指す後続の `orgs:` エントリが引き込まれることはありません。
serve は、3つの条件がすべて成り立つとき、実行のたびに1度だけ警告を出します。repository を配線して
いること。config をバインドしていること。そして、引き込み済み org の `orgs:` エントリが
`members` / `githubOrgs` / `editorTeam` のいずれかをまだ宣言していることです。`targets` だけを持つ
`orgs:` エントリは、カットオーバー後も正当であり、残り続けることが期待されます。ユニット1のとおり、
target の所有情報は config が持ち続けるためです。単に空でない `orgs:` ブロックだけを条件にすると、
適切に設定されたデプロイで永遠に鳴り続けます。あるいは、警告を止めようと `orgs:` を空にするよう
運用者を仕向け、target の所有情報を失わせてしまいます。これは、
[BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass-ja.md) が
自らの廃止した環境変数についてすでに出している、「運用者がもう読まれていないことを忘れている」
という同種の警告です。

repository を最初の起動から配線している、config だけの履歴を持たない新規デプロイを考えます。その
最初の起動がバインドする `orgs:` ブロックを対象に、引き込みが走ります（通常は空なので、引き込む
ものはありません）。そのデプロイが持つ org は、以後2種類だけです。ユニット5の API を通じて admin が
作成した org か、その後の起動または再バインドで同じように引き込まれる新しい `orgs:` エントリです。

### 7. admin Team のブートストラップ迂回が、テーブルが空の場合の答えになる

新設されたばかりのデータベース連動デプロイには、admin が作成するまで org が1つもありません。これは
ブートストラップの問いを引き起こします。最初の1つを作成するには、誰がサインインすればよいの
でしょうか。同じ問いには、`orgs:` ブロックが壊れている場合や存在しない場合について、
[BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass-ja.md) が
すでに答えています。`BAJUTSU_OAUTH_ADMIN_TEAMS` は、この項目のデータベースへの移行から意図的に
外したまま、環境変数のままにします。org モデル自身が、どこに置かれていようと、まだ誰も受け入れ
られない状態を回復するための経路だからです。設定した admin Team のメンバーは、`Org` 行がいくつ
存在するかに関係なく、この迂回を通じてサインインゲートを通過します。そして admin ロールで
サインインします（この項目による変更はありません。
[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) の「admin は
サーバー全体で1つの階層のまま」）。そのうえで、ユニット5の API を使って、このデプロイの最初の org
を作成します。鶏と卵の問題は起きません。この項目が意図的にデータベースの外に残したテナンシーの
データこそが、`orgs` テーブルが空の状態を回復可能にしているからです。

## 検討した代替案

- **org のメンバーシップを設定ファイルに残し、外部で編集した後 `POST /api/config` の再バインドを
  admin に引かせる。** 却下します。これは今日の仕組みそのもので、まさにこの項目が取り除きたい依存
  関係です。テナントを1つ迎え入れるたび、あるチームを別の GitHub Team へ移すたびに、デプロイの
  設定リポジトリへの書き込み権限を持つ人と、再デプロイまたは再バインドが要ります。
- **`ProjectRegistry` に倣ったレジストリのシームを設ける。**
  [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md) と同じく、データベース
  連動とファイル連動の両方の経路を持たせる案です。データベースを持たない serve でも、admin が
  org のメンバーシップを管理できるようになります。却下します。データベースを持たない serve は、そもそも
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
- **target の所有は名前単位の解決のままにし、他の org の target 名と衝突する org の作成を
  `POST /api/orgs` で拒否する。** 却下します。この衝突は、admin が避けるべきものではありません。
  org が主張する target 名は設定ファイルの中にあり、org を作成する admin はそれを見られません。
  そもそもメンバーシップをそのファイルの外へ移したデプロイでは、admin がそれを読む理由もありません。
  検証で衝突を弾けば、その org 自体とは無関係な理由で org の作成が失敗することにもなります。
  ユニット4であれば、2つの org はそのまま共存します。
- **同一性を `(org, target)` にするだけでなく、org ごとに config をバインドして org ごとの
  `targets:` 定義を持たせる。** 却下ではなく、先送りします。これは自然な最終形であり、
  [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md) の project ごとの
  config バインディングがすでにその方向を指しています。ただし、serve が同時に複数の config を
  バインドできる必要があり（今日の `state.config` は単一のパスです）、これは org モデルではなく
  config のバインディングに対する変更です。したがって、それを行う項目に属し、この項目には属しません。
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

- [x] 1 — `Org` テーブルに `members` / `github_orgs` / `editor_team` カラムを追加する（Alembic
      マイグレーション）。あわせて `orgs_from_db` を追加する。`parse_orgs` が設定ファイルから
      組み立てるのと同じ形を、`targets` を常に空にして組み立てるものとする。
- [x] 2 — `oauth_callback` の org の出どころを、サインインゲートの手前で一度だけ選ぶようにする。
      `state.repository is not None` なら `orgs_from_db`、そうでなければ `parse_orgs` とし、両方を
      読む呼び出し元も、一方から他方へ引き当て直す経路も設けない。データベースを持たないデプロイの、
      設定ファイル由来の経路は変更しない。
- [x] 3 — 拒否時の診断を、データベースという出どころに合わせて読み替える。
      `_unmatched_org_cause` の config 由来の3つの原因を「マッチする有効な `Org` 行がない」に畳み、
      `orgs_from_db` は空のマッピングへフェイルクローズせずデータベースのエラーを伝播させ、
      WARNING はどの行もまだメンバーシップを宣言していない場合を基準にし、`parsed is None` を
      条件とする org の回復ガードは翻訳せずに取り除く。
- [x] 4 — target の所有を、名前単位ではなく org 単位で解決する。`_target_forbidden` は、その org
      自身の `targets_for_org` の一覧にその target が含まれるかを問う。`org_for_target` は呼び出し元
      を失う。問いは `targets_for_org` を経由させ、`default` がリテラルの slug によるフォールバック
      で得ている未主張の target を失わないようにする。これにより、2つの org がそれぞれ同じ名前の
      target を主張できるようにする。config の記述順が一方に与え、他方には見えているだけの target を
      禁じる、という今日の挙動を置き換える。
- [x] 5 — 4つの `/api/orgs…` エンドポイントと、Orgs 用の admin ページを、repository を配線して
      いる場合にのみ用意する。エンドポイントは admin 限定とし、いずれも `record_audit` で記録する。
      ページは作成・空のときのみ削除・メンバーシップ編集を扱う。`POST /api/orgs` は作成時点で新しい行に引き込み済みマーカーを
      立て、以後どの `orgs:` エントリもその行を引き込まないようにする。削除はソフトデリート
      （`Org.deleted_at`）とする。行を削除せず、外部キーを壊すこともなく、その org をサインインの
      解決処理と `GET /api/orgs` の対象から外す。
- [x] 6 — バインドされた設定ファイルの `orgs:` ブロックをデータベースへ引き込む処理を追加する。
      repository を配線し、かつ config をバインドしている状態になるたび走らせる。起動時と
      `POST /api/config` の再バインド時の両方が対象となる。`Org` 行ごとの永続化したマーカーで
      一度きりを保証し、メンバーシップカラムの空欄からの推定はしない。Alembic マイグレーションは
      メンバーシップとマーカーのカラムを追加するだけで、引き込み自体は行わない。引き込み済みの
      org の `orgs:` エントリが `members` / `githubOrgs` / `editorTeam` を宣言している場合は、
      起動時・再バインド時に警告する。`targets` だけを持つエントリは、警告の対象外とする。
- [x] 7 — ゲートの判断が設定ファイルからテーブルへ移ったうえで、admin Team の迂回を確認する。
      `orgs` テーブルが空、あるいはどの行にもマッチしない状態でも、サインインを許可すること。
      `BAJUTSU_OAUTH_ADMIN_TEAMS` は環境変数のまま残す。
- [x] 8 — テスト：`orgs_from_db` が、同等の `orgs:` ブロックについて `parse_orgs` と同じ解決結果を
      再現すること。データベースを持たない経路に影響がないこと。データベースを配線したデプロイで、
      config の読み込み失敗がサインインを拒否しなくなること、およびデータベースのエラーが拒否では
      なく 5xx として現れること。同じ名前の target をそれぞれ主張する2つの org が、どちらもその
      target に対して認可されること。API を通じた org の作成・
      メンバーシップの置き換え・空でない org の削除拒否がいずれも admin 限定で記録されること。
      引き込みが一度だけ走り、その後の設定ファイルの `orgs:` 編集が効果を持たないこと。admin Team
      の迂回（[BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass-ja.md)）が、
      `orgs` テーブルが空の状態でもサインインを許可すること。その admin が、デプロイの最初の org を
      作成できること。

## 参考

- [BE-0015 — Web UI のパブリックホスティング](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)。
  この項目がデータベースへ移す `orgs:` マルチテナンシーモデル。および、この項目の `Org` テーブルが
  すでに支えている `projects` / `runs` スキーマの `org_id` 外部キー。
- [BE-0313 — GitHub Organization メンバーシップと Team ベースの RBAC を serve に導入](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md)。
  この項目が移す3つのフィールドと、引き継ぐ *用語* 節。および、`orgs_from_db` という新しい
  出どころが従う「サインインごとに再計算し、データマイグレーションを要さない」という原則。
- [BE-0352 — admin 用 GitHub Team の環境変数が Organization メンバーシップのサインインゲートを迂回する](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass-ja.md)。
  この項目が変更せずに残す admin Team のブートストラップ迂回と、引き込み処理が従う一本化の前例です。
- [BE-0225 — serve の config プロジェクトハブ（登録・一覧・切替・実行）](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)。
  *検討した代替案* が既存の手段として挙げる project レジストリ。および、この項目の org 削除が従う
  「登録解除後も履歴を残す」前例です。
- [BE-0275 — serve の projects 管理ページ](../BE-0275-serve-projects-management-page/BE-0275-serve-projects-management-page-ja.md)。
  この項目の Orgs ページが手本にする admin ページです。
- [BE-0170 — org 単位の公平な dispatch の重み付け](../BE-0170-weighted-fair-org-dispatch/BE-0170-weighted-fair-org-dispatch-ja.md)。
  **提案** の状態にある項目です。この項目によって admin が org を作成できるようになると、その
  意義は増します。動的に増えていくテナントの集合こそ、BE-0170 の公平性の仕組みが対象とする形だから
  です。
- [`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py) — `OrgConfig` と `parse_orgs`。この項目は
  その隣に2つ目の生成元（`orgs_from_db`）を置きます。`identity_matches_org` と `org_for_identity`
  は振る舞いを変えず出どころだけが変わり、`org_for_target` は唯一の呼び出し元を失います。
- [`bajutsu/serve/project_registry.py`](../../bajutsu/serve/project_registry.py) — project がすでに
  持つ `(org_id, name)` という鍵。この項目の target の同一性はこれに倣います。
- [`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py) — この項目がメンバー
  シップ用のカラムを追加する `Org` テーブル。
- [`bajutsu/serve/server/db.py`](../../bajutsu/serve/server/db.py) — `Repository.ensure_org`。
  この項目はこれを冪等な作成のまま残し、その隣に専用の引き込みメソッドを置きます。および、この
  項目の `OrgRecord` とその作成・削除・メンバーシップ更新の各操作が手本にする
  `ProjectRecord` / `create_project` / `delete_project` の形。
- [`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py) — `oauth_callback` のサインインゲート。
  この項目が変えるのはそのデータの出どころであり、位置づけではありません。あわせて、新しい出どころ
  に合わせて読み替える `_unmatched_org_cause` と、org 単位で解決し直す `_target_forbidden`。
- [`bajutsu/serve/operations/projects.py`](../../bajutsu/serve/operations/projects.py) — この
  項目の `/api/orgs…` エンドポイントが手本にするエンドポイントと、その権限判定の形。

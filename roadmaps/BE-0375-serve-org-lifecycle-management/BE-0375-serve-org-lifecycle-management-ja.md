[English](BE-0375-serve-org-lifecycle-management.md) · **日本語**

# BE-0375 — serve の org のライフサイクルとメンバーシップをデータベースで管理する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0375](BE-0375-serve-org-lifecycle-management-ja.md) |
| 提案者 | [@paihu](https://github.com/paihu) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0375") |
| 実装 PR | [#1636](https://github.com/bajutsu-e2e/bajutsu/pull/1636)、[#1663](https://github.com/bajutsu-e2e/bajutsu/pull/1663)、[#1664](https://github.com/bajutsu-e2e/bajutsu/pull/1664) |
| トピック | Web UI のホスティング |
| 関連 | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)、[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md)、[BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass-ja.md)、[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)、[BE-0170](../BE-0170-weighted-fair-org-dispatch/BE-0170-weighted-fair-org-dispatch-ja.md) |
<!-- /BE-METADATA -->

## はじめに

この項目は、admin が serve の **org** を Web UI とそのアプリケーションプログラミングインタフェース（API）から作成・削除・編集できるようにします。org は Bajutsu のマルチテナンシーの単位であり、しばしば「テナント」とも呼ばれます。今は、デプロイの設定ファイルの `orgs:` ブロックの 1 エントリとして宣言します（[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)）。設定ファイルを編集して再デプロイする代わりに、admin がその場で操作できるようにするのが狙いです。

移す対象は、org の**メンバーシップ**です。メンバーシップは 2 つのデータからなります。

- どの GitHub login または GitHub Organization がその org としてサインインできるかを決める `members` / `githubOrgs`
- その org の editor がどの GitHub Team に属するかを決める `editorTeam`（[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md)）

移し先は、データベースを一度配線した serve（`BAJUTSU_DATABASE_URL`）がすでに動かしているデータベースです。データベースが org のメンバーシップを持つ以上、参照先はそのデータベースだけになります。サインインゲート自体も設定ファイルを読まなくなります。config の読み込みに失敗しても、すべてのサインインが拒否される事態は起きなくなります。

あわせて、config に残す target の所有が、admin による org 作成のもとで何を意味するのかも決めます。org の target は名前単位ではなく org 単位で解決します。これにより、2 つの org が同じ名前の target をそれぞれ主張できます。config の記述順が、どちらか一方に黙って与えることはなくなります。

データベースを持たないデプロイは、今のまま `orgs:` を設定ファイルから読みます。この項目は、その経路に変更を加えません。

## 動機

[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) は、環境変数の login リストを GitHub 自身の Organization と Team のメンバーシップに置き換えました。login リストは、GitHub がすでに保守している名簿を重複させ、実態から次第にずれていくからです。ただし BE-0313 は、1 つの名簿を置き換えずに残しました。`orgs:` ブロック自体です。どの GitHub Organization や GitHub Team をどの org に対応させ、誰をその editor とするかを決める名簿です。

GitHub 側には、Bajutsu の org に対応するものが何もありません。org という境界は、このデプロイ自身が定義するものです。GitHub から読み出せる代わりは存在しません。したがって、org をデータとして持つこと自体は、BE-0313 が解決した名簿の重複ではありません。ギャップがあるのは、そのデータの置き場所だけです。

今日、そのデータは、運用者が手で編集するファイルの中にあります。届ける経路は、再デプロイまたは `POST /api/config` の再バインドです。どのアプリケーションを serve がテストするかを変える経路と、まったく同じです。

この経路は、新しいテナントを迎え入れたい運用者には合いません。あるチームの編集権限を別の GitHub Team へ移したい運用者にも合いません。どちらも、デプロイの設定リポジトリや CI/CD パイプラインには触れずに済ませたいからです。この項目が対象とする種類の per-org なデータは、データベースを配線した時点でほぼすべて、すでにそちらへ移っています。

- 登録された project（[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)）
- AI provider の設定（[BE-0229](../BE-0229-per-org-provider-settings-resolution/BE-0229-per-org-provider-settings-resolution-ja.md)）
- secret
- audit log

`orgs` テーブル自体はすでに存在します。`org_id` の外部キーを持つのは `projects`・`provider_settings`・`secrets`・`audit_log` です（[`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py)）。しかし今のところ、このテーブルは設定ファイルの受動的な鏡でしかありません。`ensure_org` は、その org での最初のログイン時に、id・slug・name だけを持つ行を作成し、そこで止まります。`members`・`github_orgs`・`editor_team` のいずれも持ちません。

データベースを配線したデプロイでテナントを迎え入れるにも、データベースを持たないデプロイとまったく同じファイル編集と再デプロイが要ります。そのデプロイには、まさにこの種の運用データのために置かれた、手つかずのデータベースがすでにあるにもかかわらずです。

このデータを移すと、設定ファイルが暗黙のうちに支えていた前提が 2 つ表に出てきます。どちらも、org が数えるほどしかなく、人手で書かれているあいだしか成り立ちません。

- **そのファイルが常に読める。** `oauth_callback` はサインインを、解析済みの `orgs:` ブロックでゲートしています。解析に失敗すると、空の名簿へフェイルクローズします。1 行の書き間違いが、データベース側では誰が誰かをすでに正確に把握しているデプロイで、admin 以外のすべてのユーザーを拒否します。
- **2 つの org が同じ target 名を主張することはない。** 手で編集する 1 つのファイルなら、レビューで守れました。しかし admin が、他のテナントのエントリを見られない Web UI からテナントを作れるようになれば、誰にも守れません。

どちらも、org データの出どころを変えるこの機会に片づけるのがもっとも安く済みます。あとから安くなることはありません。

## 詳細設計

### 用語

この項目は、「org」「GitHub Organization」「project」を、[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) の *用語* 節が区別した意味のままに使います。その区別はここで書き直しません。要点は次のとおりです。

- **org** は、Bajutsu 自身のマルチテナンシーの単位です（`orgs:` の 1 エントリ）。
- **GitHub Organization** は、その org に誰が属するかを決める GitHub 側の identity の出どころです。
- **project** は、org の下に登録される config ソースへのバインディングです。1 つの org が複数の project を持てます。

この項目が対象とするのは org 自身、つまりその存在とメンバーシップです。org が対応する GitHub Organization と、org が持つ project は対象としません。どちらも変更はありません。

### 1. メンバーシップはデータベースへ、target の所有は config のまま

`OrgConfig`（[`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)）は、4 つのフィールドを持ちます。`members`・`github_orgs`・`editor_team`、そして `targets` です。`targets` は、このデプロイに設定された [target](../../docs/ja/glossary.md#target-app-device)（テスト対象のアプリケーション）のうち、その org が所有するものを指します。

この項目は、最初の 3 つを `Org` テーブル（[`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py)）に新設する 3 つのカラムへ移します。3 つをまとめて org の**メンバーシップ**と呼びます。誰がその org としてサインインでき、そのうち誰が書き込めるかを決めるものです。カラムの形は `OrgConfig` 自身の形をそのまま反映します。`members` と `github_orgs` は文字列の配列を持つ JSON カラムにします。`editor_team` は null を許容する文字列カラムにします。

`targets` は config に残します。理由は 2 つあります。

- target がどのアプリケーションを指し、どの backend とどの device で動くかは、アプリケーション固有の差分です。プライム・ディレクティブ 3（アプリケーション非依存のコア）が、admin の編集できるストアの外に置いているものです。
- データベースを配線したデプロイには、アプリケーションを org に紐づけるための、より活発に保守されている手段がすでにあります。project を org の下に登録することです（[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)）。`targets` まで移せば、この手段を重複させるだけです。この項目のメンバーシップ管理という範囲が、それで完結するわけでもありません。

`orgs_from_db(repository: Repository) -> dict[str, OrgConfig]` 関数を新設し、同じモジュールの `parse_orgs` と並べて置きます。この関数は、すべての `Org` 行とそのメンバーシップカラムを問い合わせます。そして、`parse_orgs` が YAML から組み立てるのと同じ `dict[str, OrgConfig]` の形を組み立てます。`targets` は常に空にします。この項目のもとでは、データベース由来の org はどの target も所有しないためです。

repository を配線したあとは、`oauth_callback` 内でメンバーシップを参照するすべての箇所が、このデータベース由来の辞書を読みます。例外はなく、config から解析した辞書への引き当て直しもありません。対象は 4 つです。

- サインインゲートの `identity_matches_org`
- サインインしたユーザーの所属を決める `org_for_identity`
- マッチした `OrgConfig` から取り出して `role_for` へ引数として渡す `editor_team`（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）
- ユニット 3 が読み替える、拒否時の診断

config を読み続ける参照箇所は、target の解決だけです。`targets_for_org` と `org_for_target` は、どちらも org が実際に所有する target の情報を必要とします。後者を呼ぶのは `authz.py` の `_target_forbidden` です。その情報を持つのは、config 由来の辞書だけです。その解決が何に変わるべきかは、ユニット 4 で扱います。

メンバーシップを、同じ辞書の形をとる 2 つめの生成元へ通すという考え方は、[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) 自身が login リストを GitHub Team のメンバーシップへ置き換えたときに使った原則と同じです。方針は、サインインごとに出どころから再計算されます。出どころを変えても、解決ロジック自体にはデータマイグレーションが要りません。

### 2. デプロイごとに org の出どころは 1 つ、選ぶのは一度きり

`oauth_callback`（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）は、org モデルを関数の先頭近くで一度だけ読み、その 1 つの辞書を以降のすべてのチェックへ渡します。今日その辞書は、どのデプロイでも常に `load_serve_config_file(state.config)` が解析した `orgs:` ブロックです。

この項目は、その一度きりの読み取りを、デプロイの形態だけで分岐させます。

- repository を配線していれば `orgs_from_db` を使います。条件は `state.repository is not None` で、serve の他のデータベース連動の仕組みがすでに使っているものと同じです。
- そうでなければ、設定ファイルに対して `parse_orgs` を使います。

データベースを配線したデプロイにとっての帰結が、この項目の題名そのものです。誰がサインインできるかを決めるのはデータベースであり、設定ファイルはその判断に一切関与しなくなります。

サインインゲートの*位置づけ*は変わりません。`members` / `github_orgs` の一致チェックは、`org_for_identity` を呼ぶ前に走ります。そして `if state.repository is not None:` ブロックの手前に置かれたままです。データベースを持たない OAuth 設定済みデプロイでも、サインインをゲートし続けるためです（[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md)）。変わるのは、チェックの参照先だけです。参照先は、データベースの配線有無で決まるようになります。`oauth_callback` の中でチェック自体がどこで走るかは変わりません。

出どころをゲートの手前で選ぶことは、今日そこにある運用上の危険な結びつきも断ちます。`load_serve_config_file` は `None` へフェイルクローズし、`orgs` は空のマッピングへ潰れ、`identity_matches_org` は誰にもマッチしなくなります。その結果、データベースを配線したデプロイでも、設定ファイルが読めない・壊れている・まだバインドされていないというだけで、admin 以外のすべてのサインインが拒否されます。そのユーザーも、所属する org も、ロールも、すべてデータベースに行としてあるにもかかわらずです。

この項目のあとは、そのデプロイで config が読めなくても、止まるものは 2 つだけです。

- ユニット 6 の引き込み
- 解決対象を失う、ユニット 4 の target 解決

サインイン・org への割り当て・ロールの付与は、データベースから動き続けます。

データベースを持たないデプロイは、今日と変わらず設定ファイルに対して `parse_orgs` を呼び続け、org 管理用の admin UI も持ちません。org 単位のメンバーシップストアから、そのデプロイが得るものはありません。理由は 3 つあります。

- そのデプロイ形態は、そもそもローカルで単一ユーザー向けに作られています（[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md) 自身の動機が示すとおりです）。管理すべきテナントの境界がありません。
- [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) はすでに、そこでは `forbidden_for_role` がロールに関係なくフルアクセスへ短絡すると述べています。
- データベースを持たないデプロイの org モデルは、サインインそのものをゲートするためだけに存在します。ロールを付与するためではありません。

### 3. 拒否時の診断を、データベースという出どころに合わせて読み替える

`_unmatched_org_cause`（[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)）は、ある login がどの org にもマッチしなかった原因を、5 つの形のいずれかとして名づけます。あわせてログレベルも決めます。`parsed is None or not orgs` のとき、拒否も bypass による受け入れも WARNING で記録します。config 側の不具合なら運用者が手を打つべきですが、単なる名簿の不一致にその必要はない、という判断です。

この 5 つのうち 3 つは、ファイルだけが取りうる形です。

- config がまだバインドされていない
- config の読み込みが失敗した
- config が `orgs:` ブロックを宣言していない

したがって、原因の分類とログレベルは、データベースという出どころへ合わせて読み替えない限り通用しません。持ち越せば、もはや何も決めていないファイルを調べるよう運用者へ告げることになります。

データベースを配線したデプロイでは、この 3 つは 1 つに畳まれます。この login にマッチする有効な `Org` 行がテーブルにない、という形です。代わりに現れる失敗は、そもそも原因の分類器まで届きません。この項目は `orgs_from_db` に、`load_serve_config_file` とは逆の失敗の仕方を与えます。データベースのエラーは、空のマッピングへフェイルクローズせずにそのまま伝播します。serve が読めないデータベースについては、GitHub のメンバーシップのせいだと読めるメッセージで全員を拒否しません。データベースを名指しする 5xx として応答します。

したがって、そこで報告すべき原因は 3 つに絞られます。

- `orgs` テーブルのどの行も、まだメンバーシップを宣言していない。何ひとつ作成も引き込みもされていない状態か、作成されていても誰も受け入れない状態です。ユニット 7 の迂回が存在するのは、この形のためです。
- GitHub がこの login に対して Organization を 1 つも返さなかった。
- 実在する名簿にマッチしなかった。

WARNING は、運用者が手を打てる 1 つ、すなわち誰も所属していない名簿のために取っておきます。行の有無ではなくメンバーシップを基準にするのは、この WARNING が一度きりで鳴らなくなるのを防ぐためです。この WARNING が報告する迂回のサインインは、その途中で `ensure_org` を呼び、受動的な `default` 行を残します。テーブルが空であることを基準にすると、admin Team のメンバー以外は誰もサインインできないままなのに、2 回目以降のサインインは INFO へ落ちてしまいます。

その隣にある回復用のガードは、`not matched_org and parsed is None and state.config is not None` という条件のものです。config の読み込みが一度失敗したときに、bypass で受け入れたユーザーを `default` へ移さず、記録済みの org のまま維持します。このガードも、翻訳して持ち込むのではなく取り除きます。ガードは `if state.repository is not None:` ブロックの内側にあり、動いていたのはデータベースを配線したデプロイだけでした。まさに、org の出どころがファイルでなくなるデプロイです。

ガードの動機となった失敗は、原因ごと消えます。

- org をデータベースが決めるユーザーを、config の読み込みが取り違えることはありません。
- `orgs_from_db` は、空の名簿を黙って差し出す代わりに例外を投げます。

データベースを持たないデプロイは、そもそもこのブロックに入らないため、失う保護もありません。

### 4. target の同一性を (org, target) にする

target の所有は config に残しますが（ユニット 1）、その*解決の仕方*は、org を好きなだけ作れる admin のもとでは成り立ちません。`org_for_target`（[`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)）は、target 名だけを手がかりに、その名前を `targets` に挙げている最初の org を返します。`_target_forbidden` はまさにそれを根拠として、他のすべての org へその target を禁じます。

つまり、`checkout` を挙げた org が 2 つあっても、それぞれが `checkout` を持てるわけではありません。config の記述順が、先に来たほうへ黙って与えます。もう一方は、その target への操作をすべて禁じられます。しかも、その org には `checkout` が見えています。`targets_for_org` は、org 自身のエントリがその名前を挙げていれば一覧に出すからです。結果として、ある org の一覧には出るのに読み取りはすべて拒否される target ができます。どちらの org が勝ったのかは、設定ファイルのどこにも書かれません。

手で編集する `orgs:` ブロックであれば、この衝突は同じ差分の中でレビューが気づける間違いでした。ユニット 5 によって admin が Web UI からテナントを作れるようになり、しかも他のテナントのエントリを見られないとなれば、衝突は日常的に起こります。そして症状は、名前の衝突ではなく権限のバグに見えます。

そこでこの項目は、org の target 所有を、名前単位ではなく org 単位で解決するようにします。`_target_forbidden` は、その名前がどの 1 つの org に解決されるかを問いません。*その* org 自身の一覧にその target が含まれるかを問います。これにより、`org_for_target` の「名前から org を引く」検索は呼び出し元を失います。以後、2 つの org がそれぞれ `checkout` という名前の target を主張でき、どちらもその target に対して認可されます。

ただし、この問いは `orgs:` エントリを直接読むのではなく、`targets_for_org` を経由して立てる必要があります。`targets_for_org` 自体に変更は要りません。一方でそのフォールバックは、「どのエントリにも名指しされていない org」ではなく、`DEFAULT_ORG` という**リテラルの slug** を条件にしています。

- どのエントリも主張していない target をすべて得るのは、`default` だけです。
- ブロックに現れない他のすべての org は、`orgs.get(org) is None` の分岐に落ち、何も所有しません。

エントリを直接読めば、`default` は今日到達できているすべての target を禁じられます。デプロイは `default` のエントリ自体を宣言しないのが通常だからです。

`targets_for_org` を経由することで変わる挙動が 1 つあります。その変化は、整合性を高める方向のものです。`default` という名前の org を、独自の `targets:` 付きで宣言しているデプロイは、そのエントリが挙げる target への認可を失います。`targets_for_org` が、エントリを読むより先にリテラルの slug で `default` を判定するためです。失われるものは、使えていたものではありません。`targets_for_org` はもともとその org に対してそれらの target を一覧しませんでした。退役する名前ベースの解決は、同じデプロイが一度も見せなかった target を認可していたことになります。どのエントリも主張していない target を `default` が得るフォールバック自体は、そのままです。

バインドされた config が 1 つである以上、その名前が指す `targets:` の定義自体は共有されたままです。org ごとに定義を持たせるには、org ごとに config をバインドする必要があります。それは [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md) の project ごとの config バインディングが向かっている先であり、この項目が意図して立ち止まる地点です。

移行のときに変わる挙動は、この項目の導入部が挙げる 2 つのほかにもう 1 つあり、より狭い範囲に限られます。発見に任せず、ここに記録しておきます。

- `org_for_identity` は、明示の `members` に載っていない login を、`githubOrgs` が一致する*最初の* org に解決します。
- この「最初」が、`parse_orgs` では宣言順、`orgs_from_db` では slug 順です（`list_orgs` が slug で並べます）。
- どちらも安定していて、同じ login は毎回同じ org に入ります。タイブレークに求められるのはまさにこの安定性です。ただし、2 つの org が同じ GitHub organization を挙げているデプロイでは、この解決先が移行のときに一度だけ動くことがあります。

これを防ぐ案は、どちらもタイブレークより大きな話です。

- 宣言順を保持するには、位置を表す列と、「Orgs ページで作った org はどこに入るのか」への答えが要ります。
- 曖昧な一致をその場で拒否する案は、今サインインできている login を締め出します。

本当の答えは、複数の org に属する login が、そのどれとして振る舞うかを選べるようにすることです。それには `users.org_id` が単一の列であることをやめる必要があり、別の項目になります。

先に*同一性*を `(org, target)` にしておけば、その次の一歩で「その名前を誰が所有するのか」を決め直さずに済みます。これは BE-0225 の project registry が project にすでに使っている鍵と同じです。その `add` と `get` は `(org_id, name)` を鍵にしています（[`bajutsu/serve/project_registry.py`](../../bajutsu/serve/project_registry.py)）。

`orgs:` ブロックが所有を決めるのは、operator がサーバを**起動したときの** config に対してだけです。API 経由で bind した config は、ある org **として** bind されます。API 経由の config とは、アップロードした bundle、compose した組、Git ソースを指します。`Upload.org` がすでにどの org かを記録しており、bundle 自体もその org 専用のキャッシュ prefix の下に置かれます。したがって、そこで宣言されている target はすべてその org のものであり、ほかのどの org のものでもありません。その config の `orgs:` ブロックは読みません。

このブロックに権限を残すことは、この項目がメンバーシップについてすでに拒否した信頼の問題を、一段上で繰り返すことになります。デプロイが管理していないファイルが、テナントの手の届く範囲を決めてしまうからです。実際にも黙って失敗していました。ある org がアップロードした bundle の `orgs:` が、唯一の target を別の org のものだと主張していたとします。`targets_for_org` はアップロードした人に「誰も主張していない target」だけを返すので、一覧は空になり、信号は何も出ませんでした。

この問いには `ServeState.targets_for` が 1 つで答えます。3 つの読み手（target の一覧、org 跨ぎのガード、org ごとの scenario ストア）がすべてそれを通るので、4 つ目が別の答えを返すことはありません。

同じ `orgs.get(org) is None` の分岐は、ユニット 5 の API で作成した org が何を所有するかも決めます。その答えは、何も所有しない、です。そうした org は `orgs:` エントリを持たないため、誰かがエントリを追加するまで `targets_for_org` は空の一覧を返します。

この項目は、これを塞がずに受け入れ、そのことを明記します。admin が Web UI からテナントを迎え入れても、なお config の編集を待つ箇所がここだけ残るからです。org は作成された時点で存在し、メンバーを受け入れ、管理もできます。しかし `orgs:` エントリが target を名指しするまで、どの target にも認可されません。これを塞ぐ道は 2 つあります。

- admin が設定できる target を org に与える。ユニット 1 が却下した、データベースへ移す案です。
- org ごとに config をバインドする。*検討した代替案* に記録した先送りです。

どちらもこの項目より大きな変更であり、admin が作成した org が実在するようになってから判断するほうが適切です。

### 5. admin 用 API と UI：作成・削除・メンバーシップの編集

4 つのエンドポイントを設けます。すべて admin 限定です。org のメンバーシップは、他に誰がサインインでき、誰が書き込めるかを決めます。project の登録や config の再バインドに BE-0225 がすでに与えている感度と、同じ水準です。いずれの変更も、既存の `Repository.record_audit` を通じて記録します。記録するアクションは `org.create` / `org.delete` / `org.membership.update` で、対象は org の slug です。

- `GET /api/orgs` — 生存しているすべての org を一覧します。slug、name、`members` / `githubOrgs` / `editorTeam` そのもの、project の件数を返します。件数ではなく名簿そのものを返すのは、下のメンバーシップフォームが 3 つのフィールドを一括で置き換えるため、現在の値から始める必要があるからです。このエンドポイントは admin 専用であり、admin はすでに `GET /api/config/content` から `orgs:` ブロック全体を読めます。
- `POST /api/orgs` — `{slug, name}` から org を作成します。メンバーシップは空から始まります（member・GitHub Organization・editor Team のいずれもなし）。admin が追加するまで、新設した org は誰も受け入れません。
  - slug `default` は 409 で拒否します。この文字列は、すでに 3 箇所でテナントではなくフォールバックとして読まれているからです。`org_for_identity` は、どの org にも属さない login（admin Team の迂回で入るすべてを含みます）に対してこれを返します。`targets_for_org` は、エントリを読むより先にリテラルの文字列で判定します。後述の `delete_org` は、この slug を常に拒否します。ここに実在のテナントを作ると、admin が復旧に使う名前空間を奪い、しかもこの API では元に戻せません。そのため、取り消せる側、すなわち作成時に拒否します。
  - 作成した行の `id` は、その slug とします。既存の書き込み側がすでにそうしているためです。`ensure_org(org, slug=org, name=org)` は、3 つのカラムすべてに 1 つの文字列を入れます。その同じ文字列を、`upsert_user(org_id=…)`・`state.org_of()`・org 単位のストアが持ち回ります。したがって `orgs_from_db` は、`parse_orgs` が `orgs:` エントリ名を鍵にするのと同じように、その文字列を鍵にできます。slug と異なる id を生成すれば、これらすべての経路で slug から id を引く処理が要ります。この項目は、その処理を導入しません。
  - 作成した行には、ユニット 6 が一本化の際に立てるのと同じ引き込み済みマーカーを、作成時点で立てます。この行はすでに一本化を終えたものとして扱われます。以後どの `orgs:` エントリもこの行を引き込むことはなく、admin がこの API で設定したメンバーシップを上書きすることもありません。
- `POST /api/orgs/<slug>/membership` — org の `{members, githubOrgs, editorTeam}` を一括で置き換えます。`default` に対しては、作成と同じ理由で拒否します。login をある org に置くのはメンバーシップなので、フォールバックの slug に名簿を与えることは、それを作るのと同じだけ確実にテナント化します。迂回でのサインインが `ensure_org` で生きた `default` 行を残すため、ページはこれを一覧し、放っておけば到達できてしまいます。予約は 2 つではなく 3 つの動詞すべてで効く必要があります。エントリ単位の追加・削除エンドポイントに分けず、設定ファイルの編集がすでに持つのと同じ粒度にします。
  - 値全体を書き換える操作は通常 `PUT` で表しますが、ここでは `POST` を使います。`PUT` は、どちらのトランスポートも実装していない唯一の本文つきメソッドだからです。標準ライブラリのハンドラは `GET` / `POST` / `DELETE` だけを提供します。FastAPI 側の生成器も、本文を解析するのは `POST` に限られます。この 1 本のルートのために両方のトランスポートを広げるのは、この項目が他に必要としない横断的な変更です。
  - `serve` の値全体を書き換える他の操作も、すでにすべて `POST` です（`/api/projects/<name>/activate`、`/api/provider`）。
- `DELETE /api/orgs/<slug>` — org を削除します。その org がまだ project を 1 つでも所有している間は 409 で拒否します（`list_projects` が空でない）。admin は、先に org の project を登録解除する必要があります（BE-0225 の登録解除は run 履歴を残します）。

削除エンドポイントには、次の規則があります。

- **`default` org を拒否する。** `default` は serve がコードにハードコードしたフォールバックの slug（`DEFAULT_ORG`、[`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)）です。テーブルの状態に関わらず、未マッチの bypass サインインが解決先とし続けます。削除してしまうと、bypass ユーザーが着地し続けるソフトデリート済みの org が残るだけになります。
- **`default` 以外の空の org はソフトデリートする。** project を持たなくなった org では、`Org` 行を物理的に削除しません。`deleted_at` カラムを立てて削除済みと記録します（run の trash 済み状態がすでに使っている、同じソフトデリートの形です）。物理削除であれば、project をすべて登録解除した後も `users`・`runs`・`secrets`・`provider_settings`・`audit_log` がその org の id への外部キーを保持したままになり、整合性制約に反します（[`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py)）。削除の操作自身が記録する audit log のエントリも、そのときには指す先を失います。行を残すソフトデリートであれば、これらの外部キーはすべて有効なまま保たれます。
- **セッションを失効させる。** 廃止と同時に、その org の下に記録されている login に紐づくセッションもすべて失効させます。`SessionStore` の seam（[`bajutsu/serve/sessions.py`](../../bajutsu/serve/sessions.py)）に `revoke_identities` を追加し、各実装がこれを備えます。ソフトデリートだけでは*次の*サインインにしか届きません。`users.org_id` は廃止した slug を指したままなので、それ以前に発行された cookie は、期限が切れるまでそのテナントの run を一覧し、新しい run を起こし、secret を読み続けます。
- **失効の隙間は、新しく到達可能になった。** テナントの廃止は、これまで設定ファイルの編集と再デプロイを意味しました。その再起動が、副作用としてすべてのセッションを落としていました。プロセス内の admin 操作にすることで、その付随的な失効が失われます。この項目は、失効を意図的に取り戻します。identity を持たないセッション（共有トークンでのログイン）はどの org にも属さないため、対象になりません。
- **削除済みの org は解決の対象から外れる。** 削除後は、サインインの解決処理と `GET /api/orgs` が、その org を対象から外します。以後、その org としてマッチすることも一覧に出ることもありません。その org の下に残る user・secret・provider の設定は、行として残り参照はできますが、その org としての新規サインインはもう認めません。
- **履歴は残す。** その org に紐づく run 履歴と audit log のエントリは、削除された org の id のもとに残ります。project の登録解除後も run 履歴が残るのと同じ考え方です（[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)）。admin の操作が取り去るのは、テナントがサインインして行動する能力です。そのテナントが行ったことの記録は、取り去りません。

ソフトデリート済みの org と同じ slug で org を作り直すことは、この項目の対象外です。`Org.slug` には end-to-end で UNIQUE 制約があります（[`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py)）。ソフトデリート済みの行が、すでにその slug を占有しているからです。そのため `POST /api/orgs` は、その slug を専用の 409 エラーで拒否します。行を復活させることも、再利用することもしません。再有効化が必要になる場合は、将来の項目に委ねる、別の明示的な操作とします。

ユニット 6 の引き込みも、`deleted_at` が立っている行は対象にしません。ソフトデリート済みの org は未引き込みではなく廃止済みであるため、引き込まず、復活もさせません。

セッションがどの org として振る舞っているかは、このページの外でも示します。シェルのヘッダの、bind している設定ファイル名の隣です。すべてのタブがすでに起点にしている boot 時の読み取り `GET /api/config` に、呼び出し元自身の `actor` と、そこから解決される `org` を足します。ヘッダは org を表示し、カーソルを重ねると login も出します。

実行できる target、見える run と証跡、secret、プロジェクトの一覧まで、すべてのタブがその org に暗黙にスコープされます。複数のテナントを管理する admin は、そうでなければ自分の作業がどこに落ちるかを推測するしかありません。呼び出し元自身の identity を当の本人に返しても、本人が提示したもの以上を開示しません。名簿そのものは、admin 限定の `GET /api/orgs` の後ろに置いたままです。サインインした identity が無いデプロイ（ローカルや共有トークンの `serve` で、`org_of` が全員に `default` を返す状況）では、どちらのフィールドも null になり、ヘッダもバッジを出しません。

serve のシェルには、admin 限定の新しい **Orgs** ページを追加します。[BE-0275](../BE-0275-serve-projects-management-page/BE-0275-serve-projects-management-page-ja.md) の Projects ページと並ぶ位置づけです。ページの構成は次のとおりです。

- org の一覧に、作成と削除の操作を添えます。org がまだ project を持つ間は削除を無効にし、その件数を表示します。
- org ごとのメンバーシップ編集フォーム（member・GitHub Organization・editor Team）を置き、上記の `POST` エンドポイントを呼びます。

各行には、作成フォームが受け取った表示名を、slug と異なる場合にかぎり表示します。`name` を後から変更できるエンドポイントはないので、ページがどこにも表示しない表示名は、打ち間違いを永続させ、しかも見えなくしてしまうからです。この API とページはいずれも、ユニット 2 のゲートに従い、repository を配線している場合にのみ存在します。

### 6. 起動時に一度きりの移行と、その後の一本化

設定ファイルだけで org のメンバーシップを持っていたデプロイは、この項目の admin UI が表示・編集する対象を持つより先に、既存の `orgs:` ブロックをデータベース上に持つ必要があります。`serve` はこれを一度だけ、起動時に行います（`serve()` の中、`_configure_oplog` の後に置くのは、後述の警告が有効なログの出力先に届くようにするためです）。

読む対象は、このサーバを**起動したときの**設定ファイルです。実行するのは `orgs` テーブルに行が 1 つも無いときに限ります。廃止済みの行も含めて 1 つも無いときです。そのブロックのうちメンバーシップを宣言しているエントリごとに、専用の引き込みメソッドが行を作るか埋めます。メソッドは `members`/`github_orgs`/`editor_team` を書き、行ごとの永続マーカーを立てます。

このメソッドに `ensure_org` は使いません。理由は 3 つあります。

- `oauth_callback` とジョブ完了は、すでに `ensure_org(org, slug=org, name=org)` の形で、サインインのたび、実行の完了のたびに呼んでいます。
- その呼び出しは、渡せるメンバーシップを持ちません。ユニット 2 でデータベースが出どころになるデプロイでは、渡せる値がそもそも存在しません。
- `ensure_org` を create-or-update に広げると、2 つの問題のどちらかが起きます。省略した引数によって、1 つのメソッドが 2 つの意味を持ちます。あるいは、admin がユニット 5 のメンバーシップエンドポイントで設定した内容を、次のサインインが消してしまいます。

行ごとのマーカーは、従来と同じ理由で残します。各行が移行済みかどうかを直接記録するので、admin が意図的にメンバーシップを空にした org を「まだ一度も書かれていない行」と取り違えません。古い設定値で上書きし直すこともありません。

**API 経由の bind は引き込みを行わず、2 回目以降の起動も行いません。** `bind_config`、`bind_git_config`、アップロードと compose の bind、プロジェクトの切り替えは、いずれも内容がこのデプロイの管理下にない設定ファイルを受け取ります。同じファイルの `build:` を `--allow-remote-build` まで統制の外に置く [BE-0121](../BE-0121-serve-csrf-host-allowlist/BE-0121-serve-csrf-host-allowlist-ja.md) が、まさにそう述べています。そこから引き込めば、そのファイルに「誰がサインインできるか」を決める権限を渡すことになります。

この項目より前も、その権限自体はありました。ただし、権限は生きていて取り消せました。`oauth_callback` がサインインのたびにファイルを読み直していたため、正しい設定に bind し直せば、次のログインで許可が取り消されたからです。引き込んだ行は bind より長く残るので、同じ bind し直しでは何も取り消せなくなります。admin が `github:someone/repo@main` を bind し、その `orgs:` が `partners: {githubOrgs: [attacker-gh]}` を宣言していれば、`attacker-gh` の全員に恒久的なサインインを与えてしまいます。

テーブルが空のときだけという条件は、同じ穴を反対側から塞ぎます。設定を書き換えて再起動しても、次の 3 つは admin の与り知らないところではできません。

- テナントを増やすこと
- 廃止したテナントを復活させること
- 既存のテナントを作り変えること

1 回の起動でデプロイが移行し、以後はデータベースが自分の名簿の唯一の書き手になります。その後に増やす org は Orgs ページで作ります。

代償は隠さず名指しします。API 経由で bind した設定にだけ書かれている org は、admin が作るまで誰も受け入れません。これは意図した取引です。代わりに得られるのは、デプロイが管理していないファイルに、誰がサインインするかを決めさせないことです。Orgs ページがあるおかげで、復旧は再デプロイではなく 1 操作で済みます。

`members`/`github_orgs`/`editor_team` の各列（およびマーカー列とソフトデリート列）を追加する Alembic のマイグレーションは、列の追加だけを行い、データは書き込みません。引き込みは `serve` 自身が起動時に行う仕事です。Alembic のマイグレーション環境（[`bajutsu/serve/server/migrations/env.py`](../../bajutsu/serve/server/migrations/env.py)）が解決するのは `BAJUTSU_DATABASE_URL` だけです。bind された設定ファイルの `orgs:` ブロックには一切アクセスできません。

移行後、`oauth_callback` はメンバーシップをデータベースからのみ読みます。以後、`orgs:` ブロックを編集してもこの読み取りには影響しません。これは、[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) と [BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass-ja.md) が、ロールを決める同じデータに独立した出どころを 2 つ持たせるより一本化を選んだのと同じ判断です（*検討した代替案* を参照してください）。

`serve` は、次の 3 つの条件がすべて成り立つとき、起動ごとに一度警告します。

- repository が配線されている。
- 設定ファイルが bind されている。
- その `orgs:` ブロックに、メンバーシップのフィールド（`members`/`githubOrgs`/`editorTeam`）をまだ宣言しているエントリがある。

`targets` だけを持つ `orgs:` エントリは、移行後も正当であり、残ることが期待されます。target の所有は設定ファイルに残るからです（ユニット 1）。単に空でない `orgs:` ブロックに対して警告すると、2 つのうちどちらかの問題が起きます。適切に構成されたデプロイで鳴り続けるか、operator に `orgs:` を空にさせて target の所有情報を失わせるかです。

`targets:` だけのエントリは、引き込み自体の対象からも外し、空の名簿として書き込むことはしません。この項目のドキュメントは、移行後に設定ファイルを削ぎ落とすことを勧めています。その操作を移行の起動より前に行っても、すべての org を「誰も受け入れない」状態に固定する不可逆な行為になってはならないからです。

この警告は、[BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass-ja.md) が自身の退役した環境変数について与えているのと同じ、「operator が、これはもう読まれないことを忘れている」という信号です。

新規のデプロイは、最初の起動から repository が配線されていて、設定ファイルだけの過去を持ちません。このデプロイは、その最初の起動が bind した `orgs:` ブロック（通常は空）に対して移行します。以後に持つ org は、すべて admin がユニット 5 の API で作ったものです。

### 7. admin Team のブートストラップ迂回が、テーブルが空の場合の答えになる

新設されたばかりのデータベース連動デプロイには、admin が作成するまで org が 1 つもありません。これは、ブートストラップの問いを引き起こします。最初の 1 つを作成するには、誰がサインインすればよいのでしょうか。同じ問いには、`orgs:` ブロックが壊れている場合や存在しない場合について、[BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass-ja.md) がすでに答えています。

`BAJUTSU_OAUTH_ADMIN_TEAMS` は、この項目のデータベースへの移行から意図的に外したまま、環境変数のままにします。org モデル自身が、どこに置かれていようと、まだ誰も受け入れられない状態を回復するための経路だからです。設定した admin Team のメンバーは、`Org` 行がいくつ存在するかに関係なく、この迂回を通じてサインインゲートを通過します。そして admin ロールでサインインします。この点は、この項目による変更がありません（[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) の「admin はサーバー全体で 1 つの階層のまま」）。そのうえで、ユニット 5 の API を使って、このデプロイの最初の org を作成します。

鶏と卵の問題は起きません。この項目が意図的にデータベースの外に残したテナンシーのデータこそが、`orgs` テーブルが空の状態を回復可能にしているからです。

### 8. GitHub Team それ自体をメンバーシップの軸にする（追加修正）

この項目の実装後に追加しました。ユニット 1、5、6 がデータベースへ移すメンバーシップの 4 フィールドを変える変更であり、別に扱えば 2 つの生成元でフィールドの集合が食い違うからです。

org に `githubTeams` を加えます。値は `"<github-org>/<team-slug>"` の形で書くフラットな GitHub Team のリストで、その直接メンバーは **viewer** としてこの org に所属します。

これまで org が login を受け入れる手段は 2 つでした。その中間はありません。

- `members` に login を挙げる。
- `githubOrgs` に GitHub organization 全体を挙げる。

そのため、共有 organization のうち 1 つのチームだけを受け入れる手段は、2 つに限られていました。参加や離脱で古くなる名簿を手で保守するか、その organization の他のチーム全員にデプロイを開くかです。デプロイが欲しい単位は、GitHub 自身がすでに Team としてモデル化しています。しかも `serve` は、editor と admin のロールのために、サインインのたびに login の Team を読んでいます。つまりメンバーシップは手元にあり、ゲートだけがそれを見ないでいました。

`editorTeam` は、昇格させるだけでなく受け入れるようになります。このフィールドが指すのは、run、record、scenario の編集を許す Team です。つまり org 単位で与えられるもっとも強い権限ですが、誰のサインインも許していませんでした。ここだけに書かれた Team は「書き込めるがログインできない」に解決され、メンバー全員が、ロールの計算より前に走るゲートで拒否されていました。

和集合は `OrgConfig.admitting_teams()` が返します。ゲートと「この org はメンバーシップを宣言しているか」の判定（ユニット 6 の移行警告も含む）は、どちらもこの 1 つのアクセサを読みます。そのため、`editorTeam` だけで誰かを受け入れるかどうかで食い違いません。

ゲートだけに軸を足せば、2 つの不具合が起きます。

- ある org の Team で受け入れた login を、別の org に置いてしまいます。その login には、別のテナントの targets とオブジェクトストレージの prefix（ユニット 4）が渡ります。
- ロールの計算で違う org の `editorTeam` を読むので、editor Team で受け入れられた login が viewer になります。

そこで `identity_matches_org` と `org_for_identity` を、1 つの private な `_match_org` に委譲します。`_match_org` は `members`、`githubOrgs`、org の受け入れ Team の順に見て、一致した org か None を返します。Team を最後に置くので、Team を宣言しても、既存のエントリがすでに置いた login の所属先は動きません。

ゲート、editor のロール、admin Team の 3 つが使う Team の比較は、`orgs.in_teams` の 1 つにまとめます。両側は、GitHub 自身が org login と Team slug を解決するのと同じように小文字化します。BE-0313 は、綴りの大小が食い違って失うのが editor のロールだけだったあいだ、`editorTeam` の判定を完全一致に留めていました。このフィールドが受け入れるようになると、同じ食い違いはサインインを失わせます。

使うのは `str.casefold` ではなく `str.lower` です。完全な case folding は、GitHub が別物として扱う名前を同一視します（`gruß` と `gruss` は 1 つの organization の 2 つの Team になれます）。そして一致はサインインを買うので、folding で等しくなる Team を作れる人はゲートを通れてしまいます。小文字化しても、ネストした Team を一致させない保証は保たれます。この保証は、`"<github-org>/<team-slug>"` 全体の完全一致に依るからです。

Team の軸は、どちらの resolver でもデフォルト値のない必須の引数です。省略できる呼び出し口があれば、ゲートと配置がまた別の軸を見る道が開きます。しかも型検査は通ったまま、Team で受け入れた login を拒否したり置き間違えたりします。

`org_for_user` はここで廃止します。理由は、ユニット 1 が `org_for_target` を廃止したのと同じです。`_match_org` が `members` を自分で走査する以上、この helper に残るのは BE-0313 以前の意味だけで、呼び出し元もいなくなります。

データベース側は、ユニット 1 と 5 をそのまま踏襲します。nullable な `orgs.github_teams` の JSON 列を加えます（マイグレーション `0016`。`0015` と同じく列の追加だけで、引き込みは行いません）。この列は `OrgRecord`、`set_org_membership`、`seed_org_membership`、`orgs_from_db` が運びます。置き換えは、`POST /api/orgs/<slug>/membership` と Orgs ページのフォームで、他の 3 つと 1 つの単位で行います。`editor_team` は自分の列を持ち続けます。受け入れるだけでなくロールも決めるからです。

ユニット 3 の拒否理由は、Team のリストが**同時に**空のときにだけ、そのリストも名指しします（"GitHub returned no orgs or teams for this login"）。org のリストだけを挙げれば、Team を宣言した org が参照しない軸を operator に調べさせてしまいます。

`/user/teams` は fail closed なので、GitHub の Teams API がエラーを返している間は、所属が Team だけの login は拒否されます。ゲートが倒れるべき向きはこちらです。ユニット 7 の迂回がすでに受け入れた引き換えと同じです。

### 9. 書き込みを許す Team をリストにする（追加修正）

`editorTeam` を `editorTeams` に変え、フラットな GitHub Team のリストにします。ユニット 8 は org に受け入れ Team のリストを与えましたが、書き込みを許す Team は 1 つの枠のまま残しました。他の 2 つのメンバーシップの軸は、すでにその形から抜け出しています。`githubOrgs` と `githubTeams` がリストなのは、1 つの org が複数の GitHub organization にまたがることがあるからです。そうした形の org は、organization ごとの書き込み Team を挙げられません。

逃げ道は 2 つに限られていました。

- GitHub 側で Team を統合する。
- 名簿を 1 つ手で保守する。これは [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) が取り除いた手作業そのものです。

両方の Team を `githubTeams` に挙げるのは、3 つ目の逃げ道になりません。このフィールドが受け入れるのは viewer としてであり、書き込みは誰にも与えないからです。

サーバ全体の admin Team についても、同じ結論に達した先例があります。[BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass-ja.md) が `BAJUTSU_OAUTH_ADMIN_TEAM` を `BAJUTSU_OAUTH_ADMIN_TEAMS` へ広げました。1 つのデプロイが GitHub organization ごとの admin Team を挙げられるようにするためです。

`OrgConfig` は、旧来の単数形 `editorTeam` のキーを拒否するのではなく `editor_teams` に畳み込みます。この名前の変更が BE-0352 の名前の変更と分かれるのは、ここです。BE-0352 は環境変数をそのまま廃止できました。読まれない環境変数が残っていても、config は読み込めるからです。`OrgConfig` は `extra="forbid"` なので、変更し忘れたキーは `parse_orgs` で例外になります。そして `load_serve_config_file` は、パースの失敗に対して org モデルを何も返しません。その結果、すべての login が "user not allowed" で拒否されます。キーを 1 つ変え忘れたデプロイは、自分を静かに締め出すことになります。BE-0352 が廃止した名前の警告で防いでいる失敗と同じです。

畳み込みには、2 つの端の規則があります。

- 両方の綴りを書いた config は、両方の Team を保ちます。複数形の名前を足して単数形を残す中途半端な名前の変更のほうが起こりやすく、どちらの綴りも、与えるために書かれたロールを失うべきではないからです。
- `editorTeam: ""` は何にも畳み込みません。空の値は、名前の変更の前から「書き込みを許す Team はない」を意味していました。どの Team にも一致しないエントリを作れば、`orgs_declaring_membership` が名簿があると報告してしまいます。

リストにしたことで、1 つの枠を特別扱いしていた 2 箇所が単純になります。`admitting_teams()` は `github_teams` と `editor_teams` を素につないだだけになります。`role_for` は `is not None` の番を落とします。空のリストに対する `in_teams` は、すでに False だからです。

`POST /api/orgs/<slug>/membership` は、「文字列であること」の専用の判定をやめ、他の 3 フィールドが使う共通の `_string_list` に渡します。これでこのエンドポイントが検証するのは、同じ形のリスト 4 つになります。

このエンドポイントは、旧来の `editorTeam` フィールドを畳み込まず 400 で拒否します。config の側とは逆の選択で、根拠は拒否の代償です。`_string_list` は `editorTeams` が無いのを空のリストと読むので、そうした body に従えばその org の書き込み権限が消え、しかも 200 が返ります。拒否の代償は、operator が直せる 1 リクエストです。config のキーを拒否すれば、代償はそのデプロイのすべての login になります。

マイグレーション `0017` は、この一連のなかで列の追加だけでなくデータを運ぶ最初のものです。`0015` と `0016` が何も引き込まなかったのは、加えた列がどれも `orgs:` ブロックから作り直せるからでした。`editor_team` は作り直せません。`POST /api/orgs/<slug>/membership` の書き込みは `membership_seeded_at` を刻み、それ以後 `seed_org_membership` は永久に何もしません。そのため、その値はその行だけにあります。書き写さずに列を落とせば、そうした org の editor 全員が viewer に降格し、しかも何も告げられません。

ダウングレードが書き戻すのは、先頭のエントリだけです。リストへの拡張が失うのはこの向きだけであり、2 つの Team を持つ行のダウングレードを拒否すれば、このユニットが到達可能にした状態から operator が戻る道がなくなります。

## 検討した代替案

- **org のメンバーシップを設定ファイルに残し、外部で編集した後 `POST /api/config` の再バインドを admin に引かせる。** 却下します。これは今日の仕組みそのもので、まさにこの項目が取り除きたい依存関係です。テナントを 1 つ迎え入れるたび、あるチームを別の GitHub Team へ移すたびに、デプロイの設定リポジトリへの書き込み権限を持つ人と、再デプロイまたは再バインドが要ります。
- **`ProjectRegistry` に倣ったレジストリのシームを設ける。** [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md) と同じく、データベース連動とファイル連動の両方の経路を持たせる案です。データベースを持たない serve でも、admin が org のメンバーシップを管理できるようになります。却下します。データベースを持たない serve は、そもそもローカルで単一ユーザー向けに作られており、admin が管理すべきテナントの境界も、保護すべきロールの区別もありません（ユニット 2）。そのデプロイ形態が使い道を持たない機能のために、もう 1 つの永続化経路を作るのは、余分な表面積を増やすだけです。`ProjectRegistry` とは対照的です。単一ユーザーのローカルなハブには、複数の config を扱うという本物の必要があります（複数のテナントを扱う必要ではありません）。
- **`targets`（org の target 所有）も、`members`・`github_orgs`・`editor_team` と一緒にデータベースへ移す。** この項目では却下します。`targets` は、target がどのアプリケーションを指し、どう動くかを名指すアプリケーション固有のデータです。プライム・ディレクティブ 3 が設定ファイルに残すものであり、admin が編集できる運用ストアに置くものではありません。データベース連動のデプロイには、アプリケーションを org に紐づけるための、形が異なりより活発に保守されている手段がすでにあります。[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md) の project レジストリです。
- **target の所有は名前単位の解決のままにし、他の org の target 名と衝突する org の作成を `POST /api/orgs` で拒否する。** 却下します。この衝突は、admin が避けるべきものではありません。org が主張する target 名は設定ファイルの中にあり、org を作成する admin はそれを見られません。そもそもメンバーシップをそのファイルの外へ移したデプロイでは、admin がそれを読む理由もありません。検証で衝突を弾けば、その org 自体とは無関係な理由で org の作成が失敗することにもなります。ユニット 4 であれば、2 つの org はそのまま共存します。
- **同一性を `(org, target)` にするだけでなく、org ごとに config をバインドして org ごとの `targets:` 定義を持たせる。** 却下ではなく、先送りします。これは自然な最終形であり、[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md) の project ごとの config バインディングがすでにその方向を指しています。ただし、serve が同時に複数の config をバインドできる必要があります（今日の `state.config` は単一のパスです）。これは org モデルではなく config のバインディングに対する変更です。したがって、それを行う項目に属し、この項目には属しません。
- **org 自体を削除するとき、その project・run・audit log のエントリをカスケード削除する。** 却下します。project を登録解除しても run 履歴を残す BE-0225 自身の判断を、反映する案だからです。audit log は、まさに admin の操作それ自体が記録を消す理由にならないように存在します。[BE-0170](../BE-0170-weighted-fair-org-dispatch/BE-0170-weighted-fair-org-dispatch-ja.md) の今後の org 単位の公平な dispatch も、そのデータを生み出した org が存在するかどうかに関係なく、過去のデータを問い合わせ続けられることを必要とします。
- **一度きりの引き込みとその後の一本化の代わりに、サインインのたびに設定ファイルとデータベースの両方を参照する（リクエストごとのフォールバック）にする。** 却下します。BE-0313 と BE-0352 は、どちらもすでに、同じサインインを左右するデータに対して 2 つの独立した出どころを持たせることを却下しています。BE-0313 自身の動機が述べる、名簿がずれていくという同じ理由です。この項目は、org のメンバーシップについてその判断を蒸し返さず、同じ前例に従います。

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
- [x] 4 — target の所有を、名前単位ではなく org 単位で解決する。API 経由で bind した config の
      `orgs:` ブロックには何も決めさせない。その bind はある org として行われ、その org がファイル
      の宣言するすべての target を所有するからで、この問いには `ServeState.targets_for` が1つで
      答える。`_target_forbidden` は、その org
      自身の `targets_for_org` の一覧にその target が含まれるかを問う。`org_for_target` は呼び出し元
      を失う。問いは `targets_for_org` を経由させ、`default` がリテラルの slug によるフォールバック
      で得ている未主張の target を失わないようにする。これにより、2つの org がそれぞれ同じ名前の
      target を主張できるようにする。config の記述順が一方に与え、他方には見えているだけの target を
      禁じる、という今日の挙動を置き換える。
- [x] 5 — 4つの `/api/orgs…` エンドポイントと、Orgs 用の admin ページを、repository を配線して
      いる場合にのみ用意する。エンドポイントは admin 限定とし、いずれも `record_audit` で記録する。
      ページは作成・空のときのみ削除・メンバーシップ編集を扱い、各行では slug と異なる表示名を
      表示する。`POST /api/orgs` は作成時点で新しい行に引き込み済みマーカーを
      立て、以後どの `orgs:` エントリもその行を引き込まないようにする。削除はソフトデリート
      （`Org.deleted_at`）とする。行を削除せず、外部キーを壊すこともなく、その org をサインインの
      解決処理と `GET /api/orgs` の対象から外す。さらに `POST /api/orgs` は予約語の `default` slug を
      拒否し、削除は `SessionStore` の seam に追加した `revoke_identities` で、そのメンバーが持つ
      セッションもすべて失効させる。`GET /api/config` は呼び出し元自身の `actor` と `org` を返し、
      シェルのヘッダは bind している設定ファイル名の隣にバッジとして表示する。
- [x] 6 — 一度きりの移行を、起動したときの設定ファイルの `orgs:` ブロックから
      データベースへ行う。実行は起動時だけで、しかも `orgs` テーブルに行が1つも無い（廃止済みの行も
      含めて無い）ときに限る。これにより、API 経由で bind した設定ファイルも、編集した設定ファイルを
      積んだ再起動も、誰がサインインできるかを書けない。加えて `Org` の行ごとの永続マーカーで守り、
      メンバーシップ列が空かどうかから推測しない。`targets:` だけのエントリは、空の名簿として書き込む
      のではなく引き込みの対象から外す。Alembic のマイグレーションはメンバーシップ列、マーカー列、
      ソフトデリート列の追加だけを行い、自身では引き込みを行わない。`orgs:` エントリが
      `members`/`githubOrgs`/`githubTeams`/`editorTeam` をまだ宣言しているときの起動時の警告も含む（`targets` だけを
      持つエントリは期待される姿であり、警告しない）。
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
- [x] 9 — 追加修正：org の3つ目のメンバーシップの軸として `githubTeams` を加え、`editorTeam` にも
      昇格だけでなく受け入れをさせる。3つの軸の順位付けは `orgs._match_org` に一度だけ書く。これで
      サインインのゲートと org の配置は、ある org で受け入れた login を別の org へ置けなくなる。
      Team の軸はどちらの resolver でも必須の引数とし、`org_for_user` は廃止する。ゲート、editor の
      ロール、admin Team が共有する Team の比較（`orgs.in_teams`）は小文字化して比べる。このフィールドを、マイグレーション `0016`、リポジトリの seam、
      `orgs_declaring_membership`、メンバーシップの API、Orgs ページに通す。GitHub がどちらの軸も
      返さなかったとき、ユニット3の拒否理由で Team のリストも名指しする。テスト：Team だけの login が
      受け入れられ、**その Team の org に置かれる**こと。`editorTeam` だけの login が editor として
      受け入れられること。綴りの大小が食い違う Team。ネストした Team が拒否されること。
      `/user/teams` が何も返さないあいだ、Team だけの login が拒否されること。ユニット8の
      config とデータベースの解決一致を Team の軸にも広げること。

- [x] 10 — 追加修正：`editorTeam` を `editorTeams` に広げてリストにし、複数の GitHub organization に
      またがる org が organization ごとの書き込み Team を挙げられるようにする。旧来の単数形
      `editorTeam` のキーは拒否せずリストに畳み込む。`OrgConfig` は `extra="forbid"` であり、パースの
      失敗は `serve` に org モデルを残さず、すべての login を拒否するからである。このフィールドを、
      マイグレーション `0017`（API で設定した `editor_team` は作り直せないため、この一連で初めて
      データを書き写す）、リポジトリの seam、`role_for`、メンバーシップの API の共通の `_string_list`
      による検証、Orgs ページに通す。テスト：`editorTeams` の2つ目のエントリが受け入れと昇格の両方を
      すること。旧来のキーが畳み込まれること（単独のとき、複数形の隣にあるとき、空の値のとき）。
      マイグレーション `0017` が、名前の変更前の行の Team 1つを1要素のリストに運ぶこと。Team を
      持たなかった行には触れないこと。

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
  は、ユニット1から7では出どころだけが変わり、ユニット8からはユニット8の `_match_org` を共有します。
  その隣の `in_teams` が Team の比較を1箇所で持ちます。`org_for_target` はここで、`org_for_user` は
  ユニット8で、唯一の呼び出し元を失います。
- [`bajutsu/serve/project_registry.py`](../../bajutsu/serve/project_registry.py) — project がすでに
  持つ `(org_id, name)` という鍵。この項目の target の同一性はこれに倣います。
- [`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py) — この項目がメンバー
  シップ用のカラムを追加する `Org` テーブル。`github_teams` も含みます（ユニット8の
  マイグレーション [`0016`](../../bajutsu/serve/server/migrations/versions/0016_org_github_teams.py)）。
- [`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py) — `_fetch_teams`。その
  fail closed の挙動は、ユニット8以降、ユニット7の admin 迂回だけでなく Team を宣言した org の
  サインインも決めます。
- [`bajutsu/serve/server/db.py`](../../bajutsu/serve/server/db.py) — `Repository.ensure_org`。
  この項目はこれを冪等な作成のまま残し、その隣に専用の引き込みメソッドを置きます。および、この
  項目の `OrgRecord` とその作成・削除・メンバーシップ更新の各操作が手本にする
  `ProjectRecord` / `create_project` / `delete_project` の形。
- [`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py) — `oauth_callback` のサインインゲート。
  この項目が変えるのはそのデータの出どころであり、位置づけではありません。あわせて、新しい出どころ
  に合わせて読み替える `_unmatched_org_cause` と、org 単位で解決し直す `_target_forbidden`。
- [`bajutsu/serve/operations/projects.py`](../../bajutsu/serve/operations/projects.py) — この
  項目の `/api/orgs…` エンドポイントが手本にするエンドポイントと、その権限判定の形。

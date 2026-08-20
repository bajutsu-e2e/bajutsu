[English](BE-XXXX-team-based-signin-gate.md) · **日本語**

# BE-XXXX — GitHub organization だけでなく GitHub Team でもサインインを許す

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-team-based-signin-gate-ja.md) |
| 提案者 | [@paihu](https://github.com/paihu) |
| 状態 | **実装中** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | — |
| トピック | Web UI のホスティング |
| 関連 | [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) · [BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass-ja.md) · [BE-0375](../BE-0375-serve-org-lifecycle-management/BE-0375-serve-org-lifecycle-management-ja.md) |
<!-- /BE-METADATA -->

## はじめに

この項目は、`serve` の org に3つ目のメンバーシップの軸として GitHub Team を加えます。既存の軸は、
login を1つずつ挙げる `members` と、GitHub organization 全体を挙げる `githubOrgs` の2つです。org は
新しい `githubTeams` フィールドで Team を挙げられるようになります。すでに持っている `editorTeam` も、
直接メンバーを editor に昇格させるだけでなくサインインを許すようになります。login は自分を受け入れた
org に置かれます。サインインのゲートと org の解決が、2つではなく1つの順位付けから答えるからです。

## 動機

[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) が org に与えた
メンバーシップの軸は2つです。login を1つずつ挙げる `members` と、GitHub organization 全体を挙げる
`githubOrgs` です。複数のチームで共有している GitHub organization には、この中間がありません。その
うち1つのチームだけを受け入れたい場合、選べる手段は2つです。そのチームのメンバー全員を手で
`members` に書き出すか、organization 全体を挙げるかです。前者の名簿は、誰か1人の参加や離脱で
すぐに古くなります。この保守の手間をなくすために `githubOrgs` があります。後者は、その
organization の他のチーム全員も受け入れてしまいます。デプロイが欲しい単位は、GitHub 自身が
すでに Team としてモデル化しています。`serve` はサインインのたびに login の Team を読んでいます
（editor と admin のロールのために `/user/teams` を読みます）。つまりメンバーシップは手元にあり、
ゲートだけがそれを見ないでいます。

`editorTeam` は、同じ隙間を operator が誤って書ける構成として鋭くします。このフィールドが指すのは、
run、record、scenario の編集を許す Team です。つまり org 単位で与えられるもっとも強い権限ですが、この
フィールドは誰のサインインも許しません。ここだけに書かれた Team は「書き込めるがログインできない」に
解決されます。メンバー全員が、ロールの計算より前に走るゲートで拒否されるからです。org は、書き込みを
許す唯一のフィールドで自分の editor が誰かを宣言していながら、その本人たちを中に入れていません。

ゲートの軸が2つのままだと、デプロイは粗い側の許可に寄ります。共有 organization のうち1つのチームだけ
を入れたい operator の選択肢は、`githubOrgs` と手で保守する `members` の名簿しかありません。そして
古くなるのは名簿の側なので、成長するチームとの接触に耐えて残るのは粗い側の許可です。

## 詳細設計

### `githubTeams`：org のメンバーシップの軸としての Team

`OrgConfig` に `github_teams` を加えます（`orgs:` ブロックと API では `githubTeams`）。値は
`"<github-org>/<team-slug>"` の形で書くフラットな GitHub Team のリストです。`editorTeam` や
`BAJUTSU_OAUTH_ADMIN_TEAMS` の各エントリが取るのと同じ形です。いずれかの Team の直接メンバーは、
その org に所属します。ゲートを通ること自体はロールを伴いません。受け入れられた login に付くのは、
サインインした全員が得る基本ロールの **viewer** です。残りは引き続き `editorTeam` と
`BAJUTSU_OAUTH_ADMIN_TEAMS` が決めます。したがって `githubTeams` が広げるのは、サインインできる
範囲だけです。

`editorTeam` と違い、このフィールドはリストです。Team はもともと粒度の細かい単位です。そのため、
Team を受け入れる org は複数の Team を受け入れることが多くなります（共有 organization の貢献チーム
ごとに1つ、といった形です）。一方、**書き込み**を許す Team を1つに絞るのは意図した設計です。

### `editorTeam` は昇格させるだけでなく受け入れる

org の `editorTeam` の直接メンバーは、その Team を `githubTeams` に重ねて書かなくても受け入れられ
ます。重複を要求すれば「書き込めるがログインできない」を書ける構成として残します。しかも、org が
すでに書き込み権限を約束した Team を operator に2度宣言させても、得るものはありません。和集合
（`github_teams` と、設定されていれば `editor_team`）は `OrgConfig.admitting_teams()` が返します。
ゲートと「この org はメンバーシップを宣言しているか」の判定は、どちらもこの1つのアクセサを読みます。
そのため、`editorTeam` だけで誰かを受け入れるかどうかで食い違うことがありません。

### ゲートと配置を1つの順位付けが決める

以前は、ゲートと org の解決が順位付けを別々に持っていました。`identity_matches_org` が「どこかの org
がこの login を受け入れるか」に答え、`org_for_identity` が「どの org か」に答えていました。片方だけに
軸を足せば、ある org の Team で受け入れた login を別の org に置いてしまいます。その login には別の
テナントの targets とオブジェクトストレージの prefix が渡ります。ロールの計算では違う org の
`editorTeam` を読むので、editor Team で受け入れられた login が viewer になります。そこで両者を、一致した org か
None を返す1つの private な `_match_org` に委譲します。順位は次のとおりです。

1. 明示の `members` エントリ
2. どこかの org の `githubOrgs` との共通部分
3. どこかの org の受け入れ Team の直接メンバーシップ

Team を最後に置くのは、Team を宣言しても、既存の `members` や `githubOrgs` がすでに置いた login の
所属先を動かさないようにするためです。軸の追加でサインインできる範囲が広がっても、すでにできていた
人の所属先は変わりません。`identity_matches_org` は `_match_org(...) is not None` になります。
`org_for_identity` は `_match_org(...)` の None を `default` へ読み替えたものになります。これで、
2つの答えが食い違う余地はなくなります。名前は両方とも残し、Team のリストはデフォルト値付きの引数と
して受けます。防ぎたい失敗（ある org で受け入れて別の org に解決する）を招くのは、名前の数ではなく
順位付けが2つあることだからです。

### Team の一致はどこでも大文字小文字を区別しない

`role_for` の `editorTeam` の判定は、意図して完全一致にしてありました。BE-0313 は大文字小文字の罠を
記録しつつ、緩めるのを対象外としました。当時、綴りの大小が食い違って失うのは editor のロールだけ
だったからです。`editorTeam` が受け入れるようになると、同じ食い違いはサインインを失わせます。また、
片方だけで一致すれば、login を受け入れてから viewer を渡すことになります。そこで、ゲート、editor の
ロール、admin Team の3つが使う比較を `orgs.in_teams` の1つにまとめます。両側は、GitHub 自身が org
login と Team slug を解決するのと同じように case-fold します。`authz.in_admin_team` は2つ目の実装を
持たず、`orgs.in_teams` に委譲します。case-fold してもネストした Team を一致させない保証は保たれます。
この保証は `"<github-org>/<team-slug>"` 全体の完全一致に依ります。`/user/teams` は子 Team を親とは
別の Team として返すので、構成済みの Team の下にネストした Team は依然として一致しません。

### データベースは同じモデルのもう1つの生成元

データベースを繋いだデプロイは、メンバーシップを `orgs` テーブルに持ちます（BE-0375）。そこで
`orgs` に nullable な `github_teams` の JSON 列を加えます（マイグレーション `0016`）。`0015` が
加えた列と同じく nullable で、seed もしません。既存の行は値なしでアップグレードされ、「この org は自分の Team を1つも
受け入れない」と読まれます。列が存在しなかった時点で、どの org もそう意味していました。`editor_team`
は自分の列を持ち続けます。受け入れるだけでなくロールも決めるので、2つを1つにまとめると、どの Team が
書き込めるのかが失われます。

この列は、既存の3つと同じ道筋で seam を通ります。対象は `OrgRecord`、`set_org_membership`、
`seed_org_membership`、`orgs_from_db` です。これにより、config を出どころとする org モデルと
データベースを出どころとする org モデルは、引き続き同じ解決結果を返します。
`orgs_declaring_membership` は Team だけを宣言したエントリも数えるので、移行時にその行も seed され
ます。名簿がすべて Team のデプロイで、「まだどの org もメンバーシップを宣言していない」の警告が
鳴り続けることはありません。

### 拒否の理由は GitHub が返さなかったものを名指しする

`_OrgModel.unmatched` は何も一致しなかった理由を名指しします。主たる合図であり続けるのは、org の
リストが空であることです。`/user/orgs` の障害はまさにそう見えるからです。これに加えて、Team の
リストが**同時に**空のときにだけ、Team のリストも名指しします（"GitHub returned no orgs or teams for
this login"）。これで、ゲートが Team であるデプロイを扱えるようになり、Team はあるが org がない login
に伝える内容は変わりません。org のリストだけを挙げれば、Team を宣言した org が参照しない軸を、
operator に調べさせてしまいます。

`/user/teams` は fail closed です。`_fetch_teams` は Team を勝手に作り出さないので、GitHub の Teams
API がエラーを返している間は、所属が Team だけの login は拒否されます。ゲートが倒れるべき向きは
こちらです。読めなかった Team のリストで受け入れる側に倒れるゲートは、障害が続くあいだ全員を
受け入れてしまいます。BE-0352 は admin Team の迂回について同じ引き換えを受け入れており、この項目は
それが適用される login の範囲を広げます。セルフホスティングの手引きは、Team の軸を説明する箇所で
この点を述べます。

### API と Orgs ページが4つ目のフィールドを運ぶ

`POST /api/orgs/<slug>/membership` は `{members, githubOrgs, githubTeams, editorTeam}` を1つの単位
として置き換えます。`githubTeams` の検証は `githubOrgs` と同じ `_string_list` で行い、audit の記録に
も残します。メンバーシップの変更の監査が答えるべきなのは「いつから誰がこのテナントとしてサインイン
できるか」だからです。`GET /api/orgs` はこのフィールドを返します。ページのフォームは一覧の値から prefill する
ので、表示しなかったフィールドは最初の保存で黙って空になります。Orgs ページには対応する入力欄が
増え、各行の要約に Team の数が出ます。

## 検討した代替案

**`githubTeams` に viewer ではなく editor を与える。** 2つのフィールドを1つにまとめられます。
しかし `editorTeam` は org 単位の書き込み許可であり、`githubTeams` は org 単位の閲覧許可です。あるチームに
**見せたい**だけのデプロイは、それを表現する手段を失います。viewer で受け入れれば2つの許可は独立に
保たれ、両方を与えたい org は同じ Team を両方のフィールドに書きます。

**`editorTeam` は昇格専用のままにし、`githubTeams` への重複を要求する。** 明示的で、ゲートが読む
フィールドも1つに保てます。しかし「書き込めるがログインできない」を operator が書ける構成として
残しますし、その失敗は書いた時点では現れません。あとで、拒否された login として現れます。この重複
が運ぶ情報は、org がすでに与えたものだけです。

**Team を `githubOrgs` より上に置く。** 粒度の細かい軸が同点を勝つべきだ、という主張は成り立ちます。
一方で、`githubOrgs` のエントリが今日置いている login のうち、Team のエントリが別の org に置くものは
すべて、次のサインインで所属先が動きます。軸を1つ足した副作用として、テナントが変わり、見える
targets とオブジェクトストレージの prefix も変わります。そこで Team は最後に置きます。複数の org に
属する login が、そのどれとして振る舞うかを選べるようにする話は、BE-0375 がすでに別項目として
挙げているとおり、別に扱います。

**`editorTeam` に倣って単数の `githubTeam` にする。** 対称ではありますが、Team は粒度の細かい単位
なので、Team を受け入れる org は複数を受け入れることが多くなります。単数にすれば、そうしたデプロイは
`githubOrgs` に押し戻されます。この項目が避けようとしているのは、その粗い許可です。

## 進捗

> 作業の進行に合わせて最新に保ちます。チェックリストは*詳細設計*の MECE な作業分解を反映し
> （作業単位ごとに1つ）、ログには何がいつ変わったかを古い順に記録し、PR をリンクします。

- [x] `OrgConfig` に `githubTeams` を加え、`admitting_teams()` で `editorTeam` との和集合を返す。
      3つのメンバーシップの軸の順位付けを `orgs._match_org` に一度だけ書き、`identity_matches_org` と
      `org_for_identity` の両方がそこへ委譲する。これにより、ある org の Team でゲートを通した login
      を、配置が別の org に置くことはなくなる。
- [x] Team の比較（`orgs.in_teams`）を大文字小文字の区別なしにし、ゲート、`role_for` の editor
      判定、`authz.in_admin_team` で共有する。これにより、綴りの大小が食い違う `editorTeam` で
      サインインとロールのどちらも失われない。case-fold してもネストした Team を一致させない保証は
      残る。
- [x] データベース側の生成元にこの軸を通す。マイグレーション `0016` が nullable な
      `orgs.github_teams` を加え、`OrgRecord`、`set_org_membership`、`seed_org_membership`、
      `orgs_from_db`、`orgs_declaring_membership` が運ぶ。これにより、config 由来のモデルと
      データベース由来のモデルは同じ解決結果を返し、Team だけの名簿も seed される。
- [x] GitHub がどちらの軸も返さなかったとき、`_OrgModel.unmatched` の拒否理由で Team のリストも
      名指しする。Team を宣言したデプロイの operator を、参照されない `orgs:` の軸へ向かわせない
      ための記述である。
- [x] 4つ目のフィールドを `GET /api/orgs` と `POST /api/orgs/<slug>/membership`（検証、audit、
      返却）に通す。Orgs ページのメンバーシップフォームと行の要約にも通す。
- [x] ドキュメント：`orgs:` のリファレンスと、セルフホスティングの RBAC／複数 org の節を両言語で
      更新し、`architecture.md` の BE-0375 の項目も更新する。

### ログ

- 提案と実装を1つの PR で行いました（この項目の PR）。

## 参考

- [BE-0313 — serve の GitHub org メンバーシップと Team ベースの RBAC](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac-ja.md) —
  この項目がメンバーシップの軸を加える、サインインのゲートとロールの解決。
- [BE-0352 — admin 用 GitHub Team の環境変数が Organization メンバーシップのサインインゲートを迂回する](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass-ja.md) —
  Team がサインインを決める最初の経路。この項目は、その fail closed の引き換えを org 単位の Team まで
  広げる。
- [BE-0375 — serve の org ライフサイクル管理](../BE-0375-serve-org-lifecycle-management/BE-0375-serve-org-lifecycle-management-ja.md) —
  この項目のフィールドが通る、データベース由来の org モデルと `/api/orgs…` エンドポイント。
- [`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py) — `OrgConfig.github_teams`、
  `admitting_teams`、`in_teams`、および `identity_matches_org` と `org_for_identity` が共有する
  `_match_org`。
- [`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py) — `oauth_callback` のゲート、
  `_OrgModel.unmatched` の拒否理由、`in_admin_team`、`role_for`。
- [`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py) — `_fetch_teams`。その
  fail closed の挙動が、admin の迂回だけでなく Team を宣言した org のサインインも決めるようになる。
- [`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py) と
  [`migrations/versions/0016_org_github_teams.py`](../../bajutsu/serve/server/migrations/versions/0016_org_github_teams.py) —
  `orgs.github_teams` の列。
- [`bajutsu/serve/operations/orgs.py`](../../bajutsu/serve/operations/orgs.py) と
  [`bajutsu/templates/serve.orgs.mjs`](../../bajutsu/templates/serve.orgs.mjs) — メンバーシップの
  API と Orgs ページ。

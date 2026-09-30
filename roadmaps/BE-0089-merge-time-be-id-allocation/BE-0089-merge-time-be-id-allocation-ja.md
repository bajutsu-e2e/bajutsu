[English](BE-0089-merge-time-be-id-allocation.md) · **日本語**

# BE-0089 — マージ後に main で BE ID を採番する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0089](BE-0089-merge-time-be-id-allocation-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0089") |
| 実装 PR | [#359](https://github.com/bajutsu-e2e/bajutsu/pull/359)、[#436](https://github.com/bajutsu-e2e/bajutsu/pull/436)（無効化した claims/repair 機構を撤去） |
| トピック | コントリビューターワークフロー |
<!-- /BE-METADATA -->

## はじめに

ロードマップ項目の恒久 ID である `BE-NNNN` は、いまはプルリクエストを開いた瞬間に割り当てられます。
[`roadmap-id`](../../.github/workflows/roadmap-id.yml) ワークフローが `pull_request` を契機に走り、
次の空き番号を採番し、`refs/be-claims/*` ref として原子的に確保し、リネームをブランチへ push し、PR
タイトルの `BE-XXXX` を実 ID に書き換えます。
[BE-0061](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)
はこの経路を堅牢にし、2 つのブランチが同じ番号を取ることを防ぎました。その帰結として、番号は提案が受理
される前、PR を開いた時点で消費されます。

本項目は、採番を **PR のマージ後** に、しかも **`main` 上で** 行うよう移します。項目は作成、レビュー、
マージのすべてを通じて `BE-XXXX` プレースホルダのままで、ブランチは `BE-XXXX` を残した *そのまま*、
auto-merge（あるいは merge queue）でマージされます。そのうえで、`main` への push を契機とするワークフローが
既存の採番処理を `main` のツリーに対して走らせ、プレースホルダを次の空き `BE-NNNN` へリネームし、その
結果を `main` へ直接コミットします。これにより番号は、実際に出荷された項目にだけ、マージ順で割り当てられます。
したがって `main` 上の `BE-NNNN` 列は構造的に連番となり、却下や放棄された提案に番号を浪費させることが
ありません。

この方針は、BE-0061 が見送った代替案（「実 ID はマージ時にだけ振る」）から育ったものです。当時それが
扱いづらかった理由と、本項目の *初期草稿*（承認時にリネームをブランチへ push して採番する案）自体が
却下された理由は、同じです。承認後に PR ブランチへ push されるコミットは、ブランチ保護の「古い承認を
失効させる」を踏み、マージを止めてしまいます。マージ後に `main` で採番すれば、これを根本から取り除けます。
レビュー済みブランチへ承認後に何も push しないので、失効させる承認がそもそも存在しません。本項目はあくまで
開発インフラ（contributor workflow）にとどまる（どの経路にも LLM は入らず、`run` と CI は決定的なまま、
アプリ固有の事情がツールへ入り込むこともない）ので、prime directive のいずれにも抵触しません。BE-0061 が
採番を *衝突しない* ものにしたのに対し、本項目は採番を *受理を条件とし*、かつ *歯抜けのない* ものにします。
BE-0061 の直系の続きです。

## 動機

### 受理前に番号が消費される

PR オープン時の採番は、受理されるか否かにかかわらず、開かれたロードマップ PR がすべて `BE-NNNN` を
消費することを意味します。既存の仕組みはこれを和らげますが、解消はしません。

- **却下された PR は claim を解放します。** ロードマップ PR が閉じると（マージされても、されなくても）、
  （現在は撤去した）`roadmap-claims-gc` ワークフローがその PR の導入した `refs/be-claims/*` を解放して
  いました。ブランチ上のリネームは `main` に届いていないので、却下それ自体は `main` に行を残しません。
- **それでも番号列は歯抜けになることがあります。** 採番は単調で（最小の空き番号ではなく `max(used) + 1`）、
  採番順とマージ順が食い違い、かつ番号の *小さい* PR が却下されると、その番号は恒久的な穴になります。
  具体例を挙げます。PR-A が `BE-0080`、PR-B が `BE-0081` を採番し、`BE-0081` が先にマージされ、その後
  PR-A が却下されます。次の項目は `BE-0082` を採番し、`BE-0080` は永久に欠けます。

つまり `main` 上の `BE-NNNN` は連番である保証がなく、ID が、結局出荷されない提案に恒久的に費やされることが
あります。歯抜けは致命的ではありません（ID は設計上、恒久かつ単調であり、Bajutsu が踏襲する Swift-Evolution
の採番もそれを許容します）。それでも「BE-00xx って何だっけ」という混乱を招き、参照可能な番号を無駄にします。

`main` 上で、マージ順に採番すれば、歯抜けは構造的に消えます。却下された PR は決してマージされないので
採番処理に届かず、番号を消費しません。そして採番は項目が着地する順に `main` に対して走るので、列は穴のない、
連番かつ単調なものになります。

### なぜ承認時ではなく、マージ後に採番するのか

採番を後ろ倒しする先として真っ先に思いつくのは承認時です。レビュアーが approve したら採番し、
リネームをブランチへ push し、それから auto-merge する、という案です。本項目の初期草稿はこれを提案して
いましたが、この案はすっきり動きません。採番処理のコミットが承認レビューの *あと* に PR ブランチへ載るため、
ブランチ保護の「新しいコミットが push されたら古い承認を失効させる」を踏みます。承認は失効し、auto-merge は
止まります。これを避けるには、リポジトリ全体で古い承認の失効を無効化する（鈍い手で、承認後のあらゆる push
が承認を保持してしまう）か、GitHub が必須レビューに確実にはカウントしない bot の再承認を使うしかありません。

マージ後に `main` で採番すれば、この問題ごと回避できます。レビュー済みブランチは承認されたとおり、`BE-XXXX`
を残したままマージされ、番号はブランチではなく `main` へのコミットで割り当てられます。承認後にブランチへ
push しない以上、失効は起きません。おまけに、契機は「承認 → auto-merge → リネーム」から、単一の
「`main` への push」へと縮みます。

## 詳細設計

採番ロジック（[`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py)）は
**そのまま** 再利用します。このロジックはすでに、ワーキングツリー内の各 `BE-XXXX-<slug>/` プレースホルダを
見つけ、項目ごとに `max(used) + 1` を採番し（決定性のため slug 順）、ディレクトリとファイルを `git mv` し、
ファイル内のトークンを書き換え、索引の行を直します。変わるのは、走らせる場所とタイミング（`main` に対して、
マージ後に）と、その周りのワークフロー配管だけです。

### 流れ

1. `ideation` スキルが項目を `BE-XXXX-<slug>` として書き起こします（従来どおり）。PR は素の scoped タイトル
   （例: `docs(roadmap): …`）で開き、`[BE-NNNN]` 接頭辞は付けません。
2. レビュアーが `BE-XXXX` の内容をレビューして承認します。**ブランチ上では採番は起きません。**
3. auto-merge（または merge queue）がブランチを `BE-XXXX` を残した **そのまま** マージします。承認後に
   ブランチへコミットが push されないので、承認は失効しません。
4. マージは `main` への push です。`push: main` を契機とする `roadmap-id` ジョブが `main` に対して採番
   処理を走らせ、リネームと再生成した索引を `main` へ直接コミットし、マージ済み PR に、割り当てた
   `BE-NNNN` を知らせるコメント（項目へのリンク付き）を投稿します。

PR タイトルは書き換えず、BE 作成 PR には `[BE-NNNN]` 接頭辞を付けません。実番号はブランチ上では決して
分かりません（採番はマージ後にだけ行われる）。そのため、プレースホルダの `[BE-XXXX]` を付けても情報を持たず、
マージ後に書き換えるのは無駄な手間でしかありません。割り当てた ID を記録し、マージ済み PR と結びつける自然で
永続的な場所は bot コメントです。なお、既存の `[BE-NNNN]` 接頭辞ルールは、すでに採番済みの項目を
*実装する* PR（番号が初めから分かっている）に適用されます（「参考」を参照）。

### main で採番するワークフロー

`roadmap-id` の契機を `pull_request` から `main` への `push` へ変えます。

```yaml
on:
  push:
    branches: [main]
    paths: ['roadmaps/**']
concurrency:
  group: roadmap-id-main
  cancel-in-progress: false   # 直列化する。キュー済みの採番を取りこぼさない
permissions:
  contents: write             # 採番コミットを main へ push する
  pull-requests: write        # 割り当てた ID をマージ済み PR にコメントする
```

ジョブは `main` をチェックアウトし、採番処理を走らせ、採番コミットを `main` へ push し返します。

```bash
out="$(python3 scripts/allocate_roadmap_ids.py)"      # BE-XXXX ディレクトリをその場でリネーム
echo "$out" | grep -q '^Allocated ' || exit 0          # プレースホルダなし -> no-op（後述の自己トリガー）
python3 scripts/build_roadmap_index.py                 # 採番済みの行を索引へ追加
git add -A && git commit -m "docs(roadmap): allocate BE IDs for merged placeholder items"
for attempt in 1 2 3 4 5; do                            # main が動いていることがある。rebase して再試行
  git push origin HEAD:main && break
  git fetch origin main && git rebase origin/main || git rebase --abort
done
```

別途、小さな `pull_request_review` ワークフローが承認時に auto-merge を有効化し、流れを手放しにします
（`gh pr merge --auto`）。auto-merge の有効化はコミットを push しないので、レビューを失効させることは
ありません。auto-merge は作成者や merge queue から有効化しても構わず、採番処理はそのどれにも依存しません。

### GitHub App を用意する

bypass する ID は専用の GitHub App とし、admin 権限を持つメンテナーが一度だけ作成します。

1. **App を作ります**（org 所有でも repo 所有でもよい）。webhook もコールバック URL も不要で、CI で
   インストールトークンを発行するためだけに使います。リポジトリ権限は **Contents: Read and write**（採番
   コミットを push する）と **Pull requests: Read and write**（割り当てた ID をコメントする）だけにし、
   ほかは付与しません。
2. **このリポジトリにだけインストールします**。権限の及ぶ範囲を 1 リポジトリに閉じます。
3. **`main` の ruleset の bypass リストに、この App だけを載せます**。インストールトークンが採番コミットの
   push（あるいは採番 PR のマージ）をブランチ保護越しに通せるようにします。
4. **秘密鍵を生成し**、App ID とともに Actions secret に保存します。Environment 経由で `main` ref に限定し、
   PR を契機とするジョブからは読めないようにします。

ワークフローはこれらの secret から短命なインストールトークンを発行し、checkout、push、`gh` に使います。

```yaml
    - uses: actions/create-github-app-token@<sha>   # full コミット SHA で pin する
      id: app-token
      with:
        app-id: ${{ secrets.AUTOMATION_BOT_APP_ID }}
        private-key: ${{ secrets.AUTOMATION_BOT_PRIVATE_KEY }}
    - uses: actions/checkout@<sha>
      with:
        token: ${{ steps.app-token.outputs.token }}
```

トークンは約 1 時間で失効し、人には紐づきません。App が API 経由で作るコミットは verified／署名付きで App に
帰属するので、すべての bypass push が監査可能になります（「bypass する ID を守る」を参照）。

### 実現可能性

設計を支える前提を、具体的に示します。

- **`main` 上の一時的な `BE-XXXX` でもゲートは緑のままです。** ロードマップ系の 3 つのツールはいずれも
  `^BE-(\d{4})-` で判定し、それ以外を読み飛ばします。3 つのツールとは
  [`tests/test_roadmap_format.py`](../../tests/test_roadmap_format.py) と
  [`tests/test_roadmap_index.py`](../../tests/test_roadmap_index.py)
  （後者は [`build_roadmap_index.py`](../../scripts/build_roadmap_index.py) 経由で、`load_items` が
  番号なしディレクトリで `continue` する）、そして
  [`promote_roadmap_items.py`](../../scripts/promote_roadmap_items.py) です。したがって `BE-XXXX`
  ディレクトリは format チェックから見えず、索引の行を生まず（差分なし）、移動もされません。実地でも、
  このプレースホルダ項目をツリーに置いたまま `make check` が通ります。よってマージコミットと採番コミットの間、
  `main` に赤くなる窓は生じません。
- **採番は構造的に連番で、歯抜けが生じません。** `main` 上では採番処理の `used` 集合は `main` の番号付き項目
  だけなので、項目がマージされる順に `max + 1` を払い出します。マージ順が採番順であり、却下された PR はマージ
  されないので番号を消費しません。動機で述べた歯抜けの源である「`max + 1` とマージ順の食い違い」は残りません。
- **同時マージは直列化され、複数項目のマージも扱えます。** `concurrency: roadmap-id-main` を
  `cancel-in-progress: false` で使い、採番ランをキューに並べて、ほぼ同時の 2 マージを 1 つずつ採番します。
  1 つの push が複数のプレースホルダを運ぶ場合（2 項目を足す PR、あるいはジョブが走る前に 2 件がマージ
  された場合）はすでに対応済みで、`allocate()` は `placeholder_dirs()` を slug 順に回して連続した ID を
  割り当てます。push し返しは後続のマージと競合することがありますが、上記の `fetch` ＋ `rebase` ＋ 再試行
  ループが整合させます。採番コミット自体は `roadmaps/**` を触るのでワークフローを再トリガーしますが、その
  実行はプレースホルダを見つけられず no-op で終わります。
- **保護された `main` に採番を着地させるには bypass actor が要ります（本設計の要となる前提）。** いまは bot が
  *PR ブランチ* へリネームを push していますが、ここでは採番コミットを `main` に載せねばなりません。多くの
  リポジトリで `main` は保護されています（直接 push 不可、PR はレビュー必須）。着地のさせ方は 2 つあり、
  どちらも同じ付与を要します。`main` の ruleset の bypass リストに載せたトークン（GitHub App のインストール
  トークンまたは PAT）による直接 push か、bot の採番 PR ＋ auto-merge です。ただし auto-merge は必須承認を
  *免除しない* ので、その PR もまた bot が review 要件を bypass（あるいは充足）できる必要があります。
  メンテナンス用の bot を bypass actor に加えるのは定石です。既定の `GITHUB_TOKEN` は通常 `main` の保護に
  阻まれるので、いずれにせよ bypass 可能な ID が要ります。org の方針が `main` への bypass を一切禁じている
  なら、このマージ後 `main` 採番設計は成立しません。その場合は *検討した代替案* の承認時フォールバック
  （`main` へ一切 push しない）を採ります。
- **マージ済み PR へのコメントはベストエフォートです。** `push` イベントは PR 番号を持たないので、ジョブは
  マージコミットから解決し（`gh api repos/{owner}/{repo}/commits/${SHA}/pulls`）、その PR に
  `gh pr comment <pr> --body "Allocated **BE-NNNN** — <main 上の項目へのリンク>"` します。コメントは
  情報提供であり、外しても（たとえばどの PR にも対応しないマージコミットでも）無害で、すでに `main` へ
  着地した採番コミットを妨げることはありません。

### bypass する ID を守る

`main` の保護を bypass できるトークンは価値の高いクレデンシャルです。そのため本設計は秘密の管理だけに
頼らず、その権限が及ぶ面を構造的に小さく保ちます。

- **特権ジョブはマージ後に、レビュー済みコードだけを走らせます。** 契機は `push: main` で、これはレビューと
  必須チェックを通過したマージの *あと* にのみ発火します。ジョブは `main` をチェックアウトし、`main` 上に
  ある `allocate_roadmap_ids.py` ／ `build_roadmap_index.py` を実行します（PR ブランチ側の版ではない）。特権
  トークンの下で信頼できない PR head のコードをチェックアウトすること（`pull_request_target` を使った典型的な
  権限昇格）は一切しないので、ジョブ内で攻撃者由来のコードが走ることはありません。
- **出力を縛り、検証します。** 正規の push は常に同じ狭い機械的差分になります。`BE-XXXX` → `BE-NNNN` の
  rename と索引の再生成で、`roadmaps/**` の中だけです。ガードが採番処理を check モードで再実行します（あるいは
  push されたコミットを diff します）。bypass コミットが `roadmaps/**` の外を触る、または期待される rename
  から逸脱したら赤にし、万一の悪用の被害範囲をこの形に上限づけます。
- **PAT ではなく、権限を絞った GitHub App を使います。** bypass する ID は個人の PAT ではなく専用の App と
  します（用意の手順は「GitHub App を用意する」を参照）。インストールトークンは短命（約 1 時間）で人に
  紐づかず、このリポジトリで `contents: write` ＋ `pull-requests: write` だけに限られます。そのため bypass
  の付与は必要最小限の権限にとどまります。
- **特権ジョブでのサプライチェーン規律。** third-party action はすべて full コミット SHA で pin し（リポジトリ
  既存のルール）、依存インストールを走らせない（採番スクリプトは stdlib のみの Python）ことで、トークンと
  並走する外部コードをなくします。
- **監査可能な署名付きコミット。** App が API 経由で作るコミットは verified／署名付きで App に帰属するので、
  すべての bypass push が履歴と audit log に残ります。renumber のパターンに合致しない App の push は検知
  可能な異常です。
- **bypass なしフォールバックはクレデンシャルそのものを無くします。** 権限を絞った App の bypass すら許容
  できない場合、承認時ブランチ採番のフォールバック（*検討した代替案* 参照）は `main` の bypass ID を一切
  持ち込みません。最強の緩和であり、代償は古い承認の失効設定だけです。

### BE-0061 の仕組みとの関係

`main` 上での採番は採番を 1 つのブランチに直列化するので、BE-0061 が塞いだ「同一窓内のレース」は
ほぼ意味を失います。`main` に触れる採番ランは同時に高々 1 つで、常に最新の `main` を読みます。原子的な
`refs/be-claims/*` 台帳、`roadmap-id-repair`、`roadmap-claims-gc` は、このモデルでは冗長です。当初は
多層防御としてそのまま残していましたが、マージ時採番が実証できたので撤去しました。台帳、両ワークフロー、
その補助スクリプト、採番器の `--repair` 経路を削除し、純粋な採番器とマージ時の `roadmap-id` ワークフロー
だけを残しました（[BE-0061](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)
の「進捗」を参照）。

### prime directive への適合

本項目は開発インフラ（contributor workflow）にとどまります。どの経路にも LLM を足さず、`run` と CI は
決定的なまま、アプリ固有の事情がツール、ドライバ、ランナーへ移ることもありません。
[BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)、
BE-0061、
[BE-0074](../BE-0074-be-template-standardization/BE-0074-be-template-standardization.md)、
[BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md) と同じ系譜にあります。

## 検討した代替案

- **承認時にリネームをブランチへ push して採番し、それから auto-merge する案（`main` へ push しない
  フォールバック）。** この案は `main` へ一切 push しません。リネームは *PR ブランチ* に載り、`main` への
  マージは通常の保護付き PR / auto-merge を通ります。よって **bot を `main` の bypass actor にできない場合**
  （マージ後 `main` 採番が成立しない状況）に採るべき設計です。代償はブランチ保護の「新しいコミットが push
  されたら古い承認を失効させる」設定で、承認後のリネームコミットが承認を失効させ auto-merge を止めます。
  この失効は *その 1 設定だけ* を OFF にすれば避けられます（「PR 必須」「レビュー必須」とは独立）。あるいは
  （きれいではありませんが）GitHub が確実にはカウントしない bot の再承認でも避けられます。*主たる* 設計
  として却下するのは、bypass actor を使えるならマージ後 `main` 採番のほうが、保護設定のトレードオフなしに
  歯抜けゼロを実現できるからにすぎません。使えない場合は、この案が正式なフォールバックです。
- **`main` への直接 push ではなく、bot の採番 PR で採番します。** 直接 push を避ける手に見えますが、bypass
  要件を避ける手では *ありません*。採番 PR も保護された `main` にマージせねばならず、auto-merge は必須
  レビューを免除しないので、結局 bot がそれを bypass（または充足）する必要があります。採番ごとに 2 本目の
  PR が増え、`BE-XXXX` が `main` に居座る窓が伸びるだけで、必要な権限は減りません。採番の足跡を PR として
  残したい場合の、体裁上の選択肢としてのみ残します。
- **PR オープン時採番のまま、`max + 1` を最小の空き番号に変えます。** 変更はずっと小さく（契機の変更も
  `main` への push も要らない）、却下された小さい番号の PR が残した穴も埋まります。主たる経路としては却下
  します。却下された PR のタイトルやレビュースレッドに既に現れた `[BE-00xx]` を別項目に再利用すると、その
  ID が履歴上 *曖昧* になります。これはまさに「番号を再利用しない」というルールが防ごうとしていることです。
  より軽い代替としては妥当です。
- **歯抜けを無害として受け入れ、文書化して終えます。** 最も安価な案です。ID は設計上恒久かつ単調で、
  Swift-Evolution も歯抜けを許容します。`main` の採番を構造的に連番に保つという掲げた目的に照らして却下します。
- **BE-0061 の判断に手を付けません（何もしません）。** 却下します。費やされた ID と非連番という懸念は、
  小さくとも実在し、BE-0061 のレース保証を一切再び開くことなく取り除けます。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。
- **後日の注記：** 上記の例示ワークフローは今も
  `python3 scripts/build_roadmap_index.py  # 採番済みの行を索引へ追加` という手順を示しています。この
  呼び出しは、それが更新していた `README.md` / `README-ja.md` の生成済み索引表とともに
  [#1257](https://github.com/bajutsu-e2e/bajutsu/pull/1257) で撤去されました。`roadmap-id` ワークフロー
  はもうこれを実行しません。行を追加する先が無くなったためです。本項目が定める採番の仕組み
  （確保・リネーム・push）自体には影響しません。

## 参考

- [BE-0061 — Collision-proof BE-ID allocation](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)：
  本項目が拡張する項目です。BE-0061 の「検討した代替案」が、本項目で実現する「マージ時に採番する」案を
  記録しています。BE-0061 の堅牢化（原子的 claim、repair、claims-gc）は、本モデルが実証できたのちに撤去
  しました（BE-0061 の「進捗」を参照）。
- [`.github/workflows/roadmap-id.yml`](../../.github/workflows/roadmap-id.yml)：契機を
  `pull_request` から `push: main` へ移す対象です。`roadmap-id-repair` と `roadmap-claims-gc` の
  ワークフローも対象でしたが、その後削除しました。
- [`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py)（そのまま再利用）、
  [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py)、
  [`scripts/promote_roadmap_items.py`](../../scripts/promote_roadmap_items.py)：いずれも `BE-XXXX`
  ディレクトリを読み飛ばすツールです。この 3 つが、一時的な窓の間 `main` を緑に保ちます。
- [`actions/create-github-app-token`](https://github.com/actions/create-github-app-token)：ワークフロー
  内で bypass する App の短命なインストールトークンを発行する action です。ほかの third-party action と
  同様に full コミット SHA で pin します。
- [`tests/test_roadmap_format.py`](../../tests/test_roadmap_format.py)、
  [`tests/test_roadmap_index.py`](../../tests/test_roadmap_index.py)：`^BE-(\d{4})-` で判定し、
  プレースホルダを無視するゲートテストです。
- [`CLAUDE.md`](../../CLAUDE.md) ·
  [`roadmaps/README.md`](../README.md) ·
  [`docs/ai-development.md`](../../docs/ai-development.md)：番号を PR オープン時ではなくマージ後の
  `main` で採番し、BE 作成 PR のタイトルに `[BE-NNNN]` 接頭辞を付けない（接頭辞ルールは、採番済みの項目を
  実装する PR には残る）よう更新する作成ルールです。
- GitHub ドキュメント（*Automatically merging a pull request*（auto-merge）と *Managing a merge queue*）：
  `BE-XXXX` ブランチをそのままマージする標準機能です。

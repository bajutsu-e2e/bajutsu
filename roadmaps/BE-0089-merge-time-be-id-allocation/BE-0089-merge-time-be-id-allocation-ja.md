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

ロードマップ項目の恒久 ID である `BE-NNNN` は、いまはプルリクエストを開いた瞬間に割り当てられる。
[`roadmap-id`](../../.github/workflows/roadmap-id.yml) ワークフローが `pull_request` を契機に走り、
次の空き番号を採番し、`refs/be-claims/*` ref として原子的に確保し、リネームをブランチへ push し、PR
タイトルの `BE-XXXX` を実 ID に書き換える。
[BE-0061](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)
はこの経路を堅牢にし、2 つのブランチが同じ番号を取ることを防いだ。その帰結として、番号は提案が受理
される前、PR を開いた時点で消費される。

本項目は、採番を **PR のマージ後** に、しかも **`main` 上で** 行うよう移す。項目は作成、レビュー、
マージのすべてを通じて `BE-XXXX` プレースホルダのままで、ブランチは `BE-XXXX` を残した *そのまま*、
auto-merge（あるいは merge queue）でマージされる。そのうえで、`main` への push を契機とするワークフローが
既存の採番処理を `main` のツリーに対して走らせ、プレースホルダを次の空き `BE-NNNN` へリネームし、その
結果を `main` へ直接コミットする。これにより番号は、実際に出荷された項目にだけ、マージ順で割り当てられる。
したがって `main` 上の `BE-NNNN` 列は構造的に連番となり、却下や放棄された提案に番号を浪費させることが
ない。

この方針は、BE-0061 が見送った代替案（「実 ID はマージ時にだけ振る」）から育ったものである。当時それが
扱いづらかった理由と、本項目の *初期草稿*（承認時にリネームをブランチへ push して採番する案）自体が
却下された理由は、同じである。承認後に PR ブランチへ push されるコミットは、ブランチ保護の「古い承認を
失効させる」を踏み、マージを止めてしまう。マージ後に `main` で採番すれば、これを根本から取り除ける。
レビュー済みブランチへ承認後に何も push しないので、失効させる承認がそもそも存在しない。本項目はあくまで
開発インフラ（contributor workflow）にとどまる（どの経路にも LLM は入らず、`run` と CI は決定的なまま、
アプリ固有の事情がツールへ入り込むこともない）ので、prime directive のいずれにも抵触しない。BE-0061 が
採番を *衝突しない* ものにしたのに対し、本項目は採番を *受理を条件とし*、かつ *歯抜けのない* ものにする、
その直系の続きである。

## 動機

### 受理前に番号が消費される

PR オープン時の採番は、受理されるか否かにかかわらず、開かれたロードマップ PR がすべて `BE-NNNN` を
消費することを意味する。既存の仕組みはこれを和らげるが、解消はしない。

- **却下された PR は claim を解放する。** ロードマップ PR が閉じると（マージされても、されなくても）、
  （現在は撤去した）`roadmap-claims-gc` ワークフローがその PR の導入した `refs/be-claims/*` を解放して
  いた。ブランチ上のリネームは `main` に届いていないので、却下それ自体は `main` に行を残さない。
- **それでも番号列は歯抜けになりうる。** 採番は単調で（最小の空き番号ではなく `max(used) + 1`）、採番順と
  マージ順が食い違い、かつ番号の *小さい* PR が却下されると、その番号は恒久的な穴になる。具体例を挙げる。
  PR-A が `BE-0080`、PR-B が `BE-0081` を採番し、`BE-0081` が先にマージされ、その後 PR-A が却下される。次の
  項目は `BE-0082` を採番し、`BE-0080` は永久に欠ける。

つまり `main` 上の `BE-NNNN` は連番である保証がなく、ID が、結局出荷されない提案に恒久的に費やされうる。
歯抜けは致命的ではない（ID は設計上、恒久かつ単調であり、Bajutsu が踏襲する Swift-Evolution の採番も
それを許容する）が、「BE-00xx って何だっけ」という混乱を招き、参照可能な番号を無駄にする。

`main` 上で、マージ順に採番すれば、歯抜けは構造的に消える。却下された PR は決してマージされないので
採番処理に届かず、番号を消費しない。そして採番は項目が着地する順に `main` に対して走るので、列は穴のない、
連番かつ単調なものになる。

### なぜ承認時ではなく、マージ後に採番するのか

採番を後ろ倒しする先として真っ先に思いつくのは承認時である。レビュアーが approve したら採番し、
リネームをブランチへ push し、それから auto-merge する、という案だ。本項目の初期草稿はこれを提案していたが、
これはすっきり動かない。採番処理のコミットが承認レビューの *あと* に PR ブランチへ載るため、ブランチ保護の
「新しいコミットが push されたら古い承認を失効させる」を踏む。承認は失効し、auto-merge は止まる。これを
避けるには、リポジトリ全体で古い承認の失効を無効化する（鈍い手で、承認後のあらゆる push が承認を保持して
しまう）か、GitHub が必須レビューに確実にはカウントしない bot の再承認を使うしかない。

マージ後に `main` で採番すれば、この問題ごと回避できる。レビュー済みブランチは承認されたとおり、`BE-XXXX`
を残したままマージされ、番号はブランチではなく `main` へのコミットで割り当てられる。承認後にブランチへ
push しない以上、失効は起きない。おまけに、契機は「承認 → auto-merge → リネーム」から、単一の
「`main` への push」へと縮む。

## 詳細設計

採番ロジック（[`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py)）は
**そのまま** 再利用する。これはすでに、ワーキングツリー内の各 `BE-XXXX-<slug>/` プレースホルダを見つけ、
項目ごとに `max(used) + 1` を採番し（決定性のため slug 順）、ディレクトリとファイルを `git mv` し、ファイル
内のトークンを書き換え、索引の行を直す。変わるのは、走らせる場所とタイミング（`main` に対して、マージ後に）
と、その周りのワークフロー配管だけである。

### 流れ

1. `ideation` スキルが項目を `BE-XXXX-<slug>` として書き起こす（従来どおり）。PR は素の scoped タイトル
   （例: `docs(roadmap): …`）で開き、`[BE-NNNN]` 接頭辞は付けない。
2. レビュアーが `BE-XXXX` の内容をレビューして承認する。**ブランチ上では採番は起きない。**
3. auto-merge（または merge queue）がブランチを `BE-XXXX` を残した **そのまま** マージする。承認後に
   ブランチへコミットが push されないので、承認は失効しない。
4. マージは `main` への push である。`push: main` を契機とする `roadmap-id` ジョブが `main` に対して採番
   処理を走らせ、リネームと再生成した索引を `main` へ直接コミットし、マージ済み PR に、割り当てた
   `BE-NNNN` を知らせるコメント（項目へのリンク付き）を投稿する。

PR タイトルは書き換えず、BE 作成 PR には `[BE-NNNN]` 接頭辞を付けない。実番号はブランチ上では決して
分からない（採番はマージ後にだけ行われる）ので、プレースホルダの `[BE-XXXX]` を付けても情報を持たず、
マージ後に書き換えるのは無駄な手間でしかない。割り当てた ID を記録し、マージ済み PR と結びつける自然で
永続的な場所は bot コメントである。なお、既存の `[BE-NNNN]` 接頭辞ルールは、すでに採番済みの項目を
*実装する* PR（番号が初めから分かっている）に適用される（「参考」を参照）。

### main で採番するワークフロー

`roadmap-id` の契機を `pull_request` から `main` への `push` へ変える。

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

ジョブは `main` をチェックアウトし、採番処理を走らせ、採番コミットを `main` へ push し返す。

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

別途、小さな `pull_request_review` ワークフローが承認時に auto-merge を有効化し、流れを手放しにする
（`gh pr merge --auto`）。auto-merge の有効化はコミットを push しないので、レビューを失効させることは
ない。auto-merge は作成者や merge queue から有効化してもよく、採番処理はそのどれに依存するものでもない。

### GitHub App を用意する

bypass する ID は専用の GitHub App とし、admin 権限を持つメンテナーが一度だけ作成する。

1. **App を作る**（org 所有でも repo 所有でもよい）。webhook もコールバック URL も不要で、CI で
   インストールトークンを発行するためだけに使う。リポジトリ権限は **Contents: Read and write**（採番
   コミットを push する）と **Pull requests: Read and write**（割り当てた ID をコメントする）だけにし、
   ほかは付与しない。
2. **このリポジトリにだけインストールする**。権限の及ぶ範囲を 1 リポジトリに閉じる。
3. **`main` の ruleset の bypass リストに、この App だけを載せる**。インストールトークンが採番コミットの
   push（あるいは採番 PR のマージ）をブランチ保護越しに通せるようにする。
4. **秘密鍵を生成し**、App ID とともに Actions secret に保存する。Environment 経由で `main` ref に限定し、
   PR を契機とするジョブからは読めないようにする。

ワークフローはこれらの secret から短命なインストールトークンを発行し、checkout、push、`gh` に使う。

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

トークンは約 1 時間で失効し、人には紐づかない。App が API 経由で作るコミットは verified／署名付きで App に
帰属するので、すべての bypass push が監査可能になる（「bypass する ID を守る」を参照）。

### 実現可能性

設計を支える前提を、具体的に示す。

- **`main` 上の一時的な `BE-XXXX` でもゲートは緑のまま。** ロードマップ系の 3 つのツールはいずれも
  `^BE-(\d{4})-` で判定し、それ以外を読み飛ばす。すなわち
  [`tests/test_roadmap_format.py`](../../tests/test_roadmap_format.py) と
  [`tests/test_roadmap_index.py`](../../tests/test_roadmap_index.py)
  （後者は [`build_roadmap_index.py`](../../scripts/build_roadmap_index.py) 経由で、`load_items` が
  番号なしディレクトリで `continue` する）、そして
  [`promote_roadmap_items.py`](../../scripts/promote_roadmap_items.py) である。したがって `BE-XXXX`
  ディレクトリは format チェックから見えず、索引の行を生まず（差分なし）、移動もされない。実地でも、
  このプレースホルダ項目をツリーに置いたまま `make check` が通る。よってマージコミットと採番コミットの間、
  `main` に赤くなる窓は生じない。
- **採番は構造的に連番で、歯抜けが生じない。** `main` 上では採番処理の `used` 集合は `main` の番号付き項目
  だけなので、項目がマージされる順に `max + 1` を払い出す。マージ順が採番順であり、却下された PR はマージ
  されないので番号を消費しない。動機で述べた歯抜けの源である「`max + 1` とマージ順の食い違い」が残らない。
- **同時マージは直列化され、複数項目のマージも扱える。** `concurrency: roadmap-id-main` を
  `cancel-in-progress: false` で使い、採番ランをキューに並べて、ほぼ同時の 2 マージを 1 つずつ採番する。
  1 つの push が複数のプレースホルダを運ぶ場合（2 項目を足す PR、あるいはジョブが走る前に 2 件がマージ
  された場合）はすでに対応済みで、`allocate()` は `placeholder_dirs()` を slug 順に回して連続した ID を
  割り当てる。push し返しは後続のマージと競合しうるが、上記の `fetch` ＋ `rebase` ＋ 再試行ループが
  整合させる。採番コミット自体は `roadmaps/**` を触るのでワークフローを再トリガーするが、その実行は
  プレースホルダを見つけられず no-op で終わる。
- **保護された `main` に採番を着地させるには bypass actor が要る（本設計の要となる前提）。** いまは bot が
  *PR ブランチ* へリネームを push しているが、ここでは採番コミットを `main` に載せねばならず、多くのリポジトリ
  で `main` は保護されている（直接 push 不可、PR はレビュー必須）。着地のさせ方は 2 つあり、どちらも同じ
  付与を要する。`main` の ruleset の bypass リストに載せたトークン（GitHub App のインストールトークン
  または PAT）による直接 push か、bot の採番 PR ＋ auto-merge である。ただし auto-merge は必須承認を
  *免除しない* ので、その PR もまた bot が review 要件を bypass（あるいは充足）できる必要がある。メンテナンス
  用の bot を bypass actor に加えるのは定石であり、既定の `GITHUB_TOKEN` は通常 `main` の保護に阻まれるので、
  いずれにせよ bypass 可能な ID が要る。org の方針が `main` への bypass を一切禁じているなら、このマージ後
  `main` 採番設計は成立しない。その場合は *検討した代替案* の承認時フォールバック（`main` へ一切 push
  しない）を採る。
- **マージ済み PR へのコメントはベストエフォート。** `push` イベントは PR 番号を持たないので、ジョブは
  マージコミットから解決し（`gh api repos/{owner}/{repo}/commits/${SHA}/pulls`）、その PR に
  `gh pr comment <pr> --body "Allocated **BE-NNNN** — <main 上の項目へのリンク>"` する。コメントは
  情報提供であり、外しても（たとえばどの PR にも対応しないマージコミットでも）無害で、すでに `main` へ
  着地した採番コミットを妨げることはない。

### bypass する ID を守る

`main` の保護を bypass できるトークンは価値の高いクレデンシャルなので、本設計は秘密の管理だけに頼らず、
その権限が及ぶ面を構造的に小さく保つ。

- **特権ジョブはマージ後に、レビュー済みコードだけを走らせる。** 契機は `push: main` で、これはレビューと
  必須チェックを通過したマージの *あと* にのみ発火する。ジョブは `main` をチェックアウトし、`main` 上に
  ある `allocate_roadmap_ids.py` ／ `build_roadmap_index.py` を実行する（PR ブランチ側の版ではない）。特権
  トークンの下で信頼できない PR head のコードをチェックアウトすること（`pull_request_target` を使った典型的な
  権限昇格）は一切しないので、ジョブ内で攻撃者由来のコードが走ることはない。
- **出力を縛り、検証する。** 正規の push は常に同じ狭い機械的差分になる。`BE-XXXX` → `BE-NNNN` の rename と
  索引の再生成で、`roadmaps/**` の中だけだ。ガードが採番処理を check モードで再実行する（あるいは push
  されたコミットを diff する）。bypass コミットが `roadmaps/**` の外を触る、または期待される rename から
  逸脱したら赤にし、万一の悪用の被害範囲をこの形に上限づける。
- **PAT ではなく、権限を絞った GitHub App。** bypass する ID は個人の PAT ではなく専用の App とする（用意の
  手順は「GitHub App を用意する」を参照）。インストールトークンは短命（約 1 時間）で人に紐づかず、この
  リポジトリで `contents: write` ＋ `pull-requests: write` だけに限られるので、bypass の付与は必要最小限の
  権限にとどまる。
- **特権ジョブでのサプライチェーン規律。** third-party action はすべて full コミット SHA で pin し（リポジトリ
  既存のルール）、依存インストールを走らせない（採番スクリプトは stdlib のみの Python である）ことで、トークン
  と並走する外部コードをなくす。
- **監査可能な署名付きコミット。** App が API 経由で作るコミットは verified／署名付きで App に帰属するので、
  すべての bypass push が履歴と audit log に残る。renumber のパターンに合致しない App の push は検知可能な異常で
  ある。
- **bypass なしフォールバックはクレデンシャルそのものを無くす。** 権限を絞った App の bypass すら許容できない
  場合、承認時ブランチ採番のフォールバック（*検討した代替案* 参照）は `main` の bypass ID を一切持ち込まない。
  最強の緩和であり、代償は古い承認の失効設定だけである。

### BE-0061 の仕組みとの関係

`main` 上での採番は採番を 1 つのブランチに直列化するので、BE-0061 が塞いだ「同一窓内のレース」は
ほぼ意味を失う。`main` に触れる採番ランは同時に高々 1 つで、常に最新の `main` を読む。原子的な
`refs/be-claims/*` 台帳、`roadmap-id-repair`、`roadmap-claims-gc` は、このモデルでは冗長である。当初は
多層防御としてそのまま残していたが、マージ時採番が実証できたので撤去した。台帳、両ワークフロー、その補助
スクリプト、採番器の `--repair` 経路を削除し、純粋な採番器とマージ時の `roadmap-id` ワークフローだけを
残した（[BE-0061](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)
の「進捗」を参照）。

### prime directive への適合

開発インフラ（contributor workflow）にとどまる。どの経路にも LLM を足さず、`run` と CI は決定的なまま、
アプリ固有の事情がツール、ドライバ、ランナーへ移ることもない。
[BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)、
BE-0061、
[BE-0074](../BE-0074-be-template-standardization/BE-0074-be-template-standardization.md)、
[BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md) と同じ系譜にある。

## 検討した代替案

- **承認時にリネームをブランチへ push して採番し、それから auto-merge する案（`main` へ push しない
  フォールバック）。** これは `main` へ一切 push しない。リネームは *PR ブランチ* に載り、`main` へのマージは
  通常の保護付き PR / auto-merge を通る。よって **bot を `main` の bypass actor にできない場合**（マージ後
  `main` 採番が成立しない状況）に採るべき設計である。代償はブランチ保護の「新しいコミットが push された
  ら古い承認を失効させる」設定で、承認後のリネームコミットが承認を失効させ auto-merge を止める。これは
  *その 1 設定だけ* を OFF にすれば避けられる（「PR 必須」「レビュー必須」とは独立）。あるいは（きれいでは
  ないが）GitHub が確実にはカウントしない bot の再承認による。*主たる* 設計として却下するのは、bypass actor
  を使えるならマージ後 `main` 採番のほうが、保護設定のトレードオフなしに歯抜けゼロを実現できるからにすぎず、
  そうでなければこれが正式なフォールバックである。
- **`main` への直接 push ではなく、bot の採番 PR で採番する。** 直接 push を避ける手に見えるが、bypass 要件を
  避ける手では *ない*。採番 PR も保護された `main` にマージせねばならず、auto-merge は必須レビューを免除しない
  ので、結局 bot がそれを bypass（または充足）する必要がある。採番ごとに 2 本目の PR が増え、`BE-XXXX` が
  `main` に居座る窓が伸びるだけで、必要な権限は減らない。採番に目に見える PR の足跡を残したい場合の体裁上の
  選択肢としてのみ残す。
- **PR オープン時採番のまま、`max + 1` を最小の空き番号に変える。** 変更はずっと小さく（契機の変更も
  `main` への push も要らない）、却下された小さい番号の PR が残した穴も埋まる。主たる経路としては却下する。
  却下された PR のタイトルやレビュースレッドに既に現れた `[BE-00xx]` を別項目に再利用すると、その ID が
  履歴上 *曖昧* になる。これはまさに「番号を再利用しない」というルールが防ごうとしていることである。より
  軽い代替としては妥当。
- **歯抜けを無害として受け入れ、文書化して終える。** 最も安価。ID は設計上恒久かつ単調で、Swift-Evolution
  も歯抜けを許容する。`main` の採番を構造的に連番に保つという掲げた目的に照らして却下する。
- **BE-0061 の判断に手を付けない（何もしない）。** 却下。費やされた ID と非連番という懸念は、小さくとも
  実在し、BE-0061 のレース保証を一切再び開くことなく取り除ける。

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
  本項目が拡張する項目。その「検討した代替案」が、本項目で実現する「マージ時に採番する」案を記録して
  おり、その堅牢化（原子的 claim、repair、claims-gc）は、本モデルが実証できたのちに撤去した（その
  「進捗」を参照）。
- [`.github/workflows/roadmap-id.yml`](../../.github/workflows/roadmap-id.yml)（契機を
  `pull_request` から `push: main` へ移す対象）。`roadmap-id-repair` と `roadmap-claims-gc` の
  ワークフローも対象だったが、その後削除した。
- [`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py)（そのまま再利用）、
  [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py)、
  [`scripts/promote_roadmap_items.py`](../../scripts/promote_roadmap_items.py)：いずれも `BE-XXXX`
  ディレクトリを読み飛ばす 3 つのツール。これが一時的な窓の間 `main` を緑に保つ。
- [`actions/create-github-app-token`](https://github.com/actions/create-github-app-token)：ワークフロー
  内で bypass する App の短命なインストールトークンを発行する action。ほかの third-party action と同様に
  full コミット SHA で pin する。
- [`tests/test_roadmap_format.py`](../../tests/test_roadmap_format.py)、
  [`tests/test_roadmap_index.py`](../../tests/test_roadmap_index.py)：`^BE-(\d{4})-` で判定し、
  プレースホルダを無視するゲートテスト。
- [`CLAUDE.md`](../../CLAUDE.md) ·
  [`roadmaps/README.md`](../README.md) ·
  [`docs/ai-development.md`](../../docs/ai-development.md)：番号を PR オープン時ではなくマージ後の
  `main` で採番し、BE 作成 PR のタイトルに `[BE-NNNN]` 接頭辞を付けない（接頭辞ルールは、採番済みの項目を
  実装する PR には残る）よう更新する作成ルール。
- GitHub ドキュメント（*Automatically merging a pull request*（auto-merge）と *Managing a merge queue*）：
  `BE-XXXX` ブランチをそのままマージする標準機能。

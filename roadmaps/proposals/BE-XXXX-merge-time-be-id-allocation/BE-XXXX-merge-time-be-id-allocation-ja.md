[English](BE-XXXX-merge-time-be-id-allocation.md) · **日本語**

# BE-XXXX — マージ後に main で BE ID を採番する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-merge-time-be-id-allocation-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラック | [提案](../../README-ja.md#提案) |
| トピック | Development infrastructure (contributor workflow) |
<!-- /BE-METADATA -->

## はじめに

ロードマップ項目の恒久 ID である `BE-NNNN` は、いまはプルリクエストを開いた瞬間に割り当てられる。
[`roadmap-id`](../../../.github/workflows/roadmap-id.yml) ワークフローが `pull_request` を契機に走り、
次の空き番号を採番し、`refs/be-claims/*` ref として原子的に確保し、リネームをブランチへ push し、PR
タイトルの `BE-XXXX` を実 ID に書き換える。
[BE-0061](../../implemented/BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)
はこの経路を堅牢にし、2 つのブランチが同じ番号を取ることを防いだ。その帰結として、番号は **提案が受理
される前、PR を開いた時点で消費される。**

本項目は、採番を **PR のマージ後** に、しかも **`main` 上で** 行うよう移す。項目は作成・レビュー・
マージのすべてを通じて `BE-XXXX` プレースホルダのままで、ブランチは `BE-XXXX` を残した *そのまま*、
auto-merge（あるいは merge queue）でマージされる。そのうえで、`main` への push を契機とするワークフローが
既存の採番処理を `main` のツリーに対して走らせ、プレースホルダを次の空き `BE-NNNN` へリネームし、その
結果を `main` へ直接コミットする。これにより番号は、実際に出荷された項目にだけ、マージ順で割り当てられる
――したがって `main` 上の `BE-NNNN` 列は **構造的に連番** となり、却下・放棄された提案に番号を浪費させる
ことがない。

この方針は、BE-0061 が見送った代替案（「実 ID はマージ時にだけ振る」）から育ったものである。当時それが
扱いづらかった理由――そして本項目の *初期草稿*（承認時にリネームをブランチへ push して採番する案）自体が
却下された理由――は同じである。承認後に PR ブランチへ push されるコミットは、ブランチ保護の「古い承認を
失効させる」を踏み、マージを止めてしまう。**マージ後に `main` で採番する** ことは、これを根本から取り除く。
レビュー済みブランチへ承認後に何も push しないので、失効させる承認がそもそも存在しない。本項目はあくまで
開発インフラ（contributor workflow）にとどまる――どの経路にも LLM は入らず、`run` と CI は決定的なまま、
アプリ固有の事情がツールへ入り込むこともない――ので、prime directive のいずれにも抵触しない。BE-0061 が
採番を *衝突しない* ものにしたのに対し、本項目は採番を *受理を条件とし*、かつ *歯抜けのない* ものにする、
その直系の続きである。

## 動機

### 受理前に番号が消費される

PR オープン時の採番は、受理されるか否かにかかわらず、開かれたロードマップ PR がすべて `BE-NNNN` を
消費することを意味する。既存の仕組みはこれを和らげるが、解消はしない。

- **却下された PR は claim を解放する。** ロードマップ PR が閉じると――マージされても、されなくても――
  [`roadmap-claims-gc`](../../../.github/workflows/roadmap-claims-gc.yml) がその PR の導入した
  `refs/be-claims/*` を解放する。ブランチ上のリネームは `main` に届いていないので、却下それ自体は `main`
  に行を残さない。
- **それでも番号列は歯抜けになりうる。** 採番は **単調**――最小の空き番号ではなく `max(used) + 1`――
  なので、採番順とマージ順が食い違い、かつ番号の *小さい* PR が却下されると、その番号は恒久的な穴に
  なる。具体例を挙げる。PR-A が `BE-0080`、PR-B が `BE-0081` を採番し、`BE-0081` が先にマージされ、その後
  PR-A が却下される。次の項目は `BE-0082` を採番し、`BE-0080` は永久に欠ける。

つまり `main` 上の `BE-NNNN` は連番である保証がなく、ID が、結局出荷されない提案に恒久的に費やされうる。
歯抜けは致命的ではない――ID は設計上、恒久かつ単調であり、Bajutsu が踏襲する Swift-Evolution の採番も
それを許容する――が、「BE-00xx って何だっけ」という混乱を招き、参照可能な番号を無駄にする。

**`main` 上で、マージ順に** 採番すれば、歯抜けは構造的に消える。却下された PR は決してマージされないので
採番処理に届かず、番号を消費しない。そして採番は項目が着地する順に `main` に対して走るので、列は穴のない
連番・単調になる。

### なぜ承認時ではなく、マージ *後* に採番するのか

採番を後ろ倒しする先として真っ先に思いつくのは *承認時* である――レビュアーが approve したら採番し、
リネームをブランチへ push し、それから auto-merge する。本項目の初期草稿はこれを提案していたが、これは
すっきり動かない。採番処理のコミットが承認レビューの *あと* に PR ブランチへ載るため、ブランチ保護の
「新しいコミットが push されたら古い承認を失効させる」を踏む。承認は失効し、auto-merge は止まる。これを
避けるには、リポジトリ全体で古い承認の失効を無効化する（鈍い――承認後のあらゆる push が承認を保持して
しまう）か、GitHub が必須レビューに確実にはカウントしない bot の再承認を使うしかない。

マージ後に `main` で採番すれば、この問題ごと回避できる。レビュー済みブランチは承認されたとおり、`BE-XXXX`
を残したままマージされ、番号はブランチではなく `main` へのコミットで割り当てられる。承認後にブランチへ
push しない以上、失効は起きない。おまけに、契機は「承認 → auto-merge → リネーム」から、単一の
「`main` への push」へと縮む。

## 詳細設計

採番ロジック（[`scripts/allocate_roadmap_ids.py`](../../../scripts/allocate_roadmap_ids.py)）は
**そのまま** 再利用する。これはすでに、ワーキングツリー内の各 `BE-XXXX-<slug>/` プレースホルダを見つけ、
項目ごとに `max(used) + 1` を採番し（決定性のため slug 順）、ディレクトリとファイルを `git mv` し、ファイル
内のトークンを書き換え、索引の行を直す。変わるのは *どこで*・*いつ* 走るか――`main` に対して、マージ後に
――と、その周りのワークフロー配管だけである。

### 流れ

1. `ideation` スキルが項目を `BE-XXXX-<slug>` として書き起こす（従来どおり）。PR は `[BE-XXXX]` タイトルで
   開かれる。
2. レビュアーが `BE-XXXX` の内容をレビューして承認する。**ブランチ上では採番は起きない。**
3. auto-merge（または merge queue）がブランチを `BE-XXXX` を残した **そのまま** マージする。承認後に
   ブランチへコミットが push されないので、承認は失効しない。
4. マージは `main` への push である。`push: main` を契機とする `roadmap-id` ジョブが `main` に対して採番
   処理を走らせ、リネームと再生成した索引を `main` へ直接コミットし、（ベストエフォートで）マージ済み PR の
   タイトル `BE-XXXX` を `BE-NNNN` へ書き換える。

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
  pull-requests: write        # マージ済み PR のタイトルを書き換える
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

### フィジビリティ

設計を支える前提を、具体的に示す。

- **`main` 上の一時的な `BE-XXXX` でもゲートは緑のまま。** ロードマップ系の 3 つのツールはいずれも
  `^BE-(\d{4})-` で判定し、それ以外を読み飛ばす――
  [`tests/test_roadmap_format.py`](../../../tests/test_roadmap_format.py) と
  [`tests/test_roadmap_index.py`](../../../tests/test_roadmap_index.py)
  （後者は [`build_roadmap_index.py`](../../../scripts/build_roadmap_index.py) 経由で、`load_items` が
  番号なしディレクトリで `continue` する）、そして
  [`promote_roadmap_items.py`](../../../scripts/promote_roadmap_items.py)。したがって `BE-XXXX`
  ディレクトリは format チェックから見えず、索引の行を生まず（差分なし）、移動もされない。実地でも、
  このプレースホルダ項目をツリーに置いたまま `make check` が通る。よってマージコミットと採番コミットの間、
  `main` に **赤くなる窓は生じない**。
- **採番は構造的に連番・歯抜けなし。** `main` 上では採番処理の `used` 集合は `main` の番号付き項目だけ
  なので、項目がマージされる順に `max + 1` を払い出す。マージ順が採番順であり、却下された PR はマージ
  されないので番号を消費しない。動機で述べた歯抜けの源である「`max + 1` とマージ順の食い違い」が残らない。
- **同時マージは直列化され、複数項目のマージも扱える。** `concurrency: roadmap-id-main` を
  `cancel-in-progress: false` で使い、採番ランをキューに並べて、ほぼ同時の 2 マージを 1 つずつ採番する。
  1 つの push が複数のプレースホルダを運ぶ場合（2 項目を足す PR、あるいはジョブが走る前に 2 件がマージ
  された場合）はすでに対応済みで、`allocate()` は `placeholder_dirs()` を slug 順に回して連続した ID を
  割り当てる。push し返しは後続のマージと競合しうるが、上記の `fetch` ＋ `rebase` ＋ 再試行ループが
  整合させる。採番コミット自体は `roadmaps/**` を触るのでワークフローを再トリガーするが、その実行は
  プレースホルダを見つけられず no-op で終わる。
- **`main` への push が唯一の新しい前提。** いまは bot が *PR ブランチ* へリネームを push しているが、
  ここでは採番コミットを *保護された* `main` へ push しなければならない。これには `main` へ push を許され
  たトークンが要る――ワークフローを bypass リストに載せた GitHub App のインストールトークン（あるいは
  PAT）――。既定の `GITHUB_TOKEN` は通常 `main` の保護に阻まれるからである。これが唯一の真に新しいインフラ
  要件であり、直接 push しない代替案は *検討した代替案* に挙げる。
- **マージ済み PR のタイトル書き換えはベストエフォート。** `push` イベントは PR 番号を持たないので、ジョブは
  マージコミットから解決する――`gh api repos/{owner}/{repo}/commits/${SHA}/pulls`――うえで `gh pr edit`
  する。タイトルは見た目・履歴上のものなので、外しても無害である。

### BE-0061 の仕組みとの関係

`main` 上での採番は採番を 1 つのブランチに **直列化** するので、BE-0061 が塞いだ「同一窓内のレース」は
ほぼ意味を失う。`main` に触れる採番ランは同時に高々 1 つで、常に最新の `main` を読む。原子的な
`refs/be-claims/*` 台帳、[`roadmap-id-repair`](../../../.github/workflows/roadmap-id-repair.yml)、
`roadmap-claims-gc` は、このモデルでは概ね冗長になる。本項目のスコープを絞るため、推奨する方針は、これらを
多層防御として **そのまま残す**（害はない）ことであり、**claim／repair／gc の複雑さを退役させる** ことは、
マージ時採番が実証できたあとの任意のフォローアップとして扱う。

### prime directive への適合

開発インフラ（contributor workflow）にとどまる。どの経路にも LLM を足さず、`run` と CI は決定的なまま、
アプリ固有の事情がツール・ドライバ・ランナーへ移ることもない。
[BE-0043](../../implemented/BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)、
BE-0061、
[BE-0074](../../implemented/BE-0074-be-template-standardization/BE-0074-be-template-standardization.md)、
[BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md) と同じ系譜にある。

## 検討した代替案

- **承認時にリネームをブランチへ push して採番し、それから auto-merge する。** 本項目の初期形。却下。
  承認後の bot コミットが「古い承認の失効」を踏み、auto-merge を止める。これを避けるにはリポジトリ全体で
  失効を無効化する（鈍い）か、GitHub が確実にはカウントしない bot の再承認を使うしかない。マージ後に
  `main` で採番すれば、承認後の push が一切ないので、この問題は起こりえない。
- **PR オープン時採番のまま、`max + 1` を最小の空き番号に変える。** 変更はずっと小さく――契機の変更も
  `main` への push も要らず――却下された小さい番号の PR が残した穴も埋まる。主たる経路としては却下する。
  却下された PR のタイトルやレビュースレッドに既に現れた `[BE-00xx]` を別項目に再利用すると、その ID が
  履歴上 *曖昧* になる。これはまさに「番号を再利用しない」というルールが防ごうとしていることである。より
  軽い代替としては妥当。
- **`main` への直接 push ではなく、追従の auto-merge PR で採番する。** 小さな採番 PR を開くことで
  `main` の bypass トークン要件を避ける。既定としては却下。採番ごとに 2 本目の PR が増え、`BE-XXXX` が
  `main` に居座る窓が長くなる。`main` への直接 push が方針上禁じられている場合の自然なフォールバック。
- **歯抜けを無害として受け入れ、文書化して終える。** 最も安価。ID は設計上恒久かつ単調で、Swift-Evolution
  も歯抜けを許容する。`main` の採番を構造的に連番に保つという掲げた目的に照らして却下する。
- **BE-0061 の判断に手を付けない（何もしない）。** 却下。費やされた ID と非連番という懸念は、小さくとも
  実在し、BE-0061 のレース保証を一切再び開くことなく取り除ける。

## 参考

- [BE-0061 — Collision-proof BE-ID allocation](../../implemented/BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)
  ――本項目が拡張する項目。その「検討した代替案」が、本項目で実現する「マージ時に採番する」案を記録して
  おり、その堅牢化（原子的 claim、repair、claims-gc）はそのまま再利用するか、任意で後に退役させる。
- [`.github/workflows/roadmap-id.yml`](../../../.github/workflows/roadmap-id.yml)（契機を
  `pull_request` から `push: main` へ移す対象）、
  [`roadmap-id-repair.yml`](../../../.github/workflows/roadmap-id-repair.yml)、
  [`roadmap-claims-gc.yml`](../../../.github/workflows/roadmap-claims-gc.yml)――対象のワークフロー。
- [`scripts/allocate_roadmap_ids.py`](../../../scripts/allocate_roadmap_ids.py)（そのまま再利用）、
  [`scripts/build_roadmap_index.py`](../../../scripts/build_roadmap_index.py)、
  [`scripts/promote_roadmap_items.py`](../../../scripts/promote_roadmap_items.py)――いずれも `BE-XXXX`
  ディレクトリを読み飛ばす 3 つのツール。これが一時的な窓の間 `main` を緑に保つ。
- [`tests/test_roadmap_format.py`](../../../tests/test_roadmap_format.py)、
  [`tests/test_roadmap_index.py`](../../../tests/test_roadmap_index.py)――`^BE-(\d{4})-` で判定し、
  プレースホルダを無視するゲートテスト。
- [`CLAUDE.md`](../../../CLAUDE.md) ·
  [`roadmaps/README.md`](../../README.md) ·
  [`docs/ai-development.md`](../../../docs/ai-development.md)――番号を PR オープン時ではなくマージ後の
  `main` で採番するよう更新する作成ルール。
- GitHub ドキュメント――*Automatically merging a pull request*（auto-merge）と *Managing a merge queue*――
  `BE-XXXX` ブランチをそのままマージする標準機能。

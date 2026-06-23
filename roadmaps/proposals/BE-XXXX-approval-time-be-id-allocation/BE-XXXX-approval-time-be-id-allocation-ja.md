[English](BE-XXXX-approval-time-be-id-allocation.md) · **日本語**

# BE-XXXX — 承認時の BE ID 採番と自動マージ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-approval-time-be-id-allocation-ja.md) |
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

本項目は、採番のタイミングを PR オープン時から **PR の承認時（approve）** へ移し、それを **自動マージ
（auto-merge）** と組み合わせる。これにより `BE-NNNN` はレビュアーが受理した項目にだけ割り当てられ、その
直後に `main` へ着地する。項目は作成からレビューの間ずっと `BE-XXXX` プレースホルダのままで、PR が承認
された時点で実番号を採番し、必要なチェックが通り次第 auto-merge がブランチを取り込む。狙いは、`main`
上の `BE-NNNN` 列を **連番に保ち**、却下・放棄された提案に番号を浪費させないことである。

これは BE-0061 が検討のうえ見送った代替案――「実 ID はマージ時にだけ振り、レビュー中は `BE-XXXX` の
まま」――を再評価するものである。再評価を支えるのは 2 点で、その両方が BE-0061 の反対理由に答える。
1 つは、slug がレビュー中から各項目に安定した参照名を与えていること。もう 1 つは、GitHub 標準の
auto-merge（および merge queue）によって、採番を遅らせてから自動マージする流れが手作業なしで実用に
なったことである。本項目はあくまで開発インフラ（contributor workflow）にとどまる。どの経路にも LLM は
入らず、`run` と CI は決定的なまま、アプリ固有の事情がツールへ入り込むこともないので、prime directive の
いずれにも抵触しない。BE-0061 が採番を *衝突しない* ものにしたのに対し、本項目は採番を *受理を条件と
する* ものにする、その直系の続きである。

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
それを許容する――が、「BE-00xx って何だっけ」という混乱を招き、参照可能な番号を無駄にする。本項目は
この歯抜けを **構造的に** 取り除く。番号は受理された項目にしか渡らず、しかも直後に `main` へ着く。

### BE-0061 の 2 つの反対理由には、いまや答えがある

BE-0061 はこの案を 2 つの理由で見送った。そのどちらにも、今日では答えがある。

- **「レビュー中に参照できる安定した ID が失われる」。** 安定した参照名は ID だけではない。**slug** は項目
  ごとに一意で恒久であり、採番処理はすでにすべての操作を slug を軸に行っている（ディレクトリ
  `BE-XXXX-<slug>`、索引の行、相互参照の書き換え）。レビュー中、項目は `BE-XXXX-<slug>` として参照され、
  PR タイトルも `[BE-XXXX]` 接頭辞を保つ。*番号* に意味が生じるのは項目が受理されたときであり、それは
  まさに番号を割り当てるようになる時点である。レビュアーが参照すべきものは何も失われず、ただ尚早な番号が
  後ろ倒しになるだけである。
- **「素の GitHub では merge queue なしにマージ済みツリーを書き換えられず扱いづらい」。** これはもはや
  障害ではない。採番は依然として **マージ前**（承認時のブランチ上）で行われ、その後 GitHub 標準の
  **auto-merge** がチェックの通過後にブランチをマージする。*マージ済み* のツリーを書き換えることは一切なく
  ――ブランチを書き換えてからマージするだけなので――この反対理由の前提が成り立たない。

## 詳細設計

変更はワークフロー層と作成ルールの文書に限られる。採番ロジック
（[`scripts/allocate_roadmap_ids.py`](../../../scripts/allocate_roadmap_ids.py)）と BE-0061 由来の
レース対策一式はそのまま再利用する。変わるのは採番が走る *タイミング* と、その直後に起きることだけである。

### 契機：オープン時ではなく承認時に採番する

`roadmap-id` の契機を `pull_request`（opened / synchronize）から、`pull_request_review` の
`types: [submitted]` へ移し、`github.event.review.state == 'approved'` で絞り込む（bot は fork のブランチへ
push できないため、既存の `head.repo.full_name == github.repository` という同一リポジトリ判定は残す）。
ジョブ本体は現状と同じで、開いている PR と claim 台帳から予約集合を組み立て、採番し、再試行ループで各 ID を
原子的に claim し、索引を再生成し、コミットしてリネームをブランチへ push し、PR タイトルの `BE-XXXX` を
実 ID へ書き換える。

作成からレビューの間、項目は `BE-XXXX-<slug>` のままで、レビュアーが目にするのはプレースホルダである。

### auto-merge：連番のまま着地させる

リネームを push し、タイトルを書き換えたあと、ワークフローはその PR に auto-merge を有効化する
（リポジトリのマージ戦略で `gh pr merge --auto`）。あとは採番コミット上で必要なチェックが通り次第、PR は
自動でマージされる。採番が承認時――マージの直前――に行われるようになるため、「番号の割り当て」から
「番号が `main` に載る」までの窓はほぼゼロに縮まり、前述の「マージ順の食い違いによる歯抜け」は事実上
開きえなくなる。

### 承認後コミットと stale-review の失効（核心）

承認の *あと* に採番するということは、採番処理が承認済みの HEAD の上に github-actions[bot] のコミットを
push するということである。ブランチ保護の「新しいコミットが push されたら古い承認を失効させる」設定の下では、
この bot コミットが人間の承認を失効させ、auto-merge を止めてしまう。これが本設計の核心的な引っかかりである。
選択肢を、推奨とともに挙げる。

1. **auto-merge ＋ 古い承認の失効を無効化（推奨）。** 承認後に自動で push されるコミットは、採番処理による
   範囲の限られたリネーム（ディレクトリ／ファイルの移動、ファイル内の `BE-XXXX` → `BE-NNNN`、索引の再生成）
   だけである。これをレビューの無効化とみなさない設定にすれば auto-merge は進める。トレードオフ――承認後の
   あらゆる push が承認を保持する――は、本リポジトリの同一リポジトリ `claude/*` ／ `<user>/<topic>` の流儀
   では許容でき、意図した方針として文書化する。
2. **bot による再承認。** push 後にワークフローが bot トークンで承認レビューを投じる。これは部分的である。
   `GITHUB_TOKEN` のレビューは必須の *人間* ／ CODEOWNER 承認を満たさず、GitHub の ruleset は現状コミット
   作成者による失効除外をできないので、bot で必須承認数を満たせる場合にしか効かない。
3. **merge queue の中で採番する。** 却下。merge queue はブランチを *そのまま* マージし、内容の書き換えを
   差し込めないので、そこで採番を走らせることはできない。

推奨する配線は選択肢 1（auto-merge ＋ 古い承認を失効させない）である。具体的なブランチ保護／ruleset の設定は
メンテナが選ぶ保護構成に依存するため **TBD** とする。上記のワークフロー変更は、どの選択肢を採るかとは
独立に成立する。

### 据え置く部分（多層防御）

BE-0061 の堅牢化はそのまま保つ。原子的な `refs/be-claims/*` claim、
[`roadmap-id-repair`](../../../.github/workflows/roadmap-id-repair.yml) のバックストップ、
[`roadmap-claims-gc`](../../../.github/workflows/roadmap-claims-gc.yml) である。承認時採番では、実番号を
同時に保持する PR が大きく減るので、同一窓内のレースはより稀になる――が、claim 台帳と repair は多層防御
として残し、`roadmap-claims-gc` のクローズ時解放は、承認からマージの間に閉じられた PR の claim を引き続き
回収する。`allocate_roadmap_ids.py` は純粋なまま（予約集合は環境変数経由、GitHub への呼び出しなし）である。

### 作成ルールと文書の更新

[`CLAUDE.md`](../../../CLAUDE.md)、[`roadmaps/README.md`](../../README.md) ／
[`README-ja.md`](../../README-ja.md)、[`docs/ai-development.md`](../../../docs/ai-development.md) の作成
ルールを更新し、項目――およびその PR タイトル――は **レビュー中ずっと** `BE-XXXX` プレースホルダを保ち、
実 ID は（オープン時ではなく）承認時に採番される、と明記する。`ideation` スキルはすでに `BE-XXXX` で項目を
書き起こすので、その作成フロー自体は変わらない。更新するのは、CI が *いつ* 番号を書き換えるかの注記だけ
である。

### prime directive への適合

開発インフラ（contributor workflow）にとどまる。どの経路にも LLM を足さず、`run` と CI は決定的なまま、
アプリ固有の事情がツール・ドライバ・ランナーへ移ることもない。
[BE-0043](../../implemented/BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow.md)、
BE-0061、
[BE-0074](../../implemented/BE-0074-be-template-standardization/BE-0074-be-template-standardization.md)、
[BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders.md) と同じ系譜にある。

## 検討した代替案

- **PR オープン時採番のまま、`max + 1` を最小の空き番号に変える。** 変更はずっと小さく――契機の変更も
  auto-merge も要らず、レビュー中の番号も保たれる――却下された小さい番号の PR が残した穴も埋まる。主たる
  経路としては却下する。却下された PR のタイトルやレビュースレッドに既に現れた `[BE-00xx]` を別項目に再利用
  すると、その ID が履歴上 *曖昧* になる。これはまさに「番号を再利用しない」というルールが防ごうとしている
  ことである。承認時採番が過大だと判断される場合の、より軽い代替としては妥当である。
- **歯抜けを無害として受け入れ、文書化して終える。** 最も安価。ID は設計上恒久かつ単調で、Swift-Evolution
  も歯抜けを許容するので、却下された提案による穴は擁護できる。`main` の採番を構造的に連番に保つという掲げた
  目的に照らして却下する。
- **マージ時に merge queue の書き換えで採番する。** 素の GitHub では不可能。merge queue はブランチをそのまま
  マージし、内容を書き換えられないので、番号をキューで差し込めない（これは BE-0061 が「merge queue なしでは
  扱いづらい」と呼んだ形そのものであり、承認時のマージ前採番はこれを回避する）。
- **BE-0061 の判断に手を付けない（何もしない）。** 却下。費やされた ID と非連番という懸念は、小さくとも実在
  し、BE-0061 のレース保証を一切再び開くことなく取り除ける。

## 参考

- [BE-0061 — Collision-proof BE-ID allocation](../../implemented/BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening.md)
  ――本項目が拡張する項目。その「検討した代替案」が、本項目で再評価する「マージ時に採番する」案を記録して
  おり、その堅牢化（原子的 claim、repair、claims-gc）をそのまま再利用する。
- [`.github/workflows/roadmap-id.yml`](../../../.github/workflows/roadmap-id.yml)（契機を承認時へ移す対象）、
  [`roadmap-id-repair.yml`](../../../.github/workflows/roadmap-id-repair.yml)、
  [`roadmap-claims-gc.yml`](../../../.github/workflows/roadmap-claims-gc.yml)――対象のワークフロー。
- [`scripts/allocate_roadmap_ids.py`](../../../scripts/allocate_roadmap_ids.py)（そのまま再利用）、
  [`scripts/be_claims.sh`](../../../scripts/be_claims.sh)（claim 台帳）。
- [`CLAUDE.md`](../../../CLAUDE.md) ·
  [`roadmaps/README.md`](../../README.md) ·
  [`docs/ai-development.md`](../../../docs/ai-development.md)――番号を承認時へ後ろ倒しするよう更新する作成ルール。
- GitHub ドキュメント――*Automatically merging a pull request*（auto-merge）と *Managing a merge queue*――
  本項目が依拠する標準機能。

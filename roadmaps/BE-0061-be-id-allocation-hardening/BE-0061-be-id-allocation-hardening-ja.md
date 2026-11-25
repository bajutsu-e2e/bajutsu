[English](BE-0061-be-id-allocation-hardening.md) · **日本語**

# BE-0061 — 衝突しない BE ID 採番（原子的な予約と自動修復）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0061](BE-0061-be-id-allocation-hardening-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0061") |
| 実装 PR | [#175](https://github.com/bajutsu-e2e/bajutsu/pull/175)、[#436](https://github.com/bajutsu-e2e/bajutsu/pull/436)（撤去） |
| トピック | コントリビューターワークフロー |
<!-- /BE-METADATA -->

## はじめに

ロードマップの各項目は、永続的で単調に増える `BE-NNNN` の ID を持ちます。この ID は、ideation スキルが残した `BE-XXXX` プレースホルダから、[`roadmap-id`](../../.github/workflows/roadmap-id.yml) ワークフローが PR の時点で割り当てます。書き手が番号を当て推量することはありません。

従来の採番器は、`origin/main` にすでにある ID を避けていました。ベストエフォートで、他の open な PR の ID も避けていました。`ROADMAP_RESERVED_IDS` として渡されるリストを使います。加えて、`roadmap-id-repair` ワークフローが、ロードマップ PR の merge 後に、*`main` に対する*衝突を修復していました。

しかし、穴が 2 つ残っていました。

- **同じ時間帯**に採番する 2 つの PR が、なお同じ番号を取れました。プレースホルダには数字がなく、まだ割り当てられていない ID は、予約リストから見えないからです。
- **open な PR どうしの間だけ**で争われ、どちらも merge されていない番号には、裁定者がいませんでした。修復が拠り所にできるのは `main` だけなので、この場合は一度も発火しませんでした。

`BE-0056` を 3 つの open な PR が同時に持っていた件（#166、#169、#170。2026-06-21 に手作業で修復）は、この穴が生む失敗そのものです。

本項目は、この 2 つの穴を塞ぎます。採番は、各 ID を `refs/be-claims/*` という git ref として**原子的に確保**します。同じ時間帯の 2 つのブランチが、同じ番号を両取りすることはなくなります。修復は、それでもすり抜けるものへの**最後の砦**になります。手打ちした ID や、この仕組みより前から存在するブランチです。修復の権威は、`main` を第一とします。`main` になければ、その番号を持つ **open な PR のうち最小の番号**を権威とします。修復は、merge 時に加えて、定期スケジュールでも走ります。

本項目は、純粋にコントリビューター向けの基盤です。ツールの挙動、実行時、シナリオの意味論は変わらず、決定的なゲートにも手は入りません。[BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md) の直接の姉妹にあたります。BE-0043 は*ロードマップのファイル*をコンフリクトに強くしました。本項目は、その *ID* を衝突しないものにします。

**追記：** 以下で説明する予約台帳と自動修復のバックストップは、その後撤去しました。[BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation-ja.md) が、採番を `main` 上のマージ時へ移したためです。マージ順に直列化された採番は、最新の `main` を読み、番号を一度に 1 つずつ渡します。2 つのブランチが同じ番号を争うことはなくなり、台帳とその修復は不要になりました。残るのは 2 つです。純粋な採番器（`allocate_roadmap_ids.py`。現在は採番のみ）と、マージ時の `roadmap-id` ワークフローです。

## 動機

- **同じ時間帯の採番レースが、開いたままでした。** `ROADMAP_RESERVED_IDS` は、他の open な PR のファイル、つまりその `BE-NNNN` ディレクトリから組み立てられます。`BE-XXXX` プレースホルダのままの PR は、数字を一切寄与しません。`roadmap-id` の実行が重なった 2 つの PR は、互いを番号なしと見なし、どちらも `max + 1` を選びます。予約はレースを狭めましたが、塞ぎはしませんでした。
- **open な PR どうしの衝突には、修復の手段がありませんでした。** `roadmap-id-repair` は、完全に `origin/main` を鍵にしていました。*merge 済み*の項目がいま持つ番号について、その番号を持つ別の項目を採番し直すものです。複数の open な PR が番号を共有し、どれも merge されていなければ、権威となるものがないので、修復は何もしません。`BE-0056` の 3 つ巴の衝突が、まさにこの場合で、手作業で解決するしかありませんでした。
- **手打ちした具体的な ID は、採番を完全に迂回します。** `BE-XXXX` プレースホルダではなく、リテラルの `BE-NNNN` でコミットされた項目は、採番器を一度も通りません。予約も merge 時の修復も、そうした項目が重複させる番号を捕まえられません。

## 詳細設計

### `refs/be-claims/*` 台帳による原子的な予約

claim は、`refs/be-claims/<NNNN>` という名前の git ref です。GitHub の create-ref API（`POST /repos/{owner}/{repo}/git/refs`）は、ref がすでに存在すると `422` を返します。そのため、この API は compare-and-set として働きます。番号を最初に claim した PR が勝ち、2 番目の PR は `422` を受けて選び直します。ヘルパ `scripts/be_claims.sh` が、この API と `git ls-remote` の上に、`list`、`claim`、`release` を被せていました。

`roadmap-id` ワークフローは、claim の台帳を予約集合に畳み込み、採番し、割り当てた各 ID を原子的に claim します。別の PR が先んじて claim を取り損ねたら、勝ち取った ID を release し、状態を戻して、やり直します。勝った側の claim はもう見えているので、次の試行はそれを避けて進みます。

採番は、同じ単調規則を保ちます。`max(used) + 1` で、予約済みと claim 済みの番号は飛ばします。[`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py) は**純粋なまま**です。予約集合を環境変数から受け取り、GitHub とは一切やり取りしません。API 呼び出しとリトライループは、ワークフロー側に置きます。既存の予約と、まったく同じ構えです。

### claim のライフサイクル

claim が役に立つのは、*open* な PR がその ID を持ち、かつ ID が*まだ `main` にない*あいだだけです。`roadmap-claims-gc.yml` の 2 つのトリガが、それを徹底していました。

- **close 時の release。** ロードマップ PR が、merge されたか否かを問わず close されると、その PR が導入した claim を落とします。merge された ID はいま `main` にあるので、claim は冗長です。放棄された ID は解放されます。
- **日次のスイープ。** スケジュールされたジョブが、ID が `main` にあるか、どの open な PR も持っていない claim をすべて落とします。たとえば、キャンセルされた採番実行が漏らしたものを回収します。

### 修復の権威は `main`、なければ open な PR の最小番号

`allocate_roadmap_ids.py --repair` は、ブランチが*導入する*項目（slug がまだ `main` にない項目）の番号がすでに取られているとき、その項目を採番し直します。争われた番号を誰が保つかを決める権威は、「`main` のみ」から、次の 2 つの規則に一般化します。上から順に試します。

1. *別の*項目がその番号をすでに持っているなら、`origin/main`。merge された項目が常に勝ちます。
2. そうでなければ、その番号を持つ **open な PR のうち最小の番号**。ワークフローが `scripts/open_pr_be_map.sh` から算出し、`ROADMAP_LOWER_PR_IDS` で渡します。

ブランチが採番し直すのは、自身が権威で*ない*ときです。slug がすでに `main` にある項目は、ブランチが引き継いだものです。この場合は rebase で解消し、採番し直しません。

`roadmap-id-repair` ワークフローは、`main` への push 時に加えて、**日次のスケジュール**でも走ります。何も merge されなくても、open な PR どうしの衝突を捕まえられます。スイープは、新しく割り当てた各 ID を予約します。claim したうえで、その実行の残りの予約にも畳み込むので、敗者どうしが常に別々の番号に着地します。争われた番号の claim は、権威の側に残ります。

### fork からの PR

bot のトークンは、fork のブランチに push することも、このリポジトリに ref を作ることもできません。そのため、両方のワークフローは、同一リポジトリの PR にだけ作用します。既存の `head.repo.full_name == github.repository` のガードを使います。本プロジェクトの PR は、同一リポジトリの `claude/*` や `<user>/<topic>` のブランチなので、これが通常の経路です。fork のコントリビューターの ID は、メンテナがそのブランチを同一リポジトリに取り込むときに整えます。

## 検討した代替案

- **claim ではなく、グローバルな `concurrency` グループで採番を直列化する。** より安価ですが、2 点で不十分です。GitHub は、concurrency グループごとに *pending* な実行を 1 つしか保持せず、古い pending をキャンセルします。同時に開かれた 3 つの PR では、採番が静かに 1 つ落ちることがあります。さらに、直列化は、open な PR どうしの衝突に権威を与えません。原子的な claim は無条件に成り立ち、直列化を必要としません。
- **本物の ID を merge 時にだけ割り当て、レビュー中は `BE-XXXX` のままにする。** `main` 上で直列化することで、レースはなくなります。ただし、レビュー中に安定して参照できる ID が失われます。このリポジトリは PR タイトルに ID を前置し、レビュアーもそれを引用するからです。素の GitHub では、マージキューなしに merge 済みのツリーを書き換えられないので、扱いも厄介です。
- **カウンタを使わない ID 方式（コンテンツハッシュ、ULID、著者ごとの番号帯）に切り替える。** 却下しました。ロードマップは、Swift-Evolution の流儀に倣い、永続的で、人が引用でき、単調に増える `BE-NNNN` の ID の上に成り立っています。方式の変更は、読み手に何の益もないまま、大きく後戻りのできない揺らぎを生みます。
- **失敗するチェックで衝突を検出し、手作業で修復する。** 赤く目立つチェックは、より弱い砦です。すり抜けるたびに、手作業の手間が増えます。自動修復は、介入なしに衝突を直します。意図的に手で直すための、ローカルの `make roadmap-id-repair` ターゲットも用意していました。
- **まず提案として書き起こす（`BE-XXXX` プレースホルダ）。** ここでは不要です。作業は同じ変更の中で実装されているので、*コントリビューターワークフロー*の下に、BE-0043 の姉妹として、最初から実装済みで提出します。リポジトリがすでに使っている、実装済みで生まれる経路です。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。
- [x] マージ時採番（BE-0089）のもとで冗長になった予約台帳と自動修復を撤去。`scripts/be_claims.sh`、
  `scripts/open_pr_be_map.sh`、`scripts/open_pr_be_ids.sh`、`roadmap-id-repair` と `roadmap-claims-gc`
  のワークフロー、採番器の `--repair` 経路を削除しました。

## 参考

- [BE-0043 — コンフリクトに強いファイル流動](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md)：ファイルから ID へと延長した、本項目の姉妹（自己修復するフック、生成される索引、マージドライバ）。
- [CLAUDE.md](../../CLAUDE.md)、[docs/ai-development.md](../../docs/ja/ai-development.md)：本項目が固める、ロードマップ ID のルールと、採番と修復の流れ。
- [`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py)（採番）、[`tests/test_allocate_roadmap_ids.py`](../../tests/test_allocate_roadmap_ids.py)。claim の台帳（`scripts/be_claims.sh`）と、open な PR のタイブレーク入力（`scripts/open_pr_be_map.sh`）は、予約層の撤去に伴い削除しました（*進捗*を参照）。
- [`.github/workflows/roadmap-id.yml`](../../.github/workflows/roadmap-id.yml)：マージ時の採番器。`roadmap-id-repair` と `roadmap-claims-gc` のワークフローは、予約層とともに削除しました。

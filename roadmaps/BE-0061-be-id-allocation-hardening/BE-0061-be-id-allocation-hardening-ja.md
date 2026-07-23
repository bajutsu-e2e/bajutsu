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

ロードマップの各項目は、永続的で単調増加する `BE-NNNN` の ID を持ちます。この ID は、ideation スキルが
残した `BE-XXXX` プレースホルダから [`roadmap-id`](../../.github/workflows/roadmap-id.yml)
ワークフローが PR の時点で割り当てるので、書き手が番号を当て推量することはありません。従来の採番器は
`origin/main` にすでにある ID を避け、さらに（ベストエフォートで）他の open な PR の ID も避けていました
（`ROADMAP_RESERVED_IDS` で渡されるリスト）。加えて `roadmap-id-repair` ワークフローが、ロードマップ PR の
merge 後に *`main` に対する* 衝突を修復していました。

しかし穴が 2 つ残っていました。**同じ時間帯に**採番する 2 つの PR が、なお同じ番号を取り得ました。
プレースホルダには数字が無く、まだ割り当てられていない ID は予約リストからは見えないからです。そして、
**open な PR どうしの間でだけ**争われ、どちらも merge されていない番号には、裁定者がいませんでした。修復が
拠り所にできるのは `main` だけで、この場合は一度も発火しないからです。`BE-0056` を 3 つの PR が同時に
持っていた件（#166 / #169 / #170、2026-06-21 に手作業で修復）は、この穴が生む失敗そのものです。

本項目はこの両方を塞ぎます。採番は各 ID を `refs/be-claims/*` という git ref として**原子的に確保**する
ようになり、同じ時間帯の 2 つのブランチが同じ番号を両取りすることはなくなります。修復は、それでもすり抜ける
もの（手打ちした ID、この仕組みより前から存在するブランチ）への**最後の砦**として一般化されます。その権威は
`main` を第一とし、無ければその番号を持つ **open な PR のうち最小の番号** とし、merge 時だけでなく定期
スケジュールでも走ります。これは純粋にコントリビュータ向けの基盤であり、ツールの挙動、実行時、シナリオの
意味論はどれも変わらず、決定的なゲートにも手は入りません。本項目は
[BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md) の直接の姉妹です。
あちらは *ロードマップのファイル* をコンフリクトに強くしました。本項目はその *ID* を衝突しないものにします。

**追記**：以下で説明する予約台帳と自動修復のバックストップは、その後撤去しました。
[BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation-ja.md) が採番を
`main` 上のマージ時へ移したためです。マージ順に直列化された採番は最新の `main` を読み、番号を一度に
1 つずつ渡すので、二つのブランチが同じ番号を争うことはなくなり、台帳とその修復は不要になりました。いまは
純粋な採番器（`allocate_roadmap_ids.py`、現在は採番のみ）とマージ時の `roadmap-id` ワークフローだけが残ります。

## 動機

- **同じ時間帯の採番レースがなお開いたままでした。** `ROADMAP_RESERVED_IDS` は他の open な PR のファイル
  （その `BE-NNNN` ディレクトリ）から組み立てられます。`BE-XXXX` プレースホルダのままの PR は数字を一切
  寄与しないので、`roadmap-id` の実行が重なった 2 つの PR は互いを番号無しと見なし、どちらも `max + 1` を
  選びます。予約はレースを狭めましたが、塞ぎはしませんでした。
- **open な PR どうしの衝突には修復手段がありませんでした。** `roadmap-id-repair` は完全に `origin/main`
  を鍵にしており、*merge 済み* の項目がいま持つ番号の項目を採番し直すものでした。複数の open な PR が番号を
  共有し、どれも merge されていなければ、権威となるものが無いので修復は何もしません。これがまさに
  `BE-0056` の 3 つ巴の衝突で、手作業での解決を強いられました。
- **手打ちした具体的な ID は採番を完全に迂回します。** （`BE-XXXX` プレースホルダではなく）リテラルの
  `BE-NNNN` でコミットされた項目は採番器を一度も通らないので、予約も merge 時の修復も、それが重複させる
  番号を捕まえられません。

## 詳細設計

### `refs/be-claims/*` 台帳による原子的な予約

claim は `refs/be-claims/<NNNN>` という名前の git ref です。GitHub の create-ref API
（`POST /repos/{owner}/{repo}/git/refs`）は ref がすでに存在すると `422` を返すので、これが
compare-and-set として働きます。番号を最初に claim した PR が勝ち、2 番目は `422` を受けて選び直します。
ヘルパ `scripts/be_claims.sh` が、この API と `git ls-remote` の上に
`list` / `claim` / `release` を被せていました。

`roadmap-id` ワークフローは、claim の台帳を予約集合に畳み込み、採番し、その後で割り当てた各 ID を原子的に
claim します。claim を取り損ねたら（別の PR が先んじたら）、勝ち取った ID があれば release し、状態を戻して
やり直します。勝った側の claim はもう見えているので、次の試行はそれを避けて進みます。採番は同じ単調規則
（`max(used) + 1`、予約済みと claim 済みの番号は飛ばす）を保ちます。
[`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py) は**純粋なまま**で、予約集合を
環境変数から受け取り、GitHub とは一切やり取りしません。API 呼び出しとリトライループはワークフロー側に置きます。
既存の予約とまったく同じ構えです。

### claim のライフサイクル

claim が役に立つのは、*open* な PR がその ID を持ち、かつそれが *まだ `main` に無い* あいだだけです。
`roadmap-claims-gc.yml` の 2 つのトリガがそれを徹底していました。

- **close 時の release**：ロードマップ PR が（merge されたか否かを問わず）close されると、その PR が導入した
  claim は落とされます。merge された ID はいま `main` にあり（claim は冗長）、放棄された ID は解放されます。
- **日次のスイープ**：スケジュールされたジョブが、ID が `main` にあるか、どの open な PR も持っていない
  claim をすべて落とします。たとえばキャンセルされた採番実行が漏らしたものを回収します。

### 修復の権威は `main`、無ければ open な PR の最小番号

`allocate_roadmap_ids.py --repair` は、ブランチが *導入する* 項目（slug がまだ `main` に無い項目）で番号が
すでに取られているものを採番し直します。権威（争われた番号を誰が保つか）は「`main` のみ」から次のように
一般化します。

1. *別の*項目がそこで番号をすでに持っているなら `origin/main`（merge された項目が常に勝つ）。
2. そうでなければ、その番号を持つ **open な PR のうち最小の番号**。`ROADMAP_LOWER_PR_IDS` で渡されます
   （`scripts/open_pr_be_map.sh` からワークフローが算出）。

ブランチが採番し直すのは、自身が権威で *ない* ときだけです。slug がすでに `main` にある項目は、ブランチが
引き継いだものです。これは rebase で解消するもので、採番し直しません。`roadmap-id-repair` ワークフローは、
`main` への push 時に加えて**日次のスケジュール**でも走るようになり、何も merge されなくても open な PR
どうしの衝突を捕まえます。スイープは新しく割り当てた各 ID を予約し（claim し、その実行の残りの予約にも
畳み込み）、敗者どうしが常に別々の番号に着地するようにします。争われた番号の claim は権威の側に残ります。

### fork からの PR

bot のトークンは fork のブランチに push することも、このリポジトリに ref を作ることもできないので、両方の
ワークフローは同一リポジトリの PR にのみ作用します（既存の `head.repo.full_name == github.repository` の
ガード）。本プロジェクトの PR は同一リポジトリの `claude/*` / `<user>/<topic>` ブランチなので、これが通常の
経路です。fork のコントリビュータの ID は、メンテナがそのブランチを同一リポジトリに取り込むときに整えられます。

## 検討した代替案

- **claim ではなく、グローバルな `concurrency` グループで採番を直列化する。** より安価ですが、2 点で
  不十分です。GitHub は concurrency グループごとに *pending* な実行を 1 つしか保持せず、古い pending を
  キャンセルするので、同時に開かれた 3 つの PR では採番が静かに 1 つ落ちることがあります。さらに直列化は、
  open な PR どうしの衝突に権威を与えません。原子的な claim は無条件に成り立ち、直列化を要しません。
- **本物の ID を merge 時にだけ割り当て、レビュー中は `BE-XXXX` のままにする。** `main` 上で直列化する
  ことでレースは無くなりますが、レビュー中に安定して参照できる ID が失われます。このリポジトリは PR
  タイトルに ID を前置し、レビュアーもそれを引用します。素の GitHub では、マージキューなしに merge 済みの
  ツリーを書き換えられないので、扱いも厄介です。
- **カウンタを使わない ID 方式に切り替える**（コンテンツハッシュ、ULID、著者ごとの番号帯）。却下しました。
  ロードマップは Swift-Evolution の流儀に倣い、永続的で人が引用でき単調増加する `BE-NNNN` の ID の上に
  成り立っています。方式の変更は、読み手に何の益も無いのに、大きく後戻りのできない揺らぎを生みます。
- **失敗するチェックで衝突を検出し、修復は手作業のみとする。** 赤く目立つチェックは、すり抜けるたびに手作業
  の手間を足す、より弱い砦です。自動修復は介入なしにそれを直します。意図的に手で直すための、ローカルの
  `make roadmap-id-repair` ターゲットも用意していました。
- **まず提案として書き起こす（`BE-XXXX` プレースホルダ）。** ここでは不要です。作業は同じ変更の中で実装
  されているので、*コントリビューターワークフロー* の下に BE-0043 の姉妹として、最初から実装済みとして提出します。リポジトリが
  すでに使っている、実装済みで生まれる経路です。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。
- [x] マージ時採番（BE-0089）のもとで冗長になった予約台帳と自動修復を撤去。`scripts/be_claims.sh`、
  `scripts/open_pr_be_map.sh`、`scripts/open_pr_be_ids.sh`、`roadmap-id-repair` と `roadmap-claims-gc`
  のワークフロー、採番器の `--repair` 経路を削除しました。

## 参考

- [BE-0043 — コンフリクトに強いファイル流動](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md)
  — ファイルから ID へと延長した本項目の姉妹（自己修復するフック、生成される索引、マージドライバ）。
- [CLAUDE.md](../../CLAUDE.md) · [docs/ai-development.md](../../docs/ja/ai-development.md) — 本項目が
  固めるロードマップ ID のルールと、採番と修復の流れ。
- [`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py)（採番）、
  [`tests/test_allocate_roadmap_ids.py`](../../tests/test_allocate_roadmap_ids.py)。claim の台帳
  （`scripts/be_claims.sh`）と open な PR のタイブレーク入力（`scripts/open_pr_be_map.sh`）は、予約層の
  撤去に伴い削除しました（*進捗* を参照）。
- [`.github/workflows/roadmap-id.yml`](../../.github/workflows/roadmap-id.yml)（マージ時の採番器）。
  `roadmap-id-repair` と `roadmap-claims-gc` のワークフローは、予約層とともに削除しました。

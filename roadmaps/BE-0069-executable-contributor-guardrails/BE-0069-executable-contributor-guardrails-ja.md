[English](BE-0069-executable-contributor-guardrails.md) · **日本語**

# BE-0069 — コントリビュータ向けガードレールの実行可能化（手順をコマンドに）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0069](BE-0069-executable-contributor-guardrails-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0069") |
| 実装 PR | [#243](https://github.com/bajutsu-e2e/bajutsu/pull/243) |
| トピック | コントリビューターワークフロー |
<!-- /BE-METADATA -->

## はじめに

いまは**散文**としてしか存在しないコントリビュータ向けの手順（[`CLAUDE.md`](../../CLAUDE.md)、
`ideation` / `implement-be` の skill、[`docs/ja/ai-development.md`](../../docs/ja/ai-development.md)
に書かれているもの）を、**人間が実行できるコマンド**（小さなスクリプトを背後に持つ `make`
ターゲット）へ昇格させます。そうすれば、同じ入口を、作業を始める人間と、その人間に代わって作業する
AI の双方が使えます。ガードレールは「散文を読んだエージェントだけが確実にたどれる手順」ではなくなり、
誰でも実行できる一つのコマンドになります。本項目は開発者向けの基盤だけを変えます。LLM は足さず、
`run` の中で動くことはなく、決定的なゲートにも触れません。prime directive（[CLAUDE.md](../../CLAUDE.md)）は
構造上保たれます。

これは *コントリビューターワークフロー* の系譜の続きです。
[BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md)
は手で編集していた**共有台帳**（roadmap の索引）を生成物に変え、
[BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening-ja.md)
は **CI を `make check` の構造上の写し**にして、いつのまにか乖離していた重複を取り除きました。
どちらも一つの原則（書き写すのではなく*実行される*単一の情報源）を適用しています。本項目は、
その原則を**手順そのもの**に適用します。

## 動機

Bajutsu のガードレールは、人間が実行できるかどうかで、すでに二種類に分かれています。

- **実行できるもの**：人間も AI も同じように叩く一つのコマンド。[`make check`](../../Makefile)
  （ゲート）、[`make setup` / `make hooks`](../../Makefile)（git 設定の自己修復）、
  [`make roadmap-index` / `roadmap-promote` / `roadmap-id-repair`](../../Makefile)。コマンドそのものが
  ガードレールであり、忘れたり、うろ覚えになったりしません。
- **散文だけのもの**：読み手が手でたどるために書かれた、複数手順のレシピ。
  - **新しい roadmap 項目の雛形づくり**：ディレクトリと、*両方*の言語のファイルを、決まった
    Swift-Evolution 形式で作り、メタデータブロックを揃え、`BE-0069` プレースホルダを置き、author の
    GitHub ハンドルを書きます。`ideation` skill はその大半をこの説明に割いており、リポジトリの中で最も
    間違えやすく、最も手数の多い手順です。それなのにコマンドがありません。
  - **worktree とブランチの用意**：[`docs/ja/ai-development.md`](../../docs/ja/ai-development.md)
    にある `git fetch origin && git worktree add … -b … origin/main && make setup` というレシピ。
  - **push 前の手順**：`git fetch origin && git rebase origin/main && make check`。加えて「完了の定義」
    の確認（日英ドキュメントの両方に手を入れたか、振る舞いの変更にテストが伴うか、出荷するなら
    `Status` を切り替えたか）。
  - **項目が整形式か**：日英の対が揃っているか、slug がディレクトリ名と一致するか、メタデータ
    ブロックが揃っているか、author がハンドルのリンクか、`Status` がサブディレクトリと整合するか、
    参照している `BE-NNNN` が実在するか。いまこれを担保しているのは、二つの狭いテスト（索引のずれ、
    `Status` とディレクトリの整合）と、レビュアーの目だけです。

散文のままにしておくと、三つの問題が生じます。

1. **散文は現実から乖離する。** BE-0067 は、CI が `make check` から*すでに*乖離していたことを
   見つけました。読まれるだけで実行されないレシピは、同じように腐ります。コマンドは実行され、
   不変条件を守る場面ではテストされるので、正しさが保たれます。
2. **長い散文のレシピを確実に実行できるのは AI だけ。** `ideation` skill を読み込んでいない人間が、
   BE 項目を手で書くとき、形式、プレースホルダの規則、索引との関わりを正しく再現できるとは限りません。
   これこそ、本項目が取り除く「AI だのみ」の依存です。
3. **非対称が逆さま。** 最も手数が多く間違えやすい手順（項目の雛形づくり）にはコマンドが*なく*、
   日常の確認（`make check`）はキー一つで済みます。人間が最も助けを必要とするガードレールほど、
   人間には使えない状態です。

本項目を動機づける狙い（*「AI に任せる」から「人間が始め、AI が代替する」へ移る*）は、まさに
この昇格です。ガードレールがコマンドになれば、**入口を人間が持ち**、AI は手順の唯一の番人ではなく、
人間が実行できるワークフローの*中で*代わりを務める存在になります。

## 詳細設計

**原則。** それぞれの散文の手順を、[`scripts/`](../../scripts/) の小さなスクリプトを背後に持つ
`make <verb>` ターゲットにします。roadmap のファイルを操作するものは Python（型を付ける。BE-0067 以降
`mypy` は `scripts/` も対象）、git の配管にあたるものは shell（`make lint-sh` が見る `SHELL_SCRIPTS`
の一覧に加える）と、既存の分け方に合わせます。ターゲットが単一の情報源になり、`CLAUDE.md`、skill、
ドキュメントは、手順を書き写す代わりに**そのコマンドを指す**ようにします。BE-0067 が CI を Makefile
経由にしたときと同じ分け方です。確認できる不変条件を生む手順では、その検査を `make check` に組み込みます。

レバレッジの大きい順に、四つのメカニズムです。

### A. roadmap 項目の雛形（`make new-roadmap-item`）

`make new-roadmap-item SLUG=<slug> TITLE="<title>" [TOPIC="<topic>"] [STATUS=Proposal]` →
`scripts/new_roadmap_item.py`：

- `roadmaps/proposals/BE-0069-<slug>/` を作り、`BE-0069-<slug>.md` と `BE-0069-<slug>-ja.md` の
  両方を雛形から埋めます。日英のヘッダリンク、メタデータブロック（`Proposal` / `Author` / `Status` /
  `Topic`）、そして Swift-Evolution の五つの節（`Introduction` / `Motivation` /
  `Detailed design` / `Alternatives considered` / `References`）を `TBD` で置きます。
- **常に `BE-0069` プレースホルダを書き**、番号は書きません。ID は恒久で単調増加であり、採番は CI の
  アトミックな仕事です（[BE-0061](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening-ja.md)）。
  番号を当てずっぽうで書く雛形は、プレースホルダが避けている競合をぶり返させます。
- `TOPIC` を [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py) の既知の
  セクション対応表と突き合わせ、項目が実在するセクションに入るようにします。（どのセクションにも
  一致しない `Topic` は、CI が番号を振った後で、索引ビルダを単なるずれではなく*クラッシュ*させます。
  作成時に捕まえる価値のある、鋭い角です。）既定値は `Status=Proposal`、author は
  `git config` から解決します（`HANDLE=` で上書き可）。
- **索引の行は手で足しません。** 生成ツールは `BE-0069` の項目を飛ばすので、プレースホルダの間は
  索引に行が無いまま保たれ、ローカルの `make check` は緑のままです。`roadmap-id` ワークフローが項目に
  番号を振った時点で、索引を生成し直します。（これは知られた落とし穴です。雛形が正しい流れを
  埋め込むので、書き手がはまることはありません。）

これは `ideation` skill が手でこなしている手順への、人間の入口です。skill 側は、ファイルを一から書く
説明をやめ、**このコマンドを呼んで**から雛形の節を埋めるように書き直します。

### B. roadmap の検査（`make lint-roadmap`）

`make lint-roadmap` → `scripts/lint_roadmap.py`。**`make check` に組み込みます。** すべての項目について、
いまは散文とレビュアーの判断に散らばっている「形式が正しいか」の規則を検査します。

- 日英の対が揃っている（両方のファイルがある）；
- slug がディレクトリ名と一致し、ファイル内の `BE-0069`/`BE-NNNN` のトークンがディレクトリと一致する；
- メタデータブロックが揃っている（`Proposal`、`Author`、`Status`、`Topic` のすべてがある）；
- `Author` が GitHub ハンドルのリンクである（`[@handle](https://github.com/handle)`）；
- `Status` が既知の値のいずれかで、サブディレクトリと整合する（`Implemented` なら `implemented/`、
  それ以外は `proposals/`）；
- 参照している `BE-NNNN` が、実在する項目に解決される；
- `BE-0069` の項目が、*別の* `BE-0069` 項目を相互参照していない（採番は項目ごとの書き換えなので、
  これは直せません。すでに知られている制限です）。

いまテストされているのは索引のずれと `Status` とディレクトリの整合だけで
（[`tests/test_roadmap_index.py`](../../tests/test_roadmap_index.py)、
[`tests/test_promote_roadmap_items.py`](../../tests/test_promote_roadmap_items.py)）、残りは
レビュアーが気づくかどうかに頼っています。これで「形式が正しいか」は、実行でき、速く、執筆中に
動かせる第一級の検査になります。指摘も具体的で、全テストを回さずに編集の途中で確かめられます。既存の
テストと重なる部分は、不変条件をここに集約し、テストはこれを呼びます。

### C. 作業場と push 前のヘルパ（`make worktree`、`make preflight`）

[`docs/ja/ai-development.md`](../../docs/ja/ai-development.md) の複数行のレシピをコマンドにします。

- `make worktree TOPIC=<topic>` → `git fetch origin` のあと、
  `git worktree add ../bajutsu-<topic> -b claude/<topic> origin/main`（`<user>/` の接頭は設定可）、
  続けて新しいツリーで `make setup`。省略できない `git fetch origin` を組み込むので、ドキュメントが
  警告する「古い `origin/main` から枝を切る」落とし穴は起こりえません。
- `make preflight` → `git fetch origin && git rebase origin/main && make check` のあと、「完了の定義」
  の確認を表示します（日英ドキュメントは? 振る舞いの変更にテストは? 出荷するなら `Status` の
  切り替えは?）。これは**強制ではなく、人間が起点の助言**です。push 前フックがすでに `make check` を
  *強制*しています。`preflight` は、自分が終わったと思う前に人間が早めに走らせる版であって、二つ目の
  強制ゲートではありません。

### D. commit / PR のメタデータ検査（`make lint-pr`、最も軽い）

いまは散文だけでレビュアーが守らせている規約を、検査できるようにします。あくまで**機械的に**判定
できるものだけで、判断を要するものは対象にしません。

- roadmap に触れる変更は、PR タイトルに `[BE-NNNN]`（または `[BE-0069]`）の接頭を持つ；
- commit メッセージにスコープが付く（`feat(scope): …` / `fix(scope): …` / `docs: …`）；
- 振る舞いの変更にテストの差分が無いときに知らせる。

PR の文脈が要る部分は、強制ではなく任意のままにします。ローカルでブランチの commit に対して走らせても、
CI で PR タイトルに対して走らせてもかまいません。機械化できない規則（「自分のレーンにとどまる」、日英の
文章の質）では、あえて止めません。それらは散文と、人間やレビュアーの判断に残します。

### 置き場所と、散文の行く先

- スクリプトは `scripts/` に、新しい `make` ターゲットは [`Makefile`](../../Makefile) に置き、
  `lint-roadmap` は `check` ターゲットに加えて構造上ゲートに乗せます。
- いまこれらの手順を*説明している*散文は、*コマンドを指す*ように書き直します。`CLAUDE.md` の
  Conventions、`docs/ai-development.md`（と `docs/ja/` のミラー）の worktree / preflight / BE-ID の節、
  そして `ideation` / `implement-be` の skill は、手順をなぞるのではなくコマンドを**呼び**ます。
  ドキュメントの変更は、リポジトリの規則どおり日英の両方です。

### 段階的な移行

1. 本提案。
2. **まず B（`lint-roadmap`）**：純粋な検査で、振る舞いは変えず、すぐ役立ち、すでに半分しか守られて
   いない不変条件を明文化します。`make check` に組み込みます。
3. **A（`new-roadmap-item`）**：レバレッジの最も大きい入口。`ideation` skill をこれを呼ぶように
   更新します。
4. **C（worktree / preflight）**：ドキュメントのレシピを変換し、`docs/ai-development.md` と
   `CLAUDE.md` をコマンドに向け直します。
5. **D（`lint-pr`）**：最も軽い。まず任意とし、その後 `make hooks` が張る `commit-msg` フックを
   検討します。

各段階は小さく独立した PR にします（並行作業の方針、BE-0043）。

## 検討した代替案

- **手順を散文のまま残す（現状維持）。** 却下します。散文は乖離し（BE-0067 の発見）、長い散文の
  レシピを確実に実行できるのは AI だけで、人間が入口を持てません。これこそ本項目が取り除く非対称です。
- **手順を skill の中だけに書く**（`ideation` の散文を厚くする、または専用のサブエージェント）。
  却下します。それは「AI だけが実行する」状態を深めるもので、本項目が離れようとしている当のものです。
  コマンドを*呼ぶ* skill は良く、手順を走らせる*唯一の*手段である skill は良くありません。
- **雛形が本物の BE 番号を採番する。** 却下します。プレースホルダの規則に反します。ID は恒久で
  単調増加であり、採番は CI のアトミックで競合しない仕事です
  （[BE-0061](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening-ja.md)）。
  雛形は `BE-0069` を書かねばなりません。
- **`preflight` を二つ目の強制ゲートにする**（`make check` を走らせる pre-commit フック）。却下します。
  push 前フックがすでに `make check` を強制しており、commit ごとのゲートは内側のループを遅くするだけで、
  安全は増えません。`preflight` は設計上、強制ではなく人間が起点の助言です。
- **`make` ターゲットではなく、出荷する `bajutsu dev …` CLI にする。** いまは却下します。`make <verb>` は
  リポジトリの他のコントリビュータ向け入口すべてと揃っており、パッケージングが要らず、開発者向けの
  道具を出荷する `bajutsu` CLI の面から外しておけます。ターゲットが増えれば、後でまとめた開発 CLI に
  できます。
- **専用の新トピックを設ける。** 代わりに *コントリビューターワークフロー*（BE-0043 のトピック）の下に
  置きます。単一の項目のためにトピックを切らない先例
  （[BE-0065](../BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference-ja.md)）に
  ならいます。

## 進捗

- [x] A。`make new-roadmap-item`（`ideation` スキルが呼び出します）。
- [x] B。`make lint-roadmap`。`make check` に組み込みました。
- [x] C。`make worktree` / `make preflight`。ドキュメントの手順をこれらのコマンドへ向け直しました。
- [x] D。`make lint-pr` と、`pr-title.yml` の CI タイトルゲート。
- [x] フェーズ 5 の末尾。追跡される `.githooks/commit-msg` フック（`core.hooksPath` で配線）が、`lint_pr.py --commit-msg` でスコープのないコミット件名をブロックします。

## 参考

- [CLAUDE.md](../../CLAUDE.md)：これらのコマンドが置き換える散文の手順と、本項目が守る prime
  directive（ゲートに LLM を入れない、開発者向けだけ）。
- [BE-0043 — コンフリクトに強いファイル流動](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md)：
  手で編集する台帳を生成物に変えた、*コントリビューターワークフロー* の先例。
- [BE-0067 — コード品質ゲートの強化](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening-ja.md)：
  CI を `make check` の構造上の写しに（単一の情報源）。`scripts/` を `mypy` の対象に。
- [BE-0061 — 衝突しない BE ID 採番](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening-ja.md)：
  雛形が番号ではなく `BE-0069` を書くべき理由。
- [`docs/ja/ai-development.md`](../../docs/ja/ai-development.md)：C と A が変換する worktree /
  preflight / BE-ID のレシピ。
- [`Makefile`](../../Makefile)、[`scripts/`](../../scripts/)：ターゲットとスクリプトの置き場所。
  拡張する既存の、実行できるガードレール。
- [`ideation`](../../.claude/skills/ideation/) と [`implement-be`](../../.claude/skills/implement-be/)
  の skill：A が形式化し、コマンドを呼ぶように書き直す手順。

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

本項目は、いまは**散文**として存在するコントリビュータ向けの手順を、**人間が実行できるコマンド**へ昇格させます。散文は、[`CLAUDE.md`](../../CLAUDE.md)、`ideation` と `implement-be` の skill、[`docs/ja/ai-development.md`](../../docs/ja/ai-development.md) にあります。コマンドは、小さなスクリプトを背後に持つ `make` ターゲットです。

そうすれば、1 つの入口を、作業を始める人間と、その人間に代わって作業する AI の双方が使えます。ガードレールは、散文を読んだエージェントだけが確実にたどれる手順ではなくなります。誰でも実行できるコマンドになります。

変更するのは、開発者向けの基盤だけです。LLM は足さず、`run` の中で動くことはなく、決定的なゲートにも触れません。prime directive（[CLAUDE.md](../../CLAUDE.md)）は、構造上保たれます。

本項目は、*コントリビューターワークフロー* の系譜の続きです。

- [BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md) は、手で編集していた**共有台帳**（roadmap の索引）を、生成物に変えました。
- [BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening-ja.md) は、**CI を `make check` の構造上の写し**にしました。いつのまにか乖離していた重複を、取り除いています。

どちらも、1 つの原則を適用しています。書き写すのではなく、*実行される*単一の情報源にする、という原則です。本項目は、その原則を**手順そのもの**に適用します。

## 動機

Bajutsu のガードレールは、人間が実行できるかどうかで、すでに 2 種類に分かれています。

- **実行できるもの。** 人間も AI も同じように叩く 1 つのコマンドです。例は、[`make check`](../../Makefile)（ゲート）、[`make setup` / `make hooks`](../../Makefile)（git 設定の自己修復）、[`make roadmap-index` / `roadmap-promote` / `roadmap-id-repair`](../../Makefile) です。コマンドそのものがガードレールなので、忘れたり、うろ覚えになったりしません。
- **散文だけのもの。** 読み手が手でたどるために書かれた、複数手順のレシピです。
  - **新しい roadmap 項目の雛形づくり。** 書き手は、ディレクトリと、*両方*の言語のファイルを、決まった Swift-Evolution 形式で作ります。ファイルには、揃ったメタデータブロック、`BE-0069` プレースホルダ、author の GitHub ハンドルが要ります。`ideation` skill は、この手順の説明に大半を割いています。リポジトリの中で最も間違えやすく、最も手数の多い手順ですが、コマンドがありません。
  - **worktree とブランチの用意。** `git fetch origin && git worktree add … -b … origin/main && make setup` というレシピが、[`docs/ja/ai-development.md`](../../docs/ja/ai-development.md) にあります。
  - **push 前の手順。** 手順は `git fetch origin && git rebase origin/main && make check` で、「完了の定義」のチェックリストが加わります。チェックリストが問うのは、日英ドキュメントの両方に手を入れたか、振る舞いの変更にテストが伴うか、出荷するなら `Status` を切り替えたか、の 3 点です。
  - **項目が整形式か。** 日英の対が揃っているか、slug がディレクトリ名と一致するか、メタデータブロックが揃っているかを確かめます。author がハンドルのリンクか、`Status` がサブディレクトリと整合するか、参照している `BE-NNNN` が実在するかも確かめます。いまこれを担保しているのは、2 つの狭いテスト（索引のずれ、`Status` とディレクトリの整合）と、レビュアーの目だけです。

手順を散文のままにしておくと、3 つの問題が生じます。

1. **散文は現実から乖離します。** BE-0067 は、CI が `make check` から*すでに*乖離していたことを見つけました。読まれるだけで実行されないレシピは、同じように腐ります。コマンドは実行されます。不変条件を守る場面では、テストも通ります。そのため、正しさが保たれます。
2. **長い散文のレシピを確実に実行できるのは、AI だけです。** `ideation` skill を読み込んでいない人間が BE 項目を手で書くと、形式、プレースホルダの規則、索引との関わりを、正しく再現できないことが多いでしょう。これが、本項目の取り除く「AI だのみ」の依存です。
3. **非対称が逆さまです。** 最も手数が多く間違えやすい手順（項目の雛形づくり）には、コマンドが*ありません*。日常の確認（`make check`）は、キー 1 つで済みます。人間が最も助けを必要とするガードレールほど、人間には使えない状態です。

本項目の狙いは、*「AI に任せる」から「人間が始め、AI が代替する」へ移る*ことです。この昇格が、まさにそれを実現します。ガードレールがコマンドになれば、**入口を人間が持ちます**。AI は、人間が実行できるワークフローの*中で*代わりを務める存在になり、手順の唯一の番人ではなくなります。

## 詳細設計

**原則。** それぞれの散文の手順を、[`scripts/`](../../scripts/) の小さなスクリプトを背後に持つ `make <verb>` ターゲットにします。roadmap のファイルを操作するスクリプトは、Python で書き、型を付けます。BE-0067 以降、`mypy` が `scripts/` も対象にしているからです。git の配管にあたるスクリプトは shell で書き、`make lint-sh` が見る `SHELL_SCRIPTS` の一覧に加えます。この分け方は、既存のものに合わせます。

ターゲットが、単一の情報源になります。`CLAUDE.md`、skill、ドキュメントは、手順を書き写す代わりに、**そのコマンドを指します**。BE-0067 が CI を Makefile 経由にしたときも、同じ分け方でした。確認できる不変条件を生む手順では、その検査を `make check` に組み込みます。

以下に、レバレッジの大きい順に、4 つのメカニズムを示します。

### A. `make new-roadmap-item` による roadmap 項目の雛形

`make new-roadmap-item SLUG=<slug> TITLE="<title>" [TOPIC="<topic>"] [STATUS=Approved]` が、`scripts/new_roadmap_item.py` を実行します。

- `roadmaps/proposals/BE-0069-<slug>/` を作り、`BE-0069-<slug>.md` と `BE-0069-<slug>-ja.md` の両方を、雛形から埋めます。雛形は、日英のヘッダリンク、メタデータブロック（`Proposal` / `Author` / `Status` / `Topic`）、`TBD` を置いた Swift-Evolution の 5 つの節からなります。5 つの節は、`Introduction`、`Motivation`、`Detailed design`、`Alternatives considered`、`References` です。
- **常に `BE-0069` プレースホルダを書き**、番号は書きません。ID は恒久で単調増加であり、採番は CI のアトミックな仕事です（[BE-0061](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening-ja.md)）。番号を当てずっぽうで書く雛形は、プレースホルダが避けている競合をぶり返させます。
- `TOPIC` を、[`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py) の既知のセクション対応表と突き合わせ、項目が実在するセクションに入るようにします。どのセクションにも一致しない `Topic` は、CI が番号を振った後に、索引ビルダを*クラッシュ*させます。単なるずれではない鋭い角なので、作成時に捕まえる価値があります。既定値は `Status=Approved` で、author は `git config` から解決します（`HANDLE=` で上書きできます）。
- **索引の行は手で足しません。** 生成ツールは `BE-0069` の項目を飛ばします。そのため、プレースホルダの間は索引に行がないままで、ローカルの `make check` は緑のままです。`roadmap-id` ワークフローが項目に番号を振った時点で、索引を生成し直します。これは知られた落とし穴です。雛形が正しい流れを埋め込むので、書き手がはまることはありません。

このコマンドは、`ideation` skill が手でこなしている手順への、人間の入口です。skill は、ファイルを一から書く説明をやめます。**このコマンドを呼んで**から、雛形の節を埋めるように書き直します。

### B. `make lint-roadmap` による roadmap の検査

`make lint-roadmap` は `scripts/lint_roadmap.py` を実行し、**`make check` に組み込みます**。すべての項目について、いまは散文とレビュアーの判断に散らばっている「形式が正しいか」の規則を検査します。

- 日英の対が揃っている（両方のファイルがある）。
- slug がディレクトリ名と一致し、ファイル内の `BE-0069` や `BE-NNNN` のトークンがディレクトリと一致する。
- メタデータブロックが揃っている（`Proposal`、`Author`、`Status`、`Topic` のすべてがある）。
- `Author` が GitHub ハンドルのリンクである（`[@handle](https://github.com/handle)`）。
- `Status` が既知の値のいずれかで、サブディレクトリと整合する（`Implemented` なら `implemented/`、それ以外の値なら `proposals/`）。
- 参照している `BE-NNNN` が、実在する項目に解決される。
- `BE-0069` の項目が、*別の* `BE-0069` 項目を相互参照していない。採番は項目ごとの書き換えなので、これは直せません（すでに知られている制限です）。

いまテストされているのは、索引のずれと `Status` とディレクトリの整合だけです（[`tests/test_roadmap_index.py`](../../tests/test_roadmap_index.py)、[`tests/test_promote_roadmap_items.py`](../../tests/test_promote_roadmap_items.py)）。残りは、レビュアーが気づくかどうかに頼っています。

この検査で、「形式が正しいか」は、実行でき、速く、執筆中に動かせる第一級の検査になります。指摘は具体的で、全テストを回さずに、編集の途中で確かめられます。既存のテストと重なる部分は、不変条件をここに集約し、テストはこの検査を呼びます。

### C. `make worktree` と `make preflight` による作業場と push 前のヘルパ

この 2 つのターゲットは、[`docs/ja/ai-development.md`](../../docs/ja/ai-development.md) の複数行のレシピを、コマンドにします。

- `make worktree TOPIC=<topic>` は、`git fetch origin` を実行します。続けて、`git worktree add ../bajutsu-<topic> -b claude/<topic> origin/main` を実行します（`<user>/` の接頭は設定できます）。最後に、新しいツリーで `make setup` を実行します。省略できない `git fetch origin` を組み込むので、ドキュメントが警告する「古い `origin/main` から枝を切る」落とし穴は、起こりえません。
- `make preflight` は、`git fetch origin && git rebase origin/main && make check` を実行します。そのあと、「完了の定義」の確認を表示します。確認するのは、日英ドキュメント、振る舞いの変更に伴うテスト、出荷するなら切り替える `Status` です。このターゲットは、**強制ではなく、人間が起点の助言**です。push 前フックが、すでに `make check` を*強制*しています。`preflight` は、自分が終わったと思う前に人間が早めに走らせる版であり、2 つ目の強制ゲートではありません。

### D. `make lint-pr` による commit と PR のメタデータ検査

これは、最も軽いメカニズムです。いまは散文だけで、レビュアーが守らせている規約を、検査できるようにします。対象は**機械的に**判定できる規約だけで、判断を要するものは含めません。

- roadmap に触れる変更は、PR タイトルに `[BE-NNNN]`（または `[BE-0069]`）の接頭を持つ。
- commit メッセージにスコープが付く（`feat(scope): …`、`fix(scope): …`、`docs: …`）。
- 振る舞いの変更にテストの差分がないときに、知らせる。

PR の文脈が要る部分は、強制ではなく任意のままにします。ローカルでブランチの commit に対して走らせても、CI で PR タイトルに対して走らせてもかまいません。機械化できない規則では、あえて止めません。たとえば、「自分のレーンにとどまる」や、日英の文章の質です。これらは、散文と、人間やレビュアーの判断に残します。

### 置き場所と、散文の行く先

- スクリプトは `scripts/` に、新しい `make` ターゲットは [`Makefile`](../../Makefile) に置きます。`lint-roadmap` は `check` ターゲットに加えるので、構造上ゲートに乗ります。
- いまこれらの手順を*説明している*散文は、*コマンドを指す*ように書き直します。対象は、`CLAUDE.md` の Conventions と、`docs/ai-development.md`（と `docs/ja/` のミラー）の worktree、preflight、BE-ID の節です。`ideation` と `implement-be` の skill も対象で、手順をなぞるのではなく、コマンドを**呼び**ます。ドキュメントの変更は、リポジトリの規則どおり、日英の両方です。

### 段階的な移行

1. 本提案。
2. **まず B（`lint-roadmap`）。** 純粋な検査で、振る舞いは変えず、すぐ役立ちます。すでに半分しか守られていない不変条件を、明文化します。`make check` に組み込みます。
3. **A（`new-roadmap-item`）。** レバレッジの最も大きい入口です。`ideation` skill を、このコマンドを呼ぶように更新します。
4. **C（worktree / preflight）。** ドキュメントのレシピを変換し、`docs/ai-development.md` と `CLAUDE.md` をコマンドに向け直します。
5. **D（`lint-pr`）。** 最も軽いものです。まず任意とし、そのあとで `make hooks` が張る `commit-msg` フックを検討します。

各段階は、小さく独立した PR にします。BE-0043 の並行作業の方針に従います。

## 検討した代替案

- **手順を散文のまま残す（現状維持）。** 却下します。散文は乖離します（BE-0067 の発見）。長い散文のレシピを確実に実行できるのは AI だけで、人間は入口を持てません。この非対称を取り除くのが、本項目の存在理由です。
- **手順を skill の中だけに書く**（`ideation` の散文を厚くする、または専用のサブエージェントを置く）。却下します。「AI だけが実行する」状態を深めることになり、本項目が離れようとしているものそのものです。コマンドを*呼ぶ* skill は良いものです。手順を走らせる*唯一の*手段である skill は、良くありません。
- **雛形が本物の BE 番号を採番する。** 却下します。プレースホルダの規則に反します。ID は恒久で単調増加であり、採番は CI のアトミックで競合しない仕事です（[BE-0061](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening-ja.md)）。雛形は `BE-0069` を書かなければなりません。
- **`preflight` を 2 つ目の強制ゲートにする**（`make check` を走らせる pre-commit フック）。却下します。push 前フックが、すでに `make check` を強制しています。commit ごとのゲートは、内側のループを遅くするだけで、安全を増やしません。`preflight` は、設計上、強制ではなく人間が起点の助言です。
- **`make` ターゲットではなく、出荷する `bajutsu dev …` CLI にする。** いまは却下します。`make <verb>` は、リポジトリの他のコントリビュータ向け入口すべてと揃っており、パッケージングが要りません。開発者向けの道具を、出荷する `bajutsu` CLI の面から外しておけます。ターゲットが増えれば、後でまとめた開発 CLI にできます。
- **専用の新しいロードマップのトピックを設ける。** 代わりに、*コントリビューターワークフロー*（BE-0043 のトピック）の下に置きます。単一の項目のためにトピックを切らない先例（[BE-0065](../BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference-ja.md)）に従います。

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

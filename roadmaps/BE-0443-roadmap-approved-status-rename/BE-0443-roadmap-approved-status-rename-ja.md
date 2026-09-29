[English](BE-0443-roadmap-approved-status-rename.md) · **日本語**

# BE-0443 — ロードマップの状態「Proposal」を「Approved」に改称する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0443](BE-0443-roadmap-approved-status-rename-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0443") |
| 実装 PR | [#2075](https://github.com/bajutsu-e2e/bajutsu/pull/2075), [#2079](https://github.com/bajutsu-e2e/bajutsu/pull/2079) |
| トピック | コントリビューターワークフロー |
<!-- /BE-METADATA -->

## はじめに

ロードマップ項目の `状態` フィールドは、現在5つの値のいずれかを取ります
（[BE-0366](../BE-0366-roadmap-rejected-status/BE-0366-roadmap-rejected-status-ja.md)）。
`Implemented`、`In progress`、`Proposal`、`Deferred`、`Rejected` です。本項目はこのうちの1つを
改称します。`Proposal`（提案）を `Approved`（承認済み）に変更します。他の4つの値と、それぞれの
日本語表記は変更しません。改称の対象は、`状態` の値を直接読み書きしている箇所すべてです。
ロードマップ関連スクリプト、CI ワークフロー、既存のロードマップ項目ファイル、ドキュメント、
Agent Package Manager（APM）のスキルソース、それらのゲートテストです。対象はこれに限ります。
「ロードマップ項目を新規に提案する」という行為そのものを指す一般語彙としての「提案」は、現状の
まま残します。`ideation` と `propose-and-build` というスキル名、
`roadmap-proposal-approvals.yml` ワークフローのジョブ名と免除ラベル
`single-approver proposal`、ロードマップのメタデータ表にある、項目自身のファイルへのリンクを持つ
別枠の `Proposal` フィールドです（各項目のメタデータブロックがすでに踏襲している、Swift Evolution
方式の自己参照です）。ダッシュボードの区分色（インディゴ）と、
`bajutsu/common/agents/protocols/proposal.py` にある `class Proposal`（エージェントが次の行動を
提案するための、無関係な別のデータ型）も変更しません。

## 動機

ロードマップ項目の `状態` が `Proposal` のとき、`main` へのマージ前に2人のレビュアーの承認が
必要です。この要求は
[`roadmap-proposal-approvals.yml`](../../.github/workflows/roadmap-proposal-approvals.yml)
ワークフローが課しています。`状態` フィールドの値を文字列 `"Proposal"` と比較して判定します
（[L88](../../.github/workflows/roadmap-proposal-approvals.yml#L88)）。ただし、免除ラベル
`single-approver proposal` が付いた PR は例外で、1人の承認でマージできます
（[L101-110](../../.github/workflows/roadmap-proposal-approvals.yml#L101-L110)）。

つまり、`状態: Proposal` として `main` に到達する項目の大半は、すでに2人のレビュアーによる審査を
経ています。1人の承認でマージされるのは、免除ラベルが付いた少数の例外だけです。にもかかわらず、
`Proposal`（提案）という名前は「まだ検討中で、採否が決まっていない」と読めてしまいます。
ロードマップダッシュボードを眺める貢献者は、審査済みで着手待ちの項目を、メンテナーがまだ受理して
いない項目と誤読しかねません。ダッシュボードの「Show open only」表示から最初の着手対象を選ぶ
新しい貢献者は、この誤読をそのまま判断に反映し、着手できる項目を、まだ採否を待つ項目として
見送ってしまいます。

`Approved` への改称は、この食い違いを解消します。審査のゲート（2人承認、または
`single-approver proposal` の免除下では1人承認）を通過した後にしか読者の目に触れない状態は、
通過済みという実態のまま読めるようになります。本項目が実装された後、`make
roadmap-status STATUS="Proposal"` はどの項目も返さなくなり、`make roadmap-status
STATUS="Approved"` は従来 `Proposal` を持っていた項目を返します。ロードマップダッシュボードの
「Proposals」という表示も「Approved」に変わります。

この改称は、[`docs/ai-development.md`](../../docs/ai-development.md#roadmap-items-be-ids-strict)
が定める「コードが状態を決める」という原則と矛盾しません。この原則の軸は、項目の実装が存在するか
どうかであり、レビューを経たかどうかではないからです。`Approved` は、実装がまだ存在しない同じ
状態に付け替える新しい名前であって、軸そのものを変えるものではありません。新規に作成した直後の、
レビューを受ける前の項目も、この原則どおり `状態` は `Approved` になります。
`roadmap-filter`（[`scripts/roadmap_query.py`](../../scripts/roadmap_query.py)）は、ローカルの
ブランチ上の `BE-0443` プレースホルダーも番号付きの項目と同じように読むため、未レビューの提案に
取り組んでいるセッションは、レビュー前にこの値を目にすることがあります。これは新しい問題ではあり
ません。本項目が実装される前は、スキャフォールドの従来のデフォルト値である `Proposal` も、同じ
ようにローカルブランチ上で見えていました。`状態` は「この項目の実装が存在するか」以上のことを
主張したことがなく、スキャフォールドしたばかりの項目には、`状態` の表示がどうであれ、実装が
明らかに存在しません。本項目が取り除く誤読は、公開された側で目にする誤読です。**公開済みの**
ダッシュボードを読む、あるいは `main` に対して `roadmap-filter` を実行する見ず知らずの読み手に
とっては、一覧に載っている時点でその項目はすでに審査のゲート（2人承認、または免除ラベル下では
1人承認）を通過しています。原則を説明する
`docs/ai-development.md` の当該段落自体も `Proposal` に言及しているため、本項目のドキュメント
更新の対象に含みます。加えて、`Proposal` の実装に着手した瞬間を「受理」と呼んでいるいくつかの箇所
（[`.apm/skills/implement-be/SKILL.md`](../../.apm/skills/implement-be/SKILL.md)、
[`docs/roadmap-workflow.md`](../../docs/roadmap-workflow.md)、
[`docs/contributor-workflow-tutorial.md`](../../docs/contributor-workflow-tutorial.md)）も対象に
含みます。本項目の前提は、受理は2人承認によるマージの時点ですでに完了しているというものです。
そのため、これらの箇所は「`Approved` の項目を実装することは、それに**着手する**ことだ」という
言い方に書き換えます。「着手することが受理になる」のではありません（「詳細設計」を参照）。

## 詳細設計

### 値を直接扱うスクリプト

| ファイル | 変更内容 |
|---|---|
| [`scripts/check_roadmap_format.py`](../../scripts/check_roadmap_format.py) | `STATUS_PAIR` の `"Proposal": "提案"` を `"Approved": "承認済み"` に改称します。`ORDER_EN` / `REQUIRED_EN` / `REQUIRED_JA` はメタデータ表の別枠フィールド名を指しており、変更しません。 |
| [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py) | `STATUS_TO_BUCKET` の `"Proposal": "Proposals"` を `"Approved": "Approved"` に、`BUCKETS` タプルの `("Proposals", "proposals")` を `("Approved", "approved")` に改称します。`Proposals` バケットに言及しているモジュール docstring と `STATUS_TO_BUCKET` のコメントも更新します。 |
| [`scripts/build_roadmap_dashboard.py`](../../scripts/build_roadmap_dashboard.py) | `BUCKET_COLOR` のキーと `BUCKET_LABEL` の `"Proposals": "Proposal"` エントリ（キーとバッジ表示値の両方）を `"Approved"` に改称します。色 `#534AB7` は維持します。「indigo as proposed」というコメント、`OPEN_BUCKETS=['Proposals', 'In progress']`、CLI ヘルプ文言を兼ねるモジュール docstring、ページ自身の公開される導入文（`_INTRO`）にある `Proposal` への2箇所の言及も更新します。 |
| [`scripts/sync_roadmap_tracking_issues.py`](../../scripts/sync_roadmap_tracking_issues.py) | `OPEN_STATUSES = frozenset({"Proposal", "In progress"})` を `frozenset({"Approved", "In progress"})` に改称し、docstring とコメント中の `Proposal` 言及も更新します。 |
| [`scripts/new_roadmap_item.py`](../../scripts/new_roadmap_item.py) | `--status` のデフォルト値 `"Proposal"` を `"Approved"` に、`--status` のヘルプ文言と docstring の使用例 `[STATUS=Proposal]` も更新します。 |
| [`scripts/roadmap_query.py`](../../scripts/roadmap_query.py) | 改称するリテラルはありません。`VALID_STATUSES` は `STATUS_TO_BUCKET` から導出されるため、インデックス側の変更に自動的に追随します。`Proposal` を例示している2箇所の docstring 使用例だけを更新します。 |
| [`scripts/sync_roadmap_topic_labels.py`](../../scripts/sync_roadmap_topic_labels.py) | `SHIPPED_STATUS = "Implemented"` に添えたコメントにある `Proposal` への言及を更新します。値自体は `"Implemented"` のまま変更しません。 |

### CI ワークフロー

[`roadmap-proposal-approvals.yml`](../../.github/workflows/roadmap-proposal-approvals.yml) の
ジョブ名 `require two approvals for BE proposals` と免除ラベル `single-approver proposal` は、
どちらも `状態` の値ではなく「新規項目を提案する」という行為そのものを指すため、変更しません。
改称するのは、[L88](../../.github/workflows/roadmap-proposal-approvals.yml#L88) の
`if [ "$status" = "Proposal" ]`（`"Approved"` に変更）、
[L98](../../.github/workflows/roadmap-proposal-approvals.yml#L98) の `::notice::` 文言、
[L12](../../.github/workflows/roadmap-proposal-approvals.yml#L12) と
[L59-60](../../.github/workflows/roadmap-proposal-approvals.yml#L59-L60) のコメント中にある
列挙値としての言及だけです。
[`roadmap-tracking-issues.yml`](../../.github/workflows/roadmap-tracking-issues.yml) の
[L4](../../.github/workflows/roadmap-tracking-issues.yml#L4) にあるコメント「Status `Proposal` /
`In progress`」と、[`.github/roadmap-refresh-prompt.md`](../../.github/roadmap-refresh-prompt.md)
にある `状態` 遷移の説明も、同じ扱いで改称します。

### 既存ロードマップ項目の移行

各ロードマップ項目ファイルの、ファイル**先頭**にある `<!-- BE-METADATA -->` …
`<!-- /BE-METADATA -->` ブロック（ファイル中で最初に現れるフェンス対）だけを対象に、`状態` の
行を書き換えます。ファイル中のすべてのフェンス対を対象にした置換ではありません。この違いが
効くのが
[BE-0074](../BE-0074-be-template-standardization/BE-0074-be-template-standardization-ja.md) です。
自身の `状態` は `実装済み` ですが、本文中のテンプレート解説の一部として、2つ目の
`<!-- BE-METADATA -->` フェンス対を例示コードブロックに含んでおり、そこに
`| Status | **Proposal** |` という行があります。「フェンスの内側かどうか」だけで絞ると、この
2つ目のフェンス対も一致してしまいます。「ファイル先頭の最初のフェンス対」に絞ることで、これを
除外できます。BE-0074 の例示行は、当時のテンプレートをそのまま示すものであり、書き換えの対象に
しません。これは
[`roadmap-proposal-approvals.yml:86-87`](../../.github/workflows/roadmap-proposal-approvals.yml#L86-L87)
の `sed` が読み取る範囲とは異なります。あちらはファイル中のすべてのフェンス対を対象にしますが、
CI ゲートが見るのは1項目につき2ファイルの差分だけなので、それで安全です。BE-0074 も対象に含む
リポジトリ全体の移行では、同じ範囲では安全ではありません。日本語版のメタデータブロックには、
項目自身のファイルへのリンクを持つ
`| 提案 | ... |` という別の行もあります。`状態` の値としての「提案」と、フィールド名としての「提案」が同じ文字列に
なっているため、`| 状態 | **提案** |` という行全体に一致する場合だけを
`| 状態 | **承認済み** |` に置き換え、フィールド名の行は変更しません。

### ドキュメント

`状態` の取りうる値を列挙している次のページを更新し、`Proposal` を `Approved` に差し替えます。
[`CLAUDE.md`](../../CLAUDE.md)、[`roadmaps/README.md`](../../roadmaps/README.md) と日本語版の
[`README-ja.md`](../../roadmaps/README-ja.md)、
[`docs/ai-development.md`](../../docs/ai-development.md) と
[`docs/ja/ai-development.md`](../../docs/ja/ai-development.md)、
[`docs/roadmap-workflow.md`](../../docs/roadmap-workflow.md) と
[`docs/ja/roadmap-workflow.md`](../../docs/ja/roadmap-workflow.md)（本文、図の alt テキスト、
mermaid 図のノードの3箇所。このうちノードの書き換えだけが `make docs-diagrams` の再実行を必要と
し、`docs/assets/diagrams/roadmap-workflow-cycle.svg` と
`docs/ja/assets/diagrams/roadmap-workflow-cycle-ja.svg` を編集後のフェンスから再生成します）、
[`docs/contributor-workflow-tutorial.md`](../../docs/contributor-workflow-tutorial.md) と日本語版、
[`docs/overview.md`](../../docs/overview.md) と日本語版、
[`docs/specs/roadmap-dashboard-pagination-and-quick-filters.md`](../../docs/specs/roadmap-dashboard-pagination-and-quick-filters.md)、
[`Makefile`](../../Makefile) の `new-roadmap-item` と `roadmap-status` のヘルプ文言です。
`docs/architecture.md`、`docs/developer-guide.md`、
[`docs/recording.md`](../../docs/recording.md)、およびそれぞれの日本語版（`docs/ja/developer-guide.md`
のクラス図を含む）にある `Proposal` は、「はじめに」で述べたエージェント用データクラスを指す、
`状態` とは無関係な言及であり、対象から除外します。

`docs/ai-development.md` とその日本語版には、単純な置換を超える2箇所の言い回しの修正が加わります。
どちらも、実装の開始ではなく2人承認によるマージこそが提案を受理するという、本項目自身の前提から
導かれるものです。状態と区分の対応表にある `Proposal` の行は「Proposals — under consideration」
（検討中）と読めます。これを単純に置換すると「Approved — under consideration」となり、自己矛盾
します。そこで「Approved — reviewed, not yet started」（レビュー済み、未着手）に書き換えます。
同じ表の `In progress` 行は「accepted, actively being built」（受理済み、実装中）と読めますが、
受理は1行前の `Approved` の時点ですでに完了しているため、いまや冗長な「accepted,」を落とし、
「actively being built」とします。同じ修正は、実装への**着手**を受理の瞬間と呼んでいる他の箇所
すべてに及びます。[`.apm/skills/implement-be/SKILL.md`](../../.apm/skills/implement-be/SKILL.md)
（手順1に「implementing it accepts it」という一文があります）、`docs/roadmap-workflow.md` と
その日本語版、`docs/contributor-workflow-tutorial.md` とその日本語版です。いずれも
「`Approved` の項目を実装することは、それに**着手する**ことだ」という趣旨の言い回しに変わります。
`状態` が `Approved` になった時点で、受理はすでに完了しているからです。

すでに `実装済み` になっている数十件のロードマップ項目も、本文中で `Proposal` に言及しています。
`状態` がまだ生きた値だった当時に書かれた、設計上の議論や、後続の項目が引用する前例、具体例と
してのものです。そのすべてを書き換えると、本項目が本来意図していない過去の議論の蒸し返しに
本項目のスコープを丸ごと使ってしまいます。本項目は、同じ種類の改称で
[BE-0366](../BE-0366-roadmap-rejected-status/BE-0366-roadmap-rejected-status-ja.md) がすでに
引いた線をそのまま踏襲します。過去の記録は、当時を正確に伝える記録としてそのまま残します
（上の BE-0074 のテンプレート例示行と同じ扱いです）。改称するのは、出荷済みの項目の本文が
**いまも動いている仕組み**を現在形で説明しており、本項目のマージ直後から内容が古くなって
しまう場合だけです。この基準に当てはまるのは2件です。
[BE-0109](../BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues-ja.md)
（トラッキング Issue のライフサイクル。「オープンな項目……`状態` が `Proposal`……」）と、
[BE-0162](../BE-0162-roadmap-status-filter-skill/BE-0162-roadmap-status-filter-skill-ja.md)
（`roadmap-filter` スキル自身が挙げる有効な `状態` の値）です。同じ理由で BE-0366 が改称した
のと同じ2件です。どちらも（英語・日本語とも）本 PR の同じ変更のなかで `Proposal` を `Approved`
に改称します。

さらに2件、
[BE-0069](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails-ja.md)
と
[BE-0216](../BE-0216-propose-and-build-parallel-skill/BE-0216-propose-and-build-parallel-skill-ja.md)
は、そのままコピー&ペーストして実行できるコマンド例（`make new-roadmap-item … [STATUS=Proposal]`、
`Status: Proposal`）を引用しています。本項目の改称は、これらを単に古くするだけでなく
**実際に壊れた例にしてしまいます**。`STATUS=Proposal` は、もはや `check_roadmap_format.py` が
受理する値ではないため、どちらの例も、いまそのまま試すとフォーマットゲートに弾かれるファイルを
作ってしまいます。これは、「いまも動いている仕組み」テストが狙う叙述的な陳腐化よりも強い
失敗モードであり、他の点では過去の記録である項目であっても、直す価値があります。そのため、
この2件も（英語・日本語とも）改称します。一方、古い語彙を**叙述するだけ**の候補、たとえば
[BE-0094](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard-ja.md) と
[BE-0159](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders-ja.md)
は、どちらもダッシュボードのライフサイクル区分を「実装済み・実装中・Proposals・保留」と4区分で
列挙していますが、そのまま残します。この4区分の列挙は、
[BE-0366](../BE-0366-roadmap-rejected-status/BE-0366-roadmap-rejected-status-ja.md) が5つ目の
区分「却下」を追加した時点で、どちらの言及も更新されないままとっくに古くなっており、本項目の
改称とは無関係に、すでに現行の語彙に追随できていません。この2件が共有している1語だけを改称
しても、修正されるのは偶然の一致であって、実際の陳腐化ではありません。
その後のフォローアップ [#2079](https://github.com/bajutsu-e2e/bajutsu/pull/2079) で「いまも動いている仕組み」テストをもう一度適用し、
過去の値ではなく現在も有効なライフサイクルの規則を述べている項目をさらに4件見つけました。
[BE-0100](../BE-0100-roadmap-progress-tracking-template/BE-0100-roadmap-progress-tracking-template-ja.md)
（未着手の項目の `進捗` チェックリスト）、
[BE-0139](../BE-0139-roadmap-dashboard-issue-links/BE-0139-roadmap-dashboard-issue-links-ja.md) と
[BE-0156](../BE-0156-roadmap-topic-label-sync/BE-0156-roadmap-topic-label-sync-ja.md)
（トラッキング Issue を開いたままにする状態）、
[BE-0222](../BE-0222-daily-doc-freshness-pr/BE-0222-daily-doc-freshness-pr-ja.md)
（コードのマージ後も未完了の状態に残る項目）です。これらの言及は `承認済み` に改めました。
本項目の最後の `進捗` チェック項目にあるリポジトリ全体のグレップは、次の4種類の
`Proposal` を見つけても、それは指摘ではありません。メタデータのフィールド名、`class Proposal`
エージェントデータクラスとそのドキュメント言及、BE-0074 のテンプレート例示行、そして他のすべての
出荷済み項目にあるこの種の過去の記録です。

### APM スキルソース

[`.apm/skills/`](../../.apm/skills/) 配下の6スキルのソースが、`状態` の値として `Proposal` に
言及しています。`ideation`、`propose-and-build`、`roadmap-filter`、`task-select`、
`implement-be`、`be-progress-tracker`（`Proposal (pre-allocation)` という表記を
`Approved (pre-allocation)` に変更）です。`ideation` にあるメタデータ表のフィールド名としての
「提案」への言及は、メタデータ表そのものと同じ理由で変更しません。`implement-be` には、上の
「ドキュメント」で修正した「implementing it accepts it」という言い回しも含まれます。その一文は
`implement-be` 自身の手順1にあるためです。ソースを更新したら `make skills` を実行し、
`.claude/skills/` への反映と `apm.lock.yaml` の更新を同じ変更に含めます。`.claude/skills/` は
直接編集しません。

### テストコード

リテラルの `"Proposal"` をアサーションで使うテストファイルは、次の10件です。
`tests/conftest.py`、`tests/test_check_roadmap_format.py`、
`tests/test_fix_roadmap_drift.py`、`tests/test_lint_roadmap.py`、`tests/test_new_roadmap_item.py`、
`tests/test_roadmap_dashboard.py`、`tests/test_roadmap_index.py`、`tests/test_roadmap_query.py`、
`tests/test_sync_roadmap_topic_labels.py`、`tests/test_sync_roadmap_tracking_issues.py` です。
これらのフィクスチャ、パラメータ化、アサーションを `"Approved"` に更新します（日本語版の
フィクスチャも組み立てる3件、`test_lint_roadmap.py`、`test_fix_roadmap_drift.py`、
`test_new_roadmap_item.py` では、日本語の値 `提案` も `承認済み` に更新します）。旧値を関数名に
含むテストは1件もないため、改名は発生しません。`tests/test_allocate_roadmap_ids.py` にも `Proposal` への言及がありますが、これは
フィクスチャが組み立てるメタデータのフィールド名（`* Proposal: [{be_id}]({name}.md)`）としての
言及だけです。このフィールド名は他のすべての箇所と同じく変更しないため、このファイルは改称の
対象に含めません。

### プライムディレクティブとの整合性

変更が及ぶのは、メタデータの語彙、7本のスクリプト、2本の CI ワークフロー、ロードマップ項目
ファイル、ドキュメント、6件の APM スキルソース、それらのゲートテストです。どの経路にも LLM を
持ち込まず、`run` と CI は決定的なままで、アプリ固有の判断がツールやドライバーに入り込むことも
ありません。

## 検討した代替案

**「提案」という一般語彙も一括で改称する。** `ideation` と `propose-and-build` のスキル名、
`roadmap-proposal-approvals.yml` のジョブ名と免除ラベルまで含めて「承認」系の語に統一する案です。
採りません。ロードマップ項目を新規に提案する行為は、`状態` の値とは別に成立する概念だからです。
加えて、CI ワークフローのジョブ名を変えると、リポジトリのブランチ保護ルールセットが参照する
必須チェック名も変わり、リポジトリ管理者による手動の再設定が必要になります。どちらのコストも、
本項目の目的（`状態` の名前と、2人承認によるマージがすでに意味する実態との食い違いを解消する
こと）には寄与しません。

**日本語表記を、より短い「承認」にする。** 「承認」だけでは、PR レビューを承認するという別の
行為とも紛れやすくなります。「承認済み」であれば、「実装済み」と同じく、承認という行為ではなく
承認を終えた状態を表す語として読め、区別が付きます。

**旧値 `Proposal` を非推奨のエイリアスとして残し、段階的に移行する。** ロードマップ項目の
データは本リポジトリ内で完結しており、`状態: Proposal` という文字列に依存する外部の消費者は
存在しません。移行は、既存のロードマップ項目ファイルを書き換える一度限りの機械的な作業で完了
できるため、エイリアスを残す理由がありません。同じ種類の `状態` 列挙値の改称である
[BE-0366](../BE-0366-roadmap-rejected-status/BE-0366-roadmap-rejected-status-ja.md)
（`Proposal (deferred)` から `Deferred` への改称）も、エイリアスを残さず旧値を置き換えて
います。

**新規項目をレビュー前専用の別の値でスキャフォールドし、マージ時の ID
割り当て（[BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation-ja.md)）
が `main` で項目に番号を振るときに `Approved` へ切り替える。** この案なら、`Approved` が未マージの
ブランチに現れることはなくなります。ただし、スキャフォールドからマージまでの間だけ存在する
6つ目の `状態` の値が増え、割り当てジョブが書き換える対象も（ID に加えて）もう1つ増えます。
この割り当てジョブは、`roadmaps/` だけに絞った、監査しやすい狭いバイパス push であることが
存在意義であり（`scripts/check_renumber_diff.py` がすでにその影響範囲を絞り込んでいます）、
それを広げることになります。採りません。この案が防ぐはずの「未レビューのブランチに `Approved`
が現れる」という事態は、本項目が持ち込む新しい問題ではないからです。旧来のデフォルト値
`Proposal` も、まったく同じ理由（`状態` はレビューの有無ではなく実装の有無を追跡するフィールド
であること。「動機」を参照）で、マージ前から同じように見えていました。すでに存在しない問題を
解決するために、割り当てジョブの書き込み範囲を広げるのは、割に合わない交換です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに1つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `check_roadmap_format.py` の `STATUS_PAIR` にある `"Proposal"` エントリを `"Approved"` に
      改称する。
- [x] `build_roadmap_index.py` の `STATUS_TO_BUCKET` キーと `BUCKETS` エントリを改称する。
- [x] `build_roadmap_dashboard.py` の `BUCKET_COLOR` / `BUCKET_LABEL` / `OPEN_BUCKETS` と関連
      コメントを改称する。
- [x] `sync_roadmap_tracking_issues.py` の `OPEN_STATUSES` と docstring を改称する。
- [x] `new_roadmap_item.py` のデフォルト `--status` 値と docstring 使用例を改称する。
- [x] `roadmap_query.py` の docstring 使用例と、`sync_roadmap_topic_labels.py` のコメントを
      更新する。
- [x] `roadmap-proposal-approvals.yml` と `roadmap-tracking-issues.yml` のリテラル比較部分と
      コメントを改称し、ジョブ名と免除ラベルは変更しない。
- [x] 既存のロードマップ項目すべての `<!-- BE-METADATA -->` 内 `状態` 行を `承認済み` に移行する
      （BE-0074 本文中の例示行は対象外）。
- [x] *詳細設計* に挙げたドキュメントページを更新する（`docs/roadmap-workflow.md` の図の再生成を
      含む）。加えて、いまも動いている仕組みを説明している BE-0109 と BE-0162 自身の本文
      （英語・日本語とも）のリテラルを改称する。BE-0069 と BE-0216 の `make new-roadmap-item` /
      `Status:` のコマンド例も改称する。放置すると、単に古くなるだけでなく実際に壊れた例に
      なってしまうためです。
- [x] 6件の APM スキルソースを改称し、`make skills` を実行する。
- [x] 10件のテストファイルのリテラル（テスト名を含む）を改称する。
- [x] `make check` と、旧リテラルが残っていないことのリポジトリ全体でのグレップで検証する。

ログ：

- [#2075](https://github.com/bajutsu-e2e/bajutsu/pull/2075) で12個の作業単位すべてを1つの PR に
  landing しました。`make roadmap-status STATUS="Proposal"`
  は「unknown status」で失敗するようになり、`make roadmap-status STATUS="Approved"` は移行済みの
  18項目を返します（本項目自身は `実装済み` なので含みません）。2ラウンドのコールドセルフ
  レビュー（BE-0347）により、計画になかった2点の修正が加わり、本項目の本文にも反映しています。
  1点目は、審査のゲートに関する記述を `single-approver proposal` の免除ラベルを考慮したものに
  直し、すべてのマージが2人承認を経たとは限らない点を正確にしました。2点目は、新規に
  スキャフォールドした項目が持つ `Approved` という既定値について、レビュー前には見えないという
  誤った主張ではなく、無害であるという説明に改めました（`roadmap-filter` はローカルの `BE-0443`
  プレースホルダーを番号付きの項目と同じに読みます。「動機」と、却下したレビュー前専用値の
  代替案を参照）。レビューではさらに、BE-0109、BE-0162、BE-0069、BE-0216 が、この改称によって
  古くなる、あるいは実際に壊れてしまうリテラルを本文に持つことも指摘されたため、BE-0366の前例と
  「実際に壊れる例」という基準に沿って、この4件も同じ PR で改称しました。他のすでに `実装済み`
  の項目にある `Proposal` への言及は、当時を正確に伝える記録としてそのまま残しています。
  ライブの PR に対するフォローアップのセルフレビュー（Copilot と、本リポジトリ自身の自動
  レビュー）では、ワークフローの移行漏れ（改称前の `main` から分岐したままの提案 PR のために、
  2人承認ゲートが旧リテラルも受理する必要があった点）と、いくつかの言い回しの誤りをさらに
  見つけ、このログに反映しています。
- [#2079](https://github.com/bajutsu-e2e/bajutsu/pull/2079) で、現在も有効なライフサイクルの規則を述べている BE-0100、BE-0139、BE-0156、
  BE-0222 の状態の表記を改称し、*詳細設計* で過去の記録として残す項目の列挙もそれに合わせて
  絞りました。

## 参考

- [BE-0366 — ロードマップの状態に却下を追加し、保留と区別する](../BE-0366-roadmap-rejected-status/BE-0366-roadmap-rejected-status-ja.md)
  — 同じ種類の `状態` 列挙値の改称であり、「検討した代替案」で、旧値をエイリアスなしで置き換える
  前例として引用しています。
- [BE-0078 — 状態駆動のロードマップフォルダ](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders-ja.md)
  — 本項目がその値の1つを改称する、`状態` と区分の対応関係を導入しました。
- [BE-0089 — マージ時の BE ID 割り当て](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation-ja.md)
  — マージ時の ID 割り当てにより、新規に作成した項目は `BE-0443` のまま、スキャフォールドが
  設定した `状態`（本項目の実装後はデフォルトで `Approved`）を持ってレビューに進みます
  （「動機」を参照）。
- [`docs/ai-development.md`](../../docs/ai-development.md#roadmap-items-be-ids-strict) — 本項目の
  「動機」が扱う、ロードマップのメタデータ規則と「コードが状態を決める」という原則です。
- [`roadmaps/README.md`](../../roadmaps/README.md) — 本項目が更新する状態一覧です。

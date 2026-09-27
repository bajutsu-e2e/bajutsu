# ロードマップステータス「Proposal」を「Approved」へ改称する

> ステータス: ドラフト
> 対象: `roadmaps/` の既存項目、`scripts/` のロードマップ関連スクリプト、CI ワークフロー、ドキュメント、`.apm/skills/`
> 関連: [`CLAUDE.md`](../../CLAUDE.md)、[`roadmaps/README.md`](../../roadmaps/README.md)、[`docs/ai-development.md`](../ai-development.md)

Bajutsu Evolution（BE）ロードマップ項目は `Status` フィールドを持ちます。このフィールドの列挙値のうち、
`Proposal`（日本語表記「提案」）を `Approved`（「承認済み」）へ改称します。対象は `Status` フィールドの
値と、その値を直接エンコードしているコード・CI・ドキュメントです。「提案PR」「提案書」の意味で使う
「提案」という語は対象外とし、そのまま残します。対象外の例は、`ideation` / `propose-and-build` スキル名、
CI ワークフローの命名、ロードマップ項目のメタデータ表にある提案元 PR へのリンク行です。

## 1. なにをつくるのか

`Status` フィールドの列挙値を次のように改称します。

| 現行（英語） | 現行（日本語） | 改称後（英語） | 改称後（日本語） |
|---|---|---|---|
| `Proposal` | 提案 | `Approved` | 承認済み |

`Implemented` / `In progress` / `Deferred` / `Rejected` の4値と、それらの日本語表記は変更しません。

改称の対象は、この列挙値を直接扱っている箇所に限ります。

- 既存のロードマップ項目ファイルが持つ `Status: Proposal` / `状態: 提案` の行
- この列挙値を定義・比較・変換しているスクリプト（辞書のキー、集合の要素、デフォルト値、CI の文字列比較）
- ダッシュボードのバケット名・バケットIDのうち、この列挙値に由来する部分
- ドキュメント中で `Status` の取りうる値として `Proposal` を列挙している箇所
- `.apm/skills/` 配下のスキルが `STATUS=Proposal` のように具体的な値として言及している箇所

### やらないこと

- **「提案」という一般語彙の置き換え。** `ideation` スキルや `propose-and-build` スキルの名称は改称しません。
  CI ワークフロー [`roadmap-proposal-approvals.yml`](../../.github/workflows/roadmap-proposal-approvals.yml)
  のジョブ名と免除ラベル `single-approver proposal` も同様です。これらは `Status` の値ではなく、
  「ロードマップ項目を新規に提案する PR」という行為そのものを指す一般語彙です。
- **メタデータ表の `Proposal` / `提案` フィールド名の変更。** 各ロードマップ項目のメタデータ表には、
  `Status` とは別に、提案元 PR へのリンクを持つ `Proposal`（日本語表記「提案」）という行があります
  （[`scripts/check_roadmap_format.py:45-68`](../../scripts/check_roadmap_format.py#L45-L68) の
  `ORDER_EN` / `REQUIRED_EN` / `REQUIRED_JA` が定義する必須フィールドの1つです）。これは `Status` の値と
  同じ文字列を使っているだけの別概念であり、改称の対象ではありません。
- **ダッシュボードのバケット色の変更。** `Approved` バケットも、現行の紫系の色 `#534AB7` を維持します。
- **`BE-0074` 本文のテンプレート解説にある例示行の書き換え。** この項目自身の `Status` は `Implemented`
  のままです。本文中の `| Status | **Proposal** |` は解説用の例示であり、改称の対象ではありません。
- **後方互換の維持。** スクリプトや CI が旧値 `Proposal` を別名として受理し続ける仕組みは作りません。
  ロードマップ項目は本リポジトリ内だけで完結するデータです。外部の消費者は `Status: Proposal` という
  文字列に依存していません。置き換え漏れは、5章の検証で拾いきる方針とします。
- **既存の GitHub Issue 本文の手動修正はしません。** `scripts/sync_roadmap_tracking_issues.py` の
  `OPEN_STATUSES` は、`Status` が `Proposal` か `In progress` の項目だけをオープンな項目として扱います。
  Issue 本文に `Proposal` という文字列は書き込まれません。`OPEN_STATUSES` を改称後の値に更新すれば、
  `main` への push で自動的に整合します
  （[`roadmap-tracking-issues.yml`](../../.github/workflows/roadmap-tracking-issues.yml)
  が `roadmaps/**` の変更で自動実行されます）。

## 2. なぜつくるのか

ロードマップ項目の `Status` を `Proposal` とする PR は、`main` へのマージ前に2人のレビュアーの
承認が必要です。この要求は `roadmap-proposal-approvals.yml` が課しています
（[L88](../../.github/workflows/roadmap-proposal-approvals.yml#L88) の `if [ "$status" = "Proposal" ]`
が判定します）。ただし、PR に免除ラベル `single-approver proposal` が付いている場合は例外で、1人の
承認でマージできます
（[L101-110](../../.github/workflows/roadmap-proposal-approvals.yml#L101-L110)）。

つまり、ロードマップ上に `Status: Proposal` として存在する項目の多くは、2人以上のレビュアーによる
審査を経てマージされています。免除ラベル付きの少数の例外を除けば、実装はまだ始まっていませんが、
意思決定としては完了しています。

にもかかわらず、`Proposal`（提案）という名前は「まだ検討中で、採否が決まっていない案」を意味します。
ロードマップダッシュボードや `roadmap-filter` の一覧をこの状態のまま読む開発者やコミュニティ
参加者は、審査済みで着手待ちの項目を「まだ承認されていない検討中の案」と誤読しかねません。新しく参加した
貢献者が `roadmaps/README.md` の状態一覧だけを読んで着手対象を探すとき、この誤読は「着手してよい項目」の
判断を誤らせる実害につながります。

放置すれば、ロードマップの状態表記と実態（2人承認を得て受理された、という事実）が食い違ったまま増え
続けます。状態の意味を説明するたびに、「`Proposal` だが実際にはレビュー済みだ」という注釈を添える必要が
生じ続けます。`Approved`（承認済み）への改称は、この状態が指す実態をそのまま名前にします。すなわち
「ロードマップへの掲載そのものは承認されたが、実装はまだ始まっていない」という実態です。

この改称は、`docs/ai-development.md` が定める「コードが状態を決める」という原則
（[L1046-1053](../ai-development.md#L1046-L1053)、日本語版
[L950-964](../ja/ai-development.md#L950-L964)）と矛盾しません。この原則が言う `Status` の軸は
実装の有無であり、レビューの有無ではありません。`Approved` は、実装が
まだ存在しない同じ状態に付け替える新しい名前であって、軸そのものは変えません。

新規項目を作成した直後、レビューを受ける前の PR でも、この原則どおり `Status` は `Approved`
（実装が存在しない状態）になります。ロードマップダッシュボードや `roadmap-filter` は `main` 上の
項目だけを表示するため、レビュー前の読者がこの値を目にすることはありません。原則を説明する上記の
段落自体も `Proposal` という語を使っているため、3章の「ドキュメント」節に挙げたとおり書き換えの
対象に含めます。

## 3. どう実現するか

### 列挙値を直接扱うコード

| ファイル | 変更内容 |
|---|---|
| [`scripts/check_roadmap_format.py:78-84`](../../scripts/check_roadmap_format.py#L78-L84) | `STATUS_PAIR` 辞書のキー `"Proposal"` を `"Approved"` に、値 `"提案"` を `"承認済み"` に変更します。同ファイルの `ORDER_EN` / `REQUIRED_EN` / `REQUIRED_JA`（[L45-68](../../scripts/check_roadmap_format.py#L45-L68)）にある `"Proposal"` / `"提案"` はフィールド名であり、変更しません。 |
| [`scripts/build_roadmap_index.py:105-118`](../../scripts/build_roadmap_index.py#L105-L118) | `STATUS_TO_BUCKET` のキー `"Proposal"` を `"Approved"` に、対応するバケットID `"Proposals"` を `"Approved"` に変更します。`BUCKETS` タプルの `("Proposals", "proposals")` を `("Approved", "approved")` に変更します（他の4バケットは値と表示名が一致しているため、この変更で単数・複数の表記ゆれも解消します）。 |
| [`scripts/build_roadmap_dashboard.py:59-73`](../../scripts/build_roadmap_dashboard.py#L59-L73) | `BUCKET_COLOR` / `BUCKET_LABEL` 辞書のキー `"Proposals"` を `"Approved"` に変更します（色 `#534AB7` は維持し、`BUCKET_LABEL` の値も `"Approved"` にします）。[L57](../../scripts/build_roadmap_dashboard.py#L57) のコメント「indigo as proposed」も実態に合わせて更新します。[L931](../../scripts/build_roadmap_dashboard.py#L931) の `OPEN_BUCKETS=['Proposals', 'In progress']` を `['Approved', 'In progress']` に変更し、[L1413](../../scripts/build_roadmap_dashboard.py#L1413) 付近のヘルプ文言中の `Proposal` 言及も更新します。 |
| [`scripts/sync_roadmap_tracking_issues.py:52`](../../scripts/sync_roadmap_tracking_issues.py#L52) | `OPEN_STATUSES = frozenset({"Proposal", "In progress"})` を `frozenset({"Approved", "In progress"})` に変更します。[L4](../../scripts/sync_roadmap_tracking_issues.py#L4)・[L19](../../scripts/sync_roadmap_tracking_issues.py#L19)・[L192](../../scripts/sync_roadmap_tracking_issues.py#L192) の docstring とコメント中の `Proposal` 言及も更新します。 |
| [`scripts/new_roadmap_item.py:213`](../../scripts/new_roadmap_item.py#L213) | 新規項目を作るときのデフォルト値 `default="Proposal"` を `default="Approved"` に変更します。同ファイルの [L10](../../scripts/new_roadmap_item.py#L10) にある docstring の使用例 `[STATUS=Proposal]` も `[STATUS=Approved]` に更新します。 |
| `scripts/roadmap_query.py` | リテラルの比較はありません。[L46](../../scripts/roadmap_query.py#L46) の `VALID_STATUSES = tuple(STATUS_TO_BUCKET)` が `build_roadmap_index` から導出し、`resolve_status()`（[L70](../../scripts/roadmap_query.py#L70)）はそれを走査するだけなので、手順2の変更に自動的に追随します。更新するのは `Proposal` を例示している docstring の使用例（[L10](../../scripts/roadmap_query.py#L10)・[L126](../../scripts/roadmap_query.py#L126)）だけです。 |
| [`scripts/sync_roadmap_topic_labels.py:64-67`](../../scripts/sync_roadmap_topic_labels.py#L64-L67) | `SHIPPED_STATUS = "Implemented"` の定義に添えたコメント「every other Status (Proposal / In progress / Deferred / Rejected)」の `Proposal` 言及を `Approved` に更新します。`SHIPPED_STATUS` 自体の値は変更しません。 |

### CI ワークフロー

`roadmap-proposal-approvals.yml` は、次の3点を変更しません。これらは「新規提案 PR に2人承認を課す」
という一般的な運用を説明しており、`Status` の値とは独立しています。

- ジョブ名 `require two approvals for BE proposals`
- 免除ラベル `single-approver proposal`
- 冒頭コメントの「a BE *proposal* PR」という語り

変更するのは、`Status` の値を直接読み取っている箇所だけです。

- [L88](../../.github/workflows/roadmap-proposal-approvals.yml#L88) の `if [ "$status" = "Proposal" ]` を
  `"Approved"` に変更します。
- [L12](../../.github/workflows/roadmap-proposal-approvals.yml#L12)、
  [L59-60](../../.github/workflows/roadmap-proposal-approvals.yml#L59-L60) のコメント中にある
  「`Proposal`」という列挙値としての言及を `Approved` に更新します。「A new roadmap item is a proposal
  first」という地の文の「proposal」は一般語彙なので変更しません。
- [L98](../../.github/workflows/roadmap-proposal-approvals.yml#L98) の `::notice::` 文言
  「no changed item is Status: Proposal」も `Status: Approved` に更新します。

同様に [`roadmap-tracking-issues.yml:4`](../../.github/workflows/roadmap-tracking-issues.yml#L4) のコメント
「Status `Proposal` / `In progress`」も列挙値としての言及なので更新します。
[`.github/roadmap-refresh-prompt.md`](../../.github/roadmap-refresh-prompt.md) にある `Status` 遷移の説明も
同様に扱います。

### 既存ロードマップ項目ファイルの移行

現時点で英語版21件・日本語版21件の対が、自身のメタデータブロックに `Status` / `状態` の値として
`Proposal` / `提案` を持ちます。この行は次の形で固定されています
（[`roadmap-proposal-approvals.yml:86-87`](../../.github/workflows/roadmap-proposal-approvals.yml#L86-L87)
が読み取りに使う正規表現と同じ形です）。

```
| Status | **Proposal** |
| 状態 | **提案** |
```

単純に「この行全体に一致する箇所を全ファイルから置換する」という方式は使いません。
[`BE-0074`](../../roadmaps/BE-0074-be-template-standardization/BE-0074-be-template-standardization.md)
自身の `Status` は `Implemented` です。ただし本文中のテンプレート解説（EN L137）に、
`| Status | **Proposal** |` という例示行を含みます（JA 側には対応する行はありません）。これを機械的に
置換すると、`Implemented` の項目の本文が誤って書き換わります。置換は
[`roadmap-proposal-approvals.yml:86-87`](../../.github/workflows/roadmap-proposal-approvals.yml#L86-L87)
と同じ方法で、`<!-- BE-METADATA -->` フェンスに囲まれた冒頭のメタデータブロックだけを対象にします。

日本語版では、同じメタデータブロックに提案元 PR へのリンクを持つ `| 提案 | ... |` という別の行が
あります。値としての「提案」と、フィールド名としての「提案」が同じ文字列になっているため、`提案`
という文字列全体を無条件に置換すると、このフィールド名の行まで書き換えてしまいます。メタデータ
ブロック内で `| 状態 | **提案** |` という行全体に一致する場合だけ、`| 状態 | **承認済み** |` に
置き換えます。

### ドキュメント

`Status` の取りうる値を列挙している次の箇所を更新します。

- [`CLAUDE.md:283`](../../CLAUDE.md#L283) — `Status`（`Implemented` / `In progress` / `Proposal` / `Deferred` / `Rejected`）
- [`roadmaps/README.md:7`](../../roadmaps/README.md#L7)、[`roadmaps/README.md:18`](../../roadmaps/README.md#L18)
- [`roadmaps/README-ja.md:7`](../../roadmaps/README-ja.md#L7)、[`roadmaps/README-ja.md:19`](../../roadmaps/README-ja.md#L19)
- [`docs/ai-development.md:1036`](../ai-development.md#L1036) のバケット対応表の行、および
  [L1046-1053](../ai-development.md#L1046-L1053) の「The code decides the Status」段落にある
  `Proposal` への言及
- [`docs/ja/ai-development.md:939`](../ja/ai-development.md#L939) のバケット対応表の行、および
  [L950-964](../ja/ai-development.md#L950-L964) の「コードが状態を決めます」段落にある `提案` への言及
- [`docs/roadmap-workflow.md`](../roadmap-workflow.md) / [`docs/ja/roadmap-workflow.md`](../ja/roadmap-workflow.md) — 次の3箇所が対象です。
  - 本文: EN [L98](../roadmap-workflow.md#L98) 付近、JA [L95](../ja/roadmap-workflow.md#L95) 付近
  - mermaid 図のノード `Status: Proposal`: EN [L33](../roadmap-workflow.md#L33)、JA [L34](../ja/roadmap-workflow.md#L34)
  - 図の alt テキスト: EN [L23](../roadmap-workflow.md#L23)、JA [L24](../ja/roadmap-workflow.md#L24)

  書き換えたあと `make docs-diagrams` を実行します。`docs/assets/diagrams/roadmap-workflow-cycle.svg`
  とその日本語版を再生成し、同じ変更に含めます（`make check` は図の再生成を検証しません）。
- [`docs/contributor-workflow-tutorial.md`](../contributor-workflow-tutorial.md) / 日本語版
- [`docs/overview.md`](../overview.md) / 日本語版
- [`docs/specs/roadmap-dashboard-pagination-and-quick-filters.md`](roadmap-dashboard-pagination-and-quick-filters.md)
- [`Makefile`](../../Makefile) — `new-roadmap-item` のヘルプ（[L265](../../Makefile#L265)）と
  `roadmap-status` のヘルプ（[L322-323](../../Makefile#L322-L323)、`STATUS is one of: Proposal / …`
  という列挙）

`docs/architecture.md` や `docs/developer-guide.md` にある `Proposal` は、ロードマップの `Status` とは
無関係な別概念です。エージェントが次の行動を提案するためのデータクラス `class Proposal`
（`bajutsu/common/agents/protocols/proposal.py`）を説明したものです。改称の対象からは除外します。

日本語ドキュメントの改訂は、プロジェクト固有の `japanese-document-writing` スキルの規範に従います。推敲後は
textlint にかけ、新たな指摘を持ち込んでいないことを確かめます。

### `.apm/skills/` の更新

次の6スキルのソース（`.apm/skills/<name>/SKILL.md`）が、`Status` の値として `Proposal` に言及しています。

| スキル | 該当行 |
|---|---|
| `ideation` | [L71](../../.apm/skills/ideation/SKILL.md#L71)、[L127](../../.apm/skills/ideation/SKILL.md#L127) |
| `propose-and-build` | [L126](../../.apm/skills/propose-and-build/SKILL.md#L126) |
| `roadmap-filter` | [L21](../../.apm/skills/roadmap-filter/SKILL.md#L21)、[L29](../../.apm/skills/roadmap-filter/SKILL.md#L29)、[L46](../../.apm/skills/roadmap-filter/SKILL.md#L46)、[L52](../../.apm/skills/roadmap-filter/SKILL.md#L52) |
| `task-select` | [L23](../../.apm/skills/task-select/SKILL.md#L23)、[L30](../../.apm/skills/task-select/SKILL.md#L30) |
| `implement-be` | [L90-91](../../.apm/skills/implement-be/SKILL.md#L90-L91)、[L127](../../.apm/skills/implement-be/SKILL.md#L127)、[L178](../../.apm/skills/implement-be/SKILL.md#L178)、[L307](../../.apm/skills/implement-be/SKILL.md#L307)、[L322](../../.apm/skills/implement-be/SKILL.md#L322)（英日対応表） |
| `be-progress-tracker` | [L86-89](../../.apm/skills/be-progress-tracker/SKILL.md#L86-L89)（`Proposal (pre-allocation)` を `Approved (pre-allocation)` に変更） |

`.apm/skills/ideation/SKILL.md:131` は「the metadata block (`Proposal` / `Author` / `Status` / ...)」
と書いています。これはメタデータ表のフィールド名としての言及であり、変更しません。

ソースを更新したら `make skills` を実行します。`.claude/skills/` への反映と `apm.lock.yaml` の更新を、
同じ変更に含めます。`.claude/skills/` は直接編集しません。

### テストコード

`Status` の値としての `"Proposal"` を使うアサーション、パラメータ化、フィクスチャは、次の11ファイルに
あります。

- `tests/conftest.py`
- `tests/test_allocate_roadmap_ids.py`
- `tests/test_check_roadmap_format.py`
- `tests/test_fix_roadmap_drift.py`
- `tests/test_lint_roadmap.py`
- `tests/test_new_roadmap_item.py`
- `tests/test_roadmap_dashboard.py`
- `tests/test_roadmap_index.py`
- `tests/test_roadmap_query.py`
- `tests/test_sync_roadmap_topic_labels.py`
- `tests/test_sync_roadmap_tracking_issues.py`

これらの値を `"Approved"` に更新します。関数名がこの値を含む場合（例:
`test_new_item_default_status_is_proposal`）も、アサーション対象の値と名前が一致するよう改名します。

## 4. 検討した代替案と、採らなかった理由

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| 「提案」という語彙全体を一括で置き換える | `ideation` / `propose-and-build` スキル名、CI ワークフローのジョブ名・免除ラベルまで含めて「approval」系の語に統一する | `Status` の値とは別に「新規提案 PR である」という有効な概念がある。CI ワークフローのジョブ名を変えると、GitHub 側のブランチ保護ルールセットが参照する required check 名が変わり、リポジトリ管理者による手動再設定が要る。コードの変更だけでは完結せず、改称の目的（状態表記と実態の食い違いの解消）に対して過大な変更になる |
| 日本語表記を「承認」にする | `Approved` の直訳としてより短い「承認」を使う | PR レビューの「承認（approve）」という別の行為を指す語と紛れやすい。「承認済み」であれば、他の状態（`実装済み` / `実装中` / `保留` / `却下`）と同じく完了相の形容語として読め、区別が付く |
| 旧値 `Proposal` を別名として当面受理する | `STATUS_PAIR` などに `Proposal` を非推奨のエイリアスとして残し、段階的に移行する | ロードマップ項目データは本リポジトリ内で完結しており、外部の消費者が存在しない。移行は既存のロードマップ項目ファイルを直接書き換える一度限りの機械的な作業で完了でき、エイリアスを残す理由がない。同じ `Status` 列挙値の改称である [BE-0366](../../roadmaps/BE-0366-roadmap-rejected-status/BE-0366-roadmap-rejected-status.md)（`Proposal (deferred)` → `Deferred`）も、エイリアスを残さず一度で置き換えている |
| ダッシュボードのバケット色を変更する | `Approved` の語感に合わせて配色を見直す | 名称の改称と配色の見直しは別の関心事である。今回のスコープを広げるだけで、改称そのものには寄与しない |

## 5. 作業手順

| # | やること | 触るファイル | 完了条件 | 前提 |
|---|---|---|---|---|
| 1 | `STATUS_PAIR` の改称と、`ORDER_EN`/`REQUIRED_EN`/`REQUIRED_JA` を変更しないことをコメントで明記 | `scripts/check_roadmap_format.py` | `python -c "from scripts.check_roadmap_format import STATUS_PAIR; assert STATUS_PAIR['Approved'] == '承認済み'"` が通る | — |
| 2 | `STATUS_TO_BUCKET` と `BUCKETS` の改称 | `scripts/build_roadmap_index.py` | `pytest tests/test_roadmap_index.py` が通る | 1 |
| 3 | `BUCKET_COLOR`/`BUCKET_LABEL`/`OPEN_BUCKETS` の改称、関連コメントの更新 | `scripts/build_roadmap_dashboard.py` | `pytest tests/test_roadmap_dashboard.py` が通る | 2 |
| 4 | `OPEN_STATUSES` と docstring の改称 | `scripts/sync_roadmap_tracking_issues.py` | `pytest tests/test_sync_roadmap_tracking_issues.py` が通る | 1 |
| 5 | 新規項目を作るときのデフォルト値の改称 | `scripts/new_roadmap_item.py` | `pytest tests/test_new_roadmap_item.py` が通る | 1 |
| 6 | `roadmap_query.py` の docstring 使用例（コード変更は不要、手順2に自動追随）、`SHIPPED_STATUS` に添えたコメント中の `Proposal` 言及の更新 | `scripts/roadmap_query.py`、`scripts/sync_roadmap_topic_labels.py` | `pytest tests/test_roadmap_query.py` `tests/test_sync_roadmap_topic_labels.py` が通る | 2 |
| 7 | CI ワークフローの `Status` 値比較部分とコメント中の列挙値言及だけを改称（ジョブ名・免除ラベルは維持） | `.github/workflows/roadmap-proposal-approvals.yml`、`.github/workflows/roadmap-tracking-issues.yml`、`.github/roadmap-refresh-prompt.md` | `make lint-actions` が通る | 1-6 |
| 8 | 既存ロードマップ項目のメタデータブロック（`BE-0074` の本文中の例示は対象外）を、`\| Status \| **Proposal** \|` / `\| 状態 \| **提案** \|` という行全体に一致する場合だけ置換する。手順5・7と同じコミットで landing させ、2人承認ゲートが無効になる窓を作らない | `roadmaps/BE-*/*.md` | `make lint-roadmap` が通り、`Status: Proposal` を持つ項目が0件になる | 1, 5, 7 |
| 9 | 主要ドキュメントの `Status` 列挙値の言及を更新する（3章の一覧、`Makefile` と `docs/roadmap-workflow.md` の mermaid 図・Scalable Vector Graphics（SVG）を含む） | `CLAUDE.md`、`roadmaps/README.md`、`roadmaps/README-ja.md`、`docs/ai-development.md`（英日）、`docs/roadmap-workflow.md`（英日）、`docs/contributor-workflow-tutorial.md`（英日）、`docs/overview.md`（英日）、`docs/specs/roadmap-dashboard-pagination-and-quick-filters.md`、`Makefile` | 日本語ファイルは textlint が新たな指摘を持ち込んでいない。`grep -rn "Status.*Proposal\|状態.*提案" docs/ CLAUDE.md Makefile` の残り一致が、3章で除外した無関係な `Proposal` クラスの言及だけになる。`make docs-diagrams` 実行後、再生成した SVG がコミットに含まれる | 8 |
| 10 | `.apm/skills/` 配下6スキルのソースを改称し、`make skills` で `.claude/skills/` と `apm.lock.yaml` に反映する | `.apm/skills/{ideation,propose-and-build,roadmap-filter,task-select,implement-be,be-progress-tracker}/SKILL.md`、`.claude/skills/`、`apm.lock.yaml` | `make lint-skills` が通る | 8 |
| 11 | テストコード11ファイルのリテラル・パラメータ化・関数名を改称する | `tests/conftest.py` ほか10ファイル | `make test` が通る | 1-6 |
| 12 | 全体を検証する | — | `make check` が通る。加えて `grep -rn "Proposal\|提案" --include="*.md" --include="*.py" --include="*.yml" .` と `grep -n "Proposal" Makefile` を実行し、残る一致がすべて「メタデータ表のフィールド名」「`bajutsu/` 内の無関係な `Proposal` クラス」「`ideation`/`propose-and-build` の一般語彙」「`BE-0074` 本文中の例示」のいずれかであることを確かめる | 1-11 |

`Status` の値そのものに触れる箇所は手順1〜9に収まります。それを間接的に参照するスキルとテストは
手順10〜11に、全体の整合確認は手順12に収まります。

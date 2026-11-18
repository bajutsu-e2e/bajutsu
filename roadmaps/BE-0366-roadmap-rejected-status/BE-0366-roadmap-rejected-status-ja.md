[English](BE-0366-roadmap-rejected-status.md) · **日本語**

# BE-0366 — ロードマップの状態に却下を追加し、保留と区別する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0366](BE-0366-roadmap-rejected-status-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0366") |
| 実装 PR | [#1647](https://github.com/bajutsu-e2e/bajutsu/pull/1647) |
| トピック | コントリビューターワークフロー |
<!-- /BE-METADATA -->

## はじめに

ロードマップ項目の `状態` フィールドは、現在4つの値のいずれかを取ります。`Implemented`、
`In progress`、`Proposal`、`Proposal (deferred)` です。この値だけで、項目のダッシュボードの区分が決まります
（[BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders-ja.md)）。

4つ目の値は、性質の異なる2つの状況を1つの区分にまとめてしまいます。1つは、名指しされたブロッカーや将来の具体的なニーズを待って棚上げされている提案です。もう1つは、他のBE項目によってすでに意味を失った提案です。2つは現在、同じ表示になります。

本項目は、この区分を2つに分けます。`Proposal (deferred)` は、他の3つの値と同じ平らな命名に揃えて、`Deferred`（保留）という単独の値に改称します。加えて、`Rejected`（却下）という新しい値を設けます。`Rejected` は、メンテナーが見送ると決めた提案を示します。理由は次の2つのいずれかです。

- 他のBE項目が、提案内容をすでにカバーしている。
- レビューの結果、プロジェクトのプライムディレクティブや対象範囲を満たす道筋が見つからなかった。

変更が及ぶのは、メタデータの語彙、ダッシュボード生成スクリプトを含む7本のロードマップ関連スクリプト、それらのゲートテストです。
[BE-0159](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders-ja.md)
以降、`状態` はディレクトリと独立しています。そのため、項目のディレクトリは改名しません。どの経路にも LLM を持ち込みません。

## 動機

`Proposal (deferred)` を持つロードマップ項目は、現在6件あります。
[BE-0027](../BE-0027-mock-server-external/BE-0027-mock-server-external-ja.md)、
[BE-0040](../BE-0040-ai-assertions/BE-0040-ai-assertions-ja.md)、
[BE-0070](../BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split-ja.md)、
[BE-0154](../BE-0154-roadmap-promote-base-sha/BE-0154-roadmap-promote-base-sha-ja.md)、
[BE-0157](../BE-0157-shake-device-primitive/BE-0157-shake-device-primitive-ja.md)、
[BE-0158](../BE-0158-timezone-device-primitive/BE-0158-timezone-device-primitive-ja.md) です。

このうち5件は棚上げされています。ただし BE-0027 は際どいケースです（「今日のロードマップの移行」を参照）。5件はそれぞれ、復活の条件となる具体的なブロッカーや将来のニーズを名指ししています。BE-0157 と BE-0158 のブロッカーは、シェイクとタイムゾーンのプリミティブに欠けているヘッドレスなアクチュエータです。BE-0040 のブロッカーは、AI が書いたアサーションを `run` が見る前に決定的なチェックへ落とし込む設計です。

残る1件の BE-0154 は、ベース SHA から `roadmap-promote` を実行する提案です。`Superseded by` フィールドには、後継として BE-0159 がすでに記入されています。BE-0159 は、BE-0154 の提案全体が前提としていた状態別フォルダを廃止し、昇格ワークフローそのものを削除しました。BE-0154 が堅牢化しようとしたワークフローは、もう存在しません。BE-0154 はブロッカーを待っているのではなく、提案そのものがすでに死んでいます。

それでも BE-0154 の `状態` は、まだ開かれた問いのままである残り5件とまったく同じ `Proposal (deferred)` と表示されます。そのため、ダッシュボードの保留区分を眺める読み手は、各ファイルを開かないかぎり、6件のうちどれが見直す価値を持つのか判別できません。

この混同は、BE-0078 が一段階上で解決した問題と同じ種類のものです。BE-0078 以前は、生きた提案と、実装作業として受理済みの提案が、同じ `proposals/` フォルダを共有していました。各ファイルを開かないかぎり、読み手は両者を見分けられませんでした。BE-0078 はこの区分を割り、実装中の項目に専用のフォルダとインデックス区分を与えました。その過程で、`Accepted, in progress` を平らな `In progress` に改称しました。同じ論法が、今回の `Deferred` 区分にも当てはまります。この区分は、「まだ見直す価値がある」と「他のBEですでに答えが出ている」を、ひそかに混ぜたままだからです。

この区別が必要になるのは、後から現れたBEによって無効化された、かつて保留だった項目に限りません。生きた `Proposal` や `In progress` の項目も、後継のBEとは無関係な理由で、追求する価値がないと判明する場合があります。

- 唯一の素直な実装が、`run` / CI ゲートに非決定的な判断を持ち込んでしまうとレビューで判明する場合。BE-0040 はこの緊張のすぐ手前まで迫りますが、自らの本文で、境界の内側にとどまるオーサリング側の設計を切り出しています。
- 比較検討のうえで、メンテナーがそのアイデアは採らないと判断する場合。

どちらの場合も、閲覧する読み手が必要とする合図は同じです。この提案は今後も進められず、それを覆す条件は名指しされていない、という合図です。`Rejected` は、これらすべてをカバーします。今回の議論のきっかけとなった、無効化された保留項目のケースだけではありません。

## 詳細設計

### 5つの値からなる語彙

| 状態（EN） | 状態（JA） | ダッシュボードの区分 |
|---|---|---|
| `Implemented` | `実装済み` | Implemented |
| `In progress` | `実装中` | In progress |
| `Proposal` | `提案` | Proposals |
| `Deferred` | `保留` | Deferred |
| `Rejected` | `却下` | Rejected |

`Deferred` は `Proposal (deferred)` / `提案（保留）` を改称したものです。`Rejected` / `却下` は新設です。

`Deferred` から `Proposal (…)` という枠組みを外す命名は、BE-0078 が `Accepted, in progress` を `In progress` に平らにしたときと同じです。状態の語、ダッシュボードの見出し、バッジの表示は、すべて同じに読めるべきです。もはや生きた提案ではない値が、提案のように読め続けるべきではありません。

### 保留と却下の境界線

`Deferred`（保留）は、項目を生きた問いのままにとどめます。項目自身の本文が、復活の条件となる具体的な状況を名指ししています。その状況とは、まだ存在しない能力か、項目が挙げる将来の具体的なニーズです。

- BE-0157 と BE-0158 は、能力を挙げています。「信頼できる決定的なアクチュエータがない」という事情です。
- [BE-0070](../BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split-ja.md)
  は、ニーズを挙げています。「将来、分散実行の途中で真にライブなアーティファクトが必要になった場合には、再検討できます」という条件です。

作業はしていませんが、その項目が提起する問いは開いたままです。

`Rejected`（却下）は、メンテナーが見送ると決めた提案であり、それを覆す条件が名指しされていない状態を示します。この状態に至る引き金は2つです。

1. 他のBE項目が、提案内容をすでにカバーしている場合です。既存の `Superseded by` フィールドに後継を記入します。これは、
   [BE-0100](../BE-0100-roadmap-progress-tracking-template/BE-0100-roadmap-progress-tracking-template-ja.md)
   が定めた、相互リンクの `Related` / `Superseded by` の慣習です。出荷済みの項目が後で置き換えられる際に、すでに使われています。先例は
   [BE-0125](../BE-0125-authoring-agent-tool-restriction/BE-0125-authoring-agent-tool-restriction-ja.md)
   と
   [BE-0005](../BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring-ja.md)
   です。
2. レビューの結果、プロジェクトのプライムディレクティブや対象範囲を満たす道筋が見つからなかった場合です。その理由は、項目自身の「検討した代替案」に記録します。

`Rejected` は、かつて `Deferred` だった項目と同じくらい自然に、`Proposal` や `In progress` の項目にも当てはまります。一方で、`Implemented` には決して当てはめません。コードが出荷され、当時意味を持っていた項目は、後のBEに置き換えられても `Status: Implemented` のままとします（同じ BE-0125 / BE-0005 の先例）。後から却下したことにすると、実際に起きたことを誤って伝えてしまうからです。

### スキーマと生成器の変更

- [`scripts/check_roadmap_format.py`](../../scripts/check_roadmap_format.py)：`STATUS_PAIR` の
  `"Proposal (deferred)": "提案（保留）"` を `"Deferred": "保留"` に改称します。`"Rejected": "却下"`
  を追加します。
- [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py)：
  - `STATUS_TO_BUCKET`：`"Proposal (deferred)"` キーを `"Deferred"` に改称します。バケット名 `"Deferred"` 自体は変わりません。`"Rejected": "Rejected"` を追加します。
  - `BUCKETS`：`("Deferred", "deferred")` の後ろに `("Rejected", "rejected")` を加えます。ダッシュボードの進捗順は `Implemented → In progress → Proposals → Deferred → Rejected` となります。
- [`scripts/build_roadmap_dashboard.py`](../../scripts/build_roadmap_dashboard.py)：
  - `BUCKET_COLOR`：既存の灰色の `Deferred`（`#5F5E5A`）とは別の `"Rejected"` の項目を追加します。`#8B3A3A` のようなくすんだ赤なら、「終了」を表せます。既存の緑、琥珀、藍、灰の並びが「出荷済み／進行中／提案中／棚上げ」を表すのと同じ調子です。実際の配色とコントラストの検証は、本提案ではなく実装 PR の仕事です。
  - `BUCKET_LABEL`：`"Rejected": "Rejected"` を追加します。モジュールの docstring にあるバケット一覧も更新します。
  - `_topic_progress`：トピックの実装済み件数を、そのトピックの全項目で割っています。そのため、`Rejected` の項目は分母に残り続け、トピックの進捗バーを永久に押し下げてしまいます。`Rejected` は分母から除きます。却下された項目は、本項目自身の定義により二度と戻ってこないため、そのトピックに残された作業ではないからです。`Deferred` は分母に残します。保留の項目は、そのトピックがまだ答えを出していない生きた問いだからです。
- [`scripts/new_roadmap_item.py`](../../scripts/new_roadmap_item.py)：同じ表を二重に持っていた
  `STATUS_JA` を廃止し、`STATUS_PAIR` を読み込む `_status_ja()` ヘルパーに置き換えます。このヘルパーは、トピックやトラッキング Issue の URL について、このファイルがすでに使っている兄弟モジュールの読み込みと同じ形です。表を複製せず導出にすることで、今回の追加でも以降の追加でも、`make new-roadmap-item STATUS=…` が `check_roadmap_format.py` の認める値をそのまま受け付けます。
- [`scripts/sync_roadmap_tracking_issues.py`](../../scripts/sync_roadmap_tracking_issues.py)：
  ロジックの変更はありません。
  - `OPEN_STATUSES = frozenset({"Proposal", "In progress"})` は、`Rejected` を含めそれ以外のすべてを、すでに「オープンではない」として扱います。`Deferred` に対してすでに行っているのと同じように、トラッキング Issue を閉じます。
  - docstring と行内コメントは、棚上げのケースを `Proposal (deferred)` とだけ呼んでいます。`OPEN_STATUSES` の上のコメントは、オープンでない状態を「残りの2つ」と述べています。どちらも、オープンでない3つの値として `Deferred` と `Rejected` を名指しするよう直し、コードとコメントを一致させます。
- ほかに6つの箇所が `Proposal (deferred)` という文字列そのものを名指ししており、改称のもとで古びてしまいます。6箇所はいずれも `Deferred` に改称します。
  - [`.apm/skills/implement-be/SKILL.md`](../../.apm/skills/implement-be/SKILL.md)
    は、項目の `Status` による分岐の注記と、トラッキング Issue が見つからない場合の注記の2箇所で、エージェントが保留解除の確認を取るかどうかをこの文字列で判定しています。この `Status` 分岐には、`Rejected` に対しても同様の一時停止・確認の分岐を追加します。そうすれば、却下済みの項目の実装を頼まれたエージェントは、通常の提案として実装せず、人間が却下を明示的に覆したことを確認するまで止まります。
  - [`.github/roadmap-refresh-prompt.md`](../../.github/roadmap-refresh-prompt.md) には、更新ガード「（`Proposal (deferred)` は人間による意図的な決定であり、ここでは決して保留解除しない）」があります。このガードを `Deferred` に改称したうえで `Rejected` も加え、更新ジョブがどちらの値も再開しないようにします。後述の「検討した代替案」は、まさにこのガードを前提に論じています。
  - [`.apm/skills/roadmap-filter/SKILL.md`](../../.apm/skills/roadmap-filter/SKILL.md)
    は、この文字列を有効な `STATUS` フィルタ値の一覧に挙げています。一覧には `Rejected` も追加します。
  - [`docs/roadmap-workflow.md`](../../docs/roadmap-workflow.md) とその
    [`docs/ja/roadmap-workflow.md`](../../docs/ja/roadmap-workflow.md) 対訳は、`implement-be` の手順説明でこの文字列を名指ししています。
  - [`scripts/sync_roadmap_topic_labels.py`](../../scripts/sync_roadmap_topic_labels.py) は、トピックラベルの変更対象に残る状態を説明するコメントで、この文字列を名指ししています。
  - [`Makefile`](../../Makefile) の `roadmap-status` ターゲットの使用例コメントは、有効な `STATUS` 値の一覧としてこの文字列を挙げています。`scripts/roadmap_query.py` は有効な状態を `STATUS_TO_BUCKET` から導出しているため、改称後はこのコメントどおりに実行すると終了コードが非ゼロになってしまいます。このコメントにも `Rejected` を追加します。
  - 文字列と並んで、バケット数を述べた記述も2箇所古びます。`roadmap_query.py` のモジュール docstring と `roadmap-filter` の冒頭は、どちらもダッシュボードが「4つの状態バケット」にわたって項目を並べると述べています。この数が5つになります。
- [`docs/ai-development.md`](../../docs/ai-development.md) とその
  [`docs/ja/ai-development.md`](../../docs/ja/ai-development.md) 対訳：状態→区分の表に `Rejected` の行を追加し、`Deferred` の行から `Proposal (…)` の枠組みを外します。`Proposal (deferred)` を名指ししている周辺の散文も追従します。
- [`CLAUDE.md`](../../CLAUDE.md)：`Status`（`Implemented` / `In progress` / `Proposal` /
  `Proposal (deferred)`）という状態の一覧を5値にします。`Deferred` と `Rejected` は、どちらも単独の値にします。
- [`roadmaps/README.md`](../README.md) と [`README-ja.md`](../README-ja.md)：
  ページ冒頭の導入部の注記にある1行の状態一覧に、`Rejected` を加えます。
- ゲートテスト：
  - `tests/test_roadmap_index.py`、`tests/test_roadmap_query.py`、`tests/test_new_roadmap_item.py`、`tests/test_sync_roadmap_tracking_issues.py` は、状態の文字列を直接埋め込んだフィクスチャとアサーションを、改称後の `Deferred` と新設の `Rejected` に合わせます。
  - `tests/test_roadmap_format.py`（コミット済みのツリーに対するラッパー）と `tests/test_roadmap_dashboard.py`（`BUCKETS` を汎用的に走査するテスト）は、状態の文字列を直接埋め込んでいないため、修正の必要はありません。新しい値へのカバレッジを任意で追加できるのみです。

### 今日のロードマップの移行

上記のスキーマ変更は、現在 `Proposal (deferred)` である項目を再分類するまで、効果を持ちません。再分類は、同じ実装 PR の中で行います。

- BE-0154 は `Rejected` に移します。`Superseded by` フィールドには、後継として BE-0159 がすでに記入されています。`状態` の値そのもの以外に、変更の必要はありません。
- BE-0157、BE-0158、BE-0070、BE-0040 は `Deferred` のままとします。いずれも復活の条件となる具体的なブロッカーやニーズを名指ししているため、`Rejected` の「それを覆す条件が名指しされていない」という条件に当てはまりません。
- BE-0027 は、本当に判断が割れるケースです。
  - 本文の「はじめに」は、宣言的な `mocks` によってすでに無効化されたと述べています。
  - 一方で、復活の具体的な条件も名指ししています。宣言的なスタブとして表現するには状態やプロトコルが重すぎるバックエンドです。
  - `Superseded by` フィールドは空のままです。

  本項目は、BE-0027 をひとまず `Deferred` のままとし、実装時にメンテナーの判断に委ねるよう明示することを推奨します。スキーマの変更が、この境界事例を黙って決めてしまわないようにするためです。
- [BE-0357](../BE-0357-xcuitest-duplicate-node-hittable-tiebreak/BE-0357-xcuitest-duplicate-node-hittable-tiebreak-ja.md)
  は7件目の項目です。本提案を書いたあとに保留となったため、「動機」に挙げた6件には含まれていません。BE-0357 は `Deferred` のままとします。
  - 前提は、重複したアクセシビリティノードの組のうち、ちょうど1件が hittable を報告するというものでした。この前提は計測によって否定され、それが棚上げの理由となりました。
  - BE-0357 自身の「進捗」は、復活の条件を名指ししています。ライブの検査で要素どうしを本当に区別できる、重複ペアが現れることです。
  - 条件が名指しされている以上、上記の境界線に照らして `Rejected` は当てはまりません。

### 「取り込まない」との関係

[`roadmaps/README-ja.md`](../README-ja.md) の「取り込まない」節は、別の仕組みです。この節は、そのままとします。番号付きのBE項目になる前に不採用と決まったアイデアを記録するものです。経路を定めるのは、ロードマップ自身の昇格ルールです。未成熟なアイデアはまず「未整理アイデア」に置き、対象範囲が固まってから初めて番号付き項目へ昇格します。

`Rejected` は、`roadmaps/BE-NNNN-<slug>/` としてすでに存在する項目にのみ当てはまります。本項目は、「取り込まない」の箇条書きを却下するためだけに、番号付き項目へ昇格させることを提案しません。その昇格は、箇条書きがすでに一度記録している内容のために、恒久的なBE IDを1つ費やすことになるからです。

### プライムディレクティブとの整合

対象となるのは、メタデータの語彙、7本のスクリプト、それらのゲートテスト、そして旧値を名指しするドキュメントだけです。どの経路にも LLM は入り込みません。`run` と CI は決定的なままです。アプリ固有のものは、ツールやドライバに入り込みません。

## 検討した代替案

**`Proposal (deferred)` を単独の値のままとし、「すでに死んでいる」ことは既存の
`Superseded by` フィールドだけで記録する。** 却下しました。ダッシュボードの区分分けも、
`roadmap-filter` スキル
（[BE-0162](../BE-0162-roadmap-status-filter-skill/BE-0162-roadmap-status-filter-skill-ja.md)）も、
`状態` だけを鍵とします。`Superseded by` だけに記録された事実は、各ファイルを開かないかぎり、この両方から見えないままです。それこそが、本項目の取り除こうとしている摩擦です。

**新しい値を `Proposal (deferred)` と同じ枠組みに揃えて `Proposal (rejected)` と名付ける。**
検討しました。「もう進めない」2つの値を、見た目の上で対にしておけるからです。採用しませんでした。結果は次のどちらかになり、いずれも不揃いです。

- `Deferred` を枠組み付きのままにすると、平らな `In progress` / `Proposal` / `Implemented` の並びと不揃いになります。
- `Deferred` だけを平らに改称して `Rejected` を枠組み付きのままにすると、同じ役割を2つの異なる形で表すことになります。

BE-0078 は、同じ理由で、枠組み付きの `Accepted, in progress` より平らな `In progress` を選んでいます。平らな `Deferred` / `Rejected` の組は、その選択を蒸し返すのではなく延長するものです。

**`Rejected` を、手で設定する `状態` の値としてではなく、`Superseded by` フィールドが
埋まっていることから自動的に導く。** 却下しました。`Superseded by` だけでは、合図として不十分です。`Proposal` や `In progress` の項目は、後継のBEを一切名指ししない理由で却下される場合があるからです。たとえば、対象範囲外のアイデアや、プライムディレクティブに反する設計しかないアイデアです。

`状態` を手で設定したままにすることは、既存の慣行と一致します。自動で変わる `状態` は、ロードマップ更新ジョブがマージ済みPRの証跡から行う前進方向の切り替え（`Proposal` → `In progress` → `Implemented`、BE-0222）だけです。そのジョブは、保留を人間による意図的な決定として扱い、決して上書きしません。`Rejected` は、この同じ規則を延長するものです。

**既存の `Proposal (deferred)` 項目は移行せず、`Rejected` を今後の項目だけに使う。**
却下しました。本項目のきっかけとなった具体的なケースである BE-0154 が、今と同じ場所に置かれたままになります。そして、今と同じように読み手を誤導します。今日のロードマップを同じ PR で移行することこそが、実際の隙間を塞ぎます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `check_roadmap_format.py`（`STATUS_PAIR`）で `Proposal (deferred)` を `Deferred` に改称し、
      `Rejected` を追加する。`new_roadmap_item.py` の重複した表は廃止し、導出に置き換える。
- [x] `build_roadmap_index.py`（`STATUS_TO_BUCKET`、`BUCKETS`）と `build_roadmap_dashboard.py`
      （`BUCKET_COLOR`、`BUCKET_LABEL`、モジュール docstring）に `Rejected` バケットを追加し、
      `_topic_progress` の分母から `Rejected` を除く。
- [x] `sync_roadmap_tracking_issues.py`、`sync_roadmap_topic_labels.py`、`roadmap_query.py` の
      docstring とコメントについて、旧値の名指しと、あわせて古びるバケット数の記述を更新する。
- [x] `implement-be`、`.github/roadmap-refresh-prompt.md`、`roadmap-filter`、
      `docs/roadmap-workflow.md`（+ ja）、`Makefile` のコメント、`CLAUDE.md`、
      `docs/ai-development.md`（+ ja）、`roadmaps/README.md`（+ ja）にわたって文字列を改称し、
      有効な値の一覧すべてに `Rejected` を加える。
- [x] 文字列を固定しているゲートテストを更新し、新しい値をカバーする。
- [x] 現在の保留項目を移行する。BE-0154 を `Rejected` に、残る6件を `Deferred` にする。

ログを次に記します。

- 実装 PR は、6つの作業単位をまとめて実施しました。その過程で、本提案が未決としていた判断が2つあります。
  - **積み上げ棒グラフは進捗と同じ分母を使います。** `_topic_progress` の分母からだけ `Rejected` を外すと、`_progress_bar` の各区間の合計が100%を超えてしまいます。棒グラフも、同じ合計値で各バケットの件数を割っているためです。そこで、棒グラフでも `Rejected` バケットを描きません。却下された項目はカードとしては表示されますが、割合にも棒グラフにも寄与しません。項目がすべて却下のトピックは、0で割る代わりに100%と表示します。その結果、トピックはダッシュボードの「Completed」の組に入ります。残された作業がない以上、それが正しい表示です。
  - **出荷済み項目3件の散文でも文字列を改称しました。** 「詳細設計」が列挙した範囲を超えた変更です。旧値を名指ししていた出荷済みの項目は5件あります。そのうち、現在も動いている仕組みを説明する3件を改称しました。BE-0109 のトラッキング Issue のライフサイクル、BE-0162 の状態フィルタ、BE-0368 が BE-0357 をどこへ置いたかの記述です。これにより、値が現役である箇所については、リポジトリ全体を検索しても旧値は見つからなくなります。BE-0074 と BE-0078 は原文のままとしました。どちらも当時の語彙そのものを規定しており、同じく廃止された `Accepted, in progress` や `Track` フィールドと並んでいます。片方の名前だけを現在の語彙に直すと、記録を整えるどころか誤って伝えることになります。
- BE-0027 は既定どおり `Deferred` のままとしました。「今日のロードマップの移行」が求めるメンテナーの判断は、引き続き開いたままです。今回の移行はこの境界事例を決めたのではなく、スキーマの変更に決めさせなかっただけです。
- 後続のレビューで、上記の BE-0109 と BE-0162 の改称が一歩足りていないと指摘されました。両項目とも `Proposal (deferred)` を `Deferred` に改称した一方で、自身が列挙する値の一覧に `Rejected` を加えていませんでした。どちらも「現在も動いている仕組み」を説明する項目でありながら、語彙を過小に述べていました。両項目（および `-ja.md` の対訳）を、5つの値すべてを挙げる形に直しました。
  - 同じ回で、`new_roadmap_item.py` の `STATUS_JA` も見直しました。`check_roadmap_format.STATUS_PAIR` と同じ表を二重に持っていたため、廃止しました。代わりに、`STATUS_PAIR` を読み込む `_status_ja()` ヘルパーを置きました。このヘルパーは、トピックやトラッキング Issue の URL でこのファイルがすでに使っている兄弟モジュールの読み込みと同じ形です。これで、2つの表が食い違う余地はなくなります。

## 参考

- [BE-0078 — 状態ごとのロードマップフォルダ](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders-ja.md)は、4区分の `状態` の語彙と、「区分は `状態` から導く」という不変条件を導入した項目です。本項目は、この語彙を5区分に拡張します。枠組み付きの状態名を平らな名前に改称する先例も、ここから引き継ぎます。
- [BE-0159 — ロードマップ状態フォルダの平坦化](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders-ja.md)は、`状態` をディレクトリから独立させた項目であり、BE-0154 が `Superseded by` に記す後継でもあります。この無効化の関係が、本項目のきっかけとなった具体的なケースです。
- [BE-0154 — ベース SHA から roadmap-promote を実行する](../BE-0154-roadmap-promote-base-sha/BE-0154-roadmap-promote-base-sha-ja.md)は、本項目の移行によって `Rejected` に再分類される保留項目です。
- [BE-0100 — ロードマップ進捗追跡テンプレート](../BE-0100-roadmap-progress-tracking-template/BE-0100-roadmap-progress-tracking-template-ja.md)は、本項目が「無効化」の引き金として再利用する、相互リンクの `Related` / `Superseded by` フィールドを定義した項目です。
- [BE-0162 — ロードマップ状態フィルタスキル](../BE-0162-roadmap-status-filter-skill/BE-0162-roadmap-status-filter-skill-ja.md)は、「検討した代替案」で触れた、`状態` を鍵とする `roadmap-filter` スキルです。
- [`docs/ai-development.md`](../../docs/ai-development.md#roadmap-items-be-ids-strict)は、本項目が改める状態→区分の表とロードマップメタデータの規則を持つドキュメントです。

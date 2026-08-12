[English](BE-XXXX-roadmap-rejected-status.md) · **日本語**

# BE-XXXX — ロードマップの状態に却下を追加し、保留と区別する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-roadmap-rejected-status-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | コントリビューターワークフロー |
<!-- /BE-METADATA -->

## はじめに

ロードマップ項目の `状態` フィールドは、現在4つの値のいずれかを取ります。`Implemented`、
`In progress`、`Proposal`、`Proposal (deferred)` です。この値だけでダッシュボードの区分が決まります
([BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders-ja.md))。4つ目の値は、
性質の異なる2つの状況を1つの区分にまとめてしまいます。名指しされたブロッカーや将来の具体的な
ニーズを待って単に棚上げされている提案と、他のBE項目によってすでに意味を失った提案が、同じ表示に
なります。本項目はこの区分を2つに分けます。`Proposal (deferred)` は、他の3つの値と同じ平らな
命名に揃えて `Deferred`（保留）という単独の値に改称します。加えて `Rejected`（却下）という新しい
値を設けます。これは、他のBE項目がすでに提案内容をカバーしている場合、あるいはレビューの結果
プロジェクトのプライムディレクティブや対象範囲を満たす道筋が見つからなかった場合に、メンテナーが
見送ると決めた提案を示します。変更が及ぶのは、メタデータの語彙、ダッシュボード生成スクリプトを
含む6本のロードマップ関連スクリプト、それらのゲートテストです。
[BE-0159](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders-ja.md)
以降、`状態` はディレクトリと独立しているため項目のディレクトリを改名する必要はなく、どの経路にも
LLM を持ち込みません。

## 動機

`Proposal (deferred)` を持つロードマップ項目は、現在6件あります。
[BE-0027](../BE-0027-mock-server-external/BE-0027-mock-server-external-ja.md)、
[BE-0040](../BE-0040-ai-assertions/BE-0040-ai-assertions-ja.md)、
[BE-0070](../BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split-ja.md)、
[BE-0154](../BE-0154-roadmap-promote-base-sha/BE-0154-roadmap-promote-base-sha-ja.md)、
[BE-0157](../BE-0157-shake-device-primitive/BE-0157-shake-device-primitive-ja.md)、
[BE-0158](../BE-0158-timezone-device-primitive/BE-0158-timezone-device-primitive-ja.md) です。
このうち5件は棚上げされています（うち BE-0027 は際どいケースです。「今日のロードマップの移行」を
参照）。それぞれが、復活の条件となる具体的なブロッカーや将来のニーズを名指ししています。シェイクとタイムゾーンのプリミティブに欠けているヘッドレスな
アクチュエータ（BE-0157、BE-0158）から、AI が書いたアサーションを `run` が見る前に決定的な
チェックへ落とし込む設計（BE-0040）までです。残る1件、BE-0154（ベース SHA から
`roadmap-promote` を実行する提案）は、すでに `Superseded by` フィールドに後継として BE-0159 を
記入済みです。BE-0159 は、BE-0154 の提案全体が前提としていた状態別フォルダを廃止し、昇格ワークフローそのものを
削除したため、もはや昇格すべきものが残っていません。BE-0154 が堅牢化しようとしたワークフロー
自体が存在しないのです。BE-0154 はブロッカーを待っているのではなく、提案
そのものがすでに死んでいます。それでも `状態` は、まだ開かれた問いのままである残り5件とまったく
同じ `Proposal (deferred)` と表示されます。そのため、ダッシュボードの保留区分を眺める読み手は、
各ファイルを開かないかぎり、6件のうちどれが見直す価値を持つのか判別できません。

この混同は、BE-0078 がすでに一段階上で解決した問題の、もう一段下にあたります。BE-0078 以前は、
生きた提案と、実装作業として受理済みの提案が同じ `proposals/` フォルダを共有しており、各ファイルを
開かないかぎり見分けがつきませんでした。BE-0078 はこの区分を割り、実装中の項目に専用のフォルダと
インデックス区分を与え、その過程で `Accepted, in progress` を平らな `In progress` に改称しました。
今回も同じ論法が当てはまります。`Deferred` 区分は、「まだ見直す価値がある」と
「他のBEですでに答えが出ている」を、ひそかに混ぜたままだからです。

この区別は、後から現れたBEによって無効化される、かつて保留だった項目には限りません。生きた
`Proposal` や `In progress` の項目であっても、後継のBEとは無関係な理由で、追求する価値がないと
判明する場合があります。唯一の素直な実装が `run` / CI ゲートに非決定的な判断を持ち込んでしまう
場合（BE-0040 がこの緊張に肉薄しつつ、境界の内側にとどまる著者側の設計を自らの本文で切り出して
います）や、比較検討のうえでメンテナーがそのアイデアは採らないと判断する場合です。いずれの場合も、
閲覧する読み手が必要とする合図は同じです。この提案は今後も進められず、それを覆す条件は名指し
されていない、という合図です。`Rejected` はこの両方をカバーします。今回の議論のきっかけとなった、
無効化された保留項目のケースだけではありません。

## 詳細設計

### 5つの値からなる語彙

| 状態（EN） | 状態（JA） | ダッシュボードの区分 |
|---|---|---|
| `Implemented` | `実装済み` | Implemented |
| `In progress` | `実装中` | In progress |
| `Proposal` | `提案` | Proposals |
| `Deferred` | `保留` | Deferred |
| `Rejected` | `却下` | Rejected |

`Deferred` は `Proposal (deferred)` / `提案（保留）` を改称したものです。`Rejected` / `却下` は
新設です。`Deferred` から `Proposal (…)` という枠組みを外すのは、BE-0078 が
`Accepted, in progress` を `In progress` に平らにしたときと同じ命名です。状態の語、ダッシュボードの
見出し、バッジの表示はすべて同じに読めるべきであり、もはや生きた提案ではない値が提案のように
読め続けるべきではありません。

### 保留と却下の境界線

`Deferred`（保留）は、項目を生きた問いのままにとどめます。本文が、復活の条件となる具体的な状況を
名指ししています。まだ存在しない能力（BE-0157、BE-0158 が挙げる「信頼できる決定的なアクチュエータ
がない」という事情）や、項目自身が挙げる将来の具体的なニーズ
（[BE-0070](../BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split-ja.md)
が挙げる、「将来、分散実行の途中で真にライブなアーティファクトが必要になった場合には、再検討でき
ます」という条件）です。作業はしていませんが、その項目が提起する問いは開いたままです。

`Rejected`（却下）は、メンテナーが見送ると決めた提案であり、それを覆す条件が名指しされていない
状態を示します。この状態に至る引き金は2つです。1つは、他のBE項目がすでに提案内容をカバーして
いる場合で、この場合は既存の `Superseded by` フィールドに後継を記入します。これは、出荷済みの
項目が後で置き換えられる際にすでに使われている、
[BE-0100](../BE-0100-roadmap-progress-tracking-template/BE-0100-roadmap-progress-tracking-template-ja.md)
が定めた相互リンクの `Related` / `Superseded by` の慣習と同じです（先例:
[BE-0125](../BE-0125-authoring-agent-tool-restriction/BE-0125-authoring-agent-tool-restriction-ja.md)、
[BE-0005](../BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring-ja.md)）。
もう1つは、レビューの結果、プロジェクトのプライムディレクティブや対象範囲を満たす道筋が見つから
なかった場合で、その理由は項目自身の「検討した代替案」に記録します。`Rejected` は、かつて
`Deferred` だった項目と同じくらい自然に `Proposal` や `In progress` の項目にも当てはまります。
一方で `Implemented` には決して当てはめません。コードが出荷され、当時意味を持っていた項目は、
後のBEに置き換えられても `Status: Implemented` のままとします（同じ BE-0125 / BE-0005 の先例）。
後から却下したことにすると、実際に起きたことを誤って伝えてしまうからです。

### スキーマと生成器の変更

- [`scripts/check_roadmap_format.py`](../../scripts/check_roadmap_format.py)：`STATUS_PAIR` の
  `"Proposal (deferred)": "提案（保留）"` を `"Deferred": "保留"` に改称し、`"Rejected": "却下"`
  を追加します。
- [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py)：`STATUS_TO_BUCKET` の
  `"Proposal (deferred)"` キーを `"Deferred"` に改称します（バケット名 `"Deferred"` 自体は
  変わりません）。`"Rejected": "Rejected"` を追加します。`BUCKETS` には
  `("Deferred", "deferred")` の後ろに `("Rejected", "rejected")` を加え、ダッシュボードの
  進捗順（`Implemented → In progress → Proposals → Deferred → Rejected`）を保ちます。
- [`scripts/build_roadmap_dashboard.py`](../../scripts/build_roadmap_dashboard.py)：
  `BUCKET_COLOR` に、既存の灰色の `Deferred`（`#5F5E5A`）とは別の `"Rejected"` の項目を追加
  します。`#8B3A3A` のようなくすんだ赤なら、既存の緑、琥珀、藍、灰の並びが「出荷済み／進行中／
  提案中／棚上げ」を表すのと同じ調子で「終了」を表せます（実際の配色とコントラストの検証は、
  本提案ではなく実装 PR の仕事です）。`BUCKET_LABEL` に `"Rejected": "Rejected"` を追加し、
  モジュールの docstring にあるバケット一覧も更新します。
- [`scripts/new_roadmap_item.py`](../../scripts/new_roadmap_item.py)：`STATUS_JA` の
  `"Proposal (deferred)": "提案（保留）"` を `"Deferred": "保留"` に改称し、`"Rejected": "却下"`
  を追加します。これにより `make new-roadmap-item STATUS=…` は、`check_roadmap_format.py` が
  認識するすべての値を引き続き受け付けます。
- [`scripts/sync_roadmap_tracking_issues.py`](../../scripts/sync_roadmap_tracking_issues.py)：
  ロジックの変更はありません。`OPEN_STATUSES = frozenset({"Proposal", "In progress"})` は、
  `Rejected` を含めそれ以外のすべてをすでに「オープンではない」として扱い、`Deferred` に対して
  すでに行っているのと同じようにトラッキング Issue を閉じます。docstring と行内コメントは
  棚上げのケースを `Proposal (deferred)` とだけ呼んでいるため、`Deferred` と `Rejected` の
  両方を指すよう文言を直し、コードとコメントを一致させます。
- ほかに6つの箇所が `Proposal (deferred)` という文字列そのものを名指ししており、改称のもとで
  古びてしまいます。
  [`.agent-workflows/implement-be/workflow.md`](../../.agent-workflows/implement-be/workflow.md)
  は、項目の `Status` による分岐の注記と、トラッキング Issue が見つからない場合の注記の2箇所で、
  エージェントが保留解除の確認を取るかどうかをこの文字列そのもので判定しています。この `Status`
  分岐には、`Rejected` に対しても同様の一時停止・確認の分岐を追加します。人間が却下を明示的に
  覆さないかぎり、エージェントは却下済みの項目を通常の提案として実装してしまうためです。
  [`.github/roadmap-refresh-prompt.md`](../../.github/roadmap-refresh-prompt.md) の
  更新ガード「（`Proposal (deferred)` は人間による意図的な決定であり、ここでは決して保留解除
  しない）」は `Deferred` に改称します。`Rejected` にも同じ「決して再開しない」というガードを
  課すかどうかは、実装 PR の判断に委ねます。
  [`.agent-workflows/roadmap-filter/workflow.md`](../../.agent-workflows/roadmap-filter/workflow.md)
  は、これを有効な `STATUS` フィルタ値の一覧に挙げています。
  [`docs/roadmap-workflow.md`](../../docs/roadmap-workflow.md) とその
  [`docs/ja/roadmap-workflow.md`](../../docs/ja/roadmap-workflow.md) 対訳は、`implement-be` の
  手順説明でこれを名指ししています。
  [`scripts/sync_roadmap_topic_labels.py`](../../scripts/sync_roadmap_topic_labels.py) は、
  トピックラベルの変更対象に残る状態を説明するコメントでこれを名指ししています。さらに
  [`Makefile`](../../Makefile) の `roadmap-status` ターゲットの使用例コメントも、有効な
  `STATUS` 値の一覧としてこの文字列を挙げています。`scripts/roadmap_query.py` は有効な状態を
  `STATUS_TO_BUCKET` から導出しているため、改称後はこのコメントどおりに実行すると終了コードが
  非ゼロになってしまいます。この6箇所はいずれも `Deferred` に改称します。Makefile のコメントと
  `roadmap-filter` の有効な `STATUS` 一覧には `Rejected` も追加します。
- [`docs/ai-development.md`](../../docs/ai-development.md) とその
  [`docs/ja/ai-development.md`](../../docs/ja/ai-development.md) 対訳：状態→区分の表に
  `Rejected` の行を追加し、`Deferred` の行から `Proposal (…)` の枠組みを外します。
  `Proposal (deferred)` を名指ししている周辺の散文も追従します。
- [`CLAUDE.md`](../../CLAUDE.md)：`Status`（`Implemented` / `In progress` / `Proposal` /
  `Proposal (deferred)`）という状態の一覧を5値にし、`Deferred` と `Rejected` をどちらも
  単独の値にします。
- [`roadmaps/README.md`](../README.md) と [`README-ja.md`](../README-ja.md)：
  ページ冒頭の導入部の注記にある1行の状態一覧に `Rejected` を加えます。
- ゲートテスト：`tests/test_roadmap_index.py`、`tests/test_roadmap_query.py`、
  `tests/test_new_roadmap_item.py`、`tests/test_sync_roadmap_tracking_issues.py` は、状態の
  文字列を直接埋め込んだフィクスチャとアサーションを、改称後の `Deferred` と新設の `Rejected`
  に合わせます。一方 `tests/test_roadmap_format.py`（コミット済みのツリーに対するラッパー）と
  `tests/test_roadmap_dashboard.py`（`BUCKETS` を汎用的に走査するテスト）は状態の文字列を
  直接埋め込んでいないため修正の必要はなく、新しい値へのカバレッジを任意で追加できるのみです。

### 今日のロードマップの移行

上記のスキーマ変更は、現在 `Proposal (deferred)` である6件を、同じ実装 PR の中で再分類する
まで効果を持ちません。

- BE-0154 は `Rejected` に移します。`Superseded by` フィールドにはすでに後継として BE-0159 が
  記入されているため、`状態` の値そのもの以外に変更の必要はありません。
- BE-0157、BE-0158、BE-0070、BE-0040 は `Deferred` のままとします。いずれも復活の条件となる
  具体的なブロッカーやニーズを名指ししているため、`Rejected` の「それを覆す条件が名指しされて
  いない」という条件に当てはまりません。
- BE-0027 は、本当に判断が割れるケースです。本文の「はじめに」は、宣言的な `mocks` によって
  すでに無効化されたと述べる一方で、復活の具体的な条件（宣言的なスタブとして表現するには状態や
  プロトコルが重すぎるバックエンド）も名指ししており、`Superseded by`
  フィールドは空のままです。本項目は、BE-0027 をひとまず `Deferred` のままとし、実装時に
  メンテナーの判断に委ねるよう明示することを推奨します。スキーマの変更がこの境界事例を
  黙って決めてしまわないようにするためです。

### 「取り込まない」との関係

[`roadmaps/README-ja.md`](../README-ja.md) の「取り込まない」節は別の仕組みであり、そのままと
します。これは、番号付きのBE項目になる前に不採用と決まったアイデアを記録するものであり、未成熟な
アイデアをまず「未整理アイデア」に置き、対象範囲が固まってから初めて番号付き項目へ昇格する
というロードマップ自身の昇格ルールに従います。`Rejected` は `roadmaps/BE-NNNN-<slug>/` として
すでに存在する項目にのみ当てはまります。本項目は、「取り込まない」の箇条書きを却下するためだけに
番号付き項目へ昇格させることを提案しません。それは、その箇条書きがすでに一度記録している
内容のために、恒久的なBE IDを1つ費やすことになるからです。

### プライムディレクティブとの整合

対象となるのは、メタデータの語彙、6本のスクリプト、それらのゲートテスト、そして旧値を名指しする
ドキュメントだけです。どの経路にも
LLM は入り込みません。`run` と CI は決定的なままであり、アプリ固有のものはツールやドライバに
入り込みません。

## 検討した代替案

**`Proposal (deferred)` を単独の値のままとし、「すでに死んでいる」ことは既存の
`Superseded by` フィールドだけで記録する。** 却下しました。ダッシュボードの区分分けも、
`状態` だけを鍵とする `roadmap-filter` スキル
（[BE-0162](../BE-0162-roadmap-status-filter-skill/BE-0162-roadmap-status-filter-skill-ja.md)）
も、`状態` だけを見ます。`Superseded by` だけに記録された事実は、両方から見えないままであり、
それこそが本項目の取り除こうとしている摩擦です。

**新しい値を `Proposal (deferred)` と同じ枠組みに揃えて `Proposal (rejected)` と名付ける。**
検討しました。「もう進めない」2つの値を見た目の上で対にしておけるからです。採用しません
でした。`Deferred` を枠組み付きのままにするなら平らな `In progress` / `Proposal` / `Implemented`
の並びと不揃いになり、`Deferred` だけを平らに改称して `Rejected` を枠組み付きのままにする
なら、同じ役割を2つの異なる形で表すことになります。BE-0078 はすでに同じ理由で
`Accepted, in progress` より平らな `In progress` を選んでいます。平らな `Deferred` /
`Rejected` の組は、その選択を蒸し返すのではなく延長するものです。

**`Rejected` を、手で設定する `状態` の値としてではなく、`Superseded by` フィールドが
埋まっていることから自動的に導く。** 却下しました。`Superseded by` だけでは合図として
不十分です。`Proposal` や `In progress` の項目は、対象範囲外である、あるいはプライム
ディレクティブに反する設計しかないなど、後継のBEを一切名指ししない理由で却下される
場合があるからです。`状態` を手で設定したままにすることは、既存の慣行と一致します。
自動で変わる `状態` は、ロードマップ更新ジョブがマージ済みPRの証跡から行う前進方向の
切り替え（`Proposal` → `In progress` → `Implemented`、BE-0222）だけであり、そのジョブは
保留を人間による意図的な決定として扱い、決して上書きしません。`Rejected` はこの同じ
規則を延長するものです。

**既存の `Proposal (deferred)` 項目は移行せず、`Rejected` を今後の項目だけに使う。**
却下しました。本項目のきっかけとなった具体的なケースである BE-0154 が、今と同じ場所に
置かれたままになり、今と同じように読み手を誤導してしまいます。今日のロードマップを
同じ PR で移行することこそが、実際の隙間を塞ぎます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 未着手です。

## 参考

- [BE-0078 — 状態ごとのロードマップフォルダ](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders-ja.md)は、4区分の `状態` の語彙と、「区分は `状態` から導く」という不変条件を導入した項目です。本項目はこれを5区分に拡張し、枠組み付きの状態名を平らな名前に改称する先例もここから引き継ぎます。
- [BE-0159 — ロードマップ状態フォルダの平坦化](../BE-0159-flatten-roadmap-status-folders/BE-0159-flatten-roadmap-status-folders-ja.md)は、`状態` をディレクトリから独立させた項目であり、BE-0154 が `Superseded by` に記す後継でもあります。この無効化の関係が、本項目のきっかけとなった具体的なケースです。
- [BE-0154 — ベース SHA から roadmap-promote を実行する](../BE-0154-roadmap-promote-base-sha/BE-0154-roadmap-promote-base-sha-ja.md)は、本項目の移行によって `Rejected` に再分類される保留項目です。
- [BE-0100 — ロードマップ進捗追跡テンプレート](../BE-0100-roadmap-progress-tracking-template/BE-0100-roadmap-progress-tracking-template-ja.md)は、本項目が「無効化」の引き金として再利用する、相互リンクの `Related` / `Superseded by` フィールドを定義した項目です。
- [BE-0162 — ロードマップ状態フィルタスキル](../BE-0162-roadmap-status-filter-skill/BE-0162-roadmap-status-filter-skill-ja.md)は、「検討した代替案」で触れた、`状態` を鍵とする `roadmap-filter` スキルです。
- [`docs/ai-development.md`](../../docs/ai-development.md#roadmap-items-be-ids-strict)は、本項目が改める状態→区分の表とロードマップメタデータの規則を持つドキュメントです。

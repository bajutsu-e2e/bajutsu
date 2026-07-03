[English](BE-XXXX-roadmap-topic-label-sync.md) · **日本語**

# BE-XXXX — ロードマップ項目 PR のラベルを Topic と同期させる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-roadmap-topic-label-sync-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トピック | 開発基盤（コントリビュータ体験） |
<!-- /BE-METADATA -->

## はじめに

未実装のロードマップ項目の PR に、その項目の現在の `Topic` メタデータに対応する `topic:<key>`
ラベルを常に付けておく GitHub Actions ワークフローを追加します。著者やレビュアーの手作業は不要です。
このワークフローは2つの場面をカバーします。新しい項目を追加する PR にはそのトピックラベルを付与
し、すでに番号が振られた項目（`Implemented` 以外の状態）の `Topic` を変更する PR には、ラベルを
その変更に合わせて付け替えます。

## 動機

ロードマップの各項目は、すでに23個のトピック（[`scripts/build_roadmap_index.py`](../../../scripts/build_roadmap_index.py)
の `TOPICS`。「Backend expansion (iOS actuators)」「Integration & automation (MCP)」「Security
hardening」など）のいずれかに分類されています。インデックスページはこの分類ごとに項目を並べます
が、その分類が見えるのは、項目のファイルを開いてメタデータブロックを読むか、次回のインデックス
再生成を待った後だけです。レビュアーがどの PR に目を通すかを判断する GitHub の PR 一覧そのもの
には、タイトル以外のトピック情報が一切現れません。

`topic:<key>` ラベルを付ければ、この分類が PR 一覧や通知にそのまま現れます。「Security hardening」
や「codegen coverage」を担当するレビュアーは、各 diff を開かなくても自分の関心がある PR を絞り込め
ます。加えて `is:pr label:topic:mcp` のような検索で、あるトピックに寄せられた PR をオープン・
クローズを問わず一覧できる副産物も得られます。

`Topic` は proposal を出した時点で固定されるものではありません。ある項目をより適切なトピックへ
分類し直したり、肥大化した受け皿トピックを分割したりするのは、ロードマップを形作るうえで普通に
起きる作業であり、`roadmaps/in-progress/` や `roadmaps/deferred/` にあるすでに番号が振られた項目
でも起こります。ラベル付けが PR を開いた瞬間の一度きりしか動かないなら、レビュー中に `Topic` が
変わった PR は、その後もずっと古いラベルを付けたままになってしまいます。これはラベルが本来助ける
はずのトピックによる絞り込みを、かえって誤らせる結果になります。だからラベル付けは、追加だけで
なく編集にも追従する必要があります。

再ラベル付けの対象からは `Implemented` の項目を外します。すでに出荷された項目には、トピックで
トリアージすべきオープンな PR がもう残っていません。出荷済みの項目ファイルへのその後の文章修正
は、このラベルが助けようとしている「どこに注意を振り向けるか」という判断とは別の話だからです。
これは [`roadmap-tracking-issues.yml`](../../../.github/workflows/roadmap-tracking-issues.yml)
（BE-0109）がすでに引いているオープン/出荷済みの境界と同じ考え方です。あちらも `Proposal` /
`In progress` の項目のあいだだけトラッキング issue を開いたままにし、出荷されたら閉じます。
`Implemented` の項目にはそもそも手を触れません。

このワークフローは PR のトリアージを補助するだけの仕組みであり、`run` や決定的なゲート、
pass/fail の判定には一切関与しません（プライムディレクティブ1）。アプリごとの挙動にも触れません
（プライムディレクティブ3）。

## 詳細設計

作業は独立した3つの部分に分かれます。

1. **トピックとラベルの対応づけには、既存の正準トピック一覧を再利用します。**
   [`scripts/build_roadmap_index.py`](../../../scripts/build_roadmap_index.py) の `TOPICS` に
   すでに定義されている23個の `(名前, key, has_origin)` の組は、BE 項目のメタデータに現れる
   人間可読な `Topic` の値（例:「Backend expansion (iOS actuators)」）と、短く安定した key
   （`backend` など）を対応づける唯一の正典です。ラベル名はこの key を使った `topic:<key>`
   （例: `topic:backend`、`topic:mcp`、`topic:security`）とします。PR 一覧のラベル欄で一目で読み
   取れる短さであり、インデックスがすでに使っている key をそのまま使うため、24個目のトピックが
   増えてもラベルの対応表を別途更新する必要がありません。

2. **「この PR がロードマップ項目をどう変更したか」を「付け外しすべきラベル」に変換するスクリプ
   トを用意します。** 新設する `scripts/sync_roadmap_topic_labels.py` は、PR の変更ファイルの
   一覧（パス、`status`、リネームの場合は変更前のパス）を受け取り、`roadmaps/proposals/`、
   `roadmaps/in-progress/`、`roadmaps/deferred/` 配下の項目ファイル（`-ja` ではない方）だけに
   絞り込み、それぞれを次のように分類します。
   - **追加された項目（`added`）**：作業ツリー（PR の head）から現在の `Topic` を読み取り、その
     トピックのラベルを付与する *add* アクションを1つ出します。比較すべき以前の状態はありません。
   - **既存項目の編集またはリネーム（`modified` / `renamed`。slug のリネームを含む）**：作業
     ツリーから現在の `Topic` を、base コミットから以前の `Topic` を読み取ります。以前の内容は
     `git show <base-sha>:<変更前のパス>`（リネームでなければパスは現在と同じ）で取得し、
     `build_roadmap_index.py` がすでに持つ `<!-- BE-METADATA -->` のパース処理（`META_BLOCK_RE` /
     `META_ROW_RE`）を再利用します。独自に実装し直すことはしません。2つの `Topic` が同じであれば
     （ほとんどの編集は `Topic` に触れないため、これが多数派です）何もしません。異なっていれば、
     古いトピックのラベルを外す *remove* アクションと、新しいトピックのラベルを付ける *add* アク
     ションを1つずつ出します。`Status` だけの変更（例:`Proposal` → `In progress`）はそれ単独では
     トリガーになりません。トリガーになるのはあくまで `Topic` の値の変化だけです。
   - `Topic` の値が `TOPIC_KEY_BY_NAME` に存在しない場合（`make new-roadmap-item` の検証を経ずに
     手書きされた項目など）はエラーにせず、該当ファイルについて `::warning::` 行を出力してアク
     ションを出さないだけにとどめます。ラベル付けの抜けが PR 自体をブロックしたり失敗させたりす
     ることはありません。
   スクリプトは、これらのアクションを重複なく `add <label>` または `remove <label>` の形で1行ずつ
   標準出力に出し、ワークフロー側で実行できるようにします。

3. **ワークフロー `.github/workflows/roadmap-topic-labels.yml` を追加します。**
   トリガーは `pull_request: [opened, reopened, synchronize]` で、パスを `roadmaps/proposals/**`、
   `roadmaps/in-progress/**`、`roadmaps/deferred/**` に絞ります。`roadmaps/implemented/**` は
   意図的に含めません（理由は「動機」を参照してください）。手順は次のとおりです。
   - PR の head を通常どおりチェックアウトします（`actions/checkout` のデフォルトの挙動で、変更
     された各ファイルの現在の `Topic` を読むための作業ツリーが手に入ります）。
   - `gh api pulls/{pr}/files` で PR の変更ファイル一覧（`status`、`filename`、
     `previous_filename`）を取得し、上記3つのパスの配下にある項目ファイルだけを残します。
   - この集合が空であれば、何もせずグリーンのまま終了します。生成済みのインデックスだけを変更した
     PR や、項目ファイルに触れない PR には、(再) ラベル付けの対象がないからです。この無操作の扱い
     は、`roadmap-proposal-approvals.yml` や `roadmap-tracking-issues.yml` がすでに対象外の PR に
     対して採用している方針と同じです。
   - PR の base コミット（`git fetch origin ${{ github.event.pull_request.base.sha }}`）を取得
     し、変更・リネームされた各ファイルの以前の内容を `git show` で読めるようにしたうえで、
     `scripts/sync_roadmap_topic_labels.py` を変更ファイル一覧に対して実行し、add/remove の
     アクションを得ます。
   - *add* アクションのラベルごとに `gh label create <name> --color <固定色> --force` を実行し
     ます（べき等な操作です。トピックが初めて使われたときは新規作成し、すでに存在する場合は無害
     に更新するだけです）。これにより、新しいトピックが増えても手作業でのラベル作成が不要になり
     ます。すべての `topic:*` ラベルに同じ固定色を割り当て、`bug` や `documentation` などとは別の
     系統だと分かるようにします。そのあと `gh pr edit <pr> --add-label <name>` を実行します。
   - *remove* アクションのラベルごとに `gh pr edit <pr> --remove-label <name>` を実行します
     （ベストエフォートです。メンテナが手動で外すなどして、そのラベルがすでに PR に付いていなくて
     もエラーにはなりません）。
   - 必要な権限は、`contents: read`（チェックアウトと、base コミットに対する `git show`）、
     `pull-requests: write`（ファイル一覧の取得とラベルの編集）、`issues: write`（PR に対する
     ものであってもラベル作成は GitHub の REST API では Issues 側の機能であり、
     `roadmap-tracking-issues.yml` が issue とラベルの書き込みにすでに宣言している権限と同じで
     す）です。

1つの PR が複数の項目に触れる場合（稀ですが禁じられてはいません）は、各項目の add/remove アクショ
ンの和集合を適用します。項目の数ではなく、異なるトピックの遷移の数だけラベルの変更が入ります。

## 検討した代替案

- **`actions/labeler`（パスの glob パターンからラベルへの対応づけ）。** 標準的なこのアクションは、
  ファイルの「パス」を glob パターンと照合します。今回はトピックがパスに現れないため使えません。
  すべての項目は `roadmaps/<category>/BE-NNNN-<slug>/BE-NNNN-<slug>.md` という同じ形のパスを持
  ち、トピックによる違いがありません。トピックはファイル内のメタデータにしか存在せず、
  `actions/labeler` はそこを読みません。不採用としました。
- **マージ時に動く `roadmap-id` ワークフローでラベルを付ける案。**
  [`roadmap-id.yml`](../../../.github/workflows/roadmap-id.yml) はマージ後の `push: main` で
  動きます。この時点でラベルを付けても、本来の目的である「オープン中のどの PR を見るかをレビュ
  アーが判断する」場面には間に合いません。マージ済みの PR はすでにクローズされているためです。
  不採用としました。
- **項目の著者が手作業でラベルを付ける、あるいは付け替える案（規約として文書化し、
  `make new-roadmap-item` のプロンプトや PR テンプレートのチェックリストに委ねる）。** 手作業は
  抜け漏れが起きやすく、`Topic` を変更したあとにラベルの付け替えを忘れてもチェックするゲートが
  存在しません。対応表がコードとして毎回強制される自動化案に比べて drift が起きやすいため、不採
  用としました。
- **ラベルの追加だけを行い、削除は一切しない案。** より単純で、実際にこの項目が `Topic` の編集
  まで扱うようになる前の当初の範囲でした。再ラベル付けを範囲に含めることにした時点で不採用とし
  ました。`Topic` が変わったあとに古いトピックのラベルを残したままにすると、PR に正しいラベルと
  古いラベルの両方が付いた状態になり、このラベルが支えようとしているトピックによる絞り込みを
  かえって誤らせるからです。
- **`roadmaps/implemented/**` を対象とする PR も再ラベル付けする案。** 不採用としました。理由は
  「動機」のとおりで、出荷済みの項目にはトピックでトリアージすべきオープンな PR がもう残ってい
  ないため、ラベルが振り向ける先がありません。
- **base コミット1つに対する `git show` の代わりに、全履歴を取得するチェックアウト
  （`fetch-depth: 0`）で以前の `Topic` を再計算する案。** どちらでも得られる結果は同じです。必要
  なのは base コミット1つだけであり、全履歴のチェックアウトは利益のない余分なコストになるため、
  不採用としました。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `scripts/sync_roadmap_topic_labels.py`：変更ファイル一覧から `topic:<key>` の add/remove
      アクションを作る処理。`build_roadmap_index.py` のメタデータ解析と `TOPIC_KEY_BY_NAME` を
      再利用する。
- [ ] `.github/workflows/roadmap-topic-labels.yml`：未実装以外のロードマップディレクトリ配下で
      追加・編集・リネームされた項目ファイルを検出し、必要なトピックラベルの存在を保証したうえ
      で、PR に add/remove アクションを適用する。

## 参考

- [`scripts/build_roadmap_index.py`](../../../scripts/build_roadmap_index.py) — この項目が
  再利用する正準の `TOPICS` と BE-METADATA のパース処理。
- [`roadmap-proposal-approvals.yml`](../../../.github/workflows/roadmap-proposal-approvals.yml) —
  対象外の PR を無操作にする形を含め、ロードマップ限定の PR ワークフローの前例。
- [`roadmap-tracking-issues.yml`](../../../.github/workflows/roadmap-tracking-issues.yml) —
  この項目が再利用するオープン / `Implemented` の境界の前例（BE-0109）であり、同じパース処理を
  メタデータの読み取りに使っている前例でもある。
- [`roadmap-id.yml`](../../../.github/workflows/roadmap-id.yml) — ラベル付けのタイミング候補と
  して検討し、不採用とした（「検討した代替案」を参照）マージ時ワークフロー。

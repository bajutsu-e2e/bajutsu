[English](BE-0156-roadmap-topic-label-sync.md) · **日本語**

# BE-0156 — ロードマップ項目 PR のラベルを Topic と同期させる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0156](BE-0156-roadmap-topic-label-sync-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0156") |
| 実装 PR | [#612](https://github.com/bajutsu-e2e/bajutsu/pull/612), [#817](https://github.com/bajutsu-e2e/bajutsu/pull/817) |
| トピック | コントリビューターワークフロー |
<!-- /BE-METADATA -->

## はじめに

未実装のロードマップ項目の PR に、その項目の現在の `Topic` メタデータに対応する `topic:<key>`
ラベルを常に付けておく GitHub Actions ワークフローを追加します。著者やレビュアーの手作業は不要です。
このワークフローは2つの場面をカバーします。新しい項目を追加する PR にはそのトピックラベルを付与
し、すでに番号が振られた項目（`Implemented` 以外の状態）の `Topic` を変更する PR には、ラベルを
その変更に合わせて付け替えます。

## 動機

ロードマップの各項目は、すでに23個のトピック（[`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py)
の `TOPICS`。「Backend expansion (iOS actuators)」「Integration & automation」「Security
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
起きる作業であり、新しい proposal だけでなく、`Implemented` に至る前のどの `Status` でも、すでに
番号が振られた項目で起こります。ラベル付けが PR を開いた瞬間の一度きりしか動かないなら、レビュー中に `Topic` が
変わった PR は、その後もずっと古いラベルを付けたままになってしまいます。これはラベルが本来助ける
はずのトピックによる絞り込みを、かえって誤らせる結果になります。だからラベル付けは、追加だけで
なく編集にも追従する必要があります。

再ラベル付けの対象からは `Implemented` の項目を外します。すでに出荷された項目には、トピックで
トリアージすべきオープンな PR がもう残っていません。出荷済みの項目ファイルへのその後の文章修正
は、このラベルが助けようとしている「どこに注意を振り向けるか」という判断とは別の話だからです。
これは [`roadmap-tracking-issues.yml`](../../.github/workflows/roadmap-tracking-issues.yml)
（BE-0109）がすでに引いているオープン/出荷済みの境界と同じ考え方です。あちらも `Proposal` /
`In progress` の項目のあいだだけトラッキング issue を開いたままにし、出荷されたら閉じます。
`Implemented` の項目にはそもそも手を触れません。

このワークフローは PR のトリアージを補助するだけの仕組みであり、`run` や決定的なゲート、
pass/fail の判定には一切関与しません（プライムディレクティブ1）。アプリごとの挙動にも触れません
（プライムディレクティブ3）。

## 詳細設計

作業は独立した3つの部分に分かれます。

1. **トピックとラベルの対応づけには、既存の正準トピック一覧を再利用します。**
   [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py) の `TOPICS` に
   すでに定義されている23個の `(名前, key, has_origin)` の組は、BE 項目のメタデータに現れる
   人間可読な `Topic` の値（例:「Backend expansion (iOS actuators)」）と、短く安定した key
   （`backend` など）を対応づける唯一の正典です。ラベル名はこの key を使った `topic:<key>`
   （例: `topic:backend`、`topic:mcp`、`topic:security`）とします。PR 一覧のラベル欄で一目で読み
   取れる短さであり、インデックスがすでに使っている key をそのまま使うため、24個目のトピックが
   増えてもラベルの対応表を別途更新する必要がありません。

2. **「この PR がロードマップ項目をどう変更したか」を「付け外しすべきラベル」に変換するスクリプ
   トを用意します。** 新設する `scripts/sync_roadmap_topic_labels.py` は、差分をそのまま再生する
   のではなく、**あるべき状態へ調整する**方式を取ります。PR の変更ファイルの一覧（パス、`status`、
   リネームの場合は変更前のパス）を受け取り、フラットな `roadmaps/BE-NNNN-<slug>/` ツリー（BE-0159
   で `Status` ごとのフォルダは廃止されました）の英語版項目ファイル（`-ja` ではない方）だけに絞り込ん
   だうえで、まず PR に**あるべき** `topic:<key>` の集合を求めます。head の `Status` が `Implemented`
   の項目は除外します。出荷済みの項目にはトリアージすべきオープンな PR がもう残っていないからで、
   `implemented/` フォルダがなくなった以上、この判定は同じ head の内容から `Status` を読んで行います。
   - **追加された項目（`added`）**：作業ツリー（PR の head）から現在の `Topic` を読み取り、その
     トピックのラベルをあるべき集合に含めます。
   - **既存項目の編集またはリネーム（`modified` / `renamed`。slug のリネームを含む）**：作業
     ツリーから現在の `Topic` を、base コミットから以前の `Topic` を読み取ります。以前の内容は
     `git show <base-sha>:<変更前のパス>`（リネームでなければパスは現在と同じ）で取得し、
     `build_roadmap_index.py` がすでに持つ `<!-- BE-METADATA -->` のパース処理（`META_BLOCK_RE` /
     `META_ROW_RE`）を再利用します。独自に実装し直すことはしません。2つの `Topic` が同じであれば
     （ほとんどの編集は `Topic` に触れないため、これが多数派です）その項目は何も寄与しません。
     したがって、本文だけの編集がそれ単独で PR にラベルを付けることはなく、`Status` だけの変更も
     トリガーになりません。異なっていれば、**新しい**トピックのラベルをあるべき集合に含めます。
   - `Topic` の値が `TOPIC_KEY_BY_NAME` に存在しない場合（`make new-roadmap-item` の検証を経ずに
     手書きされた項目など）はエラーにせず、該当ファイルについて `::warning::` 行を出力し、あるべき
     集合には入れないだけにとどめます。ラベル付けの抜けが PR 自体をブロックしたり失敗させたりする
     ことはありません。

   続いてスクリプトは、この**あるべき集合**を、PR が現在付けている `topic:*` ラベル（ワークフロー
   から渡します）と**突き合わせて調整します**。あるべきなのに付いていないラベルごとに `add <label>`
   を、付いているのにもうあるべきでないラベルごとに `remove <label>` を、それぞれ1行ずつ標準出力に
   出します。GitHub の `pulls/{pr}/files` は PR 全体の base→head 差分なので、あるべき集合は現在の
   head だけの関数であり、毎回のプッシュで丸ごと計算し直されます。そのため、単純な「古いラベルを
   外し、新しいラベルを付ける」差分方式では**収束しない**ところでも、この調整方式は**収束します**
   （「検討した代替案」を参照）。

3. **ワークフロー `.github/workflows/roadmap-topic-labels.yml` を追加します。**
   トリガーは `pull_request`（既定で `opened` / `reopened` / `synchronize`）で、パスはフラットな
   `roadmaps/**` ツリー全体に絞ります。スクリプト側が英語版項目ファイルへ絞り込み、`Implemented`
   の項目は除外する（理由は「動機」を参照してください）ので、フォルダ単位のパスフィルタは不要です。
   トークンが読み取り専用になるフォークからの PR はスキップします。手順は次のとおりです。
   - PR の **head**（`ref: <head.sha>`）をチェックアウトします。`pull_request` の既定であるマージ
     ref（head を base にマージしたもの）ではありません。変更ファイル一覧も base との比較もどちらも
     head を基準にしているので、スクリプトが各項目の現在の `Topic` を読む作業ツリーは、base にマージ
     した状態ではなく head そのものである必要があります。
   - PR の base コミット（`git fetch --depth=1 origin ${{ github.event.pull_request.base.sha }}`）
     を取得し、変更・リネームされた各ファイルの以前の内容を `git show` で読めるようにします。この
     取得は**致命的ではありません**。base の SHA に到達できない場合（実行がキューに入ったあとに
     base ブランチが force-push された場合など）は、赤にするのではなく警告を出して緑のまま終了
     します。PR のトリアージを補助するだけの仕組みが PR の合否を左右することはありません。
   - `gh api pulls/{pr}/files` で PR の変更ファイル一覧（`status`、`filename`、
     `previous_filename`）を取得し、`gh pr view --json labels` で PR の現在のラベルを読み取ります。
     両方を `scripts/sync_roadmap_topic_labels.py` に渡し、add/remove のアクションを得ます。
   - 何も出力されなければ（生成済みのインデックスだけを変更した PR や、すでに同期済みの PR）、何も
     せず緑のまま終了します。この無操作の扱いは、`roadmap-proposal-approvals.yml` や
     `roadmap-tracking-issues.yml` がすでに対象外の PR に対して採用している方針と同じです。
   - *add* アクションのラベルごとに `gh label create <name> --color <固定色> --force` を実行し
     ます（べき等な操作です。トピックが初めて使われたときは新規作成し、すでに存在する場合は無害
     に更新するだけです）。これにより、新しいトピックが増えても手作業でのラベル作成が不要になり
     ます。すべての `topic:*` ラベルに同じ固定色を割り当て、`bug` や `documentation` などとは別の
     系統だと分かるようにします。そのうえで add と remove のラベルを、ラベルごとに1往復するのでは
     なく **2回**の `gh pr edit`（`--add-label` と `--remove-label` を1回ずつ）で適用します。remove
     はベストエフォートで、すでに付いていないラベル（メンテナが手動で外した場合など）でもエラーに
     はなりません。
   - 必要な権限は、`contents: read`（チェックアウトと、base コミットに対する `git show`）、
     `pull-requests: write`（ファイル一覧の取得と、ラベルの読み取り・編集）、`issues: write`（PR に
     対するものであってもラベル作成は GitHub の REST API では Issues 側の機能であり、
     `roadmap-tracking-issues.yml` が issue とラベルの書き込みにすでに宣言している権限と同じで
     す）です。

1つの PR が複数の項目に触れる場合（稀ですが禁じられてはいません）は、触れた各項目のあるべきトピック
の和集合へ向けて調整します。そのため、ある項目が別のトピックへ分類し直されても、そのトピックをまだ
持っている項目が PR 内にあれば、そのラベルは付いたままになります。

（BE-0159 でロードマップがフラット化される前は、この除外は `roadmaps/implemented/` に対するパス判定
でしたが、いまは同じ判断を項目の `Status` から読み取ります。）

## 検討した代替案

- **`actions/labeler`（パスの glob パターンからラベルへの対応づけ）。** 標準的なこのアクションは、
  ファイルの「パス」を glob パターンと照合します。今回はトピックがパスに現れないため使えません。
  すべての項目は `roadmaps/BE-NNNN-<slug>/BE-NNNN-<slug>.md` という同じ形のパスを持
  ち、トピックによる違いがありません。トピックはファイル内のメタデータにしか存在せず、
  `actions/labeler` はそこを読みません。不採用としました。
- **マージ時に動く `roadmap-id` ワークフローでラベルを付ける案。**
  [`roadmap-id.yml`](../../.github/workflows/roadmap-id.yml) はマージ後の `push: main` で
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
- **PR の現在のラベルを読まず、変更された項目ごとに base→head の差分（`remove old`、`add new`）を
  出す案。** 最初の設計で、この提案が当初記述していた方式でもあります。実装中にプッシュごとの
  挙動を追ったところ、不採用としました。`pull_request` は `synchronize` のたびに発火しますが、
  ラベルの集合は状態を持っており、base→head の差分は収束しません。**新しい**項目はプッシュを
  またいで `status` が `added` のままなので、差分は常に *add* しか出さず、レビュー中にその項目を
  分類し直すと（「動機」で挙げた代表的な場面です）PR には古いラベルと新しいラベルの両方が残って
  しまいます。また `Topic` の編集を差し戻すとそのファイルは差分から消え、以前のプッシュが付けた
  ラベルが取り残されます。解決策は、操作を宣言的にすることです。毎回、base→head 差分の全体から
  あるべき集合を計算し直し、PR の現在の `topic:*` ラベルと突き合わせて調整します（要素2）。これは
  以前のラベル状態によらず収束し、代償は読み取りが1回増えること（`gh pr view --json labels`）だけ
  です。
- **`Status` が `Implemented` の項目にもラベルを付ける案。** 不採用としました。理由は「動機」の
  とおりで、出荷済みの項目にはトピックでトリアージすべきオープンな PR がもう残っていないため、
  ラベルが振り向ける先がありません。（BE-0159 でロードマップがフラット化される前は、この除外は
  `roadmaps/implemented/` に対するパス判定でしたが、いまは同じ判断を項目の `Status` から読み取り
  ます。）
- **base コミット1つに対する `git show` の代わりに、全履歴を取得するチェックアウト
  （`fetch-depth: 0`）で以前の `Topic` を再計算する案。** どちらでも得られる結果は同じです。必要
  なのは base コミット1つだけであり、全履歴のチェックアウトは利益のない余分なコストになるため、
  不採用としました。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `scripts/sync_roadmap_topic_labels.py`：変更ファイル一覧から `topic:<key>` の add/remove
      アクションを作る処理。`build_roadmap_index.py` のメタデータ解析と `TOPIC_KEY_BY_NAME` を
      再利用する。
- [x] `.github/workflows/roadmap-topic-labels.yml`：フラットなロードマップツリーで追加・編集・
      リネームされた項目ファイルを検出し（`Status` が `Implemented` の項目は除外）、必要なトピック
      ラベルの存在を保証したうえで、PR に add/remove アクションを適用する。

ログ:

- 2 つの作業単位を 1 つの変更で実装しました。あるべき状態へ調整する分類処理
  （`scripts/sync_roadmap_topic_labels.py`。`tests/test_sync_roadmap_topic_labels.py` でユニット
  テスト済み）と、そのアクションを PR に適用する `roadmap-topic-labels.yml` ワークフローです。
  マージ前のレビューで、当初仕様の base→head 差分方式がプッシュをまたいで収束しない（新しい項目を
  レビュー中に分類し直すと両方のラベルが残る）ことが分かったため、要素2 を、PR の現在の `topic:*`
  ラベルとあるべき集合を突き合わせて調整する方式に作り直しました（「検討した代替案」を参照）。
- マージ前に BE-0159 のフラットなロードマップ構成へ追従させました。項目を
  `roadmaps/BE-0156-roadmap-topic-label-sync/` へ移し、ワークフローのパスフィルタを `roadmaps/**`
  へ広げ、出荷済み項目の除外を `implemented/` へのパス判定から各項目の `Status` を読む方式（`Implemented`
  を除外）へ変更しました。`Status` ごとのフォルダがなくなったためです。
- [#817](https://github.com/bajutsu-e2e/bajutsu/pull/817)：パスラベルのルールに `record` トピックを
  追加しました。record 系モジュール（`bajutsu/record.py`、`bajutsu/record_capture.py`、
  `bajutsu/cli/commands/record.py`）に触れる PR には `topic:record` が付きます。`PATH_TOPIC_*` の
  ガードが通るよう `record` を正準の `TOPICS` キーへ昇格させましたが、既存の record 系項目は
  `authoring` のままなので、index に変化はありません。

## 参考

- [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py) — この項目が
  再利用する正準の `TOPICS` と BE-METADATA のパース処理。
- [`roadmap-proposal-approvals.yml`](../../.github/workflows/roadmap-proposal-approvals.yml) —
  対象外の PR を無操作にする形を含め、ロードマップ限定の PR ワークフローの前例。
- [`roadmap-tracking-issues.yml`](../../.github/workflows/roadmap-tracking-issues.yml) —
  この項目が再利用するオープン / `Implemented` の境界の前例（BE-0109）であり、同じパース処理を
  メタデータの読み取りに使っている前例でもある。
- [`roadmap-id.yml`](../../.github/workflows/roadmap-id.yml) — ラベル付けのタイミング候補と
  して検討し、不採用とした（「検討した代替案」を参照）マージ時ワークフロー。

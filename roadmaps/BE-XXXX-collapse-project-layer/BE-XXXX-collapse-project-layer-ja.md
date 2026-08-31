[English](BE-XXXX-collapse-project-layer.md) · **日本語**

# BE-XXXX — project 階層を org と target に畳む

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-collapse-project-layer-ja.md) |
| 提案者 | [@paihu](https://github.com/paihu) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Web UI のホスティング |
| 関連 | [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md), [BE-0226](../BE-0226-cross-project-metrics-dashboard/BE-0226-cross-project-metrics-dashboard-ja.md), [BE-0275](../BE-0275-serve-projects-management-page/BE-0275-serve-projects-management-page-ja.md), [BE-0393](../BE-0393-per-org-config-memory/BE-0393-per-org-config-memory-ja.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md), [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage-ja.md), [BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`bajutsu serve` の所有の単位は3階層の入れ子です。**org** が **project** を所有し、project は
config ファイルを1つ束ねます。その config は1つ以上の
[target](../../docs/ja/glossary.md#target-app-device) を宣言します。本項目は、この中間の階層を取り除き
ます。org が config を直接所有し、run は実行した target と自由文字列の **label** を持ちます。label は
運用者が任意に付けられる値です。project レジストリと `projects` テーブル、およびその上に建てられた
操作面は、すべて削除します。

現在の **project** とは、config ソースへの名前付きバインディングです。org ごとに管理し、
`POST /api/projects` かコマンドライン（`bajutsu project`）で登録します。`serve` ヘッダのピッカーで
切り替えられます。1つの `serve` プロセスを複数 config のハブにするための仕組みです
（[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md)）。

しかし実際の運用は、そのハブを必要としていません。org は1つのサービスであり、そのサービスの中でチームが
並べて比べたいものは Android、iOS、web という target です。別のサービスに移るのは org の切り替えです。
同じサービスの別 config に移るのは `serve` の再起動であり、数秒で済みます。

ただし project 階層をそのまま消すと、残しておきたいものが1つ失われます。project は run 履歴を仕切る値
でもあります。この値があってはじめて、別の config で `serve` を起動し直した運用者は、2つの履歴の
混ざった一覧を見ずに済みます。読み取り側は後述の単位4が整えます。本項目はその仕切りを残し、周囲の
レジストリだけを落とします。起動時の自動登録がすでに計算している文字列を、run 行の素のカラムへ
移します。運用者は run ごとにその値を上書きできます。

## 動機

project 階層は、org を持つ構成では到達できず、org を持たない構成では不要です。

project が作られる経路は3つだけです。起動時にバインド済みの config が自動登録される経路、
`POST /api/projects` で登録する経路、`bajutsu project add` で登録する経路です。起動時の自動登録は
`default` org 決め打ちで（[`bajutsu/serve/operations/config.py`](../../bajutsu/serve/operations/config.py)）、
コマンドラインも同じく `default` org に固定されています。web UI の
project 追加フォームは Projects ビューにありますが、そのビューのタブは project が1つもない org では
表示されません。ヘッダのピッカーも、project が2つ以上になるまで表示されません。したがって project が
0件の org からは、追加フォームに到達できません。

したがって `default` 以外のホスト型 org でサインインしたメンバーが見るものは限られます。空の project
一覧、`None` の active project、`project_id` が空のまま記録される run、そして表示されないピッカーです。
そのメンバーに残された経路は `POST /api/projects` を直接呼ぶことだけです。とはいえメンバーは何にも困り
ません。バインド済みの config はプロセス全体で共有されており、その org の target は
`orgs.<name>.targets` によって絞り込まれるからです。この絞り込みは project を一度も参照しない、別の
仕組みです。

残された1つの経路で project を登録しても、再起動を越えられません。データベース版のレジストリは
active project をカラムではなく素の辞書に保持しています。その理由は docstring 自身が述べています。
「active」は永続的な状態ではなく、セッションの概念として設計されました
（[`bajutsu/serve/project_registry.py`](../../bajutsu/serve/project_registry.py)）。再起動後は
active project がふたたび `None` に解決されます。`POST /api/projects/<name>/run` は「アクティブな
バインディングではない。先に切り替えよ」という `409` を返します。そして、その切り替えを行うピッカーは、
その org には表示されません。[BE-0393](../BE-0393-per-org-config-memory/BE-0393-per-org-config-memory-ja.md) も、
org を持つ構成こそがどの project がアクティブだったかを忘れる構成である、という同じ欠陥を指摘して
います。

一方、この階層が存在する理由である仕切りは、記録されるだけで読まれていません。run は enqueue の時点で
`runs.project_id` を打刻します。しかし、これを読むのは project の操作面そのものだけです。project 単位の run 一覧
（`GET /api/projects/<name>/runs`）、Projects 一覧に出る最新 run の要約、そしてプロジェクト横断比較の
3つです。この横断比較が
[BE-0226](../BE-0226-cross-project-metrics-dashboard/BE-0226-cross-project-metrics-dashboard-ja.md)
にあたります。
通常の run 一覧、Replay、run 統計ダッシュボードは、project による絞り込みを一切かけていません
（[`bajutsu/serve/operations/reads.py`](../../bajutsu/serve/operations/reads.py)）。2つ目の config で
`serve` を起動し直した運用者は、run 行には異なる `project_id` を得ます。それでいて、実際に見るすべての
画面では1つの混ざった履歴を見ることになります。仕切るためのデータは存在し、読む側がそれを使っていま
せん。

そしてチームが本当に欲しい比較は、どうやっても得られません。軸となる値が捨てられているからです。run の
target は enqueue で検証され、その後どこにも永続化されません
（[`bajutsu/serve/operations/dispatch.py`](../../bajutsu/serve/operations/dispatch.py)）。`RunResult` は
target のフィールドを持ちません。manifest はアクチュエータを `backend`、OS を `device_runtime` として
記録しますが、target 名は記録しません。`runs` テーブルにも target のカラムがありません。つまり
「Android の target は通っているが iOS の target は落ちている」は、保存済みデータから計算できません。
その一方で、出荷済みのダッシュボードが提供するのは「project *checkout* 対 project *search*」という、
2つのサービスのあいだの、誰も行わない比較です。

本項目が実装されたことを後から確かめられる違いは2つあります。1つは、別の config で `serve` を起動し直した
運用者が、通常の run 一覧と run 統計ダッシュボードで、config ごとの run を混ざらずに見られることです。
もう1つは、1つの org のメンバーが、Android の target の成功と iOS の target の失敗を、何も切り替えずに
1画面で並べて見られることです。

本項目は prime directive の内側に収まります。label は run に付随するメタデータであり、判定の入力には
決してなりません。target の打刻は、決定論的な dispatch 経路がすでに解決済みの値を記録するだけです。
本項目が追加する画面は、いずれも保存済み run データへの読み取り専用の集計です。したがって `run` や
継続的インテグレーションの経路に、大規模言語モデル（LLM）は入りません。project 階層の削除は config の
形をした状態をツールから減らす変更であって、増やす変更ではありません。app 非依存の directive にも
影響しません。アプリごとの差は、これまでどおり各 config の `targets.<name>` に残ります。

## 詳細設計

作業は5つの単位に分かれ、互いに重複しません。config バインディングの org への移設、run の label、
target の打刻、読み取り側の画面、そして削除そのものです。単位1が先に来るのは、project 行が現在1つの
経路を支えているためです。単位5が最後に来るのは、他の4単位に依存するためです。

### 1. org が config ソースを1つ持つ

ホスト型のレプリカは、自分が受け取っていないアップロード済み config を、保存された project レコードを
読んで復元します。`activate_uploaded_project` が解決するのは、レコードの `sha256`、または合成された3つ組の
`{config, scenarios, binary}` のダイジェストです。解決したうえで、オブジェクトストアからバイト列を
取得します
（[BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage-ja.md)、
[BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md)）。これは、
手元でビルドしたバイナリや手元で編集した scenario をホスト型のデプロイに対して走らせるときに、メンバーが
通る経路です。ホスト型の org が実際に行う唯一の config 変更でもあります。このレコードを移さずに
`projects` テーブルを消すと、この経路が壊れます。

そこで org 行に、現在の project 行が持つのと同じ判別付きレコードを保持するカラムを1つ足します。`git`、
`file`、`upload` のいずれかの `kind` と、そのロケータです。1つの org が持つレコードはリストではなく
1つで、`activate_uploaded_project` は project ではなく org からそれを読みます。レコードがすでに受けて
いる検証はそのままです。ダイジェストは、パスやオブジェクトストアのキーへ変換される前に `_SHA256_RE` と
一致しなければなりません。レコードの形はクライアントが決められるため、信頼できない値だからです。

これは [BE-0393](../BE-0393-per-org-config-memory/BE-0393-per-org-config-memory-ja.md) が org ごとの
config 記憶のために提案している機構に、逆方向からたどり着いたものです。BE-0393 が `projects` テーブルを
使おうとするのは、それが既存の org ごとの永続ストアだからです。本項目のもとでは、その要件はカラム1つに
収束します。周囲の名前付き project 階層のほうが消えます。2つの項目は、別々ではなく1つの設計として
着地するのが筋です。順序は動きません。テーブルを落とす前に、カラムが存在している必要があります。

### 2. run の label

`runs` テーブルに、短い自由文字列を保持する空値許容の `label` カラムを足します。label が run に届く経路
は、現在 `project_id` が通っている経路と同じです。enqueue で一度解決し、`Job` に載せて運び、run 行を
記録するときに書きます。これにより、リモートのワーカーはレジストリを参照せずに label を打刻できます。

デフォルト値は、起動時の処理がすでに計算している文字列です。`launch_project_identity` は、Git から取得
した config の provenance スタンプからリポジトリ名を導出します。ローカルの config ファイルからは、
ファイル名の語幹を導出します
（[`bajutsu/serve/operations/config.py`](../../bajutsu/serve/operations/config.py)）。その導出結果は、
別の config で起動し直した運用者が欲しい仕切りそのものです。この関数は残ります。変わるのは、結果が
レジストリではなく run 行に書かれる点だけです。

運用者が run ごとに上書きする手段は、コマンドラインの `bajutsu run --label <値>` と、`POST /api/run` の
ボディの `label` フィールドです。label はツールにとって不透明な値です。解析されず、config と照合されず、
認可からも参照されません。小さな上限を超える値は、切り詰めずに境界で拒否します。黙って短くされた label
を履歴の中で見つけるのではなく、拒否されたことを運用者がその場で知るためです。

### 3. target の打刻

`RunResult` に `target` フィールドを足し、`manifest_dict` がそれを記録します。`runs` テーブルには、
`scenario_hash` や `device_runtime` と同じ形で manifest から写した `target` カラムを足します。target に
ついて注入の経路は設計しません。値はすでに run のリクエストで届いており、run が始まる前に org の宣言済み
target と照合されているからです。この単位が行うのは、dispatch 経路が解決済みの値を永続化することです。
新しい値を受け取ることではありません。

target は label の予約値ではなく、独立したカラムのままにします。label は運用者の自由文字列であり、構成上
信頼できません。いっぽう target 名は config で宣言される値で、認可の重みを持ちます。
`orgs.<name>.targets` がどの target をその org が所有するかを決め、dispatch 経路は他の org に属する
target を拒否します。両者を1つのカラムに畳むと、認可が正当な値として読む場所に、信頼できない文字列が
入り込みます。

### 4. label で絞り、target で並べる

通常の run 一覧、Replay、run 統計ダッシュボードに label のフィルタを足します。これにより、2つ目の config
で `serve` を起動し直したときに、1つの混ざった一覧ではなく2つの読める履歴が得られます。フィルタの
デフォルト値は、現在バインドされている config の label です。config を切り替えながら起動し直す運用者が
期待する挙動は、これにあたります。「すべての label」を明示的に選べば、現在の絞り込みなしの表示に戻り
ます。

プロジェクト横断比較は、target 横断比較になります。`project_comparison.py` はすでに、単一 config 向けの
集計を仕切りごとに1回ずつ走らせて結果を並べています。変更するのは、仕切りのキーを `project_id` から
`target` に、ラベルを project 名から target 名に付け替える点だけです。集計そのものは、
[BE-0226](../BE-0226-cross-project-metrics-dashboard/BE-0226-cross-project-metrics-dashboard-ja.md) が
run 統計ダッシュボードから切り出したものを、変更せずに再利用します。

### 5. project 階層の削除

先行する4単位が着地したうえで、次のものを削除します。

- `projects` テーブルと `runs.project_id` の外部キー、`ProjectRecord` 境界型、`Repository` の project
  メソッド群
- [`bajutsu/serve/project_registry.py`](../../bajutsu/serve/project_registry.py) の全体。`ProjectRegistry`
  プロトコル、`SqlProjectRegistry`、`LocalProjectRegistry`、ディスク上の JSON ストア、`ServeState` の
  `project_registry` フィールド
- `/api/projects` の各エンドポイント。一覧、登録、登録解除、project 単位の run トリガ、project 単位の
  run 一覧、再バインドを行う activate
- `bajutsu project add` / `ls` / `use` / `rm` の各コマンドと、`run --project` フラグ
- `serve` シェルのヘッダにある project ピッカーと、トップレベルの Projects ビュー（一覧、追加フォーム、
  ナビゲーションのタブ）

カラムを落とす前に、各 run の project 名から `runs.label` を埋めるマイグレーションを走らせます。project
階層のもとで記録された履歴が、それまで持っていた仕切りを保つためです。`project_id` がすでに空の run は、
label も空のままとなり、絞り込みなしの表示に現れます。実際には、`default` 以外のホスト型 org の run の
大半がこれにあたります。

`bajutsu project` の各コマンドと `run --project` フラグは、非推奨として残さず削除します。非推奨期間を
設けるということは、それらを提供するためにレジストリを生かしておくということです。そのコードこそ、本項目
が消そうとしているものです。これらを呼んでいた箇所が欲しかった仕切りには、`run --label` がそのまま代わり
になります。到達できなかった切り替えのほうには、代替を用意する必要がありません。

## 検討した代替案

**project 階層を残し、3つの穴を塞ぐ。** `default` だけでなく org ごとに自動登録する案です。あわせて
active project をカラムに永続化し、web UI から project を作れるようにします。採用しません。3つの穴は、誰も手を伸ばさ
ない階層の症状だからです。穴を塞いで得られるのは、1つの org の中での複数 config 切り替えです。本ツールが
相手にするデプロイは、それを望んでいません。そして望まれている唯一の部分である run 履歴の仕切りは、その
機構を一切使わずにカラム1つで済みます。

**project 階層を削除し、仕切りも持たない。** 採用しません。再起動後に2つの config の run が混ざったまま
になるからです。それこそが、本項目の発端となった具体的な不満です。仕切りの費用は十分に小さく、さらなる
単純化のために落とすのは、機構と引き換えに成果を手放すことになります。

**target を label の予約値にし、1つのカラムで両方を賄う。** 採用しません。理由は単位3で述べたとおり
です。label は信頼できない運用者の自由文字列であり、target は認可が参照する config 宣言済みの名前です。
1つのカラムに畳むと、認可の重みを持つ値が読まれる場所に、クライアント由来の文字列が立つことになります。

**`runs.project_id` を残し、`projects` テーブルを落とさずに `label` へ改名する。** 採用しません。label
が登録済みの project しか指せなくなるのは、外部キーがあるからです。自由文字列の label の価値は、登録を
必要としない点にあります。外部キーを満たすためだけに存在する行を保持するためにテーブルを残すのは、保守
の費用を残したまま利点を捨てることです。

**BE-0393 に統合する。** BE-0393 の org ごとの config 記憶は本項目の単位1にあたるため、2つの項目が重なる
のはちょうど1単位です。残りの単位、すなわち label、target の打刻、読み取り側の画面、削除は、BE-0393 の
範囲の外にあります。どう起票するかは BE-0393 の著者の判断です。技術的な順序は、どちらの形でも変わり
ません。project テーブルを落とす前に、org のカラムが存在している必要があります。

## 進捗

> 作業の進行に合わせて更新してください。チェックリストは *詳細設計* の作業分解を反映し（作業単位ごとに
> 1項目）、ログには何がいつ変わったかを古い順に記録し、PR を貼ります。

- [ ] 1 — org が config ソースを1つ持つ。判別付きの `kind` + ロケータのレコードを保持する org 行の
  カラムと、それを org から読む `activate_uploaded_project`
- [ ] 2 — run の label。`runs.label` カラム、`launch_project_identity` によるデフォルト値。`Job` に
  載せて運ぶ enqueue 時の解決と、`run --label` および web からの上書き
- [ ] 3 — target の打刻。`RunResult.target`、manifest のキー、`runs.target` カラム
- [ ] 4 — label で絞り、target で並べる。run 一覧、Replay、run 統計ダッシュボードの label フィルタと、
  target 軸に付け替えた `project_comparison.py`
- [ ] 5 — project 階層の削除。テーブル、外部キー、レジストリのモジュール、エンドポイント、コマンド、
  UI の操作面、label を埋めるマイグレーション

## 参考

- [`bajutsu/serve/project_registry.py`](../../bajutsu/serve/project_registry.py) — 本項目が削除する
  レジストリ
- [`bajutsu/serve/operations/projects.py`](../../bajutsu/serve/operations/projects.py) — 削除する
  エンドポイント群
- [`bajutsu/serve/operations/config.py`](../../bajutsu/serve/operations/config.py) — 起動時の自動登録と
  `launch_project_identity`
- [`bajutsu/serve/operations/dispatch.py`](../../bajutsu/serve/operations/dispatch.py) — target の検証と
  `project_id` の解決
- [`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py) — `projects` と `runs` の
  テーブル定義
- [`bajutsu/report/manifest.py`](../../bajutsu/report/manifest.py) — target を記録していない manifest
- [アーキテクチャ](../../docs/ja/architecture.md)、[設定](../../docs/ja/configuration.md)、
  [レポート](../../docs/ja/reporting.md)、[用語集](../../docs/ja/glossary.md)
- [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub-ja.md) — 本項目が削除する project
  ハブ
- [BE-0226](../BE-0226-cross-project-metrics-dashboard/BE-0226-cross-project-metrics-dashboard-ja.md) —
  本項目が軸を付け替える比較
- [BE-0275](../BE-0275-serve-projects-management-page/BE-0275-serve-projects-management-page-ja.md) —
  単位5が削除する、追加フォームを備えたトップレベルの Projects ビュー
- [BE-0393](../BE-0393-per-org-config-memory/BE-0393-per-org-config-memory-ja.md) — org ごとの config
  記憶。その機構が単位1にあたる
- [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) — `projects` テーブルの
  出どころであるホスト型スキーマ
- [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage-ja.md) と
  [BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md) — 単位1が
  移設するアップロード config の復元

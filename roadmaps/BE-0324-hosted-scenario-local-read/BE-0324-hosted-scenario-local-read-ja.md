[English](BE-0324-hosted-scenario-local-read.md) · **日本語**

# BE-0324 — ホスト提供シナリオを object storage ではなく束縛済み config のローカルキャッシュツリーから読む

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0324](BE-0324-hosted-scenario-local-read-ja.md) |
| 提案者 | [@paihu](https://github.com/paihu) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0324") |
| 実装 PR | [#1384](https://github.com/bajutsu-e2e/bajutsu/pull/1384) |
| トピック | config の取得元 |
| 関連 | [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md), [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md), [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage-ja.md), [BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) |
<!-- /BE-METADATA -->

## はじめに

ホスト提供の `bajutsu serve`（サーバーバックエンド、[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)）は、
リクエストを受け付けるより前に、束縛した config のシナリオツリーをローカルディスクへ展開します。
束縛経路は 3 つあります。

1. Git ソースの束縛（[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)）
2. zip アップロードの束縛（[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)）
3. アーティファクトの組み合わせ束縛（[BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md)）

いずれの経路も、config 本体と `scenarios` ディレクトリを同じコンテンツアドレス方式のキャッシュディレクトリへ
展開します。config ファイル自身もこのディレクトリから読まれます。

ところが control plane のシナリオ一覧、読み取り、実行時のシナリオ取得は、このディレクトリを参照しません。
3 つとも `ObjectScenarioStorage` を経由します。この store は、サーバーが実行結果アーティファクトやベースラインの
保存にも使う object storage バケットをバックエンドとします。

本提案は、この 3 つの読み取り操作の解決先を変えます。config がすでに展開済みのローカルディレクトリから、
シナリオを解決します。config ファイル自身の取得元と同じ場所です。Git や zip ソースがすでにディスクへ届けた
シナリオの内容を、バケット側に独立してもう 1 つ持たせる必要はなくなります。

## 動機

この重複は、設計上の見た目だけの問題ではありません。実際の欠落につながっています。

`ObjectScenarioStorage` にエントリが増える経路は 2 つだけです。ホスト提供の Web エディタでシナリオを保存したときと、
`record` ジョブがシナリオを書き出したときです
（`bajutsu/serve/server/scenarios.py`、`bajutsu/serve/operations/worker_uploads.py`）。`bind_git_config` も zip と
組み合わせの束縛経路も、このバケットには何も書き込みません
（`bajutsu/serve/operations/config.py`、`bajutsu/serve/operations/upload.py`）。

Git リポジトリやアップロード済みバンドルに含まれるシナリオを考えます。このシナリオは、ホスト提供 UI のシナリオ一覧に
現れません。直接の読み取りにも、実行にも現れません。誰かが Web エディタで手作業により同じ内容を書き直さない限り、
何も変わりません。シナリオツリーはその間ずっと control plane 自身のディスク上にありながら、一度も読まれずに終わります。

ローカル（非ホスト）バックエンドには、この欠落がありません。`ServeState` はシナリオ store を
`LocalScenarioStore(lambda target: _scenarios_dir_for(self, target))`（`bajutsu/serve/state.py`）として配線しています。
`_scenarios_dir_for` は、各ターゲットのシナリオディレクトリを `state.cwd` を基準に解決します。Git ソースの config なら
checkout のルートが基準です。それ以外なら、ローカル config ファイルと同じディレクトリが基準です。

サーバーバックエンドの `_build_server_state`（`bajutsu/serve/__init__.py`）は、この解決方法を再利用しません。
束縛されている config の出所にかかわらず、各 org のシナリオ store を無条件に
`StorageScenarioStore(ObjectScenarioStorage(...))` として配線します。本提案は、ローカルバックエンドがすでに持つ
読み取り経路を、サーバーバックエンドにも与えます。

2 つのバックエンドの間には、設計を左右する違いが 1 つあります。ホスト提供の実行は、control plane とファイルシステムを
共有しない別の worker プロセスで行われます（`bajutsu/serve/server/db_executor.py`、
`bajutsu/serve/server/worker_job.py`）。そのため、実行に渡すシナリオは、これまでどおりジョブの `materials` として
テキストで運ぶ必要があります。単なるローカルパスは worker に届きません。

## 詳細設計

### 1. サーバーバックエンド向けのローカルツリー読み取り

`ObjectScenarioStorage` と並べて、`ScenarioStorage` の実装を 1 つ追加します（`bajutsu/serve/server/scenarios.py`）。
名前は `LocalTreeScenarioStorage` とします。構築時には、稼働中の `ServeState` と、`ObjectScenarioStorage` の
インスタンスそのものを渡します。`has_app` は、そのインスタンスへの委譲で答えます（同じ `apps` の取得方法を、
二重に持つことなく再利用します）。

`list` と `read` は、object storage を呼ぶ代わりに、ローカルバックエンドの仕組みへ委ねます。これにより、Git や zip
ソースのシナリオは、直接の読み取りや実行だけでなく、ホスト提供 UI の一覧にも現れます。

org のターゲットが与えられると、`LocalTreeScenarioStorage` は `_scenarios_dir_for(state, target)`
（`bajutsu/serve/state.py`）を呼び、そのターゲットのシナリオディレクトリを得ます。得たディレクトリを
`LocalScenarioScope`（`bajutsu/serve/scenarios.py`）に渡し、その `list` または `read` を呼びます。

このように `LocalScenarioScope` をそのまま再利用すれば、同じディレクトリ解決とパス閉じ込めのロジックを
`bajutsu/serve/server/scenarios.py` 側で作り直さずに済みます。BE-0051 のパス閉じ込め保護は、すでに実装とテストが
済んだ 1 か所に保たれます。食い違いうる二重実装も避けられます。

`_build_server_state` は、束縛されている config が何であれ、その `cwd` を解決するだけです。Git checkout のルート、
zip 展開のルート、組み合わせ済みアーティファクトのルートのいずれでも同じです。サーバーバックエンドがサポートする
束縛経路は、すべて同じ場所へツリーを展開します。そのため、この実装は config の取得元の種類で分岐する必要がありません。
1 つの実装で、Git、zip、組み合わせアーティファクトのいずれのソースもカバーできます。

`LocalTreeScenarioStorage` にも `save` メソッドは要ります。`ScenarioStorage` プロトコル
（`bajutsu/serve/server/scenarios.py`）がそれを求めるからです。`StorageScenarioStore` と `StorageScenarioScope` は、
それぞれ注入された `ScenarioStorage` を 1 つだけ保持し、そのオブジェクトに対して `save` を呼びます。

`LocalTreeScenarioStorage` は、`has_app` の委譲先として使った `ObjectScenarioStorage` のインスタンスを、
コンストラクタの引数として受け取ります。その `save` メソッドは、このインスタンスへそのまま委ねます。`list` と
`read` は、この委譲先に触れません。触れるのは `has_app` と `save` だけです。

こうして 1 つのオブジェクトが、`ScenarioStorage` プロトコル全体に答えます。ローカルツリーから読み、
`ObjectScenarioStorage` を通じて書きます。新しいプロトコルは不要で、`StorageScenarioScope` が保持するオブジェクトも
1 つのままです。

### 2. `runnable()` は materials を運び続け、変わるのはその取得元だけ

`StorageScenarioScope.runnable()`（`bajutsu/serve/server/scenarios.py`）は、これまでどおり `materials` を運ぶ
`Runnable` を返します。変わるのは、シナリオの本文の取得元だけです。object storage への `get_bytes` 呼び出しの代わりに、
読み取り実装がローカルディスクから読んで本文を供給します。

本文は、これまでと同じく `materials` の 1 エントリとして worker へ渡されます。worker 側の `_materialize`
（`bajutsu/serve/server/worker_job.py`）が、実行前に worker 自身のワークスペースへ本文を書き出します。
worker 側の契約は変わりません。変わるのは、control plane が本文をどこから読むかだけです。

### 3. `save` と `authored` は対象外

本提案が変更するのは読み取り経路だけです。`list`、`read`、そして `runnable()` が materials として運ぶ本文の
取得元です。ホスト提供の Web エディタを通じたシナリオの保存も、`record` ジョブによる書き出しも、これまでどおり
`ObjectScenarioStorage` を経由します。

この切り分けは、提案者自身の運用に合っています。シナリオはローカルマシンで作成します。バンドルの再アップロードか
Git ソースへの push によって control plane へ反映します。サーバー上でその場編集する運用ではありません。この理由から、
書き込み側には、まだローカルツリー側の受け皿がありません。

`save` と `authored` をローカルツリー側のシナリオソースとどう整合させるかは、別の課題です。この課題は、control plane を
ディスクを共有しない複数レプリカで動かす場合にどうするかも決めなければなりません。本提案は、両方の課題を、
デプロイ構成が固まった段階の後続 BE 項目にあえて残します。まだ固まっていない前提の上に設計はしません。

### 4. 配線

`_build_server_state`（`bajutsu/serve/__init__.py`）は、org ごとの `StorageScenarioStore` の組み立て方を変えます。
従来は `StorageScenarioStore(ObjectScenarioStorage(...))` を直接構築していました。

本提案では、`ObjectScenarioStorage` のインスタンスを従来どおり先に構築します。そのインスタンスを
`LocalTreeScenarioStorage` で包んでから、`StorageScenarioStore` へ渡します。`save` と `authored` は、1 段深いところで、
同じ `ObjectScenarioStorage` オブジェクトへそのまま届きます。

### 決定性、ゲート、app 非依存性

本項目が変更するのは 1 点だけです。シナリオの本文を control plane がどこから見つけて読むかです。LLM 呼び出しは
どこにも追加せず、実行が何を検証するかも変えません。pass/fail は、今日と変わらず完全に機械判定のままです（指令 1）。

本項目はむしろ、隠れた失敗モードを 1 つ取り除きます。本項目がなければ、`StorageScenarioScope.read` と `runnable` は、
束縛したツリーに実在する Git や zip ソースのシナリオに対しても「そのようなシナリオはない」という結果を返します。
運用者は、自分で原因を調べる羽目になります。本項目は、同じシナリオを決定的に解決させます（指令 2）。

app 固有の分岐は一切ありません。ローカルツリー読み取りが解決するのは、どのターゲットの config にもすでにある
`scenarios` ディレクトリの設定と同じものです（指令 3）。

## 検討した代替案

- **現状を維持する。** 却下します。本項目は、この欠落を解消するために存在します。Git や zip ソースのシナリオは、
  誰かが object storage バケットへ手作業で同じ内容を書き直さない限り、ホスト提供 UI の一覧、読み取り、実行の
  いずれからも参照できないままです。
- **束縛時に展開済みツリーを `ObjectScenarioStorage` へコピーし、読み取り経路は変えない。** 却下します。この案は、
  本項目が取り除こうとしている重複を作り直し、しかも同期が保たれません。組み合わせ済みアーティファクトの
  ローカルディレクトリはコンテンツアドレス方式であり、展開すると不変になります
  （`materialize_composition`、`bajutsu/serve/operations/composition.py`）。後の `save` がそのディレクトリを書き換えても、
  コピーを再実行する仕組みは何もありません。バケットとディスクの内容は、そのまま食い違っていきます。
- **`save` と `authored` も本項目でローカルツリー側へ移す。** いまは却下します。control plane を、ディスクを共有しない
  複数レプリカで動かす場合、シナリオの編集内容をどう永続化し、どう可視にし続けるかを決める必要があります。この点は、
  提案者側でもまだ決まっていません。本項目は、読み取り経路という明確でリスクの低い半分だけを先に届けます。
  書き込み側は、その問いに答えが出てからの後続項目に委ねます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 1 — `LocalTreeScenarioStorage` の実装。`has_app` は注入された `ObjectScenarioStorage` への
  委譲で答え(同じ `apps` の取得方法を二重に持たない)、`list`・`read` は `LocalScenarioStore` /
  `_scenarios_dir_for` への委譲で、`save` はその同じ注入済み `ObjectScenarioStorage` への委譲で答える。
- [x] 2 — `runnable()` の変更。`materials` の本文の取得元を `LocalTreeScenarioStorage` にする
  (worker 側の契約、`_materialize` による実行ワークスペースへの書き出しは変更なし)。
- [x] 3 — `_build_server_state` の配線変更。構築した `ObjectScenarioStorage` を
  `LocalTreeScenarioStorage` で包んでから `StorageScenarioStore` へ渡す。

## 参考

- [CLAUDE.md](../../CLAUDE.md): 決定性優先、app 非依存。
- [BE-0063：Git リポジトリ + ref から config(とそのシナリオツリー)を読み込む](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md):
  その checkout ルートを基準に、ローカルバックエンドで `_scenarios_dir_for` がすでに解決している Git 束縛経路。
- [BE-0073：config + シナリオ + アプリバイナリのバンドルを zip でアップロードする](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)、
  [BE-0243：アップロードした zip config バンドルを object storage へ永続化する](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage-ja.md)、
  [BE-0268：config・シナリオ・アプリバイナリを、実行ごとに組み合わせる独立したコンテンツアドレス
  アーティファクトとしてアップロードする](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md):
  同じ方法で、シナリオツリーを control plane のディスクへ展開する zip・組み合わせアーティファクトの束縛経路。
- [BE-0015：Web UI のパブリックホスティング](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md):
  サーバーバックエンドと、本項目の `runnable()` 設計が踏まえる control plane / worker の分離。
- 本項目が触れる箇所: `bajutsu/serve/state.py` の `LocalScenarioStore` 配線と `_scenarios_dir_for`。
  `bajutsu/serve/scenarios.py` の `LocalScenarioScope`、`ScenarioStore` / `ScenarioScope` プロトコル。
  `bajutsu/serve/server/scenarios.py` の `ObjectScenarioStorage`、`StorageScenarioScope`、
  `StorageScenarioStore`。`bajutsu/serve/__init__.py` の `_build_server_state`。
  `bajutsu/serve/server/db_executor.py` の `DbQueueExecutor`。`bajutsu/serve/server/worker_job.py` の
  `_materialize` と `execute_job_spec`。

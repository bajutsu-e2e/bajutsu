[English](BE-0324-hosted-scenario-local-read.md) · **日本語**

# BE-0324 — ホスト提供シナリオを object storage ではなく束縛済み config のローカルキャッシュツリーから読む

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0324](BE-0324-hosted-scenario-local-read-ja.md) |
| 提案者 | [@paihu](https://github.com/paihu) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0324") |
| トピック | config の取得元 |
| 関連 | [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md), [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md), [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage-ja.md), [BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) |
<!-- /BE-METADATA -->

## はじめに

ホスト提供の `bajutsu serve`(サーバーバックエンド、[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md))は、
リクエストを受け付けるより前に、束縛した config のシナリオツリーをローカルディスクへ展開します。
束縛経路は3つあります。Git ソースの束縛([BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md))、
zip アップロードの束縛([BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md))、
アーティファクトの組み合わせ束縛([BE-0268](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md))です。
いずれも config 本体と `scenarios` ディレクトリを、同じコンテンツアドレス方式のキャッシュディレクトリへ展開します。
config ファイル自身もこのディレクトリから読まれます。ところが control plane のシナリオ一覧・読み取り・
実行時のシナリオ取得は、このディレクトリを参照しません。常に `ObjectScenarioStorage` という別の store を
経由します。これはサーバーが実行結果アーティファクトやベースラインの保存にも使っている、独立した
object storage バケットをバックエンドとする store です。本提案は、この3つの読み取り操作の解決先を変えます。
object storage ではなく、config がすでに展開済みのそのローカルディレクトリから解決するようにします。
config ファイル自身の取得元と同じ場所です。Git や zip ソースがすでにディスクへ届けているシナリオの
内容を、バケット側に独立してもう1つ持たせる必要はなくなります。

## 動機

この重複は設計上の見た目だけの問題ではありません。実際の欠落につながっています。
`ObjectScenarioStorage` にエントリが増える経路は2つだけです。ホスト提供の Web エディタでシナリオを
保存したときと、`record` ジョブがシナリオを書き出したときです
(`bajutsu/serve/server/scenarios.py`、`bajutsu/serve/operations/worker_uploads.py`)。
`bind_git_config` も zip・組み合わせの束縛経路も、このバケットには何も書き込みません
(`bajutsu/serve/operations/config.py`、`bajutsu/serve/operations/upload.py`)。Git リポジトリや
アップロード済みバンドルに含まれるシナリオを考えます。このシナリオは、ホスト提供 UI のシナリオ一覧に
現れません。直接の読み取りにも、実行にも現れません。誰かが Web エディタで手作業により同じ内容を
書き直さない限り、何も変わりません。シナリオツリーはその間ずっと control plane 自身のディスク上に
ありながら、一度も読まれずに終わります。

ローカル(非ホスト)バックエンドには、この欠落がありません。`ServeState` はシナリオ store を
`LocalScenarioStore(lambda target: _scenarios_dir_for(self, target))`(`bajutsu/serve/state.py`)として
配線しています。`_scenarios_dir_for` は、各ターゲットのシナリオディレクトリを `state.cwd` を基準に
解決します。Git ソースの config なら checkout のルートを基準にし、それ以外ならローカル config ファイルと
同じディレクトリを基準にします。サーバーバックエンドの `_build_server_state`(`bajutsu/serve/__init__.py`)は、
この解決方法を再利用しません。束縛されている config の出所にかかわらず、各 org のシナリオ store を
無条件に `StorageScenarioStore(ObjectScenarioStorage(...))` として配線します。本提案は、ローカル
バックエンドがすでに持つこの読み取り経路を、サーバーバックエンドにも与えます。

両者の間には、設計を左右する違いが1つあります。ホスト提供の実行は、control plane とファイルシステムを
共有しない別の worker プロセスで行われます
(`bajutsu/serve/server/db_executor.py`、`bajutsu/serve/server/worker_job.py`)。そのため実行に渡す
シナリオは、これまでどおりジョブの `materials` としてテキストで運ぶ必要があります。単なるローカル
パスでは worker に届きません。

## 詳細設計

### 1. サーバーバックエンド向けのローカルツリー読み取り

`ObjectScenarioStorage` と並べて、`ScenarioStorage` の実装を1つ追加します。名前は
`LocalTreeScenarioStorage` とします(`bajutsu/serve/server/scenarios.py`)。構築時には、稼働中の
`ServeState` と、`ObjectScenarioStorage` がすでに受け取っているのと同じ `apps` の取得方法を渡します。
`has_app` と `list` は、`ObjectScenarioStorage` の今日の答え方をそのまま踏襲します。`read` については、
object storage を呼ぶ代わりに、ローカルバックエンドの仕組みへそのまま委ねます。org のターゲットが
与えられると、`_scenarios_dir_for(state, target)`(`bajutsu/serve/state.py`)を呼び、そのターゲットの
シナリオディレクトリを得ます。得られたディレクトリを `LocalScenarioScope`(`bajutsu/serve/scenarios.py`)
に渡し、その `read` を呼びます。このように `LocalScenarioScope` をそのまま再利用すれば、同じ
ディレクトリ解決とパス閉じ込めのロジックを `bajutsu/serve/server/scenarios.py` 側で作り直さずに済み
ます。BE-0051 のパス閉じ込め保護を、すでに実装・テスト済みの1か所に保ったまま、食い違いうる二重実装を
避けられます。

`_build_server_state` は、束縛されている config が何であれ、その `cwd` を解決するだけです。Git
checkout のルート、zip 展開のルート、組み合わせ済みアーティファクトのルート、そのいずれについても
同じです。サーバーバックエンドがすでにサポートしている束縛経路は、すべて同じ場所へツリーを展開します。
そのためこの実装は、config の取得元の種類で分岐する必要がありません。1つの実装で、Git・zip・組み合わせ
アーティファクトのいずれのソースもカバーできます。

`LocalTreeScenarioStorage` にも `save` メソッドは要ります。`ScenarioStorage` プロトコル
(`bajutsu/serve/server/scenarios.py`)がそれを求めるからです。`StorageScenarioStore` と
`StorageScenarioScope` は、それぞれ注入された `ScenarioStorage` を1つだけ保持し、その同じオブジェクトに
対して `save` を呼びます。`LocalTreeScenarioStorage` は、この1点のためだけに、コンストラクタの引数として
`ObjectScenarioStorage` のインスタンスを受け取ります。その `save` メソッドは、このインスタンスへそのまま
委ねます。`has_app`・`list`・`read` はこの委譲先に触れません。触れるのは `save` だけです。こうして
1つのオブジェクトが `ScenarioStorage` プロトコル全体に答えます。ローカルツリーから読み、
`ObjectScenarioStorage` を通じて書きます。新しいプロトコルは不要で、`StorageScenarioScope` が保持する
オブジェクトも1つのままです。

### 2. `runnable()` は materials を運び続け、変わるのはその取得元だけ

`StorageScenarioScope.runnable()`(`bajutsu/serve/server/scenarios.py`)は、これまでどおり `materials` を
運ぶ `Runnable` を返します。変わるのは、シナリオの本文の取得元だけです。object storage への `get_bytes`
呼び出しの代わりに、1で追加した読み取り実装がローカルディスクから読んで供給します。本文は、これまでと
同じく `materials` の1エントリとして worker へ渡されます。worker 側の `_materialize`
(`bajutsu/serve/server/worker_job.py`)が、実行前に worker 自身のワークスペースへその本文を書き出します。
worker 側の契約は変わりません。変わるのは、control plane がその本文をどこから読むかだけです。

### 3. `save` と `authored` は対象外

本提案が変更するのは読み取り経路だけです。`list`・`read`、そして `runnable()` が materials として運ぶ本文の
取得元です。ホスト提供の Web エディタを通じたシナリオの保存も、`record` ジョブによる書き出しも、これまで
どおり `ObjectScenarioStorage` を経由します。この切り分けは、提案者自身の運用にも合っています。シナリオは
ローカルマシンで作成し、バンドルの再アップロードか Git ソースへの push によって control plane へ反映する
運用であり、サーバー上でその場編集する運用ではありません。書き込み側には、その理由から、まだローカル
ツリー側の受け皿がありません。`save`・`authored` をローカルツリー側のシナリオソースとどう整合させるかは、
別の課題です。この課題は、control plane をディスクを共有しない複数レプリカで動かす場合にどうするかも
決めなければなりません。本提案は、両方の課題を、デプロイ構成が固まった段階の後続 BE 項目にあえて残し
ます。まだ固まっていない前提の上に設計はしません。

### 4. 配線

`_build_server_state`(`bajutsu/serve/__init__.py`)は、org ごとの `StorageScenarioStore` の組み立て方
を変えます。従来は `StorageScenarioStore(ObjectScenarioStorage(...))` を直接構築していました。本提案
では、`ObjectScenarioStorage` のインスタンスは従来どおり先に構築し、それを `LocalTreeScenarioStorage`
で包んでから `StorageScenarioStore` へ渡します。`save`・`authored` は、1段深いところで、同じ
`ObjectScenarioStorage` オブジェクトへそのまま届きます。

### 決定性・ゲート・app 非依存性

本項目が変更するのは1点だけです。シナリオの本文を control plane がどこから見つけて読むかです。LLM
呼び出しはどこにも追加せず、実行が何を検証するかも変えません。したがって pass/fail は、今日と変わらず
完全に機械判定のままです(指令1)。本項目はむしろ、隠れた失敗モードを1つ取り除きます。本項目が
なければ、`StorageScenarioScope.read` / `runnable` は、束縛したツリーに実在する Git や zip ソースの
シナリオに対しても「そのようなシナリオはない」という結果を返し、運用者は自分で原因を調べる羽目に
なります。本項目は、その同じシナリオを決定的に解決させます(指令2)。そして app 固有の分岐は一切
ありません。ローカルツリー読み取りが解決するのは、どのターゲットの config にもすでにある `scenarios`
ディレクトリの設定と同じものです(指令3)。

## 検討した代替案

- **現状を維持する。** 却下します。本項目はこの欠落を解消するために存在します。Git や zip ソースの
  シナリオは、誰かが object storage バケットへ手作業で同じ内容を書き直さない限り、ホスト提供 UI の
  一覧・読み取り・実行のいずれからも参照できないままです。
- **束縛時に展開済みツリーを `ObjectScenarioStorage` へコピーし、読み取り経路は変えない。** 却下します。
  これは本項目が取り除こうとしている重複を作り直すことになり、しかも同期が保たれません。組み合わせ済み
  アーティファクトのローカルディレクトリはコンテンツアドレス方式であり、展開すると不変になります
  (`materialize_composition`、`bajutsu/serve/operations/composition.py`)。そのディレクトリが後の
  `save` によって書き換えられても、コピーを再実行する仕組みは何もありません。バケットとディスクの
  内容は、そのまま食い違っていきます。
- **`save`・`authored` も本項目でローカルツリー側へ移す。** いまは却下します。control plane を、ディスクを
  共有しない複数レプリカで動かす場合、シナリオの編集内容をどう永続化し、どう可視にし続けるかを
  決める必要があります。この点は、提案者側でもまだ決まっていません。本項目は、読み取り経路という
  明確でリスクの低い半分だけを先に届けます。書き込み側は、その問いに答えが出てからの後続項目に
  委ねます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 1 — `LocalTreeScenarioStorage` の実装。`has_app`・`list`・`read` は `_scenarios_dir_for` と
  `LocalScenarioScope` への委譲で、`save` は注入された `ObjectScenarioStorage` への委譲で答える。
- [ ] 2 — `runnable()` の変更。`materials` の本文の取得元を `LocalTreeScenarioStorage` にする
  (worker 側の契約、`_materialize` による実行ワークスペースへの書き出しは変更なし)。
- [ ] 3 — `_build_server_state` の配線変更。構築した `ObjectScenarioStorage` を
  `LocalTreeScenarioStorage` で包んでから `StorageScenarioStore` へ渡す。

## 参考

- [CLAUDE.md](../../CLAUDE.md): 決定性優先、app 非依存。
- [BE-0063 — Git リポジトリ + ref から config(とそのシナリオツリー)を読み込む](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md):
  その checkout ルートを基準に、ローカルバックエンドで `_scenarios_dir_for` がすでに解決している Git 束縛経路。
- [BE-0073 — config + シナリオ + アプリバイナリのバンドルを zip でアップロードする](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)、
  [BE-0243 — アップロードした zip config バンドルを object storage へ永続化する](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage-ja.md)、
  [BE-0268 — config・シナリオ・アプリバイナリを、実行ごとに組み合わせる独立したコンテンツアドレス
  アーティファクトとしてアップロードする](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts-ja.md):
  同じ方法で、シナリオツリーを control plane のディスクへ展開する zip・組み合わせアーティファクトの束縛経路。
- [BE-0015 — Web UI のパブリックホスティング](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md):
  サーバーバックエンドと、本項目の `runnable()` 設計が踏まえる control plane / worker の分離。
- 本項目が触れる箇所: `bajutsu/serve/state.py` の `LocalScenarioStore` 配線と `_scenarios_dir_for`。
  `bajutsu/serve/scenarios.py` の `LocalScenarioScope`、`ScenarioStore` / `ScenarioScope` プロトコル。
  `bajutsu/serve/server/scenarios.py` の `ObjectScenarioStorage`、`StorageScenarioScope`、
  `StorageScenarioStore`。`bajutsu/serve/__init__.py` の `_build_server_state`。
  `bajutsu/serve/server/db_executor.py` の `DbQueueExecutor`。`bajutsu/serve/server/worker_job.py` の
  `_materialize` と `execute_job_spec`。

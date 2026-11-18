[English](BE-XXXX-target-command-hooks.md) · **日本語**

# BE-XXXX — ターゲット設定から、ランと各シナリオの前後に外部コマンドを実行する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-target-command-hooks-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | シナリオ記述機能 |
| 関連 | [BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps-ja.md)、[BE-0059](../BE-0059-launch-target-server/BE-0059-launch-target-server-ja.md)、[BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution-ja.md)、[BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks-ja.md)、[BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs-ja.md)、[BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本提案では、ターゲットの設定から、ラン全体と各シナリオの前後に外部コマンドを実行できるようにします。
コマンドは `targets.<name>.hooks` の下に新設する4つのリストに書きます。リストは `beforeRun`、
`afterRun`、`beforeScenario`、`afterScenario` です。コマンドの用途は、Web API（application
programming interface）を介したテストデータの投入、ファイルの配置、アプリ自身の Web サーバーの起動
などです。GitHub Actions のステップが `$GITHUB_OUTPUT` に書くのと同じ要領で、コマンドは出力ファイルを
通じてシナリオに値を渡せます。

どのエントリも、実行して終了するコマンドです。サーバーを起動し、サーバーが応答するまで待ってから
終了する `beforeRun` のスクリプトは、現在の `services.server` と同じ働きをします。そこで本提案は、
`services.server` を準備完了の確認のフィールドごと削除します。あわせて、スキーマにだけ存在し、
どのランも起動したことのない `services.mockServer` も削除します。本提案の後、`services` に残るのは
`mailbox` だけです。`mailbox` は、ランが問い合わせるだけで起動はしない外部の受信箱です。

## 動機

シナリオが依存するテストデータは、どこかで用意する必要があります。シナリオ自身の `setup` フェーズで
`http` ステップを使えば、データを作れます。投入用のエンドポイントへリクエストを1回送るだけなら、
この方法が合っています。準備が複数のリクエスト、ファイルの書き込み、応答に応じた分岐へと
膨らむと、この方法は合わなくなります。シナリオファイルがステップの構文で書いたスクリプトを抱え込み、
そのスクリプトはどれも、テストしたい振る舞いではありません。

Bajutsu は、シナリオの中に汎用の抜け道を意図して設けていません。ユーティリティステップの提案
（[BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps-ja.md)）は、`shell` / `exec` ステップを
却下しました。任意のコマンドを実行できるシナリオは持ち運べなくなり、レビューする人が中身を
読み取れなくなるからです。この判断によって、シナリオは利用者から見える振る舞いの記述にとどまります。
本提案もこの判断を守ります。

現在残っている手段は、`bajutsu run` を CI のスクリプトや `Makefile` のターゲットで包むことです。
この方法でも動きはします。ただし、次の3つが欠けています。

- **シナリオに値が届きません。** ラッパーで export した環境変数は、シナリオのトークンになりません。
  唯一の経路は `evidence.secrets` への宣言です。宣言した値は資格情報と同じ扱いになり、
  すべてのレポートでマスクされます。
- **シナリオ単位で実行できません。** ラッパーはラン全体を1回だけ包みます。シナリオごとに作り直す
  データは、シナリオファイルごとに `bajutsu run` を呼ばない限り用意できません。
- **`serve` を経由すると実行されません。** Web UI から始めたランは CI のラッパーを通らないので、
  準備の手順がまったく実行されません。

Bajutsu はすでに、設定で宣言したコマンドを1つ、ランの前後で実行しています。Web ターゲットの
ホストを起動するために
[BE-0059](../BE-0059-launch-target-server/BE-0059-launch-target-server-ja.md) が追加した
`services.server` です。`services.server` は、プロセスを起動し、準備完了の URL（uniform resource
locator）に問い合わせ、最後にプロセスを止めます。この3つは、どれもスクリプト自身が行えます。
しかも、準備完了の確かめ方を、サーバーに合わせて自由に選べます。`services.server` を新しいフックと
並べて残すと、サーバーを起動する方法が2つになり、読み手が覚えるフィールドの組も2つになります。

本提案が入った後は、次の違いを観察して確かめられます。データの投入を前提とするシナリオは、
投入された識別子を `${hooks.<name>.<key>}` トークンで読みます。シナリオ自身の `setup` には、
データを準備するためだけの `http` ステップが残りません。`demos/serve-ui/dogfood.config.yaml` は
内側のサーバーを `hooks.beforeRun` のスクリプトで起動します。`services.server` を書いた設定は
読み込めなくなります。

## 詳細設計

### 4つのフックのリスト

各リストは `targets.<name>.hooks` の下に、既存の `setup`、`cleanup`、`interrupts` と並べて置きます。
既存の `setup` と `cleanup` は、引き続きシナリオのステップを持ちます。新しいリストが持つのは
外部コマンドです。

| リスト | 実行される時点 | 出力の有効範囲 |
|---|---|---|
| `beforeRun` | 最初のシナリオの前に1回 | ラン全体 |
| `afterRun` | 最後のシナリオの後に1回（ランを抜けるすべての経路で） | なし |
| `beforeScenario` | 各シナリオの前（アプリの起動より前） | そのシナリオ |
| `afterScenario` | 各シナリオの後（判定が出てから） | なし |

```yaml
targets:
  web:
    platform: web
    subject: { url: "http://127.0.0.1:8787/index.html" }
    hooks:
      beforeRun:
        - name: server
          cmd: "./scripts/start-server.sh"    # サーバーを起動し、応答するまで待ってから終了する
          timeout: 60
        - name: catalog
          cmd: "./scripts/seed-catalog.sh"
      beforeScenario:
        - name: user
          cmd: "./scripts/create-user.sh"     # userId=... を $BAJUTSU_OUTPUT に書く
      afterScenario:
        - on: always
          cmd: "./scripts/delete-user.sh"     # $BAJUTSU_HOOK__user__userId を読む
      afterRun:
        - on: always
          cmd: "./scripts/stop-server.sh"
```

シナリオは、自前の準備ステップを持たずに、投入された値を読みます。

```yaml
- name: sign in as a seeded user
  steps:
    - type: { text: "${hooks.user.userId}", into: { id: login.username } }
```

### エントリのフィールド

| フィールド | 使えるリスト | 意味 |
|---|---|---|
| `cmd` | すべて | 実行するコマンドです。シェルと同じ規則で分割し、シェルを介さずに実行します。パイプやリダイレクトには `sh -c` かスクリプトを使います。 |
| `name` | すべて | 識別子です。形式は `[A-Za-z][A-Za-z0-9_]*` で `__` を含まず、ランが動かすすべてのターゲットの4つのリスト全体で一意にします。出力を読まれるエントリでは必須です。 |
| `cwd`、`env` | すべて | 作業ディレクトリと追加の環境変数です。現在の `services.server` と同じです。 |
| `timeout` | すべて | コマンドの制限時間（秒）です。デフォルト値は 300 です。 |
| `on` | `afterRun`、`afterScenario` | `always`、`success`、`failure` のいずれかです。デフォルト値は `always` です。シナリオの `cleanup` ルールと同じ語です。 |
| `mask` | すべて | true のとき、エントリの出力値を `${secrets.*}` と同じく証跡でマスクします。デフォルト値は false です。 |

### 実行の順序

各エントリは、完了するのを待ってから次のエントリを始めます。終了コードが0以外になるか `timeout` を
過ぎると、そのエントリは失敗します。`timeout` を過ぎたときは、コマンドのプロセスグループ全体を
止めます。

スクリプトは、自分が終了した後も動き続けるプロセスを残せます。バックグラウンドで起動したサーバーが
その例です。Bajutsu は、そのプロセスを追跡も停止もしません。サーバーを起動するスクリプトは、
次の2つを受け持ちます。

- **準備完了の確認**：サーバーがリクエストを受け付けるようになってから、スクリプトを終了します。
  確かめ方は、HTTP の問い合わせ、ポートの確認、ログの行など、サーバーに合わせて選びます。
- **後始末**：`afterRun` か `afterScenario` のエントリでサーバーを止めます。

Bajutsu は、各コマンドの出力をパイプではなくログファイルに書き出します。スクリプトの出力を
引き継いだサーバーが残っていても、スクリプトの終了後に Bajutsu が待たされることはありません。

1つのシナリオは、次の順で実行します。

1. `beforeScenario` のエントリを、宣言した順に実行します。
2. デバイスの確保とアプリの起動を、従来どおりに行います。
3. `setup` のステップ、`steps` と `expect`、`cleanup` のルールを、従来どおりに実行します。
4. `on` が判定と一致する `afterScenario` のルールを、宣言した順に実行します。

`beforeScenario` をアプリの起動より前に置くのは、アプリが起動時に投入済みのデータを読めるように
するためです。`preconditions.inheritSetup: false` を指定したシナリオは、ターゲットの `setup` と
あわせて `beforeScenario` も飛ばします。`afterScenario` は、現在のターゲットの `cleanup` と
同じく実行します。

ラン全体も、すべてのシナリオを同じ形で包みます。`beforeRun` は最初のシナリオの前に実行し、
`afterRun` はすべてのシナリオが終わってから実行します。`afterRun` と `afterScenario` は、
取り消されたラン（`SIGTERM` や Web UI の Cancel ボタン）でも実行します。取り消しの猶予時間のうち、
`cleanup` が使うのと同じ上限付きの枠を共有します。

### 失敗と判定

フックがアプリの合否を判定することはありません。フックの失敗によってランやシナリオが失敗するのは、
前提条件や後始末が成り立たなかったからです。コマンドが合否を決めたからではありません。

| 失敗した箇所 | 結果 |
|---|---|
| `beforeRun` | シナリオを1つも実行しません。`afterRun` のうち `always` と `failure` のルールを実行します。ランは終了コード2で終わり、現在の `services.server` の起動失敗と同じ扱いです。 |
| `beforeScenario` | シナリオは `beforeScenario: <name>: …` で失敗し、アプリの起動とステップを飛ばします。`afterScenario` のルールは実行します。 |
| `afterScenario` | シナリオの `cleanup` と同じ規則に従います。合格していたシナリオではその失敗が判定になり、すでに失敗していたシナリオでは元の失敗の後ろに追記します。 |
| `afterRun` | 各シナリオの判定は変わりません。レポートに失敗を記録し、ランは終了コード2で終わります。 |

`afterScenario` や `afterRun` の先のルールが失敗しても、後のルールは実行します。後始末の残りを
飛ばすことは、後始末が避けるべき結果そのものだからです。

### 値の受け渡し（出力ファイルと環境変数）

各エントリは、新しく作った空のファイルを指す環境変数 `BAJUTSU_OUTPUT` を受け取ります。コマンドは、
値ごとに `key=value` の行を1行ずつ書きます。キーは `name` と同じ形式に従います。形式に合わない行が
あればエントリは失敗するので、書き損じが黙って値の欠落になることはありません。

シナリオは値を `${hooks.<name>.<key>}` で読みます。ランのループは、`${secrets.*}` と同じく
アクションを実行する時点でトークンを置き換えます。シナリオのローダーは、各トークンを静的に確かめます。
宣言されていない `name` を指すトークンは、読み込み時のエラーになります。コマンドが書かなかったキーは、
フックとキーの両方を示してステップを失敗させます。シナリオが読めるのは、`beforeRun` の出力と、
自分の `beforeScenario` の出力です。

有効範囲の中で後に実行するコマンドは、先に出た出力を `BAJUTSU_HOOK__<name>__<key>` という名前の
環境変数で受け取ります。二重の下線で区切ると対応が一意に決まります。`name` とキーに `__` を
含められないのはこのためです。各コマンドは、文脈を表す次の環境変数も受け取ります。

| 環境変数 | 渡す対象 |
|---|---|
| `BAJUTSU_RUN_ID`、`BAJUTSU_RUN_DIR`、`BAJUTSU_TARGET` | すべてのエントリ |
| `BAJUTSU_SCENARIO`、`BAJUTSU_SCENARIO_FILE`、`BAJUTSU_WORKER` | `beforeScenario` と `afterScenario` |
| `BAJUTSU_VERDICT`（`success` または `failure`） | `afterRun` と `afterScenario` |

`BAJUTSU_WORKER` は、`run --workers` の下でのワーカーの番号です。シナリオのフックは、そのシナリオを
実行するワーカーの中で実行します。そのため、並列に実行するシナリオではフックも並列に動きます。
共有の状態に触れるスクリプトや、シナリオごとにサーバーを起動するスクリプトは、この番号でデータや
ポートを選べます。

### 複数のターゲットを動かすシナリオ

1つのシナリオは、複数のターゲットを動かせます。その場合、ランは動かすすべてのターゲットの
フックを実行します。各リストの中では、シナリオが宣言した順にターゲットを処理します。`after` の
リストは逆の順に処理し、最後に準備したターゲットから後始末します。フックの `name` は、ランが
動かすすべてのターゲットを通して一意にします。同じ `name` を宣言したターゲットが2つあれば
読み込み時のエラーになるので、`${hooks.<name>.<key>}` トークンの解決先が曖昧になることは
ありません。

### 証跡

コマンドの標準出力と標準エラー出力は、ランのディレクトリの `hooks/<list>/<name または番号>.log`
に書き出します。現在の `services.server` は、どちらも捨てています。レポートは、フックの各フェーズを
シナリオの番号付きステップとは別のブロックとして示します。`setup` と `cleanup` をすでに分けて
示しているのと同じ形です。ログにもマスクを適用します。`${secrets.*}` の値と、`mask: true` の
エントリの出力値は、ログを書き出す前に置き換えます。

### コマンドが動くマシン

コマンドは `bajutsu run` のプロセスの中で、`bajutsu run` を実行するマシン上で動きます。

| ランの始め方 | フックが動くマシン |
|---|---|
| 端末や CI から `bajutsu run` を実行する | そのマシン |
| `serve` の Web UI から始める | ジョブを取った `bajutsu worker`。`serve` 自身はジョブを実行しないためです（[BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs-ja.md)）。 |
| AWS Device Farm のようなデバイスクラウドの batch で始める | batch が `bajutsu run` を実行する、デバイスクラウドのホスト |

フックが呼ぶ投入用の API や、フックが置くファイルは、そのマシンから届く必要があります。アプリではなく
特定のデプロイ環境に属する準備は、これまでどおりの場所に置きます。BE-0432 の Device Farm の
`pre_test_commands` と、BE-0435 の batch のライフサイクルのフックです。

Bajutsu のコマンドごとに、実行するリストは次のとおりです。

| コマンド | `beforeRun` / `afterRun` | `beforeScenario` / `afterScenario` |
|---|---|---|
| `run`（`serve` が worker に渡すジョブを含みます） | 実行します | 実行します |
| `audit` | 実行します | 実行します（シナリオを `run_all` で再生するためです） |
| `record`、`crawl`、`repl` | 実行します（`services.server` の起動を置き換えます） | 実行しません（シナリオファイルを実行しないコマンドだからです） |

### コマンドを実行してよいかの判断

フックは、設定に書かれたとおりのコマンドを実行します。あるマシンが、ある設定のコマンドをそもそも
実行するかどうかは別の判断で、コマンドを実行するマシンの側が受け持ちます。その判断は、姉妹提案
「設定が宣言したコマンドを実行するかを worker が判断する」で定めます。worker の capability
ファイルに、設定の出どころごとの方針を置く形です。姉妹提案は
[BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution-ja.md)
の `serve --upload-exec` を置き換えます。本提案のフックのリストは、姉妹提案が統制するコマンドに
あたります。

本提案は、姉妹提案の後に入れます。先に `services.server` を削除すると、アップロードされた Web の
バンドルが、統制された形でサーバーを起動する手段を失うためです。

姉妹提案の `sandbox` モードでは、1つのターゲットのフックのエントリを、ランの間ずっと動き続ける
1つのコンテナの中で実行します。コンテナは、ターゲットの `hooks.sandbox` で宣言します。

```yaml
hooks:
  sandbox:
    dockerImage: "node:20-slim"   # または dockerfile：バンドル内のパス
    ports: [8787]                 # worker のループバックに公開するポート
```

- **ランごとに1つのコンテナ**：Bajutsu は `beforeRun` の前にコンテナを起動し、各エントリを
  その中で実行します。`beforeRun` のスクリプトが起動したサーバーは、ランが終わるまで動き続けます。
  コンテナは `afterRun` の後に削除します。
- **ポート**：`ports` の各ポートを、worker のループバックの同じ番号のポートに公開します。worker
  上のブラウザやアプリは、`127.0.0.1:<port>` でサーバーに到達します。
- **ファイル**：バンドルは、エントリの `cwd` ではなく、worker が用意したバンドルのディレクトリから
  読み取り専用でマウントします。ランのディレクトリの `hooks/` は書き込み可能でマウントします。
  `BAJUTSU_OUTPUT` と `BAJUTSU_RUN_DIR` は、コンテナ内のパスに書き換えます。

`hooks.sandbox` は、現在の `services.server` が持つ `dockerImage`、`dockerfile`、`port` の
フィールドを置き換えます。フックが `sandbox` モードに当たったのに `hooks.sandbox` を持たない
ターゲットは、デバイスの作業より前に失敗し、メッセージはこのブロックを示します。

### `services.server` と `services.mockServer` の削除

2つの定義はスキーマから外します。どちらかを宣言した設定は読み込みに失敗し、エラーは置き換え先
（サーバーを起動し、応答するまで待つ `beforeRun` のスクリプト）を示します。ローダーが古い名前を
読まないのは、[BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md)
がターゲットのキーを改名したときと同じ規則です。`services.mailbox` は変更しません。

削除にあわせて、リポジトリ内の利用箇所をすべて移行します。

- `demos/serve-ui/dogfood.config.yaml` は、内側のサーバーを2つのスクリプトに移します。`beforeRun`
  のスクリプトがサーバーを起動して応答を待ち、`afterRun` のスクリプトがサーバーを止めます。
- `bajutsu/common/runner/launch_server.py` は削除します。`_start_launch_server_or_exit` を
  呼んでいる5つの呼び出し元は、ラン単位のフックのフェーズを呼ぶように変わります。
- `docs/configuration.md`、`docs/scenarios.md`、`docs/run-loop.md`、`docs/cli.md`、
  `docs/self-hosting.md`、`docs/overview.md`、`docs/developer-guide.md`、`DESIGN.md`、
  `docs/architecture.md` を、振る舞いの変更にあわせて日本語版とともに更新します。

### プライムディレクティブとの関係

- **AI は判定しません。** フックはコマンドと終了コードであり、ランやゲートに大規模言語モデル（LLM）の
  呼び出しは入りません。
- **決定性を優先します。** すべてのコマンドに `timeout` の上限があります。準備完了を待つ処理は
  スクリプトの中に移り、条件の確かめ方はスクリプトの作者が選びます。Bajutsu からスクリプトの中は
  見えず、この点は現在の `services.server` のコマンドと同じです。
- **アプリに依存しません。** フックは `targets.<name>` に置くので、アプリごとの準備は設定に
  とどまります。シナリオのドメイン固有言語（DSL）に加わるのはトークンの名前空間1つで、新しい
  ステップは加わりません。

## 検討した代替案

- **シナリオの中の `shell` ステップ**：BE-0036 と同じ理由で採りません。任意のコマンドを実行する
  シナリオは、持ち運べず、レビューもできなくなります。本提案はコマンドをターゲットの設定に置き、
  運用者が管理する形にします。
- **`services.server` を新しいフックと並べて残す**：採りません。サーバーを起動する方法が2つあると、
  フィールドの組もドキュメントも2つずつ必要になります。`beforeRun` のスクリプトで、
  `services.server` の働きはすべて表せます。
- **フックのエントリに準備完了の URL を残す**：採りません。URL への問い合わせは、準備完了の
  確かめ方の1つにすぎず、スクリプトならどの方法でも使えます。フックの側に問い合わせを持たせると、
  起動済みのサーバーを再利用する規則と、並列のワーカーの下でのその例外も戻ってきます。準備完了は
  スクリプトが受け持ち、`timeout` がその上限を決めます。
- **将来のモックサーバー機能のために `services.mockServer` を残す**：採りません。この定義が
  プロセスを起動したことは一度もありません。モックサーバーも、ほかのサーバーと同じく `beforeRun`
  のスクリプトで起動できます。
- **`subject.build` も `beforeRun` に取り込む**：見送ります。`build` はアプリのバイナリが無いときだけ
  実行され、`serve` はビルド済みバンドルの扱いでこの条件付きの振る舞いに頼っています。移すと
  ビルドの意味が変わるので、別に判断すべき事柄です。
- **`evidence.secrets` で宣言した環境変数で値を渡す**：主な経路としては採りません。すべての値が
  資格情報としてマスクされ、ランの中で作った値はそもそもシナリオに届きません。出力ファイルは両方の
  場合を満たし、秘密の値には `mask: true` でマスクを残せます。
- **フックの出力を `${vars.*}` に混ぜる**：採りません。フックのキーがステップの取り込んだ変数を
  隠してしまうことがあり、シナリオから値の出どころが分からなくなります。名前空間を分ければ、
  出どころがトークンに表れます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] スキーマ：4つのフックのリストとエントリのモデル。`services.server` と `services.mockServer` を削除し、置き換え先を示す読み込みエラーを出す。
- [ ] フック実行部：エントリ、制限時間、出力ファイル、環境変数、ログ、マスク。
- [ ] ラン単位のフェーズ：`beforeRun` / `afterRun` を `run`、`audit`、`record`、`crawl`、`repl` に組み込み、`services.server` の起動を置き換える。
- [ ] シナリオ単位のフェーズ：`beforeScenario` / `afterScenario` をシナリオのライフサイクルに組み込む（`inheritSetup`、ワーカー、複数ターゲットの順序、取り消しを含む）。
- [ ] `${hooks.<name>.<key>}` トークン：読み込み時の静的な確認と、アクション実行時の置き換え。
- [ ] sandbox：`hooks.sandbox` とランごとのコンテナ、そのポート、マウント、パス。
- [ ] レポート：フックのフェーズを独立したブロックで示し、ログへのリンクを付ける。
- [ ] `demos/serve-ui/dogfood.config.yaml` を移行し、ドキュメント、`DESIGN.md`、`docs/architecture.md` を両言語で更新する。

## 参考

- [BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps-ja.md)：ユーティリティステップと、却下された
  `shell` / `exec` ステップ。
- [BE-0059](../BE-0059-launch-target-server/BE-0059-launch-target-server-ja.md)：`launchServer`
  （後の `services.server`）。本提案が置き換えます。
- [BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution-ja.md)：
  アップロードされた設定が宣言するコマンドに対する、現在の方針。
- [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks-ja.md)：
  シナリオ自身の準備と後始末のフェーズ。本提案はその失敗の規則を再利用します。
- [BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs-ja.md)：`serve` はすべての
  ジョブをキューに積み、worker が実行します。
- [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md)：
  ターゲット設定のグループと、ローダーが古い名前を読まない規則。
- [`bajutsu/common/runner/launch_server.py`](../../bajutsu/common/runner/launch_server.py)：
  現在のサーバーの起動、確認、後始末。
- [`bajutsu/cli/_shared.py`](../../bajutsu/cli/_shared.py)：各コマンドが共有する起動処理
  `_start_launch_server_or_exit`。
- [`demos/serve-ui/dogfood.config.yaml`](../../demos/serve-ui/dogfood.config.yaml)：
  `services.server` を宣言している、リポジトリ内で唯一の設定。

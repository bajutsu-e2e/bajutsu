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
| 関連 | [BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps-ja.md)、[BE-0059](../BE-0059-launch-target-server/BE-0059-launch-target-server-ja.md)、[BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution-ja.md)、[BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks-ja.md)、[BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本提案では、ターゲットの設定から、ラン全体と各シナリオの前後に外部コマンドを実行できるようにします。
コマンドは `targets.<name>.hooks` の下に新設する4つのリストに書きます。リストは `beforeRun`、
`afterRun`、`beforeScenario`、`afterScenario` です。コマンドの用途は、Web API（application programming interface）を介したテストデータの投入や
ファイルの配置、アプリ自身の Web サーバーのような常駐プロセスの起動です。GitHub Actions のステップが
`$GITHUB_OUTPUT` に書くのと同じ要領で、コマンドは出力ファイルを通じてシナリオに値を渡せます。

同じ仕組みで、既存の2つのサービス定義を置き換えます。`background: true` と `readyUrl` を持つ
`beforeRun` のエントリは、現在の `services.server` と同じ働きをします。そこで本提案は
`services.server` を削除します。あわせて、スキーマにだけ存在し、どのランも起動したことのない
`services.mockServer` も削除します。本提案の後、`services` に残るのは `mailbox` だけです。
`mailbox` は、ランが問い合わせるだけで起動はしない外部の受信箱です。

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
`services.server` です。`services.server` は、本提案が必要とするフックの特殊な形にあたります。
コマンドに、準備完了の確認と後始末を加えた形だからです。両方を残すと、プロセスを起動する方法が
2つになります。フィールドの組も2つになり、
[BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution-ja.md)
のアップロードの方針の下でも、コードの経路が2本に分かれます。

本提案が入った後は、次の違いを観察して確かめられます。データの投入を前提とするシナリオは、
投入された識別子を `${hooks.<name>.<key>}` トークンで読みます。シナリオ自身の `setup` には、
データを準備するためだけの `http` ステップが残りません。`demos/serve-ui/dogfood.config.yaml` は
内側のサーバーを `hooks.beforeRun` で起動します。`services.server` を書いた設定は読み込めなくなります。

## 詳細設計

### 4つのフックのリスト

各リストは `targets.<name>.hooks` の下に、既存の `setup`、`cleanup`、`interrupts` と並べて置きます。
既存の `setup` と `cleanup` は、引き続きシナリオのステップを持ちます。新しいリストが持つのは
外部コマンドです。

| リスト | 実行される時点 | 出力と常駐プロセスの有効範囲 |
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
          cmd: "python -m http.server 8787"
          background: true
          readyUrl: "http://127.0.0.1:8787/"
          readyTimeout: 30
        - name: catalog
          cmd: "./scripts/seed-catalog.sh"
          timeout: 120
      beforeScenario:
        - name: user
          cmd: "./scripts/create-user.sh"     # userId=... を $BAJUTSU_OUTPUT に書く
      afterScenario:
        - on: always
          cmd: "./scripts/delete-user.sh"     # $BAJUTSU_HOOK__user__userId を読む
      afterRun:
        - on: always
          cmd: "./scripts/reset-catalog.sh"
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
| `timeout` | すべて | フォアグラウンドで実行するコマンドの制限時間（秒）です。デフォルト値は 300 です。常駐のエントリでは使いません。 |
| `background`、`readyUrl`、`readyTimeout` | `beforeRun`、`beforeScenario` | 常駐プロセスを起動し、準備完了を示す URL（uniform resource locator）が応答するまで待ちます。`background: true` のときは2つの準備完了フィールドがどちらも必須です。 |
| `on` | `afterRun`、`afterScenario` | `always`、`success`、`failure` のいずれかです。デフォルト値は `always` です。シナリオの `cleanup` ルールと同じ語です。 |
| `mask` | すべて | true のとき、エントリの出力値を `${secrets.*}` と同じく証跡でマスクします。デフォルト値は false です。 |
| `dockerImage`、`dockerfile`、`port` | すべて | BE-0090 の `sandbox` モードで使う実行環境です。`services.server` から引き継ぎます。 |

常駐のエントリの `readyUrl` にはデフォルト値を設けません。現在の `services.server` は、省略時に
ターゲットの `subject.url` を使います。しかしフックのリストは複数のコマンドを持てるので、
そのデフォルト値を受け持てるのは1つのエントリだけです。そこで準備完了の URL は常に明示します。

### 実行の順序

フォアグラウンドのエントリは、完了するのを待ってから次のエントリを始めます。終了コードが0以外になるか `timeout` を
過ぎると、そのエントリは失敗します。常駐のエントリは、専用のプロセスグループでプロセスを起動し、
`readyUrl` が400未満のステータスを返すまで待ちます。この待機は条件待ちで、現在の
`services.server` と同じ要領でポーリングします。プロセスを起動する前に `readyUrl` が応答した
場合は、応答したサーバーを再利用し、停止もしません。BE-0059 の再利用の規則を引き継ぐためです。

再利用の規則が成り立つのは `beforeRun` だけです。`run --workers` の下では、2つのワーカーが
`beforeScenario` のサーバーを1つ共有しかねません。先に終わったワーカーがそのサーバーを止めると、
もう一方のワーカーのシナリオが途中で壊れます。この共有を、次の2つの規則で防ぎます。

- **ワーカーごとの値**：`beforeScenario` のエントリでは、`cmd`、`readyUrl`、`env` の各値に含まれる
  `${BAJUTSU_WORKER}` という文字列を、Bajutsu がワーカーの番号に置き換えます。たとえば
  `cmd: "./mock --port 90${BAJUTSU_WORKER}"` と
  `readyUrl: "http://127.0.0.1:90${BAJUTSU_WORKER}/"` を書いたエントリは、ワーカーごとに
  サーバーを1つずつ起動します。
- **シナリオ単位では再利用しません**：常駐の `beforeScenario` のエントリは、プロセスを起動する前に
  `readyUrl` が応答した場合、シナリオを失敗させます。その応答は、別のワーカーか消し忘れの
  プロセスがポートを使っていることを意味します。サーバーを共有すると、シナリオどうしの独立が
  崩れます。

常駐のエントリは、置き換え後の `readyUrl` を出力のキー `url` として書き出します。シナリオは
`${hooks.<name>.url}` でワーカーごとのサーバーに到達でき、ポートを自分で計算する必要は
ありません。

1つのシナリオは、次の順で実行します。

1. `beforeScenario` のエントリを、宣言した順に実行します。
2. デバイスの確保とアプリの起動を、従来どおりに行います。
3. `setup` のステップ、`steps` と `expect`、`cleanup` のルールを、従来どおりに実行します。
4. `on` が判定と一致する `afterScenario` のルールを、宣言した順に実行します。
5. シナリオの常駐プロセスを、起動と逆の順に停止します。

`beforeScenario` をアプリの起動より前に置くのは、アプリが起動時に投入済みのデータを読めるように
するためです。`preconditions.inheritSetup: false` を指定したシナリオは、ターゲットの `setup` と
あわせて `beforeScenario` も飛ばします。`afterScenario` は、現在のターゲットの `cleanup` と
同じく実行します。

ラン全体も、すべてのシナリオを同じ形で包みます。`beforeRun` は最初のシナリオの前に実行します。
`afterRun` はすべてのシナリオが終わってから実行し、ラン単位の常駐プロセスは最後に停止します。
`afterRun` と `afterScenario` は、取り消されたラン（`SIGTERM` や Web UI の Cancel ボタン）でも
実行します。取り消しの猶予時間のうち、`cleanup` が使うのと同じ上限付きの枠を共有します。

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
共有の状態に触れるスクリプトは、この番号で担当を分けられます。

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

### フックを実行するコマンド

| コマンド | `beforeRun` / `afterRun` | `beforeScenario` / `afterScenario` |
|---|---|---|
| `run`（`run` を起動する `serve` を含みます） | 実行します | 実行します |
| `audit` | 実行します | 実行します（シナリオを `run_all` で再生するためです） |
| `record`、`crawl`、`repl` | 実行します（`services.server` の起動を置き換えます） | 実行しません（シナリオファイルを実行しないコマンドだからです） |

### アップロードの方針

BE-0090 は、アップロードされたバンドルの設定が宣言するすべてのコマンドを、
`serve --upload-exec=<deny|reuse|sandbox>` で統制しています。4つのフックのリストは、
`services.server` と `services.mockServer` に代わって、この統制の対象に入ります。

- **`sandbox`**：すべてのエントリを使い捨てのコンテナで実行します。コンテナはエントリの
  `dockerImage` か `dockerfile` から作ります。バンドルは、エントリの `cwd` ではなく `serve` が管理する信頼済みのバンドルのルートから、
  現在の BE-0090 の sandbox と同じく読み取り専用でマウントします。出力ファイルは書き込み可能で
  マウントします。どちらの実行環境も宣言していないエントリは失敗し、
  ホスト上での実行に切り替わることはありません。
- **`reuse`**：どのエントリも実行しません。`readyUrl` がすでに応答している常駐のエントリは、
  満たされたものとみなします。それ以外のエントリがあれば、ランは失敗します。
- **`deny`**：どのエントリも実行しません。エントリを1つでも宣言したランは失敗します。

`sandbox` の下では、1つのランのコンテナはすべて、そのラン専用に作った Docker のネットワークに
参加します。sandbox で動くエントリは、常駐のエントリにその `name` をホスト名として到達します
（`http://server:8787/` など）。Bajutsu は、文脈を表す環境変数もコンテナ内のパスに書き換えます。
`BAJUTSU_OUTPUT` と `BAJUTSU_RUN_DIR` は、マウントした出力ファイルとランのディレクトリを指します。
コンテナの中では意味を持たないホスト側のパスは渡しません。

ローカルや Git から読み込んだ設定は、現在と同じく統制の対象外です。

### `services.server` と `services.mockServer` の削除

2つの定義はスキーマから外します。どちらかを宣言した設定は読み込みに失敗し、エラーは置き換え先
（`background: true` を持つ `beforeRun` のエントリ）を示します。ローダーが古い名前を読まないのは、
BE-0452 がターゲットのキーを改名したときと同じ規則です。`services.mailbox` は変更しません。

削除にあわせて、リポジトリ内の利用箇所をすべて移行します。

- `demos/serve-ui/dogfood.config.yaml` は、内側のサーバーを `hooks.beforeRun` に移します。
  これまで `subject.url` から得ていた `readyUrl` は、明示して書き足します。
- `bajutsu/common/runner/launch_server.py` と `sandbox.py` は、フック実行部の常駐エントリを扱う経路に
  なります。`_start_launch_server_or_exit` を呼んでいる5つの呼び出し元は、ラン単位のフックの
  フェーズを呼ぶように変わります。
- `docs/configuration.md`、`docs/scenarios.md`、`docs/run-loop.md`、`docs/cli.md`、
  `docs/self-hosting.md`、`docs/overview.md`、`docs/developer-guide.md`、`DESIGN.md`、
  `docs/architecture.md` を、振る舞いの変更にあわせて日本語版とともに更新します。

### プライム・ディレクティブとの関係

- **AI は判定しません。** フックはコマンドと終了コードであり、ランやゲートに大規模言語モデル（LLM）の呼び出しは
  入りません。
- **決定性を優先します。** 準備完了の確認は条件待ちで、フォアグラウンドのコマンドにはすべて `timeout` の上限が
  あります。スクリプトの内部で待機することは防げません。Bajutsu からスクリプトの中は見えず、
  この点は現在の `services.server` のコマンドと同じです。
- **アプリに依存しません。** フックは `targets.<name>` に置くので、アプリごとの準備は設定に
  とどまります。シナリオのドメイン固有言語（DSL）に加わるのはトークンの名前空間1つで、新しいステップは加わりません。

## 検討した代替案

- **シナリオの中の `shell` ステップ**：BE-0036 と同じ理由で採りません。任意のコマンドを実行する
  シナリオは、持ち運べず、レビューもできなくなります。本提案はコマンドをターゲットの設定に置き、
  運用者が管理し、アップロードの方針が統制する形にします。
- **`services.server` を新しいフックと並べて残す**：採りません。プロセスを起動する方法が2つあると、
  フィールドの組、ドキュメント、BE-0090 の下での分岐が、それぞれ2つずつ必要になります。常駐の `beforeRun`
  エントリで、`services.server` の働きはすべて表せます。
- **将来のモックサーバー機能のために `services.mockServer` を残す**：採りません。この定義が
  プロセスを起動したことは一度もありません。モックサーバーは、準備完了の URL を持つ常駐コマンドとして
  書けます。スタブの管理が本当に必要になれば、専用の定義を別の提案で戻せます。
- **`subject.build` も `beforeRun` に取り込む**：見送ります。`build` はアプリのバイナリが無いときだけ
  実行され、`serve` はビルド済みバンドルの扱いでこの条件付きの振る舞いに頼っています。移すと
  ビルドの意味が変わるので、別に判断すべき事柄です。
- **`evidence.secrets` で宣言した環境変数で値を渡す**：主な経路としては採りません。すべての値が
  資格情報としてマスクされ、ランの中で作った値はそもそもシナリオに届きません。出力ファイルは両方の
  場合を満たし、秘密の値には `mask: true` でマスクを残せます。
- **フックの出力を `${vars.*}` に混ぜる**：採りません。フックのキーがステップの取り込んだ変数を
  隠してしまうことがあり、シナリオから値の出どころが分からなくなります。名前空間を分ければ、
  出どころがトークンに表れます。
- **常駐エントリの `readyUrl` のデフォルト値を `subject.url` にする**：採りません。1つのリストに常駐の
  エントリが複数あり得るので、そのうち1つだけに暗黙のデフォルト値を与えると、読み手が知っておくべき
  規則が増えます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] スキーマ：4つのフックのリストとエントリのモデル。`services.server` と `services.mockServer` を削除し、置き換え先を示す読み込みエラーを出す。
- [ ] フック実行部：フォアグラウンドと常駐のエントリ、出力ファイル、環境変数、ログ、マスク。
- [ ] ラン単位のフェーズ：`beforeRun` / `afterRun` を `run`、`audit`、`record`、`crawl`、`repl` に組み込み、`services.server` の起動を置き換える。
- [ ] シナリオ単位のフェーズ：`beforeScenario` / `afterScenario` をシナリオのライフサイクルに組み込む（`inheritSetup`、ワーカー、取り消しを含む）。
- [ ] `${hooks.<name>.<key>}` トークン：読み込み時の静的な確認と、アクション実行時の置き換え。
- [ ] アップロードの方針：BE-0090 の `deny` / `reuse` / `sandbox` を、すべてのフックのエントリに広げる。
- [ ] レポート：フックのフェーズを独立したブロックで示し、ログへのリンクを付ける。
- [ ] `demos/serve-ui/dogfood.config.yaml` を移行し、ドキュメント、`DESIGN.md`、`docs/architecture.md` を両言語で更新する。

## 参考

- [BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps-ja.md)：ユーティリティステップと、却下された
  `shell` / `exec` ステップ。
- [BE-0059](../BE-0059-launch-target-server/BE-0059-launch-target-server-ja.md)：`launchServer`
  （後の `services.server`）。本提案が置き換えます。
- [BE-0090](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution-ja.md)：
  設定で宣言したコマンドに対するアップロードの方針。
- [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks-ja.md)：
  シナリオ自身の準備と後始末のフェーズ。本提案はその失敗の規則を再利用します。
- [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md)：
  ターゲット設定のグループと、ローダーが古い名前を読まない規則。
- [`bajutsu/common/runner/launch_server.py`](../../bajutsu/common/runner/launch_server.py)：
  現在のサーバーの起動、確認、後始末。
- [`bajutsu/cli/_shared.py`](../../bajutsu/cli/_shared.py)：各コマンドが共有する起動処理
  `_start_launch_server_or_exit`。
- [`demos/serve-ui/dogfood.config.yaml`](../../demos/serve-ui/dogfood.config.yaml)：
  `services.server` を宣言している、リポジトリ内で唯一の設定。

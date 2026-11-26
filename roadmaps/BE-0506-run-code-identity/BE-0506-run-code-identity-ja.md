[English](BE-0506-run-code-identity.md) · **日本語**

# BE-0506 — 実行が対象としたアプリのビルドとソースの commit を記録する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0506](BE-0506-run-code-identity-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0506") |
| 実装 PR | [#2559](https://github.com/bajutsu-e2e/bajutsu/pull/2559) |
| トピック | 検証とカバレッジ |
<!-- /BE-METADATA -->

## はじめに

run の `manifest.json` には、`provenance` というスタンプがあります。スタンプが示すのは、実行したシナリオとツールです。テスト対象のアプリは示しません。

そこで、run が操作するすべての target について、アプリの情報をスタンプに加えることを提案します。加えるのは、アプリバイナリのハッシュと、アプリのバージョンです。アプリのソースリポジトリの Git commit も加えます。Bajutsu 自身のチェックアウトの commit も、スタンプに入ります。

runner が読み取れなかった値は、理由を添えた欠落として残します。証跡を読めば、判定を出したのがどのコードのどのビルドか、言えるようになります。裏付けのない事実がどれかも、読み手に見えます。

## 動機

現状の証跡は、run がどのコードをテストしたのかを示せません。`provenance`（`bajutsu/common/report/manifest.py:230`）に入るのは、`scenarioHash` と `toolVersion` です。任意の `gitRevision` と、Git から取得した設定の `configSource` も入ります。アプリの痕跡はありません。先週のレポートは、シナリオとデバイスを示しますが、そのシナリオが操作したビルドを示しません。

既存の `gitRevision` は、この穴を埋めません。runner はカレントディレクトリで `git rev-parse HEAD` を実行します（`git_revision()`、`manifest.py:21`）。値が指すのは、Bajutsu、設定のリポジトリ、アプリのソースのどれかです。どれになるかは、run を起動した場所で決まります。manifest には、どれなのかを示す情報がありません。未コミット変更のフラグもないので、変更を含むツリーの run と、クリーンな commit の run を区別できません。`git` がエラーを返すと、`git_revision()` はキーを書きません。revision がないことと、記録を試みなかったことが、同じに見えます。

この欠落が問題になるのは、2 回の run で判定が入れ替わったときです。シナリオのハッシュは同じです。フレーク分析は両方の run を同じ集団に入れ、フレークと呼びます。原因が再ビルドでも、それを示す記録はありません。問題を再現したいレビュアーも、どのバイナリを入れるべきか判断できません。

観察できる結果は 4 つあります。

ビルド済みアプリを起動した run は、各 target の `provenance.targets.<name>.app.hash` を記録します。同じバイナリの 2 回の run は同じハッシュを報告し、再ビルド後の run は別のハッシュを報告します。

ソースツリーに未コミット変更があれば、`appSource.uncommitted` が `true` になります。読み取れなかった値は、理由を添えた `null` として現れます。証跡には、沈黙ではなく欠落が残ります。

## 詳細設計

### スタンプに記録する内容

runner は `provenance` に、target 名をキーにした `targets` の対応表を加えます。run 全体の `toolSource` も加えます。target が 1 つの run なら、エントリは 1 つです。複数の target を操作する run（BE-0428）には、宣言された target ごとにエントリがあります。各エントリは、その target 自身のバイナリとソースリポジトリから作ります。

ハッシュは、256 ビットの Secure Hash Algorithm (SHA) のダイジェストです。`.apk` のような単一ファイルの種別は "file"、iOS の `.app` のようなディレクトリの種別は "tree" です。

次の `app` と `appSource` のキーは、各 `targets.<name>` エントリの下に置きます。

| キー | 内容 |
|---|---|
| `app.hash` | `app.path` にあるバイナリの `sha256:<hex>` ダイジェスト |
| `app.hashKind` | `file` または `tree` |
| `app.version` | `CFBundleShortVersionString` または `versionName` |
| `app.buildNumber` | `CFBundleVersion` または `versionCode` |
| `appSource.commit` | アプリのソースリポジトリの完全な commit ハッシュ |
| `appSource.uncommitted` | ソースツリーに未コミット変更があれば `true` |

`toolSource` オブジェクトは、Bajutsu 自身のチェックアウトを表します。`provenance` スタンプの最上位に置きます。このオブジェクトがあるときは、キー `source` と `commit` を必ず持ちます。`source` は、識別情報の出どころを示し、値は `checkout` か `build-arg` です。`uncommitted` を持つのは、`source` が `checkout` のときだけです。ビルド時の commit には、比べる作業ツリーがありません。チェックアウトと `BAJUTSU_BUILD_COMMIT` のどちらもないときは、`toolSource` 自体を省きます。

`gitRevision` の意味と値は、変えません。これを読む既存の処理は、そのまま動きます。serve のジョブ一覧も、読み手の 1 つです。その関数 `_run_provenance`（`bajutsu/serve/jobs.py:836`）が、スタンプをデータベースのレコードに写します。新しいキーのない run は、古い run として読みます。manifest の `schemaVersion` は、18 から 19 に上がります。

### 各値の取得方法

**バイナリのハッシュ。** runner は target ごとに、解決済みの `app.path` にあるバイナリを読みます。読むのは、`build_app`（`bajutsu/common/runner/build.py:41`）を実行した後です。ハッシュは、run が起動するバイナリそのものを対象にします。

ファイルは、バイト列をストリームで読んでハッシュします。ディレクトリはツリーとして扱います。runner は、通常ファイルとシンボリックリンクをすべて、相対パスの昇順に並べます。リンクはたどりません。エントリごとに 1 行、`<相対パス>\0<種別>\0<エントリのハッシュ>\n` をハッシュします。種別は `file` か `link` です。通常ファイルは、バイト列のハッシュを使います。シンボリックリンクは、リンク先の文字列のハッシュを使います。コード署名のファイルも含めます。再署名したビルドは、別の成果物だからです。

**バージョン。** iOS の `.app` は、バンドルの `Info.plist` を `plistlib` で読みます。Android は、`app.path` の `.apk` を zip アーカイブとして開きます。中のバイナリ形式の `AndroidManifest.xml` は、`bajutsu/common/` に置く小さなデコーダで解釈します。デコーダは、`versionName` と `versionCode` を読みます。依存を足さず、Android の Software Development Kit (SDK) も呼びません。バージョンは、ハッシュと同じファイルから取れます。パッケージを共有する target も、それぞれ自分の `.apk` の値を報告します。インストール後にデバイスが返すバージョンは、別の事実です。この項目は、スタンプに含めません。どちらの読み取りにも、Large Language Model (LLM) は関与しません。

**ソースの commit。** 新しい任意の target キー `sourceRoot` で、アプリのソースリポジトリを指します。runner は、`app.path` と同じく、設定のルートに対してこのキーを解決します。そこで `git rev-parse HEAD` と `git status --porcelain` を実行します。commit が表すのは作業ツリーであって、バイナリではありません。スタンプは両者を別々に記録するので、食い違いが読み手に見えます。

**ツール自身の commit。** runner は、Bajutsu 自身のチェックアウトを、`server_checkout()` が使うヘルパーで読みます。このヘルパーは [BE-0272](../BE-0272-serve-version-badge/BE-0272-serve-version-badge-ja.md) が導入しました。ヘルパーは、カレントディレクトリではなく、パッケージのディレクトリを起点に Git を実行します。ヘルパーを `bajutsu/serve/operations/version.py` から `bajutsu/common/` に移します。runner は、`serve` への依存なしで import できます。

pip でインストールした版には、チェックアウトがありません。このときは、ビルド時の `BAJUTSU_BUILD_COMMIT` に切り替えます。この値は [BE-0277](../BE-0277-docker-build-commit-badge/BE-0277-docker-build-commit-badge-ja.md) が加えました。`toolSource` は `source: build-arg` を記録し、`uncommitted` は付けません。実際のチェックアウトなら、`source: checkout` を記録します。

runner がチェックアウトとして受け入れるのは、そのチェックアウトが Bajutsu のパッケージディレクトリを追跡しているときです。確認には、パッケージの `__init__.py` に対する `git ls-files --error-unmatch` を使います。別のリポジトリの内側にあるインストールは、チェックアウトなしとして扱います。アプリのツリー配下の virtualenv が一例です。この確認がないと、`git` が site-packages から親を遡ります。アプリの commit が、Bajutsu のものとして記録されます。

### 読み取れなかった値

読み取れない値が、判定を変えたり、run を止めたりすることはありません。スタンプはアサーションではなく、メタデータです。LLM は関与しないので、原則 1 は守られます。ゲートが読むのは、機械的に検査できる判定だけです。runner はキーを落とさず、欠落を記録します。

キーはスタンプに残り、値は `null` になります。同じオブジェクトの `unavailable` が、そのキーごとに理由を示します。たとえば `"hash": "path-missing"` です。理由は、固定の集合のコードです。生のエラーメッセージは使いません。

生のメッセージには、ローカルのパスやトークンが入りえます。run ディレクトリは、ほかではどちらも出しません。固定の集合は次のとおりです。

| コード | 意味 |
|---|---|
| `path-missing` | `app.path` にバイナリがない |
| `read-error` | runner がバイナリを読めなかった |
| `plist-unreadable` | `Info.plist` を解釈できなかった |
| `apk-unreadable` | `.apk` を zip として開けない、またはマニフェストがない |
| `manifest-unparsable` | `.apk` 内のマニフェストをデコードできなかった |
| `not-a-git-repo` | `sourceRoot` が Git チェックアウトの内側にない |
| `git-unavailable` | `git` コマンドがない、またはタイムアウトした |
| `status-read-failed` | `git status` がエラーになったか、実行しなかった |

target が設定していないグループは、別の扱いです。`app.path` を持たない target では、ハッシュを試す対象がありません。デバイスへ事前に入れたアプリが一例です。runner は、その target の `app` グループを省略します。`sourceRoot` がなければ、`appSource` も同じく省略します。省略は「設定されていない」ことを表します。理由つきの `null` は、「試みたが読めなかった」ことを表します。読み手は両者を区別できます。

### スタンプの出力先

`manifest.json` には、上記のキーを `provenance` の下に入れます。pipeline（`bajutsu/common/runner/pipeline.py:2497`）は、すでに `run_provenance` を呼んでいます。pool は、シナリオごとと、メンバーのリースごとに呼びます。呼び出しの位置は `bajutsu/common/runner/pool.py:504` と `:653` です。

runner は、各 target のスタンプを run ごとに 1 回計算します。計算は、その target の `build_app` の後で、最初のリースより前に行います。結果の対応表を、すべての呼び出しに渡します。各バイナリのハッシュ計算は、run ごとに 1 回で済みます。run の途中で再ビルドが起きても、シナリオごとに値が変わることはありません。

`ctrf.json` は、Common Test Report Format (CTRF) の出力です。その `environment` は、すでに `provenance` を読んでいます。各 target のアプリのハッシュとバージョン、各 target のソースの commit、ツールの commit を加えます。欠落は、理由つきの `unavailable` エントリとして現れます。

`report.html` では、run のタイトルの下に、target ごとに 1 行のヘッダーを置きます。各行は、アプリのバージョンと短縮ハッシュを示します。フラグが `true` なら、「未コミット」の印も出ます。欠落は、理由つきで「不明」と表示します。

### 対象外

**Web の target。** Web の target が指すのは、バイナリではなく Uniform Resource Locator (URL) です。デプロイ済みの識別子をレスポンスヘッダーやエンドポイントから読むには、サービス側の協力が要ります。別の提案で扱えます。

**フレーク分析のグルーピング。** `bajutsu flakiness` は、引き続き `scenarioHash` で run をまとめます。`app.hash` でグループを分ける変更は、この項目が記録するフィールドを土台にした後続の作業です。

**ビルド時の埋め込み。** アプリのビルドが、自分のメタデータに commit を書き込む方式があります。読み取るには、すべてのアプリのビルドを変える必要があります。この項目では、その変更を求めません。

### 作業分解

| # | やること |
|---|---|
| 1 | Git の識別情報を読むヘルパーを `bajutsu/common/` に移し、ディレクトリ引数を足す |
| 2 | バイナリのハッシュ計算、iOS のバージョン読み取り、Android のマニフェストデコーダを加える |
| 3 | 任意の target キー `sourceRoot` を設定スキーマに加える |
| 4 | 各 target のスタンプを run ごとに 1 回計算し、`schemaVersion` を 19 に上げる |
| 5 | 欠落も含めて、スタンプを `ctrf.json` と `report.html` に出す |
| 6 | `docs/` と `docs/ja/`、`DESIGN.md`、`docs/architecture.md` を更新する |

2 では、値か、固定集合の理由コードのどちらかを返します。3 では、スキーマのテストを書きます。4 では、`targets` の対応表を pipeline と pool の `provenance` に渡します。6 では、`provenance` に触れる記述を更新します。

各単位は、自分のユニットテストを持ちます。ハッシュ計算のテストは、小さなディレクトリのフィクスチャを作ります。ファイル名の変更、1 バイトの編集、シンボリックリンクの追加が、それぞれハッシュを変えることを確かめます。内容が同じファイルとリンクが、別のハッシュになることも確かめます。デコーダのテストは、小さな `.apk` のフィクスチャと、途中で切れたものを与えます。Git 読み取りのテストは、パッケージディレクトリを無関係なリポジトリの内側に置きます。`toolSource` が生まれないことを確かめます。

欠落のテストは、存在しないバイナリと、リポジトリの外にある `sourceRoot` を target に指定します。run が成功のまま終わることを確かめます。各キーが `null` で、`unavailable` が理由を示すことも確かめます。マルチ target のテストは、宣言したすべての target が自分のエントリを持つことを確かめます。

## 検討した代替案

### commit をコマンドラインや環境変数で渡す

runner は、呼び出し側の申告を信じることになります。渡し忘れても、警告は出ません。runner が自分で読む値は、ディスク上のファイルと食い違いません。

### アプリのビルドがメタデータに埋め込んだ commit を読む

値は常にバイナリと一致します。ただし、すべてのアプリがビルドを変える必要があります。Bajutsu はアプリ非依存（原則 3）なので、見送ります。スタンプのキーには、あとで足せる余地を残します。

### バイナリのハッシュを持たず、Git commit だけを記録する

commit が指すのは、ソースの状態であって、成果物ではありません。ビルドフラグを変えた再ビルドは、commit が同じで、バイナリが違います。別の場所で作った成果物も、同じ問題を抱えます。

### 別の `evidence.json` を書く

`provenance` は、すでに run の識別スタンプです。フレーク分析も、これを読みます。別ファイルにすると、1 つの概念が 2 か所に分かれます。

### ディレクトリをアーカイブのバイト列でハッシュする

タイムスタンプなどのアーカイブのメタデータで、バイナリが同じでもハッシュが変わります。ファイル単位のハッシュを並べる方式は、内容だけに依存します。

### `gitRevision` の意味を特定のリポジトリに変える

既存の読み手が、現在の意味に依存しています。明示的な名前の新しいキーなら、意味が黙って変わることを避けられます。

### 読み取れなかったキーを省略する

読み手は、読み取りのエラーと、その値を設定していない target を区別できません。欠落を記録すれば、証跡が裏付けられないことを正直に示せます。

### マルチ target の run で先頭の target だけを記録する

manifest は 1 つのビルドの識別情報を主張します。判定は、別のアプリのビルドにも依存しています。target ごとの対応表のコストは、入れ子 1 段で済みます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] Git の識別情報を読むヘルパーを `bajutsu/common/` に移す
- [x] バイナリのハッシュ計算とバージョン読み取りを加える
- [x] target キー `sourceRoot` を加える
- [x] すべての target のスタンプを run ごとに 1 回記録し、`schemaVersion` を 19 に上げる
- [x] 欠落も含めて、スタンプを `ctrf.json` と `report.html` に出す
- [x] 日英のドキュメント、`DESIGN.md`、`docs/architecture.md` を更新する

ログは次のとおりです。

- 2026-11-26 — [#2559](https://github.com/bajutsu-e2e/bajutsu/pull/2559) で 6 つの単位をまとめて実装

上の設計から踏み込んだ点は次の表のとおりです。

| 点 | 内容と理由 |
|---|---|
| キーの場所 | `subject.path` と並ぶ `subject.sourceRoot` に配置。BE-0470 以降、`app` グループの名前は `subject` |
| 別経路の導入 | デバイスプロバイダと iOS の実機では `app` を省略。`subject.path` のファイルがテスト対象とは言えないため |
| 読む時点 | `beforeRun` フックの後。フックがアプリを書き換える場合があるため |
| 追跡のない対象 | 追跡ファイルのない `sourceRoot` は `not-a-git-repo`。空のバンドルは `path-missing` |
| CTRF | `uncommitted` も出力。コミットだけではクリーンに見えるため |
| Git の読み取り | リポジトリ側の `core.fsmonitor` とフックは無効化。アップロードや遠隔の config では `git status` を実行せず `status-read-failed` |

バージョンの値がない `Info.plist` は `plist-unreadable` と記録します。マニフェストの場合は `manifest-unparsable` です。`DESIGN.md` にはスタンプのキーの記述がないので、変更していません。

## 参考

[BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md) は、この項目が拡張する `provenance` スタンプを導入しました。

[BE-0272](../BE-0272-serve-version-badge/BE-0272-serve-version-badge-ja.md) は、この項目が再利用する Git 識別情報のヘルパーを導入しました。

[BE-0277](../BE-0277-docker-build-commit-badge/BE-0277-docker-build-commit-badge-ja.md) は、`.git` のない環境のための、ビルド時 commit のフォールバックを導入しました。

[BE-0161](../BE-0161-ctrf-report-export/BE-0161-ctrf-report-export-ja.md) は、`provenance` を `environment` に載せる CTRF の出力を導入しました。

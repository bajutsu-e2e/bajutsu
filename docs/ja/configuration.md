[English](../configuration.md) · **日本語**

# 設定、ターゲットのオンボーディング、doctor

ツール本体はアプリ非依存です。アプリ固有の差分はすべて config に置くので、同じバイナリと同じドライバで複数のターゲットを実行できます。ターゲットを追加するときは `targets.<name>` を 1 つ加えるだけです。

実装: `bajutsu/config.py`（解決） · `bajutsu/doctor.py`（規約充足度スコア）。config はリポジトリのルートには同梱されていません。`--config`（既定のファイル名は `bajutsu.config.yaml`）で渡します。デモにはすぐ動くものが同梱されています（例: [`demos/features/demo.config.yaml`](../../demos/features/demo.config.yaml)（iOS）、[`demos/web/demo.config.yaml`](../../demos/web/demo.config.yaml)（web））。

関連: [concepts のアプリ非依存](concepts.md#6-アプリ非依存差分は-config-に寄せる) · [drivers](drivers.md) · [scenarios](scenarios.md)

---

## 設定の階層（defaults × targets）

`bajutsu.config.yaml` は 2 層で構成されています。値の解決順は **既定 < ターゲット < シナリオ** で、テストに近い方が優先されます。

```yaml
defaults:                       # 全ターゲット共通の既定
  backend: [ios]                # 順序付きリスト。プラットフォーム(ios/android/web/fake)か actuator(idb)。単一文字列も可
  device:  "iPhone 15"
  locale:  en_US
  capture: [screenshot.after, elements, actionLog]
  redact:  { headers: [Authorization, Cookie], fields: [token, password] }
  secrets: [LOGIN_PASSWORD]         # ${secrets.X} に使える環境変数名（実値は証跡でマスク）
  reservedNamespaces: [auth, nav]   # 共有フロー / コンポーネントの id 契約（情報用）

targets:
  sample:                       # ← --target sample で選択
    bundleId:       com.bajutsu.sample     # iOS のターゲット（web で baseUrl を設定する場合を除き必須）
    deeplinkScheme: bajutsusample
    idNamespaces:   [home, list, counter, settings, onboarding, auth, nav, comp, ctrl, text, lists]
    launchEnv:      { SAMPLE_UITEST: "1" }
    scenarios:      demos/features/app/scenarios   # このターゲットのシナリオディレクトリ（run が読み、record が書く）
    # 任意: backend / device / locale / launchArgs / setup / redact / secrets / mockServer / appPath / build

  web:                          # web ターゲット（Playwright backend）は URL で指定する
    baseUrl:   "http://127.0.0.1:8787/index.html"   # web では必須（bundleId の代わり）
    backend:   [web]
    headless:  true                                 # web のみ: false でブラウザを画面に表示（--headed が実行ごとに上書き）
    browser:   chromium                             # web のみ: 描画エンジン。chromium / firefox / webkit（--browser が実行ごとに上書き）
    scenarios: demos/web/scenarios
```

ターゲット項目には `bundleId`（iOS）か `baseUrl`（web）の**どちらか**が必要で、どちらも無い config は読み込み時に拒否されます。[drivers → Playwright](drivers.md#playwrightweb) と `demos/web` を参照してください。

### 解決（`resolve` → `Effective`）

`resolve(config, target)` が 1 ターゲット分の有効値 `Effective`（frozen dataclass）を構築します。ターゲットが未定義の場合は `KeyError` となり、CLI は終了コード 2 で終了します。

| `Effective` フィールド | 由来 | 備考 |
|---|---|---|
| `bundle_id` | app | iOS のターゲット。`base_url` が無いとき必須 |
| `base_url` | app | web のターゲット URL（Playwright backend）。web では `bundle_id` の代わりに必須 |
| `headless` | app | web backend のみ: `true`（既定）はヘッドレス、`false` はブラウザを画面に表示し低速再生する。`bajutsu run --headed / --no-headed` と Web UI の「show browser」トグルが実行ごとに上書きする。iOS は無視する |
| `browser` | app | web backend のみ: 駆動する Playwright の描画エンジン。`chromium`（既定）、`firefox`、`webkit` から選びます。いずれも Linux 上でヘッドレス実行できます。`bajutsu run/record --browser <engine>` が実行ごとに上書きします（フラグ > config > 既定）。エンジンのブラウザバイナリが無ければ実行時に取得します。未知の値は config 読み込み時に拒否されます。iOS は無視します（[BE-0076](../../roadmaps/in-progress/BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines-ja.md)） |
| `launch_server` | app | 任意の `launchServer: {cmd, readyUrl, readyTimeout, cwd, env}`。run のために `baseUrl` のホストを起動し、終わったら停止します。`readyUrl`（既定は `baseUrl`）をプローブし、すでに応答すれば再利用、しなければ `cmd` を起動して準備が整うまで待ちます（固定 sleep ではなく条件待ち）。iOS の `build` の web 版です（[BE-0059](../../roadmaps/implemented/BE-0059-launch-target-server/BE-0059-launch-target-server-ja.md)）。`serve` 上の**アップロードされた**バンドルでは、ホストが `cmd` を直接実行することはなく、`serve --upload-exec` が統制します（[セルフホスティング](self-hosting.md#アップロードされた-config-のコマンド実行be-0090)を参照）。`sandbox` での実行には、追加フィールドとして `dockerImage`（Docker イメージ参照。例 `node:20-slim`）か `dockerfile`（バンドル相対のパスで、`docker build` でビルドします）のどちらか一方、加えて `port`（コンテナ内の待ち受けポート。ループバックのホストポートへ publish します）が必要です（[BE-0090](../../roadmaps/in-progress/BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution-ja.md)） |
| `deeplink_scheme` | app | preconditions の deeplink で使う scheme |
| `backend` | app ?? defaults | プラットフォーム(`ios`/`android`/`web`/`fake`)か actuator(`idb`)の安定度順リスト（単一文字列はリスト化）（[drivers](drivers.md#バックエンド選択と-actuator)） |
| `device` / `locale` | app ?? defaults | `locale` は launch 時に適用される（`simctl` の launch 引数） |
| `launch_env` / `launch_args` | app | preconditions が run 時にマージ追記 |
| `ready_when` | app | 任意の `readyWhen: { id: … }`。run を始める前に launch が出現を待つセレクタで、既定の「アプリが 2 要素以上を描画した」判定の代わりになります。最初の操作画面が常時表示のクロームの上に出るモーダルであるアプリ向けです（要素数の判定はモーダル提示前に返ってしまうことがあります）。固定 sleep ではなく条件待ちです。指定するのは、その target の**すべて**のシナリオが同じ画面から始まるときに限ります。シナリオごとに最初の画面が異なる場合は、各シナリオの先頭に `wait` ステップを置いてください |
| `id_namespaces` | app | doctor が参照 |
| `reserved_namespaces` | defaults | 情報用（doctor は app の `idNamespaces` のみで採点） |
| `mock_server` | app | ⚠️ スキーマのみ · 未配線 |
| `setup` | app | 既定の再利用前段（その steps を各シナリオの本編前に実行） |
| `scenarios` | app | このターゲットのシナリオディレクトリ。`run --target` は配下の `*.yaml` を全件読み、`record` は新規をここへ書く。実行 cwd 基準の相対パス。`run --scenario` / `record --out` で上書き |
| `capture` | defaults | 既定証跡（[evidence の注記](evidence.md#証跡の指示方法3-つ)） |
| `redact` | defaults ∪ app | マージ（下記） |
| `secrets` | defaults ∪ app | `${secrets.X}` を宣言する環境変数名。実値は証跡でマスク（[evidence](evidence.md#マスキングredact)） |

`backend` フィールドの検証で `_norm` が「単一文字列 → 1 要素リスト」に正規化します（defaults / app の両方に適用）。

### redact のマージ

config の `defaults.redact` と `targets.<name>.redact` は **union** されます（`_merge_redact` が `labels`/`headers`/`fields` を個別に和集合します）。さらにシナリオの `redact`（[evidence](evidence.md#マスキングredact)）が重なります。

### シークレット（`secrets:`）

`secrets:` は **環境変数名のリスト**で（`defaults` と `targets.<name>` の両方で宣言でき、`resolve` が和集合にします）、シナリオが入力に使える `${secrets.X}` 変数の宣言元です。`bajutsu run` は実行時に、宣言された各名前を環境から解決し、その値を action に展開（`${secrets.X}`）したうえで、**証跡に現れる箇所すべてでその実値をマスク**します（[evidence](evidence.md#マスキングredact)）。シナリオ source には `${secrets.X}` トークンだけが残り、実値は残りません。

### mailbox（`email` ステップ）

`targets.<name>.mailbox` は、[`email`](scenarios.md#emailメールで届くコードをメールボックスから取得) ステップが 2FA / 検証コードを取得するためにポーリングする汎用 HTTP メールボックスを設定します。エンドポイントと認証情報をシナリオではなく config に置くためのものです。

```yaml
targets:
  myapp:
    mailbox:
      url: "${secrets.MAILBOX_URL}"                       # 受信箱エンドポイント（GET）。${secrets.*} は実行時に解決
      headers: { Authorization: "Bearer ${secrets.MAILBOX_TOKEN}" }
      # 任意のレスポンスマッピング。プロバイダ固有コードなしで任意の JSON を読むため:
      messages: "items"                                   # メッセージ配列へのドット区切りパス（既定: レスポンス自体が配列）
      fields: { to: to, subject: subject, body: text, receivedAt: receivedAt, id: id }
```

既定値はよくある形（`to` / `subject` / `body` / `receivedAt` / `id` を持つメッセージの配列）に合わせるので、準拠 API では `messages` / `fields` のマッピングは不要です。`email` ステップは受信箱を HTTP で読み、ステップ開始より新しいメッセージ（`id` で判定）だけを残し、一致するものを待ってコードを取り出します。決定的で LLM 非依存です（[BE-0046](../../roadmaps/implemented/BE-0046-otp-email-steps/BE-0046-otp-email-steps-ja.md)）。

### org（`orgs:`、マルチテナントのサーバ backend）

`orgs:` は、ホスト型サーバ backend のテナントを宣言します（[BE-0015](../../roadmaps/proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)）。各 org は、所属メンバー（明示の GitHub login＝`members`、および／または GitHub org 全体＝`githubOrgs`）と、その org が持つ targets を列挙します。

```yaml
orgs:
  acme:
    members: [alice, bob]    # 明示の GitHub login
    githubOrgs: [acme-gh]    # この GitHub org の全員（read:org の OAuth scope が必要）
    targets: [demo, checkout]
```

OAuth ログイン時にユーザは自分の org に割り当てられます（まず明示の `members`、無ければ GitHub org メンバーシップからの `githubOrgs` 一致）。以後はその org の targets だけが見え、run の artifacts／scenarios／baselines はその org 専用のオブジェクトストレージ prefix の下に置かれます。どの org にも挙げられていない login や app は単一の `default` org に入るので、`orgs:` ブロックの**無い** config はシングルテナントです。CLI とローカルの `serve` は `orgs:` を一切参照しません。

## CLI からの選択

CLI（コマンドラインインターフェース）のすべてのコマンドは、`--target <name>` で 1 つのターゲットを選択し、`--config`（既定 `bajutsu.config.yaml`）で config を指定します。`--backend ios`（またはプラットフォーム/actuator のカンマ区切り）で解決順序を上書きできます（[cli](cli.md)）。

### Git リポジトリからの config（BE-0063）

`--config` は **Git ソース**も受け付けます。ローカルにチェックアウトせずに、テストリポジトリのスイートを実行できます（例：`bajutsu run --config github:acme/mobile-tests@v1.4.0:e2e/bajutsu.config.yaml --target checkout`）。

```
github:<owner>/<repo>[@<ref>][:<path>]                          # GitHub ショートハンド
git+https://<host>/<owner>/<repo>.git[@<ref>][#<path>]          # 一般形（ホストは将来用に予約）
```

- **現状で実装済みのホストは GitHub だけです。** 一般形 `git+https://<host>/…` は解析します（GitHub Enterprise / GitLab などへの拡張の余地を残すため）が、`github.com` 以外のホストは今のところ、黙って github.com を叩くのではなく明確なエラーで失敗します。
- Git ソースから実行した run は、解決したコミットを `manifest.json` の provenance（`configSource: { host, owner, repo, ref, sha }`）に**記録**します。ブランチ指定の run でも、実際に実行した正確なコミットが分かり、後から再現できます（[reporting](reporting.md#manifestjson)）。
- `<ref>` はブランチ、タグ、コミット SHA（既定はリポジトリのデフォルトブランチ）、`<path>` はリポジトリ内の config パス（既定はルートの `bajutsu.config.yaml`）です。スキームを認識できない値は、従来どおり**ローカルパス**として扱います。
- ref を不変のコミット SHA に解決し、その部分木を content-addressed なキャッシュ（`~/.cache/bajutsu/gitsrc/<host>/<owner>/<repo>/<sha>/`）に展開して、そこから config を読みます。config の `scenarios` / `baselines` / `schemas` / `appPath` は相対パスなので、呼び出し元の作業ディレクトリではなく**チェックアウトのルート**を基準に解決します。YAML だけでなくツリー全体が付いてきます。
- 取得したばかりのチェックアウトにはビルド済みバイナリが無く、手元で先にビルドする「最初の一回」もありません。そこで Git ソースの `run` は**アプリをオンデマンドでビルド**します。`appPath` が設定されていてバイナリが無いとき、config の `build` コマンドを**チェックアウトのルート**から実行し（`make -C demos/features sample-build` のような `build` の相対パスはそこを基準にします）、その後に実行を続けます。ビルドが失敗すれば明快に終了します。ローカルパスの `run` は従来どおりで、ビルドはせず、バイナリが無ければエラーになります。
- **固定コミット SHA**（`@<sha>`）は再現可能で、初回取得後はオフラインで動きます。ブランチ（やタグ）は毎回解決し直します。private リポジトリは `GITHUB_TOKEN` / `GH_TOKEN`、無ければ `gh auth token` のトークンを使い、トークンはログに出しません。
- `bajutsu run` にはゲート向けスイッチが 2 つあります。**`--config-offline`** はキャッシュを使いネットワークに触れません（オフラインでは解決できないので固定 `@<sha>` が必要）。**`--require-pinned-config`** は Git config がコミット SHA を固定していなければ失敗します。ゲートではブランチもタグも動きうるので、SHA だけを認めます。
- serve の UI も Git ソースを bind します。起動時の `serve --config github:…`、または「Open config」ダイアログの
  「From a Git repository」欄でチェックアウトを実体化し、そのルートから serve します（[cli → serve](cli.md#serve)）。
- 残りの後続: `record` / `crawl` の読み取り専用入力（生成物は SHA キーのキャッシュではなくローカルの `--out` に書く）です。

## 新しいターゲットのオンボーディング

新しいターゲットを追加するには、**アプリ側の準備と config への 1 エントリ追加**を行います。ツール本体への変更は不要です。

1. **実装規約を適用する**。主要要素に `accessibilityIdentifier` を（アプリの名前空間で）付け、状態を
   label / traits / value に露出させ、launch hook を用意し、アニメーションを無効化します。
2. **`targets.<name>` を追加する**。`bundleId`（必須）/ `deeplinkScheme` / 既定 `launchEnv` /
   `idNamespaces` などです。
3. **（任意）再利用前段を用意する**。ログインなどを `setup:` シナリオに切り出し、その steps を各シナリオの本編前に実行します（app 単位かシナリオ単位で指定）。
4. **`bajutsu doctor --target <name>` で検証する**。規約充足度スコアを見ます（下記）。
5. **シナリオを配置する**。識別子はそのアプリの名前空間で書きます。

## 識別子の命名規約

`accessibilityIdentifier` は **`<namespace>.<element>` のドット区切り**です。すべて小文字で、各セグメントは `[a-z0-9-]` です。先頭セグメントが名前空間で、`idNamespaces` に宣言した集合のいずれかである必要があります。

```
settings.reindex            # <namespace=settings>.<element=reindex>
home.search
list.row.<id>               # 動的行: 末尾は「データ由来の安定キー」（index 由来は禁止）
```

3 つの不変条件:

1. **画面内で一意**。同一画面に同じ id を 2 つ置きません（[selectors の曖昧検出](selectors.md#解決セマンティクス)）。
   繰り返し要素はデータ由来キーで一意化し（`list.row.3`）、集合操作には `idMatches` + `count` を使います。
2. **非ローカライズかつデータ由来**。id に表示文言を使いません（翻訳で壊れるため）。
3. **名前空間で前置**。すべての id を宣言済みの名前空間で始めます。

サンプルアプリの id カタログは [sample-app](sample-app.md#accessibilityidentifier-カタログ) にあります。

## doctor（規約充足度スコア）

実装: `bajutsu/doctor.py`。**AI 非依存で決定的**です。1 画面の `query()`（CLI は actuator で取得した現在画面）を解析してスコアを出します。

> `doctor` はまず**実行可能ゲート**（`preflight.py`）を確認し、その後でスコアを出します。ゲートが確認する内容は、選んだバックエンドが必要とするものです。iOS（idb）バックエンドなら CLI の `xcrun` と `idb` / `idb_companion`、および起動済みシミュレータ。web（Playwright）バックエンドなら Playwright パッケージとその Chromium ブラウザ（`uv sync --extra web` と `playwright install chromium`）です。続いて現在画面を採点します。web ターゲットでは新しいブラウザをターゲットの `baseUrl` に遷移させてそのページを採点し、iOS では起動済みシミュレータの画面を採点します。スコアの対象は、依然として現在表示されている画面だけです（入口や現在画面のみで、全画面は網羅しません）。

### 指標（`Score`）

操作可能要素（trait ∈ `ACTIONABLE_TRAITS` = button / link / textField / searchField / textView / switch / slider / tab / cell）を母数として測定します。

| 指標 | 定義 | しきい値 |
|---|---|---|
| `idCoverage` | id を持つ操作可能要素の割合 | ✓ ≥ 0.9 / warn 0.7–0.9 / fail < 0.7 |
| `namespaceConformance` | id の先頭が `idNamespaces` に一致する割合 | 規約外を `off_namespace` に列挙 |
| `duplicateIds` | 1 画面内の id 重複数 | 1 件でも Blocked |

### グレード判定

- **Blocked**: 画面に actionable な要素が 1 つも無い（多くは空画面、まだ読み込まれていない、または想定外の画面で、`render` がその旨を示します）、id 重複あり、**または** `idCoverage` < 0.7。
- **Ready**: `idCoverage` ≥ 0.9 **かつ** `namespaceConformance` == 1.0。
- **Partial**: それ以外（実行はできるが、座標フォールバックやフレーキーの予告）。

### 出力

`render(score)` が人間向けのサマリを返します。不足している要素は **具体的に列挙**するので、id をどこに追加すればよいかがそのまま分かります。

```
grade: Partial
idCoverage: 0.83 (5/6)
namespaceConformance: 1.00
duplicateIds: 0
  missing id: label='Close' traits=['button'] frame=(...)
```

CLI の `doctor` は Blocked のとき終了コード 1 で終了します（[cli](cli.md#doctor)）。

[English](../configuration.md) · **日本語**

# 設定、アプリのオンボーディング、doctor

ツール本体はアプリ非依存です。アプリ固有の差分はすべて config に置くので、同じバイナリと同じドライバで複数のアプリを実行できます。アプリを追加するときは `apps.<name>` を 1 つ加えるだけです。

実装: `bajutsu/config.py`（解決） · `bajutsu/doctor.py`（規約充足度スコア） · ルートの [`bajutsu.config.yaml`](../../bajutsu.config.yaml)。

関連: [concepts のアプリ非依存](concepts.md#6-アプリ非依存差分は-config-に寄せる) · [drivers](drivers.md) · [scenarios](scenarios.md)

---

## 設定の階層（defaults × apps）

`bajutsu.config.yaml` は 2 層で構成されています。値の解決順は **既定 < アプリ < シナリオ** で、テストに近い方が優先されます。

```yaml
defaults:                       # 全アプリ共通の既定
  backend: [ios]                # 順序付きリスト。プラットフォーム(ios/android/web/fake)か actuator(idb)。単一文字列も可
  device:  "iPhone 15"
  locale:  en_US
  capture: [screenshot.after, elements, actionLog]
  redact:  { headers: [Authorization, Cookie], fields: [token, password] }
  secrets: [LOGIN_PASSWORD]         # ${secrets.X} に使える環境変数名（実値は証跡でマスク）
  reservedNamespaces: [auth, nav]   # 共有フロー / コンポーネントの id 契約（情報用）

apps:
  sample:                       # ← --app sample で選択
    bundleId:       com.bajutsu.sample     # 必須
    deeplinkScheme: bajutsusample
    idNamespaces:   [home, list, counter, settings, onboarding, auth, nav, comp, ctrl, text, lists]
    launchEnv:      { SAMPLE_UITEST: "1" }
    scenarios:      demos/features/app/scenarios   # このアプリのシナリオディレクトリ（run が読み、record が書く）
    # 任意: backend / device / locale / launchArgs / setup / redact / secrets / mockServer / appPath / build
```

### 解決（`resolve` → `Effective`）

`resolve(config, app)` が 1 アプリ分の有効値 `Effective`（frozen dataclass）を構築します。アプリが未定義の場合は `KeyError` となり、CLI は終了コード 2 で終了します。

| `Effective` フィールド | 由来 | 備考 |
|---|---|---|
| `bundle_id` | app | 必須 |
| `deeplink_scheme` | app | preconditions の deeplink で使う scheme |
| `backend` | app ?? defaults | プラットフォーム(`ios`/`android`/`web`/`fake`)か actuator(`idb`)の安定度順リスト（単一文字列はリスト化）（[drivers](drivers.md#バックエンド選択と-actuator)） |
| `device` / `locale` | app ?? defaults | ⚠️ `locale` は現状 launch で未適用 |
| `launch_env` / `launch_args` | app | preconditions が run 時にマージ追記 |
| `id_namespaces` | app | doctor が参照 |
| `reserved_namespaces` | defaults | 情報用（doctor は app の `idNamespaces` のみで採点） |
| `mock_server` | app | ⚠️ スキーマのみ · 未配線 |
| `setup` | app | 既定の再利用前段（その steps を各シナリオの本編前に実行） |
| `scenarios` | app | このアプリのシナリオディレクトリ。`run --app` は配下の `*.yaml` を全件読み、`record` は新規をここへ書く。実行 cwd 基準の相対パス。`run --scenario` / `record --out` で上書き |
| `capture` | defaults | 既定証跡（[evidence の注記](evidence.md#証跡の指示方法3-つ)） |
| `redact` | defaults ∪ app | マージ（下記） |
| `secrets` | defaults ∪ app | `${secrets.X}` を宣言する環境変数名。実値は証跡でマスク（[evidence](evidence.md#マスキングredact)） |

`backend` フィールドの検証で `_norm` が「単一文字列 → 1 要素リスト」に正規化します（defaults / app の両方に適用）。

### redact のマージ

config の `defaults.redact` と `apps.<name>.redact` は **union** されます（`_merge_redact` が `labels`/`headers`/`fields` を個別に和集合します）。さらにシナリオの `redact`（[evidence](evidence.md#マスキングredact)）が重なります。

### シークレット（`secrets:`）

`secrets:` は **環境変数名のリスト**で（`defaults` と `apps.<name>` の両方で宣言でき、`resolve` が和集合にします）、シナリオが入力に使える `${secrets.X}` 変数の宣言元です。`bajutsu run` は実行時に、宣言された各名前を環境から解決し、その値を action に展開（`${secrets.X}`）したうえで、**証跡に現れる箇所すべてでその実値をマスク**します（[evidence](evidence.md#マスキングredact)）。シナリオ source には `${secrets.X}` トークンだけが残り、実値は残りません。

### org（`orgs:`、マルチテナントのサーバ backend）

`orgs:` は、ホスト型サーバ backend のテナントを宣言します（[BE-0015](../../roadmaps/proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)）。各 org は、所属メンバー（明示の GitHub login＝`members`、および／または GitHub org 全体＝`githubOrgs`）と、その org が持つ apps を列挙します。

```yaml
orgs:
  acme:
    members: [alice, bob]    # 明示の GitHub login
    githubOrgs: [acme-gh]    # この GitHub org の全員（read:org の OAuth scope が必要）
    apps: [demo, checkout]
```

OAuth ログイン時にユーザは自分の org に割り当てられます（まず明示の `members`、無ければ GitHub org メンバーシップからの `githubOrgs` 一致）。以後はその org の apps だけが見え、run の artifacts／scenarios／baselines はその org 専用のオブジェクトストレージ prefix の下に置かれます。どの org にも挙げられていない login や app は単一の `default` org に入るので、`orgs:` ブロックの**無い** config はシングルテナントです。CLI とローカルの `serve` は `orgs:` を一切参照しません。

## CLI からの選択

CLI（コマンドラインインターフェース）のすべてのコマンドは、`--app <name>` で 1 つのアプリを選択し、`--config`（既定 `bajutsu.config.yaml`）で config を指定します。`--backend ios`（またはプラットフォーム/actuator のカンマ区切り）で解決順序を上書きできます（[cli](cli.md)）。

## 新しいアプリのオンボーディング

新しいアプリを追加するには、**アプリ側の準備と config への 1 エントリ追加**を行います。ツール本体への変更は不要です。

1. **実装規約を適用する**。主要要素に `accessibilityIdentifier` を（アプリの名前空間で）付け、状態を
   label / traits / value に露出させ、launch hook を用意し、アニメーションを無効化します。
2. **`apps.<name>` を追加する**。`bundleId`（必須）/ `deeplinkScheme` / 既定 `launchEnv` /
   `idNamespaces` などです。
3. **（任意）再利用前段を用意する**。ログインなどを `setup:` シナリオに切り出し、その steps を各シナリオの本編前に実行します（app 単位かシナリオ単位で指定）。
4. **`bajutsu doctor --app <name>` で検証する**。規約充足度スコアを見ます（下記）。
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

> `doctor` はまず**実行可能ゲート**（`preflight.py`: actuator が必要とする CLI（`xcrun`、idb なら `idb` / `idb_companion`）と起動済みシミュレータ）を確認し、その後でスコアを出します。スコアの対象は、依然として現在表示されている画面だけです（入口や現在画面のみで、全画面は網羅しません）。

### 指標（`Score`）

操作可能要素（trait ∈ `ACTIONABLE_TRAITS` = button / link / textField / searchField / textView / switch / slider / tab / cell）を母数として測定します。

| 指標 | 定義 | しきい値 |
|---|---|---|
| `idCoverage` | id を持つ操作可能要素の割合 | ✓ ≥ 0.9 / warn 0.7–0.9 / fail < 0.7 |
| `namespaceConformance` | id の先頭が `idNamespaces` に一致する割合 | 規約外を `off_namespace` に列挙 |
| `duplicateIds` | 1 画面内の id 重複数 | 1 件でも Blocked |

### グレード判定

- **Blocked**: id 重複あり、**または** `idCoverage` < 0.7。
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

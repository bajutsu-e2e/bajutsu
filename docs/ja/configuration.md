[English](../configuration.md) · **日本語**

# 設定・アプリのオンボーディング・doctor

ツール本体はアプリ非依存です。アプリ固有の差分はすべて config に置き、同じバイナリ・同じドライバで複数のアプリを実行できます。アプリを追加する場合は `apps.<name>` を 1 つ追加するだけです。

実装: `bajutsu/config.py`（解決） ・ `bajutsu/doctor.py`（規約充足度スコア） ・ ルートの [`bajutsu.config.yaml`](../../bajutsu.config.yaml)。

関連: [concepts のアプリ非依存](concepts.md#6-アプリ非依存差分は-config-に寄せる) ・ [drivers](drivers.md) ・ [scenarios](scenarios.md)

---

## 設定の階層（defaults × apps）

`bajutsu.config.yaml` は 2 層で構成されています。値の解決順は **既定 < アプリ < シナリオ**（テストに近い方が優先）です。

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
| `mock_server` | app | ⚠️ スキーマのみ・未配線 |
| `setup` | app | 既定の再利用前段（その steps を各シナリオの本編前に実行） |
| `scenarios` | app | このアプリのシナリオディレクトリ。`run --app` は配下の `*.yaml` を全件読み、`record` は新規をここへ書く。実行 cwd 基準の相対パス。`run --scenario` / `record --out` で上書き |
| `capture` | defaults | 既定証跡（[evidence の注記](evidence.md#証跡の指示方法3-つ)） |
| `redact` | defaults ∪ app | マージ（下記） |
| `secrets` | defaults ∪ app | `${secrets.X}` を宣言する環境変数名。実値は証跡でマスク（[evidence](evidence.md#マスキングredact)） |

`backend` フィールドの検証で `_norm` が「単一文字列 → 1 要素リスト」に正規化します（defaults / app の両方に適用）。

### redact のマージ

config の `defaults.redact` と `apps.<name>.redact` は **union** されます（`_merge_redact`、`labels`/`headers`/`fields` を個別に和集合）。さらにシナリオの `redact`（[evidence](evidence.md#マスキングredact)）が重なります。

### シークレット（`secrets:`）

`secrets:` は **環境変数名のリスト**です（`defaults` と `apps.<name>` の両方で宣言でき、`resolve` が和集合にします）。シナリオが入力に使える `${secrets.X}` 変数の宣言元です。`bajutsu run` は実行時に宣言された各名前を環境から解決し、その値を action に展開（`${secrets.X}`）したうえで、**証跡に現れる箇所すべてでその実値をマスク**します（[evidence](evidence.md#マスキングredact)）。シナリオ source には `${secrets.X}` トークンが残り、実値は残りません。

## CLI からの選択

すべてのコマンドは `--app <name>` で 1 つのアプリを選択し、`--config`（既定 `bajutsu.config.yaml`）で config を指定します。`--backend ios`（またはプラットフォーム/actuator のカンマ区切り）で解決順序を上書きできます（[cli](cli.md)）。

## 新しいアプリのオンボーディング

新しいアプリを追加するには、**アプリ側の準備と config への 1 エントリ追加**を行います。ツール本体への変更は不要です。

1. **実装規約を適用** — 主要要素に `accessibilityIdentifier`（アプリの名前空間で）、状態を
   label / traits / value に露出、launch hook、アニメ無効化。
2. **`apps.<name>` を追加** — `bundleId`（必須）/ `deeplinkScheme` / 既定 `launchEnv` / `idNamespaces` 等。
3. **（任意）再利用前段** — ログイン等を `setup:` シナリオに切り出し、その steps を各シナリオの本編前に実行（app 単位 / シナリオ単位で指定）。
4. **`bajutsu doctor --app <name>` で検証** — 規約充足度スコアを見る（下記）。
5. **シナリオを配置** — 識別子はそのアプリの名前空間で書く。

## 識別子の命名規約

`accessibilityIdentifier` は **`<namespace>.<element>` のドット区切り**です。すべて小文字で、各セグメントは `[a-z0-9-]` の形式です。先頭セグメントが名前空間で、`idNamespaces` に宣言した集合のいずれかである必要があります。

```
settings.reindex            # <namespace=settings>.<element=reindex>
home.search
list.row.<id>               # 動的行: 末尾は「データ由来の安定キー」（index 由来は禁止）
```

3 つの不変条件:

1. **画面内で一意** — 同一画面に同じ id を 2 つ置かない（[selectors の曖昧検出](selectors.md#解決セマンティクス)）。
   繰り返し要素はデータ由来キーで一意化（`list.row.3`）。集合操作は `idMatches` + `count`。
2. **非ローカライズ・データ由来** — id に表示文言を使わない（翻訳で壊れる）。
3. **名前空間で前置** — 全 id を宣言済み名前空間で始める。

サンプルアプリの id カタログは [sample-app](sample-app.md#accessibilityidentifier-カタログ)。

## doctor（規約充足度スコア）

実装: `bajutsu/doctor.py`。**AI 非依存・決定的**。1 画面の `query()`（CLI は actuator で取得した現在画面）を解析してスコアを出します。

> `doctor` はまず**実行可能ゲート**（`preflight.py`: actuator が必要とする CLI — `xcrun`、idb なら `idb` / `idb_companion` — と起動済みシミュレータ）を確認し、その後スコアを出します。スコアは現在表示されている画面のみが対象です（入口/現在画面のみで、全画面は網羅しません）。

### 指標（`Score`）

操作可能要素（trait ∈ `ACTIONABLE_TRAITS` = button / link / textField / searchField / textView / switch / slider / tab / cell）を母数として測定します。

| 指標 | 定義 | しきい値 |
|---|---|---|
| `idCoverage` | id を持つ操作可能要素の割合 | ✓ ≥ 0.9 / warn 0.7–0.9 / fail < 0.7 |
| `namespaceConformance` | id の先頭が `idNamespaces` に一致する割合 | 規約外を `off_namespace` に列挙 |
| `duplicateIds` | 1 画面内の id 重複数 | 1 件でも Blocked |

### グレード判定

- **Blocked**: id 重複あり **または** `idCoverage` < 0.7。
- **Ready**: `idCoverage` ≥ 0.9 **かつ** `namespaceConformance` == 1.0。
- **Partial**: それ以外（実行はできるが座標フォールバック・フレーキーの予告）。

### 出力

`render(score)` が人間向けサマリを返します。不足要素は **実体を列挙**して「どこに id を追加するか」を直接示します。

```
grade: Partial
idCoverage: 0.83 (5/6)
namespaceConformance: 1.00
duplicateIds: 0
  missing id: label='Close' traits=['button'] frame=(...)
```

CLI の `doctor` は Blocked のとき終了コード 1 で終了します（[cli](cli.md#doctor)）。

[English](../drivers.md) · **日本語**

# ドライバ抽象、バックエンド、環境管理

> ひとつの `Driver` インターフェースの裏にバックエンド（`idb`（iOS Simulator）、`playwright`（web
> ブラウザ）、それにテスト用のインメモリ `fake`）を置き、能力差を抽象側で吸収します。プラットフォーム
> 対応のレジストリが `backend` リストから actuator を選びます。iOS ではアプリの起動（boot/launch）を
> `simctl` ラッパが担います。
>
> 実装: `bajutsu/drivers/`（`base.py` / `idb.py` / `playwright.py` / `fake.py`）・
> `bajutsu/backends.py` ・ `bajutsu/env.py`。

関連: [selectors](selectors.md)（解決） ・ [concepts の安定度順ラダー](concepts.md#5-安定度順ラダーstability-ladder) ・ [run-loop](run-loop.md)

---

## Driver Protocol

すべてのバックエンドが満たす共通インターフェースです（`base.py`、`runtime_checkable` な `Protocol`）。
**操作（tap/type/swipe/wait/query）は actuator のみが行います**。

```python
class Driver(Protocol):
    def query(self) -> list[Element]: ...           # 画面の要素ツリー
    def tap(self, sel: Selector) -> None: ...
    def tap_point(self, p: Point) -> None: ...       # 生座標 tap（システムアラート等）
    def long_press(self, sel: Selector, duration: float) -> None: ...
    def swipe(self, frm: Point, to: Point) -> None: ...
    def type_text(self, text: str) -> None: ...
    def wait_for(self, sel: Selector, timeout: float) -> bool: ...
    def screenshot(self, path: str) -> None: ...
    def capabilities(self) -> set[str]: ...          # 提供能力（actuator / フォールバック解決用）
```

> **`wait_for` について**: Protocol には存在しますが、run ループの条件待機は orchestrator 自身が `query()` をポーリングして行います（`_wait`、[run-loop](run-loop.md#待機条件待機)）。そのため、現状の実行系はドライバの `wait_for` を直接は使いません。インターフェースの一部として残っています。

### 能力（`Capability`）

`capabilities()` が返すトークン集合で、actuator 選択・証跡のフォールバック解決・**プリフライト能力検査**（後述）に使います。

| 能力 | 意味 | idb | playwright | fake |
|---|---|:--:|:--:|:--:|
| `query` | 要素ツリー取得 | ✅ | ✅ | ✅ |
| `elements` | 要素ダンプ証跡 | ✅ | ✅ | ✅ |
| `screenshot` | スクショ | ✅ | ✅ | ✅ |
| `semanticTap` | id/label で直接タップ（座標不要） | — | ✅ | ✅ |
| `conditionWait` | ネイティブ条件待機 | — | ✅ | ✅ |
| `network` | ネイティブネットワーク監視 | — | ✅ | — |
| `multiTouch` | 2 本指ジェスチャ（pinch / rotate） | — | — | ✅ |

> idb は **frame 中心の座標**で操作します。semantic tap を持たないため、run ループは `query()` で要素を一意に確定しその中心をタップします。`pinch` / `rotate` は `UnsupportedAction`（単一タッチ）を返し、これらは codegen → XCUITest 経由で扱います。`fake` ドライバはテストでそれらのコードパスを動かすためだけに、より広い能力集合（semanticTap / conditionWait / multiTouch）を公開します。`playwright`（web）ドライバは `semanticTap` / `conditionWait`（Playwright がネイティブに持つ）に加えて `network` も公開します。アプリ側の協力なしに通信を観測し、その場でスタブもできる**初めてのネイティブネットワーク対応バックエンド**です（BE-0054）。`multiTouch` は引き続き先送りです（[BE-0054](../../roadmaps/in-progress/BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md) で追跡）。

### プリフライト能力検査（BE-0082）

バックエンドの能力集合は静的なので、選んだ actuator が持たない能力をシナリオが必要とするかどうかは、デバイス作業の前に分かります。run の開始時——actuator を選んだ後、最初のデバイスを lease する前——に、runner は各シナリオを actuator の能力と照合し（`bajutsu/capability_preflight.py`）、未対応のシナリオを即座に失敗させます。集約した 1 つの理由（`UnsupportedAction` 相当）を付けて、デバイスを起動して途中で失敗するのを避けます（prime directive #2：速く明確に失敗する）。検査は (シナリオ, 能力集合) の純粋関数で、デバイスも時計も使いません。シナリオ単位なので、未対応のシナリオだけが失敗し、残りは実行されます。

検査は、能力集合で明確に判定できる**真の hard requirement** だけを門にします。`pinch` / `rotate` は `multiTouch`、`visual` アサーションは `screenshot`、すべての run は `query` と `elements` を必要とします。一方、`conditionWait` は門にしません（run ループはすべての待機を polling で実装するので、どのバックエンドもこのトークンを必要としません）。`network` も門にしません（idb は `network` を公開しませんが、アプリ側の collector で通信を捕捉するため、`request` / `event` / `requestSequence` / `responseSchema` アサーションや `until: { request }` 待機は idb でも動きます）。`gestures.py` の `_require_multi_touch` は、ジェスチャ実行時の多層防御の検査として残します。

## idb

ヘッドレスで座標ベースのバックエンドです。CI（継続的インテグレーション）向きで、semantic tap を持たないため、抽象側で **id → frame 中心 → 座標 tap** に解決します。実装: `drivers/idb.py`。

- `query()`: `idb ui describe-all --udid <udid> --json` を `parse_describe_all` で正規化します（JSON 配列 / 改行区切り JSON の両対応、`AXLabel`/`AXValue`/`AXUniqueId` 等を吸収）。
- `tap(sel)`: `_resolve` で一意確定します（**not-found はリトライ、ambiguity は即失敗**: 実機ツリーは遷移中に一時的に空になり得るため）。確定後、frame 中心へ `idb ui tap`（整数座標）を送ります。
- `screenshot`: idb 自身のフレームキャプチャが不安定なため **`simctl io screenshot` を使います**。
- `swipe`: `--duration 0.2` を付けて実ドラッグ化します（瞬間スワイプは SwiftUI に pan として認識されません）。

> describe-all の JSON キー名は fb-idb の出力に従い、`make -C demos/features e2e` ＋ `e2e.yml` CI ワークフローで**実機検証済みです**（iPhone 17 Pro、最近の iOS）。インストール済み idb がスキーマを変えたときだけ再確認すれば十分です（`idb.py` 冒頭の注記参照）。idb クライアントは `uv sync --extra idb`、`idb_companion` は `brew install facebook/fb/idb-companion` でインストールします。

### idb のバージョン追跡（BE-0005）

idb は唯一の on-device backend なので、新しい Simulator ランタイムを古い `idb_companion` が駆動できない、あるいは companion のアップグレードが describe-all の JSON を変える、といった事態が Bajutsu 側の変更なしに run を壊す。そこで idb を走らせるバージョンを、たまたまインストールされているものではなく、記録・比較できる入力として扱う。

- **設定で範囲を固定する。** `defaults.idbVersion` に `">=1.1.8"` や `">=1.1.0,<2.0.0"` のような制約を置く（環境レベル＝どの target を駆動しても同じ pin）。`bajutsu doctor` がインストール済みの `idb_companion` をこれと突き合わせて報告する（例: `✓ idb_companion version: 1.1.8 (expected >=1.1.8)`）。不一致が、分かりにくい後続の失敗としてではなく、起動前チェックの一覧に現れる。不正な pin は設定の読み込み時に弾く。pin を宣言しなければ、`doctor` はバージョン行を出さない。
- **manifest に記録する。** idb で実行した run はすべて `idb_companion` と idb クライアントのバージョンを `manifest.json` に書き込む（`"idb": { "companion": …, "client": … }`）。これでどの成果物がどの idb で生成されたかが正確に分かる。これは来歴（provenance）であって、pass/fail には一切関与しない。run/CI の判定は決定論的なまま保たれる。
- **定期的な互換性監視。** `idb-monitor.yml` が、最新の `idb_companion` に対して smoke シナリオを idb 経由で週次（per-PR ゲートとは分離）実行する。smoke の実行は `parse_describe_all` → Element 正規化を通るので、スキーマや挙動の drift があればそこで明確に失敗する。場当たり的に発見されるのではなく、こちらが制御する周期で捕まえられる。

## Playwright（web）

Playwright（Python）によるヘッドレス Chromium です。Mac も Simulator も要らず Linux で動くため、`make check` と同じツールチェーンに収まります。実装: `drivers/playwright.py`（ロードマップ [BE-0041](../../roadmaps/in-progress/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）。

- `query()`: 1 本の `page.evaluate()` が、可視・操作可能・アクセシビリティ関連の DOM ノードを走査し、純粋なパーサ（`parse_dom`）が各ノードを `Element` に写像します。id 規約は iOS の accessibilityIdentifier の web 版です。`data-testid` → `Selector.id`、ARIA `role`（またはタグ）→ `traits`、accessible name / `aria-label` / テキスト → `label`、input の `value` → `value`。
- `tap(sel)`: idb と同様、`query()` のスナップショットに対し共有の `resolve_unique`/`find_all` で要素を**一意に**確定し、**frame 中心**を座標クリック（`page.mouse.click`）します。Playwright 自身の `get_by_test_id().click()` は**あえて使いません**。これによりセレクタの意味が他のどの backend ともバイト単位で一致します。
- `type_text` は `page.keyboard` で入力します（オーケストレータが先に `into` をタップしてフィールドにフォーカスします）。`screenshot` は `page.screenshot`、`wait_for` は `find_all` による単発（idb と同じ）です。
- ライフサイクルは driver が所有します。新しい `BrowserContext` が `erase` 相当、`navigate()`（`page.goto(baseUrl)`）が `launch`、`close()` でブラウザを破棄します。simctl のデバイスは無いので、run はダミーのリースを使い、device control は持ちません（v1 では `pinch`/`rotate` が `UnsupportedAction`）。
- **ネイティブネットワーク**（BE-0054）: Playwright はページが出すすべてのリクエストを見られるので、`--network` はアプリ側の協力なしに web でも動きます。`network_collector()` がページの `requestfinished` イベントを iOS と同じ `NetworkExchange` に変換するため、`request` アサーションも `network.json` 証跡もそのまま使えます。シナリオの `mocks` は `page.route` でその場で fulfill します。一致したリクエストには既定のレスポンスを返し、`mocked: true` を立てて記録します。一致判定は決定論的な `request` マッチャを再利用し、モデルは一切使いません。
- **コンソール / ページエラー証跡**（BE-0054）: `deviceLog` キャプチャ種別は、ブラウザのコンソールと未捕捉のページエラーを `<scenario>/device.log` にストリームします。iOS の os_log `deviceLog` に相当しますが、simctl ではなく Playwright ネイティブです。デバイスプールがドライバの `web_interval` を `FileSink` に注入するので、バックエンド非依存の同じ `capture` ポリシーがそのまま運びます。

> `playwright` は**遅延 import** されます（実際にブラウザを起動するときだけ読み込む）。そのため既定の CLI パスには決して載りません（`tests/serve/test_import_guard.py` で固定）。インストールは `uv sync --extra web` ＋ `uv run playwright install chromium`。`demos/web` のデモ（`make -C demos/web e2e`）が小さな静的 web アプリを端から端まで駆動します。

## FakeDriver

実機なしで orchestrator / runner / record をテストするためのインメモリ実装です。実装: `drivers/fake.py`。

- `screen`（`Element` のリスト）を保持し、`query()` で返します。
- `tap` / `long_press` は本物同様 `resolve_unique` を通します（曖昧 / 不在は `SelectorError`）。
- `react` コールバックで「操作に応じて画面が変わる」動作をスクリプトできます。
- `actions` に実行した操作を記録します（検証用）。

```python
def react(driver, kind, arg):
    if kind == "tap":
        driver.screen = [...]  # タップ後の画面に差し替える
FakeDriver(screen=[...], react=react)
```

## バックエンド選択と actuator

実装: `bajutsu/backends.py`。

```python
PLATFORMS = {                              # プラットフォームトークンは actuator 列へ展開
    "ios":     ("idb",),                   #   将来: ("xcuitest", "idb")
    "android": ("adb",),                   #   計画中
    "web":     ("playwright",),            #   実装済み（BE-0041）
    "fake":    ("fake",),                  #   メモリ上のテスト/デモ用ドライバ
}
IMPLEMENTED = {"idb", "fake", "playwright"}  # 今日ドライバがある actuator

def default_available(actuator) -> bool:   # 実装済みかつ裏のツールがあるか（playwright はパッケージ import、fake は常に可）
def resolve_actuators(backends) -> list:   # 各トークン（プラットフォーム/actuator）を actuator 列へ展開
def select_actuator(backends, available) -> str:  # 順に見て最初の「実装済み かつ 利用可能」
def make_driver(actuator, udid, *, base_url=None) -> Driver:  # "idb"→IdbDriver, "playwright"→PlaywrightDriver, "fake"→FakeDriver
```

- **バックエンドトークン**は、**プラットフォーム**（`ios` / `android` / `web` / `fake`）か、具体的な **actuator**（例: `idb`）のどちらかです。`--backend ios`（または `backend: [ios]`）は今日 `idb` に解決され、より高機能な iOS actuator（XCUITest）が入ればそれを拾います。シナリオも config も変わりません。
- `backend` は **安定度順のリスト**です（先頭ほど安定。[concepts](concepts.md#5-安定度順ラダーstability-ladder)）。各トークンは順に actuator 列へ展開され、**actuator = 最初の「実装済み かつ 利用可能」**なものです。利用可能なものが無ければ `RuntimeError`（CLI は終了コード 2）。
- `web` は `playwright` に解決され、**実装済み**です（[multi-platform](multi-platform.md)）。`android`（→ `adb`）は**宣言済みだが未実装**で、要求すると汎用の失敗ではなく明確な「未実装」エラーになります。本当に未知のトークンはスキップされます（前方互換: 古いビルドでも、将来のバックエンドを列挙した config を実行できます）。
- 可用性判定 `available` は注入可能です（テストで差し替え可）。既定は `shutil.which`（`fake` は実行ファイル不要で常に利用可能）。
- actuator は run 開始時に 1 つ確定し、run 中は固定です（2 ドライバが同一デバイスを操作しません）。

> 設計（DESIGN §9）では actuator 以外を read-only な証跡フォールバックに使う構想がありますが、現状の実行系は **actuator 単一**で、証跡フォールバックの複数バックエンド利用は未配線です。

## 環境管理（simctl）

実装: `bajutsu/env.py`。コマンドビルダは純関数（単体テスト済み）で、実行は注入可能な `RunFn` 経由です。

| メソッド | コマンド | 備考 |
|---|---|---|
| `erase()` | `simctl erase <udid>` | クリーン環境 |
| `boot()` | `simctl boot <udid>` | 既に boot 済みなら冪等（エラーを握りつぶす） |
| `launch(bundle, args, env)` | `simctl launch --terminate-running-process <udid> <bundle> <args>` | env は `SIMCTL_CHILD_*` で注入 |
| `terminate(bundle)` | `simctl terminate <udid> <bundle>` | 未起動でも無視 |
| `openurl(url)` | `simctl openurl <udid> <url>` | deeplink |
| `screenshot(path)` | `simctl io <udid> screenshot <path>` | — |

> **launch env の注入**: アプリへ渡す env 変数は、親プロセスに `SIMCTL_CHILD_<NAME>` として設定すると子（アプリ）に `<NAME>` で渡ります。`child_env()` がこの変換を行います。サンプルアプリの `SAMPLE_UITEST` 等の launch hook はこの仕組みを使います（[sample-app](sample-app.md#launch-env-フック)）。

`video` / `deviceLog` の区間録りも `simctl io recordVideo` / `simctl spawn log stream` を使いますが、これらは証跡サブシステム側（`intervals.py`）に置かれています（[evidence](evidence.md#区間証跡video--devicelog--apptrace)）。

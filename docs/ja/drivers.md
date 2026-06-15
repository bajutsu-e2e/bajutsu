[English](../drivers.md) · **日本語**

# ドライバ抽象・バックエンド・環境管理

> ひとつの `Driver` インターフェースの裏にバックエンド（idb、それにテスト用のインメモリ `fake`）を
> 置き、能力差を抽象側で吸収します。現状の実バックエンドは idb のみですが、インターフェースは
> 追加のバックエンドに対応できるよう設計されています。アプリの起動（boot/launch）は `simctl` ラッパが担います。
>
> 実装: `bajutsu/drivers/`（`base.py` / `idb.py` / `fake.py`）・
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

`capabilities()` が返すトークン集合で、actuator 選択と証跡のフォールバック解決に使います。

| 能力 | 意味 | idb | fake |
|---|---|:--:|:--:|
| `query` | 要素ツリー取得 | ✅ | ✅ |
| `elements` | 要素ダンプ証跡 | ✅ | ✅ |
| `screenshot` | スクショ | ✅ | ✅ |
| `semanticTap` | id/label で直接タップ（座標不要） | — | ✅ |
| `conditionWait` | ネイティブ条件待機 | — | ✅ |
| `network` | ネイティブネットワーク監視 | — | — |
| `multiTouch` | 2 本指ジェスチャ（pinch / rotate） | — | ✅ |

> idb は **frame 中心の座標**で操作します。semantic tap を持たないため、run ループは `query()` で要素を一意に確定しその中心をタップします。`pinch` / `rotate` は `UnsupportedAction`（単一タッチ）を返し、これらは codegen → XCUITest 経由で扱います。`fake` ドライバはテストでそれらのコードパスを動かすためだけに、より広い能力集合（semanticTap / conditionWait / multiTouch）を公開します。

## idb

ヘッドレス・座標ベースのバックエンドです。CI（継続的インテグレーション）向きで、semantic tap を持たないため、抽象側で **id → frame 中心 → 座標 tap** に解決します。実装: `drivers/idb.py`。

- `query()`: `idb ui describe-all --udid <udid> --json` を `parse_describe_all` で正規化します（JSON 配列 / 改行区切り JSON の両対応、`AXLabel`/`AXValue`/`AXUniqueId` 等を吸収）。
- `tap(sel)`: `_resolve` で一意確定します（**not-found はリトライ、ambiguity は即失敗**: 実機ツリーは遷移中に一時的に空になり得るため）。確定後、frame 中心へ `idb ui tap`（整数座標）を送ります。
- `screenshot`: idb 自身のフレームキャプチャが不安定なため **`simctl io screenshot` を使います**。
- `swipe`: `--duration 0.2` を付けて実ドラッグ化します（瞬間スワイプは SwiftUI に pan として認識されません）。

> describe-all の JSON キー名は fb-idb の出力に従い、`make -C demos/features e2e` ＋ `e2e.yml` CI ワークフローで**実機検証済みです**（iPhone 17 Pro、最近の iOS）。インストール済み idb がスキーマを変えたときだけ再確認すれば十分です（`idb.py` 冒頭の注記参照）。idb クライアントは `uv sync --extra idb`、`idb_companion` は `brew install facebook/fb/idb-companion` でインストールします。

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
KNOWN = ("idb",)                           # fake はテスト専用、ここには含めない

def default_available(backend) -> bool:    # 実行ファイルが PATH にあるか（粗い一次判定）
def select_actuator(backends, available) -> str:  # 安定度順で最初に利用可能なもの
def make_driver(backend, udid) -> Driver:  # "idb" → IdbDriver
```

- `backend` は **安定度順のリスト**です（先頭ほど安定。[concepts](concepts.md#5-安定度順ラダーstability-ladder)）。現状の登録バックエンドは idb のみなのでリストは 1 要素ですが、シナリオに触れずに別のバックエンドを追加できるよう選択の仕組みを維持しています。
- **actuator = リストで最初に利用可能なバックエンド**です。利用可能なものが無ければ `RuntimeError`（CLI は終了コード 2）。
- 可用性判定 `available` は注入可能です（テストで差し替え可）。既定は `shutil.which`。
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

`video` / `deviceLog` の区間録りも `simctl io recordVideo` / `simctl spawn log stream` を使いますが、これらは証跡サブシステム側（`intervals.py`）に置かれています（[evidence](evidence.md#区間証跡video--devicelog)）。

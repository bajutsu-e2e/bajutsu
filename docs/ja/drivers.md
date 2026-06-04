[English](../drivers.md) · **日本語**

# ドライバ抽象・バックエンド・環境管理

> ひとつの `Driver` インターフェースの裏に複数のバックエンド（RocketSim / idb / fake）を置き、
> 能力差を抽象側で吸収する。アプリの起動（boot/launch）は `simctl` ラッパが担う。
>
> 実装: `bajutsu/drivers/`（`base.py` / `rocketsim.py` / `idb.py` / `fake.py`）・
> `bajutsu/backends.py` ・ `bajutsu/env.py`。

関連: [selectors](selectors.md)（解決） ・ [concepts の安定度順ラダー](concepts.md#5-安定度順ラダーstability-ladder) ・ [run-loop](run-loop.md)

---

## Driver Protocol

すべてのバックエンドが満たす共通インターフェース（`base.py`、`runtime_checkable` な `Protocol`）。
**操作（tap/type/swipe/wait/query）は actuator のみが行う**。

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

> **`wait_for` について**: Protocol には存在するが、run ループの条件待機は orchestrator 自身が
> `query()` をポーリングして行う（`_wait`、[run-loop](run-loop.md#待機条件待機)）ため、現状の実行系は
> ドライバの `wait_for` を直接は使わない。インターフェースの一部として残っている。

### 能力（`Capability`）

`capabilities()` が返すトークン集合で、actuator 選択と証跡のフォールバック解決に使う。

| 能力 | 意味 | RocketSim | idb | fake |
|---|---|:--:|:--:|:--:|
| `query` | 要素ツリー取得 | ✅ | ✅ | ✅ |
| `elements` | 要素ダンプ証跡 | ✅ | ✅ | ✅ |
| `screenshot` | スクショ | ✅ | ✅ | ✅ |
| `semanticTap` | id/label で直接タップ（座標不要・最安定） | ✅ | — | ✅ |
| `conditionWait` | ネイティブ条件待機 | ✅ | — | ✅ |
| `network` | ネイティブネットワーク監視 | ✅ | — | — |

## RocketSim

semantic tap を持つ、安定度順ラダーの最上位。手元（GUI 常駐）向け。
実装: `drivers/rocketsim.py`。

- `query()`: `rocketsim elements --agent --udid <udid>` の出力（配列または `{ elements: [...] }`）を
  `parse_elements` で正規化。
- `tap(sel)`: まず `resolve_unique` で一意確定 → 要素に `identifier` があれば
  `rocketsim tap --id <identifier>`（**semantic tap = 最安定**）、無ければ frame 中心へ座標 tap。
- `long_press` / `swipe` / `type_text` / `screenshot` も対応コマンドへ。

> ⚠️ **CLI サーフェスは「想定」**: RocketSim の実際の CLI と `rs/1` JSON スキーマは未確認で、
> パーサ・コマンドビルダは実機で確認・調整する前提（`rocketsim.py` 冒頭 NOTE）。

## idb

ヘッドレス・座標ベース。CI 向き。semantic tap を持たないため、抽象側で **id → frame 中心 → 座標 tap**
に解決する。実装: `drivers/idb.py`。

- `query()`: `idb ui describe-all --udid <udid> --json` を `parse_describe_all` で正規化
  （JSON 配列 / 改行区切り JSON の両対応、`AXLabel`/`AXValue`/`AXUniqueId` 等を吸収）。
- `tap(sel)`: `_resolve` で一意確定（**not-found はリトライ、ambiguity は即失敗**: 実機ツリーは
  遷移中に一時的に空になり得るため）→ frame 中心へ `idb ui tap`（整数座標）。
- `screenshot`: idb 自身のフレームキャプチャが不安定なため **`simctl io screenshot` を使う**。
- `swipe`: `--duration 0.2` を付けて実ドラッグ化（瞬間スワイプは SwiftUI に pan として認識されない）。

> ⚠️ describe-all の JSON キー名は fb-idb の出力に合わせた **想定**で、インストール済み idb で
> 要確認（`idb.py` 冒頭の注記）。idb クライアントは `uv sync --extra idb`、`idb_companion` は
> `brew install facebook/fb/idb-companion`。

## FakeDriver

実機なしで orchestrator / runner / record をテストするためのインメモリ実装。
実装: `drivers/fake.py`。

- `screen`（`Element` のリスト）を保持し、`query()` で返す。
- `tap` / `long_press` は本物同様 `resolve_unique` を通す（曖昧 / 不在は `SelectorError`）。
- `react` コールバックで「操作に応じて画面が変わる」をスクリプトできる。
- `actions` に実行した操作を記録（検証用）。

```python
def react(driver, kind, arg):
    if kind == "tap":
        driver.screen = [...]  # タップ後の画面に差し替える
FakeDriver(screen=[...], react=react)
```

## バックエンド選択と actuator

実装: `bajutsu/backends.py`。

```python
KNOWN = ("rocketsim", "idb")               # fake はテスト専用、ここには含めない

def default_available(backend) -> bool:    # 実行ファイルが PATH にあるか（粗い一次判定）
def select_actuator(backends, available) -> str:  # 安定度順で最初に利用可能なもの
def make_driver(backend, udid) -> Driver:  # "rocketsim" → RocketSimDriver, "idb" → IdbDriver
```

- `backend` は **安定度順のリスト**（先頭ほど安定。[concepts](concepts.md#5-安定度順ラダーstability-ladder)）。
- **actuator = リストで最初に利用可能なバックエンド**。利用可能なものが無ければ `RuntimeError`
  （CLI は終了コード 2）。
- 可用性判定 `available` は注入可能（テストで差し替え）。既定は `shutil.which`。
- actuator は run 開始時に 1 つ確定し、run 中固定（2 ドライバが同一デバイスを操作しない）。

> 設計（DESIGN §9）では actuator 以外を read-only な証跡フォールバックに使う構想があるが、現状の
> 実行系は **actuator 単一**で、証跡フォールバックの複数バックエンド利用は未配線。

## 環境管理（simctl）

実装: `bajutsu/env.py`。コマンドビルダは純関数（単体テスト済み）、実行は注入可能な `RunFn` 経由。

| メソッド | コマンド | 備考 |
|---|---|---|
| `erase()` | `simctl erase <udid>` | クリーン環境 |
| `boot()` | `simctl boot <udid>` | 既に boot 済みなら冪等（エラーを握りつぶす） |
| `launch(bundle, args, env)` | `simctl launch --terminate-running-process <udid> <bundle> <args>` | env は `SIMCTL_CHILD_*` で注入 |
| `terminate(bundle)` | `simctl terminate <udid> <bundle>` | 未起動でも無視 |
| `openurl(url)` | `simctl openurl <udid> <url>` | deeplink |
| `screenshot(path)` | `simctl io <udid> screenshot <path>` | — |

> **launch env の注入**: アプリへ渡す env 変数は、親プロセスに `SIMCTL_CHILD_<NAME>` として
> 設定すると子（アプリ）に `<NAME>` で渡る。`child_env()` がこの変換を行う。サンプルアプリの
> `SAMPLE_UITEST` 等の launch hook はこの仕組み（[sample-app](sample-app.md#launch-env-フック)）。

`video` / `deviceLog` の区間録りも `simctl io recordVideo` / `simctl spawn log stream` を使うが、
これらは証跡サブシステム側（`intervals.py`）に置かれている（[evidence](evidence.md#区間証跡video--devicelog)）。

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
| `semanticTap` | id/label で直接タップ（座標不要） | — | — | ✅ |
| `conditionWait` | ネイティブ条件待機 | — | — | ✅ |
| `network` | ネイティブネットワーク監視 | — | — | — |
| `multiTouch` | 2 本指ジェスチャ（pinch / rotate） | — | — | ✅ |

> 実機のバックエンドはどちらも **frame 中心の座標**で操作する。実機で使える semantic tap を
> どちらも公開していない（下の RocketSim 参照）ため、run ループは `query()` で要素を一意に確定し
> その中心をタップする。`pinch` / `rotate` は両者とも `UnsupportedAction`（単一タッチ）で、これらは
> codegen → XCUITest 経由で扱う。

## RocketSim

手元向けのバックエンド（RocketSim の GUI アプリが常駐している必要がある）。
実装: `drivers/rocketsim.py`。

**実機で確認（2026-06）**: RocketSim の `rs/1` agent プロトコルは role / label / value / frame と
*一時的（ephemeral）な* 要素 id を公開するが、**accessibilityIdentifier は持たない**。このため idb と
違い、RocketSim は bajutsu の id 優先セレクタを自力で解決できない。帰結は 2 つ:

1. 識別子は `query()` 内で適用する **[idmap](#識別子の復元idmap)** で復元する。
2. 操作は **frame 中心の座標**（`rocketsim interact tap <x> <y>`）で行い、`--id` の semantic tap は
   使わない（その `--id` は ephemeral id で、スナップショットをまたぐと無意味）。

- `query()`: `rocketsim elements --agent-mode debug --udid <udid>` の出力
  （`{ data: { elements: [...] } }`、frame は `[[x,y],[w,h]]`）を `parse_elements` で正規化し、
  続けてアプリの idmap を適用して識別子を埋める。
- `tap(sel)`: `resolve_unique` → `rocketsim interact tap` で frame 中心をタップ。
- `type_text` / `swipe` / `long_press` → `rocketsim interact type|swipe|long-press`、`screenshot` は
  `simctl io` を使う（idb と同じく信頼できる）。
- 具体的な UDID が必要（`booted` は simctl 専用のエイリアスで、run パイプラインが `env.resolve_udid`
  で解決する）。

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

## 識別子の復元（idmap）

実装: `bajutsu/idmap.py`。アプリ単位・任意（config の `apps.<name>.idMap`、config ファイルからの相対パス）。

idb の `describe-all` は `AXUniqueId`（= accessibilityIdentifier）を持つので、id 優先セレクタは
そのまま解決できる。RocketSim のプロトコルは識別子を一切持たず、role / label / value だけ。**idmap**
がこの差を埋める: 各 accessibilityIdentifier を、RocketSim が *実際に* 報告する内容へのマッチャに
対応づけるテーブル。

```yaml
# sample/sample.idmap.yaml
home.title:        { role: staticText, label: "Home" }      # role でタイトルと「Home」タブを分ける
counter.value:     { role: staticText, labelMatches: "^Count:" }  # 動的テキストは正規表現で
counter.increment: { role: button, label: "+" }
home.search:       { role: textField }                       # 画面で唯一のテキストフィールド
list.row.3:        { role: staticText, label: "Item 3" }
```

`apply(elements, idmap)` は識別子が未設定の各要素に識別子を埋めるが、**マッチャがちょうど 1 件に
解決したときだけ**行う — 曖昧または不在のマッチは未解決のまま残し、セレクタ層が当て推量せず
「一致なし / 曖昧」と報告できるようにする。すでに識別子を提供するバックエンド（idb）は影響を受けない。
こうして同じ id 優先シナリオが両バックエンドで動く。

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
def make_driver(backend, udid, idmap=None) -> Driver:  # "rocketsim" → RocketSimDriver（idmap を使う）, "idb" → IdbDriver
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

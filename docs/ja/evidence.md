[English](../evidence.md) · **日本語**

# 証跡（Evidence/Trace）サブシステム

> 「特定の動作のたびに証跡を取る」要求を、単発指示ではなく **繰り返し発火するルール** として
> 扱う。これにより二度目以降は AI なしで同じ証跡が再現する。
>
> 実装: `bajutsu/evidence.py`（瞬時 + Sink）・`bajutsu/intervals.py`（区間: video / deviceLog）。
> 発火判定は orchestrator 側（[run-loop](run-loop.md#証跡ルールの発火)）。

関連: [scenarios の capture トークン](scenarios.md#capture-トークン文法) ・ [reporting](reporting.md)

---

## 証跡の指示方法（3 つ）

| 方法 | 用途 | 例 |
|---|---|---|
| **A. ルール（`capturePolicy`）** ★中心 | 「特定動作の **たびに**」自動取得 | `settings.*` を tap するたびにスクショ + 要素 |
| **B. ステップ単体（`capture:`）** | この 1 ステップだけ | 特定の wait 後に video + deviceLog |
| **C. 既定ポリシー** | 全体の最低保証 | config の `capture: [screenshot.after, elements, actionLog]` |

> C（config 既定）の `capture` は `Effective.capture` に解決される（[configuration](configuration.md)）が、
> 現状 run ループはシナリオの `capturePolicy` とステップ `capture` のみを発火源にしている。config の
> 既定 capture を全ステップに自動適用する配線は入っていない点に注意。

## 証跡種別と取得タイミング

`capture:` トークンは `<種別>[.<修飾子>]`（[scenarios](scenarios.md#capture-トークン文法)）。

| 種別 | 取得元 | 区間 / 瞬時 | 現状 |
|---|---|---|---|
| `screenshot` | ドライバ（idb は `simctl io screenshot`） | 瞬時 | ✅ 取得 |
| `elements`（a11y ツリー） | `driver.query()` を JSON 化 | 瞬時 | ✅ 取得 |
| `actionLog` | orchestrator 内部（操作・所要時間） | — | ✅ manifest に内在 |
| `video` | `simctl io recordVideo` | 区間 | ✅ 取得（要 udid） |
| `deviceLog` | `simctl spawn log stream` | 区間 | ✅ 取得（要 udid） |
| `network` | （RocketSim 監視 / モックサーバ） | 区間 | ⚠️ **未実装**（取得元が無い） |
| `appTrace` | （os_signpost / OSLog） | 区間 | ⚠️ **未実装** |

> `network` / `appTrace` は capture トークンとして **検証は通る**（スキーマ上は有効）が、`evidence.py` /
> `intervals.py` に取得実装が無いため、現状は記録されない。

**修飾子の既定**: 瞬時系（`screenshot`/`elements`）は `after`、区間系（`video`/`deviceLog`）は `around`
（操作前に開始しステップ後に停止）。`screenshot.before` のように明示すると `before.png` 等のファイル名になる。

## A. `capturePolicy`（ルール方式）

繰り返し発火するルール。シナリオ単位で書く（実装: `scenario.py` `CaptureRule` / `Trigger`）。

```yaml
capturePolicy:
  # settings.* を tap するたびに、押下後のスクショ・要素を取得
  - on: { action: tap, idMatches: "settings.*" }
    capture: [screenshot.after, elements]

  # 画面遷移のたびに
  - on: { event: screenChanged }
    capture: [screenshot.around, elements]

  # どのステップでもエラー時は最大限の証跡（安全網）
  - on: { result: error }
    capture: [screenshot, video, deviceLog, elements, actionLog]
```

（[`sample/scenarios/settings.yaml`](../../sample/scenarios/settings.yaml) に実例）

トリガー `on` は **`action` / `event` / `result` のいずれか 1 つ**:

- `action: <tap|longPress|type|swipe|...>` — 任意で `idMatches`（主対象の `id` に glob 一致）を併用。
  `idMatches` は `action` とのみ併用可。
- `event: screenChanged` — そのステップで `query()` が変化したら発火。
- `result: error` — ステップが失敗したら発火（安全網）。

発火の詳細ロジックは [run-loop](run-loop.md#証跡ルールの発火)。

## B. インライン証跡

そのステップだけ取りたいとき、ステップに直接 `capture:` を付ける。

```yaml
- tap: { id: settings.reindex }
- wait: { for: { id: settings.reindexComplete }, timeout: 5 }
  capture: [video, deviceLog]     # この wait の区間を録る
```

（[`sample/scenarios/evidence.yaml`](../../sample/scenarios/evidence.yaml) に実例）

## 区間証跡（video / deviceLog）

実装: `bajutsu/intervals.py`。どちらも **バックエンド非依存の `simctl` 子プロセス**で、操作前に
開始し、ステップが落ち着いてから停止する。プロセス起動は注入可能（`Spawn`）でテスト可能。

| 種別 | 開始コマンド | 停止シグナル | ファイル名 |
|---|---|---|---|
| `video` | `simctl io <udid> recordVideo --codec h264` | **SIGINT**（強制 kill だと mp4 が壊れる） | `segment.mp4` |
| `deviceLog` | `simctl spawn <udid> log stream --level debug --style compact [--predicate ...]` | SIGTERM | `device.log` |

- `start_video` / `start_device_log` が `Interval` を返し、`Interval.stop()` でシグナルを送って
  ファイルを確定する。停止は最大 10s 待ち、超えたら kill。
- deviceLog は `--predicate`（NSPredicate）でサブシステム等に絞れる（CLI の `--log-predicate`）。
- `INTERVAL_KINDS = {"video", "deviceLog"}`。orchestrator はこの集合で「区間 / 瞬時」を振り分ける。

## Sink（証跡の出力先）

```python
class EvidenceSink(Protocol):
    def start_intervals(self, step_id, kinds) -> list[Interval]: ...   # 操作前に区間を開始
    def capture(self, driver, step_id, kinds) -> list[Artifact]: ...   # ステップ後に瞬時を取得
```

| Sink | 挙動 |
|---|---|
| `NullSink`（既定） | 何も書かない（run を副作用フリーに保つ） |
| `FileSink(run_dir, udid, log_predicate)` | `run_dir/<step_id>/` 配下に書き出す |

`FileSink` は `udid` が無いと区間証跡をスキップする（simctl が要るため）。CLI の `run` は
`FileSink(runs/<runId>, udid=..., log_predicate=...)` を使う（[cli](cli.md#run)）。

## アーティファクトの来歴（provider）

すべての証跡は `Artifact(name, kind, provider)` として記録され、**どの provider から来たか**を
manifest に残す。

```python
@dataclass
class Artifact:
    name: str       # ファイル名（例 "after.png"）
    kind: str       # "screenshot" / "elements" / "video" / "deviceLog"
    provider: str   # "driver"（瞬時）/ "simctl"（区間）
```

## マスキング（redact）

スクショ / ログ / ネットワークに PII・トークンが写り得るため、保存前にマスクする対象を宣言する。
実装: `scenario.py` `Redact`。config の `redact` とシナリオの `redact` はマージ（union）される
（[configuration](configuration.md#redact-のマージ)）。

```yaml
redact:
  labels: ["カード番号"]            # accessibility ラベル
  headers: ["Authorization", "Cookie"]  # HTTP ヘッダ名
  fields: ["token", "password"]    # JSON/body フィールド名
```

> ⚠️ redact の **適用（実際のマスク処理）** は、対象となる証跡（特に network）と合わせて今後の配線。
> 現状は宣言（スキーマとマージ）まで。

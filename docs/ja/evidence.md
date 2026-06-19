[English](../evidence.md) · **日本語**

# 証跡（Evidence/Trace）サブシステム

繰り返し発生する動作に対する証跡取得は、単発指示ではなく **繰り返し発火するルール**として表現します。これにより、2 回目以降も AI なしで同じ証跡が収集されます。

実装: `bajutsu/evidence.py`（瞬時 + Sink）・`bajutsu/intervals.py`（区間: video / deviceLog）。発火判定は orchestrator 側（[run-loop](run-loop.md#証跡ルールの発火)）で行われます。

関連: [scenarios の capture トークン](scenarios.md#capture-トークン文法) ・ [reporting](reporting.md)

---

## 証跡の指示方法（3 つ）

| 方法 | 用途 | 例 |
|---|---|---|
| **A. ルール（`capturePolicy`）** ★中心 | 「特定動作の **たびに**」自動取得 | `settings.*` を tap するたびにスクショ + 要素 |
| **B. ステップ単体（`capture:`）** | この 1 ステップだけ | 特定の wait 後に video + deviceLog |
| **C. 既定ポリシー** | 全体の最低保証 | config の `capture: [screenshot.after, elements, actionLog]` |

> C（config 既定）の `capture` は `Effective.capture` に解決されます（[configuration](configuration.md)）。ただし現状、run ループはシナリオの `capturePolicy` とステップ `capture` のみを発火源にしています。config の既定 capture を全ステップに自動適用する配線は入っていません。

## 証跡種別と取得タイミング

`capture:` トークンは `<種別>[.<修飾子>]`（[scenarios](scenarios.md#capture-トークン文法)）。

| 種別 | 取得元 | 区間 / 瞬時 | 現状 |
|---|---|---|---|
| `screenshot` | ドライバ（idb は `simctl io screenshot`） | 瞬時 | ✅ 取得 |
| `elements`（a11y＝アクセシビリティのツリー） | `driver.query()` を JSON 化 | 瞬時 | ✅ 取得 |
| `actionLog` | orchestrator 内部（操作・所要時間） | — | ✅ manifest に内在 |
| `video` | `simctl io recordVideo` | 区間 | ✅ 取得（要 udid） |
| `deviceLog` | `simctl spawn log stream` | 区間 | ✅ 取得（要 udid） |
| `network` | アプリ内 collector（BajutsuKit → `network.json`） | 区間 | ✅ 取得（`--network` フラグ） |
| `appTrace` | `simctl spawn log stream`（アプリの os_log subsystem） | 区間 | ✅ 取得（要 udid + subsystem） |

> `appTrace` はアプリの `os_signpost` / `os_log` の `<name> started` / `<name> finished` マーカーを区間にペアリングします（`intervals.parse_app_trace`）。`network` は区間システムではなく request collector が生成し、各 exchange を `<sid>/network.json` に書き出します（`--network` フラグ）。

**修飾子の既定**: 瞬時系（`screenshot`/`elements`）は `after`、区間系（`video`/`deviceLog`）は `around`（操作前に開始しステップ後に停止）です。`screenshot.before` のように明示すると `before.png` 等のファイル名になります。

## A. `capturePolicy`（ルール方式）

繰り返し発火するルールです。シナリオ単位で記述します（実装: `scenario.py` `CaptureRule` / `Trigger`）。

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

（[`demos/features/app/scenarios/settings.yaml`](../../demos/features/app/scenarios/settings.yaml) に実例）

トリガー `on` は **`action` / `event` / `result` のいずれか 1 つ**です。

- `action: <tap|longPress|type|swipe|...>` — 任意で `idMatches`（主対象の `id` に glob 一致）を併用できます。`idMatches` は `action` とのみ併用可能です。
- `event: screenChanged` — そのステップで `query()` が変化したら発火します。
- `result: error` — ステップが失敗したら発火します（安全網）。

発火の詳細ロジックは [run-loop](run-loop.md#証跡ルールの発火) にあります。

> **実行前に発火を確認する（BE-0028）。** 緩い glob や `screenChanged` ルールは意図より多くの
> ステップで発火しがちで、そこに heavy capture（`video` / `network`）を付けると気づかぬうちに
> ギガバイト級の証跡を生みます。`bajutsu trace --explain <scenario.yaml>` は読み取り専用のドライランで、
> 各ルールが何回（どのステップで）発火するかを数え、広くマッチするルールの heavy capture を ⚠ で
> 警告します。コストを払う前にマッチを絞り込めます。詳細は [cli](cli.md#trace)。

## B. インライン証跡

特定の 1 ステップだけ証跡を取りたい場合は、そのステップに直接 `capture:` を付けます。

```yaml
- tap: { id: settings.reindex }
- wait: { for: { id: settings.reindexComplete }, timeout: 5 }
  capture: [video, deviceLog]     # この wait の区間を録る
```

（[`demos/features/app/scenarios/evidence.yaml`](../../demos/features/app/scenarios/evidence.yaml) に実例）

## 区間証跡（video / deviceLog）

実装: `bajutsu/intervals.py`。どちらも **バックエンド非依存の `simctl` 子プロセス**で、操作前に開始し、ステップが落ち着いてから停止します。プロセス起動は注入可能（`Spawn`）でテスト可能です。

| 種別 | 開始コマンド | 停止シグナル | ファイル名 |
|---|---|---|---|
| `video` | `simctl io <udid> recordVideo --codec h264` | **SIGINT**（強制 kill だと mp4 が壊れる） | `segment.mp4` |
| `deviceLog` | `simctl spawn <udid> log stream --level debug --style compact [--predicate ...]` | SIGTERM | `device.log` |

- `start_video` / `start_device_log` が `Interval` を返し、`Interval.stop()` でシグナルを送ってファイルを確定します。停止は最大 10s 待ち、超えたら kill します。
- deviceLog は `--predicate`（NSPredicate）でサブシステム等に絞れます（CLI の `--log-predicate`）。
- `INTERVAL_KINDS = {"video", "deviceLog"}`。orchestrator はこの集合で「区間 / 瞬時」を振り分けます。

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

`FileSink` は `udid` が無いと区間証跡をスキップします（simctl が必要なため）。CLI の `run` は `FileSink(runs/<runId>, udid=..., log_predicate=...)` を使用します（[cli](cli.md#run)）。

## アーティファクトの来歴（provider）

すべての証跡は `Artifact(name, kind, provider)` として記録され、**どの provider から来たか**を manifest に残します。

```python
@dataclass
class Artifact:
    name: str       # ファイル名（例 "after.png"）
    kind: str       # "screenshot" / "elements" / "video" / "deviceLog"
    provider: str   # "driver"（瞬時）/ "simctl"（区間）
```

## マスキング（redact）

スクリーンショット・ログ・ネットワークデータには PII（個人情報）やトークンが含まれる可能性があります。保存前にマスクする対象を宣言してください。実装: `scenario.py` `Redact`。config の `redact` とシナリオの `redact` はマージ（union）されます（[configuration](configuration.md#redact-のマージ)）。

```yaml
redact:
  labels: ["カード番号"]            # accessibility ラベル
  headers: ["Authorization", "Cookie"]  # HTTP ヘッダ名
  fields: ["token", "password"]    # JSON/body フィールド名
```

> redact は証跡の書き出し前に **適用されます**（`redaction.py` `Redactor`）。device log / app trace は key→value パターンでスクラブされ、要素ツリーは label が設定済みの場合に value をマスクします（または埋め込まれた secret をスクラブします）。各 network exchange は構造的にマスクされます。ヘッダ値は名前で、url / request / response の body はフリーテキストとして処理されるため、クエリパラメータや `token` / `password` の body フィールドも捕捉されます。画像（スクリーンショット / video）はマスクできず、そのまま残ります。
>
> redact は **secret の入力値** にも適用されます。`${secrets.X}` の背後にある実値（環境から解決、config の `secrets:` で宣言・[configuration](configuration.md#シークレットsecrets)）は、設定済みの `labels` / `headers` / `fields` だけでなく、証跡に現れる箇所すべてでマスクされます。長い値から先にマスクするため、ある値が別の値の部分文字列であっても部分的な漏れは発生しません。

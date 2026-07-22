[English](../evidence.md) · **日本語**

# 証跡（Evidence/Trace）サブシステム

繰り返し発生する動作の[証跡](glossary.md#証跡-capturepolicy-trace-triage)は、単発の指示ではなく **繰り返し発火するルール**として表現します。こうすると、2 回目以降も AI なしで同じ証跡が集まります。

実装: `bajutsu/evidence/core.py`（瞬時 + Sink）、`bajutsu/evidence/intervals.py`（区間: video / deviceLog / appTrace）。発火判定は orchestrator 側（[run-loop](run-loop.md#証跡ルールの発火)）で行います。

関連: [scenarios の capture トークン](scenarios.md#capture-トークン文法) · [reporting](reporting.md)

---

## 証跡の指示方法（3 つ）

| 方法 | 用途 | 例 |
|---|---|---|
| **A. ルール（`capturePolicy`）** ★中心 | 「特定動作の **たびに**」自動取得 | `settings.*` を tap するたびにスクリーンショット + 要素 |
| **B. ステップ単体（`capture:`）** | この 1 ステップだけ | 特定の wait 後に video + deviceLog |
| **C. 既定ポリシー** | 全体の最低保証 | config の `capture: [screenshot.after, elements, actionLog]` |

> C（config 既定）の `capture` は `Effective.capture` に解決されます（[configuration](configuration.md)）。ただし現状、run ループはシナリオの `capturePolicy` とステップの `capture` だけを発火源にしています。config の既定 capture を全ステップに自動適用する配線は入っていません。

## 証跡種別と取得タイミング

`capture:` トークンは `<種別>[.<修飾子>]`（[scenarios](scenarios.md#capture-トークン文法)）。

| 種別 | 取得元 | 区間 / 瞬時 | 現状 |
|---|---|---|---|
| `screenshot` | ドライバ（idb は `simctl io screenshot`） | 瞬時 | ✅ 取得 |
| `elements`（a11y＝アクセシビリティのツリー） | `driver.query()` を JSON 化 | 瞬時 | ✅ 取得 |
| `actionLog` | orchestrator 内部（操作と所要時間） | — | ✅ manifest に内在 |
| `video` | `simctl io recordVideo` | 区間 | ✅ 取得（要 udid） |
| `deviceLog` | `simctl spawn log stream` | 区間 | ✅ 取得（要 udid） |
| `network` | アプリ内 collector（BajutsuKit → `network.json`） | 区間 | ✅ 取得（`--network` フラグ） |
| `appTrace` | `simctl spawn log stream`（アプリの os_log subsystem） | 区間 | ✅ 取得（要 udid + subsystem） |

> `appTrace` はアプリの `os_signpost` / `os_log` が出す `<name> started` / `<name> finished` マーカーを、時刻つきの区間にペアリングします（`intervals.parse_app_trace`）。`network` は区間システムではなく request collector が生成し、その exchange を `<sid>/network.json` に書き出します（[network observation](drivers.md)、`--network` フラグ）。

**修飾子の既定**：瞬時系（`screenshot`/`elements`）は `after`、区間系（`video`/`deviceLog`）は `around`（操作前に開始し、ステップ後に停止）です。`screenshot.before` のように明示すると、`before.png` のようなファイル名になります。

## A. `capturePolicy`（ルール方式）

繰り返し発火するルールです。シナリオ単位で記述します（実装: `scenario/models/evidence.py` `CaptureRule` / `Trigger`）。

```yaml
capturePolicy:
  # settings.* を tap するたびに、押下後のスクショと要素を取得
  - on: { action: tap, idMatches: "settings.*" }
    capture: [screenshot.after, elements]

  # 画面遷移のたびに
  - on: { event: screenChanged }
    capture: [screenshot.around, elements]

  # どのステップでもエラー時は最大限の証跡（安全網）
  - on: { result: error }
    capture: [screenshot, video, deviceLog, elements, actionLog]
```

トリガー `on` は **`action` / `event` / `result` のいずれか 1 つ**です。

- `action: <tap|longPress|type|swipe|...>`：任意で `idMatches`（主対象の `id` に glob 一致）を併用できます。`idMatches` は `action` とのみ併用できます。
- `event: screenChanged`：そのステップで `query()` が変化したら発火します。
- `result: error`：ステップが失敗したら発火します（安全網）。

発火の詳細ロジックは [run-loop](run-loop.md#証跡ルールの発火) にあります。

> **実行前に発火を確認する（BE-0028）。** 緩い glob や `screenChanged` ルールは、意図より多くの
> ステップで発火しがちです。そこに heavy capture（`video` / `deviceLog` / `appTrace` / `network`）を付けると、
> 気づかぬうちにギガバイト級の証跡を生みます。`bajutsu trace --explain <scenario.yaml>` は読み取り専用のドライランで、
> 各ルールが何回（どのステップで）発火するかを数え、広くマッチするルールの heavy capture を ⚠ で
> 警告します。コストを払う前にマッチを絞り込めます。詳細は [cli](cli.md#trace)。

## B. インライン証跡

特定の 1 ステップだけ証跡を取りたい場合は、そのステップに直接 `capture:` を付けます。

```yaml
- tap: { id: settings.reindex }
- wait: { for: { id: settings.reindexComplete }, timeout: 5 }
  capture: [video, deviceLog]     # この wait の区間を録る
```

（[`demos/showcase/scenarios/evidence.yaml`](../../demos/showcase/scenarios/evidence.yaml) に実例）

## 区間証跡（video / deviceLog / appTrace）

実装: `bajutsu/evidence/intervals.py`。これらは **子プロセス**であり（iOS は `simctl`、Android は `adb`）、操作前に開始し、ステップが落ち着いてから停止します。プロセス起動は注入可能（`Spawn`）で、テストできます。

web は子プロセスを使いません。区間証跡は Playwright ネイティブで、driver が供給します（後述）。`appTrace` も video / deviceLog と同じ区間系です（ペアリングの仕組みは前掲の注を参照）。

> **区間証跡は opt-in です（BE-0028）。** `video` / `deviceLog` / `appTrace` は重いため、シナリオが
> **その kind を要求したときだけ**記録します。要求の経路は、インライン `capture:` か `capturePolicy` ルール
> （例: `video` を取得する `result: error` ルール）です。何も要求しなければ何も記録せず、通常ケースを
> 安価に保ちます。軽量な瞬時の baseline（`screenshot` + `elements`）は常に取得するので、失敗時も証跡が
> 残ります（DESIGN §10）。何が記録されるかは `bajutsu trace --explain` で事前に確認できます
> （[cli](cli.md#trace) 参照）。

| 種別 | 開始コマンド（iOS / Android） | 停止シグナル | ファイル名 |
|---|---|---|---|
| `video` | `simctl io <udid> recordVideo --codec h264` / `adb shell screenrecord` | **SIGINT**（強制 kill だと mp4 が壊れる） | `scenario.mp4` |
| `deviceLog` | `simctl spawn <udid> log stream --level debug --style compact [--predicate ...]` / `adb logcat -T 1` | SIGTERM | `device.log` |

- iOS は `start_video` / `start_device_log`、Android は `start_screenrecord` / `start_logcat` が `Interval` を返し、`Interval.stop()` がシグナルを送ってファイルを確定します。停止は最大 10s 待ち、超えたら kill します。
- `screenrecord` はデバイス側に録画するので、その `Interval` は停止時に確定した mp4 を `adb pull` で回収し、デバイス側のコピーを削除します。pull が失敗した場合（デバイスが消えたなど）、Sink は実体のないパスを記録せず、その 1 件だけを警告つきで捨てます。区間証跡の確定処理の I/O で、通過するはずのシナリオを失敗させません。
- なお `adb screenrecord` は 1 回の録画を約 180 秒（プラットフォームの既定／上限）で打ち切るので、それより長いシナリオの Android 動画はその時点で終わります。この上限と SIGINT による確定の実機での調整は、後続の BE-0007 e2e に含みます。
- deviceLog は iOS では `--predicate`（NSPredicate）でサブシステムなどに絞れます（CLI の `--log-predicate`）。`adb logcat` は絞り込みません（logcat の filterspec は別の構文で、後続の knob です）。取得はリングバッファ全体ではなくシナリオの区間を反映するよう、末尾から追従を始めます。
- `INTERVAL_KINDS = {"video", "deviceLog", "appTrace"}`。orchestrator はこの集合で「区間 / 瞬時」を振り分けます。
- **シナリオ全体の `video` はアプリの起動より前に開始します**。録画がアプリの起動（コールドスタート）を取りこぼさず含むようにするためです。デバイスバックエンドでは環境の `start` が録画を開始し（デバイスの boot とアプリの install の後、`simctl launch` / `am start` の前）、動いている `Interval` を `prestarted_intervals` で返します。Sink はシナリオ開始時にこの録画を新たに開始し直さず引き取り（`intervals.adopt`）、停止時に確定してファイルを `scenario.mp4` へ移します。web も同じ前倒しの取得をブラウザコンテキストの生成時に組み込みます。この前倒しは `records_video_up_front` で制御し、`video` を要求しないシナリオは何も開始しません。

## Sink（証跡の出力先）

```python
class EvidenceSink(Protocol):
    def capture(self, driver, step_id, kinds, *, elements=None) -> list[Artifact]: ...   # ステップ後に瞬時を取得
    def start_scenario_intervals(self, scenario_id, kinds) -> list[Interval]: ...         # シナリオ全体の video / deviceLog / appTrace を開始
    def finish_scenario_intervals(self, scenario_id, started) -> list[Artifact]: ...      # 停止してファイルを回収
```

| Sink | 挙動 |
|---|---|
| `NullSink`（既定） | 何も書かない（run を副作用フリーに保つ） |
| `FileSink(run_dir, udid, log_predicate)` | `run_dir/<step_id>/` 配下に書き出す |

環境が起動前にすでに開始した録画（デバイスバックエンドの `video`）は、新たに開始せず引き取ります。Sink は停止時に確定したファイルをシナリオのディレクトリへ移します。それ以外の区間証跡は、driver が `driver_interval` provider を供給していればそこから取得し（web の Playwright ネイティブなコンソール / 動画、Android の `adb` logcat）、供給していなければ `FileSink` は simctl の経路を使い、`udid` が無ければスキップします。CLI の `run` は `FileSink(runs/<runId>, udid=..., log_predicate=...)` を使用します（[cli](cli.md#run)）。

## 初回 wait のタイムアウト診断（BE-0231）

`wait for <要素>` がタイムアウトすると、`run_dir/<step_id>/wait-timeout.json` を **無条件で** 書き出します。`capturePolicy` とは独立しているため、どのルールも取得しないようなタイムアウトでも、なぜ発生したかを判断するのに必要な証跡が残ります。これは純粋な診断であり、判定の入力にはなりません（run の合否は、機械で検査できるアサーションだけから決まります）。

このファイルは自己完結しているので、リトライで緑になっても証跡が失われません。

| フィールド | 何を答えるか |
|---|---|
| `readiness` | 起動後の準備完了ゲートを通過したか、どのシグナル（`readyWhen` / `namespace` / `count`、あるいは通過せず `timeout`）で通過したかです。「ゲートがコンテンツより先に返った」のか「コンテンツは描画されたが待機対象の要素が現れなかった」のかを切り分けます。準備完了結果を持たないレーンでは `null` になります。 |
| `trace` | ポーリングの時系列です。何回ポーリングしたか、ツリーが最初に空でなくなった時刻（`firstNonemptySeconds`、一度も空でなくならなければ `null`）、タイムアウト時点で要素がいくつあったかを記録し、「何も描画されなかった / 一時的に空」「描画されたが待機対象の要素が無い」「コールドブートで描画が遅い」を切り分けます。 |
| `provenance` | [BE-0049](../../roadmaps/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md) のスタンプ（シナリオハッシュ、ツールバージョン、git リビジョン）です。run から独立して証跡を識別できるようにします。この `scenarioHash` は**このシナリオ単体**のフィンガープリントです。run マニフェストの `scenarioHash` は、存在すればファイルレベルの `description` を取り込みますが、こちらはそれを含みません。そのため、スイートやマトリクスの run に限らず、単一シナリオの run でもマニフェストのハッシュと一致しないことがあります。 |
| `elements` | タイムアウトした瞬間の要素ツリー（マスキング済み）です。 |

これは `Artifact(kind="waitDiagnostic", provider="runner")` として記録します。バックエンドの actuator ではなく、run ループが書き出します。

## アーティファクトの来歴（provider）

すべての証跡は `Artifact(name, kind, provider)` として記録し、**どの provider から来たか**を manifest に残します。

```python
@dataclass
class Artifact:
    name: str       # ファイル名（例 "after.png"）
    kind: str       # "screenshot" / "elements" / "video" / "deviceLog" / "network" / "waitDiagnostic"
    provider: str   # このアーティファクトを供給した provider（下表参照）
```

| `provider` の値 | 意味 |
|---|---|
| `"driver"` | actuator が直接取得した証跡です（スクリーンショット、要素ツリー）。 |
| `"runner"` | run ループが書き出した証跡です（初回 wait のタイムアウト診断、[BE-0231](../../roadmaps/BE-0231-smoke-idb-first-wait-settling/BE-0231-smoke-idb-first-wait-settling-ja.md)）。 |
| `"simctl"` | `simctl` による区間証跡です（動画、デバイスログ、アプリトレース）。 |
| `"adb"` | `adb` による区間証跡です（screenrecord の動画、logcat のデバイスログ）。 |
| `"collector"` | idb のアプリ側ネットワークコレクタ（`BAJUTSU_COLLECTOR`）です。 |
| `"playwright"` | Playwright のネイティブなネットワーク観測です（web バックエンド）。 |
| `"<backend> (fallback)"` | read-only な証跡フォールバックが供給したアーティファクトです（[BE-0020](../../roadmaps/BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback-ja.md)）。 |

証跡の種別をリスト内のどのバックエンドも供給できない場合は、シナリオごとに `SkippedCapture(kind, reason)` を記録し、manifest で開示します。gap を黙って空にすることはありません。

## ビジュアル証跡

`visual` アサーションは `VisualEvidence` レコードを生成し、manifest とレポートに反映します。run ディレクトリからの相対パスとして、baseline コピー、実際のスクリーンショット、差分画像（差分が見つかった場合）を持ち、`diff_pct`（差分ピクセルの割合）と `engine`（判定を行った比較エンジン、`"exact"` または `"pixelmatch"`。[BE-0165](../../roadmaps/BE-0165-visual-compare-engines/BE-0165-visual-compare-engines-ja.md)）を記録します。

エンジンはアサーション単位（`compare:`）で選択でき、ターゲットレベルの config（`visualCompare`）にフォールバックします。使用されたエンジンは manifest に記録されるため、各判定がどのアルゴリズムで行われたかを追跡できます。実装: `bajutsu/assertions/visual.py` `VisualEvidence`。

## マスキング（redact）

スクリーンショット、ログ、ネットワークデータには、PII（個人情報）やトークンが写り込む可能性があります。保存前に、マスクする対象を宣言してください。実装: `scenario/models/evidence.py` `Redact`。config の `redact` とシナリオの `redact` はマージ（union）されます（[configuration](configuration.md#redact-のマージ)）。

```yaml
redact:
  labels: ["カード番号"]            # accessibility ラベル
  headers: ["X-Session"]           # 追加の HTTP ヘッダ名（既定集合に上乗せ）
  fields: ["token", "password"]    # JSON/body フィールド名
  unmaskHeaders: ["authorization"] # 既定の保護を外す（明示的で目に見える指定）
```

> **機密ヘッダは既定でマスクされます**（この保護にシナリオ側の `redact:` は不要です）。組み込みの集合は
> `authorization`、`proxy-authorization`、`cookie`、`set-cookie`、`x-api-key`、`x-auth-token` で、
> 大文字小文字を区別せずに照合します。`cookie` と `set-cookie` は一つの関心事として扱い、どちらか一方を
> 指定（または解除）すると両方に適用されます。`redact.headers` に書いたヘッダ名はこの集合に上乗せされる
> だけで、集合を置き換えることはありません。既定ヘッダの生の値がどうしても必要なとき（認証失敗のデバッグ
> など）は、そのヘッダ名を `unmaskHeaders` に書きます。保護を外すのは明示的で目に見える選択であり、
> `redact:` を書かないだけで外れることはありません。

> redact は証跡の書き出し前に **適用されます**（`evidence/redaction.py` `Redactor`）。device log と app trace は key→value パターンでスクラブし、要素ツリーは label が設定済みなら value をマスクします（または埋め込まれた secret をスクラブします）。各 network exchange は構造的にマスクします。ヘッダ値は名前で処理し、url / request / response の body はフリーテキストとして処理するので、クエリパラメータや `token` / `password` の body フィールドも捕捉します。画像（スクリーンショット / video）はマスクできず、そのまま残ります。
>
> redact は **secret の入力値** にも及びます。`${secrets.X}` の背後にある実値（環境から解決し、config の `secrets:` で宣言します。[configuration](configuration.md#シークレットsecrets)）は、設定済みの `labels` / `headers` / `fields` だけでなく、証跡に現れる箇所すべてでマスクします。長い値から先にマスクするため、ある値が別の値の部分文字列であっても、部分的な漏れは起きません。
>
> 値の照合は **エンコードを考慮** します。同じ秘密値でも、証跡に現れるときは多くの場合エンコードされており、そのままのバイト列は現れません。redact は、生の値に加えて、よくあるエンコード形もマスクします。パーセントエンコード（URL のクエリやフォームフィールド。たとえば `p@ss` は `p%40ss` になります）、HTML エスケープと JSON エスケープの形、そして `Authorization: Basic <base64(user:pass)>` トークンのうちデコードした認証情報がその値を含むもの、の三種です。これは既知の値に対して固定された変換を適用する方式（値をエンコードしてから検索します）であり、証跡内のあらゆる文字列をデコードして総当たりする方式ではないので、コストと誤検出の範囲は限定的なままです。一つ制約が残ります。redact が動く前に証跡が実際に断片化している場合（ストリーミングのチャンクにまたがって分割され、redact が一つの連続した文字列として見られない値）は、照合がベストエフォートになります。組み立て済みの全文の証跡という通常のケースには影響しません。
>
> 実行したシナリオは run ディレクトリにもスナップショットとして保存されます（`scenario.yaml`、およびレポートの生 YAML 表示）。`totp` ステップの `secret` は使い捨てのコードではなく恒久的な base32 シードなので、シナリオに **リテラル** で書かれたシードは、このスナップショット内で `<redacted>` にマスクします。`${secrets.X}` 参照はそのまま残します（参照自体はシードではなく、解決後の実値は上記の secret 入力値のルールでマスクされるためです）。`totp` のシードは `${secrets.X}` で渡し、シナリオファイルにシードが残らないようにするのが望ましい方法です。

## ファイルパーミッション

マスキングは漏えいした証跡が明かす内容を減らしますが、ベストエフォートの denylist なので、証跡を誰が読めるかも同じく重要です。ランナーは各 run ディレクトリを所有者のみ（`0700`）で作成し、機微な内容を含み得るファイル（`network.json`、コピーした `scenario.yaml`、要素ダンプ（`elements.json`）、スクリーンショット）を、ホストの `umask` に依存せず所有者のみ（`0600`）で書き込みます（[BE-0131](../../roadmaps/BE-0131-run-artifact-permissions/BE-0131-run-artifact-permissions-ja.md)）。それ以外の証跡も `0700` の run ディレクトリ配下に置かれるため、共有ホスト（CI ランナーなど）の別のローカルアカウントからは既定で読めません。実装: `artifact_perms.py`。

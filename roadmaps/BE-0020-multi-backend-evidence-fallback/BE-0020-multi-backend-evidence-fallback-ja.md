[English](BE-0020-multi-backend-evidence-fallback.md) · **日本語**

# BE-0020 — マルチ backend 証跡フォールバック

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0020](BE-0020-multi-backend-evidence-fallback-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0020") |
| 実装 PR | [#357](https://github.com/bajutsu-e2e/bajutsu/pull/357) |
| トピック | プラットフォーム対応 |
<!-- /BE-METADATA -->

## はじめに

現状、run の actuator は単一です。証跡の取得だけを別の backend に回すことで、能力の差を吸収します。この振り分けは DESIGN §9 で設計済みですが、まだ配線されていません。

## 動機

単一の backend が、あらゆる種類の証跡を提供することはまれです。たとえば idb は、ネイティブのネットワーク監視を持ちません（`capabilities()` は `network` を返しません）。ネットワーク監視を必要とする取得は、別の場所から来なければなりません。

DESIGN §9 は、これをすでに設計しています。

- `backend` は順序付きのリストです。
- **actuator**（最初に利用可能な backend）が、操作と解決をすべて行います。
- それ以外の*あらゆる* backend は、actuator に欠ける能力を供給する、**read-only の証跡フォールバック**として働けます。
- 各アーティファクトは、どの provider から来たかを記録します。そのため manifest は、各証跡の出所について正直なままです。

設計は存在しますが、配線されていません。`docs/drivers.md` ははっきり述べています。「現在の実行経路は単一 actuator を使い、マルチ backend 証跡フォールバックはまだ配線されていない」。そのため現状では、actuator が要求された取得を生み出せないと、証跡は単に skip されます。別の宣言済み backend が read-only で供給できる場合でも、skip されます。

iOS が 2 つ目の actuator（XCUITest、BE-0019）を得て、プロジェクトが他のプラットフォームへ向かうにつれ、この穴は広がります。ある backend が持ち、別の backend が欠く能力は、黙って落とさず、抽象の側で吸収すべきです。これは §9 の意図どおりです。本提案は、既存の設計を run ループに接続します。

## 詳細設計

仕組みは DESIGN §9 にそのまま従います。操作は 1 つの backend にとどまり、証跡の解決は他の backend を read-only で参照します。単一 actuator の側はすでに配線済みで、本提案は*フォールバック*の側を肉付けします。

### 現状（前提）

`backends.py:select_actuator()` は、順序付きの `backend` リストを展開し、**最初に利用可能な** actuator を返します。それ以外のトークンは捨てられるので、runner は 2 つ目の backend を生成しません。

`capabilities_for(actuator)` は、driver を構築せずに backend の*静的な*能力集合を返します。BE-0082 の preflight 用に追加されました。gap 検出器が必要とする、まさにその部品です。

`Capability.NETWORK` は**ネイティブ**の観測を意味します。idb はこれを表明しません。idb は、アプリ側のコレクタ（`BAJUTSU_COLLECTOR` → `NetworkCollector`）で通信を取得するからです。Playwright は表明します（`WebNetworkCollector`）。

`evidence.py:Artifact` は、すでに `provider` フィールドを持ちます。`runner/pool.py:device_pool()` が、唯一の継ぎ目です。ここで 1 つの actuator を選び、デバイスごとのコレクタを事前に起動し、`driver`、`FileSink`、`collector` を各 `Lease` に束ねます。`pipeline.py:run_all` は、シナリオごとにコレクタを `clear()` します。`network.json` は `_write_network` で書き、provider は `"collector"` に固定されています。

### 1 つのリストから 2 つの役割

`select_actuator` は変えません。`evidence_backends(backends, actuator, available)` を足します。このリゾルバは、リスト順で*残りの*利用可能な backend のうち、**actuator と同じプラットフォームに属するもの**を返します。これらが read-only の証跡 provider です。

**適格性は「同じ被テスト対象を観測する」こと**です（下の決定 1）。provider が適格なのは、actuator のプラットフォームに属する actuator へ解決される場合だけです。判定は、`backends.PLATFORMS` の逆引きで行います。`backends.PLATFORMS` は、BE-0042 が追加し、[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md) が土台にする、プラットフォームから actuator へのレジストリです。同じ稼働中のアプリを観測できるのは、同じプラットフォームの backend だけです。そのため、`[ios, web]` のようなプラットフォームをまたぐリストでは、idb actuator に対して web provider は得られません。

**能力 gap の検出**では、各証跡の*種別*を、それをネイティブに供給する capability に対応づけます（今は `network → Capability.NETWORK`）。gap 集合は、その capability を actuator の静的な `capabilities_for(actuator)` が欠く種別です。

**ディスパッチ**では、各 gap 種別について、`capabilities_for` がそれを表明する最初の適格な証跡 backend を選びます。能力ごとに 1 つの provider を、リスト順で割り当てます。ディスパッチの対象外になるものが 2 つあります。

- `screenshot` と `elements` は、常に actuator から取得します。
- `video`、`deviceLog`、`appTrace` は、backend 非依存の `simctl` による取得で、リストとは直交します。

gap 種別を供給できる backend がなければ、**理由を記録して skip** します。これはなだらかな劣化であり、run の失敗にはしません。

現状は各プラットフォームに実装済みの actuator が 1 つだけなので（`ios → idb`、`web → playwright`）、同一プラットフォームのフィルタは、プラットフォームが 2 つ目の actuator を得るまで（iOS と XCUITest、[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)）本番では provider を 1 つも解決しません。これは現在の run を何も変えない安全な no-op です。したがって最初のスライスは、同一プラットフォーム扱いの network 対応 fake で検証します（後述）。

### read-only は規約ではなく構造で強制する

フォールバックの完全な `Driver` を構築して「呼び出し側が操作しない」と信用するのではなく、`drivers/base.py` に狭い `EvidenceProvider` Protocol を導入します。これは `capabilities()` と read-only の観測面（例: `network_collector()`）だけを公開し、`tap` / `type` / `swipe` / `wait` / `query` を持ちません。フォールバックは `EvidenceProvider` 経由でしか参照しないので、「フォールバックは決して操作しない」が mypy strict のコンパイル時の事実になります。これは §3.3 / §5 の単一 actuator 保証の最も強い形です。セレクタ解決（決定性の核である `resolve_unique`）には actuator の `Driver` 経由でしか到達せず、手を触れません。固定 sleep は依然なく、ambiguous なセレクタは依然失敗し、証跡取得は合否経路の完全に外側にあります。

### ライフサイクル（コレクタの仕組みを再利用）

並列実行、後始末、シナリオごとのスコープを無償で得るため、`pool.py` の既存のコレクタのライフサイクルをそのまま踏襲します。

`select_actuator` のあとに gap 種別を求め、gap ごとに provider を解決します。プロセスレベルの provider は、既存の `NetworkCollector` のループと並べて、デバイスごとに事前に起動します。後始末も同じで、失敗時には起動済みの provider を止めます。その環境で provider を起動できなければ、**理由を記録して劣化**させます。

lease ごとに、provider の `Collector` を**既存の `lz.collector` スロット**へ渡します。こうすれば、`request` アサーション、シナリオごとの `clear()`、`network.json` の書き出しは、*まったく*変更が要りません。フォールバックは、既存の Protocol（`network.py:Collector`）の背後にある、もう 1 つの `Collector` 実装にすぎません。

協調はすでに解決済みです。コレクタはバックグラウンドスレッドまたはイベントフックで動き、メインスレッドがスナップショットを読みます。`scenario_start`（launch 後に取る monotonic 値）が、共有のタイムライン原点です。`shutdown()` と `lease.release()` は、現在コレクタを止めているのと同じ方法で provider を後始末します。

### 来歴（manifest を正直に保つ）

`Artifact.provider` は各アーティファクトを実際に供給した backend を名指しすべきです。web のネイティブは `"playwright"`、idb のアプリコレクタは `"collector"`（変更なし）、フォールバックは `"<backend>（fallback）"` です。最初のスライスでは、このフィールドは**素の文字列**のままにします（決定 2）。人が読みやすく、gap の理由は provider 文字列ではなく `SkippedCapture` リストが持ちます。シナリオの結果にシナリオごとの `SkippedCapture(kind, reason)` リストを足し（決定 3）、gap を黙って空にするのではなく*開示*します。これは `asdict` に乗って `manifest.json` に入り、`report/panels.py` で既存の劣化開示の隣に表示します。構造化した provider 形式（`{provider, role, reason}`）、トップレベルの `evidenceBackends: [...]`、対応する `SCHEMA_VERSION` の引き上げ（現在 3。BE-0068 のバージョニングが古い run をなだらかに劣化させます）は、**後続のスライスへ先送り**し、最初のスライスはスキーマを変えません。

### 最初のスライス（価値最大・リスク最小・ゲート内で検証可）

唯一のフォールバック種別として **`network`** を配線し、fake だけで（Simulator なしで）動かします。下流のネットワーク経路（`Collector` Protocol、シナリオごとの `clear()`、`request` アサーション、`network.json` + 来歴）はすでに存在し backend 非依存なので、このスライスは read-only な `Collector` の*解決と生成*を足して既存スロットへ差し込むだけです。純粋な解決層（`evidence_backends`、gap 検出、ディスパッチ）は今日の `select_actuator` と同じく単体テストできます（`available` を注入します）。エンドツーエンドでは、決定的な `NetworkExchange` のリストを返す network 対応の `FakeDriver` 変種（挙動のモックではなくインプロセス）を足し、actuator = network なしの fake と network 対応の fake をリストに並べてシナリオを実行し、`network.json` が書かれること、その `provider` がフォールバックを名指すこと、`request` アサーションが exchange を読めることをアサートします。スライス全体が Linux の `make check` ゲートの内側にとどまります。

### スコープと非目標

**スコープ内**：非 actuator backend からの read-only な証跡 provider、`capabilities_for(actuator)` に対する gap 検出、リスト順での能力ごとのディスパッチ、正直な来歴（アーティファクトの `provider` + skip 記録）、最初のスライス = `network`。

**非目標**：フォールバックによる*操作*（操作の能力差は actuator ラダー BE-0019 の仕事です）、機会的な取れる backend すべてからの取得（下記で不採用）、`video` / `deviceLog` / `appTrace` の解決の変更（backend 非依存の `simctl`）、いかなる LLM（これは証跡の配管であって判定ではありません）、新しい config 面（順序付きの `backend` リストを再利用します）。

### 決定

4 つの未解決の論点は次のように決めます。いずれも上の設計に織り込み済みです。

1. **provider の適格性は「同じ被テスト対象を観測する」こと。** provider が適格なのは、**actuator 自身のプラットフォーム**に属する actuator へ解決する場合だけです（`backends.PLATFORMS` の逆引き）。これにより、跨プラットフォームのリストで一方のプラットフォームの backend が他方のアプリを観測することは決して起きません。これが最重要の制約で、`evidence_backends` の形を決めます。帰結として、プラットフォームが 2 つ目の actuator を得る（BE-0019）まではフォールバックは no-op であり、だからこそ最初のスライスは同一プラットフォームの fake で検証します。
2. **provider 文字列の形式は、最初のスライスでは素の文字列**（`"<backend>（fallback）"`）です。構造化の `{provider, role, reason}` は、`evidenceBackends` を manifest に足して `SCHEMA_VERSION` を引き上げる後続スライスへ先送りし、最初のスライスはスキーマを保ちます。
3. **skip 記録はシナリオごと**です。シナリオが `network` を要求するのは一部のときだけかもしれないので、`SkippedCapture(kind, reason)` は run ではなくシナリオの結果に置きます。
4. **最初のスライスでは provider を厳密に `backend` リスト由来に限ります。** §9 が挙げる対象非依存の供給源（モックサーバ）は (1) のプラットフォーム制約を本当に回避できますが、種類の異なる供給源です。これを `network` provider として配線するのは、このスライスではなく文書化した後続作業とします。

### 実装スケッチ（小さな PR 単位のスライス）

1. **純粋な解決層** — `backends.py`：`evidence_backends`、`KIND_CAPABILITY`、`resolve_evidence_providers(...) -> (provider_per_kind, skipped)`。完全に単体テストし、runner は変えません。
2. **read-only provider インターフェース** — `drivers/base.py:EvidenceProvider` Protocol。`PlaywrightDriver` は network 部分をすでに満たします。テスト用に network 対応の `FakeDriver` 変種を足します。
3. **network フォールバックを pool に配線** — `pool.py` が provider の `Collector` を解決・生成して `lz.collector` へ差し込み、`pipeline._write_network` が正直な provider を刻み、`RunResult` に `skipped_captures` を足します。
4. **来歴の表示 + docs** — 任意で manifest に `evidenceBackends`（+ `SCHEMA_VERSION` 引き上げ）、`report/panels.py` で skip を表示、`docs/drivers.md` + `docs/ja/drivers.md` と `docs/evidence.md` を更新（日本語は `japanese-tech-writing` skill に準拠）。

## 検討した代替案

- **skip のまま放置する（現状維持）。** actuator に欠ける能力を skip するのは単純ですが、宣言済み backend が生み出せたはずの証跡を捨て、まさに Bajutsu が支えるべき失敗調査を弱めます。§9 が既にフォールバックを設計し `provider` フィールドが既に存在する以上、それを配線するのは新しいアーキテクチャではなく、小さく忠実な一歩です。
- **あるステップで「より良い」フォールバック backend に操作させる。** これは一部のアクションを 2 つ目のドライバへ流します。単一 actuator の規則を破り、1 つのデバイスを取り合う 2 ドライバの非決定性を再導入します。操作は固定された 1 つの actuator にとどまらねばなりません。*操作*の能力差は actuator ラダー（BE-0019）で扱い、ここで埋めるのは*証跡*の差だけで、read-only です。
- **各 backend の取得を機会的に統合する（取れる backend すべてから取る）。** 同じ証跡を複数 backend から同時に取得することはコストを増やし、どのコピーが正本かを曖昧にし、§9 が警告する観測者効果のリスクを招きます。能力ごとに、リスト順で 1 つの provider に解決する方が、単一の明確な出所と有界なコストを保てます。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

[drivers.md](../../docs/ja/drivers.md)（「まだ配線されていない」の記述）、[evidence.md](../../docs/ja/evidence.md)、[DESIGN §9](../../DESIGN.md)。`bajutsu/backends.py`（`select_actuator`、`resolve_actuators`、`capabilities_for`）、`bajutsu/drivers/base.py`（`Capability`、`Driver`、`resolve_unique`）、`bajutsu/capability_preflight.py`（idb の `network` の注記）、`bajutsu/evidence.py`（`Artifact`、`FileSink`）、`bajutsu/network.py`（`Collector`、`NetworkCollector`）、`bajutsu/runner/pool.py`（継ぎ目となる `device_pool`）、`bajutsu/runner/pipeline.py`（`_write_network`、`run_all`）、`bajutsu/report/manifest.py`（`SCHEMA_VERSION`）、`bajutsu/drivers/playwright.py`（ネイティブ `network`、`network_collector`）、`bajutsu/drivers/fake.py`（`CAPABILITIES`）。

**依存 / 関連項目**：[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)（2 つ目の iOS actuator は価値を大きく高めます。一方の iOS actuator が他方の gap を埋めます。ただし本提案は fake で先行着地できます）、[BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md)（`capabilities_for` とここで再利用する純粋 preflight のパターンを提供します）。web（Playwright）backend はすでにネイティブ `network` を持ち、read-only な `Collector` の参照実装です。

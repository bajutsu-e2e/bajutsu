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

現状、各プラットフォームに実装済みの actuator は 1 つだけです（`ios → idb`、`web → playwright`）。そのため本番では、プラットフォームが 2 つ目の actuator を得るまで、同一プラットフォームのフィルタは provider を 1 つも解決しません。2 つ目の actuator とは、iOS の XCUITest です（[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)）。フィルタは安全な no-op になり、現在の run は何も変わりません。したがって最初のスライスは、network に対応した同一プラットフォームの fake で検証します（後述）。

### read-only は規約ではなく構造で強制する

フォールバックの完全な `Driver` を構築すると、呼び出し側が操作しないことを信用するしかありません。代わりに、`drivers/base.py` に狭い `EvidenceProvider` Protocol を導入します。公開するのは、`capabilities()` と read-only の観測面（例: `network_collector()`）だけです。`tap`、`type`、`swipe`、`wait`、`query` は持ちません。

フォールバックは `EvidenceProvider` 経由でしか参照しません。そのため、「フォールバックは決して操作しない」ことが、mypy strict のコンパイル時の事実になります。これは、§3.3 と §5 にある単一 actuator の保証の、もっとも強い形です。

セレクタ解決（決定性の核である `resolve_unique`）には、actuator の `Driver` 経由でしか到達しません。この設計は解決に手を触れません。固定 sleep は引き続きなく、曖昧なセレクタは引き続き失敗します。証跡の取得は、合否の経路の完全に外側にあります。

### ライフサイクル（コレクタの仕組みを再利用）

並列実行、後始末、シナリオごとのスコープを無償で得るため、`pool.py` の既存のコレクタのライフサイクルをそのまま踏襲します。

`select_actuator` のあとに gap 種別を求め、gap ごとに provider を解決します。プロセスレベルの provider は、既存の `NetworkCollector` のループと並べて、デバイスごとに事前に起動します。後始末も同じで、失敗時には起動済みの provider を止めます。その環境で provider を起動できなければ、**理由を記録して劣化**させます。

lease ごとに、provider の `Collector` を**既存の `lz.collector` スロット**へ渡します。こうすれば、`request` アサーション、シナリオごとの `clear()`、`network.json` の書き出しは、*まったく*変更が要りません。フォールバックは、既存の Protocol（`network.py:Collector`）の背後にある、もう 1 つの `Collector` 実装にすぎません。

協調はすでに解決済みです。コレクタはバックグラウンドスレッドまたはイベントフックで動き、メインスレッドがスナップショットを読みます。`scenario_start`（launch 後に取る monotonic 値）が、共有のタイムライン原点です。`shutdown()` と `lease.release()` は、現在コレクタを止めているのと同じ方法で provider を後始末します。

### 来歴（manifest を正直に保つ）

`Artifact.provider` は、各アーティファクトを実際に供給した backend を名指さなければなりません。

- web のネイティブ：`"playwright"`
- idb のアプリ側コレクタ：`"collector"`（変更なし）
- フォールバック：`"<backend>（fallback）"`

最初のスライスでは、このフィールドを**素の文字列**のままにします（決定 2）。素の文字列は人が読みやすく、gap の理由は provider 文字列ではなく `SkippedCapture` リストが持ちます。

シナリオの結果に、シナリオごとの `SkippedCapture(kind, reason)` リストを足します（決定 3）。これで gap は、黙って空になるのではなく、*開示*されます。このリストは `asdict` に乗って `manifest.json` に入ります。`report/panels.py` では、既存の劣化開示の隣に表示します。

次の 3 つは**後続のスライスへ先送り**します。最初のスライスはスキーマを変えません。

- 構造化した provider 形式（`{provider, role, reason}`）
- トップレベルの `evidenceBackends: [...]`
- 対応する `SCHEMA_VERSION` の引き上げ（現在は 3。BE-0068 のバージョニングが、古い run をなだらかに劣化させます）

### 最初のスライス（価値が最大で、リスクが最小で、ゲート内で検証できる）

唯一のフォールバック種別として **`network`** を配線し、Simulator なしで、fake だけを使って動かします。下流のネットワーク経路は、すでにすべて存在し、backend 非依存です。`Collector` Protocol、シナリオごとの `clear()`、`request` アサーション、来歴つきの `network.json` がそれに当たります。したがってこのスライスが足すのは、read-only な `Collector` の*解決と生成*だけです。それを既存のスロットへ差し込みます。

純粋な解決層（`evidence_backends`、gap 検出、ディスパッチ）は、今日の `select_actuator` と同じく単体テストできます。テストでは `available` を注入します。

エンドツーエンドのテストは、次の手順で行います。

1. network 対応の `FakeDriver` の変種を足します。決定的な `NetworkExchange` のリストを、挙動のモックではなくインプロセスで返します。
2. リストに、network なしの fake を actuator として、network 対応の fake を 1 つ並べ、シナリオを実行します。
3. `network.json` が書かれること、その `provider` がフォールバックを名指すこと、`request` アサーションが exchange を読めることをアサートします。

スライス全体が、Linux の `make check` ゲートの内側にとどまります。

### スコープと非目標

**スコープ内：**

- 非 actuator の backend による、read-only の証跡 provider。
- `capabilities_for(actuator)` に対する gap 検出。
- リスト順での、能力ごとのディスパッチ。
- 正直な来歴（アーティファクトの `provider` と skip 記録）。
- 最初のスライス：`network`。

**非目標：**

- フォールバックによる*操作*。操作の能力差は、actuator ラダー（BE-0019）の仕事です。
- 取れる backend すべてからの、機会的な取得（下記で不採用）。
- `video`、`deviceLog`、`appTrace` の解決の変更（backend 非依存の `simctl`）。
- いかなる LLM。これは証跡の配管であり、判定はしません。
- 新しい config の面。順序付きの `backend` リストを再利用します。

### 決定

4 つの未解決の論点は、次のように決めます。いずれも上の設計に織り込み済みです。

1. **provider の適格性は「同じ被テスト対象を観測する」こと。** provider が適格なのは、**actuator 自身のプラットフォーム**に属する actuator へ解決される場合だけです（`backends.PLATFORMS` の逆引き）。これにより、プラットフォームをまたぐリストで、一方の backend が他方のアプリを観測することは起きません。これがもっとも重要な制約で、`evidence_backends` の形を決めます。帰結として、プラットフォームが 2 つ目の actuator を得る（BE-0019）まで、フォールバックは no-op です。最初のスライスを同一プラットフォームの fake で検証するのは、このためです。
2. **provider 文字列の形式は、最初のスライスでは素の文字列**（`"<backend>（fallback）"`）です。構造化した `{provider, role, reason}` は、後続のスライスへ先送りします。後続のスライスでは、`evidenceBackends` を manifest に足し、`SCHEMA_VERSION` も引き上げます。これで最初のスライスは、スキーマを保てます。
3. **skip 記録はシナリオごと**です。シナリオが `network` を要求するのは、一部のときだけかもしれません。そのため `SkippedCapture(kind, reason)` は、run ではなくシナリオの結果に置きます。
4. **最初のスライスでは、provider を厳密に `backend` リスト由来に限ります。** §9 が挙げる、対象に依存しない供給源（モックサーバ）は、決定 1 のプラットフォーム制約を回避できます。ただし、これは種類の異なる供給源です。`network` provider として配線するのは、このスライスではなく、文書化した後続作業とします。

### 実装スケッチ（小さな PR 単位のスライス）

1. **純粋な解決層。** `backends.py` に、`evidence_backends`、`KIND_CAPABILITY`、`resolve_evidence_providers(...) -> (provider_per_kind, skipped)` を足します。完全に単体テストし、runner は変えません。
2. **read-only な provider のインターフェース。** `drivers/base.py:EvidenceProvider` Protocol を足します。`PlaywrightDriver` は、network の部分をすでに満たします。テスト用に、network 対応の `FakeDriver` の変種を足します。
3. **network フォールバックを pool に配線する。** `pool.py` が provider を解決し、その `Collector` を `lz.collector` へ生成して差し込みます。`pipeline._write_network` が、正直な provider を刻みます。`RunResult` に `skipped_captures` を足します。
4. **来歴の表示と docs。** 任意で、manifest に `evidenceBackends` を足します（`SCHEMA_VERSION` の引き上げを伴います）。`report/panels.py` で skip を表示します。`docs/drivers.md`、`docs/ja/drivers.md`、`docs/evidence.md` を更新します（日本語は `japanese-tech-writing` skill に準拠します）。

## 検討した代替案

- **skip のまま放置する案（現状維持）。** actuator に欠ける能力を skip するのは単純です。しかし、宣言済みの backend が生み出せたはずの証跡を捨てることになり、Bajutsu が支えるべき失敗調査を弱めます。DESIGN §9 がフォールバックを設計済みで、`provider` フィールドも存在します。したがってフォールバックの配線は、新しいアーキテクチャではなく、小さく忠実な一歩です。
- **あるステップで「より良い」フォールバック backend に操作させる案。** 一部のアクションを 2 つ目のドライバへ流すことになります。単一 actuator の規則を破り、1 つのデバイスを取り合う 2 つのドライバの非決定性を、再導入します。操作は、固定された 1 つの actuator にとどまらなければなりません。*操作*の能力差は、actuator ラダー（BE-0019）が扱います。本提案が埋めるのは、*証跡*の差だけで、read-only です。
- **各 backend の取得を機会的に統合する案（取れる backend すべてから取る）。** 同じ証跡を複数の backend から同時に取ると、コストが増えます。どのコピーが正本かも曖昧になり、§9 が警告する観測者効果のリスクも招きます。能力ごとに、リスト順で 1 つの provider に解決すれば、単一で明確な出所と、有界なコストを保てます。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

- ドキュメント：[drivers.md](../../docs/ja/drivers.md)（「まだ配線されていない」の記述）、[evidence.md](../../docs/ja/evidence.md)、[DESIGN §9](../../DESIGN.md)。
- `bajutsu/backends.py`：`select_actuator`、`resolve_actuators`、`capabilities_for`。
- `bajutsu/drivers/base.py`：`Capability`、`Driver`、`resolve_unique`。
- `bajutsu/capability_preflight.py`：idb の `network` の注記。
- `bajutsu/evidence.py`：`Artifact`、`FileSink`。
- `bajutsu/network.py`：`Collector`、`NetworkCollector`。
- `bajutsu/runner/pool.py`：継ぎ目となる `device_pool`。
- `bajutsu/runner/pipeline.py`：`_write_network`、`run_all`。
- `bajutsu/report/manifest.py`：`SCHEMA_VERSION`。
- `bajutsu/drivers/playwright.py`：ネイティブの `network`、`network_collector`。
- `bajutsu/drivers/fake.py`：`CAPABILITIES`。

**依存と関連項目：**

- [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)：2 つ目の iOS actuator は、価値を大きく高めます。一方の iOS actuator が、他方の gap を埋めるからです。本項目は、fake を使って先行して着地できます。
- [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md)：`capabilities_for` と、ここで再利用する純粋 preflight のパターンを提供します。
- web（Playwright）backend は、すでにネイティブの `network` を持ちます。read-only な `Collector` の参照実装です。

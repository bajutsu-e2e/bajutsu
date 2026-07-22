# Bajutsu：自然言語駆動 E2E テストツールの設計指針

> この文書は Bajutsu の設計判断とその根拠を記録します。個々の機能が現時点でどこまで実装されているかは [`docs/ja/architecture.md`](docs/ja/architecture.md)（実装状況）を正とし、本文の記述と実装が食い違うときはそちらを優先してください。
> ツール名 `Bajutsu` は仮称。

---

## 1. 目的とスコープ

自然言語で書かれたテストシナリオを受け取り、backend 非依存のドライバ越しにアプリを操作して E2E テストを実行し、結果を検証するツールです。プラットフォームは 1 つのドライバインターフェースの背後にある backend にすぎません。対象は iOS Simulator（XCUITest）、ブラウザ（Playwright）、Android（adb）です。Android は fast ゲートとエミュレータ上の e2e CI まで到達しており（BE-0208）、実機での駆動が次の課題です。シナリオ形式、セレクタ解決、決定的ランナー、証跡サブシステム、レポーターはどれもプラットフォームを名指しせず、プラットフォーム固有なのは UI を操作する backend だけです。

**やること**
- 自然言語シナリオ → 操作（tap / type / swipe / wait）→ 機械的検証
- 探索的な操作と、再現可能な回帰テストの両立
- 特定の動作のたびに、処理の証跡（スクリーンショット / 録画 / 要素ダンプ / ログ / ネットワーク）を取得
- 実行結果と証跡のレポート生成

**やらないこと（スコープ外と明示）**
- ユニット / 結合テスト（XCTest の領域。E2E に集中）
- アプリのビルド自体（既存 `xcodebuild` 成果物を前提に受け取る）

> **デバイスクラウド実行について（BE-0236）。** かつてはここに「実機 / クラウドデバイスファーム」をスコープ外として挙げていました。BE-0236 でこの姿勢を改め、run が駆動するデバイスの取得元を差し替えられる **device provider** の seam を導入しました。`targets.<name>.deviceProvider` の `kind` で provider を選び、既定の `local`（従来のローカル接続デバイス）はそのまま、`kind` を変えると別のデバイス（ネットワーク越しに予約したクラウドデバイスなど）を run に渡せます。seam は run/CI の判定経路の外にあり（provider の仕事はデバイスの取得と解放だけ）、決定的コア・ドライバ・ランナーは不変です。具象の provider（Firebase Device Streaming、AWS Device Farm など）は、決定的ゲートがクラウド SDK に依存しないよう、任意導入の別アダプタとして実装します。本項目は seam を出荷し、既定は `local` なので既存のターゲットの挙動は変わりません。

> **補足（なぜ最初に iOS Simulator を選んだか）。** 最初の backend に iOS Simulator を選んだのは、ヘッドレスで動き、`simctl` でスクリプト化でき、`simctl clone` で水平にスケールでき、実機やクラウドデバイスファームを必要としないため、決定性と CI 親和性を最も安く得られたからです。これは「Simulator 限定」という恒久的な制約ではなく、最初の足場の選択でした。決定的コアは backend 非依存なので、同じシナリオ形式とランナーのまま web（Playwright）と Android（adb）へ広がり、BE-0236 の device provider seam でデバイスの取得元まで差し替えられるようになりました。

### 1.1 dogfood 対象（最初のテスト対象）

Bajutsu の **dogfood 対象**は、リポジトリ同梱のショーケースアプリ `demos/showcase/`（`showcase-swiftui` なら `com.bajutsu.showcase.ios.swiftui`）です。あらゆるプリミティブを試せるよう計装してあります。§6 の例（タブを開く env フック、再取得、通信のアサーション）は、このショーケースに実在する UI / フックに対応します。BE-0079 で旧来の `demo` / `sample` / `sample2` を退役させ、これを唯一の iOS フィクスチャにしました。詳細は [`demos/showcase/SPEC.md`](demos/showcase/SPEC.md) を参照してください。

- **位置づけ**：同梱ショーケースは「現実的なテスト対象」であって Bajutsu のコア依存ではなく、汎用の E2E ツールとして他アプリにも使えます（iOS では XCUITest、web では Playwright を backend にします）
- **含意**：§7 の実装規約はまず同梱ショーケースに適用して検証します
- **汎用性**：同梱ショーケースは対象アプリの 1 つにすぎません。新しいアプリは `targets.<name>` の config エントリ（§8）と §7 適用（§7.1）で増やせ、ツール本体、ドライバ、実行系は不変です。Bajutsu は特定アプリに結合しません

---

## 2. 中核となる設計判断（不変の思想）

| 判断 | 方針 | 理由 |
|---|---|---|
| AI の役割 | 「毎回の実行者」ではなく **著者 + 失敗時の調査役** | LLM の非決定性、コスト、レイテンシを CI（継続的インテグレーション）ゲートに持ち込まない |
| 2 層構成 | Tier1 = AI ライブ操作（探索 / オーサリング）、Tier2 = 決定的ランナー（CI 回帰） | 探索の柔軟性と再現性を両取り |
| セレクタ | `accessibilityIdentifier`（非ローカライズ、一意、データ由来）優先、座標は最終手段 | レイアウト変更・翻訳・座標ズレ由来のフレーキーを除去 |
| 合否判定 | AI の主観でなく **機械チェック可能なアサーション**（要素存在 / 値一致） | レポートの信頼性。「成功した気がする」を排除 |
| 待機 | 固定 sleep 禁止。条件待機（`wait element` / `screenChanged`）のみ | タイミング起因フレーキーの除去 |
| 環境 | 各テスト前にクリーン化、状態は deeplink / launch args で注入 | 再現性。前テストの汚染を断つ |
| 証跡指示 | 自然言語の指示を **構造化ルールへ正規化** して保存 | 決定的に再実行でき、二度目以降は AI 不要 |

> **切り分け**：アクセシビリティ識別子で安定するのは「選択の決定性」のみです。
> タイミング / 状態 / ネットワーク起因のフレーキーは、環境側と待機側で別途対処します。

---

## 3. アーキテクチャ

```
自然言語シナリオ (markdown / YAML)
        │
        ▼
┌─ Orchestrator ────────────────────────┐
│  observe → act → verify ループ            │
│  ・シナリオ解釈                            │
│  ・ステップ実行とアサーション                │
│  ・証跡ルールの発火判定                      │
│  ・失敗時トリアージ                         │
└───────────────┬───────────────────────┘
                │ 抽象ドライバ API（tap/type/swipe/wait/query/screenshot）
   ┌────────────┴───────────────┐
   ▼                            ▼
XCUITest                   Playwright（web）
(iOS)
        │
        ▼
Environment Manager (simctl: erase/boot/launch/status_bar/io)
        │
        ├─ scenario の mocks（in-protocol の決定的スタブ）で応答を差し替え
        │      network collector が受信リクエストを network.json に記録（§3.2 / §9）
        ▼
Evidence/Trace subsystem (証跡の取得・相関・マスキング)
        │
        ▼
Reporter (manifest.json + JUnit/CTRF/HTML + スクショ/録画)
```

### 3.1 observe → act → verify と AI の関与境界

オーケストレータのループは同じ形ですが、**AI が関与する層としない層を厳密に分けます**。

| コマンド | 層 | AI | ループの中身 / 合否の決め方 |
|---|---|---|---|
| `record` | Tier1 | 著者 | observe(`query()` + screenshot) → AI が現在の画面から確定できる 1 手以上のバッチを提案（BE-0178）→ 順に act。画面が遷移したら残りを中断して再観測 → verify → **決定的ステップ + capturePolicy を書き出す**。AI の判断は記録時のみで、成果物は AI 非依存 |
| `run` | Tier2 | なし | 各ステップを act → wait(条件待機) → verify。**合否は `expect` の機械アサーションのみ**で決まる |
| `trace` / triage | — | 調査役 | 失敗証跡を AI が読み、原因要約・修正提案（人間レビュー前提・M4） |

> **要点**：「AI がステップ成功を判定する」ことはしません。`run` の合否は常に機械アサーションで決まります。AI は *書く*（record）か *調べる*（triage）だけで、*判定者* にはなりません。これが §2「AI を CI ゲートに持ち込まない」の実装上の具体化です。

### 3.2 ネットワーク制御（in-scenario の mocks）

決定的なネットワーク（§2「ネットワークをモックへ」）と、iOS backend の `network` 証跡源を、シナリオ内に宣言する **`mocks`（in-protocol の決定的スタブ）** で兼ねます。当初はアプリと外部ネットワークの間に立つ単一の外部モックサーバでこれを担う設計でしたが、外部サーバは [BE-0027](roadmaps/BE-0027-mock-server-external/BE-0027-mock-server-external-ja.md) で見送り、実装を in-protocol のスタブへ移しました。

- **居場所**：外部プロセスではなくプロトコル層のスタブです。アプリの発する各リクエストを、シナリオの `mocks` に宣言した `match` で照合し、一致すれば `respond` の定型応答をアプリ内で返します（§6.1）。ライブサーバに触れないため、テストはオフラインで安定します
- **2 つの役割**：(a) **決定性** = 一致したリクエストへ事前定義のスタブ応答を返します。(b) **証跡** = 発受信したリクエストを in-protocol の network collector が記録し、`network` アーティファクト（`network.json`）の取得元になります（§9）
- **証跡源**：XCUITest はネイティブのネットワーク監視を持たないため、`network` は **in-protocol の collector から取得**します。取得できないときは `manifest` に skip 理由を明示します
- **設定**：`mocks` はシナリオ単位に書きます（§6.1）。`bajutsu.config.yaml` の `mockServer`（起動コマンド / ポート）は当初の外部サーバ向けスキーマが残るのみで実装されておらず、`mocks` に置き換わっています（[BE-0027](roadmaps/BE-0027-mock-server-external/BE-0027-mock-server-external-ja.md)）

### 3.3 並列実行とアイソレーション

スループットは **Simulator を複数 boot して水平分割**することで稼ぎます。決定性は崩しません（各テストはクリーン環境と機械アサーションで完結し、状態を共有しないためです）。

- **アイソレーション単位 = ワーカー**：各ワーカーは固有の `udid`（`simctl clone` 等）と固有の `runs/<runId>` を持ちます。これにより成果物の混線を避けます。`mocks` は in-protocol でアプリ内に閉じるため（§3.2）、ワーカー間で共有ポートを取り合いません
- **ヘッドレス前提**：iOS Simulator はヘッドレスで動くため、N 台へ水平スケールできます（CI 向き）
- **CLI**：`bajutsu run ... --workers N` です。既定は 1 です。`--udid` 明示時は単一デバイスに固定します
- **不変条件**：並列でもシナリオ間で simulator / 索引 / モック状態を共有しません。共有が要る前提は `preconditions` で各テストが自前で構築します（§2）

---

## 4. 実装言語 = Python に伴う構成

Bajutsu は Python パッケージとして実装します。現行のパッケージ構造（`serve/`、`crawl`、`mcp/`、`drivers`、`orchestrator`、`runner` ほか、5 万行超）の正となる一覧は [`docs/ja/architecture.md`](docs/ja/architecture.md) にあります。ここで構造の全体像を凍結すると実装とすぐに乖離するため、本文では設計上の中核の継ぎ目だけを述べます。その継ぎ目とは `drivers/base.py` の Driver 抽象で、バックエンド差はここで吸収し、XCUITest / adb / Playwright といった実体をこのインターフェースの背後に収めます（§5）。

**技術選定**
- CLI：`Typer`（or Click）／設定とシナリオの検証：`pydantic`
- 外部ツール連携：`subprocess` で `xcrun simctl` / `xcodebuild` を呼び、常駐 XCUITest runner とは loopback HTTP で通信し、各々の出力をパース
- LLM：`anthropic` SDK（Claude）。シナリオと要素ダンプは prompt cache に載せてトークン削減
- 配布：`pipx install bajutsu` を想定

---

## 5. ドライバ抽象（バックエンド非依存）

バックエンドを差し替えられるよう、**共通インターフェースを最初に固定**し、能力差は抽象側で吸収します。現状の登録バックエンドは XCUITest（iOS）、adb（Android）、Playwright（web）です。XCUITest は iOS の唯一の backend です（[BE-0290](roadmaps/BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md) で idb を撤去。当初のヘッドレスな idb と対置する第一候補として入れたのが [BE-0019](roadmaps/BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) の経緯です）。adb は fast ゲートとエミュレータ上の e2e CI まで到達済みです（[BE-0208](roadmaps/BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)）。将来の追加に備えて、この抽象を維持します。

```python
# drivers/base.py（概念）
Point = tuple[float, float]               # x, y (points)

class Element(TypedDict):
    identifier: str | None
    label: str | None
    traits: list[str]
    value: str | None
    frame: tuple[float, float, float, float]  # x, y, w, h (points)

class Selector(TypedDict, total=False):
    # id / idMatches は単一値でもリストでもかまいません。リストは候補の OR で、要素の id が
    # いずれかに一致（または glob 一致）すればマッチします。1 つの共有シナリオが各プラット
    # フォームの id 表記を持てます（例 [stable.refresh, stable_refresh]。BE-0221）
    id: str | list[str]        # accessibilityIdentifier 完全一致（第一候補）
    idMatches: str | list[str] # glob パターン（複数マッチ前提。例 "*.submit"）
    label: str            # accessibilityLabel 完全一致（補助・曖昧解消のみ）
    labelMatches: str     # label の部分一致 / 正規表現
    traits: list[str]     # 型で絞る（例 ["button"]）
    value: str            # accessibility value 一致
    within: "Selector"    # 親要素でスコープ限定（行の中の Delete 等）
    index: int            # 複数マッチ時の n 番目（最終手段・フレーキー注意）

class Driver(Protocol):
    def query(self) -> list[Element]: ...          # 画面の要素ツリー
    def tap(self, sel: Selector) -> None: ...
    def long_press(self, sel: Selector, duration: float) -> None: ...
    def swipe(self, frm: Point, to: Point) -> None: ...
    def type_text(self, text: str) -> None: ...
    def wait_for(self, sel: Selector) -> bool: ...  # 単発チェック：現在の画面に一致するか（締め切りは共有 wait_until が担う）
    def screenshot(self, path: str) -> None: ...
    def capabilities(self) -> set[str]: ...        # 提供能力（network 等）。actuator/フォールバック解決用（§9）
```

**XCUITest の能力と抽象側の吸収方針**

| 機能 | XCUITest | 抽象側の扱い |
|---|---|---|
| 要素ツリー取得 | XCTest のオートメーションスナップショット（グループコンテナの内側まで降り、識別子 / label / traits / frame を得る） | 共通の `Element` へ正規化 |
| セレクタ解決 | id（アクセシビリティ識別子）を `query()` で引き当て → **identifier で直接 tap** | `query()` で対象を一意化してから identifier で tap |
| 条件待機 | ネイティブ対応 | run ループは全 backend 共通で `query()` のポーリングを使う（§9 で `conditionWait` は門にしない） |
| 実行形態 | 実機上に常駐する runner を loopback HTTP で駆動 | `backend` リストの可用性判定で対象を選ぶ（§9） |

> **要点**：セレクタは常に識別子（id）で書きます。XCUITest は `query()` で id を引き当ててから、その識別子で直接 tap します。座標を経由しないので選択も actuation も id で安定し、シナリオはバックエンド非依存に保たれます。

### セレクタ解決のセマンティクス（決定性の要）

`query()` で取得した要素ツリーに、Selector の各フィールドを **AND** で適用して候補を絞ります。

- **0 件** → 要素不在です。待機（`wait_until`）経由ならタイムアウト、即時アクションなら失敗です
- **1 件** → 解決成功です
- **2 件以上** → 既定で **ambiguous エラー**です。`within`（親スコープ）か `index` で一意化を要求します。「たまたま最初の一致を叩く」非決定性を構造的に排除します
- `idMatches` / `labelMatches` は **複数マッチ前提**です。capturePolicy のトリガー（§9 A）や `count` アサーション（§6.4）専用で、`tap` 等の単一アクションでは一意性を要求します

> ambiguity 検出はここに一元化します。抽象側は **常に `query()` で候補数を検証してから** actuation へ渡すので、「曖昧なら失敗」挙動はバックエンドに依らず一貫します。

### 操作の安定度順（stability ladder）

UI 操作は **最も安定する選択手段から順に試し、成立した手段を採用**します。採用結果は record で凍結し（§6.5）、`run` は凍結済みを決定的に再実行します。安定度で変わるのは **どの要素を選ぶか** です。actuation は backend によって異なり、adb は semantic tap を持たないため常に frame 中心への座標 tap ですが、XCUITest は解決した要素を identifier で直接 tap します（BE-0019）。下に行くほど壊れやすく、順 3 を使ったら manifest に degradation として明示します（§10）。

| 順 | 選択（どの要素） | 安定性 |
|---|---|---|
| 1 | `id` で一意解決 | 最安定（レイアウト / 翻訳 / 座標すべてに非依存） |
| 2 | `label` / `traits` で解決 | 選択がローカライズに弱い。id 無し要素のみ |
| 3 | `index` / 生座標 | レイアウト変化で壊れる。最終手段・manifest 必須 |

- **要素ツリーに現れない操作対象は `tapPoint`（順 3 の座標タップ）で叩く**：ID なしアプリのタブバーのタブのように、アクセシビリティツリーが addressable な要素として公開しない操作対象は、順 1〜2 では選べません。この場合 record のエージェントはスクリーンショットから位置を読み取り、正規化座標（0..1、左上原点）を `tapPoint` として凍結します。`run` は現在の画面サイズを掛けて `driver.tap_point` に再生します。セレクタで検証できない最下段なので、ツリーに載っている要素には使わず、必ず `id`/`label` で指します。
- **actuation は backend ごとに異なる**：adb は `query()` で id を引き当て、その要素の frame 中心を座標で叩きます。XCUITest は同じ解決結果を要素の identifier で直接 tap し、座標を経由しません（BE-0019）。選択（順 1〜3）はどの backend でも id で安定させます。
- **actuator はシナリオごとに、最も安く十分な backend**：actuator を複数持つプラットフォームでは、各シナリオを、そのステップが実際に必要とする能力を満たす最も安い backend で走らせます（BE-0240）。判定は `capability_preflight`（§9）が計算する `unsupported(scenario, 能力集合)` を各候補に対して再利用するだけで、新しい能力モデルは要りません。iOS は [BE-0290](roadmaps/BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md) で idb を撤去して以来、単一 actuator（XCUITest）なので、`pinch` / `rotate` の `multiTouch` を含むすべての構文をそのまま扱います。actuator はシナリオの実行のあいだ固定で、複数 backend が同一デバイスを同時に操作しないことで §3.3 の決定性を維持します（従来の「run 中は固定」という単位をシナリオ単位へ狭めたもので、規則自体は緩めません）。現状は XCUITest（iOS）、adb（Android）、Playwright（web）で、将来 backend を追加できるようリスト構造を残します
- **明示指定は固定**：`--backend <one>` で単一指定したらフォールバックも能力による昇格もしません（`--udid` と同様です、§3.3）

---

## 6. シナリオ仕様（自然言語 → 構造化）

自然言語シナリオは、オーケストレータが以下の構造化フォーマットへ正規化して保存します。`run`（Tier2）はこの構造を **AI 非依存** で実行します（§3.1）。

### 6.1 トップレベル構造

```yaml
- name: ログタブでフィルタを開いて適用する
  preconditions:
    erase: true                              # 各テスト前にクリーン化（既定 true・§2）
    launchEnv: { SHOWCASE_UITEST: "1" }       # 状態注入（launch args / env）
    deeplink: "showcaseswiftui://log"        # 起動後に開く（任意・scheme は §8）
    locale: "ja_JP"                          # 省略時は config の固定 locale
    setup: _setup.yaml                       # 再利用する前段（任意・§7.1）
  steps:
    - tap: { id: log.openFilter }
    - tap: { id: log.sheet.apply }
      capture: [screenshot.after, deviceLog]  # ステップ単体の証跡（§9 B / トークン文法は §9）
  expect:
    - exists: { id: log.sheet.title, negate: true }
```

`capturePolicy`（繰り返し発火する証跡ルール）と `redact` はシナリオ単位またはファイル単位で指定します（§9 A）。識別子、`launchEnv`、deeplink scheme、mock は **アプリ固有** で、§7（規約）と §8（`targets.<name>` config）が供給します。シナリオはアプリの名前空間で識別子を書き、ツール本体は不変です（§7.1）。

### 6.2 ステップ文法（アクション）

各ステップは 1 アクション + 任意の修飾子（`capture:` / ステップ名）です。アクションは §5 の Driver と env 操作に概ね 1:1 対応します。

| アクション | 形 | 備考 |
|---|---|---|
| `tap` | `tap: <Selector>` | 一意解決を要求（§5 解決セマンティクス） |
| `longPress` | `longPress: { sel: <Selector>, duration: <sec> }` | |
| `type` | `type: { text: "...", into?: <Selector>, submit?: <bool> }` | `into` 指定時は先にフォーカス、`submit` で改行 / 確定 |
| `clear` / `delete` / `select` / `copy` | `clear: { into: <Selector> }` ほか（BE-0265） | テキスト編集。`clear` は全消去、`delete` は末尾から `count` 文字、`select` は全選択、`copy` は選択内容をクリップボードへ（要 `select` 先行）。`select` / `copy` は web コンテキスト非対応で codegen 経由の XCUITest に誘導。`clear` / `delete` も web コンテキスト非対応 |
| `swipe` | `swipe: { on: <Selector>, direction: up\|down\|left\|right }` / `swipe: { from: <Point>, to: <Point> }` | セレクタ指定は frame 中心 → 座標へ解決 |
| `wait` | `wait: { ... }`（§6.3） | 固定 sleep の代替。唯一の待機手段 |
| `assert` | `assert: [ <Assertion>... ]` | ステップ途中の中間検証（DSL は §6.4 と同一） |

> `launch` / `deeplink` / `erase` は基本 `preconditions` で宣言しますが、シナリオ途中で再起動や再注入をしたい場合はステップとしても書けます（`relaunch: { env: {...} }`）。

### 6.3 待機（条件待機のみ）

```yaml
- wait: { for: { id: stable.row.1 }, timeout: 10 }        # 要素が現れるまで
- wait: { until: screenChanged, timeout: 5 }              # 画面遷移するまで
- wait: { until: { gone: { id: spinner } }, timeout: 15 } # 要素が消えるまで
- wait: { until: { request: { method: GET, url: "https://api/items", status: 200 } }, timeout: 8 }  # 指定エンドポイントへの通信が観測されるまで（§9 collector）
```

`timeout` は必須です（無限待ちは禁止です）。タイムアウトはステップ失敗として扱い、`result:error` 安全網（§9 A）が発火します。固定 sleep は文法として持ちません（§10）。

### 6.4 アサーション DSL（機械チェック）

`expect`（ステップ末尾の最終検証）と `assert`（中間検証）は同じ DSL です。リスト内は全て **AND** で、各アサーションは **直前の wait 完了後の `query()`** に対して評価します。1 つでも失敗ならステップ失敗です。

| アサーション | 意味 | 例 |
|---|---|---|
| `exists` | 一致要素が存在（`negate: true` で不在検証） | `exists: { id: stable.row.1 }` |
| `value` | accessibility value の一致 / 部分一致 | `value: { sel: { id: log.count.value }, equals: "3" }` |
| `label` | label の一致 / 部分一致 / 正規表現 | `label: { sel: { id: stable.status }, contains: "完了" }` |
| `count` | 一致要素数 | `count: { sel: { idMatches: "stable.row.*" }, equals: 5 }` |
| `enabled` / `disabled` | 操作可否（traits / isEnabled） | `disabled: { id: log.submit }` |
| `selected` | 選択 / トグル状態 | `selected: { id: horse.favorite }` |

> **ロケール依存に注意**：`label` / `value` の文字列比較や、可視テキストを直接見るアサーションはローカライズで壊れます。これらは config の **固定 locale**（§8）に紐付けて実行し、セレクタ自体は `id` で書きます（§7）。§6.1 例の `label` 比較も固定 locale が前提です。

### 6.5 正規化とラウンドトリップ（記録 → 編集 → 再実行）

構造化シナリオ（§6）+ capturePolicy（§9 A）が **唯一の永続物**です。AI は最初の 1 回だけ書き、以後は人間が所有します（プレーンな YAML で、PR でレビューできます）。

| 段階 | 主体 | 内容 |
|---|---|---|
| **record** | AI（著者） | 自然言語 → 構造化シナリオ + ルールへ正規化して保存。各ステップ / ルールの由来（元の自然言語）を `# from:` コメントか sidecar として残す（来歴） |
| **review / edit** | 人間 | YAML が真実。手で編集してよい。`bajutsu trace --explain` でルール発火回数を検証（§9） |
| **run** | なし | 決定的に実行（§3.1）。AI 非依存・再現可能 |
| **update**（M4） | AI（調査役） | UI 変更でシナリオが壊れたら、**全体再記録ではなく最小差分**を提案。人間がレビューして取り込む（手編集を保全） |

- **冪等な正規化**：同じ自然言語からは同じ構造が出る（決定的マッピング）ことを目標にし、再記録時の無用な差分（churn）を抑えます
- **AI 出力は常に「提案差分」**：record / update いずれも AI は YAML を黙って直接書き換えず、人間が承認する差分として出します。これによりコミット済み YAML が勝手に変わらないことを保証します（§2「テストを甘くしない」と整合します、§11）
- **バージョン管理**：シナリオはリポジトリ内のただのファイルです。履歴は git が持ち、Bajutsu は独自ストアを持ちません

---

## 7. 対象アプリへの実装規約（ツールに同梱するガイド）

ツールが安定動作するために、テスト対象アプリへ推奨する実装規約。

- 主要操作要素に `accessibilityIdentifier`（一意、非ローカライズ、データ由来の ID。命名規約は §7.3）
- `accessibilityLabel` / traits / value で状態を露出（検証用）
- 装飾は `.accessibilityHidden(true)`、複合行は `.accessibilityElement(children:)` でツリー整形
- 状態構築用の launch args / env フック（オンボーディング skip、シート直開きなど）
- テスト時はアニメーションを無効化し、ネットワークをモックへ
- 内部処理の証跡が必要なら `os_signpost` / `OSLog`（subsystem 指定）を仕込む

> `accessibilityLabel` はローカライズされ文言が変わりうるため **セレクタには使いません**。
> 安定セレクタは `accessibilityIdentifier` です。label は VoiceOver / AI の意味理解用に残します（役割が違います）。

### 7.1 新しいアプリのオンボーディング（per-target に必要なもの）

汎用化の単位は「アプリ」です。新しいアプリを対象にするとき変えるのは **ツールではなくアプリ側の準備 + config 1 エントリ** です。

1. **§7 規約を適用**：主要要素に `accessibilityIdentifier`（アプリ固有の名前空間で。例 `settings.*`）、状態を label / traits / value に露出、launch hook、anim off、ネットワークの mock 化
2. **config に `targets.<name>` を追加**（§8）：bundleId / deeplink scheme / 既定 `launchEnv` / `mockServer` / `redact`
3. **再利用セットアップを用意**（任意）：ログインやオンボーディングなど複数シナリオ共通の前段を `setup:` シナリオへ切り出す（§6.1 `preconditions.setup`）
4. **`bajutsu doctor --target <name>` で検証**：実行可能ゲートと §7 充足度スコアを出す（測り方は §7.2）
5. **シナリオを `scenarios/<name>/` に配置**：識別子はそのアプリの名前空間で書く

> 「様々なアプリ」は *ツールの分岐* ではなく *config とシナリオの追加* で増えます。ツール本体、ドライバ、実行系は不変で、各アプリの決定性は同じ §7 規約により担保されます（§2）。

### 7.2 §7 充足度の測り方（`doctor --target`）

`doctor --target <name>` は **AI 非依存で決定的**です（§3.1 の通り doctor は配線検証であって著者ではありません）。アプリを 1 回起動し、入口画面の `query()` を解析して **(1) 実行可能ゲート** と **(2) §7 充足度スコア** を出します。

**(1) ゲート（pass/fail。1 つでも ✗ なら実行不能）**

| チェック | 内容 |
|---|---|
| backend | iOS は `xcodebuild`（Xcode）の存在。XCUITest backend が駆動 |
| simctl / device | `xcrun simctl` 可用、`device` / `udid` が boot 可能 |
| app | `bundleId` がインストール済み・起動可能 |
| deeplink | `deeplinkScheme` が Info.plist に登録（openurl で確認） |
| mock | `mockServer` 設定時、起動 & port 到達可能 |
| config | `targets.<name>` が pydantic スキーマ妥当 |

**(2) §7 充足度スコア（`query()` 解析、決定的）**：操作可能要素（traits ∈ button / link / textField / searchField 等）を母数に測ります。

| 指標 | 定義 | ✓ しきい値 | 不足時の意味 |
|---|---|---|---|
| `idCoverage` | id を持つ操作可能要素の割合 | ≥ 0.9（warn 0.7–0.9 / fail < 0.7） | 低いほど座標フォールバックが増えフレーキーリスク（§5） |
| `namespaceConformance` | id が `idNamespaces`（§7.3）の接頭辞に一致する割合 | = 1.0 | 規約外 id・アプリ間 id 衝突の兆候 |
| `uniqueness` | 1 画面内の id 重複数 | = 0 | 重複は単一解決を ambiguous にする（§5）→ fail |

（しきい値は config で調整できます。`label` と一致する id や、状態を持つのに `value` 未設定の要素は **advisory 警告**としてスコア外で列挙します）

**判定（グレード）**：
- **Ready（緑）**：全ゲート pass + 3 指標すべて ✓
- **Partial（黄）**：実行はできるが指標が warn 域 → 該当要素を列挙（label / traits / frame）。座標フォールバックとフレーキーの予告
- **Blocked（赤）**：ゲート ✗ / `uniqueness` 違反 / `idCoverage` < fail

**出力**：人間向けサマリ + `--json`（CI ゲート用）です。不足要素は **実体を列挙**して「どこに id を足すか」を直に示します（§10「切り詰めた箇所を明示」と整合します）。

> **測定範囲の正直さ**：既定は **入口画面 + 宣言済み deeplink で直接開ける画面** のみです。全画面の網羅は `doctor --target <name> --from <runId>` で、`record` 実行が残した各画面の `elements` ダンプ（§9）を再利用して算出します。入口だけの測定はその旨を report に明示します。

### 7.3 識別子の命名規約と名前空間（`idNamespaces`）

`accessibilityIdentifier` は **`<namespace>.<element>` のドット区切り階層**です。すべて小文字で、各セグメントは `[a-z0-9-]`（複数語はハイフン）です。先頭セグメント = 名前空間で、`targets.<name>.idNamespaces`（§8）に宣言した集合のいずれかです。

```
settings.reindex            # <namespace=settings>.<element=reindex>
search.field
search.results-empty
result.row.<recordId>       # 動的行: 末尾は「データ由来の安定キー」
```

**3 つの不変条件**：

1. **一意（画面内）**：同一画面に同じ id を 2 つ置きません（§5 の ambiguous を構造で防ぎます。doctor `uniqueness`）。リスト行など繰り返し要素は **データ由来の安定キーを末尾に付けて一意化**します（`result.row.42`）。index 由来（`row.0`）は順序変化で壊れるため禁止です。集合操作は `idMatches: "result.row.*"` + `count`（§5 / §6.4）で行います
2. **非ローカライズかつデータ由来**：id に表示文言を使いません（翻訳で壊れます）。動的部分は表示テキストではなく **裏側の安定キー**（recordId 等）から作ります。doctor は id == label を advisory 警告にします（§7.2）
3. **名前空間で前置**：すべての id を宣言済み名前空間で始めます。名前空間 = 画面 / 機能のまとまり（`settings.*` / `search.*` / `result.*`）です。アプリ内の id を区画化し、grep、ルール（`idMatches`）、証跡相関を効かせます。doctor `namespaceConformance` が検査します

**アプリ間（横展開時）の扱い**：

各アプリは隔離実行されるため（§3.3）、**id がアプリ間で衝突しても実行時の曖昧さは生じません**（1 回の run では 1 アプリのツリーしか見ません）。横展開で効くのは別の軸です。

- **共有フローは予約名前空間に固定**：ログイン等の **再利用シナリオ / 共有コンポーネント**（§6.1 `setup:`）が複数アプリで動くには、その範囲の id が全アプリで **同一** である必要があります。`auth.*`（認証）や `nav.*`（共通ナビ）等を **予約名前空間**（§8 `defaults.reservedNamespaces`）として全アプリ共通の意味で使います
- **アプリ固有空間は自由**：それ以外（`settings.*` 等）はアプリローカルです。意味が衝突してもかまいません
- **doctor が契約を検査**：各アプリの `idNamespaces` が予約名前空間を含むこと（共有フローを使う場合）と、規約外接頭辞が無いことを確認します

> **要点**：名前空間は「衝突回避」より「**区画化と再利用**」の道具です。アプリ内では区画化（grep / ルール / 相関）、アプリ間では共有フローの **id 契約** として働きます。

---

## 8. CLI と設定（per-target / マルチアプリ）

ツール本体はアプリ非依存です。**アプリ固有の差分はすべて config に寄せ**、同じバイナリと同じドライバで複数アプリを回します。

プラットフォームがアプリプロセスの内側からしか公開しない機能は、アプリに組み込む一律のテスト支援 SDK を通じて実現します。この SDK はどのアプリにも同じものを組み込むので、config に寄せる per-app の差分ではありません。ドライバやランナーがアプリごとに変わらないというアプリ非依存の原則を保ったまま、アプリの協力が要る機能を扱えます。

iOS では BajutsuKit が `URLSession` の通信を捕捉してネットワークのアサーションを支え、Android では BajutsuAndroid がクリップボードを担います（[BE-0233](roadmaps/BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md)）。クリップボードは Android 10 以降、フォアグラウンドのアプリと既定の IME しか触れず、シェルからは操作できないので、アプリ内で処理するこの経路が唯一の決定的で保守可能な方法です。

SDK は test/debug ビルドに限って有効化し、リリースには載せません。この方式では、機能が実際に動くかどうかがアプリの協力に依存します。そのため対象アプリが SDK を組み込んでいない場合は、空の結果を成功と見なさず、明確なエラーで失敗させます。

```
bajutsu run --target <name> [--scenario file.yaml] [--backend ios] [--udid booted] [--workers N]   # 既定は config の scenarios ディレクトリ全体
bajutsu record --target <name> [--out file.yaml]   # AI で探索しつつ操作・証跡指示を記録（既定は config の scenarios へ自動命名）
bajutsu doctor --target <name>                   # 環境/権限/接続 + §7 規約の充足を検証
bajutsu trace <runDir>                        # 証跡ビューア（省略時は runs 以下の最新 run）
bajutsu trace --explain <scenario>            # 証跡ルールの発火回数を事前提示（ドライラン）
bajutsu codegen <scenario.yaml> --target <name> --emit xcuitest -o UITests/   # M3。--emit は xcuitest / playwright / uiautomator
```

### 設定の階層（チーム既定 × アプリ別）

`bajutsu.config.yaml` を 2 層（`defaults` × `targets`）で持ちます。値の解決順は **既定 < アプリ < シナリオ** で、右ほど優先します（テストに近いほうが勝ちます）。

```yaml
defaults:                       # 全アプリ共通の既定
  backend: [ios]                 # actuator=リストで最初に利用可能なもの（§5 / §9）。XCUITest（iOS）、adb（Android）、Playwright（web）が登録済み・将来の追加に備えたリスト構造。単一文字列も可
  device:  "iPhone 15"
  locale:  ja_JP                # 固定 locale（§6.4）
  capture: [screenshot.after, elements, actionLog]    # 軽量 3 点（§9）
  redact:  { headers: [Authorization, Cookie], fields: [token, password] }
  reservedNamespaces: [auth, nav]    # 共有フロー / コンポーネントの id 契約（§7.3）

targets:
  showcase-swiftui:             # ← --target showcase-swiftui で選択
    bundleId:       com.bajutsu.showcase.ios.swiftui
    deeplinkScheme: showcaseswiftui
    launchEnv:      { SHOWCASE_UITEST: "1" }             # このアプリ既定のフック（§7）
    idNamespaces:   [stable, horse, search, log]       # 識別子の名前空間（§7 命名規約）
    # 決定的ネットワークは scenario 単位の mocks で宣言する（§3.2 / §6.1）。外部 mockServer は BE-0027 で見送り
    setup:          ./scenarios/showcase/_setup.yaml    # 再利用する前段等（§6.1）
    redact:         { labels: ["カード番号"] }           # アプリ固有の追加（既定にマージ）
```

> アプリを増やすには `targets.<name>` を 1 つ足すだけです。ツールもドライバもシナリオ実行系も変えません。各アプリのシナリオは `scenarios/<name>/` に置き、識別子はそのアプリの名前空間で書きます（§7.1）。

---

## 9. 証跡（Evidence/Trace）サブシステム

「特定の動作のたびに証跡を取る」要求は、単発指示ではなく **繰り返し発火するルール** として設計します。

### 指示の 3 方法

| 方法 | 用途 | 例 |
|---|---|---|
| **A. ルール（トリガー方式）** ★中心 | 「特定の動作の **たびに**」自動取得 | 送信ボタン tap のたびにスクリーンショット + ネットワーク |
| **B. ステップ単体への付与** | この 1 ステップだけ取りたい | 特定の遷移後に要素ダンプ |
| **C. 既定ポリシー** | 全体の最低保証 | 失敗時は必ずスクリーンショット + 録画 + ログ |

自然言語の指示（例:「送信を押すたびにスクショとネットワークログを残して」）は、オーケストレータが **A のルール構造へ正規化** してシナリオに保存します。これにより指示は決定的に再実行でき、二度目以降は AI なしでも同じ証跡が取れます。

### A. ルール（トリガー方式）の構造

```yaml
capturePolicy:
  # 送信系ボタンを押すたびに、押下後のスクショ・要素・ネットワークを取得
  - on:      { action: tap, idMatches: "*.submit" }
    capture: [screenshot.after, elements, network]

  # 画面遷移のたびに前後スクショ
  - on:      { event: screenChanged }
    capture: [screenshot.around, elements]

  # どのステップでもエラー時は最大限の証跡（既定の安全網）
  - on:      { result: error }
    capture: [screenshot, video, deviceLog, elements, actionLog]

redact:                       # 保存前にマスクする対象（§9 注意点）
  labels:  ["パスワード", "カード番号"]
  headers: ["Authorization", "Cookie"]
  fields:  ["token", "password"]
```

### B. ステップ単体（インライン）

```yaml
- tap: { id: settings.reindex }
  capture: [screenshot.after, deviceLog]   # この操作のみ
```

### capture トークン文法と取得タイミング

`capture:` の各要素は `<種別>[.<修飾子>]` の形を取ります（インライン §9 B / ルール §9 A 共通）。

- **種別**：`screenshot` / `elements` / `actionLog` / `deviceLog` / `network` / `video` / `appTrace`（下表）
- **修飾子**：`before` / `after` / `around`（操作前に開始し後で停止）/ `onError`
- **既定の修飾子**：瞬時系（`screenshot` / `elements`）は `after`、区間系（`video` / `deviceLog` / `network` / `appTrace`）は `around`、`actionLog` は常時記録です
- 区間を持つ種別は `around` でライフサイクル管理し、停止はステップの wait 完了に同期させます（→ 後述「区間境界」）

### 証跡種別と取得元

| 種別 | 取得元（バックエンド別） | 区間 / 瞬時 | コスト |
|---|---|---|---|
| `screenshot` | `simctl io screenshot` | 瞬時 | 低 |
| `video` | `simctl io <udid> recordVideo`（start/stop） | 区間 | 高 |
| `elements`（a11y ツリー） | XCUITest のオートメーションスナップショット | 瞬時 | 低 |
| `actionLog` | オーケストレータ内部（操作・引数・結果・所要時間） | 瞬時 | 極低 |
| `deviceLog` | `simctl spawn <udid> log stream/collect`（subsystem/process で絞る） | 区間 | 中 |
| `network` | in-protocol の network collector（§3.2） | 区間 | 中 |
| `appTrace` | アプリの `os_signpost` / `OSLog`（subsystem 指定で log stream から収集） | 区間 | 中 |

> アプリ内部処理まで証跡化したい場合は `appTrace`（os_signpost 区間）を推奨します。UI 操作と内部処理を時刻で相関できます。

### 相関と出力レイアウト

すべての証跡は **runId / scenario / stepId / timestamp** でタグ付けし、`manifest.json` に集約します。ステップ ⇔ 成果物が辿れる形にします。

```
runs/<runId>/
├── manifest.json          # step → [artifacts] の相関、結果、所要時間
├── report.html / junit.xml / ctrf.json
└── <stepId>/
    ├── before.png / after.png
    ├── elements.json
    ├── segment.mp4
    ├── device.log
    └── network.json
```

`manifest.json` がレポートと CI（JUnit attachments）の単一の真実になります。

### バックエンド対応と能力差の吸収（actuator + フォールバック）

`backend` は **安定度順の順序付きリスト**です（§8 / §5 stability ladder）。`tap` / `type` / `swipe` / `wait` / `query`（操作と解決）は **actuator のみ** が行います。**actuator はシナリオごとに、最も安く十分な backend** です（BE-0240。選定規則の詳細は §5 stability ladder を参照）。まだシナリオが手元に無い場面（doctor、プールの起動時セットアップ、明示的な単一 actuator の固定）は、安定度順で最初に利用可能なものを選びます。actuator 以外は **能力を補うフォールバック**で、実体は read-only な証跡供給に限ります。`capabilities()`（§5）で各 backend の提供能力を引きます。

能力ごとの解決順：

| 能力 | 解決 |
|---|---|
| 操作 / 解決（tap 等） | **actuator のみ**（安定度順で最初に利用可能な backend）。利用可能な actuator が無ければ実行不能（hard error） |
| `screenshot` / `elements` | actuator が提供（§5 の `Element` 正規化に乗る） |
| `video` / `deviceLog` / `appTrace` | バックエンド非依存（`simctl`）。リストと無関係に取得 |
| `network` | **in-protocol の collector**（§3.2）から取得 → 無ければ skip（XCUITest はネイティブ監視を持たない） |

- **可用性を見る**：証跡源がこの環境で取得できない場合（例: network collector が無効）はスキップして次へ進みます。最終的に collector / simctl を見て、無ければ skip します
- **来歴を残す**：各アーティファクトが **どの provider から来たか** を `manifest` に記録します（例: `network: in-protocol collector（XCUITest はネイティブ監視なし）`）。未取得は capability フラグと共に skip 理由を明示します（§10）

### 注意点（証跡特有）

- **観測者効果**：録画とログストリームは僅かに時間挙動へ影響します。決定性検証では `around` 区間の長さを固定し、待機は条件待機のまま保ちます
- **コスト管理**：`video` を全操作に付けると重くなります。ルールはマッチ条件を絞り、既定は `screenshot` + `elements` + `actionLog` の軽量 3 点とし、`video` / `network` はオプトインにします
- **秘匿情報のマスキング**：スクリーンショット / ログ / ネットワークに PII やトークンが写り得ます。`redact:`（マスクするラベル / ヘッダ / フィールド）を保存前に適用します
- **過剰マッチ対策**：`bajutsu trace --explain <scenario>` で、どのルールが何回発火するかを事前提示します
- **区間境界**：`deviceLog` / `network` の区間取得は、ステップの wait 完了に同期させてフレーキー化を避けます

---

## 10. フレーキー対策（出荷基準チェックリスト）

- [ ] 固定 sleep ゼロ（すべて条件待機）
- [ ] セレクタはローカライズ文言に依存しない
- [ ] 各テストはクリーン環境から開始
- [ ] 合否はすべて機械チェック可能なアサーション
- [ ] 失敗時に必ずスクリーンショット + 要素ダンプを残す
- [ ] カバレッジを切り詰めた箇所（リトライ無し、証跡 skip 等）はログ / manifest に明示
- [ ] 座標 / index フォールバック（stability ladder 順 3〜4）を使ったステップは manifest に degradation として明示（§5）

---

## 11. リスクと未解決論点

- adb の要素ツリー取得の一時的な空（画面遷移中）
  - 画面遷移中、adb の `uiautomator dump` が一過性で「ほぼ空」のツリーを返すことがあります（画面は描画済みでも発生します）。単発の `expect`/`assert` や条件 `wait` がこの一瞬のスナップショットを掴むと誤失敗します。対策として、座標系 backend が継承する `CoordinateTreeDriver.query()` 基底クラス（BE-0254。[BE-0290](roadmaps/BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md) で idb を撤去して以来、adb 専用）に上限付きリトライを実装しています。これは、一度リッチなツリー（`_max_seen >= 2`）を観測済みで、かつ結果が `< 2` 要素のときのみ「未準備」とみなし、短いバックオフで再取得するものです（基点 0.05 秒を毎回倍加し 0.2 秒で頭打ち、5 回までの合計で最大 ~0.75s）。`_max_seen` ゲートにより、もともと疎な画面はマスクせず即返します。`_await_ready` の起動時しきい値（`>= 2`）と同一基準です。
- AI の自己修復が「テストを甘くする」方向に働くリスク → 人間レビューを挟む
- 証跡ルールの過剰マッチによる成果物肥大 → `--explain` ドライランと既定ポリシーの軽量化

---

## 12. MVP ロードマップ（段階）

| マイルストーン | 内容 |
|---|---|
| **M1** | `simctl.py`（simctl ラッパ）+ `drivers/base,idb`（共通 IF、id → frame 中心のセレクタ解決）+ YAML シナリオ(pydantic) + `assertions/` + 証跡の軽量 3 点（`screenshot`/`elements`/`actionLog`）と `result:error` 安全網 + `manifest.json` + per-target config（`targets.<name>` 解決, `bajutsu run --target` / `doctor --target`）。**idb でシナリオが通る** こと、**config だけで対象アプリを切り替えられる** ことを完了条件とする |
| **M2** | observe→act→verify の AI ループ（自然言語ステップ・証跡指示の解釈と正規化）+ ルール方式（トリガー）一式 + `video` / `deviceLog` + Reporter(JUnit/HTML) |
| **M3** | `network`（in-protocol の mocks / collector、§3.2）+ `appTrace`（os_signpost）+ redaction + XCUITest codegen（Tier2）+ CI 統合 |
| **M4** | 自己修復トリアージ（失敗証跡から原因要約・テスト更新提案、人間レビュー前提） |

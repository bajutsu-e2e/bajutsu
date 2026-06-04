# Simyoke — 自然言語駆動 iOS E2E テストツール 設計指針

> ステータス: 設計確定版 v1.7 / M1 実装中（決定的ロジック層は実装・テスト済み、バックエンド実行部は実機検証待ち）
> ツール名 `Simyoke` は仮称。unfydqry とは独立したツールとして開発する。

---

## 1. 目的・スコープ

自然言語で書かれたテストシナリオを受け取り、AI エージェントが iOS Simulator 上でアプリを操作して E2E テストを実行・検証するツール。

**やること**
- 自然言語シナリオ → 操作（tap / type / swipe / wait）→ 機械的検証
- 探索的な操作と、再現可能な回帰テストの両立
- 特定の動作のたびに、処理の証跡（スクショ / 録画 / 要素ダンプ / ログ / ネットワーク）を取得
- 実行結果と証跡のレポート生成

**やらないこと（スコープ外と明示）**
- 実機 / クラウドデバイスファーム（初期は Simulator 限定）
- ユニット / 結合テスト（XCTest の領域。E2E に集中）
- アプリのビルド自体（既存 `xcodebuild` 成果物を前提に受け取る）

### 1.1 unfydqry との関係（位置づけ）

Simyoke は unfydqry（隣接リポジトリ。Rust core + UniFFI のクロスプラットフォーム全文検索エンジン）とは **別ドメイン・別リポジトリ・別言語** の独立ツール。両者にコード依存はない。

- **関係**: unfydqry の iOS サンプル `ios/sample/SearchSample` が Simyoke の **最初の dogfood 対象**。§6 の例（`SEARCH_SHOW_SETTINGS` env フック、reindex、「正規化設定」）は SearchSample に実在する UI / フックに対応する（`ContentView.swift` の `SEARCH_SHOW_SETTINGS`、`SearchModel.swift` の reindex 状態）
- **なぜ独立か**: ドメイン（テスト自動化 vs 検索）・言語（Python vs Rust/Swift/Kotlin）・配布（pipx vs SwiftPM/Gradle）が異なる。SearchSample は「最初の現実的なテスト対象」であって Simyoke のコア依存ではなく、汎用 iOS E2E ツールとして他アプリにも使える
- **含意**: §7 の実装規約はまず SearchSample に適用して検証する。現状 SearchSample は `accessibilityIdentifier` 未付与のため、その付与が dogfood の前提条件（M1）
- **汎用性**: SearchSample は対象アプリの 1 つめにすぎない。新しいアプリは `apps.<name>` の config エントリ（§8）と §7 適用（§7.1）で増やせ、ツール本体・ドライバ・実行系は不変。Simyoke は特定アプリに結合しない

---

## 2. 中核となる設計判断（不変の思想）

| 判断 | 方針 | 理由 |
|---|---|---|
| AI の役割 | 「毎回の実行者」ではなく **著者 + 失敗時の調査役** | LLM の非決定性・コスト・レイテンシを CI ゲートに持ち込まない |
| 2 層構成 | Tier1 = AI ライブ操作（探索 / オーサリング）、Tier2 = 決定的ランナー（CI 回帰） | 探索の柔軟性と再現性を両取り |
| セレクタ | `accessibilityIdentifier`（非ローカライズ・一意・データ由来）優先、座標は最終手段 | レイアウト変更・翻訳・座標ズレ由来のフレーキーを除去 |
| 合否判定 | AI の主観でなく **機械チェック可能なアサーション**（要素存在 / 値一致） | レポートの信頼性。「成功した気がする」を排除 |
| 待機 | 固定 sleep 禁止。条件待機（`wait element` / `screenChanged`）のみ | タイミング起因フレーキーの除去 |
| 環境 | 各テスト前にクリーン化、状態は deeplink / launch args で注入 | 再現性。前テストの汚染を断つ |
| 証跡指示 | 自然言語の指示を **構造化ルールへ正規化** して保存 | 決定的に再実行でき、二度目以降は AI 不要 |

> **切り分け**: アクセシビリティ識別子で安定するのは「選択の決定性」のみ。
> タイミング / 状態 / ネットワーク起因のフレーキーは、環境・待機側で別途対処する。

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
   ┌────────────┼───────────────┐
   ▼            ▼               ▼
RocketSim    idb            (将来) XCUITest backend
(手元/ラベル)  (CI/ヘッドレス)   (決定的コード生成)
        │
        ▼
Environment Manager (simctl: erase/boot/launch/status_bar/io)
        │
        ├─ launch env で base URL 差し替え ─▶ Mock Server（任意）
        │                                      = 決定的応答 + network 証跡源
        ▼
Evidence/Trace subsystem (証跡の取得・相関・マスキング)
        │
        ▼
Reporter (manifest.json + JUnit/HTML + スクショ/録画)
```

### 3.1 observe → act → verify と AI の関与境界

オーケストレータのループは同じ形だが、**AI が関与する層としない層を厳密に分ける**。

| コマンド | 層 | AI | ループの中身 / 合否の決め方 |
|---|---|---|---|
| `record` | Tier1 | 著者 | observe(`query()` + screenshot) → AI が次の 1 手を提案 → act → verify → **決定的ステップ + capturePolicy を書き出す**。AI の判断は記録時のみで、成果物は AI 非依存 |
| `run` | Tier2 | なし | 各ステップを act → wait(条件待機) → verify。**合否は `expect` の機械アサーションのみ**で決まる |
| `trace` / triage | — | 調査役 | 失敗証跡を AI が読み、原因要約・修正提案（人間レビュー前提・M4） |

> **要点**: 「AI がステップ成功を判定する」ことはしない。`run` の合否は常に機械アサーション。AI は *書く*（record）か *調べる*（triage）だけで、*判定者* にはならない。これが §2「AI を CI ゲートに持ち込まない」の実装上の具体化。

### 3.2 ネットワーク制御（モックサーバ）

決定的なネットワーク（§2「ネットワークをモックへ」）と、idb バックエンドの `network` 証跡源を、**1 つのモックサーバ**で兼ねる。

- **居場所**: アーキ図の通り、アプリと外部ネットワークの間に立つ任意コンポーネント。アプリは launch env で base URL を差し替えて接続する（§2 の env 注入と同じ流儀で、アプリ側に特別な改造を要求しない）
- **2 つの役割**: (a) **決定性** = 事前定義したスタブ応答を返す、(b) **証跡** = 受けたリクエストをログ化し `network` アーティファクトの取得元になる（§9）
- **バックエンド差の吸収**: RocketSim はネイティブのネットワーク監視 *または* モックサーバを使える。idb はネイティブ監視を持たないため `network` は **モックサーバのログにフォールバック**。未接続時は `manifest` に skip 理由を明示
- **設定**: `simyoke.config.yaml` の `mockServer`（起動コマンド / ポート / スタブ定義への参照）。Simyoke 同梱の軽量サーバでも、外部サーバでもよい

### 3.3 並列実行とアイソレーション

スループットは **Simulator を複数 boot して水平分割**して稼ぐ。決定性は崩さない（各テストはクリーン環境・機械アサーションで完結し、状態を共有しないため）。

- **アイソレーション単位 = ワーカー**: 各ワーカーは固有の `udid`（`simctl clone` 等）、固有の `runs/<runId>`、固有のモックサーバ **ポート** を持つ。ポート衝突と成果物の混線を避ける
- **バックエンド依存**: idb はヘッドレスのため N 台へスケールできる（CI 向き）。RocketSim は GUI 常駐・前面アプリ前提のため実質的に低並列〜直列（手元向き、§11）
- **CLI**: `simyoke run ... --workers N`（または `--shard i/n`）。既定は 1。`--udid` 明示時は単一デバイス固定
- **不変条件**: 並列でもシナリオ間で simulator / 索引 / モック状態を共有しない。共有が要る前提は `preconditions` で各テストが自前で構築する（§2）

---

## 4. 実装言語 = Python に伴う構成

```
simyoke/
├── pyproject.toml            # CLI エントリ, 依存定義
├── DESIGN.md
├── simyoke/
│   ├── cli.py                # Typer ベースの CLI
│   ├── scenario.py           # YAML/markdown シナリオの読込・スキーマ検証 (pydantic)
│   ├── orchestrator.py       # observe → act → verify ループ
│   ├── agent.py              # LLM 呼び出し（anthropic SDK, Claude）。prompt cache 前提
│   ├── drivers/
│   │   ├── base.py           # Driver 抽象インターフェース（★ 両バックエンドの要）
│   │   ├── rocketsim.py      # rocketsim CLI を subprocess で呼ぶ
│   │   └── idb.py            # idb / fb-idb を subprocess で呼ぶ
│   ├── env.py                # simctl ラッパ（erase/boot/status_bar/launch/io）
│   ├── assertions.py         # 機械アサーション評価
│   ├── evidence.py           # 証跡の取得・相関・マスキング
│   └── report.py             # manifest.json / JUnit / HTML
└── tests/
```

**技術選定**
- CLI: `Typer`（or Click）／設定・シナリオ検証: `pydantic`
- 外部ツール連携: `subprocess` で `rocketsim` / `idb` / `xcrun simctl` を呼び、各々の JSON 出力（RocketSim は `rs/1` 形式）をパース
- LLM: `anthropic` SDK（Claude）。シナリオ・要素ダンプは prompt cache に載せてトークン削減
- 配布: `pipx install simyoke` を想定（idb 本体 = `fb-idb` も Python 製で親和性が高い）

---

## 5. ドライバ抽象（RocketSim + idb 両対応）

両バックエンドを初日から積むため、**共通インターフェースを最初に固定**し、能力差は抽象側で吸収する。

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
    id: str               # accessibilityIdentifier 完全一致（第一候補）
    idMatches: str        # glob パターン（複数マッチ前提。例 "*.submit"）
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
    def wait_for(self, sel: Selector, timeout: float) -> bool: ...
    def screenshot(self, path: str) -> None: ...
    def capabilities(self) -> set[str]: ...        # 提供能力（network 等）。actuator/フォールバック解決用（§9）
```

**能力差マトリクスと吸収方針**

| 機能 | RocketSim | idb | 抽象側の扱い |
|---|---|---|---|
| 要素ツリー取得 | `elements --agent`（識別子 / ラベル / frame） | `ui describe-all`（frame / label 中心） | 共通の `Element` へ正規化 |
| セレクタ解決 | ラベル / 識別子で **セマンティック tap** 可 | 基本 **座標 tap** | idb は `query()` で対象を引き当て → frame 中心を tap |
| 条件待機 | `wait element` / `screenChanged` | ネイティブ無し | idb は `query()` のポーリングで実装 |
| GUI 常駐 | 必要 | 不要（ヘッドレス可） | `backend` リストの可用性判定で対象を選ぶ（§9） |

> **要点**: セレクタは常に識別子 / ラベルで書く。RocketSim はそれをそのまま渡し、idb バックエンドは `query()` で識別子 → frame 中心に解決してから座標 tap する。これでシナリオはバックエンド非依存になり、手元 (RocketSim) → CI (idb) で同じシナリオが動く。

### セレクタ解決のセマンティクス（決定性の要）

`query()` で取得した要素ツリーに、Selector の各フィールドを **AND** で適用して候補を絞る。

- **0 件** → 要素不在。`wait_for` 経由ならタイムアウト、即時アクションなら失敗
- **1 件** → 解決成功
- **2 件以上** → 既定で **ambiguous エラー**。`within`（親スコープ）か `index` で一意化を要求する。「たまたま最初の一致を叩く」非決定性を構造的に排除する
- `idMatches` / `labelMatches` は **複数マッチ前提**。capturePolicy のトリガー（§9 A）や `count` アサーション（§6.4）専用で、`tap` 等の単一アクションでは一意性を要求する

> backend 差はここで吸収する。RocketSim がセマンティック tap を持っていても、抽象側は **常に `query()` で候補数を検証してから** 渡す。これで ambiguity 検出を一元化し、RocketSim / idb で同じ「曖昧なら失敗」挙動になる。

### 操作の安定度順（stability ladder）

UI 操作は **最も安定する手段から順に試し、成立した手段を採用**する。採用結果は record で凍結し（§6.5）、`run` は凍結済みを決定的に再実行する。下に行くほど壊れやすく、順 3 以降を使ったら manifest に degradation として明示する（§10）。

| 順 | 選択（どの要素） | 操作（どう叩く） | 安定性 |
|---|---|---|---|
| 1 | `id` で一意解決 | ネイティブ semantic tap（RocketSim） | 最安定（レイアウト / 翻訳 / 座標すべてに非依存） |
| 2 | `id` で一意解決 | frame 中心へ座標 tap（idb 等 semantic 非対応） | 選択は id で安定、actuation のみ座標 |
| 3 | `label` / `traits` で解決 | semantic / 座標 tap | 選択がローカライズに弱い。id 無し要素のみ |
| 4 | `index` / 生座標 | 座標 tap | レイアウト変化で壊れる。最終手段・manifest 必須 |

- **backend は安定度順に並べる**: `backend` リスト（§8）は **先頭ほど操作が安定する** 順で書く（RocketSim semantic tap > idb 座標 tap）。**actuator（操作を担う backend）= リストで最初に利用可能なもの**。run 開始時に 1 つ確定し run 中は固定（複数 backend が同一デバイスを操作しない＝§3.3 の決定性を維持）
- **環境で自動降格**: 手元は RocketSim（順 1）、ヘッドレス CI は idb（順 2）へ自動で落ちる。選択は常に `id` なのでシナリオは不変（§5 要点）
- **明示指定は固定**: `--backend <one>` で単一指定したらフォールバックしない（`--udid` と同様、§3.3）

---

## 6. シナリオ仕様（自然言語 → 構造化）

自然言語シナリオは、オーケストレータが以下の構造化フォーマットへ正規化して保存する。`run`（Tier2）はこの構造を **AI 非依存** で実行する（§3.1）。

### 6.1 トップレベル構造

```yaml
- name: 設定を開いて再生成する
  preconditions:
    erase: true                              # 各テスト前にクリーン化（既定 true・§2）
    launchEnv: { SEARCH_SHOW_SETTINGS: "1" }  # 状態注入（launch args / env）
    deeplink: "searchsample://settings"      # 起動後に開く（任意・scheme は §8）
    locale: "ja_JP"                          # 省略時は config の固定 locale
    setup: _setup.yaml                       # 再利用する前段（ログイン等・任意・§7.1）
  steps:
    - tap: { id: settings.open }
    - tap: { id: settings.reindex }
      capture: [screenshot.after, deviceLog]  # ステップ単体の証跡（§9 B / トークン文法は §9）
  expect:
    - exists: { label: "正規化設定が変更されています", negate: true }
```

`capturePolicy`（繰り返し発火する証跡ルール）と `redact` はシナリオ単位またはファイル単位で指定する（§9 A）。識別子・`launchEnv`・deeplink scheme・mock は **アプリ固有** で、§7（規約）と §8（`apps.<name>` config）が供給する。シナリオはアプリの名前空間で識別子を書き、ツール本体は不変（§7.1）。

### 6.2 ステップ文法（アクション）

各ステップは 1 アクション + 任意の修飾子（`capture:` / ステップ名）。アクションは §5 の Driver と env 操作に概ね 1:1 対応する。

| アクション | 形 | 備考 |
|---|---|---|
| `tap` | `tap: <Selector>` | 一意解決を要求（§5 解決セマンティクス） |
| `longPress` | `longPress: { sel: <Selector>, duration: <sec> }` | |
| `type` | `type: { text: "...", into?: <Selector>, submit?: <bool> }` | `into` 指定時は先にフォーカス、`submit` で改行 / 確定 |
| `swipe` | `swipe: { on: <Selector>, direction: up\|down\|left\|right }` / `swipe: { from: <Point>, to: <Point> }` | セレクタ指定は frame 中心 → 座標へ解決 |
| `wait` | `wait: { ... }`（§6.3） | 固定 sleep の代替。唯一の待機手段 |
| `assert` | `assert: [ <Assertion>... ]` | ステップ途中の中間検証（DSL は §6.4 と同一） |

> `launch` / `deeplink` / `erase` は基本 `preconditions` で宣言するが、シナリオ途中で再起動・再注入したい場合はステップとしても書ける（`relaunch: { env: {...} }`）。

### 6.3 待機（条件待機のみ）

```yaml
- wait: { for: { id: home.title }, timeout: 10 }          # 要素が現れるまで
- wait: { until: screenChanged, timeout: 5 }              # 画面遷移するまで
- wait: { until: { gone: { id: spinner } }, timeout: 15 } # 要素が消えるまで
```

`timeout` は必須（無限待ち禁止）。タイムアウトはステップ失敗として扱い、`result:error` 安全網（§9 A）が発火する。固定 sleep は文法として持たない（§10）。

### 6.4 アサーション DSL（機械チェック）

`expect`（ステップ末尾の最終検証）と `assert`（中間検証）は同じ DSL。リスト内は全て **AND**、各アサーションは **直前の wait 完了後の `query()`** に対して評価する。1 つでも失敗ならステップ失敗。

| アサーション | 意味 | 例 |
|---|---|---|
| `exists` | 一致要素が存在（`negate: true` で不在検証） | `exists: { id: home.title }` |
| `value` | accessibility value の一致 / 部分一致 | `value: { sel: { id: counter }, equals: "3" }` |
| `label` | label の一致 / 部分一致 / 正規表現 | `label: { sel: { id: status }, contains: "完了" }` |
| `count` | 一致要素数 | `count: { sel: { idMatches: "row.*" }, equals: 5 }` |
| `enabled` / `disabled` | 操作可否（traits / isEnabled） | `disabled: { id: submit }` |
| `selected` | 選択 / トグル状態 | `selected: { id: tab.home }` |

> **ロケール依存に注意**: `label` / `value` の文字列比較や、可視テキストを直接見るアサーションはローカライズで壊れる。これらは config の **固定 locale**（§8）に紐付けて実行し、セレクタ自体は `id` で書く（§7）。§6.1 例の `label` 比較も固定 locale が前提。

### 6.5 正規化とラウンドトリップ（記録 → 編集 → 再実行）

構造化シナリオ（§6）+ capturePolicy（§9 A）が **唯一の永続物**。AI は最初の 1 回だけ書き、以後は人間が所有する（プレーンな YAML、PR でレビュー可能）。

| 段階 | 主体 | 内容 |
|---|---|---|
| **record** | AI（著者） | 自然言語 → 構造化シナリオ + ルールへ正規化して保存。各ステップ / ルールの由来（元の自然言語）を `# from:` コメントか sidecar として残す（来歴） |
| **review / edit** | 人間 | YAML が真実。手で編集してよい。`simyoke trace --explain` でルール発火回数を検証（§9） |
| **run** | なし | 決定的に実行（§3.1）。AI 非依存・再現可能 |
| **update**（M4） | AI（調査役） | UI 変更でシナリオが壊れたら、**全体再記録ではなく最小差分**を提案。人間がレビューして取り込む（手編集を保全） |

- **冪等な正規化**: 同じ自然言語からは同じ構造が出る（決定的マッピング）ことを目標にし、再記録時の無用な差分（churn）を抑える
- **AI 出力は常に「提案差分」**: record / update いずれも AI は YAML を黙って直接書き換えず、人間が承認する差分として出す。コミット済み YAML が勝手に変わらないことを保証（§2「テストを甘くしない」と整合、§11）
- **バージョン管理**: シナリオはリポジトリ内のただのファイル。履歴は git が持ち、Simyoke は独自ストアを持たない

---

## 7. 対象アプリへの実装規約（ツールに同梱するガイド）

ツールが安定動作するために、テスト対象アプリへ推奨する実装規約。

- 主要操作要素に `accessibilityIdentifier`（一意・非ローカライズ・データ由来 ID。命名規約は §7.3）
- `accessibilityLabel` / traits / value で状態を露出（検証用）
- 装飾は `.accessibilityHidden(true)`、複合行は `.accessibilityElement(children:)` でツリー整形
- 状態構築用の launch args / env フック（オンボーディング skip、シート直開きなど）
- テスト時はアニメーション無効化・ネットワークをモックへ
- 内部処理の証跡が必要なら `os_signpost` / `OSLog`（subsystem 指定）を仕込む

> `accessibilityLabel` はローカライズされ文言が変わりうるため **セレクタには使わない**。
> 安定セレクタは `accessibilityIdentifier`。label は VoiceOver / AI の意味理解用に残す（役割が違う）。

### 7.1 新しいアプリのオンボーディング（per-app に必要なもの）

汎用化の単位は「アプリ」。新しいアプリを対象にするとき変えるのは **ツールではなくアプリ側の準備 + config 1 エントリ**。

1. **§7 規約を適用** — 主要要素に `accessibilityIdentifier`（アプリ固有の名前空間で。例 `settings.*`）、状態を label / traits / value に露出、launch hook、anim off、ネットワークの mock 化
2. **config に `apps.<name>` を追加**（§8）— bundleId / deeplink scheme / 既定 `launchEnv` / `mockServer` / `redact`
3. **再利用セットアップを用意**（任意）— ログイン・オンボーディングなど複数シナリオ共通の前段を `setup:` シナリオへ切り出す（§6.1 `preconditions.setup`）
4. **`simyoke doctor --app <name>` で検証** — 実行可能ゲート + §7 充足度スコアを出す（測り方は §7.2）
5. **シナリオを `scenarios/<name>/` に配置** — 識別子はそのアプリの名前空間で書く

> 「様々なアプリ」は *ツールの分岐* ではなく *config とシナリオの追加* で増える。ツール本体・ドライバ・実行系は不変で、各アプリの決定性は同じ §7 規約により担保される（§2）。

### 7.2 §7 充足度の測り方（`doctor --app`）

`doctor --app <name>` は **AI 非依存・決定的**（§3.1 の通り doctor は配線検証であって著者ではない）。アプリを 1 回起動し、入口画面の `query()` を解析して **(1) 実行可能ゲート** と **(2) §7 充足度スコア** を出す。

**(1) ゲート（pass/fail・1 つでも ✗ なら実行不能）**

| チェック | 内容 |
|---|---|
| backend | rocketsim / idb_companion の存在・バージョン |
| simctl / device | `xcrun simctl` 可用、`device` / `udid` が boot 可能 |
| app | `bundleId` がインストール済み・起動可能 |
| deeplink | `deeplinkScheme` が Info.plist に登録（openurl で確認） |
| mock | `mockServer` 設定時、起動 & port 到達可能 |
| config | `apps.<name>` が pydantic スキーマ妥当 |

**(2) §7 充足度スコア（`query()` 解析・決定的）** — 操作可能要素（traits ∈ button / link / textField / searchField 等）を母数に測る:

| 指標 | 定義 | ✓ しきい値 | 不足時の意味 |
|---|---|---|---|
| `idCoverage` | id を持つ操作可能要素の割合 | ≥ 0.9（warn 0.7–0.9 / fail < 0.7） | 低いほど座標フォールバックが増えフレーキーリスク（§5） |
| `namespaceConformance` | id が `idNamespaces`（§7.3）の接頭辞に一致する割合 | = 1.0 | 規約外 id・アプリ間 id 衝突の兆候 |
| `uniqueness` | 1 画面内の id 重複数 | = 0 | 重複は単一解決を ambiguous にする（§5）→ fail |

（しきい値は config で調整可。`label` と一致する id や、状態を持つのに `value` 未設定の要素は **advisory 警告**としてスコア外で列挙する）

**判定（グレード）**:
- **Ready（緑）**: 全ゲート pass + 3 指標すべて ✓
- **Partial（黄）**: 実行はできるが指標が warn 域 → 該当要素を列挙（label / traits / frame）。座標フォールバック・フレーキーの予告
- **Blocked（赤）**: ゲート ✗ / `uniqueness` 違反 / `idCoverage` < fail

**出力**: 人間向けサマリ + `--json`（CI ゲート用）。不足要素は **実体を列挙**して「どこに id を足すか」を直に示す（§10「切り詰めた箇所を明示」と整合）。

> **測定範囲の正直さ**: 既定は **入口画面 + 宣言済み deeplink で直接開ける画面** のみ。全画面の網羅は `doctor --app <name> --from <runId>` で、`record` 実行が残した各画面の `elements` ダンプ（§9）を再利用して算出する。入口だけの測定はその旨を report に明示する。

### 7.3 識別子の命名規約と名前空間（`idNamespaces`）

`accessibilityIdentifier` は **`<namespace>.<element>` のドット区切り階層**。すべて小文字、各セグメントは `[a-z0-9-]`（複数語はハイフン）。先頭セグメント = 名前空間で、`apps.<name>.idNamespaces`（§8）に宣言した集合のいずれか。

```
settings.reindex            # <namespace=settings>.<element=reindex>
search.field
search.results-empty
result.row.<recordId>       # 動的行: 末尾は「データ由来の安定キー」
```

**3 つの不変条件**:

1. **一意（画面内）** — 同一画面に同じ id を 2 つ置かない（§5 の ambiguous を構造で防ぐ。doctor `uniqueness`）。リスト行など繰り返し要素は **データ由来の安定キーを末尾に付けて一意化**（`result.row.42`）。index 由来（`row.0`）は順序変化で壊れるため禁止。集合操作は `idMatches: "result.row.*"` + `count`（§5 / §6.4）で行う
2. **非ローカライズ・データ由来** — id に表示文言を使わない（翻訳で壊れる）。動的部分は表示テキストではなく **裏側の安定キー**（recordId 等）から作る。doctor は id == label を advisory 警告（§7.2）
3. **名前空間で前置** — すべての id を宣言済み名前空間で始める。名前空間 = 画面 / 機能のまとまり（`settings.*` / `search.*` / `result.*`）。アプリ内の id を区画化し、grep・ルール（`idMatches`）・証跡相関を効かせる。doctor `namespaceConformance` が検査

**アプリ間（横展開時）の扱い**:

各アプリは隔離実行されるため（§3.3）、**id がアプリ間で衝突しても実行時の曖昧さは生じない**（1 回の run では 1 アプリのツリーしか見ない）。横展開で効くのは別の軸:

- **共有フローは予約名前空間に固定** — ログイン等の **再利用シナリオ / 共有コンポーネント**（§6.1 `setup:`）が複数アプリで動くには、その範囲の id が全アプリで **同一** である必要がある。`auth.*`（認証）/ `nav.*`（共通ナビ）等を **予約名前空間**（§8 `defaults.reservedNamespaces`）として全アプリ共通の意味で使う
- **アプリ固有空間は自由** — それ以外（`settings.*` 等）はアプリローカル。意味が衝突してもよい
- **doctor が契約を検査** — 各アプリの `idNamespaces` が予約名前空間を含むこと（共有フローを使う場合）と、規約外接頭辞が無いことを確認する

> **要点**: 名前空間は「衝突回避」より「**区画化と再利用**」の道具。アプリ内では区画化（grep / ルール / 相関）、アプリ間では共有フローの **id 契約** として働く。

---

## 8. CLI と設定（per-app / マルチアプリ）

ツール本体はアプリ非依存。**アプリ固有の差分はすべて config に寄せ**、同じバイナリ・同じドライバで複数アプリを回す。

```
simyoke run <scenario.yaml> --app <name> [--backend rocketsim[,idb]] [--udid booted] [--workers N]
simyoke record <scenario.yaml> --app <name>   # AI で探索しつつ操作・証跡指示を記録
simyoke doctor --app <name>                   # 環境/権限/接続 + §7 規約の充足を検証
simyoke trace open <runId>                    # 証跡ビューア
simyoke trace --explain <scenario>            # 証跡ルールの発火回数を事前提示（ドライラン）
simyoke codegen <result.json> --emit xcuitest -o UITests/   # M3
```

### 設定の階層（チーム既定 × アプリ別）

`simyoke.config.yaml` を 2 層（`defaults` × `apps`）で持つ。値の解決順は **既定 < アプリ < シナリオ**（右ほど優先 = テストに近いほうが勝つ）。

```yaml
defaults:                       # 全アプリ共通の既定
  backend: [rocketsim, idb]      # UI 操作の安定度順（先頭=最安定）。actuator=最初に利用可能なもの、以降=能力フォールバック（§5 / §9）。単一文字列も可
  device:  "iPhone 15"
  locale:  ja_JP                # 固定 locale（§6.4）
  capture: [screenshot.after, elements, actionLog]    # 軽量 3 点（§9）
  redact:  { headers: [Authorization, Cookie], fields: [token, password] }
  reservedNamespaces: [auth, nav]    # 共有フロー / コンポーネントの id 契約（§7.3）

apps:
  searchsample:                 # ← --app searchsample で選択
    bundleId:       com.example.SearchSample
    deeplinkScheme: searchsample
    launchEnv:      { SEARCH_SHOW_SETTINGS: "1" }      # このアプリ既定のフック（§7）
    idNamespaces:   [settings, search, result]         # 識別子の名前空間（§7 命名規約）
    mockServer:     { cmd: "...", port: 8080, stubs: ./mocks/searchsample }   # §3.2
    setup:          ./scenarios/searchsample/_setup.yaml   # 再利用するログイン等（§6.1）
    redact:         { labels: ["カード番号"] }           # アプリ固有の追加（既定にマージ）
```

> アプリを増やす = `apps.<name>` を 1 つ足すだけ。ツールもドライバもシナリオ実行系も変えない。各アプリのシナリオは `scenarios/<name>/`、識別子はそのアプリの名前空間で書く（§7.1）。

---

## 9. 証跡（Evidence/Trace）サブシステム

「特定の動作のたびに証跡を取る」要求は、単発指示ではなく **繰り返し発火するルール** として設計する。

### 指示の 3 方法

| 方法 | 用途 | 例 |
|---|---|---|
| **A. ルール（トリガー方式）** ★中心 | 「特定の動作の **たびに**」自動取得 | 送信ボタン tap のたびにスクショ + ネットワーク |
| **B. ステップ単体への付与** | この 1 ステップだけ取りたい | 特定の遷移後に要素ダンプ |
| **C. 既定ポリシー** | 全体の最低保証 | 失敗時は必ずスクショ + 録画 + ログ |

自然言語の指示（例:「送信を押すたびにスクショとネットワークログを残して」）は、オーケストレータが **A のルール構造へ正規化** してシナリオに保存する。これにより指示は決定的に再実行でき、二度目以降は AI なしでも同じ証跡が取れる。

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

`capture:` の各要素は `<種別>[.<修飾子>]` の形を取る（インライン §9 B / ルール §9 A 共通）。

- **種別**: `screenshot` / `elements` / `actionLog` / `deviceLog` / `network` / `video` / `appTrace`（下表）
- **修飾子**: `before` / `after` / `around`（操作前に開始し後で停止）/ `onError`
- **既定の修飾子**: 瞬時系（`screenshot` / `elements`）は `after`、区間系（`video` / `deviceLog` / `network` / `appTrace`）は `around`、`actionLog` は常時記録
- 区間を持つ種別は `around` でライフサイクル管理し、停止はステップの wait 完了に同期させる（→ 後述「区間境界」）

### 証跡種別と取得元

| 種別 | 取得元（バックエンド別） | 区間 / 瞬時 | コスト |
|---|---|---|---|
| `screenshot` | RocketSim `screenshot` / idb / `simctl io screenshot` | 瞬時 | 低 |
| `video` | `simctl io <udid> recordVideo`（start/stop） | 区間 | 高 |
| `elements`（a11y ツリー） | RocketSim `elements --agent` / idb `ui describe-all` | 瞬時 | 低 |
| `actionLog` | オーケストレータ内部（操作・引数・結果・所要時間） | 瞬時 | 極低 |
| `deviceLog` | `simctl spawn <udid> log stream/collect`（subsystem/process で絞る） | 区間 | 中 |
| `network` | RocketSim ネットワーク監視のエクスポート / モックサーバのリクエストログ | 区間 | 中 |
| `appTrace` | アプリの `os_signpost` / `OSLog`（subsystem 指定で log stream から収集） | 区間 | 中 |

> アプリ内部処理まで証跡化したい場合は `appTrace`（os_signpost 区間）を推奨。UI 操作と内部処理を時刻で相関できる。

### 相関と出力レイアウト

すべての証跡は **runId / scenario / stepId / timestamp** でタグ付けし、`manifest.json` に集約。ステップ ⇔ 成果物が辿れる形にする。

```
runs/<runId>/
├── manifest.json          # step → [artifacts] の相関、結果、所要時間
├── report.html / junit.xml
└── <stepId>/
    ├── before.png / after.png
    ├── elements.json
    ├── segment.mp4
    ├── device.log
    └── network.json
```

`manifest.json` がレポートと CI（JUnit attachments）の単一の真実になる。

### バックエンド対応と能力差の吸収（actuator + フォールバック）

`backend` は **安定度順の順序付きリスト**（§8 / §5 stability ladder）。`tap` / `type` / `swipe` / `wait` / `query`（操作と解決）は **actuator のみ** が行う。**actuator = リストで最初に利用可能な backend**（run 開始時に確定し run 中固定。2 ドライバが同一デバイスを操作する非決定性を避ける、§3.3）。actuator 以外は **能力を補うフォールバック**で、実体は read-only な証跡供給に限る。`capabilities()`（§5）で各 backend の提供能力を引く。

能力ごとの解決順:

| 能力 | 解決 |
|---|---|
| 操作 / 解決（tap 等） | **actuator のみ**（安定度順で最初に利用可能な backend）。利用可能な actuator が無ければ実行不能（hard error） |
| `screenshot` / `elements` | 両バックエンドが提供 → actuator を使う（§5 の `Element` 正規化に乗る） |
| `video` / `deviceLog` / `appTrace` | バックエンド非依存（`simctl`）。リストと無関係に取得 |
| `network` | リスト順に **利用可能で提供できる** backend を探す（RocketSim はネイティブ監視）→ 無ければ **モックサーバ**（§3.2）→ それも無ければ skip |

- **可用性を見る**: フォールバック先がこの環境で起動できない場合（例: ヘッドレス CI に GUI 常駐の RocketSim は無い）はスキップして次へ。最終的に mock / simctl、無ければ skip
- **来歴を残す**: 各アーティファクトが **どの provider から来たか** を `manifest` に記録（例: `network: mockServer（idb はネイティブ監視なし）`）。未取得は capability フラグと共に skip 理由を明示（§10）

### 注意点（証跡特有）

- **観測者効果**: 録画・ログストリームは僅かに時間挙動へ影響する。決定性検証では `around` 区間の長さを固定し、待機は条件待機のまま保つ
- **コスト管理**: `video` を全操作に付けると重い。ルールはマッチ条件を絞り、既定は `screenshot` + `elements` + `actionLog` の軽量 3 点、`video` / `network` はオプトイン
- **秘匿情報のマスキング**: スクショ / ログ / ネットワークに PII・トークンが写り得る。`redact:`（マスクするラベル / ヘッダ / フィールド）を保存前に適用する
- **過剰マッチ対策**: `simyoke trace --explain <scenario>` で、どのルールが何回発火するかを事前提示
- **区間境界**: `deviceLog` / `network` の区間取得は、ステップの wait 完了に同期させてフレーキー化を避ける

---

## 10. フレーキー対策（出荷基準チェックリスト）

- [ ] 固定 sleep ゼロ（すべて条件待機）
- [ ] セレクタはローカライズ文言に依存しない
- [ ] 各テストはクリーン環境から開始
- [ ] 合否はすべて機械チェック可能なアサーション
- [ ] 失敗時に必ずスクショ + 要素ダンプを残す
- [ ] カバレッジを切り詰めた箇所（リトライ無し、証跡 skip 等）はログ / manifest に明示
- [ ] 座標 / index フォールバック（stability ladder 順 3〜4）を使ったステップは manifest に degradation として明示（§5）

---

## 11. リスク・未解決論点

- RocketSim は GUI アプリ常駐前提 → 完全ヘッドレス CI では idb / XCUITest へ寄せる（両対応の動機そのもの）
- 両バックエンドの要素ツリー差異の正規化精度（特に `.searchable` 等の SwiftUI 標準要素）
- idb 自体のメンテ頻度・最新ランタイム互換 → `idb_companion` バージョンを監視
- AI の自己修復が「テストを甘くする」方向に働くリスク → 人間レビューを挟む
- 証跡ルールの過剰マッチによる成果物肥大 → `--explain` ドライランと既定ポリシーの軽量化

---

## 12. MVP ロードマップ（段階）

| マイルストーン | 内容 |
|---|---|
| **M1** | `env.py`（simctl）+ `drivers/base,rocketsim,idb`（共通 IF、idb 側セレクタ解決）+ YAML シナリオ(pydantic) + `assertions.py` + 証跡の軽量 3 点（`screenshot`/`elements`/`actionLog`）と `result:error` 安全網 + `manifest.json` + per-app config（`apps.<name>` 解決, `simyoke run --app` / `doctor --app`）。**同一シナリオが RocketSim / idb 両方で通る** こと、**config だけで対象アプリを切り替えられる** ことを完了条件とする |
| **M2** | observe→act→verify の AI ループ（自然言語ステップ・証跡指示の解釈と正規化）+ ルール方式（トリガー）一式 + `video` / `deviceLog` + Reporter(JUnit/HTML) |
| **M3** | `network`（RocketSim / モック）+ `appTrace`（os_signpost）+ redaction + XCUITest codegen（Tier2）+ CI 統合 |
| **M4** | 自己修復トリアージ（失敗証跡から原因要約・テスト更新提案、人間レビュー前提） |

[English](../recording.md) · **日本語**

# AI オーサリング（record / Tier 1）

> Tier 1 = AI ライブ操作です。自然言語のゴールから AI がアプリを探索しながら操作し、**決定的シナリオ**
> を書き出します。AI が関与するのはここ（記録時）だけです。生成された YAML は AI 非依存で、以後はユーザーが管理します。
>
> 実装: `bajutsu/record.py`（ループ）、`bajutsu/agent.py` + `bajutsu/agents.py`（抽象 + 構築）、
> `bajutsu/claude_agent.py`（SDK オーサリングエージェント）、`bajutsu/alerts.py`（システムアラート対処）。
> 幅優先の探索 `bajutsu/crawl.py` も同じエージェントを使います。

関連: [concepts の 2 層](concepts.md#2-2-層構成tier-1--tier-2) ・ [scenarios](scenarios.md) ・ [run-loop](run-loop.md)

---

## Agent 抽象

ループとモデルを分離するための薄い Protocol です（`agent.py`）。テストではスクリプト化した fake を使い、
本番では SDK 経由の `ClaudeAgent` を使います（下記）。

```python
@dataclass
class Observation:
    goal: str                       # 自然言語ゴール
    screen: list[Element]           # 現在画面の要素
    history: list[Step]             # ここまでに記録したステップ
    screenshot: bytes | None        # 現在画面の PNG（視覚用）

@dataclass
class Proposal:
    step: Step | None = None        # 次の 1 手（None なら done か行き詰まり）
    done: bool = False              # ゴール到達
    expect: list[Assertion] = []    # done 時に、ゴールを検証するアサーション
    note: str = ""
    needs_human: bool = False       # 第三の結果。人に引き渡す（BE-0179）
    human_prompt: str = ""          # 引き渡しの理由。人に提示する

class Agent(Protocol):
    def next_action(self, observation: Observation) -> Proposal: ...
```

## record ループ

`record(driver, goal, agent, *, name, max_steps=30, alert_guard=None, ...) -> Scenario`
（`record.py`）。observe → 提案 → 実行 を `max_steps` まで繰り返します。

```
1. （alert_guard があれば）アプリを覆うもの（システムアラート等）を片付ける
2. elements = driver.query()
   - alert_guard 下で id を持つ要素が皆無なら、エージェントに死んだ画面を見せず再ループ
     （id を幻覚させないため）
3. screenshot を撮り、Observation を作って agent.next_action() を呼ぶ
4. proposal.done なら:
     - settle ステップ（後述）を必要なら差し込み、expect を確定して終了
   proposal.needs_human なら（BE-0179）:
     - `handoff` 応答者に引き渡す。値の供給や「操作した」応答なら観測し直して続行し（人のターンは
       ステップを記録しません）、cancel なら停止する。応答者が居なければ `HumanHandoffUnavailable`
       を送出する。ハングも推測もない、明確でラベル付きの失敗です
   proposal.step なら:
     - _execute_with_recovery で実行（失敗かつ alert_guard ありなら片付けて 1 回再試行）
     - 成功したら steps に積む。解決しなければ break
5. Scenario(name, steps, expect) を返す
```

出力は `dump_scenarios` を通して YAML 化されます（[scenarios](scenarios.md#ラウンドトリップ読込--書出)）。

### human-in-the-loop ハンドオフ（BE-0179）

一部の flow は、AI が供給できない何か（ワンタイムパスワード、CAPTCHA、生体認証のプロンプト）で
塞がれます。ターン結果が「人が必要」（`proposal.needs_human`）のとき、ループは一時停止し、転送方式に
依存しない `Handoff` 契約（`handoff.py`）を通して人に制御を引き渡します。要求（なぜ止まったか、現在
画面の要約とスクリーンショット）が出て行き、応答（供給された値、または「デバイスを操作した。観測し
直せ」、または cancel）が返り、ループは実際の画面を観測し直して再開します。人がループに入るのは
オーサリングの最中だけで、記録した scenario は決定論的な `run` の経路に人を置かずに再生されます。

同じ契約に二つのインターフェースがあります。端末からは、`record` が上限つきで中断可能な対話的な標準
入力プロンプトから応答を読み取ります。`serve` から駆動する場合は、要求を record の server-sent
events ストリーム上の `human-request` イベントとしてシリアライズ配信し（一時停止したジョブは、目に
見える再開可能な「人待ち」状態に入ります）、ブラウザが応答を `/api/jobs/<id>/respond-human` へ
POST すると、`serve` がそれを spawn した `record` プロセスの標準入力へ書き込みます。どちらの経路でも
待機は上限つきで中断可能なので、席を外した人を相手にどのインターフェースもハングしません。応答者が
まったく居ない非対話的な実行や CI では、「人が必要」のターンは明確でラベル付きの非ゼロ終了になり、
ハングにも AI の推測にもなりません。この土台は仕組みと境界を担い、「人が必要」を立ち上げる
ヒューリスティックと、ハンドオフが記録する成果物の形は、子項目が受け持ちます。

### settle ステップの自動挿入

`_settle_step`: エージェントはターン間に「落ち着いた画面」を見ますが、決定的リプレイは速く、
非同期遷移（シート等）が描画される前に検証してしまうことがあります。そのため **expect の最初の
「存在を要する要素」への `wait`** を、アサーションの直前に記録します。これにより `run` に
暗黙のタイミングを追加せず、記録シナリオが自己完結します。

### モバイルターゲットでの動画キャプチャ

モバイル（iOS シミュレータ）ターゲットの場合、`record` は記録シナリオの最初のステップに
`capture: [video]` を付与します。これにより、リプレイ時にシナリオ全体の画面動画を記録します。
`requested_intervals` は 1 つのステップの inline `capture` を見てシナリオ全体のインターバルを
開始するため、1 つのアクションの区間だけでなくリプレイ全体が録画されます。この録画は `simctl`
インターバル（BE-0028）なので iOS バックエンド固有です。web ターゲットは別の手段で動画を取得し、
記録シナリオに `capture` は付きません。

## Claude オーサリングエージェント

`record` / `crawl` は本番の `agent.Agent` 実装として `ClaudeAgent` を 1 つ構築します
（`claude_agent.py`、`agents.py` が組み立てます）。モデルへはベンダー中立の `AiBackend`
シーム（BE-0104）を通して話すため、**プロバイダは設定の細部**であって別のエージェントではありません。
解決済みの `ai.provider`（[configuration](configuration.md#ai-プロバイダai-be-0047)）が次を選びます。

- **`api-key`**（既定）：**Anthropic API**。`ANTHROPIC_API_KEY`（または `ai.keyEnv` が指す環境変数）で認証します。（旧称 `anthropic` も同じものとして解決されます。）
- **`bedrock`**：**Amazon Bedrock**。AWS の資格情報と、プロバイダプレフィックス付きのモデル id
  （`BAJUTSU_BEDROCK_MODEL`）で認証します。
- **`ant`**：公式の **Anthropic CLI**（`ant auth login`。ブラウザ経由の OAuth（SSO）サインイン）。
  API キーの代わりに Claude の Pro / Max / Console のシートに課金され、すべての AI 経路で画像も
  そのまま使えます（BE-0163）。

モデルは `claude-opus-4-8` です。`anthropic` は遅延インポートで（資格情報無しでもモジュールは
読み込めます）、クライアントはテスト用に注入できます。どのプロバイダでもターン契約は同じです。

- **ツール強制呼び出し**: `tool_choice={"type": "any"}` で毎ターン **ちょうど 1 つ**のツールを呼びます。`tap(id)` / `type_text(id, text)` / `wait_for(id, timeout)` / `finish(assertions)`。`finish` の `assertions`（`exists` / `notExists` / `valueEquals` / `labelContains`）は `Assertion` に変換されます（`_to_assertion`）。
- **prompt cache**（API 経路）: 静的なシステムプロンプトとツール定義に `cache_control: ephemeral` を付け、ターンごとに変わるのは観測（要素 + スクショ）だけです。
- **視覚 + 要素の併用**: スクショで見た目と状態を読み、**操作は必ず要素リストの `id`** で行います（id を生成させません。リストには id を持つ要素だけを出します）。

```python
ClaudeAgent()                      # api: 本番（環境の ANTHROPIC_API_KEY を使う）
ClaudeAgent(client=fake_client)    # api: テスト
```

## システムアラートの自動対処

idb のアクセシビリティクエリは前面アプリにスコープされるため、**SpringBoard レベルのプロンプト**
（iOS の「パスワードを保存しますか?」等）は見えず、アプリの要素ツリーが 1 つの window ノードに
縮退して run が静かにブロックされます。`alerts.py` がこれを片付けます。

```python
class AlertLocator(Protocol):
    def locate(self, screenshot_png, instruction) -> AlertDecision: ...

class SystemAlertGuard:
    def dismiss(self, driver) -> bool: ...   # プロンプトがあれば座標 tap して True
```

- `SystemAlertGuard.dismiss`: スクショを撮り、ロケータに「プロンプトがあるか、どこを押すか」を
  問い合わせ、**正規化座標 [0,1]** を画面の point サイズ（最大要素 frame = アプリ window）に掛けて
  `driver.tap_point` でタップします。画面の point サイズは、ツリーが縮退してもアプリ window ノードが
  全画面に広がることから求めます。
- `ClaudeAlertLocator`: 本番の実装です。Claude vision に PNG を渡し、ツール `resolve_alert` を強制呼び出しします。
  既定は **最も無害な（dismiss 系の）ボタン**（"Not Now" / "Don't Allow" / "Cancel" 等）を選びます。
  `instruction` を与えると代わりにそのボタンをタップします。座標はピクセルで返させ、PNG の IHDR から得た
  画像サイズで [0,1] に正規化します。
- ロケータは注入可能です。テストやオフライン実行では決定的なロケータを使います。

### run / record での使い方

- `run`: ガードはシナリオごとに**既定 ON** です。CLI は [`dismissAlerts`](scenarios.md#dismissalertsシステムアラートガード)
  が有効な各シナリオに `SystemAlertGuard(...).dismiss` を `on_blocked` として渡します。ステップ失敗時に
  プロンプトを片付け、**そのステップを 1 回だけ再試行**します（[run-loop](run-loop.md#run_scenario1-シナリオの実行)）。
  シナリオ側で `dismissAlerts: false` で無効化、`{ instruction: "tap Allow" }` でボタンを指定できます。
  `--dismiss-alerts`/`--no-dismiss-alerts` は全シナリオを上書きし、`--alert-instruction "..."` は既定指示を設定します。
- `record --dismiss-alerts`: opt-in です（オーサリング時はまだシナリオが無いため）。割り込むプロンプトを片付け、
  エージェントに常にクリーンな画面を見せます。**dismissal は環境操作であって記録ステップではありません**
  （リプレイ側は各シナリオの `dismissAlerts` で対処します）。

> ガードは視覚モデルを使うため `ANTHROPIC_API_KEY` が必要です（[cli の .env](cli.md#環境変数env)）。
> 無くても**ベストエフォート**で単に no-op し、run を失敗させません。ガードはブロックしたプロンプトを
> 片付けるためだけに動作し、合否は機械判定のみで AI 非依存です（[concepts](concepts.md#1-ai-は著者と調査役であり判定者ではない)）。

[English](../recording.md) · **日本語**

# AI オーサリング（record / Tier 1）

> Tier 1 = AI ライブ操作。自然言語のゴールから、AI がアプリを探索しながら操作し、**決定的シナリオ**
> を書き出す。AI が関与するのはここ（記録時）だけ。成果物の YAML は AI 非依存で、以後は人間が所有する。
>
> 実装: `bajutsu/record.py`（ループ）・`bajutsu/agent.py`（抽象）・`bajutsu/claude_agent.py`（Claude）・
> `bajutsu/alerts.py`（システムアラート対処）。

関連: [concepts の 2 層](concepts.md#2-2-層構成tier-1--tier-2) ・ [scenarios](scenarios.md) ・ [run-loop](run-loop.md)

---

## Agent 抽象

ループとモデルを分離するための薄い Protocol（`agent.py`）。テストではスクリプト化した fake を、
本番では Claude を差す。

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

class Agent(Protocol):
    def next_action(self, observation: Observation) -> Proposal: ...
```

## record ループ

`record(driver, goal, agent, *, name, max_steps=30, alert_guard=None, ...) -> Scenario`
（`record.py`）。observe → 提案 → 実行 を `max_steps` まで繰り返す。

```
1. （alert_guard があれば）アプリを覆うもの（システムアラート等）を片付ける
2. elements = driver.query()
   - alert_guard 下で id を持つ要素が皆無なら、エージェントに死んだ画面を見せず再ループ
     （id を幻覚させないため）
3. screenshot を撮り、Observation を作って agent.next_action() を呼ぶ
4. proposal.done なら:
     - settle ステップ（後述）を必要なら差し込み、expect を確定して終了
   proposal.step なら:
     - _execute_with_recovery で実行（失敗かつ alert_guard ありなら片付けて 1 回再試行）
     - 成功したら steps に積む。解決しなければ break
5. Scenario(name, steps, expect) を返す
```

出力は `dump_scenarios` を通して YAML 化される（[scenarios](scenarios.md#ラウンドトリップ読込--書出)）。

### settle ステップの自動挿入

`_settle_step`: エージェントはターン間に「落ち着いた画面」を見るが、決定的リプレイは速く、
非同期遷移（シート等）が描画される前に検証してしまうことがある。そこで **expect の最初の
「存在を要する要素」へ向けた `wait`** を、アサーションの直前に記録しておく。これで `run` に
暗黙のタイミングを足さずに、記録シナリオが自己完結する。

## ClaudeAgent

`agent.Agent` を Claude（Anthropic SDK）で実装（`claude_agent.py`）。

- **ツール強制呼び出し**: `tool_choice={"type": "any"}` で、毎ターン **ちょうど 1 つ**のツールを呼ばせる。
  - `tap(id)` / `type_text(id, text)` / `wait_for(id, timeout)` / `finish(assertions)`。
  - `finish` の `assertions` は `exists` / `notExists` / `valueEquals` / `labelContains` を
    `Assertion` に変換（`_to_assertion`）。
- **prompt cache**: システムプロンプトとツール定義は静的で `cache_control: ephemeral` を付ける。
  ターンごとに変わるのは観測（要素 + スクショ）の user メッセージだけ。
- **視覚 + 要素の併用**: スクショで見た目・状態を読み、**操作は必ず要素リストの `id`** で行う
  （id を発明させない）。要素リストには id を持つ要素だけを出す。
- モデルは `claude-opus-4-8`。`anthropic` は遅延インポート（API キー無しでもモジュールは読める）。
  クライアントは注入可能（テスト用）。

```python
ClaudeAgent()                      # 本番（環境の ANTHROPIC_API_KEY を使う）
ClaudeAgent(client=fake_client)    # テスト
```

## システムアラートの自動対処

idb のアクセシビリティクエリは前面アプリにスコープされるため、**SpringBoard レベルのプロンプト**
（iOS の「パスワードを保存しますか?」等）は見えず、アプリの要素ツリーが 1 つの window ノードに
崩壊して run が静かにブロックされる。これを片付ける保険が `alerts.py`。

```python
class AlertLocator(Protocol):
    def locate(self, screenshot_png, instruction) -> AlertDecision: ...

class SystemAlertGuard:
    def dismiss(self, driver) -> bool: ...   # プロンプトがあれば座標 tap して True
```

- `SystemAlertGuard.dismiss`: スクショを撮り、ロケータに「プロンプトがあるか・どこを押すか」を
  尋ね、**正規化座標 [0,1]** を画面の point サイズ（最大要素 frame = アプリ window）に掛けて
  `driver.tap_point` で叩く。画面の point サイズは、ツリーが崩壊してもアプリ window ノードが
  全画面を張ることから求める。
- `ClaudeAlertLocator`: 本番の「目」。Claude vision に PNG を渡し、ツール `resolve_alert` を強制呼び。
  既定は **最も無害な（dismiss 系の）ボタン**（"Not Now" / "Don't Allow" / "Cancel" 等）を選ぶ。
  `instruction` を与えると代わりにそのボタンを押す。座標はピクセルで返させ、PNG の IHDR から得た
  画像サイズで [0,1] に正規化する。
- ロケータは注入可能。テスト・オフラインでは決定的なロケータを差す。

### run / record での使い方

- `run --dismiss-alerts`: `SystemAlertGuard(...).dismiss` を `on_blocked` として渡す。ステップ失敗時に
  プロンプトを片付け、**そのステップを 1 回だけ再試行**する（[run-loop](run-loop.md#run_scenario1-シナリオの実行)）。
  `--alert-instruction "..."` で押すボタンを指定できる。
- `record --dismiss-alerts`: オーサリング中に割り込むプロンプトを片付け、エージェントに常にクリーンな
  画面を見せる。**dismissal は環境操作であって記録ステップにはしない**（リプレイ側は
  `run --dismiss-alerts` で対処する）。

> いずれも視覚モデルを使うため `ANTHROPIC_API_KEY` が要る（[cli の .env](cli.md#環境変数env)）。
> `--dismiss-alerts` を付けない限り `run` は完全に AI 非依存（[concepts](concepts.md#1-ai-は著者と調査役であり判定者ではない)）。

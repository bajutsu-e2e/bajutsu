[English](../concepts.md) · **日本語**

# 中核概念と設計原則

> Bajutsu のすべてのモジュールは、ここに挙げる少数の原則から導かれます。実装の詳細に入る前に、
> 各部がなぜこの形になっているかを押さえるためのページです。設計指針の全文は [`DESIGN.md`](../../DESIGN.md) にあります。

関連: [architecture](architecture.md) · [selectors](selectors.md) · [run-loop](run-loop.md)

---

## 1. AI は著者と調査役であり、判定者ではない

LLM（大規模言語モデル）の非決定性、コスト、レイテンシを CI（継続的インテグレーション）ゲートに持ち込むわけにはいきません。これが最上位の制約であり、
下記の 2 層構造を直接生み出しています。

| コマンド | 層 | AI | 合否の決め方 |
|---|---|---|---|
| `record` | Tier 1 | 著者 | 探索しながら次の 1 手を提案 → 決定的シナリオを書き出す（[recording](recording.md)） |
| `run` | Tier 2 | **なし** | 各ステップを act → wait → verify。合否は `expect` の機械アサーションのみ（[run-loop](run-loop.md)） |
| `codegen` | — | なし | シナリオ → XCUITest の構造マッピング（[codegen](codegen.md)） |

`run` の経路には `anthropic` の呼び出しが一切ありません。唯一の例外は `--alert-handling`（OS の
システムアラートを視覚的に消す機能）です。これは合否を判定するのではなく環境を準備するものであり、
明示的にオプトインしたときにだけ動きます（[alert guard](recording.md#システムアラートの自動対処)）。

## 2. 2 層構成（Tier 1 / Tier 2）

- **[Tier 1](glossary.md#2-つの層) = AI ライブ操作**：探索とオーサリングを担います。柔軟ですが非決定的です。
  成果物であるシナリオ YAML は AI に依存せず、以後は人間が所有して編集します。
- **[Tier 2](glossary.md#2-つの層) = 決定的ランナー**：CI 回帰を担います。同じシナリオが毎回同じ経路をたどります。

両者は `observe → act → verify` という同じ形のループですが、AI が関与する層を厳密に分けています。

> シナリオはただの YAML なので、人間は AI を介さないオーサリング機能で拡張できます。再利用可能な
> `use` コンポーネント、データ駆動の行（`data` / `dataFile`）、シークレット変数（`${secrets.X}`）、
> 選択用の `tag`、`setLocation` / `push` のデバイスステップ、ファイル単位やシナリオ単位の
> `description` などです（[scenarios](scenarios.md)）。

## 3. 決定性ファースト（4 つの具体策）

Bajutsu の「決定的」という性質は、コードの構造として強制されます。

1. **曖昧なセレクタは即失敗する**：単一アクションの対象が 2 件以上一致した場合、最初に一致した要素を叩く
   のではなく `AmbiguousSelector` を投げます（[selectors](selectors.md#解決セマンティクス)）。
   非決定性を構造として排除するこの仕組みが、4 つの中で最も重要です。
2. **条件待機のみで、固定 sleep は使わない**：待機は条件が成立するまで `query()` をポーリングします。
   タイムアウトは必須で、無限待ちはできません（[run-loop](run-loop.md#待機条件待機)）。
3. **クリーン環境から開始する**：各テストは既定で boot/launch の前に `simctl erase` を実行し、前テストからの
   汚染を断ちます。状態は launch env や deeplink から注入します（[drivers](drivers.md#環境管理simctl)）。
4. **合否は機械チェックのみで決める**：「成功した気がする」という判断は入りません。機械アサーションは
   `exists`/`value`/`label`/`count`/`enabled`/`disabled`/`selected`/`request`/`visual` です
   （[selectors](selectors.md#アサーション評価)）。

> 適用範囲に注意してください。安定した識別子が安定させるのは「選択の決定性」だけです。タイミング、状態、
> ネットワークに起因するフレーキーは、待機と環境、そして `mocks` で別途対処します。

## 4. 安定セレクタ（accessibilityIdentifier 優先）

セレクタは常に **ローカライズされず、一意で、データに由来する id** で書きます。レイアウト変更や翻訳、座標のズレに起因するフレーキーを除去するためです。iOS では
`accessibilityIdentifier`、web では `data-testid`、Android では `resource-id` がそれにあたります。
セレクタの YAML は backend をまたいで同じで、変わるのは backend がそれを満たすために読む属性だけです。`label` は VoiceOver や AI が
意味を理解するためのもので、ローカライズで文言が変わるため、セレクタには使いません（補助や曖昧解消のためにだけ使います）。
命名規約（`<namespace>.<element>`）は [configuration](configuration.md#識別子の命名規約)を参照してください。

## 5. 安定度順ラダー（stability ladder）

UI 操作は、最も安定する手段から順に試します。ただし「安定」とは選択（どの要素か）の話であって、
actuation（どう叩くか）の話ではありません。idb（iOS の actuator）はいずれにせよ要素の frame 中心への座標 tap で操作するので、
順位によって変わるのは要素の選び方だけです。下に行くほど壊れやすくなります。

| 順 | 選択（どの要素） | 安定性 |
|---|---|---|
| 1 | `id` で一意解決 | 最安定（レイアウト / 翻訳 / 座標すべてに非依存） |
| 2 | `label` / `traits` で解決 | ローカライズに弱い |
| 3 | `index` / 生座標 | レイアウト変化で壊れる。最終手段 |

> actuation は常に frame 中心への座標 tap です。idb は semantic tap を公開しないため、run ループは
> 要素を一意に解決し（ネイティブの `AXUniqueId` を `id` として用います）、その frame 中心を叩きます。

**actuator（操作を担う backend）** は、安定度順の `backend` リストのうち最初に利用可能なものです。run 開始時に
1 つに確定し、run のあいだは固定します（2 つのドライバが同一デバイスを操作することで生じる非決定性を避けるためです）。
[driver backend actuator platform](glossary.md#driver-backend-actuator-platform) の関係の全体と、各 platform が
どの actuator に展開されるかは、用語集にまとめてあります。選択は常に `id` で行うため、シナリオは変わりません
（[drivers](drivers.md#バックエンド選択と-actuator)）。

## 6. アプリ非依存（差分は config に寄せる）

ツール本体とドライバ、実行系はどのアプリにも依存しません。新しいアプリを対象にするときに変えるのは
**アプリ側の準備（識別子の付与など）と `targets.<name>` の config エントリ 1 つ**だけです。各アプリの
決定性は、同じ実装規約によって保証されます（[configuration](configuration.md#新しいターゲットのオンボーディング)）。

同じやり方が Bajutsu を **プラットフォーム非依存**にします。プラットフォームとは `Driver` インターフェースの背後の backend（§5 の actuator）です。web（`playwright`）と Android（`adb`）はこの作り方で加わった backend です。シナリオ形式、セレクタ解決、アサーション、orchestrator、証跡、レポーターはバイト単位で同じです。プラットフォーム固有の差分は backend と config にだけ置きます。

## 7. 証跡はルール（繰り返し発火）

「特定の動作の **たびに** 証跡を取る」という要求は、単発の指示としてではなく **トリガー方式のルール**
（[`capturePolicy`](glossary.md#証跡-capturepolicy-trace-triage)）として保存します。こうすることで、2 度目以降の
run では AI なしで同じ証跡が再現します（[evidence](evidence.md)）。

---

### この原則がコードのどこに現れるか（早見表）

| 原則 | 主な実装箇所 |
|---|---|
| 曖昧セレクタ即失敗 | `drivers/base.py` `resolve_unique` |
| 条件待機のみ | `orchestrator/waits.py` `_wait` |
| クリーン環境 | `runner/launch.py` `launch_driver` · `simctl.py` `Env.erase` |
| 機械アサーション | `assertions/` |
| 安定度順 / actuator | `backends.py` `select_actuator` · 各 `drivers/*.py` `capabilities()` |
| アプリ非依存 | `config/resolve.py` `resolve` → `Effective` |
| 証跡ルール | `orchestrator/loop.py` `_collect_captures` · `evidence/core.py` |

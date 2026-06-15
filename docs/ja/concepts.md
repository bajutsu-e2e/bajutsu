[English](../concepts.md) · **日本語**

# 中核概念と設計原則

> Bajutsu のすべてのモジュールは、ここに挙げる少数の原則から導かれます。実装の詳細に入る前に、
> なぜそうなっているかを押さえるためのページです。設計指針の全文は [`DESIGN.md`](../../DESIGN.md) にあります。

関連: [architecture](architecture.md) ・ [selectors](selectors.md) ・ [run-loop](run-loop.md)

---

## 1. AI は著者と調査役であり、判定者ではない

LLM の非決定性・コスト・レイテンシを CI（継続的インテグレーション）ゲートに持ち込みません。これが最上位の制約であり、コードの
2 層構造（下記）に直結します。

| コマンド | 層 | AI | 合否の決め方 |
|---|---|---|---|
| `record` | Tier 1 | 著者 | 探索しながら次の 1 手を提案 → 決定的シナリオを書き出す（[recording](recording.md)） |
| `run` | Tier 2 | **なし** | 各ステップを act → wait → verify。合否は `expect` の機械アサーションのみ（[run-loop](run-loop.md)） |
| `codegen` | — | なし | シナリオ → XCUITest の構造マッピング（[codegen](codegen.md)） |

`run` の経路には `anthropic` の呼び出しが一切ありません。唯一の例外は `--dismiss-alerts`（OS の
システムアラートを視覚的に消す機能）で、これは合否判定ではなく環境の準備にあたり、
明示的にオプトインしたときのみ動きます（[recording の alert guard](recording.md#システムアラートの自動対処)）。

## 2. 2 層構成（Tier 1 / Tier 2）

- **Tier 1 = AI ライブ操作**: 探索とオーサリングです。柔軟ですが非決定的です。成果物（シナリオ YAML）は
  AI 非依存で、以後は人間が所有・編集します。
- **Tier 2 = 決定的ランナー**: CI 回帰です。同じシナリオが毎回同じ経路をたどります。

両者は `observe → act → verify` という同じ形のループですが、AI が関与する層を厳密に分けています。

> シナリオはただの YAML なので、人間は AI を介さないオーサリング機能で拡張できます。具体的には、再利用可能な
> `use` コンポーネント、データ駆動（`data` / `dataFile`）、シークレット変数（`${secrets.X}`）、
> 選択用の `tag`、`setLocation` / `push` のデバイスステップ、ファイル / シナリオ単位の
> `description` などです（[scenarios](scenarios.md)）。

## 3. 決定性ファースト（4 つの具体策）

Bajutsu の「決定的」という性質は、コードの構造として強制されます。

1. **曖昧なセレクタは即失敗** — 単一アクションの対象が 2 件以上一致した場合、「最初の一致を叩く」
   のではなく `AmbiguousSelector` を投げます（[selectors](selectors.md#解決セマンティクス)）。
   非決定性を構造で排除するもので、この 4 つの中で最も重要な仕組みです。
2. **固定 sleep 禁止・条件待機のみ** — 待機は `query()` をポーリングして条件成立を待ちます。
   タイムアウトは必須です（無限待ち禁止）（[run-loop](run-loop.md#待機条件待機)）。
3. **クリーン環境から開始** — 各テストは既定で `simctl erase` 後に boot/launch し、前テストの汚染を
   断ちます。状態は launch env / deeplink で注入します（[drivers](drivers.md#環境管理simctl)）。
4. **合否は機械チェックのみ** — 「成功した気がする」という判断を排除します。機械アサーションは
   `exists`/`value`/`label`/`count`/`enabled`/`disabled`/`selected`/`request` の 8 種です
   （[selectors](selectors.md#アサーション評価)）。

> 範囲の切り分けです。アクセシビリティ識別子で安定するのは「選択の決定性」だけです。タイミング・状態・
> ネットワーク起因のフレーキーは、待機・環境・（将来の）モックで別途対処します。

## 4. 安定セレクタ（accessibilityIdentifier 優先）

セレクタは常に **`accessibilityIdentifier`（非ローカライズ・一意・データ由来）** で書きます。
理由は、レイアウト変更・翻訳・座標ズレ由来のフレーキーを除去するためです。`label` は VoiceOver / AI の
意味理解用で、ローカライズで文言が変わるためセレクタには使いません（補助・曖昧解消のみに使います）。
命名規約（`<namespace>.<element>`）は [configuration の規約](configuration.md#識別子の命名規約)を参照してください。

## 5. 安定度順ラダー（stability ladder）

UI 操作は、最も安定する手段から順に試します。ただし「安定」とは選択（どの要素か）の話で、
actuation（どう叩くか）の話ではありません。idb は要素の frame 中心へ座標 tap で操作するため、順位で
変わるのは要素の選び方だけです。下に行くほど壊れやすくなります。

| 順 | 選択（どの要素） | 安定性 |
|---|---|---|
| 1 | `id` で一意解決 | 最安定（レイアウト / 翻訳 / 座標すべてに非依存） |
| 2 | `label` / `traits` で解決 | ローカライズに弱い |
| 3 | `index` / 生座標 | レイアウト変化で壊れる。最終手段 |

> actuation は常に frame 中心への座標 tap です。idb は semantic tap を公開しないため、run ループは
> 要素を一意に解決し（ネイティブの `AXUniqueId` を `id` として用います）、その frame 中心を叩きます。

**actuator（操作を担う backend）はリストで最初に利用可能なバックエンド**で、run 開始時に 1 つ確定し、
run 中は固定します（2 ドライバが同一デバイスを操作する非決定性を避けるためです）。`backend` リストは
引き続き安定度順で書きますが、現状の登録バックエンドは idb のみで、常に actuator になります。将来 backend を
追加できるようリスト構造を残しています。選択は常に `id` なのでシナリオは変わりません
（[drivers](drivers.md#バックエンド選択と-actuator)）。

## 6. アプリ非依存（差分は config に寄せる）

ツール本体・ドライバ・実行系はアプリに依存しません。新しいアプリを対象にするときに変えるのは
**アプリ側の準備（識別子付与など）と `apps.<name>` の config 1 エントリ**だけです。各アプリの
決定性は同じ実装規約で担保されます（[configuration のオンボーディング](configuration.md#新しいアプリのオンボーディング)）。

## 7. 証跡はルール（繰り返し発火）

「特定の動作の **たびに** 証跡を取る」という要求は、単発指示ではなく **トリガー方式のルール**
（`capturePolicy`）として保存します。これにより二度目以降は AI なしで同じ証跡が再現します
（[evidence](evidence.md)）。

---

### この原則がコードのどこに現れるか（早見表）

| 原則 | 主な実装箇所 |
|---|---|
| 曖昧セレクタ即失敗 | `drivers/base.py` `resolve_unique` |
| 条件待機のみ | `orchestrator.py` `_wait` |
| クリーン環境 | `runner.py` `launch_driver` ・ `env.py` `Env.erase` |
| 機械アサーション | `assertions.py` |
| 安定度順 / actuator | `backends.py` `select_actuator` ・各 `drivers/*.py` `capabilities()` |
| アプリ非依存 | `config.py` `resolve` → `Effective` |
| 証跡ルール | `orchestrator.py` `_collect_captures` ・ `evidence.py` |

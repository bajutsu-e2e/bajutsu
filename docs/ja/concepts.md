[English](../concepts.md) · **日本語**

# 中核概念と設計原則

> Bajutsu のすべてのモジュールはここに挙げる少数の原則から導かれる。実装の詳細に入る前に、
> 「なぜそうなっているか」を押さえるためのページ。設計指針の全文は [`DESIGN.md`](../../DESIGN.md)。

関連: [architecture](architecture.md) ・ [selectors](selectors.md) ・ [run-loop](run-loop.md)

---

## 1. AI は著者と調査役であり、判定者ではない

LLM の非決定性・コスト・レイテンシを **CI ゲートに持ち込まない**。これが最上位の制約で、コードの
2 層構造（下記）に直結する。

| コマンド | 層 | AI | 合否の決め方 |
|---|---|---|---|
| `record` | Tier 1 | 著者 | 探索しながら次の 1 手を提案 → 決定的シナリオを書き出す（[recording](recording.md)） |
| `run` | Tier 2 | **なし** | 各ステップを act → wait → verify。合否は `expect` の機械アサーションのみ（[run-loop](run-loop.md)） |
| `codegen` | — | なし | シナリオ → XCUITest の構造マッピング（[codegen](codegen.md)） |

`run` の経路には `anthropic` の呼び出しが一切ない。唯一の例外は `--dismiss-alerts`（OS の
システムアラートを視覚で消す保険）で、これは「合否判定」ではなく「環境の片付け」であり、
明示オプトイン時のみ動く（[recording の alert guard](recording.md#システムアラートの自動対処)）。

## 2. 2 層構成（Tier 1 / Tier 2）

- **Tier 1 = AI ライブ操作**: 探索とオーサリング。柔軟だが非決定的。成果物（シナリオ YAML）は
  AI 非依存で、以後は人間が所有・編集する。
- **Tier 2 = 決定的ランナー**: CI 回帰。同じシナリオが毎回同じ経路をたどる。

両者は `observe → act → verify` という同じ形のループだが、AI が関与する層を厳密に分けている。

> シナリオはただの YAML なので、人間は AI を介さないオーサリング機能で拡張できる: 再利用可能な
> `use` コンポーネント、データ駆動（`data` / `dataFile`）、シークレット変数（`${secrets.X}`）、
> 選択用の `tag`、`setLocation` / `push` のデバイスステップ、ファイル / シナリオ単位の
> `description`（[scenarios](scenarios.md)）。

## 3. 決定性ファースト（4 つの具体策）

Bajutsu の「決定的」は精神論ではなく、コードの構造として強制される。

1. **曖昧なセレクタは即失敗** — 単一アクションの対象が 2 件以上一致したら「最初の一致を叩く」
   のではなく `AmbiguousSelector` を投げる（[selectors](selectors.md#解決セマンティクス)）。
   非決定性を *構造で* 排除する、最も重要な一点。
2. **固定 sleep 禁止・条件待機のみ** — 待機は `query()` をポーリングして条件成立を待つ。
   タイムアウトは必須（無限待ち禁止）（[run-loop](run-loop.md#待機条件待機)）。
3. **クリーン環境から開始** — 各テストは既定で `simctl erase` 後に boot/launch。前テストの汚染を
   断つ。状態は launch env / deeplink で注入する（[drivers](drivers.md#環境管理simctl)）。
4. **合否は機械チェックのみ** — 「成功した気がする」を排除。8 種の機械アサーション
   `exists`/`value`/`label`/`count`/`enabled`/`disabled`/`selected`/`request`
   （[selectors](selectors.md#アサーション評価)）。

> 切り分け: アクセシビリティ識別子で安定するのは「**選択の決定性**」だけ。タイミング・状態・
> ネットワーク起因のフレーキーは、待機・環境・（将来の）モックで別途対処する。

## 4. 安定セレクタ（accessibilityIdentifier 優先）

セレクタは常に **`accessibilityIdentifier`（非ローカライズ・一意・データ由来）** で書く。
理由はレイアウト変更・翻訳・座標ズレ由来のフレーキーを除去するため。`label` は VoiceOver / AI の
意味理解用で、ローカライズで文言が変わるためセレクタには使わない（補助・曖昧解消のみ）。
命名規約（`<namespace>.<element>`）は [configuration の規約](configuration.md#識別子の命名規約)。

## 5. 安定度順ラダー（stability ladder）

UI 操作は「**最も安定する手段から順に**」試す。ただし「安定」とは **選択**（どの要素か）の話で、
actuation（どう叩くか）の話ではない。idb は要素の frame 中心へ座標 tap で操作するため、順位で
変わるのは要素の選び方だけ。下に行くほど壊れやすい。

| 順 | 選択（どの要素） | 安定性 |
|---|---|---|
| 1 | `id` で一意解決 | 最安定（レイアウト / 翻訳 / 座標すべてに非依存） |
| 2 | `label` / `traits` で解決 | ローカライズに弱い |
| 3 | `index` / 生座標 | レイアウト変化で壊れる。最終手段 |

> actuation は常に frame 中心への座標 tap。idb は semantic tap を公開しないため、run ループは
> 要素を一意に解決し（ネイティブの `AXUniqueId` を `id` として用いる）、その frame 中心を叩く。

**actuator（操作を担う backend）はリストで最初に利用可能なバックエンド**で、run 開始時に 1 つ確定し
run 中固定（2 ドライバが同一デバイスを操作する非決定性を避ける）。`backend` リストは依然「安定度順」で
書くが、現状の登録バックエンドは idb のみで常に actuator になる — 将来 backend を追加できるよう
リスト構造を残している。選択は常に `id` なのでシナリオは不変
（[drivers](drivers.md#バックエンド選択と-actuator)）。

## 6. アプリ非依存（差分は config に寄せる）

ツール本体・ドライバ・実行系はアプリに依存しない。新しいアプリを対象にするときに変えるのは
**アプリ側の準備（識別子付与など）と `apps.<name>` の config 1 エントリ**だけ。各アプリの
決定性は同じ実装規約で担保される（[configuration のオンボーディング](configuration.md#新しいアプリのオンボーディング)）。

## 7. 証跡はルール（繰り返し発火）

「特定の動作の **たびに** 証跡を取る」という要求は、単発指示ではなく **トリガー方式のルール**
（`capturePolicy`）として保存する。これにより二度目以降は AI なしで同じ証跡が再現する
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

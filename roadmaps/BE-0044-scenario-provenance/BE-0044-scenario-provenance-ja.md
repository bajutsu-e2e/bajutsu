[English](BE-0044-scenario-provenance.md) · **日本語**

# BE-0044 — シナリオの来歴（`from:` — ステップ ↔ 自然言語の対応）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0044](BE-0044-scenario-provenance-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0044") |
| 実装 PR | [#235](https://github.com/bajutsu-e2e/bajutsu/pull/235)（スキーマ、record、lint）、[#264](https://github.com/bajutsu-e2e/bajutsu/pull/264)（trace + レポート表示） |
| トピック | オーサリング体験 |
| 由来 | BE-0002 の follow-up（来歴はそこで「軽いままの後続作業」として先送りされた） |
<!-- /BE-METADATA -->

## はじめに

`from:` という第一級の DSL フィールドを追加し、**記録された各要素がどの自然言語の語句から生まれたか**
を残します。各ステップ、各 `expect` アサーション、各 `capturePolicy` ルールに付与し、シナリオ全体の
元のゴールはシナリオ単位で保持します。`record`（Tier 1 の AI）が自然言語を構造化シナリオへ正規化する際に
埋め、`run`（Tier 2）は一切読みません。プレーンでレビュー可能な YAML であり、`load ↔ dump` の
round-trip を生き残るので、`update`（M4 の最小差分提案）や再記録でも黙って失われることがありません。

## 動機

[DESIGN §6.5](../../DESIGN.md) は、各ステップ / ルールの由来（正規化元の自然言語）を**来歴**として
保存することを求めています。すなわち「由来を `# from:` コメントか sidecar として残す」ということです。
[BE-0002](../BE-0002-m2-ai-loop-and-evidence/BE-0002-m2-ai-loop-and-evidence-ja.md) は M2 の
オーサリングループを実装しましたが、この点は明示的に「軽いまま。alternative ではなく follow-up」と
されました。現状はこれを担うものが何もありません。`Step` には自由記述の `name` がありますが、ステップを
それを生んだ自然言語へ結びつけるフィールドは存在しません。

なぜ重要か、そしてなぜ代替案ではなく構造化フィールドなのか:

1. **AI 出力の人間レビュー。** シナリオは人間が所有し PR でレビューする永続成果物です
   （[DESIGN §6.5](../../DESIGN.md)）。「この `tap` はゴールの『設定を開く』に由来する」と見えること
   こそが、正規化が意図に忠実かをレビュアーが判断する拠り所になります。`record` が意図と食い違う
   シナリオを黙って生むのを防ぐ、核心のチェックです。
2. **`update` には拠り所が要る。** M4 の自己修復は UI 変更時に**最小差分**を提案します
   （[DESIGN §6.5](../../DESIGN.md)、`triage.py`）。壊れたステップだけを再導出するには、その
   ステップがどの意図に対応していたかが分かると有利です。来歴は「*この*意図を再オーサリングする」
   （シナリオ全体を再記録しない）ための自然なアンカーになります。
3. **コメントは round-trip を生き残らない。** DESIGN 時代の `# from:` コメント案は、現状では技術的に
   塞がっています。シナリオは `scenario_dict()` → `_prune()` → `model_dump()` → `_yaml.safe_dump()`
   （`scenario.py`）を通って出力され、この経路は **YAML コメントをすべて落とします**。`record` の
   書き出しも `update` / 再 dump も、その都度来歴を消してしまいます。構造化フィールドはモデルの内側に
   乗るので、設計上 round-trip を保証できます。
4. **決定性は無傷。** `from:` は純粋なメタデータです。`run` は決して読まないので、ゲートに LLM 呼び出しを
   **一切**増やさず、合否に影響しません。完全にオーサリング側にとどまります
   （[DESIGN §2](../../DESIGN.md)、[CLAUDE.md](../../CLAUDE.md) の第 1 原則）。

## 詳細設計

### DSL：任意の `from:` フィールド

`from:` は、その要素が正規化された元の自然言語の語句を持つ**任意の文字列**です。`name` / `capture` と
並ぶ*修飾子*として追加し、アクションにはしません。よって「アクションは 1 つ / アサーション種別は 1 つ」の
検証（`scenario.py` の `_STEP_ACTIONS` / `_ASSERTION_KINDS`）を乱しません。付与先は 4 レベルです:

| レベル | モデル（`scenario.py`） | 意味 |
|---|---|---|
| シナリオ | `Scenario.from_`（alias `from`） | シナリオ記録の元になった自然言語ゴール |
| ステップ | `Step.from_`（alias `from`） | この 1 ステップの由来となった語句 / 意図 |
| アサーション | `Assertion.from_`（alias `from`） | `expect` / `assert` の検証が由来する語句 |
| 証跡ルール | `CaptureRule.from_`（alias `from`） | 証跡ルールが正規化された元の指示（例「送信を押すたびにスクショ」） |

```yaml
- name: 設定を開いて再生成する
  from: "設定を開いて、再インデックスして、正規化設定が消えていることを確認して"   # ← 元のゴール
  steps:
    - tap: { id: settings.open }
      from: "設定を開く"
    - tap: { id: settings.reindex }
      from: "再インデックスする"
      capture: [screenshot.after, deviceLog]
  expect:
    - exists: { label: "正規化設定が変更されています", negate: true }
      from: "正規化設定が消えていること"
  capturePolicy:
    - on: { action: tap, idMatches: "*.submit" }
      capture: [screenshot.after, elements, network]
      from: "送信を押すたびにスクショとネットワークログを残して"
```

- **グループ化は新構文ではなく自然な創発。** 1 つの発話が複数ステップを生む場合、それらのステップは
  **同じ** `from:` 文字列を持ちます。レポート（と `trace`）は同一 `from:` の連続を 1 つのラベル付き
  グループにまとめます。span/range 構文は導入しません。これにより round-trip を単純に保ち、各ステップ単位で
  独立に手編集できるようにします。
- **round-trip。** `from:` は alias で dump され、未設定時は prune されます（`_prune` が `None` を落とす）。
  来歴を持たない手書きシナリオはきれいなまま、記録されたシナリオは何度 `load`/`dump` しても来歴を保ちます。
- **言語。** 文字列は著者が書いた言語のまま逐語的に保持します（本プロジェクトはバイリンガルですが、来歴は
  翻訳しません）。

### 記録時の生成（Tier 1）

書き手は `record` だけです。`Agent` はすでに 1 ターン 1 アクションを提案しており、来歴はその背後にある
自然言語の意図です:

- `claude_agent.py`: 各アクションツール（`tap` / `type_text` / `wait_for`）に、そのアクションの*理由*を
  表す短い語句である任意の `intent` 引数を追加し、`Step.from_` に保存します。`finish` ツールの
  アサーションも、それぞれが検証する語句を `Assertion.from_` に持たせます。シナリオ単位の `from:` は
  単に `Observation.goal` です。
- これは完全に Tier 1（AI が著者）の内側で、第 1 原則と整合します。テスト用のスクリプト `Agent` は
  決定的な intent を埋めるので、record のテストは AI 非依存のままです。

### run / lint / report

- **`run`（Tier 2）:** `from:` を完全に無視します。オーケストレータは読まないので、決定性と
  「ゲートに AI を入れない」原則が設計上保たれます。
- **`lint`:** フィールド型を検証し、来歴の**アドバイザリ**カバレッジ（記録シナリオ中で `from:` を持つ
  ステップ割合）を提示できます。`doctor` のアドバイザリ流儀に倣い、run を失敗させません（手書きシナリオは
  正当に来歴を持ちません）。
- **`trace` / `report.html`:** 各ステップの `from:` をインライン表示（「なぜこのステップがあるか」）し、
  創発したグループにラベルを付け、タイムラインを自然言語 ↔ アクションの対応表にします。これがユーザに
  見える成果で、スキーマの後に追加できます。

## 検討した代替案

- **`# from:` の YAML コメント（DESIGN の元表現）。** 保存形式としては却下します。現状の dumper
  （`model_dump` → `safe_dump`）はコメントを落とすため、`record` / `update` の書き出しのたびに消えます。
  コメントを保つには load/dump 経路全体を comment-aware なシリアライザ（例 `ruamel.yaml`）へ置換する
  大規模で侵襲的な変更が要り、しかもモデルフィールドより round-trip 保証は劣ります。
- **sidecar の来歴ファイル**（`<scenario>.provenance.yaml`、step id をキーに）。シナリオ本体をきれいに
  保てますが、手編集のたびに同期すべき第 2 ファイルが増え、ズレのリスク（YAML 側でステップを並べ替え/削除
  すると sidecar に古い項目が残る）が生じます。本プロジェクトの原則は「YAML が唯一の永続成果物」であり、
  来歴はその*中*に属します。
- **正規化した意図テーブル**（シナリオ単位の `{id, text}` 意図リストを置き、ステップが
  `from: <intentId>` で参照）。多数のステップが 1 発話を共有するときの重複は減りますが、オーサリングと
  編集が重くなり、一目でのレビューを損なう間接参照が入ります。創発グループ化を伴うインライン文字列の方が
  単純です。重複が痛くなれば正規化テーブルを再検討できます。
- **`Step.name` の流用。** `name` はステップの自由記述の人間向けラベルであって由来発話ではなく、手書き
  シナリオにも存在します。流用すると人間のネーミングと AI の来歴が混ざり、シナリオ/アサーション/ルールの
  各レベルを失います。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

- [DESIGN §6.5](../../DESIGN.md) — 正規化とラウンドトリップ。`# from:` / 来歴の要件
- [DESIGN §2](../../DESIGN.md)、[CLAUDE.md](../../CLAUDE.md) — AI は著者であって判定者ではない / 決定性優先
- [BE-0002 — AI authoring loop & evidence (M2)](../BE-0002-m2-ai-loop-and-evidence/BE-0002-m2-ai-loop-and-evidence-ja.md) — 来歴が先送りされた箇所
- [recording.md](../../docs/ja/recording.md) — `record` ループと `Agent` 抽象
- [scenarios.md](../../docs/ja/scenarios.md)、`bajutsu/scenario.py` — シナリオスキーマと load/dump 経路

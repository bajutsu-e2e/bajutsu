[English](BE-0250-assertions-package-eval-context.md) · **日本語**

# BE-0250 — assertions のパッケージ分割と評価コンテキストの EvalContext への統合

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0250](BE-0250-assertions-package-eval-context-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0250") |
| 実装 PR | [#1093](https://github.com/bajutsu-e2e/bajutsu/pull/1093)、[#1100](https://github.com/bajutsu-e2e/bajutsu/pull/1100)、[#1106](https://github.com/bajutsu-e2e/bajutsu/pull/1106)、[#1113](https://github.com/bajutsu-e2e/bajutsu/pull/1113) |
| トピック | コードベース品質・技術的負債 |
| 関連 | [BE-0172](../BE-0172-run-loop-step-decomposition/BE-0172-run-loop-step-decomposition-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`bajutsu/assertions.py` は 970 行あり、5 つの役割が同居しています。`AssertionResult` を返すという一点だけを
共通の理由に、dispatcher にあたる `evaluate`、`evaluate_one`、種別ごとの evaluator、web のモックルータや
`until: {request}` の待機とも共有するネットワークタイムラインの照合、`visual` assertion 向けの画像前処理、
`responseSchema` 向けの JSON Schema の読み込みと検証が 1 つのモジュールに詰め込まれています。本項目は、
このモジュールをそれぞれの境界に沿ってパッケージへ分割し、`visual_context`、`schema_context`、
`golden_context`、`clipboard` という種別ごとのコンテキストを 5 層にわたって個別の引数で受け渡している現状を、
1 つの `EvalContext` に束ねる、挙動を保存したリファクタリングです。これにより、コンテキストを必要とする
assertion の種別を新設するたびに 5 か所を書き換える必要がなくなります。

## 動機

現状の 1 モジュールは、selector の解決、ネットワークタイムラインの照合、画像の座標計算とピクセル I/O、
JSON Schema のファイル読み込みとパストラバーサル対策、そしてトップレベルの dispatch という、互いにほとんど
関係のない 5 つの関心事を担っています。これは、`bajutsu/orchestrator/actions/` が action の handler を
グループごとに分けていたり、`bajutsu/visual.py` がすでに画素比較エンジンを単独で持っていたりする、
このコードベースがすでに実践している単一責任の考え方とは食い違っています。具体的には次のとおりです。

- **dispatcher。** `evaluate`、`evaluate_one`（847〜970 行）。`if a.X is not None: return _eval_X(...)` という
  分岐の連なりと、`request` の 1 対 1 割り当てを担います。
- **種別ごとの UI evaluator。** `_eval_exists`、`_eval_text`、`_eval_count`、`_eval_state`（133〜202 行）と
  `_eval_request`、`_eval_event`、`_eval_request_sequence`（278〜418 行）です。`query()` のスナップショットや
  ネットワークタイムラインに対する、小さく純粋なチェックです。
- **ネットワークの照合。** assertion だけでなく web のモックルータや `until: {request}` の待機とも共有される
  `match_request`、`count_matching`、`request_label`（205〜275 行）と、二部グラフの `_assign_requests`
  （497〜519 行、Kuhn のアルゴリズムによる増加道探索で、広い `request` matcher がより限定的な matcher の
  唯一の exchange を奪わないようにしています）です。
- **約 240 行の画像前処理サブシステム。** 座標計算、クロップ、マスク、Pillow によるファイル I/O を行う
  `_visual_scale`、`_frame_to_px`、`_resolve_mask`、`_prepare_visual_comparison`、`_resolve_masks`、
  `_resolve_baselines`、`_eval_visual`（537〜780 行）で、`shutil` と `PIL.Image` を直接 import しています。
  本来は UI とネットワークの assertion を扱うはずのモジュールに、画像処理の依存が入り込んでいる形です。
- **JSON Schema のファイル読み込み、パスの閉じ込めチェック、検証。** `_load_schema`、`_validate_instance`
  （421〜494 行）です。

これとは別に、per-run のコンテキストを必要とする assertion の種別を新設する作業も、現状はあちこちに手を
入れる必要があります。各コンテキストが 5 つの層にまたがって、それぞれ独立した緩い引数として受け渡されて
いるためです。`evaluate`（914〜923 行）と `evaluate_one`（847〜856 行）は、`visual_context`、
`schema_context`、`clipboard`、`golden_context` という 4 つの keyword-only 引数をそれぞれ個別に持ちます。
`bajutsu/orchestrator/loop.py` の `run_scenario`（164〜168 行）と `_run_step_body`（107〜118 行）も、同じ
4 つ（に加えて `mailbox`）を個別のシグネチャ引数として持ちます。さらに `bajutsu/runner/pipeline.py`
（100〜105 行、138〜162 行）が、3 つのコンテキストオブジェクトをそれぞれ個別に構築してから渡しています。
つまり、コンテキストを伴う assertion の種別を 1 つ新設するだけで、`Assertion` モデル、
`_ASSERTION_KINDS`（`bajutsu/scenario/models/_base.py:30`）、新しい `_eval_X` 関数、14 分岐の `if` 連鎖への
新しい分岐、そしてこれら 5 つのシグネチャへの新しい引数という、フィールド 1 つぶんの追加のために 5 か所を
書き換える羽目になります。

assertion の判定結果や、観測可能な run の挙動はここでは変わりません。assertion は決定的な Tier-2 のゲート
上で合否を決めるものであり（プライムディレクティブ 1）、本提案はあくまで構造上の変更にとどまります。
分割の前後で `AssertionResult` が一致することを証明する parity テストで検証します。

## 詳細設計

作業は、互いに独立した次の 4 単位に MECE に分かれます。いずれも挙動を保存し、単独で投入できます。

- **`bajutsu/assertions.py` を `assertions/` パッケージへ分割する。**
  - **`assertions/network.py`**：`match_request`、`count_matching`、`request_label`、`_assign_requests`、
    `_request_assignment_result`（`assertions.py:522`。`evaluate_one` で `_assign_requests` と並んで呼ばれ、
    その結果を `request_label` から組み立てます）。
    web のモックルータや `until: {request}` の待機もすでに `match_request` や `count_matching` に依存している
    共有の matcher なので、`bajutsu/network.py` の `NetworkExchange` の近くに置くほうがふさわしいとも言えます。
    循環 import さえ生じなければどちらの配置でもよく、実装 PR で明示的に判断します。
  - **`assertions/visual.py`**：`_visual_scale`、`_frame_to_px`、`_resolve_mask`、`_shift`、`_Prepared`、
    `_prepare_visual_comparison`、`_resolve_masks`、`_resolve_baselines`、`_eval_visual`、`VisualContext`、
    `VisualEvidence`。画素比較エンジンを持つ `bajutsu/visual.py` の隣に置き、両者で visual assertion に
    関わるものを一括して担わせます。
  - **`assertions/schema.py`**：`SchemaContext`、`_load_schema`、`_validate_instance`、`_eval_response_schema`。
  - **`assertions/evaluate.py`**：薄い dispatcher（`evaluate`、`evaluate_one`、`passed`）と、独立したモジュールを
    持つほどではない種別ごとの UI evaluator（`_eval_exists`、`_eval_text`、`_eval_count`、`_eval_state`、
    `_eval_request`、`_eval_event`、`_eval_request_sequence`、`_eval_clipboard`、`_eval_golden`）です。
    `GoldenContext`（`assertions.py:105`）も、唯一の評価器である `_eval_golden`（比較ロジックはすでに
    `bajutsu/golden.py` から import しています）の隣に、独立モジュールではなくここに置きます。
  - `bajutsu/assertions.py`（あるいは `assertions/__init__.py`）が公開インターフェースを re-export し、
    既存の `from bajutsu import assertions` や `bajutsu.assertions.evaluate(...)` の呼び出し側には影響しません。
    純粋なモジュールの再編成であり、公開 API の変更ではありません。

- **per-run のコンテキストを 1 つの `EvalContext` に束ねる。** `EvalContext(visual: VisualContext | None,
  schema: SchemaContext | None, golden: GoldenContext | None, clipboard_reader: Callable[[], str | None] |
  None)` という frozen な型を導入します（フィールドの正確な形は実装時に詰めます。`clipboard` は現状、
  ブロックに `clipboard` assertion があるときだけ `_clipboard_for` 経由で遅延読み込みされており、置き換え後も
  この遅延性を保ち、毎ステップ読み込むようにしてはいけません）。この `EvalContext` を 1 つだけ、`evaluate` →
  `evaluate_one` → `run_scenario` → `_run_step_body` → `pipeline.py` での構築という経路の端から端まで受け渡し、
  この 5 層それぞれで重複していた 4 つ個別の引数を取り除きます。

- **`evaluate_one` の 14 分岐の `if` 連鎖を、データ駆動の registry に置き換える。** `_ASSERTION_KINDS` を
  キーにした `{field_name: eval_fn}` の対応表を導入します。これは、`bajutsu/orchestrator/actions/_registry.py`
  が action の dispatch にすでに使っている `_HANDLERS: dict[str, ActionHandler]` という registry
  （`@_handler(kind)` で自己登録する handler を `_do_action` が dispatch する仕組み）を踏襲するものです。
  dispatch は `is not None` の連鎖ではなく参照になり、種別を追加する作業は連鎖への分岐追加ではなく関数の
  登録で済むようになります。

- **`_ASSERTION_KINDS` を `Assertion` モデルから導出する。** `bajutsu/scenario/models/_base.py` で手動保守
  されているタプルを、`tuple(f for f in Assertion.model_fields if f != "from_")` に置き換えます。これは、
  `bajutsu/orchestrator/actions/_registry.py` の `_RUNTIME_ACTIONS`（`tuple(a for a in STEP_ACTIONS if a !=
  "use")`、こちらも `Step` モデルから導出されています）という既存の先例を踏襲するものです。assertion の種別を
  1 つ追加する作業は、`Assertion` への新しいフィールドと registry への登録だけで済み、手動保守のタプルを
  編集する必要がなくなります。

各単位は単独で `make check` を緑に保ち、assertion の観測可能な挙動を変えるのではなく、分割の前後で
`AssertionResult` の出力が一致することを確認する parity テストを追加します。

## 検討した代替案

- **`EvalContext` だけを導入し、モジュールは平坦なままにする。** 5 層にわたる引数の重複はなくなるので
  部分的な改善にはなりますが、god module の問題は残ります。selector の解決、ネットワークタイムラインの
  照合、画像の座標計算やピクセル I/O、schema のファイル I/O が、依然として 1 つのファイルに同居したままです。
  それぞれを独立に読み、テストし、変更できるようにするのはパッケージ分割のほうであり、コンテキストの束ね
  だけでは 2 つの問題のうち浅いほうしか解決しません。

- **モジュールは分割し、コンテキストは緩い引数のままにする。** 裏返しの理由で退けます。パッケージ分割で
  各ファイルの役割は明確になりますが、コンテキストを伴う assertion の種別を新設するたびに、依然として
  5 つのシグネチャを書き換える作業が残ります。どちらの単位も行う価値があり、それぞれが本項目のなかで独立した
  小さな PR として投入できる規模です。

- **registry の辞書ではなく、plugin または protocol インターフェースへ assertion の評価を再設計する。** 課題の
  大きさに対して重すぎるとして退けます。action の種別ですでに実証済みの `_HANDLERS` の辞書パターンのほうが
  単純で、新しい抽象を覚える必要もなく、すべての assertion の種別にクラスインターフェースの実装を強いる
  こともなく、評価をただの関数のままにしておけます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `bajutsu/assertions.py` を `assertions/` パッケージ（`network.py`、`visual.py`、`schema.py`、
      `evaluate.py`）へ分割し、既存の公開インターフェースを re-export する。
- [x] `visual_context`、`schema_context`、`golden_context`、`clipboard` を 1 つの `EvalContext` に束ね、
      `evaluate` → `evaluate_one` → `run_scenario` → `_run_step_body` → `pipeline.py` まで端から端で渡す。
- [x] `evaluate_one` の 14 分岐の `if` 連鎖を `{field_name: eval_fn}` の registry に置き換える
      （`bajutsu/orchestrator/actions/_registry.py` の `_HANDLERS` を踏襲）。
- [x] `_ASSERTION_KINDS` を手動保守のタプルではなく `Assertion.model_fields` から導出する。

### ログ

- ユニット 1（パッケージ分割）— `bajutsu/assertions.py` を `bajutsu/assertions/`（`_common`、`network`、
  `visual`、`schema`、`evaluate`）へ分割し、公開インターフェースを `__init__` から re-export しました。
  振る舞いは不変で、既存の assertion / network テスト群と re-export・非循環を確認するガードがパリティの
  網です。PR は [#1093](https://github.com/bajutsu-e2e/bajutsu/pull/1093) です。
- ユニット 2（EvalContext）— frozen な `EvalContext(visual, schema, golden, clipboard)` を `assertions/evaluate.py`
  に定義し `__init__` から re-export して、これまで `evaluate` / `evaluate_one` / `_evaluate_expect` /
  `run_scenario` / `_run_step_body` / `pipeline.py` を一括で貫いていた 4 つの独立したキーワード引数を置き換えました。
  `clipboard` は reader ではなく解決済みの値のままで、`_clipboard_for` がブロック単位で 1 度だけ読む従来の遅延性を
  保ちます（ポーリングループで読み直しません）。step の assert は golden と clipboard だけを受け取り続けます
  （`visual` / `responseSchema` にはステップ単位のスクリーンショットが存在しないため）。この非対称性は、その呼び出し
  箇所で当該 2 フィールドを落とすことで維持します。振る舞いは不変で、既存の assertion / network / loop テスト群に
  frozen 性・フィールド振り分け・step での drop を確認する新規ガードを加えたものがパリティの網です。
  PR は [#1100](https://github.com/bajutsu-e2e/bajutsu/pull/1100) です。
- ユニット 3（ディスパッチ registry）— `evaluate_one` の 14 分岐の `if a.X is not None` 連鎖を、`assertions/evaluate.py`
  の `_EVALUATORS: dict[str, _Evaluator]` registry へ置き換えました。各種別は `@_evaluator(kind)` で自身を登録し、
  `_ASSERTION_KINDS` を走査して唯一設定されたフィールドで振り分けます（`orchestrator/actions/_registry.py` の
  `_HANDLERS` / `_handler` / `_action_of` を踏襲）。各エントリは自身のフィールドを `assert a.X is not None` で絞り込み、
  変更していない種別ごとの `_eval_*` へ委譲する薄いアダプタです。これにより 1 つの辞書が strict な型付けのまま全種別を
  保持します。シナリオ検証が種別をちょうど 1 つに保証するため走査順は結果に影響せず、振る舞いは不変です。既存の
  assertion テスト群に、registry が全種別を過不足なく網羅することを確認するガードを加えたものがパリティの網です。
  PR は [#1106](https://github.com/bajutsu-e2e/bajutsu/pull/1106) です。
- ユニット 4（種別タプルの導出）— `scenario/models/_base.py` で手動保守していた `_ASSERTION_KINDS` タプルを、
  `tuple(f for f in Assertion.model_fields if f != "from_")` に置き換えました。定義は `Assertion` モデルの隣、
  `scenario/models/assertions.py` へ移し、`ASSERTION_KINDS` として re-export します（`Step` モデルから導出する
  `_STEP_ACTIONS` を踏襲）。これにより新しい assertion 種別の追加は、モデルへのフィールド追加と registry への登録
  だけで済み、並行するタプルの手編集が不要になります。導出したタプルは従来の手動タプルと一致し、フィールドの宣言順も
  保たれるため、振る舞いは不変です。導出の一致を確認する新規ガードがパリティの網です。
  PR は [#1113](https://github.com/bajutsu-e2e/bajutsu/pull/1113) です。

## 参考

- [BE-0172](../BE-0172-run-loop-step-decomposition/BE-0172-run-loop-step-decomposition-ja.md)
  （隣接する run 経路の分解。ステップループとシナリオ単位の実行関数を対象としており、本項目は同じ決定的な
  `run` 経路のうち assertion 評価側にあたる兄弟の分解です）
- `bajutsu/assertions.py`（970 行。本項目が分割するモジュール）
- `bajutsu/orchestrator/actions/_registry.py`（本項目が assertion の dispatch に踏襲する、既存の
  `_HANDLERS` という dispatch registry のパターン）
- `bajutsu/scenario/models/_base.py:30`（`_ASSERTION_KINDS`。本項目がモデルからの導出に置き換えることを
  提案する、手動保守のタプル）

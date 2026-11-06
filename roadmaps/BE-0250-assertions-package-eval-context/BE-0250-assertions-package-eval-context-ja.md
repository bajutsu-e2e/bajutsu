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

`bajutsu/assertions.py` は 970 行あり、5 つの役割が同居しています。5 つが同じファイルにある理由は、どれも最後に `AssertionResult` を返すという一点だけです。

- dispatcher にあたる `evaluate` と `evaluate_one`
- 種別ごとの UI evaluator
- ネットワークタイムラインの照合（web のモックルータや `until: {request}` の待機とも共有します）
- `visual` assertion 向けの画像前処理
- `responseSchema` 向けの JSON Schema の読み込みと検証

本項目は、挙動を保存するリファクタリングで、2 つの部分からなります。1 つ目は、このモジュールを上の境界に沿ってパッケージへ分割することです。2 つ目は、`visual_context`、`schema_context`、`golden_context`、`clipboard` という種別ごとのコンテキストを 1 つの `EvalContext` に束ねることです。現状では、この 4 つのコンテキストを 5 層にわたって個別の引数で受け渡しています。変更後は、コンテキストを必要とする assertion の種別を新設しても、5 か所を書き換える必要がなくなります。

## 動機

現状の 1 モジュールは、互いにほとんど関係のない 5 つの関心事を担っています。selector の解決、ネットワークタイムラインの照合、画像の座標計算とピクセル I/O、JSON Schema のファイル読み込みとパストラバーサル対策、そしてトップレベルの dispatch です。
これは、コードベースがすでに実践している単一責任の考え方と食い違います。`bajutsu/orchestrator/actions/` は action の handler をグループごとに分けています。`bajutsu/visual.py` は画素比較エンジンを単独で持っています。具体的には、モジュールに次の部品が同居しています。

- **dispatcher。** `evaluate`、`evaluate_one`（847〜970 行）です。`if a.X is not None: return _eval_X(...)` という分岐の連なりと、`request` の 1 対 1 割り当てを担います。
- **種別ごとの UI evaluator。** `_eval_exists`、`_eval_text`、`_eval_count`、`_eval_state`（133〜202 行）と、`_eval_request`、`_eval_event`、`_eval_request_sequence`（278〜418 行）です。どれも、`query()` のスナップショットやネットワークタイムラインに対する、小さく純粋なチェックです。
- **ネットワークの照合。** assertion だけでなく、web のモックルータや `until: {request}` の待機も、このコードを共有します。構成は、`match_request`、`count_matching`、`request_label`（205〜275 行）と、二部グラフの `_assign_requests`（497〜519 行）です。後者は Kuhn のアルゴリズムによる増加道探索を使います。これにより、広い `request` matcher が、より限定的な matcher の唯一の exchange を奪うことがありません。
- **約 240 行の画像前処理サブシステム。** 座標計算、クロップ、マスク、Pillow によるファイル I/O を行います。対象は `_visual_scale`、`_frame_to_px`、`_resolve_mask`、`_prepare_visual_comparison`、`_resolve_masks`、`_resolve_baselines`、`_eval_visual`（537〜780 行）です。`shutil` と `PIL.Image` を直接 import するため、UI とネットワークの assertion を扱うはずのモジュールに、画像処理の依存が入り込んでいます。
- **JSON Schema のファイル読み込み、パスの閉じ込めチェック、検証。** `_load_schema` と `_validate_instance`（421〜494 行）が担います。

per-run のコンテキストを必要とする assertion の種別を新設する作業も、現状はあちこちに手を入れる必要があります。各コンテキストが、5 つの層にまたがって、独立した緩い引数として受け渡されているためです。

- `evaluate`（914〜923 行）と `evaluate_one`（847〜856 行）は、`visual_context`、`schema_context`、`clipboard`、`golden_context` という 4 つの keyword-only 引数を、それぞれ個別に持ちます。
- `bajutsu/orchestrator/loop.py` の `run_scenario`（164〜168 行）と `_run_step_body`（107〜118 行）も、同じ 4 つに `mailbox` を加えた引数を、個別のシグネチャ引数として持ちます。
- `bajutsu/runner/pipeline.py`（100〜105 行、138〜162 行）は、3 つのコンテキストオブジェクトをそれぞれ個別に構築してから、すべて下へ渡します。

したがって、コンテキストを伴う assertion の種別を 1 つ新設するだけで、5 か所に手が入ります。`Assertion` モデル、`_ASSERTION_KINDS`（`bajutsu/scenario/models/_base.py:30`）、新しい `_eval_X` 関数、14 分岐の `if` 連鎖への新しい分岐、そして 5 つのシグネチャすべてへの新しい引数です。フィールド 1 つぶんの追加のために、これだけの書き換えが要ります。

assertion の判定結果や、観測可能な run の挙動は変わりません。assertion は決定的な Tier-2 のゲート上で合否を決めるものであり（プライムディレクティブ 1）、本提案は構造上の変更にとどまります。分割の前後で `AssertionResult` が一致することを証明する parity テストで、これを検証します。

## 詳細設計

作業は、互いに独立した次の 4 単位に MECE に分かれます。いずれも挙動を保存し、単独で投入できます。

- **`bajutsu/assertions.py` を `assertions/` パッケージへ分割する。**
  - **`assertions/network.py`**：`match_request`、`count_matching`、`request_label`、`_assign_requests`、`_request_assignment_result` を置きます。最後の `_request_assignment_result`（`assertions.py:522`）は、`evaluate_one` で `_assign_requests` と並んで呼ばれ、結果を `request_label` から組み立てます。
    web のモックルータや `until: {request}` の待機は、すでに `match_request` と `count_matching` に依存しています。つまり共有の matcher なので、`bajutsu/network.py` の `NetworkExchange` の近くに置くほうがふさわしいとも言えます。循環 import が生じなければ、どちらの配置でもかまいません。実装 PR で明示的に判断します。
  - **`assertions/visual.py`**：`_visual_scale`、`_frame_to_px`、`_resolve_mask`、`_shift`、`_Prepared`、`_prepare_visual_comparison`、`_resolve_masks`、`_resolve_baselines`、`_eval_visual`、`VisualContext`、`VisualEvidence` を置きます。画素比較エンジンを持つ `bajutsu/visual.py` の隣に置く考え方です。両者で、visual assertion に関わるものを一括して担います。
  - **`assertions/schema.py`**：`SchemaContext`、`_load_schema`、`_validate_instance`、`_eval_response_schema` を置きます。
  - **`assertions/evaluate.py`**：薄い dispatcher（`evaluate`、`evaluate_one`、`passed`）を置きます。独立したモジュールを持つほどではない、種別ごとの UI evaluator も置きます。対象は `_eval_exists`、`_eval_text`、`_eval_count`、`_eval_state`、`_eval_request`、`_eval_event`、`_eval_request_sequence`、`_eval_clipboard`、`_eval_golden` です。
    `GoldenContext`（`assertions.py:105`）も、ここに置きます。唯一の evaluator である `_eval_golden` の隣です（比較ロジックは、すでに `bajutsu/golden.py` から import しています）。独立モジュールにはしません。
  - `bajutsu/assertions.py`（あるいは `assertions/__init__.py`）が、公開インターフェースを re-export します。既存の `from bajutsu import assertions` や `bajutsu.assertions.evaluate(...)` の呼び出し側には、影響しません。純粋なモジュールの再編成であり、公開 API の変更ではありません。

- **per-run のコンテキストを 1 つの `EvalContext` に束ねる。** frozen な `EvalContext` を導入します。フィールドは 4 つで、`visual: VisualContext | None`、`schema: SchemaContext | None`、`golden: GoldenContext | None`、`clipboard_reader: Callable[[], str | None] | None` です。フィールドの正確な形は、実装時に詰めます。
  現状、`clipboard` は `_clipboard_for` 経由で遅延読み込みされます。ブロックに `clipboard` assertion があるときだけ読み込む仕組みです。置き換え後もこの遅延性を保ち、毎ステップ読み込むようにしてはいけません。

  この `EvalContext` を 1 つだけ、`evaluate` → `evaluate_one` → `run_scenario` → `_run_step_body` → `pipeline.py` での構築という経路の端から端まで受け渡します。これにより、5 層それぞれで重複していた、4 つ個別の引数を取り除きます。

- **`evaluate_one` の 14 分岐の `if` 連鎖を、データ駆動の registry に置き換える。** `_ASSERTION_KINDS` をキーにした `{field_name: eval_fn}` の対応表を導入します。
  `bajutsu/orchestrator/actions/_registry.py` は、action の dispatch に `_HANDLERS: dict[str, ActionHandler]` という registry をすでに使っています。handler は `@_handler(kind)` で自己登録し、`_do_action` が dispatch します。この仕組みを踏襲します。
  dispatch は、`is not None` の連鎖ではなく参照になります。種別の追加は、連鎖への分岐追加ではなく、関数の登録で済みます。

- **`_ASSERTION_KINDS` を `Assertion` モデルから導出する。** 現状、`bajutsu/scenario/models/_base.py` のタプルは手動で保守されています。これを `tuple(f for f in Assertion.model_fields if f != "from_")` に置き換えます。
  `bajutsu/orchestrator/actions/_registry.py` の `_RUNTIME_ACTIONS`（`tuple(a for a in STEP_ACTIONS if a != "use")`）に、既存の先例があります。こちらも `Step` モデルから導出されています。
  assertion の種別を追加する作業は、`Assertion` への新しいフィールドと registry への登録だけで済みます。手動保守のタプルを編集する必要はなくなります。

各単位は、単独で `make check` を緑に保ちます。assertion の観測可能な挙動は変えません。分割の前後で `AssertionResult` の出力が一致することを確認する parity テストを、各単位で追加します。

## 検討した代替案

- **`EvalContext` だけを導入し、モジュールは平坦なままにする。** 5 層にわたる引数の重複はなくなるので、部分的な改善にはなります。しかし、god module の問題は残ります。selector の解決、ネットワークタイムラインの照合、画像の座標計算やピクセル I/O、schema のファイル I/O が、1 つのファイルに同居したままです。
  それぞれを独立に読み、テストし、変更できるようにするのは、パッケージ分割のほうです。コンテキストを束ねるだけでは、2 つの問題のうち浅いほうしか解決しません。

- **モジュールは分割し、コンテキストは緩い引数のままにする。** 裏返しの理由で退けます。パッケージ分割で、各ファイルの役割は明確になります。しかし、コンテキストを伴う assertion の種別を新設するたびに、5 つのシグネチャを書き換える作業が残ります。
  どちらの単位も行う価値があります。それぞれが、本項目のなかで独立した小さな PR として投入できる規模です。

- **registry の辞書ではなく、plugin または protocol インターフェースへ assertion の評価を再設計する。** 課題の大きさに対して重すぎるとして退けます。action の種別で実証済みの `_HANDLERS` の辞書パターンのほうが、単純です。新しい抽象を覚える必要もありません。インターフェースを採ると、すべての assertion の種別にクラスの実装を強いることになります。辞書パターンなら、評価をただの関数のままにしておけます。

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

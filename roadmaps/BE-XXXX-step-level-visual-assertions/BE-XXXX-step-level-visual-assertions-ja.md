[English](BE-XXXX-step-level-visual-assertions.md) · **日本語**

# BE-XXXX — ステップ内のビジュアルリグレッションアサーション

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-step-level-visual-assertions-ja.md) |
| 提案者 | [@handle](https://github.com/handle) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Verification & coverage |
| 関連 | [BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions-ja.md)、[BE-0165](../BE-0165-visual-compare-engines/BE-0165-visual-compare-engines-ja.md)、[BE-0171](../BE-0171-element-scoped-visual-assertions/BE-0171-element-scoped-visual-assertions-ja.md)、[BE-0250](../BE-0250-assertions-package-eval-context/BE-0250-assertions-package-eval-context-ja.md) |
<!-- /BE-METADATA -->

## はじめに

既存の`visual`アサーション(BE-0029)を拡張します。シナリオ末尾の`expect:`ブロックだけでなく、
ステップ内の中間検証`assert:`ブロックでも動作させます。比較は引き続き決定的なピクセル差分です。
baseline画像と照合し、`run`/CIのゲートにAIは介在しません。

## 動機

Bajutsuは決定的な`visual`アサーション種別(BE-0029)をすでに実装しています。比較エンジンを選択
できる機能(BE-0165)と、要素単位のスコープ指定(BE-0171)も備えています。しかし現状は、シナリオ
末尾の`expect:`ブロックでしか動作しません。この`expect:`ブロックの直前で、`_capture_visual_actual`
がスクリーンショットを1回だけ撮影します。

ステップ自身の`assert:`ブロック(中間検証)は、`expect`と同じ`Assertion`スキーマを受け付けます。
そのため`assert: [{ visual: { baseline: "modal.png" } }]`とステップ内に書いても、YAMLの
バリデーションは通ります。しかし`bajutsu/common/orchestrator/loop/_functions.py`のステップ
レベル`assert_`分岐は、評価のたびにvisualコンテキストを落とします。この挙動は
`tests/orchestrator/test_loop.py`が意図的な非対称性として固定しています(BE-0250 Unit 2)。
結果として、比較は画面の実際の内容にかかわらず、常に「no visual context provided」と報告します。

この非対称性は、著者に2つの代償を強います。1つ目は、シナリオ途中の画面を検証できないことです。
モーダルが開いた直後、次のステップが閉じる前に、その描画を確認したい場合があります。現状では、
その検証を最後の画面へ移すためだけに、1つのシナリオを複数へ分割するしかありません。2つ目は、
この制約が執筆時には見えないことです。スキーマは`assert:`内の`visual`を受け入れます。シナリオの
ドキュメントは`assert`が`expect`とドメイン固有言語（DSL）を共有すると説明するだけで、しかも
`visual`セクション自体の例がすでに`- assert:`の下に書かれています。この例外について、著者への
警告はどこにもありません。そのため著者は実行時になって初めてこの制約に気づきます。しかもその
メッセージは、著者が対処できる原因を何も示しません。

出荷後は、`visual`エントリを含む`assert:`ブロックが、シナリオ内のその時点で撮影したスクリーン
ショットと実際にピクセル比較します。画面の内容にかかわらず常に
「no visual context provided」で失敗する状態ではなく、その比較結果によって合否が決まります。

## 詳細設計

BE-0029、BE-0165、BE-0171がすでに用意した`VisualMatch`スキーマ、`compare_images`によるピクセル
差分エンジン、各バックエンドの`driver.screenshot()`をそのまま再利用します。新しいアサーション
種別も、新しい`Driver`メソッドも必要ありません。

- **ステップレベルの`assert_`分岐で`visual=None`を強制するのをやめます。** 代わりに、
  `dataclasses.replace(cfg.ctx.visual, prefix=…, screenshot_path=…)`で、ステップ専用の
  `VisualContext`を構築します。条件は、ステップの`assert:`リストに`visual`エントリがあり、
  かつ`_StepRunner`が持つルーティング先の実行コンテキスト(`cfg.ctx`)がすでに1つ持っている
  場合です。この条件は、`_capture_visual_actual`が`expect`側で適用する`ctx.visual is not None`
  と同じゲートです。固定の実行レベルコンテキストではなく`cfg.ctx`から導くことで、第二の
  ターゲットを指すステップも、そのターゲット自身のbaselineと比較できます(BE-0428)。
  `bajutsu/common/orchestrator/loop/_step_runner.py`の`_StepRunner._handle_action`は、
  すでにステップのエビデンスprefix(`step_id`)を計算している唯一の場所です。3か所ある呼び出し
  箇所のそれぞれで、このprefixとステップの`StepOutcome.index`を`_run_step_body`へ渡す必要が
  あります。`_poll_asserts`の変更は不要です。撮影は`_poll_asserts`の実行より前に済んでいる
  からです。`SchemaContext`は同じ分岐で引き続き落とします。BE-0250 Unit 2がここで落としたのは
  リファクタリング前の挙動を保つためであり、ステップレベルのassertに`responseSchema`が読む
  ネットワーク交信が存在しないからではありません。この扱いを見直すのは別の変更であり、
  本提案の対象外とします。
- **`visual`エントリを含むステップの`assert:`ブロックごとに、スクリーンショットをちょうど
  1回だけ撮影します。** タイミングは`_poll_asserts`の実行直前です。これにより、そのブロック内の
  すべての`visual`エントリが、同じ1枚の撮影結果と比較されます。末尾の`expect`ブロックが今日
  行っている方式と同じです。`_evaluate_expect`もすでに同じ方式です。`_capture_visual_actual`
  経由で、`expect:`ブロックの評価前に1回だけ撮影します。`_poll_asserts`はすでに`visual`を
  `_READ_ONCE_KINDS`の1つとして扱っており、各ポーリングで再読み込みされるのはUIツリーだけです。
  そのため、ステップレベルの検証はティックごとに新しいスクリーンショットを必要としません。
- **ステップごとのスクリーンショットの保存先を、そのステップ自身のエビデンスprefixと
  outcomeインデックスに限定します。** `write_screenshot`はすでにステップの`after.png`に
  このprefixを使っていますが、ステップ名は一意である必要がありません。そのため`for_each`の
  中にある名前付きステップは、反復のたびに同じprefixを再利用してしまいます。`StepOutcome.index`
  は実行全体で一意なので(ステップカウンタが全体で共有されるため)、このindexで保存先を1階層
  深くします。これにより、あるステップのvisualキャプチャは、シナリオ自身の`visual-actual.png`
  とも、同じステップの別の実行とも衝突しません。1回の実行内でのリトライ(TipKitのリトライや
  alert guardのリトライ)は同じindexを再利用するため、ステップが保持する結果は、その実行の
  最終試行のピクセルを指したままになります。
- **`expect`のvisualキャプチャがすでに適用している、タッチマーカーの一時停止と通知バナーの
  クリアを踏襲します。** ステップレベルのキャプチャも`_capture_visual_actual`を経由させます。
  `_capture_visual_actual`は、`_hides_touch_markers`がシナリオのタッチマーカー描画を検出した
  場合に`capability_suspended`でマーカーを一時停止し、先に通知バナーもクリアします。しかし
  `_run_step_body`は、実行のcollectorである`channel`と`hide_markers`フラグのどちらも今は
  受け取っていません。今日はどちらも`run_scenario`だけが持つローカル変数です。どちらも
  `_LoopConfig`(とターゲットごとの構築処理)まで届く必要があります。バナー除去で消費した
  アクチュエーションは、`RunResult.dropped_expect_actuations`ではなく、そのステップ自身の
  `StepOutcome`に記録する必要があります。除去処理が`expect`だけでなく、ステップの内部でも
  動くようになるからです。最後に、`run --touch-markers`がどのシナリオにマーカーチャンネルを
  与えるかを決めている`_visual_asserting_scenarios`(`bajutsu/run/cli.py`)は、今日は`expect`と
  シナリオ最上位の`steps`しか見ていません。`capability_preflight._assertions_with_path`と
  同じように、あらゆるフェーズと、`if`・`for_each`・`web`にネストしたステップまで含む、
  ステップツリー全体を走査する必要があります。そうしなければ、`visual`アサーションがネストした
  ステップの中にしかないシナリオは、必要なチャンネルを得られません。
- **旧挙動を固定しているテストを更新します。** `tests/orchestrator/test_loop.py`の
  `test_step_level_assert_drops_visual_context`(BE-0250 Unit 2で追加)を、新しい挙動を検証する
  内容に更新します。ステップレベルの`visual`アサーションが実際に比較すること、
  `responseSchema`は引き続き落とされることの両方をテストします。
- **変更内容と、それが残す既知のギャップの両方をドキュメント化します。** `docs/scenarios.md`の
  `assert`セクション、`visual`セクション、通知バナーの捕捉箇所一覧を更新します。`expect`フェーズ
  のvisualキャプチャに関する`docs/architecture.md`の記述も更新します。`docs/ja/`の対応する
  ページも合わせて更新します。`bajutsu approve`と、レポートのbaseline/actual/diffストリップは、
  まだステップ自身の`assertion_results`を読まないことを明記します(次項)。そのため、ステップ
  レベルの検証の最初のbaselineは、手動でのコピーが必要になります。
- **`bajutsu approve`とレポートのvisualストリップには手を付けず、そのことを明記します。**
  `approve`(`bajutsu/serve/cli/approve.py`)は、シナリオの`expect_results`しか走査しません。
  レポートのbaseline/actual/diffストリップとApproveボタン(`bajutsu/common/report/rows.py`の
  `_visual_row`)も、`expect`側の行にしか描画されません。どちらも、ステップの
  `assertion_results`は見ていません。著者は、ステップレベルの検証の最初のbaselineを手動で
  用意します。baselineがまだ存在しない場合でも、撮影結果はそのステップ自身のprefixの下で
  実行のエビデンスに残ります。著者はそのファイルを、`baseline:`フィールドが指定する名前で
  baselineディレクトリへコピーします。今日`approve`が`expect`の撮影結果に対して行っている
  コピーと同じ操作です。`approve`とレポートをステップレベルの結果に対応させること自体は独立
  した作業であり、本提案には含めず、別の項目に委ねます。

## 検討した代替案

- **`assert:`内の`visual`を、サポートする代わりにスキーマまたはlintで拒否する。** この方法は
  見えない罠を取り除きますが、根本的な制約は残ります。シナリオ途中でのビジュアル検証を本当に
  必要とする著者は、最後の画面ではない画面を比較するためだけに、1つのシナリオを複数へ分割し
  続けることになります。シナリオの他の事情は、この分割を求めていません。
- **`_poll_asserts`の各ティックでスクリーンショットを撮り直し、`value`や`exists`と同じように、
  落ち着いていく画面に対してステップレベルの`visual`検証をリトライさせる。** この方式は、
  1回だけ撮影する`expect`から外れます。得られる利点もほとんどのレーンで小さいものです。
  ポーリング自体の待機予算は、レーンの待機下限(`BAJUTSU_MIN_WAIT_TIMEOUT`)であり、レーンが
  それを設定しない限りゼロです。画面がまだ落ち着いていない場面での検証は、`expect`がすでに
  行っているのと同じ方法、つまり手前に`wait`を置くことで対応できます。下限を設定したレーンで
  ティックごとにデバイスとの往復が1回増える代償だけで済みます。
- **ステップレベルのvisual検証に、`expect`とは別のbaselineディレクトリ名前空間を与える。**
  baselineは、著者が付けた名前(`baseline: <file>.png`)ですでにキー付けされています。その検証が
  `expect`と`assert`のどちらから実行されたかでは区別されません。したがって新しい名前空間は
  不要です。ステップレベルの検証も、今日の`expect`と同じように、自分のbaselineファイル名を
  指定するだけで済みます。
- **`bajutsu approve`とレポートのvisualストリップも、この項目の中で拡張する。** スコープの
  観点から見送ります。この拡張は、上記のorchestrator loopの変更に加えて、
  `bajutsu/serve/cli/approve.py`と`bajutsu/common/report/rows.py`(とそのテンプレート)にまで
  踏み込みます。*詳細設計*にある手動コピーの回避策によって、本提案自身のスコープは、著者が
  他に回避しようのない部分、つまり比較そのものに留められます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] ステップレベルの`assert_`分岐で`visual=None`を強制するのをやめる。ステップのエビデンス
      prefixと`StepOutcome.index`を、`_StepRunner._handle_action`から`_run_step_body`へ渡す。
      ステップの`assert:`リストに`visual`エントリがある場合、ルーティング先の実行が持つ
      `cfg.ctx`から、ステップ専用の`VisualContext`を構築する。
- [ ] `visual`エントリを含むステップの`assert:`ブロックごとに、`_poll_asserts`の実行直前に
      スクリーンショットをちょうど1回だけ撮影する。
- [ ] ステップごとのスクリーンショットの保存先を、そのステップ自身のエビデンスprefixと
      `StepOutcome.index`に限定し、シナリオの`visual-actual.png`や別の実行のキャプチャと
      衝突しないようにする。
- [ ] `channel`と`hide_markers`を`_LoopConfig`まで届くようにし、ステップレベルのキャプチャが
      `_capture_visual_actual`のタッチマーカー一時停止と通知バナーのクリアを再利用できるように
      する。バナー除去で消費したアクチュエーションは、そのステップ自身の`StepOutcome`に記録する。
- [ ] `_visual_asserting_scenarios`(`bajutsu/run/cli.py`)が、`expect`と最上位の`steps`だけで
      なく、ステップツリー全体を走査するようにする。`run --touch-markers`が、ネストした
      ステップレベルの`visual`アサーションにもマーカーチャンネルを与えられるようにするため。
- [ ] `tests/orchestrator/test_loop.py`の`test_step_level_assert_drops_visual_context`を新しい
      挙動に合わせて更新する。単発撮影の挙動、indexによる保存先の切り分け、タッチマーカーと
      バナーの再利用、`responseSchema`が引き続き落とされることのテストを追加する。
- [ ] `docs/scenarios.md`(`assert`、`visual`、バナー捕捉箇所一覧)と`docs/architecture.md`に、
      変更内容と手動baselineコピーのギャップを記載し、`docs/ja/`の対応するページも更新する。

## 参考

- [BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions-ja.md) —
  本提案が新しい評価箇所へ拡張する`visual`アサーション種別。
- [BE-0165](../BE-0165-visual-compare-engines/BE-0165-visual-compare-engines-ja.md) —
  ステップレベルの検証がそのまま再利用する、選択可能な比較エンジン。
- [BE-0171](../BE-0171-element-scoped-visual-assertions/BE-0171-element-scoped-visual-assertions-ja.md)
  — ステップレベルの検証がそのまま再利用する、要素スコープとセレクタによるマスキング。
- [BE-0250](../BE-0250-assertions-package-eval-context/BE-0250-assertions-package-eval-context-ja.md)
  — Unit 2のログが記録する「ステップレベルのassertは`visual`/`responseSchema`を落とす」という
  決定を、本提案は`visual`に限って見直す。
- `bajutsu/common/orchestrator/loop/_functions.py` — 本提案が変更する`_run_step_body`の
  `assert_`分岐。`_poll_asserts`は変更せずそのまま再利用する。
- `bajutsu/common/orchestrator/loop/_step_runner.py` — `_StepRunner._handle_action`。ステップの
  エビデンスprefixとoutcomeインデックスを、すでに計算している唯一の場所。
- `bajutsu/common/orchestrator/loop/_loop_config.py` — `_LoopConfig`。ステップレベルのキャプチャ
  に必要な`channel`と`hide_markers`のフィールドを追加する。
- `bajutsu/run/cli.py` — `_visual_asserting_scenarios`。本提案がステップツリー全体まで拡張する。
- `docs/scenarios.md` — 本提案が更新する`assert`（中間検証）と`visual`（ビジュアルリグレッション）
  のセクション。

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
| 関連 | [BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions-ja.md)、[BE-0171](../BE-0171-element-scoped-visual-assertions/BE-0171-element-scoped-visual-assertions-ja.md)、[BE-0250](../BE-0250-assertions-package-eval-context/BE-0250-assertions-package-eval-context-ja.md) |
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
レベル`assert_`分岐は、評価の前にvisualコンテキストを常に落とします。この挙動は
`tests/orchestrator/test_loop.py`が意図的な非対称性として固定しています(BE-0250 Unit 2)。
結果として、比較は画面の実際の内容にかかわらず、常に「no visual context provided」と報告します。

この非対称性は、著者に2つの代償を強います。1つ目は、シナリオ途中の画面を検証できないことです。
モーダルが開いた直後、次のステップがそれを閉じる前に、正しく描画されているかを確認したくても、
本当に必要な検証を最後の画面へ移すためだけに、1つのシナリオを複数へ分割するしかありません。
2つ目は、この制約が執筆時には見えないことです。スキーマは`assert:`内の`visual`を受け入れます。
シナリオのドキュメントも、`assert`が`expect`とDSL（ドメイン固有言語）を共有すると説明するだけで、この例外を明記
していません。そのため著者は実行時になって初めてこの制約に気づきます。しかもそのメッセージは、
著者が対処できる原因を何も示しません。

出荷後は、`visual`エントリを含む`assert:`ブロックが、シナリオ内のその時点で撮影したスクリーン
ショットと実際にピクセル比較します。画面の内容にかかわらず常に
「no visual context provided」で失敗する状態ではなく、その比較結果によって合否が決まります。

## 詳細設計

BE-0029、BE-0165、BE-0171がすでに用意したものを、そのまま再利用します。`VisualMatch`スキーマ、
`compare_images`によるピクセル差分エンジン、各バックエンドの`driver.screenshot()`、baseline
承認ワークフロー(`bajutsu approve`)です。新しいアサーション種別も、新しい`Driver`メソッドも
必要ありません。

- **ステップレベルの`assert_`分岐で`visual=None`を強制するのをやめます。** 代わりに、ステップ
  の`assert:`リストに`visual`エントリがあり、かつ実行のコンテキストがすでにbaseline設定を持つ
  場合に限り、ステップ専用の`VisualContext`を構築します。この条件は、`_eval_context_for`が
  visualアサーションの設定有無を判定する際の`ctx.visual is not None`と同じゲートです。
  `responseSchema`は同じ分岐で引き続き落とします。これはキャプチャ済みのネットワーク交信を読む
  種別であり、ステップレベルのassertには相当するキャプチャが存在しないという別の制約による
  ものです。本提案はこの制約に触れません。
- **ステップレベルのvisual検証ごとに、スクリーンショットをちょうど1回だけ撮影します。** タイミング
  は`_poll_asserts`の実行直前です。`_evaluate_expect`が`_capture_visual_actual`経由で`expect:`
  ブロックの評価前に1回だけ撮影する方式と同じです。`_poll_asserts`はすでに`visual`を
  `_READ_ONCE_KINDS`の1つとして扱っており、各ポーリングで再読み込みされるのはUIツリーだけです。
  そのため、ステップレベルの検証はティックごとに新しいスクリーンショットを必要としません。
  むしろ毎回撮影すると、デバイスとの往復が増えます。本来決定的であるはずの検証時間も、実行の
  たびにばらつきます。
- **ステップごとのスクリーンショットの保存先を、そのステップ自身のエビデンスprefixに限定します。**
  `write_screenshot`がすでにステップの`after.png`に使っているprefixと同じものです。この結果、
  あるステップのvisualキャプチャは、シナリオ自身の`visual-actual.png`とも、別のステップの
  キャプチャとも衝突しません。同じステップが複数回実行される場合(リトライや`for_each`の反復)
  も同様です。
- **タッチマーカーの一時停止と通知バナーのクリアを踏襲します。** `expect`のvisualキャプチャは
  すでに`_hides_touch_markers`でマーカーを一時停止し、
  `_clear_notification_banner_before_visual_capture`で通知バナーをクリアしています。ステップ
  レベルのキャプチャも両方を再利用し、どちらにも汚染されません。
- **旧挙動を固定しているテストを更新します。** `tests/orchestrator/test_loop.py`の
  `test_step_level_assert_drops_visual_context`(BE-0250 Unit 2で追加)を、新しい挙動を検証する
  内容に更新します。ステップレベルの`visual`アサーションが実際に比較すること、
  `responseSchema`は引き続き落とされることの両方をテストします。
- **変更内容をドキュメント化します。** `docs/scenarios.md`と`docs/ja/scenarios.md`の`assert`
  セクションを更新します。著者が試行錯誤で、これまでの制約を発見する必要がないようにします。

## 検討した代替案

- **`assert:`内の`visual`を、サポートする代わりにスキーマまたはlintで拒否する。** この方法は
  見えない罠を取り除きますが、根本的な制約は残ります。シナリオ途中でのビジュアル検証を本当に
  必要とする著者は、最後の画面ではない画面を比較するためだけに、1つのシナリオを複数へ分割し
  続けることになります。シナリオの他の事情は、この分割を求めていません。
- **`_poll_asserts`の各ティックでスクリーンショットを撮り直し、`value`や`exists`と同じように、
  落ち着いていく画面に対してステップレベルの`visual`検証をリトライさせる。** `visual`はすでに
  `_READ_ONCE_KINDS`に含まれています。UIツリーの再読み込みは、ピクセル比較の結果を変えません。
  そのため、繰り返し撮影してもカバレッジは増えず、コストだけが増えます。しかも、本来決定的
  であるべき検証の実行時間が、実行のたびにばらつきます(prime directive 2)。
- **ステップレベルのvisual検証に、`expect`とは別のbaselineディレクトリ名前空間を与える。**
  baselineは、著者が付けた名前(`baseline: <file>.png`)ですでにキー付けされています。その検証が
  `expect`と`assert`のどちらから実行されたかでは区別されません。したがって新しい名前空間は
  不要です。ステップレベルの検証も、今日の`expect`と同じように、自分のbaselineファイル名を
  指定するだけで済みます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] ステップレベルの`assert_`分岐で`visual=None`を強制するのをやめ、ステップの`assert:`に
      `visual`エントリがあり、実行がbaseline設定を持つ場合は、ステップ専用の`VisualContext`を
      構築する。
- [ ] ステップレベルのvisual検証ごとに、`_poll_asserts`の実行直前にスクリーンショットをちょうど
      1回撮影する。`expect`がすでに適用しているタッチマーカーの一時停止と通知バナーのクリアを
      踏襲する。
- [ ] ステップごとのスクリーンショットの保存先を、そのステップ自身のエビデンスprefixに限定し、
      シナリオの`visual-actual.png`や他のステップのキャプチャと衝突しないようにする。
- [ ] `tests/orchestrator/test_loop.py`の`test_step_level_assert_drops_visual_context`を新しい
      挙動に合わせて更新し、単発撮影の挙動と`responseSchema`が引き続き落とされることのテストを
      追加する。
- [ ] `docs/scenarios.md`と`docs/ja/scenarios.md`の`assert`セクションに変更内容を記載する。

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
  `assert_`分岐と`_poll_asserts`。
- `docs/scenarios.md` — 本提案が更新する`assert`（中間検証）と`visual`（ビジュアルリグレッション）。
  のセクション。

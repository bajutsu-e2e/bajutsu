[English](BE-0259-assert-query-snapshot-reuse.md) · **日本語**

# BE-0259 — assert と extract のステップで、確定した query のスナップショットを使い回す

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0259](BE-0259-assert-query-snapshot-reuse-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0259") |
| 実装 PR | [#1083](https://github.com/bajutsu-e2e/bajutsu/pull/1083) |
| トピック | コードベース品質・技術的負債 |
| 関連 | [BE-0172](../BE-0172-run-loop-step-decomposition/BE-0172-run-loop-step-decomposition-ja.md)、[BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance-ja.md) |
<!-- /BE-METADATA -->

## はじめに

ステップループ（`bajutsu/orchestrator/loop.py`）は、画面を変化させない[ステップ](../../docs/ja/glossary.md#シナリオのオーサリング)が必要とする回数を超えて、アクセシビリティツリーを取得することがありました。取得には `driver.query()` を使います。

BE-0234（Implemented、#987）は、この隙間の大半をすでに解消しています。ループのステップ後の読み取りは遅延実行になりました。`screenChanged` キャプチャ、付随する `extract`、wait タイムアウトの診断のいずれかがツリーを必要とするときだけ、読み取りが走ります。ループは前のステップの `after` を次のステップの `before` としても使い回します（`loop.py` の `BE-0234 Unit 2` コメント）。これらが付随しない素の `assert` は、二重に query しません。

残る隙間は、より狭いものです。画面を変化させないステップ（`assert`、またはそれに付随する `extract`）は、ステップ後の読み取りを発火させることがあります。その時点で `_run_step_body` は、ステップを評価するためにツリーを解決して query 済みで、その後に画面は変化していません。それでもステップ後の読み取りは、ツリーをもう一度取得します。

本項目は、確定済みのスナップショットを再取得せずに受け渡します。同じ発想を 1 層下にも適用します。単一の `tap` が、ステップループの下に重複した取得を積み重ねている箇所です。

## 動機

`bajutsu/orchestrator/waits.py:64` は、実機バックエンドでのコストを記しています。idb の `describe-all` コマンド（idb ドライバの `query()` が実行する query）は、1 回あたりおおよそ 100〜300ms かかります。呼び出しのたびに、Simulator へのサブプロセス経由の往復が発生するためです。インプロセスの参照ではありません。

`assert` ステップで状態を確認するシナリオは、作用する前に状態を読む通常の書き方です。ステップ後の読み取りが発火する場合、このシナリオは追加の情報を何も得ないまま、このコストを 2 回払います。発火するのは、`assert` に `screenChanged` キャプチャか `extract` が付くときです。`_run_step_body` は、`assertions.evaluate` を実行した時点で、解決済みの要素一覧をすでに保持しています。その呼び出しとループ自身のステップ後の `query()` のあいだに、画面を変化させるものはありません。

同じ構造は、単一の `tap` の内部でも積み重なります。

- idb [ドライバ](../../docs/ja/glossary.md#driver-backend-actuator-platform)（`bajutsu/drivers/idb.py:397`）では、`_center` が `_settle()` を呼びます。このコストは query 1 回分で、ツリーの identifier と frame の組が安定していなければ、さらに複数回かかります。
- 続けて `_center` は `_resolve`（`idb.py:376`）を呼びます。セレクタが最初の試行で見つからないと、`_resolve` は 3 秒のデッドラインまで、自前のポーリングで再度 query します。
- `tap` は、`base.wait_until`（`base.py:359`）の内側から呼ばれることが多くあります。`base.wait_until` もデッドラインとポーリングのループです。そのため、不安定な 1 回の tap に、独立した 2 つのデッドライン管理が付き、それぞれ自分のスケジュールで query を重ねます。
- adb ドライバでは、`_scroll_into_view`（`bajutsu/drivers/adb.py:369`）が、スクロールの試行ごとに `_settle()` をもう 1 回追加します（`adb.py:379`）。画面外の要素までスクロールが必要な tap は、概念上は 1 つの操作です。それでも十数回のツリー全体取得のサブプロセスが発生することがあります。

ここまでの内容は、シナリオが何をアサートするかを変えるものではありません。避けられる遅延が、`assert` を多用するすべての run と、対象が画面外にあるすべての tap に生じています。

## 詳細設計

これは**挙動を保存する**リファクタリングです。ステップのアサーションと extract が見る要素は、これまでと同じ要素で、取得回数だけが減ります。セレクタの解決、アサーションの意味、wait と poll の条件、ドライバのインターフェースには、変更を加えません。作業は、互いに独立した次の 3 単位に MECE に分かれます。

- **画面を変化させないステップについて、`_run_step_body` の query 結果をループの `after` スナップショットとして使い回します。**
  - `_run_step_body` を変更します（`bajutsu/orchestrator/loop.py`。関数は 141 行目から、`assert_` の query は 172 行目です）。既存の `(ok, reason, assertion_results)` に加えて、`assert_` で query した要素一覧を返します。`wait` が query するなら、その要素一覧も返します。
  - `exec_steps` では、ステップの種類が画面を変化させえないものであるとき、返されたスナップショットを `after` として使います。BE-0234 で遅延化されたステップ後の読み取りは、2 回目の `query()` を呼ばなくなります。
  - `tap`、`type`、`swipe` などのアクションステップは、実行後に画面が実際に変わっている可能性があるため、引き続き実行後に新しく query します。本単位が取り除くのは、「前」と「後」が同一の query であると証明できるステップの、余分な 2 回目の query だけです。
  - `extract` 修飾子（`bajutsu/scenario/models/steps.py:134`）は、追加の作業なしにこの変更の恩恵を受けます。画面を変化させないステップに付く場合は、使い回されたスナップショットを読みます。画面を変化させるステップに付く場合は、これまでどおり実行後の新しい query を読みます。

- **tap の実行過程で、層ごとに再 query する代わりに、確定した 1 つのスナップショットを受け渡します。**
  - idb ドライバと adb ドライバでは、`_center`（`idb.py:397`）が `_settle()` を呼んでから `_resolve`（`idb.py:376`）を呼びます。`_resolve` は、キャッシュミス時に自前で再度 query します。
  - `_resolve` を変更し、`_settle()` がすでに生成したツリーを、再試行の全体を通じて優先的に使うようにします。最初の試行用の `initial_tree` 引数は、すでにあります（`idb.py:376`）。
  - `_center` を変更し、最初に見つからなかった時点で `_resolve` にもう一度 query させる代わりに、確定したツリーを渡します。
  - 外側の `base.wait_until` のデッドラインループ（`base.py:359`）は変更しません。本単位が取り除くのは、隣接する層がすでに手元に持つデータと同じデータのために、下位の層が直後にもう一度実行していた query だけです。

- **query の回数が減ったことを確認する回帰テストを追加します。**
  - `FakeDriver`、または `query()` の呼び出し回数を数える計測用のラッパーを使います。
  - `assert` ステップ 1 つだけからなるシナリオで、`query()` の呼び出しが最初から最後まででちょうど 1 回になること（現在は 2 回）を、テストで確認します。
  - もう 1 つのテストでは `tap` ステップを対象にし、アクションステップが影響を受けないことを確認します。画面が変わっている可能性があるため、実行後の query は引き続き発生します。

## 検討した代替案

- **`query()` の結果を、ポーリングの反復をまたいで壁時計時間ベースでキャッシュする（たとえば生存時間（TTL、time to live）の短いキャッシュを設ける）。** 退けます。時間ベースのキャッシュは、画面が実際に変化したあとでも古いスナップショットを返しえます。`wait` や `assert` ステップが、実機を反映していないデータを読む可能性があり、プライムディレクティブ 2 が守る決定性を、気づかれないまま弱めます。本提案の使い回しは、もっと狭く、構造的に安全です。あるステップがすでに実行した query と証明可能に同一の query について、その結果を差し替えるだけです。差し替えはステップ 1 つの生存期間に限られ、ポーリングやステップをまたぎません。
- **ループを現状のまま残し、`waits.py:64` に明記されたコストを受け入れる。** 退けます。このコストはよく理解されており、意味を変えずに避けられます。`assert` を多用するシナリオと、スクロールを伴うすべての tap で積み重なります。本リポジトリは、CI とローカルの run 時間のコストを、他の場面でも低く保とうとしています。`docs/ai-development.md` の right-sizing の考え方は、計算資源について同じ原則を述べています。本項目は、その原則を壁時計時間に適用します。
- **ドライバの query インターフェースを、無効化を追跡する永続的なツリーキャッシュへ再設計する。** スコープ外として退けます。この変更は、`Driver` インターフェースと、すべてのバックエンド（idb、adb、Playwright）に及び、無効化の正しさそのものに固有のリスクも抱えます。本提案の狭い範囲の純粋な追加による使い回しは、よくあるケースの効果をすでに得ています。将来、汎用的なキャッシュを求める提案が出るなら、本項目が追加する query 回数の回帰テストの上に構築するべきです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 画面を変化させないステップ（`assert_` と `wait`、および付随する `extract`）について、
      `_run_step_body` の query 結果をループの `after` スナップショットとして使い回す。
- [x] idb ドライバと adb ドライバの `_center`/`_resolve` で、各層ごとに再 query する代わりに、
      確定した 1 つのスナップショットを受け渡す。*（既存のコードですでに満たされています。下のログを参照してください。）*
- [x] `assert` ステップで query の回数が減ったこと（およびアクションステップが影響を受けない
      こと）を確認する回帰テストを追加する。

### ログ

- **Unit 1 — 完了。** `_run_step_body` は、画面を変化させないステップについて、query した確定済みのツリーを返すようになりました。対象は `assert_` と、`_wait` と `_wait_settled` が最後にポーリングしたツリーを返す `wait` です。ステップループは、そのツリーで `_ScreenRead` を種として初期化します。`screenChanged`、`extract`、wait 診断、キャプチャの各利用側は、同じ query をもう一度発行せず、スナップショットを使い回します。画面を変化させるステップやツリーを読まないステップ（`tap`・`type` など、`email`、`wait until: request`）は `None` を返します。そのため、ステップ後の読み取りは新しい query のままで、挙動は保存されます。（`bajutsu/orchestrator/loop.py`、`bajutsu/orchestrator/waits.py`。）
- **Unit 2 — すでに満たされており、変更は不要でした。** 本単位が対象としていた tap 時の重複した query は、もう存在しません。`_center`（idb）と `_resolve_frame_and_screen`（adb）は、`_settle()` が生成したツリーを `_resolve(..., initial_tree=tree)` に渡しています。tap は確定したツリー上で解決され、通常の経路では 2 回目の query が発生しません。idb は BE-0087（`settle before actuation`）以降これを行っており、adb の同等の対応は BE-0210 で入りました。`adb._scroll_into_view` にスクロールごとに残る `_settle()` は、画面が動いたあとの本来必要な再読み取りで、重複ではありません。そのため、そのまま残しています。
- **Unit 3 — 完了。** `tests/orchestrator/test_read_count.py` に BE-0259 のケースを追加しました。
  - `assert`+extract のステップは、`query()` をちょうど 1 回だけ発行します（従来の 2 回から減少）。
  - `screenChanged` ポリシー下の `assert` は、2 回で済みます（従来の 3 回から減少）。
  - `wait for`+extract のステップは、確定したツリーを使い回します。
  - アクションステップは、引き続きステップ後に新しい `after` を読み取ります。画面を変化させるステップが種として使われないことの正しさの確認です。

## 参考

- [BE-0172 — run 経路のステップループとシナリオ実行関数の分解](../BE-0172-run-loop-step-decomposition/BE-0172-run-loop-step-decomposition-ja.md)
  （本項目が最適化する run 経路のステップループ。すでに名前付きヘルパへ分解済み）
- `bajutsu/orchestrator/loop.py` — `_run_step_body`（141 行目）、`assert_` の query（172 行目）、
  `exec_steps`、および遅延化されたステップ後の `after` 読み取り（BE-0234 Unit 2）
- `bajutsu/orchestrator/waits.py:64`（idb の describe-all が 1 回あたり約 100〜300ms であることを
  記した箇所）
- `bajutsu/drivers/idb.py:297`（`_settle`）、`:376`（`_resolve`）、`:397`（`_center`）
- `bajutsu/drivers/adb.py:369`（`_scroll_into_view`）
- `bajutsu/drivers/base.py:359`（`wait_until`）

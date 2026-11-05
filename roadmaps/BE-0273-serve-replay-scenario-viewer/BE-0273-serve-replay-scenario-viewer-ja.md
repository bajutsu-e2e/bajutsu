[English](BE-0273-serve-replay-scenario-viewer.md) · **日本語**

# BE-0273 — Replay タブからシナリオの内容を生 YAML と構造化ステップで確認する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0273](BE-0273-serve-replay-scenario-viewer-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0273") |
| 実装 PR | [#1131](https://github.com/bajutsu-e2e/bajutsu/pull/1131) |
| トピック | オーサリング体験 |
| 関連 | [BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view-ja.md) |
<!-- /BE-METADATA -->

## はじめに

serve Web UI の **Replay** タブは、シナリオを選んで実行する場所です。タブでは、シナリオを「選ぶ」ことと「実行する」ことはできますが、「その中身を見る」ことはできません。実行ボタンを押す前に見えるのは、シナリオファイル先頭の `description` と、各シナリオの `name`／`description` だけです。これから実行するシナリオの本体にあたるステップ、セレクタ、アサーションは見えません。

この提案は、Replay から到達できる読み取り専用の **シナリオビューア** を追加します。「View scenario」の操作で、選択中のシナリオの内容を **生の YAML** として開き、**構造化したステップ表示** へ切り替えられるようにします。ビューアは読み取り専用で、判定に関与しない補助的な画面です。シナリオを編集することはなく、`run`／CI の判定経路にも一切入りません。

これは、束縛中の **config** に対して同じ読み取り専用ビューア（生 YAML と構造ツリー）を追加した
[BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view-ja.md) の、シナリオ版に相当します。

## 動機

Replay は、実行対象を「選んで起動する」タブです。ところが、起動しようとしているものの中身を見られない唯一の場所になっています。現在の情報ボックス（`bajutsu/templates/serve.panels.js` の `showInfo()`）が描画するのは、`/api/scenarios` の一覧が返す description だけで、ステップは描画しません。

そのため、実行前のごく普通の問いに、UI の中で答えられません。このシナリオはどのセレクタをタップするのか。確認したい内容はすでにアサートされているか。ステップは何個あるのか。答えるには、UI を離れてディスク上の YAML を開く必要があります。もう 1 つの経路は、Author タブに切り替えて、独立したピッカーからシナリオを選び直す方法です。この手間は、中身の確認が最も必要になる次の 3 つの場面で、特に大きくなります。

- **不慣れなシナリオを実行する前。** 他人が書いたシナリオや、Git ソースやアップロードした zip の config から展開されたシナリオが該当します。このときディスク上のパスは、中身のわからない内容アドレス方式のキャッシュ位置です。実行を 1 回消費する前に、そのシナリオが何をするかを UI 上で確認する手段がありません。
- **triage のとき（BE-0147）。** Replay からは「なぜ実行が失敗したか」を見られます。しかし、失敗したステップを生んだシナリオ本体は、その画面を離れないと読めません。
- **determinism のグレードを読むとき（BE-0145）。** Replay の Form は、選択中のシナリオをすでに採点し、たとえば「ステップ 3 は壊れやすいセレクタを使っている」と報告します。ところが、そのステップ 3 をそこから見ることはできません。

サーバ側は、この機能をすでに備えており、Replay が使っていないだけです。エンドポイント `GET /api/scenario?target=&path=`（`bajutsu/serve/handler.py`、`bajutsu/serve/operations/reads.py` の `ops.read_scenario`）は、シナリオの `{"yaml": …}` をすでに返しています。現在これを呼んでいるのは、Record タブ（作成直後の YAML を表示するため）と Author エディタだけです。同じ内容を、シナリオを「実行対象として選ぶ」場所に出せば、ほとんど新しいバックエンドなしで、「実行するものを見る」ギャップを埋められます。BE-0187 が config に対して行ったことの、そのまま鏡写しです。

## 詳細設計

Replay タブに、読み取り専用のビューアを配線します。`run`／CI の経路には一切触れません（プライムディレクティブ 1）。ビューアは Tier-1 の補助的な画面であり、アプリ非依存でもあります（プライムディレクティブ 3）。アクティブな config が公開するシナリオをそのまま読むだけで、アプリごとの分岐はありません。

- **既存の読み取りエンドポイントと、そのランナー由来の既存のステップ抽出を再利用します。** ビューアは、選択中のシナリオ本体を既存の `GET /api/scenario?target=<t>&path=<p>`（`{"yaml": …}` を返す）から取得します。生 YAML の表示に、新しいサーバルートは不要です。

  構造化ステップ表示には、このエンドポイントが**すでに持っている**抽出を再利用します。`read_scenario` は、`_step_artifacts` と `_step_action_fields` が組み立てる `steps` フィールドを返します。この 2 つは、`load_scenario_file(...).scenarios`（ランナー自身の解析）と `Step.model_dump(...)` を呼んで、ステップごとの `{action, fields}` を導出します（`bajutsu/serve/operations/reads.py`、[BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md) 由来）。

  現状、エンドポイントが `steps` を返すのは、`run_id` が渡されたときだけです。同時に計算する**実行由来**の情報（`elementsUrl` と `screenshotUrl`）が、完了した run のマニフェストを必要とするためです。構造抽出そのものは、マニフェストを必要としません。したがって作業は、`steps` の**構造部分の gate を外し**、事前の実行なしでランナー由来のステップ構造を返せるようにすることです。実行由来の URL は、`run_id` 依存のままにします。構造化表示は、ランナーの解析に忠実なまま保てます。それがランナーの解析そのものであり、第二のパーサーを持ち込まないからです（「検討した代替案」を参照）。

- **Replay の Form に「View scenario」の操作を置きます。** 既存のシナリオ `<select>`、情報ボックス、グレードバッジ（`bajutsu/templates/serve.html.j2`、`bajutsu/templates/serve.panels.js`）の隣に、選択中のシナリオのビューアを開く操作を追加します。操作は、シナリオが選択されていればいつでも有効で、事前の実行を必要としません。シナリオを読むことは、実行成果物に依存しないからです。この点は、`runId` を必要とする BE-0262 のステップピッカーとは異なります。

- **生 YAML と構造化を切り替えるモーダルまたは専用ペインを置きます**（BE-0187 のパターンに合わせます）。
  - **生 YAML。** エンドポイントが返すシナリオファイルの本文を、読み取り専用の等幅でスクロール可能なブロックに表示します。これが正典の表示で、実行されるものとバイト単位で一致します。
  - **構造化ステップ。** エンドポイントのランナー由来の `steps`（上記）から、シナリオごとに人が読める一覧を描画します。各シナリオの `name` と `description` に続けて、順序どおりのステップをコンパクトに描画します。アクション、セレクタまたはターゲット、主要な引数を並べます（たとえば `tap { id: nav.replay }`、`assert exists { id: … }`）。これは「何をするか」をざっと把握するための表示です。正確なソースが必要なら、切り替え 1 つで生 YAML に戻れます。
  - **読み取り専用の動作。** ビューアでは、編集も保存も再束縛もできません。編集は Author タブの役目のままです。将来のフォローアップとして「Author で開く」リンクを足す余地はありますが、この項目は意図的に閲覧で止めます（「検討した代替案」を参照）。

- **フロントエンドのモジュール配置。** BE-0202 のモジュール化以降、Replay タブの JS は `bajutsu/templates/serve.panels.js`（Record、Replay、Triage）にあり、マークアップは `bajutsu/templates/serve.html.j2` にあります。ビューアと切り替えは、そこに追加します。BE-0187 の config ビューアやテーマエディタが、すでに使っている serve のモーダルまたは保持ペインの慣習に従います。オーバーレイの重なりと閉じ方を、一貫させるためです。

- **テスト ID と dogfood カバレッジ。** View scenario の操作、ビューアのコンテナ、生と構造化の切り替え、描画された内容のそれぞれに、安定した `data-testid` を付けます。`demos/serve-ui/scenarios/replay-tools.yaml` の隣に、dogfood E2E シナリオを追加します。そのシナリオは、ビューアを開き、YAML の本文が表示されることをアサートし、構造化表示に切り替え、既知のステップが現れることをアサートします。これは、新しい画面に対する Web バックエンド（Playwright）の回帰ネットです。既存の Replay dogfood フィクスチャ（BE-0058 と BE-0189）にそろえます。

- **ドキュメント。** `docs/architecture.md`（と `docs/ja/` の対応）の serve の節を更新し、Replay が選択中のシナリオの内容を読み取り専用で出すようになったことを記します。

## 検討した代替案

- **Replay 内ビューアではなく、Replay から Author エディタへのリンクにする案。** 主案としては採りません。Author は**エディタ**であり、変更可能な状態と独自のシナリオピッカーを持ちます。「これから実行するものをちょっと見たい」という要求には重く、ユーザーを Replay の文脈から連れ出してしまいます。読み取り専用ビューアなら、実行前に確認するループを Replay の中に保てます。「Author で開く」リンクは、妥当な任意のフォローアップ候補ですが、ここでは範囲外とします。
- **生 YAML だけを出し、構造化表示は置かない案。** この最小版は、`{"yaml": …}` を表示するだけです。最小のコードで核心のギャップを埋められます。ただし、長いシナリオをざっと読めるようにするのは構造化ステップ表示であり、determinism グレードのステップ単位のフィードバック（BE-0145）とも自然に組み合わさります。この項目は、両方を使えるよう切り替えを採用します。範囲を絞る必要があれば、生 YAML を先に出し、構造化表示をフォローアップに回すのが自然な分割です。
- **構造化表示をクライアント側で導出する案（YAML を JS で再解析する）。** 機能を純粋にフロントエンドへ閉じる案として検討しましたが、採りません。ブラウザ側にシナリオ構造の**第二の**パーサーを持ち込むことになり、ランナーが実際にシナリオを解析するやり方からずれる恐れがあるためです。サーバ側には、この問題をすでに避ける仕組みがあります。`read_scenario` の `steps` は、ランナー自身の `Step` モデル（`_step_artifacts` と `Step.model_dump`、BE-0013 由来）から組み立てられるので、これを再利用することはランナーの解析そのものです。現状エンドポイントが `steps` を `run_id` で gate しているのは、同時に付ける実行由来の `elementsUrl` と `screenshotUrl` のためだけです。構造部分の gate を外す（「詳細設計」を参照）ほうが、並行する JS パーサーより小さく、かつ忠実な変更です。「シナリオを描画する」**別建て**のエンドポイントを新設するのも、同様に不要です。抽出は、すでに `read_scenario` にあるからです。
- **BE-0187 に取り込む案。** 採りません。BE-0187 はすでに実装済みで、あくまで **config** のビューアです。シナリオは別の対象であり、入口（Replay のピッカー）も、構造化表示（config のキーではなくステップ）も異なります。本項目は、BE-0187 の UI パターンを再利用する兄弟項目であって、その拡張ではありません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] Replay の Form に「View scenario」の操作を追加する（`serve.html.j2`／`serve.panels.mjs` の
      マークアップと配線）。取得は既存の `GET /api/scenario` を使う。
- [x] 生 YAML と構造化ステップを切り替えるビューアのオーバーレイまたはペインを実装する（BE-0187 や
      テーマエディタのモーダルの慣習に従う）。
- [x] `read_scenario` の構造部分を `run_id` なしで返せるようにする。ビューアが指定するオプトインの
      `structure` クエリフラグを介して、事前の実行なしでランナー由来のシナリオごとのステップを返し、
      一方で Author エディタの run 未選択の読み込みは従来どおり素の `{yaml}` を受け取ります（実行由来の
      `elementsUrl`／`screenshotUrl` を伴う `steps` は `run_id` 依存のまま）。そこから構造化表示を描画します。
- [x] `data-testid` を付け、`demos/serve-ui/scenarios/replay-tools.yaml` の隣に dogfood E2E シナリオ
      （`demos/serve-ui/scenarios/replay-scenario-view.yaml`）を追加する。
- [x] `docs/architecture.md` とその `docs/ja/` の対応を更新する。

### ログ

- PR #1131 で実装：Replay の Form に読み取り専用のシナリオビューアを追加しました。「View scenario」の
  操作が config ビューア（BE-0187）を鏡写しにしたモーダルを開き、生 YAML と構造化ステップを切り替えられます。
  `read_scenario` にオプトインの `structure` フラグを足し、実行なしでランナーのシナリオごとの解析
  （`_step_action_fields` を再利用する `_scenario_structure`）を返します。既存の run 未選択の呼び出し側の
  応答はバイト単位で従来のままです。ユニットテストと HTTP テスト、Playwright の dogfood シナリオで担保します。

## 参考

- [BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view-ja.md)：これが鏡写しにする config
  ビューア（生 YAML と構造ツリー、読み取り専用、判定に関与しない）。
- [BE-0145](../BE-0145-serve-audit/BE-0145-serve-audit-ja.md)：Replay の Form にすでに出ている
  determinism グレード。そこで参照されるステップを、このビューアが見られるようにする。
- [BE-0147](../BE-0147-serve-triage/BE-0147-serve-triage-ja.md)：Replay／History からの triage。
  「なぜ実行が失敗したか」に対する「シナリオに何が書いてあるか」の補完。
- [BE-0202](../BE-0202-serve-js-modularization/BE-0202-serve-js-modularization-ja.md)：Replay タブの JS を
  `serve.panels.js` に配置した serve.js のモジュール化。
- [BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md)：ここで再利用する、ランナー
  由来のステップ単位の抽出（`_step_artifacts`／`Step.model_dump`）を導入した GUI エディタ。
- 既存エンドポイント：`GET /api/scenario?target=&path=`（`bajutsu/serve/handler.py`、
  `bajutsu/serve/operations/reads.py` の `ops.read_scenario`）。その `steps` フィールドは
  `_step_artifacts`／`_step_action_fields` が組み立てる。

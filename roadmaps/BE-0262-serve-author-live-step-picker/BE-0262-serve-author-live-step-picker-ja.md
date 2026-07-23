[English](BE-0262-serve-author-live-step-picker.md) · **日本語**

# BE-0262 — Author エディタにライブなステップ選択と target 単位に絞った run を導入する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0262](BE-0262-serve-author-live-step-picker-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0262") |
| 実装 PR | [#1134](https://github.com/bajutsu-e2e/bajutsu/pull/1134) |
| トピック | オーサリング体験 |
<!-- /BE-METADATA -->

## はじめに

Author タブの Edit モード（[BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md)）では、
スクリーンショットをクリックしてステップの selector を直せます。クリックは要素ツリーに対して解決され、選ばれた
selector が YAML に書き戻されます。ところが現状この流れは*過去の run*が残した成果物に完全依存します。ステップ一覧は
`/api/scenario?…&runId=…` から来るため、run を選ばないとステップは 0 件になり、ピッカーの解決呼び出し
（`/api/scenario/resolve`）は `runId` と `stepId` を必須とし、なければ `invalid or missing runId` を返します。これを
支える Run ドロップダウンは、**target やシナリオで絞らずハブ内の全 run** を並べます（`/api/runs`）。

結果として、一度も実行していないシナリオでは Edit の目玉であるスクリーンショット・ピッカーが使えず、run が存在しても
ピッカーは step id の噛み合わない無関係なシナリオの run を並べてしまいます。本項目は、過去の run なしでも Edit を使える
ようにし、run ピッカーを実際に噛み合うものだけに絞ります。

## 動機

Edit モードの約束は「シナリオを開き、画面をクリックし、selector を直す」です。しかし実際には、まずどこかでシナリオを
実行して成果物を作り、戻ってきて、グローバルな run 一覧が正しい run を出してくれることを祈る、という手順を踏まされます。
これを回避できる唯一の入口は、取り込んだばかりのシナリオをそのまま Edit に流す `auOpenSaved` です。ただし、その起点となる
Capture 自体が `[ios]` target で壊れています（姉妹提案のアクチュエータ選択で対処）。したがって大半のシナリオで、
平文編集に対する Edit の差別化機能であるスクリーンショット・ピッカーが機能しません。

2 つの修正で動くようにします。

1. **ステップ選択のライブな供給源。** Capture と同様に、Edit も実機ドライバを起動して現在のスクリーンショットと要素ツリーを
   取れます。これにより selector を凍結された過去の run ではなく*いま*に対して解決でき、前提となる run なしで動くピッカーが
   得られます。
2. **run ピッカーを絞る。** run を（特定の過去状態を確認するために）*使う*ときは、Run ドロップダウンは選択中の target と
   シナリオの run だけを並べるべきです。選んだ run の step id が読み込み中のシナリオと揃い、黙って噛み合わない事態を防ぎます。

いずれも判定経路には触れません。解決は、人が Apply して Save するための selector を提案するオーサリング支援であり、`run` は
決定的で AI を含まないままです。決定性とアプリ非依存性も影響を受けません（ライブドライバは target の config に従って選ばれ、
Capture と同じ選択を再利用します）。

## 詳細設計

1. **target/シナリオで絞った run 一覧。** Author の Run ドロップダウンを、現在の選択と target・シナリオが一致する run だけに
   絞ります。グローバル一覧のクライアント側フィルタより、サーバ側での絞り込み（`/api/runs` の scope 用クエリパラメータ、または
   Author 専用の runs エンドポイント）を優先し、ペイロードを小さく保ち、絞り込みの基準をサーバ側に一元化します。
2. **run なしのライブなステップ選択。** 選択中の target のドライバを起動し、スクリーンショットと query を取得し、画面
   クリックをライブなツリーに対して解決するライブ解決経路を Edit に追加します。ドライバの選択は共通のコスト順アクチュエータ選択を
   再利用し（姉妹提案のアクチュエータ選択を参照）、同じ `[ios]` クラッシュを再導入しません。Capture の `mark` 解決を写したものです。選ばれた
   selector は、run 由来の経路がすでに使う Apply → YAML 書き込みにそのまま流れます。
3. **モードの依存を UI で明示する。** run 未選択かつライブセッション未開始のとき、Edit の画面は無反応なプレースホルダを出す
   のではなく、ピッカーを得る方法（ライブセッションの開始）を示します。run 選択時は既存のステップごとのスクリーンショット
   ナビゲーションを保ちます。
4. **セッションと安全性の再利用。** capture セッションの仕組み（単一のアクティブセッション、actor ごとの所有、後片付け）を
   再利用し、ライブな Edit セッションがドライバを漏らしたり利用者間で衝突したりしないようにします。Capture のセッション管理と
   一貫します。
5. **テスト。** 絞った run 一覧のフィルタ（別シナリオの run が除かれる）、ライブ解決経路がクリックに対し selector を返す
   （stub の driver factory 経由）、run なし・セッションなしのプレースホルダ状態、を確認します。

## 検討した代替案

- **Edit のピッカーに run を前提とし続ける（現状）。** Edit を純粋な run 後の仕上げ道具に留めますが、それこそ本項目が取り除く
   摩擦です。run なしのケースは平文編集がすでに担うので、ライブで動けないならピッカーは何も足しません。
- **グローバル run 一覧のクライアント側フィルタだけ。** 単純ですが、全 run を毎回ブラウザに送ることになり、絞り込みの基準もクライアント実装任せのままです。
   サーバ側で絞った一覧のほうがきれいで小さいです。
- **必要時にシナリオを自動実行して成果物を作る。** 重く（オーサリングのクリック内で決定的な run を回す）、オーサリングと判定
   経路を混同します。selector の解決にはライブなスクリーンショットで足ります。

## 進捗

> 作業の進行に合わせて最新に保ってください。チェックリストは *詳細設計* の MECE な作業分解を反映し
> （作業単位ごとに 1 ボックス）、ログは変更内容と時期を（古い順に）記録し PR をリンクします。

- [x] Unit 1 — target/シナリオで絞った Author の run 一覧。
- [x] Unit 2 — ライブなステップ選択経路（ドライバ起動・スクリーンショット・クリック解決）。
- [x] Unit 3 — run なし・セッションなしの明示的な UI 状態。
- [x] Unit 4 — capture セッションのライフサイクルと所有の再利用。
- [x] Unit 5 — 絞り込み・ライブ解決・プレースホルダ状態のテスト。

### ログ

- [#1134](https://github.com/bajutsu-e2e/bajutsu/pull/1134) — 5 つのユニットを 1 本の PR で実装しました。Unit 1 は `/api/runs` をシナリオ名で絞ります
  （`runs_payload` のフィルタと `serve.author.mjs` の `auLoadRuns`）。Unit 2 は Capture のセッションを
  使ってライブなドライバを起動し、`POST /api/capture/resolve` から到達する `resolve_capture_pick`
  （actuate もステップ追加もしない純粋な解決）を追加します。run を選んでいないときは `read_scenario` が
  シナリオ YAML からステップ一覧を導出するので、一度も run していないシナリオでも直す対象のステップが
  そろいます。Unit 3 は run なしの不活性なプレースホルダに代えて、ライブセッションの始め方を示します。
  Unit 4 は単一セッションのスロットと actor 単位の所有を再利用し、保存せずに終了する `close_capture`
  （`POST /api/capture/close`）を加えます。Unit 5 は絞り込み・ライブ解決・プレースホルダ状態のテストを
  追加します。
- [#1137](https://github.com/bajutsu-e2e/bajutsu/pull/1137) — #1134 のレビューで挙がった非ブロッキングな
  2 点への追従です。`runs_payload` は DB の最新 50 件の上限を適用した*後*で run 一覧を絞っていたため、
  hosted で対象シナリオの run がその窓の外に落ちると拾えませんでした。シナリオを指定したときは上限なしで
  列挙し、絞り込んだ後に同じ窓へ改めて上限をかけるようにしました。あわせて、抽出した `_resolve_point` と
  `_feedback_payload` の型を `Any` から `CaptureResult` に絞りました。

## 参考

- [BE-0013 — Scenario GUI editor](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md)（拡張する Edit モード）
- [BE-0012 — Action-capture record](../BE-0012-action-capture-record/BE-0012-action-capture-record-ja.md)（再利用するライブセッションの仕組み）
- [BE-0098 — Unified authoring surface in serve](../BE-0098-unified-authoring-surface/BE-0098-unified-authoring-surface-ja.md)
- `bajutsu/templates/serve.author.js`（`auLoadRuns`・`auLoad`・`editResolve`）、`bajutsu/serve/operations/reads.py`（`resolve_scenario_pick`・`read_scenario`）

[English](BE-0267-serve-author-actuator-selection.md) · **日本語**

# BE-0267 — serve の capture・enrich でコスト順のアクチュエータ選択を再利用する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0267](BE-0267-serve-author-actuator-selection-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0267") |
| 実装 PR | [#1121](https://github.com/bajutsu-e2e/bajutsu/pull/1121) |
| トピック | オーサリング体験 |
<!-- /BE-METADATA -->

## はじめに

serve の Author タブにある Capture モード（[BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record-ja.md)）と
Enrich モード（[BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation-ja.md)）は、選択した target の
`backend` から実機ドライバを起動します。その起動は `bajutsu/serve/operations/_common.py` の
`_default_driver_factory` を経由し、backend の解決に `backends.select_actuator` を使います。この関数は
プラットフォームの*エイリアス*順（`PLATFORMS["ios"] = ("xcuitest", "idb")`、BE-0019）をたどるため、
`backend: [ios]` の target では常に XCUITest を先に返します。

[BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md)
は、*コスト順*でシナリオに応じた選択（`select_actuator_for_scenario`、`COST_ORDER["ios"] = ("idb", "xcuitest")`）を
導入しました。実行ラダーは安価な idb を優先し、シナリオの構成要素が要求するときだけ XCUITest に切り替えます。
ところが serve の capture・enrich 経路はこの仕組みを取り込んでいません。本項目はそのギャップを埋め、serve の
オーサリングが決定的な run 経路と同じ方法でアクチュエータを選ぶようにします。

## 動機

`backend: [ios]` の target（showcase の `showcase-swiftui` / `showcase-uikit`）で **Start capture** を押すと
クラッシュします。`select_actuator(["ios"])` は `xcuitest` を返し、続く `backends.make_driver("xcuitest", …)` が
`ValueError: xcuitest backend requires a runner_port (the runner must be started first)` を送出します。serve は
XCUITest の runner を起動しないためです。Enrich は同一の backend 選択ブロックを使うので、AI クレデンシャルを
設定すると同じ経路で失敗します。つまり Author の目玉機能である Capture が、showcase が「オーサリングの例」として
出荷しているまさにその target で動きません。

より深い問題は、**serve と run ラダーの乖離**です。BE-0240 は「iOS のオーサリングは runner 不要の idb を既定とし、
必要なときだけ切り替える」と定めました。run 経路はこれを守りますが、serve のオーサリングは黙って古いエイリアス順を
使い、唯一起動できないアクチュエータを選んでしまいます。capture・enrich のセッションは 1 台の実機を操作するだけで、
シナリオに応じた切り替えは要りません。しかし「起動できる中で最も安価なアクチュエータ」は必要で、それはコスト順が
すでに計算している値そのものです。この選択を再利用すれば、「CLI では動くのに Web UI ではクラッシュする」という一群の
驚きが消え、アクチュエータの選択が 1 箇所に集約されます。

これは見た目の整理ではなく機能不具合の修正で、`[ios]` target で Capture と Enrich を復旧させます。prime directive の
いずれにも抵触しません。`run`/CI の判定に LLM は入らず、決定性は変わらず、アクチュエータの選択は target ごとの config
駆動のままです。

## 詳細設計

単一アクチュエータの target（`[idb]`・`[adb]`・`[playwright]`）では挙動を保ち、候補が複数の target（`[ios]`）では
修正が効きます。

1. **`_default_driver_factory` で起動可能な最安アクチュエータを選ぶ。** 素の `select_actuator([backend])` を、
   `COST_ORDER` を尊重する選択に置き換えます。すなわち iOS では XCUITest より idb を優先し、`[ios]` のライブ
   capture・enrich が idb で起動するようにします。capture セッションにはシナリオがないため、シナリオ対応の
   `select_actuator_for_scenario` はそのままは使えません。`resolve_actuators` に `_cost_ordered` をかけた候補列を
   使い、利用可能な先頭を採ることで、シナリオなしでも BE-0240 の「最安優先」の意図を再現します。
2. **capture・enrich を共通の選択に通す。** `start_capture` と `start_enrich` は backend 列を同じ手順で組み立てます
   （`target_cfg.backend or config.defaults.backend` の `[0]`）。これを共通ファクトリに畳み込み、両者が `[0]` ではなく
   backend 列全体をセレクタに渡すようにします。これでコスト順がエイリアス先頭に固定されず、全候補に対して働きます。
3. **XCUITest が唯一の選択肢のときは到達可能に保つ。** `backend: [xcuitest]` を明示的に固定する target では、黙って
   idb に書き換えるのではなく「runner not started」という明確なエラーを出し続けます。選択が変えるのは複数候補の中で
   どれが勝つかだけで、単一の明示アクチュエータは従来どおり固定（BE-0240 の「単一アクチュエータ指定は固定」規則と
   一致）です。
4. **回帰テスト。** `[ios]` target が capture・enrich で idb に解決されること（既存テストが使う fake/stub の
   driver factory 経由）、および単一アクチュエータの target が不変であることを serve-operations のテストで確認します。

## 検討した代替案

- **serve から XCUITest runner をオンデマンドで起動する。** スコープが大きく（runner のライフサイクル、ポート、後片付け）、
  idb で足りるオーサリングには不要です。runner の起動をすでに所有する run 経路に委ねます。
- **serve だけで `ios` → `idb` を特別扱いする。** COST_ORDER が変わった途端に core と再び乖離する局所的なごまかしです。
  共通のコスト順を再利用すれば、判断の基準を 1 箇所に保てます。
- **capture・enrich をエイリアス順に固定したままエラーメッセージだけ直す。** クラッシュを読みやすい失敗に変えますが、
  `[ios]` target で Capture が動かないことは変わりません。原因ではなく症状への対処です（とはいえ読みやすいエラーを
  出すこと自体には価値があり、serve の未捕捉例外処理は別提案として切り出す価値があります）。

## 進捗

> 作業の進行に合わせて最新に保ってください。チェックリストは *詳細設計* の MECE な作業分解を反映し
> （作業単位ごとに 1 ボックス）、ログは変更内容と時期を（古い順に）記録し PR をリンクします。

- [x] Unit 1 — `_default_driver_factory` のコスト順選択（新設 `backends.select_actuator_cost_first`）。
- [x] Unit 2 — capture・enrich が backend 列全体を共通ファクトリに渡す。
- [x] Unit 3 — 明示的な単一アクチュエータ固定を保持（XCUITest のエラーを維持）。
- [x] Unit 4 — `[ios]` → idb と単一アクチュエータ不変の回帰テスト。

ログ:

- [#1121](https://github.com/bajutsu-e2e/bajutsu/pull/1121) — serve の capture・enrich でコスト順選択を
  再利用。`select_actuator_cost_first` を追加し、`_default_driver_factory` と capture・enrich を backend
  列全体で共通ファクトリに通す。

## 参考

- [BE-0240 — iOS capability-aware actuator selection](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md)（再利用するコスト順選択）
- [BE-0012 — Action-capture record](../BE-0012-action-capture-record/BE-0012-action-capture-record-ja.md)
- [BE-0014 — Demarcation from the existing AI record](../BE-0014-record-demarcation/BE-0014-record-demarcation-ja.md)
- [BE-0098 — Unified authoring surface in serve](../BE-0098-unified-authoring-surface/BE-0098-unified-authoring-surface-ja.md)
- `bajutsu/serve/operations/_common.py`、`bajutsu/serve/operations/capture.py`、`bajutsu/serve/operations/enrich.py`、`bajutsu/backends.py`

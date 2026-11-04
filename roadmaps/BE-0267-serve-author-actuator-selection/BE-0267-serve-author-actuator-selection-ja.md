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

serve の Author タブにある Capture モード（[BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record-ja.md)）と Enrich モード（[BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation-ja.md)）は、選択した target の `backend` から、実機ドライバを起動します。どちらのモードも、`bajutsu/serve/operations/_common.py` の `_default_driver_factory` というヘルパーを経由します。ヘルパーは、[backend](../../docs/ja/glossary.md#driver-backend-actuator-platform) の解決に `backends.select_actuator` を使います。

この関数は、プラットフォームの*エイリアス*順（`PLATFORMS["ios"] = ("xcuitest", "idb")`、BE-0019）をたどります。そのため、`backend: [ios]` の target では、常に XCUITest を先に返します。

[BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md) は、*コスト順*で、シナリオに応じた選択を導入しました。`select_actuator_for_scenario` と、`COST_ORDER["ios"] = ("idb", "xcuitest")` です。これにより、実行ラダーは、安価な idb を優先します。シナリオの構成要素が要求するときだけ、XCUITest に切り替えます。

ところが、serve の capture と enrich の経路は、この選択を取り込んでいません。本項目は、このギャップを埋めます。serve のオーサリングが、決定的な run 経路と同じ方法で、アクチュエータを選ぶようにします。

## 動機

`backend: [ios]` の target（showcase の `showcase-swiftui` と `showcase-uikit`）で **Start capture** を押すと、クラッシュします。

1. `select_actuator(["ios"])` が `xcuitest` を返します。
2. 続く `backends.make_driver("xcuitest", …)` が、`ValueError: xcuitest backend requires a runner_port (the runner must be started first)` を送出します。serve は XCUITest の runner を起動しないためです。

Enrich は、同一の backend 選択ブロックを使います。AI クレデンシャルを設定すると、同じ経路で失敗します。つまり、Author の目玉機能である Capture が、showcase が「オーサリングの例」として出荷している、まさにその target で動きません。

より深い問題は、**serve と run ラダーの乖離**です。BE-0240 は、「iOS のオーサリングは、runner が不要な idb を既定とし、必要なときだけ切り替える」と定めました。run 経路は、これを守ります。serve のオーサリングは、黙って古いエイリアス順を使い、唯一起動できないアクチュエータを選んでしまいます。

capture や enrich のセッションは、1 台の実機を操作するだけで、シナリオに応じた切り替えを必要としません。しかし、起動できるアクチュエータのうち最も安価なものは必要です。それは、コスト順がすでに計算している値そのものです。この選択を再利用すれば、「CLI では動くのに、Web UI ではクラッシュする」という一群の驚きが消えます。アクチュエータの選択も、1 箇所に集約されます。

これは、見た目の整理ではなく、機能不具合の修正です。`[ios]` target で、Capture と Enrich を復旧させます。プライムディレクティブのいずれにも、抵触しません。`run` や CI の判定に LLM は入らず、決定性は変わらず、アクチュエータの選択は、target ごとの config 駆動のままです。

## 詳細設計

単一アクチュエータの target（`[idb]`、`[adb]`、`[playwright]`）では、挙動を保ちます。候補が複数の target（`[ios]`）では、修正が効きます。

1. **`_default_driver_factory` で、起動可能な最安アクチュエータを選びます。** 素の `select_actuator([backend])` を、`COST_ORDER` を尊重する選択に置き換えます。iOS では、XCUITest より idb を優先します。これにより、`[ios]` のライブな capture や enrich が、idb で起動します。
   - capture セッションにはシナリオがないため、シナリオ対応の `select_actuator_for_scenario` は、そのままは使えません。
   - `resolve_actuators` に `_cost_ordered` をかけた、コスト順の候補列を使い、利用可能な先頭を採ります。これにより、シナリオなしでも、BE-0240 の「最安優先」の意図を再現します。
2. **capture と enrich を、共通の選択に通します。** `start_capture` と `start_enrich` は、backend 列を同じ手順で組み立てます。`target_cfg.backend or config.defaults.backend` の `[0]` です。この手順を共通ファクトリに畳み込み、両モードが、`[0]` ではなく backend 列全体をセレクタに渡すようにします。これで、コスト順は、エイリアス先頭に固定されず、全候補に対して働きます。
3. **XCUITest が唯一の選択肢のときは、到達可能に保ちます。** `backend: [xcuitest]` を明示的に固定する target では、黙って idb に書き換えず、「runner not started」という明確なエラーを出し続けます。選択が変えるのは、複数候補の中でどれが勝つかだけです。単一の明示アクチュエータは、BE-0240 の「単一アクチュエータ指定は固定」規則と一致して、従来どおり固定です。
4. **回帰テストを書きます。** serve-operations のテストで、次の 2 つの挙動を確認します。
   - `[ios]` target が、capture と enrich で idb に解決されます。既存のテストが使う、fake または stub の driver factory を経由します。
   - 単一アクチュエータの target が、不変です。

## 検討した代替案

- **serve から XCUITest runner をオンデマンドで起動する。** スコープが大きくなります。runner のライフサイクル、ポート、後片付けが必要です。idb で足りるオーサリングには、不要です。runner の起動をすでに所有する run 経路に、委ねます。
- **serve だけで `ios` → `idb` を特別扱いする。** `COST_ORDER` が変わった途端に、core と再び乖離する局所的なごまかしです。共通のコスト順を再利用すれば、判断の基準を 1 箇所に保てます。
- **capture と enrich をエイリアス順に固定したまま、エラーメッセージだけ直す。** クラッシュを読みやすい失敗に変えますが、`[ios]` target で Capture が動かないことは変わりません。原因ではなく、症状への対処です。読みやすいエラーを出すこと自体には価値があり、serve の未捕捉例外処理を別の提案として切り出す価値があります。

## 進捗

> 作業の進行に合わせて最新に保ってください。チェックリストは *詳細設計* の MECE な作業分解を反映し
> （作業単位ごとに 1 ボックス）、ログは変更内容と時期を（古い順に）記録し PR をリンクします。

- [x] Unit 1 — `_default_driver_factory` のコスト順選択（新設 `backends.select_actuator_cost_first`）。
- [x] Unit 2 — capture・enrich が backend 列全体を共通ファクトリに渡す。
- [x] Unit 3 — 明示的な単一アクチュエータ固定を保持（XCUITest のエラーを維持）。
- [x] Unit 4 — `[ios]` → idb と単一アクチュエータ不変の回帰テスト。

ログ:

- [#1121](https://github.com/bajutsu-e2e/bajutsu/pull/1121) で、serve の capture と enrich に、コスト順選択を再利用しました。`select_actuator_cost_first` を追加し、`_default_driver_factory` と capture、enrich を、backend 列全体でこの選択に通します。

## 参考

- [BE-0240 — iOS capability-aware actuator selection](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md)（再利用するコスト順選択）
- [BE-0012 — Action-capture record](../BE-0012-action-capture-record/BE-0012-action-capture-record-ja.md)
- [BE-0014 — Demarcation from the existing AI record](../BE-0014-record-demarcation/BE-0014-record-demarcation-ja.md)
- [BE-0098 — Unified authoring surface in serve](../BE-0098-unified-authoring-surface/BE-0098-unified-authoring-surface-ja.md)
- `bajutsu/serve/operations/_common.py`、`bajutsu/serve/operations/capture.py`、`bajutsu/serve/operations/enrich.py`、`bajutsu/backends.py`

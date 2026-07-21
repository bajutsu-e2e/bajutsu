[English](BE-XXXX-xcuitest-default-ios-backend.md) · **日本語**

# BE-XXXX — XCUITest を iOS のデフォルトバックエンドにし、idb を撤去する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-xcuitest-default-ios-backend-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md), [BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md) |
<!-- /BE-METADATA -->

## はじめに

XCUITest を iOS のデフォルトバックエンドにします。そして idb の唯一残る利点、すなわち Xcode ツールチェーンなしで動けることが不要になった時点で、idb を撤去します。idb の撤去は [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) が意図して下した判断を覆すので、変更は段階的に進めます。まずデフォルトを切り替え、すべての iOS run が明示的なオプトアウトのない限り XCUITest を使うようにします。次に、XCUITest が現在 idb の回すすべてのシナリオを網羅することを実機で確認し、ツールチェーン不要のパスが不要だと確定した時点で、idb をまるごと撤去します。到達点は iOS バックエンドを 1 つにすることで、途中の状態では idb を明示的なオプトアウトとしてのみ残します。

## 動機

きっかけは、ユーザーが Web UI で突き当たる具体的な欠落です。report と `serve` の要素ツリー表示は、iOS では `trait: group` のコンテナの子を示せません。タブバーは中身を持たない不透明なグループ 1 つとして描画されます。原因は idb の設計です。`idb ui describe-all` は VoiceOver 向けのアクセシビリティツリーを読みます。このツリーでは、それ自体が 1 つのアクセシビリティ要素であるコンテナ（`AXGroup`）は、子を隠す葉になります。そのため idb はその配下の要素をすべて落とします（idb の issue 767）。idb のどのオプションでもその子は復元できず、この制限はそのツリーを読むこと自体に内在します。

XCUITest にこの制限はなく、しかも他のあらゆる軸でより高機能です。XCUITest は XCTest の automation スナップショットを読み、コンテナの内側まで降りて子を 1 つずつ列挙します。だから、忠実に、完全に展開された要素ツリーを示せるのは XCUITest だけです。そのケイパビリティ集合は idb の厳密な上位集合で、セマンティックタップ・ネイティブの条件待ち・マルチタッチ・テキスト選択を加えます。そのため、能力を踏まえた解決器（[BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md)）は、シナリオがそれらのいずれかを必要とするたびに、すでに XCUITest へエスカレーションしています。読者は、その代償として XCUITest がアプリ側の統合を要求すると考えがちですが、そうではありません。ランナーは対象アプリをバンドル ID で起動する汎用ホストで（`XCUIApplication(bundleIdentifier:)`）、対象アプリのソースへ一切触れず、任意のアプリを操作します。

ケイパビリティと忠実さがどちらも XCUITest を後押しするなか、idb をデフォルトにとどめていた理由は 1 つ、コストだけでした。XCUITest ランナーはコールドな `xcodebuild test-without-building` の起動をシナリオごとに払っていました。[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) はこのコストを、idb をそっくり置き換える案の却下理由として挙げ、idb を安価な段とする 2 段のコスト順はしごを選びました。姉妹提案「シナリオをまたいで XCUITest ランナーを再利用する」は、ランナーを 1 デバイスにつき 1 つ常駐させ、シナリオの切り替え時にはアプリだけを再起動することで、このコストを取り除きます。起動はシナリオごとではなくデバイスごとに 1 回になります。この償却が入れば、idb をとどめるコスト上の論拠は成り立たなくなります。idb に残る唯一の正当化は、Xcode ツールチェーンが使えない環境で動けることです。idb はコマンドラインツールとコンパニオンだけで足りるのに対し、XCUITest は `xcodebuild` を必要とします。本提案は、この線引きに基づいてデフォルトを切り替え、idb をまるごと撤去する条件を定めます。

## 詳細設計

作業は段階に分け、デフォルトの切り替えと idb の撤去を分離できるようにし、撤去を思い込みではなく証拠で条件づけます。

### Unit 1 — デフォルトを XCUITest へ切り替える

iOS のデフォルトを変え、run が明示的なオプトアウトのない限り XCUITest に解決されるようにします。`Defaults.backend` を `["idb"]` の固定ピンから外し（`bajutsu/config/schema.py`）、idb を先頭に置くコスト順の選択（`bajutsu/backends.py` の `COST_ORDER["ios"]` と `select_actuator_for_scenario`）を iOS について廃止します。残るのは安定性順（`PLATFORMS["ios"] = ("xcuitest", "idb")`）だけです。上位集合である XCUITest がすでにデフォルトになれば、ケイパビリティによるエスカレーション（[BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md)）は iOS では何もしなくなるので、解決器は複雑になるのではなく単純になります。

### Unit 2 — フィクスチャと CI レーンを移行する

showcase の `-noax` ターゲットは、ランナーが用意されていないため今は idb に固定されています。汎用ランナーを用意して XCUITest で動くようにし、必須の `smoke (idb)` レーンと `E2E` レーンを、ランナーをプリビルド（`xcuitest.testRunner`）したうえで XCUITest へ張り替えます。プリビルドにより、CI は run ごとのビルドを払いません。この Unit は、移行を主張するだけでなく CI で証明する場です。

### Unit 3 — idb を前提とする箇所とドキュメントを更新する

idb がデフォルトである前提に立つコード経路、すなわちコスト順の分岐、XCUITest を idb に落とす doctor のフォールバック、ケイパビリティ preflight の idb 先行の前提を廃止します。あわせて、idb を iOS のデフォルトとして説明するドキュメント（`docs/drivers.md`、`docs/getting-started/ios.md`、およびそれらの日本語版）を、XCUITest を先頭に据える形へ更新します。

### Unit 4 — idb の最終形を決めて実行する

idb をまるごと撤去するか、明示的なオプトアウトとして残すかを、Unit 2 と Unit 5 の証拠、およびツールチェーン不要のパスを残すべきかの判断に基づいて選びます。まるごとの撤去では、`IdbDriver` とその周辺の idb 専用の面を削除します。すなわち、コンパニオンのバージョン監視（[BE-0005](../BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring-ja.md)）、idb がタブをアドレスできないためだけに存在するクロールのビジョンタブ探索、共有のコーディネートツリー読み取り経路にある idb の分岐を削り、`smoke (idb)` レーンを落とします。idb は多くのモジュールから参照されているので、この Unit はスケジュールする時点でそれ自体を分解します。本提案は 1 行ずつの撤去計画ではなく、到達点とゲートを記録します。

### Unit 5 — 実機での同等性の検証

撤去は証拠で条件づけます。名前を付けた環境（Simulator の機種と Xcode のバージョン）を用意します。そのうえで、現在 idb の `smoke` レーンと `E2E` レーンが網羅するすべてのシナリオが、ランナー再利用の償却を入れた XCUITest で通ることを確認します。あわせてスイート単位の実時間を記録し、移行のコストを思い込みではなく計測します。もし今 idb だけが回せるシナリオが見つかれば、それは Unit 4 を進める前にこの Unit が明らかにするブロッカーです。

## 検討した代替案

- **idb を撤去せず、恒久的なフォールバックとして残す。** idb を「デフォルトにはしないオプトアウト」として残すのはもっとも低リスクな選択で、まさに本提案が通過する途中の状態です。これを *到達点* として却下する理由はコストにあります。2 つの iOS バックエンドを恒久的に維持すること、つまり 2 つの読み取り経路・2 つのアクチュエータ・2 つの CI レーンを維持することが、移行の終わらせようとしている当のコストです。本提案はこの状態を到達点ではなく通過点として残します。
- **ランナー再利用の enabler なしでデフォルトを切り替える。** ランナーをシナリオごとに起動し直したまま XCUITest をデフォルトにすると、すべての iOS run でシナリオごとのコールド起動をまるごと払うことになります。これは [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) が idb を残したときに挙げた後退そのものです。この理由から、ランナー再利用の提案は本提案の厳格な前提条件です。
- **段階を踏まず、idb を即座に撤去する。** idb を一度に削除すると、XCUITest がすべての idb シナリオを網羅するという実機の証拠が出る前に、ツールチェーン不要のパスと必須の `smoke (idb)` レーンを落とすことになります。撤去の前にデフォルトの切り替えを段階として置くことが、撤去を安全に踏み切れるようにします。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] Unit 1 — iOS のデフォルトを XCUITest へ切り替え、idb 先行のコスト順選択を廃止する。
- [ ] Unit 2 — `-noax` フィクスチャと `smoke` / `E2E` レーンを XCUITest へ移行する。
- [ ] Unit 3 — idb を前提とするコード経路を廃止し、ドキュメントを XCUITest 先頭へ更新する。
- [ ] Unit 4 — idb の最終形（まるごと撤去、または恒久的オプトアウト）を決めて実行する。
- [ ] Unit 5 — ランナー再利用の償却を入れた実機での同等性の検証。

## 参考

- [BE-0019 — XCUITest バックエンド](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) — バックエンドとコスト順のはしご。本提案が見直す *検討した代替案* の項目を含みます。
- [BE-0240 — 能力を踏まえた iOS アクチュエータ選択](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md) — 本提案が単純にする、シナリオごとの解決器。
- [idb issue 767](https://github.com/facebook/idb/issues/767) — idb のアクセシビリティツリーがグループロールのコンテナの子を落とす問題。XCUITest を先頭に据える動機となる忠実さの欠落。

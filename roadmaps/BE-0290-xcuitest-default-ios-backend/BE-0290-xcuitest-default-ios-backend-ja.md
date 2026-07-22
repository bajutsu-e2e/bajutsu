[English](BE-0290-xcuitest-default-ios-backend.md) · **日本語**

# BE-0290 — XCUITest を iOS のデフォルトバックエンドにし、idb を撤去する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0290](BE-0290-xcuitest-default-ios-backend-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0290") |
| 実装 PR | [#NNN](https://github.com/bajutsu-e2e/bajutsu/pull/NNN) |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md), [BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md), [BE-0005](../BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring-ja.md) |
<!-- /BE-METADATA -->

## はじめに

XCUITest を iOS のデフォルトバックエンドにし、idb をまるごと撤去します。この移行は、Simulator での検証まで含めて 1 つのプルリクエストで完結させます。idb の撤去は [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) が意図して下した判断を覆すので、本提案はその反転を十分に論じます。XCUITest はより高機能であり、姉妹提案のランナー再利用がシナリオごとの起動コストを取り除けば、run set 全体でみて idb より高価でもなくなります。そうなれば、idb に残る唯一の利点、すなわち Xcode ツールチェーンなしで動けることは、2 つ目の恒久バックエンドを正当化しません。この 1 つの PR が、デフォルトの切り替え、すべてのフィクスチャと CI レーンの移行、idb とその支持基盤の削除をまとめて担います。そのうえで、すべてのシナリオがなお動くことを Simulator で確認します。到達点は iOS バックエンドを 1 つにすることであり、複数段に分けるのではなく 1 つの変更で到達します。

## 動機

きっかけは、ユーザーが Web UI で突き当たる具体的な欠落です。report と `serve` の要素ツリー表示は、iOS では `trait: group` のコンテナの子を示せません。タブバーは中身を持たない不透明なグループ 1 つとして描画されます。原因は idb の設計です。`idb ui describe-all` は VoiceOver 向けのアクセシビリティツリーを読みます。このツリーでは、それ自体が 1 つのアクセシビリティ要素であるコンテナ（`AXGroup`）は、子を隠す葉になります。そのため idb はその配下の要素をすべて落とします（idb の issue 767）。idb のどのオプションでもその子は復元できず、この制限はそのツリーを読むこと自体に内在します。

XCUITest にこの制限はなく、しかも他のあらゆる軸でより高機能です。XCUITest は XCTest の automation スナップショットを読み、コンテナの内側まで降りて子を 1 つずつ列挙します。だから、忠実に、完全に展開された要素ツリーを示せるのは XCUITest だけです。そのケイパビリティ集合は idb の厳密な上位集合で、セマンティックタップ、ネイティブの条件待ち、マルチタッチ、テキスト選択を加えます。そのため、能力を踏まえた解決器（[BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md)）は、シナリオがそれらのいずれかを必要とするたびに、すでに XCUITest へエスカレーションしています。読者は、その代償として XCUITest がアプリ側の統合を要求すると考えがちですが、そうではありません。ランナーは対象アプリをバンドル ID で起動する汎用ホストで（`XCUIApplication(bundleIdentifier:)`）、対象アプリのソースへ一切触れず、任意のアプリを操作します。

ケイパビリティと忠実さがどちらも XCUITest を後押しするなか、idb を今なおデフォルトにとどめている理由は 1 つ、コストだけです。XCUITest ランナーはコールドな `xcodebuild test-without-building` の起動をシナリオごとに払います。だからこそ、コスト順の actuator 解決器（[BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md)）は idb を安価なデフォルトに据え、XCUITest をエスカレーションとして扱います。姉妹提案「シナリオをまたいで XCUITest ランナーを再利用する」は、ランナーを 1 デバイスにつき 1 つ常駐させ、シナリオの切り替え時にはアプリだけを再起動することで、このコストを取り除きます。起動はシナリオごとではなくデバイスごとに 1 回になります。この償却が入れば、idb をとどめるコスト上の論拠は成り立たなくなります。idb に残る唯一の正当化は、[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) が idb を残す理由として挙げたもの、すなわち Xcode ツールチェーンが使えない環境で動けることです。idb はコマンドラインツールとコンパニオンだけで足りるのに対し、XCUITest は `xcodebuild` を必要とします。本提案は、その単一環境向けの利点を 2 つ目の恒久バックエンドに値しないとみなし、デフォルトを切り替え、idb をまるごと撤去します。

## 詳細設計

5 つの Unit はすべて 1 つのプルリクエストで完結します。これらは別々の変更の連なりではなく、その 1 つの PR のなかの作業分解です。`main` を移行の途中の状態、すなわちデフォルトは切り替わったのに idb がまだ残っている状態や、idb は消したのにフィクスチャがまだ idb に固定されている状態のまま残すべきではないからです。Unit 5、つまりすべてのシナリオがなお通ることを確かめる Simulator の実行が、この PR のマージゲートです。

### Unit 1 — デフォルトを XCUITest へ切り替える

iOS のデフォルトを変え、run が明示的なオプトアウトのない限り XCUITest に解決されるようにします。`Defaults.backend` を `["idb"]` の固定ピンから外し（`bajutsu/config/schema.py`）、idb を先頭に置くコスト順の選択（`bajutsu/backends.py` の `COST_ORDER["ios"]` と `select_actuator_for_scenario`）を iOS について廃止します。残るのは安定性順（`PLATFORMS["ios"] = ("xcuitest", "idb")`）だけです。上位集合である XCUITest がすでにデフォルトになれば、ケイパビリティによるエスカレーション（[BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md)）は iOS では何もしなくなるので、解決器は複雑になるのではなく単純になります。

### Unit 2 — フィクスチャと CI レーンを移行する

showcase の `-noax` ターゲットは、ランナーが用意されていないため今は idb に固定されています。汎用ランナーを用意して XCUITest で動くようにし、必須の `E2E` レーンを XCUITest へ張り替え、既存の `smoke (idb)` と並べて XCUITest の smoke レーンを新設します。いずれもランナーをプリビルド（`xcuitest.testRunner`）したうえで行うので、CI は run ごとのビルドを払いません。この Unit は XCUITest のカバレッジを追加して移行を CI で証明する場です。重複した `smoke (idb)` レーンの撤去は、idb 本体が消える Unit 4 の仕事です。

### Unit 3 — idb を前提とする箇所とドキュメントを更新する

idb がデフォルトである前提に立つコード経路、すなわちコスト順の分岐、XCUITest を idb に落とす doctor のフォールバック、ケイパビリティ preflight の idb 先行の前提を廃止します。`doctor` は特に注意が要ります。`doctor` は自身の iOS クエリを idb に回していますが、それは idb が常駐ランナーなしでアクセシビリティツリーを読めるからです。しかも `doctor` はランナー再利用のプールの外で動くので、その償却に頼れません。この Unit は `doctor` に軽量な XCUITest クエリ経路、すなわち preflight のためだけに起動して終了する短命のランナーを与え、チェックが run ごとのフル起動へ後退しないようにします。あわせて、idb を iOS のデフォルトとして説明するドキュメント（`docs/drivers.md`、`docs/getting-started/ios.md`、およびそれらの日本語版）を、XCUITest を先頭に据える形へ更新します。

### Unit 4 — idb をまるごと撤去する

idb を格下げするのではなく、削除します。この Unit は `IdbDriver` とその周辺の idb 専用の面を取り除きます。すなわち、コンパニオンのバージョン監視（[BE-0005](../BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring-ja.md)）、idb がタブをアドレスできないためだけに存在するクロールのビジョンタブ探索、共有のコーディネートツリー読み取り経路にある idb の分岐、idb 実行ファイルの可用性判定の配線を削り、Unit 2 が新設した XCUITest の smoke レーンがカバレッジを引き継ぐので、冗長になった `smoke (idb)` レーンを落とします。idb はおよそ 60 のモジュールと同数のテストから参照されているので、この Unit 自体の分解は大きくなります。もっとも作業は機械的で、削除したドライバから外向きに、壊れた import をたどって進みます。本提案は 1 行ずつの撤去計画ではなく、到達点、すなわちツリーに idb バックエンドが 1 つも残らないことを定めます。

これらの削除のうち 2 つには留保が付きます。1 つはクロールのビジョンタブ探索で、これは動機で論じた不透明なグループのケースより狭い場合、すなわち id もなく `tab` trait もないカスタム画像タブを扱います。そのため削除は、XCUITest がそのケース（不透明グループだけでなく）も扱えることを Unit 5 で確認できることを条件とします。もう 1 つは BE-0005（コンパニオンのバージョン監視）で、今は `Implemented` ですが、ロードマップの分類には「実装後に機能が削除された」状態がありません。この Unit は BE-0005 を本項目により `Superseded by`（相互リンクつき）とし、その扱いを両者の進捗ログに記録します。これにより、idb が消えたあとも BE-0005 が有効なままに読めてしまうことを防ぎます。

### Unit 5 — すべてのシナリオが Simulator で動くことを確認する

名前を付けた環境（Simulator の機種と Xcode のバージョン）で、現在 idb の `smoke` レーンと `E2E` レーンが網羅するすべてのシナリオを、ランナー再利用の償却を入れた XCUITest で実行し、Simulator でそれぞれが通ることを確認します。この Simulator の実行が、PR のマージゲートです。すべてのシナリオが XCUITest で緑になるまで、変更はマージしません。またこの実行は、クロールのビジョンタブ探索が今日扱っているカスタムタブバーの形、すなわち id もなく `tab` trait もないタブバーも動かす必要があります。Unit 4 でそのロケータを削除することは、XCUITest がそれを扱えると確認できない限り、機能後退だからです。あわせてスイート単位の実時間を記録し、移行のコストを思い込みではなく計測します。

## 検討した代替案

- **idb を撤去せず、恒久的なフォールバックとして残す。** idb を「デフォルトにはしないオプトアウト」として残すのはもっとも低リスクな選択ですが、ここでは却下します。2 つの iOS バックエンドを恒久的に維持すること、つまり 2 つの読み取り経路、2 つの actuator 、2 つの CI レーンを維持することが、この移行の終わらせようとしている当のコストです。XCUITest がより高機能で、しかも run set 全体でみて idb より高価でもなくなれば、idb の唯一の利点（Xcode ツールチェーンなしで動けること）は 2 つ目の恒久バックエンドに値しません。撤去こそが目標であり、フォールバックとして残す状態は目標ではありません。
- **ランナー再利用の enabler なしでデフォルトを切り替える。** ランナーをシナリオごとに起動し直したまま XCUITest をデフォルトにすると、すべての iOS run でシナリオごとのコールド起動をまるごと払うことになります。これはまさに、コスト順の actuator 解決器（[BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md)）が idb を今なお安価なデフォルトにとどめている、そのコストです。この理由から、ランナー再利用の提案は本提案の厳格な前提条件で、この PR より先に入っている必要があります。
- **移行を複数の PR に分ける。** デフォルトの切り替え、フィクスチャの移行、idb の削除を、それぞれ別の PR に分ける案もあります。これを却下するのは、途中のどの時点でも `main` が壊れた状態になるからです。デフォルトは切り替わったのに idb が中途半端に残っている状態や、idb は消したのにフィクスチャがまだ idb に固定されている状態です。移行全体を 1 つの PR で行い、Unit 5 の Simulator 実行でゲートすれば、どのマージ時点でも `main` は正しいままです。代償は 1 回の大きなレビューですが、それは上の Unit の分解でたどりやすくします。
- **Unit 4 の idb 削除を Units 1-3 の後の fast-follow として出す。** より緩やかな分割です。まずデフォルトを切り替えてフィクスチャを移行し、idb のおよそ 60 モジュールにわたる面はあとの PR で削除します。その間 idb は使われないまま残るだけなので `main` は正しいまま、という理屈です。これを却下する理由は 2 つあります。1 つ目は、idb が使われないまま残る状態は、最初の代替案がすでに却下した「idb を恒久的なフォールバックとして残す」状態そのものだからです。後続 PR が入るまで idb は `--backend idb` で選べたままで、`smoke (idb)` レーンも idb を動かし続けます。そして優先度を下げた後続作業は、往々にして入らないままになります。つまりこの分割は、却下したフォールバック状態を、なしくずしに恒久化しかねません。2 つ目は、削除を正当化するのは Unit 5 の Simulator 同等性の証拠だからです。idb を削除するのは、XCUITest がその回すすべてのシナリオを網羅すると証明できたからです。削除をその証拠から切り離すと、証拠のない PR に削除だけが取り残されるか、Simulator 実行を二重に走らせることになります。1 つにまとめれば、「同等性を証明したうえで idb を削除する」を、証拠でゲートされた 1 つのステップに保てます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。5 つの Unit はすべて 1 つの PR で完結し、Unit 5 でゲートします。

- [x] Unit 1 — iOS のデフォルトを XCUITest へ切り替え、idb 先行のコスト順選択を廃止する。
- [x] Unit 2 — `-noax` フィクスチャと `smoke` / `E2E` レーンを XCUITest へ移行する。
- [x] Unit 3 — idb を前提とするコード経路を廃止し、ドキュメントを XCUITest 先頭へ更新する。
- [x] Unit 4 — idb をまるごと撤去する（`IdbDriver`、idb 専用モジュール、`smoke (idb)` レーン）。
- [ ] Unit 5 — すべてのシナリオが Simulator で通ることを確認する（PR のマージゲート）。

## 参考

- [BE-0019 — XCUITest バックエンド](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) — バックエンドと、その安定性順のはしご。本提案が見直す *検討した代替案* の項目（ツールチェーン不要の CI ホスト向けに idb を残した判断）を含みます。
- [BE-0240 — 能力を踏まえた iOS actuator 選択](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md) — 本提案が単純にする、シナリオごとの解決器。
- [idb issue 767](https://github.com/facebook/idb/issues/767) — idb のアクセシビリティツリーがグループロールのコンテナの子を落とす問題。XCUITest を先頭に据える動機となる忠実さの欠落。

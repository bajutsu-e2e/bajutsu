[English](BE-0290-xcuitest-default-ios-backend.md) · **日本語**

# BE-0290 — XCUITest を iOS のデフォルトバックエンドにし、idb を撤去する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0290](BE-0290-xcuitest-default-ios-backend-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0290") |
| 実装 PR | [#1279](https://github.com/bajutsu-e2e/bajutsu/pull/1279) |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md), [BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md), [BE-0005](../BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本提案は、XCUITest を iOS のデフォルトバックエンドにし、idb をまるごと撤去します。この移行は、Simulator での検証まで含めて、1 つのプルリクエストで完結させます。

idb の撤去は、[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) が意図して下した判断を覆します。そのため、本提案は反転の根拠を十分に論じます。

- XCUITest は、より高機能なバックエンドです。
- 姉妹提案のランナー再利用が、XCUITest のシナリオごとの起動コストを取り除きます。すると XCUITest は、run 全体で見て idb より高価でもなくなります。
- idb に残る唯一の利点は、Xcode ツールチェーンなしで動けることです。この利点は、2 つ目の恒久バックエンドを正当化しません。

この 1 つの PR が、デフォルトの切り替え、すべてのフィクスチャと CI レーンの移行、idb とその周辺の削除をまとめて担います。そのうえで、すべてのシナリオがなお動くことを Simulator で確認します。到達点は、iOS バックエンドを 1 つにすることです。複数段に分けず、1 つの変更で到達します。

## 動機

きっかけは、ユーザーが Web UI で突き当たる、具体的な欠落です。report と `serve` の要素ツリー表示は、iOS では `trait: group` のコンテナの子を示せません。タブバーは、中身を持たない不透明なグループ 1 つとして描画されます。

原因は、idb の設計です。`idb ui describe-all` は、VoiceOver 向けのアクセシビリティツリーを読みます。このツリーでは、それ自体が 1 つのアクセシビリティ要素であるコンテナ（`AXGroup`）は、子を隠す葉になります。そのため idb は、コンテナの配下の要素をすべて落とします（idb の issue 767）。このツリーを読むこと自体に制限が内在するので、idb のどのオプションでも、子は復元できません。

XCUITest にはこの制限がなく、ほかのあらゆる軸でも、より高機能です。

- **忠実さ。** XCUITest は XCTest の automation スナップショットを読みます。スナップショットは、コンテナの内側まで降りて、子を 1 つずつ列挙します。忠実で、完全に展開された要素ツリーを示せるのは、XCUITest だけです。
- **ケイパビリティ。** XCUITest のケイパビリティ集合は、idb の集合の厳密な上位集合です。セマンティックタップ、ネイティブの条件待ち、マルチタッチ、テキスト選択を加えます。ケイパビリティを踏まえた解決器（[BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md)）は、シナリオがこれらのいずれかを必要とするたびに、すでに XCUITest へエスカレーションしています。
- **統合。** 読者は、その代償として XCUITest がアプリ側の統合を要求すると考えるかもしれません。要求しません。ランナーは、対象アプリをバンドル ID で起動する汎用ホストです（`XCUIApplication(bundleIdentifier:)`）。対象アプリのソースに触れずに、任意のアプリを操作します。

ケイパビリティと忠実さがどちらも XCUITest を後押しするなか、idb をデフォルトにとどめている理由は、コストだけです。XCUITest ランナーは、コールドな `xcodebuild test-without-building` の起動を、シナリオごとに払います。このため、コスト順の actuator 解決器（[BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md)）は、idb を安価なデフォルトに据え、XCUITest をエスカレーションとして扱っています。

姉妹提案「シナリオをまたいで XCUITest ランナーを再利用する」は、このコストを取り除きます。ランナーを 1 デバイスにつき 1 つ常駐させ、シナリオの切り替え時にはアプリだけを再起動します。起動は、シナリオごとではなく、デバイスごとに 1 回で済みます。

この償却が入れば、idb をデフォルトにとどめるコスト上の論拠は、成り立たなくなります。idb に残る正当化は、[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) が idb を残す理由として挙げたものだけです。Xcode ツールチェーンが使えない環境で動けることです。idb はコマンドラインツールとコンパニオンだけで足りますが、XCUITest は `xcodebuild` を必要とします。本提案は、この単一環境向けの利点を、2 つ目の恒久バックエンドに値しないと判断します。デフォルトを切り替え、idb をまるごと撤去します。

## 詳細設計

5 つの Unit は、すべて 1 つのプルリクエストで完結します。これらは別々の変更の連なりではなく、1 つの PR の中の作業分解です。`main` を移行の途中の状態にしておくと、壊れた状態になり、そのような状態にマージしてはなりません。途中の状態とは、たとえば、デフォルトは切り替わったのに idb がまだ残っている状態や、idb は消したのにフィクスチャがまだ idb に固定されている状態です。Unit 5、つまり、すべてのシナリオがなお通ることを確かめる Simulator の実行が、この PR のマージゲートです。

### Unit 1 — デフォルトを XCUITest へ切り替える

iOS のデフォルトを変え、run がオプトアウトされない限り、XCUITest に解決されるようにします。

- `Defaults.backend` を、`["idb"]` の固定ピンから外します（`bajutsu/config/schema.py`）。
- idb を先頭に置くコスト順の選択（`bajutsu/backends.py` の `COST_ORDER["ios"]` と `select_actuator_for_scenario`）を、iOS について廃止します。残るのは、安定性順（`PLATFORMS["ios"] = ("xcuitest", "idb")`）だけです。
- ケイパビリティによるエスカレーション（[BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md)）は、iOS では何もしなくなります。上位集合である XCUITest が、すでにデフォルトだからです。解決器は、複雑になるのではなく、単純になります。

### Unit 2 — フィクスチャと CI レーンを移行する

showcase の `-noax` ターゲットは、ランナーが用意されていないため、いまは idb に固定されています。この Unit は、3 つのことを行います。

- 汎用ランナーを用意して、`-noax` ターゲットが XCUITest で動くようにします。
- 必須の `E2E` レーンを、XCUITest へ張り替えます。
- 既存の `smoke (idb)` と並べて、XCUITest の smoke レーンを新設します。

3 つとも、ランナーをプリビルド（`xcuitest.testRunner`）して使うので、CI は run ごとのビルドを払いません。この Unit は、XCUITest のカバレッジを追加し、移行を CI で証明します。重複した `smoke (idb)` レーンの撤去は、idb 本体が消える Unit 4 の仕事です。

### Unit 3 — idb を前提とする箇所とドキュメントを更新する

idb がデフォルトであることを前提とするコード経路を、廃止します。

- コスト順の分岐。
- XCUITest を idb に落とす、doctor のフォールバック。
- ケイパビリティ preflight の、idb 先行の前提。

`doctor` は、特に注意が要ります。`doctor` は自身の iOS クエリを idb に回しています。idb が、常駐ランナーなしでアクセシビリティツリーを読めるからです。しかも `doctor` は、ランナー再利用のプールの外で動くので、その償却に頼れません。この Unit は、`doctor` に軽量な XCUITest クエリ経路を与えます。`doctor` が preflight のためだけに起動して終了する、短命のランナーです。これにより、チェックが run ごとのフル起動へ後退しません。

続いて、idb を iOS のデフォルトとして説明するドキュメントを、XCUITest を先頭に据える形へ更新します。対象は、`docs/drivers.md` と `docs/getting-started/ios.md`、およびそれらの日本語版です。

### Unit 4 — idb をまるごと撤去する

idb を格下げせず、削除します。この Unit は、`IdbDriver` と、その周辺の idb 専用の面を取り除きます。

- コンパニオンのバージョン監視（[BE-0005](../BE-0005-idb-companion-version-monitoring/BE-0005-idb-companion-version-monitoring-ja.md)）。
- クロールのビジョンタブ探索。idb がタブをアドレスできないためだけに存在します。
- 共有のコーディネートツリー読み取り経路にある、idb の分岐。
- idb 実行ファイルの可用性判定の配線。

この Unit は、冗長になった `smoke (idb)` レーンも落とします。Unit 2 が新設した XCUITest の smoke レーンが、そのカバレッジをすでに提供しているからです。

idb は、およそ 60 のモジュールと、同数のテストから参照されています。そのため、この Unit 自体の分解は大きくなります。ただし、作業は機械的です。削除したドライバから外向きに、壊れた import をたどって進めます。本提案が定めるのは、到達点、すなわち、ツリーに idb バックエンドが 1 つも残らないことです。1 行ずつの撤去計画は定めません。

これらの削除のうち 2 つには、留保が付きます。

- クロールのビジョンタブ探索は、動機で論じた不透明なグループのケースより、狭い場合を扱います。アクセシビリティ id もなく `tab` trait もない、カスタム画像タブです。そのため、探索の削除は、Unit 5 で XCUITest がこのケースを扱えると確認できることを条件とします。不透明なグループのケースだけでは、条件を満たしません。
- BE-0005（コンパニオンのバージョン監視）は、いまは `Implemented` です。ロードマップの分類には、「実装後に機能が削除された」状態がありません。この Unit は、BE-0005 を本項目により `Superseded by`（相互リンクつき）とし、その扱いを両者の進捗ログに記録します。これにより、idb が消えたあとも BE-0005 が有効なままに読めてしまうことを防ぎます。

### Unit 5 — すべてのシナリオが Simulator で動くことを確認する

現在 idb の `smoke` レーンと `E2E` レーンが網羅する、すべてのシナリオを、XCUITest で実行します。名前を付けた環境（Simulator の機種と Xcode のバージョン）で、ランナー再利用の償却を入れて実行します。Simulator で、それぞれが通ることを確認します。

この Simulator の実行が、PR のマージゲートです。すべてのシナリオが XCUITest で緑になるまで、変更はマージしません。

この実行は、クロールのビジョンタブ探索が今日扱っている、カスタムタブバーの形も動かす必要があります。アクセシビリティ id もなく `tab` trait もないタブバーです。Unit 4 で探索を削除することは、XCUITest がこの形を扱えると確認できない限り、機能後退だからです。

スイート単位の実時間も記録します。移行のコストを、思い込みではなく、計測するためです。

## 検討した代替案

- **idb を撤去せず、恒久的なフォールバックとして残す。** idb を、デフォルトにはしないオプトアウトとして残すのは、もっとも低リスクな選択です。ここでは却下します。2 つの iOS バックエンドを恒久的に維持すると、2 つの読み取り経路、2 つの actuator、2 つの CI レーンを維持することになります。これが、この移行の終わらせようとしている、当のコストです。XCUITest がより高機能で、しかも run 全体で見て idb より高価でもなくなれば、idb の唯一の利点（Xcode ツールチェーンなしで動けること）は、2 つ目の恒久バックエンドに値しません。撤去が目標であり、フォールバックとして残す状態は目標ではありません。
- **ランナー再利用なしで、デフォルトを切り替える。** ランナーをシナリオごとに起動し直したまま XCUITest をデフォルトにすると、すべての iOS run で、シナリオごとのコールド起動をまるごと払います。これは、コスト順の actuator 解決器（[BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md)）が、idb を今なお安価なデフォルトにとどめている、そのコストです。したがって、ランナー再利用の提案は、本提案の厳格な前提条件です。この PR より先に入っている必要があります。
- **移行を複数の PR に分ける。** デフォルトの切り替え、フィクスチャの移行、idb の削除を、それぞれ別の PR にする案です。却下します。途中のどの時点でも、`main` が壊れた状態になるからです。デフォルトは切り替わったのに idb が中途半端に残っている状態や、idb は消したのにフィクスチャがまだ idb に固定されている状態です。移行全体を 1 つの PR で行い、Unit 5 の Simulator 実行でゲートすれば、どのマージ時点でも `main` は正しいままです。代償は 1 回の大きなレビューです。上の Unit の分解が、そのレビューをたどりやすくします。
- **Unit 4 の idb 削除を、Units 1〜3 のあとの fast-follow として出す。** より緩やかな分割です。まずデフォルトを切り替えてフィクスチャを移行し、idb のおよそ 60 モジュールにわたる面は、あとの PR で削除します。その間、idb は使われないまま残るだけなので、`main` は正しいままだという理屈です。却下する理由は、2 つあります。
  - 1 つ目は、idb が使われないまま残る状態が、最初の代替案がすでに却下した「idb を恒久的なフォールバックとして残す」状態そのものだからです。後続の PR が入るまで、idb は `--backend idb` で選べるままです。`smoke (idb)` レーンも、idb を動かし続けます。優先度を下げた後続作業は、往々にして入らないままになります。この分割は、却下したフォールバック状態を、なしくずしに恒久化しかねません。
  - 2 つ目は、削除を正当化するのが、Unit 5 の Simulator 同等性の証拠だからです。idb を削除するのは、XCUITest が idb の回すすべてのシナリオを網羅すると、証明できたからです。削除をその証拠から切り離すと、証拠のない PR に削除だけが取り残されるか、Simulator 実行を二重に走らせることになります。1 つにまとめれば、「同等性を証明したうえで idb を削除する」が、証拠でゲートされた 1 つのステップに保てます。

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

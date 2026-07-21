[English](BE-XXXX-xcuitest-runner-reuse-across-scenarios.md) · **日本語**

# BE-XXXX — シナリオをまたいで XCUITest ランナーを再利用し、コールド起動を償却する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-xcuitest-runner-reuse-across-scenarios-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md), [BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query-ja.md), [BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md) |
<!-- /BE-METADATA -->

## はじめに

XCUITest ランナーを 1 デバイスにつき 1 つ、run set 全体を通じて常駐させ、シナリオの切り替え時にはアプリだけを再起動します。これにより、1 回の run が払うランナーのコールド起動は、シナリオごとに 1 回ではなく、デバイスごとに 1 回で済みます。チャネル、要素のアドレッシング方式、シナリオごとのデバイスリセットはいずれも変えません。変えるのはランナープロセスの寿命だけです。この償却は、XCUITest バックエンドの最大の固定コストを取り除きます。固定コストの解消こそが、現在はエスカレーション専用である actuator を iOS のデフォルトへ引き上げる候補にする条件です。

## 動機

XCUITest バックエンドは、デバイス上の常駐ランナーからアプリを操作します。ランナーの実体は `xcodebuild test-without-building` プロセスで、アプリを起動したうえで、Python 側ドライバが叩くループバック HTTP エンドポイントを提供します（[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)）。このランナーの起動は高価です。ランナーが最初のヘルスチェックに答えるまで、XCTest ホストの起動とアプリの立ち上がりを待つからです。負荷のかかった継続的インテグレーション（CI）ホストでのコールド起動は 10 秒を優に超えるのが常で、ドライバは最大 120 秒まで待ちます（`bajutsu/platform_lifecycle/environments/xcuitest.py` の `_RUNNER_STARTUP_TIMEOUT`）。

現状、1 回の run はこのコールド起動をシナリオごとに払っています。デバイスプールはシナリオごとにデバイスをリースし（`bajutsu/runner/pool.py`）、各リースは新しい `XcuitestEnvironment` を組み立てます。この環境の `start()` が新しいランナーを起動し、リース解放時のティアダウンがそれを終了させます。20 シナリオのスイートは 20 個のランナーを起動しては終了させ、どのアサーションも走らないうちにランナー起動だけで数分を費やします。このシナリオごとのコストこそ、actuator解決器が idb を安価なデフォルトに据える理由です（[BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md)）。同じ解決器が XCUITest をエスカレーションに回すのも同じ理由からです。idb は常駐ランナーやビルド工程を持たず、シナリオの間に何も起動しないからです。

このコストは XCUITest に固有のものではなく、ランナーを起動し直すことに固有のものです。1 つのリースの内側では、`relaunch` ステップがすでにランナーを再利用しています。`device_relauncher`（`bajutsu/platform_lifecycle/relaunchers.py`）はアプリプロセスだけを終了して起動し直し、ランナーには手を触れず、アプリが再び ready になるまで待ちます。ランナーはそのとき起動されているアプリを操作するだけで、シナリオ固有の状態を自身に持ちません。リースの内側で行っているアプリだけの再起動を、1 デバイス上でリースをまたいで一般化すること、それがこの提案のすべてです。

起動の償却は、単なる速度を超えて意味を持ちます。シナリオごとのコストが、実在する機能差を放置し続ける論拠になっているからです。idb バックエンドは VoiceOver 向けのアクセシビリティツリーを読みます。このツリーでは、それ自体が 1 つのアクセシビリティ要素であるコンテナ、たとえばタブバーのような `AXGroup` は、子を隠す葉になります。そのため idb の要素ツリーは、そうしたグループ配下の要素をすべて落とします（idb の issue 767）。一方 XCUITest バックエンドは XCTest の automation スナップショットを読み、コンテナの内側まで降りて子を 1 つずつ列挙します。だから、忠実に、完全に展開された要素ツリーを示せるのは XCUITest だけです。この忠実なツリーを iOS のデフォルトの挙動に、すなわち run が取得する証跡と、それを描画する Web UI のデフォルトにすることは、今はこの項目が取り除く起動コストだけによって阻まれています。したがってこの提案は、その前提を整える項目です。iOS のデフォルトを idb から XCUITest へ切り替えることは、この項目に依存する別の後続項目であり、ここでは意図的に対象外とします。

## 詳細設計

変更は、デバイスプールの環境ライフサイクルと、XCUITest 環境のプロセス所有権に限られます。Python↔ランナー間のチャネル、スナップショットハンドルによるアドレッシング（[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)）、単一スナップショットのクエリ（[BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query-ja.md)）、そしてシナリオごとのリセット（`erase` / `relaunch` / 権限付与）は、いずれも現状のまま変えません。

### Unit 1 — actuatorをキーにした、デバイスごとのウォームランナーのキャッシュ

プールは常駐ランナーをデバイスごとに 1 つ、`(udid, actuator)` をキーとして保持し、デバイスごとのネットワークコレクタをすでに再利用しているのと同じやり方でリースをまたいで再利用します（`bajutsu/runner/pool.py` はコレクタをデバイスごとに前もって 1 つ起動し、リースをまたいで再利用します）。解決されたactuatorがキャッシュ済みランナーのactuatorと一致するリースは、ランナーを起動せずに、動いているプロセスを引き継ぎます。キーにactuatorを含めるのは、actuatorがシナリオごとに解決されるためです（[BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md)）。idb に解決されるシナリオがウォームな XCUITest ランナーを引き継いではならず、その逆もまた同様です。

ランナーのハンドルは、リースごとに作り直す `XcuitestEnvironment` ではなく、プールが保持します。現状、`pool.py` の `lease()` は毎回新しい環境を組み立て、`XcuitestEnvironment.start()` は simctl のデバイス準備とランナーの起動を 1 つのメソッドに同居させています（`bajutsu/platform_lifecycle/environments/xcuitest.py`）。この Unit はその 2 つの工程を分けます。キャッシュ済みランナーを持つリースは、デバイス準備とアプリの再起動は行いますが、起動は飛ばして、プールが保持するプロセスを引き継ぎます。起動はキャッシュミスのときだけ走ります。対応する所有権の移動は Unit 3 が扱います。プロセスを終了させるのはリースではなくプールになります。

### Unit 2 — 同一actuatorのシナリオ間での、アプリだけの引き継ぎ

リースがウォームランナーを再利用するとき、シナリオのセットアップはランナーではなくアプリを再起動します。経路は、`device_relauncher` がリースの内側で使っている既存のアプリだけの経路です。具体的には、アプリを終了したうえで、シナリオの起動環境変数、起動引数、ロケールを再適用してアプリを起動し直します。そのあと ready になるまで待ちます。デバイス状態をリセットする前提条件（`erase`）と権限付与は、これまでどおりアプリ起動の前に simctl 経由で走ります。そのため、再利用されたランナーが、単一リース経路が今日与えているシナリオごとの分離を弱めることはありません。

### Unit 3 — ランナーの所有権、ティアダウン、actuatorの切り替え

ウォームランナーは、それを生成したリースよりも長く生き延びるので、所有権はリースからプールへ移ります。プールは、次の 3 つの契機でデバイスのランナーを終了させます。1 つ目は run set が終わったときです。2 つ目はそのデバイス上の次のシナリオが別のactuatorに解決されたときで、キャッシュ済みランナーは新しいactuatorの環境が起動する前にティアダウンされます。3 つ目は障害が新しいランナーを要求したときです。ランナープロセスを所有するインスタンスがそれを終了させるインスタンスである、という不変条件は保たれます。現在のリースごとのティアダウンが明示している不変条件を、リースからプールへと 1 階層引き上げるだけです。

### Unit 4 — 再利用されるランナーをまたいだクラッシュ回復

run の途中でクラッシュしたりウェッジしたりしたランナーが、それを再利用しようとする次のシナリオを汚染してはなりません。プールはウォームランナーのヘルスチェック失敗をキャッシュミスとして扱います。死んだランナーを破棄し、次のリースのために新しいランナーを起動します。ここでは 2 つ目の回復経路を足すのではなく、ドライバがすでに持つ、境界の定まったヘルスポーリングによる回復を再利用します。1 つのシナリオの障害は、失敗した run ではなく、コールド起動 1 回分の追加で済みます。

### Unit 5 — 名前を付けた環境での償却の計測

償却の効果は、[BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query-ja.md) がクエリのベースラインを定めたのと同じやり方で確かめます。名前を付けた環境（Simulator の機種、Xcode のバージョン、複数シナリオの showcase スイート）で、スイート全体のランナー起動時間の合計を変更の前後で計測し、シナリオごと 1 回からデバイスごと 1 回へ下がることを確認します。受け入れの数値は、単一シナリオの起動時間ではなくスイート単位の起動時間です。償却はシナリオをまたいでしか現れないからです。

## 検討した代替案

- **起動を償却せずに iOS のデフォルトを XCUITest へ切り替える。** ランナーをシナリオごとに起動し直したまま XCUITest をデフォルトへ切り替えると、すべての iOS run でシナリオごとのコールド起動をまるごと払うことになります。これはまさに、コスト順のactuator解決器（[BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md)）が idb を今なお安価なデフォルトにとどめている、そのコストです。先に起動を償却しておくことが、既知の後退を飲み込むのではなく、そのデフォルトを誠実に再検討できるようにします。
- **`.xctestrun` をプリビルドして、問題は解決したと見なす。** プリビルドしたテストランナーを `xcuitest.testRunner` で指定すれば、run 経路から `xcodebuild build-for-testing` の工程は消えます。この項目もそれを前提とします。しかしプリビルドは、ランナーのコールド *起動* を取り除きません。シナリオごとのコストを支配する XCTest ホストの起動とアプリの起動は残ります。プリビルドは、再利用の代わりではなく、補完し合う運用上の選択です。
- **actuatorによらず 1 つのウォームランナーを保持する。** 解決されたactuatorを無視する、デバイスごとに 1 つのランナーを考えます。この設計は idb シナリオをウォームな XCUITest ランナーに対して走らせてしまいます。その結果、run のマニフェストが記録するactuatorを黙って変え、シナリオごとに 1 actuatorという規則に反します（[BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md)）。キャッシュのキーをactuatorにすれば、選択の主導権は解決器に残ります。
- **デバイスをまたいでランナーを再利用する。** ランナーは `xcodebuild -destination` によって 1 つの Simulator に束縛されるので、デバイスをまたいで共有できるランナーは存在しません。デバイスをまたいだ並列性はプールの既存の関心事であり、1 デバイス上でランナーを再利用することとは直交します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] Unit 1 — `(udid, actuator)` をキーにした、デバイスごとのウォームランナーのキャッシュ。
- [ ] Unit 2 — 既存の relaunch 経路による、同一actuatorのシナリオ間でのアプリだけの引き継ぎ。
- [ ] Unit 3 — ランナーの所有権をプールへ移し、run set の終了、actuatorの切り替え、障害でティアダウンする。
- [ ] Unit 4 — 死んだウォームランナーをキャッシュミスとして扱うクラッシュ回復。
- [ ] Unit 5 — 名前を付けた環境での、スイート単位の起動時間の短縮の計測。

## 参考

- [BE-0019 — XCUITest バックエンド](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) — 常駐ランナーとループバックチャネル。この項目はそのシナリオごとの起動を償却します。
- [BE-0105 — XCUITest の単一スナップショット要素クエリ](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query-ja.md) — この項目が手法として倣う、クエリ遅延の後続項目。
- [BE-0240 — 能力を踏まえた iOS actuator選択](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md) — キャッシュのキーが尊重する、シナリオごとのactuator解決。
- [idb issue 767](https://github.com/facebook/idb/issues/767) — idb のアクセシビリティツリーがグループロールのコンテナの子を落とす問題。この償却が最終的に埋める助けとなる機能差。

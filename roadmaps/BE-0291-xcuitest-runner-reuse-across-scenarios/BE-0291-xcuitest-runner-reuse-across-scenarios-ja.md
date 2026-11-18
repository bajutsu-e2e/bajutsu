[English](BE-0291-xcuitest-runner-reuse-across-scenarios.md) · **日本語**

# BE-0291 — シナリオをまたいで XCUITest ランナーを再利用し、コールド起動を償却する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0291](BE-0291-xcuitest-runner-reuse-across-scenarios-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0291") |
| 実装 PR | [#1226](https://github.com/bajutsu-e2e/bajutsu/pull/1226) |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md), [BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query-ja.md), [BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本提案は、XCUITest ランナーを 1 デバイスにつき 1 つ、run set 全体を通じて常駐させます。シナリオの切り替え時には、アプリだけを再起動します。これにより、1 回の run が払うランナーのコールド起動は、シナリオごとに 1 回ではなく、デバイスごとに 1 回で済みます。

チャネル、要素のアドレッシング方式、シナリオごとのデバイスリセットは、いずれも変えません。変えるのは、ランナープロセスの寿命だけです。

この償却は、XCUITest バックエンドの最大の固定コストを取り除きます。固定コストがなくなれば、このバックエンドは iOS のデフォルトの候補になります。現在は、エスカレーション専用の actuator です。

## 動機

XCUITest バックエンドは、デバイス上の常駐ランナーからアプリを操作します。ランナーの実体は `xcodebuild test-without-building` プロセスです。アプリを起動したうえで、Python 側のドライバが叩くループバック HTTP エンドポイントを提供します（[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)）。

このランナーの起動は高価です。ランナーが最初のヘルスチェックに答えるまでに、XCTest ホストが起動し、アプリが立ち上がります。負荷のかかった継続的インテグレーション（CI）ホストでのコールド起動は、10 秒を優に超えるのが常です。ドライバは最大 120 秒まで待ちます（`bajutsu/platform_lifecycle/environments/xcuitest.py` の `_RUNNER_STARTUP_TIMEOUT`）。

現状、1 回の run は、このコールド起動をシナリオごとに払っています。

- デバイスプールは、シナリオごとにデバイスをリースします（`bajutsu/runner/pool.py`）。
- 各リースは、新しい `XcuitestEnvironment` を組み立てます。この環境の `start()` が新しいランナーを起動し、リース解放時のティアダウンがランナーを終了させます。
- 20 シナリオのスイートは、20 個のランナーを起動しては終了させます。どのアサーションも走らないうちに、ランナー起動だけで数分を費やします。

このシナリオごとのコストが、actuator 解決器が idb を安価なデフォルトに据え、XCUITest をエスカレーションとして扱う、具体的な理由です（[BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md)）。idb は、常駐ランナーもビルド工程も持たないので、シナリオの間に何も起動しません。

このコストは、XCUITest に固有のものではありません。ランナーを起動し直すことに固有のものです。1 つのリースの内側では、`relaunch` ステップが、すでにランナーを再利用しています。`device_relauncher`（`bajutsu/platform_lifecycle/relaunchers.py`）は、アプリプロセスだけを終了して起動し直します。ランナーには手を触れず、アプリが再び ready になるまで待ちます。ランナーは、そのとき起動されているアプリを操作するだけで、シナリオ固有の状態を自身に持ちません。本提案は、このアプリだけの再起動を一般化します。再起動をリースの内側にとどめず、1 デバイス上のリースをまたいで適用します。この一般化が、提案のすべてです。

起動の償却は、単なる速度を超えて意味を持ちます。シナリオごとのコストが、実在する機能差を放置し続ける論拠になっているからです。

- idb バックエンドは、VoiceOver 向けのアクセシビリティツリーを読みます。このツリーでは、それ自体が 1 つのアクセシビリティ要素であるコンテナ、たとえばタブバーの `AXGroup` は、子を隠す葉になります。そのため、idb の要素ツリーは、そうしたグループ配下の要素をすべて落とします（idb の issue 767）。
- XCUITest バックエンドは、XCTest の automation スナップショットを読みます。スナップショットは、コンテナの内側まで降りて、子を 1 つずつ列挙します。忠実で、完全に展開された要素ツリーを示せるのは、XCUITest だけです。

この忠実なツリーを iOS のデフォルトの挙動にすれば、run が取得する証跡が良くなります。証跡を描画する Web UI も良くなります。いま、この変更を阻んでいるのは、この項目が取り除く起動コストだけです。したがって、本提案は、前提を整える項目です。iOS のデフォルトを idb から XCUITest へ切り替えることは、この項目に依存する別の後続項目です。ここでは意図的に対象外とします。

## 詳細設計

変更は、デバイスプールの環境ライフサイクルと、XCUITest 環境のプロセス所有権に限られます。次のものは、現状のまま変えません。

- Python↔ランナー間のチャネル。
- スナップショットハンドルによるアドレッシング（[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)）。
- 単一スナップショットのクエリ（[BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query-ja.md)）。
- シナリオごとのリセット（`erase`、`relaunch`、権限付与）。

### Unit 1 — actuator をキーにした、デバイスごとのウォームランナーのキャッシュ

プールは、常駐ランナーをデバイスごとに 1 つ、`(udid, actuator)` をキーとして保持します。プールは、デバイスごとのネットワークコレクタをすでに再利用しているのと同じやり方で、ランナーをリースをまたいで再利用します。（`bajutsu/runner/pool.py` は、コレクタをデバイスごとに前もって 1 つ起動し、リースをまたいで再利用します。）解決された actuator が、キャッシュ済みランナーの actuator と一致するリースは、ランナーを起動せず、動いているプロセスを引き継ぎます。

キーに actuator を含めるのは、actuator がシナリオごとに解決されるためです（[BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md)）。idb に解決されるシナリオが、ウォームな XCUITest ランナーを引き継いではなりません。XCUITest のシナリオが、ウォームな idb ランナーを引き継いでもなりません。

ランナーのハンドルは、リースごとに作り直す `XcuitestEnvironment` ではなく、プールが保持します。現状、`pool.py` の `lease()` は、毎回新しい環境を組み立てます。`XcuitestEnvironment.start()` は、simctl のデバイス準備とランナーの起動を、1 つのメソッドに同居させています（`bajutsu/platform_lifecycle/environments/xcuitest.py`）。この Unit は、その 2 つの工程を分けます。

- キャッシュ済みランナーを持つリースは、デバイス準備とアプリの再起動を行います。起動は飛ばして、プールが保持するプロセスを引き継ぎます。
- 起動は、キャッシュミスのときだけ走ります。

対応する所有権の移動は、Unit 3 が扱います。プロセスを終了させるのは、リースではなく、プールになります。

### Unit 2 — 同一 actuator のシナリオ間での、アプリだけの引き継ぎ

リースがウォームランナーを再利用するとき、シナリオのセットアップは、ランナーではなくアプリを再起動します。経路は、`device_relauncher` がリースの内側で使っている、既存のアプリだけの経路です。

1. アプリを終了します。
2. シナリオの起動環境変数、起動引数、ロケールを再適用します。
3. アプリを起動し直します。
4. ready になるまで待ちます。

デバイス状態をリセットする前提条件（`erase`）と権限付与は、これまでどおり、アプリの起動前に simctl 経由で走ります。そのため、再利用されたランナーが、単一リース経路が今日与えている、シナリオごとの分離を弱めることはありません。

### Unit 3 — ランナーの所有権、ティアダウン、actuator の切り替え

ウォームランナーは、生成したリースより長く生き延びるので、所有権はリースからプールへ移ります。プールは、次の 3 つの場合に、デバイスのランナーを終了させます。

- run set が終わったとき。
- そのデバイス上の次のシナリオが、別の actuator に解決されたとき。プールは、新しい actuator の環境が起動する前に、キャッシュ済みランナーをティアダウンします。
- 障害が、新しいランナーを要求したとき。

この移動は、現在のリースごとのティアダウンが明示している不変条件を保ちます。ランナープロセスを所有するインスタンスが、それを終了させるインスタンスである、という不変条件です。不変条件は、リースからプールへ、1 階層引き上がります。

### Unit 4 — 再利用されるランナーをまたいだクラッシュ回復

run の途中でクラッシュしたり、ウェッジしたりしたランナーが、それを再利用しようとする次のシナリオに、持ち越されてはなりません。プールは、ウォームランナーのヘルスチェック失敗を、キャッシュミスとして扱います。応答しないランナーを破棄し、次のリースのために新しいランナーを起動します。プールは、ドライバがすでに持つ、境界の定まったヘルスポーリングによる回復を再利用します。2 つ目の回復経路は足しません。1 つのシナリオの障害は、run の喪失ではなく、コールド起動 1 回分の追加で済みます。

### Unit 5 — 名前を付けた環境での償却の計測

償却の効果は、[BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query-ja.md) がクエリのベースラインを定めたのと同じやり方で確かめます。名前を付けた環境を使います。Simulator の機種、Xcode のバージョン、複数シナリオの showcase スイートです。スイート全体のランナー起動時間の合計を、変更の前後で計測します。そのうえで、起動時間が、シナリオごと 1 回から、デバイスごと 1 回へ下がることを確認します。

受け入れの数値は、単一シナリオの起動時間ではなく、スイート単位の起動時間です。償却は、シナリオをまたいでしか現れないからです。

## 検討した代替案

- **起動を償却せずに、iOS のデフォルトを XCUITest へ切り替える。** ランナーをシナリオごとに起動し直したまま XCUITest をデフォルトにすると、すべての iOS run で、シナリオごとのコールド起動をまるごと払います。これは、コスト順の actuator 解決器（[BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md)）が、idb を今なお安価なデフォルトにとどめている、そのコストです。先に起動を償却しておけば、既知の後退を飲み込まずに、そのデフォルトを誠実に再検討できます。
- **`.xctestrun` をプリビルドして、問題は解決したと見なす。** プリビルドしたテストランナーを `xcuitest.testRunner` で指定すれば、run 経路から `xcodebuild build-for-testing` の工程は消えます。この項目も、プリビルドを前提とします。しかし、プリビルドは、ランナーのコールド*起動*を取り除きません。シナリオごとのコストを支配する、XCTest ホストの起動とアプリの起動は残ります。プリビルドは、再利用の代わりではなく、補完し合う運用上の選択です。
- **actuator によらず、1 つのウォームランナーを保持する。** 解決された actuator を無視する、デバイスごとに 1 つのランナーは、idb のシナリオを、ウォームな XCUITest ランナーに対して走らせてしまいます。run のマニフェストが記録する actuator が、黙って変わります。シナリオごとに 1 つの actuator という規則にも反します（[BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md)）。キャッシュのキーを actuator にすれば、選択の主導権は解決器に残ります。
- **デバイスをまたいでランナーを再利用する。** ランナーは、`xcodebuild -destination` によって 1 つの Simulator に束縛されます。デバイスをまたいで共有できるランナーは、存在しません。デバイスをまたいだ並列性は、プールの既存の関心事です。1 デバイス上でランナーを再利用することとは、直交します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] Unit 1 — `(udid, actuator)` をキーにした、デバイスごとのウォームランナーのキャッシュ。
- [x] Unit 2 — 既存の relaunch 経路による、同一 actuator のシナリオ間でのアプリだけの引き継ぎ。
- [x] Unit 3 — ランナーの所有権をプールへ移し、run set の終了、 actuator の切り替え、障害でティアダウンする。
- [x] Unit 4 — 死んだウォームランナーをキャッシュミスとして扱うクラッシュ回復。
- [x] Unit 5 — 償却を決定論的に証明しました（複数シナリオのスイートでランナーの spawn がデバイスごと
  1 回になることを `tests/runner/test_pool.py` / `tests/test_xcuitest_environment.py` で検証）。名前を
  付けた Simulator + Xcode 環境でのスイート単位の起動時間のウォールクロック計測は、fast gate が持たない
  実機での確認事項として残します。

**ログ**

- 再利用は、3 つの部分として実装しました。
  - `Environment` シームへの 2 メソッドの追加です。`has_reusable_resident` と `end_lease` で、どちらも「ウォームな常駐なし」を既定値とします。
  - `runner/pool.py` にある、`(udid, actuator)` をキーとした、デバイスごとのウォームランナーキャッシュです。
  - ランナーが健全なときは、アプリだけを再起動する、再開対応の `XcuitestEnvironment.start` です。
- これにより、プールは actuator 非依存のままです。XCUITest 以外のバックエンドは変わりません。
- 対象は、Simulator 経路に限定します。実デバイスの XCUITest と、live WebDriver 環境は、リースごとにコールドのままです。
- 詳細は、実装 PR を参照してください。

## 参考

- [BE-0019 — XCUITest バックエンド](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) — 常駐ランナーとループバックチャネル。この項目はそのシナリオごとの起動を償却します。
- [BE-0105 — XCUITest の単一スナップショット要素クエリ](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query-ja.md) — この項目が手法として倣う、クエリ遅延の後続項目。
- [BE-0240 — 能力を踏まえた iOS actuator 選択](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection-ja.md) — キャッシュのキーが尊重する、シナリオごとの actuator 解決。
- [idb issue 767](https://github.com/facebook/idb/issues/767) — idb のアクセシビリティツリーがグループロールのコンテナの子を落とす問題。この償却が最終的に埋める助けとなる機能差。

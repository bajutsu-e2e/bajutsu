[English](BE-0409-step-latency-ios-device-executor.md) · **日本語**

# BE-0409 — XCTest ランナー内の iOS 端末側ステップ実行機

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0409](BE-0409-step-latency-ios-device-executor-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0409") |
| トピック | Platform support |
| 関連 | [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)、[BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md)、[BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md)、[BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md)、[BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md)、[BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md) は、端末側ステップ実行プロトコルを定義しています。このプロトコルは、ホストから端末へ移すもの、ホストに残すもの、両プラットフォームが共有するセレクタ意味論の契約を定めます。ホスト側のドライバ調整だけでは届かない、Bajutsu の 1 ステップあたり 250〜500 ミリ秒という目標へ至る道筋です。

この項目は、そのプロトコルの実装のうち iOS 側の半分です。既存の `APIHandler` の HTTP サーバー上に、XCTest ランナープロセス内で動くステップ実行機を加えます。実行機は、セレクタの解決と条件の評価をネイティブに行います。ポーリングのたびに HTTP 経由でホストへ差し戻すことはしません。

## 動機

今日、iOS のアクチュエーションと読み取りはどちらも `XcuitestElementProvider` を経由します。ホストが発行する `query` や `tap` は、そのつど完全な HTTP 往復です。`wait for` ステップは、この方式で 50 ミリ秒ごとにポーリングします。`POST /tap` 単体でも、タップを合成する前に約 11 個の要素属性を順番に解決します。

同じ解決と条件評価をランナープロセスの内側で行えば、ポーリングごと、属性読みごとの往復がなくなります。ランナーはすでにアクセシビリティツリーを手元に持っているので、中間の読み取りをホストへ返す必要がありません。

実行機を構築したら、`tap` ステップについて次の見積もりと照らし合わせます。

- `app.snapshot()` 1 回で約 35 ミリ秒
- 座標によるタップで 100〜200 ミリ秒
- settle の判定で 35〜70 ミリ秒
- 合計でおよそ 200〜350 ミリ秒（スクリーンショットは臨界パスの外に出す）

後から読む人は、ドライバ内部調整の [BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md) が測定を取ったのと同じ方法で、この見積もりを確かめられます。この実行機に対して実際の `tap` ステップをトレースし、今日の iOS の基準値である 0.95〜1.07 秒と比較します。

## 詳細設計

**実装の順番。** この項目は、関連する 4 項目のうち 3 番目で、順番は厳密です。

1. ドライバ内部調整の項目（[BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md)）
2. 端末側プロトコル（BE-0408）
3. この項目
4. Android 実行機（[BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md)）

**この項目への着手は、BE-0408 が完了するまで行ってはいけません。** この項目は、BE-0408 のプロトコルとセレクタ意味論の契約を実装します。設計がまだ変わっている段階で着手すると、設計が変わるたびに作り直しが必要になります。

BE-0410 も同様に、この項目が完了するまで着手してはいけません。2 つのプラットフォーム実行機をこの順に並べると、セレクタ意味論の移植を、もう一方のプラットフォームより先にこの項目で 1 度だけ行えます。移植とは、ここでは `resolve_unique` を Swift へ、Android 側では Kotlin へ移すことです。この移植で見つかった課題を、両プラットフォームが別々に発見し直す事態は避けられます。

### アプリ内 SDK ではなくランナープロセスを選ぶ理由

実行機は、アプリ内 SDK（BajutsuKit）ではなく XCTest ランナープロセスの内側で動かします。理由は 3 つです。

- BajutsuKit には、イベント注入、キーボード入力、frame を伴うアクセシビリティツリー、スクリーンショット取得のいずれもありません。`BajutsuTouch` は観測専用の swizzle で、入力を合成しません。
- SpringBoard の権限ダイアログ、システムキーボード、`SFSafariViewController` のコンテンツには、アプリプロセスの内側から届きません。[BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md) も、Safari のコンテンツを読むために同じ境界を越える必要がありました。
- アプリのクラッシュを観測できるのは、プロセス境界があるからです。アプリ内の実行機は、検査対象のアプリとともに落ちます。

### 実行機

実行機は、既存の `APIHandler` の HTTP サーバーへの追加として構築します。ステップの列（BE-0408 の段階 4 に沿ったもの）を受け取る `POST /scenario` を追加します。実行機は、次の 4 つのことをネイティブに行います。

1. **単一の `app.snapshot()` からセレクタを解決する。** ホスト側の `resolve_unique` セレクタロジック（`bajutsu/common/drivers/base.py`）を Swift に移植し、ランナー内で実行します。属性ごとに新しく読み取らず、同じスナップショットに対して解決します。
2. **解決済み要素の frame 中心を座標でタップする。** 属性を読み直すのは、stale が疑われるときだけです。BE-0407 が同じ技法について採っている立場を、この項目は実行機が行うすべてのタップへ一般化します。座標タップの方式自体は新しくありません。[BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md) が Safari のコンテンツに対してすでに採っています。着手前の検討が必要な理由は、BE-0407 が挙げるものと同じです。座標タップは、要素が移動していた場合の正しさというリスクをレイテンシと引き換えにします。そのため、stale 判定のフォールバックを正しく設計する必要があります。詳しくは下の「検討した代替案」を参照してください。
3. **条件待ちを、ランナー内部だけで完結する 30〜40 ミリ秒周期の `snapshot()` ループで評価する。** BajutsuKit の画面遷移シグナル（[BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md)）は、今日は Python 側の collector にしか届きません。ランナーへ直接届くよう拡張すれば、`settled` は、ツリー差分のポーリングの代わりに、このシグナルを条件として使えます。
4. **スクリーンショットと要素ツリーを、ステップの結果と一緒に非同期で返す。** chunked transfer で、ステップごとに送ります。証拠取得は、ステップ自体の完了を測る臨界パスの外にとどまります。

### タップ合成の下限と、それを取り除く選択肢

XCUITest は、合成した各イベントの前後で、アプリの静止（quiescence）を待ちます。上記の変更を加えても、この待ちが 100〜200 ミリ秒の下限として残る場合があります。その場合は、WebDriverAgent がすでに採っている private API の経路が選択肢になります。`XCUIApplicationProcess` の `waitForQuiescenceIncludingAnimationsIdle:` を無効化する方法です。

この変更はテストバンドル内部で完結するので、検査対象のアプリには影響しません。ただし、Apple の private API に依存します。Xcode の更新で、予告なく変わったり削除されたりする可能性があります。この項目は、下限が実際に問題になると判明した場合に、このリスクを受け入れます。先回りして行う変更ではありません。

## 検討した代替案

- **ランナー内で単一スナップショットからの解決に一本化せず、属性をそのつど解決し続ける。** 却下します。これは、BE-0407 がすでにホスト側で狙っている冗長な読み取りのパターンそのものです。同じパターンを端末側で再現する新しい実行機を構築すると、この項目自身の動機の大半を失います。
- **実行機のセレクタ解決を、Swift へ移植せずホスト経由のままにする。** 却下します。セレクタ解決のためにホストへ差し戻すと、この項目が取り除こうとしている往復を再び持ち込みます。今日のドライバに対するレイテンシの改善が残りません。
- **XCUITest の quiescence 待ちを、フォールバックとしてではなく最初から無効化する。** 出発点としては却下します。この方法は、非公開の private API に依存します。この待ちの実際の影響も、この項目の残りの設計を構築してトレースするまでわかりません。実測された下限に応じたフォールバックとして残せば、価値が確かめられる前に、この脆さを引き受けずに済みます。
- **座標タップの後、stale が疑われるときだけでなく、常に identity を再検証する。** 却下ではなく、検討中です。常に再検証するのはより安全な既定値であり、出発点としてはこちらから始めます。ただし、タップのたびに属性読みの往復をもう 1 回持ち込むので、作業単位 2 が節約する分の一部を返してしまいます。stale 判定のヒューリスティックであれば、通常の経路でも節約を保てます。たとえば、解決してから注入するまでの間に端末を操作するアクチュエーションが起きていなければ、再検証を省きます。実行機がすでに把握している自分自身のタイミング情報を使う方法です。どのヒューリスティックを既定として使うのに十分安全かは、この作業単位の着地前に検討が必要です。下の進捗チェックリストでは、確定した設計ではなく未解決の論点として扱っています。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

**順番の状態：BE-0408 の完了待ちです**（詳細設計の「実装の順番」を参照）。
それまでは下のチェックリストに着手しないでください。

- [ ] `resolve_unique` のセレクタ意味論を Swift に移植する。プロトコル項目の
  フィクスチャ拡張が着地した時点で、driver conformance suite
  （[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)）
  で検証する。
- [ ] `APIHandler` に `POST /scenario` を追加し、プロトコル項目の段階 4 に沿った
  ステップの列を受け付ける。
- [ ] 座標タップの後に identity 再検証を行うかどうかを決める stale 判定の
  ヒューリスティックを、実装より前に決めて文書化する（「検討した代替案」を参照）。
- [ ] 解決済みの frame 中心に対する座標タップを、そのヒューリスティックを
  組み込んだ形で実装する。
- [ ] BE-0310 の画面遷移シグナルをランナープロセスへ直接届くように拡張し、
  `settled` の条件として使う。
- [ ] スクリーンショットと木の取得を臨界パスから外し、ステップの結果とともに
  非同期で返す。
- [ ] この実行機に対して実際の `tap` ステップをトレースし、上記の 200〜350
  ミリ秒という見積もりと比較した結果のステップごとの壁時計をここに記録する。
- [ ] 上記を行ってもなおタップ合成の下限が無視できない場合は、
  `waitForQuiescenceIncludingAnimationsIdle:` の無効化をフォローアップとして検討する。
- [x] `roadmap-id` ワークフローが `main` 上で 4 項目の ID を採番したら、ドライバ内部
  調整、端末側プロトコル、Android 実行機の各項目との間で `関連` の相互リンクを補う
  （BE-0407 にある同じチェック項目を参照）。

## 参考

[BE-0114 — backend 非依存の挙動を検査する driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)、
[BE-0310 — アクセシビリティの画面遷移通知による readiness 判定の精度向上](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md)、
[BE-0396 — SFSafariViewController の要素ツリーをそれを描くプロセスから読む](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md)、
[`bajutsu/common/drivers/base.py`](../../bajutsu/common/drivers/base.py)、
[`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)

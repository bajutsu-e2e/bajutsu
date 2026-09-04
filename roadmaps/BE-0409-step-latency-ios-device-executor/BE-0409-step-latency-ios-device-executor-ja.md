[English](BE-0409-step-latency-ios-device-executor.md) · **日本語**

# BE-0409 — XCTest ランナー内の iOS 端末側ステップ実行機

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0409](BE-0409-step-latency-ios-device-executor-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0409") |
| トピック | Platform support |
| 関連 | [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)、[BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md)、[BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md)、[BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md)、[BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md)、[BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md) |
<!-- /BE-METADATA -->

## はじめに

関連する別提案は、端末側ステップ実行プロトコルを定義しています。何をホスト側から
端末側に移し、何をホスト側に残すか、そして両プラットフォームが共有するセレクタ
意味論の契約です。これは、ホスト側のドライバ調整だけでは届かない、Bajutsu の
250〜500 ミリ秒という 1 ステップあたりの目標に到達する道筋です。この項目は、
そのプロトコルの実装のうち iOS 側の半分です。既存の `APIHandler` の上に、XCTest
ランナープロセス内で動くステップ実行機を構築し、すべてのポーリングを HTTP 経由で
ホストへ差し戻すのではなく、セレクタの解決と条件の評価をネイティブに行います。

## 動機

今日、iOS のアクチュエーションと読み取りはどちらも `XcuitestElementProvider` を
経由し、ホストが発行する `query` や `tap` はそれぞれ完全な HTTP 往復です。
`wait for` ステップはこの方式で 50 ミリ秒ごとにポーリングします。`POST /tap` 単体
でも、タップを合成する前に約 11 個の要素属性を順番に解決しています。同じ解決と
条件評価を、中間の読み取りをいちいちホストへ返すのではなく、ランナープロセスの
内側で行えば、ポーリングのたびの往復と属性読みのたびの往復がなくなります。
ランナーはすでにアクセシビリティツリーを手元に持っているためです。

構築後にこの項目を照らし合わせるべき見積もりは次のとおりです。`app.snapshot()`
1 回で約 35 ミリ秒、座標によるタップで 100〜200 ミリ秒、settle の判定で 35〜70
ミリ秒、合計でおよそ 200〜350 ミリ秒であり、スクリーンショットは臨界パスの外に
出します。後から読む人は、ドライバ内部調整の別項目が測定を取ったのと同じ方法で
これを確かめられます。この実行機に対して実際の `tap` ステップをトレースし、
今日の iOS の基準値である 0.95〜1.07 秒と比較すればよいのです。

## 詳細設計

**実装の順番。** この項目は、関連する 4 項目の厳密な順番のうち 3 番目です。ドライバ内部
調整の項目、端末側プロトコルの項目、この項目、Android 実行機の項目の順に進みます。
**この項目への着手は、端末側プロトコルの項目が完了するまで行ってはいけません。** この
項目はその項目のプロトコルとセレクタ意味論の契約を実装するものであり、まだ変わって
いる設計に対して着手すれば、設計が変わるたびに作り直しが必要になります。この項目が
出荷した後は、Android 実行機の項目についても同じ理由で、この項目が完了するまで
着手してはいけません。2 つのプラットフォーム実行機をこの順に並べることで、セレクタ
意味論の移植（ここでは `resolve_unique` を Swift へ、Android 側では Kotlin へ）を、
まずこの項目で 1 度行い、そこで見つかった課題をもう一方のプラットフォームが
それぞれ独立に発見し直す事態を避けられます。

### アプリ内 SDK ではなくランナープロセスを選ぶ理由

実行機は、アプリ内 SDK（BajutsuKit）ではなく XCTest ランナープロセスの内側で
動かします。理由は 3 つです。

- BajutsuKit には、イベント注入、キーボード入力、frame を伴うアクセシビリティ
  ツリー、スクリーンショット取得のいずれもありません。`BajutsuTouch` は観測専用の
  swizzle であり、入力を合成しません。
- SpringBoard の権限ダイアログやシステムキーボード、`SFSafariViewController` の
  コンテンツは、アプリプロセスの内側からは届きません。これは、
  [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md)
  が Safari のコンテンツを読むためにすでに越える必要のあった境界と同じです。
- アプリのクラッシュを観測できるのは、プロセス境界があるからこそです。アプリ内の
  実行機は、検査対象のアプリと運命をともにして落ちてしまいます。

### 実行機

既存の `APIHandler` の HTTP サーバーへの追加として構築します。ステップの列
（関連プロトコル項目の段階 4 に沿ったもの）を受け取る `POST /scenario` を
追加します。実行機はネイティブに 4 つのことを行います。

1. **単一の `app.snapshot()` からセレクタを解決する。** ホスト側の
   `resolve_unique` セレクタロジック（`bajutsu/common/drivers/base.py`）を
   Swift に移植し、属性ごとに新しく読み取るのではなく、同じスナップショットに
   対してランナー内で実行します。
2. **解決済み要素の frame 中心を座標でタップし、属性の再読みは stale が疑われる
   ときだけ行う。** これは、同じ技法についてドライバ内部調整の別項目が採っているのと
   同じ立場を、この実行機が行うすべてのタップへ一般化したものです。座標タップの
   方式自体は新しくありません。
   [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md)
   が Safari のコンテンツに対してすでに採っています。着手前に検討が必要な点も、
   別項目が挙げているのと同じ理由からです。座標タップは、要素が移動していた場合の
   正しさというリスクをレイテンシと引き換えにするため、stale 判定のフォールバックを
   正しく設計する必要があります。詳しくは下の「検討した代替案」を参照してください。
3. **条件待ちを、ランナー内部だけで完結する 30〜40 ミリ秒周期の `snapshot()`
   ループで評価する。** BajutsuKit の画面遷移シグナル
   （[BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md)）
   は今日、Python 側の collector にだけ届いています。これをランナーへ直接届く
   ように拡張すれば、`settled` はツリー差分のポーリングではなく、このシグナル自体を
   条件として使えます。
4. **スクリーンショットと要素ツリーを、ステップの結果と一緒に非同期で返す。**
   chunked transfer でステップごとに送ります。証拠取得は、ステップ自体の完了を
   測る臨界パスの外にとどまります。

### タップ合成の下限と、それを取り除く選択肢

XCUITest は、合成した各イベントの前後でアプリの静止（quiescence）を待ちます。
上記の変更を加えてもなお、この待ちが 100〜200 ミリ秒の下限として残る場合は、
WebDriverAgent がすでに採っている private API の経路が選択肢になります。
`XCUIApplicationProcess` の `waitForQuiescenceIncludingAnimationsIdle:` を
無効化する方法です。これはテストバンドル内部の変更であり、検査対象のアプリには
影響しません。ただし、Apple の private API に依存するため、Xcode の更新で
予告なく変わったり削除されたりする可能性があります。このリスクは、実際に
この下限が問題になると判明した場合に限って受け入れるものであり、あらかじめ
先回りして行う変更ではありません。

## 検討した代替案

- **ランナー内で単一スナップショットからの解決に一本化せず、属性をそのつど
  解決し続ける。** 却下します。これはまさに、ドライバ内部調整の別項目がすでに
  ホスト側で狙っている冗長な読み取りのパターンそのものです。同じパターンを
  端末側で再現する新しい実行機を構築すれば、この項目自身の動機の大半を
  失うことになります。
- **実行機のセレクタ解決を、Swift へ移植せずホスト経由のままにする。** 却下
  します。ホストへ差し戻してセレクタを解決すれば、この項目が取り除こうとしている
  往復そのものを再び持ち込むことになり、今日のドライバに対するレイテンシの
  改善が残りません。
- **XCUITest の quiescence 待ちを、フォールバックとしてではなく最初から無効化
  する。** 出発点としては却下します。これは非公開の private API に依存しており、
  この待ちの実際の影響は、この項目の残りの設計が構築されトレースされるまでは
  わかりません。実測された下限に応じたフォールバックとして残しておけば、
  その価値が確かめられる前に、この脆さを引き受けずに済みます。
- **座標タップの後、stale が疑われるときだけではなく常に identity を
  再検証する。** 却下ではなく検討中です。これはより安全な既定値であり、
  出発点としてはこちらから始めるべきです。無条件の再検証は、タップのたびに
  属性読みの往復をもう 1 回持ち込むため、作業単位 2 が節約する分の一部を
  返してしまいます。stale 判定のヒューリスティック（たとえば、解決してから
  注入するまでの間に端末を操作するアクチュエーションが起きていなければ
  再検証を省く、など、実行機がすでに把握している自分自身のタイミング情報を
  使う方法）であれば、通常の経路でも節約を保てます。どのヒューリスティックを
  既定として使うのに十分安全かは、この作業単位の着地前に検討が必要です。
  下の進捗チェックリストでは、これを確定した設計ではなく未解決の論点として
  扱っています。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

**順番の状態：端末側プロトコルの項目の完了待ちです**（詳細設計の「実装の順番」を参照）。
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
  （ドライバ内部調整の項目にある同じチェック項目を参照）。

## 参考

[BE-0114 — backend 非依存の挙動を検査する driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)、
[BE-0310 — アクセシビリティの画面遷移通知による readiness 判定の精度向上](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md)、
[BE-0396 — SFSafariViewController の要素ツリーをそれを描くプロセスから読む](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md)、
[`bajutsu/common/drivers/base.py`](../../bajutsu/common/drivers/base.py)、
[`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)

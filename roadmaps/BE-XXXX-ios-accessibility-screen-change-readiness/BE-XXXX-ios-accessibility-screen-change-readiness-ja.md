[English](BE-XXXX-ios-accessibility-screen-change-readiness.md) · **日本語**

# BE-XXXX — アクセシビリティの画面遷移通知で readiness と settled 待ちの判定をより正確にする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-ios-accessibility-screen-change-readiness-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Platform support |
| 関連 | [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md), [BE-0087](../BE-0087-idb-action-settle/BE-0087-idb-action-settle-ja.md), [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md), [BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait-ja.md) |
<!-- /BE-METADATA -->

## はじめに

この項目は、Bajutsu の iOS バックエンドに**画面遷移がいつ完了したか**という肯定的なシグナルを与えます。
このシグナルによって、今は画面遷移の完了を推測しているだけの 2 か所——起動直後の readiness ゲートと
`settled` 待ち——が、画面の読み取りを繰り返して得た推測ではなく、画面遷移そのものへ反応できるようになります。
シグナルの出どころは、標準的なコンテナ遷移が完了したときに iOS 自身が投げるアクセシビリティ通知
（`UIAccessibility.screenChangedNotification`）です。この通知を、どのターゲットも同一に組み込む
テスト補助パッケージ `BajutsuKit` へのオプトインの追加によって観測します。この通知は、
`UINavigationController`・`UITabBarController`・モーダル提示を直接支え、`SwiftUI` の `NavigationStack` を
内部で支える UIKit のコンテナ機構が投げるものです。そのためシグナルは UIKit アプリと SwiftUI アプリを、
コンテナ遷移の粒度で 1 つの仕組みで覆い、テスト対象のアプリには一切変更を求めません。ターゲットが `BajutsuKit` を
組み込まない場合は、readiness ゲートと `settled` 待ちのいずれも従来どおりツリー差分のポーリングに
フォールバックします。したがってこの変更は、オプトインするアプリにとっての強化ではあっても、
オプトインしないアプリに対する新たな要求には決してなりません。

## 動機

今の Bajutsu は「画面が落ち着いたか」を、アクセシビリティツリーを繰り返し読み、それが変化しなくなるのを
見ることで判定しています。起動直後の readiness ゲート `_await_ready`
（`bajutsu/platform_lifecycle/readiness.py`）は、アプリが前面に出るまで `query()` をポーリングし、
[BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)
によって namespace の外にある SpringBoard の画面を弾くよう強化されています。`settled` 待ち
（`bajutsu/orchestrator/waits.py` の `_wait_settled`）は、連続する 2 回の `query()` の読みが一致した
時点で先へ進みます。どちらの判定も、遷移が実際に終わったというシグナルからではなく、ポーリングをまたいで
観測された変化の**不在**から導いた推測です。

この推測は、遅いマシンや負荷の高いマシンで脆く崩れます。そして iOS のフレーキーさが表面化するのは、
まさにそうしたマシンです。連続する 2 回のツリー読み取りは、過渡的な中間フレーム——途中で止まった
ナビゲーションのアニメーション、最終的な subview がまだレイアウトされていない画面——で一致しうるので、
ポーリングはまだ動いている画面を settled と宣言し、次のステップが早すぎるタイミングで作用してしまいます。
逆向きの失敗は、idb バックエンドを解析した
[BE-0087](../BE-0087-idb-action-settle/BE-0087-idb-action-settle-ja.md) 自身が示しています。負荷の高い
CI シミュレータでは `describe-all` の 1 回の読み取りに数秒かかるため、数回の読み取りで表したポーリング予算は、
本当に遅い遷移が終わる前に尽きてしまいます。ツリー差分は、止まったアニメーションと終わったアニメーションを
区別できません。差分が参照する唯一の証拠は、2 回の読みがたまたま一致したかどうかだけだからです。

足りないシグナルは、iOS 自身がすでに投げています。標準的なコンテナ遷移が完了したとき——ナビゲーションの
push や pop、モーダルの提示や取り下げ、タブの切り替え——UIKit は VoiceOver が新しい画面へフォーカスを
移し直せるように `UIAccessibility.screenChangedNotification` を投げます。この通知は、アクセシビリティの
規約そのものの設計によって、遷移が落ち着いた**あと**に発火します。この発火のタイミングこそ、readiness
ゲートと `settled` 待ちがツリー差分から再構成しようとしているものです。通知を直接観測すれば、推測を、
それが近似していた事実そのものに置き換えられます。

Bajutsu は、どのアプリにも画面のコードを変えさせることなく、この通知を観測できます。この形の能力を
実現する仕組みと方針を、プロジェクトはすでに持っているからです。
[BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md) は、アプリのプロセス
内部からしか到達できない能力を、per-app 設定ではなく、どのターゲットも同一に組み込む統一された
オプトインのソフトウェア開発キット（SDK）で実現するという恒久的な設計方針を定めました。この形なら
prime directive 3 のもとで app-agnostic を保てます。`BajutsuKit` は iOS における、まさにその SDK です。
`BajutsuKit` のネットワークキャプチャは、テスト対象のアプリに組み込まれ、観測した各リクエストを
プロセス外の Bajutsu が動かすコレクタへ報告します（`BAJUTSU_COLLECTOR` の launch 環境変数で有効化される
`BajutsuNet`）。画面遷移の observer も同じ形です。アクチュエータが外側から観測できないプロセス内部の
観測を、同じアプリ側の連携チャネルを通じて surface するものなので、新しい仕組みを発明するのではなく、
確立されたパターンを再利用します。

シグナルの届く範囲には意図的な境界があり、それを明示することはこの提案の一部であって、あとになっての
不意打ちではありません。`UIAccessibility.screenChangedNotification` は**画面**の遷移で発火するもので、
1 つの画面の内側での通常のデータ更新——ラベルへ反映されたカウンタ、フィールドの編集済みテキスト——では
発火しません。この画面内の値反映レースは
[BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait-ja.md) が扱う主題で
あり、この項目は扱いません。画面のデータがその場で変わるとき、投げられるのはせいぜい
`UIAccessibility.layoutChangedNotification` であり、自動では何も投げられない場合が多いので、そこでは
[BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait-ja.md) の要素単位の
条件待ちが引き続き正しい道具です。この項目は、readiness と `settled` が判定に使う**画面遷移**のシグナルを
より正確なものにするのであって、値反映の作業を置き換えるものではなく、それを補い合う関係にあります。

共有された名前は、もう 1 つの補足を招きます。Bajutsu にはすでに、シナリオの待ち条件と capturePolicy の
イベントの双方に `screenChanged` という名前のものがあり、`until: screenChanged` の待ちは、この項目が
改善するツリー差分ポーリング（読みをまたいだ `current != before`）でまさに今実装されています。この
既存の `until: screenChanged` の待ちは、同じシグナルのもう 1 つの自然な消費者であり、名前の再利用は
偶然ではありません。それでもこの項目は、消費者を、*動機*が辿った 2 つの既定でつねに有効なフレーキーさの
現場——起動直後の readiness ゲートと `settled` 待ち——にとどめ、作者が明示的に呼ぶ
`until: screenChanged` のステップは今のツリー差分ポーリングのまま残します。シグナルを
`until: screenChanged` の待ちへ流し込むのは、きれいな後続作業です。共有された語が言及なき混同として
読まれないよう、ここで名前を挙げます。外すのは、この項目の作業分解を、作者のオプトインなしにフレークする
2 つの現場へ絞るためです。

## 詳細設計

提案の粒度です。以下の各作業単位はオプトインであり、アプリが `BajutsuKit` を組み込むことを条件とし、
組み込まないターゲットでは今の挙動をそのまま保ちます。readiness ゲートと `settled` 待ちは、既存の
ツリー差分ポーリングをフォールバックとして残すので、どのシナリオも退行せず、どのアプリにも SDK の
組み込みを新たに要求しません。どの作業単位も `run`／CI の判定の近くに LLM を置かず、固定の `sleep` を
足しません。どの待ちも条件待ちのままで、いまはより正確に判定できる条件が使えるようになります。

シグナルは、`_await_ready` と `_wait_settled` へ渡す読み取り専用の入力として
決定性コアへ届きます。値の出どころは、コレクタが Python 側に蓄える遷移レコードです。正確な seam——
バックエンドが広告する `Driver` の capability にするか、2 つの待ち関数へ渡すコレクタへの問い合わせに
するか——は実装へ委ねますが、1 つの制約を課します。readiness と settle のコアはシグナルを読み取りとして
だけ参照し、今日すでに独立を保っているネットワークキャプチャのコレクタの状態に対して、書き込み経路や
判定経路の依存を決して持ちません。

### 作業分解（MECE）

1. **`BajutsuKit` でアクセシビリティの画面遷移通知を観測する。** メインの run loop 上で
   `UIAccessibility.screenChangedNotification` を購読するオプトインの observer を追加し、
   monotonic なタイムスタンプと通し番号のカウンタを小さな observable なストアへ記録します。このストアは、
   ネットワークの exchange について `BajutsuExchangeStore` がすでに使っているアプリ内ストアと同じ形です。
   observer が購読するのは、UIKit のコンテナ機構が**自動で**投げる通知だけです。これがアプリの画面変更を
   要さないことの拠りどころです。アプリが手で投げなければならない通知——たとえばページの説明文字列を
   アプリが与える `UIAccessibility.pageScrolledNotification`——は、購読してもこの項目が退ける per-app な
   連携なしには何も観測できないので、意図して除きます。
   有効化は、`BajutsuNet` が `BAJUTSU_COLLECTOR` を条件にするのとまったく同じく、注入された launch 環境変数を
   条件にします。これによって、`BajutsuKit` を組み込んでいても Bajutsu の run の外で動くアプリは何も
   観測しません。この作業単位が触れるのは `BajutsuKit` だけで、テスト対象のアプリの画面コードには決して
   触れません。

2. **既存の連携チャネルを通じてシグナルをプロセス外へ報告する。** 観測した遷移を Bajutsu へ surface する
   経路は、ネットワークキャプチャが exchange をすでに surface しているのと同じにします。すなわち、run ごとの
   トークンを付けた `BAJUTSU_COLLECTOR` チャネルで、Bajutsu が動かすコレクタへ報告します。これにより、
   トランスポートと認証とプロセス境界のいずれも、プロジェクトがすでに運用しているものになり、新しい経路になりません。
   報告するレコードは最小限——遷移の種別と monotonic なタイムスタンプ——で画面の内容を一切含まないので、
   証拠のプライバシーに関する新たな面を増やしません。

3. **readiness ゲートからシグナルを参照し、ツリー差分をフォールバックにする。** `_await_ready` を拡張し、
   アプリが起動以降に画面遷移を少なくとも 1 回報告している場合は、
   [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)
   が積み重ねた要素数と namespace 内判定のヒューリスティックではなく、その遷移によって readiness を満たします。
   シグナルを報告しないターゲットに向けたフォールバックの梯子として、
   [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)
   の優先順位はそのまま保ちます。明示的な `readyWhen` セレクタ、次に namespace 内の要素、次に 2 つ以上の
   要素数という順です。シグナルは、その梯子の新たな最上段になり、SDK が組み込まれているときにだけ使えます。
   この最上段が依存する 1 つのケースは、コードレビューでは決着できません。すなわち、アプリの**最初の**画面が
   cold launch で現れるときに `UIAccessibility.screenChangedNotification` がそもそも投げられるか、という
   ケースです。readiness の事象は最初の画面が立ち上がることであり、これは後続の push やタブ切り替えのような、
   前画面からの**変化**ではないからです。作業単位 5 がこれを測ります。起動画面が何も投げない場合、readiness は
   [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)
   の梯子にとどまるだけで退行はなく、シグナルの確実な利得は、画面**間**の遷移を画面遷移として曖昧さなく
   観測する `settled` 待ち（作業単位 4）に落ちます。

4. **`settled` 待ちからシグナルを参照し、ツリー差分をフォールバックにする。** `_wait_settled` を拡張し、
   シグナルが使える場合は、ステップの wall-clock の期限で区切った短い静止の窓のあいだ、それ以上の画面遷移が
   報告されなくなった時点で画面を settled と数えます。これは「2 回の読みがたまたま一致した」ではなく、
   「最後の遷移が終わり、新しい遷移が始まっていない」という肯定的な判定です。シグナルが使えない場合、
   `_wait_settled` は連続する 2 回の読みが変わらないという今の挙動を、空のツリーやシステムアラートに覆われた
   ツリーを settled と扱わない既存の拒否も含めて、そのまま保ちます。タイムアウト時は、今のドキュメントの
   とおり best-effort で先へ進みます。

5. **シグナルが想定どおり発火することを、UIKit と SwiftUI の双方についてシミュレータ上で検証する。** この提案は、
   コードレビューでは決着できない 1 つの経験的な仮定に依拠しています。すなわち、`SwiftUI` の
   `NavigationStack` は内部で `UINavigationController` に支えられているため、その遷移も UIKit のコンテナ遷移と
   同じ粒度で `UIAccessibility.screenChangedNotification` を投げる、という仮定です。名前を付けた環境で、
   2 つの showcase iOS ターゲット——UIKit アプリ（`demos/showcase/ios/uikit`）と SwiftUI アプリ
   （`demos/showcase/ios/swiftui`）——の双方に対してこの仮定を確認します。それぞれについて、cold launch から
   最初の画面が現れる場面（作業単位 3 の readiness の段が依存するケース）と、代表的な
   ナビゲーションの push、モーダルの提示、タブの切り替えで、observer が遷移を記録し、readiness と `settled` が
   その遷移で発火することを測ります。シグナルが**覆わない**ケース——標準コンテナを迂回するカスタム遷移と、
   画面内のデータ更新——についても結果を記録し、フォールバックの役割を仮定ではなく証拠から裏づけます。
   このオンデバイスでの確認がこの項目のゲートであり、
   [BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait-ja.md) が提案する
   シミュレータ整合性ゲートや、Platform support の各項目がすでに用いているオンデバイスのゲートと
   同じ精神に立ちます。

## 検討した代替案

- **アクセシビリティ通知を観測する代わりに `UIViewController.viewDidAppear` を swizzle する。**
  `UIViewController` へのメソッド swizzling は、アプリのコードを変えずに UIKit のコントローラごとの
  ライフサイクルをフックできますが、SwiftUI には同等には届きません。SwiftUI のビューは値型で、
  swizzle 対象となる共有のビューコントローラ基底クラスを持たないので、フックは `UIHostingController` の
  境界でしか発火せず、1 つの hosting controller の内側での遷移を取りこぼします。シグナルが UIKit と
  SwiftUI を等しく覆うという要件が、この手段を退けます。アクセシビリティ通知は、両フレームワークが
  乗る共有のコンテナ機構が投げるものなので、1 つの observer で双方を覆います。

- **画面ごとに `.onAppear`（SwiftUI）／ライフサイクル呼び出し（UIKit）を足して SDK へ報告する。**
  各画面に自分の出現を告げさせれば、アクセシビリティ通知が取りこぼしうる SwiftUI の hosting controller
  内側の遷移も覆えますが、テスト対象のどのアプリのどの画面にも編集が要ります。それはまさに、prime
  directive 3 と
  [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md) の方針がアプリ自身の
  コードから遠ざける per-app な連携です。アプリが一度組み込むだけの統一 SDK は app-agnostic ですが、
  作者がアプリのビューへ振りまく modifier は per-app な差異です。アプリの画面変更を要さない、より狭い
  自動シグナルのほうを採り、覆う範囲の境界は、あらゆるアプリへ作業を押し込んで閉じるのではなく、
  作業単位 5 で記録します。

- **何もせず、今のツリー差分ポーリングにそのまま頼る。** 許容でき、`BajutsuKit` を組み込まないどの
  ターゲットでもこれが挙動であり続けます。ただし、オプトインできるアプリにとっては、これだけでは
  足りません。ツリー差分は、途中で止まった遷移のフレームと終わった画面を区別できず、遅いマシンでは
  ポーリング予算を使い果たしうるからです。*動機*が辿った 2 つの失敗の様式です。SDK を組み込む気のある
  アプリは、組み込まないアプリに何のコストも課さずに、厳密により正確なシグナルを得ます。

- **シグナルを画面内のデータ更新まで広げる。** スコープ外であり、かつ仕組みとして誤っているので退けます。
  その場のデータ更新が投げるのはせいぜい `UIAccessibility.layoutChangedNotification` で、自動では何も
  投げないことが多いので、画面遷移の observer では確実には見えません。
  [BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait-ja.md) がそのレースを、
  本来あるべき要素単位ですでに扱っています。この項目は、確実に観測できる画面遷移のシグナルへ、意図して
  スコープをとどめます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 作業単位 1 — `BajutsuKit` でアクセシビリティの画面遷移通知を観測する（オプトイン）。
- [ ] 作業単位 2 — 既存のコレクタチャネルを通じてシグナルをプロセス外へ報告する。
- [ ] 作業単位 3 — `_await_ready` からシグナルを参照し、BE-0218 のツリー差分の梯子をフォールバックにする。
- [ ] 作業単位 4 — `_wait_settled` からシグナルを参照し、ツリー差分の挙動をフォールバックにする。
- [ ] 作業単位 5 — シグナルが UIKit と SwiftUI の双方で発火することをシミュレータで確認する（ゲート）。

## 参考

- [`bajutsu/platform_lifecycle/readiness.py`](../../bajutsu/platform_lifecycle/readiness.py) —
  この項目が肯定的なシグナルを与える起動直後の readiness ゲート `_await_ready`。
- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) — この項目が肯定的なシグナルを
  与える `settled` 待ち `_wait_settled`。
- [`BajutsuKit`](../../BajutsuKit) — observer が加わるオプトインの iOS SDK。`BajutsuNet` と
  `BajutsuExchangeStore` は、この項目が倣うネットワークキャプチャの前例です。
- [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)
  — この項目が最上段を足す readiness の梯子。
- [BE-0087](../BE-0087-idb-action-settle/BE-0087-idb-action-settle-ja.md) — この項目のシグナルが settle の
  経路で避けるツリー読み取りのコストを定量化した、アクション前 settle の解析。
- [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md) — この項目が
  app-agnostic を保つために倣う、アプリ側 SDK 連携の方針。
- [BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait-ja.md) — この項目が
  意図して覆わない、補い合う関係にある画面内の値反映レース。

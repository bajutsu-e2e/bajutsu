[English](BE-0310-ios-accessibility-screen-change-readiness.md) · **日本語**

# BE-0310 — アクセシビリティの画面遷移通知で readiness と settled 待ちの判定をより正確にする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0310](BE-0310-ios-accessibility-screen-change-readiness-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0310") |
| 実装 PR | [#1309](https://github.com/bajutsu-e2e/bajutsu/pull/1309) |
| トピック | Platform support |
| 関連 | [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md), [BE-0087](../BE-0087-idb-action-settle/BE-0087-idb-action-settle-ja.md), [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md), [BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait-ja.md) |
<!-- /BE-METADATA -->

## はじめに

この項目は、Bajutsu の iOS バックエンドに、**画面遷移がいつ完了したか**という肯定的なシグナルを与えます。

現在は、起動直後の readiness ゲートと `settled` 待ちの 2 か所が、遷移の完了を推測しています。どちらも、画面の読み取りを繰り返して得た推測ではなく、画面遷移そのものに反応できるようになります。

シグナルの出どころは、テスト補助パッケージ `BajutsuKit` へのオプトインの追加です。`BajutsuKit` は、どのターゲットも同一に組み込みます。追加するフックは、UIKit の `UIViewController.viewDidAppear` を対象とします。このメソッドは、出現の遷移が完了した**あと**に呼ばれます。

当初は、この遷移で iOS が投げるアクセシビリティ通知 `UIAccessibility.screenChangedNotification` を観測する計画でした。しかし、このシンボルは外向き専用でした。`UIAccessibility.post(...)` の enum ケースであり、観測できる `NSNotification.Name` ではありません。そのため、下の*検討した代替案*で比較した viewDidAppear のフックを採用しました。

`UINavigationController`、`UITabBarController`、モーダル提示は、それぞれビューコントローラの出現を直接引き起こします。`SwiftUI` の `NavigationStack`、`.sheet`/`.fullScreenCover`、タブの切り替えは、いずれも内部で `UIHostingController` を提示します。したがって、1 つの仕組みで UIKit アプリと SwiftUI アプリをコンテナ遷移の粒度で覆えます。テスト対象のアプリには一切変更を求めません。

ターゲットが `BajutsuKit` を組み込まない場合、2 つのゲートは従来どおりツリー差分のポーリングにフォールバックします。この変更は、オプトインするアプリの強化であり、オプトインしないアプリへの新たな要求にはなりません。

## 動機

現在の Bajutsu は、アクセシビリティツリーを繰り返し読み、変化しなくなるのを見て「画面が落ち着いたか」を判定しています。判定する箇所は 2 つあります。

- 起動直後の readiness ゲート `_await_ready`（`bajutsu/platform_lifecycle/readiness.py`）は、アプリが前面に出るまで `query()` をポーリングします。[BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md) は、namespace の外にある SpringBoard の画面を弾くようにゲートを強化しました。
- `settled` 待ち（`bajutsu/orchestrator/waits.py` の `_wait_settled`）は、連続する 2 回の `query()` の読みが一致した時点で先へ進みます。

どちらの判定も、ポーリングをまたいで観測された変化の**不在**から結果を推測します。遷移が終わったというシグナルは受け取っていません。

この推測は、遅いマシンや負荷の高いマシンで崩れやすく、iOS のフレーキーさはそうしたマシンで表面化します。連続する 2 回のツリー読み取りは、過渡的な中間フレームで一致することがあります。例は、途中で止まったナビゲーションのアニメーションと、最終的な subview がまだレイアウトされていない画面です。すると、ポーリングは動いている途中の画面を settled と宣言し、次のステップが早すぎるタイミングで作用します。

逆向きの失敗は、idb バックエンドを解析した [BE-0087](../BE-0087-idb-action-settle/BE-0087-idb-action-settle-ja.md) が示しています。負荷の高い CI シミュレータでは、`describe-all` の 1 回の読み取りに数秒かかります。数回の読み取りで表したポーリング予算は、遅い遷移が終わる前に尽きることがあります。

ツリー差分は、止まったアニメーションと終わったアニメーションを区別できません。差分が参照する証拠は、2 回の読みがたまたま一致したかどうかだけだからです。

足りないシグナルは、iOS 自身がすでに投げています。標準的なコンテナ遷移が完了すると、UIKit は `UIAccessibility.screenChangedNotification` を投げます。VoiceOver が新しい画面へフォーカスを移し直すための通知です。コンテナ遷移には、ナビゲーションの push と pop、モーダルの提示と取り下げ、タブの切り替えが含まれます。

アクセシビリティの規約の設計により、この通知は遷移が落ち着いた**あと**に発火します。この発火のタイミングは、readiness ゲートと `settled` 待ちがツリー差分から再構成しようとしているものです。完了を直接捉えれば、推測を、推測が近似していた事実そのものに置き換えられます。

この事象に名前を与えたのは通知です。ただし、実装が観測に使うのは、同じ完了を表す `UIViewController.viewDidAppear` です。通知は観測できる `NSNotification.Name` ではありませんでした。*詳細設計*の作業単位 1 と*検討した代替案*を参照してください。

Bajutsu は、どのアプリにも画面のコードを変えさせることなく、この完了を捉えられます。この形の能力を実現する仕組みと方針を、プロジェクトがすでに持っているからです。

[BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md) は、恒久的な設計方針を定めました。アプリのプロセス内部からしか到達できない能力は、per-app 設定ではなく、どのターゲットも同一に組み込む統一されたオプトインのソフトウェア開発キット（SDK）で実現します。この方針により、prime directive 3 のもとで app-agnostic を保てます。

`BajutsuKit` は、iOS におけるその SDK です。ネットワークキャプチャは、テスト対象のアプリに組み込まれ、観測した各リクエストをプロセス外の Bajutsu が動かすコレクタへ報告します（`BAJUTSU_COLLECTOR` の launch 環境変数で有効化される `BajutsuNet`）。

画面遷移の observer も、同じ形です。アクチュエータが外側から観測できないプロセス内部の観測を行い、同じアプリ側の連携チャネルを通じて Bajutsu へ渡します。新しい仕組みは発明せず、確立されたパターンを再利用します。

シグナルの届く範囲には、意図的な境界があります。この境界を明示することは、提案の一部です。`UIAccessibility.screenChangedNotification` は**画面**の遷移で発火します。1 つの画面の内側での通常のデータ更新では発火しません。データ更新の例は、ラベルへ反映されたカウンタと、フィールドの編集済みテキストです。

画面内の値反映レースは、[BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait-ja.md) が扱います。この項目は扱いません。画面のデータがその場で変わるとき、投げられるのはせいぜい `UIAccessibility.layoutChangedNotification` です。自動では何も投げられない場合も多くあります。その場合は、[BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait-ja.md) の要素単位の条件待ちが、引き続き正しい道具です。

この項目は、readiness と `settled` が判定に使う**画面遷移**のシグナルを、より正確にします。値反映の作業は置き換えず、補い合う関係にあります。

共有された名前について、もう 1 つ補足します。Bajutsu には、シナリオの待ち条件と capturePolicy のイベントの双方に、`screenChanged` という名前のものがあります。`until: screenChanged` の待ちは、この項目が改善するツリー差分ポーリング（読みをまたいだ `current != before`）で実装されています。この待ちは、同じシグナルの自然な消費者の 1 つであり、名前の再利用は偶然ではありません。

この項目は、消費者を、*動機*で辿った 2 つの既定でつねに有効なフレーキーさの現場に限ります。2 つの現場は、起動直後の readiness ゲートと `settled` 待ちです。作者が明示的に呼ぶ `until: screenChanged` のステップは、今のツリー差分ポーリングのまま残します。

シグナルを `until: screenChanged` の待ちへ流し込む作業は、きれいな後続作業です。共有された語が説明なき混同として読まれないよう、ここで名前を挙げます。後続作業を外すのは、作業分解を、作者のオプトインなしにフレークする 2 つの現場へ絞るためです。

## 詳細設計

本節は提案の粒度で書きます。以下の各作業単位は、オプトインです。アプリが `BajutsuKit` を組み込むことを条件とし、組み込まないターゲットでは今の挙動をそのまま保ちます。readiness ゲートと `settled` 待ちは、既存のツリー差分ポーリングをフォールバックとして残します。どのシナリオも退行せず、どのアプリにも SDK の組み込みを新たに要求しません。

どの作業単位も、`run`/CI の判定の近くに LLM を置かず、固定の `sleep` を足しません。どの待ちも条件待ちのままで、より正確に判定できる条件が使えるようになります。

シグナルは、`_await_ready` と `_wait_settled` へ渡す読み取り専用の入力として、決定性コアへ届きます。値の出どころは、コレクタが Python 側に蓄える遷移レコードです。

正確な seam は、実装へ委ねます。seam は、バックエンドが広告する `Driver` の capability か、2 つの待ち関数へ渡すコレクタへの問い合わせのどちらかです。どちらの場合も、1 つの制約を課します。readiness と settle のコアは、シグナルを読み取りとして参照するだけです。ネットワークキャプチャのコレクタの状態に対して、書き込み経路や判定経路の依存を持ちません。コアは現在、その状態から独立を保っています。

### 作業分解（MECE）

1. **`BajutsuKit` で完了したビューコントローラの出現を観測する（`viewDidAppear` の swizzle）。** `UIViewController.viewDidAppear(_:)` を swizzle するオプトインのフックを追加します。フックは、`BajutsuURLProtocol` がすでに使う `method_exchangeImplementations` の作法を再利用します。フックは、monotonic なタイムスタンプと通し番号のカウンタを、小さな observable なストアへ記録します。このストアは、ネットワークの exchange を保持する `BajutsuExchangeStore` と同じ、アプリ内ストアの形です。

   フックが発火するのは、UIKit のコンテナ機構が**自動で**引き起こす出現です。ナビゲーションの push と pop、モーダル提示、タブの切り替えが該当します。この性質が、アプリの画面変更を要さないことの拠りどころです。

   当初は、出どころとして `UIAccessibility.screenChangedNotification` を挙げていました。このシンボルは外向き専用であり、`UIAccessibility.post(...)` の enum ケースで、観測できる `NSNotification.Name` ではありません。そのため、*検討した代替案*で比較した viewDidAppear のフックが、この作業単位を実装します。粒度は、同じコンテナ遷移のままです。

   有効化は、`BajutsuNet` が `BAJUTSU_COLLECTOR` を条件にするのと同じく、注入された launch 環境変数を条件にします。`BajutsuKit` を組み込んでいても Bajutsu の run の外で動くアプリは、何も観測しません。この作業単位が触れるのは `BajutsuKit` だけで、テスト対象のアプリの画面コードには触れません。

2. **既存の連携チャネルを通じてシグナルをプロセス外へ報告する。** 観測した遷移を Bajutsu へ渡す経路は、ネットワークキャプチャが exchange を渡している経路と同じにします。run ごとのトークンを付けた `BAJUTSU_COLLECTOR` チャネルで、Bajutsu が動かすコレクタへ報告します。トランスポート、認証、プロセス境界は、プロジェクトがすでに運用しているものになります。新しい経路は生まれません。

   報告するレコードは最小限で、遷移の種別と monotonic なタイムスタンプだけです。画面の内容は一切含まないので、証拠のプライバシーに関する新たな面は増えません。

3. **readiness ゲートからシグナルを参照し、ツリー差分をフォールバックにする。** `_await_ready` を拡張します。アプリが起動以降に画面遷移を少なくとも 1 回報告している場合は、その遷移が readiness を満たします。この場合、[BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md) が積み重ねた要素数と namespace 内判定のヒューリスティックは、readiness を決めません。

   シグナルを報告しないターゲットに向けたフォールバックの梯子として、[BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md) の優先順位はそのまま保ちます。梯子は、明示的な `readyWhen` セレクタ、namespace 内の要素、2 つ以上の要素数の順です。シグナルは、梯子の新たな最上段になります。この段は、SDK が組み込まれているときに使えます。

   この最上段が依存するケースの 1 つは、コードレビューでは決着できません。アプリの**最初の**画面が cold launch で現れるときに、`UIAccessibility.screenChangedNotification` がそもそも投げられるか、という問いです。readiness の事象は最初の画面が立ち上がることです。後続の push やタブ切り替えと違って、前画面からの**変化**ではありません。作業単位 5 が、このケースを測ります。

   起動画面が何も投げない場合、readiness は [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md) の梯子にとどまり、退行は起きません。シグナルの確実な利得は、`settled` 待ち（作業単位 4）に落ちます。`settled` 待ちは、シナリオの途中で画面**間**の遷移を観測し、そこでは画面遷移が曖昧になりません。

4. **`settled` 待ちからシグナルを参照し、ツリー差分をフォールバックにする。** `_wait_settled` を拡張します。シグナルが使える場合、短い静止の窓のあいだ、それ以上の画面遷移が報告されなくなった時点で、画面を settled と数えます。窓は、ステップの wall-clock の期限で区切ります。判定は「2 回の読みがたまたま一致した」ではなく、「最後の遷移が終わり、新しい遷移が始まっていない」という肯定的な事実に基づきます。

   シグナルが使えない場合、`_wait_settled` は連続する 2 回の読みが変わらないという今の挙動を保ちます。空のツリーやシステムアラートに覆われたツリーを settled と扱わない既存の拒否も、そのまま保ちます。タイムアウト時は、今のドキュメントのとおり best-effort で先へ進みます。

5. **シグナルが想定どおり発火することを、UIKit と SwiftUI の双方についてシミュレータ上で検証する。** この提案は、コードレビューでは決着できない経験的な仮定に依拠しています。`SwiftUI` の `NavigationStack` は内部で `UINavigationController` に支えられています。そのため、`NavigationStack` の遷移も、UIKit のコンテナ遷移と同じ粒度で `UIAccessibility.screenChangedNotification` を投げる、という仮定です。

   名前を付けた環境で、2 つの showcase iOS ターゲットに対して、この仮定を確認します。ターゲットは、UIKit アプリ（`demos/showcase/ios/uikit`）と SwiftUI アプリ（`demos/showcase/ios/swiftui`）です。それぞれのアプリで、2 つを測ります。1 つ目は、cold launch から最初の画面が現れる場面で、作業単位 3 の readiness の段が依存するケースです。2 つ目は、代表的なナビゲーションの push、モーダルの提示、タブの切り替えです。いずれの場合も、observer が遷移を記録し、readiness と `settled` がその遷移で発火することを確かめます。

   シグナルが**覆わない**ケースについても、結果を記録します。標準コンテナを迂回するカスタム遷移と、画面内のデータ更新です。フォールバックの役割は、仮定ではなく証拠から裏づけられます。

   このオンデバイスでの確認が、この項目のゲートです。[BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait-ja.md) が提案するシミュレータ整合性ゲートや、Platform support の各項目がすでに用いているオンデバイスのゲートと、同じ精神に立ちます。

## 検討した代替案

- **アクセシビリティ通知を観測する代わりに `UIViewController.viewDidAppear` を swizzle する。** *採用しました。これが実装された手段です。* 当初の計画は、`UIAccessibility.screenChangedNotification` を観測することでした。しかし、このシンボルは外向き専用でした。iOS は `.screenChanged` を、`UIAccessibility.post(notification:argument:)`（アプリから VoiceOver への向き）の enum ケースとして提供します。観測できる `NSNotification.Name` は提供しないので、購読すべき公開の通知は存在しません。購読を試みたコードは、コンパイルに失敗しました。

  この項目は当初、SwiftUI の穴を懸念して swizzle を退けていました。懸念は「フックは `UIHostingController` の境界でしか発火せず、1 つの hosting controller の内側の遷移を取りこぼす」というものでした。この懸念は、この項目が対象とする**コンテナ**遷移については過大でした。`NavigationStack` の push、`.sheet` や `.fullScreenCover` の提示、タブの切り替えは、いずれも `UIHostingController` を提示し、その `viewDidAppear` が発火します。フックは、UIKit と SwiftUI を、コンテナ遷移の粒度で等しく覆います。

  本当に取りこぼすのは、1 つの hosting controller の内側での、画面内のビュー差し替えです。これはコンテナ遷移ではなく画面内の更新であり、次の項目や [BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait-ja.md) と同じスコープ外のケースです。通知の経路が使えない以上、このフックが、アプリの画面変更を要さない、残る公開 API の手段です。

- **画面ごとに `.onAppear`（SwiftUI）／ライフサイクル呼び出し（UIKit）を足して SDK へ報告する。** 各画面に自分の出現を告げさせれば、アクセシビリティ通知が取りこぼしうる、1 つの hosting controller 内側の遷移も覆えます。ただし、テスト対象のどのアプリのどの画面にも、編集が必要です。

  この編集は、prime directive 3 と [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md) の方針が、アプリ自身のコードから遠ざけている per-app な連携です。アプリが一度組み込むだけの統一 SDK は、app-agnostic です。作者がアプリのビューへ振りまく modifier は、per-app な差異です。

  アプリの画面変更を要さない、より狭い自動シグナルのほうを採ります。覆う範囲の境界は、作業単位 5 で記録します。あらゆるアプリへ作業を押し込んで境界を閉じることはしません。

- **何もせず、今のツリー差分ポーリングにそのまま頼る。** この選択肢は許容でき、`BajutsuKit` を組み込まないターゲットでは、これが挙動であり続けます。ただし、オプトインできるアプリにとっては、これでは足りません。ツリー差分は、途中で止まった遷移のフレームと終わった画面を区別できません。遅いマシンでは、ポーリング予算を使い果たすこともあります。*動機*で辿った 2 つの失敗の様式です。SDK を組み込むアプリは、組み込まないアプリに何のコストも課さずに、より正確なシグナルを得ます。

- **シグナルを画面内のデータ更新まで広げる。** スコープ外であり、仕組みとしても誤っているので、退けます。その場のデータ更新が投げるのは、せいぜい `UIAccessibility.layoutChangedNotification` です。自動では何も投げないことも多く、画面遷移の observer では確実には見えません。

  [BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait-ja.md) が、そのレースを、本来あるべき要素単位ですでに扱っています。この項目は、確実に観測できる画面遷移のシグナルへ、意図してスコープをとどめます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 作業単位 1 — `BajutsuKit` で完了したビューコントローラの出現を観測する（`viewDidAppear` の
  swizzle、オプトイン）。（提案が当初挙げたアクセシビリティ通知ではなく出現フックで実装しました。下のログと
  *検討した代替案*を参照してください。）
- [x] 作業単位 2 — 既存のコレクタチャネルを通じてシグナルをプロセス外へ報告する。
- [x] 作業単位 3 — `_await_ready` からシグナルを参照し、BE-0218 のツリー差分の梯子をフォールバックにする。
- [x] 作業単位 4 — `_wait_settled` からシグナルを参照し、ツリー差分の挙動をフォールバックにする。
- [ ] 作業単位 5 — シグナルが UIKit と SwiftUI の双方で発火することをシミュレータで確認する（ゲート）。
  作業単位 1〜4 は実装済みで、高速な Linux ゲートでカバーしています（フェイクの遷移シグナル源が
  シミュレータの代わりを務めます）。この作業単位は経験的なオンデバイス限定の確認であり、Mac と
  シミュレータを持つ人による実行が必要です。手順と XCUITest スキャフォールドは
  [`demos/showcase/BE-0310-screen-transition-verification.ja.md`](../../demos/showcase/BE-0310-screen-transition-verification.ja.md)
  にあります。

ログ（古い順）:

- 実装中の仕組みの転換：この項目の前提は、`UIAccessibility.screenChangedNotification` を*観測*することでした。しかし、このシンボルは外向き専用（`UIAccessibility.post(...)` の enum ケースであって、観測できる `NSNotification.Name` ではない）で、購読すべき公開の通知が存在しませんでした。そこで作業単位 1 は `UIViewController.viewDidAppear` を swizzle します。これは*検討した代替案*で比較した手段です。この手段の SwiftUI への到達は、この項目が対象とするコンテナ遷移を覆うことが分かりました。NavigationStack、モーダル、タブは、それぞれ `UIHostingController` を提示し、その出現を観測できます。ワイヤ契約、Python 側のコレクタ、作業単位 2〜4 は変わりません。変わったのは、iOS の観測の仕組みだけです。作業単位 5 のオンデバイス確認が、この出現フックが両ツールキットで想定どおり発火することを裏づけます。

## 参考

- [`bajutsu/platform_lifecycle/readiness.py`](../../bajutsu/platform_lifecycle/readiness.py)：この項目が肯定的なシグナルを与える起動直後の readiness ゲート `_await_ready`。
- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py)：この項目が肯定的なシグナルを
  与える `settled` 待ち `_wait_settled`。
- [`BajutsuKit`](../../BajutsuKit)：observer が加わるオプトインの iOS SDK。`BajutsuNet` と
  `BajutsuExchangeStore` は、この項目が倣うネットワークキャプチャの前例です。
- [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)
 ：この項目が最上段を足す readiness の梯子。
- [BE-0087](../BE-0087-idb-action-settle/BE-0087-idb-action-settle-ja.md)：この項目のシグナルが settle の
  経路で避けるツリー読み取りのコストを定量化した、アクション前 settle の解析。
- [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md)：この項目が
  app-agnostic を保つために倣う、アプリ側 SDK 連携の方針。
- [BE-0299](../BE-0299-settle-value-condition-wait/BE-0299-settle-value-condition-wait-ja.md)：この項目が
  意図して覆わない、補い合う関係にある画面内の値反映レース。

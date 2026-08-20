[English](BE-XXXX-ios-native-tipkit-guard.md) · **日本語**

# BE-XXXX — iOSのTipKit tipによる操作ブロックをネイティブに解消する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-ios-native-tipkit-guard-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Platform support |
| 関連 | [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config-ja.md)、[BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers-ja.md)、[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md)、[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md)、[BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check-ja.md)、[BE-0357](../BE-0357-xcuitest-duplicate-node-hittable-tiebreak/BE-0357-xcuitest-duplicate-node-hittable-tiebreak-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Apple の TipKit（iOS 17 以降）は、UI 要素に紐づけて popover 形式の tip を表示し、機能の使い方を
案内するフレームワークです。tip が出現するタイミングはアプリ自身が完全に制御しているわけではなく、
初回起動時の状態や表示条件、イベントの発生によって決まり、フローの決まったステップに固定されている
わけではありません。この tip のアンカーがシナリオのタップ対象と重なると、tip 自身のビューがその
前面に出ます。本提案は、iOS の XCUITest バックエンドに、ブロックしている TipKit の tip を決定論的
かつネイティブに、しかも opt-in で検知して閉じるガードを追加します。スクリーンショットとモデル呼び
出しのどちらも使いません。[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md)
と[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md)が
SpringBoard のシステムアラートに対してすでに確立した形を、そのまま踏襲します。この提案の貢献は、
同一プロセス内にあってフレームワークが構造を所有し、アプリ側ではカスタマイズできない、この tip と
いう具体的なケースを認識する点にあります。TipKit を使うすべてのアプリに、同じ回避策を書かせること
なく解消します。

## 動機

[BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers-ja.md)は、
シナリオが `interrupts` を宣言できるようにしています。待機処理がすでに取得済みのツリーに対して
機会があるたびにチェックする `condition` と、それに対応する回復用の `steps` の組で、オンボーディング
画面やチュートリアルのオーバーレイ、アクセシビリティツリーから見える権限プロンプトなど、シナリオの
中で不定のタイミングに出現しうる画面のために用意されたものです。TipKit の tip もまさにこの
「タイミングが不定」という形を持つため、一見 `interrupts` が適した道具に見えます。

しかし、そうではありません。理由は具体的です。`interrupts` の `condition` は、アプリ自身のアクセシ
ビリティツリーに対して著者が書くアサーションであり、著者が名指しできるセレクタを前提にしています。
カスタムのオンボーディング画面はアプリチーム自身のビューなので、チームが選んだ任意の識別子を持たせ
られます。これはまさに `interrupts` が想定していたケースです。一方 TipKit の tip はアプリのビューで
はありません。tip のタイトルや本文は `Tip` のインスタンスごとにアプリ内でも異なり、TipKit が標準で
追加する閉じるボタンには、アプリ側の設定したアクセシビリティ識別子が付いていません。アプリが提供
しているのは tip の内容だけで、それを包むコンテナや閉じる操作そのものではないのです。そのため著者
は、`condition` に書けるような安定した識別子をそもそも持ちません。TipKit の内部ビュー構造をアプリ
チームがある程度解析して選べない限り、「表示されている TipKit の tip を閉じる」とい
う条件を `interrupts` で表現できないのです。しかもこの解析は、TipKit を採用するアプリの数だけやり直
しになります。これは、[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md)
が通知権限のプロンプトで直面したのと同じ種類のギャップです。アプリ側の設定では届かない画面です。

[BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check-ja.md)は、
ブロックされたタップが誤った要素にそのまま当たってしまうことをすでに防いでいます。tip が対象を覆っ
ていると `isHittable` は false を返すため、ドライバは tip への誤タップではなく `ElementNotTappable`
を送出します。想定外の障害物であればこれは正しい失敗ですが、障害物が既知で回復可能な TipKit の tip
である場合、そのまま失敗させるのはランを無駄にします。`ElementNotTappable` 自身の回復手段である回
数を区切ったスクロールは、ここでは効きません。画面上の要素に紐づいた popover は、スクロールしても
閉じないからです。

ここで踏襲すべきパターンは、[BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers-ja.md)
ではなく、[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md)と
[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md)のものです。
TipKit の tip は OS のフレームワークが所有しているため、採用するどのアプリでも構造は同じであり、こ
れは BE-0315 が XCUITest ドライバへ SpringBoard アラート用の組み込みのネイティブガードを持たせ、
アプリ側の設定を不要にできた性質と同じです。Web 側にはすでに縮小版がありました。Playwright のネイ
ティブ API である `page.on('dialog')`(`bajutsu/drivers/playwright.py`)は、ブラウザ自身が所有する
JavaScript の `alert`/`confirm`/`prompt` を同じ理屈で自動的に閉じます。ページではなくブラウザエンジ
ンがそのオーバーレイを所有しているからです。TipKit の tip は、iOS におけるこの in-process 版の対応
物であり、アプリではなくフレームワークが所有している以上、シナリオの設定ではなくドライバのケイパビ
リティとして扱うのが適しています。

## 詳細設計

作業は、実現可能性の検証、ネイティブな検知と閉じる操作、tap 時点の回復フック、待機ループのゲート、
opt-in の設定、実機での検証に分かれます。

1. **実現可能性の検証：ロケールとアプリのどちらにも依存しない TipKit tip 検知シグナルを探す。** 実装を
   始める前に、実際の Simulator 上で、TipKit の popover コンテナと標準の閉じるボタンが、XCUITest
   のアクセシビリティツリーから安定して問い合わせられる構造的シグナルを持つかどうかを確認します。
   tip 自体のタイトルや本文、そしてデバイスのロケールに依存しないことが条件です(SpringBoard のア
   ラートのボタン文字列について[BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md)
   が指摘したのと同じロケール決定性の懸念です)。実際に表示中の tip のアクセシビリティツリーを覗いた
   初期調査では、すでに有望な手がかりが見つかっています。tip が表示されている間、ツリーには
   `PopoverDismissRegion` という識別子の領域が定義される模様で、この名前は
   `UIPopoverPresentationController` に支えられた popover が通常備える「外側をタップして閉じる」ため
   の scrim と符合します。この識別子が安定していてロケールにも依存しないと確かめられれば、この領域は
   検証すべき 2 つの役割を同時に満たせそうです。存在すること自体が検知シグナルになり、タップすること
   は、ユーザーが tip の外側をタップした場合と同じ閉じる操作になります。そのため作業単位 3 は、別途
   閉じるボタンのセレクタを探す代わりに、この 1 つの要素だけを解決すれば済みます。
   ただしこれは実機で確かめるべき手がかりであり、まだ確認済みの仕組みではありません。あわせて検証す
   べき他の候補は、TipKit が popover の周囲に組み立てるビュー階層(通常のアプリコンテンツとは異なる
   コンテナの形)と、閉じるボタン自体のアクセシビリティ特性(多くのアプリが tip のアンカー位置には
   置かないボタン)です。実機での検証で信頼できるシグナルが見つからない場合は、
   [BE-0357](../BE-0357-xcuitest-duplicate-node-hittable-tiebreak/BE-0357-xcuitest-duplicate-node-hittable-tiebreak-ja.md)
   の検証が行き着いたのと同じ結論として、この作業単位で提案を止め、うまくいくかどうかが偶然に左右
   されるヒューリスティックを出荷するのではなく Deferred とします。

2. **`Driver` インターフェース越しに公開する、ネイティブな検知クエリ。** TipKit の tip が画面をブ
   ロックしているかどうか、そしてブロックしている場合はその閉じる対象（作業単位 1 で確認され次第、
   `PopoverDismissRegion` か閉じるボタンのいずれか）を決定論的に解決できるだけの情報を返す、バックエ
   ンドに依存しないメソッドを追加します。これは新しいトップレベルのケイパビリティトークン
   `Capability.HANDLE_TIPKIT_TIP` の背後に置き、iOS の XCUITest バックエンドだけが公開します。TipKit
   は iOS 専用のフレームワークなので、Android と web バックエンドはこのトークンを公開しません。これ
   は `bajutsu/drivers/base.py` がすでに `HANDLE_SYSTEM_ALERT` について説明しているのと同じ理由です。
   このシグナルは事実を報告するだけで合否を判定しないため、prime directive 1 から外れません。

3. **決定論的な閉じる操作。** 現在表示されている tip を、解決済みの閉じる対象（作業単位 1 が
   `PopoverDismissRegion` を確認できればその scrim、そうでなければ閉じるボタン）をタップして閉じま
   す。対象はちょうど 1 つに一意に解決し、一致がゼロまたは複数の場合は推測せずに失敗させます
   (`resolve_unique` と `AmbiguousSelector` の契約、prime directive 2)。これは BE-0316 の
   `handle_system_alert` が
   SpringBoard のボタンにすでに適用している規律と同じです。

4. **tap 時点の回復パスを拡張する（待機ループのゲートと並行して）。**
   [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check-ja.md)の
   `_tap_with_recovery`(`bajutsu/orchestrator/actions/handlers/gestures.py`)は、tap の
   `ElementNotTappable` をすでに捕捉し、有界なスクロールのあとに再試行します。しかし tip は
   スクロールで取り除けないため、待機ループのゲート（作業単位 5）だけを組み込んだ場合、直前に待機の
   ない tap の瞬間にすでに対象を覆っている tip があると、そのステップは失敗したままになります。
   `_tap_with_recovery` にネイティブな閉じる操作を追加し、`HANDLE_TIPKIT_TIP` と `tipKitHandling` で
   ゲートします。`ElementNotTappable` が起きたら、スクロール回復の前に一度だけ検知クエリを確認し、
   tip が見つかればそれを閉じて即座に tap を再試行します。それでも対象がタップできない場合にだけ、
   既存の有界なスクロール回復にフォールバックします。スクロールより先に閉じる操作を試すのは、この
   順序が障害物の性質に合っているからです。対象を覆う popover はスクロール位置にかかわらずそこに
   居座るため、先にスクロールを試すと `_TAP_RECOVERY_MAX_SCROLLS` の有界な予算を、実際の障害物には
   まったく対処しないまま消費してしまいます。

5. **ケイパビリティと opt-in の設定の両方でゲートされる、待機ループのゲート。** `bajutsu/orchestrator/waits.py`
   の `_AlertGuardGate` と同じ形で、新しいゲートがネイティブな検知クエリを独自の wall-clock 間隔で
   ポーリングします。この間隔は待機自体のポーリング周期とは切り離されています。理由は BE-0315 が説
   明しているのと同じで、単一のメインスレッドで動くランナーの負荷を抑えるためです。ポーリングで tip
   を検知した瞬間に閉じます。ネイティブな検知クエリは事実を報告するのであって、ツリーの崩れから推
   測する代理シグナルではないため、デバウンスやクールダウンは不要です。シナリオがまだ待機している
   間に先回りして閉じておけば、たいていのランは作業単位 4 の tap 時点の回復パスに到達しないで済みます。
   作業単位 4 は、直前に待機のない tap の瞬間にすでに tip が出ているケースの、あくまで最後の砦です。
   このゲートは、バックエンドが `HANDLE_TIPKIT_TIP` を公開しており、かつ後述の opt-in の設定が有効
   な場合にだけ動作し、それ以外では今日の挙動を変えません。

6. **既定でオフの opt-in 設定。** 新しい `tipKitHandling` 設定(真偽値)を、`systemAlertHandling` に
   すでに確立されているフラグ、シナリオ、ターゲット、既定値という優先順位([BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config-ja.md))
   と同じ形で解決し、`--tipkit-handling`/`--no-tipkit-handling` という CLI フラグを対応させます。
   `systemAlertHandling` は既定でオンですが、この設定は既定で**オフ**にします。TipKit の tip 自体が
   シナリオの検証対象になる場合があるからです(tip 自体の文言や閉じる挙動を確認するオンボーディング
   フローのアサーション)。ガードを既定でオンにすると、検証対象であるはずの tip を自動で閉じてしまい、
   そのシナリオを静かに壊します。ガードを求めていないシナリオは、今日と変わらず tip をそのまま見続
   けます。

7. **実機で検証し、showcase に組み込む。** showcase の SwiftUI デモアプリに、ボタンへ紐づけた
   `.popoverTip()` の fixture を追加し、`tipKitHandling` を有効にした 2 つのシナリオを追加します。
   1 つは待機中に出現した tip をゲートが閉じることを確認し、もう 1 つは直前に待機を挟まず、すでに
   tip が覆っている対象をタップして作業単位 4 の回復パスを直接確認します。実際の TipKit の tip に
   対するネイティブな閉じる操作は Simulator 外のゲートでは証明できないため、この作業単位は起動済み
   の Simulator に対して実行する必要があります。バックエンドに依存しない配線(作業単位 3 から 5)は、
   ドライバのケイパ
   ビリティをスタブしたオフデバイスのテストでカバーします。

## 検討した代替案

- **ドライバのケイパビリティではなく、BE-0314 の `interrupts` で表現する。** 却下します。TipKit の
  閉じるボタンにはアプリの設定した識別子が付いていないため、著者が書く `condition` やセレクタでは
  アプリに依存しない形で一致させられません。TipKit を採用するアプリはすべて、本提案がドライバ側で
  一度だけ解決する内部構造を、それぞれ自分で解析し直すことになります。
- **`systemAlertHandling` に倣い、ガードを既定でオンにする。** 却下します。OS の権限プロンプトがア
  サーションの対象そのものになることはありませんが、TipKit の tip はオンボーディングのシナリオで
  アサーションの対象そのものになる場合があります。既定でオフにすることで、ガードを求めていないすべ
  てのシナリオの挙動を今日のまま保てます。
- **アンカー要素からの固定オフセットで閉じる。** 却下します。理由は BE-0315 が SpringBoard のボタン
  について同じ選択肢を却下したのと同じで、固定オフセットはデバイスサイズや Dynamic Type が変わると
  崩れます。実現可能性の検証でシグナルが見つかれば、解決済みの閉じるボタン要素の方が安定したプリミ
  ティブになります。
- **具体的な 2 つ目の対象が現れる前に、クロスプラットフォームな「フレームワーク所有のオーバーレイ」
  というケイパビリティの抽象化を今設計する。** 却下します。TipKit が持つ、採用するどのアプリでも一
  貫したアクセシビリティツリーの形になるという性質と同等のものは、OS レベルでも Jetpack の標準とし
  ても Android 側には存在しません。`TooltipCompat` や Compose Material3 の `PlainTooltip`/`RichTooltip`
  はアプリごとにインスタンス化するウィジェットであり、他の独自実装のオンボーディング画面と同様、す
  でに BE-0314 の `interrupts` で届く範囲にあります。`HANDLE_SYSTEM_ALERT` の前例からも、ケイパビリ
  ティトークンのモデル自体は再設計なしにすでに一般化できていることがわかるため、プラットフォームが
  所有する別のオーバーレイが実際に現れたときに、そのための新しいトークンを追加すれば足ります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 作業単位 1 — ロケールとアプリのどちらにも依存しない TipKit tip 検知シグナルの実現可能性の検証。
      `PopoverDismissRegion` という手がかりから着手する。
- [ ] 作業単位 2 — 新しい `Capability.HANDLE_TIPKIT_TIP` トークンの背後にあるネイティブな検知クエリ。
- [ ] 作業単位 3 — 一意に解決したセレクタによる決定論的な閉じる操作。
- [ ] 作業単位 4 — `_tap_with_recovery` における tap 時点の回復フック。有界なスクロール回復より先に
      試す。
- [ ] 作業単位 5 — 独自の間隔でポーリングする待機ループのゲート。
- [ ] 作業単位 6 — 既定でオフの `tipKitHandling` 設定(`--tipkit-handling`/`--no-tipkit-handling`、
      BE-0177 の優先順位に従う)。
- [ ] 作業単位 7 — 実機での検証と、両方の回復パスに対する showcase の fixture・シナリオ。

## 参考

- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — `Driver` インターフェースと、本提案の
  `HANDLE_TIPKIT_TIP` が形を踏襲する `Capability` トークン(`HANDLE_SYSTEM_ALERT`、`PICKER_WHEEL`)。
- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) — `_AlertGuardGate`。本提案
  の TipKit 用ゲートが再利用する待機ループのゲートの形。
- [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py)
  — `_tap_with_recovery` と `_TAP_RECOVERY_MAX_SCROLLS`。BE-0349 の tap 時点の回復パスで、本提案の
  作業単位 4 が有界なスクロールより先にネイティブな閉じる操作を試すよう拡張する対象。
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) — 検知メソッドと閉じるメソッド
  が追加される、Python の XCUITest ドライバ。
- [`bajutsu/drivers/playwright.py`](../../bajutsu/drivers/playwright.py) — `_on_dialog`。フレームワーク
  が所有するオーバーレイを、シナリオの設定ではなくネイティブに処理する web バックエンド側の既存の
  前例。
- [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config-ja.md) —
  `tipKitHandling` が従う、フラグ、シナリオ、ターゲット、既定値という優先順位。
- [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) — 通知権限
  のギャップが前例となる、アプリ側の設定では届かない画面。
- [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers-ja.md) —
  `interrupts` の仕組み。本提案の動機で、フレームワークが所有するオーバーレイには不向きで、独自実装
  のオンボーディング画面には引き続き適した道具であると説明しています。
- [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md)、
  [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md) — 本提案が
  in-process のオーバーレイ向けに踏襲する、ネイティブで決定論的な SpringBoard アラートガード。
- [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md)
  — 実現可能性の検証が考慮すべき、ロケール決定性の懸念。
- [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check-ja.md) —
  ブロックしている tip がすでに引き起こす `isHittable` の判定と `ElementNotTappable` エラー。ただし
  回復する手段はまだありません。
- [BE-0357](../BE-0357-xcuitest-duplicate-node-hittable-tiebreak/BE-0357-xcuitest-duplicate-node-hittable-tiebreak-ja.md)
  — 一見有望に見えるシグナルを実機の検証が反証し、ヒューリスティックを出荷せずに Deferred とした前例。

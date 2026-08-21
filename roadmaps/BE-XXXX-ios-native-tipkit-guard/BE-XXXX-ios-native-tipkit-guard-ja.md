[English](BE-XXXX-ios-native-tipkit-guard.md) · **日本語**

# BE-XXXX — iOSのTipKit tipによる操作ブロックをネイティブに解消する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-ios-native-tipkit-guard-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | _PR を作成した時点で記入_ |
| トピック | Platform support |
| 関連 | [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config-ja.md)、[BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers-ja.md)、[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md)、[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md)、[BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check-ja.md)、[BE-0357](../BE-0357-xcuitest-duplicate-node-hittable-tiebreak/BE-0357-xcuitest-duplicate-node-hittable-tiebreak-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Apple の TipKit（iOS 17 以降）は、UI 要素に紐づけて popover 形式の tip を表示し、機能の使い方を
案内するフレームワークです。tip が出現するタイミングはアプリ自身が完全に制御しているわけではなく、
初回起動時の状態や表示条件、イベントの発生によって決まり、フローの決まったステップに固定されている
わけではありません。この tip のアンカーがシナリオのタップ対象と重なると、tip 自身のビューがその
前面に出ます。しかも実機で確かめたところ、TipKit の表示は覆われたコンテンツを単に隠すのではなく、
アクセシビリティツリーから完全に取り除きます。そのためブロックされたタップは `ElementNotTappable`
だけでなく `ElementNotFound` としても失敗しえます。本提案は、iOS の XCUITest バックエンドに、ブロッ
クしている TipKit の tip を決定論的かつ opt-in で検知して閉じるガードを追加します。スクリーンショッ
トとモデル呼び出しのどちらも使いません。しかも tip はすでにアプリ自身のアクセシビリティツリーの中に
現れるため、Swift ランナー側の変更も要りません。この提案の貢献は、同一プ
ロセス内にあってフレームワークが構造を所有し、アプリ側ではカスタマイズできない、この tip という具
体的なケースを認識する点にあります。TipKit を使うすべてのアプリに、同じ回避策を書かせることなく解
消します。

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

踏襲すべきパターンは[BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers-ja.md)
ではなく、[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md)/
[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md)のものです。
TipKit の tip は OS のフレームワークが所有しているため、採用するどのアプリでも構造は同じです。これは
BE-0315 が、アプリごとの設定を求める代わりに、ケイパビリティトークンの背後に組み込みの決定論的なガー
ドを XCUITest バックエンドへ持たせられた性質と同じです。Web 側にはすでに縮小版の同種のケースがありま
した。Playwright のネイティブ API である `page.on('dialog')`(`bajutsu/drivers/playwright.py`)は、
ブラウザ自身が所有する JavaScript の `alert`/`confirm`/`prompt` を自動的に閉じます。ページではなくブ
ラウザエンジンがそのオーバーレイを所有しているからです。そしてこの処理は*ドライバ*の中にあります。
あるバックエンド固有のオーバーレイに関する知識は、そこに置くべきものです。本提案もこの層の切り分けを
保ちます。TipKit 固有の識別子は XCUITest ドライバの中に留め、orchestrator からはバックエンドに依存し
ない「ブロックしている tip を閉じたか」という呼び出しだけが見えるようにします。

実機での検証(後述の作業単位 1)が変えるのは、そのドライバのメソッドに必要な仕掛けの量です。BE-0315
の SpringBoard アラートは、もう 1 つのプロセス外の `XCUIApplication` の中に存在するため、そこへ到達
するにはケイパビリティトークンに加えて新しい Swift ランナーのルートが必要でした。TipKit の tip はプ
ロセス外ではありません。アプリ自身のビュー階層の中に描画されており、待機のポーリングやタップの解決が
すでに毎回取得している `elements` のスナップショットに、この tip はすでに現れます。そのためドライバ
は、すでに持っているクエリとタップのプリミティブの上で、このガード全体を Python だけで実装できます。
**Swift と BajutsuKit 側の変更は一切不要**であり、これがこの項目を、ランナーのプロトコル変更ではな
く小さな項目に留めている理由です。

## 詳細設計

作業は、実現可能性の検証(この提案を書き上げる前に実機で確認済み)、ケイパビリティトークンの背後に置
くドライバ側の閉じるメソッド、失敗後の再試行、待機中のゲート、opt-in の設定、実機での検証に分かれま
す。

1. **実現可能性の検証 — 確認済み。** 実際の Simulator を使ったランで、表示中の TipKit tip のアクセ
   シビリティツリー(showcase アプリの Stable タブでのステップの `elements.json`)を取得し、ロケール
   に依存しない安定した識別子を 3 つ見つけました。`PopoverDismissRegion`(ラベル「dismiss popup」の、
   画面全体を覆う「外側をタップして閉じる」scrim)、`TipView`(tip 自体のコンテナ)、そして
   `xmark.circle.fill`(閉じるボタン、`traits: [button]`、ラベル「Close」)です。このうち
   `PopoverDismissRegion` のフレームは画面全体と一致しており、`UIPopoverPresentationController`
   という手がかりを裏づけています。閉じる
   ボタンの識別子は SF Symbol の名前であってローカライズされた文字列ではないため、SpringBoard のアラー
   トのボタン文字列について[BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md)
   が指摘したのと同じロケール非依存性を満たしていたはずですが、それでも閉じる対象としては採用しませ
   ん。SF Symbol の名前は、同じアプリ内の無関係なアプリ側ボタンが偶然同じ識別子を使う可能性があるた
   め、これに一致させると `AmbiguousSelector` を招くリスクがあります。一方 `PopoverDismissRegion` は
   TipKit 内部の名前であり、アプリ側のビューが偶然衝突することはありません。そのため本設計では
   `PopoverDismissRegion` だけを検知シグナルにも閉じる対象にも使います。同じランでは、もともとの設計
   が見落としていた事実も判明しました。tip が表示されている間、その裏にある通常のツールバーボタン
   (`stable.refresh`)は、単に `isHittable` が false になるだけではなく、ツリーから完全に消えてしま
   います(10 秒間・188 回のポーリングで一度も見つかりませんでした)。TipKit の表示はモーダル
   と同様に、覆われたコンテンツをアクセシビリティ上非表示にするのであって、単に前面にビューを重ねる
   のではないからです。したがって、そうした対象へのタップは `ElementNotTappable` ではなく
   `ElementNotFound` として失敗します。これは後述の作業単位 3 で両方に対処します。最後に、`tap: { id:
   "PopoverDismissRegion" }` という、新しいコードを一切追加していない既存のステップだけで tip を閉じ
   られ、直後に `stable.refresh` が再び問い合わせ可能になることも確認できました。これが、この設計の
   残りの部分が前提とする簡略化の実証的な根拠、すなわち**Swift ランナーのルートが不要**であることの
   根拠です。`PopoverDismissRegion` は、待機のポーリングやタップの解決がすでに毎回取得しているツリー
   の中のごく普通のノードであり、ドライバが既存のタップでそのまま届くからです。この検証が省けるのは
   BE-0315 が必要としたランナー側の作業であって、ドライバ層そのものではありません。作業単位 2 はメソ
   ッドとケイパビリティトークンをどちらも残しており、識別子をそこに置く理由は後述の検討した代替案で
   説明します。

2. **ケイパビリティトークンの背後に置く、ドライバ側の閉じるメソッド。** バックエンドに依存しない
   `Driver` のメソッドを 1 つ追加します。`dismiss_blocking_tip() -> bool` で、tip を見つけて閉じたか
   どうかを返します。これを新しいトップレベルのケイパビリティトークン `Capability.HANDLE_TIPKIT_TIP`
   の背後に置き、XCUITest バックエンドだけが公開します。形も理由も、`bajutsu/drivers/base.py` がすで
   に `HANDLE_SYSTEM_ALERT` と `PICKER_WHEEL` について説明しているものと同じです。
   `PopoverDismissRegion` という識別子は `bajutsu/drivers/xcuitest.py` の中**だけ**に置き、orchestrator
   には決して置きません。TipKit はあるバックエンド固有のオーバーレイなので、その知識は、すでにバック
   エンド固有の事情を持っている層に属します。これは Playwright の `_on_dialog` がブラウザのダイアログ
   に対して果たしている役割とまったく同じです。orchestrator から見えるのは識別子ではなく真偽値なので、
   決定論的なコアはバックエンドに依存しないままです(prime directive 3)。このメソッドは対象をちょうど
   1 つに解決し、一致がなければ `False` を返し、複数ある場合は推測せずに失敗させます(`resolve_unique`
   と `AmbiguousSelector` の契約、prime directive 2)。また事実を報告するだけで合否を判定しないため、
   prime directive 1 からも外れません。この作業単位が小さく収まるのは作業単位 1 の所見のおかげです。
   Swift ランナーのルートは不要で、ドライバがすでに持っているクエリとタップのプリミティブだけで済み
   ます。

3. **ステップループに置く、失敗後の閉じる操作と再試行。アラートガードとは順番に評価する。** 作業単位 1 で確認したとおり、tip に覆われ
   た対象は `isHittable` を失うだけでなく、ツリーから消えてしまいます。そのため、ブロックされたステッ
   プは `ElementNotTappable` と同じくらい `ElementNotFound` としても失敗します。この事実から、
   [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check-ja.md)の
   `_tap_with_recovery`(`bajutsu/orchestrator/actions/handlers/gestures.py`)はフックの置き場所とし
   て外れます。捕捉しているのは後者だけであり、しかもステップハンドラのシグネチャにはシナリオ単位の
   実行設定を渡す口がないため、そこでゲートするには `ActionHandler` と全ハンドラ、全呼び出し元を変更
   することになります。代わりに回復処理はステップループ(`bajutsu/orchestrator/loop.py`)へ置き、アラ
   ートガードが持つ既存の「閉じてから、もう一度だけ」のステップ末尾の分岐の隣に並べます。この層は例
   外の種類にかかわらずすべてのステップの失敗を見ており、シナリオも持っているため、両方のガードが設
   定を所有する唯一の場所に収まります。ステップが失敗したら作業単位 2 の `dismiss_blocking_tip()` を
   呼び出し、tip を実際に閉じたと報告した場合に**限って**、そのステップを一度だけ再試行します。tip が
   存在しない失敗はそのまま失敗させます。確認された原因のない単なる「見つからない」に対して再試行し
   てしまうと、本来のセレクタの誤りを静かに覆い隠すことになるからです。これは `resolve_unique` がすで
   に従っている、prime directive 2 の失敗を隠さないという規律と同じです。再試行を決めるのが閉じる操作
   の成否である以上、成功するランがこのためにクエリを払うことは一度もありません。ステップ末尾の 2 つ
   のガードは、1 本の `elif` の連鎖ではなく順番に評価します。tip とシステムアラートは同時に出ているこ
   とがあり、先に閉じた tip が失敗を消費して、アラートガードが本来担当するはずのアラートを取り残して
   はならないからです。それぞれが発火するのは、1 ステップにつき最大 1 回のままです。

4. **BE-0314 がすでに設置しているポーリングのフックに乗る、待機中のゲート。** tip の後ろでブロックさ
   れた待機が、作業単位 3 に救われるまでタイムアウトを使い切ってしまうのは避けたいので、待機のポーリ
   ング中にも閉じる操作を走らせます。`_AlertGuardGate` の隣に 2 つ目のゲートオブジェクトを置くのでは
   なく、BE-0314 の `interrupts` がすべての待機に通している `on_interrupt_poll` コールバックに合成し
   ます。このフックはポーリングごとに取得済みのツリーを受け取り、それをドライバへ渡すため、ほぼ毎回の
   答えである「tip はない」の判定にクエリを払わず、ガードを有効にした待機も通常どおりの周期でポーリン
   グします。実際に tip があるときだけ、閉じる操作が使うハンドルを得るために新しいスナップショットを
   取ります。そのためこのゲートに独自の wall-clock 間隔は要りません。作業単位 1 で確認したとおり、BE-0315 のプロセス外の
   SpringBoard クエリのように速度を抑える必要のある処理が、そもそも存在しないからです。このフックの契
   約から 1 つ注意点が生じます。閉じる操作が成功した場合は `False` を報告しなければなりません。この
   コールバックの `True` は「回復が失敗したので待機を終える」という意味であり、報告してしまうと、閉じ
   る操作によって成功できるようになったばかりの待機を中断させてしまいます。このゲートは、バックエンド
   が `HANDLE_TIPKIT_TIP` を公開しており、かつ後述の opt-in の設定が有効な場合にだけ動作し、シナリオ
   が `interrupts` を宣言しているかどうかにかかわらず透過的に合成されます。

5. **既定でオフの opt-in 設定。** 新しい `tipKitHandling` 設定(真偽値)を、`systemAlertHandling` に
   すでに確立されているフラグ、シナリオ、ターゲット、既定値という優先順位([BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config-ja.md))
   と同じ形で解決し、`--tipkit-handling`/`--no-tipkit-handling` という CLI フラグを対応させます。
   `systemAlertHandling` は既定でオンですが、この設定は既定で**オフ**にします。TipKit の tip 自体が
   シナリオの検証対象になる場合があるからです(tip 自体の文言や閉じる挙動を確認するオンボーディング
   フローのアサーション)。ガードを既定でオンにすると、検証対象であるはずの tip を自動で閉じてしまい、
   そのシナリオを静かに壊します。ガードを求めていないシナリオは、今日と変わらず tip をそのまま見続
   けます。

6. **実機で検証し、showcase に組み込む。** showcase の SwiftUI デモアプリの Stable タブにある
   「Refresh」ボタンへ紐づけた `.popoverTip()` の fixture(作業単位 1 の実現可能性の検証で実際に使っ
   たもの)と、`tipKitHandling` を有効にした 2 つのシナリオです。1 つは待機中に出現した tip をゲート
   が閉じることを確認し、もう 1 つは直前に待機を挟まず、すでに tip が覆っている対象をタップして作業
   単位 3 の回復パスを直接確認します。実際の TipKit の tip に対する閉じる操作は Simulator 外のゲート
   では証明できないため、この作業単位は起動済みの Simulator に対して実行する必要があります。バックエ
   ンドに依存しない配線(作業単位 3・4)は、スタブしたドライバに対するオフデバイスのテストでカバーし
   ます。

## 検討した代替案

- **組み込みのガードではなく、BE-0314 の `interrupts` で表現する。** 却下します。TipKit の閉じるボ
  タンにはアプリの設定した識別子が付いていないため、著者が書く `condition` やセレクタではアプリに
  依存しない形で一致させられません。TipKit を採用するアプリはすべて、本提案がドライバ側で一度だけ解
  決する内部構造を、それぞれ自分で解析し直すことになります。
- **ドライバのメソッドとケイパビリティトークンを設けず、orchestrator で `PopoverDismissRegion` を直
  接マッチさせる。** 実機での検証(作業単位 1)から、これは動作するとわかっています。tip は
  orchestrator がポーリングしているツリーにすでに含まれているため、`waits.py` と `gestures.py` で識別
  子を確認するだけなら、新しいドライバの表面はまったく必要ありません。それでも却下します。あるバック
  エンド固有のオーバーレイの知識を、バックエンドに依存しないコアへ持ち込むことになり、これは prime
  directive 3 が引いている境界そのものだからです。しかも本提案が拠り所とする前例は、その知識をドライ
  バに置いています(Playwright の `_on_dialog` は `bajutsu/drivers/playwright.py` にあり、orchestrator
  にはありません)。真偽値を返すメソッド 1 つは、iOS 専用の識別子を全バックエンド共通のファイルから遠
  ざけるための、安い代償です。この検証の本当の成果はもっと限定的で、それはきちんと得られています。
  BE-0315 とは違い、*Swift ランナー*のルートは不要だという点です。
- **`PopoverDismissRegion` ではなく、閉じるボタンの識別子(`xmark.circle.fill`)で閉じる。** 却下しま
  す。実機で確認したところ両方とも閉じる対象として同様に機能しましたが、SF Symbol の名前は同じアプ
  リ内の無関係なアプリ側ボタンが偶然使ってしまう可能性のある識別子であり、`AmbiguousSelector` を招
  くリスクがあります。`PopoverDismissRegion` は TipKit 内部の名前であり、アプリ側のビューと衝突する
  ことはありません。
- **`systemAlertHandling` に倣い、ガードを既定でオンにする。** 却下します。OS の権限プロンプトがア
  サーションの対象そのものになることはありませんが、TipKit の tip はオンボーディングのシナリオで
  アサーションの対象そのものになる場合があります。既定でオフにすることで、ガードを求めていないすべ
  てのシナリオの挙動を今日のまま保てます。
- **アンカー要素からの固定オフセットで閉じる。** 却下します。理由は BE-0315 が SpringBoard のボタン
  について同じ選択肢を却下したのと同じで、固定オフセットはデバイスサイズや Dynamic Type が変わると
  崩れます。安定したプリミティブは名前で解決した要素であり、作業単位 1 でそれが実在することを確認し
  ています。
- **具体的な 2 つ目の対象が現れる前に、クロスプラットフォームな「フレームワーク所有のオーバーレイ」
  というケイパビリティの抽象化を今設計する。** 却下します。TipKit が持つ、採用するどのアプリでも一
  貫したアクセシビリティツリーの形になるという性質と同等のものは、OS レベルでも Jetpack の標準とし
  ても Android 側には存在しません。`TooltipCompat` や Compose Material3 の `PlainTooltip`/`RichTooltip`
  はアプリごとにインスタンス化するウィジェットであり、他の独自実装のオンボーディング画面と同様、す
  でに BE-0314 の `interrupts` で届く範囲にあります。`HANDLE_SYSTEM_ALERT` の前例からも、ケイパビリ
  ティトークンのモデル自体は再設計なしにすでに一般化できていることがわかるため、プラットフォームが
  所有する別のオーバーレイが実際に必要になれば、そのための新しいトークンを追加すれば足ります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 作業単位 1 — 実現可能性の検証、確認済み。`PopoverDismissRegion` が唯一のシグナルであり閉じる
      対象であること、tip に覆われた対象が `ElementNotTappable` だけでなく `ElementNotFound` として
      も失敗しうることを実機で確認した。
- [x] 作業単位 2 — `Capability.HANDLE_TIPKIT_TIP` の背後に置く `Driver.dismiss_blocking_tip()`。
      TipKit の識別子は `bajutsu/drivers/xcuitest.py` の中だけに閉じ込める。
- [x] 作業単位 3 — ステップループに置く、失敗後の閉じる操作と再試行。アラートガードの分岐の隣に並べる。
- [x] 作業単位 4 — BE-0314 の `on_interrupt_poll` フックに合成した、待機中の閉じる操作。
- [x] 作業単位 5 — 既定でオフの `tipKitHandling` 設定(`--tipkit-handling`/`--no-tipkit-handling`、
      BE-0177 の優先順位に従う)。
- [x] 作業単位 6 — 実機での検証と、両方の回復パスに対する showcase の fixture・シナリオ。

`demos/showcase/scenarios/tipkit.yaml` を起動済みの Simulator で検証しました。ガードを有効にすると
3 つのシナリオはすべて成功し、`--no-tipkit-handling` で強制的に無効にすると、ガードに依存する 2 つだ
けが失敗します。tap のシナリオは `一致なし: {'id': 'stable.refresh'}`(対象がツリーから消えている、作
業単位 1 の `ElementNotFound` の所見)で、wait のシナリオは 15 秒のタイムアウトを使い切って失敗します。
したがってどちらのガードの経路も、たまたま成功しているのではなく実際に効いています。

## 参考

- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — `Driver` インターフェースと、本提案の
  `HANDLE_TIPKIT_TIP` が形を踏襲する `Capability` トークン(`HANDLE_SYSTEM_ALERT`、`PICKER_WHEEL`)。
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) — `dismiss_blocking_tip()` が追
  加される XCUITest ドライバであり、`PopoverDismissRegion` という識別子が現れる唯一のファイル。
- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) — `_AlertGuardGate`。作業
  単位 4 が当初手本にした待機中のガードの形と、閉じる操作の成功を `False` として報告させる
  `on_interrupt_poll` の契約。
- [`bajutsu/orchestrator/loop.py`](../../bajutsu/orchestrator/loop.py) — 両方のガード(作業単位 3・4)
  が入るステップループ。アラートガードが持つ既存のステップ末尾の閉じる操作と再試行の隣。
- [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py)
  — `_tap_with_recovery`。BE-0349 の tap 時点の回復であり、作業単位 3 がフックの置き場所として外れる
  と説明している対象。捕捉するのは `ElementNotTappable` だけで、tip に覆われた対象はツリーから消える。
- [`bajutsu/drivers/playwright.py`](../../bajutsu/drivers/playwright.py) — `_on_dialog`。フレームワーク
  が所有するオーバーレイを、シナリオの設定や orchestrator ではなくドライバで処理する web バックエンド
  側の既存の前例。
- [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config-ja.md) —
  `tipKitHandling` が従う、フラグ、シナリオ、ターゲット、既定値という優先順位。
- [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) — 通知権限
  のギャップが前例となる、アプリ側の設定では届かない画面。
- [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers-ja.md) —
  `interrupts` の仕組み。本提案の動機で、フレームワークが所有するオーバーレイには不向きで、独自実装
  のオンボーディング画面には引き続き適した道具であると説明しています。
- [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling-ja.md)、
  [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step-ja.md) — 本提案が
  踏襲する、ネイティブで決定論的な SpringBoard アラートガード。ただしその Swift ランナーのルートは、
  ツリー内のオーバーレイには不要だとわかりました。
- [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md)
  — 実現可能性の検証(作業単位 1)で、見つかった 3 つの識別子のいずれにも当てはまらないと確認された、
  ロケール決定性の懸念。
- [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check-ja.md) —
  ブロックしている tip がすでに引き起こす `isHittable` の判定と `ElementNotTappable` エラー。ただし
  回復する手段はまだありません。
- [BE-0357](../BE-0357-xcuitest-duplicate-node-hittable-tiebreak/BE-0357-xcuitest-duplicate-node-hittable-tiebreak-ja.md)
  — 一見有望に見えるシグナルが実際に信頼できるかどうかを、実機の検証で見極める前例。

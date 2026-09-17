[English](BE-XXXX-ios-cross-app-ui-control-feasibility.md) · **日本語**

# BE-XXXX — シナリオから指定アプリを起動してUIを操作できるようにする（iOS）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-ios-cross-app-ui-control-feasibility-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Platform support |
<!-- /BE-METADATA -->

## はじめに

この項目は、シナリオの記述言語から、テスト対象アプリが一度も起動していないアプリをバンドルIDで
起動し、そのアプリ自身のUIを操作できるようにします。アクセシビリティツリーを読み、タップし、
文字を入力し、アサーションを書けます。テスト対象アプリを操作するときと同じ書き方です。新しい
ステップは、下で`app: { bundleId, steps }`という形に素描します。[BE-0037](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support-ja.md)
がWebViewの文書オブジェクトモデルへ入るために確立した形を踏襲します。ドライバのクエリと操作の対象を、
入れ子の`steps`のあいだだけ指定したアプリへ切り替え、ブロックを抜けたらテスト対象アプリへ戻します。
これはiOS限定です。XCUITest backendの`XCUIApplication(bundleIdentifier:)`という能力に立っており、
webやAndroidのbackendにはこの能力がありません。

この設計を書く前に、使い捨てのXCTestスパイクを1つ実行し、設計の前提となる2つの事実を確かめました。
`XCUIApplication(bundleIdentifier:).activate()`は、テスト対象アプリ側の協力なしに、テスト対象アプリが
一度も起動していないアプリを確実に前面へ出します。Simulator上のSafari・Maps・Contacts
のいずれでも、`.runningForeground`へ到達し、非空のアクセシビリティツリーが返りました。常駐ランナーの
「1つの長寿命テストメソッド」という設計も、関係のないアプリ間の受け渡しを繰り返してもテストプロセスが
落ちずに持ちこたえました。どちらの事実も、その裏付けとなる実測値とともに
[`docs/specs/ios-cross-app-ui-control-feasibility.md`](../../docs/specs/ios-cross-app-ui-control-feasibility.md)
に記録してあります。この項目ではそれを繰り返さず、その上に設計を積み重ねます。

## 動機

XCUITest backendは現在、テスト対象アプリ1つを指すハンドルを軸に組まれています
（`XcuitestElementProvider.app`）。実装は
[`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift:35`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)
にあります。現状の例外は2つあります。SpringBoard（システムアラート用）と、
`SFSafariViewController`を描くプロセスである`com.apple.SafariViewService`です
（[BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md)）。
どちらも、`.state == .runningForeground`を確認してから、そのツリーを**テスト対象アプリ自身の応答へ
マージする**遅延構築の副次ハンドルです。この形は、テスト対象アプリ自身の画面に重なって出る相手には
合います。権限プロンプトや、アプリ内ブラウザのシートがそれにあたります。関係のないアプリをしばらくの
あいだ主として操作しようとするシナリオには合いません。Safari.app本体（アプリ内の
`SFSafariViewController`ではなく）へのOAuthハンドオフや、Mail.appのような共有シートの宛先が
そのシナリオにあたります。テスト対象アプリ自身では切り替えられない権限を切り替えるための、
Settings.appへの往復もそうです。こうしたシナリオは今のところ一切書けません。シナリオの作成者には、
2つ目のアプリを名指しする手段そのものがありません。

スパイクは2つの問いを閉じました。「SpringBoard・SafariViewServiceが使っているパターンは存在する」と
「テスト対象アプリの所有しないアプリへ一般化できる」のあいだにあった問いです。`activate()`だけで、
協力しないアプリを前面に出せるかどうか。常駐ランナーの1テストメソッド設計が、アプリをまたぐ受け渡しの
繰り返しに耐えるかどうか。どちらも、試したすべてのアプリで成り立ちました。残るのは、スパイク単体では
答えが出ない設計上の問いです。シナリオがアプリをどう名指しするか、ドライバのクエリ・操作対象がどう
切り替わりどう戻るか、そのブロックの中でシナリオ作成者に何ができて何ができないか。それがこの項目の
詳細設計です。

## 詳細設計

### シナリオから見えるステップ

```
{ app: { bundleId: string, steps: list(<Step>) } }
```

`bundleId`は、操作するアプリを名指しする素のstringです。固定の集合から選ぶ値ではありません。
そのため、このステップはprime directive 3が求めるapp非依存を保ちます。config に列挙したアプリ
だけでなく、インストール済みの任意のアプリを名指しできます。`steps`は、既存の`Step`文法をそのまま
入れ子にします。`tap`・`type`・`assert`をはじめとする各ステップは、ブロックの中では名指ししたアプリ
自身のツリーに対してセレクタを解決し、操作します。ブロックの外でテスト対象アプリに対して行うのと
まったく同じ形です。ブロックへ入るとき、ドライバは名指ししたアプリをactivateします。ブロックを
抜けるとき——最後のステップが完了したとき、あるいは中のステップが失敗したとき——ブロックへ入る前に
アクティブだったアプリへactivateし直します。これは[`web: { within, steps }`](../../docs/dsl-grammar.md)
（BE-0037）が持つ、入って抜けるという契約をそのまま踏襲したもので、別の契約を新しく発明しては
いません。`app:`ブロックを入れ子にした場合も、スパイクが手作業でなぞったのと同じスタック規律に
従います（Safari→Maps→Safariへ戻る）。各ブロックは、テスト対象アプリへ無条件に戻るのではなく、
直近の親へ戻ります。シナリオは1つ目のアプリの中から2つ目のアプリを訪れても、戻り道を見失いません。

### Unit 1 — 実現可能性のスパイク（完了）

すでに実行済みです。設計と実測値は、上の「はじめに」と
[`docs/specs/ios-cross-app-ui-control-feasibility.md`](../../docs/specs/ios-cross-app-ui-control-feasibility.md)
にあります。スパイクからは何も出荷していません。使い捨てのファイルは削除しました。
`BajutsuRunner.xcodeproj`は、XcodeGenが生成するgitignore対象の成果物です
（[`BajutsuKit/Runner/project.yml:36-37`](../../BajutsuKit/Runner/project.yml)）。
`xcodegen generate`でスパイク前の状態に戻しました。このUnitが残す唯一の恒久的な成果物は、この項目の
設計全体が拠って立つ答えです。

### Unit 2 — `XcuitestElementProvider`にクエリ・操作対象のスタックを持たせる

`XcuitestElementProvider.app`は、現在は固定の単一プロパティです。これを、`XCUIApplication`ハンドルの
スタックの先頭に変えます。スタックの初期値はテスト対象アプリ自身のハンドルです。`query()`・`tap`をはじめ
とするすべての操作は、スタックの先頭に対して解決します。それ以外の点はすべて現状のままです。既存の
SpringBoard・SafariViewServiceのマージ処理には触れません。システムアラートは、`app:`ブロックが現在
どのアプリを対象にしていても割り込みうるため、この処理はこれまでどおり、現在のスタック先頭ではなく
テスト対象アプリ自身のエントリを基準に読み取り続けます。`app:`ブロックに入ると、新しい
`XCUIApplication(bundleIdentifier:)`ハンドルをpushして`.activate()`します。ブロックを抜けると、
それをpopして1つ下のハンドルを`.activate()`します。実装時に詰める点として、テスト対象アプリが対応する
すべての操作（ジェスチャ、テキスト選択、ピッカーホイール）が、別アプリのハンドルに対しても変わらず
成り立つかは未確認です。スパイクが測ったのは`activate()`とツリーの読み取りだけで、操作面全体では
ありません。各能力は、それぞれのステップが実装される段階で、実際の別アプリに対して確かめるべきで、
前提として扱うべきではありません。

### Unit 3 — capabilityのゲーティング

`app:`はXCUITest限定なので、専用のpreflightトークンが要ります。`handleSystemAlert`や
`setPickerValue`（[docs/architecture.md](../../docs/architecture.md#implementation-status)）と並ぶ、
操作単位の新しいcapabilityです。BE-0212が古い粗い`deviceControl`ファミリーを操作単位のトークンへ
分割した前例にならいます。オール・オア・ナッシングのゲートにはしません。webやAndroidのbackendで
`app:`を使うシナリオは、デバイス側の作業より前で、名指しでpreflightに失敗します。今日の
`setPickerValue`と同じ扱いです。

### Unit 4 — シナリオモデルとドライバインタフェース

Pythonのシナリオモデル（`bajutsu/common/scenario/models/`）に、`App`ステップ型（`bundleId: str`、
`steps: list[Step]`）を追加します。検証の仕方は、`Web`の`within`・`steps`の組と同じにします。
`Driver`インタフェースには、Unit 2のスタックに対してXCUITest backendが実装する、入る・抜けるの
1組を追加します。ほかのすべてのbackendの実装は「非対応」の経路をraiseするだけです。Unit 3の
preflightトークンがシナリオより前ですでに弾いているため、これらのbackendにとっては新しい実行時
分岐ではなく、コンパイル時の表面にとどまります。

### Unit 5 — showcaseのフィクスチャとシナリオ

showcaseのシナリオで、実際のシステムアプリに対して`app:`ステップを端から端まで動かします。
[BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md)の
`scenarios/browser.yaml`が使うのと同じ、フィクスチャアプリのパターンです。候補となる流れは次の
とおりです。showcaseアプリから`app:`ブロックでSafari（または別のシステムアプリ）をactivateし、その
中の要素をアサートします。ブロックを抜けたら、showcaseの要素が再び読めることをアサートします。
スパイクが手作業で測ったのと同じ往復を、シナリオの記述言語を通して駆動し、一度きりの手作業実行ではなく、
リポジトリ自身のCIが継続して見張る回帰網の一部にします。

### Unit 6 — ドキュメント

新しいステップを、`docs/dsl-grammar.md`・`docs/drivers.md`・`docs/scenarios.md`へ追記します。
それぞれ`Action`文法、XCUITestの節、クックブックです。両言語で書きます。

### prime directiveへの適合

- **AIをゲートに入れません。** このステップは、アプリのactivate・セレクタの解決・操作という、ほかと
  変わらない決定的な操作です。判定経路にモデルは乗りません。
- **決定性を優先します。** `app:`ブロックの中のセレクタ解決も、ほかの場所と同じ「曖昧なら即失敗」の
  規則に従います。`.state`のポーリングはほかの条件待ちと同じ条件待ちであり、固定sleepではないので、
  新しい待機のプリミティブは持ち込みません。
- **appに依存しません。** `bundleId`は、特定のパートナーアプリを1つ名指しするconfig値ではなく、
  シナリオレベルの素のstringです。ツールそのものは、各targetがどの具体的なアプリを駆動するかには
  関与しません。

## 検討した代替案

- **名指ししたアプリのツリーを、テスト対象アプリの応答へマージする。** SpringBoard・SafariViewService
  と同じ扱いにする案です。テスト対象アプリ自身の画面に*重なって*出る相手には合いますが、
  関係のないアプリをしばらくのあいだ主として操作しようとするシナリオには合いません。2つのツリーが
  1つの応答にマージされると、シナリオの作成者には、あるセレクタがどちらのツリーに対して解決される
  べきかを指定する手段もなくなります。ブロックの範囲で閉じた`app:`ステップは、この問題を構造で
  避けます。一度に解決対象となるツリーは常に1つだけです。
- **ステップでアプリを名指しする代わりに、showcaseにSafari.appを開くボタンを持たせて操作する。**
  このステップは、任意の名指ししたバンドル
  IDで動く必要があります。テスト対象アプリ自身が遷移のきっかけを一切作らないアプリ（このリポジトリの
  外にあるディープリンクからしか届かないパートナーアプリなど）も含みます。アプリ内の起点に入り口を
  縛ると、この機能はシナリオ作成者がたまたま手元に持っている起点の分しか使えなくなります。
- **シナリオステップの代わりに`bajutsu repl`（BE-0423の対話シェル）経由で操作する。** `bajutsu repl`は、
  1つのアプリのツリーを調べるための執筆支援ツールです。再利用可能でコミットできるシナリオを表現する
  手段ではありません。両者は補い合う関係です。`bajutsu repl`は、シナリオ
  ファイルが持ち運べ、CIで走り、決定的に再実行できるステップの代わりにはなりません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに1つ）に対応し、ログには変更内容と時期（古い順）をPRへのリンクとともに
> 記録します。

- [x] Unit 1 — 実現可能性のスパイク。Safari/Maps/Contactsで`activate()`と常駐ランナーの持ちこたえを
  確認済み
- [ ] Unit 2 — `XcuitestElementProvider`のクエリ・操作対象スタック
- [ ] Unit 3 — `app:`のpreflight capabilityトークン
- [ ] Unit 4 — シナリオモデル（`App`ステップ型）と`Driver`インタフェースの入る・抜けるの組
- [ ] Unit 5 — `app:`を端から端まで動かすshowcaseのフィクスチャとシナリオ
- [ ] Unit 6 — ドキュメント（`dsl-grammar.md`、`drivers.md`、`scenarios.md`、両言語）

ログ：

- 2026-09-18 — Unit 1を使い捨てのスパイクとして完了しました。出荷はしていません。
  `CrossAppFeasibilitySpike.swift`が、検証専用のiPhone 17 Pro Simulator（iOS 26.5、Xcode 26.6）上で
  `com.apple.mobilesafari`・`com.apple.Maps`・`com.apple.MobileAddressBook`を順にactivateしました。
  3つとも`.runningForeground`へ到達し、非空のツリー（それぞれ136/50/127要素）が返りました。前の
  アプリへactivateし直したときも非空のツリー（134/50要素）が返り、常駐テストプロセスは一連の切り替えを
  通じて最後まで落ちませんでした（合計11.288秒、失敗0件）。結果は
  `docs/specs/ios-cross-app-ui-control-feasibility.md`に記録し、スパイクのファイルと
  `project.pbxproj`への配線はそのあと削除しました。

## 参考

- [`docs/specs/ios-cross-app-ui-control-feasibility.md`](../../docs/specs/ios-cross-app-ui-control-feasibility.md) ——
  実現可能性のスパイクの設計と実測値です（Unit 1）。
- `BajutsuKit/Runner/Sources/XcuitestElementProvider.swift` —— Unit 2がスタックへ変える、テスト対象
  アプリ1つに固定したハンドルです。既存のSpringBoard・SafariViewServiceの副次ハンドルには、Unit 2は
  触れません。
- `BajutsuKit/Runner/Sources/RunnerUITest.swift` —— 常駐ランナーの「1つの長寿命テストメソッド」という
  設計です。スパイクはこれを、関係のないアプリ間の受け渡しの繰り返しでも確かめました。
- [BE-0037 — WebView / hybrid support](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support-ja.md) ——
  この項目の`app:`ステップが踏襲する、入る・抜けるの契約です。
- [BE-0212 — 粗い deviceControl 能力を操作単位のトークンに分割する](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities-ja.md) ——
  Unit 3の新しいpreflightトークンが従う前例です。
- [BE-0396 — SFSafariViewController の要素ツリーを、それを描くプロセスから読む](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree-ja.md) ——
  この項目の「動機」が限界を説明する、副次ハンドルのパターンです。Unit 5が踏襲するshowcaseフィクスチャの
  パターンでもあります。
- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) ——
  `XcuitestElementProvider`を所有する常駐ランナーです。
- [BE-0423 — 要素ツリーを閲覧し id で操作する対話シェル](../BE-0423-cli-repl-inspect-actuate/BE-0423-cli-repl-inspect-actuate-ja.md) ——
  上で検討し、採らなかった代替案です。

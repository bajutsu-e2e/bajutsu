# iOSで別アプリのUIを操作する可能性調査

> ステータス: 調査完了（結果は[BE-XXXX](../../roadmaps/BE-XXXX-ios-cross-app-ui-control-feasibility/BE-XXXX-ios-cross-app-ui-control-feasibility-ja.md)のシナリオ記法の設計の根拠に使う）
> 対象: `BajutsuKit/Runner/`（XCUITest backend）
> 関連: [docs/drivers.md](../drivers.md#xcuitest-ios)、[docs/architecture.md](../architecture.md#xcuitest-ios)、[docs/dsl-grammar.md](../dsl-grammar.md)

BajutsuのXCUITest backendは、テスト対象アプリ以外のアプリを操作できるでしょうか。
ここでは、Safari.app、Maps.app、Contacts.appを例にとります。
いずれの場合も、テスト対象アプリは一切関与しません。
この文書は、その可能性を使い捨てのスパイクコードで実証した記録です。
スパイクは2026-09-18に、iPhone 17 Pro Simulator（iOS 26.5、Xcode 26.6）上で実行し、3つのアプリすべてで成功しました。
結果は2章にあります。
スパイクコード自体は、実証後に削除しました。
記録として残るのは、この文書と[BE-XXXX](../../roadmaps/BE-XXXX-ios-cross-app-ui-control-feasibility/BE-XXXX-ios-cross-app-ui-control-feasibility.md)だけです。

## 1. なにをつくったか

`BajutsuKit/Runner/BajutsuRunner.xcodeproj`のUIテストターゲットに、一時的なXCTestCaseを1つ追加して実行しました。

- `com.apple.mobilesafari`（Safari）、`com.apple.Maps`（Maps）、`com.apple.MobileAddressBook`（Contacts）の3つが対象です。この3つを順に`XCUIApplication(bundleIdentifier:)`で`activate()`しました。そのつど、アクセシビリティツリーが非空で読み取れるかを確認しました。
- 1つのアプリを`activate()`した状態から、直前のアプリへ`activate()`で戻しました。戻った先のツリーが引き続き読み取れるかを確認しました。
- この一連の操作を、1つの長寿命テストメソッドの中で行いました。Bajutsuの常駐ランナーも、同じ形を取ります（BE-0019）。途中でランナーのプロセスが落ちないかを確認しました。

### やらなかったこと

- シナリオを記述する`*.yaml`への、新しいステップの追加はしませんでした。スパイクはSwiftのテストコードのみで完結させ、Pythonにも`*.yaml`の記法にも触れていません。新しいステップの設計は、[BE-XXXX](../../roadmaps/BE-XXXX-ios-cross-app-ui-control-feasibility/BE-XXXX-ios-cross-app-ui-control-feasibility.md)の詳細設計に書きました。
- テスト対象アプリ（showcase）は関与させませんでした。showcaseには、外部アプリを開く導線が現状ありません。`demos/showcase`に`openURL`や`UIApplication.shared.open`系の呼び出しは見つかりませんでした。確かめたいのはXCUITest自身の`activate()`能力であり、テスト対象アプリ側の導線とは無関係です。
- 実機とデバイスクラウドでの検証はしませんでした。Simulatorでの検証に限りました。

## 2. なぜつくったか

XCUITest backendは現在、テスト対象アプリ1つを指すハンドルを軸に組まれています（`XcuitestElementProvider.app`）。
実装は[`XcuitestElementProvider.swift:35`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)にあります。
例外は、SpringBoard（システムアラート）とcom.apple.SafariViewService（`SFSafariViewController`を描画するプロセス）の2つに限ります。
どちらも、`bundleIdentifier`だけで構築した副次ハンドルを持ちます。
読み取った結果は、専用実装がテスト対象アプリのツリーへ**マージ**します（[`:38-41`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)）。
この専用実装は、テスト対象アプリと同じ画面に重なって出る場面にしか対応しません。

この2つの副次ハンドルは、`.launch()`を一度も呼ばずに構築されます。
`.state == .runningForeground`を見てから読み取ります（[`:73-77`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)）。
この形は、XCUITestが公式に提供する「マルチアプリUIテスト」機能の一部を使います。
この機能は、`XCUIApplication(bundleIdentifier:)`と`.activate()`で任意のインストール済みアプリを前面に出し、そのアプリ自身のアクセシビリティツリーを読みます。
Bajutsuは、この機能をSpringBoardとSafariViewServiceという2つの固定した相手にだけ使っています。
この事実は、任意の別アプリへ一般化できる可能性を示します。
ただし、次の2点は現在のコードを読むだけでは判定できませんでした。

- SpringBoardとSafariViewServiceは、どちらもOS自身が前面に出す相手です。テスト対象アプリ側の操作をきっかけに、OSが自発的にフォアグラウンドを渡します。システムアラートの表示や、SFSafariViewControllerの提示がそれにあたります。Safari.app本体やMaps.app、Contacts.appは、これと違います。ユーザー操作なしには勝手に前面へ出てきません。こうしたアプリを、テスト対象アプリの協力なしに`activate()`だけで前面へ出せるかどうかは、確かめていませんでした。
- 常駐ランナーは、1つの長寿命テストメソッドが多数の操作を捌く設計です（[`RunnerUITest.swift:15-16`](../../BajutsuKit/Runner/Sources/RunnerUITest.swift)のコメント）。SpringBoardとSafariViewServiceの読み取りは、この設計の中ですでに実証済みです。フォアグラウンドの主が何度も入れ替わる操作でも、同じ前提が保たれるかどうかは、確かめていませんでした。たとえばSafari→Maps→Contacts→元のアプリという切り替えの連続です。

### 得られた答え

どちらの問いにも、**イエス**という答えが出ました。

3アプリすべてで、テスト対象アプリ側の協力なしに、`activate()`だけで前面へ出せることを確認しました。
テスト対象アプリを一切起動せず、Safari、Maps、Contactsを直接`activate()`した実行でも、同じ結果になりました。
フォアグラウンドの主が3回切り替わっても、常駐テストプロセスは最後まで落ちませんでした。
実測値を次に示します。

| ステップ | `.state`が`.runningForeground`に到達 | アクセシビリティ要素数 | 所要時間（累計） |
|---|---|---|---|
| Safariをactivate() | ✅ | 136 | 3.24s |
| Mapsをactivate() | ✅ | 50 | 7.00s |
| Safariへ戻ってactivate() | ✅ | 134 | 7.05s |
| Contactsをactivate() | ✅ | 127 | 9.83s |
| Mapsへ戻ってactivate() | ✅ | 50 | 9.95s |

テスト全体は11.288秒で完了し、失敗は0件でした。
XCTestの内部ログは、初めて訪れるアプリを`Launch`、2回目以降に戻るアプリを`Activate`と区別して記録しています。
`.activate()`の呼び出しコードは同じでも、XCUITestの側で「新規起動」と「再開」を正しく判別していることが読み取れます。
Safariの要素数は、`activate()`のたびに136→134とわずかに変化しました。
Mapsは50→50で、完全に一致しました。
Safariの2件のずれは、ページの読み込み状況のような一過性のUI状態に起因すると見られます。
他アプリの活性化に影響されたわけではありません。

## 3. シナリオの記法へどう活かすか

この調査は、シナリオの`*.yaml`から特定のアプリを起動し、そのAX（アクセシビリティ）ツリーを取得、操作できるようにすることを目指します。
新しいステップの詳細設計と作業分解は、この調査結果を根拠として、[BE-XXXX: シナリオから指定アプリを起動してUIを操作できるようにする（iOS）](../../roadmaps/BE-XXXX-ios-cross-app-ui-control-feasibility/BE-XXXX-ios-cross-app-ui-control-feasibility-ja.md)に書きました。
この文書では繰り返しません。

## 4. スパイクの実装の記録

### 配置と実行

`BajutsuKit/Runner/Sources/`に一時ファイル`CrossAppFeasibilitySpike.swift`を追加しました。実行は`BajutsuRunnerUITests`ターゲットで行いました。
**当初、このターゲットへの追加には`project.pbxproj`の手編集が要ると見込んでいましたが、これは誤りでした。**

`BajutsuKit/Runner/project.yml`（[`project.yml:36-37`](../../BajutsuKit/Runner/project.yml)）では、`Sources`ディレクトリが`BajutsuRunnerUITests`の`sources`です。
XcodeGenは、`Sources/`配下の`.swift`ファイルをすべて自動的に拾います。
`BajutsuRunner.xcodeproj/`は、`.gitignore`（[`.gitignore:63`](../../.gitignore)）でリポジトリから除外されています。
このディレクトリで`xcodegen generate`を実行するたびに、`project.yml`から生成し直されます。
新しいSwiftファイルを追加したら、`xcodegen generate`を実行するだけでよく、`project.pbxproj`を手で編集する必要はありません。

実行したコマンドを次に示します。

```bash
cd BajutsuKit/Runner && xcodegen generate
xcodebuild test \
  -project BajutsuRunner.xcodeproj \
  -scheme BajutsuRunner \
  -only-testing:BajutsuRunnerUITests/CrossAppFeasibilitySpike \
  -destination 'platform=iOS Simulator,id=<検証専用SimulatorのUDID>' \
  -skipPackagePluginValidation
```

`-skipPackagePluginValidation`を付けないと、`xcodebuild`は「プラグインを有効化してから使ってください」というビルドエラーで止まります。
`BajutsuRunner`が`swift-openapi-generator`のビルドツールプラグインに依存しているため、このフラグが要ります。
`demos/showcase/Makefile`がXCUITestランナーをビルドする箇所（[`demos/showcase/Makefile:54-62`](../../demos/showcase/Makefile)）も、同じ理由でこのフラグを使っています。

### テストコード

```swift
import XCTest

final class CrossAppFeasibilitySpike: XCTestCase {
    func testActivateAndReadForeignAppTrees() throws {
        let targets: [(name: String, bundleId: String)] = [
            ("Safari", "com.apple.mobilesafari"),
            ("Maps", "com.apple.Maps"),
            ("Contacts", "com.apple.MobileAddressBook"),
        ]

        var previous: (name: String, app: XCUIApplication)?
        for (name, bundleId) in targets {
            let app = XCUIApplication(bundleIdentifier: bundleId)
            app.activate()

            var reachedForeground = false
            for _ in 0..<20 {
                if app.state == .runningForeground {
                    reachedForeground = true
                    break
                }
                Thread.sleep(forTimeInterval: 0.5)
            }

            let elementCount = app.descendants(matching: .any).count
            print("SPIKE_RESULT app=\(name) bundleId=\(bundleId) reachedForeground=\(reachedForeground) state=\(app.state.rawValue) elementCount=\(elementCount)")

            if let previous {
                previous.app.activate()
                // (前のアプリへ戻ったあとの読み取りも同様に記録する — 省略)
            }
            previous = (name, app)
        }
    }
}
```

このコードは疑似コードではなく、実際に実行したコードそのものです。
ただし`XCTAssert`は持ちません。
`reachedForeground`や`elementCount`を`print`するだけで、しきい値を判定してテストを失敗させることはしません。
この1回限りのスパイクは、「`activate()`のあとどれだけ待てば前面化を確認できるか」という測定値そのものを確かめようとしました。
判定基準は、あらかじめ決めていませんでした。
合否は、`xcodebuild`の出力に印字された`SPIKE_RESULT`行を目で読んで確認しました。
判定に使った実測値は、上の「2. なぜつくったか」の「得られた答え」に記録済みです（各アプリの`elementCount`、`reachedForeground`がすべて`true`であったこと）。
恒久的なテストではなく、この記録がその検証の唯一の記録として残ります。

検証専用のSimulatorは、`xcrun simctl create`で作成し、実行後に`xcrun simctl shutdown` / `delete`で破棄しました。
他セッションがすでに起動しているSimulatorは、流用していません。

### 後始末

実証後、`CrossAppFeasibilitySpike.swift`を削除しました。
`BajutsuKit/Runner/`で`xcodegen generate`を再実行し、`project.pbxproj`を元の状態に戻しました。
`git status`で、リポジトリに差分が残っていないことを確認しました。

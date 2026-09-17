# iOSで別アプリのUIを操作する可能性調査

> ステータス: 調査完了（結果は[BE-XXXX](../../roadmaps/BE-XXXX-ios-cross-app-ui-control-feasibility/BE-XXXX-ios-cross-app-ui-control-feasibility-ja.md)のシナリオ記法の設計の根拠に使う）
> 対象: `BajutsuKit/Runner/`（XCUITest backend）
> 関連: [docs/drivers.md](../drivers.md#xcuitest-ios)、[docs/architecture.md](../architecture.md#xcuitest-ios)、[docs/dsl-grammar.md](../dsl-grammar.md)

BajutsuのXCUITest backendは、テスト対象アプリ以外のアプリを操作できるか。ここではSafari.app、Maps.app、Contacts.appを例にとる。いずれもテスト対象アプリは一切関与しない。この文書は、その可能性を使い捨てのスパイクコードで実証した記録を残す。スパイクは2026-09-18にiPhone 17 Pro Simulator（iOS 26.5、Xcode 26.6）上で実行し、3つのアプリすべてで成功した。結果は2章にある。スパイクコード自体は実証後に削除済みで、この文書と[BE-XXXX](../../roadmaps/BE-XXXX-ios-cross-app-ui-control-feasibility/BE-XXXX-ios-cross-app-ui-control-feasibility.md)だけが記録として残る。

## 1. なにをつくったか

`BajutsuKit/Runner/BajutsuRunner.xcodeproj`のUIテストターゲットに、一時的なXCTestCaseを1つ追加して実行した。

- `com.apple.mobilesafari`（Safari）、`com.apple.Maps`（Maps）、`com.apple.MobileAddressBook`（Contacts）の3つを対象にした。この3つを順に`XCUIApplication(bundleIdentifier:)`で`activate()`し、そのつどアクセシビリティツリーが非空で読み取れるかを確認した。
- 1つのアプリをactivate()した状態から、直前のアプリへ`activate()`で戻した。戻った先のツリーが引き続き読み取れるかを確認した。
- この一連の操作を、1つの長寿命テストメソッドの中で行った。Bajutsuの常駐ランナーも同じ形を取る（BE-0019）。途中でランナーのプロセスが落ちないかを確認した。

### やらなかったこと

- シナリオを記述する`*.yaml`への新しいステップ追加はしなかった。スパイクはSwiftのテストコードのみで完結させた。Pythonにも`*.yaml`の記法にも触れていない。新しいステップの設計は[BE-XXXX](../../roadmaps/BE-XXXX-ios-cross-app-ui-control-feasibility/BE-XXXX-ios-cross-app-ui-control-feasibility.md)の詳細設計に書いた。
- テスト対象アプリ（showcase）は関与させなかった。showcaseには外部アプリを開く導線が現状ない。`demos/showcase`に`openURL`や`UIApplication.shared.open`系の呼び出しは見つからなかった。確かめたいのはXCUITest自身の`activate()`能力であり、テスト対象アプリ側の導線とは無関係である。
- 実機・デバイスクラウドでの検証はしなかった。Simulatorでの検証に限った。

## 2. なぜつくったか

XCUITest backendは現在、テスト対象アプリ1つを指すハンドルを軸に組まれている（`XcuitestElementProvider.app`）。実装は[`XcuitestElementProvider.swift:35`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)にある。例外はSpringBoard（システムアラート）とcom.apple.SafariViewService（`SFSafariViewController`を描画するプロセス）の2つに限る。どちらも`bundleIdentifier`だけで構築した副次ハンドルを持つ。読み取った結果は、専用実装がテスト対象アプリのツリーへ**マージ**する（[`:38-41`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)）。この専用実装は、テスト対象アプリと同じ画面に重なって出る場面にしか対応しない。

この2つの副次ハンドルは、`.launch()`を一度も呼ばずに構築される。`.state == .runningForeground`を見てから読み取る（[`:73-77`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)）。この形は、XCUITestが公式に提供する「マルチアプリUIテスト」機能の一部を使う。この機能は`XCUIApplication(bundleIdentifier:)`と`.activate()`で任意のインストール済みアプリを前面に出し、そのアプリ自身のアクセシビリティツリーを読む。Bajutsuはこの機能を、SpringBoardとSafariViewServiceという2つの固定した相手にだけ使っている。この事実は、任意の別アプリへ一般化できる可能性を示す。ただし次の2点は、現在のコードを読むだけでは判定できなかった。

- SpringBoardとSafariViewServiceは、どちらもOS自身が前面に出す相手である。テスト対象アプリ側の操作をきっかけに、OSが自発的にフォアグラウンドを渡す。システムアラートの表示や、SFSafariViewControllerの提示がそれにあたる。Safari.app本体やMaps.app、Contacts.appはこれと違う。ユーザー操作なしには勝手に前面へ出てこない。こうしたアプリを、テスト対象アプリの協力なしに`activate()`だけで前面へ出せるかどうかは、確かめていなかった。
- 常駐ランナーは、1つの長寿命テストメソッドが多数の操作を捌く設計である（[`RunnerUITest.swift:9-10`](../../BajutsuKit/Runner/Sources/RunnerUITest.swift)のコメント）。SpringBoardとSafariViewServiceの読み取りは、この設計の中ですでに実証済みである。フォアグラウンドの主が何度も入れ替わる操作、たとえばSafari→Maps→Contacts→元のアプリという切り替えの連続でも、同じ前提が保たれるかどうかは、確かめていなかった。

### 得られた答え

どちらの問いにも、**イエス**という答えが出た。

3アプリすべてで`activate()`だけを呼び、テスト対象アプリ側の協力なしに前面へ出せることを確認した。テスト対象アプリを一切起動せず、Safari・Maps・Contactsを直接`activate()`した実行でも同じ結果になった。フォアグラウンドの主が3回切り替わっても、常駐テストプロセスは最後まで落ちなかった。実測値を次に示す。

| ステップ | `.state`が`.runningForeground`に到達 | アクセシビリティ要素数 | 所要時間（累計） |
|---|---|---|---|
| Safariをactivate() | ✅ | 136 | 3.24s |
| Mapsをactivate() | ✅ | 50 | 7.00s |
| Safariへ戻ってactivate() | ✅ | 134 | 7.05s |
| Contactsをactivate() | ✅ | 127 | 9.83s |
| Mapsへ戻ってactivate() | ✅ | 50 | 9.95s |

テスト全体は11.288秒で完了し、失敗は0件だった。XCTestの内部ログは、初めて訪れるアプリを`Launch`、2回目以降に戻るアプリを`Activate`と区別して記録している。`.activate()`の呼び出しコードは同じでも、XCUITestの側で「新規起動」と「再開」を正しく判別していることが読み取れる。Safariの要素数はactivate()のたびに136→134とわずかに変化したが、Mapsは50→50で完全に一致した。Safariの2件のずれは、ページの読み込み状況のような一過性のUI状態に起因すると見られ、他アプリの活性化に影響されたわけではない。

## 3. シナリオの記法へどう活かすか

この調査は、シナリオの`*.yaml`から特定のアプリを起動し、そのAX（アクセシビリティ）ツリーを取得・操作できるようにすることを目指す。新しいステップの詳細設計と作業分解は、この調査結果を根拠として
[BE-XXXX — シナリオから指定アプリを起動してUIを操作できるようにする（iOS）](../../roadmaps/BE-XXXX-ios-cross-app-ui-control-feasibility/BE-XXXX-ios-cross-app-ui-control-feasibility-ja.md)
に書いた。この文書では繰り返さない。

## 4. スパイクの実装（記録）

### 配置と実行

`BajutsuKit/Runner/Sources/`に一時ファイル`CrossAppFeasibilitySpike.swift`を追加し、`BajutsuRunnerUITests`ターゲットで実行した。**当初、このターゲットへの追加には`project.pbxproj`の手編集が要ると見込んでいたが、これは誤りだった。**

`BajutsuKit/Runner/project.yml`（[`project.yml:36-37`](../../BajutsuKit/Runner/project.yml)）は、`BajutsuRunnerUITests`の`sources`に`Sources`ディレクトリを指定している。XcodeGenは`Sources/`配下の`.swift`ファイルをすべて自動的に拾う。`BajutsuRunner.xcodeproj/`は`.gitignore`（[`.gitignore:63`](../../.gitignore)）でリポジトリから除外されている。`xcodegen generate`をこのディレクトリで実行するたびに、`project.yml`から生成し直される。新しいSwiftファイルを追加したら、`xcodegen generate`を実行するだけでよい。`project.pbxproj`を手で編集する必要はない。

実行したコマンドを次に示す。

```bash
cd BajutsuKit/Runner && xcodegen generate
xcodebuild test \
  -project BajutsuRunner.xcodeproj \
  -scheme BajutsuRunner \
  -only-testing:BajutsuRunnerUITests/CrossAppFeasibilitySpike \
  -destination 'platform=iOS Simulator,id=<検証専用SimulatorのUDID>' \
  -skipPackagePluginValidation
```

`-skipPackagePluginValidation`を付けないと、`xcodebuild`は「プラグインを有効化してから使ってください」というビルドエラーで止まる。`BajutsuRunner`は`swift-openapi-generator`のビルドツールプラグインに依存しているため、このフラグが要る。`demos/showcase/Makefile`がXCUITestランナーをビルドする箇所（[`demos/showcase/Makefile:54-62`](../../demos/showcase/Makefile)）も、同じ理由でこのフラグを使っている。

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

検証専用のSimulatorは`xcrun simctl create`で作成し、実行後に`xcrun simctl shutdown` / `delete`で破棄した。他セッションがすでに起動しているSimulatorは流用していない。

### 後始末

実証後、`CrossAppFeasibilitySpike.swift`を削除した。`BajutsuKit/Runner/`で`xcodegen generate`を再実行し、`project.pbxproj`を元の状態に戻した。`git status`でリポジトリに差分が残っていないことを確認した。

[English](BE-0310-screen-transition-verification.md) · **日本語**

# BE-0310 — オンデバイス検証：画面遷移シグナル

これは、
[BE-0310](../../roadmaps/BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md)
がゲート（作業単位5）として名指ししているオンデバイスでの確認です。`BajutsuScreen` の `viewDidAppear`
swizzle が、showcase の両ツールキットで実際に遷移を報告するかを確かめます。対象は、readiness ゲートと `settled` 待ちが
新たに参照する遷移と、意図して覆わない2つのケースです。高速ゲート（`make check`）はシミュレータを一切
動かさないため、この手順は Mac 上で手動により実行してください。実行し終えたら、結果をこのファイル
（またはこの項目にリンクした後続のコメント）に記録してください。

## 前提条件

- Xcode を入れた macOS と、起動済みのシミュレータです。`make -C demos/showcase run-swiftui` がすでに動く状態を
  前提とします。`make deps` や `make runner-build` のセットアップは、リポジトリルートの
  [`CLAUDE.md`](../../CLAUDE.md) を参照してください。
- 両方の showcase アプリは、すでに `BajutsuNet.startIfEnabled()` を呼んでいます。呼び出し箇所は
  [`ShowcaseApp.swift`](ios/swiftui/Sources/ShowcaseApp.swift) と
  [`AppDelegate.swift`](ios/uikit/Sources/AppDelegate.swift) です。これが `BajutsuScreen` も同時に
  有効化するので、この手順を実行するためにアプリのコードを変更する必要はありません。

## 何を確認するか

`showcase-swiftui` と `showcase-uikit` の**両方**について、次を確認します。

1. **cold launch から最初の画面へ。** 最初の画面の `viewDidAppear` は発火するはずで、cold launch でも
   readiness にシグナルを与えます。最初のビューコントローラも、後続の push やタブ切り替えと同じように出現するからです。
   結果を記録してください。もし報告されなくても退行ではなく、提案どおり readiness はその瞬間だけ
   BE-0218 の梯子を使い続けるだけです。
2. **ナビゲーションの push。** `stable.row.3` から Horse Detail へ、
   [`navigation.yaml`](scenarios/navigation.yaml) が示すとおりです。
3. **モーダルの提示。** Log タブの detented sheet、[`modals.yaml`](scenarios/modals.yaml) が示すとおりです。
4. **タブの切り替え。** [`tabs.yaml`](scenarios/tabs.yaml) が示すとおりです。
5. **意図して覆わないケース。** フォールバックの役割を証拠から確かめます。
   - 画面遷移を伴わない**画面内のデータ更新**です。Log タブの「Intense」トグル（`log.intense`）が対象で、
     ナビゲーションなしにアクセシビリティの値だけを更新します。新しいビューコントローラを提示しないため、
     `viewDidAppear` は発火せず、何も報告されません。
   - **標準コンテナを迂回するカスタム遷移**です。どちらの showcase アプリにも、今日この形で作られた画面は
     ありません。コードレビューではなく経験的にこのケースを確かめたいなら、使い捨ての
     `UIView.transition(with:duration:options:animations:)`(UIKit)や独自の `AnyTransition`(SwiftUI)を
     試作用の画面に一時的に足して観測し、確認後は変更を破棄してください。恒久的な showcase 画面を、
     一度限りの確認のために追加しないでください。

## 手順A — `bajutsu run` と、新設した2行のデバッグログによる確認

BE-0310 は、まさにこの確認のために `_logger.debug(...)` を2行足しました。新しい段が readiness を決めたとき、
`bajutsu.platform_lifecycle.readiness` はログを1行残します。その内容は
`"readiness satisfied by the screenChanged signal"` です。`settled` がこのシグナルを使ったとき、
`bajutsu.orchestrator.waits` も同様にログを1行残します。その内容は `"settled via the screen-transition
signal (quiescence=...)"` です。CLI には今のところ `--verbose` フラグがありません。`bajutsu run` を直接呼ぶ
代わりに、次のような1行ラッパーでログレベルを引き上げてください。

```bash
cd /path/to/bajutsu
uv run python -c "
import logging
logging.basicConfig(level=logging.DEBUG, format='%(name)s: %(message)s')
from bajutsu.cli import main
main()
" run --target showcase-swiftui --udid "$(xcrun simctl list devices booted | grep -oE '[0-9A-F-]{36}' | head -1)" \
  --backend ios --config demos/showcase/showcase.config.yaml \
  --scenario demos/showcase/scenarios/navigation.yaml demos/showcase/scenarios/modals.yaml demos/showcase/scenarios/tabs.yaml
```

`--target showcase-uikit` でも同様に実行してください。実行結果の出力から上記の2行を検索します。それが
現れていれば、その回はツリー差分のフォールバックではなく、シグナルが readiness / settled を決めたことが
確認できます。push・モーダル・タブ切り替えのように遷移が起きるはずの回でこの2行が**現れない**ことこそ、
この手順が捕まえたい失敗のシグナルです。

## 手順B — SwiftUI の XCUITest スキャフォールド

[`ios/swiftui/UITests/BE0310ScreenTransitionSignalUITests.swift`](ios/swiftui/UITests/BE0310ScreenTransitionSignalUITests.swift)
は、`bajutsu codegen` の生成物ではなく手書きしたスキャフォールドです。実際の Python 側コレクタの代わりに、
最小限のループバック HTTP リスナーを立てます。これにより、`BAJUTSU_COLLECTOR` から `POST /transitions` へ
届く実際の配線を確かめます。cold launch、手順Aと同じ push・モーダル・タブの流れ、画面内データ更新のケースを
一通り操作し、それぞれの前後で遷移の記録数を検証します。実行は次のとおりです。

```bash
cd demos/showcase/ios/swiftui && xcodegen generate
xcodebuild test -project BajutsuShowcaseSwiftUI.xcodeproj -scheme UITests \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
```

`make -C demos/showcase ui-test` も同じスキームを実行します。ただしその前に、`bajutsu codegen` で
`ComponentsUITests.swift` を再生成します。このファイルも同じ `UITests` ターゲットの `sources:` 配下にあるので、
両立させても問題ありません。

**UIKit には今日時点で `UITests` の Xcode ターゲットがありません。** `demos/showcase/ios/uikit/project.yml` には
これまで1つも足されていません。存在するのは、SwiftUI 側の `BajutsuShowcaseSwiftUIUITests`
（`project.yml` を参照）だけです。UIKit については、手順A（`bajutsu run` とデバッグログによる確認）が主となる
確認経路です。UIKit にも XCUITest ベースの確認を用意することは妥当な後続作業ですが、それをこの項目の作業として
当てずっぽうに足すのは範囲外とします。

## 結果の記録

実行し終えたら、
[BE-0310 の進捗チェックリスト](../../roadmaps/BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md#進捗)
を更新してください。作業単位5にチェックを入れ、他のチェックリスト項目と同じログの書式で1行を書き足します。
その1行には、実行した日付、どのツールキットで何が発火し、何が発火しなかったかを記録してください。

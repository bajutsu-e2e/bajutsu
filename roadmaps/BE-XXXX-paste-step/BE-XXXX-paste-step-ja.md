[English](BE-XXXX-paste-step.md) · **日本語**

# BE-XXXX — クリップボードの内容をフィールドに貼り付ける paste ステップ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-paste-step-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | [#2438](https://github.com/bajutsu-e2e/bajutsu/pull/2438) |
| トピック | シナリオ記述機能 |
| 関連 | [BE-0265](../BE-0265-text-editing-steps/BE-0265-text-editing-steps-ja.md)、[BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md)、[BE-0369](../BE-0369-ios-paste-consent-prompt-choice/BE-0369-ios-paste-consent-prompt-choice-ja.md)、[BE-0280](../BE-0280-conformance-unactuated-verbs/BE-0280-conformance-unactuated-verbs-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[シナリオ](../../docs/ja/glossary.md#scenario-authoring)はクリップボードに文字列を入れられますが、貼り付けることはできません。`setClipboard` はクリップボードに値を入れ、`copy` はフィールドの選択範囲をクリップボードにコピーします。どちらの結果も、アプリの外からクリップボードを読む `clipboard` アサーションでしか確かめられません。クリップボードの内容をフィールドに入れるステップはありません。そのため、`setClipboard` を導入した動機の流れを、いまも検証できません。ほかの場所でコピーしたクーポンコードやワンタイムコードを、アプリに貼り付ける操作です。

この項目では `paste` ステップを追加します。`paste` はフィールドにフォーカスし、カーソル位置にクリップボードの内容を入れます。選択範囲があれば置き換えます。`select`、`clear`、`delete`、`copy` からなるテキスト編集ステップの組を補い、iOS（XCUITest）、Android（adb）、web（Playwright）で動きます。

## 動機

貼り付けの流れは実際のアプリでよく使われますが、`type` では検証できません。文字列を打ち込むと、検証したいコードを通りません。対象はアプリ自身の貼り付け処理、貼り付け時の入力整形、クリップボードの読み取りです。「Paste」コントロールを `tap` する代替も使えません。編集メニューの「Paste」項目には ID がなく、ラベルはアプリの言語で変わります。

設計は、iOS 26.5 の Simulator で計測した iOS の挙動にも耐える必要があります。ハードウェアの Cmd+V は、同じアプリが書いたクリップボードなら貼り付けます。別のプロセスが書いたクリップボードは貼り付けず、エラーも iOS の貼り付け同意ダイアログも出しません。`setClipboard` は `simctl` 経由で書くので、iOS はこれを別プロセスの書き込みとして扱います。Cmd+V で作ったステップは `select` → `copy` → `paste` を通しますが、`setClipboard` → `paste` では何もせずに終わります。後者こそが、このステップの存在理由です。

この項目が入ると、シナリオは `setClipboard` でクリップボードに値を入れ、`paste` でフィールドに貼り付け、フィールドの `value` を確かめられます。iOS では、`actuation` レーンの `paste.yaml` が、アプリ内のコピーと `setClipboard` の値の両方でこれを示します。Android と web は、ユニットテストと適合性テストで確かめます。

## 詳細設計

### ステップの形と結果の確かめ方

`paste` は `select`、`clear`、`delete` と同じ形をとります。

```yaml
- setClipboard: { text: "COUPON123" }
- paste: { into: { id: form.coupon } }
- assert:
    - value: { sel: { id: form.coupon }, equals: "COUPON123" }
```

ハンドラーはドライバーの `paste(sel)` を呼びます。`paste(sel)` は `into` をタップしてフィールドにフォーカスし、それから貼り付けます。フォーカスのタップをドライバーが受け持つのは、iOS がそのタップより前に画面を読む必要があるためです（後述）。ハンドラーは、`tap` と同じスクロールによる回復でこの呼び出しを包みます。フォーカスのタップが失敗するのは貼り付けより前なので、呼び出し全体を再試行しても安全です。ステップは状態を持たず、結果は既存の `value` アサーションで確かめます。ステップが記録した内容を確かめに使うことはありません。`copy` と違い、`paste` は事前の `select` を必要としません。フォーカスのタップで置かれたカーソル位置に入れるからです。文字列のあるフィールドでは、タップした位置になります。`copy` 以外のアクションと同じく、`paste` は残っている選択を無効にします。選択範囲は貼り付けで置き換わるためです。

### ドライバーの契約と capability による門

`Driver.paste(sel)` はフィールドのセレクタを受け取り、フィールドへのフォーカスも自分で行います。`paste` は `select` や `copy` と同じ `textSelection` capability を共有します。全選択とコピーを実現できるバックエンドは貼り付けも実現できます。preflight は、このトークンを持たないバックエンドで3つのどれかを使うシナリオを拒否します。live Appium の経路と WebView コンテキストは、`copy` と同じく `UnsupportedAction` を送出します。

### バックエンドごとの実現方法

| バックエンド | 方法 |
|---|---|
| Android（adb） | `input keycombination` で Ctrl+V を送ります。Ctrl+A や Ctrl+C と同じ経路です。ドライバーは `delete_text` と同じツリーの追従を仕掛けるので、次の読み取りで貼り付け後の `value` が見えます。 |
| web（Playwright） | `ControlOrMeta+v` を送ります。macOS のホストでは Ctrl+V が何も貼り付けないため、Cmd+V になります。 |
| iOS（XCUITest） | 編集メニューの「Paste」項目を使います。詳細は下記のとおりです。 |

iOS では、ドライバーは何にも触れる前に、「Paste」ラベルを持つ画面上の要素を数えます。操作によって**新たに現れた**「Paste」項目だけを、編集メニューの項目とみなします。そのため、アプリ自身の「Paste」ボタンをメニューと取り違えることはありません。続いてドライバーはフィールドをタップします。すでにフォーカスのあるフィールドでは、そのタップだけで編集メニューが出ます。そこでさらに操作するとメニューが閉じてしまうので、ドライバーは新しい「Paste」項目を最大 1 秒探します。現れなければ、フィールドに 0.6 秒の長押しを加えます。2回目のタップではなく長押しを使うのは、UIKit のテキストフィールドが素早い2回のタップをダブルタップと解釈し、メニューを出さないためです。ドライバーは新しい項目を最大 5 秒待ち、それをタップします。次の3つの場合は、ステップをはっきり失敗させます。

- 新しい項目が現れない場合は `ElementNotFound` です（クリップボードが空の場合や、貼り付けを受け付けないフィールドの場合です）。
- 2つの言語の「Paste」項目が同時に現れた場合は `AmbiguousSelector` です。
- 新しい項目のラベルがアプリの要素と同じ場合は、タップの対象が2つに解決されて `AmbiguousSelector` になります。

編集メニューの「Paste」はシステムの貼り付けコントロールです。別のプロセスが書いたクリップボードも読み、貼り付け同意ダイアログは出ません。runner に新しいエンドポイントは不要です。ドライバーが既存のタップ、長押し、要素取得の呼び出しを組み合わせます。

### ローカライズされた「Paste」ラベル

メニュー項目には ID がなく、ラベルしかありません。UIKit はこのラベルを、端末の言語ではなくアプリのローカライズで表示します。日本語の Simulator でも、日本語の文字列を持たないアプリでは「Paste」と表示されます。BE-0320 と BE-0369 の仕組みは SpringBoard のラベルをシナリオのロケールで引くので、ここには当てはまりません。代わりにドライバーは、UIKit が持つすべての言語のラベルを受け付け、画面にちょうど1つあることを求めます。

ラベルの表は、UIKit 自身の `Localizable.strings` にあるキー `Paste` の値です。手で書き写したものではなく、iOS 26.5 ランタイムの `UIKitCore.framework` から読み取りました。[`misc/extract_paste_labels.py`](misc/extract_paste_labels.py) を使うと、ランタイムから表を作り直せます。

### コード生成

| 出力先 | 生成するコード |
|---|---|
| XCUITest | まずタップします。1 秒以内に `app.staticTexts["Paste"]` が現れなければ、0.6 秒の `press(forDuration:)` による長押しを加えます。最後に `app.staticTexts["Paste"]` をタップします。ラベルは英語なので、ほかの言語にローカライズしたアプリでは書き換えが必要です。 |
| UI Automator | クリックしたあと `pressKeyCode(KEYCODE_V, META_CTRL_ON)` を送ります。 |
| Playwright | `focus()` のあと `keyboard.press('ControlOrMeta+v')` を送ります。 |

### 実機での検証

`demos/showcase/scenarios/paste.yaml` には、Search フィールドを使う3つのシナリオがあります。1つ目は、アプリ内の `copy` で取った文字列を、空にしたフィールドへ貼り戻します。2つ目は、`setClipboard` で入れた値を空のフィールドに貼り付けます。3つ目は、文字列とフォーカスのあるフィールドのカーソル位置に貼り付けます。3つとも、iOS 26.5 の Simulator 上の SwiftUI アプリと UIKit アプリで通りました。

このファイルは `actuation` の CI レーンでだけ、`text_editing.yaml` のあとに実行します。`pasteboard` タグにより、ローカルの一括実行からは外れます。理由は、この項目を作る途中で見つけた別の Simulator の挙動です。`setClipboard` のあとは、デバイスを消去するまで、それ以降のすべてのシナリオでアプリ内の全選択とコピー（⌘A / ⌘C）が効かなくなります。一括実行はファイルを名前順に見つけるので、`paste.yaml` を `text_editing.yaml` より先に実行してしまいます。ファイル内でアプリ内コピーのシナリオを先頭に置いているのも、同じ理由です。

## 検討した代替案

- **iOS でハードウェアの Cmd+V を使う案。** もっとも単純な方法で、アプリ内のコピーなら貼り付けられます。しかし「動機」で述べたとおり、`setClipboard` の値は何も起きずに貼り付けられないため、採用しませんでした。以前 `type` を Cmd+V 経由にした実験でも、iOS の貼り付け同意ダイアログが runner を止めました。
- **BE-0369 が SpringBoard のダイアログに使う、ロケールで引くラベル表を使う案。** この表はシナリオのロケールで引きます。一方、編集メニューはアプリのローカライズに従います。そのため日本語のシナリオでは表が「ペースト」を返し、メニューには「Paste」が出る、という食い違いが起きます。UIKit のすべてのラベルを受け付ければ、アプリがどのローカライズになっても一致します。
- **2回目のタップで編集メニューを出す案。** SwiftUI アプリでは動きましたが、UIKit アプリでは2回のタップがダブルタップと解釈され、失敗しました。
- **画面上のどの「Paste」ラベルもメニューとみなす案。** 初期の下書きはこの方式で、セルフレビューで欠陥が見つかりました。アプリ自身の「Paste」ボタンをタップしてしまい、何も貼り付けずにステップが成功します。最初のタッチの前にラベルを数えることで、この推測をなくしました。
- **runner 側に `/paste` エンドポイントを設ける案。** ドライバーは既存のタップ、長押し、要素取得の呼び出しを組み合わせれば足ります。新しいエンドポイントは、仕組みの信頼性を上げないまま契約の範囲だけを広げます。

## 進捗

- [x] `paste` ステップのモデル、`Step` のフィールド、ハンドラー、`textSelection` による preflight の門
- [x] すべてのバックエンドの `Driver.paste(sel)`。Android は Ctrl+V、web は `ControlOrMeta+v`、iOS は自分の操作で現れた編集メニューの「Paste」項目。live Appium の経路と WebView コンテキストは送出
- [x] UIKit の「Paste」ラベル表と、その抽出スクリプト
- [x] XCUITest、UI Automator、Playwright のコード生成
- [x] テスト。ハンドラー、preflight、ドライバーごと、コード生成、ドライバー適合性ケースの貼り付け部分
- [x] `actuation` の iOS レーンへの `paste.yaml` の追加。iOS 26.5 の Simulator（SwiftUI アプリと UIKit アプリ）で検証
- [x] 英語と日本語のドキュメント

## 参考

- [BE-0265 — テキスト編集ステップ](../BE-0265-text-editing-steps/BE-0265-text-editing-steps-ja.md)：`select`、`clear`、`delete`、`copy`。このステップは形と capability をこれらと共有します。
- [BE-0052 — デバイスの状態](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md)：`setClipboard` と `clipboard` アサーション。
- [BE-0369 — 貼り付け同意ダイアログ](../BE-0369-ios-paste-consent-prompt-choice/BE-0369-ios-paste-consent-prompt-choice-ja.md)：編集メニューの経路が避ける iOS のダイアログ。
- [`docs/ja/scenarios.md`](../../docs/ja/scenarios.md)：ステップのリファレンス。

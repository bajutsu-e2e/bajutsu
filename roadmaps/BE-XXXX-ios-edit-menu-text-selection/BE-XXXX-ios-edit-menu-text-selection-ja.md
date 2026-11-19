[English](BE-XXXX-ios-edit-menu-text-selection.md) · **日本語**

# BE-XXXX — iOS の select と copy をシステムの編集メニューで操作する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-ios-edit-menu-text-selection-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0265](../BE-0265-text-editing-steps/BE-0265-text-editing-steps-ja.md)、[BE-0489](../BE-0489-paste-step/BE-0489-paste-step-ja.md)、[BE-0052](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md)、[BE-0381](../BE-0381-runner-openapi-contract/BE-0381-runner-openapi-contract-ja.md) |
<!-- /BE-METADATA -->

## はじめに

iOS Simulator では、テスト対象のアプリ以外のプロセスが pasteboard に一度でも書き込むと、その端末で `select` と `copy` の各ステップが効かなくなります。この状態は、端末を erase するか再起動するまで続きます。本項目は、2 つのステップの操作をハードウェアのショートカット（⌘A と ⌘C）から、システムの編集メニューにある「Select All」と「Copy」の項目に移します。編集メニューは、この状態の端末でも動きます。iOS の `paste` ステップはすでに同じメニューを操作しているので、テキスト選択に関わる 3 つのステップが 1 つの仕組みにそろいます。ドライバがメニューを開く操作の対象フィールドを特定できるよう、`paste(sel)` と同じく `Driver.select_all()` がフィールドの selector を受け取るように広げます。この変更はすべてのドライバに及びます。

## 動機

現在の XCUITest Runner は、`select` を `app.typeKey("a", modifierFlags: .command)` で実装しています。`copy` も、`"c"` を渡す同じ呼び出しです（`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`）。issue [#2439](https://github.com/bajutsu-e2e/bajutsu/issues/2439) は、`setClipboard` ステップ（`simctl pbcopy`）を実行すると、その Simulator でこの 2 つのショートカットが効かなくなると報告しました。書き込みのあと、フィールドには選択のハイライトが出ず、`clipboard` アサーションは書き込んだ文字列を読み続けます。

iOS 26.5 Simulator 上での調査によって、原因の範囲が絞れました。きっかけは、前面のアプリ以外のプロセスによる pasteboard への書き込みです。

- シナリオを挟まず、`simctl pbcopy` を単体で実行するだけで壊れます。
- XCUITest Runner 自身のプロセスの中から `UIPasteboard` に書き込んでも、同じように壊れます。
- アプリ自身の ⌘C による書き込みでは壊れません。1 台の端末で `text_editing.yaml` を何度実行しても成功します。

壊れた状態は SpringBoard のメモリ上にあり、pasteboard の中身や pasteboard 系のデーモンにはありません。

| 試した復旧方法 | その後の `text_editing.yaml` |
|---|---|
| pasteboard を読み戻す（`simctl pbpaste`） | 失敗します |
| 空文字の `pbcopy` で pasteboard を消す | 失敗します |
| `pasted`、`CoreSimulatorBridge`、`kbd`、`testmanagerd` を再起動する | 失敗します |
| SpringBoard を再起動する、または端末を再起動する | 成功します |

きっかけはまだ完全には特定できていません。`device.yaml` は `setClipboard` で書き込んだ直後に `clipboard` アサーションで読み戻しており、同じ端末でその後に流す `text_editing.yaml` は成功します。壊れたあとの読み戻しでは直らないので、直後の読み戻しが、そもそも壊れるのを防いでいるように見えます。ただし、この文書化されていないタイミングにシナリオが頼るわけにはいきません。読み戻さずに書き込むだけのシナリオは、実際に端末を壊します。

Bajutsu は iOS を直せませんが、ショートカットに頼るのをやめることはできます。同じ調査で、Runner に一時的なパッチを当て、編集メニューの「Select All」と「Copy」をタップさせました。すでに壊れた状態の端末で、このパッチ版は `text_editing.yaml` に 2 回とも成功しました。パッチを当てていない Runner は、同じ端末で失敗しています。

ショートカットを残すと、表に出ない並び順の規則が iOS のすべてのレーンで残ります。シナリオファイルを名前順に 1 台の端末で流すレーンでは、`setClipboard` を使うシナリオが `select` を使うシナリオより前に並んだ時点で壊れます。現在の `paste.yaml`（BE-0489）は、この規則を 2 重に回避しています。`pasteboard` タグで一括実行から外し、さらに CI の `actuation` レーンでは意図して `text_editing.yaml` の後に実行しています。次に `setClipboard` を使うシナリオを足す作成者は、この 2 つの回避策を知っておく必要があります。

本項目の実装後は、`setClipboard` を使うシナリオを先に実行した端末でも `text_editing.yaml` が成功します。`actuation` レーンは、書き込み直後に読み戻さない `paste.yaml` を `text_editing.yaml` より前に実行し、このことを直接確かめます。`pasteboard` タグと並び順の注記は、showcase の Makefile から消えます。

## 詳細設計

### Driver API の変更

現在の `Driver.select_all()` は引数を取りません（`bajutsu/common/drivers/base/driver.py`）。`Element` もキーボードフォーカスの有無を持たないので、XCUITest ドライバは `query()` だけではタップや長押しの対象フィールドを特定できません。本項目は、このメソッドを `paste(sel)` と同じ形の `select_all(sel)` に変えます。変更は、このメソッドを宣言または呼び出すすべての箇所に及びます。

- `Driver` プロトコルと、トレース用のラッパー（`bajutsu/common/drivers/tracing.py`）
- 6 つのドライバ：XCUITest、live XCUITest、adb、Playwright、WebView コンテキスト、フェイク
- `bajutsu/common/orchestrator/actions/handlers/gestures.py` の `select` と `clear` のハンドラ（自前のフォーカス用タップをやめ、ステップの selector を渡します）

ハンドラは、先にフィールドをタップするのをやめます。`_do_paste` が `paste` を呼ぶのと同じく、`_tap_with_recovery(lambda: driver.select_all(sel), driver, sel)` を呼びます。`paste(sel)` と同じく、`select_all(sel)` がフィールドへのフォーカスを自分で合わせます。iOS ではフォーカスのあるフィールドをタップすると編集メニューが開くので、ハンドラが別にタップすると、ドライバがラベルを数える前に「Select All」項目が画面に出てしまい、ドライバは新しく増えた項目を見つけられません。XCUITest 以外のドライバは、フィールドをタップしてから今までどおりに操作します。`clear` のハンドラは、最初にフィールドの現在の値を読むところを残します。フォーカス用のタップを `select_all(sel)` に任せるのは、`TEXT_SELECTION` を持つバックエンドの場合だけです。空のフィールドと、この機能を持たないバックエンドでは、文字数ぶんのバックスペースがフォーカスのあるフィールドに効くので、ハンドラが自分でタップします。`copy_selection()` は、`select` が残した選択範囲に対して働くので、形を変えません。

### XCUITest ドライバでの操作

Python の XCUITest ドライバ（`bajutsu/common/drivers/xcuitest/xcuitest_driver.py`）は、2 つの操作を既存の基本操作（`tap`、`long_press`、`query`）の組み合わせで実現します。同じドライバの `paste` メソッドと同じ作り方で、Runner のエンドポイントは使いません。

- **`select_all`** は、画面上の「Select All」ラベルの数を数えてから、そのステップで最初の操作として、`sel` が指すフィールドをタップします。すでにフォーカスのあるフィールドをタップすると、編集メニューが開きます。1 秒以内に新しい「Select All」項目が現れない場合は、代わりにフィールドを長押しします。2 回目のタップは、ダブルタップとして扱われるからです。そのうえで、要素が増えた「Select All」ラベルをタップします。
- **`copy_selection`** は、select-all の直後から画面上で開いている「Copy」項目をタップします。「Select All」を押すと、iOS はメニューを「Cut」「Copy」「Paste」を並べたものに切り替えるので、追加の操作は要りません。`select_all` は、自分の操作の前の時点での「Copy」ラベルの数を保持します。`copy_selection` は、その時点から要素が増えた「Copy」ラベルだけを対象にします。

操作の前にラベルを数えておくことで、アプリ内の要素を取り違えずに済みます。アプリに「Copy」というボタンがあっても、そのボタンは操作の前から画面にあるので、メニューの項目には数えません。アプリの要素とメニューの項目が同じラベルを持つときは、最後のタップの対象が一意に決まらず、ステップは失敗します（プライムディレクティブ 2）。メニューが現れない場合、ステップは `ElementNotFound` を送出し、待っていた項目の名前をメッセージに含めます。

`clear` ステップは、`select_all` のあとにバックスペースを 1 回送ります（`bajutsu/common/orchestrator/actions/handlers/gestures.py`）。ハンドラはフォーカス用のタップを `select_all(sel)` に任せるようになりますが、バックスペースは編集メニューが作った選択範囲をそのまま消すので、それ以外の手順は変わりません。残った文字を文字数ぶん消し直す `clear` のフォールバックも、そのまま残します。

### ローカライズされたメニューのラベル

メニューの項目は識別子を持たず、ラベルしか持ちません。UIKit はこのラベルを、端末の言語ではなくアプリのローカライズで表示します。BE-0489 が「Paste」について確かめたとおりです。そのため、ドライバはシナリオのロケールからラベルを引けません。UIKit が収録しているすべての言語のラベルを受け付け、そのうち要素の増えたラベルがちょうど 1 つであることを求めます。

`bajutsu/common/drivers/xcuitest/paste_menu.py` は、「Paste」「Select All」「Copy」の 3 つの表を持つモジュールに広げます。各表は、iOS 26.5 ランタイムの `UIKitCore.framework` から読み出した、そのキーに対する UIKit 自身の `Localizable.strings` の値を持ちます。BE-0489 の抽出スクリプト（`roadmaps/BE-0489-paste-step/misc/extract_paste_labels.py`）に 2 つのキーを加え、1 回の実行で 3 つの表をすべて作り直せるようにします。

### Runner と契約

ドライバ自身がメニューを操作するようになると、`/selectAll` と `/copy` のエンドポイントを呼ぶ箇所はなくなります。本項目は、この 2 つを次の箇所から削除します。

- Runner の OpenAPI 契約（`BajutsuKit/Sources/BajutsuRunner/openapi.yaml`）
- `ElementProviding` プロトコル、ルーター、API ハンドラ
- XCUITest の element provider と、Swift のテスト用フェイク

エンドポイントを残すと、今後の呼び出し元からショートカットに届く道が残ります。本項目は、その道もあわせて閉じます。

### コード生成

生成する XCUITest のコードは、`paste` ですでにそうしているように、ドライバの操作に合わせます（`bajutsu/codegen/xcuitest.py`）。

| ステップ | 生成するコード |
|---|---|
| `select` | フィールドをタップします。1 秒以内に `app.staticTexts["Select All"]` が現れなければ、0.6 秒の `press(forDuration:)` を続けます。そのあと項目の出現を待ってタップします。 |
| `copy` | `app.staticTexts["Copy"]` の出現を待ってタップします。 |
| `clear` | 上の `select` の手順のあと、`XCUIKeyboardKey.delete` を 1 回送ります。 |

生成する `paste` と同じく、これらのラベルは英語です。別の言語にローカライズしたアプリでは、書き出したテストに独自のラベルが必要になります。

### レーンとドキュメント

- `.github/workflows/ios-e2e.yml` の `actuation` レーンで、`paste.yaml` を `text_editing.yaml` より前に移します。これにより、このレーンは実行のたびに回帰を確かめます。
- `demos/showcase/Makefile` のすべての `--exclude` から `pasteboard` を外し、その説明コメントも削除します。`paste.yaml` からもタグと、冒頭コメントの並び順の説明を外します。`ios-e2e.yml` の `actuation` ジョブのコメントのうち、`select` と `copy` を ⌘A / ⌘C と説明している箇所と、以前の並び順の理由を述べている箇所も、あわせて書き直します。`paste.yaml` と `text_editing.yaml` には `textselection` タグを付け、`run-flutter` がそれを除外します（理由は後述の「範囲」の節にあります）。
- `docs/codegen.md` と `docs/ja/` のミラーに、`select`、`copy`、`clear` で生成するコードの新しい形を書きます。
- `docs/architecture.md` とそのミラーがショートカットに言及していれば、テキスト選択のステップの iOS での操作方法を書き直します。

### 範囲

本項目が変えるのは、ローカルの XCUITest の経路だけです。live Appium / WebDriver の経路（BE-0238）は、`select`、`copy`、`paste` に対して引き続き `UnsupportedAction` を送出します。これらのステップをドライバ側の組み合わせで実現するようになると、その経路にも同じ操作を載せられる見込みが立ちますが、本項目では配線しません。

Flutter の showcase も範囲外です。`run-flutter` ターゲットは XCUITest バックエンドで Flutter のアプリを操作しますが、Flutter は UIKit の編集メニューではなく、独自のテキスト選択ツールバーを描きます。フォーカスのある Flutter のフィールドをタップしてそのツールバーが開くか、どのラベルを持つかは、まだ誰も確かめていません。そのため `run-flutter` では、`paste.yaml` と `text_editing.yaml` を実行から外したままにします。2 つのファイルに `textselection` タグを付け、`run-flutter` だけがこのタグを除外します。SwiftUI と UIKit の一括実行には、どちらのファイルも含めます。

## 検討した代替案

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| Runner の中から pasteboard に書き込む | `setClipboard` の `simctl pbcopy` を、Runner のプロセスでの `UIPasteboard` への書き込みに置き換えます | 調査で、Runner のプロセスからの書き込みでも同じようにショートカットが壊れるとわかりました。きっかけは `simctl` そのものではなく、前面のアプリ以外の書き込み元です。 |
| `setClipboard` のたびに SpringBoard を再起動する | SpringBoard を再起動し、壊れていない状態に戻します | SpringBoard はホーム画面とアプリスイッチャーを担っているので、再起動はシナリオの途中でテスト対象のアプリを乱します。端末が落ち着くまでの待ち時間も数秒増えます。 |
| まず ⌘A を試し、だめならメニューに切り替える | 速いショートカットを残し、失敗したときだけメニューを使います | 選択範囲を読めるバックエンドはないので、ドライバは ⌘A が効いたかを判定できません。推測で切り替えると、ステップの操作が端末の履歴に左右されます（プライムディレクティブ 2）。 |
| Runner の中で実行時にラベルを引く | Runner のプロセスから、適切な言語の「Select All」「Copy」を UIKit に問い合わせます | この問い合わせは Runner 自身のローカライズで決まり、メニューはアプリのローカライズに従うので、両者が食い違うことがあります。BE-0489 の表とラベルの扱いが 2 系統に分かれることにもなります。 |
| ショートカットを残し、並び順の規則を文書にする | 操作は変えず、`pasteboard` タグと並び順を既知の制限として説明します | `setClipboard` を使うシナリオを足すたびに、表に出ない並び順の規則を引き継ぐことになり、回帰も検証されないまま残ります。 |

iOS 26.5 Simulator では、編集メニューを使うと `select` や `copy` 1 回につき約 1.6 秒かかります。ショートカットなら、ほとんど時間はかかりません。ただしこの費用はステップ単位でかかり、テキストを選択またはコピーするシナリオだけが負担します。決定的に動くステップの価値のほうが、その費用を上回ると判断しました。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] Driver API：フィールドへのフォーカスを自分で合わせる `select_all(sel)`（プロトコル、トレース用ラッパー、すべてのドライバ、`select` と `clear` のハンドラ）
- [ ] 「Select All」と「Copy」のラベル表と、それを作り直す抽出スクリプトの拡張
- [ ] 編集メニューを使う XCUITest ドライバの `select_all` と `copy_selection`、およびユニットテスト
- [ ] Runner：契約、プロトコル、ルーター、ハンドラ、provider、フェイクからの `/selectAll` と `/copy` の削除
- [ ] `select`、`copy`、`clear` の XCUITest コード生成とテスト
- [ ] `actuation` レーンの順序、`pasteboard` タグと除外リストの整理、`run-flutter` での `textselection` タグ、iOS 26.5 Simulator での確認（SwiftUI と UIKit のアプリ）
- [ ] 英語と日本語のドキュメント

## 参考

- issue [#2439](https://github.com/bajutsu-e2e/bajutsu/issues/2439)：`setClipboard` のあとに ⌘A と ⌘C が効かなくなるという報告です。
- [BE-0265 — テキスト編集ステップ](../BE-0265-text-editing-steps/BE-0265-text-editing-steps-ja.md)：`select`、`clear`、`delete`、`copy` を定めた項目で、本項目はその iOS での操作を変えます。
- [BE-0489 — paste ステップ](../BE-0489-paste-step/BE-0489-paste-step-ja.md)：編集メニューによる paste と、本項目が広げるラベル表を定めた項目です。
- [BE-0052 — 端末状態](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md)：`setClipboard` と `clipboard` アサーションを定めた項目です。
- [BE-0381 — Runner の OpenAPI 契約](../BE-0381-runner-openapi-contract/BE-0381-runner-openapi-contract-ja.md)：本項目が 2 つのエンドポイントを削除する契約です。

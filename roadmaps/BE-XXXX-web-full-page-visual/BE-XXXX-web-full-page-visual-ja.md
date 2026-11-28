[English](BE-XXXX-web-full-page-visual.md) · **日本語**

# BE-XXXX — web のページ全体を 1 回の visual assertion で比べる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-web-full-page-visual-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Verification & coverage |
<!-- /BE-METADATA -->

## はじめに

この項目は、web バックエンドの `visual` assertion に `fullPage` オプションを足します。`fullPage: true`
を指定すると、スクロールできるページ全体を 1 枚の画像として baseline と比べます。画像には、最初の画面より
下の部分も入ります。

比較は、これまでと同じく決定的なピクセル差分で、モデルは介在しません。このオプションは web の
capability です。ほかのバックエンドで使うと、実行の前の preflight で止まります。

## 動機

`visual` assertion（BE-0029）は、撮影したスクリーンショットを保存済みの baseline と比べます。web
バックエンドで撮るのは viewport の範囲です。スクロールしないと見えない部分は、比較に入りません。
Playwright の `full_page` 撮影を使う箇所も、現状のリポジトリにはありません。

困るのは縦に長いページです。設定画面のフォーム、レポート、ランディングページでは、回帰の多くが最初の
画面より下で起こります。フッターが崩れても、assertion は通ってしまいます。

画面ごとにスクロールして 1 画面ずつ assertion を置く回避策もありますが、安定せず手間もかかります。
固定ヘッダーが各画像に重なります。スクロール量が 2 ピクセルずれると、画像全体がずれます。画面ごとに
baseline が要り、確認と承認の手間も画面の数だけ増えます。

ページを包む要素を `element` で指定しても解決しません。要素単位の比較（BE-0171）は、viewport の
スクリーンショットから切り出します。viewport からはみ出た部分は、画像に入りません。

ページ全体の画像を 1 枚撮れば、この 3 つの問題は消えます。通らなかった実行の証跡もよくなります。actual
画像と diff にページ全体が写ります。読み手は画面をめくらずに、どこで崩れたかを確かめられます。

出荷後は、viewport より高いページの最下部でフッターが変わると、`fullPage: true` の `visual` assertion
が失敗します。実行の証跡には、ページ全体の actual 画像とその diff が残ります。同じ変更は、今の viewport
の assertion では通ってしまいます。この項目が埋めるのは、その差です。

## 詳細設計

### scenario での書き方

`fullPage` は `visual` の任意の真偽値で、デフォルトは false です。名前は、`colorTolerance` のような既存の
camelCase のオプションに合わせます。

```yaml
- assert:
    - visual:
        baseline: settings-page.png
        fullPage: true
        compare: pixelmatch
        threshold: 0.2
        exclude: [ { selector: { id: last-updated-at } } ]
```

`fullPage` は `element`、`compare`、`threshold`、`exclude` と組み合わせられます。ページ全体の
baseline は、viewport の baseline とは別の画像です。一方のモードで記録した baseline は、もう一方の
モードとは一致しません。既存のサイズ不一致の検査がその場合を報告します。作者は、通常の approve の流れで
新しい baseline を記録します。

### 撮影

web ドライバは、Playwright の `page.screenshot(full_page=True)` でページを撮影します。この能力には、
新しい capability の名前 `fullPageScreenshot` を付けます。web ドライバだけがこの名前を宣言します。
`selectOption` や `attribute` を web に限っているのと同じ形です。

preflight（BE-0082）は、`fullPage: true` の `visual` assertion をこの capability に対応づけます。iOS
や Android に向けた scenario は、実行の開始前に止まります。メッセージには、該当する構文と、対応している
バックエンドを示します。

コアは、任意で実装する小さなプロトコルを通してこの撮影を呼びます。`ViewportProvider`
（`bajutsu/common/drivers/base/viewport_provider.py`）と同じ形です。`Driver.screenshot` のシグネチャは
変えません。このプロトコルを持たないドライバは、撮影の時点で例外を出します。この例外は、preflight の
後ろに置く安全網です。

`VisualContext.capture_actual` は、scenario の `visual` assertion が比べるスクリーンショットを撮ります。
1 つの scenario に、viewport の assertion とページ全体の assertion が同居することがあります。そこで
撮影はモードごとに 1 回ずつ行い、各 assertion は自分のモードの画像を読みます。

撮影した画像は縮小しません。縮小すると、`pixelmatch` の比べる画素が変わります。比較する画像は、
ブラウザの device pixel ratio のままにします。

### `exclude` と `element` の座標

要素の frame は `getBoundingClientRect` から得るので、viewport を基準にした座標です。ページ全体の画像
では、frame をスクロール量だけずらす必要があります。撮影では、frame と同時にスクロール量も読みます。
矩形を画像のピクセルへ換算する前に、frame をこの量だけずらします。

現在の倍率は、画像の高さを viewport の高さで割って求めます。ページ全体の画像は viewport より高いので、
この比は誤りになります。画像の幅を viewport の幅で割る方法でも、横にはみ出すページで同じ誤りが起きます。
ページ全体の画像では、撮影時に frame と同時に `window.devicePixelRatio` を読みます。その値を、縦横共通の
倍率にします。

web の問い合わせは、viewport の外にある要素もすでに返します。`query()` は、画面外の Document Object Model (DOM) ノードも
viewport 基準の frame のまま要素ツリーに残すからです。`playwright_driver.py` の `viewport()` のコメントが、
この扱いを記録しています（BE-0326）。最初の画面より下の要素もセレクタで解決でき、新しい問い合わせは
要りません。

### 範囲の境界

**遅延読み込みと無限スクロール**：撮影の側では、遅延読み込みの画像を表示させるためのスクロールはしません。
無限スクロールのページは対象外です。作者は assertion の前に、明示的な待機でページを完成させます。
`wait` の `until: settled` か、コンテンツの条件を使います。作者の代わりにスクロールすると、隠れた、
タイミングに依存するステップが増えます。prime directive 2 は、そのステップを許しません。

**固定要素と sticky 要素**：`position: fixed` の要素は、ページをスクロールしても frame が動きません。
sticky の要素も、貼り付いている間は同じです。こうした frame にスクロール量を足すと、矩形が違う画素に
当たることがあります。ページ全体の撮影でこうした要素をどこに描くかは、エンジンが決めます。そこで、
対応づけは推測ではなく測定に基づかせます。

**エンジンごとの測定**：実装では、web ドライバが起動できる各エンジン（Chromium、Firefox、WebKit）で
描かれる位置を測ります。固定ヘッダー、固定フッター、sticky の見出しを既知の位置に置いた固定のページを
用意します。テストはこのページ全体を撮り、各要素がどこに写ったかを確かめます。測った結果から、エンジンごとに
1 つの決まった規則を作り、コードに置きます。規則の例は「スクロール量を足さず frame の位置に描く」です。

**撮影時の対応づけ**：ドライバは frame と同時に、セレクタで解決した各要素の計算済みの `position`
を読みます。sticky の要素については、同じ読み取りで貼り付いているかどうかも記録します。固定要素と
貼り付いた sticky 要素には、スクロール量のずらしの代わりに、そのエンジンで測った規則を当てます。
対応づけは、実行ごとに変わりません。

**測定の維持**：測定のテストはテストスイートに残します。Playwright の更新でエンジンの描き方が変われば、
scenario より先にこのテストが失敗します。測定で安定した位置が 1 つに決まらないエンジンには、厳しい規則を
残します。そのエンジンでは、セレクタがこうした要素に解決されると、要素を名指しするメッセージで
assertion が止まります。`{ x, y, w, h }` の矩形なら、どのエンジンでもマスクに使えます。

**極端に縦長のページ**：ブラウザは、1 回の撮影の大きさに上限を持ちます。上限を超えるページでは、assertion
は明確なメッセージで失敗します。切り詰められた画像は比べません。切り詰めは、ブラウザの例外を待たず
に検出します。画像の縦横どちらかが、文書のスクロール寸法に device pixel ratio を掛けて切り捨てた値に届かなければ
失敗とします。比が小数でも、丸めの差を切り詰めと取り違えません。どちらの
値も、撮影時に frame と同時に読みます。上限の値は、実装で測ります。

**証跡のポリシー**：ステップごとの証跡のスクリーンショットは、viewport の大きさのままです。ページ全体の
選択肢を足すとファイルが大きくなりますし、今それを必要とする scenario はありません。assertion 自身の
actual 画像と diff は、もともとページ全体を写します。

**ほかのバックエンド**：ネイティブアプリには、この意味でのスクロールできるページがありません。iOS と
Android では、`fullPage` を使えません。

### codegen

Playwright の codegen は、現状ではすべての `visual` assertion に対して `TODO` コメントを出します。
`fullPage` も同じ経路に入ることを、実装で確認します。codegen が代わりに viewport の検査を出力することは
ありません。

### 作業分解（MECE）

| 単位 | 内容 |
|---|---|
| スキーマと preflight | `VisualMatch` に `fullPage` を足します。capability の preflight で `fullPageScreenshot` に対応づけます。 |
| 撮影 | ページ全体の撮影プロトコルを足し、web ドライバで実装します。`capture_actual` はモードごとに 1 枚を撮ります。 |
| 座標の対応づけ | frame をスクロール量だけずらし、device pixel ratio で換算します。固定要素と sticky 要素の描かれる位置をエンジンごとに測り、その規則で対応づけます。 |
| テスト | viewport より高いページと、最初の画面より下の `exclude` セレクタを扱います。viewport より高い `element` も扱います。 |
| テスト（続き） | 撮影の上限を超えるページ、各エンジンでの固定要素の配置、iOS と Android での preflight の拒否を扱います。どれも、修正を戻すと通らなくなる形にします。 |
| ドキュメント | scenario のリファレンスと `docs/architecture.md` を、`docs/ja/` のミラーとあわせて更新します。 |

## 検討した代替案

| 代替案 | 採らない理由 |
|---|---|
| 画面ごとにスクロールして 1 画面ずつ assertion を置く | 固定ヘッダーとスクロール量の誤差で各画像が不安定になり、baseline も増えます。 |
| ページを包む要素を `element` で指定する | 比較は viewport のスクリーンショットから切り出すので、はみ出た部分が画像にありません。 |
| 要素の撮影を Playwright の要素スクリーンショットに切り替える | 1 つの要素のはみ出しは直りますが、ページ全体は撮れません。要素単位の baseline の作り方も、すべて変わります。viewport より高い要素は、`fullPage: true` と `element` の併用で扱えます。 |
| ステップごとの証跡にもページ全体のモードを足す | ファイルが大きくなり、assertion はこのモードを必要としません。要望が出れば、あとの項目で足せます。 |
| `fullPage` モードでは、固定要素と貼り付いた sticky 要素をすべて失敗にする | 作者がマスクしたい時刻は、固定ヘッダーにあることが多いです。厳しい規則にすると、作者はピクセルの矩形に戻るしかありません。安定した位置が決まらないエンジンでは、この規則を代替として残します。 |
| 自動でスクロールして、遅延読み込みのコンテンツを表示させる | そのスクロールは、隠れた、タイミングに依存するステップになります。 |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] スキーマと preflight
- [ ] 撮影
- [ ] 座標の対応づけ
- [ ] テスト
- [ ] ドキュメント

## 参考

- [BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions-ja.md)：
  この項目が拡張する `visual` assertion
- [BE-0165](../BE-0165-visual-compare-engines/BE-0165-visual-compare-engines-ja.md)：ページ全体の
  画像に対して動く比較エンジン
- [BE-0171](../BE-0171-element-scoped-visual-assertions/BE-0171-element-scoped-visual-assertions-ja.md)：
  この項目が座標を対応づける、要素の範囲指定とセレクタのマスク
- [BE-0441](../BE-0441-step-level-visual-assertions/BE-0441-step-level-visual-assertions-ja.md)：
  撮影の経路を共有する、ステップ単位の `visual` assertion

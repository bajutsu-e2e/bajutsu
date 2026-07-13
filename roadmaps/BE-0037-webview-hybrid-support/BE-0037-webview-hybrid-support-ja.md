[English](BE-0037-webview-hybrid-support.md) · **日本語**

# BE-0037 — WebView / ハイブリッド対応

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0037](BE-0037-webview-hybrid-support-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0037") |
| 実装 PR | [#400](https://github.com/bajutsu-e2e/bajutsu/pull/400), [#401](https://github.com/bajutsu-e2e/bajutsu/pull/401) |
| トピック | プラットフォーム対応 |
| 由来 | MagicPod |
<!-- /BE-METADATA -->

## はじめに

現状の実装はネイティブ a11y ツリーを前提としています。本提案では、WebView 内の DOM（Document Object Model）へのアクセスをサポートします。

## 動機

本番の iOS アプリの多くはハイブリッドです。ネイティブのシェルが画面全体に `WKWebView` を埋め込みます（チェックアウト、ヘルプ、組み込みの Web アプリ、OAuth の同意ページなど）。bajutsu のセレクタはネイティブのアクセシビリティ（a11y）ツリーに対して解決しますが、WebView はその Web コンテンツ全体を、a11y ツリー上の小さく不透明な領域へ畳み込んでしまいます。内側の HTML のボタン、フィールド、リンクは、ネイティブ要素としてアドレス指定できません。結果として、WebView に入るフローはそもそも操作も検証もできません。タップすべき安定した `id` がなく、待つべき要素もなく、曖昧または空の一致は（正しく）ステップを失敗させます。Web で実装されたステップを持つアプリは、WebView の境界より先が事実上自動化不能になります。WebView 内の DOM（Document Object Model）に到達することが、こうしたフローをテスト可能にします。

## 詳細設計

ここで解くべき課題は、DOM とネイティブの a11y ツリーが、異なる 2 つのアドレス指定方式を持つ別々のツリーであり、セレクタは対象とする側のツリーで一意に解決しなければならない、という点です。本提案は、ネイティブ要素には既存のセレクタ文法を保ったまま、WebView の DOM のために明示的な*Web コンテキスト*を追加します。両者を黙って混ぜることはしません。

- **コンテキスト境界。** ステップは、ホストとなる WebView へスコープすることで Web コンテキストを選択します（例：`web: { within: { id: checkout.webview } }`）。そのブロック内のセレクタは DOM をアドレス指定します。ブロックの外では、セレクタは今日とまったく同じくネイティブに解決します。境界を明示することで、各セレクタがどちらのツリーを問い合わせるかが常に曖昧でなくなります。推測はしません。
- **DOM セレクタ。** Web コンテキスト内では、セレクタは CSS セレクタで（および安定している場合は要素のアクセシブルネームで）DOM ノードをアドレス指定します。これは `id` の Web 版に相当します。曖昧性のルールは変わりません。0 個または 2 個以上の DOM ノードに一致するセレクタは、最初に一致したものを操作するのではなく、ステップを失敗させます。
- **バックエンドへのマッピング。** DOM の操作には idb が提供しない機能、すなわち `WKWebView.evaluateJavaScript` による DOM ノードの問い合わせと操作が必要です。主要な経路は、その JavaScript をテスト対象アプリ内の BajutsuKit を通じて実行し、XCUITest backend（BE-0019）が使うのと同じ常駐ループバックチャネルに載せます。`network.py` が `127.0.0.1` にバインドし、起動 env がそのアドレスをアプリへ注入して、Python からアプリへの向きで要求を運びます。これにより Web コンテキストは **BE-0019 の着地を前提とせず**、idb を actuator としたまま動きます（XCUITest の完全なホストが扱いづらいヘッドレス CI で効きます）。ブリッジが返す DOM は、web（Playwright）backend がページを要素へ変換するのに既に使っているのと同じロジックで正規化するので、セレクタ解決と「曖昧なら失敗」の規則がそのまま引き継がれます。これは既存の `Driver` ／ capability モデルに収まります。新しい `webView` capability が Web コンテキストをゲートし、それを欠くバックエンドはステップをきれいに失敗させます（今日のマルチタッチジェスチャと同じ契約です）。後から登場する WebView 対応アクチュエータ（例: XCUITest 経由）は、シナリオと config を変えずに [stability-ladder](../../docs/ja/drivers.md) の `backend` リストが拾います。
- **待機とアサーション**は、形のうえでは変わらないまま引き継がれ（timeout 必須の条件待機、機械チェック可能なアサーション）、Web コンテキスト内の DOM クエリに対して評価されます。固定 sleep はなく、run／CI ゲートは AI 非依存のままです。

config：どのアプリが WebView を使うか、安定した WebView ホストの id があればそれを `apps.<name>` に置きます。ツール、ドライバ、ランナーはアプリ非依存のままです。

### コンテキスト境界の文法

ステップは、スコープ先のネイティブな WebView ホスト要素を指名して Web コンテキストに入ります。

```yaml
- web:
    within: { id: checkout.webview }   # a11y ツリー上のネイティブな WKWebView 要素
    steps:
      - tap: { id: place-order }       # id は正規化された DOM（要素の data-testid）に対して解決
      - assert: { exists: { id: order-confirmation } }
```

ここでの `within` は、既存のセレクタの形（`bajutsu/scenario/models/selector.py` の `Selector.within`）を、新しい高さで再利用したものです。これは今日の `resolve_unique` とまったく同じく、ただ 1 つの `WKWebView` 要素へ*ネイティブに*解決します。このネイティブ解決が、境界の**検出**そのものでもあります。ホストは a11y ツリーの `query()` 上で自身のネイティブな `id` によって見つかるので、Web コンテキストに入ることは通常の一意なネイティブ一致にほかなりません（ホストが曖昧、または見つからない場合は、DOM に触れる前にステップを失敗させます。これも同じ決定性のルールです）。ブロック内では、セレクタの文法は形のうえでは変わりません（`id`／`idMatches`／`label`／`traits`／`value`／`index`）が、その `id` は `accessibilityIdentifier` ではなく、正規化された DOM の `Element.identifier`、すなわち要素の `data-testid`（Web backend の規約。`parse_dom` / `QUERY_JS`）に対して解決します。これは同じ安定した識別子であって、一般的な CSS セレクタではありません。ランナーは CSS を解析せず、iOS と同じく正規化された `Element` ツリーをそのまま照合します。

**ステップごとに有効な WebView は 1 つ（最初のスライス）。** `web` ブロックはちょうど 1 つのホストにスコープするので、その配下のステップが走る間に見えている DOM はちょうど 1 つの WebView のものになります。境界はブロックの先頭で開き、末尾で閉じます。**入れ子の WebView（DOM が別の WebView をホストする WebView）は最初のスライスの対象外です。** ホストセレクタはただ 1 つのネイティブな WebView へ解決しなければならず、その DOM がさらに別の Web コンテキストを埋め込んでいる場合は後の反復に回します。これにより「このセレクタはどちらのツリーを問い合わせるのか」が、推測ではなく見ればわかる状態を保てます。

### ループバック JS ブリッジ

DOM の操作には idb が持たない機能、すなわち `WKWebView.evaluateJavaScript` による DOM ノードの問い合わせと操作が必要です。ブリッジはネットワークコレクタのループバックのパターン（`bajutsu/network.py`）を写し取ります。bajutsu は `127.0.0.1:<port>` に小さな受信器をバインドし、そのアドレスを起動 env で（`BAJUTSU_COLLECTOR` と並べて）アプリへ注入します。これにより、アプリ内の **BajutsuKit** 側と Python 側は Mac のループバックインターフェースを共有します。DOM クエリを処理するとき、bajutsu は（ループバックチャネル越しに）BajutsuKit へ、スコープ中の `WKWebView` でページ走査の JavaScript を `evaluateJavaScript` によって実行するよう依頼します。BajutsuKit は得られたノードの一覧を JSON として送り返し、Python の受信器がそれを正規化処理へ渡します。操作（タップ）も同じ往復で行います。bajutsu がスナップショットから一意なノードを解決し、その後 BajutsuKit へ、WebView 内でそのクリックを発火するよう依頼します。

これは **BE-0019 から独立**です。BajutsuKit は今日 idb のもとでアプリと並んでプロセス内で動くので、ブリッジは idb を actuator としたまま動きます。これは、XCUITest の完全なホストを立ち上げるのが扱いづらいヘッドレス CI で効いてきます。WebView コンテンツへネイティブに到達する XCUITest 経路は、シナリオと config を変えずに stability ladder が拾う、後からの任意の補完であって、前提条件ではありません。

### DOM から Element への正規化

ブリッジが返す JSON は、web（Playwright）backend が**すでに使っているのと同じロジック**（`bajutsu/drivers/playwright.py` の `parse_dom`／`_to_element`）で正規化します。`data-testid`（開発者が設定する非ローカライズの値）は `Element.identifier`（`id` セレクタが狙う先）へ、ARIA の `role`（またはタグ）は role マップを通じて正規化された `traits` へ、アクセシブルネーム／`aria-label`／テキストは `label` へ、`disabled`／`aria-disabled` と `aria-selected`／`aria-checked` は `notEnabled`／`selected` の trait へ、それぞれ写します。ページ走査の JavaScript は Playwright の `QUERY_JS` と同じフィールド（可視で操作可能、または a11y に関わるノードと、その bounding rect）を集めるので、正規化後の出力はコアの残りの部分が扱うのと同じ `Element` の形になります。こうして WebView の DOM は通常の `list[Element]` スナップショットになるため、`resolve_unique`／`find_all`、「曖昧なら失敗」の規則、アサーション、条件待機は、そのうえで**すべてそのまま**使えます。決定性コアは、自分が見ているのが a11y ツリーではなく DOM だとは決して知りません。

### 最初のスライス

価値のある最小単位で、かつゲートでテストできる増分です。

1. 1 つの `web` ブロックを、単一のネイティブな WebView ホスト（自身のネイティブな `id` で解決）にスコープします。
2. その WebView の DOM をブリッジ越しに問い合わせ、`list[Element]` へ正規化します。
3. その内側で `id`／CSS セレクタを、既存の `resolve_unique` で解決します。
4. 解決したノードを WebView 内でタップします。

純粋な正規化（ブリッジの JSON ペイロードから `list[Element]` へ）は、Simulator も JavaScript エンジンも要らず、**フェイクの DOM ペイロードに対して単体テストできます**。これは今日 `parse_dom` をテストしているのと同じやり方なので、価値ある中核は決定性ゲートの内側に着地します。ブリッジの転送はフェイクの BajutsuKit エンドポイント（用意したペイロードを返すループバック受信器）に対して検証し、オンデバイスの往復はゲートの外に置きます。最初のスライスの対象外：入れ子の WebView、DOM フィールドへの入力、存在確認を超える DOM ネイティブの条件待機、a11y ブリッジの読み取り経路。

### シーム

この変更が加える具体的な接点は、いずれも小さく、名前を付けられます。

- **ドライバのクエリ振り分け**（`bajutsu/drivers/base.py` の `Driver.query` ／ idb backend）：ステップが `web` ブロックの内側にあるとき、スナップショットはネイティブの a11y `query()` ではなくブリッジの正規化済み DOM から得ます。`Driver` プロトコルの形は変わりません。Web コンテキストが `list[Element]` の供給元を選ぶだけです。
- **セレクタリゾルバ内のコンテキストスコープ**（`bajutsu/scenario/models/actions.py` ／ `selector.py`）：`web: { within, steps }` というステップの形です。その `within` がネイティブのホストを解決し、内側のステップは同じ `resolve_unique` を通じて DOM スナップショットに対して解決します。
- **BajutsuKit の JS-eval チャネル**（Swift パッケージと、`network.py` を手本にした Python のループバック受信器）：WKWebView 内でページ走査を実行し、クリックを発火する `evaluateJavaScript` の往復です。

### プライムディレクティブの遵守

- **決定的。** ホストが曖昧または見つからない場合、あるいは DOM の一致が曖昧または空の場合は、ステップを失敗させます（`resolve_unique` をそのまま再利用します）。待機は timeout 必須の条件待機のままで、固定 sleep は持ち込みません。
- **LLM なし。** ブリッジ、正規化、解決はすべて純粋な機械ロジックです。run／CI ゲートは AI 非依存のままで、ここに合否判定へ LLM を加える要素はありません。
- **アプリ非依存。** アプリ固有の事実（どのアプリが WebView を使うか、安定したホストの id）は `apps.<name>` に置きます。ツール、ドライバ、正規化処理、ランナーは、アプリをまたいでも、また idb から後の XCUITest アクチュエータへ移っても変わりません。

## 検討した代替案

- **WebView を画面座標だけで操作する。** WebView 内のピクセル位置をタップするなら DOM ブリッジは不要ですが、非決定的かつ非可搬です（レイアウト、フォント、スクロールのどれが変わっても壊れます）。これはまさに、座標形式が最終手段だと文書化されている所以です。主要な機構としては却下します。
- **WebView 自身の a11y ブリッジ（iOS が一部の Web コンテンツについてすでに公開するネイティブツリー）に頼る。** 存在する場合は JavaScript が不要ですが、コンテンツによって網羅が部分的で一貫せず、安定した `id` ではなくアクセシブルネームを返すため、セレクタはしばしば曖昧になります。アサーションのための読み取り経路の候補としては維持しますが、アドレス指定のモデルとはしません。
- **WebView 対応を、XCUITest backend（BE-0019）がネイティブに DOM へ到達することに依存させる。** XCUITest は一部の WebView コンテンツにネイティブツリー経由で到達できるので、Web コンテキストはそのアクチュエータを待つこともできます。主要な経路としては不採用です。WebView 対応のすべてが BE-0019 の出荷まで先送りになり、しかも（XCUITest のホストが扱いづらい）ヘッドレス CI では WebView をまったく操作できないまま残ります。BajutsuKit のブリッジは今日 idb と並んでプロセス内で動きます。XCUITest 経由の経路は前提条件ではなく、stability ladder が拾う後からの補完です。
- **別個の Web 自動化バックエンド（例：Playwright アクチュエータ）を立ち上げ、Web 画面でそちらへ切り替える。** プラットフォームマップはすでに `web: (playwright,)` を予約していますが、ネイティブセッションと別個のブラウザセッションをフロー途中で受け渡すのは複雑で、WebView が生きたネイティブ画面に埋め込まれているハイブリッドアプリには合いません。ハイブリッドのケースについては、埋め込まれた DOM へその場で到達する方を優先して却下します。独立した Web バックエンドは別トラックのままとします。

## 進捗

- [x] WebView のハイブリッド対応。`web: { within, steps }` のコンテキスト、DOM から Element への正規化（`bajutsu/dom.py`）、`WebViewBridge` / `WebContextDriver`、`BajutsuWebView.swift` のエンドポイント、パイプラインの配線、コンテキスト内の `type_text` / `scrollIntoView`、`Capability.WEBVIEW` を、[#400](https://github.com/bajutsu-e2e/bajutsu/pull/400)（最初のスライス）と [#401](https://github.com/bajutsu-e2e/bajutsu/pull/401)（後続）で出荷しました。
- 本項目の対象外（必要に応じて別の BE で扱います）。値・ラベルに対する DOM の条件待機、入れ子の WebView、a11y ブリッジによるアサーション読み取り経路。

## 参考

[drivers.md](../../docs/ja/drivers.md)

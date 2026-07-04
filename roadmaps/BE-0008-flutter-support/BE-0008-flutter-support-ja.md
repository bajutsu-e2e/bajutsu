[English](BE-0008-flutter-support.md) · **日本語**

# BE-0008 — Flutter 対応

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0008](BE-0008-flutter-support-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0008") |
| トピック | プラットフォーム拡張（Android / Web / Flutter） |
<!-- /BE-METADATA -->

## はじめに

クロスレンダリングな UI（Flutter、および関連する React Native / WebView ハイブリッド）を、新しい
OS レベルの actuator を追加するのではなく、フレームワーク自身の semantics ツリーへ手を伸ばすことで対応します。
これらの UI は自前でピクセルを描く（Flutter）か WebView を埋め込む（ハイブリッド）ため、その要素は
ネイティブバックエンドが依拠する OS アクセシビリティ（a11y）ツリーに出てこないことが多いです。答えは
新しい OS actuator ではなく **semantics ブリッジ**です。

## 動機

ネイティブの iOS / Android バックエンドは、セレクタを解決するために OS アクセシビリティツリーを読みます。
Flutter は自前でピクセルを描き、既定ではそのウィジェットを当該ツリーに出しません。WebView ベースの
ハイブリッドは、DOM が OS の a11y 層から見えない Web コンテンツを埋め込みます。したがって、座標 actuator や
a11y ツリー actuator だけでは、クロスレンダリングな UI に対して `id` セレクタを確実に解決できません。これらの
フレームワークが代わりに公開するのは *自身の* semantics ツリーで、フレームワークのツール経由で到達できます。
別の OS actuator をもう 1 つ発明するのではなく、そのツリーへブリッジすることで、セレクタモデル、機械
アサーション、オーケストレータループを不変のまま、クロスレンダリングなアプリへ対応範囲を広げます。

## 詳細設計

クロスレンダリングな UI（Flutter は自前でピクセルを描く、ハイブリッドは WebView を埋め込む）は、OS の a11y
ツリーに要素を出さないことが多いです。これらは新しい OS actuator ではなく **semantics ブリッジ**を必要とします。

- **Flutter**：フレームワークの semantics ツリーを `integration_test` / VM Service / Flutter Driver 経由で
  読み、システムの残りがすでに消費している共通 `Element` ツリーへ正規化します。
- **WebView / 埋め込み Web ハイブリッド**：埋め込み Web 向けの WebView→DOM（Document Object Model）ブリッジで、
  WebView 内の DOM ノードを解決可能な要素にします。これは専用の WebView/ハイブリッド項目
  [BE-0037](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support-ja.md) と重なります。
  ブリッジの方針を重複させず共有するため、両者は一緒に設計すべきです。

設計の要点: これは **既存のネイティブバックエンドの上に重ねる semantics ブリッジ**であって、新しい OS レベルの
actuator ではありません。セレクタモデル（`resolve_unique`）、機械アサーション、オーケストレータループ、
レポーターはすべて不変のままで、変わるのはバックエンドが正規化 `Element` ツリーを組み立てるために読む取得元だけです。

### 最も安価な経路：ネイティブ identifier の公開

Flutter 3.19 以降、`SemanticsProperties.identifier` は OS アクセシビリティツリーへそのまま対応づけられます。
Android では `resource-id`（`AccessibilityNodeInfo.setViewIdResourceName` 経由）、iOS では
`accessibilityIdentifier` です。したがってウィジェットが `identifier` を設定している Flutter アプリは、
ブリッジも新しい actuator もなしに **既存の idb / adb バックエンドでそのまま解決できます**。システムの残りが
使う `id` セレクタが、公開された `resource-id` / `accessibilityIdentifier` に着地します。これが推奨する
第一の経路であり最も安価なスライスで、必要なのは新しいバックエンドではなく例となるアプリとドキュメント上の
id 規約です。下記の semantics ブリッジを最初に作らず、identifier を公開できないアプリ向けの *フォールバック*
と位置づけるのは、このためです。

### フォールバック：Dart VM Service semantics ブリッジ

`identifier` を公開しない（できない）アプリや、より豊かなウィジェット照会が要るアプリ向けには、フレームワーク
自身のツリーを **Dart VM Service**（`integration_test` / Flutter Driver 拡張が公開する observatory URL の
WebSocket）越しに読み、その semantics/ウィジェットのノードを、システムの残りがすでに消費している共通
`Element` ツリーへ正規化します。ここでの設計判断は BE-0019 の runner チャネルの選択を映します。すなわち
`appium-flutter-integration-driver` を取り込むのではなく、その WebSocket 上に薄い自前の VM Service
クライアントを作り、薄い依存の姿勢（DESIGN §4）を保ちます。解決は Python 側（`resolve_unique`）に留まるので、
ブリッジは読み取りの取得元にすぎず、変えるのは *`Element` ツリーの出どころ* だけで、セレクタの解決方法は
変えません。

### 作業分解（MECE）

1. **ネイティブ identifier 経路**：ウィジェットが `SemanticsProperties.identifier` を設定した Flutter の例／
   showcase ターゲットと、id 規約のドキュメントを用意し、Flutter アプリが既存の idb / adb バックエンドで
   そのまま駆動されることを実証します。公開された Flutter ツリーが `Element` へきれいに対応づくために必要な
   正規化の調整は、ここで着地します。
2. **VM Service semantics ブリッジ**：observatory の WebSocket に接続し、semantics/ウィジェットツリーを読み、
   `Element` へ正規化する、薄い自前の Dart VM Service クライアント（`bajutsu/webview.py` に倣った
   `bajutsu/flutter.py`）です。新しい actuator ではなく、選ばれたネイティブ actuator の背後の読み取りの
   取得元として配線します。
3. **WebView / 埋め込み Web ハイブリッドブリッジ**：既存の WebView→DOM ブリッジ（`bajutsu/webview.py` と
   BajutsuKit のブリッジサーバ、[BE-0037](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support-ja.md)）
   を再利用し、WebView 内の DOM ノードを解決可能にして、Android の WebView にも広げます。ブリッジの方針を
   重複させず共有するため、BE-0037 と一緒に設計します。
4. **能力と開示**：ブリッジは新しい actuator 能力を宣言しません。セレクタモデル、機械アサーション、
   オーケストレータループ、レポーターは不変のままで、実行マニフェストはどの取得元（ネイティブツリー / VM
   Service / WebView）が要素を供給したかを記録します。
5. **検証**：高速ゲート（デバイス不要）：取得済みの semantics ツリーと DOM のフィクスチャを `Element` へ
   正規化し、それに対する解決と曖昧さの挙動を確認します。実機（e2e）：ネイティブ `identifier`（スライス 1）と
   VM Service ブリッジ（スライス 2）の両方で駆動する Flutter のデモ、および WebView ハイブリッドのシナリオです。

### 展開順：2 つのネイティブツリーの後の第 3 段階

クロスレンダリング対応は **第 3 段階**として扱い、2 つのネイティブツリー（idb 経由の iOS と adb 経由の
Android）が抽象を実証した後にのみ着手します。ネイティブバックエンドは、iOS 固有の前提が本当に 3 つの継ぎ目
（actuator、環境マネージャ、id 規約）に閉じていたことを確認する、最も安価で直接的な手段です。semantics
ブリッジはより難しくフレームワーク固有の問題であり、コアが本当にプラットフォーム中立だと示された後に挑むのが
最善です。2 つのネイティブツリーが固まるまで後回しにします。

## 検討した代替案

- **Flutter 向けの新しい OS レベル actuator（描画ピクセル上の座標 tap）。** 却下: 安定した開発者付与の id が
  *何らかの* ツリーに出ていなければ、座標 actuation はセレクタを決定的に解決できず、「曖昧なら即失敗」も守れません。
  フレームワーク自身の semantics ツリーが正しい取得元です。
- **ネイティブツリーが着地する前にブリッジを作る。** 第 3 段階へ見送り: クロスレンダリング対応はフレームワーク
  固有で難しく、2 つのネイティブツリーが抽象を実証する前に挑むと 2 つのリスクを混同してしまいます。上記の展開順を参照。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] ネイティブ identifier 経路：`SemanticsProperties.identifier` を使う Flutter の例ターゲットを既存の idb / adb バックエンドで駆動、id 規約のドキュメント。
- [ ] VM Service semantics ブリッジ：semantics ツリーを `Element` へ正規化する薄い自前の Dart VM Service クライアント（`bajutsu/flutter.py`）。
- [ ] WebView / ハイブリッドブリッジ：BE-0037 の WebView→DOM ブリッジを再利用し Android へ拡張、BE-0037 と一緒に設計し重複させない。
- [ ] 能力と開示：ブリッジは読み取りの取得元のみ、マニフェストに要素の取得元を記録。
- [ ] 検証：フィクスチャに対する高速ゲートの正規化／解決テスト、実機の Flutter（ネイティブ id + VM Service）と WebView ハイブリッドの e2e。

## 参考

[DESIGN](../../../DESIGN.md)、`bajutsu/drivers/`、`bajutsu/backends.py`、
[BE-0037 — WebView / hybrid support](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support-ja.md)、
[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend-ja.md)、
[BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)

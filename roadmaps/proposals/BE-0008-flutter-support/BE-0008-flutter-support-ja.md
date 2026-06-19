[English](BE-0008-flutter-support.md) · **日本語**

# BE-0008 — Flutter 対応

* 提案: [BE-0008](BE-0008-flutter-support-ja.md)
* 状態: **提案**
* トラック: [提案](../../README-ja.md#提案)
* トピック: プラットフォーム拡張（Android / Web / Flutter）

## はじめに

クロスレンダリングな UI —— Flutter、および関連する React Native / WebView ハイブリッド —— を、新しい
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
別の OS actuator をもう 1 つ発明するのではなく、そのツリーへブリッジすることで、セレクタモデル・機械
アサーション・オーケストレータループを不変のまま、クロスレンダリングなアプリへ対応範囲を広げます。

## 詳細設計

クロスレンダリングな UI（Flutter は自前でピクセルを描く、ハイブリッドは WebView を埋め込む）は、OS の a11y
ツリーに要素を出さないことが多いです。これらは新しい OS actuator ではなく **semantics ブリッジ**を必要とします。

- **Flutter** —— フレームワークの semantics ツリーを `integration_test` / VM Service / Flutter Driver 経由で
  読み、システムの残りがすでに消費している共通 `Element` ツリーへ正規化します。
- **WebView / 埋め込み Web ハイブリッド** —— 埋め込み Web 向けの WebView→DOM（Document Object Model）ブリッジで、
  WebView 内の DOM ノードを解決可能な要素にします。これは専用の WebView/ハイブリッド項目
  [BE-0037](../../proposals/BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support-ja.md) と重なります。
  ブリッジの方針を重複させず共有するため、両者は一緒に設計すべきです。

設計の要点: これは **既存のネイティブバックエンドの上に重ねる semantics ブリッジ**であって、新しい OS レベルの
actuator ではありません。セレクタモデル（`resolve_unique`）・機械アサーション・オーケストレータループ・
レポーターはすべて不変のままで、変わるのはバックエンドが正規化 `Element` ツリーを組み立てるために読む取得元だけです。

### 展開順 —— 2 つのネイティブツリーの後の第 3 段階

クロスレンダリング対応は **第 3 段階**として扱い、2 つのネイティブツリー（idb 経由の iOS と adb 経由の
Android）が抽象を実証した後にのみ着手します。ネイティブバックエンドは、iOS 固有の前提が本当に 3 つの継ぎ目
（actuator・環境マネージャ・id 規約）に閉じていたことを確認する、最も安価で直接的な手段です。semantics
ブリッジはより難しくフレームワーク固有の問題であり、コアが本当にプラットフォーム中立だと示された後に挑むのが
最善です。2 つのネイティブツリーが固まるまで後回しにします。

## 検討した代替案

- **Flutter 向けの新しい OS レベル actuator（描画ピクセル上の座標 tap）。** 却下: 安定した開発者付与の id が
  *何らかの* ツリーに出ていなければ、座標 actuation はセレクタを決定的に解決できず、「曖昧なら即失敗」も守れません。
  フレームワーク自身の semantics ツリーが正しい取得元です。
- **ネイティブツリーが着地する前にブリッジを作る。** 第 3 段階へ見送り: クロスレンダリング対応はフレームワーク
  固有で難しく、2 つのネイティブツリーが抽象を実証する前に挑むと 2 つのリスクを混同してしまいます。上記の展開順を参照。

## 参考

[DESIGN](../../../DESIGN.md)、`bajutsu/drivers/`、`bajutsu/backends.py`、
[BE-0037 — WebView / hybrid support](../../proposals/BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support-ja.md)、
[BE-0007 — Android backend](../../proposals/BE-0007-android-backend/BE-0007-android-backend-ja.md)、
[BE-0041 — Web (Playwright) backend](../../proposals/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)

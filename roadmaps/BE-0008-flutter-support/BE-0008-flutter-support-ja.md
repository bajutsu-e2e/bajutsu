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

Flutter アプリを、新しいバックエンドや semantics ブリッジではなく、**既存の idb / adb バックエンドのまま**
駆動します。この項目は Flutter を、新機能ではなく id 規約と検証の課題として扱います。Flutter は Skia /
Impeller で自前のピクセルを描きますが、ネイティブバックエンドはピクセルを一切読みません。読むのは OS の
アクセシビリティ（a11y）ツリーで、Flutter はそこへ自身の semantics ツリーを橋渡ししています。したがって
`SemanticsProperties.identifier` を設定した Flutter ウィジェットは、新しいコードなしに両バックエンドで解決
可能な要素として現れます。成果物は新しい actuator ではなく、id 規約と例となるアプリ、そして公開が実際に
成り立つことの実機検証です。

## 動機

ネイティブの iOS（idb）と Android（adb）のバックエンドは、描画ピクセルを解析するのではなく OS アクセシビリティ
ツリーを読んでセレクタを解決します。これはネイティブアプリでも Flutter アプリでも同じで、tap 座標は要素の
bounds の中心から計算します。したがって「Flutter は自前でピクセルを描くから新しい actuator が要る」という
前提は、独立した 2 つの層を混同しています。Flutter は確かに Skia / Impeller で単一の不透明なサーフェスを
描きますが、それとは別に semantics ツリーを保持し、それを OS のアクセシビリティ API へ変換します。Android の
`AccessibilityBridge` は各 `SemanticsNode` を仮想の `AccessibilityNodeInfo` に変換し、iOS のエンジンは
`SemanticsObject`（`UIAccessibility` 要素）を公開します。idb と adb が読むのは、まさにこのツリーです。各
semantics ノードは画面上の矩形を持つので、bounds 中心への tap がウィジェットに着地し、届いた OS タッチを
Flutter 自身の gesture arena が正しいウィジェットへ振り分けます。レンダラが Skia か Impeller かは、この経路に
影響しません。

Flutter 3.19 以降、`SemanticsProperties.identifier` はそのツリーへそのまま対応づけられます。Android では
`resource-id`（`AccessibilityNodeInfo.setViewIdResourceName` 経由）、iOS では `accessibilityIdentifier` です。
したがって、システムの残りがすでに使うプラットフォーム中立な `id` セレクタが、公開された identifier に着地し、
セレクタモデル、機械アサーション、オーケストレータループ、レポーターはそのまま変わりません。Flutter 対応に
必要なのは id 規約と、それが実機で成り立つことの実証であって、新しい OS actuator やフレームワークブリッジでは
ありません。

## 詳細設計

### なぜ idb / adb がすでに Flutter の UI に届くのか

Flutter は UI を子ビューのない 1 つのネイティブビュー（Android は `FlutterSurfaceView`、iOS は
`FlutterView`）へ描くので、OS の a11y ツリーからは既定で 1 枚の不透明なサーフェスに見えます。ウィジェットを
解決可能にしているのは、描画とは独立したエンジンのアクセシビリティ橋渡しです。エンジンは semantics ツリーを
組み立て、それをネイティブバックエンドがすでに読んでいる OS アクセシビリティ API へ差し込みます。こうして
公開された `resource-id` / `accessibilityIdentifier` は、不変の `resolve_unique` 経路で解決され、座標 tap は
semantics ノードの画面上の矩形と Flutter 自身のヒットテストを通じて着地します。この項目がバックエンドを足さ
ないのは、このためです。公開の仕組みはすでに存在し、作業はそれを使うアプリを用意することと、次の 2 つの条件が
成り立つことを実証することです。

### 成り立つべき 2 つの条件

橋渡しは無条件ではありません。idb / adb が Flutter ウィジェットを駆動できるかは、レンダラではなくフレーム
ワークの semantics の状態に依存します。

- **semantics ツリーは遅延構築されます。** Flutter はアクセシビリティのクライアントが接続したとき、または
  アプリが `SemanticsBinding.instance.ensureSemantics()` を呼んだときにだけツリーを組み立てます。Android
  では uiautomator がアクセシビリティサービスとして接続し、構築の引き金になります。iOS で idb のアクセシ
  ビリティ取得が同じ引き金になるかは、実機で確かめるべき未解決の点です。ならない場合は、規約に一行の
  `ensureSemantics()` 呼び出しをフォールバックとして書きます。
- **semantics を持つウィジェットしか現れません。** 標準の Material / Cupertino ウィジェットやテキストは
  semantics を自動で持ちますが、`CustomPaint` で描いたコントロールを `Semantics` で包んでいなければ、ツリー
  には入らず解決できません。`Semantics(identifier: …)` で包むことは、id を公開する規約と同じ操作です。独自
  コントロールを Skia が描くか Impeller が描くかは、ここに影響しません。影響するのは、開発者が semantics を
  付けたかどうかだけです。

### id 規約

ドキュメントに、iOS と Android のものと並べて記す規約です。

| `Selector` フィールド | Flutter（ネイティブバックエンド経由） |
|---|---|
| `id`（主）| `Semantics(identifier: "…")` → `resource-id`（Android）／ `accessibilityIdentifier`（iOS）、Flutter 3.19 以降 |
| `label`（補助）| ウィジェットの semantics ラベル（可視テキスト）|
| `value` | ウィジェットの semantics 値（状態値のミラー、SPEC §2.1）|
| `traits`（役割フィルタ）| プラットフォームのウィジェットクラス／trait として公開される semantics の役割 |

`identifier` の公開が入るのが Flutter 3.19（2024 年 2 月）なので、これを最低バージョンとします。

### 作業分解（MECE）

1. **Flutter ショーケースターゲット。** ウィジェットが `Semantics(identifier: …)` を設定した Flutter のデモを
   `demos/` に追加し、`backend: [ios]` / `backend: [android]` ターゲットとして、既存のショーケースシナリオ
   （id / tap / type / value）を idb と adb でそのまま流します。Flutter 固有のシナリオ DSL は導入しません。
   公開された Flutter ツリーが `Element` へきれいに対応づくために必要な正規化の調整は、ここで着地します。
2. **id 規約のドキュメント。** Flutter の id 規約（上表）、Flutter 3.19 の最低バージョン、semantics の遅延
   構築という前提と `ensureSemantics()` のフォールバック、そして後述のスコープ外の境界を、`docs/drivers.md`
   とその日本語版に記します。
3. **実機検証。** この項目の技術リスクはここに集中します。次の 3 つの仮説を実機で確かめ、結果を規約へ反映
   します。
   - semantics ツリーの起動タイミング（Android は uiautomator の a11y 接続経由、iOS は idb 経由という未解決
     の点、`ensureSemantics()` のフォールバック付き）。
   - `MergeSemantics` による統合と画面外（スクロール）の culling。セレクタが依然として一意に解決することを
     確認し、注意点を文書化します。
   - 3.19 以降の両バックエンドで、`identifier` が `resource-id` / `accessibilityIdentifier` として公開される
     こと。

### スコープ外

- **Dart VM Service の semantics ブリッジ。** 作りません（*検討した代替案* を参照）。救おうとするアプリを実際
  には救えず、その前提が E2E の用途と両立しないためです。
- **WebView / 埋め込み Web ハイブリッド。** [BE-0037](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support-ja.md)
  の領分で、こちらは **実装済み** です（`bajutsu/webview.py` と BajutsuKit のブリッジ）。Android の WebView へ
  広げるのは BE-0037 の後続作業で、Flutter 自身の描画とは無関係なので、この項目には含めません。
- **Flutter Web（CanvasKit）。** Web 向けの Flutter は canvas へ描き、要素を DOM に出さないため、Playwright
  バックエンドでは解決できません。SEO / semantics の DOM オーバーレイは別の問題であり、スコープ外とします。
- **`identifier` を公開せず、かつ変更もできないアプリ。** *何らかの* ツリーに開発者付与の id が出ていなければ、
  座標 actuation はセレクタを決定的に解決できず、「曖昧なら即失敗」も守れません。壊れやすいブリッジで無理に
  救うのではなく、正直にスコープ外とします。

### 展開順：2 つのネイティブツリーの後の第 3 段階

Flutter 対応は引き続き **第 3 段階**とし、ネイティブツリー（idb 経由の iOS と adb 経由の Android）が抽象を
実証した後に着手します。Flutter-on-Android は adb を通じて駆動するので、
[BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md) が実機で固まっていることが前提です。ただし
スコープはフレームワーク固有のブリッジではなく、デモと規約と検証に収まり、新しいバックエンドの数分の一で
済みます。

## 検討した代替案

- **Dart VM Service の semantics ブリッジ（旧来の第一フォールバック）。** 作る対象からは却下し、明確な需要が
  出たときにだけ見直す文書上の選択肢へ降格します。semantics ツリーを Dart VM Service 越しに読むには、debug /
  profile ビルド（VM Service は release で無効ですが、E2E の用途は release 相当を望みます）と、アプリ側の
  `integration_test` / Flutter Driver の計装が要ります。アプリに計装できるなら `identifier` を付ける方が安く、
  ブリッジも要らないので、フォールバックが意図する受益者（id を公開できないアプリ）はほぼ空です。Flutter
  Driver 自体が deprecated なので、自前の VM Service クライアントを抱えてもフレームワークへの追随コストに見合う
  だけの到達範囲がありません。`identifier` を付けられず、かつ debug ビルドでなら駆動してよいアプリという具体的
  な需要が出たときにだけ見直します。
- **Flutter 向けの新しい OS レベル actuator（描画ピクセル上の座標 tap）。** 却下します。安定した開発者付与の id
  が *何らかの* ツリーに出ていなければ、座標 actuation はセレクタを決定的に解決できず、「曖昧なら即失敗」も
  守れません。OS の a11y ツリーへ橋渡しされた Flutter 自身の semantics ツリーが正しい取得元であり、ネイティブ
  バックエンドはそれをすでに読んでいます。
- **ネイティブツリーが着地する前にブリッジを作る。** 上記の展開順のとおり見送ります。Flutter 対応は Android
  （adb）バックエンドに依拠するので、2 つのネイティブツリーに先行させず、その後に続けます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] Flutter ショーケースターゲット：`Semantics(identifier: …)` を使う `demos/` の Flutter アプリを、既存の idb / adb バックエンドで既存のショーケースシナリオにより駆動。
- [ ] id 規約のドキュメント：Flutter の id 規約、3.19 の最低バージョン、semantics 遅延構築の前提と `ensureSemantics()` フォールバック、スコープ外の境界（`docs/drivers.md` と日本語版）。
- [ ] 実機検証：3 つの仮説（semantics の起動タイミング、`MergeSemantics` と culling、`identifier` の公開）を確かめ、結果を規約へ反映。

## 参考

[DESIGN](../../DESIGN.md)、`bajutsu/drivers/`、`bajutsu/backends.py`、
[drivers.md](../../docs/drivers.md)、
[BE-0037 — WebView / hybrid support](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support-ja.md)、
[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend-ja.md)、
[BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)

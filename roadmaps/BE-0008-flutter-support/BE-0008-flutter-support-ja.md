[English](BE-0008-flutter-support.md) · **日本語**

# BE-0008 — Flutter 対応

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0008](BE-0008-flutter-support-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0008") |
| トピック | プラットフォーム対応 |
<!-- /BE-METADATA -->

## はじめに

Flutter アプリを、新しいバックエンドや semantics ブリッジではなく、**既存の XCUITest / adb バックエンドの
まま**駆動します。この項目は Flutter を、新機能ではなく id 規約と検証の課題として扱います。Flutter は
Skia / Impeller で自前のピクセルを描きますが、どちらのネイティブバックエンドもピクセルを読みません。
XCUITest は XCTest の自動化スナップショットを読み、adb は UI Automator のツリーを読みます。そして
Flutter は、自身の semantics ツリーをその両方へ橋渡ししています。したがって `SemanticsProperties.identifier`
を設定した Flutter ウィジェットは、新しいコードなしに両方の
[バックエンド](../../docs/ja/glossary.md#driver-backend-actuator-platform)で解決可能な要素として現れます。
成果物は新しい actuator ではなく、id 規約と例となるアプリ、そして公開が実際に成り立つことの実機検証です。

## 動機

ネイティブの iOS（[XCUITest](../../docs/ja/drivers.md#xcuitestios)）と Android（[adb](../../docs/ja/drivers.md#adbandroid)）
のバックエンドは、描画ピクセルを解析するのではなく、アクセシビリティツリーを読んで
[セレクタ](../../docs/ja/glossary.md#シナリオのオーサリング)を解決します。これはネイティブアプリでも Flutter
アプリでも同じです。ただし actuation の仕方は両者で異なります。XCUITest は解決した要素をアクセシビリティ
識別子で直接タップします（座標を使わない semantic tap です）。唯一の座標バックエンドである adb は、要素の
frame 中心からタップ座標を計算します。どちらの経路でも、タップはピクセルに一切触れずに着地します。
したがって「Flutter は自前でピクセルを描くから新しい actuator が要る」という前提は、独立した2つの層を
混同しています。

Flutter は確かに Skia / Impeller で単一の不透明なサーフェスを描きますが、それとは別に semantics ツリーを
保持し、それを OS のアクセシビリティ API へ変換します。Android の `AccessibilityBridge` は各
`SemanticsNode` を仮想の `AccessibilityNodeInfo` に変換し、iOS のエンジンは `SemanticsObject`
（`UIAccessibility` 要素）を公開します。XCUITest の自動化スナップショットと adb の UI Automator ダンプが
読むのは、まさにこのツリーです。各 semantics ノードは画面上の矩形も持つので、Android では frame 中心への
tap がウィジェットに着地し、届いた OS タッチを Flutter 自身の gesture arena が正しいウィジェットへ振り分け
ます。iOS では、XCUITest が同じノードを識別子で解決し、座標計算を一切挟まずに直接タップします。レンダラが
Skia か Impeller かは、どちらの経路にも影響しません。

Flutter 3.19 以降、`SemanticsProperties.identifier` はそのツリーへそのまま対応づけられます。iOS では
`accessibilityIdentifier`、Android では `resource-id`（`AccessibilityNodeInfo.setViewIdResourceName`
経由）です。したがって、システムの残りがすでに使うプラットフォーム中立な `id` セレクタが、公開された
identifier に着地し、セレクタモデル、機械アサーション、オーケストレータループ、レポーターはそのまま
変わりません。Flutter 対応に必要なのは id 規約と、それが実機で成り立つことの実証であって、新しい OS
actuator やフレームワークブリッジではありません。

## 詳細設計

### なぜ XCUITest / adb がすでに Flutter の UI に届くのか

Flutter は UI を子ビューのない1つのネイティブビュー（Android は `FlutterSurfaceView`、iOS は
`FlutterView`）へ描くので、OS のアクセシビリティツリーからは既定で1枚の不透明なサーフェスに見えます。
ウィジェットを解決可能にしているのは、描画とは独立したエンジンのアクセシビリティ橋渡しです。エンジンは
semantics ツリーを組み立て、それをネイティブバックエンドがすでに読んでいる OS アクセシビリティ API へ
差し込みます。こうして公開された `resource-id` は adb の不変の `resolve_unique` 経路で解決され、frame
中心への座標タップで actuate されます。同じ経路で公開された `accessibilityIdentifier` は XCUITest の
自動化スナップショットで解決され、座標を挟まないネイティブの semantic tap で actuate されます。この項目
がバックエンドを足さないのは、このためです。公開の仕組みは両プラットフォームにすでに存在し、作業はそれを
使うアプリを用意することと、次の2つの条件が成り立つことを実証することです。

### 成り立つべき2つの条件

橋渡しは無条件ではありません。XCUITest / adb が Flutter ウィジェットを駆動できるかは、レンダラではなく
フレームワークの semantics の状態に依存します。

- **semantics ツリーは遅延構築されます。** Flutter はアクセシビリティのクライアントが接続したとき、または
  アプリが `SemanticsBinding.instance.ensureSemantics()` を呼んだときにだけツリーを組み立てます。Android
  では UI Automator がアクセシビリティサービスとして接続し、構築の引き金になります。iOS で BajutsuKit の
  常駐ランナーを通じた XCTest の自動化スナップショット読み取りが同じ引き金になるかは、実機で確かめるべき
  未解決の点です。ならない場合は、規約に一行の `ensureSemantics()` 呼び出しをフォールバックとして書きます。
- **semantics を持つウィジェットしか現れません。** 標準の Material / Cupertino ウィジェットやテキストは
  semantics を自動で持ちますが、`CustomPaint` で描いたコントロールを `Semantics` で包んでいなければ、
  ツリーには入らず解決できません。`Semantics(identifier: …)` で包むことは、id を公開する規約と同じ操作
  です。独自コントロールを Skia が描くか Impeller が描くかは、ここに影響しません。影響するのは、開発者が
  semantics を付けたかどうかだけです。

### id 規約

ドキュメントに、iOS と Android のものと並べて記す規約です。

| `Selector` フィールド | Flutter（XCUITest / adb 経由） |
|---|---|
| `id`（主）| `Semantics(identifier: "…")` → `accessibilityIdentifier`（iOS）／ `resource-id`（Android）、Flutter 3.19 以降 |
| `label`（補助）| ウィジェットの semantics ラベル（可視テキスト）|
| `value` | ウィジェットの semantics 値（状態値のミラー、SPEC §2.1）|
| `traits`（役割フィルタ）| プラットフォームのウィジェットクラス／trait として公開される semantics の役割 |

`identifier` の公開が入るのが Flutter 3.19（2024年2月）なので、これを最低バージョンとします。

### 作業分解（MECE）

1. **Flutter ショーケースターゲット。** ウィジェットが `Semantics(identifier: …)` を設定した Flutter の
   デモを `demos/` に追加し、`backend: [ios]` / `backend: [android]` ターゲットとして、既存のショーケース
   シナリオ（id / tap / type / value）を XCUITest と adb でそのまま流します。Flutter 固有のシナリオ DSL は
   導入しません。公開された Flutter ツリーが `Element` へきれいに対応づくために必要な正規化の調整は、
   ここで着地します。
2. **id 規約のドキュメント。** Flutter の id 規約（上表）、Flutter 3.19 の最低バージョン、semantics の
   遅延構築という前提と `ensureSemantics()` のフォールバック、そして後述のスコープ外の境界を、
   `docs/drivers.md` とその日本語版に記します。
3. **実機検証。** この項目の技術リスクはここに集中します。次の3つの仮説を実機で確かめ、結果を規約へ
   反映します。
   - semantics ツリーの起動タイミング（Android は UI Automator のアクセシビリティサービス接続経由、iOS は
     BajutsuKit の常駐ランナーを通じた XCUITest の自動化スナップショット経由という未解決の点。
     `ensureSemantics()` のフォールバック付き）。
   - `MergeSemantics` による統合と画面外（スクロール）の culling。セレクタが依然として一意に解決すること
     を確認し、注意点を文書化します。
   - 3.19 以降の両バックエンドで、`identifier` が `accessibilityIdentifier` / `resource-id` として公開
     されること。

### スコープ外

- **Dart VM Service の semantics ブリッジ。** 作りません（*検討した代替案* を参照）。救おうとするアプリを
  実際には救えず、その前提が E2E の用途と両立しないためです。
- **WebView / 埋め込み Web ハイブリッド。** [BE-0037](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support-ja.md)
  の領分で、こちらは**実装済み**です（`bajutsu/webview.py` と BajutsuKit のブリッジ）。Android の WebView
  へ広げるのは BE-0037 の後続作業で、Flutter 自身の描画とは無関係なので、この項目には含めません。
- **Flutter Web（CanvasKit）。** Web 向けの Flutter は canvas へ描き、要素を DOM に出さないため、
  Playwright バックエンドでは解決できません。SEO / semantics の DOM オーバーレイは別の問題であり、
  スコープ外とします。
- **`identifier` を公開せず、かつ変更もできないアプリ。** *何らかの*ツリーに開発者付与の id が出ていなけれ
  ば、座標 actuation はセレクタを決定的に解決できず、「曖昧なら即失敗」も守れません。壊れやすいブリッジで
  無理に救うのではなく、正直にスコープ外とします。

### 展開順：最後に残るプラットフォーム

iOS（[XCUITest](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md)）、
[Android](../BE-0007-android-backend/BE-0007-android-backend-ja.md)（adb）、
[Web](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)（Playwright）はすでに実機
またはゲート上で着地し検証も済んでいるので、この項目はもう先行項目の着地待ちではありません。Flutter は
reach 軸に残る最後の1プラットフォームです（[docs/vision.md §1](../../docs/ja/vision.md#1-reachより多くのプラットフォームと面)
参照）。残っているのはこの項目自身のスコープ、すなわちデモと規約と上記の実機検証であり、新しいバックエンド
の数分の一で済みます。

## 検討した代替案

- **Dart VM Service の semantics ブリッジ（旧来の第一フォールバック）。** 作る対象からは却下し、明確な需要
  が出たときにだけ見直す文書上の選択肢へ降格します。semantics ツリーを Dart VM Service 越しに読むには、
  debug / profile ビルド（VM Service は release で無効ですが、E2E の用途は release 相当を望みます）と、
  アプリ側の `integration_test` / Flutter Driver の計装が要ります。アプリに計装できるなら `identifier` を
  付ける方が安く、ブリッジも要らないので、フォールバックが意図する受益者（id を公開できないアプリ）はほぼ
  空です。Flutter Driver 自体が deprecated なので、自前の VM Service クライアントを抱えてもフレームワーク
  への追随コストに見合うだけの到達範囲がありません。`identifier` を付けられず、かつ debug ビルドでなら
  駆動してよいアプリという具体的な需要が出たときにだけ見直します。
- **Flutter 向けの新しい OS レベル actuator（描画ピクセル上の座標 tap）。** 却下します。安定した開発者
  付与の id が*何らかの*ツリーに出ていなければ、座標 actuation はセレクタを決定的に解決できず、「曖昧なら
  即失敗」も守れません。OS のアクセシビリティツリーへ橋渡しされた Flutter 自身の semantics ツリーが正しい
  取得元であり、ネイティブバックエンドはそれをすでに読んでいます。
- **ネイティブツリーが着地する前にブリッジを作る。** iOS と Android がまだ組み上がっている途中は、上記の
  展開順のとおり見送りました。その両方がその後着地し（BE-0290、BE-0007）、これが今この項目の実機検証を
  着手可能にしています。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに1つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] Flutter ショーケースターゲット：`Semantics(identifier: …)` を使う `demos/` の Flutter アプリを、既存の XCUITest / adb バックエンドで既存のショーケースシナリオにより駆動。
- [ ] id 規約のドキュメント：Flutter の id 規約、3.19 の最低バージョン、semantics 遅延構築の前提と `ensureSemantics()` フォールバック、スコープ外の境界（`docs/drivers.md` と日本語版）。
- [ ] 実機検証：3つの仮説（semantics の起動タイミング、`MergeSemantics` と culling、`identifier` の公開）を確かめ、結果を規約へ反映。

## 参考

[DESIGN](../../DESIGN.md)、`bajutsu/drivers/`、`bajutsu/backends.py`、
[drivers.md](../../docs/ja/drivers.md)、[vision.md](../../docs/ja/vision.md)、
[BE-0290 — XCUITest を iOS のデフォルトバックエンドにする](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md)、
[BE-0037 — WebView / hybrid support](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support-ja.md)、
[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend-ja.md)、
[BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)

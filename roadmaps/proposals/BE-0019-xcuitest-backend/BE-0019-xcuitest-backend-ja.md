[English](BE-0019-xcuitest-backend.md) · **日本語**

# BE-0019 — XCUITest backend

* 提案: [BE-0019](BE-0019-xcuitest-backend-ja.md)
* 状態: **提案**
* トラック: [提案](../../README-ja.md#提案)
* トピック: バックエンド拡張（iOS actuator）

## はじめに

idb に次ぐ 2 つ目の actuator です。安定度順ラダーの上位として登録できるようにします（抽象は既に維持済み）。

## 動機

現状、iOS の actuator は idb のみで、idb は **frame 中心への座標 tap** で操作します。semantic tap を持たないので、run ループは `query()` で要素を一意化し、その中心を叩きます。ヘッドレス CI や一般的なケースにはこれで十分ですが、実際の穴が残ります。idb は `semanticTap` も、ネイティブの `conditionWait` も、`multiTouch` も提供しません（`docs/drivers.md`）。pinch や rotate のような 2 本指ジェスチャは `UnsupportedAction` を上げ、それらの操作は codegen → XCUITest を要すると注記されています。つまり、idb では今日まったく実行できないジェスチャがあり、しかもすべての tap が、識別子で要素を叩くより本質的に脆い座標往復を経由しています。

アーキテクチャはこれを既に想定しています。DESIGN §3 は idb の隣に「（将来）XCUITest backend —— 決定的コード生成」を描き、DESIGN §5 はまさに 2 つ目の iOS actuator を差し込めるようドライバ抽象を backend 非依存に保ち、`bajutsu/backends.py` はその意図した順序をコメントで既に宣言しています: `"ios": ("idb",),  # later: ("xcuitest", "idb")`。本提案の狙いは、このプレースホルダを現実のものにすること——安定度ラダーで idb の**上位**に座る本物の 2 つ目の actuator として XCUITest を追加し、idb にできない semantic な操作と多指ジェスチャを供給する一方、XCUITest が動かないヘッドレス環境では idb をフォールバックとして残すことです。

## 詳細設計

XCUITest は既存の `Driver` Protocol を満たす登録済み actuator になるので、シナリオ DSL・セレクタ解決・run ループ・証跡サブシステム・レポータのいずれも変わりません。

- **レジストリでの配置。** `bajutsu/backends.py` で iOS プラットフォームを `("xcuitest", "idb")`——XCUITest が先、idb が後——に展開し、`xcuitest` を実行可否チェックとともに `IMPLEMENTED` に加えます。actuator は「順に並べた中で最初に実装済みかつ利用可能な backend」なので、`--backend ios` は XCUITest が動くなら自動的にそれを優先し、動かなければ idb にフォールバックします——どのシナリオも config も変えずに。これはまさに、このレジストリが備える前方互換の挙動です。
- **より豊かな capability、同じ契約。** XCUITest ドライバの `capabilities()` は、idb が提供するものに加えて `semanticTap`、ネイティブの `conditionWait`、`multiTouch` を返します。選択が決定性の核であることは変わらないので、`tap` は依然としてちょうど 1 要素に解決します——XCUITest はそれを frame 中心の座標ではなく識別子で操作するだけで、座標往復が消えます。idb では `UnsupportedAction` を上げる `pinch` / `rotate` が、直接実行できるようになります。
- **決定性を保つ。** XCUITest がネイティブの条件待機を提供する場面でも、オーケストレータの待機は固定 sleep のない条件待機のままで、ambiguous なセレクタは依然として即時に失敗します——新しい capability は表現できることを広げるだけで、規則を緩めません。重要なのは、XCUITest は `run` 時の決定的 actuator としてのみ使われ、LLM は Tier-2 ゲートに入りません。（これは、完成したシナリオを XCUITest テストソースへ構造的にマップする `codegen` とは別物で、その経路は影響を受けません。）
- **app-agnostic、必要な所は per-app。** ドライバ自体はアプリ非依存です。XCUITest で駆動されるためにアプリが用意すべきもの（例: テストホストや起動引数）は、既存の per-app 設定と並べて `apps.<name>` の下に置くので、ツールと runner はアプリをまたいで不変です。`doctor --app` は、idb の可否を報告するのと同じ仕方で XCUITest の可否を報告します。
- **フォールバックは健在。** idb をラダーの 2 番目に残すことで、XCUITest が動かない環境（必要なホストのないヘッドレス CI）は、座標ベースの idb へなだらかに劣化します。run はどの actuator が選ばれたかを記録するので、manifest は豊かな経路とフォールバック経路のどちらを通ったかを示します——既存の劣化開示の規則と整合します。

## 検討した代替案

- **idb を XCUITest で丸ごと置き換える。** XCUITest はより豊かな actuator ですが、idb のヘッドレスかつ座標ベースの動作は、まさに XCUITest の完全なホストが扱いづらい CI 環境で価値があります。両方を順序付きラダーに保つことで双方の良さが得られます——XCUITest を優先し、idb にフォールバックし、シナリオは不変です。
- **欠けているジェスチャを idb に後付けする。** idb の単一タッチのプリミティブから pinch/rotate を合成する手もあります。idb は本質的に単一タッチを露出するので、これは多指ジェスチャの不確実な近似になります——プロジェクトが避ける、まさに脆く非決定的な挙動です。本物の多指 backend が誠実な解です。
- **特定のジェスチャだけ XCUITest へ流し、idb を actuator のままにする。** 2 つのドライバが 1 つのデバイスを操作することは、単一 actuator の規則（DESIGN §3.3 / §5）が防ぐためにある非決定性を再導入します。actuator は run ごとに一度固定されます。*証跡*の能力差は read-only フォールバックの設計（BE-0020）で別途扱いますが、*操作*は 1 つの backend にとどまります。

## 参考

[DESIGN §5 / §3](../../../DESIGN.md)、`bajutsu/backends.py`

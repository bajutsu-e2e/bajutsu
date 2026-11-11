[English](BE-XXXX-adb-unrooted-multitouch.md) · **日本語**

# BE-XXXX — 常駐サーバーを使い、root なしで adb ドライバの pinch と rotate を実行する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-adb-unrooted-multitouch-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | [#2410](https://github.com/bajutsu-e2e/bajutsu/pull/2410) |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0232](../BE-0232-adb-multitouch-gestures/BE-0232-adb-multitouch-gestures-ja.md), [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md), [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Android（adb）ドライバが、root 化していない端末でも `pinch` と `rotate` を実行できるようにします。
常駐する UI Automator サーバーに、2 本指ジェスチャのエンドポイントを追加します。このエンドポイントは
`UiAutomation.injectInputEvent` で 2 本の指を注入するので、root を必要としません。ドライバは 2 本指の
ジェスチャをまずこのエンドポイントへ送ります。生の `sendevent` スイープは、root 化した端末でサーバーが
止まっているときのフォールバックとして残します。

今は市販のどの端末でも両ステップが失敗しており、この変更はその穴を埋めます。`multiTouch` 能力を宣言する
ドライバが、root 化していない端末でもその宣言どおりに動くようになります。

## 動機

現在、Android での 2 本指ジェスチャは root に依存しています。adb ドライバは 2 つの接点を `sendevent`
による生のカーネルタッチイベントとして書き込み、`/dev/input` への書き込みには root が必要だからです
（[BE-0232](../BE-0232-adb-multitouch-gestures/BE-0232-adb-multitouch-gestures-ja.md)）。root 化していない
端末では、`pinch` と `rotate` は `UnsupportedAction` で失敗します。市販の端末はすべて root 化されておらず、
デバイスクラウドの端末も大半がそうです。それでもドライバは `multiTouch` を宣言しているので、preflight は
シナリオを通し、そのシナリオは最初の 2 本指ステップで失敗します。

BE-0232 は解決策を見つけたうえで、いったん見送りました。BE-0232 の代替案は、端末上の UI Automator
インストルメンテーションを「root の前提を外すための、文書化した将来の選択肢」として挙げています。見送った
理由は、端末側のテスト APK をビルドしてインストールする必要があったことです。常駐 UI Automator サーバー
（[BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)）が、
今ではまさにその APK です。サーバーはビルドしたすべてのリースですでに動いており、ダブルタップを
`UiAutomation.injectInputEvent` で root なしに注入しています。エンドポイントを 1 つ足せば、2 本の指も
注入できます。

効果は CI で観測できます。共有シナリオ `gestures_multitouch.yaml` は、Android エミュレータで
`adb unroot` したあとでも通るようになります。現在は同じ条件で `UnsupportedAction` により失敗します。
さらに、run の actuation log に残る pinch と rotate の各記録が、ジェスチャの通った経路を示します。
読み手は証跡だけから、その run が root を使ったかどうかを判別できます。

## 詳細設計

host は、現在下している判断をすべてそのまま保ちます。対象を解決し、既存の `pinch_contacts` と
`rotate_contacts` で 2 つの接点を導きます。変わるのは注入の手段だけです。host は 4 つの接点座標を、
すでに読んでいるツリー座標系のピクセルのまま端末へ渡します。

### 作業の分割（MECE）

1. **サーバーのエンドポイント。** `BajutsuAndroidUIAutomatorServer` に `POST /gesture` を追加します。
   リクエストは、2 つの接点それぞれの始点と終点、所要時間、ステップ数を指定します。欠けた項目や不正な
   項目には `400` を返し、既定値で補いません。サーバーは 2 本指の一連のイベントを 1 回注入します。
   1 本目の指を下ろし、2 本目の指を下ろし、交互の移動を送り、両方の指を上げます。各イベントには、
   サーバーのダブルタップと同じく、明示したイベント時刻を付けます。プラットフォームがすべてのイベントを
   受け付けたときは `200` を返します。イベントが 1 つでも拒否されたときは、その時点で注入を止め、
   下ろしたままの指を `ACTION_CANCEL` で取り消してから `500` を返します。
2. **Python クライアント。** `adb_resident.gesture` を `act` と `type_text` の隣に置き、障害を同じ規則で
   対応づけます。古いサーバーが返す `404` は `AdbActUnsupported` にします。リクエストの送信後に応答が
   失われた場合は `AdbActUncertain` にします。それ以外の障害は `AdbResidentError` にします。常駐チャネルが、
   `type_text` と同じようにこの呼び出しをドライバへ渡します。
3. **ドライバの経路選択。** `pinch` と `rotate` はまずサーバーを試します。サーバーがない、またはエンドポイントを
   持たないときは、root 化した端末に限り `sendevent` スイープへフォールバックします。どちらの場合も、
   画面には何も届いていません。サーバーがジェスチャを拒否したときは、フォールバックせずにステップを
   失敗させます。最初のイベントがすでに着地しているかもしれないからです。応答が失われたときも、同じ理由で
   フォールバックしません。
   どちらの経路も使えない端末では、両方の前提条件を挙げた `UnsupportedAction` で失敗します。
4. **actuation log。** サーバーが注入したジェスチャは、新しいチャネル値 `via: resident` を記録します。
   `sendevent` スイープが注入したジェスチャは、従来どおり `via: coordinate` を記録します。どちらも同じ
   基準点、frame、拡大率または角度を持ちます。そのため新しい値は、`coordinate` と並んで座標を持つ
   チャネルになります。ジェスチャの狙いを決めるのは引き続き host で、この値はどの注入手段が届けたかを
   記録します。
5. **単体テスト。** 高速なテストスイートで、クライアントのステータスの対応づけ、経路選択の順序、各
   フォールバック、応答が失われた場合、両方の前提条件を挙げるエラーを検証します。
6. **実機での確認。** Android の `e2e` ターゲットが、`adb unroot` のあとに `gestures_multitouch` をもう一度
   実行します。CI はこれにより、root なしの経路を毎回確認します。
7. **ドキュメント。** `docs/platform-parity.md` から `pinch` と `rotate` の行を削り、`docs/drivers.md` に
   2 つの経路を記述します。どちらも英語と日本語の両方で更新します。BE-0232 には、将来の選択肢が実装された
   ことを記録します。

### 対象外

1 本指の `sendevent` ダブルタップは、今の経路のままにします。常駐サーバーの `/act` エンドポイントがすでに
root なしでダブルタップを提供しているので、この経路が使われる場面はもう多くありません。

## 検討した代替案

- **`pinch` と `rotate` を root 専用のままにする。** 採用しません。穴を塞ぐサーバーがすでにリース上で
  動いているのに、実機ではどれも穴が残ったままになります。
- **`sendevent` を完全に置き換える。** 今は採用しません。root 化した端末でサーバーが使えないとき、動く
  経路は `sendevent` スイープだけです。サーバー経路に CI での実績ができるまで、両方を残します。
- **`/act` と同じく、要素の同一性で接点を指定する。** 採用しません。host は 1 つの frame と拡大率と角度から
  4 つの点を導きます。同一性を送ると、その幾何計算を端末へ移して Kotlin で二重に持つことになります。
  現在の経路も解決済みの frame で動いているので、得るものがありません。

## 進捗

> 作業の進行に合わせて最新に保ってください。チェックリストは *詳細設計* の MECE な作業分割を
> 反映します（作業単位ごとに 1 つ）。ログには何がいつ変わったかを（古い順に）記録し、PR へリンクします。

- [x] `BajutsuAndroidUIAutomatorServer` のエンドポイント `POST /gesture`
- [x] 常駐チャネルが渡す Python クライアント `adb_resident.gesture`
- [x] ドライバの経路選択（サーバーを先に、root 化した端末の `sendevent` を次に、どちらもなければ両方の前提条件を挙げるエラー）
- [x] actuation log に経路（`resident` または `coordinate`）を記録
- [x] クライアントの対応づけと経路選択の順序の単体テスト
- [x] Android の `e2e` ターゲットでの root なしの `gestures_multitouch` 実行
- [x] ドキュメント（en と ja）と BE-0232 への記録

## 参考

- [BE-0232 — adb ドライバのマルチタッチジェスチャ](../BE-0232-adb-multitouch-gestures/BE-0232-adb-multitouch-gestures-ja.md)
- [BE-0245 — 常駐 UI Automator サーバー](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)
- [Issue #2391](https://github.com/bajutsu-e2e/bajutsu/issues/2391)：この項目が答える、穴の報告です。
- [`docs/platform-parity.md`](../../docs/ja/platform-parity.md)

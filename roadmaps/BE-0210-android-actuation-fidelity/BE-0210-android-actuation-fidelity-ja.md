[English](BE-0210-android-actuation-fidelity.md) · **日本語**

# BE-0210 — Android 実機アクチュエーションの忠実度

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0210](BE-0210-android-actuation-fidelity-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0210") |
| 実装 PR | [#857](https://github.com/bajutsu-e2e/bajutsu/pull/857)、[#910](https://github.com/bajutsu-e2e/bajutsu/pull/910) |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md)、[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Android バックエンドの最初の実機検証（[BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md)、
2026-07-07、arm64 API 34 エミュレータ）は、id/tap/type/value の中核シナリオを通しましたが、fast
ゲートでは捕捉できない座標アクチュエーションのギャップを 4 つ残しました。いずれも実機に対しての
み現れるものです。system back と by-scheme deeplink は `BackButton` を送出し、double-tap は認識
されず、スクロールしたビューポートの外にある要素には手が届かず、ランタイム権限ダイアログが
シナリオを止めます。本項目はこの 4 つについて実機アクチュエーションを堅牢にし、Android でまだ
失敗しているシナリオ（`notices`・`gestures`・`controls`、および権限で止まるフロー）で idb と
同等にします。

## 動機

idb はこの 4 つをすべて扱えるので、いずれも Android と iOS の直接的な同等性のギャップであり、
それぞれが 2026-07-07 の検証で失敗のまま残った具体的なシナリオに対応します。

- **system back／by-scheme deeplink**。今はどちらも `BackButton` を送出するため、system back を
  必要とする `notices` や、by-scheme deeplink のフローが完了できません。
- **double-tap**。2 回に分けた `adb shell input tap` の呼び出しはプラットフォームの double-tap
  ウィンドウを超えます。`input` バイナリ自体の起動が間隔を支配するため、2 回のタップを 1 回の
  シェル往復にまとめても足りません。この結果 `gestures` は double-tap で失敗します（long-press は
  通ります）。
- **scroll-into-view**。`controls` の `log.segment.value` はスクロール後のビューポートのすぐ外に
  あり、`notices` はリストのスクロールを必要とするため、スクロールすれば解決するはずのセレクタが
  代わりに失敗します。
- **ランタイム権限ダイアログ**。ランタイムの権限プロンプトがアプリを止め、それを決定論的に消す
  手段がありません。

これらはいずれも Linux の fast ゲートでは捕捉できず（実機が要る）、Android エミュレータの e2e
レーンで検証します。ただし、アクチュエーションのロジックそのものは本項目で作ります。

## 詳細設計

4 つとも決定論優先の枠内にとどまります。条件待ちと回数を区切ったリトライを使い、固定の
`sleep` は使わず、それでも曖昧なままのセレクタは推測せずに失敗します。

### 作業分解（MECE）

1. **system back と by-scheme deeplink**。system-back ステップを `adb shell input keyevent 4`
   （`KEYCODE_BACK`）に対応づけ、by-scheme deeplink を `am start -a android.intent.action.VIEW
   -d <scheme://…>` で扱い、どちらも `BackButton` を送出しないようにします。
2. **プラットフォームウィンドウ内での double-tap**。両方のタップを、タップ間の間隔を double-tap
   ウィンドウ内に保つ単一の低遅延経路で実行します。取り除くべきコストは `input tap` の呼び出し
   ごとの起動です（2 つの `input` プロセスではなく、単一のモーションイベント列など）。合格条件は、
   実機で `gestures` の double-tap が認識されることです。
3. **scroll-into-view**。セレクタが現在のビューポートで何も解決しないとき、`input swipe` で
   そちらへスクロールして再クエリし、リトライ回数で区切ったうえで失敗させます。固定の sleep では
   なく条件待ちです。決して現れないセレクタは、それでも決定論的に失敗します。
4. **ランタイム権限ダイアログ**。ランタイムの権限プロンプトを決定論的に消します。ダイアログの
   ボタンをタップするより、権限を事前に付与する方法（`pm grant`／`appops`）を優先し、タイミングを
   実行経路に持ち込みません。ここでの設計上の判断は、この手段の選択です。
5. **検証**。ロジックが許す範囲で fixture／注入した `run` に対する fast ゲート（back/deeplink の
   コマンドビルダ、scroll して再クエリするループ）。実機での合格条件は、これまで失敗していた 4 つの
   シナリオ（`notices`・`gestures`・`controls`・権限で止まるフロー）がエミュレータの e2e レーンで
   通ることです。

## 検討した代替案

- **より高機能なアクチュエータ（Appium UiAutomator2）でジェスチャを扱う**。BE-0007 自身の代替案と
  同じく先送りします。adb 経路は idb の双子のままとし、意味的なアクチュエータは後から二つ目の
  バックエンドとして追加できます。本項目は座標モデルを保ったまま、それを堅牢にします。
- **double-tap の間隔を広げる、あるいは権限ダイアログをやり過ごすための固定 sleep**。却下しました。
  固定 sleep は決定論優先に反します。double-tap は遅延を足すのではなく取り除いて解決し、権限
  ダイアログはプロンプトを待つのではなく事前付与で解決します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] system back — 第一級の `back` ステップ、Android では `keyevent 4`。by-scheme deeplink は
      ローンチ時の `preconditions.deeplink`（`am start -a android.intent.action.VIEW`）が既に担って
      おり、シナリオ途中で開く deeplink ステップは見送りました（ログを参照）。
- [x] プラットフォームの double-tap ウィンドウ内での double-tap（単一の `adb shell` 往復）。
- [x] scroll-into-view（回数を区切った scroll して再クエリする条件待ち）。
- [x] ランタイム権限の処理（起動前の決定論的な `pm grant`）。
- [x] 検証：fast ゲートでコマンドビルダと scroll して再クエリするループを、注入した `run` で検証済み
      です。実機合格は一部進みました。`notices` と `controls` は
      [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md) の時点で
      エミュレータの e2e レーン（`demos/showcase/android/Makefile` の `E2E_SCENARIOS`）で実行され、
      通ります。`gestures` と権限で止まるフローは後続作業のままです（前者の double-tap は
      `input tap ; input tap` では実機でまだ認識されず、`permission.yaml` も決定論的な
      `grantPermissions`／`pm grant` 経路にはまだ整えられていません）。

ログ:

- [#857](https://github.com/bajutsu-e2e/bajutsu/pull/857) — `back`（バックエンド横断のステップ。Android は `keyevent 4`、iOS／XCUITest は OS の戻る
  ボタンをタップ、web は `history.back()`）、`double_tap` の単一 `adb shell` 往復、解決経路での adb
  scroll-into-view（既定は上方向スワイプ、回数区切り）、config の `grantPermissions` を lease 時に
  `pm grant` で事前付与。シナリオ途中で開く独立した deeplink ステップは見送りました。ローンチ時の
  経路が既に `am start -a android.intent.action.VIEW` を使っており、実機の受け入れシナリオはいずれも
  途中の deeplink を必要とせず、ステップ化にはバックエンド横断の `DeviceControl` 面の拡張が要るため
  です。あとで途中の deeplink が必要になれば、小さな後続作業になります。

## 参考

[BE-0007 — Android バックエンド](../BE-0007-android-backend/BE-0007-android-backend-ja.md)、
[drivers.md](../../docs/ja/drivers.md)、`bajutsu/drivers/adb.py`、`bajutsu/adb.py`

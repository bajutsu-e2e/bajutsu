[English](BE-0238-ios-device-cloud-execution.md) · **日本語**

# BE-0238 — iOS device-cloud execution

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0238](BE-0238-ios-device-cloud-execution-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装中** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0238") |
| 実装 PR | [#1192](https://github.com/bajutsu-e2e/bajutsu/pull/1192)（ユニット 1: XCUITest 実機ターゲティング）、[#1193](https://github.com/bajutsu-e2e/bajutsu/pull/1193)（ユニット 2: batch package 化）、[#1195](https://github.com/bajutsu-e2e/bajutsu/pull/1195)（ユニット 3: 再署名 / 実機ケーパビリティの preflight）、[#1196](https://github.com/bajutsu-e2e/bajutsu/pull/1196)（ユニット 4: live の経路 Appium endpoint provider、seam のみ）、[#1197](https://github.com/bajutsu-e2e/bajutsu/pull/1197)（ユニット 5: 境界を fake にしたテスト）、[#1198](https://github.com/bajutsu-e2e/bajutsu/pull/1198)（ユニット 6: iOS デバイスクラウドの手順） |
| トピック | デバイスクラウド実行 |
<!-- /BE-METADATA -->

## はじめに

デバイスクラウドは iOS を**実機**で走らせます。Bajutsu の現在の iOS actuator はそこに届きません。
`simctl` は Simulator だけを対象とし（実機のクラウドに simulator は存在しません）、`idb` は Mac に常駐する
`idb_companion` のデーモンを要しますが、マネージドの macOS ホストはそれを露出しません。iOS でクラウドが
*話せる*のは **XCTest** と、AWS Device Farm では **Appium の XCUITest ドライバ**です。この項目は、それらの
成果物を生む iOS の実行経路を、すでに出荷済みの **XCUITest backend（BE-0019）**の上に足します。これにより、
クラウドで、そして副産物としてローカルでも、iOS 実機の自動化が可能になります。

## 動機

iOS は、クラウド対応に実際の新規コードが要る唯一のプラットフォームです。既存の iOS actuator は config の
不足ではなく、構造としてクラウドと相容れないからです。後で誤診しないよう、制約を確認します。

- **`simctl`** は Simulator を駆動します。実機のクラウドには対象となる simulator がありません。
- **`idb`** は Mac ホスト上で動く `idb_companion` を要します。マネージドのクラウド macOS ホストは限られた
  シェルしか提供せず、任意のデーモンを常駐させません。

AWS Device Farm も Firebase も、iOS のテストを物理デバイス上で XCTest により走らせ（Device Farm は Appium の
XCUITest ドライバでも走らせ）ます。どちらも `idb` / `simctl` を記載していません。したがって前進の経路は、
XCTest と Appium-XCUITest を話すことで、駆動の層としては新しく作るのではなく **BE-0019 の XCUITest backend**
を再利用します。これには単体での価値もあります。同じ作業が、Bajutsu に**ローカルの iOS 実機**を（デバイス
target に対する `xcodebuild` を通じて）駆動させます。これは今のところ事実上 Simulator 専用です。

これはデバイスクラウドの項目のうち最も重く、意図的に独立させています。Android の provider は adb backend を
ほぼそのまま再利用しますが、iOS は実機を駆動できる経路とクラウドの package 化を要します。foundation の
継ぎ目の後に順序づけ、iOS の *live* な経路（たとえば Appium endpoint の provider）が
*device-cloud-provider-abstraction* を再利用でき、*batch* の経路（Device Farm の XCTest / Appium の package）
が *aws-device-farm-submitter* の package 化を再利用できるようにします。

## 詳細設計

XCUITest の駆動層を共有する 2 つの経路があります。

- **batch（AWS Device Farm）。** アプリと、Bajutsu のシナリオ実行を担う XCTest / Appium-XCUITest の bundle を
  package 化し、*aws-device-farm-submitter* の仕掛けで提出し、artifact を回収します。Device Farm の iOS の
  制約を織り込む必要があります。アプリはデバイス向けに**再署名**され（App Groups や Push などの一部の
  entitlement が剥がれます）、`.ipa` はデバイス向けビルドが要り（simulator ビルドは不可）、XCTest は Appium の
  経路と同じようにはカスタマイズできません。
- **live（遠隔デバイス、後続）。** クラウドが予約済みの iOS デバイスに Appium / WebDriver の endpoint を
  露出する場合、それを live の継ぎ目上の `DeviceProvider` としてモデル化し（adb の serial の代わりに endpoint
  を渡す）、Appium-XCUITest の経路で駆動します。これは foundation の継ぎ目をそのまま再利用します。

まとめとなる作業は、**XCUITest backend（BE-0019）を実機対応にする**ことです。今日それは `xcodebuild` を
通じて Simulator を対象にします。この項目は target の選択を実機へ一般化し（まずローカル、次にクラウドの
package 化）、それが両経路の再利用可能な核になります。

### 作業分解（MECE）

1. **XCUITest の実機ターゲティング** — BE-0019 の backend を一般化して iOS の実機を駆動する（ローカルの
   `xcodebuild` のデバイス target）。両経路の共有の土台。
2. **batch の package 化（Device Farm）** — シナリオ実行を担う XCTest / Appium-XCUITest の package を作り、
   *aws-device-farm-submitter* のアップロードと回収の流れに統合する。
3. **再署名と entitlement の扱い** — Device Farm の再署名を記述して扱う（どの entitlement が落ちるか、
   それに依存するシナリオを preflight でどう縮退または skip するか）。
4. **live の経路（Appium endpoint の provider）** — 予約済みの iOS デバイスに Appium の endpoint を渡す
   `DeviceProvider` を作り、XCUITest / Appium の経路で駆動する（後続の slice として着地しうる）。
5. **テスト** — 実機ターゲティングの解決と package の組み立てを、`xcodebuild` とツールチェインの境界で fake に
   して検証する。ゲートに live のクラウドは持ち込まない。
6. **ドキュメント** — iOS のデバイスクラウドの手順（両言語）を置く。`idb` / `simctl` が使えない理由、XCTest と
   Appium の経路、再署名の注意を記す。

### prime directive への適合

- **AI をゲートに入れない。** 実機の iOS 実行は決定的な XCTest / XCUITest で、合否判定の経路にモデルは
  ありません。
- **決定性優先。** 固定 sleep は導入せず、準備完了は既存 backend と同じく条件に基づいたままです。
- **app 非依存。** iOS のクラウド固有の事柄は target の config と（batch では）CI 側の submitter に置き、
  ランナーとシナリオ形式は変わりません。

## 検討した代替案

- **`idb` / `simctl` をクラウドへ移植する。** 不可能です。実機のクラウドに simulator はなく、マネージドの
  macOS ホストはデーモンを常駐できません。構造として実現不能なので却下し、XCTest / Appium の経路を採ります。
- **iOS 用のクラウド backend を一から新設する。** BE-0019 の XCUITest backend を重複させます。却下し、代わりに
  BE-0019 を実機へ一般化します。
- **iOS を Android と同時に行う。** Android は adb backend をほぼそのまま再利用しますが、iOS は駆動と
  package 化の実際の新規コードを要します。分けておき、Android の経路が重い iOS の作業を待たずに先に出荷
  できるようにします。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] XCUITest の実機ターゲティング（BE-0019 を Simulator の先へ一般化）
- [x] Device Farm 向けの batch package 化（submitter への統合）
- [x] 再署名と entitlement の扱い（記述 + preflight の縮退）
- [ ] live の経路：Appium endpoint の `DeviceProvider`（後続の slice）
- [x] テスト（`xcodebuild` とツールチェインの境界を fake に）— 実機ターゲティングはユニット 1 で担保
- [x] ドキュメント（iOS のデバイスクラウドの手順、idb / simctl の理由、再署名の注意）

**ログ。**

- ユニット 1（[#1192](https://github.com/bajutsu-e2e/bajutsu/pull/1192)）：`xcuitest.deviceType`（既定 `simulator` / `device`）を追加し、XCUITest
  環境の `-destination` を実機向けに `platform=iOS` へ一般化しました。駆動レイヤ（`xcodebuild
  test-without-building`）は Simulator と実機で共通です。実機は simctl の端末準備を飛ばし、実機では
  成立しない simctl 依存の前提条件（erase / `appPath` インストール / 権限付与）は明示的に失敗させ、
  ユニット 2〜3 に先送りします。`xcodebuild` とツールチェインの境界を fake にし、ゲートに Simulator は
  不要です。
- ユニット 2（[#1193](https://github.com/bajutsu-e2e/bajutsu/pull/1193)）：*aws-device-farm-submitter*（`scripts/devicefarm_submit.py`）を Android 専用から
  iOS の投入にも対応させました。`platform` がアプリのアップロード種別（`ANDROID_APP` / `IOS_APP`）と
  プラットフォームごとの実行方法を選びます。iOS は Device Farm が公開する予約済みのデバイスの
  `$DEVICEFARM_DEVICE_UDID` に対して `bajutsu run --backend xcuitest` を走らせ（ユニット 1 の実機
  ターゲティングを再利用）、Android は従来どおり `--backend adb --udid booted` を使います。Appium-Python の
  カスタム環境向けテストパッケージとスペックの種別は変更していません。XCTest / Appium-XCUITest の
  バンドルを別に作るのではなく、Bajutsu のシナリオ実行をすでに担っている既存の Appium-Python カスタム
  環境パッケージをあえて再利用し、iOS の実行だけをそこに通す方針です。そのため `build_package` には
  手を入れず、変更は `render_test_spec` と CLI 側の配線（backend、`--udid`、アップロード種別）に
  限られます。テストは AWS SDK の境界だけを fake にします。showcase の iOS 用 Device Farm 設定と
  CI ワークフローのジョブは、実機向けの `.ipa` を署名なしにはビルドできないため、ユニット 3（再署名）を
  待ちます。
- ユニット 3（[#1195](https://github.com/bajutsu-e2e/bajutsu/pull/1195)）：実機の iOS（`xcuitest.deviceType: device`）では simctl
  依存のケーパビリティが失われることを preflight に教えました。新しい
  `backends.capabilities_for_run(actuator, eff)` が静的な XCUITest のケーパビリティ集合を縮退させ、
  シミュレータにしか届かない `DeviceControl` 一式と simctl-privacy の権限付与を落とします。これにより、
  デバイス制御や権限付与を使うシナリオは、実行の途中で `simctl` エラーとして遅れて失敗する代わりに、
  明確な理由とともに前もって（BE-0082）スキップされます。ユニット 1 の実行時の明示的な失敗に対する
  preflight 側の対応物です。あわせて Device Farm の再署名を `docs/devicefarm.md`（両言語）に記述しました。
  再署名はエンタイトルメント（Push / App Groups）を剥がすため、それに依存するアプリの機能は再署名後の
  挙動になり、simctl 依存のステップは実機ではスキップされます。showcase の iOS 用 Device Farm 設定と
  CI ワークフローのジョブは、実機の署名基盤（署名なしの実機向け `.ipa` はビルドできません）を引き続き
  待つため、後続に残します。
- ユニット 4（[#1196](https://github.com/bajutsu-e2e/bajutsu/pull/1196)）：BE-0236 の provider seam の上に、live
  の経路の `DeviceProvider`（seam のみ）を足しました。新しい組み込みの `appium` provider（`deviceProvider.kind:
  appium`）は、予約済みの iOS デバイスが待つ固定の Appium / WebDriver `endpoint`（セルフホストの grid）を
  udid spec としてそのまま run に渡し、デバイスは起動済みでビルドも導入済み（simctl で起動もインストールも
  しない live なリモートデバイス）と報告し、解放する対象は持ちません（予約は grid のものです）。`endpoint`
  が無ければ解決時に fail-closed で、未知の `kind` のガードと同型です。今回は seam のみで、endpoint を
  Appium / WebDriver プロトコルで駆動する部分は後続のトランスポート（XCUITest backend は現状 W3C WebDriver
  ではなく独自の runner チャネルを話します）に残すため、チェックは付けず、live の経路はまだ端から端まで
  走りません。そのトランスポートは今日の経路の上に WebDriver クライアントを重ねるだけでは実現できません。
  udid spec はそのまま `XcuitestEnvironment` に流れ、その `_destination()` が `simctl.validated_udid` を
  通しますが、共有の `device_id` の文字集合は URL の `/` を除くため、実際の `http(s)://` endpoint は現状
  `DeviceError: invalid udid` を送出します。後続のスライスでは、この値を simctl / xcodebuild の udid 機構
  ごと迂回させる必要があります（この機構は構造的に URL を運べないためです）。Android 環境の
  `ProvisionProfile` 配線は XCUITest には意図的に持ち込みません。実機の経路
  （ユニット 1）は既に simctl の端末準備を丸ごと飛ばすため、そこでフラグを尊重しても到達しないコードに
  なるからです。合否判定の経路の外で、fake で、デバイスは不要です。
- ユニット 5（[#1197](https://github.com/bajutsu-e2e/bajutsu/pull/1197)）：ユニット 1〜4 のあとに残っていた
  テストの穴を埋めました。いずれも純粋で、ゲート上で走ります（Simulator もクラウドも要りません）。ユニット 4 の
  live 経路の境界を実行可能な事実として固定しました。`appium` provider が渡す Appium / WebDriver の endpoint は、
  そのまま `_destination` に流れる udid spec そのものなので、実際の `http(s)://` endpoint は共有の `device_id`
  ポリシー（`invalid udid`）で現状は弾かれます。これは live の経路がまだ端から端まで走らないことを示す事実であり、
  後続のトランスポートが endpoint を simctl / xcodebuild の udid 機構ごと迂回させたときに、このテストが目に見えて
  赤くなって更新の合図になります。また、ユニット 3 の capability 縮退を支える `xcuitest_targets_real_device`
  アクセサに直接のユニットテスト（device / simulator / ブロック省略 / 非 iOS）を足しました。これまでは間接的に
  しか叩かれていませんでした。加えて `appium` provider の空 endpoint の分岐（falsy だが `None` ではない）を、
  endpoint 欠如の場合とは別の経路として押さえました。
- ユニット 6（[#1198](https://github.com/bajutsu-e2e/bajutsu/pull/1198)）：iOS のデバイスクラウドの手順
  （`docs/ios-device-cloud.md` と `docs/ja/` のミラー）を追加しました。work breakdown の *ドキュメント* の
  ユニットです。このページは、実機で `idb` / `simctl` が構造的にデバイスクラウドと相容れない理由
  （シミュレータ専用、デーモンの常駐）、BE-0019 バックエンドの `-destination` を実機（ローカル接続の
  デバイスを含む）へ一般化する再利用可能な `xcuitest.deviceType: device` の核、batch の経路
  （submitter の仕組みを重複させず `docs/devicefarm.md` へ相互リンク）と seam だけの live の経路
  （`appium` endpoint provider と、その endpoint がまだエンドツーエンドで動かない理由）、そして実機の
  注意点（再署名で entitlement が剥がれること、simctl に支えられたデバイス制御と権限付与が preflight で
  縮退すること）を説明します。mkdocs のナビゲーションにも追加しました。ドキュメントだけで、製品コードの
  変更はありません。live の経路のトランスポートが、残る最後の未了ボックスです。

## 参考

- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)
- [AWS Device Farm — iOS custom test environment hosts](https://docs.aws.amazon.com/devicefarm/latest/developerguide/custom-test-environments-hosts-ios.html)
- [AWS Device Farm — Appium test types](https://docs.aws.amazon.com/devicefarm/latest/developerguide/test-types-appium.html)
- [Firebase Test Lab — iOS (XCTest)](https://firebase.google.com/docs/test-lab/ios/get-started)
- 依存する兄弟項目：**device-cloud-provider-abstraction**（live の継ぎ目）、**aws-device-farm-submitter**
  （batch の package 化）

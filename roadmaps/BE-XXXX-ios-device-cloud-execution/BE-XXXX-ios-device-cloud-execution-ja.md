[English](BE-XXXX-ios-device-cloud-execution.md) · **日本語**

# BE-XXXX — iOS device-cloud execution

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-ios-device-cloud-execution-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Backend expansion (iOS actuators) |
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

- [ ] XCUITest の実機ターゲティング（BE-0019 を Simulator の先へ一般化）
- [ ] Device Farm 向けの batch package 化（XCTest / Appium-XCUITest）
- [ ] 再署名と entitlement の扱い（記述 + preflight の縮退）
- [ ] live の経路：Appium endpoint の `DeviceProvider`（後続の slice）
- [ ] テスト（`xcodebuild` とツールチェインの境界を fake に）
- [ ] ドキュメント（iOS のデバイスクラウドの手順、idb / simctl の理由、再署名の注意）

## 参考

- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)
- [AWS Device Farm — iOS custom test environment hosts](https://docs.aws.amazon.com/devicefarm/latest/developerguide/custom-test-environments-hosts-ios.html)
- [AWS Device Farm — Appium test types](https://docs.aws.amazon.com/devicefarm/latest/developerguide/test-types-appium.html)
- [Firebase Test Lab — iOS (XCTest)](https://firebase.google.com/docs/test-lab/ios/get-started)
- 依存する兄弟項目：**device-cloud-provider-abstraction**（live の継ぎ目）、**aws-device-farm-submitter**
  （batch の package 化）

[English](BE-0303-xcuitest-live-real-grid-verification.md) · **日本語**

# BE-0303 — XCUITest live デバイスクラウド経路の実グリッド検証

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0303](BE-0303-xcuitest-live-real-grid-verification-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0303") |
| トピック | デバイスクラウド実行 |
<!-- /BE-METADATA -->

## はじめに

`drivers/xcuitest_live.py` の `WebDriverClient` と `XcuitestLiveDriver`
（[BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md)）は、Appium に
対する device-cloud 経路を駆動します。この経路は W3C（World Wide Web Consortium）WebDriver プロトコルで
話し、[BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction-ja.md)
の device provider の継ぎ目に対応します。この[ドライバ](../../docs/ja/glossary.md#driver-backend-actuator-platform)
モジュール自身の docstring は、両方のクラスが「ネットワーク境界で fake に置き換えられているため、ゲートに
grid は不要」と述べています。そのためすべてのテストは、合成された transport を駆動するだけで、実際の
Appium grid は一度も使いません。

この backend が依存する実際のワイヤ形式、セッションのライフサイクル、`mobile:` コマンドの意味論を検証する
CI レーンは、ありません。本項目は、そのレーンを1つ追加します。

## 動機

合成された transport が証明するのは、`XcuitestLiveDriver` が自身のテストから渡された WebDriver の JSON を
組み立て、パースできることです。これはドライバの内部ロジックに対する、実質的なカバレッジです。

一方で、次の3点については何も証明しません。

- 実際の Appium サーバが、その JSON を受け付けるか。
- 実際の grid のタイミングの下で、セッションの作成や破棄がドライバの想定どおりに振る舞うか。
- 実際に使われている grid provider が、ドライバの依存する `mobile:` の拡張コマンドを、クライアントコードの
  想定どおりにサポートしているか。

この経路は、実際の device-cloud のハードウェアへの opt-in な経路として存在します
（[BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction-ja.md)）。
この経路が話すために存在するプロトコルそのものをモックしても、本当に重要な性質は検証できません。その性質
とは、実際の grid に対して動くかどうかです。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **CI 向けの実際の Appium ターゲット**：実際の Appium サーバを立てるか、既存のものに接続します。
  WebDriver のワイヤ契約の検証には、Simulator やエミュレータに対するローカルインスタンスで足ります。
  実際のクラウド grid は、ローカル経路が実証されたあとの、さらなる任意のステップです。
- **API キーまたは環境でゲートしたライブセッションテスト**：その Appium ターゲットに対して、実際の
  セッション作成 → 操作 → 破棄という一連の流れで、`XcuitestLiveDriver` を駆動します。ターゲットが設定
  されていないときは、テストをスキップします。
- **ドライバが実際に使う `mobile:` の拡張コマンドをカバーする**：基本的なセッションのライフサイクル
  だけでは足りません。これらのコマンドこそが、Appium のドライババージョン間でもっとも乖離しやすいからです。
- **まずゲート対象外とする**：
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
  の前例に従い、新しいジョブをまず CI のシグナルとして着地させ、必須化はそのあとで検討します。実際の
  Appium や grid への依存は、既存の conformance suite よりも、セットアップとフレーキーさのリスクが大きい
  ためです。

## 検討した代替案

- **WebDriver の JSON 構築がユニットテストされていることを根拠に、合成 transport のテストを信頼する**：
  JSON を正しく構築できても、実際の Appium サーバがそれを受け付けるかは何も語りません。その受け入れ可否
  こそが、この backend が本来主張していることです。
- **具体的な device-cloud provider がこの経路を採用するまで、実検証を先送りする**：この経路は、device
  provider の継ぎ目を通じて今日すでに到達可能です。特定の provider 統合が着地するまで未検証のままにすると、
  その間の回帰を捕まえるレーンがなく、静かに出荷されます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] CI 向けに、実際のローカル Appium ターゲットを立てる、または既存のものに接続する。
- [ ] Appium ターゲットに対する gated なライブセッションテスト（作成 → 操作 → 破棄）を追加する。
- [ ] ドライバが依存する `mobile:` の拡張コマンドをカバーする。
- [ ] ゲート対象外のシグナルとして CI に組み込む。

## 参考

- [BE-0238 — iOS device-cloud execution](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md)
- [BE-0236 — Device-cloud provider abstraction](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction-ja.md)
- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- `bajutsu/drivers/xcuitest_live.py`（`WebDriverClient`、`XcuitestLiveDriver`）、
  `tests/test_xcuitest_live.py`

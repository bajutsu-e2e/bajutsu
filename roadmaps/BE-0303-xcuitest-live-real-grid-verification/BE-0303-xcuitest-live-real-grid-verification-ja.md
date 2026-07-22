[English](BE-0303-xcuitest-live-real-grid-verification.md) · **日本語**

# BE-0303 — XCUITest live デバイスクラウド経路の実グリッド検証

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0303](BE-0303-xcuitest-live-real-grid-verification-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0303") |
| トピック | デバイスクラウド実行 |
<!-- /BE-METADATA -->

## はじめに

`drivers/xcuitest_live.py` の `WebDriverClient` と `XcuitestLiveDriver`
（[BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md)）は、
[BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction-ja.md)
の device provider の継ぎ目のために、W3C（World Wide Web Consortium）WebDriver プロトコル越しに Appium に対する device-cloud
経路を駆動します。このドライバモジュール自身の docstring は、両方とも「ネットワーク境界で fake に
置き換えられているため、ゲートに grid は不要」と述べており、すべてのテストは合成された transport を
駆動するだけで、実際の Appium grid は一度も使いません。この backend が依存する実際のワイヤ
形式、セッションのライフサイクル、`mobile:` コマンドの意味論を検証する CI レーンはありません。
本項目は CI レーンを追加します。

## 動機

合成された transport は、`XcuitestLiveDriver` が自身のテストから渡された WebDriver の
JSON をまさしく組み立て、パースできることを証明します。この証明はドライバの内部ロジックに対する
実質的なカバレッジです。しかし、実際の Appium サーバがその JSON を本当に受け付けるか、
実際の grid のタイミングの下でセッションの作成や破棄がドライバの想定どおりに振る舞うか、
ドライバが依存する `mobile:` の拡張コマンドが実際に使われている grid provider 上でクライアント
コードの想定どおりにサポートされているかについては何も証明しません。この経路はまさに、実際の
device-cloud のハードウェアへの opt-in な経路として存在します
（[BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction-ja.md)）。
この経路が話すために存在するまさにそのプロトコルをモックしても、本当に重要な性質までは検証できません。
その性質とは、実際の grid に対して本当に動くかどうかです。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **CI 向けの実際の Appium ターゲット**：実際の Appium サーバを立てます（または既存のものに接続
  します）。Simulator/エミュレータに対するローカルインスタンスで WebDriver のワイヤ契約を検証する
  には十分であり、実際のクラウド grid はローカル経路が実証されたあとの、さらなる任意のステップ
  です。
- **API キーまたは環境で gate したライブセッションテスト**：Appium ターゲットに対して、
  実際のセッション作成 → 操作 → 破棄という一連の流れで `XcuitestLiveDriver` を駆動します。
  ターゲットが設定されていないときはスキップします。
- **ドライバが実際に使う `mobile:` の拡張コマンドをカバーする**：基本的なセッションのライフ
  サイクルだけでなく、これらのコマンドこそが Appium のドライババージョン間でもっとも乖離しやすい
  ためです。
- **まずゲート対象外とする**：
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
  の前例に従い、新しいジョブをまず CI のシグナルとして着地させ、必須化はそのあとで検討します。
  実際の Appium/grid への依存は、既存の conformance suite よりもセットアップとフレーキーさの
  リスクが大きいためです。

## 検討した代替案

- **WebDriver の JSON 構築がユニットテストされていることを根拠に、合成 transport のテストを
  信頼する**：正しい JSON 構築は、実際の Appium サーバがそれを受け付けるかどうかについては何も
  語りません。その受け入れ可否こそがこの backend が本来主張していることです。
- **具体的な device-cloud provider がこの経路を採用するまで、実検証を先送りする**：この経路は
  device provider の継ぎ目を通じて今日すでに到達可能です。特定の provider 統合が着地するまで
  未検証のまま放置すれば、その間の回帰はどのレーンにも捕まらず静かに出荷されます。

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

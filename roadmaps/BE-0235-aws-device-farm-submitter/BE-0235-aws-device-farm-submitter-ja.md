[English](BE-0235-aws-device-farm-submitter.md) · **日本語**

# BE-0235 — AWS Device Farm batch submitter

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0235](BE-0235-aws-device-farm-submitter-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0235") |
| トピック | Platform expansion (Android / Web / Flutter) |
<!-- /BE-METADATA -->

## はじめに

この項目は、**AWS Device Farm** の **custom test environment** を使って Bajutsu の Android シナリオを
実機で走らせます。*device-cloud-provider-abstraction* の継ぎ目が扱う live なデバイスの provider とは違い、
Device Farm は **batch** のサービスです。ネットワーク越しに駆動するデバイスを貸すのではなく、*あなたの
コマンドをそのホスト上で*走らせます。そのホストにはすでに `adb` が予約済みのデバイスへ接続されています。
したがってここでの成果物は実行時の provider ではなく、**CI 側の submitter** です。Bajutsu とシナリオを
package にまとめ、`bajutsu run --backend adb` を走らせる test spec の YAML と一緒にアップロードし、artifact
を回収します。submitter は決定的コアから意図的に切り離します。

## 動機

AWS Device Farm は企業でよく使われるデバイスクラウドで、その custom test environment は Bajutsu の Android
backend にほぼあつらえたようです。spec の各フェーズはデバイスのホスト上で任意のシェルコマンドを走らせ、
`pip install` で依存を入れて Python の runtime を選べ、予約済みのデバイスに対する `adb` を露出します。
Android backend は「`adb` がデバイスへ接続されたホスト」さえあれば動きます。それこそが Device Farm の
custom environment での実行が提供するものです。したがって Android シナリオは、コアをほとんど、あるいは
まったく変えずにそこで走らせられるはずで、作業は package 化と提出のつなぎです。

これは live な provider とは別のトポロジで、実行時の provider の継ぎ目に押し込むと抽象が漏れます（取得
すべきネットワーク上のデバイスがなく、Bajutsu はアップロードされる積荷だからです）。これを別立ての
submitter として、コアから切り離しておくのは意図的です。

- 提出の仕掛け（AWS の SDK や CLI、認証情報、アップロード、ポーリング、artifact のダウンロード）は非決定的
  で provider に結合しており、`run` と CI の合否判定の経路に入れてはなりません。
- Bajutsu は Device Farm の*内側で*、他のどこと同じように動きます。同じ決定的コアで、機械チェック可能な
  アサーションから同じ合否が出ます。submitter はそこへ運び、戻ってくるだけです。

## 詳細設計

submitter は CI 側のツール（スクリプトやワークフローに Device Farm の test spec の YAML を添えたもの）で、
任意の extra（たとえば `bajutsu[aws]`）として、あるいは `.github/` の CI のつなぎとして出荷し、`run` には
配線しません。

1. **package 化** — Bajutsu のソースや wheel、target の config、シナリオを Device Farm のテスト package に
   束ねる。
2. **test spec** — 各フェーズで依存をインストールし（`devicefarm-cli use python …`、`pip install`）、`test`
   フェーズで `bajutsu run --backend adb <scenarios>` を走らせ、`post_test` で `runs/` を
   `$DEVICEFARM_LOG_DIR` へコピーして artifact を回収する spec YAML。
3. **serial 解決の PoC** — `adb.resolve_serial()` が Device Farm のホストに接続されたデバイスを拾うことを
   確認する（予約済みのデバイスはすでに `adb devices` に現れるはず）。これが唯一の経験的な未知で、磨き込み
   の*前に*検証する。
4. **提出と回収** — AWS の SDK や CLI でアップロードし、実行をポーリングし、artifact とレポートを
   ダウンロードする。合否は Device Farm の分類ではなく Bajutsu 自身の manifest から出す。

記述して扱うべき batch トポロジの性質があります。1 回の実行には **150 分のハード上限**があり、Appium の
コマンド 1 件ごとのタイムアウトは生 adb の経路には適用されません。`.aab` は受け付けません（APK のみ）。
iOS の custom mode には追加の制約があります（兄弟項目の *ios-device-cloud-execution* が扱い、ここでは
扱いません）。Device Farm 上での生 adb アクセスは、第一級の保証ではなくホストのツールチェインの副産物です
（第一級の経路は Appium です）。submitter はこれを記述し、将来の Device Farm の変更で黙って壊れないように
します。

### 作業分解（MECE）

1. **serial 解決の PoC** — showcase の Android シナリオを 1 本走らせ、`resolve_serial` がホスト上の予約済み
   デバイスを見つけることを示す最小の spec。他のすべてはこれを前提にする。
2. **package builder** — アップロード用に Bajutsu の積荷（ソースや wheel、config、シナリオ）を組み立てる。
3. **test spec のテンプレート** — 依存をインストールして `bajutsu run --backend adb` を走らせ、artifact を
   `$DEVICEFARM_LOG_DIR` へ収める install / pre_test / test / post_test の spec。
4. **提出と回収のツール** — アップロード、ポーリング、ダウンロード、Bajutsu の合否の報告を行う SDK や CLI の
   ラッパー。`run` から切り離し、`bajutsu[aws]` や CI のつなぎの裏に置く。
5. **ドキュメント** — `docs/`（両言語）に AWS の手順を置く。batch のモデル、生 adb の注意、150 分の上限、
   APK のみ、を記す。

### prime directive への適合

- **AI をゲートに入れない。** Bajutsu は Device Farm の内側で変わらず動き、合否はそのアサーションから出ます。
  submitter はどこにもモデルを足しません。
- **決定性優先。** submitter は決定的コアの外側のオーケストレーションで、それが起こす実行はローカルと同じ
  決定的な実行です。
- **app 非依存。** シナリオと target の config は変わらず、変わるのは配送の仕組みだけで、それはコアの外に
  あります。

## 検討した代替案

- **Device Farm を実行時の `DeviceProvider`（live の継ぎ目）としてモデル化する。** 取得すべきネットワーク上の
  デバイスはなく、Bajutsu はホスト上で動く積荷です。却下し、batch は実行時の継ぎ目ではなく CI 側の submitter
  に置きます。
- **生 adb ではなく Device Farm 内蔵の Appium を使う。** 可能ですが、Appium を話す backend が必要になり、
  既存の adb backend をほぼ無改造で再利用する利点を捨てます。Android の経路では却下します（Appium の経路は
  iOS の兄弟項目に関わります）。
- **提出を `bajutsu run` に組み込む。** AWS の SDK や認証情報やポーリングをコアへ引き込みます。却下し、任意の
  extra の裏で、切り離した CI 側のツールに保ちます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] serial 解決の PoC（予約済みデバイスが DF のホストで `resolve_serial` から見える）
- [ ] package builder（Bajutsu の積荷 + config + シナリオ）
- [ ] test spec のテンプレート（install / pre_test / test / post_test、artifact を `$DEVICEFARM_LOG_DIR` へ）
- [ ] 提出と回収のツール（アップロード / ポーリング / ダウンロード / 報告、切り離し、`bajutsu[aws]`）
- [ ] ドキュメント（AWS の手順：batch のモデル、生 adb の注意、150 分上限、APK のみ）

## 参考

- [AWS Device Farm — custom test environments](https://docs.aws.amazon.com/devicefarm/latest/developerguide/custom-test-environments.html)
- [AWS Device Farm — custom test spec](https://docs.aws.amazon.com/devicefarm/latest/developerguide/custom-test-environment-test-spec.html)
- [AWS Device Farm — service limits](https://docs.aws.amazon.com/devicefarm/latest/developerguide/limits.html)
- [BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md)
- [BE-0208 — Android emulator E2E in CI](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md)
- 関連する兄弟項目：**device-cloud-provider-abstraction**（この項目が中ではなく隣に据える live の継ぎ目）

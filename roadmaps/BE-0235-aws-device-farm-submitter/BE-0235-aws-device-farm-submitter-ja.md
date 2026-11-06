[English](BE-0235-aws-device-farm-submitter.md) · **日本語**

# BE-0235 — AWS Device Farm batch submitter

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0235](BE-0235-aws-device-farm-submitter-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0235") |
| 実装 PR | [#1040](https://github.com/bajutsu-e2e/bajutsu/pull/1040) |
| トピック | デバイスクラウド実行 |
<!-- /BE-METADATA -->

## はじめに

この項目は、**AWS Device Farm** の **custom test environment** を使って、Bajutsu の Android シナリオを
実機で走らせます。

*device-cloud-provider-abstraction* の継ぎ目が扱うのは、live なデバイスの provider です。Device Farm は
その仲間ではなく、**batch** のサービスです。ネットワーク越しに駆動するデバイスを貸しません。
*利用者のコマンドをホスト上で*走らせます。そのホストには、予約済みのデバイスへ接続された `adb` が
すでにあります。

したがって成果物は、実行時の provider ではなく、**CI 側の submitter** です。submitter は、Bajutsu と
シナリオを package にまとめます。`bajutsu run --backend adb` を走らせる test spec の YAML と一緒に
アップロードし、artifact を回収します。submitter は、決定的コアから意図的に切り離します。

## 動機

AWS Device Farm は企業でよく使われるデバイスクラウドです。その custom test environment は、Bajutsu の
Android backend にほぼあつらえたような環境です。spec の各フェーズは、デバイスのホスト上で任意のシェル
コマンドを走らせます。`pip install` で依存を入れ、Python の runtime も選べます。予約済みのデバイスに
対する `adb` も使えます。

Android backend は、「`adb` がデバイスへ接続されたホスト」さえあれば動きます。Device Farm の custom
environment での実行は、まさにそれを提供します。Android シナリオは、コアをほとんど、あるいはまったく
変えずにそこで走らせられるはずです。作業は、package 化と提出のつなぎです。

このトポロジは、live な provider とは異なります。実行時の provider の継ぎ目に押し込むと、抽象が漏れます。
取得すべきネットワーク上のデバイスがなく、Bajutsu がアップロードされる積荷だからです。別立ての
submitter として、コアから切り離しておくのは、次の 2 つの理由から意図的です。

- 提出の仕掛けは、非決定的で provider に結合しています。AWS の SDK や CLI、認証情報、アップロード、
  ポーリング、artifact のダウンロードがこれにあたります。`run` と CI の合否判定の経路には入れません。
- Bajutsu は Device Farm の*内側で*、他のどこと同じように動きます。決定的コアは同じで、機械チェック
  可能なアサーションから同じ合否が出ます。submitter は、Bajutsu を運び、結果を持ち帰るだけです。

## 詳細設計

submitter は CI 側のツールです。スクリプトやワークフローに、Device Farm の test spec の YAML を添えた
ものです。任意の extra（たとえば `bajutsu[aws]`）として、あるいは `.github/` の CI のつなぎとして出荷します。
`run` には配線しません。構成は次の 4 つです。

1. **package 化。** Bajutsu のソースや wheel、target の config、シナリオを、Device Farm のテスト
   package に束ねます。
2. **test spec。** 次の 3 つを行う spec YAML です。
   - 各フェーズで依存をインストールします（`devicefarm-cli use python …`、`pip install`）。
   - `test` フェーズで `bajutsu run --backend adb <scenarios>` を走らせます。
   - `post_test` フェーズで `runs/` を `$DEVICEFARM_LOG_DIR` へコピーし、artifact を回収します。
3. **serial 解決の PoC。** `adb.resolve_serial()` が、Device Farm のホストに接続されたデバイスを拾う
   ことを確認します。予約済みのデバイスは、すでに `adb devices` に現れるはずです。これが唯一の経験的な
   未知であり、磨き込みの*前に*検証します。
4. **提出と回収。** AWS の SDK や CLI でアップロードし、実行をポーリングし、artifact とレポートを
   ダウンロードします。合否は、Device Farm の分類ではなく、Bajutsu 自身の manifest から出します。

batch トポロジには、記述して扱うべき性質があります。

- 1 回の実行には、**150 分のハード上限**があります。
- Appium のコマンド 1 件ごとのタイムアウトは、生 adb の経路には適用されません。
- `.aab` は受け付けません（APK のみ）。
- iOS の custom mode には、追加の制約があります。兄弟項目の *ios-device-cloud-execution* が扱い、
  ここでは扱いません。

Device Farm 上での生 adb アクセスは、ホストのツールチェインの副産物です。第一級の保証ではありません
（第一級の経路は Appium です）。submitter はこの事実を記述し、将来の Device Farm の変更で黙って
壊れないようにします。

### 作業分解（MECE）

1. **serial 解決の PoC。** showcase の Android シナリオを 1 本走らせる最小の spec です。
   `resolve_serial` がホスト上の予約済みデバイスを見つけることを示します。他のすべてはこれを前提に
   します。
2. **package builder。** アップロード用に、Bajutsu の積荷（ソースや wheel、config、シナリオ）を
   組み立てます。
3. **test spec のテンプレート。** install / pre_test / test / post_test の spec です。依存を
   インストールして `bajutsu run --backend adb` を走らせ、artifact を `$DEVICEFARM_LOG_DIR` へ収めます。
4. **提出と回収のツール。** アップロード、ポーリング、ダウンロード、Bajutsu の合否の報告を行う SDK や
   CLI のラッパーです。`run` から切り離し、`bajutsu[aws]` や CI のつなぎの裏に置きます。
5. **ドキュメント。** `docs/`（両言語）に AWS の手順を置きます。batch のモデル、生 adb の注意、
   150 分の上限、APK のみ、を記します。

### prime directive への適合

- **AI をゲートに入れない。** Bajutsu は Device Farm の内側で変わらず動き、合否はそのアサーションから
  出ます。submitter は、どこにもモデルを足しません。
- **決定性優先。** submitter は、決定的コアの外側のオーケストレーションです。submitter が起こす実行は、
  ローカルと同じ決定的な実行です。
- **app 非依存。** シナリオと target の config は変わりません。変わるのは配送の仕組みだけで、その仕組みは
  コアの外にあります。

## 検討した代替案

- **Device Farm を実行時の `DeviceProvider`（live の継ぎ目）としてモデル化する。** 却下します。取得すべき
  ネットワーク上のデバイスはなく、Bajutsu はホスト上で動く積荷だからです。batch は、実行時の継ぎ目では
  なく、CI 側の submitter に置きます。
- **生 adb ではなく、Device Farm 内蔵の Appium を使う。** Android の経路では却下します。可能ですが、
  Appium を話す backend が必要になります。既存の adb backend をほぼ無改造で再利用する利点も捨てます。
  Appium の経路は、iOS の兄弟項目に関わります。
- **提出を `bajutsu run` に組み込む。** 却下します。AWS の SDK や認証情報やポーリングを、コアへ引き込む
  ためです。任意の extra の裏で、切り離した CI 側のツールに保ちます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] serial 解決の PoC — 仕組み（`pre_test: adb devices` と `--udid booted`）と文書化した手動手順を出荷。
  経験的な実行には実際の AWS アカウントが要るため、決定的ゲートからは意図的に外した手動手順とする
  （docs/ja/devicefarm.md）
- [x] package builder（`build_package`：Bajutsu の積荷 + config + シナリオ → zip）
- [x] test spec のテンプレート（`render_test_spec`：install / pre_test / test / post_test、artifact を `$DEVICEFARM_LOG_DIR` へ）
- [x] 提出と回収のツール（`submit_and_collect`：アップロード / ポーリング / ダウンロード / manifest からの合否、切り離し、`bajutsu[aws]`）
- [x] ドキュメント（AWS の手順：batch のモデル、生 adb の注意、150 分上限、APK のみ）

### 進捗ログ

- `scripts/devicefarm_submit.py` — CI 側のサブミッター。`render_test_spec`、`build_package`、
  `verdict_from_manifest`（Device Farm の分類ではなく Bajutsu 自身の `manifest.json` から合否を出します）、
  そして `DeviceFarmClient` / `Transfer` の継ぎ目の裏に置いた `submit_and_collect`（boto3 は遅延 import）。
  インメモリの fake に対して単体テストします（`tests/test_devicefarm_submit.py`）。
- `demos/showcase/devicefarm/testspec.yml` — コミット済みの参照用 spec。
- `.github/workflows/devicefarm.yml` — 手動でオプトイン（`workflow_dispatch`）、OIDC でゲートし、
  運用者がアカウントを接続するまで休止します。
- `docs/devicefarm.md`（と `docs/ja/`）、および `ci.md` の表の行。
- `pyproject.toml` の `aws = ["boto3>=1.34"]` extra。
- 2026-07-18：実際の AWS Device Farm の実機（`SM-S911U1`、Android 13）でエンドツーエンドに検証しました。
  `firstlook.yaml` で `bajutsu verdict: PASS (1/1)` となり、`adb devices` が予約デバイスを表示し
  `--udid booted` がそれを解決しました。これでシリアル解決の実証がもっていた唯一の経験的な未知が解消されます。
  この手動実行では、インメモリの fake では現れない Device Farm 固有の問題が 7 件見つかり、いずれもサブミッター側で
  修正しました（PR #1040）。出力の zip を自分自身に取り込まないこと、アップロード失敗時に Device Farm の理由を
  表に出すこと、`tests/` が入るようにパッケージを zip のルートへ収めること、ルートに `requirements.txt` を合成すること、
  zip でない成果物を展開せずに保存すること、uv で Python 3.13 を用意すること（ホストは 3.12 までなので、Device Farm が
  3.13 を提供したら消しやすいよう `_python_bootstrap_commands` に隔離しました）、そして `appPath` を持たない
  `demos/showcase/devicefarm/showcase.devicefarm.config.yaml` で事前インストール済みのアプリに対して実行することです。

## 参考

- [AWS Device Farm — custom test environments](https://docs.aws.amazon.com/devicefarm/latest/developerguide/custom-test-environments.html)
- [AWS Device Farm — custom test spec](https://docs.aws.amazon.com/devicefarm/latest/developerguide/custom-test-environment-test-spec.html)
- [AWS Device Farm — service limits](https://docs.aws.amazon.com/devicefarm/latest/developerguide/limits.html)
- [BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md)
- [BE-0208 — Android emulator E2E in CI](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md)
- 関連する兄弟項目：**device-cloud-provider-abstraction**（この項目が中ではなく隣に据える live の継ぎ目）

[English](BE-XXXX-adb-clipboard-fidelity.md) · **日本語**

# BE-XXXX — adb クリップボードの実機での忠実性

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-adb-clipboard-fidelity-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Platform expansion (Android / Web / Flutter) |
| 関連 | [BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control-ja.md)、[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control-ja.md) は、デバイス制御
ファミリのエミュレータ対応部分を adb バックエンドに追加しました。`setLocation`（`emu geo fix` 経由）
と、クリップボード操作（`setClipboard` / `getClipboard` / `clearClipboard`。`cmd clipboard
set/get/clear-primary-clip` を土台にしています）です。バックエンドは `DC_SET_LOCATION` と
`DC_CLIPBOARD` の両方を capability として宣言しているので、操作ごとの preflight（BE-0212）は
クリップボードのステップとクリップボードの読み戻し assertion を受け入れます。

クリップボードの側は実機で動きません。google_apis API 34 のエミュレータイメージでは、`cmd clipboard
set/get-primary-clip` が `No shell command implementation` を返します。`clipboard` のシステムサービス
自体は存在し `cmd -l` にも載りますが、これらのビルダが叩くシェルコマンドのインタフェースを実装して
いないのです。そのため `setClipboard` は何も書き込まないまま黙って成功し、クリップボードの読み戻しは
空を返します。本項目は、宣言している `DC_CLIPBOARD` capability を実態に合わせます。実機で動く仕組みで
クリップボードを操作するか、あるいは capability を狭めて、クリップボードのステップを assertion の時点で
失敗させるのではなく preflight で拒否するようにします。

## 動機

バックエンドが宣言していながら実現できない capability は、宣言していない capability よりたちが悪い
ものです。preflight（BE-0212）は、まさに未対応のステップを使うシナリオを実行の前に速く明確な理由で
失敗させるために存在します。実行の奥深く、満たしようのない assertion で失敗させないためです。adb の
`DC_CLIPBOARD` はこの約束を裏切ります。クリップボードに値を書き込んで読み戻しを検証するシナリオは
preflight を通過して実行に入り、そのうえで `clipboard was ''` という assertion で失敗します。これは
バックエンドの制約ではなくアプリの不具合のように見えてしまいます。

このギャップが見えていなかったのは、BE-0211 のクリップボード往復が、注入した **fake の `run`** に対して
しか実行されていなかったためです（`test_android_device_control.py` の `fake_run` は文字列を dict に
格納して返します）。fake はコマンドの**ビルダ**と委譲の配線を証明しますが、デバイスがそのコマンドを
実際に受け付けることは証明しません。このギャップは、BE-0208 のデバイス制御レーンのスライスを出荷する
過程で実機上で見つかりました（[PR #934](https://github.com/bajutsu-e2e/bajutsu/pull/934)）。この PR は
クリップボードの側を落とし、`setLocation` だけを出荷しています。

これは一つのエミュレータイメージにとどまる問題ではありません。デバイス制御ファミリはバックエンドを
またぐ面であり（idb は `simctl` でフルセットを、adb はエミュレータ対応部分を担います）、assertion の
一種（`clipboard`）が読み戻しの動作に依存しています。`DC_CLIPBOARD` を「宣言しているのに壊れている」
まま放置すると、このツールをバックエンド非依存に保つ capability モデルへの信頼が損なわれます。これは `CLAUDE.md` の「platform is a backend」という前提にあたるものであり、アプリごとの差異（`targets.<name>`）を扱う原則 3 とは別の原則です。

## 詳細設計

### 作業分解（MECE）

1. **実機のベースラインを確立する。** プロジェクトが対象とする API レベル（少なくとも CI レーンの
   x86_64 API 34 とローカルの arm64 API 34）にわたって、シェル uid のプロセスが実際に駆動できる
   クリップボードの仕組みを見極めます。`cmd clipboard` のサブコマンド、生のパーセルによる
   `service call clipboard`、アプリ内の小さなレシーバへの broadcast のいずれが、どこで動くのかを
   記録します。ユニット 2 の判断が、一つのイメージの挙動ではなく証拠に基づくようにするためです。

2. **判断する。修復か、狭めるか。** ユニット 1 に基づいて、次のどちらかを選びます。
   - *修復* — `cmd clipboard` のビルダを実機で動く仕組みに置き換えます。`DeviceControl` の
     インタフェース（`set_clipboard` / `get_clipboard` / `clear_clipboard`）と `DC_CLIPBOARD`
     capability はそのままにして、シナリオに手を入れずに済ませます。
   - *狭める* — adb バックエンドの宣言する capability から `DC_CLIPBOARD` を外し、preflight が
     クリップボードのステップを明確な理由で拒否するようにします。あわせて adb の `DeviceControl` の
     クリップボードのメソッドが `UnsupportedAction` を送出するようにします（他の未対応の操作が
     すでに使っている実行時のバックストップです）。idb は capability を保ちます。

   判断は本項目に記録し、capability の面が変わる場合は `DESIGN.md` / `docs/architecture.md` にも
   記録します（BE-0113）。

3. **選んだ方向を実装する。** `bajutsu/adb.py`、`bajutsu/platform_lifecycle.py`、
   `bajutsu/drivers/adb.py` に手を入れ、変更を adb バックエンドの内側にとどめます（バックエンドを
   またぐ変更は生じさせません）。

4. **fake だけでなく、デバイスに対してテストする。** 今回のギャップを捕まえられたはずのカバレッジを
   追加します。起動したエミュレータで実際のクリップボード経路を実行するテスト（他の実機チェックと
   同じく、速い Linux ゲートには載せないようガードします）か、ユニット 2 で狭めた場合は、preflight が
   adb でクリップボードのステップを拒否しメソッドが送出することを確認する、ゲート内のテストです。
   fake ランナーのテストは残しますが、実機の証明の代わりにはしません。

5. **e2e レーンと整合させる。** ユニット 2 で*修復*した場合は、BE-0208 の `device_android` シナリオを
   拡張してクリップボードを書き込み読み戻します（PR #934 が求めていた強い assertion です）。そして
   復旧したカバレッジを BE-0208 に記録します。*狭めた*場合は、`device_android` を `setLocation` だけの
   ままにし、クリップボードのステップが adb では設計上未対応であることを記録します。

## 検討した代替案

- **現状のまま（宣言しているが壊れている）にする。** 却下します。preflight の約束に反し、実機では
  アプリの不具合のように見えます。capability モデルの要点は、バックエンドごとに速く正直に失敗する
  ことにあります。
- **BE-0208 のレーン PR の中で直す。** 却下します。BE-0208 は CI の e2e レーンについての項目であり、
  adb のデバイス制御の実装そのものではありません。あそこでドライバに手を入れるとレーンをまたいで
  しまう（変更は BE-0211 の面に属します）ので、レーン PR は `setLocation` だけを出荷し、クリップボード
  は本項目に先送りしました。
- **root 前提の `service call clipboard` によるクリップボード。** ユニット 1 / 2 の候補となる仕組みで
  あって、ここでの判断ではありません。生のパーセル呼び出しは API レベルやエンコーディングをまたぐと
  脆いので、狭める選択肢より優先して採用する前に、ユニット 1 のベースラインが要ります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] ユニット 1 — 対象の API レベル / ABI にわたって、実機のクリップボードのベースラインを確立する。
- [ ] ユニット 2 — 修復か狭めるかを判断し、その判断を記録する（capability の面が変わる場合は
  DESIGN / architecture も）。
- [ ] ユニット 3 — 選んだ方向を adb バックエンドの内側で実装する。
- [ ] ユニット 4 — ギャップを捕まえられたはずの、実機（または preflight での拒否）のカバレッジを追加する。
- [ ] ユニット 5 — 結果に合わせて BE-0208 の `device_android` レーンのシナリオを整合させる。

## 参考

[BE-0211 — Android のデバイス制御](../BE-0211-android-device-control/BE-0211-android-device-control-ja.md)、
[BE-0208 — CI での Android 実機 e2e](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)、
[PR #934](https://github.com/bajutsu-e2e/bajutsu/pull/934)、
`bajutsu/adb.py`、`bajutsu/platform_lifecycle.py`、`bajutsu/drivers/adb.py`、
`bajutsu/capability_preflight.py`

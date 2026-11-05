[English](BE-0237-firebase-device-streaming-adapter.md) · **日本語**

# BE-0237 — Firebase Test Lab / Device Streaming adapter

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0237](BE-0237-firebase-device-streaming-adapter-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0237") |
| トピック | デバイスクラウド実行 |
<!-- /BE-METADATA -->

## はじめに

この項目は、*device-cloud-provider-abstraction* の継ぎ目の上に、最初の具体的な **device provider** を
足します。**Firebase の Android Device Streaming** を通じて、Google がホストする実機に対し、Bajutsu の
Android シナリオを走らせます。Device Streaming は、予約したデバイスを「adb over SSL」として見せます。

Android backend は、到達可能な `adb` の serial なら何でも駆動できます。`IP:port` 形式の `adb connect`
対象も含みます。そのため adapter の仕事は狭く、次の 4 つだけです。

- デバイスを予約します。
- adb over SSL の接続を確立します。
- ランナーへ serial を渡します。
- 後で予約を解放します。

## 動機

Firebase は、最初の対象として自然です。多くのチームがすでに使っています。Device Streaming は、live
トポロジの継ぎ目が求める形を、まさに提供します。adb 越しに到達できる、予約済みの実機です。Firebase を
最初に着地させると、抽象を凍結する前に、**provider の継ぎ目を実サービスで実証する**ことにもなります。
これは、foundation 項目が求める、PoC を先行させる順序です。

調査で、要点が 1 つ分かりました。スコープを読み違えないよう、ここに記録します。**Firebase Test Lab 本体は、
Bajutsu のドライバをホストできません。**

- Test Lab が受け付けるのは、固定のテストタイプだけです。Instrumentation（Espresso / UI Automator）、
  Robo、Game Loop で、`gcloud firebase test android run` を通じて APK / AAB としてアップロードします。
- 任意の Python ドライバをアップロードし、デバイスを外側から駆動する経路はありません。
- Test Lab は閉じたテストランナーであり、開かれた実行のサンドボックスではありません。

Google のクラウド上で live な adb デバイスに至る現実的な経路は、**別製品**の Android Device Streaming
（Android Studio のデバイス予約）です。これは、「adb を使うあらゆるツール」から使える「adb over SSL」の
接続を、明示的に付与します。この項目は Device Streaming を対象とします。Test Lab 本体は、包めるふりを
せず、制約として記述します。

## 詳細設計

`DeviceProvider`（`kind` は `firebase-streaming`）を、**任意の extra**（たとえば
`pip install "bajutsu[firebase]"`）として実装します。extra は、Firebase と Android Device Streaming の
CLI を包みます。provider の操作は 2 つです。

- **acquire(target)**：デバイスを予約します。device model、API level、project は、target の
  `deviceProvider` config から取ります。adb over SSL のトンネルを確立します。ストリームされたデバイスが
  `adb devices` で準備完了として現れるまで待ちます。ストリームされた `IP:port` の対象を serial に持つ
  `DeviceLease` を返します。
- **release()**：トンネルを畳み、予約を終了します。課金も止まります。

その先は、すべて変わりません。`make_driver("adb", serial)` は、ストリームされたデバイスを、ローカルの
emulator を駆動するのと同じように駆動します。ドライバに必要なのは serial だけだからです。

foundation 項目が定義するクラウド差異のフックが、ここに効きます。

- デバイスはすでに起動済みなので、起動待ちを省きます。
- アプリのインストールは、トンネル越しの通常の `adb install` で走ります。Device Streaming は事前
  インストールしないためです。
- ストリームされたデバイスは、emulator 限定の device control プリミティブの一部を欠きます。provider が
  それらを非対応として宣言し、preflight が明快に切り落とします。

### 作業分解（MECE）

1. **adapter の骨組み。** 継ぎ目の registry の下に `firebase-streaming` の provider を登録し、
   `bajutsu[firebase]` の extra の裏で出荷します。extra の import は遅延させ、ゲートをクラウド非依存に
   保ちます。
2. **予約と adb over SSL。** Device Streaming の CLI で予約し、トンネルを確立します。ストリームされた
   serial を解決し、`adb devices` を通じて準備完了を確認します。確認は条件待ちであり、固定 sleep では
   ありません。
3. **lease のライフサイクル。** 成功時、失敗時、中断時のいずれでも `release()` を確実に行い、予約を
   漏らしません（課金の安全性）。
4. **クラウド差異のフック。** 起動待ちの省略、通常の `adb install`、device control の capability 縮退を、
   foundation の `RunEnvironment` フックを通じて配線します。
5. **config。** provider 固有のフィールド（project、device model、API level）を adapter が検証します。
   未知や欠落は、明快なエラーにします。
6. **テスト。** CLI とトンネルの境界を fake にします。外部プロセスやネットワークは、許容されるモック点
   です。acquire から serial、release までと、準備完了が条件であること、失敗時の lease 後始末を検証します。
   ゲートに live の Firebase は持ち込みません。
7. **ドキュメント。** `docs/`（両言語）に Firebase の手順を置きます。Device Streaming が対応する経路で
   あり、Test Lab 本体は包めないことを明記します。

### prime directive への適合

- **AI をゲートに入れない。** adapter は、予約と接続と解放だけを行います。モデルは介在せず、決定的判定
  に影響しません。
- **決定性優先。** 準備完了は、`adb devices` に対する条件であり、sleep ではありません。ストリームされた
  デバイスの駆動は、ローカルと同じだけ再現可能です。
- **app 非依存。** Firebase 固有の事柄は、すべて target の `deviceProvider` config と任意の adapter に
  置きます。ドライバとランナーは変わりません。

## 検討した代替案

- **Firebase Test Lab 本体を包む（Instrumentation / Robo / Game Loop）。** 却下し、代わりに制約として
  記述します。包むには、Bajutsu の実行を Instrumentation のテスト APK に詰め込み、外側から駆動する
  モデルを捨てる必要があります。これは、決定的コアの設計と、自然言語シナリオを中心に据える方針に反します。
- **adapter を任意の extra ではなくコアに置く。** 却下します。Google Cloud の SDK や CLI を、決定的な
  依存の閉包へ引き込むためです。`bajutsu[firebase]` の裏で出荷します。
- **Device Streaming を batch としてモデル化する（AWS Device Farm のように）。** 却下します。Device
  Streaming は*遠隔実行*ではなく、*live な遠隔デバイス*です。batch としてモデル化すると、すでに噛み合う
  継ぎ目を捨てることになります。live のまま扱います。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] adapter の骨組み（`firebase-streaming`、`bajutsu[firebase]` extra、遅延 import）
- [ ] 予約と adb over SSL のトンネル、ストリームされた serial の解決
- [ ] lease のライフサイクル（成功 / 失敗 / 中断で漏れない release）
- [ ] クラウド差異のフック（起動待ち省略 / `adb install` / capability 縮退）
- [ ] config（provider のフィールドを検証、未知や欠落で明快に失敗）
- [ ] テスト（CLI とトンネルの境界を fake に）
- [ ] ドキュメント（Device Streaming の手順、Test Lab の制約の明記）

## 参考

- [Android Device Streaming (adb over SSL)](https://developer.android.com/studio/run/android-device-streaming)
- [Firebase Test Lab](https://firebase.google.com/docs/test-lab)
- [BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md)
- [BE-0082 — capability preflight check](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md)
- 依存する兄弟項目：**device-cloud-provider-abstraction**（この adapter が実装する継ぎ目）

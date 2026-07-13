[English](BE-0237-firebase-device-streaming-adapter.md) · **日本語**

# BE-0237 — Firebase Test Lab / Device Streaming adapter

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0237](BE-0237-firebase-device-streaming-adapter-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0237") |
| トピック | Platform expansion (Android / Web / Flutter) |
<!-- /BE-METADATA -->

## はじめに

この項目は、*device-cloud-provider-abstraction* の継ぎ目の上に、最初の具体的な **device provider** を
足します。**Firebase の Android Device Streaming** を通じて、Google がホストする実機に対して Bajutsu の
Android シナリオを走らせます。Device Streaming は予約したデバイスを「adb over SSL」として見せます。
Android backend はすでに到達可能な `adb` の serial なら何でも駆動でき、`IP:port` 形式の `adb connect`
対象も含みます。そのため adapter の仕事は狭く、デバイスを予約し、adb over SSL の接続を確立し、ランナーへ
serial を渡し、後で予約を解放するだけです。

## 動機

Firebase は最初の対象として自然です。多くのチームがすでに使っており、Device Streaming は live トポロジの
継ぎ目が求める形、すなわち adb 越しに到達できる予約済みの実機を、まさに提供します。Firebase を最初に
着地させることは、抽象を凍結する前に **provider の継ぎ目を実サービスで実証する**ことにもなります
（foundation 項目が求める、PoC を先行させる順序です）。

調査で分かった要点を、スコープを読み違えないよう記録します。**Firebase Test Lab 本体は Bajutsu のドライバ
をホストできません。** Test Lab が受け付けるのは固定のテストタイプ、すなわち Instrumentation
（Espresso / UI Automator）、Robo、Game Loop だけで、`gcloud firebase test android run` を通じて APK / AAB
としてアップロードします。任意の Python ドライバをアップロードし、デバイスを外側から駆動する経路はあり
ません。Test Lab は閉じたテストランナーであって、開かれた実行のサンドボックスではありません。したがって
Google のクラウド上で live な adb デバイスに至る現実的な経路は、**別製品**の Android Device Streaming
（Android Studio のデバイス予約）です。これは「adb を使うあらゆるツール」から使える「adb over SSL」の
接続を明示的に付与します。この項目は Device Streaming を対象とし、Test Lab 本体は包めるふりをせず、
制約として記述します。

## 詳細設計

`DeviceProvider`（`kind` は `firebase-streaming`）を、Firebase と Android Device Streaming の CLI を包む
**任意の extra**（たとえば `pip install "bajutsu[firebase]"`）として実装します。

- **acquire(target)**：デバイスを予約し（device model、API level、project は target の `deviceProvider`
  config から取る）、adb over SSL のトンネルを確立し、ストリームされたデバイスが `adb devices` で準備完了
  として現れるまで待ち、ストリームされた `IP:port` の対象を serial に持つ `DeviceLease` を返します。
- **release()**：トンネルを畳み、予約を終了します（課金を止めます）。

その先はすべて変わりません。`make_driver("adb", serial)` はストリームされたデバイスを、ローカルの emulator
を駆動するのと同じように駆動します。serial だけがドライバに必要なものだからです。foundation 項目が定義する
クラウド差異のフックがここに効きます。デバイスはすでに起動済みなので起動待ちを省き、アプリのインストールは
トンネル越しの通常の `adb install` で走り（Device Streaming は事前インストールしません）、ストリームされた
デバイスが欠く emulator 限定の device control プリミティブは非対応として宣言し、preflight が明快に切り
落とします。

### 作業分解（MECE）

1. **adapter の骨組み** — 継ぎ目の registry の下に `firebase-streaming` の provider を登録し、`bajutsu[firebase]`
   の extra の裏で出荷する。extra の import は遅延させ、ゲートをクラウド非依存に保つ。
2. **予約と adb over SSL** — Device Streaming の CLI で予約し、トンネルを確立し、ストリームされた serial を
   解決し、`adb devices` を通じて準備完了を確認する（条件待ちであって、固定 sleep ではない）。
3. **lease のライフサイクル** — 成功時、失敗時、中断時のいずれでも `release()` を確実に行い、予約を漏らさ
   ない（課金の安全性）。
4. **クラウド差異のフック** — 起動待ちの省略、通常の `adb install`、device control の capability 縮退を、
   foundation の `RunEnvironment` フックを通じて配線する。
5. **config** — provider 固有のフィールド（project、device model、API level）を adapter が検証し、未知や
   欠落は明快なエラーにする。
6. **テスト** — CLI とトンネルの境界を fake にし（外部プロセスやネットワークが許容されるモック点）、
   acquire から serial、release までと、準備完了が条件であること、失敗時の lease 後始末を検証する。ゲートに
   live の Firebase は持ち込まない。
7. **ドキュメント** — `docs/`（両言語）に Firebase の手順を置き、Device Streaming が対応する経路であり、
   Test Lab 本体は包めないことを明記する。

### prime directive への適合

- **AI をゲートに入れない。** adapter は予約と接続と解放だけを行い、モデルは介在せず、決定的判定に影響
  しません。
- **決定性優先。** 準備完了は `adb devices` に対する条件であって sleep ではなく、ストリームされたデバイスの
  駆動はローカルと同じだけ再現可能です。
- **app 非依存。** Firebase 固有の事柄はすべて target の `deviceProvider` config と任意の adapter に置き、
  ドライバとランナーは変わりません。

## 検討した代替案

- **Firebase Test Lab 本体を包む（Instrumentation / Robo / Game Loop）。** Bajutsu の実行を Instrumentation
  のテスト APK に詰め込み、外側から駆動するモデルを捨てる必要があります。決定的コアの設計と、自然言語
  シナリオを中心に据える方針に反します。却下し、代わりに制約として記述します。
- **adapter を任意の extra ではなくコアに置く。** Google Cloud の SDK や CLI を決定的な依存の閉包へ
  引き込みます。却下し、`bajutsu[firebase]` の裏で出荷します。
- **Device Streaming を batch としてモデル化する（AWS Device Farm のように）。** これは*遠隔実行*ではなく
  *live な遠隔デバイス*です。batch としてモデル化すると、すでに噛み合う継ぎ目を捨てることになります。
  live のまま扱います。

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

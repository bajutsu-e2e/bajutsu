[English](BE-0052-device-state-timezone-clipboard-shake.md) · **日本語**

# BE-0052 — デバイス状態プリミティブ: タイムゾーン・クリップボード・シェイク

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0052](BE-0052-device-state-timezone-clipboard-shake-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0052") |
| 実装 PR | [#206](https://github.com/bajutsu-e2e/bajutsu/pull/206), [#257](https://github.com/bajutsu-e2e/bajutsu/pull/257), [#378](https://github.com/bajutsu-e2e/bajutsu/pull/378) |
| トピック | シナリオ記述機能 |
| 関連 | [BE-0157](../BE-0157-shake-device-primitive/BE-0157-shake-device-primitive-ja.md)、[BE-0158](../BE-0158-timezone-device-primitive/BE-0158-timezone-device-primitive-ja.md)、[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) |
| 由来 | MagicPod |
<!-- /BE-METADATA -->

## はじめに

[BE-0035](../BE-0035-device-control-primitives/BE-0035-device-control-primitives-ja.md) は、最初のスライス（`background`、`overrideStatusBar`、`clearStatusBar`）を出荷しました。本項目は、そのあとに残ったデバイス状態プリミティブを扱います。タイムゾーンの固定、クリップボードへの値の仕込みと読み戻し、シェイクジェスチャ、背面化したアプリのレジュームです。

## 動機

実際のフローは、アプリ自身が制御しないデバイス状態に依存します。その状態を設定できないテストは、フローを決定的に検証できません。BE-0035 のあとに、次の 4 つの欠落が残りました。

- **タイムゾーン。** 日付に依存する UI（「今日」のヘッダ、カウントダウン、スケジュール画面）は、テストがデバイスのゾーンを固定できて初めて、複数のタイムゾーンにわたって検証できます。固定できなければ、結果は CI の実行場所によってぶれます。
- **クリップボード。** 貼り付けフロー（クーポンコード、共有リンク、別所からコピーしたワンタイムコード）では、テストがペーストボードへ値を仕込む必要があります。「コピー」操作の検証には、ペーストボードの読み戻しが必要です。BE-0035 が実装した `clearClipboard` はクリアだけを行います。値の仕込みと読み戻しが残っています。
- **シェイク。** 一部のアプリは、「取り消し」やデバッグメニューをシェイクジェスチャに結びつけています。現状、このジェスチャをトリガするステップがありません。
- **アプリのレジューム。** BE-0035 の `background` は、アプリを背面へ送ります。アプリをレジュームすることと、上限のある時間だけ背面化しておくことは、前面と背面の遷移のもう半分です。どちらも未実装です。

これらのプリミティブがないと、こうしたフローは非決定的な回避策に頼るか、そもそも自動化できません。

**競合の文脈（Maestro）。** Maestro は、標準で幅広いデバイス制御の語彙を備えています。次の語彙を含みます。`setAirplaneMode` / `toggleAirplaneMode`、`setOrientation`、`setLocation` / `travel`、`setPermissions`、`clearKeychain`、`clearState`、`pressKey`、`hideKeyboard`、`openLink` です。これに並ぶのは前提条件です。Bajutsu は中核部分を BE-0035 で実装済みです。残るプリミティブ（タイムゾーン、クリップボードの仕込みと読み戻し、シェイク、アプリのレジューム）を埋めれば、「Maestro はできるが Bajutsu はできない」という安易な反論を取り除けます。

違いは*やり方*にあります。各プリミティブは、落ち着き待ちの sleep と AI を使わない、決定的な `simctl` レベルの副作用のままです。Bajutsu は、決定性の契約を手放さずに能力面のパリティへ到達します。

## 詳細設計

各プリミティブは、新しいステップです。既存の `simctl` とデバイス制御チャネルを通じて、シミュレータを操作します。`setLocation`、`push`、`background` が確立したパターンに従います。このパターンは、AI を使わず、要素ツリーの外で評価される、デバイスへの決定的な副作用です。提案する表面は次のとおりです。最終的な名称は、採用時に確定します。

```yaml
- setTimezone: { id: "Asia/Tokyo" }          # simctl レベルのタイムゾーン上書き
- setClipboard: { text: "COUPON123" }        # ペーストボードへ値を仕込む
- shake: {}                                   # シェイクジェスチャ
- foreground: {}                              # 背面へ送ったアプリをレジュームする
```

バックエンドへのマッピングは次のとおりです。

- `setTimezone`、`setClipboard`、`shake` は、`simctl` のサブコマンドに対応します。ゾーンは `simctl status_bar` または `simctl spawn`、クリップボードは `simctl pbcopy` と `pbpaste`、シェイクはシェイクのデバイスイベントです。それぞれ、既存の `boot`、`launch`、`openurl` ビルダーと同様に、純粋なコマンド関数として組み立てます。実行は、注入可能な `RunFn` が担います。
- `foreground` は、`background`（BE-0035）がサスペンドしたアプリをレジュームします。固定 sleep を再導入**してはなりません**。レジューム後の落ち着きは、シナリオが条件待機を指定している場合に、それ（要素の出現または消失）でゲートします。単なる時間指定は、明示的で上限のある背面化区間としてのみ許されます。「アプリが落ち着くまで待つ」sleep には使いません。
- 既存のデバイス制御ステップと同様に、これらのステップはデバイスごとの制御チャネルを必要とします。**fake ドライバと並列実行では利用できません**。その場合はクラッシュせず、きれいに失敗します。

プリミティブは、prime directive を保ちます。

- **決定性。** 各プリミティブは、機械でチェックできる結果を持つ、決定的なデバイス変更です。落ち着き待ちの `sleep` と大規模言語モデル（LLM）を使いません。run / CI ゲートは AI 非依存のままです。
- **アプリ非依存。** アプリごとのコードはありません。アプリ固有の値（タイムゾーン id、クリップボード文字列）は、シナリオに置きます。複数のシナリオで共有する場合は、`apps.<name>` config に置きます。
- **codegen。** これらのステップは `simctl` レベルで動くので、アプリレベルの XCUITest 等価物がありません。codegen は、コマンドを明記したラベル付き `// TODO` を出力します。[BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md) と整合します。

## 検討した代替案

- **各効果を、deeplink や launch env でアプリ内から近似する。** たとえば、タイムゾーンを固定する launch フラグや、シェイクを模すデバッグ deeplink です。アプリごとには機能しますが、負担を各対象アプリへ押し付け、アプリ非依存性を壊します。主要な機構としては却下します。launch env は、純粋にアプリ固有のセットアップ用として引き続き利用できます。
- **アラートガードの vision パス（スクリーンショットとタップ）でシステムを操作する。** このパスは、idb が見られない SpringBoard プロンプトのために存在します。ただし AI フォールバックなので、決定的な run ゲートには決して入れられません。run パス上に LLM を置くことになるため、却下します。
- **背面化区間とレジューム後の落ち着き待ちに、固定 sleep を使う。** 固定 sleep は、設計上禁止されています。許される唯一の区間は上限のある背面化時間で、レジューム後はすべて条件待機を使います。レジューム後の落ち着き待ちとしては却下します。

## 進捗

- [x] `setClipboard` と `foreground`（バックグラウンドのアプリの再開）。最初のスライス（[#206](https://github.com/bajutsu-e2e/bajutsu/pull/206)）。
- [x] クリップボードの読み戻しアサーション。`simctl pbpaste` による `expect: - clipboard: { equals | matches }`（[#257](https://github.com/bajutsu-e2e/bajutsu/pull/257)）。
- [x] `setTimezone`。実装トリアージで、信頼できる `simctl` の作動手段が今のところ存在しないことを確認しました（実際には効かないコマンドの出荷は決定性優先の原則に反します）。`simctl` にも `idb` にも `timezone` サブコマンドがなく、シミュレータのタイムゾーンはデバイスごとに設定できず、ホスト Mac から継承します。launch 時の `SIMCTL_CHILD_TZ` はアプリプロセスの libc の `localtime` だけを動かし、iOS の日付 UI の多くが読む `TimeZone.current` には効きません。成功したように見えて、検証対象の UI は変わらないことになります。ホスト Mac 自体のタイムゾーンを変えれば動きますが、これはグローバルで `sudo` を要し、起動中のすべてのシミュレータにわたって開発機や CI の時計を書き換えるため、デバイスごとのプリミティブとしては契約外です。検証済みの手段を待つ独立した項目、[BE-0158](../BE-0158-timezone-device-primitive/BE-0158-timezone-device-primitive-ja.md) として切り出しました。
- [x] `shake`。実装トリアージで、`simctl` / `idb` に shake コマンドがないことを確認しました（ハードウェアメニューのジェスチャーです）。AppleScript でシミュレータの Device ▸ Shake メニューを操作する、あるいは RocketSim のような第三者ツールを使う GUI 自動化であれば決定的にトリガできます。ただし、シミュレータの GUI が起動していることと、操作するプロセスへのアクセシビリティ権限が必要で、フォーカス中のシミュレータに限られ、ヘッドレス CI では動きません。そのため、`simctl` レベルのプリミティブとしては今のところ成立しません。RocketSim 自身の CLI も shake（やタイムゾーン）を公開しておらず、スクリプト化できるのは `tap` / `swipe` / `type` / `button` / inspect 系のコマンドだけです。検証済みの手段を待つ独立した項目、[BE-0157](../BE-0157-shake-device-primitive/BE-0157-shake-device-primitive-ja.md) として切り出しました。

[#206](https://github.com/bajutsu-e2e/bajutsu/pull/206)、[#257](https://github.com/bajutsu-e2e/bajutsu/pull/257)、[#378](https://github.com/bajutsu-e2e/bajutsu/pull/378) で出荷しました。`setTimezone` と `shake` は、信頼できる作動手段がなかったため、それぞれ独立した保留中の項目 [BE-0158](../BE-0158-timezone-device-primitive/BE-0158-timezone-device-primitive-ja.md) と [BE-0157](../BE-0157-shake-device-primitive/BE-0157-shake-device-primitive-ja.md) へ切り出しました。本項目は、出荷できた範囲で完了です。

## 参考

[BE-0035 — デバイス制御ステップ](../BE-0035-device-control-primitives/BE-0035-device-control-primitives-ja.md)から分割しました。`setTimezone` と `shake` は、さらに [BE-0158 — タイムゾーンのデバイスプリミティブ](../BE-0158-timezone-device-primitive/BE-0158-timezone-device-primitive-ja.md)と [BE-0157 — シェイクのデバイスプリミティブ](../BE-0157-shake-device-primitive/BE-0157-shake-device-primitive-ja.md)へ分割しました。あわせて [DESIGN §6.2](../../DESIGN.md) と `bajutsu/orchestrator/actions/handlers/device.py` を参照してください。

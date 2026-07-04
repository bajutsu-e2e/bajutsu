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
| トピック | 競合調査（MagicPod / Autify）由来の候補 |
| 関連 | [BE-0157](../deferred/BE-0157-shake-device-primitive/BE-0157-shake-device-primitive-ja.md)、[BE-0158](../deferred/BE-0158-timezone-device-primitive/BE-0158-timezone-device-primitive-ja.md) |
| 由来 | MagicPod |
<!-- /BE-METADATA -->

## はじめに

[BE-0035](../BE-0035-device-control-primitives/BE-0035-device-control-primitives-ja.md)
の最初のスライス（`background`、`overrideStatusBar`、`clearStatusBar`）が出荷されたあとに切り出した、
残りのデバイス状態プリミティブです。タイムゾーンの固定、クリップボードへの値の仕込みと読み戻し、
シェイクジェスチャ、そして背面化したアプリのレジュームを扱います。

## 動機

実際のフローは、アプリ自身が制御しないデバイス状態に依存します。その状態を設定できないテストは、
そのフローを決定的に検証できません。BE-0035 のあとに残る欠落は次のとおりです。

- **タイムゾーン。** 日付に依存する UI（「今日」のヘッダ、カウントダウン、スケジュール画面）は、
  テストがデバイスのゾーンを固定できて初めて、複数のタイムゾーンにわたり検証できます。固定でき
  なければ、結果は CI の実行場所によってぶれます。
- **クリップボード。** 貼り付けフロー（クーポンコード、共有リンク、別所からコピーしたワンタイム
  コード）には、テストがペーストボードへ値を仕込み、「コピー」操作を検証するために読み戻すことが
  必要です。BE-0035 では `clearClipboard`（クリアのみ）を実装しました。値の仕込みと読み戻しが残り
  です。
- **シェイク。** 一部のアプリは「取り消し」やデバッグメニューをシェイクジェスチャに結びつけて
  います。現状、これをトリガする手段がありません。
- **アプリのレジューム。** BE-0035 の `background` はアプリを背面へ送ります。それをレジュームする
  こと（および上限のある背面化区間）は、前面と背面の遷移のもう半分であり、まだ未実装です。

これらがないと、こうしたフローは非決定的な回避策に頼るか、そもそも自動化できません。

**競合の文脈（Maestro）。** これらを埋めることは Maestro に対する前提でもあります。Maestro は標準で
幅広いデバイス制御の語彙を備えています。`setAirplaneMode` / `toggleAirplaneMode`、`setOrientation`、
`setLocation` / `travel`、`setPermissions`、`clearKeychain`、`clearState`、`pressKey`、
`hideKeyboard`、`openLink` といった具合です。Bajutsu は中核部分を BE-0035 で実装済みです。残るこれ
らのプリミティブ（タイムゾーン、クリップボードの仕込み／読み戻し、シェイク、アプリのレジューム）を
埋めれば、「Maestro はできるが Bajutsu はできない」という安易な反論を取り除けます。違いは*やり方*に
あります。各プリミティブは、落ち着き待ちの sleep も AI もない決定的な `simctl` レベルの副作用のまま
です。こうして Bajutsu は、決定性の契約を手放さずに能力面のパリティへ到達します。

## 詳細設計

各プリミティブは、`setLocation` / `push` / `background` がすでに確立したパターンに従い、既存の
`simctl` ／デバイス制御チャネルを通じてシミュレータを操作する新しいステップです。すなわち、AI を
使わず、要素ツリーの外で評価される、デバイスへの決定的な副作用です。提案する表面は次のとおりです
（最終的な名称は採用時に確定します）。

```yaml
- setTimezone: { id: "Asia/Tokyo" }          # simctl レベルのタイムゾーン上書き
- setClipboard: { text: "COUPON123" }        # ペーストボードへ値を仕込む
- shake: {}                                   # シェイクジェスチャ
- foreground: {}                              # 背面へ送ったアプリをレジュームする
```

バックエンドへのマッピング：

- `setTimezone`、`setClipboard`、`shake` は `simctl` のサブコマンドに対応します（ゾーンは
  `simctl status_bar`／`simctl spawn`、クリップボードは `simctl pbcopy`／`pbpaste`、シェイクは
  シェイク／デバイスイベント）。既存の `boot` / `launch` / `openurl` ビルダーと同様に純粋な
  コマンド関数として組み立て、注入可能な `RunFn` を通じて実行します。
- `foreground` は、`background`（BE-0035）がサスペンドしたアプリをレジュームします。ここでは固定
  sleep を再導入**しない**ことが肝心です。レジューム後の落ち着きは、シナリオが指定する条件待機
  （要素の出現と消失）でゲートします。単なる時間指定は、明示的で上限のある「背面化していた区間」
  としてのみ許され、「アプリが落ち着くまで待つ」sleep には決してしません。
- 既存のデバイス制御ステップと同様、これらはデバイスごとの制御チャネルを必要とするため、
  **fake ドライバと並列実行では利用不可**であり、クラッシュせずきれいに失敗します。

prime directive の保持：

- **決定性。** 各プリミティブは、機械チェック可能な結果を持つ決定的なデバイス変更です。落ち着き
  待ちの `sleep` も LLM もありません。run／CI ゲートは AI 非依存のままです。
- **アプリ非依存。** アプリごとのコードはありません。アプリ固有の値（タイムゾーン id、クリップ
  ボード文字列）は、シナリオに、または複数シナリオで共有する場合は `apps.<name>` config に置き
  ます。
- **codegen。** これらにはアプリレベルの XCUITest 等価物がないため（`simctl` レベルです）、codegen
  はコマンドを明記したラベル付き `// TODO` を出力します。これは
  [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md) と整合します。

## 検討した代替案

- **各効果を、deeplink や launch env でアプリ内から近似する。** たとえばタイムゾーンを固定する
  launch フラグや、シェイクを模すデバッグ deeplink です。アプリごとには機能しますが、負担を各対象
  アプリへ押し付け、アプリ非依存性を壊します。主要な機構としては却下します。launch env は、純粋に
  アプリ固有のセットアップ用として引き続き利用できます。
- **アラートガードの vision パス（スクリーンショット + タップ）でシステムを操作する。** このパスは
  idb が見られない SpringBoard プロンプトのために存在しますが、AI フォールバックであり、決定的な
  run ゲートには決して入れてはなりません。却下：run パス上に LLM を置くことになります。
- **背面化区間とレジューム後の落ち着き待ちに固定 sleep を使う。** 固定 sleep は設計上禁止されて
  います。許される唯一の区間は上限のある背面化時間であり、レジューム後はすべて条件待機を使わねば
  なりません。レジューム後の落ち着き待ちとしては却下します。

## 進捗

- [x] `setClipboard` と `foreground`（バックグラウンドのアプリの再開）。最初のスライス（[#206](https://github.com/bajutsu-e2e/bajutsu/pull/206)）。
- [x] クリップボードの読み戻しアサーション。`simctl pbpaste` による `expect: - clipboard: { equals | matches }`（[#257](https://github.com/bajutsu-e2e/bajutsu/pull/257)）。
- [x] `setTimezone`。実装トリアージで、信頼できる `simctl` の作動手段が今のところ存在しないことを確認しました（実際には効かないコマンドの出荷は決定性優先の原則に反します）。`simctl` にも `idb` にも `timezone` サブコマンドがなく、シミュレータのタイムゾーンはデバイスごとに設定できず、ホスト Mac から継承します。launch 時の `SIMCTL_CHILD_TZ` はアプリプロセスの libc の `localtime` だけを動かし、iOS の日付 UI の多くが読む `TimeZone.current` には効きません。成功したように見えて、検証対象の UI は変わらないことになります。ホスト Mac 自体のタイムゾーンを変えれば動きますが、これはグローバルで `sudo` を要し、起動中のすべてのシミュレータにわたって開発機や CI の時計を書き換えるため、デバイスごとのプリミティブとしては契約外です。検証済みの手段を待つ独立した項目、[BE-0158](../deferred/BE-0158-timezone-device-primitive/BE-0158-timezone-device-primitive-ja.md) として切り出しました。
- [x] `shake`。実装トリアージで、`simctl` / `idb` に shake コマンドがないことを確認しました（ハードウェアメニューのジェスチャーです）。AppleScript でシミュレータの Device ▸ Shake メニューを操作する、あるいは RocketSim のような第三者ツールを使う GUI 自動化であれば決定的にトリガできます。ただし、シミュレータの GUI が起動していることと、操作するプロセスへのアクセシビリティ権限が必要で、フォーカス中のシミュレータに限られ、ヘッドレス CI では動きません。そのため、`simctl` レベルのプリミティブとしては今のところ成立しません。RocketSim 自身の CLI も shake（やタイムゾーン）を公開しておらず、スクリプト化できるのは `tap` / `swipe` / `type` / `button` / inspect 系のコマンドだけです。検証済みの手段を待つ独立した項目、[BE-0157](../deferred/BE-0157-shake-device-primitive/BE-0157-shake-device-primitive-ja.md) として切り出しました。

[#206](https://github.com/bajutsu-e2e/bajutsu/pull/206) / [#257](https://github.com/bajutsu-e2e/bajutsu/pull/257) / [#378](https://github.com/bajutsu-e2e/bajutsu/pull/378) で出荷しました。`setTimezone` と `shake` は信頼できる作動手段がなく、それぞれ独立した項目 [BE-0158](../deferred/BE-0158-timezone-device-primitive/BE-0158-timezone-device-primitive-ja.md) と [BE-0157](../deferred/BE-0157-shake-device-primitive/BE-0157-shake-device-primitive-ja.md) へ切り出したため、この項目は出荷できた範囲で完了です。

## 参考

[BE-0035 — デバイス制御ステップ](../BE-0035-device-control-primitives/BE-0035-device-control-primitives-ja.md)
から分割。`setTimezone` と `shake` は、さらに
[BE-0158 — タイムゾーンのデバイスプリミティブ](../deferred/BE-0158-timezone-device-primitive/BE-0158-timezone-device-primitive-ja.md)と
[BE-0157 — シェイクのデバイスプリミティブ](../deferred/BE-0157-shake-device-primitive/BE-0157-shake-device-primitive-ja.md)へ分割しました。
[DESIGN §6.2](../../../DESIGN.md)、`bajutsu/orchestrator/actions/handlers/device.py`

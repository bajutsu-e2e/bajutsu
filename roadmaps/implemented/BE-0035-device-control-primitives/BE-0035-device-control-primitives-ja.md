[English](BE-0035-device-control-primitives.md) · **日本語**

# BE-0035 — デバイス制御ステップ（background・ステータスバー上書き）

* 提案: [BE-0035](BE-0035-device-control-primitives-ja.md)
* Author: [@0x0c](https://github.com/0x0c)
* 状態: **実装済み**
* 実装 PR: [#59](https://github.com/bajutsu-e2e/bajutsu/pull/59)
* トラック: [可決済み](../../README-ja.md#可決済み)
* トピック: 競合調査（MagicPod / Autify）由来の候補
* 由来: MagicPod

## はじめに

要素ツリーの外でシミュレータを操作する、決定的なデバイス制御ステップを 3 つ実装しました。
`background`（アプリを背面へ送る）、`overrideStatusBar`（スクリーンショットを安定させるため
ステータスバーを固定する）、`clearStatusBar`（ライブのステータスバーへ戻す）です。この項目が
当初対象としていた残りのデバイス状態プリミティブ — タイムゾーン、クリップボードへの値の仕込み、
シェイク — は、別の提案へ分割しました（[参考](#参考)を参照）。

## 動機

実際のフローは、アプリ自身が制御しないデバイス状態に依存します。要素ツリーの外で AI を使わずに
評価される、デバイスへの決定的な副作用だけが、それを再現可能にセットアップできる手段です。ここ
では次の 2 つの需要を実装しました。

- **前面・背面遷移。** 多くの挙動は背面化のときにのみ発火します（状態復元、再認証プロンプト、
  復帰時のリフレッシュ）。`relaunch` はアプリを再起動するもので、これは別のイベントです。ホーム
  ボタンによる背面化はそれ自体が一つのケースです。
- **決定的なステータスバー。** ビジュアル回帰アサーション（[BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions-ja.md)）は
  スクリーンショットを比較するため、ライブのステータスバー（時計・バッテリ・電波）があると、毎回
  のキャプチャが食い違ってしまいます。ステータスバーを固定値に固定するとそのぶれの源を取り除け、
  あとでクリアするとライブ表示へ戻せます。

## 詳細設計

各ステップは、`setLocation` と `push` がすでに確立したパターンに従い、既存の `simctl` ／デバイス
制御チャネルを通じてシミュレータを操作します。すなわち、AI を使わず、要素ツリーの外で評価される
決定的な副作用です。

```yaml
- background: {}                                    # アプリを背面へ送る（ホームボタン）
- overrideStatusBar: { time: "9:41", batteryLevel: 100, cellularBars: 4, wifiBars: 3 }
- clearStatusBar: {}                                # ライブのステータスバーへ戻す
```

バックエンドへのマッピング：

- **`background`** は、ホームを押してアプリを背面へ送ります（注入されたデバイス制御の `home()`
  経由で `simctl ui home`）。フィールドは取りません — 背面化のアクションのみです。アプリの
  レジューム（および上限のある背面化区間）は、分割した提案での今後の作業です。
- **`overrideStatusBar`** は、ステップが指定したフィールドだけを上書きします — `time`・
  `batteryLevel`・`batteryState`・`cellularBars`・`wifiBars` — `simctl status_bar override` に
  対応します。指定しなかったフィールドはライブの値のままです。
- **`clearStatusBar`** は上書きを取り除き（`simctl status_bar clear`）、ライブのバーへ戻します。
- `setLocation` / `push` と同様、これらはデバイスごとの制御チャネルを必要とするため、
  **fake ドライバと並列実行では利用不可**であり、クラッシュせずきれいに失敗します。デバイス制御
  ステップについてすでに文書化されているのと同じ契約です。

prime directive の保持：

- **決定性。** 各ステップは、機械チェック可能な結果を持つ決定的なデバイス変更です。落ち着き待ちの
  `sleep` も LLM もありません。run／CI ゲートは AI 非依存のままです。
- **アプリ非依存。** アプリごとのコードはありません。アプリ固有の値（ステータスバーの時刻）は
  シナリオに、共有する場合は `apps.<name>` config に置きます。ツール・ドライバ・ランナーはアプリ
  をまたいで不変です。
- **codegen。** これらにはアプリレベルの XCUITest 等価物がないため（`simctl` レベルです）、codegen
  はコマンドを明記したラベル付き `// TODO` を出力します。これは
  [BE-0026](../../proposals/BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md) と
  整合します。

## 検討した代替案

- **各効果を、deeplink や launch env でアプリ内から近似する。** アプリごとには機能しますが、負担を
  各対象アプリへ押し付け、アプリ非依存性を壊します。アプリがたまたま公開しているフックによって、
  ツールの挙動が変わってしまいます。主要な機構としては却下します。launch env は、純粋にアプリ固有の
  セットアップ用として引き続き利用できます。
- **アラートガードの vision パス（スクリーンショット + タップ）でシステムを操作する。** このパスは
  idb が見られない SpringBoard プロンプトのために存在しますが、AI フォールバックであり、決定的な
  run ゲートには決して入れてはなりません。却下：run パス上に LLM を置くことになります。

## 参考

残りのデバイス状態プリミティブ（タイムゾーン、クリップボードへの値の仕込み、シェイク、アプリの
レジューム）は、[デバイス状態プリミティブ: タイムゾーン・クリップボード・シェイク](../../proposals/BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md)
で別途扱います。

[DESIGN §6.2](../../../DESIGN.md)、`bajutsu/orchestrator/actions/handlers/device.py`

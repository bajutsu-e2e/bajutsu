[English](BE-0035-device-control-primitives.md) · **日本語**

# BE-0035 — デバイス制御ステップ（background・ステータスバー上書き）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0035](BE-0035-device-control-primitives-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0035") |
| 実装 PR | [#59](https://github.com/bajutsu-e2e/bajutsu/pull/59) |
| トピック | シナリオ記述機能 |
| 由来 | MagicPod |
<!-- /BE-METADATA -->

## はじめに

この項目では、要素ツリーの外でシミュレータを操作する決定的なデバイス制御ステップを 3 つ実装しました。

- `background` は、アプリを背面へ送ります。
- `overrideStatusBar` は、スクリーンショットを安定させるためにステータスバーを固定します。
- `clearStatusBar` は、ライブのステータスバーへ戻します。

この項目は当初、タイムゾーン、クリップボードへの値の仕込み、シェイクといったデバイス状態のプリミティブも対象にしていました。これらは別の提案へ分割しました（[参考](#参考)を参照）。

## 動機

実際のフローは、アプリ自身が制御しないデバイス状態に依存します。その状態を再現可能にセットアップする手段は、デバイスへの決定的な副作用だけです。副作用は、要素ツリーの外で AI を使わずに評価されます。ここでは次の 2 つの需要を実装しました。

- **前面と背面の遷移。** 多くの挙動は、アプリが背面へ移るときにだけ発火します。状態復元、再認証プロンプト、復帰時のリフレッシュが例です。`relaunch` はアプリを再起動するので、別のイベントです。ホームボタンによる背面化は、それ自体が独立したケースです。
- **決定的なステータスバー。** ビジュアル回帰アサーション（[BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions-ja.md)）はスクリーンショットを比較します。ステータスバーがライブのままだと、時計、バッテリ、電波が変わり、キャプチャのたびに差分が出ます。ステータスバーを固定値にすると、このぶれの源がなくなります。あとでクリアすれば、ライブ表示へ戻せます。

## 詳細設計

各ステップは、既存の `simctl` ／デバイス制御チャネルを通じてシミュレータを操作します。`setLocation` と `push` がすでに確立したパターンに従い、AI を使わず、要素ツリーの外で評価される決定的な副作用として動きます。

```yaml
- background: {}                                    # アプリを背面へ送る（ホームボタン）
- overrideStatusBar: { time: "9:41", batteryLevel: 100, cellularBars: 4, wifiBars: 3 }
- clearStatusBar: {}                                # ライブのステータスバーへ戻す
```

バックエンドへのマッピングは次のとおりです。

- **`background`** は、ホームを押してアプリを背面へ送ります（注入されたデバイス制御の `home()` 経由で `simctl ui home`）。背面化のアクションだけなので、フィールドは取りません。アプリのレジューム（および上限のある背面化区間）は、分割した提案での今後の作業です。
- **`overrideStatusBar`** は、ステップが指定したフィールドだけを上書きします。対象は `time`、`batteryLevel`、`batteryState`、`cellularBars`、`wifiBars` で、`simctl status_bar override` に対応します。指定しなかったフィールドは、ライブの値のままです。
- **`clearStatusBar`** は、上書きを取り除いて（`simctl status_bar clear`）ライブのバーへ戻します。
- `setLocation` / `push` と同様に、これらのステップはデバイスごとの制御チャネルを必要とします。そのため、**fake ドライバと並列実行では利用できません**。クラッシュせず、きれいに失敗します。これは、デバイス制御ステップについてすでに文書化されている契約と同じです。

prime directive は次のように保たれます。

- **決定性。** 各ステップは、機械でチェックできる結果を持つ決定的なデバイス変更です。落ち着き待ちの `sleep` と LLM は使わないので、run／CI ゲートは AI 非依存のままです。
- **アプリ非依存。** アプリごとのコードはありません。アプリ固有の値（ステータスバーの時刻）はシナリオに置き、共有する場合は `apps.<name>` config に置きます。ツール、ドライバ、ランナーは、アプリをまたいで変わりません。
- **codegen。** これらのステップは `simctl` レベルで動くため、アプリレベルの XCUITest 等価物がありません。そのため codegen は、コマンドを明記したラベル付きの `// TODO` を出力します。これは [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax-ja.md) と整合します。

## 検討した代替案

- **各効果を、deeplink や launch env でアプリ内から近似する。** アプリごとには機能しますが、負担を各対象アプリへ押し付け、アプリ非依存性を壊します。アプリがたまたま公開しているフックによって、ツールの挙動が変わってしまいます。主要な機構としては却下します。launch env は、純粋にアプリ固有のセットアップ用として引き続き利用できます。
- **アラートガードの vision パス（スクリーンショットとタップ）でシステムを操作する。** このパスは、idb が見られない SpringBoard プロンプトのために存在します。ただし AI フォールバックであり、決定的な run ゲートに入れるわけにはいきません。run パス上に LLM を置くことになるため、却下します。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

残りのデバイス状態プリミティブ（タイムゾーン、クリップボードへの値の仕込み、シェイク、アプリのレジューム）は、[デバイス状態プリミティブ: タイムゾーン・クリップボード・シェイク](../BE-0052-device-state-timezone-clipboard-shake/BE-0052-device-state-timezone-clipboard-shake-ja.md)で別途扱います。

[DESIGN §6.2](../../DESIGN.md)、`bajutsu/orchestrator/actions/handlers/device.py`

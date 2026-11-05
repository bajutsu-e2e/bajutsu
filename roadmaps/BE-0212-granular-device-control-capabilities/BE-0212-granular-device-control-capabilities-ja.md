[English](BE-0212-granular-device-control-capabilities.md) · **日本語**

# BE-0212 — 粗い deviceControl 能力を操作単位のトークンに分割する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0212](BE-0212-granular-device-control-capabilities-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0212") |
| 実装 PR | [#856](https://github.com/bajutsu-e2e/bajutsu/pull/856) |
| トピック | ドライバとバックエンドのアーキテクチャ |
| 関連 | [BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight-ja.md), [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md), [BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`deviceControl`（`bajutsu/drivers/base.py`）は、simctl の `DeviceControl` ファミリ全体を表す
単一の能力トークンです。ファミリには、`setLocation`、`push`、`clearKeychain`、`clearClipboard`、
`setClipboard`、`getClipboard`、`background`、`foreground`、`overrideStatusBar`、
`clearStatusBar` が含まれます。
[BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight-ja.md)
は、すべてのデバイス制御ステップをこの 1 つのトークンでゲートしています。

この設計は、ファミリを全か無かで扱います。正しく機能するのは、どのバックエンドもファミリ全体を
満たす（idb）か、まったく満たさない（fake、web）かのいずれかである間に限られます。本項目は、
この粗いトークンを操作単位の能力トークンに分割します。すると preflight は、各デバイス制御ステップを、
そのステップが必要とする操作だけでゲートできます。

## 動機

ファミリの一部だけを満たすバックエンドが現れると、この全か無かの前提は崩れます。Android の adb
バックエンド（[BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md)）が、その最初の例
です。エミュレータは `setLocation`（`emu geo fix`）とクリップボード操作を満たせます。一方で、
`push`、`clearKeychain`、ステータスバーの上書きに相当する手段は持ちません。単一トークンのままでは、
誠実な選択肢がありません。

- `deviceControl` を advertise すると、preflight は `push` ステップを通してしまい、実行時に
  失敗します。これは
  [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md) と
  [BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight-ja.md)
  が取り除こうとした、まさにその遅延失敗の型です。
- `deviceControl` を advertise しないと、preflight はエミュレータが実行できたはずの
  `setLocation` ステップを止めてしまいます。

つまり粗いトークンは、ファミリを部分的に満たすバックエンドに対して、遅延失敗を再導入します。
操作単位のトークンにすれば、バックエンドは実行できる操作を正確に宣言できます。preflight は、各ステップを
その宣言と照合してゲートできます。バックエンドが増えても、fail-fast の保証が保たれます。

これは、決定論的な `run`/CI 経路に対する、決定論と診断性の改善です。LLM は関与しません。

## 詳細設計

変更は能力レイヤーと preflight のマッピングにとどまります。ステップハンドラと `DeviceControl`
Protocol には手を入れません。

### 作業分解（MECE）

1. **操作単位のトークンを定義する**（`bajutsu/drivers/base.py`）。単一の `DEVICE_CONTROL`
   トークンを、操作ごとのトークンに置き換えます。常に一体で提供される操作は、結合したサブグループ
   ごと（クリップボードの read/write/clear など）のトークンにします。設計上の判断はこのグループ分け
   だけです。各バックエンドの実際のサポート範囲を表現できるところまで分割し、それより細かくは
   しません。
2. **各ステップをトークンに対応づける**（`bajutsu/capability_preflight.py`）。単一の
   `deviceControl` ゲートを、ステップ単位の参照に変えます。すると、サポートしていない操作を、
   BE-0128 がすでに出力している集約済みの preflight メッセージのなかで、個別に名指しできます。
3. **バックエンドごとにサポートを宣言する**。idb はファミリ全体を advertise します（挙動は
   byte-for-byte で不変）。fake と web バックエンドは、何も宣言しないので変更しません。
   ここで新しいバックエンドのサポートは追加しません。本項目は、部分的なサポートを表現可能にする
   だけです。
4. **実行時ゲートを保つ**。ハンドラ内のステップ単位の `_need_control` ガードは、これまでどおり
   `UnsupportedAction` を送出して、最後の砦として機能します。したがって変更は preflight の精度だけに
   閉じ、サポート済みステップの挙動を退行させません。
5. **検証**。fast ゲートのテストで、2 点を確かめます。サブセットを advertise するバックエンドが、
   サポート済みの操作では preflight を通り、サポートしていない操作では該当ステップを名指しして
   即座に失敗すること。そして、idb のファミリ全体の挙動が変わらないことです。

本項目は、Android device control の項目の前提になります。その項目は、ここで定義するトークンに対して、
エミュレータで実現できる `setLocation`／クリップボード操作を実装します。この同じバッチで別途
起票します。

## 検討した代替案

- **粗いトークンを保ち、Android は何も advertise しない**：却下しました。エミュレータが満たせる
  `setLocation` とクリップボードを止めてしまい、実行できたはずのシナリオを拒否します。これは逆向きの
  失敗であり、バックエンドの実際の能力について、依然として不誠実です。
- **各操作を実行時に試し、失敗したら降格する**：却下しました。決定論優先（BE-0082 の趣旨そのもの）に
  反します。ステップがサポートされていないと分かる前に、実行が部分的なデバイス操作を行ってしまいます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 操作単位のトークンを定義する（`bajutsu/drivers/base.py`）。
- [x] 各デバイス制御ステップをトークンに対応づける（`bajutsu/capability_preflight.py`）。
- [x] バックエンドごとにサポートを宣言する（idb はファミリ全体、fake／web は不変）。
- [x] 実行時の `_need_control` の砦を保つ。
- [x] 検証：サブセットを advertise するバックエンドに対する fast ゲートの preflight テスト。

ログ：

- [#856](https://github.com/bajutsu-e2e/bajutsu/pull/856)：粗い `DEVICE_CONTROL` トークンを操作単位の 6 トークン（`deviceControl.setLocation` /
  `.clipboard` / `.push` / `.clearKeychain` / `.appLifecycle` / `.statusBar`、まとまりのある操作は
  一つにまとめました）へ分割し、各デバイス制御ステップを preflight でそのトークンに対応づけました。
  idb と xcuitest は `DEVICE_CONTROL_ALL` でファミリ全体を advertise します。サブセットを advertise する
  バックエンド向けの fast ゲートテストを追加しました。実行時の `_need_control` の砦は変えていません。

## 参考

[BE-0128 — デバイス制御ステップを能力で preflight ゲートする](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight-ja.md)、
[BE-0082 — 実行前の preflight 能力チェック](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md)、
[BE-0007 — Android バックエンド](../BE-0007-android-backend/BE-0007-android-backend-ja.md)、
`bajutsu/drivers/base.py`、`bajutsu/capability_preflight.py`

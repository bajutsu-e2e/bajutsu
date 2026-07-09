[English](BE-0211-android-device-control.md) · **日本語**

# BE-0211 — Android のデバイス制御（setLocation、クリップボード）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0211](BE-0211-android-device-control-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0211") |
| トピック | Platform expansion (Android / Web / Flutter) |
| 関連 | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md), [BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Android バックエンド（[BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md)）には
デバイス制御がありません。`AndroidEnvironment.device_control()` は `None` を返すため
（`bajutsu/platform_lifecycle.py`）、すべてのデバイス制御ステップ（`setLocation`・`setClipboard`
など）が `UnsupportedAction` で失敗します。Android エミュレータはファミリの一部を満たせます。
`setLocation` は `emu geo fix` で、クリップボードの read/write/clear も実行できます。本項目は、
まさにそのサブセットに対する `AndroidDeviceControl` を実装して environment に配線し、エミュレータが
操作を実現できる範囲で idb と同等にします。

## 動機

iOS では `DeviceControl` Protocol を simctl が完全に裏付けており（`bajutsu/platform_lifecycle.py`）、
`setLocation` やクリップボードのステップが実行できます。Android では今これらがまったく実行できず、
位置情報に依存するフローやクリップボードの貼り付けフローといった一群のシナリオが、二番目に一般的な
モバイルターゲットで実行不能なまま残っています。エミュレータは必要な操作を adb／エミュレータ
コンソール経由で提供しているので、このギャップは実装の問題であり、プラットフォームの制約では
ありません。ただし、それはエミュレータが満たせるサブセットの範囲でのことです。

エミュレータが満たせない操作（`push`・`clearKeychain`・ステータスバーの上書き）は、正直に
サポート外のままにしておく必要があります。だからこそ本項目は、粗い `deviceControl` トークンでは
なく操作単位の能力トークンに依存します。粗いトークンを advertise すると、preflight で
サポート外の `push` を通してしまうからです。このトークン分割は、この同じバッチで別項目として
起票しています（粗い device-control 能力を分割する項目）。本項目は、それらのトークンに対して
自身のサポート済みサブセットを宣言します。

## 詳細設計

### サポートする操作

| 操作 | Android での手段 |
|---|---|
| `setLocation` | `emu geo fix <lon> <lat>`（adb 経由のエミュレータコンソール） |
| `setClipboard` / `getClipboard` / `clearClipboard` | `cmd clipboard`（`set-primary-clip` / `get-primary-clip` / `clear-primary-clip`） |

`push`・`clearKeychain`・`overrideStatusBar`・`clearStatusBar`・`background`・`foreground` は
エミュレータに忠実な相当手段がなく、サポート外のままにします（advertise しないので、preflight が
早期に失敗させます）。

### 作業分解（MECE）

1. **adb コマンドビルダ**（`bajutsu/adb.py`）。サポートする操作（`emu geo fix` と `cmd clipboard`
   の set/get/clear）の純粋なビルダを、既存の `_adb()` シリアル検証ヘルパー経由で用意します。simctl
   のビルダの双子です。
2. **`AndroidDeviceControl`**（`bajutsu/platform_lifecycle.py`）。サポートする操作について
   `DeviceControl` Protocol を実装するクラスです。environment の注入されたランナーに委譲し、idb の
   `_Control` の形をなぞります。
3. **environment への配線**。`AndroidEnvironment.device_control()` が `None` の代わりに新しい制御を
   返すようにします。`getClipboard` の読み戻し経路は、idb と同じく `clipboard` アサーションに
   供給します。
4. **サポート済みサブセットを宣言する**。操作単位の能力トークン（粗い device-control 能力を分割する
   項目のもの）に対して宣言し、preflight が `setLocation`／クリップボードを通し、残りを早期に
   失敗させるようにします。
5. **検証**。注入したランナーに対する fast ゲートのテスト：各操作のコマンドの形、クリップボードの
   読み戻しの往復、そして preflight がサポート済みサブセットを通しつつサポート外の操作を拒否する
   こと。実機での確認は Android エミュレータの e2e レーンに乗せます。

## 検討した代替案

- **UI 操作（貼り付けメニュー）でクリップボードを実装する**。却下しました。壊れやすくアプリ依存で、
  決定論的なデバイス状態操作とは正反対です。`cmd clipboard` はシステムのクリップボードを直接
  設定します。
- **ファミリ全体に手が届くまで何も advertise しない**。却下しました。今エミュレータが満たせる
  `setLocation` とクリップボードを無用に止めてしまいます。操作単位のトークンは、バックエンドが
  実現できるサブセットを出荷できるように、まさにそのために存在します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `emu geo fix` と `cmd clipboard` の adb コマンドビルダ（`bajutsu/adb.py`）。
- [ ] `DeviceControl` Protocol のサブセットに対する `AndroidDeviceControl`（`bajutsu/platform_lifecycle.py`）。
- [ ] `AndroidEnvironment.device_control()` とクリップボード読み戻しの配線。
- [ ] 操作単位の能力トークンに対するサポート済みサブセットの宣言。
- [ ] 検証：fast ゲートのテスト（コマンドの形、クリップボードの往復、preflight の通過／拒否）。

## 参考

[BE-0007 — Android バックエンド](../BE-0007-android-backend/BE-0007-android-backend-ja.md)、
[BE-0128 — デバイス制御ステップを能力で preflight ゲートする](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight-ja.md)、
`bajutsu/adb.py`、`bajutsu/platform_lifecycle.py`

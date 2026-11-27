[English](BE-XXXX-android-routes-and-kind-any.md) · **日本語**

# BE-XXXX — Android の環境ルートを分け、端末の種類を問わない実行を指定できるようにする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-android-routes-and-kind-any-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Platform support |
| 関連 | [BE-0467](../BE-0467-environment-specific-settings/BE-0467-environment-specific-settings-ja.md), [BE-0468](../BE-0468-runson-family-and-any-of/BE-0468-runson-family-and-any-of-ja.md), [BE-0481](../BE-0481-android-device-set-location/BE-0481-android-device-set-location-ja.md) |
<!-- /BE-METADATA -->

## はじめに

**環境ルート**は、実行が乗る環境の名前です。Bajutsu は
[ターゲット](../../docs/ja/glossary.md#target-app-device)の config と
[ドライバ](../../docs/ja/glossary.md#driver-backend-actuator-platform)から、ルートを導出します。ルートごとに、使える設定と能力の表が決まります。
iOS には、Simulator、実機、WebDriver の接続先という別々のルートがあります。Android のルートは
`android` の 1 つで、エミュレータと実機を区別しません。

本項目の変更は次の 2 つです。

| 変更 | 内容 |
|---|---|
| Android のルート分割 | `android` を `android-emulator` と `android-device` に置き換えます。 |
| `kind: any` の追加 | iOS と Android のターゲットは、仮想端末と実機のどちらでも実行できます。 |

分割に合わせて、種類を指定する新しいキー `runsOn.android.kind` を加えます。サーバーは、その種類の端末を持つ worker にジョブを渡します。
`any` のターゲットでは、Bajutsu が[シナリオ](../../docs/ja/glossary.md#scenario-authoring)全体を実行できる種類の中から選びます。

`any` は config の値であり、ルートではありません。どの実行も、具体的なルートの 1 つに乗ります。

## 動機

`platform` はテスト対象が何のアプリかを表します。ルートは、どこで実行し、どう端末を操作するかを表します。
Android のルートは 1 つしかないため、ジョブが必要とする種類を、どこにも記録できません。

**サーバーは Android のジョブを正しい種類の worker に渡せません。** 実機を持つ worker と
エミュレータを持つ worker は、同じトークンを名乗ります。実機向けのジョブがエミュレータに届きえます。
その場合、作者が選んでいない環境でジョブが成功します。

**config に意図を書けません。** iOS には `runsOn.ios.kind` がありますが、Android には対応するキーがありません。

**「空いている端末で」と指定できません。** `kind: [simulator, device]` のようなリストは、種類ごとに
1 回ずつ実行します。どちらか一方で 1 回だけ実行する値はありません。

[BE-0467](../BE-0467-environment-specific-settings/BE-0467-environment-specific-settings-ja.md) は、
2 つの種類を区別する Android のフィールドがないことを理由に、分割を後の項目に回しました。本項目はそのフィールドを加えます。

本項目の成否は、次の 2 つの現象で確かめられます。

- worker がエミュレータしか持たない間、`kind: device` の Android ターゲットのジョブはキューに残ること。
- `kind: any` のターゲットは、どちらの種類を持つ構成でも、config を直さずに実行できること。

## 詳細設計

本設計は、モデルの呼び出しも固定の待機も加えません。すべての判断は、config、シナリオ、端末の一覧から決まります。

### Config

| キー | 値 | 省略時 |
|---|---|---|
| `runsOn.ios.kind` | `simulator`、`device`、`any`、または前の 2 つのリスト | `simulator` |
| `runsOn.android.kind` | `emulator`、`device`、`any`、または前の 2 つのリスト | `emulator` |

リストの意味は今と変わらず、挙げた種類ごとに 1 回ずつの実行です。リストに `any` は含められません。

Android の省略時の値は、挙動を変えます。今 Android の実機を使っているターゲットは、`kind: device` の追記が必要です。
ターゲットの種類の端末が見つからない実行は、端末の操作を始める前に停止します。エラーは `runsOn.android.kind` を名指しします。
showcase の Device Farm 用 config にも、同じ変更でキーを加えます。

実機に関する次の 2 つの規則は、iOS の規則を引き継ぎます。

| 対象のターゲット | 宣言できないキー | 理由 |
|---|---|---|
| Android で `kind` が `device`、`any`、または `device` を含むリスト | `avd` | 実機には Android Virtual Device (AVD) がない |
| iOS で `kind: any` | `model`、`os` | 今の `device` のターゲットと同じ制限 |

`apiLevel` は引き続き使えます。

`kind: any` の iOS ターゲットは、`driver.runner.testRunner` も宣言できません。`any` では空き端末によって
種類が決まるため、ビルド済みの runner が合うかどうかが偶然に左右されます。リストは実行する種類を
すべて名指しするので、引き続きこのキーを使えます。

`--kind` フラグは、Android のターゲットでも使えるようになります。`any` のターゲットでは、実行を 1 つの種類に絞ります。

### ルートと表

ルートは `ios-simulator`、`ios-device`、`ios-live`、`android-emulator`、`android-device`、`web` になります。
Android の 2 ルートは、今の `android` と同じ設定と能力で始まります。後で差が生じても、直すのは表のセルだけです。
`setLocation` は引き続き、[BE-0481](../BE-0481-android-device-set-location/BE-0481-android-device-set-location-ja.md)
の定めに従い、エミュレータのコンソールとアプリ内の受信機を使い分けます。

Android の端末情報にも、iOS と同じく実機の印を付けます。印がないと、種類のフィルタは両者を区別できません。

### `any` での種類の選び方

Bajutsu は端末を借りる前に、シナリオとターゲットの組ごとに候補の種類を計算します。候補になるのは、次の 2 条件を満たす種類です。

| 条件 | 内容 |
|---|---|
| 能力 | ターゲットのステップが必要とする能力を、ルートがすべて持つ（ライフサイクルフックを含む） |
| 設定 | 有効な設定を、ルートがすべて適用できるか満たせる |

`runsOn.ignoredSettings` に挙げた設定は、設定の条件に数えません。

たとえば `setLocation` を含むシナリオでは、iOS ターゲットの候補は `simulator` だけです。`subject.path` を持つターゲットも、
`install` を無視すると宣言しない限り、候補は `simulator` だけです。候補が空の実行は停止し、エラーにルートごとの不足が並びます。

| 場面 | 規則 |
|---|---|
| 両方の種類に空き端末がある | シミュレータまたはエミュレータを使う |
| 端末を持つ候補が片方だけ | その端末を使う |
| 端末を共有する複数ターゲット | 全員が受け入れる種類を残す |
| `record`、`crawl`、`repl` | シナリオがないため、ターゲットの設定だけで判定する |
| Appium グリッド | `ios-live` のままで、`kind` は適用しない |

仮想端末の優先は、1 つの端末プールの中で働きます。サーバーは worker どうしに順位を付けません。

`runsOn.ignoredSettings` の宣言は、ターゲットが挙げるすべての種類で設定が使える（または満たせる）場合に、
古い宣言として実行を停止します。検査は 1 回の実行の種類ではなく、ターゲットの種類の全体を読みます。
この規則は、1 つのルートだけを見る今の検査を、リストでも置き換えます。たとえば `erase` を無視する
`kind: [simulator, device]` のターゲットは、Simulator の実行でもエラーになりません。

設定が使えるルートでは、Bajutsu はその設定を適用します。証跡の種類は候補を絞りません。選んだルートで
記録できない証跡は、今と同じくレポートの省略項目に載ります。レポートには、実行ごとに乗ったルートを記録します。

`any` の実行は、選んだ種類を実行の座標にも加えます。flakiness の履歴は、この座標で実行をまとめます。
仮想端末と実機の実行は別々の履歴になります。2 つの環境の差が、不安定なシナリオとして現れることはありません。

### ジョブの振り分け

iOS と Android のターゲットのジョブは、ルートを名指しするトークン `route:<route>` を必要とします。
シナリオに必要な能力や設定がない場合でも、この要件は変わりません。`kind: any` のジョブは、シナリオとターゲットの組ごとに 1 つの選択を持ちます。
各選択は、その組の候補ルートのどれか 1 つで満たせます。ジョブは、すべての選択を満たす worker を必要とします。
リストのジョブは、1 台の worker が全部を実行するため、挙げたすべてのルートのトークンを必要とします。

1 つのルートのトークンは、まとめて満たす必要があります。能力を一方のルートから、設定を他方から取ることはできません。
既存の any-of はトークン単位なので、`can_serve` にはトークンの組の単位の選択が必要です。
iOS の組には、今の種類に付くトークンも含めます。ファンアウトジョブは、ルートの `kind:` のトークンを加えます。
`runsOn.ios.family` を持つターゲットは、ファミリーのトークンを加えます。`ios-device` では `device:<family>`、
`ios-simulator` では `iphone` または `ipad` です。ファミリーのないターゲットは、何も加えません。

Android の組は `kind:` のトークンを含めません。`kind:emulator` を名乗る worker はなく、
ルートのトークンが種類をすでに表すためです。

Device Farm には、もう 1 つ考慮が必要です。Amazon Web Services (AWS) Device Farm の iOS のファンアウトは、
`driver.runner.testRunner` を必要とします。`any` のターゲットは、このキーを宣言できません。そこで worker の
capability で、該当するジョブを Device Farm から外します。

実機を操作するローカルの worker は、XCUITest の runner を自分で解決できます。この能力を `runner:resolve`
として名乗ります。Device Farm の worker は名乗りません。`testRunner` のない iOS ターゲットは、`ios-device` の
組にこのトークンを加えます。`any` のジョブはローカルの worker に渡ります。該当する worker がなければ、
ジョブはキューに残ります。

Android のファンアウトジョブも、同じルートのトークンを必要とします。既存の `kind:device` では足りません。
iPhone をつないだ Mac が `kind:device` を名乗るためです。Device Farm の worker は
`route:android-device` と `route:ios-device` を名乗り、今と同じく `cap:` と `setting:` は名乗りません。
fake ドライバのジョブは端末を操作しないので、ルートのトークンは不要です。

ルートのトークンは厳格に要求します。今のサーバーは、更新でキューを止めないように、BE-0467 より前の
worker には `cap:` と `setting:` の要件を外します。`route:` にはこの例外を設けません。トークンを持たない
worker は、iOS と Android のジョブを引き受けません。worker は、ジョブを引き受ける時点でもう一度確認します。
`route:` は予約済みの接頭辞にも加え、`--capabilities` で名乗れないようにします。

新しいトークンは、`cap:` と `setting:` の検査方法を変えません。Device Farm の worker のように
`cap:` と `setting:` を名乗らない worker には、今と同じく、その 2 つの要件を外します。

運用者は、先にキューを空にしてから、サーバーと全 worker を同時に更新します。キューにある Android のジョブは
`cap:android:` や `setting:android:` のトークンを持ちます。更新後の worker は、どれも名乗りません。
更新後のサーバーは、残ったジョブを、更新が原因だと示すメッセージとともに停止します。そのため、
エラーのないままキューで待ち続けるジョブは出ません。

### Worker

ローカルの worker は、宣言と検出を組み合わせます。

`worker.yaml` には、引き受けてよい種類を宣言します。新しいキー `androidKinds` は `emulator` と `device`
を取り、省略時は両方です。`realDevices` の iOS での意味は変わりません。

worker はリースのたびに、Android Debug Bridge (adb) で接続中の端末を読みます。宣言が許し、かつ
その種類の端末が応答する場合に、Android のルートを名乗ります。

iOS の実機ルートにも、同じ条件を加えます。今の worker は、`realDevices` が未指定なら `ios-device` を
名乗ります。今後は、`devicectl` が実機を見つけた場合に限り `route:ios-device` を名乗ります。
Simulator のルートは変わりません。Appium グリッドの worker は、
そのルートの `cap:` と `setting:` に加えて `route:ios-live` を名乗ります。

### 作業の単位

1. Config：Android の `kind`、両プラットフォームの `any`、読み込み時の検査、`--kind`。
2. ルート：表とトークンの分割、Android の実機の印。
3. 候補の選択：候補の計算、仮想端末の優先、ルートの記録。
4. 振り分け：ルートのトークン、トークンの組の選択、ファンアウトの要件、廃止トークンを持つジョブの停止。
5. Worker：`androidKinds`、adb の読み取り、iOS の `devicectl` の条件。
6. 文書とデモ：日英の文書と showcase の config の更新。

どの単位も、fake ドライバか記録した端末一覧を使うテストを伴います。ゲートに実機やシミュレータは不要です。

## 検討した代替案

| 案 | 概要 | 採らない理由 |
|---|---|---|
| `ios-any` と `android-any` のルート | 2 ルートの共通部分を表にしたルートを加える | その名のルートで実行が起きることはなく、iOS は Simulator の機能の大半を失う |
| `any` では共通部分だけを許す | 片方にない機能はすべて拒否する | iOS の `any` がほぼ実機のターゲットになる |
| 実行時に種類を判定 | config のキーを設けず、端末を読む | lint と振り分けには読む端末がない |
| 1 リリースの移行期間 | `kind: device` のない実機を当面は警告で通す | その間、振り分けの時点でルートが決まらない |
| `worker.yaml` の宣言だけ | adb の読み取りを省く | 外した実機の worker がジョブを取り続ける |
| 優先順を config に書く | ターゲットが優先する種類を並べる | リストは種類ごとに 1 回の実行という意味をすでに持つ |

[BE-0468](../BE-0468-runson-family-and-any-of/BE-0468-runson-family-and-any-of-ja.md) は `kind: any` を退けました。
理由は 2 つです。種類の優先順が config の外に置かれること。そして「両方を別々の実行にする」を表せないことです。
後者はリストが表せるので、`any` が担う必要はありません。前者は今も当てはまります。本項目はこれを受け入れ、
優先順を仮想端末が先と固定します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 単位 1：Config のキー、読み込み時の検査、`--kind`
- [ ] 単位 2：ルートの分割と Android の実機の印
- [ ] 単位 3：`any` の候補の選択
- [ ] 単位 4：振り分けのトークン
- [ ] 単位 5：Worker の宣言と検出
- [ ] 単位 6：文書とデモ

## 参考

- `bajutsu/common/capability/settings_support.py`：ルートごとの設定の表。
- `bajutsu/common/capability/route_tokens.py`：ルートごとのトークン。
- `bajutsu/common/devices/facts.py`：種類のフィルタが読む端末情報。
- [セルフホスト](../../docs/ja/self-hosting.md)：サーバーがジョブを振り分ける仕組み。

[English](BE-XXXX-runson-destination-blocks.md) · **日本語**

# BE-XXXX — runsOn を実行先ごとにまとめ、シナリオの runsOn を廃止する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-runson-destination-blocks-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Driver & backend architecture |
<!-- /BE-METADATA -->

## はじめに

ターゲットの `runsOn` は、そのターゲットを実行できる場所を限定します。本項目は、この限定を実行先ごとに
`runsOn.ios`、`runsOn.android`、`runsOn.browser` へまとめます。ブラウザのブロックはすでに実行先を
名指ししていますが、iOS と Android のフィールドは現在その隣にむき出しで並んでいます。あわせて、
シナリオから `preconditions.runsOn` を取り除きます。実行できる場所はターゲットが持つ性質だからです。
シナリオのデバイス状態（`locale`、`seedPhotos`）は `preconditions.device.ios` に移します。意味の
変わるフィールドはありません。どの条件も、新しいパスから現在とまったく同じようにデバイスを照合します。

## 動機

`runsOn` の形には一貫性がありません。`runsOn.browser` はブラウザを明示的に名指ししますが、
`runsOn.model` はどのプラットフォームかを言わずにモバイルデバイスを暗に指します。`runsOn.os: "18"` を
読んだ人は、ターゲットの `platform` を見てはじめて、この範囲が iOS のものだとわかります。この隙間は
プラットフォームがフィールドを足すたびに広がり、対になる提案「runsOn.ios でデバイスファミリーを、
runsOn.browser で複数エンジンのいずれかを指定できるようにする」は 2 つ足します。実行先ごとにまとめれば、
各フィールドが何を限定するかを自ら示します。`runsOn.ios.os` は iOS のバージョン、
`runsOn.browser.engine` はブラウザと読めます。

シナリオの `preconditions.runsOn` は、2 つの関心事を混ぜています。実行できる場所はターゲットの性質ですが、
シナリオはそれをフィールド単位で上書きできます。このリポジトリでこのブロックを使うシナリオは、条件を
1 つも書いていません。デモのシナリオ 4 本が書いているのは `locale` か `seedPhotos` で、どちらも
実行場所の限定ではなくデバイスの状態です。さらに上書きには、互いに排他な 2 つのフィールドをターゲットが
得るたびに、層をまたぐ規則が要ります。シナリオのブロックは、ターゲット自身の検証の後に平らな条件として
畳み込まれるからです。シナリオのブロックを取り除けば「どこで」を言う場所は 1 つになり、デバイス状態には
それを名乗るブロックができます。

出荷後に確かめられる結果は具体的です。config の `runsOn` のフィールドはすべて実行先のキーの下に並び、
`bajutsu config schema` は各プラットフォームの限定を 1 つのブロックで示します。リポジトリのシナリオに
`runsOn` は残らず、旧書式は置き換え先のパスを示して読み込みに失敗します。

## 詳細設計

### `runsOn` の形

各ターゲットは、実行先のブロックを 1 つ書きます。現在は、どのプラットフォームも実行先を 1 つだけ許します。

| プラットフォーム | ブロック | フィールド |
|---|---|---|
| iOS | `runsOn.ios` | `model`、`os`、`kind`、`locale`、`seedPhotos` |
| Android | `runsOn.android` | `avd`、`apiLevel` |
| web | `runsOn.browser` | `engine`、`version`、`emulate` |
| fake | なし | なし |

`platform` と `runsOn` は別のことを表します。`platform` はテストするアプリの種類を名指しし、アプリの
渡し方（`app.id` か `app.url` か）と、それを動かすドライバーを決めます。`runsOn` は、そのアプリを実行
できる場所を限定します。現在はどのプラットフォームでも両者が一致しますが、一致する必要はありません。
iOS Simulator のモバイル Safari で動く web アプリは、`platform: web` で実行先が iOS です。Flutter の
アプリなら iOS と Android の両方で動きます。そこでローダーは、2 つの名前が等しいかではなく、組が
許されたものかを確かめます。許される組は上の表の 4 行です。たとえば `runsOn.browser` を書いた
`platform: ios` のターゲットは、`ios` が許す実行先を示して失敗します。後の提案は、`runsOn` の形を
変えずにプラットフォームの実行先を広げられます。
チームのデフォルト値は、`defaults.runsOn` に複数のブロックを持てます。各ターゲットは自分の
プラットフォームのブロックだけを受け取るので、iOS 向けのデフォルト値が web のターゲットに届くことは
なくなります。

[BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md) は、
ターゲットの階層でプラットフォーム名をキーにする案を退けています。その案では `targets.web.web:` のように
同じ語が二重になり、キーが `platform` の代わりを務めていました。本項目のキーは 1 段下の `runsOn` の
内側にあり、`runsOn.browser` と同じく実行される場所を名指しします。`platform` の代わりは務めません。
`app` と `driver` の形を決めるのは、引き続きプラットフォームです。

`locale` と `seedPhotos` は、当面 iOS のフィールドと一緒に移します。どちらも実行場所の限定ではなく、
デバイスの状態です。ターゲットのデバイス状態を `runsOn` から外すかどうかは、別の提案で決めます。
本項目が移すのは、シナリオのデバイス状態だけです。

### シナリオは `runsOn` を書かない

`preconditions.runsOn` はシナリオのスキーマから外れます。2 種類のフィールドは、それぞれ別の道をたどります。

- **条件**（`model`、`os`、`avd`、`apiLevel`、`browser.engine`、`browser.version`）には、シナリオ側の
  置き換え先がありません。iPad が必要なシナリオは、`targets: [app-ipad]` のように iPad で動く
  ターゲットを名指しします。条件は、実行先についてのほかの限定と同じ場所にとどまります。リストや
  範囲で run を倍増させていたシナリオは、そのリストや範囲を書いたターゲットから同じ run を得ます。
- **デバイス状態**（`locale`、`seedPhotos`）は `preconditions.device.ios` に移ります。意味は変わりません。
  シナリオの locale はターゲットのものを置き換え、異なるときはコールドリスポーンを強制します
  （[BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md)）。
  シナリオの `seedPhotos` はターゲットのリストを置き換え、有効な `run.erase` を必要とします。

シナリオのブロックがなくなると、`bajutsu/common/devices/runs.py` の `form_target_runs` に届く条件は、
`--browser` フラグを除けばターゲットのものだけになります。`bajutsu/common/config/preconditions.py` の
`effective_conditions` はシナリオを畳み込まなくなります。`runs_on_problems` は、新しいデバイスの
ブロックの検査に置き換わります。複数のターゲットを持つシナリオは、従来どおり各ターゲットの run の
すべての組み合わせを実行します。

### 既存の config とシナリオの移行

旧書式は読み込みに失敗し、キーの移り先を示します。BE-0452 が自身の移動で採ったのと同じ方法です。
ローダーが両方の書式を受け付けることはないので、config の書き方は常に 1 通りです。

| 旧キー | 新キー |
|---|---|
| `runsOn.model`、`os`、`kind`、`locale`、`seedPhotos` | `runsOn.ios.<同じフィールド>` |
| `runsOn.avd`、`apiLevel` | `runsOn.android.<同じフィールド>` |
| `runsOn.emulate` | `runsOn.browser.emulate` |
| `preconditions.runsOn.ios.locale`、`seedPhotos` | `preconditions.device.ios.<同じフィールド>` |
| `preconditions.runsOn.<プラットフォーム>.<条件>` | その条件を書いたターゲット |

### 実行先ごとの書き方

```yaml
targets:
  app-sim:                     # Simulator
    platform: ios
    app: { id: com.example.app }
    runsOn:
      ios: { model: "iPhone 16", os: "18" }

  app-device:                  # 実機（--udid で渡す）
    platform: ios
    app: { id: com.example.app }
    runsOn:
      ios: { kind: device }

  site:                        # ブラウザ
    platform: web
    app: { url: https://example.com }
    runsOn:
      browser: { engine: firefox, emulate: "iPhone 15" }
```

シナリオは必要なターゲットを名指しし、デバイスの状態は `preconditions.device` に書きます。

```yaml
targets: [app-sim]
preconditions:
  device:
    ios: { locale: ja_JP }
```

### スキーマとドキュメント

- `bajutsu/common/config/schema/platform/` のプラットフォームの registry は、各プラットフォームの
  `runsOn` のモデルを実行先のキーで包みます。`emulate` は `WebRunsOn` から `browser` のブロックへ移ります。
- シナリオのスキーマから `preconditions.runsOn` を外し、`preconditions.device.ios` を加えます。
- `bajutsu config schema` は実行先のブロックを出力します。
- [設定](../../docs/ja/configuration.md#device-conditions-runson)と英語版には、新しい形、移行の表、
  例を載せます。[CLI](../../docs/ja/cli.md)からはシナリオの `runsOn` の節を外します。
  [アーキテクチャ](../../docs/ja/architecture.md)では `common/config/` の行を更新します。

### 原則との整合

run に LLM は入らず、照合の方法が変わる条件もありません。本項目はフィールドを新しいパスへ移し、
上書きを 1 つ取り除くだけなので、差異はすべて config に、ターゲットごとに 1 か所にとどまります。

### 対象外

- ターゲットのデバイス状態（`locale`、`seedPhotos`、`emulate`）を `runsOn` から外すこと。置き場所は
  別の提案で決めます。
- モバイル Safari で動く web アプリのような、実行先を複数持つプラットフォーム。本項目はプラット
  フォームごとに実行先を 1 つに保ち、増やす余地を残します。
- デバイスファミリーやブラウザエンジンの集合のような、新しい条件。対になる提案「runsOn.ios で
  デバイスファミリーを、runsOn.browser で複数エンジンのいずれかを指定できるようにする」が、この形の上に
  加えます。

### 作業分解

1. **実行先のブロック。** 各プラットフォームの `runsOn` のモデルを `ios`、`android`、`browser` で包み、
   `platform` がキーを許すかを確かめ、プラットフォームごとのチームのデフォルト値を受け取り、`emulate` を
   `browser` の下へ移し、旧キーごとに新しいパスを示して失敗させます。`demos/` の config とテストの
   フィクスチャを書き換え、JSON Schema の出力も広げます。
2. **シナリオのブロック。** `preconditions.runsOn` を外し、`locale` と `seedPhotos` のために
   `preconditions.device.ios` を加え、旧ブロックはフィールドごとの置き換え先を示して失敗させます。
   `effective_conditions` からシナリオの畳み込みを外し、デバイス状態を書いているデモのシナリオ 4 本を
   書き換えます。
3. **ドキュメント。** 上記の日英ドキュメントに、新しい形、移行の表、例を載せます。

各ユニットが自分のテストを持ちます。許される組の規則、移行メッセージのそれぞれ、デバイスのブロックの
置き換えの意味です。

## 検討した代替案

| 代替案 | 退けた理由 |
|---|---|
| `runsOn` を平らなままにする | `runsOn.browser` は実行先を名指しするのに、`runsOn.model` はプラットフォームを言わずにモバイルデバイスを暗に指します。フィールドが増えるたびに、形の一貫性が失われます。 |
| web のブロックのキーを `runsOn.web` にする | ページを動かすものではなく、プラットフォームを名指しすることになります。`runsOn.browser` はすでに実行先を名指ししており、iOS と Android のキーもそれに倣います。 |
| シナリオの `runsOn` を残し、層をまたぐ規則を足す | 実行できる場所はターゲットの性質です。上書きには排他なフィールドの組ごとに規則が要り、このリポジトリでのブロックの実際の用途はデバイス状態でした。 |
| `platform` を廃止し、`runsOn` のキーから推定する | `platform` はテストするアプリを、`runsOn` は実行先を表し、モバイル Safari の web アプリが示すとおり両者は異なりえます。推定にすると `runsOn` がキーちょうど 1 つの必須項目になり、「どの iOS デバイスでもよい」にも空のブロックが要り、fake プラットフォームにもキーが要ります。 |
| 旧書式を猶予期間だけ受け付ける | ローダー、JSON Schema、ドキュメントに 2 つの書き方が並びます。新しいキーを示して読み込みが失敗すれば、config は 1 回の編集で移れます。 |
| ターゲットのデバイス状態も同じ項目で `runsOn` から出す | デバイス状態の置き場所は別の判断であり、合わせるとこの破壊的変更が大きくなります。シナリオのデバイス状態をいま移すのは、シナリオのブロックを取り除くと置き場所がなくなるからです。 |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] ユニット 1: 実行先のブロック
- [ ] ユニット 2: シナリオのブロック
- [ ] ユニット 3: ドキュメント

## 参考

- [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md)：本項目が形を変える `runsOn` のグループと、シナリオの `runsOn`。
- [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md)：意味ごと `preconditions.device.ios` に移るシナリオの locale。
- `bajutsu/common/config/schema/platform/` と `bajutsu/common/config/preconditions.py`：本項目が変えるスキーマと、シナリオの畳み込み。

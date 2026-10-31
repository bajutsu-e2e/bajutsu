[English](BE-0473-config-run-group-and-interrupts.md) · **日本語**

# BE-0473 — target の run グループを解体し、自動で処理する割り込みを interrupts に集める

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0473](BE-0473-config-run-group-and-interrupts-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0473") |
| 実装 PR | [#2242](https://github.com/bajutsu-e2e/bajutsu/pull/2242)（単位 3、5、6）、[#2263](https://github.com/bajutsu-e2e/bajutsu/pull/2263)（単位 2） |
| トピック | ドライバとバックエンドのアーキテクチャ |
| 関連 | [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md)、[BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers-ja.md)、[BE-0406](../BE-0406-system-alert-declared-prompts/BE-0406-system-alert-declared-prompts-ja.md)、[BE-0401](../BE-0401-system-alert-handling-dsl-consolidation/BE-0401-system-alert-handling-dsl-consolidation-ja.md)、[BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config-ja.md)、[BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications-ja.md)、[BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`bajutsu.config.yaml` の target は、設定を用途ごとに11個のキーへまとめています。そのうちの `run` は、
用途を共有しない6つのフィールドを抱えています。6つのうち `systemAlertHandling` と `tipKitHandling` は、
実行中の予測できない時点で現れる割り込みを片付ける設定です。同じ目的のキーとして、`hooks.interrupts`
がすでにあります。target が割り込みをどう処理するかを知りたい読み手は、2つのグループを、2つの違う書き方で
読まなければなりません。

この項目は `run` を取り除き、各フィールドを用途の合うグループへ移します。

- `systemAlertHandling` と `tipKitHandling` は、`hooks.interrupts` のエントリになります。アプリ内の画面を
  処理する既存のエントリと並びます。シナリオ自身の `interrupts` も同じエントリを受け付けます。
- `erase` は、`app.reinstall` のもっとも強い値になります。値は `overwrite`、`clean`、`erase` の3段階です。
- `network`、`visualCompare`、`secrets` は `evidence` へ移ります。

同じファイルにある小さな論点も3つ片付けます。target 内の `notify` はトップレベルの一覧を上書きしなくなり、
結果の送り先はトップレベルの一覧だけで決まります。`services` の説明は、すでに含んでいる受信箱まで
覆うように広げます。さらに、ローダーは `x-` で始まるトップレベルのキーを受け付けるようにし、target 間で
共有する YAML のアンカーを置けるようにします。BE-0452 と同じく、後方互換性は意図して捨てます。古いキーは
読み込みで失敗し、新しい置き場所をエラーで示します。

## 動機

`run` グループ（`bajutsu/common/config/schema/run_policy.py`）は、「実行の方針」という名目で6つの
フィールドを集めています。6つのフィールドが答える問いは、次の3種類に分かれます。

| フィールド | 答える問い |
|---|---|
| `erase` | 端末をどの状態から始めるか |
| `network`、`visualCompare`、`secrets` | 実行が何を記録し、どう比較し、何を伏せるか |
| `systemAlertHandling`、`tipKitHandling` | 実行中に現れた割り込みを何が片付けるか |

BE-0452 は target のフラットなキーを用途別に組み直しましたが、`run` だけは用途の混ざった一覧のまま
残りました。混在は、読み手に2つの負担を生んでいます。

1つ目の負担は、割り込みの処理が2つのグループに分かれていることです。`hooks.interrupts` には、アプリ自身の
アクセシビリティツリーにある画面を条件で捉え、ステップで片付けるエントリを並べます。showcase が閉じている
Android の「アプリが応答しません」ダイアログが、その例です。`run.systemAlertHandling` は、通知の許可の
ような OS のプロンプトに、プロンプトの名前と選択で答えます。`run.tipKitHandling`
は、Apple の TipKit が出すポップオーバーを閉じる組み込みのガードを有効にします。実行時には、この3つは
すでに `RunDefaults`（`bajutsu/common/config/effective/run_defaults.py`）で一緒に運ばれています。
分かれているのは設定ファイルの上だけです。「この target は何を自動で閉じるのか」を確かめるレビュー担当者は、
`hooks` と `run` の両方を読み、2つの書き方が同じ種類のものを指すと知っている必要があります。

2つ目の負担は、用途の外に置かれたフィールドがグループをまたぐ依存を生むことです。`runsOn.seedPhotos` は、
端末を消去する経路でだけ iOS の写真ライブラリに画像を入れます。そのため、ローダーは端末を触る前に、
有効な `run.erase` があるかを確かめます。一方、Android のライフサイクルは、`erase` と
`reinstall: clean` をすでに何を消すかという1つの判断として読み、同じ処理をしています
（`bajutsu/common/platform_lifecycle/environments/android/android_environment.py`）。スキーマは、その1つの
判断を `app.reinstall` と `run.erase` に分けて置いています。

同じファイルには、小さな不整合も2つあります。`notify` は、Webhook を一部の target に絞る方法を2通り
持っています。送信先ごとの `targets:` による絞り込みと、トップレベルの一覧を置き換える target 内の `notify`
です。どちらも「どの結果をどこへ送るか」に答えますが、もう一方の存在を読み手に知らせません。また、
`Services` の docstring は `services` を「実行が立ち上げるサーバ」と説明しています。しかし
`services.mailbox` は、実行が問い合わせるだけで起動はしない外部の受信箱です。

最後に、兄弟の target がブロックをまるごと繰り返しています。`demos/showcase/showcase.config.yaml` は、
`driver.runner.testRunner` の同じパスを6回書いています。同じ `app.launch.env` は14回です。
`defaults.platforms.<platform>` を使えば繰り返しの大半は消せますが、このファイルは使っていません。
`showcase-swiftui-noax` のような同じアプリの変種は、元の target の `app` と `driver` のブロックの大半を繰り返しています。YAML のアンカーを
使えば残りを共有できますが、ローダーはスキーマにないトップレベルのキーをすべて弾きます。アンカーを置く場所が
ファイルの中にありません。

この項目が入ると、読み手は次の3つの結果を確かめられます。

- `targets.<name>` と `defaults` は `run` を受け付けなくなります。古いファイルの読み込みは失敗し、各
  フィールドの新しい置き場所をエラーで示します。
- target が自動で適用する処理は、すべて `hooks.interrupts` という1つの一覧に並びます。シナリオの分は、
  シナリオ自身の `interrupts` に並びます。
- `demos/showcase/showcase.config.yaml` は、ランナーのパスと起動時の環境変数を1回だけ書きます。

## 詳細設計

### 3種類の割り込みエントリ

`hooks.interrupts` のエントリは、3つの形のどれか1つをとります。形はエントリが書いたキーで決まるので、
読み手が値の型から推測する必要はありません。

```yaml
hooks:
  interrupts:
    - condition: { exists: { id: [aerr_wait] } }   # アプリ内の画面（従来どおり、BE-0314）
      steps:
        - tap: { id: [aerr_close] }
    - systemAlert: notifications                   # OS のプロンプト（旧 run.systemAlertHandling.rules）
      choice: grant
    - systemAlert: savePassword
      choice: deny
      frozenScreenTimeout: 20                      # このプロンプトにだけ関わる調整値
    - tipKit: dismiss                              # Apple の TipKit のポップオーバー（旧 run.tipKitHandling）
```

| 形 | 必須のキー | 任意のキー | 置き換える設定 |
|---|---|---|---|
| 画面 | `condition` | `steps` | 変更なし |
| システムアラート | `systemAlert`、`choice` | `pollInterval`、`frozenScreenTimeout` | `run.systemAlertHandling` |
| TipKit | `tipKit` | なし | `run.tipKitHandling` |

`systemAlert` は、`systemAlertHandling.rules` がすでに使っている `SystemAlertPrompt` の名前を受け付けます。名前は
`notifications`、`tracking`、`paste`、`savePassword`、`localNetwork` の5つです。`choice` は `grant`、
`deny`、新しい値の `leave` のどれかをとります。`leave` は、そのプロンプトには何も答えないようガードに
伝えます。能動的な `handleSystemAlert` ステップも同じ `choice` の語彙を共有しますが、`leave` は読み込みで
弾きます。ボタンを押さないステップには意味がないからです。いま `paste_system_alert.yaml` のようなシナリオは `systemAlertHandling: false` と書き、
自身の `handleSystemAlert` ステップが処理することを示しています。`leave` はこの書き方を置き換えます。
`tipKit` は `dismiss` か `leave` をとります。ヒントそのものをシナリオがアサーションで確かめる場合も
あるので、シナリオは `- tipKit: leave` と書いて target の TipKit エントリを止められます。

2つの形のキーを混ぜたエントリは、読み込みで失敗します。エラーは両方の形の名前を示します。エントリに
対する既存の規則は、そのまま残ります。target 側のエントリは `target` を書けず、そのステップは `group` と
`setMocks` を使えません。

### エントリに載せる調整値

ガードには、いま2つの調整値があります。それぞれ、その値が働きかけるエントリへ移します。

- `frozenScreenTimeout` は、`savePassword` のようなアプリ内のプロンプトをシナリオが宣言したときだけ
  有効になります。そこで、アプリ自身のツリーで答えるプロンプトの `systemAlert` エントリでだけ受け付け、
  それ以外のエントリでは弾きます。この値は、ラベルのない読み込み画面がデフォルト値より長く続くといった、
  アプリの性質を表します。そのため、同じプロンプトのシナリオのエントリが値を書かなければ、target の
  エントリから受け継ぎます。
- `pollInterval` は、待機中にガードが SpringBoard を確かめる間隔です。ガードはシナリオごとに1つの
  ポーリングを持つので、値は層ごとに、今と同じ順で決まります。順は、シナリオ、`--alert-poll-interval`、
  target です。最初に値を持つ層が勝つので、シナリオは target より間隔を広げることもできます。1つの層の
  中で2つのエントリが違う値を書いた場合は、その層のもっとも小さい値を採ります。短い間隔は、長い間隔を
  求めたエントリの要求も満たすので、最小値を採っても他のエントリの要求を弱めることはありません。どの層も
  値を書かなければ、組み込みの1秒が適用されます。

### target、シナリオ、コマンドラインの重ね方

シナリオ自身の `interrupts` も、同じ3つの形を受け付けます。重ね方の規則は、この項目が統合する2つの
キーからそのまま引き継ぎます。

| エントリの形 | target とシナリオのエントリの組み合わせ方 |
|---|---|
| 画面 | 連結します。target のエントリが先です（BE-0314） |
| システムアラート | プロンプトごとに決まります。シナリオのエントリは、同じプロンプトの target のエントリの `choice` を置き換え、書かなかった `frozenScreenTimeout` は受け継ぎます。`pollInterval` は、「エントリに載せる調整値」で述べたとおり層ごとに決まります。他のプロンプトは両方とも適用されます（BE-0401、BE-0406） |
| TipKit | シナリオのエントリ（`dismiss` か `leave`）を使い、なければ target のエントリを使います。どちらにもなければ無効です |

シナリオが `choice: leave` のエントリを書くと、そのプロンプトの target の答えを置き換えます。
シナリオは、この方法で継承した答えを1つずつ止められます。`push.yaml` のように、1つのプロンプトを自分で
処理するために `systemAlertHandling: false` と書いているシナリオは、代わりに
`- systemAlert: notifications` と `choice: leave` を書きます。`false` と違って、このエントリは止める
プロンプトを名前で示します。そのため、target が後から加えたプロンプトは、そのシナリオにも適用されます。同じ層に同じプロンプトのエントリが2つあれば、
読み込みで失敗します。いま同じプロンプトの `rules` が2つあると失敗するのと同じ扱いです。

コマンドラインのフラグは、名前も優先順位の中の位置も変えません。`--no-system-alert-handling` は、
すべてのシナリオでガードを止めます。`--system-alert-handling` は、ガードを有効に戻します。
`--alert-poll-interval` は、シナリオの値と target の値のあいだという今の位置を保ちます。`--ios-tipkit-handling` と
`--no-ios-tipkit-handling` は、すべてのシナリオに TipKit のエントリを加える、または取り除きます。変わるのは
ヘルプの文面だけで、`run` の代わりに `interrupts` を指すようにします。

### もっとも強い reinstall としての erase

`app.reinstall` に3つ目の値を足します。3つの値は、実行の前にどこまで状態を消すかという1本の段階に
なります。

| 値 | 起動前に実行がすること |
|---|---|
| `overwrite` | 既存のアプリに上書きでインストールし、データを残します |
| `clean` | アンインストールしてからインストールします（組み込みのデフォルト値） |
| `erase` | 端末全体を消去してからインストールします（旧 `run.erase: true`）。端末全体を消去する手段のない Android では `clean` と同じです |

`app.reinstall` は iOS と Android にあります。web の実行は毎回新しいブラウザコンテキストから始まるので、
web には `reinstall` を置きません。シナリオは `preconditions.app.reinstall` で同じ値を指定します。web の
target では、いまの `run.erase` と同じく、この値は効果を持ちません。

`--erase` は、有効な値を `erase` にします。`--no-erase` は、有効な値が `erase` なら `clean` に下げ、
`overwrite` と `clean` はそのままにします。この段階化は、挙動を1つ意図して変えます。いまの `erase` と
`reinstall` は独立しているので、`run.erase: true` と `app.reinstall: overwrite` を持つ target に
`--no-erase` を付けると、アプリのデータが残ります。この項目のあとは、そのような target は `erase` だけを
書くことになり、`--no-erase` はアプリをアンインストールします。データを残したいシナリオは、フラグに頼らず
`preconditions.app.reinstall: overwrite` を書きます。`runsOn.seedPhotos` は有効な `app.reinstall: erase` を
求めるようになり、エラーの文面もこのキーを示します。

### evidence に加わる3つのフィールド

| フィールド | 意味 | デフォルト値 |
|---|---|---|
| `evidence.collectNetwork` | `request` アサーションのために、アプリのネットワーク通信を収集します。`network` という証跡の種類の前提になります | `true` |
| `evidence.visualCompare` | `visual` アサーションの比較方法で、`exact` か `pixelmatch` です | 組み込みの値 |
| `evidence.secrets` | `${secrets.X}` で使える環境変数名で、証跡では伏せ字になります | 空 |

`evidence.secrets` は、`run.secrets` の和集合の規則を引き継ぎます。target は
`defaults.evidence.secrets` に足すことはできても、取り除くことはできません。一覧に `null` を書くと読み込みで
失敗します。マージの処理（`bajutsu/common/config/schema/_merge.py`）は、`_UNION_LISTS` と `_NO_NULL` に
載せるパスを移し、両方から `run` を外します。

### シナリオの preconditions

target の `run` とともに、`preconditions.run` もなくなります。

| 旧シナリオキー | 新しい置き場所 |
|---|---|
| `preconditions.run.erase: true` | `preconditions.app.reinstall: erase` |
| `preconditions.run.erase: false` | `preconditions.app.reinstall: clean`、データを残すなら `overwrite` |
| `preconditions.run.systemAlertHandling` | シナリオ自身の `interrupts` のエントリ |
| `preconditions.run.tipKitHandling: true` | シナリオ自身の `interrupts` の `- tipKit: dismiss` |
| `preconditions.run.tipKitHandling: false` | シナリオ自身の `interrupts` の `- tipKit: leave` |
| `preconditions.run.inheritSetup` | `preconditions.inheritSetup` |

`inheritSetup` は、このシナリオの前に target の `hooks.setup` を実行するかを決めます。このフラグは実行の
方針ではなくシナリオの前提条件に関わるので、1つうえの階層へ移します。

### 通知の範囲

target 内の `notify` はなくなります。結果の送り先はトップレベルの `notify` の一覧だけで決まり、送信先ごとの
`targets:` で一部の target に絞ります。ある target の結果を専用のチャンネルへ送っていたチームは、その
チャンネルをトップレベルに書き、`targets: [<name>]` を添えます。チーム全体の送信先から1つの target だけを
外すには、その送信先に他の target を列挙する必要があります。このリポジトリの設定には、いまそのような除外は
ありません。

### services の意味

`Services` の docstring と `docs/configuration.md` は、`services` の説明を「実行が依存するサービス」に
改めます。実行が起動するものと、実行が接続するものの両方を含みます。キーの置き場所は変えません。
`bajutsu/common/config/effective/effective.py` には、`targets.<name>.mailbox` を指す古いコメントが
残っています。このコメントは `services.mailbox` に直します。

### target 間での値の共有

スキーマは、すでに `defaults` をすべての target に、`defaults.platforms.<platform>` をそのプラットフォームの
target にマージしています。この項目では、showcase の設定ファイルがこの仕組みを使うようにします。残る場合に
YAML のアンカーで対応できるよう、ローダーにも変更を1つ加えます。

ローダーは、名前が `x-` で始まるトップレベルのキーを無視します。このキーにアンカーを置き、target は YAML の
`<<` キーでマージします。

```yaml
x-swiftui-app: &swiftui-app
  launch: { env: { SHOWCASE_UITEST: "1" } }
  idNamespaces: [stable, horse, search, log, notice, perm, sys, net]

targets:
  showcase-swiftui-noax:
    platform: ios
    app:
      <<: *swiftui-app
      id: com.bajutsu.showcase.ios.swiftui.noax
      idNamespaces: []
```

YAML の `<<` がマージするのは1階層だけです。そのため、アンカーは target 全体ではなく、違いのあるブロック
（この例では `app`）の階層に置きます。`docs/configuration.md` は、この制約を例の隣に書きます。`x-` という
接頭辞は Docker Compose ファイルの拡張フィールドの慣習にならったもので、読み手が見慣れている可能性があります。

### 旧キーの移行先

| 旧キー | 新しい置き場所 |
|---|---|
| `run.erase` | `app.reinstall: erase` |
| `run.network`、`run.visualCompare`、`run.secrets` | `evidence.collectNetwork`、`evidence.visualCompare`、`evidence.secrets` |
| `run.systemAlertHandling.rules[]` | 規則1つにつき、`hooks.interrupts` の `systemAlert` エントリ1つ |
| `run.systemAlertHandling.pollInterval`、`.frozenScreenTimeout` | 値が働きかけるエントリの同名のキー |
| `run.systemAlertHandling: false` | 答えないプロンプトに `choice: leave`、または `--no-system-alert-handling` |
| `run.systemAlertHandling.visionInstruction` | 削除します。`run` はすでに拒否しており（BE-0402）、`record` と `crawl` は `--alert-vision-instruction` で受け取ります |
| `run.tipKitHandling: true` | `hooks.interrupts` の `- tipKit: dismiss` |
| `defaults.run.*`、`defaults.platforms.ios.run.tipKitHandling` | `defaults` の下で同じように移します |
| `targets.<name>.notify` | トップレベルの `notify` に `targets: [<name>]` を添えます |
| シナリオのフィールド | 「シナリオの preconditions」を参照してください |

`TARGET_KEYS` からは `run` を外し、単位 5 で `notify` も外します。`DEFAULTS_KEYS` からは `run` を
外します。そのため、target が受け付けるキーは9個になります。
`PlatformDefaults` とプラットフォームのレジストリからも `run` グループを外します。このグループのフィールドは
iOS の `tipKitHandling` だけだったからです。解決後の `Effective` は、内部のまとまりとして `run_defaults` を
残し、新しい置き場所から値を埋めます。

### プライムディレクティブへの適合

- **AI は判定しません。** この変更は宣言の置き場所を動かすだけで、ガードの照合は決定的なまま、モデルを
  呼びません。
- **決定性を優先します。** システムアラートのエントリは、待ち受けるプロンプトを名前で示します。どの
  エントリにも当たらないアラートは、推測したボタンで答えずにそのまま残します（BE-0406）。
- **アプリに依存しません。** アプリごとの違いは、すべて `targets.<name>` に残ります。

### 範囲外

- **セレクタに ID の2つの綴りを書くこと。** `id: [stable.row.1, stable_row_1]` のような書き方です。ドット
  区切りとアンダースコア区切りの扱いは、設定ファイルの構造ではなくセレクタの設計の問題です。BE-0221 は、
  ドライバ側の書き換えではなく明示的な候補の列挙を選びました。書き換えは `a.b` と `a-b` のような別々の ID を
  混同しうるからです。実害は検証の側にあります。Views の target が実行しないシナリオにも、誰も確かめていない
  アンダースコア版の候補が残っています。`device.yaml` を Views のレーンで実行することと、どのレーンも解決
  しない候補を消すことは、別の GitHub Issue（[#2233](https://github.com/bajutsu-e2e/bajutsu/issues/2233)）で扱います。
- **target が別の target を継承する `extends:` キー。** 「検討した代替案」を参照してください。
- **古いキーの読み込みと、移行用のコマンド。**
- **コマンドラインのフラグの改名。**

### 作業の分解

1. **割り込みエントリ。** `Interrupt` に `systemAlert` と `tipKit` の形、`leave` の選択、エントリごとの
   調整値を加えます。形を混ぜたエントリは弾きます。重ね方は `run_defaults` とアラートガードの設定で解決し、
   各フラグの優先順位の位置を保ちます。テストは、各形、各重ね方の規則、書かなかった調整値の継承、1つの
   層の中での最小値を確かめます。
2. **reinstall の段階。** `Reinstall` と、シナリオ側の `PreconditionsApp.reinstall` の Literal に `erase` を加え、`--erase` と `--no-erase` を対応づけます。
   `seedPhotos` の検査を移し、iOS と Android のライフサイクルが1つの値を読むようにします。
3. **evidence のフィールド。** `Evidence` に3つのフィールドを加え、`_merge.py` の和集合と null 禁止の
   パスを移します。
4. **`run` の削除。** `RunPolicy`、`run_model`、レジストリの `run` グループ、`PreconditionsRun`、
   `SystemAlertHandling` を取り除きます。取り除いたキーごとに、新しい置き場所を示すエラーを出します。
   `inheritSetup` を移します。`bajutsu config schema` が出力する JavaScript Object Notation（JSON）
   Schema を作り直します。
5. **通知の範囲。** `TargetConfig.notify` と、`resolve.py` にあるその解決を取り除きます。
6. **アンカー用のキー。** `Config` が `x-` で始まるトップレベルのキーを無視するようにします。マージした
   アンカーが、書き下したブロックと同じく検証されることをテストで確かめます。
7. **設定ファイルとシナリオ。** `demos/` の設定ファイルとシナリオを新しいキーへ書き換えます。showcase の
   共有値は、`defaults.platforms` とアンカーへ移します。
8. **ドキュメント。** `docs/configuration.md`、`docs/scenarios.md`、`docs/cli.md`、
   `docs/architecture.md`、`DESIGN.md` と、それぞれの `docs/ja/` 側を更新します。`services` の説明と、
   `effective.py` の古いコメントを直します。

## 検討した代替案

- **`run` を `erase`、`network`、`visualCompare` だけにして残す案。** 2つの割り込みと `secrets` を外せば
  `run` は小さくなります。しかし定義は「実行がするその他のこと」のままです。置き場所が明らかでない次の
  フィールドもここに入り、グループはまた混ざっていきます。
- **割り込みを新しい `hooks.guards` ブロックに分ける案。** 画面の形と組み込みのガードを、形の違う2つの
  一覧に分けて保てます。それでも、1つの問いのために読む場所は2つ残ります。3つの形はキーで区別できるので、
  1つの一覧にしても曖昧になりません。
- **調整値をガード全体の1つのブロックに置く案。** `hooks.systemAlertGuard: { pollInterval,
  frozenScreenTimeout }` のようなブロックは、どちらの値もガード全体のものという実行時の姿に合います。
  ただし、読む場所が2つに戻ります。`frozenScreenTimeout` はもともと1種類のプロンプトにしか働かず、
  `pollInterval` は「層ごとに決め、層の中では最小値」という規則で意味が1つに決まるので、エントリに
  載せます。
- **1つのシナリオでガード全体を止める形を足す案。** `- systemAlert: all` と `choice: leave` のエントリが
  あれば、今の `systemAlertHandling: false` と同じ力を保てます。そのかわり、特別なプロンプト名が1つ増え、
  シナリオが名前を挙げていないプロンプトまで止めるエントリになります。プロンプトを名前で1つずつ止めれば、
  シナリオが止める答えはすべてファイルに見えます。実行全体を止めるには、`--no-system-alert-handling` が
  残ります。
- **`erase` を `runsOn` へ移す案。** `erase` は `locale` や `seedPhotos` と同じく端末の状態です。しかし
  シナリオの `preconditions.runsOn` はプラットフォームごとに分かれているので、複数のプラットフォームを
  扱うシナリオはプラットフォームの数だけ `erase` を書くことになります。Android のライフサイクルはすでに
  `erase` と `reinstall` を1つの判断として読んでおり、`app.reinstall` のほうが振る舞いに合います。
- **`secrets` を target のトップレベルへ移す案。** `secrets` は `${secrets.X}` の入力にもなるので、証跡だけの
  設定ではありません。独立したキーにすれば、target のキーは10個になります。それでも、このフィールドが
  ある理由は伏せ字にすることで、その規則を持つ `evidence.redact` がすぐ隣にあります。
- **target 内の `notify` を残し、送信先ごとの絞り込みを外す案。** `defaults.notify` と target による
  上書きで、他のグループと同じ重ね方になります。そのかわり、送信先の全体像がすべての target に散らばります。
  「失敗を誰が知るのか」には、1つの一覧を読んで答えられるほうを選びます。
- **`extends:` キーを足す案。** target が別の target を深いマージで継承できます。YAML の1階層だけの
  マージでは届かない、入れ子のフィールドまで共有できます。そのかわり、10個目のキーが戻り、循環と多段の
  継承の規則も増えます。アンカーを使ったあとも showcase の変種が入れ子のブロックを繰り返すようなら、この案を
  見直す価値があります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 単位 1：割り込みエントリ
- [x] 単位 2：reinstall の段階
- [x] 単位 3：evidence のフィールド
- [x] 単位 4：`run` の削除
- [x] 単位 5：通知の範囲
- [x] 単位 6：アンカー用のキー
- [x] 単位 7：設定ファイルとシナリオ
- [x] 単位 8：ドキュメント

ログ：

- [#2242](https://github.com/bajutsu-e2e/bajutsu/pull/2242)：単位 3、5、6 を実装しました。`evidence` に `collectNetwork`、`visualCompare`、`secrets` を
  加え、`run` はこの 3 つのキーを、移動先を示すエラーで拒否します。ターゲットは `notify` を受け付けなく
  なり、ローダは `x-` で始まるトップレベルのキーを無視します。これらのキーを書いているデモの設定と
  ドキュメントも同じ変更で直しました。後の単位が動かすキーが残るので、単位 7 と 8 は未完了のままです。
  *通知の範囲* からは 1 点ずれています。エンドポイントの `targets:` は config のターゲット名ではなく
  シナリオ名で絞り込むので、ターゲット自身の `notify` を config で置き換える手段はありません。
  それでも、メンテナの判断で設計どおり削除しました。
- [#2263](https://github.com/bajutsu-e2e/bajutsu/pull/2263)：単位 2 を実装しました。`subject.reinstall` と `preconditions.subject.reinstall` は `overwrite`、
  `clean`、`erase` のいずれかを取り、`run.erase` と `preconditions.run.erase` は読み込み時に
  `subject.reinstall: erase` を示すエラーになります。`--erase` と `--no-erase` はこの値を段階に沿って
  動かし、iOS と Android のライフサイクルはこの 1 つの値だけを読みます。`seedPhotos` には実効の
  `erase` が必要です。保存済みの実行プランにある `erase: true` は `reinstall: erase` として読み込みます。
  `run.erase` を書いているデモのシナリオとドキュメントも同じ変更で直しました。*もっとも強い reinstall
  としての erase* からは 2 点ずれています。1 つ目に、`defaults` には `app` がないので、共有する erase は
  `defaults.platforms.<platform>.app` に書きます。2 つ目に、実行時にプライマリターゲットの erase を
  各シナリオへ書き込むのをやめたので、シナリオが動かすターゲットはそれぞれ自身の `subject.reinstall` を
  マージし、`--no-erase` も各ターゲット自身の `erase` を下げます。この結果が 2 つあります。iOS の
  デバイスグループで、リードが消去しなかった Simulator 上でメンバーが erase を求めると、消去せずに
  動くのではなく失敗します。また、保存された実行プランには、ターゲットが与えた reinstall ではなく
  シナリオが書いた reinstall が残ります。また、*旧キーの移行先* の表より後に加わった `run.ignoredSettings`（BE-0467）は
  まだ `run` の下にあり、単位 4 で移動先を決める必要があります。
- [#PR_NUMBER](https://github.com/bajutsu-e2e/bajutsu/pull/PR_NUMBER)：単位 1、4、7、8 を実装し、この項目を完了しました。`hooks.interrupts` と
  シナリオ自身の `interrupts` は、画面の形式に加えて `systemAlert` と `tipKit` の形式を取ります。
  `choice: leave`、エントリごとの `pollInterval` と `frozenScreenTimeout`、および *target、シナリオ、
  コマンドラインの重ね方* が述べる重ね方もあわせて実装しました。ターゲット、`defaults`、
  `defaults.platforms.<platform>`、シナリオの preconditions から `run` を取り除き、旧キーは読み込み時に
  移動先を示すエラーになります。`inheritSetup` は `preconditions.inheritSetup` へ移しました。保存済みの
  実行プランにある旧キーは、新しいエントリとして読み込みます。デモのシナリオ、showcase の設定、
  ドキュメントも同じ変更で直しました。*詳細設計* からは 4 点ずれています。1 つ目に、`run.ignoredSettings`
  は `runsOn.ignoredSettings` へ移しました。`runsOn` が示す実行環境についての設定だからで、移動先は
  メンテナが選びました。2 つ目に、ガードが持つ画面停止の上限は 1 つなので、応答するエントリのうち
  複数が `frozenScreenTimeout` を書いたときは、もっとも寛容な値を採ります。つまり最大の値で、0 は
  どの値よりも優先します。3 つ目に、`systemAlertHandling: false` がなくなったため、ガード全体を切り替える
  設定はありません。`--system-alert-handling` は既定値を述べ直すだけになり、`record` と `crawl` も
  ターゲットのスイッチを読まなくなりました。4 つ目に、showcase の設定はすでに `templates`（BE-0471）で
  runner のパスと起動時の環境変数を 1 か所に書いていたので、単位 7 では値を `defaults.platforms` や
  `x-` アンカーへ移していません。

## 参考

- [`bajutsu/common/config/schema/run_policy.py`](../../bajutsu/common/config/schema/run_policy.py)：
  この項目が取り除くグループです。
- [`bajutsu/common/config/schema/hooks.py`](../../bajutsu/common/config/schema/hooks.py) と
  [`bajutsu/common/scenario/models/steps/interrupt.py`](../../bajutsu/common/scenario/models/steps/interrupt.py)：
  この項目が広げる割り込みの一覧です。
- [`bajutsu/common/scenario/models/scenario/system_alert_handling.py`](../../bajutsu/common/scenario/models/scenario/system_alert_handling.py)：
  割り込みエントリになる宣言です。
- [`bajutsu/common/config/schema/_merge.py`](../../bajutsu/common/config/schema/_merge.py)：和集合と
  null 禁止の規則です。
- [`docs/configuration.md`](../../docs/configuration.md)：いま文書化されている target のキーです。

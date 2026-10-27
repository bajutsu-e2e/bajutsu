[English](BE-0468-runson-family-and-any-of.md) · **日本語**

# BE-0468 — runsOn で iOS のデバイスファミリーと、デバイスやブラウザの種類の集合を指定できるようにする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0468](BE-0468-runson-family-and-any-of-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0468") |
| トピック | Driver & backend architecture |
<!-- /BE-METADATA -->

## はじめに

ターゲットの `runsOn` は、必要なデバイスを正確な文字列で、ブラウザを Playwright のエンジンで指定します。
本項目は、ターゲットの実行環境を種類で指定する方法を 2 つ加えます。

- **`runsOn.ios.family`** は iOS のデバイスファミリーを指定します。`family: [iphone, ipad]` は優先順位
  リストで、プールにある最初のファミリーからデバイスを 1 台使います。デバイスは Simulator でも、
  接続した実機でもかまいません。
- **`kind`** は、ターゲットが対応する実行環境の種類を指定します。`runsOn.ios.kind: [simulator, device]`
  や `runsOn.browser.kind: [chrome, edge, firefox]` のように書きます。引数がなければ、挙げた種類それぞれで
  1 回ずつ実行します。コマンドラインの引数で種類を指定すると、その種類だけで実行します。

`runsOn.browser.kind` は `engine` を置き換えるので、ブラウザのターゲットは Playwright の 3 つのエンジンに
加えて Chrome と Edge も指定できます。サーバーの worker は、ジョブを引き受ける前に実機のファミリーを
検査します。

## 動機

現状の config は「どの iPad でもよい」と書けません。デバイスタイプを 1 つ名指しする必要があり、
Apple は Xcode のリリースごとにデバイスタイプを改名・入れ替えします。`"iPad Pro 13-inch (M4)"` を
固定したターゲットは、その型を持たない Xcode のホストで一致しなくなります。シナリオが必要としたのは
タブレット程度の画面だけなのに、run は `model` の条件不一致で失敗します。モデルを書かないターゲットなら
失敗は避けられますが、意図も失われます。run はプール順で最初のモデルを使うので、ホストによって
iPhone になることも iPad になることもあります。`model` のリストも役に立ちません。リストは値ごとに
1 回ずつ実行する意味なので、`model: [iPhone …, iPad …]` と書くと、どちらか一方でよい場合でも run が
倍になります。

ターゲットはまた、デバイスの種類を 1 つしか宣言できません。Simulator でも実機でも同じように動くアプリは
多いのに、両方を対象にするには、ほかの設定が同じターゲットを 2 つ作る必要があります。ターゲットを
名指しするシナリオはそのどちらかを選ばなければならず、両方を名指しすると、2 台を同時に使う
複数ターゲットのシナリオになってしまいます。

ブラウザにも同じ隙間があります。`engine` が指定するのは Playwright のレンダリングエンジン（`chromium`、
`firefox`、`webkit`）で、利用者が使うブラウザではなく実装の詳細です。config は、Playwright が
`chromium` エンジンにチャネルを添えて起動する Chrome や Edge を指定できません。1 つの選択に名前が
2 つ並んでもいます。config では `engine`、コマンドラインでは `--browser` です。

出荷後に確かめられる結果は具体的です。`runsOn.ios.family: [iphone, ipad]` と書いた config は、Xcode の
更新ですべてのデバイスタイプが改名されても、config を直さずに解決し続けます。`kind: [simulator, device]`
を持つターゲット 1 つで、各シナリオを両方で実行でき、`--kind device` なら実機だけで実行します。各 run の
ラベル、マニフェスト、`doctor` の一覧には、その run が使った種類、ファミリー、モデルが表示されます。

### 対象範囲

本項目は、対になる提案「runsOn を実行先ごとにまとめ、シナリオの runsOn を廃止する」の上に載り、
その提案が先に入ります。対になる提案は、すべてのフィールドを `runsOn.ios`、`runsOn.android`、
`runsOn.browser` の下へ移し、シナリオの `runsOn` を取り除きます。本項目はそのフィールドの一部の意味を
変えるだけで、フィールドは移しません。Android には `family` も `kind` も加えません。`avd` を省略した
Android ターゲットはすでに任意のエミュレーターを受け入れ、Android のフォームファクター（スマートフォンか
タブレットか）には別のデバイスの読み取りが要るので、どちらも本項目の外に置きます。

## 詳細設計

### 2 種類のリスト

本項目のリストには 2 つの意味があり、各フィールドはそのどちらか 1 つだけを持ちます。

| フィールド | リストの意味 | 引数での選択 |
|---|---|---|
| `runsOn.ios.kind` | 挙げた種類ごとに 1 回ずつ実行 | `--kind` |
| `runsOn.browser.kind` | 挙げたブラウザごとに 1 回ずつ実行 | `--browser`、`--browsers` |
| `runsOn.ios.family` | プールにある最初のファミリーで 1 回実行 | なし |
| `runsOn.ios.model`、`runsOn.android.avd` | 従来どおり、挙げた値ごとに 1 回ずつ実行 | なし |

`kind` のリストは、ターゲットが対応する実行環境を宣言します。そのため run はそれぞれを対象にし、現在の
[BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines-ja.md) のクロスブラウザの
マトリックスと同じように動きます。`family` のリストは、どのデバイスなら足りるかを述べます。そのため、
合う最初のものを 1 回だけ使います。`family` のリストでは記載順が結果を決め、同じプールからは常に同じ
結果になります。

### iOS の `kind`

`runsOn.ios.kind` は `simulator`、`device`、またはその両方のリストを取ります。単独の値は現在と同じに
振る舞います。`kind` がなければ、ターゲットは従来どおり Simulator で実行します。

```yaml
targets:
  app:
    platform: ios
    app: { id: com.example.app }
    runsOn:
      ios:
        kind: [simulator, device]   # --kind で選ばなければ、それぞれで 1 回ずつ
        family: ipad
```

`bajutsu run --target app` は、各シナリオを 2 回実行します。iPad の Simulator で 1 回、接続した iPad で
1 回です。`bajutsu run --target app --kind device` は、接続した iPad で 1 回だけ実行します。`app` を
名指しするシナリオは 1 ファイルのままで、config も両方の種類に対してターゲット 1 つで済みます。

`--kind` は種類を 1 つ、またはカンマ区切りのリストで取ります。指定する種類は、ターゲットが挙げたもので
なければなりません。挙げていない種類を指定すると終了コード 2 で終わり、ターゲットの種類を示します。
`kind` が単独のターゲットでは、その種類を指定しても、フラグを省いてもかまいません。

実行できるデバイスのない種類は、その run が失敗し、種類を示します。現在、デバイスのない `model` を
挙げたときと同じ扱いです。run を黙って飛ばすことはありません。実機のないホストでは、2 種類を挙げた
ターゲットを `--kind simulator` で実行します。

### web の `kind`

`runsOn.browser.kind` は `engine` を置き換えます。各値はブラウザと、Playwright での起動方法を表します。

| `kind` | Playwright での起動 | 備考 |
|---|---|---|
| `chromium` | `chromium` エンジン | 同梱の Chromium。`kind` を書かないときのデフォルト |
| `chrome` | `chromium` エンジン、チャネル `chrome` | ホストにインストールした Google Chrome |
| `edge` | `chromium` エンジン、チャネル `msedge` | ホストにインストールした Microsoft Edge |
| `firefox` | `firefox` エンジン | Playwright の Firefox |
| `webkit` | `webkit` エンジン | Playwright の WebKit。Playwright は Safari を動かせないので、最も近い代わり |

```yaml
targets:
  site:
    platform: web
    app: { url: https://example.com }
    runsOn:
      browser:
        kind: [chrome, edge, firefox]   # --browser で選ばなければ、それぞれで 1 回ずつ
```

`--browser <kind>` はブラウザ 1 つで、`--browsers <kind>,<kind>` は一部のブラウザで、それぞれ 1 回ずつ
実行します。フラグは `kind` の値を取るので、`--browser chrome` と書けます。種類を挙げたターゲットでは、
指定する種類は挙げたものでなければならず、そうでなければ終了コード 2 で終わります。`kind` を書かない
ターゲットでは、現在の `--browser` が任意のエンジンを取れるのと同じく、任意の種類を指定できます。

### iOS の `family`

| フィールド | 意味 |
|---|---|
| `family: iphone` | ファミリーが iPhone であるデバイス 1 台 |
| `family: [iphone, ipad]` | プールにある、最初に挙げたファミリーのデバイス 1 台 |
| `family` を省略 | 従来どおりプールの任意のデバイス |

`family` が取る値は `iphone` と `ipad` です。どちらも小文字で、大文字と小文字を区別します。
Simulator のファミリーは、`simctl list devicetypes` がデバイスタイプごとに報告する `productFamily`
（`iPhone`、`iPad`）です。ほかのファミリー（Apple TV、Apple Watch）のデバイスタイプは読み取れますが、
一致することはありません。Simulator も実機もファミリーを報告するので、`family` はどの `kind` とも
組み合わせられます。

### ローダーが強制する規則

どの規則も、チームのデフォルト値を畳み込んだ後の config ブロックで判定します。

- `family` と `model` を併記すると、読み込みが失敗し、両方のフィールド名を示します。継承された側は
  ターゲットが `null` で解除します。
- `kind` に `device` が含まれるとき、`model` と `os` を書くと読み込みが失敗します。実機ではこれらを
  確かめられないからです。Simulator だけのターゲットは、従来どおり両方を書けます。
- `appium` のグリッドや Device Farm のジョブが担うターゲットには読み取るプールがないので、現在の
  すべての条件と同じく、`family` もデバイスの作業の前に失敗します。
- ブラウザの種類のリストと `runsOn.browser.version` の併記は、現在のエンジンのリストと同じ理由で
  読み込みが失敗します。ブラウザごとにバージョンの付け方が違うからです。
- 空のリスト、重複した項目、未知の種類やファミリーは、読み込みが失敗し、許される値を示します。
- `runsOn.browser.engine` は読み込みが失敗し、`kind` を示します。メッセージには、`kind` のリストも
  `engine` のリストと同じく挙げたブラウザごとに 1 回ずつ実行するので、書き換えても run は減らないことを
  書きます。

`family` は、Simulator では `os` と組み合わせられます。バージョン範囲は従来どおり run を倍増させます。
メジャーバージョンごとの run は、そのバージョンのデバイスの中から自分のファミリーを選びます。

### run の解決方法

`bajutsu/common/devices/runs.py` の `form_target_runs` は、すでにプールのデバイス情報を受け取って
います。

1. 選ばれた種類ごとに run を 1 つ作ります。選ばれた種類とは、挙げたすべての種類か、`--kind` が指定した
   種類です。挙げた種類は、現在の `model` のリストと同じく座標 `kind=device` を加えます。
2. run の中では、その run の種類のデバイスを残します。ファミリーを順に見て、そのファミリーのデバイスの
   うち、run の `os` 条件を満たすものを残します。
3. 該当するデバイスが 1 台以上ある最初のファミリーが勝ちます。run はそれを `family` に記録します。
4. 挙げたファミリーのどれにもデバイスがなければ、run は失敗します。理由には、種類、挙げたファミリー、
   利用可能なデバイスを書きます。現在の `model` 欠落と同じ扱いです。

`eligible_set` は、勝ったファミリーに絞ってから既存の規則を適用します。規則は、プール順で最初のモデル、
その中で台数が足りる最新のリリースです。必要台数が勝ったファミリーのデバイス数を超えても、規則は
勝者の外を探しません。その run は失敗し、ファミリー名を示します。ここでは、収まる最大の組み合わせより
決定的な選択を優先します。

2 つ以上のファミリーから 1 つを選んだ run には、座標 `family=ipad` が加わります。座標は run のラベル、
マニフェストの `run_coordinates`、flakiness のキー（デバイス OS の隣）に表示されます。座標がないと、
勝者の違うホストにまたがって同じシナリオが 1 本の履歴に入り、iPhone と iPad のレイアウト差が
flakiness に見えてしまいます
（[BE-0358](../BE-0358-device-os-as-a-first-class-fact/BE-0358-device-os-as-a-first-class-fact-ja.md)）。
`family: ipad` の単独指定は、単独の `model` と同じく座標を加えません。

両方の種類を挙げたターゲットには、両方を含むプールが要ります。プールは `--udid` で渡したすべての udid で、
各 udid はそれを示す一覧の種類に属します。Simulator なら `simctl`、実機なら `devicectl` です。どちらの
一覧にも出ない udid は `DeviceReadError` にします。各 run の種類が、その run の XCUITest の実行先を
決めます。現在は `kind` がターゲット全体の実行先を決めています。リストに `device` を含むターゲットには、
`kind: device` のターゲットと同じく、署名した実機用の runner が要ります。

web では、各 run のブラウザの種類が `TargetRun` の既存のエンジンの欄に入り、チャネルがその隣に並びます。
`bajutsu/run/device_matrix.py` の `target_pool` は、`read_web` でブラウザごとに `DeviceFacts` を 1 つ
読みます。この読み取りはブラウザをヘッドレスで起動し、起動できなければ `DeviceReadError` を送出します。
そのため、ホストにない Chrome や Edge は、その run が確実に失敗として現れます。ドライバーはチャネルを
Playwright の `launch` に渡します。

`record`、`crawl`、`repl` は run を倍増させません。`--kind` や `--browser` が種類を指定しなければ最初に
挙げた種類を使い、最初に解決できるファミリーを使い、何も合わなければ終了コード 2 で終わります。

### ファミリーの読み取り

`bajutsu/common/devices/facts.py` の `DeviceFacts` に `family: str | None` を加えます。`read_ios` は、
すでに名前を引くために探しているデバイスタイプの `productFamily` から読み取ります。`productFamily` の
ないデバイスタイプは `DeviceReadError` にします。読み取りは推測せず、確実に失敗する方針だからです。
Android と web のデバイスは `None` を持ちます。

実機には専用の読み取り `read_ios_device` を加えます。プールの各 udid を
`xcrun devicectl list devices` の JSON 出力から読み、`hardwareProperties.deviceType`（`iPhone`、
`iPad`）をファミリーとします。一覧に出ない udid や、デバイスタイプを報告しない udid は
`DeviceReadError` にします。読み取るのはファミリーだけです。実機のモデルとリリースも読めますが、
`model` の条件が比べる相手は、Simulator のデバイスタイプ名ではなく `iPad13,8` のような製品識別子に
なります。この識別子での照合には別の設計が要るので、実機では `model` と `os` を引き続き拒否します。
読み取りはデバイスの作業の前に行い、Simulator 向けに `read_ios` が担うプールの読み取りと同じ段階で
動きます。

### worker での検査

サーバーの worker は、自分が動かせる実機をすでに知っています。そこで、実機の `family` は worker が
ジョブを引き受ける前に検査します。この検査は
[BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues-ja.md) の capability
ルーティングを使います。このルーティングでは、ジョブが必要なトークンを持ち、worker はそのすべてを
広告しているときに限りジョブを借ります（`bajutsu/serve/capabilities.py` の `can_serve`）。worker は
すでに、Simulator の一覧から `iphone` と `ipad` を広告しています（`simctl_capabilities`）。

- **worker が広告するもの。** worker は、接続している実機について `device:iphone` と `device:ipad` の
  トークンを加えます。読み取りには `read_ios_device` と同じ `devicectl` の読み取りを使います。実機は
  抜き差しされるので、登録時に一度だけではなく、ジョブを借りようとするたびにトークンを更新します。
  `device:` は `environment:` や `host:` と同じく予約済みの接頭辞にし、運用者が手で書いたトークンで
  worker にない実機を名乗れないようにします。
- **ジョブが要求するもの。** `kind` のリストは別々の run を作るので、各 run は自分の種類でルーティング
  されます。ファミリーを 1 つだけ書いた実機の run は、そのトークン（たとえば `device:ipad`）を要求します。
  ファミリーの優先順位リストは、どれか 1 つで足りるので、部分集合の要求にはできません。代わりに run の
  ジョブは any-of のグループ `[device:iphone, device:ipad]` を、必要トークンと並べて持ちます。
  Simulator の run のグループは、既存の `iphone` と `ipad` のトークンを使います。`can_serve` は、worker が
  必要トークンをすべて広告し、かつ各グループのトークンを 1 つ以上広告しているときにジョブを受け入れます。
- **ジョブを借りた worker がすること。** worker は、ローカルの `run` と同じく自分のプールに対して
  ファミリーのリストを解決します。どの worker が実行するかはルーティングが決め、その worker のどの
  デバイスで実行するかはファミリーのリストが決めます。勝者は run の座標に入るので、flakiness のキーは
  異なるファミリーを使った run を区別します。

どの worker も受け入れられないジョブは、現在の満たされないトークンを持つジョブと同じく、キューに
残ります。ジョブを借りてから run までのあいだに実機が外れた場合、worker がプールを読む時点で run は
失敗します。読み取りはデバイスを読み飛ばさず、`DeviceReadError` を送出するからです。ルーティングは
判定に関わりません。ジョブをどこで実行するかを決めるだけで、run 自体は決定的なままです。

### スキーマ、レポート、ドキュメント

- `bajutsu/common/config/schema/platform/ios.py` の `IosRunsOn` に `family` を加え、`kind` がリストを
  受け付けるようにします。`web.py` の `WebBrowser` では `engine` を `kind` に置き換え、値に `chrome` と
  `edge` を加えます。`WEB_ENGINES` は、上のブラウザの種類の表に置き換わります。
- `bajutsu config schema` は、`family` と 2 つの `kind` を、enum またはその enum のリストとして出力します。
- `run`、`record`、`crawl`、`repl` に `--kind` を加えます。`--browser` と `--browsers` はブラウザの種類を
  取り、ターゲットのリストと照らし合わせます。
- `doctor` は、各 run を、その種類、勝ったファミリー、合致したデバイスとともに一覧します。
- [設定](../../docs/ja/configuration.md#device-conditions-runson)と英語版に、表と例を載せます。
  [CLI](../../docs/ja/cli.md)には `--kind` と、`--browser` の新しい値を書きます。
  [アーキテクチャ](../../docs/ja/architecture.md)では、`common/devices/` の `facts.py` と `runs.py` の
  行を更新します。

### 原則との整合

run に LLM は入りません。解決は config、引数、プールだけの純粋関数で、同じ入力からは同じ run が
できます。曖昧な指定のとき、最初に一致したものを黙って使うことはありません。`family` のリストは順序を
先に固定し、`kind` のリストはすべての run を名指しし、どの項目にも合わないプールでは run が失敗します。
差異はすべて config に置きます。

### 対象外

- 実機での `model` と `os`。理由は「ファミリーの読み取り」に書きます。
- Safari そのもの。Playwright が動かすのは Safari ではなく WebKit なので、`webkit` を代わりに使います。
- Android の `family` と `kind`。理由は「対象範囲」に書きます。
- `emulate` の優先順位リスト。これはプールのデバイスではなく Playwright のビューポートプロファイルを
  選ぶので、解決すべき可用性がありません。
- `model` に対する glob や正規表現の照合。理由は代替案の表に書きます。
- `model` と `avd` のリストの意味の変更。既存の config の run は変わりません。

### 作業分解

1. **ファミリーの読み取り。** `DeviceFacts` に `family` を加え、`read_ios` で `productFamily` から
   読みます。実際の `simctl list devicetypes` で、iPhone と iPad のすべての型が `productFamily` を
   持つことを確かめます。実機向けに `devicectl` を使う `read_ios_device` を加え、接続した iPhone と
   iPad で `hardwareProperties.deviceType` がファミリーを持つことを確かめます。
2. **スキーマ。** `runsOn.ios.family` を加え、`runsOn.ios.kind` がリストを取れるようにし、
   `runsOn.browser.engine` を `kind` に置き換え、読み込み時の規則と `engine` の移行メッセージを加えます。
   JSON Schema の出力も広げます。
3. **解決。** 選ばれた種類ごとに run を作り、Simulator と実機が混ざったプールを読み、
   `form_target_runs` でファミリーのリストを解決し、`eligible_set` を勝ったファミリーで絞り、`kind` と
   `family` の座標を加えます。`first_run` と `declared_runs` も対象です。`declared_runs` には解決に使う
   プールがありません。そのためファミリーのリストを、プール次第でファミリーが決まる 1 つの run として
   示します。開いた範囲を `PER_AVAILABLE_MAJOR` で表すのと同じ扱いです。
4. **引数と起動。** `--kind` を加え、`--browser` と `--browsers` がブラウザの種類を取ってターゲットの
   リストと照らし合わせるようにし、チャネルを Playwright の `launch` に渡し、各 run の種類から XCUITest の
   実行先を決めます。
5. **レポート。** 種類と勝ったファミリーを、run のラベル、マニフェスト、`doctor`、flakiness のキーに
   表示します。
6. **worker のルーティング。** worker に接続した実機から `device:iphone` と `device:ipad` を広告し、
   ジョブを借りるたびに更新します。`device:` 接頭辞を予約し、run の要求にファミリーの any-of の
   グループを加え、`can_serve` がそれを受け入れるように広げます。
7. **ドキュメントとデモ。** 上記の日英ドキュメントを更新し、例を設定ページの `runsOn` の節へ日英両方で
   載せます。デモ config の 1 つを正確なモデル名から `family` に、もう 1 つを `engine` から `kind` に
   切り替えます。

各ユニットが自分のテストを持ちます。スキーマの規則、移行メッセージ、引数の検査、解決の分岐ごとの
偽プール、失敗メッセージです。

## 検討した代替案

| 代替案 | 退けた理由 |
|---|---|
| `kind` のリストを優先順位リストにし、合う最初の種類で 1 回実行する | どの種類で実行したかがプールの陰に隠れ、両方の種類を対象にするにはやはりターゲットが 2 つ要ります。挙げた種類をすべて実行し、引数で絞る形なら、ターゲットの対応範囲を 1 回書くだけで、実行のたびに選べます。 |
| 「どちらの種類でもよい」を表す `kind: any` | 種類のあいだの順序が config ではなく仕様に置かれ、`any` では「両方で別々に実行」を表せません。 |
| `runsOn.ios` の下に実行環境のブロックを並べる | 各ブロックに `os` や `model` を持たせられますが、フィールドのリストの隣に 2 つ目の構造が増えます。`kind` のリストと `family` で必要は満たせます。 |
| 種類ごとにターゲットを 2 つ作る | 現在も使えますが、ターゲットの設定が倍になり、ターゲットを名指しするシナリオはどちらか 1 つを選ぶ必要があります。 |
| `engine` を残し、ブラウザ名のフィールドを別に足す | 同じ選択を 2 つのフィールドが表し、`engine` は Playwright の実装を config の作成者に見せてしまいます。 |
| `anyOf` を優先順位の意味のまま `engine` に改名する | 既存の `engine` のリストが、黙ってエンジンごとの実行から 1 回の実行に減ります。また `chrome` はエンジンではありません。 |
| `model` のリストを any-of の意味にする | 既存の複数モデル config がすべて、黙って「各々を実行」から「1 つを実行」に変わります。チームが頼っているマトリックスを壊します。 |
| `model: "iPad*"` のような glob 照合 | Apple の命名に依存します。本項目が取り除きたい壊れやすさそのものです。ファミリー接頭辞の改名でも、正確な名前が壊れるのと同じように壊れます。 |
| `idiom: phone \| tablet` によるプラットフォーム横断の指定 | 全プラットフォームでフォームファクターの読み取りが要り、Android と web のエミュレーションにも及びます。本項目の iOS と web の要望は、これなしで満たせます。後続の項目で上に載せられます。 |
| `family` の同順位の決定にプール順を使う | 単純ですが、勝者がホストのデバイス順に左右されます。flakiness の履歴が、異なるデバイスにまたがって 1 本のシナリオを束ねてしまいます。 |
| 勝者のデバイスが必要台数に足りないとき、低順位のファミリーを選ぶ | 収まる最大の組み合わせにはなりますが、ファミリーが `--workers` とデバイス台数に左右されます。並列度で run の素性が変わってしまいます。 |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] ユニット 1: ファミリーの読み取り
- [ ] ユニット 2: スキーマ
- [ ] ユニット 3: 解決
- [ ] ユニット 4: 引数と起動
- [ ] ユニット 5: レポート
- [ ] ユニット 6: worker のルーティング
- [ ] ユニット 7: ドキュメントとデモ

## 参考

- [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md)：本項目が拡張する `runsOn` の条件、デバイスの照合、run。
- [BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines-ja.md)：ブラウザの `kind` が引き継ぐクロスブラウザのマトリックスと、`--browser` と `--browsers` のフラグ。
- [BE-0358](../BE-0358-device-os-as-a-first-class-fact/BE-0358-device-os-as-a-first-class-fact-ja.md)：flakiness のキーが読むデバイス情報。
- [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues-ja.md)：worker がジョブを借りる前に実機のファミリーを検査するのに使う capability ルーティング。
- `bajutsu/common/devices/facts.py` と `bajutsu/common/devices/runs.py`：ファミリーを読み取り、解決する場所。

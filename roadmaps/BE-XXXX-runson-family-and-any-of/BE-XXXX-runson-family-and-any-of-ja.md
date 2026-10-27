[English](BE-XXXX-runson-family-and-any-of.md) · **日本語**

# BE-XXXX — runsOn で正確なモデル名の代わりにデバイスファミリーやいずれかのブラウザを指定できるようにする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-runson-family-and-any-of-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Driver & backend architecture |
<!-- /BE-METADATA -->

## はじめに

ターゲットの `runsOn` は、必要なデバイスを正確な文字列で指定します。iOS の `model` は、
Simulator のデバイスタイプ名（たとえば `"iPad Pro 13-inch (M4)"`）と一致しなければなりません。
web の `browser.engine` はエンジン 1 つで、エンジンのリストは run を倍増させます。本項目は、
モデル名を書かずに「このうちのどれか」と書く方法を 2 つ加えます。iOS では
`family: [iphone, ipad]` と書くと、プールにある最初のファミリーから Simulator を 1 台使います。
web では `browser: { anyOf: [chromium, firefox, webkit] }` と書くと、起動できた最初のエンジンを
使います。どちらも run は 1 つで、マニフェストが各 run の選択結果を記録します。

## 動機

現状の config は「どの iPad でもよい」と書けません。デバイスタイプを 1 つ名指しする必要があり、
Apple は Xcode のリリースごとにデバイスタイプを改名・入れ替えします。`"iPad Pro 13-inch (M4)"` を
固定したターゲットは、その型を持たない Xcode のホストで一致しなくなります。シナリオが必要としたのは
タブレット程度の画面だけなのに、run は `runsOn.ios.model` 不一致で失敗します。モデルを書かない
ターゲットなら失敗は避けられますが、意図も失われます。run はプール順で最初のモデルを使うので、
ホストによって iPhone になることも iPad になることもあります。

リスト構文はこの隙間を埋めません。`model` や `browser.engine` のリストは、値ごとに 1 回ずつ実行する
意味です。そのため、`model: [iPhone …, iPad …]` と書くと、どちらか一方でよい場合でも run が倍になります。
「どれか 1 つで足りる」を表す書き方が、config にはありません。

出荷後に確かめられる結果は具体的です。`family: [iphone, ipad]` と書いた config は、Xcode の更新で
すべてのデバイスタイプが改名されても、config を直さずに解決し続けます。各 run のラベル、マニフェスト、
`doctor` の一覧には、その run が使ったファミリーとモデルが表示されます。

### 対象範囲

対象は iOS（`family`）と web（`browser.anyOf`）です。Android は変更しません。`avd` を省略した
Android ターゲットは、すでに任意のエミュレーターを受け入れるので、「何でもよい」を表す書き方が
現状にもあります。Android のフォームファクターは別の事実であり、
デバイスの読み取りも別に要るため、本項目の外に置きます。

## 詳細設計

### config の書き方

| プラットフォーム | フィールド | 意味 |
|---|---|---|
| iOS | `runsOn.family: iphone` | デバイスタイプのファミリーが iPhone である Simulator 1 台 |
| iOS | `runsOn.family: [iphone, ipad]` | プールにある、最初に挙げたファミリーの Simulator 1 台 |
| iOS | `runsOn.family` を省略 | 従来どおり任意の Simulator |
| web | `runsOn.browser.anyOf: [chromium, firefox]` | 起動できた、最初に挙げたエンジン |

`family` と `anyOf` のリストは優先順位リストです。最初の項目が合えばそれを使い、それより前の
項目に合うデバイスやエンジンがないときに限り、次の項目へ進みます。結果は config の記載順で決まり、
同じプールからは常に同じ結果になります。

`family` が取る値は `iphone` と `ipad` です。どちらも小文字で、大文字と小文字を区別します。
読み取りには、`simctl list devicetypes` がデバイスタイプごとに報告する `productFamily`（`iPhone`、
`iPad`）を使います。ほかのファミリー（Apple TV、Apple Watch）のデバイスタイプは読み取れますが、
一致することはありません。

web で「どのエンジンでもよい」を書く方法は、すべてのエンジンを挙げた `anyOf` の 1 通りです。
エンジンには組み込みのデフォルト値 `chromium` があるので、省略では表せません。iOS にはデフォルトの
モデルがないので、省略がすでに「何でも」を意味します。継承された `family` は、ターゲットが
`family: null` で解除します。

### ローダーが強制する規則

1 つの `runsOn` ブロックの中の規則は、チームのデフォルト値を畳み込んだ後の config ブロックで判定します。
既存の `kind: device` の規則と同じです。シナリオの `preconditions.runsOn` は、このブロックに含まれません。
ローダーはシナリオのブロックを単独で検証し、後から平らな条件として畳み込みます。その途中で `null` は
落ちるので、シナリオはターゲットのフィールドを解除できません。そこで、層をまたぐ組には置き換えの規則を
設けます。

- シナリオの `family` はターゲットの `model` を置き換え、シナリオの `model` はターゲットの `family` を
  置き換えます。
- シナリオの `anyOf` はターゲットの `engine` を置き換え、シナリオの `engine` はターゲットの `anyOf` を
  置き換えます。

`form_target_runs` が、互いに排他な 2 つの条件を受け取ることはありません。1 つの config ブロックの中では、
次の規則が成り立ちます。

- `family` と `model` を併記すると、読み込みが失敗し、両方のフィールド名を示します。継承された側は
  ターゲットが `null` で解除します。
- `kind: device` のターゲットに `family` を書くと、現在の `model` や `os` と同じく読み込みが失敗します。
  実機にはマッチさせるプールがありません。
- `anyOf` と明示した `engine` を併記すると、読み込みが失敗します。そのため `engine` はフィールドの
  デフォルト値 `"chromium"` を失い、型は `str | list[str] | None` になります。マージは検証の前に
  `null` を取り除くので、フィールドのデフォルト値が再び効いて `anyOf` に勝ってしまうからです。
  組み込みの `chromium` は run の解決時に、`engine` と `anyOf` のどちらもないときに限り効きます。
- `anyOf` と `browser.version` の併記は、エンジンのリストと同じ理由で読み込みが失敗します。
  エンジンごとにバージョンの付け方が違うからです。
- 空のリスト、重複した項目、未知のファミリーやエンジンは、読み込みが失敗し、許される値を示します。

`family` は `os` と組み合わせられる条件のままです。バージョン範囲は従来どおり run を倍増させます。
メジャーバージョンごとの run は、そのバージョンのデバイスの中から自分のファミリーを選びます。

### run の解決方法

`bajutsu/common/devices/runs.py` の `form_target_runs` は、すでにプールのデバイス情報を受け取って
います。優先順位リストはここで run ごとに 1 回解決し、勝ったファミリーを `TargetRun` に保存します。

1. 項目の順に、そのファミリーのデバイスのうち、run の `os` 条件を満たすものを残します。
2. 該当するデバイスが 1 台以上ある最初のファミリーが勝ちます。run はそれを `family` に記録します。
3. 挙げたファミリーのどれにもデバイスがなければ、run は失敗します。理由には、挙げたファミリーと
   利用可能なデバイスを書きます。現在の `model` 欠落と同じ扱いです。「該当なし」にはしません。
   「該当なし」になれるのは、run のバージョン部分だけだからです。

`eligible_set` は、勝ったファミリーに絞ってから既存の規則を適用します。規則は、プール順で最初のモデル、
その中で台数が足りる最新のリリースです。必要台数が勝ったファミリーのデバイス数を超えても、規則は
勝者の外を探しません。その run は失敗し、ファミリー名を示します。ここでは、収まる最大の組み合わせより
決定的な選択を優先します。

2 つ以上のファミリーから 1 つを選んだ run には、座標 `family=ipad` が加わります。座標は run のラベル、
マニフェストの `run_coordinates`、flakiness のキー（デバイス OS の隣）に表示されます。座標がないと、
勝者の違うホストにまたがって同じシナリオが 1 本の履歴に入り、iPhone と iPad のレイアウト差が
flakiness に見えてしまいます
（[BE-0358](../BE-0358-device-os-as-a-first-class-fact/BE-0358-device-os-as-a-first-class-fact.md)）。
`family: ipad` の単独指定は、単独の `model` と同じく座標を加えません。

web の `anyOf` も同じ方法で、プールの読み取りが返すエンジンに対して解決します。
`bajutsu/run/device_matrix.py` の `target_pool` は、すでに `read_web` でエンジンごとに `DeviceFacts` を
1 つ読みます。この読み取りはエンジンをヘッドレスで起動し、起動できなければ `DeviceReadError` を送出します。
run を形成する前に、読み取れた最初のエンジンが勝ちます。挙げたエンジンがどれも読み取れなければ、run は
失敗し、各エンジンと読み取りエラーを示します。勝ったエンジンが後で起動に失敗した場合、その run は失敗し、
次の項目へは進みません。解決は config とプールの読み取りの関数のままで、壊れたエンジンは確実に失敗として
現れます。選ばれたエンジンは既存の `engine` に入ります。`browser.engine` の座標は、リストの場合と同じく
any-of の run にも現れます。

`run`、`record`、`crawl`、`repl` の `--browser` は、`runsOn.browser` のエンジン選択全体を置き換えます。
挙げたエンジンの `engine` をすでに解除しているのと同様に、`anyOf` も解除します。

`record`、`crawl`、`repl` は run を倍増させません。優先順位の中で最初に解決できるファミリーまたは
エンジンを使い、どれも合わなければ終了コード 2 で終わります。

### ファミリーの読み取り

`bajutsu/common/devices/facts.py` の `DeviceFacts` に `family: str | None` を加えます。`read_ios` は、
すでに名前を引くために探しているデバイスタイプの `productFamily` から読み取ります。`productFamily` の
ないデバイスタイプは `DeviceReadError` にします。読み取りは推測せず、確実に失敗する方針だからです。
Android と web のデバイスは `None` を持ちます。

### スキーマ、レポート、ドキュメント

- `bajutsu/common/config/schema/platform/ios.py` の `IosRunsOn` に `family` を加えます。
  `web.py` の `WebBrowser` には `anyOf` を加え、`engine` のフィールドのデフォルト値を外します。
  プラットフォームの registry は同じフィールドをシナリオの `preconditions.runsOn` にも渡すので、
  シナリオは上記の規則に従ってターゲットの選択を置き換えられます。
- `bajutsu config schema` は、`family` を enum またはその enum のリストとして、`anyOf` をエンジンの
  リストとして出力します。
- `doctor` は、各 run を勝ったファミリーまたはエンジンと、合致したデバイスとともに一覧します。
- [設定](../../docs/ja/configuration.md#device-conditions-runson)と英語版に、上の表を追加します。
  [CLI](../../docs/ja/cli.md)には `--browser` が `anyOf` に及ぼす効果を書きます。
  [アーキテクチャ](../../docs/ja/architecture.md)では、`common/devices/` の `facts.py` と `runs.py` の
  行を更新します。

### 原則との整合

run に LLM は入りません。解決は config とプールだけの純粋関数で、同じ入力からは同じ勝者が出ます。
曖昧な指定のとき、最初に一致したものを黙って使うことはありません。優先順位リストで順序を先に固定し、
どの項目にも合わないプールでは run が失敗します。差異はすべて config に置きます。

### 対象外

- Android のフォームファクター。タブレットかスマートフォンかを知るには、エミュレーターで
  `ro.build.characteristics` を読む必要があり、本項目では別の提案に回します。
- `runsOn.emulate`。これはプールのデバイスではなく Playwright のビューポートプロファイルを選ぶので、
  解決すべき可用性がありません。ここでのリストはプロファイルをまたぐカバレッジの話であり、
  「どれか 1 つ」とは別の問いです。
- `model` に対する glob や正規表現の照合。理由は代替案の表に書きます。
- `model`、`avd`、`engine` のリストの意味の変更。既存の config の run は変わりません。

### 作業分解

1. **ファミリーの読み取り。** `DeviceFacts` に `family` を加え、`read_ios` で `productFamily` から
   読みます。実機の `simctl list devicetypes` で、iPhone と iPad のすべての型が `productFamily` を
   持つことを確かめます。
2. **スキーマ。** `IosRunsOn.family` と `WebBrowser.anyOf` を加え、`chromium` のデフォルト値を
   `engine` フィールドから run の解決へ移し、上記の読み込み時の規則を加えます。シナリオの条件を
   畳み込む箇所には、層をまたぐ置き換えの規則を適用します。JSON Schema の出力も広げます。
3. **解決。** `form_target_runs` で優先順位リストを解決し、`eligible_set` を勝ったファミリーで絞り、
   any-of の run に座標を加えます。`first_run` と `declared_runs` も対象です。`declared_runs` には
   解決に使うプールがありません。そのため優先順位リストを、プール次第でファミリーやエンジンが決まる
   1 つの run として示します。開いた範囲を `PER_AVAILABLE_MAJOR` で表すのと同じ扱いで、挙げた項目ごとの
   run としては示しません。
4. **配線。** `_conditions` と `unpack_conditions` にフィールドを通します。
   `--browser` が `anyOf` を解除するようにし、勝ったエンジンを web のレーンへ渡します。
5. **レポート。** 勝者を run のラベル、マニフェスト、`doctor`、flakiness のキーに表示します。
6. **ドキュメントとデモ。** 上記の日英ドキュメントを更新し、デモ config の 1 つを、正確なモデル名から
   `family` に切り替えて実例にします。

各ユニットが自分のテストを持ちます。スキーマの規則、解決の分岐ごとの偽プール、失敗メッセージです。

## 検討した代替案

| 代替案 | 退けた理由 |
|---|---|
| `model` のリストを any-of の意味にする | 既存の複数モデル config がすべて、黙って「各々を実行」から「1 つを実行」に変わります。チームが頼っているマトリックスを壊します。 |
| `model` に `{ anyOf: [...] }` を許す | フィールドが文字列、リスト、オブジェクトの 3 形になり、JSON Schema とエラーメッセージも膨らみます。`family` を別フィールドにすれば、各形の役割が 1 つで済みます。 |
| `model: "iPad*"` のような glob 照合 | Apple の命名に依存します。本項目が取り除きたい壊れやすさそのものです。ファミリー接頭辞の改名でも、正確な名前が壊れるのと同じように壊れます。 |
| `idiom: phone \| tablet` によるプラットフォーム横断の指定 | 全プラットフォームでフォームファクターの読み取りが要り、Android と web のエミュレーションにも及びます。本項目の iOS と web の要望は、これなしで満たせます。後続の項目で上に載せられます。 |
| すべてのエンジンを表す短縮形 `engine: any` | 同じ指定に 2 つ目の書き方ができ、読み手は両方を覚える必要があります。`anyOf` に 3 つのエンジンを挙げても 1 行で済み、優先順位も見えたままです。 |
| 同順位の決定にプール順を使う | 単純ですが、勝者がホストのデバイス順に左右されます。flakiness の履歴が、異なるデバイスにまたがって 1 本のシナリオを束ねてしまいます。 |
| 勝者のデバイスが必要台数に足りないとき、低順位のファミリーを選ぶ | 収まる最大の組み合わせにはなりますが、ファミリーが `--workers` とデバイス台数に左右されます。並列度で run の素性が変わってしまいます。 |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] ユニット 1: ファミリーの読み取り
- [ ] ユニット 2: スキーマ
- [ ] ユニット 3: 解決
- [ ] ユニット 4: 配線
- [ ] ユニット 5: レポート
- [ ] ユニット 6: ドキュメントとデモ

## 参考

- [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md)：本項目が拡張する `runsOn` の条件、デバイスの照合、run。
- [BE-0358](../BE-0358-device-os-as-a-first-class-fact/BE-0358-device-os-as-a-first-class-fact.md)：flakiness のキーが読むデバイス情報。
- [BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines.md)：本項目が優先順位で選べるようにする web のエンジン。
- `bajutsu/common/devices/facts.py` と `bajutsu/common/devices/runs.py`：ファミリーを読み取り、解決する場所。

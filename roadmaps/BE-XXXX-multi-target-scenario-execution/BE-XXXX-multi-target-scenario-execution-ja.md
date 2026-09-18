[English](BE-XXXX-multi-target-scenario-execution.md) · **日本語**

# BE-XXXX — 複数ターゲットを1シナリオ内で行き来する実行モデル

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-multi-target-scenario-execution-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Scenario authoring features |
| 関連 | [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)、[BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks-ja.md)、[BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md)、[BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation-ja.md) |
<!-- /BE-METADATA -->

## はじめに

シナリオに新しいフィールドを2つ追加します。トップレベルの`targets`と、ステップごとの`target`です。
`targets`は、そのシナリオが操作する[ターゲット](../../docs/ja/glossary.md#target-app-device)を列挙します。
`target`は、各ステップがどのターゲットで実行されるかを指定します。両方を設定すると、1つのシナリオ
ファイルの中で、あるターゲットを操作し、別のターゲットで結果を確認できます。行き来は自由です。
具体的には、iOSターゲットで「いいね」をタップし、Webターゲットでカウントが更新されたことを確認
します。続けてWebターゲットでコメントを編集し、iOSターゲットに届いたことを確認します。この一連の
流れを、決定的な[`bajutsu run`](../../docs/cli.md)の1回の呼び出しとして、1つの合否判定で実行でき
ます。`targets`に列挙したターゲットは、最初のステップの前にまとめて起動し、最後のステップの後に
まとめて後片付けします。
`target`を省略したステップは、今までと同じ挙動をします。既存の単一ターゲットのシナリオは、変更が要り
ません。

このアイテムは`bajutsu run`だけを対象とします。`bajutsu crawl`、`bajutsu record`、`serve`自身の
ディスパッチUIも、今日は`run`と同じ方法で単一のターゲットを解決しています。それぞれに追従作業が
必要であり、今回のスコープには含めません。

## 動機

`bajutsu run --target <name>`は、ターゲットをちょうど1つだけ受け取ります
([`bajutsu/run/cli.py:1293`](../../bajutsu/run/cli.py))。ランナー側もこの制約に揃っています。
`run_scenario`は`driver: base.Driver`をちょうど1つだけ受け取り
([`bajutsu/common/orchestrator/loop/_functions.py:572-574`](../../bajutsu/common/orchestrator/loop/_functions.py))、
パイプラインもシナリオ実行1回につきドライバを1つだけ起動します
([`bajutsu/common/runner/pool.py:408`](../../bajutsu/common/runner/pool.py)、
[`launch_driver`](../../bajutsu/common/runner/launch.py)経由)。パイプラインの`_ScenarioRunner`も、
型のレベルで同じ制約を映しています。実行の中のすべてのシナリオで読み取り専用のまま共有される、1つの
`eff: Effective`フィールドを持ちます
([`bajutsu/common/runner/pipeline.py:152-165`](../../bajutsu/common/runner/pipeline.py))。2つの
異なるプラットフォームに触れる必要があるシナリオは、1回の実行では実現できません。

[BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation-ja.md)は、この
制約が生む回避策をすでに書いています。「両方の顔が必要なチームは、シナリオを二つのターゲットのもとで
実行します。」というものです。この回避策には、実行回数が増える以上のコストがあります。2回の別々の実行は、
2つの別々の`RunResult`と2つの別々のレポートを生みます。どちらも壊れていないことを、レビュー担当者が
両方開いて確認しなければならず、1つの合否判定にはなりません。2回の実行は状態も共有しません。
`${vars.*}`は`extract`修飾子
([`bajutsu/common/scenario/models/steps/step.py:117`](../../bajutsu/common/scenario/models/steps/step.py))
が書き込み、`live_bindings`
([`bajutsu/common/orchestrator/loop/_functions.py:697`](../../bajutsu/common/orchestrator/loop/_functions.py))
が保持する値ですが、これは1回の`run_scenario`呼び出しの中でしか生きません。一方のターゲットの
ステップが捕まえた値、例えば「いいね」操作が作った識別子は、もう一方のターゲットに対するアサーション
には一切届きません。アプリ側の操作がWeb側に正しい効果を生むこと、あるいはその逆を確認したいチームは、
どのレコードが変化したかを名指しできません。何らかのカウントが変化したことしか確認できず、それが
正しいレコードだという保証がありません。

操作したターゲット自身が正しいエンドポイントを呼んだことだけを確認したいチームには、すでに軽量な
手段があります。操作対象自身の`request`アサーションです。ただしこれは別の問いに答えるものです。
操作対象が正しい呼び出しを送ったことは確認できますが、もう一方のプラットフォームのクライアントが
それを受け取り正しく描画したかどうかこそ、「アプリとWebの両方を確認したい」チームが確認したいことです。

実現後にレビュー担当者が指させる具体的な違いは2つです。1つ目は、iOSターゲットとWebターゲットを
名指しした`targets`を持つシナリオファイルが、1回の`bajutsu run`呼び出しとして実行され、1つの合否
判定を生むことです。今までは、2つの独立した実行を手作業で突き合わせる必要がありました。2つ目は、
アプリ側のステップが`extract`で捕まえた値が、同じ実行内でWeb側のターゲットに対するアサーションから
`${vars.*}`として読めるようになることです。これは、前段落が指摘した、値が実行をまたげないという
欠落を埋めます。

## 詳細設計

### 参加するターゲットを宣言する：`targets`とステップごとの`target`

```yaml
name: liking a post on the app shows up on the web, and a web comment reaches the app
targets: [showcase-app, showcase-web]
steps:
  - target: showcase-app
    tap: { id: post.like }
    extract: { var: postId, sel: { id: post.id } }

  - target: showcase-web
    wait: { for: { id: "post.${vars.postId}.likeCount" }, timeout: 10 }
    assert:
      - value: { sel: { id: "post.${vars.postId}.likeCount" }, equals: "1" }

  - target: showcase-web
    tap: { id: "post.${vars.postId}.comment.input" }
    type: { text: "nice!" }

  - target: showcase-app
    wait: { for: { id: "post.${vars.postId}.comment.latest" }, timeout: 10 }
    assert:
      - value: { sel: { id: "post.${vars.postId}.comment.latest" }, equals: "nice!" }
```

`Scenario`は`targets: list[str] = Field(default_factory=list)`を新たに持ちます。既存の`before`、
`steps`、`after`と並ぶ位置です
([`bajutsu/common/scenario/models/scenario/scenario.py:45-84`](../../bajutsu/common/scenario/models/scenario/scenario.py))。
各要素は`targets.<name>`という設定単位を指します。これは、`--target`が今日解決しているのと同じ
[ターゲット](../../docs/ja/glossary.md#target-app-device)です。宣言された名前は、読み込んだ設定に
すでに存在していなければなりません。チェック方法は`--target`と同じです。

`Step`は`target: str | None = None`を新たに持ちます。既存の直交した修飾子群に加わる形です。修飾子群
は現在`("capture", "extract", "name", "from_")`です
([`bajutsu/common/scenario/models/steps/_shared.py:8`](../../bajutsu/common/scenario/models/steps/_shared.py))。
これらは`Step`の「アクションはちょうど1つ」という規則から除外されています
([`bajutsu/common/scenario/models/steps/step.py:136-139`](../../bajutsu/common/scenario/models/steps/step.py))。
`target`も同じ扱いが必要です。新しいバリデータは、`Step`自身の`model_validator`の中ではなく
`Scenario`のレベルでチェックします。`_exactly_one`
([`bajutsu/common/scenario/models/_base.py:40`](../../bajutsu/common/scenario/models/_base.py))は
`Step`自身から呼ばれ、外側の`Scenario`にアクセスできないため、この新しい規則には専用のバリデータが
必要です。このバリデータは、新しいフィールドの意味を`scenario.targets`に対して強制します。`targets`
が0個か1個なら、`target`は省略するか、その1つの名前と一致していなければなりません。既存の単一
ターゲットのシナリオはこれで変更不要になり、今までどおりに動きます。`targets`が2個以上なら、すべての
アクションステップが`target`を明示しなければなりません。「最初に宣言したターゲット」への暗黙の
フォールバックを用意しないのは、2つのプラットフォームを行き来するシナリオで、ステップの宛先を読者が
覚えておかねばならない暗黙のルールを残さないためです。このバリデータは`steps`、`before`、`after`の
各ルールの`steps`を再帰的に辿り、`if`と`forEach`が持つネストしたステップリストの中にも入ります。
`forEach`の本体の中の3階層下のステップも、トップレベルのステップと同じく`target`の明示が必要です。
`web`ブロックの中にネストしたステップだけは例外です。`target`を自分では一切持ちません(指定すると
読み込み時にエラーになります)。そのステップは常に、囲む`web`ステップ自身が解決済みのターゲットに
対して開いた`WebContextDriver`ブリッジに対して実行されるからです。これは今日、複数のターゲットが
存在することを知らずに動いているのと同じ形です。

`if`、`forEach`、`web`は事情が違います。`_CONTROL_FLOW_ACTIONS`
([`bajutsu/common/scenario/models/_base.py:33`](../../bajutsu/common/scenario/models/_base.py))は
この3つを指しており、`_no_modifiers_on_control_flow`
([`bajutsu/common/scenario/models/steps/step.py:141-149`](../../bajutsu/common/scenario/models/steps/step.py))
はすでにこの3つを`capture`と`extract`から除外しています。それぞれがネストしたステップリストを包む
だけで、自分自身は画面を操作しないからです(`name`はこの3つでも使えます)。`web`が`target`を持つ理由は
ほかのアクションステップと同じです。そのブロック自身のWebViewブリッジがどのターゲットのドライバに
対して開くかを指定するためです。`if`と`forEach`が`target`を持つ理由は別です。条件そのものを評価する
には、いずれかのターゲットの要素ツリーへの問い合わせが必要になります。`ForEach`自身が`sel: Selector`
フィールドを持つこと
([`bajutsu/common/scenario/models/steps/for_each.py:19`](../../bajutsu/common/scenario/models/steps/for_each.py))
がこれを裏付けます。`if`と`forEach`は自分自身の`target`を、内側の各ステップが自分自身に設定する
`target`とは独立に持ちます。

### CLI（コマンドラインインターフェース）：シナリオが自己宣言していれば`--target`は省略できる

`--target`は、シナリオ自身の`targets`が空であれば、今までどおり必須のままです
([`bajutsu/run/cli.py:1293`](../../bajutsu/run/cli.py))。シナリオの`targets`が空でなければ、その
シナリオは自己宣言しています。`bajutsu run --scenario <file>`(繰り返し指定可能で、`--scenario`は
常に個別のファイルを指し、ディレクトリは指しません
[`bajutsu/run/cli.py:135-146`](../../bajutsu/run/cli.py))は、`--target`なしで、読み込んだ設定から
すべての名指しされたターゲットを解決します。`--target`を省略するときは`--scenario`が最低1つ必要
です。自己宣言したシナリオには、頼れる`targets.<name>.scenarios`ディレクトリ
([`bajutsu/run/cli.py:149-159`](../../bajutsu/run/cli.py))がないためです。`bajutsu run --target
<name>`だけで得られるこのディレクトリの一括読み込みは1つのターゲットに属するものなので、複数の
ターゲットを名指しするシナリオは、常に自分のファイルを明示的に指定します。そのようなシナリオと
一緒に明示的な`--target`を渡した場合は、無視されるのではなく`scenario.targets`への所属で照合され
ます。宣言したターゲットのどれか1つと一致すれば一致、それ以外は不一致です。一致しなければ実行を
拒みます。シナリオを編集した後に取り残された古いフラグが、ファイルがもう期待していないターゲットを
黙って選んでしまう事態を防ぐためです。

このディレクトリの一括読み込みは`run`が持つ唯一のスイート全体向けの簡便な手段であり、1つのターゲット
に属します。したがって、自己宣言したシナリオファイルは、どのターゲット自身の`scenarios`ディレクトリ
の中にも置いてはいけません。`--scenario`を渡さない普通の`bajutsu run --target <name>`は、自己宣言
したファイルも含めてそのディレクトリのすべてを一括で読み込みますが、どちらの結果も望ましくありません。
`name`がそのファイル自身の宣言したターゲットのどれでもなければ、前述の不一致チェックが拒否し、
同じバッチの中のほかのすべてのシナリオまで道連れに失敗します。`name`がたまたま宣言したターゲットの
1つであれば、所属チェックは通過しますが、そのバッチはその1つのターゲットだけでなく、ファイルが
宣言するほかのすべてのターゲットまで黙って起動してしまいます。これは著者がディレクトリの配置で守る
運用上の規律であり、このアイテムが加える新しい実行時チェックではありません。自己宣言したシナリオは
専用のディレクトリに置き、明示的な`--scenario`ファイル指定だけで実行してください。

このアイテムは、Webバックエンドのクロスエンジンマトリクス(`--browsers`、
[`bajutsu/run/cli.py:264-307`](../../bajutsu/run/cli.py))や`--headed`/`--browser`には手を付けません。
これらは今までどおり、実行が持つ唯一のWebプラットフォームターゲットに対して適用され続けます。
複数のWebプラットフォームターゲットを名指しする自己宣言シナリオでは、どちらを指しているか推測する
のではなく、これらのフラグをそのまま拒否します。マトリクスの軸を複数ターゲットの実行にまで広げる
のは、このアイテムが扱わない追従作業です。

### 宣言したターゲットをまとめて起動し、まとめて後片付けする

1つのターゲット名を1つの`Effective`設定に解決する処理は、すでに名前の付いた手順であり、単なる
`resolve(config, target)`が`config.targets[target]`を参照するだけの処理
([`bajutsu/common/config/resolve.py:138-160`](../../bajutsu/common/config/resolve.py))以上のことを
します。そこに到達する呼び出し元`_load_effective_with_source`
([`bajutsu/cli/_shared.py:217-285`](../../bajutsu/cli/_shared.py))は、最後に結果をリベースします。
ローカル設定なら`eff.rebased(cfg_path.resolve().parent, confine=False)`、それ以外は
`eff.rebased(root)`です
([`bajutsu/cli/_shared.py:279-285`](../../bajutsu/cli/_shared.py))。これにより`app_path`/
`scenarios`/`baselines`/`schemas`/`goldens`は、呼び出し元の作業ディレクトリではなく設定ファイル
自身のディレクトリを基準に解決されます
([`bajutsu/common/config/effective/effective.py:98-108`](../../bajutsu/common/config/effective/effective.py)、
BE-0242)。2つ目に宣言したターゲットを単なる参照だけで解決すると、その相対パスは誤ったディレクトリ
を基準に静かに解決されてしまいます。そこでこのアイテムのターゲット名ごとの解決は、`_load_effective_with_source`
の経路をまるごと再利用します。CLIの`_resolve_config_and_engines`
([`bajutsu/run/cli.py:264-307`](../../bajutsu/run/cli.py))も、単一の`--target`に対してすでにこの
経路を呼んでいます。これを`scenario.targets`の名前ごとに1回呼び、単なる`resolve()`だけは呼びません。

パイプラインの`_ScenarioRunner`は、今日は1つの`eff: Effective`フィールドを持ちます。この
オブジェクトは実行につき1回だけ作られ、すべてのシナリオと`ThreadPoolExecutor`のすべてのワーカーの
間で、読み取り専用のまま共有されます。自分自身のdocstringも、まさにこの理由で「シナリオごとの
可変状態を持たない」と述べています
([`bajutsu/common/runner/pipeline.py:152-165`](../../bajutsu/common/runner/pipeline.py))。同じ実行
の中の2つのシナリオが、異なる`targets`を宣言することもあります。したがって、ターゲットごとの
すでにリベース済みの`Effective`のマップは、今日の単一の`eff`と同じようにはこの共有オブジェクトに
置けません。これは`_run_one_impl`
([`bajutsu/common/runner/pipeline.py:329-331`](../../bajutsu/common/runner/pipeline.py))の中の
ローカル変数のままにします。このメソッドは、同じdocstringが述べているとおり、シナリオごとの状態を
すでにすべてローカルに保つ場所だからです。代わりに`_ScenarioRunner`は、上の解決の経路が参照する
読み込み済みの`Config`を保持する、新しい読み取り専用フィールドを1つ持ちます。`run`のCLI自身は
今日この`Config`を保持していません。`_load_effective_with_source`は、単一の`--target`を解決した
あとにこれを捨て、`(Effective, source, checkout_root)`だけを返します
([`bajutsu/cli/_shared.py:263`](../../bajutsu/cli/_shared.py))。`_resolve_config_and_engines`
も、その`Effective`だけを先へ渡します
([`bajutsu/run/cli.py:264-307`](../../bajutsu/run/cli.py))。両方とも、自分自身の戻り値に読み込み
済みの`Config`を加え、そこから`run`のCLIを経由して`run_and_report`
([`bajutsu/common/runner/pipeline.py:1176-1178`](../../bajutsu/common/runner/pipeline.py))と
`run_all`
([`bajutsu/common/runner/pipeline.py:986-987`](../../bajutsu/common/runner/pipeline.py))を経由して
配線します。どちらも今日は、CLIの単一の`eff: Effective`しか受け取っていません。これにより
`_run_one_impl`は、この経路を`scenario.targets`の名前ごとに1回呼び、自分のローカルな
`dict[str, Effective]`を組み立てられます。

宣言した各ターゲットのドライバを立ち上げるには、共有された1つのプールではなく、それぞれ専用の
プールが要ります。プールは今日プラットフォーム固有だからです。`make_pool`は、1つのプラットフォーム
のudid一覧から1組の`pool_actuator`/`pool_env`だけを解決し、そのプラットフォーム自身のコレクタを
あらかじめ起動します
([`bajutsu/common/runner/pool.py:149-182`](../../bajutsu/common/runner/pool.py))。このアイテムの
見出しの例であるiOSターゲットとWebターゲットには、1つのプールが両方を扱うのではなく、別々に構築
された2つのプールが必要です。`run_all`は、今日構築している単一のプールの代わりに、シナリオ集合
全体で宣言されたターゲットが指す、それぞれ異なるプラットフォームごとに1つのプールを構築します。
これも今までどおり実行につき1回だけです。`_run_one_impl`は、自分のシナリオが宣言する各ターゲット
に合ったプールからリースを取得します。そして、解決した`Effective`ごとに、既存の`launch_driver`
([`bajutsu/common/runner/launch.py:27-110`](../../bajutsu/common/runner/launch.py))を通じて
ドライバを1つ起動します。プールが今日行っている、シナリオ1回につき1つのリースと起動
([`bajutsu/common/runner/pool.py:408`](../../bajutsu/common/runner/pool.py))を、この形に置き換え
ます。起動結果はターゲット名をキーとするローカルな`dict[str, base.Driver]`にまとめます。すべて
最初のステップが走る前に済ませます。ターゲットを名指しするどのステップも、そのターゲットがすでに
起動済みであることを期待します。初めて参照された時点での遅延起動は行いません。これは、このアイテム
が支えようとしている行き来を成立させるためです。今日`_run_one_impl`が単一の`self.eff`から導いて
いるものがいくつかあります。`select_actuator_for_scenario`が選ぶアクチュエータ
([`bajutsu/common/runner/pool.py:260`](../../bajutsu/common/runner/pool.py))、ロケール、`capture`
の基準値、`run_defaults.interrupts`とシナリオ自身の`s.interrupts`の組み合わせ
([`bajutsu/common/runner/pipeline.py:913`](../../bajutsu/common/runner/pipeline.py))、そして
`target_launch_env`として渡すターゲット自身の`launchEnv`です
([`bajutsu/common/runner/pipeline.py:928-932`](../../bajutsu/common/runner/pipeline.py))。これら
のどれもが、同じ方法で宣言したターゲットごとの`Effective`から導かれ、そのドライバと並べてキーに
なります。今日1つのリースから`_run_on_lease`が渡している、あと5つの引数も同様です。`sink`、
`relaunch`、`control`、`webview_bridge`、`transitions`
([`bajutsu/common/runner/pipeline.py:890-907`](../../bajutsu/common/runner/pipeline.py))は、それぞれ
自分が来たリース1つに紐づいているからです。2つ目に宣言したターゲットに対する`web:`ステップは、
1つ目のターゲットのものではなく、そのターゲット自身の`webview_bridge`を必要とします。そうでなければ、
誤ったアプリのWebViewを黙って開いてしまいます。アラートガードも同様です。今日は`self.alert_guard_for(s)`
によってシナリオごとに1回解決されます
([`bajutsu/common/runner/pipeline.py:441`](../../bajutsu/common/runner/pipeline.py))。ネットワーク
コレクタも同様です。すでに起動済みのドライバごとに、そのリースから組み立てられています
([`bajutsu/common/runner/pool.py:380-428`](../../bajutsu/common/runner/pool.py))。
`ctx=EvalContext(..., golden=gc_with_screen)`
([`bajutsu/common/runner/pipeline.py:875-902`](../../bajutsu/common/runner/pipeline.py))も、別の
理由で同じ一般化が必要です。`self.baselines_dir`/`self.schemas_dir`/`self.golden_context`は、
どれも単一の`self.eff`から導かれており、それぞれが指すディレクトリは、このアイテムのターゲット
ごとの解決がすでに運んでいるパスと同じく、`Effective.rebased`がすでにターゲットごとに解決して
います。したがって、2つ目に宣言したターゲットに対する`visual`や`golden`アサーションは、主
ターゲットのものではなく、そのターゲット自身のbaselineディレクトリとgoldensディレクトリと比較
しなければなりません。これらのターゲットごとの値もすべて同じ方法で導かれ、そのドライバと並べて
キーになります。`_ScenarioRunner`のうち3つのフィールドは、どれか
1つのターゲットの性質ではないため、ターゲットごとに分けずランレベルのままにします。`redactor`は、
どのターゲットの証跡にredactionを適用する場合にも、宣言した各ターゲット自身の`secrets`の和集合を
使います。
あるターゲットの設定が秘密として指定した値は、そのターゲット自身の証跡だけでなく、どこからでも
取り除かれます。`mailbox`は、どのターゲットの操作が要求したメッセージであるかにかかわらず、
`email`/`totp`ステップが読む受信箱を指します。`caps`は、実行全体がどの操作を行えるかを制御する、
ターゲットごとではなく実行全体のポリシーです。

宣言したターゲットごとに1つのリースを取得するには、このアイテムが明示的に加えるルールがもう1つ
必要です。`pool.lease()`は、実行のudidを入れたキューに対して`free.get()`でブロックします
([`bajutsu/common/runner/pool.py:163-165, 255`](../../bajutsu/common/runner/pool.py))。したがって、
デバイスに紐づくN個のターゲットを、前段落のとおり最大N個の異なるプラットフォームごとのプールから
リースするシナリオが、`--workers`によって複数同時に走っていると、飢餓状態やデッドロックになり得ます。
どのワーカーも1つのプールから1台のデバイスを保持したまま、別のプールから次の1台を取得しようとして
永遠にブロックしてしまいます。このアイテムの起動処理は、1つずつ順に`pool.lease()`を呼ぶのではあり
ません。シナリオが必要とするデバイス紐づきのリース一式を、必要とするすべてのプールにまたがって、
どれも起動する前に1回でまとめて確保します。これにより、一式を揃えられないシナリオはその場で待機
するだけになり、一部だけを保持したまま隣のワーカーを飢餓状態やデッドロックにすることがなくなります。

後片付けは、今日1回の起動が1つのシナリオを包んでいるのと同じ形で、宣言した集合全体を包みます。
途中で起動が失敗した場合は、その時点までに起動できていたドライバをすべて後片付けしてから、失敗を
伝えます。これは、`env.start`の後に準備確認が失敗した場合の`launch_driver`自身のガード
([`bajutsu/common/runner/launch.py:100-108`](../../bajutsu/common/runner/launch.py))と同じ考え方
です。実行の終わりには、`_run_on_lease`の`finally: lz.release()`
([`bajutsu/common/runner/pipeline.py:955-956`](../../bajutsu/common/runner/pipeline.py))が今日1つ
のリースを後片付けしているのと同じ方法で、宣言したターゲットのリースごとにこの解放を呼びます。

### ステップをそのドライバへ振り分け、`${vars.*}`を全ターゲットで共有する

`run_scenario`の既存のフラットなキーワードパラメータ、`driver`、`sink`、`alert_guard`、`network`、
`relaunch`、`control`、`ctx`、`webview_bridge`、`transitions`、`interrupts`、`locale`、`capture`、
`channel`、`target_launch_env`
([`bajutsu/common/orchestrator/loop/_functions.py:572-594`](../../bajutsu/common/orchestrator/loop/_functions.py))
は、まとめて見ると、今日パイプラインが1つのターゲットの1つのリースから束ねているものすべてです
（前節の一覧のとおりです）。`target`を持たないステップは、今までどおりこれらすべてを読み続けます。
これらは`run_scenario`の主ターゲットのランタイムのままで、形は変わらないため、既存の呼び出し元は
影響を受けません。`run_scenario`は、これらの各フィールドをターゲットごとのマッピングへその場で
広げる代わりに、それ以外の宣言済みターゲットごとに1つずつ、新しいパラメータを1つ持ちます。
`target_runtimes: Mapping[str, TargetRuntime] | None = None`です。`TargetRuntime`(新しい小さな
データクラス)は、まさに同じフィールドの並びを束ねます。追加で宣言したターゲット1つにつき1
インスタンスであり、前節ですでにドライバのマップを組み立てているのと同じ方法で`_run_one_impl`が
組み立てます。`_StepRunner`の各メソッドは、すでに`active_driver: base.Driver`という明示的な引数を、
共有された状態から読む代わりに、呼び出しのたびに受け取っています
([`bajutsu/common/orchestrator/loop/_step_runner.py:78-118`](../../bajutsu/common/orchestrator/loop/_step_runner.py))。
あるステップの種類、`web:`ブロックは、すでにこの方法で自分のネストしたステップ用に別のドライバへ
切り替えています。`WebContextDriver`を作り、`exec_steps(step.web.steps, web_driver)`へ再帰し、その
後で外側の`active_driver`に制御を戻します
([`bajutsu/common/orchestrator/loop/_step_runner.py:242-293`](../../bajutsu/common/orchestrator/loop/_step_runner.py))。
この切り替えは、すでに起動済みの1つのアプリの、プロセス内WebViewブリッジに閉じたものであり、独立
して起動した2つ目のターゲットではありません。しかし、ループの基本部品が、1つの共有オブジェクトから
読む代わりに、すでにターゲットごとの状態を呼び出しのたびに明示的に渡していることは裏付けられます。
これはまさに、`target`をキーにした参照が必要とする形です。`_run_one`
([`bajutsu/common/orchestrator/loop/_step_runner.py:89`](../../bajutsu/common/orchestrator/loop/_step_runner.py))
は、あるステップを支配する`TargetRuntime`(主ターゲットのものか、`target_runtimes`の1つ)を、
`step.target`を調べて決めます。そのステップのドライバは、今日と同じく`active_driver`を通じて
振り分けられます。主ターゲット自身の`driver`パラメータにではありません。`web:`ブロックの中に
ネストしたステップにとって、この違いが重要です。そのステップの`active_driver`はすでにブロック
自身の`WebContextDriver`になっているため、それを飛び越えて主ターゲットの`driver`まで戻って
しまうと、誤ったアプリの画面に対してステップを実行してしまいます。トップレベルの各ステップの
`active_driver`は最初、自分が解決した`TargetRuntime.driver`そのものなので、これは今日の唯一の
ケースに対する挙動を変えるものではなく、その厳密な一般化です。

ターゲットをまたいで`${vars.*}`を共有するのに、新しい配線は要りません。`live_bindings`は、すでに
1つのプレーンな`dict[str, str]`であり、`run_scenario`の呼び出しごとに1回だけ作られ、`before`、
`steps`、`after`のどの段階からもクロージャとして共有されています
([`bajutsu/common/orchestrator/loop/_functions.py:695-697`](../../bajutsu/common/orchestrator/loop/_functions.py))。
特定のドライバに紐づいているわけではありません。あるターゲットに対する`extract`ステップは、別の
ターゲットに対するアサーションがすでに読んでいるのと同じ辞書に書き込みます。この仕組みは単一の
ドライバを前提にしていません。前提にしているのは、1回の`run_scenario`呼び出しがこの辞書を1回だけ
組み立てることであり、それは変わりません。

### レポート：どのターゲットが各ステップを生んだかを記録する

`StepOutcome`は、既存の`action: str`
([`bajutsu/common/orchestrator/types/step_outcome.py:15-44`](../../bajutsu/common/orchestrator/types/step_outcome.py))
に加えて`target: str = ""`を持ちます。シナリオ自身の`targets`が空なら空文字列とします。ステップが
`target`を明示していればその名前を設定します。`scenario.targets`がちょうど1個で、ステップが
（前述のバリデータのとおり）`target`を省略している場合は、その1個の宣言名を設定します。自己宣言した
シナリオが、唯一のターゲットに曖昧さがないというだけの理由で、レポートで空白になることはありません。
これは、`RunResult.engine`がすでに単一エンジンの実行に対して使っている、「空は該当なしを意味する」
という慣習と同じです
([`bajutsu/common/orchestrator/types/run_result.py:26-30`](../../bajutsu/common/orchestrator/types/run_result.py))。

`RunResult`自身の`backend`、`device`、`device_name`、`device_runtime`
([`bajutsu/common/orchestrator/types/run_result.py:25-40`](../../bajutsu/common/orchestrator/types/run_result.py))
は、それぞれちょうど1つのターゲットを表します。単一ターゲットの実行に対しては、今までどおり意味を
持ちます。複数ターゲットの実行では、この同じ情報がシナリオ全体で1回ではなく、宣言したターゲットの
数だけ必要です。そこで`RunResult`は`target_devices: dict[str, TargetDeviceInfo]`も持ちます。
`TargetDeviceInfo`は`backend`/`engine`/`device`/`device_name`/`device_runtime`を運ぶ新しい小さな
データクラスであり、ターゲット名をキーにします。既存の単数形フィールドは、複数ターゲットの実行では、
どれか1つのターゲットの値を他に優先して報告するのではなく、空のままにします。これは、このアイテムが
直前で`StepOutcome.target`に適用したのと同じ「空は該当なしを意味する」慣習を、`RunResult`自身に
ついても反転させずに一貫させるものです。今日の`RunResult`の形に合わせて書かれた既存の読み手、JUnitや
Common Test Report Format（CTRF）のエクスポートを含め、単一ターゲットの場合は今までどおりの値を、
複数ターゲットの実行では空の値を見ます。ある1つのターゲットの値をシナリオ全体を代表するかのように
示すことはありません。レポートのStepsビューは、各ステップのアクションの隣にターゲット名を表示します。
新しいヘッダーブロックが、宣言した各ターゲットのデバイスを、それぞれの証跡と並べて一覧します。
ターゲットを何も宣言しないシナリオでは、既存の単一デバイスのヘッダーがそのまま残ります。

### この提案が実装に残す2つの未決事項

共有されたラン全体の状態のうち2つは、今日は単一の`Effective`から解決されていますが、この提案では
まだ解決していません。それぞれ、ここで推測するのではなく、上の作業が始まった段階で改めて判断が
必要です。`_ScenarioRunner`の`run_dir`、`udid_spec`、`actuator`、`resolve_actuator`、
`baselines_dir`、`schemas_dir`、`golden_context`の各フィールドは、今日はどれもCLIの単一の`eff:
Effective`から導かれています。`run_all`と`run_and_report`は、このアイテムが`--target`自体を省略
可能にした後も`eff`を必須パラメータのままにしています。したがって、実行に単一の`--target`がなく
なった場合に、これらをどのターゲットの`Effective`から導くべきかは未決の問いです。もう1つ別に、
`gc_with_screen`
([`bajutsu/common/runner/pipeline.py:875-884`](../../bajutsu/common/runner/pipeline.py))は、
`golden`のフレーム健全性チェック(BE-0006)のために1つのドライバの画面サイズを調べます。iOSターゲット
とWebターゲットは一般に画面のジオメトリが異なり、今日は1つの`GoldenContext`しか`run_scenario`に
届きません。したがって、2つ目に宣言したターゲットに対する`golden`アサーションの挙動も同様に未決
です。この提案が見出した形は、ターゲットごとの`GoldenContext`を持つか、`golden`を1つの宣言した
ターゲットに限定するかの2つであり、どちらを選ぶかは推測せずに先送りします。

### 作業分解(MECE)

1. **スキーマ**：`Scenario.targets: list[str]`、`Step.target: str | None`、`target`の要否を
   `len(scenario.targets)`に紐づける`Scenario`レベルのバリデータ（`Step`自身のバリデータは外側の
   シナリオを見られないため不可）。`steps`、`before`、`after`の各ルールの`steps`を、`if`/`forEach`/
   `web`のネストしたステップリストにも再帰的に適用します。
2. **CLI**：`scenario.targets`が空でなければ`--target`を省略可能にし、代わりに`--scenario`を必須に
   します。明示的な`--target`は黙って上書きせず`scenario.targets`と照合します。シナリオファイルの
   ローダーは、宣言された各名前を、実行が始まる前に読み込んだ設定に対して検証します。
3. **起動と後片付け**：`_load_effective_with_source`と`_resolve_config_and_engines`が、すでに返して
   いる`Effective`と並べて、読み込んだ`Config`も返すようにします。それを`run`のCLIから
   `run_and_report`と`run_all`を経由して、`_ScenarioRunner`の新しい読み取り専用フィールドまで配線
   します（どちらも今日は受け取っていません）。`run_all`は、今日の単一プールの代わりに、宣言された
   ターゲットが指すプラットフォームごとに1つの
   プールを構築します。`_run_one_impl`は、宣言したターゲット名ごとに1つの、すでにリベース済みの
   `Effective`をローカルなマップへ解決します（共有される`_ScenarioRunner`には保存しません）。
   ターゲットごとに`pool.lease()`を1つずつ呼ぶのではなく、必要とするすべてのプールにまたがる
   シナリオ1回につき1回のアトミックな複数リース確保にします。これにより一部だけの取得が隣の
   ワーカーを飢餓状態やデッドロックにすることを防ぎます。名前ごとに1回`launch_driver`を呼び出し、
   ローカルな`dict[str, base.Driver]`にまとめます。すべて最初のステップより前に済ませます。途中の
   起動失敗は、その時点までに起動できたドライバをすべて後片付けします。実行の終わりは集合全体を
   後片付けします。
4. **ランナー**：`run_scenario`が今日1つのリースから束ねているすべての引数（`driver`、`sink`、
   `alert_guard`、`network`、`relaunch`、`control`、`ctx`、`webview_bridge`、`transitions`、
   `interrupts`、`locale`、`capture`、`channel`、`target_launch_env`）を束ねる新しい`TargetRuntime`
   データクラス。主ターゲット向けの変更しないフラットなパラメータと並ぶ、`run_scenario`の新しい
   `target_runtimes: Mapping[str, TargetRuntime] | None`パラメータ。`step.target`を主ターゲットの
   値ではなく`active_driver`/`target_runtimes`に対して解決する、`_run_one`のステップ振り分け。
   宣言したターゲットごとに1つ`TargetRuntime`を組み立てる`_run_one_impl`は、今日1組の
   リースごとの引数がシナリオごとに1回組み立てられているのと同じ方法です。
5. **レポート**：`StepOutcome.target`（宣言されたターゲットが1個の場合の既定値を含む）、複数
   ターゲットの実行で空のままになる`RunResult`の単数形フィールド、`RunResult.target_devices`、
   Stepsビューのターゲットラベル、宣言した各ターゲットのデバイスを一覧するヘッダーブロック。
6. **ドキュメント**：`docs/scenarios.md`(`targets`/`target`のリファレンスと実例)、`docs/cli.md`
   (`--target`の新しい省略条件と`--scenario`の新しい必須条件)、`docs/run-loop.md`(複数ドライバの
   ステップ振り分け)、それぞれの`docs/ja/`ミラー。

## 検討した代替案

- **アクターとベリファイアの役割分担**：このアイテムの初期案です。1つのターゲットがシナリオの
  `steps`を実行し、もう1つが後から独立した`verify`ブロックを実行する形でした。却下した理由は、1つの
  シナリオにつき一方向の受け渡しを1回しか表現できないことです。あるターゲットを操作し、別のターゲット
  で確認し、また最初のターゲットを操作する、という行き来を表現できません。Web側の編集がアプリに届く
  ケースは、アプリ側の操作がWebに届くケースとちょうど同じくらいよくあります。
- **専用の`switchTarget`ステップ**：
  [BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation-ja.md)が
  デバイスモードの切り替え用にすでに検討し、却下した`setViewport`/`emulate`ステップに似ています。
  同じ理由で却下します。切り替えステップは、経路の指定を、読み手がステップ列全体を通して追わなければ
  ならない命令的なアクションに変えてしまいます。それが効くステップ自身に現れる性質にはなりません。
  `target`修飾子であれば、`capture`や`extract`がすでにそうしているように、修飾するステップそのものに
  効果が見える形を保てます。
- **ネットワークだけを見るクロスターゲットのアサーション**：2つ目の生きたドライバを一切持たず、操作
  対象自身の`request`アサーションだけで、もう一方のプラットフォームの正しいAPI（アプリケーション
  プログラミングインターフェース）を正しい内容で呼んだことを確認する案です。これはすでに今日
  できることであり、そこまでで十分なチーム向けの軽量な選択肢として残します。唯一の手段としては
  却下します。理由は、このアイテムの動機が挙げている問いとは別の問いに答えてしまうからです。操作
  対象が正しい呼び出しを送ったことは確認できても、もう一方のプラットフォームのクライアントがそれを
  受け取り正しく描画したかどうかには答えません。「もう一方のプラットフォームで結果を確認する」とは
  そういう意味です。
- **2つの連結したシナリオファイル**：一方のファイルが捕まえた`${vars.*}`を、別のターゲットに対して
  実行するもう一方のファイルへ渡す、新しいランナーのフラグを想定した案です。却下する理由は、1つの
  論理的なテストを2つのファイルに分けると、`preconditions`、`before`、`after`が両方に重複すること
  と、行き来を表現できないことです。最初のファイルが完全に終わってから2つ目が始まるので、アプリから
  Web、またアプリへ、というテストには3つ目のファイルが必要になってしまいます。
- **`--target`を複数指定可能にする案**：`--scenario`がすでに持つ複数指定可能な形
  ([`bajutsu/run/cli.py:1294-1303`](../../bajutsu/run/cli.py))に倣い、`list[str]`にする案です。
  シナリオ側の`targets`フィールドの代わりにこちらを採用することは却下します。あるシナリオがどの
  プラットフォームを行き来するかは、シナリオ自身が持つ性質であり、呼び出しごとの選択ではありません。
  `bajutsu run --scenario a.yaml b.yaml ...`のように複数ファイルを渡すCIジョブは、その呼び出しの中の
  どのファイルがどのターゲットを必要とするかに、`--target`のリストを常に同期させ続けなければ
  なりません。あるファイルの`targets`が変わった瞬間に、呼び出しのフラグとずれてしまいます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] スキーマ：`Scenario.targets`、`Step.target`、`target`の要否を`len(scenario.targets)`に紐づける
      バリデータ。
- [ ] CLI：自己宣言したシナリオのもとでの`--target`の省略、不一致の拒否、宣言された各名前の設定に
      対する検証。
- [ ] 起動と後片付け：`_ScenarioRunner`まで配線した`Config`、宣言したプラットフォームごとの1つの
      プール、宣言したターゲットごとに1つのドライバの、まとめての起動とまとめての後片付け。
- [ ] ランナー：`TargetRuntime`のまとまり、`run_scenario`の`target_runtimes`マッピング、ターゲット
      ごとのアクチュエータ/ロケール/`capture`/`interrupts`/ガード/ネットワーク/証跡コンテキストの
      組み立て。
- [ ] レポート：`StepOutcome.target`、`RunResult.target_devices`、それらを表示するレポートの画面。
- [ ] ドキュメント：`docs/scenarios.md`、`docs/cli.md`、`docs/run-loop.md`、それぞれの`docs/ja/`
      ミラー。

## 参考

- [`docs/scenarios.md`](../../docs/scenarios.md)：このアイテムが拡張するシナリオファイルの形式です。
- [`docs/run-loop.md`](../../docs/run-loop.md)：このアイテムが一般化する、`run_scenario`の単一
  ドライバのステップループです。
- [`docs/ja/glossary.md#target-app-device`](../../docs/ja/glossary.md#target-app-device)：ターゲットとは
  何か、それが駆動するアプリやデバイスとどう違うかです。
- [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)：
  このアイテムの複数ドライバ実行がなお依存する、バックエンドに依存しない中核です。各ターゲットは、
  実行全体で1つだったドライバの代わりに、宣言したターゲットごとに1つのインスタンスを持ちつつ、同じ
  `Driver`インターフェースで駆動し続けます。
- [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks-ja.md)：
  このアイテムのステップごとの`target`が同じく適用される、`before`/`after`ライフサイクルフェーズ
  です。
- [BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md)：
  このアイテムが変更なしにターゲットをまたいで共有する、`${vars.*}`の仕組みです。
- [BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation-ja.md)：今日の
  回避策(「2つのターゲットでシナリオを実行する」)をこのアイテムが1回の実行に置き換える先であり、
  このアイテムの設計が繰り返しを避けた、却下済みの`switchTarget`的なステップの出どころです。

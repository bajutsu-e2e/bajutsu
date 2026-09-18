[English](BE-0428-multi-target-scenario-execution.md) · **日本語**

# BE-0428 — 複数ターゲットを1シナリオ内で行き来する実行モデル

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0428](BE-0428-multi-target-scenario-execution-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装中** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0428") |
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
- name: liking a post on the app shows up on the web, and a web comment reaches the app
  targets: [showcase-app, showcase-web]
  steps:
    - target: showcase-app
      tap: { id: post.like }
      extract:
        postId: { sel: { id: post.id } }

    - target: showcase-web
      wait: { for: { id: "post.${vars.postId}.likeCount" }, timeout: 10 }
    - target: showcase-web
      assert:
        - value: { sel: { id: "post.${vars.postId}.likeCount" }, equals: "1" }

    - target: showcase-web
      tap: { id: "post.${vars.postId}.comment.input" }
    - target: showcase-web
      type: { text: "nice!" }

    - target: showcase-app
      wait: { for: { id: "post.${vars.postId}.comment.latest" }, timeout: 10 }
    - target: showcase-app
      assert:
        - value: { sel: { id: "post.${vars.postId}.comment.latest" }, equals: "nice!" }
```

`load_scenario_file`が受け付けるのは、シナリオのトップレベルのリスト、または`{description,
scenarios}`のマッピングだけであり、シナリオ自身の`name`から始まる素のマッピングは受け付けません
([`bajutsu/common/scenario/load.py:48-63`](../../bajutsu/common/scenario/load.py)、§6.1)。この
アイテムのすべてのシナリオ例が、この例も含めて1件だけのリストになっているのはそのためです。

上の例は説明のためのものです。上の例のような、iOSターゲットとWebターゲットの間で1つの製品を
共有するフィクスチャは、このリポジトリには存在しません。2つ目の例は、このリポジトリがすでに持って
いる2つのフィクスチャだけで組み立て、同じ仕組みを実在のシナリオに対して示します。
`showcase-swiftui`
([`demos/showcase/showcase.config.yaml`](../../demos/showcase/showcase.config.yaml))と`web`
([`demos/web/demo.config.yaml`](../../demos/web/demo.config.yaml))は、バックエンドを共有しない
本当に独立した2つのアプリです。したがって、ここで一方のステップが捕まえる値は、もう一方の側では
製品としての意味を持ちません。狙いは、実在のidに対して`targets`、ステップごとの`target`、
`${vars.*}`の共有が最後まで動くことを示すだけであり、アプリをまたいだ製品上のチェックを主張する
ものではありません。

```yaml
- name: favorite a horse on the iOS showcase, then carry what it captured into the web demo
  targets: [showcase-swiftui, web]
  steps:
    - target: showcase-swiftui
      wait: { for: { id: [stable.row.1, stable_row_1] }, timeout: 10 }
    - target: showcase-swiftui
      tap: { id: [stable.row.1, stable_row_1] }

    - target: showcase-swiftui
      wait: { for: { id: [horse.favorite, horse_favorite] }, timeout: 5 }
    - target: showcase-swiftui
      tap: { id: [horse.favorite, horse_favorite] }
      extract:
        favorited: { sel: { id: [horse.favorite.value, horse_favorite_value] } }

    - target: web
      tap: { id: onboarding.start }
    - target: web
      type: { text: "favorited-${vars.favorited}@example.com", into: { id: auth.email } }
    - target: web
      type: { text: "pw", into: { id: auth.password } }
    - target: web
      tap: { id: auth.submit }
    - target: web
      wait: { for: { id: home.title }, timeout: 5 }
    - target: web
      tap: { id: counter.increment }
  expect:
    - target: showcase-swiftui
      value: { sel: { id: [horse.favorite.value, horse_favorite_value] }, equals: "on" }
    - target: web
      value: { sel: { id: counter.value }, equals: "1" }
```

iOS側のステップは、`demos/showcase/scenarios/firstlook.yaml`自身の「馬をお気に入りに登録する」
流れを踏襲しています(BE-0221がすでに要求している、ドット区切りとアンダースコア区切りのid併記も
含めて)。ただし途中の「まだoffである」ことを確認するアサーション、`preconditions.launchEnv`、
`capture`修飾子は、簡潔さのために省いています。Web側のステップは、
`demos/web/scenarios/counter.yaml`自身のオンボーディングフローの冒頭です。どちらも今日すでに、
それぞれ単独の単一ターゲットシナリオファイルとして動いています。この例が加えているのは`targets`、
`target`、そして`extract`/`${vars.*}`による両者の受け渡しだけです。

このファイルをこのリポジトリの現状に対してそのまま実行するには、この例が省いているもう1段階が
必要です。`showcase-swiftui`と`web`は、
[`demos/showcase/showcase.config.yaml`](../../demos/showcase/showcase.config.yaml)と
[`demos/web/demo.config.yaml`](../../demos/web/demo.config.yaml)という別々の2つの設定ファイルに
それぞれ宣言されています。そして、上の「参加するターゲットを宣言する」の規則は、`scenario.targets`
のすべての名前をその呼び出しで`bajutsu run`が読み込む1つの設定に対して解決します。複数の設定を
同時には解決しません。この提案は、2つの設定ファイルを1つにまとめる方法を加えません。この
シナリオを記載どおりに実行するには、`showcase-swiftui`と`web`の両方を`targets.<name>`として並べて
宣言する設定が必要です。`demo.config.yaml`の`web`のブロックを、コピーした`showcase.config.yaml`
へ貼り付ける(あるいはその逆でも構いません)だけで十分です。どちらのターゲットの設定も、自分だけが
存在することに依存していないためです。`bajutsu run --scenario`が両方を1回の読み込みから解決できる
ようになるのは、そのあとです。それ以外の点でこの例は変わりません。実在のid、既存の2つのシナリオ
ファイルそれぞれの実際のステップの流れであり、2つのアプリがデータを共有しているという製品上の
主張はしていません。

`Scenario`は`targets: list[str] = Field(default_factory=list)`を新たに持ちます。既存の`before`、
`steps`、`after`と並ぶ位置です
([`bajutsu/common/scenario/models/scenario/scenario.py:45-84`](../../bajutsu/common/scenario/models/scenario/scenario.py))。
各要素は`targets.<name>`という設定単位を指します。これは、`--target`が今日解決しているのと同じ
[ターゲット](../../docs/ja/glossary.md#target-app-device)です。宣言された名前は、読み込んだ設定に
すでに存在していなければなりません。チェック方法は`--target`と同じです。また、`targets`の中で同じ
名前を2回宣言することは、黙って重複除去されるのではなく、読み込み時に拒否されます。

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

読み込みのあとに2箇所、このバリデータがまったく見ないステップを追加できる場所があります。どちらも、
`Scenario.model_validate`を通して新しいオブジェクトを組み立てるのではなく、すでに検証済みの
`Scenario`をその場で書き換えるからです。Pydanticは、ふつうの属性代入や`model_copy`に対して
`model_validator`を再実行しません。`expand_components`
([`bajutsu/common/scenario/expand.py:104-114`](../../bajutsu/common/scenario/expand.py))は、
`use`ステップをそのコンポーネント自身のステップで置き換える際、`scenario.steps = expand(...)`
という代入を(`before`、各`after`ルールの`steps`、各`interrupts`エントリの`steps`についても
同様に)`Scenario`がすでに読み込まれ検証された後に行います。コンポーネント自身のステップが
`target`を省略していても、ほかのステップごとの規則はすべて満たしてしまうため、このアイテムが
求める規則だけを静かにすり抜けます。`with_lifecycle_phases`
([`bajutsu/common/runner/pipeline.py:959-983`](../../bajutsu/common/runner/pipeline.py))は、
あるターゲットの設定レベルの`before`/`after`フックを`model_copy`でシナリオへ折り込みますが、
これもPydanticは再検証しません。このアイテムは、`target`必須チェックを`Scenario`自身の
`model_validator`から素の関数として切り出し、この2つの書き換え箇所がそれぞれ自分の結果に対して
もう一度呼び出すことで、両方の抜け穴をふさぎます。`expand_components`がシナリオの処理を終えた
直後にもう一度、`with_lifecycle_phases`が折り込み済みのコピーを組み立てた直後にもう一度呼び出し、
`target`を持たない裸のステップを含むシナリオファイルがすでに起こしているのと同じ、読み込み時の
エラーを起こします。コンポーネントや設定レベルのフックが静かにすり抜けることはありません。
`with_lifecycle_phases`自身もターゲットごとに呼ぶ必要があります(あるターゲットの`before`/`after`
フックはそのターゲット自身の設定に由来するため。詳しくは後述の「宣言したターゲットをまとめて
起動し、まとめて後片付けする」を参照してください)。そして、この再検査が走る前に、折り込む各フック
ステップへそのターゲット自身の名前を刻みます。これにより、設定レベルのフックも、著者が書いた
ステップと同じように、どのターゲットに対して動くのかが明確になります。この再検査は主に、どちらかの
関数を将来呼び出す誰かが、自分の挿入するステップへ名前を刻み忘れた場合への保険として存在します。

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

シナリオレベルの`expect`ブロックにも、`steps`と同じ理由で同じフィールドが必要です。今日は
単一のドライバに対する1回の条件待ちポーリングとして評価されます。
`_evaluate_expect(driver, expect, network, clock, ctx=...)`
([`bajutsu/common/orchestrator/loop/_functions.py:157-171`](../../bajutsu/common/orchestrator/loop/_functions.py))
は、`run_scenario`自身の合格経路と、ガード解除後のリトライの両方から呼ばれます
([`bajutsu/common/orchestrator/loop/_functions.py:748-760`](../../bajutsu/common/orchestrator/loop/_functions.py)、
[`:1076`](../../bajutsu/common/orchestrator/loop/_functions.py))。そこで`Assertion`
([`bajutsu/common/scenario/models/assertions/assertion.py:23-56`](../../bajutsu/common/scenario/models/assertions/assertion.py))
は`target: str | None = None`を持ちます。既存の`from_`という来歴フィールドと同じように
`_ASSERTION_KINDS`から除外されます。`Scenario`レベルのバリデータの規則は、そのまま`expect`の
各エントリにも及びます。`scenario.targets`が0個か1個なら任意(または宣言した1個との一致)、2個以上
なら必須です。`_evaluate_expect`は`expect`を各エントリが指すターゲットごとにまとめ、`_poll_asserts`
([`bajutsu/common/orchestrator/loop/_functions.py:115-123`](../../bajutsu/common/orchestrator/loop/_functions.py))
を、参照されたターゲットごとに、その`TargetRuntime`自身のドライバとネットワークソースを使って1回
ずつ呼び出し、結果をシナリオが宣言した順序どおりに1つの`expect_results`リストへまとめ直します。
`AssertionResult`
([`bajutsu/common/assertions/_common.py:29-38`](../../bajutsu/common/assertions/_common.py))は、
既存の`kind`と並んで`target: str = ""`を持ちます。これは`StepOutcome.target`(後述)と同じ、レポート
上の理由からの鏡像です。インラインの`assert:`の結果はすでにそのステップ自身の`target`で範囲が
決まっているため、これが意味を持つのは`expect_results`だけです。

`Assertion`にこのフィールドをモデル自体に1回だけ加えると、同じ`target`キーは`expect`だけでなく
`Assertion`が現れるあらゆる場所で文法上使えるようになってしまいます。`Step`が持つインラインの
`assert:`のリスト
(`assert_: list[Assertion] | None`、
[`bajutsu/common/scenario/models/steps/step.py:92`](../../bajutsu/common/scenario/models/steps/step.py))と、
`If`が包む`condition: Assertion`
([`bajutsu/common/scenario/models/steps/if_.py:20`](../../bajutsu/common/scenario/models/steps/if_.py))は、
どちらもすでに、それを包むステップ自身の`target`フィールドが指すターゲットに対して実行されます。
そのため、そのアサーションの中で`target`を設定しても、同じ値を冗長に繰り返すだけか、最悪の場合は
それと黙って食い違ってしまいます。`Scenario`レベルのバリデータは、上の`target`に関する規則に
もう1つの検査を加えます。`steps`、`before`、`after`をこれまでと同じように走査し、インラインの
`assert:`のリストや`if`の`condition`を通じて到達した`Assertion`が`target`を少しでも設定していれば、
読み込み時に拒否します。`target`を設定できるのは、トップレベルの`expect`ブロックを通じて到達
した`Assertion`だけです。

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

`--target`は呼び出し全体で1つのフラグですが、`--scenario`は繰り返し指定できるため、1回のバッチが
複数のファイルを同時に名指しできます。そして、従来のファイル(`targets`が空)には頼れる自分自身の
`target`がなく、今日と同じく、どのターゲットに対して実行するかを知るには呼び出し全体の単一の
`--target`が必要です。したがって、従来のファイルを1つでも含むバッチで`--target`を省略すると、
そのファイルには解決する手立てが何も残りません。このアイテムはそのようなバッチを読み込み時に
拒否します。従来のファイルが1つでもあるのに`--target`がない組み合わせはエラーであり、そのファイル
名を示したうえで、`--target`を渡すかそのファイル自身に`targets`フィールドを持たせるよう求めます。
`--target`を渡しつつ従来のファイルと自己宣言したファイルを混在させたバッチは、今日の単一ターゲット
の規則どおりに動き続けます。従来のファイルは今までどおりそれを使い、自己宣言したファイルに対しては
上の不一致規則のとおり、そのファイル自身の`scenario.targets`への所属で照合されます。

このディレクトリの一括読み込みは`run`が持つ唯一のスイート全体向けの簡便な手段であり、1つのターゲット
に属します。そこでこのアイテムは、著者がディレクトリの配置で守る運用上の規律ではなく、実際に強制
されるチェックにします。`--scenario`を渡さない普通の`bajutsu run --target <name>`が取る
ディレクトリの一括読み込み経路は、発見した時点で、自分の`targets`フィールドが空でないファイルを、
前述の不一致チェックに渡したり起動したりするのではなく拒否します。ファイル名を示し、代わりに
`--scenario`を使うよう伝える、実行そのものが始まる前の単純なエラーです。このチェックがなければ、
そのようなファイルがそのディレクトリに置かれた時点でどちらの結果も望ましくありません。`name`が
そのファイル自身の宣言したターゲットのどれでもなければ、前述の不一致チェックが拒否し、同じバッチの
中のほかのすべてのシナリオまで道連れに失敗します。`name`がたまたま宣言したターゲットの1つであれば、
所属チェックは通過しますが、そのバッチはその1つのターゲットだけでなく、ファイルが宣言するほかの
すべてのターゲットまで黙って起動してしまいます。発見した時点での拒否があれば、自己宣言したシナリオ
は明示的な`--scenario <file>`でしか実行できなくなるため、どちらの望ましくない結果も、ファイルが
ツリーのどこに置かれているかに左右されなくなります。自己宣言したシナリオは、専用のディレクトリへ
まとめておくとツリーを読む人に分かりやすいという点で今も良い習慣ですが、このアイテムはもはやそれを
正しさの拠り所にしません。

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

プールが存在するより前に、実行につき1つの`DeviceLease`がすでに取得されています。これは、この
アイテムがここまで一般化してきたシナリオごとの`pool.lease()`/`Lease`とは別の、その手前にある
概念です。`acquire_device(eff, udid)`
([`bajutsu/run/cli.py:1514`](../../bajutsu/run/cli.py))は、ターゲット自身の
`eff.device_provider`
([`bajutsu/common/config/schema/target_config.py:72`](../../bajutsu/common/config/schema/target_config.py)、
`targets.<name>.deviceProvider`、BE-0236)を呼び出します。これは未設定なら組み込みの`local`
プロバイダ、そうでなければ実際のデバイスクラウドプロバイダです。そして、`make_pool`を含む後続の
すべての手順がデバイスを解決する元になる`udid_spec`とプロビジョニングデータを返します。この呼び
出しは今日どの実行でも無条件に1回行われ、1つのターゲットの`eff`を読みます。`deviceProvider`は
ターゲットごとの設定なので、このアイテムはプールを構築する前に、宣言した各ターゲットにつき1回
これを呼び、後片付けの際にはドライバとプールに並べて各ターゲット自身の`DeviceLease`を解放します。

宣言した各ターゲットのドライバを立ち上げるには、共有された1つのプールではなく、それぞれ専用の
プールが要ります。プールは今日プラットフォーム固有だからです。`make_pool`は、1つのプラットフォーム
のudid一覧から1組の`pool_actuator`/`pool_env`だけを解決し、そのプラットフォーム自身のコレクタを
あらかじめ起動します
([`bajutsu/common/runner/pool.py:149-182`](../../bajutsu/common/runner/pool.py))。このアイテムの
見出しの例であるiOSターゲットとWebターゲットには、1つのプールが両方を扱うのではなく、別々に構築
された2つのプールが必要です。`run_all`は、今日構築している単一のプールの代わりに、シナリオ集合
全体で宣言されたターゲットが指す、それぞれ異なるプラットフォームごとに1つのプールを構築します。
これも今までどおり実行につき1回だけですが、それぞれのプールには、前段落のそのプラットフォームに
属する各ターゲット自身の`DeviceLease`が供給されます。`_run_one_impl`は、自分のシナリオが宣言する各ターゲット
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
しなければなりません。これらのターゲットごとの値もすべて同じ方法で導かれ、そのドライバと並べてキーになります。さらに
もう2つのフィールドも同じように移します。これは、この設計の以前の草案がランレベルの共有のままに
していたことを修正するものです。`caps`は、今日は1つのターゲットのアクチュエータから実行につき1回
だけ計算され
([`bajutsu/common/backends.py:194-236`](../../bajutsu/common/backends.py))、どのデバイスもリース
する前にシナリオ*全体*に対して1回だけ検査されます
([`bajutsu/common/runner/pipeline.py:373`](../../bajutsu/common/runner/pipeline.py)、BE-0082の
フェイルファストな事前検査)。これは実行の性質ではなく、1つのバックエンドが持つ能力集合の性質です。
これを共有すると、2つ目に宣言したターゲットのステップを主ターゲットの能力に対して事前検査して
しまい、2つ目のターゲットが対応している構文を誤って拒否したり、対応していない構文を誤って許可
したりします。このアイテムは`caps`も同じ方法で宣言したターゲットごとに解決し、事前検査を宣言した
ターゲットごとに1回、そのターゲット自身のステップだけ（`step.target`でグループ化します。これは
このアイテムの`_evaluate_expect`の変更がすでに`expect`に導入しているグループ化と同じです）と
そのターゲット自身の能力集合に対して実行します。`mailbox`もランレベルではありません。すでに1つの
ターゲット自身の設定
(`targets.<name>.mailbox`、[`bajutsu/common/config/schema/target_config.py:103`](../../bajutsu/common/config/schema/target_config.py))
から来ており、`email`/`totp`ステップは*実行*の単一のターゲットが設定した受信箱を読みます
([`bajutsu/common/orchestrator/loop/_functions.py:281`](../../bajutsu/common/orchestrator/loop/_functions.py))。
宣言したターゲットの間で1つの受信箱を共有すると、あるステップのターゲットが別の受信箱を設定して
いる場合に、誤った受信箱をポーリングしてしまいます。`caps`と`mailbox`はどちらも、上のほかの
ターゲットごとのフィールドと同じく、`_ScenarioRunner`にとどまるのではなく`TargetRuntime`に
加わります。ランレベルのままにするのは`redactor`だけです。これは、どのターゲットの証跡を
redactionする場合にも、宣言した各ターゲット自身の`secrets`の和集合を適用します。あるターゲットの
設定が秘密として指定した値は、そのターゲット自身の証跡だけからではなく、どこからでも取り除かれます。
redactionの対象を広げることは、狭めることより厳密に安全です。これは、あるターゲット自身の値を
共有すると別のターゲットに対して誤った答えを生む`caps`や`mailbox`とは異なります。

`with_lifecycle_phases`
([`bajutsu/common/runner/pipeline.py:959-983`](../../bajutsu/common/runner/pipeline.py))は、
実行が始まる前に、あるターゲットの設定レベルの`before`(設定してからシナリオの順)と`after`
(シナリオしてから設定の順)のフックをシナリオへ折り込みます(BE-0392)。今日はCLIの単一の`eff`
から実行につき1回だけ呼ばれますが、このアイテムは宣言したターゲットごとに、そのターゲット自身の
`eff`で1回ずつ呼びます。これにより、宣言した各ターゲット自身の`targets.<name>.before`/`after`
フックが、主ターゲットのものだけでなくそれぞれに対して折り込まれます。複数のターゲットのフックを
シナリオの単一の`before`/`after`リストへまとめるには、このアイテムが明示的に順序を加える必要が
あります。宣言した各ターゲット自身の`before`フックは、シナリオ自身の`before`ステップより前に
折り込まれ、ターゲット同士の間では`scenario.targets`の宣言順です。これは今日の単一の
「設定してからシナリオ」の順序を踏襲します。`after`はその逆順です。シナリオ自身のステップの
後片付けが先に来て、そのあとに宣言した各ターゲット自身の`after`フックが同じ宣言順で続きます。
これは今日の「シナリオしてから設定」の順序を踏襲します。折り込む各フックステップには、自分自身の
ターゲットの名前を刻みます。これは前段落の再検査バリデータがすでに求めている修正であり、同じ
シナリオに折り込まれたアプリ側の`erase`フックとWeb側のフックが、それぞれ正しいターゲットに対して
動き続けるためです。

`preconditions`と`permissions`にはこのようなターゲットごとの分割は要りません。どちらもすでに
今日、シナリオレベルのフィールドです(`scenario.preconditions`、`scenario.permissions`)。これらは
`launch_driver`
([`bajutsu/common/runner/launch.py:27-98`](../../bajutsu/common/runner/launch.py))へ1回だけ渡され、
すでにiOS固有ではなくバックエンドを問いません。`env.start`は、iOSの`Preconditions`フィールド
(erase、reinstall)をsimctlのライフサイクルを通じてすでに解釈し、Webターゲット自身の`env.start`は
代わりに新しいブラウザコンテキストとして解釈します。これは、`--target ios`と`--target web`の
それぞれの実行が、1つのバックエンドずつ今日すでに行き来している違いそのものです。このアイテムの
ターゲットごとの`launch_driver`呼び出しは、同じ`scenario.preconditions`と`scenario.permissions`を
宣言した各ターゲット自身の呼び出しへ渡し、各ターゲット自身のバックエンドが、自分に当てはまる部分
だけを解釈し残りを無視し続けます。単一ターゲットの実行がすでに、`--target`がある`Preconditions`
フィールドの当てはまらないバックエンドを指すたびに頼っている仕組みであり、このアイテムが新たに
発明する能力ではありません。

宣言したターゲットごとに1つのリースを取得するには、このアイテムが明示的に加えるルールがもう1つ
必要です。`pool.lease()`は、実行のudidを入れたキューに対して`free.get()`でブロックします
([`bajutsu/common/runner/pool.py:163-165, 255`](../../bajutsu/common/runner/pool.py))。したがって、
デバイスに紐づくN個のターゲットを、前段落のとおり最大N個の異なるプラットフォームごとのプールから
リースするシナリオが、`--workers`によって複数同時に走っていると、飢餓状態やデッドロックになり得ます。
どのワーカーも1つのプールから1台のデバイスを保持したまま、別のプールから次の1台を取得しようとして
永遠にブロックしてしまいます。「1回でまとめて確保する」と言うだけでは、実際のプロトコルが伴わない
限り意味を持ちません。`pool.lease()`にも、独立した2つ目のプールオブジェクトにも、両者にまたがる
トランザクションは用意されていないからです。そこで、このアイテムの起動処理は、シナリオが必要と
するすべてのプールを決まった一意のキー（宣言されたプラットフォーム名をソートしたもの）の順に
並べます。どのワーカーも例外なく、その順序どおりに厳密に取得します。これは、この段落で扱っているあらゆる
デッドロックの形である循環待ちを排除する、標準的なロック順序付けの原則です。ある順序で次のプールを
取得できないワーカーは、その1つのプールのキューだけでブロックし、決まった順序でそれより前のプール
だけを保持し続けます。したがって、両方のワーカーが同じ順序ですべてのプールに近づく以上、互いが次に
必要とするプールを互いに保持してしまうことは起こりません。ワーカーは、自分の順序の後方にある
プールがタイムアウトした場合、一部だけを無期限に保持し続けるのではなく、すでに保持しているものを
すべて解放します。

後片付けは、今日1回の起動が1つのシナリオを包んでいるのと同じ形で、宣言した集合全体を包みます。
途中で起動が失敗した場合は、その時点までに起動できていたドライバをすべて後片付けしてから、失敗を
伝えます。これは、`env.start`の後に準備確認が失敗した場合の`launch_driver`自身のガード
([`bajutsu/common/runner/launch.py:100-108`](../../bajutsu/common/runner/launch.py))と同じ考え方
です。実行の終わりには、`_run_on_lease`の`finally: lz.release()`
([`bajutsu/common/runner/pipeline.py:955-956`](../../bajutsu/common/runner/pipeline.py))が今日1つ
のリースを後片付けしているのと同じ方法で、宣言したターゲットのリースごとにこの解放を呼びます。

### ステップをそのドライバへ振り分け、`${vars.*}`を全ターゲットで共有する

`run_scenario`の既存のフラットなキーワードパラメータ、`driver`、`sink`、`alert_guard`、`network`、
`relaunch`、`control`、`ctx`、`mailbox`、`webview_bridge`、`transitions`、`interrupts`、`locale`、
`capture`、`channel`、`target_launch_env`
([`bajutsu/common/orchestrator/loop/_functions.py:572-594`](../../bajutsu/common/orchestrator/loop/_functions.py))
は、まとめて見ると、今日パイプラインが1つのターゲットの1つのリースから束ねているものすべてです
（前節の一覧のとおりです）。`target`を持たないステップは、今までどおりこれらすべてを読み続けます。
これらは`run_scenario`の主ターゲットのランタイムのままで、形は変わらないため、既存の呼び出し元は
影響を受けません。`run_scenario`は、これらの各フィールドをターゲットごとのマッピングへその場で
広げる代わりに、それ以外の宣言済みターゲットごとに1つずつ、新しいパラメータを1つ持ちます。
`target_runtimes: Mapping[str, TargetRuntime] | None = None`です。`TargetRuntime`(新しい小さな
データクラス)は、同じフィールドの並びに加えて`caps`も束ねます。ほかのフィールドと違い、`caps`は
`run_scenario`自身のキーワード引数として渡されることはありません。それを参照する事前検査は
`run_scenario`を呼ぶより前の`_run_one_impl`の中で走るからです。それでも宣言したターゲットごとに
同じ方法で解決されるため、別の入れ物を新設するのではなく、ほかのフィールドと並べて各ターゲット
自身の`TargetRuntime`に載せて運びます。追加で宣言したターゲット1つにつき1インスタンスであり、
前節ですでにドライバのマップを組み立てているのと同じ方法で`_run_one_impl`が組み立てます。`_StepRunner`の各メソッドは、すでに`active_driver: base.Driver`という明示的な引数を、
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

### この提案が実装に残す1つの未決事項

この設計の以前の草案が未決のままにしていたほかの共有状態は、この設計の中ですでに解決されているか、
そもそも解決の必要がないと確認できました。`_ScenarioRunner`の`run_dir`は
`runs_dir / run_id`から導かれ
([`bajutsu/common/runner/pipeline.py:1223`](../../bajutsu/common/runner/pipeline.py))、どのターゲット
の`Effective`からも導かれたことのないラン全体のアーティファクトディレクトリです。したがって
ターゲットごとの一般化は必要ありません。`udid_spec`、`actuator`、`baselines_dir`、`schemas_dir`、
`golden_context`は、いずれも上の「宣言したターゲットをまとめて起動し、まとめて後片付けする」が
`caps`や`mailbox`と並んで、宣言したターゲットごとに解決しているフィールドそのものです。任意の
シナリオごとの`resolve_actuator`コールバック(BE-0240、
[`bajutsu/common/runner/pipeline.py:190`](../../bajutsu/common/runner/pipeline.py))は、黙って
先送りするのではなく、明示的にスコープ外とします。これを渡す呼び出し元は、このアイテムが一般化する
プールベースのアクチュエータ選択からすでに外れています。シナリオ全体で1つのアクチュエータを返す
代わりに、宣言したターゲットごとに1つのアクチュエータを解決できるようにする拡張は、
`resolve_actuator`と複数ターゲットのシナリオを両方必要とする将来のアイテムに委ねます。

未決の問いが1つだけ残ります。`gc_with_screen`
([`bajutsu/common/runner/pipeline.py:875-884`](../../bajutsu/common/runner/pipeline.py))は、
`golden`のフレーム健全性チェック(BE-0006)のために1つのドライバの画面サイズを調べます。iOSターゲット
とWebターゲットは一般に画面のジオメトリが異なり、今日は1つの`GoldenContext`しか`run_scenario`に
届きません。したがって、2つ目に宣言したターゲットに対する`golden`アサーションの挙動は未決です。
この提案が見出した形は、ターゲットごとの`GoldenContext`を持つか、`golden`を1つの宣言した
ターゲットに限定するかの2つであり、どちらを選ぶかは推測せずに先送りします。

### 作業分解(MECE)

1. **スキーマ**：`Scenario.targets: list[str]`（重複した名前は読み込み時に拒否）、`Step.target: str |
   None`、`Assertion.target: str | None`（`from_`と同様に`_ASSERTION_KINDS`から除外）、`target`の
   要否を`len(scenario.targets)`に紐づける`Scenario`レベルのバリデータ（`Step`自身のバリデータは
   外側のシナリオを見られないため不可）。`steps`、`before`、`after`の各ルールの`steps`を、
   `if`/`forEach`のネストしたステップリストにも再帰的に適用します。`web:`ブロックにネストした
   ステップだけは例外で、`target`を宣言するのではなく省略するよう求めます。これは、そのステップが
   常に、囲んでいる`web:`ステップがすでに解決したターゲットに対して実行されるからです。同じ
   要否の規則を`expect`にも適用し、インラインの`assert:`リストや`if`の`condition`を通じて到達した
   `Assertion`（`expect`を経由しないもの）が`target`を設定していれば、それも拒否します。この検査
   全体を素の関数として切り出し、`expand_components`と`with_lifecycle_phases`がそれぞれ自分の
   結果に対してもう一度呼び出せるようにします。どちらもすでに検証済みの`Scenario`をその場で
   書き換えるため、Pydanticはふつうの属性代入や`model_copy`に対して`model_validator`を
   再実行しないからです。
2. **CLI**：`scenario.targets`が空でなければ`--target`を省略可能にし、代わりに`--scenario`を必須に
   します。明示的な`--target`は黙って上書きせず`scenario.targets`と照合します。シナリオファイルの
   ローダーは、宣言された各名前を、実行が始まる前に読み込んだ設定に対して検証します。`--target`
   だけを渡すディレクトリの一括読み込み経路は、発見した時点で、自分の`targets`フィールドが空でない
   ファイルを、自己宣言したシナリオを不一致チェックに渡したり起動したりするのではなく拒否します。
   `--target`を省略したバッチに従来のファイル(`targets`が空)が1つでも含まれていれば、解決する
   手立てのないそのファイル名を示して、読み込み時に拒否します。
3. **起動と後片付け**：宣言したターゲットごとに、どのプールが存在するより前に`DeviceLease`を1つ
   取得し、後片付けの際にはドライバとプールに並べて解放します。`_load_effective_with_source`と
   `_resolve_config_and_engines`が、すでに返している`Effective`と並べて、読み込んだ`Config`も返す
   ようにします。それを`run`のCLIから`run_and_report`と`run_all`を経由して、`_ScenarioRunner`の
   新しい読み取り専用フィールドまで配線します（どちらも今日は受け取っていません）。`run_all`は、
   今日の単一プールの代わりに、宣言されたターゲットが指すプラットフォームごとに1つのプールを、
   それぞれそのプラットフォームに属する各ターゲット自身の`DeviceLease`で供給しながら構築します。
   `_run_one_impl`は、宣言したターゲット名ごとに1つの、すでにリベース済みの`Effective`をローカルな
   マップへ解決します（共有される`_ScenarioRunner`には保存しません）。ターゲットごとに任意の順で
   `pool.lease()`を1つずつ呼ぶのではなく、シナリオが必要とするすべてのプールを決まった一意の順序
   (宣言されたプラットフォーム名をソートしたもの)で取得します。この標準的なロック順序付けにより、
   どの2つのワーカーも互いの次のプールを取得できずデッドロックすることがなくなり、順序の後方にある
   プールがタイムアウトした場合はすでに保持しているものをすべて解放します。名前ごとに1回
   `launch_driver`を呼び出し、ローカルな`dict[str, base.Driver]`にまとめます。すべて最初のステップ
   より前に済ませ、シナリオ自身の変更しない`preconditions`/`permissions`をどの呼び出しにも渡します。
   `with_lifecycle_phases`を宣言したターゲットごとに、そのターゲット自身の`eff`で1回ずつ呼び、
   折り込む各フックステップにそのターゲット自身の名前を刻み、宣言したターゲットのフックを宣言順で
   シナリオの`before`/`after`へまとめます。途中の起動失敗は、その時点までに起動できたドライバを
   すべて後片付けします。実行の終わりは集合全体を後片付けします。
4. **ランナー**：`run_scenario`が今日1つのリースから束ねているすべての引数（`driver`、`sink`、
   `alert_guard`、`network`、`relaunch`、`control`、`ctx`、`mailbox`、`webview_bridge`、
   `transitions`、`interrupts`、`locale`、`capture`、`channel`、`target_launch_env`）に加えて、
   `caps`も束ねる新しい`TargetRuntime`データクラス。`caps`はほかのフィールドと違い、
   `run_scenario`を呼ぶより前の`_run_one_impl`内の事前検査が直接参照するため、`run_scenario`へは
   渡しません。主ターゲット向けの変更しないフラットなパラメータと並ぶ、`run_scenario`の新しい
   `target_runtimes: Mapping[str, TargetRuntime] | None`パラメータ。`step.target`を主ターゲットの
   値ではなく`active_driver`/`target_runtimes`に対して解決する、`_run_one`のステップ振り分け。
   宣言したターゲットごとに1つ`TargetRuntime`を組み立てる`_run_one_impl`は、今日1組の
   リースごとの引数がシナリオごとに1回組み立てられているのと同じ方法です。`_evaluate_expect`が
   `expect`をターゲットごとにまとめ、参照されたターゲットごとに、その自身のドライバとネットワーク
   ソースで`_poll_asserts`を1回呼び出し、結果をまとめ直します。
5. **レポート**：`StepOutcome.target`（宣言されたターゲットが1個の場合の既定値を含む）、
   `expect_results`だけに向けてこれを鏡映しする`AssertionResult.target`（インラインの`assert:`の
   結果は引き続きそのステップ自身の`target`で範囲が決まります）、複数ターゲットの実行で空のままに
   なる`RunResult`の単数形フィールド、`RunResult.target_devices`、Stepsビューのターゲットラベル、
   宣言した各ターゲットのデバイスを一覧するヘッダーブロック。
6. **ドキュメント**：`docs/scenarios.md`(`targets`/`target`のリファレンスと実例)、`docs/cli.md`
   (`--target`の新しい省略条件と`--scenario`の新しい必須条件)、`docs/run-loop.md`(複数ドライバの
   ステップ振り分け)、それぞれの`docs/ja/`ミラー。
7. **テスト**：スキーマ側は、`targets`が0個・1個・2個以上のそれぞれでの`target`必須バリデータを
   `steps`/`before`/`after`/`expect`と`if`/`forEach`/`web`のネストした形すべてに対して、重複名の
   拒否、`expect`の外での`Assertion.target`の拒否、そして`expand_components`と
   `with_lifecycle_phases`を経ても再検査が生き残ることを検証します。CLI側は、`--target`の省略・
   必須の切り替え、不一致の拒否、自己宣言したファイルのディレクトリ一括読み込みでの拒否、従来の
   ファイルと自己宣言したファイルが混在するバッチの拒否を検証します。起動側は、複数ターゲットの
   集合の途中の起動失敗が起動済みのものを後片付けすること、ロック順序付けによる取得が同じ2つの
   プラットフォームを逆順に必要とする2つのワーカー間でデッドロックしないこと、ターゲットごとの
   `caps`事前検査が主ターゲットにしかない構文だけを拒否すること、ターゲットごとの`mailbox`選択を
   検証します。ランナー側は、1つのシナリオの中でのターゲット混在ステップ振り分け(隣接する2つの
   ブロックだけでなく行き来すること)、ターゲットをまたいだ`${vars.*}`共有、`expect`が宣言順で
   まとめ直されることを検証します。レポート側は、複数ターゲットの`RunResult`で単数形フィールドが
   空のまま`target_devices`が埋まること、既存のJUnit/CTRFの読み手が単一ターゲットの実行を今までと
   変わらず解釈できることを検証します。

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

- [x] スキーマ：`Scenario.targets`、`Step.target`、`Assertion.target`、`target`の要否を
      `len(scenario.targets)`に`steps`と`expect`の両方で紐づけるバリデータ。
- [ ] CLI：自己宣言したシナリオのもとでの`--target`の省略、不一致の拒否、宣言された各名前の設定に
      対する検証。
- [ ] 起動と後片付け：`_ScenarioRunner`まで配線した`Config`、宣言したプラットフォームごとの1つの
      プール、宣言したターゲットごとに1つのドライバの、まとめての起動とまとめての後片付け。
- [ ] ランナー：`TargetRuntime`のまとまり、`run_scenario`の`target_runtimes`マッピング、ターゲット
      ごとのアクチュエータ/ロケール/`capture`/`interrupts`/ガード/ネットワーク/証跡コンテキストの
      組み立て、`_evaluate_expect`のターゲットごとのグループ化。
- [ ] レポート：`StepOutcome.target`、`AssertionResult.target`、`RunResult.target_devices`、それらを
      表示するレポートの画面。
- [ ] ドキュメント：`docs/scenarios.md`、`docs/cli.md`、`docs/run-loop.md`、それぞれの`docs/ja/`
      ミラー。
- [ ] テスト：スキーマのバリデータ（`expand_components`/`with_lifecycle_phases`の再検査を含む）、
      CLIの選択・拒否の規則、複数プールのリース・デッドロック網羅、ターゲットごとの
      caps/mailbox/事前検査、ターゲット混在のステップ振り分け、レポートの後方互換性。

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

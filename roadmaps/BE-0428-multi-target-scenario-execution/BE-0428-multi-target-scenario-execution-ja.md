[English](BE-0428-multi-target-scenario-execution.md) · **日本語**

# BE-0428 — 複数ターゲットを1シナリオ内で行き来する実行モデル

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0428](BE-0428-multi-target-scenario-execution-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0428") |
| 実装 PR | [#2025](https://github.com/bajutsu-e2e/bajutsu/pull/2025)（単位 1）、[#2028](https://github.com/bajutsu-e2e/bajutsu/pull/2028)（単位 2〜7） |
| トピック | Scenario authoring features |
| 関連 | [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)、[BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks-ja.md)、[BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow-ja.md)、[BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation-ja.md) |
<!-- /BE-METADATA -->

## はじめに

シナリオに、新しいフィールドを2つ追加します。トップレベルの`targets`は、そのシナリオが操作する[ターゲット](../../docs/ja/glossary.md#target-app-device)をすべて列挙します。ステップごとの`target`は、各ステップがどのターゲットで実行されるかを指定します。

両方を設定すると、1つのシナリオファイルの中で、あるターゲットを操作し、別のターゲットで結果を確認できます。行き来は自由です。iOSターゲットで「いいね」をタップし、Webターゲットでカウントが更新されたことを確認できます。続けてWebターゲットでコメントを編集し、iOSターゲットに届いたことを確認できます。この一連の流れは、決定的な[`bajutsu run`](../../docs/cli.md)の1回の呼び出しとして、1つの合否判定で実行されます。

`targets`に列挙したターゲットは、最初のステップの前にまとめて起動し、最後のステップの後にまとめて後片付けします。`target`を省略したステップは、今までと同じ挙動をします。既存の単一ターゲットのシナリオは、変更が要りません。

本項目は`bajutsu run`だけを対象とします。`bajutsu crawl`、`bajutsu record`、`serve`自身のディスパッチUIも、今日は`run`と同じ方法で単一のターゲットを解決しています。それぞれに`targets`/`target`を通すための追従作業が必要であり、今回のスコープには含めません。

## 動機

`bajutsu run --target <name>`は、ターゲットをちょうど1つだけ受け取ります([`bajutsu/run/cli.py:1293`](../../bajutsu/run/cli.py))。ランナー側も、あらゆる層でこの制約に揃っています。

- `run_scenario`は、`driver: base.Driver`をちょうど1つだけ受け取ります([`bajutsu/common/orchestrator/loop/_functions.py:572-574`](../../bajutsu/common/orchestrator/loop/_functions.py))。
- パイプラインは、シナリオ実行1回につきドライバを1つだけ起動します([`bajutsu/common/runner/pool.py:408`](../../bajutsu/common/runner/pool.py)、[`launch_driver`](../../bajutsu/common/runner/launch.py)経由)。
- パイプラインの`_ScenarioRunner`は、型のレベルでも同じ制約を映しています。実行の中のすべてのシナリオで読み取り専用のまま共有される、1つの`eff: Effective`フィールドを持ちます([`bajutsu/common/runner/pipeline.py:152-165`](../../bajutsu/common/runner/pipeline.py))。

2つの異なるプラットフォームに触れる必要があるシナリオは、1回の実行では実現できません。

[BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation-ja.md)は、この制約が生む回避策をすでに書いています。「両方の顔が必要なチームは、シナリオを二つのターゲットのもとで実行します。」というものです。この回避策には、実行回数が増える以上のコストがあり、理由は2つあります。

- **合否判定が共有されない**：2回の別々の実行は、2つの別々の`RunResult`と2つの別々のレポートを生みます。アプリ側の実行の合格とWeb側の実行の合格を、1つの合否判定に結び付けるものはありません。レビュー担当者は両方を開き、どちらも単独で壊れていないことを確認しなければなりません。
- **状態が共有されない**：`${vars.*}`は、`extract`修飾子([`bajutsu/common/scenario/models/steps/step.py:117`](../../bajutsu/common/scenario/models/steps/step.py))が書き込み、`live_bindings`([`bajutsu/common/orchestrator/loop/_functions.py:697`](../../bajutsu/common/orchestrator/loop/_functions.py))が保持します。この値は、1回の`run_scenario`呼び出しの中でしか生きません。一方のターゲットのステップが捕まえた値(例えば「いいね」操作が作った識別子)は、もう一方のターゲットに対するアサーションに、一切届きません。アプリ側の操作がWeb側に正しい効果を生むこと(あるいはその逆)を確認したいチームは、どのレコードが変化したかを名指しできません。確認できるのは、何らかのカウントが変化したことだけで、それが正しいレコードだという保証はありません。

操作したターゲット自身が正しいエンドポイントを呼んだことだけを確認したいチームには、すでに軽量な手段があります。そのターゲット自身のネットワークトラフィックに対する`request`アサーションです。ただし、この手段は別の問いに答えます。操作したターゲットが正しい呼び出しを*送った*ことは確認できます。しかし、もう一方のプラットフォームのクライアントが、その呼び出しを受け取り、描画し、正しく反映したかどうかは、何も言えません。「アプリとWebの両方を確認したい」チームが確認したいのは、まさにそこです。

実現後に、レビュー担当者が指させる具体的な違いは2つです。

1. iOSターゲットとWebターゲットを`targets`に挙げたシナリオファイルが、1回の`bajutsu run`呼び出しとして実行され、1つの合否判定を生みます。今日は、2つの独立した実行を、レビュー担当者が手作業で突き合わせる必要があります。
2. アプリ側のステップが`extract`で捕まえた値が、同じ実行内で、Web側のターゲットに対するアサーションから`${vars.*}`として読めます。今日は、この値が実行をまたげません。

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

`load_scenario_file`が受け付けるのは、シナリオのトップレベルのリスト、または`{description, scenarios}`のマッピングです。シナリオ自身の`name`から始まる素のマッピングは、受け付けません([`bajutsu/common/scenario/load.py:48-63`](../../bajutsu/common/scenario/load.py)、§6.1)。本項目のシナリオ例が、この例も含めてすべて1件だけのリストになっているのは、そのためです。

上の例は説明のためのものです。iOSターゲットとWebターゲットの間で1つの製品を共有するフィクスチャは、このリポジトリには存在しません。

2つ目の例は、このリポジトリがすでに持っている2つのフィクスチャだけで組み立て、同じ仕組みを実在のシナリオに対して示します。`showcase-swiftui`([`demos/showcase/showcase.config.yaml`](../../demos/showcase/showcase.config.yaml))と`web`([`demos/web/demo.config.yaml`](../../demos/web/demo.config.yaml))は、バックエンドを共有しない、本当に独立した2つのアプリです。したがって、一方のステップが捕まえる値は、もう一方の側では製品としての意味を持ちません。この例が示すのは、実在のidに対して、`targets`、ステップごとの`target`、`${vars.*}`の共有が最後まで動くことです。アプリをまたいだ製品上のチェックを主張するものではありません。

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

iOS側のステップは、`demos/showcase/scenarios/firstlook.yaml`自身の「馬をお気に入りに登録する」流れを踏襲しています。BE-0221がすでに要求している、ドット区切りとアンダースコア区切りのid併記も含みます。簡潔さのために、途中の「まだoffである」ことを確認するアサーション、`preconditions.launchEnv`、`capture`修飾子は省いています。Web側のステップは、`demos/web/scenarios/counter.yaml`自身のオンボーディングフローの冒頭です。

どちらの流れも、今日すでに、それぞれ単独の単一ターゲットシナリオファイルとして動いています。この例が加えているのは、`targets`、`target`、そして両者の間の`extract`/`${vars.*}`による受け渡しだけです。

このファイルを、このリポジトリの現状に対してそのまま実行するには、この例が省いているもう1段階が必要です。`showcase-swiftui`と`web`は、[`demos/showcase/showcase.config.yaml`](../../demos/showcase/showcase.config.yaml)と[`demos/web/demo.config.yaml`](../../demos/web/demo.config.yaml)という、別々の2つの設定ファイルに宣言されています。上の「参加するターゲットを宣言する」の規則は、`scenario.targets`のすべての名前を、その呼び出しで`bajutsu run`が読み込む1つの設定に対して解決します。複数の設定を同時には解決しません。本提案は、2つの設定ファイルを1つにまとめる方法を加えません。

このシナリオを記載どおりに実行するには、`showcase-swiftui`と`web`の両方を`targets.<name>`として並べて宣言する設定が必要です。`demo.config.yaml`の`web`のブロックを、コピーした`showcase.config.yaml`へ貼り付けるだけで十分です。逆の向きでも構いません。どちらのターゲットの設定も、自分だけが存在することに依存していないためです。そのような設定があれば、`bajutsu run --scenario`は両方のターゲットを1回の読み込みから解決できます。

それ以外の点で、この例は変わりません。実在のid、既存の2つのシナリオファイルそれぞれの実際のステップの流れがあり、2つのアプリがデータを共有しているという製品上の主張はしていません。

`Scenario`は、`targets: list[str] = Field(default_factory=list)`を新たに持ちます。既存の`before`、`steps`、`after`と並ぶ位置です([`bajutsu/common/scenario/models/scenario/scenario.py:45-84`](../../bajutsu/common/scenario/models/scenario/scenario.py))。各要素は`targets.<name>`という設定単位を指します。これは、`--target`が今日解決しているのと同じ[ターゲット](../../docs/ja/glossary.md#target-app-device)です。要素には2つの規則があります。

- 宣言された名前は、読み込んだ設定にすでに存在していなければなりません。チェックの方法は、今日の`--target`と同じです。
- `targets`の中で同じ名前を2回宣言すると、黙って重複除去されるのではなく、読み込み時に拒否されます。

`Step`は、`target: str | None = None`を新たに持ちます。このフィールドは、既存の直交した修飾子群に加わります。修飾子群は、現在`("capture", "extract", "name", "from_")`です([`bajutsu/common/scenario/models/steps/_shared.py:8`](../../bajutsu/common/scenario/models/steps/_shared.py))。これらの修飾子は、`Step`の「アクションはちょうど1つ」という規則から除外されています([`bajutsu/common/scenario/models/steps/step.py:136-139`](../../bajutsu/common/scenario/models/steps/step.py))。`target`にも、同じ除外が必要です。

この新しい規則には、専用のバリデータが必要です。`_exactly_one`([`bajutsu/common/scenario/models/_base.py:40`](../../bajutsu/common/scenario/models/_base.py))は`Step`自身の`model_validator`の中で走り、外側のシナリオにアクセスできないからです。新しいバリデータは、`Scenario`のレベルで1回チェックします。新しいフィールドの意味を、`scenario.targets`に対して強制します。

- `targets`が0個か1個なら、`target`は省略するか、その1つの名前と一致していなければなりません。既存の単一ターゲットのシナリオは、変更が要らず、今までどおりに動きます。
- `targets`が2個以上なら、すべてのアクションステップが`target`を明示しなければなりません。「最初に宣言したターゲット」を既定にせず、明示を求めるのは、2つのプラットフォームを行き来するシナリオで、ステップの宛先を暗黙のルールに委ねないためです。読者がそのルールを覚えておく必要はありません。

このバリデータは、`steps`、`before`、`after`の各ルールの`steps`を再帰的に辿ります。`if`と`forEach`が持つ、ネストしたステップリストの中にも入ります。`forEach`の本体の3階層下にあるステップも、トップレベルのステップと同じく、`target`の明示が必要です。

`web`ブロックの中にネストしたステップだけは、例外です。このステップは`target`を自分では一切持たず、指定すると読み込み時にエラーになります。このステップは、囲む`web`ステップがすでに自分の解決済みターゲットに対して開いた`WebContextDriver`ブリッジに対して、常に実行されるからです。今日と同じく、複数のターゲットが存在することを知らずに動きます。

読み込みの後に、このバリデータが見ないステップを追加できる箇所が、2つあります。どちらも、`Scenario.model_validate`を通して新しいオブジェクトを組み立てるのではなく、検証済みの`Scenario`をその場で書き換えます。Pydanticは、ふつうの属性代入に対して`model_validator`を再実行しません。

- `expand_components`([`bajutsu/common/scenario/expand.py:104-114`](../../bajutsu/common/scenario/expand.py))は、`use`ステップをそのコンポーネント自身のステップで置き換えます。そのために`scenario.steps = expand(...)`と代入し、`before`、各`after`ルールの`steps`、各`interrupts`エントリの`steps`にも同様に代入します。代入は、`Scenario`が読み込まれ検証された後に行われます。コンポーネント自身のステップが`target`を省略していても、ほかのステップごとの規則はすべて満たすため、本項目が求める規則だけを静かにすり抜けます。
- `with_lifecycle_phases`([`bajutsu/common/runner/pipeline.py:959-983`](../../bajutsu/common/runner/pipeline.py))は、ターゲットの設定レベルの`before`/`after`フックを、`model_copy`でシナリオへ折り込みます。Pydanticは、このコピーも再検証しません。

本項目は、`target`必須チェックを`Scenario`自身の`model_validator`から素の関数として切り出し、この2つの書き換え箇所がそれぞれ自分の結果に対してもう一度呼び出すことで、両方の抜け穴をふさぎます。`expand_components`がシナリオの処理を終えた直後と、`with_lifecycle_phases`が折り込み済みのコピーを組み立てた直後に、1回ずつ呼びます。再検査は、`target`を持たない裸のステップを含むシナリオファイルがすでに起こしているのと同じ、読み込み時のエラーを起こします。コンポーネントや設定レベルのフックが、`target`のないステップを静かにすり抜けさせることはありません。

`with_lifecycle_phases`自身も、ターゲットごとに呼ぶ必要があります。あるターゲットの`before`/`after`フックは、そのターゲット自身の設定に由来するためです(後述の「宣言したターゲットをまとめて起動し、まとめて後片付けする」を参照してください)。この関数は、再検査が走る前に、折り込む各フックステップへ、そのターゲット自身の名前を刻みます。これにより、設定レベルのフックも、著者が書いたステップと同じく、どのターゲットに対して動くのかが明確になります。再検査は、主に、どちらかの関数を将来呼び出す誰かが、自分の挿入するステップへ名前を刻み忘れた場合への保険として存在します。

`if`、`forEach`、`web`は、種類が違います。`_CONTROL_FLOW_ACTIONS`([`bajutsu/common/scenario/models/_base.py:33`](../../bajutsu/common/scenario/models/_base.py))は、この3つを指します。`_no_modifiers_on_control_flow`([`bajutsu/common/scenario/models/steps/step.py:141-149`](../../bajutsu/common/scenario/models/steps/step.py))は、すでにこの3つを`capture`と`extract`から除外しています。それぞれがネストしたステップリストを包むだけで、自分自身は画面を操作しないからです。(`name`は、この3つでも使えます。)

この3つが`target`を持つ理由は、2種類あります。

- `web`が`target`を持つ理由は、ほかのアクションステップと同じです。そのブロック自身のWebViewブリッジが、どのターゲットのドライバに対して開くかを指定するためです。
- `if`と`forEach`が`target`を持つ理由は、別です。条件そのものを評価するには、いずれかのターゲットの要素ツリーへの問い合わせが必要になるからです。`ForEach`自身が`sel: Selector`フィールドを持つこと([`bajutsu/common/scenario/models/steps/for_each.py:19`](../../bajutsu/common/scenario/models/steps/for_each.py))が、これを裏付けます。この問い合わせは、内側の各ステップが自分自身に設定する`target`とは独立です。

シナリオレベルの`expect`ブロックにも、`steps`と同じ理由で、同じフィールドが必要です。`expect`は、今日、単一のドライバに対する1回の条件待ちポーリングとして評価されます。`_evaluate_expect(driver, expect, network, clock, ctx=...)`([`bajutsu/common/orchestrator/loop/_functions.py:157-171`](../../bajutsu/common/orchestrator/loop/_functions.py))です。これは、`run_scenario`自身の合格経路と、ガード解除後のリトライの両方から呼ばれます([`bajutsu/common/orchestrator/loop/_functions.py:748-760`](../../bajutsu/common/orchestrator/loop/_functions.py)、[`:1076`](../../bajutsu/common/orchestrator/loop/_functions.py))。

そこで、`Assertion`([`bajutsu/common/scenario/models/assertions/assertion.py:23-56`](../../bajutsu/common/scenario/models/assertions/assertion.py))は、`target: str | None = None`を持ちます。このフィールドは、既存の`from_`という来歴フィールドと同じように、`_ASSERTION_KINDS`から除外されます。`Scenario`レベルのバリデータの規則は、そのまま`expect`の各エントリにも及びます。`scenario.targets`が0個か1個なら、`target`は任意です(宣言した1個との一致でも構いません)。2個以上なら必須です。

`_evaluate_expect`は、`expect`を各エントリが指すターゲットごとにまとめます。そして、`_poll_asserts`([`bajutsu/common/orchestrator/loop/_functions.py:115-123`](../../bajutsu/common/orchestrator/loop/_functions.py))を、参照されたターゲットごとに1回ずつ呼び出します。そのとき、そのターゲットの`TargetRuntime`自身のドライバとネットワークソースを使います。結果は、シナリオが宣言した順序どおりに、1つの`expect_results`リストへまとめ直します。

`AssertionResult`([`bajutsu/common/assertions/_common.py:29-38`](../../bajutsu/common/assertions/_common.py))は、既存の`kind`と並んで`target: str = ""`を持ちます。これは、`StepOutcome.target`(後述)と同じ、レポート上の理由から鏡映しにしたものです。インラインの`assert:`の結果は、そのステップ自身の`target`ですでに範囲が決まっています。そのため、このフィールドが意味を持つのは`expect_results`だけです。

`Assertion`にこのフィールドをモデル自体に1回加えると、同じ`target`キーは、`expect`だけでなく、`Assertion`が現れるあらゆる場所で文法上使えるようになります。`Assertion`を持つ場所は、ほかに2つあります。

- `Step`が持つインラインの`assert:`のリスト(`assert_: list[Assertion] | None`、[`bajutsu/common/scenario/models/steps/step.py:92`](../../bajutsu/common/scenario/models/steps/step.py))
- `If`が包む`condition: Assertion`([`bajutsu/common/scenario/models/steps/if_.py:20`](../../bajutsu/common/scenario/models/steps/if_.py))

この2つは、どちらも、それを包むステップ自身の`target`フィールドが指すターゲットに対して、すでに実行されます。そのアサーションの中で`target`を設定しても、同じ値を冗長に繰り返すだけか、最悪の場合はそれと黙って食い違います。

そこで、`Scenario`レベルのバリデータは、上の`target`に関する規則にもう1つの検査を加えます。`steps`、`before`、`after`をこれまでと同じように走査し、インラインの`assert:`のリストか`if`の`condition`を通じて到達した`Assertion`が`target`を少しでも設定していれば、読み込み時に拒否します。`target`を設定できるのは、トップレベルの`expect`ブロックを通じて到達した`Assertion`だけです。

### CLI（コマンドラインインターフェース）：シナリオが自己宣言していれば`--target`は省略できる

シナリオ自身の`targets`が空なら、`--target`は今までどおり必須のままです([`bajutsu/run/cli.py:1293`](../../bajutsu/run/cli.py))。

シナリオの`targets`が空でなければ、そのシナリオは自己宣言しています。`bajutsu run --scenario <file>`は、`--target`なしで、読み込んだ設定から、名指しされたすべてのターゲットを解決します。`--scenario`は繰り返し指定でき、常に個別のファイルを指します。ディレクトリは指しません([`bajutsu/run/cli.py:135-146`](../../bajutsu/run/cli.py))。

`--target`を省略するときは、`--scenario`が最低1つ必要です。自己宣言したシナリオには、頼れる`targets.<name>.scenarios`ディレクトリ([`bajutsu/run/cli.py:149-159`](../../bajutsu/run/cli.py))がないためです。`bajutsu run --target <name>`だけで得られる、このディレクトリの一括読み込みは、1つのターゲットに属します。複数のターゲットを名指しするシナリオは、そのため、常に自分のファイルを明示的に指定します。

そのようなシナリオと一緒に明示的な`--target`を渡した場合は、無視されるのではなく、`scenario.targets`への所属で照合されます。宣言したターゲットのどれか1つと一致すれば一致で、それ以外は不一致です。フラグを無視せずに照合するので、シナリオを編集した後に取り残された古いフラグは、黙って通らず、実行を拒否されます。ファイルがもう期待していないターゲットを、黙って選んでしまうことはありません。

`--target`は呼び出し全体で1つのフラグですが、`--scenario`は繰り返し指定できるため、1回のバッチが複数のファイルを同時に名指しできます。従来のファイル(`targets`が空)には、頼れる自分自身の`target`がありません。この点は、今日と同じです。どのターゲットに対して実行するかを知るには、呼び出し全体の単一の`--target`が必要です。

したがって、従来のファイルを1つでも含むバッチで`--target`を省略すると、そのファイルには解決する手立てが残りません。本項目は、そのようなバッチを読み込み時に拒否します。従来のファイルが1つでもあるのに`--target`がない組み合わせは、エラーです。エラーはそのファイル名を示し、`--target`を渡すか、そのファイル自身に`targets`フィールドを持たせるよう求めます。解決できないまま黙って失敗することはありません。

`--target`を渡しつつ、従来のファイルと自己宣言したファイルを混在させたバッチは、今日の単一ターゲットの規則が述べるとおりに動き続けます。従来のファイルは、今までどおり`--target`を使います。自己宣言したファイルに対しては、上の不一致規則のとおり、そのファイル自身の`scenario.targets`への所属で`--target`を照合します。

このディレクトリの一括読み込みは、`run`が持つ唯一のスイート全体向けの簡便な手段であり、1つのターゲットに属します。そこで、本項目は、この規則を、ディレクトリの配置で守る著者の規律ではなく、実際に強制されるチェックにします。対象となる経路は、`--scenario`を渡さない普通の`bajutsu run --target <name>`が取る、ディレクトリの一括読み込みです。この経路は、発見した時点で、自分の`targets`フィールドが空でないファイルを拒否します。前述の不一致チェックに渡すことも、起動することもしません。拒否は、ファイル名を示し、代わりに`--scenario`を使うよう伝える単純なエラーです。実行そのものが始まる前に起こります。

このチェックがなければ、そのようなファイルがそのディレクトリに置かれた時点で、どちらの結果も望ましくありません。

- `name`がそのファイル自身の宣言したターゲットのどれでもなければ、不一致チェックがそのファイルを拒否します。その拒否は、同じバッチの中のほかのすべてのシナリオまで、道連れに失敗させます。
- `name`がたまたま宣言したターゲットの1つであれば、所属チェックはそのファイルを通過させます。するとバッチは、その1つのターゲットだけでなく、ファイルが宣言するほかのすべてのターゲットまで黙って起動します。呼び出しが名指ししたターゲットを、はるかに超えます。

発見した時点での拒否があれば、自己宣言したシナリオは、明示的な`--scenario <file>`でしか実行できなくなります。どちらの望ましくない結果も、ファイルがツリーのどこに置かれているかに左右されなくなります。自己宣言したシナリオを専用のディレクトリへまとめておくと、ツリーを読む人に分かりやすいという点で、今も良い習慣です。ただし、本項目はもはやそれを正しさの拠り所にしません。

本項目は、Webバックエンドのクロスエンジンマトリクス(`--browsers`、[`bajutsu/run/cli.py:264-307`](../../bajutsu/run/cli.py))と、`--headed`/`--browser`には手を付けません。これらは、今までどおり、実行が持つ唯一のWebプラットフォームターゲットに対して適用され続けます。複数のWebプラットフォームターゲットを名指しする自己宣言シナリオでは、これらのフラグがどちらを指すかを推測せず、そのまま拒否します。マトリクスの軸を複数ターゲットの実行にまで広げるのは、本項目が扱わない追従作業です。

### 宣言したターゲットをまとめて起動し、まとめて後片付けする

1つのターゲット名を1つの`Effective`設定に解決する処理は、すでに名前の付いた手順です。この手順は、`resolve(config, target)`が`config.targets[target]`を参照するだけの処理([`bajutsu/common/config/resolve.py:138-160`](../../bajutsu/common/config/resolve.py))以上のことをします。

この手順に到達する呼び出し元`_load_effective_with_source`([`bajutsu/cli/_shared.py:217-285`](../../bajutsu/cli/_shared.py))は、最後に結果をリベースします。ローカル設定なら`eff.rebased(cfg_path.resolve().parent, confine=False)`、それ以外は`eff.rebased(root)`です([`bajutsu/cli/_shared.py:279-285`](../../bajutsu/cli/_shared.py))。リベースにより、`app_path`/`scenarios`/`baselines`/`schemas`/`goldens`は、呼び出し元の作業ディレクトリではなく、設定ファイル自身のディレクトリを基準に解決されます([`bajutsu/common/config/effective/effective.py:98-108`](../../bajutsu/common/config/effective/effective.py)、BE-0242)。

2つ目に宣言したターゲットを、単なる参照だけで解決すると、その相対パスは誤ったディレクトリを基準に、静かに解決されてしまいます。そこで、本項目のターゲット名ごとの解決は、`_load_effective_with_source`の経路をまるごと再利用します。経路を`scenario.targets`の名前ごとに1回呼び、単なる`resolve()`だけは呼びません。CLIの`_resolve_config_and_engines`([`bajutsu/run/cli.py:264-307`](../../bajutsu/run/cli.py))も、単一の`--target`に対して、すでにこの経路を呼んでいます。

パイプラインの`_ScenarioRunner`は、今日、1つの`eff: Effective`フィールドを持ちます。このオブジェクトは、実行につき1回だけ作られ、すべてのシナリオと`ThreadPoolExecutor`のすべてのワーカーの間で、読み取り専用のまま共有されます。自分自身のdocstringも、まさにこの理由で、「シナリオごとの可変状態を持たない」と述べています([`bajutsu/common/runner/pipeline.py:152-165`](../../bajutsu/common/runner/pipeline.py))。

同じ実行の中の2つのシナリオが、異なる`targets`を宣言することもあります。したがって、ターゲットごとの、リベース済みの`Effective`のマップは、今日の単一の`eff`と同じようには、この共有オブジェクトに置けません。このマップは、`_run_one_impl`([`bajutsu/common/runner/pipeline.py:329-331`](../../bajutsu/common/runner/pipeline.py))の中のローカル変数のままにします。このメソッドは、同じdocstringが述べているとおり、シナリオごとの状態をすでにすべてローカルに保つ場所だからです。

代わりに、`_ScenarioRunner`は、上の解決の経路が参照する、読み込み済みの`Config`を保持する、新しい読み取り専用フィールドを1つ持ちます。`run`のCLI自身は、今日この`Config`を保持していません。`_load_effective_with_source`は、単一の`--target`を解決した後にこれを捨て、`(Effective, source, checkout_root)`だけを返します([`bajutsu/cli/_shared.py:263`](../../bajutsu/cli/_shared.py))。`_resolve_config_and_engines`も、その`Effective`だけを先へ渡します([`bajutsu/run/cli.py:264-307`](../../bajutsu/run/cli.py))。

両方の関数は、自分自身の戻り値に読み込み済みの`Config`を加えます。`Config`は、そこから`run_and_report`([`bajutsu/common/runner/pipeline.py:1176-1178`](../../bajutsu/common/runner/pipeline.py))と`run_all`([`bajutsu/common/runner/pipeline.py:986-987`](../../bajutsu/common/runner/pipeline.py))を経由して配線します。この2つは、今日、CLIの単一の`eff: Effective`しか受け取っていません。`Config`があれば、`_run_one_impl`は、経路を`scenario.targets`の名前ごとに1回呼び、自分のローカルな`dict[str, Effective]`を組み立てられます。

プールが存在するより前に、実行につき1つの`DeviceLease`が、すでに取得されています。これは、本項目がここまで一般化してきた、シナリオごとの`pool.lease()`/`Lease`とは別の、その手前にある概念です。`acquire_device(eff, udid)`([`bajutsu/run/cli.py:1514`](../../bajutsu/run/cli.py))は、ターゲット自身の`eff.device_provider`([`bajutsu/common/config/schema/target_config.py:72`](../../bajutsu/common/config/schema/target_config.py)、`targets.<name>.deviceProvider`、BE-0236)を呼び出します。プロバイダは、未設定なら組み込みの`local`プロバイダ、そうでなければ実際のデバイスクラウドプロバイダです。この呼び出しは、`make_pool`を含む後続のすべての手順がデバイスを解決する元になる、`udid_spec`とプロビジョニングデータを返します。

この呼び出しは、今日、どの実行でも無条件に行われ、1つのターゲットの`eff`を読みます。`deviceProvider`はターゲットごとの設定です。そこで本項目は、プールを構築する前に、宣言した各ターゲットにつき1回、`acquire_device`を呼びます。後片付けの際には、ドライバとプールに並べて、各ターゲット自身の`DeviceLease`を解放します。

宣言した各ターゲットのドライバを立ち上げるには、共有された1つのプールではなく、それぞれ専用のプールが要ります。プールが、今日、プラットフォーム固有だからです。`make_pool`は、1つのプラットフォームのudid一覧から、1組の`pool_actuator`/`pool_env`だけを解決し、そのプラットフォーム自身のコレクタをあらかじめ起動します([`bajutsu/common/runner/pool.py:149-182`](../../bajutsu/common/runner/pool.py))。本項目の見出しの例であるiOSターゲットとWebターゲットには、1つのプールが両方を扱うのではなく、別々に構築された2つのプールが必要です。

起動は、次のように進みます。

- `run_all`は、今日構築している単一のプールの代わりに、シナリオ集合全体で宣言されたターゲットが指す、プラットフォームごとに1つのプールを構築します。構築は今までどおり実行につき1回です。各プールには、前の段落で取得した、そのプラットフォームに属する各ターゲット自身の`DeviceLease`が供給されます。
- `_run_one_impl`は、自分のシナリオが宣言する各ターゲットに合ったプールから、リースを取得します。
- `_run_one_impl`は、解決した`Effective`ごとに、既存の`launch_driver`([`bajutsu/common/runner/launch.py:27-110`](../../bajutsu/common/runner/launch.py))を通じて、ドライバを1つ起動します。これは、プールが今日行っている、シナリオ1回につき1つのリースと起動([`bajutsu/common/runner/pool.py:408`](../../bajutsu/common/runner/pool.py))を、置き換えます。
- 起動結果は、ターゲット名をキーとするローカルな`dict[str, base.Driver]`にまとめます。すべて、最初のステップが走る前に済ませます。

ターゲットを名指しするどのステップも、そのターゲットがすでに起動済みであることを期待します。初めて参照された時点での遅延起動は行いません。本項目が支えようとしている行き来を成立させるためです。

今日、`_run_one_impl`が単一の`self.eff`から導いているものが、いくつかあります。

- `select_actuator_for_scenario`が選ぶアクチュエータ([`bajutsu/common/runner/pool.py:260`](../../bajutsu/common/runner/pool.py))
- ロケール
- `capture`の基準値
- `run_defaults.interrupts`とシナリオ自身の`s.interrupts`の組み合わせ([`bajutsu/common/runner/pipeline.py:913`](../../bajutsu/common/runner/pipeline.py))
- `target_launch_env`として渡す、ターゲット自身の`launchEnv`([`bajutsu/common/runner/pipeline.py:928-932`](../../bajutsu/common/runner/pipeline.py))

これらのどれもが、同じ方法で、宣言したターゲットごとの`Effective`から導かれ、そのドライバと並べてキーになります。

あと5つの引数も、同じ規則に従います。`_run_on_lease`は、今日、1つのリースから`sink`、`relaunch`、`control`、`webview_bridge`、`transitions`を渡しています([`bajutsu/common/runner/pipeline.py:890-907`](../../bajutsu/common/runner/pipeline.py))。それぞれが、自分の来たリース1つに紐づいています。2つ目に宣言したターゲットに対する`web:`ステップは、1つ目のターゲットのものではなく、そのターゲット自身の`webview_bridge`を必要とします。そうでなければ、誤ったアプリのWebViewを黙って開いてしまいます。

次の2つの状態も、同じ規則に従います。

- アラートガードは、今日、`self.alert_guard_for(s)`によってシナリオごとに1回解決されます([`bajutsu/common/runner/pipeline.py:441`](../../bajutsu/common/runner/pipeline.py))。
- ネットワークコレクタは、すでに起動済みのドライバごとに、そのターゲット自身のリースから組み立てられています([`bajutsu/common/runner/pool.py:380-428`](../../bajutsu/common/runner/pool.py))。

`ctx=EvalContext(..., golden=gc_with_screen)`([`bajutsu/common/runner/pipeline.py:875-902`](../../bajutsu/common/runner/pipeline.py))も、別の理由で、同じ一般化が必要です。`self.baselines_dir`/`self.schemas_dir`/`self.golden_context`は、どれも単一の`self.eff`から導かれています。それぞれが指すディレクトリは、`Effective.rebased`がすでにターゲットごとに解決しています(本項目のターゲットごとの解決がすでに運んでいるパスのとおりです)。したがって、2つ目に宣言したターゲットに対する`visual`や`golden`アサーションは、主ターゲットのものではなく、そのターゲット自身のbaselineディレクトリとgoldensディレクトリと比較しなければなりません。

さらに2つのフィールドも、同じように移します。これは、この設計の以前の草案が共有のままにしていたことを、修正するものです。

- **`caps`**：今日は、1つのターゲットのアクチュエータから実行につき1回だけ計算されます([`bajutsu/common/backends.py:194-236`](../../bajutsu/common/backends.py))。どのデバイスもリースする前に、シナリオ*全体*に対して1回だけ検査されます([`bajutsu/common/runner/pipeline.py:373`](../../bajutsu/common/runner/pipeline.py)、BE-0082のフェイルファストな事前検査)。`caps`は、実行の性質ではなく、1つのバックエンドが持つ能力集合の性質です。共有すると、2つ目に宣言したターゲットのステップを、主ターゲットの能力に対して事前検査してしまいます。2つ目のターゲットが対応している構文を誤って拒否したり、対応していない構文を誤って許可したりします。本項目は、`caps`も同じ方法で、宣言したターゲットごとに解決します。事前検査は、宣言したターゲットごとに1回、そのターゲット自身のステップだけと、そのターゲット自身の能力集合に対して実行します。ステップは`step.target`でグループ化します。このグループ化は、本項目の`_evaluate_expect`の変更が`expect`にすでに導入しているものと同じです。
- **`mailbox`**：これも、ランレベルではありません。すでに1つのターゲット自身の設定(`targets.<name>.mailbox`、[`bajutsu/common/config/schema/target_config.py:103`](../../bajutsu/common/config/schema/target_config.py))から来ています。`email`/`totp`ステップは、*実行*の単一のターゲットが設定した受信箱を読みます([`bajutsu/common/orchestrator/loop/_functions.py:281`](../../bajutsu/common/orchestrator/loop/_functions.py))。宣言したターゲットの間で1つの受信箱を共有すると、あるステップのターゲットが別の受信箱を設定している場合に、誤った受信箱をポーリングしてしまいます。

`caps`と`mailbox`は、どちらも、上のほかのターゲットごとのフィールドと同じく、`_ScenarioRunner`にとどまるのではなく、`TargetRuntime`に加わります。

ランレベルのままにするのは、`redactor`だけです。`redactor`は、どのターゲットの証跡をredactionする場合にも、宣言した各ターゲット自身の`secrets`の和集合を適用します。あるターゲットの設定が秘密として指定した値は、そのターゲット自身の証跡だけからではなく、どこからでも取り除かれます。redactionの対象を広げることは、狭めることより厳密に安全です。この点は、あるターゲット自身の値を共有すると別のターゲットに対して誤った答えを生む`caps`や`mailbox`とは異なります。

`with_lifecycle_phases`([`bajutsu/common/runner/pipeline.py:959-983`](../../bajutsu/common/runner/pipeline.py))は、実行が始まる前に、ターゲットの設定レベルのフックをシナリオへ折り込みます。`before`は設定、シナリオの順に折り込み、`after`はシナリオ、設定の順に折り込みます。今日は、CLIの単一の`eff`から、実行につき1回だけ呼ばれます(BE-0392)。本項目は、宣言したターゲットごとに、そのターゲット自身の`eff`で1回ずつ呼びます。これにより、宣言した各ターゲット自身の`targets.<name>.before`/`after`フックが、主ターゲットのものだけでなく、それぞれに対して折り込まれます。

複数のターゲットのフックを、シナリオの単一の`before`/`after`リストへまとめるには、明示的な順序が必要です。本項目は、次の順序を加えます。

- 宣言した各ターゲット自身の`before`フックは、シナリオ自身の`before`ステップより前に折り込みます。ターゲット同士の間では、`scenario.targets`の宣言順です。これは、今日の単一の「設定してからシナリオ」の順序を踏襲します。
- `after`は、その逆順です。シナリオ自身のステップの後片付けが先に来て、その後に、宣言した各ターゲット自身の`after`フックが、同じ宣言順で続きます。これは、今日の「シナリオしてから設定」の順序を踏襲します。

折り込む各フックステップには、自分自身のターゲットの名前を刻みます。これは、前節の再検査バリデータがすでに求めていることです。同じシナリオに折り込まれた、アプリ側の`erase`フックとWeb側のフックは、それぞれ正しいターゲットに対して動き続けます。

`preconditions`と`permissions`には、このようなターゲットごとの分割は要りません。どちらも、今日すでにシナリオレベルのフィールドです(`scenario.preconditions`、`scenario.permissions`)。どちらも`launch_driver`([`bajutsu/common/runner/launch.py:27-98`](../../bajutsu/common/runner/launch.py))へ1回だけ渡され、すでに、iOS固有ではなくバックエンドを問いません。`env.start`は、iOSの`Preconditions`フィールド(erase、reinstall)を、simctlのライフサイクルを通じて解釈します。Webターゲット自身の`env.start`は、代わりに新しいブラウザコンテキストを通じて解釈します。`--target ios`と`--target web`の実行が、1つのバックエンドずつ、今日すでに行っている違いそのものです。

本項目のターゲットごとの`launch_driver`呼び出しは、同じ`scenario.preconditions`と`scenario.permissions`を、宣言した各ターゲット自身の呼び出しへ渡します。各ターゲット自身のバックエンドは、自分に当てはまる部分だけを解釈し、残りを無視し続けます。これは、本項目が新たに発明する能力ではありません。単一ターゲットの実行が、`--target`が`Preconditions`のあるフィールドの当てはまらないバックエンドを指すたびに、すでに頼っている仕組みです。

宣言したターゲットごとに1つのリースを取得するには、本項目が明示的に加えるルールが、もう1つ必要です。`pool.lease()`は、実行のudidを入れたキューに対して、`free.get()`でブロックします([`bajutsu/common/runner/pool.py:163-165, 255`](../../bajutsu/common/runner/pool.py))。デバイスに紐づくN個のターゲットを、シナリオがリースするとします。リース先は、前の段落のとおり、最大N個の異なるプラットフォームごとのプールです。そのシナリオが`--workers`によって複数同時に走ると、飢餓状態やデッドロックになり得ます。どのワーカーも、1つのプールから1台のデバイスを保持したまま、別のプールから次の1台を取得しようとして、永遠にブロックしてしまうからです。

「1回でまとめて確保する」と言うだけでは、実際のプロトコルが伴わない限り意味を持ちません。`pool.lease()`にも、独立した2つ目のプールオブジェクトにも、両者にまたがるトランザクションは用意されていないからです。そこで、本項目の起動処理は、シナリオが必要とするすべてのプールを、決まった一意のキー(宣言されたプラットフォーム名をソートしたもの)の順に並べます。どのワーカーも、例外なく、その順序どおりに厳密に取得します。これは、標準的なロック順序付けの原則です。この段落で扱っているあらゆるデッドロックの形である、循環待ちを排除します。

順序の次のプールを取得できないワーカーは、その1つのプールのキューだけでブロックし、決まった順序でそれより前のプールだけを保持し続けます。どのワーカーも同じ順序ですべてのプールに近づくので、互いが次に必要とするプールを互いに保持してしまうことは起こりません。ワーカーは、自分の順序の後方にあるプールがタイムアウトした場合、一部だけを無期限に保持し続けるのではなく、すでに保持しているものをすべて解放します。

後片付けは、今日、1回の起動が1つのシナリオを包んでいるのと同じ形で、宣言した集合全体を包みます。

- 途中で起動が失敗した場合は、その時点までに起動できていたドライバをすべて後片付けしてから、失敗を伝えます。これは、`env.start`の後に準備確認が失敗した場合の`launch_driver`自身のガード([`bajutsu/common/runner/launch.py:100-108`](../../bajutsu/common/runner/launch.py))と同じ考え方です。
- 実行の終わりには、`_run_on_lease`の`finally: lz.release()`([`bajutsu/common/runner/pipeline.py:955-956`](../../bajutsu/common/runner/pipeline.py))が今日1つのリースを後片付けしているのと同じ方法で、すべてのドライバを後片付けします。その解放を、宣言したターゲットのリースごとに1回呼びます。

### ステップをそのドライバへ振り分け、`${vars.*}`を全ターゲットで共有する

`run_scenario`の既存のフラットなキーワードパラメータは、まとめて見ると、今日パイプラインが1つのターゲットの1つのリースから束ねているもののすべてです(前節の一覧のとおりです)。

そのパラメータは、`driver`、`sink`、`alert_guard`、`network`、`relaunch`、`control`、`ctx`、`mailbox`、`webview_bridge`、`transitions`、`interrupts`、`locale`、`capture`、`channel`、`target_launch_env`です([`bajutsu/common/orchestrator/loop/_functions.py:572-594`](../../bajutsu/common/orchestrator/loop/_functions.py))。

`target`を持たないステップは、今までどおり、これらすべてを読み続けます。これらは`run_scenario`の主ターゲットのランタイムのままで、形は変わらないため、既存の呼び出し元は影響を受けません。

`run_scenario`は、これらの各フィールドをターゲットごとのマッピングへその場で広げる代わりに、それ以外の宣言済みターゲットのために、新しいパラメータを1つ持ちます。`target_runtimes: Mapping[str, TargetRuntime] | None = None`です。`TargetRuntime`は新しい小さなデータクラスで、同じフィールドの並びに加えて`caps`も束ねます。

`caps`は、ほかのフィールドと違い、`run_scenario`自身のキーワード引数として渡されることはありません。それを参照する事前検査は、`run_scenario`を呼ぶより前の`_run_one_impl`の中で走るからです。ただし、`caps`も宣言したターゲットごとに同じ方法で解決されます。そのため、別の入れ物を新設せず、ほかのフィールドと並べて各ターゲット自身の`TargetRuntime`に載せて運びます。`_run_one_impl`は、追加で宣言したターゲット1つにつき1つの`TargetRuntime`を、前節でドライバのマップを組み立てているのと同じ方法で組み立てます。

`_StepRunner`の各メソッドは、すでに`active_driver: base.Driver`という明示的な引数を、共有された状態から読む代わりに、呼び出しのたびに受け取っています([`bajutsu/common/orchestrator/loop/_step_runner.py:78-118`](../../bajutsu/common/orchestrator/loop/_step_runner.py))。ステップの種類の1つである`web:`ブロックは、すでにこの方法で、自分のネストしたステップ用に別のドライバへ切り替えています。`WebContextDriver`を作り、`exec_steps(step.web.steps, web_driver)`へ再帰し、その後で外側の`active_driver`に制御を戻します([`bajutsu/common/orchestrator/loop/_step_runner.py:242-293`](../../bajutsu/common/orchestrator/loop/_step_runner.py))。

この切り替えは、すでに起動済みの1つのアプリの、プロセス内WebViewブリッジに閉じたものです。独立して起動した2つ目のターゲットではありません。ただし、ループの基本部品が、1つの共有オブジェクトから読む代わりに、ターゲットごとの状態を呼び出しのたびに明示的に渡していることは、裏付けられます。これは、まさに、`target`をキーにした参照が必要とする形です。

`_run_one`([`bajutsu/common/orchestrator/loop/_step_runner.py:89`](../../bajutsu/common/orchestrator/loop/_step_runner.py))は、あるステップを支配する`TargetRuntime`(主ターゲットのものか、`target_runtimes`の1つ)を、`step.target`を調べて決めます。ステップのドライバは、今日と同じく、`active_driver`を通じて振り分けます。主ターゲット自身の`driver`パラメータは使いません。`web:`ブロックの中にネストしたステップにとって、この違いが重要です。そのステップの`active_driver`は、すでにブロック自身の`WebContextDriver`になっています。それを飛び越えて主ターゲットの`driver`まで戻ると、誤ったアプリの画面に対してステップを実行してしまいます。トップレベルの各ステップの`active_driver`は、最初、自分が解決した`TargetRuntime.driver`そのものです。したがって、この振り分けは、今日の唯一のケースに対する挙動を変えるものではなく、その厳密な一般化です。

ターゲットをまたいで`${vars.*}`を共有するのに、新しい配線は要りません。`live_bindings`は、すでに1つのプレーンな`dict[str, str]`です。`run_scenario`の呼び出しごとに1回だけ作られ、`before`、`steps`、`after`のどの段階からもクロージャとして共有されています([`bajutsu/common/orchestrator/loop/_functions.py:695-697`](../../bajutsu/common/orchestrator/loop/_functions.py))。特定のドライバには紐づいていません。あるターゲットに対する`extract`ステップは、別のターゲットに対するアサーションがすでに読んでいるのと同じ辞書に、書き込みます。この仕組みは、単一のドライバを前提にしていません。前提にしているのは、1回の`run_scenario`呼び出しがこの辞書を1回だけ組み立てることであり、それは変わりません。

### レポート：どのターゲットが各ステップを生んだかを記録する

`StepOutcome`は、既存の`action: str`([`bajutsu/common/orchestrator/types/step_outcome.py:15-44`](../../bajutsu/common/orchestrator/types/step_outcome.py))に加えて、`target: str = ""`を持ちます。値は、3つの場合に分かれます。

- シナリオ自身の`targets`が空なら、空文字列です。
- ステップが`target`を明示していれば、その宣言名です。
- `scenario.targets`がちょうど1個で、ステップが(前述のバリデータのとおり)`target`を省略している場合は、その1個の宣言名です。自己宣言したシナリオが、唯一のターゲットに曖昧さがないというだけの理由で、レポートで空白になることはありません。

これは、`RunResult.engine`が単一エンジンの実行に対してすでに使っている、「空は該当なしを意味する」という慣習と同じです([`bajutsu/common/orchestrator/types/run_result.py:26-30`](../../bajutsu/common/orchestrator/types/run_result.py))。

`RunResult`自身の`backend`、`device`、`device_name`、`device_runtime`([`bajutsu/common/orchestrator/types/run_result.py:25-40`](../../bajutsu/common/orchestrator/types/run_result.py))は、それぞれちょうど1つのターゲットを表します。単一ターゲットの実行に対しては、今までどおり意味を持ちます。複数ターゲットの実行では、この同じ情報が、シナリオ全体で1回ではなく、宣言したターゲットの数だけ必要です。

そこで、`RunResult`は`target_devices: dict[str, TargetDeviceInfo]`も持ちます。ターゲット名をキーにします。`TargetDeviceInfo`は、`backend`/`engine`/`device`/`device_name`/`device_runtime`を運ぶ、新しい小さなデータクラスです。

既存の単数形フィールドは、複数ターゲットの実行では、空のままにします。どれか1つのターゲットの値を、ほかに優先して報告することはしません。これは、`StepOutcome.target`に適用した「空は該当なしを意味する」慣習を、`RunResult`自身についても反転させず、一貫させるものです。

今日の`RunResult`の形に合わせて書かれた既存の読み手(JUnitやCommon Test Report Format(CTRF)のエクスポートを含みます)は、単一ターゲットの場合には今までどおりの値を見ます。複数ターゲットの実行では、空の値を見ます。ある1つのターゲットの値が、シナリオ全体を代表するかのように示されることはありません。

レポートのStepsビューは、各ステップのアクションの隣にターゲット名を表示します。新しいヘッダーブロックは、宣言した各ターゲットのデバイスを、それぞれの証跡と並べて一覧します。ターゲットを何も宣言しないシナリオでは、既存の単一デバイスのヘッダーが、そのまま残ります。

### この提案が実装に残す1つの未決事項

この設計の以前の草案が未決のままにしていた、ほかの共有状態は、この設計の中ですでに解決されているか、そもそも解決の必要がないと確認できました。

- `_ScenarioRunner`の`run_dir`は、`runs_dir / run_id`から導かれます([`bajutsu/common/runner/pipeline.py:1223`](../../bajutsu/common/runner/pipeline.py))。どのターゲットの`Effective`からも導かれたことのない、ラン全体のアーティファクトディレクトリです。したがって、ターゲットごとの一般化は必要ありません。
- `udid_spec`、`actuator`、`baselines_dir`、`schemas_dir`、`golden_context`は、上の「宣言したターゲットをまとめて起動し、まとめて後片付けする」が、`caps`や`mailbox`と並んで、宣言したターゲットごとに解決しているフィールドそのものです。
- 任意のシナリオごとの`resolve_actuator`コールバック(BE-0240、[`bajutsu/common/runner/pipeline.py:190`](../../bajutsu/common/runner/pipeline.py))は、黙って先送りするのではなく、明示的にスコープ外とします。これを渡す呼び出し元は、本項目が一般化するプールベースのアクチュエータ選択から、すでに外れています。シナリオ全体で1つのアクチュエータを返す代わりに、宣言したターゲットごとに1つのアクチュエータを解決できるようにする拡張は、`resolve_actuator`と複数ターゲットのシナリオを両方必要とする、将来の項目に委ねます。

未決の問いが1つだけ残ります。`gc_with_screen`([`bajutsu/common/runner/pipeline.py:875-884`](../../bajutsu/common/runner/pipeline.py))は、`golden`のフレーム健全性チェック(BE-0006)のために、1つのドライバの画面サイズを調べます。iOSターゲットとWebターゲットは、一般に画面のジオメトリが異なります。しかも、今日は1つの`GoldenContext`しか`run_scenario`に届きません。したがって、2つ目に宣言したターゲットに対する`golden`アサーションの挙動は、未決です。本提案が見出した形は、2つあります。ターゲットごとの`GoldenContext`を持つか、`golden`を1つの宣言したターゲットに限定するかです。どちらを選ぶかは、推測せずに先送りします。

### 作業分解(MECE)

1. **スキーマ**：
   - `Scenario.targets: list[str]`。重複した名前は読み込み時に拒否します。
   - `Step.target: str | None`。
   - `Assertion.target: str | None`。`from_`と同様に、`_ASSERTION_KINDS`から除外します。
   - `target`の要否を`len(scenario.targets)`に紐づける、`Scenario`レベルのバリデータ。`Step`自身のバリデータは、外側のシナリオを見られないため使えません。このバリデータは、`steps`、`before`、`after`の各ルールの`steps`を、`if`/`forEach`のネストしたステップリストにも再帰的に辿ります。`web:`ブロックにネストしたステップだけは例外で、`target`を宣言するのではなく省略するよう求めます。そのステップは、囲んでいる`web:`ステップがすでに解決したターゲットに対して、常に実行されるからです。
   - 同じ要否の規則を`expect`にも適用します。インラインの`assert:`リストや`if`の`condition`を通じて到達した(`expect`を経由しない)`Assertion`が`target`を設定していれば、それも拒否します。
   - この検査全体を素の関数として切り出し、`expand_components`と`with_lifecycle_phases`がそれぞれ自分の結果に対してもう一度呼び出します。どちらも、検証済みの`Scenario`をその場で書き換えます。Pydanticは、ふつうの属性代入や`model_copy`に対して`model_validator`を再実行しません。
2. **CLI**：
   - `scenario.targets`が空でなければ`--target`を省略可能にし、代わりに`--scenario`を必須にします。
   - 明示的な`--target`は、黙って上書きせず、`scenario.targets`と照合します。
   - シナリオファイルのローダーは、宣言された各名前を、実行が始まる前に、読み込んだ設定に対して検証します。
   - `--target`だけを渡すディレクトリの一括読み込み経路は、発見した時点で、自分の`targets`フィールドが空でないファイルを拒否します。自己宣言したシナリオを、不一致チェックに渡したり起動したりしません。
   - `--target`を省略したバッチに、従来のファイル(`targets`が空)が1つでも含まれていれば、読み込み時に拒否します。エラーは、`--target`を解決する手立てのないそのファイル名を示します。
3. **起動と後片付け**：
   - 宣言したターゲットごとに、どのプールが存在するより前に`DeviceLease`を1つ取得し、後片付けの際にドライバとプールに並べて解放します。
   - `_load_effective_with_source`と`_resolve_config_and_engines`が、すでに返している`Effective`と並べて、読み込んだ`Config`も返すようにします。`Config`を、`run`のCLIから`run_and_report`と`run_all`を経由して、`_ScenarioRunner`の新しい読み取り専用フィールドまで配線します。どれも、今日は`Config`を受け取っていません。
   - `run_all`は、今日の単一プールの代わりに、宣言されたターゲットが指すプラットフォームごとに1つのプールを構築します。各プールには、そのプラットフォームに属する各ターゲット自身の`DeviceLease`を供給します。
   - `_run_one_impl`は、宣言したターゲット名ごとに1つの、リベース済みの`Effective`を、ローカルなマップへ解決します。マップは、共有される`_ScenarioRunner`には保存しません。
   - シナリオが必要とするすべてのプールを、決まった一意の順序(宣言されたプラットフォーム名をソートしたもの)で取得します。ターゲットごとに任意の順で`pool.lease()`を1つずつ呼ぶことはしません。この標準的なロック順序付けにより、どの2つのワーカーも互いの次のプールを取得できずにデッドロックすることがなくなります。順序の後方にあるプールがタイムアウトした場合は、すでに保持しているものをすべて解放します。
   - 名前ごとに1回`launch_driver`を呼び出し、ローカルな`dict[str, base.Driver]`にまとめます。すべて最初のステップより前に済ませます。どの呼び出しにも、シナリオ自身の変更しない`preconditions`/`permissions`を渡します。
   - `with_lifecycle_phases`を、宣言したターゲットごとに、そのターゲット自身の`eff`で1回ずつ呼びます。折り込む各フックステップに、そのターゲット自身の名前を刻みます。宣言したターゲットのフックを、宣言順でシナリオの`before`/`after`へまとめます。
   - 途中の起動失敗は、その時点までに起動できたドライバをすべて後片付けします。実行の終わりは、集合全体を後片付けします。
4. **ランナー**：
   - 新しい`TargetRuntime`データクラス。`run_scenario`が今日1つのリースから束ねているすべての引数(`driver`、`sink`、`alert_guard`、`network`、`relaunch`、`control`、`ctx`、`mailbox`、`webview_bridge`、`transitions`、`interrupts`、`locale`、`capture`、`channel`、`target_launch_env`)に加えて、`caps`も束ねます。`caps`は、同じ方法でターゲットごとに解決しますが、`run_scenario`を呼ぶより前の`_run_one_impl`内の事前検査が直接参照するため、`run_scenario`へは渡しません。
   - `run_scenario`の新しい`target_runtimes: Mapping[str, TargetRuntime] | None`パラメータ。主ターゲット向けの、変更しないフラットなパラメータと並びます。
   - `_run_one`のステップ振り分け。`step.target`を、主ターゲットの値ではなく、`active_driver`/`target_runtimes`に対して解決します。
   - `_run_one_impl`は、宣言したターゲットごとに1つの`TargetRuntime`を組み立てます。今日、1組のリースごとの引数がシナリオごとに1回組み立てられているのと、同じ方法です。
   - `_evaluate_expect`は、`expect`をターゲットごとにまとめます。参照されたターゲットごとに、そのターゲット自身のドライバとネットワークソースで`_poll_asserts`を1回呼び出し、結果を宣言順にまとめ直します。
5. **レポート**：
   - `StepOutcome.target`。宣言されたターゲットが1個の場合の既定値を含みます。
   - `AssertionResult.target`。`expect_results`だけに向けて、これを鏡映しします。インラインの`assert:`の結果は、引き続きそのステップ自身の`target`で範囲が決まります。
   - 複数ターゲットの実行で空のままになる、`RunResult`の単数形フィールド。
   - `RunResult.target_devices`。
   - Stepsビューのターゲットラベル。
   - 宣言した各ターゲットのデバイスを一覧するヘッダーブロック。
6. **ドキュメント**：`docs/scenarios.md`(`targets`/`target`のリファレンスと実例)、`docs/cli.md`(`--target`の新しい省略条件と`--scenario`の新しい必須条件)、`docs/run-loop.md`(複数ドライバのステップ振り分け)、それぞれの`docs/ja/`ミラー。
7. **テスト**：
   - スキーマ：`targets`が0個・1個・2個以上のそれぞれでの`target`必須バリデータを、`steps`/`before`/`after`/`expect`と、`if`/`forEach`/`web`のネストした形すべてに対して検証します。重複名の拒否、`expect`の外での`Assertion.target`の拒否、`expand_components`と`with_lifecycle_phases`を経ても再検査が生き残ることも検証します。
   - CLI：`--target`の省略・必須の切り替え、不一致の拒否、自己宣言したファイルのディレクトリ一括読み込みでの拒否、従来のファイルと自己宣言したファイルが混在するバッチの拒否を検証します。
   - 起動：複数ターゲットの集合の途中の起動失敗が、起動済みのものを後片付けすることを検証します。ロック順序付けによる取得が、同じ2つのプラットフォームを逆順に必要とする2つのワーカー間で、デッドロックしないことも検証します。ターゲットごとの`caps`事前検査が、主ターゲットにしかない構文だけを拒否すること、ターゲットごとの`mailbox`選択も検証します。
   - ランナー：1つのシナリオの中でのターゲット混在ステップ振り分け(隣接する2つのブロックだけでなく、行き来すること)、ターゲットをまたいだ`${vars.*}`共有、`expect`が宣言順でまとめ直されることを検証します。
   - レポート：複数ターゲットの`RunResult`で、単数形フィールドが空のまま`target_devices`が埋まることを検証します。既存のJUnit/CTRFの読み手が、単一ターゲットの実行を今までと変わらず解釈できることも検証します。

## 検討した代替案

- **アクターとベリファイアの役割分担**：本項目の初期案です。1つのターゲットがシナリオの`steps`を実行し、もう1つが後から独立した`verify`ブロックを実行する形でした。却下した理由は、1つのシナリオにつき、一方向の受け渡しを1回しか表現できないことです。あるターゲットを操作し、別のターゲットで確認し、また最初のターゲットを操作する、という行き来を表現できません。実際のクロスターゲットのテストには、この行き来が必要です。Web側の編集がアプリに届くケースは、アプリ側の操作がWebに届くケースと、ちょうど同じくらいよくあるからです。
- **専用の`switchTarget`ステップ**：[BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation-ja.md)が、デバイスモードの切り替え用にすでに検討し、却下した`setViewport`/`emulate`ステップに似ています。同じ理由で却下します。切り替えステップは、経路の指定を、読み手がステップ列全体を通して追わなければならない命令的なアクションに変えてしまいます。`target`修飾子であれば、それが効くステップ自身に経路の指定が見える形を保てます。`capture`や`extract`が、先行するコマンドではなく修飾するステップそのものに、自分の効果を見せているのと同じです。
- **ネットワークだけを見るクロスターゲットのアサーション**：2つ目の生きたドライバを一切持たず、操作対象自身の`request`アサーションだけで、もう一方のプラットフォームのAPI(アプリケーションプログラミングインターフェース)を正しい内容で呼んだことを確認する案です。これは、今日すでにできることであり、そこまでで十分なチーム向けの軽量な選択肢として残します。唯一の手段としては却下します。本項目の動機が挙げている問いとは別の問いに答えるからです。操作対象が正しい呼び出しを送ったことは確認できます。しかし、もう一方のプラットフォームのクライアントがそれを受け取り、正しく描画したかどうかには答えません。「もう一方のプラットフォームで結果を確認する」とは、そういう意味です。
- **2つの連結したシナリオファイル**：一方のファイルが捕まえた`${vars.*}`を、別のターゲットに対して実行するもう一方のファイルへ渡す、新しいランナーのフラグを想定した案です。却下します。1つの論理的なテストを2つのファイルに分けると、`preconditions`、`before`、`after`が両方に重複します。しかも行き来を表現できません。最初のファイルが完全に終わってから2つ目が始まるので、アプリからWeb、またアプリへ、というテストには3つ目のファイルが必要になります。
- **`--target`を複数指定可能にする案**：`--scenario`がすでに持つ複数指定可能な形([`bajutsu/run/cli.py:1294-1303`](../../bajutsu/run/cli.py))に倣い、`list[str]`にする案です。シナリオ側の`targets`フィールドの代わりにこちらを採用することは、却下します。あるシナリオがどのプラットフォームを行き来するかは、シナリオ自身が持つ性質であり、呼び出しごとの選択ではありません。`bajutsu run --scenario a.yaml b.yaml ...`のように、複数ファイルを渡すCIジョブは、その呼び出しの中のどのファイルがどのターゲットを必要とするかに、`--target`のリストを常に同期させ続けなければなりません。あるファイルの`targets`が変わった瞬間に、呼び出しのフラグとずれてしまいます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] スキーマ：`Scenario.targets`、`Step.target`、`Assertion.target`、`target`の要否を
      `len(scenario.targets)`に`steps`と`expect`の両方で紐づけるバリデータ。
- [x] CLI：自己宣言したシナリオのもとでの`--target`の省略、不一致の拒否、宣言された各名前の設定に
      対する検証。
- [x] 起動と後片付け：`_ScenarioRunner`まで配線したターゲットごとの解決済み設定、宣言したバックエンド
      ごとの1つのプール、宣言したターゲットごとに1つのドライバの、まとめての起動とまとめての後片付け。
- [x] ランナー：`TargetRuntime`のまとまり、`run_scenario`の`target_runtimes`マッピング、ターゲット
      ごとのアクチュエータ/ロケール/`capture`/`interrupts`/ガード/ネットワーク/証跡コンテキストの
      組み立て、`_evaluate_expect`のターゲットごとのグループ化。
- [x] レポート：`StepOutcome.target`、`AssertionResult.target`、`RunResult.target_devices`、それらを
      表示するレポートの画面。
- [x] ドキュメント：`docs/scenarios.md`、`docs/cli.md`、`docs/run-loop.md`、それぞれの`docs/ja/`
      ミラー。
- [x] テスト：スキーマのバリデータ（`expand_components`/`with_lifecycle_phases`の再検査を含む）、
      CLIの選択・拒否の規則、複数プールのリース・デッドロック網羅、ターゲットごとの
      caps/mailbox/事前検査、ターゲット混在のステップ振り分け、レポートの後方互換性。

ログ：

- [#2025](https://github.com/bajutsu-e2e/bajutsu/pull/2025) — 単位1（スキーマ）です。
  `Scenario.targets`、`Step.target`、`Assertion.target`を追加し、`target`の有無を
  `len(scenario.targets)`に紐づけるバリデータを実装しました。`steps`/`before`/`after`/
  `interrupts`と、あらゆる`if`/`forEach`/`web`の入れ子を再帰的に辿ります。設計が触れていなかった
  抜け穴も塞ぎました。`apply_setups`は、`expand_components`や`with_lifecycle_phases`と並ぶ
  第3の変更点です。Pydantic が `model_validator` を再実行しない形で、検証済みの`Scenario`を
  作り直します。この3つすべてが、自身の結果に対してチェックを再実行するようになりました。設計が
  未解決のまま残した2つの問題は、推測せずに拒否する形で解決しました。`use:`ステップは、
  コンポーネント展開が自身の`target`を捨ててしまいます。空でない`interrupts`は、`condition`に
  自身がポーリングするターゲットがありません。どちらも、シナリオが2つ以上のターゲットを宣言すると
  拒否され、解決は後続のユニットに委ねます。複数のライブなターゲットへステップを振り分ける仕組みは
  まだありません。そのため、そうしたシナリオを実行・生成しようとするすべての呼び出し元がこれを
  拒否します。`run_all`（`run`と`audit --repeat`が共有）、`codegen`、serve自身のCodegen
  エンドポイントです。間違った対象に対して、黙って作用することはありません。

- [#2028](https://github.com/bajutsu-e2e/bajutsu/pull/2028) — 単位2〜7（CLI、起動と後片付け、ランナー、レポート、ドキュメント、テスト）です。
  `--scenario`のファイルがすべて自分の`targets`を宣言していれば、`--target`は省略できるように
  なりました。宣言された各名前は同じ読み込み済み config から解決され、自分の`DeviceLease`とプールを
  与えられ、最初のステップより前に起動します。ステップループは、共有された1つの`StepLoopState`の上で
  各ステップを自分のターゲットのドライバと証跡へ振り分けます。採番も`${vars.*}`の辞書も判定も1つずつ
  で、すべてのターゲットにまたがります。設計からの逸脱が3つあり、いずれも意図的なものです。

  - **パイプラインは解決済みのターゲット表を受け取ります。** 読み込み済みの`Config`ではありません。
    設計では`Config`を`_ScenarioRunner`まで配線し、シナリオごとに`_load_effective_with_source`を
    再実行する予定でした。代わりに CLI が、その同じ経路で（つまり rebase も同一のまま）宣言された各名前を一度
    だけ解決し、読み取り専用の`Mapping[str, TargetPool]`を渡します。ランナーに CLI 層の import を
    持ち込まずに済み、Git 由来の config をシナリオ×ターゲットの回数だけ再取得することも避けられます。
    表は名前をキーにするため、別々の部分集合を宣言する2つのシナリオも、それぞれ自分のものを選べます。
  - **プールのキーはプラットフォームではなくアクチュエータです。** `device_pool`は解決済みの
    アクチュエータから環境を組み立てます。プラットフォームをキーにすると、同じプラットフォームの2つの
    アクチュエータに、組み立て方の誤ったプールを渡してしまいます。アクチュエータをキーにすれば、
    プラットフォームのキーが分ける組み合わせをすべて分けたうえで、さらにこの1組も分けられます。
    ロック順序に従うリース取得も、同じキーで整列します。
  - **`golden`の未決問題は、ターゲットごとのコンテキストで解決しました。** 各ターゲットの
    `EvalContext`は、golden のフレーム妥当性のために*自分自身の*ドライバの画面境界を調べます。設定が
    指定していれば、自分の`baselines` / `schemas` / `goldens`のディレクトリも読みます。web ターゲットの
    golden を iOS ターゲットの画面形状で判定すると、正しく撮られた golden を落としてしまいます。
    `golden`を1つの宣言済みターゲットに限る案では、それを避けられませんでした。

  単位1が記録した2つの拒否は、拒否のまま残しています。2つ以上のターゲットを宣言したときの`use:`
  ステップと、空でない`interrupts`です。どちらもこのアイテムにまだ答えのない決定を要し、どちらも、
  このアイテムが実現しようとしている組み合わせ実行を妨げません。クロスブラウザマトリックス
  （`--browsers`）も、*詳細設計*のとおり単一ターゲットのままです。複数ターゲットのシナリオと
  組み合わせた実行は、推測せずに拒否します。設計が想定していなかった制限が1つあります。同じ
  バックエンドの2つのターゲットは、シナリオの全長にわたってそのバックエンドのデバイスキューを共有
  します。そのため`run`は、シナリオの必要数に満たないプールを拒み、`--workers`もプールが賄える範囲
  まで抑えます。空きが永久に出ないキューで待ち続ける代わりです。

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

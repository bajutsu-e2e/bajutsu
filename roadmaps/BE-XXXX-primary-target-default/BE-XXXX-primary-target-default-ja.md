[English](BE-XXXX-primary-target-default.md) · **日本語**

# BE-XXXX — primary target: 複数ターゲットシナリオのステップで `target` を省略できるようにする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-primary-target-default-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | シナリオ記述機能 |
| 関連 | [BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[複数ターゲットシナリオ](../../docs/ja/scenarios.md)
([BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution-ja.md))
に、任意のトップレベルフィールド `primaryTarget` を追加します。値には、そのシナリオ自身の
`targets` リストに含まれる名前を1つ指定します。シナリオが `primaryTarget` を設定すると、ステップ、
`if` / `forEach` / `web` / `app` のラッパー、トップレベルの `expect` エントリのいずれも、
単一ターゲットのシナリオで今までできていたのと同じように `target` を再び省略できます。省略した
`target` は、読み込みエラーにはならず、指定した primary target を対象に実行されます。

```yaml
- name: liking a post on the app shows up on the web
  targets: [showcase-app, showcase-web]
  primaryTarget: showcase-app
  steps:
    - tap: { id: post.like }              # target省略: showcase-appで実行される
      extract:
        postId: { sel: { id: post.id } }
    - target: showcase-web
      wait: { for: { id: "post.${vars.postId}.likeCount" }, timeout: 10 }
  expect:
    - target: showcase-web
      value: { sel: { id: "post.${vars.postId}.likeCount" }, equals: "1" }
```

`primaryTarget` は、`targets` の数によらず常に任意です。設定しないシナリオでは、今日のルールが
そのまま残ります。`targets` が2つ以上のとき、すべてのステップと、トップレベルの `expect` エントリ
すべてが、引き続き自分の `target` を明示します。`targets` 自体の仕様は変わりません。`targets` が
0個または1個のシナリオも、どちらの場合もこの提案の影響を受けません。

## 動機

BE-0428 自身のバリデータ `_check_target_requirements`
([`bajutsu/common/scenario/models/scenario/_targets.py:80-143`](../../bajutsu/common/scenario/models/scenario/_targets.py))
は、シナリオが2つ以上のターゲットを宣言すると、すべてのステップとすべての `expect` エントリに
`target` を要求します。このルールは意図的なものです。BE-0428 自身の「検討した代替案」は、
ステップの行き先を暗黙のルールから決める案を退けています。読者に暗黙のルールを覚えさせないためです。
このルールは、ターゲットを何度も切り替え続けるシナリオにはよく合います。一方で、クロスプラット
フォームの確認がよく取る形には、あまり合いません。1つのターゲットがほぼすべての操作を担い、
シナリオは検証のため、あるいは1ステップの結果を渡すためだけに、もう1つのターゲットへ一度だけ
寄り道します。

BE-0428 自身のワーク例が、まさにこの後者の形を示しています
([`docs/scenarios.md:1193-1206`](../../docs/scenarios.md))。1つを除くすべてのステップに、
同じターゲット名が書かれています。読者は `target: showcase-app` を6回読み返して、
シナリオ全体に共通する1つの事実を読み取ることになります。このテストの主戦場は `showcase-app` です。
シナリオが `showcase-web` へ寄り道するのは一度きりです。繰り返されるフィールドは、その事実を
最初の1行で伝えたあと、残り5回は何も足さずに同じことを言い直しています。しかも、本当に重要な
1行(*別の*ターゲットを名指しするステップ)が、同じ文言を繰り返す6行の隣人に埋もれて、
目立たなくなっています。

`primaryTarget` が答える問いは、BE-0428 自身のバリデータが答える問いより狭いものです。
「どのターゲットでステップを実行するか」ではなく、「どのターゲットなら書かなくてよいか」に答えます。
シナリオがその答えを宣言すれば、ステップは自分の `target` を、答えと異なる場合にだけ書けば
済みます。単一ターゲットのシナリオがすでに得ている、`target` を一度も書かずに済む経済性と同じ
ものです。こうして読者は、クロスターゲットシナリオの形を一目で読み取れます。`target` を明示している
ステップこそが、primary target から離れるステップです。それ以外の行を読み直す必要はありません。

## 詳細設計

### `primaryTarget` の宣言

`Scenario` に新しいフィールド `primary_target: str | None = Field(default=None,
alias="primaryTarget")` を追加します。既存の `targets: list[str]`
([`bajutsu/common/scenario/models/scenario/scenario.py:59`](../../bajutsu/common/scenario/models/scenario/scenario.py))
の隣に置きます。新しいチェック関数 `_check_primary_target` が、この値を `scenario.targets` に
照らして検証します。この関数は
[`_targets.py`](../../bajutsu/common/scenario/models/scenario/_targets.py) に置き、
`_check_target_requirements` がステップを1つも歩く前に一度呼び出します。`primaryTarget` を
未設定のままにすることは、常に許されます。`targets` が空なのに設定すると読み込みエラーになります。
メッセージは `primaryTarget is set but the scenario declares no targets` とし、ステップ自身の
`target` が同じ形で不正なときに `_check_target` が出す既存のメッセージに合わせます。`targets` に
含まれない名前を設定した場合も読み込みエラーとし、宣言済みの一覧を示します。`targets` が
1個だけのシナリオでも、一致する `primaryTarget` は冗長として拒否せず受け入れます。単一ターゲットの
シナリオでは、すでにステップがその1つのターゲットを省略せず明示してもよいことになっています
([`docs/scenarios.md:1214-1215`](../../docs/scenarios.md))。`primaryTarget` はその1つの名前を
無害に繰り返すだけです。

### 省略の解決先

`_check_target_requirements` は、`_check_target` という関数を、ステップごとに1回、`expect`
エントリごとに1回呼び出します
([`_targets.py:26-46`](../../bajutsu/common/scenario/models/scenario/_targets.py))。この関数に
`default: str | None` という引数を追加し、戻り値を呼び出し元が受け取るようにします。今日の
呼び出し元は、この戻り値を使っていません。検証を通過したとき、`_check_target` はフィールド自身の
値をそのまま返しているだけだからです。この提案が追加する版は、これまで失敗していた1つの新しい
ケースでだけ `default` を返します。そのケースとは、ターゲットが2つ以上宣言されていて、`target`
が省略されていて、`default` が手元にある状況です。それ以外の分岐は、今日と同じくフィールド
自身の値を返します。
`primaryTarget` を未設定のままにしたシナリオも同じです。そこでは `default` が `None` のままなので、
新しいケースは発火せず、読み込みエラー `target is required` がそのまま発生します。

`_check_target_requirements` は、`_check_target` を呼び出すたびに `scenario.primary_target` を
`default` として渡します。ステップ自身の `target`
([`_targets.py:68`](../../bajutsu/common/scenario/models/scenario/_targets.py)、`_check_step_target`
の内側)と、トップレベルの `expect` エントリの `target`
([`_targets.py:142`](../../bajutsu/common/scenario/models/scenario/_targets.py))のどちらもです。
それぞれの呼び出し元は、戻り値をそのままチェック対象のフィールドへ書き戻します(`step.target = ...`、
`a.target = ...`)。`Step` と `Assertion` はどちらも `frozen` を設定していないので
([`bajutsu/common/scenario/models/_base.py:35`](../../bajutsu/common/scenario/models/_base.py))、
この単純な属性への書き込みは、`expand_components` がシナリオ自身の `steps` を丸ごと置き換えるのに
すでに使っている手法と同じです。

この書き戻しには、`Scenario` をきれいに保つ以上の意味があります。省略された `target` を `None` の
まま残し、ランナーが手元のドライバへフォールバックする設計にすると、`step.target` /
`a.target` を直接読んでいる既存の箇所を2つ壊します。どちらも、宣言されたターゲットが2つ以上のとき
このフィールドは `None` にならないという前提で書かれています。

1つ目は `_steps_for_target`
([`bajutsu/common/runner/pipeline.py:1263-1275`](../../bajutsu/common/runner/pipeline.py))です。
BE-0082 の能力プリフライトのため、シナリオを1つのターゲット自身のステップだけに絞り込む関数です。
`target` が `None` のステップは、*どの*ターゲットの絞り込み結果にも残します。これは今日、正しい
動作です。`None` が現れるのは、宣言されたターゲットが多くて1個のシナリオだけであり、そこでは
すべてのステップがその1つのターゲットで実行されるからです。この提案のもとでは、ターゲットが
2個のシナリオで解決されないまま残った `None` は、「primary で実行する」という別の意味を
持ちます。しかし `_steps_for_target` は、この2つの意味を区別する手段を持ちません。結果として、
そのステップを*両方*のターゲットのプリフライト群に残してしまい、実際には実行しないバックエンドに
対しても検証してしまいます。

2つ目は `_route`
([`bajutsu/common/orchestrator/loop/_step_runner.py:78-114`](../../bajutsu/common/orchestrator/loop/_step_runner.py))
です。この関数が `self.state.last_target` を更新するのは、ルーティング対象のステップが空でない
`target` を持つときだけです。BE-0428 がデバイスの切り替えの目印に使う `prev_after` /
`prev_after_screenshot` の組をリセットする処理も同様です
([`_step_runner.py:87`](../../bajutsu/common/orchestrator/loop/_step_runner.py))。寄り道のあとで
primary target へ戻ってくるステップにも、同じリセットが必要です。これは BE-0428 自身のコード
コメントが名指しする `app, web, app` という形です
([`_step_runner.py:98-103`](../../bajutsu/common/orchestrator/loop/_step_runner.py))。明示的な
`target: <primary>` は、すでにこのリセットを起動します。`target` が `None` のまま残るステップは、
このリセットを飛ばしてしまい、直前のステップが動かした別デバイスのスクリーンショットとアクセシビリ
ティツリーを、3番目のステップへそのまま渡してしまいます。

読み込み時に解決済みの名前を書き戻せば、この2つの問題をどちらも避けられます。ランナー側の
コードはどちらも変更せずに済みます。すべてのステップの `target` が具体的な宣言名になれば、
`_route` の既存のルックアップ `self.by_target.get(step.target)`
([`_step_runner.py:88`](../../bajutsu/common/orchestrator/loop/_step_runner.py))は、primary に
解決されたステップも、明示的に名指しされたステップと同じ経路で振り分けます。`_steps_for_target`
も `None` を一度も見ることなく、正しいターゲットの検証群へ分類します。解決された名前は、
著者が明示した名前と同じ経路でレポートへ届きます。`StepOutcome(index=idx, action=kind,
target=self.target)`
([`_step_runner.py:137-150`](../../bajutsu/common/orchestrator/loop/_step_runner.py))も、
`_evaluate_expect` の内側で `replace(r, target=name)` によって組み立てられる `AssertionResult`
([`_functions.py:196-227`](../../bajutsu/common/orchestrator/loop/_functions.py))も同様です。

この解決は平坦であり、囲む `if` / `forEach` から継承されるものではありません。どちらかの内側に
ネストしたステップが `target` を省略すると、その解決先は `scenario.primary_target` に直接なります。
ラッパー自身の `target` にはなりません。ネストの深さによらず、同じ1つのルールが適用されます。
読者は、ネストしたステップの省略が何を意味するかを知るために、囲むラッパー自身の `target` を
たどる必要がありません。

### 引き続き必須のままにする範囲

`web` や `app` のブロックの内側にネストしたステップは、既存の逆向きのルールをそのまま保ちます。
`target` を省略することが引き続き必須です。そこで `target` を設定すると、引き続き読み込むときに
失敗します。そのステップは常に、囲むブロックがすでに解決したデバイス上で実行されるからです
([`_targets.py:49-57`](../../bajutsu/common/scenario/models/scenario/_targets.py))。
`primaryTarget` は、このブロック自身の `target` フィールドには何も変更を加えません。この
フィールドは、ほかのステップと同じ規則で解決されます。明示するか、primary が宣言されていれば
そちらがデフォルトになるか、のどちらかです。

`use:` ステップと、空でない `interrupts` リストは、シナリオが2つ以上のターゲットを宣言すると
引き続き無条件で拒否されます。BE-0428 が残したままの状態です。
[`_targets.py:58-67, 128-137`](../../bajutsu/common/scenario/models/scenario/_targets.py)に、
それぞれの理由があります。コンポーネント展開は `use:` ステップ自身の `target` を捨ててしまい
ます。`interrupts` エントリの `condition` には、そもそも target を固定してくれる囲みステップが
ありません。どちらも BE-0428 が当て推量せずに保留した未決事項であり、`primaryTarget` は
どちらにも答えません。コンポーネント自身のステップが `target` をどう解決すべきか、
`interrupts` の `condition` がどのターゲットを監視すべきかは、今後の別の提案が自由に決められます。

### 作業手順(MECE)

1. **スキーマ。** `Scenario.primary_target: str | None`。`_check_primary_target` をステップの
   走査の前に一度呼び出す。`_check_target` の新しい `default` 引数と戻り値。
   `_check_step_target` と `expect` のループで、解決した値を `step.target` / `a.target` へ
   書き戻す。
2. **ドキュメント。** `docs/dsl-grammar.md`(`primaryTarget` フィールドと、更新された `target`
   必須ルール)と `docs/scenarios.md`(`targets` / `target` の節、ワーク例つき)、およびそれぞれの
   `docs/ja/` 版。
3. **テスト。** スキーマ: `primaryTarget` のメンバーシップ検証と、`targets` が空のときの拒否。
   ステップ、`if` / `forEach` にネストしたステップ、`web` / `app` のラッパー、トップレベルの
   `expect` エントリがそれぞれ、`targets` が0個・1個・2個以上のどの場合でも、省略時に primary へ
   解決されること。`web` / `app` の内側にネストしたステップが、明示的な `target` を引き続き
   拒否すること。`use:` ステップと、空でない `interrupts` が引き続き無条件で拒否されること。
   ランナー: 寄り道のあとで primary へ戻るステップが、明示的な `target: <primary>` と同じように
   `prev_after` / `prev_after_screenshot` をリセットすること。能力プリフライトが、primary へ
   解決されたステップを自分のターゲットの検証群だけへ分類し、もう一方には分類しないこと。

## 検討した代替案

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| 位置による primary | 新しいフィールドを追加せず、`targets` の先頭の要素を primary とみなす。 | BE-0428 自身の「検討した代替案」が、`Step.target` 自体についてこの形をすでに却下している。`targets` の並び替えが、省略されたステップの実行先を黙って変えてしまう。BE-0428 が避けようとした、読者が覚えておかなければならない暗黙のルールそのものになる。 |
| マッピング形式の `targets` | 独立したフィールドの代わりに、`targets` を `{name, primary}` の形のエントリ(`targets: [{name: showcase-app, primary: true}, {name: showcase-web}]`)に拡張する。 | `targets` は、スキーマのほかの箇所ではすべて単純な `list[str]` である。`tags` や `capturePolicy` のトークンなど、シナリオレベルのリストはすべてこの形を共有している。マッピング形式は、ありふれたケース(primary 以外のエントリも含め、すべて)で YAML(YAML Ain't Markup Language)の分量を2倍にし、独立した `primaryTarget: <name>` がより素直に言えることを、何も付け加えない。 |
| `primaryTarget` を `targets` 2個以上で必須にする | `target` を全ステップに明示していて省略に頼らないシナリオも含め、複数ターゲットのシナリオすべてに primary の宣言を強制する。 | BE-0428 のもとですでにコミットされている複数ターゲットのシナリオは、どれもすでに全ステップへ `target` を明示している。`primaryTarget` を無条件に必須にすると、一度も使わないフィールドのためだけに、そのすべてへ編集を強いることになる。任意にすれば、関心のないシナリオには何のコストもかからない。`primaryTarget` を未設定のままにしたときの読み込み時ルールは、今日から変わらない。 |
| リーフのアクションステップだけで省略を許す | 単純なアクションステップ(`tap`、`type`、`wait` など)には `target` の省略を許す一方、`expect` エントリと `if` / `forEach` / `web` / `app` のラッパーには引き続き明示を要求する。これらはどのターゲットで確認するかを決めたり検証したりする役割を持つため。 | この提案が加えるルールは、すでに一文で言い切れる。省略された `target` は `primaryTarget` を意味する。`target` が許される場所ならどこでも同じである。これを「ここでは省略可、あそこでは必須」に分割すると、読者が覚えておくべきルールが1つ増える。リーフのステップとラッパーや `expect` という区別は、シナリオがすでに primary を宣言して同意している以上、扱いを分ける理由にならない。 |

## 進捗

> 作業の進行に合わせて最新の状態を保つ。チェックリストは「詳細設計」の MECE な作業手順に対応する
> (作業単位ごとに1つのチェック項目)。ログには、変更内容と時期を古い順に記録し、PR へリンクする。

- [ ] スキーマ: `Scenario.primary_target`、`_check_primary_target`、`_check_target` の `default`
      引数を `_check_step_target` と `expect` のループへ通す変更。
- [ ] ドキュメント: `docs/dsl-grammar.md`、`docs/scenarios.md`、およびそれぞれの `docs/ja/` 版。
- [ ] テスト: すべてのステップの形と宣言済みターゲット数にわたるスキーマの解決。primary への
      復帰時のランナーの `prev_after` リセット。能力プリフライトのターゲットごとの分類。

## 参考

- [BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution-ja.md)：
  この提案が拡張する `targets` / `target` の仕組みであり、この提案が緩める `target` 必須ルールの
  出どころでもある。
- [`docs/scenarios.md#targets--target-multi-target-scenarios-be-0428`](../../docs/scenarios.md#targets--target-multi-target-scenarios-be-0428)：
  `targets` / `target` の現行の一次情報であり、この提案のドキュメント作業が拡張する対象。
- [`bajutsu/common/scenario/models/scenario/_targets.py`](../../bajutsu/common/scenario/models/scenario/_targets.py)：
  この提案のスキーマ作業単位が変更するバリデータ。
- [`bajutsu/common/orchestrator/loop/_step_runner.py`](../../bajutsu/common/orchestrator/loop/_step_runner.py)：
  `_route`。この提案は、その既存のディスパッチと `last_target` の記録処理を変更せずに利用する。

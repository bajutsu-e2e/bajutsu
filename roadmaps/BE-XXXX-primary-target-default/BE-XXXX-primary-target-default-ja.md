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
`targets` リストの先頭の名前を指定します。ランナーがすでに primary として扱っている、まさに
その1つです(詳しくは後述の「`primaryTarget` の宣言」を参照)。シナリオが `primaryTarget` を
設定すると、ステップ、`if` / `forEach` / `web` / `app` のラッパー、トップレベルの `expect`
エントリのいずれも、単一ターゲットのシナリオで今までできていたのと同じように `target` を再び
省略できます。省略した `target` は、読み込みエラーにはならず、primary を対象に実行されます。

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

`primaryTarget` を未設定のままにすると、`targets` の数によらず今日のルールがそのまま残ります。
`targets` が2つ以上のとき、すべてのステップと、トップレベルの `expect` エントリすべてが、
引き続き自分の `target` を明示します。`targets` が0個または1個のときも、どちらの場合も今日と
同じ動作のままです。`primaryTarget` を設定して初めて、`targets` が2つ以上のシナリオの動作が
変わります。`targets` が0個のシナリオへ設定することは、それ自体が読み込みエラーです。
`targets` がちょうど1個のシナリオへ設定できるのは、その1個のターゲットを指定した場合に限ります。
どちらも次の「`primaryTarget` の宣言」で詳しく述べます。

## 動機

BE-0428 自身のバリデータ `_check_target_requirements`
([`bajutsu/common/scenario/models/scenario/_targets.py:80-143`](../../bajutsu/common/scenario/models/scenario/_targets.py))
は、シナリオが2つ以上のターゲットを宣言すると、すべてのステップとすべての `expect` エントリに
`target` を要求します。このルールは意図的なものです。BE-0428 自身の「検討した代替案」は、
ステップの行き先を暗黙のルールから決める案を退けています。読者に暗黙のルールを覚えさせないためです。
このルールは、ターゲットを何度も切り替え続けるシナリオにはよく合います。一方で、1つのターゲット
自身の流れを軸に組み立てられ、もう1つのターゲットに対して一度だけ確認するシナリオには、あまり
合いません。BE-0428 自身のワーク例がまさにこの形を示しています。`showcase-app` に対する `tap`
が1つ、それが届いたことを確かめる `wait` と `value` の検証が `showcase-web` に対して1つずつです
([`docs/scenarios.md:1193-1206`](../../docs/scenarios.md))。この1個の `tap` ステップに
`target: showcase-app` と書くことには、実際の情報があります。読者には、そのステップがどの
ターゲットに対して動くのかを知る手段がほかにないからです。

同じ記述は、`showcase-app` から一度も離れない、もっと長い流れの4番目や10番目のステップには、
新しい情報を何も足しません。2回目以降の繰り返しはどれも、シナリオの形自体がすでに決めている
事実を述べ直しているだけです。最後にようやく `showcase-web` へ向かう1つのステップだけが、
読者がまだ知らなかったことを告げます。そのステップは、10個の同じ隣人に囲まれていても、
1個だけの隣人に囲まれていても、同じくらいはっきり目立ちます。

`primaryTarget` が答える問いは、BE-0428 自身のバリデータが答える問いより狭いものです。
「どのターゲットでステップを実行するか」ではなく、「どのターゲットなら書かなくてよいか」に答えます。
シナリオがその答えを宣言すれば、ステップは自分の `target` を、答えと異なる場合にだけ書けば
済みます。単一ターゲットのシナリオがすでに得ている、`target` を一度も書かずに済む経済性と同じ
ものです。こうして読者は、クロスターゲットシナリオの形を一目で読み取れます。primary 以外を
明示的に名指しするステップこそが、別のプラットフォームで動くステップです。それ以外の行を
読み直す必要はありません。primary 自身の名前をあえて明示するステップも文法上は有効なままですが、
それはどちらにしても何も付け加えません。

## 詳細設計

### `primaryTarget` の宣言

`Scenario` に新しいフィールド `primary_target: str | None = Field(default=None,
alias="primaryTarget")` を追加します。既存の `targets: list[str]`
([`bajutsu/common/scenario/models/scenario/scenario.py:59`](../../bajutsu/common/scenario/models/scenario/scenario.py))
の隣に置きます。新しいチェック関数 `_check_primary_target` が、この値を `scenario.targets` に
照らして検証します。この関数は
[`_targets.py`](../../bajutsu/common/scenario/models/scenario/_targets.py) に置き、
`_check_target_requirements` がステップを1つも歩く前に一度呼び出します。

`primaryTarget` を未設定のままにすることは、常に許されます。何も変わりません。`targets` が空
なのに設定すると読み込みエラーになります。メッセージは `primaryTarget is set but the scenario
declares no targets` とし、ステップ自身の `target` が同じ形で不正なときに `_check_target` が
出す既存のメッセージに合わせます。それ以外の値を設定できるのは、その値が `targets` 自身の先頭
エントリ `targets[0]` と一致する場合に限ります。一致しなければ、指定した値と要求される値の両方を
示す読み込みエラーになります。これは「宣言済みのどれでもよい」の言い換えではなく、実質を伴う
制約です。`_lease_set` と `_target_runtimes`
([`bajutsu/common/runner/pipeline.py:930-943, 1000-1007`](../../bajutsu/common/runner/pipeline.py))
は、今日すでにランナー全体を通じて `targets[0]` こそが primary だと扱っています。クラッシュ
リカバリのループが判断材料にするリースは `targets[0]` のものであり、`_runtime_for`
([`bajutsu/common/runner/pipeline.py:1011-1060`](../../bajutsu/common/runner/pipeline.py))は、
そのすでに解決済みの評価コンテキストをそのまま再利用します。ほかの宣言済みターゲットが通る、
ターゲット設定優先の経路を通し直すことはありません。`primaryTarget` に別のエントリを許すと、
シナリオファイル側の「primary」という考えとランナー側のそれとが、黙って食い違ってしまいます。
一致を要求しておけば、この2つは常に1つの事実のままです。リース、クラッシュリカバリ、評価
コンテキストの解決がこの提案自身の変更を必要としないのは、この一致に支えられています。変更が
必要になるのは、以下で述べる、ステップを自分自身の解決済みターゲットで振り分ける2箇所だけです。
`targets` がちょうど1個のシナリオでも、一致する `primaryTarget` は冗長として拒否せず受け入れます。
理由はいつもと同じで、`targets[0]` はその1個のエントリでもあるからです。

### 書き戻さずに省略を解決する

`_check_target`
([`_targets.py:26-46`](../../bajutsu/common/scenario/models/scenario/_targets.py))に、逃げ道を
1つ追加します。今日、ターゲットが2つ以上宣言されているときに `target` が `None` だと
`target is required` を送出していますが、`default`(シナリオの `primary_target`)が手元にあれば、
`target` が `None` であることを合法とします。`_check_target_requirements` は、呼び出すたびに
`scenario.primary_target` をその `default` として渡します。ステップ自身の `target`
([`_targets.py:68`](../../bajutsu/common/scenario/models/scenario/_targets.py)、`_check_step_target`
の内側)と、トップレベルの `expect` エントリの `target`
([`_targets.py:142`](../../bajutsu/common/scenario/models/scenario/_targets.py))のどちらもです。
`primaryTarget` を未設定のままにしたシナリオは `None` を渡すことになるため、この新しい逃げ道は
開かず、今日の `target is required` エラーがそのまま発生します。

トップレベルの `expect` エントリについては、合法にするだけでこの提案は完結します。
`_evaluate_expect` 自身のグルーピング `groups.setdefault(a.target or primary_target, [])`
([`_functions.py:198`](../../bajutsu/common/orchestrator/loop/_functions.py))が、省略された
`a.target` を実行中の `primary_target` 引数へすでに今日フォールバックさせているからです。
0個・1個のターゲットのために作られた仕組みですが、`primaryTarget` があればその引数が2個以上の
シナリオでも正しいターゲットになる、というだけの話で、そのまま正確に働きます(後述の
「`primaryTarget` の宣言」を参照)。`Assertion.target` にはこれ以上の変更は要りません。
`_check_target` で省略を合法にすることが、修正のすべてです。

ステップ自身の `target` には、もう1つ手当てが要ります。`_route` のディスパッチと BE-0082 の
能力プリフライトが、どちらも `step.target` を直接読んでいて、そのようなフォールバックを持たない
からです。ここで誘惑される修正は、`_check_step_target` が解決した名前を `step.target` へ直接
書き戻すことですが、それはこの提案がしてはならないことです。`Step` は、検証が終わったあとも
常に書き込み専用の読み取り専用値であり続けるわけではないからです。すでに検証済みの `Step` を
YAML へ再びシリアライズし直す既存の呼び出し箇所が2つあり、どちらも「出てくるものは著者が
書いたものを映している」という前提に立っています。

- **serve の Author エディタです。** `apply_selector` はファイルを読み込み、すでに検証済みの
  `scenario.steps[step_index]` を取り出してダンプし、そのダンプをそのステップのソース範囲へ
  そのまま差し戻します
  ([`bajutsu/common/scenario/edit.py:74-95`](../../bajutsu/common/scenario/edit.py))。モデルへ
  書き戻した `target` は、そのまま著者自身のファイルへダンプされてしまいます。しかもそれは、
  省略することにこそ意味があったはずの、その1つのステップに対してです。
- **run 自身のシナリオスナップショットです。** `scenario_dict` は、レポートのシナリオソース表示と、
  run の結果の隣に書かれる `scenario.yaml` の両方に使われます。そのドキュメンテーション文字列
  自身が、意図を直接述べています。「著者が書いたとおりに簡潔なスナップショットを保つ」という
  ものです
  ([`bajutsu/common/scenario/serialize.py:57-66`](../../bajutsu/common/scenario/serialize.py))。
  読み込み時にこの提案が刻んだ `target` は、複数ターゲットを持つすべての run のスナップショットで、
  省略されたステップすべてについて、この約束を破ってしまいます。

代わりに `Step` に `_resolved_target: str | None = PrivateAttr(default=None)` を追加し、
`self.target or self._resolved_target` を返す読み取り専用プロパティ `resolved_target` を添えます。
これは `Scenario` 自身の `_source_stem` / `source_stem`(BE-0417、
[`scenario.py:115-120`](../../bajutsu/common/scenario/models/scenario/scenario.py))とまったく
同じ手法です。「検証済みモデルに載せて運ぶ、読み込み時の状態を `model_dump()` へ漏らさない」ための、
まさにその前例です。`_check_step_target` は、`_check_target` が上で開いたのと同じ新しい逃げ道の
中で `step._resolved_target = default` を設定し、`step.target` 自体には手を触れず `None` の
ままにします。`model_dump()` は private attribute を一切見ないので、`apply_selector` も
`scenario_dict` も、著者が書いたとおりの `None` をそのままダンプし続けます。具体的な名前を
必要とするそれぞれの箇所は、`step.target` を直接読む代わりに `step.resolved_target` を読みます。
`_route` のディスパッチ
([`_step_runner.py:78-114`](../../bajutsu/common/orchestrator/loop/_step_runner.py))と
`_steps_for_target` 自身の絞り込み
([`bajutsu/common/runner/pipeline.py:1263-1275`](../../bajutsu/common/runner/pipeline.py))は、
それぞれの1箇所の `step.target` 読み取りを `step.resolved_target` に切り替えるだけで、どちらの
関数もそれ以外は変わりません。`web:` / `app:` ブロックの内側にネストしたステップは、この新しい
逃げ道にそもそも到達しません。`_check_step_target` が、`_check_target` を呼ぶ前にそこで `target`
を無条件に拒否するからです。そのため `resolved_target` も、今日の `target` と同じく `None` の
ままであり、`_route` も今日どおり `self, active_driver` を返し続けます。レポートへの帰属にも
変更は要りません。`StepOutcome(index=idx, action=kind, target=self.target)`
([`_step_runner.py:137-150`](../../bajutsu/common/orchestrator/loop/_step_runner.py))は、すでに
*ランナー自身*の `self.target`、つまり構築時に一度だけ決まる、その生きている `_StepRunner` 自身の
名前を読んでいて、ステップ側の値を読んではいません。ステップが `target` を明示していたかどうかに
かかわらず、すでに正しいターゲット名を答えています。

`step.target` へ書き戻さないことで、シリアライズの往復に関する危険は消えますが、private attribute
であっても、それが載っているモデルのインスタンスと運命をともにする危険までは消えません。
`apply_setups`
([`bajutsu/common/scenario/expand.py:208-236`](../../bajutsu/common/scenario/expand.py))は、
共有セットアップへの参照を一度だけ解決して `Step` のリストをキャッシュし、その同じオブジェクトを、
同じ参照を名指しするすべてのシナリオへ差し込みます。`scenario.steps = [*cache[ref],
*scenario.steps]` という形です。今日、セットアップを共有する2つのシナリオは、この共有ステップを
読むだけなので、オブジェクトを共有しても何のコストもかかりません。一方のシナリオで
`_resolved_target` を設定すると、そうはいきません。同じセットアップ参照を共有するシナリオは
すべて、同じキャッシュ済みの `Step` オブジェクトに対して検証を行います。`_check_target_requirements`
を呼び出すたびに、直前のシナリオの検証がちょうど設定したばかりの値を上書きしてしまいます。
どのシナリオも実際に実行される頃には、その同じセットアップを共有するすべてのシナリオが、
*最後に*検証されたシナリオの `primaryTarget` を見ることになります。それ以外のシナリオはすべて、
黙って誤ってルーティングされてしまいます。`apply_setups` は、キャッシュした各ステップを、あるシナリオへ
差し込む前に複製することでこれを塞ぎます。`cache[ref]` のそれぞれに対して `st.model_copy(deep=True)`
を行い、この提案のバリデータがそのステップに対して実行される前に済ませます。こうすれば、
それぞれのシナリオ自身の検証が、そのシナリオ自身の複製にだけ `_resolved_target` を設定します。

`_steps_for_target` に残る既存の限界を、読者に後から見つけさせるのではなく、ここで名指しして
おきます。この関数自身のドキュメンテーション文字列が、絞り込みをトップレベルのエントリだけに
限っています。「ネストした `if` / `forEach` の本体は、中身をそのまま保持する。ネストしたステップは
自分自身の `target` を持ち、プリフライトはいずれにせよそこへ入っていくからだ」
([`pipeline.py:1266-1269`](../../bajutsu/common/runner/pipeline.py))という説明です。BE-0428 は
すでに、ネストしたステップが自分を囲むラッパーとは異なるターゲットを名指しすることを許して
います。そのため、プリフライトの絞り込み結果に残ったラッパーが運んできた、そのラッパーとは
異なるターゲットを持つネストしたステップは、今日すでに誤ったバックエンドの能力に照らして
検証されています。この提案とは無関係に、明示的にその食い違いを書けば今日でも起きることです。
この提案がこの隙間を作るわけではありません。それでも、`target` を省略しただけのネストしたステップが
黙って primary に解決されるようになる分だけ、著者が気づかないままこの同じ構図に行き着きやすく
なります。`_steps_for_target` 自身のトップレベル限定の絞り込みを閉じることは、この提案の役目
ではありません。この隙間はこの提案より前からあり、BE-0428 もそれを閉じてはいません。ただし
後述の作業手順には、テストを1つ加えます。primary へ解決されたステップが、primary 以外へ
ルーティングされたラッパーの内側にネストしている状態です。黙って通り過ぎるのではなく可視化して
おきます。`_steps_for_target` 自身の再帰を次に引き受ける提案のために、既知の限界として記録します。

この解決は平坦であり、囲む `if` / `forEach` から継承されるものではありません。どちらかの内側に
ネストしたステップが `target` を省略すると、その `resolved_target` を通じて primary へ直接
解決されます。ラッパー自身の `target` にはなりません。ネストの深さによらず、同じ1つのルールが
適用されます。読者は、ネストしたステップの省略が何を意味するかを知るために、囲むラッパー自身の
`target` をたどる必要がありません。

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

1. **スキーマ。** `Scenario.primary_target: str | None`。`_check_primary_target` は、未設定か
   `targets[0]` と一致する場合だけを許し、ステップの走査の前に一度呼び出します。`_check_target`
   に、`default` が手元にあるときは `target` の省略を合法にする逃げ道を追加します。
   `Step._resolved_target: str | None = PrivateAttr(default=None)` とその `resolved_target`
   プロパティを追加します。`_check_step_target` は、その同じ逃げ道の中で `step._resolved_target`
   を設定し、`step.target` 自体には手を触れません。`apply_setups` は、キャッシュした各ステップを、
   あるシナリオへ差し込む前に `st.model_copy(deep=True)` で複製し、この提案の
   `_resolved_target` の書き込みが別のシナリオがすでに検証したステップへ及ばないようにします。
2. **ランナー。** `_route` と `_steps_for_target` は、それぞれの1箇所の `step.target` 読み取りを
   `step.resolved_target` に切り替えます。どちらの関数もそれ以外は変わりません。
   `_evaluate_expect`、`StepOutcome` の帰属、リース、評価コンテキストの解決には、まったく変更が
   要りません。
3. **ドキュメント。** `docs/dsl-grammar.md`(`primaryTarget` フィールド、その `targets[0]`
   制約、更新された `target` 必須ルール)と `docs/scenarios.md`(`targets` / `target` の節、
   ワーク例つき)、およびそれぞれの `docs/ja/` 版。
4. **テスト。** スキーマ: `primaryTarget` が未設定か `targets[0]` と一致する場合だけ受け入れられる
   こと、`targets` が空のときの拒否。ステップ、`if` / `forEach` にネストしたステップ、
   `web` / `app` のラッパー、トップレベルの `expect` エントリがそれぞれ、`targets` が2個以上の
   とき、省略時に primary へ解決されること。それとは別に、`targets` が0個または1個のときの
   ステップは、`primaryTarget` の有無によらず今日どおりの動作を保つこと。`web` / `app` の
   内側にネストしたステップが、明示的な `target` を引き続き拒否し、その `resolved_target` も
   `None` のままであること。`use:` ステップと、空でない `interrupts` が引き続き無条件で拒否
   されること。省略された `target` を持つシナリオへ `model_dump()` を実行しても、`target` が
   一切現れないこと(往復修正の証明)。同じ1つの `setup` を共有する2つのシナリオが、それぞれ
   違う `primaryTarget` を持っていても、それぞれ自分の省略ステップを正しく解決できること
   (複製による修正の証明)。ランナー: 寄り道のあとで primary へ戻るステップが、明示的な
   `target: <primary>` と同じように `prev_after` / `prev_after_screenshot` をリセットすること。
   能力プリフライトが、primary へ解決されたトップレベルのステップを自分のターゲットの検証群だけへ
   分類すること。そして、上で述べた既知の限界を見過ごさず、primary へ解決されたステップが
   primary 以外へルーティングされたラッパーの内側にネストしていると、そのラッパー自身の
   バックエンドに照らして検証されることを、今後の別の提案に向けた既知の隙間として記録すること。

## 検討した代替案

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| 位置による primary | 新しいフィールドをまったく追加せず、`targets` の先頭の要素を primary とみなします。 | BE-0428 自身の「検討した代替案」が、`Step.target` 自体についてこの形をすでに却下しています。理由はここでもそのまま当てはまります。`targets` の並び替えが、省略されたステップの実行先を黙って変えてしまい、並び順に意味があることをどこにも記録しません。この提案の `primaryTarget` も結局は `targets[0]` に解決されます(「`primaryTarget` の宣言」を参照)が、著者が明示し、ローダーがリストと突き合わせて検証する名前としてです。`targets` を編集して意図した primary が先頭から外れると、黙った挙動の変化ではなく、食い違いを名指しする読み込みエラーになります。 |
| `primaryTarget` をランナーへ通し、どの宣言済みターゲットも許す | `primaryTarget` を `targets[0]` に制約する代わりに、`_lease_set`・`_target_runtimes`・`_runtime_for` の評価コンテキスト解決(`pipeline.py:930-1060`)を拡張し、今日 `targets[0]` を直書きしている箇所すべてで `scenario.primary_target` を読ませます。 | より柔軟な選択肢であり、著者は宣言順によらずどのターゲットでも primary に選べます。この提案では採りません。スキーマだけの変更を、リース、クラッシュリカバリのリース判定、評価コンテキスト解決に触れるランナーの変更へ変えてしまうからです。得られる利点(宣言順とは無関係に primary を選べること)は、著者が `targets` 自体の並び順を選ぶだけですでにただで手に入ります。primary と先頭宣言とを実際に食い違わせたいシナリオが現れたときのために、今後の提案として残しておきます。 |
| マッピング形式の `targets` | 独立したフィールドの代わりに、`targets` を `{name, primary}` の形のエントリ(`targets: [{name: showcase-app, primary: true}, {name: showcase-web}]`)に拡張します。 | `targets` は、スキーマのほかの箇所ではすべて単純な `list[str]` です。`tags` や `capturePolicy` のトークンなど、シナリオレベルのリストはすべてこの形を共有しています。マッピング形式は、ありふれたケース(primary 以外のエントリも含め、すべて)で YAML(YAML Ain't Markup Language)の分量を2倍にし、独立した `primaryTarget: <name>` がより素直に言えることを、何も付け加えません。 |
| `primaryTarget` を `targets` 2個以上で必須にする | `target` を全ステップに明示していて省略に頼らないシナリオも含め、複数ターゲットのシナリオすべてに primary の宣言を強制します。 | BE-0428 のもとですでにコミットされている複数ターゲットのシナリオは、どれもすでに全ステップへ `target` を明示しています。`primaryTarget` を無条件に必須にすると、一度も使わないフィールドのためだけに、そのすべてへ編集を強いることになります。任意にすれば、関心のないシナリオには何のコストもかかりません。`primaryTarget` を未設定のままにしたときの読み込み時ルールは、今日から変わりません。 |
| リーフのアクションステップだけで省略を許す | 単純なアクションステップ(`tap`、`type`、`wait` など)には `target` の省略を許す一方、`expect` エントリと `if` / `forEach` / `web` / `app` のラッパーには引き続き明示を要求します。これらはどのターゲットで確認するかを決めたり検証したりする役割を持つためです。 | この提案が加えるルールは、すでに一文で言い切れます。省略された `target` は `primaryTarget` を意味します。`target` が許される場所ならどこでも同じです。これを「ここでは省略可、あそこでは必須」に分割すると、読者が覚えておくべきルールが1つ増えます。リーフのステップとラッパーや `expect` という区別は、シナリオがすでに primary を宣言して同意している以上、扱いを分ける理由になりません。 |

## 進捗

> 作業の進行に合わせて最新の状態を保つ。チェックリストは「詳細設計」の MECE な作業手順に対応する
> (作業単位ごとに1つのチェック項目)。ログには、変更内容と時期を古い順に記録し、PR へリンクする。

- [ ] スキーマ: `Scenario.primary_target`、`_check_primary_target`(未設定か `targets[0]` の
      どちらかだけを許します)、`target` の省略を合法にする `_check_target` の新しい逃げ道、
      `Step._resolved_target` / `resolved_target`、キャッシュしたステップを複製する
      `apply_setups` の変更を追加します。
- [ ] ランナー: `_route` と `_steps_for_target` を、`step.target` の代わりに `step.resolved_target`
      を読むよう変更します。
- [ ] ドキュメント: `docs/dsl-grammar.md`、`docs/scenarios.md`、およびそれぞれの `docs/ja/` 版を
      更新します。
- [ ] テスト: すべてのステップの形と宣言済みターゲット数にわたるスキーマの解決、往復の修正
      (`model_dump()` が `target` を出力しないこと)、共有セットアップの複製による修正、primary
      への復帰時のランナーの `prev_after` リセット、能力プリフライトのターゲットごとの分類と
      既知のネストしたラッパーの限界を検証します。

## 参考

- [BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution-ja.md)：
  この提案が拡張する `targets` / `target` の仕組みであり、この提案が緩める `target` 必須ルールの
  出どころでもあります。
- [`docs/scenarios.md#targets--target-multi-target-scenarios-be-0428`](../../docs/scenarios.md#targets--target-multi-target-scenarios-be-0428)：
  `targets` / `target` の現行の一次情報であり、この提案のドキュメント作業が拡張する対象です。
- [`bajutsu/common/scenario/models/scenario/_targets.py`](../../bajutsu/common/scenario/models/scenario/_targets.py)：
  この提案のスキーマ作業単位が変更するバリデータです。
- [`bajutsu/common/scenario/expand.py`](../../bajutsu/common/scenario/expand.py)：`apply_setups`
  です。この提案の `_resolved_target` の書き込みのために、そのキャッシュしたステップを複製する
  必要があります。
- [`bajutsu/common/scenario/edit.py`](../../bajutsu/common/scenario/edit.py)：`apply_selector`
  です。`step.target` 自体への書き戻しが壊していたはずの、再シリアライズする2つの呼び出し元の
  1つです。
- [`bajutsu/common/scenario/serialize.py`](../../bajutsu/common/scenario/serialize.py)：
  `scenario_dict` です。「著者が書いたとおりに簡潔に保つ」という、もう1つの契約です。
- [`bajutsu/common/runner/pipeline.py`](../../bajutsu/common/runner/pipeline.py)：`_lease_set` と
  `_target_runtimes` です。この提案自身の `primaryTarget` が食い違わずに固定される、
  「`targets[0]` が primary」というルールの出どころです。`_steps_for_target` もここにあり、
  この提案がその1箇所の `step.target` 読み取りを `step.resolved_target` へ切り替えます。
- [`bajutsu/common/orchestrator/loop/_step_runner.py`](../../bajutsu/common/orchestrator/loop/_step_runner.py)：
  `_route` です。この提案は同じ1箇所の読み取りを切り替えるだけで、そのディスパッチと
  `last_target` の記録処理はそれ以外そのまま利用します。

[English](BE-XXXX-multi-target-interrupts.md) · **日本語**

# BE-XXXX — interruptsのエントリにtargetを追加し、省略時はprimary targetにします

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-multi-target-interrupts-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Scenario authoring features |
| 関連 | [BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution-ja.md)、[BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers-ja.md) |
<!-- /BE-METADATA -->

## はじめに

シナリオの[`interrupts`](../../docs/ja/scenarios.md#interrupts-handling-unpredictable-interstitial-screens)の
各エントリに、`target: str | None = None`フィールドを追加します。この`target`は、
[BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution-ja.md)が
`Step`と`expect`のトップレベルエントリにすでに与えているフィールドと同じ形です。どちらも、シナリオが
宣言する[ターゲット](../../docs/ja/glossary.md#target-app-device)のうち、そのエントリがどれを指すかを
名指します。ただし`interrupts`エントリの`target`は、`Step`・`expect`の2つとは違い、シナリオが2つ以上の
ターゲットを宣言していても常に省略できます。省略したエントリは、シナリオのprimary targetを監視します。
primary targetとは、`--target`が名指すターゲット、または自己宣言シナリオが持つ`targets`の最初の1つの
ことです。`target`を設定したエントリは、その名指したターゲットを監視します。そのエントリ自身の`steps`
(割り込みを解除する手順)内の`Step`は、自分の`target`を省略すると、シナリオのprimary targetではなく、
囲むエントリのtargetを継承します。

この変更は、BE-0428がわざと残した制限も取り除きます。現状、シナリオが2つ以上のターゲットを宣言していると、
`interrupts`の各エントリの中身を見るより前に、空でない`interrupts`リストそのものをロード時に拒否します。

## 動機

BE-0428は、`Step`とトップレベルの`expect`エントリに`target`を追加しました。これにより、1つのシナリオが
Bajutsuの[複数のバックエンド](../../docs/ja/architecture.md#drivers-and-backend-selection)
(iOS Simulator、web、Android)をまたいでステップを行き来させられるようになりました。`interrupts`は
わざとその対象から外しました。当時は、監視すべきターゲットの要素ツリーを決める方法が
ありませんでした。この検証を今日担っているのは`_check_target_requirements`です
([`_targets.py`](../../bajutsu/common/scenario/models/scenario/_targets.py))。
`len(scenario.targets) >= 2`になった時点で、各エントリの中身を見るより前に`interrupts`そのものを
拒否します。

この拒否は、2つのターゲットを混在させるすべてのシナリオから、ある仕組みを奪います。ステップの並びの
どこに現れるか決まっていない画面、たとえばOSの権限プロンプトやCookie同意バナーのための仕組みです。
単一ターゲットのシナリオなら、`interrupts`を通じてすでに持っている仕組みです。今日この制限を回避する
シナリオ作者は、そうした画面を起こしうる操作の直前ごとに`if`分岐を手で書くか、`interrupts`を諦めて
2つ目のターゲットの宣言そのものを落とすかのどちらかを選んでいます。その画面が現れるかどうかは、
シナリオがどのステップをどのターゲットへ振り分けるかとは、そもそも関係がありません。

`target`を省略したエントリは、primary targetへフォールバックします。この設計は、このコードベースが
すでに1つ隣のフィールドで採用しているパターンをなぞります。`expect`自身の`Assertion.target`は、
省略された値を評価時にprimary targetへ解決します。その処理は
`groups.setdefault(a.target or primary_target, []).append(i)`
([`_functions.py:198`](../../bajutsu/common/orchestrator/loop/_functions.py))です。本項目は、この
フォールバックを`interrupts`にも広げます。作者が特定のエントリを別のターゲットへ振り分ける理由を
持たない限り、2つ以上のターゲットを宣言したシナリオでも、単一ターゲットのシナリオとまったく同じ
感覚で`interrupts`を使い続けられます。

## 詳細設計

### 単位1 — `Interrupt.target`

[`Interrupt`](../../bajutsu/common/scenario/models/steps/interrupt.py)に
`target: str | None = None`を追加します。省略時はprimary targetへ解決される旨をdocstringに書きます。
`Step.target`([`step.py:131`](../../bajutsu/common/scenario/models/steps/step.py))には手を加えません。
シナリオが2つ以上のターゲットを宣言した場合に`Step`自身の`target`を必須とするBE-0428のルールは、
この常に省略可能な新フィールドの隣で、変わらず保たれます。

### 単位2 — 検証ロジック

[`_check_target`](../../bajutsu/common/scenario/models/scenario/_targets.py)に
`required: bool = True`引数を追加します。`required=False`のときは、`len(known) >= 2`かつ
`target is None`で本来なら例外を出す分岐をスキップします。`known`に含まれない`target`を拒否する
チェックは、`required`の値によらず今までどおり働きます。
[`_check_target_requirements`](../../bajutsu/common/scenario/models/scenario/_targets.py)は、
128〜137行にある一律拒否を削除します。代わりに、各`interrupts`エントリを検証します。その呼び出しは
`_check_target(entry.target, known=known, context="interrupts entry", required=False)`です。
エントリの`condition`は、今までどおり
[`_reject_assertion_target`](../../bajutsu/common/scenario/models/scenario/_targets.py)で拒否します。
`target`は`Interrupt`自身が持ち、`condition`には持たせません。`Step`の`assert:`リストや`if.condition`
にすでにある区別と対称です。

`_check_step_target`と、それを呼ぶ`walk_steps`は、3つ目のモードを持ちます。今日すでにある「トップ
レベル、2つ以上のターゲット宣言時は必須」と「`web:`/`app:`の内側、常に禁止」の2つに加える形です。
`Interrupt`自身の`steps`内にある`Step`は、常に`target`を省略でき、設定する場合は宣言済みターゲットの
中から選びます。この割り込み解除の手順が`target`を必須としないのは、省略時にシナリオのprimary
targetではなく、囲むエントリのtargetへフォールバックするからです。

### 単位3 — config-levelのinterruptsはtargetを拒否する

[`TargetConfig.interrupts`](../../bajutsu/common/config/schema/target_config.py)は、すでに1つの
`targets.<name>`ブロックの内側にあります。そこにあるエントリが別のターゲット名を名指せてしまうと、
そのブロック自身と矛盾します。
新しいバリデータを、
[`_no_component_in_target_steps`](../../bajutsu/common/config/schema/target_config.py)の隣に
足します。`targets.<name>.interrupts`のエントリに`target`が設定されていたら、ロード時に拒否します。

### 単位4 — 実行時の合成

[`_runtime_for`](../../bajutsu/common/runner/pipeline.py)は現在、`scenario.interrupts`の全エントリを、
宣言済みの各ターゲットの`TargetRuntime.interrupts`へ無条件にコピーしています。
[`_run_on_lease`](../../bajutsu/common/runner/pipeline.py)も、primaryの`_LoopConfig.interrupts`へ
同じことをしています。どちらも
`(entry.target or primary_target) == そのターゲットの名前`でフィルタするよう変更します。
`primary_target`には、`_target_runtimes`がすでに`routed[0]`として持っている値を使います。
config由来のinterrupts(`run_defaults.interrupts`)はフィルタしません。すでに、それを宣言した設定
自身が属する1つのターゲットのものだからです。

### 単位5 — 割り込み解除の手順が使う実効target

`Interrupt.steps`内の`Step`が`target`を省略したとき、その`Step`はそのエントリ自身の実効target
に対して実行します。実効targetとは`entry.target or primary_target`のことで、シナリオのprimary
targetではありません。割り込み解除の実行には、`run_scenario`または`_step_runner.py`の割り込み
リカバリ経路が`primary_target`を渡します。そのエントリの`steps`を実行するあいだだけ、この値を
実効targetに差し替えます。

### 単位6 — ターゲットをまたいだ発火のテスト

2つのターゲットを宣言し、片方だけが割り込みを起こす画面を出すシナリオを使う結合テストを追加します。
その割り込みが、`target`(またはフォールバック)が名指すターゲットに対してだけ発火し、解除されることを
確認します。もう一方のターゲットの`_InterruptGuard`は関与しません。

### 単位7 — ドキュメント

[`docs/scenarios.md`](../../docs/scenarios.md)には、更新すべき節が2つあります。1つは
「interrupts」、もう1つは「What a multi-target run does with the rest of a target's config」です。
どちらも、2つ以上のターゲットを宣言すると`interrupts`を一律拒否するという現行の記述を持ちます。
その記述を、新しい`target`フィールドとそのprimary targetへのフォールバックの説明に置き換えます。
[`docs/ja/scenarios.md`](../../docs/ja/scenarios.md)にも対応する日本語の更新を反映します。

## 検討した代替案

| 案 | 採らなかった理由 |
|---|---|
| `Step`・`expect`と同じく、シナリオが2つ以上のターゲットを宣言したら`interrupts`エントリにも`target`を必須にする | `Step`自身の必須な`target`は、複数のターゲットが入り交じるシナリオの中で、その1つの操作がどのターゲットを指すかを読み手に伝えます。マルチターゲットシナリオの`interrupts`エントリの大半は、依然としてprimary targetを指すため、すべてのエントリにそう書かせると、振り分けの判断を伴わない繰り返しの行が増えるだけになります。 |
| 1つの`interrupts`エントリで複数のターゲットを同時に名指せるようにする(`targets: list[str]`) | 各ターゲット自身の`_InterruptGuard`は、すでにそれぞれの要素ツリーを独立に監視しています。1つのエントリで複数のターゲットを名指しても、内部では結局ターゲットごとに1つずつのチェックへ展開されます。単一の値を取る`target`は`Step.target`とのあいだで対称性を保ち、同じ条件と解除手順を2つのターゲットへ適用したい作者は、エントリを2つ書けば済みます。 |
| `Interrupt.steps`内の`Step`を、トップレベルのstepの省略時と同じくシナリオのprimary targetをデフォルトにする | 割り込みが発火するターゲットと、その解除手順が働きかけるターゲットは、通常のケースでは同一です。primary targetをデフォルトにすると、primary target以外を監視するエントリの解除手順のほぼすべてに`target`を書かせることになります。 |
| `targets.<name>.interrupts`のエントリにも、シナリオレベルと同じ`target`フィールドを許す | `targets.<name>.interrupts`配下のエントリは、すでにそのconfigブロックが設定する1つのターゲットに属しています。そこに`target`フィールドを置いても、同じ名前を繰り返すか、矛盾するかのどちらかにしかならず、そのブロック自身から読み取れる以上のことは何も語りません。 |

## 進捗

> 作業の進行に合わせて最新の状態を保ちます。チェックリストは*詳細設計*のMECEな作業分解を
> 1単位1項目でなぞります。ログには、変更した内容と時期(古い順)を、PRへのリンクとともに記録します。

- [ ] 単位1 — `Interrupt.target`(`bajutsu/common/scenario/models/steps/interrupt.py`)。
- [ ] 単位2 — `_check_target`の`required`引数、一律拒否の削除、`Interrupt.steps`向けの新しい検証
      モード(`_targets.py`)。
- [ ] 単位3 — config-levelの`interrupts`エントリで`target`を拒否する(`target_config.py`)。
- [ ] 単位4 — `_runtime_for`と`_run_on_lease`で、ターゲットごとに`interrupts`をフィルタする
      (`pipeline.py`)。
- [ ] 単位5 — `Interrupt.steps`が使う実効targetの解決。
- [ ] 単位6 — ターゲットをまたいだ割り込み発火の結合テスト。
- [ ] 単位7 — `docs/scenarios.md` / `docs/ja/scenarios.md`。

## 参考

- [仕様書 — マルチターゲットシナリオにおける interrupts の target 対応](../../docs/specs/multi-target-interrupts.md) —
  本項目が正式化する設計の書き起こしです。
- [BE-0428 — Multi-target scenario execution](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution-ja.md) —
  `Step.target`と`Assertion.target`を追加し、`interrupts`をわざと対象から外しました。
- [BE-0314 — Scenario interrupt handlers](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers-ja.md) —
  ターゲットの振り分けが存在する前の、`interrupts`そのものです。

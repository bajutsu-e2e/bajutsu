# マルチターゲットシナリオにおける interrupts の target 対応

> ステータス: ドラフト
> 対象: `bajutsu/common/scenario/models/steps/interrupt.py`、`bajutsu/common/scenario/models/scenario/_targets.py`、`bajutsu/common/runner/pipeline.py`、`bajutsu/common/config/schema/target_config.py`、`docs/scenarios.md`
> 関連: [BE-0428](../../roadmaps/BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md)（マルチターゲットシナリオ実行）、[BE-0314](../../roadmaps/BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md)（interrupts本体）

`interrupts` の各エントリに `target` フィールドを追加します。現状は、2つ以上のターゲットを宣言したシナリオで `interrupts` がロード時に一律拒否されます。この変更により、そのシナリオでも `interrupts` を使えるようになります。

`Step` と `expect` の `target` は、2つ以上のターゲットを宣言したシナリオでは省略できません。`interrupts` の `target` は、その場合でも常に省略できます。省略したエントリは、実行時にそのシナリオの**primary target**を監視します。primary targetとは、実行の基準となる1つのターゲットのことです。`--target` フラグが名指すターゲットから解決されます。シナリオが自身で `targets` を宣言している場合は、その最初の1つから解決されます。

## 1. なにをつくるのか

- `Interrupt` モデルに `target: str | None = None` を追加します。
- 宣言済みターゲットが2つ以上のシナリオでも、`interrupts` エントリの `target` を省略できます。省略したエントリは、実行時にそのシナリオのprimary targetのtreeを監視します。
- `target` を明示すると、そのエントリが監視するターゲットを宣言済みターゲットの中から指定できます。
- `Interrupt.steps`（割り込みを解除する手順）内の各 `Step` は、`target` を省略すると、囲む `Interrupt` 自身の実効targetを継承します。実効targetとは、明示があればその値、省略ならprimary targetのことです。シナリオ全体のprimary targetは継承しません。`Step` 側で明示すれば、解除の手順だけを別のターゲットへ向けることもできます。
- `interrupts` 自体を一律拒否している現行の検証を取り除きます。この検証は、宣言済みターゲットが2つ以上のシナリオを対象にしています（`bajutsu/common/scenario/models/scenario/_targets.py:128-137`）。

### やらないこと

- `Step.target` と `expect` の `Assertion.target` が持つ既存の必須ルールは変更しません。このルールは、2つ以上のターゲットを宣言したシナリオでは省略不可というものです。この2つは今回のスコープ外に据え置きます。
- `Interrupt` が1つのエントリで複数ターゲットを同時に監視できる拡張は作りません。たとえば `targets: list[str]` です。`target` は単一の値に限ります。
- `targets.<name>.interrupts` のエントリに `target` を設定できるようにはしません。このエントリは、設定ファイル側にあるアプリ全体のinterruptsデフォルト値です。設定した場合はロード時にエラーとします。

## 2. なぜつくるのか

[BE-0428](../../roadmaps/BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md) は、`Scenario.targets`、`Step.target`、`expect` の `Assertion.target` を実装しました。これにより、iOS Simulator、web、Androidのような複数のバックエンドを1つのシナリオの中で同時に扱えるようになりました。ただし `interrupts` だけはスコープ外に残されました。条件（`condition`）がどのターゲットのtreeを監視すべきかを決める方法が、まだなかったからです。宣言済みターゲットが2つ以上のシナリオでは、`interrupts` エントリを1つでも書くとロード時に拒否されます（`_targets.py:128-137`）。

この制限のもとでは、アプリとwebを同時に操作するようなマルチターゲットシナリオで困りごとが起きます。OS権限プロンプトやCookie同意バナーのような予測不能な割り込み画面が現れても、`interrupts` で処理できません。シナリオ作者は、該当する操作の直前ごとに手作業で `if` 分岐を挟むしかありません。あるいは、マルチターゲット化を諦めるかです。

`target` を省略したときはprimary targetを見る、というデフォルトを置けば、この困りごとは解消します。大半のシナリオでは、今までどおり `target` 行を書かずに `interrupts` を使い続けられます。2つ以上のターゲットを宣言したシナリオでも、その恩恵を失わずに済みます。

## 3. どう実現するか

### Interruptモデルへのtarget追加

`bajutsu/common/scenario/models/steps/interrupt.py` の `Interrupt` に `target: str | None = None` を追加します。`Step.target`（`bajutsu/common/scenario/models/steps/step.py:131`）と同じ形の任意フィールドです。ただし省略時の解決先が異なります。その違いをdocstringに明記します。

### 検証ロジック（`_targets.py`）の変更

`_check_target`（`_targets.py:26-46`）は現在、宣言済みターゲットが2つ以上のとき、`target is None` を無条件でエラーにしています。ここに `required: bool = True` 引数を足します。`required=False` のときは、その分岐をスキップして省略を許可します。`target` が設定されている場合の「宣言済みターゲットに含まれるか」というチェックは、`required` の値によらず今までどおり適用します。

`_check_target_requirements`（`_targets.py:80-142`）の128-137行にある一律拒否のブロックを削除します。代わりに、各 `interrupts` エントリを次のように検証します。

```python
for entry in scenario.interrupts:
    _check_target(entry.target, known=known, context="interrupts entry", required=False)
    walk_steps(entry.steps, mode="interrupt")
    _reject_assertion_target(entry.condition, context="interrupts entry: condition")
```

`entry.condition` への `target` は、今までどおり `_reject_assertion_target`（`_targets.py:71-77`）で拒否します。`target` は `Interrupt` 自身が持ち、`condition` には持たせません。`Step` の `assert:` リストや `if` の `condition` に `target` を許さない既存のルールと対称になります。

`Interrupt.steps` 内の各 `Step` には、`_check_step_target`（`_targets.py:49-68`）をそのままでは使えません。この関数は `walk_steps`（`_targets.py:100-122`）が呼びます。現状の `_check_step_target` は、次の2モードしか持ちません。

- トップレベルのstepは、2つ以上のターゲットで必須です。
- `web:` と `app:` の内側は、禁止です。

ここに3つ目のモードを追加します。`Interrupt.steps` 内は常に省略可で、省略時は囲む `Interrupt` の実効targetを継承するというモードです。`_check_step_target` と、それを呼ぶ `walk_steps` の両方に `mode: Literal["top", "nested", "interrupt"]` を持たせます。既存の `inside_web: bool` の代わりです。`"interrupt"` モードでは、`_check_target` を `required=False` で呼びます。

### config-level `interrupts` の扱い

`TargetConfig.interrupts`（`bajutsu/common/config/schema/target_config.py:155`）は、`targets.<name>` の内側にあります。すでに1つのターゲット名に紐づいた設定の内側です。ここで `target` を設定できてしまうと、自分自身の設定の中で別のターゲット名を名指すことになり、意味の通らない状態になります。`_no_component_in_target_steps`（`target_config.py:165-183`）は、`use` を同じ理由で拒否しています。その同じ場所に、`targets.<name>.interrupts` 配下のエントリが `target` を設定していたら拒否するチェックを足します。

このチェックは、エントリ自身の `target` だけでなく、`entry.steps` 内の各 `Step` が持つ `target` も拒否します。`TargetConfig` はシナリオ側の `_check_target_requirements` を通らないからです。対策しなければ、`targets.web.interrupts` 配下の `Step.target` は、どのバリデータにも見られません。`targets.web.interrupts[0].steps[0].target: ios` のような設定が、そのままロードに成功してしまいます。実行時には、`_StepRunner._route`（`_step_runner.py:87`）が、そのStepを `ios` のランナーへ配送します。これは、webの設定が別ターゲットを名指すという、まさにこの単位が防ぐはずの穴です。

走査には、`_no_component_in_target_steps` がすでに組み立てている形をそのまま使います。その形は `[s for entry in self.interrupts for s in entry.steps]` です。

### 実行時の合成（`pipeline.py`）

現在、`_runtime_for`（`bajutsu/common/runner/pipeline.py:1011-1060`）は、1055行目で次のように書いています。

```python
interrupts=[*pool.eff.run_defaults.interrupts, *s.interrupts]
```

この記述は、`scenario.interrupts` を無条件に全ターゲットの `TargetRuntime` へコピーします。`_run_on_lease`（`pipeline.py:1062`〜）も、1150行目で同様に、primaryの `_LoopConfig` へ無条件にコピーしています。

これを変更します。`_routed(s)` が空でないときだけ、宣言済みターゲット名ごとに `(entry.target or primary_target) == そのターゲット名` でフィルタします。`primary_target` には、`_run_on_lease`（`pipeline.py:1175`）がすでに計算している値をそのまま再利用します。その値は `next(iter(self._routed(s)), "")` です。新たに計算し直しません。この値を `_runtime_for` の引数にも足して渡します。

`_routed(s)` が空のときは、フィルタしません。今までどおり、`scenario.interrupts` を無条件にコピーします。`_routed(s)` が空になるのは、次のどちらかの場合です。

- シナリオが `targets` を宣言していない。
- 宣言していても、実行側の設定に `targets.<name>` マップ自体がない。

`_routed`（`pipeline.py:291-297`）のdocstringは、この経路を「単一ターゲットの道はここでも正しい」と呼んでいます。この経路では、`next(iter(self._routed(s)), "")` が空文字列になります。ところが、エントリの `target` は具体的なターゲット名でありえます。宣言済みターゲットがちょうど1つのシナリオで、`interrupts` エントリがその1つを明示している場合です（`_check_target` の `n == 1` 分岐が許します）。

この場合にフィルタを適用すると、条件式 `(entry.target or primary_target) == primary_target` が常に不成立になります。たとえば `("web" or "") == ""` のような形になります。単一ターゲットのシナリオの `interrupts` が、黙って発火しなくなります。これは、検出されず保証もない回帰です。prime directive 2（サイレント障害の禁止）に反します。

config由来のinterrupts（`run_defaults.interrupts`）は別です。各ターゲット自身が持つ設定であり、このフィルタの対象にはしません。今までどおり、無条件にそのターゲットの `TargetRuntime` へ含めます。primaryの `_LoopConfig` も同様です。

### ステップ実行側（`_functions.py`、`_step_runner.py`）

`_config_for`（`_functions.py:1180-1203`）は、`TargetRuntime.interrupts` をそのまま各ターゲットの `_LoopConfig` へ渡します。`run_scenario` 内の `by_target` 構築（同ファイル1283-1294行）も、この `TargetRuntime` を介して、ターゲットごとの `_StepRunner` を作ります。前段でフィルタ済みの `interrupts` を `TargetRuntime` とprimaryの `_LoopConfig` に渡せば、この層は変更しなくて済みます。

`_InterruptGuard`（`_interrupt_guard.py`）は、すでに `_StepRunner` ごとに独立して構築されています（`_step_runner.py:598-605`）。つまりターゲットごとに1つずつ持ちます。ガード自体の構造も、変更しなくて済みます。

`Interrupt.steps` 内の各 `Step` の実行先も、変更しなくて済みます。`_StepRunner._route`（`_step_runner.py:87`）は、`step.target` が設定されているときだけ別のランナーへ配送し直します。省略したときは `self` のままです。つまり、そのエントリの `_InterruptGuard` が発火したランナーです。`_run_recovery`（`_step_runner.py:116`）は、この `self` の上で `entry.steps` を実行します。

「実行時の合成」の単位が、すでに各エントリを正しいランナーの `cfg.interrupts` へ置いています。そのランナーは、そのエントリの実効targetをすでに担っています。実効targetは、次のとおりに決まります。

- 明示があれば、その値です。
- シナリオレベルのエントリで省略なら、primary targetです。
- config-levelのエントリなら、自身が属する設定のターゲットです。

したがって `Interrupt.steps` は、追加の仕組みなしに正しいターゲットへ向かいます。`primary_target` を実効targetへ明示的に差し替える経路も検討しましたが、レビューで問題が見つかったため採りませんでした。理由は4章の代替案表にまとめます。

## 4. 検討した代替案と、採らなかった理由

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| `Interrupt.target` も `Step` や `expect` と同じ「2つ以上のターゲットでは必須」ルールにする | 既存の `_check_target` をそのまま流用し、`target` の扱いをモデル間で揃えます | `Step` が明示を必須にしているのは、1つ1つの操作がどのターゲットへ向かうかを、シナリオの読み手に毎回示すためです。`interrupts` は性質が異なります。マルチターゲットシナリオでも、大半のエントリは主目的のターゲット（primary target）向けであることが多く、すべてに `target` 行を強制すると、単なる定型記述の繰り返しになります |
| `Interrupt.target` を `targets: list[str]` として複数指定できるようにする | 1つのエントリで複数ターゲットを同時に監視できるようにします | 監視は、各ターゲットの `_StepRunner` が独立に持つ `_InterruptGuard` が、ターゲットごとのtreeに対して行います。1エントリで複数ターゲットを束ねても、内部では結局ターゲットごとに展開されます。まとめて書ける以上の恩恵が薄い一方、`Step.target`（単数）との非対称が増え、モデルの理解コストが上がります。同じ条件と手順を複数ターゲットに適用したい場合は、エントリを複製すれば足ります |
| `Interrupt.steps` 内の `Step` は、省略時にシナリオ全体のprimary targetを実行対象にする | recovery手順のデフォルトターゲットを、割り込みが発火した対象ではなく、常にprimary targetにします | 割り込みが発火したターゲットと、それを解除する操作が向かうターゲットは、通常は同一です。省略時に常にprimary targetへ向けると、primary target以外で発火するエントリの解除手順のほぼすべてに、`target` の明示を強いることになります |
| `Interrupt.steps` の実効target（`entry.target or primary_target`）を明示的に計算し、リカバリ実行時に `primary_target` を差し替える | 省略時のルーティング先を、新しい計算式で明示的に決めます | `_StepRunner._route`（`_step_runner.py:87`）は、`target` を明示したStepだけを配送し直します。省略時は、発火元のランナー（`self`）のまま `_run_recovery`（`_step_runner.py:116`）が実行します。実行時の合成（単位「実行時の合成」）が各エントリを正しいランナーの `cfg.interrupts` へ置きさえすれば、この経路は変更なしにすでに動作します。scenario全体の `primary_target` を明示的に注入すると、config-levelのエントリが誤って配送されます。config-levelのエントリの `target` は常に `None` です（前述の「config-level `interrupts` の扱い」を参照）。そのエントリが、自身の設定が属するターゲットではなく、scenarioのprimary targetへ配送されてしまいます |
| `targets.<name>.interrupts` のエントリにも `target` を設定できるようにする | config-levelのinterruptsでも、`Interrupt` の `target` フィールドをそのまま使います | `targets.<name>.interrupts` は、その設定ファイル自身のターゲット名にすでに紐づいて管理されています。そこに別のターゲット名を指す `target` を書けると、自分自身の設定の中で別ターゲットを名指すという、意味の通らない状態を作れてしまいます |

## 5. 作業手順

| # | やること | 触るファイル | 完了条件 | 前提 |
|---|---|---|---|---|
| 1 | `Interrupt` に `target: str \| None = None` を追加し、フォールバック規則をdocstringに書きます | `bajutsu/common/scenario/models/steps/interrupt.py` | `Interrupt(condition=..., target="web")` のようにモデルが `target` を受理します（`pytest tests/scenario/test_models_scenario.py`） | なし |
| 2 | `_check_target` に `required: bool = True` を追加します。`required=False` なら、2つ以上のターゲットでも `target is None` を許容します | `bajutsu/common/scenario/models/scenario/_targets.py` | `_check_target(None, known={"a", "b"}, context="x", required=False)` が例外を出しません | 1 |
| 3 | `_check_target_requirements` の128-137行の一律拒否を削除します。各 `interrupts` エントリを `_check_target(entry.target, known=known, context="interrupts entry", required=False)` で検証します | 同上 | 2つ以上のターゲットを宣言したシナリオで、`target` を指定しない `interrupts` エントリと、宣言済みターゲット名を指定した `interrupts` エントリの両方がロードに成功します。宣言されていないターゲット名を指定したエントリはロードエラーになります（`pytest tests/scenario/test_target_routing.py`） | 2 |
| 4 | `_check_step_target` に `Interrupt.steps` 専用モードを足します。その中のStepは、常に `target` を省略でき、指定時は宣言済みターゲットの中から選べるようにします | `bajutsu/common/scenario/models/scenario/_targets.py` | `Interrupt.steps` 内のStepが `target` を省略してもロードエラーになりません。宣言されていないターゲット名を指定した場合はエラーになります（`pytest tests/scenario/test_target_routing.py`） | 3 |
| 5 | `targets.<name>.interrupts` 配下のエントリ、および `entry.steps` 内の各Stepが `target` を設定していたら、ロード時に拒否するチェックを追加します | `bajutsu/common/config/schema/target_config.py` | `targets.<name>.interrupts` に `target` を持つエントリ、または `steps` 内に `target` を持つStepを書いた設定ファイルが、ロードエラーになります（テストを追加） | 1 |
| 6 | `_target_runtimes` と `_runtime_for` に、`_run_on_lease` がすでに計算している `primary_target`（`next(iter(self._routed(s)), "")`）を引数として渡します。`_routed(s)` が空でないときだけ、各ターゲットの `TargetRuntime.interrupts` を `(entry.target or primary_target) == そのターゲット名` でフィルタします | `bajutsu/common/runner/pipeline.py` | 2つのターゲットを宣言し、片方だけに `target` を明示した `interrupts` を持つシナリオで、そのターゲットの `TargetRuntime.interrupts` にだけ、そのエントリが含まれます（テストを追加） | 3, 4 |
| 7 | `_run_on_lease` のprimaryの `_LoopConfig.interrupts` も、`_routed(s)` が空でないときだけ、同じ規則でフィルタします。`_routed(s)` が空のとき（シナリオが `targets` を宣言していない、または実行側の設定に `targets.<name>` マップがない通常の単一ターゲットのシナリオ）は、今までどおり無条件にコピーします | 同上 | `target` 省略のエントリと、primary target名を明示したエントリの両方がprimaryの `_LoopConfig.interrupts` に含まれ、他のターゲットを指定したエントリは含まれません | 6 |
| 8 | 単一ターゲットのシナリオ（`Scenario.targets` が空、または宣言済みターゲットが1つで実行側に `targets.<name>` マップがない構成）で、`interrupts` が今までどおり発火することを確認する回帰テストを足します | `tests/orchestrator/test_interrupts.py`（または近い既存ファイル） | 単一ターゲットのシナリオの `interrupts` が、この変更の前後で同じように発火し解除されます | 6, 7 |
| 9 | マルチターゲットシナリオでの割り込み発火が、対応するターゲットの `_InterruptGuard` にだけ届くことを確認する結合テストを足します。`targets.<name>.interrupts`（config-level）のエントリについても、その解除手順が自身の設定が属するターゲットのランナー上で実行され、シナリオのprimary targetへ誤って配送されないことを確認します | `tests/orchestrator/test_multi_target_routing.py`（または近い既存ファイル） | 2つのターゲットを宣言し、片方だけに割り込み画面を出すシナリオで、その割り込みが対応するターゲット側でだけ解除されます。config-levelのinterruptsエントリの解除手順も、そのconfigが属するターゲット側で実行されます | 6, 7, 8 |
| 10 | `docs/scenarios.md` の「interrupts」節に、`target` フィールドとそのデフォルトの説明を追加します。「Limits」節にある「2つ以上のターゲットでは `interrupts` を一律拒否する」という現行の記述を、新しい挙動に書き換えます | `docs/scenarios.md` | 「interrupts」節が新しい `target` フィールドを説明し、「Limits」節の一律拒否の記述が新しい挙動の説明に置き換わっています | 6, 7 |
| 11 | `docs/ja/scenarios.md` の対応箇所を日本語で追記、修正します。執筆には `japanese-document-writing` スキルを適用し、textlintを通します | `docs/ja/scenarios.md` | textlintの指摘がゼロになります | 10 |
| 12 | `make check` を実行し、format、lint、typecheck、testがすべて通ることを確認します | なし | `make check` が成功します | 1-11 |

上の12の手順をすべて終えると、2つ以上のターゲットを宣言したシナリオでも `interrupts` が使えるようになります。`target` を省略したエントリはprimary targetを監視し、明示したエントリはそのターゲットを監視します。`Interrupt.steps` は、追加の実装なしに、発火したランナー自身の上で実行されます。`Step` と `expect` の既存ルールは、そのまま残ります。

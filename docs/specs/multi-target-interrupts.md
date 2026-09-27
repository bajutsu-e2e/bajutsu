# マルチターゲットシナリオにおける interrupts の target 対応

> ステータス: ドラフト
> 対象: `bajutsu/common/scenario/models/steps/interrupt.py`、`bajutsu/common/scenario/models/scenario/_targets.py`、`bajutsu/common/runner/pipeline.py`、`bajutsu/common/config/schema/target_config.py`、`docs/scenarios.md`
> 関連: [BE-0428](../../roadmaps/BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md)（マルチターゲットシナリオ実行）、[BE-0314](../../roadmaps/BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md)（interrupts本体）

`interrupts` の各エントリに `target` フィールドを追加する。`Step`・`expect` の `target` と同じ形だが、省略時の扱いが違う。`Step`・`expect` の `target` は、2つ以上のターゲットを宣言したシナリオでは省略できない。`interrupts` の `target` は、その場合でも常に省略できる。省略したエントリは、実行時にそのシナリオの**primary target**を監視する。primary targetとは、実行の基準となる1つのターゲットのことだ。`--target` フラグが名指すターゲット、またはシナリオが自身で `targets` を宣言している場合はその最初の1つから解決される。この変更により、`interrupts` は2つ以上のターゲットを宣言したシナリオでも使えるようになる。現状は、ロード時に一律拒否されている。

## 1. なにをつくるのか

- `Interrupt` モデルに `target: str | None = None` を追加する。
- 宣言済みターゲットが2つ以上のシナリオでも、`interrupts` エントリの `target` は省略できる。省略したエントリは実行時にそのシナリオのprimary targetのtreeを監視する。
- `target` を明示すれば、そのエントリが監視するターゲットを宣言済みターゲットの中から指定できる。
- `Interrupt.steps`（割り込みを解除する手順）内の各 `Step` は、`target` を省略すると囲む `Interrupt` 自身の実効targetを継承する。実効targetとは、明示があればその値、省略ならprimary targetのことだ。シナリオ全体のprimary targetは継承しない。`Step` 側で明示すれば、解除の手順だけ別のターゲットへ向けることもできる。
- `interrupts` 自体を一律拒否している現行の検証を取り除く。この検証は、宣言済みターゲットが2つ以上のシナリオを対象にしている（`bajutsu/common/scenario/models/scenario/_targets.py:128-137`）。

### やらないこと

- `Step.target` と `expect` の `Assertion.target` が持つ「2つ以上のターゲットを宣言したシナリオでは省略不可」という既存の必須ルールは変更しない。この2つは今回のスコープ外に据え置く。
- `Interrupt` が1つのエントリで複数ターゲットを同時に監視できるようにする拡張（たとえば `targets: list[str]`）は作らない。`target` は単一の値に限る。
- `targets.<name>.interrupts`（設定ファイル側、アプリ全体のinterruptsデフォルト値）のエントリに `target` を設定できるようにはしない。設定した場合はロード時にエラーとする。

## 2. なぜつくるのか

[BE-0428](../../roadmaps/BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md) は `Scenario.targets`・`Step.target`・`expect` の `Assertion.target` を実装した。これにより、iOS Simulator・web・Androidのような複数のバックエンドを1つのシナリオの中で同時に扱えるようになった。ただし `interrupts` だけはスコープ外に残された。条件（`condition`）がどのターゲットのtreeを監視すべきかを決める方法が、まだなかったからだ。宣言済みターゲットが2つ以上のシナリオでは、`interrupts` エントリを1つでも書くとロード時に拒否される（`_targets.py:128-137`）。

この制限のもとでは、アプリとwebを同時に操作するようなマルチターゲットシナリオで困りごとが起きる。OS権限プロンプトやCookie同意バナーのような、予測不能な割り込み画面が現れても `interrupts` で処理できない。シナリオ作者は該当する操作の直前ごとに、手作業で `if` 分岐を挟むしかない。あるいは、そもそもマルチターゲット化を諦めるかだ。`target` を省略したときはprimary targetを見る、というデフォルトを置けばこの困りごとは解消する。大半のシナリオでは、今までどおり `target` 行を書かずに `interrupts` を使い続けられる。2つ以上のターゲットを宣言したシナリオでも、その恩恵を失わずに済む。

## 3. どう実現するか

### Interruptモデルへのtarget追加

`bajutsu/common/scenario/models/steps/interrupt.py` の `Interrupt` に `target: str | None = None` を追加する。`Step.target`（`bajutsu/common/scenario/models/steps/step.py:131`）と同じ形の任意フィールドである。ただし省略時の解決先が異なる。その違いをdocstringに明記する。

### 検証ロジック（`_targets.py`）の変更

`_check_target`（`_targets.py:26-46`）は現在、宣言済みターゲットが2つ以上のとき `target is None` を無条件でエラーにしている。ここに `required: bool = True` 引数を足し、`required=False` のときはその分岐をスキップして省略を許可する。`target` が設定されている場合の「宣言済みターゲットに含まれるか」というチェックは、`required` の値によらず今までどおり適用する。

`_check_target_requirements`（`_targets.py:80-142`）の128-137行にある一律拒否のブロックを削除し、各 `interrupts` エントリを次のように検証する。

```python
for entry in scenario.interrupts:
    _check_target(entry.target, known=known, context="interrupts entry", required=False)
    walk_steps(entry.steps, mode="interrupt")
    _reject_assertion_target(entry.condition, context="interrupts entry: condition")
```

`entry.condition` への `target` は今までどおり `_reject_assertion_target`（`_targets.py:71-77`）で拒否する。`target` は `Interrupt` 自身が持ち、`condition` には持たせない。`Step` の `assert:` リストや `if` の `condition` に `target` を許さない既存のルールと対称になる。

`Interrupt.steps` 内の各 `Step` には、`_check_step_target`（`_targets.py:49-68`）をそのままでは使えない。この関数は `walk_steps`（`_targets.py:100-122`）が呼ぶ。現状の `_check_step_target` は2モードしか持たない。「トップレベルのstepは2つ以上のターゲットで必須」と「`web:`/`app:` 内は禁止」の2つだ。ここに3つ目のモードを追加する。「`Interrupt.steps` 内は常に省略可で、省略時は囲む `Interrupt` の実効targetを継承する」というモードだ。`_check_step_target` と、それを呼ぶ `walk_steps` の両方に `mode: Literal["top", "nested", "interrupt"]` を持たせる。既存の `inside_web: bool` の代わりだ。`"interrupt"` モードでは `_check_target` を `required=False` で呼ぶ。

### config-level `interrupts` の扱い

`TargetConfig.interrupts`（`bajutsu/common/config/schema/target_config.py:155`）は `targets.<name>` の内側にある。すでに1つのターゲット名に紐づいた設定の内側だ。ここで `target` を設定できてしまうと、自分自身の設定の中で別のターゲット名を名指すことになる。これは意味の通らない状態だ。`_no_component_in_target_steps`（`target_config.py:165-183`）は `use` を同じ理由で拒否している。その同じ場所に、`targets.<name>.interrupts` 配下のエントリが `target` を設定していたら拒否するチェックを足す。

このチェックは、エントリ自身の `target` だけでなく、`entry.steps` 内の各 `Step` が持つ `target` も拒否する。理由は同じだ。`TargetConfig` はシナリオ側の `_check_target_requirements` を通らない。対策しなければ、`targets.web.interrupts` 配下の `Step.target` はどのバリデータも見ない。`targets.web.interrupts[0].steps[0].target: ios` のような設定は、そのままロードに成功してしまう。実行時には `_StepRunner._route`（`_step_runner.py:87`）が、そのStepを `ios` のランナーへ配送する。これは、webの設定が別ターゲットを名指すという、まさにこの単位が防ぐはずの穴だ。走査には、`_no_component_in_target_steps` がすでに組み立てている形をそのまま使う。その形は `[s for entry in self.interrupts for s in entry.steps]` だ。

### 実行時の合成（`pipeline.py`）

現在、`_runtime_for`（`bajutsu/common/runner/pipeline.py:1011-1060`）は1055行目で次のように書いている。

```python
interrupts=[*pool.eff.run_defaults.interrupts, *s.interrupts]
```

これは `scenario.interrupts` を無条件に全ターゲットの `TargetRuntime` へコピーしている。`_run_on_lease`（`pipeline.py:1062`〜）も1150行目で同様に、primaryの `_LoopConfig` へ無条件にコピーしている。

これを変更する。`_routed(s)` が空でないときだけ、宣言済みターゲット名ごとに `(entry.target or primary_target) == そのターゲット名` でフィルタする。`primary_target` には、`_run_on_lease`（`pipeline.py:1175`）がすでに計算している値をそのまま再利用する。その値は `next(iter(self._routed(s)), "")` だ。新たに計算し直さない。この値を `_runtime_for` の引数にも足して渡す。

`_routed(s)` が空のときは、フィルタしない。今までどおり `scenario.interrupts` を無条件にコピーする。`_routed(s)` は、ある条件で空になる。シナリオが `targets` を宣言していないとき、または宣言していても実行側の設定に `targets.<name>` マップ自体がないときだ。`_routed`（`pipeline.py:291-297`）のdocstringは、この経路を「単一ターゲットの道はここでも正しい」と呼ぶ。この経路では `next(iter(self._routed(s)), "")` は空文字列になる。だが、エントリの `target` は具体的なターゲット名でありうる。宣言済みターゲットがちょうど1つのシナリオで、`interrupts` エントリがその1つを明示している場合がそれだ（`_check_target` の `n == 1` 分岐が許す）。ここでフィルタを適用すると、`(entry.target or primary_target) == primary_target` が `("web" or "") == ""` のように常に不成立になる。単一ターゲットのシナリオの `interrupts` が、黙って発火しなくなる。これは検出・保証のどちらもされない回帰であり、prime directive 2（サイレント障害の禁止）に反する。

config由来のinterrupts（`run_defaults.interrupts`）は別だ。各ターゲット自身が持つ設定であり、このフィルタの対象にはしない。今までどおり、無条件にそのターゲットの `TargetRuntime` へ含める。primaryの `_LoopConfig` も同様である。

### ステップ実行側（`_functions.py`・`_step_runner.py`）

`_config_for`（`_functions.py:1180-1203`）は `TargetRuntime.interrupts` をそのまま渡す。渡す先は各ターゲットの `_LoopConfig` だ。`run_scenario` 内の `by_target` 構築（同ファイル1283-1294行）も、この `TargetRuntime` を介してターゲットごとの `_StepRunner` を作る。前段でフィルタ済みの `interrupts` を `TargetRuntime`／primaryの `_LoopConfig` に渡せば、この層は変更しなくてよい。`_InterruptGuard`（`_interrupt_guard.py`）は、すでに `_StepRunner` ごとに独立して構築されている（`_step_runner.py:598-605`）。つまりターゲットごとに1つずつ持つ。ガード自体の構造も変更しなくてよい。

`Interrupt.steps` 内の各 `Step` の実行先も、変更しなくてよい。`_StepRunner._route`（`_step_runner.py:87`）は `step.target` が設定されているときだけ別のランナーへ配送し直す。省略したときは `self`、つまりそのエントリの `_InterruptGuard` が発火したランナーのままだ。`_run_recovery`（`_step_runner.py:116`）は、この `self` の上で `entry.steps` を実行する。上の単位が、すでに各エントリを正しいランナーの `cfg.interrupts` へ置いている。そのランナーは、すでにそのエントリの実効targetを担うランナーだ。実効targetとは、明示があればその値、シナリオレベルのエントリで省略ならprimary target、config-levelのエントリなら自身が属する設定のターゲットのことだ。`Interrupt.steps` は、追加の仕組みなしに正しいターゲットへ向かう。

`primary_target` を実効targetへ明示的に差し替える経路を検討したが、レビューで問題が見つかり採らなかった。理由は4章の代替案表にまとめる。

## 4. 検討した代替案と、採らなかった理由

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| `Interrupt.target` も `Step`・`expect` と同じ「2つ以上のターゲットでは必須」ルールにする | 既存の `_check_target` をそのまま流用し、`target` の扱いをモデル間で揃える | `Step` が明示を必須にしているのは、1つ1つの操作がどのターゲットへ向かうかをシナリオの読み手に毎回示すためである。`interrupts` は性質が異なり、マルチターゲットシナリオでも大半のエントリは主目的のターゲット（primary target）向けであることが多く、そのすべてに `target` 行を強制すると単なる定型記述の繰り返しになる |
| `Interrupt.target` を `targets: list[str]` として複数指定できるようにする | 1つのエントリで複数ターゲットを同時に監視できるようにする | 監視自体は各ターゲットの `_StepRunner` が独立に持つ `_InterruptGuard` がターゲットごとのtreeに対して行うため、1エントリで複数ターゲットを束ねても内部的には結局ターゲットごとに展開される。まとめて書ける以上の恩恵が薄い一方、`Step.target`（単数）との非対称が増え、モデルの理解コストが上がる。同じ条件・手順を複数ターゲットに適用したい場合はエントリを複製すればよい |
| `Interrupt.steps` 内の `Step` は、省略時にシナリオ全体のprimary targetを実行対象にする | recovery手順のデフォルトターゲットを、割り込みが発火した対象ではなく常にprimary targetにする | 割り込みが発火したターゲットと、それを解除する操作が向かうターゲットは同一であるのが通常のケースである。省略時に常にprimary targetへ向けると、primary target以外で発火するエントリの解除手順のほぼすべてに `target` の明示を強いることになる |
| `Interrupt.steps` の実効target（`entry.target or primary_target`）を明示的に計算し、リカバリ実行時に `primary_target` を差し替える | 省略時のルーティング先を新しい計算式で明示的に決める | `_StepRunner._route`（`_step_runner.py:87`）は `target` を明示したStepだけを配送し直し、省略時は発火元のランナー（`self`）のまま `_run_recovery`（`_step_runner.py:116`）が実行する。実行時の合成（単位「実行時の合成」）が各エントリを正しいランナーの `cfg.interrupts` へ置きさえすれば、この経路は変更なしにすでに動作する。scenario全体の `primary_target` を明示的に注入すると、config-levelのエントリ（`target` は常に `None`。前述の「config-level `interrupts` の扱い」参照）が、自身の設定が属するターゲットではなくscenarioのprimary targetへ誤って配送される |
| `targets.<name>.interrupts` のエントリにも `target` を設定できるようにする | config-levelのinterruptsでも `Interrupt` の `target` フィールドをそのまま使う | `targets.<name>.interrupts` はすでにその設定ファイル自身のターゲット名に紐づいて管理されている。そこに別のターゲット名を指す `target` を書けると、自分自身の設定の中で別ターゲットを名指すという、意味の通らない状態が作れてしまう |

## 5. 作業手順

| # | やること | 触るファイル | 完了条件 | 前提 |
|---|---|---|---|---|
| 1 | `Interrupt` に `target: str \| None = None` を追加し、フォールバック規則をdocstringに書く | `bajutsu/common/scenario/models/steps/interrupt.py` | `Interrupt(condition=..., target="web")` のようにモデルが `target` を受理する（`pytest tests/scenario/test_models_scenario.py`） | — |
| 2 | `_check_target` に `required: bool = True` を追加し、`required=False` なら2つ以上のターゲットでも `target is None` を許容する | `bajutsu/common/scenario/models/scenario/_targets.py` | `_check_target(None, known={"a", "b"}, context="x", required=False)` が例外を出さない | 1 |
| 3 | `_check_target_requirements` の128-137行の一律拒否を削除し、各 `interrupts` エントリを `_check_target(entry.target, known=known, context="interrupts entry", required=False)` で検証する | 同上 | 2つ以上のターゲットを宣言したシナリオで、`target` を指定しない `interrupts` エントリと、宣言済みターゲット名を指定した `interrupts` エントリの両方がロードに成功する。宣言されていないターゲット名を指定したエントリはロードエラーになる（`pytest tests/scenario/test_target_routing.py`） | 2 |
| 4 | `_check_step_target` に `Interrupt.steps` 専用モードを足し、その中のStepは常に `target` 省略可、指定時は宣言済みターゲットの中から選べるようにする | `bajutsu/common/scenario/models/scenario/_targets.py` | `Interrupt.steps` 内のStepが `target` を省略してもロードエラーにならない。宣言されていないターゲット名を指定した場合はエラーになる（`pytest tests/scenario/test_target_routing.py`） | 3 |
| 5 | `targets.<name>.interrupts` 配下のエントリ、および `entry.steps` 内の各Stepが `target` を設定していたらロード時に拒否するチェックを追加する | `bajutsu/common/config/schema/target_config.py` | `targets.<name>.interrupts` に `target` を持つエントリ、または `steps` 内に `target` を持つStepを書いた設定ファイルがロードエラーになる（テストを追加） | 1 |
| 6 | `_target_runtimes`／`_runtime_for` に、`_run_on_lease` がすでに計算している `primary_target`（`next(iter(self._routed(s)), "")`）を引数として渡し、`_routed(s)` が空でないときだけ各ターゲットの `TargetRuntime.interrupts` を `(entry.target or primary_target) == そのターゲット名` でフィルタする | `bajutsu/common/runner/pipeline.py` | 2つのターゲットを宣言し、片方だけに `target` を明示した `interrupts` を持つシナリオで、そのターゲットの `TargetRuntime.interrupts` にだけそのエントリが含まれる（テストを追加） | 3, 4 |
| 7 | `_run_on_lease` のprimaryの `_LoopConfig.interrupts` も、`_routed(s)` が空でないときだけ同じ規則でフィルタする。`_routed(s)` が空のとき（シナリオが `targets` を宣言していない、または実行側の設定に `targets.<name>` マップがない通常の単一ターゲットのシナリオ）は、今までどおり無条件にコピーする | 同上 | `target` 省略のエントリと、primary target名を明示したエントリの両方がprimaryの `_LoopConfig.interrupts` に含まれ、他のターゲットを指定したエントリは含まれない | 6 |
| 8 | 単一ターゲットのシナリオ（`Scenario.targets` が空、または宣言済みターゲットが1つで実行側に `targets.<name>` マップがない構成）で `interrupts` が今までどおり発火することを確認する回帰テストを足す | `tests/orchestrator/test_interrupts.py`（または近い既存ファイル） | 単一ターゲットのシナリオの `interrupts` が、この変更の前後で同じように発火し解除される | 6, 7 |
| 9 | マルチターゲットシナリオでの割り込み発火が、対応するターゲットの `_InterruptGuard` にだけ届くことを確認する結合テストを足す。`targets.<name>.interrupts`（config-level）のエントリについても、その解除手順が自身の設定が属するターゲットのランナー上で実行され、シナリオのprimary targetへ誤って配送されないことを確認する | `tests/orchestrator/test_multi_target_routing.py`（または近い既存ファイル） | 2つのターゲットを宣言し、片方だけに割り込み画面を出すシナリオで、その割り込みが対応するターゲット側でだけ解除される。config-levelのinterruptsエントリの解除手順も、そのconfigが属するターゲット側で実行される | 6, 7, 8 |
| 10 | `docs/scenarios.md` の「interrupts」節に `target` フィールドとそのデフォルトの説明を追加し、「Limits」節にある「2つ以上のターゲットでは `interrupts` を一律拒否する」という現行の記述を新しい挙動に書き換える | `docs/scenarios.md` | 「interrupts」節が新しい `target` フィールドを説明し、「Limits」節の一律拒否の記述が新しい挙動の説明に置き換わっている | 6, 7 |
| 11 | `docs/ja/scenarios.md` の対応箇所を日本語で追記・修正する。執筆には `japanese-document-writing` スキルを適用し、textlintを通す | `docs/ja/scenarios.md` | textlintの指摘がゼロになる | 10 |
| 12 | `make check` を実行し、format・lint・typecheck・testがすべて通ることを確認する | — | `make check` が成功する | 1-11 |

上の12の手順をすべて終えると、2つ以上のターゲットを宣言したシナリオでも `interrupts` が使えるようになる。`target` を省略したエントリはprimary targetを監視し、明示したエントリはそのターゲットを監視する。`Interrupt.steps` は、追加の実装なしに、発火したランナー自身の上で実行される。`Step`・`expect` の既存ルールはそのまま残る。

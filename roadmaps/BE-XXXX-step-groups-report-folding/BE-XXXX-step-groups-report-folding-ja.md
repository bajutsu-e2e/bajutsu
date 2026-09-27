[English](BE-XXXX-step-groups-report-folding.md) · **日本語**

# BE-XXXX — ステップを名前付き区画にまとめ、report.htmlで折りたたむ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-step-groups-report-folding-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | [#2059](https://github.com/bajutsu-e2e/bajutsu/pull/2059) |
| トピック | Scenario authoring features |
<!-- /BE-METADATA -->

## はじめに

新しい `group:` ステップで、`steps:` の下に連続するステップへ名前を付けられるようにします。
`group:` は `name` と入れ子の `steps:` を受け取り、`run` が始まる前にその入れ子のステップ列へ
展開されます。決定的なランナーは `group` 自体を一度も見ません。`use:`
（[BE-0030](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps-ja.md)）が
すでに受けている扱いと同じです。展開で生まれた各ステップは、どのグループから来たかの記録を保持
します。`report.html` は、グループのステップを1つの折りたたみ区画にまとめ、その見出しの下に
表示します。見出しにはグループ名とステップ数が載ります。ステップがすべて成功しているグループは、
デフォルトで畳んだ状態になります。失敗したステップを含むグループは、自動的に展開されます。既存の
「全展開」「全折りたたみ」操作も、シナリオ内のグループを一緒に切り替えます。

## 動機

`report.html` のステップ表はフラットです。ステップごとに1行が並びます（`steprow` /
`steptable`、[report.html.j2](../../bajutsu/templates/report.html.j2)）。連続するステップの
まとまりを畳む手段はありません。ステップ数の多いシナリオでは、ページ全体をスクロールしないと
読み終えられません。実行のうち1つのフェーズだけが失敗した場合でも、この点は変わりません。

連続するステップに名前を付ける仕組みに近い既存の概念が3つありますが、それぞれ別の問題を解決
しています。

`from:`（[BE-0044](../BE-0044-scenario-provenance/BE-0044-scenario-provenance-ja.md)）は、
`record` がどの自然言語の発話からステップを正規化したかを記録します。すでに、連続する同じ値の
並びを1つのラベルにまとめる仕組みを持っています。`from:` は記録のためのメタデータであり、著者が
選ぶ区画名ではありません。連続を「ラベル付け」はしますが、「折りたたみ」はしません。

`use:` / `components:`
（[BE-0030](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps-ja.md)、
[BE-0422](../BE-0422-inline-scenario-components/BE-0422-inline-scenario-components-ja.md)）は、
再利用可能なステップ列に名前を付けます。`expand_components`
（[expand.py](../../bajutsu/common/scenario/expand.py)）は、`run` の前に `use:` の呼び出しを
完全に平坦化します。その呼び出しの境界を示す痕跡は、report生成の時点では一切残りません。
`report.html` は、呼び出されたコンポーネントのステップを、ただの連続する行として描画します。

`if` / `forEach` / `web` / `app` は、自分自身の入れ子 `steps:` を持つ、実行時のコンテナステップ
です。入れ子のステップは、親と同じ番号付けを共有します。この制約は `rows.py` の2箇所のコメントに
記録されています（[rows.py](../../bajutsu/common/report/rows.py)）。4つのどれも、折りたたんでは
表示されません。

`group:` が導入されると、著者は連続するステップに一度だけ名前を付けます。`report.html` は、
成功しているグループを1行の要約の裏に隠します。失敗を含むグループは、最初から展開された状態で
表示されます。長い実行を眺めるレビュアーは、シナリオ内の全ステップを展開しなくても、どの名前付き
区画が失敗したかを見分けられます。

## 詳細設計

### スキーマ: `Group` モデルと `Step.group` フィールド

`bajutsu/common/scenario/models/steps/group.py` を追加します。`ForEach` と同じ形にします。
`name` と `steps` はどちらも空を許しません。

```python
class Group(_Model):
    name: str = Field(min_length=1)
    steps: list[Step] = Field(min_length=1)
```

`Step`（[step.py:71](../../bajutsu/common/scenario/models/steps/step.py)）に
`group: Group | None = None` フィールドを追加します。`_STEP_ACTIONS` は、`Step.model_fields`
から `_MODIFIERS` を除いた集合として導出されます
（[step.py:161](../../bajutsu/common/scenario/models/steps/step.py)）。このフィールドの追加だけ
で、`group` は1つのアクションとして認識されます。既存の `_one_action` バリデータも、これを対象に
含めます。1つのステップは、`group` を `tap` や `use` など他のアクションと組み合わせられません。

`group` は `_CONTROL_FLOW_ACTIONS`（[_base.py:33](../../bajutsu/common/scenario/models/_base.py)）
に足します。`if_` / `for_each` / `web` / `app` と同じ扱いにします。`group` ステップは単一の実行
結果を持ちません。`capture` や `extract` を書ける意味がありません。既存の
`_no_modifiers_on_control_flow` バリデータが、そのまま両方を拒否します。新しいコードは要りません。

`use` は今この一覧に入っていません。`capture` や `extract` を書いても、展開時に黙って捨てます。
`group` はこの挙動に合わせません。`group: { name: ..., capture: [...] }` と書いた著者は、
`capture` が効くと思い込みやすいからです。黙って捨てる挙動は、事故を招きます。`use` 側の同じ
挙動をどう直すかは、この項目の範囲外とし、別のissueへ切り出します
（[#2057](https://github.com/bajutsu-e2e/bajutsu/issues/2057)）。

`Group.name` と `Group.steps` 以外のフィールド（ステップ側の `name`、`from`、`target`）も、
後述する展開が捨てます。`use` が `use.component` と `use.with_` 以外を捨てる
（[expand.py:76](../../bajutsu/common/scenario/expand.py)以下）のと同じ扱いです。

### 展開後もグループの由来を運ぶ

展開後のステップは、`report.html` に届くまで2つのことを覚えておく必要があります。1つはどの
グループから来たか、もう1つはそのグループの何回目の呼び出しから来たかです。`Step` に2つの内部
フィールドを追加します。著者が手で書くことは想定していません。

```python
report_group: str | None = Field(default=None, alias="_reportGroup")
report_group_id: int | None = Field(default=None, alias="_reportGroupId")
```

`report_group` は見出しに出す表示名です。`report_group_id` は `group` 呼び出し1回ごとに振る
通し番号です。後述の `_fold_groups` は、連続する行の区間を `report_group_id` の一致で見つけます。
名前の一致では見つけません。同じ名前の `group` を離れた場所で2回使ったとき、その2つの区間が
たまたま隣り合う行になっても、別々の見出しとして描画するためです。

両方とも `_MODIFIERS`（[_shared.py:8](../../bajutsu/common/scenario/models/steps/_shared.py)）に
加えます。どちらも普通の `Step` フィールドです。`_interp_steps` が `use` の置換で行う
`model_dump` / `model_validate` の往復を、そのまま生き延びます
（[expand.py](../../bajutsu/common/scenario/expand.py)）。追加の配線は要りません。

フィールドバリデータで値を拒否することはしません。`Annotated[..., SkipJsonSchema()]` だけで、
両方を著者向けの項目から隠します。`bajutsu schema` の出力にこの2つは載りません。`expand()` は
`model_copy(update=...)` でこの2つを設定します。これにより、`group:` を含むシナリオが展開された
あと、`report_group` は*設定済み*の非デフォルト値になります。`run` 経路は、同じダンプを2回
再検証します。`redact_totp_secrets` は、実行を終えたすべてのシナリオに対して走ります。エビデンス
のスナップショットを書き出す直前に走ります。`load_run` も、自分自身の `scenario.yaml` を同じ
ように再読み込みします。`exclude_defaults=True` は、設定済みのフィールドをどちらのダンプからも
落としません。`None` 以外を拒否するバリデータを足すと、この内部の往復そのものを拒否してしまい
ます。`serialize.py` の `redact_totp_secrets` のdocstringに、同じ形の問題の記録があります。
BE-0401 が `systemAlertHandling.labels` で踏んだのと同じ形です。著者が `_reportGroup` を手で
書いて偽のfoldを作れる余地は残ります。ただし、著者がその項目を見つける手段であるスキーマ自体に、
この項目は載りません。

この役割の最初の設計案は、既存の `step_lines` の仕組み
（[raw_source.py:36](../../bajutsu/common/scenario/raw_source.py)）に沿った並行配列でした。この
案は採りませんでした。`use` の置換は、呼び出し元のステップオブジェクトを丸ごと捨てます。残すのは
`use.component` と `use.with_` だけです。配列の添字で管理すると、展開の結果生まれた新しいステップ
群への対応づけを、展開処理の外側で別途組み立てる必要があります。`Step` 自身のフィールドにして
おけば、`group` の中で `use` を呼んだときの伝播に必要なのは、後述する展開の再帰へ引数を足す
だけです。

### 展開: `group` と `use` を1つの再帰で扱う

`expand_components` の内部にある `expand()`
（[expand.py:70](../../bajutsu/common/scenario/expand.py)）に、2つの引数を足します。
`group_ctx: str | None = None` は、現在の再帰呼び出しを包んでいるグループを表します。
`group_id: int | None = None` は、その呼び出しの通し番号を表します。通し番号は、`expand.py`
がモジュール単位で1つ持つカウンタ（`itertools.count()`）から払い出します。`expand_components`
の呼び出しごとに閉じたカウンタではありません。

`setup` プレリュードは、自分専用の `expand_components` 呼び出しで展開されます。この呼び出しは、
`apply_setups` がプレリュードのステップをシナリオ本体の前へ差し込むより前に実行されます
（[run/cli.py:292](../../bajutsu/run/cli.py)）。呼び出しごとのカウンタだと、プレリュード末尾の
`group` とシナリオ本体先頭の `group` の両方に `group_id=0` を配ってしまいます。この2つが行
リストで隣り合えば、1つのfoldへ誤って混ざります。モジュール単位のカウンタは値を繰り返さない
ため、この事故は起きません。値そのものが実行のたびに同じである必要はありません。1回のreport
生成の外側で、この番号に依存するものは何もないからです。

ループの先頭では、次のように分岐します。

- `st.group is not None` のとき: すでに `group_ctx` が立っていれば、入れ子としてエラーを
  送出します。`use` を経由して `group` の中に紛れ込んだ入れ子も、ここで捕まえます。そうでなければ、
  カウンタから新しい `group_id` を払い出し、`expand(st.group.steps, stack, resolve,
  group_ctx=st.group.name, group_id=group_id)` を呼びます。結果はそのまま `out` へ足します。
- `st.use is None` かつ `st.group is None`（普通のステップ）のとき: `out` へ足します。
  `group_ctx` が `None` でなければ、`report_group=group_ctx` と `report_group_id=group_id` を
  付けて複製します。`None` ならそのまま足します。
- `st.use is not None` のとき: 置換後のステップを `expand(substituted, [*stack, ref], nested,
  group_ctx=group_ctx, group_id=group_id)` として展開し、両方を持ち越します。`group` の中で
  呼んだ `use` は、こうしてコンポーネントが展開して生まれた全ステップに、同じグループ名とidを
  付けます。

この再帰を起動する呼び出しは4か所です。`scenario.steps`、`scenario.before`、各 `after` ルール
の `steps`、各 `interrupts` エントリの `steps` です
（[expand.py:104](../../bajutsu/common/scenario/expand.py) 以下）。4つとも `group_ctx=None` と
`group_id=None` から始まり、他の変更は要りません。

### 入れ子の禁止をどこで検出するか

`group` の入れ子には2つの形があります。1つは `group.steps` の中に直接書いた `group` です。もう
1つは `if.then` / `if.else_` / `for_each.steps` / `web.steps` / `app.steps` の中に書いた `group`
です。どちらも、ロード後の `Scenario` の木を静的に辿れば見つかります。`scenario.steps` /
`scenario.before` / 各 `rule.steps` / 各 `entry.steps` から、この6か所すべて（`group.steps` 自身
を含む）へ再帰するバリデータを1つ追加します。`group` が見つかれば、どちらの形の入れ子かを言って
拒否します。

このバリデータには届かない、もう1つの入れ子があります。`use` で呼んだコンポーネントの `steps` の
中に `group` があり、そのコンポーネントを `group` の中から呼ぶ場合です。ロード時点の `Scenario`
はコンポーネントの中身を持たないため、静的なバリデータでは見えません。ここは、上で述べた
`expand()` 内の実行時チェック（`group_ctx` がすでに立っているところへ `st.group` が来たら
エラー）が拾います。

`expand()` は `if.then` / `if.else_` / `for_each.steps` / `web.steps` / `app.steps` の中までは
再帰しません。静的なバリデータをすり抜けて、かつコンポーネント経由でこれらの中に紛れ込んだ
`group` は、展開されないまま `run` のstep loopへ届きます。中身のないアクションとして、
`AssertionError` で停止します。これは `use` が現在すでに持つ制約と同じ範囲であり、今回はここ
までとします。

### 複数ターゲットシナリオでの `group` の扱い

`_targets.py:58` はすでに `if step.use is not None and len(known) >= 2:` を読み、`targets` を
2つ以上宣言したシナリオで `use` を拒否しています。理由は、展開が `use` ステップ自身の `target`
フィールドを捨ててしまうからです
（[_targets.py:61](../../bajutsu/common/scenario/models/scenario/_targets.py) 以下）。展開は
`group` ステップの `target` フィールドも同じように捨てます。同じ条件式に `or step.group is not
None` を足します。エラーメッセージは、`use` と `group` のどちらが引っかかったかを名指しするよう
組み立て直します。`use:` という固定文言のままだと、`group` が原因のとき誤った案内を出します。

### `run` が見るアクション一覧からの除外

`_RUNTIME_ACTIONS`（[_registry.py:19](../../bajutsu/common/orchestrator/actions/_registry.py)）
は、`STEP_ACTIONS` から `"use"` だけを除いた集合です。`group` を `Step` に足すと、そのままでは
`_RUNTIME_ACTIONS` にも入ります。この式を `a not in ("use", "group")` に変えます。変えなければ、
展開に失敗して残った `group` だけでなく、正しく展開されたはずの `group` が万一残った場合にも、
対応するハンドラのないまま実行を試みることになります。

### ターゲット設定ファイルの `before` / `after` / `interrupts`

`_no_component_in_target_steps`
（[target_config.py:166](../../bajutsu/common/config/schema/target_config.py)）は、ターゲット
設定の `before` / `after` / `interrupts` に `use` を書くことをすでに拒否しています。理由は、
ターゲット設定はシナリオファイルの読み込み時に展開されないためです。`group` も同じ経路を通る
ため、同じ理由で同じ拒否が要ります。このバリデータの対象アクションに `group` を加えます。

### `report.html` への到達

`html_report` / `write_report` が受け取る `scenarios` は、すでに展開済みの `Scenario` です。
`scenario_dict(s)`（[html.py:44](../../bajutsu/common/report/html.py)）は、それをエイリアス
付きでダンプします。`report_group` と `report_group_id` は、`_reportGroup` / `_reportGroupId`
というキーで、各ステップの定義dict（`rows.py` が `plan[i]` と呼ぶもの）に届きます。追加の配線は
要りません。

`_step_detail` / `_step_run_row` / `_step_skip_row`
（[rows.py:30](../../bajutsu/common/report/rows.py) 以下）に、`group: str | None` と
`group_id: int | None` の2つの引数を足します。位置は `from_` と並べます。呼び出し側の
`_merged_rows` / `_phase_rows` / `_after_rows`
（[rows.py:382](../../bajutsu/common/report/rows.py) 以下）は、`step_def.get("_reportGroup")` と
`step_def.get("_reportGroupId")` をそのまま渡します。`grouped_provenance`
（[from_grouping.py](../../bajutsu/common/report/from_grouping.py)）は、ラベル表示のため、
繰り返す `from:` の値を最初の行以外で消します。同じグループ呼び出しに属する行は、すべて同じ
`group_id` を持たせます。あとの処理が、連続する行の区間がどこから始まりどこで終わるかを見つける
ためです。

`steprow` マクロは、1つのステップに対して最大4つの `<tr>` を出します。本体行に加えて、
`alertrow` / `actrow` / `genrow` が条件次第で続きます。ステップの行が持つ `expand` キーは、
常に `None` です（[rows.py:201](../../bajutsu/common/report/rows.py)、
[rows.py:276](../../bajutsu/common/report/rows.py)）。5つ目の種類 `nxdetail` を出すのは、
ステップの行ではなく、ネットワーク交信行だけです
（[rows.py:340](../../bajutsu/common/report/rows.py)、
[rows.py:375](../../bajutsu/common/report/rows.py)）。交信行はスコープ上、どのグループにも
属しません。したがって、fold側には `nxdetail` の状態と衝突する心配がありません。`nxdetail` が
すでに使っている素の `hidden` 属性を、そのまま使い回せます。折りたたみは、本体行だけでなく、
1つのステップが持ちうる最大4つの行すべてに、同じ `group_id` を付ける必要があります。

純粋関数を1つ追加します。`_fold_groups(rows: list[dict]) -> list[dict]` のような形です。上の
3つの行生成関数それぞれの最後で、1回だけ呼びます。この関数は、`group_id` が連続する行の区間
ごとに、見出し行を1つ挿入します。ネットワーク交信行や未実行ステップの割り込みで、同じ
`group_id` の区間が複数に分かれることは受け入れます。それぞれの区間が独立した見出しを持ちます。
1つの `group` 呼び出しがreport上で複数の折りたたみに分かれて見えることがある、という限界です。
区間に失敗した行が1つでもあれば、その区間には `hidden` を付けません。入力と出力はどちらも
素のdictなので、Jinjaを介さず `pytest` で直接検証できます。

`report.html.j2` の `steprow` マクロ
（[report.html.j2:42](../../bajutsu/templates/report.html.j2)）を拡張し、見出し行を描画する分岐
と、本体行および付随行への `hidden` 属性・`group_id` 属性の付与を足します。トグル用の
JavaScriptは `bajutsu/templates/report.js` に足します。`toggleAll` の定義は
[report.js:114](../../bajutsu/templates/report.js) にあり、`report.html.j2` 側にあるのは
`onclick` 呼び出しだけです。見出し行クリックで、対応する `group_id` を持つ行の `hidden` を
切り替える関数と、既存の `toggleAll` がすべてのグループも一緒に開閉する変更を、この2つに足し
ます。`bajutsu/templates/report.css` に見出し行のスタイルを足します。

### 展開前のシナリオを読む道具への影響

`load_scenario_file` は `use` と `group` のどちらも展開しません。
`bajutsu/serve/operations/audit.py:53` と `bajutsu/common/lint.py` の `provenance_coverage` は、
いずれもこの関数でシナリオを読み、その `steps` を歩いて集計や表示をします。`group` を導入する
と、この歩き方は `group.steps` の中まで踏み込みません。`group` でくるんだステップを素通りし、
集計対象から漏らしてしまいます。各ツールのステップの歩き方に、`group.steps` の中へ降りる分岐を
足します。

`bajutsu/analysis/trace.py:285` も `load_scenario_file` を呼びますが、対象は
`run_dir / "scenario.yaml"` です。これは `pipeline.py` が、すでに展開済みの `scenarios` から
書き出すファイルです（[pipeline.py:1899](../../bajutsu/common/runner/pipeline.py) 以下）。
`group` ステップがそこに残ることはないため、`trace.py` に対応する変更は要りません。`use` にも
どのツールに対しても対応する修正は要りません。展開されるまで中身が見えないのは同じだからです。

## 検討した代替案

- **`use:` / `components:` の呼び出しを、そのままfold単位にする。** `group` を導入する代わりに、
  `report.html` が呼び出されたコンポーネントの名前で畳む案です。不採用です。短い、1回限りの
  ステップのまとまりを名前付きコンポーネントへ切り出すだけでも、BE-0030とBE-0422がシナリオを
  またぐ再利用のために作ったparamsとファイルスコープの仕組みを、著者に強います。この仕組みは、
  長い`report.html`が求める以上に重いものです。`use:` の呼び出し自体を畳む拡張は、後日の課題と
  して残します。`group` と `use` はすでに組み合わせられます。`group` の中に `use` を置けるから
  です。
- **`from:` のグルーピング表示を折りたたみへ拡張する。** BE-0044の `grouped_provenance` は、
  すでに連続する同じ `from:` の値を1つのラベルにまとめています。これも不採用です。`from:` は、
  `record` がどの自然言語の発話からステップを正規化したかを記録する欄であり、著者が選ぶ区画名
  ではありません。両方の用途に使い回すと、`report.html` の読み手が見ているものが、`record` に
  よるステップの由来の記録なのか、著者自身の整理なのか、区別できなくなります。
- **`if` / `forEach` / `web` / `app` と同じ、実行時のコンテナステップとして実装する。** これも
  不採用です。実行時コンテナの入れ子ステップは、すでに親と同じ番号付けを共有します。`rows.py`
  は、こうしたステップの後続行が誤って対応づけられる、既知の原因としてこれを記録しています
  （[rows.py:442](../../bajutsu/common/report/rows.py) 以下）。`group` は `run` 時点で何にも
  影響しません。`use` と同じくコンパイルタイムで展開してしまえば、オーケストレータには触れずに
  済み、この既知の制約を広げることもありません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `Group` モデルと `Step.group` フィールドを追加します（`step.py`、新規 `group.py`）。
      両フィールドを `min_length=1` にします。`_one_action` が単独の `group` ステップを
      受け入れることを確認します
- [x] `group` を `_CONTROL_FLOW_ACTIONS` に足します。既存の `_no_modifiers_on_control_flow`
      バリデータが `capture` / `extract` を拒否することを確認します
- [x] 内部フィールド `report_group` / `report_group_id`
      （エイリアス `_reportGroup` / `_reportGroupId`）を `Step` に追加し、両方を `_MODIFIERS`
      へ登録します。両方を `SkipJsonSchema` にして `bajutsu schema` から隠します
      （値を拒否するバリデータは足しません。理由はログを参照してください）
- [x] `expand.py` にモジュール単位のidカウンタを追加します
      （`setup` プレリュード専用の `expand_components` 呼び出しとも共有します）。`expand()`
      の再帰へ `group_ctx` /
      `group_id` を足し、`group` の展開、`use` の置換を通した伝播、`group_ctx` が立っている
      状態で `st.group` に出会ったときのエラーを実装します
- [x] `Scenario` の木を辿るロード時バリデータを追加します
      （`group.steps`、`if.then` / `if.else_`、`for_each.steps`、`web.steps`、`app.steps` を
      含む）。見つかった `group` は、どちらの形の入れ子かを名指しして拒否します
- [x] `_targets.py` の複数ターゲットチェックを拡張し、`targets` を2つ以上宣言したシナリオでの
      `group` 利用も拒否します。エラーメッセージは実際のアクションを名指しします
- [x] `_registry.py` の `_RUNTIME_ACTIONS` の除外対象に `group` を加えます
- [x] `target_config.py` の `_no_component_in_target_steps` の対象アクションに `group` を
      加えます
- [x] `rows.py` の行生成関数へ `group` / `group_id` を通し、`_fold_groups` を追加します。
      付随行（`alertrow` / `actrow` / `genrow`）にも本体行と同じidを付けます
- [x] 折りたたみ表示を実装します。`report.html.j2` に見出し行と `hidden` / `group_id` 属性、
      `report.js` にトグル用の関数と `toggleAll` との連動、`report.css` に見出し行のスタイルを
      足します
- [x] `audit.py` のステップの歩き方に、`group.steps` の中へ降りる分岐を足します。`lint.py` の
      `provenance_coverage` はそのままにしました。理由はログを参照してください
- [x] `bajutsu lint` / `bajutsu schema` が新しい `group` アクションを認識することを確認します
- [x] `docs/scenarios.md`、`docs/dsl-grammar.md`、`docs/reporting.md`、および各 `docs/ja/`
      ミラーを更新します
- [x] 上記を高速suiteでカバーします。`make check` をgreenにします

`use` ステップが `capture` / `extract` / `name` / `from` / `target` を黙って捨てる既存の挙動を
どうするかは、この項目のスコープに含めません。別のGitHub Issueとして切り出します
（[#2057](https://github.com/bajutsu-e2e/bajutsu/issues/2057)）。

**ログ**

- `audit.py` のセレクタ抽出とfinding抽出は、`step.group.steps` の中へ再帰するようになりました。
  対象は `_step_selectors` / `_nested_step_selectors` / `_step_findings` です。`if` / `forEach`
  へのすでにある対応と同じ形です。`lint.py` の `provenance_coverage` はそのままにしました。この
  関数はシナリオの最上位の `steps` しか数えておらず、`if` / `forEach` の中へも元から降りていま
  せん。`group` だけを個別に直すと、既存のこの限界に対して一貫性のない対応になってしまいます。
- この項目の以前のバージョンでは、フィールドバリデータを追加していました。著者が指定した
  `_reportGroup` / `_reportGroupId` を拒否するバリデータです。オープンしたPRへのCIレビューで、
  `run` 経路自体の往復に問題が見つかりました。`redact_totp_secrets` と `load_run` は、どちらも
  同じダンプを再検証します。そのダンプは、設定済みの `report_group` を正当に運んでいます。
  バリデータはそのダンプも拒否していました。その結果、`group:` を含むシナリオの `run` が、
  自分自身のエビデンスのスナップショット書き出し中に失敗していました。この項目では、そのバリ
  データを削除しました。`SkipJsonSchema` だけで、両方のフィールドを著者向けの項目から隠します。
  `tests/test_group_steps.py` と `tests/scenario/test_models_steps.py` が、この往復をカバー
  します。
- `_fold_groups` は、foldの `data-group-id` 属性を、共有された `group_id` そのものにして
  いました。1回の `group:` 呼び出しから分かれた2つのfoldが、同じキーで描画されていました。
  これは、失敗したグループの通常の形です。ネットワーク交信行や未実行の末尾が、1回の呼び出しを
  2つのfoldへ分割することがあります。片方のfoldの見出しを開閉すると、もう片方の行も一緒に
  開閉していました。同じCIレビューがこれを見つけました。キーは、行リストの中でのfold自身の
  位置と組み合わせた値になりました。この組み合わせは、foldごとに一意です。
  `tests/report/test_group_folding.py` の回帰テストが、これをカバーしています。
- 新規ファイルの `_group_nesting.py` は、測定どおり100％として `coverage-floors.json` に
  登録しました。

## 参考

- [docs/specs/step-groups-report-folding.md](../../docs/specs/step-groups-report-folding.md) —
  この項目が要約している、詳細な技術設計（日本語）
- [BE-0044 — Scenario provenance](../BE-0044-scenario-provenance/BE-0044-scenario-provenance-ja.md)
  — この項目の代替案の節が区別している `from:` のグルーピング
- [BE-0030 — Parameterized shared steps](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps-ja.md)、
  [BE-0422 — Inline, scenario-local components](../BE-0422-inline-scenario-components/BE-0422-inline-scenario-components-ja.md)
  — `group` があえて再利用しない `use:` / `components:` の仕組み
- [docs/scenarios.md](../../docs/scenarios.md#step-grammar-steps)、
  [docs/reporting.md](../../docs/reporting.md#reporthtml) — この項目が更新するドキュメント

# ステップのグループ化とreport.htmlでの折りたたみ表示

> ステータス: ドラフト
> 対象: `bajutsu/common/scenario/models/steps/`、`bajutsu/common/scenario/expand.py`、`bajutsu/common/scenario/models/scenario/`、`bajutsu/common/orchestrator/actions/_registry.py`、`bajutsu/common/config/schema/target_config.py`、`bajutsu/common/report/`、`bajutsu/templates/report.html.j2` / `report.js` / `report.css`、`bajutsu/analysis/audit/_functions.py`
> 関連: [BE-0044](../../roadmaps/BE-0044-scenario-provenance/BE-0044-scenario-provenance.md)（provenance）、[BE-0030](../../roadmaps/BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps.md) / [BE-0422](../../roadmaps/BE-0422-inline-scenario-components/BE-0422-inline-scenario-components.md)（コンポーネント）、[docs/scenarios.md](../scenarios.md)、[docs/reporting.md](../reporting.md)

新しい`group:`ステップで、`steps:`の中の連続するステップに意味のある単位の名前を付けて書けるようにする。`group:`は`run`の前にコンパイルタイムで平坦なステップ列へ展開されるため、決定性には影響しない。展開後の各ステップには元のグループ名を残し、`report.html`ではそのグループ名でステップ行を折りたたんで表示する。

## 1. なにをつくるのか

シナリオの`steps:`に、次の形の`group`アクションを追加する。

```yaml
steps:
  - group:
      name: ログイン
      steps:
        - tap: { id: auth.open }
        - type: { text: "${vars.user}", into: { id: auth.user } }
        - tap: { id: auth.submit }
  - tap: { id: home.tab }
```

`group`は`name`（必須）と`steps`（必須、1件以上）だけを持つ。`use`のようなパラメータ化や別ファイル化は行わない。ロード時に`group`ステップは自分の`steps`の中身へその場で置き換わり、`run`が実際に実行するステップ列には`group`という概念自体が残らない。

`report.html`は、この展開のときに各ステップへ残るグループ名をもとに、連続する同じグループのステップ行をひとまとめにし、グループ名とステップ数を示す見出し行の下に折りたたんで表示する。折りたたみの初期状態は次のとおりである。

- グループ内のすべてのステップが成功しているとき、見出し行だけを表示し、中身は畳んだ状態にする。
- グループ内に失敗したステップが1つでもあるとき、そのグループは最初から展開された状態で表示する。
- 既存の「全展開」「全折りたたみ」ボタン（シナリオ単位の`<details class="scn">`を開閉する）は、グループの折りたたみ状態も一緒に切り替える。

グループに属さないステップは、今までどおりフラットな行として表示する。

### やらないこと

- **グループの入れ子は対象外にする。** `group`の`steps`の中にさらに`group`を書くことは許さず、バリデーションで拒否する。1シナリオ内はフラットな1階層のみとする。
- **`if` / `forEach` / `web` / `app`の入れ子の中での`group`利用は対象外にする。** 対象には`then` / `else`も含む。後述のとおり、この制約は`use`が現在すでに持つ制約と同じ範囲である。今回新たに導入するものではない。
- **ネットワーク交信行（request / response）のグループ折りたたみは対象外にする。** 折りたたみの対象はステップ行だけとする。`_merged_rows`が時系列で割り込ませる交信行は、常にグループの外として表示する。
- **`targets`を2つ以上宣言した複数ターゲットシナリオでの`group`利用は対象外にする。** `use`が現在受けている制約（後述）と同じ理由で、まず拒否する。
- **`group`使用シナリオでの元のYAML行番号ヒント（`report.html`の行番号表示）の維持は対象外にする。** `use`や`setup`のプレリュードを使ったシナリオは、現在も行番号ヒントが表示されない。その既存の制約をそのまま引き継ぐ。

## 2. なぜつくるのか

`report.html`は、シナリオごとに開閉可能な`<details class="scn">`を持つ（[report.html.j2:125](../../bajutsu/templates/report.html.j2)）。「全展開」「全折りたたみ」ボタンも備える。これを開くと、中のステップは単一の`<table class="sttbl">`に一行ずつフラットに並ぶ。ステップ数の多いシナリオでは、このテーブルが縦に長くなり、`report.html`を開いたときのスクロール量が増える。ステップをグループ化して折りたたむ機能がないため、この長さを縮める手段は現状ない。

意味のある単位でまとめて畳む仕組みに近い既存の概念は3つあるが、いずれも今回の用途には合わない。

`from:`（BE-0044）は、連続する同じ`from:`文字列を1つのラベルとして表示する仕組み（`grouped_provenance`、[from_grouping.py](../../bajutsu/common/report/from_grouping.py)）をすでに持つ。ただしこれは`record`がどの自然言語の発話からステップを生成したかを記録するための欄であり、ラベル表示だけで折りたたみはしない。人間が意味のある単位名を書く用途とは性格が異なる。

`use:` / `components:`（BE-0030 / BE-0422）は、名前を付けたステップ列を呼び出せる点で一番近い。ただし`run`前にコンパイルタイムで完全展開され（`expand_components`、[expand.py](../../bajutsu/common/scenario/expand.py)）、report生成の時点では呼び出しの境界情報が残らない。`use`で呼んだひとまとまりも、現状の`report.html`ではただの連続行にしか見えない。

`if` / `forEach` / `web` / `app`は、実行時に入れ子の`steps:`を持つコンテナステップである。ただし入れ子ステップは親と同じ連番空間に平坦化され、コンテナに続く行の取り違えという既知の制約がコード中のコメントに明記されている（[rows.py:442](../../bajutsu/common/report/rows.py)、[rows.py:476](../../bajutsu/common/report/rows.py)）。折りたたみ表示もない。

`report.html`が長くなりすぎるという困りごとを放置すると、シナリオのステップ数が増えるたびにレビューや失敗調査での負担が積み上がる。ステップを意味のある単位でまとめて畳めれば、開いた直後に見えるステップ数を絞り込め、失敗したグループだけが自動で開くことで、失敗調査に必要な情報は失わずに済む。

## 3. どう実現するか

### スキーマ: `Group`モデルと`Step.group`フィールド

`bajutsu/common/scenario/models/steps/group.py`に、`ForEach`（[for_each.py](../../bajutsu/common/scenario/models/steps/for_each.py)）と同じ形の新しいモデルを追加する。`name`と`steps`はどちらも空を許さない。

```python
class Group(_Model):
    name: str = Field(min_length=1)
    steps: list[Step] = Field(min_length=1)
```

`Step`（[step.py:71](../../bajutsu/common/scenario/models/steps/step.py)）に`group: Group | None = None`フィールドを追加する。`_STEP_ACTIONS`は`Step.model_fields`から`_MODIFIERS`を除いた集合として自動的に導出される（[step.py:161](../../bajutsu/common/scenario/models/steps/step.py)）。このフィールド追加だけで、`group`は1つのアクションとして認識される。`_one_action`バリデータの対象にも自動的に含まれる。他のアクションと同時には書けない。

`group`は`_CONTROL_FLOW_ACTIONS`（[_base.py:33](../../bajutsu/common/scenario/models/_base.py)）に足す。`if_` / `for_each` / `web` / `app`と同じ扱いにする。`group`ステップは単一の実行結果を持たないため、`capture`や`extract`を書ける意味がない。既存の`_no_modifiers_on_control_flow`バリデータが、そのまま`capture` / `extract`の記述を拒否する。新しいコードは要らない。

`use`は今この一覧に入っていない。`capture` / `extract`を書いても、展開時に黙って捨てる。`group`をこの挙動に合わせなかった理由は、事故の起きやすさにある。`group: { name: ..., capture: [...] }`と書いた著者は、`capture`が効くと思い込みやすい。`use`側の同じ挙動をどう直すかは、この項目の範囲外とし、別のissueへ切り出す。

`Group.name`と`Group.steps`以外のフィールド（ステップ側の`name`、`from`、`target`）も、後述する展開が捨てる。`use`が`use.component` / `use.with_`以外を捨てる（[expand.py:76](../../bajutsu/common/scenario/expand.py)以下）のと同じ扱いである。

### 展開後もグループの由来を運ぶ内部フィールド

展開後のステップは、`report.html`に届くまで2つのことを覚えておく必要がある。1つはどのグループから来たか、もう1つはそのグループの何回目の呼び出しから来たかである。`Step`に2つの内部フィールドを追加する。著者が手で書くことは想定していない。

```python
report_group: str | None = Field(default=None, alias="_reportGroup")
report_group_id: int | None = Field(default=None, alias="_reportGroupId")
```

`report_group`は見出しに出す表示名で、`report_group_id`は`group`呼び出し1回ごとに振る通し番号である。後述の`_fold_groups`は、連続する行の区間を`report_group_id`の一致で見つける。名前の一致では見つけない。同じ名前の`group`を離れた場所で2回使ったとき、その2つの区間がたまたま隣り合う行になっても、別々の見出しとして描画するためである。

両方とも`_MODIFIERS`（[_shared.py:8](../../bajutsu/common/scenario/models/steps/_shared.py)）に加える。ステップオブジェクトの通常のフィールドとして`model_dump` / `model_validate`の往復に乗るため、後述する`use`展開との合成でも特別な配線を要らない。

値そのものを拒否するバリデータは足さない。`expand()`は`model_copy(update=...)`でこの2つを設定するため、`report_group`は*設定済み*の非デフォルト値になる。`run`経路は、この値を含んだダンプを2回再検証する。1つは`redact_totp_secrets`である。もう1つは`load_run`による`scenario.yaml`の再読み込みである。`redact_totp_secrets`は、展開済みの全シナリオに対して走る。`exclude_defaults`は*設定済み*の値を落とさない。そのため、どちらの再検証にも`_reportGroup` / `_reportGroupId`が届く。ここで値を拒否するバリデータを足すと、この2回の再検証そのものが失敗する。`group:`を含むシナリオの`run`は、エビデンスの書き出し中に落ちる。これはBE-0401が`systemAlertHandling.labels`で踏んだ罠と同じ形である。`serialize.py`の`redact_totp_secrets`のdocstringに記録がある。著者が`_reportGroup`を手で書けてしまう余地は残る。ただし`Annotated[..., SkipJsonSchema()]`が、この2つを`bajutsu schema`の出力から著者向けの項目として隠す。そのため、実際に踏む経路ではない。

側路（`step_lines`のような、行番号を並行配列で運ぶ方式、[raw_source.py:36](../../bajutsu/common/scenario/raw_source.py)）ではなくこの方式を選ぶ理由は、`group`の中に`use`が入れ子になったときの伝播にある。`use`ステップの展開は、置き換え元の`use`ステップオブジェクトそのものを捨て、コンポーネント定義側の`steps`だけを使う（[expand.py:76](../../bajutsu/common/scenario/expand.py)〜）。並行配列で管理すると、展開の結果生まれた新しいステップ群への対応づけを、展開処理の外側で別途組み立てる必要がある。`Step`自身のフィールドにしておけば、後述する`expand()`の再帰に引数を足すだけで、`group`の中にある`use`呼び出しの展開結果にも伝播できる。

### 展開: `group`と`use`を1つの再帰で扱う

`expand_components`内部の`expand(steps, stack, resolve)`（[expand.py:70](../../bajutsu/common/scenario/expand.py)）に、2つの引数を足す。`group_ctx: str | None = None`は、現在どのグループの中にいるかを表す。`group_id: int | None = None`は、その呼び出しの通し番号を表す。通し番号は、`expand.py`がモジュール単位で1つ持つカウンタ（`itertools.count()`）から払い出す。`expand_components`の呼び出しごとに閉じたカウンタではない。

`setup`プレリュードは、自分専用の`expand_components`呼び出しで展開される。この呼び出しは、`apply_setups`がプレリュードのステップをシナリオ本体の前へ差し込むより前に実行される（[run/cli.py:292](../../bajutsu/run/cli.py)）。呼び出しごとのカウンタだと、プレリュード末尾の`group`とシナリオ本体先頭の`group`の両方に`group_id=0`を配ってしまう。この2つが行リストで隣り合えば、1つのfoldへ誤って混ざる。モジュール単位のカウンタは値を繰り返さないため、この事故は起きない。値そのものが実行のたびに同じである必要はない。1回のreport生成の外側で、この番号に依存するものは何もないからである。

ループの先頭には、`st.group`のケースを足す。

- `st.group is not None`のとき: まず、現在すでに`group_ctx`が`None`でなければ、入れ子の`group`としてエラーを送出する。これが、下で述べるもう1つのバリデータでは検出できない、`use`経由で`group`の中に紛れ込む入れ子を捕まえる最後の場所である。そうでなければ、カウンタから新しい`group_id`を払い出し、`expand(st.group.steps, stack, resolve, group_ctx=st.group.name, group_id=group_id)`を呼ぶ。結果は`out`へそのまま足す。
- `st.use is None`かつ`st.group is None`の通常のステップは、`out`へ足す。`group_ctx`が`None`でなければ、`report_group=group_ctx`と`report_group_id=group_id`を付けて複製する。`None`ならそのまま足す。
- `st.use is not None`のときは、既存のコンポーネント置換のあと、結果のステップ列を展開する。展開は`expand(substituted, [*stack, ref], nested, group_ctx=group_ctx, group_id=group_id)`として行う。同じ`group_ctx`と`group_id`を持ち越すことで、グループの中で`use`を呼んだときも、コンポーネントが展開して生まれたステップ全部に同じグループ名とidが付く。

`expand()`を呼ぶ箇所は4つある。`scenario.steps`自身、`scenario.before`、各`rule.steps`（after）、各`entry.steps`（interrupts）である（[expand.py:104](../../bajutsu/common/scenario/expand.py)以下）。いずれも`group_ctx=None`と`group_id=None`から始まる。呼び出し側の書き換えは要らない。

### 入れ子の禁止をどこで検出するか

`group`の入れ子には、2つの形がある。1つは`group.steps`の中に直接書いた`group`で、もう1つは`if` / `forEach` / `web` / `app`の入れ子`steps`（`then` / `else`を含む）の中に書いた`group`である。どちらも、シナリオのロード時に静的に検出できる。ロード後の`Scenario`の木を、`scenario.steps` / `scenario.before` / 各`rule.steps` / 各`entry.steps`から再帰的に辿るバリデータを1つ追加する。この再帰は、`if.then` / `if.else_` / `for_each.steps` / `web.steps` / `app.steps` / `group.steps`のすべてに入り、そこに`group`があれば、どちらの形の入れ子かを言って拒否する。

このバリデータには届かない、もう1つの入れ子がある。`use`で呼んだコンポーネントの`steps`の中に`group`があり、そのコンポーネントを`group`の中から呼ぶ場合である。ロード時点の`Scenario`はコンポーネントの中身を持たないため、静的なバリデータでは見えない。ここは、上で述べた`expand()`内の実行時チェック（`group_ctx`がすでに立っているところへ`st.group`が来たらエラー）が拾う。

`expand()`は`if.then` / `if.else_` / `for_each.steps` / `web.steps` / `app.steps`の中までは再帰しない。そのため、静的なバリデータをすり抜けて、かつコンポーネント経由でこれらの中に紛れ込んだ`group`は、展開されないまま`run`のstep loopへ届き、`AssertionError`で停止する。これは`use`が現在すでに持つ制約と同じ範囲であり、今回はここまでとする。

### 複数ターゲットシナリオでの`group`の扱い

`_targets.py:58`の`if step.use is not None and len(known) >= 2:`は、`targets`を2つ以上宣言したシナリオでの`use`利用を拒否している。理由は、`use`ステップの`target`が展開で捨てられてしまうためである（[_targets.py:61](../../bajutsu/common/scenario/models/scenario/_targets.py)以下のコメント）。`group`ステップ自身が持つ`target`も、同じく展開で捨てられる。同じ条件式に`or step.group is not None`を足す。エラーメッセージは、`use`と`group`のどちらが引っかかったかを名指しするよう、文言を組み立て直す。`use:`という固定文言をそのまま流用すると、`group`が原因のとき誤った案内を出す。

### `run`が見るアクション一覧からの除外

`bajutsu/common/orchestrator/actions/_registry.py:19`の`_RUNTIME_ACTIONS`は、`STEP_ACTIONS`から`"use"`だけを除いた集合である。`group`を`Step`に足すと、そのままでは`_RUNTIME_ACTIONS`にも入ってしまう。`group`は`use`と同じくrunの前に消えるアクションなので、この式を`a not in ("use", "group")`に変える。変えなければ、展開に失敗して残った`group`ステップだけでなく、正しく展開されたはずの`group`が万一残った場合にも、オーケストレータが対応するハンドラを持たない状態で実行を試みることになる。

### ターゲット設定ファイルの`before` / `after` / `interrupts`

`bajutsu/common/config/schema/target_config.py:166`の`_no_component_in_target_steps`は、ターゲット設定の`before` / `after` / `interrupts`に`use`を書くことを拒否している。理由は、ターゲット設定はシナリオファイルの読み込み時に展開されず、`use`が中身のないアクションのまま`run`へ届いてしまうためである。`group`も同じ経路を通るため、同じ理由で同じ拒否が要る。このバリデータの対象アクションに`group`を加える。

### `report.html`への到達

`html_report` / `write_report`が受け取る`scenarios`は、展開済みの`Scenario`である。`scenario_dict(s)`（`scenario_render_inputs`、[html.py:44](../../bajutsu/common/report/html.py)）は、そのままエイリアス付きで`model_dump`する。`report_group`と`report_group_id`は、`_reportGroup`・`_reportGroupId`というキーで、各ステップの定義dict（`rows.py`が`plan[i]`と呼ぶもの）に自然に現れる。

`rows.py`の`_step_detail` / `_step_run_row` / `_step_skip_row`（[rows.py:30](../../bajutsu/common/report/rows.py)以下）に、`group: str | None`と`group_id: int | None`の2つの引数を足す。位置は`from_`と並べる。呼び出し側の`_merged_rows` / `_phase_rows` / `_after_rows`（[rows.py:382](../../bajutsu/common/report/rows.py)以下）は、`step_def.get("_reportGroup")`と`step_def.get("_reportGroupId")`をそのまま渡す。`grouped_provenance`（`from:`用、[from_grouping.py](../../bajutsu/common/report/from_grouping.py)）とは違い、同じグループ呼び出しに属する行にはすべて同じ`group_id`を持たせる。理由は、連続する行の区間をあとで検出するためである。最初の行だけに残す「創発的な」縮約はしない。

`steprow`マクロは、1つのステップに対して最大4つの`<tr>`を出す。本体行に加えて、`alertrow` / `actrow` / `genrow`が条件次第で続く。ステップの行が持つ`expand`キーは、常に`None`である（[rows.py:201](../../bajutsu/common/report/rows.py)、[rows.py:276](../../bajutsu/common/report/rows.py)）。5つ目の種類`nxdetail`を出すのは、ステップの行ではなく、ネットワーク交信行だけである（[rows.py:340](../../bajutsu/common/report/rows.py)、[rows.py:375](../../bajutsu/common/report/rows.py)）。交信行はスコープ上、どのグループにも属さない。したがって、fold側には`nxdetail`の状態と衝突する心配がない。`nxdetail`がすでに使っている素の`hidden`属性を、そのまま使い回せる。折りたたみは、本体行だけでなく、1つのステップが持ちうる最大4つの行すべてに、同じ`group_id`を付ける必要がある。

その3つの関数が作るフラットな行リストに対して、後処理を行う新しい純粋関数を1つ追加する（`_fold_groups(rows: list[dict]) -> list[dict]`のような形を想定）。この関数は、`group_id`が連続する行の区間ごとに見出し行を1つ挿入する。同じ`group_id`の区間が、ネットワーク交信行や未実行ステップの割り込みによって複数の区間に分かれることは受け入れる。それぞれの区間が独立した見出しを持つ。1つの`group`呼び出しがreport上で複数の折りたたみに分かれて見えることがある、という限界として明記する。区間に失敗した行が1つでもあれば、その区間の各行に`hidden`を付けない。`_merged_rows` / `_phase_rows` / `_after_rows`は、それぞれが作ったフラットな行リストの最後に、この関数を1回通すだけでよい。入力と出力はどちらも素のdictのリストであるため、Jinjaを介さずpytestで直接検証できる。

`report.html.j2`のsteprowマクロは、見出し行を描画する分岐と、本体行および付随行への`hidden`属性・`group_id`属性の付与を持つ。トグル用のJavaScriptは`bajutsu/templates/report.js`に足す（`toggleAll`の定義は[report.js:114](../../bajutsu/templates/report.js)にあり、`report.html.j2`側にあるのは`onclick`呼び出しだけである）。見出し行クリックで、対応する`group_id`を持つ行の`hidden`を切り替える処理と、既存の`toggleAll`がすべてのグループも一緒に開閉する変更を、この2つの関数に足す。CSSは`bajutsu/templates/report.css`に見出し行のスタイルを足す。

### 展開前のシナリオを読む道具への影響

`load_scenario_file`は、`use`や`group`を展開しない。`bajutsu/analysis/audit/_functions.py`の選択子とfinding抽出は、この関数でシナリオを読み、その`steps`を歩いて集計や表示をする。`group`を導入すると、この歩き方は`group.steps`の中まで踏み込まない。`group`でくるんだステップを素通りし、集計対象から漏らしてしまう。ステップの歩き方に、`group`を透過的に扱う（`group`に出会ったら、その`steps`の中へ降りる）分岐を足す。

`bajutsu/common/lint.py`の`provenance_coverage`は対象外とする。この関数はシナリオ最上位の`steps`しか数えておらず、`if` / `forEach`の中へも元から降りていない。`group`だけを個別に直すと、この既存の限界に対して一貫性のない対応になる。

`bajutsu/analysis/trace.py:285`も`load_scenario_file`を呼ぶが、対象は`run_dir / "scenario.yaml"`である。これは`pipeline.py`が、すでに展開済みの`scenarios`から書き出すファイルである（[pipeline.py:1899](../../bajutsu/common/runner/pipeline.py)以下）。`group`ステップがそこに残ることはないため、`trace.py`に対応する変更は要らない。`use`にもどのツールに対しても対応する修正は要らない。展開されるまで中身が見えないのは同じだからである。

## 4. 検討した代替案と、採らなかった理由

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| `use` / `components:`の呼び出し単位をそのままfold単位にする | 既存のコンポーネント機構をそのまま流用し、`use: { component: login }`と呼ぶだけでreport側がコンポーネント名で自動的に折りたたむ | 一回限りの整理目的でも、別名の定義と再利用の抽象化（ファイル分割や`params`の検討）を強制することになり、「report.htmlが長くなりすぎる」という動機に対して重い。将来、コンポーネント呼び出しも合わせて折りたたみたくなったときに、`group`と共存させる形で別途拡張する余地は残る |
| `from:`のグルーピング表示を折りたたみへ拡張する | BE-0044の`grouped_provenance`（連続する同じ`from:`を1つのラベルにまとめる仕組み）を、ラベル表示だけでなく折りたたみ表示にも使う | `from:`は「`record`がどの自然言語の発話からこのステップを生成したか」を記録する欄であり、人間が意味のある単位名を書く用途とは性格が異なる。`from:`は技術的には手書きできるが、フィールドの意味を混在させると、reportを読む側が「これはAIの記録か、人間が付けた整理用の名前か」を判別できなくなる |
| `if` / `forEach` / `web` / `app`と同じ、実行時に存在するコンテナステップとして実装する | `group`を`run`時点でオーケストレータのstep loopが直接辿るコンテナステップにする | 実行時コンテナには既知の制約がある。子ステップの番号付けが親と同じカウンタを共有し、コンテナに続く行が誤って対応づけられる（[rows.py:442](../../bajutsu/common/report/rows.py)、[rows.py:476](../../bajutsu/common/report/rows.py)）。`group`は`run`に一切影響しない表示目的の整理であり、`use`と同じコンパイルタイム展開にすれば、オーケストレータのコードに触れずに済み、この既知の制約を新たに広げることもない |

## 5. 作業手順

| # | やること | 触るファイル | 完了条件 | 前提 |
|---|---|---|---|---|
| 1 | `Group`モデルを追加し、`Step`に`group: Group \| None`フィールドを足す。`Group.name` / `Group.steps`は`min_length=1`にする | `bajutsu/common/scenario/models/steps/group.py`（新規）、`bajutsu/common/scenario/models/steps/step.py` | `group`を含むステップが`_one_action`バリデーションを通り、他のアクションと同時には書けないこと、`name`や`steps`が空だとロードに失敗することをユニットテストで確認する | — |
| 2 | `group`を`_CONTROL_FLOW_ACTIONS`に加え、既存の`_no_modifiers_on_control_flow`が`capture` / `extract`を拒否することを確認する | `bajutsu/common/scenario/models/_base.py` | `group`ステップに`capture`や`extract`を書いたシナリオのロードが失敗することをユニットテストで確認する | 1 |
| 3 | 内部フィールド`report_group`（エイリアス`_reportGroup`）と`report_group_id`（エイリアス`_reportGroupId`）を`Step`に足し、`_MODIFIERS`へ登録する。両方を`SkipJsonSchema`にして`bajutsu schema`の出力から隠す。値そのものは拒否しない。`expand()`が`model_copy(update=...)`で設定した値は*設定済み*の非デフォルト値になり、`run`経路がその後2回再検証する（`redact_totp_secrets`、`load_run`による`scenario.yaml`の再読み込み）ため、通常の`model_validate`でも通す必要がある | `bajutsu/common/scenario/models/steps/step.py`、`bajutsu/common/scenario/models/steps/_shared.py` | `_STEP_ACTIONS`にこの2つが含まれないこと、`bajutsu schema`の出力にこの2つが載らないこと、`group`を含むシナリオが`expand`後に`redact_totp_secrets`を経由してもエラーなく再検証できることをユニットテストで確認する | 1 |
| 4 | `expand.py`にモジュール単位のidカウンタを追加する（`setup`プレリュード専用の`expand_components`呼び出しとも共有する）。`expand()`に`group_ctx` / `group_id`引数を足し、`st.group`ケースの展開、`use`展開結果への伝播、`group_ctx`がすでに立っている状態で`st.group`に出会ったときの入れ子エラーを実装する | `bajutsu/common/scenario/expand.py` | グループ単体、グループの中に`use`がある場合、`use`経由でグループの中にグループが紛れ込む場合、`setup`プレリュードとシナリオ本体の両方に`group`がある場合のそれぞれで、`report_group` / `report_group_id`と入れ子エラーの発生がユニットテストで期待どおりであることを確認する | 1, 3 |
| 5 | ロード後の`Scenario`の木を`scenario.steps` / `before` / `after`各ruleの`steps` / `interrupts`各entryの`steps`から再帰的に辿り、`if.then` / `if.else_` / `for_each.steps` / `web.steps` / `app.steps` / `group.steps`の中に直接書かれた`group`を拒否するバリデータを追加する | `bajutsu/common/scenario/models/scenario/`配下の該当バリデータ | それぞれの禁止パターンを含むシナリオのロードが、どちらの形の入れ子かを説明するエラーメッセージで失敗することをユニットテストで確認する | 1 |
| 6 | `targets`を2つ以上宣言したシナリオでの`group`利用を拒否する。エラーメッセージは`use` / `group`のどちらが原因かを名指しする | `bajutsu/common/scenario/models/scenario/_targets.py` | 複数ターゲット宣言と`group`を同時に使うシナリオのロードが、`group`を名指ししたエラーメッセージで失敗することをユニットテストで確認する | 1 |
| 7 | `_RUNTIME_ACTIONS`の除外対象に`group`を加える | `bajutsu/common/orchestrator/actions/_registry.py` | `_RUNTIME_ACTIONS`に`group`が含まれないことをユニットテストで確認する | 1 |
| 8 | `_no_component_in_target_steps`の対象アクションに`group`を加える | `bajutsu/common/config/schema/target_config.py` | ターゲット設定の`before` / `after` / `interrupts`に`group`を書いた設定のロードが、`use`と同じ理由で失敗することをユニットテストで確認する | 1 |
| 9 | `rows.py`の`_step_detail` / `_step_run_row` / `_step_skip_row`に`group` / `group_id`引数を足し、`_merged_rows` / `_phase_rows` / `_after_rows`から`_reportGroup` / `_reportGroupId`を渡す | `bajutsu/common/report/rows.py` | `plan[i]`に`_reportGroup` / `_reportGroupId`があるとき、対応する行dictに同じ値が入ることをユニットテストで確認する | 4 |
| 10 | フラットな行リストへ、`group_id`の連続する区間ごとにグループ見出し行を挿入し、本体行と付随行（`alertrow` / `actrow` / `genrow`）のすべてに`hidden`属性と`group_id`属性を付与する後処理関数を追加し、3つの行生成関数から呼び出す | `bajutsu/common/report/rows.py` | `group_id`が連続する行の区間が見出し行1つにまとまること、付随行にも`group_id`が付くこと、失敗を含む区間には`hidden`が付かないことをユニットテストで確認する | 9 |
| 11 | `report.html.j2`のsteprowマクロに見出し行の描画と`hidden` / `group_id`属性の付与を追加する。`report.js`にトグル用の関数と、既存の`toggleAll`をグループにも連動させる変更を追加する。`report.css`に見出し行のスタイルを追加する | `bajutsu/templates/report.html.j2`、`bajutsu/templates/report.js`、`bajutsu/templates/report.css` | `group:`を含むサンプルシナリオを1つ用意し、生成した`report.html`をブラウザで開いて、初期状態の折りたたみ、失敗グループの自動展開、見出しクリックでの開閉、全展開 / 全折りたたみボタンとの連動を目視で確認する | 10 |
| 12 | `load_scenario_file`でシナリオを読む`audit`のセレクタ / finding の歩き方に、`group`を透過的に扱う（`group`に出会ったら、その`steps`の中へ降りる）分岐を足す。`lint.py`の`provenance_coverage`は対象外とする。この関数はシナリオ最上位の`steps`しか数えておらず、`if` / `forEach`の中へも元から降りていないため、`group`だけを直すと既存の限界に対して一貫性のない対応になる | `bajutsu/analysis/audit/_functions.py` | `group`でステップをくるんだシナリオで、`audit`の集計結果が、くるまなかった場合と一致することをユニットテストで確認する | 1 |
| 13 | `bajutsu lint` / `bajutsu schema`が`group`アクションを認識することを確認する | 該当すれば`bajutsu/cli/commands/lint.py`、`schema.py` | `group:`を含むシナリオを`bajutsu lint`がエラーなく処理し、`bajutsu schema`の出力に`group`アクションが載ることを確認する | 1 |
| 14 | `docs/scenarios.md`（Step grammarの表と新セクション）、`docs/dsl-grammar.md`、`docs/reporting.md`（report.html章）と、それぞれの`docs/ja/`ミラーを更新する | `docs/scenarios.md`、`docs/dsl-grammar.md`、`docs/reporting.md`、`docs/ja/`配下の対応ファイル | 更新した文書がtextlintの指摘ゼロで通ることを確認する | 11 |
| 15 | `make check`が通ることを確認する | — | `make check`がgreenで終わる | 1–14 |

`use`ステップが`capture` / `extract` / `name` / `from` / `target`を黙って捨てる既存の挙動をどうするかは、この項目のスコープに含めない。`record-issue`スキルで別のGitHub Issueとして切り出す。

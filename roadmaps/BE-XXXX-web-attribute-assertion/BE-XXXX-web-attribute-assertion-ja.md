[English](BE-XXXX-web-attribute-assertion.md) · **日本語**

# BE-XXXX — Web バックエンドで任意の HTML 属性を確かめるアサーション

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-web-attribute-assertion-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | 検証とカバレッジ |
<!-- /BE-METADATA -->

## はじめに

この項目は、web（Playwright）バックエンドに `attribute` アサーションを加えます。`attribute` アサーションは、名前で指定した HyperText Markup Language（HTML）属性を、1つの要素について確かめます。例として、作者が `attribute: { sel: { id: docs.link }, name: href, equals: /docs }` を書いた場合を考えます。この場合、その要素の `href` 属性が `/docs` と等しければ実行は成功します。比較には、`value` アサーションや `label` アサーションと同じく `equals`、`contains`、`matches` を使います。さらに、存在してはならない属性のために `absent: true` を加えます。

ドライバーは、ランナーがアサーションを評価するときに、その属性だけを読みます。クエリが返す要素のスナップショットは変わりません。読み出しには、新しい `Driver.read_attribute` メソッドと、新しい `elementAttribute` という capability を使います。この capability を持つのは web バックエンドだけです。iOS や Android で `attribute` を使うシナリオは、デバイスを確保する前の preflight で失敗します。

## 動機

いまの web シナリオが確かめられる要素の性質は、アクセシブルなラベル、`value`、trait（`selected` や `disabled` など）の3つです。しかし、web ページが示す内容の多くは、それ以外の HTML 属性にあります。次の属性は、どのアサーションからも確かめられません。

- **リンク先**：リンクの `href` は、クリックしたときの移動先を決めます。シナリオはリンクの有無と表示文字列を確かめられますが、移動先は確かめられません。
- **画像**：`<img>` の意味は `src` と `alt` にあります。正しい画像が読み込まれたか、代替テキストがあるかを、シナリオは確かめられません。
- **ページがデータとして示す状態**：ページは、状態を属性で示すことがよくあります。`data-*` 属性（`data-state="ready"`）がその例です。Accessible Rich Internet Applications（ARIA）属性（`aria-expanded`、`aria-busy`、`aria-current`）も使われます。テストの作者がこの状態を確かめたいのは、ページ自身が報告する状態だからです。

進行中の変更が、この欠落の一部を埋めます。web のクエリが、タグごとに値を持つ属性から `value` を埋めるようにする変更です（[#2216](https://github.com/bajutsu-e2e/bajutsu/pull/2216)）。たとえばリンクは `href`、画像は `src`、独自のスライダーは `aria-valuenow` から読みます。ただし、この変更がタグごとに割り当てる属性は、ツールが決めた1つだけです。要素は多くの属性を持ち、作者が必要とするのは別の属性であることがよくあります。画像なら `src` ではなく `alt` が必要な場合があり、ツールには推測できない `data-*` 属性が必要な場合もあります。アプリケーションに固有の属性は、タグごとの初期値では扱えません。

この項目で観察できる成果は次のとおりです。web シナリオは、作者が名前を挙げた属性を、ツールを変えずに1つの `attribute` アサーションで確かめられるようになります。リンク先や `data-state` の値がその例です。web のデモには、リンクの `href` と `data-*` 属性を確かめるシナリオを加え、web のエンドツーエンド（E2E）レーンで成功させます。どのアサーションも名前を挙げない属性は読まないので、要素のスナップショット、golden、レポートは大きくなりません。

## 詳細設計

### 記述の形

```yaml
steps:
  - assert:
      - attribute: { sel: { id: docs.link }, name: href, equals: /docs }
      - attribute: { sel: { id: hero.image }, name: alt, contains: Bajutsu }
      - attribute: { sel: { id: cart }, name: data-state, matches: "^(ready|idle)$" }
      - attribute: { sel: { id: submit }, name: aria-busy, absent: true }
```

文法には、アサーションの種類を1つ加えます。

```
Assertion ::= … | { attribute: <AttributeMatch> }
AttributeMatch ::= { sel: <Selector>, name: string }
                 & ( {equals:string} | {contains:string} | {matches:string} | {absent:true} )
```

- **`sel`**：通常のセレクタです。`value` と同じ規則で解決します。一致する要素がなければアサーションは失敗し、2つ以上あれば曖昧として失敗します。
- **`name`**：属性名です。検証では HTML の属性名の構文を受け付け、空の名前を拒否します。HTML と同じく、属性名の大文字と小文字は区別しません。
- **比較の種類**：4つのうちちょうど1つを指定します。`equals`、`contains`、`matches` は、`value` と `label` がすでに使う `TextMatch` の規則に従います。`matches` は正規表現です。`absent: true` は、その名前の属性を要素が持たないときに成功します。
- **存在と空は区別します**：空の値で存在する属性は `""` として読まれます。この場合、`equals: ""` は成功し、`absent: true` は失敗します。属性がない場合、`equals`、`contains`、`matches` は失敗し、失敗の理由には属性がないことを示します。
- **`${...}` トークン**：期待値の文字列に含まれるトークンは、ほかのアサーションのフィールドと同じく展開します。

このアサーションは、シナリオの言語が `Assertion` を受け付ける次のすべての場所で使えます。

- ステップの `assert` リスト
- シナリオの `expect` リスト
- `if` の条件
- `interrupts` の条件

`wait` ステップが受け付けるのは、セレクタ（`for`）か、決まった `until` の形（`screenChanged`、`settled`、`gone`、`request`）のどれかです。アサーションを受け付ける `until` の形はないので、`attribute` は待機の条件にはなりません。待機の条件にすることは対象外とします。

### 実行時の振る舞い

ステップの `assert` リストとシナリオの `expect` リストは、条件待ちとして実行します。その実装は `_poll_asserts`（`bajutsu/common/orchestrator/loop/_functions.py:129`）です。`_poll_asserts` は、アサーションが成功するか lane の待機の下限に達するまで、`driver.query()` を呼び直します。このポーリングは、読み出しの競合を防ぐためにあります。操作が変えた値は、操作から戻った少し後に反映されることがあるからです。この項目が対象とする `data-state` や `aria-busy` のような属性は、まさにそのように変わります。そのため、属性の読み出しもポーリングに合わせます。

1. 各回の `driver.query()` の直後に、補助関数 `_attributes_for(driver, tree, block)` が、まとまりの中の各 `attribute` アサーションを処理します。補助関数は、セレクタをその回のツリーに対して解決し、`Driver.read_attribute(element, name)` を1回呼びます。読み出しが `SelectorError` を送出した場合（実際のページで要素が見つからない場合や、2つ以上見つかった場合）、補助関数は値の代わりにその理由を記録します。アサーションはその理由で失敗し、ステップを中断せずに次の回で読み直します。
2. 補助関数は、結果を `EvalContext` の新しい `attributes` フィールドに、展開済みのセレクタと属性名をキーとして保存します。評価関数が `evaluate_one` から受け取るのはアサーションだけで、まとまりの中の位置は渡されないからです。その回の評価には、このコンテキストを使います。
3. 評価関数は、読んだ値を比較の種類に従って比べ、通常の `AssertionResult` を返します。セレクタが解決できない場合は、`value` アサーションと同じ理由を返します。
4. 失敗が続く `attribute` アサーションは、`value` などツリーから導く種類と同じく、ポーリングを続けさせます。そのため、この種類は `_READ_ONCE_KINDS`（`_functions.py:109`）に入れません。`_READ_ONCE_KINDS` は、読み直しても結果が変わらない種類の集合です。

`if` の条件と `interrupts` の条件は、いまはコンテキストなしで評価します。`_run_if`（`_functions.py:1349`）はツリーを1回読み、そのクエリの後で同じ補助関数を呼びます。`_InterruptGuard._fire_once`（`_interrupt_guard.py:66`）は自身ではクエリを行わず、ループがすでに持つツリーに対して評価します。そのツリーは、`wait` のポーリングの各回で読んだものか、操作のステップが操作の前に読んだものです。そのため補助関数はそのツリーに対して呼ばれ、属性はポーリングの各回で読み直されます。どちらの場合も、そこに置いた `attribute` が分岐や発火を決めます。

評価関数は、ツリーとコンテキストだけを受け取る純粋な関数のままです。読み出しは、`_clipboard_for`（`_functions.py:315`）でのクリップボードの読み出しと同じく、組になるクエリの隣でランナーが行います。ただし、読み直しても変わらないクリップボードとは違い、属性はツリーを読むたびに読み直します。

このアサーションは、プライムディレクティブの範囲に収まります。

- **大規模言語モデル（LLM）を使いません**：判定は文字列の比較です。
- **固定の待機を入れません**：各回の読み出しは、既存の条件待ちの中での同期的な1回の呼び出しです。
- **推測しません**：曖昧なセレクタは、読み出しの前に失敗します。
- **アプリに依存しません**：属性名はシナリオに書き、ツールはアプリケーションごとの属性の一覧を持ちません。

### web ドライバーでの属性の読み方

`read_attribute` は、スナップショットが表すのと同じ要素を、実際のページの中で見つける必要があります。Playwright ドライバーは、次の2つの方法で要素を見つけます。

| 要素が持つもの | ドライバーが要素を見つける方法 |
|---|---|
| `data-testid` | `data-testid` がスナップショットの `identifier` に等しい要素を、`QUERY_JS` が一覧に入れるノード（`display: none`、`visibility: hidden`、大きさのないノードを除いたもの）の中から探します。該当する要素が2つあれば、曖昧として読み出しを失敗させます |
| `data-testid` がない | `QUERY_JS`（`bajutsu/common/drivers/dom.py`）が走査するのと同じノードの一覧をたどり、境界の矩形、アクセシブルな名前、タグやロールから決まる trait で照合します。タップで `_point_hits`（`bajutsu/common/drivers/playwright/playwright_driver.py:389`）がすでに使っている規則を広げたものです。一致するノードが2つ以上あれば、曖昧として読み出しを失敗させます |

見つけたら、ドライバーは `el.getAttribute(name)` を返します。属性がない場合の戻り値は `null` です。属性名は `json.dumps` で符号化してスクリプトに渡し、そのまま埋め込みません。`_point_hits` も、`identifier` や `label` を同じ方法で渡しています。`document.elementFromPoint` ではなくノードの一覧をたどるので、ほかの要素に覆われた要素や、表示領域の外にある要素も読めます。覆われているかどうかはタップでは問題になりますが、属性の値は変わりません。スクリプトは例外を投げず、次の3つの形のどれかを返します。

- 要素が見つからない場合は `{found: false}` です。
- 2つ以上見つかった場合は `{found: false, ambiguous: true}` です。
- 1つだけ見つかった場合は `{found: true, value}` です。

例外を投げない理由は `select_option` と同じで、投げられたスクリプトのエラーは `_wedge_guard` を通るとブラウザーの障害として扱われるからです。`select_option` のような目印の文字列を使わないのは、属性の本当の値が目印と同じだったとき、両者を区別できなくなるからです。

### ドライバー、capability、preflight

| 層 | 変更 | ファイル |
|---|---|---|
| シナリオのモデル | `AttributeMatch` と、`Assertion` の `attribute` の種類 | `bajutsu/common/scenario/models/assertions/attribute_match.py`（新規）、`assertion.py` |
| ドライバーのプロトコル | `def read_attribute(self, el: Element, name: str) -> str \| None` | `bajutsu/common/drivers/base/driver.py` |
| capability | `ELEMENT_ATTRIBUTE = "elementAttribute"` | `bajutsu/common/drivers/base/capability.py` |
| preflight | `attribute` アサーションは `elementAttribute` を必要とします。`_assertions_with_path` は `web:` と `app:` のブロックも `interrupts` も扱いません。そのため、場所の探索は `_navigate_locations`（BE-0464）と同じく `web:` と `app:` のブロックの中と `interrupts` の回復のステップまでたどり、さらに `interrupts` の条件も確かめます。あわせて `_steps_for_target` で `interrupts` もそのターゲットが監視する項目（項目の `target`、なければ primary）に絞り、web だけを対象とする項目が iOS のターゲットを失敗させないようにします | `bajutsu/common/capability/capability_preflight.py` |
| 評価 | 補助関数 `_attributes_for`、`EvalContext.attributes` フィールド、`attribute` の評価関数 | `bajutsu/common/orchestrator/loop/_functions.py`（ステップと `expect` のポーリング、`_run_if`）、`bajutsu/common/orchestrator/loop/_interrupt_guard.py`、`bajutsu/common/assertions/evaluate/` |
| web ドライバー | 前節で述べた `read_attribute` | `bajutsu/common/drivers/playwright/playwright_driver.py` |
| フェイクドライバー | 要素ごとに与えた属性を返し、capability を広告します | `bajutsu/common/drivers/fake/fake_driver.py` |
| ほかのドライバー | iOS と Android の `select_option` と同じく `UnsupportedAction` を送出します | `adb`、`xcuitest`、`xcuitest_live`、`webview` の各ドライバー |

WebView を埋め込んだ iOS アプリでも、Bajutsu は BajutsuKit のブリッジを通して Document Object Model（DOM）にアクセスできます。ブリッジの実装は `bajutsu/common/drivers/webview/web_context_driver.py` にあります。最初の段階では、このブリッジは対象外とします。ブリッジはいま `selectOption` に対して `UnsupportedAction` を送出しており、`read_attribute` にも同じ扱いをします。そのため、iOS のターゲットで `web:` ブロックの中に置いた `attribute` アサーションも、デバイスを確保する前の preflight で失敗します。

ターゲットの設定は、独自の interrupt を宣言できます（`hooks.interrupts`、`bajutsu/common/config/schema/hooks.py:47`）。ランナーは、それをシナリオの項目とあわせて使います。preflight はシナリオだけを読むので、設定側の項目は見えません。そのため、web 以外のターゲットの `hooks.interrupts` に `attribute` があれば、設定の読み込み時の検証で拒否します。バックエンドが `elementAttribute` を広告するかどうかはターゲットのプラットフォームで決まるので、この検証にデバイスは要りません。

### レポート、golden、秘密情報

属性は必要なときにだけ読むので、実行結果に属性が現れるのは、アサーションがその属性の名前を挙げたときだけです。

- **スナップショットと golden**：クエリのスナップショットと `Element` 型は変わりません。golden ファイル、crawl の出力、レポートの要素ツリーにフィールドは増えません。
- **アサーションの結果**：失敗の理由には、いまの `value` アサーションと同じく実際の値を出します（`expected equals='/docs' but actual='/about'`）。この文字列は、ほかの理由の文字列と同じく成果物の秘匿化の境界（[BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary-ja.md)）を通るので、解決済みの秘密情報は伏せられます。
- **作者が明かしうるもの**：作者は、トークンを持つ属性の名前を挙げることもできます。その場合、レポートには作者が求めたその1つの値が出ます。秘密情報だとわかっている値は、秘匿化の境界が引き続き伏せます。ツールがすべての属性をまとめて集めることはないので、誰も名前を挙げていない属性が漏れることはありません。

### コード生成、記述支援、文書の面

- Playwright のコード生成は `await expect(locator).toHaveAttribute(name, value)` を出力します。`matches` には正規表現を、`contains` にはエスケープした正規表現を渡します。`absent` には `not.toHaveAttribute(name)` を出力します。XCUITest と UI Automator のコード生成は、web 限定のほかの構文と同じく、ラベル付きの `// TODO` を出力します。
- アサーションの語彙は、モデル以外の場所にも並んでいます。実装では、次のそれぞれを確認して更新します。

  | 面 | 場所 |
  |---|---|
  | Author エディターのアサーション一覧 | `bajutsu/common/scenario/edit.py` |
  | JavaScript Object Notation（JSON）スキーマ | `bajutsu schema` の出力 |
  | 英語のページ | `docs/scenarios.md`、`docs/dsl-grammar.md`、`docs/drivers.md`、`docs/codegen.md` |
  | 日本語のミラー | `docs/ja/` 以下の同じページ |

### 対象外

- **属性による要素の選択**（`sel: { attr: { href: /docs } }`）：照合には、スナップショットにすべての属性が必要になります。この項目は、すべての属性を集めることを避けています。選択は、のちの項目で別に検討できます。
- **待機の条件としての属性**（`wait: { until: { attribute: … } }`）：アサーションを受け付ける `until` の形はいまのところありません。属性をくり返し読む待機には、それ自体の設計が必要です。
- **BajutsuKit の WebView ブリッジ**：ブリッジには新しいメッセージの種類が必要です。Playwright の経路で契約が固まってから対応できます。
- **DOM のプロパティ**：このアサーションが読むのは属性（`getAttribute`）であり、`checked` のような実行中のプロパティは読みません。読める値は、マークアップとページのスクリプトが設定した値です。フォーム部品の実行中の値は、引き続き `value` アサーションで確かめます。

### 作業の分解（MECE）

| # | 単位 | 完了の条件 |
|---|---|---|
| 1 | シナリオのモデル：`AttributeMatch`、`attribute` の種類、属性名の検証、比較の種類はちょうど1つ | モデルのテストで、各比較の種類を読み込めることと、空の名前、2つの比較の種類、`absent: false` を拒否することを確かめます |
| 2 | ドライバーのプロトコル、capability、preflight の要件、設定の検証、web 以外のドライバーの送出 | preflight のテストで、iOS と Android のターゲット、iOS の `web:` ブロック内の `attribute`、`interrupts` の条件の中の `attribute` が、デバイスの確保前に `attribute` を名指しするメッセージで失敗することを確かめます。web だけを対象とする `interrupts` の項目が iOS のターゲットを失敗させないこと、web 以外のターゲットの `hooks.interrupts` に `attribute` を書いた設定が読み込みで失敗することも確かめます |
| 3 | `_attributes_for`、`EvalContext.attributes`、評価関数 | フェイクドライバーのテストで、各比較の種類、存在と空の区別、属性がない場合、読み出しをせずに失敗する曖昧なセレクタを確かめます。後の回で変わる属性が待機の下限内に成功すること、`if` の条件と `interrupts` の条件に置いた `attribute` が属性を読んで分岐や発火を決めることも確かめます |
| 4 | Playwright の `read_attribute` | 実際の Chromium を使う web ドライバーのテストで、`data-testid` とノードの一覧の走査のそれぞれで属性を読めること、覆われた要素と表示領域外の要素を読めること、`data-testid` が曖昧な場合と、矩形、名前、trait で一致するノードが2つある場合に失敗すること、非表示の重複した `data-testid` があっても曖昧にならないことを確かめます |
| 5 | ドライバーの適合性テスト | web の適合性テストに `read_attribute` のケースを加えて成功させ、web 以外のバックエンドでは capability によって省略します |
| 6 | コード生成 | `attribute` を使うシナリオの Playwright 出力が golden と一致し、ほかのコード生成がラベル付きの `TODO` を出力することを確かめます |
| 7 | web のデモシナリオ | web のデモに、リンクの `href` と `data-*` 属性を確かめるシナリオを加え、web の E2E レーンで成功させます |
| 8 | 文書、スキーマ、語彙の一覧 | 英語と日本語のページに `attribute` を載せ、スキーマに含め、`make check` が成功します |

### レビューで見てほしい点

- **種類の名前**：`value` や `label` と並べて自然に読めるので、`attribute` を選びました。短い `attr` は入力の手間を減らしますが、ほかの種類が使う省略しない名前から外れます。
- **`absent` の比較**：`exists` に新しいフィールドを加える案ではなく、`absent: true` を選びました。問いの対象は、存在する要素が持つ1つの属性だからです。

## 検討した代替案

- **クエリのスナップショットごとに、すべての属性を集める**：各 `Element` が `attributes` の対応表を持ち、アサーションは `value` と同じくスナップショットから読みます。この案を退けた理由は3つです。どのアサーションも読まないマークアップの分だけ、すべてのクエリが大きくなります。ページが無関係な属性を加えるたびに、golden ファイルが変わります。トークンを持つ `data-*` 属性が、誰も求めていなくても、すべてのレポートに現れます。
- **ターゲットの設定で、`value` を埋める属性を選べるようにする**：`img: alt` のような `targets.<name>` の対応表で、[#2216](https://github.com/bajutsu-e2e/bajutsu/pull/2216) のタグごとの初期値を広げる案です。シナリオの言語は変わりません。この案を退けたのは、ターゲット全体でタグごとに1つの属性しか割り当てられないからです。シナリオは画像の `src` と `alt` の両方を確かめられず、`data-state` のように要素ごとに異なる属性には、キーにするタグがありません。
- **同じ項目で属性による選択も加える**：選択を加えると、作者は `href` だけで要素を指定できます。照合にはスナップショットにすべての属性が必要になり、最初の代替案の欠点が戻ってくるので、選択は外しました。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] シナリオのモデル：`AttributeMatch`、`attribute` の種類、属性名の検証
- [ ] ドライバーのプロトコル、capability、preflight の要件、設定の検証、web 以外のドライバーの送出
- [ ] `_attributes_for`、`EvalContext.attributes`、評価関数
- [ ] Playwright の `read_attribute`
- [ ] ドライバーの適合性テスト
- [ ] コード生成
- [ ] web のデモシナリオ
- [ ] 文書、スキーマ、語彙の一覧

## 参考

- [#2216](https://github.com/bajutsu-e2e/bajutsu/pull/2216)：web バックエンドで、タグごとに値を持つ属性から `value` を埋める変更です。
- [BE-0331](../BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary-ja.md)：アサーションの理由に含まれる秘密情報を伏せる、成果物の秘匿化の境界です。
- [BE-0464](../BE-0464-web-navigate-step/BE-0464-web-navigate-step-ja.md)：web 限定の機能を capability と preflight の要件の背後に置いた前例です。
- `bajutsu/common/orchestrator/loop/_functions.py`：`_poll_asserts` は、属性の読み出しが従う条件待ちです。`_clipboard_for` は、クエリの隣で値を読む前例です。

[English](BE-XXXX-declarative-hook-conditions.md) · **日本語**

# BE-XXXX — `hook` の実行条件をターゲットや環境変数でも宣言する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-declarative-hook-conditions-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | シナリオ記述機能 |
| 関連 | [BE-0034](../BE-0034-tags-selective-runs/BE-0034-tags-selective-runs-ja.md)、[BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks-ja.md)、[BE-0437](../BE-0437-multi-target-step-groups/BE-0437-multi-target-step-groups-ja.md)、[BE-0467](../BE-0467-environment-specific-settings/BE-0467-environment-specific-settings-ja.md)、[BE-0491](../BE-0491-target-command-hooks/BE-0491-target-command-hooks-ja.md)、[BE-0492](../BE-0492-worker-command-execution-policy/BE-0492-worker-command-execution-policy-ja.md)、[BE-0497](../BE-0497-tag-conditional-hooks/BE-0497-tag-conditional-hooks-ja.md)、[BE-0501](../BE-0501-scenario-command-hooks/BE-0501-scenario-command-hooks-ja.md) |
<!-- /BE-METADATA -->

## はじめに

この項目は、`hook` を実行するかどうかを決める `when` ブロックを広げます。現在の `when` ブロックが
読むのは、シナリオのタグだけです。ここに `targets`、`platforms`、`routes`、`env` の4つのフィールドを
足します。**route** は、ターゲットが動く実行先の種類を表します。iOS Simulator や iOS の実機が
その例です。

広げた `when` ブロックは、もう1種類の `hook` にも使えるようにします。現在 `when` で条件を付けられる
のは、外部コマンドだけです。この項目の後は、シナリオの前後に挟む `setup` と `cleanup` のステップにも
条件を付けられます。どのフィールドも宣言的に書きます。式は書けず、判定用のコマンドも実行しません。

## 動機

**コマンド `hook`** は、実行全体やシナリオの前後で Bajutsu が実行する外部コマンドです。ターゲットは
`targets.<name>.hooks` に、シナリオは自身の `hooks` にエントリを宣言します。BE-0497 は各エントリに
省略可能な `when: { tags, exclude }` ブロックを加えました。タグが表すのは、シナリオの扱う内容です。
シナリオがどこで動くかは表しません。

準備の内容は、シナリオがどこで動くかによって変わることがよくあります。

| 場面 | 分けたい内容 |
|---|---|
| 1つのシナリオを `--target ios` と `--target android` で実行 | 実行ごとの準備 |
| `defaults.hooks` に書いた共通のエントリ | 効かせるターゲット |
| 実機とシミュレータ | 実機だけに必要なステップ |
| 開発者の手元と継続的インテグレーション（CI） | 手元だけで起動するモックサーバー |

どの場面にも、現在はよい手段がありません。コマンドは `BAJUTSU_TARGET` を読んでスクリプト内で分岐
できます。しかし、スクリプトが何もしなかった場合でも、レポートにはエントリを実行したと残ります。
シナリオ自身のエントリがその変数で受け取るのは、最初に宣言したターゲットだけです。`setup` と
`cleanup` のステップには、条件がありません。`if` ステップが調べるのは画面なので、ターゲット名は
読めません。

この項目を実装すると、読者は次の3つの違いを確かめられます。1つのシナリオファイルに、両方の
プラットフォームの準備を書けます。レポートには、スキップしたエントリとブロックが条件とともに
並びます。`hook` のスクリプトは、ターゲット名や CI の変数で分岐する必要がなくなります。

## 詳細設計

### `when` のフィールド

| フィールド | 型 | 条件が成り立つ場合 |
|---|---|---|
| `tags` | 文字列のリスト | シナリオがいずれかのタグを持つ（BE-0497 のまま） |
| `exclude` | 文字列のリスト | シナリオがどのタグも持たない（BE-0497 のまま） |
| `targets` | ターゲット名のリスト | 判定対象のターゲットが列挙にある |
| `platforms` | `ios`、`android`、`web`、`fake` のリスト | 判定対象のプラットフォームが列挙にある |
| `routes` | route 名のリスト | 判定対象の route が列挙にある |
| `env` | 変数名から文字列または null への対応 | 列挙した変数がすべて一致する |

route 名には、実行先が適用できる設定を判定するために Bajutsu がすでに使っている5つを使います
（BE-0467）。`ios-simulator`、`ios-device`、`ios-live`、`android`、`web` です。`ios-live` は、
稼働中の WebDriver エンドポイントを指します。`fake` バックエンドには route がないので、`routes` の
条件は成り立ちません。

```yaml
defaults:
  hooks:
    beforeRun:
      - cmd: "./scripts/start-mock.sh"
        when: { env: { CI: null } }          # CI が未設定の環境だけ
    beforeScenario:
      - cmd: "./scripts/trust-certificate.sh"
        when: { platforms: [ios], routes: [ios-device] }
```

### フィールドの組み合わせ

| 対象 | 規則 |
|---|---|
| 1つのリストの中の値 | 「または」で結合 |
| 異なるフィールド | 「かつ」で結合 |
| `targets`、`platforms`、`routes` | 同じ1つの判定対象で判定 |
| `exclude` | 引き続き `tags` より優先 |
| 制約のない `when` ブロック | 現在と同じく読み込みエラー |

config にない名前を `targets` に書くと、読み込みに失敗します。そのような名前は書き間違いだからです。

### 判定対象のターゲット

`targets`、`platforms`、`routes` がどのターゲットを読むかは、条件を書いた場所で決まります。

| 条件を書いた場所 | 判定対象 |
|---|---|
| `target` を持つ条件ブロック | そのターゲット |
| `targets.<name>.hooks` のエントリ、条件ブロック、cleanup ルール | 持ち主の `<name>` |
| シナリオのエントリ、条件ブロック、cleanup ルール | シナリオが動かすいずれかのターゲット |

`defaults` やテンプレートからマージしたエントリは、受け取ったターゲットが持ち主です。たとえば
`defaults.hooks` に `when: { targets: [ios] }` のエントリを書くとします。`android` が受け取った
写しは、両方を動かすシナリオでも実行しません。

`targets.<name>.hooks` に直接書いた条件は、最初から成り立たないことがあります。`targets` や
`platforms` が判定対象を含まない場合です。`routes` が判定対象のプラットフォームの route を1つも
含まない場合も同じです。このような条件は、制約のない `when` と同じく読み込みに失敗します。検査は、
`defaults` やテンプレートをマージする前の、書いたとおりのエントリを読みます。マージで届いて実行
されない写しは、有効なままです。

シナリオが動かすのは、宣言したターゲットです。何も宣言しないシナリオは、実行全体のターゲットを
動かします。シナリオに書いた `{ targets: [app], routes: [ios-device] }` は、`app` が実機で動くかを
問います。

### 環境変数

`env` は、`bajutsu run` のプロセスが持つ環境変数を読みます。`serve` ではそのプロセスがワーカーで
動くので、ワーカーの環境が結果を決めます。エントリ自身の `env` フィールドは、判定に関係しません。

文字列の値は、大文字と小文字を区別する完全一致を求めます。null は、変数が未設定であることを
求めます。空文字列を設定した変数は、設定済みとみなします。設定のパーサーが真偽値や数値と解釈する値は
読み込みに失敗するので、引用符で囲んで書きます。レポートには条件を書いたとおりに表示し、変数の
実際の値は表示しません。

### 実行全体のリスト

`beforeRun` と `afterRun` のエントリには、対応する1つのシナリオがありません。BE-0497 は、シナリオの
集まりを対象に条件を判定します。集まりに含むのは、選んだシナリオのうちエントリのターゲットを動かす
ものです。この項目がこの集まりを使うのは、`tags` と `exclude` の判定だけです。

ほかのフィールドには、シナリオが要りません。`targets`、`platforms`、`routes` は持ち主のターゲットを
読み、`env` はプロセスを読みます。これらだけの条件は、集まりが空になる `record`、`crawl`、`repl`
でも成り立ちます。各コマンドは、最初の `hook` の前に持ち主の route を確定します。`tags` を含む
条件は、集まりが空なら引き続き成り立ちません。実行全体のエントリに対する読み込み時の検査は狭めます。現在は `tags` のない `when` を
すべて拒否します。この項目の後で拒否するのは、`tags` のない `exclude` だけです。

`beforeRun` と `afterRun` の条件は、最初の `beforeRun` エントリの前に一度だけ決めます。各
`afterRun` エントリは、そのときの判定を使います。

### setup と cleanup の条件付きステップ

`setup` のリストには、通常のステップと並べて**条件ブロック**を書けます。条件ブロックは `when` と
`steps` を持ちます。`cleanup` のルールには、`on` と並べて `when` を書けます。どちらの形も、
シナリオと `targets.<name>.hooks` の両方で使えます。

```yaml
setup:
  - when: { platforms: [ios] }
    steps:
      - tap: { id: onboarding.skip }
  - tap: { id: tab.home }
cleanup:
  - on: always
    when: { platforms: [android] }
    steps:
      - tap: { id: debug.reset }
```

条件ブロックは、複数のステップに `target` を一度だけ指定する既存の**ターゲットグループ**に似た形
です（BE-0437）。条件ブロックにも `target` を書けます。書かない場合、各ステップは通常のターゲットの
規則に従います。条件ブロックには、1つ以上のステップが必要です。条件ブロックは、別の条件ブロック、
`steps`、`if` ステップの中には書けません。

Bajutsu は、シナリオの最初のステップより前にすべての条件ブロックを判定します。判定が読むのは
コマンド `hook` と同じ事実で、画面は読みません。画面で分岐する手段は、引き続き `if` ステップです。

読み込み時の検査は、条件が成り立つかどうかに関係なく、すべての条件ブロックに適用します。検査の
対象は、コンポーネント、`target` の名前、そのほかのステップの規則です。ブロック内で保存した変数と、
その変数を読む後続のステップとを結び付ける検査は、この項目では加えません。

### 条件付きエントリの出力の参照

シナリオは、コマンドの出力を `${hooks.<name>.<key>}` で読みます。BE-0497 は、条件を満たさない
エントリを読むシナリオを拒否します。この項目はその規則を2段階で保ちます。

| 条件に含むフィールド | 検査するシナリオ | 検査の時点 |
|---|---|---|
| `tags`、`exclude`、`targets`、`platforms` | 展開後のすべてのシナリオ | 現在と同じく読み込みのとき |
| `env`、`routes` | 選んだシナリオ | 各 route の確定後、最初の `hook` の前 |

`env` か `routes` を含む条件は、条件全体を2段階目で検査します。1段階目が検査するのは、どちらも
含まない条件です。

不一致があれば、どちらの段階でも終了コード2で終了します。2段階目は、選んでいないシナリオを検査
しません。結果がファイルだけでは決まらないからです。`env` は実行するマシンで決まり、route は `--backend` や
`--udid` などの指定で決まります。スキップした条件ブロック内の
トークンには値が要らないので、検査しません。

### 事前検査、ルーティング、コード生成、証跡

| 項目 | 振る舞い |
|---|---|
| 事前検査 | 能力の検査は、判定後に残ったステップを読みます。スキップしたブロックの能力不足で実行が失敗することはありません。 |
| `serve` のルーティング | ジョブが必要とする能力でワーカーを選びます。扱いは下の段落で述べます。 |
| コード生成 | `bajutsu codegen` は、`if` ステップと同じように、条件ブロックと `when` を持つ `cleanup` ルールを拒否します。生成したテストには、`env` の条件を判定する実行がありません。静的なフィールドの判定には `codegen` に実行時の規則が必要で、後の項目に残します。 |
| コマンドの許可方針 | ワーカーは、条件の成否に関係なく、宣言したすべてのコマンドを検査します（BE-0492）。 |
| 証跡 | スキップしたコマンドは、現在と同じく条件とともに `skipped (when)` と表示します。スキップしたブロックにも、レポートで同じ表示を使います。 |

`serve` がブロックの必要能力を除くのは、`tags`、`exclude`、`targets`、`platforms` のいずれかで
ブロックが対象外になる場合です。判定対象のどのターゲットの route も `routes` にない場合にも除きます。
route はワーカーによって変わりません。`serve` はジョブを振り分けるために、config とリクエストから
各 route をすでに導いています（BE-0467）。`env` を持つブロックの必要能力は残します。`serve` からワーカーの環境は見えないからです。

### 最優先の原則との関係

条件が比べるのは、ファイル、config、プロセスがすでに示している文字列です。Large Language Model
(LLM) は関与しません。ファイル、フラグ、環境、実行先が同じなら、選ぶ `hook` は常に同じです。
フィールドは特定のアプリを指さないので、アプリごとの違いは config とシナリオに残ります。

## 検討した代替案

| 代替案 | 採用しない理由 |
|---|---|
| 判定用コマンドの終了コードで決める | 実行するまで結果がわからず、レポートに示せる理由も数値だけです。宣言的なフィールドで足りないとわかれば、後の項目で追加できます。 |
| すべての条件を、シナリオが動かすいずれかのターゲットで判定 | 共有する `defaults` のエントリが動かすターゲットの数だけ実行され、除きたいターゲットの分も走ります。採用案の代償として、ターゲット側のエントリは、ほかにどのターゲットを動かすかを問えません。 |
| `target == 'ios' and env.CI` のような式 | BE-0034 と BE-0497 は式言語を退けました。構文解析器と、文法の文書化が必要になります。 |
| `excludeEnv` フィールドの追加 | 変数が未設定という主な場面は null で書けます。「この値以外」を必要とする場面は、把握していません。 |
| `routes` の代わりに `virtual` と `physical` の区別 | Android と WebDriver の実行先を分類する新しい規則が必要です。route 名はすでにあります。 |
| すべてのステップに `when` の修飾子 | 変更がシナリオ本体に及び、`hook` の範囲を大きく超えます。 |
| `if` ステップの条件の種類を追加 | `if` は実行中の画面を読みます。静的な事実を同じ欄に置くと、2つの意味が混ざります。 |
| `codegen` で静的なフィールドを判定 | `codegen` に実行時のターゲットとプラットフォームの規則が必要です。最初はブロックの拒否のほうが小さく済みます。 |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] スキーマ: `when` の新しい4フィールドと、読み込みエラー
- [ ] スキーマ: 実行全体のエントリに対する読み込み時の検査の緩和
- [ ] スキーマ: ターゲットに直接書いた成り立たない条件の読み込みエラー
- [ ] 判定: シナリオ、動かすターゲット、その route、環境変数を受け取る1つの述語
- [ ] コマンド `hook`: シナリオ単位と実行全体のリストへの述語の適用
- [ ] setup と cleanup: 条件ブロックと、cleanup ルールの `when`
- [ ] 最初のステップより前の条件ブロックの判定
- [ ] 出力トークン: 2段階の検査
- [ ] 条件ブロックに対する事前検査、`serve` のルーティング、`codegen` の振る舞い
- [ ] 証跡: スキップしたブロックのレポート表示
- [ ] 利用者向け文書: `docs/configuration.md`、`docs/scenarios.md`、`docs/codegen.md`
- [ ] 設計文書: `DESIGN.md` と `docs/architecture.md`
- [ ] 各文書の日本語版

## 参考

- [BE-0034](../BE-0034-tags-selective-runs/BE-0034-tags-selective-runs-ja.md): シナリオのタグ
- [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks-ja.md): `setup` と `cleanup`
- [BE-0437](../BE-0437-multi-target-step-groups/BE-0437-multi-target-step-groups-ja.md): ターゲットグループ
- [BE-0467](../BE-0467-environment-specific-settings/BE-0467-environment-specific-settings-ja.md): 実行先の route
- [BE-0491](../BE-0491-target-command-hooks/BE-0491-target-command-hooks-ja.md): ターゲットのコマンド `hook`
- [BE-0497](../BE-0497-tag-conditional-hooks/BE-0497-tag-conditional-hooks-ja.md): `when` ブロック
- [BE-0501](../BE-0501-scenario-command-hooks/BE-0501-scenario-command-hooks-ja.md): シナリオのコマンド `hook`

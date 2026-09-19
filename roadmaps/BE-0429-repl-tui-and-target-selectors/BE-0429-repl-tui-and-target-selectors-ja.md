[English](BE-0429-repl-tui-and-target-selectors.md) · **日本語**

# BE-0429 — `repl`のncurses風画面と、`tap`/`type`の`label`・`index`・座標ターゲット

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0429](BE-0429-repl-tui-and-target-selectors-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0429") |
| 実装 PR | [#2026](https://github.com/bajutsu-e2e/bajutsu/pull/2026) |
| トピック | オーサリング体験 |
| 関連 | [BE-0423](../BE-0423-cli-repl-inspect-actuate/BE-0423-cli-repl-inspect-actuate-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0423](../BE-0423-cli-repl-inspect-actuate/BE-0423-cli-repl-inspect-actuate-ja.md)は、`bajutsu
repl`をプレーンな1行ずつの`bajutsu>`プロンプトとして実装しました。そこでの`tap`/`type`は`id`だけ
で要素を指定する形式で、このBE-0423自身の「詳細設計」と「検討した代替案」が、シェル本体が着地した
あとの自然な続きとして明記していた、意図的なv1の範囲縮小です。本項目は、その続きを3つの方向で
実現します。`tap`/`type`が、シェルが内部ですでに解決している`Selector`の語彙に届くようになります。
短いショートカット文法(`label`、`index`)と、ショートカットでは届かないフィールド
(`idMatches`・`labelMatches`・`traits`・`value`・`within`)向けにシナリオの`Selector`モデルを
そのまま再利用する`--sel <yaml>`という逃げ道の、両方によってです。加えてセレクタの解決を一切
経由しない座標タップが加わります。そしてシェル自体が、標準入力と標準出力の両方が対応する端末で
あるとき、ncurses風の画面として描画されるようになります。コマンド行はつねに画面の一番上に固定され、
各コマンドへの応答は下のスクロール可能かつ検索可能な領域に積み重なります。

## 動機

v1のシェルを実際に使うなかで、2つの隙間が見えてきました。

`id`を持たない、`label`だけの要素は、`tree`がその`label`をそのまま表示しているにもかかわらず、
`repl`からはまったく届きませんでした。操作者は行を読むことはできても、それを操作するコマンドを
持ちませんでした。同じ`id`や`label`を共有する2つの要素(重複した行、繰り返しのリスト項目)も同様に
届きませんでした。`tap`は`AmbiguousSelector`を報告して止まり、どちらを指すかを伝える方法があり
ませんでした。`label`と`index`はどちらも、`Selector`
(`bajutsu/common/drivers/base/selector.py`)にすでに端から端まで存在し、`resolve_unique`
(`bajutsu/common/drivers/base/_functions.py`)もすでにこれらを尊重しています。欠けていたのは、
ターゲットを指定するREPL自身のコマンドライン文法だけでした。座標タップは、これとは別の、より
狭い3つ目の隙間を埋めます。覆っている要素の端が正確にどこにあるかを確かめたり、ツリーが誤って
報告するコントロールに届いたりするために、シナリオのステップを書かずに済ませる用途です。
`Driver.tap_point`はすでにどのbackendにも存在しますが、`repl`にはそれを呼び出すコマンドが
ありませんでした。

`id`・`label`・`index`をカバーするショートカット文法だけでは、本当の隙間がまだ残ります。
`idMatches`・`labelMatches`・`traits`・`value`・`within`には、ショートカットの綴りが一つも
ありません。それぞれにglobフラグ・正規表現フラグ・trait一覧・入れ子セレクタ構文といった綴りを
発明していけば、コマンドライン文法はショートカットが本来持つべき範囲をはるかに超えて膨らんで
しまいます。シナリオの著者はまさにこのための語彙をすでに持っています。ステップが実際に使う
`Selector`のYAMLです。これをREPLの逃げ道としてそのまま再利用すれば、新しい語彙を設計したり
覚えたりすることなくこの隙間を埋められます。

もう一つ、プレーンなプロンプトループでは、長い`tree`を読み返したり、以前の`tap`の結果を探したり
する作業が、端末自体の履歴をスクロールすることを意味していました。シェル自身は自分が表示した内容
を覚えておらず、それを検索する方法もありません。不安定なセレクタを調査している操作者が求めるのは
まさにそれです。いくつかコマンドを打ってから、あとで画面を巻き戻し、何が変わったのかを説明する
1行を出力全体から検索する、という使い方です。入力を画面の一番上に固定し、出力を下にスクロール
可能に置き、両者を1つのキーで行き来する、という古典的なncurses風の分割は、この要求に対する
よく知られた答えです。Pythonの標準ライブラリはすでに`curses`を同梱しており(BE-0423の対象は
macOSとLinuxに限られ、このプロジェクトが元々持つSimulator専用の対応範囲と一致するため、新しい
依存は増えません)。

## 詳細設計

### `tap`/`type`のターゲット構文

`bajutsu/repl/session.py`に`_parse_target(token) -> Selector | Point | None`を追加します。既存の
`Selector`のフィールドと`Driver.tap_point`をそのまま再利用し、新しい解決の意味付けは発明しません。
新しいのはトークンの文法そのものだけです。

| 形式 | 解決する先 |
|---|---|
| `<id>` | `{"id": token}`。既定の挙動は変わりません。`tap`ではこれまでどおり残り全体をそのままidとして扱い、空白を含んでいてもかまいません |
| `<id>#<index>` | `{"id": id, "index": index}`。`index`は0始まりで、負の値は末尾から数える`resolve_unique`の既存の意味付けそのままです |
| `label:<text>` | `{"label": text}`。`label`だけを持つ要素に届く唯一の方法です。`matches()`のどこにもidからlabelへのフォールバックが存在しないことを確認済みです |
| `label:<text>#<index>` | `{"label": text, "index": index}` |
| `@<x>,<y>` | 生の`Point`。`tap`専用で、`resolve_unique`を一切経由せず`driver.tap_point((x, y))`を直接呼び出します |

末尾の`#<index>`は`label:`の接頭辞を調べる前に切り離すので、どちらの形式とも組み合わせられます。
id や label自体が`#<digits>`で終わっていたり、`@`や`label:`で始まっていたりする場合は、この方法
では届きません。これは狭い、`help`にも書かれた既知の制約であり、黙って誤読するわけではありません。
実際の値は`tree`/`find`で確かめられます。

`_tap(rest)`は`rest`全体を1つのターゲットとしてそのまま解析します(枠組みは変わりません。`tap`
は引数を1つだけ取り、その後には何も続かないので、空白を含むlabelにも引用符は要りません)。
`_type(rest)`はターゲットとそれに続くテキストを分ける必要があるため、小さな、引用符を認識する
分割処理を加えました。`shlex`は使いません。自由に打つテキストが引用符を要求されたり、たまたま
含む引用符でエラーになったりしてはならないからです。行が`"`で始まっていれば、対応する`"`までを
ターゲットとして読み(`type "label:Sign in" hello`のように空白を含むlabelを指定できます)、
それ以外では最初の空白区切りトークンがターゲットになる、という今までどおりの挙動です。`type`は
`@<x>,<y>`ターゲットをそのまま拒否します。「その座標にタップする」という概念はあっても、「その
座標にタイプする」という概念は存在しないためです。

### 完全なセレクタへの逃げ道(`--sel`)

`tap --sel <yaml>`と`type --sel <yaml> <text>`は、flowスタイルの`{...}`という1つのYAMLマッピング
だけを受け付けます。blockスタイルは改行を必要とし、1行で打つ入力にはその形を取れないため、flow
スタイルがこの入力にとりうる唯一の形です。その閉じる`}`は、`"<空白を含むターゲット>"`における
引用符と同じ役割を果たし、`type`のターゲットとテキストの境界にもなります。このマッピングは
`yaml.safe_load`で解析したあと、シナリオのステップが実際に使うのと**同じ**
`bajutsu.common.scenario.Selector` pydanticモデルで検証します
(`Selector.model_validate(data).as_selector()`)。独自に組んだスキーマではありません。そのため
シナリオが使えるすべてのフィールド(`id`・`idMatches`・`label`・`labelMatches`・`traits`・
`value`・`within`・`index`。snake_caseのフィールド名とcamelCaseの別名のどちらも)が、両者の間に
語彙のずれを一切持ち込まずにここでも使えます。ショートカットの綴りでは表せない`within`の入れ子
セレクタの形も含めてです。YAMLの構文エラーやSelectorの検証失敗(未知のフィールド、空のセレクタ、
不正な`id`のOR候補リストなど)は、汎用的な使い方の案内ではなく、その根本の例外
(`yaml.YAMLError`または`pydantic.ValidationError`)をそのままコマンドの応答として表示します。
これらのメッセージはすでに具体的で、そのまま手がかりになるからです。上のショートカット形式は、
`--sel`に置き換えられることなく既定のまま残ります。よくある単一idの場合には、`tap <id>`のほうが
`--sel {id: ...}`より短く打てるからです(詳細は「検討した代替案」を参照)。

### ncurses風の画面

新しい`bajutsu/repl/tui.py`を、既存のプレーンなループ(`loop.py`)を置き換えるのではなく並べて追加
します。`bajutsu/repl/cli.py`は`sys.stdin.isatty() and sys.stdout.isatty()`でどちらを使うかを
選びます。パイプされた`repl`(スクリプトやテストハーネス)は、ほとんどの端末ツールがそうするのと
同じように、プレーンな1行ずつのシェルのままです。`CliRunner`経由で`repl`を駆動する既存のCLI配線
テストは実際の端末を持たないため、そのまま変更なしにプレーンなループを検証し続けます。

`tui.py`は、`loop.py`がすでに自分自身のテスト容易性のために使っている依存注入の形をそのまま踏襲
します(そちらは`read_line`/`say`、こちらは小さな`Screen` Protocol、`getmaxyx`/`get_wch`/
`erase`/`addnstr`/`move`/`refresh`という、実際の`curses.wrapper`が渡す`stdscr`がすでにそのまま
実装しているメソッドの部分集合なので、本番でアダプタクラスは要りません)。振る舞いのすべては、
純粋な`TuiState`データクラス(モード、入力バッファとカーソル、履歴、出力の全文、スクロール位置、
フィルタ文字列)と、純粋な`handle_key(state, key, pane_height)`が持ちます。`run_tui`は描画し、
1つキーを読み、送信された行を`repl_loop`とまったく同じやり方
(同じ`COMMAND_ERRORS`/`FATAL_ERRORS`/`ReplExit`の扱い)で`session.dispatch`に通します。テストは
`handle_key`を直接駆動し、`run_tui`は`get_wch()`の呼び出し列を台本どおりに返し描画を記録する
`FakeScreen`という代役に対して駆動します。実際の端末は要りません。`test_repl.py`がすでにプレーン
なループを`read_line`/`say`の代役でテストしているのと同じ形です。

キー割り当ては、今回求められていた「何らかのショートカットキー」1つでモードを切り替える設計に
なっています。

- **`"input"`モード**(起動時の既定)。表示可能な文字は(`get_wch`が1回の呼び出しで1文字分の
  Unicodeをまるごと解決するため、日本語の`type`/`tap label:`引数も正しく往復します。生の`getch`
  ではUTF-8の1バイトずつしか返らず、これは得られません)カーソル位置に挿入されます。
  Backspace/Left/Right/Home/Endは行を編集し、Enterは送信します。上下キーはシェルの慣習どおりに
  履歴を呼び出します。**Tab**は`"scroll"`へ切り替えます。
- **`"scroll"`モード**。上下キーは出力を1行ずつ、PageUp/PageDownは1画面分ずつスクロールし、いま
  の(フィルタがかかっていればその)内容に合わせて動く範囲が制限されます。下端に固定されている
  (`scroll_offset == 0`)あいだに新しい出力が届いてもそのまま下端に固定され続け、上にスクロール
  している最中に届いた場合は、読んでいる最中に下まで引き戻されることなく、いま見えている内容が
  そのまま保たれます。`/`はフィルタの入力欄を開きます(行編集の仕組みをそのまま使います)。
  **Tab**は`"input"`に戻ります。
- **`"filter"`モード**(`/`で入る)。Enterで確定すると、大文字小文字を区別しない部分一致のフィルタ
  が出力全体にかかります(空のパターンで確定すると解除されます)。Escは、そのときアクティブだった
  フィルタを変えずに編集だけを取り消します。

Ctrl-Cは打ちかけの行(またはフィルタの入力)を捨てます。プレーンなループと同じ意図です。`input()`
と違い、cursesはcbreakモードで読み取れるCtrl-D / EOFの合図を一切持たないため、`exit`/`quit`を
打って送信することだけが、このTUIからシェルを抜ける唯一の方法です。プレーンなフォールバックとの
唯一の挙動差として文書化しています。

### カバレッジ

`bajutsu/repl/session.py`と`bajutsu/repl/tui.py`はどちらも、本項目自身のPRの時点でファイルごとの
カバレッジ下限(`coverage-floors.json`)を満たしています。BE-0423がこのパッケージ全体について
定めた基準と同じです。

## 検討した代替案

- **サードパーティのTUIライブラリ**(`prompt_toolkit`、`textual`)。却下しました。`curses`は、この
  プロジェクトが対象とするすべてのプラットフォーム(macOSとLinux。Windows向けCIはありません)で
  すでに標準ライブラリに含まれているため、新しい依存は増えません。本項目が必要とするのは、固定
  された入力行、スクロール可能な領域、いくつかのキー割り当てだけであり、フレームワークのウィジェ
  ット機構までは必要ありません。
- **プレーンなループへのフォールバックを持たず、つねにTUIを描画する。** 却下しました。パイプ
  された`repl`(スクリプトやテストハーネス)には、cursesが描画できる実際の端末がありません。
  `repl_loop`を非ttyのフォールバックとして残すことに追加のコストはなく(すでに存在していたもの
  です)、`repl`を`CliRunner`経由で駆動する既存のCLI配線テストを、curses用の代役なしに、実際の
  変更されていないコードのまま検証し続けられます。
- **YAMLの`tapPoint`アクションに合わせて、`tap @<x>,<y>`を正規化(0から1)座標にする。** 却下しま
  した。REPL自身の`tree`/`find`はすでにデバイスの生ポイントでframeを報告しているため、生ピクセル
  の`tap_point`呼び出しのほうがREPLの流儀に合っています。YAMLアクション側の正規化は、複数回の
  実行をまたいだ解像度の違いに対する移植性のためのものであり、すでに起動済みの1台のデバイスに
  対する1回の対話セッションには当てはまりません。
- **`Esc`を、`Tab`と同じく`"scroll"`/`"input"`を切り替える2つ目のショートカットにする。** 今回
  は却下しました。単独で押された`Esc`を、エスケープシーケンス(矢印キーやファンクションキー)の
  先頭と確実に区別できるのは、curses内部の短いタイムアウトによってだけです。これは、今回の要望
  が実際に求めていた「何らかのショートカットキー1つ」(すでに`Tab`がそれにあたります)に遅延を
  持ち込みます。`Esc`は代わりに、進行中のフィルタ編集を取り消す用途に限って使っています。小さな
  遅延が気にならない場面だからです。
- **`--sel`を並べて足すのではなく、ショートカットのターゲット文法を丸ごとYAMLに置き換える。**
  却下しました。素朴な`tap <id>`は圧倒的によくある使い方であり、`tap {id: stable.save}`はそれに
  対して単に打つ量が増えるだけで得るものがありません。ショートカットが存在する理由自体が、操作者
  がまず手を伸ばす操作に対して完全なセレクタ構文より速いことにあります。先頭の`{`を検知する方式
  ではなく明示的な`--sel`フラグにすることで、パーサにとっても読み手にとっても2つの文法が曖昧に
  ならず、このシェルがすでに持つフラグの流儀(`tree --json`)にも合います。

## 進捗

> 作業の進行に合わせてつねに最新の状態に保ってください。チェックリストは「詳細設計」のMECEな
> 作業分解に対応します(作業の単位ごとに1つのボックス)。ログには、何がいつ変わったかを、PRへの
> リンクとともに古い順に記録します。

- [x] `bajutsu/repl/session.py`の`_parse_target`/`_split_target_and_text`。`tap`/`type`向けの
  `<id>`、`<id>#<index>`、`label:<text>[#<index>]`、`@<x>,<y>`(tap専用)の各ターゲット形式と、
  それを文書化した`_HELP`の更新。
- [x] `bajutsu/repl/session.py`の`--sel <yaml>`。`_strip_sel_flag`/`_split_yaml_selector`/
  `_parse_yaml_selector`が、`bajutsu.common.scenario.Selector`を通じて
  `id`・`idMatches`・`label`・`labelMatches`・`traits`・`value`・`within`・`index`の全語彙を検証します。
- [x] `bajutsu/repl/tui.py`。`Screen` Protocol、`TuiState`、`handle_key`、`run_tui`、そして実際
  のエントリポイントである`run`(`curses.wrapper` + `locale.setlocale`)。`bajutsu/repl/cli.py`
  は、標準入力と標準出力の両方が端末であるときはそちらへ、そうでなければ従来どおり
  `repl_loop`へ振り分けます。
- [x] 新しいターゲット形式(`--sel`を含む)に対する`FakeDriver`ベースのテスト(`tests/test_repl.py`)
  と、TUIのエンジンに対する`FakeScreen`ベースのテスト一式(`tests/test_repl_tui.py`)。どちらも
  カバレッジ100%です。
- [x] `docs/cli.md`と`docs/ja/cli.md`の`## repl`節を更新しました。新しいターゲット構文の表
  (`--sel`を含む)とTUIのキー割り当ての表を加え、閉じた「`id`だけで指定する」というv1の制約を
  置き換えました。

ログ:

- [#2026](https://github.com/bajutsu-e2e/bajutsu/pull/2026) — 作業単位1〜5、本項目の全体。

## 参考

- [BE-0423 — 要素ツリーを閲覧し id で操作する対話シェル](../BE-0423-cli-repl-inspect-actuate/BE-0423-cli-repl-inspect-actuate-ja.md) —
  本項目が続きとなる元の項目です。id だけに限定した v1 の範囲縮小については、その「詳細設計」と
  「検討した代替案」を参照してください。
- [`Selector`](../../docs/ja/glossary.md#シナリオのオーサリング) — `bajutsu/common/drivers/base/selector.py`、
  `bajutsu/common/drivers/base/_functions.py`(`resolve_unique`、`matches`)
- シナリオの`Selector` pydanticモデル — `bajutsu/common/scenario/models/selector.py`。`--sel <yaml>`
  がそのまま再利用するバリデータです
- `Driver.tap_point` — `bajutsu/common/drivers/base/driver.py`
- [BE-0332 — 読み取り遅延バリア](../BE-0332-read-lag-barrier/BE-0332-read-lag-barrier-ja.md) —
  本項目による影響は受けません。すべての`tree`読み取りに対して、BE-0423のときと同じように働き
  続けます。

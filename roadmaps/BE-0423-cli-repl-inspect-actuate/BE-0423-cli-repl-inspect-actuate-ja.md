[English](BE-0423-cli-repl-inspect-actuate.md) · **日本語**

# BE-0423 — 要素ツリーを閲覧し id で操作する対話シェル

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0423](BE-0423-cli-repl-inspect-actuate-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0423") |
| 実装 PR | [#2009](https://github.com/bajutsu-e2e/bajutsu/pull/2009) |
| トピック | オーサリング体験 |
<!-- /BE-METADATA -->

## はじめに

`bajutsu repl`は、起動中のターゲットに対して手動で操作する対話シェルです。操作者はまずアプリを1回起動し、そのあとは1コマンドずつ入力します。`tree`は現在の画面の要素ツリーを読み取ります。`tap <id>`は、その要素の1つを操作します。操作者は、毎回の結果を確かめてから次のコマンドを決めます。

このシェルは、`record`(ゴール指向のAIオーサリング)と`crawl`(自律探索)の隣に位置します。両コマンドがすでに使っている[`Driver`](../../docs/ja/glossary.md#driver-backend-actuator-platform)インタフェースを通じて、ターゲットに到達する3つ目の手段です。`record`や`crawl`と異なり、`repl`は大規模言語モデル(LLM)に何も尋ねず、シナリオも書き出しません。

`repl`は、`query()`と、各[バックエンド](../../docs/ja/glossary.md#driver-backend-actuator-platform)がすでに実装している操作メソッドを呼び出すだけの、薄いループです。そのため、XCUITest、adb、Playwrightは、バックエンド固有の読み取りコードも操作コードもなしに、このシェルを手に入れます。起動と終了の経路だけは、この共通部分を超える分岐を持ちます(詳細設計を参照)。

## 動機

[セレクタ](../../docs/ja/glossary.md#シナリオのオーサリング)がどの要素に解決されるかを知る作業は、今日、その問い自体の重さに見合わないコストがかかります。操作者は、Xcode の Accessibility Inspector やブラウザの開発者ツールで、ツリーを目で読めます。しかし、その読み取りはバックエンド固有の手段に頼ります。しかも、シナリオのステップが実際に照合する、正規化された`id`・`label`・`traits`フィールドは表示されません。

既存のツールには、それぞれ足りない点があります。

- `bajutsu doctor`は、すでにコマンドラインからライブな画面を読みます。ただし、評価するのはidの網羅率やnamespace外のidといった規約です。ドライバを1回だけ起動して読み取り、すぐに破棄します。ツリーを一覧として読ませることも、idを操作することもしません。
- `record`と`crawl`は、どちらもAI呼び出しを介してアプリを操作します。出力は、シナリオや画面マップという、それぞれ固有の成果物向けに整形されます。1つの問いにその場で答える出力ではありません。
- `serve`のWeb UIが持つAuthorビューは、これより近いところまで来ています。`/api/capture/start`と`/api/capture/resolve`は、ライブなドライバを起動します。AI呼び出しなしに、画面上のクリックをセレクタへ解決します([BE-0262](../../roadmaps/BE-0262-serve-author-live-step-picker/BE-0262-serve-author-live-step-picker-ja.md))。ただし、このピッカーはブラウザのタブの中で動きます。シナリオに足す1ステップを選ぶために作られていて、打ったidが解決するかどうかには答えません。

現在の画面はどう見えていて、その中の1つのidを操作すると何が起きるか。この問いに、バックエンドに依存せず、コマンドラインから直接答える手段は、どれも用意されていません。

`repl`は、この問いに直接答えます。`tree`は、現在の要素ツリーを表示します。`tap <id>`は、その要素の1つを操作します。もう一度`tree`を実行すれば、何が変わったかがわかります。AIの往復も、シナリオファイルも、バックエンドごとのインスペクタを覚える手間も要りません。

オーサリング中にセレクタが一致しない事態は、よくあります。原因は、idの誤記、別の要素による隠蔽、待機後にしか現れない要素などです。今日、原因を突き止めるには、候補のシナリオステップを書いて実行します。そのうえで、マニフェストが捕捉したツリーを事後に読みます。`repl`は、この一連の作業を、起動中のアプリに対して`tree`と`tap`を打つだけに縮めます。

実装後は、操作者が`bajutsu repl --target <name>`を起動して`tree`を実行します。見えている要素のidを読み、そのうち1つを`tap`すれば、画面の変化を手元だけで確かめられます。この確認は数秒で終わります。runを実行してレポートを読むという一連の流れに、取って代わります。

## 詳細設計

`bajutsu repl --target <name> [--udid <id>] [--backend <list>] [--erase/--no-erase] [--headed/--no-headed] [--browser <engine>] [--config <path>]`は、アプリを起動します。起動の経路は、既存のヘルパーを再利用します。

- ターゲットの解決とバックエンドの選択は、`record`がすでに呼んでいる共有CLIヘルパー(`bajutsu/cli/_shared.py`)を再利用します。ヘルパーは、`_load_effective_with_source`と`_select_actuator_or_exit`です。
- デバイスの起動は、`launch_driver`(`bajutsu/common/runner/launch.py`)を再利用します。`record`と`crawl`も、同じ組み合わせを使っています。`udid`を解決し(`playwright`アクチュエータでは省略)、デバイスを起動してから、ドライバを渡します。
- この起動より前に、`repl`は設定で`launchServer`が宣言されている場合に、ターゲット自身のサーバを起動します。使うのは`_start_launch_server_or_exit`ヘルパーです。`run`・`record`・`crawl`・`audit`がすでに共有しています(`bajutsu/cli/_shared.py`)。終了時には、`record`や`crawl`と同じく`atexit`でサーバを停止します。この手順がなければ、`baseUrl`を`launchServer`から供給するWebターゲットでは、ブラウザが何も待ち受けていないホストを開いてしまいます。`tree`は、どれもエラーページの要素を読むことになります。
- `--headed`/`--no-headed`と`--browser`は、Webバックエンド専用です。どちらも`bajutsu/cli/_shared.py`の共有ヘルパーを再利用します。`_with_headed`は、`record`・`crawl`・`run`が呼びます。`_resolve_browser`は、`record`と`run`が呼びます(`crawl`に`--browser`はありません)。この2つのフラグは、このシェルにとって特に重要です。headlessなブラウザのままでは、操作者が変化を確かめる画面そのものがありません。

起動すると、`repl`は解決したバックエンドとターゲットを表示します。続けて、`bajutsu>`というプロンプトを出します。

v1のコマンドは、id中心の小さな集合にとどめます。

| コマンド | 動作 |
|---|---|
| `tree [--json]` | ドライバが`SettledReadProvider`を実装していれば(adb)`driver.settled_query()`を、そうでなければ`driver.query()`を呼び、`id`・`label`・`traits`・`value`・`frame`の表(またはJSON)として表示します |
| `find <substring>` | 同じツリーのうち、`id`または`label`に`<substring>`を含む行だけに絞り込みます |
| `tap <id>` | `driver.tap({"id": "<id>"})`を呼びます |
| `type <id> <text>` | `<id>`をタップしてフォーカスしてから、`driver.type_text("<text>")`を呼びます |
| `back` | `driver.back()`を呼びます |
| `screenshot [path]` | `driver.screenshot(path)`を呼びます。`path`省略時は自動で名前を付けます |
| `help` | 上記のコマンド一覧を表示します |
| `exit` / `quit` | シェルを終了します。ローカルなSimulator・デバイス側のアプリ(`xcuitest`・`adb`)はそのまま起動状態を保ちますが、このプロセス自身が所有するセッションは、対応する`Environment.teardown`と同じ呼び出しで終了します。Webバックエンドのブラウザは`cast(base.BackendLifecycle, driver).close()`(`WebEnvironment.teardown`と同じ呼び出しで、`Driver`自体は`close()`を宣言していません)、`--udid https://…`のライブ経路では`XcuitestLiveEnvironment.teardown`が削除するWebDriverセッション(放置すればグリッド上に予約されたまま期限切れまで残ります)です |

ツリーを読む、あるいはツリーに対して解決するすべてのコマンドは、`run`の各ハンドラと同じやり方で、アクチュエーション用の読み取りを求めます。`SettledReadProvider`を実装しているドライバは、`settled_query()`で答えます。プロトコルは`bajutsu/common/drivers/base/settled_read_provider.py`にあります。ドライバ自身の読み取りがすでに十分であれば、素の`query()`で答えます。

read-lagバリア([BE-0332](../../roadmaps/BE-0332-read-lag-barrier/BE-0332-read-lag-barrier-ja.md))が、adbで`settled_query()`を安全にします。`tap`の直後に実行した`tree`が、操作前の古いスナップショットを読んでしまう事態を防ぎます。

`resolve_unique`がすでに持つ、「0件または複数件の一致は失敗させる」という契約は、そのコマンドを即座に失敗させます。メッセージは、`run`が出すのと同じ`ElementNotFound`・`AmbiguousSelector`です。どちらの経路も、操作者の意図を推測しません(prime directive 2、決定性優先)。

`tap`は、`driver.tap()`を直接呼びます。`run`自身が使う`_tap_with_recovery`(`bajutsu/common/orchestrator/actions/handlers/gestures.py`)は経由しません。そのため、別の要素に覆われたターゲットに対して、`repl`は`ElementNotTappable`を送出します。`run`なら、まず範囲を区切ったスクロールを試みて成功します。これは意図したv1の割り切りです。こっそり回避すべき不具合ではありません(検討した代替案を参照)。

`tap`と`type`は、要素を`id`だけで指定します。これは、`run`が受け付ける完全な[セレクタ](../../docs/ja/glossary.md#シナリオのオーサリング)構文(`id`・`idMatches`・`label`・`labelMatches`・`traits`・`value`・`within`・`index`)より、意図的に狭い範囲です。絞り込みの理由は2つあります。1つ目は、v1をレビューしやすい小ささに保てることです。2つ目は、シェルの実際の使い方に合うことです。`tree`はすでに各要素の`label`と`traits`を表示します。操作者は、その行を読んで、idを入力するだけで済みます。

`id`を持たない要素は、完全なセレクタなら`label`や`traits`で指定できます。id単独のv1では、そこまで届きません。ツリーに要素そのものが現れない場合は、別の問題です。典型は、no-idのアプリのタブバーにある個々のタブです。この問題を埋めるのは、`record`のvisionフォールバックの役目です(`docs/recording.md`)。`repl`はそれを行いません。visionを足すと、AI呼び出しを避ける目的で作ったツールに、AI呼び出しを呼び戻すことになるからです。

`tap`と`type`を、残りの`Selector`のフィールド(`idMatches`・`label`・`labelMatches`・`traits`・`value`・`within`・`index`)に広げる作業は、自然な拡張です。シェル本体がリリースされたあとの、別スコープの作業とします。

ジェスチャ(`swipe`・`scroll`・`pinch`・`rotate`)と、プラットフォーム固有の操作(`set_picker_value`・`select_option`)は、v1の対象外とします。`tap`・`type_text`・`back`・`screenshot`は、操作者がまず使う操作をひととおり満たします。セレクタを確かめるときと、手で1つの流れをたどるときです。残りの`Driver`のメソッドは、コマンド解析の形が固まったあとであれば、素直に追加できます。すべてを一度に追加すると、この項目は、1回でレビューできる変更の範囲を超えてしまいます。

`repl`は、`record/`・`crawl/`と並ぶ、独立した最上位パッケージ`bajutsu/repl/`に置きます。`docs/architecture.md`のモジュール一覧に行を1つ追加するので、`make lint-module-map`は通り続けます。高速スイートには、`FakeDriver`を使った次のテストを付けます。

- コマンド解析
- `tree`・`find`の表示
- `ElementNotFound`・`AmbiguousSelector`・`ElementNotTappable`の各経路

`run`の各ハンドラも、同じようにテストしています。そのため、新しいモジュールは最初のPRから`coverage-floors.json`のファイル単位のフロアを満たします。あとから追いかける必要はありません。

## 検討した代替案

- **プラットフォーム固有のツール(Xcode の Accessibility Inspector、ブラウザの開発者ツール)でツリーを読む。** 却下しました。各ツールはバックエンドごとに異なります。その結果は、バックエンドに依存しない唯一の[`Driver`](../../docs/ja/glossary.md#driver-backend-actuator-platform)という接点を通りません。Bajutsuの他の部分は、この接点を通じてツリーを読みます。しかも、どのツールも、Bajutsuのセレクタが実際に照合する、正規化された`id`・`label`・`traits`フィールドを表示しません。そこで読んだidが、`run`が解決するidと一致する保証はありません。
- **新しいコマンドの代わりに、`serve`のAuthorライブステップピッカーを使う。** この項目では却下しました。`/api/capture/start`と`/api/capture/resolve`([BE-0262](../../roadmaps/BE-0262-serve-author-live-step-picker/BE-0262-serve-author-live-step-picker-ja.md))は、AI呼び出しなしにライブなドライバを起動します。画面上のクリックをセレクタへ解決する機能も、すでに提供しています。ただし、それはブラウザのピッカーを通してです。このピッカーは、シナリオに足す1ステップを選ぶために作られています。打ったidが解決するかどうかには答えません。端末でidを打ってその場で操作する経路もありません。ブラウザのタブもエディタも開かずに済ませたいという、この項目が動機とする、より狭い需要には答えません。
- **`repl`の`tap`を、`run`の`_tap_with_recovery`経由にする。** v1では却下しました。ドライバ自身の`ElementNotTappable`をそのまま見せるほうが、セレクタを確かめている最中には、より役立つ答えになります。何を名指しするかは、バックエンドによって異なります。
  - adb、XCUITestのライブ経路、`FakeDriver`は、`base.raise_if_covered`を通じて覆っている要素を名指しします。
  - Webバックエンドは、自前のヒット判定を通じて名指しします。
  - XCUITest Simulatorドライバは、`element resolved but not hittable`とだけ報告します。

  回復を望む操作者は、シナリオ側で明示的な`scroll`ステップを書き、そこで`tap`すればよいだけです。代償は、`repl`が、`run`なら回復する場面で失敗を報告することです。覆われたターゲットに対して、両者の答えが食い違いうる点は、詳細設計で述べたとおりです。
- **新しいコマンドの代わりに、`record`に「手動モード」フラグを足す。** 却下しました。`record`のループは、スクリーンショットから操作を提案する`ClaudeAgent`を中心に組まれています。ループは、必ずシナリオの書き出しで終わります。そこに人間が打つコマンドの経路を継ぎ足すと、AI駆動の経路と非AIの経路が絡み合います。`repl`は、意図してシナリオを書き出さないツールです。コマンドを分けたほうが、両方とも読みやすく保てます。
- **最初から`tap`・`type`に完全なセレクタ(`label`・`labelMatches`・`index`)を受け付けさせる。** v1では見送り、id単独の指定にとどめました(詳細設計を参照)。この小さい範囲でも、この項目の動機となった問いにはすでに答えられます。コマンドライン上でのセレクタ構文の解析まで一度に決める変更より、1回でレビューできる変更として収まります。
- **標準入力やファイルからコマンド列を読む、非対話的なモード。** この項目では見送りました。対話シェル単体で、まずコマンドの集合を検証できます。バッチモードは、そのコマンド集合が固まったあとの、別スコープの拡張です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 新しい`bajutsu/repl/`パッケージでの`bajutsu repl`コマンドの土台。
  `_load_effective_with_source`・`_select_actuator_or_exit`・`_start_launch_server_or_exit`・
  `launch_driver`の再利用(launchサーバは`atexit`で停止)、
  `--headed`・`--no-headed`・`--browser`、`bajutsu>`プロンプトのループ、`help`・`exit`・`quit`
  (終了時のWebバックエンドの`cast(base.BackendLifecycle, driver).close()`と、
  `--udid https://…`のライブ経路での`XcuitestLiveEnvironment.teardown`によるWebDriverセッション
  終了を含みます)。
- [x] `tree`・`tree --json`・`find <substring>`。`settled_query()`・`query()`とread-lagバリアの
  経路を再利用します。
- [x] `tap <id>`・`type <id> <text>`。`run`と同じ形で`ElementNotFound`・`AmbiguousSelector`・
  `ElementNotTappable`を表示します。
- [x] `back`・`screenshot [path]`。
- [x] `FakeDriver`を使った高速スイートのテスト。コマンド解析、`tree`・`find`の表示、
  `ElementNotFound`・`AmbiguousSelector`・`ElementNotTappable`の各経路を対象にします。
- [x] `docs/cli.md`と`docs/ja/cli.md`のリファレンス節。あわせて、`repl`によって古くなるCLIの一覧
  (`docs/glossary.md`と`docs/ja/glossary.md`のCLI動詞の表、`docs/architecture.md`のコマンド一覧
  ([BE-0113](../../roadmaps/BE-0113-design-doc-realignment/BE-0113-design-doc-realignment-ja.md)))
  と、`bajutsu/repl/`自身の`docs/architecture.md`モジュール表への行(`make lint-module-map`)も
  更新します。

ログ：

- [#2009](https://github.com/bajutsu-e2e/bajutsu/pull/2009) — 作業単位 1〜6、項目全体。`bajutsu/repl/` パッケージを追加しました。`render.py` が
  `id` / `label` / `traits` / `value` / `frame` の表とそのままの JSON を出力し、`session.py` がコマンド
  集合（`tree` / `find` / `tap` / `type` / `back` / `screenshot` / `help` / `exit`）を持ち、`loop.py` が
  `bajutsu>` のプロンプトループ、`cli.py` が Typer コマンドです。`cli.py` は `record` の起動ヘルパと、
  `crawl` の「1 つの environment で起動と終了を行う」形を再利用しており、終了時にはこのプロセス自身が
  持つブラウザまたは live 経路の WebDriver セッションだけを閉じます。`find` の一致は、セレクタの照合と
  同じく大文字と小文字を区別します。コマンドは `_FEATURE_MODULE_NAMES` に登録し、`capabilities.py` で
  Claude 不要として分類し、`bajutsu.repl` を決定的コアの import 契約に加えました。`tests/test_repl.py`
  が表示、各動詞が呼ぶ driver、`ElementNotFound` / `AmbiguousSelector` / `ElementNotTappable` の各経路、
  ループの 3 通りの抜け方（`exit`/`quit`、入力の終端、割り込み）を対象にします。ドキュメントは英日の両方を更新しました。`cli` のリファレンス節、
  用語集の CLI 動詞の表、AI 境界の分離表、`getting-started` の `--help` 一覧、`architecture` のコマンド
  一覧とモジュール表、`developer-guide` のパッケージ表と地図、そして両方の README です。

## 参考

- [`Driver`](../../docs/ja/glossary.md#driver-backend-actuator-platform)プロトコル —
  `bajutsu/common/drivers/base/driver.py`
- [`Selector`](../../docs/ja/glossary.md#シナリオのオーサリング) —
  `bajutsu/common/scenario/models/selector.py`
- `SettledReadProvider` — `bajutsu/common/drivers/base/settled_read_provider.py`
- `BackendLifecycle` — `bajutsu/common/drivers/base/backend_lifecycle.py`
- `_start_launch_server_or_exit` — `bajutsu/cli/_shared.py`
- `XcuitestLiveEnvironment` — `bajutsu/common/platform_lifecycle/environments/xcuitest_live.py`
- `record`と`crawl` — `repl`が隣に位置する、既存の2つのTier 1オーサリング経路(`docs/cli.md`)
- [BE-0332 — read-lagバリア](../../roadmaps/BE-0332-read-lag-barrier/BE-0332-read-lag-barrier-ja.md)
- [BE-0262 — Author エディタにライブなステップ選択と target 単位に絞った run を導入する](../../roadmaps/BE-0262-serve-author-live-step-picker/BE-0262-serve-author-live-step-picker-ja.md)

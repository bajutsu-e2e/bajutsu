[English](BE-0172-run-loop-step-decomposition.md) · **日本語**

# BE-0172 — run 経路のステップループとシナリオ実行関数の分解

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0172](BE-0172-run-loop-step-decomposition-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0172") |
| 実装 PR | [#693](https://github.com/bajutsu-e2e/bajutsu/pull/693) |
| トピック | Codebase quality & technical debt |
| 関連 | [BE-0143](../BE-0143-run-command-decomposition/BE-0143-run-command-decomposition-ja.md)、[BE-0092](../BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction-ja.md) |
<!-- /BE-METADATA -->

## はじめに

決定的な `run` 経路のなかでもとりわけ実行頻度の高い 2 つの関数、すなわちステップループ
（`bajutsu/orchestrator/loop.py:_run_steps`）とシナリオ単位の実行関数
（`bajutsu/runner/pipeline.py:run_one`）は、状態を明示的な引数ではなく、可変リストや外側スコープの変数への
クロージャとして持っています。本項目は、その隠れた状態を素直なデータを受け渡す名前付きヘルパへ引き上げる、
挙動を保存したリファクタリングです。すべてのステップの合否を決めるコードが最初から最後まで読み通せるようになり、
シミュレータなしで単体テストできるようになります。これは、[BE-0143](../BE-0143-run-command-decomposition/BE-0143-run-command-decomposition-ja.md)
が `run` の CLI コマンドに対して行った分解の、run 経路における兄弟にあたり、あの項目が触れなかった 2 つの関数を対象にします。

## 動機

次の 2 つの関数が run ループの制御フローを集約しており、読む・テストする・変更するのが難しくなっています。

- **`bajutsu/orchestrator/loop.py:_run_steps`（304〜454 行、150 行の関数）は、すべてのステップを駆動します。**
  この関数は、ステップごとの状態を、入れ子の再帰クロージャが捕捉する要素数 1 の可変リストで引き回しています。
  `step_counter = [0]` は共有のステップ番号で、`exec_steps` の内部でインクリメントされます。
  `_active_driver: list[base.Driver] = [driver]` は現在アクティブなドライバで、web ステップが 2 つ目のドライバへ
  制御を渡してから戻すときに入れ替えられます。再帰（`exec_steps` は `for_each` やネストしたステップ群のために
  自分自身を呼びます）は入口で `_active_driver[0]` を読み、本体の途中で書き換えるため、あるステップ群の挙動は
  先行する反復が積み上げた状態に依存します。どれか 1 つの分岐を理解する、あるいは安全に手を入れるには、
  クロージャ全体を頭に入れておく必要があり、`[0]` で箱に入れた変数は Python のクロージャ規則を迂回して
  変更を持ち込むためだけに存在しています。これは、正しさがもっとも問われる経路で変更を危うくする、
  まさにその形です。

- **`bajutsu/runner/pipeline.py:run_one`（およそ 140〜229 行）は、シナリオ単位の実行関数で、`run_all`（74〜229 行）
  に入れ子になっています。** この関数は外側スコープの広い範囲を捕捉します。`eff`、`lease`、`mailbox`、
  `redactor`、`caps`、`run_dir`、`bindings`、`clock`、`progress`、`total`、baselines と schemas の各ディレクトリ、
  `golden_context`、アラートガードのハンドラなどです。クロージャである
  ために、`run_all` のセットアップを丸ごと再構築しない限り単体テストできません。しかも `run_all` はこの関数を
  逐次と `ThreadPoolExecutor`（`pool.map(lambda pair: run_one(*pair), …)`）の 2 通りで実行するため、暗黙に
  捕捉された状態はワーカースレッド間でも共有されます。スレッド処理は現状で正しく動いていますが、クロージャは
  各ワーカーがどの状態に触れるのかを見えにくくしています。

どちらの関数も、ここで挙動を変えるわけにはいきません。決定的な Tier-2 のゲート上にあるからです。したがって
得られる価値はもっぱら構造上のもので、要となるコードを読めてテストできる状態にすることにあります。これは
規模 M の作業ですが、継ぎ目はすでに存在します。`_run_steps` の web ステップのドライバ入れ替えとステップ番号の
管理はそれぞれ自己完結しており、`run_one` の捕捉変数はそのまま引数リストになります。CLI コマンドの
カバレッジを整えた [BE-0142](../BE-0142-cli-command-coverage/BE-0142-cli-command-coverage-ja.md) と、クロールの
コーディネータ抽出（[BE-0092](../BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction-ja.md)）が、
その手順を示しています。まず回帰テストの網を敷き、それから再設計せずに移動して名前を付けます。

## 詳細設計

このリファクタリングは**挙動を保存します**。ステップの意味、ドライバのライフサイクル、証跡の取得、判定、
スレッド処理、その他あらゆる観測可能な出力を変えません。プライムディレクティブ 1 は守られます。`run` や CI の
経路に LLM を加えることはなく、決定的なコードの形を整えるだけです。回帰テストの網は既存の `tests/` スイートと、
先に埋める不足分（下記）です。作業は互いに独立した次の 4 単位に MECE に分かれます。

- **回帰テストの網の確認（最初に行う）。** `_run_steps` と `run_one` が、高速でシミュレータ不要なテストによって
  分岐単位で覆われていることを確認します（ステップ番号の進行、`for_each` やネストした群、web ステップのドライバの
  入れ替えと復元、逐次実行と `ThreadPoolExecutor` 実行）。抽出に先立って不足する分岐テストを足し、緑の基準に対して
  移動を検証できるようにします。

- **`_run_steps` のステップ番号の状態を `[0]` の箱から引き上げる。** `step_counter = [0]` というクロージャへの
  持ち込みを、引数と返り値で受け渡す明示的なカウンタ（あるいは小さくまとまったヘルパオブジェクト）に置き換え、
  `exec_steps` が箱入りのリストを読み書きしないようにします。挙動は同じで、番号がシグネチャ上に見えるようになります。

- **`_run_steps` から web ステップのドライバ入れ替えを抽出する。** `_active_driver` の入れ替えと復元（web ステップが
  `web_driver` に制御を渡し、あとで `prev_driver` に戻す処理）を名前付きヘルパに移します。たとえば小さなコンテキスト
  マネージャや、`_exec_web_step(step, driver, …) -> (driver, result)` のような関数にして、`_active_driver[0]` を
  書き換える代わりにアクティブなドライバを明示的に受け取り返します。これで 2 つ目の箱入り変数がなくなり、
  ライフサイクルが単純でない唯一の分岐が切り離されます。

- **`run_one` を明示的なシグネチャのトップレベル関数に昇格する。** クロージャを、捕捉していた値を明示的な引数に
  取る独立した関数（あるいは小さな呼び出し可能な dataclass や `_ScenarioRunner`）に変え、`run_all` の 2 つの
  呼び出し箇所（逐次の内包表記と `ThreadPoolExecutor.map`）から値を渡すようにします。スレッドモデルは変わらず、
  共有状態が明示的な引数リストになるだけで、`run_one` は単独で単体テストできるようになります。

各単位はそれぞれ独立して小さな PR で投入でき、いずれも単独でスイートを緑に保ちます。

## 検討した代替案

- **どちらの関数もそのままにして、コメントに頼る。** [BE-0143](../BE-0143-run-command-decomposition/BE-0143-run-command-decomposition-ja.md)
  が `run` について退けたのと同じ理由で退けます。これらの関数にはすでに工程ごとのコメントがありますが、
  `[0]` の箱入りクロージャ状態と多数の自由変数の捕捉こそ、コメントでは安全に編集できるようにできない部分です。
  リスクは説明文の不足ではなく、隠れた可変状態にあります。

- **BE-0143 に取り込む。** 退けます。BE-0143 は `cli/commands/run.py` の `run` 関数に限定され、すでに出荷済みです
  （[#624](https://github.com/bajutsu-e2e/bajutsu/pull/624)）。こちらは別のファイルの別の関数であり、別項目として
  追跡するほうが各 PR を小さく保ち、回帰テストの網の焦点も絞れます。

- **ステップループを再設計する（明示的な状態機械やイテレータプロトコルなど）。** スコープ外として退けます。それは
  決定的なゲート上での挙動に影響する再設計であり、まさに本項目が避けるものです。本項目は現在の設計を読めるように
  するだけにとどめます。再設計が必要になるなら、それは固有のリスクを負う別の提案です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 回帰テストの網の確認：`_run_steps` と `run_one` に対する高速でシミュレータ不要な分岐テストを先に投入する。
- [x] `_run_steps` のステップ番号の状態を `[0]` の箱から明示的なカウンタへ引き上げる。
- [x] web ステップのドライバ入れ替えを、ドライバを明示的に受け渡す名前付きヘルパへ抽出する。
- [x] `run_one` を、捕捉していた値を明示的な引数に取るトップレベル関数に昇格する。

## 参考

- [BE-0143 — run コマンドの巨大関数の分解](../BE-0143-run-command-decomposition/BE-0143-run-command-decomposition-ja.md)（`run` CLI コマンドに対する兄弟の分解）
- [BE-0092 — クロールコーディネータの抽出](../BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction-ja.md)（先例：run 経路のコーディネータを挙動保存で抽出）
- [BE-0142 — CLI コマンドのカバレッジ](../BE-0142-cli-command-coverage/BE-0142-cli-command-coverage-ja.md)（「まず回帰テストの網を敷く」先例）
- `bajutsu/orchestrator/loop.py:304`（`_run_steps`）、`bajutsu/runner/pipeline.py:140`（`run_one`）

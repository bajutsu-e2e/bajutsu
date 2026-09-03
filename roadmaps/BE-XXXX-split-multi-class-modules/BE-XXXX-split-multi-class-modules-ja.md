[English](BE-XXXX-split-multi-class-modules.md) · **日本語**

# BE-XXXX — bajutsuの複数クラス共存モジュールを1クラス1ファイルへ分割する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-split-multi-class-modules-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

`bajutsu/`配下には、1つのモジュールに複数のトップレベルクラスを定義しているファイルが85個あります。
合計すると446クラスです。この項目は、この85ファイルそれぞれをパッケージディレクトリへ分割します。
各クラスはそのディレクトリ配下の専用モジュールへ移動します。パッケージの`__init__.py`が公開シンボル
をすべて再エクスポートするため、既存のimportパスはすべてそのまま解決できます。対応するテストファイル
がある場合は、テストも同じ方法で分割します。この変更は物理的な再配置です。どのクラスも責務が増減せず、
テストが新たに検証する内容もありません。この変更で新たに書く文章は1種類だけです。既存のlintルールが
求める、新規ファイルごとの1行のモジュールdocstringです(詳細設計の「既存の4つの仕組み」を参照)。

## 動機

2つのファイルが、この問題の規模を示しています。`bajutsu/common/scenario/models/actions.py`は645行
に40クラスを詰め込んでいます。`bajutsu/common/drivers/base.py`は1044行に23クラスです。残り83ファイル
の多くも、規模は小さいながら同じ形をしています。1つのクラスを開きたい読み手は、エディタのファイル
ツリーからそこへたどり着けません。ファイルツリーはファイル名で索引を作りますが、目的のクラスは他の
数十クラスとファイル名を共有しているからです。読み手はファイルを開いて中を検索するか、先に行番号を
grepで調べることになります。

AIエージェントが1クラスだけを編集する場面でも、同じコストが別の形で生じます。目的のクラスにたどり
着くには、結局ファイル全体を読み込む必要があります。1クラスへの編集でも、無関係な残り39クラスが
コンテキストに乗ります。ファイルはクラスが追加されるたびに大きくなるだけなので、このコストは
積み上がります。`actions.py`に次のクラスが1つ増えるたびに、そのファイルは追加した本人だけでなく、
以後すべての読み手と編集者にとって大きくなります。

1クラス1ファイルへの分割は、この両方のコストを一度に取り除きます。ファイル名での検索やファイル
ツリーのクリックが、クラスへ直接届くようになります。1クラスへの編集は、そのクラスのファイルだけを
読み込めば済みます。この効果が実際に得られたかどうかは、他に何も読まずに確認できます。`LongPress`は
今日時点では`actions.py`のどこかにありますが、分割後はファイルツリーから`actions/long_press.py`へ
直接たどり着けます。途中の検索は要りません。

## 詳細設計

### 分割規則

トップレベルクラスを2つ以上持つ85ファイルそれぞれを、同名のパッケージディレクトリに置き換えます。
規則は5つです。

1. **1クラス1ファイル。** `foo.py`は`foo/`になります。パッケージの`__init__.py`は、元の`foo.py`の
   モジュールdocstringを引き継ぎます。続けて、全公開クラスを元の宣言順にimportして再エクスポート
   し、再エクスポートするすべての名前を`__all__`へ書き出します。元のファイルに`__all__`があった
   かどうかに関わらず、`__all__`を新たに書き出します。`pyproject.toml`のruffの`select`には`F`が
   含まれ、`__init__.py`向けのper-file-ignoreはありません。再エクスポートするためだけのimportは、
   `__all__`がなければ`F401`となり`make lint`を失敗させるためです。各クラスは
   `snake_case(クラス名).py`へ移動します。`_Model`のような先頭アンダースコア付きの非公開クラスは
   `_model.py`となり、ファイル名にもアンダースコアを残します。この規則は、Pydanticモデル・
   `TypedDict`・`Protocol`・`Exception`・素の`dataclass`のいずれであっても、同じ形で適用します。
2. **トップレベル関数は1ファイルにまとめて残します。** この項目の対象はクラスであって関数では
   ありません。1つのファイルが持つトップレベル関数は、新しいパッケージ内の`_functions.py`へ
   まとめて移動します。`__init__.py`への分散や、関数1つごとのファイル分割はしません。85ファイル
   中62ファイルは、クラスと並んでトップレベル関数を1つ以上定義しています。
   `bajutsu/common/backend_cli/adb.py`だけで66個です。この規則がなければ、これらの関数は結局
   `__init__.py`へ戻り、もっともコード量の多いファイル群で削減効果を打ち消してしまいます。
   `__init__.py`は、公開関数を`_functions.py`からクラスと同じ方法で再エクスポートします。
3. **パッケージ内の明示的なimport。** あるクラスが、同じ元ファイル内の別クラスを継承・参照している
   場合があります。分割後は相対importで明示的に読み込みます(`from ._model import _Model`)。2つの
   クラスは、もはやモジュールスコープを共有していないためです。
4. **所有者を持たないモジュールレベルのコードは`__init__.py`へ、単一の所有者を持つコードはその
   所有者のファイルへ。** 複数の分割後クラスから実際に使われるコードは、他に行き場がありません。
   一例は
   [`serve/server/models.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/bajutsu/serve/server/models.py)
   の`_JSON`です。SQLAlchemyの列型バリアントで、6つの異なるO/Rマッパーのモデルクラスが
   `mapped_column`へ渡します。

   1つのクラスや関数だけに紐づくコードは、モジュールレベルにあっても、その所有者自身のファイルに
   留めます。移動すると参照が壊れます。あるいは、`global`でその値を再束縛する関数へ移動した場合、
   エラーを出さないまま値の更新が止まります。`global`は別モジュールの名前には届かないためです。
   [`assertions.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/bajutsu/common/scenario/models/assertions.py)
   の`_ASSERTION_KINDS`が前者の例です。`Assertion.model_fields`から導出され、読むのは
   `Assertion._one_kind`だけなので、`Assertion`と一緒に留まります。`drivers/playwright.py`の
   `_PW_ERRORS`が後者の例です。`_playwright_error_types`がオプショナルなimportをメモ化するために
   `global`で再束縛しており、そのメモは規則2でその関数が行き着く先、つまり`_functions.py`に
   留まります。
5. **新たな循環importは既存の遅延パターンで対処します。**
   `bajutsu/common/config/schema.py`の`Config`モデルはすでに`bajutsu.common.config.resolve`を
   遅延importしています。循環を避けるためです。分割によって新たに生じる循環importも、同じ方法で
   対処します。

### 実現方式: 構文解析を行うスクリプトであり、手作業の編集ではない

規則1〜3と5は、バッチごとに1回実行するPythonスクリプトが機械的に適用します。人間であれAIエージェント
であれ、エディタで1ファイルずつ分割するのではありません。AIエージェントが446クラスを1つずつ読んで
書き換えると、消費するトークン量はおおむねその件数に比例します。スクリプトは一度だけ執筆コストを
払えば、以降はバッチを何回実行してもトークンを消費しません。

このスクリプトは標準ライブラリの`ast`ではなく[`libcst`](https://github.com/Instagram/LibCST)で
各対象ファイルを解析します。`ast`はコメントを保持しないためです。これらのファイルの多くのクラスは、
設計判断を説明する先頭のコメントを持っており、分割でそれを失うわけにはいきません。スクリプトは
トップレベルの`class`または`def`ノードを、その先頭のコメントブロックごと抽出し、バイト単位で規則1・
規則2が指定するファイルへ書き出します。抽出した各クラスの本体を走査して兄弟のトップレベル名への
参照を検出し、規則3が必要とする相対importを生成します。`__init__.py`も同じ解析結果から書き出し、
元のモジュールdocstring、クラスや関数ごとのimport1行、`__all__`のリストを含めます。
`libcst`は、このスクリプト専用の開発時依存であり、実行時の依存にはなりません。

規則4の2択、つまりモジュールレベルのコードに単一の所有者がいるかいないかは、スクリプトでは
判断しません。この判断には、`_ASSERTION_KINDS`と`_PW_ERRORS`にこの設計が下したのと同じ判断力が
必要だからです。この判断と、新規モジュールごとの`D100`のdocstringを執筆することが、各バッチで
手作業のまま残る2つの作業です。

このスクリプトへの信頼は、このコードベースの他の部分と同じ方法で獲得します。専用のテストスイート
(`tests/test_split_modules.py`など)を用意し、上記の各規則を小さなfixtureモジュールに対して検証
します。先頭コメント付きのクラス、兄弟クラスを参照するクラス、`__all__`があるファイルとないファイル、
モジュールレベルの名前を`global`で再束縛する関数、といった例です。このスイートが通ってから、
スクリプトを実際のファイルに対して実行します。実際のファイルへバッチごとに適用していく作業は、
以下の「進捗」のバッチ項目が追跡します。

### テスト側の分割

対応するソースファイルを持つテストファイルの例を挙げます。
`bajutsu/common/scenario/models/actions.py`に対する`tests/scenario/test_models_actions.py`です。
ソース側と同じ方法で、クラスごとに1テストモジュールへ分割します。分割後のテスト
ディレクトリには、空の`__init__.py`を置きます。`pyproject.toml`の`[tool.pytest.ini_options]`は
`--import-mode`を指定していません。デフォルトの`prepend`モードでは、テストモジュールのファイル名が
テストツリー全体で一意でなければなりません。空の`__init__.py`は、分割後のディレクトリをパッケージ化
します。これでこの制約を避けられます。85ファイルの中には、重複するクラス名が8組あります。
`DeviceError`と`Env`は、`backend_cli/adb.py`と`backend_cli/simctl.py`の両方でクラス名です。
パッケージ化していなければ、この2ファイルを分割したテストは同じ`test_device_error.py`
という名前で衝突します。結合テストだけでカバーされ、専用のユニットテストファイルを持たないソース
ファイルには、新規のテストファイルを追加しません。この項目の範囲は既存のものを分割することであり、
存在しないテストの網羅を新たに追加することではありません。

モジュールパスでパッチを当てているテストは、対応するソースファイルと同じコミットで参照先を
書き換えます。`tests/`は`monkeypatch.setattr("bajutsu.<module>.<name>", ...)`という形の呼び出しを
100箇所ほど持っています。`__init__.py`が再エクスポートしているだけの名前にパッチを当てると、
呼び出し元が実際に参照する束縛ではなく再エクスポート側が差し替わります。この不具合は、気づかずに
通るケースとエラーになるケースの両方があります。

`bajutsu.run.notify._RETRY_DELAY`(4箇所、バッチ2)は気づかずに通るケースです。`_deliver`は
規則2により`_functions.py`へ移動し、そこで別の束縛を持ちます。パッチは効かなくなり、リトライの
テストはパッチされた値の代わりに本物のスリープを実行します。
`drivers.playwright._PW_ERRORS`と`drivers.xcuitest.XcuitestDriver`(バッチ7)も同じ形で気づかずに
通ります。

`drivers.base.time.sleep`と`.time.monotonic`(10箇所、バッチ7)は逆の形です。`base/__init__.py`が
`time`をimportしなくなった時点で`AttributeError`となり、その場で気づきます。

### 既存の4つの仕組みが今日のファイルパスを前提にしている箇所

`coverage-floors.json`は、ファイルパスをキーに1ファイルあたりのカバレッジ下限を記録しています
([BE-0385](../BE-0385-coverage-floor-continuous-ratchet/BE-0385-coverage-floor-continuous-ratchet-ja.md))。
分割で生まれた新しいパスには、分割した時点ではまだ下限の記録がありません。`scripts/coverage_floors.py`
はこの状態をエラーではなく情報として扱うため、分割が進行中の間は害がありません。`make coverage-floors`
を最後のバッチが終わったあとに一度実行すれば、新しいパスを含むスナップショット全体を再生成できます。

`Makefile`の`DOCSTRING_PATHS`変数は、BE-0065のGoogleスタイルdocstring移行のもとで
`lint-docstrings`が対象とするモジュールを列挙しています。今日の85ファイルのうち26ファイルは、
`.py`付きの正確なパスでこの変数に列挙されています。`bajutsu/common/drivers/base.py`や
`bajutsu/analysis/audit.py`がその例です。残り15ファイルは、ファイル単位ではなくディレクトリ単位の
エントリですでにカバーされています。正確なパスで列挙されているファイルを分割してエントリを更新
しなければ、`lint-docstrings`が壊れます。そのエントリが指す先のファイルは、もはや存在しないためです。
ディレクトリ単位のエントリは変更が要りません。`bajutsu/common/scenario`は`models/actions.py`と
その兄弟ファイルをすでにカバーしており、`ruff`がディレクトリ全体を自ら走査するためです。

ただし、ディレクトリ単位のエントリにはファイル単位のエントリにはない帰結があります。
`lint-docstrings`は`D`ファミリー全体から`D102`/`D105`/`D107`を除いて選択しており、`D100`
(公開モジュールにdocstringがない)は除外されていません。`D100`は、41の対象ファイルのいずれかの下で
分割が生み出すすべてのモジュールに適用されます。ファイル単位から改名した26ファイルと、すでに
ディレクトリ単位でカバーされている15ファイルの両方が対象です。`bajutsu/common/scenario`は
すでに`models/actions.py`をカバーしています。そのため、40個の新しいクラス単位モジュールそれぞれに
専用のモジュールdocstringが必要で、単純な引き継ぎでは済みません。41の対象ファイル全体では
275個の新規モジュールdocstringを執筆することになります。これは機械的な引き継ぎではなく、
実際に書く文章であり、はじめにの枠組みだけでは捉えきれないコストです。`bajutsu/common/scenario`と
`bajutsu/common/drivers/`(バッチ11と7)がこのコストの大半を占めます。`actions.py`と`base.py`は、
もともとクラス数がもっとも多い2ファイルだからです。

[`docs/architecture.md`](../../docs/architecture.md)のモジュール一覧の表も、85ファイルのうち15件を
`.py`付きの正確なパスで列挙しています。

- `bajutsu/common/drivers/`配下の全ファイル
- `config_source.py`、`doctor.py`、`handoff.py`
- `provisioning/provision.py`、`provisioning/requirements.py`
- `run/notify.py`

`scripts/lint_module_map.py`は`make lint-module-map`(`make check`の一部)として実行されます。表の
エントリがツリー上に存在しないパスを指していると失敗するため、これらのファイルを分割して表の
エントリを書き換えなければ、ゲートが壊れます。同じチェックは`bajutsu/common/`配下も1階層だけ走査し、
文書化されていないサブパッケージがないか調べます。`config_source.py`・`doctor.py`・`handoff.py`は
`common/`の直下にあります。これらのいずれかを分割すると、その新しいパッケージディレクトリは、表の
エントリを新しいディレクトリパスへ同じコミットで更新しない限り、文書化されていないサブパッケージに
なります。表のパス欄を書き換えれば、両方のチェックを一度に満たせます。

`pyproject.toml`の`[tool.importlinter]`には3つのcontractがあります。いずれも
`bajutsu.common.drivers.base`を`source_modules`または`forbidden_modules`として名指ししています。
この分割が触れる残り2つの
モジュール、`bajutsu.common.drivers.actuation`と`bajutsu.common.doctor`もそこに加わっています。
これらのcontractは、決定論的なコア部分が周辺部から独立していることを守っています。

これらのエントリは変更が要りません。import-linterは、名前を挙げたモジュールをそれ自身と
すべての子孫として解決します。プレーンなモジュールがパッケージになっても、この扱いによって
カバーされたままです。`pyproject.toml`にある、artifact sinkに関するcontract自身のコメントが
すでに前提にしているのと同じサブツリーの意味論です。

1つだけ、サブツリーではなく厳密なエッジを名指ししているエントリがあり、それは壊れます。scenario
schemaと`Driver`Protocolをポータブルな内側の層に保つcontractには、`ignore_imports`エントリが
あり、1つのエッジ`bajutsu.common.drivers.base -> bajutsu.common.evidence.network`をカバー
しています。このエッジは、`Driver`Protocolが`network_collector`シグネチャのために持つ、
`TYPE_CHECKING`で守られた`Collector`への参照です。`Driver`はバッチ7で自分専用のファイルへ
移動します。実際のエッジはそのとき
`bajutsu.common.drivers.base.driver -> bajutsu.common.evidence.network`になります。`Driver`が
別のモジュールへ行き着けば、そのモジュールのエッジになります。記録されている文字列は、もう
一致しません。
`unmatched_ignore_imports_alerting`はデフォルトで`error`なので、`make lint-imports`はこの
古いエントリで失敗します。バッチ7のコミットで、新しいモジュールパスへ更新します。

### 作業の分解

85ファイルは、既存のディレクトリ境界に沿って14バッチにまとまります。各バッチは1本のPR上の
1コミットです。

| # | バッチ | ファイル |
|---|---|---|
| 1 | `bajutsu/analysis/` | `audit.py`, `coverage.py`, `flakiness.py`, `impact.py`, `stats.py` |
| 2 | 単発モジュール4件 | `bajutsu/cli/handoff.py`, `bajutsu/codegen/common.py`, `bajutsu/run/notify.py`, `bajutsu/triage/heuristic.py` |
| 3 | `bajutsu/common/agents/` + `bajutsu/common/ai/` | `agents/alerts.py`, `agents/claude.py`, `agents/claude_triage.py`, `agents/protocols.py`, `ai/base.py` |
| 4 | `bajutsu/common/analytics/` + `bajutsu/common/assertions/` | `analytics/ledger.py`, `analytics/stats.py`, `analytics/usage.py`, `assertions/evaluate.py`, `assertions/visual.py` |
| 5 | `bajutsu/common/backend_cli/` + `bajutsu/common/cloud/` | `backend_cli/adb.py`, `backend_cli/adb_resident.py`, `backend_cli/simctl.py`, `cloud/devicefarm.py` |
| 6 | `bajutsu/common/config/`まわり | `config/effective.py`, `config/schema.py`, `config_source.py`, `doctor.py` |
| 7 | `bajutsu/common/drivers/` | `actuation.py`, `adb.py`, `base.py`, `fake.py`, `playwright.py`, `webview.py`, `xcuitest.py`, `xcuitest_live.py`, `zorder.py` |
| 8 | `bajutsu/common/evidence/` + `bajutsu/common/handoff.py` | `evidence/core.py`, `evidence/golden.py`, `evidence/intervals.py`, `evidence/network.py`, `handoff.py` |
| 9 | `bajutsu/common/orchestrator/` + `bajutsu/common/platform_lifecycle/` | `orchestrator/loop.py`, `orchestrator/types.py`, `orchestrator/waits.py`, `platform_lifecycle/environments/android.py`, `platform_lifecycle/environments/xcuitest.py`, `platform_lifecycle/protocols.py` |
| 10 | `bajutsu/common/provisioning/` + `run_meta/` + `runner/` | `provisioning/provision.py`, `provisioning/requirements.py`, `run_meta/object_store.py`, `runner/device_provider.py`, `runner/recovery.py` |
| 11 | `bajutsu/common/scenario/` | `models/actions.py`, `models/assertions.py`, `models/evidence.py`, `models/mocks.py`, `models/scenario.py`, `models/steps.py`, `system_alerts.py` |
| 12 | `bajutsu/crawl/` | `core.py`, `guide.py`, `report.py`, `tabs.py` |
| 13 | `bajutsu/serve/`直下 | `artifacts.py`, `baselines.py`, `batch_provider.py`, `executor.py`, `logbus.py`, `oplog.py`, `provider_store.py`, `routes.py`, `scenarios.py`, `secrets.py`, `sessions.py`, `state.py`, `themes.py`, `uploads.py` |
| 14 | `bajutsu/serve/operations/` + `bajutsu/serve/server/` | `operations/coverage.py`, `server/db.py`, `server/executor.py`, `server/logbus.py`, `server/models.py`, `server/oauth.py`, `server/scenarios.py`, `server/sessions.py` |

各バッチのコミットは、対象ファイルが`.py`付きパスで`DOCSTRING_PATHS`や`docs/architecture.md`の表に
列挙されている場合、そのエントリも合わせて更新し、`DOCSTRING_PATHS`が対象とするファイル配下の
新規モジュールへ`D100`が要求するモジュールdocstringを書きます。バッチ7のコミットは、
`pyproject.toml`の`ignore_imports`エントリも更新します。`make coverage-floors`と最終的な
`make check`は、全バッチが終わったあとにそれぞれ1回だけ実行します。

## 検討した代替案

| 案 | 採らなかった理由 |
|---|---|
| このリポジトリが通常好む「1トピック1ブランチ」に従い、ディレクトリ単位で複数の小さいPRに分割します | ほぼ全パッケージに触れる1本の長命なPRは、実際の摩擦を生みます。同じファイルを編集する他ブランチとのリベースで競合します。`git`のリネーム検知も、1つのファイルが多数のファイルへ変わる変更は追えません。14本のPRに分けても、この摩擦は消えません。`bajutsu/common/drivers/`を編集する並行PRは、そのコミットが本PRにあっても独立PRにあっても、バッチ7のコミットと衝突します。14本のPRが増やすのは、互いに依存しないファイル群に対する14回のレビュー・マージの往復だけです。バッチごとのコミットを持つ1本のPRなら、この往復を1回に抑えつつ、レビュー側は1ディレクトリずつ差分を読め、バッチが終わるまでの間だけ開いたままになります |
| `actions.py`・`assertions.py`・`bajutsu/common/config/schema.py`のように、複数クラスを1つの概念の複数バリアントとして意図的に集約しているファイルは対象から外します。`assertions.py`(309行目)には、新しいバリアントは1箇所に追加すればよいという意図を明記したコメントがあります | これらは、動機の節が最悪のケースとして挙げたファイルです。`actions.py`だけで40クラス645行あります。ここを対象から外すと、この項目が取り除こうとしているファイルツリーとコンテキスト読み込みのコストを、もっとも重い箇所に残すことになります |
| クラス単位ではなく、関連するクラスをいくつかまとめた小グループ単位でファイルを分けます | ファイル名検索やファイルツリーのクリックが依存するゴールに届きません。クラスは依然としてグループとファイル名を共有します。また、1クラス1ファイルという明快な規則を、各グループの境界をどう引くかという別の判断に置き換えることになります。得られるものも小さく、1グループが2〜3クラスを持つままなら、446クラスという総数はわずかしか減りません |
| 各ファイルを手作業で分割します(人間であれAIエージェントであれ、1ファイルずつ編集する) | 85ファイル・446クラスは、先頭コメントを取りこぼす、クラス本体を誤って写す、パッケージ内importを書き忘れる、といった446回の機会です。スクリプトは規則1〜3と5を毎回同じ形で適用し、この種の誤りを持ち込みません。規則4の所有者判定と`D100`のdocstring執筆には人手の判断がバッチごとに残りますが、その量は446件からバッチごとの少数へ縮みます。AIエージェントが446クラスを1つずつ編集する場合、消費するトークン量もおおむねその件数に比例しますが、スクリプトは執筆コストを一度払うだけです |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `libcst`ベースの分割スクリプトとそのテストスイートを書きます。実際のファイルに対して
      実行する前に、スイートが通ることを確認します。
- [ ] バッチ1 — `bajutsu/analysis/`(5ファイル)を分割します。
- [ ] バッチ2 — 単発モジュール4件`cli/handoff.py`、`codegen/common.py`、`run/notify.py`、
      `triage/heuristic.py`を分割します。
- [ ] バッチ3 — `bajutsu/common/agents/`と`bajutsu/common/ai/`(5ファイル)を分割します。
- [ ] バッチ4 — `bajutsu/common/analytics/`と`bajutsu/common/assertions/`(5ファイル)を分割します。
- [ ] バッチ5 — `bajutsu/common/backend_cli/`と`bajutsu/common/cloud/`(4ファイル)を分割します。
- [ ] バッチ6 — `bajutsu/common/config/`、`config_source.py`、`doctor.py`(4ファイル)を分割します。
- [ ] バッチ7 — `bajutsu/common/drivers/`(9ファイル)を分割します。
- [ ] バッチ8 — `bajutsu/common/evidence/`と`bajutsu/common/handoff.py`(5ファイル)を分割します。
- [ ] バッチ9 — `bajutsu/common/orchestrator/`と`bajutsu/common/platform_lifecycle/`(6ファイル)を
      分割します。
- [ ] バッチ10 — `bajutsu/common/provisioning/`、`run_meta/`、`runner/`(5ファイル)を分割します。
- [ ] バッチ11 — `bajutsu/common/scenario/`(7ファイル)を分割します。
- [ ] バッチ12 — `bajutsu/crawl/`(4ファイル)を分割します。
- [ ] バッチ13 — `bajutsu/serve/`直下(14ファイル)を分割します。
- [ ] バッチ14 — `bajutsu/serve/operations/`と`bajutsu/serve/server/`(8ファイル)を分割します。
- [ ] `make coverage-floors`で`coverage-floors.json`を再生成します。差分がファイルパスの
      追加・削除だけであることを確認します。
- [ ] 全バッチが終わった状態で`make check`が通ることを確認します。

## 参考

- [BE-0385 — カバレッジ下限の継続的な引き上げ](../BE-0385-coverage-floor-continuous-ratchet/BE-0385-coverage-floor-continuous-ratchet-ja.md) — `coverage-floors.json`を定義しており、この項目の最終ステップがそのファイル単位の仕組みを再生成します。
- [BE-0065 — docstring規約とAPIリファレンス](../BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference-ja.md) — `DOCSTRING_PATHS`の移行を定義しており、この項目の各バッチはそれが実在するパスを指し続けるようにします。
- [`docs/ai-development.md`](../../docs/ai-development.md#roadmap-items-be-ids-strict) — この提案が従うロードマップ項目のフォーマットです。

[English](BE-0411-split-multi-class-modules.md) · **日本語**

# BE-0411 — bajutsuの複数クラス共存モジュールを1クラス1ファイルへ分割する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0411](BE-0411-split-multi-class-modules-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0411") |
| 実装 PR | [#1940](https://github.com/bajutsu-e2e/bajutsu/pull/1940)（分割本体）、[#1946](https://github.com/bajutsu-e2e/bajutsu/pull/1946)（テスト側の分割を見送り） |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

`bajutsu/` 配下には、1 つのモジュールに複数のトップレベルクラスを定義しているファイルが 86 個あります。合計すると 459 クラスです。この項目は、この 86 ファイルそれぞれをパッケージディレクトリへ分割します。各クラスは、そのディレクトリ配下の専用モジュールへ移動します。パッケージの `__init__.py` が公開シンボルをすべて再エクスポートするため、既存の import パスはすべてそのまま解決できます。対応するテストファイルがある場合は、テストも同じ方法で分割します。

この変更は物理的な再配置です。どのクラスも責務が増減せず、テストが新たに検証する内容もありません。この変更で新たに書く文章は、1 種類だけです。既存の lint ルールが求める、新規ファイルごとの 1 行のモジュール docstring です（詳細設計の「既存の 5 つの仕組み」を参照）。

## 動機

2 つのファイルが、この問題の規模を示しています。`bajutsu/common/scenario/models/actions.py` は、645 行に 40 クラスを詰め込んでいます。`bajutsu/common/drivers/base.py` は、1044 行に 23 クラスです。残り 84 ファイルの多くも、規模は小さいながら同じ形をしています。

1 つのクラスを開きたい読み手は、エディタのファイルツリーからそこへたどり着けません。ファイルツリーはファイル名で索引を作りますが、目的のクラスは、他の数十クラスとファイル名を共有しているからです。読み手は、ファイルを開いて中を検索するか、先に行番号を grep で調べることになります。

AI エージェントが 1 クラスだけを編集する場面でも、同じコストが別の形で生じます。目的のクラスにたどり着くには、結局ファイル全体を読み込む必要があります。1 クラスへの編集でも、無関係な残り 39 クラスがコンテキストに乗ります。ファイルは、クラスが追加されるたびに大きくなるので、このコストは積み上がります。`actions.py` に次のクラスが 1 つ増えるたびに、そのファイルは、追加した本人だけでなく、以後すべての読み手と編集者にとって大きくなります。

1 クラス 1 ファイルへの分割は、この両方のコストを一度に取り除きます。ファイル名での検索やファイルツリーのクリックが、クラスへ直接届くようになります。1 クラスへの編集は、そのクラスのファイルだけを読み込めば済みます。

この効果が実際に得られたかどうかは、ほかに何も読まずに確認できます。`LongPress` は、今日時点では `actions.py` のどこかにあります。分割後は、ファイルツリーから `actions/long_press.py` へ直接たどり着けます。途中の検索は要りません。

## 詳細設計

### 分割規則

トップレベルクラスを 2 つ以上持つ 86 ファイルそれぞれを、同名のパッケージディレクトリに置き換えます。規則は 6 つです。

1. **1 クラス 1 ファイル。** `foo.py` は `foo/` になります。パッケージの `__init__.py` は、元の `foo.py` のモジュール docstring を引き継ぎます。続けて、全公開クラスを元の宣言順に import して再エクスポートし、再エクスポートするすべての名前を `__all__` へ書き出します。元のファイルに `__all__` があったかどうかに関わらず、`__all__` を新たに書き出します。`pyproject.toml` の ruff の `select` には `F` が含まれ、`__init__.py` 向けの per-file-ignore はありません。再エクスポートするためだけの import は、`__all__` がなければ `F401` となり、`make lint` を失敗させるためです。

   各クラスは、`snake_case(クラス名).py` へ移動します。`_Model` のような先頭アンダースコア付きの非公開クラスは `_model.py` となり、ファイル名にもアンダースコアを残します。この規則は、Pydantic モデル、`TypedDict`、`Protocol`、`Exception`、素の `dataclass` のいずれであっても、同じ形で適用します。
2. **トップレベル関数は 1 ファイルにまとめて残します。** この項目の対象はクラスであって、関数ではありません。1 つのファイルが持つトップレベル関数は、新しいパッケージ内の `_functions.py` へまとめて移動します。`__init__.py` への分散や、関数 1 つごとのファイル分割はしません。

   86 ファイル中 62 ファイルは、クラスと並んでトップレベル関数を 1 つ以上定義しています。`bajutsu/common/backend_cli/adb.py` だけで 66 個です。この規則がなければ、これらの関数は結局 `__init__.py` へ戻り、もっともコード量の多いファイル群で削減効果を打ち消してしまいます。`__init__.py` は、公開関数を、クラスと同じ方法で `_functions.py` から再エクスポートします。
3. **パッケージ内の明示的な import。** あるクラスが、同じ元ファイル内の別クラスを継承または参照している場合があります。分割後は、そのクラスを相対 import で明示的に読み込みます（`from ._model import _Model`）。2 つのクラスは、もはやモジュールスコープを共有していないためです。
4. **所有者を持たないモジュールレベルのコードは `__init__.py` へ、単一の所有者を持つコードはその所有者のファイルへ。** 複数の分割後クラスから実際に使われるコードは、ほかに行き場がありません。一例は、[`serve/server/models.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/bajutsu/serve/server/models.py) の `_JSON` です。SQLAlchemy の列型バリアントで、6 つの異なる O/R マッパーのモデルクラスが `mapped_column` へ渡します。

   1 つのクラスや関数だけに紐づくコードは、モジュールレベルにあっても、その所有者自身のファイルに留めます。移動すると、参照が壊れます。あるいは、`global` でその値を再束縛する関数へ移動した場合は、エラーを出さないまま値の更新が止まります。`global` は、別モジュールの名前には届かないためです。

   [`assertions.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/bajutsu/common/scenario/models/assertions.py) の `_ASSERTION_KINDS` が、前者の例です。`Assertion.model_fields` から導出され、読むのは `Assertion._one_kind` だけなので、`Assertion` と一緒に留まります。`drivers/playwright.py` の `_PW_ERRORS` が、後者の例です。`_playwright_error_types` が、オプショナルな import をメモ化するために `global` で再束縛しています。そのメモは、規則 2 でその関数が行き着く先、つまり `_functions.py` に留まります。
5. **新たな循環 import は既存の遅延パターンで対処します。** `bajutsu/common/config/schema.py` の `Config` モデルは、メソッド内で遅延 import します。対象は `bajutsu.common.config.resolve` です。循環を避けるためです。分割で新たに生じる循環 import も、同じ方法で対処します。
6. **エントリポイントのガードは `__main__.py` へ移します。** `python -m <パッケージ>` が実行するのは `__main__.py` であって、`__init__.py` ではありません。`if __name__ == "__main__":` ブロックをパッケージ本体に残したままにすると、何の例外も上がらないまま、エントリポイントが失われます。ガードを持つのは、86 ファイルのうち `bajutsu/common/provisioning/provision.py` だけです。`scripts/install.sh` と web-e2e ワークフローは、このファイルを `python -m` の形で呼び出しています。

### 実現方式: 構文解析を行うスクリプトであり、手作業の編集ではない

規則 1〜3 と 5〜6 は、バッチごとに 1 回実行する Python スクリプトが機械的に適用します。人間であれ AI エージェントであれ、エディタで 1 ファイルずつ分割するのではありません。

AI エージェントが 459 クラスを 1 つずつ読んで書き換えると、消費するトークン量はおおむねその件数に比例します。スクリプトは、一度だけ執筆コストを払えば、以降はバッチを何回実行してもトークンを消費しません。

スクリプトは、標準ライブラリの `ast` ではなく [`libcst`](https://github.com/Instagram/LibCST) で、各対象ファイルを解析します。`ast` はコメントを保持しないためです。これらのファイルの多くのクラスは、設計判断を説明する先頭のコメントを持っており、分割でそれを失うわけにはいきません。

スクリプトは、次の 4 つの手順で動きます。

1. トップレベルの `class` または `def` ノードを、その先頭のコメントブロックごと抽出します。
2. 抽出したノードを、バイト単位で、規則 1 または規則 2 が指定するファイルへ書き出します。
3. 抽出した各クラスの本体を走査して、兄弟のトップレベル名への参照を検出し、規則 3 が必要とする相対 import を生成します。
4. 同じ解析結果から `__init__.py` を書き出します。元のモジュール docstring、クラスや関数ごとの import 1 行、`__all__` のリストを含めます。

`libcst` は、このスクリプト専用の開発時依存であり、実行時の依存にはなりません。

規則 4 の 2 択、つまりモジュールレベルのコードに単一の所有者がいるかいないかは、スクリプトでは判断しません。この判断には、`_ASSERTION_KINDS` と `_PW_ERRORS` に対してこの設計が下したのと同じ判断力が必要だからです。この判断と、新規モジュールごとの `D100` の docstring の執筆が、各バッチで手作業のまま残る 2 つの作業です。上記の規則と例に沿って進めます。

スクリプトへの信頼は、このコードベースの他の部分と同じ方法で獲得します。専用のテストスイート（`tests/test_split_modules.py` など）を用意し、上記の各規則を小さな fixture モジュールに対して検証します。

- 先頭コメント付きのクラス
- 兄弟クラスを参照するクラス
- `__all__` があるファイルとないファイル
- モジュールレベルの名前を `global` で再束縛する関数

このスイートが通ってから、スクリプトを実際のファイルに対して実行します。実際のファイルへバッチごとに適用していく作業は、以下の「進捗」のバッチ項目が追跡します。

### テスト側の分割

対応するソースファイルを持つテストファイルの例を挙げます。`bajutsu/common/scenario/models/actions.py` に対する `tests/scenario/test_models_actions.py` です。このテストファイルは、ソース側と同じ方法で、クラスごとに 1 テストモジュールへ分割します。分割後のテストディレクトリには、空の `__init__.py` を置きます。

空の `__init__.py` は、ある制約を避けるために置きます。`pyproject.toml` の `[tool.pytest.ini_options]` は、`--import-mode` を指定していません。デフォルトの `prepend` モードでは、テストモジュールのファイル名がテストツリー全体で一意でなければなりません。空の `__init__.py` は、分割後のディレクトリをパッケージ化します。

86 ファイルの中には、重複するクラス名が 8 組あります。たとえば `DeviceError` と `Env` は、`backend_cli/adb.py` と `backend_cli/simctl.py` の両方でクラス名です。パッケージ化していなければ、この 2 ファイルを分割したテストは、同じ `test_device_error.py` という名前で衝突します。

結合テストだけでカバーされ、専用のユニットテストファイルを持たないソースファイルには、新規のテストファイルを追加しません。この項目の範囲は、既存のものを分割することです。存在しないテストの網羅を、新たに追加することではありません。

モジュールパスでパッチを当てているテストは、対応するソースファイルと同じコミットで、参照先を書き換えます。`tests/` は、`monkeypatch.setattr("bajutsu.<module>.<name>", ...)` という形の呼び出しを 100 箇所ほど持っています。`__init__.py` が再エクスポートしているだけの名前にパッチを当てると、呼び出し元が実際に参照する束縛ではなく、再エクスポート側が差し替わります。この不具合は、気づかずに通るケースとエラーになるケースの両方があります。

気づかずに通るケースは、次のとおりです。

- `bajutsu.run.notify._RETRY_DELAY`（4 箇所、バッチ 2）。`_deliver` は、規則 2 により `_functions.py` へ移動し、そこで別の束縛を持ちます。パッチは効かなくなり、リトライのテストは、パッチされた値の代わりに本物のスリープを実行します。
- `drivers.playwright._PW_ERRORS` と `drivers.xcuitest.XcuitestDriver`（バッチ 7）。同じ形で、気づかずに通ります。

エラーになるケースは、次のとおりです。

- `drivers.base.time.sleep` と `.time.monotonic`（10 箇所、バッチ 7）。`base/__init__.py` が `time` を import しなくなった時点で `AttributeError` となり、その場で気づきます。

### 既存の5つの仕組みが今日のファイルパスを前提にしている箇所

**カバレッジの下限。** `coverage-floors.json` は、ファイルパスをキーに、1 ファイルあたりのカバレッジ下限を記録しています（[BE-0385](../BE-0385-coverage-floor-continuous-ratchet/BE-0385-coverage-floor-continuous-ratchet-ja.md)）。分割で生まれた新しいパスには、分割した時点ではまだ下限の記録がありません。`scripts/coverage_floors.py` は、この状態をエラーではなく情報として扱います。そのため、分割が進行中の間は害がありません。`make coverage-floors` を最後のバッチが終わったあとに一度実行すれば、新しいパスを含むスナップショット全体を再生成できます。

**docstring の lint。** `Makefile` の `DOCSTRING_PATHS` 変数は、`lint-docstrings` が対象とするモジュールを列挙しています。BE-0065 の Google スタイル docstring 移行のもとでの対象です。今日の 86 ファイルのうち 26 ファイルは、`.py` 付きの正確なパスでこの変数に列挙されています。`bajutsu/common/drivers/base.py` や `bajutsu/analysis/audit.py` が、その例です。
残り 15 ファイルは、ディレクトリ単位のエントリで、すでにカバーされています。

正確なパスで列挙されているファイルを分割して、エントリを更新しなければ、`lint-docstrings` はそのファイルの検査をやめます。しかも、失敗としては現れません。`ruff` は、存在しないパスを渡されても終了コード 0 を返すためです。取り残されたエントリは、その範囲の docstring 検査を黙って落とします。そこで実装では、`lint-docstrings` のレシピにパス存在チェックを足し、黙って落ちる形を失敗に変えます。

ディレクトリ単位のエントリは、変更が要りません。`bajutsu/common/scenario` は `models/actions.py` とその兄弟ファイルをすでにカバーしており、`ruff` がディレクトリ全体を自ら走査するためです。

ただし、ディレクトリ単位のエントリには、ファイル単位のエントリにはない帰結があります。`lint-docstrings` は、`D` ファミリー全体から `D102`/`D105`/`D107` を除いて選択しており、`D100`（公開モジュールに docstring がない）は除外されていません。`D100` は、41 の対象ファイルのいずれかの下で分割が生み出すすべてのモジュールに適用されます。ファイル単位から改名した 26 ファイルと、すでにディレクトリ単位でカバーされている 15 ファイルの両方が対象です。

`bajutsu/common/scenario` は、すでに `models/actions.py` をカバーしています。そのため、40 個の新しいクラス単位モジュールそれぞれに、専用のモジュール docstring が必要で、単純な引き継ぎでは済みません。41 の対象ファイル全体では、275 個の新規モジュール docstring を執筆することになります。これは機械的な引き継ぎではなく、実際に書く文章です。はじめにの枠組みだけでは捉えきれないコストです。`bajutsu/common/scenario` と `bajutsu/common/drivers/`（バッチ 11 と 7）が、このコストの大半を占めます。`actions.py` と `base.py` は、もともとクラス数がもっとも多い 2 ファイルだからです。

**モジュール一覧の表。** [`docs/architecture.md`](../../docs/architecture.md) のモジュール一覧の表も、86 ファイルのうち 15 件を、`.py` 付きの正確なパスで列挙しています。

- `bajutsu/common/drivers/` 配下の全ファイル
- `config_source.py`、`doctor.py`、`handoff.py`
- `provisioning/provision.py`、`provisioning/requirements.py`
- `run/notify.py`

`scripts/lint_module_map.py` は、`make lint-module-map`（`make check` の一部）として実行されます。表のエントリがツリー上に存在しないパスを指していると失敗します。そのため、これらのファイルを分割して表のエントリを書き換えなければ、ゲートが壊れます。

同じチェックは、`bajutsu/common/` 配下も 1 階層だけ走査し、文書化されていないサブパッケージがないか調べます。`config_source.py`、`doctor.py`、`handoff.py` は、`common/` の直下にあります。これらのいずれかを分割すると、その新しいパッケージディレクトリは、文書化されていないサブパッケージになります。表のエントリを、同じコミットで新しいディレクトリパスへ更新すれば、この事態を避けられます。表のパス欄を書き換えれば、両方のチェックを一度に満たせます。

**実機レーンの選択。** [`scripts/e2e_changes.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/scripts/e2e_changes.py) は、ある変更がどの実機 CI レーンを起動するかを決めます。このスクリプトは、約 30 のモジュールを `.py` 付きの正確なパスで名指ししています。レーンごとのパターン、共有の実行経路フィルタ、周辺部の除外リストの、いずれにも登場します。

そのうちの 1 つを分割して、エントリを更新しないと、スクリプト自身のコメントが [BE-0252](../BE-0252-config-package-split/BE-0252-config-package-split-ja.md) の事例として記録している失敗が再現します。新しいパッケージ配下のファイルは、どれもマッチしなくなります。レーンの `changes` ジョブは関連なしと報告し、必須の集約ジョブは、何も実行しないまま緑になります。スクリプト自身のテストは、取り残されたプレーンなパスは検出しますが、正規表現は検出しません。そのため、各バッチで両方を確認します。

**import-linter の contract。** `pyproject.toml` の `[tool.importlinter]` には、3 つの contract があります。いずれも、`bajutsu.common.drivers.base` を `source_modules` または `forbidden_modules` として名指ししています。この分割が触れる残り 2 つのモジュール、`bajutsu.common.drivers.actuation` と `bajutsu.common.doctor` も、そこに加わっています。これらの contract は、決定論的なコア部分が周辺部から独立していることを守っています。

これらのエントリは、変更が要りません。import-linter は、名前を挙げたモジュールを、それ自身とすべての子孫として解決します。プレーンなモジュールがパッケージになっても、この扱いによってカバーされたままです。`pyproject.toml` にある artifact sink に関する contract 自身のコメントが、すでに前提にしているのと同じサブツリーの意味論です。

1 つだけ、サブツリーではなく厳密なエッジを名指ししているエントリがあり、それは壊れます。scenario schema と `Driver` Protocol をポータブルな内側の層として保つ contract で、`ignore_imports` エントリが覆うエッジは 1 つです。そのエッジは `bajutsu.common.drivers.base -> bajutsu.common.evidence.network` です。これは `TYPE_CHECKING` で守られた `Collector` への参照で、`network_collector` シグネチャのために持ちます。このシグネチャを持つのは、`Driver` ではなく `EvidenceProvider` です。

バッチ 7 は、この Protocol に専用のファイルを与えます。実際のエッジは、そのとき `bajutsu.common.drivers.base.evidence_provider -> bajutsu.common.evidence.network` になります。記録されている文字列は、もう一致しません。`unmatched_ignore_imports_alerting` はデフォルトで `error` なので、`make lint-imports` は、この古いエントリで失敗します。バッチ 7 のコミットで、新しいモジュールパスへ更新します。

### 作業の分解

86 ファイルは、既存のディレクトリ境界に沿って 14 バッチにまとまります。各バッチは、1 本の PR 上の 1 コミットです。

| # | バッチ | ファイル |
|---|---|---|
| 1 | `bajutsu/analysis/` | `audit.py`, `coverage.py`, `flakiness.py`, `impact.py`, `stats.py` |
| 2 | 単発モジュール5件 | `bajutsu/cli/handoff.py`, `bajutsu/codegen/common.py`, `bajutsu/run/notify.py`, `bajutsu/triage/heuristic.py`, `bajutsu/common/devices/errors.py` |
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

各バッチのコミットは、2 つのことを行います。

- 対象ファイルが `.py` 付きパスで `DOCSTRING_PATHS` や `docs/architecture.md` の表に列挙されている場合、そのエントリも合わせて更新します。
- `DOCSTRING_PATHS` が対象とするファイル配下の新規モジュールへ、`D100` が要求するモジュール docstring を書きます。

バッチ 7 のコミットは、`pyproject.toml` の `ignore_imports` エントリも更新します。`make coverage-floors` と最終的な `make check` は、全バッチが終わったあとに、それぞれ 1 回だけ実行します。

## 検討した代替案

| 案 | 採らなかった理由 |
|---|---|
| このリポジトリが通常好む「1トピック1ブランチ」に従い、ディレクトリ単位で複数の小さいPRに分割します | ほぼ全パッケージに触れる1本の長命なPRは、実際の摩擦を生みます。同じファイルを編集する他ブランチとのリベースで競合します。`git`のリネーム検知も、1つのファイルが多数のファイルへ変わる変更は追えません。14本のPRに分けても、この摩擦は消えません。`bajutsu/common/drivers/`を編集する並行PRは、そのコミットが本PRにあっても独立PRにあっても、バッチ7のコミットと衝突します。14本のPRが増やすのは、互いに依存しないファイル群に対する14回のレビュー・マージの往復だけです。バッチごとのコミットを持つ1本のPRなら、この往復を1回に抑えつつ、レビュー側は1ディレクトリずつ差分を読め、バッチが終わるまでの間だけ開いたままになります |
| `actions.py`・`assertions.py`・`bajutsu/common/config/schema.py`のように、複数クラスを1つの概念の複数バリアントとして意図的に集約しているファイルは対象から外します。`assertions.py`(309行目)には、新しいバリアントは1箇所に追加すればよいという意図を明記したコメントがあります | これらは、動機の節が最悪のケースとして挙げたファイルです。`actions.py`だけで40クラス645行あります。ここを対象から外すと、この項目が取り除こうとしているファイルツリーとコンテキスト読み込みのコストを、もっとも重い箇所に残すことになります |
| クラス単位ではなく、関連するクラスをいくつかまとめた小グループ単位でファイルを分けます | ファイル名検索やファイルツリーのクリックが依存するゴールに届きません。クラスは依然としてグループとファイル名を共有します。また、1クラス1ファイルという明快な規則を、各グループの境界をどう引くかという別の判断に置き換えることになります。得られるものも小さく、1グループが2〜3クラスを持つままなら、459クラスという総数はわずかしか減りません |
| 各ファイルを手作業で分割します(人間であれAIエージェントであれ、1ファイルずつ編集する) | 86ファイル・459クラスは、先頭コメントを取りこぼす、クラス本体を誤って写す、パッケージ内importを書き忘れる、といった459回の機会です。スクリプトは規則1〜3と5〜6を毎回同じ形で適用し、この種の誤りを持ち込みません。規則4の所有者判定と`D100`のdocstring執筆には人手の判断がバッチごとに残りますが、その量は459件からバッチごとの少数へ縮みます。AIエージェントが459クラスを1つずつ編集する場合、消費するトークン量もおおむねその件数に比例しますが、スクリプトは執筆コストを一度払うだけです |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `libcst`ベースの分割スクリプトとそのテストスイートを書きます。実際のファイルに対して
      実行する前に、スイートが通ることを確認します。
- [x] バッチ1 — `bajutsu/analysis/`(5ファイル)を分割します。
- [x] バッチ2 — 単発モジュール5件`cli/handoff.py`、`codegen/common.py`、`run/notify.py`、
      `triage/heuristic.py`、`common/devices/errors.py`を分割します。
- [x] バッチ3 — `bajutsu/common/agents/`と`bajutsu/common/ai/`(5ファイル)を分割します。
- [x] バッチ4 — `bajutsu/common/analytics/`と`bajutsu/common/assertions/`(5ファイル)を分割します。
- [x] バッチ5 — `bajutsu/common/backend_cli/`と`bajutsu/common/cloud/`(4ファイル)を分割します。
- [x] バッチ6 — `bajutsu/common/config/`、`config_source.py`、`doctor.py`(4ファイル)を分割します。
- [x] バッチ7 — `bajutsu/common/drivers/`(9ファイル)を分割します。
- [x] バッチ8 — `bajutsu/common/evidence/`と`bajutsu/common/handoff.py`(5ファイル)を分割します。
- [x] バッチ9 — `bajutsu/common/orchestrator/`と`bajutsu/common/platform_lifecycle/`(6ファイル)を
      分割します。
- [x] バッチ10 — `bajutsu/common/provisioning/`、`run_meta/`、`runner/`(5ファイル)を分割します。
- [x] バッチ11 — `bajutsu/common/scenario/`(7ファイル)を分割します。
- [x] バッチ12 — `bajutsu/crawl/`(4ファイル)を分割します。
- [x] バッチ13 — `bajutsu/serve/`直下(14ファイル)を分割します。
- [x] バッチ14 — `bajutsu/serve/operations/`と`bajutsu/serve/server/`(8ファイル)を分割します。
- [x] `make coverage-floors`で`coverage-floors.json`を再生成します。差分がファイルパスの
      追加・削除だけであることを確認します。
- [x] 全バッチが終わった状態で`make check`が通ることを確認します。
- [x] ~~分割したソースファイルに対応するテストファイルを、1テストモジュール1クラスの形へ分割します。~~
  - 分割を終えた状態を計測したうえで、見送りました。分割済みパッケージと名前で対応する
    テストファイルは57本あります。そこに含まれる1,771件のケースのうち、対象パッケージの
    クラスをまったく名指ししないものが61パーセントを占めます。ちょうど1つだけ名指しする
    ものは30パーセント、2つ以上にまたがるものは10パーセントです。対象パッケージの
    327クラスのうち、対応するテストファイルのケースに名前が現れるのは96クラスにとどまります。
    57本のうち41本では、単一のクラスに定まるケースが半数に届きません。1クラス1モジュールへ
    割ると、10件中6件のケースには置き先のクラスがなく、10クラス中7クラスには空のテスト
    モジュールが残ります。原因は、規則2がソース側ですでに名指ししているものと同じです。
    これらのテストは、モジュールの関数を軸にケースを束ねています。分割は、その関数を
    `_functions.py`にまとめて残す設計です。公開の入口を通した振る舞いを軸に束ねている
    ケースもあります。1クラス1モジュールへ割る操作は、ソースを写し取る操作になりません。
    ケースが従っていない境界でスイートを切り直す操作になります。
  - 本項目が取り除こうとした、辿りにくさの問題はテスト側にも実在します。
    `tests/test_adb.py`は2,730行、`tests/test_xcuitest.py`は2,395行あります。ただし、
    そこを割る境界はクラスではなく主題です。境界を引くには、それ自体の設計が要ります。
    各主題がどこで終わるか、結果をサイズの上限で縛るか、pytestの`prepend`インポート
    モードが要求するファイル名の一意性を新しいモジュールでどう保つかを決める必要があります。
    本項目の単位ではなく独立したロードマップ項目の主題であり、該当する項目はまだありません。

ログ：

- [#1940](https://github.com/bajutsu-e2e/bajutsu/pull/1940) — 全バッチを一度に実施しました。まず`scripts/split_modules.py`と
  そのテストスイートを入れ、続けて14バッチを順に適用しました。その結果、`bajutsu/`配下に
  トップレベルクラスを2つ以上持つモジュールはなくなりました。既存のテストスイートは全過程を通じて
  無改変のまま通っており、この再配置が振る舞いを一切運んでいないことを、その事実が示しています。

  スクリプトを実際のコードに当てた結果、8件の欠陥が見つかり、それぞれ回帰テストを付けて、詰まった
  バッチの前に直しました。3巡の自己レビューで見つかった11件は、この後にまとめます。
  タプル展開の代入先が見えておらず、束縛される名前を誰もimportしませんでした。
  Python Enhancement Proposal 695（PEP 695）の型パラメータの境界を読めていませんでした。
  アノテーション外のクォートされた前方参照も同様です。
  クラス本体のフィールドが兄弟クラスと同名のとき、その参照と誤読していました。内側の`import`が
  束縛する名前を読み取りと数えていました。所有者そのものから導出されるモジュールレベルのコードを、
  所有者より前に出力していました。参照の集約が出力先ファイル単位ではなく宣言単位だったため、
  1モジュールの関数のうち最後の1つしか所有者として数えられませんでした。

  実際のコードに当てて形が変わった規則を2つ挙げます。アノテーションだけで参照する兄弟を
  `TYPE_CHECKING`へ送るとPydanticが壊れます。Pydanticは実行時にアノテーションを解決するためです。
  そこで兄弟のimportはデフォルトで実行時importとし、循環がある場合に限って遅延させます。もう1つは規則4の
  「単独の所有者がいない」場合で、2つに分かれました。何も束縛しないコードは`__init__.py`の末尾で
  実行します。この項目の設計が置いた場所そのものです。名前を束縛するコードは`_shared.py`へ回します。
  `__init__.py`は、その名前を読み返すモジュール自身を再エクスポートするためです。

  規則4は、モジュールレベル文どうしの所有関係も数えるようになりました。`oplog.py`の`_CONTEXT_KEYS`と
  その隣のコンテキスト変数が別々のファイルに分かれ、モジュール読み込み時に互いをimportしたためです。
  新たに生じた循環15件には、規則4ではなく規則5の遅延importを、それぞれの単一のエッジに手で適用しました。

  テストスイート全体で42箇所の`monkeypatch`の対象を、束縛を持つモジュールへ差し替えました。多くは
  この項目の設計が予告した「静かに壊れる」種類でした。リトライのテストは本当にsleepするところでした。
  キャッシュヒット時に呼ばれないことを検証するテストは、空振りのまま通るところでした。拒否を検証する
  テストが下げるアップロード上限も、すべて同じ形でした。1件は正しさ以上の代償を伴っていました。
  起動タイムアウトへのパッチが届かなくなり、`test_spawn_cold_discards_a_never_ready_runner`が
  ゲートを回すたび実際の120秒を待っていました。現在は0.24秒です。

  完成した差分に対する3巡の自己レビューで、スクリプトにさらに11件の欠陥が見つかりました。いずれも同じ形で、
  もっともらしいが誤ったパッケージを書いたうえで成功を報告していました。解けない循環importは、注記だけ
  添えてディスクへ書かれていました。`Literal["Beta"]`の文字列を前方参照として読んでいました。メソッドが
  自前でimportしている兄弟が、モジュールレベルのimportとして戻り、人手で解いた規則5の循環を復活させて
  いました。`global`で再束縛される名前を値として再エクスポートしていたため、パッケージ属性がimport時に
  凍りつき、実際の束縛だけが更新されていました。使用量台帳のテストのアサーションが無条件に成立していた
  のは、これが原因です。メソッド内のローカルimportによる抑制がファイル全体に効いており、隣のモジュール
  レベル文が必要としていたヘッダimportを落としていました。添字式の全体をアノテーション文脈として読んで
  いたため、その中の実行時参照が`TYPE_CHECKING`へ送られ、使う場所で未定義になりえました。
  現在はいずれも拒否または修正となり、整形処理・削除処理・バッチループのどれも、
  終了コード0ではなく失敗を報告します。テストは41件から94件に増え、スクリプトの分岐カバレッジは92%です。
  代表的な3つのプランを実際の
  パッケージとして書き出し、importしてlintにかけます。他のすべてのアサーションが代理していた、
  スクリプト本来の契約そのものです。

  `make check`は緑で、全体のカバレッジは94.71％です。設計のうち唯一残したのがテスト側の分割です。
  対象のテストファイルはケースをソースのクラス単位ではなく関数単位でまとめており、規則2はすでに
  1モジュールの関数を1ファイルに保ちます。クラス単位で分割すると、ソースを写すのではなく
  テストスイートを別の切り口で組み替えることになります。

  12件目の欠陥はCIでのみ現れました。`.gitignore`には`uploads/`というパターンがあります。
  `serve`が実行時に展開するバンドルのディレクトリを除外するためのもので、位置を固定していません。
  `bajutsu/serve/uploads.py`がディレクトリになった瞬間、生成した6ファイルはこのパターンに入ります。
  `git add`は何も言わずにそれらを飛ばしました。作業ツリーにはパッケージがあるため、ローカルの
  検査はすべて通ります。一方でCIは、モジュールが削除されたまま何も置かれていないブランチを
  クローンしました。現在はこのソースのパスだけをパターンから除外し直しています。スクリプトの側でも、
  Gitがすでに無視するパッケージのパスは、何かを書き出す前に拒否します。モジュール名と除外パターンの
  衝突は、作業ツリーを読むどの検査からも見えません。ディレクトリを作る道具自身が拒否するほかない
  のは、そのためです。

- [#1946](https://github.com/bajutsu-e2e/bajutsu/pull/1946) — 最後まで残っていた単位を閉じました。
  テスト側の分割は、本項目のもとでコードを出しません。分割を終えた状態を計測したところ、設計が残していた問いに答えが
  出ました。分割済みパッケージと名前で対応する57本のテストファイルには、1,771件のケースが
  あります。そのうち61パーセントは、対象パッケージのクラスをまったく名指ししません。
  327クラスのうち、対応するテストファイルのケースに名前が現れるのは96クラスだけです。
  スイートは、モジュールの関数と、公開の入口を通した振る舞いを軸にケースを束ねています。
  関数を軸にした束ね方は、規則2がソース側で保つ束ね方そのものであり、分割が乱したものでは
  ありません。1クラス1モジュールへ割れば、10件中6件のケースには所有者を無理に割り当てる
  ことになります。
  10クラス中7クラスには空のモジュールが残ります。ほかの単位はすべて終わっているため、本項目は
  実装済みになります。

## 参考

- [BE-0385 — カバレッジ下限の継続的な引き上げ](../BE-0385-coverage-floor-continuous-ratchet/BE-0385-coverage-floor-continuous-ratchet-ja.md) — `coverage-floors.json`を定義しており、この項目の最終ステップがそのファイル単位の仕組みを再生成します。
- [BE-0065 — docstring規約とAPIリファレンス](../BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference-ja.md) — `DOCSTRING_PATHS`の移行を定義しており、この項目の各バッチはそれが実在するパスを指し続けるようにします。
- [`docs/ai-development.md`](../../docs/ai-development.md#roadmap-items-be-ids-strict) — この提案が従うロードマップ項目のフォーマットです。

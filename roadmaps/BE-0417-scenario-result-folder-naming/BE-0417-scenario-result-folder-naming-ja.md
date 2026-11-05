[English](BE-0417-scenario-result-folder-naming.md) · **日本語**

# BE-0417 — シナリオの結果ディレクトリ名を、読み込み元のシナリオファイルにちなんだ名前にする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0417](BE-0417-scenario-result-folder-naming-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0417") |
| 実装 PR | [#1977](https://github.com/bajutsu-e2e/bajutsu/pull/1977) |
| トピック | コードベース品質・技術的負債 |
| 関連 | [BE-0200](../BE-0200-run-id-contract/BE-0200-run-id-contract-ja.md) |
<!-- /BE-METADATA -->

## はじめに

run に含まれる各[シナリオ](../../docs/ja/glossary.md#シナリオのオーサリング)は、`runs/<runId>/` の中の専用の `sid` ディレクトリに証拠を置きます。
現在の `sid` は `f"{i:02d}-{scenario_slug(s.name)}"` という形式です。実行順のインデックスと、
シナリオ内部の `name:` フィールドをスラグ化した文字列を組み合わせています
（[`bajutsu/common/runner/pipeline.py:269`](../../bajutsu/common/runner/pipeline.py)）。

本項目は、このスラグ部分の由来を変えます。スラグは `name:` フィールドではなく、シナリオを実際に
読み込んだファイルの stem から作ります。stem とは、`.yaml` 拡張子を除いたファイル名です。たとえば
`sid` は `00-login-succeeds-with-a-valid-password` ではなく `00-login_flow` になります。
連番プレフィックス `{i:02d}-` は変更しません。

### やらないこと

- **トップレベルの run ディレクトリは変更しません。** `runs/<runId>/` は UTC タイムスタンプです。
  辞書順が時系列順に一致するという契約（[BE-0200](../BE-0200-run-id-contract/BE-0200-run-id-contract-ja.md)）に、
  次の3つが依存しています。
  - `bajutsu/analysis/trace.py` の `latest_run()`
  - `serve` の run 一覧と crawl 一覧（`bajutsu/serve/helpers.py`）
  - `run/notify` の直前 run 照合

  本項目は、この3つのいずれにも触れません。run ディレクトリ自体をシナリオファイルにちなんで改名する
  案には、複数シナリオや複数ファイルを実行する run に対する一貫した答えがありません。1つの
  `runs/<runId>/` は、その run が実行したすべてのシナリオについて `manifest.json`、`junit.xml`、
  `ctrf.json`、`report.html` を保持します。1シナリオ専用の場所ではないからです。
- **重複を避けるための新しいサフィックスカウンタは導入しません。** 既存の `{i:02d}-` インデックス
  プレフィックスが、同一 run 内での `sid` の一意性をすでに保証しています。由来ファイルから同じ名前が
  導かれるシナリオが2つあっても、衝突しません。stem が同じ複数ファイルの場合も、1つのファイルの
  `scenarios:` に複数のシナリオがある場合も、同じです。詳細は「詳細設計」と
  「検討した代替案」に書きます。
- **由来ファイルがわからない `Scenario` は、これまでどおりの挙動を保ちます。** ファイルローダーを
  経由せず、メモリ上で直接組み立てるシナリオがあります。そのシナリオには、
  `scenario_slug(s.name)` によるフォールバックを引き続き使います。テストのフィクスチャが一例です。本項目の影響は受けません。
- **クロスブラウザマトリックスの `<engine>/<sid>/` という階層は変更しません。** `--browsers` が作る
  階層はそのままです。変わるのは `sid` 自体の組み立て方だけです。
- **`_matrix()` にあった、シナリオ名をキーにした衝突は扱いません。**
  `bajutsu/common/report/manifest.py` は `--browsers` のマトリックス集計をシナリオ名でキー化して
  いました。そのため、同一エンジン内に同名のシナリオが複数あると、片方のマトリックスセルをもう片方が
  上書きしていました。これは本項目とは別の課題であり、本項目は触れていません。
  [#1970](https://github.com/bajutsu-e2e/bajutsu/pull/1970) が、本項目の実装に着手する前に独立して
  修正しました。`_matrix()` は現在、同名のシナリオを `(N)` サフィックスで区別します。この衝突は
  もう存在しません。

## 動機

現在、`sid` のスラグ部分は、シナリオを読み込んだファイルではなく、シナリオ内部の `name:`
フィールドに由来します。`name:` は自由記述であり、ファイル名と似ているとは限りません。たとえば
`login_flow.yaml` に書かれたシナリオが `name: "Login succeeds with a valid password"` を持つと
します。この名前は `00-login-succeeds-with-a-valid-password` にスラグ化されます。

特定のシナリオの結果を `runs/<runId>/` から探すには、操作者は `manifest.json` を開く必要があります。
そのうえで、`scenario` フィールドを、念頭に置いているファイルと照合します。`login_flow.yaml` から
証拠ディレクトリへ直接たどり着く方法はありません。

この不便さが本項目の動機です。複数のシナリオ結果の中から目的のシナリオの結果だけを探す作業には、
必要以上に手間がかかります。特に手間がかかるのは、シナリオを書きながら、あるいは 継続的インテグレーション（CI）の失敗を
追いながら、同じシナリオファイルを繰り返し実行する場面です。

`sid` のスラグをシナリオ自身のファイルに由来させれば、この遠回りがなくなります。操作者は、すでに
知っているファイル名から証拠ディレクトリへ直接たどり着けます。`manifest.json` を開く必要はありません。

## 詳細設計

1. **`Scenario.source_stem`（新規）。**
   [`Scenario`](../../bajutsu/common/scenario/models/scenario/scenario.py) に、読み込み時にだけ
   設定されるプライベート属性を追加します（`_source_stem: str | None = PrivateAttr(default=None)`）。
   この属性を公開する、読み取り専用の `source_stem` プロパティも追加します。通常のフィールドでは
   なく `PrivateAttr` にするのは、これがローダーによる解析後に埋められる由来情報だからです。
   シナリオ自身の YAML が宣言する値ではありません。`model_dump()` には決して現れてはなりません。
   現れると、読み込み時だけの詳細が、シナリオのスキーマの一部として再シリアライズ後のファイルに
   紛れ込みます。そのようなファイルは、`record` や `audit`、将来のシナリオ編集機能が書き出す
   可能性があります。
2. **ファイルからシナリオを読み込む、デバイス不要の2つのローダーが、この属性を設定します。**
   どちらのローダーも、返す直前に、最終的に展開された `list[Scenario]` へ `source_stem` を設定
   します。その時点で、`expand_data` によるカンマ区切り値（CSV）の行ごとの
   複製は、すでに終わっています。データ駆動で展開されたシナリオのどの行も、同じ由来ファイルの
   stem を持ちます。2つのローダーは次のとおりです。
   - [`bajutsu/run/cli.py:161-200`](../../bajutsu/run/cli.py) の `_expand_file()`。`run` 自身の、
     setup を前置きするローダーです。
   - [`bajutsu/common/scenario/load_expanded.py:63-89`](../../bajutsu/common/scenario/load_expanded.py)
     の `load_expanded_scenarios()`。`audit`、`trace --explain`、`coverage`、serve Web UI の
     カバレッジビューが共有します。`load_scenarios_dir()` を経由して、スイート全体を読む他の
     デバイス不要な読み手も共有します。
3. **`sid` の組み立ては、狭い範囲のサニタイザを通してこの属性を読み、フォールバックも備えます。**
   書き換える箇所は2つです。`_ScenarioRunner.run_one` にある
   [`bajutsu/common/runner/pipeline.py:269`](../../bajutsu/common/runner/pipeline.py) と、
   クロスブラウザマトリックスの `_cancelled_pass` にある `:1173` です。どちらも
   `sid = f"{i:02d}-{scenario_slug(s.name)}"` を
   `sid = f"{i:02d}-{sanitize_source_stem(s.source_stem) if s.source_stem else scenario_slug(s.name)}"`
   に変更します。

   `sid` は、ファイルシステムのパスセグメントとしてだけ使われるわけではありません。次の2つが、
   `sid` をエスケープせずに埋め込みます。
   - `report.html` の `src=` と `href=` によるアセットリンク
     （[`bajutsu/templates/report.html.j2:32,43,47`](../../bajutsu/templates/report.html.j2)）
   - serve の `/runs/<runId>/<sid>/…` へのフェッチ

   そのため、`login#1` や `a?b` のような stem をそのまま使うと、相対リンクが `#` や `?` の位置で
   静かに切れます。`scenario_slug()` が出力する `[0-9a-z-]+` では、この種の破損は起こりえません
   （「検討した代替案」を参照）。

   **`sanitize_source_stem()`** は新規の関数です。
   [`bajutsu/common/orchestrator/types/_functions.py`](../../bajutsu/common/orchestrator/types/_functions.py)
   の `scenario_slug()` の隣に置きます。エスケープされない HTML 属性や URL パスセグメントとして安全で
   ない文字だけを `_` に置き換えます。対象は `[A-Za-z0-9_.-]` の範囲外の文字です。`login_flow` の
   ような通常の stem は変わりません。`scenario_slug()` と異なり、`_` と `.` には手を加えません。
   そのため、通常のケースでは、`sid` が由来ファイルの名前と変わらず読めます。安全でない文字を含む
   stem だけが変化します。`{i:02d}-` プレフィックスは今日のままです。新たなカウンタを設けなくても、
   `sid` は同一 run 内で一意であり続けます。
4. **ドキュメント。** [`docs/reporting.md`](../../docs/reporting.md) と
   [`docs/ja/reporting.md`](../../docs/ja/reporting.md) の「出力レイアウト」節を、次の2点で変更します。
   - `sid` の由来を明記する行を追加します。由来ファイルの stem を使うこと、由来ファイルがわから
     ない場合はシナリオの `name:` フィールドを使うことを書きます。この行は、同じ節がすでに説明して
     いる `runId` のフォーマット（`docs/reporting.md:30-32`）の隣に置きます。
   - レイアウトを示す図に、`<sid>/` の階層を追加します。現状の図にはこの階層がなく、`<stepId>/` を
     `runs/<runId>/` に直接ぶら下げています。
5. **テスト。**
   - `tests/runner/test_pipeline.py`：ファイルから読み込んだシナリオの `sid` が、由来ファイルの
     stem を使うことを確認します。`source_stem` を設定せずに直接組み立てた `Scenario` が、これまで
     どおり `scenario_slug(s.name)` による `sid` になることも確認します。
   - ユニット2の2つのローダーそれぞれについて、`source_stem` が読み込んだファイルの stem と一致する
     ことを確認するテストを追加します。データ駆動展開のケースも含め、展開後のどの行も同じ stem を
     持つことを確認します。
   - `source_stem` が `Scenario.model_dump()` の出力に一切現れないことを確認するテストを追加します。
   - `sanitize_source_stem()` のユニットテストを追加します。`login_flow` のような通常の stem は、
     そのまま変わらないことを確認します。`login#1` や `a?b` のように HTML や URL で安全でない文字を
     含む stem は、その文字だけが置き換わることを確認します。

## 検討した代替案

| 代替案 | 採らなかった理由 |
|---|---|
| トップレベルの run ディレクトリ（`runs/<runId>/`）自体をシナリオファイルにちなんで改名する | [BE-0200](../BE-0200-run-id-contract/BE-0200-run-id-contract-ja.md) が確立した、辞書順が時系列順に一致するという契約を壊します。`latest_run()`、`serve` の run 一覧と crawl 一覧、`run/notify` の直前 run 照合がこの契約に依存しているため、そのすべてに代わる仕組みが別途必要になります。加えて、複数のシナリオや複数のシナリオファイルを実行する run に対して、一貫した答えがありません。1つの `runs/<runId>/` ディレクトリが保持するすべてのシナリオに対して、1つの名前が必要になるからです。 |
| `{i:02d}-` インデックスプレフィックスを廃止し、一意性を重複サフィックスカウンタに委ねる | プレフィックスは、追加のコストなしに、run 内で `sid` をすでに一意にしています。プレフィックスを残せば、ディレクトリ一覧を見ただけで実行順がわかります。単なるカウンタには、この利点がありません。一方で、同一ファイル由来のシナリオを区別しやすくする点では、プレフィックスもカウンタと変わりません。本項目の設計では、1つの `login_flow.yaml` に含まれる2つのシナリオは `00-login_flow` と `01-login_flow` になります。両者を区別するのは実行順インデックスだけです。この区別は、`login_flow` と `login_flow_2` というカウンタでも同じように得られます。同一ファイル由来のシナリオを名前で区別できるのは、`scenario_slug(s.name)` です。これは現状の挙動であり、由来ファイルがわからない場合に本項目が使うフォールバックでもあります。インデックスプレフィックスにも重複カウンタにも、この性質はありません。 |
| ファイルの stem を、既存の `scenario_slug()` で正規化する | `login_flow.yaml` が `login-flow` になります。通常のケースでファイル名と一致しなくなり、`sid` をファイルにちなんで名付ける狙いそのものが崩れます。`sanitize_source_stem()`（「詳細設計」を参照）は `_` と `.` に手を加えないため、ファイルシステム、HTML、URL についての同じ安全性を、より狭い代償で得られます。 |
| ソースの stem をそのまま使い、正規化を一切しない | シナリオファイルの名前は、ファイルシステム上すでに安全なパスの1セグメントです。しかし `sid` は、`report.html` のアセットリンクや serve の `/runs/<runId>/<sid>/…` ルートにも、エスケープなしで渡ります（「詳細設計」を参照）。`login#1` や `a?b.yaml` のような正規化していない stem を使うと、これらのリンクが `#` や `?` の位置で静かに切れます。`make check` はこれを検出できません。`sanitize_source_stem()` は、通常のケース（`login_flow`）を変えずに、この隙間を塞ぎます。 |
| `Scenario` に stem を持たせる代わりに、`list[Scenario]` と並行して由来 stem の `list[str]` を、`run_all`、`run_and_report`、`run_matrix_and_report` とその呼び出し元に引き回す | 今日 `list[Scenario]` を持ち回るすべての呼び出し箇所に影響が及びます。`bajutsu/run/cli.py`、`bajutsu/analysis/cli/audit.py`、pipeline のテスト群が該当します。この情報は、常にシナリオ自身と一緒に運ばれるものです。`Scenario` に読み込み時専用の非公開フィールドを1つ持たせるほうが、変更を2つのローダーと1つの読み取り箇所に閉じ込められます。 |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解(作業の単位ごとに1つ)に対応し、ログには変更内容と時期(古い順)を PR へのリンクと
> ともに記録します。

- [x] 作業単位 1——`Scenario.source_stem`：`PrivateAttr` のプライベート属性、読み取り専用の
  `source_stem` プロパティ、そして `set_source_stem()` セッターを追加します。ローダーから
  `_source_stem` へ直接代入すると `ruff` の `SLF001` に抵触するため、公開セッター経由の書き込みに
  しています（プロパティ自体は設計どおり読み取り専用のままです）。
- [x] 作業単位 2——`_expand_file()`（`bajutsu/run/cli.py`）と `load_expanded_scenarios()`
  （`bajutsu/common/scenario/load_expanded.py`）が、それぞれ展開後の最終的なシナリオ一覧に対し、
  返す直前に `set_source_stem()` を呼び出します。
- [x] 作業単位 3——`scenario_slug()` の隣に `sanitize_source_stem()` を追加します。
  `bajutsu/common/runner/pipeline.py` の `run_one` と `_cancelled_pass` は、共通の
  `_evidence_sid()` ヘルパー1つを介して（2箇所が食い違わないように）`s.source_stem` をこの関数に
  通して `sid` を組み立て、読み込み元ファイルが不明なときだけ `scenario_slug(s.name)` に
  フォールバックします。設計の字面からの逸脱があります：置換する文字クラスは、仕様どおりの
  `[^A-Za-z0-9_.-]` ではなく `[^\w.-]`（Unicode の単語構成文字）にしています。ASCII 限定の
  文字クラスでは、日本語のシナリオファイル名の語幹が黙ってアンダースコアの連なりに潰れてしまい
  ます。この codebase 自身のバイリンガルな慣習（CLAUDE.md）を踏まえると現実的なケースであり、
  まさにそのファイルに対して本項目自身の動機を損ないます。`\w` であっても、設計の根拠が挙げていた
  文字（`#`、`?`、`/`、空白など。エスケープされない HTML 属性や URL パスセグメントとしての安全性）
  はすべて置換対象のままで、かつ非 ASCII の語幹も識別可能なまま残ります。
- [x] 作業単位 4——`docs/reporting.md` と `docs/ja/reporting.md` の出力レイアウトに `<sid>/`
  の階層を追加しました（これまで `<stepId>/` が `runs/<runId>/` の直下にぶら下がっているように
  見えていましたが、実際のランタイムはすでにこの階層で証跡を書き出しています）。あわせて `sid`
  の導出規則を説明する 1 行を追加しました。
- [x] 作業単位 5——上記すべてに対するテストを追加しました。`_cancelled_pass` のマトリックス経路、
  両ローダーのデータ駆動展開ケース、`sanitize_source_stem()` の文字置換、そして `source_stem`
  が `model_dump()` に現れないことを確認しています。

ログ：

- [#1977](https://github.com/bajutsu-e2e/bajutsu/pull/1977)——作業単位すべて（5つ）を実装し、本項目
  を完了しました。`Scenario.source_stem`（`model_dump()` に一切現れない、読み込み時専用の
  `PrivateAttr`）、それを展開後の最終的なシナリオ一覧に設定する2つのデバイス不要ローダー、そして
  `scenario_slug()` の隣に `sanitize_source_stem()` を追加しました。`pipeline.py` の `run_one` と
  `_cancelled_pass` は、共通の `_evidence_sid()` ヘルパーを介して読み込み元ファイルの語幹から
  `sid` を組み立て、読み込み元ファイルが不明なときは `scenario_slug(name)` にフォールバック
  します。設計の字面からの逸脱が1点あります：サニタイザーが置換する文字クラスを、仕様どおりの
  ASCII 限定 `[^A-Za-z0-9_.-]` ではなく `[^\w.-]`（Unicode の単語構成文字）にしています。セルフ
  レビューの過程で見つかったもので、ASCII 限定の文字クラスでは日本語のシナリオファイル名の語幹が
  黙ってアンダースコアの連なりに潰れてしまい、まさにそのファイルに対して本項目自身の動機を損なう
  ためです。また、`_matrix()` の衝突について、本 PR に着手する前に #1970 で独立に修正済みだった
  内容を指していた「やらないこと」の記述も訂正しました。

## 参考

- [BE-0200 — run id のフォーマットを 1 つの名前付き契約にする](../BE-0200-run-id-contract/BE-0200-run-id-contract-ja.md)
- [`bajutsu/common/scenario/models/scenario/scenario.py`](../../bajutsu/common/scenario/models/scenario/scenario.py)
- [`bajutsu/common/scenario/load_expanded.py`](../../bajutsu/common/scenario/load_expanded.py)
- [`bajutsu/run/cli.py`](../../bajutsu/run/cli.py)
- [`bajutsu/common/runner/pipeline.py`](../../bajutsu/common/runner/pipeline.py)
- [`bajutsu/common/orchestrator/types/_functions.py`](../../bajutsu/common/orchestrator/types/_functions.py)
- [`bajutsu/templates/report.html.j2`](../../bajutsu/templates/report.html.j2)
- [`docs/reporting.md`](../../docs/reporting.md)

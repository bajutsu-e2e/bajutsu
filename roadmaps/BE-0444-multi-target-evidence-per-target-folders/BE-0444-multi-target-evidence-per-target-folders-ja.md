[English](BE-0444-multi-target-evidence-per-target-folders.md) · **日本語**

# BE-0444 — 宣言済みの各ターゲットの証跡を、ターゲットごとのフォルダにまとめる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0444](BE-0444-multi-target-evidence-per-target-folders-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0444") |
| 実装 PR | [#2073](https://github.com/bajutsu-e2e/bajutsu/pull/2073) |
| トピック | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## はじめに

[BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution-ja.md)
で導入された、[ターゲット](../../docs/ja/glossary.md#target-app-device)を2つ以上宣言する
シナリオについて、このアイテムは宣言済みの各ターゲットの証跡を、そのターゲットの名前を冠した
フォルダにまとめます。シナリオ自身の証跡ディレクトリの中に、`runs/<runId>/<sid>/<target>/…`と
いう形で並びます。今日の
実装は一貫していません。最初に宣言したターゲット(「primary」)だけは`<sid>/`直下に書き込み、それ
以外の宣言済みターゲットはすでに`<sid>/<target>/`へ書き込んでいます。さらに、ステップごとの
スクリーンショットや要素ダンプは、どのターゲットが生成したかによらず、同じ平坦な
`<sid>/<stepId>/`フォルダに置かれます。ターゲットを0個または1個しか宣言しないシナリオ、つまり
このリポジトリ自身のテストスイートとデモが今日使っているシナリオはすべて、この変更のあとも今の
レイアウトのままです。

## 動機

[BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution-ja.md)
は、1つのシナリオが複数のターゲット、例えばiOSターゲットとWebターゲットを1回の`bajutsu run`
呼び出しの中で行き来できるようにしました。同時に、各ターゲット自身のシナリオ全体の録画(動画、
デバイスログ、アプリケーショントレース)には、そのターゲットの名前を冠したフォルダを与え、2つ目
のターゲット自身の`scenario.mp4`が1つ目のものと衝突しないようにしました。ところが、このフォルダ
の慣習は、最初に宣言したターゲット以外にしか適用されていません。`_ScenarioRunner._run_on_lease`
自身のターゲットごとのループ(`bajutsu/common/runner/pipeline.py`)は、primary以外のターゲットの
録画とネットワークキャプチャを、すでに`<sid>/<name>/`の下に書き込んでいます。それでも、primary
自身の録画とネットワークキャプチャは、`manifest.json`や`junit.xml`など実行レベルの他のファイル
と並んで、今も`<sid>/`直下に置かれたままです。各ターゲット自身のシナリオ全体録画を開始・終了する
同じ実行ループのコード(`bajutsu/common/orchestrator/loop/_functions.py`)も、同じ非対称を繰り返し
ます。追加ターゲットの`scenario.mp4`はその名前で名前空間を切りながら、primary自身のものは平坦な
ままにしています。

ステップごとの証跡には、そもそも同じ慣習が一度も適用されていません。各ステップの`before.png`、
`after.png`、`elements.json`は、ステップ自身のidだけから名付けられます
(`bajutsu/common/orchestrator/loop/_step_runner.py`の
`step_id = f"{self.cfg.sid}/{prefix}{step.name or f'step{idx}'}"`。`prefix`は`before`・`after`
フック自身のフェーズラベルです、BE-0392)。ターゲットを表すセグメントはどこにもありません。
ステップの実行順を通した連番のおかげで、異なるターゲットのステップフォルダが偶然衝突することは
ありません。それでも、`<sid>/<stepId>/`というフォルダがどのターゲットに属するかは、
`manifest.json`を開き、そのステップ自身が記録した`target`フィールド(`StepOutcome.target`、
BE-0428)と突き合わせない限り、ディレクトリの構造だけからは読み取れません。失敗したクロス
ターゲットシナリオを調査するチーム、まさにBE-0428が支えようとしている場面では、このグループ
分けをステップ1つずつ、手作業でマニフェストから組み立て直す必要があります。失敗しているター
ゲットのフォルダをそのまま開けば済む話ではありません。

このアイテムは、両方のギャップを一度に取り除きます。primaryを含む宣言済みのすべてのターゲット
の証跡を、シナリオレベルだけでなくステップレベルでも、そのターゲットの名前を冠した1つのフォルダ
にまとめます。実装が終われば、`targets: [showcase-app, web]`を宣言したシナリオは
`runs/<runId>/<sid>/showcase-app/`と`runs/<runId>/<sid>/web/`を作ります。それぞれのフォルダには、
そのターゲット自身の`scenario.mp4`、`network.json`、そのターゲットのステップが書いた
`<stepId>/`フォルダがすべて収まります。読者はどちらか一方のフォルダを開くだけで、そのターゲット
自身の録画、ログ、ステップごとのスクリーンショットだけを見られます。もう一方のターゲットの証跡
が混ざることも、両者を見分けるためにマニフェストを経由する必要もありません。

## 詳細設計

### 新しいフォルダが有効になる条件

このアイテムが加えるフォルダ分けは、`Scenario.targets`が**2つ以上**のエントリを宣言している
シナリオでだけ有効になります。`_ScenarioRunner._run_on_lease`は今日、この条件を`others`
(primary以外のリース群を保持する辞書で、シナリオが2つ目のターゲットを宣言しない限り空のまま)
が空かどうかで判定しています。`run_scenario`内部の`len(target_runtimes or {}) >= 2`
(`bajutsu/common/orchestrator/loop/_functions.py`)も、これと同値の条件です。ターゲットを0個宣言するシナリオ
(既存の単一ターゲットシナリオすべて)や、1個だけ宣言するシナリオ(primaryの名前だけを挙げる
自己宣言シナリオ、
[BE-0436](../BE-0436-primary-target-default/BE-0436-primary-target-default-ja.md))は、今日の
平坦な`<sid>/…`レイアウトのままにします。どちらの場合も、そこにあるターゲットの証跡は1つだけ
であり、区別すべき相手がいないフォルダを1段増やす意味がないからです。このアイテムが変更する
すべての読み取り側・書き込み側は同じ条件を判定するので、1つのシナリオの証跡ツリーの中に、
2つの慣習が混ざることはありません。

### 書き込み側:primaryを含むすべての宣言済みターゲットをフォルダ分けする

4箇所の書き込み処理が変わります。そのうち3箇所は、`bajutsu/common/runner/pipeline.py`と
`bajutsu/common/orchestrator/loop/_functions.py`にあり、すでにprimary以外のターゲットの証跡パス
を`f"{sid}/{name}"`として組み立てています。シナリオが2つ目のターゲットを宣言したときは、
primary自身のパスも同じ扱いに広げます。今のように裸の`sid`のままにはしません。残る1箇所、
ステップごとの証跡には、primaryか非primaryかによらず、初めてターゲットのセグメントを持たせます。

- `_ScenarioRunner._run_on_lease`が、primary自身のシナリオレベルの`expect`アサーション向けに
  組み立てる`VisualContext`(`screenshot_path`・`prefix`。今日は裸の`sid`から作られています)。
- `_ScenarioRunner._run_on_lease`が、primary自身が捕捉したトラフィックのために呼ぶ
  `_write_network`(今日の`prefix = sid if name == primary_name else f"{sid}/{name}"`は、2つ目
  のターゲットが宣言された時点で、`name`がprimaryも含むかたちで、常に`f"{sid}/{name}"`へ収束
  します)。
- `run_scenario`自身が、primaryのシナリオ全体の動画・デバイスログ・アプリケーショントレース
  のために呼ぶ`sink.start_scenario_intervals(sid, …)`・`finish_scenario_intervals(sid, …)`。
- `_step_runner.py`の`step_id`組み立て。シナリオの宣言済みターゲット数が2つ以上のときは、
  ルーティング先のステップ自身のターゲット名を、ステップ名の手前のパスセグメントとして加えます
  (`f"{self.cfg.sid}/{target}/{prefix}{step.name or f'step{idx}'}"`。既存の`prefix`はそのまま
  残します)。この判定は`len(self.by_target)`から得ます。これは
  `_run_steps`がすでに宣言済みターゲットごとに1エントリずつ(primaryも含め、BE-0428自身のコメ
  ントの通り)組み立てているマップです。セグメントの名前は`self.target`(`_StepRunner`ごとに
  すでに追跡済み、BE-0428・BE-0438)から取ります。`web:`ブロックの中に入れ子になったステップは
  今も自分自身のターゲットを持たず、外側のブロックのランナーをそのまま引き継ぐので、そのランナー
  が共有する他のすべてのステップと同じく、そのランナー自身のターゲットフォルダの下に置かれます。

`driver_trace.json`(任意の`--trace`診断出力、`bajutsu/common/runner/pipeline.py`の`run_one`)と、
クラッシュ診断ディレクトリ(`_write_crash_artifacts`、`_CRASH_DIAGNOSTICS_DIR`)は、あえて変更
しません。理由は後述の「対応しないこと」で述べます。

### 読み取り側:固定された深さを前提にしている2箇所

記録済みの証跡を配信・走査する他のコードは、`bajutsu/common/report/`のマニフェスト・JUnit・
Common Test Report Format(CTRF)・HTML生成、`report.html.j2`のアセットリンク、serveの汎用
`/runs/{path}`証跡ルート、`RunArtifactWriter`自身のいずれも、各証跡がすでに組み立て済みの
相対パス名をそのまま読み書きします。固定のフォルダの深さを前提にしていないので、このアイテム
のための変更は要りません。次の2箇所だけは固定の深さを前提にしており、上の書き込み側の変更と
セットで直す必要があります。

- `bajutsu/serve/operations/reads.py`の`_step_artifacts()`(Author→Editのステップピッカー、
  [BE-0013](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md))は、記録済みの
  証跡名からidを読み戻すのではなく、シナリオ自身の読み込み済みYAMLからステップのidを
  `f"{sid}/{step.name or f'step{idx}'}"`として組み立て直し、そのキーで記録済みの証跡を検索
  しています。シナリオ自身の`targets`フィールドが2つ以上のエントリを挙げているときは、
  ステップ自身の`resolved_target`プロパティ(`Step`、BE-0436)から読み取った、書き込み側と同じ
  ターゲットセグメントを受け取ります。書き込み側自身は、生きている`_StepRunner`自身の
  `self.target`からセグメントを取ります。この静的で実行前提のない読み取り側には、その代わりが
  ないので、`resolved_target`を使います。このアイテムより前に記録された複数ターゲットの実行は、
  証跡が今も古い平坦なidのままです。ネスト済みキーでの検索が外れたときは、平坦なidへフォール
  バックします。今後の書き込み側は複数ターゲットの実行に対して平坦なidを二度と生成しないので、
  この2つのキーが同時に別々の実ステップを指すことはなく、安全です。同じ関数が用意する、実行前提
  のないPlay/Replay用のステップ一覧は平坦なままで、この変更の影響を受けません(BE-0262)。
- `bajutsu/analysis/coverage/_functions.py`の`_evidence_files()`と
  `bajutsu/analysis/cli/coverage.py`の`_element_lists()`(`bajutsu coverage`コマンド)は、
  実行セット自身のディレクトリの下にある`network.json`・`elements.json`を探すために、固定の
  深さでグロブしています。`_evidence_files()`の無条件の分岐は`*/*/<name>`(2段)、実行id別の
  分岐は`*/<name>`(1段)です。この固定の段数は、primary以外のターゲット自身の`network.json`を、
  今日すでに1段深いところで見逃しています。このアイテムの動機がそのままなら、primary自身の
  証跡についても同じ見逃しを新たに残すところでした。`elements.json`はさらに広く見逃しています。
  このファイルはもう1段深い`<sid>/<stepId>/elements.json`にあり、今日の固定の段数では単一
  ターゲットの実行でも複数ターゲットの実行でも一切見つかりません。これは、上のマルチターゲット
  固有のギャップとは別の、このアイテムが持ち込んだわけではない既存の不具合ですが、同じ修正で
  一緒に解消します。`_evidence_files()`は、固定の段数ではなく任意の深さにマッチする`**/<name>`
  へ広げます。これで、単一ターゲットの実行でも複数ターゲットの実行でも、宣言済みの各ターゲット
  自身の`network.json`と、各ステップの`elements.json`を見つけられます。
  `bajutsu/analysis/cli/coverage.py`自身のグロブは、並行して広げるのではなく廃止します。
  `bajutsu/analysis/coverage/_functions.py`の`read_exchanges()`・`read_observed_ids()`の隣に
  新設する`read_element_lists()`が、`elements.json`向けに`_evidence_files()`を一度だけ包み、
  この列挙とパースを行う唯一の場所になります。`read_observed_ids()`とCLIの画面到達率の次元の
  両方がこれを再利用するので、2つのコピーが同じ形で壊れていたという、そもそもの重複自体を
  解消します。

### 対応しないこと

- **`driver_trace.json`は`<sid>/driver_trace.json`のまま、フォルダ分けしません。** `run
  --trace`は、シナリオ全体を囲む1つのトレースコンテキストを開きます。複数ターゲットシナリオ
  が起動するすべてのバックエンドにまたがり、1つのターゲット自身のドライバだけに閉じません。
  帰属させるべき単一のターゲットが存在しないので、どれか1つのターゲットのフォルダへ入れると、
  このファイルが実際に扱っている範囲を誤って伝えることになります。
- **クラッシュ診断ディレクトリ(`_write_crash_artifacts`、`_CRASH_DIAGNOSTICS_DIR`)は、今日の
  位置のまま変更しません。** クラッシュしたリースがprimaryかどうかによらず、今も裸の`<sid>/`
  の下に書き込まれています。これはこのアイテムが新しく持ち込むギャップではなく、以前から
  ある未対応の部分です。解消するには、どのリースがクラッシュしたかをリトライループ自身の
  失敗経路まで通す、独自のターゲット別帰属の設計が要り、このアイテムのスコープ外とします。
- **ターゲット名をパスセグメントとして使う際のサニタイズは追加しません。** primary以外の
  ターゲットのフォルダ名は、BE-0428が実装されて以来、サニタイズしていない設定キーのままです。
  このアイテムは、この既存の慣習をprimaryとステップごとのフォルダにまで広げるだけであり、
  シナリオ自身のソースファイルのstemに対して
  [BE-0417](../BE-0417-scenario-result-folder-naming/BE-0417-scenario-result-folder-naming-ja.md)
  が加えたような、狭い範囲のサニタイザがターゲット名自身にも要るかどうかは扱いません。

### 作業分解(MECE)

1. **書き込み側。** 上記の4箇所(`pipeline.py`のprimary向け`VisualContext`と`_write_network`の
   プレフィックス、`_functions.py`のprimary向け`start_scenario_intervals`・
   `finish_scenario_intervals`呼び出し、`_step_runner.py`の`step_id`)。それぞれ、シナリオの
   宣言済みターゲット数が2つ以上であることをゲートにします。
2. **読み取り側。** `bajutsu/serve/operations/reads.py`の`_step_artifacts()`。
   `bajutsu/analysis/coverage/_functions.py`と`bajutsu/analysis/cli/coverage.py`にある
   2つのカバレッジ用グロブ。
3. **ドキュメント。** [`docs/reporting.md`](../../docs/reporting.md)の出力レイアウトの節に、
   ターゲットを2つ以上宣言したシナリオ向けの`<target>/`の段を加えます。`docs/ja/`の対訳も
   同様に更新します。
4. **テスト。** 複数ターゲットシナリオの証跡が、シナリオレベル(動画・deviceLog・
   network.json)とステップレベル(スクリーンショット・elements.json)のどちらでも、
   primaryと非primaryの両方について`<sid>/<target>/…`の下に置かれること。ターゲットを
   0個または1個しか宣言しないシナリオの証跡が、今日とバイト単位で変わらないこと。serveの
   ステップピッカーが、複数ターゲットの実行のステップごとの証跡を、新しいターゲット付きの
   idで解決できること。このアイテムより前に記録された複数ターゲットの実行に対しては、古い
   平坦なidへフォールバックできること。広げたカバレッジ用グロブが、複数ターゲットの実行で
   宣言済みの各
   ターゲット自身の`network.json`を見つけられること。加えて、今日の平坦なテスト用フィクス
   チャーではなく実際の`<sid>/<stepId>/elements.json`というレイアウトのフィクスチャーで、
   単一ターゲットの実行でも複数ターゲットの実行でも、`elements.json`をそもそも見つけられる
   ようになること。

## 検討した代替案

| 代替案 | 採用しなかった理由 |
|---|---|
| 1つしか宣言していないシナリオも含め、すべてのターゲットをフォルダ分けする | 採用しませんでした。ターゲットを1つしか宣言していなければ、区別すべき相手がいません。パスセグメントを増やしても、読者にとっての利点がないまま、既存の自己宣言シナリオすべての証跡パスを長くするだけです。このアイテムの動機が関わらないシナリオのレイアウトまで変えてしまいます。 |
| primaryは平坦なままにし、非primaryのターゲットだけステップごとのフォルダにターゲットセグメントを持たせる | 採用しませんでした。同じシナリオの証跡ツリーの中に、`<sid>/<stepId>/…`のprimaryと`<sid>/<target>/<stepId>/…`の非primaryという、2つの慣習が並んで残ります。これは、このアイテムが取り除こうとしている非対称そのものです。1つの慣習を一様に適用するほうを選びました。 |
| 平坦なレイアウトのまま変えず、`manifest.json`がすでに記録している`StepOutcome.target`・`RunResult.target_devices`(BE-0428)だけを頼りに、読者がターゲットごとに証跡をグループ分けし直す | 採用しませんでした。読者(あるいはスクリプト)は、ステップ1つごとにマニフェストと実行ディレクトリを手作業で突き合わせる必要が残ります。これはまさに*動機*の節で述べた不便さです。読者がそのまま開けるフォルダのほうが、自分で調べ直す手間より安く済みます。 |
| すでに完了している実行のディレクトリを、新しいレイアウトへディスクの上で移行する | 採用しませんでした。完了済みの実行は、`manifest.json`にすべての証跡を記録済みのパスで持っているので、書き込まれた当時のレイアウトのまま、正しくレンダリングされ、正しくリンクされ続けます。過去の実行ディレクトリをディスク上で書き換えることは、調査がまだ頼っている実行を壊すリスクを負うだけで、見た目を揃える以上の利益がありません。 |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 書き込み側:シナリオが2つ以上のターゲットを宣言したとき、primary自身のシナリオレベル・
      ステップレベルの証跡が、他の宣言済みターゲットと同じように、それぞれ専用のフォルダの下に
      収まります。
- [x] 読み取り側:serveのステップピッカーと`bajutsu coverage`のグロブが、複数ターゲットの実行の
      ターゲットごとの証跡を正しく解決できます。
- [x] ドキュメント:`docs/reporting.md`とその`docs/ja/`対訳。
- [x] テスト:書き込み側のフォルダ分け(複数ターゲットと単一・0ターゲットの両方のケース)、serve
      のステップピッカー、広げたカバレッジ用グロブ。

ログ:

- [#2073](https://github.com/bajutsu-e2e/bajutsu/pull/2073) — 単位1〜4、このアイテムを完了しました。`pipeline.py`のprimary向け`VisualContext`・
  `_write_network`のプレフィックス、`_functions.py`のprimary向け`start_scenario_intervals`・
  `finish_scenario_intervals`呼び出し、`_step_runner.py`の`step_id`組み立てはすべて、シナリオが
  2つ以上のターゲットを宣言したときに、ルーティング先のターゲット自身のフォルダの下にまとまり
  ます。serveのステップピッカー(`reads.py`)は、`Step.resolved_target`から同じターゲット付きの
  idを組み立て直し、このアイテムより前に記録された複数ターゲットの実行に対しては古い平坦なid
  へフォールバックします。`_evidence_files()`は、固定の段数から`**`へ広げました。これは既存の
  不具合も一緒に解消します。`elements.json`は単一ターゲットの実行でも、古い段数よりさらに1段
  深いところにあったためです。セルフレビューの1ラウンドで、2箇所に重複したまま直すと再びずれ
  かねないと判明したため、`bajutsu coverage`自身のグロブは廃止し、`read_exchanges()`・
  `read_observed_ids()`の隣に新設した`read_element_lists()`を、観測id側と画面到達率側の両方が
  再利用する形にしました。同じラウンドで、primaryをフォルダ分けする判定自身の実バグも見つかり
  ました。`run_scenario`は公開関数であり、`target_runtimes`を渡しつつ`primary_target`(既定値は
  `""`)を省略した呼び出しでは、この判定がprimary自身の証跡を`<sid>`直下ではなく末尾スラッシュ
  付きの`<sid>//scenario.mp4`にまとめてしまっていました。この判定は、今後`primary_target`が
  空でないことも要求します。
  `docs/reporting.md`・`docs/ja/reporting.md`の出力レイアウトの節には、新しい`<target>/`の段を
  加えました。

## 参考

- [BE-0428 — 複数ターゲットを1シナリオ内で行き来する実行モデル](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution-ja.md) — `Scenario.targets`、ステップごとの`target`、そしてこのアイテムがprimaryにまで広げる非primary向けフォルダの慣習を導入しました。
- [BE-0436 — primary target: 複数ターゲットシナリオのステップで`target`を省略できるようにする](../BE-0436-primary-target-default/BE-0436-primary-target-default-ja.md) — `Step.resolved_target`。serveのステップピッカーの修正が読み取ります(書き込み側自身は、ランナー自身の`self.target`からセグメントの名前を取ります)。
- [BE-0013 — シナリオGUIエディタ](../BE-0013-scenario-gui-editor/BE-0013-scenario-gui-editor-ja.md) — このアイテムがステップidの組み立て直しを直す、Author→Editのステップピッカー。
- [BE-0262 — Authorエディタでのライブなステップピッキングとターゲット限定実行](../BE-0262-serve-author-live-step-picker/BE-0262-serve-author-live-step-picker-ja.md) — このアイテムのserve側の修正が変更せずに残す、実行前提のないステップ一覧。
- [BE-0417 — シナリオの証跡ディレクトリをソースファイルにちなんで名付ける](../BE-0417-scenario-result-folder-naming/BE-0417-scenario-result-folder-naming-ja.md) — 同じディレクトリの慣習に対する直近の変更であり、このアイテムがターゲット名にまでは広げないサニタイズの前例です。
- [`docs/reporting.md`](../../docs/reporting.md) — このアイテムが更新する出力レイアウトの節。
- [`docs/glossary.md#target-app-device`](../../docs/glossary.md#target-app-device) — ターゲットとは何かを説明する用語集の項目。
- [`bajutsu/common/runner/pipeline.py`](../../bajutsu/common/runner/pipeline.py)・[`bajutsu/common/orchestrator/loop/_functions.py`](../../bajutsu/common/orchestrator/loop/_functions.py)・[`bajutsu/common/orchestrator/loop/_step_runner.py`](../../bajutsu/common/orchestrator/loop/_step_runner.py) — このアイテムが変更する書き込み側。
- [`bajutsu/serve/operations/reads.py`](../../bajutsu/serve/operations/reads.py)・[`bajutsu/analysis/coverage/_functions.py`](../../bajutsu/analysis/coverage/_functions.py)・[`bajutsu/analysis/cli/coverage.py`](../../bajutsu/analysis/cli/coverage.py) — このアイテムが変更する読み取り側。

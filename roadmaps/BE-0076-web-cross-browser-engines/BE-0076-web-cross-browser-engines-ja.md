[English](BE-0076-web-cross-browser-engines.md) · **日本語**

# BE-0076 — ブラウザエンジンの選択とクロスブラウザ互換マトリクス（web backend）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0076](BE-0076-web-cross-browser-engines-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0076") |
| 実装 PR | [#355](https://github.com/bajutsu-e2e/bajutsu/pull/355), [#360](https://github.com/bajutsu-e2e/bajutsu/pull/360) |
| トピック | プラットフォーム対応 |
<!-- /BE-METADATA -->

## はじめに

本提案は、Web（Playwright）backend に**ブラウザエンジンの軸**を、2 段階で加えます。

1. **エンジンの選択。** `--browser chromium|firefox|webkit` フラグ（および設定での既定値）を設け、`record` と `run` が選んだエンジンを駆動できるようにします。現状は、どちらも Chromium に固定されています。
2. **クロスブラウザマトリクス。** `--browsers chromium,firefox,webkit` のように複数エンジンを指定すると、同じシナリオを各エンジンで実行します。**エンジン × シナリオの pass/fail マトリクス**を決定論的に出力します。あるエンジンだけ落ちて他は通ったセルは、レンダリングエンジンや仕様の差による非互換として、機械的に検出されます。

ブラウザエンジンは、`--workers` や端末選択と同じ**実行軸**であり、シナリオの内容ではありません。そのため、シナリオはエンジン非依存、プラットフォーム非依存のままです。ゲートに大規模言語モデル（LLM）は入りません。エンジンごとの結果は既存の決定論的 `run` の判定そのものであり、マトリクスはその判定を集計するだけです。本提案は、prime directive（[CLAUDE.md](../../CLAUDE.md)）の内側に完全に収まります。

## 動機

### 現状の backend は Chromium 固定です

[BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md) で着地した Web backend は、単一エンジンに固定されています。

- `bajutsu/drivers/playwright.py` は、ブラウザを `_start_chromium` 経由で起動します。`_start_chromium` は `pw.chromium.launch(...)` を呼びます。
- `bajutsu/backends.py` の `ensure_web_runtime` は、`chromium` だけをインストールします。

BE-0041 の seam 表は、「headless で**クロスブラウザ**」なアクチュエータを掲げていました。出荷した v1 スライスが届くのは、Chromium（Blink）までです。利用者がエンジンを選べる口は、どこにも開いていません。

### エンジン固有の不具合こそ E2E が扱うべき対象です

Playwright は、独立した 3 つのレンダリングエンジンを同梱します。Chromium（Blink）、Firefox（Gecko）、WebKit（Safari の中核エンジン）です。**3 つとも Linux 上で headless 実行できます**。既存の `make check` / CI ゲートの内側で動き、Mac も追加のインフラも要りません。

これらが露わにする不具合は、単一エンジンのテストでは原理的に見えないものです。

- Gecko だけが従う CSS 機能やレイアウトの癖
- WebKit に欠けるか、挙動の異なる JavaScript / DOM API
- 描画の食い違う日付入力や数値入力
- flexbox や grid の境界条件

「Chrome では動くが Safari で壊れる」は、本番障害の典型です。Chromium しか駆動しない E2E ツールは、この種の失敗に対して構造的に盲目です。この種の失敗を検出することは、まさに E2E ツールの役目です。代替エンジンはデバイスファームではなくダウンロードで済むので、Playwright を使えば、検出はここでほぼ無償で手に入ります。

### prime directive に素直に収まります

この検出は、**AI の判断ではなく、機械的に検証できる**事実です。「シナリオが Chromium で通り、WebKit で落ちる」は、2 つの決定論的 `run` 判定です。それぞれの判定は、従来どおり機械的アサーションだけから計算されます。マトリクスはその判定の表であり、ゲートはモデルを一切参照しません。そのため、prime directive #1（AI は判定しない）は構成上保たれます。

AI の役割は、従来どおり助言にとどまります。`triage`（[BE-0021](../BE-0021-ai-triage/BE-0021-ai-triage-ja.md)）は「なぜ WebKit だけ落ちたか」を*調査*できますが、pass/fail は決めません。

決定論（#2）も保たれます。エンジンごとの実行は、固定 sleep を使わず条件待ちで動く、独立した決定論的な run だからです。アプリ非依存（#3）も保たれます。エンジンはフラグや設定が運ぶ実行軸であり、シナリオには書き込まれません。同じシナリオ YAML が、どのエンジンでも無変更で動きます。

## 詳細設計

### 第 1 段階：エンジンの選択

**軸。** `run` と `record` に `--browser <engine>` を加えます。`<engine>` は、`chromium`（既定。現在の挙動を維持します）、`firefox`、`webkit` のいずれかです。設定側にも既定値を設け、ターゲットがフラグなしでエンジンを固定できるようにします。キーは `targets.<name>.browser` で、`config.py` が `Effective` に解決し、既定は `chromium` とします。優先順位は、既存の `headless` / `--headed` の仕組みと同じです。フラグが設定を上書きし、設定が組み込み既定を上書きします。

`config.py` には、この設計の雛形になる web 専用フィールドがすでにあります。`TargetConfig.headless`（`bool = True`）が `Effective.headless` に解決されます。`--headed/--no-headed` が与えられたときは、`run` が `replace(eff, headless=not headed)` で上書きします。`browser` 軸も、同じ 3 つの部品を同じ形で足します。

1. **設定フィールド。** `TargetConfig.browser: str = "chromium"` です（web 専用で、iOS は無視します）。`field_validator` で `{"chromium", "firefox", "webkit"}` のいずれかに検証し、綴り間違いを読み込み時に落とします。これは `Defaults._valid_idb_version` と同じ「読み込み時に大きく失敗させる」流儀であり、実行中に落ちる形を避けます。
2. **`Effective` への解決。** 凍結データクラス `Effective` に `browser: str = "chromium"` フィールドを足し、`resolve()` で `a.browser` からそのまま埋めます。`headless` と同じくターゲット単位の値であり、`defaults` とマージする値ではありません。
3. **フラグの上書き。** `run` / `record` の `--browser <engine>` オプションが指定されたとき、`eff = replace(eff, browser=<engine>)` を適用します。形は現在の `--headed` の上書きとまったく同じです。同じ 3 エンジンの集合に検証し、未知の値は Playwright に届く前に終了コード 2 で弾きます。

**唯一の seam に通す。** エンジン選択が触れるのは、web backend の構築経路だけです。決定論的コア（`base.resolve_unique` / `find_all`、`query()` のスナップショット、orchestrator）には手を入れません。現状では、エンジンは 3 層下で固定されています。`PlaywrightDriver.__init__` が `starter: Starter = _start_chromium` を既定とし、`Starter = Callable[[bool], _Started]` です。`_start_chromium(headless)` が `pw.chromium.launch(...)` を呼びます。変更は、この 1 つのクロージャをエンジン名で一般化するだけです。

| Seam | 現状 | 変更 |
|---|---|---|
| `bajutsu/drivers/playwright.py` | `_start_chromium(headless)` → `pw.chromium.launch(headless=…, slow_mo=…)`。`PlaywrightDriver(…, starter=_start_chromium)` | `getattr(pw, engine)`（`pw.chromium` / `pw.firefox` / `pw.webkit`）を起動する `Starter` を返す `_start_browser(engine)` ファクトリを設ける。`PlaywrightDriver` が `browser: str = "chromium"` 引数を取り、そこから starter を組み立てるので、`relaunch()`（`self._starter` を呼び直す）も同じエンジンを建て直す |
| `bajutsu/backends.py` `make_driver` | `make_driver(actuator, udid, *, base_url, headless, record_video_dir)` → `PlaywrightDriver(base_url, headless=headless, …)` | `browser: str = "chromium"` キーワードを足して `PlaywrightDriver(base_url, headless=headless, browser=browser, …)` と渡す |
| `bajutsu/runner/launch.py` `launch_driver` | `make_driver(actuator, udid, base_url=eff.base_url, headless=eff.headless, …)` を呼ぶ | `browser=eff.browser` も渡す（run 向けに web ドライバを建てる唯一の呼び出し箇所。`doctor._current_screen` は `PlaywrightDriver` を直に組み立てるので、同じく `browser=eff.browser` を加える） |
| `bajutsu/backends.py` `ensure_web_runtime` | Playwright が import 可能なら no-op。web が要求され*かつ* Playwright が無いときだけ `uv pip install playwright` ＋ `playwright install chromium` を実行 | Playwright を導入するとき（および後続のエンジン導入ステップとして）、要求されたエンジンを導入する。単一なら `playwright install <engine>`、マトリクスなら `playwright install firefox webkit`（と chromium）。詳細は後述 |
| `.github/workflows/web-e2e.yml` / docs | `playwright install --with-deps chromium` | クロスエンジンのジョブで `firefox webkit` も導入する。Playwright ブラウザのキャッシュキー（すでに `hashFiles('uv.lock')`）は変えない |

`record` は、選ばれたエンジンを駆動します。同じ `Driver` インターフェースに対する AI オーサリングなので、`eff.browser` を `launch_driver` に通すだけが変更点です。記録される YAML は、エンジン非依存のままです。次の部品はいずれもエンジン非依存で、現状のままです。

- セレクタの対応付け（`_ROLE_MAP`）
- `QUERY_JS` による `query()` の DOM 走査
- コアを通した actuation
- 健全性シグナル（`pageerror` とメインフレームのステータスと `dialog`）
- `capabilities()`

`QUERY_JS` は標準 DOM であり、`getBoundingClientRect` の座標計算も 3 つのエンジンで同一に動きます。

**オンデマンドの導入。** 現状の `ensure_web_runtime(backends)` は、web backend が要求され*かつ* Playwright パッケージが無いとき以外は no-op です。web が要求されていないとき、または `_playwright_available()`（import の有無を見るプローブ）がすでに真のときは、早期 return します。無いときに限って、`uv pip install playwright` のあと `playwright install chromium` を実行します。このパッケージのプローブは、*どのブラウザバイナリが存在するか*を区別しません。そのため、firefox / webkit の導入には、単一のパッケージプローブではなく、エンジンごとの確認が必要です。

設計は次のとおりです。パッケージを確保したあと、この run が必要とするエンジンについて `playwright install <engines>` を走らせます。必要なエンジンとは、解決した `eff.browser`、または `--browsers` のリストです。`playwright install` は**冪等**です。web-e2e ワークフローのコメントも、すでにこの性質に依拠しています（「古いブラウザは単に再取得される」）。要求したエンジンに対して無条件に呼んでも安全です。欠けているバイナリは取得され、存在するものは高速な no-op になります。自動プロビジョニングの約束（`make serve` が idb を足し、web run が Playwright を足す）は保たれます。設計は、その約束を選んだエンジンへ広げます。

**`doctor` の報告。** `doctor` は、すでに `preflight.runnability(actuator, …)` で実行可能性を判定し、修正できるチェックリストを出します。web アクチュエータについては、**どのエンジンが導入済みか**を報告すべきです。エンジンごとの存在確認で実現します。たとえば `playwright install --dry-run` の出力やブラウザレジストリのパスを調べます。すると、「`webkit` を要求したが `chromium` しか入っていない」が、1 行の直し方とともに `doctor` で表面化します。下流の分かりにくい起動失敗にはなりません。iOS で idb バージョンチェックが担うのと同じ役割です。

**実装状況。** 第 1 段階（エンジンの選択）は出荷済みです。対象は次のとおりです。

- `browser` の config フィールドと、その読み込み時の検証
- `run` / `record` の `--browser` フラグ（フラグ > config > 既定の優先順位）
- `PlaywrightDriver` / `make_driver` / web environment / `doctor` を貫くエンジンの引き回し
- `ensure_web_runtime` での実行時 `playwright install <engine>`

いずれも、fake starter を使った高速な `make check` ゲート（実ブラウザなし）で確認しています。実機での firefox / webkit の起動は、web-e2e の経路に委ねます。

**第 2 段階（`--browsers` マトリクス）は出荷済みです。** 対象は次のとおりです。

- `run` の `--browsers` フラグ
- エンジンごとに 1 パスを回すファンアウト（`run_matrix_and_report`。証跡を `run_dir/<engine>/<sid>` の下に書く）
- エンジンでタグ付けする `RunResult.engine`
- manifest の `matrix` ブロックと all-must-pass の `ok`
- エンジンを織り込んだ JUnit の `classname="bajutsu.<engine>"`
- `report.html` のエンジン × シナリオのグリッド

いずれも、fake な lease を使った高速ゲートで確認しています。実機でのクロスエンジン実行は、web-e2e の経路に残します。

### 第 2 段階：クロスブラウザマトリクス

**ファンアウト。** `run` に `--browsers <list>`（例：`--browsers chromium,firefox,webkit`）を加え、選ばれた各シナリオを、列挙したエンジンごとに 1 度ずつ実行します。`--browsers chromium` は `--browser chromium` と同義です。2 つのオプションは、1 つの軸の単一エンジン版と複数エンジン版にすぎません。単一エンジンの run は、マトリクスの仕掛けを一切負担しません。

**ファンアウトを run パイプラインへ写す：エンジンごとに逐次。** run は、**エンジンを巡るループ**として構成し、各エンジンに `run_and_report` 相当の完全な 1 パスを割り当てます。複数エンジンを 1 つのデバイスプールに混ぜる構成は採りません。理由は具体的です。`device_pool` は、アクチュエータをちょうど 1 つ選び（`select_actuator(backends)`）、1 種類のレーンを建てます。`_resolve_lanes` は、すでに `--workers N` を、*1 つのエンジンに対する* N 本のほぼ無償な `web-{i}` `BrowserContext` レーンへ変換します。

設計は、[BE-0054](../BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md) の並列レーンをエンジンの**内側**で再利用します。そのため、`--workers` は引き続きシナリオを並列化します。エンジンの巡回は、そのプールの**外側**に置きます。各エンジンのプールは同質に保たれ、エンジンごとの区別を `device_pool` / `launch_driver` やコレクタの配線へ通す必要がなくなります。

各エンジンの 1 パスは、自分のプールをリースし、選ばれたシナリオを既存の `run_all` で実行します。エンジンごとの結果リストとエビデンスツリーが生まれます。マトリクスは、それらのパスの組み立てです。

将来は、エンジンを並行実行する最適化もありえます。エンジンは互いに独立したプロセスだからです。それでも v1 を逐次とするのは、既存の単一エンジン経路を無変更で再利用でき、エビデンスのディレクトリが自明に衝突しないからです。

**エビデンスの配置（衝突なし）。** 現状の `run_all` は、各シナリオの成果物を `run_dir/<sid>` に書きます（`sid = f"{i:02d}-{scenario_slug(s.name)}"`）。マトリクスは、このパスにエンジンを前置し、`run_dir/<engine>/<sid>`（例：`chromium/00-login`、`webkit/00-login`）とします。2 つのエンジンで同じシナリオを動かしても、`network.json`、スクリーンショット、動画を互いに上書きしません。各エンジンのパスには、固有の `run_dir` サブツリーを渡します。

**成果物：マニフェスト内のエンジン × シナリオのマトリクス。** `manifest.json` は run の単一の真実です（`manifest_dict` → `"scenarios": [asdict(r) for r in results]`、`"ok": all(r.ok …)`）。マトリクスは、v1 の形を壊さずにこれを拡張します。各 `RunResult` はすでに `backend` フィールドを持つので、自然な表現は、エンジンで印付けた**結果のフラットなリスト 1 つ**に、集計のマトリクスビューを添える形です。具体的には次のとおりです。

- エンジンごとの `RunResult` が、自身のエンジンを記録します。現状の web の結果は、どれも `RunResult.backend` が `"playwright"` です。そこで `backend` に意味を重ねず、明示的な `engine: str = ""` フィールド（iOS や単一エンジンでは空）を足し、パスごとに埋めます。`backend` はアクチュエータ、`engine` はレンダリングエンジンと、意味を分けて保ちます。
- マニフェストにトップレベルの `"matrix"` ブロックを足します。形は `{ engines: ["chromium","firefox","webkit"], scenarios: [<name>…], cells: { "<scenario>": { "<engine>": {ok, sid, failure} } } }` です。ブロックは、`scenarios` にすでにあるエンジンごとの判定の純粋な集計です。`report.html` は、これをエンジン × シナリオの格子として描きます。Chromium と Firefox では緑で WebKit では赤という行が、本項目が見つけようとする、機械検出された非互換です。
- JUnit（`junit_xml`）は、現状シナリオごとに `<testcase classname="bajutsu">` を 1 つ出します。マトリクスは、エンジンをケースに織り込み、`classname="bajutsu.<engine>"`（あるいはエンジンごとの `<testsuite>`）とします。CI は `chromium.login` と `webkit.login` を別ケースとして見られます。マニフェストを読まなくても、エンジンごとの失敗を CI の画面で帰属できます。

**判定の意味：機械のみ、全エンジン通過。** `--browsers` の run は、要求した**すべての**エンジンが、選択した全シナリオを通したときにだけ緑とします（`manifest["ok"] = all(r.ok for r in 全エンジンの結果)`）。エンジン固有の失敗が 1 つでもあれば、run は失敗します。各セルの `ok` は、既存の決定論的 `run` の判定です。機械的アサーションのみで、条件待ちであり、固定 sleep はなく、**LLM もありません**。マトリクスはその真偽値の純粋な集計なので、prime directive #1 は構成上保たれます。ゲートはモデルを一切参照しません。

あるエンジンを「助言的で非ブロッキング」と印付ければ、既知の WebKit の欠落を CI を落とさずに追跡できます。これは改良の余地であり、Alternatives に記します。v1 では扱いません。

### 検証計画

作業は、プロジェクトがすでに回している 2 つのゲート（CLAUDE.md の「ゲート」）に、素直に分かれます。

**高速な `make check` ゲート（ブラウザなし、Linux、被覆の大半）。** 実際のブラウザ起動を除けば、すべてがブラウザ非依存です。フェイクを使ってユニットテストできます。`parse_dom` や注入した `_Page` を Playwright なしで駆動する、既存の web テストと同じ要領です。

- *エンジンの解決と優先順位。* `resolve()` が既定で `Effective.browser == "chromium"` を返すこと、`targets.<name>.browser` を尊重すること、`--browser` の上書きが設定に勝つことをアサートします。`headless` / `--headed` の優先順位と同じ 3 段階のテストです。
- *検証。* 不正な `browser` 値が設定読み込みで落ち（`field_validator`）、不正な `--browser` フラグが終了コード 2 になることを確かめます。どちらもブラウザに触れません。
- *受け渡し。* フェイクの `Starter`（既存のテスト用 seam である `PlaywrightDriver(starter=…)`）が、要求されたエンジンを記録します。記録によって、`make_driver` / `launch_driver` が `eff.browser` を端から端まで通すことを示します。
- *マトリクスの組み立て。* マニフェストと JUnit のビルダーに、合成のエンジンごと `RunResult`（同じシナリオで緑の Chromium と赤の WebKit）を与えます。`"matrix"` ブロック、集計の `ok`、エンジンごとのエビデンス前置、エンジンで分けた JUnit ケースをアサートします。すべてデータからで、ブラウザは不要です。「あるエンジンで赤、別のエンジンで緑が表面化する」契約を、決定論的に固定するテストです。

**web-e2e 経路（実ブラウザ、Linux、重め、`make check` の外）。** `web-e2e.yml` は、すでに実機の headless Chromium を `demos/web` に対して駆動します。これに、クロスエンジンのジョブを足します。ジョブは次の 3 つを行います。

- `firefox webkit` を導入します。
- 代表的な `demos/web` のシナリオを各エンジンで実行し、エンジンごとの判定をアサートします。
- エンジン間で挙動が分かれる振る舞いにわざと依存するフィクスチャを 1 つ加えます。このフィクスチャが、マトリクスは本当にエンジン固有の失敗を（1 つで赤、他で緑として）報告し、黙って通り抜けないことを、端から端まで示します。

このジョブは、現在の web-e2e と同様にパスフィルタで起動され、必須チェックではありません。高速ゲートを遅くせずに、実機のクロスエンジン信号を加えます。

### 決定論、アプリ非依存、ゲート

* **LLM なし。判定にモデルは触れません。** エンジンごとの結果は既存の決定論的 `run` であり、マトリクスは判定を集計します。prime directive #1 と #2 は、構成上保たれます。
* **アプリ非依存。** エンジンは実行軸（フラグ、設定）であり、シナリオの内容ではありません。同じ YAML がどのエンジンでも動くため、prime directive #3 が保たれます。
* **Linux で検証でき、既存ゲートの内側で回ります。** 3 つのエンジンはいずれも Linux 上で headless 実行できます。クロスエンジン経路は、現状の Chromium 経路と同じ `make check` / web-e2e の CI ジョブで動かせます。Mac もエミュレータも要りません。実費は、クロスエンジンのジョブで Firefox と WebKit のブラウザビルドを CI が取得してキャッシュする、1 点だけです。Chromium のみの実行には影響しません。

### 既存項目との関係

* **[BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)（web backend）の上に積みます。** ファンアウトには、**[BE-0054](../BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md) の並列レーンを再利用します**。本項目は BE-0054 とは*別物*です。BE-0054 のスコープは、単一エンジン上の rich-end な機能と明記されています。ネイティブネットワーク、video / console エビデンス、エミュレートした multi-touch、並列実行です。エンジン選択やクロスエンジンのマトリクスは扱いません。本項目は、その上にエンジン軸を加えます。
* **[BE-0021](../BE-0021-ai-triage/BE-0021-ai-triage-ja.md)（AI triage）** が、素直に拡張されます。1 つのエンジンだけで見られた失敗は、助言的な原因調査にとって、強く構造化された手がかりになります。それでも判定にはなりません。
* **[BE-0062](../BE-0062-playwright-codegen/BE-0062-playwright-codegen-ja.md)（Playwright codegen）** は、将来、エンジンごとのプロジェクト設定を出力できるかもしれません。本項目の範囲外です。

## 検討した代替案

* **エンジンをシナリオ YAML に入れる。** 却下します。シナリオがエンジンに縛られ、プラットフォーム / エンジン非依存というシナリオモデルが壊れます。同じ YAML が、iOS でも Chromium / Firefox / WebKit でも無変更で動かなければなりません。エンジンは `--workers` や端末選択と同じく実行軸に属し、テスト対象の成果物には属しません。
* **エンジンを backend トークンに埋める（`--backend web:firefox`）。** 主たる表面としては却下します。registry は、*プラットフォーム*トークンを*アクチュエータ*へ展開します（`web` → `playwright`）。レンダリングエンジンは、プラットフォームでも別のアクチュエータでもなく、唯一の Playwright アクチュエータへのパラメータです。専用の `--browser` フラグのほうが明快で、Playwright 自身の CLI とも揃います。registry のプラットフォーム → アクチュエータという意味も保てます。`web:firefox` という綴りは、需要があれば後から糖衣として加えられます。
* **Chromium 固定のまま、手作業のクロスブラウザ確認に頼る。** 却下します。それこそ本項目が埋める穴です。エンジン固有の不具合は、E2E ツールが捕らえるべきものです。Playwright は、代替エンジンを既存の Linux ゲート上でほぼ無償にします。手作業にとどめれば、手に入る最も安価なクロスエンジンの被覆を放棄することになります。
* **クロスエンジンの*視覚*的一致（エンジン間でスクリーンショットをピクセル比較）。** v1 では意図的に対象外とします。レンダリングはエンジン間で正当に異なります（フォントのヒンティング、サブピクセルのレイアウト）。クロスエンジンのピクセル差分はノイズが多く、「同じに見えるか」という清潔でない機械判定へ漂流する危険があります。本項目が扱うのは、エンジンごとの**機能的**な pass/fail です。既存の視覚回帰アサーション（[BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions-ja.md)）によるエンジンごとの基準画像は、別の任意導入の経路として引き続き使えます。
* **一部のエンジンを「助言的、非ブロッキング」と印付ける。** 妥当な改良です。既知の WebKit の欠落を、CI を落とさずに追跡できます。一方で、判定ポリシーの表面が増えます。v1 は単純な「全エンジン通過」規則を保ち、エンジンごとのブロッキング方針は後続に委ねます。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

* [CLAUDE.md](../../CLAUDE.md)、[DESIGN.md](../../DESIGN.md)：本提案が守る prime directive。AI は判定しない（マトリクスは決定論的判定の集計）、決定論優先（エンジンごとの条件待ち run）、アプリ非依存（エンジンは実行軸でありシナリオの内容ではない）。
* [BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)：本項目が拡張する backend と、その「クロスブラウザ」という seam の約束。
* [BE-0054 — Web backend completion](../BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md)：このファンアウトが再利用する並列レーンのプール。スコープ（単一エンジンの rich 機能）としては別物。
* [BE-0021 — AI triage](../BE-0021-ai-triage/BE-0021-ai-triage-ja.md)：エンジン固有の失敗の助言的調査。
* [BE-0029 — Visual-regression assertions](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions-ja.md)、[BE-0062 — Playwright codegen](../BE-0062-playwright-codegen/BE-0062-playwright-codegen-ja.md)：隣接する範囲外の後続。
* 本項目が変える seam：`bajutsu/drivers/playwright.py`（`_start_chromium`、`Starter` 型、`PlaywrightDriver.__init__`）、`bajutsu/backends.py`（`make_driver`、`ensure_web_runtime`、`capabilities_for`）、`bajutsu/runner/launch.py`（`launch_driver`）、`bajutsu/runner/pool.py`（web レーンの分岐と `_resolve_lanes` の `web-{i}` レーン）、`bajutsu/config.py`（`browser` フィールドが倣う雛形である `TargetConfig.headless` → `Effective.headless` → `resolve`）、`bajutsu/cli/commands/run.py` / `record.py`（`--browser` フラグが倣う `--headed` の上書き）、`bajutsu/report/manifest.py`（`manifest_dict`、`junit_xml`）、`.github/workflows/web-e2e.yml`。[drivers.md](../../docs/drivers.md)、[multi-platform.md](../../docs/multi-platform.md)。

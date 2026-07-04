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
| トピック | プラットフォーム拡張（Android / Web / Flutter） |
<!-- /BE-METADATA -->

## はじめに

Web（Playwright）backend に**ブラウザエンジンの軸**を加えます。段階は二つあります。

1. **エンジンの選択**：`--browser chromium|firefox|webkit` フラグ（および設定での既定値）を設け、
   `record` と `run` を Chromium 固定ではなく選んだエンジンで駆動できるようにします。
2. **クロスブラウザマトリクス**：`--browsers chromium,firefox,webkit` のように複数エンジンを指定すると、
   同じシナリオを各エンジンで実行し、**エンジン × シナリオの pass/fail マトリクス**を決定論的に出力します。
   あるエンジンだけ落ちて他は通ったセルは、レンダリングエンジンや仕様の差による非互換として機械的に検出されます。

ブラウザエンジンは `--workers` や端末選択と同じ**実行軸**であって、シナリオの内容ではありません。したがって
シナリオはエンジン非依存、プラットフォーム非依存のままです。ゲートに LLM は入りません。エンジンごとの結果は
既存の決定論的 `run` の判定そのものであり、マトリクスはその判定を集計するだけだからです。本提案は
prime directive（[CLAUDE.md](../../../CLAUDE.md)）の内側に完全に収まります。

## 動機

### 現状の backend は Chromium 固定である

[BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md) で着地した Web backend は、
単一エンジンに固定されています。`bajutsu/drivers/playwright.py` はブラウザを `_start_chromium` 経由で起動し、
その中で `pw.chromium.launch(...)` を呼びます。`bajutsu/backends.py` の `ensure_web_runtime` も `chromium` だけを
インストールします。BE-0041 の seam 表は「headless で**クロスブラウザ**」なアクチュエータを掲げていたが、出荷した
v1 スライスが届くのは Chromium（Blink）までです。エンジンを利用者が選べる口はどこにも開いていません。

### エンジン固有の不具合こそ E2E が扱うべき対象である

Playwright は独立した三つのレンダリングエンジンを同梱します。Chromium（Blink）、Firefox（Gecko）、
WebKit（Safari の中核エンジン）です。そして**三つとも Linux 上で headless 実行できます**。既存の
`make check` / CI ゲートの内側で動き、Mac も追加のインフラも要りません。これらが露わにする不具合は、単一エンジンの
テストでは原理的に見えないものです。Gecko だけが従う CSS 機能やレイアウトの癖、WebKit に欠けるか挙動の異なる
JavaScript / DOM API、描画の食い違う日付、数値入力、flexbox や grid の境界条件です。「Chrome では動くが Safari で
壊れる」は本番障害の典型であり、Chromium しか駆動しない E2E ツールはその種の失敗に対して構造的に盲目です。
この種の失敗を検出することはまさに E2E ツールの役目であり、代替エンジンがデバイスファームではなくダウンロードで
済む以上、ここではそれがほぼ無償で手に入ります。

### prime directive に素直に収まる

この検出は **AI の判断ではなく機械的に検証できる**事実です。「シナリオが Chromium で通り WebKit で落ちる」は
二つの決定論的 `run` 判定であり、それぞれが従来どおり機械的アサーションだけから計算されます。マトリクスはその判定の
表に過ぎず、ゲートはモデルを一切参照しません。したがって prime directive #1（AI は判定しない）は構成上保たれます。
AI の役割は従来どおり助言的なものに留まります。`triage`（[BE-0021](../BE-0021-ai-triage/BE-0021-ai-triage-ja.md)）は
「なぜ WebKit だけ落ちたか」を*調査*できるが、pass/fail を決めることはありません。決定論（#2）も保たれます。エンジンごとの
実行は固定 sleep ではなく条件待ちによる独立した決定論的 run だからです。そしてエンジンはフラグや設定が運ぶ実行軸で
あってシナリオに書き込まれないため、アプリ非依存（#3）も保たれます。同じシナリオ YAML がどのエンジンでも無変更で動きます。

## 詳細設計

### 第1段階（エンジンの選択）

**軸。** `run` と `record` に `--browser <engine>` を加えます。`<engine>` は `chromium`（既定で、現在の挙動を維持します）か
`firefox`、`webkit` のいずれかです。設定側の既定値も設け、ターゲットがフラグなしでエンジンを固定できるようにします。
具体的には `targets.<name>.browser` キーを `config.py` が `Effective` に解決し、既定は `chromium` とします。優先順位は
既存の `headless` / `--headed` の仕組みと同じで、フラグが設定を上書きし、設定が組み込み既定を上書きします。

`config.py` には、この設計の雛形になる web 専用フィールドがすでにあります。`TargetConfig.headless`（`bool = True`）が
`Effective.headless` に解決され、`--headed/--no-headed` が与えられたときは `run` が `replace(eff, headless=not headed)` で
上書きします。`browser` 軸も同じ三つの部品を同じ形で足します。

1. **設定フィールド。** `TargetConfig.browser: str = "chromium"`（web 専用で、iOS は無視します）。`field_validator` で
   `{"chromium", "firefox", "webkit"}` のいずれかに検証し、綴り間違いを読み込み時に落とします。これは
   `Defaults._valid_idb_version` と同じ「読み込み時に大きく失敗させる」流儀であって、実行中に落ちる形ではありません。
2. **`Effective` への解決。** 凍結データクラス `Effective` に `browser: str = "chromium"` フィールドを足し、`resolve()` で
   `a.browser` からそのまま埋めます（`headless` と同じくターゲット単位の値であって、`defaults` とマージする値ではありません）。
3. **フラグの上書き。** `run` / `record` の `--browser <engine>` オプションが指定されたとき `eff = replace(eff, browser=<engine>)`
   を適用します。これは現在の `--headed` の上書きとまったく同じ形であり、同じ三エンジンの集合に検証して、未知の値は Playwright に
   届く前に終了コード 2 で弾きます。

**唯一の seam に通す。** エンジン選択が触れるのは web backend の構築経路だけです。決定論的コア（`base.resolve_unique` /
`find_all`、`query()` のスナップショット、orchestrator）には手を入れません。現状ではエンジンは三層下で固定されています。
`PlaywrightDriver.__init__` が `starter: Starter = _start_chromium` を既定とし、`Starter = Callable[[bool], _Started]` で、
`_start_chromium(headless)` が `pw.chromium.launch(...)` を呼びます。変更はこの一つのクロージャをエンジン名で一般化するだけです。

| Seam | 現状 | 変更 |
|---|---|---|
| `bajutsu/drivers/playwright.py` | `_start_chromium(headless)` → `pw.chromium.launch(headless=…, slow_mo=…)`。`PlaywrightDriver(…, starter=_start_chromium)` | `getattr(pw, engine)`（`pw.chromium` / `pw.firefox` / `pw.webkit`）を起動する `Starter` を返す `_start_browser(engine)` ファクトリを設ける。`PlaywrightDriver` が `browser: str = "chromium"` 引数を取り、そこから starter を組み立てるので、`relaunch()`（`self._starter` を呼び直す）も同じエンジンを建て直す |
| `bajutsu/backends.py` `make_driver` | `make_driver(actuator, udid, *, base_url, headless, record_video_dir)` → `PlaywrightDriver(base_url, headless=headless, …)` | `browser: str = "chromium"` キーワードを足して `PlaywrightDriver(base_url, headless=headless, browser=browser, …)` と渡す |
| `bajutsu/runner/launch.py` `launch_driver` | `make_driver(actuator, udid, base_url=eff.base_url, headless=eff.headless, …)` を呼ぶ | `browser=eff.browser` も渡す（run 向けに web ドライバを建てる唯一の呼び出し箇所。`doctor._current_screen` は `PlaywrightDriver` を直に組み立てるので、同じく `browser=eff.browser` を加える） |
| `bajutsu/backends.py` `ensure_web_runtime` | Playwright が import 可能なら no-op。web が要求され*かつ* Playwright が無いときだけ `uv pip install playwright` ＋ `playwright install chromium` を実行 | Playwright を導入するとき（および後続のエンジン導入ステップとして）、要求されたエンジンを導入する。単一なら `playwright install <engine>`、マトリクスなら `playwright install firefox webkit`（と chromium）。詳細は後述 |
| `.github/workflows/web-e2e.yml` / docs | `playwright install --with-deps chromium` | クロスエンジンのジョブで `firefox webkit` も導入する。Playwright ブラウザのキャッシュキー（すでに `hashFiles('uv.lock')`）は変えない |

`record` は選ばれたエンジンを駆動します。同じ `Driver` インターフェースに対する AI オーサリングなので、`eff.browser` を
`launch_driver` に通すだけが変更点であり、記録される YAML はエンジン非依存のままです。セレクタの対応付け（`_ROLE_MAP`）、
`QUERY_JS` による `query()` の DOM 走査、コアを通した actuation、健全性シグナル（`pageerror` とメインフレームのステータスと
`dialog`）、`capabilities()` はいずれもエンジン非依存で現状のままです。`QUERY_JS` は標準 DOM であり、`getBoundingClientRect`
の座標計算も三つのエンジンで同一に動きます。

**オンデマンドの導入。** 現状の `ensure_web_runtime(backends)` は、web backend が要求され*かつ* Playwright パッケージが無いとき
以外は no-op です。web が要求されていないとき、または `_playwright_available()`（import の有無を見るプローブ）がすでに真のときは
早期 return し、無いときに限って `uv pip install playwright` のあと `playwright install chromium` を実行します。このパッケージの
プローブは*どのブラウザバイナリが存在するか*を区別しないため、firefox / webkit の導入は単一のパッケージプローブではなくエンジン
ごとの確認を要します。設計はこうです。パッケージを
確保したあと、この run が必要とするエンジン（解決した `eff.browser`、または `--browsers` のリスト）について `playwright install
<engines>` を走らせます。`playwright install` は**冪等**であり（web-e2e ワークフローのコメントもすでに「古いブラウザは単に再取得される」と
この性質に依拠しています）、要求したエンジンに対して無条件に呼んでも安全です。欠けているバイナリは取得され、存在するものは高速な no-op に
なります。これにより自動プロビジョニングの約束（`make serve` が idb を足し、web run が Playwright を足す）を保ったまま、選んだエンジンへ
対象を広げます。

**`doctor` の報告。** `doctor` はすでに `preflight.runnability(actuator, …)` で実行可能性を判定し、修正できるチェックリストを出します。
web アクチュエータについては**どのエンジンが導入済みか**を報告すべきです（エンジンごとの存在確認。たとえば `playwright install --dry-run`
の出力やブラウザレジストリのパスを調べる）。そうすれば「`webkit` を要求したが `chromium` しか入っていない」が一行の直し方とともにここで
表面化し、下流の分かりにくい起動失敗にはなりません。iOS で idb バージョンチェックが担うのと同じ役割です。

**実装状況。** 第 1 段階（エンジンの選択）は出荷済みです。`browser` の config フィールドとその読み込み時の検証、
`run` / `record` の `--browser` フラグ（フラグ > config > 既定の優先順位）、`PlaywrightDriver` / `make_driver` /
web environment / `doctor` を貫くエンジンの引き回し、そして `ensure_web_runtime` での実行時 `playwright install <engine>` を、
いずれも fake starter を使った高速な `make check` ゲート（実ブラウザなし）で確認しています。実機での firefox / webkit の起動は
web-e2e の経路に委ねます。**第 2 段階（`--browsers` マトリクス）は出荷済みです。** `run` の `--browsers` フラグ、
エンジンごとに 1 パスを回すファンアウト（`run_matrix_and_report`。証跡を `run_dir/<engine>/<sid>` の下に書く）、
エンジンでタグ付けする `RunResult.engine`、manifest の `matrix` ブロックと all-must-pass の `ok`、エンジンを織り込んだ
JUnit の `classname="bajutsu.<engine>"`、そして `report.html` のエンジン × シナリオのグリッドを、いずれも fake な lease を使った
高速ゲートで確認しています。実機でのクロスエンジン実行は web-e2e の経路に残します。

### 第2段階（クロスブラウザマトリクス）

**ファンアウト。** `run` に `--browsers <list>`（例: `--browsers chromium,firefox,webkit`）を加え、選ばれた各シナリオを
列挙したエンジンごとに一度ずつ実行します。`--browsers chromium` は `--browser chromium` と同義であり、二つのオプションは
一つの軸の単一エンジン版と複数エンジン版の綴り違いに過ぎません。単一エンジンの run はマトリクスの仕掛けを一切負担しません。

**ファンアウトを run パイプラインへ写す（エンジンごとに逐次）。** run は、複数エンジンを一つのデバイスプールに混ぜるのではなく、
**エンジンを巡るループとして構成し、各エンジンを `run_and_report` 相当の完全な一巡**とします。理由は具体的です。`device_pool` は
アクチュエータをちょうど一つ選び（`select_actuator(backends)`）、一種類のレーンを建てます。そして `_resolve_lanes` はすでに
`--workers N` を*一つのエンジンに対する* N 本のほぼ無償な `web-{i}` `BrowserContext` レーンへ変換します。
[BE-0054](../BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md) の並列レーンをエンジンの**内側**で
再利用し（`--workers` が引き続きシナリオを並列化します）、エンジンの巡回をそのプールの**外側**に置けば、各エンジンのプールは同質に
保たれ、エンジンごとの区別を `device_pool` / `launch_driver` やコレクタの配線へ通す必要がなくなります。各エンジンの一巡は自分の
プールをリースし、選ばれたシナリオを既存の `run_all` で実行し、エンジンごとの結果リストとエビデンスツリーを生みます。マトリクスは
それらの一巡の組み立てです。（将来はエンジンを並行実行する最適化もあり得ます。互いに独立したプロセスだからです。それでも v1 を逐次と
するのは、既存の単一エンジン経路を無変更で再利用でき、エビデンスのディレクトリが自明に衝突しないからです。）

**エビデンスの配置（衝突なし）。** 現状の `run_all` は各シナリオの成果物を `run_dir/<sid>` に書きます（`sid = f"{i:02d}-{scenario_slug(s.name)}"`）。
マトリクスはこれにエンジンを前置し、`run_dir/<engine>/<sid>`（例: `chromium/00-login`、`webkit/00-login`）とします。これにより
二つのエンジンで同じシナリオを動かしても `network.json` やスクリーンショット、動画を互いに上書きしません。各エンジンの一巡には
固有の `run_dir` サブツリーを渡します。

**成果物（マニフェスト内のエンジン × シナリオのマトリクス）。** `manifest.json` は run の単一の真実です（`manifest_dict` →
`"scenarios": [asdict(r) for r in results]`、`"ok": all(r.ok …)`）。マトリクスは v1 の形を壊さずにこれを拡張します。各
`RunResult` はすでに `backend` フィールドを持つので、自然な表現はエンジンで印付けた**結果のフラットなリスト一つ**に集計のマトリクス
ビューを添える形です。具体的にはこうです。

- エンジンごとの `RunResult` が自身のエンジンを記録します。現状 web の結果はどれも `RunResult.backend` が `"playwright"` なので、
  そこへ多重に意味を持たせず、明示的な `engine: str = ""` フィールド（iOS や単一エンジンでは空）を足して一巡ごとに埋めます。
  `backend` はアクチュエータ、`engine` はレンダリングエンジン、と意味を分けて保ちます。
- マニフェストにトップレベルの `"matrix"` ブロックを足します。`{ engines: ["chromium","firefox","webkit"], scenarios:
  [<name>…], cells: { "<scenario>": { "<engine>": {ok, sid, failure} } } }` という形で、これは `scenarios` にすでにある
  エンジンごとの判定の純粋な集計です。`report.html` はこれをエンジン × シナリオの格子として描き、Chromium と Firefox では緑だが
  WebKit では赤、という行が、本アイテムが見つけようとする機械検出された非互換になります。
- JUnit（`junit_xml`）は現状シナリオごとに `<testcase classname="bajutsu">` を一つ出します。マトリクスはエンジンをケースに
  織り込み、`classname="bajutsu.<engine>"`（あるいはエンジンごとの `<testsuite>`）とします。これにより CI は `chromium.login` と
  `webkit.login` を別ケースとして見られ、マニフェストを読まなくてもエンジンごとの失敗を CI の画面で帰属できます。

**判定の意味（機械のみ、全エンジン通過）。** `--browsers` の run は、要求した**すべての**エンジンが選択した全シナリオを通したときに
のみ緑とします（`manifest["ok"] = all(r.ok for r in 全エンジンの結果)`）。エンジン固有の失敗が一つでもあれば run は失敗します。
各セルの `ok` は既存の決定論的 `run` の判定であり、機械的アサーションのみで、条件待ちであり、固定 sleep はなく、**LLM もありません**。
マトリクスはその真偽値の純粋な集計なので、prime directive #1 は構成上保たれます。ゲートはモデルを一切参照しません。（既知の WebKit の
欠落を CI を落とさずに追跡するために、あるエンジンを「助言的で非ブロッキング」と印付けできるようにするかどうかは改良の余地であり、
Alternatives に記します。v1 では扱いません。）

### 検証計画

作業は、プロジェクトがすでに回している二つのゲート（CLAUDE.md の「ゲート」）に素直に分かれます。

**高速な `make check` ゲート（ブラウザなし、Linux、被覆の大半）。** 実際のブラウザ起動を除けば、すべてがブラウザ非依存で、
フェイクによりユニットテストできます。`parse_dom` や注入した `_Page` を Playwright なしで駆動する既存の web テストと同じ要領です。

- *エンジンの解決と優先順位。* `resolve()` が既定で `Effective.browser == "chromium"` を返すこと、`targets.<name>.browser` を
  尊重すること、`--browser` の上書きが設定に勝つことをアサートします。`headless` / `--headed` の優先順位と同じ三段階のテストです。
- *検証。* 不正な `browser` 値が設定読み込みで落ち（`field_validator`）、不正な `--browser` フラグが終了コード 2 になることを、
  いずれもブラウザに触れずに確かめます。
- *受け渡し。* フェイクの `Starter`（既存のテスト用 seam である `PlaywrightDriver(starter=…)`）が要求されたエンジンを記録し、
  `make_driver` / `launch_driver` が `eff.browser` を端から端まで通すことを示します。
- *マトリクスの組み立て。* マニフェストと JUnit のビルダーに合成のエンジンごと `RunResult`（同じシナリオで緑の Chromium と赤の
  WebKit）を与え、`"matrix"` ブロックと集計の `ok`、エンジンごとのエビデンス前置、エンジンで分けた JUnit ケースをアサートします。
  いずれもデータからで、ブラウザは不要です。「あるエンジンで赤、別のエンジンで緑が表面化する」契約を決定論的に固定するのがここです。

**web-e2e 経路（実ブラウザ、Linux、重め、`make check` の外）。** `web-e2e.yml` はすでに実機の headless Chromium を `demos/web` に
対して駆動します。これに、`firefox webkit` を導入し、代表的な `demos/web` のシナリオを各エンジンで実行してエンジンごとの判定を
アサートするクロスエンジンのジョブを足します。さらに、エンジン間で挙動が分かれる振る舞いにわざと依存するフィクスチャを一つ加え、
マトリクスが本当にエンジン固有の失敗を（一つで赤、他で緑として）報告し黙って通り抜けないことを端から端まで示します。これは現在の
web-e2e と同様にパスフィルタで起動され、必須チェックではないので、高速ゲートを遅くせずに実機のクロスエンジン信号を加えます。

### 決定論、アプリ非依存、ゲート

* **LLM なし。判定にモデルは触れない。** エンジンごとの結果は既存の決定論的 `run` であり、マトリクスは判定を集計します。
  prime directive #1 と #2 は構成上保たれます。
* **アプリ非依存。** エンジンは実行軸（フラグ、設定）であってシナリオの内容ではありません。同じ YAML がどのエンジンでも動くため、
  prime directive #3 が保たれます。
* **Linux で検証でき、既存ゲートの内側で回る。** 三つのエンジンはいずれも Linux 上で headless 実行できるので、クロスエンジン
  経路は現状の Chromium 経路と同じ `make check` / web-e2e の CI ジョブで動かせます。Mac もエミュレータも要りません。実費はクロス
  エンジンのジョブで Firefox と WebKit のブラウザビルドを CI が取得しキャッシュする一点だけです（Chromium のみの実行には影響しません）。

### 既存アイテムとの関係

* **[BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md) の上に積む**（web backend）。
  ファンアウトには **[BE-0054](../BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md) の並列レーンを
  再利用する**。本アイテムは BE-0054 とは*別物*です。BE-0054 のスコープは単一エンジン上の rich-end な機能（ネイティブ
  ネットワーク、video / console エビデンス、エミュレートした multi-touch、並列実行）と明記されており、エンジン選択や
  クロスエンジンのマトリクスは扱いません。本アイテムはその上にエンジン軸を加えます。
* **[BE-0021](../BE-0021-ai-triage/BE-0021-ai-triage-ja.md)（AI triage）** が素直に拡張されます。一つの
  エンジンだけで見られた失敗は、助言的な原因調査にとって強く構造化された手がかりになります。それでも判定にはなりません。
* **[BE-0062](../BE-0062-playwright-codegen/BE-0062-playwright-codegen-ja.md)（Playwright codegen）** は将来、
  エンジンごとのプロジェクト設定を出力できるかもしれないが、本アイテムの範囲外です。

## 検討した代替案

* **エンジンをシナリオ YAML に入れる。** 却下します。シナリオがエンジンに縛られ、プラットフォーム / エンジン非依存という
  シナリオモデルが壊れます。同じ YAML が iOS でも Chromium / Firefox / WebKit でも無変更で動かなければなりません。エンジンは
  `--workers` や端末選択と同じく実行軸に属し、テスト対象の成果物には属しません。
* **エンジンを backend トークンに埋める（`--backend web:firefox`）。** 主たる表面としては却下します。registry は*プラット
  フォーム*トークンを*アクチュエータ*へ展開する（`web` → `playwright`）が、レンダリングエンジンはプラットフォームでも別の
  アクチュエータでもなく、唯一の Playwright アクチュエータへのパラメータです。専用の `--browser` フラグの方が明快で、
  Playwright 自身の CLI とも揃い、registry のプラットフォーム→アクチュエータという意味を保てます。`web:firefox` という綴りは
  需要があれば後から糖衣として加えればよいです。
* **Chromium 固定のまま手作業のクロスブラウザ確認に頼る。** 却下します。それこそ本アイテムが埋める穴です。エンジン固有の
  不具合は E2E ツールが捕らえるべきものであり、Playwright は代替エンジンを既存の Linux ゲート上でほぼ無償にします。手作業に
  留めることは、手に入る最も安価なクロスエンジンの被覆を放棄することになります。
* **クロスエンジンの*視覚*的一致（エンジン間でスクリーンショットをピクセル比較）。** v1 では意図的に対象外とします。レンダ
  リングはエンジン間で正当に異なる（フォントのヒンティング、サブピクセルのレイアウト）ため、クロスエンジンのピクセル差分は
  ノイズが多く、「同じに見えるか」という清潔でない機械判定へ漂流する危険があります。本アイテムが扱うのはエンジンごとの**機能的**な
  pass/fail です。既存の視覚回帰アサーション（[BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions-ja.md)）
  によるエンジンごとの基準画像は、別の任意導入の経路として引き続き利用できます。
* **一部のエンジンを「助言的、非ブロッキング」と印付ける。** 妥当な改良です（既知の WebKit の欠落を CI を落とさずに追跡する）
  が、判定ポリシーの表面が増えます。v1 は単純な「全エンジン通過」規則を保ち、エンジンごとのブロッキング方針は後続に委ねます。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

* [CLAUDE.md](../../../CLAUDE.md)、[DESIGN.md](../../../DESIGN.md) — 本提案が守る prime directive。AI は判定しない
  （マトリクスは決定論的判定の集計）、決定論優先（エンジンごとの条件待ち run）、アプリ非依存（エンジンは実行軸でありシナリオの
  内容ではない）。
* [BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md) — 本アイテムが
  拡張する backend と、その「クロスブラウザ」という seam の約束。
* [BE-0054 — Web backend completion](../BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md) — このファンアウトが
  再利用する並列レーンのプール。スコープ（単一エンジンの rich 機能）としては別物。
* [BE-0021 — AI triage](../BE-0021-ai-triage/BE-0021-ai-triage-ja.md) — エンジン固有の失敗の助言的調査。
* [BE-0029 — Visual-regression assertions](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions-ja.md)、
  [BE-0062 — Playwright codegen](../BE-0062-playwright-codegen/BE-0062-playwright-codegen-ja.md) — 隣接する範囲外の後続。
* 本アイテムが変える seam。`bajutsu/drivers/playwright.py`（`_start_chromium`、`Starter` 型、`PlaywrightDriver.__init__`）、
  `bajutsu/backends.py`（`make_driver`、`ensure_web_runtime`、`capabilities_for`）、`bajutsu/runner/launch.py`（`launch_driver`）、
  `bajutsu/runner/pool.py`（web レーンの分岐と `_resolve_lanes` の `web-{i}` レーン）、`bajutsu/config.py`
  （`browser` フィールドが倣う雛形である `TargetConfig.headless` → `Effective.headless` → `resolve`）、
  `bajutsu/cli/commands/run.py` / `record.py`（`--browser` フラグが倣う `--headed` の上書き）、
  `bajutsu/report/manifest.py`（`manifest_dict`、`junit_xml`）、`.github/workflows/web-e2e.yml`。
  [drivers.md](../../../docs/drivers.md)、[multi-platform.md](../../../docs/multi-platform.md)。

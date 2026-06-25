[English](BE-0076-web-cross-browser-engines.md) · **日本語**

# BE-0076 — ブラウザエンジンの選択とクロスブラウザ互換マトリクス（web backend）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0076](BE-0076-web-cross-browser-engines-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トピック | プラットフォーム拡張（Android / Web / Flutter） |
<!-- /BE-METADATA -->

## はじめに

Web（Playwright）backend に**ブラウザエンジンの軸**を加えます。段階は二つあります。

1. **エンジンの選択** — `--browser chromium|firefox|webkit` フラグ（および設定での既定値）を設け、
   `record` と `run` を Chromium 固定ではなく選んだエンジンで駆動できるようにします。
2. **クロスブラウザマトリクス** — `--browsers chromium,firefox,webkit` のように複数エンジンを指定すると、
   同じシナリオを各エンジンで実行し、**エンジン × シナリオの pass/fail マトリクス**を決定論的に出力します。
   あるエンジンだけ落ちて他は通ったセルは、レンダリングエンジンや仕様の差による非互換として機械的に検出されます。

ブラウザエンジンは `--workers` や端末選択と同じ**実行軸**であって、シナリオの内容ではありません。したがって
シナリオはエンジン非依存・プラットフォーム非依存のままです。ゲートに LLM は入りません。エンジンごとの結果は
既存の決定論的 `run` の判定そのものであり、マトリクスはその判定を集計するだけだからです。本提案は
prime directive（[CLAUDE.md](../../../CLAUDE.md)）の内側に完全に収まります。

## 動機

### 現状の backend は Chromium 固定である

[BE-0041](../../in-progress/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md) で着地した Web backend は、
単一エンジンに固定されています。`bajutsu/drivers/playwright.py` はブラウザを `_start_chromium` 経由で起動し、
その中で `pw.chromium.launch(...)` を呼びます。`bajutsu/backends.py` の `ensure_web_runtime` も `chromium` だけを
インストールします。BE-0041 の seam 表は「headless で**クロスブラウザ**」なアクチュエータを掲げていたが、出荷した
v1 スライスが届くのは Chromium（Blink）までです。エンジンを利用者が選べる口はどこにも開いていません。

### エンジン固有の不具合こそ E2E が扱うべき対象である

Playwright は独立した三つのレンダリングエンジンを同梱します。Chromium（Blink）、Firefox（Gecko）、
WebKit（Safari の中核エンジン）です。そして**三つとも Linux 上で headless 実行できます**。既存の
`make check` / CI ゲートの内側で動き、Mac も追加のインフラも要りません。これらが露わにする不具合は、単一エンジンの
テストでは原理的に見えないものです。Gecko だけが従う CSS 機能やレイアウトの癖、WebKit に欠けるか挙動の異なる
JavaScript / DOM API、描画の食い違う日付・数値入力、flexbox や grid の境界条件——「Chrome では動くが Safari で
壊れる」は本番障害の典型であり、Chromium しか駆動しない E2E ツールはその種の失敗に対して構造的に盲目です。
この種の失敗を検出することはまさに E2E ツールの役目であり、代替エンジンがデバイスファームではなくダウンロードで
済む以上、ここではそれがほぼ無償で手に入ります。

### prime directive に素直に収まる

この検出は **AI の判断ではなく機械的に検証できる**事実です。「シナリオが Chromium で通り WebKit で落ちる」は
二つの決定論的 `run` 判定であり、それぞれが従来どおり機械的アサーションだけから計算されます。マトリクスはその判定の
表に過ぎず、ゲートはモデルを一切参照しません。したがって prime directive #1（AI は判定しない）は構成上保たれます。
AI の役割は従来どおり助言的なものに留まります。`triage`（[BE-0021](../../implemented/BE-0021-ai-triage/BE-0021-ai-triage-ja.md)）は
「なぜ WebKit だけ落ちたか」を*調査*できるが、pass/fail を決めることはありません。決定論（#2）も保たれます。エンジンごとの
実行は固定 sleep ではなく条件待ちによる独立した決定論的 run だからです。そしてエンジンはフラグや設定が運ぶ実行軸で
あってシナリオに書き込まれないため、アプリ非依存（#3）も保たれます。同じシナリオ YAML がどのエンジンでも無変更で動きます。

## 詳細設計

### 第1段階 — エンジンの選択

**軸。** `run` と `record` に `--browser <engine>` を加えます。`<engine>` は `chromium`（既定。現在の挙動を維持）、
`firefox`、`webkit` のいずれかです。設定側の既定値も設け、ターゲットがフラグなしでエンジンを固定できるようにします
（例: `config.py` が解決する `apps.<name>.browser` キー。既定は `chromium`）。フラグが設定を上書きし、設定が組み込み
既定を上書きします。既存の web 系オプションと同じ優先順位です。

**唯一の seam に通す。** エンジン選択が触れるのは web backend の構築経路だけであり、決定論的コアには手を入れません。

| Seam | 現状 | 変更 |
|---|---|---|
| `bajutsu/drivers/playwright.py` | `_start_chromium(headless)` → `pw.chromium.launch(...)` | `_start_browser(engine, headless)` が `pw.chromium` / `pw.firefox` / `pw.webkit` を選ぶ。`PlaywrightDriver` が `browser` 引数を取る |
| `bajutsu/backends.py` `make_driver` | `PlaywrightDriver(base_url, headless=headless)` | 解決した `browser` も渡す |
| `bajutsu/backends.py` `ensure_web_runtime` | `playwright install chromium` | 要求されたエンジンを必要に応じて導入（`chromium` / `firefox` / `webkit`） |
| `pyproject.toml` / CI | Chromium のみ | クロスエンジンのジョブ向けに firefox + webkit も任意で導入 |

`record` は選ばれたエンジンを駆動します（ドライバに対する AI オーサリングであり、フラグを通す以外の作業はない）。
セレクタの対応付け、`query()` の DOM 走査、コアを通した actuation、`capabilities()` はいずれもエンジン非依存で現状の
ままでよいです。QUERY_JS のスナップショットは標準 DOM であり、三つのエンジンで同一に動きます。

### 第2段階 — クロスブラウザマトリクス

**ファンアウト。** `run` に `--browsers <list>`（例: `--browsers chromium,firefox,webkit`）を加え、選ばれた各シナリオを
列挙したエンジンごとに一度ずつ実行します。これは [BE-0054](../../in-progress/BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md)
の並列レーン機構を再利用します。`BrowserContext` はほぼ無償の「デバイス」なので、（シナリオ, エンジン）の各組はデバイス
プールの web 分岐における独立したレーンになります。`--browsers chromium` は `--browser chromium` と同義であり、二つの
オプションは一つの軸の単一エンジン版と複数エンジン版の綴り違いに過ぎません。

**成果物 — エンジン × シナリオのマトリクス。** レポート（`manifest.json` + `report.html`）にエンジン次元が加わります。
（シナリオ, エンジン）の各セルがその run の決定論的判定とエビデンスを持ちます（エビデンスはエンジンごとのサブディレクトリに
分けて格納し、成果物が衝突しないようにする）。マトリクス表示はエンジン固有の不具合を一目で読めるようにします。Chromium と
Firefox では緑だが WebKit では赤、という行が、本アイテムが見つけようとする機械検出された非互換です。JUnit 出力は結果を
エンジンで分ける（エンジンを suite / classname の軸にする）ので、CI からはエンジンごとのケースとして見えます。

**判定の意味。** `--browsers` の run は、要求した**すべての**エンジンが選択した全シナリオを通したときにのみ緑とします。
エンジン固有の失敗が一つでもあれば run は失敗します。これによりクロスブラウザ実行は助言的なレポートではなく本物の決定論的
ゲートになります。（既知の WebKit の欠落を CI を落とさずに追跡するために、あるエンジンを「助言的・非ブロッキング」と印付け
できるようにするかどうかは改良の余地であり、Alternatives に記します。v1 では扱いません。）

### 決定論・アプリ非依存・ゲート

* **LLM なし。判定にモデルは触れない。** エンジンごとの結果は既存の決定論的 `run` であり、マトリクスは判定を集計します。
  prime directive #1 と #2 は構成上保たれます。
* **アプリ非依存。** エンジンは実行軸（フラグ・設定）であってシナリオの内容ではありません。同じ YAML がどのエンジンでも動くため、
  prime directive #3 が保たれます。
* **Linux で検証でき、既存ゲートの内側で回る。** 三つのエンジンはいずれも Linux 上で headless 実行できるので、クロスエンジン
  経路は現状の Chromium 経路と同じ `make check` / web-e2e の CI ジョブで動かせます。Mac もエミュレータも要りません。実費はクロス
  エンジンのジョブで Firefox と WebKit のブラウザビルドを CI が取得・キャッシュする一点だけです（Chromium のみの実行には影響しません）。

### テストの契約（機械的に検証できる）

`demos/web` のシナリオはすでに CI 上で web backend により決定論的に動いています。本アイテムはその網を広げます。代表的なシナリオを
各エンジンで実行してエンジンごとの判定をアサートし、さらにエンジン間で挙動が分かれる振る舞いにわざと依存するフィクスチャを
追加して、マトリクスが実際にエンジン固有の失敗を（一つのエンジンで赤、他で緑として）報告し黙って通り抜けないことを示します。

### 既存アイテムとの関係

* **[BE-0041](../../in-progress/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md) の上に積む**（web backend）。
  ファンアウトには **[BE-0054](../../in-progress/BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md) の並列レーンを
  再利用する**。本アイテムは BE-0054 とは*別物*です。BE-0054 のスコープは単一エンジン上の rich-end な機能（ネイティブ
  ネットワーク、video / console エビデンス、エミュレートした multi-touch、並列実行）と明記されており、エンジン選択や
  クロスエンジンのマトリクスは扱いません。本アイテムはその上にエンジン軸を加えます。
* **[BE-0021](../../implemented/BE-0021-ai-triage/BE-0021-ai-triage-ja.md)（AI triage）** が素直に拡張されます。一つの
  エンジンだけで見られた失敗は、助言的な原因調査にとって強く構造化された手がかりになります。それでも判定にはなりません。
* **[BE-0062](../../implemented/BE-0062-playwright-codegen/BE-0062-playwright-codegen-ja.md)（Playwright codegen）** は将来、
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
  pass/fail です。既存の視覚回帰アサーション（[BE-0029](../../implemented/BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions-ja.md)）
  によるエンジンごとの基準画像は、別の任意導入の経路として引き続き利用できます。
* **一部のエンジンを「助言的・非ブロッキング」と印付ける。** 妥当な改良です（既知の WebKit の欠落を CI を落とさずに追跡する）
  が、判定ポリシーの表面が増えます。v1 は単純な「全エンジン通過」規則を保ち、エンジンごとのブロッキング方針は後続に委ねます。

## 参考

* [CLAUDE.md](../../../CLAUDE.md)、[DESIGN.md](../../../DESIGN.md) — 本提案が守る prime directive。AI は判定しない
  （マトリクスは決定論的判定の集計）、決定論優先（エンジンごとの条件待ち run）、アプリ非依存（エンジンは実行軸でありシナリオの
  内容ではない）。
* [BE-0041 — Web (Playwright) backend](../../in-progress/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md) — 本アイテムが
  拡張する backend と、その「クロスブラウザ」という seam の約束。
* [BE-0054 — Web backend completion](../../in-progress/BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md) — このファンアウトが
  再利用する並列レーンのプール。スコープ（単一エンジンの rich 機能）としては別物。
* [BE-0021 — AI triage](../../implemented/BE-0021-ai-triage/BE-0021-ai-triage-ja.md) — エンジン固有の失敗の助言的調査。
* [BE-0029 — Visual-regression assertions](../../implemented/BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions-ja.md)、
  [BE-0062 — Playwright codegen](../../implemented/BE-0062-playwright-codegen/BE-0062-playwright-codegen-ja.md) — 隣接する範囲外の後続。
* `bajutsu/drivers/playwright.py`（`_start_chromium`）、`bajutsu/backends.py`（`make_driver`、`ensure_web_runtime`）、
  `bajutsu/runner/pool.py`（web レーンの分岐） — 本アイテムが変える seam。[drivers.md](../../../docs/drivers.md)、
  [multi-platform.md](../../../docs/multi-platform.md)。

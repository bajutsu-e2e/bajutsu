# serve Web UI の Dogfood（Playwright backend）

[English](README.md)

Bajutsu が**自分自身**の `serve` Web UI をテストします。テスト対象は serve のシングルページアプリで、
**Playwright** backend（[BE-0041](../../roadmaps/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）で
駆動します。[demos/web](../web) と同じく **Mac も Simulator も不要**で、`make check` と同じツールチェーンの
Linux 上で動きます。Web UI の決定的なリグレッション網であり、合否は機械的なアサーションだけから決まり、LLM は
関与しません。

## 構成

| パス | 役割 |
|---|---|
| `dogfood.config.yaml` | `targets.webui`（`baseUrl`（起動中の serve）＋ `backend: [web]`、`bundleId` なし） |
| `scenarios/shell-navigation.yaml` | 上部タブ（Record / Replay / Crawl / Author / Stats / Coverage）が表示中のビューを入れ替える |
| `scenarios/theme.yaml` | テーマ切替が OS 設定に追従し、クリックで切り替わる |
| `scenarios/crawl-form.yaml` | Crawl フォームが bind したターゲットと既定の探索予算を持つ |
| `scenarios/crawl-history.yaml` | 過去の crawl run が読み取り専用で開き直し（past-crawl バッジ）、未探索のフロンティアが残る run は「続きを探索する」を出す。コミット済みの `fixtures/crawl-runs` の run で駆動する |
| `scenarios/modals.yaml` | config ブラウザと Settings パネルの開閉。AI provider は明示選択が必要（既定なし。選ぶまで Save は拒否される） |
| `scenarios/config-sources.yaml` | config モーダルが三つのバインド元（`--root` を一覧するファイルブラウザ、Git 指定、バンドルのアップロード）をすべて提示する |
| `scenarios/replay-contract.yaml` | 束ねた config が Replay のピッカーに届く（config → `/api/targets` → `/api/scenarios`） |
| `scenarios/replay-tabs.yaml` | Replay の Run / History タブが左パネルを入れ替える |
| `scenarios/replay-tools.yaml` | 選択中のシナリオに determinism grade が付き（BE-0145）、codegen が Playwright テストとして書き出し（BE-0137）、readiness パネル（BE-0148）が提示される |
| `scenarios/record-form.yaml` | Record の Save はシナリオが存在するまで disabled。goal フィールドは入力を受け取る |
| `scenarios/author-modes.yaml` | Author の Capture / Edit / Enrich モード切り替えが各モードのコントロールを出し分ける |
| `scenarios/author-editor.yaml` | Edit モードの Load が YAML エディタを埋めて grade を付ける。不正な YAML はインラインの problems パネルに出る（BE-0138） |
| `scenarios/stats.yaml` | Stats ビューが run のダッシュボードを読み込む（BE-0102） |
| `scenarios/coverage.yaml` | Compute がターゲットの coverage マップを描画する（BE-0146） |
| `scenarios/platform-ui.yaml` | 各パネルは選択中の target のプラットフォームに応じた操作だけを表示する。web target では iOS 用デバイス UI（simulators / workers / erase）ではなく headed トグルが出る |
| `scenarios/panel-resize.yaml` | タイル区切り一本のドラッグは隣接する二枚だけを再配分し、三枚目に触れない |
| `Makefile` | `web-deps` / `serve-ui` / `e2e` |

## 実行

```bash
make -C demos/serve-ui e2e
```

これは web backend（`uv sync --extra web` ＋ `playwright install chromium`）を入れ、`bajutsu serve` を起動し
（[web デモ](../web)の config を読ませ、ドロップダウンに実在のアプリとシナリオが並ぶようにします）、Tier A の
シナリオで Web UI を Playwright backend から駆動し、serve を畳みます。serve は `make serve` ではなく直接起動
します。dogfood は Web 限定で、XCUITest ランナーも iOS の actuator も関わらないからです。

Web UI を手で触るには、`make -C demos/serve-ui serve-ui` を実行して <http://127.0.0.1:8799/> を開きます。

CI は serve UI に触れる PR ごとにこれをゲートします。[`.github/workflows/web-e2e.yml`](../../.github/workflows/web-e2e.yml)
の `dogfood (serve UI)` ジョブが Linux 上で `make -C demos/serve-ui e2e` を走らせます（BE-0189）。

## コアとの対応

Web UI のコントロールは `data-testid` 属性を持ちます。iOS の accessibilityIdentifier に相当する Web 側の id で、
ビューごとに名前空間を切っています（`nav.*`、`view.*`、`record.*`、`replay.*`、`crawl.*`、`author.*`、
`stats.*`、`coverage.*`、`settings.*`、`config.*`、`upload.*`）。
シナリオの `{ id: nav.replay }` という selector は、ほかの backend と**同じ** `resolve_unique` / `find_all` の
決定性コアで解決します。

シナリオは、LLM も device もなしに決定性コアが確かめられることだけをアサートします。どの `<main>` ビューと
どのモーダルが存在するか（SPA は `hidden` 属性で切り替えるので、Playwright backend はアクティブなものだけを
見ます）、ボタンが enabled か disabled か、フィールドやピッカーが持つ値です。これにより、この dogfood は
**Tier 2** にとどまります。AI 駆動の Record と Crawl の run（モデルと device が要る）は、LLM を run/CI ゲート
から締め出すのと同じ規則で、ここでは対象外です。

二つの状態は、意図して間接的にアサートします。レポート系のペイン（Stats、Coverage、run のレポート）は
**shadow root** に描画され、要素クエリはその中を覗けません。そこで該当シナリオは、light DOM の
プレースホルダ（`stats.empty` / `coverage.empty`）がツリーから*消える*ことをアサートします。これは
「描画パスが走った」ことと正確に一致します。また Record の Generate ボタンは Claude への到達可能性
（BE-0101）でゲートされ、これはホスト側の状態なので、存在だけをアサートし、enabled かどうかは
アサートしません。

## 機能カバレッジマップ

機能の一覧は [docs/ja/web-ui.md](../../docs/ja/web-ui.md) です。serve Web UI のユーザーに見える挙動は
すべてそこに文書化されており、この表はその一つひとつを dogfood のシナリオに対応づけます。dogfood が
動かせない機能は理由つきで列挙します。黙って未カバーのものはありません。どの機能もサーバ側は
`tests/` の決定的な pytest（`make check` のゲート）が覆っており、AI 駆動のフローは**設計上**
（プライムディレクティブ 1）どの決定的ゲートからも除外されます。

| Web UI の機能（docs/ja/web-ui.md） | シナリオ |
|---|---|
| 上部タブが 6 ビューを切り替える | `shell-navigation.yaml` |
| テーマ切替（既定は OS 追従。往復して戻る） | `theme.yaml` |
| パネルのリサイズが隣接ペアに閉じる | `panel-resize.yaml` |
| config モーダルの開閉 | `modals.yaml` |
| config の 3 ソースの提示（`--root` を一覧するファイルブラウザ、Git 指定、アップロード枠） | `config-sources.yaml` |
| Settings：既定 provider なし、Save 拒否、選ぶまで provider 固有欄は非表示、model / effort の上書きの提示 | `modals.yaml` |
| Record フォーム：YAML ができるまで Save と ▶ Run は無効、goal は入力を受け取る | `record-form.yaml` |
| Readiness（doctor）パネルが Record と Replay にある | `replay-tools.yaml` |
| bind した config が Replay のピッカーを埋める | `replay-contract.yaml` |
| Replay の Run / History タブ、レポートペインの初期表示 | `replay-tabs.yaml` |
| 選択中シナリオの determinism audit バッジ | `replay-tools.yaml` |
| codegen の書き出し（Playwright emit の提示、コードとファイル名、閉じる） | `replay-tools.yaml` |
| プラットフォーム対応のコントロール（web では iOS デバイス UI を隠し headed を出す） | `platform-ui.yaml` |
| Crawl フォーム：bind したターゲット、既定の予算（1 / 50 / 200）、Start の提示 | `crawl-form.yaml` |
| Crawl History：過去の run を読み取り専用で開き直す（past-crawl バッジ）、plan ツリーが保存済みマップを描画する | `crawl-history.yaml` |
| Crawl の続きを探索する：未探索のフロンティアが残る過去の run が continue コントロールを出す | `crawl-history.yaml` |
| Author のモード切替が各モードのコントロールを出し分ける | `author-modes.yaml` |
| Author の Edit：Load が YAML を埋めて grade を付け、不正な YAML はインライン lint が指摘する | `author-editor.yaml` |
| Stats ダッシュボードの描画 | `stats.yaml` |
| Coverage マップの計算と描画 | `coverage.yaml` |

**dogfood が動かさないもの（理由つき）：**

- **AI 駆動のフロー**：Record の Generate、Crawl の Start と、その「続きを探索する」/ 枝刈りブランチの
  **resume**（どちらも crawl を起動します）、Enrich の提案、Claude での Triage。ゲートの合否経路に
  LLM を置かないという設計により、どの決定的な網からも除外します。`crawl-history.yaml` は continue
  コントロールが**提示される**ことをアサートし、クリックはしません。
- **過去の run を前提とするフロー**：埋め込みレポートの中身、visual の Approve、Triage
  （ルールベースでも）、Replay の History の項目、Coverage の run 合成。Replay の run 履歴はホスト側の
  状態なので、その中身のアサートはマシン依存になります。背後の operation は pytest が覆っています。
  唯一の例外は **Crawl** の History で、コミット済みの `fixtures/crawl-runs` の画面マップ（BE-0181）で
  駆動するので、読み取り専用の開き直しと continue コントロールを決定的にアサートします。
- **run 実行の往復**：Replay の Run と Record の ▶ Run は入れ子の `bajutsu run`
  （ブラウザテストの中のブラウザ）を起動します。BE-0058 で先送りにしています。
- **ネイティブ `<select>` の選択操作**：座標タップの web backend はネイティブのドロップダウンを
  開けません。「provider を選ぶ／ターゲットを変える」の先にあるもの（API キーの保存、Bedrock の
  欄、provider 固有のフロー）は、選択前の状態だけをアサートします。
- **ブラウザのネイティブ UI**：アップロードのファイル選択、codegen の Copy（クリップボード）と
  Download、トークンログインの `prompt()` ダイアログは、ページの DOM にありません。
- **ホスト依存の状態**：Generate / Start の enabled とゲートのバナー（Claude への到達可能性）、
  doctor のチェック*結果*（ホストのツールを調べます）。存在はアサートし、結果はアサートしません。
- **環境で形が変わるレイアウト**：狭幅のスタック表示（ドライバに viewport の制御がありません）と、
  パネルを移動する ⠿ グリップのドラッグ（多段のポインタ操作。タイルの*計算*は
  `panel-resize.yaml` が守っています）。
- **別 config の bind、キーの保存、サインイン**：ほかのシナリオ全部の足元にある共有のサーバ状態を
  書き換えるため、dogfood はダイアログの開閉だけを行います。書き換え自体は pytest が覆っています。

## スコープ（現状）

web backend は座標でタップするため、ネイティブの `<select>` ドロップダウンを操作できません。これらのシナリオは、
オプションを切り替えるのではなく、ページ読み込み時の**状態**（ドロップダウン操作を要しません）をアサート
します。`<select>` の駆動と AI の往復は、dogfood のロードマップ項目で今後の課題として追跡します。

# Web デモ（Playwright backend）

[English](README.md)

Bajutsu の **Playwright** backend（[BE-0041](../../roadmaps/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）で駆動する、小さな静的 web アプリです。iOS のデモと違い **Mac も Simulator も不要**で、`make check` と同じツールチェーンの Linux 上で動きます。

## 構成

| パス | 役割 |
|---|---|
| `app/index.html` | テスト対象アプリ。onboarding → login → counter、`/api/sync` へ POST する Sync ボタン（ネットワークレーンのリクエスト）、加えてハンドオフデモ用のデバイス検証フロー。素の JS、安定した `data-testid` の id |
| `scenarios/smoke.yaml` | 決定論的なスモークシナリオ（iOS デモと同じ step/expect スキーマ） |
| `scenarios/network.yaml` | ネットワークスモーク（[BE-0282](../../roadmaps/BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)）。モックされ、キャプチャされる `POST /api/sync` が秘密情報を運びます。`network` タグを付けてあるので、既定の `e2e`（`--no-network`）はこれを除外します |
| `network/assert_redaction.py` | 永続化された `network.json` が Sync リクエストの秘密情報をマスクしていることを確認します。run の文法ではアサートできない redaction のギャップを塞ぎます |
| `record/goals.txt` | `make -C demos/web record` が記述の起点にする自然言語ゴール |
| `record/record_offline.py` | `record` のオフライン版（API キー不要）。同じループを keyword agent と FakeDriver で回す |
| `record/record_handoff_offline.py` | human-in-the-loop ハンドオフデモのオフライン版（キー不要）。実際の一時停止・再開を、台本化したエージェントと応答者で回す |
| `demo.config.yaml` | `targets.web`（`baseUrl` ＋ `scenarios` ＋ `backend: [web]`、`bundleId` なし） |
| `codegen/smoke.spec.ts` | `scenarios/smoke.yaml` から**生成**してチェックインした Playwright テスト。codegen 実コンパイルゲートのフィクスチャ（[BE-0293](../../roadmaps/BE-0293-codegen-playwright-real-compile/BE-0293-codegen-playwright-real-compile-ja.md)）で、iOS 側の `ComponentsUITests.swift` の web 版です |
| `codegen/package.json`・`codegen/playwright.config.ts` | `@playwright/test` ランナー（Python の `web` extra と同じ Playwright バージョンにピン留め）とその設定。spec に焼き込まれたポートで `app/` を配信します |
| `Makefile` | `web-deps` / `app-serve` / `e2e` / `e2e-network` / `codegen-e2e` / `record` / `record-handoff` / `record-offline` / `record-handoff-offline` |

## 実行

```bash
make -C demos/web e2e
```

web backend をインストールし（`uv sync --extra web` ＋ `playwright install chromium`）、`app/` を `127.0.0.1:8787` で配信し、スモークシナリオを Playwright backend で実行して、サーバを後始末します。実行は完全に決定論的で、合否はシナリオの機械アサーション（カウンタが `2` を示す）だけから決まり、LLM は関与しません。

手で触る（または Web UI を向ける）には、`make -C demos/web app-serve` を実行し <http://127.0.0.1:8787/index.html> を開きます。

## ネットワークスモーク（BE-0282）

上の既定の `e2e` は `--no-network` で実行するので、実ネットワーク経路を一度も動かしません。ネットワークスモークはその逆で、page.route の介入、`requestfinished` のキャプチャ、`mocked` の来歴フラグ、そして実際にキャプチャした証拠の redaction を動かします。

```bash
make -C demos/web e2e-network
```

`app/` を配信し、**Sync account** ボタン（`Authorization` ヘッダと `password` ボディフィールドを運ぶ実際の `POST /api/sync`）をタップして、`network` タグの付いた [`scenarios/network.yaml`](scenarios/network.yaml) を **network を有効にして** 実行します。[`mocks:`](scenarios/network.yaml) のエントリがその POST に `201` で応答するので（実サーバがあれば `404` を返すはずです）、status `201` でキャプチャされた exchange は、ネットワークではなくモックが応答したことの証拠になります。シナリオの `request` アサーションが決定論的な介入・キャプチャの確認で、続いて [`network/assert_redaction.py`](network/assert_redaction.py) が永続化された `network.json` を読み、exchange が `mocked` で `201`、かつ両方の秘密情報がマスクされていなければ失敗します。どこにも LLM はありません。合否はそのアサーションとチェックだけです。

このレーンは [BE-0282](../../roadmaps/BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md) の web 側です。CI では `network (playwright)` ジョブとして走ります。まずシグナルとして着地させましたが、CI で安定を確認できたので、現在は必須の `E2E (web)` ゲートに昇格しています（BE-0282）。**Android にも対応物ができました**（[BE-0283](../../roadmaps/BE-0283-android-network-capture/BE-0283-android-network-capture-ja.md)）。`android-e2e.yml` の `network (adb)` が、BajutsuAndroid のアプリ側インターセプタが `adb reverse` 越しにホスト側コレクタへ報告する形で実エミュレータのトラフィックをキャプチャします。ここでの Playwright のブラウザ内介入とは transport が異なりますが、`request` アサーションによる判定は同じで、このジョブと同様にあちらでもゲートに入っています。

## codegen 実コンパイルゲート（BE-0293）

上の `e2e` は Bajutsu *自身* の Playwright backend を実行時に駆動するもので、codegen の出力には一切触れません。`bajutsu codegen --emit playwright` はこれとは別の経路で、シナリオを単体の `@playwright/test` ファイルへ変換します。チームが自分たちの Playwright CI で、Bajutsu ランタイムも AI も使わずに実行するためのものです。`tests/test_codegen_playwright.py` は出力ソースを文字列として検査しますが、確認できないのは codegen が本来主張していること、すなわち「生成されたファイルが実際に動く本物のネイティブテストである」という点です。このゲートがそのギャップを埋めます。

```bash
make -C demos/web codegen-e2e
```

[`scenarios/smoke.yaml`](scenarios/smoke.yaml) から [`codegen/smoke.spec.ts`](codegen/smoke.spec.ts) を再生成し、その出力を実際の `@playwright/test` ランナーで実 Chromium に対して実行します。ランナーは TypeScript をトランスパイルして実行するので、これは `tsc --noEmit` の構文チェックではなく、コンパイル*かつ*実行です。最後に、出力が**チェックイン済みのフィクスチャからドリフトしていれば失敗させます**（エミッタとフィクスチャが黙って乖離しないようにします）。必要なのは Node/npm だけで（ランナーは我々の Python backend ではなく行き先のフレームワークです）、Simulator も macOS も要りません。

これは iOS 側の `xcuitest (codegen)` ゲート（`demos/showcase` の `ui-test`。生成した `ComponentsUITests.swift` を `xcodebuild test` でビルドして実行します）の web 版です。CI では `web-e2e.yml` の `codegen (playwright)` ジョブになります。まず**シグナル**として着地させ（マージはブロックせず報告のみ）、`network (playwright)` がそうだったように、安定を確認してから必須の `E2E (web)` ゲートへ昇格させます（[BE-0293](../../roadmaps/BE-0293-codegen-playwright-real-compile/BE-0293-codegen-playwright-real-compile-ja.md)）。

## 記録（record）

record（Tier 1）は記述の経路です。AI が自然言語のゴールと現在の画面を読み、`run` があとで AI なしにリプレイする決定論的なシナリオを書き出します。これは `make -C demos record` の web 版で、変わるのは backend だけです（Simulator ではなくブラウザ）。

```bash
make -C demos/web record          # 実 Claude が Playwright backend を駆動（ANTHROPIC_API_KEY が必要）
make -C demos/web record GOAL="Get started, then increment the counter three times and confirm it shows 3."
```

`app/` を配信し、[`record/goals.txt`](record/goals.txt) のゴールに向けて実 Claude を走らせ、記述したシナリオを gitignore 対象の `tmp/` ファイルに書き出します。web アプリが安定した `data-testid` の id を公開しているので、Claude は `scenarios/smoke.yaml` と同じ id ベースのきれいなセレクタで記述します。API キーが要るのは web ではこの record 経路だけで、上の決定論的な `run`／`e2e` には要りません。

キーもブラウザも無い場合、オフライン版が同じ record ループを再現します。実際の `Observation → Proposal` プロトコルと出力シナリオはそのままに、各ステップをインメモリの FakeDriver 上の要素に結びつけるのが決定論的な keyword agent なので、`make check` のツールチェーンで動きます。

```bash
make -C demos/web record-offline                                   # 既定のゴール
uv run python demos/web/record/record_offline.py "get started, increment twice, check the counter shows 2"
```

## human-in-the-loop ハンドオフ（BE-0179）

一部のステップは、AI が供給できない何かで塞がれます。ここでは out-of-band で届くワンタイムの検証コードです。`record` はそのようなステップで一時停止し、人に引き渡し、応答を受け取り、実際の画面を観測し直して再開します（[BE-0179](../../roadmaps/BE-0179-record-human-handoff/BE-0179-record-human-handoff-ja.md)）。人がループに入るのはオーサリングの最中だけで、記録したシナリオは決定論的な `run` の経路に人を置かずに再生されます。

AI が一時停止したときにブラウザを操作できるよう、**headed** で実行します。

```bash
make -C demos/web record-handoff   # 実 Claude ＋ headed ブラウザ（ANTHROPIC_API_KEY が必要）
```

AI は **Verify a device** を押し、コード画面に着き、そのコードを知り得ないと判断して（`ask_human` のターン）一時停止します。ブラウザに表示されたコードを入力して **Verify** を押し、ターミナルのプロンプトに `done` と答えて再開してください。ループは検証済み画面を観測し直して完了します。

キーもブラウザも生身の人も無い場合、オフライン版が同じ一時停止・再開を再現します。実際のハンドオフ契約と record ループはそのままに、台本化したエージェントと応答者で回すので、`make check` のツールチェーンで動きます。

```bash
make -C demos/web record-handoff-offline
```

## コアとの対応

web アプリは `data-testid` 属性を公開します。これは iOS の accessibilityIdentifier の web 版です。シナリオの `{ id: counter.increment }` セレクタは、他のどの backend とも**同じ** `resolve_unique` / `find_all` の決定論コアで解決されます。Playwright driver が変えるのは、セレクタを満たす属性（`data-testid`）と、タップの送り方（確定した frame 中心を座標クリック）だけです。[drivers → Playwright](../../docs/ja/drivers.md#playwrightweb) を参照してください。

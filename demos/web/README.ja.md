# Web デモ（Playwright backend）

[English](README.md)

Bajutsu の **Playwright** backend（[BE-0041](../../roadmaps/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）で駆動する、小さな静的 web アプリです。iOS のデモと違い **Mac も Simulator も不要**で、`make check` と同じツールチェーンの Linux 上で動きます。

## 構成

| パス | 役割 |
|---|---|
| `app/index.html` | テスト対象アプリ。onboarding → login → counter、素の JS、安定した `data-testid` の id |
| `scenarios/smoke.yaml` | 決定論的なスモークシナリオ（iOS デモと同じ step/expect スキーマ） |
| `record/goals.txt` | `make -C demos/web record` が記述の起点にする自然言語ゴール |
| `record/record_offline.py` | `record` のオフライン版（API キー不要）。同じループを keyword agent と FakeDriver で回す |
| `demo.config.yaml` | `targets.web`（`baseUrl` ＋ `scenarios` ＋ `backend: [web]`、`bundleId` なし） |
| `Makefile` | `web-deps` / `app-serve` / `e2e` / `record` / `record-offline` |

## 実行

```bash
make -C demos/web e2e
```

web backend をインストールし（`uv sync --extra web` ＋ `playwright install chromium`）、`app/` を `127.0.0.1:8787` で配信し、スモークシナリオを Playwright backend で実行して、サーバを後始末します。実行は完全に決定論的で、合否はシナリオの機械アサーション（カウンタが `2` を示す）だけから決まり、LLM は関与しません。

手で触る（または Web UI を向ける）には、`make -C demos/web app-serve` を実行し <http://127.0.0.1:8787/index.html> を開きます。

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

## コアとの対応

web アプリは `data-testid` 属性を公開します。これは iOS の accessibilityIdentifier の web 版です。シナリオの `{ id: counter.increment }` セレクタは、他のどの backend とも**同じ** `resolve_unique` / `find_all` の決定論コアで解決されます。Playwright driver が変えるのは、セレクタを満たす属性（`data-testid`）と、タップの送り方（確定した frame 中心を座標クリック）だけです。[drivers → Playwright](../../docs/ja/drivers.md#playwrightweb) を参照してください。

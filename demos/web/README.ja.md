# Web デモ（Playwright backend）

[English](README.md)

Bajutsu の **Playwright** backend（[BE-0041](../../roadmaps/proposals/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend-ja.md)）で駆動する、小さな静的 web アプリです。iOS のデモと違い **Mac も Simulator も不要**で、`make check` と同じツールチェーンの Linux 上で動きます。

## 構成

| パス | 役割 |
|---|---|
| `app/index.html` | テスト対象アプリ。onboarding → login → counter、素の JS、安定した `data-testid` の id |
| `scenarios/smoke.yaml` | 決定論的なスモークシナリオ（iOS デモと同じ step/expect スキーマ） |
| `demo.config.yaml` | `apps.web`（`baseUrl` ＋ `scenarios` ＋ `backend: [web]`、`bundleId` なし） |
| `Makefile` | `web-deps` / `app-serve` / `e2e` |

## 実行

```bash
make -C demos/web e2e
```

web backend をインストールし（`uv sync --extra web` ＋ `playwright install chromium`）、`app/` を `127.0.0.1:8787` で配信し、スモークシナリオを Playwright backend で実行して、サーバを後始末します。実行は完全に決定論的で、合否はシナリオの機械アサーション（カウンタが `2` を示す）だけから決まり、LLM は関与しません。

手で触る（または Web UI を向ける）には、`make -C demos/web app-serve` を実行し <http://127.0.0.1:8787/index.html> を開きます。

## コアとの対応

web アプリは `data-testid` 属性を公開します。これは iOS の accessibilityIdentifier の web 版です。シナリオの `{ id: counter.increment }` セレクタは、他のどの backend とも**同じ** `resolve_unique` / `find_all` の決定論コアで解決されます。Playwright driver が変えるのは、セレクタを満たす属性（`data-testid`）と、タップの送り方（確定した frame 中心を座標クリック）だけです。[drivers → Playwright](../../docs/ja/drivers.md#playwrightweb) を参照してください。

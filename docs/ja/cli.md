[English](../cli.md) · **日本語**

# CLI リファレンス

> 実装: `bajutsu/cli.py`（Typer）。エントリポイントは `pyproject.toml` の `bajutsu = "bajutsu.cli:app"`。
> すべてのコマンドは `--app <name>` で 1 アプリを選び、`--config`（既定 `bajutsu.config.yaml`）で
> 設定を指す。アプリ固有差分は config 側にある（[configuration](configuration.md)）。

関連: [run-loop](run-loop.md) ・ [recording](recording.md) ・ [codegen](codegen.md) ・ [configuration](configuration.md)

---

## 共通

- 全コマンドの前に `.env` を読み込む（`_bootstrap`、下記）。
- config が無い / アプリ未定義 / actuator 無し → メッセージを出して **終了コード 2**。
- `--backend` はカンマ区切り（例 `idb`）。空なら config の `backend` を使う。先頭から
  順に可用性を見て **最初に使えるものが actuator**（[drivers](drivers.md#バックエンド選択と-actuator)）。

## `run`

シナリオを **決定的に実行**（`--dismiss-alerts` を付けない限り AI 非依存）。

```bash
bajutsu run <scenario.yaml> --app <name> [options]
```

| オプション | 既定 | 説明 |
|---|---|---|
| `--app` | （必須） | 対象アプリ（config の `apps.<name>`） |
| `--backend` | config | actuator 順（カンマ区切り。先頭から最初に使えるもの） |
| `--udid` | `booted` | 対象 Simulator |
| `--erase / --no-erase` | `--erase` | 各テスト前に `simctl erase`。`--no-erase` で全シナリオの `preconditions.erase` を false に |
| `--dismiss-alerts` | off | システムアラートを視覚で消す保険（要 API キー・[recording](recording.md#システムアラートの自動対処)） |
| `--alert-instruction` | "" | dismiss の代わりに押すボタンの指示 |
| `--log-predicate` | "" | `deviceLog` ストリームを絞る NSPredicate（例 subsystem） |
| `--workers` | 1 | デバイスプール上で並列実行。`--udid u1,u2,…` と `--no-network` が必要（プール数で上限） |
| `--config` | `bajutsu.config.yaml` | config ファイル |

- 証跡は `FileSink(runs/<runId>, udid=..., log_predicate=...)` に書く（[evidence](evidence.md#sink証跡の出力先)）。
- `runId` は `YYYYMMDD-HHMMSS`。
- 出力: `PASS|FAIL  runs/<runId>/manifest.json`。**終了コードは全シナリオ成功で 0、失敗で 1**。

```bash
bajutsu run sample/scenarios/smoke.yaml --app sample --udid <UDID> --backend idb --no-erase
```

## `doctor`

現在画面の **規約充足度スコア**を出す（[configuration](configuration.md#doctor規約充足度スコア)）。AI 非依存。

```bash
bajutsu doctor --app <name> [--udid booted] [--backend ...] [--config ...]
```

- actuator で `query()` し、`score(elements, idNamespaces)` を `render` して表示。
- **終了コードは grade が Blocked で 1、それ以外 0**。

> ⚠️ env / 接続ゲートは未実装。スコアは「いま表示されている画面」に対してのみ計算される。

## `record`

AI でゴールに向けて探索し、**記録したシナリオを OUT に書き出す**（Tier 1・[recording](recording.md)）。

```bash
bajutsu record <out.yaml> --app <name> --goal "<自然言語ゴール>" [options]
```

| オプション | 既定 | 説明 |
|---|---|---|
| `--app` | （必須） | 対象アプリ |
| `--goal` | （必須） | 著すゴール（自然言語） |
| `--udid` | `booted` | 対象 Simulator |
| `--backend` | config | actuator 順 |
| `--erase / --no-erase` | `--erase` | 起動前に erase（アプリはインストール済みである必要） |
| `--dismiss-alerts` | off | オーサリング中のプロンプトを片付ける（要 API キー） |
| `--alert-instruction` | "" | 同上の押下指示 |
| `--config` | `bajutsu.config.yaml` | config |

- 内部で `launch_driver` → `record_loop(driver, goal, ClaudeAgent(), ...)` → `dump_scenarios` で書き出す。
- 出力: `recorded <N> steps -> <out>`。**要 `ANTHROPIC_API_KEY`**（`ClaudeAgent`）。

## `codegen`

シナリオから **ネイティブ XCUITest** を生成（AI 非依存・構造マッピング・[codegen](codegen.md)）。

```bash
bajutsu codegen <scenario.yaml> --app <name> [--emit xcuitest] [-o <out.swift>] [--config ...]
```

| オプション | 既定 | 説明 |
|---|---|---|
| `--emit` | `xcuitest` | 出力形式（現状 `xcuitest` のみ。他は終了コード 2） |
| `-o, --out` | `-` | 出力ファイル。`-` で標準出力 |

- config の `launchEnv` が生成テストの `app.launchEnvironment` に入る。
- ファイル出力時は `wrote <N> scenario(s) -> <out>`。

## 環境変数（.env）

`_bootstrap`（`@app.callback`）が全コマンドの前に `.env` を読む（実装: `bajutsu/dotenv.py`）。

- `KEY=VALUE` 形式。`#` コメント・空行・`export ` 接頭・クォートに対応。
- **既存の環境変数を上書きしない**（実環境の値が常に勝つ）。`.env` はフォールバック。
- 読み先は既定 `.env`、`BAJUTSU_DOTENV` で変更可。`.gitignore` 済み。
- 主な用途: `ANTHROPIC_API_KEY`（`record` と `--dismiss-alerts`）。

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
```

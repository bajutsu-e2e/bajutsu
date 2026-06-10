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

**実行可能ゲート** + 現在画面の **規約充足度スコア**（AI 非依存。[configuration](configuration.md#doctor規約充足度スコア)）。

```bash
bajutsu doctor --app <name> [--udid booted] [--backend ...] [--config ...]
```

- まず env ゲート（`preflight.py`）: actuator が必要とする CLI（`xcrun`、idb なら `idb` /
  `idb_companion`）と**起動済みシミュレータ**を ✓/✗ チェックリストで表示。不足があれば**終了 1**（直し方ヒント付きで即失敗）。
- 次に actuator で `query()` し、`score(elements, idNamespaces)` を表示。**grade が Blocked で 1、それ以外 0**。

## `trace`

完了した run を**テキストタイムライン**で検査する。シナリオごとに、ステップと観測した通信を
時系列で交互に並べ、続けて expectations・app-trace 区間・証跡サマリを出す。読み取り専用
（保存済みの `manifest.json` / `network.json` / `appTrace.json` を読む）。

```bash
bajutsu trace [<run-dir>] [--scenario <substr>] [--runs runs]
```

- `<run-dir>` 省略時は `runs/` 直下の最新 run。`--scenario` は名前の部分一致で絞り込み。
- run が見つからなければ**終了 2**。

## `triage`

run 内の最初の**失敗**シナリオを診断し、最小の修正案を出す — **助言のみ**で pass/fail は判定しない
（AI 境界）。失敗コンテキスト（落ちたステップ＋理由、失敗 expectation、失敗時の要素ツリー、シナリオ）
を組み立て `TriageAgent` に渡す。既定はルールベース（`HeuristicTriageAgent`、API キー不要）: 失敗を
分類（selector / timing / assertion）し、対象 id が画面に無く似た id があれば「もしかして…?」を提案
（id リネームの自己修復）。`--ai` は同じコンテキストに失敗**スクショ**を加えて推論する Claude 版に差し替え
（`ANTHROPIC_API_KEY` が必要）。

エージェントは適用可能な**構造化 fix**（`renameId` / `addIndex` 曖昧一致の一意化 / `raiseTimeout`）も
返せる。`--apply <scenario-file>` が **dry-run diff** を表示、`--write` が source に適用、`--rerun --app
<name>` が patched シナリオを再実行（`--no-erase`）して緑になったか報告する。境界は保たれる: fix は人間が
diff をレビューして opt-in した時のみ適用、断片が source に一致しなければ安全に no-op。

```bash
bajutsu triage [<run-dir>] [--scenario <substr>] [--runs runs] [--ai]
bajutsu triage [<run-dir>] --ai --apply <scenario-file> [--write] \
               [--rerun --app <name> [--backend idb] [--udid <udid>]]
```

- 既定は `runs/` 直下の最新 run。失敗シナリオが無ければ**終了 0**。
- `--rerun` は `--write` と `--app` が必要。

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

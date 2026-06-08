[English](../architecture.md) · **日本語**

# アーキテクチャとモジュール関係

> どのモジュールが何を担当し、どこに依存するか。そして **設計（[`DESIGN.md`](../../DESIGN.md)）に
> あるが現状まだ配線されていない機能** を明示する。

関連: [concepts](concepts.md) ・ 各機能ページ（下のリンク）

---

## モジュール一覧と役割

`bajutsu/` パッケージ（Python 3.11+、pydantic v2 / typer / anthropic / pyyaml）。

| モジュール | 役割 | ページ |
|---|---|---|
| `drivers/base.py` | Driver Protocol + 共通型（`Element`/`Selector`/`Point`）+ **セレクタ解決**（決定性の核） | [selectors](selectors.md) / [drivers](drivers.md) |
| `drivers/fake.py` | インメモリの `FakeDriver`（実機不要テスト用） | [drivers](drivers.md#fakedriver) |
| `drivers/idb.py` | idb バックエンド（ヘッドレス・座標 tap） | [drivers](drivers.md#idb) |
| `scenario.py` | シナリオスキーマ（pydantic 厳格検証）+ YAML 読込 / 書出 | [scenarios](scenarios.md) |
| `assertions.py` | 機械アサーション評価（総関数・例外を投げない） | [selectors](selectors.md#アサーション評価) |
| `orchestrator.py` | 決定的 Tier 2 run ループ（act → wait → verify） | [run-loop](run-loop.md) |
| `evidence.py` | 証跡の取得（瞬時 / 区間）と Sink | [evidence](evidence.md) |
| `intervals.py` | 区間証跡（video / deviceLog）の simctl 子プロセス管理 | [evidence](evidence.md#区間証跡video--devicelog) |
| `report.py` | `manifest.json` + JUnit XML + HTML | [reporting](reporting.md) |
| `config.py` | チーム既定 × アプリ別の解決（`Effective`） | [configuration](configuration.md) |
| `backends.py` | バックエンド可用性判定・actuator 選択・Driver 生成 | [drivers](drivers.md#バックエンド選択と-actuator) |
| `env.py` | `simctl` ラッパ（erase/boot/launch/openurl/io） | [drivers](drivers.md#環境管理simctl) |
| `runner.py` | config + シナリオ → レポート。デバイス factory（launch 手順） | [run-loop](run-loop.md#runner実行パイプライン) |
| `doctor.py` | 規約充足度スコア（id カバレッジ等） | [configuration](configuration.md#doctor規約充足度スコア) |
| `agent.py` | オーサリング Agent 抽象（`Observation`/`Proposal`/`Agent`） | [recording](recording.md) |
| `claude_agent.py` | Claude 実装（ツール強制呼び出し・prompt cache） | [recording](recording.md#claudeagent) |
| `record.py` | record ループ（observe → 提案 → 実行 → 書き出し） | [recording](recording.md#record-ループ) |
| `alerts.py` | システムアラートの検出・dismiss（視覚ロケータ） | [recording](recording.md#システムアラートの自動対処) |
| `codegen.py` | シナリオ → XCUITest（Swift）生成 | [codegen](codegen.md) |
| `cli.py` | Typer ベース CLI（`run`/`record`/`doctor`/`codegen`） | [cli](cli.md) |
| `dotenv.py` | `.env` の最小ローダ（既存環境変数を上書きしない） | [cli](cli.md#環境変数env) |
| `_yaml.py` | `on`/`off`/`yes`/`no` を文字列のまま読む YAML ローダ | [scenarios](scenarios.md#yaml-の注意点) |

## 依存関係（レイヤ）

下層ほど安定で、上層が下層に依存する。中核は `drivers/base.py`（セレクタ解決）であり、すべての
実行系がここに依存する。

```
                       cli.py            ← ユーザ接点（Typer）
        ┌─────────────────┼───────────────────────────┐
     runner.py        record.py                     codegen.py
        │           （Tier 1 / AI）                （構造マッピング）
   orchestrator.py   agent.py / claude_agent.py / alerts.py
        │                 │
   ┌────┼────────┬────────┘
assertions.py  evidence.py ── intervals.py
        │         │
   scenario.py  report.py        config.py     backends.py     env.py
        │                            │              │            │
        └──────────────┬─────────────┴──────────────┴────────────┘
                       ▼
                drivers/base.py  ←── 決定性の核（Element / Selector / resolve_unique）
                       ▲
        ┌──────────────┴──────────────┐
   drivers/fake                   drivers/idb
```

- `orchestrator.py` は `base.Driver` にのみ依存し、**どの具象ドライバとも結合しない**。だから
  `FakeDriver` で実機なしにテストでき、本番では同じループが idb を駆動する。
- `runner.py` が「アプリを起動して準備済みドライバを返す」factory を担い、ループを実機から分離する。
- `scenario.py`（オーサリング表現の pydantic モデル）と `drivers/base.py`（実行時の TypedDict）は
  別物。`Selector.as_selector()` が前者を後者へ変換する。

## テスト構成

`tests/` に **150 のユニットテスト**（`uv run pytest -q`）。すべて実機 Simulator を必要としない:
コマンドビルダは純関数として、実行系は `FakeDriver` / 注入ランナー（`RunFn`・`Spawn`・`Clock`）で
検証する。サンプルアプリに対する実機 E2E は `make e2e` / `make ui-test`（[sample-app](sample-app.md)）。

---

## 実装状況

> 設計（[`DESIGN.md`](../../DESIGN.md)）には将来像も含まれる。**現状のコードが実際に動かすもの**と
> **まだ配線されていないもの**を区別する。

### 実装済み（テストあり・経路が通っている）

- セレクタ解決と曖昧検出（決定性の核）
- シナリオスキーマ（厳格検証）と YAML ラウンドトリップ
- 7 種のアサーション評価
- Tier 2 run ループ（act → wait → verify）、`FakeDriver` で検証
- 証跡: 瞬時（`screenshot`/`elements`）+ 区間（`video`/`deviceLog`）+ `capturePolicy` 発火
- レポート（`manifest.json` / `junit.xml` / `report.html`）
- config 解決（defaults × apps、redact マージ）と actuator 選択
- `simctl` コマンド層・idb の出力パーサ・`doctor` スコア
- CLI `run` / `doctor` / `codegen`、および `record`（AI オーサリング）+ alert guard
- XCUITest コード生成

### 実機未検証（実装はあるが外部 CLI に対する検証が必要）

- idb バックエンドの subprocess 実行。**出力パーサはテスト済みだが、外部 CLI の
  サーフェスと JSON スキーマは「想定」**で、インストール済みツールに対する確認が要る
  （`drivers/idb.py` 冒頭の注記）。`simctl` の launch 手順も best-effort。

### 未配線（スキーマ/フラグはあるが実行時に効かない）

| 機能 | 現状 | 場所 |
|---|---|---|
| 並列実行 `--workers` | CLI フラグは受けるが**未使用**（直列実行のみ） | `cli.py:55` |
| `locale` の適用 | config / preconditions に値は持つが launch で**適用していない** | `config.py` / `scenario.py` |
| `preconditions.setup`（再利用前段） | スキーマのみ。runner は読まない | `config.py` / `scenario.py` |
| `mockServer`（決定的ネットワーク） | config スキーマのみ。起動・接続は**未実装** | `config.py` `MockServer` |
| `network` / `appTrace` 証跡 | capture トークンとして**検証は通る**が、取得は未実装（取得元が無い） | `evidence.py` |
| `relaunch` ステップ | `NotImplementedError`（env 統合後） | `orchestrator.py` `_do_action` |
| `within` セレクタ | `NotImplementedError`（階層クエリが必要） | `drivers/base.py` `matches` |
| `trace` コマンド | CLI に**未実装**（DESIGN の構想） | — |
| `doctor` の実行可能ゲート | コードの `doctor` は **充足度スコアのみ**。env/権限ゲートは未実装 | `doctor.py` |

これらは各機能ページでも該当箇所に「未実装」と注記している。

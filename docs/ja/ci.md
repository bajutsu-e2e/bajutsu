# 継続的インテグレーション（CI）

2 つの別々のトピックを扱います。

1. **このリポの CI** — ツール自体をガード（`.github/workflows/`）。
2. **あなたのアプリの CI で bajutsu を回す** — 再利用可能な composite action とレシピ。

## このリポの CI

| Workflow | ランナー | タイミング | 内容 |
|---|---|---|---|
| [`ci.yml`](../../.github/workflows/ci.yml) | Linux | `main` への push・全 PR | `make check` ゲート一式（Python 3.13）。ロックの鮮度（`uv lock --check`）・整形（`ruff format --check`）・lint（`ruff`）・シェル lint（`shellcheck`）・ワークフロー lint（`actionlint`）・型（`mypy bajutsu demos`）・カバレッジ下限つき `pytest`（`--cov-fail-under=85`）を実行します。ロジック層はシミュレータ不要なので速く安価です |
| [`swift.yml`](../../.github/workflows/swift.yml) | macOS | `main` への push + `BajutsuKit/**` を触る PR | [BajutsuKit](../../BajutsuKit) の `swift build` + `swift test` を実行します。純 Foundation のロジック（リクエスト照合 / モック解析）をシミュレータ無しで単体テストします。実機でのインターセプトそのものは `e2e.yml` がカバーします |
| [`e2e.yml`](../../.github/workflows/e2e.yml) | macOS | 手動 + アプリ/SDK/ランタイムを触る PR | 2 ジョブ: **smoke (idb)** はサンプルをビルドしてシミュレータを起動し、idb バックエンドで `smoke.yaml` を実行します（driver + simctl + idb）。**xcuitest (codegen)** はシナリオからネイティブ XCUITest を生成し（`make -C demos/features ui-test`）`xcodebuild` で実行します（テスト時に bajutsu / idb / AI は不要） |

dev ツールは `dev` 依存グループにあるため、Linux ジョブは `uv sync --group dev` → `uv run
--no-sync …` で実行します（素の `uv run` はデフォルト集合に再同期して落としてしまいます）。
このゲートは [`make check`](../../Makefile) と [`pre-push`](../../.githooks/pre-push) フックを
段ごとにミラーしており、`actionlint`（CI が導入する単体バイナリ）以外は新規クローンでも `uv`
だけで同一に走ります。これが「ローカルで緑」＝「CI で緑」を担保します。

## あなたのアプリの CI で回す

> bajutsu はプレリリース（未公開）です。PyPI 公開までは vendor（submodule / checkout）して、その
> checkout からアクションを実行してください。アクションは bajutsu の `pyproject.toml` に対して
> `uv sync` を実行します。

bajutsu は CI 向け出力を持っています: `junit.xml`、自己完結の `report.html`、`0`/`1` 終了
コード、そして Actions 内では失敗**アノテーション** + ジョブ**サマリ**です。macOS ランナーで:

1. **アプリをビルドしてインストール**します（ビルドはアプリごとに異なるためあなたの担当 — `xcodebuild`
   + `xcrun simctl install`）。
2. [`bajutsu-e2e`](../../.github/actions/bajutsu-e2e/action.yml) composite action で **bajutsu を実行**します。
   `idb_companion` 導入・依存同期・`doctor` プリフライト（非ブロッキング）・シナリオ実行・成果物
   （report / スクショ / 動画 / `network.json`）のアップロードを行います。

```yaml
jobs:
  e2e:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4
      - uses: maxim-lobanov/setup-xcode@v1
        with: { xcode-version: latest-stable }
      - uses: astral-sh/setup-uv@v6
        with: { enable-cache: true }
      # --- アプリのビルド + インストール（あなたのビルド、起動済みシミュレータへ） ---
      - run: xcodebuild -scheme MyApp -destination 'generic/platform=iOS Simulator' -derivedDataPath dd build
      - id: sim
        run: |
          udid=$(xcrun simctl create ci "iPhone 16")
          xcrun simctl boot "$udid"; xcrun simctl bootstatus "$udid" -b
          echo "udid=$udid" >> "$GITHUB_OUTPUT"
      - run: xcrun simctl install "${{ steps.sim.outputs.udid }}" dd/Build/Products/Debug-iphonesimulator/MyApp.app
      # --- bajutsu 実行 ---
      - uses: your-org/bajutsu/.github/actions/bajutsu-e2e@main
        with:
          scenarios: e2e/*.yaml
          app: myapp
          udid: ${{ steps.sim.outputs.udid }}
```

### アノテーション + ジョブサマリ

`GITHUB_ACTIONS` がセットされていると、`bajutsu run` は失敗シナリオごとに `::error::` アノテー
ション（PR にインライン表示）を出し、`$GITHUB_STEP_SUMMARY` に PASS/FAIL 表を追記します。フラグ
不要で、Actions 環境を自動検出します。

### 補足

- **JUnit**: `junit.xml` を test-reporter 系アクション（例 `dorny/test-reporter`）に渡すとインライン表示できます。
- **決定性**: シナリオ [`mocks`](../network.md#deterministic-mocks) で通信をスタブし、ライブサーバ依存を排除します。
- **`doctor`**: 現状は規約*スコア*（非ブロッキングのプリフライト）です。env/権限の実行可能ゲート（idb /
  idb_companion / Xcode の存在チェック）は今後の課題です。

# 継続的インテグレーション（CI）

別物を 2 つに分けています:

1. **このリポの CI** — ツール自体をガード（`.github/workflows/`）。
2. **あなたのアプリの CI で bajutsu を回す** — 再利用可能な composite action とレシピ。

## このリポの CI

| Workflow | ランナー | タイミング | 内容 |
|---|---|---|---|
| [`ci.yml`](../../.github/workflows/ci.yml) | Linux | `main` への push・全 PR | `ruff` + `mypy` + `pytest`（Python 3.11 / 3.13）。ロジック層はシミュレータ不要で速い・安い |
| [`e2e.yml`](../../.github/workflows/e2e.yml) | macOS | 手動 + アプリ/SDK/ランタイムを触る PR | 2 ジョブ: **smoke (idb)** はサンプルをビルド → シミュレータ起動 → idb バックエンドで `smoke.yaml` 実行（driver + simctl + idb）。**xcuitest (codegen)** はシナリオからネイティブ XCUITest を生成し（`make ui-test`）`xcodebuild` で実行（テスト時に bajutsu / idb / AI は不要） |

dev ツールは `dev` extra にあるため、Linux ジョブは `uv sync --extra dev` → `uv run --no-sync …`
で実行（素の `uv run` はデフォルト集合に再同期して落としてしまう）。

## あなたのアプリの CI で回す

> bajutsu はプレリリース（未公開）です。PyPI 公開までは vendor（submodule / checkout）して、その
> checkout からアクションを実行してください — アクションは bajutsu の `pyproject.toml` に対して
> `uv sync` を実行します。

bajutsu は CI 向け出力を既に持っています: `junit.xml`、自己完結の `report.html`、`0`/`1` 終了
コード、そして Actions 内では失敗**アノテーション** + ジョブ**サマリ**。macOS ランナーで:

1. **アプリをビルド & インストール**（ビルドはアプリごとに異なるのであなたの担当 — `xcodebuild`
   + `xcrun simctl install`）。
2. [`bajutsu-e2e`](../../.github/actions/bajutsu-e2e/action.yml) composite action で **bajutsu を実行** —
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
不要（Actions 環境を自動検出）。

### 補足

- **JUnit**: `junit.xml` を test-reporter 系アクション（例 `dorny/test-reporter`）に渡すとインライン表示。
- **決定性**: シナリオ [`mocks`](../network.md#deterministic-mocks) で通信をスタブし、ライブサーバ依存を排除。
- **`doctor`**: 現状は規約*スコア*（非ブロッキングのプリフライト）。env/権限の実行可能ゲート（idb /
  idb_companion / Xcode の存在チェック）は今後の課題。

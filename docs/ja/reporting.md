[English](../reporting.md) · **日本語**

# レポート（manifest.json / JUnit / HTML）

> 1 回の run は 1 つ以上のシナリオ（`list[RunResult]`）を実行する。その結果を 3 つの形式で
> 書き出す。`manifest.json` がレポートと CI の **単一の真実**。
>
> 実装: `bajutsu/report.py`。

関連: [run-loop の実行結果](run-loop.md#実行結果データ構造) ・ [evidence](evidence.md)

---

## 出力レイアウト

```
runs/<runId>/
├── manifest.json     # step → outcome の相関（単一の真実）
├── junit.xml         # CI 連携（1 シナリオ = 1 testcase）
├── report.html       # 自己完結 HTML（外部アセット無し）
└── <stepId>/         # ステップごとの証跡（FileSink 使用時）
    ├── after.png     # screenshot
    ├── elements.json # query() ダンプ
    ├── segment.mp4   # video（区間）
    └── device.log    # deviceLog（区間）
```

`runId` は CLI が `YYYYMMDD-HHMMSS` で採番する（`cli.py`）。`stepId` は `step.name` または `step<i>`。

## manifest.json

`RunResult` 以下はすべて dataclass なので、`asdict()` でステップ / expect の結果がそのまま落ちる。

```json
{
  "runId": "20260605-101530",
  "ok": true,
  "scenarios": [
    {
      "scenario": "onboard, log in, and increment the counter",
      "ok": true,
      "steps": [
        {
          "index": 5, "action": "tap", "ok": true, "reason": "",
          "duration_s": 0.12,
          "assertion_results": [],
          "artifacts": [{ "name": "after.png", "kind": "screenshot", "provider": "driver" }]
        }
      ],
      "expect_results": [
        { "ok": true, "kind": "value", "detail": "value equals='2': id='counter.value'", "reason": "" }
      ],
      "failure": null
    }
  ]
}
```

- `ok` (トップ): 全シナリオが ok なら true。
- `steps[].duration_s`: 各ステップの計時（`actionLog` 相当の情報）。
- `steps[].artifacts`: そのステップで取れた証跡の来歴（[evidence](evidence.md#アーティファクトの来歴provider)）。
- `failure`: 失敗時の要約（例 `"step 3 (tap): 一致なし: {...}"`）。成功なら null。

## junit.xml

CI 連携用。**1 シナリオ = 1 `<testcase>`**。失敗シナリオには `<failure>` が付き、その `text` に
各ステップ / expect の ok/FAIL と理由が並ぶ。

```xml
<testsuite name="bajutsu" tests="2" failures="1">
  <testcase name="..." classname="bajutsu"/>
  <testcase name="..." classname="bajutsu">
    <failure message="step 1 (tap): ...">step 0 tap: ok
step 1 tap: FAIL 一致なし: {...}</failure>
  </testcase>
</testsuite>
```

## report.html

人間が見る自己完結 HTML（インライン CSS、外部アセット無し）。シナリオごとに PASS/FAIL バッジと、
ステップ表（`#` / action / result / time / reason）を出す。失敗行は赤背景。

## 書き出し API

```python
def write_report(run_dir, run_id, results) -> Path   # 3 形式を書き、manifest のパスを返す
def manifest_dict(run_id, results) -> dict            # manifest の素（テスト・検査用）
def junit_xml(results) -> str
def html_report(run_id, results) -> str
```

`runner.run_and_report` がこの `write_report` を呼び、CLI に `(results, manifest_path)` を返す
（[run-loop](run-loop.md#runner実行パイプライン)）。CLI は全シナリオ成功なら終了コード 0、失敗で 1。

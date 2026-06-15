[English](../reporting.md) · **日本語**

# レポート（manifest.json / JUnit / HTML）

> 1 回の run は 1 つ以上のシナリオ（`list[RunResult]`）を実行する。その結果を 3 つの形式で
> 書き出す。`manifest.json` がレポートと CI（継続的インテグレーション）の **単一の真実**。
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
  "backend": "idb",
  "scenarios": [
    {
      "scenario": "onboard, log in, and increment the counter",
      "ok": true,
      "backend": "idb",
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
- `backend`: その run を操作したアクチュエータ（`idb`、テストでは `fake`）。アクチュエータは
  run ごとに 1 つ固定なので、トップレベルは通常 1 つの名前。各シナリオも自分の `backend` を持つ
  （[drivers](drivers.md#バックエンド選択と-actuator)）。
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

人間が見る自己完結 HTML（インライン CSS、外部アセット無し）。ヘッダには run id と全体 PASS/FAIL、
その下に **シナリオファイル名**（`source_name`）、さらにファイルレベルの **`description`** があれば
表示する。各シナリオ行のサマリには **シナリオ名** と、設定があれば横に **シナリオレベルの
`description`** を表示する。こうして run 全体でシナリオ名 + ファイル名 + description を提示する。
シナリオ定義とその実行結果は **1 つの Steps タブに統合**され、ラベル付きセクション
（preconditions / **steps** / **expectations**）ごとにテーブルで描画する。**steps** テーブル：`#` / `result`（PASS/FAIL ピルを独立カラムで）/
`action`（色付きバッジ）/ `detail`（対象説明）/ `at` / `view`（スクショ＋**レポート内 element tree
ビューア**: キャプチャした要素を別タブではなくページ内オーバーレイで開く）/ `reason`。
detail 中の識別子（`#home.title`）と定数リテラル（`“text”`・数値）は控えめなインライントークンで
描画し、ソリッドな action/assert バッジとは**異なるトンマナ**にして、変数と定数を一目で識別できる
ようにしている。`assert` ステップの複数チェックは**ネストしたテーブル**になり、1 アサーション 1 行で
`kind` / `target` / `comparison` のセルに分割する（読みにくい `a; b; c` を解消）。実行されなかった
ステップ（失敗で停止）も skipped として残る。**観測した通信を時系列で steps に差し込む**（各々シナリオ開始からのオフセットで配置）: HTTP メソッドを中立バッジ、ステータスを `result` 列に置き、通信の設定（method / endpoint / status / duration / ヘッダ）を detail セル内の**ネストしたテーブル**で表示する。どの通信を出すかはシナリオの `network.filter.domains`（URL ホスト）で絞る。Network タブは引き続き全件を載せる。**preconditions** テーブルは折りたたみ可（key / value）。
**expectations** テーブルは並行カラム `result` / `kind`（バッジ）/ `target`（検査対象セレクタ＝例
`#counter.value`）/ `comparison`（例 `== “2”`）/ `reason`（同じ id/定数トークン）。**Rich / YAML
トグル**で同じタブを構造化ビューと生のシナリオ YAML に切り替えられる。

`visual` の expectation は行の下に **baseline と actual のインタラクティブ比較ビュー**を描画する。
4 モード: **Swipe**（仕切りをドラッグして左右にワイプ）/ **Onion**（スライダーで actual を
baseline に重ねてクロスフェード）/ **Blend**（`mix-blend-mode: difference` — 同一画素は黒、差分が
ライブに光る）/ **Diff**（マシンが算出した確定ピクセル diff。アサーションの `exclude` 領域は
マスク済み。失敗時のみ表示）。`diff <pct>%` バッジ、初回実行（actual だけ）では `no baseline yet`
バッジが付く。チェックが合格しなかった場合は **Approve as baseline** ボタンが出て、撮影スクショを
ベースラインディレクトリへ昇格させる。これは `/api/approve` への `POST` なので `bajutsu serve`
経由で開いたときだけ機能する（ディスクから直接開いたレポートでは非表示）。CLI 版は
[`bajutsu approve`](cli.md#approve)。

失敗行は赤背景。ステップをクリックすると録画をその時刻にシークするが**自動再生はしない**
（停止中なら停止のまま・再生中なら再生継続）。ステップのスクショをクリックすると原寸ライトボックスが
開き、**← / →**（または画面上の矢印）で run 内の全スクショを**シナリオをまたいで**順送りできる
（キャプションにシナリオ・ステップ・位置を表示）。run のアクチュエータはヘッダの `driver: <backend>`
チップと各シナリオ行の小バッジで表示する。Device Log / App Trace は別タブのまま。

## 書き出し API

```python
def write_report(run_dir, run_id, results, definitions=None, sources=None, source_name=None, description=None) -> Path  # 3 形式を書く。definitions=シナリオ毎の dict、sources=生 YAML、source_name=シナリオファイル名、description=ファイルレベルの説明
def manifest_dict(run_id, results) -> dict            # manifest の素（テスト・検査用）
def junit_xml(results) -> str
def html_report(run_id, results, run_dir=None, definitions=None, sources=None, source_name=None, description=None) -> str
```

`runner.run_and_report` がこの `write_report` を呼び、CLI に `(results, manifest_path)` を返す
（[run-loop](run-loop.md#runner実行パイプライン)）。CLI は全シナリオ成功なら終了コード 0、失敗で 1。

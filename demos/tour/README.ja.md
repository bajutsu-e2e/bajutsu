[English](README.md) · **日本語**

# ツアー：実機 Simulator で run、改変、診断

Bajutsu のライフサイクル全体を、コマンド一つで、実機 iOS Simulator 上で動かします。しかも**完全に決定論的で、LLM も APIキーも不要**です。`run` と `triage` は AI を一切使わないので、必要なのは Xcode と起動中の Simulator のある Mac だけです。

```bash
make -C demos tour                   # または直接:
./demos/tour/demo.sh
```

[showcase](../showcase/README.ja.md) アプリ（SwiftUI のアクセシビリティ版）に対して動き、3つのフェーズを
辿ります。

1. **Run**：著作済みシナリオ [`tour.yaml`](../showcase/scenarios/menu/tour.yaml) を Simulator 上で実行し、
   Stable カタログの3番目の馬を開いて Favorite をオンにします。本物の実行ディレクトリ（manifest、JUnit、
   `report.html`）を書き出し、**PASS** します。
2. **改変（Modify）**：期待する Favorite の状態を誤った値に変える → 決定論的なチェックが **FAIL**（LLM
   ではなく機械的なアサーションが捕捉）→ 戻す → 再び **PASS**。
3. **診断（Diagnose）**：セレクタを解決できないように改名する（テストの足元でセレクタがずれた状況）→
   実行が **FAIL** → [`triage`](../../bajutsu/triage.py) が失敗した実行を読み、診断します: カテゴリ
   （`selector`）に加え、捕捉した要素ツリーから引いた *「`stable.row.3` のことでは？」* という
   修正案 → セレクタを戻す → **PASS**。

スクリプトは gitignore された作業コピー（`demos/tour/scenario.yaml`）を編集し、追跡対象の `tour.yaml`
には手を触れないので、リポジトリは汚れません。実機の `record` デモとの唯一の違いは、シナリオが既に
著作済みである点です。Claude のステップが無いので APIキーも要りません。

## Mac が無い場合は？ 同じ物語を仮想デバイスで（セットアップ不要）

まったく同じ 著作 → run → 改変 → 診断 のライフサイクルが、メモリ上の
[`FakeDriver`](../../bajutsu/drivers/fake.py) に対しても動きます。Simulator も Xcode も APIキーも要らず、
Linux/CI で数秒です。

```bash
uv run python demos/tour/tour.py
```

こちらは加えて**著作**も見せます: 自然言語の目標が、本物の [`record`](../../bajutsu/record.py) ループで
シナリオになります（Claude の代役 [`KeywordAgent`](../record/generate_from_nl.py) を使うのでキー不要）。
この代役の頭脳を除けば、すべて本番のコードパスです。実際の実行が使うのと同じオーケストレータ、アサーション
エンジン、レポート出力、ヒューリスティック triage を通ります。`demos/tour/runs/` の下に開ける本物の
`report.html` も書き出します。いちばん速い最初の一歩で、上の実機 `make -C demos tour` がデバイス上の
本番です。

## ステータス

> `tour.yaml` は、showcase の smoke / search シナリオと `record` の目標がすでに実機で動かしている識別子
> （`stable.row.*`、`horse.favorite`）を再利用しており、本物のパイプラインでパースも通りますが、ツアーの
> 流れそのものはこの形では**まだ実機で再生していません**。Mac 上で `make -C demos tour` で確認してください。

## 次にどこへ

- [`demos/showcase/WEBUI.ja.md`](../showcase/WEBUI.ja.md)：Web UI ですべての証跡タイプを巡るツアー。
- [`demos/showcase/`](../showcase/README.ja.md)：実機での本物の Claude による著作（`record`）。
- すべてのデモの地図: [`demos/README.ja.md`](../README.ja.md)。

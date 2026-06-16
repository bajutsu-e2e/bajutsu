[English](README.md) · **日本語**

# 60秒ツアー（セットアップ不要）

Bajutsu の物語の全体を、コマンド一つで — **Simulator も idb も API キーも Mac も不要**。Python
コアが動く環境ならどこでも（Linux、CI、クローン直後）数秒で動き、実機セットアップに踏み込む前に、この
ツールが何をするのかを*見る*いちばん速い方法です。

```bash
make -C demos tour                  # または直接:
uv run python demos/tour/tour.py
```

実機デモ（[`demos/record/demo.sh`](../record/README.ja.md)）と同じ4フェーズを辿りますが、外部依存が
何も要らないように、メモリ上の [`FakeDriver`](../../bajutsu/drivers/fake.py) に対して動かします:

1. **著作（Author）** — 自然言語の目標が決定論的なシナリオ（YAML）になります。本物の
   [`record`](../../bajutsu/record.py) ループを動かし、API キーが要らないように「頭脳」だけを
   キーワードの代役（[`KeywordAgent`](../record/generate_from_nl.py)）に差し替えています。
2. **実行（Execute）** — [`run`](../../bajutsu/runner.py) が本物のパイプラインで再生し、本物の実行
   ディレクトリ（`manifest.json`、JUnit XML、そしてブラウザで開ける自己完結の **`report.html`**）を
   書き出します。**PASS** します。
3. **改変（Modify）** — 期待するカウンター値を誤った値に変える → 決定論的なチェックが **FAIL**（LLM
   ではなく機械的なアサーションが捕捉）→ 戻す → 再び **PASS**。
4. **診断（Diagnose）** — セレクタを解決できないように改名する（テストの足元でセレクタがずれた状況）→
   実行が **FAIL** → [`triage`](../../bajutsu/triage.py) が失敗した実行を読み、診断します:
   カテゴリ（`selector`）に加え、捕捉した要素ツリーから引いた *「`counter.increment` のことでは？」*
   という修正案 → セレクタを戻す → 再び **PASS**。

フェーズ1の頭脳を除けば、すべて本番のコードパスです — 実際の実行が使うのと同じオーケストレータ、
アサーションエンジン、レポート出力、ヒューリスティック triage。そこが要点です。これは作り直したおもちゃ
ではなく、下に仮想デバイスを敷いた本物のパイプラインです。

## 得られるもの

- 生成されたシナリオ `demos/tour/generated.yaml`（gitignore 済み）— スクリプト内の目標か YAML を直接
  編集して再実行できます。
- 各フェーズの本物のレポート `demos/tour/runs/<phase>/report.html`（gitignore 済み）—
  `runs/02-pass/report.html` を開くと、手順・スクリーンショット・アサーションが、Web UI と同じ見た目で
  表示されます。

## 次の一歩: 同じ物語を実機で

このツアーは意図的に FakeDriver の境界で止まっています。**本物の Claude** が **起動中の Simulator** に
対して idb バックエンド経由で著作する*まったく同じ*ライフサイクルを — 証跡一式（動画、通信ログ、
システムアラート処理、ビジュアルリグレッション）込みで — 見るには、以下を参照してください:

- [`demos/record/`](../record/README.ja.md) — AI 著作 → 実行 → 改変 → triage を実機で。
- [`demos/features/`](../features/README.ja.md) — Web UI ですべての証跡タイプを巡るツアー。

3本のデモの地図は [`demos/README.ja.md`](../README.ja.md) にあります。

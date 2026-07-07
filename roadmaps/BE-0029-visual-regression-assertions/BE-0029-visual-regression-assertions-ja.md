[English](BE-0029-visual-regression-assertions.md) · **日本語**

# BE-0029 — ビジュアル回帰アサーション

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0029](BE-0029-visual-regression-assertions-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0029") |
| 実装 PR | [#30](https://github.com/bajutsu-e2e/bajutsu/pull/30), [#34](https://github.com/bajutsu-e2e/bajutsu/pull/34) |
| トピック | 競合調査（MagicPod / Autify）由来の候補 |
| 由来 | 両社 |
<!-- /BE-METADATA -->

## はじめに

スクリーンショットをベースラインと差分比較する、新しいアサーション種別です。除外領域と、per-device / per-locale のベースラインに対応します。AI ではなく決定的な機械チェックであるため、「機械アサーションのみで合否を判定する」という原則に合致します。

## 動機

既存のアサーション種別がチェックするのは、画面の*構造*です。要素の値、その存在、保持しているテキストといったものです。
しかし、純粋に*見た目*の回帰はどれも捕捉できません。レイアウトのずれ、色の変化、アイコンの欠落、フォントの
誤った描画などです。これらはまさに人間がひと目で気づく不具合でありながら、アクセシビリティツリーが変わって
いないために、構造チェックはすべて通過してしまいます。競合（MagicPod / Autify）がスクリーンショット比較を備えて
いるのもこの理由からで、要望としても頻出します。

ビジュアルチェックは、Bajutsu のプライムディレクティブの内側に収まる必要があります。リスクは、「これは正しく
見えるか？」が判断を要する問いに聞こえる点にあります。LLM に委ねたくなる類の作業です。しかし実際は違います。
保存済みベースラインに対するピクセル差分は決定的な機械チェックであり、同じ入力からは常に同じ合否が得られ、
ループ内にモデルは存在しません。これにより、ビジュアル回帰は Tier-2 の run/CI ゲートに自然に適合します。「曖昧すぎる」
として棚上げするのではなく採用できるのは、この理由からです。

## 詳細設計

シナリオレベルの `expect:` ブロックで使う、決定的な `visual` アサーション種別として実装しました。
キャプチャしたスクリーンショットを、保存済みのベースライン画像と差分比較します。除外領域で動的な内容
（時計やステータスバー）をマスクし、`threshold` で許容差分率を指定できます（既定 `0` ＝完全一致）。
画像が異なる場合は、確認用の差分画像を run と並べて出力します。

```yaml
expect:
  - visual:
      baseline: counter-2.png     # ベースラインディレクトリからの相対パス
      threshold: 0.1              # 許容差分率（既定 0 ＝完全一致）
      exclude:                    # 動的領域をマスク
        - { x: 0, y: 0, w: 390, h: 54 }
```

- スキーマ：`bajutsu/scenario/` の `ExcludeRegion` / `VisualMatch` モデルと `Assertion.visual`
  フィールド。
- 比較エンジン：`bajutsu/visual.py`。Pillow の `ImageChops.difference` によるピクセル単位の差分、
  除外領域のマスク、しきい値の許容、差分画像の出力を担います。Pillow はオプション依存です
  （`pip install bajutsu[visual]`）。
- 評価：`bajutsu/assertions.py` の `VisualContext`（スクリーンショットのパス、ベースラインディレクトリ、
  差分出力ディレクトリ）と `_eval_visual`。
- オーケストレーション：expect 評価の前にスクリーンショットをキャプチャし、`VisualContext` を
  `run_scenario` に引き渡します（`bajutsu/orchestrator/`、`bajutsu/runner/`）。

評価は AI を介さない純粋な機械チェックであるため、プライムディレクティブを損なうのではなく強化します。

## 検討した代替案

Python レベルのピクセルループは、C レベルで高速な `ImageChops.difference` を採るために見送りました。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

[PR #30](https://github.com/bajutsu-e2e/bajutsu/pull/30)、`bajutsu/visual.py`、
`bajutsu/assertions.py`、`bajutsu/scenario/`、[DESIGN §6.4](../../DESIGN.md)、
[evidence.md](../../docs/ja/evidence.md)

### フォローアップ（未実装）

- 現在のスクリーンショットを新しいベースラインとして保存する `--update-baselines` CLI フラグ
- ビジュアル差分の HTML レポート描画（ベースライン / 実際 / 差分の横並び）
- `bajutsu.config.yaml` での `baselines_dir` 設定
- per-device / per-locale のベースライン亜種

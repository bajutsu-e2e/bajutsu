# textlint config for `japanese-tech-writing`

このディレクトリは、`japanese-tech-writing` スキルが推敲後に走らせる
[textlint](https://github.com/textlint/textlint) の設定と実行環境を持つ。

## ファイル

- `.textlintrc.json` — 適用するルール。**ここを編集すればルールを変えられる。**
- `package.json` — textlint 本体とルールの版を固定する。

## いまの既定ルール

`textlint-rule-preset-ja-technical-writing`（日本語の技術文書向けの定番プリセット）を、
プリセットの既定設定のまま有効にしているだけである。個別ルールの調整はしていない。

## ルールの変え方

`.textlintrc.json` の `rules` を編集する。例えば特定のルールを切るなら、プリセット全体を
展開せず、そのルールだけを名前で上書きする。

```json
{
  "rules": {
    "preset-ja-technical-writing": {
      "sentence-length": false
    }
  }
}
```

別のプリセットやルールを足すときは、`package.json` の `devDependencies` に加えてから
`.textlintrc.json` の `rules` に登録する。版は必ず固定する（どの環境でも同じ指摘になるように）。

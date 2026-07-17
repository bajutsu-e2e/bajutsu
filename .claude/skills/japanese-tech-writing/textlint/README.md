# textlint config for `japanese-tech-writing`

このディレクトリは、`japanese-tech-writing` スキルが推敲後に走らせる
[textlint](https://github.com/textlint/textlint) の設定と実行環境を持つ。

## ファイル

- `.textlintrc.json` — 適用するルール。**ここを編集すればルールを変えられる。**
- `package.json` — textlint 本体とルールの版を正確に固定する（範囲指定は使わない）。
- `package-lock.json` — 推移的依存も含めた全パッケージを、版と整合性ハッシュで固定する。

## インストール

```bash
npm --prefix .claude/skills/japanese-tech-writing/textlint ci --ignore-scripts
```

## サプライチェーン対策

依存は `npm install` ではなく `npm ci` で入れる。`npm ci` は `package-lock.json` を
書き換えず厳守する。さらに各パッケージの tarball を、lockfile に記録された整合性ハッシュ
（Subresource Integrity、sha512）と照合する。同じ版番号のまま中身が差し替えられても、
ハッシュ不一致で失敗する。`--ignore-scripts` は install 時のライフサイクルスクリプトの
実行を止める（npm の供給網攻撃が実行される主な経路である）。

`package.json` で依存を git の commit hash に固定する案は採らない。textlint は
monorepo で、公開 npm パッケージはその一部から publish されるため、リポジトリの
commit を指しても公開 CLI パッケージには解決しない。より本質的には、commit hash は
トップレベルの一パッケージしか固定せず、実際の攻撃面である多数の推移的依存を守れない。
それらを内容ハッシュで一括して固定するのが lockfile の整合性ハッシュであり、commit
hash より広く強い保証になる。版を上げるときは `package.json` を編集して `npm install`
を一度だけ実行し、更新された `package-lock.json` を必ずコミットする。

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

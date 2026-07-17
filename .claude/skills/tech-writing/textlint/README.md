# textlint config for `tech-writing`

このディレクトリは、`tech-writing` スキルおよびその日本語レイヤーである
`japanese-tech-writing` スキルが推敲後に走らせる
[textlint](https://github.com/textlint/textlint) の設定と実行環境を持つ。英語・日本語どちらの
文章にも、このディレクトリの設定一本で対応する。

## ファイル

- `.textlintrc.json` — 適用するルール。**ここを編集すればルールを変えられる。**
- `package.json` — textlint 本体とルールの版を正確に固定する（範囲指定は使わない）。
- `package-lock.json` — 推移的依存も含めた全パッケージを、版と整合性ハッシュで固定する。

## インストール

```bash
npm --prefix .claude/skills/tech-writing/textlint ci --ignore-scripts
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

`textlint-rule-preset-ja-technical-writing`（日本語の技術文書向けの定番プリセット）に加えて、
以下の個別ルールを有効にしている。

日本語向け：

- `spellcheck-tech-word` — 技術用語の表記ゆれ（「インターフェース」→「インタフェース」など）
- `ja-hiragana-keishikimeishi` / `ja-hiragana-fukushi` / `ja-hiragana-hojodoushi` — 形式名詞・
  副詞・補助動詞をひらがなで書くべき箇所の指摘
- `ja-hiraku` — 上記3ルールと同じ品詞（`keishikimeishi`・`fukushi`・`hojodoushi`）は二重報告を
  避けるため無効化し、代名詞・副助詞・補助形容詞・連体詞・接続詞など残りの品詞だけを有効にしている
- `general-novel-style-ja` — 句読点・記号の用法（既定設定は一部の項目のみ有効。詳細は
  `.textlintrc.json` を参照）
- `prefer-tari-tari` — 「〜たり〜たりする」の並列表現の用法
- `@textlint-ja/textlint-rule-no-insert-dropping-sa` — サ抜き・サ入れ表現の誤用
- `no-mixed-zenkaku-and-hankaku-alphabet` — 全角・半角アルファベットの混在
- `joyo-kanji` / `ja-joyo-or-jinmeiyo-kanji` — 常用漢字・人名用漢字の範囲チェック

英語向け：

- `write-good` — 受動態・冗長表現など英文スタイルの指摘
- `stop-words` — filler word・バズワード・決まり文句の検出
- `unexpanded-acronym` — 頭字語が文書中で展開されているかの確認
- `abbr-within-parentheses` — 括弧内の略語表記の確認
- `alex` — 性別・人種・宗教などに関する配慮を欠く表現の検出
- `ukraine` — ウクライナの地名・人名がロシア語風の綴りになっていないかの検出

`ja-kyoiku-kanji`（教育漢字）と `ja-allowed-kanji`（許可漢字の明示的な指定）は
`package.json` に依存として加えたうえで `.textlintrc.json` では無効(`false`)にしている。
どちらも常用漢字よりさらに狭い範囲に漢字を限定するルールで、技術文書には厳しすぎるため
既定では無効にしている。必要な場面があれば、個別に `true` にして使う。

`textlint-rule-ginger`（Ginger 校正 API を使う英文チェック）は導入していない。依存の
`gingerbread` → `request` → `form-data`/`qs`/`tough-cookie`/`uuid` が `npm audit` で
critical 2件・moderate 3件（いずれも修正版なし）を報告し、`gingerbread` 自体も npm 上で
サポート終了と表示されているため見送った。

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

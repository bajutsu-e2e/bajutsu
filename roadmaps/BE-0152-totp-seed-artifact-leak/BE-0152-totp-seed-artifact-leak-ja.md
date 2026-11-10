[English](BE-0152-totp-seed-artifact-leak.md) · **日本語**

# BE-0152 — TOTP のシード値を run 成果物に含めない

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0152](BE-0152-totp-seed-artifact-leak-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0152") |
| 実装 PR | [#607](https://github.com/bajutsu-e2e/bajutsu/pull/607) |
| トピック | セキュリティ強化 |
<!-- /BE-METADATA -->

## はじめに

scenario の `totp` ステップは、`secret` フィールドに TOTP のシード値をそのまま持つことがあります。
その scenario ファイルは、run 成果物へそのままコピーされます。本提案は、シードの生の値がディスクに
書き出されず、アーカイブとして配布されるものにも含まれないようにします。

## 動機

`Totp`（`bajutsu/scenario/models/actions.py:127-136`）は、`totp` ステップを `secret: str` として
モデル化しています。`secret` は共有の base32 鍵で、多くは `${secrets.*}` です。このステップは
決定的かつローカルで完結します（`bajutsu/totp.py`）。シードとクロックから RFC 6238 のコードを
計算し、ネットワーク呼び出しはしません。そのため scenario 側に生のシードが必要になります。

run は、実行した scenario ファイルをエビデンスの一部として保存します。run 成果物
（`bajutsu/evidence.py`、`report.html` やアーカイブ一式）は、このリポジトリが他の箇所でも機微情報
として扱っている面です。ネットワークのデフォルトシークレット redaction とスクリーンショットの
シークレット警告に関する姉妹提案も、同種の漏えいを対象にしています。

漏えいが起きるのは、`secret` が YAML 内にリテラル文字列として書かれている場合です。`${secrets.*}`
経由にすれば避けられます。`${secrets.*}` は、scenario ファイル自体に資格情報を含めないための
BE-0032 の仕組みです。run は、リテラルなシードを redaction せずに成果物一式へコピーします。
アーカイブにアクセスできる人（共有された CI 成果物、ダウンロードしたレポートなど）は、使い捨ての
コードではなく、恒久的な TOTP シードを抽出できてしまいます。

深刻度は Low です。`${secrets.*}`（BE-0032）によって、著者は scenario にリテラルなシードを書かずに
済みます。scenario ファイルにシードをリテラルでコミットすること自体、成果物へのコピーとは無関係に、
以前からあるアンチパターンです。しかしツール側は、それが起きても警告も redaction も行いません。
執筆中の利便性からリテラルな `secret` で書かれた scenario は、以後のすべての run の成果物へ、
黙ってシードを持ち込み続けます。

## 詳細設計

1. **scenario ファイルが `run_dir` へコピーされる際に `totp.secret` を redaction する。**
   成果物ライタが、実行済み scenario のスナップショットを書き出します（`network.json` /
   `report.html` と並ぶエビデンス側の処理）。コピーを書き出す前に、`totp.secret` フィールドの
   リテラル値を固定のプレースホルダ（例えば `"<redacted>"`）へ置き換えます。これは、`serve` の
   API キープレビューを `mask_secret` がすでに redaction している方法と同じ考え方です。
2. **解決済みの値のケースにも対応する。** scenario が `totp.secret` に `${secrets.*}` を使う
   場合があります。補間・解決された後の値も、成果物へ書き出してはいけません。この作業では、
   既存のシークレット変数 redaction（BE-0032）が、他の `${secrets.*}` 利用と同様に解決済みの
   `totp.secret` もカバーしているかを確認します。カバーしていなければ、その隙間を塞ぎます。
3. **回帰テストを追加する。** リテラルで書かれた場合と `${secrets.*}` から解決された場合の
   どちらでも、`run_dir` 内の scenario スナップショットに TOTP シードの生の値が含まれないことを
   検証します。

## 検討した代替案

- **scenario の検証時点で `totp` ステップのリテラル `secret` を禁止し、`${secrets.*}` を必須に
  する。** より厳格な補完策です。本項目の一次的な修正としては採用せず、後続の候補として残します。
  成果物へのコピーによる漏えいを塞ぐだけでなく、受理される scenario の構文自体を変えることに
  なるためです。リテラルなシードを持つ既存の scenario には、移行も必要になります。本提案の
  コピー時 redaction であれば、破壊的なスキーマ変更なしに既存の執筆スタイルを保護できます。
- **プロジェクト全体で、汎用のシークレットスキャナにより `Totp.secret` らしき文字列すべてを
  redaction する。** 必要以上に広い対策なので却下しました。対象フィールドはモデルレベルで正確に
  わかっています（`Totp.secret`）。その 1 フィールドに絞った redaction のほうが単純です。
  無関係な文字列を誤検知するリスクもありません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] run 成果物へコピーされる scenario スナップショット内の `totp.secret` を redaction する。
- [x] BE-0032 のシークレット変数 redaction が解決済みの `totp.secret` をカバーしていることを
      確認する（不足していれば拡張する）。
- [x] リテラルの場合と `${secrets.*}` から解決された場合の両方をカバーする回帰テストを追加する。

- 2026-07-03: `redact_totp_secrets` が、エビデンスのスナップショット（`scenario.yaml`、レポートの
  生 YAML 表示、`manifest.json`）でリテラルの `totp.secret` シードをマスクし、`${secrets.*}`
  参照はそのまま残すようにしました。解決後の値は run レベルの secret 値スクラブ（BE-0032）が
  すでにカバーしていることを確認し、回帰テスト（ユニットと `run_and_report`）と英日のエビデンス
  ドキュメントを追加しました。

## 参考

`bajutsu/scenario/models/actions.py:127-136`（`Totp`）、`bajutsu/totp.py`、
`bajutsu/evidence.py`。関連: BE-0046（OTP・メールステップ）、BE-0032（シークレット変数）。
2026-07-02 のコードベース分析レポート（セキュリティ）に基づきます。

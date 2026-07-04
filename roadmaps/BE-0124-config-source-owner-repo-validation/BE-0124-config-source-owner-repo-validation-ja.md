[English](BE-0124-config-source-owner-repo-validation.md) · **日本語**

# BE-0124 — config-source の owner・repo 検証を厳格化する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0124](BE-0124-config-source-owner-repo-validation-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0124") |
| 実装 PR | [#627](https://github.com/bajutsu-e2e/bajutsu/pull/627) |
| トピック | セキュリティ強化 |
<!-- /BE-METADATA -->

## はじめに

Git の config-source パーサは owner や repo のセグメント内に `.`、`..`、`%` を許可しています。
本提案はどちらも GitHub が実際に許可している文字集合に制約し、config source がパスのような
セグメントや percent-encoding されたセグメントを、後で URL・トークンのスコープ・キャッシュパスの
構築に使われる値へ紛れ込ませられないようにします。

## 動機

`bajutsu/config_source.py` は `github:<owner>/<repo>[@<ref>][:<path>]` と
`git+https://<host>/<owner>/<repo>.git[@<ref>][#<path>]` を、`_GITHUB_RE`
（`config_source.py:30-32`、owner/repo は `[^/@:]+`）と `_GIT_URL_RE`
（`config_source.py:34-36`、owner は `[^/@]+`、repo は `[^/@#]+?`）でパースしています。
どちらも `.`、`..`、`%` を除外していないため、owner/repo の値が `..` であったり
percent-encoding されたセグメントであったりしてもパースに成功してしまいます。パース結果の
`owner` / `repo` は GitHub API の URL（`config_source.py:119,123`、
`https://api.github.com/repos/{owner}/{repo}/...`）とディスク上のキャッシュパス
（`config_source.py:181`、`cache_root / spec.host / spec.owner / spec.repo / sha`）の両方に
使われます。同一セグメント内のトラバーサルトークン（`..`）や、他のシステムと組み合わさった際に
予期せず解釈される値は、API 呼び出しの対象やキャッシュディレクトリを、意図した
`<host>/<owner>/<repo>/<sha>/` というレイアウトの外へ向けさせかねません。

深刻度は Low です。値は依然として固定のホストに対して TLS 経由で送られますし、多くの場合
GitHub 自身の API がリクエストパス中の不正な owner/repo を拒否します。また展開処理
（`config_source.py:181-212`）はすでに *tarball 内部のエントリ*における `..` や絶対パスを
防いでいます。本提案が対象とするのは owner/repo のセグメントそのものであり、その除去ロジックが
走る前の段階でキャッシュパスに到達してしまう点です。文脈は TLS とトークン認証で GitHub と
通信する場面であり、開かれたファイルシステムやシェル面ではないため、実際の影響は限定的です。

## 詳細設計

1. **`_GITHUB_RE` と `_GIT_URL_RE` の owner/repo の文字クラスを GitHub の実際の許可文字集合へ
   置き換える。** owner（GitHub のユーザー名・org 名）は英数字とハイフン（先頭・末尾・連続する
   ハイフンの禁止まで GitHub のユーザー名文法を厳密に再現するのは対象外とし、まずは保守的な
   `[A-Za-z0-9-]+` で許容する）、repo 名は英数字とハイフン・アンダースコア・ピリオドとします。
   ただし repo セグメントがちょうど `.` または `..` である場合や `%` を含む場合は明示的に
   拒否します（owner/repo セグメントにおいて percent-encoding が正当な役割を持つ場面は
   ありません）。
2. **この拒否をサイレントな正規化ではなくパース失敗として扱う。** `parse_config_source`
   （あるいは `_GITHUB_RE` / `_GIT_URL_RE` を消費している箇所）が、マッチしない文字列に対して
   すでに行っているのと同じ方法で `None` を返す、または例外を送出するようにします。これにより
   不正な形式の config source は、現在パースできない値と同じ扱いで失敗します。
3. **単体テストを追加する。** 拒否されるべき形（`..`、repo が `.` のみ、`%2e%2e` のような
   セグメント）を検証し、既存の正当な `owner/repo` の形（BE-0063 のテストがカバーしているものを
   含む）が引き続きパースできることを確認します。

## 検討した代替案

- **パース時ではなくキャッシュパス構築の段階でのみ検証する。** 却下しました。パースはすべての
  config source の形（`github:` の省略記法と一般的な `git+https://` 形式）が通る唯一の関所
  であり、そこで検証すれば `owner` / `repo` を消費するすべての箇所で個別にチェックを重複させる
  必要がありません。パース時点で早期に失敗させれば、実際に不正だった入力に紐づいた明確な
  エラーメッセージも得られます。
- **`%` を即座に拒否せず、percent-decode してから再検証する。** 不要な複雑さとして却下しました。
  GitHub の owner/repo 名が正当に `%` を含むことはないため、即座に拒否するほうが単純で、
  誤検知のコストもありません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `_GITHUB_RE` と `_GIT_URL_RE` の owner/repo の文字クラスを GitHub の実際の文字集合へ
      厳格化し、`.`、`..`、`%` を拒否する。
- [x] この拒否が、パース不能な source と同様にパース失敗として現れることを確認する。
- [x] 拒否されるべき形と既存の正当な形の両方をカバーする単体テストを追加する。

- 2026-07-04: owner の文字クラスを `[A-Za-z0-9-]+`、repo の文字クラスを `[A-Za-z0-9._-]+` に
  制約して（これで `%` はマッチしなくなります）、`parse_config_spec` で repo がちょうど `.`・`..`
  のセグメントを拒否しました。拒否される形と、引き続きパースできるべきピリオド付き・`.git` 付きの
  形を単体テストで確認しています。

## 参考

`bajutsu/config_source.py:30-36`（`_GITHUB_RE`、`_GIT_URL_RE`）、
`bajutsu/config_source.py:119,123`（API URL の構築）、`bajutsu/config_source.py:181`
（キャッシュパスの構築）。関連: BE-0063（Git config source）。2026-07-02 のコードベース分析
レポート（セキュリティ）に基づきます。

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

Git の config-source パーサは、owner や repo のセグメント内に `.`、`..`、`%` を許可しています。
そのため config source は、パスのようなセグメントや percent-encoding されたセグメントを、後で URL、
トークンのスコープ、キャッシュパスの構築に使われる値へ紛れ込ませられます。本提案は、owner と repo の
どちらも GitHub が実際に許可している文字集合に制約します。

## 動機

`bajutsu/config_source.py` は、2 つの形式をパースしています。

- `github:<owner>/<repo>[@<ref>][:<path>]`。`_GITHUB_RE`（`config_source.py:30-32`）が使われ、
  owner と repo は `[^/@:]+` にマッチします。
- `git+https://<host>/<owner>/<repo>.git[@<ref>][#<path>]`。`_GIT_URL_RE`
  （`config_source.py:34-36`）が使われ、owner は `[^/@]+`、repo は `[^/@#]+?` にマッチします。

どちらのパターンも `.`、`..`、`%` を除外していません。owner や repo の値が `..` であっても、
percent-encoding されたセグメントであっても、パースに成功します。パース結果の `owner` と `repo` は、
次の 2 か所で使われます。

- GitHub API の URL（`config_source.py:119,123`、`https://api.github.com/repos/{owner}/{repo}/...`）
- ディスク上のキャッシュパス（`config_source.py:181`、`cache_root / spec.host / spec.owner / spec.repo / sha`）

同一セグメント内のトラバーサルトークン（`..`）は、API 呼び出しの対象やキャッシュディレクトリを、
意図した `<host>/<owner>/<repo>/<sha>/` というレイアウトの外へ向けさせかねません。他のシステムと
組み合わさった際に予期せず解釈される値も同様です。

深刻度は Low です。理由は 3 つあります。

- 値は固定のホストに対して TLS 経由で送られます。
- 多くの場合、GitHub 自身の API がリクエストパス中の不正な owner/repo を拒否します。
- 展開処理（`config_source.py:181-212`）は、*tarball 内部のエントリ*における `..` や絶対パスをすでに
  防いでいます。本提案が対象とするのは owner/repo のセグメントそのものです。このセグメントは、除去
  ロジックが走る前の段階でキャッシュパスに到達します。

文脈は TLS とトークン認証で GitHub と通信する場面であり、開かれたファイルシステムやシェル面では
ないため、実際の影響は限定的です。

## 詳細設計

1. **`_GITHUB_RE` と `_GIT_URL_RE` の owner/repo の文字クラスを、GitHub の実際の許可文字集合へ置き換え
   る。**
   - owner（GitHub のユーザー名または org 名）は、英数字とハイフンです。先頭・末尾・連続するハイフン
     の禁止まで GitHub のユーザー名文法を厳密に再現することは対象外とし、まずは保守的な
     `[A-Za-z0-9-]+` で許容します。
   - repo 名は、英数字と `-`、`_`、`.` です。repo セグメントがちょうど `.` または `..` である場合と、
     `%` を含む場合は、明示的に拒否します。owner/repo のセグメントで percent-encoding が正当な役割を
     持つ場面はありません。
2. **この拒否を、サイレントな正規化ではなくパース失敗として扱う。** `parse_config_source`（あるいは `_GITHUB_RE` と `_GIT_URL_RE` を消費している箇所）が、マッチしない文字列に対してすでに行っている
   のと同じ方法で、`None` を返すか例外を送出します。これにより、不正な形式の config source は、現在
   パースできない値と同じ扱いで失敗します。
3. **単体テストを追加する。** 拒否されるべき形（`..`、repo が `.` のみ、`%2e%2e` のようなセグメント）
   を検証します。あわせて、既存の正当な `owner/repo` の形（BE-0063 のテストがカバーしているものを含む）
   が引き続きパースできることを確認します。

## 検討した代替案

- **パース時ではなく、キャッシュパス構築の段階でのみ検証する。** 却下しました。パースは、すべての
  config source の形（`github:` の省略記法と一般的な `git+https://` 形式）が通る唯一の関所です。
  そこで検証すれば、2 つの呼び出し箇所を 1 か所で塞げます。消費側で検証すると、`owner` と `repo` を
  使うすべての箇所でチェックを重複させることになります。パース時点で早期に失敗させれば、実際に不正
  だった入力に紐づいた明確なエラーメッセージも得られます。
- **`%` を即座に拒否せず、percent-decode してから再検証する。** 不要な複雑さとして却下しました。
  GitHub の owner/repo 名が正当に `%` を含むことはないため、即座に拒否するほうが単純で、誤検知の
  コストもありません。

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

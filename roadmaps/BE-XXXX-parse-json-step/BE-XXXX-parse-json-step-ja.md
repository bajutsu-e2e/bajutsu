[English](BE-XXXX-parse-json-step.md) · **日本語**

# BE-XXXX — JSON 文字列から値を取り出す `parseJson` ステップ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-parse-json-step-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | シナリオ記述機能 |
| 関連 | [BE-0440](../BE-0440-http-step-field-extraction/BE-0440-http-step-field-extraction-ja.md)、[BE-0472](../BE-0472-eval-step-value-operations/BE-0472-eval-step-value-operations-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本項目は `parseJson` ステップを追加します。このステップは、任意の Java Script Object Notation（JSON）文字列からパス指定で値を取り出し、変数に入れます。あわせて `http` ステップの `extractBody` を削除します。役割は新しいステップが引き継ぎます。

削除は既存のファイルを壊します。`extractBody` を使うシナリオは読み込めなくなります。

## 動機

[BE-0440](../BE-0440-http-step-field-extraction/BE-0440-http-step-field-extraction-ja.md) の項目抽出は、`http` ステップ内の応答本文にしか使えません。`http` は Hyper Text Transfer Protocol（HTTP）のリクエストを送るステップです。変数に入った文字列を読む手段はありません。

`eval` ステップの `json` 操作は、JSON 配列をリストに変えます。オブジェクトの中は読めません。

変更の効果は、以前に保存した文字列から入れ子の値を読むシナリオが成功することで確認できます。

## 詳細設計

### `parseJson` ステップ

```yaml
- parseJson:
    from: "${vars.response}"
    extract:
      - { var: code, path: "data.items[0].code" }
      - { var: userId, path: "data.user.id" }
```

`from` は、通常の `${...}` 置換が使える文字列です。`extract` は、`{ var, path }` を 1 件以上並べたリストです。次の規則は `extractBody` から引き継ぎます。

| 観点 | 規則 |
|---|---|
| パスの文法 | 変更なし。オブジェクトのキーと、0 始まりの `[n]` 添字 |
| 値の保存 | 文字列はそのまま。ほかの型は空白を詰めた JSON 文字列 |
| 失敗 | JSON でない入力、不正なパス、解決できないパス |
| 複数の指定 | すべて解決できたときに限り書き込む |
| `var` の重複 | 読み込みエラー。置換後にも再検査 |
| パスへの置換 | 値は 1 区間だけを埋め、`.`、`[`、`]` を含められない |

秘密値の扱いは `eval` ステップ（[BE-0472](../BE-0472-eval-step-value-operations/BE-0472-eval-step-value-operations-ja.md)）に合わせます。エラーメッセージには、入力の文字列も置換後の値も含めません。秘密値を含む入力から読んだ値は秘密値とみなし、以降の成果物でマスクします。`from` の文字数の上限は、`eval` の入力と同じ 1,048,576 文字です。

### `extractBody` の削除

`http` ステップから `extractBody` を削除します。`extractBody` を使うシナリオは読み込みに失敗し、エラーメッセージで `saveBody` と `parseJson` の組み合わせを案内します。

```yaml
- http: { url: "${secrets.API}/login", status: 200, saveBody: login }
- parseJson:
    from: "${vars.login}"
    extract:
      - { var: token, path: "data.token" }
```

### 最優先原則との関係

| 原則 | 本設計での守り方 |
|---|---|
| 合否の判定にモデルを使わない | パスの解決は、解析した JSON をたどる固定の手順 |
| 決定性 | 不正な入力やパスはステップを失敗させ、何も書き込まない |
| アプリ非依存 | ステップとパスの文法はすべてのターゲットで共通 |

### 作業手順

| # | 作業 | 主なファイル | 完了条件 | 前提 |
|---|---|---|---|---|
| 1 | `parseJson` のモデル、ハンドラー、読み込み検査、秘密値の記録を追加 | `bajutsu/common/scenario/`、`bajutsu/common/orchestrator/` | 上記の規則をユニットテストで確認 | なし |
| 2 | `extractBody` を削除し、テストを `parseJson` へ移行 | `http_request.py`、`_http_extract.py`、`handlers/http.py`、`substitution.py` | 旧シナリオが移行の案内付きで読み込みに失敗 | 1 |
| 3 | 日英の文書を更新 | `docs/scenarios.md`、`docs/dsl-grammar.md`、`docs/architecture.md` と日本語版 | `extractBody` を使える機能として説明するページがない | 2 |

## 検討した代替案

| 代替案 | 採用しなかった理由 |
|---|---|
| `eval` にパス取得の操作を追加 | 複数の値を取るには専用ステップが読みやすく、`{ var, path }` の形も保てる |
| `extractBody` と `parseJson` の併存 | 同じ役割の仕組みが 2 つになり、規則を二重に保守する |
| `extractBody` にワイルドカードや条件を追加 | 読めるのが応答本文だけという制約は残る |

## 進捗

> 作業の進行に合わせて更新します。チェックリストは *詳細設計* の MECE な作業分解に対応し（作業単位ごとに
> 1 項目）、ログは変更内容と時期を（古い順に）記録して PR にリンクします。

- [ ] `parseJson` ステップ
- [ ] `extractBody` の削除
- [ ] 日英の文書

## 参考

- [BE-0440](../BE-0440-http-step-field-extraction/BE-0440-http-step-field-extraction-ja.md)：`parseJson` が引き継ぐパスの文法
- [BE-0472](../BE-0472-eval-step-value-operations/BE-0472-eval-step-value-operations-ja.md)：秘密値とエラーメッセージの規則

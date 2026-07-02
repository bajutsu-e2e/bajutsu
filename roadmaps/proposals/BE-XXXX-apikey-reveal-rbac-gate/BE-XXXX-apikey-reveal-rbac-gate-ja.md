[English](BE-XXXX-apikey-reveal-rbac-gate.md) · **日本語**

# BE-XXXX — API キー公開エンドポイントに RBAC を適用する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-apikey-reveal-rbac-gate-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トピック | セキュリティ強化 |
<!-- /BE-METADATA -->

## はじめに

ホスティングされた `serve` では、`GET /api/apikey?reveal=1` が role に関わらず認証済みの呼び出し元
全員に共有 AI プロバイダキーを全文で返します。本提案は、この公開パスに RBAC を適用し、admin または
owner だけが生のキーを読めるようにします。

## 動機

`bajutsu/serve/authz.py` の `required_role`（147 行目）はリクエストに必要な最小 role を計算します
が、状態変更を伴う呼び出ししか見ていません。`if method != "POST": return None` としているためです。
`/api/apikey` は `_ADMIN_PATHS` に含まれており、キーを設定する `POST` には正しく admin role が
要求されます。しかし公開パスはクエリパラメータ付きの `GET`（`bajutsu/serve/handler.py:162-163`、
`ops.api_key_info(state, bool(self._qs("reveal")))`）であり、`POST` でないため `required_role`
はこれを一切ゲートしません。`api_key_info`（`bajutsu/serve/operations.py:237-246`）は `reveal`
が真値であれば追加の role チェックなしに、JSON レスポンスへキーの全文を含めます。

深刻度は Low です。このエンドポイントには依然として有効なセッションまたはトークンが必要で
（`_gate()` による全体の認証チェック）、既定のローカル・単一ユーザー構成の `serve` では影響は
operator 自身に留まります。しかし複数 role を持つホスティング構成（BE-0051、BE-0047）では、
run 結果の閲覧に限定されるはずの `viewer` role が、公開用クエリパラメータを直接叩くだけで共有
AI プロバイダキーを読めてしまいます。これはまさに admin 限定の `POST /api/apikey` が非 admin
から守ろうとしているキーそのものです。

## 詳細設計

1. **`required_role` を `GET /api/apikey?reveal=1` にも適用する。** 既存の `POST` 分岐と並べて、
   `method == "GET"` かつ `path == "/api/apikey"` かつ `reveal` クエリパラメータが真値である
   場合に admin role を要求するチェックを追加します。公開しない `GET`（マスクされたプレビュー
   のみ）は現行の公開読み取りのままとします。キーが設定されているかどうかとマスク済みプレビュー
   しか含まないため、秘密情報を一切露出しないからです。
2. **クエリの値を RBAC チェックへ渡す。** `required_role` は現在 `(method, path)` しか受け取り
   ませんが、公開の判定にはクエリ文字列も必要です。トランスポート側の呼び出し（`bajutsu/serve/
   handler.py` の `forbidden_for_role` / `_gate()`）を拡張し、このパスに限ってパース済みクエリ
   を渡すようにします。他の呼び出し元のシグネチャは変更しません。
3. **同種の漏えいが他の読み取りパスにないか確認する。** `provider_info` と `config_info` が
   生のキーを一切返していないことを確認します。`api_key_info` の `reveal` 分岐だけが該当する
   はずです。

## 検討した代替案

- **公開値を `GET` から完全に外し、取得を `POST` 必須にする。** 却下しました。修正の本質はどちらも
  同じ RBAC ゲートであり、`GET` のまま（冪等で状態変更を伴わない）にしておくほうが `serve` の
  他の読み取りエンドポイントの流儀に合うためです。API 形状を広く変えるだけの労力に見合いません。
- **キーを常にマスクし、`reveal` 自体を廃止する。** 却下しました。admin が `serve` から生のキーを
  コピーして別のツールで再利用するといった正当な必要があるためです。RBAC ゲートのほうが、その
  機能を適切な role に残したまま行える、より的を絞った修正です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `required_role`（またはトランスポート側のゲート）を拡張し、`GET /api/apikey?reveal=1`
      に admin role を要求する。
- [ ] `provider_info` / `config_info` が生のキーを返さないことを確認する。
- [ ] 非 admin role が公開パスで 403 になることを検証する回帰テストを追加する。

まだ着手した PR はありません。

## 参考

`bajutsu/serve/authz.py:147`（`required_role`）、`bajutsu/serve/handler.py:162-163`、
`bajutsu/serve/operations.py:237-246`（`api_key_info`）。関連: BE-0051（ホスティングのための
serve ハードニング）、BE-0047（AI データ主権）。2026-07-02 のコードベース分析レポート
（セキュリティ）に基づきます。

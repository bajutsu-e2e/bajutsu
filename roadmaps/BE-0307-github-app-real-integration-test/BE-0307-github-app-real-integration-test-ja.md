[English](BE-0307-github-app-real-integration-test.md) · **日本語**

# BE-0307 — GitHub App の config source トークンフローに対する実統合テスト

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0307](BE-0307-github-app-real-integration-test-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0307") |
| トピック | config の取得元 |
| 関連 | [BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth-ja.md), [BE-0302](../BE-0302-config-source-real-repo-fetch/BE-0302-config-source-real-repo-fetch-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`github/app.py` は、非公開リポジトリ向けの config source を GitHub App のインストール
トークンフロー（JSON Web Token (JWT) へ署名し、それをインストールトークンへ交換し、そのトークンで
config を取得する）で支えています。JWT の署名自体は、実際の `cryptography`（実際の RSA 鍵、実際の
RS256 署名検証）に対して正しくテストされています。しかしネットワーク側は違います。`installation_token`
の交換は手書きの `fake_fetch` だけで駆動されており、`_fetch` の HTTP エラーマッピングは
`urllib.request.urlopen` を monkeypatch してあらかじめ用意したエラーを送出させることでテストされています。
このフローを実際の GitHub App のインストールに対して完了させるテストや CI ジョブは、
1つもありません。本項目は、そのテストと CI ジョブを追加します。

## 動機

署名の数学的な処理は健全であり、本項目はそこには手を入れません。証明できないのは、GitHub の
実際の API が Bajutsu の送るものを受け入れ、コードが想定するものを返すかどうかです。実際には
GitHub が拒否するクレーム形状やアルゴリズムの JWT、実際の時計に対してしか現れない `iat`/`exp`
クレームのクロックスキューの境界ケース、あるいは手書きの `{"id": 999}` / `{"token": "..."}`
というフィクスチャから実際の形状がドリフトしたインストールトークンのレスポンス、これらのいずれも
現行のテストスイートでは検出できません。処理がプロセスの外に一度も出ないからです。非公開
リポジトリ向け config source は副次的な機能です（`config_source.py`）が、その背後にあるトークン
フローは、まさにモックが構造上検証できない外部統合の領域です。モックは、それを書いた人が
「GitHub はこう返すはずだ」と信じている内容しか返さないからです。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **CI 専用の使い捨て GitHub App**：使い捨て、あるいは低権限のテスト用リポジトリにインストール
  した、テスト専用の最小限の GitHub App を登録し、その秘密鍵をリポジトリの secret として保存
  します。`installation_token` が必要とする以上の権限は一切持たせません。
  [BE-0302](../BE-0302-config-source-real-repo-fetch/BE-0302-config-source-real-repo-fetch-ja.md)
  の非公開リポジトリ版オプションも、同じ種類の使い捨て App を同じ種類のリポジトリに必要とします。
  2つ用意するのではなく、App とリポジトリの組を両項目で使い回します。
- **秘密鍵で gate したライブ統合テスト**：実際の `_app_jwt` → `installation_token` → `_fetch`
  という一連の流れを、その実際の App と実際のリポジトリに対して実行するテストを追加します。
  secret がないときはスキップし、`make check` はどの contributor の環境でも認証情報不要のまま
  green を保ちます。
- **実際のレスポンス形状を検証する**：実際の installation-lookup レスポンスが `id` フィールドを、
  実際の access-tokens レスポンスが `token` フィールドを依然として持つこと（フローが `_json_field`
  で2つの別々のエンドポイントから読み取る2つのフィールド）を確認し、手書きのフィクスチャでは
  検出できないスキーマのドリフトを捉えます。
- **まずゲート対象外とする**：
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
  の前例に従い、新しいジョブをまず CI のシグナルとして着地させ、必須化はそのあとで検討します。

## 検討した代替案

- **エラーマッピングのロジックがユニットテストされていることを根拠に、モック化された HTTP
  テストを信頼する**：あらかじめ用意した `HTTPError` に対してエラーマッピングが正しいことは、GitHub の実際の
  API が実際にその形でそのエラーを返すかどうかや、そのエラーを引き起こす JWT がそもそも
  受理される点については何も語りません。
- **使い捨ての App を用意せず、GitHub の公開ドキュメントに記載された想定形状に対してのみ
  テストする**：ドキュメントは実際に実装された API から実務上ドリフトします。実際の呼び出しだけが、
  GitHub の公表された挙動ではなく現在の実際の挙動を観測できる唯一の検証です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 使い捨てのテスト用リポジトリにスコープした、CI 用の使い捨て GitHub App を登録する。
- [ ] `_app_jwt` → `installation_token` → `_fetch` の一連の流れに対する、秘密鍵で gate したライブ統合テストを追加する。
- [ ] 実際の installation-lookup レスポンスと access-tokens レスポンスが、フローが読み取る `id` /
  `token` フィールドを依然として持っていることを確認する。
- [ ] ゲート対象外のシグナルとして CI に組み込む。

## 参考

- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- [BE-0302 — config source の実リポジトリ fetch 検証](../BE-0302-config-source-real-repo-fetch/BE-0302-config-source-real-repo-fetch-ja.md)
- `bajutsu/github/app.py`、`tests/test_github_app.py`

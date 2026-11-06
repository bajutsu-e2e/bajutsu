[English](BE-0307-github-app-real-integration-test.md) · **日本語**

# BE-0307 — GitHub App の config source トークンフローに対する実統合テスト

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0307](BE-0307-github-app-real-integration-test-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0307") |
| トピック | config の取得元 |
| 関連 | [BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth-ja.md), [BE-0302](../BE-0302-config-source-real-repo-fetch/BE-0302-config-source-real-repo-fetch-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`github/app.py` は、非公開リポジトリ向けの config source を、GitHub App のインストールトークンフローで
支えています。フローは3段階です。JSON Web Token（JWT）へ署名し、それをインストールトークンへ交換し、その
トークンで config を取得します。

JWT の署名自体は、実際の `cryptography` に対して正しくテストされています。実際の RSA 鍵と、実際の RS256
署名検証を使います。しかしネットワーク側は違います。`installation_token` の交換は、手書きの `fake_fetch`
だけで駆動されています。`_fetch` の HTTP エラーマッピングは、`urllib.request.urlopen` を monkeypatch して、
あらかじめ用意したエラーを送出させることでテストされています。

このフローを、実際の GitHub App のインストールに対して完了させるテストや CI ジョブは、1つもありません。
本項目は、そのテストと CI ジョブを追加します。

## 動機

署名の数学的な処理は健全であり、本項目はそこには手を入れません。現行のテストが証明できないのは、GitHub の
実際の API が Bajutsu の送るものを受け入れ、コードが想定するものを返すかどうかです。

現行のスイートを素通りする不具合が、3つあります。

- 実際には GitHub が拒否する、クレーム形状やアルゴリズムの JWT。
- 実際の時計に対してしか現れない、`iat` / `exp` クレームのクロックスキューの境界ケース。
- 手書きの `{"id": 999}` / `{"token": "..."}` というフィクスチャから、実際の形状がドリフトしたインストール
  トークンのレスポンス。

処理がプロセスの外に一度も出ないため、スイートはこれらのどれも検出できません。

非公開リポジトリ向け config source（`config_source.py`）は、副次的な機能です。しかしその背後にあるトークン
フローは、まさにモックが構造上検証できない、外部統合の領域です。モックが返すのは、それを書いた人が
「GitHub はこう返すはずだ」と信じている内容だけだからです。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **CI 専用の使い捨て GitHub App**：テスト専用の最小限の GitHub App を登録します。権限は、
  `installation_token` が必要とする分に限ります。使い捨てか低権限のテスト用リポジトリにインストールし、
  秘密鍵をリポジトリの secret として保存します。
  [BE-0302](../BE-0302-config-source-real-repo-fetch/BE-0302-config-source-real-repo-fetch-ja.md)
  の非公開リポジトリ版オプションも、同じ種類の使い捨て App を同じ種類のリポジトリに必要とします。2つ
  用意せず、App とリポジトリの組を両項目で使い回します。
- **秘密鍵でゲートしたライブ統合テスト**：実際の `_app_jwt` → `installation_token` → `_fetch` という
  一連の流れを、その実際の App と実際のリポジトリに対して実行するテストを追加します。secret がないときは
  スキップします。そのため `make check` は、どの contributor の環境でも認証情報不要のまま green を保ちます。
- **実際のレスポンス形状を検証する**：実際の installation-lookup レスポンスが `id` フィールドを、実際の
  access-tokens レスポンスが `token` フィールドを、依然として持つことを確認します。フローは、この2つの
  フィールドを、2つの別々のエンドポイントから `_json_field` で読み取ります。この確認により、手書きの
  フィクスチャでは検出できないスキーマのドリフトを捉えます。
- **まずゲート対象外とする**：
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
  の前例に従い、新しいジョブをまず CI のシグナルとして着地させ、必須化はそのあとで検討します。

## 検討した代替案

- **エラーマッピングのロジックがユニットテストされていることを根拠に、モック化された HTTP テストを
  信頼する**：あらかじめ用意した `HTTPError` に対してエラーマッピングが正しくても、それは何も語りません。
  GitHub の実際の API が、実際にその形でそのエラーを返すか。そのエラーを引き起こす JWT が、そもそも受理
  されるか。どちらも未検証のままです。
- **使い捨ての App を用意せず、GitHub の公開ドキュメントに記載された想定形状に対してのみテストする**：
  ドキュメントは、実務上、実際に実装された API からドリフトします。GitHub の公表された挙動ではなく、現在の
  実際の挙動を観測できるのは、実際の呼び出しだけです。

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

[English](BE-XXXX-serve-oauth-real-handshake.md) · **日本語**

# BE-XXXX — Real OAuth handshake verification for serve's GitHub login

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-serve-oauth-real-handshake-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Hosting the web UI (cloud / self-hosted) |
<!-- /BE-METADATA -->

## はじめに

`serve/server/oauth.py` の `GitHubOAuthClient` は、Authlib の `OAuth2Client` をラップして `serve`
の GitHub ログインを駆動します。`tests/serve/test_oauth.py` のどのテストも、この実際のクラスを
インスタンス化したり駆動したりすることはありません。すべてのテストが `FakeOAuthClient`、
`_RaisingOAuthClient`、あるいは `httpx` の代役を務める手書きの `_PagingClient` に置き換えられて
います。本項目は、使い捨ての GitHub OAuth App に対する実際のハンドシェイクテストを追加します。

## 動機

これらの fake は、`serve` のログインフローが `GitHubOAuthClient` の返す内容をまさしく呼び出して
いることを証明します。しかし、証明できないことがあります。実際の Authlib `OAuth2Client` を
実際の `httpx` の上でラップした実際のクラスが、GitHub に対して本当にトークン交換を完了できるか
という点です。あるいは、`_fetch_orgs` のページネーションロジックが、手組みの `_PagingClient`
ではなく GitHub の実際のページネーションレスポンスヘッダーに対しても成立するかという点も
証明されません。Authlib のバージョンアップに
よるトークン交換の呼び出しシグネチャの変更、GitHub の OAuth レスポンス形状の変更、または
リダイレクト/Cookieのドメイン設定ミスは、いずれもこのテストスイートからは見えません。処理が
プロセスの外に一度も出ないからです。これはまさに、モック化されたクライアントによるテストスイート
が構造上検出できない失敗モードです。

## 詳細設計

提案の粒度は次の単位で MECE に分解します。

- **CI 専用の使い捨て GitHub OAuth App**：クライアントシークレットをリポジトリの secret として
  保存した、テスト専用の最小限の OAuth App を登録し、使い捨てのテストアカウントまたは組織に
  スコープします。
- **API キーで gate したライブハンドシェイクテスト**：その App に対して実際の(スクリプト化された
  ヘッドレスの)認可コード交換で `GitHubOAuthClient` を駆動するテストを追加します。secret が
  ないときはスキップし、実際のアクセストークンが返ってくること、そして `_fetch_orgs` が実際の
  ページネーションレスポンスをまさしくパースできることを検証します。
- **まず非 gating とする**：
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
  の前例に従い、新しいジョブをまず CI の signal として着地させ、必須化はそのあとで検討します。

## 検討した代替案

- **ログインフロー自体のロジックがユニットテストされていることを根拠に、モック化されたクライアント
  によるテストを信頼する**：デフォルトのクライアントレスポンスに対してフローのロジックが正しいことは、
  実際の Authlib/`httpx` のスタックが GitHub に対して本当にハンドシェイクを完了できるかどうかとは
  無関係です。それこそが本項目が検証する性質です。
- **OAuth2Client の契約については Authlib 自身のテストスイートに任せる**：Authlib のテストは
  Authlib 自体をカバーするものであり、`serve` 自身の `GitHubOAuthClient` ラッパーと `_fetch_orgs`
  のページネーションロジックが、GitHub を相手にまさしくそれを駆動できているかどうかについては
  何も語りません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] CI 専用の使い捨て GitHub OAuth App を登録する。
- [ ] `GitHubOAuthClient` と `_fetch_orgs` に対する、API キーで gate したライブハンドシェイクテスト
  を追加する。
- [ ] 非 gating の signal として CI に組み込む。

## 参考

- [BE-0282 — CI における実 backend のネットワーク捕捉・モック・アサーションのカバレッジ](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- `bajutsu/serve/server/oauth.py`、`tests/serve/test_oauth.py`
  (`FakeOAuthClient`、`_RaisingOAuthClient`、`_PagingClient`)、GitHub App の実統合テストを扱う
  姉妹提案

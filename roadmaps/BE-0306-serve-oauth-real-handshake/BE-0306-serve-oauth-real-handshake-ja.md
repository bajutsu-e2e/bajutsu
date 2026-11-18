[English](BE-0306-serve-oauth-real-handshake.md) · **日本語**

# BE-0306 — serve の GitHub ログインに対する実 OAuth ハンドシェイク検証

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0306](BE-0306-serve-oauth-real-handshake-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0306") |
| トピック | 検証とカバレッジ |
| 関連 | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md), [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`serve/server/oauth.py` の `GitHubOAuthClient` は、Authlib の `OAuth2Client` をラップして `serve` の GitHub
ログインを駆動します。`tests/serve/test_oauth.py` のどのテストも、この実際のクラスをインスタンス化したり
駆動したりしません。すべてのテストが、`FakeOAuthClient`、`_RaisingOAuthClient`、または `httpx` の代役を務める
手書きの `_PagingClient` に置き換えられています。

本項目は、使い捨ての GitHub OAuth App に対して本物のハンドシェイクを一度捕捉します。そして、捕捉した
ハンドシェイクを実際のクライアントコードに対して再生します。

## 動機

これらの fake が証明するのは、`serve` のログインフローが `GitHubOAuthClient` の返す内容を正しく呼び出して
いることです。次の2点は、未検証のまま残ります。

- 実際の Authlib `OAuth2Client` を実際の `httpx` の上でラップした実際のクラスが、GitHub に対して本当に
  トークン交換を完了できるか。
- `_fetch_orgs` が、手組みの `_PagingClient` の代役ではなく、GitHub の実際の org 一覧レスポンス形状を
  そもそもパースできるか。

次の2つの変更は、このテストスイートからは見えません。Authlib のバージョンアップによるトークン交換の
呼び出しシグネチャの変更と、GitHub の OAuth レスポンス形状の変更です。処理がプロセスの外に一度も出ない
からです。モック化されたクライアントによるテストスイートは、この失敗モードを構造上検出できません。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **使い捨ての GitHub OAuth App を用意し、一度だけ捕捉する**：テスト専用の最小限の OAuth App を登録します。
  クライアント ID とシークレットは、一度きりの手動捕捉にのみ使い、CI には一切保存しません。
  - メンテナが手動で、その App に対して実際の認可コード交換を一度完了します。そして、生の HTTP
    レスポンスを保存します。トークン交換、ユーザー情報取得、代表的な org 一覧ページの3つです。
  - レスポンスをコミットする前に、実際のアクセストークンと、実際の識別情報のすべてを、フィクスチャ用の値へ
    置き換えます。識別情報とは、login、org 識別子、数値のユーザー ID、メールアドレス、氏名、アバター URL、
    および GitHub が返すそのほかの個人情報です。
- **CI のライブログインではなく、捕捉したレスポンスを実際のコードに対して再生する**：最初の認可 `code` を
  得るには、人間が GitHub のホスト済みログイン・同意画面を完了する必要があります。
  - これを CI ランナーからスクリプト化すると、実アカウントへプログラム的にログインすることになります。
    GitHub の 2FA、デバイス確認、CAPTCHA が、CI の IP に対して予測不能に発生するおそれがあります。
  - これは、本項目がまさに避けようとしている種類の不安定さです。しかも、OAuth クライアントシークレット
    よりはるかに機微なシークレットを、危険にさらします。
  - 代わりに、`httpx` のトランスポート境界（`respx` または自作の `httpx.MockTransport`）でインターセプト
    します。捕捉した実際のレスポンスを、実際の `GitHubOAuthClient`、`OAuth2Client`、`_fetch_orgs` へ再生
    します。
  - これにより、その既知の実際の形状に対する Authlib の呼び出しシグネチャの破壊を、CI 上のライブな
    ネットワーク呼び出しや認証情報なしで検出できます。ただし、*将来の* GitHub のレスポンス形状の変更は、
    フィクスチャを再捕捉するまで見えないままです。
- **モック化されたクライアントによるテストはそのまま残す**：`FakeOAuthClient`、`_RaisingOAuthClient`、
  `_PagingClient` は、すでにログインフロー自体のロジックとそのエラー経路をカバーしています。本項目は、
  これらを置き換えず、捕捉した実レスポンスによるフィクスチャをその隣に追加します。

## 検討した代替案

- **CI 上で実際にスクリプト化されたヘッドレスブラウザログインを駆動する**：これが、当初検討した設計
  でした。しかし最初の認可 `code` は、人間が GitHub のホスト済みログイン・同意画面を完了して初めて得られ
  ます。これを CI からスクリプト化すると、実アカウントの認証情報を保持して駆動することになります。しかも
  GitHub の防御機構（2FA、デバイス確認、CAPTCHA）は、予測不能に発動し得ます。この設計は、本項目が解決しよう
  としている問題より悪い、不安定さとシークレット管理の問題を抱えます。
- **ログインフロー自体のロジックがユニットテストされていることを根拠に、モック化されたクライアントによる
  テストを信頼する**：用意したクライアントレスポンスに対して、フローのロジックが正しいとします。それは、
  実際の Authlib と `httpx` のスタックが、GitHub の実際の API が返すものをパースできるかを何も語りません。
  その性質こそ、本項目が検証するものです。
- **OAuth2Client の契約については Authlib 自身のテストスイートに任せる**：Authlib のテストがカバーするのは
  Authlib 自体です。`serve` 自身の `GitHubOAuthClient` ラッパーと `_fetch_orgs` のページネーションロジック
  が、GitHub の実際のレスポンスを正しく処理できるかは、何も語りません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 使い捨ての GitHub OAuth App を登録し、実際のトークン交換・ユーザー情報取得・org 一覧の
  レスポンスを一度手動で捕捉する。
- [ ] 捕捉したレスポンスを、`httpx` のトランスポートインターセプト経由で実際の
  `GitHubOAuthClient`/`_fetch_orgs` に対して再生する。
- [ ] モック化されたクライアントによるテストはそのまま残す。

## 参考

- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- `bajutsu/serve/server/oauth.py`、`tests/serve/test_oauth.py`
  （`FakeOAuthClient`、`_RaisingOAuthClient`、`_PagingClient`）

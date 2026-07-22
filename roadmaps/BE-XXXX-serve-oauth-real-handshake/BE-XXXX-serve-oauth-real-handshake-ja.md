[English](BE-XXXX-serve-oauth-real-handshake.md) · **日本語**

# BE-XXXX — serve の GitHub ログインに対する実 OAuth ハンドシェイク検証

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-serve-oauth-real-handshake-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | 検証とカバレッジ |
| 関連 | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md), [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`serve/server/oauth.py` の `GitHubOAuthClient` は、Authlib の `OAuth2Client` をラップして `serve`
の GitHub ログインを駆動します。`tests/serve/test_oauth.py` のどのテストも、この実際のクラスを
インスタンス化したり駆動したりすることはありません。すべてのテストが `FakeOAuthClient`、
`_RaisingOAuthClient`、あるいは `httpx` の代役を務める手書きの `_PagingClient` に置き換えられて
います。本項目は、使い捨ての GitHub OAuth App に対して本物のハンドシェイクを一度捕捉し、それを
実際のクライアントコードに対して再生します。

## 動機

これらの fake は、`serve` のログインフローが `GitHubOAuthClient` の返す内容をまさしく呼び出して
いることを証明します。しかし、証明できないことがあります。実際の Authlib `OAuth2Client` を
実際の `httpx` の上でラップした実際のクラスが、GitHub に対して本当にトークン交換を完了できるか
という点です。あるいは、`_fetch_orgs` が手組みの `_PagingClient` の代役ではなく GitHub の実際の
org 一覧レスポンス形状をそもそもパースできるかという点も証明されません。Authlib のバージョンアップに
よるトークン交換の呼び出しシグネチャの変更や GitHub の OAuth レスポンス形状の変更は、いずれもこの
テストスイートからは見えません。処理がプロセスの外に一度も出ないからです。これはまさに、モック化
されたクライアントによるテストスイートが構造上検出できない失敗モードです。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **使い捨ての GitHub OAuth App を用意し、一度だけ捕捉する**：テスト専用の最小限の OAuth App を
  登録します（クライアント ID・シークレットは一度きりの手動捕捉にのみ使い、CI には一切保存しません）。
  メンテナが手動で、その App に対して実際の認可コード交換を一度完了し、生の HTTP レスポンス
  （トークン交換、ユーザー情報取得、代表的な org 一覧ページ）を保存します。実際のアクセストークンだけでなく、
  login/org 識別子・数値のユーザー ID・メールアドレス・氏名・アバター URL など、レスポンスに含まれる
  あらゆる実際の識別情報を、保存する前にフィクスチャ用の値へ置き換えます。
- **CI のライブログインではなく、捕捉したレスポンスを実際のコードに対して再生する**：最初の
  認可 `code` を得るには、人間が GitHub のホスト済みログイン・同意画面を完了する必要があります。
  これを CI ランナーからスクリプト化するということは、実アカウントへプログラム的にログインする
  ことを意味し、GitHub の 2FA、デバイス確認、CAPTCHA が CI の IP に対して予測不能に発生する
  リスクを負います。これは本項目がまさに避けようとしている種類の不安定さであり、しかもここでの
  OAuth クライアントシークレットよりもはるかに機微なシークレットを扱うことになります。代わりに、
  `httpx` のトランスポート境界（`respx` または自作の `httpx.MockTransport`）でインターセプトし、
  捕捉した実際のレスポンスを実際の `GitHubOAuthClient`/`OAuth2Client`/`_fetch_orgs` へ再生する
  ことで、その既知の実際の形状に対する Authlib の呼び出しシグネチャの破壊は、CI 上のライブな
  ネットワーク呼び出しや認証情報を一切必要とせずに検出できます。ただし*将来の* GitHub の
  レスポンス形状の変更は、フィクスチャを再捕捉するまで見えないままです。
- **モック化されたクライアントによるテストはそのまま残す**：`FakeOAuthClient`、
  `_RaisingOAuthClient`、`_PagingClient` はすでにログインフロー自体のロジックとそのエラー経路を
  カバーしています。本項目はこれらを置き換えるのではなく、捕捉した実レスポンスによる
  フィクスチャをその隣に追加します。

## 検討した代替案

- **CI 上で実際にスクリプト化されたヘッドレスブラウザログインを駆動する**：これが当初検討した
  設計でした。しかし最初の認可 `code` は、人間が GitHub のホスト済みログイン・同意画面を完了して初めて
  得られるものであり、これを CI からスクリプト化するということは、実アカウントの認証情報を
  保持して駆動することを意味します。しかも GitHub の防御機構（2FA、デバイス確認、CAPTCHA）は
  予測不能に発動しえます。この設計は、本項目が解決しようとしている不安定さより悪い、不安定さと
  シークレット管理の問題です。
- **ログインフロー自体のロジックがユニットテストされていることを根拠に、モック化されたクライアント
  によるテストを信頼する**：デフォルトのクライアントレスポンスに対してフローのロジックが正しいことは、
  実際の Authlib/`httpx` のスタックが GitHub の実際の API が返すものをまさしくパースできるかどうかとは
  無関係です。それこそが本項目が検証する性質です。
- **OAuth2Client の契約については Authlib 自身のテストスイートに任せる**：Authlib のテストは
  Authlib 自体をカバーするものであり、`serve` 自身の `GitHubOAuthClient` ラッパーと `_fetch_orgs`
  のページネーションロジックが、GitHub の実際のレスポンスをまさしく処理できるかどうかについては
  何も語りません。

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

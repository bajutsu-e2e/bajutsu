[English](BE-0302-config-source-real-repo-fetch.md) · **日本語**

# BE-0302 — config source の実リポジトリ fetch 検証

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0302](BE-0302-config-source-real-repo-fetch-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0302") |
| トピック | config の取得元 |
| 関連 | [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md), [BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`config_source.py` の `materialize()` は、Git ホストから config を取得します。これにより、チームは
ローカルのチェックアウトではなく、リモートリポジトリを `bajutsu` の対象にできます。

テストモジュール自身が、「fake transport を注入する…ネットワークや `git` バイナリのいずれも使わない」と
明言しています。`materialize()` のテストはすべて、fake の `Transport` を渡しています。
`_GitHubTransport` の実際の `urllib.request` ベースの実装が検証されるのは、HTTP エラーのマッピングだけです。
その検証でも、monkeypatch した `urlopen` に、あらかじめ用意したエラーを送出させています。実際のリポジトリ
から実際の tarball を取得するテストは、1つもありません。本項目はそれを追加します。

## 動機

fake の transport が証明するのは、`materialize()` が transport から渡されたバイト列を、動く config ツリーへ
組み立てられることです。これは組み立てロジックに対する、実質的で有用なカバレッジです。

一方で、`_GitHubTransport` 自体については何も証明しません。

- 実際の GitHub の tarball URL のリダイレクトチェインが、正しくたどられるか。
- 実際のレスポンスの content-type や圧縮が、想定どおりに扱われるか。
- 実際のレート制限や認証失敗のレスポンスが、未処理の例外ではなく正しいエラーへマッピングされるか。

同じ形のギャップは、GitHub App のトークンフローにもあります。モックが返すのは、その作者が「実際のホストは
こう返すはずだ」と信じている内容だけです。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **実際の使い捨てテスト用リポジトリ**：小さく安定した低権限の公開リポジトリを、取得対象とします。代案は、
  専用の使い捨て GitHub App をインストールした非公開リポジトリです。
  - `_GitHubTransport` は、トークン未設定でもすでに未認証で動作します。GitHub の commits / tarball
    エンドポイントも、公開リポジトリに対しては認証を要求しません。したがって公開リポジトリ版は、fetch
    そのものに認証情報を必要としません。
  - そこで personal access token（PAT）を使う目的は、認証済みの高いレート制限を得ることだけです。GitHub の
    未認証枠は1時間あたり60リクエストで、Actions ランナーが IP を共有する状況ではきつくなりがちです。
    PAT は任意です。
  - 実際に使う認証情報は、専用のリポジトリシークレットとして保管します。公開リポジトリ版では、そのリポジトリ
    1つだけに読み取り専用でスコープした fine-grained PAT です。非公開リポジトリ版では、使い捨て App の
    秘密鍵です。
  - テスト用リポジトリ（または App）の所有者が、シークレットをローテーションします。
- **ライブ fetch テスト**：そのリポジトリに対して、実際の `_GitHubTransport` を通して `materialize()` を
  駆動します。ネットワークアクセスまたは関連する認証情報がないときは、テストをスキップします。取得した
  config ツリーが実際のリポジトリの内容と一致することを検証します。
- **エラー経路も実際に検証する**：可能な場合は、実際の 404 や 403 を誘発します。意図的に誤った ref や、
  権限のない非公開リポジトリを使います。monkeypatch した `HTTPError` だけには頼りません。`_GitHubTransport`
  のテストが想定するとおりに、実際のエラー面がマッピングされることを確認します。
- **恒久的にゲート対象外とする**：
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
  の、新しいバックエンドのカバレッジをまずシグナルとして着地させる前例に従います。
  - BE-0282 自身の `network (playwright)` ジョブは、シークレットを必要としません。そのため、フォークから
    のものを含め、どんな `pull_request` でも安全にトリガーできます。
  - このジョブは実際の認証情報を必要とします。トリガーは `main` への `push`（または `schedule`）のみとし、
    `pull_request` では実行しません。`roadmap-id.yml` は、`AUTOMATION_BOT_PRIVATE_KEY` /
    `AUTOMATION_BOT_APP_ID` をフォークからの実行にさらさないために、同じトリガー制限を使っています。
  - マージ後にしか走らないチェックは、PR の head SHA ではなく `main` のコミットに対して報告されます。
    BE-0282 のジョブとは違い、構造上、必須ステータスチェックになり得ません。
  - これは必須化へ向けた途中経過ではなく、この設計が恒久的にとる形です。変える方法は、認証情報を PR
    トリガーの実行にさらすことしかありません。

## 検討した代替案

- **組み立てロジックが十分にカバーされていることを根拠に、fake transport のテストを信頼する**：
  組み立てが正しいという前提は、そもそも transport が正しいバイト列を渡していたことを前提とします。実際の
  transport の実装が、実際のホストからそのバイト列を生成できるかは、何も語りません。
- **GitHub App のトークンフロー統合テストだけで間接的にカバーする**：そのテストが検証するのは、トークン
  フローです。本項目は、トークンフローの上に構築される fetch-and-materialize の経路を検証します。両方が
  必要です。動くトークンと壊れた fetch、動く fetch と壊れたトークンは、それぞれ独立して観測しうる失敗
  モードだからです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 実際の使い捨てテスト用リポジトリを取得対象として指定する。
- [ ] 実際の `_GitHubTransport` を通して `materialize()` を駆動するライブ fetch テストを追加する。
- [ ] monkeypatch したものだけでなく、実際のエラーレスポンス（404/403）もカバーする。
- [ ] ゲート対象外のシグナルとして CI に組み込む。

## 参考

- [BE-0063 — Git リポジトリ + ref から config（とシナリオ一式）を読み込む](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)
- [BE-0224 — GitHub を取得元とする config での private リポジトリへのアクセス権限の付与](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth-ja.md)
- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- `bajutsu/config_source.py`、`tests/test_config_source.py`、`.github/workflows/roadmap-id.yml`

[English](BE-XXXX-config-source-real-repo-fetch.md) · **日本語**

# BE-XXXX — config source の実リポジトリ fetch 検証

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-config-source-real-repo-fetch-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | config の取得元 |
| 関連 | [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md), [BE-0224](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`config_source.py` の `materialize()` は、チームがローカルのチェックアウトではなくリモート
リポジトリを `bajutsu` の対象にできるよう、Git ホストから config を取得します。このテスト
モジュール自身が、「fake transport を注入する…ネットワークや `git` バイナリのいずれも使わない」と
明言しているとおり、`materialize()` のテストはすべて fake の `Transport` を渡しており、
`_GitHubTransport` の実際の `urllib.request` ベースの実装が検証されるのは、monkeypatch した
`urlopen` にデフォルトのエラーを送出させる HTTP エラーマッピングのケースだけです。実際のリポジトリ
から実際の tarball を取得するテストは1つもありません。本項目はそれを追加します。

## 動機

fake の transport は、`materialize()` が transport から渡されたバイト列をまさしく動く config
ツリーへ組み立てられることを証明します。これは組み立てロジックに対する実質的で有用な
カバレッジです。しかし `_GitHubTransport` 自体、すなわち実際の GitHub の tarball URL の
リダイレクトチェインがまさしくたどられるか、実際のレスポンスの content-type や圧縮が想定どおりに
扱われるか、実際のレート制限や認証失敗のレスポンスが未処理の例外ではなく正しいエラーへ
マッピングされるかについては何も証明しません。同じ形のギャップは GitHub App のトークンフローにも
現れます。モックは、その作者が「実際のホストはこう返すはずだ」と信じている内容しか返しません。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **実際の使い捨てテスト用リポジトリ**：小さく安定した、低権限の公開リポジトリ（あるいは、
  専用の使い捨て GitHub App をインストールした非公開リポジトリ）を取得対象とします。
  `_GitHubTransport` はトークン未設定でもすでに未認証で動作し、GitHub の commits / tarball
  エンドポイントも公開リポジトリに対しては認証を要求しません。したがって公開リポジトリ版は
  fetch そのものに認証情報を必要とせず、そこで PAT を使うのはあくまで認証済みの高いレート制限
  （GitHub の未認証枠は1時間あたり60リクエストで、Actions ランナーが IP を共有する状況では
  きつくなりがちです）を得るためであり、任意です。実際に使う認証情報は専用のリポジトリ
  シークレットとして保管し、テスト用リポジトリ（または App）の所有者がローテーションします。
  公開リポジトリ版はそのリポジトリ1つだけに読み取り専用でスコープした fine-grained
  personal access token（PAT）、非公開リポジトリ版は使い捨て App の秘密鍵を使います。
- **ライブ fetch テスト**：そのリポジトリに対して実際の `_GitHubTransport` を通して
  `materialize()` を駆動するテストを追加します。ネットワークアクセスまたは関連する認証情報が
  ないときはスキップし、取得した config ツリーが実際のリポジトリの内容と一致することを検証します。
- **エラー経路も実際に検証する**：可能な場合は、monkeypatch した `HTTPError` だけでなく、
  意図的に誤った ref や権限のない非公開リポジトリで実際の 404/403 を誘発し、`_GitHubTransport` の
  テストが想定するとおりに実際のエラー面がマッピングされることを確認します。
- **まずゲート対象外とする**：
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
  の前例に従い、新しいジョブをまず CI のシグナルとして着地させ、必須化はそのあとで検討します。
  トリガーは `main` への `push`（または `schedule`）とし、`pull_request` では実行しません。
  `roadmap-id.yml` が `AUTOMATION_BOT_PRIVATE_KEY` / `AUTOMATION_BOT_APP_ID` をフォークからの
  実行にさらさないためにすでに使っているトリガー制限と同じ考え方で、非公開リポジトリ版の
  App の認証情報にも同じ保護を与えます。

## 検討した代替案

- **組み立てロジックが十分にカバーされていることを根拠に、fake transport のテストを信頼する**：
  組み立てが正しいという前提は、そもそも transport が正しいバイト列を渡していたことを前提と
  します。実際の transport の実装が、実際のホストからそのバイト列を本当に生成できるかどうかに
  ついては何も語りません。
- **GitHub App のトークンフロー統合テストだけで間接的にカバーする**：それはトークンフローを検証する
  ものであり、本項目はその上に構築される fetch-and-materialize の経路を検証するものです。両方が
  必要です。動くトークンと壊れた fetch、動く fetch と壊れたトークンは、それぞれ独立して
  観測しうる失敗モードだからです。

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

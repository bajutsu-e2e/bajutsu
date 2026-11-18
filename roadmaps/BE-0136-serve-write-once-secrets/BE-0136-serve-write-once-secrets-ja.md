[English](BE-0136-serve-write-once-secrets.md) · **日本語**

# BE-0136 — serve の秘密情報ストアを書き込み専用にする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0136](BE-0136-serve-write-once-secrets-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0136") |
| 実装 PR | [#661](https://github.com/bajutsu-e2e/bajutsu/pull/661) |
| トピック | セキュリティ強化 |
| 関連 | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)、[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)、[BE-0032](../BE-0032-secret-variables/BE-0032-secret-variables-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`serve` は、Web UI から管理者がオペレーター向けの認証情報を設定できます。現状は Claude の API キー
だけです。GitHub Actions の Secrets と違い、いったん設定した値を、認証済みの呼び出し元なら誰でも平文で
読み出せてしまいます。

本提案は、`serve` のオペレーター向け秘密情報を**書き込み専用**にします。管理者は値を設定・上書きできます。
どのロールであっても、どのエンドポイントも平文を二度と返しません。返すのはマスク済みのプレビューだけです。
hosted な `server` バックエンドでは、値をプロセスのメモリに置くだけでなく、暗号化して永続化します。

## 動機

`bajutsu/serve/operations.py:237` の `api_key_info` と `bajutsu/serve/operations.py:598` の
`set_api_key` は、`serve` の設定パネルにある「Claude API キー」欄を支えています。この欄には、秘密情報
管理としてはあってはならない非対称性があります。

- **設定**（`POST /api/apikey`）には `admin` ロールが必要です。`/api/apikey` は `authz.py:122` の
  `_ADMIN_PATHS` に含まれているからです。ただし、このゲートが効くのは、データベースバックエンドの
  `Repository` が有効な OAuth セッションに限られます（`authz.py:164` の `forbidden_for_role`）。
  Bearer トークンによるリクエストには身元がありません。オペレーター自身の認証情報なので、意図的に
  フルアクセスのままです。データベースがそもそも接続されていないローカル `serve` では、ロールによる
  制限自体が働きません。
- **平文での読み出し**（`GET /api/apikey?reveal=1`）は、認証の方式によらず、ロールをまったく要求
  しません。`authz.py:147` の `required_role` は、すべての `GET` に対して `None` を返します。そのため、
  BE-0015 7c-2 で定義された読み取り専用ロール `viewer` の OAuth セッションでも、より高い権限を持つ
  管理者が設定した認証情報を、そのまま取得できてしまいます。同じエンドポイントの書き込み側であれば、
  拒否される操作です。
- **保存方式**は `set_api_key` の `os.environ[var] = value` で、値は `serve` プロセスのメモリに
  乗るだけです。再起動すれば失われます。BE-0015 §4（「Control-plane scale-out」）が計画している
  コントロールプレーンの複数レプリカ間では共有されません。組織単位のスコープも持てません。

GitHub Actions の Secrets は、この点を正しく扱っています。組織のオーナーは値を設定・上書きできますが、
オーナー自身であっても、UI や API 経由でその値を二度と参照できません。`serve` のオペレーター向け
認証情報に必要なのは、この「書き込み専用で、二度と読み出せない」という形です。ロードマップは、この
ギャップをすでに名指ししたまま未解決にしています。

- BE-0015 の Secrets の行は、公開前のセキュリティ強化として「**組織ごとに持ち込む
  `ANTHROPIC_API_KEY`**」を求めています。マルチテナンシーの節でも、「秘密情報は今のところサーバー単位の
  単一の API キーであり、組織ごとに注入される秘密情報ではない」と認めています。
- BE-0016 のセルフホスティングの一覧表は、Secrets の行を「Shipped today: `.env`」としています。
  つまり、compose スタックの背後に、管理された秘密情報の層は実質存在しません。

どちらも同じ欠落を指しています。必要なのは本物の秘密情報の仕組みです。現状は、プロセス全体が共有し、
どの viewer でも読み返せてしまう、単一の可変な環境変数にすぎません。

## 詳細設計

**1. 既存のパターンに従った `SecretStore` の導入。** `bajutsu/serve/` はすでに `ServeState` の背後に
5 つの差し替え口（`RunExecutor` / `LogBus` / `ArtifactStore` / `ScenarioStore` / `Repository`）を持って
います。いずれも `Protocol` とローカル実装・hosted 実装の組で、`_build_server_state` が選択します。
ここに 6 つ目として `SecretStore` を追加します。操作は `set(name, value)` と
`describe(name) -> マスク済みプレビュー | None` の 2 つだけです。HTTP ハンドラから到達できる
インターフェースに、`get(name) -> value` は意図的に含めません。平文が存在するのは、それを消費する
コード経路（`record` / `run` / `crawl` のサブプロセスを起動する箇所）の内側だけです。レスポンスとして
返る経路には、決して現れません。

**2. ローカル実装は今の挙動のまま。** ローカルの `serve` は、これまでどおり値を `os.environ` に、
プロセスの生存期間だけ保持します（メモリ内のみという、現在ドキュメントに書かれている挙動と同じです）。
単一ユーザーのローカル利用では、挙動は変わりません。`api_key_info` / `set_api_key`（および将来追加する
秘密情報）が 1 つのインターフェースを経由するように、差し替え口の内側へ移すだけです。

**3. hosted 実装は組織ごとに暗号化して永続化。** `server` バックエンド（`bajutsu/serve/server/db.py`）に
`secrets` テーブルを追加します。列は `org_id`、`name`、`ciphertext`、`updated_at`、`updated_by` です。
既存の `projects` / `runs` テーブルと同じく `org_id` でスコープします。BE-0015 §8 の組織ごとの
ストレージが今日すでに解決に使っている列を、そのまま流用します。

値は認証付き暗号化（authenticated encryption）方式である `cryptography` パッケージの `Fernet` で暗号化
します。`Fernet` 自体は、既存の `db` extra の下に新規依存として追加します。鍵には、オペレーターが
用意するマスターキー（`BAJUTSU_SECRETS_KEY`）を使います。データベースの外側で用意する運用上の秘密
情報という点で、`BAJUTSU_DATABASE_URL` と同じ性質を持ちます。たとえば、デプロイ先のプラットフォーム
自身の秘密情報管理機構から供給します。

この設計は、BE-0015 の「組織ごとに持ち込む API キー」というギャップをそのまま解決します。今日の単一の
Claude キーを保持しているのと同じテーブルと差し替え口が、組織ごとに `name` でスコープされた秘密情報へ
一般化されるからです。

**4. reveal は緩めるのではなく廃止します。** `GET /api/apikey` からは `reveal` パラメーターを完全に
取り除き、常に `{"set": bool, "masked": "sk-...ab12"}` だけを返します。管理者を含め、どのロールであっても
`value` フィールドは二度と返しません。キーを更新したい管理者は、`POST /api/apikey` で上書きするだけで
よく、古い値を読み返す必要はありません。GitHub Actions の Secrets の挙動と同じです。

この廃止は、既存の `reveal` クエリーパラメーターに対する破壊的変更です。`docs/getting-started.md` の
「reveal トグルでマスクを外して表示する」という記述と、設定パネルの UI（`serve.js`）の reveal 用の
操作を取り除きます。

**5. 単一の秘密情報にとどめず一般化します。** `api_key_info` / `set_api_key` は、
`SecretStore.describe("aiApiKey")` / `.set("aiApiKey", value)` を呼ぶだけの薄いラッパーになります。
将来 2 つ目の秘密情報（たとえば Bedrock 用の AWS 認証情報や、ターゲットアプリ自身の API 認証情報）を
追加する際も、同じストアと同じ「書き込み専用」の性質をそのまま再利用できます。新たな配線は不要です。

**6. テスト。** serve の HTTP ハーネスを拡張します。Simulator も、稼働中の Postgres も不要です。
`Repository` に対する SQLite と同様に、hosted 版の `SecretStore` のテストダブルが同じ契約で動くから
です。確認するのは次の 4 点です。

- `POST /api/apikey` が引き続き admin を要求すること。
- `GET /api/apikey` が、ロールやクエリーパラメーターに関わらず `value` キーを一切含まないこと。
- 設定してから読み出す一連の操作が、マスク済みプレビューだけを返すこと。
- hosted 実装では、保存された `ciphertext` 列に平文の部分文字列が含まれないこと。

**7. ドキュメント。** `docs/getting-started.md` と `docs/self-hosting.md` を、両言語で更新します。
`docs/getting-started.md` では、reveal トグルの説明を削除します。`docs/self-hosting.md` では、Secrets の行を
「Shipped today: `.env`」から新しい暗号化ストアへ更新し、`BAJUTSU_SECRETS_KEY` の用意の仕方を追記します。

この変更は、決定的な `run` / CI のゲート、シナリオのスキーマ、ランナー、ドライバーのいずれにも触れ
ません。あくまで `serve` のオペレーター向け設定という範囲に閉じています。ここで何をしても合否判定に
影響しないという原則と整合します。

## 検討した代替案

- **reveal を廃止せず admin ロール限定にする案。** 弱い修正として却下します。viewer が読み出せて
  しまうギャップは塞げますが、管理者（あるいは管理者セッションを乗っ取った何者か）が平文を往復
  取得できる経路は残ります。GitHub Actions の Secrets は、設定した本人を含めて誰にも開示しません。
  この基準に合わせてこそ、単に少し保護を強めた設定項目ではなく、秘密情報管理の仕組みだと言えます。
- **クラウドの秘密情報管理サービス（Doppler、AWS Secrets Manager、Vault）を使う案。** BE-0015 の
  Secrets の行が、もともと挙げていた選択肢です。全面的には却下しません。大規模かつマルチクラウドな
  デプロイでは、今後も適切な選択肢であり続けます。ただし、秘密情報を 1 つ設定するだけのために外部の
  依存とアカウントが先に必要になります。BE-0016 がすでに提供しているセルフホストの単一ノード構成
  （[`deploy/self-host/`](../../deploy/self-host/)）には不向きです。データベース内に暗号化して保存する
  方式なら、オペレーターがすでに `BAJUTSU_DATABASE_URL` と並べて管理しているマスターキーが 1 つ増える
  だけで済みます。同じ `SecretStore` の差し替え口の内側で、あとから Doppler や Vault に切り替える道も
  塞ぎません。
- **hosted バックエンドでも値を `os.environ` に置いたまま、RBAC だけ締める案。** 却下します。
  再起動で失われること、BE-0015 §4 が計画するコントロールプレーンの複数レプリカ間で共有できないこと、
  組織単位のスコープを持てないことの 3 つの欠落が残ります。組織ごとに暗号化して保存するテーブルは、
  この 3 つをまとめて解決します。
- **セッショントークンやリクエストごとの値から導いた鍵で暗号化する案。** 却下します。トークンが
  ローテーションした瞬間や、セッションが終了した瞬間に、秘密情報が読めなくなってしまいます。
  起動される `record` / `run` ジョブが前提にしている「一度設定すれば動き続ける」という要件を壊します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `SecretStore` の Protocol（`set` / `describe`。HTTP ハンドラから到達できる `get` は持たない）
- [x] ローカル実装 — 今の `os.environ` による挙動を差し替え口の内側へ移す
- [x] hosted 実装 — `secrets` テーブル（`org_id`、`name`、`ciphertext`）、`BAJUTSU_SECRETS_KEY`
      で鍵付けした `Fernet` 暗号化、`db` extra の下への追加
- [x] `GET /api/apikey` から `reveal` を削除し、どのロールでも平文の `value` を返さないようにする
- [x] `api_key_info` / `set_api_key` を `SecretStore.describe("aiApiKey")` /
      `.set("aiApiKey", value)` へ一般化する
- [x] テスト — ロール・reveal の削除、設定してから読み出す一連の操作、hosted 側の `ciphertext` に
      平文が含まれないことの確認
- [x] ドキュメント更新（`docs/getting-started.md`、`docs/self-hosting.md`）を両言語で

**スコープ外（フォローアップ）。** hosted のワーカーが保存済みシークレットを*消費*する配線は、本項目に
含めず、現状のままとします。ワーカーは自身のプロセスで動き、資格情報を自身のデプロイ環境から読みます。
UI から設定した値は、ここで org ごとに write-once かつ暗号化して保存します。ワーカーが起動するジョブへ
org 単位で注入する部分は、BE-0015 の org 別キーの作業に属します。

### ログ

- 実装 PR は、MECE の内訳をまとめて一つの変更で届けます。[#661](https://github.com/bajutsu-e2e/bajutsu/pull/661) で出荷しました。内容は次のとおりです。
  - `SecretStore` の差し替え口（`bajutsu/serve/secrets.py`）とローカルの `EnvSecretStore`
  - `secrets` テーブル（マイグレーション `0006`）と `Fernet` 暗号化を備えた hosted の `DbSecretStore`（`bajutsu/serve/server/secrets.py`）
  - `operations` と両方の HTTP シェルと Web UI にまたがる `reveal` の削除
  - 両言語のドキュメント

## 参考

`bajutsu/serve/operations.py:237`（`api_key_info`）、`bajutsu/serve/operations.py:598`
（`set_api_key`）、`bajutsu/serve/authz.py:122`（`_ADMIN_PATHS`）および `:147`
（`required_role`）、`bajutsu/serve/server/db.py`（本提案が拡張する `Repository` の差し替え口）。
関連項目として、[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)
（本提案が解決する「組織ごとに持ち込む API キー」というギャップを名指ししています）、
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)
（セルフホストの Secrets を、管理されていない `.env` のままだとしています）、
[BE-0032](../BE-0032-secret-variables/BE-0032-secret-variables-ja.md)
（別の層の話です。こちらは *シナリオ* が実行時に秘密情報をどう使うかであり、`serve` が
オペレーターの認証情報をどう保存するかではありません）。

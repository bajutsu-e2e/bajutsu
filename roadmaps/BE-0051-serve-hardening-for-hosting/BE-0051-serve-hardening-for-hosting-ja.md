[English](BE-0051-serve-hardening-for-hosting.md) · **日本語**

# BE-0051 — ホスティングのための serve ハードニング（認証・入力検証）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0051](BE-0051-serve-hardening-for-hosting-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0051") |
| 実装 PR | [#92](https://github.com/bajutsu-e2e/bajutsu/pull/92)（スライス 1）、[#94](https://github.com/bajutsu-e2e/bajutsu/pull/94)（スライス 2）、[#95](https://github.com/bajutsu-e2e/bajutsu/pull/95)（スライス 3）、[#96](https://github.com/bajutsu-e2e/bajutsu/pull/96)（スライス 4）、[#97](https://github.com/bajutsu-e2e/bajutsu/pull/97)（スライス 5）、[#114](https://github.com/bajutsu-e2e/bajutsu/pull/114)（スライス 3、5 の `/api/crawl` 対応） |
| トピック | Web UI のホスティング |
| 関連 | [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md) |
<!-- /BE-METADATA -->

## はじめに

stdlib の `bajutsu serve`（`bajutsu/serve/`）が今安全なのは、**localhost 限定かつ単一ユーザー**だからです。認証はありません。下記スライス 1（#92）が封じ込めるまで、`/api/run` はクライアント指定の scenario パスを受け付けていました。

ホスティングの 2 つの提案は、`serve` を loopback の外へ到達させる前に**必須**となる一連のセキュリティ修正を挙げています。公開/クラウドの [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) と、セルフホストの [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) です。本項目は、それらの修正を**既存の** stdlib サーバ上の単一のハードニングトラックとしてまとめ、段階的に出荷します。決定的コアは一切変えません。各スライスは、Simulator なしで Linux ゲートでテストできます。

本項目は、BE-0015 と BE-0016 の**前提レイヤ**です。2 つの提案は、FastAPI コントロールプレーン、macOS ワーカープール、OAuth、オブジェクトストレージからなる完全なホスティング構成を記述します。本項目は、それらが存在する前に、いま出荷しているサーバを公開しても安全にします。

## 動機

`serve` は `bajutsu run` を起動するだけの薄いランチャです。loopback 以外のネットワークにそのまま公開すると、2 つの危険な性質があります。

- **認証がない。** すべてのエンドポイントが開いています。ポートに到達できれば、誰でも run を起動し、run 成果物を読み、scenario ファイルを読み書きできます。
- **クライアントが実行面を制御できる。** `/api/run` は `body["scenario"]` をそのまま `bajutsu run` の argv へ渡していました。パスがアプリの scenarios dir 内にあるかどうかは検査していません。`backend` と `udid` も自由入力でした。この 2 点が合わさり、任意パス実行の面になっていました。

これらのリスクは机上の話ではありません。[BE-0015 §セキュリティハードニング](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)と [BE-0016 Tier A](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) が、同じ項目をブロッカーとして挙げています。

現行サーバでこれらを塞ぐのは安価で、単独でも有用です。BE-0016 Tier A の Tailscale/LaunchAgent による単一 Mac 構成が、今日から安全になります。後のホスティングシステムにとっても、きれいな土台になります。

## 詳細設計

設計は、既存の `bajutsu/serve/` サーバ上のスライス列です。各スライスは独立して出荷できます。いずれも pass/fail や決定的コアに触れません。すべて、serve の HTTP ハーネスで Simulator なしにテストできます。

1. **`/api/run` の入力検証**（#92 で実装）。要求された scenario を、選択中アプリの scenarios dir のファイル名と突き合わせます。実行に使うパスは dir 列挙由来で、クライアントの文字列は使いません。既知のトークンでない `backend` や `udid` は拒否します。これで任意パス実行の面がなくなります。他のエンドポイント（`/api/config`、`/api/scenario`、`/api/approve`、`/runs/...` 配信）は、すでにパスを封じ込め済みです。
2. **トークン認証と非 loopback ガード**（#94 で実装）。任意の共有トークン（`--token` または `BAJUTSU_SERVE_TOKEN`）を、定数時間で比較します。API クライアントは `Bearer` ヘッダで提示します。ブラウザは、**POST のログインエンドポイントを通して HttpOnly かつ SameSite=Strict の Cookie を確立**します。クエリ文字列は履歴、ログ、`Referer` から漏えいするので、トークンは URL に載せません。**トークンなしで非 loopback host を bind するのは、起動時に拒否します**。無認証のまま誤って公開されることはありません。
3. **同じ入力検証を他の run 起動エンドポイントにも適用**（#95 で実装、`/api/crawl` は #114）。`/api/record` と `/api/crawl` に、スライス 1 と同じ `backend` と `udid` のトークン検証を入れます。
4. **CSRF 対策と標準セキュリティヘッダ**（#96 で実装）。Cookie 認証が入ったら、状態を変更する POST の保護が必要になります。保護には **Origin チェック**を使い、`Origin` があれば `Host` と一致することを求めます。このチェックは `SameSite=Strict` のセッション Cookie に重ねます。API クライアントは `Authorization` ヘッダで認証するので、Cookie を持ちません。標準セキュリティヘッダも付けます。Cookie が関わって初めて意味を持つので、スライス 2 の後に回しました。
5. **トークンまたは org 単位の run dispatch レート制限**（#97 で実装、`/api/crawl` は #114）。同時に実行中の run に上限を設け、1 人の呼び出し元が希少なデバイスを独占できないようにします。BE-0015 の per-org クォータの、軽量な前段です。

スライス 1 と 2 が、単一 Mac で tailnet から到達できる構成（BE-0016 Tier A）を安全にする最小の集合です。スライス 3 から 5 で、面を仕上げます。

マルチテナントに関わるものは、BE-0015 と BE-0016 に残します。per-org キー、ロールベースアクセス制御（RBAC）、オブジェクトストレージ、ワーカープールです。本項目は、あえて「出荷するサーバを、トークン付きでプライベートネットワークの背後に置いても安全」までで止めます。

## 検討した代替案

- **ハードニングを BE-0015 の完全なコントロールプレーンの一部としてのみ行う。** 却下しました。公開しても安全な単一 Mac サーバが、大規模で未構築の FastAPI、OAuth、ワーカープールの取り組みと結合してしまうためです。修正は小さく、現行サーバ単独でも有用です。BE-0016 Tier A を今日から安全にできます。独立した漸進トラックに属します。
- **stdlib サーバ上の OAuth と per-org RBAC。** 本レイヤでは却下しました。完全な identity は、ホスティングのコントロールプレーン（BE-0015）に属します。単一 Mac のプライベートネットワーク構成には、共有トークン 1 つが適切な重さです。新規依存なしで実装できます。
- **`0.0.0.0` に bind し、認証をネットワーク ACL やリバースプロキシに任せる。** 既定としては却下しました。設定ミス 1 つで公開されうる無認証サーバは、危険だからです。トークンなしの非 loopback bind を拒否すれば、安全な側が既定になります。リバースプロキシ（BE-0016 の Caddy basic-auth）は、上乗せの選択肢として残ります。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)（公開/クラウドホスティング）、
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)（セルフホスティング）、
`bajutsu/serve/`、[cli.md](../../docs/cli.md#serve)

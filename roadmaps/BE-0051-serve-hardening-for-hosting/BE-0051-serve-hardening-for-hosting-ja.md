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

stdlib の `bajutsu serve`（`bajutsu/serve/`）が今安全なのは、**localhost 限定、単一ユーザー**だからです。
認証は無く、`/api/run` は（下記スライス 1（#92）が封じ込めるまで）クライアント指定の scenario パスを
受け付けていました。ホスティングの 2 提案、すなわち
公開/クラウドの [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) と
セルフホストの [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) は、いずれも、
`serve` を loopback の外へ到達させる前に**必須**となる一連のセキュリティ修正を挙げています。本項目は、それらを
**既存の** stdlib サーバ上の単一のハードニングのトラックとしてまとめます。決定的コアは一切変えず、各スライスは
Simulator 無しで Linux ゲートでテストできます。

これは BE-0015/BE-0016 の**前提レイヤ**です。あれらは完全なホスティング構成（FastAPI コントロールプレーン、
macOS ワーカープール、OAuth、オブジェクトストレージ）を記述しますが、本項目はそれらが存在する前に、
**いま出荷しているサーバを公開しても安全にする**ことだけを扱います。

## 動機

`serve` は `bajutsu run` を起動するだけの薄いランチャです。そのまま公開すると、loopback 以外のネットワークでは
2 つの性質が危険です。

- **認証が無い。** すべてのエンドポイントが開いており、ポートに到達できれば誰でも run を起動し、run 成果物を読み、
  scenario ファイルを読み書きできます。
- **クライアントが実行面を制御できる。** `/api/run` は `body["scenario"]` を、アプリの scenarios dir 内かの
  チェック無しに `bajutsu run` の argv へ渡し、`backend` / `udid` も自由入力でした。これは任意パス実行の面です。

これらは机上の話ではなく、[BE-0015 §セキュリティハードニング](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)
と [BE-0016 Tier A](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) がブロッカーとして名指しする
項目そのものです。現行サーバでこれらを塞ぐのは安価で、単独でも有用（BE-0016 Tier A の Tailscale/LaunchAgent
単一 Mac 構成を今日から安全にする）であり、後のホスティングシステムの綺麗な土台になります。

## 詳細設計

既存 `bajutsu/serve/` サーバ上の、独立して出荷可能なスライス列です。いずれも pass/fail や決定的コアに触れず、serve の
HTTP ハーネスで Simulator 無しにテストできます。

1. **`/api/run` の入力検証**（#92 で実装）。要求された scenario を選択中アプリの scenarios dir の
   ファイル名と突き合わせ（実行に使うパスは dir 列挙由来で、クライアント文字列は使わない）、既知トークンでない
   `backend` / `udid` を拒否します。任意パス実行の面を排除します。他のエンドポイント（`/api/config`、`/api/scenario`、
   `/api/approve`、`/runs/...` 配信）は既にパスを封じ込め済みです。
2. **トークン認証 + 非 loopback ガード**（#94 で実装）。任意の共有トークン（`--token` / `BAJUTSU_SERVE_TOKEN`）を
   定数時間比較します。API クライアントは `Bearer` ヘッダで提示し、ブラウザは **POST のログインエンドポイント経由で
   HttpOnly かつ SameSite=Strict の Cookie を確立**します（トークンは URL に載せません。クエリ文字列は履歴、ログ、`Referer`
   から漏えいするため）。**非 loopback host をトークン無しで bind するのは起動時に拒否**するので、無認証で誤って
   公開されることがありません。
3. **同じ入力検証を他の run 起動エンドポイントにも適用**（#95 で実装、`/api/crawl` は #114）。`/api/record`
   と `/api/crawl` に、スライス 1 と同様の `backend` / `udid` のトークン検証を入れます。
4. **CSRF 対策 + 標準セキュリティヘッダ**（#96 で実装）。Cookie 認証が入ったら、状態変更 POST を **Origin チェック**
   （`Origin` があれば `Host` と一致すること）で保護し、`SameSite=Strict` セッション Cookie に重ねます。API
   クライアントは `Authorization` ヘッダで認証し、Cookie を持ちません。標準セキュリティヘッダを付与します。Cookie が
   関わって初めて意味を持つので、スライス 2 の後に回します。
5. **トークン/org 単位の run dispatch レート制限**（#97 で実装、`/api/crawl` は #114）。1 呼び出し元が
   （希少な）デバイスを独占できないよう同時/実行中 run を上限化します。BE-0015 の per-org クォータの軽量な前段です。

スライス 1–2 が、単一 Mac と tailnet 到達（BE-0016 Tier A）を安全にする最小集合です。3–5 は面を仕上げます。
マルチテナント（per-org キー、RBAC、オブジェクトストレージ、ワーカープール）は BE-0015/BE-0016 に残し、本項目は
あえて「出荷するサーバを、トークン付きでプライベートネットワークの背後に置いても安全」までで止めます。

## 検討した代替案

- **ハードニングを BE-0015 の完全なコントロールプレーンの一部としてのみ行う。** 却下しました。公開しても安全な単一 Mac
  サーバを、大規模で未構築の FastAPI/OAuth/ワーカープールの取り組みに結合してしまうためです。修正は小さく現行サーバ単独で
  有用（BE-0016 Tier A を今日安全にする）なので、独立した漸進トラックに属します。
- **stdlib サーバ上の OAuth / per-org RBAC。** 本レイヤでは却下しました。完全な identity はホスティングのコントロール
  プレーン（BE-0015）に属します。単一 Mac のプライベートネットワーク構成には共有トークン 1 つが適切な重さで、新規
  依存無しで実装できます。
- **`0.0.0.0` に bind し、認証はネットワーク ACL / リバースプロキシに任せる。** 既定としては却下しました。設定ミス 1 つで
  公開されうる無認証サーバは危険だからです。トークン無しの非 loopback bind を拒否すれば、安全側が既定になります。リバースプロキシ
  （BE-0016 の Caddy basic-auth）は上乗せの選択肢として残ります。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)（公開/クラウドホスティング）、
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)（セルフホスティング）、
`bajutsu/serve/`、[cli.md](../../docs/cli.md#serve)

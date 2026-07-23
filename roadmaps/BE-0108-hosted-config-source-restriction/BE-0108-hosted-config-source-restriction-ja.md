[English](BE-0108-hosted-config-source-restriction.md) · **日本語**

# BE-0108 — ホスティング時は config の取得元をアップロードと Git だけに絞る

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0108](BE-0108-hosted-config-source-restriction-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0108") |
| 実装 PR | [#648](https://github.com/bajutsu-e2e/bajutsu/pull/648) |
| トピック | Web UI のホスティング |
| 関連 | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md), [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md), [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md), [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Web UI の「Open config」ダイアログには、実行対象の config を bind する経路が三つあります。**Git
リポジトリ**（[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)）、
**アップロードした `.zip` バンドル**（[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)）、
そして **serve ホストの `--root` 以下を辿る**ファイルブラウザ（最初からある経路で、
`bajutsu/serve/operations.py` の `browse_fs` / `bind_config`）です。ファイルブラウザは自分のマシンで
動かすローカルの単一利用者向け `serve` では既定として妥当ですが、ホスティング時（`server` バックエンド、
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)）には意味を
なしません。ホスティング先のブラウザ利用者はホストのファイルシステムと何の関係もなく、運用者の
`--root` のディレクトリ一覧を認証済みの全利用者にさらすのは不要な露出面だからです。

この項目では、提示する config の取得元をデプロイ形態に応じて変えます。`server` バックエンドでは
「Open config」ダイアログが遠隔の利用者に意味のある二つの取得元、すなわち**アップロード**と **Git** だけ
を提示し、ファイルブラウザの取得元は UI から取り除いたうえで**サーバ側でも拒否します**。ローカル
バックエンドは変更しません。三つの取得元がそのまま残ります。スキーマ、runner、driver、決定論的な
ゲートはどれも変わらず、LLM をどこにも足しません。

## 動機

ファイルブラウザはローカル向けの利便機能ですが、それがホスティング時のデプロイにまで漏れ出ています。
そこでは役に立たないうえに、軽度ながら情報漏洩の露出面になります。

1. **ホスティング先の利用者はホストのファイルシステムと関係がありません。** `server` バックエンドでは、
   config もシナリオも `runs/` も、ブラウザ利用者が用意したわけではない共有ワーカー上にあります。利用者
   自身のスイートをそのホストに置く経路は、まさにアップロード（BE-0073）と Git（BE-0063）の二つです。
   運用者の `--root` を辿っても、利用者が所有するものは何一つ bind できません。運用者が手で置いた
   ファイルを bind できるだけで、これはホスティング時の想定された使い方ではありません。
2. **運用者の `--root` ツリーを認証済みの全利用者にさらします。** BE-0051 がブラウザを `--root` に閉じ込め
   たので任意パスへの脱出ではありませんが、複数利用者のホスティング環境では、ログイン済みの各利用者に
   対して運用者の管理下にあるツリーの一覧を見せ、その下の任意の config を bind できるようにしてしまいます。
   単一利用者のローカル `serve` ならそのツリーは運用者自身のマシンなので問題ありません。共有ホストでは、
   BE-0015 でも BE-0051 でも取り除かれない、避けられる露出です。
3. **UI で隠すだけでは見た目だけの対処になります。** `/api/fs`（ブラウズ）と `POST /api/config` の path
   分岐（パス指定での bind）は、手で組み立てたリクエストにはなお応じてしまいます。描画されたダイアログ
   から消えているだけでなく、この制限が実効を持つには、サーバ側で強制する必要があります。

対処は小さく、自己完結しています。提示する取得元をデプロイ形態の属性とし、UI は利用できるものだけを
描画し、二つのファイルブラウザ用エンドポイントは取得元が無効なとき拒否します。

## 詳細設計

判定のよりどころは**バックエンド**です。`server` バックエンドがホスティング形態にあたり、ローカル
バックエンド（stdlib の `serve`。セルフホストの単一 Mac を含む）は三つの取得元をすべて保ちます。
`ServeState` に明示的な `hosted: bool`（既定 `False`）を持たせ、server バックエンドがシームを組む
（`bajutsu/serve/server/`）ときに `True` を設定します。これにより決定論的なコアとローカル経路は
変わらず、フラグはシミュレータなしで serve の HTTP ハーネスから検証できます。

1. **`ServeState` のホスティングフラグ。** `hosted: bool = False` を足し、server バックエンドが既に
   ホスティング用のシーム（executor / logbus / 各ストア / repository）を差し替えている箇所で `True` に
   します。ローカルバックエンドは決して設定しません。これがこの項目の他の部分が読む唯一の真実の出所
   です。

2. **利用できる config 取得元を `/api/config` で提示する。** `config_info` に `configSources` フィールドを
   足します。UI が提示してよい一覧で、ローカルでは `["git", "upload", "fs"]`、ホスティング時は
   `["git", "upload"]` のようになります。同じペイロードが既に持つ `oauthEnabled` の capability に
   ならった形なので、フロントエンドはデプロイの capability を一箇所から読めます。

3. **提示しないときはファイルブラウザの取得元を UI で隠す。** `serve.js` は起動時に `/api/config` から
   `configSources` を読み、`fs` が無ければ「Open config」モーダルの「or browse the server」部分
   （`serve.html.j2` の `.fsor` / `#fspath` / `#fslist` / `.fshint` ブロック）を隠し、`browseFs` を
   呼びません。Git とアップロードの部分は触りません。起動時に config が未 bind で、ダイアログを自動的に
   開く引き金が残っているときも、ダイアログは Git とアップロードを提示して開きます。

4. **ホスティング時はファイルブラウザ用エンドポイントをサーバ側で拒否する（多層防御）。** `state.hosted`
   が立っているとき、`browse_fs`（`/api/fs`）と `bind_config` の path 分岐（`path` 付きの
   `POST /api/config`）は `4xx`（`{"error": "the file browser is disabled on a hosted server"}`）を
   返し、手で組み立てたリクエストでも隠した UI を迂回できないようにします。`POST /api/config` の `git`
   分岐と `/api/upload` は影響を受けません。

5. **テスト。** serve の HTTP ハーネスを拡張します。ホスティング状態では `/api/config` の
   `configSources` から `fs` が外れ、`/api/fs` と path bind の分岐が `4xx` を返し、`git` / `upload` は
   なお bind できること。ローカル状態では三つとも利用でき、挙動が変わらないこと（ローカル既定の
   回帰ネット）。

6. **ドキュメント。** config 取得元を説明している箇所（serve / ホスティングのドキュメント。`docs/` と
   `docs/ja/` の両方）に、取得元の集合がデプロイ形態に依存する旨を書きます。

pass/fail、runner、driver、シナリオスキーマに触れる変更は一つもありません。制限はすべて serve の
config 取得層に収まります。

## 検討した代替案

- **バックエンドではなく非ループバック公開を判定に使う。** `server` バックエンドに限らず、`serve` が
  ループバックを越えて bind されている（token 認証つきの公開 stdlib サーバ）ときに常にファイルブラウザを
  無効化する案です。既定としては採りません。Tailscale 越しに到達するセルフホストの単一 Mac
  （[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) の Tier A）は
  ファイルシステムを所有する当人が運用しているので、そこではブラウザはなお役立ちます。バックエンドを
  基準にすると、ファイルシステムが利用者自身のものでなくなる境目にちょうど線を引けます。token 認証つきの
  stdlib デプロイにも同じ制限を掛けたい場合に備えて、運用者が明示的に上書きする手段（フラグや環境変数）を
  後から重ねる余地はありますが、ここでは対象外とします。
- **UI で取得元を隠すだけにする。** より単純ですが見た目だけの対処です。`/api/fs` と path bind の
  エンドポイントはなお応じてしまいます。制限を実効あるものにするため、サーバ側の強制を採ります。
- **ファイルブラウザを完全に撤去する。** 自分のマシンに既にある config を bind するには、ファイル
  ブラウザこそが唯一かつ正当なローカルの利便機能です。撤去すると、このツールが出発点としたローカルの
  単一利用者フローを損ないます。制限は全面ではなくデプロイ形態に限る必要があります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `ServeState` の `hosted: bool` を server バックエンドが設定する
- [x] `/api/config`（`config_info`）で `configSources` を提示する
- [x] `fs` を提示しないとき、フロントエンドがファイルブラウザの取得元を隠す
- [x] ホスティング時に `browse_fs` / `bind_config` の path 分岐を拒否する（サーバ側の強制）
- [x] テスト。ホスティング時は `fs` を外し拒否する、ローカルは三つとも保つ
- [x] ドキュメント更新（取得元がデプロイ形態に依存する旨）。両言語

**ログ**

- 1 つの PR で出荷しました。`ServeState.hosted`（`_build_server_state` でのみ `True` に設定）、
  `config_info` の `configSources`、`browse_fs` と `bind_config` の path 分岐での `403` 拒否、
  フロントエンドの `#fssrc` ブロックの制御、両デプロイ形態を検証する serve の HTTP テスト、そして
  両言語のセルフホスティングの説明です。

## 参考

- [BE-0015 — Web UI の公開ホスティング](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)。この項目が判定に使うホスティング（`server`）バックエンド。
- [BE-0051 — ホスティングのための serve ハードニング](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)。ブラウザを `--root` に閉じ込めた項目。本項目はホスティング時にそれを完全に取り除く。
- [BE-0063 — Git を config の取得元にする](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)。ホスティング時にも残る取得元の一つ。
- [BE-0073 — config バンドルを zip でアップロードする](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)。もう一つの残る取得元。

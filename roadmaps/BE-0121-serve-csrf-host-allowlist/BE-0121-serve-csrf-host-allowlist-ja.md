[English](BE-0121-serve-csrf-host-allowlist.md) · **日本語**

# BE-0121 — serve の CSRF・Host allowlist 防御を無条件化する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0121](BE-0121-serve-csrf-host-allowlist-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0121") |
| 実装 PR | [#641](https://github.com/bajutsu-e2e/bajutsu/pull/641) |
| トピック | セキュリティ強化 |
<!-- /BE-METADATA -->

## はじめに

`make serve` のデフォルト構成は、loopback にバインドし、トークンを設定しません。この構成では、状態を変更する `POST` リクエストに対して、Origin・CSRF チェックと `Host` allowlist のどちらも働きません。本項目は、両方の防御を無条件にします。あわせて、実行時に API 経由でバインドされた config が、運用者の関知しないまま運用者の信頼済みとして扱われる欠落も塞ぎます。

## 動機

`serve` の CSRF 防御は、トークンが設定されているときにしか働きません。`_csrf_ok`（`bajutsu/serve/handler.py:198`）は `Origin` と `Host` を比較します。ところが `do_POST` は、`state.token is not None` のときにしかこのチェックを呼びません（`bajutsu/serve/handler.py:219`）。`make serve` はデフォルトでトークンなしに起動します。そのため、もっとも一般的な使い方では、状態を変更する `POST` がすべて無防備です。

`text/plain` を使った `fetch` は、CORS の「simple request」に分類されます。ブラウザは、プリフライトなしにクロスオリジンで送信します。`make serve` を動かしているユーザーが別タブで悪意あるページを開いたとします。そのページを読み込むだけで、ページは次の 2 つを実行できます。

1. `POST /api/config` に `git` 指定を送り、攻撃者が用意した Git リポジトリを有効な config としてバインドさせる（`bajutsu/serve/operations.py:551` の `bind_git_config`）。
2. `POST /api/run` を送る。アプリのバイナリが存在しなければ、その config の `build:` コマンドが `shlex.split` を経由して実行されます（`bajutsu/serve/jobs.py:451`）。結果は、`serve` を動かしているユーザーの権限による、ホスト上の任意コードの実行です。

アップロードされたバンドルの経路には、すでに同種の防御があります。`start_run` は、アップロードされた config の `build` を `None` にし、そのコマンドを実行しません（`bajutsu/serve/operations.py:761`、BE-0090 で導入）。Git config の経路には、対応する防御がありません。`bajutsu/serve/operations.py:753` のコメントは、「ローカルおよび Git の config は運用者が信頼したものであり、統制の対象外なので、フラグは付かない」と明言しています。

この前提は、運用者自身が Git の指定を入力した場合には成り立ちます。クロスオリジンのリクエストが運用者に代わって指定をバインドした場合には、成り立ちません。

これとは別に、`Host` ヘッダーの allowlist も存在しません。DNS rebinding は、次のように働きます。最初は同一オリジンに見えるページを読み込んだあと、そのホスト名が被害者のブラウザで `127.0.0.1` に解決されます。すると、ページからの同一オリジン扱いのリクエストが、loopback サーバーに届きます。そのため、上記の CSRF 回避を使わなくても、`GET /api/apikey?reveal=1` で `ANTHROPIC_API_KEY` を持ち出せます。

どちらの欠落も、BE-0051（serve hardening for hosting）がヘッダーとセッションをすでに強化している場所にあります。また、BE-0090 と BE-0108（hosted config source restriction）が「どの config ソースが安全か」という角度から扱う、config ソースのリスクを増幅します。本項目は、信頼できないソースがそもそもバインドされてしまう、transport 層の穴を塞ぎます。

## 詳細設計

1. **CSRF・Origin チェックを無条件で実行する。** `do_POST` 内の `_csrf_ok()` 呼び出しを、`if state.token is not None` の外に出します。すると、トークンの設定有無にかかわらず、すべての状態変更リクエストがチェックされます。`_csrf_ok` は、もともと `Origin` ヘッダーのない非ブラウザクライアントを通します。ブラウザを介さない CLI やスクリプトからのアクセスには影響がなく、ブロックされるのはクロスオリジンのブラウザリクエストだけです。
2. **`Host` allowlist を追加する。** `Host` ヘッダーが `serve` の実際のバインド先と一致しないリクエストは、`4xx` で拒否します。デフォルトのバインド先は、loopback のホスト名とアドレスです。設定されていれば、そのバインドホストがバインド先です。チェックは、CSRF チェックと同じリクエストゲーティングの経路で行います。これにより、`/api/apikey?reveal=1` を含むすべてのエンドポイントへの DNS rebinding 経路が、トークンや CSRF の状態と無関係に塞がります。
3. **実行時にバインドされた Git config を、デフォルトで未信頼として扱う。** `bind_git_config`（`bajutsu/serve/operations.py:551`）がバインドする指定は、API 経由で届いたものの場合があります。`serve` の起動前に運用者があらかじめ設定したものの場合もあります。API 経由の場合は、結果として得られる config の `build` を、デフォルトで統制対象にします。
   `start_run` は、その `build` を実行する前に、明示的なオプトインを求めます。オプトインは、`--allow-remote-build` のようなフラグか、UI 上でのリクエストごとの確認です。これは、BE-0090 がアップロードされたバンドルに導入した `upload_exec` によるゲーティングと同じ考え方です。オプトインがなければ、`start_run` は、アップロードの場合と同様に、実行時バインドされた Git config の `build` を無効化します。
4. **テスト。** serve の HTTP ハーネスを拡張し、3 つのケースを検証します。トークン未設定でのクロスオリジン `POST` が拒否されること。`Host` ヘッダーが一致しないリクエストが拒否されること。実行時バインドされた Git config の `build` が、オプトインなしには実行されないことです。
5. **ドキュメント。** 無条件にした CSRF・Host 防御と、Git config の信頼境界を、serve のハードニングに関するドキュメント（`docs/` と `docs/ja/` の両方）に追記します。

この変更は、決定的な `run`・CI ゲートの合否判定にも、drivers にも、scenario スキーマにも触れません。作業は serve の transport 層と config バインディング層の内部に閉じており、LLM はどこにも入りません。

## 検討した代替案

- **チェックをトークン設定時に限り、`Host` allowlist だけで補う。** 却下しました。2 つの防御は、異なる攻撃形態をカバーします。クロスオリジンの `fetch` と、DNS rebinding です。今回の指摘は、トークン未設定という一般的なデフォルト構成で、両方が独立に悪用できることを示しています。どちらを欠いても、実質的な穴が残ります。
- **BE-0063 がすでに content-addressed cache に閉じ込めているという理由で、Git config の経路を信頼する。** 却下しました。この閉じ込めが防ぐのは、展開時のパストラバーサルです。`build:` コマンドの任意実行は防ぎません。cache は、リクエストが指すリポジトリを安全に展開します。攻撃者由来の内容も、そのまま取り込みます。
- **API 経由で Git config がバインドされたときに、サーバー側の防御を置かず、UI 上の警告だけを出す。** 見かけだけの対処なので、却下しました。BE-0108 は、ファイルブラウザについて UI のみの対処を、同じ理由で退けています。フロントエンドだけで強制している制約は、手作りのリクエストで回避されます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] CSRF・Origin チェック（`_csrf_ok`）をトークンの有無にかかわらず `do_POST` で無条件実行する
- [x] `Host` ヘッダー allowlist を追加し、すべてのリクエストに適用する
- [x] 実行時バインドされた Git config の `build` を既定で統制対象にし、明示的なオプトインで
      ゲーティングする
- [x] テスト：トークン未設定でのクロスオリジン POST 拒否、Host 不一致の拒否、オプトインなしでの
      Git `build` 未実行
- [x] ドキュメント更新（日英両方）

ログ：

- **無条件の CSRF・Host 防御と Git config の build 信頼境界**：`do_POST` と `_handle_upload` で
  `_csrf_ok` チェックをトークンゲートの外に出し、トークンなしのループバック既定でもクロスオリジンの
  `POST` をブロックするようにしました。バインドしたインタフェースから導く `Host` ヘッダの allowlist
  （`make_server` が `state.allowed_hosts` に設定）を追加し、`_gate` の先頭で全リクエストに適用して
  `/api/apikey` への DNS リバインディング経路を塞ぎました。API 経由で実行時にバインドした Git config を
  信頼できないものとして印付け（`state.git_config_from_api`）、`--allow-remote-build` /
  `BAJUTSU_ALLOW_REMOTE_BUILD` で opt-in しない限り run・record・crawl の各 dispatch がその `build:`
  コマンドを null にします。共通の `_governed_build` ヘルパで三つの経路をまとめて統制し、アップロード
  されたバンドルの扱いに揃えたものです。同じ無条件 CSRF チェックと Host allowlist を FastAPI（`--asgi`）
  のゲート（`server/app.py`、`make_asgi_server`）にも適用し、二つのトランスポートのポリシーを揃えました。

## 参考

- `bajutsu/serve/handler.py:198` — `_csrf_ok`、Origin・Host のチェック。
- `bajutsu/serve/handler.py:219` — このチェックを既定でスキップさせるトークンゲートの呼び出し箇所。
- `bajutsu/serve/operations.py:551` — `bind_git_config`。
- `bajutsu/serve/operations.py:753` — Git・ローカル config を「運用者が信頼したもので統制対象外」
  とするコメント。
- `bajutsu/serve/operations.py:761` — アップロードされたバンドルに対する既存の `build = None` 防御。
- `bajutsu/serve/jobs.py:451` — 本項目がゲーティングする `shlex.split(job.build)` によるコマンド実行。
- [BE-0051 — Serve hardening for hosting](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)
- [BE-0090 — Uploaded-config command execution](../BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution-ja.md)
- [BE-0063 — Git config source](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)
- [BE-0108 — Hosted config source restriction](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md)
- 2026-07-02 のコードベース分析レポート（セキュリティ）に由来します。

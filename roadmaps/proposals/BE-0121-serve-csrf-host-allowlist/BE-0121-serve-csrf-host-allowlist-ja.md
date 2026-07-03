[English](BE-0121-serve-csrf-host-allowlist.md) · **日本語**

# BE-0121 — serve の CSRF・Host allowlist 防御を無条件化する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0121](BE-0121-serve-csrf-host-allowlist-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トピック | セキュリティ強化 |
<!-- /BE-METADATA -->

## はじめに

`make serve` の既定構成（loopback バインド、トークン未設定）では、状態を変更する `POST` リクエストに
Origin・CSRF チェックも `Host` allowlist も適用されません。本項目は両方の防御を無条件化し、実行時に
API 経由でバインドされた config を無条件に信頼している欠落も塞ぎます。

## 動機

`serve` の CSRF 防御はトークンが設定されているときにしか働きません。`_csrf_ok`
（`bajutsu/serve/handler.py:198`）は `Origin` と `Host` を比較しますが、`do_POST` はこのチェックを
`state.token is not None` のときにしか呼びません（`bajutsu/serve/handler.py:219`）。`make serve` は
既定でトークンなしに起動するため、最も一般的な使い方で、状態を変更する `POST` はすべて無防備です。

`text/plain` を使った `fetch` は CORS の「simple request」に分類され、ブラウザはプリフライトなしに
クロスオリジンで送信します。`make serve` を動かしているユーザーが別タブで悪意あるページを開くだけで、
そのページは次の操作を実行できます。

1. `POST /api/config` に `git` 指定を送り、攻撃者が用意した Git リポジトリを config として
   バインドさせる（`bajutsu/serve/operations.py:551` の `bind_git_config`）。
2. `POST /api/run` を送る。アプリのバイナリが存在しない場合、その config の `build:` コマンドが
   `shlex.split` を経由して実行され（`bajutsu/serve/jobs.py:451`）、`serve` を動かしているユーザーの
   権限でホスト上の任意コードが実行されます。

アップロードされたバンドル側にはすでに同種の防御があります。`start_run` はアップロードされた config の
`build` を `None` にし、そのコマンドを一切実行しません（`bajutsu/serve/operations.py:761`、BE-0090 で
導入）。ところが Git config のパスには対応する防御がなく、`bajutsu/serve/operations.py:753` の
コメントは「ローカルおよび Git の config は運用者が信頼したものであり、統制の対象外」と明言しています。
この前提は、運用者自身が Git の指定を入力した場合には成り立ちますが、クロスオリジンのリクエストが
代わりにその指定をバインドした場合には成り立ちません。

もう一つ、`Host` ヘッダーの allowlist も存在しません。DNS rebinding（被害者のブラウザが最初は
同一オリジンに見えるページを読み込んだあと、そのホスト名を `127.0.0.1` に解決させる手法）を使えば、
ページからの同一オリジン扱いのリクエストが loopback サーバーに到達してしまうため、上記の CSRF 回避を
経由しなくても `GET /api/apikey?reveal=1` によって `ANTHROPIC_API_KEY` を持ち出せます。

いずれの欠落も、BE-0051（serve hardening for hosting）がヘッダーとセッションをすでに強化している
場所に位置し、「どの config ソースが安全か」という角度から迫る BE-0090 や BE-0108（hosted config
source restriction）とも重なります。本項目は、そもそも信頼できないソースがバインドされてしまう
transport 層の穴を塞ぐものです。

## 詳細設計

1. **CSRF・Origin チェックを無条件で実行する。** `do_POST` 内の `_csrf_ok()` 呼び出しを
   `if state.token is not None` の外に出し、トークンの設定有無にかかわらずすべての状態変更
   リクエストをチェックします。`_csrf_ok` はもともと `Origin` ヘッダーがない非ブラウザクライアント
   （CLI やスクリプト）を通すため、ブラウザを介さないアクセスへの影響はなく、クロスオリジンの
   ブラウザリクエストだけがブロックされます。
2. **`Host` allowlist を追加する。** `Host` ヘッダーが `serve` の実際のバインド先（既定では
   loopback のホスト名・アドレス、設定されていればそのバインドホスト）と一致しないリクエストを
   `4xx` で拒否します。CSRF チェックと同じリクエストゲーティングの経路でチェックし、
   `/api/apikey?reveal=1` を含むすべてのエンドポイントへの DNS rebinding 経路を塞ぎます。
3. **実行時にバインドされた Git config を既定で未信頼として扱う。** API 経由で供給された指定を
   `bind_git_config`（`bajutsu/serve/operations.py:551`）がバインドした場合（`serve` 起動前に
   運用者があらかじめ設定していた場合と区別する）、結果として得られる config の `build` を
   既定で統制対象とし、`start_run` がそれを実行する前に明示的なオプトイン（
   `--allow-remote-build` のようなフラグ、または UI 上での確認）を要求します。これは BE-0090 が
   アップロードされたバンドルに導入した `upload_exec` によるゲーティングと同じ考え方です。
   オプトインがない場合、`start_run` はアップロードの場合と同様に、実行時バインドされた
   Git config の `build` を無効化します。
4. **テスト。** serve の HTTP ハーネスを拡張し、トークン未設定でのクロスオリジン `POST` が
   拒否されること、`Host` ヘッダーが一致しないリクエストが拒否されること、実行時バインドされた
   Git config の `build` がオプトインなしには実行されないことを検証します。
5. **ドキュメント。** 無条件化した CSRF・Host 防御と Git config の信頼境界について、serve の
   ハードニングに関するドキュメント（`docs/` と `docs/ja/` の両方）に追記します。

この変更は決定的な `run`・CI ゲートの合否判定、drivers、scenario スキーマのいずれにも触れません。
すべて serve の transport 層と config バインディング層の内部に閉じており、LLM もどこにも
導入しません。

## 検討した代替案

- **チェックをトークン設定時のみに保ち、`Host` allowlist だけで補う。** 却下しました。2 つの防御は
  異なる攻撃形態（クロスオリジンの `fetch` と DNS rebinding）をカバーしており、今回の指摘は
  トークン未設定という一般的な既定構成の下で両方が独立に悪用可能であることを示しています。
  どちらか一方を欠いても実質的な穴が残ります。
- **BE-0063 がすでに content-addressed cache に閉じ込めているという理由で Git config パスを
  信頼する。** 却下しました。その閉じ込めは展開時のパストラバーサルを防ぐものであり、
  `build:` コマンドの任意実行を防ぐものではありません。cache はリクエストが指すリポジトリを
  安全に展開するだけで、攻撃者由来の内容もそのまま取り込みます。
- **API 経由で Git config がバインドされたときに、サーバー側の防御なしで UI 上の警告だけを出す。**
  却下しました。BE-0108 がファイルブラウザについて UI のみの対処を見かけだけと判断したのと同じ
  理由で、フロントエンドだけの制約は手作りのリクエストで回避されます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] CSRF・Origin チェック（`_csrf_ok`）をトークンの有無にかかわらず `do_POST` で無条件実行する
- [ ] `Host` ヘッダー allowlist を追加し、すべてのリクエストに適用する
- [ ] 実行時バインドされた Git config の `build` を既定で統制対象にし、明示的なオプトインで
      ゲーティングする
- [ ] テスト：トークン未設定でのクロスオリジン POST 拒否、Host 不一致の拒否、オプトインなしでの
      Git `build` 未実行
- [ ] ドキュメント更新（日英両方）

まだ着手した PR はありません。

## 参考

- `bajutsu/serve/handler.py:198` — `_csrf_ok`、Origin・Host のチェック。
- `bajutsu/serve/handler.py:219` — このチェックを既定でスキップさせるトークンゲートの呼び出し箇所。
- `bajutsu/serve/operations.py:551` — `bind_git_config`。
- `bajutsu/serve/operations.py:753` — Git・ローカル config を「運用者が信頼したもので統制対象外」
  とするコメント。
- `bajutsu/serve/operations.py:761` — アップロードされたバンドルに対する既存の `build = None` 防御。
- `bajutsu/serve/jobs.py:451` — 本項目がゲーティングする `shlex.split(job.build)` によるコマンド実行。
- [BE-0051 — Serve hardening for hosting](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)
- [BE-0090 — Uploaded-config command execution](../../implemented/BE-0090-uploaded-config-command-execution/BE-0090-uploaded-config-command-execution-ja.md)
- [BE-0063 — Git config source](../../implemented/BE-0063-git-config-source/BE-0063-git-config-source-ja.md)
- [BE-0108 — Hosted config source restriction](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md)
- 2026-07-02 のコードベース分析レポート（セキュリティ）に由来します。

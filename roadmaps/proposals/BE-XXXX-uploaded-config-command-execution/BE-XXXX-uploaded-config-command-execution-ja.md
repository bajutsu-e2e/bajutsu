[English](BE-XXXX-uploaded-config-command-execution.md) · **日本語**

# BE-XXXX — アップロードされたバンドル config からのコマンド実行を統制する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-uploaded-config-command-execution-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トピック | Web UI のホスティング（クラウド / セルフホスト） |
<!-- /BE-METADATA -->

## はじめに

[BE-0073](../../proposals/BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md) では、ブラウザ利用者が `.zip` バンドルをアップロードすると、`serve` がそれを展開し、Replay / Record / Crawl タブが動く**アクティブな config** としてバインドします。アップロードされた config は信頼できない入力であり、BE-0073 はそのうち一つをすでに拒否しています。ターゲットの `build` コマンドはホスト上で**決して実行しません**（バンドルはビルド済みバイナリを同梱します。[DESIGN §1](../../../DESIGN.md)）。しかし config には、同じく `serve` ホスト上でシェルコマンドを走らせるフィールドがほかにもあります。`launchServer.cmd` と `mockServer.cmd` です。これらは**統制されておらず**、バインドしたバンドルを実行すると `bajutsu run` のサブプロセスが記述どおりに実行します。本項目は、アップロードされた config の**あらゆる**コマンド実行フィールドに対する明示的でティアに応じたポリシーを定め、アップロード経路のセキュリティモデルを一貫させ、マルチテナントのホスティングに耐えるようにします。

## 動機

BE-0073 はアップロードされたバンドルをアクティブな config としてバインドし、それに対して決定的な `run` を走らせます。config 内でホスト上のシェルコマンドを起動するターゲットフィールドは 3 つあります。

- `build`：`appPath` のオンデマンドビルド（`bajutsu/serve/jobs.py` の `_build_app`）。
- `launchServer.cmd`（[BE-0059](../../implemented/BE-0059-launch-target-server/BE-0059-launch-target-server-ja.md)）：run のために `baseUrl` の背後のホストを、専用のプロセスグループで起動する。
- `mockServer.cmd`：アプリが呼ぶ依存先をスタブするモックを起動する。

BE-0073 はアップロードに対して 1 つめの扉を閉じています。アクティブな config がアップロードされたバンドルのとき（`state.upload` がセットされているとき）、`start_run` は `build=None` を強制するので、アップロードされた config の `build` は走りません。一方、残りの 2 つは**開いたまま**で、`launchServer.cmd` と `mockServer.cmd` はそのまま実行されます。この非対称は実在し、しかも Web のケースでは本質的に必要でした。Web バンドルには `appPath` のバイナリが**無く**、`launchServer` がバンドルを自己完結にできる唯一の手段だからです（run 時に同梱の静的アプリを配信します）。一律にブロックできないのは、まさにこの理由によります。

この穴が効いてくるのはラップトップ上ではなく、デプロイの境界です。

- **Tier-A（認証済みの単一 Mac の `serve`）。** アップロードされた config の `launchServer.cmd` を実行することは、同じバンドルが同梱するアップロード済み `.app` バイナリを実行することと危険度が変わりません。認証済みの運用者は、自分のスイートを持ち込んで動かすことをすでに信頼されています。ここでは現在の挙動が*意図した*モデルです。
- **ホスト型 / マルチテナントの `serve`**（[BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)、[BE-0016](../../proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)）。認証済みだが信頼できないテナントが、config に `launchServer.cmd: rm -rf …`（あるいは任意の内容）を仕込んだバンドルをアップロードすることは、**ホスト上の任意コマンド実行**であり、リモートコード実行のベクタです。BE-0073 は単一 Mac の Tier-A に明示的に限定し、マルチテナントの隔離を BE-0015 / BE-0016 に委ねているので、これは BE-0073 のバグではありません。ホスティングを安全にする前に、それらの項目が塞ぐべき継ぎ目です。

つまり現在のポリシーは**暗黙的で一貫していません**。アップロードに対して `build` は拒否し、`launchServer` と `mockServer` は黙って許可しており、その規則を名指しするものも、運用者が変えられる手段もありません。本項目は規則を明示的かつティアに応じたものにします。これにより非対称が解消され、安全なマルチテナント経路も開けます。

## 詳細設計

「アップロードされた config のコマンド実行フィールド」（`build`、`launchServer.cmd`、`mockServer.cmd`）を一つの統制対象として扱い、単一の `serve` レベルのポリシーでゲートします。完全に決定的で、LLM を含まず、アプリ単位ではなくホストレベルなので、prime directives に従います。

- **ポリシーのつまみ。** `serve` のオプション（`--upload-exec=<deny|reuse|allow>`、ホスト型バックエンドには環境変数で対応）が、*アクティブな config がアップロードされたバンドルで*、run がこれらのコマンドのいずれかを起動しようとするときの挙動を決めます。対象はアップロード由来の config だけで、ローカルや Git ソースの config（すでに運用者が信頼している）には影響しません。
- **ティアに応じた既定。** ローカルの単一 Mac の `serve` では `allow` を既定にし（持ち込みスイートの Tier-A モデル。アップロードしたバイナリを実行することと整合）、ホスト型 / マルチテナント構成で動く `serve`（非ループバックのバインド、または BE-0015 / BE-0016 のサーババックエンド）では `deny` を既定にします。既定がデプロイ形態に追従するので、安全な選択が自動になり、運用者は緩い側へ*明示的に*オプトインするのであって、厳しい側から外れることはありません。
- **`reuse`：`launchServer` に効く中間。** `launchServer` はすでに `readyUrl` を先に探り、起動済みのサーバがあれば `cmd` を起動せずに再利用します（[BE-0059](../../implemented/BE-0059-launch-target-server/BE-0059-launch-target-server-ja.md)）。`reuse` のもとでは、アップロードされた config が、運用者が外で起動したホストの `baseUrl` を*使う*ことは許しつつ、その `cmd` は走らせません。ホスト運用者がテスト対象サーバを事前に用意し、それに対するアップロードスイートを受け付ける、という運用ができます。
- **黙って飛ばさず、失敗は声高に。** `deny` / `reuse` のもとで、run がブロックされたコマンドを必要とする（`readyUrl` に応答するサーバが無い）場合、run は対象フィールドを名指しする明確なエラーで失敗します（[DESIGN §2](../../../DESIGN.md)：失敗は声高に、暗黙のフォールバックは無し）。拒否された `launchServer` が、不安定な run のように見えてはなりません。`build` はアップロードに対する既存の「常に拒否」の扱いを保ち（バンドルがバイナリを同梱する）、別個の特例ではなく、このポリシーの先例として畳み込みます。
- **来歴。** ポリシーの判断（許可 / 拒否 / 再利用と、どのフィールドか）を、BE-0073 のアップロード来歴と並べて run の `manifest.json` に記録し、「この run は何を実行し、何を抑止したか」を後から辿れるようにします。

対象は**ポリシーの継ぎ目**、すなわちアップロードされた config のコマンドを走らせる*かどうか*の判断であって、プロセスのサンドボックス化ではありません。より深いジョブ単位の実行隔離（コンテナ、seatbelt、egress 制御）は BE-0015 / BE-0016 の領域です。本項目は、それらが乗る決定的なゲートを与えます。

## 検討した代替案

- **`build` のように `launchServer` / `mockServer` もアップロードに対して一律ブロックする。** 却下します。`launchServer` は run 時にテスト対象アプリを起動するために必要で、Web バンドルにはほかに自己完結する手段が無いため、一律ブロックはアップロード経路が存在する理由である Tier-A の正当な用途を壊します。
- **何もせず、BE-0073 の Tier-A 限定に頼る。** 恒久的な答えとしては却下します。規則は Tier-A には正しいものの、暗黙的で一貫しておらず（`build` は拒否、残りは許可）、潜在的な落とし穴であり、BE-0015 / BE-0016 の明確な障害になります。アップロード経路が新しいうちに継ぎ目を定義するほうが、ホスティングの納期に追われて後付けするより安く済みます。
- **特定のコマンド文字列をホワイトリスト化する。** 脆いので却下します。コマンド文字列のホワイトリストは回避が容易で、正しく作るのも難しく、ティアベースの許可 / 拒否に運用者の明示的オプトインを足すほうが単純で、セキュリティの筋も明快です。
- **コマンドをゲートする代わりに run をサンドボックス化する（コンテナ / seatbelt / プロセス隔離）。** 反対ではなく先送りします。ジョブ単位の隔離は BE-0015 / BE-0016 の領分で、より重い仕組みです。本項目はそれらが背後に置くポリシーゲートを定義するので、両者は競合せず補い合います。

## 参考

- [BE-0073 — config・シナリオ・アプリバイナリのバンドルをアップロードしてアクティブな config としてバインドする](../../proposals/BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md) — アップロード経路。`build` がアップロードに対して拒否され、`launchServer` / `mockServer` は拒否されない箇所。
- [BE-0059 — run のためにテスト対象サーバを起動する（`launchServer`）](../../implemented/BE-0059-launch-target-server/BE-0059-launch-target-server-ja.md) — `cmd` を運ぶ run 時のサーバ起動と、本項目が土台にする `readyUrl` 再利用。
- [BE-0051 — ホスティングのための serve hardening](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md) — token 認証とパス封じ込め。本項目はこれにコマンド実行ポリシーを足します。
- [BE-0015 — Web UI の公開ホスティング](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)、[BE-0016 — Web UI のセルフホスティング](../../proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) — `deny` 既定が効いてくるマルチテナント / ホスト型のターゲットと、ジョブ単位のサンドボックスの置き場所。
- [DESIGN §1](../../../DESIGN.md)（Bajutsu はビルド済みアプリを受け取り、ビルドしない）、[DESIGN §2](../../../DESIGN.md)（決定性。失敗は声高に、暗黙のフォールバックは無し）。
- `bajutsu/config.py`（`LaunchServer.cmd`、`MockServer.cmd`、`AppConfig.build`）、`bajutsu/serve/operations.py`（アップロードされたバンドルに対して `build=None` を強制する `start_run`、`state.upload`）、`bajutsu/serve/jobs.py`（`_build_app`。run ジョブ機構） — 修正が触れる箇所。

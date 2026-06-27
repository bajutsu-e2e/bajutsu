[English](BE-0090-uploaded-config-command-execution.md) · **日本語**

# BE-0090 — アップロードされたバンドル config からのコマンド実行を統制し、サンドボックス化する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0090](BE-0090-uploaded-config-command-execution-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トピック | Web UI のホスティング（クラウド / セルフホスト） |
<!-- /BE-METADATA -->

## はじめに

[BE-0073](../../proposals/BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md) では、ブラウザ利用者が `.zip` バンドルをアップロードすると、`serve` がそれを展開し、Replay / Record / Crawl タブが動く**アクティブな config** としてバインドします。アップロードされた config は信頼できない入力であり、BE-0073 はそのうち一つをすでに拒否しています。ターゲットの `build` コマンドはホスト上で**決して実行しません**（バンドルはビルド済みバイナリを同梱します。[DESIGN §1](../../../DESIGN.md)）。しかし config には、同じく `serve` ホスト上でシェルコマンドを走らせるフィールドがほかにもあります。主なものは `launchServer.cmd` で、これは**統制されていません**。バインドしたバンドルを実行すると、`bajutsu run` のサブプロセスが、運用者の環境ごと、生のホスト上でそのコマンドを記述どおりに起動します。本項目は、この surface に明示的でティアに応じたポリシーを与え、さらにそれを安全に走らせる手段を加えます。アップロードされたコマンドを使い捨ての **Docker** コンテナの中で実行し、`serve` ホストに触れさせない `sandbox` モードです。ポリシーがコマンドを走らせる*かどうか*を決め、サンドボックスが*どれだけ安全に*走らせるかを決めます。両者が揃って初めて、「はい、アップロードされたサーバを動かします」をマルチテナントのホストが現実に許せるものになります。

## 動機

BE-0073 はアップロードされたバンドルをアクティブな config としてバインドし、それに対して決定的な `run` を走らせます。config 内でシェルコマンドを名指すフィールドは 3 つありますが、いまどれも現に動いているわけではありません。

- `build`：`appPath` のオンデマンドビルド（`bajutsu/serve/jobs.py` の `_build_app`）。ローカル config では実行しますが、アップロードされた config では**すでに拒否**しています。
- `launchServer.cmd`（[BE-0059](../../implemented/BE-0059-launch-target-server/BE-0059-launch-target-server-ja.md)）：run のために `baseUrl` の背後のホストを起動します。**現在実行している**もので、`serve` ホスト上で、専用のプロセスグループとして走ります（`bajutsu/runner/launch_server.py`：`subprocess.Popen(shlex.split(ls.cmd), env={**os.environ, …}, start_new_session=True)`）。
- `mockServer.cmd`：アプリが呼ぶ依存先のモックを起動するものとしてスキーマに宣言されています（`bajutsu/config.py` の `MockServer.cmd`）が、**まだ実行器に配線されていません**。モックは Playwright の network collector がプロセス内で充足するため、`cmd` はいまのところ休眠している surface であり、実行器ができれば `launchServer` と同じ形で走ることになります。

つまり今日現に露出しているのは `launchServer.cmd` であり、`mockServer.cmd` は、配線される前に同じポリシーが覆っておくべき潜在的な surface です。BE-0073 はアップロードに対して `build` の扉を閉じています（アクティブな config がアップロードされたバンドルのとき、`start_run` が `build=None` を強制します）が、`launchServer` は**開いたまま**にしています。そうせざるを得ませんでした。Web バンドルには `appPath` のバイナリが**無く**、`launchServer` がバンドルを自己完結にできる唯一の手段だからです（run 時に同梱の静的アプリを配信します）。一律にブロックできないのは、まさにこの理由によります。

この穴が効いてくるのはラップトップ上ではなく、デプロイの境界です。

- **Tier-A（認証済みの単一 Mac の `serve`）。** アップロードされた config の `launchServer.cmd` を実行することは、同じバンドルが同梱するアップロード済み `.app` バイナリを実行することと危険度が変わりません。認証済みの運用者は、自分のスイートを持ち込んで動かすことをすでに信頼されています。ここでは現在の挙動が*意図した*モデルです。
- **ホスト型 / マルチテナントの `serve`**（[BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)、[BE-0016](../../proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)）。認証済みだが信頼できないテナントが、config に `launchServer.cmd: rm -rf …`（あるいは任意の内容）を仕込んだバンドルをアップロードすることは、**ホスト上の任意コマンド実行**であり、リモートコード実行のベクタです。BE-0073 は単一 Mac の Tier-A に明示的に限定し、マルチテナントの隔離を BE-0015 / BE-0016 に委ねているので、これは BE-0073 のバグではありません。ホスティングを安全にする前に、それらの項目が塞ぐべき継ぎ目です。

*拒否*か*許可*しか言えないポリシーでは、ホストのケースは行き詰まります。`deny` はアップロード経路が存在する理由である自己完結 Web バンドルの正当な用途を壊し、`allow` は生ホストの RCE です。欠けている選択肢は**安全な yes**、すなわちアップロードされたコマンドを走らせつつ、敵対的な `cmd` が使い捨てコンテナ以外の何にも届かないよう封じ込めることです。サーバ起動コマンドを Docker で仮想化すると、まさにこれが手に入ります。しかもこれは、すでにプロジェクトの流儀の内にあります。Tier-B のセルフホストは `docker compose` の control plane を同梱しており（[`deploy/self-host/`](../../../deploy/self-host/)、[docs/self-hosting.md](../../../docs/self-hosting.md)）、BE-0015 / BE-0016 はジョブ単位のコンテナ隔離を自分の領分として挙げています。本項目はその隔離を具体的な実行モードとして手前に引き、その背後に置く決定的なポリシーゲートを与えます。

## 詳細設計

「アップロードされた config のコマンド実行フィールド」（`build`、`launchServer.cmd`、`mockServer.cmd`）を一つの統制対象として扱い、単一の `serve` レベルのポリシーでゲートし、さらにコマンドをコンテナの中で走らせるサンドボックス実行モードを加えます。両者とも完全に決定的で（run 経路にもゲート経路にも LLM を含みません）、アプリ単位ではなくホストレベルで、`targets.<name>` を通じて設定するので、prime directives に従います。

- **ポリシーのつまみ：いまや 4 モード。** `serve` のオプション `--upload-exec=<deny|reuse|sandbox|allow>`（ホスト型バックエンドには環境変数で対応）が、*アクティブな config がアップロードされたバンドルで*、run がこれらのコマンドのいずれかを起動しようとするときの挙動を決めます。対象はアップロード由来の config だけで、ローカルや Git ソースの config（すでに運用者が信頼している）には影響しません。
  - **`deny`**：コマンドは決して走りません。それを必要とする run は声高に失敗します（後述）。
  - **`reuse`**：アップロードされた `cmd` は走らせず、`launchServer` の既存の `readyUrl` 探りが、`baseUrl` ですでに応答している運用者用意のサーバを*使う*ことだけを許します。
  - **`sandbox`**：アップロードされた `cmd` を、`serve` ホスト上ではなく**使い捨ての Docker コンテナの中で**走らせます。これが新しい安全な yes です（後述）。
  - **`allow`**：アップロードされた `cmd` を `serve` ホスト上で直接走らせます（今日の挙動）。
- **既定でサンドボックス。** `serve` は Docker をアップロード経路の必須依存として扱うので、`sandbox` が*あらゆる*デプロイで、アップロードされた config のコマンドの既定になります。退避すべき Docker 不在のケースはありません。`allow` は、アップロードしたバイナリを実行することと揃えて生ホストで走らせたい、信頼されたローカルの単一 Mac の `serve`（持ち込みスイートの Tier-A モデル）向けの、運用者による明示的なオプトアウトです。ホスト型 / マルチテナントの `serve` は `sandbox` の既定を保ちます。運用者は生ホストの `allow` へ*明示的に*オプトインするのであって、封じ込めから外れることはありません。
- **`sandbox` モード：サーバ起動コマンドを仮想化する。** `sandbox` が効いていて run が `launchServer.cmd`（配線後は `mockServer.cmd` も）を必要とするとき、bajutsu はホスト上の `subprocess.Popen` ではなく、まっさらなコンテナの中でコマンドを走らせます。
  - **image とポートはコードではなく config から。** `launchServer` に、任意の `image`（コンテナの基底。たとえば `node:20-slim` で、`cmd` が必要とするランタイムを携える）と、`port`（サーバがリッスンするコンテナ内ポート。bajutsu がこれをループバックのホストポートに publish する）を持たせます。バンドルの静的ファイルは `cwd` に**読み取り専用**でバインドマウントし、`cmd`、`env`、`readyTimeout` はそのまま引き継ぎます。`image` が無ければ `sandbox` は声高に失敗します（バンドルは自分が何の中で走るかを宣言しなければなりません）。生ホスト実行に退くことは決してありません。
  - **堅牢で使い捨てのコンテナ。** `--rm`、読み取り専用のバンドル以外にホストのバインドマウントを持たないこと、削減した capability 集合（`--cap-drop=ALL`）、`--security-opt=no-new-privileges`、tmpfs のスクラッチを伴う読み取り専用のルートファイルシステム、非 root ユーザ、CPU / メモリ / pids の上限、そして publish した一つのポートだけ。コンテナは run ごとに命名し、teardown で破棄します。これが `start_launch_server` のホストプロセスグループ kill を置き換えます。
  - **readiness は変わりません。** run は引き続き `readyUrl`（いまは publish したループバックポート）を**condition wait** として探り、固定 sleep は使いません（[DESIGN §2](../../../DESIGN.md)）。`reuse` のセマンティクスもそのまま効きます。外部で応答した `readyUrl` は、起動とコンテナの両方を短絡します。
  - **VM ではなく封じ込め。** Docker は被害範囲を使い捨てコンテナに封じ込めますが、VM 級の境界ではありません。egress の制限、テナント単位のネットワーク名前空間、VM レベルの隔離は BE-0015 / BE-0016 の領域に残ります。`sandbox` はそれらが土台にする、いま手に入る決定的な堅牢化であり、publish したポートはループバックに封じたままにするので、サンドボックスがホストの露出を広げることはありません。
- **黙って飛ばさず、失敗は声高に。** どのモードでも、run がそのモードの禁じる、あるいは満たせないコマンドを必要とするとき（`deny` / `reuse` で `readyUrl` に応答するサーバが無い、または `sandbox` で `image` が無い）、run は対象フィールドと理由を名指しする明確なエラーで失敗します（[DESIGN §2](../../../DESIGN.md)：失敗は声高に、暗黙のフォールバックは無し）。ブロックされた、あるいはサンドボックスの設定を欠いた `launchServer` が、不安定な run のように見えてはならず、`sandbox` が生ホストの `allow` へ黙って劣化してもなりません。`build` はアップロードに対する既存の「常に拒否」の扱いを保ち、別個の特例ではなく、このポリシーの先例として畳み込みます。
- **来歴。** ポリシーの判断（許可 / 拒否 / 再利用 / サンドボックスと、どのフィールドか、サンドボックスのときはどの image か）を、BE-0073 のアップロード来歴と並べて run の `manifest.json` に記録し、「この run は何を、どこで実行し、何を抑止したか」を後から辿れるようにします。

対象は、アップロード経路に対する**ポリシーの継ぎ目と、コンテナ化された実行モード**です。すなわち、アップロードされた config のコマンドを走らせるかどうかの判断と、走らせるときに使い捨てコンテナの中で走らせることです。より深いジョブ単位の隔離（egress 制御、ネットワーク名前空間、VM 級のサンドボックス化）は BE-0015 / BE-0016 の領域に残ります。本項目はそれらに、決定的なゲートと、拡張できる動くコンテナの土台を与えます。

## 検討した代替案

- **ポリシーゲートのみ（`deny|reuse|allow`）でサンドボックスを設けない。** 本項目の当初の形です。ホスティングには不十分なので却下します。コマンドを走らせる*かどうか*は決められても、その「はい」を安全にはできないため、マルチテナントのホストは、壊れた `deny` か生ホストの RCE かの二択に置かれます。`sandbox` モードが欠けていた安全な yes であり、それこそマルチテナントの継ぎ目の眼目です。
- **`sandbox` を唯一のモードにする（残り 3 つを捨てる）。** Docker が常にあるとしても却下します。運用者が用意したサーバに対する `reuse` は正当なホスト型のパターンで（run はコンテナを一切必要としません）、`allow` は信頼された Tier-A の運用者が、アップロードしたバイナリの実行と揃えてコンテナを省く道を与え、`deny` はアップロードされたコマンドを丸ごと拒みたいときの正しい答えです。`sandbox` は安全な*既定*であって唯一の道ではありません。残り 3 モードを保つ費用は小さく、それぞれが居場所を持ちます。
- **`build` のように `launchServer` / `mockServer` もアップロードに対して一律ブロックする。** 却下します。`launchServer` は run 時にテスト対象アプリを起動するために必要で、Web バンドルにはほかに自己完結する手段が無いため、一律ブロックはアップロード経路が存在する理由である Tier-A の正当な用途を壊します。
- **何もせず、BE-0073 の Tier-A 限定に頼る。** 恒久的な答えとしては却下します。規則は Tier-A には正しいものの、暗黙的で一貫しておらず（`build` は拒否、残りは許可）、潜在的な落とし穴であり、BE-0015 / BE-0016 の明確な障害になります。アップロード経路が新しいうちに継ぎ目を定義するほうが、ホスティングの納期に追われて後付けするより安く済みます。
- **特定のコマンド文字列をホワイトリスト化する。** 脆いので却下します。コマンド文字列のホワイトリストは回避が容易で、正しく作るのも難しく、ティアベースのモードに運用者の明示的オプトインとコンテナ境界を足すほうが単純で、セキュリティの筋も明快です。
- **Docker でないサンドボックス（macOS の `sandbox-exec` / seatbelt、bubblewrap、VM）。** 反対ではなく先送りします。seatbelt は macOS 専用で（Linux の control plane がありません）、bubblewrap は Linux 専用、VM ははるかに重いものです。Docker は、プロジェクトのホスティングスタックにすでに同梱され、Tier-A の Mac（Docker Desktop）でも Tier-B の Linux ノードでも使える唯一の仕組みなので、最初のサンドボックスとして現実的です。より強い VM 級の隔離は、同じポリシーゲートの上に重ねる BE-0015 / BE-0016 の選択肢として残ります。

## 参考

- [BE-0073 — config・シナリオ・アプリバイナリのバンドルをアップロードしてアクティブな config としてバインドする](../../proposals/BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md) — アップロード経路。`build` がアップロードに対して拒否され、`launchServer` は拒否されない箇所。
- [BE-0059 — run のためにテスト対象サーバを起動する（`launchServer`）](../../implemented/BE-0059-launch-target-server/BE-0059-launch-target-server-ja.md) — `cmd` を運ぶ run 時のサーバ起動と、本項目が土台にする `readyUrl` 再利用。`sandbox` はそのホスト `Popen` を、探りは保ったままコンテナに置き換えます。
- [BE-0051 — ホスティングのための serve hardening](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md) — token 認証とパス封じ込め。本項目はこれにコマンド実行ポリシーとサンドボックスを足します。
- [BE-0015 — Web UI の公開ホスティング](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)、[BE-0016 — Web UI のセルフホスティング](../../proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) — `sandbox` 既定が効いてくるマルチテナント / ホスト型のターゲットと、より深い（egress / VM 級）ジョブ単位の隔離の置き場所。
- [docs/self-hosting.md](../../../docs/self-hosting.md)、[`deploy/self-host/`](../../../deploy/self-host/) — `sandbox` が整合する、既存の `docker compose` ホスティングスタック。
- [DESIGN §1](../../../DESIGN.md)（Bajutsu はビルド済みアプリを受け取り、ビルドしない）、[DESIGN §2](../../../DESIGN.md)（決定性。失敗は声高に、暗黙のフォールバックは無し）。
- `bajutsu/config.py`（`LaunchServer.cmd`、`MockServer.cmd`、`AppConfig.build`、および新しい `LaunchServer.image` / `port`）、`bajutsu/runner/launch_server.py`（`sandbox` モードがコンテナに置き換える `start_launch_server` のホスト `Popen`）、`bajutsu/serve/operations.py`（アップロードされたバンドルに対して `build=None` を強制する `start_run`、`state.upload`）、`bajutsu/serve/jobs.py`（`_build_app`。run ジョブ機構） — 修正が触れる箇所。

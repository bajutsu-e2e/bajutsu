[English](BE-0055-operational-logging.md) · **日本語**

# BE-0055 — ホスト型 serve の運用ログ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0055](BE-0055-operational-logging-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0055") |
| 実装 PR | [#334](https://github.com/bajutsu-e2e/bajutsu/pull/334) |
| トピック | Web UI のホスティング |
<!-- /BE-METADATA -->

## はじめに

ホスト型の `bajutsu serve` は、**複数プロセスのマルチテナント**サービスになりました。制御プレーンとリモートの macOS worker からなり、org でスコープされます。永続化、identity、マルチテナントの実装が着地した（[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)）ためです。

ところが、ツール自身の**運用ログはほぼ存在しません**。コードベース全体で `getLogger` の呼び出しは 1 箇所だけです。stdlib のリクエストハンドラは、リクエストごとのログを意図的に抑止しています。本提案は、ホスト型 serve のための**運用ログの契約**を設計します。ログは**構造化**（JSON）し、id で**相関**を取り、**機密をマスク**したうえで、**stdout** に出します。

契約の対象は、ツール**自身**の診断の軌跡です。すでに存在する 3 つのログ面とは、意図的に切り分けます。

- **証跡。** **テスト対象**の軌跡（`deviceLog`、`appTrace`、network、actionLog）です。evidence サブシステムが取得し、機密をスクラブします（[evidence.md](../../docs/ja/evidence.md)）。本提案の対象ではありません。
- **run 出力。** `--progress` の scenario と step のストリームです。serve の LogBus がライブで配信し、保存します（BE-0015）。本提案の対象ではありません。
- **監査ログ。** 誰が何をしたかを、`audit_log` テーブルに記録します（BE-0015）。隣接しますが別物で、その閲覧手段は別の関心事です。

設計は、prime directive（[CLAUDE.md](../../CLAUDE.md)、[DESIGN.md](../../DESIGN.md)）に従います。決定的な `run` / CI ゲートは、軽量に保ちます。stdlib のみで、静かで、人間が読める形です。**機密は、ログ行に絶対に出しません。**

## 動機

- **プロセスをまたいだ追跡ができない。** 制御プレーンと worker が別プロセスで動きます。「org X の run が dispatch されない」のような 1 つのユーザ操作を、リクエストから、enqueue されたジョブ、それを実行した worker まで追う手段が、今はありません。run の**出力**は LogBus で流れますが、ツールの**運用イベント**は相関が取れていません。
- **運用出力に機密マスクの保証がない。** その場しのぎの `print` やログ呼び出しは、解決済みの `${secrets.X}` の値、オペレータトークン、OAuth の session id、`ANTHROPIC_API_KEY` を漏らしかねません。証跡には redaction の仕組みがありますが、運用チャネルには同じ保証がありません。
- **設計の担当が不在。** [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) は、可観測性の行で「構造化 JSON ログ」に触れていますが、設計は先送りしています。契約を所有する項目はありません。
- **読み手は将来のサイト信頼性エンジニア（SRE）。** ホスト型サービスを運用する人には、grep でき、アラートを張れ、相関の取れるログが必要です。テスト対象の証跡をかき分けて探す形にはしたくありません。

## 詳細設計

### 2 層の切り分け（ゲートを太らせない）

決定的な `run`（Tier 2 / CI ゲート）は、現状の挙動を保ちます。人間が読める形で静かに動き、**stdlib ロギングのみ**を使い、新しい依存を足しません。構造化した運用ログは、**serve モード**の関心事です。差分をアプリごとのコードではなく config に置くため、環境変数で選択します。

- `BAJUTSU_LOG_FORMAT=json|text`。serve は `json`、CLI は `text` を既定にします。
- `BAJUTSU_LOG_LEVEL`。標準のレベル名を取り、モードごとに妥当な既定値を持ちます。
- **出力先は stdout のみ**（12-factor）。集約（Sentry、Prometheus、OpenTelemetry）は**デプロイ側**の仕事で、BE-0015 の可観測性スコープに残します。本項目はこうした依存を足さないので、Linux ゲートでテストできます。

### どこに差し込むか（継ぎ目）

いまのコードベースには、運用ログと呼べるものがほぼありません。[`serve/jobs.py`](../../bajutsu/serve/jobs.py) に `logging.getLogger(__name__)` が 1 つあるだけです。2 つの HTTP リクエストハンドラは、どちらも per-request ログを意図的に黙らせています。`serve/handler.py` の `Handler.log_message` と、`network.py` のスタブです。ルート設定も `contextvars` の利用も、どこにもありません。したがって配線は更地への作業で、1 か所に据えます。

- **新規 `bajutsu/serve/oplog.py`** が、契約一式を持ちます。stdlib の `logging` を隠さないように、この名前にします。`configure(format, level)` が、ルートのフォーマッタと、redact と相関のフィルタを据えます。`contextvars`、event 名のレジストリ、JSON フォーマッタも、このモジュールに置きます。`serve` が起動時に呼びます。CLI と `run` の経路は import しないので、ゲートは stdlib のみで静かなままです。
- **HTTP surface は 2 つ、フィルタは 1 枚。** フィルタは**ルートロガー**に座るので、2 つのサーバを両方覆います。1 つは、hosted の FastAPI アプリ（[`serve/server/app.py`](../../bajutsu/serve/server/app.py)）です。リクエスト境界に `@app.middleware("http")` がすでにあり、`request_id` を採番して contextvar に bind する自然な場所です。もう 1 つは、ローカルの stdlib サーバ（[`serve/handler.py`](../../bajutsu/serve/handler.py) の `do_GET` と `do_POST`）です。JSON は serve モードで有効にし、ローカル CLI は text のままです。
- **worker の境界。** [`serve/server/worker_job.py`](../../bajutsu/serve/server/worker_job.py) の `execute_job_spec(spec, …)` が、ジョブの id を bind する場所です。job spec は **`job_id`、`org`、`actor`** を運びます。`worker_job.py` が組み立てます。**`run_id` は、worker が run を始めるときに採番**します。spec には含まれないので、worker がそこで bind します。これは下の不変条件を訂正するものです。したがって、プロセスをまたぐ相関は、両側にすでにある**共有 id の値**で行います。context オブジェクトは伝播しません。
- **redaction の再利用。** フィルタは、既存の `Redactor`（[`redaction.py`](../../bajutsu/redaction.py) の `redact_text`）を値マスクに使います。新しいキーベースのマスクを足します。

### 契約（機械でチェックできる不変条件）

運用ログは、タイムスタンプと順序が変わるので**非決定的**です。証跡（[DESIGN §2](../../DESIGN.md)）と違って、バイト一致は求めません。代わりに、**スキーマと不変条件の束**を定めます。それぞれをゲートのテストで検証します。

1. **機密ゼロ（redaction）。** redact するフィルタまたはフォーマッタを、**ルートロガー**に 1 枚かませます。サードパーティのものを含む**すべての record** がスクラブされるので、正しさを各呼び出し側のふるまいに依存させません。マスクは 2 系統です。
   - **値ベース。** 既知の機密値をマスクします。解決済みの `${secrets.X}`、`BAJUTSU_SERVE_TOKEN`、OAuth の session id と client secret、`ANTHROPIC_API_KEY` です。既存の仕組み（`redaction.py`）を再利用します。
   - **キーベース。** 機密になりうる構造化フィールドのキーを、値に関係なくキー名でマスクします。`authorization`、`token`、`secret`、`password`、`cookie`、`api_key` です。

   *テスト：* 素の `logging.getLogger("anything").info(<機密>)` は、生の機密を出しません。`{"authorization": "Bearer …"}` を持つ record は、マスクされます。

2. **相関 id の貫通。** リクエストの入口で `request_id` を採番します。FastAPI のミドルウェアと、stdlib ハンドラの `do_*` が担います。worker 側では、`execute_job_spec` が spec から `job_id`、`org`、`actor` を bind します。run が `run_id` を採番した時点で、それも bind します。id は `contextvars` に保持し、ロギングの `Filter` がすべての record に注入します。この仕組みは framework 非依存なので、stdlib ハンドラと FastAPI アプリの両方で動きます。プロセスをまたぐ相関は、context の伝播ではなく**共有 id** で行います。`job_id`、`org`、`actor` は job spec で運ばれ、`run_id` は run 自身の id です。context オブジェクトをプロセス境界越しに渡さなくても、両側が同じ値を出力できます。

   *テスト：* アプリに 1 リクエストを流すと、その間の record がすべて同じ `request_id` を持ちます。worker の `execute_job_spec` の record は、`job_id`、`run_id`、`org` を持ちます。

3. **構造化スキーマ（serve）。** serve のログ行は 1 行の JSON で、形は固定です。`ts, level, logger, event?, msg, request_id?, org?, actor?, job_id?, run_id?` です。

   *テスト：* 出力された各行が、必須キーと型を備えた JSON として parse できます。

4. **ゲートを汚さない（2 層）。** 運用ログは、run の合否に絡まない側チャネルです。その設定は、既定の経路で重い依存を引きません。

   *テスト：* 既存の import guard が green のままです。ロギングの設定は stdlib のみです。

5. **イベント分類。** SRE が `event=` で grep やアラートをできるよう、**安定した event 名**の小さなレジストリを持ちます。例は、`run.dispatched`、`run.recorded`、`oauth.login`、`quota.rejected`、`worker.job.started`、`worker.job.finished`、`artifact.upload.failed` です。

   *テスト：* 主要なフローが、期待する `event` を出します。

### redaction の再利用

運用チャネルは、既存の `redaction.py` の値マスクを通します。上記のキーベースのマスクを足します。「何を機密とみなすか」の単一の真実を保てます。secret 変数（[BE-0032](../BE-0032-secret-variables/BE-0032-secret-variables-ja.md)）と、AI 経路を redact する方向（[BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md)）が、これを共有します。

機密は 2 つのスコープに存在するので、値マスクの対象は**2 つのソース**を持ちます。

- **静的（プロセス存続中）、制御プレーン側。** `BAJUTSU_SERVE_TOKEN`、OAuth の client secret と session id、`ANTHROPIC_API_KEY` です。`oplog.configure` が一度読み、プロセス全体でマスクします。
- **run スコープ、worker 側。** run が `${secrets.X}` から解決した値です。これらはプロセス全体では共有されません。run の開始時に、worker が **run スコープの `Redactor`** を `contextvar` に bind します。この `Redactor` には、その run の解決済み機密値を入れます。ルートのフィルタは、その run の間に出る record についてだけ、これを参照します。スコープは run の終了で外れます。env 由来だけのマスクが残す「run が解決した機密」の穴を、プロセス全体に晒さずに塞げます。

キーベースのマスク（機密になりうるフィールド*名*）が、両方のソースを下支えします。どちらの集合にも値がない構造化値も拾います。

### スコープ外

- **メトリクス、エラートラッキング、分散トレーシング**（Prometheus、Sentry、OpenTelemetry）。デプロイと可観測性の領域で、[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) が所有します。
- **監査ログの閲覧 UI。** `audit_log` テーブルは存在します（BE-0015）。それを見せるのは別の関心事です。
- **証跡と run 出力。** evidence サブシステムと LogBus が、すでに所有しています。
- **ファイル出力とプロセス内ローテーション。** 出力先は stdout のみです。「検討した代替案」を参照してください。

## 検討した代替案

- **呼び出し側ごとの redaction**（各ログ呼び出しが自分の値をマスクする）。却下しました。規律に依存し、サードパーティライブラリ経由で漏れます。ルートのフィルタが、構造的な保証になります。
- **プロセス内ローテーション付きのファイル出力**（`BAJUTSU_LOG_FILE`）。見送りました。stdout に出し、デプロイ側が集約する形（12-factor）なら、ツールは単純で、ゲートも依存ゼロに保てます。セルフホストで必要になれば、後から足せます。
- **相関に `threading.local` や id の明示的な引き回しを使う。** `contextvars` を採用しました。stdlib ハンドラのスレッドと、FastAPI の async および threadpool の両方で動きます。すべてのシグネチャに id を通す必要もありません。
- **構造化ロギングのライブラリ**（`structlog` など）。今は却下しました。stdlib のみでゲートを軽く保ちます。手書きの JSON フォーマッタで足りなくなれば、再検討します。
- **「可観測性と運用」という専用トピックの新設。** 見送りました。本項目は、当面「Web UI のホスティング」の下に置きます。監査ログの閲覧やメトリクスの項目が続くなら、その時に専用トピックを切り出せます。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

- [DESIGN.md](../../DESIGN.md) §2：決定性優先と機密マスク。
- [BE-0015 — Web UI の公開ホスティング](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)：ホスト型トポロジと、本項目が実体化する「構造化 JSON ログ」の可観測性の行。
- [BE-0032 — Secret 変数](../BE-0032-secret-variables/BE-0032-secret-variables-ja.md)：本項目と共有する機密マスクの仕組み。
- [BE-0047 — AI データ主権](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md)：本項目が運用ログへ広げる「redact された経路」の思想。
- [BE-0011 — ローカル Web UI（`bajutsu serve`）](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)と [BE-0051 — serve のハードニング](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)：本ログが計装する serve。
- [evidence.md](../../docs/ja/evidence.md)：本項目が意図的に切り分ける証跡サブシステム。

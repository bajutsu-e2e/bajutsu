[English](BE-0055-operational-logging.md) · **日本語**

# BE-0055 — ホスト型 serve の運用ログ

<!-- BE-METADATA -->
| 提案 | [BE-0055](BE-0055-operational-logging-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **提案** |
| トラック | [提案](../../README-ja.md#提案) |
| トピック | Web UI のホスティング（クラウド / セルフホスト） |
<!-- /BE-METADATA -->

## はじめに

永続化・identity・マルチテナントの実装が着地し
（[BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)）、ホスト型の
`bajutsu serve` は**複数プロセス・マルチテナント**のサービスになりました。制御プレーンとリモートの macOS
worker から成り、org でスコープされます。ところが、ツール自身の**運用ログはほぼ存在しません**。コードベース
全体で `getLogger` の呼び出しは 1 箇所だけで、stdlib のリクエストハンドラはリクエストごとのログを意図的に
抑止しています。本提案は、ホスト型 serve のための**運用ログの契約**を設計します。**構造化**（JSON）し、id で
**相関**を取り、**機密をマスク**したうえで、**stdout** に出します。

これはツール**自身**の診断の軌跡であり、すでに存在する 3 つのログ面とは意図的に切り分けます。

- **証跡**：**テスト対象**の軌跡（`deviceLog`、`appTrace`、network、actionLog）。evidence サブシステムが取得し
  機密をスクラブします（[evidence.md](../../../docs/ja/evidence.md)）。本提案の対象ではありません。
- **run 出力**：`--progress` の scenario/step ストリーム。serve の LogBus 経由でライブ配信・保存します
  （BE-0015）。本提案の対象ではありません。
- **監査ログ**：誰が何をしたかを `audit_log` テーブルに記録します（BE-0015）。隣接しますが別物です（その閲覧
  手段は別の関心事）。

設計は prime directive（[CLAUDE.md](../../../CLAUDE.md)、[DESIGN.md](../../../DESIGN.md)）に従います。決定的な
`run` / CI ゲートは軽量（stdlib のみ・静か・人間可読）に保ち、**機密はログ行に絶対に出さない**ことが前提です。

## 動機

- **プロセスをまたいだ追跡ができない。** 制御プレーンと worker が別プロセスのため、1 つのユーザ操作（「org X
  の run が dispatch されない」）を、リクエストから、enqueue されたジョブ、それを実行した worker まで追う手段が
  今はありません。run の**出力**は LogBus で流れますが、ツールの**運用イベント**は相関が取れていません。
- **運用出力に機密マスクの保証がない。** その場しのぎの `print`／ログ呼び出しは、解決済みの `${secrets.X}` 値、
  オペレータトークン、OAuth の session id、`ANTHROPIC_API_KEY` を漏らしかねません。証跡には redaction の仕組みが
  ありますが、運用チャネルには同じ保証がありません。
- **設計の担当が不在。** [BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)
  は可観測性の行で「構造化 JSON ログ」に触れていますが、設計は先送りで、契約を所有する項目がありません。
- **読み手は将来の SRE。** ホスト型サービスを運用する人には、grep でき、アラートを張れ、相関の取れるログが
  必要です。テスト対象の証跡をかき分けて探す形にはしたくありません。

## 詳細設計

### 2 層の切り分け（ゲートを太らせない）

決定的な `run`（Tier 2 / CI ゲート）は現状の挙動を保ちます。人間可読・静か・**stdlib ロギングのみ**で、新しい
依存は足しません。構造化された運用ログは **serve モード**の関心事です。差分をコードではなく config に置くため、
環境変数で選択します。

- `BAJUTSU_LOG_FORMAT=json|text`：serve は `json`、CLI は `text` を既定にします。
- `BAJUTSU_LOG_LEVEL`：標準のレベル名。モードごとに妥当な既定値を持ちます。
- **出力先は stdout のみ**（12-factor）。集約（Sentry／Prometheus／OpenTelemetry）は**デプロイ側**の仕事で、
  BE-0015 の可観測性スコープに残します。本項目はそうした依存を足さないので、Linux ゲートでテスト可能なまま
  です。

### 契約 — 機械チェック可能な不変条件

運用ログは**非決定的**（タイムスタンプや順序）なので、証跡（[DESIGN §2](../../../DESIGN.md)）と違って
バイト一致は求めません。代わりに**スキーマと不変条件の束**を定め、それぞれをゲートのテストで検証します。

1. **機密ゼロ（redaction）。** redact するフィルタ／フォーマッタを**ルートロガー**に 1 枚かませ、サードパーティ
   のものを含む**すべての record** がそこを通るようにします。正しさを各呼び出し側のふるまいに依存させません。
   マスクは 2 系統です。
   - **値ベース**：既知の機密値（解決済み `${secrets.X}`、`BAJUTSU_SERVE_TOKEN`、OAuth の session id／client
     secret、`ANTHROPIC_API_KEY`）。既存の仕組み（`redaction.py`）を再利用します。
   - **キーベース**：機密になりうる構造化フィールドのキー（`authorization`、`token`、`secret`、`password`、
     `cookie`、`api_key`）を、値に関係なくキー名でマスクします。

   *テスト*：素の `logging.getLogger("anything").info(<機密>)` が生の機密を出さない。`{"authorization": "Bearer …"}`
   を持つ record がマスクされる。

2. **相関 id の貫通。** リクエスト入口で `request_id` を採番し、dispatch された run は `job_id` / `run_id` /
   `org` / `actor` を持ちます。id はリクエスト（および worker 側ではジョブ）の境界で bind した `contextvars` に
   保持し、ロギングの `Filter` がすべての record に注入します。framework 非依存なので、stdlib ハンドラと FastAPI
   アプリの両方で動きます。プロセスをまたぐ相関は、context の伝播ではなく**共有 id** で行います（`job_id` /
   `run_id` / `org` はすでに job spec で運ばれています）。

   *テスト*：アプリに 1 リクエストを流すと、その間の record がすべて同じ `request_id` を持つ。worker の
   `execute_job_spec` の record が `job_id` + `run_id` + `org` を持つ。

3. **構造化スキーマ（serve）。** serve のログ行は 1 行 JSON で、形は固定です。
   `ts, level, logger, event?, msg, request_id?, org?, actor?, job_id?, run_id?`。

   *テスト*：出力された各行が、必須キーと型を備えた JSON として parse できる。

4. **ゲートを汚さない（2 層）。** 運用ログは run の合否に絡まない側チャネルであり、その設定は既定経路で重い依存を
   引きません。

   *テスト*：既存の import guard が green のまま。ロギング設定が stdlib のみ。

5. **イベント分類。** SRE が `event=` で grep／アラートできるよう、**安定した event 名**の小さなレジストリを
   持ちます。例：`run.dispatched`、`run.recorded`、`oauth.login`、`quota.rejected`、`worker.job.started`、
   `worker.job.finished`、`artifact.upload.failed`。

   *テスト*：主要なフローが期待する `event` を出す。

### redaction の再利用

運用チャネルは既存の `redaction.py` の値マスクを通し、上記のキーベースのマスクを足します。「何を機密とみなすか」
の単一の真実を保ち、secret 変数
（[BE-0032](../../implemented/BE-0032-secret-variables/BE-0032-secret-variables-ja.md)）や、AI 経路を redact する
方向（[BE-0047](../../proposals/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md)）と共有します。

### スコープ外

- **メトリクス／エラートラッキング／分散トレーシング**（Prometheus、Sentry、OpenTelemetry）：デプロイ／可観測性
  の領域で、[BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) が所有します。
- **監査ログの閲覧 UI**：`audit_log` テーブルは存在します（BE-0015）。それを見せるのは別の関心事です。
- **証跡と run 出力**：すでに所有されています（evidence サブシステム／LogBus）。
- **ファイル出力／プロセス内ローテーション**：stdout のみ。「検討した代替案」を参照。

## 検討した代替案

- **呼び出し側ごとの redaction**（各ログ呼び出しが自分の値をマスク）：却下。規律に依存し、サードパーティ
  ライブラリ経由で漏れます。ルートのフィルタが構造的な保証になります。
- **プロセス内ローテーション付きのファイル出力**（`BAJUTSU_LOG_FILE`）：見送り。stdout ＋ デプロイ側が集約する
  （12-factor）方がツールは単純で、ゲートも依存ゼロに保てます。セルフホストで必要になれば後から足せます。
- **相関に `threading.local` や id の明示的引き回し**：`contextvars` を採用。stdlib ハンドラのスレッドと
  FastAPI の async／threadpool の両方で動き、すべてのシグネチャに id を通さずに済みます。
- **構造化ロギングのライブラリ**（`structlog` など）：今は却下。stdlib のみでゲートを軽く保ちます。手書きの
  JSON フォーマッタで足りなくなれば再検討します。
- **「可観測性・運用」という専用トピックの新設**：見送り。本項目は当面「Web UI のホスティング」の下に置きます。
  監査ログ閲覧やメトリクスの項目が続くなら、その時に専用トピックを切り出せます。

## 参考

- [DESIGN.md](../../../DESIGN.md) §2 — 決定性優先・機密マスク。
- [BE-0015 — Web UI の公開ホスティング](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) — ホスト型トポロジと、本項目が実体化する「構造化 JSON ログ」の可観測性行。
- [BE-0032 — Secret 変数](../../implemented/BE-0032-secret-variables/BE-0032-secret-variables-ja.md) — 本項目と共有する機密マスクの仕組み。
- [BE-0047 — AI データ主権](../../proposals/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md) — 本項目が運用ログへ広げる「redact された経路」の思想。
- [BE-0011 — ローカル Web UI（`bajutsu serve`）](../../implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)、[BE-0051 — serve のハードニング](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md) — 本ログが計装する serve。
- [evidence.md](../../../docs/ja/evidence.md) — 本項目が意図的に切り分ける証跡サブシステム。

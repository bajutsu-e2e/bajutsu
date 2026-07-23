[English](BE-0225-config-project-hub.md) · **日本語**

# BE-0225 — serve の config プロジェクトハブ（登録・一覧・切替・実行）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0225](BE-0225-config-project-hub-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0225") |
| 実装 PR | [#909](https://github.com/bajutsu-e2e/bajutsu/pull/909), [#921](https://github.com/bajutsu-e2e/bajutsu/pull/921), [#923](https://github.com/bajutsu-e2e/bajutsu/pull/923), [#926](https://github.com/bajutsu-e2e/bajutsu/pull/926), [#928](https://github.com/bajutsu-e2e/bajutsu/pull/928) |
| トピック | オーサリング体験 |
| 関連 | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md), [BE-0102](../BE-0102-run-stats-dashboard/BE-0102-run-stats-dashboard-ja.md), [BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view-ja.md), [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md), [BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`bajutsu serve` は、一度に一つの active config だけをバインドします。起動時に config を一つ
（ファイル、Git ソース、またはアップロードした zip バンドルのいずれか）受け取り、run・record・crawl・
実行統計ダッシュボードといったすべてのタブが、その一つのバインディングに対して動きます。別の config に
切り替えたければ、`--config` を変えて `serve` を起動し直すしかありません。一つのアプリを操作するぶんには
これで足りますが、複数の config（複数のアプリ、あるいは一つのアプリの複数ターゲット）を管理するチームが、
それらを並べて見て、一つを選び、実行し、その履歴に戻ってくる、という使い方には向きません。`serve` は
そうした**ハブ**としては力不足なのです。

本提案は、`serve` をそのハブに変えます。それぞれの config を名前付きの**プロジェクト**として登録し、
追加・一覧・切替・実行を Web UI と CLI の両方から行える、軽量な**プロジェクトレジストリ**を導入します。
メトリクスの側面（プロジェクトどうしを比較する機能）は別の提案「cross-project metrics comparison
dashboard」が扱います。本項目は、そのレジストリと、比較機能が土台にするプロジェクト単位の実行まわりの
配線を提供します。

## 動機

今の `serve` には、この隙間を生んでいる事実が二つあります。

- **プロセスあたり config は一つ。** active config は起動時に選ばれ、プロセスの生存期間を通じて固定です
  （[BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view-ja.md) は config を読み取り専用で
  *表示*できるようにしましたが、切り替えはできません）。config を三つ持つチームは、三つのポートで三つの
  `serve` プロセスを動かすことになり、それらを一覧する共通の画面はどこにもありません。
- **実行履歴もその一つの config に閉じている。** 実行統計ダッシュボード
  （[BE-0102](../BE-0102-run-stats-dashboard/BE-0102-run-stats-dashboard-ja.md)）は、今バインドされている
  config の実行履歴を集計します。「*checkout* の config の履歴を見せて」と言うには、まずその config を
  バインドし直して `serve` を起動し直すしかありません。

一方で、ホスト版の設計はまさにこの形をあらかじめ見込んでいます。
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) の下でシングルテナントの
サーバーバックエンドが導入した `projects` テーブル（`id`・`org_id`・`name`（= config のアプリ名）・
`created_at`・`unique(org_id, name)`）と、`runs.project_id` という外部キーがそれです。しかし**今この
`project_id` に書き込むコードは一つもありません**。ホスト版 UI がいずれ育てるはずの「プロジェクトピッカー」を
待つ、未接続のスキャフォールドとして置かれているだけです。ローカルの `serve` にいたっては、プロジェクトという
概念そのものがありません。

つまり、一つの機能が二つの半分に分かれて宙に浮いています。書き込み手のいないホスト版のスキーマと、複数 config を
扱う画面を持たないローカルのランチャーです。本項目は、この**ローカル側の半分を、二つ目のスキーマを新たに作るのでは
なく、ホスト版のスキーマを再利用する形で**埋めます。こうすることで、ローカルのハブと将来のホスト版プロジェクト
ピッカーは同じ概念になります。ローカルのレジストリは、DB 上の実行履歴の一覧がすでにそうしているのと同じく、
BE-0015 が既定でフォールバックする単一の `default` org に解決されます。

**スケジューラではなく、ハブです。** 「config に対して継続的にテストを実行する」は、意図的に*外部トリガーを
受ける*ところまでに範囲を絞っています。手動の **Run** ボタン、CLI の呼び出し、あるいは CI や cron からの
HTTP エンドポイント呼び出しを受けるのであって、Bajutsu 自身がスケジューラを持つわけではありません。ロード
マップはスケジューリングをすでに *Not adopting*（「CI/通知層の領分」）として記録しており、本項目はその判断を
覆しません。トリガーに、プロジェクトで指定できる安定した宛先を与える（cron ジョブや CI ステップが、ファイル
システムのパスを知らなくても「プロジェクト *checkout* を実行」と言える）だけです。結果の側は、既存の webhook
実行通知（[BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications-ja.md)）と
組みます。実行の頻度は Bajutsu の外にとどめます。

これはプライムディレクティブの範囲に収まります。レジストリも実行トリガーも完全に決定的です。プロジェクトは
名前付きの config バインディングであり、実行はランチャーがすでに起動しているのと同じ `bajutsu run` です。
`run`/CI の経路に LLM は入りません。アプリごとの差異は各プロジェクト自身の config（`targets.<name>`）に
とどまるので、ハブそのものは app-agnostic です。config を一覧して選ぶだけで、特定のアプリに関することは
何も持ちません。

## 詳細設計

作業は五つの単位に MECE に分かれます。レジストリのモデル、その永続化、API、UI、そして CLI/トリガーの
画面です。

### 1. プロジェクトのモデル（BE-0015 のスキーマを再利用する）

**プロジェクト**は、config ソースへの名前付きのバインディングです。BE-0015 がすでに定義した `projects`
行（`name`（org 内で一意）・`org_id`（ローカルでは単一の `default` org）・`created_at`）を再利用し、
そこにバインドする **config ソース**を持たせて拡張します。ソースは `serve` が今受け付けている三種類と
同じで、Git（[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)）、zip アップ
ロード（[BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload-ja.md)）、
ローカルのファイルパスです。生のパスとしてではなく、小さな判別付きレコード（`kind` とそのロケータ）として
保存します。こうしておけば、ホスト版バックエンドが後から同じレコードを自前の `ScenarioStore` で解決する
際にも、スキーマを変えずに済みます。プロジェクトに対して実行を開始すると `runs.project_id` に印が付き、
BE-0015 が宙吊りにしていた外部キーがようやく使われます。

### 2. 永続化（ローカルを起点に、ストレージのシームに沿わせる）

レジストリは、BE-0015 が確立したのと同じシームの境界を通じて永続化します。データベースが配線されていれば
（`BAJUTSU_DATABASE_URL`）、プロジェクトとその実行は `projects` / `runs` テーブルに載ります。データ
ベースがない既定のローカル `serve` では、レジストリは serve の状態ディレクトリ配下の小さなディスク上の
ストア（既存の `runs/` ツリーと並ぶ JSON）に永続化するので、一人で使うローカルのハブに Postgres は要りません。
どちらの経路も、`ProjectRegistry` という一つのアクセサ（一覧 / 取得 / 追加 / 削除 / active の解決）の
背後に置き、他のシームを組み立てているのと同じ `_build_server_state` の中で、リポジトリの有無に応じて
組み立てます。

実行履歴はどちらの経路でもプロジェクト単位に分割するので、`GET /api/projects/<name>/runs`、UI の
プロジェクトごとの「直近の実行結果」、そして姉妹の cross-project ダッシュボードは、ローカルでも動きます。
データベースがあれば、その分割は `runs.project_id` 列（単位 1）です。データベースがなければ、ディスク上の
ストアが同じ対応関係を記録します。既存の `runs/` ツリー配下の各実行に、それが属するプロジェクトの印を
付ける（JSON ストア内のプロジェクト→実行 ID の索引で、`project_id` 列のローカル版）ので、プロジェクト
単位の実行一覧は走査ではなく参照で済みます。明示的にプロジェクトを作る前に開始した実行は、起動時 config
から自動登録された active プロジェクトが持ちます。

レジストリを何も設定していないときのローカルの挙動は変わりません。素の `serve --config X` は今まで通り
`X` をバインドし、最初に使うときにそれを active なプロジェクトとして自動登録するので、単一 config の
利用者に後退は起きません。

### 3. API

コントロールプレーンに新しいエンドポイントを追加します。いずれも決定的で、org スコープ（ローカルでは
`default` に解決）です。

- `GET /api/projects`：登録済みプロジェクトの一覧（名前、config ソース、直近の実行サマリ）。
- `POST /api/projects`：config ソースからプロジェクトを登録する（[BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md)
  が定めるホスト時の config ソース allowlist（アップロードと Git のみ）で検証し、ホスト時はクライアント
  指定のファイルシステムパスを受けない）。
- `DELETE /api/projects/<name>`：登録解除する（履歴は残し、バインディングだけを外す）。
- `POST /api/projects/<name>/run`：既存の `RunExecutor` シームを通じてそのプロジェクトの実行を投入し、
  `project_id` に印を付ける。これが外部トリガーの宛先。
- `GET /api/projects/<name>/runs`：そのプロジェクトの実行履歴（cross-project ダッシュボードが集計する
  プロジェクト単位のスライス）。

既存の単一 config 向けエンドポイントはそのまま動き続けます。プロジェクトスコープのものは追加であって、
置き換えではありません。

### 4. UI

serve のシェルに**プロジェクトスイッチャー**（ヘッダーの config ビューアの隣に置くピッカー）と、
**プロジェクト一覧**ビューを設けます。一覧の各行は、プロジェクト名、その config ソース、直近の実行結果、
そして **Run** ボタンを表示します。プロジェクトを選ぶと、プロセスを再起動せずに UI の active なプロジェクトが
切り替わり、既存の各タブ（run / record / crawl / BE-0102 のダッシュボード）はそのプロジェクトの config と
履歴に対して動くようになります。`serve` を単一 config のランチャーからハブに変えるのが、この画面です。

### 5. CLI/トリガーの画面

CI や cron が Web UI なしにハブをヘッドレスで駆動できるよう、薄い CLI の対応物を用意します。

- `bajutsu project add <name> --config <source>` / `bajutsu project ls` /
  `bajutsu project rm <name>`。
- `bajutsu run --project <name>`：プロジェクトの config を解決して実行する。`POST
  /api/projects/<name>/run` トリガーと等価です。cron のエントリや CI のステップが呼ぶのはこれで、実行の
  頻度は Bajutsu ではなくその外部システムに置かれます。

## 検討した代替案

- **ローカル専用のプロジェクトスキーマを新たに作る。** 却下しました。BE-0015 がすでに `projects` /
  `runs.project_id` を定義しており、互換性のない二つ目のローカルモデルを持つと、ホスト版ピッカーが着地
  したときに面倒な統合を招くのが確実だからです。スキーマを再利用し、DB 上の実行履歴の一覧がすでにそうして
  いるようにローカルでは `default` org にフォールバックすることで、ローカルのハブとホスト版ピッカーを一つの
  概念に保ちます。
- **複数の `serve` プロセスと静的なインデックスページ。** 現状の回避策で、config ごとに `serve` を一つ
  動かしてポートをブックマークするやり方です。却下しました。履歴が共有されず、切り替えもできず、実行を
  トリガーする一か所もありません。本項目が閉じようとしている隙間そのものです。
- **Bajutsu が一定の頻度でプロジェクトを実行する組み込みスケジューラを足す。** スコープ外であり、かつ
  ロードマップの確定した判断（*Not adopting: scheduling*、つまり「CI/通知層の領分」）に反するため却下しました。
  ハブはプロジェクトで指定できるトリガーを公開し、頻度は CI/cron に委ね、通知の側は
  [BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications-ja.md) と組みます。
- **BE-0015 に取り込む。** 却下しました。BE-0015 はホスト版のマルチテナントなトポロジー（OAuth、
  ワーカープール、Orka）です。本項目は、BE-0015 のプロジェクトスキーマを初めて実際に使う*ローカル*の
  ハブであり、クラウドがまったくなくても役に立ちます。分けておくことで、ホスト版のスタックを待たずに
  ローカルのゲートだけで着地できます。両者は `関連` で結びます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 1. プロジェクトのモデル：BE-0015 の `projects` 行に config ソースのレコードを足し、`runs.project_id` に印を付ける。
- [x] 2. 永続化：`ProjectRegistry` シーム（リポジトリがあれば DB 上、なければディスク上の JSON）、どちらの経路でもプロジェクト単位に分割した実行履歴（DB なら `project_id` 列、なければプロジェクト→実行 ID の索引）、起動時 config の active プロジェクトへの自動登録。
- [x] 3. API：org スコープの五つの `/api/projects…` エンドポイント（既存の単一 config 向けへの追加）。#921 のレビューで挙がったユニット 2 からの持ち越し三点をここで解決しました。解決済みの `project_id` は `job_spec` に載ってリモートワーカーまで届き、ワーカー側の `_persist_run` が刻印します。自動有効化は org 対応となり、active プロジェクトのない org で最初に登録したプロジェクト（`POST /api/projects`）が active になります。明示的な `name` により、同じ Git リポジトリの二つの config を区別できます。
- [x] 4. UI：プロジェクトスイッチャーとプロジェクト一覧。再起動なしで active プロジェクトを切り替える。
- [x] 5. CLI：`bajutsu project add/ls/rm` と、ヘッドレスなトリガーとしての `bajutsu run --project <name>`。

### ログ

- 2026-07-11：ユニット 1 と 2 のうち DB 経路を実装しました（#909）。BE-0015 の `projects` 行に、
  プロジェクトが束ねる config ソースを表すレコード（`kind` と `locator`）を持つ nullable な
  `source` 列を alembic マイグレーション `0009` で追加し、シームの境界型 `ProjectRecord` を足し
  ました。`Repository` シームには、org スコープのプロジェクト操作（`create_project` /
  `get_project` / `list_projects` / `delete_project`。登録を解除しても実行履歴は残します）と、実行
  履歴をプロジェクト単位に分割できるよう `list_runs` の `project_id` フィルタを追加しました。
  `create_project` は id を鍵にした冪等な upsert（`session.merge`）です。ユニット 3 の
  `POST /api/projects` ハンドラは、既存の `(org_id, name)` を先に `get_project` で解決して
  その id を再利用しながらソースを束ね直す必要があります。こうすれば id を鍵にした経路の
  ままとなり、`(org_id, name)` の一意制約に抵触しません。また、マイグレーション `0010` で
  `runs.project_id` の FK に `ON DELETE SET NULL` を追加しました。これにより、実行履歴を持つ
  プロジェクトを削除しても Postgres で `IntegrityError` が発生しなくなり、「登録を解除しても
  実行履歴は残す」という契約が成立します。これらのユニットで残るのは、この DB 経路と
  データベースを持たないローカルの `serve` 向けのディスク上 JSON ストアを一つの
  `ProjectRegistry` シームに束ねること、および起動時 config を active プロジェクトとして
  自動登録することです。
- 2026-07-11：ユニット 2 のレジストリシームと配線を実装しました（#921）。`bajutsu/serve/project_registry.py`
  を追加し、一つの `ProjectRegistry` プロトコルの下に二つのバックエンドを置きました。`SqlProjectRegistry`
  はユニット 1 の `Repository` へ委譲し、実行履歴を `project_id` 列で分割します（active プロジェクトは
  プロセス内で保持します）。`LocalProjectRegistry` はデータベースを持たない場合の既定で、`runs_dir` の
  隣に置く JSON ファイルにプロジェクト一覧と active プロジェクト、プロジェクト→実行 ID の索引を持ち、
  書き込みは `LocalProviderSettingsStore` にならって原子的に行います。`add` は既存の `(org, name)` の
  id を再利用するため一意制約に抵触しません。`remove` はどちらの経路でも実行そのものは残したまま
  プロジェクトの印だけを外します（DB は SET NULL、ローカルは索引の削除）。このシームを
  `_build_state` と `_build_server_state` に配線し（`ServeState.project_registry` フィールドを新設）、
  `serve()` は起動時に config を active プロジェクトとして自動登録します（`register_launch_project` と
  `launch_project_identity`）。さらに `_persist_run` は、終了した実行に active プロジェクトの印を付けます
  （データベースがあれば `project_id` 列、なければローカル索引への `tag_run`）。レジストリのエラーが
  ジョブの後処理を壊さないよう防御し、ハブが配線されていなければ何もしません。ユニット 2 は完了し、
  ユニット 3（API）、4（UI）、5（CLI）が残ります。
- 2026-07-11：ユニット 3 の API を実装しました（#923）。`bajutsu/serve/operations/projects.py`
  に五つのエンドポイントを追加しました。`GET /api/projects`（一覧。各プロジェクトのソース、active か
  どうか、最新の実行要約を返します）、`POST /api/projects`（登録と束ね直し。BE-0108 の config ソース
  許可リストで検査し、ホスト版はファイルソースを拒否します）、`DELETE /api/projects/<name>`（登録解除。
  実行履歴は残します）、`POST /api/projects/<name>/run`（外部トリガー）、`GET /api/projects/<name>/runs`
  （プロジェクト単位のスライス）です。両トランスポートに配線しました。stdlib ハンドラには `do_DELETE`
  を新設し、`do_POST` と同じクロスオリジンブロックを掛けます。FastAPI コントロールプレーンには
  `@app.delete` を追加し、CSRF ミドルウェアの対象を DELETE まで広げました。RBAC は `required_role` で
  与えます。登録と登録解除は config の束ね直しにあたるため admin（`/api/config` と同じ）、プロジェクトの
  実行は editor（`/api/run` と同じ）、一覧は読み取りです。#921 の持ち越し三点はここで解決しました。
  `start_run` は active プロジェクトを enqueue の時点で一度だけ解決し、その id を `Job` に載せます
  （`job_spec` を通じてリモートワーカーまで届き、ワーカーの `_persist_run` は自前のレジストリなしで
  `runs.project_id` を刻印します。終了時に解決する競合と、サーバーバックエンドで刻印されない問題が
  解消します）。`POST /api/projects` は、active プロジェクトのない org で最初に登録したプロジェクトを
  active にします（`default` 以外の org も API 経由で active プロジェクトを持てます）。明示的な `name` は
  同じ Git リポジトリの二つの config を区別します。active でないプロジェクトの実行は 409 です。ライブな
  再バインドはユニット 4 のスイッチャーが担います。この MVP の範囲は作者と確認しました。ユニット 4
  （UI）と 5（CLI）が残ります。
- 2026-07-11：ユニット 5 の CLI を実装しました。`bajutsu/cli/commands/project.py`（`project add` /
  `ls` / `use` / `rm`）と `run --project <name>` を追加しました。どちらも `serve` と同じストアを操作
  します。`BAJUTSU_DATABASE_URL` があれば DB の `Repository`、なければ runs ディレクトリの隣の on-disk
  JSON です。CLI で登録したプロジェクトは web のハブにも現れ、その逆も成り立ちます。共有モジュール
  `bajutsu/cli/_projects.py` が `open_registry` と `source_from_config` / `config_from_source` の組を
  持ちます。後者は前者の逆変換で、`--config` の spec を復元し（動く `ref` より固定の `sha` を優先）、
  `run --project X` は X の保存済みソースを解決して通常の run 経路を駆動します。API のトリガーと違い、
  ステートレスな CLI は呼び出しごとに config を解決し直すので、active バインディングへの切り替えなしに
  任意の名前のプロジェクトを実行します（409 はありません）。`upload` ソースはローカルにチェックアウトが
  ないため拒否します。ユニット 4（UI）が残ります。
- 2026-07-11：ユニット 4 の UI を実装しました（#928）。ユニット 3 の `run_project` が先送りにした
  ライブな再バインドとして、`POST /api/projects/<name>/activate` を追加しました。`activate_project` は
  プロジェクトの保存済みソースから `--config` の spec を復元し（`config_from_source`。`serve` が CLI を
  import せずに使えるよう、`cli/_projects.py` から `bajutsu.config_source` へ移しました）、既存のバインダ
  （git は `bind_git_config`、file は `bind_config`）で `state.config` を貼り替えます。active プロジェクト
  はバインドが成功したあとにだけ切り替わるので、失敗した再バインドがハブを読み込めない config に残す
  ことはありません。`None` のソースは束ねる対象がなく（400）、`upload` のバンドルは再展開するチェック
  アウトがありません（409）。RBAC は activate を admin として扱います（config の束ね直しにあたり、
  `/api/config` と同じです）。serve のシェルには、ヘッダのスイッチャー（config ビューアの隣に置く
  ネイティブな `<select>`）とプロジェクト一覧のモーダルが加わります。各行はプロジェクト名とその config
  ソース、最新の実行結果を示し、**Run** でそのプロジェクトを active にしてから Replay を開きます。どちらも
  ハブが存在する（プロジェクトが 1 件以上ある）まで隠すので、単一 config の `serve` は従来どおりです。
  active にすると config ラベルと共有のターゲットおよびシナリオ一覧を読み込み直すので、再起動なしで
  どのタブも切り替え先の config に対して動きます。プロジェクトの追加と削除はユニット 5 の
  `bajutsu project` CLI で行います。BE-0225 は完了です。

## 参考

`bajutsu/serve/`、`bajutsu/serve/server/db.py`（`projects` / `runs` テーブル）、
[architecture](../../docs/ja/architecture.md)、[cli](../../docs/ja/cli.md#serve)、
[reporting](../../docs/ja/reporting.md)。
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)（再利用する
ホスト版スキーマ）、[BE-0102](../BE-0102-run-stats-dashboard/BE-0102-run-stats-dashboard-ja.md)
（スイッチャーが切り替える config 単位のダッシュボード）、
[BE-0187](../BE-0187-serve-config-view/BE-0187-serve-config-view-ja.md)（本項目が切替可能にする
読み取り専用の config ビューア）、
[BE-0099](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications-ja.md)（外部
トリガーのループの通知側）、そして本項目が記録するプロジェクト単位の実行履歴を集計する姉妹提案
「cross-project metrics comparison dashboard」。

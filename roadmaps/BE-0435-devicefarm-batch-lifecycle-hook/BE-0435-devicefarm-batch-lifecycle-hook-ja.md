[English](BE-0435-devicefarm-batch-lifecycle-hook.md) · **日本語**

# BE-0435 — Device Farm 実行のためのサーバ側バッチライフサイクルフック

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0435](BE-0435-devicefarm-batch-lifecycle-hook-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0435") |
| トピック | デバイスクラウド実行 |
| 関連 | [BE-0432](../BE-0432-devicefarm-pretest-extension-hook/BE-0432-devicefarm-pretest-extension-hook-ja.md), [BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`DeviceFarmBatchProvider.submit` は、テストスペックの描画、パッケージのビルド、アップロード、
Device Farm run のスケジュール、判定（verdict）の収集を行います。実体は
`bajutsu/serve/batch_provider/device_farm_batch_provider.py` にあります。

[BE-0432](../BE-0432-devicefarm-pretest-extension-hook/BE-0432-devicefarm-pretest-extension-hook-ja.md)
は `pre_test_commands` を追加し、呼び出し側が `pre_test` フェーズにデバイスホスト側のセットアップを
差し込めるようにしました。このフックは *Device Farm ホスト上*で、run の最中に、シェルとして実行されます。

しかし、そこでは実行できない per-run セットアップがあります。それは `submit` が動く場所——`serve`
プロセス——で、パッケージのビルド前に実行される必要があり、その結果はデバイスホストのコマンド
ではなく **設定（config）を通じて**テスト対象アプリに届かなければなりません。現状、デプロイ側が
バッチのライフサイクルのこの地点に関与する手段は存在しません。

本提案は、サーバ側のライフサイクルフックを追加します。すなわち、パッケージング前に `serve`
プロセス内で走り run config に launch 環境変数を注入できる `before_submit` ステップと、判定
収集後に走る `after_run` ステップです。特定デプロイのバックエンド固有のセットアップ/ティアダウンは、
`pre_test_commands` がデバイスホスト側のセットアップを `bajutsu/` の外に保つのと同様に、完全に
`bajutsu/` の外に置かれます。

## 動機

Prime directive 3 は Bajutsu をアプリ非依存に保ちます。アプリごとの差分は設定または呼び出し側が
供給するフックに置き、ツール内部には決して置きません。BE-0432 はこれをデバイスホスト側セットアップ
について確立しました。同じ統合作業から、二つ目の別ケースが現れました。IP allowlist の背後にある
staging バックエンドに、Device Farm デバイスから per-run 認証リレー経由で到達する、というものです。

そのリレーは各 run を短命な per-run クレデンシャルで認証します。このクレデンシャルの二つの性質は、
BE-0432 の `pre_test_commands` では満たせません。

- **クレデンシャルは、それを発行できる権限が存在する場所で発行されなければなりません。** per-run
  クレデンシャルの発行自体が、デプロイ自身のインフラに対する特権操作です。`serve` プロセスは
  その権限を持ちます（例：フェデレーションされたクラウド ID）。Device Farm ホストは持ちませんし、
  意図的に持たせてはなりません——BE-0432 は既に、run のクラウド認証情報を行使しうるホストへ
  呼び出し側入力を流すことを拒否しています。したがって発行は run 開始前に `serve` で行うほかありません。

- **その結果はホストではなくテスト対象アプリに届かなければなりません。** アプリは launch 環境変数
  （iOS の `launchEnvironment`、Android の launch intent extras——いずれも既に config の
  `targets.<name>.launchEnv` によって運ばれます）から自身のネットワークスタックを構成します。
  デバイスホストの `pre_test` コマンドはそこに値を置けません。パッケージされデバイス上で
  `bajutsu run` に再パースされる run config だけがそれを置けます。

対称的なティアダウンの必要もあります。run が終わったら、per-run クレデンシャルは失効を待つのではなく
解放すべきです。run が終わったことを知るのは `submit` だけです。

BE-0432 と同様、これらは一切 Bajutsu の内部に属しません。別のデプロイは別のクレデンシャルを発行し、
別の変数を注入し、あるいは何も必要としないかもしれません。Bajutsu は一つの汎用的な継ぎ目（seam）を
提供し、呼び出し側がそれで何をするかには関与しません。

## 詳細設計

### フックのプロトコル

新しい `BatchLifecycleHook` プロトコルと、両ステップに渡される `BatchContext` を導入します。

```python
@dataclass
class BatchContext:
    request: BatchRequest       # run リクエストの読み取り専用ビュー
    work_dir: Path              # build_package が固める（pack する）プロジェクトディレクトリ
    job_id: str                 # serve のジョブ id（checkpoint が保存されるキー）
    launch_env: dict[str, str] = field(default_factory=dict)  # 追加分。パッケージング前に適用される

class BatchLifecycleHook(Protocol):
    def before_submit(self, ctx: BatchContext) -> None: ...
    def after_run(self, ctx: BatchContext, verdict: Verdict | None) -> None: ...
```

`before_submit` は外部セットアップを行い `ctx.launch_env` を埋めてよいです。`after_run` は外部
ティアダウンを行ってよく、収集された `Verdict` を受け取ります。ただし run が verdict の存在する前に
失敗した場合は `None` を受け取ります（下の provider フローを参照）。いずれの経路でも実行されます。

`...` の本体が no-op の既定として働くのは、**`BatchLifecycleHook` を明示的にサブクラス化した
フックの場合のみ**です。構造的部分型として満たすフック——`BAJUTSU_BATCH_HOOKS` の factory が
最も自然に返す形——が片方のメソッドしか定義していない場合、それは `BatchLifecycleHook` では
ありません。`mypy --strict` は代入箇所で弾きますし、実行時には provider の無条件の
`hook.after_run(...)` 呼び出しが *ティアダウンの `finally` の中で* `AttributeError` を送出し、
run の本来の結果を壊してしまいます。したがって片方のステップだけを実装したいフックは、もう一方の
no-op 既定を受け継ぐためにプロトコルクラスを継承しなければなりません。

`ctx.job_id` は serve 層が各ジョブに既に割り当てている安定した識別子であり
（`bajutsu/serve/state/job.py`）、バッチ checkpoint が保存されるキーと同じものです
（`_RepositoryBatchCheckpoint`、`bajutsu/serve/jobs.py`）。これは最初の submit と、その後の
checkpoint 再開とで同一であり、再起動をまたいでティアダウンを成立させる鍵になります
（*checkpoint 再開パス*を参照）。

### Provider の変更

`DeviceFarmBatchProvider.__init__` に `hooks: Sequence[BatchLifecycleHook] = ()` を追加し、
`submit` はコンテキストを構築できるようジョブ id を受け取ります。`submit` は既存フローの前後で
これらのフックを呼びます。

1. `ctx = BatchContext(request, work_dir, job_id)` を構築します。
2. 各フックの `hook.before_submit(ctx)` を順に実行します。
3. `ctx.launch_env` が空でなければ、**`work_dir` を書き換えずに**パッケージされる config へ
   マージします。`work_dir` は共有のパッケージ/バインディングのルートであり
   （`bajutsu/serve/jobs.py` が `state.devicefarm_package_root or job.cwd or
   state.binding.cwd` を渡します）、並行するバッチジョブが同じディレクトリを固めるため、そこで
   config ファイルを書き換えると、ある run の発行したクレデンシャルが別の run の zip に入り込み、
   さらにユーザーのプロジェクト config に per-run の値が残ってしまいます。したがって：
   - `work_dir / request.config` を読み込み、各エントリを `targets[request.target].launchEnv`
     にマージし（キー衝突時は呼び出し側エントリが勝つ）、
   - マージ後のテキストを `build_package` に `extra_texts` の per-submit オーバーレイとして渡します。
     このとき **config の arcname を、走査対象の `entries` から除外**し、zip にマージ後の config が
     ちょうど一つだけ入るようにします（`build_package` の `extra_texts` ループは arcname を
     *追加*するのであって、走査で入った同名エントリを置換しません——
     `bajutsu/common/cloud/devicefarm/_functions.py`）。
4. **衝突ガード（loud に失敗する）。** デバイス上で `bajutsu run` は二つの launch env ソースを
   `{**target_env, **scenario.preconditions.launch_env}` としてマージします
   （`bajutsu/run/cli.py:993`）。そのため、シナリオが自身の `preconditions.launchEnv` に同じキーを
   設定していると、注入した値を暗黙に上書きしてしまい、アプリはシナリオの古い値で起動し、リレーは
   原因の手掛かりが無いまま run を拒否します。これを決定論的に保つため、`submit` は `work_dir` から
   参照シナリオの `preconditions.launchEnv` を読み込み、`ctx.launch_env` に存在するキーを宣言している
   シナリオがあれば、パッケージング前に submit 時点でシナリオ名・キー・ファイルを示すメッセージと
   ともに例外を送出します。（これは provider にシナリオ preconditions の狭い読み取りを新たに与えます。
   現状 provider はシナリオをファイルパスとしてのみ扱います。）
5. スペックの描画、パッケージのビルド、アップロード、スケジュール、収集——ここは不変です。
6. `finally` の中で、各フックの `hook.after_run(ctx, verdict)` を逆順に実行します。ティアダウンが
   セットアップと鏡写しになり、常に走るようにするためです。`verdict` は `Verdict | None` です。
   パッケージング・アップロード・収集が verdict の存在する前に失敗した場合は `None` になり、それでも
   ティアダウンは走り、元の例外はそのまま伝播します（未バインドの `verdict` による
   `UnboundLocalError` に置き換わることはありません）。

フックが無い場合（既定の `()`）、`submit` は現状と完全に同じ挙動になり、パッケージされる config
はバイト単位で同一になります。

### checkpoint 再開パス

checkpoint 再開は再起動後に起こります。`submit` は永続化された run ARN を見つけ
（`checkpoint.load()`）、既にスケジュール済みの run に対して `collect_run(...)` を返し、描画・
パッケージング・アップロード——したがって `before_submit` と config マージ——をスキップします
（`bajutsu/serve/batch_provider/device_farm_batch_provider.py`）。フックは新しいプロセス内の新規
オブジェクトです。ここでは `before_submit` は実行されていないため `ctx.launch_env` は空であり、
メモリ上のフックは、再起動前の `before_submit` が発行したものへの handle を一切持ちません。

`after_run` はこのパスでも、同じ `finally` の中で、再開した run の verdict とともに実行されます。
ティアダウンが実際にクレデンシャルを解放するには、`before_submit` が発行したクレデンシャルの
同一性を **`ctx.job_id` をキーとする durable な per-job state に永続化**し、`after_run` が同じ
`job_id` でそれを引いて解放する必要があります。`job_id` は再起動をまたいで安定なので、再開された
`after_run` は——新しいプロセスで `ctx.launch_env` が空であっても——クレデンシャルを見つけて
解放できます。

その durable store は呼び出し側自身のものであり、`bajutsu/` の外にあります（デプロイは自身の
データベースの行や DynamoDB item などを使うかもしれません）。これは prime directive 3 と整合します。
Bajutsu の唯一の寄与は、`ctx.job_id` が最初の submit と再開とで同じ値である、という保証であり、
これがフックに安定した相関キーを与えます。

### フックのロード——サーバ側限定

フックはプロセス起動時に配線され、リクエストからは決して配線されません。`batch_bootstrap` は
環境変数 `BAJUTSU_BATCH_HOOKS`（`module:factory` パスのカンマ区切りリスト）を読み、各 factory を
`importlib` でロードし、その結果を `DeviceFarmBatchProvider` に渡します。デプロイ側はフック
モジュールをイメージに同梱し、その変数で名前を指定します。

これは BE-0432 の信頼境界をそのまま踏襲します。`DeviceFarmBatchProvider` は `render_test_spec` の
引数を HTTP リクエストボディから組み立てます。一方フックはクレデンシャルを発行しパッケージされる
config を書き換えるため、フックの選択や設定をリクエスト経由にすると、BE-0432 が拒否したのと同じ
「ホスト認証情報への到達」と「config 改ざん」をクライアントに与えてしまいます。したがって：

- いかなる `serve` エンドポイント、config フィールド、`BatchRequest` フィールドもフックを
  選択・設定してはなりません。
- フックの同一性はデプロイ時の環境からのみ与えられます。

BE-0432 の `render_test_spec` に対する AST チェックと同じ精神で、リクエスト由来の値がフック選択に
到達しないことをユニットテストで確認します。

### デバイスに届くもの

`ctx.launch_env` 経由で注入された per-run の値は、パッケージされる config に載り、したがって run の
Device Farm アーティファクトに載ります。これはデプロイ自身の Device Farm プロジェクトへアクセス
できるプリンシパルから見えます。これは `pre_test_commands` の内容が既に持つのと同じ露出であり、
デプロイ自身のクラウドアカウント内に閉じます——デバイス egress を共有する他テナントからは見えません。
秘密値を注入するフックは、長命な値ではなく短命な per-run 値（＝本提案の動機となるケース）を発行
すべきです。

## 検討した代替案

- **BE-0432 の `pre_test_commands` だけで済ませる。** 却下。これらのコマンドは Device Farm
  ホスト上で走りますが、ホストは per-run 秘密を発行するクレデンシャルを持たず、BE-0432 自身の決定に
  よって持たせてはなりません。またアプリの launch 環境（パッケージされた config を源とする）に書き
  込むこともできません。二つのフックは代替ではなく補完です。`pre_test_commands` はデバイスホスト
  側セットアップ、`before_submit`/`after_run` はサーバ側セットアップと config 注入のためのものです。
- **専用の `proxy_config` / `vpn_config` / `relay_config` パラメータ。** BE-0432 が却下したのと
  同じ理由で全面的に却下。prime directive 3 はアプリ固有の特別扱いを禁じます。デプロイごとに
  まったく異なる per-run セットアップが必要であり、Bajutsu はバックエンド形状ごとにパラメータを
  増やすのではなく一つの汎用フックを提供します。
- **`render_test_spec` にコールバックを通す。** 却下。`render_test_spec` は入力に対する純粋関数
  です。セットアップとティアダウンは描画ではなく `submit` のライフサイクルの関心事であり、
  provider に置くことで純粋関数を純粋なまま保ち、副作用を一箇所に集めます。
- **`extra_texts` オーバーレイではなく `work_dir` の config を書き換える。** 却下。`work_dir` は
  並行するバッチジョブが固める共有のパッケージルートなので、その場での書き換えはジョブ間で競合し、
  per-run の値をディスク上に残します。オーバーレイはマージ後の config を一回の submit のためだけに
  パッケージし、共有ツリーには一切触れません。
- **シナリオの `preconditions.launchEnv` 層に注入する（デバイス上のマージでフックの値を勝たせる）。**
  衝突ガードを採る形で却下。シナリオ層に届かせるには provider が各シナリオファイルを書き換える
  ことになり、target レベルのキーを一つ注入するよりも呼び出し側コンテンツへの深い変更になります。
  ガードは注入を `targets.<name>.launchEnv` に保ったまま、その層が唯一失う衝突を、submit 時点の
  loud な失敗に変えます（決定論優先）。
- **checkpoint 再開パスで `after_run` をスキップする。** 却下。それは per-run クレデンシャルを失効
  まで漏らすことになり、ティアダウンが存在する目的そのものを損ないます。クレデンシャルの同一性を
  `ctx.job_id` で永続化しておけば、再開された `after_run` がそれを解放できます。
- **フックをリクエストから選択・設定する。** BE-0432 と一貫して、セキュリティ上の理由で却下。
  フックの同一性はデプロイ時のみ。
- **環境変数ではなくビルド/エントリポイントのプラグインレジストリ。** 必要以上に重いため却下。
  Bajutsu の既存 provider レジストリは bootstrap で命令的に populate されます。`BAJUTSU_BATCH_HOOKS`
  は、パッケージメタデータの仕組みを増やさずに、同じ「起動時に設定する」形状を踏襲します。

## 進捗

- [ ] `BatchLifecycleHook` プロトコルと `BatchContext` dataclass（`request`・`work_dir`・
  `job_id`・`launch_env`）を追加する。`after_run` は `Verdict | None` を取る。no-op 既定は
  プロトコルクラスの明示的な継承が必要である旨を明記する。
- [ ] `DeviceFarmBatchProvider.__init__` に `hooks: Sequence[BatchLifecycleHook] = ()` を追加し、
  安定した `job_id` を `submit`/`ctx` に通す。
- [ ] パッケージング前に `before_submit` を呼び、`ctx.launch_env` をパッケージされる config の
  `targets[request.target].launchEnv` にマージし、`build_package` の `extra_texts` オーバーレイで
  パッケージする（config の arcname を `entries` から除外し、`work_dir` は書き換えない）。
- [ ] 衝突ガード：`work_dir` から参照シナリオの `preconditions.launchEnv` を読み込み、注入キーと
  衝突するものがあれば submit 時にシナリオ名・キー・ファイルを示して例外を送出する（さもないと
  `bajutsu run` のマージが暗黙に上書きしてしまう）。
- [ ] `after_run` を `finally` の中で逆順に、`verdict: Verdict | None`（verdict 前の失敗時は
  `None`）で呼ぶ。run 失敗時と checkpoint 再開パスも含む。
- [ ] checkpoint 再開：`ctx.job_id` が submit と再開とで同一であることを保証し、フックが呼び出し
  側の durable な per-job state を通じて、最初の `before_submit` が発行したクレデンシャルを解放
  できるようにする。
- [ ] `batch_bootstrap` で `BAJUTSU_BATCH_HOOKS`（`module:factory`、カンマ区切り）からフックを
  ロードし、provider に渡す。
- [ ] ユニットテスト：フック無し（既定）でパッケージされる config と挙動が現状とバイト単位で同一。
- [ ] ユニットテスト：`before_submit` の `ctx.launch_env` がパッケージされる config にマージされて
  現れ（オーバーレイ経由、`work_dir` は不変）、キー衝突時は呼び出し側エントリが勝つ。
- [ ] ユニットテスト：シナリオの `preconditions.launchEnv` が衝突するとき、衝突ガードが submit 時に
  シナリオ名・キー・ファイルを示して例外を送出する。
- [ ] ユニットテスト：`after_run` が成功時・run 失敗時（`verdict is None`）・checkpoint 再開パスで
  走り、ティアダウン順序がセットアップの逆順であり、verdict 前の失敗がそのまま伝播する
  （`UnboundLocalError` にならない）。
- [ ] ユニットテスト：いかなる `serve` エンドポイント・config フィールド・`BatchRequest` フィールドも
  フックを選択・設定しない（BE-0432 の AST チェックと同様に配線を走査する）。
- [ ] `docs/devicefarm.md` とその日本語ミラーを更新する。

## 参考

[BE-0432](../BE-0432-devicefarm-pretest-extension-hook/BE-0432-devicefarm-pretest-extension-hook-ja.md)
は本提案が補完するデバイスホスト側 `pre_test_commands` フックを追加します。
[BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter-ja.md)
は `render_test_spec`、`build_package`、`DeviceFarmBatchProvider` を導入します。

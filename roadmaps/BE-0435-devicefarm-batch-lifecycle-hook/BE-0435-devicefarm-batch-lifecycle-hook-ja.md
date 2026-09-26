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
Device Farm run のスケジュール、判定(verdict)の収集を行う。実体は
`bajutsu/serve/batch_provider/device_farm_batch_provider.py` にある。

[BE-0432](../BE-0432-devicefarm-pretest-extension-hook/BE-0432-devicefarm-pretest-extension-hook-ja.md)
は `pre_test_commands` を追加し、呼び出し側が `pre_test` フェーズにデバイスホスト側のセットアップを
差し込めるようにした。このフックは *Device Farm ホスト上*で、run の最中に、シェルとして実行される。

しかし、そこでは実行できない per-run セットアップがある。それは `submit` が動く場所——`serve`
プロセス——で、パッケージのビルド前に実行される必要があり、その結果はデバイスホストのコマンド
ではなく **設定(config)を通じて**テスト対象アプリに届かなければならない。現状、デプロイ側が
バッチのライフサイクルのこの地点に関与する手段は存在しない。

本提案は、サーバ側のライフサイクルフックを追加する。すなわち、パッケージング前に `serve`
プロセス内で走り run config に launch 環境変数を注入できる `before_submit` ステップと、判定
収集後に走る `after_run` ステップである。特定デプロイのバックエンド固有のセットアップ/ティアダウンは、
`pre_test_commands` がデバイスホスト側のセットアップを `bajutsu/` の外に保つのと同様に、完全に
`bajutsu/` の外に置かれる。

## 動機

Prime directive 3 は Bajutsu をアプリ非依存に保つ。アプリごとの差分は設定または呼び出し側が
供給するフックに置き、ツール内部には決して置かない。BE-0432 はこれをデバイスホスト側セットアップ
について確立した。同じ統合作業から、二つ目の別ケースが現れた。IP allowlist の背後にある staging
バックエンドに、Device Farm デバイスから per-run 認証リレー経由で到達する、というものである。

そのリレーは各 run を短命な per-run クレデンシャルで認証する。このクレデンシャルの二つの性質は、
BE-0432 の `pre_test_commands` では満たせない。

- **クレデンシャルは、それを発行できる権限が存在する場所で発行されねばならない。** per-run
  クレデンシャルの発行自体が、デプロイ自身のインフラに対する特権操作である。`serve` プロセスは
  その権限を持つ（例：フェデレーションされたクラウド ID）。Device Farm ホストは持たないし、
  意図的に持たせてはならない——BE-0432 は既に、run のクラウド認証情報を行使しうるホストへ
  呼び出し側入力を流すことを拒否している。したがって発行は run 開始前に `serve` で行うしかない。

- **その結果はホストではなくテスト対象アプリに届かねばならない。** アプリは launch 環境変数
  （iOS の `launchEnvironment`、Android の launch intent extras——いずれも既に config の
  `targets.<name>.launchEnv` によって運ばれる）から自身のネットワークスタックを構成する。
  デバイスホストの `pre_test` コマンドはそこに値を置けない。パッケージされデバイス上で
  `bajutsu run` に再パースされる run config だけがそれを置ける。

対称的なティアダウンの必要もある。run が終わったら、per-run クレデンシャルは失効を待つのではなく
解放すべきである。run が終わったことを知るのは `submit` だけである。

BE-0432 と同様、これらは一切 Bajutsu の内部に属さない。別のデプロイは別のクレデンシャルを発行し、
別の変数を注入し、あるいは何も必要としないかもしれない。Bajutsu は一つの汎用的な継ぎ目(seam)を
提供し、呼び出し側がそれで何をするかには関与しない。

## 詳細設計

### フックのプロトコル

新しい `BatchLifecycleHook` プロトコルと、両ステップに渡される `BatchContext` を導入する。

```python
@dataclass
class BatchContext:
    request: BatchRequest       # run リクエストの読み取り専用ビュー
    work_dir: Path              # build_package が zip するプロジェクトディレクトリ
    launch_env: dict[str, str] = field(default_factory=dict)  # 追加分。パッケージング前に適用される

class BatchLifecycleHook(Protocol):
    def before_submit(self, ctx: BatchContext) -> None: ...
    def after_run(self, ctx: BatchContext, verdict: Verdict) -> None: ...
```

`before_submit` は外部セットアップを行い `ctx.launch_env` を埋めてよい。`after_run` は外部
ティアダウンを行ってよく、収集された `Verdict` を受け取り、run が失敗した場合でも実行される。
プロトコル既定で両者とも no-op なので、片方だけ実装してもよい。

### Provider の変更

`DeviceFarmBatchProvider.__init__` に `hooks: Sequence[BatchLifecycleHook] = ()` を追加する。
`submit` は既存フローの前後でこれらを呼ぶ。

1. `ctx = BatchContext(request, work_dir)` を構築する。
2. 各フックの `hook.before_submit(ctx)` を順に実行する。
3. `ctx.launch_env` が空でなければ、パッケージされる config へマージする。
   `work_dir / request.config` を読み込み、各エントリを `targets[request.target].launchEnv`
   にマージし（キー衝突時は呼び出し側エントリが勝つ）、config を `work_dir` に書き戻して
   `build_package` がマージ後の形を zip するようにする。
4. スペックの描画、パッケージのビルド、アップロード、スケジュール、収集——ここは不変。
5. `finally` の中で、各フックの `hook.after_run(ctx, verdict)` を逆順に実行する。ティアダウンが
   セットアップと鏡写しになり、常に走るようにするためである。

checkpoint 再開パス（既にスケジュール済みの run）では `before_submit` と config マージを
スキップする——パッケージは既にアップロード済みだからである——が、再開した run の verdict
収集後に `after_run` は実行する。

フックが無い場合（既定の `()`）、`submit` は現状と完全に同じ挙動になり、パッケージされる config
はバイト単位で同一になる。

### フックのロード——サーバ側限定

フックはプロセス起動時に配線され、リクエストからは決して配線されない。`batch_bootstrap` は
環境変数 `BAJUTSU_BATCH_HOOKS`（`module:factory` パスのカンマ区切りリスト）を読み、各 factory を
`importlib` でロードし、その結果を `DeviceFarmBatchProvider` に渡す。デプロイ側はフックモジュールを
イメージに同梱し、その変数で名前を指定する。

これは BE-0432 の信頼境界をそのまま踏襲する。`DeviceFarmBatchProvider` は `render_test_spec` の
引数を HTTP リクエストボディから組み立てる。一方フックはクレデンシャルを発行しパッケージされる
config を書き換えるため、フックの選択や設定をリクエスト経由にすると、BE-0432 が拒否したのと同じ
「ホスト認証情報への到達」と「config 改ざん」をクライアントに与えてしまう。したがって：

- いかなる `serve` エンドポイント、config フィールド、`BatchRequest` フィールドもフックを
  選択・設定してはならない。
- フックの同一性はデプロイ時の環境からのみ与えられる。

BE-0432 の `render_test_spec` に対する AST チェックと同じ精神で、リクエスト由来の値がフック選択に
到達しないことをユニットテストで確認する。

### デバイスに届くもの

`ctx.launch_env` 経由で注入された per-run の値は、パッケージされる config に載り、したがって run の
Device Farm アーティファクトに載る。これはデプロイ自身の Device Farm プロジェクトへアクセスできる
プリンシパルから見える。これは `pre_test_commands` の内容が既に持つのと同じ露出であり、デプロイ
自身のクラウドアカウント内に閉じる——デバイス egress を共有する他テナントからは見えない。秘密値を
注入するフックは、長命な値ではなく短命な per-run 値（＝本提案の動機となるケース）を発行すべきである。

## 検討した代替案

- **BE-0432 の `pre_test_commands` だけで済ませる。** 却下。これらのコマンドは Device Farm
  ホスト上で走るが、ホストは per-run 秘密を発行するクレデンシャルを持たず、BE-0432 自身の決定に
  よって持たせてはならない。またアプリの launch 環境（パッケージされた config を源とする）に書き
  込むこともできない。二つのフックは代替ではなく補完である。`pre_test_commands` はデバイスホスト
  側セットアップ、`before_submit`/`after_run` はサーバ側セットアップと config 注入のためのものである。
- **専用の `proxy_config` / `vpn_config` / `relay_config` パラメータ。** BE-0432 が却下したのと
  同じ理由で全面的に却下。prime directive 3 はアプリ固有の特別扱いを禁じる。デプロイごとに
  まったく異なる per-run セットアップが必要であり、Bajutsu はバックエンド形状ごとにパラメータを
  増やすのではなく一つの汎用フックを提供する。
- **`render_test_spec` にコールバックを通す。** 却下。`render_test_spec` は入力に対する純粋関数
  である。セットアップとティアダウンは描画ではなく `submit` のライフサイクルの関心事であり、
  provider に置くことで純粋関数を純粋なまま保ち、副作用を一箇所に集める。
- **フックをリクエストから選択・設定する。** BE-0432 と一貫して、セキュリティ上の理由で却下。
  フックの同一性はデプロイ時のみ。
- **環境変数ではなくビルド/エントリポイントのプラグインレジストリ。** 必要以上に重いため却下。
  Bajutsu の既存 provider レジストリは bootstrap で命令的に populate される。`BAJUTSU_BATCH_HOOKS`
  は、パッケージメタデータの仕組みを増やさずに、同じ「起動時に設定する」形状を踏襲する。

## 進捗

- [ ] `BatchLifecycleHook` プロトコルと `BatchContext` dataclass を追加する。
- [ ] `DeviceFarmBatchProvider.__init__` に `hooks: Sequence[BatchLifecycleHook] = ()` を追加する。
- [ ] パッケージング前に `before_submit` を呼び、`ctx.launch_env` をパッケージされる config の
  `targets[request.target].launchEnv` にマージする。
- [ ] `after_run` を `finally` の中で逆順に呼ぶ。checkpoint 再開パスと run 失敗時も含む。
- [ ] `batch_bootstrap` で `BAJUTSU_BATCH_HOOKS`（`module:factory`、カンマ区切り）からフックを
  ロードし、provider に渡す。
- [ ] ユニットテスト：フック無し（既定）でパッケージされる config と挙動が現状とバイト単位で同一。
- [ ] ユニットテスト：`before_submit` の `ctx.launch_env` がパッケージされる config にマージされて
  現れ、キー衝突時は呼び出し側エントリが勝つ。
- [ ] ユニットテスト：`after_run` が成功時・run 失敗時・checkpoint 再開パスで走り、ティアダウン
  順序がセットアップの逆順である。
- [ ] ユニットテスト：いかなる `serve` エンドポイント・config フィールド・`BatchRequest` フィールドも
  フックを選択・設定しない（BE-0432 の AST チェックと同様に配線を走査する）。
- [ ] `docs/devicefarm.md` とその日本語ミラーを更新する。

## 参考

[BE-0432](../BE-0432-devicefarm-pretest-extension-hook/BE-0432-devicefarm-pretest-extension-hook-ja.md)
は本提案が補完するデバイスホスト側 `pre_test_commands` フックを追加する。
[BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter-ja.md)
は `render_test_spec`、`build_package`、`DeviceFarmBatchProvider` を導入する。

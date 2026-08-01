[English](BE-0336-serve-device-farm-bounded-fan-out.md) · **日本語**

# BE-0336 — serve から Device Farm へ投入する、デバイス数を制限したシナリオ単位の分割実行

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0336](BE-0336-serve-device-farm-bounded-fan-out-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0336") |
| 実装 PR | [#1425](https://github.com/bajutsu-e2e/bajutsu/pull/1425)（単位1 — submitter core の移行）、[#1429](https://github.com/bajutsu-e2e/bajutsu/pull/1429)（単位2 — batch executor seam）、[#1435](https://github.com/bajutsu-e2e/bajutsu/pull/1435)（単位3 — シナリオ単位の分割）、[#1438](https://github.com/bajutsu-e2e/bajutsu/pull/1438)（単位4 — デバイス予算）、[#1440](https://github.com/bajutsu-e2e/bajutsu/pull/1440)（単位5 — 長時間ポーリングの永続化）、[#1441](https://github.com/bajutsu-e2e/bajutsu/pull/1441)（単位6 — ドキュメントと fake AWS 検証） |
| トピック | Device-cloud execution |
| 関連 | [BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter-ja.md), [BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction-ja.md), [BE-0198](../BE-0198-serve-state-job-registry-split/BE-0198-serve-state-job-registry-split-ja.md) |
<!-- /BE-METADATA -->

## はじめに

この項目は、serve の Web UI から AWS Device Farm の run を直接投入できるようにし、投入をシナリオ
単位に分割して、デバイス数の上限のもとでシナリオを並列に走らせます。Bajutsu はすでに Android と
iOS のシナリオを Device Farm 上で実行できますが、その経路は
[BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter-ja.md) の batch
submitter に限られます。手動で起動する GitHub Actions ワークフローが、すべてのシナリオを1つの run
にまとめ、確保した1台のデバイス上で順番に実行する形です。この項目は、その機能を、運用者がすでに
run を投入し履歴を見ている面である serve へ移し、形を変えます。スイート全体で1つの run にする代わ
りに、serve はシナリオごとに1つの Device Farm run へ分割し、設定できるデバイス予算 `K` が、同時に
確保する run の本数を抑えます。デバイス予算が主たる制御です。ホスティング環境は、割り当てられた枠
を超えて Device Farm のデバイスを確保してはならず、シナリオ単位の並列は、その予算を `K` まで埋め
つつ、超えないようにします。

## 動機

batch submitter はスイートを直列に実行します。1つの run が1台のデバイスを確保し、その test spec は
シナリオを `bajutsu run` コマンドの列として並べるため、実行時間はシナリオ数に比例して伸び、その間、
プール内の他のデバイスは遊んだままになります。デバイスクラウドの価値は多数のデバイスで同時に走ら
せることにありますが、
[BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter-ja.md) が提供する
形は1台しか使いません。スイートをシナリオごとに1つの run へ分割することが、この遊休を並列に変えます。

この機能は、現在は継続的インテグレーションの中にしか存在しません。貢献者が Device Farm に届くのは、
serve の Web UI から離れて GitHub Actions ワークフローを手で起動する経路です。しかし serve はすでに、
run の投入と、ジョブレジストリの同時実行キャップ
([BE-0198](../BE-0198-serve-state-job-registry-split/BE-0198-serve-state-job-registry-split-ja.md))、
ローカルの device pool による run の履歴を持っています。serve を Device Farm へ広げれば、同じ運用者
が、隠れた第2の経路ではなく、同じ面でクラウドを扱えます。

デバイス数は、2つの要件の優先順位を定める厳しい制約です。Device Farm の確保は有限の枠を消費し、費用
もかかるため、多数の利用者に提供するホスティング環境の Bajutsu は、ある時点で確保するデバイスの台数
を抑えなければなりません。上限のないシナリオ単位の分割は、シナリオごとに1台を確保して枠を超えます。
この項目が導入する並列こそが、上限を必要にする当のものです。したがって、優先されるのはデバイス予算
という要件であり、シナリオ単位の並列は、その予算が抑える側の要件です。予算が許す範囲で並列にシナリオ
を走らせ、それを超えません。

以上は判定には触れません。Device Farm の run は、ローカルの run と同じく、Bajutsu 自身の
`manifest.json` から合否を報告するため、原則1は保たれます。投入とポーリングの機構は `run` / CI の判定
経路の外にとどまり、大規模言語モデル（LLM）の呼び出しはそこに入りません。

## 詳細設計

この設計は、新しい機構を作るのではなく、2つの資産を再利用します。シナリオ単位の実行単位は
[BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter-ja.md) の
`submit_and_collect` であり、上限つきの待ち行列は serve の executor seam と、ジョブレジストリの同時
実行キャップ
([BE-0198](../BE-0198-serve-state-job-registry-split/BE-0198-serve-state-job-registry-split-ja.md))
です。デバイス予算 `K` は、ローカルの run の同時数をすでに抑えているのと同じ atomic なキャップへ写り、
「デバイス数を制限する」と「同時に走る Device Farm ジョブを制限する」が1つの機構になります。

作業は6つの単位に分かれます。

1. **シナリオ単位の投入単位。** `scripts/devicefarm_submit.py` は、すでに `render_test_spec` で
   シナリオのリストを test spec に展開し、`submit_and_collect` でそれをアップロードして実行しますが、
   現状の呼び出し側はスイート全体を `render_test_spec` に渡します。シナリオ1件の呼び出しを分割の単位
   として確認します。各 Device Farm run はちょうど1件のシナリオを運び、そのシナリオの合否を manifest
   から報告します。

2. **serve 向けの Device Farm executor。** serve がすでに選び分けている、ローカルスレッド版と
   データベースキュー版の executor の隣に、実装を1つ加えます。この executor の dispatch は、1件のシナリオ
   に対する Device Farm run を投入し、batch トポロジが課す150分のハードキャップの範囲で run の完了を
   ポーリングし、manifest の合否を記録します。この executor は判定経路の外にとどまります。決定的な
   run をクラウドへ運んで戻すだけで、自身は合否を判断しません。

3. **シナリオ単位の分割。** シナリオの集合を求める要求を、シナリオごとに1つのジョブへ展開する serve
   の投入モードを加えます。serve はローカルの run では、すでにシナリオごとに1つのジョブを投入している
   ため、この分割は、要求されたシナリオを列挙して各々にジョブを登録する薄い層で済みます。

4. **デバイス予算 `K`。** 同時に走る Device Farm ジョブの本数を、ジョブレジストリの同時実行キャップ
   で抑えます。取り合う資源として Device Farm の device pool を鍵にし、同時にデバイスを確保する run
   を最大 `K` 本に抑え、残りは待ち行列に入れます。既定の `K` は設定の `targets.<name>` に置き、
   ターゲットごとの差を設定に置くという原則3に沿わせます。要求はその値を下げられます。Device Farm 側の
   `maxDevices` は1つの run につき1に固定し、1つの run が1台のデバイスを確保するようにして、デバイス
   数は Bajutsu 側のキャップだけが定めるようにします。

5. **長時間ポーリングのための永続化。** ホスティング用のデータベースを備えた backend を本命とします。
   データベースキュー版の executor が各ジョブをキューに入れ、worker がそれを lease する構成では、run
   の状態（queued、submitted、polling、done）が永続化されるため、150分のポーリングの途中で serve が
   再起動しても、run を失わずに再開します。ローカルの単一プロセス版の backend は、単一運用者の構成向
   けに、背景スレッド上の薄い best-effort な経路を保ちます。

6. **ドキュメントとテスト。** 英日両言語の Device Farm の使い方に、serve からの投入の流れとデバイス
   予算の設定を書き加えます。分割とキャップを、
   [BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter-ja.md) がすでに使う
   in-memory の AWS の fake で検証し、ゲートが実際の AWS に触れずに seam を通るようにします。

## 検討した代替案

**`K` 台のプールで1つの run を走らせ、Device Farm にシャーディングさせる。** `K` 台のプールに対する
1回の `schedule_run` は、`K` 回に分けた投入より単純に見えます。しかし Device Farm の custom test
environment は、test spec をプール内の各デバイスに複製し、シナリオをデバイス間に分配しません。この
やり方は、スイートを分割するのではなく `K` 回まるごと再実行します。spec の内部に追加のシャーディング
処理を書かなければシナリオ単位の並列にならないため、この項目は、予算 `K` とデバイス数が同じ数になる
Bajutsu 側で、シナリオごとに1つの run へ分割します。

**Device Farm を継続的インテグレーションに残し、ビルドマトリックスを足す。** GitHub Actions の
マトリックスは、シナリオごとに1つのワークフロー脚を投入し、その本数を抑えられます。この方法は並列を継続的
インテグレーションの層で抑えますが、運用者がすでに run を投入し監視している serve には機能が入らず、
serve の利用者ごと・org ごとのキャップや run の履歴も共有できません。この項目は serve を主たる経路とし、
ヘッドレスな利用のために既存の手動ワークフローを残します。

**ジョブレジストリとは別の、専用のデバイスセマフォ。** Device Farm のデバイス用に独立したセマフォを
置いても動きますが、ジョブレジストリはすでに、全体・利用者ごと・org ごとの atomic なキャップを強制
しています
([BE-0198](../BE-0198-serve-state-job-registry-split/BE-0198-serve-state-job-registry-split-ja.md))。
第2の機構は、その処理を重複させ、そこから乖離します。そのため、この項目はデバイス予算をレジストリの
既存のキャップに通します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] シナリオ単位の投入単位 — シナリオ1件の `submit_and_collect` の呼び出しを分割の単位として露出し、
  確認する。
- [x] serve 向けの Device Farm executor — 1件のシナリオを投入・ポーリングし、判定経路の外で manifest
  の合否を記録する。
- [x] シナリオ単位の分割 — シナリオの集合を求める要求を、シナリオごとに1つのジョブへ展開する。
- [x] デバイス予算 `K` — 同時に走る Device Farm ジョブを、ジョブレジストリで抑える。既定は設定から取り、
  要求ごとに上書きでき、Device Farm の `maxDevices` を1に固定する。
- [x] 長時間ポーリングのための永続化 — ホスティング用の backend で queued/polling/done の状態を永続化し、
  ローカルには best-effort な経路を保つ。
- [x] ドキュメントとテスト — 英日両言語の使い方の更新と、分割・キャップの fake AWS による検証。

ログ:

- [#1425](https://github.com/bajutsu-e2e/bajutsu/pull/1425)（単位1）— submitter の core（`render_test_spec`、`build_package`、`verdict_from_manifest`、
  `submit_and_collect` とその補助、`DeviceFarmClient` / `Transfer` の seam）を
  `scripts/devicefarm_submit.py` から `bajutsu/cloud/devicefarm.py` へ移しました。これにより、後続の
  serve の分割と executor が、カバレッジ計測対象の経路で 1 つの submitter を共有します。
  `scripts/devicefarm_submit.py` は argparse と実際の boto3/urllib アダプタだけを持つ薄い CLI ラッパに
  なりました。シナリオ1件の呼び出しを分割の単位として確認しています（シナリオ1件の spec は
  `bajutsu run` をちょうど1つだけ生成します）。移設に加えて、成果物の収集を堅牢にしました。
  `_safe_extract` は symlink メンバーを拒否し、`_store_artifact` は成果物の拡張子からパス区切りを
  取り除きます（いずれも新しいテスト付き）。既存の fake AWS スイートはそのまま通ります。
- [#1429](https://github.com/bajutsu-e2e/bajutsu/pull/1429)（単位2）— プロバイダ非依存の batch seam `bajutsu/serve/batch_provider.py`（`BatchProvider`
  Protocol ＋ fail-closed レジストリ）と、その最初の具象 `DeviceFarmBatchProvider` を追加しました。具象は
  シナリオ1件の spec を生成し、プロジェクトをパッケージ化し、`deviceSelectionConfiguration` ＋
  `maxDevices:1` で**デバイス1台だけ**を確保する run を1つ投入します（CI batch 経路は静的な
  `devicePoolArn` を据え置きます）。`Job` に `batch` 要求を追加し、serve の `run_job` がそれで分岐して
  seam 経由で投入し、ダウンロードした run を `runs_dir` へ配置し、manifest の合否を記録します（ローカル
  デバイス無し・サブプロセス無し・判定経路の外）。範囲は executor の機構で、in-memory の AWS fake で
  端から端まで検証します。config によるプロバイダ登録とホスティングの object-store／永続ワーカー経路は
  単位3〜5で続けます。
- [#1435](https://github.com/bajutsu-e2e/bajutsu/pull/1435)（単位3）— シナリオ単位の分割投入を追加しました。中立な `POST /api/run-set` エンドポイント
  （`start_run_set`）が、シナリオの集合を求める要求を、シナリオごとに1つの cloud-batch `Job` へ展開します。
  各ジョブは自身の `BatchRequest` を持ち、ほかのすべての run と同じ同時実行キャップ付きの経路
  （`_register_and_dispatch`）で登録されます。対象が使う batch プロバイダは、新しい `cloudBatch` 設定
  （`TargetConfig` → `Effective`）で宣言するため、要求はプロバイダ名を出しません。executor の種別は
  クライアントではなく設定の判断です（設計論点④）。対象とするシナリオは要求された部分集合、または省略時は
  対象ディレクトリ内の全件です。各シナリオは投入の前に信頼できる runnable へ解決するため、未知の名前は
  途中まで分割を残さず要求全体を fail-closed にします。web 対象、`cloudBatch` 未設定の対象、インストール
  可能な `appPath` を持たない対象、プロバイダがパッケージ化する run ディレクトリの外にある config や
  シナリオは、明示的に拒否します。同時にデバイスを確保できる本数を抑えるデバイス予算は単位4です。
- [#1438](https://github.com/bajutsu-e2e/bajutsu/pull/1438)（単位4）— シナリオ単位の分割を、デバイス予算 `K` で抑えるようにしました。
  対象ごとの新しい `cloudBatchBudget`（`TargetConfig` → `Effective`）が既定のデバイス台数を解決し、
  `start_run_set` はジョブレジストリの同時実行キャップを batch プロバイダ（競合するデバイスプール）で
  キー付けします。これにより、対象の cloud run が同時にデバイスを確保するのは高々 `K` 件までで、残りは
  エラーではなく通常の部分的な投入として先送りされます。`JobRegistry.try_register` に、`job.batch.provider`
  でキー付けする `max_concurrent_batch` キャップを追加し、global／per-user／per-org のキャップと同じロック
  配下で atomic に数えます。`ServeState.try_register` は対象ごとの予算を呼び出しごとに転送します。要求側の
  `deviceBudget` は予算を**下げる**ことしかできません（両者の `min`）。要求の非正・不正な値も、設定の
  非正な `cloudBatchBudget` も、黙って「無制限」を意味させず明示的に拒否します（determinism first）。
  Device Farm の `maxDevices` は run ごとに1へ固定したまま（単位2）で、Bajutsu 側のキャップだけがデバイス
  台数を律します。判定経路には触れていません。ホスティング用 backend での queued/polling 状態の永続化と、
  英日両言語の使い方は単位5〜6で続けます。
- [#1440](https://github.com/bajutsu-e2e/bajutsu/pull/1440)（単位5）— cloud-batch の run を、serve の再起動をまたいで永続化しました。
  まず要求が運ばれるようにします。`job_spec` が `Job.batch` をシリアライズし、ワーカーがそれを組み立て直すので、
  DB キュー経由で投入された batch ジョブは、ホスティング用 backend でローカルのサブプロセスへ退化せず batch seam
  で走ります。次に長時間ポーリングが再開できるようにします。`submit_and_collect` は投入した run の ARN を
  `on_scheduled` コールバックで通知し、そこから切り出した `collect_run` が既に投入済みの run をポーリングして
  ダウンロードします。これにより `DeviceFarmBatchProvider.submit` は run を投入した瞬間に ARN を永続化し、再 lease
  時には再アップロードや再投入をせずにその run のポーリングを再開します。実行中の run と確保済みのデバイスを
  孤児にしません。checkpoint は、新しい `jobs.batch_state` 列（マイグレーション 0013 — `NOT NULL`、既定は `{}`）と
  `Repository.save_batch_run_arn` / `load_batch_run_arn` が、ホスティングの DB backend で裏付けます。ローカルの
  単一プロセス backend は checkpoint を渡しません（そこでは再起動で in-memory の状態がすべて失われるため）。設計が
  求める、より薄い best-effort な経路を保ちます。判定経路には触れておらず、in-memory の AWS fake で端から端まで
  検証します。英日両言語の使い方と、より広い fake AWS の検証は単位6です。
- [#1441](https://github.com/bajutsu-e2e/bajutsu/pull/1441)（単位6）— serve からの投入経路を文書化し、fake AWS の
  検証を広げて、この項目を締めくくりました。`docs/devicefarm.md` と `docs/ja/` のミラーに「serve からのディスパッチ」の
  節を加えました。すなわち、submitter の直列 1 実行と対比したシナリオ単位の fan-out、ターゲット単位の 2 つの設定項目
  （プロバイダーを選ぶ `cloudBatch`、プロバイダープールでキーされるデバイス予算 `K` を定める `cloudBatchBudget`）、
  `POST /api/run-set` 要求（任意の `scenarios` 部分集合と下げるだけの `deviceBudget`、未知シナリオでの fail-closed、
  予算が埋まったときの通常の部分ディスパッチ、そして明示的な拒否）、ホスティング backend での再起動をまたぐ
  checkpoint、変わらない manifest 判定です。新しい `tests/serve/test_run_set_devicefarm.py` が、fan-out を実際の
  `DeviceFarmBatchProvider` 経由で in-memory の Device Farm fake に対して端から端まで駆動します。これは先行する単位が
  残したギャップです（`test_run_set` は投入層で止まり、`test_batch_provider` は単一シナリオを走らせるだけでした）。
  3 つのシナリオが 3 つの投入済み run へ fan-out し、それぞれが自分の run を収めて PASS 判定を出すこと、失敗する
  manifest が失敗ジョブとして表面化すること（判定は Device Farm の分類ではなく `manifest.json` 由来）、そしてデバイス
  予算が fake の fan-out を `K` 件に抑えることを検証します。プロダクトコードは変更していません。キャップのプロバイダー
  キー付きの計数は `test_job_registry_caps_concurrent_batch_by_provider_pool` が引き続き覆います。

## 参考

- [BE-0235 — AWS Device Farm batch submitter](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter-ja.md)
  — この項目が分割し、serve から駆動する batch submitter と `submit_and_collect`。
- [BE-0236 — Device-cloud provider abstraction](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction-ja.md)
  — この項目が広げる batch トポロジとは別の、ライブデバイスのトポロジ。
- [BE-0198 — serve state and job-registry split](../BE-0198-serve-state-job-registry-split/BE-0198-serve-state-job-registry-split-ja.md)
  — 同時実行キャップがデバイス予算を強制するジョブレジストリ。
- [`docs/ja/devicefarm.md`](../../docs/ja/devicefarm.md) — この項目が serve からの投入の流れで広げる
  Device Farm の使い方。

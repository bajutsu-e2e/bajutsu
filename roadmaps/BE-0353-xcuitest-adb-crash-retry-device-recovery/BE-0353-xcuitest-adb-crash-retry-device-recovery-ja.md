[English](BE-0353-xcuitest-adb-crash-retry-device-recovery.md) · **日本語**

# BE-0353 — バックエンドクラッシュの再試行でデバイス復旧を強制し、run 全体のクラッシュ復旧時間に上限を設ける

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0353](BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0353") |
| 実装 PR | [#1526](https://github.com/bajutsu-e2e/bajutsu/pull/1526) |
| トピック | Platform support |
| 関連 | [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md), [BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery-ja.md), [BE-0342](../BE-0342-ondevice-lease-teardown/BE-0342-ondevice-lease-teardown-ja.md), [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Bajutsu の run パイプラインは、シナリオ実行中のバックエンドクラッシュから、すでに復旧します。死んだ
リースを破棄し、新しいデバイスをリースし直して、シナリオ全体をやり直します。復旧の上限は2つあります。
リトライ回数（`crash_retries`）と、wall-clock の予算（`crash_recovery_budget`）です。この2つの上限は、
オンデバイスのドライバ適合性スイートとも共有されています
（[BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery-ja.md)、
[BE-0342](../BE-0342-ondevice-lease-teardown/BE-0342-ondevice-lease-teardown-ja.md)）。

この復旧が差し替えるのは、クラッシュした runner プロセスだけです。そのプロセスが動いていたデバイス自体
には、一切手を触れません。クラッシュの本当の原因がデバイス側にある場合があります。たとえば、iOS
シミュレータや Android エミュレータの描画が応答しなくなった場合です。新しい runner プロセスは、まさに
クラッシュを引き起こしたそのデバイスの上で起動し、シナリオは同じ形で再び失敗します。

本項目は、このギャップを2つの部分で埋めます。

1. クラッシュを起点とする再試行に、シナリオが `preconditions.erase: true` を宣言したときにすでに得ら
   れるのと同じ「デバイスを消去して再インストールする」手順を強制します。これまでは、その場での素の
   respawn でした。
   - XCUITest バックエンドでは、この手順はシミュレータのプロセス自体を再起動します
     （`simctl shutdown → erase → boot` に加えて再インストール）。
   - adb バックエンドでは、この手順はアプリレベルのクリーンな状態化です（`uninstall`/`install` に加えて
     `pm clear`）。エミュレータのプロセス自体は再起動しないので、回復できる Android の障害の範囲は、
     iOS 側の対処より狭くなります。詳しくは「検討した代替案」を参照してください。
2. 新しい run 単位の wall-clock 予算（`run_crash_recovery_budget`）を追加し、クラッシュ復旧に使ってよい
   時間を、1つのシナリオ内だけでなく run 全体を通して制限します。これにより、劣化し続けるデバイスは、
   run 自体を早い段階ではっきりと失敗させます。予算がなければ、各シナリオが自分の予算を静かに使い切り、
   ジョブ自身の継続的インテグレーション（CI）タイムアウトに診断不能な形で打ち切られます。

run 単位の予算が上限を設けるのは、「再試行」に費やす時間であり、run 自体ではありません。予算の枯渇は、
シナリオがすでに一度リースしてクラッシュしたあとにしか読み取りません。予算を使い果たしたあとも、残る
シナリオはそれぞれ、劣化したデバイスに対して1回はコールドスポーンでの試行を行ってから失敗します。これは
意図した trade-off です。予算を使い切った時点で以後のリースを一律に打ち切ると、その予算を使い切った
再試行のあとで自力回復していたデバイスまで、見捨てることになるためです（詳細は
`.github/workflows/ios-e2e.yml` の `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET` 直上のコメントを参照してください）。

## 動機

2026-08-06、ある通常の pull request の `actuation (xcuitest)` ジョブは 27 分間実行され、その後、自身の
`timeout-minutes` によって**キャンセル**されました。失敗ではありません。キャンセルは、読み手が手がかりに
できる原因を何も残しません。runner のログは、何が起きたかを伝えています。

どのシナリオもまだ実行されていない時点で、既存のコールド起動復旧のラダー
（[BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md)）が、独自の判断で
シミュレータを1度再起動していました。

```
rebooted Simulator 2A6DC5A9-CE8C-4BC5-959D-F98D5F4BD9AA after a failed cold runner spawn
```

この再起動は、既存のラダーが発火すれば機能する証拠です。しかし、シナリオが実行中で runner が `/health`
に正常に応答している状態では、run の途中でデバイスが劣化しても、それに相当するトリガーはありません。
その2シナリオ後、シミュレータの画面キャプチャがフレームを生成しなくなりました。

```
recordVideo produced no new bytes in runs/.../02-foreach-opens-every-real-catalog-row/scenario.mp4 within 5.0s
```

以降の screenshot 取得は、すべて、タイムアウトしては復旧し、また再びタイムアウトするようになりました。

```
runner channel GET /screenshot failed (attempt 1/3), retrying: timed out
runner channel GET /screenshot failed (attempt 2/3), retrying: timed out
runner channel GET /screenshot: the runner became unreachable past the retry budget — a mid-run crash: ...
runner channel GET /screenshot: the runner recovered from a mid-run crash; re-issuing the idempotent call (recovery 1/3)
```

このパターンはさらに3つのシナリオにわたって繰り返され、最終的に `xcodebuild` 自身が終了しました
（コード 65）。パイプライン自身の `BackendCrashError` 復旧（`bajutsu/runner/pipeline.py`）が runner を
respawn し、シナリオを最初からやり直しました。この respawn は、同じ環境インスタンスを保持したまま、
`bajutsu/runner/pool.py` のコメントの言葉を借りれば「その場でコールド respawn する」動作をします。同じ
シミュレータの上で、新しい runner プロセスを起動します。

新しいプロセスは `/health` に正常に応答したため、デバイス復旧のラダーは二度目には走りませんでした。この
ラダーが発火するのは、起動の試行自体が ready にならなかったときだけです。描画がすでに固まったシミュ
レータを、一見健全に見えるプロセスへそのまま引き渡すケースには、発火しません。同じパターンは次の
シナリオでも、その次のシナリオでも再現しました。ジョブの `timeout-minutes` によって終了し、原因をどの
シナリオも報告しませんでした。

`.github/workflows/ios-e2e.yml` には、2026-08-04 に起きた同種のインシデントが、
`BAJUTSU_CRASH_RECOVERY_BUDGET` の上のコメントにすでに記録されています。シナリオ側の予算も同時に消費
されるジョブでは、その消費がコールド起動復旧の上限に上乗せされ、「2026-08-04 のある実行はこの経路で
`timeout-minutes` に到達した」と記されています。コメントはこの注意点を記録しているだけで、コードは何も
解消していません。`crash_recovery_budget` はシナリオが変わるたびにリセットされ、各シナリオの最初の
クラッシュ起点の再試行は、その場での respawn 以上のものを一度も得られないからです。

## 詳細設計

互いに独立した2つの単位から成ります。

1. **クラッシュ起点の再試行では `preconditions.erase = True` を強制する。ただし強制すると危険な場合は
   除く。**
   `bajutsu/runner/pipeline.py` の `_ScenarioRunner.run_one` の再試行ループは、1回目より後の試行の
   たびに、新しいデバイスをリースします。2回目以降の試行では、`preconditions.erase` を `True` にした
   シナリオのコピーを作り、元のシナリオの代わりに、そのコピーでリースします。次の2つの場合は、コピーを
   作りません。シナリオが `reinstall: overwrite` を宣言している場合と、この経路で `erase` を強制すると
   そもそも例外になる場合です。

   ```python
   retry_scenario = s
   if (
       attempt > 1
       and self.force_erase_on_retry
       and s.preconditions.reinstall != "overwrite"
       and erase_precondition_supported(actuator, self.eff, self.udid_spec)
   ):
       retry_scenario = s.model_copy(
           update={"preconditions": s.preconditions.model_copy(update={"erase": True})}
       )
   lz = self.lease(self.eff, retry_scenario)
   ```

   `force_erase_on_retry` は、`_ScenarioRunner` のフィールドです。`run_all` / `run_and_report` を通じて
   渡され、既定は `True` なので、既存の呼び出し元はすべて変わりません。このフィールドは、
   `bajutsu run --no-erase` の、`_filter_scenarios` による解決前の値を運びます（`erase is not False`。
   `bajutsu/cli/commands/run.py` の `run()` 内で計算します）。CLI フラグは、その後、シナリオ単位の同じ
   bool に潰れます。この bool では、opt-out と沈黙の区別がつかないことを、下の段落で説明します。

   `preconditions.erase is not False` というガード（下記）を外しても、opt-out の合図がすべて失われる
   わけではありません。失われるのは、*解決後のシナリオ側フィールド*が運べた合図だけです。*解決前の CLI
   フラグ*は、今も合図を運べますし、実際に運んでいます。

   リース自体が失敗することもあります。`erase=True` は、`XcuitestEnvironment._prepare_simulator` を
   `e.shutdown()` → `e.erase()` → `e.boot()` という経路に進めます。そのいずれかが非ゼロ終了すると、
   つまりデバイスがこの手順そのものを拒否した場合に、`simctl.DeviceError` として現れます。この例外は
   `BackendCrashError` のサブクラスではなく、兄弟にあたる型です。放置すると、このループ自身の
   `except BackendCrashError` をすり抜け、`run_all` 全体を中断させてしまいます。それまでにパスした
   すべてのシナリオの判定結果まで、失うことになります。

   そのため、`run_one` は、強制 erase を伴うリースの周りに限って、`device_errors.DeviceError` を捕まえ
   ます。erase を強制しない素のリースは、従来どおり、その `DeviceError` を伝播させます。捕まえた場合は、
   `s` をそのままリースする、従来どおりの素の respawn に切り替えます。この試行で起きた不具合は、この
   試行だけの問題にとどまり、run 全体の問題にはなりません。

   `simctl` 自身のサブプロセス呼び出しには、`timeout=` が付いていません（`simctl._real_run`）。完全に
   固まった CoreSimulator デーモンに対する `shutdown`、`erase`、`boot` は、例外を送出せず、ハングし続け
   ることがあります。これは後続の作業として残します。この特性は、この強制再試行に固有のものではなく、
   `erase: true` という precondition すべてに元から備わっているものです。これを止めるには、意図して選ん
   だタイムアウト値と失敗時の振る舞いが必要で、それは `simctl.py` の他の呼び出しとも共有します。この再試行
   ループだけの局所的な修正では、それを与えられません。

   `Scenario` と `Preconditions`（`bajutsu/scenario/models/scenario.py`）は、どちらも pydantic のモデル
   です。`model_copy(update=...)` は、フィールドを上書きするだけで、元のシナリオや `RunResult.scenario`
   の名前をミューテーションしません。両バックエンドとも、`erase=True` に対してすでに完全な消去手順を走ら
   せているため、新しい simctl/adb の仕組みは不要です。

   - `bajutsu/platform_lifecycle/environments/xcuitest.py` は、シミュレータをシャットダウンし、消去し、
     起動し直し、アプリを再インストールします。
   - `bajutsu/platform_lifecycle/environments/android.py` は、アプリをアンインストールして再インストール
     し、そのデータを消去します。

   XCUITest では、`erase` 経路は、すでにコールド起動の完全な readiness の上限を維持しています。その場
   での素の respawn が得る上限は、もっと短いものです。`erase` から戻ってきたデバイスは本物の初回起動
   状態にあるので、この点はコード変更なしでそのまま成り立ちます。

   `reinstall != "overwrite"` という条件を設けたのは、`reinstall: overwrite` が、リースをまたいでアプリ
   のデータコンテナを保持したいという、シナリオ自身の明示的な宣言だからです。`reinstall` の既定値は
   `clean` であり、両バックエンドとも、すでに `pre.erase or pre.reinstall == "clean"` のときに自分の消去
   を実行しています。`erase` を無条件に強制すると、そのシナリオが保持しようとしていた状態そのものを、
   黙って消去してしまいます。そのためこのシナリオの再試行は、今日と同じその場での respawn のままにします。

   `preconditions.erase` には、あえて同じガードを設けません。初期版では、`s.preconditions.erase is not
   False` を確認していました。`erase: false` の明示的な指定も、`reinstall: overwrite` と同じ種類の意図的
   な上書きだ、という理屈です。しかし、実際の CLI 経路を追跡すると、このガードは本番環境でこの機能全体を
   黙って無効化していました。

   `bajutsu/cli/commands/run.py` の `_filter_scenarios` は、`run_all` が受け取る*前*に、どのシナリオの
   `erase` も `None` から具体的な `bool` へ解決します。シナリオ自身が値を設定していなければ、ターゲット
   設定自身の既定値（ターゲットが opt-in しない限り `False`）を使います。この関数の docstring が、この設計
   を述べています（「Leaves every scenario with a concrete bool, so downstream never sees the unset
   `None`」）。したがって、`erase` が `False` の状態で `run_one` に届くシナリオは、誰も `erase` を指定
   しなかった、ありふれたケースです。このガードが想定していた、稀な明示的オプトアウトのケースでは
   ありません。パイプラインに届く時点では、両者は区別できません。なぜ解決前のシグナルをそのまま通す方では
   なく、このガードを削除する方が正しいかは、「検討した代替案」を参照してください。

   `erase_precondition_supported` を設けたのは、2つの XCUITest 経路が、`erase` の precondition をそもそも
   受け付けないからです。

   - 実機（`xcuitest.deviceType: device`）
   - live WebDriver エンドポイント

   どちらも、黙って何もしない代わりに例外を送出します（`simctl.DeviceError` と
   `base.UnsupportedAction`）。これは、権限やインストールの precondition がすでに従っている、「決定性を
   優先し、黙らせずに失敗する」という設計に沿います。どちらの例外も `base.BackendCrashError` ではありま
   せん。これらの経路で `erase` を強制すると、このループ自身の `except BackendCrashError` を素通りして、
   1つのシナリオを再試行する代わりに run 全体が中断します。これは、本項目が置き換えるその場での respawn
   よりも悪い結果です。

   この関数は、`pipeline.py` ではなく `bajutsu/backends.py` の、`capabilities_for_run`（BE-0238）の隣に
   置きます。同じ関数の2つの判定材料（`xcuitest_targets_real_device(eff)`、
   `is_webdriver_endpoint(udid_spec)`）を、そのまま再利用します。すでに経路ごとの capability を分類して
   いる唯一のファイルが、この問いにも答える唯一の場所になります。将来 XCUITest 隣接の経路（デバイス
   ファーム、新しいトランスポート）が追加されたときも、そこでの capability の絞り込みと並べてレビュー
   されます。別のファイルに、`erase` を常に安全だと思い込んだままの箇所が残る心配がありません。

   Android の `pre.erase` は、アプリレベルのクリーンな状態化であり、エミュレータのプロセス自体
   （`adb emu kill` と再起動）の再起動ではありません。詳しくは「検討した代替案」を参照してください。

2. **run 全体を通して蓄積した「実際に復旧に使った時間」に上限を設ける。**
   `bajutsu/runner/recovery.py` の既存の `CrashRecoveryBudget` と並べて、小さなプリミティブを追加します。

   ```python
   class RunCrashRecoveryBudget:
       def __init__(self, budget: float | None) -> None:
           self.budget = budget if budget is None or budget > 0 else None
           self._spent = 0.0
           self._lock = threading.Lock()

       def exhausted(self) -> bool:
           """累積した復旧時間がすでに予算に達しているかどうか。"""
           with self._lock:
               return self.budget is not None and self._spent >= self.budget

       def add_recovery_time(self, seconds: float) -> None:
           """`seconds` 秒の実際の復旧時間を、run 単位の累積値に加算する。"""
           with self._lock:
               self._spent += seconds
   ```

   `bajutsu/runner/pipeline.py` の `run_one` は、自分自身のクラッシュ再試行ループを、ローカル変数
   （`recovery_started`。シナリオの最初のクラッシュ時に1度だけ設定）で計測します。ループ全体を囲む
   `finally` で、`add_recovery_time` を1度だけ呼び出します。この呼び出しは、このシナリオの再試行が実際に
   復旧へ費やした秒数だけを計上します。

   この設計は、意図的に、早いクラッシュから経過した wall-clock ではなく、蓄積した実際の復旧時間を課金
   します。設計の初期版では、run 中で最初のクラッシュのときに単一の共有デッドラインを設定し、以後一切
   再設定しませんでした。そのため、無関係な2つの単発クラッシュのあいだにある、まったく健全で長い期間が、
   同じ予算を黙って消費しました。run の終盤でバックエンドが1回だけクラッシュしたシナリオが、最初の再試行
   すら拒否されかねませんでした。これは、まさに `crash_retries` が乗り越えるために存在する「単発の残存
   クラッシュ」です。累積した合計を課金する設計なら、600秒は「実際に復旧に使った600秒」を意味します。

   計時状態（`recovery_started`）は、`RunCrashRecoveryBudget` のフィールドではなく、各 `run_one` 呼び出し
   のローカルに留めます。`run_all` の `workers > 1` の経路は、複数のシナリオのクラッシュ再試行ループを
   同時に走らせることがあります（`bajutsu/runner/pool.py` の `lease_defect_lock` が存在するのと同じ理由）。
   単一の共有された「復旧開始」タイムスタンプでは、同時に走る2つの復旧が、互いの計時を壊します。先に終わっ
   た方が、もう一方の復旧が使っている「その」計測区間を、勝手に終了させてしまうからです。

   各 `run_one` 呼び出しが共有オブジェクトに触れる方法は、2つだけです。スレッドセーフな読み取り
   （`exhausted`）と、自分自身のループが終わったときの、単一のアトミックな加算（`add_recovery_time`）
   です。並行度がどれだけ高くても、集計は正しく保たれます。

   `budget` は `_budget` ではなく、公開フィールドです。予算を強制するその1つのオブジェクトが、失敗メッセ
   ージ用に設定済みの秒数を読む唯一の場所にもなり、手で同期を保つ2つ目のフィールドを持たずに済みます。
   `_default_crash_recovery_budget` と並べて、新しい `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET` を読む、環境変数
   駆動のデフォルトも追加します。

   `bajutsu/runner/pipeline.py` に配線します。

   - `run_all` に、`run_crash_recovery_budget: float | None = None` を追加します。`crash_recovery_budget`
     と同じ「`None` なら環境変数を読む」方式で解決します。
   - `run_all` は、この予算を、run 内の全シナリオで共有する1つの `RunCrashRecoveryBudget` として、
     `_ScenarioRunner` に渡します。
   - `run_one` の `except BackendCrashError` 節では、`exhausted()` の読み取り結果が、次にリースするかどう
     かを決めます。判断には、シナリオ単位の予算自身の `on_crash(attempt).will_retry` も使います。
   - 失敗メッセージが run 単位の予算を原因として名指しするのは、その予算が実際に決め手になった場合
     （`run_exhausted and decision.will_retry`）に限ります。たまたま run 単位の予算も使い切っていただけで、
     実際にはリトライ回数やシナリオ単位の予算が復旧を止めた原因だった場合は、その原因をそのまま報告し
     ます。そうしないと、自分のリトライ回数を使い切っただけのシナリオが、一度も到達していない予算のせい
     だと、誤解を招く形で報告してしまいます。

   `.github/workflows/ios-e2e.yml` のワークフローレベルの `env` に、`BAJUTSU_RUN_CRASH_RECOVERY_BUDGET`
   を追加します。`run`/`actuation` ジョブの `timeout-minutes` に対して、十分に余裕のあるサイズにします。
   2026-08-04 の注意点を記録しているコメントは、今はこの上限で抑えられている、という記述に書き換えます。

   `android-e2e.yml` にも、`BAJUTSU_CRASH_RETRIES` と `BAJUTSU_CRASH_RECOVERY_BUDGET` に並べて、同じ knob
   を追加します。このワークフローは、今のところ3つとも設定しておらず、リトライ1回・予算無制限にフォール
   バックしています。iOS の値をそのまま流用せず、そのワークフロー自身の `timeout-minutes` に対してサイズ
   を決めます。

   文書化された挙動の変更は両方の言語ミラーを同じ変更で更新する、という本リポジトリの規則に従い、次の
   文書を更新します。

   - `docs/architecture.md` と `docs/ja/architecture.md` の、「run パイプラインでのバックエンドクラッ
     シュ復旧」の箇条書き
   - `docs/run-loop.md` と `docs/ja/run-loop.md` の、より詳しい説明

   更新では、強制 `erase` による再試行と、run 単位の予算の両方を記述します。`run-loop.md` は、本項目が
   覆す既存の「消去しない」という安全性の記述を持つページです。`architecture.md` 側の短い要約だけでなく、
   こちらも直す必要があります。

## 検討した代替案

- **シナリオが `reinstall: overwrite` を宣言している場合でも、`erase=True` を無条件に強制する。**
  却下しました。`overwrite` は、アップグレードや「既存データのまま再開する」シナリオのように、リース
  をまたいでアプリのデータコンテナを保持するためにこそ存在します。両バックエンドとも、`reinstall` が
  `overwrite` で `erase` が未設定のときは、自分の消去をすでにスキップしています。それでもクラッシュ再
  試行がこれを上書きすると、そのシナリオのアサーションが前提としていたのとは異なる precondition を、
  黙って差し込むことになります。インフラのクラッシュに対するきれいな再試行の代わりに、無関係なアサー
  ション失敗（あるいは偽の成功）を生みます。現時点で、リポジトリ内に `reinstall: overwrite` を設定して
  いるシナリオはありません。このガードの追加コストは比較1回分だけで、いつかそのようなシナリオが現れた日
  のために、再試行を正直なものに保ちます。
- **明示的な `erase: false` では強制再試行をスキップし、CLI 解決前のシナリオの `erase` 値をパイプライン
  まで通す（ガードを削除する代わりに）。** 最初の実装は、これと逆でした。*解決後*の値を見るガード
  （`s.preconditions.erase is not False`）を積み、そのガードが、実際の `bajutsu run` の CLI 経路で、この
  単位全体を無効化していました。`_filter_scenarios`（`bajutsu/cli/commands/run.py`）は、`run_all` が
  受け取る前に、未設定のシナリオの `erase` を、ターゲット自身の既定値（ターゲット設定が opt-in しない限り
  `False`）へ、必ず解決します。「シナリオが明示的に `erase: false` と書いた」場合と「何も言わなかった」
  場合は、パイプラインが見る時点では、同じ値になります。

  解決前のシグナルを通せば、区別を取り戻せます。方法は2つです。`Scenario` に別フィールドを持たせるか、
  CLI の解決自体を遅らせます。どちらも、CLI と `Preconditions` に手を入れることになります。解決ステップ
  自身が約束している不変条件（「downstream never sees the unset `None`」）に照らせば、すべてのシナリオ
  が解決済みの状態でパイプラインに届くことを前提とする、他のコードにも、手が及びそうです。単なる
  `erase: false` が実際に保護しているものは、それ自体では何もないので、この対応は不釣り合いだとして却下
  しました。`reinstall` の既定値（`clean`）は、`erase` の値にかかわらず、アプリのデータを消去します。
  上書きから守る必要がある precondition は `reinstall: overwrite` だけで、そちらにはすでに独自のガード
  があります。`erase: false` のガードを削除しても、今日どのシナリオも当てにしているものは、何も失われ
  ません。
- **クラッシュ起点の再試行の1回目からではなく、素の respawn を数回試してから、完全なデバイス復旧に格上げ
  する。** 却下しました。上記のインシデントは、素の respawn が、その場で描画劣化したデバイスをすでに解消
  できていないことを示しています。格上げの前に何度も素の respawn を待つと、うまくいかないとすでにわかっ
  ている対処に、本項目が節約しようとしているまさにその wall-clock を費やすことになります。1回目の再試行
  から `erase` を強制するコストは、消去と再インストールの1サイクル分です。すでに `erase: true` を宣言して
  いるどのシナリオも、最初の試行で同じコストを支払っています。
- **「どの XCUITest 経路が `erase` を拒否するか」という判定を、`bajutsu/backends.py` の
  `capabilities_for_run` の隣に置かず、`bajutsu/runner/pipeline.py` の中に、独自の private ヘルパーとして
  留める。** 初期のドラフトがこれを行っていたため、後から却下しました。`capabilities_for_run` は、すでに
  同じ2つの判定材料（`xcuitest_targets_real_device`、`is_webdriver_endpoint`）で経路の capability を分類
  しています。`pipeline.py` 側の独自コピーは、同じ規則が2つのファイルに存在することになります。将来、
  `backends.py` 側の絞り込みにだけ経路が追加されると、`pipeline.py` 側のコピーは黙って「安全」と答え続け
  ます。本項目自身の再試行安全性ガードが防ごうとしている、run 全体中断という失敗モードが、今日の2経路で
  はなく*新しい*経路に対して再発します。`erase_precondition_supported` は代わりに `backends.py` に置き、
  経路についてのどちらの問いも、すでに経路の分類を持つ唯一のファイルでレビューされるようにしました。
- **run 単位のデッドラインを、run 中の最初のクラッシュ1回だけ設定して共有する（累積した実際の復旧時間を
  課金する代わりに）。** Unit 2 の最初の実装が、これを行っていました。`note_crash()` が、run 中の最初の
  クラッシュで `_deadline = now() + budget` を設定し、以後のすべてのクラッシュの時刻を、それと比較して
  いました。複数シナリオの run で追跡した結果、却下しました。このデッドラインが測っているのは、実際に
  「復旧に使った」時間ではなく、wall-clock の経過です。無関係な2つの単発クラッシュのあいだにある、まった
  く健全で長い期間が、同じ予算を黙って消費しました。run の終盤でバックエンドが1回だけクラッシュしたシナ
  リオが、最初の再試行すら拒否されかねません。これは、まさに `crash_retries` が乗り越えるために存在する
  「単発の残存クラッシュ」です（`ios-e2e.yml` の `BAJUTSU_CRASH_RETRIES` へのコメントを参照）。累積した
  復旧時間を課金する（`add_recovery_time`、各シナリオでローカルに計測）ことで、予算は、実際の復旧活動に
  応じてのみ減ります。
- **Android にも、アプリレベルの `erase` 経路を再利用する代わりに、本物のエミュレータ・プロセス再起動
  （`adb emu kill` と再起動）を与える。** 本項目のスコープからは外します。`pre.erase`（アンインストール/
  インストールに加えて `pm clear`）は、adb バックエンド上でシナリオがすでに要求できる格上げ手段であり、
  これを再利用するのに、新しい adb の仕組みは不要です。エミュレータ・プロセスレベルの再起動が、追加の
  仕組みに見合うかどうかは、別の問題です。アプリレベルの経路が不十分だとわかった場合に、
  `bajutsu/platform_lifecycle/environments/android.py` と `bajutsu/adb.py` 側で、後から取り上げます。
- **同じ変更で、run 単位の予算をオンデバイスのドライバ適合性スイートにも拡張する。** 後続の作業に残し
  ます。適合性スイート（`tests/test_driver_conformance_ondevice.py`、BE-0334）は、`Preconditions` を介さ
  ずモジュールスコープのデバイスを直接リースするため、強制 `erase` は同じようには持ち越せません。また、
  このスイート自身のジョブにも、すでに独自の `timeout-minutes` があります。
- **これを「失敗したテストの自動リトライ」と読む。** ロードマップの「採用しない」一覧は、その考え方を
  決定性優先と緊張関係にあるとして、すでに却下しています。本項目はそれには当たりません。本項目が拡張する
  のは、`BackendCrashError` がすでにゲートしているクラッシュ起点の復旧です
  （[BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery-ja.md)、
  [BE-0342](../BE-0342-ondevice-lease-teardown/BE-0342-ondevice-lease-teardown-ja.md)、
  [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md)）。パイプラインは、
  このインフラの障害を、契約違反とはすでに切り分けています。シナリオ自身のアサーションが失敗するケース
  を再試行することは、ありません。
  [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md) の
  「フレーキネスを吸収によって許容しない」という立場は、変わりません。バックエンドがどの試行でもクラッ
  シュし続けるシナリオや、run 単位の予算を使い切ったシナリオは、それでも大きく失敗します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] Unit 1 — クラッシュ起点の再試行の2回目以降の試行で、シナリオが `reinstall: overwrite` を
      宣言している場合、または経路自体が `erase` をそもそも拒否する場合
      （`bajutsu/backends.py` の `erase_precondition_supported`）を除き、XCUITest・adb 両
      バックエンドについて `preconditions.erase=True` を強制する。`erase is False` だけでは
      スキップしない（「検討した代替案」を参照）。明示的な `bajutsu run --no-erase` は今も尊重
      される。`force_erase_on_retry` がこのフラグの解決前の値を `_filter_scenarios` の先まで
      運ぶためである（「詳細設計」を参照）。強制 erase のリース自体が `device_errors.DeviceError`
      を送出した場合は、run 全体を中断させるのではなく素の respawn に切り替える（「詳細設計」を
      参照）。`simctl` のサブプロセス呼び出しに `timeout=` がない点は今回の後続作業として残した。
      この再試行に固有の問題ではないためである。`--no-erase` を尊重するようにした変更には、実際に
      稼働中の `actuation (xcuitest)` run で捕捉された CI 側の実害があった。`golden`／
      `actuation`／`run`／`bundled-runner`（xcuitest）が共有するコンポジットアクション
      `.github/actions/bajutsu-e2e/action.yml` は、すべての `bajutsu run` 呼び出しに `--no-erase`
      をハードコードしていた。通常のシナリオのリースに対しては no-op である（どちらの showcase
      ターゲットもターゲット単位の `erase` 既定値を設定していないため）が、この単位がこのフラグを
      尊重するようになった結果、2026-08-06 のインシデントで名指しされたまさにそのジョブ群で、
      強制 erase による復旧が黙って無効化されていた。CLI 側の保証を緩めるのではなく、コンポジット
      アクション側の冗長なフラグを取り除いた。
- [x] Unit 2 — `RunCrashRecoveryBudget`（デッドライン方式ではなく、累積した実際の復旧時間を
      課金する方式）を追加し、`run_crash_recovery_budget` / `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET`
      を `run_all` に配線し、ワークフローの env knob を追加し、`docs/architecture.md` /
      `docs/run-loop.md` および両方の `docs/ja/` ミラーを更新する。

## 参考

- [BE-0344 — XCUITest のコールド起動の再試行のあいだにシミュレータを修復する](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md) — 本項目がクラッシュ起点の再試行経路にまで広げる、デバイス復旧のラダー。
- [BE-0334 — 実機 conformance スイートに run パイプラインと同じインフラ障害からの復旧を持たせる](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery-ja.md) — 本項目が run 単位の対になるものを追加する `CrashRecoveryBudget`。
- [BE-0342 — 実機スイートの lease に runner まで届く teardown を持たせる](../BE-0342-ondevice-lease-teardown/BE-0342-ondevice-lease-teardown-ja.md) — クラッシュ起点の再試行経路が使う、共有の teardown の記録。
- [BE-0049 — 決定性／フレーキネス監査](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md) — 本項目の上限付き復旧が保つ、フレーキネスを吸収によって許容しないという立場。
- `bajutsu/runner/pipeline.py`、`bajutsu/runner/recovery.py`、`bajutsu/runner/pool.py` — クラッシュ起点の再試行ループと、新しい予算プリミティブ。
- `bajutsu/backends.py` — `capabilities_for_run` と、本項目がその隣に追加する `erase_precondition_supported`。
- `bajutsu/platform_lifecycle/environments/xcuitest.py`、`bajutsu/platform_lifecycle/environments/android.py` — 両バックエンドがすでに走らせている消去経路。
- `.github/workflows/ios-e2e.yml`、`.github/workflows/android-e2e.yml` — 本項目が追加するワークフローの env knob と、前者にすでに記録されているインシデント。
- `docs/run-loop.md`、`docs/architecture.md`（および両方の `docs/ja/` ミラー）— 本項目が更新する、文書化された挙動。

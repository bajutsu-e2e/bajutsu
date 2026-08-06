[English](BE-XXXX-xcuitest-adb-crash-retry-device-recovery.md) · **日本語**

# BE-XXXX — バックエンドクラッシュの再試行でデバイス復旧を強制し、run 全体のクラッシュ復旧時間に上限を設ける

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-xcuitest-adb-crash-retry-device-recovery-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | [#1526](https://github.com/bajutsu-e2e/bajutsu/pull/1526) |
| トピック | Platform support |
| 関連 | [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md), [BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery-ja.md), [BE-0342](../BE-0342-ondevice-lease-teardown/BE-0342-ondevice-lease-teardown-ja.md), [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Bajutsu の run パイプラインは、シナリオ実行中のバックエンドクラッシュからすでに復旧します。死んだ
リースを破棄し、新しいデバイスをリースし直して、シナリオ全体をやり直します。この復旧はリトライ
回数（`crash_retries`）と wall-clock の予算（`crash_recovery_budget`）で上限を設けており、この予算
はオンデバイスのドライバ適合性スイートとも共有されています
（[BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery-ja.md)、
[BE-0342](../BE-0342-ondevice-lease-teardown/BE-0342-ondevice-lease-teardown-ja.md)）。ただしこの復旧が
差し替えるのはクラッシュした runner プロセスだけで、そのプロセスが動いていたデバイス自体には一切
手を触れません。クラッシュの本当の原因がデバイス側にある場合（たとえば iOS シミュレータや Android
エミュレータの描画が応答しなくなった場合）、新しい runner プロセスは、まさにクラッシュを引き起こした
そのデバイスの上で起動し、シナリオは同じ形で再び失敗します。

本項目は、このギャップを2つの部分で埋めます。1つ目は、クラッシュを起点とする再試行に、シナリオが
`preconditions.erase: true` を宣言したときにすでに得られるのと同じ「デバイスを消去して再インストール
する」手順を、その場での素の respawn の代わりに強制することです。XCUITest バックエンドでは、この
手順はシミュレータのプロセス自体を再起動します（`simctl shutdown → erase → boot` に加えて
再インストール）。adb バックエンドでは、この手順はエミュレータのプロセス自体は再起動しない
アプリレベルのクリーンな状態化です（`uninstall`/`install` に加えて `pm clear`）。つまり adb 側で
回復できる Android の障害の範囲は、iOS 側の対処より狭くなります。詳しくは「検討した代替案」を
参照してください。2つ目は、新しい run 単位の wall-clock 予算（`run_crash_recovery_budget`）を追加
し、クラッシュ復旧に使ってよい時間を1つのシナリオ内だけでなく run 全体を通して制限することです。
これにより、劣化し続けるデバイスは、各シナリオが自分の予算を静かに使い切ってジョブ自身の継続的
インテグレーション（CI）タイムアウトに診断不能な形で打ち切られる代わりに、run 自体が大きく・素早く
失敗するようになります。

## 動機

2026-08-06、ある通常の pull request の `actuation (xcuitest)` ジョブは 27 分間実行され、その後
自身の `timeout-minutes` によって**キャンセル**されました。失敗ではなく、読み手が手がかりにできる
原因を何も残さない状態です。runner のログは何が起きたかを伝えています。どのシナリオもまだ実行
されていない時点で、既存のコールド起動復旧のラダー
（[BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md)）が、すでに
独自の判断でシミュレータを1度再起動していました。

```
rebooted Simulator 2A6DC5A9-CE8C-4BC5-959D-F98D5F4BD9AA after a failed cold runner spawn
```

この再起動は、既存のラダーが発火すれば機能する証拠です。しかしシナリオが実行中で runner が
`/health` に正常に応答している状態では、run の途中でデバイスが劣化しても、それに相当するトリガー
はありません。その2シナリオ後、シミュレータの画面キャプチャがフレームを生成しなくなりました。

```
recordVideo produced no new bytes in runs/.../02-foreach-opens-every-real-catalog-row/scenario.mp4 within 5.0s
```

そして以降の screenshot 取得はすべて、タイムアウトしては復旧し、また再びタイムアウトするように
なりました。

```
runner channel GET /screenshot failed (attempt 1/3), retrying: timed out
runner channel GET /screenshot failed (attempt 2/3), retrying: timed out
runner channel GET /screenshot: the runner became unreachable past the retry budget — a mid-run crash: ...
runner channel GET /screenshot: the runner recovered from a mid-run crash; re-issuing the idempotent call (recovery 1/3)
```

このパターンはさらに3つのシナリオにわたって繰り返され、最終的に `xcodebuild` 自身が終了し
（コード 65）、パイプライン自身の `BackendCrashError` 復旧（`bajutsu/runner/pipeline.py`）が runner
を respawn してシナリオを最初からやり直しました。この respawn は同じ環境インスタンスを保持した
まま、`bajutsu/runner/pool.py` 自身のコメントの言葉を借りれば「その場でコールド respawn する」
動作をします。つまり同じシミュレータの上で新しい runner プロセスを起動します。新しいプロセスは
`/health` に正常に応答したため、デバイス復旧のラダーは二度目には走りませんでした。このラダーは、
起動の試行自体が ready にならなかったときにしか発火せず、描画がすでに固まったシミュレータを、
一見健全に見えるプロセスへそのまま引き渡すケースには発火しないからです。同じパターンは次の
シナリオでも、その次のシナリオでも再現し、原因をどのシナリオも報告しないまま、ジョブの
`timeout-minutes` によって終了しました。

`.github/workflows/ios-e2e.yml` には、2026-08-04 に起きた同種のインシデントが、
`BAJUTSU_CRASH_RECOVERY_BUDGET` の上のコメントにすでに記録されています。シナリオ側の予算も
同時に消費されるジョブでは、それがコールド起動復旧の上限に上乗せされ、2026-08-04 のある実行は
この経路で `timeout-minutes` に到達した、と記されています。コメントはこの注意点を記録している
だけで、コードは何も解消していません。`crash_recovery_budget` はシナリオが変わるたびにリセット
され、各シナリオの最初のクラッシュ起点の再試行は、その場での respawn 以上のものを一度も得られない
からです。

## 詳細設計

互いに独立した2つの単位から成ります。

1. **クラッシュ起点の再試行では `preconditions.erase = True` を強制する。ただし強制すると危険な
   場合は除く。**
   `bajutsu/runner/pipeline.py` の `_ScenarioRunner.run_one` の再試行ループは、1回目より後の
   試行のたびに新しいデバイスをリースします。2回目以降の試行では、`preconditions.erase` を
   `True` にしたシナリオのコピーを作り、元のシナリオの代わりにそのコピーでリースします。ただし
   シナリオが `reinstall: overwrite` を宣言している場合、およびこの経路で `erase` を強制すると
   そもそも例外になる場合は除きます。

   ```python
   retry_scenario = s
   if (
       attempt > 1
       and s.preconditions.reinstall != "overwrite"
       and s.preconditions.erase is not False
       and _erase_is_safe_to_force(actuator, self.eff, self.udid_spec)
   ):
       retry_scenario = s.model_copy(
           update={"preconditions": s.preconditions.model_copy(update={"erase": True})}
       )
   lz = self.lease(self.eff, retry_scenario)
   ```

   `Scenario` と `Preconditions`（`bajutsu/scenario/models/scenario.py`）はどちらも pydantic の
   モデルなので、`model_copy(update=...)` はフィールドを上書きするだけで、元のシナリオや
   `RunResult.scenario` の名前をミューテーションしません。両バックエンドとも `erase=True` に
   対してすでに完全な消去手順を走らせているため、新しい simctl/adb の仕組みは不要です。
   `bajutsu/platform_lifecycle/environments/xcuitest.py` はシミュレータをシャットダウンし、
   消去し、起動し直し、アプリを再インストールします。
   `bajutsu/platform_lifecycle/environments/android.py` はアプリをアンインストールして
   再インストールし、そのデータを消去します。XCUITest では、`erase` 経路はすでに、その場での
   respawn が得るような短い readiness の上限ではなく、コールド起動の完全な上限を維持しています。
   `erase` から戻ってきたデバイスは、本物の初回起動状態にあるからです。この点はコード変更なしで
   そのまま成り立ちます。

   `reinstall != "overwrite"` という条件を設けたのは、`reinstall: overwrite` が、リースを
   またいでアプリのデータコンテナを保持したいという、シナリオ自身の明示的な宣言だからです
   （`reinstall` の既定値は `clean` であり、両バックエンドともすでに `pre.erase` が真か
   `pre.reinstall` が `clean` のいずれかのときに自分の消去を実行しています）。`erase` を
   無条件に強制すれば、そのシナリオが保持しようとしていた状態そのものを黙って消去してしまいます。
   そのためこのシナリオの再試行は、今日と同じその場での respawn のままにします。
   `erase is not False` という条件は、同じ種類の判断を1段上で扱うものです。
   `Preconditions.erase` は `bool | None` であり（`None` はターゲット自身の `erase` の既定値を
   継承し、明示的な `true`/`false` はこのシナリオに固定します）、`erase: false` を明示的に固定した
   シナリオも、`reinstall: overwrite` と同じ種類の意図的な上書きを行っているため、強制された
   再試行がそれを覆してはいけません。

   `_erase_is_safe_to_force` を設けたのは、2つの XCUITest 経路が `erase` の precondition を
   そもそも受け付けず、黙って no-op にする代わりに例外を送出するからです。実機
   （`xcuitest.deviceType: device`）と live WebDriver エンドポイントはどちらも、権限やインストール
   の precondition がすでに従っている「決定性を優先し、黙らせずに失敗する」という設計に沿って
   （`simctl.DeviceError` / `base.UnsupportedAction` を）送出します。どちらの例外も
   `base.BackendCrashError` ではないため、これらの経路で `erase` を強制すると、このループ自身の
   `except BackendCrashError` を素通りして run 全体が中断してしまいます。これは本項目が置き換える
   その場での respawn よりも悪い結果です。このガードは、preflight がすでに使っている（BE-0238）
   のと全く同じ判定材料（`xcuitest_targets_real_device(eff)`、`is_webdriver_endpoint(udid_spec)`）
   を再利用するため、シナリオがどちらの経路にいるかについて2つの判定が食い違うことはありません。

   Android の `pre.erase` は、アプリレベルのクリーンな状態化であり、エミュレータのプロセス自体
   （`adb emu kill` と再起動）の再起動ではありません。詳しくは「検討した代替案」を参照してください。

2. **run 全体を通したクラッシュ復旧の累積時間に上限を設ける。** `bajutsu/runner/recovery.py` の
   既存の `CrashRecoveryBudget` と並べて、小さなプリミティブを追加します。

   ```python
   class RunCrashRecoveryBudget:
       def __init__(self, budget: float | None, now: Callable[[], float]) -> None:
           self.budget = budget
           self._now = now
           self._deadline: float | None = None
           self._lock = threading.Lock()

       def note_crash(self) -> bool:
           """クラッシュを記録し、run 単位の予算を使い切ったかどうかを返す。"""
           with self._lock:
               t = self._now()
               if self.budget is not None and self._deadline is None:
                   self._deadline = t + self.budget
                   return False
               return self._deadline is not None and t >= self._deadline
   ```

   デッドラインは run 中で最初のクラッシュのときに設定され、それ以降のすべてのシナリオで共有
   されます。`note_crash` はクロックをちょうど1回だけ読みます。そのためデッドラインを設定する
   まさにそのクラッシュは、デッドラインを設定したのと同じ瞬間で判定され、それ自身が「予算を
   使い切った」と報告されることは決してありません。これは `CrashRecoveryBudget.on_crash` がすでに
   自分自身の1回のクロック読み取りで与えている、最初の respawn を絶対にブロックしないという規則と
   同じです（この2つの操作を別メソッドではなく1つのメソッドにしているのはこのためです。設計の
   初期版では `note_crash()` と、それぞれ独自にクロックを読む別の `exhausted()` に分けていました。
   今日利用できるどのクロックの上でも正しく動きますが、原理的には2回の呼び出しの間に実時間が
   進む余地があり、デッドラインを設定したまさにそのクラッシュが、すでに使い切っていると読める
   可能性が残っていました）。`budget` は `_budget` ではなく公開フィールドです。予算を強制する
   その1つのオブジェクトが、失敗メッセージ用に設定済みの秒数を読む唯一の場所にもなるようにし、
   手で同期を保つ2つ目のフィールドを持たないようにするためです。`note_crash` を
   `threading.Lock` で保護しているのは、`run_all` の `workers > 1` の経路が1つの
   `_ScenarioRunner` をスレッドプール全体で共有するためで、`bajutsu/runner/pool.py` の
   `lease_defect_lock` が存在する理由と同じです。`_default_crash_recovery_budget` と並べて、
   新しい `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET` を読む環境変数駆動のデフォルトも追加します。

   `bajutsu/runner/pipeline.py` にも配線します。`run_all` に
   `run_crash_recovery_budget: float | None = None` を追加し、`crash_recovery_budget` と同じ
   「`None` なら環境変数を読む」方式で解決したうえで、`_ScenarioRunner` に、run 内の全シナリオ
   で共有する1つの `RunCrashRecoveryBudget` として渡します。`run_one` の
   `except BackendCrashError` 節では、`note_crash()` の戻り値が、シナリオ単位の予算自身の
   `on_crash(attempt).will_retry` と並んで、次にリースするかどうかを決めます。run 単位の
   予算を使い切っていれば、再試行を止め、シナリオ単位の予算やリトライ回数の枯渇とは区別できる
   形で、run 単位の予算を名指しした失敗を報告します。

   `.github/workflows/ios-e2e.yml` のワークフローレベルの `env` に
   `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET` を追加します。`run`/`actuation` ジョブの
   `timeout-minutes` に対して十分に余裕のあるサイズにし、2026-08-04 の注意点を記録している
   コメントを、今はこの上限で抑えられている、という記述に書き換えます。`android-e2e.yml` にも、
   `BAJUTSU_CRASH_RETRIES` と `BAJUTSU_CRASH_RECOVERY_BUDGET` に並べて同じ knob を追加します。
   このワークフローは今のところ3つとも設定しておらず、リトライ1回・予算無制限にフォール
   バックしています。iOS の値をそのまま流用せず、そのワークフロー自身の `timeout-minutes` に
   対してサイズを決めます。

   `docs/architecture.md` と `docs/ja/architecture.md` の「run パイプラインでのバックエンド
   クラッシュ復旧」の箇条書きを更新し、強制 `erase` による再試行と run 単位の予算の両方を
   記述します。文書化された挙動の変更は両方の言語ミラーを同じ変更で更新するという、本
   リポジトリの規則に従います。

## 検討した代替案

- **シナリオが `reinstall: overwrite` を宣言している場合でも、`erase=True` を無条件に強制する。**
  却下しました。`overwrite` は、アップグレードや「既存データのまま再開する」シナリオのように、
  リースをまたいでアプリのデータコンテナを保持するためにこそ存在し、両バックエンドとも
  `reinstall` が `overwrite` で `erase` が未設定のときは自分の消去をすでにスキップしています。
  それでもクラッシュ再試行がこれを上書きすれば、そのシナリオのアサーションが前提としていたのとは
  異なる precondition を黙って差し込むことになり、インフラのクラッシュに対するきれいな再試行の
  代わりに、無関係なアサーション失敗（あるいは偽の成功）を生みます。現時点でリポジトリ内に
  `reinstall: overwrite` を設定しているシナリオはありませんが、このガードの追加コストは比較
  1回分だけであり、いつかそのようなシナリオが現れた日のために、再試行を正直なものに保ちます。
- **クラッシュ起点の再試行の1回目からではなく、素の respawn を数回試してから完全なデバイス
  復旧に格上げする。** 却下しました。上記のインシデントは、素の respawn がその場で描画劣化した
  デバイスをすでに解消できていないことを示しており、格上げの前に何度も素の respawn を待つのは、
  うまくいかないとすでにわかっている対処に、本項目が節約しようとしているまさにその
  wall-clock を費やすことになります。1回目の再試行から `erase` を強制するコストは、すでに
  `erase: true` を宣言しているどのシナリオも、その最初の試行で支払っているコストと同じです。
- **Android にも、アプリレベルの `erase` 経路を再利用するのではなく、本物のエミュレータ・
  プロセス再起動（`adb emu kill` と再起動）を与える。** 本項目のスコープからは外します。
  `pre.erase`（アンインストール/インストールに加えて `pm clear`）は、adb バックエンド上で
  シナリオがすでに要求できる格上げ手段であり、これを再利用するのに新しい adb の仕組みは
  不要です。エミュレータ・プロセスレベルの再起動が、追加の仕組みに見合うかどうかは別の問題
  であり、アプリレベルの経路が不十分だとわかった場合に、
  `bajutsu/platform_lifecycle/environments/android.py` / `bajutsu/adb.py` 側で後から
  取り上げます。
- **同じ変更で、run 単位の予算をオンデバイスのドライバ適合性スイートにも拡張する。** 後続の
  作業に残します。適合性スイート（`tests/test_driver_conformance_ondevice.py`、BE-0334）は、
  `Preconditions` を介さずモジュールスコープのデバイスを直接リースするため、強制 `erase` は
  同じようには持ち越せません。また、このスイート自身のジョブにはすでに独自の
  `timeout-minutes` があります。
- **これを「失敗したテストの自動リトライ」と読む。** ロードマップの「採用しない」一覧はすでに、
  これを決定性優先と緊張関係にあるとして却下しています。しかし本項目はそれには当たりません。
  本項目が拡張するのは、`BackendCrashError` がすでにゲートしているクラッシュ起点の復旧
  （[BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery-ja.md)、
  [BE-0342](../BE-0342-ondevice-lease-teardown/BE-0342-ondevice-lease-teardown-ja.md)、
  [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md)）であり、
  パイプラインがすでに契約違反とは切り分けているインフラの障害です。シナリオ自身のアサー
  ションが失敗するケースを再試行するものではありません。
  [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)
  の「フレーキネスを吸収によって許容しない」という立場は変わりません。バックエンドがどの
  試行でもクラッシュし続けるシナリオや、run 単位の予算を使い切ったシナリオは、それでも
  大きく失敗します。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] Unit 1 — クラッシュ起点の再試行の2回目以降の試行で、シナリオが `reinstall: overwrite` を
      宣言している場合を除き、XCUITest・adb 両バックエンドについて `preconditions.erase=True`
      を強制する。
- [x] Unit 2 — `RunCrashRecoveryBudget` を追加し、`run_crash_recovery_budget` /
      `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET` を `run_all` に配線し、ワークフローの env knob を
      追加し、`docs/architecture.md` / `docs/ja/architecture.md` を更新する。

## 参考

- [BE-0344 — XCUITest のコールド起動の再試行のあいだにシミュレータを修復する](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md) — 本項目がクラッシュ起点の再試行経路にまで広げる、デバイス復旧のラダー。
- [BE-0334 — 実機 conformance スイートに run パイプラインと同じインフラ障害からの復旧を持たせる](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery-ja.md) — 本項目が run 単位の対になるものを追加する `CrashRecoveryBudget`。
- [BE-0342 — 実機スイートの lease に runner まで届く teardown を持たせる](../BE-0342-ondevice-lease-teardown/BE-0342-ondevice-lease-teardown-ja.md) — クラッシュ起点の再試行経路が使う、共有の teardown の記録。
- [BE-0049 — 決定性／フレーキネス監査](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md) — 本項目の上限付き復旧が保つ、フレーキネスを吸収によって許容しないという立場。
- `bajutsu/runner/pipeline.py`、`bajutsu/runner/recovery.py`、`bajutsu/runner/pool.py` — クラッシュ起点の再試行ループと、新しい予算プリミティブ。
- `bajutsu/platform_lifecycle/environments/xcuitest.py`、`bajutsu/platform_lifecycle/environments/android.py` — 両バックエンドがすでに走らせている消去経路。
- `.github/workflows/ios-e2e.yml`、`.github/workflows/android-e2e.yml` — 本項目が追加するワークフローの env knob と、前者にすでに記録されているインシデント。

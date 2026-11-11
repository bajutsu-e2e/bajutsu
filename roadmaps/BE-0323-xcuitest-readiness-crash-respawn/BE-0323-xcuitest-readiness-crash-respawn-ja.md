[English](BE-0323-xcuitest-readiness-crash-respawn.md) · **日本語**

# BE-0323 — readiness ゲート中の runner クラッシュから XCUITest のコールド起動を回復する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0323](BE-0323-xcuitest-readiness-crash-respawn-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0323") |
| 実装 PR | [#1358](https://github.com/bajutsu-e2e/bajutsu/pull/1358) |
| トピック | Platform support |
| 関連 | [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md), [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md), [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience-ja.md) |
<!-- /BE-METADATA -->

## はじめに

run パイプラインは、バックエンドのクラッシュからの復旧を得ました（PR
[#1368](https://github.com/bajutsu-e2e/bajutsu/pull/1368)）。scenario の実行中に起きた
`base.BackendCrashError` は、判定ではなくインフラの問題として扱います。復旧は死んだ lease を破棄し、
新しいデバイスを lease し（コールド再起動）、scenario 全体を `crash_retries` の範囲で再実行します。

しかし、復旧のきっかけとなったクラッシュが run を失敗させる残課題が、まだ 2 つあります。本項目は両方を塞ぎます。

1 つ目の残課題は、**lease そのものの最中に起きるクラッシュ**です。復旧は lease をリトライループの*内側*で呼びながら、
`try` の*外側*に置いています。そのため、どの scenario ステップよりも前の launch と readiness ゲートで起きたクラッシュは、
ループを抜けて run を落とします。

2 つ目の残課題は、**死んだ runner へのポーリング**です。runner の `xcodebuild` **プロセスが終了**していても、
crash-recovery 層は諦めるまで 60 秒の復旧予算をまるごと `GET /health` に費やします。来ない復旧に 1 分を使います。

本項目は、lease をリトライの内側へ移します。これで bring-up 時のクラッシュも、他のクラッシュと同様に回復します。
さらに、runner プロセスが消えているときは復旧を即座に失敗させます。

最初の修正の CI 実行は、その下の層をあらわにしました。混雑した CI ホストでは、常駐 runner が**繰り返し**
クラッシュします。最初の起動も、再起動後のリトライも、どちらも死ぬことがあります。`conformance` ゲートは `bajutsu run` ではなく
pytest スイートなので、パイプライン復旧にそもそも到達しません。

クラッシュを源流までたどると、runner について 2 つの事実が見つかりました。runner の HTTP サーバは接続を並行に捌きます。
また、`app.snapshot()` / `app.screenshot()` は app を待つあいだ main の run loop を回します。そのため、並行する 2 つの
`DispatchQueue.main.sync` 操作（scenario の `/elements` と evidence の `/screenshot`）が再入します。2 つ目の XCUITest
呼び出しが、1 つ目の*内側*で main スレッド上を走るのです。XCUITest はこの再入を禁じており、XCTest ホストが abort します。

そこで本項目は、**runner の XCUITest 操作を直列化**します。この直列化は再入を源流から取り除き、`conformance` を含む
全ジョブに効きます。あわせて、**crash-retry 予算を設定可能**にします。従量課金の on-device レーンが、残る 1 回限りの
クラッシュを乗り切れるようにするためです。

## 動機

必須の `run (xcuitest)` ジョブが、scenario のアサーションではなくセットアップ段階の失敗で flaky になりました。

```
XcuitestRunnerCrashError: runner channel GET /elements failed: the runner crashed mid-run and did
not recover within 60s
```

トレースバックは、この失敗を起動パスに位置づけます。どの scenario 本体も走る前の、`launch_driver` → `_await_ready` →
`driver.query()` → `GET /elements` です。順を追うと次のとおりです。

1. コールド起動（`_spawn_cold_with_retry`）は、runner が `GET /health` に応答するまで待ってドライバを返します。
   runner は確かに起動しています。
2. `launch_driver` が `_await_ready` を呼び、最初の `driver.query()` が `GET /elements` を発行します。
3. ここで runner がクラッシュし、その `xcodebuild` プロセスが終了します。これは
   [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md)
   と [BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md)
   が述べる、アプリ起動と画面出現に伴うクラッシュで、負荷の高い CI ホストでのみ再現します。
4. crash-recovery 層（`_with_crash_recovery`）は、トランスポートの失敗を捕捉します。`GET` は冪等なので、
   runner が戻るのを 60 秒の復旧予算のあいだ `GET /health` でポーリングして待ちます。プロセスはもういないうえ、
   誰も再起動しません。ポーリングは 60 秒まるごと拒否され、最後に `XcuitestRunnerCrashError` が送出されます。
5. このエラーは `_await_ready` と `launch_driver` の外へ抜けます。`launch_driver` は、パイプラインが lease を作るために
   呼ぶ関数です。パイプラインの crash 復旧は lease をリトライループの内側で呼びます。ところが `lease` 呼び出しは
   `BackendCrashError` を捕捉する `try` の*外側*にあるため、lease 時のクラッシュは捕まりません。クラッシュは
   `run_all` の外へ伝播して、run 全体を落とします。

このクラッシュは #1368 の復旧をすでに載せたブランチ上で表面化したので、復旧がこの窓を覆えていないことは実証済みです。
覆えない理由は 2 つあり、互いに独立しています。

- **クラッシュは step 時ではなく lease 時に起きる。** #1368 は `_run_on_lease`（scenario 本体）を crash リトライの
  `try` で包みます。その手前の `self.lease(...)` は `launch_driver` と readiness ゲートを走らせますが、`try` の外にあります。
  lease を*作っている*最中に送出されたクラッシュは、まさにリトライが見ないケースです。
- **復旧が死んだプロセスに窓をまるごと費やす。** 復旧がクラッシュを捕捉できても、プロセスが終了した runner を
  60 秒ポーリングするのは、避けられない失敗の前の空き時間です。
  [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience-ja.md)
  は、*コールド起動*で `xcodebuild` プロセスが死んだときにすでに即座に失敗させます。実行中の crash-recovery
  パスには、そうした liveness チェックがありません。

結果は、[BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)
が取り除こうとした flaky ゲートの代償です。実際に何かが壊れているのかについて、赤い必須チェックは何のシグナルも
持ちません。手動でジョブを再実行するまで解消されません。

## 詳細設計

作業は 5 つのユニットに分かれます。ユニット 1 と 2 は上記の 2 つの隙間を塞ぎます。bring-up のクラッシュを回復し、
次に死んだプロセスに窓を費やすのをやめます。ユニット 3 は、両方を注入可能なシームの上でテストします。ユニット 4 と 5 は、
その下にある再入クラッシュを塞ぎます。ユニット 4 が runner の XCUITest 操作を直列化し（根本原因）、
ユニット 5 がその安全余裕として crash-retry 予算を引き上げます。Simulator は不要です。

1. **lease を crash リトライの `try` の内側に置く。** `_ScenarioRunner.run_one`
   （`bajutsu/runner/pipeline.py`）で、`self.lease(...)` の呼び出しを `try` の直前から `try` の内側へ移します。
   lease を作っている最中（launch と readiness ゲート）に送出された `base.BackendCrashError` を、step がクラッシュした
   scenario をすでに再実行している同じ復旧が捕捉します。リトライは、step 時のクラッシュとまったく同じに、
   新たに lease を取ります。プールが死んだ warm runner を捨てるので、このリトライはコールド再起動です。

   `_run_on_lease` は、引き続き自身の `finally` で lease を release します。そのため、step 中のクラッシュでも
   lease は漏れません。lease 時のクラッシュは、release すべき lease を残しません。プールが、失敗した自分の lease を
   片付けるからです。したがって、この移動で漏れは増えません。`crash_retries` の上限は不変なので、bring-up が毎回
   クラッシュすれば、これまでどおり大きく失敗します
   （[BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)）。

2. **runner プロセスが終了していたら crash-recovery で即座に失敗させる。** crash-recovery 層
   （`bajutsu/drivers/xcuitest.py` の `_with_crash_recovery`）は、runner が戻るという前提のもとで復旧予算を待ちます。
   この前提は、runner の*プロセス*できれいに分かれます。

   プロセスが生きていて、一時的に到達できないだけの場合は、
   [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md)
   の復旧可能なケースです。層は待ち続けなければなりません。

   プロセスが**終了**している場合、`/health` に二度と応答しません。復旧のあいだ、誰もそのポートで再起動しないためです。
   予算を丸ごと待つのは空き時間です。

   環境から `make_driver` 経由で crash-recovery 層へ、runner の生存を返す述語（`runner_alive: () -> bool`）を
   渡します。環境は `xcodebuild` サブプロセスのハンドルを持っています。クラッシュ時に述語がプロセスの消滅を報告したら、
   層は死んだポートをポーリングせず、識別しやすい診断とともに即座に失敗します。ユニット 1 のパイプライン復旧が、
   新しいデバイスを lease して scenario を再実行します。

   述語がない場合（テストのフェイク）や、プロセスの生存を報告する場合の挙動は、BE-0287 と寸分違いません。
   ユニット 2 は、すでに詰んでいる待ちを*短くする*だけで、復旧可能な瞬断を失敗に変えることはありません。

3. **両シームのオフデバイステスト。** ユニット 1 と 2 は、フェイクの注入で Simulator なしに検証できます。
   パイプラインとチャネルのテストがすでに使っている隔離と同じです。

   ユニット 1 のテスト（`tests/runner/test_pipeline.py`）は、bring-up で `BackendCrashError` を送出する lease が、
   リトライの新しい lease で回復することを確認します。毎回クラッシュする lease が、上限の lease 回数ちょうどで
   大きく失敗することも確認します。

   ユニット 2 のテスト（`tests/test_xcuitest.py`）は、liveness 述語がプロセスの**消滅**を報告するクラッシュが、
   復旧予算をポーリングせず即座に失敗することを確認します。**生存**を報告するクラッシュが予算を待ち続け、
   BE-0287 の挙動を保つことも確認します。述語がないデフォルトの挙動が不変であることも確認します。

4. **runner の XCUITest 操作を直列化して再入クラッシュを取り除く（根本原因）。** 常駐 runner の HTTP サーバ
   （`BajutsuRunner`）は、接続を並行に捌きます。意図的な並行で、長いジェスチャ中も `/health` ポーリングに応答できる
   ようにするためです（BE-0287）。

   あらゆる XCUITest 呼び出しは、`DispatchQueue.main.sync` で main スレッドへ marshal されます。ところが
   `app.snapshot()`、`app.screenshot()`、操作は、app を待つあいだ main の run loop を回します。その spin が main の
   dispatch キューを drain するので、並行する 2 つ目の操作のブロックが 1 つ目の*内側*で走り、XCUITest に再入します。
   この再入が XCTest ホストを abort させます。これが CI 固有の実行中クラッシュで、混雑したホストほど窓が広がります。

   connection スレッドで main へ dispatch する*前に*握るロック（`Router.actuationLock`）を追加します。すると、
   1 つ目が実行中のあいだ 2 つ目は main へ enqueue されず、run loop の spin には drain すべき再入対象がありません。
   `/health` はロックを取りません。XCUITest 状態に触れないからです。そのため並行サーバは、「runner は busy であって
   dead ではない」というシグナルを保ちます。

   これは、ユニット 1 と 2 のパイプライン復旧を通らない `conformance` ジョブに届く、唯一の修正です。
   `BajutsuRunner` のテストが担保します。このテストは、main スレッド外から 2 つの読み取りを並行に走らせ、
   決して重ならず再入しないことを確かめます。

5. **crash-retry 予算を設定可能にし、on-device レーンで引き上げる。** パイプラインの `crash_retries` は、
   ハードコードのデフォルト 1（2 試行）でした。予算を `BAJUTSU_CRASH_RETRIES` から読みます（未設定なら 1）。従量課金の
   on-device CI レーンが、コード変更なしで予算を引き上げられるようにするためです。そのレーンでは 2（3 試行）に設定します。

   runner のクラッシュは、*テストホスト*の死であって、アプリの判定ではありません。1 回限りのクラッシュを乗り切るのは、
   flake の吸収ではありません。毎回クラッシュする scenario は、予算を使い切れば依然として大きく失敗します
   （[BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)）。引き上げた予算は
   ユニット 4 の根本修正の*背後*にある安全余裕であって、その修正の代替ではありません。

   テストは 2 点を担保します。環境変数がデフォルトを設定すること（未設定または不正なら 1、`0` で無効化）と、
   `run_all` がそのデフォルトを end to end で尊重することです。

## 検討した代替案

- **パイプラインではなく `launch_driver` の内側でリトライする。** launch ローカルのリトライは、パイプラインが
  知らないところで runner を再起動します。step 時のクラッシュに対してパイプラインがすでに持つ復旧を二重化し、
  「バックエンドがクラッシュした」の周りに 2 本目の並行した境界を引きます。lease をパイプラインの既存の `try` の
  内側へ移せば、bring-up のクラッシュと step のクラッシュの両方に、1 つの復旧を再利用できます。単一のシームを採り、
  却下します。
- **60 秒の復旧予算を全体的に短くする。** 予算を短くすれば、このケースは速く失敗します。しかし、本物の BE-0287 の
  復旧（約 30 秒到達できないが*戻ってくる* runner）も早々に打ち切ってしまい、予算が乗り切るために存在する flaky さを
  ぶり返します。ユニット 2 は、速い経路の鍵を時計の短縮ではなく*プロセス*の消滅に置くので、復旧可能なケースは
  予算をまるごと保ちます。却下します。
- **上限のないリトライ。** 上限なくリトライすると、本当に繰り返すクラッシュを吸収し、壊れたビルドやアプリを
  覆い隠します。[BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)
  が却下するのは、まさにこの吸収です。復旧は #1368 の `crash_retries` の上限を保ち、変えるのは、復旧が*どの*
  クラッシュを捕捉するかだけです。
- **ジョブ単位の再実行（GitHub の re-run や `pytest-rerunfailures`）。** 再実行は、原因を取り除かず flaky さを
  覆い隠します。1 回の悪い起動から回復するためにジョブ全体（ビルドを含む）をやり直し、しかもその道すがら
  60 秒の空ポーリングを消費します。プロセス内の復旧のほうが安く、run を 1 回で green に保てます。手動のジョブ再実行は、
  修正ではなく補完的な運用フォールバックにとどめます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] ユニット 1 — lease をパイプラインの crash リトライの `try` の内側に置き、bring-up（launch・
  readiness）のクラッシュを step 時のクラッシュと同様に回復させる。
- [x] ユニット 2 — runner プロセスが終了していたら crash-recovery で即座に失敗させる（環境から
  liveness 述語を渡す。BE-0287 のプロセス生存ケースは不変）。
- [x] ユニット 3 — パイプラインとチャネルのシームのオフデバイステスト。
- [x] ユニット 4 — runner の XCUITest 操作を直列化（`Router.actuationLock`）し、XCTest ホストを abort
  させる並行スナップショット再入を取り除く。`conformance` ゲートにも届く。
- [x] ユニット 5 — crash-retry 予算を設定可能にし（`BAJUTSU_CRASH_RETRIES`）、on-device レーンで
  ユニット 4 の背後の安全余裕として 2 に引き上げる。

## 参考

- [PR #1368](https://github.com/bajutsu-e2e/bajutsu/pull/1368)：本項目が完成させるパイプラインのバックエンドクラッシュ復旧（`base.BackendCrashError`、`crash_retries`）。
- [BE-0207：XCUITest runner チャネルを一時的なタイムアウトに強くする](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md)
- [BE-0218：E2E Simulator ゲートを安定させる](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)
- [BE-0287：マルチタッチ操作下での XCUITest runner チャネルのレジリエンス](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md)
- [BE-0310：iOS アクセシビリティの画面遷移 readiness](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md)
- [BE-0319：XCUITest のコールド runner 起動を診断可能で自己修復的にする](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience-ja.md)
- [BE-0049：決定性と flaky さの監査](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)
- `bajutsu/runner/pipeline.py`：`_ScenarioRunner.run_one`（lease を内側へ移す crash リトライループ）、`_default_crash_retries`（`BAJUTSU_CRASH_RETRIES` 予算）。
- `bajutsu/drivers/xcuitest.py`：`_with_crash_recovery`、`_http_transport`、`XcuitestDriver`（チャネルとその crash-recovery シーム）。
- `bajutsu/platform_lifecycle/environments/xcuitest.py`：`XcuitestEnvironment`（liveness 述語が読む `xcodebuild` サブプロセスのハンドルを持つ）。
- `BajutsuKit/Sources/BajutsuRunner/Router.swift`：`Router.actuationLock`（runner の XCUITest 操作を直列化して再入クラッシュを取り除く）。
- `.github/workflows/ios-e2e.yml`：on-device レーンの `BAJUTSU_CRASH_RETRIES` 予算。

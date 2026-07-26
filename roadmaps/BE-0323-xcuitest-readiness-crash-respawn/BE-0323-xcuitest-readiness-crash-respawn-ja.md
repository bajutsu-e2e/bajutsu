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

run パイプラインは、バックエンドのクラッシュに対する復旧を得ました（PR
[#1368](https://github.com/bajutsu-e2e/bajutsu/pull/1368)）。scenario の実行中に起きた
`base.BackendCrashError` を判定ではなくインフラの問題として扱い、死んだ lease を破棄し、新しい
デバイスを lease し（コールド再起動）、scenario 全体を `crash_retries` の範囲で再実行します。しかし、
この復旧のきっかけとなったクラッシュを依然として run の失敗に至らせる残課題が 2 つあり、本項目は
その両方を塞ぎます。1 つ目は、この復旧が lease をリトライループの*内側*で呼びながら `try` の*外側*に
置いているため、**lease そのもの**——launch と readiness ゲート、どの scenario ステップよりも前——で
起きたクラッシュがループを抜けて run を落とすことです。2 つ目は、runner の `xcodebuild`
**プロセスが終了**していても、crash-recovery 層が諦めるまで 60 秒の復旧予算をまるごと `GET /health` に
費やし、来ない復旧に 1 分を使うことです。本項目は lease をリトライの内側へ移して bring-up 時の
クラッシュを他と同様に回復させ、runner プロセスが消えているときは復旧を即座に失敗させます。

最初の修正の CI 実行が、その下の層をあらわにしました。混雑した CI ホストでは runner が**繰り返し**
クラッシュするため、最初の起動も再起動後のリトライも死にうえに、`conformance` ゲート——`bajutsu run`
ではなく pytest スイート——はパイプライン復旧にそもそも到達しません。クラッシュを源流までたどると、
runner の HTTP サーバは接続を並行に捌き、`app.snapshot()` / `app.screenshot()` は app を待つあいだ
main の run loop を回すので、並行する 2 つの `DispatchQueue.main.sync` 操作（scenario の `/elements`
と evidence の `/screenshot`）が再入します——2 つ目の XCUITest 呼び出しが 1 つ目の*内側*で main
スレッド上を走る——これは XCUITest が禁じており、XCTest ホストが abort します。そこで本項目は
**runner の XCUITest 操作を直列化**して再入を源流から取り除き（`conformance` を含む全ジョブに効く）、
**crash-retry 予算を設定可能**にして、従量課金の on-device レーンが残る 1 回限りのクラッシュを乗り切れる
ようにもします。

## 動機

必須の `run (xcuitest)` ジョブが、scenario のアサーションではなくセットアップ段階の失敗で flaky に
なりました。

```
XcuitestRunnerCrashError: runner channel GET /elements failed: the runner crashed mid-run and did
not recover within 60s
```

トレースバックはこの失敗を起動パス、すなわち `launch_driver` → `_await_ready` →
`driver.query()` → `GET /elements` に位置づけます。どの scenario 本体も走る前です。順を追うと次の
とおりです。

1. コールド起動（`_spawn_cold_with_retry`）は runner が `GET /health` に応答するまで待ってドライバを
   返します。runner は確かに起動しています。
2. `launch_driver` が `_await_ready` を呼び、その最初の `driver.query()` が `GET /elements` を発行
   します。
3. ここで runner がクラッシュします。
   [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md)
   と [BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md)
   が述べる、アプリ起動・画面出現に伴うクラッシュで、負荷の高い CI ホストでのみ再現します。その
   `xcodebuild` プロセスは終了します。
4. crash-recovery 層（`_with_crash_recovery`）はトランスポートの失敗を捕捉し、`GET` は冪等なので、
   runner が戻るのを 60 秒の復旧予算のあいだ `GET /health` をポーリングして待ちます。プロセスは
   もういないうえ誰も再起動しないため、ポーリングは 60 秒まるごと拒否され、最後に
   `XcuitestRunnerCrashError` を送出します。
5. このエラーは `_await_ready` と `launch_driver` の外へ抜けます。`launch_driver` はパイプラインが
   lease を作るために呼ぶものです。パイプラインの crash 復旧は lease をリトライループの内側で呼び
   ますが、その `lease` 呼び出しは `BackendCrashError` を捕捉する `try` の*外側*にあるため、lease 時の
   クラッシュは決して捕まりません。`run_all` の外へ伝播して run 全体を落とします。

このクラッシュは #1368 の復旧をすでに載せたブランチ上で表面化したので、復旧がこの窓を覆えていない
ことは実証済みです。覆えないのは、独立した 2 つの理由によります。

- **クラッシュは step 時ではなく lease 時に起きる。** #1368 は `_run_on_lease`——scenario 本体——を
  crash リトライの `try` で包みますが、その手前の `self.lease(...)`（`launch_driver` と readiness
  ゲートを走らせる）は `try` の外にあります。lease を*作っている*最中に送出されるクラッシュは、まさに
  リトライが見ないケースです。
- **復旧が死んだプロセスに窓をまるごと費やす。** クラッシュを捕捉できても、プロセスが終了した runner
  を 60 秒ポーリングするのは避けられない失敗の前の空き時間です。
  [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience-ja.md)
  はすでに*コールド起動*で `xcodebuild` プロセスが死んだときに即座に失敗させますが、実行中の
  crash-recovery パスにはそうした liveness チェックがありません。

結果は
[BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)
が取り除こうとした flaky ゲートの代償です。実際に何かが壊れているのかについて何のシグナルも持たない
赤い必須チェックが、手動でジョブを再実行するまで解消されません。

## 詳細設計

作業は 5 つのユニットに分かれます。ユニット 1 と 2 は上記の 2 つの隙間——bring-up の
クラッシュを回復し、次に死んだプロセスに窓を費やすのをやめる——で、ユニット 3 は両方を注入可能な
シームの上でテストします。ユニット 4 と 5 は、その下にある再入クラッシュを塞ぎます。runner の
XCUITest 操作を直列化（根本原因）し、その安全余裕として crash-retry 予算を引き上げます。Simulator は不要です。

1. **lease を crash リトライの `try` の内側に置く。** `_ScenarioRunner.run_one`
   （`bajutsu/runner/pipeline.py`）で、`self.lease(...)` の呼び出しを `try` の直前から `try` の内側へ
   移します。これにより、lease を作っている最中——launch と readiness ゲート——で送出された
   `base.BackendCrashError` を、step がクラッシュした scenario をすでに再実行するのと同じ復旧が捕捉
   します。リトライは新たに lease を取り（プールが死んだ warm runner を捨てるので、これはコールド
   再起動です）、step 時のクラッシュとまったく同じに振る舞います。`_run_on_lease` は引き続き自身の
   `finally` で lease を release するので、step 中のクラッシュでも lease は決して漏れません。lease 時の
   クラッシュは release すべき lease を残しません（プールは失敗した自分の lease を片付けます）ので、
   この移動で漏れは増えません。`crash_retries` の上限は不変なので、bring-up が毎回クラッシュすれば
   これまで通り大きく失敗します
   （[BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)）。

2. **runner プロセスが終了していたら crash-recovery で即座に失敗させる。** crash-recovery 層
   （`bajutsu/drivers/xcuitest.py` の `_with_crash_recovery`）は、runner が戻るという前提のもとで
   復旧予算を待ちます。この前提は runner の*プロセス*できれいに分かれます。プロセスが生きていて
   一時的に到達できないだけなら
   [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md)
   の復旧可能なケースなので待ち続けるべきですが、プロセスが**終了**していれば `/health` に二度と
   応答しません。復旧のあいだ誰もそのポートで再起動しないためです。ですから予算を丸ごと待つのは
   空き時間です。`xcodebuild` サブプロセスのハンドルを持つ環境から、runner の生存を返す述語
   （`runner_alive: () -> bool`）を `make_driver` 経由で crash-recovery 層へ渡します。クラッシュ時に
   この述語がプロセスの消滅を報告したら、死んだポートをポーリングせず、識別しやすい診断とともに即座に
   失敗します。ユニット 1 のパイプライン復旧が新しいデバイスを lease して scenario を再実行します。
   述語がない場合（テストのフェイク）やプロセスの生存を報告する場合の挙動は BE-0287 と寸分違わない
   ため、本ユニットはすでに詰んでいる待ちを*短くする*だけで、復旧可能な瞬断を失敗に変えることは決して
   ありません。

3. **両シームのオフデバイステスト。** どちらのユニットもフェイクの注入で Simulator なしに検証でき、
   パイプラインとチャネルのテストがすでに使っている隔離と同じです。次を確認します。bring-up で
   `BackendCrashError` を送出する lease がリトライの新しい lease で回復すること、毎回クラッシュする
   lease が上限の lease 回数ちょうどで大きく失敗すること（ユニット 1、`tests/runner/test_pipeline.py`）。
   liveness 述語がプロセスの**消滅**を報告するクラッシュは復旧予算をポーリングせず即座に失敗すること、
   **生存**を報告するクラッシュは予算を待ち続け BE-0287 が保たれること、そして述語がない既定の挙動が
   不変であること（ユニット 2、`tests/test_xcuitest.py`）。

4. **runner の XCUITest 操作を直列化して再入クラッシュを取り除く（根本原因）。** 常駐 runner の HTTP
   サーバ（`BajutsuRunner`）は接続を並行に捌きます——意図的に、長いジェスチャ中も `/health`
   ポーリングに応答できるようにするためです（BE-0287）。あらゆる XCUITest 呼び出しは
   `DispatchQueue.main.sync` で main スレッドへ marshal されますが、`app.snapshot()` /
   `app.screenshot()` / 操作は app を待つあいだ main の run loop を回し、その spin が main の
   dispatch キューを drain します——ので並行する 2 つ目の操作のブロックが 1 つ目の*内側*で走り、
   XCUITest に再入して XCTest ホストを abort させます（CI 固有の実行中クラッシュ。混雑したホストほど
   窓が広がる）。connection スレッドで main へ dispatch する*前に*握るロック（`Router.actuationLock`）を
   追加し、1 つ目が実行中のあいだ 2 つ目が main へ enqueue されないようにします。すると run loop の
   spin には drain すべき再入対象がありません。`/health` はロックを取りません（XCUITest 状態に触れない）
   ので、並行サーバは「runner は busy であって dead ではない」シグナルを保ちます。これはユニット 1・2 の
   パイプライン復旧を通らない `conformance` ジョブに届く唯一の修正です。main スレッド外から 2 つの読み取りを
   並行に走らせ、決して重ならない・再入しないことを確かめる `BajutsuRunner` テストで担保します。

5. **crash-retry 予算を設定可能にし、on-device レーンで引き上げる。** パイプラインの `crash_retries` は
   ハードコードの既定 1（2 試行）でした。`BAJUTSU_CRASH_RETRIES` から読む（未設定なら 1）ようにして、
   従量課金の on-device CI レーンがコード変更なしで引き上げられるようにし、そこで 2（3 試行）に設定します。
   runner のクラッシュは*テストホスト*の死であってアプリの判定ではないので、1 回限りを乗り切るのは
   flake の吸収ではありません——毎回クラッシュする scenario は予算を使い切れば依然として大きく失敗します
   （[BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)）。これは
   ユニット 4 の根本修正の*背後*にある安全余裕であって、代替ではありません。env が既定を設定すること
   （未設定・不正 → 1、`0` で無効化）と `run_all` がそれを end to end で尊重することをテストで担保します。

## 検討した代替案

- **パイプラインではなく `launch_driver` の内側だけでリトライする。** launch ローカルのリトライは、
  パイプラインが知らないところで runner を再起動し、step 時のクラッシュに対してパイプラインがすでに
  持つ復旧を二重化し、「バックエンドがクラッシュした」の周りに 2 本目の並行した境界を引きます。lease を
  パイプラインの既存の `try` の内側へ移せば、bring-up と step の両方のクラッシュに 1 つの復旧を再利用
  できます。単一のシームを採り、却下します。
- **60 秒の復旧予算を全体的に短くする。** 予算を短くすればこのケースは速く失敗しますが、本物の
  BE-0287 の復旧（〜30 秒到達できないが*戻ってくる* runner）も早々に打ち切ってしまい、その予算が
  乗り切るために存在する flaky さをぶり返します。ユニット 2 は速い経路の鍵を時計の短縮ではなく
  *プロセス*の消滅に置くので、復旧可能なケースは予算をまるごと保ちます。却下します。
- **上限のないリトライ。** 上限なくリトライすると本当に繰り返すクラッシュを吸収し、壊れたビルドや
  アプリを覆い隠します。これはまさに
  [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)
  が却下する吸収です。復旧は #1368 の `crash_retries` の上限を保ち、変えるのは*どの*クラッシュを捕捉
  するかだけです。
- **ジョブ単位の再実行（GitHub の re-run や `pytest-rerunfailures`）。** 再実行は原因を取り除かず
  flaky さを覆い隠し、1 回の悪い起動から回復するためにジョブ全体（ビルドを含む）をやり直し、しかも
  その道すがら 60 秒の空ポーリングを消費します。プロセス内の復旧のほうが安く、run を 1 回で green に
  保てます。手動のジョブ再実行は修正ではなく補完的な運用フォールバックにとどめます。

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

- [PR #1368](https://github.com/bajutsu-e2e/bajutsu/pull/1368) — 本項目が完成させるパイプラインのバックエンドクラッシュ復旧（`base.BackendCrashError`、`crash_retries`）。
- [BE-0207 — XCUITest runner チャネルを一時的なタイムアウトに強くする](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md)
- [BE-0218 — E2E Simulator ゲートを安定させる](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)
- [BE-0287 — マルチタッチ操作下での XCUITest runner チャネルのレジリエンス](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md)
- [BE-0310 — iOS アクセシビリティの画面遷移 readiness](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness-ja.md)
- [BE-0319 — XCUITest のコールド runner 起動を診断可能で自己修復的にする](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience-ja.md)
- [BE-0049 — 決定性と flaky さの監査](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)
- `bajutsu/runner/pipeline.py` — `_ScenarioRunner.run_one`（lease を内側へ移す crash リトライループ）、`_default_crash_retries`（`BAJUTSU_CRASH_RETRIES` 予算）。
- `bajutsu/drivers/xcuitest.py` — `_with_crash_recovery`、`_http_transport`、`XcuitestDriver`（チャネルとその crash-recovery シーム）。
- `bajutsu/platform_lifecycle/environments/xcuitest.py` — `XcuitestEnvironment`（liveness 述語が読む `xcodebuild` サブプロセスのハンドルを持つ）。
- `BajutsuKit/Sources/BajutsuRunner/Router.swift` — `Router.actuationLock`（runner の XCUITest 操作を直列化して再入クラッシュを取り除く）。
- `.github/workflows/ios-e2e.yml` — on-device レーンの `BAJUTSU_CRASH_RETRIES` 予算。

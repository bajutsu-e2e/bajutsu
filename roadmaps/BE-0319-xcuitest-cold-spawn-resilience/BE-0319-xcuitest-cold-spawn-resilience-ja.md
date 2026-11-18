[English](BE-0319-xcuitest-cold-spawn-resilience.md) · **日本語**

# BE-0319 — XCUITest のコールド runner 起動を診断可能で自己修復的にする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0319](BE-0319-xcuitest-cold-spawn-resilience-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0319") |
| 実装 PR | [#1327](https://github.com/bajutsu-e2e/bajutsu/pull/1327)、[#1403](https://github.com/bajutsu-e2e/bajutsu/pull/1403) |
| トピック | Platform support |
| 関連 | [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md), [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md) |
<!-- /BE-METADATA -->

## はじめに

オンデバイスの XCUITest runner が、コールドの `xcodebuild test-without-building` から起動しきれないことがあります。そのときドライバは、起動予算の 120 秒間ずっと `GET /health` をポーリングし続けます。最後に `health never ready` で失敗します。既定では、この失敗は原因を示す手がかりを何も残しません。

PR [#1299](https://github.com/bajutsu-e2e/bajutsu/pull/1299) が、その土台を築きました。この PR は、runner の `xcodebuild` 出力を**任意設定**で捕捉する仕組みを加えました（`BAJUTSU_XCUITEST_RUNNER_LOG`）。捕捉した出力の末尾は、実行中クラッシュの*警告*に載ります。

本項目はこの作業を仕上げ、コールド起動を次の 3 点で改善します。

- 既定で**診断可能**にします。コールド起動の経路で捕捉を既定で有効にし、末尾を別立ての警告ではなく、失敗する `XcuitestChannelError` 自体に織り込みます。
- **即座に失敗**させます。`xcodebuild` プロセスが死んだ瞬間に、死んだポートを残り 2 分間ポーリングせずに中断します。
- 一度きりのコールド起動のばらつきに対して**自己修復的**にします。明確に失敗する前に、起動を 1 度だけ再試行します。

判定は弱めません。本当に壊れたビルドは 2 度とも失敗し、ゲートを止めます。

## 動機

`conformance (xcuitest)` ジョブが CI でばらつきます。ある実行は 14 個のエラーで失敗し、そのすべてが同一のセットアップ失敗でした。

```
XcuitestChannelError: xcuitest runner did not come up within 120.0s (health never ready)
```

runner を起動するモジュールスコープの fixture が一度失敗しました。すると 14 個の conformance テストがすべてセットアップ段階でエラーになり、必須ジョブ全体が沈みました。捕捉されたログには、起動予算のあいだ中、次の 1 対の行だけが繰り返し現れます。

```
runner channel GET /health failed (attempt 1/3), retrying: [Errno 61] Connection refused
runner channel GET /health failed (attempt 2/3), retrying: [Errno 61] Connection refused
…（この対が約 120 秒間繰り返される）
```

ログには、ソケットタイムアウトではなく `Connection refused` が最後まで続いています。つまり、runner のループバックサーバがポートを一度も開けませんでした。`xcodebuild test-without-building` が起動した XCTest ホストは、ソケットを bind する地点まで到達しなかったのです。

同じコミット上の別の xcuitest レーンは、ビルドも実行も通っています。これはコードのリグレッションではありません。負荷の高い CI Simulator 内で、XCTest ホストのコールド起動がばらついたのです。

PR #1299 はすでに runner の出力を捕捉し、その末尾を長さを区切って読み取ります（`XcuitestEnvironment` の `_open_runner_output` と `_runner_log_hint`、`bajutsu/platform_lifecycle/environments/xcuitest.py`）。それでも 3 つの隙間が残っています。この隙間が、まさにこの CI のばらつきを、診断不能で最大限に遅い失敗のままにしています。

1. **捕捉が任意設定で、既定は `DEVNULL`。** `_open_runner_output` は、`BAJUTSU_XCUITEST_RUNNER_LOG` がディレクトリを指すときに捕捉します。CI のように未設定のときは、runner は従来どおり `DEVNULL` へ起動します。したがって、実行を失敗させる*最初の*ばらつきは、どこにも捕捉されません。人間が変数を設定し、ばらつきの再発を待つほかありません。本当に必要な診断材料は、すでに手元にある失敗のためのものです。
2. **末尾が失敗ではなく警告に載る。** `_discard_runner` は、`_runner_log_hint` を `warning` のログ行として出します。実行を失敗させる `XcuitestChannelError: health never ready` は、その内容をまったく運びません。原因と失敗が別々の場所に落ちるため、失敗を読んでも、なぜ落ちたのかはわかりません。
3. **待機ループが依然として死んだプロセスを無視する。** `_discard_runner` は `self._runner_proc.poll()` を確認します。ただし確認が走るのは teardown であり、`await_ready` がすでに予算を丸ごと空回りした*後*です。この確認を health のポーリングの待機に挟む処理は、どこにもありません。起動 3 秒後に死んだ runner でも、残りの 117 秒を、誰も所有していないポートを探って費やします。最も速く診断できるはずの失敗（プロセスがすでに消えている）が、最も遅い失敗のままです。

この帰結は、[BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md) と [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md) が取り除こうとした、ばらつくゲートのコストそのものです。つまり、本当に何かが壊れているのかを何も示さない、赤い必須チェックです。ジョブを手作業で再実行してはじめて、このチェックは解消されます。

これらの項目と [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md) は、いずれも runner が起動した**後**のチャネルを堅牢にします。対象は、過渡的な transport の再試行、実行中クラッシュからの復帰、アプリ起動後の準備完了判定です。runner **プロセス自体のコールド起動**、つまり本件の失敗様態に触れる項目はありません。本項目がその隙間を埋めます。

## 詳細設計

作業は独立した 6 つの単位に分かれます。単位 1 と 2 は、PR #1299 の捕捉の接合部（`_open_runner_output` と `_runner_log_hint`）の上に直接立ちます。単位 3 から 5 は新規です。単位 6 は、単位 1 から 5 が PR #1327 で出荷されたあとに CI が表に出した、残りの問題を解きます。

1. **コールド起動の経路で、runner 出力の捕捉を既定で有効にする。** PR #1299 は、`BAJUTSU_XCUITEST_RUNNER_LOG` が設定されているときに捕捉します。コールド起動が、既定で実行ごとの一時領域と証跡領域へ捕捉するようにします。そうすれば、人間が変数を前もって用意しなくても、最初の CI のばらつきを診断できます。既存の変数は、捕捉先ディレクトリの上書きとして残します。成功時のコストは、teardown が刈り取れる小さなファイル 1 つだけです。

2. **捕捉した末尾を起動失敗のエラーに織り込む。** 現在、`_runner_log_hint` の末尾が届くのは、`_discard_runner` の警告だけです。起動待機が失敗したとき（タイムアウトでも、単位 3 の死亡プロセス検知でも）、長さを区切った末尾を `XcuitestChannelError` のメッセージ自体に含めます。すると、実行を失敗させるエラーが、runner が応答しなかったという事実だけでなく、*なぜ*応答しなかったのかを示します。末尾をそのまま引用することで、経路は決定論的なまま保たれ、LLM は関与しません（プライム指令 1）。

3. **待機中に runner プロセスの死亡で即座に失敗する。** PR #1299 の `self._runner_proc.poll()` の確認は、`_discard_runner` にあります。この関数は teardown であり、`await_ready` が予算を丸ごと空回りした後にしか到達しません。同じ確認を、死活対応ヘルパー（単位 5）を通じて、コールド起動の待機に挟みます。

   ヘルパーは、health のプローブの合間に、environment が所有する `xcodebuild` のハンドルをポーリングします。`poll()` が非 `None` の終了コードを返した瞬間に、ヘルパーは明確な診断（終了コードと単位 2 の末尾）とともに、直ちに中断します。残り予算のあいだ `Connection refused` をポーリングし続けることはしません。

   `_await_health` そのものは変更しません。`_await_health` を同じく呼ぶクラッシュ復帰経路（`bajutsu/drivers/xcuitest.py`）は、手つかずのままです。その経路は、常駐 runner をチャネル越しに駆動し、ポーリングすべきローカルのサブプロセスを持ちません。そのため、BE-0287 の実行中の挙動は保たれます。

4. **コールド起動を 1 度だけ再試行する。** ポートを一度も開けなかったコールドの XCTest ホスト起動は、過渡的なインフラのばらつきです。BE-0207 が transport 層で吸収するのと、同じ種類のものです。最初のコールド起動の失敗（単位 3 の即時失敗、またはタイムアウト）で、死んだ runner を破棄し、起動をやり直し、もう一度待ちます。**2 度目**の失敗では、両方の試行の捕捉した末尾とともに、明確に失敗します。

   1 度の再試行は、繰り返し起こる失敗を覆い隠さずに、一度きりのコールド起動のばらつきを吸収します。壊れたビルド、署名、アプリは 2 度とも失敗し、ゲートを止めます。この再試行は、[BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md) の「ばらつきを吸収によって許容しない」という姿勢を保ちます。再試行はコールド起動にのみ適用し、ウォーム再利用の経路（[BE-0291](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios-ja.md)）には決して適用しません。

5. **起動と待機の接合部に対するオフデバイスのテスト。**「死活の確認と長さを区切った再試行つきで準備完了を待つ」ロジックを、ヘルパーへ切り出します。ヘルパーは、注入可能なプロセスハンドルと起動 thunk を受け取るので、テストは Simulator なしでヘルパーを実行できます。チャネルのテストも、偽の transport を注入して、すでに同じ分離を使っています。テストは 3 つの場合を網羅します。
   - `poll()` が非 `None` を返すプロセスは、待機を即座に失敗させ（単位 3）、そのメッセージは捕捉した末尾を運びます（単位 2）。
   - 最初の試行の失敗に続く 2 度目の試行の成功は、1 度の再試行を通します（単位 4）。
   - 繰り返し起こる失敗は、ちょうど 2 度の試行で明確に失敗し、それ以上は試行しません。

6. **コールド起動の待機を、上限の満了ではなく、テスト実行の終了で打ち切る。** 単位 1 から 5 の出荷後も、`actuation (xcuitest)` と `bundled-runner (xcuitest)` の 2 つのレーンは、シナリオが 1 つも走らないうちに失敗し続けました。引き金は、Simulator のアプリ起動タイムアウトです。単位 3 と単位 4 は、この引き金に届きません。

   `xcodebuild` は `Failed to launch <bundle id>: Timed out attempting to launch app` を報告し、XCTest のスイートは終了します。ところが `xcodebuild` のプロセスは、そのあとも居座ります。単位 3 の死活確認が見張るのは、テスト実行ではなくプロセスです。確認は生きたハンドルを見続けるため、待機は上限まで走ります。

   PR [#1398](https://github.com/bajutsu-e2e/bajutsu/pull/1398) で観測された 2 件の失敗では、スイートが失敗を報告し終えたあと、300 秒の上限のうち 225 秒と 127 秒が空費されていました。

   この空費が、残りの問題を自己維持的にしています。上限は試行のあいだで共有される予算です（単位 4）。上限まで走った試行は、再試行に予算を残しません。再試行が吸収するために存在するはずの失敗こそが、再試行の決して届かない唯一の失敗になります。発生のたびに、`attempt 1/2: health never ready` が 1 行だけ上がっていました。

   単位 1 が既定で有効にした捕捉から、実行の終了を示す印を読み取り、そこで待機を終えます。アプリ起動タイムアウトは、数分ではなく数秒で失敗するようになります。待機が使わずに済んだ予算が、再試行を賄います。

## 検討した代替案

- **120 秒の起動タイムアウトを引き上げる。** ポートはこの窓のあいだずっと拒否されるため、待機を延ばしても、より遅く失敗するだけです。この変更は、症状（辛抱が足りない）に対処するだけで、原因（runner が bind しない）には触れません。却下します。
- **ジョブレベルの再実行（`pytest-rerunfailures` や GitHub の再実行）。** 再実行は、ばらつきの原因を表に出さずに隠します。しかも、1 度の悪い起動から復帰するために、120 秒の空回りを含む 14 テストのモジュール全体をやり直します。即時失敗と 1 度だけのコールド起動再試行のほうが安価で、診断材料も残ります。手作業のジョブ再実行は、修正そのものではなく、補完的な運用上の逃げ道にとどめます。
- **上限のない起動再試行。** 上限なく再試行すると、本当に繰り返し起こる失敗を吸収し、壊れたビルドを覆い隠します。これはまさに BE-0049 が退ける吸収です。再試行は 1 度に限ります。
- **#1299 のまま捕捉を任意設定で残す。** 運用者が前もって `BAJUTSU_XCUITEST_RUNNER_LOG` を設定しておく必要があります。すると、ゲートを失敗させるばらつき（最初の発生）は決して捕捉されず、後の再現だけが捕捉されます。コールド起動の経路で捕捉を既定で有効にすれば（単位 1）、すでに手元にある失敗を捕捉できます。既定で有効にする方を採り、却下します。
- **health の待機とは別に、runner の内部でアプリ起動を再試行する。** Swift の runner が自身の `XCUIApplication.launch()` のタイムアウトを捕まえ、`xcodebuild` を立て直さずに起動をやり直す方法もあります。この再試行は、単位 4 の runner ごとの再試行より安価です。それでも、単位 6 の Python 側での検知を選ぶ理由が 2 つあります。
  - すでに詰まった XCTest の自動化セッションを持つ runner の内部でやり直すと、たった今タイムアウトしたセッションをそのまま使い回すことになります。
  - 修正がロジックコアではなく Swift のパッケージに入るため、修正を取り込むにはすべてのレーンで runner の再ビルドが必要になります。

  単位 6 のあともアプリ起動タイムアウトが残る場合は、あらためて検討する価値があります。
- **`xcodebuild` の出力をファイルではなく CI ログへライブで流す。** ライブのストリーミングは、pytest の捕捉出力と入り混じり、成功経路では騒がしくなります。捕捉したファイルを失敗時に末尾表示すれば、同じ診断材料が得られ、runner が起動したときは静かなままです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 単位 1 — コールド起動の経路で runner 出力の捕捉を既定で有効にする（#1299 の上に立つ）。
- [x] 単位 2 — 捕捉した末尾を `XcuitestChannelError` の起動失敗メッセージに織り込む。
- [x] 単位 3 — コールド起動の待機中に `xcodebuild` プロセスが終了したら即座に失敗する。
- [x] 単位 4 — 明確に失敗する前に、コールド起動を 1 度だけ再試行する。
- [x] 単位 5 — 起動・待機の接合部に対するオフデバイスのテスト。
- [x] 単位 6 — テスト実行の終了でコールド起動の待機を打ち切り、Simulator のアプリ起動タイム
      アウトが即座に失敗して再試行の予算を残すようにする。

## 参考

- [BE-0207 — XCUITest runner チャネルを過渡的タイムアウトに強くする](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md)
- [BE-0218 — E2E Simulator ゲートを安定させる](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)
- [BE-0287 — マルチタッチ操作下での XCUITest runner チャネルの耐性](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md)
- [BE-0290 — XCUITest を既定の iOS バックエンドにし idb を退役させる](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md)
- [BE-0049 — 決定性とばらつきの監査](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)
- [PR #1299](https://github.com/bajutsu-e2e/bajutsu/pull/1299)：本項目が土台とする、任意設定の runner 出力捕捉です。
- `bajutsu/platform_lifecycle/environments/xcuitest.py`：`_spawn_cold`、`_open_runner_output`、`_runner_log_hint`、`_discard_runner` です（コールド起動、その出力捕捉、および teardown）。
- `bajutsu/drivers/xcuitest.py`：`await_ready`、`_await_health`、`_with_retry` です（起動待機とチャネルの再試行の接合部）。

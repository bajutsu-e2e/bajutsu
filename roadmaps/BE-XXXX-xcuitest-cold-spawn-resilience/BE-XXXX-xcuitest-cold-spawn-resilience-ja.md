[English](BE-XXXX-xcuitest-cold-spawn-resilience.md) · **日本語**

# BE-XXXX — XCUITest のコールド runner 起動を診断可能で自己修復的にする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-xcuitest-cold-spawn-resilience-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Platform support |
| 関連 | [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md), [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md) |
<!-- /BE-METADATA -->

## はじめに

オンデバイスの XCUITest runner がコールドの `xcodebuild test-without-building` から起動しきれな
かったとき、ドライバは起動予算の 120 秒間ずっと `GET /health` をポーリングし続け、最後に `health
never ready` で失敗します。そして既定では、その失敗は原因を示す手がかりを何も残しません。PR
[#1299](https://github.com/bajutsu-e2e/bajutsu/pull/1299) がその土台を築きました。runner の
`xcodebuild` 出力を **任意設定** で捕捉し（`BAJUTSU_XCUITEST_RUNNER_LOG`）、その末尾を実行中クラッ
シュの *警告* に載せるものです。本項目はこの作業を仕上げ、コールド起動を既定で **診断可能** にし
（コールド起動の経路では捕捉を既定で有効にし、末尾を別立ての警告ではなく失敗する
`XcuitestChannelError` 自体に織り込む）、**即座に失敗** させ（`xcodebuild` プロセスが死んだ瞬間に、
死んだポートを残り 2 分間ポーリングせずに中断する）、一度きりのコールド起動のばらつきに対して
**自己修復的** にします（明確に失敗する前に起動を 1 度だけ再試行する）。判定は弱めません。本当に
壊れたビルドは 2 度とも失敗し、ゲートを止めます。

## 動機

`conformance (xcuitest)` ジョブが CI でばらつきます。ある実行は 14 個のエラーで失敗し、そのすべ
てが同一のセットアップ失敗でした。

```
XcuitestChannelError: xcuitest runner did not come up within 120.0s (health never ready)
```

runner を起動するモジュールスコープの fixture が一度失敗すると、14 個の conformance テストがすべ
てセットアップ段階でエラーになり、必須ジョブ全体が沈みます。捕捉されたログには、起動予算のあいだ
中、次の 1 対の行だけが繰り返し現れます。

```
runner channel GET /health failed (attempt 1/3), retrying: [Errno 61] Connection refused
runner channel GET /health failed (attempt 2/3), retrying: [Errno 61] Connection refused
…（この対が約 120 秒間繰り返される）
```

ソケットタイムアウトではなく `Connection refused` が最後まで続くということは、runner のループ
バックサーバがポートを一度も開けなかった、つまり `xcodebuild test-without-building` が起動した
XCTest ホストが、ソケットを bind する地点まで到達しなかったということです。同じコミット上の別の
xcuitest レーンはビルドも実行も通っているため、これはコードのリグレッションではなく、負荷の高い
CI Simulator 内での XCTest ホストのコールド起動のばらつきです。

PR #1299 はすでに runner の出力を捕捉し、その末尾を長さを区切って読み取ります
（`XcuitestEnvironment` の `_open_runner_output` / `_runner_log_hint`、
`bajutsu/platform_lifecycle/environments/xcuitest.py`）。しかし、まさにこの CI のばらつきを診断
不能で最大限に遅い失敗のままにしている隙間が、3 つ残っています。

1. **捕捉が任意設定で、既定は `DEVNULL`。** `_open_runner_output` は `BAJUTSU_XCUITEST_RUNNER_LOG`
   がディレクトリを指すときだけ捕捉します。CI のように未設定のときは、runner は従来どおり `DEVNULL`
   へ起動します。したがって、実行を失敗させる *最初の* ばらつきは、どこにも捕捉されません。人間が
   変数を設定し、ばらつきの再発を待つほかありません。本当に必要な診断材料は、すでに手元にある失敗
   のためのものです。
2. **末尾が失敗ではなく警告に載る。** `_runner_log_hint` は `_discard_runner` が `warning` のログ
   行として出しますが、実行を失敗させる `XcuitestChannelError: health never ready` はその内容を
   まったく運びません。原因と失敗が別々の場所に落ちるため、失敗を読んでも、なぜ落ちたのかはわかり
   ません。
3. **待機ループが依然として死んだプロセスを無視する。** `_discard_runner` は
   `self._runner_proc.poll()` を確認しますが、それは teardown、つまり `await_ready` がすでに予算を
   丸ごと空回りした *後* にしか到達しません。その確認を health のポーリングの待機に挟む処理はどこ
   にもないため、起動 3 秒後に死んだ runner でも、残りの 117 秒を、誰も所有していないポートを探って
   費やします。最も速く診断できるはずの失敗（プロセスがすでに消えている）が、最も遅い失敗のまま
   です。

その帰結は、
[BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md)
と [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)
が取り除こうとした、ばらつくゲートのコストそのものです。すなわち、本当に何かが壊れているのかを何も
示さない、赤い必須チェックであり、ジョブを手作業で再実行してはじめて解消されるものです。

これらの項目、そして
[BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md)
は、いずれも runner が起動した **後** のチャネルを堅牢にします。過渡的な transport の再試行、実行
中クラッシュからの復帰、アプリ起動後の準備完了判定です。runner **プロセス自体のコールド起動**、
つまり本件の失敗様態に触れる項目はありません。本項目がその隙間を埋めます。

## 詳細設計

作業は独立した 5 つの単位に分かれます。単位 1 と 2 は PR #1299 の捕捉の接合部
（`_open_runner_output` / `_runner_log_hint`）の上に直接立ち、単位 3〜5 は新規です。

1. **コールド起動の経路で、runner 出力の捕捉を既定で有効にする。** #1299 は
   `BAJUTSU_XCUITEST_RUNNER_LOG` が設定されているときだけ捕捉します。コールド起動が既定で
   ——実行ごとの一時領域・証跡領域へ——捕捉するようにし、人間が変数を前もって用意しなくても最初の
   CI のばらつきを診断できるようにします。既存の変数は捕捉先ディレクトリの上書きとして残します。
   成功時のコストは、teardown が刈り取れる小さなファイル 1 つだけです。

2. **捕捉した末尾を起動失敗のエラーに織り込む。** 現在、`_runner_log_hint` の末尾は
   `_discard_runner` の警告にしか届きません。起動待機が失敗したとき（タイムアウトでも、単位 3 の
   死亡プロセス検知でも）、その長さを区切った末尾を `XcuitestChannelError` のメッセージ自体に含め、
   実行を失敗させるエラーが、runner が応答しなかったという事実だけでなく *なぜ* 応答しなかったのか
   を示すようにします。末尾をそのまま引用することで、経路は決定的なまま保たれ、LLM は関与しません
   （プライム指令 1）。

3. **待機中に runner プロセスの死亡で即座に失敗する。** #1299 の `self._runner_proc.poll()` の確認
   は `_discard_runner`、つまり `await_ready` が予算を丸ごと空回りした *後* にしか到達しない teardown
   にあります。同じ確認を、死活対応ヘルパー（単位 5）を通じてコールド起動の待機に挟みます。ヘルパー
   は health のプローブの合間に、environment が所有する `xcodebuild` のハンドルをポーリングし、
   `poll()` が非 `None` の終了コードを返した瞬間に、残り予算のあいだ `Connection refused` をポーリング
   するのではなく、明確な診断（終了コードと単位 2 の末尾）とともに直ちに中断します。`_await_health`
   そのものは変更しないので、これを同じく呼ぶクラッシュ復帰経路（`bajutsu/drivers/xcuitest.py`）は
   手つかずのままです。そちらは常駐 runner をチャネル越しに駆動し、ポーリングすべきローカルのサブ
   プロセスを持たないため、BE-0287 の実行中の挙動は保たれます。

4. **コールド起動を 1 度だけ再試行する。** ポートを一度も開けなかったコールドの XCTest ホスト
   起動は、過渡的なインフラのばらつきであり、BE-0207 が transport 層で吸収するのと同じ種類のもの
   です。最初のコールド起動の失敗（単位 3 の即時失敗、またはタイムアウト）で、死んだ runner を
   破棄し、起動をやり直し、もう一度待ちます。**2 度目** の失敗では、両方の試行の捕捉した末尾と
   ともに明確に失敗します。これにより、一度きりのコールド起動のばらつきを、繰り返し起こる失敗を
   覆い隠すことなく吸収します。壊れたビルド・署名・アプリは 2 度とも失敗し、ゲートを止めるので、
   [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)
   の「ばらつきを吸収によって許容しない」という姿勢を保ちます。再試行はコールド起動にのみ適用し、
   ウォーム再利用の経路（[BE-0291](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios-ja.md)）には決して適用しません。

5. **起動・待機の接合部に対するオフデバイスのテスト。**「死活の確認と長さを区切った再試行つきで
   準備完了を待つ」ロジックを、注入可能なプロセスハンドルと起動 thunk を受け取るヘルパーへ切り
   出し、Simulator なしで実行できるようにします（チャネルのテストがすでに偽の transport を注入
   して使っているのと同じ分離です）。次を網羅します。`poll()` が非 `None` を返すプロセスは待機を
   即座に失敗させ（単位 3）、そのメッセージは捕捉した末尾を運ぶ（単位 2）。最初の試行の失敗に続く
   2 度目の試行の成功は、1 度の再試行を通す（単位 4）。繰り返し起こる失敗は、ちょうど 2 度の試行で
   明確に失敗し、それ以上は試行しない。

## 検討した代替案

- **120 秒の起動タイムアウトを引き上げる。** ポートはこの窓のあいだずっと拒否されるため、待機を
  延ばしても、より遅く失敗するだけです。症状（辛抱が足りない）に対処するだけで、原因（runner が
  bind しない）には触れません。却下します。
- **ジョブレベルの再実行（`pytest-rerunfailures` や GitHub の再実行）。** 再実行はばらつきの原因を
  表に出さずに隠し、しかも 1 度の悪い起動から復帰するために、120 秒の空回りを含む 14 テストの
  モジュール全体をやり直します。即時失敗と 1 度だけのコールド起動再試行のほうが安価で、診断材料
  も残ります。手作業のジョブ再実行は、修正そのものではなく、補完的な運用上の逃げ道にとどめます。
- **上限のない起動再試行。** 上限なく再試行すると、本当に繰り返し起こる失敗を吸収し、壊れたビルド
  を覆い隠します。これはまさに BE-0049 が退ける吸収です。1 度の再試行に上限を設けます。
- **#1299 のまま捕捉を任意設定で残す。** 運用者が前もって `BAJUTSU_XCUITEST_RUNNER_LOG` を設定して
  おく必要があると、ゲートを失敗させるばらつき——最初の発生——は決して捕捉されず、後の再現だけが
  捕捉されます。コールド起動の経路で捕捉を既定で有効にすれば（単位 1）、すでに手元にある失敗を
  捕捉できます。既定で有効にする方を採り、却下します。
- **`xcodebuild` の出力をファイルではなく CI ログへライブで流す。** ライブのストリーミングは
  pytest の捕捉出力と入り混じり、成功経路では騒がしくなります。捕捉したファイルを失敗時にだけ
  末尾表示すれば、runner が起動したときは静かなまま、同じ診断材料が得られます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 単位 1 — コールド起動の経路で runner 出力の捕捉を既定で有効にする（#1299 の上に立つ）。
- [ ] 単位 2 — 捕捉した末尾を `XcuitestChannelError` の起動失敗メッセージに織り込む。
- [ ] 単位 3 — コールド起動の待機中に `xcodebuild` プロセスが終了したら即座に失敗する。
- [ ] 単位 4 — 大声で失敗する前に、コールド起動を 1 度だけ再試行する。
- [ ] 単位 5 — 起動・待機の接合部に対するオフデバイスのテスト。

## 参考

- [BE-0207 — XCUITest runner チャネルを過渡的タイムアウトに強くする](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md)
- [BE-0218 — E2E Simulator ゲートを安定させる](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)
- [BE-0287 — マルチタッチ操作下での XCUITest runner チャネルの耐性](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md)
- [BE-0290 — XCUITest を既定の iOS バックエンドにし idb を退役させる](../BE-0290-xcuitest-default-ios-backend/BE-0290-xcuitest-default-ios-backend-ja.md)
- [BE-0049 — 決定性とばらつきの監査](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)
- [PR #1299](https://github.com/bajutsu-e2e/bajutsu/pull/1299) — 本項目が土台とする、任意設定の runner 出力捕捉。
- `bajutsu/platform_lifecycle/environments/xcuitest.py` — `_spawn_cold`、`_open_runner_output`、`_runner_log_hint`、`_discard_runner`（コールド起動、その出力捕捉、および teardown）。
- `bajutsu/drivers/xcuitest.py` — `await_ready`、`_await_health`、`_with_retry`（起動待機とチャネルの再試行の接合部）。

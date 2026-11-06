[English](BE-0305-driver-resilience-fault-injection.md) · **日本語**

# BE-0305 — ドライバ耐障害経路への実機障害注入カバレッジ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0305](BE-0305-driver-resilience-fault-injection-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0305") |
| 実装 PR | [#1461](https://github.com/bajutsu-e2e/bajutsu/pull/1461), [#1724](https://github.com/bajutsu-e2e/bajutsu/pull/1724) |
| トピック | ドライバとバックエンドのアーキテクチャ |
| 関連 | [BE-0254](../BE-0254-coordinate-tree-driver-base/BE-0254-coordinate-tree-driver-base-ja.md), [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md), [BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve-ja.md), [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md) |
<!-- /BE-METADATA -->

## はじめに

実デバイスの障害条件を生き延びるために存在する耐障害機構が、2つあります。CI は、どちらも実際の障害で
発火させたことがありません。

- **transient-empty リトライ**：`CoordinateTreeDriver` のリトライ
  （[BE-0254](../BE-0254-coordinate-tree-driver-base/BE-0254-coordinate-tree-driver-base-ja.md)）は、idb と
  adb が遷移途中で返す、ほぼ空の要素ツリーのために存在します。そのテストは、要素数の合成シーケンス
  （`[3, 1, 3]`）を組み立て、backoff をゼロ化しています。
- **crash-recovery とリトライ**：XCUITest チャネルの経路
  （[BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md)、
  [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md)）は、
  常駐ランナーが強制終了またはフリーズしたときのために存在します。そのテストは、入れ子のクロージャから
  合成例外を送出しているだけです。

実際に実機で動く conformance suite は、どちらの経路も踏みません。画面はあらかじめシードされ、準備完了を
待ってから使われるため、transient-empty の分岐には到達しません。操作の途中でランナーを意図的に強制終了
させるジョブもありません。本項目は、両方に対する実際の障害注入（fault injection）のカバレッジを追加します。

## 動機

合成された要素数シーケンスや送出された例外が証明するのは、リトライや復旧の*コード経路*が、発火時に実行
されることです。これは制御フローに対する、実質的で有用なカバレッジです。しかし、耐障害機構が本来対象と
する実際の条件を、本当に生き延びられるかどうかは証明できません。

- idb や uiautomator の遷移途中でほぼ空になるレスポンスの、実際の形状とタイミング。
- 強制終了した XCUITest 常駐ランナーの、実際のソケットレベルの失敗モード（正常な RST、ハングした接続、
  部分的な書き込み）と、実際の再起動レイテンシ。

実際の検出ヒューリスティクスを壊す回帰が起こり得ます。`_is_transient_empty` の閾値や、クラッシュ分類器の
例外マッチングです。合成フィクスチャによるテストが green のままなら、その回帰は気づかれずに出荷されます。
CI のどこにも、耐障害機構が生き延びるために存在する条件そのものを再現するものがないからです。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **transient-empty の実際の障害注入（idb/adb）**：実機上の conformance または E2E に、ケースを追加します。
  このケースは、実際にほぼ空の中間ツリーを生む画面遷移を意図的に駆動します。あるいは、その条件を再現する
  人為的な競合を加えます。`CoordinateTreeDriver` のリトライが、誤った「要素が見つからない」を出さずに
  回復することを検証します。
- **crash-recovery の実際の障害注入（XCUITest）**：シナリオの途中で、常駐 BajutsuRunner プロセスを意図的に
  強制終了またはフリーズさせる実機上のケースを追加します。ドライバの crash-recovery 経路がランナーを
  再起動することを検証します。そのうえで、シナリオが回復するか、無関係なタイムアウトではなく正しい
  `XcuitestRunnerCrashError` 由来の診断で失敗することを検証します。
- **両方ともまずゲート対象外のシグナルとして着地させる**：障害注入レーンは、既存の conformance suite より
  も本質的にフレーキーになるリスクが高くなります。
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
  の前例に従い、それぞれのレーンを、安定を確認してから必須化します。
- **既存の合成フィクスチャによるユニットテストはそのまま残す**：制御フローロジック自体に対する高速で決定的
  な検証として、引き続き適切だからです。本項目はその下に実際の条件による層を追加するのであって、置き換え
  ではありません。

## 検討した代替案

- **制御フローのロジックがユニットテストされていることを根拠に、合成フィクスチャを信頼する**：作り上げた
  要素数シーケンスや送出された例外に対して、制御フローが正しいとします。それは、検出ヒューリスティクスが
  対象とする実際の条件で本当に発火するかを、何も語りません。その条件で発火することこそ、耐障害機構が保証
  するために存在する性質です。
- **実運用で耐障害機構が失敗するまで実際のカバレッジ追加を待つ**：リトライや復旧の経路が現場で静かに失敗
  することこそ、CI での障害注入がユーザーより先に捕まえるべき結末です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] idb/adb 向けに、実際の transient-empty 障害注入をまずゲート対象外で追加する。
- [x] XCUITest 向けに、実際の crash-recovery 障害注入をまずゲート対象外で追加する。
- [x] それぞれ安定後に必須化する。
- [x] 既存の合成フィクスチャによるユニットテストを、高速で決定的な制御フロー検証として残す。

ログ:

- [#1461](https://github.com/bajutsu-e2e/bajutsu/pull/1461) 両方のレーンをゲート対象外のシグナルとして
  着地させました。
  - **adb 側**：`fault-injection (adb)` は、エミュレータのディスプレイをスリープさせます。この操作で実際の
    読み取り元が要素 0 を返すことを実測しました。判定の下限は 2 要素で、別のものと見誤るようなエラーは
    出ません。リトライはそこを乗り越え、既知の要素が解決できます。2 つ目のケースは、ディスプレイを
    落としたまま保って予算を使い切らせ、その結末が明示的な `ElementNotFound` であることを固定します。
  - adb の 2 つのケースは、どちらもミューテーションで確認しました。`_READY_MIN` をゼロにすると、動機の節が
    述べるとおりに検出ヒューリスティクスが壊れ、両方が赤くなります。組み立てた要素数シーケンスでは、
    これができません。
  - **XCUITest 側**：`fault-injection (xcuitest)` は、ランナー自身のホストプロセスにシグナルを送ります。
    `SIGSTOP` はソケットを accept できる状態に保ったまま応答を止めます。短い凍結は BE-0207 のリトライが
    吸収します（実測 15.5 秒、1 回目の再試行で回復）。リトライ予算を超える凍結は、BE-0287 の
    crash-recovery が乗り越えます（実測 46.6 秒、ログは「recovered from a mid-run crash; re-issuing」）。
    `SIGKILL` では、実行中のランナー障害を名指しする `BackendCrashError` で読み取りが終わります。
  - どちらのレーンも待ち時間を推測しません。各ケースは、検証対象の層に到達したというドライバ自身のログ記録
    （`tests/fault_injection.py`）を合図に、障害を解除します。そのため、これまで無言だった
    `CoordinateTreeDriver` のリトライにログ行を追加し、高速スイートのテストも付けました。
  - 合成フィクスチャは、高速な制御フロー検証としてそのまま残します。
  - 作業中に実測した、記録に値することが1つあります。強制終了したランナーは、crash-recovery のプロセス終了
    による早期失敗の経路を通りません。`_runner_alive` が見るのは `xcodebuild` の親プロセスで、強制終了した
    ホストプロセスより長く生き残るからです。診断は正しいものの、ただちにではなく、60 秒の復旧ウィンドウを
    使い切ったあとに届きます。判定は正しく、明確に失敗するため、本項目ではそのままにしました。より厳密な
    生存確認は、別項目にします。
- [#1724](https://github.com/bajutsu-e2e/bajutsu/pull/1724) 両方のレーンを、それぞれのレーンの必須集約
  チェックへ入れ、本項目を完了しました。必須化の条件は「安定を確認してから」でした。
  - 安定を数えて決めようとした最初の試みは、誤った答えを出しました。`gh run list --limit 150` が返すのは
    直近の 150 run だけで、このリポジトリの run 密度では 4 日程度しか遡りません。そのため
    `--jq 'select(.createdAt > "2026-08-04")'` というフィルタは、150 件すべてに一致し、何も絞っていません
    でした。その 4 日間は、状況が変わったあとの綺麗な側に、丸ごと収まっていました。両レーンが無傷に見えた
    のは、そのためです。
  - Actions API をページングして、2026-08-04 から 2026-08-22 までの全 run を数え直しました。記録は
    `fault-injection (xcuitest)` が 116 回成功に対して**78 回失敗**、`fault-injection (adb)` が 378 回成功に
    対して 4 回失敗です。
- 必須化を正当化するのは、失敗率の低さではなく、原因が判明したことです。
  - iOS の 78 回の失敗は、すべて 2026-08-13T12:58Z より前に起きています。この時刻に
    [#1609](https://github.com/bajutsu-e2e/bajutsu/pull/1609) が
    `fix(xcuitest): keep the runner HTTP server alive through abandoned connections` をマージしました。
    最後の失敗は、そのマージの 30 分前です。
  - このレーンは、ランナーを `SIGSTOP` で止めたまま保ちます。相手が去った接続は、まさにそこから生まれます。
    そのためレーンは、健全なランナーを駆動するどのスイートよりも高い頻度で、その SIGPIPE を踏んでいました。
    レーンがランナーチャネルの実際の不具合を見つけたわけで、それがレーンの存在理由です。
  - その修正の後、`fault-injection (xcuitest)` は 64 回実行して 1 回も失敗していません。同じ期間の
    `conformance (xcuitest)` は、58 回成功して 2 回失敗しています。
  - `fault-injection (adb)` は、同じ期間に 73 回実行して失敗は 1 回でした。その 1 回は、レーンが検証して
    いる内容ではなく、エミュレータが起動しなかったこと（`Unable to connect to adb daemon on port: 5037`）に
    よるものです。同じ期間の `conformance (adb)` は、67 回中 4 回失敗しています。
  - 両レーンは今、ずっとゲートに乗っているスイートと同等か、それよりよい水準にあります。必須化が拠る
    のは、この比較です。
- 必須化は、ゲートの実時間を増やしません。各レーンの障害注入ジョブは、同じレーンの `conformance` ジョブより
  十分早く終わります（adb では 3 分と 6 分、xcuitest では 9 分と 22 分）。そのため、どちらもクリティカル
  パスには乗りません。branch protection の変更も不要でした。ruleset が固定しているのは `E2E (iOS)` と
  `E2E (android)` の集約ジョブなので、必須化は `needs:` の編集で済みます。
- 回帰の網は `tests/test_e2e_gate_needs.py` で、3 つの層に分かれています。最初の 2 つだけでは足りないと
  判明したからです。
  - 1 つ目の層は、各ゲートがどのジョブに依存するかを固定します。
  - 2 つ目の層は、そのすべてのジョブについて、結果が読まれていることを固定します。
  - 3 つ目の層は、verdict のスクリプトを `bash` で依存ジョブごと結果ごとに実行します。それぞれの結果が
    本当にチェックを赤にすることを固定します。
- 2 つ目の層だけでも、書いている途中で実在する欠陥を見つけました。`E2E (iOS)` は `changes` を `needs:` に
  挙げながら、`needs.changes.result` を一度も読んでいませんでした。`changes` が落ちると、すべての macOS
  ジョブが*依存の失敗*として skip されます。必須チェックはそれをパススキップの合格と数えて、何も実行しない
  まま green を報告してしまいます。Android と web はどちらもガードしていて、iOS だけが抜けていました。
  修正と必須化は同じジョブの数行を編集するので、同じ変更で直しました。
- 外側の 2 つの層は、レビューのパスが 2 つの穴を示したために足しました。
  - `needs:` とステップの読み取りが一致していても、両方から同じジョブを落とせば一致は保たれます。同じ欠陥
    が復活します。
  - ループの条件を、どのジョブも報告しない値に書き換えると、永久に green の必須チェックが残ります。
- 3 つの層は、すべてミューテーションで確認しました。次の操作は、それぞれ赤くなります。`changes` をすべての
  箇所から削除する操作、ゲートを狭める操作、`cancelled` の判定を落とす操作、verdict を無効化する操作です。
- この網そのものをレビューした結果、3 つの層に加えて、さらに 2 つを固定しました。編集ではなく追加によって
  生まれる経路のためです。
  - このファイルが集約ジョブを一度も読まないレーン。
  - 赤を無効にする `continue-on-error`。ゲート対象のジョブに置かれると、そのジョブが得ていない `success` を
    `needs:` に渡します。集約ジョブ自身の判定ステップに置かれると、スクリプトが非ゼロで終了しても、必須
    チェックを green と報告します。

## 参考

- [BE-0254 — idb と adb 向けに共有の CoordinateTreeDriver 基底クラスを抽出する](../BE-0254-coordinate-tree-driver-base/BE-0254-coordinate-tree-driver-base-ja.md)
- [BE-0207 — XCUITest ランナーチャネルを一過性のタイムアウトに強くする](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md)
- [BE-0287 — 多点タッチ操作下での XCUITest runner チャネルの耐障害性](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md)
- [BE-0289 — XCUITest チャネルが失敗する前に古い操作ハンドルを再解決する](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve-ja.md)
- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- `bajutsu/drivers/coordinate_tree.py`（`_read_settled_tree`、`_is_transient_empty`、`_empty_backoff`）、
  `tests/test_coordinate_tree.py`、
  `bajutsu/drivers/xcuitest.py`（`_with_retry`、`_with_crash_recovery`、`XcuitestRunnerCrashError`）、
  `tests/test_xcuitest.py`、`tests/driver_conformance.py`

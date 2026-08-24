[English](BE-0305-driver-resilience-fault-injection.md) · **日本語**

# BE-0305 — ドライバ耐障害経路への実機障害注入カバレッジ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0305](BE-0305-driver-resilience-fault-injection-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0305") |
| 実装 PR | [#1461](https://github.com/bajutsu-e2e/bajutsu/pull/1461), [#PRNUM](https://github.com/bajutsu-e2e/bajutsu/pull/PRNUM) |
| トピック | ドライバとバックエンドのアーキテクチャ |
| 関連 | [BE-0254](../BE-0254-coordinate-tree-driver-base/BE-0254-coordinate-tree-driver-base-ja.md), [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md), [BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve-ja.md), [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md) |
<!-- /BE-METADATA -->

## はじめに

実デバイスの障害条件を生き延びるために存在する耐障害機構が2つあり、どちらも CI で実際の障害に
よって発火することはありません。`CoordinateTreeDriver` の transient-empty リトライ
（[BE-0254](../BE-0254-coordinate-tree-driver-base/BE-0254-coordinate-tree-driver-base-ja.md)）は、
idb と adb の遷移途中でほぼ空になる要素ツリーのために存在しますが、そのテストは要素数の合成
シーケンス(`[3, 1, 3]`)を組み立て、backoff をゼロ化しています。XCUITest チャネルの
crash-recovery とリトライ経路
（[BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md)、
[BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md)）は、
常駐ランナーが強制終了またはフリーズしたときのために存在しますが、そのテストは入れ子の
クロージャから合成例外を送出しているだけです。実際に実機で動く conformance suite は、どちらの経路も
踏むことがありません。その画面はあらかじめシードされ準備完了を待ってから使われるため
transient-empty の分岐には到達せず、また操作の途中でランナーを意図的に強制終了させるジョブも
ありません。本項目は、両方に対する実際の障害注入(fault injection)のカバレッジを追加します。

## 動機

合成された要素数シーケンスや送出された例外は、リトライ/復旧の*コード経路*が発火時に実行される
ことを証明します。これは制御フローに対する実質的で有用なカバレッジです。しかし、耐障害機構が
本来対象とする実際の条件を本当に生き延びられるかどうかまでは証明しません。対象となる条件とは、
idb/uiautomator の遷移途中でほぼ空になるレスポンスの実際の形状とタイミングです。あるいは強制終了
した XCUITest 常駐ランナーの実際のソケットレベルの失敗モード(正常な RST、ハングした接続、部分的
な書き込み)と、実際の再起動レイテンシです。実際の検出ヒューリスティクス
(`_is_transient_empty` の閾値、またはクラッシュ分類器の例外マッチング)を壊す回帰があっても、
合成フィクスチャによるテストは green のままであり、そのまま出荷されてしまいます。CI のどこにも、
耐障害機構が生き延びるために存在する条件そのものを再現するものがないからです。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **transient-empty の実際の障害注入(idb/adb)**：実機上の conformance または E2E に、実際に
  ほぼ空の中間ツリーを生む画面遷移を意図的に駆動するケース(あるいはその条件を再現する人為的な
  競合を加えたケース)を追加し、`CoordinateTreeDriver` のリトライが誤った「要素が見つからない」
  を出さずに回復することを検証します。
- **crash-recovery の実際の障害注入(XCUITest)**：シナリオの途中で常駐 BajutsuRunner プロセスを
  意図的に強制終了またはフリーズさせる実機上のケースを追加し、ドライバの crash-recovery 経路が
  それを再起動し、シナリオが回復するか、無関係なタイムアウトではなく正しい
  `XcuitestRunnerCrashError` 由来の診断で失敗することを検証します。
- **両方ともまずゲート対象外のシグナルとして着地させる**：障害注入レーンは、既存の conformance
  suite よりも本質的にフレーキーになるリスクが高くなります。
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
  の前例に従い、それぞれ安定を確認してから必須化します。
- **既存の合成フィクスチャによるユニットテストはそのまま残す**：制御フローロジック自体に対する
  高速で決定的な検証として引き続き適切だからです。本項目はその下に実際の条件による層を追加
  するのであって、置き換えるのではありません。

## 検討した代替案

- **制御フローのロジックがユニットテストされていることを根拠に、合成フィクスチャを信頼する**：
  作り上げた要素数シーケンスや送出された例外に対して制御フローが正しいことは、検出ヒューリス
  ティクスが対象とする実際の条件で本当に発火するかどうかについては何も語りません。それこそが
  耐障害機構が保証するために存在する性質です。
- **実運用で耐障害機構が失敗するまで実際のカバレッジ追加を待つ**：リトライ/復旧の経路が現場で
  静かに失敗することこそ、CI での障害注入がユーザーより先に捕まえるべき結末です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] idb/adb 向けに、実際の transient-empty 障害注入をまずゲート対象外で追加する。
- [x] XCUITest 向けに、実際の crash-recovery 障害注入をまずゲート対象外で追加する。
- [x] それぞれ安定後に必須化する。
- [x] 既存の合成フィクスチャによるユニットテストを、高速で決定的な制御フロー検証として残す。

ログ:

- [#1461](https://github.com/bajutsu-e2e/bajutsu/pull/1461) 両方のレーンをゲート対象外のシグナルとして着地させました。**adb 側**：`fault-injection
  (adb)` はエミュレータのディスプレイをスリープさせます。この操作で実際の読み取り元が要素 0 を
  返すことを実測しました（判定の下限は 2 要素で、別のものと見誤るようなエラーは出ません）。リトライは
  そこを乗り越え、既知の要素が解決できます。2 つ目のケースはディスプレイを落としたまま保って予算を
  使い切らせ、その結末が明示的な `ElementNotFound` であることを固定します。どちらもミューテーションで
  確認しました。`_READY_MIN` をゼロにすると（動機の節が述べるとおりに検出ヒューリスティクスを壊す操作
  です）両方が赤くなります。組み立てた要素数シーケンスでは、これができません。**XCUITest 側**：
  `fault-injection (xcuitest)` はランナー自身のホストプロセスにシグナルを送ります。`SIGSTOP` は
  ソケットを accept できる状態に保ったまま応答を止めるので、短い凍結は BE-0207 のリトライが吸収し
  （実測 15.5 秒、1 回目の再試行で回復）、リトライ予算を超える凍結は BE-0287 の crash-recovery が
  乗り越えます（実測 46.6 秒、ログは「recovered from a mid-run crash; re-issuing」）。`SIGKILL` では、
  実行中のランナー障害を名指しする `BackendCrashError` で読み取りが終わります。どちらのレーンも
  待ち時間を推測しません。各ケースは、検証対象の層に到達したというドライバ自身のログ記録
  （`tests/fault_injection.py`）を合図に障害を解除します。そのため、これまで無言だった
  `CoordinateTreeDriver` のリトライにログ行を追加し、高速スイートのテストも付けました。合成
  フィクスチャは、高速な制御フロー検証としてそのまま残します。作業中に実測して記録に値することが
  1 つあります。強制終了したランナーは crash-recovery のプロセス終了による早期失敗の経路を通りません。
  `_runner_alive` が見るのは `xcodebuild` の親プロセスで、これは強制終了したホストプロセスより長く
  生き残るからです。したがって診断は正しいものの、ただちにではなく 60 秒の復旧ウィンドウを
  使い切ったあとに届きます。本項目ではそのままにしました（判定は正しいままで、明確に失敗します）。より厳密な生存
  確認は別項目にします。
- [#PRNUM](https://github.com/bajutsu-e2e/bajutsu/pull/PRNUM) 両方のレーンをそれぞれのレーンの必須集約
  チェックへ入れ、本項目を完了しました。必須化の条件は「安定を確認してから」でしたが、それを数えて
  決めようとした最初の試みは、教訓の残る形で誤りました。`gh run list --limit 150` が返すのは直近の
  150 run だけで、このリポジトリの run 密度では 4 日程度しか遡りません。そのため
  `--jq 'select(.createdAt > "2026-08-04")'` というフィルタは 150 件すべてに一致し、何も絞って
  いませんでした。そしてその 4 日間は、たまたま状況が変わった後の綺麗な側に丸ごと収まっていました。
  だから両レーンは無傷に見えたのです。Actions API をページングして 2026-08-04 から 2026-08-22 までの
  全 run を数え直すと、記録は `fault-injection (xcuitest)` が 116 回成功に対して**78 回失敗**、
  `fault-injection (adb)` が 378 回に対して 4 回失敗でした。
- したがって必須化を正当化するのは、失敗率の低さではなく、原因が判明したことです。iOS の 78 回の失敗は
  すべて 2026-08-13T12:58Z より前に起きています。この時刻に
  [#1609](https://github.com/bajutsu-e2e/bajutsu/pull/1609) が
  `fix(xcuitest): keep the runner HTTP server alive through abandoned connections` をマージしました。
  最後の失敗は、その 30 分前です。このレーンは runner を `SIGSTOP` で止めたまま保ちます。相手が去った
  接続は、まさにそこから生まれます。したがってこのレーンは、健全な runner を駆動するどのスイートよりも
  高い頻度でその SIGPIPE を踏んでいました。レーンが runner チャネルの実際の不具合を見つけていたわけで、
  それこそがレーンの存在理由です。この修正の後、レーンは 64 回実行して 1 回も失敗していません。同じ期間の
  `conformance (xcuitest)` は 58 回成功して 2 回失敗しています。`fault-injection (adb)` は同じ期間に
  73 回実行して失敗は 1 回で、その 1 回はレーンが検証している内容ではなく、エミュレータが起動しなかった
  こと（`Unable to connect to adb daemon on port: 5037`）によるものです。同じ期間の
  `conformance (adb)` は 67 回中 4 回失敗しています。両レーンはいま、ずっとゲートに乗っているスイートと
  同等か、それよりよい水準にあります。必須化が拠るのはこの比較です。
- 必須化はゲートの実時間を増やしません。各レーンの障害注入ジョブは、同じレーンの `conformance` ジョブ
  より十分早く終わります（adb では 3 分と 6 分、xcuitest では 9 分と 22 分）。branch protection の変更も
  不要でした。ruleset が固定しているのは `E2E (iOS)` と `E2E (android)` の集約ジョブなので、必須化は
  `needs:` の編集で済みます。
- 回帰の網は `tests/test_e2e_gate_needs.py` で、3 つの層に分かれています。最初の 2 つだけでは足りないと
  判明したからです。各ゲートがどのジョブに依存するかを固定し、そのすべてについて結果が読まれていることを
  固定します。そのうえで verdict のスクリプトを `bash` で依存ジョブごと結果ごとに実行し、それぞれの結果が
  本当にチェックを赤にすることを固定します。真ん中の層だけでも、書いている途中で実在する欠陥を見つけました。
  `E2E (iOS)` は `changes` を `needs:` に挙げながら `needs.changes.result` を一度も読んでおらず、
  `changes` が落ちるとすべての macOS ジョブが*依存の失敗*として skip され、必須チェックはそれを
  パススキップの合格と数えて、何も実行しないまま green を報告してしまいます。Android と web はどちらも
  ガードしていて、iOS だけが抜けていました。修正と必須化は同じジョブの数行を編集するので、同じ変更で
  直しました。外側の 2 つの層は、レビューのパスが次の 2 点を示したために足しました。`needs:` と
  ステップの読み取りが一致していても、両方から同じジョブを落とせば一致は保たれ、同じ欠陥が復活します。
  そして、ループの条件をどのジョブも報告しない値に書き換えれば、永久に green の必須チェックが残ります。
  3 つの層はすべてミューテーションで確認しました。`changes` をすべての箇所から削除する操作、ゲートを
  狭める操作、`cancelled` の判定を落とす操作、verdict を無効化する操作は、それぞれ赤くなります。

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

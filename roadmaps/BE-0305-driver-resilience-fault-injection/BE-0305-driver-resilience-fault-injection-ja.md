[English](BE-0305-driver-resilience-fault-injection.md) · **日本語**

# BE-0305 — ドライバ耐障害経路への実機障害注入カバレッジ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0305](BE-0305-driver-resilience-fault-injection-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0305") |
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

- [ ] idb/adb 向けに、実際の transient-empty 障害注入をまずゲート対象外で追加する。
- [ ] XCUITest 向けに、実際の crash-recovery 障害注入をまずゲート対象外で追加する。
- [ ] それぞれ安定後に必須化する。
- [ ] 既存の合成フィクスチャによるユニットテストを、高速で決定的な制御フロー検証として残す。

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

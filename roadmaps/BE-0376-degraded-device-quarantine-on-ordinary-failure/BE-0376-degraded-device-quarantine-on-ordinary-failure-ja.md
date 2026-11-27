[English](BE-0376-degraded-device-quarantine-on-ordinary-failure.md) · **日本語**

# BE-0376 — キャプチャが止まったデバイスを、シナリオ自身のアサーションで失敗した場合にも隔離する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0376](BE-0376-degraded-device-quarantine-on-ordinary-failure-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **却下** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0376") |
| トピック | Platform support |
| 関連 | [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement-ja.md)、[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md)、[BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md) |
<!-- /BE-METADATA -->

## はじめに

> **却下。隔離は CI では発火せず、ローカルでの効果も費用に見合いません。**
> `ios-e2e.yml` のジョブはすべて具体的な `--udid` を固定し、交換の段は `--udid booted` を要求します。予定している変更のどれを入れても、この設計は発火しません。そのため保留ではなく却下とします。CI で効かせるには、要求をプール経由で回す別の設計が必要です。本項目のうち役に立つ部分は、赤い結果に停止の合図を記録する別項目へ移します。

シナリオが待機のタイムアウトで止まる原因が、デバイスの描画の停止にある場合があります。各リースは、動画の録画が開始を確認できたかどうかを記録します。本項目では、この事実を **停止の合図** と呼びます。停止の合図を入れたのは [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement-ja.md) で、respawn による再試行がこの合図を読んで交換用のデバイスを要求します。

本提案は、シナリオが自身のアサーションで赤になったあとにも同じ合図を読むものでした。合図が立っていれば、パイプラインが後続のシナリオのために交換用のデバイスを要求します。赤い判定はそのまま残します。

## 動機

本項目のきっかけは、プルリクエスト [#1556](https://github.com/bajutsu-e2e/bajutsu/pull/1556) の `actuation (xcuitest)` ジョブです。最初のシナリオは `GET /screenshot` のハングに2度当たり、respawn 後の試行は待機のタイムアウトを報告しました。ジョブのログには10本すべてのシナリオで動画の開始の停止が記録され、2本目以降もすべて同じデバイスの上で走りました。

同じジョブは、本提案の限界も示しています。このレーンは udid を固定して走ります。`device_replacement_supported` は、固定した実行に対して偽を返します。そのジョブでは、本提案も BE-0354 の段も、デバイスを交換できませんでした。

## 詳細設計

設計は次の4つの部分からなっていました。

| 部分 | 内容 |
|---|---|
| 合図の読み取り | `_run_on_lease` が赤い結果を返したら、`run_one` が `video_start_stalled()` を読む |
| 交換の要求 | 合図が立ち、`can_replace` が成り立てば `request_device_replacement()` を呼ぶ |
| スパイク | 要求が次のシナリオまで残るかを確かめ、1回の実行で作れる台数の上限を決める |
| テスト | 赤い判定がバイト単位で変わらないことを固定する |

この設計は、次の3つの理由で却下しました。

| 理由 | 内容 |
|---|---|
| CI では効かない | `ios-e2e.yml` のジョブはすべて具体的な `--udid` を渡す。`device_replacement_supported` は `--udid booted` を要求するので、固定した実行ではデバイスを交換しない |
| 届く範囲が狭い | `--udid booted` の実行は、1台のデバイスを1つの worker で使う。開発者の Mac がこの形で、そこでは開発者が止まったデバイスに気づいて再起動できる |
| 費用が小さくない | スパイクと台数の上限が、狭い効果に対して作業と危険を増やす |

## 検討した代替案

### 隔離をせず、赤い結果に停止の合図を記録する

この部分は別項目として残しました。結果に添える注記は、固定した CI のジョブを含むすべての実行で役に立ちます。注記は判定を変えません。

### CI のレーンの固定をやめる、あるいは `device_replacement_supported` を `booted` の外へ広げる

どちらの方法を取っても、交換が CI まで届きます。対象のプールを広げるなら、要求をプール経由で回す設計が必要です。どちらも独立した項目が必要で、そのどちらかが入った時点で、隔離の提案をあらためて検討できます。

### 赤いシナリオを交換用のデバイスで再実行する

インフラ側の合図を根拠に、赤を緑へ変えることになります。[`DESIGN.md`](../../DESIGN.md) の決定性の原則が、この道を禁じています。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解(作業の単位ごとに 1 つ)に対応し、ログには変更内容と時期(古い順)を PR へのリンクと
> ともに記録します。

作業を始める前に却下しました。

## 参考

| 参照先 | 内容 |
|---|---|
| [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement-ja.md) | リースの停止の合図と、それを読む respawn による再試行 |
| [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md) | respawn による再試行でのデバイス回復 |
| [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md) | 交換で再利用するはずだったデバイス作成のコード |
| [#1556 の `actuation (xcuitest)` ジョブ](https://github.com/bajutsu-e2e/bajutsu/actions/runs/31559685322/job/93999696836) | 記録された発生事例 |

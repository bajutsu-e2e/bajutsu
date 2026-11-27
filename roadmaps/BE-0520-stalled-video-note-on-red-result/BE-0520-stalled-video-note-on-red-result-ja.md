[English](BE-0520-stalled-video-note-on-red-result.md) · **日本語**

# BE-0520 — 成功しなかったシナリオの結果に、動画の開始が確認できなかったことを記録する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0520](BE-0520-stalled-video-note-on-red-result-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0520") |
| トピック | Platform support |
| 関連 | [BE-0376](../BE-0376-degraded-device-quarantine-on-ordinary-failure/BE-0376-degraded-device-quarantine-on-ordinary-failure-ja.md)、[BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement-ja.md)、[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md)、[BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md) |
<!-- /BE-METADATA -->

## はじめに

シナリオが待機のタイムアウトで止まる原因が、アプリではなくデバイスの描画の停止にある場合があります。実行の側は、その状態の手がかりをすでに記録しています。各リースは、動画の録画が開始を確認できたかどうかを記録します。本項目では、この事実を **停止の合図** と呼びます。

停止の合図を入れたのは [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement-ja.md) です。バックエンドが応答しなくなったとき、respawn による再試行がこの合図を読んで対処を選びます。

ところが、シナリオ自身の結果には停止の合図が出てきません。赤い結果を読む人に見えるのは、待機のタイムアウトだけです。動画の警告は別のログ行にあり、結果の上には両者を結ぶものがありません。

本提案は、成功しなかったすべてのシナリオの結果に、停止の合図を記録します。この記録は証跡であり、判定ではありません。何も再実行せず、成否の答えも変えません。

本項目は [BE-0376](../BE-0376-degraded-device-quarantine-on-ordinary-failure/BE-0376-degraded-device-quarantine-on-ordinary-failure-ja.md) から切り出したものです。BE-0376 は、そうした結果のあとでデバイスを隔離し、交換用のデバイスを要求することも提案していました。隔離の部分は却下となり、理由は「検討した代替案」に書きます。

## 動機

プルリクエスト [#1556](https://github.com/bajutsu-e2e/bajutsu/pull/1556) の `actuation (xcuitest)` ジョブに、その損失が表れています。最初のシナリオは、`GET /screenshot` のハングに2度当たりました。パイプラインはバックエンドを respawn し、2回目の試行は最後まで走って待機のタイムアウトを報告しました。

```
step 1 (for_each): step 3 (wait): wait timeout: for {'id': ['horse.title', 'horse_title']} (20.0s)
```

健全なマシンなら、この push 遷移は1秒もかかりません。同じジョブのログには、その理由が10本のシナリオすべてについて1回ずつ残っています。

```
recordVideo produced no new bytes in runs/.../scenario.mp4 within 20.0s
```

キャプチャのサービスが何も出力していなかったので、デバイスは描画もしていませんでした。それでも結果は `horse.title` を名指し、アプリかシナリオの不具合に見えます。

同じ停止は、[BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics-ja.md) の停止診断にもすでに届いています。`ios-e2e.yml` は、この診断をすべてのジョブで有効にしています（`BAJUTSU_STALL_DIAGNOSTICS`）。ただし、そのキャプチャはシナリオを名指しません。また、1つのプロセスでは理由ごとに2回で止まるので、10個のシナリオを走らせるジョブでは、後半の停止は何も残しません。そのため読む人は、今もジョブのログから動画の警告を探し、シナリオと手作業で突き合わせることになります。

停止の合図は、デバイスの不調を証明するものではありません。この CI のランナーでは、成功するシナリオでも動画の警告が出ます。結果に添える注記は、読む人が判断するための事実を示すにとどまります。原因を断定せず、判定を左右することもありません。

この変更のあとは、結果だけを見て停止に気づけます。リースで動画の開始が確認できなかった赤いシナリオには、そのことを示す注記が付きます。注記は実行のマニフェストとレポートに載ります。レポートでは、成功しなかったステップの隣に表示されます。

## 詳細設計

### 合図を読んでいる現在の場所

`Lease.video_start_stalled()` は、1つのリースの停止の合図を報告します。`bajutsu/common/runner/pool.py` のプールは、このフラグをリースごとに保持しています。そのため、リースをプールに返したあとでも値は有効です。

`bajutsu/common/runner/pipeline.py` の `run_one` がこのフラグを読むのは1箇所だけです。その場所は `except BackendCrashError` の分岐で、フラグは強制 erase と交換用デバイスのどちらを使うかを決めます。`_run_on_lease` から通常に戻る経路は、フラグを読まずに `RunResult` を返します。

### 結果に添える注記

`run_one` は、結果を返す前にフラグを読みます。結果が成功ではなく、かつフラグが立っているときは、結果に注記を1つ加えます。注記の内容は「動画の開始が確認できず、デバイスが描画していなかった可能性がある」です。

シナリオが赤で終わる道は2つあり、どちらも対象にします。

| 終わり方 | 読むリース |
|---|---|
| `_run_on_lease` が赤い結果を返した | シナリオが走ったリース |
| respawn の繰り返しが最後の試行で打ち切られた | 最後に応答しなくなったリース |

成功した結果には注記を付けません。成功したシナリオの下でも合図は立つので、付けても雑音が増えるだけで何も説明しないからです。

注記は、判定が読まない `RunResult` のフィールドに置きます。候補は次の2つで、実装の変更でどちらかを選び、理由を書きます。

| 候補 | 得失 |
|---|---|
| `channel_notes` を使い回す | フィールドは増えないが、ドライバーの読み取りチャネル用の一覧である |
| 新しいフィールドを足す | 意味ははっきりするが、マニフェストとレポートの読み込みの両方に手が入る |

どちらを選んでも、マニフェストとレポートは注記を運びます。レポートは、注記を赤いステップの隣に表示します。

BE-0361 の停止診断は、`BAJUTSU_STALL_DIAGNOSTICS` が指すディレクトリに、同じ停止のキャプチャを残していることがあります。診断が有効なときに注記がそのディレクトリを指すかどうかは、実装の変更で検討します。理由ごとの上限があるので、診断が有効でもキャプチャがないことはあります。

### 遵守する原則

**AI は判定しない。** 決定的なコードが、動画の録画について記録された事実を読みます。注記を作る経路と読む経路のどちらにも、モデルの呼び出しは入りません。

**決定性を最優先する。** 注記は判定を変えず、何も再実行しません。`ok` と `failure` の値は、注記の有無にかかわらずバイト単位で同一です。テストがこの性質を固定します。

**アプリに依存しない。** 確認が読むのはパイプライン自身のリース単位の合図で、アプリごとの分岐はありません。停止を報告しないリースのバックエンドには、注記は付きません。

### 作業分解

以下の作業の単位は、相互に排他的で網羅的（Mutually Exclusive and Collectively Exhaustive、MECE）です。

**作業 1、注記。** `run_one` の赤い出口の両方で `video_start_stalled()` を読み、結果に注記を加えます。フィールドを選び、マニフェストとレポートまで注記を通します。

**作業 2、テスト。** パイプラインのテストを分岐ごとに用意します。対象は次の4つです。

| 状況 | 期待 |
|---|---|
| 合図が立った赤いシナリオ | 注記あり |
| 合図が立った成功シナリオ | 注記なし |
| 合図のない赤いシナリオ | 注記なし |
| 注記の有無による違い | `ok` と `failure` は同一 |

**作業 3、ドキュメント。** [`docs/run-loop.md`](../../docs/run-loop.md) とその `docs/ja/` 側を更新します。このページは現在、停止の合図を respawn による再試行の入力としてだけ書いています。更新では、赤い結果への注記を加えます。BE-0113 の規範に従い、作業 1 と同じ変更で入れます。

## 検討した代替案

### 赤い結果のあとでデバイスを隔離し、交換を要求する

[BE-0376](../BE-0376-degraded-device-quarantine-on-ordinary-failure/BE-0376-degraded-device-quarantine-on-ordinary-failure-ja.md) が提案した範囲です。赤いシナリオのリースで合図が立っていたら、パイプラインが `request_device_replacement()` を呼ぶ設計でした。この範囲は、次の3つの理由で却下しました。

| 理由 | 内容 |
|---|---|
| CI では効かない | `ios-e2e.yml` のジョブはすべて具体的な `--udid` を渡す。`device_replacement_supported` は `--udid booted` を要求するので、固定した実行ではデバイスを交換しない。#1556 のジョブも固定した実行である |
| 届く範囲が狭い | `--udid booted` の実行は、1台のデバイスを1つの worker で使う。開発者の Mac がこの形で、そこでは開発者が止まったデバイスに気づいて再起動できる |
| 費用が小さくない | 交換の要求が次のシナリオまで残ることを、スパイクで確かめる必要がある。1回の実行で作れるデバイスの台数にも上限が要る |

CI で効かせる方法の問いは、開いたままです。レーンを固定せずに走らせる方法があります。`device_replacement_supported` を `booted` の外へ広げ、要求をプール経由で回す方法もあります。どちらも独立した項目が必要で、そのどちらかが入った時点で、隔離の提案をあらためて検討できます。

### 赤いシナリオを交換用のデバイスで再実行する

インフラ側の合図を根拠に、赤を緑へ変えることになります。[`DESIGN.md`](../../DESIGN.md) の決定性の原則が、この道を禁じています。再実行は、ツールが暴くべき不安定なアプリをその場で覆い隠すことにもなります。

### 待機のタイムアウトを延ばし、遅いデバイスでも成功させる

1秒もかからない遷移に対して、20秒の待機はすでに十分な長さです。さらに延ばしても、得られるのは情報ではなく沈黙です。本項目の注記が示そうとしている劣化も隠れてしまいます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解(作業の単位ごとに 1 つ)に対応し、ログには変更内容と時期(古い順)を PR へのリンクと
> ともに記録します。

- [ ] Unit 1 — 赤い結果への注記と、マニフェストおよびレポートへの反映
- [ ] Unit 2 — テスト(判定への中立性の回帰テストを含む)
- [ ] Unit 3 — ドキュメント(`run-loop.md` とその `ja` 側)

## 参考

| 参照先 | 内容 |
|---|---|
| [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement-ja.md) | リースの停止の合図と、それを読む respawn による再試行 |
| [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics-ja.md) | 同じきっかけがすでに届いている、iOS のプロセス内の停止キャプチャ |
| [BE-0367](../BE-0367-android-ci-emulator-diagnostics/BE-0367-android-ci-emulator-diagnostics-ja.md) | Android での同様の停止キャプチャ |
| [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md) | respawn による再試行でのデバイス回復 |
| [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md) | 交換で再利用するはずだったデバイス作成のコード |
| [`pipeline.py`](../../bajutsu/common/runner/pipeline.py) | 確認を置く `run_one` |
| [`stall_diagnostics.py`](../../bajutsu/common/stall_diagnostics.py) | 停止キャプチャと、理由ごとに2回の上限 |
| [`pool.py`](../../bajutsu/common/runner/pool.py) | `Lease.video_start_stalled()` とリースごとのフラグ |
| [`run_result.py`](../../bajutsu/common/orchestrator/types/run_result.py) | 注記を運ぶ `RunResult` |
| [#1556 の `actuation (xcuitest)` ジョブ](https://github.com/bajutsu-e2e/bajutsu/actions/runs/31559685322/job/93999696836) | 記録された発生事例 |

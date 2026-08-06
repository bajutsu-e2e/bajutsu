[English](BE-XXXX-android-conformance-evidence-capture.md) · **日本語**

# BE-XXXX — Android のオンデバイス conformance・fault-injection スイートに、シナリオ駆動のCIジョブがすでに得ている動画とdeviceLogのエビデンスを持たせる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-android-conformance-evidence-capture-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | TBD（PR を開いた時点で記入します。BE 作成 PR のため、ID と PR は本セッションでは作成しません） |
| トピック | プラットフォーム対応 |
<!-- /BE-METADATA -->

## はじめに

`bajutsu run`でシナリオを実行するAndroidのCIジョブは、いずれも`video`と`deviceLog`のエビデンスを
取得します。ところが、オンデバイスの`conformance (adb)`と`fault-injection (adb)`の2ジョブはpytestから
adbドライバーを直接操作しており、この取得を一切受け継いでいません。どちらのジョブで失敗が起きても、
診断できるアーティファクトが残らないということです。本項目は、パイプラインがすでに提供している
このインターバル型エビデンス（テスト1件につき1本の画面録画と1本の`logcat`ストリーム）を両スイートに
追加します。録画とログは、それを生んだテストが失敗したときだけ残します。これにより、どちらのジョブで
失敗しても、シナリオ駆動の失敗がすでに得ているのと同じ動画とdevice.logが手に入ります。

## 動機

このギャップは仮説ではなく、実際に観測されています。プルリクエスト
[#1520](https://github.com/bajutsu-e2e/bajutsu/pull/1520)（差分はシナリオYAMLへの`deviceLog`取得の
追加のみ）で、`conformance (adb)`の`test_a_read_postdates_a_content_moving_gesture`が
`bajutsu.drivers.base.ElementNotFound: scroll: {'id': 'conformance.scroll.row.19'} not found; the
region did not change ... (end of content)`で失敗しました。これはまさに、画面録画さえあれば一目で
判断がつくはずのスクロールタイミングのずれです。このジョブ自身のログは
`No files were found with the provided path: runs/. No artifacts will be uploaded.`で終わっており、
ワークフローの「Upload run artifacts」ステップ自体はすでに存在し、シナリオ駆動のAndroidジョブがいずれも
書き出す`runs/`を対象にしているにもかかわらず、両方のオンデバイスpytestスイートはそこに何も書き出して
いません。

このエビデンス欠落は、取得コード自体の見落としではなく、両スイートがデバイスへ到達する経路そのものの
結果です。`bajutsu/evidence/core.py`の`FileSink`は、シナリオの`capture:`リストに基づいて
`video`/`deviceLog`のインターバルを開始・停止しますが、これは`bajutsu run`パイプライン
（`bajutsu/runner/pool.py`）の内部で完結しています。一方、
`tests/test_driver_conformance_ondevice_android.py`と`tests/test_fault_injection_ondevice_android.py`は、
モジュールスコープのpytestフィクスチャから`launch_driver`を直接呼び出します。これは意図的な設計であり、
すでに[BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery-ja.md)
が同じ形のpytestハーネスを持つiOS側のスイートに、同じ理由でインフラ障害からの復旧を持たせています。
パイプラインを経由しないことでドライバーレベルの契約テストが得られる一方、エビデンス取得を含め
パイプラインが無償で提供するものすべてを失う、という代償を払っています。

## 詳細設計

3つの単位に分け、それぞれ単独でランディングできるようにします。

### 単位1 — パイプライン自身のプリミティブの上に、テスト単位の取得フィクスチャを作る

各テストを`bajutsu.evidence.intervals.start_screenrecord`と`start_logcat`で包む共有pytestフィクス
チャを追加します。これらはシナリオパイプライン自身が呼んでいる関数そのものであり、もともとどんな
シナリオやYAMLにも依存しません。`demos/showcase/android/screenrecord.py`は、codegenレーンの
`connectedAndroidTest`向けに、`bajutsu run`の外から`start_screenrecord`をすでに直接呼んでいます。
単位1は、バックグラウンドで動かすスクリプトの代わりにpytestフィクスチャへ、同じ手法を適用するという
ものです。

取得の範囲は、モジュール単位ではなくテスト単位にします。`screenrecord`のデバイス側録画は、
`time_limit`を明示的に指定するかどうかにかかわらず約180秒で止まります。本項目のきっかけとなったジョブ
では、conformanceモジュールの18件のテストですでに177秒かかっています。モジュール全体を1本の録画で
覆おうとすると、テストが1件増えるだけで末尾から欠け始め、診断にまさに必要な部分を失います。テスト単位の
録画ならこの上限に対して十分な余裕があり、失敗の様子を長い録画から探し出す代わりに、そのテスト自身の
クリップから直接見つけられます。

### 単位2 — 失敗したテストのアーティファクトだけを残す

録画とdevice.logは、それを包んだテストが成功すれば破棄し、失敗したときだけ`runs/<lane>/<test-id>/`
以下に残します。これは`screenrecord.py`自身のMakefileターゲットがcodegenレーンに対してすでに適用している、
失敗時のみ残す方針と同じです。フィクスチャのteardownで成功と失敗を判定するには、フィクスチャ自身からは
見えないテスト自身のレポートが必要になるため、`pytest_runtest_makereport`フックでタグ付けします。これは
`tests/backend_crash_recovery.py`が、オンデバイスconformanceスイート向けにレポートを分類するために
（BE-0334）、同じフックポイントですでに使っている仕組みと同じものです。成功したテストの実行では何も
アップロードされないため、既存の「Upload run artifacts」ステップの`if-no-files-found: ignore`は
クリーンなスイートに対しては引き続き何もせず、実際の失敗が最初に起きたときだけ内容が入り始めます。

### 単位3 — 両スイートへautouseフィクスチャとして組み込む

このフィクスチャを`tests/test_driver_conformance_ondevice_android.py`と
`tests/test_fault_injection_ondevice_android.py`にautouseフィクスチャとして追加し、テストごとに個別に
指定しなくても両スイートの全ケースを対象にします。それぞれのスイートは自分自身のレーン名
（`conformance-adb`/`fault-injection-adb`）を渡すので、2つのジョブがアップロードするアーティファクトは
互いに独立したままで、同じパス上で衝突しません。

## 検討した代替案

- **両スイートを`bajutsu run`経由にして、`FileSink`をそのまま受け継ぐ。** 採用しません。同じ形の
  pytestハーネスについて、BE-0334がすでに記録している理由と同じです。両スイートはシナリオレベルではなく
  ドライバーレベルの契約テストであり、パイプラインの配管まで到達するようシナリオへ作り替えることは、
  ドライバーの検証のためではなく、エビデンス取得だけを目的に契約をねじ曲げることになります。
- **テスト単位ではなく、モジュール単位で1本の連続した録画を取得する。** 採用しません。`screenrecord`の
  約180秒という上限は、conformanceモジュール自身の実測所要時間とすでに数秒しか差がなく、モジュール全体を
  覆う録画では、テストが1件増えただけで診断にもっとも必要な末尾から欠け始めてしまいます。
- **結果にかかわらずアーティファクトを常に残す。** 採用しません。`screenrecord.py`のMakefileターゲットが
  成功時には録画を破棄しているという、このコードベース自身の前例と合いません。加えて、成功したテストには
  診断すべきものがありません。その録画を残したままにしても、テストが増えるたびにアップロードされる
  アーティファクトが大きくなるだけで、利点がありません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 単位1 — `intervals.start_screenrecord`/`start_logcat`の上に、テスト単位の取得フィクスチャを作る。
- [x] 単位2 — `pytest_runtest_makereport`でタグ付けし、失敗したテストの動画とdevice.logだけを残す。
- [x] 単位3 — `conformance (adb)`と`fault-injection (adb)`の両方に、autouseフィクスチャとして組み込む。

## 参考

- [PR #1520](https://github.com/bajutsu-e2e/bajutsu/pull/1520) — このジョブの`conformance (adb)`の
  実行で、診断用のアーティファクトが残らないまま失敗し、本項目のきっかけとなった変更です。
- [`bajutsu/evidence/intervals.py`](../../bajutsu/evidence/intervals.py) — 本項目がそのまま再利用する
  `start_screenrecord`と`start_logcat`です。
- [`demos/showcase/android/screenrecord.py`](../../demos/showcase/android/screenrecord.py) — これらの
  プリミティブを`bajutsu run`の外から呼ぶ既存の前例であり、成功時に録画を破棄する前例でもあります。
- [`tests/backend_crash_recovery.py`](../../tests/backend_crash_recovery.py) — 本項目の単位2が同じ
  やり方で再利用する、`pytest_runtest_makereport`フックを持つ、オンデバイススイート向けの姉妹プラグイン
  です。
- [`tests/test_driver_conformance_ondevice_android.py`](../../tests/test_driver_conformance_ondevice_android.py)
  と
  [`tests/test_fault_injection_ondevice_android.py`](../../tests/test_fault_injection_ondevice_android.py)
  — 本項目がこのフィクスチャを組み込む2つのスイートです。
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) — 
  `conformance (adb)`が検証しているドライバー適合性契約です。
- [BE-0270](../BE-0270-android-adb-driver-conformance/BE-0270-android-adb-driver-conformance-ja.md) — 
  本項目が計装するオンデバイスadb conformanceスイートです。
- [BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery-ja.md)
  — 同じ形のpytestハーネスが持つ同じギャップのiOS版で、インフラ障害からの復旧という観点で解消済みです。
  本項目は、Android側のスイートが同じハーネスと共有しているエビデンス取得のギャップを解消します。

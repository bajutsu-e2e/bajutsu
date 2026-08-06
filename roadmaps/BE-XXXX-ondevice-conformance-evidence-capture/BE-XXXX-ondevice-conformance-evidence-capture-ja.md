[English](BE-XXXX-ondevice-conformance-evidence-capture.md) · **日本語**

# BE-XXXX — オンデバイスのconformance・fault-injectionスイートに、シナリオ駆動のCIジョブがすでに得ている動画とdeviceLogのエビデンスを持たせる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-ondevice-conformance-evidence-capture-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | [#1523](https://github.com/bajutsu-e2e/bajutsu/pull/1523) |
| トピック | プラットフォーム対応 |
<!-- /BE-METADATA -->

## はじめに

`bajutsu run`でシナリオを実行するCIジョブは、いずれも`video`と`deviceLog`のエビデンスを取得します。
ところが、オンデバイスの`conformance (adb)`・`fault-injection (adb)`と、そのiOS版である
`conformance (xcuitest)`・`fault-injection (xcuitest)`は、いずれもpytestからバックエンドを直接
操作しています。この取得を一切受け継いでいません。4つのジョブのどれで失敗が起きても、診断できる
アーティファクトが残らないということです。本項目は、パイプラインがすでに提供しているこのインター
バル型エビデンス（テスト1件につき1本の画面録画と1本のデバイスログストリーム）を4つのスイートすべて
に追加します。録画とログは、それを生んだテストが失敗したときだけ残します。これにより、どのジョブで
失敗しても、シナリオ駆動の失敗がすでに得ているのと同じ動画とデバイスログが手に入ります。

## 動機

このギャップは仮説ではなく、実際に観測されています。プルリクエスト
[#1520](https://github.com/bajutsu-e2e/bajutsu/pull/1520)（差分はシナリオYAMLへの`deviceLog`取得の
追加のみ）で、`conformance (adb)`の`test_a_read_postdates_a_content_moving_gesture`が
`bajutsu.drivers.base.ElementNotFound: scroll: {'id': 'conformance.scroll.row.19'} not found; the
region did not change ... (end of content)`で失敗しました。これはまさに、画面録画さえあれば一目で
判断がつくはずのスクロールタイミングのずれです。このジョブ自身のログは
`No files were found with the provided path: runs/. No artifacts will be uploaded.`で終わっており、
ワークフローの「Upload run artifacts」ステップ自体はすでに存在し、シナリオ駆動のAndroidジョブがいずれも
書き出す`runs/`を対象にしているにもかかわらず、オンデバイスのpytestスイートはそこに何も書き出して
いません。

このエビデンス欠落は、取得コード自体の見落としではなく、これらのスイートがデバイスへ到達する経路
そのものの結果です。`bajutsu/evidence/core.py`の`FileSink`は、シナリオの`capture:`リストに基づいて
`video`/`deviceLog`のインターバルを開始・停止しますが、これは`bajutsu run`パイプライン
（`bajutsu/runner/pool.py`）の内部で完結しています。一方、
`tests/test_driver_conformance_ondevice_android.py`と`tests/test_fault_injection_ondevice_android.py`、
そのiOS版である`tests/test_driver_conformance_ondevice.py`と`tests/test_fault_injection_ondevice.py`は、
モジュールスコープのpytestフィクスチャから`launch_driver`を直接呼び出します。これは意図的な設計であり、
すでに[BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery-ja.md)
が、同じ形のpytestハーネスを持つiOS側のconformanceスイート自身に、同じ理由でインフラ障害からの復旧を
持たせています。パイプラインを経由しないことでドライバーレベルの契約テストが得られる一方、エビデンス
取得を含め、パイプラインが無償で提供するものすべてを失う、という代償を払っています。iOSのconformance
ジョブには、同じギャップがもう一段重なっています。そのCIワークフロー（`ios-e2e.yml`）はBE-0334の復旧
回数レポートをアップロードするステップこそ持っていますが、`runs/`向けの「Upload run artifacts」ステップ
自体がほかのどのジョブとも違って存在しません。

## 詳細設計

4つの単位に分け、それぞれ単独でランディングできるようにします。

### 単位1 — パイプライン自身のプリミティブの上に、テスト単位・バックエンド非依存の取得フィクスチャを作る

各テストを2つのインターバル起動関数で包む共有pytestフィクスチャを追加します。これらはシナリオ
パイプライン自身が呼んでいる関数そのものであり、もともとどんなシナリオやYAMLにも依存しません。adb向け
の`bajutsu.evidence.intervals.start_screenrecord`/`start_logcat`と、XCUITest向けの
`intervals.start_video`/`start_device_log`です。`demos/showcase/android/screenrecord.py`は、codegen
レーンの`connectedAndroidTest`向けに、`bajutsu run`の外から`start_screenrecord`をすでに直接呼んでい
ます。単位1は、バックグラウンドで動かすスクリプトの代わりにpytestフィクスチャへ、同じ手法を適用する
というものです。フィクスチャ自身はバックエンド非依存のままにします。2つの起動関数をどちらかのバック
エンドに固定せず明示的な引数として受け取るので、1つの実装で4つのスイートすべてに使えます。adb側の2
スイートが共有するサイズ・ビットレート・時間上限は、小さなヘルパー`android_screenrecord`へあらかじめ
束縛しておき、どちらの呼び出し側でも重複させません。

取得の範囲は、モジュール単位ではなくテスト単位にします。`screenrecord`のデバイス側録画は、
`time_limit`を明示的に指定するかどうかにかかわらず約180秒で止まります。本項目のきっかけとなったジョブ
では、conformanceモジュールの18件のテストですでに177秒かかっています。モジュール全体を1本の録画で
覆おうとすると、テストが1件増えるだけで末尾から欠け始め、診断にまさに必要な部分を失います。テスト単位の
録画ならこの上限に対して十分な余裕があり、失敗の様子を長い録画から探し出す代わりに、そのテスト自身の
クリップから直接見つけられます。

### 単位2 — 失敗したテストのアーティファクトだけを残す

録画とデバイスログは、それを包んだテストが成功すれば破棄し、失敗したときだけ`runs/<lane>/<test-id>/`
以下に残します。これは`screenrecord.py`自身のMakefileターゲットがcodegenレーンに対してすでに適用して
いる、失敗時のみ残す方針と同じです。フィクスチャのteardownで成功と失敗を判定するには、フィクスチャ
自身からは見えないテスト自身のレポートが必要になるため、`pytest_runtest_makereport`フックでタグ付け
します。これは`tests/backend_crash_recovery.py`が、オンデバイスconformanceスイート向けにレポートを
分類するために（BE-0334）、同じフックポイントですでに使っている仕組みと同じものです。成功したテストの
実行では何もアップロードされないため、既存の「Upload run artifacts」ステップの`if-no-files-found:
ignore`はクリーンなスイートに対しては引き続き何もせず、実際の失敗が最初に起きたときだけ内容が入り
始めます。

このタグは、真のときだけ立てて以後保持するのではなく、レポートが来るたびに書き換える必要があります。
iOS側のconformanceスイート自身が持つ`backend_crash_recovery`マーカー（BE-0334）は、インフラ障害からの
再試行のたびに、このフィクスチャを含む項目1件全体を`_initrequest()`で再実行し、同じ`pytest.Item`（した
がって同じstash）を試行のあいだ使い続けます。立てたら保持するだけのタグでは、後で復旧して成功した試行
でも「失敗」と読めてしまい、成功したテストには要らないエビデンスを誤って残してしまいます。しかもその
時点では、クラッシュした試行自身の録画は、それを引き継いだ試行によって同じファイルパス上でもう上書き
されています。レポートが来るたびに書き換えることで、`backend_crash_recovery`自身が最終的に公開する
試行の結果だけが残ります。

### 単位3 — 4つのスイートすべてにautouseフィクスチャとして組み込む

このフィクスチャを`tests/test_driver_conformance_ondevice_android.py`、
`tests/test_fault_injection_ondevice_android.py`、`tests/test_driver_conformance_ondevice.py`、
`tests/test_fault_injection_ondevice.py`の4つにautouseフィクスチャとして追加し、テストごとに個別に
指定しなくても4つのスイートすべての全ケースを対象にします。それぞれのスイートは自分自身のレーン名
（`conformance-adb`/`fault-injection-adb`/`conformance-xcuitest`/`fault-injection-xcuitest`）を渡す
ので、各ジョブがアップロードするアーティファクトは互いに独立したままで、同じパス上で衝突しません。

### 単位4 — iOSのconformanceジョブに「Upload run artifacts」ステップを追加する

`ios-e2e.yml`・`android-e2e.yml`の他のオンデバイスジョブがすでに持っている、`path: runs/`・
`if-no-files-found: ignore`の同じアップロードステップを、`conformance (xcuitest)`ジョブに追加します。
このジョブにはこれまで一切存在しませんでした。単位3のフィクスチャだけでは、このジョブの`runs/`に
書き出しても、ワークフローがそれを一切拾わないため、無駄になってしまいます。

## 検討した代替案

- **Androidだけを対応し、iOSは後続の項目に見送る。** 採用しません。`capture()`の明示的な
  `start_video`/`start_log`注入（単位1）は、すでにAndroid固有の前提を共有関数側に残さずバックエンド
  をまたいで一般化できているため、iOSを見送っても得られるものがないままレビューをもう1周増やすだけ
  になります。iOSのconformanceジョブのほうがむしろこの修正をより必要としていました。フィクスチャに
  加えて、そのワークフロー自体に「Upload run artifacts」ステップがまるごと欠けていたからです（単位4）。
- **両スイートを`bajutsu run`経由にして、`FileSink`をそのまま受け継ぐ。** 採用しません。同じ形の
  pytestハーネスについて、BE-0334がすでに記録している理由と同じです。これらはシナリオレベルではなく
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

- [x] 単位1 — `intervals.start_screenrecord`/`start_logcat`（adb）と`intervals.start_video`/
      `start_device_log`（XCUITest）の上に、テスト単位・バックエンド非依存の取得フィクスチャを作る。
- [x] 単位2 — 項目のstashを（立てたら保持するのではなく）レポートごとに書き換える
      `pytest_runtest_makereport`フックでタグ付けし、失敗したテストの動画とデバイスログだけを残す。
- [x] 単位3 — `conformance`/`fault-injection` × adb/XCUITestの4つのスイートすべてに、autouseフィクス
      チャとして組み込む。
- [x] 単位4 — `conformance (xcuitest)`に欠けていた「Upload run artifacts」ステップを追加する。

## 参考

- [PR #1520](https://github.com/bajutsu-e2e/bajutsu/pull/1520) — このジョブの`conformance (adb)`の
  実行で、診断用のアーティファクトが残らないまま失敗し、本項目のきっかけとなった変更です。
- [`bajutsu/evidence/intervals.py`](../../bajutsu/evidence/intervals.py) — adb向けの
  `start_screenrecord`/`start_logcat`と、XCUITest向けの`start_video`/`start_device_log`という、本項目
  がそのまま再利用するプリミティブです。
- [`demos/showcase/android/screenrecord.py`](../../demos/showcase/android/screenrecord.py) — これらの
  プリミティブを`bajutsu run`の外から呼ぶ既存の前例であり、成功時に録画を破棄する前例でもあります。
- [`tests/backend_crash_recovery.py`](../../tests/backend_crash_recovery.py) — 本項目の単位2が同じ
  やり方で再利用する、`pytest_runtest_makereport`フックを持つ、オンデバイススイート向けの姉妹プラグイン
  です。単位2のタグを保持ではなく書き換えにしている理由も、この項目のitem再利用の再試行にあります。
- [`tests/test_driver_conformance_ondevice_android.py`](../../tests/test_driver_conformance_ondevice_android.py)、
  [`tests/test_fault_injection_ondevice_android.py`](../../tests/test_fault_injection_ondevice_android.py)、
  [`tests/test_driver_conformance_ondevice.py`](../../tests/test_driver_conformance_ondevice.py)、
  [`tests/test_fault_injection_ondevice.py`](../../tests/test_fault_injection_ondevice.py) — 本項目が
  このフィクスチャを組み込む4つのスイートです。
- [`.github/workflows/ios-e2e.yml`](../../.github/workflows/ios-e2e.yml) — 単位4が`conformance
  (xcuitest)`ジョブに欠けていたアップロードステップを追加する場所です。
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) — どの
  `conformance`ジョブも検証しているドライバー適合性契約です。
- [BE-0270](../BE-0270-android-adb-driver-conformance/BE-0270-android-adb-driver-conformance-ja.md) —
  本項目が計装するオンデバイスadb conformanceスイートです。
- [BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery-ja.md)
  — iOSのconformanceスイートがすでに持っているインフラ障害からの復旧です。本項目の単位2は、同じpytest
  ハーネスの形を共有しているだけでなく、同じ項目を互いに再試行しあう形で直接絡み合っています。

[English](BE-0350-ondevice-conformance-evidence-capture.md) · **日本語**

# BE-0350 — オンデバイスのconformance・fault-injectionスイートに、シナリオ駆動のCIジョブがすでに得ている動画とdeviceLogのエビデンスを持たせる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0350](BE-0350-ondevice-conformance-evidence-capture-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0350") |
| 実装 PR | [#1523](https://github.com/bajutsu-e2e/bajutsu/pull/1523) |
| トピック | プラットフォーム対応 |
<!-- /BE-METADATA -->

## はじめに

`bajutsu run`でシナリオを実行するCIジョブは、いずれもそのシナリオの`video`と`deviceLog`のエビデンスを
取得します。オンデバイスの4つのジョブは取得しません。`conformance (adb)`、`fault-injection (adb)`、
およびそのiOS版である`conformance (xcuitest)`、`fault-injection (xcuitest)`です。これらはpytestから
バックエンドを直接操作しており、取得の仕組みを一切受け継いでいません。4つのどれで失敗が起きても、
診断に使えるアーティファクトが残りません。

本項目は、パイプラインがすでに提供しているインターバル型エビデンスを、4つのスイートすべてに追加します。
エビデンスとは、テスト1件につき1本の画面録画と1本のデバイスログストリームです。録画とログは、それを
生んだテストが失敗したときだけ残します。対象は各テスト自身のセットアップ以降に起きた失敗です。
シナリオ駆動の失敗が得ているのと同じ動画とデバイスログが、これらの失敗にも付くようになります。
セットアップより前に起きる失敗、つまりスイート自身のmoduleスコープのバックエンド起動中の失敗は、
隙間として残ります。この隙間は単位2で述べます。

## 動機

このギャップは仮説ではなく、実際に観測されています。プルリクエスト
[#1520](https://github.com/bajutsu-e2e/bajutsu/pull/1520)の差分は、シナリオYAMLへの`deviceLog`取得の
追加だけでした。そのプルリクエストで、`conformance (adb)`の
`test_a_read_postdates_a_content_moving_gesture`が
`bajutsu.drivers.base.ElementNotFound: scroll: {'id': 'conformance.scroll.row.19'} not found; the
region did not change ... (end of content)`で失敗しました。この種のスクロールタイミングのずれは、
画面録画があれば一目で判断がつきます。

このジョブ自身のログは、`No files were found with the provided path: runs/. No artifacts will be
uploaded.`で終わっています。ワークフローの「Upload run artifacts」ステップはすでに存在し、
シナリオ駆動のAndroidジョブがいずれも書き出す`runs/`を対象にしています。それでも、オンデバイスの
pytestスイートは、そのパスの下に何も書き出していません。

エビデンスが欠ける原因は、これらのスイートがデバイスへ到達する経路にあります。取得コード自体に
見落としがあるわけではありません。`bajutsu/evidence/core.py`の`FileSink`は、シナリオの`capture:`
リストに基づいて`video`/`deviceLog`のインターバルを開始・停止します。この処理は`bajutsu run`
パイプライン（`bajutsu/runner/pool.py`）の内部で完結しています。次の4つのテストファイルは、このパイプラインを
経由しません。

- `tests/test_driver_conformance_ondevice_android.py`
- `tests/test_fault_injection_ondevice_android.py`
- `tests/test_driver_conformance_ondevice.py`
- `tests/test_fault_injection_ondevice.py`

どのファイルも、moduleスコープのpytestフィクスチャから`launch_driver`を直接呼び出します。これは意図的な
設計であり、[BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery-ja.md)
に記録されています。BE-0334は、同じ形のpytestハーネスを持つiOS側のconformanceスイートに、同じ理由で
インフラ障害からの復旧を持たせました。パイプラインを経由しないことで、ドライバーレベルの契約テストが
得られます。その代償として、エビデンス取得を含め、パイプラインが無償で提供するものをすべて失います。

iOSのconformanceジョブには、同じギャップがもう一段重なっています。そのCIワークフロー（`ios-e2e.yml`）は
BE-0334の復旧回数レポートをアップロードしますが、`runs/`向けの「Upload run artifacts」ステップを
まったく持ちません。他のジョブはいずれもこのステップを持っています。

- `run`、`actuation`、`golden`、`bundled-runner`は、共有の`bajutsu-e2e`コンポジットアクションから
  このステップを受け取ります。
- `fault-injection`と`visual`は、自身のインラインステップとして持ちます。
- `android-e2e.yml`のオンデバイスジョブも、いずれも持っています。

## 詳細設計

4つの単位に分け、それぞれ単独でランディングできるようにします。

### 単位1 — パイプライン自身のプリミティブの上に、テスト単位・バックエンド非依存の取得フィクスチャを作る

各テストを2つのインターバル起動関数で包む、共有pytestフィクスチャを追加します。2つの起動関数は、
シナリオパイプライン自身が呼んでいる関数そのものです。どんなシナリオやYAMLにも、もともと依存しません。

- adb向けの`bajutsu.evidence.intervals.start_screenrecord`と`start_logcat`
- XCUITest向けの`intervals.start_video`と`start_device_log`

`demos/showcase/android/screenrecord.py`は、codegenレーンの`connectedAndroidTest`向けに、
`bajutsu run`の外から`start_screenrecord`をすでに直接呼んでいます。単位1は、バックグラウンドで動かす
スクリプトの代わりにpytestフィクスチャで、同じ手法を適用します。フィクスチャ自身はバックエンド非依存の
ままにします。2つの起動関数を、どちらかのバックエンドに固定せず明示的な引数として受け取るので、
1つの実装で4つのスイートすべてに使えます。

フィクスチャとプリミティブのあいだには、薄いヘルパーを2つ置きます。

- `android_screenrecord`は、adb側の2スイートが共有するサイズ、ビットレート、時間上限をあらかじめ束縛し、
  どちらの呼び出し側でも重複させません。あわせて、spawnのたびに`adb.VIDEO_DEVICE_PATH`を消去します。
  `start_screenrecord`はこの1つの固定パスへ録画し、stop時にはそこにあるものを無条件にpullします。
  前のテストがpullに失敗して残したクリップが、このテストのエビデンスとしてpullされてしまうからです。
  そのクリップは中身が空ではないので、欠落・空の検査でも見つかりません。
- `xcuitest_video`は`confirm_started=True`を束縛します。`android_screenrecord`も同じです。spawnしただけ
  では、手元のクライアントが起動したことしか分かりません。早く失敗したケースは、録画が何も残らない
  うちにteardownへ進むことがあります。その場合、このエビデンスが説明するはずの失敗に対して、存在しない
  mp4や再生できないmp4を残してしまいます。

取得の範囲は、モジュール単位ではなくテスト単位にします。`screenrecord`のデバイス側録画は、`time_limit`を
明示的に指定するかどうかにかかわらず、約180秒で止まります。本項目のきっかけとなったジョブでは、
conformanceモジュールの18件のテストですでに177秒かかっています。モジュール全体を1本の録画で覆うと、
テストが1件増えるだけで末尾から欠け始め、診断に必要な部分を失います。テスト単位の録画なら、この上限に
対して十分な余裕があります。長い録画から失敗の瞬間を探す代わりに、そのテスト自身のクリップから失敗の
様子を直接見つけられます。

### 単位2 — 失敗したテストのアーティファクトだけを残す

録画とデバイスログは、それを包んだテストが成功すれば破棄します。失敗したときは`runs/<lane>/<test-id>/`
以下に残します。これは、`screenrecord.py`自身のMakefileターゲットがcodegenレーンにすでに適用している、
失敗時のみ残す方針と同じです。

この判定は、フィクスチャ自身のteardownには置けません。`_evidence`はautouseかつfunctionスコープなので、
そのテストのfunctionスコープのフィクスチャの中では最初にセットアップされ、最後にteardownされます。
pytestが「teardown」の`TestReport`を組み立てるのは、その項目のすべてのfinalizerが実行を終えたあとです。
このフィクスチャ自身のteardownも、finalizerに含まれます。フィクスチャのteardownコードは、その時点で
すでに作られている「call」フェーズのレポートを読めます。一方で、自分より前にteardownされる別の
フィクスチャのteardown失敗は読めません。その失敗は、まだレポートされていないからです。

fault-injectionスイートでは、これが仮の話ではありません。テストごとの`driver`フィクスチャの`yield`
より後の半分は、画面表示を既知の状態へ戻す処理です。画面が戻らないという失敗は、録画があれば説明が
つく失敗そのものです。

判定は、代わりに`pytest_runtest_makereport`フックへ委ねます。`tests/backend_crash_recovery.py`が、
オンデバイスconformanceスイート向けにレポートを分類するために（BE-0334）すでに使っている仕組みと
同じものです。委ねる先は、このフックの「teardown」呼び出しです。試行全体を実際に見終えている、最初の
時点だからです。

フィクスチャは、2つの`start_*`呼び出しが成功したかどうかにかかわらず、自分のディレクトリをこのフックが
掃除してよい対象として無条件に登録します。開始の失敗は警告するだけで例外にしないため、試行の結果タグには
届きません。未登録のまま残すと、録画だけが残ったディレクトリを、成功したテストに対しても保持し続けて
しまいます。

成功した実行では、このフィクスチャ自身は`runs/`へ何も加えません。そのため、「Upload run artifacts」
ステップが運ぶのは、そのジョブがもともと書き出していたものだけです。`conformance (xcuitest)`では、
ワークフローレベルの`BAJUTSU_XCUITEST_RUNNER_LOG`が指す`runs/runner-logs`がそれにあたります。このジョブに
アップロードステップが欠けていたため、ランナーログも同時に捨てられていました。テストごとの
エビデンスが加わるのは、実際の失敗が最初に起きたときです。

`_evidence`自身のfunctionスコープは、本項目の境界でもあります。各スイートのバックエンド起動は、
functionスコープのどのフィクスチャよりも前に、moduleスコープで実行されます。

- Android側の`_eff`、`_adb_driver`、`_component`
- iOS側conformanceスイートの`_backend_launch`
- `backend_crash_recovery.py`の`_backend_lease_holder`

いずれも、`_evidence`より後ではなく前にセットアップされます。この順序から、失敗する経路が2つ生じます。

- `launch_driver`が、アプリのインストールや起動に失敗する。
- XCUITestランナーが、`BAJUTSU_XCUITEST_STARTUP_TIMEOUT`（300秒）以内に準備完了へ達しない。

どちらの場合も、pytestはその起動フィクスチャでmoduleのセットアップを打ち切ります。`_evidence`は
一度も実行されず、`runs/<lane>/<test-id>/`も作られません。moduleの全ケースが、動画もデバイスログも
欠けたままエラーになります。`backend_crash_recovery`（BE-0334）の再試行も、同じmoduleスコープの起動を
`_evidence`より前にやり直します。復旧を使い切るまでクラッシュし続ける起動失敗には、やはり何も
残りません。

したがって、「はじめに」と単位3が述べる対象範囲は、各テスト自身のfunctionスコープのセットアップ以降に
起きた失敗です。スイート自身のmoduleスコープの起動中の失敗は、本項目の対象外です。

フックが追う試行ごとの結果は、「setup」レポートごとにリセットし、それ以降は積算するだけにする
必要があります。真になったら保持し続ける方式にはしません。iOS側conformanceスイート自身が持つ
`backend_crash_recovery`マーカー（BE-0334）は、インフラ障害からの再試行のたびに、このフィクスチャを含む
項目1件全体を`_initrequest()`で再実行します。再実行は同じ`pytest.Item`、つまり同じstashを、試行の
あいだ使い続けます。立てたら保持するだけのタグでは、後で復旧して成功した試行でも「失敗」と読めて
しまいます。すると、成功したテストには要らないエビデンスを誤って残すことになります。しかもその時点では、
クラッシュした試行自身の録画は、それを引き継いだ試行によって同じディレクトリからもう消去されています。
各試行自身の「setup」レポートでリセットすれば、`backend_crash_recovery`が最終的に公開する試行の結果だけが
残ります。

### 単位3 — 4つのスイートすべてにautouseフィクスチャとして組み込む

このフィクスチャを、次のファイルにautouseフィクスチャとして追加します。テストごとに個別に指定しなくても、
4つのスイートすべての全ケースに適用されます。

- `tests/test_driver_conformance_ondevice_android.py`
- `tests/test_fault_injection_ondevice_android.py`
- `tests/test_driver_conformance_ondevice.py`
- `tests/test_fault_injection_ondevice.py`

対象は、各テスト自身のセットアップ以降に起きた失敗です。moduleスコープの起動区間が対象外である点は、
単位2で述べたとおりです。それぞれのスイートは自分自身のレーン名（`conformance-adb`/
`fault-injection-adb`/`conformance-xcuitest`/`fault-injection-xcuitest`）を渡します。これにより、
各ジョブがアップロードするアーティファクトは互いに独立したままで、同じパス上で衝突しません。

### 単位4 — iOSのconformanceジョブに「Upload run artifacts」ステップを追加する

`conformance (xcuitest)`ジョブに、アップロードステップを追加します。このジョブには、これまでアップロード
ステップが一切ありませんでした。他のジョブがすでに持っている、`path: runs/`、
`if-no-files-found: ignore`の同じステップを使います。

- `ios-e2e.yml`では、`run`、`actuation`、`golden`、`bundled-runner`が共有の`bajutsu-e2e`
  コンポジットアクション経由で受け取り、`fault-injection`と`visual`が自身のインラインステップとして
  持ちます。
- `android-e2e.yml`のオンデバイスジョブは、いずれもすでに持っています。

このステップがないと、単位3のフィクスチャがこのジョブの`runs/`に書き出しても、ワークフローがそれを
拾わないため、無駄になります。

## 検討した代替案

- **Androidだけを対応し、iOSは後続の項目に見送る。** 採用しません。`capture()`の明示的な
  `start_video`/`start_log`注入（単位1）は、すでにバックエンドをまたいで一般化できており、共有関数に
  Android固有の前提は残っていません。iOSを見送っても得られるものはなく、レビューがもう1周増えるだけです。
  iOSのconformanceジョブのほうが、むしろこの修正を必要としていました。フィクスチャに加えて、ワークフロー
  自体に「Upload run artifacts」ステップがまるごと欠けていたからです（単位4）。
- **これらのスイートを`bajutsu run`経由にして、`FileSink`をそのまま受け継ぐ。** 採用しません。同じ形の
  pytestハーネスについて、BE-0334が記録している理由と同じです。これらはシナリオレベルではなく
  ドライバーレベルの契約テストです。パイプラインの配管に到達するためにシナリオへ作り替えると、
  ドライバーを検証するためではなく、エビデンスを取得するために契約をねじ曲げることになります。
- **テスト単位ではなく、モジュール単位で1本の連続した録画を取得する。** 採用しません。`screenrecord`の
  約180秒という上限は、conformanceモジュール自身の実測所要時間と数秒しか違いません。モジュール全体を
  覆う録画では、テストが1件増えただけで、診断にもっとも必要な末尾から欠け始めます。
- **結果にかかわらずアーティファクトを常に残す。** 採用しません。このコードベース自身の前例と合いません。
  `screenrecord.py`のMakefileターゲットは、成功時には録画を破棄しています。また、診断用アーティファクトの
  目的にも合いません。成功したテストには診断すべきものがなく、その録画を残すと、テストが増えるたびに
  アップロードされるアーティファクトが大きくなるだけで、利点がありません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 単位1 — `intervals.start_screenrecord`/`start_logcat`（adb）と`intervals.start_video`/
      `start_device_log`（XCUITest）の上に、テスト単位・バックエンド非依存の取得フィクスチャを作る。
- [x] 単位2 — フィクスチャ自身のteardownではなく`pytest_runtest_makereport`フックの「teardown」
      レポートから判定し、「setup」レポートごとにリセットする（保持し続けない）試行ごとの結果に基づいて、
      失敗したテストの動画とデバイスログだけを残す。
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

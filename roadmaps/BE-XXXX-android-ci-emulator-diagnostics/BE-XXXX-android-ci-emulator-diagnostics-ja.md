[English](BE-XXXX-android-ci-emulator-diagnostics.md) · **日本語**

# BE-XXXX — Android の CI 失敗の原因究明に必要な多層の診断ログを収集する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-android-ci-emulator-diagnostics-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Platform support |
| 関連 | [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics-ja.md)、[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md)、[BE-0270](../BE-0270-android-adb-driver-conformance/BE-0270-android-adb-driver-conformance-ja.md)、[BE-0350](../BE-0350-ondevice-conformance-evidence-capture/BE-0350-ondevice-conformance-evidence-capture-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`.github/workflows/android-e2e.yml` は、`smoke` / `golden` / `network` / `conformance` /
`fault-injection` / `visual` の6ジョブを、Linux ランナー上で KVM により起動した Android
Virtual Device（AVD）に対して、adb バックエンド（`bajutsu/platform_lifecycle/environments/android.py`、
`bajutsu/drivers/adb.py`）で駆動しています。どのジョブも、終了時に集めるのはシナリオ自身の
`runs/` 出力（レポート、スクリーンショット、オプトインの `video` / `deviceLog` 区間証拠）だけです。
ジョブがインフラ由来の理由で失敗する場合があります。常駐 UI Automator サーバー
（[BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)）
が応答しなくなる場合、エミュレータの描画自体が固まる場合、あるいはジョブが `timeout-minutes` を
使い切って終わる場合です。そのいずれであっても、アップロードされたアーティファクトには理由を
示す証拠が残りません。シナリオまたはジョブそのものが完了しなかったという事実だけが残ります。
本提案は、Android バックエンド自身の多層診断ログ収集を追加します。設計は
[BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics-ja.md) が
XCUITest レーン向けに提案する3層構成を踏襲します。ストールを最初に検知した時点で発火する
`bajutsu` 内部のフック、エミュレータ自身の状態を CI 側から一括で採取する仕組み、ジョブ全体を
通してホスト負荷を記録するバックグラウンドサンプラーの3層です。ただし iOS レーンとの構造上の
違いが1つあり、これが設計全体を貫くため先に述べておきます。エミュレータは各ジョブの
`reactivecircus/android-emulator-runner` ステップの内側にしか存在しません。そのため、デバイス側の
採取は、そのステップと並ぶ composite action ではなく、ステップが呼び出すスクリプトに置きます
（「詳細設計」で改めて扱います）。収集物はすべて `runs/` 配下に置かれ、各ジョブの既存の
「Upload run artifacts」ステップがすでに運ぶため、新しいアップロード配線は不要です。

## 動機

Android の CI 診断ギャップは、
[BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics-ja.md) が
閉じようとしている iOS 側のギャップより狭くはなく、むしろ広いものです。XCUITest レーンの
composite action（`.github/actions/bajutsu-e2e/action.yml`）には、すでに「Collect crash
diagnostics」ステップがあり、`~/Library/Logs/DiagnosticReports` を走査しています。BE-0361 が
調べている失敗クラスに対しては空で終わりますが、ステップ自体は存在し、本物のクラッシュレポート
があればそこに載ります。`android-e2e.yml` には、これに相当するステップがどこにもありません。
どのジョブの手順も「Run …」の直後に「Upload run artifacts」が続くだけです。そのため、シナリオ
自身のアサーションの外側で失敗したジョブ（`bajutsu run` が駆動するシナリオの前後、あるいはその
最中）は、そのシナリオ自身のオプトインの `capture:` リストがすでに書いた分を超えて何も
アップロードしません。

このギャップは収集だけでなく検知にも及びます。`bajutsu/drivers/base.py` の
`BackendCrashError` のドキュメント文字列は、バックエンドのクラッシュが指しうるプロセスとして
「常駐 XCUITest ランナーの XCTest ホスト、adb サーバー、ブラウザプロセス」の3つを名指しして
います。
[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md)
はすでに、adb バックエンド自身のクラッシュ起因リトライを設計しています。XCUITest の Simulator
再起動に対して、adb 側は「アプリレベルのクリーンな状態」、つまり `uninstall` / `install` と
`pm clear` です。
ところが、この回復ロジックが反応する例外を実際に送出しているバックエンドは、今日1つしか
ありません。`bajutsu/drivers/xcuitest.py` の `XcuitestRunnerCrashError` が、コードベース中で
唯一の `base.BackendCrashError` のサブクラスです。adb ドライバ自身のインフラ障害シグナルである
`AdbResidentError`（`bajutsu/drivers/adb.py`）は、意図的に `BackendCrashError` ではありません。
これはドライバがすでに吸収するフォールバックへの縮退を示すものであり、シナリオを終わらせる
クラッシュではないからです。したがって、パイプラインのクラッシュ起因リトライには adb 側の
トリガーが今のところ存在しません。この検知面のギャップを閉じる価値があるかどうかは別の問い
です（「検討した代替案」を参照）。本提案にとっての意味は、第1層のフックが今日すでに確実に
発火するシグナルに取り付く必要があり、まだ存在しないクラッシュ宣言を待つわけにはいかない、
という点だけです。

本提案には、BE-0361 を導いた2026-08-12のランナーログのような、特定の Android CI インシデント
が根拠としてあるわけではありません。iOS レーンはすでに一度、「`timeout-minutes` で打ち切られる
だけで、診断できる原因が残らない」という筋書きを経験しました。本提案の狙いは、Android 側で
同じ筋書きが繰り返される前に、このギャップを閉じることです。インシデントを待たずに動くのは
意図的な選択です。上で示したコードベース上の証拠は、BE-0361 が診断した構造的な弱さ（静かに
失敗しうるバックエンドと、それを捕捉するように作られていない CI 収集の組み合わせ）を、すでに
示しています。より高価で計測しにくいプラットフォームでの再現を待つことは、より安価な収集を
先に作ることの前提条件ではありません。

## 詳細設計

収集は BE-0361 と同じ3層構成に従い、それぞれ異なる観測者が見える証拠を扱います。「いつ」
ストールが始まったかを知っているのは `bajutsu` 自身だけであり、CI の action はその瞬間の
エミュレータと Linux ホストの状態を読み、テレメトリのサンプラーだけがジョブ全体を見通します。

### 第1層は bajutsu だけが捕捉できるもの

**録画のバイト増加監視。** iOS の `video` プロバイダ（`bajutsu/evidence/intervals.py` の
`start_video`）は、対象ファイルのサイズを起動前のベースラインと比較するポーリング
（`_await_video_file_growing`）によって録画が実際に始まったことをすでに確認しており、バイトが
増えないまま終わると `recordVideo produced no new bytes …` と警告します。この警告が BE-0361 の
2つのストールトリガーの片方です。Android 側の対になる `start_screenrecord` は、デバイス上の
`screenrecord` プロセスが存在することしか確認しておらず（`_await_screenrecord_started`）、
バイトを生成しているかどうかは見ていないため、今日の Android にはこの警告に相当するものが
ありません。そこで、デバイス側の録画ファイルのサイズ（`adb shell stat -c %s <path>`、`stat` が
使えない場合は `ls -l` へのフォールバック）を起動前のベースラインと比較する、上限付きの
ポーリングを追加します。`start_video` がすでに使っている `confirm_started` のオプトインと
同じ形にし、タイムアウト内に増加が確認できなければ同じ形の警告を記録します。

**ストール時プローブ。** トリガーは2つあり、どちらも意図的に絞り込んであります。1つ目は常駐
チャネルの**階層読み取り**のフォールバック地点です（`bajutsu/drivers/adb.py` の階層読み取りを
囲む `except AdbResidentError` で、ドライバが `_fetch_hierarchy = None` をラッチし、以降その
リースのあいだ `uiautomator dump` サブプロセスへ縮退する箇所）。上の「動機」が述べているのは
まさにこの地点であり、読み取りチャネルが一時的に不安定なのではなく失われたことを意味します。
2つ目は、上記の新しい録画監視が出すバイト無し警告です。どちらの瞬間にも、描画が固まっているのか
ホストが固まっているのかを切り分ける状態がまだ存在します。この状態は、次のフレームで上書き
される寸前です。

このプローブが引っ掛けるのは**その伝播地点**であって、`AdbResidentError` というクラスでは
ありません。同クラスのサブクラスのうち2つは、健全な実行でも発火し、ストールとは正反対の意味を
持つからです。`AdbActUnsupported` は恒久的で想定内の機能欠如を表します（`/act` に 404 を返す
古い常駐サーバーで、ドライバは一度ラッチして以降は問い合わせません）。`AdbActUncertain` は
応答を取りこぼした競合状態を表し、二重に操作しないよう、ドライバはあえて操作が成立したものとして
扱います。どちらも操作経路で捕捉され、その経路では基底クラス自体もジェスチャ1回を縮退させる
だけでチャネルは使い続けます。クラスを引っ掛けてしまうと、何もストールしていない実行で捕捉が
走り、後述の実行ごとの上限を消費します。その結果、同じ実行の後半で本物のストールが起きたときに
予算が残っていない、という事態を招きます。プローブが存在する唯一の目的はその場面です。したがって
操作経路は対象外とします。

環境変数 `BAJUTSU_STALL_DIAGNOSTICS`（BE-0361 が提案するのと同じ名前で、意図的にバックエンド名を
冠していません）がディレクトリを指すとき、この2つのトリガー地点は、そこへ向けて上限付き
かつベストエフォートで捕捉します。捕捉するのは `adb shell dumpsys SurfaceFlinger --latency`、
直近の `logcat -d -t 200` の末尾（Android の logcat は iOS の unified log と違ってホスト側の
権限障壁がないため安価です）、そして `ps aux` と `top -bn1` のスナップショットです。各コマンドは
短いサブプロセスタイムアウトを持ち、失敗は握りつぶされ、捕捉回数は実行ごとに上限を持ちます。
BE-0361 自身の上限をそのまま踏襲する形です。`BAJUTSU_ADB_STALL_DIAGNOSTICS` のような新しい
変数名を作らず BE-0361 の変数名を再利用するのは、オペレータが1つの設定を入れるだけで、
どちらのバックエンドが動くジョブでも同じフックが有効になるからです。2つの提案が共有
するのはこの名前だけで、互いのコードには依存しないため、どちらを先に実装しても、あるいは
どちらか一方だけを実装しても構いません。

### 第2層はエミュレータ自身のステップの内側から集めるデバイスの状態

**この層の形を決める制約であり、Android レーンが BE-0361 の iOS レーンから最も鋭く分かれる点
です。** iOS では Simulator がジョブのあいだホスト上に存在し続けるため、独立したステップとして
動く composite action からでも到達できます。BE-0361 の `collect-ios-diagnostics` はまさにそれを
前提にしています。Android にはその余地がありません。`android-e2e.yml` でデバイスに触れる
ステップはすべて `reactivecircus/android-emulator-runner` ステップの**内側**で動きます。この
action はエミュレータを起動し、`script:` を実行し、そのステップが終わるとエミュレータを落とし
ます。その後ろに置いた通常のステップ（およびステップ単位の `if: failure()` ゲート）は、
**デバイスが接続されていない状態**で動きます。そのため `adb logcat -d`、`adb bugreport`、root
権限での tombstone / ANR の pull は、いずれも何ひとつ採取できません。同じ理由による先例がすでに
ます。`poll_cpuinfo` のサンプラーは `smoke` の `script:` 文字列の中に埋め込まれており、しかも
本来の終了コードを `rc` に保存して、シナリオの失敗を覆い隠さないようにしています。

そこでデバイス側の採取は、composite action ではなくスクリプトに置きます。
`scripts/collect_android_diagnostics.sh` を、エミュレータがまだ生きている各ジョブ自身の
`script:` の末尾から呼び出します。失敗時段は、デバイスが消えた後に発火するステップ単位の
`if: failure()` ではなく、実行コマンド自身の `rc` で分岐します。`poll_cpuinfo` のポーラーが
失敗する実行を生き延びるために使っているのと同じ形です。配線先は、AVD を起動して adb ドライバ
経由で駆動するすべてのジョブ、すなわち `smoke` / `golden` / `network` / `conformance` /
`fault-injection` / `visual`（`docs/ci.md` の Android レーンがすでに挙げている6ジョブと同じ）
です。`codegen`（`uiautomator (codegen)`）も、BE-0361 が iOS 側の `codegen` ジョブを除外する
理由と同じ理由で除外します。Gradle の `connectedAndroidTest` を直接駆動し、自分自身の
`androidTest-results` / `codegen-diagnostics` レポートだけをアップロードし、この収集が乗るはずの
`runs/` 配下には何も書かないからです。このスクリプトは2段階で実行します。

- **常時**（軽量で、毎回実行します）。`adb logcat -d -b main,system,crash,events,radio` による
  全バッファのダンプです。シナリオに `capture: [deviceLog]` が付いている間だけストリームする
  区間証拠の `deviceLog` とは違い、これはデバイス自身のリングバッファを直接読みます。そのため、
  どのシナリオの捕捉も始まる前にジョブが失敗した場合や、ストリームを担うプロセスが死んで途中で
  切れた場合にも、何かしら見せるものが残ります。加えて `adb shell dumpsys meminfo`、`adb
  shell getprop`、`adb devices -l` による環境スナップショット（API level、ABI、エミュレータ
  自身の起動オプション）も取ります。これは BE-0361 の実行横断の仮説（4）、すなわち失敗が特定の
  エミュレータ構成に集中しているかどうかに答えます。
- **失敗時のみ**（重量級）。Android 純正の総合コレクタである `adb bugreport`（`simctl
  diagnose` の直接の対応物です）を `runs/diagnostics/` 配下へ zip して保存します。加えて、
  これらのジョブがすでに使っている AVD のプロファイル（`target: google_apis` であり
  `google_apis_playstore` ではありません）は `adb root` に対応しているため、root 権限での
  `/data/tombstones`（ネイティブクラッシュレポート）と `/data/anr/`（Application Not
  Responding（ANR）のトレース）の pull も行います。この2種類のクラッシュレポートは、iOS 側では
  `~/Library/Logs/DiagnosticReports` の走査だけで無料で手に入りますが、Android ではデバイス側
  に置かれるため明示的な pull が必要です。この段は上で述べた `rc` でゲートするので、デバイスが
  まだ接続されているうちに発火します。

### 第3層は時系列のホストテレメトリ

第2層と違い、この層が読むのは **Linux ランナーだけ**でデバイスには触れません。そのため上の制約は
この層を縛りません。`top -bn1` と `free -m` は、エミュレータの接続の有無によらず、通常の
ワークフローステップから動きます。したがって第3層は、この収集のうち唯一 composite action に
できる部分です。`.github/actions/collect-android-diagnostics` に `start` フェーズと `collect`
フェーズを設け、各ジョブがエミュレータステップの前後で呼びます。`start` は約20秒ごとに
`runs/diagnostics/host-telemetry.log` へ追記するバックグラウンドサンプラーを起動します。
エミュレータステップを外側から挟むことには、もう1つ利点があります。エミュレータステップ自体が
落ちたジョブもテレメトリで覆えます。そのステップの内側で起動したサンプラーには、これができません。

どのジョブも、共有される Linux ランナー上でエミュレータ自身の資源上限（`-memory 8192 -cores 2`）
をすでに調整しており、ストールがホストのメモリやCPU圧迫と相関している可能性は、BE-0361 の
macOS ホストに関する仮説（2）とまったく同じだけ現実的です。この層があることで、その相関を
事後の推測ではなく記録として残せます。BE-0361 のサンプラーと同じく、これは判定経路の外にいる
観測者です。その間隔は待機ではなくサンプリング周期なので、実行ループにおける固定 sleep を
禁じる第2の最重要原則には触れません。

### 作業分解（`MECE`）

相互に排他的で全体として網羅的（Mutually Exclusive, Collectively Exhaustive、`MECE`）な
作業単位は次のとおりです。

1. **録画のバイト増加監視。** `bajutsu/evidence/intervals.py` の `start_screenrecord` に
   `_await_screenrecord_growing`（または同等のポーリング）を追加します。`start_video` が
   すでに使っている `confirm_started` のオプトインの背後に置き、iOS 側のプロバイダと同じ形で
   バイト無しを警告します。
2. **ストール時プローブ。** `BAJUTSU_STALL_DIAGNOSTICS` の背後の上限付き捕捉モジュール、その
   2つの adb 側トリガー地点（`_fetch_hierarchy = None` をラッチする階層読み取りのフォールバック
   と、新しい録画のバイト無し警告。操作経路は意図的に対象外）、実行ごとの捕捉回数上限、そして
   `android-e2e.yml` でのオプトインを実装します。
3. **`scripts/collect_android_diagnostics.sh`。** 常時段と、`rc` で分岐する失敗時段を実装し、
   `smoke` / `golden` / `network` / `conformance` / `fault-injection` / `visual` の各
   エミュレータステップ自身の `script:` の末尾から呼び出します。デバイスがまだ接続されている
   ステップの内側です。
4. **composite action `collect-android-diagnostics`。** ホストテレメトリの `start` / `collect`
   フェーズだけを実装し、各ジョブのエミュレータステップを外側から挟みます。
5. **ドキュメント。**
   [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics-ja.md) が
   `docs/ci.md` に設ける診断の節を拡張し（その `docs/ja/` ミラーも含みます）、Android 側の段構成
   を同じ節に加えます。2つのバックエンドを別々に書くのではなく、1つの節でまとめて扱います。
   デバイス側の採取が action ではなくスクリプトに置かれる理由も、あわせて記します。
6. **テスト。** `bajutsu` 側の2つのフックのユニットテストです。録画監視がバイト増加を確認
   できないときに限り警告すること、ストールプローブが環境変数の未設定時は no-op であること、
   設定時は上限付きかつベストエフォートであり、操作経路では決して発火しないことを固定します。
   新しいスクリプトは `make lint-sh` / `shellcheck` が、新しい composite action は
   `make lint-actions` / `actionlint` が検査します。

### 最重要原則の維持

- **AI は判定しない。** どの層も証拠を集めるだけです。本提案が触れるどの経路にもモデル
  呼び出しは入らず、収集物が判定に流れ込むこともありません。
- **決定論を最優先する。** プローブはタイムアウト付きの有界なサブプロセス呼び出しであり、
  sleep ではありません。テレメトリのサンプラーは実行ループの外から観測します。合否の決め方は
  従来と一切変わりません。
- **アプリ非依存。** 本提案は対象アプリを読みません。フックはデバイスと常駐チャネルに、CI の
  action はエミュレータとホストに鍵付けされ、ターゲットごとの分岐はどこにもありません。

## 検討した代替案

- **第3層を `android-e2e.yml` の既存の `poll_cpuinfo` サンプラーに統合する、あるいはその入力に
  手を触れない。** このレーンには、まさに同じ問いのためのバックグラウンドサンプラーがすでに
  あります。`workflow_dispatch` の入力を設定すると、`smoke` の実行中は2秒ごとに
  `adb shell dumpsys cpuinfo` が `runs/cpuinfo.log` へ追記され、フレークを追う目的の手動実行を
  ホストの負荷と突き合わせられます。第3層は**これと共存し、削除しません**。両者は別の側から別の
  ものを測っているからです。`poll_cpuinfo` は adb 越しに**ゲスト**の見え方を読み、第3層は
  **ホスト**自身の負荷を読みます。`poll_cpuinfo` が `pull_request` と `merge_group` のすべての
  実行で無効になっている理由も、そのままは当てはまりません。あの入力が無効なのは「2秒ごとの
  ポーリング自体が、まさに CPU 競合を測ろうとしているレーンに対して継続的な adb トラフィックと
  ホスト負荷を加える」からですが、約20秒ごとのホスト側 `top` / `free` にこの観測者効果はありません。
  adb トラフィックをまったく発生させず、頻度も100分の1です。そのため第3層は、`poll_cpuinfo` が
  オプトインであるのに対して常時有効です。実装者は、一方が他方を置き換えるものではなく、補い合う
  ものとして読んでください。
- **iOS レーンと Android レーンで composite action を1つに共有する。** 退けます。
  `collect-ios-diagnostics` は `simctl` / CoreSimulator / macOS の unified log を読み、
  `collect-android-diagnostics` は adb / logcat / Linux ホストを読みます。両者の背後にある
  ツールに共通する面はなく、1つの action にまとめれば共有ロジックではなく `if backend ==
  …` の分岐になってしまいます。2つの action は別々のまま、常時段と失敗時段の分割、`start` /
  `collect` のテレメトリフェーズという設計、そして第1層については環境変数名という1点だけを
  共有します。
- **`BAJUTSU_STALL_DIAGNOSTICS` を定義する
  [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics-ja.md)
  の着地を待ってから本提案に着手する。** 退けます。BE-0361 自体がまだ実装されていない
  「提案」であり、コードを共有する理由もないまま Android 側のゼロ収集というギャップを
  そこへ従属させれば、ギャップが無期限に残りかねません。2つの提案が共有するのは
  変数「名」の規約だけであり、互いに依存するモジュールはないため、どちらを先に実装しても
  構いません。先に着地したほうが `BAJUTSU_STALL_DIAGNOSTICS` の共有ドキュメントを定義し、
  後から着地するほうは自分のバックエンドのトリガー地点をその既存の名前へ配線します。
- **adb バックエンドに本物の `AdbBackendCrashError(base.BackendCrashError)` を持たせ、
  ストールプローブをそこへ配線して、本提案の「動機」が示すクラッシュ検知のギャップ自体を
  閉じる。** スコープ外とします。adb 側の障害がどの程度深刻なら現在のリースを捨てて
  シナリオ全体を再試行すべきかを決めるのは回復セマンティクスの問い（BE-0353 のクラッシュ
  起因リトライが、このバックエンドについて実際には何をトリガーに動くべきかという問い）で
  あり、診断収集の問いではありません。この2つを混ぜると、本提案が「証拠をもっと集める」に
  必要な範囲をはるかに超えた挙動変更の責任を負うことになります。検知はいずれ別の提案が担い、
  本提案の第1層は、まだ存在しないシグナルを待つのではなく、adb ドライバが今日すでに送出
  している障害シグナル（`AdbResidentError`）にフックします。
- **`adb bugreport` を失敗時だけでなく毎回実行する。** 退けます。bugreport は数十秒かかり
  数メガバイト規模のアーカイブを作ります。macOS の `sysdiagnose`（BE-0361 がまさにこの理由で
  先送りしています）よりはるかに軽量ですが、それでも6ジョブすべての緑の実行のたびに
  払うには現実のコストです。常時段と失敗時段を分けることで、軽量な段は無条件に残しつつ、
  重量級のコレクタは本当に必要な実行だけに使います。
- **ジョブ全体で `adb logcat` のリングバッファをストリームし続ける。** シナリオ単位の
  `deviceLog` 区間証拠（`bajutsu/evidence/intervals.py` の `start_logcat`）がすでにシナリオ
  自身のウィンドウをストリームしており、ジョブ全体の2本目のストリームは、常時段の
  `logcat -d` ダンプ（デバイス自身が保持しているバッファを事後に1回読むだけ）がすでに与える
  利益の大半を、より高いコストで重複させるだけです。BE-0361 が iOS の unified log をジョブ
  全体でストリームすることを退ける理由と同じです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の `MECE` な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] Unit 1 — Android の `video` プロバイダの録画バイト増加監視
- [ ] Unit 2 — ストール時プローブのフック（階層読み取りのフォールバック、新しいバイト無し警告）
- [ ] Unit 3 — `scripts/collect_android_diagnostics.sh` と各ジョブの `script:` への配線
- [ ] Unit 4 — composite action `collect-android-diagnostics`（ホストテレメトリのみ）
- [ ] Unit 5 — ドキュメント（`docs/ci.md` とその `ja` ミラー）
- [ ] Unit 6 — テスト

## 参考

- [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics-ja.md) —
  本提案が Android バックエンドへ移植する3層診断設計、および共有する変数名
  `BAJUTSU_STALL_DIAGNOSTICS` の出典
- [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md) —
  adb 側のクラッシュ起因リトライ。その設計自体がすでに前提としているバックエンドクラッシュ
  シグナルが、本提案の「動機」が示すとおり adb にはまだ存在しません
- [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md) —
  常駐チャネル。その階層読み取りのフォールバック地点が本提案の第1のストールトリガーです
- [BE-0270](../BE-0270-android-adb-driver-conformance/BE-0270-android-adb-driver-conformance-ja.md) —
  本提案の収集が挙動を変えずに失敗を計測しやすくする、adb ドライバの conformance 契約
- [BE-0350](../BE-0350-ondevice-conformance-evidence-capture/BE-0350-ondevice-conformance-evidence-capture-ja.md) —
  同じオンデバイススイート向けに、本提案の収集が補う video / deviceLog 証拠
- [`bajutsu/evidence/intervals.py`](../../bajutsu/evidence/intervals.py) — `start_video`
  （Unit 1 が移植するバイト増加監視のパターン）と `start_screenrecord`（Unit 1 が拡張する継ぎ目）
- [`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py) — Unit 2 の第1トリガーである階層
  読み取りのフォールバックと、意図的に対象外とする操作経路の `AdbActUnsupported` /
  `AdbActUncertain`
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — `BackendCrashError`。本提案の
  「動機」が指摘し、今後の提案に委ねる検知面のギャップです（「検討した代替案」を参照）
- [`.github/actions/bajutsu-e2e/action.yml`](../../.github/actions/bajutsu-e2e/action.yml) —
  XCUITest レーンの「Collect crash diagnostics」ステップ。本提案が意図の面では踏襲し、機構の面
  では踏襲しない先例です（第2層を参照）
- [`.github/workflows/android-e2e.yml`](../../.github/workflows/android-e2e.yml) — 全層に
  オプトインするジョブ群を持ち、`reactivecircus/android-emulator-runner` ステップがデバイスに
  到達できる範囲を区切り、`poll_cpuinfo` サンプラーが Unit 3 の形の先例かつ第3層の隣人である
  レーン
- [`docs/ci.md`](../../docs/ci.md) — Unit 5 が拡張する CI ドキュメントであり、本提案が配線する
  6ジョブをその Android レーンがすでに挙げているページ

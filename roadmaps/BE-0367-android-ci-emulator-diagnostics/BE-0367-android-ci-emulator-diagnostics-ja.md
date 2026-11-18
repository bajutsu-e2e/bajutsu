[English](BE-0367-android-ci-emulator-diagnostics.md) · **日本語**

# BE-0367 — Android の CI 失敗の原因究明に必要な多層の診断ログを収集する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0367](BE-0367-android-ci-emulator-diagnostics-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0367") |
| 実装 PR | [#1631](https://github.com/bajutsu-e2e/bajutsu/pull/1631) |
| トピック | Platform support |
| 関連 | [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics-ja.md)、[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md)、[BE-0270](../BE-0270-android-adb-driver-conformance/BE-0270-android-adb-driver-conformance-ja.md)、[BE-0350](../BE-0350-ondevice-conformance-evidence-capture/BE-0350-ondevice-conformance-evidence-capture-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`.github/workflows/android-e2e.yml` は、`smoke` / `golden` / `network` / `conformance` /
`fault-injection` / `visual` の6ジョブを持ちます。各ジョブは、adb バックエンド（`bajutsu/platform_lifecycle/environments/android.py`、`bajutsu/drivers/adb.py`）で Android Virtual Device（AVD）を駆動します。AVD は、Linux の GitHub Actions ランナー上で KVM により起動します。

どのジョブも、終了時に集めるのはシナリオ自身の `runs/` 出力だけです。出力の中身は、レポート、スクリーンショット、オプトインの `video` / `deviceLog` 区間証拠です。それ以外は何も集めません。

ジョブがインフラ由来の理由で失敗する場合があります。

- 常駐 UI Automator サーバー（`bajutsu/adb_resident.py`、[BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)）が応答しなくなる。
- エミュレータの描画自体が固まる。
- ジョブが `timeout-minutes` を使い切って終わる。

いずれの場合も、アップロードされたアーティファクトには、理由を示す証拠が残りません。シナリオまたはジョブそのものが完了しなかったという事実だけが残ります。

本提案は、Android バックエンド自身の多層診断ログ収集を追加します。設計は、[BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics-ja.md) が XCUITest レーン向けに提案する3層構成を踏襲します。

1. ストールを最初に検知した時点で発火する、`bajutsu` 内部のフック。
2. エミュレータ自身の状態を CI 側から一括で採取する仕組み。
3. ジョブ全体を通してホスト負荷を記録する、バックグラウンドサンプラー。

ただし、iOS レーンとの構造上の違いが1つあり、設計全体を貫きます。エミュレータは、各ジョブの `reactivecircus/android-emulator-runner` ステップの内側にしか存在しません。そのため、デバイス側の採取は、そのステップと並ぶ composite action ではなく、ステップが呼び出すスクリプトに置きます。この点は「詳細設計」で改めて扱います。

収集物はすべて `runs/` 配下に置かれます。各ジョブの既存の「Upload run artifacts」ステップがすでにこれを運ぶため、新しいアップロード配線は不要です。

## 動機

Android の CI 診断ギャップは、[BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics-ja.md) が閉じようとしている iOS 側のギャップより狭くはなく、むしろ広いものです。XCUITest レーンの composite action（`.github/actions/bajutsu-e2e/action.yml`）には、すでに「Collect crash diagnostics」ステップがあります。このステップは `~/Library/Logs/DiagnosticReports` を走査します。BE-0361 が調べている失敗クラスに対しては、そのディレクトリは空のままです。しかし、本物のクラッシュレポートがあれば、そこに載ります。

`android-e2e.yml` には、これに相当するステップがどこにもありません。どのジョブの手順も、「Run …」の直後に「Upload run artifacts」が続くだけです。シナリオ自身のアサーションの外側で失敗したジョブがあります。`bajutsu run` が駆動するシナリオの前後、あるいはその最中に失敗した場合です。そのジョブは、シナリオ自身のオプトインの `capture:` リストがすでに書いた分を超えて、何もアップロードしません。

このギャップは、収集だけでなく検知にも及びます。`bajutsu/drivers/base.py` の `BackendCrashError` のドキュメント文字列は、バックエンドのクラッシュが指しうるプロセスとして、「常駐 XCUITest ランナーの XCTest ホスト、adb サーバー、ブラウザプロセス」の3つを名指ししています。[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md) は、adb バックエンド自身のクラッシュ起因リトライを、すでに設計しています。回復の内容は、XCUITest の Simulator 再起動に対して、「アプリレベルのクリーンな状態」、つまり `uninstall` / `install` と `pm clear` です。

ところが、この回復ロジックが反応する例外を実際に送出しているバックエンドは、今日1つしかありません。`bajutsu/drivers/xcuitest.py` の `XcuitestRunnerCrashError` が、コードベース中で唯一の `base.BackendCrashError` のサブクラスです。adb ドライバ自身のインフラ障害シグナルである `AdbResidentError`（`bajutsu/drivers/adb.py`）は、意図的に `BackendCrashError` ではありません。このシグナルは、ドライバがすでに吸収するフォールバックへの縮退を示すものであり、シナリオを終わらせるクラッシュではないからです。したがって、パイプラインのクラッシュ起因リトライには、adb 側のトリガーが今のところ存在しません。

この検知面のギャップを閉じる価値があるかどうかは、別の問いです（「検討した代替案」を参照）。本提案にとっての意味は、次の1点だけです。第1層のフックは、今日すでに確実に発火するシグナルに取り付く必要があります。まだ存在しないクラッシュ宣言を待つわけにはいきません。

本提案には、BE-0361 を導いた2026-08-12のランナーログのような、特定の Android CI インシデントが根拠としてあるわけではありません。iOS レーンは、すでに一度、「`timeout-minutes` で打ち切られるだけで、診断できる原因が残らない」という筋書きを経験しました。本提案の狙いは、Android 側で同じ筋書きが繰り返される前に、このギャップを閉じることです。

インシデントを待たずに動くのは、意図的な選択です。上で示したコードベース上の証拠は、BE-0361 が診断した構造的な弱さを、すでに示しています。静かに失敗しうるバックエンドと、それを捕捉するように作られていない CI 収集の組み合わせです。より高価で計測しにくいプラットフォームでの再現を待つことは、より安価な収集を先に作る前提条件ではありません。

## 詳細設計

収集は BE-0361 と同じ3層構成に従います。各層は、異なる観測者が見える証拠を扱います。

- 「いつ」ストールが始まったかを知っているのは、`bajutsu` 自身だけです。
- CI の action は、その瞬間のエミュレータと Linux ホストの状態を読みます。
- テレメトリのサンプラーだけが、ジョブ全体を見通します。

### 第1層は bajutsu だけが捕捉できるもの

**録画のバイト増加監視。** iOS の `video` プロバイダ（`bajutsu/evidence/intervals.py` の `start_video`）は、録画が実際に始まったことをすでに確認しています。対象ファイルのサイズを起動前のベースラインと比較するポーリング（`_await_video_file_growing`）で確認し、バイトが増えないまま終わると `recordVideo produced no new bytes …` と警告します。この警告が、BE-0361 の2つのストールトリガーの片方です。

Android 側の対になる `start_screenrecord` が確認するのは、デバイス上の `screenrecord` プロセスが存在することだけです（`_await_screenrecord_started`）。バイトを生成しているかどうかは見ていません。今日の Android には、この警告に相当するものがありません。そこで、次の監視を追加します。

- デバイス側の録画ファイルのサイズを、起動前のベースラインと比較する、上限付きのポーリングを追加します。サイズの取得には `adb shell stat -c %s <path>` を使います。`stat` が使えない場合は `ls -l` へフォールバックします。
- このポーリングは、`start_video` がすでに使っている `confirm_started` のオプトインの背後に置きます。
- タイムアウト内に増加が確認できなければ、同じ形の警告を記録します。

**ストール時プローブ。** トリガーは2つあります。どちらも意図的に絞り込んであります。

1. 常駐チャネルの**階層読み取り**のフォールバック地点。場所は `bajutsu/drivers/adb.py` の、階層読み取りを囲む `except AdbResidentError` です。ここでドライバは `_fetch_hierarchy = None` をラッチし、以降そのリースのあいだ `uiautomator dump` サブプロセスへ縮退します。上の「動機」が述べているのは、まさにこの地点です。読み取りチャネルが一時的に不安定なのではなく、失われたことを意味します。
2. 上記の新しい録画監視が出すバイト無し警告。

どちらの瞬間にも、描画が固まっているのかホストが固まっているのかを切り分ける状態が、まだ存在します。この状態は、次のフレームで上書きされる寸前です。

このプローブが引っ掛けるのは**その伝播地点**であって、`AdbResidentError` というクラスではありません。同クラスのサブクラスのうち2つは、健全な実行でも発火し、ストールとは正反対の意味を持つからです。

- `AdbActUnsupported` は、恒久的で想定内の機能欠如を表します。`/act` に 404 を返す古い常駐サーバーがその例です。ドライバは一度ラッチして、以降は問い合わせません。
- `AdbActUncertain` は、応答を取りこぼした競合状態を表します。ドライバは、二重に操作しないよう、あえて操作が成立したものとして扱います。

どちらも操作経路で捕捉されます。その経路では、基底クラス自体もジェスチャ1回を縮退させるだけで、チャネルは使い続けます。クラスを引っ掛けると、何もストールしていない実行で捕捉が走り、後述の実行ごとの上限を消費します。その結果、同じ実行の後半で本物のストールが起きたときに、予算が残っていません。プローブが存在する唯一の目的は、その場面です。したがって、操作経路は対象外とします。

環境変数 `BAJUTSU_STALL_DIAGNOSTICS` がディレクトリを指すとき、2つのトリガー地点は、そこへ向けて上限付きかつベストエフォートで捕捉します。この変数名は、BE-0361 が提案するのと同じで、意図的にバックエンド名を冠していません。捕捉するのは次の3つです。

- `adb shell dumpsys SurfaceFlinger --latency`
- 直近の `logcat -d -t 200` の末尾。Android の logcat は、iOS の unified log と違ってホスト側の権限障壁がないため、安価です。
- `ps aux` と `top -bn1` のスナップショット

各コマンドは、短いサブプロセスタイムアウトを持ちます。失敗は握りつぶします。捕捉回数は実行ごとに上限を持ちます。BE-0361 自身の上限をそのまま踏襲する形です。

`BAJUTSU_ADB_STALL_DIAGNOSTICS` のような新しい変数名を作らず、BE-0361 の変数名を再利用します。そうすると、オペレータが1つの設定を入れるだけで、どちらのバックエンドが動くジョブでも同じフックが有効になります。2つの提案が共有するのは、この名前だけです。互いのコードには依存しないため、どちらを先に実装しても、あるいはどちらか一方だけを実装しても構いません。

### 第2層はエミュレータ自身のステップの内側から集めるデバイスの状態

**この層の形を決める制約は、Android レーンが BE-0361 の iOS レーンから最も鋭く分かれる点でもあります。** iOS では、Simulator がジョブのあいだホスト上に存在し続けます。そのため、独立したステップとして動く composite action からでも到達できます。BE-0361 の `collect-ios-diagnostics` は、まさにそれを前提にしています。

Android には、その余地がありません。`android-e2e.yml` でデバイスに触れるステップは、すべて `reactivecircus/android-emulator-runner` ステップの**内側**で動きます。この action は、エミュレータを起動し、`script:` を実行し、そのステップが終わるとエミュレータを落とします。その後ろに置いた通常のステップは、**デバイスが接続されていない状態**で動きます。ステップ単位の `if: failure()` ゲートも同じです。そのため、`adb logcat -d`、`adb bugreport`、root 権限での tombstone / ANR の pull は、いずれも何ひとつ採取できません。

同じ理由による先例が、すでにあります。`poll_cpuinfo` のサンプラーは、`smoke` の `script:` 文字列の中に埋め込まれています。しかも、本来の終了コードを `rc` に保存して、シナリオの失敗を覆い隠さないようにしています。

そこで、デバイス側の採取は、composite action ではなくスクリプトに置きます。`scripts/collect_android_diagnostics.sh` を、エミュレータがまだ生きている各ジョブ自身の `script:` の末尾から呼び出します。失敗時段は、実行コマンド自身の `rc` で分岐します。これは、`poll_cpuinfo` のポーラーが失敗する実行を生き延びるために使っているのと同じ形です。デバイスが消えた後に発火するステップ単位の `if: failure()` は使いません。

配線先は、AVD を起動して adb ドライバ経由で駆動するすべてのジョブです。すなわち `smoke` / `golden` / `network` / `conformance` / `fault-injection` / `visual` で、`docs/ci.md` の Android レーンがすでに挙げている6ジョブと同じです。

`codegen`（`uiautomator (codegen)`）は、除外します。BE-0361 が iOS 側の `codegen` ジョブを除外する理由と同じです。`codegen` は、Gradle の `connectedAndroidTest` を直接駆動します。アップロードするのは自分自身の `androidTest-results` / `codegen-diagnostics` レポートだけです。この収集が乗るはずの `runs/` 配下には、何も書きません。

このスクリプトは、2段階で実行します。

- **常時**（軽量で、毎回実行します）。
  - `adb logcat -d -b main,system,crash,events,radio` による全バッファのダンプです。区間証拠の `deviceLog` は、シナリオに `capture: [deviceLog]` が付いている間だけストリームします。このダンプは違い、デバイス自身のリングバッファを直接読みます。そのため、どのシナリオの捕捉も始まる前にジョブが失敗した場合にも、何かしら見せるものが残ります。ストリームを担うプロセスが死んで途中で切れた場合にも、同じです。
  - `adb shell dumpsys meminfo`、`adb shell getprop`、`adb devices -l` による環境スナップショットです。内容は API level、ABI、エミュレータ自身の起動オプションです。このスナップショットは、BE-0361 の実行横断の仮説（4）、すなわち失敗が特定のエミュレータ構成に集中しているかどうかに答えます。
- **失敗時のみ**（重量級）。この段は、上で述べた `rc` でゲートします。そのため、デバイスがまだ接続されているうちに発火します。
  - Android 純正の総合コレクタである `adb bugreport`（`simctl diagnose` の直接の対応物です）。zip して `runs/diagnostics/` 配下へ保存します。
  - root 権限での `/data/tombstones`（ネイティブクラッシュレポート）と `/data/anr/`（Application Not Responding（ANR）のトレース）の pull。これらのジョブがすでに使っている AVD のプロファイル（`target: google_apis` であり `google_apis_playstore` ではありません）は、`adb root` に対応しています。この2種類のクラッシュレポートは、iOS 側では `~/Library/Logs/DiagnosticReports` の走査だけで無料で手に入ります。Android ではデバイス側に置かれるため、明示的な pull が必要です。

### 第3層は時系列のホストテレメトリ

第2層と違い、この層が読むのは **Linux ランナーだけ**で、デバイスには触れません。そのため、上の制約はこの層を縛りません。`top -bn1` と `free -m` は、エミュレータの接続の有無によらず、通常のワークフローステップから動きます。

第3層は、この収集のうち唯一 composite action にできる部分です。`.github/actions/collect-android-diagnostics` に、2つのフェーズを設けます。

- `start` フェーズ：各ジョブがエミュレータステップの前に呼びます。約20秒ごとに `runs/diagnostics/host-telemetry.log` へ追記する、バックグラウンドサンプラーを起動します。
- `collect` フェーズ：各ジョブがエミュレータステップの後に呼びます。

エミュレータステップを外側から挟むことには、もう1つ利点があります。エミュレータステップ自体が落ちたジョブも、テレメトリで覆えます。そのステップの内側で起動したサンプラーには、これができません。

どのジョブも、共有される Linux ランナー上で、エミュレータ自身の資源上限（`-memory 8192 -cores 2`）をすでに調整しています。ストールがホストのメモリや CPU の圧迫と相関している可能性は、BE-0361 の macOS ホストに関する仮説（2）とまったく同じだけ現実的です。この層があることで、その相関を事後の推測ではなく記録として残せます。

BE-0361 のサンプラーと同じく、このサンプラーは判定経路の外にいる観測者です。その間隔は待機ではなくサンプリング周期なので、実行ループにおける固定 sleep を禁じる第2の最重要原則には触れません。

### 作業分解（`MECE`）

相互に排他的で全体として網羅的（Mutually Exclusive, Collectively Exhaustive、`MECE`）な作業単位は次のとおりです。

1. **録画のバイト増加監視。** `bajutsu/evidence/intervals.py` の `start_screenrecord` に、`_await_screenrecord_growing`（または同等のポーリング）を追加します。`start_video` がすでに使っている `confirm_started` のオプトインの背後に置きます。iOS 側のプロバイダと同じ形で、バイト無しを警告します。
2. **ストール時プローブ。** 次の部品を実装します。
   - `BAJUTSU_STALL_DIAGNOSTICS` の背後の、上限付き捕捉モジュール。
   - 2つの adb 側トリガー地点。`_fetch_hierarchy = None` をラッチする階層読み取りのフォールバックと、新しい録画のバイト無し警告です。操作経路は意図的に対象外とします。
   - 実行ごとの捕捉回数上限。
   - `android-e2e.yml` でのオプトイン。
3. **`scripts/collect_android_diagnostics.sh`。** 常時段と、`rc` で分岐する失敗時段を実装します。`smoke` / `golden` / `network` / `conformance` / `fault-injection` / `visual` の各エミュレータステップ自身の `script:` の末尾から呼び出します。呼び出しは、デバイスがまだ接続されているステップの内側で行います。
4. **composite action `collect-android-diagnostics`。** ホストテレメトリの `start` / `collect` フェーズだけを実装します。各ジョブのエミュレータステップを外側から挟みます。
5. **ドキュメント。** [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics-ja.md) が `docs/ci.md` に設ける診断の節を拡張します（その `docs/ja/` ミラーも含みます）。Android 側の段構成を同じ節に加え、2つのバックエンドを別々に書くのではなく、1つの節でまとめて扱います。デバイス側の採取が action ではなくスクリプトに置かれる理由も、あわせて記します。
6. **テスト。** `bajutsu` 側の2つのフックのユニットテストです。
   - 録画監視は、バイト増加を確認できないときに限り警告します。
   - ストールプローブは、環境変数の未設定時は no-op です。設定時は上限付きかつベストエフォートで、操作経路では決して発火しません。
   - 新しいスクリプトは `make lint-sh` / `shellcheck` が、新しい composite action は `make lint-actions` / `actionlint` が検査します。

### 最重要原則の維持

- **AI は判定しない。** どの層も証拠を集めるだけです。本提案が触れるどの経路にもモデル呼び出しは入らず、収集物が判定に流れ込むこともありません。
- **決定論を最優先する。** プローブは、タイムアウト付きの有界なサブプロセス呼び出しであり、sleep ではありません。テレメトリのサンプラーは、実行ループの外から観測します。合否の決め方は従来と一切変わりません。
- **アプリ非依存。** 本提案は対象アプリを読みません。フックはデバイスと常駐チャネルに、CI の action はエミュレータとホストに鍵付けされ、ターゲットごとの分岐はどこにもありません。

## 検討した代替案

- **第3層を `android-e2e.yml` の既存の `poll_cpuinfo` サンプラーに統合する、あるいはその入力に手を触れない。** このレーンには、まさに同じ問いのためのバックグラウンドサンプラーがすでにあります。`workflow_dispatch` の入力を設定すると、`smoke` の実行中は2秒ごとに `adb shell dumpsys cpuinfo` が `runs/cpuinfo.log` へ追記されます。これで、フレークを追う目的の手動実行を、ホストの負荷と突き合わせられます。

  第3層は、**これと共存し、削除しません**。両者は別の側から別のものを測っているからです。`poll_cpuinfo` は adb 越しに**ゲスト**の見え方を読みます。第3層は**ホスト**自身の負荷を読みます。

  `poll_cpuinfo` が `pull_request` と `merge_group` のすべての実行で無効になっている理由も、そのままは当てはまりません。あの入力が無効なのは、「2秒ごとのポーリング自体が、まさに CPU 競合を測ろうとしているレーンに対して継続的な adb トラフィックとホスト負荷を加える」からです。これは観測者効果です。約20秒ごとのホスト側 `top` / `free` に、この効果はありません。adb トラフィックをまったく発生させず、頻度も10分の1です。

  そのため第3層は、`poll_cpuinfo` がオプトインであるのに対して、常時有効です。実装者は、一方が他方を置き換えるものではなく、補い合うものとして読んでください。
- **iOS レーンと Android レーンで composite action を1つに共有する。** 退けます。`collect-ios-diagnostics` は、`simctl` / CoreSimulator / macOS の unified log を読みます。`collect-android-diagnostics` は、adb / logcat / Linux ホストを読みます。両者の背後にあるツールに共通する面はありません。1つの action にまとめれば、共有ロジックではなく `if backend == …` の分岐になってしまいます。2つの action は別々のままです。共有するのは、常時段と失敗時段の分割、`start` / `collect` のテレメトリフェーズという設計と、第1層の環境変数名だけです。
- **`BAJUTSU_STALL_DIAGNOSTICS` を定義する [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics-ja.md) の着地を待ってから、本提案に着手する。** 退けます。BE-0361 自体が、まだ実装されていない「提案」です。コードを共有する理由もないのに、Android 側のゼロ収集というギャップをそこへ従属させれば、ギャップが無期限に残りかねません。2つの提案が共有するのは、変数「名」の規約だけです。互いに依存するモジュールはないため、どちらを先に実装しても構いません。先に着地したほうが、`BAJUTSU_STALL_DIAGNOSTICS` の共有ドキュメントを定義します。後から着地するほうは、自分のバックエンドのトリガー地点を、その既存の名前へ配線します。
- **adb バックエンドに本物の `AdbBackendCrashError(base.BackendCrashError)` を持たせ、ストールプローブをそこへ配線して、本提案の「動機」が示すクラッシュ検知のギャップ自体を閉じる。** スコープ外とします。adb 側の障害がどの程度深刻なら、現在のリースを捨ててシナリオ全体を再試行すべきか。これを決めるのは回復セマンティクスの問いです。BE-0353 のクラッシュ起因リトライが、このバックエンドについて実際には何をトリガーに動くべきか、という問いになります。診断収集の問いではありません。この2つを混ぜると、本提案が「証拠をもっと集める」に必要な範囲をはるかに超えた挙動変更の責任を負うことになります。検知は、いずれ別の提案が担えます。本提案の第1層は、まだ存在しないシグナルを待つのではなく、adb ドライバが今日すでに送出している障害シグナル（`AdbResidentError`）にフックします。
- **`adb bugreport` を失敗時だけでなく毎回実行する。** 退けます。bugreport は数十秒かかり、数メガバイト規模のアーカイブを作ります。macOS の `sysdiagnose` よりはるかに軽量です（BE-0361 は、まさにこの理由で `sysdiagnose` を先送りしています）。それでも、6ジョブすべての緑の実行のたびに払うには、現実のコストです。常時段と失敗時段を分けることで、軽量な段は無条件に残します。重量級のコレクタは、本当に必要な実行だけに使います。
- **ジョブ全体で `adb logcat` のリングバッファをストリームし続ける。** シナリオ単位の `deviceLog` 区間証拠（`bajutsu/evidence/intervals.py` の `start_logcat`）が、すでにシナリオ自身のウィンドウをストリームしています。ジョブ全体の2本目のストリームは、その内容の大半を重複させるだけです。常時段の `logcat -d` ダンプは、デバイス自身が保持しているバッファを事後に1回読むだけで、同じ利益をより低いコストで与えます。BE-0361 が iOS の unified log をジョブ全体でストリームすることを退ける理由と、同じです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の `MECE` な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] Unit 1 — Android の `video` プロバイダの録画バイト増加監視
- [x] Unit 2 — ストール時プローブのフック（階層読み取りのフォールバック、新しいバイト無し警告）
- [x] Unit 3 — `scripts/collect_android_diagnostics.sh` と各ジョブの `script:` への配線
- [x] Unit 4 — composite action `collect-android-diagnostics`（ホストテレメトリのみ）
- [x] Unit 5 — ドキュメント（`docs/ci.md` とその `ja` ミラー）
- [x] Unit 6 — テスト

[BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics-ja.md) が先に着地しました。そのため、本項目は2つのトリガーを、隣に別のモジュールを足すのではなく、BE-0361 の `bajutsu/stall_diagnostics.py` に配線しました。*検討した代替案* が本項目を BE-0361 に依存させることを退けたときに見込んでいた結果です。

この配線の過程で、どちらの項目も名前を与えていなかった継ぎ目が見えました。いつ捕捉するか、捕捉が何を費やしてよいか、どこへ書くかは、両バックエンドで同一です。何を読むかには、共通のコマンドが1つもありません。そこで Unit 2 は、この継ぎ目でモジュールを分けました。

- `capture`：オプトインの門、トリガーごとの上限、サマリを引き受けます。
- `ProbeSet`：各バックエンドが持ち寄ります。

2つのレーンのプローブは、`simulator_probes` と `device_probes` になりました。3つめのバックエンドは、プローブ一式を足すだけで済みます。Unit 5 が2つのレーンの `docs/ci.md` の記述を1つの節にまとめたのも、同じ理由です。

Unit 3 と Unit 4 は、上記 *詳細設計* が除外した `uiautomator (codegen)` も対象にしました。あの除外は、収集の**仕組み**から論じたものです。つまり、「このジョブは `runs/` に何も書かないので相乗りできない」という話です。これを、ジョブ自身の**必要性**と取り違えていました。アップロードの実態は、むしろ逆を示します。`codegen` がアップロードする経路は、どれも Gradle が生成テストを実行して初めて生まれます。その手前で落ちると、`if-no-files-found: ignore` に当たり、何もアップロードできませんでした。手前で落ちる原因は、AVD の boot の固まり、codegen やビルドの失敗、ジョブ自身の `timeout-minutes` の発火です。

収集の出力先は、制約ではなく引数です。そこで、このジョブにも同じ2段とホストのサンプラをそのまま入れ、アップロードに `runs/` を足しました。

第1層は、`codegen` ではフックが2つではなく1つです。テスト時に adb ドライバが走らないため、ホスト側のレコーダだけが発火します。そのレコーダは [`demos/showcase/android/screenrecord.py`](../../demos/showcase/android/screenrecord.py) で、録画のバイト増加を確認するようにしました。描画が固まった状態はこのレーンの既知のフレークなので、こちらが適切なフックでもあります。

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

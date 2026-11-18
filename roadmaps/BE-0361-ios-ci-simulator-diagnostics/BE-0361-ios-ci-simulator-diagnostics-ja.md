[English](BE-0361-ios-ci-simulator-diagnostics.md) · **日本語**

# BE-0361 — iOS の CI 失敗の原因究明に必要な多層の診断ログを収集する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0361](BE-0361-ios-ci-simulator-diagnostics-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0361") |
| 実装 PR | [#1619](https://github.com/bajutsu-e2e/bajutsu/pull/1619) |
| トピック | CI / build infrastructure |
| 関連 | [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience-ja.md)、[BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md)、[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md)、[BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement-ja.md)、[BE-0346](../BE-0346-video-timing-sync/BE-0346-video-timing-sync-ja.md)、[BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md) |
<!-- /BE-METADATA -->

## はじめに

GitHub Actions 上の継続的インテグレーション（CI）の iOS レーンは、常駐 XCUITest ランナーの実行中クラッシュで失敗し続けています。顕著なのは、`.github/workflows/ios-e2e.yml` の `run` と `actuation` のジョブです。今日アップロードされるアーティファクトを見ても、ランナーのクラッシュの理由はわかりません。

ランナーは XCTest ホストです。`bajutsu` が macOS ランナー上で `xcodebuild test-without-building` により起動し（`bajutsu/platform_lifecycle/environments/xcuitest.py`）、ループバックの Hypertext Transfer Protocol（`HTTP`）チャネル越しに駆動します。ランナーが実行中に死ぬと、パイプラインは [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md) が加えたクラッシュ回復予算の下で再起動して再試行します。

この予算により、劣化していく実行は `timeout-minutes` まで沈黙する代わりに、大きな音を立てて失敗するようになりました。しかし、修正方針を決める問いである「何がランナーを殺したのか」には、この予算は答えられません。

本提案は、多層の診断ログ収集を追加します。次に失敗した実行そのものが、原因究明に必要な証拠を携えて戻ってくるようにします。層は次の3つの場所に置きます。

- `bajutsu` の内部
- Simulator と CoreSimulator
- macOS ホスト

収集物はすべて `runs/` 配下に置かれます。収集対象のオンデバイスジョブが `runs/` をすでにアップロードしているので、新しいアップロード配線は不要です。決定論的な判定経路には一切触れません。

## 動機

捕捉済みのランナーログは、2026-08-12 の失敗実行をすでに1つの署名へ絞り込んでいます（[BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience-ja.md) の捕捉で、`runs/runner-logs/` に保全されます）。

1. ランナー自身の XCTest 失敗は `Failed to get screenshot: Timed out while requesting screenshot.` です。同系統に `cannot request screenshot data because it has an empty frame` と `Lost connection to the application` があります。
2. この後テストメソッドが終了し、`xcodebuild` はコード 65 で終了します。
3. Python 側は `GET /screenshot` のタイムアウトと `Connection refused` を観測し、実行中クラッシュを宣言します。

2つの観測が、原因は `bajutsu` 自身のコードではなく、その下のプラットフォームにあると示しています。

- **録画パイプラインが死んでいます。** `recordVideo produced no new bytes` の警告は、赤い実行でも緑の実行でも**すべての**シナリオで出ています。GitHub Actions 上では、Simulator の録画パイプラインが1バイトも生成できていません。つまり、赤い実行で致命化するスクリーンショットサービスは、すべての実行で劣化しています。
- **OS 側のアーティファクトが空です。** `.github/actions/bajutsu-e2e/action.yml` の既存ステップ「Collect crash diagnostics」は、毎回0件です。どのプロセスもクラッシュしていないため、CI が今日集めている唯一の OS 側アーティファクトは、この失敗クラスに対して構造的に空になります。

今日のアーティファクトでは、次の4つの仮説のどれが成り立つかを区別できません。

1. Simulator の描画またはスクリーンショットサービスが固まる。録画パイプラインが死んでいることから、最有力です。
2. 仮想化された macOS ホスト上で、ストールの瞬間に Simulator が使える `CPU` 時間やメモリが枯渇している。
3. アプリのプロセス自体が殺される。`empty frame` と `Lost connection` の変種が示唆します。
4. 失敗が特定のランナーイメージやハードウェア世代に集中している。

どの仮説にも、失敗の瞬間には存在するのに、人間が見る頃には消えている証拠が対応します。

- ランナーの `xcodebuild` が書く XCTest の result bundle。そのパスはランナーログに印字された後、破棄されます。今日 `.xcresult` をアップロードするのは `codegen` ジョブだけです。
- スクリーンショットを担うプロセス群の unified log のエントリ
- ストール時点のホスト負荷
- 固まったプロセスのスレッドサンプル

これらの収集は、10倍課金の macOS ランナーで診断不能な赤い実行をもう1回払うことに比べれば、安価です。

## 詳細設計

収集は3層です。第1層は `bajutsu` の中に置きます。ストールが「いつ」起きたか、どの起動が失敗したかを知っているのは、実行中のプロセスだけだからです。残る2層は CI に置きます。この2層は、`bajutsu` が触るべきでないホスト状態を読むからです。どの層も `runs/` 配下へ書き込むため、既存の「Upload run artifacts」ステップが追加の配線なしで収集物を運びます。

### 第1層は bajutsu だけが捕捉できるもの

**ランナーの XCTest result bundle。** 新しい環境変数 `BAJUTSU_XCUITEST_RESULT_BUNDLES` がディレクトリを指すとき、`_spawn_runner`（`bajutsu/platform_lifecycle/environments/xcuitest.py`）は `-resultBundlePath` 引数を加えます。起動ごとに1つのバンドルを、ランナーポートで鍵付けして書きます。ランナーログがすでに使っている鍵付けと同じなので、再起動が先行の起動のバンドルを上書きすることはありません。バンドルには、testmanagerd 自身の観測が記録されます。正確な失敗内容、そのタイムスタンプ、添付物です。

保全の方針は、この環境変数から決まります。ディレクトリを指す環境変数そのものが「オペレータがバンドルを求めた」という意思表示なので、明示的な `BAJUTSU_XCUITEST_RUNNER_LOG` ディレクトリと同様に、すべてのバンドルを保持します。BE-0319 が健全な終了時に削除するのは、環境変数が未設定のときのデフォルトの捕捉だけです。この機能には、そのようなデフォルトがありません。

正直な限界が1つあります。`xcodebuild` は、テスト実行の終了時にバンドルを確定します。上記のコード 65 の失敗では、まさに確定まで到達します。一方、ハング中に `bajutsu` が kill したランナーは、不完全なバンドルを残しうるため、そこではベストエフォートの収集になります。

**ストール時プローブ。** プローブのトリガーは2つあります。

- チャネルが実行中クラッシュを宣言した瞬間（`bajutsu/drivers/xcuitest.py` で一時障害リトライ予算が尽きる箇所）
- 録画監視が `recordVideo produced no new bytes` を記録した瞬間（`bajutsu/evidence/intervals.py`）

どちらの瞬間にも、仮説 (1) と (2) を切り分ける状態がまだ存在し、まさに消えようとしています。新しい環境変数 `BAJUTSU_STALL_DIAGNOSTICS` がディレクトリを指すとき、この2つのトリガー地点は、そこへ向けて上限付きかつベストエフォートで捕捉します。捕捉するのは次の3つです。

- 所要時間を計測した `xcrun simctl io <udid> screenshot`。simctl 自身の経路もストールするなら、固まっているのはランナーではなく描画サービスです。
- ランナーホストプロセスと、Simulator のスクリーンショットを担うプロセス群への `sample`
- `ps aux` と `vm_stat` のスナップショット

各コマンドは短いサブプロセスタイムアウトを持ち、失敗は握りつぶされます。捕捉回数は実行ごとに上限（最初の数回のストールだけ）を持つため、クラッシュループに陥った実行が、ディスクや実行時間を食い尽くすことはありません。2つの環境変数が未設定なら、挙動は今日と完全に同じです。フックへのオプトインは CI の役目です。

### 第2層は CI から集める Simulator と CoreSimulator の状態

新しい composite action `.github/actions/collect-ios-diagnostics` が OS 側の収集を担い、Simulator を駆動する7つの macOS ジョブが、それぞれ自前のシェルステップを育てることを防ぎます。この action は、`bajutsu-e2e/action.yml` 内の「Collect crash diagnostics」ステップを置き換えます。その action を経由しないジョブ（`conformance` / `fault-injection` / `visual`）にも配線されます。

Simulator を起動する残り1つの macOS ジョブである `codegen` は、対象外です。`codegen` は自分の `.xcresult` だけをアップロードし、収集物を載せる `runs/` アーティファクトを持たないからです。実行は2段階です。

- **常時**（軽量で、毎回実行します）：
  - `~/Library/Logs/CoreSimulator/CoreSimulator.log` と、起動済みデバイスの `~/Library/Logs/CoreSimulator/<UDID>/` ディレクトリをコピーします。
  - クラッシュレポートの走査対象を、`*.ips` / `*.crash` から `*.diag` / `*.spin` / `*.hang` / `JetsamEvent*` へ広げます。システム側の `/Library/Logs/DiagnosticReports` も加えます。
  - 環境スナップショットとして、`sw_vers` / `sysctl hw.model hw.ncpu hw.memsize kern.hv_vmm_present` / `system_profiler SPDisplaysDataType` / `xcodebuild -version` / `xcrun simctl list -j` を記録します。これは、仮説 (4) が必要とする実行横断の集計キーです。
- **失敗時のみ**（重量級）：
  - タイムアウト付きの `xcrun simctl diagnose` を実行します。CoreSimulator とデバイス状態に対する、Apple 純正のコレクタです。
  - unified log は、`log show --last <window> --predicate` で対象を絞って抽出します。抽出対象は、描画とスクリーンショットを担うプロセス群です。`backboardd` / `SpringBoard` / `testmanagerd` と CoreSimulator のサービスプロセスが該当します。
  - `bajutsu-e2e/action.yml` の内部では、この段を実行ステップ自身の結果でゲートします。pytest のレーンでも、呼び出し側が実行ステップの結果でゲートします。`failure()` は `with:` の値としては使えないためです。

実装の過程で、この設計の誤りが5つ実測で判明しました。どれも、放置すれば「静かに何も集めない収集器」を出荷することになります。それは、この提案が終わらせようとしている失敗そのものです。

- **ゲストはホストのログに書き込みません。** この設計の初期版は、Simulator のゲストプロセスがホストの unified log に書き込むと述べていました。この記述は誤りです。起動済みデバイスで `SpringBoard` / `backboardd` / `testmanagerd` をホスト側の `log show` で絞ると、見出し行だけが返ります。これらのエントリは、デバイス自身のログストアにあります。そこで収集は、抽出を**2つ**走らせます。
  - 1つはホスト側で、CoreSimulator のサービスプロセス向けです。プロセス名は `CoreSimulatorService` ではなく `com.apple.CoreSimulator.CoreSimulatorService` で、サブシステムは `CONTAINS` での一致が必要です。
  - もう1つは、`simctl spawn` を通してゲストの内側で走らせます。

  30分の窓での実測は、ホスト側が34.5k行、ゲスト側が11.4k行で、いずれも約1秒です。
- **`simctl diagnose` は標準入力で止まり、`--output` のディレクトリを破壊します。** 同意通知を表示して、改行を待ちます。CI ステップの標準入力は `/dev/null` なので、そこで EOF を読み、何も収集せずに exit 0 します。さらに `--output` をアーカイブの*基準パス*として扱います。そのディレクトリを `<パス>.tar.gz` で置き換え、中にあった他のものを削除します。収集先ディレクトリを指定すると、収集物の残りが消えます。実測では、起動済みデバイス1台で約15秒、22〜78MB でした。フラグは `--flag=value` の形式が必須です。
- **`CoreSimulator.log` には上限がありません。** 丸ごとコピーすると、長寿命のホストでは 183MB の成果物になります。しかも、それが*常時*段です。収集では末尾だけを取ります。
- **捕捉上限を共有にすると、肝心のトリガーが飢えます。** 上の動機に記したとおり、`recordVideo produced no new bytes` は、緑の実行を含む全シナリオで発火します。1つのジョブは、1つの `bajutsu run` プロセスで十数個のシナリオドキュメントを走らせます。したがって、プロセスごとに3回という単一の上限は、3番目のシナリオの映像警告で使い切られます。ランナーのクラッシュは、この捕捉が説明したい失敗であり、デバイスの id を伴って到達する唯一のトリガーです。そのトリガーは、残りがない状態で届きます。上限はトリガーごとに持たせます。捕捉を断ったことは、debug ではなく warning で記録します。このレーンはログハンドラを設定しないため、debug では記録がまったく残らないからです。
- **捕捉は、自分のものでない時計に課金されます。** BE-0353 の `CrashRecoveryBudget` は、実時間の締切です。*最初の*クラッシュ時点で置かれ、以降のクラッシュごとに再確認されます。したがって、捕捉が使う1秒は、復帰が使えなくなる1秒です。この変更の最初の CI 実行が、それを示しました。`visual` ジョブが300秒の復帰締切を75秒超過し、その窓の内側に最大30秒の捕捉が2回入っていました。これは、復帰可能な劣化を失敗した実行に変える診断であり、観測者がしてはならないことです。1回あたりの予算は、そのレーンの300秒に対して10秒とします。スクリーンショットの上限は5秒とします。ランナー自身の読み取りタイムアウトが15秒なので、`simctl` の経路が5秒で応答しなければ、それだけで問いには答えています。裸の数値ではなく、レーンが設定している予算との関係を、テストで固定します。

同じ実測から、小さな形が2つ決まりました。

- `xcodebuild` は、`-resultBundlePath` が既存だと起動そのものを拒みます。そのため Unit 1 は、自分の鍵にある残骸を先に消します。そうしないと、エフェメラルポートの再利用が、診断機能を起動失敗に変えてしまいます。
- `start` フェーズは、呼び出す各ジョブではなく `bajutsu-e2e/action.yml` の内側に置きます。`bundled-runner` がこの action を2回呼ぶため、ジョブ側に置くと、1回目の `collect` がサンプラを止めてしまうからです。

### 第3層は時系列のホストテレメトリ

ストールとホストの資源枯渇の相関は、ストールが起きた瞬間のホスト負荷が記録にあって初めて取れます。そこで同じ composite action に `start` フェーズを設け、消費側ジョブが `bootstatus` の直後に呼びます。`start` フェーズは、次の2つを行います。

- バックグラウンドのサンプラを起動します。サンプラは、約20秒ごとに `top` / `vm_stat` / `memory_pressure` の出力を `runs/diagnostics/host-telemetry.log` へ追記する定期ループです。
- 描画パイプラインの一発プローブを実行します。プローブは、所要時間を計測した `simctl io screenshot` と、5秒間の `recordVideo` です。そのバイト数が、「録画パイプラインは最初から死んでいたのか」にジョブ単位で答えます。

`collect` フェーズがサンプラを停止します。サンプラは、判定経路の外にいる観測者です。その間隔は待機ではなくサンプリング周期なので、実行ループにおける固定 sleep を禁じる第2の最重要原則には触れません。

### 作業分解（`MECE`）

相互に排他的で全体として網羅的（Mutually Exclusive, Collectively Exhaustive、`MECE`）な作業単位は次のとおりです。

1. **Result bundle。** `BAJUTSU_XCUITEST_RESULT_BUNDLES` の背後で、`_spawn_runner` に `-resultBundlePath` を追加します。ランナーログと同じポート単位の鍵付けを使い、すべてのバンドルを保持します（環境変数がオペレータの意思表示だからです）。`ios-e2e.yml` は、この変数を `runs/runner-logs/` 配下へ向けます。
2. **ストール時プローブ。** `BAJUTSU_STALL_DIAGNOSTICS` の背後に、上限付きの捕捉モジュールを追加します。2つのトリガー地点（チャネルのクラッシュ宣言と録画のバイト無し警告）と、実行ごとの捕捉回数上限を実装します。`ios-e2e.yml` にオプトインを加えます。
3. **composite action `collect-ios-diagnostics`。** 常時段、失敗時段、`start` フェーズを実装します。`bajutsu-e2e/action.yml` の「Collect crash diagnostics」ステップを置き換えます。`conformance` / `fault-injection` / `visual` へ配線します。
4. **ドキュメント。** `docs/ci.md` とその `docs/ja/` ミラーに、診断の節を設けます。段の構成、各収集物が `runs/` のどこに置かれるか、4つの仮説に対して収集物をどう読むかを記します。
5. **テスト。** テストは3つを対象にします。
   - `bajutsu` 側の2つのフックのユニットテスト。環境変数が設定されたときに限り、起動 argv が `-resultBundlePath` を得ます。未設定ならプローブは no-op です。設定時は、上限付きかつベストエフォートです。
   - `tests/test_e2e_changes.py`。iOS レーンの正のパスリストに、新しい action を名指しすることを固定します。これがないと、その action への変更はどのレーンも発火させません。
   - composite action 自身のシェル。この unit は当初、静的なゲートがこのシェルを検査すると想定していましたが、実際には検査が**ありません**。`actionlint` が検査するのは `.github/workflows/` だけで、`action.yml` を指定すると `jobs` セクションがないと報告します。`make lint-sh` は、名指しした `.sh` ファイルの一覧に対して `shellcheck` を走らせるので、`.github/actions/` 配下には届きません。

   この穴から、実際に欠陥が出荷されました。action の番犬が、固まった `simctl` のスクリーンショットを SIGKILL し、`wait` がその子の 137 を返しました。Actions 自身がシェル行に errexit を付けます。`bash --noprofile --norc -e -o pipefail` で、スクリプト側の `set -uo pipefail` では解除できません。この errexit がステップを落とし、本来のテストステップを skip させ、iOS の5ジョブを赤くしました。

   そこでテストは、action 自身の `run:` ブロックを、まさにその処理系とフラグの下で、ハングする `xcrun` のスタブとともに実行します（`tests/test_collect_ios_diagnostics_action.py`）。固定するのは、ただ1つの性質です。診断のステップはジョブを落とせない、ということです。このテストを書く過程で、上限のない呼び出しがもう1つ見つかりました。*常時*段の `simctl list` です。CoreSimulator が固まっていれば、すべてのジョブを止めていたはずのものです。すべての composite action について静的な lint の穴を閉じるのは、引き続き別途の後続作業とします。

### 最重要原則の維持

- **AI は判定しない。** どの層も証拠を集めるだけです。本提案が触れるどの経路にもモデル呼び出しは入らず、収集物が判定に流れ込むこともありません。
- **決定論を最優先する。** プローブは、タイムアウト付きの有界なサブプロセス呼び出しであり、sleep ではありません。テレメトリのサンプラは、実行ループの外から観測します。合否の決め方は従来と一切変わりません。
- **アプリ非依存。** 本提案は対象アプリを読みません。フックはデバイスとランナープロセスに、CI の action はホストに鍵付けされ、ターゲットごとの分岐はどこにもありません。

## 検討した代替案

- **OS 側の状態収集の代わりに `BAJUTSU_LOG_LEVEL` を上げる。** 主たる手段としては退けます。失敗している層は Simulator のスクリーンショットサービスであり、`bajutsu` のプロセスの外にあります。`bajutsu` 側をどれだけ饒舌にしても、記録されません。ログ増量が同乗するのは構いませんが、第2層と第3層の代替にはなりません。
- **失敗時に `log collect` の完全アーカイブ、または `sysdiagnose` を取る。** 先送りします。対象期間全体の `.logarchive` は、ジョブあたり数百 MB に達します。`sysdiagnose` は、数分かけてギガバイト級を生成します。一方、対象を絞った `log show` の抽出は、失敗署名がすでに名指ししているプロセス群を覆います。抽出で不足するとわかったときに限り、再検討します。
- **ジョブ全体で unified log をストリームし続ける。** 退けます。ステップ単位の `device.log` という区間証拠が、シナリオ実行中のゲストログのストリームをすでに行っています。失敗時の遡及的な `log show` は、長寿命の追加プロセスなしで、同じホスト側エントリを得られます。
- **スクリーンショット失敗を診断する代わりに、ランナーが失敗を生き延びるようにする。** 意図的にスコープ外とします。`/screenshot` を `simctl io screenshot` 経由へ迂回することや、Swift 側で失敗を非致命化することは「修正」です。診断が原因を確定する前にどれかを選ぶのは、当て推量になります。証拠が揃った後の後続提案が、修正を担います。

## 進捗

> 作業の進行に合わせてここを最新に保ってください。チェックリストは「詳細設計」の `MECE` な
> 作業分解を鏡写しにします（作業単位ごとに1つのボックス）。ログには何がいつ変わったかを
> 古い順に、PR へのリンク付きで記録します。

- [x] Unit 1 — ランナー起動ごとの result bundle
- [x] Unit 2 — ストール時プローブのフック
- [x] Unit 3 — composite action `collect-ios-diagnostics` とその配線
- [x] Unit 4 — ドキュメント（`docs/ci.md` とその `ja` ミラー）
- [x] Unit 5 — テスト

## 参考

- [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience-ja.md)：本提案の result bundle と保全方針が拡張する、ランナー出力の捕捉
- [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md) / [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement-ja.md)：この失敗を「大きな音を立てる」ようにしたが、まだ診断可能にはしていないクラッシュ回復予算と wedge 検出
- [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md)：収集された証拠がその必要性を説明するはずの、試行間のデバイス修復（再起動と置換）
- [BE-0346](../BE-0346-video-timing-sync/BE-0346-video-timing-sync-ja.md)：本提案の2つのストールトリガーの片方である `recordVideo` バイト無し警告を持つ、動画アンカー補正
- [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)：Simulator レーンの flakiness の履歴
- [`bajutsu/platform_lifecycle/environments/xcuitest.py`](../../bajutsu/platform_lifecycle/environments/xcuitest.py)：Unit 1 が拡張する継ぎ目である `_spawn_runner`
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py)：Unit 2 の第1トリガーであるクラッシュ宣言を持つチャネル
- [`bajutsu/evidence/intervals.py`](../../bajutsu/evidence/intervals.py)：Unit 2 の第2トリガーであるバイト無し警告を持つ録画監視
- [`.github/actions/bajutsu-e2e/action.yml`](../../.github/actions/bajutsu-e2e/action.yml)：Unit 3 が置き換えるクラッシュ診断ステップ
- [`.github/workflows/ios-e2e.yml`](../../.github/workflows/ios-e2e.yml)：全層にオプトインするジョブ群を持つレーン

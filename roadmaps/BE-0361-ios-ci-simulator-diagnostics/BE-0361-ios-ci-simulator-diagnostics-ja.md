[English](BE-0361-ios-ci-simulator-diagnostics.md) · **日本語**

# BE-0361 — iOS の CI 失敗の原因究明に必要な多層の診断ログを収集する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0361](BE-0361-ios-ci-simulator-diagnostics-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0361") |
| 実装 PR | （未定） |
| トピック | CI / build infrastructure |
| 関連 | [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience-ja.md)、[BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md)、[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md)、[BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement-ja.md)、[BE-0346](../BE-0346-video-timing-sync/BE-0346-video-timing-sync-ja.md)、[BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md) |
<!-- /BE-METADATA -->

## はじめに

GitHub Actions 上の継続的インテグレーション（CI）の iOS レーンは、常駐 XCUITest ランナーの
実行中クラッシュで失敗し続けています（`.github/workflows/ios-e2e.yml` の `run` と `actuation`
のジョブが顕著です）。そして、CI が今日アップロードするアーティファクトからは、その理由が
わかりません。ランナーは `bajutsu` が macOS ランナー上で `xcodebuild test-without-building`
により起動する XCTest ホストで（`bajutsu/platform_lifecycle/environments/xcuitest.py`）、
ループバックの Hypertext Transfer Protocol（`HTTP`）チャネル越しに駆動されます。ランナーが
実行中に死ぬと、パイプラインは
[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md)
が加えたクラッシュ回復予算の下で再起動して再試行します。この予算により、劣化していく実行は
`timeout-minutes` まで沈黙する代わりに大きな音を立てて失敗するようになりました。しかし、
修正方針を決める問いである「何がランナーを殺したのか」には、この予算は答えられません。
本提案は多層の診断ログ収集を追加します。層は `bajutsu` の内部、Simulator と CoreSimulator、
そして macOS ホストの3つで、次に失敗した実行そのものが原因究明に必要な証拠を携えて戻って
くるようにします。収集物はすべて `runs/` 配下に置かれ、収集対象のオンデバイスジョブが
`runs/` をすでにアップロードしているので、新しいアップロード配線は不要です。決定論的な
判定経路には一切触れません。

## 動機

捕捉済みのランナーログは、2026-08-12 の失敗実行をすでに1つの署名へ絞り込んでいます
（[BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience-ja.md)
の捕捉で、`runs/runner-logs/` に保全されます）。ランナー自身の XCTest 失敗は
`Failed to get screenshot: Timed out while requesting screenshot.` か、その同系統の
`cannot request screenshot data because it has an empty frame` と
`Lost connection to the application` です。この後テストメソッドが終了し、`xcodebuild` は
コード 65 で終了します。Python 側は `GET /screenshot` のタイムアウトと `Connection refused`
を観測し、実行中クラッシュを宣言します。2つの観測が、原因は `bajutsu` 自身のコードではなく
その下のプラットフォームにあると示しています。第一に、`recordVideo produced no new bytes`
の警告は、赤い実行でも緑の実行でも**すべての**シナリオで出ています。GitHub Actions 上では
Simulator の録画パイプラインが 1 バイトも生成できていません。つまり、赤い実行で致命化する
スクリーンショットサービスは、実はすべての実行で劣化しています。第二に、
`.github/actions/bajutsu-e2e/action.yml` の既存ステップ「Collect crash diagnostics」は毎回
0 件です。どのプロセスもクラッシュしていないため、CI が今日集めている唯一の OS 側
アーティファクトは、この失敗クラスに対して構造的に空になります。

今日のアーティファクトでは、次の4つの仮説を区別できません。(1) Simulator の描画または
スクリーンショットサービスが固まる（録画パイプラインが死んでいることから、最有力です）。
(2) 仮想化された macOS ホスト上で、ストールの瞬間に Simulator が使える `CPU` 時間やメモリが
枯渇している。(3) アプリのプロセス自体が殺される（`empty frame` と `Lost connection` の
変種が示唆します）。(4) 失敗が特定のランナーイメージやハードウェア世代に集中している。
どの仮説にも、失敗の瞬間には存在するのに人間が見る頃には消えている証拠が対応します。
ランナーの `xcodebuild` が書く XCTest の result bundle がその筆頭です（そのパスはランナー
ログに印字された後、破棄されます。今日 `.xcresult` をアップロードするのは `codegen` ジョブ
だけです）。ほかに、スクリーンショットを担うプロセス群の unified log のエントリ、ストール
時点のホスト負荷、固まったプロセスのスレッドサンプルがあります。これらの収集は、10 倍課金の
macOS ランナーで診断不能な赤い実行をもう1回払うことに比べれば安価です。

## 詳細設計

収集は3層です。第1層は `bajutsu` の中に置きます。ストールが「いつ」起きたか、どの起動が
失敗したかを知っているのは実行中のプロセスだけだからです。残る2層は CI に置きます。これらは
`bajutsu` が触るべきでないホスト状態を読むからです。どの層も `runs/` 配下へ書き込むため、
既存の「Upload run artifacts」ステップが追加の配線なしで収集物を運びます。

### 第1層は bajutsu だけが捕捉できるもの

**ランナーの XCTest result bundle。** 新しい環境変数 `BAJUTSU_XCUITEST_RESULT_BUNDLES` が
ディレクトリを指すとき、`_spawn_runner`
（`bajutsu/platform_lifecycle/environments/xcuitest.py`）は `-resultBundlePath` 引数を加え、
起動ごとに1つのバンドルをランナーポートで鍵付けして書きます。ランナーログがすでに使っている
鍵付けと同じなので、再起動が先行の起動のバンドルを上書きすることはありません。バンドルには
testmanagerd 自身の観測が記録されます。すなわち正確な失敗内容、そのタイムスタンプ、
添付物です。保全の方針は次のとおりです。ディレクトリを指す環境変数そのものが「オペレータが
バンドルを求めた」という意思表示なので、明示的な `BAJUTSU_XCUITEST_RUNNER_LOG` ディレクトリ
と同様に、すべてのバンドルを保持します。BE-0319 が健全な終了時に削除するのは、環境変数が
未設定のときのデフォルトの捕捉だけで、この機能にはそのようなデフォルトがありません。
正直な限界を1つ述べます。
`xcodebuild` はテスト実行の終了時
にバンドルを確定します。上記のコード 65 の失敗ではまさに確定まで到達します。一方、ハング中
に `bajutsu` が kill したランナーは不完全なバンドルを残しうるため、そこではベストエフォート
の収集になります。

**ストール時プローブ。** トリガーは2つあります。チャネルが実行中クラッシュを宣言した瞬間
（`bajutsu/drivers/xcuitest.py` で一時障害リトライ予算が尽きる箇所）と、録画監視が
`recordVideo produced no new bytes` を記録した瞬間（`bajutsu/evidence/intervals.py`）です。
どちらの瞬間にも、仮説 (1) と (2) を切り分ける状態がまだ存在し、まさに消えようとして
います。新しい環境変数
`BAJUTSU_STALL_DIAGNOSTICS` がディレクトリを指すとき、この2つのトリガー地点は、そこへ向けて
上限付きかつベストエフォートで捕捉します。捕捉するのは次の3つです。第一に、所要時間を計測
した `xcrun simctl io <udid> screenshot` です（simctl 自身の経路もストールするなら、固まって
いるのはランナーではなく描画サービスです）。第二に、ランナーホストプロセスと Simulator の
スクリーンショットを担うプロセス群への `sample` です。第三に、`ps aux` と `vm_stat` の
スナップショットです。
各コマンドは短いサブプロセスタイムアウトを持ち、失敗は握りつぶされます。捕捉回数は実行ごと
に上限（最初の数回のストールだけ）を持つため、クラッシュループに陥った実行がディスクや
実行時間を食い尽くすことはありません。どちらの環境変数も、未設定なら挙動は今日と完全に
同じです。フックへのオプトインは CI の役目です。

### 第2層は CI から集める Simulator と CoreSimulator の状態

新しい composite action `.github/actions/collect-ios-diagnostics` が OS 側の収集を担い、
Simulator を駆動する 7 つの macOS ジョブがそれぞれ自前のシェルステップを育てることを
防ぎます。この action は `bajutsu-e2e/action.yml` 内の「Collect crash diagnostics」ステップ
を置き換え、その action を経由しないジョブ（`conformance` / `fault-injection` / `visual`）
にも配線されます。Simulator を起動する残り 1 つの macOS ジョブである `codegen` は対象外
です。`codegen` は自分の `.xcresult` だけをアップロードし、収集物を載せる `runs/`
アーティファクトを持たないからです。実行は 2 段階です。

- **常時**（軽量で、毎回実行します）。`~/Library/Logs/CoreSimulator/CoreSimulator.log` と、
  起動済みデバイスの `~/Library/Logs/CoreSimulator/<UDID>/` ディレクトリをコピーします。
  クラッシュレポートの走査対象を `*.ips` / `*.crash` から `*.diag` / `*.spin` / `*.hang` /
  `JetsamEvent*` へ広げ、システム側の `/Library/Logs/DiagnosticReports` も加えます。さらに
  環境スナップショットとして `sw_vers` / `sysctl hw.model hw.ncpu hw.memsize
  kern.hv_vmm_present` / `system_profiler SPDisplaysDataType` / `xcodebuild -version` /
  `xcrun simctl list -j` を記録します。これは仮説 (4) が必要とする、実行横断の集計キーです。
- **失敗時のみ**（重量級）。タイムアウト付きの `xcrun simctl diagnose`（CoreSimulator と
  デバイス状態に対する Apple 純正のコレクタ）を実行し、unified log は
  `log show --last <window> --predicate` で対象を絞って抽出します。抽出対象は描画と
  スクリーンショットを担うプロセス群、すなわち `backboardd` / `SpringBoard` /
  `testmanagerd` と CoreSimulator のサービスプロセスです。`bajutsu-e2e/action.yml` の内部では
  この段を実行ステップ自身の結果でゲートします。pytest のレーンでも呼び出し側が実行ステップの
  結果でゲートします。`failure()` は `with:` の値としては使えないためです。

実装の過程で、この設計の誤りが3つ実測で判明しました。どれも、放置すれば「静かに何も集めない
収集器」を出荷することになり、この提案が終わらせようとしている失敗そのものになります。

- **ゲストはホストのログに書き込みません。** 上の「Simulator のゲストプロセスはホストの
  unified log に書き込む」という記述は誤りです。起動済みデバイスで `SpringBoard` /
  `backboardd` / `testmanagerd` をホスト側の `log show` で絞ると、見出し行だけが返ります。
  これらのエントリはデバイス自身のログストアにあります。そこで収集は抽出を**2つ**走らせます。
  1つはホスト側で CoreSimulator のサービスプロセス向けです（プロセス名は
  `CoreSimulatorService` ではなく `com.apple.CoreSimulator.CoreSimulatorService` で、サブ
  システムは `CONTAINS` での一致が必要です）。もう1つは `simctl spawn` を通してゲストの内側で
  走らせます。30分の窓での実測は、ホスト側 34.5k 行、ゲスト側 11.4k 行で、いずれも約1秒です。
- **`simctl diagnose` は標準入力で止まり、`--output` のディレクトリを破壊します。** 同意通知を
  表示して改行を待ちます。CI ステップの標準入力は `/dev/null` なので、そこで EOF を読み、何も
  収集せずに exit 0 します。さらに `--output` をアーカイブの*基準パス*として扱い、そのディレクトリ
  を `<パス>.tar.gz` で置き換えて、中にあった他のものを削除します。収集先ディレクトリを指定すると、
  収集物の残りが消えます。実測では起動済みデバイス1台で約15秒、22〜78MB でした。フラグは
  `--flag=value` の形式が必須です。
- **`CoreSimulator.log` には上限がありません。** 丸ごとコピーすると、長寿命のホストでは 183MB の
  成果物になり、しかもそれが*常時*段です。収集では末尾だけを取ります。

同じ実測から、小さな形が2つ決まりました。`xcodebuild` は `-resultBundlePath` が既存だと起動
そのものを拒むため、Unit 1 は自分の鍵にある残骸を先に消します。そうしないと、エフェメラルポートの
再利用が診断機能を起動失敗に変えてしまいます。そして `start` フェーズは、呼び出す各ジョブではなく
`bajutsu-e2e/action.yml` の内側に置きます。`bundled-runner` がこの action を2回呼ぶため、ジョブ側に
置くと1回目の `collect` がサンプラを止めてしまうからです。

### 第3層は時系列のホストテレメトリ

ストールとホストの資源枯渇の相関は、ストールが起きた瞬間のホスト負荷が記録にあって初めて
取れます。そこで同じ composite action に `start` フェーズを設け、消費側ジョブが
`bootstatus` の直後に呼びます。`start` はバックグラウンドのサンプラを起動します。サンプラは
約 20 秒ごとに `top` / `vm_stat` / `memory_pressure` の出力を
`runs/diagnostics/host-telemetry.log` へ追記する定期ループです。`start` は描画パイプライン
の一発プローブも実行します。プローブは所要時間を計測した `simctl io screenshot` と 5 秒間の
`recordVideo` で、そのバイト数が「録画パイプラインは最初から死んでいたのか」にジョブ単位で
答えます。`collect` フェーズがサンプラを停止します。サンプラは判定経路の外にいる観測者です。
その間隔は待機ではなくサンプリング周期なので、実行ループにおける固定 sleep を禁じる第2の
最重要原則には触れません。

### 作業分解（`MECE`）

相互に排他的で全体として網羅的（Mutually Exclusive, Collectively Exhaustive、`MECE`）な
作業単位は次のとおりです。

1. **Result bundle。** `BAJUTSU_XCUITEST_RESULT_BUNDLES` の背後で `_spawn_runner` に
   `-resultBundlePath` を追加します。ランナーログと同じポート単位の鍵付けを持ち、すべての
   バンドルを保持し（環境変数がオペレータの意思表示だからです）、`ios-e2e.yml` はこれを
   `runs/runner-logs/` 配下へ向けます。
2. **ストール時プローブ。** `BAJUTSU_STALL_DIAGNOSTICS` の背後の上限付き捕捉モジュール、
   その2つのトリガー地点（チャネルのクラッシュ宣言と録画のバイト無し警告）、実行ごとの
   捕捉回数上限、そして `ios-e2e.yml` でのオプトインを実装します。
3. **composite action `collect-ios-diagnostics`。** 常時段と失敗時段と `start` フェーズを
   実装し、`bajutsu-e2e/action.yml` の「Collect crash diagnostics」ステップを置き換え、
   `conformance` / `fault-injection` / `visual` へ配線します。
4. **ドキュメント。** `docs/ci.md` とその `docs/ja/` ミラーに診断の節を設けます。段の構成、
   各収集物が `runs/` のどこに置かれるか、4つの仮説に対して収集物をどう読むかを記します。
5. **テスト。** `bajutsu` 側の2つのフックのユニットテストです。環境変数が設定されたときに
   限り起動 argv が `-resultBundlePath` を得ること、未設定ならプローブが no-op であること、
   設定時は上限付きかつベストエフォートであることを固定します。あわせて
   `tests/test_e2e_changes.py` が、iOS レーンの正のパスリストに新しい action を名指しすることを
   固定します。これがないと、その action への変更はどのレーンも発火させません。この unit が当初
   想定していたのに反して、composite action のシェルには静的なゲートの検査が**ありません**。
   `actionlint` が検査するのは `.github/workflows/` だけで、`action.yml` を指定すると `jobs`
   セクションがないと報告します。`make lint-sh` は名指しした `.sh` ファイルの一覧に対して
   `shellcheck` を走らせるので、`.github/actions/` 配下には届きません。シェルは各ステップを
   抽出し、起動済みの Simulator に対して実行して検証しました。すべての composite action について
   このゲートの穴を閉じるのは、別途の後続作業とします。

### 最重要原則の維持

- **AI は判定しない。** どの層も証拠を集めるだけです。本提案が触れるどの経路にもモデル
  呼び出しは入らず、収集物が判定に流れ込むこともありません。
- **決定論を最優先する。** プローブはタイムアウト付きの有界なサブプロセス呼び出しであり、
  sleep ではありません。テレメトリのサンプラは実行ループの外から観測します。合否の決め方は
  従来と一切変わりません。
- **アプリ非依存。** 本提案は対象アプリを読みません。フックはデバイスとランナープロセスに、
  CI の action はホストに鍵付けされ、ターゲットごとの分岐はどこにもありません。

## 検討した代替案

- **OS 側の状態収集の代わりに `BAJUTSU_LOG_LEVEL` を上げる。** 主たる手段としては退けます。
  失敗している層は Simulator のスクリーンショットサービスであり、`bajutsu` のプロセスの
  外にあるため、`bajutsu` 側をどれだけ饒舌にしても記録されません。ログ増量が同乗するのは
  構いませんが、第2層と第3層の代替にはなりません。
- **失敗時に `log collect` の完全アーカイブ、または `sysdiagnose` を取る。** 先送りします。
  対象期間全体の `.logarchive` はジョブあたり数百 MB に達し、`sysdiagnose` は数分かけて
  ギガバイト級を生成します。一方、対象を絞った `log show` の抽出は、失敗署名がすでに名指し
  しているプロセス群を覆います。抽出で不足するとわかったときに限り再検討します。
- **ジョブ全体で unified log をストリームし続ける。** 退けます。ステップ単位の `device.log`
  という区間証拠が、シナリオ実行中のゲストログのストリームをすでに行っており、失敗時の
  遡及的な `log show` は、長寿命の追加プロセスなしで同じホスト側エントリを得られます。
- **スクリーンショット失敗を診断する代わりに、ランナーが失敗を生き延びるようにする。**
  意図的にスコープ外とします。`/screenshot` を `simctl io screenshot` 経由へ迂回することや、
  Swift 側で失敗を非致命化することは「修正」であり、診断が原因を確定する前にどれかを選ぶ
  のは当て推量になります。証拠が揃った後の後続提案が修正を担います。

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

- [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience-ja.md) —
  本提案の result bundle と保全方針が拡張する、ランナー出力の捕捉
- [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery-ja.md) /
  [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement-ja.md) —
  この失敗を「大きな音を立てる」ようにしたが、まだ診断可能にはしていないクラッシュ回復予算と
  wedge 検出
- [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery-ja.md) —
  収集された証拠がその必要性を説明するはずの、試行間のデバイス修復（再起動と置換）
- [BE-0346](../BE-0346-video-timing-sync/BE-0346-video-timing-sync-ja.md) — 本提案の2つの
  ストールトリガーの片方である `recordVideo` バイト無し警告を持つ、動画アンカー補正
- [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md) —
  Simulator レーンの flakiness の履歴
- [`bajutsu/platform_lifecycle/environments/xcuitest.py`](../../bajutsu/platform_lifecycle/environments/xcuitest.py) —
  Unit 1 が拡張する継ぎ目である `_spawn_runner`
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) — Unit 2 の第1トリガーで
  あるクラッシュ宣言を持つチャネル
- [`bajutsu/evidence/intervals.py`](../../bajutsu/evidence/intervals.py) — Unit 2 の第2トリガー
  であるバイト無し警告を持つ録画監視
- [`.github/actions/bajutsu-e2e/action.yml`](../../.github/actions/bajutsu-e2e/action.yml) —
  Unit 3 が置き換えるクラッシュ診断ステップ
- [`.github/workflows/ios-e2e.yml`](../../.github/workflows/ios-e2e.yml) — 全層にオプトイン
  するジョブ群を持つレーン

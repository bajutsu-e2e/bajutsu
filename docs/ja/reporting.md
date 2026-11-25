[English](../reporting.md) · **日本語**

# レポート（manifest.json / JUnit / CTRF / HTML）

> 1 回の run は、1 つ以上のシナリオ（`list[RunResult]`）を実行します。その結果を 4 つの形式で
> 書き出します。`manifest.json` が、レポートと CI（継続的インテグレーション）の **唯一の情報源**です。
>
> 実装: `bajutsu/common/report/`。パッケージは段階で分割してあり、
> `format` → `manifest` / `richtext` → `rows` / `panels` → `html` の順に進みます。

関連: [run-loop の実行結果](run-loop.md#実行結果データ構造) · [evidence](evidence.md)

---

## 出力レイアウト

```
runs/<runId>/
├── manifest.json     # step → outcome の相関（単一の真実）
├── junit.xml         # CI 連携（1 シナリオ = 1 testcase）
├── ctrf.json         # Common Test Report Format（PR コメントやダッシュボードなど、より豊かな CI の消費側向け）
├── report.html       # 自己完結 HTML（外部アセット無し）
└── <sid>/            # シナリオ単位の証跡（FileSink 使用時）
    ├── result.json   # このシナリオの判定（シナリオが終わった時点で書き出す）
    └── <stepId>/     # ステップごとの証跡
        ├── before.png    # screenshot（ステップが動作する前）
        ├── after.png     # screenshot（動作したあと）
        ├── elements.json # query() ダンプ
        ├── segment.mp4   # video（区間）
        └── device.log    # deviceLog（区間）
```

ランナーが`manifest.json`、`junit.xml`、`ctrf.json`、`report.html`を書き出すのは、すべてのシナリオが
終わった後です。そのため、ホストのクラッシュやCIジョブのタイムアウトでスイートの途中で実行が止まると、
判定は何も残りません。`<sid>/result.json`は、この欠落を埋めるファイルです。ランナーは、各シナリオが
終わった時点で、次のシナリオを始める前に書き出します。

中身は2つで、manifestと同じ`schemaVersion`と、`scenario`キーの下に置いたエントリです。このエントリは、
`manifest.json`の`scenarios`がそのシナリオについて持つものと同じです。シークレットの値は、manifestと
同じくマスクします。クロスブラウザのマトリックス実行では、ファイルは`<engine>/<sid>/`の下に置かれます。
親ディレクトリがエンジンを表し、エントリの`engine`は空で、成果物のパスは`<engine>/`からの相対パスです。
最後まで完了した実行では、引き続き`manifest.json`が唯一の情報源です。

シナリオの`targets`フィールド
([BE-0428](../../roadmaps/BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution-ja.md))
が2つ以上のエントリを挙げているときは、primaryを含む宣言済みの各ターゲットの証跡を、そのターゲット
名のフォルダにまとめます。上記のステップごとの証跡は`<sid>/<target>/<stepId>/…`になります。そのターゲットの
シナリオ全体の録画（`scenario.mp4`など）と`network.json`は、`<sid>/<target>/`の直下に置かれます。

ターゲットを0個または1個しか宣言しないシナリオは、上記の平坦なレイアウトのままです。

`runId` は `YYYYMMDD-HHMMSS` の形式で、`bajutsu/common/run_meta/id.py`（[BE-0200](../../roadmaps/BE-0200-run-id-contract/BE-0200-run-id-contract-ja.md)）が一箇所で採番します。この形式は report、Web UI、その他すべての呼び出し元で共有する単一の契約です。

`sid` は `{NN}-{slug}` の形式で、ゼロ埋めされた実行順の連番と、シナリオを読み込んだ元ファイルの語幹（`login_flow.yaml` なら `login_flow`）をつなげたものです。ファイルから読み込まれていないシナリオ（メモリ上で直接組み立てられたものなど、元ファイルが不明な場合）では、代わりにシナリオの `name:` フィールドをスラッグ化した値になります
（[BE-0417](../../roadmaps/BE-0417-scenario-result-folder-naming/BE-0417-scenario-result-folder-naming-ja.md)）。`stepId` は `step.name` または `step<i>` です。

スラッグは、元ファイルの語幹から作る場合も `name:` から作る場合も、60 文字に切り詰めます（[BE-0420](../../roadmaps/BE-0420-scenario-slug-length-cap/BE-0420-scenario-slug-length-cap-ja.md)）。元ファイルの語幹には Unicode の文字がそのまま残るため、上限はバイト数ではなく文字数で数えます。American Standard Code for Information Interchange（ASCII）は、1文字を1バイトで表します。全角文字や日本語の名前は、1文字あたりのバイト数が多くなりがちです。文字数で数えることで、日本語の名前も ASCII と同じ長さに切り詰められます。

ファイル名や `name:` フィールドが上限を超えるシナリオも、最後まで実行できます。証跡ディレクトリの名前は、見分けのつく先頭部分だけになります。`Scenario.name` 自体は切り詰めないので、`manifest.json` と `report.html` には元の名前がそのまま残ります。切り詰めた結果スラッグが一致する2つのシナリオも、`runs/<runId>/<sid>/` がスラッグの前に置く実行順の連番で区別できます。

## manifest.json

`RunResult` 以下はすべて dataclass なので、`manifest_dict` でステップ / expect の結果がそのまま落ちます。ただし`wall_offset_s`は例外です（詳細は後述）。これは run 内だけで使う変換定数であり、永続化後は意味を持たないため除外されます。

それ以外のタイムスタンプはすべて絶対的な実時刻（エポック秒）なので、記録した run のプロセスが終了したあとも同じ意味を保ちます（[evidence](evidence.md#区間証跡video--devicelog--apptrace)参照）。

```json
{
  "runId": "20260605-101530",
  "ok": true,
  "backend": "xcuitest",
  "scenarios": [
    {
      "scenario": "onboard, log in, and increment the counter",
      "ok": true,
      "backend": "xcuitest",
      "steps": [
        {
          "index": 5, "action": "tap", "ok": true, "reason": "",
          "duration_s": 0.12,
          "assertion_results": [],
          "artifacts": [
            { "name": "before.png", "kind": "screenshot", "provider": "driver",
              "depicts": "xcuitest:before" },
            { "name": "after.png", "kind": "screenshot", "provider": "driver",
              "depicts": "xcuitest:after" }
          ]
        }
      ],
      "expect_results": [
        { "ok": true, "kind": "value", "detail": "value equals='2': id='counter.value'", "reason": "" }
      ],
      "failure": null
    }
  ]
}
```

- `ok`（トップ）: 全シナリオが ok なら true です。
- `backend`: その run を操作したアクチュエータです（`xcuitest`、テストでは `fake`）。アクチュエータは
  run ごとに 1 つ固定なので、トップレベルは通常 1 つの名前です。各シナリオも自分の `backend` を持ちます
  （[drivers](drivers.md#バックエンド選択と-actuator)）。
- `steps[].duration_s`: 各ステップの計時です。run が求める経過時間は、すべて単調増加時計で測ります。実時刻の時計は run の途中で過去へ戻ることがあり（Network Time Protocol（NTP）による補正、手動での時刻変更）、経過時間やタイムアウトを壊すからです。実時刻の時計はシナリオごとに一度だけ読み、記録するタイムスタンプにだけ使います。
- `steps[].started_at`: そのステップが始まった絶対的な実時刻（エポック秒）です。記録する値に動画の補正は入りません。録画のどこへシークするかは、描画のときに下の`video_anchor_s`を引いて求めます。[report.html](#reporthtml)はその値を**steps**テーブルの`at`列に表示します。補正済みのオフセットではなく生の時刻を保存するので、保存済みの run からシーク位置を計算し直せます。たとえば基準時刻の求め方を修正したときも、シナリオの実行し直しは要りません（[BE-0348](../../roadmaps/BE-0348-absolute-timestamp-recording/BE-0348-absolute-timestamp-recording-ja.md)）。
- `video_anchor_s`（シナリオごと）: そのシナリオの動画が始まった絶対的な実時刻です。確定した録画の長さが実時間の測定値になっている場合はその長さから測り、そうでない場合は録画の開始確認の信号から取ります（[evidence](evidence.md#区間証跡video--devicelog--apptrace)）。描画する側は、どの表示でもこの値を引きます。`schemaVersion` 6 より前に記録された run は、この値を持ちません。その`started_at`はすでに動画基準の相対値なので、描画する側は欠けた基準時刻を`0.0`と読み、記録どおりに表示します。
- `wall_offset_s`（run 内だけの値で、永続化されません）: その run が、自分自身の単調増加時計の値を実時刻のエポックへ変換するために足す差分です。通信ログの受信時刻（単調増加時計）を、`video_anchor_s`と同じ基準時刻へ載せるためだけに使います。manifest には残りません。単調増加時計の値もmanifestには残らないので、あとから読む側にはこの差分を使って変換する対象自体がないからです。
- `steps[].artifacts`: そのステップで取れた証跡の来歴です（[evidence](evidence.md#アーティファクトの来歴provider)）。
- `steps[].actuations`: そのステップのあいだにドライバが画面に対して実際に行ったことです。タップが送った座標、スワイプが動いた端点、各ジェスチャを運んだ経路が入ります。これが `actionLog` の証跡種別で、ファイルではなく manifest に内在します（[evidence](evidence.md#各ステップが画面に対して実際に行ったことactionlog)）。`schemaVersion` 5 より前に記録された run は持ちません。
  - `expect_actuations` は、シナリオ末尾の `expect` の再チェックについて同じものを持ちます。そこではシステムアラートガードが、載せる先のステップなしに操作しうるからです。
  - `schemaVersion` 7 からは、操作した要素がドライバの既定の規則の指す要素と異なる理由を `substitution` として持つことがあります。それより前の run は持たず、いまその項目がないのと同じに読めます。
  - `schemaVersion` 13 からは、ドライバがその操作を送った絶対的な時刻を `at` として持ちます。`started_at` と同じ土俵に立つので、描画する側は同じように `video_anchor_s` を引きます。`at` を持たない古い run では、描画する側はステップの `started_at` を代わりに使います。
- `steps[].system_alert`: `handleSystemAlert` ステップが押したボタン（`label`）と、それを選んだ規則（`rule`）です。`rule` は `sel`、`label table: <locale>`、`position: button 2 of 2` のいずれかです。label の対応表にない言語でも、何をなぜ押したかがわかります（[BE-0445](../../roadmaps/BE-0445-system-alert-locale-agnostic-answer/BE-0445-system-alert-locale-agnostic-answer-ja.md)）。ほかのアクションと、`schemaVersion` 12 より前に記録された run は持ちません。
- `steps[].warnings`: ステップの実行についての、失敗にはならない注記です。レポートのステップ行に表示されます。現在の発生源は 2 つあります。`minRemaining` によるウィンドウ待機の途中で壁時計が飛んだ `totp` または `otp` ステップ（[BE-0474](../../roadmaps/BE-0474-totp-window-alignment/BE-0474-totp-window-alignment-ja.md)）と、ステップ中に回復、切り替え、復帰が起きた Android バックエンドの読み取りチャネルです。警告が `ok` や `reason` を変えることはありません。注記のないステップでは空で、`schemaVersion` 16 より前に記録された run には存在しません。存在しない場合は、警告なしとして読みます。
- `channel_degraded`: シナリオの終了時に Android バックエンドが `uiautomator dump` の経路で読んでいた理由です。この切り替え先の経路で通った run を見つけられます。`ok` を変えることはありません。チャネルが正常なときと、ほかのバックエンドでは空です。`schemaVersion` 18 より前に記録された run には存在せず、その場合は正常として読みます。
- `channel_notes`: シナリオの最後のステップのあとに同じ読み取りチャネルで起きたことです。どのステップの `warnings` にも載せられなかった注記が入ります。何も起きなければ空で、`schemaVersion` 18 より前に記録された run には存在しません。
- `ignored_settings`: シナリオが有効にした設定のうち、ターゲットのルートが適用できず、ターゲットが `runsOn.ignoredSettings` で無視を宣言したものです。1 件ごとに `{target, key, reason}` で表します（[BE-0467](../../roadmaps/BE-0467-environment-specific-settings/BE-0467-environment-specific-settings-ja.md)、[設定](configuration.md#環境ごとの設定runsonignoredsettingsbe-0467)）。この一覧が `ok` を変えることはありません。何も無視しなかったシナリオや、起動前に preflight で拒否されたシナリオでは空です。`schemaVersion` 17 より前に記録された run には存在せず、その場合は無視した設定なしとして読みます。
- `network.json`の`startedAt`（シナリオごとに1ファイルで、上のmanifestには出てきません）: 観測した各通信が始まった絶対的な実時刻です。`steps[].started_at`と同じ土俵に立ち、同じシナリオの基準時刻を通して導かれるので、描画する側は両方から`video_anchor_s`を引きます。両者がどのように1本のタイムラインへ織り込まれるかは[report.html](#reporthtml)を参照してください。
- `failure`: 失敗時の要約です（例 `"step 3 (tap): no match: {...}"`）。成功なら null です。
- `provenance`（トップ、任意）: run の同一性スタンプです（[BE-0049](../../roadmaps/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)）。キーは次のとおりです。
  - `scenarioHash`：実行した `scenario.yaml` の `sha256:` フィンガープリントです。
  - `toolVersion`：`bajutsu.__version__` です。
  - `gitRevision`：コミットです。git チェックアウト内の run のときだけ付きます。
  - `configSource`：`{ host, owner, repo, ref, sha }` で、ブランチ指定の run が実際に実行した正確なコミットです。config が Git ソース由来のときに付きます（[BE-0063](../../roadmaps/BE-0063-git-config-source/BE-0063-git-config-source-ja.md)）。
  - `targets` と `toolSource`：どのコードのどのビルドを実行したかです（[後述](#ビルドとソースの識別情報)）。

  このスタンプで蓄積した run を同一性でグルーピングできるので、フィンガープリントが変わっていないのに判定が反転すれば、それは編集ではなく**真の flakiness** だと分かります。純粋なメタデータで、`ok` には一切入りません。（このブロックが出るようになった時点で `schemaVersion` は `3` 以上です。現在は `19` です。）

  `run-set` の job は実行場所を指定しないので、同じ job をもう一度実行すると別の場所に割り当てられることがあります。そのため worker は、実行した自分の環境を `workerEnvironment` として記録します。値は `local` や `devicefarm` などです。レポートは、この記録を *notices* に載せます（[BE-0484](../../roadmaps/BE-0484-job-group-lease/BE-0484-job-group-lease-ja.md)）。
- `target`（トップ、任意）: この run が実行した target です。「Android の target は通るのに iOS の target は落ちる」を、保存済みのデータから計算できます（[BE-0404](../../roadmaps/BE-0404-collapse-project-layer/BE-0404-collapse-project-layer-ja.md)）。1 つの run が解決する target は 1 つなので、シナリオごとではなく `backend` の隣に置きます。`serve` はこれを run の行へ写し、target どうしを順位付けします。target を持たない run では省かれます。（この項目が出るようになった時点で `schemaVersion` は `10` 以上です。）
- `label`（トップ、任意）: run 履歴の区切りです。bind している config 自身の名前か、運用者が `run --label` で上書きした値が入ります。不透明なメタデータで、解析も config との照合もせず、`ok` の入力にもなりません。`serve` を再起動したあとに 2 つの config の run を読み分けられるのは、この値のおかげです。label を持たない run では省かれます。（この項目が出るようになった時点で `schemaVersion` は `10` 以上です。）
- `idb`（トップ、任意、レガシー）: 古い manifest には `idb_companion` / client のバージョンブロックが残っていることがあります（BE-0005）。idb バックエンドとともに BE-0290 で廃止され、今は書き出されません。未知のトップレベルキーは読み込み時に無視されるため、これを含む古い manifest も問題なく読めます。
- `matrix`（トップ、任意）: クロスブラウザのエンジン × シナリオのグリッドで、`bajutsu run --browsers` の run のときだけ出ます（[BE-0076](../../roadmaps/BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines-ja.md)）。`scenarios` はフラットな結果リストのままで、各エントリに `engine` が付きます。`matrix` は `{ engines, scenarios, cells: { "<scenario>": { "<engine>": { ok, sid, failure } } } }` で、エンジンごとの判定を集約しただけのものです（report はこれをグリッドとして描画します）。`ok` はエンジン × シナリオのすべてに対する all-must-pass です。単一エンジン／iOS の run では省かれます。

  複数ファイルにまたがる run では `name` の一意性は保証されません。そのため、同じ名前のシナリオが2つ目以降にあると、最初のセルを上書きするのではなく、`(2)`、`(3)` などの接尾辞が付いた別の行として保持します。（このブロックが出るようになった時点で `schemaVersion` は `4` 以上です。）

### ビルドとソースの識別情報

スタンプは、run が操作した各アプリのビルドを記録します（[BE-0506](../../roadmaps/BE-0506-run-code-identity/BE-0506-run-code-identity-ja.md)）。`gitRevision` の意味は変わりません。run を起動したディレクトリのコミットです。次の 2 つのキーは `schemaVersion` 19 から現れます。キーを持たない run は、古い run として読みます。

`provenance.targets.<name>` には、run が操作したターゲットごとに 1 件が入ります。1 件の中身は 2 つのグループです。

| キー | 内容 |
|---|---|
| `app.hash` | `subject.path` のバイナリの `sha256:<hex>` ダイジェスト |
| `app.hashKind` | `.apk` は `file`、`.app` バンドルは `tree` |
| `app.version` | `CFBundleShortVersionString` または `versionName` |
| `app.buildNumber` | `CFBundleVersion` または `versionCode` の文字列 |
| `appSource.commit` | `subject.sourceRoot` のリポジトリの完全なコミットハッシュ |
| `appSource.uncommitted` | 作業ツリーに未コミットの変更があれば `true` |

`provenance.toolSource` は Bajutsu 自身のコードを表します。持つキーは `commit` と `source` です。`source` が `checkout` のときは `uncommitted` も持ちます。チェックアウトと見なす条件は、Bajutsu のパッケージを追跡していることです。`source` が `build-arg` の場合は、チェックアウトのないインストールで使う `BAJUTSU_BUILD_COMMIT` の値です。どちらもなければ、`toolSource` は省かれます。

ランナーはこれらの値を run につき一度だけ読みます。読むのはビルドと `beforeRun` フックの後、最初のリースの前です。そのため、どのシナリオも同じビルドを報告します。コミットが表すのは作業ツリーで、バイナリではありません。両者は食い違うことがあるので、スタンプは両方を残します。

読めなかった値は `null` としてスタンプに残ります。同じ階層の `unavailable` が、キーごとの理由を示します。読めない値があっても、判定は変わらず、run も止まりません。

| 理由 | 意味 |
|---|---|
| `path-missing` | `subject.path` にバイナリがないか、バンドルにファイルがない |
| `read-error` | ランナーがバイナリを読めなかった |
| `plist-unreadable` | `Info.plist` を解析できないか、バージョンのキーがない |
| `apk-unreadable` | `.apk` を zip として開けないか、マニフェストがない |
| `manifest-unparsable` | マニフェストをデコードできないか、バージョンが直接の値でない |
| `not-a-git-repo` | `subject.sourceRoot` に Git が追跡するファイルがない |
| `git-unavailable` | `git` コマンドがないか、時間切れ |
| `status-read-failed` | `git status` がエラーを返したか、実行しなかった |

ランナーが `git status` を実行するのは、運用者が選んだ config の場合だけです。`--config-source` が `uploaded` か `remote` のときは、代わりに `status-read-failed` を記録します。そうしたツリーが設定したコマンドを、Git が status の読み取り中に起動できるためです。コミットは記録します。

ターゲットが設定していないグループは省かれます。`subject.path` のないターゲットには `app` がありません。`subject.sourceRoot` のないターゲットには `appSource` がありません。web ターゲットには両方ともありません。デバイスプロバイダがアプリをインストールするターゲットにも、`app` はありません。iOS の実機も同じです。ビルドは Bajutsu の外でインストールされます。これらの場合、`subject.path` のファイルがテスト対象のバイナリだとは言えないためです。

## junit.xml

`junit.xml` は CI 連携用です。**1 シナリオ = 1 `<testcase>`** です。失敗シナリオには `<failure>` が付き、その `text` に
各ステップ / expect の ok/FAIL と理由が並びます。

- `--browsers` のマトリクス run では、各ケースにエンジンが織り込まれ（`classname="bajutsu.<engine>"`）、CI からは `chromium.login` と `webkit.login` が別々のケースに見えます（BE-0076）。単一エンジンの run は `classname="bajutsu"` のままです。
- ターゲットが `runsOn.ignoredSettings` で無視を宣言した設定があると、そのシナリオの testcase に `<properties>` ブロックが付きます。ブロックの中身は `bajutsu.ignoredSettings` プロパティ 1 つで、その値は `{target, key, reason}` の JSON 配列です（[BE-0467](../../roadmaps/BE-0467-environment-specific-settings/BE-0467-environment-specific-settings-ja.md)）。判定は変わりません。

```xml
<testsuite name="bajutsu" tests="2" failures="1">
  <testcase name="..." classname="bajutsu"/>
  <testcase name="..." classname="bajutsu">
    <failure message="step 1 (tap): ...">step 0 tap: ok
step 1 tap: FAIL no match: {...}</failure>
  </testcase>
</testsuite>
```

## ctrf.json

[Common Test Report Format（CTRF）](https://ctrf.io/)への出力です（[BE-0161](../../roadmaps/BE-0161-ctrf-report-export/BE-0161-ctrf-report-export-ja.md)）。CTRF はオープン標準の JSON テストレポートです。`ctrf-io` の GitHub Actions（PR コメントやジョブサマリーの出力ツール）、ツールをまたぐダッシュボード、flaky なテストの分析といった消費側のエコシステムが育ちつつあり、これらはツールごとのアダプターなしに CTRF を読めます。

JUnit XML が run を名前、所要時間、失敗内容の塊まで削ぎ落とすのに対し、CTRF は Bajutsu の構造化された詳細を運びます。ステップごとの結果、エンジンとデバイス、第一級のアタッチメントとしてのアーティファクトで、これらは JUnit には収まる場所がありません。この出力は **`manifest.json` の純粋な射影**であり、同じデータを `junit.xml` の隣に別の形で並べるだけなので、記録が二重になるわけではありません。判定より後に書くので判定を動かすこともありません（LLM は関与せず、合否にも影響しません）。

文書は `{ reportFormat: "CTRF", specVersion, generatedBy, timestamp, results }` で、`results` が `tool` / `summary` / `tests`（＋任意の `environment` / `extra`）を持ちます。

```json
{
  "reportFormat": "CTRF",
  "specVersion": "0.0.0",
  "generatedBy": "bajutsu",
  "results": {
    "tool": { "name": "bajutsu", "version": "…" },
    "summary": { "tests": 2, "passed": 1, "failed": 1, "skipped": 0, "pending": 0, "other": 0,
                 "start": 1717581300000, "stop": 1717581302300, "duration": 2300 },
    "tests": [
      { "name": "login", "status": "passed", "duration": 1500,
        "steps": [{ "name": "tap", "status": "passed", "extra": { "duration": 500 } }],
        "browser": "chromium", "device": "iPhone 15 (iOS 17.2)",
        "attachments": [{ "name": "00-login/scenario.mp4", "contentType": "video/mp4", "path": "00-login/scenario.mp4" }] }
    ]
  }
}
```

`environment.commit` は `gitRevision` と同じ値です。CTRF は `environment` に未定義のキーを許さず、ターゲットごとのキーもないので、ビルドの識別情報は `environment.extra` に入ります。`extra.targets.<name>` が持つのはアプリの `hash` と `version`、ソースの `commit` と `uncommitted` です。`extra.toolSource` はツールの `commit`、`uncommitted`、`source` を持ちます。欠けた値は `null` のままで、理由は `unavailable` に入ります。

- `summary.duration` と各 `tests[].duration` はミリ秒（Σ／シナリオごとの `duration_s`）で、CTRF の消費側が拠り所にするフィールドであり、正確です。`summary.start` と文書の `timestamp` は `YYYYMMDD-HHMMSS` の runId から導出します。runId は UTC で採番されるため、UTC として解釈します。`stop = start + duration` です。テストごとの絶対 start/stop は、シナリオごとの絶対エポックが要る（オプションの後続作業）ため、近似せず省きます。実行時のホスト状態は一切載せないので、`bajutsu report` は同じ実行の `ctrf.json` をバイト単位で同一に再生成します。
- `tests[].status` は、実行した run では `passed` / `failed` です。対象外（not applicable）の記録（[端末の条件が作る実行](#端末の条件が作る実行)を参照）は `skipped` になります。他の CTRF の集計は `0` のままです。
- CTRF の `step` は `{ name, status, extra }` しか許さないので、ステップのより豊かなデータ（duration、reason、ステップごとのアサーション、アーティファクト）は `step.extra` に入れます。name／status だけを描画する消費側にはきれいな一覧が見え、Bajutsu を理解するツールは extra を読めます。
- アタッチメントの `contentType` は、アーティファクトの `kind` → MIME の対応表（`screenshot`→`image/png`、`deviceLog`→`text/plain`、`elements`／`network`／`appTrace`→`application/json`）から決め、未知の kind には `application/octet-stream` を充てます。`path` は manifest と同じく実行ディレクトリからの相対パスです。`video` だけは例外で、固定の対応表ではなくアーティファクトの実際のファイル拡張子から MIME を求めます。web シナリオで Playwright が録画する動画は本物の WebM です（`video/webm`、`scenario.webm`）。simctl / adb の録画が使う `video/mp4` とは別物だからです。詳しくは [evidence](evidence.md) を参照してください。
- `--browsers` のマトリクス run では、エンジン × シナリオの各セルが 1 つの CTRF テストになります。エンジンはテストの `name` と `browser` フィールドに入れ（JUnit の `classname` に倣います）、エンジン × シナリオのグリッドは `results.extra.matrix` に持ちます。Bajutsu のその他の余剰は、テストごとの `extra` に入ります。対象は `sid`、`expect` の結果、アラート、`skipped_captures`、`ignoredSettings` です。
- CTRF は秘匿処理済みの manifest から射影されるので、同じ秘匿処理を継承します（[BE-0047](../../roadmaps/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md)）。生のシークレットは届きません。

### CI で ctrf.json を消費する

`ctrf.json` は `junit.xml` の隣にあるので、CI ジョブへの組み込みは消費側の一手順で済みます。たとえば `ctrf-io/github-test-reporter` アクションは、`ctrf.json` を PR コメントやジョブサマリーに変換します。

```yaml
- uses: ctrf-io/github-test-reporter@v1
  with:
    report-path: runs/*/ctrf.json
  if: always()
```

## report.html

`report.html` は、人間が見る自己完結 HTML です。CSS はインラインで、外部アセットを持ちません。

### ヘッダとシナリオ名

ヘッダには run id と全体の PASS/FAIL を表示し、その下にシナリオファイル名（`source_name`）を表示します。
`source_name` は run 全体のラベルです。単一ファイルならそのファイル名、複数ファイルやディレクトリ指定なら
親ディレクトリ名になります。ファイルレベルの `description` があれば、さらに表示します。

その下に、ターゲットごとのビルドを 1 行ずつ表示します（[ビルドとソースの識別情報](#ビルドとソースの識別情報)）。
行の中身は、アプリのバージョン、短いハッシュ、短いソースコミットです。ソースツリーに変更があれば
`uncommitted` の目印が付きます。欠けた値は `unknown` と理由で表示します。

各シナリオ行のサマリには、シナリオ名と、そのシナリオ自身の元ファイル名（`source_files`）を表示します。
元ファイル名は `source_name` とは別物です。`bajutsu run` がディスクからシナリオを読んだ場合だけ設定され、
オフライン再描画では設定されません。設定があれば、横にシナリオレベルの `description` も表示します。こうして
run 全体で、シナリオ名、ファイル名、description を提示します。

### Steps タブ

シナリオ定義とその実行結果は 1 つの Steps タブに統合され、ラベル付きセクション（**preconditions** /
**steps** / **expectations**）ごとにテーブルで描画されます。

**steps** テーブルの列は次のとおりです。

- `#`
- `result`：PASS/FAIL ピルを独立した列に置きます
- `action`：色付きバッジです
- `detail`：対象の説明です
- `at`
- `view`：スクリーンショットと、レポート内の element tree ビューアです。キャプチャした要素は、別タブではなく
  ページ内オーバーレイで開きます
- `reason`

`#` 列には、`step_lines` がシナリオファイル中の行番号を復元できたとき、`2:15` のようにその行番号も併記
します。コンポーネント展開でステップ構成が変わった場合は、行番号を省略します。誤った行番号を示すより、
示さないほうがましだからです。

`at` セルには、所要時間が 0.0 秒以外に丸まるとき、セルの末尾に括弧書きで経過時間も表示します（例：
`3.2s (1.1s)`）。時間のかかった `wait` や動作の遅いジェスチャーが、録画を開かなくてもわかります。

**preconditions** テーブルは折りたたみ可（key / value）です。**expectations** テーブルは並行カラム
`result` / `kind`（バッジ）/ `target`（検査対象セレクタ。例：`#counter.value`）/ `comparison`
（例 `== “2”`）/ `reason` を持ち、同じ id / 定数トークンを使います。

detail 中の識別子（`#home.title`）と定数リテラル（`”text”` や数値）は、控えめなインライントークンで
描画します。ソリッドな action/assert バッジと視覚的に区別されるため、変数と定数を一目で識別できます。
`assert` ステップの複数チェックはネストしたテーブルになり、読みにくい `a; b; c` 形式ではなく、
1 アサーション 1 行で `kind` / `target` / `comparison` のセルに分割します。

実行されなかったステップ（失敗で停止）も、skipped として残ります。
[`group:`](scenarios.md#ステップのグループ化group--reporthtmlで折りたたむ) ステップの中身は、1 つの
折りたたみ区画にまとまります。見出しにはグループ名とステップ数が載ります。ステップがすべて成功している
グループはデフォルトで畳まれ、失敗したステップを含むグループは自動で展開されます。ヘッダーの「全展開」
「全折りたたみ」ボタンは、シナリオと同じくグループも一緒に開閉します。

観測した通信は、時系列で steps に差し込みます。各通信は、シナリオ開始からのオフセットで配置します。
通信の行は、HTTP メソッドを中立バッジ、ステータスを `result` 列に置き、通信の設定（method / endpoint /
status / duration / ヘッダ）を detail セル内のネストしたテーブルで表示します。どの通信を出すかは、
シナリオの `network.filter.domains`（URL ホスト）で絞ります。Network タブは引き続き全件を載せます。

### スクリーンショットと element tree ビューア

ステップに表示するスクリーンショットは、動作後の `after.png` です
（[evidence](evidence.md#区間証跡video--devicelog--apptrace)）。動作したステップは、いずれも `after.png` を
記録します。動作する前に失敗したステップは `after.png` を記録しないため、動作前の `before.png` を表示します。

ビューアで要素にカーソルを合わせると、その要素の枠をスクリーンショット上に重ねます。枠の座標は
`elements.json` から来ます。`elements.json` も、ステップが動作したあとに取り直します。そのため、枠と
その下にある画素は同じ瞬間を表します。ただし例外が 1 つあります。動作しないステップ（`assert` と `wait`）は、
そのステップが落ち着いたときのツリーを再利用します（BE-0259）。このツリーは、スクリーンショットのあと
ではなく前の瞬間を表します。serve のエディタの要素ピッカーも、同じ理由から同じ画像を使います。

画像と要素ツリーが *別々の* 画面を表すこともあります。該当するのは、
[`web` ブロック](scenarios.md#webwebview-の-dom-コンテキストに入る)のネイティブ画像と WebView の要素
ツリーの組と、`after.png` を失った実行結果です。そのときビューアは、両方を見せたうえで枠を描きません。
枠がない理由は、要素ツリーのボタンに添えます
（[evidence](evidence.md#アーティファクトが写した画面depicts)）。serve のエディタは、そうしたステップでは
画像そのものを出しません。出せば、どこをクリックしても、その画素にはいなかった要素へ解決してしまうからです。

### ソース表示

Rich / YAML トグルで、同じタブを構造化ビューと生のシナリオ YAML に切り替えられます。新しく bake した
`bajutsu run` のレポートでは、その YAML はシナリオ自身の元ファイルから切り出した生テキストです。コメントや
整形は、パースしたモデルから作り直さないため、そのまま残ります。リテラルな `totp.secret` は、その場で
マスクします。

次の 2 つの場合は、上記のデフォルトと異なります。

- コンポーネント展開でステップ構成が変わったシナリオでは、この元テキストは著者が書いたとおり正確なので
  保持しますが、`#` 列の行番号は失われます。
- `data`/`dataFile` によるデータ駆動シナリオは、常に構造化データから作り直した YAML を表示し、生テキストは
  表示しません。各行は同じ元テンプレートを共有します。`expand_data` は行ごとに `${row.*}` を置換しますが、
  ステップ数は変えません。そのため生テキストをそのまま出すと、どの行も同じ未置換のテンプレートになり、
  実際に実行した内容を示せません。

オフライン再描画（`bajutsu report`、後述）は、元ファイルの生テキストを run に保存していないため、従来
どおり構造化データから作り直した YAML を使います。

### 視覚比較

`visual` の expectation は、行の下に **baseline と actual のインタラクティブ比較ビュー**を描画します。
モードは 4 つあります。

- **Swipe**：仕切りをドラッグして左右にワイプします。
- **Onion**：スライダーで actual を baseline に重ねてクロスフェードします。
- **Blend**：`mix-blend-mode: difference` により、同一画素は黒、差分画素は強調表示になります。
- **Diff**：マシンが算出した確定ピクセル diff です。アサーションの `exclude` 領域はマスク済みで、失敗時のみ
  表示します。

比較ビューには `diff <pct>%` バッジが付きます。初回実行（actual のみ存在）では `no baseline yet`
バッジになります。チェックが合格しなかった場合は **Approve as baseline** ボタンが表示され、撮影した
スクリーンショットをベースラインディレクトリへ昇格させます。ボタンは `/api/approve` への `POST` なので、
`bajutsu serve` 経由で開いたときだけ機能します（ディスクから直接開いたレポートでは非表示）。CLI 版は
[`bajutsu approve`](cli.md#approve) です。

### 再生

失敗行は赤背景です。再生中は、画面に映っている操作のステップ行を強調し、その行が見えるようにスクロール
します。行が強調に切り替わるのは、その行で最初に受け入れられた操作の `at` の時点です。ただし、その行
自身の `at` 列より前にはなりません。理由はステップが `started_at` を記録する位置にあります。ステップは
`started_at` を記録したあとで、スクリーンショットを撮り、ツリーを読み、対象を解決します。画面が変わるのは
その数秒後になることもあります。`started_at` で切り替えると、強調がその待ち時間のぶんだけ映像より先に
進んでしまいます。

操作の時刻を持たない行もあります。

- `wait` や `assert` のように操作をしないステップ
- `schemaVersion` 13 より前に記録された run のすべてのステップ

こうした行は `at` 列の時刻で切り替わります。レポートは、間隔の粗い `timeupdate` イベントだけでなく、
表示されるフレームごとに再生位置を読みます。シークバーには、各ステップの帯のどこで操作が起きたかを示す印を
描きます。

ステップをクリックすると、録画をその切り替えの時刻にシークしますが、**自動再生はしません**（停止中なら
停止のまま、再生中なら再生を続けます）。クリックした行がそのまま強調されます。画面は送られたジェスチャに
1 秒足らずで応じるので、その直後に操作が映ります。

行の開始ボタンは、引き続き `at` 列の時刻にシークします。`at` 列の値は、各ステップの `started_at` から
`video_anchor_s` を引いたものです。基準にするのは、シナリオのステップループが始まった生の瞬間ではなく、
録画そのものから測った原点です。原点を測れない場合は、分かっている範囲でもっとも確からしい動画の実際の
開始時刻を基準にします（[evidence](evidence.md#区間証跡video--devicelog--apptrace)）。クリックした行が
実際に示す瞬間にシークが着地するのは、この基準のためです。

この結果、目に見える変化が 1 つあります。動画を録画する Android または Web のシナリオでは、この描画時に
求めた動画基準の秒数（**steps** テーブルの `at` 列）が、シナリオ自身の `duration_s` を超えることが
あります。この 2 つは、そもそも別のものを測っているためです。動画のタイムラインは実行本体のステップ
ループより前から始まりますが、`duration_s` はそのステップループ自体の長さを測ります。これは想定された
挙動であり、どちらかの数値が誤っているわけではありません。

ステップのスクリーンショットをクリックすると原寸ライトボックスが開き、**← / →**（または画面上の矢印）で
run 内の全スクリーンショットを**シナリオをまたいで**順送りできます（キャプションにシナリオ、ステップ、
位置を表示）。run のアクチュエータはヘッダの `driver: <backend>` チップと各シナリオ行の小バッジで表示
します。

### マルチターゲットの録画

マルチターゲットシナリオでは、動画を記録したターゲットごとの録画が縦に並びます。各録画は再生中に自分の
ターゲットのステップをハイライトするため、2 つの行が同時にハイライトされる場合もあります。

録画が 2 つ以上並ぶときは、どの行がどの録画に対応するかを見分けられるように、録画ごとに色を割り当てます。
録画のターゲット名のラベル、その録画に対応するステップのハイライト、そのステップのターゲットチップは、
すべて同じ色になります。色は並び順に黄、青、ピンク、緑で、5 つ目の録画からは同じ順に繰り返します。動画を
記録しなかったターゲットには専用のプレイヤーがありません。そのステップは最初の録画をシークし、最初の録画と
同じ色になります。録画の枠は、どの録画も同じ青です。録画が 1 つだけのシナリオは、従来の表示のままです。

各録画には、再生/一時停止ボタンの横に**拡大**ボタンが付いています。押すと、その録画を大きく表示する
モーダルが開き、そのシナリオの **steps** テーブルの行が複製されて横に並びます。複製の screenshot / element
tree 列は空にしてあります。ここにクリックできる要素を残すと、このモーダルの上に element viewer がさらに
開いてしまうためです。複製したステップをクリックすると、コンパクト表示のクリックと同じように、拡大した
録画がその時刻へシークします。再生に合わせて実行中の行がハイライトされ、表示範囲に自動でスクロールします。

複数の録画を持つマルチターゲットシナリオでは、録画ごとにモーダル内のタブが並び、タブを切り替えると
モーダルに表示する録画が入れ替わります。切り替えていない他の録画は、通常のコンパクト表示のまま同期再生を
続けます。背景クリック、閉じるボタン、Escape キーのいずれでもモーダルを閉じられ、録画は元のプレイヤーへ
戻ります。

Device Log / App Trace は別タブのままです。

## 端末の条件が作る実行

ターゲットは、`runsOn` に端末の条件（機種の一覧や OS バージョンの範囲など）を宣言できます
（[configuration](configuration.md)）。条件は1つのシナリオを、座標の組ごとの複数の**実行**（run）に
分けます。座標の組の例は `iPhone SE (3rd generation) / iOS 17` です。

片側が開いた範囲に当てはまる端末がない実行は、**対象外**（not applicable）の記録になります。対象外の記録は、
成功と失敗のどちらとしても数えません。各出力は、実行と対象外の記録を次のように示します。

| 出力 | 実行 | 対象外の記録 |
|---|---|---|
| `manifest.json` | 各シナリオの項目が `coordinates`（ラベル）と `run_coordinates`（ターゲットと条件フィールドごと）を持ちます | 記録ごとの `{ scenario, target, coordinates, reason }` を `notApplicable` に持ちます |
| `manifest.json` の `runMatrix` | `{ columns, scenarios, cells: { "<scenario>": { "<coordinates>": { status, sid, failure } } } }` で、`status` は `passed` か `failed` です | `status: "not-applicable"` と `reason` を持つセルになります |
| `report.html` | シナリオと座標を軸にした **run matrix** の表を表示し、各シナリオのカードに座標を添えます | `N/A` のセル、ヘッダーの件数、各記録の理由の一覧を表示します |
| `junit.xml` | 各ケースの名前は `<scenario> [<coordinates>]` です | `<skipped>` のケースになり、スイートの `skipped` 属性で数えます |
| `ctrf.json` | 座標をテスト名と `extra.coordinates` に入れ、グリッドを `results.extra.runMatrix` に持ちます | `extra.notApplicable: true` を持つ `skipped` のテストになり、`summary.skipped` で数えます |
| Webhook の通知 | 失敗の項目に座標を添えます | 結果の行で件数を示します |
| `serve` の run 履歴 | 各シナリオ名を1回だけ並べ、`passed` と `total` ではすべての実行を数えます | `notApplicable` の件数を `N n/a` と表示します |

すべての記録が対象外だった run は、何も実行していません。その manifest とレポートは FAIL になり、
コマンドは終了コード 1 で終わります。run matrix では、条件を持つシナリオと一緒に実行した条件のない
シナリオを `any device` の列に置きます。

どの条件も実行を作らなかった run は、これらのフィールドを持ちません。そのため出力の形は、上の各節で説明した
ものから変わりません。`runMatrix` は `schemaVersion` 15 から、ほかのフィールドは 14 から現れます。

## 書き出し API

```python
def write_report(run_dir, run_id, results, definitions=None, sources=None, source_name=None, description=None, provenance=None, writer=None, target=None, label=None, source_files=None, step_lines=None) -> Path  # 4 形式を書く。definitions=シナリオ毎の dict、sources=生 YAML、source_name=シナリオファイル名（run 全体）、description=ファイルレベルの説明、source_files/step_lines=live run が復元できたときの各シナリオ自身のファイル名とステップの元の行番号、provenance=run の同一性スタンプ（BE-0049）
def write_html_and_junit(run_dir, run_id, results, definitions=None, sources=None, source_name=None, description=None, provenance=None, writer=None, source_files=None, step_lines=None) -> None  # 再生成できる側だけ（report.html + junit.xml + ctrf.json）。manifest.json は触らない。再描画が使う。provenance は CTRF の tool/environment フィールドに使う
def manifest_dict(run_id, results, *, source_name=None, provenance=None, target=None, label=None) -> dict  # バージョン付き render モデル（schemaVersion）。manifest の素（テスト、検査用）
def run_provenance(scenario_yaml, *, git_revision, config_source=None, identity=None) -> dict  # run の同一性スタンプ: scenarioHash + toolVersion + 任意の gitRevision（BE-0049）+ 任意の configSource（BE-0063）+ 任意の targets / toolSource（BE-0506）
def ctrf_json(run_id, results, *, provenance=None) -> dict  # 実行結果モデルの CTRF への射影（BE-0161）。provenance は tool.version / environment.commit / environment.extra に使う
def junit_xml(results) -> str
def html_report(run_id, results, run_dir=None, definitions=None, sources=None, source_name=None, description=None, source_files=None, step_lines=None) -> str
def scenario_render_inputs(scenarios, plan_sources=None) -> tuple[list[dict], list[str]]  # (definitions, sources)。初回 bake と再描画で共有。plan_sources（宣言されたシナリオ名がキー）が復元できたときは、sources に各シナリオ自身の生 YAML を使う。復元できなければ従来どおり構造化データから作り直す
def scenario_source_meta(scenarios, plan_sources=None) -> tuple[list[str | None], list[list[int] | None]]  # (source_files, step_lines)。scenario_render_inputs の sources と対応する
```

`runner.run_and_report` が `write_report` を呼び、CLI に `(results, manifest_path)` を返します
（[run-loop](run-loop.md#runner実行パイプライン)）。CLI は全シナリオ成功なら終了コード 0、失敗で 1 です。

## レポートの再生成（BE-0068）

レポートは **run dir に保存されたデータの純粋なレンダリング**です。そのため、完了した run を再実行せずに現行テンプレでオフライン再描画できます。`manifest.json` が**バージョン付き**（`schemaVersion`）で無損失の render モデルで、`report.load` がその逆変換です。`results_from_manifest()` が `RunResult` を復元し、`load_run(run_dir)` が render モデル全体（outcome は `manifest.json`、シナリオの plan は `scenario.yaml`）を復元します。

`bajutsu report <run>`（[cli](cli.md#report)）は、その render モデルを使って `report.html`、`junit.xml`、`ctrf.json` を書き直します。再描画は記録済みの outcome を再提示するだけで、assertion を再実行したり verdict を変えたりしません。古い run も、新しいバージョンにしかないセクションを捏造せず「not captured」と表示して描画します。

## プルリクエストへの証跡の投稿（BE-0504）

コンポジット action の `post-evidence` は、完了した run をプルリクエストまたは issue に投稿します。読むのは `manifest.json` だけで、manifest がない run では `<sid>/result.json` を読みます。Bajutsu のインストールも Bajutsu のコマンド呼び出しも要らないため、run ディレクトリを生成するリポジトリならどこでも使えます。

コメントには、失敗したシナリオごとに、最初に失敗したステップと失敗理由を載せます。action が GitHub Actions のアーティファクトとしてアップロードした証跡へのリンクも付けます。再実行すると、コメントの先頭行にある隠しマーカーで同じコメントを見つけて更新します。そのため、ジョブごとにコメントは 1 つだけです。

```yaml
permissions:
  contents: read
  pull-requests: write   # 対象が issue の場合は issues: write
steps:
  - run: uv run bajutsu run --scenario scenarios/smoke.yaml --target web
  - name: Post run evidence
    if: always()
    uses: bajutsu-e2e/bajutsu/.github/actions/post-evidence@<ref>
```

action は次の 3 種類のアーティファクトをアップロードします。

- **run 全体の zip 1 つ。** 展開して `report.html` を開くと、オフラインでも動きます。
- **失敗シナリオごとのスクリーンショット。** 失敗したシナリオのうち最初の 3 件が対象です。失敗したステップに `after.png` があればそれを選び、zip にせずアップロードします。
- **失敗シナリオごとの動画。** 同じ 3 件が対象で、zip にせずアップロードします。

zip にしないアップロードでは、ファイル名がそのままアーティファクト名になります。そこで action は、各ファイルを `<artifact-name>-screenshot-N` または `<artifact-name>-video-N` の名前にコピーします。ブラウザがこのファイルを表示するのか、ダウンロードするのかは、まだ確認できていません。どちらの場合でも、リンクからファイルは受け取れます。アーティファクトの URL はサインインを必要とするため、閲覧者にはリポジトリの読み取り権限が要ります。

### 入力

| 入力 | デフォルト | 意味 |
|---|---|---|
| `run-dir` | `runs` | run ディレクトリ 1 つ、または複数の run を含むルート。 |
| `artifact-name` | ワークフロー名とジョブ名 | zip の名前であり、ほかのアーティファクトの接頭辞。マトリックスジョブでは matrix の値を加える。 |
| `retention-days` | `14` | GitHub が各アーティファクトを保持する日数（1 から 90）。 |
| `compression-level` | `6` | zip の zlib 圧縮レベル。ほとんどが動画の run では `0` にする。 |
| `log-tail-kb` | `1024` | これより大きい `deviceLog` は、zip では末尾の `log-tail-kb` KB だけを残す。`0` ならログを丸ごと残す。 |
| `size-warn-mb` | `500` | ステージしたサイズがこれを超えると、zip のアップロードを省く。`0` でこの保護を無効にする。 |
| `upload` | `true` | `false` にすると何もアップロードせず、`artifact-url` をリンクする。 |
| `artifact-url` | 空 | `upload` が `false` のときにリンクするアーティファクト。`bajutsu-e2e` action が出力する。 |
| `issue-number` | プルリクエスト | 番号がなければ、action はジョブサマリーだけを書く。 |
| `github-token` | `github.token` | コメントを書くトークン。 |
| `comment-key` | ワークフロー名とジョブ名 | 固定コメントのキー。マトリックスジョブでは matrix の値を加える。 |
| `comment-author` | `github-actions[bot]` | 固定コメントの所有者のログイン名。App のトークンでは App のログイン名にする。 |
| `max-failures` | `10` | 全文を載せる失敗シナリオの数。 |
| `evidence-base-url` | 空 | run ディレクトリを配信するベース URL。指定するとスクリーンショットがインラインで表示される。 |

### 知っておく挙動

- **action はジョブの結果を変えません。** アップロードの失敗、読み取り専用のトークン（フォークからのプルリクエスト）、API エラーは、警告とジョブサマリーになります。合否を決めるのは、テストステップ自身の終了コードです。ステージとコメントのステップは `continue-on-error` でも実行するので、スクリプトの想定外のクラッシュも警告に変わります。
- **途中で打ち切られた run には manifest が残りません。** その場合、コメントは run が完了しなかったことを伝えます。
- **切り詰めはマスキングではありません。** `log-tail-kb` を超えるデバイスログは末尾だけに切り詰められ、先頭行に元のサイズが記録されます。完全なログはどこにもアップロードされません。ステージしたコピーには、ログがもともと含んでいる文字列がそのまま残ります。コメント自体が載せるのは `manifest.json` が記録した文字列だけで、run がすでにマスキングしています。スクリーンショットや動画をジョブの外に出せない場合は、`upload: false` にします。
- **`pull_request_target` は非対応です。** このトリガーは、フォークのコードを実行しなければならないジョブに書き込みトークンを渡してしまいます。
- **証跡のベース URL** を指定すると、スクリーンショットがコメント内に表示され、`<base>/report.html` へのリンクが付きます。action がそこへアップロードすることはありません。署名付き URL は、期限が切れると画像が壊れます。
- **manifest のバージョン。** レンダラーが読むのは最初の `schemaVersion` から存在するフィールドだけなので、記録されたどのバージョンでも描画できます。バージョンが欠けている、または認識できない manifest では、シナリオの詳細の代わりに通知が表示されます。

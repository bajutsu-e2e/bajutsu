[English](../cli.md) · **日本語**

# CLI リファレンス

> 実装: `bajutsu/cli/`（Typer。コマンドごとに `cli/commands/` の 1 ファイル）。エントリポイントは `pyproject.toml` の `bajutsu = "bajutsu.cli:app"`。
> この CLI（コマンドラインインターフェース）のすべてのコマンドは `--target <name>` で 1 アプリを選び、
> `--config`（既定 `bajutsu.config.yaml`）で設定を指します。アプリ固有の差分は config 側にあります（[configuration](configuration.md)）。

関連: [run-loop](run-loop.md) ・ [recording](recording.md) ・ [codegen](codegen.md) ・ [configuration](configuration.md)

---

## 共通

- 全コマンドの前に `.env` を読み込む（`_bootstrap`、下記）。
- config が無い / アプリ未定義 / actuator 無し → メッセージを出して **終了コード 2**。
- `--backend` はカンマ区切り（例 `idb`）。空なら config の `backend` を使う。先頭から
  順に可用性を確認し、**最初に使えるものが actuator** になる（[drivers](drivers.md#バックエンド選択と-actuator)）。

## `run`

シナリオを **決定的に実行**します。合否は機械判定のみです。唯一の AI コンポーネントは**アラートガード**（シナリオごとに既定 ON）で、ステップをブロックした OS プロンプトを片付けるためだけに動作します。詳しくは [`dismissAlerts`](scenarios.md#dismissalertsシステムアラートガード) を参照してください。

```bash
bajutsu run --target <name> [--scenario <file.yaml>] [options]
```

既定では、そのアプリの設定済みシナリオディレクトリ（`targets.<name>.scenarios`、[configuration](configuration.md) 参照）内の
**すべての `*.yaml`** を読み込んで実行します。config だけで実行できます。単一ファイルだけ実行するには `--scenario <file>` を渡してください。

| オプション | 既定 | 説明 |
|---|---|---|
| `--target` | （必須） | 対象アプリ（config の `targets.<name>`） |
| `--scenario` | config の `scenarios` ディレクトリ | アプリのシナリオディレクトリ全体ではなく単一の `*.yaml` を実行 |
| `--backend` | config | actuator 順（カンマ区切り。先頭から最初に使えるもの） |
| `--tag` | "" | カンマ区切り。これらの tag のいずれかを持つシナリオのみ実行 |
| `--exclude` | "" | カンマ区切り。これらの tag のいずれかを持つシナリオをスキップ |
| `--udid` | `booted` | 対象 Simulator（カンマ区切り = `--workers` 用のデバイスプール） |
| `--erase / --no-erase` | シナリオ準拠 | 各シナリオの `preconditions.erase`（シム全体を wipe）を上書き。省略時は各シナリオの指定に従う。アプリはどちらでも毎回 fresh に再インストール（config `appPath` + `preconditions.reinstall`） |
| `--dismiss-alerts / --no-dismiss-alerts` | シナリオ準拠（ON） | 各シナリオの `dismissAlerts` を上書きします。idb から見えないシステムアラートを視覚で消すガードです。省略時は各シナリオの指定に従います（設定した AI プロバイダを使用。`ANTHROPIC_API_KEY`、Bedrock なら AWS 認証情報。[recording](recording.md#システムアラートの自動対処)） |
| `--alert-instruction` | "" | 既定のボタン指示（シナリオ自身の `dismissAlerts.instruction` が勝つ） |
| `--log-predicate` | "" | `deviceLog` ストリームを絞る NSPredicate（例 subsystem） |
| `--log-subsystem` | "" | `appTrace` 用の os_log subsystem（既定はアプリの `bundleId`） |
| `--network / --no-network` | `--network` | `request` アサーション用にアプリの通信を収集（iOS はアプリに BajutsuKit が必要。web は Playwright でネイティブに観測し、シナリオの `mocks` をその場でスタブする） |
| `--workers` | 1 | デバイスプール上で並列実行する。iOS では `--udid u1,u2,…` が必要で、そのプール数で上限になる。web では `--workers N` だけで N 本の並列ブラウザコンテキストレーンになる（`--udid` 不要、[BE-0054](../../roadmaps/implemented/BE-0054-web-backend-completion/BE-0054-web-backend-completion-ja.md)）。各レーンが自前のネットワークコレクタ、インターバル録画、（iOS では）デバイス制御を持つので、network / 動画 / `setLocation` / `push` はシングルデバイス実行と同じく機能する |
| `--baselines` | シナリオ隣の `baselines/` | `visual` アサーション用のベースライン画像ディレクトリ。`baseline: home.png` はこの中で解決される |
| `--schemas` | シナリオ隣の `schemas/` | `responseSchema` アサーション用の JSON Schema ファイルのディレクトリ。`schema: items.json` はこの中で解決される（`schema` extra が必要） |
| `--headed / --no-headed` | アプリの `headless`（既定はヘッドレス） | web backend: ヘッドレスの代わりにブラウザを画面に表示し（低速再生）、実行の各ステップを確認できる（コマンドを実行しているマシン上でウィンドウが開く）。省略時はアプリの `headless` 設定に従う。iOS は無視する |
| `--progress / --no-progress` | off | シナリオ / ステップごとの進捗を stderr に流す（`serve` UI が消費する） |
| `--zip` | off | run の後に `runs/<id>.zip` も書き出す。レポートと証跡をまとめた1つの可搬な成果物で、CI アップロードや共有に使える。**判定の後**に走るので pass/fail に影響しない。[`export`](#export) 参照 |
| `--config` | `bajutsu.config.yaml` | config ファイル |

- 証跡は `FileSink(runs/<runId>, udid=..., log_predicate=...)` に書きます（[evidence](evidence.md#sink証跡の出力先)）。
- `runId` は `YYYYMMDD-HHMMSS`。
- 出力: `PASS|FAIL  runs/<runId>/manifest.json`。**終了コードは全シナリオ成功で 0、失敗で 1**。
- run 内で唯一 AI を使うアラートガードが実際に発火したときは、結果の後に消費トークン量を示す
  `AI usage:` 行を **stderr** に出力します（stdout は機械可読の結果 1 行のままです）。AI を使わな
  かった run では何も出力しません。

```bash
bajutsu run --target sample --udid <UDID> --backend idb --no-erase            # アプリのシナリオディレクトリ全体
bajutsu run --scenario demos/features/app/scenarios/smoke.yaml --target sample --no-erase   # 単一ファイル
```

## `doctor`

**実行可能ゲート** + 現在画面の **規約充足度スコア**（AI 非依存。[configuration](configuration.md#doctor規約充足度スコア)）。

```bash
bajutsu doctor --target <name> [--udid booted] [--backend ...] [--config ...]
```

- まず env ゲート（`preflight.py`）: actuator が必要とする CLI（`xcrun`、idb なら `idb` /
  `idb_companion`）と**起動済みシミュレータ**を ✓/✗ チェックリストで表示します。不足があれば**終了 1**（直し方ヒント付きで即失敗）。
- 次に actuator で `query()` し、`score(elements, idNamespaces)` を表示します。**grade が Blocked で 1、それ以外 0**。

## `audit`

シナリオの **静的な決定性スコア**です。`doctor` の規約充足度スコアの、デバイスを使わない従兄弟にあたります（AI 非依存。[selectors](selectors.md)・[BE-0049](../../roadmaps/implemented/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)）。シナリオファイルを読み込み（`trace --explain` と同様に components / data を展開）、実行せずに、各シナリオがどれだけ再現可能かを報告します。

```bash
bajutsu audit <scenario.yaml> [--json]
```

- 各セレクタを**安定度ラダー**（[selectors](selectors.md)）で採点します。一意な `id` / `idMatches` は **stable**、`label` / `labelMatches` / `traits` / `value` は **moderate**（id を伴わない補助的指定）、`index`（複数一致の n 番目）は **fragile** です。加えて、**座標ジェスチャ**（`swipe {from,to}`。安定した id で置き換えられる）と**緩すぎる wait**（`until: screenChanged` / `settled`。具体的な条件を待たない）を指摘します。
- シナリオごとに `grade`（`Stable` / `Moderate` / `Fragile`）、安定度の割合、位置付きの findings を出力します。テキスト、または `--json` で機械可読に出せます。
- **助言的かつ read-only** です。シナリオを実行も編集もせず、**CI ゲートにもなりません**。成功した監査は **finding があっても終了 0**（シナリオファイルが無い/読めないときだけ終了 2）です。finding は直すべき箇所であって判定ではありません。flake を隠す retry-to-pass の逆の発想です。

静的な採点に加えて、**観測**によって決定性を示すモードが2つあります。

```bash
bajutsu audit <scenario.yaml> --repeat K --target <name>   # K 回実行して結果を差分する
bajutsu audit --history <runs-dir>                         # 過去の run からフレーキネスを掘り起こす
```

- `--repeat K` は同一の前提条件でシナリオを `K` 回実行し、結果が変動したものを報告します（`deterministic` か `flaky` か）。変動は直すべき finding であって、赤を緑に変える retry ではありません。
- `--history <runs-dir>` は**経時ビュー**です。各シナリオの蓄積した run を、その run のシナリオ**フィンガープリント**（各 `manifest.json` が持つ `provenance.scenarioHash`）でグルーピングし、シナリオごとに分類します（`flaky` / `deterministic` / `unproven`）。フィンガープリントが一定のまま verdict がブレていれば、それは*真の*フレーキネスです。シナリオを編集するとハッシュが変わって新しいグループになるため、編集とは区別されます。フィンガープリントに加えてシナリオ名でもキーを引くので、スイートの*どの*シナリオがブレたのかまで特定できます。フィンガープリントを持たない run（provenance 導入前）はグルーピングできず、skip として報告します。他のモードと同様 read-only で、**フレーキネスを検出しても終了 0**（runs ディレクトリが無いときだけ終了 2）です。

## `coverage`

スイートの **静的な e2e カバレッジマップ**です。`doctor` の規約充足度スコアの、read-only な従兄弟にあたります（AI 非依存。[BE-0050](../../roadmaps/in-progress/BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map-ja.md)）。`doctor` が1画面の*提供する* id を採点するのに対し、こちらはスイート全体が*行使する* id を採点します。アプリの設定済み `scenarios` ディレクトリの `*.yaml` をすべて読み込み（components / data を展開）、参照している安定した id を namespace ごとにまとめ、アプリが宣言した `idNamespaces`（[configuration](configuration.md#doctor規約充足度スコア)）に対して、実行せずに突き合わせます。

```bash
bajutsu coverage --target <name> [--config ...] [--runs <dir>] [--crawl <screenmap>] [--json] [--html <path>]
```

- **カバレッジの割合**（スイートが参照する宣言済み namespace 数 / 宣言済み namespace 数）、各 namespace に触れる **namespace ごとの id**、**gap 一覧**（どのシナリオも参照しない宣言済み namespace。すなわち未テストの範囲）、**off-namespace な id**（参照されているが宣言されていない namespace の id）を報告します。テキスト、または `--json` で機械可読に出せます。
- 参照している id とは、シナリオが指定する `id` / `idMatches` のすべてです。ステップ、入れ子の制御フロー、`within` スコープ、アサーションを横断して集めます。
- **`--runs <dir>`** を渡すと、run の証跡に基づく次元が2つ加わります。
  - **エンドポイントカバレッジ**: runs ディレクトリ配下のすべての `network.json`（観測した通信の和集合）を読み、**観測したエンドポイント**（`METHOD path`）のうちスイートのネットワークアサーション（`request` / `event` / `requestSequence`）がカバーしている割合を測ります。アサート済みの割合、**未アサートの観測エンドポイント**（スイートが一度もアサートしないトラフィック）、どの run でも**観測されなかった宣言マッチャ**を報告します。
  - **観測 id カバレッジ**: runs ディレクトリ配下のステップごとの `elements.json` をすべて読み、run が実際に**描画した**安定 id（各要素の `identifier`。null や空文字の id は除く）を集め、宣言済みの namespace ごとにまとめます。静的な id マップに対する、run の証跡側の対応物です。namespace ごとの観測 id、**どの run でも描画されなかった** namespace、宣言されていない namespace に属する観測 id（**off-namespace**）を報告します。

  `--runs` を省くと従来どおり静的な id-namespace マップのみです。
- **`--crawl <screenmap>`**（`--runs` と併用）を渡すと、自律クロールが発見した面に対する **screens-visited**
  次元が加わります（[BE-0038](../../roadmaps/in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)）。
  **分母**はクロールが見つけた画面（`screenmap.json` のノード。ファイルでもその run ディレクトリでも渡せます）、
  **分子**は run セットが到達した画面です。ステップごとの `elements.json` を*同じ* `crawl.fingerprint` で
  指紋化するので、訪問した画面が発見した画面と突き合わせられます。到達した割合と、クロールは発見したのに
  どの run も触れなかった **unvisited** な画面を報告します。訪問の証跡には `--runs` が必要で、`--crawl` だけを
  渡した場合はこの次元を警告つきでスキップします。
- **`--html <path>`** を渡すと、同じ数値を **自己完結した HTML レポート**にも書き出します（CSS は埋め込み、JavaScript も外部アセットも無し。ディスクから直接開けます）。次元ごとにカバレッジバーを描き、gap・off-namespace・unvisited の一覧を目立たせます。エンドポイント・観測 id・screens-visited のセクションは `--runs`（screens は加えて `--crawl`）を渡したときだけ描画します。テキスト（または `--json`）の出力は変わらず、書き出し先は標準エラー出力で知らせます。
- **助言的かつ read-only** です。シナリオを実行も編集もせず、**CI ゲートにもなりません**。**gap があっても終了 0**（config / scenarios ディレクトリが無い、またはシナリオが読めないときだけ終了 2）です。gap は埋めるべき namespace であって判定ではありません。

## `export`

完了した run を1つの可搬な `.zip` にまとめます。`report.html` に加えて `manifest.json`・`junit.xml`・実行した `scenario.yaml`・**すべての**証跡（スクリーンショット、動画、`network.json` …）を含みます（[BE-0060](../../roadmaps/implemented/BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md)）。`runs/<id>/` のツリー全体を単一の `<id>/` フォルダ直下に収めるので、`report.html` の**相対**リンクがオフラインで解決します。ダブルクリックで開け、サーバは要りません。

```bash
bajutsu export <run-id | run-dir> [-o out.zip] [--force]
```

- `<run>` は run id（`--runs` 既定 `runs/` の下で解決）または run ディレクトリのパスです。
- 出力は既定で run dir の隣の `<id>.zip`。`-o/--output` で上書き指定。`--force` なしでは既存ファイルを**上書きしません**（`record` が黙って上書きしないのと同じ）。
- run が既に書いたものを詰めるだけで、デバイスも AI も使わず、判定にも影響しません。**run dir の中だけ**を固めます（`.env`・config・その上位には一切触れません）。run の secret スクラブをそのまま継承します。`bajutsu run --zip` は同じアーカイバを判定後にインラインで走らせ、`bajutsu serve` は埋め込みレポートの隣に **Download** ボタンとして提供します。

## `trace`

完了した run を**テキストタイムライン**で検査します。シナリオごとに、ステップと観測した通信を
時系列で交互に並べ、続けて expectations、app-trace 区間、証跡サマリを出力します。読み取り専用
（保存済みの `manifest.json` / `network.json` / `appTrace.json`、来歴のための `scenario.yaml` を読む）。

- 各ステップには、それを記録した元の自然言語フレーズ — [`from:`](scenarios.md#from来歴) 来歴（BE-0044）
  — を `← "<フレーズ>"` の形でインライン表示します。1 つのフレーズを共有する連続ステップはまとめて 1 回だけ
  ラベル表示します。手書きのシナリオや来歴のない古い run では、単に何も表示されません。

```bash
bajutsu trace [<run-dir>] [--scenario <substr>] [--runs runs]
bajutsu trace --explain <scenario.yaml>     # 実行前のドライラン（デバイス不要）
```

- `<run-dir>` 省略時は `runs/` 直下の最新 run。`--scenario` は名前の部分一致で絞り込み。
- run が見つからなければ**終了 2**。
- **`--explain <scenario.yaml>`** は*実行前*版です。完了した run を読むのではなく、そのシナリオの
  `capturePolicy` がどう発火するかを事前に表示します（BE-0028）。action トリガのルールは正確に回数を
  数えて該当ステップを列挙し、`event` / `result` ルールは実行時依存として報告します。広くマッチする
  ルールに heavy capture（`video` / `deviceLog` / `appTrace` / `network`）が付いていれば ⚠ で警告し、
  コストを払う前にマッチを絞り込めます。読み取り専用かつ決定的で、デバイスも LLM も不要です
  （components と data 行は展開しますが、config の `setup` プレリュードは含みません）。シナリオファイルが
  無ければ**終了 2**。

## `report`

完了した run の `report.html` を**保存済みデータ**から、**現行の**テンプレで再描画します（`junit.xml` も再出力）。デバイスも LLM も使わず、再実行もしません（[BE-0068](../../roadmaps/in-progress/BE-0068-regenerable-reports/BE-0068-regenerable-reports-ja.md)）。テンプレ改善やレンダリングのバグ修正が、再実行せずに過去の run にも届きます。合否は保存済みモデルから読むだけで、再計算しません。

```bash
bajutsu report <run-id | run-dir>      # 1 つの run を再描画
bajutsu report --all [--runs runs]     # runs/ 配下で manifest.json を持つ run dir をすべて再描画
```

- レンダリングモデルは `manifest.json`（バージョン付き・無損失の render 入力。`schemaVersion`）と、実行した `scenario.yaml` です。レンダラは run dir だけを読みます。**古い** run もエラーにならず描画でき、新しいバージョンにしかないセクションは値を捏造せず「not captured」と表示します。
- 記録済みの outcome を再提示するだけで、assertion を再評価したり verdict を変えたりしません。決定性の契約の内側に収まります。`serve` も**同じレンダラを表示時に使い**、リクエストごとに各 run の保存済みモデルから `report.html` を都度描画します（モデルを読めない場合は baked ファイルにフォールバック）。これにより `serve` を上げれば再 bake 不要で全レポートが最新化されます。
- run（`--all` の場合は runs ルート）に読める `manifest.json` が無ければ**終了 2**。

## `triage`

run 内の最初の**失敗**シナリオを診断し、最小の修正案を提示します。**助言のみ**で pass/fail は判定しません
（AI 境界）。失敗コンテキスト（落ちたステップと理由、失敗 expectation、失敗時の要素ツリー、シナリオ）
を組み立て `TriageAgent` に渡します。既定はルールベース（`HeuristicTriageAgent`、API キー不要）で、失敗を
分類（selector / timing / assertion）し、対象 id が画面に無く似た id があれば「もしかして…?」を提案
します（id リネームの自己修復）。`--ai` は同じコンテキストに失敗**スクショ**を加えて推論する Claude 版に差し替えます
（`ANTHROPIC_API_KEY` が必要）。

エージェントは適用可能な**構造化 fix**（`renameId` / `addIndex` 曖昧一致の一意化 / `raiseTimeout`）も
返せます。`--apply <scenario-file>` が **dry-run diff** を表示、`--write` が source に適用、`--rerun --target
<name>` が patched シナリオを再実行（`--no-erase`）して緑になったか報告します。境界は保たれます: fix はユーザーが
diff をレビューして opt-in した時のみ適用され、断片が source に一致しなければ安全に no-op です。

```bash
bajutsu triage [<run-dir>] [--scenario <substr>] [--runs runs] [--ai]
bajutsu triage [<run-dir>] --ai --apply <scenario-file> [--write] \
               [--rerun --target <name> [--backend idb] [--udid <udid>]]
```

- 既定は `runs/` 直下の最新 run。失敗シナリオが無ければ**終了 0**。
- `--rerun` は `--write` と `--target` が必要。
- `--ai` のときは、診断後に消費トークン量を示す `AI usage:` 行を stderr に出力します。既定のルール
  ベースは AI を使わないので何も出力しません。

## `record`

AI でゴールに向けて探索し、**記録したシナリオを書き出します**（Tier 1・[recording](recording.md)）。
既定ではアプリの設定済みシナリオディレクトリ（`targets.<name>.scenarios`）配下に自動命名の `*.yaml` を書きます。
特定パスに書くには `--out` を渡してください。

```bash
bajutsu record --target <name> --goal "<自然言語ゴール>" [--out <file.yaml>] [options]
```

| オプション | 既定 | 説明 |
|---|---|---|
| `--target` | （必須） | 対象アプリ |
| `--goal` | （必須） | 著すゴール（自然言語） |
| `--out` | config の `scenarios` 配下に自動命名 | 出力パスを明示（アプリのシナリオディレクトリを上書き） |
| `--name` | （ゴールから） | 自動命名するファイル名（`--out` 指定時は無視） |
| `--udid` | `booted` | 対象 Simulator |
| `--backend` | config | actuator 順 |
| `--erase / --no-erase` | `--erase` | 起動前に erase（アプリはインストール済みである必要） |
| `--dismiss-alerts` | off | オーサリング中のプロンプトを片付ける（要 API キー） |
| `--headed / --no-headed` | アプリの `headless` | web backend: ヘッドレスではなく目に見える（低速再生の）ブラウザでオーサリングする。省略時はアプリの `headless` 設定に従う |
| `--alert-instruction` | "" | 同上の押下指示 |
| `--config` | `bajutsu.config.yaml` | config |

- 内部で `launch_driver` → `record_loop(driver, goal, ClaudeAgent(), ...)` → `dump_scenarios` で書き出します。
- 出力: `recorded <N> steps -> <path>`。**要 `ANTHROPIC_API_KEY`**（`ClaudeAgent`）。
- **Git の `--config` は読み取り専用入力です**（[BE-0063](../../roadmaps/in-progress/BE-0063-git-config-source/BE-0063-git-config-source-ja.md)）。`record` は config を取得したチェックアウトから読みますが、生成したシナリオは**ローカル**へ書きます。`--out` 省略時は（読み取り専用の SHA キーのキャッシュであるチェックアウト内の `scenarios` ディレクトリではなく）**カレントディレクトリ**に自動命名し、チェックアウト内を指す `--out` は拒否します。そのファイルをレビューし、通常の git でリポジトリへコミットしてください。
- 続けて、オーサリング（およびアラートガード）の AI が消費したトークン量を示す `AI usage:` 行を
  stderr に出力します。`claude-code` エージェントはここで API トークンを消費しないため、何も表示
  されません。

## `crawl`

アプリを**幅優先**で探索し、到達できる画面と画面間の遷移の**画面マップ**を書き出します
（Tier 1・[BE-0038](../../roadmaps/in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)）。`record` は *目的指向* で、1 つの自然言語ゴールに
向かって AI が探索し、1 本のシナリオを書き出します。これに対し `crawl` は *体系的な発見* です。
到達できる画面を巡り、見つけたものを報告します。探索エンジンは**決定的**で（画面の識別子と候補
アクションを試す順序は、どちらも要素ツリーの純粋な関数です）、**AI は関与せず**、**合否ゲートには
決してなりません**。

```bash
bajutsu crawl --target <name> [--max-screens N] [--max-steps N] [--out <dir>] [options]
```

| オプション | 既定 | 説明 |
|---|---|---|
| `--target` | （必須） | 対象アプリ |
| `--max-screens` | `50` | この数の異なる画面を発見したら停止 |
| `--max-steps` | `200` | この数のアクションを実行したら停止 |
| `--agent` | `$BAJUTSU_AGENT` または `api` | クロールガイドの AI バックエンド。`api`（Anthropic SDK、従量課金。設定した AI プロバイダを使用し、Anthropic なら `ANTHROPIC_API_KEY`、`BAJUTSU_AI_PROVIDER=bedrock` なら AWS 認証情報 + `BAJUTSU_BEDROCK_MODEL`）か `claude-code`（Claude Code CLI。サブスクリプションを利用、テキストのみ。`record --agent claude-code` と同様）。省略時は `$BAJUTSU_AGENT`（`serve` が Settings の選択から設定）に従い、なければ `api` |
| `--udid` | `booted` | 対象 Simulator。カンマ区切り（`A,B,C`）で並列プールも指定できる（`--workers` 参照） |
| `--workers` | `1` | 同時に動かすワーカー数。1 つの画面マップを共有する。iOS は同数のシミュレータ（[BE-0064](../../roadmaps/implemented/BE-0064-parallel-crawl/BE-0064-parallel-crawl-ja.md)、`--udid` のデバイス数で上限）、web は同数のブラウザプロセス（[BE-0077](../../roadmaps/implemented/BE-0077-parallel-web-crawl/BE-0077-parallel-web-crawl-ja.md)）。`1` はシングルワーカーのクロール |
| `--backend` | config | actuator 順 |
| `--erase / --no-erase` | `--erase` | 起動前に erase（アプリはインストール済みである必要） |
| `--dismiss-alerts / --no-dismiss-alerts` | `--dismiss-alerts` | クロール中に予期せぬ OS プロンプトを片付ける（クラッシュ誤判定を防ぐ。設定した AI プロバイダを使用し、`ANTHROPIC_API_KEY`、Bedrock なら AWS 認証情報） |
| `--headed / --no-headed` | アプリの `headless` | web backend: ヘッドレスではなく目に見える（低速再生の）ブラウザでクロールする。省略時はアプリの `headless` 設定に従う |
| `--out` | `runs/<timestamp>` | 画面マップを書き出す run ディレクトリ |
| `--config` | `bajutsu.config.yaml` | config |

- **Git の `--config` は読み取り専用入力です**（[BE-0063](../../roadmaps/in-progress/BE-0063-git-config-source/BE-0063-git-config-source-ja.md)）。`crawl` は config を取得したチェックアウトから読みますが、画面マップ／スクリーンショットはローカルの `--out` run ディレクトリ（既定 `runs/<timestamp>`）に書き、読み取り専用の SHA キーのキャッシュには書きません。チェックアウト内を指す `--out` は拒否します。
- 走査は**決定的リプレイ**で行い、その場での後戻りはしません。既知の画面を再訪するには、アプリを
  クリーンな状態に再起動し、そこへの最短経路を再生してから次の未試行アクションを取ります。これは
  `run` が任意の状態へ到達するのと同じやり方です。
- 無効化された操作要素（`notEnabled`）はタップせず、画面ごとに `blocked` として報告します。遷移を洗い出すため、
  crawl は操作要素の状態の**組合せ**を探索します。各空テキスト欄を個別に入力し（スイッチは個別にトグル、タブバーは各タブに切り替え）、
  空欄が複数あるときは**一括入力（compound fill）**も試します。一括入力が重要なのは、操作要素が *複数* の欄が
  揃って初めて有効化される場合があり、かつ途中の単独入力が不可視（マスクされたパスワードは値を公開しない）な
  ことが多く、1つずつ埋めても全充足状態へ到達できないためです。クロールは **AI 駆動**です。
  まず画面を決定的に検査し、その操作を Claude に渡して**組合せ**を
  考えさせ、有効化条件が自明でない操作要素のために**現実的な入力**（正しいメール形式、規約を満たすパスワード、
  フォーム全欄の一括入力など）や id 無し要素への操作を提案し、その推論を run ログに流します。
  AI は**個々のタブをツリーで指定できないタブバー**にも対応します。idb は SwiftUI の TabView を
  「Tab Bar」というラベルの group 1個（タブごとの id 無し）として返すため、バーは見えてもタブをセレクタで
  タップできません。そうしたバーがあり（かつ id で指定できるタブが無い）場合に、システムアラートガードと同じ要領で
  画像認識（vision）から位置を割り出し、各タブを座標でタップします（タブを優先して切り替えてから先の画面へ進む方針は同じ）。
  （UIKit のタブバー、つまり idb が各タブを個別要素として返すものは今後の対応予定で、現状は同じ vision 経路にフォールバックします。）
  AI は「何を試すか」を選ぶだけで、画面同一性、遷移、クラッシュ判定は決定的のままです。よって crawl は合否を下さず、
  CI ゲートにもなりません。
- 出力: `<out>/screenmap.json`。`nodes`（画面。fingerprint、種別、id、候補アクション、`blocked` 無効操作要素を持つ）、
  `edges`（遷移）、`crashes`（アプリ UI を崩壊させたアクション経路）、`alerts`（クロール中にガードが閉じた OS
  プロンプト。誘発した経路とタップしたボタンを記録する）、`plan`（探索フロンティア。画面ごとの未試行操作で、クロールの
  進行に合わせて更新されるので、読み手は次に何を試すか把握できます）、`stop_reason`（`completed` / `max_screens` /
  `max_steps`）からなる JSON グラフです。あわせて `<out>/screens/<fingerprint>.png`、すなわち発見した各画面の
  スクリーンショット（その画面にいる間に撮影）も出力します。クロールの進行に合わせて書き直されるので、読み手
  （`serve` の **Crawl** タブ）はマップをリアルタイムに描けます。各画面はラベルと情報のノードで表示され、同じ UI で
  状態だけ違う画面（フォーム未入力と入力済みなど）は 1 つのノードにまとめられ、その場で展開できます。
  `--max-screens` / `--max-steps` のいずれか早い方で停止します。
- 完了時には `<out>/screenmap.html` も書き出します。これは**自己完結**したレポート（CSS は埋め込み、JavaScript も
  外部アセットも無し）で、ライブの **Crawl** タブのオフライン版にあたります。発見した画面を幅優先の深さ列に並べて
  スクリーンショットを添え、遷移を静的なインライン SVG で描き（OS アラートをタップして通過した遷移は 🛡️ マーク付きの
  アンバー色）、クラッシュと閉じたアラートの経路を下にまとめます。`screenmap.json` や `screens/` と同じ場所に置くので
  run ディレクトリから直接開け、web UI なしで共有・保管できます。JSON と同じく read-only でモデルも使いません。
- **並列プール**（[BE-0064](../../roadmaps/implemented/BE-0064-parallel-crawl/BE-0064-parallel-crawl-ja.md)）:
  `--udid A,B,C` のプールに `--workers N` を組み合わせると、起動済みのシミュレータ N 台で同時にクロールします。
  全ワーカーが 1 つの画面マップとフロンティアを共有し、独立した枝が並行して進み、AI ガイドの往復もデバイス間で
  重なるので、実時間はおおよそ台数に反比例して減ります。並行になるのは**スケジューリングだけ**です。どのワーカーが
  画面に先に到達するかは時間依存なので、アプリ自体に非決定性があると記録されるパスや発見順は実行ごとに変わりえますが、
  画面同一性・遷移・クラッシュ判定は要素ツリーの同じ決定的関数のままです（クロールは依然として合否を下しません）。
  固まったデバイスは自分の作業を手放し、残りのワーカーが続行します。既定の `--workers 1` は従来どおりのシングル
  ワーカーのクロールです。**web** でも同じ仕組みが働き、シミュレータの代わりに N 個の**ブラウザプロセス**を動かします
  （[BE-0077](../../roadmaps/implemented/BE-0077-parallel-web-crawl/BE-0077-parallel-web-crawl-ja.md)）。web に端末はない
  のでワーカー数だけでレーン数が決まり、リセットのたびに新しいブラウザコンテキスト（クリーンな初期状態 = `erase` 相当）
  を開きます。固まったブラウザは破棄して再起動するので、1 つの不調がクロール全体を沈めることはありません。

### 画面の同定方法（fingerprint）

各画面は **fingerprint**（再訪と新規画面を区別するための、短く安定した識別子）に還元されます。これは
要素ツリーだけから決まる純粋で決定的な関数であり（AI もスクリーンショットのピクセルも使いません）、これが画面
マップの再現性を支えます。同じ画面は常に同じ値にハッシュされます。各ノードは fingerprint とその種別（`kind`）を
記録します。

- **id fingerprint（`kind: "id"`、通常時）.** 画面上に存在するアクセシビリティ id の**集合**をソートして
  ハッシュします。表示テキストではなく「どの id が存在するか」を鍵にするので、ロケールやデータが変わっても同定は
  安定します（行の内容が違うリストや、別言語のラベルでも同じ画面）。さらに各 id には、**操作上の状態**が既定から
  外れているときに限ってマーカーを付けます。これにより同じレイアウトでも状態が違えば別のハッシュになり、クロールが
  状態の組み合わせを探索できます。
  - `!`：**無効**なコントロール（`notEnabled`）、
  - `=`：**入力済み**のテキスト入力（値が入ったフィールド）、
  - `+`：**選択 / トグル**されたコントロール（ON のスイッチ、選択中のタブ）。

  有効で空かつ未選択の画面は素の id だけを寄与するので、そうした状態を持たない画面は素の id 集合とまったく同じ値に
  ハッシュされます。フォームを入力したりスイッチを切り替えたりすると*新しい*ノードになるのはこのためで、これは別個に
  探索できる固有の状態です。前提条件を満たして初めて使えるようになる操作（全フィールド入力後に有効化される送信
  ボタンなど）を、クロールはこの仕組みで発見します。
- **構造 fingerprint（`kind: "structural"`、フォールバック）.** 画面のアクセシビリティ id が 2 つ未満で同定に
  使うには薄すぎる場合、代わりに**操作可能要素のトレイトを、画面上の大まかな位置でバケット化**してハッシュします。
  これはレイアウトの揺れで変わりうるうえ、コントロール構成が似た無関係な画面と衝突しうるため安定度が低く、識別が
  近似であることを示す `structural` が付きます。アクセシビリティ id のカバレッジを上げる（`doctor` 参照）と、その
  画面は安定した id 方式に戻ります。
- **表示上のグループ化は別の、見た目だけの処理です.** 上記の fingerprint がマップに保存される*状態ごとの*厳密な
  識別子です。**Crawl** タブはこれに加えて、同じ画面で一時的な状態だけが違うノード（未入力と入力済みのフォーム、
  トグル、要素を少し足すアラート/オーバーレイ）を 1 つの展開可能なユニットに*まとめ*、グラフを読みやすくします。
  このグループ化は fingerprint やクロールの探索内容を一切変えず、描画を畳むだけです。

### web backend（`--backend web`）

クロールは Simulator に対するのと同じように web アプリにも走ります。探索エンジンはプラットフォーム非依存
なので、`bajutsu crawl --target <web-app> --backend web` は同じ `screenmap.json`・スクリーンショット・クラッシュ
一覧を出力します。web アプリは `bundleId` ではなく `baseUrl` で指定し、ブラウザは Mac もエミュレータも要らない
ので、web クロールは Linux の `make check` / CI ゲート内で走ります。iOS と違う点は次の 3 つで、いずれも決定的
です（[BE-0066](../../roadmaps/implemented/BE-0066-web-crawl/BE-0066-web-crawl-ja.md)）。

- **クリーンな起点 = 再ナビゲート。** 再起動するアプリプロセスはありません。クリーンな状態に戻すのは、新しい
  ブラウザコンテキストに対する `page.goto(baseUrl)`（`erase` 相当で、ほぼ無償）です。`run` が使うのと同じ
  ライフサイクルの接合点です。
- **クラッシュ検出。** iOS の信号（アクセシビリティツリーの崩壊）は web には存在しないため、ブラウザ自身の
  決定的な信号を使います。未捕捉の JS 例外（`pageerror`）、メインフレームの 4xx/5xx ナビゲーション、空の
  ドキュメントの 3 つです。どれも機械的な事実（イベント・ステータス番号・空の要素集合）であり、ページが
  「壊れて見えるか」をモデルが判断することはありません。
- **OS アラートではなくダイアログ。** web に OS プロンプトはなく、JS ダイアログ（`alert` / `confirm` /
  `beforeunload`）があります。これらは固定のモデル非依存ポリシー（dismiss）で自動処理し、`alerts` に記録します。
  iOS の vision アラートガードの置き換えです。`--dismiss-alerts` と vision 経路は iOS 専用で、`--headed` は
  web で有効です（可視ブラウザでクロールを見られます）。

## `codegen`

シナリオから **ネイティブテスト** を生成します（AI 非依存・構造マッピング・[codegen](codegen.md)）。出力先は
**XCUITest**（Swift、iOS）または **Playwright**（TypeScript、web）です。

```bash
bajutsu codegen <scenario.yaml> --target <name> [--emit xcuitest | playwright] [-o <out>] [--config ...]
```

| オプション | 既定 | 説明 |
|---|---|---|
| `--emit` | `xcuitest` | 出力形式。`xcuitest` または `playwright`（他は終了コード 2） |
| `-o, --out` | `-` | 出力ファイル。`-` で標準出力 |

- config の `launchEnv` が生成テストに入ります。XCUITest では `app.launchEnvironment`、Playwright では
  `localStorage` のシードに反映されます。
- `--emit playwright` は対象が web ターゲットであること（`targets.<name>.baseUrl`）を要求し、なければ終了コード 2 で
  終わります。
- ファイル出力時は `wrote <N> scenario(s) -> <out>`。

## `approve`

実行で撮影したスクリーンショットを `visual` の**ベースライン**へ昇格させます。これはビジュアル
リグレッションのループ（run → 確認 → approve → 再 run）の後半にあたります。`manifest.json` を読むだけなので
**Simulator 不要**で CI でもヘッドレスに動作し、WebUI の **Approve** ボタンの CLI 版です。

```bash
bajutsu approve [<run_dir>] --baselines <dir> [--scenario <id>] [--all] [--runs runs]
```

| オプション | 既定 | 説明 |
|---|---|---|
| `<run_dir>` | `runs/` 配下の最新 | 承認元の実行 |
| `--baselines` | （必須） | 昇格した PNG を書き出す先 |
| `--scenario` | "" | この scenario id のみ（実行ディレクトリの `00-home` など） |
| `--all` | off | 既に合格したベースラインも更新（既定: 失敗 / 不在のみ） |
| `--runs` | `runs` | `<run_dir>` 省略時に使う runs ルート |

- 各 visual チェックの `visual-actual.png` を `<dir>/<baseline>` へコピーします。1 件以上昇格すれば
  **終了 0**、対象が無ければ **1**。

## `serve`

**オーサリング、実行、探索**のためのローカル Web UI です。Tier 1 の利便機能で、**CI ゲートには含まれません**。
CLI を覆う 3 つのトップレベルタブがあります。**Record** はゴールからシナリオを著し（`python -m bajutsu
record ...`）、**Replay** はシナリオを実行してレポートを表示し（`python -m bajutsu run ...`）、**Crawl** は
アプリを探索して画面マップをリアルタイムに描きます（`python -m bajutsu crawl ...`）。リクエストごとに CLI を
バックグラウンドスレッドで起動し、出力をストリームし、生成された `runs/<id>/` ツリーを配信します（report の
相対アセットリンクと crawl の `screenmap.json` が解決します）。stdlib のみ（Web フレームワーク不要）、
`127.0.0.1` バインド。

```bash
bajutsu serve [--port 8765] [--config bajutsu.config.yaml] [--root .] [--runs runs] [--baselines <dir>]
              [--host 127.0.0.1] [--token <t>] [--max-concurrent-runs 4]
```

- **`--token`（または `$BAJUTSU_SERVE_TOKEN`）による認証（BE-0051）。** トークン設定時は全リクエストが認証必須です。
  API クライアントは `Authorization: Bearer <token>` を送り、ブラウザは `POST /api/login` で一度だけトークンを
  交換し（401 で UI が入力を促す）、HttpOnly かつ SameSite のセッション Cookie を受け取ります。トークンは URL に
  載せません。**非 loopback の `--host`（例 `0.0.0.0`）への bind はトークン必須**で、無認証での公開を防ぎます
  （無ければ起動を中止）。トークン未設定なら従来どおり全開放で、loopback でのみ安全です。完全なマルチユーザー認証
  （OAuth/RBAC）は対象外です（[BE-0015](../../roadmaps/proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)）。
- **CSRF + セキュリティヘッダ（BE-0051）。** トークン設定時、`Origin` が `Host` と一致しない状態変更 POST は
  拒否します（`SameSite=Strict` セッション Cookie に対する多層防御）。`Origin` を送らない非ブラウザ
  クライアントは影響を受けません。全レスポンスに `X-Content-Type-Options: nosniff` /
  `X-Frame-Options: DENY` / `Referrer-Policy: no-referrer` を付与します。
- `--config` は**任意**です。省略すると UI のファイルブラウザ（「Open config」ボタン）から `config.yml` を開けます。
  ブラウザの走査は `--root`（既定: カレントディレクトリ）配下に限定されます。`--scenarios <dir>` は選択アプリの設定済み
  ディレクトリの上書きとして使えます。
- **Git リポジトリから（[BE-0063](../../roadmaps/in-progress/BE-0063-git-config-source/BE-0063-git-config-source-ja.md)）。**
  `--config` は Git ソース（`github:owner/repo@ref:path`）も受け付け、「Open config」ダイアログにも同じ spec を入れる
  **From a Git repository** 欄があります。serve はその ref のリポジトリ部分木をキャッシュへ実体化し、その config を bind し、
  チェックアウトのルートから serve します。これにより config の相対パス（`scenarios` / `appPath` / `build`）は取得したツリーを
  基準に解決されます。これはセルフホストの狙い（[BE-0016](../../roadmaps/proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)
  Tier A）そのもので、ファイルを手で同期する代わりにチームのテストリポジトリを serve に向け、ブランチの切り替えを再デプロイではなく
  UI 上で行えます。ファイルブラウザは `--root` 配下に限定されたままです。チェックアウトは管理された content-addressed キャッシュで、
  Git ソースの run は config のパス項目をチェックアウトのルートに閉じ込めます（[BE-0063](../../roadmaps/in-progress/BE-0063-git-config-source/BE-0063-git-config-source-ja.md)）。
- `--baselines` でビジュアルリグレッションのベースラインディレクトリを指定します（既定: アプリのシナリオ
  ディレクトリ配下の `baselines/`）。UI から起動した実行はこれを使い、レポートの **Approve** ボタンが
  `POST /api/approve` 経由で撮影スクリーンショットをここへ昇格させます。
- app を選ぶ（そのシナリオがドロップダウンに並ぶ）と、backend / udid / erase / `disable alert-dismiss` を設定して **Run** を押します。
  出力がライブ表示され、完了で `report.html` が埋め込まれます。
- **Crawl** タブは、app、シミュレータのプール（Replay と同様の複数選択。2 台以上選ぶと 1 つの画面マップを
  共有して並列にクロールします。詳細は [BE-0064](../../roadmaps/implemented/BE-0064-parallel-crawl/BE-0064-parallel-crawl-ja.md)）、
  予算（max screens / steps）を選び、`POST /api/crawl` で crawl を起動します。返ってきた run id で UI が
  `runs/<id>/screenmap.json` をポーリングし、画面マップを成長に合わせて描きます（画面は幅優先の層に配置し、
  遷移は矢印で表示）。**Stop** ボタンで Replay と同様に中止できます。
- オーサリング（Record と Crawl）の **AI バックエンド**は **Settings → AI プロバイダ** の一箇所で選びます。
  **Anthropic API**（`ANTHROPIC_API_KEY`）、**Amazon Bedrock**（AWS 認証情報 + `BAJUTSU_BEDROCK_MODEL`）、
  **Claude Code**（ローカルの `claude` CLI。サブスクリプションを利用、テキストのみ）の 3 択で、`serve` は
  この選択を `BAJUTSU_AI_PROVIDER` / `BAJUTSU_AGENT` として起動ジョブに渡します。タブごとの Agent 選択は
  ありません。Claude Code のときは、API キー（設定済みなら）はアラートガードにのみ使われます。
- アプリのビルド済みバイナリ（config `appPath`）が無い場合は、先にそのアプリの `build` コマンドを
  実行します（出力は job ログにストリーム）。ビルド失敗時は run を開始せず中止します。`targets.<name>.build`
  に `appPath` を生成するシェルコマンド（例: `make -C demos/features sample-build`）を設定すると、
  手動ビルド無しに UI からオンデマンドでビルドできます。
- 操作 UI の下の **History** リストに過去の run（新しい順、pass/fail ドット、シナリオ要約）が並び、
  クリックでそのレポートを再表示します。`GET /api/runs` が裏側です。
- run サブプロセスは起動環境を継承します（venv の `bin` を `PATH` 先頭に付与し `idb` クライアントを解決）。
  `bajutsu.config.yaml` が解決するようプロジェクトルートから実行してください。
- **`/api/run` の入力検証。** scenario は**選択中アプリの scenarios dir 内**に実在する `*.yaml` でなければ
  なりません（任意のホストパスや `..` トラバーサル不可）。`backend` / `udid` も既知のトークンに限定され、
  自由入力は拒否されます。これにより、リクエストが任意ファイルを実行したり想定外の argv を紛れ込ませたりするのを防ぎます。
  これは `serve` を loopback を越えてホスティングするための前提です
  （[BE-0015 / BE-0016](../../roadmaps/proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)）。
  現状は `127.0.0.1` バインドかつ認証なしなので、信頼できないネットワークにはまだ晒さないでください。
- **`--max-concurrent-runs`（既定 4）** は同時実行できる run/record ジョブ数の上限です。1 呼び出し元が
  希少なデバイスを独占しないようにします（BE-0051）。上限超過の dispatch は **429** を返します。`0` で無制限。
- **ホスティング向けフラグ（応用）。** `--emit-launchagent` は `serve` を単一 Mac 上でトークン認証付きの LaunchAgent として動かす launchd plist を出力します。`--backend server`（と `--asgi`）はホスティング用の FastAPI コントロールプレーンに切り替えます。どちらも [self-hosting](self-hosting.md) で扱います。

## `mcp`

エージェント（Claude Desktop / Code）がシナリオを実行し、run の証跡を読めるように **MCP（Model Context Protocol）サーバ**を起動します。オプションの `bajutsu[mcp]` extra（`fastmcp`）が必要です。

```bash
bajutsu mcp [--config bajutsu.config.yaml] [--runs runs] [--transport stdio]
```

| オプション | 既定 | 説明 |
|---|---|---|
| `--config` | `bajutsu.config.yaml` | ツールがアプリを解決する config |
| `--runs` | `runs` | リソースとして公開する runs ディレクトリ |
| `--transport` | `stdio` | `stdio`（ローカルエージェント）または `sse`（HTTP） |

- **ツール**: `bajutsu_run`（決定的 run）と `bajutsu_doctor`（規約スコア）。どちらも CLI と同じで、合否に AI は入りません。
- **リソース**: 完了した run の `manifest.json` / `report.html` / `junit.xml` と任意の入れ子の artifact（`bajutsu://runs/<id>/…`）、および `runs/latest`。

## `worker`

Redis からキュー済みの run をリースして実行します。ホスティング用サーバ backend の実行側です（[BE-0015](../../roadmaps/proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)、[self-hosting](self-hosting.md)）。オプションの `bajutsu[worker]` extra（`redis` / `rq`）が必要です。ローカル利用には不要です。

```bash
bajutsu worker [--redis-url <url>] [--queue bajutsu]
```

## `lint`

シナリオファイルを **実行せずに** 文法に対して検証します（`run` がロード時に行う厳格な検証と同じ）。正しければ終了コード 0、そうでなければエラーとともに非 0 で終了します。

```bash
bajutsu lint <scenario.yaml>
```

## `schema`

シナリオの **JSON Schema** を stdout に出力します。エディタ連携（補完 / インライン検証）用です。オプションはありません。

```bash
bajutsu schema > bajutsu.schema.json
```

## 環境変数（.env）

`_bootstrap`（`@app.callback`）が全コマンドの前に `.env` を読みます（実装: `bajutsu/dotenv.py`）。

- `KEY=VALUE` 形式。`#` コメント、空行、`export ` 接頭、クォートに対応。
- **既存の環境変数を上書きしません**（実環境の値が常に勝つ）。`.env` はフォールバックです。
- 読み先は既定 `.env`、`BAJUTSU_DOTENV` で変更可能。`.gitignore` 済み。
- 主な用途: `ANTHROPIC_API_KEY`（`record`、`crawl` と、既定で動くアラートガード）。
- AI プロバイダ（BE-0053）: `BAJUTSU_AI_PROVIDER=bedrock` を設定すると、Anthropic API ではなく
  **Amazon Bedrock** 経由で Claude を呼びます。認証は標準の AWS 認証情報チェーン（環境変数 / 共有
  プロファイル / インスタンスまたはロール）で行うため、このパスでは **`ANTHROPIC_API_KEY` は不要**です。
  `BAJUTSU_BEDROCK_MODEL` にはプロバイダ接頭辞付きのモデル id（例 `global.anthropic.claude-opus-4-6-v1`。
  素の Anthropic id は Bedrock では無効）、`AWS_REGION` にはリージョンを設定します。既定は Anthropic
  です。この選択は `record`、`crawl`、`triage`、アラートガードに共通で効きます。
- オーサリングエージェント: `BAJUTSU_AGENT=claude-code` にすると、`record` / `crawl` は API ではなく
  ローカルの `claude` CLI（Claude Code のサブスクリプション）でシナリオを書きます。`--agent` を省略した
  ときの既定値です。`api`（既定）は上記の SDK プロバイダを使います。`serve` の Settings の選択がこの値を
  起動ジョブに書き込みます。アラートガードはエージェントに関わらず常に SDK プロバイダを使います。

```bash
# .env —— Anthropic（既定）
ANTHROPIC_API_KEY=sk-ant-...

# .env —— 代わりに Amazon Bedrock（AWS 認証情報で認証）
BAJUTSU_AI_PROVIDER=bedrock
BAJUTSU_BEDROCK_MODEL=global.anthropic.claude-opus-4-6-v1
AWS_REGION=us-east-1
```

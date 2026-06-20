[English](../cli.md) · **日本語**

# CLI リファレンス

> 実装: `bajutsu/cli.py`（Typer）。エントリポイントは `pyproject.toml` の `bajutsu = "bajutsu.cli:app"`。
> すべてのコマンドは `--app <name>` で 1 アプリを選び、`--config`（既定 `bajutsu.config.yaml`）で
> 設定を指す。アプリ固有差分は config 側にある（[configuration](configuration.md)）。

関連: [run-loop](run-loop.md) ・ [recording](recording.md) ・ [codegen](codegen.md) ・ [configuration](configuration.md)

---

## 共通

- 全コマンドの前に `.env` を読み込む（`_bootstrap`、下記）。
- config が無い / アプリ未定義 / actuator 無し → メッセージを出して **終了コード 2**。
- `--backend` はカンマ区切り（例 `idb`）。空なら config の `backend` を使う。先頭から
  順に可用性を見て **最初に使えるものが actuator**（[drivers](drivers.md#バックエンド選択と-actuator)）。

## `run`

シナリオを **決定的に実行**します。合否は機械判定のみです。唯一の AI コンポーネントは**アラートガード**（シナリオごとに既定 ON）で、ステップをブロックした OS プロンプトを片付けるためだけに動作します。[`dismissAlerts`](scenarios.md#dismissalertsシステムアラートガード) を参照してください。

```bash
bajutsu run --app <name> [--scenario <file.yaml>] [options]
```

既定では、そのアプリの設定済みシナリオディレクトリ（`apps.<name>.scenarios`、[configuration](configuration.md) 参照）内の
**すべての `*.yaml`** を読み込んで実行します。config だけで実行できます。単一ファイルだけ実行するには `--scenario <file>` を渡してください。

| オプション | 既定 | 説明 |
|---|---|---|
| `--app` | （必須） | 対象アプリ（config の `apps.<name>`） |
| `--scenario` | config の `scenarios` ディレクトリ | アプリのシナリオディレクトリ全体ではなく単一の `*.yaml` を実行 |
| `--backend` | config | actuator 順（カンマ区切り。先頭から最初に使えるもの） |
| `--tag` | "" | カンマ区切り。これらの tag のいずれかを持つシナリオのみ実行 |
| `--exclude` | "" | カンマ区切り。これらの tag のいずれかを持つシナリオをスキップ |
| `--udid` | `booted` | 対象 Simulator（カンマ区切り = `--workers` 用のデバイスプール） |
| `--erase / --no-erase` | シナリオ準拠 | 各シナリオの `preconditions.erase`（シム全体を wipe）を上書き。省略時は各シナリオの指定に従う。アプリはどちらでも毎回 fresh に再インストール（config `appPath` + `preconditions.reinstall`） |
| `--dismiss-alerts / --no-dismiss-alerts` | シナリオ準拠（ON） | 各シナリオの `dismissAlerts` を上書きします。idb から見えないシステムアラートを視覚で消すガードです。省略時は各シナリオの指定に従います（設定した AI プロバイダを使用 —— `ANTHROPIC_API_KEY`、Bedrock なら AWS 認証情報・[recording](recording.md#システムアラートの自動対処)） |
| `--alert-instruction` | "" | 既定のボタン指示（シナリオ自身の `dismissAlerts.instruction` が勝つ） |
| `--log-predicate` | "" | `deviceLog` ストリームを絞る NSPredicate（例 subsystem） |
| `--log-subsystem` | "" | `appTrace` 用の os_log subsystem（既定はアプリの `bundleId`） |
| `--network / --no-network` | `--network` | `request` アサーション用にアプリの通信を収集（アプリに BajutsuKit が必要） |
| `--workers` | 1 | デバイスプール上で並列実行。`--udid u1,u2,…` が必要（プール数で上限）。各デバイスが自前のネットワークコレクタ・インターバル録画・デバイス制御を持つので、network / 動画 / `setLocation` / `push` はシングルデバイス実行と同じく機能する |
| `--baselines` | シナリオ隣の `baselines/` | `visual` アサーション用のベースライン画像ディレクトリ。`baseline: home.png` はこの中で解決される |
| `--config` | `bajutsu.config.yaml` | config ファイル |

- 証跡は `FileSink(runs/<runId>, udid=..., log_predicate=...)` に書きます（[evidence](evidence.md#sink証跡の出力先)）。
- `runId` は `YYYYMMDD-HHMMSS`。
- 出力: `PASS|FAIL  runs/<runId>/manifest.json`。**終了コードは全シナリオ成功で 0、失敗で 1**。
- run 内で唯一 AI を使うアラートガードが実際に発火したときは、結果の後に消費トークン量を示す
  `AI usage:` 行を **stderr** に出力します（stdout は機械可読の結果 1 行のままです）。AI を使わな
  かった run では何も出力しません。

```bash
bajutsu run --app sample --udid <UDID> --backend idb --no-erase            # アプリのシナリオディレクトリ全体
bajutsu run --scenario demos/features/app/scenarios/smoke.yaml --app sample --no-erase   # 単一ファイル
```

## `doctor`

**実行可能ゲート** + 現在画面の **規約充足度スコア**（AI 非依存。[configuration](configuration.md#doctor規約充足度スコア)）。

```bash
bajutsu doctor --app <name> [--udid booted] [--backend ...] [--config ...]
```

- まず env ゲート（`preflight.py`）: actuator が必要とする CLI（`xcrun`、idb なら `idb` /
  `idb_companion`）と**起動済みシミュレータ**を ✓/✗ チェックリストで表示します。不足があれば**終了 1**（直し方ヒント付きで即失敗）。
- 次に actuator で `query()` し、`score(elements, idNamespaces)` を表示します。**grade が Blocked で 1、それ以外 0**。

## `trace`

完了した run を**テキストタイムライン**で検査します。シナリオごとに、ステップと観測した通信を
時系列で交互に並べ、続けて expectations・app-trace 区間・証跡サマリを出力します。読み取り専用
（保存済みの `manifest.json` / `network.json` / `appTrace.json` を読む）。

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
  コストを払う前にマッチを絞り込めます。読み取り専用・決定的で、デバイスも LLM も不要です
  （components と data 行は展開しますが、config の `setup` プレリュードは含みません）。シナリオファイルが
  無ければ**終了 2**。

## `triage`

run 内の最初の**失敗**シナリオを診断し、最小の修正案を提示します。**助言のみ**で pass/fail は判定しません
（AI 境界）。失敗コンテキスト（落ちたステップと理由、失敗 expectation、失敗時の要素ツリー、シナリオ）
を組み立て `TriageAgent` に渡します。既定はルールベース（`HeuristicTriageAgent`、API キー不要）で、失敗を
分類（selector / timing / assertion）し、対象 id が画面に無く似た id があれば「もしかして…?」を提案
します（id リネームの自己修復）。`--ai` は同じコンテキストに失敗**スクショ**を加えて推論する Claude 版に差し替えます
（`ANTHROPIC_API_KEY` が必要）。

エージェントは適用可能な**構造化 fix**（`renameId` / `addIndex` 曖昧一致の一意化 / `raiseTimeout`）も
返せます。`--apply <scenario-file>` が **dry-run diff** を表示、`--write` が source に適用、`--rerun --app
<name>` が patched シナリオを再実行（`--no-erase`）して緑になったか報告します。境界は保たれます: fix はユーザーが
diff をレビューして opt-in した時のみ適用され、断片が source に一致しなければ安全に no-op です。

```bash
bajutsu triage [<run-dir>] [--scenario <substr>] [--runs runs] [--ai]
bajutsu triage [<run-dir>] --ai --apply <scenario-file> [--write] \
               [--rerun --app <name> [--backend idb] [--udid <udid>]]
```

- 既定は `runs/` 直下の最新 run。失敗シナリオが無ければ**終了 0**。
- `--rerun` は `--write` と `--app` が必要。
- `--ai` のときは、診断後に消費トークン量を示す `AI usage:` 行を stderr に出力します。既定のルール
  ベースは AI を使わないので何も出力しません。

## `record`

AI でゴールに向けて探索し、**記録したシナリオを書き出します**（Tier 1・[recording](recording.md)）。
既定ではアプリの設定済みシナリオディレクトリ（`apps.<name>.scenarios`）配下に自動命名の `*.yaml` を書きます。
特定パスに書くには `--out` を渡してください。

```bash
bajutsu record --app <name> --goal "<自然言語ゴール>" [--out <file.yaml>] [options]
```

| オプション | 既定 | 説明 |
|---|---|---|
| `--app` | （必須） | 対象アプリ |
| `--goal` | （必須） | 著すゴール（自然言語） |
| `--out` | config の `scenarios` 配下に自動命名 | 出力パスを明示（アプリのシナリオディレクトリを上書き） |
| `--name` | （ゴールから） | 自動命名するファイル名（`--out` 指定時は無視） |
| `--udid` | `booted` | 対象 Simulator |
| `--backend` | config | actuator 順 |
| `--erase / --no-erase` | `--erase` | 起動前に erase（アプリはインストール済みである必要） |
| `--dismiss-alerts` | off | オーサリング中のプロンプトを片付ける（要 API キー） |
| `--alert-instruction` | "" | 同上の押下指示 |
| `--config` | `bajutsu.config.yaml` | config |

- 内部で `launch_driver` → `record_loop(driver, goal, ClaudeAgent(), ...)` → `dump_scenarios` で書き出します。
- 出力: `recorded <N> steps -> <path>`。**要 `ANTHROPIC_API_KEY`**（`ClaudeAgent`）。
- 続けて、オーサリング（およびアラートガード）の AI が消費したトークン量を示す `AI usage:` 行を
  stderr に出力します。`claude-code` エージェントはここで API トークンを消費しないため、何も表示
  されません。

## `crawl`

アプリを**幅優先**で探索し、到達できる画面と画面間の遷移の**画面マップ**を書き出します
（Tier 1・[BE-0038](roadmap/README-ja.md)）。`record` が *目的指向* —— 1 つの自然言語ゴールに
向かって AI が探索し、1 本のシナリオを書き出す —— であるのに対し、`crawl` は *体系的な発見* です。
到達できる画面を巡り、見つけたものを報告します。探索エンジンは**決定的**で（画面の識別子と候補
アクションを試す順序は、どちらも要素ツリーの純粋な関数です）、**AI は関与せず**、**合否ゲートには
決してなりません**。

```bash
bajutsu crawl --app <name> [--max-screens N] [--max-steps N] [--out <dir>] [options]
```

| オプション | 既定 | 説明 |
|---|---|---|
| `--app` | （必須） | 対象アプリ |
| `--max-screens` | `50` | この数の異なる画面を発見したら停止 |
| `--max-steps` | `200` | この数のアクションを実行したら停止 |
| `--agent` | `api` | クロールガイドの AI バックエンド: `api`（Anthropic SDK・従量課金。設定した AI プロバイダを使用 —— Anthropic なら `ANTHROPIC_API_KEY`、`BAJUTSU_AI_PROVIDER=bedrock` なら AWS 認証情報 + `BAJUTSU_BEDROCK_MODEL`）/ `claude-code`（Claude Code CLI。サブスクリプションを利用・テキストのみ。`record --agent claude-code` と同様） |
| `--udid` | `booted` | 対象 Simulator |
| `--backend` | config | actuator 順 |
| `--erase / --no-erase` | `--erase` | 起動前に erase（アプリはインストール済みである必要） |
| `--dismiss-alerts / --no-dismiss-alerts` | `--dismiss-alerts` | クロール中に予期せぬ OS プロンプトを片付ける（クラッシュ誤判定を防ぐ。設定した AI プロバイダを使用 —— `ANTHROPIC_API_KEY`、Bedrock なら AWS 認証情報） |
| `--out` | `runs/<timestamp>` | 画面マップを書き出す run ディレクトリ |
| `--config` | `bajutsu.config.yaml` | config |

- 走査は**決定的リプレイ**で行い、その場での後戻りはしません。既知の画面を再訪するには、アプリを
  クリーンな状態に再起動し、そこへの最短経路を再生してから次の未試行アクションを取ります ——
  `run` が任意の状態へ到達するのと同じやり方です。
- 無効化された操作要素（`notEnabled`）はタップせず、画面ごとに `blocked` として報告します。遷移を洗い出すため、
  crawl は操作要素の状態の**組合せ**を探索します：各空テキスト欄を個別に入力し（スイッチは個別にトグル、タブバーは各タブに切り替え）、
  空欄が複数あるときは**一括入力（compound fill）**も試します。一括入力が重要なのは、操作要素が *複数* の欄が
  揃って初めて有効化される場合があり、かつ途中の単独入力が不可視（マスクされたパスワードは値を公開しない）な
  ことが多く、1つずつ埋めても全充足状態へ到達できないためです。クロールは **AI 駆動**です——
  まず画面を決定的に検査し、その操作を Claude に渡して**組合せ**を
  考えさせ、有効化条件が自明でない操作要素のために**現実的な入力**（正しいメール形式、規約を満たすパスワード、
  フォーム全欄の一括入力など）や id 無し要素への操作を提案し、その推論を run ログに流します。
  AI は**個々のタブをツリーで指定できないタブバー**にも対応します——idb は SwiftUI の TabView を
  「Tab Bar」というラベルの group 1個（タブごとの id 無し）として返すため、バーは見えてもタブをセレクタで
  タップできません。そうしたバーがあり（かつ id で指定できるタブが無い）場合に、システムアラートガードと同じ要領で
  画像認識（vision）から位置を割り出し、各タブを座標でタップします（タブを優先して切り替えてから深掘りする方針は同じ）。
  （UIKit のタブバー——idb が各タブを個別要素として返すもの——は今後の対応予定で、現状は同じ vision 経路にフォールバックします。）
  AI は「何を試すか」を選ぶだけで、画面同一性・遷移・クラッシュ判定は決定的のまま——よって crawl は合否を下さず、
  CI ゲートにもなりません。
- 出力: `<out>/screenmap.json`。`nodes`（画面 —— fingerprint・種別・id・候補アクション・`blocked` 無効操作要素）、
  `edges`（遷移）、`crashes`（アプリ UI を崩壊させたアクション経路）、`alerts`（クロール中にガードが閉じた OS
  プロンプト —— 誘発した経路＋タップしたボタン）、`plan`（探索フロンティア —— 画面ごとの未試行操作。クロールの
  進行に合わせて更新されるので、読み手は次に何を試すか把握できます）、`stop_reason`（`completed` / `max_screens` /
  `max_steps`）からなる JSON グラフです。あわせて `<out>/screens/<fingerprint>.png` —— 発見した各画面の
  スクリーンショット（その画面にいる間に撮影）も出力します。クロールの進行に合わせて書き直されるので、読み手
  （`serve` の **Crawl** タブ）はマップをリアルタイムに描けます。各画面はラベル＋情報のノードで表示され、同じ UI で
  状態だけ違う画面（フォーム未入力と入力済みなど）は 1 つのノードにまとめられ、その場で展開できます。
  `--max-screens` / `--max-steps` のいずれか早い方で停止します。

### 画面の同定方法（fingerprint）

各画面は **fingerprint** —— 再訪と新規画面を区別するための、短く安定した識別子 —— に還元されます。これは
要素ツリーだけから決まる純粋・決定的な関数であり（AI もスクリーンショットのピクセルも使いません）、これが画面
マップの再現性を支えます。同じ画面は常に同じ値にハッシュされます。各ノードは fingerprint とその種別（`kind`）を
記録します。

- **id fingerprint（`kind: "id"` —— 通常時）.** 画面上に存在するアクセシビリティ id の**集合**をソートして
  ハッシュします。表示テキストではなく「どの id が存在するか」を鍵にするので、ロケールやデータが変わっても同定は
  安定します（行の内容が違うリストや、別言語のラベルでも同じ画面）。さらに各 id には、**操作上の状態**が既定から
  外れているときに限ってマーカーを付けます。これにより同じレイアウトでも状態が違えば別のハッシュになり、クロールが
  状態の組み合わせを探索できます。
  - `!` —— **無効**なコントロール（`notEnabled`）、
  - `=` —— **入力済み**のテキスト入力（値が入ったフィールド）、
  - `+` —— **選択 / トグル**されたコントロール（ON のスイッチ、選択中のタブ）。

  有効・空・未選択の画面は素の id だけを寄与するので、そうした状態を持たない画面は素の id 集合とまったく同じ値に
  ハッシュされます。フォームを入力したりスイッチを切り替えたりすると*新しい*ノードになるのはこのためで、これは別個に
  探索できる固有の状態です。前提条件を満たして初めて使えるようになる操作（全フィールド入力後に有効化される送信
  ボタンなど）を、クロールはこの仕組みで発見します。
- **構造 fingerprint（`kind: "structural"` —— フォールバック）.** 画面のアクセシビリティ id が 2 つ未満で同定に
  使うには薄すぎる場合、代わりに**操作可能要素のトレイトを、画面上の大まかな位置でバケット化**してハッシュします。
  これはレイアウトの揺れで変わりうるうえ、コントロール構成が似た無関係な画面と衝突しうるため安定度が低く、識別が
  近似であることを示す `structural` が付きます。アクセシビリティ id のカバレッジを上げる（`doctor` 参照）と、その
  画面は安定した id 方式に戻ります。
- **表示上のグループ化は別の、見た目だけの処理です.** 上記の fingerprint がマップに保存される*状態ごとの*厳密な
  識別子です。**Crawl** タブはこれに加えて、同じ画面で一時的な状態だけが違うノード —— 未入力と入力済みのフォーム、
  トグル、要素を少し足すアラート/オーバーレイ —— を 1 つの展開可能なユニットに*まとめ*、グラフを読みやすくします。
  このグループ化は fingerprint やクロールの探索内容を一切変えず、描画を畳むだけです。

## `codegen`

シナリオから **ネイティブ XCUITest** を生成します（AI 非依存・構造マッピング・[codegen](codegen.md)）。

```bash
bajutsu codegen <scenario.yaml> --app <name> [--emit xcuitest] [-o <out.swift>] [--config ...]
```

| オプション | 既定 | 説明 |
|---|---|---|
| `--emit` | `xcuitest` | 出力形式（現状 `xcuitest` のみ。他は終了コード 2） |
| `-o, --out` | `-` | 出力ファイル。`-` で標準出力 |

- config の `launchEnv` が生成テストの `app.launchEnvironment` に入ります。
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

**オーサリング・実行・探索**のためのローカル Web UI です。Tier 1 の利便機能で、**CI ゲートには含まれません**。
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

- **`--token`（または `$BAJUTSU_SERVE_TOKEN`）— 認証（BE-0051）。** トークン設定時は全リクエストが認証必須です。
  API クライアントは `Authorization: Bearer <token>` を送り、ブラウザは `POST /api/login` で一度だけトークンを
  交換し（401 で UI が入力を促す）、HttpOnly・SameSite のセッション Cookie を受け取ります —— トークンは URL に
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
- `--baselines` でビジュアルリグレッションのベースラインディレクトリを指定します（既定: アプリのシナリオ
  ディレクトリ配下の `baselines/`）。UI から起動した実行はこれを使い、レポートの **Approve** ボタンが
  `POST /api/approve` 経由で撮影スクリーンショットをここへ昇格させます。
- app を選ぶ（そのシナリオがドロップダウンに並ぶ）と、backend / udid / erase / `disable alert-dismiss` を設定して **Run** を押します。
  出力がライブ表示され、完了で `report.html` が埋め込まれます。
- **Crawl** タブは app・AI ガイドを動かす **Agent**（`api` または `claude-code`。`crawl --agent` や
  Record タブと同じ選択肢）・デバイス・予算（max screens / steps）を選び、`POST /api/crawl` で crawl を
  起動します。返ってきた run id で UI が `runs/<id>/screenmap.json` をポーリングし、画面マップを成長に
  合わせて描きます（画面は幅優先の層に配置し、遷移は矢印で表示）。**Stop** ボタンで Replay と同様に中止できます。
- アプリのビルド済みバイナリ（config `appPath`）が無い場合は、先にそのアプリの `build` コマンドを
  実行します（出力は job ログにストリーム）。ビルド失敗時は run を開始せず中止します。`apps.<name>.build`
  に `appPath` を生成するシェルコマンド（例: `make -C demos/features sample-build`）を設定すると、
  手動ビルド無しに UI からオンデマンドでビルドできます。
- 操作 UI の下の **History** リストに過去の run（新しい順・pass/fail ドット・シナリオ要約）が並び、
  クリックでそのレポートを再表示します。`GET /api/runs` が裏側です。
- run サブプロセスは起動環境を継承します（venv の `bin` を `PATH` 先頭に付与し `idb` クライアントを解決）。
  `bajutsu.config.yaml` が解決するようプロジェクトルートから実行してください。
- **`/api/run` の入力検証。** scenario は**選択中アプリの scenarios dir 内**に実在する `*.yaml` でなければ
  なりません（任意のホストパスや `..` トラバーサル不可）。`backend` / `udid` も既知のトークンに限定され、
  自由入力は拒否されます —— リクエストが任意ファイルを実行したり想定外の argv を紛れ込ませることを防ぎます。
  これは `serve` を loopback を越えてホスティングするための前提です
  （[BE-0015 / BE-0016](../../roadmaps/proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)）。
  現状は `127.0.0.1` バインドかつ認証なしなので、信頼できないネットワークにはまだ晒さないでください。
- **`--max-concurrent-runs`（既定 4）** は同時実行できる run/record ジョブ数の上限です。1 呼び出し元が
  希少なデバイスを独占しないようにします（BE-0051）。上限超過の dispatch は **429** を返します。`0` で無制限。

## 環境変数（.env）

`_bootstrap`（`@app.callback`）が全コマンドの前に `.env` を読みます（実装: `bajutsu/dotenv.py`）。

- `KEY=VALUE` 形式。`#` コメント・空行・`export ` 接頭・クォートに対応。
- **既存の環境変数を上書きしません**（実環境の値が常に勝つ）。`.env` はフォールバックです。
- 読み先は既定 `.env`、`BAJUTSU_DOTENV` で変更可能。`.gitignore` 済み。
- 主な用途: `ANTHROPIC_API_KEY`（`record`・`crawl` と、既定で動くアラートガード）。
- AI プロバイダ（BE-0053）: `BAJUTSU_AI_PROVIDER=bedrock` を設定すると、Anthropic API ではなく
  **Amazon Bedrock** 経由で Claude を呼びます。認証は標準の AWS 認証情報チェーン（環境変数 / 共有
  プロファイル / インスタンス・ロール）で行うため、このパスでは **`ANTHROPIC_API_KEY` は不要**です。
  `BAJUTSU_BEDROCK_MODEL` にはプロバイダ接頭辞付きのモデル id（例 `global.anthropic.claude-opus-4-6-v1`。
  素の Anthropic id は Bedrock では無効）、`AWS_REGION` にはリージョンを設定します。既定は Anthropic
  です。この選択は `record`・`crawl`・`triage`・アラートガードに共通で効きます。

```bash
# .env —— Anthropic（既定）
ANTHROPIC_API_KEY=sk-ant-...

# .env —— 代わりに Amazon Bedrock（AWS 認証情報で認証）
BAJUTSU_AI_PROVIDER=bedrock
BAJUTSU_BEDROCK_MODEL=global.anthropic.claude-opus-4-6-v1
AWS_REGION=us-east-1
```

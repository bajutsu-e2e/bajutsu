[English](BE-0073-serve-zip-bundle-upload.md) · **日本語**

# BE-0073 — config・シナリオ・アプリバイナリを zip でまとめてアップロードし Web UI から実行する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0073](BE-0073-serve-zip-bundle-upload-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0073") |
| 実装 PR | [#216](https://github.com/bajutsu-e2e/bajutsu/pull/216) |
| トピック | config の取得元 |
| 関連 | [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`bajutsu.config.yaml`、そのシナリオ木、**ビルド済みアプリバイナリ**（`.app` / `.app.zip` / `.ipa`）を 1 つの `.zip` にまとめ、**`bajutsu serve` の Web UI からアップロードして実行する**ための機能です。serve ホストのファイルシステムに触れないブラウザだけで完結します。serve は zip を隔離された一時ディレクトリへ展開し、ローカルのファイルブラウザで選んだ config と同じ流儀でそれをバインドし、バイナリを run の Simulator にインストールし、展開したツリーに対して既存の決定的な `run` 経路を走らせます。これは config + シナリオ + バイナリの束を**取得する経路を増やすだけ**であり、スキーマ、ランナー、ドライバ、決定的ゲートはいずれも変更せず、LLM をどこにも追加しません。

この提案は、既存の 2 項目とちょうど対になります。完了した run を zip に**エクスポート**する [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md) の**インポート**側の鏡像であり、config とシナリオ木を Git リポジトリから**プル**する [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md) の**プッシュ**側の兄弟です。後者の 2 つは「ホスト型 serve は実行する config とシナリオをどこから得るのか」という同じ問いに、片方は Git で、片方はアップロードで答えます。本提案は [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)（token 認証 + パス封じ込め）として既に出荷済みの serve hardening の上に乗ります。これなしに任意のバイナリをアップロードして実行する機能を公開するのは安全ではありません。

## 動機

チームの config とシナリオはリポジトリに置かれており、`bajutsu run` は*ローカルの*ツリーと*ビルド済みの*アプリ成果物を消費します（[DESIGN §1](../../DESIGN.md)：「Bajutsu はアプリをビルドしない。既存の `xcodebuild` 成果物を受け取る」）。ローカルの Mac ならこれで十分ですが、**ホスト型でリモートの serve** では、手作業での配置でも Git source でも埋めきれない隙間が残ります。

1. **ホスト型 serve のブラウザ利用者はホストのファイルシステムに触れない。** serve がリモートのワーカーや共有 Mac で動くとき（[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)、[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)）、config、シナリオ、`runs/` はすべて*その*マシンにあります。現在の UI の config 選択は **`--root` に封じ込められたファイルブラウザ**（`bajutsu/serve/operations.py` の `_confined_config_path`）なので、運用者が事前にホストへ手で置いたものからしか選べません。ブラウザ利用者は自分のスイートを持ち込めないのです。これは [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md) のモチベーション 3（run を*取り出す*ためのファイルシステムアクセスが無い）のちょうど鏡像で、ここで欠けている方向は**スイートを*入れる***ことです。

2. **Git source はビルド済みバイナリを運べない。** [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md) は ref で指定したリポジトリの部分木を materialize します。これは*テキスト*（config + YAML シナリオ）には理想的ですが、**コンパイル済みアプリ**には向きません。チームは `.app` / `.ipa` 成果物を Git にコミットしませんし、BE-0063 自身も config の `build:` コマンドでホスト上でバイナリを（再）生成する設計に寄っています。これにはそのホストに完全なツールチェーンが要ります。zip は、**ビルド済み**成果物を config やシナリオと一緒に束ねられる唯一の転送手段であり、これはまさに [DESIGN §1](../../DESIGN.md) が「Bajutsu が消費する」と述べているものです。2 つの取得経路は補完的です。バージョン管理されたテキストには Git、ビルド済みバイナリにはアップロードです。

3. **今日はホストへ手で配置するしか道がない。** セルフホストの Tier A ガイド（[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)）では、運用者がチームの config、シナリオ、バイナリを Mac へコピーし、手で同期し続けます。アップロードは「運用者に build を scp してもらい `--root` を編集する」を「ページに zip をドラッグして実行を押す」に変えます。

アーカイブの*インポート*はコードベースにまだ存在しません。[BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md) が**エクスポート**側（stdlib の `zipfile`、1 つの archiver）を提案しており、本提案は同じ stdlib の土台の上に**インポート**側を提案します。両者が揃うと、run の束は双方向に持ち運べる単位になります。

## 詳細設計

### 束は「materialize するツリー」にすぎない（新しいレイアウトを発明しない）

[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md) の核心がそのまま当てはまります。**config だけでは足りません**。`scenarios` / `baselines` / `setup` / `appPath` / `build` は run の作業ディレクトリからの相対パスだからです（`bajutsu/config.py` の `AppConfig`）。したがってアップロードされた zip は、BE-0063 が Git checkout を扱うのとまったく同じく **config が住む自己完結した部分木**として扱い、config の相対エントリは**展開ルートを基準に**解決します。これは BE-0063 が導入する「パスの基準をプロセスの作業ディレクトリではなく明示的な値にする」仕組みの再利用です。

つまり最小の束は、動くローカル checkout がもともと持っている形そのものです。

```
my-suite.zip
├── bajutsu.config.yaml         # appPath: ./build/Sample.app   scenarios: ./scenarios
├── scenarios/
│   └── sample/…                # YAML 木
└── build/
    └── Sample.app/             # appPath が指すビルド済みバイナリ（.app ディレクトリ・.app.zip・.ipa のいずれか）
```

レイアウトに backend 固有の点はありません。今日 `--config <path>` の run が使うのと同じツリー形を、zip として届けるだけです。バイナリを名指すのは config の `appPath` であり、束はそれを*含んで*いればよい。`appPath` がもともとインストールを駆動するので、「バイナリはどこか」という新しい問いは生じません。既存のフィールドが答えています。

### 取得のシーム（BE-0063 の兄弟）

解決は BE-0063 が確立するのと同じシームの裏に置きます。すなわち**ツリーを封じ込めディレクトリに materialize し、展開ルートをパス基準として config を読み込む** config source です。BE-0063 が `GitHubSource`（tarball → 内容アドレス指定キャッシュ）を足すのに対し、本提案は **`UploadBundleSource`** を足します。アップロードされた zip を受け取り、serve の管理下にある隔離された一時ディレクトリへ展開し、同じ `(config, root)` の組を返します。下流のすべて（`resolve()`、ランナー、ドライバ、アサーション評価器、レポート）はローカルや Git の run と同一です。（BE-0063 が先に入れば、その `ConfigSource` Protocol をそのまま再利用します。本提案が先に入れば、同じシームを導入します。両者はそれを共有する設計です。）

### serve の表面

- **エンドポイント。** 認証付きの新しい `POST /api/upload` が、zip を**生のボディ**（`Content-Type: application/zip`、ファイル名は `?name=`）として受け取ります。リクエストは SPA が制御するので、ストリームするボディに multipart パーサは要りません。アップロードを一時ファイルへストリーム（メモリ有界）し、後述の検証を行い、serve 専用の新しいディレクトリへ展開し、**その config をアクティブな config としてバインド**します（`state.config` がバンドルの config を、`state.cwd` がバンドル根を指す）。これは `bind_git_config` が Git チェックアウトをバインドするのとまったく同じです。config のバインドはサーバ全体が配信する config を切り替える操作なので admin ロールの操作とし、BE-0051 の token 認証の裏に置きます。
- **封じ込め。** 展開ディレクトリは serve 専用のサンドボックス（`runs/` の兄弟であり、ブラウズ用 `--root` ではない）なので、アップロードされたツリーが運用者のファイルを上書きすることはありません。各ターゲットのパス項目はバインド時にバンドル内へ封じ込めます（`Effective.rebased`。Git ソースと同じガード）。run の成果物の読み戻しは既存の `ArtifactStore` 境界（`bajutsu/serve/artifacts.py`）のままです。
- **UI。** **Open config** ダイアログに、ファイルブラウザと Git ピッカーに並ぶ 3 つめのソース **Upload a bundle** が加わります。`.zip` をドロップするとそれがアクティブな config になり、**Replay / Record / Crawl** タブはそこから動きます。通常の run と同じジョブ機構（`bajutsu/serve/jobs.py`）を使い、違うのは*ソース*だけです。バインドされたバンドル（cwd は展開ツリー）からの run は `--runs-dir` 経由で serve 自身の runs ストアへ書き出すので **History** に残り、[BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md) のダウンロードが往復を閉じます（スイートをアップロード → 実行 → 結果をダウンロード）。アップロードされた config の `build` コマンドはホスト上で実行しません。バンドルはビルド済みバイナリを同梱します（[DESIGN §1](../../DESIGN.md)）。

### 設計の核心としてのセキュリティ（BE-0051 の上に）

バイナリをアップロードしてホスト上で実行することは、構造上「利用者が供給したコードを実行する」ことそのものです。したがって本項目は [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md) の hardening の**上に**乗ってはじめて安全であり、アップロード固有の防御を足します。ここで対象にするのは**単一 Mac の Tier A serve で今日安全に動く範囲**です。より深いマルチテナント隔離は BE-0015 / BE-0016 に委ねます（後述）。

- **認証は必須。** エンドポイントはほかの serve リクエストと同様に BE-0051 の token 認証の裏に置きます。未認証の serve はアップロードを一切公開してはなりません。serve は token なしの非ループバックバインドを既に拒否するので、アップロードも「token なし ⇒ ループバックのみ」を引き継ぎます。
- **zip-slip / パストラバーサル。** 各エントリは書き込み前に**展開ルートの厳密な配下**へ解決されることを検証します。絶対パス、`..` セグメント、symlink エントリは拒否します。これは serve が config パス（`_confined_config_path`）や baseline で既に強制している「ルートへ封じ込める」不変条件を、アーカイブ展開へ適用したものです。
- **リソース上限（zip-bomb 対策）。** アップロードサイズの上限、エントリ数の上限、展開後の総サイズの上限、エントリごとの圧縮率の上限を設けます。ディスクを埋めきってからではなく、上限を超えた瞬間に展開を中止します。
- **既定でエフェメラル。** 各アップロードは serve 専用の自分のディレクトリへ展開し、**同時にバインドできるバンドルは一つだけ**です。別の config を（どのソースからでも）開くと直前のバンドルのサンドボックスは削除されるので、アップロードされたコードがセッションを越えて残ったりディスクに溜まったりしません。iOS では run はクリーン化／`--erase` した Simulator を使う（[DESIGN §2](../../DESIGN.md)）ので、アップロードされたアプリは今日の run ごとの実行アイソレーションをそのまま得ます。
- **来歴。** アップロードされたファイル名と **zip の sha256** を run の `manifest.json` に記録します。これは BE-0063 が解決済みコミット SHA を記録するのと同じで、「この run は何を実行したのか」を後から必ず答えられるようにし、[DESIGN §2](../../DESIGN.md) の「未知のリビジョンを黙って実行しない」を保ちます。
- **秘匿情報。** 束は config とシナリオを運びますが、**秘匿値は運びません**。`${secrets.*}` は今日どおり serve ホストの環境から解決します（[BE-0032](../BE-0032-secret-variables/BE-0032-secret-variables-ja.md)）。したがってアップロードされたスイートがホストの秘匿情報を持ち込んだり持ち出したりすることはできず、run の成果物マスキングもそのまま適用されます。

### 決定性、ゲート、app 非依存

- **LLM なし、合否に影響なし。** これは決定的な `run` の前段の取得 + 展開であり、合否は依然として機械アサーションだけで決まります。prime directive 1 と 2（[CLAUDE.md](../../CLAUDE.md)）は構造上保たれます。
- **Linux でテスト可能。** 展開、zip-slip 拒否、リソース上限、パス基準の解決は純粋なパッケージング／配線であり、fixture の zip に対して既存の Linux ゲートでユニットテストできます。Simulator は不要です。実際のアプリの*インストール + 実行*だけが Mac を必要とし、これはあらゆる iOS run と同じです。
- **app 非依存。** アプリ固有の差分は束の config（`apps.<name>`）に残ります。ツール、ドライバ、ランナーは束ごとに分岐しません。

### backend のスコープ（まず iOS、web は言及にとどめる）

主対象は **iOS** です。config の `appPath` が名指す `.app` ディレクトリ、zip 化した `.app`、`.ipa`（それ自体が zip）を Simulator へインストールします。束の*レイアウト*は backend 非依存（「config ツリー」にすぎない）なので、機構が iOS を決め打ちすることはありません。**web（Playwright）** backend には「アプリバイナリ」がなく、その対応物は**静的サイト**を束ね、config の `baseUrl` をそれに向けて [BE-0059](../BE-0059-launch-target-server/BE-0059-launch-target-server-ja.md)（`launchServer`）で配信することです。この web 版は**最初のスライスではスコープ外**とし、レイアウトを一般のまま保つためにここで言及しておきます。iOS 経路が入ったあとの後続にできます。

### スコープ外

- **ソースからのアプリビルド。** [DESIGN §1](../../DESIGN.md) は Bajutsu がビルド済み成果物を受け取ると明言しています。束はビルド成果物を運ぶのであって、ビルドはしません。（config の `build:` によるオンデマンドビルドは、ツールチェーンのある*ローカル* / Git の場合のために残ります。）
- **マルチテナントの実行アイソレーション。** テナントごとの Simulator、ジョブごとの egress 制御、org 単位のストレージは [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) / [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) の領域です。本項目は単一 Mac の Tier A serve を対象にします。
- **保持／アップロード束のライブラリ化。** アップロードはエフェメラルです。それを永続化しバージョン管理するのは Git source（[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)）の仕事であり、本項目の仕事ではありません。

## 検討した代替案

- **Git source だけ（[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)）。** 完全な代替としては却下します。Git は*テキスト*をよく運びますが、[DESIGN §1](../../DESIGN.md) が Bajutsu の消費対象とする*ビルド済みバイナリ*は運びません。バイナリを Git に通すには、ビルド成果物をコミットするか、ホストで完全なビルドを走らせるかになり、これこそアップロードが避けるものです。両者は冗長ではなく補完的です。
- **ホストへ手で配置し、既存のファイルブラウザ選択を使う。** ローカル Mac では動きますが、ホスト型 serve の*ブラウザ*利用者には自分のスイートを持ち込む手段を与えません。これはダウンロード方向で [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md) が閉じるのと同じ隙間です。
- **config の YAML だけをアップロードし、ツリーは送らない。** BE-0063 と同じ理由で却下します。config の `scenarios` / `appPath` は相対パスなので、ツリー（とバイナリ）のない config は実行できません。
- **独自マニフェスト付きの専用バンドル形式。** 却下します。動くローカル checkout が*すでに*形式そのものです。zip を「materialize するツリー」として扱えば、config の既存の相対パスと BE-0063 のパス基準機構を再利用でき、何も発明しません。
- **zip ではなく tarball。** 対称性と到達範囲のため却下します。`.ipa` や zip 化した `.app` は既に zip であり、stdlib の `zipfile` は [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md) がエクスポートに使うのと同じ土台で、zip はどの OS でもダブルクリックで開けます。
- **アップロードを再利用ライブラリとして永続化する。** 先送りします。それはバージョン管理されたストレージであり、Git（[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)）が既にそれです。アップロードはエフェメラルのままにします。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

- [CLAUDE.md](../../CLAUDE.md)、[DESIGN §1](../../DESIGN.md)（Bajutsu はビルド済みアプリを受け取り、ビルドはしない）、[DESIGN §2](../../DESIGN.md)（AI は判定者にならない、決定性優先、テストごとにクリーン環境）。
- [BE-0060 — run レポートを zip でダウンロード／エクスポート](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md) — **エクスポート**側の鏡像。共有する stdlib `zipfile` の土台であり、往復の相手。
- [BE-0063 — config（とそのシナリオ木）を Git リポジトリ + ref から読む](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md) — **プル**側の兄弟。本提案が再利用する `ConfigSource` シームと「ツリーを materialize し展開ルートを基準に config を解決する」機構。
- [BE-0051 — ホスティングのための serve hardening](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md) — 本提案が乗る token 認証 + パス封じ込め。`_confined_config_path` の不変条件を展開へ拡張。
- [BE-0015 — Web UI の公開ホスティング](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)、[BE-0016 — Web UI のセルフホスティング](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) — なぜブラウザ利用者にアップロードが要るか。より深いマルチテナント隔離の置き場所。
- [BE-0059 — run のために対象サーバを起動する（`launchServer`）](../BE-0059-launch-target-server/BE-0059-launch-target-server-ja.md) — 後続スライス向けの web backend の対応物（束ねた静的サイトを配信）。
- [BE-0032 — 秘匿変数](../BE-0032-secret-variables/BE-0032-secret-variables-ja.md) — 秘匿情報は束ではなくホスト環境から来る。
- `bajutsu/config.py`（`AppConfig.appPath` / `build` / `bundleId` / `baseUrl`）、`bajutsu/serve/handler.py`（生の zip アップロード経路を得る `do_POST` のボディ処理）、`bajutsu/serve/operations.py`（`bind_config` / `bind_git_config` / `bind_upload_config`、config バインド経路）、`bajutsu/serve/jobs.py`（run ジョブ機構と `ServeState.bind_upload`）、`bajutsu/serve/artifacts.py`（封じ込められた成果物ストア） — 本提案が触れる表面。
- [docs/ja/configuration.md](../../docs/ja/configuration.md)、[docs/ja/cli.md](../../docs/ja/cli.md#serve)。

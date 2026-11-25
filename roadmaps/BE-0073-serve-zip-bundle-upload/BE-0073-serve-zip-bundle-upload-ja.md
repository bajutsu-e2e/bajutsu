[English](BE-0073-serve-zip-bundle-upload.md) · **日本語**

# BE-0073 — config・シナリオ・アプリバイナリを zip でまとめてアップロードし Web UI から実行する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0073](BE-0073-serve-zip-bundle-upload-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0073") |
| 実装 PR | [#216](https://github.com/bajutsu-e2e/bajutsu/pull/216)、[#890](https://github.com/bajutsu-e2e/bajutsu/pull/890) |
| トピック | config の取得元 |
| 関連 | [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本提案は、自己完結したテストの束をブラウザから実行する、1 ステップの手段を加えます。束には、`bajutsu.config.yaml`、そのシナリオ木、**ビルド済みアプリバイナリ**（`.app` / `.app.zip` / `.ipa`）が入り、1 つの `.zip` にまとめます。利用者は **`bajutsu serve` の Web UI から zip をアップロード**して実行します。ブラウザは、serve ホストのファイルシステムに触れる必要がありません。

serve は zip を、隔離された一時ディレクトリへ展開します。ローカルのファイルブラウザで選んだ config と同じ流儀で、展開した config をバインドします。バイナリを run の Simulator にインストールし、展開したツリーに対して既存の決定的な `run` 経路を走らせます。本提案は、config、シナリオ、バイナリの束を**取得する経路を増やすだけ**です。スキーマ、ランナー、ドライバ、決定的ゲートは変更せず、大規模言語モデル（LLM）もどこにも追加しません。

本提案は、既存の 2 項目と対になります。

- 完了した run を zip に**エクスポート**する [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md) の、**インポート**側の鏡像です。
- config とシナリオ木を Git リポジトリから**プル**する [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md) の、**プッシュ**側の兄弟です。どちらも「ホスト型 serve は実行する config とシナリオをどこから得るのか」という同じ問いに答えます。BE-0063 は Git で、本提案はアップロードで答えます。

本提案は、[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)（token 認証とパス封じ込め）として出荷済みの serve hardening の上に乗ります。この hardening がなければ、任意のバイナリをアップロードして実行する機能を公開するのは安全ではありません。

## 動機

チームの config とシナリオはリポジトリに置かれています。`bajutsu run` は、*ローカルの*ツリーと*ビルド済みの*アプリ成果物を消費します（[DESIGN §1](../../DESIGN.md)：「Bajutsu はアプリをビルドしない。既存の `xcodebuild` 成果物を受け取る」）。ローカルの Mac なら、これで十分です。**ホスト型でリモートの serve** では、手作業での配置でも Git source でも埋めきれない隙間が残ります。

1. **ホスト型 serve のブラウザ利用者は、ホストのファイルシステムに触れない。** serve がリモートのワーカーや共有 Mac で動くことがあります（[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)、[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)）。このとき、config、シナリオ、`runs/` はすべて*そのマシン*にあります。現在の UI の config 選択は、**`--root` に封じ込められたファイルブラウザ**です（`bajutsu/serve/operations.py` の `_confined_config_path`）。運用者が事前にホストへ手で置いたものからしか選べないので、ブラウザ利用者は自分のスイートを持ち込めません。これは [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md) の動機 3（run を*取り出す*ためのファイルシステムアクセスがない）のちょうど鏡像です。ここで欠けている方向は、**スイートを*入れる***ことです。

2. **Git source は、ビルド済みバイナリを運べない。** [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md) は、ref で指定したリポジトリの部分木を materialize します。*テキスト*（config と YAML シナリオ）には理想的ですが、**コンパイル済みアプリ**には向きません。チームは `.app` や `.ipa` の成果物を Git にコミットしません。BE-0063 自身の設計も、config の `build:` コマンドでホスト上のバイナリを（再）生成する方向に寄っています。この方向には、そのホストに完全なツールチェーンが要ります。zip は、**ビルド済み**成果物を config やシナリオと一緒に束ねられる唯一の転送手段です。[DESIGN §1](../../DESIGN.md) は、Bajutsu がまさにその成果物を消費すると述べています。2 つの取得経路は補完的です。バージョン管理されたテキストは Git が、ビルド済みバイナリはアップロードが運びます。

3. **今日は、ホストへ手で配置するしか道がない。** セルフホストの Tier A ガイド（[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)）では、運用者がチームの config、シナリオ、バイナリを Mac へコピーし、手で同期し続けます。アップロードは、「運用者に build を scp してもらい `--root` を編集する」を「ページに zip をドラッグして実行を押す」に変えます。

アーカイブの*インポート*は、コードベースにまだ存在しません。[BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md) は**エクスポート**側（stdlib の `zipfile`、1 つの archiver）を提案しています。本提案は、同じ stdlib の土台の上に**インポート**側を加えます。両者が揃うと、run の束は双方向に持ち運べる単位になります。

## 詳細設計

### 束は materialize するツリー

本設計は、新しいレイアウトを発明しません。[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md) の核心がそのまま当てはまります。**config だけでは足りません**。`scenarios`、`baselines`、`setup`、`appPath`、`build` は、run の作業ディレクトリからの相対パスだからです（`bajutsu/config.py` の `AppConfig`）。

そのため、アップロードされた zip は、BE-0063 が Git checkout を扱うのとまったく同じく、**config が住む自己完結した部分木**として扱います。config の相対エントリは、**展開ルートを基準に**解決します。BE-0063 が導入する、パスの基準をプロセスの作業ディレクトリではなく明示的な値にする仕組みを、そのまま再利用します。

最小の束は、動くローカル checkout がもともと持っている形そのものです。

```
my-suite.zip
├── bajutsu.config.yaml         # appPath: ./build/Sample.app   scenarios: ./scenarios
├── scenarios/
│   └── sample/…                # YAML 木
└── build/
    └── Sample.app/             # appPath が指すビルド済みバイナリ（.app ディレクトリ・.app.zip・.ipa のいずれか）
```

レイアウトに backend 固有の点はありません。今日 `--config <path>` の run が使うのと同じ形のツリーを、zip として届けます。バイナリを名指すのは config の `appPath` で、束はそれを*含んで*いる必要があります。`appPath` はもともとインストールを駆動するので、「バイナリはどこか」という新しい問いは生じません。既存のフィールドが答えています。

### 取得のシーム（BE-0063 の兄弟）

解決は、BE-0063 が確立するシームの裏に置きます。config source が**ツリーを封じ込めディレクトリに materialize** し、展開ルートをパス基準として config を読み込みます。BE-0063 は `GitHubSource`（tarball から内容アドレス指定キャッシュへ）を足します。本提案は **`UploadBundleSource`** を足します。アップロードされた zip を受け取り、serve の管理下にある隔離された一時ディレクトリへ展開し、同じ `(config, root)` の組を返します。

下流のすべては、ローカルや Git の run と同一です。`resolve()`、ランナー、ドライバ、アサーション評価器、レポートが該当します。BE-0063 が先に入れば、本提案はその `ConfigSource` Protocol をそのまま再利用します。本提案が先に入れば、同じシームを導入します。両者はシームを共有する設計です。

### serve の表面

- **エンドポイント。** 認証付きの新しい `POST /api/upload` が、zip を**生のボディ**（`Content-Type: application/zip`、ファイル名は `?name=`）として受け取ります。リクエストはシングルページアプリ（SPA）が制御するので、ストリームするボディに multipart パーサは要りません。エンドポイントは、アップロードをメモリ有界で一時ファイルへストリームし、後述の検証を行います。続いて serve 専用の新しいディレクトリへ展開し、**展開した config をアクティブな config としてバインド**します。`state.config` がバンドルの config を、`state.cwd` がバンドルのルートを指します。`bind_git_config` が Git チェックアウトをバインドするのと、まったく同じ方法です。config のバインドはサーバ全体が配信する config を切り替える操作なので、admin ロールの操作とし、BE-0051 の token 認証の裏に置きます。
- **封じ込め。** 展開ディレクトリは serve 専用のサンドボックスです。`runs/` の兄弟であり、ブラウズ用の `--root` ではありません。そのため、アップロードされたツリーが運用者のファイルを上書きすることはありません。バインド時に、各ターゲットのパス項目をバンドル内へ封じ込めます（`Effective.rebased`。Git ソースと同じガードです）。run の成果物の読み戻しは、既存の `ArtifactStore` 境界（`bajutsu/serve/artifacts.py`）のままです。
- **UI。** **Open config** ダイアログに、ファイルブラウザと Git ピッカーに並ぶ 3 つめのソース **Upload a bundle** が加わります。利用者が `.zip` をドロップすると、それがアクティブな config になり、**Replay / Record / Crawl** タブはそこから動きます。run は通常の run と同じジョブ機構（`bajutsu/serve/jobs.py`）を使い、違うのは*ソース*だけです。バインドされたバンドル（cwd は展開ツリー）からの run は、`--runs-dir` 経由で serve 自身の runs ストアへ書き出すので、**History** に残ります。[BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md) のダウンロードが往復を閉じます（スイートをアップロードし、実行し、結果をダウンロードする）。アップロードされた config の `build` コマンドは、ホスト上で実行しません。バンドルはビルド済みバイナリを同梱するからです（[DESIGN §1](../../DESIGN.md)）。

### BE-0051 の上に積むセキュリティ

バイナリをアップロードしてホスト上で実行することは、構造上「利用者が供給したコードを実行する」ことそのものです。そのため、本項目は [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md) の hardening の**上に**乗ってはじめて安全になり、アップロード固有の防御を足します。対象は、**単一 Mac の Tier A serve で今日安全に動く範囲**です。より深いマルチテナント隔離は、BE-0015 と BE-0016 に委ねます（後述）。

- **認証は必須。** エンドポイントは、ほかの serve リクエストと同様に、BE-0051 の token 認証の裏に置きます。未認証の serve は、アップロードを一切公開してはなりません。serve は token なしの非ループバックバインドをすでに拒否するので、アップロードも「token なし ⇒ ループバックのみ」を引き継ぎます。
- **zip-slip とパストラバーサル。** serve は、書き込み前に、各エントリが**展開ルートの厳密な配下**へ解決されることを検証します。絶対パス、`..` セグメント、symlink エントリは拒否します。serve は config パス（`_confined_config_path`）と baseline に、「ルートへ封じ込める」不変条件をすでに強制しています。同じ不変条件を、アーカイブ展開へ適用します。
- **リソース上限（zip-bomb 対策）。** serve は 4 つの上限を強制します。アップロードサイズ、エントリ数、展開後の総サイズ、エントリごとの圧縮率です。ディスクを埋めきってからではなく、上限を超えた瞬間に展開を中止します。
- **既定でエフェメラル。** 各アップロードは serve 専用の自分のディレクトリへ展開し、**同時にバインドできるバンドルは 1 つだけ**です。別の config を（どのソースからでも）バインドすると、直前のバンドルのサンドボックスは削除されます。アップロードされたコードがセッションを越えて残ることも、ディスクに溜まることもありません。iOS の run は、クリーン化した（`--erase` の）Simulator を使います（[DESIGN §2](../../DESIGN.md)）。アップロードされたアプリは、今日の run ごとの実行アイソレーションをそのまま得ます。
- **来歴。** serve は、アップロードされたファイル名と **zip の sha256** を run の `manifest.json` に記録します。BE-0063 が解決済みコミット SHA を記録するのと同じ方法です。「この run は何を実行したのか」に後から必ず答えられるので、[DESIGN §2](../../DESIGN.md) の「未知のリビジョンを黙って実行しない」を保てます。
- **秘匿情報。** 束は config とシナリオを運びますが、**秘匿値は運びません**。`${secrets.*}` は今日どおり serve ホストの環境から解決します（[BE-0032](../BE-0032-secret-variables/BE-0032-secret-variables-ja.md)）。アップロードされたスイートが、ホストの秘匿情報を持ち込んだり持ち出したりすることはできません。run の成果物マスキングも、そのまま適用されます。

### 決定性、ゲート、app 非依存

- **LLM なし、合否に影響なし。** 変更は、決定的な `run` の前段にある取得と展開です。合否は、依然として機械アサーションだけで決まります。prime directive 1 と 2（[CLAUDE.md](../../CLAUDE.md)）は構造上保たれます。
- **Linux でテスト可能。** 展開、zip-slip 拒否、リソース上限、パス基準の解決は、純粋なパッケージングと配線です。fixture の zip に対して、既存の Linux ゲートでユニットテストでき、Simulator は不要です。実際のアプリの*インストールと実行*だけが Mac を必要とします。これは、あらゆる iOS run と同じです。
- **app 非依存。** アプリ固有の差分は、束の config（`apps.<name>`）に残ります。ツール、ドライバ、ランナーは、束ごとに分岐しません。

### backend のスコープ（まず iOS、web は言及にとどめる）

主対象は **iOS** です。`.app` ディレクトリ、zip 化した `.app`、`.ipa`（それ自体が zip）のいずれかを、config の `appPath` が名指し、Simulator がインストールします。束の*レイアウト*は backend 非依存です。「config ツリー」にすぎないからです。そのため、機構が iOS を決め打ちすることはありません。

**web（Playwright）** backend には、「アプリバイナリ」がありません。その対応物は、**静的サイト**を束ね、config の `baseUrl` をそれに向けて [BE-0059](../BE-0059-launch-target-server/BE-0059-launch-target-server-ja.md)（`launchServer`）で配信することです。この web 版は**最初のスライスではスコープ外**です。レイアウトを一般のまま保つためにここで言及しておき、iOS 経路が入ったあとに続けられます。

### スコープ外

- **ソースからのアプリビルド。** [DESIGN §1](../../DESIGN.md) は、Bajutsu がビルド済み成果物を受け取ると明言しています。束はビルド成果物を運ぶだけで、ビルドはしません。config の `build:` によるオンデマンドビルドは、ツールチェーンのある*ローカル*と Git の場合のために残ります。
- **マルチテナントの実行アイソレーション。** テナントごとの Simulator、ジョブごとの egress 制御、org 単位のストレージは、[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) と [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md) の領域です。本項目は単一 Mac の Tier A serve を対象にします。
- **保持とアップロード束のライブラリ化。** アップロードはエフェメラルです。永続化とバージョン管理は、Git source（[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)）の仕事であり、本項目の仕事ではありません。

## 検討した代替案

- **Git source だけ（[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)）。** 完全な代替としては却下します。Git は*テキスト*をよく運びますが、[DESIGN §1](../../DESIGN.md) が Bajutsu の消費対象とする*ビルド済みバイナリ*は運びません。バイナリを Git に通すには、ビルド成果物をコミットするか、ホストで完全なビルドを走らせるかのどちらかです。アップロードは、そのどちらも避けられます。両者は冗長ではなく、補完的です。
- **ホストへ手で配置し、既存のファイルブラウザ選択を使う。** ローカル Mac では動きます。しかし、ホスト型 serve の*ブラウザ*利用者には、自分のスイートを持ち込む手段を与えません。これは、ダウンロード方向で [BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md) が閉じるのと同じ隙間です。
- **config の YAML だけをアップロードし、ツリーは送らない。** BE-0063 で挙げた理由と同じ理由で却下します。config の `scenarios` と `appPath` は相対パスなので、ツリーとバイナリのない config は実行できません。
- **独自マニフェスト付きの専用バンドル形式。** 却下します。動くローカル checkout が、*すでに*形式そのものです。zip を「materialize するツリー」として扱えば、config の既存の相対パスと BE-0063 のパス基準機構を再利用でき、何も発明せずに済みます。
- **zip ではなく tarball。** 対称性と到達範囲のため、却下します。`.ipa` や zip 化した `.app` は、すでに zip です。stdlib の `zipfile` は、[BE-0060](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md) がエクスポートに使うのと同じ土台です。zip は、どの OS でもダブルクリックで開けます。
- **アップロードを再利用ライブラリとして永続化する。** 先送りします。それはバージョン管理されたストレージであり、Git（[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)）がすでにその役割を担います。アップロードはエフェメラルのままにします。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

- [CLAUDE.md](../../CLAUDE.md)、[DESIGN §1](../../DESIGN.md)（Bajutsu はビルド済みアプリを受け取り、ビルドはしない）、[DESIGN §2](../../DESIGN.md)（AI は判定者にならない、決定性優先、テストごとにクリーン環境）。
- [BE-0060 — run レポートを zip でダウンロード／エクスポート](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export-ja.md)：**エクスポート**側の鏡像です。共有する stdlib `zipfile` の土台であり、往復の相手です。
- [BE-0063 — config（とそのシナリオ木）を Git リポジトリ + ref から読む](../BE-0063-git-config-source/BE-0063-git-config-source-ja.md)：**プル**側の兄弟です。本提案が再利用する `ConfigSource` シームと、「ツリーを materialize し展開ルートを基準に config を解決する」機構です。
- [BE-0051 — ホスティングのための serve hardening](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting-ja.md)：本提案が乗る token 認証とパス封じ込めです。`_confined_config_path` の不変条件を展開へ拡張します。
- [BE-0015 — Web UI の公開ホスティング](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)、[BE-0016 — Web UI のセルフホスティング](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)：なぜブラウザ利用者にアップロードが要るか、より深いマルチテナント隔離の置き場所です。
- [BE-0059 — run のために対象サーバを起動する（`launchServer`）](../BE-0059-launch-target-server/BE-0059-launch-target-server-ja.md)：後続スライス向けの web backend の対応物です（束ねた静的サイトを配信します）。
- [BE-0032 — 秘匿変数](../BE-0032-secret-variables/BE-0032-secret-variables-ja.md)：秘匿情報は束ではなくホスト環境から来ます。
- `bajutsu/config.py`（`AppConfig.appPath` / `build` / `bundleId` / `baseUrl`）、`bajutsu/serve/handler.py`（生の zip アップロード経路を得る `do_POST` のボディ処理）、`bajutsu/serve/operations.py`（`bind_config` / `bind_git_config` / `bind_upload_config`、config バインド経路）、`bajutsu/serve/jobs.py`（run ジョブ機構と `ServeState.bind_upload`）、`bajutsu/serve/artifacts.py`（封じ込められた成果物ストア）：本提案が触れる表面です。
- [docs/ja/configuration.md](../../docs/ja/configuration.md)、[docs/ja/cli.md](../../docs/ja/cli.md#serve)。

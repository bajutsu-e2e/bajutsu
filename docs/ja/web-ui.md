[English](../web-ui.md) · **日本語**

# Web UI（`serve` のブラウザアプリ）

> `bajutsu serve` で開くブラウザ UI について、各タブが何をするか、どう操作するかをタスク指向で
> 説明します。起動フラグ、認証、ホスティングの詳細はこのページでは扱わず、
> [CLI リファレンス](cli.md#serve) と [self-hosting](self-hosting.md) に委ねます。ここで扱うのは
> 画面であって、コマンドラインではありません。
>
> 実装: `bajutsu/serve/`（stdlib のサーバ）。マークアップは `bajutsu/templates/serve.html.j2` にあります。

関連: [CLI リファレンス](cli.md#serve) ・ [scenarios](scenarios.md) ・ [recording](recording.md) ・ [reporting](reporting.md) ・ [selectors](selectors.md) ・ [configuration](configuration.md) ・ [self-hosting](self-hosting.md)

---

## Web UI とは何か、いつ使うか

Web UI は **Tier 1 の利便性のための機能**です。手で実行する CLI コマンドを、そのままブラウザから使える
フロントエンドにしたものです。**CI（継続的インテグレーション）ゲートには含まれません**。決定的な `run` の判定が
これに依存することはなく、ここで合否を下すものは何もありません。自然言語のゴールからシナリオを
**オーサリング**する、シナリオを**実行**してレポートを読む、アプリを**探索**して画面マップが描かれていく
様子を見る、実際のスクリーンショットに対してシナリオを**編集**する、run 履歴を**俯瞰**する。これらを
1 つのアクティブな config に対して、ターミナルのコマンドを切り替えることなく行えます。

UI の操作の多くは CLI コマンド（`record`、`run`、`crawl`、`stats`、`lint`）に対応します。例外は
**Author** タブで、その Capture / Edit / Enrich はシナリオファイルの作成と編集を助ける serve 専用の
機能で、対応する CLI コマンドはありません。ここでの説明でオプションの全体像が必要になったら、
[CLI リファレンス](cli.md) が拠り所です。

### 起動する

リポジトリの checkout では、`make serve` でサーバを起動してください。これが一番手数の少ない方法です。
`make serve`（[`scripts/serve.sh`](https://github.com/bajutsu-e2e/bajutsu/blob/main/scripts/serve.sh)）は、
iOS backend のオンデマンド依存（idb クライアントと `idb_companion`）を用意してからサーバを起動します。
これらが無いと、iOS の実行は `no available actuator` で失敗します。フラグは `ARGS` で渡します。

```bash
make serve                                                        # 既定ポート（8765）
make serve ARGS="--config demos/showcase/showcase.config.yaml --port 8766"   # ショーケースアプリ
```

リポジトリにはルートの `bajutsu.config.yaml` が無いため、ショーケースアプリにはこの config 指定が
必要です。

`make serve` は内部で `python -m bajutsu serve`、すなわち [CLI リファレンス](cli.md#serve) が説明する
`bajutsu serve` コマンドを実行します。checkout の外で Bajutsu を導入した場合（`make` が無い場合）は、
`bajutsu serve`（または `python -m bajutsu serve`）を直接実行してください。その場合、backend の依存は
自分で用意します。iOS ターゲットには idb クライアントと `idb_companion` が要りますが、web（Playwright）
ターゲットには不要です。いずれの方法でも、サーバがブラウザを自動で開くことはありません。`127.0.0.1` に
bind するので、起動したら表示された URL（既定は `http://127.0.0.1:8765`、`--port` を渡した場合はその
ポート）を自分で開いてください。オプションの全体像（`--port`、`--config`、`--root`、`--runs`、
`--baselines`、`--host`、`--token`、`--max-concurrent-runs`、`--evidence-store`）は
[CLI リファレンス](cli.md#serve) にあります。

## 画面の全体像

ヘッダには 6 つのトップレベルタブがあります。**Record**、**Replay**、**Crawl**、**Author**、
**Stats**、**Coverage** です。その右に **Open config**（config を bind すると隣にその名前が出ます）、**Settings**、
そして既定でシステム設定に追従するダークとライトのテーマ切替があります。各タブはそれぞれ独立した
1 画面で、タブを切り替えても別のタブの作業内容は失われません。

一部のフォームは backend によって変わります。iOS の Simulator（idb）では **Device** のピッカー、
**Simulators** の複数選択、**erase device first** が現れ、web ターゲット（Playwright / Chromium）では
それらの代わりに **show browser (headed)** が現れます。それ以外は backend をまたいで共通です。
**Replay** タブには過去の run を並べる **History** の一覧もあります（後述）。

## アクティブな config を選ぶ

すべてのタブは 1 つの**アクティブな config** に対して動きます。**Open config** を押して bind します。
ダイアログには 3 つのソースがあります。

- **From a Git repository。** `github:owner/repo@ref:path/to/bajutsu.config.yaml` の形で spec を
  入力し、**Load** を押します。`ref` はブランチ、タグ、またはコミット SHA です（既定はリポジトリの
  デフォルトブランチ）。部分木ごと取得するので、config のシナリオやビルド済みアプリも一緒に来ます。
- **Upload a bundle (.zip)。** **Choose a .zip…** を押す（またはボックスにファイルをドロップする）と、
  ホストのファイルシステムに触れないまま、自分のスイートをホスト型のサーバへ持ち込めます。zip の中身は
  動くローカル checkout そのもの、つまり `bajutsu.config.yaml`、その `scenarios` ツリー、config の
  `appPath` が指すビルド済みアプリバイナリです。秘匿情報は運びません。`${secrets.*}` はサーバの環境から
  解決されます。
- **or browse the server。** サーバの `--root` 配下に限定されたファイルブラウザで、ローカルの
  `.yml`/`.yaml` の config を選びます。リモートの利用者はホストのファイルシステムと関係を持たないため、
  ホスト型のデプロイではこのソースは隠れます。

各ソースの詳しい挙動（content-addressed な Git キャッシュ、バンドルのサンドボックス化と zip-slip 対策、
`--root` への封じ込め）は [CLI リファレンス](cli.md#serve) に書いてあります。bind できる config は同時に
1 つだけで、別の config を開くと置き換わります。

## Settings

**Settings** には、AI を使うパスが必要とする 2 つの選択があります。ここの内容はディスクには書かれません。
設定はそのセッションのあいだサーバのメモリ上に生き、`serve` を再起動するとリセットされます。再起動を
またいで保持したい値は、`serve` を起動する前にシェルか `.env` で `ANTHROPIC_API_KEY` / `AWS_*` を
設定してください。

**AI provider** は、オーサリング（Record と Crawl）が使う backend を選びます。

- **Anthropic API**：従量課金の API です。下で設定する Claude API キーで認証します。
- **Amazon Bedrock**：AWS 経由の Claude です。標準の AWS 認証情報で認証し、**AWS region** と
  **Bedrock model id** の欄が加わります（Bedrock の id はプロバイダ接頭辞を持ちます。例
  `global.anthropic.…`。素の Anthropic id とは異なります）。
- **Claude Code**：ローカルの `claude` CLI で、Pro/Max のサブスクリプションを使います（テキストのみの
  オーサリング）。`serve` を起動した環境にインストールし、認証（`claude setup-token`）しておく必要が
  あります。

**Claude API key** は **write-once** です。キーを入力して **Save** すると、以後はマスクした表示だけになり、
内容が再表示されることはありません。変更するには新しいキーを設定します。**Clear** で削除します。これは
Anthropic API プロバイダとアラートガードで使われます（Bedrock は代わりに AWS 認証情報を使います）。
必要とするのは AI を使うパスだけです。**Record**、**Crawl**、そしてアラートガードを有効にした Replay です。

## Record — ゴールからシナリオをオーサリングする

**何をする画面か。** 自然言語のゴールに向けて AI で探索し、その結果のシナリオを 1 手ずつ書き出します。
`record` コマンドをブラウザにしたものです。

**操作手順。**

1. 目的を **Goal (natural language)** に書きます（例「カウンタを 2 回増やして 2 と表示されるか確認する」）。
2. **Target** を選びます。iOS では **Device** も選びます（隣の更新ボタンでシミュレータを再取得できます）。
   web では **show browser (headed)** にチェックを入れると、既定のヘッドレスではなく、目に見える
   Chromium のウィンドウがオーサリングを低速再生する様子を見られます。
3. 必要なら **Save as** にシナリオのファイル名を入れます。空なら `generated.yaml` が既定になり、同名が
   すでにあれば run の日時が付くので上書きされません。
4. iOS のオプション：**erase device first** はシミュレータを初期化してアプリをオンボーディングから
   始めます。**disable alert-dismiss** はこのオーサリング実行で vision のアラートガードを止めます。
5. **Generate scenario** を押します（**Stop** で中止）。エージェントの 1 手ずつの進捗がログに流れます。

**結果どうなるか。** オーサリングが終わると、**Generated scenario** パネルに YAML が出ます。必要なら
その場で編集し、**Save**（保存するものが無いあいだは無効）を押します。シナリオはターゲットの
scenarios ディレクトリに書き込まれるので、**Replay** タブの **Scenario** ピッカーに現れ、そのまま
実行できます。オーサリングループとアラートガードの仕組みは [recording](recording.md) を参照してください。

## Replay — シナリオを実行してレポートを読む

**何をする画面か。** シナリオを決定的に実行し、そのレポートを埋め込みます。`run` コマンドをブラウザに
したものです。サブタブは **Run** と **History** の 2 つです。

**Run。**

1. **Scenario** と **Target** を選びます。
2. iOS では、実行する **Simulators** を選びます（停止中のものは先に起動されます。2 台以上選ぶと、
   シナリオをそれらのあいだで並列に実行し、**Workers** がその台数に追従します）。**erase device first** と
   **disable alert-dismiss** は Record と同じです。web では **show browser (headed)** にチェックを入れると、
   目に見える Chromium のウィンドウで実行を見られます。
3. **Run** を押します（**Stop** で中止）。出力がログにライブ表示され、完了するとその run の
   `report.html` が隣に埋め込まれます。

埋め込まれたレポートの中で、visual チェックの **Approve** ボタンは、その run が撮影した
スクリーンショットを visual ベースラインへ昇格させます（`approve` CLI コマンドと同じ昇格です）。以後の
run はそれと比較されます。これは run → 確認 → approve → 再 run というループの確認のステップです。
レポートの各セクションが何を示すかは [reporting](reporting.md) を参照してください。

**History。** このサブタブは過去の run を新しい順に並べ、それぞれに pass/fail のドットとシナリオの
要約が付きます。クリックするとそのレポートを再表示します。更新ボタンで再取得します。

## Crawl — アプリを探索して画面マップをその場で描く

**何をする画面か。** アプリを幅優先で探索し、到達できる画面と画面間の遷移を、発見に合わせて描きます。
`crawl` コマンドをブラウザにしたものです。これは**発見のためのツールで、合否ゲートには決してなりません**。
探索エンジン（画面の同一性、遷移、クラッシュ判定）は決定的で、**Settings** で選んだ AI プロバイダは
「何を試すか」を提案するだけです。

**操作手順。**

1. **Target** を選び、**Workers**（並列のブラウザプロセス数。iOS では 1 つの画面マップを共有する
   シミュレータ数）を設定します。iOS では、クロールする **Simulators** も選びます。
2. 予算を設定します。**Max screens**（既定 50）と **Max steps**（既定 200）です。クロールは先に達した方で
   停止します。
3. iOS には **erase device first** と **disable alert-dismiss**、web には **show browser (headed)** が
   あります。
4. **Start crawl** を押します（**Stop** で中止）。進捗はステータス行と **Console** に出ます。

**得られるもの**（フォームの隣の 3 つのビュー）。

- **Screen map**：発見した画面と遷移のグラフで、クロールの進行に合わせてライブで描かれます。**−** /
  リセット / **+** で拡大縮小します。同じ UI で一時的な状態だけが違う画面（未入力と入力済みのフォームなど）は
  1 つのノードにまとまり、その場で展開できます。
- **Exploration plan**：画面ごとの未試行操作を並べた計画ツリーで、進捗バーが付きます。**show pruned** は、
  クロールが重複するグローバルコントロール（一度だけ探索したタブやナビゲーションバー）として刈り込んだ
  操作の表示を切り替えます。それらは取り消し線付きで表示され、クリックすると探索を再開できます。
- **Console**：クロールの進捗ログです。

マップの画面をクリックすると**スクリーンショットのライトボックス**が開きます。拡大された画面に **‹** /
**›** が付き、その画面への遷移や、その画面からの遷移へ移動できます。ホットスポットは、各遷移がどこで
起きたかを示します。

画面マップ、画面を同定する fingerprint、web と iOS の違いは [CLI リファレンス](cli.md#crawl) で扱います。

## Author — 1 つのシナリオを capture・edit・enrich する

**何をする画面か。** 1 つの開いているシナリオを、**Capture** / **Edit** / **Enrich** のボタンで選ぶ
3 つのモードで扱います。モードを切り替えても、シナリオが読み直されたり未保存の YAML 編集が失われたりは
しません。

**Capture** — 実際のスクリーンショットをクリックしてシナリオを組み立てます。

1. **Target** を選び、**Start capture** を押してライブセッションを始めます。
2. **Action mode** を **tap** か **type** にし（**type** のときは **Text to type** を埋めます）、
   スクリーンショットをクリックして各操作を記録します。
3. **Finish & save** を押して、記録したフローを書き出します。

**Edit** — run が撮影したスクリーンショットに対してステップを直します。

1. **Scenario** と **Run** を選んで **Load** を押します。
2. **← Prev** / **Next →** でステップを移動します。ラベルにどのステップを読み込んでいるかが出ます。
3. スクリーンショットをクリックしてステップの対象を付け替え、**Apply** でその変更を YAML に反映します。

**Enrich** — 提案されたアサーションを加えます。

1. シナリオを読み込み、**Enrich** を押します。
2. **Proposed assertions** パネルに提案が並びます。**Accept** で YAML に取り込み、**Dismiss** で
   破棄します。

**YAML エディタ**（3 モード共通）は入力しながら検証します。デバウンスしたチェックが `bajutsu lint` と
同じルールを実行し、行に紐づく診断を、該当行のガターのマーカーと、クリックできる問題リストとして
表示します。問題をクリックするとその行へ移動します。シナリオの JSON Schema は、軽量なキー補完
（Ctrl/⌘+Space）と hover の説明表示にも使われます。これは決定的で、AI を使いません。**Save** で
シナリオを書き出します。これらのチェックが従う文法は [scenarios](scenarios.md)、セレクタの採点方法は
[selectors](selectors.md) を参照してください。

**決定性の監査バッジ**は、lint と同じライブの YAML を安定性の観点で採点します。グレードのバッジ
（**Stable** / **Moderate** / **Fragile** と、`id` ベースのセレクタの割合）がエディタのヘッダに表示され、
エディタの下の findings 一覧が決定性リスクを一つずつ挙げます。安定性ラダーより下のセレクタ、具体的な条件を
持たない `wait`、生座標のジェスチャです。これは [`audit`](cli.md) コマンドの静的スコアをブラウザに出した
もので、読み取り専用でデバイスも AI も使わず、あくまで参考情報でありゲートにはなりません。**Replay** タブ
でも、選択中のシナリオに対して同じバッジを表示します。シナリオを書く場所でも実行する場所でも、安定性の
シグナルが見えるようにするためです。

**Generate code** — 読み込んだシナリオを、ブラウザを離れずにネイティブテストとして書き出します。
シナリオを読み込んで **Generate code** を押すと、結果が読み取り専用のビューアに開き、**Copy** で
コピー、**Download** でダウンロードできます（ファイル名はシナリオと出力先から導きます。例:
`LoginUITests.swift` / `login.spec.ts`）。出力先はターゲットの backend に従い、iOS なら **XCUITest**、
web なら **Playwright** になるので、そのターゲットが対応する形式だけを提示します。これは
[`codegen`](codegen.md) コマンドを UI に出したもので、シナリオを出力先フレームワークの流儀へ構造的に
対応づけるだけです。デバイスも AI も動かさず、合否も計算しません。未対応の限界は `codegen` 自身のものです。

## Stats — run 履歴のダッシュボード

**何をする画面か。** サーバの run 履歴を横断した集計 run 統計ダッシュボードを描きます。`stats` コマンドを
ブラウザにしたものです。これは**読み取り専用かつ助言的で、判定ではありません**。合格率の推移、遅い
シナリオと flaky なシナリオ、失敗ホットスポット、run のボリュームを、各 run の保存済み `manifest.json` から
集計します。

**操作手順。** タブを開くとダッシュボードが読み込まれます。更新ボタンで、現在の run 履歴に対して
再集計します。デバイスも AI も run も関わりません。

## Coverage — E2E カバレッジマップ

**何をする画面か。** ターゲットの E2E カバレッジマップを描きます。`coverage` コマンドをブラウザにした
ものです。シナリオスイートが参照する安定 id を、アプリが宣言した `idNamespaces` と突き合わせ、名前空間
ごとのカバレッジ、不足の一覧（どのシナリオも触れていない宣言済み名前空間）、名前空間から外れた id を
示します。過去の run を選ぶと、run の証跡に基づく次元が加わります。観測されたエンドポイントとアサート
されたエンドポイントの比（選んだ run の `network.json` の和集合を、スイートのネットワークアサーションと
突き合わせたもの）と、観測された id と宣言済み名前空間の比（各 run の `elements.json` から）です。Stats と
同じく**読み取り専用で助言的であり、判定にもゲートにもなりません**。どの数字も決定的な数え上げで、モデルは
使いません。

**操作手順。** ターゲットを選び、run の証跡に基づく次元を加えたいときは run を選んでから、**Compute** を
押すとマップが描かれます。デバイスも AI も run も関わりません。

## セキュリティとホスティング

既定の `make serve` は `127.0.0.1` に無認証で bind します。loopback インターフェースでのみ安全です。
自分のマシンの外に公開するにはトークン（`--token`）が要ります。トークンは認証と CSRF 対策を有効にし、
非 loopback の `--host` への bind にはトークンが必須です。単一 Mac でのチーム向けホスティング
（`--emit-launchagent`）や本格的なコントロールプレーンの backend（`--backend server`）、そして
`--max-concurrent-runs` と `--evidence-store` は、[CLI リファレンス](cli.md#serve) と
[self-hosting](self-hosting.md) で扱います。

---

各タブのスクリーンショットは今後追加する予定です。現時点では本文だけの案内です。

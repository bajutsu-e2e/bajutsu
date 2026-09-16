[English](README.md) · **日本語**

<p align="center">
  <img src="assets/icons/logo.png" alt="Bajutsu - logo" width="300" height="300">
</p>

# Bajutsu

[![CI](https://github.com/bajutsu-e2e/bajutsu/actions/workflows/ci.yml/badge.svg)](https://github.com/bajutsu-e2e/bajutsu/actions/workflows/ci.yml) [![iOS E2E (Simulator)](https://github.com/bajutsu-e2e/bajutsu/actions/workflows/ios-e2e.yml/badge.svg)](https://github.com/bajutsu-e2e/bajutsu/actions/workflows/ios-e2e.yml) [![Web E2E (Playwright)](https://github.com/bajutsu-e2e/bajutsu/actions/workflows/web-e2e.yml/badge.svg)](https://github.com/bajutsu-e2e/bajutsu/actions/workflows/web-e2e.yml) [![Android E2E (emulator)](https://github.com/bajutsu-e2e/bajutsu/actions/workflows/android-e2e.yml/badge.svg)](https://github.com/bajutsu-e2e/bajutsu/actions/workflows/android-e2e.yml) [![Serve DB (Postgres)](https://github.com/bajutsu-e2e/bajutsu/actions/workflows/serve-db.yml/badge.svg)](https://github.com/bajutsu-e2e/bajutsu/actions/workflows/serve-db.yml)

> backend 非依存のドライバを土台とする自然言語駆動 E2E（エンドツーエンド）テストです。シナリオ形式と
> 決定的ランナーは 1 つで、**プラットフォームはその 1 つのインターフェースの背後の backend に過ぎません**。
> backend を差し替えれば、同じシナリオが別のターゲットで動きます。iOS Simulator（XCUITest）、
> web（Playwright）backend、Android（adb）backend はいずれも実装済みです。Flutter アプリは
> 同じ iOS / Android の backend の上でそのまま動き、新しい backend を追加する必要はありません
> （[BE-0008](roadmaps/BE-0008-flutter-support/BE-0008-flutter-support-ja.md)）。
> **ステータス: pre-alpha。** 決定的コア、AI オーサリングループ（`record` / `crawl`）、証跡
> サブシステム、codegen、自己修復トリアージはいずれも実装・ユニットテスト済みです
> （Simulator 不要）。iOS の **XCUITest backend** は **実機 Simulator で
> end-to-end に検証済み**で、シナリオ実行・証跡取得・triage の自己修復ループはいずれも実機で
> 動きます。**web（Playwright）backend** はブラウザに対する決定的な `run` を
> Linux のゲート上で動かし（[`demos/web`](demos/web/README.ja.md)）、**Android（adb）backend** は
> エミュレータ上で end-to-end に検証済みです（[`android-e2e.yml`](.github/workflows/android-e2e.yml)）。
> ホスティング可能な制御プレーンと Mac / Linux ワーカーからなる **サーバ backend** も実装済みです。
> `serve` をチームで共有したいときに使えます
> （[`docs/ja/self-hosting.md`](docs/ja/self-hosting.md)）。

Bajutsu は自然言語で書かれた（または記録された）テストシナリオを受け取り、アプリを操作（tap / type /
swipe / wait）して、**機械チェック可能なアサーション**で結果を検証します。1 つの継ぎ目を除いてすべてが
プラットフォーム非依存です。シナリオ形式、セレクタ解決、決定的ランナー、証跡サブシステム、レポーターは
どれもプラットフォームを名指ししません。その 1 つの継ぎ目が、UI を操作する **backend** です。ランナーを
別の backend に向ければ、同じシナリオが別のターゲットで動きます。対象は iOS Simulator（XCUITest）、
ブラウザ（Playwright）、Android（adb）です。Flutter アプリは同じ iOS / Android の backend の上で
そのまま動き、新しい backend は要りません。プラットフォームを選ぶとは backend を
選ぶことであり、別のツールへ乗り換えることではありません。

> **名前について。** *Bajutsu*（馬術）は馬を扱う技術を指す日本語です。この名前は、ツールが扱う
> テスト不安定要因（フレーキーなタイミング、非同期遷移、想定外のシステムアラート）に由来します。
> これらは **iOS Simulator** で顕著に現れます。Bajutsu は対象をシナリオ通りに決定的に操作し、毎回
> 同じ結果になるようにします。Simulator でも、同じドライバの下にあるどの backend でも同じです。

中核となる設計判断は、**LLM（大規模言語モデル）を CI（継続的インテグレーション）ゲートに
持ち込まない**ことです。

- **AI は著者と失敗時の調査役であり、判定者ではありません。** シナリオを *書く*（探索 + 記録）・
  失敗を *調べる* のは助けますが、`run` は完全に決定的で AI を含みません。合否は機械アサーションの
  みで決まります。
- **2 層構成。** Tier 1 は AI のライブ操作（探索 / オーサリング）、Tier 2 は CI 回帰向けの決定的
  ランナーです。

設計指針（日本語）は [`DESIGN.md`](DESIGN.md) にあります。実装ベースの機能別ドキュメント（英語、
日本語ミラーは [`docs/ja/`](docs/ja/README.md)）は [`docs/`](docs/README.md) にあります。

## 中核原則

- **決定性ファースト。** 固定 `sleep` は使わず、条件待機のみを使います。曖昧なセレクタは「最初の
  一致を叩く」のではなく即失敗します。各テストはクリーン環境から開始します。
- **安定セレクタ。** 非ローカライズでデータ由来の id を優先します。iOS なら `accessibilityIdentifier`、
  web なら `data-testid` です。テキストより id を選び、座標は最終手段です。
- **安定度順ラダー。** UI 操作は最も安定する手段から試します（id による semantic tap → 座標 tap → …）。
  選ぶ backend も、利用可能な中で最も安定なものにします。
- **プラットフォームは backend。** 決定的コアはプラットフォームを名指ししません。プラットフォーム固有の
  継ぎ目は `Driver` インターフェースの背後の backend（xcuitest / playwright / adb / …）ただ 1 つです。
  backend を足したり差し替えたりするだけで、同じシナリオ形式、ランナー、CLI が新しいプラットフォームを
  不変のまま対象にします。アプリやプラットフォームに固有の差分は config と選んだ backend にだけ置きます。
- **証跡はルール。** 「X のたびに取得」を再利用可能なルールへ正規化し、2 度目以降は AI なしで
  同じ証跡を再現します。

## アーキテクチャ

![データフロー図。自然言語のゴールまたは人手編集がシナリオ YAML を生成し、Tier 2 の Orchestrator が backend 非依存の Driver API を通じて XCUITest・adb・Playwright のいずれかに対して決定的に実行します。合否は Reporter に渡り、失敗時は triage がシナリオへの修正案を提案します。](docs/ja/assets/diagrams/architecture-data-flow-ja.svg)

エントリポイントはシナリオ形式を共有します。`record` と `crawl`（AI オーサリング / 探索）、
`run`（決定的リプレイ）、`codegen`（ネイティブテストを出力）です。決定的コア（セレクタ解決、
orchestrator、証跡、config、レポート）は [`bajutsu/common/`](bajutsu/common) に置きます。
ホスティング制御プレーンと、それを呼び出せるローカル Web UI は [`bajutsu/serve/`](bajutsu/serve)
に置きます。import-linter の契約が両者を隔てているため、決定的コアがホスティング層に依存する
ことはありません。モジュール一覧、依存レイヤ、編集可能な mermaid ソースはすべて
[docs/ja/architecture.md](docs/ja/architecture.md) にあります。機能別の詳細は
[`docs/ja/`](docs/ja/README.md) を参照してください。

## ステータス

実装済み・テスト済みです（Simulator 不要で実行できます）。

- ドライバ抽象と **セレクタ解決**（決定性の核）
- **プラットフォーム対応の backend レジストリ**。`--backend` / `backend:` は `ios` / `android` /
  `web` / `fake` を受け取り、それぞれが安定度順に actuator へ展開されます（`ios` は idb 廃止以降
  唯一の iOS actuator である `xcuitest` に展開されます）
- **シナリオスキーマ**: ステップ（tap / type / swipe / drag / scroll / double-tap / pinch / rotate、
  WebView コンテンツ向けの `web`、デバイス制御、`http` / `totp` / `email` / `generate` /
  `manual` など）、待機、ステップ内の `assert`、再利用可能なコンポーネント（`use`）、制御フロー
  （`if` / `forEach`）、変数（`extract` → `${vars.*}`）、パラメータ化（`data` / `dataFile`）、
  `preconditions`、`permissions`、`interrupts`、`systemAlertHandling`、ネットワーク `mocks`、
  `network` フィルタ、`capturePolicy` 証跡ルール、`redact`。厳格検証・YAML ラウンドトリップ・
  生成された JSON Schema 付きです
- **アサーション評価**（exists / value / label / count / enabled / disabled / selected /
  request / requestSequence / event / responseSchema / clipboard / golden / **visual**）
- **Tier 2 run ループ**（act → wait → verify）。インメモリ fake driver で検証しています
- **証跡サブシステム**: 瞬時証跡（screenshot / elements / actionLog）、`video` / `deviceLog` /
  `appTrace` の区間証跡、ネットワーク観測 + `mocks`、**ビジュアルリグレッション**（baseline +
  `approve`）、golden 要素ツリー比較、`capturePolicy` トリガールール、シークレットの **redaction**
- **レポート**（`manifest.json` + JUnit XML + CTRF JSON + 自己完結のインタラクティブ HTML）
- **config 解決**（チーム既定 × アプリ別。iOS は `bundleId`、web は `baseUrl`）と **backend 選択**
  （安定度順）
- **backend コマンド層**（iOS 向け simctl、Android 向け adb）、**XCUITest チャネル**（常駐ランナーの
  要素スナップショット）、**Playwright web ドライバ**、**adb ドライバ**（常駐の UI Automator サーバ、
  フォールバックとして `uiautomator dump`）、**doctor** 規約スコアと環境 preflight
- **助言的分析**（CI をゲートしません）: `audit`（静的・観測ベースの決定性スコア）、`coverage`
  （id 名前空間カバレッジマップ）、`impact`（`git diff` からのテスト影響範囲分析）、`flakiness`
  （複数回の run をまたぐフレーキーシナリオのランキング）、`stats`（run 履歴ダッシュボード）
- **AI オーサリング**: `record`（ゴール志向）と `crawl`（決定的な幅優先スクリーンマップ、詰まった
  ときは AI 支援の `guide` / `tabs` にフォールバック）。ベンダー非依存の `AiBackend` 層が
  5 つのプロバイダ（Anthropic API、Amazon Bedrock、Anthropic の CLI、Claude Code の CLI、AI を
  丸ごと無効化する `none`）を持ち、システムアラートガードを伴います
- **codegen**: シナリオからネイティブテストへの変換で、XCUITest（Swift、iOS）、Playwright
  （TypeScript、web）、UI Automator（Kotlin、Android）の 3 ターゲットに対応します（構造マッピング、
  テスト時は AI 不要）
- **自己修復トリアージ**: 既定はルールベースの heuristic agent で、`--ai` で Claude ベースの
  agent に切り替えられます。原因 + 最小修正案を出す、CI ゲートから外れた助言です
- 配線済み CLI: `run` / `doctor` / `audit` / `coverage` / `impact` / `stats` / `flakiness` /
  `export` / `trace` / `report` / `triage` / `record` / `crawl` / `repl` / `codegen` / `approve` /
  `serve` / `mcp` / `worker` / `lint` / `schema`
- **MCP サーバ**（`bajutsu mcp`）: `run` と `doctor` を MCP ツールとして、run の証跡
  （manifest / report / JUnit / artifact）をリソースとして公開し、Claude Desktop / Code 連携に
  使えます
- **Web UI**（`bajutsu serve`）: シナリオのオーサリング（`record` / `crawl`）・編集・実行、レポートと
  あらゆる証跡の閲覧、ビジュアル baseline の承認、SSE によるジョブのライブ配信、usage / flakiness /
  coverage の各ダッシュボードです。各タブの操作方法は [docs/ja/web-ui.md](docs/ja/web-ui.md) を
  参照してください
- **ホスティング可能なサーバ backend**（`serve --backend server`、`bajutsu worker`）: FastAPI、
  Postgres、S3 互換オブジェクトストレージ、GitHub OAuth ログイン、RBAC（ロールベースアクセス
  制御）、クォータを備えた、自前でホスティングできる制御プレーンです。Mac / Linux ワーカーは制御
  プレーンの URL とトークンだけを持ち、クラウド SDK やオブジェクトストレージの認証情報は要りま
  せん。そのワーカーが素の HTTP でキューをポーリングするので、チームは `serve` をマシンごとに
  動かす代わりに 1 つ共有できます。ガイドは [`docs/ja/self-hosting.md`](docs/ja/self-hosting.md) に
  あります

実機 Simulator で検証済みです（iPhone 17 Pro・近年の iOS）。

- `showcase` のシナリオ実行・証跡取得・triage 自己修復ループを実機で走らせ、XCUITest backend
  （常駐ランナーの要素スナップショット、バンドル ID 指定でのセマンティックタップ / text /
  マルチタッチ / テキスト選択、`xcodebuild` で組み上げるランナー）を確認済みです。

ブラウザで検証済みです（Linux・Mac 不要）。

- Playwright backend は [`demos/web`](demos/web/README.ja.md) のシナリオを決定的に実行し、CI と
  同じゲートの中で動きます。リッチ寄りの web 機能（ネットワーク取得、動画、マルチタッチ、並列実行）も
  含めて、コアがプラットフォーム非依存であることの裏付けです。

Android エミュレータで検証済みです（Linux・Mac 不要）。

- adb backend の要素読み取り（既定は常駐の UI Automator サーバ、フォールバックが `uiautomator
  dump`）、デバイス側で再解決する tap（フォールバックはホスト側で算出したフレーム中心の座標
  tap）、起動シーケンスは XCUITest と同等の actuation を実現しており、KVM 上で起動した API 34 の
  エミュレータに対して
  [`android-e2e.yml`](.github/workflows/android-e2e.yml) が同じ共有シナリオを走らせて確認済みです。

実際の Postgres でも検証済みです（Linux・Mac 不要）。

- サーバ backend の Alembic マイグレーションと、その上のオブジェクト関係マッピング（ORM）による
  リポジトリ層を [`serve-db.yml`](.github/workflows/serve-db.yml) が使い捨ての `postgres:16`
  コンテナに対して走らせて確認済みです。参考情報の任意チェック（signal）として着地し、安定が
  確認された後に**必須チェック**へ昇格しました。

未配線: 外部 `mockServer` コマンド（シナリオ内 `mocks` で代替済み）、web backend 上の `appTrace`
区間証跡（iOS 専用で、web backend は独自の `video` / `deviceLog` 相当の証跡を持ちます）、
SwiftUI / Compose 画面での `nativeZ` z-order レポート（宣言的ツールキット側の制約であり、
Bajutsu 側の欠落ではありません）。完全な「実装済み vs 未配線」表は
[`docs/ja/architecture.md`](docs/ja/architecture.md) にあります。

## 要件

- Python 3.13（[uv](https://github.com/astral-sh/uv) で管理）。決定的コアとゲート全体は Linux を
  含むどこでも動きます
- **iOS の場合:** macOS + Xcode（iOS Simulator と `xcodebuild`）。XCUITest ランナーはリポジトリから
  ビルドします
- **web の場合:** Playwright の Chromium（`playwright install chromium`）が入った任意の OS。Mac は
  不要です
- **Android の場合:** `adb` と、起動済みのデバイスまたはエミュレータが入った任意の OS。Mac は
  不要です（CI は API 34 のエミュレータで検証しています）
- **ホスティング可能なサーバ backend の場合:** 制御プレーン向けの Linux ノード（Postgres、S3 互換
  ストレージ）に加えて、Mac ワーカーか Linux ワーカー、あるいはその両方が要ります。詳細は
  [`docs/ja/self-hosting.md`](docs/ja/self-hosting.md) を参照してください。任意項目で、ローカルの
  `bajutsu serve` はどれも要りません

## セットアップ

> **はじめての方へ。** [Getting started チュートリアル](docs/ja/getting-started/index.md)は一連のループを
> iOS Simulator で辿ります。**Mac がない場合は**、[web トラック](docs/ja/getting-started/web.md)が同じ
> ループをブラウザ（Playwright backend）に対してどの OS でも辿ります。Xcode も Simulator も要りません。

```bash
make setup                 # 土台: .venv（Python 3.13）+ 開発ツール + git hooks（backend なし・どこでも動く）
make install               # 土台に加えて、config が使う backend だけを導入（config 対応）
```

`make setup` は決定的ゲートが必要とする backend 非依存の土台です。`make install` はその上に重ねます。
`--config`（`make install ARGS="--config demos/showcase/showcase.config.yaml"` のように渡します）を読み、
`targets.*` が実際に使う backend と、AI プロバイダが設定されているかどうかを解決し、必要な pip extra と
外部ツールだけを導入します（iOS なら XCUITest ランナーを組む Xcode の `xcodebuild`、web なら
Playwright のブラウザ、AI が設定されていれば `anthropic` SDK）。冪等なので再実行しても安全です。
作業ディレクトリに config がなければ、土台以外は何も導入しません。導入元の要件は単一のマッピング
（[`bajutsu/common/provisioning/`](bajutsu/common/provisioning)）にまとまっており、`doctor` の
pre-flight と共有するので両者がずれることはありません。

## 使い方

CLI の概要です（完全リファレンスは [`docs/ja/cli.md`](docs/ja/cli.md)）。

```bash
bajutsu run    --target <name> [--scenario file.yaml]        # 既定: アプリのシナリオディレクトリ全体
bajutsu record --target <name> --goal "..." [--out file]     # AI 探索 + 記録（Tier 1・要 API キー / ログイン）
bajutsu crawl  --target <name> [--max-screens N]             # 幅優先クロール → スクリーンマップ（Tier 1）
bajutsu doctor --target <name>                               # 環境チェック + 現在画面の規約スコア
bajutsu codegen <scenario.yaml> --target <name> -o UITests/Foo.swift   # ネイティブ XCUITest を出力
bajutsu approve --baselines <dir> [--scenario s.yaml]     # 取得済みスクリーンショットを visual baseline に昇格
bajutsu serve  [--port 8765] [--config c.yaml]            # ローカル Web UI: オーサリング + 実行 + レポート（Tier 1）
bajutsu mcp    [--config c.yaml] [--transport stdio]      # エージェント連携用 MCP サーバ（要 `bajutsu[mcp]`）
bajutsu lint   <scenario.yaml>                            # 実行せずにシナリオを検証
bajutsu schema                                            # エディタ連携用の JSON Schema を出力
```

`trace`（完了した run の確認）、`report`（保存済みデータから `report.html` / JUnit / CTRF を
再生成）、`triage`（失敗の診断）、`worker`（サーバ backend からキュー済み run をリース）も
揃っています。CLI には `audit` / `coverage` / `export` / `flakiness` / `impact` / `stats` も
あります。全コマンドは [CLI リファレンス](docs/ja/cli.md) を参照してください。

> `make serve`（または `scripts/serve.sh`）は `bajutsu serve` をラップし、設定された backend の
> 依存を必要時に導入します。これにより、クリーンなチェックアウトでも
> `no available actuator among ['xcuitest']` に当たりません。フラグは `make serve ARGS="--port 8766"`
> のように渡します。

アプリ別・プラットフォーム別の設定は、`--config` で渡す config ファイルに置きます。デモにはすぐ動く
ものが同梱されています（例: [`demos/showcase/showcase.config.yaml`](demos/showcase/showcase.config.yaml)、
[`demos/web/demo.config.yaml`](demos/web/demo.config.yaml)）。アプリは `bundleId` で iOS を、`baseUrl`
で web を対象にします。

```yaml
defaults:
  backend: [ios]            # 安定度順; 最初に利用可能な backend が actuator
  device: "iPhone 17 Pro"
  locale: en_US

targets:
  showcase-swiftui:         # iOS アプリ — XCUITest 経由で Simulator を操作
    bundleId: com.bajutsu.showcase.ios.swiftui
    deeplinkScheme: showcaseswiftui
    launchEnv: { SHOWCASE_UITEST: "1" }
    idNamespaces: [stable, horse, search, log, notice, perm, sys, net]
    scenarios: demos/showcase/scenarios

  web:                      # web アプリ — Playwright 経由でブラウザを操作
    baseUrl: "http://127.0.0.1:8787/index.html"
    backend: [web]
    scenarios: demos/web/scenarios
```

## デモ

実行できるデモは、すべて 1 つのエントリポイント `make -C demos <target>` から動かせます
（[`demos/`](demos/README.ja.md)）。

- **[tour](demos/tour/README.ja.md)**（`make -C demos tour`）。実行 → 改変 → 診断のライフサイクル全体を
  実機 Simulator で完全に決定的に通します。**API キー不要**です。（インメモリの fake デバイスに対して
  **セットアップ不要**でも動きます: `uv run python demos/tour/tour.py`。）
- **[features](demos/showcase/README.ja.md)**（`make -C demos features`）。シナリオ著作の機能（タグ、
  パラメータ化した共有ステップ、シークレット）を実機 Simulator で示します。
- **[webui](demos/showcase/WEBUI.ja.md)**（`make -C demos webui`）。**Web UI** で Simulator を操作し、
  あらゆる証跡（スクリーンショット、動画、ログ、通信（観測 + モック）、ビジュアルリグレッション、
  システムアラート突破）をブラウザで集めます。iOS 開発者向けの目玉デモです。
- **[record](demos/showcase/README.ja.md)**（`make -C demos record`）。起動中アプリに対する本物の Claude に
  よる著作と、改変 → 自己修復（`triage`）ループです。
- **[web](demos/web/README.ja.md)**（`make -C demos/web e2e`）。**Playwright backend** で静的な web アプリ
  に対しシナリオを実行します。Mac も Simulator も不要で、Linux で動きます。このデモを手順を追って辿る
  なら、[web getting-started トラック](docs/ja/getting-started/web.md)を参照してください。
- **[docs-site](demos/docs-site/README.ja.md)**（`bajutsu run --target docs --backend web --config
  demos/docs-site/docs-site.config.yaml`）。Playwright backend で公開中の
  [ドキュメントサイト](https://bajutsu-e2e.github.io/bajutsu/)そのものを操作します。ローカルに立てる
  アプリはなく、対象は公開 URL です。
- **[serve-ui](demos/serve-ui/README.ja.md)**（`make -C demos/serve-ui e2e`）。Playwright backend で
  `serve` の Web UI 自身の単一ページアプリを dogfooding します。Mac も Simulator も不要な、Web UI
  向けの決定的な回帰ネットです。

## 開発

```bash
make check                # 完全なゲート: format + lint + 型チェック + テスト（CI と同一）
uv run pytest -q          # テストのみ（Simulator 不要）
```

作業規約は [`CLAUDE.md`](CLAUDE.md) と [`CONTRIBUTING.ja.md`](CONTRIBUTING.ja.md) を参照してください。

## プロジェクト構成

```
bajutsu/
├── common/               # 決定的コア + 共有周辺: drivers、シナリオスキーマ、orchestrator、証跡、
│                         #   config、backend コマンド層、AI 層、provisioning、analytics、
│                         #   github 連携、cancellation、devices
│   ├── drivers/          #   Driver プロトコル + セレクタ解決; fake / xcuitest (iOS、実機直結の
│   │                     #     経路を含む) / playwright (web) / adb (Android)
│   ├── scenario/         #   シナリオスキーマ (models)、YAML ラウンドトリップ、展開、JSON Schema、
│   │                     #     ${namespace.key} 補間、システムアラート設定
│   ├── orchestrator/     #   決定的 Tier 2 run ループ（act → wait → verify）
│   ├── runner/           #   config + シナリオ -> レポート; デバイスプール経由
│   ├── evidence/         #   瞬時 / 区間証跡、ネットワーク観測、ビジュアルリグレッション、
│   │                     #     golden 要素ツリー比較、シークレットの redaction
│   ├── report/           #   manifest.json + JUnit + CTRF + インタラクティブ HTML
│   ├── config/           #   チーム既定 × アプリ別の解決
│   ├── ai/               #   ベンダー非依存の AiBackend 層 (Anthropic API / Bedrock / CLI /
│   │                     #     Claude Code / 無効化)
│   ├── agents/           #   ai/ 層の上に立つオーサリング agent 周辺: record / enrich / triage
│   │                     #     の各 agent、システムアラートガード
│   ├── backend_cli/      #   simctl (iOS) + adb (Android) のコマンド層
│   ├── doctor/           #   規約スコア
│   ├── capability/       #   doctor / CI 向けの環境 preflight
│   └── github/           #   GitHub 連携: Actions アノテーション、App インストールトークン
├── run/                  # `bajutsu run` の CLI 呼び出し: target / backend 解決、デバイスの
│                         #   リース、レポートの配送、run 完了通知（notify/）
├── record/               # AI record ループ: 観測 -> 提案 -> 実行 -> シナリオ出力
├── crawl/                # 幅優先クロール -> スクリーンマップ（決定的な core/、AI 支援の guide/
│                         #   と tabs/、レイアウト用の report/）
├── repl/                 # AI を使わない手動シェル: 要素ツリーを読み、id を指定して操作する
├── triage/               # 自己修復トリアージ: ルールベースの heuristic agent（既定）と
│                         #   Claude ベースの agent（--ai）
├── codegen/              # シナリオ -> ネイティブテスト (XCUITest / Playwright / UI Automator)
├── analysis/             # 読み取り専用の助言的分析、CI をゲートしない: audit / coverage /
│                         #   flakiness / impact / stats / trace
├── serve/                # ローカル Web UI（オーサリング + 実行 + レポート; Tier 1）、および
│                         #   server/ 配下にホスティング制御プレーン（FastAPI + Postgres +
│                         #   OAuth）。`serve --backend server` の背後にあります
├── mcp/                  # MCP サーバ（エージェント連携用のツール + リソース）
├── templates/            # Jinja のレポート / ダッシュボードテンプレート + Web UI 自身の JS / CSS
├── cli/                  # CLI (typer) — コマンドごとに 1 ファイル
└── __main__.py
```

## ロードマップ

Bajutsu は独立して組み合わさる 3 つの軸で成長します。**reach**（より多くのプラットフォームと面）、
**scale & collaboration**（ローカルツールから共有・ホスティング型サービスへ）、
**authoring & maintenance**（テストを所有するコストを下げる）です。決定的ランナー、AI `record`
ループ、`capturePolicy` 証跡ルール、codegen、自己修復トリアージが最初に着地しました（実装済みの
範囲は上の[ステータス](#ステータス)を参照してください）。それ以降、reach 軸では **web
（Playwright）** backend と **Android（`adb`）** backend、**Flutter** 対応が実装済みになりました。
scale 軸では、公開ホスティングと自前ホスティングの両方の制御プレーン構成が実装済みになりました。
どの方向へ進むときも、Tier 2 の `run` / CI ゲートに AI を持ち込まないという不変条件は変わりません。
各軸の根拠と現在地は [`docs/ja/vision.md`](docs/ja/vision.md) にあります。

今後の優先順位付きバックログ（次に作りたいもの）は [`roadmaps/`](roadmaps/README-ja.md) にあります。

## ライセンス

このプロジェクトは [Apache License, Version 2.0](LICENSE) の下で提供されています。帰属表示については [`NOTICE`](NOTICE) を参照してください。

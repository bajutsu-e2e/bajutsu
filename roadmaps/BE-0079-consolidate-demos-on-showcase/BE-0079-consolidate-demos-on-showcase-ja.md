[English](BE-0079-consolidate-demos-on-showcase.md) · **日本語**

# BE-0079 — デモ／dogfood 用アプリを showcase 群へ統合する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0079](BE-0079-consolidate-demos-on-showcase-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0079") |
| 実装 PR | [#371](https://github.com/bajutsu-e2e/bajutsu/pull/371), [#418](https://github.com/bajutsu-e2e/bajutsu/pull/418), [#438](https://github.com/bajutsu-e2e/bajutsu/pull/438) |
| トピック | Dogfood フィクスチャ（デモアプリ） |
| 関連 | [BE-0107 — showcase の各タブへナビゲーションで到達](../BE-0107-showcase-tab-navigation-no-launch-shortcut/BE-0107-showcase-tab-navigation-no-launch-shortcut-ja.md) |
| 由来 | Dogfooding |
<!-- /BE-METADATA -->

## はじめに

[BE-0045](../BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps-ja.md) で、
**showcase** 群（同じアプリを UIKit と SwiftUI で書き、各々をアクセシビリティ有／無の変種で出す。
2 コードベースで 4 プロダクト）を、Bajutsu の次世代 dogfood 対象として出荷しました。ただし移行そのものは
意図的にスコープから外しています。BE-0045 自身の設計ノートは、旧来の `sample` フィクスチャについて
「showcase がオンデバイス CI と Web UI ツアーをカバーするまで残す。置き換えは後続作業であって本アイテムの一部ではない」
と述べています。

本アイテムがその後続作業です。showcase を**唯一**の iOS フィクスチャにします。すなわち、showcase を旧来の 3 アプリと
同等の機能まで引き上げ、すべてのデモとオンデバイス CI ジョブの向き先を showcase に変え、その上で `demo`
（[`demos/app`](../../../demos/app)）、`sample`（[`demos/features/app`](../../../demos/features/app)）、`sample2`
（[`demos/record/app`](../../../demos/record/app)）を**退役**させます。Web フィクスチャ
（[`demos/web`](../../../demos/web)、Playwright backend）は別プラットフォームなのでスコープ外で、そのまま残します。

## 動機

**1. iOS アプリが 4 本あることが dogfood の物語を分断し、保守コストを増やしている。** 現状、デモとオンデバイス検証は
4 つのコードベースに散っています。

| フィクスチャ | 場所 | bundle id | 使う場所 |
|---|---|---|---|
| `demo` | [`demos/app`](../../../demos/app) | `com.bajutsu.demo` | `tour` デモ（およびトップレベルの `make -C demos features`） |
| `sample` | [`demos/features/app`](../../../demos/features/app) | `com.bajutsu.sample` | `webui` ツアー、**およびオンデバイス CI**（[`e2e.yml`](../../../.github/workflows/e2e.yml) の `smoke (idb)` + `xcuitest (codegen)`） |
| `sample2` | [`demos/record/app`](../../../demos/record/app) | `com.bajutsu.sample2` | `record` デモ |
| `showcase`（4 プロダクト） | [`demos/showcase`](../../../demos/showcase) | 4 つの `targets.<name>` | `run` / `doctor` / `record`、将来の `crawl` |

どれも個別の Xcode プロジェクトで、ビルドし続け、アクセシビリティを保ち、DESIGN や規約と揃え続ける必要があります。
新しい機能デモはそのつどどれか 1 つを選ばねばならず、コントリビュータは「どのアプリが何を示すのか」を覚える羽目に
なります。フィクスチャが 1 本になれば、この負担が消えます。

**2. showcase は明確に上位互換の後継であり、ドキュメントもすでにそう書いている。** showcase は操作面の全体
（5 タブ、ナビゲーションの push、4 種すべてのモーダル、テキスト入力、非同期読み込み、ライブ＋モック可能なネットワーク、
OS アラートを出す画面）を備え、さらに単一変種のアプリでは示せない 2 つの軸を持ちます。**ツールキット**軸
（UIKit と SwiftUI の要素ツリーの違い）と、**アクセシビリティ**軸（`-a11y` ↔ `-noax`。セレクタ安定性の対照実験、
[DESIGN §5](../../../DESIGN.md)）です。showcase の README と `SPEC.md` は、すでに `sample` を置き換えるものとして
記述しています。一方で [DESIGN §1.1](../../../DESIGN.md) は今なお `demos/features/app`（`com.bajutsu.sample`）を
「最初の dogfood 対象」と名指ししており、これは陳腐化しています。本アイテムは、その記述の向き先を showcase に変えて
再び真にします。

**3. フィクスチャが 1 本なら、練習対象となる契約も 1 つで済む。** アプリ固有の差分はすべて config に寄せてある
（[DESIGN §8](../../../DESIGN.md)）ため、フィクスチャが 1 群あれば、今後のあらゆる機能（visual 回帰のベースライン、
データ駆動の run、crawl の画面マップ、`doctor` のアプリ全体カバレッジ）が、そのつど使い捨てアプリを作ることなく、
すぐ使える代表的な対象を 1 つ持てます（BE-0045 の動機 4 を、代替肢を取り除くことで実現します）。

**4. すべてのプライム指令を尊重する**（[CLAUDE.md](../../../CLAUDE.md)）。変更はテスト**対象**＋ config ＋シナリオ＋
CI 配線＋ドキュメントのみです。どのゲートにも LLM 呼び出しを足さず、ツール／ドライバ／実行系は不変です。showcase は
[DESIGN §7.1](../../../DESIGN.md) のとおり、すべて `targets.<name>` エントリ経由でオンボードされます。

## 詳細設計

**到達状態。** [`demos/showcase`](../../../demos/showcase) が唯一の iOS フィクスチャになります。旧来の 3 アプリの
ディレクトリは、その config、シナリオ、ハーネス、README ごと無くなり、リポジトリ全体のそれらへの参照はすべて更新します。
[`demos/web`](../../../demos/web)（Playwright フィクスチャ）と
[`demos/serve-ui`](../../../demos/serve-ui)（Web UI の dogfood、
[BE-0058](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui-ja.md)）は別プラットフォーム／別対象なので、
**手を付けません**。

### A. showcase が既にカバーしているもの（正直なベースライン）

showcase はすでにかなり近い位置にあり、作業は「ゼロから作り直す」ことではなく「特定の穴を埋める」ことです。
[`SPEC.md` §5](../../../demos/showcase/SPEC.md) のとおり、現状で次を備えています。push ナビゲーションを伴う 5 タブ、
4 種すべてのモーダル（detent 付きシート、フルスクリーンカバー、アクションシート、自動で消えるトースト）、テキスト入力
（`log.note`、`search.field`）、ステッパ（`log.count`）とトグル（`log.intense`、状態を value に映す `horse.favorite`）、
非同期読み込み（Stable のカタログ）、スクロールして要素に到達する長いリスト（Notices）、`sample` と**同じ** BajutsuKit
連携によるライブ＋モック可能なネットワーク（redaction 用に意図的に秘匿ヘッダ／ボディを載せたものを含む）、そして
OS アラートの画面（Permissions）。その `scenarios/` はすでに、タブ、ナビゲーション、モーダル、検索、ネットワークモック、
notices、permission、smoke 経路を駆動しています。

### B. 実装するもの（削除の前に埋める穴）

1. **codegen → XCUITest ターゲット。** `sample` は `BajutsuSampleUITests` ターゲットと `UITests` スキームを持ち、
   CI の `xcuitest` ジョブは `make -C demos/features ui-test` で `components.yaml` から `ComponentsUITests.swift` を
   生成し、`xcodebuild test` で走らせます。showcase にはこれが一切ありません。showcase のいずれかのコードベース
   （`{swiftui,uikit}/project.yml`）に UITests ターゲットと `UITests` スキーム、代表的な範囲を駆動するシナリオ、
   そして [`demos/showcase/Makefile`](../../../demos/showcase/Makefile) に `ui-test` ターゲットを足します。
2. **visual 回帰のシナリオ＋ベースライン。** `sample` には `visual.yaml` とベースラインの手順（`make vrt` /
   `vrt-approve`）があります。visual 回帰ツアーが対象を持てるよう、`scenarios/visual.yaml` と `vrt` / `vrt-approve` の
   Make ターゲットを足します。
3. **本当に不足している操作ターゲットだけ** を、`sample` の画面と突き合わせて洗い出し、既存のタブの中に、他と同じ
   `ACCESSIBLE` コンパイルフラグ（SPEC §8）で切り替える形で、ソースを分岐させず、名前空間規律（SPEC §9）を保って足します。
   - **ジェスチャ：** 明示的なロングプレスとダブルタップのターゲット（showcase は Notices のスクロールで `swipe` を
     行使していますが、専用のロングプレス／ダブルタップ要素はありません。`sample` は両方を行使し、`GesturesUITests.swift`
     も持ちます）。
   - **デバイス／システム状態：** デバイス操作ステップ（`background`、`clearClipboard`、`clearKeychain`、
     `overrideStatusBar`）の結果を読める value に**映す**小さな画面。`sample` の `SystemView` が行っていることで、
     シナリオがそれをアサートできるようにします。
   - **コントロール：** 既存のステッパ＋トグルに加えて操作面を補うなら、スライダ／セグメンテッドコントロール。
   - **再利用セットアップ：** 共有コンポーネントの前段（現状は `demo` の `_components/login.yaml` が示している）を
     showcase のフローで表現し直します。showcase は意図的にログイン無しのアプリなので、ログインではなく「ナビゲート＋
     シード」の前段にします。
   新しい id を足したら、あわせて [`SPEC.md`](../../../demos/showcase/SPEC.md)（§5 / §9）も更新します。
4. **証跡ツアーの全体。** [`WEBUI.md`](../../../demos/features/WEBUI.md) の、証跡の全種別（スクリーンショット、動画、
   デバイスログ、ネットワーク（観測＋モック）、visual 回帰、システムアラート処理）を一巡する道筋を showcase に移植し、
   showcase の `scenarios/` と `capturePolicy` がそのすべてを実際に発火させることを確かめます（surface が揃った範囲で
   `evidence` / `network` / `relaunch` / `controls` / `gestures` / `system` / `text` / `async` 相当のシナリオを足します。
   `settings`／`reindex` のような `sample` 固有画面は、showcase のフロー（例：`stable.refresh` や `log.submit`）で表現し直します）。
5. **絞り込んだ first-look の一片。** `demo` / `sample2` は、`tour` と `record` がひと目で読めるよう意図的に最小
   （オンボーディング → ログイン → カウンタ）にしてあります。showcase にはログイン／カウンタが無いので、その最小の
   **筋書き**は、既存の showcase フロー上の**絞り込んだシナリオ／ゴール**として残します（例：馬を開いて `horse.favorite` を
   トグルする、または `log.count` をステップして value のミラーをアサートする。「変更したらアサーションが壊れる」を見せる
   きれいな対象です）。同じ run → modify → triage と record → 自己修復のライフサイクルを、2 本目のバイナリではなく、
   1 本のアプリの一片の上で見せます（`record` は `make -C demos/showcase record` ＋ `record/goals.txt` で既に showcase を
   対象にしているので、動かすのはトップレベルのメニュー項目だけです）。

### C. 向き先を変えるもの（挙動は同じ、対象が変わる）

- **オンデバイス CI**：[`e2e.yml`](../../../.github/workflows/e2e.yml)：`smoke (idb)` と `xcuitest (codegen)` の
  向き先を showcase に変え（ビルド → インストール → 実行）、`changes` のパスゲート
  （`demos/features/app/`、`demos/features/demo.config.yaml` → `demos/showcase/…`）、DerivedData のキャッシュパスと
  `hashFiles` キー、インストールパス、[`bajutsu-e2e`](../../../.github/actions) アクションの入力
  （`scenarios` / `target` / `config`）を更新します。**注意点：** これらのジョブはローカルの `make check` ゲートに
  **含まれず**、`.github/` は ripgrep の既定の掃き出しから漏れます。変更は `e2e.yml` と composite action を明示的に
  カバーする必要があります。
- **デモのメニュー**：トップレベルの [`demos/Makefile`](../../../demos/Makefile)（`tour` / `features` / `offline` /
  `webui` / `record`、`app-build`）と [`demos/demo.config.yaml`](../../../demos/demo.config.yaml) の向き先を showcase に
  変え、旧アプリをハードコードしているハーネススクリプト（[`demos/tour/demo.sh`](../../../demos/tour/demo.sh)、
  [`demos/tour/tour.py`](../../../demos/tour/tour.py) の bundle id、[`demos/record/demo.sh`](../../../demos/record/demo.sh)）を
  showcase に向け直すか畳み込み、[`demos/README.md`](../../../demos/README.md)（＋ `.ja`）と各デモの README を、
  `make -C demos …` のメニュー全体が 1 本のアプリを駆動するように書き直します。
- **ドキュメント（英日両方。各 EN ファイルとその `docs/ja` ミラーを更新）。** `DESIGN.md` §1.1（「最初の dogfood 対象」の
  文）と §6.1 / §8 の `bajutsusample` の例、[`architecture.md`](../../../docs/ja/architecture.md) の「実機 Simulator で
  検証済み」の記述、[`docs/sample-app.md`](../../../docs/sample-app.md) → showcase のページ化、
  `docs/getting-started.md`、`docs/cli.md`、`docs/configuration.md`、`docs/evidence.md` と `docs/README.md` の索引にある
  `sample` 参照、ルートの `README.md` / `README.ja.md` の config 例、ルートの [`Makefile`](../../../Makefile) のヘルプ行、
  そして [`.gitignore`](../../../.gitignore)（3 アプリの `build/` と `*.xcodeproj/` の行を落とす）。最後に、もはや真でなくなった
  showcase の `README` / `SPEC` の「`sample` を置き換える／〜まで残す」但し書きを取り除きます。

### D. 削除するもの（退役）

B／C が入り、オンデバイス経路が showcase で緑になった後に限ります。

- [`demos/app/`](../../../demos/app) — `demo` アプリ（`BajutsuDemo`：Swift 5 ファイル＋ `Info.plist` ＋ `project.yml`）、
  その `scenarios/`（`counter.yaml`、`features.yaml`、`_components/login.yaml`）、README。
- [`demos/features/app/`](../../../demos/features/app) — `sample` アプリ（`BajutsuSample`、Swift 約 15 ファイル）、
  `BajutsuSampleUITests/`、21 個すべての `scenarios/*.yaml` ＋ `baselines/`、`project.yml`、README。そのカバレッジを
  showcase 上で再現できてから（B）。
- [`demos/record/app/`](../../../demos/record/app) — `sample2` アプリ（`BajutsuSample`、`project.yml`、README）。
- 孤立する per-app の config とハーネス：`demos/demo.config.yaml`、`demos/features/demo.config.yaml`、
  `demos/record/demo.config.yaml`、および `demos/tour/`、`demos/record/`、`demos/features/` のトップレベルの
  スクリプト／シナリオのうち showcase の配線が置き換えるもの（向け直す／削除する／`demos/showcase/scenarios/` へ移すの
  どれにするかは段階ごとに決めます。確定しているのは、3 つの**アプリ**ディレクトリが消えることです）。

### E. 段階分け（1 アイテム、PR は分割）

各 PR をレビュー可能に保ち、その間ずっと CI を緑に保つためです。本移行は `.github/` と、ローカルゲートが走らせられない
オンデバイス経路にまたがります。

1. **パリティ。** B を showcase に実装し（UITests／codegen ターゲット、visual 回帰、不足する操作ターゲット、証跡ツアー）、
   showcase の CI ジョブを既存の `sample` ジョブと**並べて**足します。まだ何も削除せず、オンデバイス経路は両方とも緑にします。
2. **切り替え。** C（`tour` / `features` / `webui` / `record`、トップレベルの Makefile／config、ドキュメント）の向き先を
   showcase に変え、CI ジョブを showcase に切り替えて `sample` のジョブを落とします。
3. **退役。** D を実行します。旧来の 3 アプリと config、シナリオ、README を削除し、DESIGN §1.1 と `architecture.md` を
   更新し、showcase の「置き換える」但し書きを取り除きます。

## 検討した代替案

- **first-look のデモのために最小の `demo` / `sample2` を残す。** 却下。目的は iOS フィクスチャを 1 本にすることです。
  最小のオンボーディング → ログイン → カウンタの物語は showcase 上の絞り込んだシナリオとして残るので、保守すべき 2 本目
  （3 本目）のバイナリなしに first-look の分かりやすさを保てます。
- **一括の単一 PR。** 却下。`.github/` とオンデバイスジョブ（ローカルの `make check` ゲートでは走らせられない）にまたがり、
  かつ 3 アプリを一度に削除することになります。段階分けにすれば、各 PR がレビュー可能で、オンデバイス経路を継続的に緑に
  保てます。
- **完全パリティに到達させず、冗長な `sample` シナリオを捨てる。** 却下。完全パリティは、`webui` ツアーと codegen の CI
  ジョブが依存する証跡、操作のカバレッジを保ちます。間引けば、オンデバイスのリグレッション網を密かに弱めてしまいます。
- **本件を BE-0045 に畳み込む。** 却下。BE-0045 は出荷済みで、移行を明示的にスコープ外としており、BE ID は恒久です。
  移行は独立して追跡できる別の作業です。

## 進捗

- [x] showcase 上の codegen → XCUITest と視覚回帰の経路。`UITests` ターゲット（`demos/showcase/swiftui/UITests/`）と VRT のシナリオ・基準画像（[#371](https://github.com/bajutsu-e2e/bajutsu/pull/371)）。
- [x] showcase の実機 CI。`e2e.yml` が sample と並んで showcase の smoke・xcuitest ジョブを実行します（[#371](https://github.com/bajutsu-e2e/bajutsu/pull/371)）。
- [x] 残るパリティ（E.1）。ボタン式のセグメントコントロール（`log.segment.*`）とアプリ内で完結するペーストボードの往復（`sys.*`）を両ツールキットに追加し、証跡の一巡と最初の一見のシナリオ（`controls` / `system` / `network_live` / `evidence` / `relaunch` / `firstlook`）も加えました。いずれも `showcase-swiftui` と `showcase-uikit` の両方に対する `run` で PASS します。ジェスチャーは [#371](https://github.com/bajutsu-e2e/bajutsu/pull/371) で導入済みで、外部クリップボードと background カウンタのミラーは AI なしでは非決定的になるため見送りました（iOS のペースト許可プロンプト、および CI のツールチェーンで `simctl ui home` が有効なステップでないこと）。
- [x] 切り替え（E.2）。デモメニュー（`demos/Makefile`、`demos/demo.config.yaml`）と実機 CI は既に showcase を向いていました。BE-0006 の golden を showcase に移植（`scenarios/golden.yaml` ＋実機録りの `goldens/`）し `idb-monitor.yml` を向け直し、showcase に無かったシナリオエンジンの機能デモ（`data_driven.yaml`、`device.yaml`、FakeDriver の `run_demo.py` / `run_tree_report.py`、オフライン record の `generate_from_nl.py`）を移植しました。
- [x] 退役（E.3）。レガシーの `demos/app/`・`demos/features/app/`・`demos/record/app/` と、孤立した設定・ハーネス・冗長シナリオ（`demos/features/`・`demos/record/` トップレベル）を削除し、`tests/test_sample_fixtures.py` を showcase 版に置き換え、退役を二言語のドキュメント（DESIGN、docs/*、README、CONTRIBUTING、CLAUDE、demos/README）へ反映しました。sample-app ページは `docs/showcase.md` になりました。
- [x] 部分的な「画面・状態へ起動時に直行する近道を持たない」。`SHOWCASE_SEED`（カタログは固定）と deeplink の詳細画面 push（詳細は行タップでのみ到達）を両ツールキットで廃止。`SHOWCASE_TAB` は残しました。idb はネイティブのタブバーをタップできず、タップでのタブ切り替えには XCUITest backend が要るためで、[BE-0107](../BE-0107-showcase-tab-navigation-no-launch-shortcut/BE-0107-showcase-tab-navigation-no-launch-shortcut-ja.md)（showcase の各タブへナビゲーションで到達し `SHOWCASE_TAB` を退役、BE-0019 の成熟が前提）へ先送りしました。

## 参考

- [BE-0045 — Dogfood ショーケースアプリ群](../BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps-ja.md) — 本アイテムが完了させる群（その先送りされた「Migration」ノートを本アイテムが片付けます）
- [BE-0058 — serve Web UI の Dogfood](../BE-0058-dogfood-web-ui/BE-0058-dogfood-web-ui-ja.md) — Web 側の dogfood の対応物
- [BE-0038 — 自律クロール探索](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md) — showcase はその最初の現実的な対象
- [`demos/showcase/SPEC.md`](../../../demos/showcase/SPEC.md) — 画面ごとの契約 · [`demos/README.md`](../../../demos/README.md) — デモのメニュー
- [`.github/workflows/e2e.yml`](../../../.github/workflows/e2e.yml) — 向き先を変えるオンデバイス CI
- [DESIGN §1.1 / §5 / §7.1 / §8](../../../DESIGN.md) — 最初の dogfood 対象、stability ladder、per-target オンボーディング、config · [architecture.md](../../../docs/ja/architecture.md) — 実装状況

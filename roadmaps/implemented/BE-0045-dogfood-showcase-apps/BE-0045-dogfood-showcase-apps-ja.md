[English](BE-0045-dogfood-showcase-apps.md) · **日本語**

# BE-0045 — Dogfood ショーケースアプリ群（UIKit × SwiftUI、アクセシビリティ対比）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0045](BE-0045-dogfood-showcase-apps-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| 実装 PR | [#85](https://github.com/bajutsu-e2e/bajutsu/pull/85) |
| トピック | Dogfood フィクスチャ（デモアプリ） |
| 由来 | Dogfooding |
<!-- /BE-METADATA -->

## はじめに

Bajutsu の主要な dogfood 対象となる、目的特化のフィクスチャ群です。`record`（Tier 1 オーサリング）、
`crawl`（Tier 1 探索、[BE-0038](../../in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)）、
`run`（Tier 2 決定的ゲート）のすべてを、1 つの現実的なアプリに対して実践する土台です。この群は同じアプリを
2 回書き（UIKit と SwiftUI）、各々をさらにアクセシビリティの 2 変種（識別子の有/無）で出すので、2 つの
コードベースから 4 つのインストール可能なプロダクトになります。画面ごとの完全な契約は
[`demos/showcase/SPEC.md`](../../../demos/showcase/SPEC.md)（[ja](../../../demos/showcase/SPEC.ja.md)）
にあります。本項目は根拠とスコープを記録します。

これは旧来の単一アプリ `sample` フィクスチャ（[`demos/features/app`](../../../demos/features/app)）を
置き換えます。`sample` は 1 つの SwiftUI アプリで機能面を示しますが、ツールキット軸（UIKit と SwiftUI の
要素ツリーの差）も、アクセシビリティ軸（識別子が無いとき `record` と `doctor` が何をするか）も示せません。
それらこそ dogfood フィクスチャが負荷をかけるべき次元です。

## 動機

**1. すべてのコマンドを一度に行使できるほど豊かなフィクスチャ。** 現状は 3 つのアプリ（`demo`、`sample`、
`sample2`）に分散しています。showcase は操作面のすべてを 1 つの一貫したアプリに収めます。5 つのタブ、
ナビゲーションスタックの push、4 つのモーダル様式（detent 付き sheet、フルスクリーンカバー、アクション
シート、一過性トースト）、テキスト入力、非同期ロード、通信（実通信と BajutsuKit によるモック可能）、
そして意図的に OS レベルのアラートを上げるタブです。本当に枝分かれの多いアプリ（5 タブ × push × 4 モーダル
様式）であることが、`crawl` の幅優先探索を意味あるものにします。マップすべき本物のグラフがあるからです。

**2. アクセシビリティの対比は、単なるフィクスチャではなく対照実験。** セレクタの安定性こそが決定性の
レバーです（[DESIGN §2](../../../DESIGN.md)）。id ベースのセレクタは一意に解決し、レイアウトやロケールの
変化に耐えますが、座標や label へのフォールバックは脆いものです（[DESIGN §5](../../../DESIGN.md) stability ladder）。
`-a11y` ↔ `-noax` の双子は、その抽象的な主張を具体的で測定可能なものにします。同じアプリ、同じフロー、違うのは
識別子だけです。

- **`-a11y`** ビルドに対して、`run` は全シナリオを決定的に再実行し、`doctor --app` は **Ready** を出します。
- **`-noax`** ビルド（識別子が一切ない）に対して、`record` は stability ladder を *下って*
  `label`/`traits`/座標へ向かわざるを得ず、`doctor --app` は **Blocked**（`idCoverage` ≈ 0）を出します。
  `-noax` アプリは `idNamespaces: []` を宣言します（何も露出しないという正直な表明であり、「通ったように
  見える」ことを防ぎます）。

同じ自然言語ゴールを `record` で両方の双子に通せば、その差分こそがアクセシビリティ作業の価値です。
単一変種のフィクスチャには出せないデモです。

**3. ツールキット軸が要素ツリーの差を早期に捕まえる。** idb の要素ツリー正規化は既知のリスク領域で、特に
SwiftUI 標準コントロールで顕著です（[DESIGN §11](../../../DESIGN.md)；
[BE-0006](../BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization-ja.md)）。
SwiftUI の双子と *同じ* 識別子契約を露出する UIKit の双子があれば、*同じ* シナリオ集を両方で走らせ、2 つの
ツールキットの a11y ツリーがどこで食い違うかを表面化できます。フィクスチャが存在するだけで得られる、
ドライバの回帰ネットです。

**4. 将来機能を実践する安定した土台。** アプリ固有の差分はすべて config に寄る（[DESIGN §8](../../../DESIGN.md)）
ので、showcase は新機能を試す自然な対象です。毎回使い捨てアプリを作らずに、視覚回帰のベースライン
（[BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions-ja.md)）、
データ駆動実行、秘匿情報のマスキング、crawl の画面マップ、`doctor` の全画面カバレッジ
（[BE-0024](../../proposals/BE-0024-doctor-onboarding/BE-0024-doctor-onboarding-ja.md)）が、すぐに使える代表的な被験体を
持てます。

これはすべてのプライム・ディレクティブ（[CLAUDE.md](../../../CLAUDE.md)）を尊重します。純粋にテスト
*対象* と config とシナリオであり、どのゲートにも LLM 呼び出しを **加えず**、ツールもドライバも実行系も
**何も** 変えません。群全体は [DESIGN §7.1](../../../DESIGN.md) の通り `apps.<name>` エントリで
オンボーディングされます。

## 詳細設計

画面ごとの正式な契約は [`demos/showcase/SPEC.md`](../../../demos/showcase/SPEC.md)
（[ja](../../../demos/showcase/SPEC.ja.md)）にあります。識別子マップ、launch-env フック、deeplink、OS アラート
配置、`ACCESSIBLE` ビルドフラグです。形は以下の通り。

- **配置**：`demos/showcase/{swiftui,uikit}/` に 2 つのコードベース（xcodegen `project.yml`、**1 つの
  `Sources/` を共有する 2 ターゲット**。変種差は a11y ターゲットの `SWIFT_ACTIVE_COMPILATION_CONDITIONS =
  ACCESSIBLE` ただ 1 つ）。共有資産を並置します。`showcase.config.yaml`（4 つの `apps.<name>` エントリ）、
  `scenarios/`（id ベースの `run` シナリオ）、`record/`（`-noax` デモ用の自然言語ゴール）、`Makefile` です。
- **1 つの識別子契約、4 プロダクト。** 2 つの `-a11y` アプリは識別子/launch フック/deeplink を完全同一
  （byte-for-byte）に露出するので、`scenarios/*.yaml` がどちらのツールキットにも無改変で通ります。
  `accessibilityID(...)` ヘルパ（SwiftUI では `View` 拡張、UIKit では `UIAccessibilityIdentification` 拡張）は
  識別子がツリーに入る *唯一* の場所で、`#if ACCESSIBLE` でゲートされます。したがって `-noax` ビルドは識別子も
  ミラー状態値もないツリーにコンパイルされます。
- **意図的で限定的な OS アラート**（[SPEC §7](../../../demos/showcase/SPEC.md)）：起動時の許可プロンプト
  なし。通知と位置情報のプロンプトは **Permissions タブ**でのみ現れ、そこで run の vision
  alert guard / `dismissAlerts` の典型フィクスチャになります（showcase 自身の
  [`permission.yaml`](../../../demos/showcase/scenarios/permission.yaml) シナリオ）。
- **通信** は `sample` アプリの BajutsuKit 連携をそのまま再利用するので、アプリ側無改変で `network` 証跡と
  `mocks` が効きます（[DESIGN §3.2](../../../DESIGN.md)）。

### スコープと段階

- **今回のスコープ：** 4 つのアプリ、config、id ベースの `run` シナリオ、`record` ゴール集、デモ配線
  （`Makefile`、README）。`run` と `doctor` は今日このフィクスチャに対して動きます。`record` は `-noax` アプリに
  対して今日動きます。
- **先行き：** `crawl` 自体が提案
  （[BE-0038](../../in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)）で未実装です。
  showcase はその最初の本格的な対象になるよう作られる（seed config と期待画面マップのメモを `crawl/` の
  テストデータとして同梱）が、crawl デモは BE-0038 が入った時点で有効になります。
- **移行：** 旧 `sample` フィクスチャは、showcase がその実機 CI と Web UI ツアーをカバーするまで残します。置換は
  フォローアップであり本項目の一部ではありません。

## 検討した代替案

- **新群ではなく既存 `sample` を拡張。** 却下します。`sample` は単一 SwiftUI アプリで、要点は *ツールキット* 軸と
  *アクセシビリティ* 軸そのものです。`sample` に UIKit ターゲットと no-a11y 変種を継ぎ足すと、既存の実機 CI と
  Web UI ツアーを遥かに大きな面と絡めてしまいます。きれいな `demos/showcase/` は移行を漸進的に保ちます。
- **4 つの独立コードベース（共有ソースなし）。** 保守コストで却下します。「同じアプリ」の手書き 4 コピーはドリフト
  し、a11y と no-a11y の双子のドリフトは対照実験の前提を静かに壊します。ツールキットごとに 1 コードベースと
  コンパイル時フラグなら、「同じアプリ、違うのは識別子だけ」を *構成上* 真に保てます。
- **識別子を実行時トグルする単一統合アプリ。** 却下します。実行時フラグは識別子をバイナリに同梱するので
  `doctor`/`record` は本当に識別子のないアプリを見ないし、「アクセシビリティを省いたチームが出荷する
  アプリ」が何を意味するかを曖昧にします。ビルド時条件は正直に識別子のないプロダクトを生みます。
- **UIKit を省き SwiftUI のみ。** 却下します。UIKit は依然としてインストールベースが大きく、ツールキット軸こそ
  idb の要素ツリー正規化差が表面化する場所です
  （[BE-0006](../BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization-ja.md)）。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

- [`demos/showcase/SPEC.md`](../../../demos/showcase/SPEC.md)（[ja](../../../demos/showcase/SPEC.ja.md)）— 画面ごとの契約
- [DESIGN §2 / §5 / §7.1 / §7.3 / §8 / §11](../../../DESIGN.md) — 決定性、stability ladder、per-app オンボーディング、識別子命名、config、リスク
- [BE-0038](../../in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md) — 自律 crawl 探索（本フィクスチャの先行き対象）
- [BE-0024](../../proposals/BE-0024-doctor-onboarding/BE-0024-doctor-onboarding-ja.md) — doctor / onboarding（本フィクスチャのカバレッジを消費）
- [BE-0006](../BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization-ja.md) — idb 要素ツリー正規化（ツールキット軸が負荷をかける）
- [`demos/features/app`](../../../demos/features/app) — 本項目が置き換える `sample` フィクスチャ

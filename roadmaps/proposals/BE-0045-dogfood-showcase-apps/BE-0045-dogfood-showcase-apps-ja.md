[English](BE-0045-dogfood-showcase-apps.md) · **日本語**

# BE-0045 — Dogfood ショーケースアプリ群（UIKit × SwiftUI、アクセシビリティ対比）

* Proposal: [BE-0045](BE-0045-dogfood-showcase-apps-ja.md)
* Status: **Proposal**
* Track: [Proposals](../../README-ja.md#proposals)
* Topic: Dogfood フィクスチャ（デモアプリ）
* Origin: Dogfooding

## Introduction

Bajutsu の主要な dogfood 対象となる、目的特化のフィクスチャ群です。`record`（Tier 1 オーサリング）、
`crawl`（Tier 1 探索・[BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)）、
`run`（Tier 2 決定的ゲート）のすべてを、1 つの現実的なアプリに対して実践する土台です。この群は
**同じアプリを 2 回書き**（UIKit と SwiftUI）、**さらに各々をアクセシビリティの 2 変種**（識別子の
有/無）で出すので、2 コードベースから 4 つのインストール可能なプロダクトになります。画面ごとの完全な
契約は [`demos/showcase/SPEC.md`](../../../demos/showcase/SPEC.md)（[ja](../../../demos/showcase/SPEC.ja.md)）
にあります。本項目は根拠とスコープを記録します。

これは旧来の単一アプリ `sample` フィクスチャ（[`demos/features/app`](../../../demos/features/app)）を
置き換えます。`sample` は 1 つの SwiftUI アプリで機能面を示しますが、ツールキット軸（UIKit と SwiftUI の
要素ツリーの差）も、アクセシビリティ軸（識別子が無いとき `record` と `doctor` が何をするか）も示せません。
それらこそ dogfood フィクスチャが負荷をかけるべき次元です。

## Motivation

**1. すべてのコマンドを一度に行使できるほど豊かなフィクスチャ。** 現状は 3 アプリ（`demo`、`sample`、
`sample2`）に分散しています。showcase は操作面のすべて——4 タブ、ナビゲーションスタックの push、4 つの
モーダル様式（detent 付き sheet、フルスクリーンカバー、アクションシート、一過性トースト）、テキスト入力、
非同期ロード、通信（実通信＋BajutsuKit によるモック可能）、そして意図的に OS レベルのアラートを上げる
画面——を 1 つの一貫したアプリに収めます。本当に枝分かれの多いアプリ（4 タブ × push × 4 モーダル様式）で
あることが、`crawl` の幅優先探索を意味あるものにします。マップすべき本物のグラフがあるからです。

**2. アクセシビリティの対比は、単なるフィクスチャではなく対照実験。** セレクタの安定性こそが決定性の
レバーです（[DESIGN §2](../../../DESIGN.md)）。id ベースのセレクタは一意に解決し、レイアウト/ロケール変化に
耐えます。一方、座標/label へのフォールバックは脆い（[DESIGN §5](../../../DESIGN.md) stability ladder）。
`-a11y` ↔ `-noax` の双子は、その抽象的な主張を具体的・測定可能にします——同じアプリ、同じフロー、識別子
だけが違います。

- **`-a11y`** ビルドに対して、`run` は全シナリオを決定的に再実行し、`doctor --app` は **Ready** を出す。
- **`-noax`** ビルド（識別子が一切ない）に対して、`record` は stability ladder を *下って*
  `label`/`traits`/座標へ向かわざるを得ず、`doctor --app` は **Blocked**（`idCoverage` ≈ 0）を出す。
  `-noax` アプリは `idNamespaces: []` を宣言する——何も露出しないという正直な表明であり、「通ったように
  見える」ことを防ぐ。

同じ自然言語ゴールを `record` で両方の双子に通せば、その差分こそがアクセシビリティ作業の価値です——
単一変種のフィクスチャには出せないデモです。

**3. ツールキット軸が要素ツリーの差を早期に捕まえる。** idb の要素ツリー正規化は既知のリスク領域で、特に
SwiftUI 標準コントロールで顕著です（[DESIGN §11](../../../DESIGN.md)；
[BE-0006](../BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization-ja.md)）。
SwiftUI の双子と *同じ* 識別子契約を露出する UIKit の双子があれば、*同じ* シナリオ集を両方で走らせ、2 つの
ツールキットの a11y ツリーがどこで食い違うかを表面化できます——フィクスチャが存在するだけで得られる、
ドライバの回帰ネットです。

**4. 将来機能を実践する安定した土台。** アプリ固有の差分はすべて config に寄る（[DESIGN §8](../../../DESIGN.md)）
ので、showcase は新機能を試す自然な対象です。毎回使い捨てアプリを作らずに、視覚回帰のベースライン
（[BE-0029](../../implemented/BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions-ja.md)）、
データ駆動実行、秘匿情報のマスキング、crawl の画面マップ、`doctor` の全画面カバレッジ
（[BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding-ja.md)）が、すぐに使える代表的な被験体を
持てます。

これはすべてのプライム・ディレクティブ（[CLAUDE.md](../../../CLAUDE.md)）を尊重します。純粋にテスト
*対象* ＋ config ＋ シナリオであり、どのゲートにも LLM 呼び出しを **加えず**、ツール・ドライバ・実行系を
**何も** 変えません。群全体は [DESIGN §7.1](../../../DESIGN.md) の通り `apps.<name>` エントリで
オンボーディングされます。

## Detailed design

画面ごとの正式な契約——識別子マップ、launch-env フック、deeplink、OS アラート配置、`ACCESSIBLE`
ビルドフラグ——は [`demos/showcase/SPEC.md`](../../../demos/showcase/SPEC.md)（[ja](../../../demos/showcase/SPEC.ja.md)）
です。形は以下の通り。

- **配置** — `demos/showcase/{swiftui,uikit}/` に 2 つのコードベース（xcodegen `project.yml`、**1 つの
  `Sources/` を共有する 2 ターゲット**。変種差は a11y ターゲットの `SWIFT_ACTIVE_COMPILATION_CONDITIONS =
  ACCESSIBLE` ただ 1 つ）。共有資産——`showcase.config.yaml`（4 つの `apps.<name>` エントリ）、`scenarios/`
  （id ベースの `run` シナリオ）、`record/`（`-noax` デモ用の自然言語ゴール）、`Makefile`——を並置。
- **1 つの識別子契約、4 プロダクト。** 2 つの `-a11y` アプリは識別子/launch フック/deeplink を完全同一
  （byte-for-byte）に露出するので、`scenarios/*.yaml` がどちらのツールキットにも無改変で通る。`aid(...)`
  ヘルパ（SwiftUI では `View` 拡張、UIKit では `UIAccessibilityIdentification` 拡張）は識別子がツリーに
  入る *唯一* の場所で、`#if ACCESSIBLE` でゲートされる。したがって `-noax` ビルドは識別子もミラー状態値も
  ないツリーにコンパイルされる。
- **意図的で限定的な OS アラート**（[SPEC §7](../../../demos/showcase/SPEC.md)）— 起動時の許可プロンプト
  なし、AutoFill の「パスワードを保存しますか？」シートなし（ログインのセキュアフィールドは
  `textContentType` を省く）。通知と位置情報のプロンプトは Permissions 画面でのみ現れ、そこで run の vision
  alert guard / `dismissAlerts` の典型フィクスチャになる（既存の
  [`permission.yaml`](../../../demos/features/app/scenarios/permission.yaml) の前例を一般化）。
- **通信** は `sample` アプリの BajutsuKit 連携をそのまま再利用するので、アプリ側無改変で `network` 証跡と
  `mocks` が効く（[DESIGN §3.2](../../../DESIGN.md)）。

### スコープと段階

- **今回のスコープ：** 4 アプリ、config、id ベースの `run` シナリオ、`record` ゴール集、デモ配線
  （`Makefile`、README）。`run` と `doctor` は今日このフィクスチャに対して動く。`record` は `-noax` アプリに
  対して今日動く。
- **先行き：** `crawl` 自体が提案
  （[BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)）で未実装。
  showcase はその最初の本格的な対象になるよう作られる（seed config と期待画面マップのメモを `crawl/` の
  テストデータとして同梱）が、crawl デモは BE-0038 が入った時点で有効になる。
- **移行：** 旧 `sample` フィクスチャは、showcase がその実機 CI と Web UI ツアーをカバーするまで残す。置換は
  フォローアップであり本項目の一部ではない。

## Alternatives considered

- **新群ではなく既存 `sample` を拡張。** 却下：`sample` は単一 SwiftUI アプリで、要点は *ツールキット* 軸と
  *アクセシビリティ* 軸そのもの。`sample` に UIKit ターゲットと no-a11y 変種を継ぎ足すと、既存の実機 CI と
  Web UI ツアーを遥かに大きな面と絡める。きれいな `demos/showcase/` は移行を漸進的に保つ。
- **4 つの独立コードベース（共有ソースなし）。** 保守コストで却下：「同じアプリ」の手書き 4 コピーはドリフト
  し、a11y と no-a11y の双子のドリフトは対照実験の前提を静かに壊す。ツールキットごとに 1 コードベース＋
  コンパイル時フラグなら、「同じアプリ、識別子だけが違う」を *構成上* 真に保てる。
- **識別子を実行時トグルする単一統合アプリ。** 却下：実行時フラグは識別子をバイナリに同梱する（ので
  `doctor`/`record` は本当に識別子のないアプリを見ない）し、「アクセシビリティを省いたチームが出荷する
  アプリ」が何を意味するかを曖昧にする。ビルド時条件は正直に識別子のないプロダクトを生む。
- **UIKit を省き SwiftUI のみ。** 却下：UIKit は依然としてインストールベースが大きく、ツールキット軸こそ
  idb の要素ツリー正規化差が表面化する場所
  （[BE-0006](../BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization-ja.md)）。

## References

- [`demos/showcase/SPEC.md`](../../../demos/showcase/SPEC.md)（[ja](../../../demos/showcase/SPEC.ja.md)）— 画面ごとの契約
- [DESIGN §2 / §5 / §7.1 / §7.3 / §8 / §11](../../../DESIGN.md) — 決定性、stability ladder、per-app オンボーディング、識別子命名、config、リスク
- [BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md) — 自律 crawl 探索（本フィクスチャの先行き対象）
- [BE-0024](../BE-0024-doctor-onboarding/BE-0024-doctor-onboarding-ja.md) — doctor / onboarding（本フィクスチャのカバレッジを消費）
- [BE-0006](../BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization-ja.md) — idb 要素ツリー正規化（ツールキット軸が負荷をかける）
- [`demos/features/app`](../../../demos/features/app) — 本項目が置き換える `sample` フィクスチャ

[English](BE-0111-ai-sdk-optional-dependency.md) · **日本語**

# BE-0111 — AI SDK を extra へ降ろし、決定的ゲートを AI 非依存でインストールできるようにする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0111](BE-0111-ai-sdk-optional-dependency-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0111") |
| 実装 PR | [#NNN](https://github.com/bajutsu-e2e/bajutsu/pull/NNN) |
| トピック | AI provider configuration |
| 関連 | [BE-0047](../../implemented/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md), [BE-0053](../../implemented/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider-ja.md), [BE-0101](../../implemented/BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config-ja.md), [BE-0104](../../proposals/BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`anthropic` SDK を基本依存（base `dependencies`）から `ai` extra へ移し、既定のインストールが AI
SDK を一切含まないようにします。既定のインストールは、決定的な `run` や CI ゲートを回すために使う
ものです。決定的な経路はモデルを一度も呼びませんが、`anthropic` はいま 5 つの基本依存の先頭に置かれ
ており、AI 機能を使うかどうかに関わらず、どのインストールも SDK とその推移的依存を引き込みます。本
項目は、実行時の Claude 利用と Claude 非利用の経路をすでに分離した
[BE-0101](../../implemented/BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config-ja.md) に対する、
**パッケージング側**の対応です。

## 動機

`pyproject.toml` は 5 つの基本依存を宣言しており、その先頭が `anthropic` です。

```toml
dependencies = [
    "anthropic>=0.50",
    "jinja2>=3.1",
    "pydantic>=2.7",
    "pyyaml>=6.0",
    "typer>=0.12",
]
```

ほかの任意サブシステムは、すでにすべて extra の背後に置かれています。`idb`、`web`、`visual`、`mcp`、
`bedrock`、`worker`、`server`、`db`、`oauth`、`schema`、`docs` がそうです。基本依存に乗ったままなのは
AI SDK だけで、これは規律の抜けです。抜けには具体的な代償が 2 つあります。第一に供給網の問題です。
決定的なインストールが、`run` や CI ゲートの一度も使わない SDK とその推移的依存を運び込み、機能上の
利得なく攻撃面と監査面を広げます。第二に、実行時がすでに守っている約束と食い違います。BE-0101 は
既定の経路を実行時に Claude 非依存にしましたが、SDK を基本インストールに残したままでは、**コード**が
もう AI を必須としていないのに、**パッケージ**は依然「AI は必須」と言っている状態になります。

論点は狭く、界面の再設計とは切り離せます。BE-0104（vendor-neutral AI backend）は AI 経路が話す
**モデルの系統**を抽象化しますが、本項目は **SDK をどこで宣言するか**を移すだけです。基本パッケージ
は AI 非依存になり、AI を使う著者支援や調査の機能は、`ai` extra を通じて SDK 依存を明示的に宣言する
ようになります。2 つの項目は組み合わせて成立し（BE-0104 が入れば `anthropic` はパッケージ全体では
なく 1 つのアダプタの依存になります）、どちらも相手を待つ必要はありません。基本インストールの掃除は、
それ自体で行う価値があります。

## 詳細設計

作業は次の 5 つに MECE に分解できます。

### 1. `ai` extra を導入し、基本依存から `anthropic` を外す

`[project].dependencies` から `anthropic>=0.50` を取り除き、`[project.optional-dependencies]` に
`ai = ["anthropic>=0.50"]` を追加します。基本パッケージの依存は `jinja2`、`pydantic`、`pyyaml`、
`typer` の 4 つだけになり、いずれもモデルには到達しません。

### 2. `bedrock` extra を `ai` の上に再構成する

`bedrock` は現在 `["anthropic[bedrock]>=0.50"]` です。バージョンの宣言元を 1 か所に保つため、
`bedrock` は `ai` extra が宣言するのと同じ `anthropic` のピンに Bedrock 変種を重ねる形で表します
（たとえば `bedrock = ["bajutsu[ai]", "anthropic[bedrock]>=0.50"]` とするか、`bedrock` が `ai` を
拡張する形にします）。こうすればバージョンは 1 度だけ宣言され、`ai` と `bedrock` が食い違うことは
ありません。

### 3. 「既定の経路で `anthropic` を import しない」を import guard で固定する

既定の経路は、基本（AI 非依存）インストール上できれいに import できなければなりません。既存の
import guard テスト（`tests/serve/test_import_guard.py` のパターン）を拡張し、`bajutsu` を import
して決定的な `run` の経路をたどっても `anthropic` を import しないことを検査します。これにより、
AI 非依存の基本インストールは、いまの import 順序の偶然ではなく、テストで保証された性質になります。

### 4. ゲートの AI 経路のテスト網は dev group で維持する

AI のモジュールはコードベースの一部であり続け、回帰の網が要ります。そのため `dev` の依存グループは
AI extra を引き続きインストールします（`bajutsu[bedrock,server,worker,db,oauth,mcp,visual,schema]`
の列に `ai` を加えます）。したがって `make check` は引き続き AI 経路を import してテストします。新たに
保証するのは、**基本**インストールが AI 非依存であることであって、AI のテスト網を落とすことでは
ありません。`ai` も `bedrock` も入れない基本インストールが、環境に `anthropic` が存在しない状態で
`bajutsu` を import でき、決定的なサブセットを走らせられることを、明示的な検査（CI かテスト）で
確かめます。

### 5. インストールの分岐を文書化する

インストール手順（README と `docs/`、およびその `docs/ja/` の対訳）を更新し、2 つの読み手を明示
します。決定的な著述や実行には `pip install bajutsu`、AI を使う `record` や `triage`、クロールの
案内、アラート消去の各経路には `pip install bajutsu[ai]`（または `[bedrock]`）です。

### 機械的に検査できる成果

基本パッケージ（`ai` も `bedrock` も付けない）から作った仮想環境が、`anthropic` を入れないまま
`bajutsu` を import でき、決定的なテストを走らせられること。そして、既定の経路のモジュールが
`anthropic` を import した場合に import guard テストが落ちること。いずれの検査も静的で決定的であり、
確認のどこにも LLM は関与しません。

### プライムディレクティブとの整合

本項目はディレクティブ 1 を補強します。決定的ゲートは実行時にモデルを呼ばず（BE-0101）、基本
インストールでも SDK を持たなくなります。LLM の呼び出しはどこにも追加しません。AI は Tier-1 の
著述と調査の経路にとどまり、明示的な extra の背後に置かれます。決定性とアプリ非依存の挙動には手を
付けません。パッケージングだけの変更です。

## 検討した代替案

- **`anthropic` を基本依存に残す（現状）。** 却下します。使われない SDK とその推移的依存をあらゆる
  決定的インストールに運び込み、しかも extra の背後に置かれていない唯一の任意サブシステムという、
  プロジェクト自身の extra 規律との不整合を残します。
- **BE-0104 に取り込む。** これを浮かび上がらせたレビューは、BE-0104 への増補を薦めていました。あえて
  独立した項目にします。BE-0104 は**プロバイダ界面**の再設計（すべての AI 呼び出し箇所の、挙動を
  変えないリファクタリング）であるのに対し、本項目は独立して入れられ、中立な界面が存在する前より
  先に AI 非依存の基本インストールを届けられる、自己完結したパッケージングの一手です。両者は関連
  （`関連`）で結び、きれいに組み合わさります。
- **ゲートを AI 完全非インストールにし、AI 経路のテストを落とす。** 却下します。AI のモジュールは
  コードベースの一部であり、回帰の網を保たねばなりません。`dev` グループは `ai` extra を保ち、
  `make check` は引き続きそれらをテストします。保証の範囲は**基本**インストールに限り、テスト網には
  及ぼしません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `ai` extra を導入し、基本依存から `anthropic` を外す
- [x] `bedrock` extra を `ai` の上に再構成する（バージョン宣言元を 1 か所に）
- [x] import guard で「既定の経路で `anthropic` を import しない」を固定する
- [x] AI 経路のテスト網を `dev` グループで維持し、基本インストールが AI 非依存で決定的サブセットを走らせられることを検査する
- [x] `pip install bajutsu` と `bajutsu[ai]` の分岐を文書化する（両言語）

- [#NNN](https://github.com/bajutsu-e2e/bajutsu/pull/NNN)：`anthropic` を `ai` extra へ移し（基本
  インストールを AI-free に）、`bedrock` をその上に再構成し、`dev` グループに `ai` を追加。import
  guard に「既定の経路で `anthropic` を import しない」検査と、基本インストール（`anthropic` 不在）を
  模したシミュレーションを追加し、`bajutsu` / `bajutsu[ai]` のインストール分岐を両言語で文書化しました。

## 参考

`pyproject.toml`（`[project].dependencies`、`[project.optional-dependencies]`、
`[dependency-groups].dev`。本項目が編集する基本依存、extra 群、ゲートのインストール一覧）、
`tests/serve/test_import_guard.py`（本項目が `anthropic` へ拡張する import guard のパターン）、
`bajutsu/anthropic_client.py` と AI 呼び出し箇所（`bajutsu/claude_agent.py`、
`bajutsu/claude_triage.py`、`bajutsu/alerts.py`、`bajutsu/claude_enrich_agent.py`、
`bajutsu/crawl_guide.py`、`bajutsu/crawl_tabs.py`。`ai` extra を要するコード）、
[BE-0101](../../implemented/BE-0101-ai-free-zero-config/BE-0101-ai-free-zero-config-ja.md)（本項目が
パッケージング層で仕上げる、実行時の Claude 利用と非利用の分離）、
[BE-0104](../../proposals/BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md)（本項目が
組み合わさる中立な界面）、
[BE-0047](../../implemented/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md) と
[BE-0053](../../implemented/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider-ja.md)（本項目が
再構成する `bedrock` extra に関わる、プロバイダと Bedrock の設定）。

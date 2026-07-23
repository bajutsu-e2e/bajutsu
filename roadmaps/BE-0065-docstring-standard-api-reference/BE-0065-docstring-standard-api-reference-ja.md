[English](BE-0065-docstring-standard-api-reference.md) · **日本語**

# BE-0065 — docstring の規範と API リファレンス生成

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0065](BE-0065-docstring-standard-api-reference-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0065") |
| 実装 PR | [#232](https://github.com/bajutsu-e2e/bajutsu/pull/232) |
| トピック | コントリビューターワークフロー |
<!-- /BE-METADATA -->

## はじめに

Python コアの **docstring（ドキュメンテーションコメント）の規範**を一つ明文化し、それを基に **API
リファレンスを生成**します。このリポジトリを読み書きする人と AI エージェントが、すべてのファイルを
開かなくても公開 API の全体像をつかめるようにすることが目的です。公開 API は **Google 形式**の
docstring で書き、生成したリファレンスとして公開します。内部のヘルパーは、このコードベースが既に
好んでいる簡潔な散文の docstring のままにします。型は散文に書き直しません。型はシグネチャの注釈にあり、
生成ツールがそこから読み取ります。規範は公開 API の範囲で `ruff` が強制し、静的なドキュメント生成ツール
（推奨は MkDocs + `mkdocstrings`）が描画します。どちらも決定的な `run` / CI ゲートには触れません。

## 動機

Python コアは既によくコメントされています。module docstring は *なぜ* を説明し、型注釈は揃っており
（`mypy` は strict、`ruff` の `ANN` 規則も有効）、挙動は BE 項目に相互参照されています。しかし、二つ
欠けているものがあります。

一つ目は、**規範が暗黙であること**です。`ruff` は `D`（pydocstyle）規則を選択しておらず、コードベースは
構造化セクションのない散文の docstring を使っています（`Args:` / `Returns:` / `:param:` は現状どこにも
ありません）。これは意図的で良いスタイルですが、どこにも書かれていないため、公開 API が増えるにつれて
揺らぎます。`Driver` プロトコル、CLI、MCP ツール、シナリオスキーマと、対象は広がっています。

二つ目は、**生成されたリファレンスが無いこと**です。`docs/` の手書きドキュメントは概念とワークフローを
説明しますが、公開 API そのものを描画するものはありません。`Driver` の形、selector の型、MCP ツールの
一覧を知りたい読者は、ソースを開くしかありません。

この二つの欠落が、今いっそう効いてきます。**エージェントによるコーディングが、このコードを読み書きする
主要な手段になっている**からです。一貫した docstring の規範と、たどれる API の面は、AI に頼る
コントリビュータと、その AI 自身の双方を助けます。公開引数の意味を探す場所が一定になり、100 を超える
ファイルではなく、描画された一枚の面で全体を見渡せます（リポジトリにアクセスできるエージェントは依然と
してソースを読みます。規範のより大きな効果は形式そのものではなく、一貫性と描画された面にあります。
「検討した代替案」を参照）。

本項目はドキュメントとツールだけの変更です。どこにも LLM を足さず、`run` の中で動くことはなく、
リファレンスの生成はゲートの外に置きます。prime directive の 1 と 2（[CLAUDE.md](../../CLAUDE.md)）は
構造上保たれます。

## 詳細設計

### 範囲：公開 API の面

構造化した（Google 形式の）docstring は、**公開 API の面**に適用します。外部の呼び出し側とエージェントが
触れるものです。

- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) の `Driver` プロトコルと共有の型
  （`Selector`、`Element` など）
- CLI コマンド（[`bajutsu/cli/`](../../bajutsu/cli/)）
- MCP のツールとリソース（[`bajutsu/mcp/`](../../bajutsu/mcp/)）
- シナリオスキーマ（ハブとなる成果物）
- runner、`assertions`、`network` の公開関数

内部の module-private なヘルパー（`_` で始まる関数）は、**簡潔な散文の docstring** のままにします。存在
理由を述べる、目的のある一行で十分です。小さなヘルパーに `Args:` ブロックを強いるのは、このリポジトリが
避ける *what* のナレーションです。生成したリファレンスからは private を除外します。

### 規範

- **言語は英語**。すべてのコードコメントと同じです（散文ドキュメントは日英の両方ですが、コードは英語
  だけです。[`docs/ja/README.md`](../../docs/ja/README.md)）。
- **公開 API は Google 形式**。一行の要約のあと、`Args:` / `Returns:` / `Raises:`（および `Yields:` /
  `Examples:`）を、**情報が増えるときだけ**置きます。
- **型を書き直さない**。型は注釈にあり、生成ツールがシグネチャから読み取ります。`Args:` / `Returns:`
  は *意味* を書きます。単位、制約、`None` が何を表すかであって、型ではありません。
- **what ではなく why**。理由、不変条件（とりわけ決定性を守るもの）、トレードオフ、境界条件。挙動の
  理由は、その `BE-NNNN` 項目に結びつけます。
- **周囲の密度に合わせる**。短く、目的を持って。ナレーションは足しません。
- **フィールドごとの作法を保つ**。`TypedDict` や定数を持つクラスでは、フィールドごとの行末コメントが、
  各フィールドの *why* を散文より良く表します。これは残します。

### 具体例

公開関数の before（現在の散文）と after（規範）です。型は繰り返さず、`Args:` / `Returns:` は意味を書き、
決定性の不変条件を先頭に置き、理由を BE 項目に結びつけます。

```python
# before (today's prose) — bajutsu/drivers/base.py
def resolve_unique(elements: list[Element], sel: Selector) -> Element:
    """Resolve to exactly one element for a single action.

    - 0 matches -> ElementNotFound
    - 2+ matches -> AmbiguousSelector (rules out "tap whatever matched first")
    - only with `index` do we pick the nth of multiple candidates (last resort)
    """

# after (Google style on the public surface)
def resolve_unique(elements: list[Element], sel: Selector) -> Element:
    """Resolve a selector to exactly one element for a single action.

    A single action requires a unique match, so an ambiguous selector fails
    rather than acting on "whatever matched first" (the determinism core, BE-0001).

    Args:
        elements: One `query()` snapshot of the on-screen elements.
        sel: The selector to resolve. `index` is honored only as a last resort,
            picking the nth of several candidates.

    Returns:
        The one element the selector resolves to.

    Raises:
        ElementNotFound: Nothing matched, or `index` is out of range.
        AmbiguousSelector: Two or more matched and no `index` disambiguates.
    """
```

内部ヘルパーは散文のままにします。*why* を述べる一行で、`Args:` ブロックは置きません。

```python
def _contains(outer: Frame, inner: Frame) -> bool:
    """Whether `inner`'s frame sits inside `outer`'s (edges inclusive)."""
```

`TypedDict` や定数を持つクラスは、フィールドごとの行末コメントを残します。各フィールドの *why* を、散文の
`Args:` 風ブロックより良く表します。

```python
class Selector(TypedDict, total=False):
    """How to address an element. Provided fields are combined with AND."""

    id: str      # exact accessibilityIdentifier (first choice)
    index: int   # nth of multiple matches (last resort; flaky)
```

### 生成

推奨する構成は **MkDocs + Material + `mkdocstrings[python]`** です。

- **Markdown ネイティブ**なので、API ページは既存の日英 `docs/` と同じサイトに同居できます。
- `mkdocstrings` はシグネチャを **`griffe` で読みますが、`griffe` は静的に解析**し、モジュールを import
  しません。コアが土台にしている遅延かつ任意の import（`playwright`、`fb-idb`、`fastapi`、`redis`、
  `boto3`）を、`Sphinx autodoc` ならモックする必要があるところ、回避できます。
- 型付きのシグネチャは注釈から自動で出るので、docstring が型を書き直すことはありません。

代替は `Sphinx + autodoc + napoleon`（Markdown 用に `myst-parser`）です。同じ Google 形式の docstring を
解釈しますが、ここでは重く、任意の import をモックする必要があります。

### パッケージング、強制、ホスティング

- `pyproject.toml` に新しい `docs` の **任意依存グループ**（生成ツールとプラグイン）を、他の extra と
  同じく隔離して置きます。
- `make docs` / `make docs-serve` ターゲットを設け、**コアのゲートからは外します**。実機 E2E と同様、
  リファレンスの生成は別の重い経路であり、`make check` を遅くしてはいけません。
- **強制**：`ruff` の `D` 規則を `convention = "google"` で有効にし、`per-file-ignores` で**公開モジュール
  に限定**します（tests / demos / scripts は既に `ANN` / `T20` を ignore 済み）。内部ヘルパーやそれ以外の
  ツリーを構造化セクションに強いることはしません。
- `main` への merge で **GitHub Pages** のワークフローがリファレンスを公開します。API リファレンスは
  英語です（英語の docstring を描画します）。サイトの手書き部分は、リポジトリの日英ドキュメントの規則に
  従います。

### 規範の置き場所

規範そのものは [`docs/ja/ai-development.md`](../../docs/ja/ai-development.md)（と `docs/` のミラー）に
*コードのドキュメンテーションコメント（docstring）* の節として書き、[`CLAUDE.md`](../../CLAUDE.md) の
**Conventions** に要約します。既存の *ドキュメントの書き方* の規則と同じ分け方です。

### 段階的な移行

1. 本提案。
2. **既存の散文 docstring のまま**サイトを立ち上げます。docstring は変えません。型付きシグネチャは既に
   描画でき、パイプラインを実証し、すぐに価値が出ます。
3. 規範を `docs/ai-development.md`（と `docs/ja/`）と `CLAUDE.md` に書きます。
4. 公開 API の docstring を **module 単位の小さな PR で** Google 形式へ移します（小さな差分は速く merge
   でき、衝突もまれです。並行作業の方針、
   [BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md)）。
5. 限定した `ruff D` の強制を有効にします。
6. Pages のホスティングを有効にします。

## 検討した代替案

- **散文の docstring のまま、シグネチャを内省するツールで生成し、形式は変えない。** `pdoc` や
  `mkdocstrings` は散文の docstring を描画し、型付きのシグネチャも出します（型は docstring ではなく注釈
  から来ます）。つまり、docstring を一つも触らずに、たどれて型の付いたリファレンスが今日でも得られます。
  これは最も低コストの道で、推奨もこれでした。基準点としてここに記録します。本提案はそこから一歩進み、
  公開 API の面に Google 形式の構造化セクションを採り、リファレンスに引数単位の説明という一貫性を
  もたらすために、移行のコストを引き受けます。
- **`Sphinx + autodoc + napoleon`。** 同じ Google 形式の docstring を解釈しますが、reStructuredText
  ネイティブで（Markdown には `myst-parser` が要ります）、`autodoc` はモジュールを import するため、
  任意かつ遅延のすべての依存に `autodoc_mock_imports` を強います。`mkdocstrings` + `griffe` は Markdown
  ネイティブで静的です。代替として残します。
- **内部ヘルパーまで含め、ツリー全体を移行する。** 却下します。1,000 を超える docstring は churn と衝突面
  が大きく、小さな private ヘルパーへの `Args:` は、このリポジトリが避ける *what* のナレーションです。
  公開 API の面に絞ります。
- **何もしない。** 現状維持です。暗黙で明文化されていない散文の規範と、描画された API の面が無いまま
  です。却下します。規範は揺らぎ、増えていく公開 API には、人にもエージェントにも地図がありません。
- **専用の新トピックを設ける。** 代わりに *コントリビューターワークフロー*
  （[BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md)
  のトピック）の下に置きます。コントリビュータとエージェントがコードを理解する話だからであり、単一の
  項目のためにトピックを切らない先例にならいます。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

- [CLAUDE.md](../../CLAUDE.md)：Conventions（コメントは *why*、ドキュメントは日英、コードコメントは
  英語）と、本項目が守る prime directive（ゲートに LLM を入れない）。
- [BE-0043 — コンフリクトに強いファイル流動](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md)：
  *コントリビューターワークフロー* の先例。成果物としての生成ドキュメント、衝突しない小さな PR。
- [`docs/ja/ai-development.md`](../../docs/ja/ai-development.md)：規範の置き場所。
  [`docs/ja/README.md`](../../docs/ja/README.md)：「コードコメントと docstring は英語」。
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py)、[`bajutsu/cli/`](../../bajutsu/cli/)、
  [`bajutsu/mcp/`](../../bajutsu/mcp/)：規範が最初に扱う公開の面。
- [`pyproject.toml`](../../pyproject.toml)：`mypy` strict と `ruff` `ANN`。型を docstring ではなく注釈に
  置く理由。
- MkDocs Material、`mkdocstrings[python]`（`griffe`）、`Sphinx` + `napoleon`：候補となる生成ツール。

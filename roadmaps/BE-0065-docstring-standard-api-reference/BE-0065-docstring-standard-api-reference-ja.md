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

本項目は、Python コアの **docstring の規範**を一つ明文化し、それを基に **API リファレンスを生成**します。このリポジトリを読み書きする人と AI エージェントが、すべてのファイルを開かなくても、公開 API の全体像をつかめるようになります。

公開 API は **Google 形式**の docstring で書き、生成したリファレンスとして公開します。内部のヘルパーは、このコードベースがすでに好んでいる、簡潔な散文の docstring のままにします。散文で型を繰り返すことはしません。型は各シグネチャの注釈にあり、生成ツールがそこから読み取るからです。

規範は、公開 API の範囲で `ruff` が強制します。リファレンスは、静的なドキュメント生成ツール（推奨は MkDocs と `mkdocstrings`）が描画します。どちらも、決定的な `run` / CI ゲートには触れません。

## 動機

Python コアは、すでによくコメントされています。module docstring は *なぜ* を説明し、型注釈は揃っています（`mypy` は strict で、`ruff` の `ANN` 規則も有効です）。挙動は BE 項目に相互参照されています。ただし、欠けているものが 2 つあります。

1 つ目は、**規範が暗黙であること**です。`ruff` は `D`（pydocstyle）規則を選択していません。コードベースは構造化セクションのない散文の docstring を使っており、`Args:`、`Returns:`、`:param:` は現状どこにもありません。意図的で良いスタイルですが、どこにも書かれていません。そのため、公開 API が増えるにつれて揺らぎます。対象は、`Driver` プロトコル、CLI、MCP ツール、シナリオスキーマと広がっています。

2 つ目は、**生成されたリファレンスがないこと**です。`docs/` の手書きドキュメントは、概念とワークフローを説明します。しかし、公開 API そのものを描画するものはありません。`Driver` の形、selector の型、MCP ツールの一覧を知りたい読者は、ソースを開くしかありません。

この 2 つの欠落は、いま一段と効いてきます。**エージェントによるコーディングが、このコードを読み書きする主要な手段になっている**からです。一貫した docstring の規範と、たどれる API の面は、AI に頼るコントリビュータと、AI 自身の双方を助けます。公開引数の意味を探す場所が一か所に定まります。100 を超えるファイルをたどる代わりに、描画された 1 枚の面で全体を見渡せます。

リポジトリにアクセスできるエージェントは、依然としてソースを読みます。そのため、規範のより大きな効果は、*一貫性と描画された面*にあり、形式そのものの重要性は下がります（「検討した代替案」を参照）。

変更の対象は、ドキュメントとツールだけです。どこにも LLM を足さず、`run` の中で動くこともなく、リファレンスの生成はゲートの外に置きます。prime directive の 1 と 2（[CLAUDE.md](../../CLAUDE.md)）は、構造上保たれます。

## 詳細設計

### 範囲：公開 API の面

構造化した（Google 形式の）docstring は、外部の呼び出し側とエージェントが触れる**公開 API の面**に適用します。

- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) の `Driver` プロトコルと、共有の型（`Selector`、`Element` など）
- CLI コマンド（[`bajutsu/cli/`](../../bajutsu/cli/)）
- MCP のツールとリソース（[`bajutsu/mcp/`](../../bajutsu/mcp/)）
- シナリオスキーマ（ハブとなる成果物）
- runner、`assertions`、`network` の公開関数

内部の module-private なヘルパー（`_` で始まる関数）は、**簡潔な散文の docstring** のままにします。存在理由を述べる、目的のある 1 行で十分です。小さなヘルパーに `Args:` ブロックを強いると、このリポジトリが避けている *what* のナレーションが増えます。生成したリファレンスからは、private のメンバーを除外します。

### 規範

- **言語は英語**。すべてのコードコメントと同じです。散文のドキュメントは日英の両方で書きますが、コードは英語だけです（[`docs/ja/README.md`](../../docs/ja/README.md)）。
- **公開 API は Google 形式**。1 行の要約を書きます。`Args:`、`Returns:`、`Raises:`（および `Yields:` や `Examples:`）は、**情報が増えるときに**置きます。
- **型を書き直さない**。型は注釈にあり、生成ツールがシグネチャから読み取ります。`Args:` と `Returns:` には *意味* を書きます。単位、制約、`None` が何を表すかを書き、型は書きません。
- **what ではなく why**。理由、不変条件（とりわけ決定性を守るもの）、トレードオフ、境界条件を書きます。挙動の理由は、対応する `BE-NNNN` 項目に結びつけます。
- **周囲の密度に合わせる**。短く、目的を持って書き、ナレーションは足しません。
- **フィールドごとの作法を保つ**。`TypedDict` や定数を持つクラスでは、各フィールドの行末コメントが、そのフィールドの *why* を散文のブロックより良く表します。このコメントは残します。

### 具体例

次の例は、公開関数の before（現在の散文）と after（規範）です。型は繰り返しません。`Args:` と `Returns:` は意味を書き、決定性の不変条件を先頭に置き、理由を BE 項目に結びつけます。

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

内部ヘルパーは散文のままにします。*why* を述べる 1 行を置き、`Args:` ブロックは置きません。

```python
def _contains(outer: Frame, inner: Frame) -> bool:
    """Whether `inner`'s frame sits inside `outer`'s (edges inclusive)."""
```

`TypedDict` や定数を持つクラスは、フィールドごとの行末コメントを残します。このコメントは、各フィールドの *why* を、散文の `Args:` 風ブロックより良く表します。

```python
class Selector(TypedDict, total=False):
    """How to address an element. Provided fields are combined with AND."""

    id: str      # exact accessibilityIdentifier (first choice)
    index: int   # nth of multiple matches (last resort; flaky)
```

### 生成

推奨する構成は、**MkDocs + Material + `mkdocstrings[python]`** です。

- この構成は **Markdown ネイティブ**なので、API ページを、既存の日英 `docs/` と同じサイトに置けます。
- `mkdocstrings` は、シグネチャを **`griffe` で読みます。`griffe` は静的に解析**し、モジュールを import しません。コアが土台にしている、遅延かつ任意の import（`playwright`、`fb-idb`、`fastapi`、`redis`、`boto3`）を回避できます。`Sphinx autodoc` なら、これらをモックしなければなりません。
- 型付きのシグネチャは注釈から自動で出るので、docstring が型を書き直すことはありません。

代替は、`Sphinx + autodoc + napoleon`（Markdown 用に `myst-parser`）です。同じ Google 形式の docstring を解釈しますが、ここでは重く、任意の import をモックする必要があります。

### パッケージング、強制、ホスティング

- `pyproject.toml` に、新しい `docs` の **任意依存グループ**を置き、生成ツールとプラグインを入れます。他の extra と同じく隔離します。
- `make docs` ターゲットと `make docs-serve` ターゲットは、**コアのゲートの外**に置きます。実機 E2E と同様に、リファレンスの生成は別の重い経路であり、`make check` を遅くしてはいけません。
- **強制**：`ruff` の `D` 規則を `convention = "google"` で有効にし、`per-file-ignores` で**公開モジュールに限定**します。tests、demos、scripts は、すでに `ANN` と `T20` を ignore しています。内部ヘルパーと、それ以外のツリーを、構造化セクションに強いることはしません。
- `main` への merge で、**GitHub Pages** のワークフローがリファレンスを公開します。API リファレンスは、英語の docstring を描画するので英語です。サイトの手書き部分は、リポジトリの日英ドキュメントの規則に従います。

### 規範の置き場所

規範そのものは、[`docs/ai-development.md`](../../docs/ai-development.md)（と `docs/ja/` のミラー）に、*コードのドキュメンテーションコメント（docstring）* の節として書きます。要約は、[`CLAUDE.md`](../../CLAUDE.md) の **Conventions** に置きます。既存の *ドキュメントの書き方* の規則も、同じ分け方をしています。

### 段階的な移行

1. 本提案。
2. **既存の散文 docstring のまま**、サイトを立ち上げます。docstring は変えません。型付きシグネチャはすでに描画でき、パイプラインを実証し、すぐに価値が出ます。
3. 規範を、`docs/ai-development.md`（と `docs/ja/`）と `CLAUDE.md` に書きます。
4. 公開 API の docstring を、**module 単位の小さな PR で** Google 形式へ移します。小さな差分は速く merge でき、衝突もまれです。これは、[BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md) の並行作業の方針に沿います。
5. 限定した `ruff D` の強制を有効にします。
6. Pages のホスティングを有効にします。

## 検討した代替案

- **散文の docstring のまま、シグネチャを内省するツールで生成し、形式は変えない。** `pdoc` と `mkdocstrings` は、散文の docstring *と*型付きのシグネチャを描画できます。型は docstring ではなく、注釈から来るからです。docstring を 1 つも触らずに、たどれて型の付いたリファレンスが、いまでも得られます。この道は最も低コストで、推奨もこれでした。そのため、基準点として本項目に記録します。本提案はそこから一歩進み、公開 API の面に Google 形式の構造化セクションを採ります。リファレンスに、引数単位の予測しやすい説明がもたらされます。この一貫性のために、移行のコストを引き受けます。
- **`Sphinx + autodoc + napoleon`。** 同じ Google 形式の docstring を解釈しますが、reStructuredText ネイティブで、Markdown には `myst-parser` が要ります。`autodoc` はモジュールを import するので、任意かつ遅延のすべての依存に `autodoc_mock_imports` を強います。`mkdocstrings` と `griffe` は、Markdown ネイティブで静的です。Sphinx は代替として残します。
- **内部ヘルパーまで含め、ツリー全体を移行する。** 却下します。約 1,000 の docstring は、churn と衝突面が大きくなります。小さな private ヘルパーへの `Args:` は、このリポジトリが避けている *what* のナレーションです。公開 API の面に絞ります。
- **何もしない。** 現状維持では、暗黙で明文化されていない散文の規範が残り、描画された API の面もありません。却下します。規範は揺らぎ、増えていく公開 API には、人にもエージェントにも地図がありません。
- **専用の新しいロードマップのトピックを設ける。** 代わりに、*コントリビューターワークフロー*（[BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md) のトピック）の下に置きます。本項目は、コントリビュータとエージェントがコードを理解する方法を扱うからです。単一の項目のためにトピックを切らない、という先例にも従います。

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

[English](BE-0078-roadmap-status-folders.md) · **日本語**

# BE-0078 — 状態ごとのロードマップフォルダ（提案 / 保留 / 実装中 / 実装済み）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0078](BE-0078-roadmap-status-folders-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0078") |
| 実装 PR | [#220](https://github.com/bajutsu-e2e/bajutsu/pull/220) |
| トピック | 開発基盤（コントリビュータ体験） |
<!-- /BE-METADATA -->

## はじめに

ロードマップ項目の `状態` はすでに **4つ** の値のいずれかを取ります。`提案`、`提案（保留）`、`可決・実装中`、
`実装済み` です。ところがリポジトリは、どの項目も `roadmaps/proposals/` と `roadmaps/implemented/` の **2つ**
のフォルダにしか振り分けません。フォルダの軸は二段階ぶん粗く、メタデータがすでに記録している2つの区別が、
項目をディスクに書いた瞬間に失われます。*実装中* の項目が単に *提案された* だけの項目と並んで `proposals/`
に座り、*棚上げされた*（保留の）提案も同じ場所に、生きた提案と見分けがつかないまま座ります。今、`proposals/`
の下に実装中の項目が9つ（BE-0026、BE-0038、BE-0041、BE-0048、BE-0049、BE-0050、BE-0052、BE-0054、
BE-0068）、保留が2つ（BE-0027、BE-0040）あります。

本項目は、フォルダ配置を `状態` の忠実な1対1の写像にします。すなわち、(1) 中間の状態を `In progress` /
`実装中` に改称し、(2) **各** 状態にそれぞれのフォルダを与え（`proposals/`、`deferred/`、`in-progress/`、
`implemented/`）、(3) 索引を同じ順の4つの最上位区分に再編して、読み手がたどるページとディスク上の配置を
正確に一致させます。対象はすべて文書、2つのスクリプト、書式／索引のテストであり、どの経路にも LLM を
持ち込まず、runner、driver、`serve` の挙動も変えません。したがってプライムディレクティブのいずれにも触れません。

## 動機

ロードマップは今、**3つの独立した軸** をひそかに抱えており、ライフサイクルをどこまで細かく分けるかで軸
どうしが食い違っています。

| 軸 | 値 | 何を決めるか |
|---|---|---|
| `状態` | `提案` · `提案（保留）` · `可決・実装中` · `実装済み` | 唯一の根拠 |
| `トラック` | `可決済み` · `提案` | 索引の最上位の区分 |
| フォルダ | `proposals/` · `implemented/` | 項目のディスク上の置き場とリンクパス |

`状態` はもっとも細かい軸（4値）で、唯一の根拠として扱われています。`promote_roadmap_items.py` が自身の
docstring でそう述べています。にもかかわらず、そこから導かれるフォルダはもっとも粗く（2値）、`実装済み`
*だけ* を `implemented/` に、*それ以外すべて* を `proposals/` に写します。つまり `proposals/` は同時に3つの
ものを兼ねています。生きた提案、棚上げされた（保留の）提案、そして PR を進行させている項目です。日々の
摩擦はこうです。`proposals/` をたどっても（ディスク上でも索引でも）、各ファイルの `状態` を開かないかぎり、
「半分組み上がった」と「未着手」と「棚上げ」を見分けられません。

中間と棚上げの状態は周辺的な事例ではありません。プロジェクトでもっとも活発な項目のうち9つが実装中で、2つが
保留です。それらを分ける情報はすでに `状態` にあります。平らに潰しているのはフォルダ配置だけです。

これは、コントリビュータ体験の系譜がずっと閉じてきたのと同じ種類の問題です。不変条件を散文に委ねず、
構造そのものに担わせます。
[BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md)
は、独立した変更が互いに素なファイルに触れるようファイルフローを組み替えました。
[BE-0074](../BE-0074-be-template-standardization/BE-0074-be-template-standardization-ja.md)
は、項目テンプレートと `状態` の語彙をゲートテストで固定しました。本項目はその系譜を継ぎ、各 `状態` をそれぞれ
のフォルダにして、メタデータがすでに記録している区別を配置に語らせます。

## 詳細設計

### `状態` と4つの置き場

`状態` を唯一の根拠のままとし、各値がちょうど1つのフォルダと1つの索引区分に対応します。全単射です。

| 状態（EN / JA） | フォルダ／索引区分 |
|---|---|
| `Proposal` / `提案` | `roadmaps/proposals/` |
| `Proposal (deferred)` / `提案（保留）` | `roadmaps/deferred/` |
| `In progress` / `実装中` | `roadmaps/in-progress/` |
| `Implemented` / `実装済み` | `roadmaps/implemented/` |

`提案` と `提案（保留）` は兄弟であり（どちらも提案で、一方は生きており一方は棚上げされている）、
親子ではなく *隣り合う* 区分とします。両者を分けることで、いま活発に検討中のものと、棚上げされたものとを、
読み手が一目で見分けられます。それこそが `保留` という区別の存在理由です。

### 中間の状態の改称

`Accepted, in progress` を `In progress` に、`可決・実装中` を `実装中` にします。これにより、揃うべき3つの
名前（状態の語、フォルダ名、索引の見出し）がいずれも「実装中」と読めるようになり、いまや不要な「可決
済みである」という枠組み（実装中の項目は必ず可決済みである）を落とします。変更が及ぶのは、9つの実装中項目の組
（`状態` の値、両言語）、[`tests/test_roadmap_format.py`](../../tests/test_roadmap_format.py) の
`STATUS_PAIR`、[`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py) の
`status_display` 写像（索引はすでに中間の状態を「In progress」/「実装中」と表示している。生のキーが変わる）、
そして状態の集合を列挙している散文（[`CLAUDE.md`](../../CLAUDE.md)、
[`docs/ai-development.md`](../../docs/ai-development.md)、両言語の README 索引ページ）です。

### `状態` を唯一の根拠とし、`トラック` を退役させる

フォルダは *すでに* `状態` から導かれています。いまだ手で設定する唯一の軸が `トラック` であり、4分の区分に
なると `状態` の純粋な言い換えになります。すなわち `実装済み` ⇒ 実装済み、`実装中` ⇒ 実装中、`提案` ⇒ 提案、
`提案（保留）` ⇒ 保留。`状態` をなぞるだけの手入力フィールドは、まさに BE-0043 と BE-0074 が取り除こうと
してきたズレの温床です。

そこで推奨する設計は、`トラック` フィールドを **退役させ**、索引の区分を `状態` から導くことです。
`build_roadmap_index.py` のセクションキーを `(track, topic)` から `(bucket(status), topic)` に変えます
（`bucket` は上表の写像）。`トラック` はメタデータの定義と、`test_roadmap_format.py` のフィールド順／必須
集合から外します。各項目ファイルはメタデータ1行を失い、`状態` が、項目のフォルダと索引のセクションの両方を
決める唯一のフィールドとして残ります。両者がもはや食い違いえなくなります。（`トラック` を残す、変更量の少ない
代替案は「検討した代替案」で比較します。）

### 索引の再編

2つの最上位の見出し（`## Accepted` / `## Proposals`、日本語の `## 可決済み` / `## 提案`）を4つにします。
進捗の進んだ順に並べ、棚上げの区分を末尾に置きます。

- EN: `## Implemented` · `## In progress` · `## Proposals` · `## Deferred`
- JA: `## 実装済み` · `## 実装中` · `## 提案` · `## 保留`

`トピック` は変えません。各区分の中の二次的なまとまりのままです。`build_roadmap_index.py` の `SECTIONS`
表は区分でキーし直します。区分をまたぐトピックは分かれ、各部分が自分の区分の下で自分の `<!-- GENERATED:* -->`
マーカー対を持ちます。今日、新しい保留区分にまたがるトピックは2つあります。*Miscellaneous / on hold*（保留の
メンバー BE-0027 が保留区分へ移り、生きた BE-0028 は提案に残る）と *Candidates from competitive research
(MagicPod / Autify)*（BE-0040 が移り、BE-0037 / BE-0046 は残る）。マーカーの外のトピックごとの散文は
保たれます。「可決済み」の枠組みの段落は実装済み／実装中の分割に合わせて書き直し、「Miscellaneous / **on
hold**」というトピック名は、保留が独立区分になった今、「on hold」を外せます。

### フォルダの移行

移動するのは11個の項目ディレクトリ、すなわち9つの `実装中` 項目と2つの `保留` 項目です
（`git mv roadmaps/proposals/<dir> roadmaps/in-progress/<dir>` および `…/deferred/<dir>`）。`実装済み`
の項目はすでに `implemented/` にあり、生きた `提案` の項目は `proposals/` に残るので、どちらも動きません。
索引の **表の行** は自動で再生成されます。`build_roadmap_index.py` が各リンクのパスを、項目が今いる
フォルダから導くからです。よって索引のリンクは手で直しません。

手で直すべきは、生成領域の外にある、移動した項目への **手書きのリンク** です。コミット時に誰も読み直さ
ないからです。これらはすべて9つの実装中項目を指しています。保留2件は、自動再生成される表の外には参照を **持ち
ません**。

- 各言語の README のセクション前書きのリンク2つ（BE-0038 と BE-0041）。
- `docs/` のおよそ十数か所の参照（および `docs/ja/` の対応箇所）。`cli.md`、`multi-platform.md`、
  `drivers.md`、`scenarios.md` がいずれも、9項目のどれかへ `roadmaps/proposals/BE-00xx-…` で指しています。

本リポジトリのゲートには **リンクチェッカがない** ため、古い `proposals/…` パスが残っても `make check`
は失敗しません。読み手に対して 404 になるだけです。実装では同じ変更でこれらのパスを一掃する必要があります
（既知の slug に対するスクリプト置換で足ります）。ゲートにロードマップのリンク検査を加えるのも妥当な随伴策で、
これは
[BE-0069](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails-ja.md)
（実行可能なコントリビュータガードレール）の方向に収まります。

### 触れるスクリプトとテスト

- [`scripts/promote_roadmap_items.py`](../../scripts/promote_roadmap_items.py) —— `CATEGORIES` を
  4つに増やし、`expected_category(status)` が4分岐の写像を返します。スクリプトの仕事は変わりません（各項目の
  フォルダを `状態` に合わせて整える）。単に行き先が4つになるだけです。ゲートの対となる
  `tests/test_promote_roadmap_items.py` も追従します。
- [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py) —— `CATEGORIES` を4つに、
  `status_display` のキーを改称、`SECTIONS` を区分でキーし直し、`トラック` の解釈を除去（または代替案では
  転用）。ゲートの対となる `tests/test_roadmap_index.py` も追従します。
- [`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py) —— 既存 ID を全フォルダに
  わたって数えるよう `CATEGORIES` を4つに。`PLACEHOLDER_CATEGORY` は `proposals` のまま（新規項目は常に
  まず生きた提案である）。
- [`tests/test_roadmap_format.py`](../../tests/test_roadmap_format.py) —— `CATEGORIES` を4つに、
  `STATUS_PAIR` の中間の対を改称、`トラック` を必須フィールド集合とフィールド順から外す（推奨設計）。

### プライムディレクティブとの整合

変更は、文書、2つの生成器／検査スクリプト、それらのゲートテストです。どの経路にも LLM を加えません。`run`
と CI は決定的なまま、アプリ固有のものはツールに入り込みません。BE-0043 / BE-0061 / BE-0074 と同じ系統の、
コントリビュータ体験のリファクタリングです。

## 検討した代替案

- **2フォルダのまま、状態の改称と索引の見た目の再編だけ行う。** 却下します。フォルダの粗さ *こそ* が摩擦で
  あります。実装中と保留の項目を物理的に `proposals/` に置いたままにすれば、本項目が取り除こうとするまさにその
  平坦化が残ります。
- **`in-progress/` フォルダだけ設け、保留は `proposals/` の中に残す**（3フォルダ）。これは変更量の小さい、
  筋の通った案でもあります。保留の項目は棚上げされた提案なので、提案フォルダを共有してもよいでしょう。それでもここでは
  完全な全単射を採ります。各 `状態` にそれぞれのフォルダを与えれば、読み手はファイルを開かずとも、ディスク上でも
  索引でも「棚上げ中のもの」を直接たどれるし、ある状態だけ別の状態の置き場を間借りする特例を設けず、「一状態
  一フォルダ」という規則を一様に保てます。
- **`可決・実装中` のラベルを残す。** 却下します。`in-progress/` という名のフォルダと「可決・実装中」と読む
  状態の組み合わせは、本項目が取り除く名前と概念の継ぎ目をふたたび持ち込みます。`In progress` / `実装中` なら
  状態、フォルダ、見出しが同じに読めます。
- **`トラック` を残し、4つの値に拡張する**。`状態` から区分を導くのではなく。生成器の変更は小さく
  （`(track, topic)` でキーしたまま）、BE-0074 が固定したメタデータの形も保てます。推奨はしません。4区分では
  `トラック` は `状態` の純粋な重複であり、*フォルダを `状態` から導く* 不変条件がすでに避けているズレの
  温床を持ち込みます。しかも全項目で `トラック` の値を編集する手間は依然かかります。`状態` から導くほうが終着点と
  して綺麗です。本代替案は、スキーマ変更が1つの PR には大きすぎると判断した場合の保守的な代替に留めます。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。
- **後日の注記：** 本項目が定める「状態からバケットを導く」設計はそのまま有効です。ただし本項目が
  描く描画先（`README.md` / `README-ja.md` にある `## Implemented` / `## In progress` /
  `## Proposals` / `## Deferred` の 4 見出しと、その `<!-- GENERATED:* -->` マーカー対）は
  [#1257](https://github.com/bajutsu-e2e/bajutsu/pull/1257) でロードマップダッシュボードへ
  置き換わりました。ダッシュボードは、本項目が定めるのと同じ 4 つのバケットへ全項目を分類します。

## 参考

- [`CLAUDE.md`](../../CLAUDE.md) —— 本項目が改める、ロードマップの状態／フォルダの規則。
- [`roadmaps/README.md`](../README-ja.md) —— 4つになる 2フォルダモデルの散文による説明。
- [`docs/ai-development.md`](../../docs/ai-development.md) —— コントリビュータガイドの 状態→トラック 表と
  「ディレクトリを移す」ライフサイクルの散文。どちらも本項目が更新します。
- [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py)、
  [`scripts/promote_roadmap_items.py`](../../scripts/promote_roadmap_items.py)、
  [`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py) —— 2フォルダ前提を
  ハードコードしている3つのスクリプトと、それを守る `tests/test_roadmap_format.py` /
  `tests/test_roadmap_index.py` / `tests/test_promote_roadmap_items.py`。
- [BE-0074 —— BE 項目テンプレートの標準化](../BE-0074-be-template-standardization/BE-0074-be-template-standardization-ja.md)
  —— 本項目が改める `状態` の語彙とメタデータの形（`トラック` を含む）を固定した項目。
- [BE-0043 —— 衝突に強いファイルフロー](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md)、
  [BE-0061 —— 衝突しない BE-ID 採番](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening-ja.md)
  —— 「構造に不変条件を担わせる」やり方を、本項目が継ぐコントリビュータ体験の兄弟項目。
- [BE-0069 —— 実行可能なコントリビュータガードレール](../BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails-ja.md)
  —— 移動後の古い `proposals/…` パスを捕まえる随意のロードマップリンク検査が収まる先。

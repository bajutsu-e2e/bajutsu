[English](BE-0159-flatten-roadmap-status-folders.md) · **日本語**

# BE-0159 — ロードマップ項目を単一ディレクトリにまとめ、状態別フォルダをやめる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0159](BE-0159-flatten-roadmap-status-folders-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0159") |
| 実装 PR | [#628](https://github.com/bajutsu-e2e/bajutsu/pull/628)（1/2）、[#631](https://github.com/bajutsu-e2e/bajutsu/pull/631)（2/2） |
| トピック | コントリビューターワークフロー |
| 関連 | [BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders-ja.md)、[BE-0096](../BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity-ja.md)、[BE-0109](../BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues-ja.md)、[BE-0149](../BE-0149-roadmap-placeholder-format-guardrail/BE-0149-roadmap-placeholder-format-guardrail-ja.md)、[BE-0154](../BE-0154-roadmap-promote-base-sha/BE-0154-roadmap-promote-base-sha-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0078](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders-ja.md) は
「状態」（`Status`）の 4 つの値それぞれに専用のディレクトリを与えました
（`roadmaps/{implemented,in-progress,proposals,deferred}/`）。この結果、項目の状態が変わるたびに
`git mv` でディレクトリが移動します。本項目は、この仕組みの半分を廃止することを提案します。すべての
項目を単一の `roadmaps/BE-NNNN-<slug>/` にまとめ、項目の ID が割り当てられた
（[BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation-ja.md)）
瞬間からパスを恒久的に固定し、以後の昇格がファイルシステムに一切触れないようにします。生成される索引と
ダッシュボードの分類は、これまでどおり状態が決めます。BE-0078 が本来求めていた読み手向けの区別はそのまま
残り、変わるのはファイルの置き場所を状態が決めなくなる点だけです。この置き場所こそが、リンク切れを
生んでいる張本人です。

## 動機

### 前提：昇格のたびにファイルが動き、リポジトリの外は誰も気付かない

`git mv` はファイルのパスを変えます。`roadmaps/<状態フォルダ>/BE-NNNN-…` へのパスを持つ読み手は、
Markdown のリンクであれ、GitHub の blob URL であれ、ブックマークであれ、その項目の状態が次に変わった
瞬間、何かがそのリンクを書き換えない限り、存在しない場所を指すことになります。このリポジトリはすでに
2 か所でこの後始末をしています。`promote_roadmap_items.py` が**`roadmaps/` の中の**クロスリンクを書き換え、
[BE-0096](../BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity-ja.md)
がそれを `docs/` まで広げました（見落としを `make check` の失敗にするゲート検査つきです）。どちらも実際に
動いている修復ですが、対象は HEAD の時点でリポジトリが把握し書き換えられる 2 つのリンク面に限られます。

### 既存の修復が届かない、現在進行形の具体的なバグ

[`scripts/sync_roadmap_tracking_issues.py`](../../scripts/sync_roadmap_tracking_issues.py)
（BE-0109）は、未完了の項目ごとに GitHub のトラッキング Issue を開き、本文にその項目のファイルへの
リンクを埋め込みます。

```python
href = f"{REPO_BLOB_ROOT}/roadmaps/{item.category}/{stem}/{stem}.md"
```

`create_issue()` はこの本文を Issue 作成時に**一度だけ**設定します。同期スクリプトが既存 Issue に対して
行うもう一方の操作は `close_issue()` だけで、開いたままの Issue の本文を編集する処理はどこにもありません。
つまり、ある項目が「提案」から「実装中」へ昇格すると（状態が変わっても両方とも未完了なので Issue は
開いたまま、`category` のセグメントだけが `proposals` から `in-progress` に変わります）、その Issue の
本文に埋め込まれたリンクは直後から 404 になり、Issue が開いている間はそのまま、閉じたあとは二度と
見直されないので永遠に壊れたままになります。ほぼすべての項目が「提案」を、多くは「実装中」も経て
「実装済み」に至ることを踏まえると、これは例外的なケースではありません。昇格を経た項目のトラッキング
Issue リンクにとって既定の結末であり、`docs/` と `roadmaps/` の 2 面に限定された BE-0096 の修復は、
構造上ここに届きません（ファイルではなく GitHub API 上のオブジェクトだからです）。

### 「読み手を一つずつ追いかけて直す」戦略には上限がない

トラッキング Issue は、より広いパターンの一例にすぎません。ロードマップ項目のパスを参照する消費者が
1 つ増えるたびに、そこが新しく腐りうる場所になります。ずれを検出し、自動修復するかゲートで検査する
やり方でカバーできるのは、このリポジトリのツールが自分のコミットの中で見て書き換えられる消費者だけです。
次のものには、届きませんし今後も届きません。

- **外部からの参照**：Slack のスレッドに貼られたリンク、ブログ記事からの引用、検索エンジンに索引化
  されたページ、別の GitHub リポジトリのコメントに残されたリンク。どれも `main` ブランチ上のある時点の
  パスを指しており、そのパスが動いた瞬間に、このリポジトリのどんな仕組みも触れられないまま恒久的に
  壊れます。
- **このリポジトリ自身の、すでに閉じた成果物**：マージ済み PR の説明文、古いコミットメッセージ、そして
  前述のとおりすでに閉じたトラッキング Issue。これらは技術的には GitHub 上にありますが、昇格の経路が
  それらを編集するようには配線されていないため、後から見直されることはありません。

新しく見つかる消費者（今日ならトラッキング Issue、明日には別の何か）は、そのたびに `roadmap-promote`
に継ぎ足される個別対応になります。これは、contributor のワークフロー路線がこれまで従ってきた「不変条件を
構造そのものに担わせる」という原則
（[BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md)、
[BE-0061](../BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening-ja.md)）
の逆を行っています。動き続けるパスそのものが不変条件の破れであり、その読み手を一つずつ追いかけて直すのは
症状への対処です。`docs/ai-development.md` はすでに `docs/` 側の修復を「索引と同じ自己修復」と説明して
おり、腐りが起きること自体を前提にツールで追いかけている実態を暗に認めています。本項目は、その原因
そのものを取り除きます。

### BE-0078 が本当に必要としていたものと、実際に作られたもの

BE-0078 の問題意識自体は正当でした。`proposals/` は、状態がすでに区別している 3 つの状態（提案中、
実装中、保留）を一つに潰していたため、ディスク上でも索引上でもフォルダを見ただけでは区別が付きません
でした。しかし BE-0078 は、性質の異なる 2 つのことを 1 つの変更にまとめてしまいました。読み手のための
4 分類のグループ分けと、そのグループ分けの手段としてのファイルシステム上の配置です。
`build_roadmap_index.py` は、ファイルが物理的にどのフォルダにあるかではなく、項目の `状態` フィールド
そのものから分類を導いています。生成される表と GitHub Pages のダッシュボード
（[BE-0094](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard-ja.md)）
は、ディレクトリの移動を一切必要とせずに、BE-0078 が求めていた一目で分かる区別をすでに読み手へ与えて
います。この 2 つを切り離せば、BE-0078 が本当に必要としていたものを保ったまま、昇格のたびに起きる
リンク腐りだけを取り除けます。

## 詳細設計

BE-0078 が導入し、BE-0096・BE-0149 が拡張したツール群に触れる、MECE な作業分解です。

1. **単一のフラットなディレクトリ。** すべての項目は `roadmaps/BE-NNNN-<slug>/`（未割り当ての間は
   `BE-0159-<slug>/`）に置かれ、`{implemented,in-progress,proposals,deferred}/` のセグメントは
   なくなります。項目のパスは ID が割り当てられた
   （[BE-0089](../BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation-ja.md)）
   瞬間に恒久化され、二度と変わりません。ID そのものにすでに適用されている「恒久的で、振り直さない」
   という保証を、パスにも広げる形です。
2. **`scripts/build_roadmap_index.py`。** 実装済み・実装中・提案・保留 × トピックの表を状態から生成する
   処理は今のままです。変わるのは生成されるリンクパスからフォルダ区分が消えることだけです。ディレクトリ
   走査の関心事である `CATEGORIES` は単一ディレクトリの走査にまとまり、状態による分類の関心事である
   `SECTIONS` は変わりません。
3. **`scripts/promote_roadmap_items.py` と `roadmap-promote.yml`。** `git mv` や「誤ったフォルダに
   入っている項目」という概念そのものがなくなります。項目の状態と食い違いうるフォルダがもう存在しない
   からです。このスクリプトに残る仕事、すなわち状態の変更後に索引を再生成する処理は、`make
   roadmap-index` がすでに単独で行っていることなので、promote の工程は「索引を再生成し、変わっていれば
   コミットする」だけに縮み、別立てのワークフローとして残すよりも既存の `roadmap-id` ワークフローに
   畳み込む方が自然です。
4. **`scripts/allocate_roadmap_ids.py`。** 使用済み ID の集計とプレースホルダの発見の両方に使っている
   4 カテゴリの走査（`CATEGORIES`）が、単一ディレクトリの走査にまとまります。`PLACEHOLDER_CATEGORY`
   は探す場所が 1 つしかなくなるため意味を失います。
5. **`scripts/check_roadmap_format.py` / `scripts/roadmap_ids.py`（BE-0149）。** 共有の
   `is_item_dir` / `is_numbered_dir` / `is_placeholder_dir` はディレクトリの名前だけを見て親を見ない
   ため、影響を受けません。`CATEGORIES` を走査する `_items()` が単一ディレクトリの走査にまとまるだけ
   です。BE-0149 が仕上げたばかりの締め付けから、「4 つのフォルダのどれか」という軸を丸ごと取り除く、
   正味の単純化になります。
6. **`scripts/new_roadmap_item.py`。** `--status` の値に関わらず、新しい項目を常に `roadmaps/` 直下に
   作成します。`--status` フラグ自体はメタデータの状態（ひいては将来の索引分類）を設定する役目のまま
   残り、フォルダを選ぶ役目だけを失います。
7. **`scripts/sync_roadmap_tracking_issues.py`（BE-0109）。ここが具体的な修正点です。** `issue_body()`
   の `href` から `item.category` のセグメントを取り除きます。これにより、トラッキング Issue のリンクは
   その項目が存在する限り、以後どんな状態の変更にも動じなくなり、前述の現在進行形のバグが直ります。
   さらに一度限りの移行作業として（繰り返す仕組みではありません）、既存のトラッキング Issue すべてを
   （`gh issue list --label roadmap-tracking --state all` で列挙し、それぞれ `gh issue edit` で）洗い
   直し、すでに壊れているリンクを直すパスも用意します。壊れたまま放置するよりも明らかに良い選択です。
8. **文章。** `CLAUDE.md`、`roadmaps/README.md` / `README-ja.md`、`docs/ai-development.md` から
   「状態は 4 つのフォルダのどれか 1 つに対応する（全単射）」という規則とその 4 行の対応表を取り除き、
   「すべての項目は `roadmaps/BE-NNNN-<slug>/` に置かれ、状態が決めるのは索引・ダッシュボードの分類
   だけである」に書き換えます。索引の 4 分類自体、その並び順、「実装中」という呼び方（いずれも
   BE-0078）には手を付けません。撤回するのは「フォルダ = 分類」という主張だけです。
9. **一度限りの移行。** 1 つの PR で既存のすべての項目のディレクトリをフラットな配置へ `git mv` し、
   索引を再生成します。すでに公開されている `roadmaps/<状態フォルダ>/…` へのすべての外部リンク（他所で
   共有された GitHub の blob リンク、検索エンジンに索引化されたページ、修復前に閉じていたトラッキング
   Issue）は、この移行で一度だけ壊れます。これが本項目が受け入れる、唯一の意図的で一度限りの破損です
   （見返りは「検討した代替案」で述べます）。
10. **波及する項目。**
    [BE-0154](../BE-0154-roadmap-promote-base-sha/BE-0154-roadmap-promote-base-sha-ja.md)
    は `roadmap-promote.yml` が `promote_roadmap_items.py` を PR 側からチェックアウトして実行している
    点を締め付ける提案です。このスクリプトの仕事が索引の再生成だけに縮む（項目 3）と、BE-0154 の前提で
    ある「PR の影響を受けるスクリプトが `contents: write` で動く」という状況自体がほぼ消えます。
    したがって BE-0154 は、本項目が廃止するコードに向けて実装するのではなく、本項目が着地したあとに
    見直す（閉じるか書き直す）べきです。

### 性能とスケール

状態変更のたびに索引を再生成し（トラッキング Issue も同期し）ていると、項目数が増えるにつれて処理が
重くなるのではないか、というのは正当な疑問です。しかし次の 3 つの理由から重くならず、本項目がそれを
悪化させることもありません。

- **再生成のコストは今日すでにかかっており、本項目は仕事を増やすのではなく減らします。** 索引は今でも、
  あらゆるロードマップ変更のたびにゼロから作り直されています。`roadmap-promote` が移動のあとに
  `build_roadmap_index.py` を走らせているからです。本項目が取り除くのは昇格ごとの `git mv` であって、
  残る再生成は今動いているものと同じです。状態変更は「ディレクトリを移動し、なおかつ再生成する」から
  「再生成だけ」になるので、1 回あたりの仕事はむしろ減ります。
- **再生成は軽い O(N) の走査です。** 索引（`build_roadmap_index.py`）もダッシュボード
  （`build_roadmap_dashboard.py`）も、各項目の小さなメタデータブロックを読んで表を出し直すだけの
  一巡の線形走査です。現在の 151 項目では実作業は数十ミリ秒のオーダーで（実時間の大半は走査ではなく
  Python の起動が占めます）、10 倍の規模でも 1 秒には遠く及びません。ゲートは `make check` のたびに
  すでに索引の全再生成を走らせているので、このコストは常時実行されており、退行のリスクにはなりません。
- **トラッキング Issue の同期は差分方式で、全再生成ではないため、総項目数では増えません。**
  `sync_roadmap_tracking_issues.py` の `plan()` は差分（`to_create` / `to_close`）だけを計算し、
  変わった Issue だけを触ります。1 回の昇格で API 書き込みは通常 0〜1 件です。ロードマップの規模に
  比例するのはローカルのファイル走査 1 回（軽い）と `gh issue list` 呼び出し 1 回だけで、後者は
  「未完了」の Issue 数に縛られ（`--limit 1000`）、どちらも本項目は変えません。

### プライムディレクティブへの適合

ドキュメントとロードマップ用ツール、そのゲートテストだけに触れる変更です。どの経路にも LLM は入らず、
`run` と CI は決定的なままで、アプリ固有・バックエンド固有の部分には触れません。

## 検討した代替案

- **4 つのフォルダを維持し、新しく見つかる消費者（トラッキング Issue、この先見つかる何か）へ検出と修復を
  広げ続ける。** これはすでに歩んでいる道です（`roadmaps/` に対する `promote_roadmap_items.py`、
  `docs/` に対する BE-0096、そして今回のトラッキング Issue への修正）。長期的な答えとしては不採用です。
  消費者の一覧に上限がなく、このリポジトリの外にある消費者（外部のブックマーク、検索エンジンのキャッシュ、
  他リポジトリの Issue や PR、このリポジトリ自身のすでに閉じた成果物）には、そもそも見ることも修復する
  こともできません。BE-0096 がすでに出荷されたあとでも、リポジトリの内部にある消費者（トラッキング
  Issue）がすり抜けたという事実が、この戦略に構造的な上限があることを示しています。
- **4 つのフォルダを維持したまま、項目が移動したときに旧パスへリダイレクトやエイリアスを残す**
  （例えば新しい場所へ転送するスタブファイル）。不採用です。git にはエイリアスやリダイレクトに相当する
  第一級の仕組みがなく、それを模倣するにはフラット化とほぼ同じ量の作り込みが要る一方、ファイルシステム
  上の配置が「現在の状態」を暗示しつつ実際にはそれを確実に反映していない、という状態は残ってしまいます。
- **フォルダの数を変える**（例えば `deferred/` を `proposals/` に統合して 3 つにする）。本質的な問題
  には対処になりません。可変な状態に紐づくフォルダである限り、状態が変わるたびにファイルは動きます。
  フォルダの数は、パスが安定しているかどうかとは無関係です。
- **何もせず、腐りを既知の限られたコストとして受け入れる。** ほぼ現状維持です。`docs/ai-development.md`
  はすでに `docs/` 側の修復を必要な「自己修復」と位置づけており、腐りが起きること自体は織り込み済みだと
  暗に認めています。不採用です。トラッキング Issue のバグは、コストがすでにパッチ済みの 2 面に収まって
  いないことを示しており、現在開いているすべての項目について、今後のあらゆる昇格のたびに再発し、しかも
  今のところ何の修復もありません。
- **全再生成をやめ、変更のたびに索引やダッシュボードを差分更新する**（項目数の増加に伴う再生成時間の
  増大を先回りして避けるため）。不採用で、かつ本項目の範囲外です。各項目のメタデータからの全再生成は、
  プロジェクトがすでに依拠している「単一の真実」保証そのものであり
  （[BE-0043](../BE-0043-conflict-resistant-file-flow/BE-0043-conflict-resistant-file-flow-ja.md)、
  [BE-0094](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard-ja.md)）、
  差分更新はロードマップが必要としていない速度と引き換えに、状態を持つドリフトの余地を持ち込みます
  （O(N) の全再生成は現在の規模で数十ミリ秒です。「性能とスケール」を参照）。本項目はこの全再生成を
  足しも引きもしないので、差分生成が将来必要になったとしても、それはフォルダを残す理由ではなく、
  独立した後続作業です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

> **2 つのバッチに分けて出荷します。** 各 PR を単独でレビュー可能に保つためです（レビュアーの 1 PR
> あたりのファイル数上限を下回るようにします）。PR1 は、フラットな直下と旧来の状態フォルダの両方を走査する
> 一時的な二層対応ツーリングを導入し、実装済みの項目をフラット化します。PR2 は残りをフラット化し、二層対応の
> コードを取り除いて、フラット専用の後片付け（`promote` の廃止、状態による承認ゲート、文章の書き換え）を
> 行います。どの段階でもツリーは正しい状態に保たれます。

- [ ] 物理的な配置のフラット化：すべての項目をフラットな直下へ `git mv`（PR1 は実装済みの項目、PR2 は
      残り）し、新しい（フラットな）パスで索引を再生成する。
- [ ] `scripts/build_roadmap_index.py`：リンク生成からカテゴリ別ディレクトリを取り除く。生成される
      表の状態による分類そのものは変えない。
- [ ] `scripts/promote_roadmap_items.py` / `roadmap-promote.yml`：ファイル移動のロジックを廃止し、
      残る「状態変更時の索引再生成」を既存の `roadmap-id` ワークフローへ畳み込む（または最小限の
      別立てのまま残す）。
- [ ] `scripts/allocate_roadmap_ids.py`：4 カテゴリの走査を単一ディレクトリの走査にまとめる。
- [ ] `scripts/check_roadmap_format.py` / `scripts/roadmap_ids.py`：`_items()` の 4 フォルダ走査を
      単一ディレクトリの走査にまとめる（ID の形を見る述語自体は影響を受けない）。
- [ ] `scripts/new_roadmap_item.py`：`--status` に関わらず、新しい項目を常に `roadmaps/` 直下に
      作成する。
- [ ] `scripts/sync_roadmap_tracking_issues.py`：`issue_body()` の `href` から `item.category` を
      取り除く。既存のトラッキング Issue（開いているものも閉じたものも）すべてに対して、壊れた
      リンクを直す一度限りの修復を走らせる。
- [ ] 文章：`CLAUDE.md`、`roadmaps/README.md` / `README-ja.md`、`docs/ai-development.md` の
      フォルダ全単射の規則を「単一ディレクトリ。状態が決めるのは索引の分類だけ」に書き換える。
- [ ] [BE-0154](../BE-0154-roadmap-promote-base-sha/BE-0154-roadmap-promote-base-sha-ja.md)
      に、`promote_roadmap_items.py` の仕事が索引再生成だけに縮んだ段階で見直しか終了が必要である旨の
      印を付ける。

ログ:

- PR1（バッチ 1）、[#628](https://github.com/bajutsu-e2e/bajutsu/pull/628)：一時的な二層対応の走査
  （`roadmap_ids.iter_item_dirs` がフラットな直下と旧来のフォルダの両方を走査）を、索引・採番・
  フォーマット・同期・ダッシュボード・lint の各ツールに導入し、新規項目をフラットに生成するようにし、
  実装済みの項目をフラットな直下へ `git mv`（索引を再生成し、クロスリンクを修復）しました。`promote` は
  残るフォルダ内の項目を引き続き管理します。BE-0159 を `実装中` に移しました。
- PR2（バッチ 2）、[#631](https://github.com/bajutsu-e2e/bajutsu/pull/631)：残りの項目（提案・実装中・保留）をフラットな直下へ `git mv` し、一時的な二層対応の
  コードを取り除き（`iter_item_dirs` は再び単一のフラット走査になり、`category` を削除）、
  `promote_roadmap_items.py` とそのワークフロー・ゲートテスト・Make ターゲットを廃止し、提案承認ゲートを
  状態による判定へ切り替え、`CLAUDE.md`・各 README・`docs/ai-development.md`・`docs/roadmap-workflow.md`・
  各スキルの文章の書き換えを完了しました。BE-0159 は `実装済み` へ。BE-0154 に印を付けました。フォロー
  アップ：既存のトラッキング Issue リンクの一度限りの修復（`gh issue edit`）はマージ後に行います（フラットな
  パスは `main` に入って初めて解決するためです）。

## 参考

- [BE-0078 — 状態駆動のロードマップフォルダ](../BE-0078-roadmap-status-folders/BE-0078-roadmap-status-folders-ja.md)：
  本項目が絞り込む対象です。索引の分類という発想は残し、フォルダ = 状態という発想だけを撤回します。
- [BE-0096 — 項目昇格で docs のロードマップリンクが腐るのを防ぐ](../BE-0096-docs-roadmap-link-integrity/BE-0096-docs-roadmap-link-integrity-ja.md)：
  本項目が構造的な修正で置き換える、検出と修復のアプローチです。`roadmaps/` と `docs/` のリンク面を
  対象にしていました。ゲート検査の検出器の部分は、昇格とは無関係な単純な打ち間違いを捕まえる用途で
  引き続き役立ちます。
- [BE-0109 — 未完了のロードマップ項目を GitHub Issue で所有権追跡する](../BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues-ja.md)：
  `issue_body()` のリンクが昇格のたびに壊れている、本項目のきっかけとなった具体的なバグを持つ仕組みです。
- [BE-0094 — 生成されるロードマップ状態ダッシュボード](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard-ja.md)：
  4 分類のグループ分けが状態だけからすでに機能しており、フォルダを必要としないことを示す証拠です。
- [BE-0149 — ロードマッププレースホルダーの書式ガードレールの隙間を塞ぐ](../BE-0149-roadmap-placeholder-format-guardrail/BE-0149-roadmap-placeholder-format-guardrail-ja.md)：
  本項目の書式検査の変更が土台にする、共有の `scripts/roadmap_ids.py` 述語モジュールと
  `scripts/check_roadmap_format.py` です。
- [BE-0154 — roadmap-promote をベース SHA から実行する](../BE-0154-roadmap-promote-base-sha/BE-0154-roadmap-promote-base-sha-ja.md)：
  本項目が廃止するスクリプトを対象にした提案です。本項目が着地した段階で見直すか終了するべきです。
- [`scripts/sync_roadmap_tracking_issues.py`](../../scripts/sync_roadmap_tracking_issues.py)、
  [`scripts/promote_roadmap_items.py`](../../scripts/promote_roadmap_items.py)、
  [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py)、
  [`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py)、
  [`scripts/new_roadmap_item.py`](../../scripts/new_roadmap_item.py)、
  [`scripts/check_roadmap_format.py`](../../scripts/check_roadmap_format.py)、
  [`scripts/roadmap_ids.py`](../../scripts/roadmap_ids.py)：本項目が触れるツール群です。

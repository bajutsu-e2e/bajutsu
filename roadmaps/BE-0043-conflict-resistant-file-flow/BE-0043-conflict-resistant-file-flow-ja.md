[English](BE-0043-conflict-resistant-file-flow.md) · **日本語**

# BE-0043 — コンフリクトに強いファイル流動（索引の生成・ファイル分割・git 衛生）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0043](BE-0043-conflict-resistant-file-flow-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0043") |
| 実装 PR | [#66](https://github.com/bajutsu-e2e/bajutsu/pull/66), [#69](https://github.com/bajutsu-e2e/bajutsu/pull/69), [#73](https://github.com/bajutsu-e2e/bajutsu/pull/73) |
| トピック | コントリビューターワークフロー |
<!-- /BE-METADATA -->

## はじめに

このリポジトリでは、多数のセッションとコントリビュータが同時に作業しています（[ai-development.md](../../docs/ja/ai-development.md) を参照）。
プルリクエストは、作業が意味的に重なる頻度よりも、はるかに頻繁にコンフリクトします。

本項目は、*マージコンフリクトを設計上の欠陥*として扱うことを提案します。
独立した変更が互いに素なファイルだけに触れるように、ファイルの流れを見直します。
見直しは 3 つの手順です。

- 手編集の共有台帳を、生成物に変えます。
- モノリスなモジュールとテストファイルを分割し、新規作業が共有ファイルを編集する代わりにファイルを追加するようにします。
- 現状で欠けている最小限の git 側の防御を入れます。具体的には、`rerere` とロックファイル用のマージドライバです。

## 動機

直近 200 コミットを調べると、コンフリクトのホットスポットは偶発的ではなく構造的です。
ほぼすべての PR が、同じ少数の共有ファイルを編集しています。

| 種別 | ファイル（変更頻度） | 衝突する理由 |
|---|---|---|
| 共有 append 台帳 | `roadmaps/README.md`（12）、`README-ja.md`（11） | 全ロードマップ PR が**同じ**トピック表に行を追記。独立な項目でもテキスト上で競合 |
| モノリスなモジュール | `cli.py`（20）、`orchestrator.py`（13）、`runner.py`（12）、`serve.py`（11）、`scenario.py`（10） | 全機能追加が 1 ファイルの近接行を編集 |
| 単一ファイルのテスト | `test_serve.py`（16）、`test_scenario.py`、`test_orchestrator.py` | 複数 PR が同じテストファイルに追記 |
| EN/JA の二重化と依存 | `README.md` / `README.ja.md`、`docs/*` ↔ `docs/ja/*`、`uv.lock`（59）/ `pyproject.toml` | 1 つの変更が常に 2 ファイルに及び、衝突面積が倍になる |

git 側の防御は、**何も入っていません**。

- `.gitattributes` がないため、マージドライバが未設定です。
- `rerere` は無効で、`make setup` に配線されていません。
- 履歴は `Merge branch 'main' into <branch>` だらけです。

長命ブランチがドリフトして統合が遅れるため、衝突は大きくなってから顕在化します。

BE ID の採番レースは、すでに解決済みです。
`BE-0043` プレースホルダと `scripts/allocate_roadmap_ids.py` が担い、[`roadmap-id`](../../.github/workflows/roadmap-id.yml) ワークフローが実行します。
しかし、**その ID を載せる索引表は手編集のまま**なので、索引表が最大の衝突源として残っています。
本提案は、この穴を塞ぎ、得られた教訓を一般化します。

## 詳細設計

効果の大きい順に、4 つの仕組みを示します。

1. **共有台帳を生成物にする。**
   各 `BE-NNNN/*.md` には、索引行に必要なメタデータ（`Status`、`Track`、`Topic`）がすでに揃っています。
   `scripts/build_roadmap_index.py` が、そのメタデータブロックを読んで `README.md` / `README-ja.md` の表を再生成します。
   `make check`（および CI）は、コミット済みの索引が最新かを検証し、差分があれば fail します。

   これでロードマップ PR は自分のディレクトリしか触らなくなり、索引は衝突しません。
   万一生成物が衝突しても、「再生成して上書き」で機械的に解決できます。解決は `rerere` が再適用します。

   同じフラグメント方式（towncrier 風の `changes/<id>.md`）は、将来 CHANGELOG を導入する場合の、定番の単一ファイル衝突も解消します。

2. **モノリスを分割し、新規作業がファイルを追加するようにする。**
   CLI コマンドは、すでに独立した `@app.command()` 関数です。
   これを `bajutsu/commands/<name>.py` へ移し、Typer サブアプリをディレクトリ走査で登録します。
   すると、新コマンドは**新規ファイル**になります。

   単一ファイルのテストは、`tests/<area>/test_<feature>.py` に分割します。

   `orchestrator.py` と `runner.py` のより深い分割は、本項目の範囲外とします。
   効果が大きく低リスクな勝ち筋は、CLI とテストだからです。

3. **最小限の git 側の防御を入れる。**
   - `.gitattributes` のエントリと、カスタムマージドライバを登録する `make setup` の手順を追加します。
     このドライバは、`uv.lock` を行単位でマージする代わりに、**衝突時に `uv lock` で再生成**します。
   - `make setup` で `rerere` を有効化（`git config rerere.enabled true`）し、一度解決した衝突を自動で再適用します。
   - `merge=union` は、真に append-only で行が独立な生成リストにのみ限定します。誤用はセマンティクスを壊すからです。

4. **プロセスと流れ。**
   PR は小さく短命に保ち、merge ではなく rebase します。
   CLAUDE.md は rebase を指示していますが、履歴には merge コミットが目立ちます。そこで、squash または rebase をポリシーにします。

   必要なら、GitHub の**マージキュー**を有効化します。
   マージキューは、PR をマージ後の状態に対して直列にテストするので、ドリフト由来の衝突を入口で止めます。

仕組み 1 と 3 だけで、計測された上位の衝突源のほとんどを、低コストで潰せます。
上位の衝突源とは、ロードマップ索引、ロックファイル、再解決です。
仕組み 2 と 4 は補強です。

## 検討した代替案

- **索引表に `merge=union`。**
  最も安価な選択肢です。
  しかし、union は両側の行を連結するため、行の重複や順序崩れ、静かに壊れた表を生みます。
  構造化されソートされた索引には不適切で、生成こそが正しい解です。
- **索引をコミットせず、ビルド成果物のみにする。**
  GitHub 上で閲覧できるロードマップ表が失われます。
  索引を生成して*コミットする*（CI で鮮度を検証）ことで、閲覧可能性を保てます。
  手編集の衝突も除けます。
- **何もせず、手作業の rebase に頼る。**
  これは現状そのものです。
  履歴が示すとおり、同時セッション数に対してスケールしません。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。
- **後日の注記：** 仕組み 1 が導入した `README.md` / `README-ja.md` の生成済み索引表と、その
  `merge=roadmap-index` git マージドライバは、
  [#1257](https://github.com/bajutsu-e2e/bajutsu/pull/1257) で撤去されました。ロードマップ
  ダッシュボードがその索引表の役割を引き継いだため、そこで衝突する共有の生成済みファイルはもう
  ありません。マージドライバも、何にも紐付かないまま残すのではなく撤去しました。仕組み 2〜4
  （ファイル分割、`uv.lock` のマージドライバ、`rerere`、小さな PR）には影響しません。

## 参考

- [ai-development.md](../../docs/ja/ai-development.md) — 並行作業ガイド（worktree、rebase、レーン分け）
- [`scripts/allocate_roadmap_ids.py`](../../scripts/allocate_roadmap_ids.py) — 本提案が一般化する
  既存の ID レース対策
- [CLAUDE.md](../../CLAUDE.md) — 「Working in parallel without breaking each other」

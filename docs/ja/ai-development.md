[English](../ai-development.md) · **日本語**

# AI エージェント（と人間）で並行開発する

> 複数のセッション —— 人間と AI エージェント —— が同時に同じリポジトリで作業しても、衝突したり
> 互いの機能を壊し合ったりしないための運用ガイド。要点は [`CLAUDE.md`](../../CLAUDE.md) にあり、
> このページはその詳細版。

設計全体は一つの性質に乗っている: **決定的なゲートが軽く・どこでも走り・CI（継続的インテグレーション）と完全に一致する** こと。
これがあるから作業を安全に並列展開できる —— どのブランチも単独で検証可能なので「ローカルで green」が
「CI で green」を確実に予測し、テストスイートがあるセッションの変更が別セッションの機能を壊したことを
捕まえる回帰ネットになる。

## ゲート

```bash
make check        # ruff check . + mypy bajutsu + pytest -q
```

[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) と同じ 3 ステップ。Python コアは
Simulator 不要なので Linux で数秒。変更を「完了」と呼ぶ前と push 前に必ず走らせる。実機 E2E
（macOS + Simulator）は別の重い経路で、このゲートには **含まれない**。

## 1 トピック 1 ブランチ

- `main` から派生: エージェントは `claude/<トピック>`、人間は `<user>/<トピック>`。
- 各ブランチは小さく単一目的に。小さな差分は速くマージでき、衝突もまれ。
- 人間が頼まない限り PR は作らない。自分のブランチに push し、PR は人間に開いてもらう。

## 赤いまま push しない

追跡対象の **pre-push フック** が `make check` を走らせ、失敗したら push を拒否する:

```bash
make setup   # uv sync --group dev + git フックの有効化（クローン直後に 1 回）
```

`core.hooksPath` はクローンごとのローカル設定で、clone/pull では伝播しない。だから既存クローンには
入っていない —— が、覚えておく必要はない。`make check`（および `make hooks`）が毎回これを張り直すので、
push 直前にゲートが自己修復される。Claude Code の web セッションでも
[`.claude/hooks/session-start.sh`](../../.claude/hooks/session-start.sh) が自動で設定する。
本当の緊急時は `git push --no-verify` で回避できるが、その後の CI が PR をゲートする。

挙動を変えたらテストも一緒に変える —— スイートは、あなたの変更から他の全セッションを守る契約。

## 早めに rebase し、小さな衝突のうちに統合する

```bash
git fetch origin
git rebase origin/main      # 他者のマージ済み作業を取り込む。衝突が小さいうちに解消
make check                  # rebase 後に再検証
```

こまめに rebase すれば、他セッションのマージ済み作業に早く出会える —— 衝突が 1〜2 行のうちに。
最後にまとめて絡まったマージを解くことにならない。

## worktree で同時セッションを隔離する

2 つのエージェントが同じチェックアウトを編集してはいけない。各セッションに専用の
[worktree](https://git-scm.com/docs/git-worktree) + ブランチを与える（`.git` は 1 つを共有）:

```bash
# メインのチェックアウトから
git worktree add ../bajutsu-<topic> -b claude/<topic> origin/main
cd ../bajutsu-<topic>
make setup                   # uv sync --group dev + この worktree のフック有効化
```

ブランチがマージ（または破棄）されたら片付ける:

```bash
git worktree remove ../bajutsu-<topic>
```

生成物・スクラッチ出力（`runs/`、`tmp/`、`.venv/`、ビルド成果物）は意図的に gitignore 済み。
コミットに混ぜず、worktree を独立に保つ。

## 自分のレーンに留まる

タスクに必要なファイルだけ触る。アーキテクチャは層状（scenario → orchestrator → driver →
backend。[architecture](architecture.md) 参照）なので、ほとんどのタスクは 1 層に収まる。多数の
モジュールを横断せざるを得ない変更 —— 抽象 **Driver API**、シナリオ **スキーマ**、共有 config の
形を変えるなど —— は、他セッションがその面を避けられる（または着地を待てる）よう、動く的の上に
積み上げる前に先に宣言する。

調整が必要な共有面:

| 面 | ファイル | 共有される理由 |
|---|---|---|
| Driver API | [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) | 全 backend と orchestrator が依存 |
| シナリオスキーマ | [`bajutsu/scenario.py`](../../bajutsu/scenario.py) | ハブとなる成果物。codegen/runner/report が読む |
| config の形 | [`bajutsu/config.py`](../../bajutsu/config.py) | 全コマンドが解決する per-app レイヤリング |

## CI がブランチを正直に保つ

CI は全 PR で同じゲートを走らせ、`concurrency: ci-${{ github.ref }}` と `cancel-in-progress` を使う。
同じブランチへの再 push は古い run を積み上げず置き換える。各々単独で通る 2 つの PR でも挙動は衝突し
うる —— マージこそが両者の出会う場であり、だからこそ判定者は決定的なテストスイート（LLM（大規模言語モデル）でも
人間の目視でもない）。スイートを意味あるものに保ち、ブランチを rebase し続ければ、並行作業は破綻なく合成
される。

## ロードマップ項目: BE ID（厳守）

ロードマップは [`roadmap/`](../roadmap/README-ja.md) 配下に**1 項目 1 ディレクトリ**で置く。各項目は
`docs/roadmap/BE-NNNN-<slug>/` ディレクトリに、英語版 `BE-NNNN-<slug>.md` と日本語版
`BE-NNNN-<slug>-ja.md`（ID・slug は同一）を入れる。**BE** は *Bajutsu Evolution*、`NNNN` は
**ゼロ詰め 4 桁・単調増加**の ID。

ロードマップ項目を追加するとき:

1. **次の ID を採番** = 既存の最大 `BE-NNNN` + 1。現在の最大は次で確認する:
   ```bash
   ls -d docs/roadmap/BE-*/ | sort | tail -1
   ```
   番号の再利用・飛ばし・当て推量は禁止。
2. **項目ディレクトリと両言語のファイルを作成** — `docs/roadmap/BE-NNNN-<slug>/BE-NNNN-<slug>.md`
   （英語）と `docs/roadmap/BE-NNNN-<slug>/BE-NNNN-<slug>-ja.md`（日本語・同一 ID & slug）— そして
   **両方**のインデックスページの該当トピック表に行を追加する
   （[en](../roadmap/README.md) / [ja](../roadmap/README-ja.md)）。
3. **ID は不変**。既存項目を採番し直さない —— 状態が変わっても、完了しても、表から削除しても。
   一度割り当てた BE ID は、その項目を永遠に指す。

各ファイルは **Swift-Evolution の proposal フォーマット**に従う —— メタデータブロック（`* 提案`・
`* 状態`・`* トラック`・`* トピック`・任意で `* 由来`）の後に `## はじめに` / `## 動機` /
`## 詳細設計` / `## 検討した代替案` / `## 参考`。埋められる範囲だけ記入し、不明は `TBD`。**状態**
フィールドが管理トラック（index のどのセクションに並ぶか）を決める:

| 状態 | トラック |
|---|---|
| `実装済み`・`可決・実装中` | **可決済み** —— 意思決定・実装の記録 |
| `提案`・`提案（保留）` | **提案** —— 検討中 |

項目が進んだら、ファイル名を変えるのではなく**状態を更新**する（index のグループ間で行を移す）。
マイルストーン M1–M4 は `BE-0001`–`BE-0004`（可決・実装済み）。

これはエージェントが従うべき厳格なルール。短縮版は [`CLAUDE.md`](../../CLAUDE.md) にある。

## ドキュメントの書き方（全ドキュメント・両言語に適用）

このルールはすべてのドキュメント —— `docs/` の英語版と `docs/ja/` の日本語ミラー —— に、そして
今後のすべての更新（新規ファイルに限らない）に適用される。エージェントは厳守すること。作業の
報告・要約のときも同じく適用される。

- **自然な文章で書く。** 日本語ドキュメントは自然な日本語、英語ドキュメントは自然な英語で書く。
  ミラーは逐語的な置き換えではなく、その言語で同じ内容を自然に伝えるものにする。
- **造語禁止。** 必ず一般的で広く使われている技術用語・普通の言葉を使う。語を勝手に作ったり、
  通常持たない意味に拡張したりしない。
- **不自然な翻訳禁止。** 用語は一般的な訳語を使う。訳すと不自然になる場合は、訳さず元の用語
  （多くは英単語。例: `selector`・`actuator`・`backend`・`assertion`）をそのまま使う。
- **省略禁止・単体で完結。** 読者がその文書単体で理解できること。略語は初出で展開し、用語に必要な
  文脈を与え、他ページを先に読んでいる前提にしない。

このルールの短縮版は [`CLAUDE.md`](../../CLAUDE.md) にある。

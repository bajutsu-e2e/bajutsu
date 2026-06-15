[English](../ai-development.md) · **日本語**

# AI エージェント（と人間）で並行開発する

> 複数のセッション —— 人間と AI エージェント —— が同時に同じリポジトリで作業しても、衝突したり
> 互いの機能を壊し合ったりしないための運用ガイド。要点は [`CLAUDE.md`](../../CLAUDE.md) にあり、
> このページはその詳細版。

設計全体は一つの性質に乗っている: **決定的なゲートが軽く・どこでも走り・CI と完全に一致する** こと。
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
git fetch origin            # まず必ず main を追従 —— 古い ref ではなく最新から派生する
git worktree add ../bajutsu-<topic> -b claude/<topic> origin/main
cd ../bajutsu-<topic>
make setup                   # uv sync --group dev + この worktree のフック有効化
```

`git fetch origin` は省略不可。`origin/main` は fetch したときだけ進むローカルの追跡 ref なので、
これを飛ばすと前回 fetch した時点の古い main から worktree を切ってしまい、他セッションが既に
マージで解消したはずの衝突を再び持ち込む。fetch してから、新しい `origin/main` を基点に派生する。

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
うる —— マージこそが両者の出会う場であり、だからこそ判定者は決定的なテストスイート（LLM でも人間の
目視でもない）。スイートを意味あるものに保ち、ブランチを rebase し続ければ、並行作業は破綻なく合成
される。

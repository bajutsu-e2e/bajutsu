[English](CONTRIBUTING.md) · **日本語**

# Bajutsu への貢献

貢献に関心をお寄せいただきありがとうございます。このページは人間の貢献者向けの入口です。人間と
AI エージェントの双方が従う詳しい作業協定は [`CLAUDE.md`](CLAUDE.md) と、その詳細版である
[`docs/ai-development.md`](docs/ai-development.md) にあります。このページは内容を再掲せずそちらへ
案内する役割に徹し、二つの記述が食い違わないようにしています。

はじめての方は、Bajutsu が何かを知るために [`README.ja.md`](README.ja.md) を、実際に手を動かす
入門として [getting-started チュートリアル](docs/ja/getting-started.md) をご覧ください。

## 開発環境のセットアップ

Bajutsu のロジックコアは Python **3.13** で、[uv](https://docs.astral.sh/uv/) で管理します。

```bash
uv sync --group dev   # .venv ＋ 依存関係 ＋ 開発ツール
make setup            # 上記に加え、追跡対象の git フックを配線（新しいクローンで一度だけ実行）
```

API キーが必要なのは AI パス（`record`、`run --dismiss-alerts`）だけです。
[`.env.example`](.env.example) を `.env`（gitignore 済み）にコピーして `ANTHROPIC_API_KEY` を
設定してください。下記の決定論的なゲートはシークレットも Simulator も不要です。

## ゲート（これが契約です）

Python コアは Simulator を必要としないため、ゲートは高速で、Linux を含むどこでも動きます。
**変更を完了とする前と、push する前に**必ず実行してください。

```bash
make check   # lock-check ＋ format-check ＋ lint ＋ lint-sh ＋ lint-actions ＋ typecheck ＋ test
```

これは [CI](.github/workflows/ci.yml) と完全に一致するため、「ローカルで緑」が「CI で緑」を予測
します。追跡対象の [pre-push フック](.githooks/pre-push) が `make check` を実行し、赤い push を
拒否します。挙動を変えたら、対応するテストも一緒に変えてください。テストスイートは、他の貢献者の
成果を守る回帰検出網です。

実機での E2E（macOS ＋ Simulator）はより重い別経路で、このゲートには**含まれません**。
`make -C demos/features e2e`（事前に `make deps`）で実行します。コア作業をこれで止めないで
ください。

## ブランチ・コミット・プルリクエスト

- **1 ブランチ 1 トピック。** `main` から `<user>/<topic>`（エージェントは `claude/<topic>`）で
  分岐します。各ブランチは小さく単一目的に保ってください。小さな差分は速くマージでき、衝突も
  まれです。
- **ドリフトさせず、リベースする。** push 前に `git fetch origin && git rebase origin/main` を
  行い、その後 `make check` を再実行します。
- **コミットメッセージ**は命令形でスコープ付きにします（`feat(run): …`、`fix(record): …`、
  `docs: …`）。
- **プルリクエストのタイトルと本文は、作業中に使った言語にかかわらず常に英語**にします。履歴を
  誰にとっても読みやすく保つためです。
- PR がロードマップ項目を実装する場合は、**タイトル先頭に角括弧付きで ID** を付け（例:
  `[BE-0017] feat(mcp): add MCP server`）、その項目の markdown（両言語ファイル）に PR への
  リンクを追加します。ロードマップ項目に紐づかない PR は、スコープ付きのタイトルのままにします。
- このリポジトリは複数のセッションが並行して作業します。worktree、`uv.lock` のマージドライバ、
  その他の並行作業モデルについては [`docs/ai-development.md`](docs/ai-development.md) を参照して
  ください。

## ロードマップ項目（BE ID）

大きめの機能は **Bajutsu Evolution** 項目として
[`roadmaps/`](roadmaps/README.md) で管理します。項目ごとに `roadmaps/<implemented|proposals>/BE-NNNN-<slug>/`
ディレクトリを 1 つ作り（出荷済みは `implemented/`、それ以外は `proposals/` 配下）、英語ファイルとその日本語版を
Swift-Evolution の提案書式で置きます。ID は
不変で単調増加し、索引の表は手編集せず生成します。正確な手順（ID の採番、両言語ファイル、
`make roadmap-index`）は [`docs/ja/ai-development.md`](docs/ja/ai-development.md) に従ってください。

## ドキュメント

ドキュメントはバイリンガルです。英語は [`docs/`](docs/README.md)、日本語ミラーは
[`docs/ja/`](docs/ja/README.md) にあります。文書化された挙動を変えたら**両方を更新**してください。
それぞれの言語で自然な散文を書き、確立した技術用語を用い（造語や不自然な強制翻訳をしない）、各
ページを自己完結させます。詳しい指針は
[ドキュメントの書き方](docs/ja/ai-development.md)にあります。

## 守るべき原則（破ってはいけません）

これらはプロジェクト全体が依拠する設計上の不変条件です。全リストは
[`CLAUDE.md`](CLAUDE.md#prime-directives-do-not-violate) にあります。

1. **AI は author（作成者）であり failure investigator（失敗の調査役）であって、judge（判定者）
   ではない。** `run` は完全に決定論的で、合否は機械検証可能なアサーションだけから決まります。
   Tier-2 の run/CI ゲートに LLM 呼び出しを持ち込まないでください。
2. **決定論ファースト。** 固定の `sleep` を使わず（条件待ちのみ）、曖昧なセレクタは「最初に一致した
   ものをタップ」せず即座に失敗します。
3. **アプリ非依存。** アプリごとの違いは設定（`apps.<name>`）に置き、ツール・ドライバ・ランナーは
   アプリをまたいで変わりません。

[English](CONTRIBUTING.md) · **日本語**

# Bajutsu への貢献

貢献に関心をお寄せいただきありがとうございます。このページは人間の貢献者向けの入口です。人間と
AI エージェントの双方が従う詳しい作業協定は [`CLAUDE.md`](CLAUDE.md) と、その詳細版である
[`docs/ai-development.md`](docs/ai-development.md) にあります。このページは内容を再掲せずそちらへ
案内する役割に徹し、二つの記述が食い違わないようにしています。

はじめての方は、Bajutsu が何かを知るために [`README.ja.md`](README.ja.md) を、Bajutsu を *動かす*
入門として [getting-started チュートリアル](docs/ja/getting-started/index.md) をご覧ください。最初の変更に
取りかかる準備ができたら、[コントリビューターワークフローチュートリアル](docs/ja/contributor-workflow-tutorial.md)
は、一つのアイデアが `/ideation` でマージ済みの提案になり、`/implement-be` でマージ済みの PR になるまでを
案内します。下のリファレンスに進む前に、そこから始めてください。

## 開発環境のセットアップ

Bajutsu のロジックコアは Python **3.13** で、[uv](https://docs.astral.sh/uv/) で管理します。

```bash
uv sync --group dev   # .venv ＋ 依存関係 ＋ 開発ツール
make setup            # 上記に加え、追跡対象の git フックを配線（新しいクローンで一度だけ実行）
```

API キーが必要なのは AI パス（`record`、`run --alert-handling`）だけです。
[`.env.example`](.env.example) を `.env`（gitignore 済み）にコピーして `ANTHROPIC_API_KEY` を
設定してください。下記の決定論的なゲートはシークレットも Simulator も不要です。

## ゲート（これが契約です）

Python コアは Simulator を必要としないため、ゲートは高速で、Linux を含むどこでも動きます。
**変更を完了とする前と、push する前に**必ず実行してください。

```bash
make check   # 決定的なゲート（ステップの全一覧は CLAUDE.md）
```

これは [CI](.github/workflows/ci.yml) と完全に一致するため、「ローカルで緑」なら「CI でも緑」だと
見込めます。追跡対象の [pre-push フック](.githooks/pre-push) が `make check` を実行し、赤い push を
拒否します。挙動を変えたら、対応するテストも一緒に変えてください。テストスイートは、他の貢献者の
成果を守る回帰検出網です。

実機での E2E（macOS ＋ Simulator）はより重い別経路で、このゲートには**含まれません**。
`make -C demos/showcase run-swiftui`（事前に `make deps`）で実行します。コア作業をこれで止めないで
ください。

## ブランチ、コミット、プルリクエスト

- **1 ブランチ 1 トピック。** `main` から `<user>/<topic>`（エージェントは `claude/<topic>`）で
  分岐します。各ブランチは小さく単一目的に保ってください。小さな差分は速くマージでき、衝突も
  まれです。
- **ドリフトさせずにリベースする。** push する前に `git fetch origin && git rebase origin/main`
  を行い、その後 `make check` を再実行します。
- **コミットメッセージ**は命令形でスコープ付きにします（`feat(run): …`、`fix(record): …`、
  `docs: …`）。
- **プルリクエストのタイトルと本文は、作業中に使った言語にかかわらず常に英語**にします。履歴を
  誰にとっても読みやすく保つためです。
- **PR のタイトルと本文は 1 つの型に従います。** タイトルを 1 行で言い換えるだけの本文にせず丁寧に
  書くこと、ロードマップ項目を実装する PR には `[BE-NNNN]` 接頭辞と項目からの逆リンクを付けること
  です。完全な規約は
  [`docs/ja/ai-development.md`](docs/ja/ai-development.md#プルリクエスト-タイトルと本文) にあります。
- **レビューには 1 件ずつ、根拠とともに返信します。** まとめて 1 つの返信では足りません。詳しくは
  [`docs/ja/ai-development.md`](docs/ja/ai-development.md#pr-レビューコメントへの対応) を
  参照してください。
- このリポジトリは複数のセッションが並行して作業します。worktree、`uv.lock` のマージドライバ、
  その他の並行作業モデルについては [`docs/ai-development.md`](docs/ai-development.md) を参照して
  ください。

## ロードマップ項目（BE ID）

大きめの機能は **Bajutsu Evolution** 項目として [`roadmaps/`](roadmaps/README.md) で管理します。
正確な手順（ディレクトリ構成、ID の採番、両言語ファイル、書式）は
[`docs/ja/ai-development.md`](docs/ja/ai-development.md#ロードマップ項目-be-id厳守) に従って
ください。

## ドキュメント

ドキュメントはバイリンガルです。英語は [`docs/`](docs/README.md)、日本語ミラーは
[`docs/ja/`](docs/ja/README.md) にあります。文書化された挙動を変えたら**両方を更新**してください。
詳しい指針は
[ドキュメントの書き方](docs/ja/ai-development.md#ドキュメントの書き方全ドキュメント両言語に適用)
にあります。

## 守るべき原則（破ってはいけません）

これらはプロジェクト全体が依拠する設計上の不変条件です。全リストは
[`CLAUDE.md`](CLAUDE.md#prime-directives-do-not-violate) にあります。

1. **AI は author（作成者）であり failure investigator（失敗の調査役）であって、judge（判定者）
   ではない。** `run` は完全に決定論的で、合否は機械検証可能なアサーションだけから決まります。
   Tier-2 の run/CI ゲートに LLM 呼び出しを持ち込まないでください。
2. **決定論ファースト。** 固定の `sleep` を使わず（条件待ちのみ）、曖昧なセレクタは「最初に一致した
   ものをタップ」せず即座に失敗します。
3. **アプリ非依存。** アプリごとの違いは設定（`targets.<name>`）に置き、ツール、ドライバ、ランナーは
   アプリをまたいで変わりません。

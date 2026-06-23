[English](../ai-development.md) · **日本語**

# AI エージェント（と人間）で並行開発する

> 複数のセッション（人間と AI エージェント）が同時に同じリポジトリで作業しても、衝突したり
> 互いの機能を壊し合ったりしないための運用ガイドです。要点は [`CLAUDE.md`](../../CLAUDE.md) にあり、
> このページはその詳細版です。

設計全体の基盤となるのは一つの性質です。**決定的なゲートが軽く、どこでも走り、CI（継続的インテグレーション）と完全に一致する**ことです。
これがあるから作業を安全に並列展開できます。どのブランチも単独で検証可能なので「ローカルで green」が
「CI で green」を確実に予測し、テストスイートが、あるセッションの変更が別セッションの機能を壊したことを
捕まえる回帰ネットになります。

## ゲート

```bash
make check        # ruff check . + mypy bajutsu + pytest -q
```

[`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) と同じ 3 ステップです。Python コアは
Simulator 不要なので Linux で数秒で完了します。変更を「完了」と呼ぶ前と push 前に必ず走らせてください。実機 E2E
（macOS + Simulator）は別の重い経路で、このゲートには **含まれません**。

## 1 トピック 1 ブランチ

- `main` から派生します: エージェントは `claude/<トピック>`、人間は `<user>/<トピック>`。
- 各ブランチは小さく単一目的に保ちます。小さな差分は速くマージでき、衝突もまれです。
- 人間が頼まない限り PR は作りません。自分のブランチに push し、PR は人間に開いてもらいます。

## 赤いまま push しない

追跡対象の **pre-push フック** が `make check` を走らせ、失敗したら push を拒否します。

```bash
make setup   # uv sync --group dev + git フックの有効化（クローン直後に 1 回）
```

`core.hooksPath` はクローンごとのローカル設定で、clone/pull では伝播しません。そのため既存クローンには
入っていませんが、覚えておく必要はありません。`make check`（および `make hooks`）が毎回これを張り直すので、
push 直前にゲートが自己修復されます。Claude Code の web セッションでも
[`.claude/hooks/session-start.sh`](../../.claude/hooks/session-start.sh) が自動で設定します。
本当の緊急時は `git push --no-verify` で回避できますが、その後の CI が PR をゲートします。

挙動を変えたらテストも一緒に変えてください。スイートは、その変更から他の全セッションを守る契約です。

## 早めに rebase し、小さな衝突のうちに統合する

```bash
git fetch origin
git rebase origin/main      # 他者のマージ済み作業を取り込む。衝突が小さいうちに解消
make check                  # rebase 後に再検証
```

こまめに rebase すれば、他セッションのマージ済み作業に早く出会えます。衝突が 1〜2 行のうちに解消でき、
最後にまとめて絡まったマージを解く必要がなくなります。

`make hooks` は、それでも残る衝突の痛みを和らげる 2 つのローカル git 設定も自己修復します（BE-0043）。手で
設定する必要はありません。

- **`uv.lock` のマージドライバ**（[`scripts/merge-uv-lock.sh`](../../scripts/merge-uv-lock.sh)、
  [`.gitattributes`](../../.gitattributes) でマッピング）。競合時に resolver の出力を行マージするのではなく
  **`pyproject.toml` から `uv.lock` を再生成**します。`pyproject.toml` 自体が競合している場合は `uv lock` が
  失敗し、git は `uv.lock` を競合のまま残します。先に `pyproject.toml` を解決してから再マージしてください。
- **`rerere`**（記録した解決の再利用）。一度解決した衝突は、同じ衝突が次に現れたときに自動で再適用されます。

`core.hooksPath` と同様、これらは clone/pull が引き継がないクローンごとのローカル git 設定なので、
`make check` / `make setup` が毎回再配線します。

## worktree で同時セッションを隔離する

2 つのエージェントが同じチェックアウトを編集してはいけません。各セッションに専用の
[worktree](https://git-scm.com/docs/git-worktree) + ブランチを与えます（`.git` は 1 つを共有）。

```bash
# メインのチェックアウトから
git fetch origin            # まず必ず main を追従。古い ref ではなく最新から派生する
git worktree add ../bajutsu-<topic> -b claude/<topic> origin/main
cd ../bajutsu-<topic>
make setup                   # uv sync --group dev + この worktree のフック有効化
```

`git fetch origin` は省略できません。`origin/main` は fetch したときだけ進むローカルの追跡 ref です。
これを飛ばすと前回 fetch した時点の古い main から worktree を切ることになり、他セッションが既に
マージで解消したはずの衝突を再び持ち込みます。fetch してから、新しい `origin/main` を基点に派生してください。

ブランチがマージ（または破棄）されたら片付けます。

```bash
git worktree remove ../bajutsu-<topic>
```

生成物とスクラッチ出力（`runs/`、`tmp/`、`.venv/`、ビルド成果物）は意図的に gitignore 済みです。
コミットに混ぜず、worktree を独立に保ってください。

## 自分のレーンに留まる

タスクに必要なファイルだけ触ります。アーキテクチャは層状です（scenario → orchestrator → driver →
backend。[architecture](architecture.md) 参照）。ほとんどのタスクは 1 層に収まります。多数の
モジュールを横断せざるを得ない変更（抽象 **Driver API**、シナリオ **スキーマ**、共有 config の
形を変えるなど）は、他セッションがその面を避けられる（または着地を待てる）よう、事前に宣言してください。

調整が必要な共有面:

| 面 | ファイル | 共有される理由 |
|---|---|---|
| Driver API | [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) | 全 backend と orchestrator が依存 |
| シナリオスキーマ | [`bajutsu/scenario.py`](../../bajutsu/scenario.py) | ハブとなる成果物。codegen/runner/report が読む |
| config の形 | [`bajutsu/config.py`](../../bajutsu/config.py) | 全コマンドが解決する per-target レイヤリング |

## CI がブランチを正直に保つ

CI は全 PR で同じゲートを走らせ、`concurrency: ci-${{ github.ref }}` と `cancel-in-progress` を使います。
同じブランチへの再 push は古い run を積み上げず置き換えます。各々単独で通る 2 つの PR でも挙動は衝突し
えます。マージこそが両者の出会う場です。だからこそ判定者は決定的なテストスイートであり、LLM（大規模言語モデル）でも
人間の目視でもありません。スイートを意味あるものに保ち、ブランチを rebase し続ければ、並行作業は破綻なく合成
されます。

## プルリクエスト: タイトルと本文

PR は、人間が頼まない限り自分では開きません（[1 トピック 1 ブランチ](#1-トピック-1-ブランチ)を参照）。
自分のブランチに push し、PR は人間に開いてもらいます。ただし PR を書き起こすとき、あるいは人間が
開くためのタイトルと本文を用意するときは、以下の型に従ってください。これはこのリポジトリが実際に
マージしてきた PR から型を起こしたもので、これに合わせると履歴の体裁がそろい、レビュアーは毎回
同じ情報を同じ場所で見つけられます。**タイトルと本文は、作業に使った言語にかかわらず常に英語**で
書きます。

### タイトル

スコープ付きの [Conventional Commits](https://www.conventionalcommits.org/) の subject を 1 行で
書きます。先頭コミットの subject と同じ形です。

```
[BE-NNNN] type(scope): summary
```

- **`type(scope):`**：conventional-commit の type（`feat`、`fix`、`docs`、`chore`、`ci`、
  `refactor`、`test`）と、触れる領域（`run`、`web`、`codegen`、`audit`、`roadmap`、`hooks`、`ja`
  など）です。例: `feat(audit):`、`fix(hooks):`、`docs(roadmap):`。
- **summary**：命令形、小文字始まり、末尾のピリオドなしで、レビュアーが一目で読める 1 行にします。
  ロードマップ提案なら `docs(roadmap): propose <提案の内容>` の形です。
- **`[BE-NNNN]` の接頭辞**：PR がロードマップ項目に紐づくときだけ、スコープ付き subject の前に角括弧で
  付けます（例: `[BE-0017] feat(mcp): add MCP server`）。ロードマップ項目に紐づかない PR は、スコープ
  付き subject のままにします。PR が新しいロードマップ項目を*導入する*場合は、リテラルの `[BE-XXXX]`
  プレースホルダで書き起こします。`roadmap-id` ワークフローが、割り当てた番号へ書き換えます
  （[ロードマップ項目](#ロードマップ項目-be-id厳守)を参照）。

### 本文

必須は 2 つ——`## Summary` と検証の記述——で、残りのセクションは変更が必要とする範囲で、以下の順に
足します。詳しさは差分に合わせます。1 ファイルの修正なら、短い Summary と緑の数値で足ります。横断的な
機能なら全セクションが要ります。文章は、マージ済みの PR でこれらのセクションが実際に読める形にならって
書きます。現在形で、たどり着くまでの経緯を語るのではなく、変更が*何であるか*を述べます。**太字**は、変更の
鍵となる少数の名詞に限り、文全体には使いません。変更一覧では、繰り返し現れる `**パス** — 何をするか、
そしてなぜこの継ぎ目か` の形に従い、単なる編集ではなく設計上の選択を書きます。

よく現れるセクションと、それぞれが担う内容は次のとおりです。

- **`## Summary`**（必須）：PR が何をするか、そして*なぜ重要か*を、短い段落 1〜3 個で書きます。鍵となる
  名詞は**太字**にします。経緯ではなく変更そのものから書き始めます。より大きな項目の一部を成す PR なら、
  どの一部かを示し、マージによってその項目の `Status` がどう動くか（例: *Accepted, in progress* へ移る）を
  述べます。
- **`## What changed`** / **`## Changes`**：ファイルまたはコンポーネントごとに箇条書き 1 個を当て、
  **パスまたはコンポーネント名を太字**にし、em ダッシュに続けて、何をするか*となぜこの継ぎ目か*（単なる
  編集内容ではなく設計上の選択）を書きます。新規ファイルには `(new)` を付けます。コミット単位ではなく
  コンポーネント単位でまとめます。レビュアーが読むのは、たどり着いた結果であって経路ではありません。
- **`## Prime-directive compliance`**：変更がツールの挙動やランタイムに触れるときに置きます。判定に
  モデルを介在させないこと、`run` / CI ゲートが決定論的なままであること、アプリごとの違いは設定に
  留まることを、はっきり述べます。変更が関わる[prime
  directive](../../CLAUDE.md#prime-directives-do-not-violate)ごとに 1 行です。ドキュメントのみ、または
  基盤のみの PR は、その旨を 1 文で述べれば足ります。
- **`## Scope`**（多くは *Scope (deferred to …)*）：この PR に意図的に**含めない**ものを書きます。境界を
  レビュアーに推測させないためです。より大きな項目の一部なら、後続の一部が負っている残りを挙げます。
- **`## Verification`** / **`## Testing`** / **`## Test plan`**（必須、いずれかの形で）：`make check` が
  緑であることを、出力した具体的な数値（`N passed, coverage X%`）とともに示し、新しいテストが何を
  カバーするかを 1 文で書きます。ゲートが*動かせない*もの（ワークフローの実行時挙動、Simulator でしか
  通らない経路）は明記し、何が証明され何が証明されていないかをレビュアーに伝えます。ここでの正確さが要で、
  テストしていない経路をテストしたかのように書きません。
- ロードマップ提案の場合：**`## Files`**（両言語のペア）と **`## BE ID allocation`**（`BE-XXXX`
  プレースホルダについての注記。番号はワークフローが採番するので、手で書き換えません）。
- **`## Notes`**：注意点、関連する、あるいは番号を争う open な PR、予期されるマージ衝突とその解消方法。

本文の末尾は、引用した項目への参照リンク（`[BE-0049]: roadmaps/…`）と、フッタ
`🤖 Generated with [Claude Code](https://claude.com/claude-code)` で締めます。GitHub の `> [!NOTE]`
コールアウトは、レビュアーが見落としてはいけない注意点に限って使います。

小さな修正なら、必須の 2 つだけで足ります。

```markdown
## Summary

Follow-up to #189: `session-start.sh` could abort the hook — and the session — under `set -e`
when `CLAUDE_PROJECT_DIR` is unset. This makes the project-dir discovery best-effort.

## Verification

`shellcheck` clean; `make check` green (1059 passed, coverage 87.4%). Repro'd that the hook now
logs the skip and exits 0 instead of aborting.
```

機能や、ロードマップ項目に紐づく PR は、全体の型を埋めます。

```markdown
## Summary

The **<slice>** of [BE-NNNN]. <What it does and why it matters, key nouns in bold.> This moves
the item to **Accepted, in progress**.

## What changed

- **`bajutsu/<file>.py` (new)** — <what it does, and why this seam>.
- **`bajutsu/<other>.py`** — <the change, and the design choice behind it>.
- **docs (en/ja)** — <what was documented>.

## Prime-directive compliance

No model is consulted on the verdict; the `run` / CI gate stays deterministic; per-target
differences stay in config.

## Scope (deferred to later BE-NNNN slices)

<What is deliberately not in this PR.>

## Verification

`make check` green: format-check / ruff / mypy (Success) / test (N passed, coverage X%). New
tests cover <…>.

[BE-NNNN]: roadmaps/proposals/BE-NNNN-<slug>/BE-NNNN-<slug>.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

このルールの短縮版は [`CLAUDE.md`](../../CLAUDE.md) にあります。

## PR レビューコメントへの対応

レビューには 1 件ずつ返信します。返信するのは、**その pull request の担い手——人間の貢献者でも AI
エージェントでも同じ**です。レビュアー（GitHub Copilot などの AI レビュアー、あるいは人間）が
コメントを残したときは、すべてのコメントを解消するまで作業を続け、そのうえで**コメント 1 件ごとに個別に
返信します**。PR にまとめて 1 つ返信するだけでは足りません。指摘が出たスレッドそのものに解消の記録が
残るよう、コメントのスレッドそれぞれに返信します。

返信では次の 2 点を必ず示します。

- **その指摘に対応したこと** — コードを修正したのか、意図して見送ったのか。
- **その根拠** — 解消にあたる具体的な変更（何をどこで変えたか。コミットやファイル・行を挙げます）。
  変更しない場合は、その指摘が当てはまらない具体的な理由。

「対応しました」や 👍 だけではこの規範を満たしません。根拠があってこそ、後でスレッドを読む人が解消の
妥当性を確認できます。返信は短く事実に即して書きます。重要なのは根拠であって、説明の量ではありません。

コメントへの対応に迷うとき（修正の解釈が複数ありうる、あるいはアーキテクチャ上重要な箇所に触れる場合）
は、当て推量せず確認します。AI エージェントは自分を動かしている人間に、人間の貢献者はレビュアーや
メンテナに確認し、判断が出るまでそのスレッドは開いたままにします。

## ロードマップ項目: BE ID（厳守）

ロードマップは [`roadmaps/`](../../roadmaps/README-ja.md) 配下に**1 項目 1 ディレクトリ**で置きます。各項目は
`roadmaps/<implemented|proposals>/BE-NNNN-<slug>/` ディレクトリに、英語版 `BE-NNNN-<slug>.md` と日本語版
`BE-NNNN-<slug>-ja.md`（ID と slug は同一）を入れます。**BE** は *Bajutsu Evolution* の略で、`NNNN` は
**ゼロ詰め 4 桁で単調増加する** ID です。出荷済み（`状態: 実装済み`）の項目は `roadmaps/implemented/`、
それ以外の進行中のものは `roadmaps/proposals/` に置きます。

ロードマップ項目を追加するとき:

1. **次の ID を採番する** = 既存の最大 `BE-NNNN` + 1（両方のフォルダを数えます）。現在の最大は次で確認します。
   ```bash
   ls -d roadmaps/{implemented,proposals}/BE-*/ | sort | tail -1
   ```
   番号を再利用したり、飛ばしたり、当て推量したりしてはいけません。
2. **項目ディレクトリと両言語のファイルを作成する**（新規項目はまず提案なので `roadmaps/proposals/` の下に）。
   すなわち、`roadmaps/proposals/BE-NNNN-<slug>/BE-NNNN-<slug>.md`
   （英語）と `roadmaps/proposals/BE-NNNN-<slug>/BE-NNNN-<slug>-ja.md`（日本語、ID と slug は同一）です。**インデックス表は
   手で編集しません。** これは各項目自身のメタデータから生成されます。`make roadmap-index`（または
   `python scripts/build_roadmap_index.py`）を実行して、**両方**のインデックスページ
   （[en](../../roadmaps/README.md) / [ja](../../roadmaps/README-ja.md)）の `<!-- GENERATED:* -->` マーカー間の表を
   再生成してください。項目の `Track` + `Topic` が並ぶセクションを決めるので、既存トピックの項目なら表の手編集は
   不要です。コミット済みインデックスがズレると `tests/test_roadmap_index.py`（`make test` が実行）が落ちます。
   まったく新しいトピックの場合は、マーカー付きセクションとスクリプトの `Section` エントリも追加します。
3. **ID は不変**。既存項目を採番し直しません。状態が変わっても、完了しても、表から削除しても同じです。
   一度割り当てた BE ID は、その項目を永遠に指します。

手で採番すると競合するので、その必要はありません。`roadmap-id` ワークフローが PR の時点で ID を割り当て、
2 つの防御（[BE-0061](../roadmaps/implemented/BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening-ja.md)）が
`main` *と* すべての open な PR にわたって ID を一意に保ちます。**採番は原子的に予約します。** 払い出す各 ID は、
GitHub の create-ref API を通じて `refs/be-claims/<NNNN>` という git ref として確保されます。これは ref が
すでに存在すると失敗する compare-and-set なので、同じ時間帯に採番する 2 つのブランチが番号を両取りすることは
できず、敗者が選び直します。claim は PR が close されると解放され（その ID はその時点で `main` に載っているか、
放棄されています）、日次のスイープが漏れを回収します。**修復はそれでもすり抜けるもの**（手打ちした具体的な
ID、この仕組みより前から存在するブランチ）への砦です。`roadmap-id-repair` ワークフローが、`main` への push 時
と日次のスケジュールで、open なロードマップ PR すべてに対して採番をやり直します。PR が導入する項目（slug が
まだ `main` に無い項目）で番号がすでに取られていれば、次の空き番号を割り当て、ディレクトリを移動してファイルや
相互参照、PR タイトルを書き換え、修正をブランチへ push します（ローカルで同じ処理を行うなら
`make roadmap-id-repair`）。権威（争われた番号を誰が保つか）は `main` を第一とし（merge 済みの項目が常に
勝つ）、無ければその番号を持つ **open な PR のうち最小の番号** です。動くのは敗者だけです。ブランチが古い
`main` から引き継いだだけの項目（slug がすでにそこにある項目）は rebase に委ね、採番し直しません。`BE-XXXX`
プレースホルダで書き起こすのが依然として常道で、そもそも番号を当て推量せずに済みます。（fork からの PR は
push も claim 用 ref の作成もできないので、両方のワークフローは同一リポジトリの PR にのみ作用します。）

各ファイルは **Swift-Evolution の proposal フォーマット**に従います。メタデータブロック（`* 提案`、
`* Author`、`* 状態`、`* トラック`、`* トピック`、任意で `* 由来`）の後に `## はじめに` / `## 動機` /
`## 詳細設計` / `## 検討した代替案` / `## 参考` と続けます。埋められる範囲だけ記入し、不明は `TBD` とします。**Author は
GitHub のアカウント名で明記**します。書式は `* Author: [@handle](https://github.com/handle)` で、最初にその項目を
作成した人（AI 支援で書き起こした場合は、それを主導してコミットした人）のアカウントです。**状態**
フィールドが管理トラック（index のどのセクションに並ぶか）を決めます。

| 状態 | トラック |
|---|---|
| `実装済み` · `可決・実装中` | **可決済み**。意思決定と実装の記録 |
| `提案` · `提案（保留）` | **提案**。検討中 |

項目が進んだら**状態を更新**してインデックスを再生成します（行は自動で正しいグループへ移ります）。出荷時には
`状態: 実装済み` にすれば、**`roadmap-promote`** ワークフローが PR 上でディレクトリを `roadmaps/proposals/` から
`roadmaps/implemented/` へ（同じ ID と slug のまま）**移動**し、インデックスも再生成します（ローカルで行うなら
`make roadmap-promote`）。`状態` が項目の置き場所（どちらのサブディレクトリか）を決める唯一の基準であり、両者が
食い違うと `make test` が失敗します。そのため、出荷済みの項目が `roadmaps/proposals/` に置かれたまま merge される
ことはありません。マイルストーン M1–M4 は `BE-0001`–`BE-0004`（可決され、実装済み）です。

これはエージェントが従うべき厳格なルールです。短縮版は [`CLAUDE.md`](../../CLAUDE.md) にあります。

## ドキュメントの書き方（全ドキュメント、両言語に適用）

このルールはすべてのドキュメント（`docs/` の英語版と `docs/ja/` の日本語ミラー）に、そして
今後のすべての更新（新規ファイルに限らない）に適用されます。エージェントは厳守してください。作業を
報告したり要約したりするときも同じく適用されます。

- **自然な文章で書く。** 日本語ドキュメントは自然な日本語、英語ドキュメントは自然な英語で書きます。
  ミラーは逐語的な置き換えではなく、その言語で同じ内容を自然に伝えるものにします。
- **造語禁止。** 必ず一般的で広く使われている技術用語や普通の言葉を使います。語を勝手に作ったり、
  通常持たない意味に拡張したりしません。
- **不自然な翻訳禁止。** 用語は一般的な訳語を使います。訳すと不自然になる場合は、訳さず元の用語
  （多くは英単語。例: `selector`、`actuator`、`backend`、`assertion`）をそのまま使います。
- **省略禁止、単体で完結。** 読者がその文書単体で理解できるようにします。略語は初出で展開し、用語に必要な
  文脈を与え、他ページを先に読んでいる前提にしません。
- **日本語の文章は `japanese-tech-writing` スキルに従う。** 日本語版を新規に書くときも、英語版を
  `docs/ja/`（やロードマップの `*-ja.md`）へ翻訳するときも、[`japanese-tech-writing`](../../.claude/skills/japanese-tech-writing/)
  を適用します。これがこのリポジトリにおける日本語の文章の正式なスタイルであり、翻訳は英語の逐語訳ではなく、
  この規範に沿った自然な日本語にします。

このルールの短縮版は [`CLAUDE.md`](../../CLAUDE.md) にあります。

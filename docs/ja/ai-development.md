[English](../ai-development.md) · **日本語**

# AI エージェント（と人間）で並行開発する

> 複数のセッション（人間と AI エージェント）が同時に同じリポジトリで作業しても、衝突したり
> 互いの機能を壊し合ったりしないための運用ガイドです。要点は [`CLAUDE.md`](../../CLAUDE.md) にあり、
> このページはその詳細版です。

> **貢献が初めての方は、[コントリビューターワークフローチュートリアル](contributor-workflow-tutorial.md)から
> 始めてください。** 最初の提案と最初の実装を、手を動かしながら辿る実地の手引きです。このページは、その規則
> （ゲート、ブランチ、BE ID のライフサイクル、モデルの階層、PR テンプレート）についてそこからリンクされる
> 詳細なリファレンスです。

設計全体を支えているのは、**決定的なゲートが軽く、どこでも走り、CI（継続的インテグレーション）と完全に一致する**という一つの性質です。
これがあるから作業を安全に並列展開できます。どのブランチも単独で検証可能なので「ローカルで green」が
「CI で green」を確実に予測し、テストスイートが、あるセッションの変更が別セッションの機能を壊したことを
捕まえる回帰ネットになります。

## ゲート

```bash
make check
```

各ステップは [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml) と完全に一致します。
ステップの現在の一覧は、ゲートの唯一の拠り所である [`CLAUDE.md`](../../CLAUDE.md) にあります。Python
コアは Simulator 不要なので Linux で数秒で完了します。変更を「完了」と呼ぶ前と push 前に必ず走らせて
ください。実機 E2E（macOS + Simulator）は別の重い経路で、このゲートには **含まれません**。

## 1 トピック 1 ブランチ

- `main` から派生します。エージェントは `claude/<トピック>`、人間は `<user>/<トピック>`。
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

同じ `core.hooksPath` は、追跡対象の **commit-msg フック**（[`.githooks/commit-msg`](../../.githooks/commit-msg)、BE-0069）も配線します。subject がスコープ付きの conventional subject（`type(scope): …` または `docs: …`）でないコミットをブロックし、機械的な規約をレビューではなくコミット時に捕まえます。意図的に狭く、merge / revert / fixup / squash のコミットは通し、`uv` が PATH に無ければ no-op です。単発の回避は `git commit --no-verify` です。

挙動を変えたらテストも一緒に変えてください。スイートは、その変更から他の全セッションを守る契約です。

このルールの短縮版は [`CLAUDE.md`](../../CLAUDE.md) にあります。

## 早めに rebase し、小さな衝突のうちに統合する

```bash
make preflight   # git fetch origin && git rebase origin/main && make check のあと「完了の定義」リマインダ
```

`make preflight`（[`scripts/preflight.sh`](../../scripts/preflight.sh)、BE-0069）は pre-push の手順を
早めに走らせる版です。fetch して `origin/main` に rebase し、ゲートを走らせたあと、「完了の定義」の
リマインダ（両言語のドキュメントを触ったか、挙動の変更と一緒にテストを変えたか、出荷なら `Status` を
切り替えたか）を表示します。これは助言的で、人が起動するものです。pre-push フックはすでに `make check` を
ゲートしているので、これは出荷前の二重のゲートではなく、完了したと思う前に自分で早めに回すためのものです。
個々の手順を覚えておく必要はなく、いつでも走らせられます。

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

このルールの短縮版は [`CLAUDE.md`](../../CLAUDE.md) にあります。

## worktree で同時セッションを隔離する

2 つのエージェントが同じチェックアウトを編集してはいけません。各セッションに専用の
[worktree](https://git-scm.com/docs/git-worktree) + ブランチを与えます（`.git` は 1 つを共有）。

```bash
# メインのチェックアウトから
make worktree TOPIC=<topic>             # ../bajutsu-<topic> に claude/<topic> ブランチ
make worktree TOPIC=<topic> PREFIX=<user>   # 人間の <user>/<topic> ブランチ
```

`make worktree`（[`scripts/worktree.sh`](../../scripts/worktree.sh)、BE-0069）が手順をまとめて実行します。
`git fetch origin`、`git worktree add ../bajutsu-<topic> -b claude/<topic> origin/main`、続いて新しいツリーで
`make setup`（依存と自己修復する git フック）です。ブランチの接頭辞は既定で `claude` ですが、人間のブランチには
`PREFIX=<user>` を渡します。

`git fetch origin` は組み込みで省略できません。`origin/main` は fetch したときだけ進むローカルの追跡 ref です。
これを飛ばすと前回 fetch した時点の古い main から worktree を切ることになり、他セッションが既にマージで
解消したはずの衝突を再び持ち込みます。コマンドが先に fetch するので、この落とし穴にはまることはありません。

ブランチがマージ（または破棄）されたら片付けます。

```bash
git worktree remove ../bajutsu-<topic>
```

生成物とスクラッチ出力（`runs/`、`tmp/`、`.venv/`、ビルド成果物）は意図的に gitignore 済みです。
コミットに混ぜず、worktree を独立に保ってください。

このルールの短縮版は [`CLAUDE.md`](../../CLAUDE.md) にあります。

## 自分のレーンに留まる

タスクに必要なファイルだけ触ります。アーキテクチャは層状です（scenario → orchestrator → driver →
backend。[architecture](architecture.md) 参照）。ほとんどのタスクは 1 層に収まります。多数の
モジュールを横断せざるを得ない変更（抽象 **Driver API**、シナリオ **スキーマ**、共有 config の
形を変えるなど）は、他セッションがその面を避けられる（または着地を待てる）よう、事前に宣言してください。

調整が必要な共有面:

| 面 | ファイル | 共有される理由 |
|---|---|---|
| Driver API | [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) | 全 backend と orchestrator が依存 |
| シナリオスキーマ | [`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py) | ハブとなる成果物。codegen/runner/report が読む |
| config の形 | [`bajutsu/config/`](../../bajutsu/config/) | 全コマンドが解決する per-target レイヤリング |

## CI がブランチを正直に保つ

CI は全 PR で同じゲートを走らせ、`concurrency: ci-${{ github.ref }}` と `cancel-in-progress` を使います。
同じブランチへの再 push は古い run を積み上げず置き換えます。各々単独で通る 2 つの PR でも挙動は衝突し
えます。マージこそが両者の出会う場です。だからこそ判定者は決定的なテストスイートであり、LLM（大規模言語モデル）でも
人間の目視でもありません。スイートを意味あるものに保ち、ブランチを rebase し続ければ、並行作業は破綻なく合成
されます。

## GitHub Actions のワークフロー名とジョブ名の付け方

Actions タブや PR の checks 一覧でレビュアーの目に入るのは、ワークフローの `name:` と各ジョブの `name:` だけです。
その背後の YAML はもう 1 クリック先にあるので、名前はそれ単独で内容が伝わる必要があります。両方を同じ形式で名付けます。
チェックが何をするのかを示す短く平易な句に、情報が増える場合だけ対象のツールや範囲を括弧書きで添えます（`E2E (Simulator)`、`Swift (BajutsuKit)`、`Web E2E (Playwright)`、`Dependency audit (pip-audit)` など）。
実行結果を開かないと意味の通じない、単語 1 語だけの名前（`docs`、`build`、`deploy`）は残しません。模範例は `ios-e2e.yml` と `swift.yml` です（BE-0122）。
`name:` の値そのものにコロンと空白が含まれる場合は、YAML が入れ子のマッピングと解釈しないよう引用符で囲みます（`name: "Roadmap: allocate BE IDs"`）。

リネームには一つだけ制約があります。required status check のコンテキストは、ワークフロー名ではなく **ジョブ**の `name:` そのものです。
そして `main` のブランチ保護ルールセットは、このうちいくつかを文字列そのままで固定しています。`check`（`ci.yml`）、`E2E`（`ios-e2e.yml`）、`require two approvals for BE proposals`（`roadmap-proposal-approvals.yml`）の 3 つです。
これらのジョブ名を、ルールセットの `required_status_checks` を同時に更新せずにリネームすると、開かれているすべての PR が、二度と報告されないチェックを待ち続けてマージできなくなります。
ルールセットの編集は通常の PR からは届かないリポジトリ外の管理者操作なので、この 3 つの名前は現状のまま残します。意図的にリネームする場合は、人手によるルールセットの管理者編集と必ずセットで行ってください。

### プラットフォームごとの E2E ジョブ構成

バックエンドごとに実機・実環境の E2E ワークフローが 1 つあります。[`ios-e2e.yml`](../../.github/workflows/ios-e2e.yml)（macOS / idb + XCUITest）、[`android-e2e.yml`](../../.github/workflows/android-e2e.yml)（Linux+KVM / adb）、[`web-e2e.yml`](../../.github/workflows/web-e2e.yml)（Linux / Playwright）です。これらはジョブの語彙を共有しており、レビュアーはプラットフォームをまたいで同じ形を読み、新しいバックエンドも合わせるべき形を得られます。レーンごとに 1 つの束ねた合否を返すのではありません。

どのレーンも備える機能面の核が **smoke** です。実バックエンド上で showcase のシナリオを `bajutsu run` で回し、決定的に合否を判定します（「bajutsu はそのプラットフォームを操作できるか」）。ほかのジョブは特定の機能を確認するもので、そのプラットフォームに当てはまる場合だけ備えます。

| ジョブ | 確認する内容 | iOS | Android | Web |
|---|---|:-:|:-:|:-:|
| `smoke` | showcase に対する機能的な `bajutsu run` | ✓ | ✓ | ✓ |
| `golden` | element ツリー（BE-0006）が committed ベースラインと一致するか | ✓ | ✓ | — |
| `visual` | committed ベースラインに対するピクセル VRT | ✓ | ✓ | — |
| `conformance` | 実バックエンド上のドライバ契約（BE-0114） | ✓ | ✓ | ✓ |
| `codegen` / `gestures` | ネイティブテスト出力 / マルチタッチ（idb は不可） | ✓ | — | — |
| `fallback` | resident と `uiautomator dump` の読み取り経路が一致するか（BE-0245） | — | ✓（ステップ） | — |

この構成を健全に保つ規則が 2 つあります。**どのレーンも、レーンごとに必須です。** 必須ステータスチェックは
ルールセットが固定するジョブの `name:`（前述）であり、各レーンは常に結果を報告する自分自身の集約ジョブ
（`E2E`、`E2E (android)`、`E2E (web)`、BE-0279）を持ち、その集約ジョブが束ねる重いジョブを `changes` ジョブが
パスゲートするので、無関係な PR は走りもブロックもされません。バックエンドを一つの集約ジョブにまとめず、レーンごとに分けているのは
切り分けを保つためです。赤いチェックが壊れたバックエンドを名指しします。**ホスト依存や upstream に脆いチェックは
必須ゲートから外します。** `visual` はレンダラによってベースラインが変わるピクセル比較であり、element ツリーの
`golden` は upstream 依存（`idb_companion` などの実機側サーバ）に対して走るため、その変化は制御外です。どちらも
PR ごとにシグナルとして走らせますが、各集約ジョブの `needs:` からは除外するので、ドリフトはマージを止めずに
表れます。

## モデルと推論エフォートの適正化（BE-0103）

このリポジトリはエージェント主導なので、セッションの**モデル**と**推論エフォート**はそのまま現実の反復的な
トークンコストになります。タスクの負荷に合わせて選んでください。負荷の高い作業には高エフォートの高性能モデルを
充て、機械的な雑務では引き下げます。これはあくまで指針です（難しい個別ケースでは人間がいつでも上位モデルへ
引き上げられます）。決定的な `run` / CI ゲートには一切触れません。ゲートは開発セッションが何で動こうとモデルを
呼ばないからです。

失敗の出方は非対称です。過剰な割り当てはトークンを目に見えず浪費し（出力は問題なく見えます）、不足は悪い結果として
はっきり表面化します。そのため自然な流れは常に最上位へ傾きます。この規約が取り除くのはまさにその無駄ですが、
難しいタスクで品質を損なうほど引き下げはしません。

### タスク → 能力の対応表

この表が唯一の典拠です。下のスキルの frontmatter とサブエージェントの指針は、これを反映します。タスクはモデルと
推論エフォートの 2 軸で、3 つの段階のいずれかに対応づきます。

| 段階 | モデル | エフォート | タスク |
|---|---|---|---|
| **重** | `opus` | 高 | BE 項目の実装（`implement-be`）、軽くないリファクタリング、アーキテクチャや設計の判断、ゲート失敗のデバッグ |
| **中** | `sonnet` | 中 | ロードマップの発想と起草（`ideation`）、技術文書執筆と翻訳のレビュー（`english-document-writing`・`japanese-document-writing`）、PR レビュー |
| **軽** | `haiku` | 低またはなし | ロードマップ index の再生成や promote、ドキュメントの整形とリンク修正、機械的なリネーム、ロックファイルや整形の雑務、中段階のレビューに回す前の翻訳の下書き |

段階からモデル id への対応はここだけにあります。新しい Claude モデルへ段階を差し替えるときは、1 箇所を 1 行
直すだけです。表のモデル id は Claude Code のエイリアス（`opus` / `sonnet` / `haiku`）で、背後のモデルの
バージョンが上がっても安定します。

### スキルの frontmatter に既定を埋め込む

リポジトリ内の各スキルは、自分の段階を `SKILL.md` frontmatter の `model:` として宣言します。ハーネスはスキル
実行時にこれを読み、正しいモデルを選びます。覚えておく必要はなく、上書きもできます。

- [`implement-be`](../../.claude/skills/implement-be/SKILL.md)：`opus`（重）
- [`propose-and-build`](../../.claude/skills/propose-and-build/SKILL.md)：`opus`（重）。Phase B で
  プロダクトコードを実装するので、`implement-be` と同じ段階にします。
- [`ideation`](../../.claude/skills/ideation/SKILL.md)：`sonnet`（中）
- [`document-writing`](../../.claude/skills/document-writing/SKILL.md)：`sonnet`（中）
- [`english-document-writing`](../../.claude/skills/english-document-writing/SKILL.md)：`sonnet`（中）
- [`japanese-document-writing`](../../.claude/skills/japanese-document-writing/SKILL.md)：`sonnet`（中）
- [`roadmap-filter`](../../.claude/skills/roadmap-filter/SKILL.md)：`haiku`（軽）。`Status` で
  ロードマップを見渡す読み取り専用のスキルです（BE-0162）。`make roadmap-status STATUS="…"` を包み、
  ある状態の項目だけ（たとえば未着手の `Proposal` すべて）を、次に開くファイルパス付きで一覧します。
  700 行を超える `roadmaps/README.md` を文脈に読み込む必要がありません。

軽い雑務の多くはスキルではないので、その段階はふだん下の対話操作かサブエージェントへの委譲で使います。
`roadmap-filter` は例外で、その仕事そのものが軽い決定論的な検索だからです。`tests/test_skill_models.py` が
各スキルの `model:` を既知の妥当な id かどうか確認するので、打ち間違いは黙って握りつぶされず、ローカルの
ゲートが落とします。

### フェーズとサブエージェントへの委譲

frontmatter は対話的な作業や委譲した作業には届かないので、そこは手で選びます。

- **セッション内のフェーズ**：探索、調査、機械的な雑務では引き下げます（あるいは `/fast`）。実装と設計では
  引き上げます。`/model` と `/fast` は、セッションの途中でモデルとエフォートを切り替えます。
- **サブエージェントへの委譲**：Agent ツールでサブエージェントを起動するときは、駆動側ではなく委譲するタスクに
  合った `model` を渡します。広く展開する `Explore` のファンアウトや index の再生成は、駆動しているセッションより
  安いモデルで回せます。これは、frontmatter を所有していないリポジトリ外のレビュー用プラグイン
  （`pr-review-toolkit`）に対して唯一効くつまみでもあります。起動時にモデルを指定してください。

意図的にゲートでは強制しません。セッションがどのモデルを使ったかは差分から復元できず、固定的に縛ると、本来は
軽いタスクが難しかったときに人間が引き上げる判断を奪うからです。これは、コントリビューターの作業フローの残り
（[BE-0069](../../roadmaps/BE-0069-executable-contributor-guardrails/BE-0069-executable-contributor-guardrails-ja.md)）と
同じ、「手順をコマンドに、ゲート強制ではなく指針」という先例に従います。

## ロードマップ項目を起草し出荷する 3 つのスキル

アイデアを出荷可能なコードにする作業では、3 つのスキルを使い分けます。起草するか、出荷するか、
その両方かです。

- [`ideation`](../../.claude/skills/ideation/SKILL.md)：**起草のみ**。アイデアを BE 提案に整える相談相手で、
  `roadmaps/` のファイルで止まります（プロダクトコードには触れません）。提案は `BE-XXXX` のプレースホルダを
  持ち、実際の id は PR がマージされたあとに CI が採番します。
- [`implement-be`](../../.claude/skills/implement-be/SKILL.md)：**採番済み項目の出荷**。採番済みの `BE-NNNN`
  を受け取り、その提案を仕様として実装とテストを書き、項目を `Status: Implemented` に切り替え、
  `make check` が緑であることを示します。
- [`propose-and-build`](../../.claude/skills/propose-and-build/SKILL.md)：**両方を、スタックで**。設計が固まった
  小さな項目を、作者がいま実装できると確信しているときに、前の 2 つを組み合わせます。提案の起草と実装を
  並行して進め、一時的な 2 本の PR スタック（提案 PR が先、実装 PR が後）として出します。提案がマージされて
  id が採番されると、ハンドオフが実装ブランチを rebase し、その `BE-XXXX` 参照を採番済みの `BE-NNNN` に
  書き換え、base を `main` へ付け替え、`implement-be` の昇格とゲートのステップを走らせます。こうしてスタックは、
  通常の `implement-be` 形の PR に平坦化されます。

**どれを選ぶか**。既定は直列の `ideation` → マージ → 採番 → `implement-be` の経路です。コードを書く前に設計を
レビューへ通すことを強制し、出荷する項目にだけ番号を割り当てて `BE-NNNN` の列を連続に保ちます
（[BE-0089](../../roadmaps/BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation-ja.md)）。
`propose-and-build` に手を伸ばすのは、この経路の待ち時間が純粋なオーバーヘッドになるときだけです。つまり、
作者がレビューで設計が変わらないと見込む、小さくよく絞られた項目です。並行化は「提案を開いた」から
「id が採番された」までの空き時間を取り戻しますが、レビューが提案を変えれば実装ブランチが手戻りを負います。
設計が本当に不確かなときは、直列の経路に戻してください。

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
  付き subject のままにします。PR が新しいロードマップ項目を*導入する*場合も、スコープ付き subject の
  ままにし、**`[BE-NNNN]` 接頭辞は付けません**。番号はマージ後に `main` 上で採番されるからです
  （[ロードマップ項目](#ロードマップ項目-be-id厳守)を参照）。

### 本文

リポジトリ内の [`.github/PULL_REQUEST_TEMPLATE.md`](../../.github/PULL_REQUEST_TEMPLATE.md) が、この型の
正典です。GitHub が新規 PR の本文へ自動で挿入します。**AI が PR を起草するときは、これに従います。**
該当するセクションを埋め、残りは削除します。テンプレートにあらかじめ書き込まれている
`## Prime-directive compliance` と `## Verification` の定型ブロックは正典の文言なので、言い回しを作り直す
のではなく、その変更が関係する範囲に削って使います。この節の残りは、テンプレート内のコメントが参照先と
して指し示している内容です。

必須は 2 つ（`## Summary` と検証の記述）で、残りのセクションは変更が必要とする範囲で、以下の順に
足します。詳しさは差分に合わせます。1 ファイルの修正なら、短い Summary と緑の数値で足ります。横断的な
機能なら全セクションが要ります。文章は、マージ済みの PR でこれらのセクションが実際に読める形にならって
書きます。現在形で、たどり着くまでの経緯を語るのではなく、変更が*何であるか*を述べます。**太字**は、変更の
鍵となる少数の名詞に限り、文全体には使いません。変更一覧では、繰り返し現れる `**パス**：何をするか、
そしてなぜこの継ぎ目か` の形に従い、単なる編集ではなく設計上の選択を書きます。

よく現れるセクションと、それぞれが担う内容は次のとおりです。

- **`## Summary`**（必須）：PR が何をするか、そして*なぜ重要か*を、短い段落 1〜3 個で書きます。鍵となる
  名詞は**太字**にします。経緯ではなく変更そのものから書き始めます。より大きな項目の一部を成す PR なら、
  どの一部かを示し、マージによってその項目の `Status` がどう動くか（例: *In progress* へ移る）を
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
  プレースホルダについての注記。番号はマージ後に `main` 上でワークフローが採番するので、手で書き換えません）。
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
the item to **In progress**.

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

[BE-NNNN]: roadmaps/BE-NNNN-<slug>/BE-NNNN-<slug>.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

このルールの短縮版は [`CLAUDE.md`](../../CLAUDE.md) にあります。

## PR レビューコメントへの対応

レビューには 1 件ずつ返信します。返信するのは、**その pull request の担い手（人間の貢献者でも AI
エージェントでも同じ）**です。レビュアー（後述する自動レビュアーの Claude Code、あるいは人間）が
コメントを残したときは、すべてのコメントを解消するまで作業を続け、そのうえで**コメント 1 件ごとに個別に
返信します**。PR にまとめて 1 つ返信するだけでは足りません。指摘が出たスレッドそのものに解消の記録が
残るよう、コメントのスレッドそれぞれに返信します。

返信では次の 2 点を必ず示します。

- **その指摘に対応したこと**：コードを修正したのか、意図して見送ったのか。
- **その根拠**：解消にあたる具体的な変更（何をどこで変えたか。コミットやファイル、行を挙げます）。
  変更しない場合は、その指摘が当てはまらない具体的な理由。

「対応しました」や 👍 だけではこの規範を満たしません。根拠があってこそ、後でスレッドを読む人が解消の
妥当性を確認できます。返信は短く事実に即して書きます。重要なのは根拠であって、説明の量ではありません。

**対応したコメントには、返信とスレッドの解決の両方を残します。** コードを修正した場合も、意図して
見送った場合も、まず根拠を記した返信を残し、そのうえでそのスレッドを解決済みにします。
返信はその指摘に対応した理由を示し、解決はそのスレッドが決着したことを示します。こうしておくと、開いた
ままのスレッドの集合が、対応済みのコメントの山ではなく、いま対応を要するものだけを常に表します。例外は
次に述べる未決の場合だけです。意図して開いたままにしたスレッドは開いたままにし、問いにまだ答えが出て
いないコメントは解決しません。

コメントへの対応に迷うとき（修正の解釈が複数ありうる、あるいはアーキテクチャ上重要な箇所に触れる場合）
は、当て推量せず確認します。AI エージェントは自分を動かしている人間に、人間の貢献者はレビュアーや
メンテナに確認し、判断が出るまでそのスレッドは開いたままにします。

### 自動レビュアー（Claude Code、BE-0203）

`claude-review` Environment にプロバイダーの資格情報（Claude Code サブスクリプションのトークン、または
Amazon Bedrock のロールと `BEDROCK_MODEL_ID` 変数）が設定されると、同一リポジトリのブランチからの pull request を **Claude Code** が自動でレビューします（フォークの PR はオンデマンドです。後述します）。
[`claude-review`](../../.github/workflows/claude-review.yml) ワークフローから走り、PR がオープンした
ときにレビューし、プッシュのたびに再レビューします。
[`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md) の契約に照らしてレビューし、行単位の
インラインコメント（機械的な修正が当てはまる箇所では `suggestion` ブロックつき）だけを投稿します。トップレベルの
要約は投稿しません。このジョブはプッシュのたびに再実行され、その都度新しい要約を出すと古い要約が残って PR 上で
矛盾するためです。同じ指摘を毎回蒸し返さず、かつ見落としも出さないため、各実行は **diff 全体**を読み（変更行を
一つも未レビューにしません）、あわせて PR に**すでに投稿済み**の指摘の一覧を（API 経由で、PR head を
チェックアウトせずに）渡して、それらは二度と再投稿しないよう指示します。つまり、見る範囲を狭めるのではなく、
重複の再投稿を抑えることで重複を省きます。以前の実行が見落とした箇所であっても、本物の問題を新たに見つけたら
必ず挙げます。
資格情報を用意するまではワークフローは休眠状態の green no-op で、何も投稿せず、マージを妨げることもありません。
したがって、まだレビューが付かないのは Environment が未設定なだけです。
このプロンプトはレビュアーを*このリポジトリの*契約に向けます。三つの
[prime directive](../../CLAUDE.md#prime-directives-do-not-violate)、docstring 規約、ドキュメントの二言語
ルール、BE ID のライフサイクルです。おかげで、汎用のレビュアーには拾えないものを拾えます。レビューは
（アクション既定の Sonnet ではなく）Opus で走らせ、深刻度の見極めを鋭くします。投稿するのは `issue`・
`suggestion`・`question` だけで、`nitpick` と `praise` は抑制します。毎プッシュで再実行される助言レビューに、
価値の低いノイズをためないためです。

このレビューは**助言であって、ゲートではありません**。あえて必須のステータスチェックにはせず、ジョブの
結果を指摘の有無から切り離しています（指摘を見つけたレビューは*成功した*レビューなので、ジョブが赤くなるのは
インフラの失敗のときだけです）。マージを決める唯一の裁定者は、これまでどおり決定論的な `check` と `E2E` の
ゲートです。これはレビュアーであって審判ではありません（prime directive 1）。そのコメントは、ほかのどの
レビュアーのコメントとも同じく、上の返信ルールに従って扱います。

- **オンデマンド。** 自動レビューとは別に、メンテナまたはコラボレーターが PR に `@claude review` と書く
  （あるいはレビュースレッドに返信する）ことで、新しいレビューや特定コメントへの追随を依頼できます。この経路は
  trusted actor（OWNER / MEMBER / COLLABORATOR）に限定しています。コメントイベントはフォーク PR でも
  リポジトリのシークレットを伴って走るためで、それ以外の人の `@claude review` は無視されます。
- **フォーク。** フォークからの素の `pull_request` イベントは（GitHub の設計上）シークレットを露出しないため、
  自動レビューは同一リポジトリの `claude/<topic>` / `<user>/<topic>` ブランチを対象とし、フォークの PR は
  代わりにメンテナがオンデマンドでレビューします。
- **Copilot からの移行（手動、リポジトリ外）。** このワークフローは Copilot のレビューを残したまま投入するので、
  両者が並行して走り、比較できます。Claude Code のレビューが実力を示したら、メンテナがリポジトリまたは組織の
  設定で **Copilot の自動レビューを無効にします**。これは PR では持ち運べない管理状態であり（BE-0122 や
  BE-0089 が指摘するブランチ保護ルールセットの編集と同じ形です）、明示的な手動の手順です。

### 定期リフレッシュ（Claude Code、BE-0222）

人手で維持しているリポジトリの部分を、出荷済みの内容と毎日照合する 2 本の定期ワークフローです。上記の自動
レビュアーに対する**起草側**の対応物にあたります。BE-0203 がマージを止めない AI レビュアーを足したのに対し、
こちらはマージしない AI 起草者を足します。

- [`roadmap-refresh`](../../.github/workflows/roadmap-refresh.yml) は、各 BE 項目の `Status` /
  `Progress` / `Implementing PR` を `main` にマージ済みの内容と照合します。
- [`docs-refresh`](../../.github/workflows/docs-refresh.yml) は、挙動に対してずれる文章、すなわち
  `docs/architecture.md#implementation-status` と、`DESIGN.md` / `docs/architecture.md` の文章とコードの
  対応（[BE-0113](../../roadmaps/BE-0113-design-doc-realignment/BE-0113-design-doc-realignment-ja.md)
  のレビュー時の規範）を照合します。

どちらも 1 本の再利用ワークフロー [`refresh.yml`](../../.github/workflows/refresh.yml) の薄い呼び出し側です。
共有された型をそこにまとめることで、2 本がドリフトしないようにしています。違うのはブランチ、契約ファイル
（[`.github/roadmap-refresh-prompt.md`](../../.github/roadmap-refresh-prompt.md)、
[`.github/docs-refresh-prompt.md`](../../.github/docs-refresh-prompt.md)）、そして**パス許可リスト**
（`roadmaps/**`、または `docs/**` と `DESIGN.md`。`README*` / `CLAUDE.md` の契約面は意図的に除外します）
だけです。どちらも既存の自動化と揃えてあります。

- **設定が揃うまで休止します。** 各実行は、AI プロバイダ（レビュアーが使うのと同じ `claude-review`
  Environment の資格情報）と自動化 App トークン（`roadmap-id.yml` と同じ）の**両方**が揃わない限り、緑の
  no-op です。bot が開いた PR に自身の `check` CI を走らせられるのは、App の身元があるからです。設定が中途
  半端なリポジトリが赤になることはありません。
- **AI が起草し、ゲートと人間が判断します。** Claude Code アクションは作業ツリーを編集するだけです。その
  あと決定論的なステップがパス許可リストを強制し（許可外の編集は復元します）、ジョブ内で `make check` を
  実行し、ワークフローごとに**1 本の更新用ドラフト PR** を開きます。`run`/CI の合否に LLM は載りません
  （prime directive 1）。ready にしてマージするのは人間だけです。
- **冪等で、人間の作業を上書きしません。** 差分がない日は PR を開きません。ドリフトがあれば、そのワーク
  フローの固定ブランチを再利用し、その tip を bot 自身がコミットしていたときだけ `--force-with-lease` で
  force-update します。レビュアーがブランチに push した fixup を上書きすることはなく、その場合は声高に
  スキップします。

## ロードマップ項目: BE ID（厳守）

ロードマップは [`roadmaps/`](../../roadmaps/README-ja.md) 配下に**1 項目 1 ディレクトリ**で置きます。各項目は
`roadmaps/BE-NNNN-<slug>/` ディレクトリに、英語版 `BE-NNNN-<slug>.md` と日本語版
`BE-NNNN-<slug>-ja.md`（ID と slug は同一）を入れます。**BE** は *Bajutsu Evolution* の略で、`NNNN` は
**ゼロ詰め 4 桁で単調増加する** ID です。すべての項目は `roadmaps/` の直下にフラットに置きます。ID を
採番した時点でパスが確定し、以後は動きません（BE-0159 で、BE-0078 が導入した `状態` ごとのフォルダを
廃止しました。`状態` はこの後のインデックスのバケットだけを決め、ファイルの場所は決めません）。

ロードマップ項目を追加するとき:

1. **次の ID を採番する** = 既存の最大 `BE-NNNN` + 1（`roadmaps/` 直下のすべての項目を数えます）。現在の最大は次で確認します。
   ```bash
   ls -d roadmaps/BE-*/ | sort | tail -1
   ```
   番号を再利用したり、飛ばしたり、当て推量したりしてはいけません。
2. **項目ディレクトリと両言語のファイルを作成する**（新規項目はまず提案なので `roadmaps/` の直下に `状態: 提案` で置きます）。
   すなわち、`roadmaps/BE-NNNN-<slug>/BE-NNNN-<slug>.md`
   （英語）と `roadmaps/BE-NNNN-<slug>/BE-NNNN-<slug>-ja.md`（日本語、ID と slug は同一）です。**インデックス表は
   手で編集しません。** これは各項目自身のメタデータから生成されます。`make roadmap-index`（または
   `python scripts/build_roadmap_index.py`）を実行して、**両方**のインデックスページ
   （[en](../../roadmaps/README.md) / [ja](../../roadmaps/README-ja.md)）の `<!-- GENERATED:* -->` マーカー間の表を
   再生成してください。項目の `状態`（バケット）+ `トピック` が並ぶセクションを決めるので、既存セクションの項目なら
   表の手編集は不要です。コミット済みインデックスがズレると `tests/test_roadmap_index.py`（`make test` が実行）が
   落ちます。あるトピックがあるバケットに初めて入るときは、マーカー付きセクションを追加します（不足している領域は
   生成スクリプトが名指しします）。
3. **ID は不変**。既存項目を採番し直しません。状態が変わっても、完了しても、表から削除しても同じです。
   一度割り当てた BE ID は、その項目を永遠に指します。

番号は **PR のマージ後に `main` 上で**採番されます。PR を開いた時点ではありません
（[BE-0089](../../roadmaps/BE-0089-merge-time-be-id-allocation/BE-0089-merge-time-be-id-allocation-ja.md)）。
`BE-XXXX` プレースホルダで書き起こすのが常道です。項目はオーサリング、レビュー、マージ自体を通じて
`BE-XXXX` のまま保たれ、**BE 作成 PR は `[BE-NNNN]` 接頭辞をいっさい持ちません**。本当の番号はマージ後まで
分からないので、タイトルはスコープ付き subject のままにします。マージは `main` への push であり、これが
`roadmap-id` ワークフローを起動します。ワークフローは `main` に対して allocator を実行し、各プレースホルダを
次の空き `BE-NNNN` にリネームし、リネームと再生成したインデックスを `main` へ直接コミットし、採番した ID を
マージ済み PR にコメントします。採番が `main` 上でマージ順に走るので、`BE-NNNN` の並びは構成上連続します。
却下された PR はマージされないため、番号を消費しません。

保護された `main` へこのコミットを着地させるには、bypass の identity が要ります。`main` のルールセットの
bypass list に載せた専用の GitHub App で、このリポジトリにのみ `contents: write` と `pull-requests: write`
を与え、その App ID と秘密鍵を `AUTOMATION_BOT_APP_ID` / `AUTOMATION_BOT_PRIVATE_KEY` の Actions secret として
保存します。セットアップはメンテナが一度だけ行います（後述の「マージ時採番 App のセットアップ」を参照）。
secret が無いあいだワークフローは緑の no-op なので、App の用意中も `main` は緑のままです。ジョブはマージ後の
レビュー済みコードだけを実行し（`main` を checkout します）、すべての action をフルコミット SHA に pin し、
`scripts/check_renumber_diff.py` を実行します。これは bypass コミットが `roadmaps/` の外に触れたらジョブを
失敗させ、トークンの影響範囲をそのツリーに限定します。

番号を最初から固定したいときは、これまでどおり手で採番（既存の最大 `BE-NNNN` + 1）してもかまいません。
BE-0061 の衝突ハードニング（原子的な `refs/be-claims/*` の予約と、`roadmap-id-repair` および
`roadmap-claims-gc` のワークフロー）は撤去しました。マージ時採番では `main` に触れる allocate の実行が
同時に最大 1 つで、つねに最新の `main` を読むため、番号は構成上連続し、二つのブランチが同じ番号を取り合う
ことはなくなります。予約台帳とその修復のバックストップは不要になりました。詳しくは
[BE-0061](../../roadmaps/BE-0061-be-id-allocation-hardening/BE-0061-be-id-allocation-hardening-ja.md) を参照してください。

#### マージ時採番 App のセットアップ

`roadmap-id` ワークフローが renumber コミットを `main` のブランチ保護を越えて push できるよう、admin 権限を
持つメンテナが一度だけ次を行います。

1. **GitHub App を作成**します（org 所有でもリポジトリ所有でもかまいません）。webhook も callback URL も
   不要です。権限は **Repository permissions → Contents: Read and write**（renumber を push するため）と
   **Pull requests: Read and write**（採番した ID をコメントするため）だけにします。
2. **このリポジトリにのみ install** し、到達範囲を 1 リポジトリに限ります。
3. **App を `main` のルールセットの bypass list に追加**します（唯一のエントリにします）。これで installation
   token が renumber コミットをブランチ保護を越えて push できます。
4. **秘密鍵を生成**し、App ID とともに `AUTOMATION_BOT_PRIVATE_KEY` と `AUTOMATION_BOT_APP_ID` の Actions secret
   として保存します（`main` ref に紐づく Environment でスコープし、PR 起動のジョブが読めないようにします）。

ワークフローはこれらの secret から短命（約 1 時間）の installation token を作り、checkout、push、`gh` に使います。
App が作るコミットは署名され App に帰属するので、すべての bypass push は監査できます。

#### トラッキング Issue。オープンな項目の担当者を示す（BE-0109）

**オープンな**ロードマップ項目（`状態` が `提案` または `実装中` の項目）にはそれぞれ GitHub Issue が
あり、その Issue の標準機能である担当者（Assignees）が、誰がそれに取り組んでいるか（取り組んでいる
なら誰か）を示す一次情報になります。提案として存在した時点で Issue が起票されるので、担当者が付いて
いない Issue は、ロードマップにこれまで欠けていた「まだ誰も拾っていない」というシグナルそのものです。
二つの保存済みフィルタが、Issue 一覧をボードに変えます。

- `label:roadmap-tracking no:assignee`。**未着手のバックログ**です（誰も付いていない、提案と実装中の
  項目）。
- `label:roadmap-tracking assignee:<user>`。一人の担当分です。

**項目に着手する前に、そのトラッキング Issue を確認してください**
（`label:roadmap-tracking BE-NNNN in:title` で検索します）。担当者が付いていなければ、作業を拾うときに、
他の GitHub Issue と同じように**自分をアサイン**します。トラッキング Issue を手でクローズしてはいけません。
同期処理が行います。

Issue の起票とクローズは、`roadmap-tracking-issues` ワークフロー
（`scripts/sync_roadmap_tracking_issues.py`）が自動で行います。ワークフローは `push: main`（パス
`roadmaps/**`）で実行します。ライフサイクルは、各項目の現在の `状態` だけを見る関数です。対応する
オープンな Issue の無いオープンな項目には起票し、項目がすでに出荷済み（`実装済み`）または棚上げ
（`提案（保留）`）になった Issue はクローズします。したがって同期処理は冪等で自己修復的であり
（BE-0043 や BE-0061）、再実行しても一つの項目に二つ目の Issue を作りません。追跡する二つの事実、
すなわち担当者（Assignees）と Issue がすでに存在するか（その項目の `BE-NNNN` をタイトルに持つ、
`roadmap-tracking` ラベル付きのオープンな Issue）の両方について GitHub が一次情報なので、リポジトリ
側には何も書き戻しません。ジョブは `issues: write` だけで済み、`main` へのコミットも bypass 用 App も
要りません。実行は PR ではなく `main` で行い、`BE-XXXX` プレースホルダはスキップします。実番号を
タイトルに持つ Issue は、`roadmap-id` が `main` で番号を割り当てたあとでなければ作れないからです
（BE-0089）。その割り当てコミット自体が `roadmaps/**` への push なので、同期処理を再び起動し、番号の
付いた項目を拾います。スクリプトはネットワーク越しに `gh` を呼び出すため、`make check` の内側では
実行しません。読み取り専用の `--check` モードが、何も変更せずにメンテナ向けにずれを報告します。

各ファイルは **Swift-Evolution の proposal フォーマット**に従います。メタデータブロック（`* 提案`、
`* Author`、`* 状態`、`* トピック`、任意で `* 由来`）の後に `## はじめに` / `## 動機` /
`## 詳細設計` / `## 検討した代替案` / `## 参考` と続けます。埋められる範囲だけ記入し、不明は `TBD` とします。
**`-ja.md` ファイルのタイトル（`# BE-NNNN — <タイトル>` の見出し）は日本語で書き、英語ファイルの見出しを
そのまま転記しません。** 地の文と同じ規範で翻訳します。訳すと不自然になる用語（`selector`、`backend` など）
は訳さず元のまま残しますが、タイトル自体は日本語にします。**Author は
GitHub のアカウント名で明記**します。書式は `* Author: [@handle](https://github.com/handle)` で、最初にその項目を
作成した人（AI 支援で書き起こした場合は、それを主導してコミットした人）のアカウントです。**状態**
フィールドは、項目が並ぶ index のバケットを決める唯一の基準です（BE-0078）。項目の場所は決めません。
BE-0159 以降、すべての項目はパスが固定された一つの `roadmaps/BE-NNNN-<slug>/` ディレクトリに置かれます。
ディレクトリが `状態` に依存しないので、`状態` とディレクトリが食い違うことはそもそも起こりません。

| 状態 | index バケット |
|---|---|
| `実装済み` | Implemented。出荷済み |
| `実装中` | In progress。可決済みで、現在構築中 |
| `提案` | Proposals。検討中 |
| `提案（保留）` | Deferred。棚上げ |

最後の 3 つのバケットだけが、インデックスページ（`roadmaps/README.md` / `README-ja.md`）の表として
描画されます。`実装済み` に達した項目は、その表に移るのではなく、ページから外れます。出荷済みの
項目は、トピックごとに整理され進捗バーも付いた
[ロードマップダッシュボード](https://bajutsu-e2e.github.io/bajutsu/api/roadmap.html)で引き続き
閲覧できます。このページは `scripts/build_roadmap_dashboard.py`（BE-0094）が、ドキュメントの
ビルドのたびに同じ項目メタデータから生成し、GitHub Pages に公開するものです。インデックスページと
ダッシュボードは同一のソースを読むため、両者が食い違うことはありません。インデックスは、見せる
範囲をまだ完了していない項目だけに絞っています。

**コードが状態を決めます。これは厳格なルールです。** 項目の `状態` は、その実装が存在するかどうかを表すもので、
項目を前向きな提案として読ませ続けたいという好みを表すものではありません。コードのない状態で書き起こした項目は
`提案` です。その**コードを出荷する** PR は、同じ PR のなかで `状態` を `実装済み`（一部だけを出荷するなら
`実装中`）に変え、対応する `進捗` のチェックを付け、その PR を `実装 PR` に記録します。コードがすでに出荷された
項目に `提案` を残すことはありません。これはまさに [`implement-be`](../../.claude/skills/implement-be/SKILL.md)
スキルが行う昇格であり、人にもエージェントにも等しく適用されます。（唯一の例外は新規項目を*起草*する場合です。
コードを出荷しない `ideation` 形式の提案は、まだ何も実装していないので `提案` のままにします。）

項目が進んだら**状態を更新**し、`make roadmap-index` でインデックスを再生成します（行は自動で正しいバケットへ
移るか、`状態` が `実装済み` になった時点でページから完全に外れます）。ディレクトリは移動しません（BE-0159）。
同じ `roadmaps/BE-NNNN-<slug>/` のパスがその項目を生涯
保持するので、昇格はもうその項目へ出入りするリンクを腐らせません。これがフォルダ方式に対する具体的な利点で、
フォルダ方式では項目の `状態` が変わるたびにリンクが一つ壊れていました。**`make lint-roadmap`**（`make check`
に含まれる）は今も相互リンクを守ります。ある項目の他項目への markdown リンクが解決しない場合（slug の打ち間違い、
リネームされた項目へのリンクなど）、または `Author` が `[@handle](…)` リンクでない場合に失敗します。`make
lint-roadmap ARGS="--fix"` は壊れた項目リンクを対象の現在のパスへ書き換えます。
マイルストーン M1–M4 は `BE-0001`–`BE-0004`（実装済み）です。

これはエージェントが従うべき厳格なルールです。短縮版は [`CLAUDE.md`](../../CLAUDE.md) にあります。

## ドキュメントの書き方（全ドキュメント、両言語に適用）

このルールはすべてのドキュメント（`docs/` の英語版と `docs/ja/` の日本語ミラー）に、そして
今後のすべての更新（新規ファイルに限らない）に適用されます。エージェントは厳守してください。作業を
報告したり要約したりするときも同じく適用されます。

- **[`document-writing`](../../.claude/skills/document-writing/) スキルに従う。** ここのすべての
  ドキュメントとすべての BE ロードマップ項目に対する、両言語の正式な散文規範です。両言語が共有する
  言語に依存しない執筆技法（上から下へ推敲する、主眼を冒頭で述べる、各文の文末を最も重要な要素のために
  空ける、主語と述語を近づける、能動態を選ぶ、冗語を削る、段落ごとに1つの話題だけを置いて論証を一方向に
  進める（パラグラフライティング））を定めます。書いたあとではなく、書く前・
  推敲する前に呼び出します。このスキルは、英語と日本語それぞれの言語レイヤーの上位に立つ傘です。
  英語の散文には [`english-document-writing`](../../.claude/skills/english-document-writing/) を併せて適用します
  （シリアルコンマ、*that* / *which*、ダッシュ、数の表記といった英語固有の作法）。日本語の散文には
  下記の [`japanese-document-writing`](../../.claude/skills/japanese-document-writing/) を適用します。以下の
  ルールは、この節とこれらのスキルが共有する具体的な期待です。
- **自然な文章で書く。** 日本語ドキュメントは自然な日本語、英語ドキュメントは自然な英語で書きます。
  ミラーは逐語的な置き換えではなく、その言語で同じ内容を自然に伝えるものにします。
- **造語禁止。** 必ず一般的で広く使われている技術用語や普通の言葉を使います。語を勝手に作ったり、
  通常持たない意味に拡張したりしません。
- **不自然な翻訳禁止。** 用語は一般的な訳語を使います。訳すと不自然になる場合は、訳さず元の用語
  （多くは英単語。例: `selector`、`actuator`、`backend`、`assertion`）をそのまま使います。
- **省略禁止、単体で完結。**
  [`document-writing`](../../.claude/skills/document-writing/SKILL.md#self-contained-prose-both-languages)
  スキルの自己完結の規範に従います。読者がリポジトリの他のページを何も読んでいなくても文書を最初から
  最後まで追えるようにし、略語は初出で展開し、用語は初出の箇所で定義します。これは `docs/` に限らず、
  ロードマップ項目を含め、用語が現れるあらゆる箇所に適用されます。
- **指示語で読者を後戻りさせない。**
  [`document-writing`](../../.claude/skills/document-writing/SKILL.md#minimize-anaphora-both-languages)
  スキルの指示語抑制の規範に従います。先行詞が1文より前に離れる、段落・箇条書き・見出しをまたぐ、あるいは
  近くに候補となる先行詞が複数ありうるときは、指示語ではなく名詞をそのまま繰り返します。
- **横断的な規範は再記述せず、リンクする（BE-0284）。** 複数の文書にまたがる規則（ゲートのステップ一覧、
  ロードマップの BE-ID ライフサイクル、PR のタイトルと本文の書式、このドキュメントスタイル）は、**1 つの**
  基準ファイルに全文を書き、それ以外の言及は短いリンクでその基準ファイルを指します。規則を複製しません。
  再記述した写しはそれぞれ、後の修正が見落としうるもう 1 箇所になり、2 つの写しはやがて矛盾へ乖離します。
  短く行き渡らせるべき prime directives は意図的な例外です。初読で自己完結していなければならない文書は、
  読者を他所へ送る代わりに、短く正確な写しを保ちます。
- **用語集の用語は初出でリンクし、その場で説明を繰り返さない（BE-0286）。** BE ロードマップ項目や
  `docs/` 配下のページの文章が [`glossary.md`](glossary.md) で定義された用語を Bajutsu 固有の意味で
  使うときは、最初に実質的に言及する箇所を、定義をその場で書き直すのではなく、用語集の該当項目
  （`glossary.md#アンカー`。英語版のページからは `../glossary.md#anchor`）へのリンクにします。
  アンカーはその用語を定義する節を指します。たとえば driver / backend / actuator / platform の
  いずれも [`glossary.md#driver-backend-actuator-platform`](glossary.md#driver-backend-actuator-platform)
  を指します。上の横断的な規範のルールの用語単位の対応版であり、写しを増やして乖離させる代わりに
  唯一の定義を指すものです。CI のゲートではなくレビュー時の規約です。用語集の語の多くは `step`、
  `target`、`app`、`platform` のように普通の英単語でもあるため、ある言及が Bajutsu 固有の意味を
  指しているかどうかの判断には人間の判断が必要で、prime directive 1 がその判断を `run` / CI の
  経路から遠ざけているためです。手本は [`drivers.md`](drivers.md) です。
- **日本語の文章は `japanese-document-writing` スキルに従う。** 日本語版を新規に書くときも、英語版を
  `docs/ja/`（やロードマップの `*-ja.md`）へ翻訳するときも、[`japanese-document-writing`](../../.claude/skills/japanese-document-writing/)
  を適用します。これがこのリポジトリにおける日本語の文章の正式なスタイルであり、翻訳は英語の逐語訳ではなく、
  この規範に沿った自然な日本語にします。これは [`document-writing`](../../.claude/skills/document-writing/)
  の下位に位置する日本語レイヤーです（上記）。日本語の散文では両方を適用します。
- **日本語ドキュメントは敬体（ですます調）で書く。** `docs/ja/` 配下のすべての日本語ファイルと、
  ロードマップの `*-ja.md` は敬体で書きます。常体（だ・である調）は使いません。文書全体で一貫させます。
  敬体にするのは文末の述語だけで、連体修飾節や条件・接続の形（「〜する場合」「〜すると」「〜であり」）は従来どおり
  常体のままにし、見出しや純粋な体言止めのラベルには繋辞を付けません。

このルールの短縮版は [`CLAUDE.md`](../../CLAUDE.md) にあります。

## コードのドキュメンテーションコメント（docstring、BE-0065）

上の「ドキュメントの書き方」は散文ドキュメント向けのルールです。こちらは **Python コアの docstring**
についての対のルールで、生成 API リファレンス（`make docs`、MkDocs + `mkdocstrings`）が描画する対象です。
リファレンスのビルドは `make check` から外した別の重い経路で、LLM を一切加えず、`run` の中で動くこともないので、
prime directive は構成上そのまま保たれます。

- **英語で書きます。** コード（とその docstring）は両言語にしません。両言語にするのは `docs/` 配下の散文ドキュメントだけです。
- **公開面は Google style。** 公開 API（[`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) の `Driver`
  プロトコルと共有型、CLI、MCP ツール、シナリオスキーマ、runner / `assertions` / `network` の公開関数）は、
  1 行の要約に続けて `Args:` / `Returns:` / `Raises:`（必要なら `Yields:` / `Examples:`）を、**情報が増えるときだけ**
  付けます。生成リファレンスは非公開（`_` 始まり）メンバーを除外します。
- **内部ヘルパは散文のまま。** モジュール内部の `_helper` は「なぜあるか」を 1 行で書きます。小さなヘルパに `Args:`
  ブロックを強いるのは、このリポジトリが避ける「何をするか」の説明です。
- **型は書き直しません。** 型は注釈に置きます（`mypy` は strict、`ruff` の `ANN` ルールも有効）。生成器はシグネチャ
  から型を読みます。`Args:` / `Returns:` は型ではなく**意味**（単位、制約、`None` が何を表すか）を書きます。
- **なぜを書き、何をは書きません。** 根拠、不変条件（特に決定論を守るもの）、トレードオフ、エッジケースを書き、
  挙動の根拠は `BE-NNNN` 項目に結び付けます。周囲の密度に合わせ、短く目的を持って書き、ナレーションはしません。
- **per-field の流儀を保ちます。** `TypedDict` や定数を保持するクラスでは、フィールドごとのインラインコメントが
  各フィールドの「なぜ」を散文ブロックよりよく伝えるので、`Args:` 形式に変換せずそのまま残します。

例として、公開関数は構造化セクションを持ちます（決定論の不変条件を先頭に置き、根拠を BE 項目に結び付け、型は繰り返しません）。

```python
def resolve_unique(elements: list[Element], sel: Selector) -> Element:
    """Resolve a selector to exactly one element for a single action.

    A single action requires a unique match, so an ambiguous selector fails rather than acting on
    "whatever matched first" (the determinism core, BE-0001).

    Args:
        elements: One `query()` snapshot of the on-screen elements.
        sel: The selector to resolve. `index` is honored only as a last resort, picking the nth of
            several candidates.

    Returns:
        The one element the selector resolves to.

    Raises:
        ElementNotFound: Nothing matched, or `index` is out of range.
        AmbiguousSelector: Two or more matched and no `index` disambiguates.
    """
```

内部ヘルパは「なぜ」を 1 行で残します（`Args:` ブロックは付けません）。

```python
def _contains(outer: Frame, inner: Frame) -> bool:
    """Whether `inner`'s frame sits inside `outer`'s (edges inclusive)."""
```

**移行は段階的かつ漸進的に進めます**（[BE-0065](../../roadmaps/BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference-ja.md)）。
サイトは今ある散文 docstring からすでに描画でき（型付きシグネチャだけでも有用なリファレンスになります）、公開 API
の docstring はモジュール単位の小さな PR で Google style へ移し、scoped な `ruff` `D` の強制と Pages ホスティングは
その後に入れます。**無関係な変更のついでにモジュール全体の docstring を書き換えないでください**。移行は 1 つずつ
小さな PR にします。

リファレンスはローカルで `make docs`（プレビューは `make docs-serve`）でビルドします。`docs` extra が要ります。
このルールの短縮版は [`CLAUDE.md`](../../CLAUDE.md) にあります。

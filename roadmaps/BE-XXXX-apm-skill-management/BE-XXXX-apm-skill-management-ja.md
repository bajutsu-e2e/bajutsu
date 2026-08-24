[English](BE-XXXX-apm-skill-management.md) · **日本語**

# BE-XXXX — スキルごとに単一のソースを持ち、APMで管理する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-apm-skill-management-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Contributor workflow |
<!-- /BE-METADATA -->

## はじめに

Bajutsu自身のエージェントスキルを、**Agent Package Manager（APM）** で管理することを提案します。
APMは、スキル、プロンプト、指示、Model Context Protocol（MCP）サーバーといったエージェント向けの
文脈を、依存関係として扱うツールです。マニフェストを解決し、各エージェントホストが読むファイルを
生成します。
本項目の後、1つのスキルは`.apm/skills/<name>/`というソースディレクトリ1つになり、そこに`SKILL.md`と
必要な補助ファイルを置きます。マニフェストは`apm.yml`1つで、スキルの一覧と対応するホストを宣言します。
コマンドは`apm install`1つで、配備先の`.claude/skills/`を生成し、生成結果はコミットします。
今日1つのスキルが占める3つのツリー、すなわち共有ワークフローとClaude Codeアダプター、そして`.agents`
リンクの先にあるCodexアダプターは、同じ変更で撤去します。BajutsuはCodexを対象にしないためです。

## 動機

今日、スキルを1つ増やすには、3つのツリーに3つのファイルを書く必要があります。手順は
`.agent-workflows/<name>/workflow.md`に、Claude Codeアダプターは`.claude/skills/<name>/SKILL.md`に
置きます。Codexアダプターは`.agent-hosts/codex/skills/<name>/SKILL.md`と、付属の`agents/openai.yaml`
です。
14個のスキルは40を超えるファイルに散らばり、3つの写しが同じ手順を指しているかを検査する仕組みは
ありません。配置の規約は`CLAUDE.md`と撤去対象の2つの`README.md`に書かれており、規約を守るのは
コントリビューターの手作業です。

規約とリポジトリの実態は、すでに食い違っています。`.claude/skills/.gitignore`は、`git-sync`、
`cleanup`、`task-select`、`pr-followup`の4つをローカル限定と宣言しています。しかし、4つの
アダプターはいずれも追跡されています。いったんバージョン管理下に入ったファイルは、後から
`.gitignore`に書いても無視されないからです。その結果、リポジトリをクローンしたコントリビューターは、
自分が持つべきスキルの集合を判断できません。2人のコントリビューターが異なる集合を持ったまま、
どちらもそのことに気付かない状況が起こりえます。

スキルを呼び出すたびのコストも、この分割に由来します。アダプターの本文は、行動する前に共有
ワークフローを最後まで読むようホストに指示します。セッションが必要とするのが手順の一部だけでも、
手順の全体が文脈に入ります。14個のワークフローは、合計2,003行、132KBあります。なかでも大きいのは
`implement-be`の23.8KBと、日本語でトークン密度の高い`japanese-document-writing`の23.5KBです。
長いセッションほど、読み込む頻度が高くなります。APMの執筆規約は、`SKILL.md`をおよそ500行かつ
5,000トークン以内に収め、詳細は`references/`へ降ろします。`references/`は、その手順を必要とする
段階でのみホストが読み込みます。

変更後、読み手は2点を確認できます。1つは、スキルの呼び出しがアダプターとワークフロー全体では
なく、規約の範囲に収まった`SKILL.md`1つの読み込みで済むことです。`references/`を読むのは、
その手順を必要とする段階だけになります。もう1つは、`apm audit`がソースと
一致しなくなった配備ファイルを報告することです。今日は、この不一致を報告する仕組みがありません。

## 詳細設計

作業は、独立した6つの単位に分かれます。

1. **マニフェストとソースツリー**：リポジトリ直下の`apm.yml`に、パッケージ名とバージョン、そして
   `targets: [claude]`を宣言します。ホストを1つに限定することで、APMが他ホスト向けに生成する
   `.agents/skills/`ツリーの書き出しを防げます。各スキルのソースは`.apm/skills/<name>/SKILL.md`と、
   必要に応じた`references/`、`scripts/`、`assets/`です。`.gitignore`には、APMがロックファイルから
   再構築するキャッシュ`apm_modules/`を加えます。`apm.yml`と`apm.lock.yaml`、そして配備先の
   `.claude/skills/`はコミットします。クローンした直後、コマンドを1つも実行しない状態でスキルが
   揃うためです。
2. **スキルの変換**：14個のスキルそれぞれについて、Claude Codeアダプターが持つホスト固有の指示を
   単一の`SKILL.md`へ畳み込みます。畳み込む対象は、Agentツール、`/loop`、`pr-review-toolkit`
   プラグイン、役割ごとのモデル指定です。あわせて手順を分割し、本文をAPMの規約の範囲に収めたうえで、詳細を
   `references/`へ移します。frontmatterはそのまま配備されるため、`roadmap-filter`と
   `be-progress-tracker`の`model: haiku`は移行後も残り、
   [BE-0103](../BE-0103-dev-model-effort-tiering/BE-0103-dev-model-effort-tiering-ja.md)が定めた
   モデルの使い分けに影響はありません。
3. **旧ツリーの撤去**：`.agent-workflows/`と`.agent-hosts/`、`.agents`シンボリックリンクを
   削除します。両ツリーを説明する2つの`README.md`も削除します。BE-0366、BE-0379、BE-0380、
   BE-0383、BE-0384の5件は、両言語あわせて約80箇所で`.agent-workflows/`へリンクしています。各リンクは、同じ変更で新しい`.apm/skills/<name>/SKILL.md`のパスへ書き換えます。
   ロードマップのリンターが検査するのは`roadmaps/`へ入るリンクであって出るリンクではないため、
   書き換えを怠るとゲートは通ったままリンクだけが壊れます。
4. **textlint実行環境の移設**：APMはスキルのソースディレクトリ配下のファイルをすべて複製します。
   コントリビューターがそこに`npm ci`で作った`node_modules/`も複製の対象です。そこで、今日
   `.agent-workflows/document-writing/textlint/`にあるtextlintの実行環境を、スキルツリーの外の
   リポジトリパスへ移します。`document-writing`スキルと
   [`.github/dependabot.yml`](../../.github/dependabot.yml)のnpmエントリは、移設先を指すように
   更新します。
5. **ツール整備**：`make skills`で`apm install`を、`make lint-skills`で`apm audit --ci`を実行します。
   `apm audit --ci`は一時ディレクトリへインストールを再現し、ソースと異なる配備ファイルを報告します。
   `make check`にはこの検査を加え、`apm`が入っていない環境では`lint-actions`や`lint-secrets`と
   同じく通知を出して飛ばします。
   [session-startフック](../../.claude/hooks/session-start.sh)は、バージョンを固定した`apm-cli`を
   導入して`apm install`を実行し、webセッションがコミット済みのスキル集合から始まるようにします。
6. **ドキュメント**：[`CLAUDE.md`](../../CLAUDE.md)の*Agent skill layout*節を、単一ソースの構成に
   書き換えます。[`AGENTS.md`](../../AGENTS.md)、
   [`docs/ja/ai-development.md`](../../docs/ja/ai-development.md)と英語版、
   `docs/contributor-workflow-tutorial.md`と日本語版、
   [`.github/claude-review-prompt.md`](../../.github/claude-review-prompt.md)のスキルへのリンクは、
   新しいパスへ移します。

本項目が触れるのは、コントリビューター向けのツールとドキュメントだけです。`bajutsu/`と
`BajutsuKit/`、そして`run`とCIの判定経路は、いずれも変更しません。ゲートに検査を1つ加えたあとも
プライムディレクティブ1は保たれます。`apm audit`はロックファイルに記録したSHA-256とワーキング
ツリーを突き合わせる決定的な検査であり、判定に言語モデルは介在しません。

以上の設計は、ドキュメントだけでなくAPM 0.28.0の実挙動を確認したうえで組み立てています。ローカルの
`.apm/skills/<name>/`は、frontmatterを含めて`.claude/skills/<name>/`へそのまま複製されました。
`references/`と`scripts/`も、実行権限ごと複製されました。`targets: [claude]`だけを宣言した場合、
`.agents/`ツリーは生成されませんでした。`apm.lock.yaml`には、配備ファイルごとのSHA-256が記録され
ました。配備ファイルを手で書き換えると`apm audit`がずれとして報告し、次の`apm install`が書き戻し
ました。

## 検討した代替案

- **3つのツリーを保ったまま、整合性リンターを追加する**：不採用とします。リンターが捉えられるのは
  アダプターの書き忘れであり、問題のうち小さいほうです。アダプターの重複と、1つの手順のために
  ワークフロー全体を文脈へ取り込む二段読み込みは、どちらも残ります。
- **Codexを第2のターゲットとして残す**：不採用とします。このプロジェクトでCodexを使う人はおらず、
  APMはClaude以外のホスト向けスキルを`.agents/skills/`へ配備します。配備先は、現在の`.agents`
  シンボリックリンクが占めているパスそのものです。両方を支えるには、利用者のいないホストのために
  この衝突を解消し続けることになります。
- **配備先をコミットせず、gitignoreする**：不採用とします。クローンしただけの状態では、誰かがAPMを
  導入して実行するまでスキルが1つもない状態になり、本項目が求める統一とは逆の結果を招きます。
  APM自身の指針も、配備物のコミットです。代償として、各スキルのバイト列がソースと配備物の2箇所で
  追跡されます。`SKILL.md`をAPMの規約の範囲に収めることで、代償の大きさを抑えます。
- **プラグイン、MCPサーバー、フックも`apm.yml`に載せる**：不採用ではなく、後続に回します。APMは
  3種類とも配備できますが、フックの配備先は`.claude/settings.json`です。このリポジトリは、
  SessionStartフックのために同じファイルを手で管理しています。本項目が論じる統一は、スキルだけで
  達成できます。残りは、設定ファイルの所有者を決めたあとで進められます。
- **外部レジストリからスキルを導入する**：本項目の範囲外とします。APMはリモートのパッケージを
  `api.github.com`経由で解決しますが、このホストを遮断するサンドボックスがあります。本項目が扱うのは
  Bajutsu自身のスキルであり、ワーキングツリーから解決できるため、ネットワークを必要としません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] マニフェスト、ソースツリー、コミットする配備先（`apm.yml`、`.apm/skills/`、`.gitignore`）
- [ ] 14個のスキルを1つの`SKILL.md`へ変換し、詳細を`references/`へ配置
- [ ] `.agent-workflows/`、`.agent-hosts/`、`.agents`の撤去とロードマップのリンク書き換え
- [ ] textlint実行環境の移設と`dependabot.yml`のエントリ更新
- [ ] `make skills`、`make lint-skills`、`make check`への組み込み、session-startフック
- [ ] ドキュメント：`CLAUDE.md`、`AGENTS.md`、`docs/ai-development.md`（両言語）、コントリビューター向けチュートリアル（両言語）、レビュー契約

## 参考

- [APM quickstart](https://microsoft.github.io/apm/quickstart/) — 本項目が採用するマニフェスト、
  ロックファイル、インストールの手順。
- [APM skill authoring](https://microsoft.github.io/apm/producer/author-primitives/skills/) —
  `.apm/skills/<name>/`の配置と`SKILL.md`の分量の規約。
- [APM targets matrix](https://microsoft.github.io/apm/reference/targets-matrix/) — ホストごとの
  配備先の一覧であり、上で述べた`.agents/skills/`の衝突の出典。
- [BE-0103](../BE-0103-dev-model-effort-tiering/BE-0103-dev-model-effort-tiering-ja.md) —
  移行後も残る`model:`のfrontmatterが担う、モデルと推論負荷の使い分け。
- [BE-0379](../BE-0379-be-progress-tracker/BE-0379-be-progress-tracker-ja.md)、
  [BE-0380](../BE-0380-fix-issue-skill/BE-0380-fix-issue-skill-ja.md) — 本項目が撤去する3ツリー
  構成を前提に設計された、直近の2項目。

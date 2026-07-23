[English](BE-0122-workflow-name-legibility.md) · **日本語**

# BE-0122 — Legible GitHub Actions workflow and job names

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0122](BE-0122-workflow-name-legibility-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0122") |
| 実装 PR | [#611](https://github.com/bajutsu-e2e/bajutsu/pull/611) |
| トピック | コントリビューターワークフロー |
<!-- /BE-METADATA -->

## はじめに

Bajutsu の CI は 13 本のワークフロー（`.github/workflows/*.yml`）まで増えています。コア
ゲート、ドキュメント、依存関係監査、idb 互換性の定期監視、PR タイトルの静的チェック、ロード
マップ自動化の 4 本、auto-merge、実機・Web の E2E スイートです。GitHub の Actions タブや PR
の checks 一覧では、レビュアーには各ワークフローの `name:` と各ジョブの `name:` しか見えず、
背後の YAML は見えません。このうち `docs`、`pr-title`、`roadmap-id`、`roadmap-promote`、
`roadmap-proposal-approvals`、`roadmap-tracking-issues`、`dependency audit`、`idb monitor`、
`web e2e`、`auto-merge` は、文脈のないキーワード 1 語のまま名付けられています。checks 一覧が
赤くなったとき、レビュアーはこれらのどれが何を検証しているのか、実行結果を開かないと判断でき
ません。一方で `E2E (Simulator)` と `Swift (BajutsuKit)` の 2 本は、すでにこの提案が目指す形
を示しています。短い句に、何を対象にしているかを示す括弧書きを添える形式です。本項目は、残り
のワークフローを同じ形式に揃えます。`name:` フィールドだけを変更するドキュメントのみの変更で
あり、実行時の挙動は変わりません。

## 動機

- **checks 一覧は、ワークフローファイルそのものより遥かに頻繁に読まれます。** 人間・エージ
  ェントを問わず、あらゆる PR はまず checks 一覧でトリアージされます。名前から中身を推測でき
  ず実行結果を開く必要があるなら、赤くなった 1 件ごとにクリックが 1 回増えます。本リポジトリ
  は多数のセッションを並行して走らせる運用（[CLAUDE.md](../../CLAUDE.md) の「並行作業」節
  参照）なので、この積み重ねは無視できません。
- **良い形式はすでに存在するのに、適用が一貫していません。** `E2E (Simulator)` と
  `Swift (BajutsuKit)` は、目指すべき形式が一度は見つかっていたことを示しています。残る 10 本
  のワークフローには同じ手当てが及んでおらず、リポジトリを読むだけでは新しい貢献者がこの慣例
  に気付けません。
- **これは正しさの問題ではなく、読みやすさの問題です。**
  [BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening-ja.md)
  はゲートが何を検証するかを強化しました。本項目はその結果の表示名だけを変えるものであり、
  BE-0067 が抱えていたような挙動面のリスクはありません。

## 詳細設計

作業はメタデータのリネームとドキュメント追記のみです。ジョブのロジック・トリガー・権限は変更
しません。

1. **文脈のないトップレベルのワークフロー `name:` を、短く説明的な句にリネームします。**
   `E2E (Simulator)` や `Swift (BajutsuKit)` がすでに示している形式、すなわち動作を示す平易な
   句に、対象のツールや範囲を示す括弧書きを添える（情報が増える場合のみ）形式に揃えます。対応
   の例を次に示します（最終的な文言は実装時に決めます）。

   | ファイル | 現在の `name:` | 変更後の `name:` |
   |---|---|---|
   | `docs.yml` | `docs` | `Docs site (build & deploy)` |
   | `pr-title.yml` | `pr-title` | `PR title lint` |
   | `roadmap-id.yml` | `roadmap-id` | `Roadmap: allocate BE IDs` |
   | `roadmap-promote.yml` | `roadmap-promote` | `Roadmap: promote shipped items` |
   | `roadmap-proposal-approvals.yml` | `roadmap-proposal-approvals` | `Roadmap: require two approvals (proposals)` |
   | `roadmap-tracking-issues.yml` | `roadmap-tracking-issues` | `Roadmap: sync tracking issues` |
   | `dependency-audit.yml` | `dependency audit` | `Dependency audit (pip-audit)` |
   | `idb-monitor.yml` | `idb monitor` | `idb compatibility monitor` |
   | `web-e2e.yml` | `web e2e` | `Web E2E (Playwright)` |
   | `auto-merge.yml` | `auto-merge` | `Auto-merge (bypass App)` |

   `ci.yml`（`CI`）、`e2e.yml`（`E2E (Simulator)`）、`swift.yml`（`Swift (BajutsuKit)`）は、す
   でに目指す形式になっているため変更しません。

2. **文脈のないジョブ `name:` も同じ方針でリネームします。** ジョブ名がそっけない場合
   （`docs.yml` の `build`、`deploy`）や、ワークフロー名を変えた結果ジョブ名と意味が重複する
   場合（`Web E2E (Playwright)` というワークフロー名になったあとの `web e2e (playwright)` と
   いうジョブ名など）が対象です。対応の例を次に示します。

   | ファイル | ジョブ | 現在 | 変更後 |
   |---|---|---|---|
   | `docs.yml` | build | `build` | `build site` |
   | `docs.yml` | deploy | `deploy` | `deploy to GitHub Pages` |
   | `dependency-audit.yml` | audit | `dependency audit (pip-audit)` | `audit (pip-audit)` |
   | `web-e2e.yml` | smoke | `web e2e (playwright)` | `smoke (playwright)`（`e2e.yml` の `smoke (idb)` に合わせた形） |

3. **3 つのジョブ名だけは変更せず、その理由を明文化します。** 本リポジトリのブランチ保護
   ルールセット（`Require code review`。`gh api repos/bajutsu-e2e/bajutsu/rulesets/<id>` で確
   認できます）は、`required_status_checks` を `check`（`ci.yml`）、`E2E`（`e2e.yml` の最終
   ゲートジョブ）、`require two approvals for BE proposals`
   （`roadmap-proposal-approvals.yml`）という、ジョブ名そのものに固定しています。GitHub
   Actions の required status check は、ワークフロー名ではなくジョブの `name:` をそのまま
   コンテキストとして使います。ルールセットの更新と同時でなくこの 3 つのどれかをリネームする
   と、開かれているすべての PR が、二度と報告されないチェックを待ち続けてマージできなくなり
   ます。この 3 つは本項目では現状のまま残し、将来これらをリネームする作業は本項目のスコープ
   外とします。実施する場合は、ルールセットの `required_status_checks` の更新を必ず同時に行っ
   てください（ルールセットの編集は通常の PR からは届かない、リポジトリ外の管理者操作であり、
   人手での作業になります）。

4. **命名の慣例をドキュメント化します。** [`docs/ai-development.md`](../../docs/ai-development.md)
   （英語）とその日本語版に、「GitHub Actions のワークフロー名・ジョブ名の付け方」という短い
   節を追加し、形式（短い句＋必要な場合のみツールや範囲を示す括弧書き、単語 1 語だけの
   `name:` は避ける）を示します。`e2e.yml` と `swift.yml` を模範例として挙げ、項目 3 で述べた
   required status check の制約にも触れ、将来のリネームで同じ失敗を繰り返さないようにします。

5. **手作業で検証します。** `actionlint`（`make check` に組み込み済み）はワークフローの構文は
   検証しますが、命名については何も言いません。したがってここに自動チェックは追加しません。
   検証は、ブランチを push したうえで Actions タブと PR の checks 一覧を実際に読み、実行結果
   を開かなくてもそれぞれの名前だけで内容がわかることを確認する形で行います。

## 検討した代替案

- **`[Roadmap] allocate BE IDs`、`[Docs] build & deploy` のような、角括弧によるカテゴリ接頭
  辞を付ける案。** 採用しません。GitHub の checks 一覧はすでにジョブをワークフロー単位で視覚
  的にグルーピングしており、角括弧接頭辞はそのグルーピングを文字列としてなぞるだけで新しい情
  報を増やしません。しかも 4 本の `roadmap-*` 系ワークフローは、項目 1 のリネームだけで
  `Roadmap: …` という平易な句に揃い、ひとまとまりの系列として読めるようになります。角括弧を足
  してもそれ以上の効果はありません。
- **項目 3 の 3 つの保護対象ジョブ名も同じ PR でリネームし、ルールセットも同時に更新する案。**
  本項目のスコープには含めません。ルールセットの編集は通常の PR からは届かないリポジトリ外の
  管理者操作であり、リネームとルールセット更新の順序を誤ると（リネームが先に着地する、あるい
  はその逆）、マージが止まります。本項目とは別に追跡する、明示的な人手のフォローアップ（項目
  3）として切り離し、それ以外はリスクのないドキュメントのみの変更にとどめます。
- **何もしない案。** 採用しません。このリネームはドキュメントのみで実行時のリスクがない一方、
  現状維持を続けると、checks 一覧を読むすべての貢献者・エージェントセッションに小さいながらも
  繰り返しのコストを課し続けます。本リポジトリの並行作業のスタイルでは、この頻度は無視できま
  せん。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 文脈のないトップレベルのワークフロー `name:` をリネームする（項目 1）
- [x] 文脈のないジョブ `name:` を、保護対象のチェックを除いてリネームする（項目 2）
- [x] ルールセットで保護されている 3 つのジョブ名は変更しない（項目 3。コード変更はないが、
      項目 2 に紛れ込まないよう明示的に追跡する）
- [x] `docs/ai-development.md`（英語・日本語）に命名の慣例を明文化する（項目 4）
- [ ] リネーム後、Actions タブと PR の checks 一覧が実際に読みやすくなったことを手動で確認する
      （項目 5。チェック名は実際の run でしか表示されないため、ブランチを push したあとに行う）

ログ：

- トップレベルのワークフロー `name:` 10 本（`docs`、`pr-title`、`roadmap-*` の 4 本、
  `dependency audit`、`idb monitor`、`web e2e`、`auto-merge`）と、`docs.yml`、`dependency-audit.yml`、
  `web-e2e.yml` の冗長またはそっけないジョブ名を、`E2E (Simulator)` や `Swift (BajutsuKit)` の形式
  にリネームしました。`Roadmap: …` の 4 本はコロンと空白を含むため、YAML として有効なまま保つよう引
  用符で囲んでいます。ルールセットで保護されている 3 つのジョブ名（`check`、`E2E`、
  `require two approvals for BE proposals`）は現状のまま残しました。本項目の起案後に追加された
  `codeql.yml` はすでに読みやすいので、`ci.yml`、`e2e.yml`、`swift.yml` と同じく変更していません。
  命名の慣例と required status check の制約は `docs/ai-development.md` とその日本語版に明文化しました。
  検証は `make check` で行い、読みやすさそのもの（項目 5）はブランチを push したあとに Actions タブを
  読んで確認します。
- レビューを受けた追随。`dependency-audit.yml` のジョブ名を、ツール名だけの `pip-audit` から
  `audit (pip-audit)` に変更しました。平易な句にツールの括弧書きを添えた形で、本項目の命名の慣例に沿い、
  ワークフロー名をなぞるのではなく `smoke (idb)` や `smoke (playwright)` と同じ形にそろえたものです。
  あわせて、日本語の命名の慣例の節を、語中で改行が入らないよう整形し直しました。

## 参考

- [`.github/workflows/`](../../.github/workflows/) — 本項目がリネームするワークフロー
- [`docs/ai-development.md`](../../docs/ai-development.md) — 命名の慣例を明文化する先
- [BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening-ja.md) —
  本項目が補完する、先行する CI 強化項目（正しさ側 vs. 読みやすさ側）
- GitHub Actions のドキュメント（required status check がジョブの `name:` をチェックの
  コンテキストとして使う仕様について）

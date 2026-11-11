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

Bajutsu の CI は、13 本のワークフロー（`.github/workflows/*.yml`）まで増えています。内訳は、コアゲート、ドキュメント、依存関係監査、idb 互換性の定期監視、PR タイトルの静的チェック、ロードマップ自動化の 4 本、auto-merge、実機と Web の E2E スイートです。GitHub の Actions タブと PR の checks 一覧に出るのは、各ワークフローの `name:` と各ジョブの `name:` です。背後の YAML は、レビュアーには見えません。この見え方では、複数のワークフローが、文脈のないキーワード 1 語として読めます。具体的には、`docs`、`pr-title`、`roadmap-id`、`roadmap-promote`、`roadmap-proposal-approvals`、`roadmap-tracking-issues`、`dependency audit`、`idb monitor`、`web e2e`、`auto-merge` です。

checks 一覧が赤くなったとき、レビュアーは、実行結果を開かないと、これらのどれが何を検証しているのか判断できません。本項目が一般化する形は、すでに 2 本のワークフローが示しています。`E2E (Simulator)` と `Swift (BajutsuKit)` は、短い句に、何を実行しているかを示す括弧書きを添えています。本項目は、残りのワークフローを同じ形に揃えます。変更はドキュメントのみで、`name:` フィールドに触れ、実行時の挙動は変えません。

## 動機

- **checks 一覧は、ワークフローファイルそのものより、はるかに頻繁に読まれます。** 人間かエージェントかを問わず、あらゆる PR は、まず checks 一覧でトリアージされます。名前を読み解くために実行結果を開く必要があれば、赤くなったビルドごとに、クリックが 1 回増えます。本リポジトリは多数のセッションを並行して走らせるので、このコストは積み重なります（[CLAUDE.md](../../CLAUDE.md) の「並行作業」節を参照）。
- **よい形はすでに存在するのに、適用が一貫していません。** `E2E (Simulator)` と `Swift (BajutsuKit)` は、目指す形がすでに一度は見つかっていたことを示します。残る 10 本のワークフローには、同じ手当てが及んでいません。そのため、新しい貢献者は、リポジトリを読むだけではこの慣例に気付けません。
- **これは正しさの問題ではなく、読みやすさの問題です。** [BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening-ja.md) は、ゲートが何を検証するかを強化しました。本項目が変えるのは、その結果の表示名です。したがって、BE-0067 が抱えていた挙動面のリスクはありません。

## 詳細設計

作業は、メタデータのリネームと、ドキュメントへの追記だけです。ジョブのロジック、トリガー、権限は変更しません。

1. **文脈のないトップレベルのワークフロー `name:` を、短く説明的な句にリネームします。** `E2E (Simulator)` と `Swift (BajutsuKit)` がすでに示している形に揃えます。つまり、動作を示す平易な句です。対象のツールや範囲を示す括弧書きは、情報が増える場合に限って添えます。次の対応は例であり、最終的な文言は実装時に決めます。

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

   `ci.yml`（`CI`）、`e2e.yml`（`E2E (Simulator)`）、`swift.yml`（`Swift (BajutsuKit)`）は、すでに目指す形になっています。本項目では、これらを変更しません。

2. **文脈のないジョブ `name:` も、同じ方針でリネームします。** 対象は、ジョブ名がそっけない場合です（`docs.yml` の `build` と `deploy`）。ワークフロー名を変えた結果、ジョブ名と意味が重複する場合も対象です。例は、`Web E2E (Playwright)` というワークフロー名になったあとの、`web e2e (playwright)` というジョブ名です。次の対応は例です。

   | ファイル | ジョブ | 現在 | 変更後 |
   |---|---|---|---|
   | `docs.yml` | build | `build` | `build site` |
   | `docs.yml` | deploy | `deploy` | `deploy to GitHub Pages` |
   | `dependency-audit.yml` | audit | `dependency audit (pip-audit)` | `audit (pip-audit)` |
   | `web-e2e.yml` | smoke | `web e2e (playwright)` | `smoke (playwright)`（`e2e.yml` の `smoke (idb)` に合わせた形） |

3. **3 つのジョブ名だけは変更せず、その理由を明文化します。** 本リポジトリのブランチ保護ルールセット（`Require code review`。`gh api repos/bajutsu-e2e/bajutsu/rulesets/<id>` で確認できます）は、`required_status_checks` を、ジョブ名そのものに固定しています。固定先は、`check`（`ci.yml`）、`E2E`（`e2e.yml` の最終ゲートジョブ）、`require two approvals for BE proposals`（`roadmap-proposal-approvals.yml`）の 3 つです。

   GitHub Actions の required status check は、ワークフロー名ではなく、ジョブの `name:` をそのままコンテキストとして使います。ルールセットの更新と同時でなく、この 3 つのどれかをリネームすると、開かれているすべての PR が、二度と報告されないチェックを待ち続けます。マージは、何の通知もなく止まります。本項目は、この 3 つを現状のまま残します。

   将来、この 3 つのどれかをリネームする作業は、本項目のスコープ外です。その場合は、ルールセットの `required_status_checks` のコンテキストを、管理者が同時に編集する必要があります。この編集は人手の作業です。ルールセットの編集は、リポジトリ外の管理者操作で、通常の PR からは届かないからです。

4. **命名の慣例をドキュメント化します。** [`docs/ai-development.md`](../../docs/ai-development.md)（英語）とその日本語版に、「GitHub Actions のワークフロー名・ジョブ名の付け方」という短い節を追加します。節は、形を示します。短い句であること、ツールや範囲を示す括弧書きを添えること、単語 1 語だけの `name:` を避けることです。模範例として、`e2e.yml` と `swift.yml` を挙げます。さらに、項目 3 の required status check の制約にも触れます。将来のリネームで、項目 3 が防ごうとしている失敗を繰り返さないためです。

5. **手作業で検証します。** `actionlint`（`make check` に組み込み済み）は、ワークフローの構文を検証しますが、命名については何も判断しません。そのため、ここに自動チェックは追加しません。検証は、ブランチを push したうえで、Actions タブと PR の checks 一覧を読んで行います。実行結果を開かなくても、それぞれの名前だけで内容がわかることを確認します。

## 検討した代替案

- **`[Roadmap] allocate BE IDs` や `[Docs] build & deploy` のような、角括弧によるカテゴリ接頭辞を付ける案。** 採用しません。GitHub の checks 一覧は、すでにジョブをワークフロー単位で視覚的にグルーピングしています。角括弧の接頭辞は、そのグルーピングを重ねるだけで、新しい情報を増やしません。しかも、4 本の `roadmap-*` 系ワークフローは、項目 1 のリネームで、`Roadmap: …` という平易な句を持ちます。それだけで、ひとまとまりの系列として読めます。角括弧を足しても、それ以上の効果はありません。
- **保護対象の 3 つのジョブ名も同じ PR でリネームし、ルールセットも同時に更新する案。** 本項目のスコープには含めません。ルールセットの編集は、リポジトリ外の管理者操作であり、通常の PR からは届きません。順序を誤ると、マージが止まります。誤りは、リネームが先に着地する場合と、その逆の場合です。この作業は、本項目とは別に追跡する、明示的な人手のフォローアップ（項目 3）として切り離します。そうすれば、本項目は、リスクのないドキュメントのみの変更にとどまります。
- **何もしない案。** 採用しません。このリネームは、ドキュメントのみで、実行時のリスクがありません。現状維持を続けると、checks 一覧を読むすべての貢献者とエージェントセッションに、小さいながら繰り返しのコストを課し続けます。本リポジトリの並行作業のスタイルでは、この読み取りは頻繁に起こります。

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

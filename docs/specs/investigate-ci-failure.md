# 開発CI失敗調査スキル

> ステータス: 実装完了
> 対象: `.apm/skills/investigate-ci-failure/`、`.apm/skills/pr-followup/`
> 関連: [BE-0361](../../roadmaps/BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md)、
> [BE-0367](../../roadmaps/BE-0367-android-ci-emulator-diagnostics/BE-0367-android-ci-emulator-diagnostics.md)、
> [docs/ci.md](../ci.md)、[investigate-scenario-flakiness](investigate-scenario-flakiness.md)

このリポジトリ自身の開発CI（`.github/workflows/`）が失敗したときに、原因を分類するエージェントスキルを追加します。
スキルは、分類と根拠を並べたレポートを返します。
分類は、ログにそのまま出ている不具合、既知のインフラ由来の揺れ、未知の症状のいずれかです。
[`pr-followup`](../../.apm/skills/pr-followup/SKILL.md) の Step 2 がこのスキルをサブステップとして呼びます。
修正そのものは pr-followup 側に残します。

## 1. なにをつくるのか

`.apm/skills/investigate-ci-failure/SKILL.md` を新規に作ります。
入力は PR 番号で、[`pr-followup`](../../.apm/skills/pr-followup/SKILL.md) の Step 2 から渡されます。
出力は、失敗しているチェックそれぞれに、次の4分類のいずれかとその根拠を添えたレポートです。

| 分類 | 意味 |
|---|---|
| `gate-mechanical` | `ci.yml` の `check` ジョブの失敗で、既知の1コマンドで直る種類（lockfileのずれ、フォーマット崩れ、skillデプロイのずれなど） |
| `code-defect` | lint、型、テストの失敗が、実際のコード上の問題を指している |
| `e2e-known-flake` | iOS、Android、webいずれかのE2Eレーンのジョブの失敗が、既知のインフラ由来の揺れに一致する |
| `e2e-unclassified` | E2Eレーンのジョブが失敗しているが、既知の型に一致せず、履歴照合でも不安定と判定されない |

判定の根拠には2つを使います。
1つは、[BE-0361](../../roadmaps/BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md)
と [BE-0367](../../roadmaps/BE-0367-android-ci-emulator-diagnostics/BE-0367-android-ci-emulator-diagnostics.md)
が積んでいる3層診断（`runs/diagnostics/` 配下のartifact）です。
もう1つは、[investigate-scenario-flakiness](investigate-scenario-flakiness.md) による過去run履歴の照合です。

### やらないこと

- **コードの修正、push、再実行はしません。** [`record-issue`](../../.apm/skills/record-issue/SKILL.md) と同じく、このスキルの範囲は分類と根拠の提示までです。実際に行動するのは、呼び出し元の [`pr-followup`](../../.apm/skills/pr-followup/SKILL.md) です。
- **`devicefarm.yml` と `ai-smoke.yml` は対象にしません。** どちらも `workflow_dispatch` 専用で、PRの必須チェックになりません。[`pr-followup`](../../.apm/skills/pr-followup/SKILL.md) の Step 1 が読む `gh pr checks` の対象にも出てこないため、扱う場面自体が生じません。
- **人間による単独起動の入力形式は、今回は作りません。** PR番号以外の入力（run-id、「直近のmain失敗」など）を受け付ける拡張は、今回のスコープから外します。理由は4章の代替案に書き残します。

## 2. なぜつくるのか

現状の [`pr-followup`](../../.apm/skills/pr-followup/SKILL.md) の Step 2 は一段構えの手順です。
`gh run view --log-failed` でログを読み、原因を特定し、直接直します。
`ci.yml` の `check` ジョブのように、失敗理由がログにそのまま出るケースには、この手順で足ります。
iOS、Android、webの各E2Eレーンには足りません。

[BE-0361](../../roadmaps/BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md)
と [BE-0367](../../roadmaps/BE-0367-android-ci-emulator-diagnostics/BE-0367-android-ci-emulator-diagnostics.md)
が3層診断を追加しました。
3層診断は `runs/diagnostics/` 配下のartifactとして積まれるだけで、`gh run view --log-failed` の出力には現れません。
したがって現状の Step 2 は、E2Eレーンの失敗に対して診断artifactを一切見ずに、ログの断片だけから原因を推測しています。

診断artifactを見ないと、2つの失敗の仕方が生じます。
前提として、[docs/ci.md](../ci.md) が列挙する既知のインフラ由来の揺れがあります。
例は、`SimRenderServer` のクラッシュ、`CoreSimulator` の応答待ち、Androidレンダラのwedgeです。
1つ目の失敗は、既知の揺れをコードの回帰と誤認し、無関係な修正をpushしてCIをもう一度回すことです。
2つ目は逆に、本当の回帰を「いつもの揺れ」と決めつけて見送ることです。
どちらも、[`pr-followup`](../../.apm/skills/pr-followup/SKILL.md) のループが「push、CI待ち、また外れる」を繰り返すコストとしてそのまま跳ね返ります。

[BE-0220](../../roadmaps/BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix.md)
の `triage --flaky` は、この2つの失敗の仕方を、人間の勘ではなくデータで切り分ける仕組みをすでに持っています。
データとは、同一シナリオの過去run群における合否の分布です。
ただしCIの実行結果は、どこにも履歴として蓄積されていません。
`serve-db.yml` はPostgres方言のテストレーンであり、CIの実行結果を蓄積する仕組みではありません。
この履歴の不在が、[`pr-followup`](../../.apm/skills/pr-followup/SKILL.md) がまだ `triage --flaky` を使えていない直接の理由です。
investigate-ci-failureは、この不在を埋めます。
`gh run download` で直近のrunをその場で履歴ディレクトリに再構成し、[investigate-scenario-flakiness](investigate-scenario-flakiness.md) に渡します。

## 3. どう実現するか

### 配置

- `.apm/skills/investigate-ci-failure/SKILL.md`（手順の本体）
- `.apm/skills/investigate-ci-failure/references/known-ci-failure-patterns.md`：既知のインフラ由来flakyパターンの一覧です。最初の項目は、[docs/ci.md](../ci.md) が本文中で個別に触れている症状から書き起こします。例は、`Timed out while requesting screenshot` の再現、`CoreSimulator` の応答待ち、Androidレンダラのwedgeです。以後、このスキルが新しいパターンを確認するたびに追記します。

### 入力

PR番号1つです。[`pr-followup`](../../.apm/skills/pr-followup/SKILL.md) の Step 2 から渡されます。

### 手順

1. `gh pr checks <PR>` で、失敗しているチェックを列挙します。
   それぞれについて `gh run view <run-id> --json jobs` でジョブ名を取り、次のどれかに判別します。
   - `ci.yml` の `check` ジョブ
   - E2Eレーンのジョブ
   - それ以外（`pr-title`、`roadmap-id`、`mcp-wire` など）
2. `check` ジョブの失敗は、`gh run view --log-failed` で失敗したstep名を読み、`known-ci-failure-patterns.md` の `gate-mechanical` 一覧と照合します。
   一覧には、lockfileのずれ、フォーマット崩れ、skillデプロイのずれのそれぞれについて、1コマンドの直し方が書いてあります。
   一致すれば、`gate-mechanical` として直し方ごと返します。
   一致しなければ、ログの該当箇所を添えて `code-defect` として返します。
3. E2Eレーンのジョブの失敗は、次の順で判別します。
   まず、`collect-ios-diagnostics`、`collect-android-diagnostics`、ホスト側サンプラーが `runs/diagnostics/` 配下に積んだartifactを、`gh run download <run-id> -n <artifact名>` で取得します。
   次に、取得したartifactを `known-ci-failure-patterns.md` の `e2e-known-flake` 一覧と突き合わせます。
4. 手順3で判別できない場合は、履歴照合へ進みます。
   「判別できない」とは、一致するパターンがない場合、または初めて見る症状の場合を指します。
   対象は、同じワークフロー、同じジョブ名の直近runです。
   件数のデフォルトは20件で、`main` へのpushとmerge queueのrunに限ります。
   `gh run list --workflow <wf> -L 20 --json databaseId,conclusion,headBranch,createdAt` で直近runを列挙し、それぞれの対象artifactを `gh run download` で1つのディレクトリにまとめます。
   このディレクトリを [investigate-scenario-flakiness](investigate-scenario-flakiness.md) に渡します。
   `use_ai` は渡しません。分類に要るのは `classification` の値だけで、triageの原因仮説は要らないためです。
   返ってきたレポートで対象シナリオが `flaky` 判定なら、`e2e-known-flake` とします。これは、データに基づく新しいパターンとしての判定です。
   そうでなければ、`e2e-unclassified` として返します。
5. 手順4で `e2e-known-flake` と判定した場合は、確認した症状を `known-ci-failure-patterns.md` に追記します。
   追記はファイルの変更にとどめ、その変更をpushするかどうかは判断しません。
   判断は、呼び出し元の [`pr-followup`](../../.apm/skills/pr-followup/SKILL.md) に委ねます。
6. 全チェックの分類結果を、1本のレポートにまとめて返します。
   各項目は、チェック名、分類、根拠、推奨アクションの4つで構成します。
   根拠は、ログの該当行、artifactのパス、または [investigate-scenario-flakiness](investigate-scenario-flakiness.md) が返したシナリオ名と `flip_rate` です。
   推奨アクションは、分類ごとに次のとおりです。
   - `gate-mechanical`：直し方のコマンド
   - `code-defect`：「通常の修正フローに進む」
   - `e2e-known-flake`：「再実行のみで足りる可能性が高い」
   - `e2e-unclassified`：「人間の判断が要る」

### `pr-followup` 側の変更

Step 2 の冒頭でinvestigate-ci-failureを呼び、返ってきた分類に応じて次のように分岐させます。

- `gate-mechanical` は、レポートに書かれたコマンドをそのまま実行します。
- `code-defect` は、現行の Step 2 の手順（ログを読み、直す）をそのまま続けます。
- `e2e-known-flake` は、`gh run rerun --failed <run-id>` で再実行を要求します。コードは変更しません。
- `e2e-unclassified` は、[`pr-followup`](../../.apm/skills/pr-followup/SKILL.md) の既存のエスカレーション経路に載せ、人間の判断を仰ぎます。

## 4. 検討した代替案と、採らなかった理由

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| `pr-followup` のStep 2に直接書き足す（別スキルにしない） | 分類手順を `pr-followup` 自身のSKILL.mdに追記します | いずれ人間が単独起動したい場面（`main` が赤い、`devicefarm` の謎の失敗を調べたいなど）が別に想定されており、[`record-issue`](../../.apm/skills/record-issue/SKILL.md) と同じ「サブステップとして切り出す」形にしておけば、その拡張が `pr-followup` 側を触らずに済みます |
| 既知パターンの参照ファイルを持たず、毎回diagnostics artifactと[investigate-scenario-flakiness](investigate-scenario-flakiness.md)の結果だけから判断する | `known-ci-failure-patterns.md` を作りません | 同じ症状（`CoreSimulator` の応答待ちなど）を毎回diagnostics artifactの読み直しからやり直すことになり、過去に確認済みの原因が次の調査に持ち越されません。[docs/ci.md](../ci.md) はすでに個別の事例に触れていますが、「症状から分類へ」引ける一覧としては整備されていないため、検索と追記の単位として専用の参照ファイルを別に持つほうが扱いやすいと判断しました |
| E2Eレーンの未分類ケースでも、履歴照合（`gh run download` をN回）を毎回自動で行う | 手順4の分岐を作らず、E2Eレーンの失敗は常に直近run履歴を集めます | 履歴照合はartifactのダウンロードを複数回伴い、コストが高くなります。`known-ci-failure-patterns.md` との一致で説明がつく大半のケースでは不要な出費になるため、一致しないときの最後の手段として位置づけました |

## 5. 作業手順

| # | やること | 触るファイル | 完了条件 | 前提 |
|---|---|---|---|---|
| 1 [x] | `.apm/skills/investigate-ci-failure/SKILL.md` を新規作成し、3章の手順（1から6）を書きます | `.apm/skills/investigate-ci-failure/SKILL.md` | `make lint-skills` が通ります | [investigate-scenario-flakiness](investigate-scenario-flakiness.md) が先に実装されています（手順4がサブステップとして呼ぶため） |
| 2 [x] | `known-ci-failure-patterns.md` を新規作成し、[docs/ci.md](../ci.md) がすでに触れている既知のインフラ由来flakyパターンを書き起こします | `.apm/skills/investigate-ci-failure/references/known-ci-failure-patterns.md` | 1のSKILL.mdから参照が通ります | 1 |
| 3 [x] | `make skills` を実行し、`.claude/skills/` 側へデプロイします | `.claude/skills/investigate-ci-failure/`（生成）、`apm.lock.yaml` | `apm audit --ci` が通ります | 1、2 |
| 4 [x] | `pr-followup` のStep 2を改訂し、investigate-ci-failureを先頭で呼ぶ分岐（3章「`pr-followup` 側の変更」）を書きます | `.apm/skills/pr-followup/SKILL.md` | `make lint-skills` が通り、既存のStep 3以降の記述と矛盾しません | 3 |
| 5 [x] | [docs/ci.md](../ci.md)の「What a failing E2E job collects」節末尾に、E2Eレーンの失敗調査にこのスキルを使う旨を一言添えます | `docs/ci.md`、`docs/ja/ci.md` | 日英両方が更新され、japanese-document-writingのtextlintが通ります | 4 |
| 6 [x] | `make check` を実行します | なし | 全ステップがgreenになります | 1から5 |

# 開発CI失敗調査スキル

> ステータス: 実装完了
> 対象: `.apm/skills/investigate-ci-failure/`、`.apm/skills/pr-followup/`
> 関連: [BE-0361](../../roadmaps/BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md)、
> [BE-0367](../../roadmaps/BE-0367-android-ci-emulator-diagnostics/BE-0367-android-ci-emulator-diagnostics.md)、
> [docs/ci.md](../ci.md)、[investigate-scenario-flakiness](investigate-scenario-flakiness.md)

このリポジトリ自身の開発CI（`.github/workflows/`）が失敗したとき、原因を分類し、根拠付きのレポート
として返すエージェントスキルを追加します。分類は「ログにそのまま出ている不具合」「既知のインフラ由来
の揺れ」「未知の症状」のいずれかです。[`pr-followup`](../../.apm/skills/pr-followup/SKILL.md) の
Step 2からサブステップとして呼ばれ、修正そのものはpr-followup側に残します。

## 1. なにをつくるのか

`.apm/skills/investigate-ci-failure/SKILL.md` を新規に作ります。入力はPR番号で、
[`pr-followup`](../../.apm/skills/pr-followup/SKILL.md) のStep 2から渡されます。出力は、失敗して
いるチェックそれぞれについて次の4分類のいずれかと、その根拠を並べたレポートです。

| 分類 | 意味 |
|---|---|
| `gate-mechanical` | `ci.yml` の `check` ジョブの失敗で、既知の1コマンドで直る種類（lockfileのずれ、フォーマット崩れ、skillデプロイのずれなど） |
| `code-defect` | lint、型、テストの失敗が、実際のコード上の問題を指している |
| `e2e-known-flake` | iOS、Android、webいずれかのE2Eレーンのジョブの失敗が、既知のインフラ由来の揺れに一致する |
| `e2e-unclassified` | E2Eレーンのジョブが失敗しているが、既知の型に一致せず、履歴照合でも不安定と判定されない |

判定の根拠には2つを使います。1つは、
[BE-0361](../../roadmaps/BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md)
/
[BE-0367](../../roadmaps/BE-0367-android-ci-emulator-diagnostics/BE-0367-android-ci-emulator-diagnostics.md)
が積んでいる3層診断（`runs/diagnostics/` 配下のartifact）です。もう1つは、
[investigate-scenario-flakiness](investigate-scenario-flakiness.md) による過去run履歴の照合です。

### やらないこと

- **コードの修正、push、再実行はしない。** [`record-issue`](../../.apm/skills/record-issue/SKILL.md)
  と同じく、分類と根拠の提示までがこのスキルの範囲であり、実際に行動するのは呼び出し元の
  [`pr-followup`](../../.apm/skills/pr-followup/SKILL.md) である。
- **`devicefarm.yml` / `ai-smoke.yml` は対象にしない。** どちらも `workflow_dispatch` 専用で、PRの
  必須チェックにならない。[`pr-followup`](../../.apm/skills/pr-followup/SKILL.md) のStep 1が読む
  `gh pr checks` の対象にも出てこないため、扱う場面自体が生じない。
- **人間による単独起動の入力形式は今回作らない。** PR番号以外の入力（run-id、「直近のmain失敗」
  など）を受け付ける拡張は、4章の代替案に理由とともに書き残し、今回のスコープからは外す。

## 2. なぜつくるのか

現状の [`pr-followup`](../../.apm/skills/pr-followup/SKILL.md) のStep 2は一段構えの手順です。
`gh run view --log-failed` でログを読み、原因を特定し、直接直します。`ci.yml` の `check` ジョブの
ように失敗理由がログにそのまま出るケースはこれで足ります。ただし、iOS、Android、webの各E2Eレーンでは
足りません。
[BE-0361](../../roadmaps/BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md)
/
[BE-0367](../../roadmaps/BE-0367-android-ci-emulator-diagnostics/BE-0367-android-ci-emulator-diagnostics.md)
が3層診断を追加しました。それは `runs/diagnostics/` 配下のartifactとして積まれるだけです。
`gh run view --log-failed` の出力には現れません。したがって現状のStep 2は、E2Eレーンの失敗に対して
診断artifactを一切見ずに、ログの断片だけから原因を推測しています。

診断artifactを見ないことは、2つの失敗の仕方につながります。[docs/ci.md](../ci.md) が列挙する既知の
インフラ由来の揺れ（`SimRenderServer` のクラッシュ、`CoreSimulator` の応答待ち、Androidレンダラの
wedgeなど）があります。1つは、これをコードの回帰と誤認し、無関係な修正をpushしてCIをもう一度回す
ことです。もう1つは逆に、本当の回帰を「いつもの揺れ」と決めつけて見送ることです。どちらも、
[`pr-followup`](../../.apm/skills/pr-followup/SKILL.md) のループが「push、CI待ち、また外れる」を
繰り返すコストとしてそのまま跳ね返ります。

[BE-0220](../../roadmaps/BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix.md)
の `triage --flaky` は、この2つの失敗の仕方を人間の勘ではなくデータ（同一シナリオの過去run群における
合否の分布）で切り分ける仕組みをすでに持っています。ただしCIの実行結果はどこにも履歴として蓄積されて
いません。`serve-db.yml` はPostgres方言のテストレーンであり、CIの実行結果を蓄積する仕組みでは
ありません。この履歴の不在こそが、[`pr-followup`](../../.apm/skills/pr-followup/SKILL.md) がまだ
`triage --flaky` を使えていない直接の理由です。investigate-ci-failureはこの不在を埋めます。
`gh run download` で直近のrunをその場で履歴ディレクトリに再構成し、
[investigate-scenario-flakiness](investigate-scenario-flakiness.md) に渡します。

## 3. どう実現するか

### 配置

- `.apm/skills/investigate-ci-failure/SKILL.md`（手順の本体）
- `.apm/skills/investigate-ci-failure/references/known-ci-failure-patterns.md`。既知のインフラ由来
  flakyパターンの一覧である。種は [docs/ci.md](../ci.md) が本文中で個別に触れている症状から書き
  起こす。`Timed out while requesting screenshot` の再現、`CoreSimulator` の応答待ち、Androidレンダ
  ラのwedgeなどである。以後このスキルが新しいパターンを確認するたびに追記する。

### 入力

PR番号1つです。[`pr-followup`](../../.apm/skills/pr-followup/SKILL.md) のStep 2から渡されます。

### 手順

1. `gh pr checks <PR>` で失敗しているチェックを列挙する。それぞれについて
   `gh run view <run-id> --json jobs` でジョブ名を取り、`ci.yml` の `check` ジョブか、E2Eレーンの
   ジョブか、それ以外（`pr-title` / `roadmap-id` / `mcp-wire` など）かを判別する。
2. `check` ジョブの失敗は、`gh run view --log-failed` で失敗したstep名を読み、
   `known-ci-failure-patterns.md` の `gate-mechanical` 一覧（lockfileのずれ、フォーマット崩れ、skill
   デプロイのずれのそれぞれについて、直し方が1コマンドで書いてある）と照合する。一致すれば
   `gate-mechanical` としてその直し方ごと返す。一致しなければ `code-defect` として、ログの該当箇所を
   添えて返す。
3. E2Eレーンのジョブの失敗は、次の手順で判別する。まず `collect-ios-diagnostics` /
   `collect-android-diagnostics` / ホスト側サンプラーが `runs/diagnostics/` 配下に積んだartifactを、
   `gh run download <run-id> -n <artifact名>` で取得する。取得したartifactを
   `known-ci-failure-patterns.md` の `e2e-known-flake` 一覧と突き合わせる。
4. 手順3で判別できない場合は、履歴照合へ進む。「判別できない」とは、一致するパターンがない、または
   初めて見る症状の場合を指す。対象は、同じワークフロー、同じジョブ名の直近runとする。件数はデフォル
   トで20件とし、`main` へのpushとmerge queueのrunに限る。
   `gh run list --workflow <wf> -L 20 --json databaseId,conclusion,headBranch,createdAt` で直近run
   を列挙し、それぞれの対象artifactを `gh run download` で1つのディレクトリにまとめる。このディレク
   トリを [investigate-scenario-flakiness](investigate-scenario-flakiness.md) に渡す。`use_ai` は
   渡さない。分類に要るのは `classification` の値だけで、triageの原因仮説は要らないためである。
   返ってきたレポートで対象シナリオが `flaky` 判定なら `e2e-known-flake` とする。データに基づく
   新しいパターンとしての判定である。そうでなければ `e2e-unclassified` として返す。
5. 手順4で `e2e-known-flake` と判定した場合は、確認した症状を `known-ci-failure-patterns.md` に
   追記する。追記はファイルの変更にとどめ、その変更をpushするかどうかの判断はしない。判断は呼び出し元
   の[`pr-followup`](../../.apm/skills/pr-followup/SKILL.md) に委ねる。
6. 全チェックの分類結果を1本のレポートにまとめて返す。各項目はチェック名、分類、根拠、推奨アクション
   の4つで構成する。根拠は、ログの該当行、artifactのパス、または
   [investigate-scenario-flakiness](investigate-scenario-flakiness.md) が返したシナリオ名と
   `flip_rate` である。推奨アクションは、`gate-mechanical` なら直し方のコマンド、`code-defect` なら
   「通常の修正フローに進む」、`e2e-known-flake` なら「再実行のみで足りる可能性が高い」、
   `e2e-unclassified` なら「人間の判断が要る」である。

### `pr-followup` 側の変更

Step 2の冒頭でinvestigate-ci-failureを呼び、返ってきた分類に応じて次のように分岐させます。

- `gate-mechanical` は、レポートに書かれたコマンドをそのまま実行する。
- `code-defect` は、現行のStep 2の手順（ログを読み、直す）をそのまま続ける。
- `e2e-known-flake` は、`gh run rerun --failed <run-id>` で再実行を要求する。コードは変更しない。
- `e2e-unclassified` は、[`pr-followup`](../../.apm/skills/pr-followup/SKILL.md) の既存のエスカレー
  ション経路に載せ、人間の判断を仰ぐ。

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

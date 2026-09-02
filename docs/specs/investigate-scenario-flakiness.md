# シナリオの不安定性調査スキル

> ステータス: 実装完了
> 対象: `.apm/skills/investigate-scenario-flakiness/`
> 関連: [BE-0049](../../roadmaps/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)、
> [BE-0220](../../roadmaps/BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix.md)、
> [investigate-ci-failure](investigate-ci-failure.md)

`bajutsu flakiness` と `bajutsu triage --flaky` という既存の2つのコマンドを1つの手順に
束ねるエージェントスキルを追加します。呼び出し元には、「どのシナリオが本当に不安定か」と「その原因は
何か」をランキング付きの1本のレポートとして返します。

## 1. なにをつくるのか

`.apm/skills/investigate-scenario-flakiness/SKILL.md` を新規に作ります。このスキルは次の手順を実行
します。

1. 呼び出し元が渡した実行履歴（`--history` のrunディレクトリ、または `serve` データベース）に対して
   `bajutsu flakiness` を実行し、シナリオを不安定さでランク付けした結果を得る。
2. そのランキングのうち、`classification` が `flaky`（合否が同一フィンガープリントの下で入れ替わって
   いる）と判定されたシナリオだけを対象に選ぶ。`deterministic`（常に同じ結果）と `unproven`
   （観測run数が2未満）は対象から外す。
3. `use_ai` が明示的にtrueのときに限り、対象シナリオそれぞれについて `bajutsu triage --flaky --ai`
   を実行し、複数run間の差分から原因の仮説を得る。デフォルトではこの手順を実行しない。
4. 手順1から3の結果を、シナリオごとの不安定さの指標（`flip_rate`）と、手順3を実行した場合は原因の
   仮説を並べた1本のMarkdown形式のレポートにまとめて返す。

出力は読み取り専用のレポートに限ります。判定、修正、再実行はいずれもこのスキルの外側の責務とします。

### やらないこと

- **履歴データそのものを集める処理は持たない。** `--history` のディレクトリや `serve` データベースが
  すでに存在している前提で動く。CIの実行結果からその場で履歴を組み立てる処理は、呼び出し元
  ([investigate-ci-failure](investigate-ci-failure.md)) の責務とする。
- **修正の適用はしない。** `bajutsu triage` が持つ `--apply` / `--write` / `--rerun` はいずれも使わず、
  常に読み取り専用の診断だけを行う。シナリオを記述するYAMLファイルの書き換えや再実行は、レポートを
  受け取った人間か呼び出し元のスキルが判断する。
- **不安定さの判定ロジック自体には手を入れない。** `flip_rate` の計算ロジックは
  [`bajutsu/analysis/audit.py`](../../bajutsu/analysis/audit.py) にある。flaky、deterministic、
  unprovenの分類も同じファイルの `classify_stability` が行う。このスキルはその出力をそのまま使う。

## 2. なぜつくるのか

`bajutsu flakiness` と `bajutsu triage --flaky` は別々のCLIコマンドです。実装は
[`bajutsu/analysis/cli/flakiness.py`](../../bajutsu/analysis/cli/flakiness.py) と
[`bajutsu/triage/cli.py`](../../bajutsu/triage/cli.py) に分かれています。前者は
シナリオの不安定さをランク付けするだけで、原因の仮説までは出しません。原因を知るには、ランキングの
上位から対象のシナリオ名を控える必要があります。そのうえで、それぞれについて
`bajutsu triage --flaky --scenario <name> --history <dir>` を打ち直します。

この組み立ては、シナリオ数が増えるほど繰り返しの手数が増えます。上位を見落とす、打ち間違える、
どこまでを対象にするかの基準がその場その場でぶれるといった失敗が、繰り返すたびに積み上がります。
とりわけ [investigate-ci-failure](investigate-ci-failure.md) から呼ぶ場面では、組み立てをエージェント
が毎回その場で判断するのではなく、確立した1つの手順として再利用できることが重要になります。判断が
ぶれれば、同じ入力に対して毎回違う件数、違う対象のシナリオがtriageされ、レポートの再現性が失われます。

## 3. どう実現するか

### 配置

`.apm/skills/investigate-scenario-flakiness/SKILL.md` の1ファイルで完結させます。
[`record-issue`](../../.apm/skills/record-issue/SKILL.md) と同じく、手順が1本道で分岐が少ないため、
`references/` は持ちません。

### 入力

| 引数 | 内容 |
|---|---|
| `history` | 過去runのディレクトリ（`bajutsu flakiness --history` に渡す値）。省略時は `serve` データベースを読みます |
| `org` | `history` を省略したときに読む組織名（`bajutsu flakiness --org` にそのまま渡します） |
| `limit` | triage対象にする上限件数。デフォルトは5です |
| `use_ai` | trueのときだけ手順3のtriageを実行します。デフォルトはfalseで、ランキングだけを返します |

`bajutsu triage --flaky` にはルールベースの診断系がなく、`--ai` を必須とします。実装は
[`bajutsu/triage/cli.py`](../../bajutsu/triage/cli.py) の `_flaky_triage` です。
したがって手順3は常に `ANTHROPIC_API_KEY` を消費します。デフォルトで実行しないのはそのためで、
費用を伴う手順を呼び出し元が明示的に選ぶ形にします。

### 手順

1. `bajutsu flakiness --history <history> --json` を実行する。`history` を省略した
   ときは `--org <org> --json` を使う。得られた出力から `FlakinessReport` をパースする。
2. `scenarios` のうち `classification == "flaky"` のものだけを残す。この一覧はすでに `flip_rate`
   降順で並んでいる。並び順の根拠は
   [`bajutsu/serve/flakiness.py`](../../bajutsu/serve/flakiness.py) の `FlakinessReport`
   docstringにある。先頭から `limit` 件を対象に選ぶ。
3. `use_ai` がtrueのときだけ、対象それぞれについて
   `bajutsu triage --flaky --ai --scenario <name> --history <history>` を実行し、診断結果を得る。
   `--flaky` モードは位置引数の `run_dir` を読まないため渡さない。`use_ai` がfalseのときはこの手順を
   飛ばす。
4. 対象シナリオが0件のとき（flaky判定のシナリオがない）は、その旨と `deterministic` /
   `unproven` の件数を書いたレポートを返す。「不安定なシナリオがない」ことと「調べていない」ことを
   区別できるようにするためである。
5. 手順1から4の結果を、シナリオ名、`flip_rate`、観測run数、
   `representative_pass_run_id` / `representative_fail_run_id` を並べた1本のMarkdown形式の
   レポートにまとめて返す。手順3を実行した場合は、各シナリオにtriageの診断も併記する。実行して
   いない場合は、triageを飛ばした旨とその理由（`use_ai` が指定されていない）を明記する。

### 呼び出し元との受け渡し

戻り値は本文のMarkdownで完結させます。[`record-issue`](../../.apm/skills/record-issue/SKILL.md) が持つ
「pending draft」のような構造化フィールドは持ちません。
[investigate-ci-failure](investigate-ci-failure.md) からの呼び出しも、人間による直接起動も、同じ形式の
レポートをそのまま読めばよい設計にします。

## 4. 検討した代替案と、採らなかった理由

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| Bajutsu本体のCLI機能として実装する（`bajutsu triage --flaky-auto` のような新コマンド） | Pythonで手順を実装し、テストとdocstringを整備します | この手順は既存2コマンドの呼び出し順を決めているだけで、新しい決定的な検証ロジックを持ち込むわけではありません。読み取り専用の手順であればスキルの手順書1本で足り、プロダクトコードへの変更を避けられます |
| [investigate-ci-failure](investigate-ci-failure.md) に直接埋め込み、独立したスキルにはしない | 3コマンド連携のロジックをinvestigate-ci-failureのSKILL.mdに直接書きます | Bajutsuの開発者がこのリポジトリ以外のターゲットアプリのシナリオを調べる場面でも同じ手順が要りえます。investigate-ci-failure専用に埋め込むと、その場面で再利用できなくなります |
| `classification` で絞らず、履歴のある全シナリオをtriageにかける | flaky、deterministic、unprovenを区別せず対象にします | `deterministic` なシナリオをtriageしても「不安定ではない」という自明な結果しか返らず、`--ai` 使用時は特にコストに見合いません。`classify_stability` の判定を信頼し、対象を `flaky` だけに絞るほうが安価です |

## 5. 作業手順

| # | やること | 触るファイル | 完了条件 | 前提 |
|---|---|---|---|---|
| 1 [x] | `.apm/skills/investigate-scenario-flakiness/SKILL.md` を新規作成し、3章の手順を書きます | `.apm/skills/investigate-scenario-flakiness/SKILL.md` | `make lint-skills` が通ります | なし |
| 2 [x] | `make skills` を実行し、`.claude/skills/` 側へデプロイします | `.claude/skills/investigate-scenario-flakiness/SKILL.md`（生成）、`apm.lock.yaml` | `apm audit --ci` が通り、`git diff` がSKILL.mdとlockファイルだけになります | 1 |
| 3 [x] | 手元の複数run履歴（`fake` バックエンドで同じシナリオを複数回runして作れます）に対して手順1から5を人手でなぞり、`bajutsu flakiness --history` と `bajutsu triage --flaky --history` が想定通りの出力を返すことを確認します | なし | 手順に書いたコマンド列がエラーなく完走し、レポートの生成手順に矛盾がありません | 1 |
| 4 [x] | `make check` を実行します | なし | 全ステップがgreenになります | 1から3 |

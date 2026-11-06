[English](BE-0123-composite-action-input-indirection.md) · **日本語**

# BE-0123 — composite action の入力を env 経由の間接参照にする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0123](BE-0123-composite-action-input-indirection-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0123") |
| 実装 PR | [#675](https://github.com/bajutsu-e2e/bajutsu/pull/675) |
| トピック | セキュリティ強化 |
<!-- /BE-METADATA -->

## はじめに

このリポジトリの composite action のうち 2 つが、`${{ inputs.* }}` を `env:` 経由で渡さず、シェルの
`run:` ブロックへ直接展開しています。対象は
[`.github/actions/bajutsu-e2e/action.yml`](../../.github/actions/bajutsu-e2e/action.yml) と
[`.github/actions/boot-simulator/action.yml`](../../.github/actions/boot-simulator/action.yml)
です。

現在の呼び出し元はすべて、リテラル文字列か信頼できる先行ステップの出力だけを渡しています。そのため、
今の時点で悪用できる経路はありません。それでもこのパターンは脆く、入力に攻撃者の影響を受けた内容が
渡された日に、シェルインジェクションの経路へ変わります。本提案は composite action の入力を `env:`
経由の間接参照にします。このリポジトリの CI が、信頼できないフィールドにすでに行っている扱い方と
揃えるためです。

## 動機

`bajutsu-e2e/action.yml` は、2 つの `run:` ステップにまたがって 6 つの入力を直接展開しています。

```yaml
- name: Preflight (bajutsu doctor — non-blocking)
  shell: bash
  run: >-
    uv run --no-sync bajutsu doctor --target "${{ inputs.target }}" --udid "${{ inputs.udid }}"
    --backend "${{ inputs.backend }}" --config "${{ inputs.config }}"
    || echo "doctor: non-blocking (convention score only)"

- name: Run scenarios
  shell: bash
  run: >-
    uv run --no-sync bajutsu run --scenario "${{ inputs.scenarios }}" --target "${{ inputs.target }}"
    --udid "${{ inputs.udid }}" --backend "${{ inputs.backend }}"
    --config "${{ inputs.config }}" --no-erase
```

`boot-simulator/action.yml` も、1 つの入力について同じことをしています。

```yaml
if [ "${{ inputs.wait }}" = "true" ]; then
```

GitHub Actions は、`run:` ブロック内の `${{ ... }}` をテキストとして置き換えます。この展開は、シェルが
その行を解釈するより前に行われます。そのため、シェルのメタ文字（`` ` ``、`$(...)`、`;`、`"` など）を
含む入力値は、不活性な引数としては渡されません。スクリプトの一部として展開され、`bash` に解釈されます。
これは GitHub Actions のスクリプトインジェクションとしてよく知られたパターンです。GitHub のセキュリティ
チーム自身が、`pull_request_target` ワークフローや信頼できない `github.event.*` フィールドについて、
同じ種類の問題を書いています。

現時点では**悪用できません**。`.github/workflows/e2e.yml` と `.github/workflows/idb-monitor.yml` の
すべての呼び出し元が渡すのは、次のどちらかです。

- ハードコードされたリテラル。`scenarios: demos/showcase/scenarios/smoke.yaml`、
  `target: showcase-swiftui`、`wait: "false"` などです。
- `udid: ${{ steps.sim.outputs.udid }}`。`boot-simulator` 自身が `xcrun simctl` の呼び出しから生成した
  UDID の文字列であり、ユーザーや PR に由来する入力ではありません。

PR タイトルやブランチ名、issue の本文といった、攻撃者の影響を受けうるテキストをこれらの入力へ転送する
呼び出し元は、現状ひとつもありません。

この指摘は、実際に悪用可能な脆弱性としては**未確認**です。実証された悪用ではなく潜在的なパターンで
あり、現状のすべての呼び出し元は安全です。それでも取り上げるのは、composite action が再利用を前提に
設計されているからです。将来のワークフロー、または既存のワークフローへの将来の変更が、より信頼度の
低い値を `target`、`scenarios`、`artifact-name`、`wait` へ渡す可能性があります。値の例は、ブランチ名、
PR のラベル、イベントデータ由来の matrix の値です。その `run:` ブロックがそうした値に対して一度も
強化されていなかったことには、誰も気付けません。

## 詳細設計

修正は機械的で、2 つのアクションファイルに閉じています。プロダクトコードにも、`run`/CI の合否判定
ロジックにも変更はありません。

1. **`bajutsu-e2e/action.yml`: 2 つの `run:` ステップの入力を `env:` 経由にする。** 「Preflight」と
   「Run scenarios」のそれぞれに、各入力を大文字の環境変数へ対応付ける `env:` ブロックを追加します
   （例：`TARGET: ${{ inputs.target }}`、`UDID: ${{ inputs.udid }}`、`BACKEND: ${{ inputs.backend }}`、
   `CONFIG: ${{ inputs.config }}`、`SCENARIOS: ${{ inputs.scenarios }}`）。シェルコマンド内では、
   `${{ inputs.* }}` の直接展開をやめて `"$TARGET"`、`"$UDID"` などを参照します。`env:` による代入も
   元の値をそのまま代入しますが、値はシェルの*変数*として渡ります。シェルはその値をスクリプトの一部
   ではなくデータとして解釈し、スクリプトのテキストには埋め込みません。これがこの種の指摘に対する
   標準的な対処法です。
2. **`bajutsu-e2e/action.yml`: 「Upload run artifacts」ステップの `${{ inputs.artifact-name }}` も
   同様に扱う。** この入力は `run:` ブロックではなく、`uses:` ステップ（`actions/upload-artifact`）の
   `with:` の値です。シェルインジェクションの対象にならないため、コードの変更は不要です。ただし、
   将来の変更で同じリスクがあると誤って想定されないよう、アクション内に一言メモを残す価値はあります。
3. **`boot-simulator/action.yml`: `${{ inputs.wait }}` を `env:` 経由にする。** 「Boot」ステップに
   `env: WAIT: ${{ inputs.wait }}` を追加し、`if [ "${{ inputs.wait }}" = "true" ]` を
   `if [ "$WAIT" = "true" ]` に変更します。
4. **リグレッションチェックを追加する。** `make lint-actions`（`actionlint`）の対象を広げるか、小さな
   リポジトリ固有のチェック（`scripts/` 配下の lint や `grep` ベースのテストなど）を追加します。
   このチェックは、composite action の `run:` ブロックに `${{ inputs.` の直接展開があれば失敗します。
   これにより、将来のアクションや変更でパターンが気付かれずに再発することを防ぎます。

## 検討した代替案

- **現在の呼び出し元がすべてリテラルか信頼できる出力であることを理由に、入力をそのままにしておく。**
  却下します。本提案の動機は、「今は信頼できない値が渡っていないから安全」という状態が、再利用される
  composite action にとって永続的な保証にならない点にあります。修正は安価です。将来のすべての呼び出し
  元がリテラルしか渡さないことを期待し続けるより、危険そのものをなくすほうが確実です。
- **`env:` による間接参照の代わりに、composite action の入力を正規表現や `pattern` による
  バリデーションで制限する。** GitHub Actions には入力バリデーションのネイティブな構文がありません。
  実施するには、リスクのあるステップの前にシェルベースの検証ステップを足すことになり、`env:` による
  修正よりコード量が増えます。しかも、許容範囲を絞るだけで完全ではないパターンが残るため、リスクを
  減らすにとどまります。`env:` による間接参照は、インジェクションの経路そのものを取り除きます。
- **該当ステップを、シェルによる文字列展開を一切使わない形（ラッパースクリプトの argv 経由で引数を
  渡すなど）に書き直す。** 必要以上に大がかりです。`env:` による間接参照は、まさにこのパターンに
  対する標準的で最小限の修正であり、差分も小さくレビューしやすい状態を保てます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `bajutsu-e2e/action.yml` の「Preflight」「Run scenarios」ステップの入力を `env:` 経由の間接参照にする
- [x] `bajutsu-e2e/action.yml` の `with: name: ${{ inputs.artifact-name }}` アップロードステップについて、メモを残す（あるいは変更不要であることを確認する）
- [x] `boot-simulator/action.yml` の `${{ inputs.wait }}` を `env:` 経由の間接参照にする
- [x] composite action の `run:` ブロックに `${{ inputs. }}` の直接展開が残っていたら失敗するリグレッションチェックを追加する

- [#675](https://github.com/bajutsu-e2e/bajutsu/pull/675): 2 つの composite action の `run:` ブロックの入力を `env:` 経由の間接参照にしました
  （`bajutsu-e2e` の `TARGET`・`UDID`・`BACKEND`・`CONFIG`・`SCENARIOS`、`boot-simulator` の
  `WAIT`）。インジェクションの対象外である `with:` アップロードステップにはメモを残し、composite
  action の `run:` ブロックに `${{ inputs. }}` の直接展開が再発したら失敗する
  `tests/test_action_input_indirection.py` をリグレッションネットとして追加しました。

## 参考

- `.github/actions/bajutsu-e2e/action.yml`：2 つの `run:` ステップで `target`、`udid`、`backend`、
  `config`、`scenarios` を直接展開している
- `.github/actions/boot-simulator/action.yml`：`run:` ステップで `wait` を直接展開している
- `.github/workflows/e2e.yml`、`.github/workflows/idb-monitor.yml`：現在の呼び出し元。いずれも
  リテラルか `steps.sim.outputs.udid` しか渡していない
- GitHub Security Lab, "Keeping your GitHub Actions and workflows secure: Preventing pwn
  requests"：本提案が対処する `${{ ... }}` スクリプトインジェクションパターンの背景資料
- 関連: BE-0069（実行可能な contributor ガードレール）
- 2026-07-02 のコードベース分析レポート（セキュリティ）に由来します。

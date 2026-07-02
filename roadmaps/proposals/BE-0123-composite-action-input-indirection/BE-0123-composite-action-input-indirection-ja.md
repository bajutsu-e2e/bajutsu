[English](BE-0123-composite-action-input-indirection.md) · **日本語**

# BE-0123 — composite action の入力を env 経由の間接参照にする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0123](BE-0123-composite-action-input-indirection-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トピック | セキュリティ強化 |
<!-- /BE-METADATA -->

## はじめに

このリポジトリの composite action のうち 2 つ、
[`.github/actions/bajutsu-e2e/action.yml`](../../../.github/actions/bajutsu-e2e/action.yml) と
[`.github/actions/boot-simulator/action.yml`](../../../.github/actions/boot-simulator/action.yml)
は、`${{ inputs.* }}` を `env:` 経由で受け渡す前に、シェルの `run:` ブロックへ直接展開していま
す。現在のすべての呼び出し元はリテラル文字列か、信頼できる先行ステップの出力しか渡していないため、
今の時点で悪用できる経路はありません。ただしこのパターン自体は脆く、将来ある入力に攻撃者の影響を
受けた内容が渡された日に、そのままシェルインジェクションの経路へと変わる形をしています。本提案
は、composite action の入力を `env:` 経由の間接参照にすることで、このリポジトリの CI がすでに
信頼できないフィールドに対して行っている扱い方に合わせます。

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

GitHub Actions が `run:` ブロック内の `${{ ... }}` を展開する処理は、シェルがその行を解釈するより
前に、テキストとして置き換える形で行われます。そのため、シェルのメタ文字（`` ` ``、`$(...)`、
`;`、`"` など）を含む入力値が渡されると、それは不活性な引数としてではなく、スクリプトの一部として
展開されたうえで `bash` に解釈されます。これは GitHub Actions のスクリプトインジェクションとして
よく知られたパターンであり、GitHub のセキュリティチーム自身が `pull_request_target` ワークフロー
や信頼できない `github.event.*` フィールドについて書いているものと同じ種類の問題です。

現時点では**悪用できません**。`.github/workflows/e2e.yml` と `.github/workflows/idb-monitor.yml`
にあるすべての呼び出し元は、ハードコードされたリテラル（`scenarios:
demos/showcase/scenarios/smoke.yaml`、`target: showcase-swiftui`、`wait: "false"` など）か、
`udid: ${{ steps.sim.outputs.udid }}` のいずれかしか渡していません。後者は `boot-simulator` 自身
が `xcrun simctl` の呼び出しから生成した UDID の文字列であり、ユーザーや PR に由来する入力ではあり
ません。PR タイトルやブランチ名、issue の本文といった攻撃者の影響を受けうるテキストをこれらの入力
へ転送している呼び出し元は、現状ひとつもありません。

深刻度については、この指摘は実際に悪用可能な脆弱性としては**未確認**です。潜在的なパターンであっ
て実証された悪用ではなく、現状のすべての呼び出し元は安全です。それでも取り上げるのは、composite
action が再利用されることを前提に設計されているためです。将来のワークフロー（あるいは既存のワーク
フローへの将来の変更）が、ブランチ名や PR のラベル、イベントデータ由来の matrix の値といった、より
信頼度の低い値を `target`、`scenarios`、`artifact-name`、`wait` へ渡してしまう可能性があり、その
ときになって初めて、この `run:` ブロックがそうした値に対して一度も強化されていなかったことに気付
くことになりかねません。

## 詳細設計

修正は機械的で、2 つのアクションファイルに閉じています。プロダクトコードや `run`/CI の合否判定
ロジックへの変更はありません。

1. **`bajutsu-e2e/action.yml`: 2 つの `run:` ステップの入力を `env:` 経由にする。** 「Preflight」
   と「Run scenarios」のそれぞれに、各入力を大文字の環境変数へ対応付ける `env:` ブロックを追加し
   ます（例：`TARGET: ${{ inputs.target }}`、`UDID: ${{ inputs.udid }}`、
   `BACKEND: ${{ inputs.backend }}`、`CONFIG: ${{ inputs.config }}`、
   `SCENARIOS: ${{ inputs.scenarios }}`）。そのうえで、シェルコマンド内では `${{ inputs.* }}` を
   直接展開する代わりに `"$TARGET"`、`"$UDID"` などを参照します。`env:` による代入自体は元の値を
   そのまま代入しますが、それをシェルの*変数*として渡すことになるため、シェルはその値をスクリプト
   の一部としてではなくデータとして解釈します。これがこの種の指摘に対する標準的な対処法です。
2. **`bajutsu-e2e/action.yml`: 「Upload run artifacts」ステップの `${{ inputs.artifact-name }}`
   も同様に扱う。** このステップは `run:` ブロックではなく、`uses:` ステップ
   （`actions/upload-artifact`）の `with:` の値であるため、シェルインジェクションの対象にはなり
   ません。コードの変更は不要ですが、将来の変更で同じリスクがあると誤って想定されないよう、アク
   ション内に一言メモを残す価値はあります。
3. **`boot-simulator/action.yml`: `${{ inputs.wait }}` を `env:` 経由にする。** 「Boot」ステップに
   `env: WAIT: ${{ inputs.wait }}` を追加し、`if [ "${{ inputs.wait }}" = "true" ]` を
   `if [ "$WAIT" = "true" ]` に変更します。
4. **リグレッションチェックを追加する。** `make lint-actions`（`actionlint`）の対象を広げるか、
   小さなリポジトリ固有のチェック（`scripts/` 配下の lint や `grep` ベースのテストなど）を追加し、
   composite action の `run:` ブロックに `${{ inputs.` の直接展開が含まれていたら失敗するように
   します。これにより、このパターンが将来のアクションや変更で気付かれずに再発することを防ぎます。

## 検討した代替案

- **現在の呼び出し元がすべてリテラルか信頼できる出力であることを理由に、入力をそのままにしておく。**
  却下します。本提案の動機そのものが、「今の時点では信頼できない値が渡っていないから安全」という
  状態は、再利用される composite action にとって永続的な保証にはならないという点にあります。修正
  自体は安価であり、将来のすべての呼び出し元がリテラルしか渡さないことを期待し続けるより、この
  危険をそもそもなくしてしまうほうが確実です。
- **`env:` による間接参照の代わりに、composite action の入力を正規表現や `pattern` によるバリデー
  ションで制限する。** GitHub Actions には入力バリデーションのネイティブな構文がないため、これを
  行うにはリスクのあるステップの前にシェルベースの検証ステップを追加することになり、`env:` による
  間接参照よりコード量が増えます。しかもそれはリスクを減らす（許容範囲を絞るが完全ではないパター
  ンになる）だけであり、`env:` による間接参照のようにインジェクションの経路そのものを取り除くわけ
  ではありません。
- **該当ステップを、シェルによる文字列展開を一切使わない形（ラッパースクリプトの argv 経由で引数を
  渡すなど）に書き直す。** 必要以上に大がかりです。`env:` による間接参照は、まさにこのパターンに
  対する標準的で最小限の修正であり、差分も小さくレビューしやすい状態を保てます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `bajutsu-e2e/action.yml` の「Preflight」「Run scenarios」ステップの入力を `env:` 経由の間接参照にする
- [ ] `bajutsu-e2e/action.yml` の `with: name: ${{ inputs.artifact-name }}` アップロードステップについて、メモを残す（あるいは変更不要であることを確認する）
- [ ] `boot-simulator/action.yml` の `${{ inputs.wait }}` を `env:` 経由の間接参照にする
- [ ] composite action の `run:` ブロックに `${{ inputs. }}` の直接展開が残っていたら失敗するリグレッションチェックを追加する

まだ着手した PR はありません。

## 参考

- `.github/actions/bajutsu-e2e/action.yml` — 2 つの `run:` ステップで `target`、`udid`、
  `backend`、`config`、`scenarios` を直接展開している
- `.github/actions/boot-simulator/action.yml` — `run:` ステップで `wait` を直接展開している
- `.github/workflows/e2e.yml`、`.github/workflows/idb-monitor.yml` — 現在の呼び出し元。いずれも
  リテラルか `steps.sim.outputs.udid` しか渡していない
- GitHub Security Lab, "Keeping your GitHub Actions and workflows secure: Preventing pwn
  requests" — 本提案が対処する `${{ ... }}` スクリプトインジェクションパターンの背景資料
- 関連: BE-0069（実行可能な contributor ガードレール）
- 2026-07-02 のコードベース分析レポート（セキュリティ）に由来します。

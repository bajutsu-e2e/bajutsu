[English](BE-0125-authoring-agent-tool-restriction.md) · **日本語**

# BE-0125 — claude-code オーサリングエージェントのツールを制限する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0125](BE-0125-authoring-agent-tool-restriction-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0125") |
| 実装 PR | [#620](https://github.com/bajutsu-e2e/bajutsu/pull/620) |
| トピック | セキュリティ強化 |
| 関連 | [BE-0176](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend-ja.md) |
| 無効化 | [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`ClaudeCodeAgent`（`bajutsu/claude_code_agent.py`）は、ローカルの `claude` CLI を使うオーサリング
エージェントです。この CLI を明示的なツール制限なしに print モードで起動しています。CLI が不要な
ツールを呼ばないようにする歯止めは、システムプロンプトの一文だけです。

## 動機

`ClaudeCodeAgent._command`（`bajutsu/claude_code_agent.py:148`）は、CLI の起動コマンドを次のように
組み立てます。

```python
def _command(self, prompt: str, schema: dict[str, Any], system: str) -> list[str]:
    cmd = [
        self._binary,
        "-p",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(schema),
        "--append-system-prompt",
        system,
    ]
```

このコマンドは `--allowedTools`、`--disallowedTools`、`--permission-mode` のいずれも渡しません。
そのため CLI は、デフォルトのツール範囲のまま動きます。範囲には、シェル、ファイルの読み書き、実行環境
が許可するその他のツールが含まれます。CLI をツール呼び出しから遠ざけるのは、システムプロンプトに追記
した文章だけです。この歯止めはソフトであり、強制力を持ちません。

この歯止めが問題になるのは、エージェントがテスト対象アプリの**観測結果**（プロンプト中の
`_render(observation)`）から次のテストアクションを決めるからです。観測結果には、エージェント自身が
生成していない画面上のテキストやアクセシビリティラベルが含まれます。そのため、攻撃者が細工した、
あるいは改ざんされた画面（指示のように見えるラベルやフィールド値）は、プロンプトインジェクションの
経路になり得ます。観測結果に含まれるテキストが、期待される構造化アクションを返す代わりに、ツールの
呼び出し（シェルコマンドの実行、任意ファイルの読み書き）をモデルに促そうとする可能性があります。

深刻度は中程度です。悪用には、テスト対象アプリが攻撃者の制御下にあるコンテンツを、アクセシビリティ
ツリーやスクリーンショットに現れる形で描画する必要があります。しかし、それはまさに E2E オーサリング
が扱う想定の状況です。実環境やステージング環境へのログイン、Web ビュー内のサードパーティコンテンツ
などが該当します。

## 詳細設計

この呼び出し箇所だけ、CLI のツール範囲を絞ります。エージェントのプロトコル、JSON スキーマによる
アクションの契約、決定的な run/CI パスには手を加えません。

- `_command` に、明示的な `--disallowedTools` を渡します。インストールされている CLI のバージョンが
  より厳密にサポートするなら、同等の `--allowedTools` allowlist でも構いません。対象はシェル実行と
  ファイルの読み書きです。このエージェントの契約は「構造化されたアクションを 1 つ返す」ことであり、
  それらのツールを正当に必要とする場面はありません。
- 加えて、`--permission-mode` を非対話・デフォルト拒否のモードに設定します。フラグの一覧が想定して
  いないツール呼び出しが発生しても、黙って通ってしまわないようにするためです。print モードはもともと
  人間に確認を求められないため、未処理の権限要求は通すのではなく、失敗させるべきです。
- 変更は `ClaudeCodeAgent` に閉じます。API を使う `bajutsu/claude_agent.py` の `ClaudeAgent` は CLI を
  呼び出さないため、影響を受けません。`plan()` の CLI 呼び出しも同じ `_command` ヘルパーを経由する
  ため、追加の作業なしにカバーされます。

## 検討した代替案

- **システムプロンプトの指示だけに頼る（現状維持）。** 却下しました。システムプロンプトはモデルへの
  ガイダンスであり、観測結果に十分敵対的な入力があれば、モデルが従わなくなり得ます。プロンプト
  インジェクションの経路を塞ぐには、ガイダンスではなく強制力のある境界が必要です。
- **ツール制限の代わりに `claude` プロセス全体をサンドボックス化する（コンテナ・VM）。** このコード
  ベースには過剰だと判断し、却下しました。サンドボックスは、今は CLI バイナリだけで済んでいる経路に、
  コンテナランタイムという運用上の依存を加えます。対処したいリスクは、CLI 自身のフラグで直接塞げます。
- **Claude Code エージェントの経路自体をやめ、API エージェントだけを残す。** 却下しました。
  `ClaudeCodeAgent` の存在意義は、API のクレジットではなく Claude Pro/Max のサブスクリプションで
  オーサリングを回せることにあります（モジュールの docstring を参照）。経路を削除すると、その選択肢
  自体がなくなり、抜け穴の修正にはなりません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `ClaudeCodeAgent._command` にシェルとファイル読み書きを対象とする
      `--disallowedTools`／`--allowedTools` を追加する。
- [x] デフォルト拒否・非対話の `--permission-mode` を追加する。
- [x] 構築されたコマンドに常にこれらの制限フラグが含まれることを検証するテストを追加する。

- [#620](https://github.com/bajutsu-e2e/bajutsu/pull/620) — `ClaudeCodeAgent._command` に `--disallowedTools`
  （`Bash,Read,Write,Edit,NotebookEdit,Glob,Grep`）と `--permission-mode default` を追加しました。
  `next_action` と `plan` はどちらも共通ヘルパー経由のため同時にカバーされ、これらのフラグが常に
  含まれることを検証するテストを添えています。

## 参考

- `bajutsu/claude_code_agent.py:148`（`ClaudeCodeAgent._command`）
- 関連: [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md)
  （AI データ主権）
- 2026-07-02 のコードベース分析レポート（セキュリティ）に由来します。

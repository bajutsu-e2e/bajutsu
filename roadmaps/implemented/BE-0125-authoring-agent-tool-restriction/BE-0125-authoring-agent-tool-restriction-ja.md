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
<!-- /BE-METADATA -->

## はじめに

ローカルの `claude` CLI を使うオーサリングエージェント `ClaudeCodeAgent`
（`bajutsu/claude_code_agent.py`）は、この CLI を明示的なツール制限なしに print
モードで起動しており、不要なツールを呼ばせないための歯止めはシステムプロンプトの一文だけです。

## 動機

`ClaudeCodeAgent._command`（`bajutsu/claude_code_agent.py:148`）は CLI の起動コマンドを次のように組み立てます。

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

`--allowedTools` も `--disallowedTools` も `--permission-mode` も渡していないため、CLI はデフォルトのツール範囲（シェル、ファイルの読み書き、実行環境が許可するその他のツール）のまま動き、ツール呼び出しから遠ざける役割はシステムプロンプトに追記した一文だけが担っています。これはあくまでソフトな歯止めであり、強制力を持ちません。この点が問題になるのは、このエージェントの仕事がテスト対象アプリの**観測結果**（プロンプト中の `_render(observation)`）から次のテストアクションを決めることであり、その観測結果にはエージェント自身が生成していない画面上のテキストやアクセシビリティラベルが含まれるからです。攻撃者が細工した、あるいは何らかの理由で改ざんされた画面（指示のように見えるラベルやフィールド値）はプロンプトインジェクションの経路になり得ます。観測結果に含まれるテキストが、期待される構造化アクションを返す代わりにツールの呼び出し（シェルコマンドの実行、任意ファイルの読み書き）をモデルに促そうとする可能性があります。深刻度は中程度です。悪用にはテスト対象アプリが攻撃者の制御下にあるコンテンツをアクセシビリティツリーやスクリーンショットに描画する必要がありますが、それはまさに E2E オーサリングが扱う想定の状況（実環境やステージング環境へのログイン、Web ビュー内のサードパーティコンテンツなど）です。

## 詳細設計

この呼び出し箇所だけ CLI のツール範囲を絞ります。エージェントのプロトコルや JSON スキーマによるアクションの契約、決定的な run/CI パスには一切手を加えません。

- `_command` に明示的な `--disallowedTools`（あるいは、インストールされている CLI のバージョンがより厳密にサポートするなら同等の `--allowedTools` allowlist）を渡し、シェル実行とファイルの読み書きを対象にします。このエージェントの契約は「構造化されたアクションを 1 つ返す」ことであり、それらのツールを正当に必要とする場面はありません。
- 加えて、`--permission-mode` を非対話・デフォルト拒否のモードに設定します。フラグの一覧が想定していないツール呼び出しが発生しても、黙って通ってしまわないようにするためです（print モードはもともと人間に確認を求められないため、未処理の権限要求は通すのではなく失敗させるべきです）。
- 変更は `ClaudeCodeAgent` に閉じます。API を使う `bajutsu/claude_agent.py` の `ClaudeAgent` は CLI を呼び出さないため影響を受けません。`plan()` の CLI 呼び出しも同じ `_command` ヘルパーを経由するため、この修正で自動的にカバーされます。

## 検討した代替案

- **システムプロンプトの指示だけに頼る（現状維持）。** 却下しました。システムプロンプトはあくまでモデルへのガイダンスであり、観測結果に十分敵対的な入力があれば従わせなくなり得ます。プロンプトインジェクションの経路を塞ぐために必要なのは、そうしたガイダンスではなく強制力のある境界です。
- **ツール制限の代わりに `claude` プロセス全体をサンドボックス化する（コンテナ・VM）。** このコードベースには過剰だと判断し却下しました。今は CLI バイナリだけで済んでいる経路にコンテナランタイムという運用上の依存を追加することになり、しかも対処したいリスクは CLI 自身のフラグで直接ふさげます。
- **Claude Code エージェントの経路自体をやめ、API エージェントだけを残す。** 却下しました。`ClaudeCodeAgent` の存在意義は、API のクレジットではなく Claude Pro/Max のサブスクリプションでオーサリングを回せることにあります（モジュールの docstring を参照）。経路を削除すればその選択肢自体がなくなってしまい、抜け穴の修正にはなりません。

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
- 関連: [BE-0047](../../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md)
  （AI データ主権）
- 2026-07-02 のコードベース分析レポート（セキュリティ）に由来します。

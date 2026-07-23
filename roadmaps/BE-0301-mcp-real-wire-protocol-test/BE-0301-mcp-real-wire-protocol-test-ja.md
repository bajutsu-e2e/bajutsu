[English](BE-0301-mcp-real-wire-protocol-test.md) · **日本語**

# BE-0301 — MCP サーバの実ワイヤプロトコル round-trip テスト

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0301](BE-0301-mcp-real-wire-protocol-test-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0301") |
| トピック | 統合と自動化 |
| 関連 | [BE-0017](../BE-0017-mcp-server/BE-0017-mcp-server-ja.md), [BE-0018](../BE-0018-evidence-as-mcp-resources/BE-0018-evidence-as-mcp-resources-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`bajutsu mcp` は `bajutsu_run`/`bajutsu_doctor` を MCP のツールとして、run の証跡をリソースとして
公開し、Claude Desktop / Code との連携に使われます。`tests/test_mcp.py` のほとんどのテストは、
in-process の `FastMCP` インスタンスに対して `mcp.call_tool(...)` / `mcp.read_resource(...)` を
直接呼び出しています。`test_cli_mcp_starts_server` は `create_server` を monkeypatch しているため、
`.run()` は実際のサーバを一度も起動せず、どの transport 文字列が要求されたかを記録するだけです。
このテストスイートには、ツール呼び出しを stdio や SSE 越しにシリアライズし、それを逆シリアライズ
して受け取るものが1つもありません。本項目は、実際のワイヤプロトコルによる round-trip テストを
追加します。

## 動機

in-process で `mcp.call_tool(...)` を呼び出すことは、FastMCP の Python レベルのディスパッチを
検証します。ツール関数は実際に実行され、その返り値はテストが見ているものそのものです。しかし、
実際のクライアントとサーバが実際の transport 越しに話すときにだけ発生する、JSON-RPC のフレーミング、
ツールスキーマの広告、リソース URI のエンコーディングは検証していません。Claude Desktop / Code が
パースできないスキーマ、実際のシリアライゼーションを経由すると round-trip しないリソース URI、
stdio のフレーミングのバグ、これらはいずれも現行のテストスイートを素通りします。このスイートは
何もワイヤに乗せることがないからです。

## 詳細設計

提案の粒度です。作業は以下の単位に沿って MECE に分かれます。

- **実際のサーバプロセスを起動する**：`create_server` を monkeypatch するのではなく、`bajutsu mcp`
  を実際のサブプロセスとして（あるいは実際の transport に束縛した in-process サーバとして）起動
  します。クライアントは固定の sleep ではなく、サーバの準備完了シグナル（最初のリクエストが
  応答するまでの接続待ち）を条件待機してから話しかけます（プライムディレクティブ 2）。
- **実際の MCP クライアントで接続する**：`mcp` SDK のクライアントを使い、実際の transport
  （まずはネットワークを必要としない stdio）越しにツール一覧を取得し、`bajutsu_run`/
  `bajutsu_doctor` を呼び出し、リソースを読み取ります。この round-trip が、in-process のテストが
  ツール自体のロジックに対してすでに検証している結果と同じものを生むことを確認します。
- **in-process のテストはそのまま残す**：ツール関数自体のロジックを検証する手段として
  引き続き適切だからです。本項目はそれを置き換えるのではなく、その下にあるワイヤレベルの層を追加します。
- **まずゲート対象外とする**：実際のサブプロセスを起動し stdio 越しに IPC を行うことは、
  準備完了の条件待機を使っていても、プロセス起動のレイテンシや CI のリソース競合下でのパイプ
  バッファリングなど、現行の in-process 呼び出しより時間に敏感な面が増えます。
  [BE-0282](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
  の前例に従い、まず CI のシグナルとして着地させ、安定を確認してから必須化します。

## 検討した代替案

- **FastMCP がよくテストされたライブラリであることを根拠に、in-process のテストを信頼する**：
  FastMCP 自身のテストは FastMCP 自体をカバーするものであり、Bajutsu 固有のツールスキーマや
  リソース URI が実際のシリアライゼーションを経ても生き残るかどうかについては何も語りません。
  それこそが実際のクライアントによる round-trip の目的です。
- **Claude Desktop に対して MCP 連携を一度手動で確認し、それで完了とする**：一度きりの手動確認は
  現時点の状態は捉えますが、将来の回帰は捉えません。CI で実行される round-trip テストだけが、
  それを観測し続ける唯一の形です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 実際の `bajutsu mcp` サーバプロセス（または実際の transport 上の in-process サーバ）を、
  準備完了の条件待機つきで起動する。
- [ ] 実際の `mcp` SDK クライアントで接続し、ツール呼び出しとリソース読み取りを round-trip する。
- [ ] in-process のテストはそのまま残す。
- [ ] ゲート対象外のシグナルとして CI に組み込み、安定後に必須化する。

## 参考

- [BE-0282 — ネットワークのキャプチャ・モック・アサーションを CI で実バックエンド検証する](../BE-0282-real-backend-network-coverage/BE-0282-real-backend-network-coverage-ja.md)
- `bajutsu/mcp/tools.py`、`bajutsu/mcp/resources.py`、`tests/test_mcp.py`
  (`test_cli_mcp_starts_server`)

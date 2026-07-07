[English](BE-0176-claude-code-ai-backend.md) · **日本語**

# BE-0176 — Claude Code を AiBackend アダプタとして復活させる（ファイル経由の vision 付き）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0176](BE-0176-claude-code-ai-backend-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0176") |
| 実装 PR | [#713](https://github.com/bajutsu-e2e/bajutsu/pull/713), [#745](https://github.com/bajutsu-e2e/bajutsu/pull/745) |
| トピック | AI provider configuration |
| 関連 | [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md), [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider-ja.md), [BE-0125](../BE-0125-authoring-agent-tool-restriction/BE-0125-authoring-agent-tool-restriction-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Claude Code のバックエンドをもう一度用意します。ただし今回は、[BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md)
が導入したベンダー中立な `AiBackend` シームの背後に単一のアダプタとして置きます。こうすると、
[BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider-ja.md) が削除した
「text-only で `record` 専用」の迂回路とは違い、すべての AI 経路（`record`、`crawl`、`enrich`、
`triage --ai`、`run --dismiss-alerts`、SwiftUI のタブ locator）を vision 込みで賄えます。vision は、
スクリーンショットをスクラッチファイルに書き出してそのパスを CLI に伝え、Claude Code が `Read` ツールで
ディスクから画像を読む方式で実現します。これが前回の試みに欠けていた仕組みです。バックエンドは
`claude-code` というプロバイダ名で登録し、課金は従量制の API クレジットではなく、利用者の Claude
Pro / Max / Console サブスクリプションから引きます。

## 動機

BE-0163 以前は、`ClaudeCodeAgent` によって、`ANTHROPIC_API_KEY` ではなく自分の Claude サブスクリプション
枠でシナリオをオーサリングできました。これが削除されたのには具体的な理由が 2 つあり、本項目が構造的に
解決するのは 2 つ目のほうです。

- **text-only だった。** 旧エージェントはアクセシビリティの要素リストだけから推論し、スクリーンショットを
  一度も送りませんでした。そのため vision に依存する経路（`ClaudeAlertLocator`、`ClaudeTabLocator`、および
  スクリーンショットを伴う `record` / `crawl` / `enrich` / `triage` のターン）はこのバックエンドをまったく
  使えませんでした。
- **層が誤っていた。** 旧統合は高レベルの `Agent` プロトコル（`next_action` / `plan`）を直接実装しており、
  そのために `record` しか賄えず、`crawl` 用に並行の `ClaudeCodeActionProposer` を要し、`triage` / `enrich` /
  vision locator には届きませんでした。AI 経路が増えるたびに、その経路専用の Claude Code 版が必要になる構造
  でした。

BE-0163 はこれを `ant` プロバイダ（公式 Anthropic CLI の OAuth 資格情報を *SDK* アダプタに供給するもの）で
置き換えました。これは確かに、全経路で vision 付きのサブスクリプション課金を実現します。課金という目標は
これで達成できました。ただし `ant` と `claude-code` は同じ提供物ではありません。`ant` は Anthropic API を
通じて Claude Console のシートに課金するのに対し、本来の Claude Code バックエンドは、多くの貢献者がすでに
持っている Claude Code のサブスクリプション（Pro / Max）に課金し、彼らがすでに認証済みの `claude` CLI を
再利用します。復活させる価値があり、しかも今なら安価に済むのは、BE-0104 が適切なシームを用意したからです。
すなわち、単一の `create_message` ターン（システムプロンプト、テキストと画像の user メッセージ、そして
強制されたツール呼び出しからツール利用ブロックへ）です。この層のアダプタを 1 つ置けば、vision を含む
6 経路すべてに自動的に共有されます。実質的に唯一の隙間は vision の伝送であり、それをファイルパス方式が
塞ぎます。

## 詳細設計

この機能の全体は、新しいアダプタ 1 つとその登録だけで、AI の呼び出し側はどこも変えません（それこそが
BE-0104 のシームの狙いです）。

### 1. `ClaudeCodeBackend` アダプタ（`bajutsu/ai/claude_code.py`）

`AiBackend.create_message(MessageRequest) -> MessageResponse` を、`claude -p --output-format json`
（print モード）へのサブプロセス起動で実装します。削除された `claude_code_agent.py` と現在の `ant`
プロバイダがともに使う、注入可能な `Runner` / `_default_runner` のパターン（サブプロセス起点は 1 箇所、
テスト用に `Runner` のシーム）を踏襲します。

- **強制ツール呼び出しから structured output へ。** BE-0104 の `tool_choice` は `request.tools` に対する
  `AnyTool` か `NamedTool` です。これを CLI の `--json-schema` に対応づけます。
  - `NamedTool`、または単一ツールに対する `AnyTool` は、そのツールの `input_schema` をそのまま渡し、
    返ってきた `structured_output` を `ToolUseBlock(name=tool.name, input=…)` に包みます。
    `next_action` 以外はすべて単一ツールで、`propose_actions`（crawl）、`propose_assertions`
    （enrich）、`plan`、`diagnose`（triage）、`resolve_alert`（`--dismiss-alerts`）、`find_tabs`
    （タブ locator）がそれぞれ `ToolDef` を 1 つだけ提示します。
  - 複数ツールに対する `AnyTool`（該当するのは `tap` / `type_text` / `wait_for` / `finish` を持つ
    `ClaudeAgent.next_action` のみ）は、判別用スキーマ
    `{"tool": {"enum": [names]}, "arguments": {"oneOf": [各スキーマ]}}` に包み、各ツールのスキーマを
    追記するシステムプロンプトに列挙したうえで、`{tool, arguments}` を `ToolUseBlock` へ戻します。
  - `structured_output` が欠けているか壊れている場合は、`ToolUseBlock` を持たない空の `MessageResponse`
    を返します。各呼び出し側は `first_tool_use()` が `None` になる場合をすでに許容しています。
- **スクラッチファイル経由の vision。** リクエスト中の各 `ImagePart` について、そのバイト列を呼び出しごとの
  一時ディレクトリに PNG として書き出し、user メッセージにパスを示すテキスト行を追記します（たとえば
  「現在の画面は `<path>` にあります。Read で開いてください」）。CLI は `--add-dir <scratchdir>` と、その
  ディレクトリに限定した `--allowedTools Read` で起動し、Claude Code がディスクから画像を読めるようにします。
  呼び出しが返ったらディレクトリを削除します。text-only のリクエストではファイルを書かず、ツールも一切
  許可しません。
- **ツール制限とプロンプトインジェクションの境界（[BE-0125](../BE-0125-authoring-agent-tool-restriction/BE-0125-authoring-agent-tool-restriction-ja.md)
  の拡張）。** 画面上のテキストや要素ラベルは攻撃者の影響を受けうる入力です。`Read`（スクラッチディレクトリに
  限定）以外はすべて `--disallowedTools` で拒否し、`--permission-mode` は fail-closed のままにして、
  非対話モードで想定外の権限要求が起きても素通りさせず拒否します。`Read` は vision の伝送に必要となる
  唯一の権限です。
- **サブスクリプション課金。** 子プロセスの環境から `ANTHROPIC_API_KEY` を取り除きます（CLI の認証優先順位は
  API キーをサブスクリプションのトークンより上に置くため、残すと黙って API へ課金してしまいます）。また
  スクラッチの作業ディレクトリで実行し、CLI がこのリポジトリの `CLAUDE.md` / skills / MCP サーバを
  呼び出しに読み込まないようにします。
- **秘匿化（BE-0047 / BE-0097）。** テキストは中立リクエストに達する前に上流ですでに秘匿化済みで、画像は
  秘匿化できず、利用者が認証した CLI にのみ届きます。この契約は変えません（ファイルはローカルのスクラッチで、
  読むのもローカルの CLI です）。
- **usage の受け渡し。** CLI の JSON エンベロープの usage / cost フィールドから `MessageResponse.usage` を
  ベストエフォートで埋め、`bajutsu.usage` が報告できるようにします（報告のみで、合否判定の経路には
  決して載せません）。

### 2. レジストリへの登録（`bajutsu/ai/registry.py`）

`claude-code` を独自の `Adapter`（固有の `factory` と `credential_gap`）として登録します。`ant` と違い、
`AnthropicBackend` / SDK を経由しないため、共有アダプタの別名ではなく真に別個のアダプタになります。
`credential_gap` は、`claude` バイナリが存在し利用可能な資格情報を持つかを報告し（`claude setup-token` /
`CLAUDE_CODE_OAUTH_TOKEN` を案内する実行可能なメッセージを返す）、資格情報なしで runner を構築するのではなく、
BE-0047 に従って経路を fail-closed にします。これで `known_providers()` にも自動的に含まれます。

### 3. 露出（`serve` とドキュメント）

- `serve` の設定画面のプロバイダ選択に、`api-key` / `bedrock` / `ant` と並べて `claude-code` を出します。
- `doctor` は `claude-code` の資格情報ギャップを他と同様に報告します。
- AI プロバイダのドキュメントと README に、vision の伝送方式とツール制限の境界を含めて、両言語でこの
  プロバイダを記載します。

### 4. テスト

- 注入した `Runner` によるアダプタの単体テスト。`NamedTool` と単一ツールの `AnyTool` はそのまま対応づき、
  複数ツールの `AnyTool` は判別用スキーマを往復し、`structured_output` の欠落や不正では `ToolUseBlock` が
  生じず、`ImagePart` はスクラッチファイルを書き出してそのパスをプロンプトに示し `Read` を許可して後片付け
  し、子プロセスの環境が `ANTHROPIC_API_KEY` を取り除くこと。
- レジストリのテスト。`claude-code` が独自のアダプタに解決され `known_providers()` に現れること、その
  `credential_gap` がバイナリ不在の場合を報告すること。

## 検討した代替案

- **CLI ではなく Claude Agent SDK（Python の `claude-agent-sdk`）を呼ぶ。** 今回は見送ります。SDK は同じ
  資格情報で同じ `claude` CLI を起動するため、認証や課金の利点はなく、その一方で依存関係と 2 つ目の
  プロセス管理面が増えます。CLI は、削除されたバックエンドの実績あるパターンと、現在の `ant` プロバイダの
  サブプロセス 1 箇所の設計に合致します。いずれかの経路がマルチターンのツール結果フィードバックを要する
  ようになれば再検討します（BE-0104 は意図的にそれをモデル化していません）。
- **スクリーンショットを base64 で CLI のプロンプト引数に埋め込む（スクラッチファイルも `Read` も使わない）。**
  見送ります。前回の失敗の様相を再現するうえ（CLI のプロンプト引数の経路は信頼できる画像チャネルではなく、
  引数サイズが膨張します）、ファイルパス方式こそが本項目の核心だからです。
- **`ant` だけを残し `claude-code` を復活させない。** 見送ります。`ant` は API を通じて Console のシートに
  課金するもので、多くの貢献者がすでに持ち `claude` CLI で認証済みの Claude Code の Pro / Max
  サブスクリプションとは別物です。両者は重複ではなく補完的なプロバイダです。
- **旧来の高レベル `Agent` プロトコル統合を復元する。** 見送ります。構造上 vision locator / `triage` /
  `enrich` に届かず、経路ごとの版が必要になります。単一のアダプタで足りるのは `AiBackend` シームがあってこそ
  です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `ClaudeCodeBackend` アダプタ。`create_message`、structured output の対応づけ、vision のスクラッチ
      ファイル、ツール制限、サブスクリプション用の環境、usage の受け渡し（`bajutsu/ai/claude_code.py`）。
- [x] `claude-code` プロバイダのレジストリ登録と `credential_gap`（surface 用の `resolved_provider` も）。
- [x] 露出。`serve` の設定選択とプロバイダハンドラ、`doctor`（`ai_availability` 経由）、両言語の
      `configuration.md`。
- [x] テスト。アダプタの単体テスト（対応づけ、vision、環境）とレジストリのテスト。

**ログ**

- BE-0104 の `AiBackend` シームの背後にアダプタを実装し、6 つの AI 経路すべてがファイルパス方式の
  vision 付きで `claude-code` を選べるようにしました。プロバイダを登録し、`serve` / `doctor` /
  ドキュメントに露出させ、テストを追加しました。`make check` は緑です。
- ローカルの `crawl` での検証。プロンプトを可変長の `--disallowedTools` / `--allowedTools` /
  `--add-dir` の後ろに末尾の `[prompt]` 位置引数として渡していたため、`claude` がそれを不正なツール名
  として飲み込んでいました。プロンプトを stdin で渡し、deny ツールをスペース区切りのトークンにして
  可変長フラグを最後に置くよう修正しました。非ゼロ終了時は stdout のエンベロープを表面化し、認証
  401 などのエラーを実用的に読めるようにしました。
- フォローアップ（[#745](https://github.com/bajutsu-e2e/bajutsu/pull/745)）。プロバイダの起動時開示を
  プロバイダごとに切り替えるようにしました。アダプタの契約に `announce` を追加し、各プロバイダが
  自分の開示行を生成します。Anthropic SDK は reasoning effort のノブを持たないため provider と model
  だけを示し、`claude-code` はこの項目が導入した強制サブスクリプションの認証行と、尊重する effort を
  加えます。`record` と `crawl` は同じ `announce_ai` を共有するようになり、これまで `crawl` が出していなかった
  model（`claude-code` では effort と認証も）を開示します。`make check` は緑です。

## 参考

- [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md) — この
  アダプタが差し込まれる、ベンダー中立な `AiBackend` シーム。
- [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider-ja.md) — 旧来の text-only で
  `record` 専用の Claude Code バックエンドを削除し、`ant` OAuth プロバイダを出荷した項目。
- [BE-0125](../BE-0125-authoring-agent-tool-restriction/BE-0125-authoring-agent-tool-restriction-ja.md)
  — このバックエンドが vision 用の `Read` の場合へ拡張する、オーサリングエージェントのツール制限の境界。
- [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md) — アダプタが保つ、
  データ主権と資格情報ギャップのセマンティクス。

[English](BE-0215-claude-code-oauth-token-credential.md) · **日本語**

# BE-0215 — claude-code プロバイダに CLAUDE_CODE_OAUTH_TOKEN による明示的な資格情報を追加する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0215](BE-0215-claude-code-oauth-token-credential-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0215") |
| 実装 PR | [#869](https://github.com/bajutsu-e2e/bajutsu/pull/869) |
| トピック | AI プロバイダ設定 |
| 関連 | [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider-ja.md)、[BE-0176](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend-ja.md)、[BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets-ja.md)、[BE-0183](../BE-0183-per-provider-serve-settings/BE-0183-per-provider-serve-settings-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0176](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend-ja.md) の `claude-code`
プロバイダ（`bajutsu/ai/claude_code.py`）は、ローカルの `claude` バイナリがすでに保持している
資格情報を引き継ぐことでしか認証できません。その資格情報は、対話的なブラウザログインか、同じ
マシン上で事前に実行した `claude setup-token` の結果です。Bajutsu 自身は、`claude-code` の資格情報
という概念を明示的には持っていません。ドキュメント化された環境変数も、`.env.example` の記載も、
`serve` の Settings パネルの項目もありません。

他のプロバイダには、それぞれ資格情報の入り口があります。`api-key` には `ANTHROPIC_API_KEY` /
`ai.keyEnv` があり、`ant` には `ant auth login` と `serve` 発のログインボタンがあります。

本項目では、`CLAUDE_CODE_OAUTH_TOKEN` を Bajutsu が管理する一級の資格情報として追加します。これは、
`claude setup-token` が非対話的な利用のために発行する長期 OAuth トークンです。`claude` CLI は、
このトークンが環境変数に存在すればそのまま読みます。資格情報は、`.env` や実際の環境変数から設定
できます。さらに、既存の Claude API キー欄と並べて、`serve` の Settings パネルにも write-once な
secret として置けるようにします。

## 動機

- **ヘッドレスなホストには入り口がありません。** `claude-code` の資格情報の経路は、ローカルかつ
  対話的なものだけです。`claude setup-token` のブラウザフローか、既存の端末セッションが保持して
  いるログイン状態のいずれかに限られます。継続的インテグレーション（CI）ランナーやコンテナ、
  リモートの自己ホスト型 `serve` インスタンス
  （[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)）には、そのフローを
  実行できるブラウザも対話端末もありません。BE-0176 の目的は、すべての AI 経路でサブスクリプション
  課金を使えるようにすることでした。それにもかかわらず、こうしたホストではプロバイダをまったく
  使えないままです。
- **トークンによる経路はすでに存在しており、Bajutsu 側の配線が欠けているだけです。**
  `claude setup-token` は、ヘッドレスな環境がまさに必要とする静的で長期のトークンを発行します。
  `claude` CLI は、`CLAUDE_CODE_OAUTH_TOKEN` が環境変数に存在すればすでに読みます。このリポジトリ
  自身の CI 自動化（[BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review-ja.md)）
  も、PR レビューワークフローの `CLAUDE_CODE_OAUTH_TOKEN` シークレットとして、この仕組みに頼って
  います。`bajutsu/ai/claude_code.py` の `_child_env()` は、この変数を取り除きません。周辺のシェルが
  たまたまこの変数をエクスポートしていれば、変数はすでに子プロセスまで届きます。ただし、それは
  「取り除いていないだけ」という偶然の産物です。Bajutsu を通じて設定できる、頼ってよい経路では
  ありません。
- **他のすべてのプロバイダには、Bajutsu が管理する名前付きの資格情報があります。`claude-code`
  にはありません。** `api-key` には `ANTHROPIC_API_KEY` / `ai.keyEnv` と `serve` の Settings 欄が
  あります（[BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets-ja.md)）。
  `ant` には `ant auth login` と `serve` 発のログインボタンがあります
  （[BE-0175](../BE-0175-serve-web-ui-ant-sso-login/BE-0175-serve-web-ui-ant-sso-login-ja.md)）。
  `claude-code` だけを「ホストがたまたまログイン済みかどうか」に任せたままにすると、一貫性を欠き
  ます。BE-0136 の設計は、この状況をすでに見越していました。secret ストアを *名前付き* の secret
  として一般化したのは、「二つ目の名前付き secret も、新しい配線を追加することなく同じストアと
  同じ write-once の保証を再利用できる」ようにするためです。本項目は、その見越されていた二つ目の
  secret にあたります。

## 詳細設計

1. **`bajutsu/ai/claude_code.py` がトークンを明示的に認識します。** 環境変数名を持つモジュール
   レベルの定数（`OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"`）を追加します。これにより、アダプタは
   取り除かれずに通過しているだけの状態から、この資格情報を明文化した状態になります。`_ROUTING_ENV`
   にこの変数は含まれず、子プロセスにはすでに届いているので、`_child_env()` の挙動は変えません。
   この工程の狙いは、ドキュメントや `serve`、`credential_gap` が同じ名前で参照できる対象にする
   ことです。サブプロセス呼び出しの挙動は変えません。
2. **`.env.example` に、API キーの代替として記載します。** 既存の `ANTHROPIC_API_KEY` の行と並べて
   書きます。想定するのは、本項目が解決しようとしている状況です。つまり、`claude setup-token` の
   ブラウザフローを対話的に実行できないヘッドレスなホストで、トークンを別の場所で一度だけ発行して
   コピーしてくる運用として書きます。
3. **`serve` の write-once secret ストアに、二つ目の名前付き secret を追加します。** BE-0136 が
   この一般化をすでに設計しています。既存の `AI_API_KEY_SECRET` と並べて
   `AI_CLAUDE_CODE_TOKEN_SECRET = "aiClaudeCodeOauthToken"` を追加します。この secret には、
   `bajutsu/serve/operations/config.py` の `api_key_info` / `set_api_key` と同じ形の `describe` と `set`
   の対（`claude_code_token_info` / `set_claude_code_token`）を持たせます。
   - ローカルの `serve` は、保存した値をプロセス環境の `CLAUDE_CODE_OAUTH_TOKEN` に反映します。
     `set_api_key` が `ai.keyEnv` に反映するのと同じ形です。
   - ホスト型バックエンドは、他の名前付き secret と同様に、組織ごとに暗号化して保存します。
   BE-0136 が `aiApiKey` に与えているスコープの注記を、そのまま引き継ぎます。ホスト型ワーカーが
     spawn したジョブで secret を実際に利用する対応は、別のフォローアップとします。
4. **Settings UI。** `bajutsu/templates/serve.html.j2` / `serve.core.js` に「Claude Code OAuth
   トークン」欄を追加します。欄は既存の API キー欄と同じ形で、書き込み専用です。保存後は、マスクした
   プレビューだけを返します。`claude-code` プロバイダが選択されているときにだけ表示します。既存の
   API キー欄は置き換えず、並べて置きます。一つのホストが両方の資格情報を保持できるので、どちらも
   入力し直さずにプロバイダを切り替えられるようにするためです。
5. **`ai_availability.py` のギャップメッセージが新しい経路に言及します。** 既存の
   `CLAUDE_CODE_CLI_MISSING` のメッセージは、すでに「Claude Code をインストールしてサインインして
   ください（`claude setup-token`、または対話的なログイン）」と述べています。ここに
   `CLAUDE_CODE_OAUTH_TOKEN` を、非対話的な代替として明記するよう拡張します。`doctor` / `serve` の
   ギャップメッセージは、対話的な経路だけでなく、本項目が追加するヘッドレスな経路も案内するように
   なります。`credential_gap()` の判定ロジックは変更せず、これまでどおりバイナリの有無だけを見ます。
   これは、BE-0176 が意図して選んだトレードオフです。CLI に対する生きた資格情報の事前確認は当てに
   なりません。そこで、認証されていない呼び出しは、どの方法で認証したかに関わらず、呼び出し時点で
   大きく失敗させます。判定を作り込むことは、本項目の対象外とします。詳細は「検討した代替案」に記し
   ます。
6. **ドキュメント。** `docs/configuration.md`（および `docs/ja/`）の既存の `claude-code`
   プロバイダの段落の近くに `CLAUDE_CODE_OAUTH_TOKEN` を記載します。書き方は、ヘッドレス、CI、
   自己ホスト型の利用を想定したものにします。README の自己ホスティングの節にある Secrets の案内
   にも、一行のポインタを加えます。
7. **テスト。** 次の二つのテストを追加します。
   - 新しい secret エンドポイントの対に対する `serve` operations のテスト。設定してから describe
     する往復がプレーンテキストを一切返さないことを確認し、既存の `aiApiKey` の契約テストと同じ形
     にします。
   - `OAUTH_TOKEN_ENV` が変更されないまま子プロセスへエクスポートされることを確認する、
     `claude_code.py` のテスト。現在すでにそうなっています。このテストは契約を明文化するもので、
     将来 `_ROUTING_ENV` を編集した際に、変数が気付かず取り除かれてしまうことを防ぎます。

## 検討した代替案

- **環境変数を文書化するだけでなく、実際の資格情報検出まで作り込む案。** この検出は、軽量な
  `claude` プローブを起動して、生きたトークンやセッションの有無を確認するものです。本項目では
  見送ります。BE-0176 は、簡易な事前プローブは当てにならないと、すでに意図して判断しています。認証
  されていない呼び出しは、プローブから推測せず、実行時に大きく失敗させるべきだという判断です。
  このトレードオフを覆すことは、「トークンに文書化された設定先を与える」こととは別の、より大きな
  議論です。さらに、一つの項目に二つの異なる問題を混ぜることにもなります。
- **新しい secret を独立させず、既存の `aiApiKey` の枠に押し込む案。** 見送ります。両者は別々の
  プロバイダのための別々の資格情報で、API キーと、CLI が消費する OAuth トークンです。BE-0136 の
  `SecretStore` は、新しい配線を追加することなく二つ目の名前付き secret を持てるよう、明示的に
  設計されています。一つの枠に無関係な二つの値を持たせると、この区別が失われます。さらに、`serve` の
  Settings パネルで、どちらの資格情報が設定されているのかが曖昧になります。
- **環境変数の文書化だけにとどめ、`serve` の Settings UI への追加は見送る案。** より小さな切り分け
  として検討しました。しかし、本項目の動機であるデプロイ形態、つまりブラウザを持たないホスト型
  `serve` インスタンスでは、コンテナの環境変数をアウトオブバンドで編集する以外に、資格情報を設定
  する手段が残りません。BE-0016 は、マネージドで自己ホスト型の利用ケースをすでに第一級のデプロイ
  形態として扱っています。この案は、そのケースでの目的を損ないます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `bajutsu/ai/claude_code.py` に `CLAUDE_CODE_OAUTH_TOKEN` を文書化された定数として追加
- [x] `.env.example` への記載
- [x] `serve` の write-once secret：`aiClaudeCodeOauthToken`（ローカルは環境変数反映、ホスト型は
      暗号化保存）、`claude_code_token_info` / `set_claude_code_token` の operations
- [x] `claude-code` が選択プロバイダのときに表示する Settings UI 欄
- [x] `ai_availability.py` のギャップメッセージがトークンを非対話的な代替として明記
- [x] ドキュメント（`docs/configuration.md`、`docs/ja/`、自己ホスティングの Secrets へのポインタ）
- [x] テスト：secret エンドポイントの契約、`_child_env` の通過契約テスト

ログ：

- [#869](https://github.com/bajutsu-e2e/bajutsu/pull/869)：本項目を一括で実装しました。
  `OAUTH_TOKEN_ENV` 定数、`/api/claudecodetoken` エンドポイントを持つ `aiClaudeCodeOauthToken` の
  write-once secret、プロバイダで出し分ける Settings 欄、ギャップメッセージ、`.env.example`、
  ドキュメントの更新、エンドポイントと `_child_env` の通過テストまでを含みます。

## 参考

- `bajutsu/ai/claude_code.py`：本項目が明示的な資格情報を与える `claude-code` アダプタ
- `bajutsu/serve/secrets.py`、`bajutsu/serve/operations/config.py`：本項目の新しい名前付き
  secret が拡張する write-once な `SecretStore` シーム
- [BE-0176 — Claude Code を file-based vision つきの AiBackend アダプタとして復活させる](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend-ja.md)：
  本項目が Bajutsu 管理の資格情報を与えるプロバイダ
- [BE-0163 — Claude Code CLI によるオーサリングバックエンドを ant CLI の OAuth プロバイダに置き換える](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider-ja.md)：
  このリポジトリで `claude setup-token` / `CLAUDE_CODE_OAUTH_TOKEN` の仕組みを最初に文書化した項目
- [BE-0136 — serve 向けの write-once secret ストア](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets-ja.md)：
  本項目の二つ目の名前付き secret が設計を踏襲する secret ストア
- [BE-0175 — serve の Web UI から ant プロバイダにサインインする](../BE-0175-serve-web-ui-ant-sso-login/BE-0175-serve-web-ui-ant-sso-login-ja.md)：
  Settings UI にプロバイダ固有の資格情報の入り口を置く先例
- [BE-0203 — Claude Code による PR レビューワークフロー](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review-ja.md)：
  このリポジトリ自身の CI 自動化がすでに同じ仕組みで `CLAUDE_CODE_OAUTH_TOKEN` に頼っている
- Anthropic のドキュメント：`claude setup-token`（非対話的な利用のための長期 OAuth トークン）

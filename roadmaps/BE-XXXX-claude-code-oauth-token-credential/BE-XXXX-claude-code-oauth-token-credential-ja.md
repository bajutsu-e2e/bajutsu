[English](BE-XXXX-claude-code-oauth-token-credential.md) · **日本語**

# BE-XXXX — claude-code プロバイダに CLAUDE_CODE_OAUTH_TOKEN による明示的な資格情報を追加する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-claude-code-oauth-token-credential-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | AI provider configuration |
| 関連 | [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider-ja.md)、[BE-0176](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend-ja.md)、[BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets-ja.md)、[BE-0183](../BE-0183-per-provider-serve-settings/BE-0183-per-provider-serve-settings-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0176](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend-ja.md) の
`claude-code` プロバイダ（`bajutsu/ai/claude_code.py`）は、ローカルの `claude` バイナリがすでに
保持している資格情報をそのまま引き継ぐことでしか認証できません。対話的なブラウザログイン、または
同じマシン上で事前に実行した `claude setup-token` の結果に頼っています。Bajutsu 自身は
`claude-code` の資格情報という概念を明示的には持っていません。ドキュメント化された環境変数もなく、
`.env.example` にも記載がなく、`serve` の Settings パネルにも項目がありません。他のプロバイダには
それぞれ資格情報の入り口があるのとは対照的です（`api-key` には `ANTHROPIC_API_KEY` / `ai.keyEnv`
が、`ant` には `ant auth login` と `serve` 発のログインボタンがあります）。本項目では、
非対話的な利用のために `claude setup-token` が発行する長期 OAuth トークンであり、`claude` CLI が
環境変数に存在すればそのまま読む `CLAUDE_CODE_OAUTH_TOKEN` を、Bajutsu が管理する一級の資格情報
として追加します。`.env` や実際の環境変数から設定できるようにするだけでなく、既存の Claude API
キー欄と並べて `serve` の Settings パネルにも write-once な secret として置けるようにします。

## 動機

- **ヘッドレスなホストには入り口がありません。** `claude-code` の資格情報の経路は、ローカルかつ
  対話的なものだけです。`claude setup-token` のブラウザフローか、既存の端末セッションが保持して
  いるログイン状態のいずれかに限られます。CI ランナーやコンテナ、あるいはリモートの自己ホスト型
  `serve` インスタンス（[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)）
  には、そのフローを実行できるブラウザも対話端末もありません。BE-0176 の目的がすべての AI 経路で
  サブスクリプション課金を使えるようにすることだったにもかかわらず、そうしたホストではこのプロバ
  イダをまったく使えないままです。
- **トークンによる経路はすでに存在しており、Bajutsu 側の配線が欠けているだけです。**
  `claude setup-token` は、ヘッドレスな環境がまさに必要とする静的で長期のトークンを発行します。
  `claude` CLI は `CLAUDE_CODE_OAUTH_TOKEN` が環境変数に存在すればすでにそれを読みます。この
  リポジトリ自身の CI 自動化（[BE-0203](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review-ja.md)）
  も、PR レビューワークフローの `CLAUDE_CODE_OAUTH_TOKEN` シークレットとして、まさにこの仕組みに
  すでに頼っています。`bajutsu/ai/claude_code.py` の `_child_env()` はこの変数を取り除かないため、
  周辺のシェルがたまたまこの変数をエクスポートしていれば、すでに子プロセスまで届きます。ただし
  それは「取り除いていないだけ」という偶然の産物であり、Bajutsu を通じて設定できる、頼ってよい
  経路ではありません。
- **他のすべてのプロバイダには、Bajutsu が管理する名前付きの資格情報があるのに、`claude-code`
  にはありません。** `api-key` には `ANTHROPIC_API_KEY` / `ai.keyEnv` と `serve` の Settings 欄が
  あり（[BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets-ja.md)）、
  `ant` には `ant auth login` と `serve` 発のログインボタンがあります
  （[BE-0175](../BE-0175-serve-web-ui-ant-sso-login/BE-0175-serve-web-ui-ant-sso-login-ja.md)）。
  `claude-code` だけを「ホストがたまたまログイン済みかどうか」に任せたままにするのは一貫性を欠き
  ます。しかも BE-0136 自身の設計は、この状況をすでに見越していました。secret ストアを *名前付き*
  の secret として一般化したのは、「二つ目の名前付き secret も、新しい配線を追加することなく同じ
  ストアと同じ write-once の保証を再利用できる」ようにするためであり、本項目はまさにその見越されて
  いた二つ目の secret にあたります。

## 詳細設計

1. **`bajutsu/ai/claude_code.py` がトークンを明示的に認識します。** 環境変数名を持つモジュール
   レベルの定数（`OAUTH_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"`）を追加し、単に取り除かれずに
   通過しているだけの状態から、アダプタがこの資格情報を明文化した状態にします。`_child_env()` の
   挙動自体を変える必要はありません。この変数はすでに `_ROUTING_ENV` に含まれておらず、子プロセス
   にはすでに届いているからです。この工程の狙いは、ドキュメントや `serve`、`credential_gap` が
   同じ名前で参照できる対象にすることであり、サブプロセス呼び出しの挙動を変えることではありません。
2. **`.env.example` に、API キーの代替として記載します。** 既存の `ANTHROPIC_API_KEY` の行と
   並べ、本項目が解決しようとしている状況、つまり `claude setup-token` のブラウザフローを対話的に
   実行できないヘッドレスなホストを想定し、トークンを別の場所で一度だけ発行してコピーしてくる
   運用として書きます。
3. **`serve` の write-once secret ストアに、二つ目の名前付き secret を追加します。** BE-0136 が
   すでに見越していた一般化に沿って、既存の `AI_API_KEY_SECRET` と並べて
   `AI_CLAUDE_CODE_TOKEN_SECRET = "aiClaudeCodeOauthToken"` を追加し、`bajutsu/serve/operations/config.py`
   の `api_key_info` / `set_api_key` と同じ形の `describe`・`set` の対
   （`claude_code_token_info` / `set_claude_code_token`）を持たせます。ローカルの `serve` は、
   保存した値をプロセス環境の `CLAUDE_CODE_OAUTH_TOKEN` にそのまま反映します（`set_api_key` が
   `ai.keyEnv` に反映するのと同じ形です）。ホスト型バックエンドは、他の名前付き secret と同様に
   組織ごとに暗号化して保存し、「ホスト型ワーカーの spawn したジョブでの実際の利用は別の
   フォローアップとする」という、BE-0136 が `aiApiKey` にすでに与えているのと同じ、明示的な
   スコープの注記を引き継ぎます。
4. **Settings UI。** `bajutsu/templates/serve.html.j2` / `serve.core.js` に「Claude Code OAuth
   トークン」欄を追加します。既存の API キー欄と同じ形（書き込み専用で、保存後はマスクした
   プレビューだけを返す）にし、`claude-code` プロバイダが選択されているときにだけ表示します。
   既存の API キー欄を置き換えるのではなく並べて置くことで、一つのホストが両方の資格情報を
   保持し、どちらかを入力し直すことなくプロバイダを切り替えられるようにします。
5. **`ai_availability.py` のギャップメッセージが新しい経路に言及します。** 既存の
   `CLAUDE_CODE_CLI_MISSING` のメッセージはすでに「Claude Code をインストールしてサインインして
   ください（`claude setup-token`、または対話的なログイン）」と述べています。ここに
   `CLAUDE_CODE_OAUTH_TOKEN` を非対話的な代替として明記するよう拡張し、`doctor` / `serve` の
   ギャップメッセージが、対話的な経路だけでなく本項目が追加するヘッドレスな経路も積極的に案内
   するようにします。`credential_gap()` 自体の判定ロジックは変更しません。バイナリの有無だけを
   見る現在の判定は BE-0176 が意図して選んだトレードオフであり（CLI に対する生きた資格情報の
   事前確認は当てにならないため、認証されていない呼び出しは、どちらの方法で認証したかに関わらず
   呼び出し時点で大きく失敗させる、という判断です）、この判定を作り込むことは本項目の対象外と
   します。詳細は「検討した代替案」に記します。
6. **ドキュメント。** `docs/configuration.md`（および `docs/ja/`）の既存の `claude-code`
   プロバイダの段落の近くに `CLAUDE_CODE_OAUTH_TOKEN` を記載し、ヘッドレス・CI・自己ホスト型の
   利用を想定した書き方にします。README の自己ホスティングの Secrets に関する案内にも一行の
   ポインタを加えます。
7. **テスト。** 新しい secret エンドポイントの対に対する `serve` operations のテスト
   （設定してから describe する往復がプレーンテキストを一切返さないことを確認し、既存の
   `aiApiKey` の契約テストと同じ形にします）。`OAUTH_TOKEN_ENV` が変更後も子プロセスへそのまま
   エクスポートされることを確認する `claude_code.py` のテスト（現在すでにそうなっていますが、
   将来 `_ROUTING_ENV` を編集した際に気付かず取り除かれてしまわないよう、契約として明文化します）。

## 検討した代替案

- **環境変数を文書化するだけでなく、実際の資格情報検出（軽量な `claude` プローブを起動して生きた
  トークンやセッションの有無を確認する）まで作り込む。** 本項目では見送ります。BE-0176 はすでに、
  簡易な事前プローブは当てにならず、認証されていない呼び出しはプローブから推測するのではなく実行
  時に大きく失敗させるべきだ、という判断を意図的に下しています。このトレードオフを覆すことは、
  「トークンに文書化された設定先を与える」こととは別の、より大きな議論であり、一つの項目に二つの
  異なる問題を混ぜることになります。
- **新しい secret を独立させず、既存の `aiApiKey` の枠に押し込む。** 見送ります。両者は別々の
  プロバイダのための別々の資格情報です（API キーと、CLI が消費する OAuth トークン）。BE-0136 の
  `SecretStore` は、新しい配線を追加することなく二つ目の名前付き secret を持てるよう明示的に
  設計されており、一つの枠に無関係な二つの値を持たせてしまうと、その区別が失われ、`serve` の
  Settings パネルでどちらの資格情報が設定されているのかが曖昧になります。
- **環境変数の文書化だけにとどめ、`serve` の Settings UI への追加は見送る。** より小さな切り分け
  として検討しましたが、それでは本項目の動機そのものである、ブラウザを持たないホスト型 `serve`
  インスタンスに対して、コンテナの環境変数をアウトオブバンドで編集する以外に資格情報を設定する
  手段が残りません。これは、BE-0016 がすでに第一級のデプロイ形態として扱っている、マネージド・
  自己ホスト型の利用ケースの目的を損ないます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `bajutsu/ai/claude_code.py` に `CLAUDE_CODE_OAUTH_TOKEN` を文書化された定数として追加
- [ ] `.env.example` への記載
- [ ] `serve` の write-once secret：`aiClaudeCodeOauthToken`（ローカルは環境変数反映、ホスト型は
      暗号化保存）、`claude_code_token_info` / `set_claude_code_token` の operations
- [ ] `claude-code` が選択プロバイダのときに表示する Settings UI 欄
- [ ] `ai_availability.py` のギャップメッセージがトークンを非対話的な代替として明記
- [ ] ドキュメント（`docs/configuration.md`、`docs/ja/`、自己ホスティングの Secrets へのポインタ）
- [ ] テスト：secret エンドポイントの契約、`_child_env` の通過契約テスト

## 参考

- `bajutsu/ai/claude_code.py` — 本項目が明示的な資格情報を与える `claude-code` アダプタ
- `bajutsu/serve/secrets.py`、`bajutsu/serve/operations/config.py` — 本項目の新しい名前付き
  secret が拡張する write-once な `SecretStore` シーム
- [BE-0176 — Claude Code を file-based vision つきの AiBackend アダプタとして復活させる](../BE-0176-claude-code-ai-backend/BE-0176-claude-code-ai-backend-ja.md)
  — 本項目が Bajutsu 管理の資格情報を与えるプロバイダ
- [BE-0163 — Claude Code CLI によるオーサリングバックエンドを ant CLI の OAuth プロバイダに置き換える](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider-ja.md)
  — このリポジトリで `claude setup-token` / `CLAUDE_CODE_OAUTH_TOKEN` の仕組みを最初に文書化した項目
- [BE-0136 — serve 向けの write-once secret ストア](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets-ja.md)
  — 本項目の二つ目の名前付き secret が設計を踏襲する secret ストア
- [BE-0175 — serve の Web UI から ant プロバイダにサインインする](../BE-0175-serve-web-ui-ant-sso-login/BE-0175-serve-web-ui-ant-sso-login-ja.md)
  — Settings UI にプロバイダ固有の資格情報の入り口を置く先例
- [BE-0203 — Claude Code による PR レビューワークフロー](../BE-0203-claude-code-pr-review/BE-0203-claude-code-pr-review-ja.md)
  — このリポジトリ自身の CI 自動化がすでに同じ仕組みで `CLAUDE_CODE_OAUTH_TOKEN` に頼っている
- Anthropic のドキュメント：`claude setup-token`（非対話的な利用のための長期 OAuth トークン）

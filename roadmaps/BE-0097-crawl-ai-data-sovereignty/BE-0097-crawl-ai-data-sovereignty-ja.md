[English](BE-0097-crawl-ai-data-sovereignty.md) · **日本語**

# BE-0097 — crawl ガイドと serve が起動する AI 経路の AI データ主権

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0097](BE-0097-crawl-ai-data-sovereignty-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0097") |
| 実装 PR | [#380](https://github.com/bajutsu-e2e/bajutsu/pull/380) |
| トピック | 競合調査（Maestro）由来の候補 |
<!-- /BE-METADATA -->

## はじめに

[BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md) が
`record` ／ `triage` ／ `--dismiss-alerts` に与えた AI データ主権の保証を、**`crawl --guide ai`**
の経路（とそのアラートガード）へ広げ、あわせて **`serve`** が起動する AI run にその保証がどう届くかを
定めます。BE-0047 の後、オーサリング／調査の経路はユーザーが設定したプロバイダで動き、テキスト入力を
秘匿化し、キーが無ければフェイルクローズします。しかし crawl の AI ガイドはまだどれもしていないので、
「あなたの AI、あなたのキー、あなたのデータ」はまだ半分しか本当ではありません。本項目はその穴を塞ぎます。
Tier-1 に留まり、`run` ／ CI ゲートにモデルを追加しません。

## 動機

BE-0047 はオーサリング／調査の経路に対し 3 つの保証を具体化しました。config 駆動で差し替え可能な
プロバイダ（`defaults.ai` ／ `targets.<name>.ai` → `Effective.ai`）、モデルへ送るテキスト入力の秘匿化、
キー未設定時のフェイルクローズです。その報告自身が残りを指摘していました。`crawl` と `serve` は対象外でした。

現状、これは具体的に次のように現れます。

- **`crawl --guide ai`** は同じ `anthropic_client` ファクトリ経由で Claude に到達しますが、エージェントと
  アラートガードを、解決済みの `eff.ai` プロバイダ設定**なし**、run スコープの `Redactor`**なし**で構築します。
  そのため crawl の AI ガイドは画面の要素ツリー（とアラートガードの instruction テキスト）を秘匿化せずに
  モデルへ送り、`ai` に設定した自己ホストゲートウェイや社内プロキシを無視します。資格情報チェックは
  BE-0047 が追加したプロバイダ対応版ではなく、環境変数のみを見る `credential_gap()`
  （`bajutsu/cli/commands/crawl.py` の `_ai_credential_gap`）です。
- **`serve`** は `run` ／ `record` ／ `crawl` をサブプロセスとして起動し（`bajutsu/serve/jobs.py` の
  `_spawn_env`）、`ANTHROPIC_API_KEY` を環境変数で渡します。起動された `record` ／ `triage` はすでに
  BE-0047 を継承しますが（束縛された config の `defaults.ai` を読み、秘匿化します）、`crawl` はまだです。
  また serve UI のキー設定（`set_api_key`）は `ANTHROPIC_API_KEY` だけを書き、config の `keyEnv` を
  尊重しません。

ベンダークラウドの AI と比較検討するプライバシーに敏感なチームにとって、秘匿化されない crawl ガイドは、
まさに BE-0047 が排除しようとしている静かなデータ流出です。保証は大半の AI 経路ではなく、**すべて**の
AI 経路で成り立つべきです。

## 詳細設計

BE-0047 の継ぎ目を再利用します。新しい仕組みはありません。作業は、同じ 3 つの保証を crawl 経路へ通し、
serve 経路を確認することです。

- **crawl ガイドのプロバイダ設定と秘匿化。** crawl ガイドが組み立てるエージェント（crawl 経路の
  `make_agent` ／ `ClaudeAgent` 構築）とそのアラートガード（`ClaudeAlertLocator`）に、BE-0047 が
  `record` でしたのと同じく、解決済みの `eff.ai` を通します。run スコープの `Redactor`（対象の `redact`
  キーと秘密値から、証跡が既に構築しているのと同じ作り方で得るもの）を渡し、要素ツリーとアラートの
  instruction をプロセスを離れる前に秘匿化します。スクリーンショットは画像のまま、BE-0047 と同じ正直な
  限界です。テキストは秘匿化し、**すべて**の入力はユーザーが設定したプロバイダにのみ送り、ベンダーの
  既定先には送りません。
- **crawl ガイドのフェイルクローズ。** crawl の環境変数のみの `_ai_credential_gap` を、プロバイダ対応の
  `credential_gap(eff.ai)` に置き換えます。資格情報が未設定なら `crawl --guide ai`（とアラートガード）は
  プロバイダ別の明確なエラーで即座に失敗し、ホストされた既定先への静かな往復はしません。`record` ／
  `triage` と揃います。
- **serve の継承を明示する。** serve が起動する `crawl` は、ローカル CLI と同じく束縛された config の
  `defaults.ai` ／ `targets.<name>.ai` を読むので、上の crawl 経路がそれらを尊重すれば、プロバイダ設定と
  秘匿化を自動的に受け取ります。serve 固有の穴を一つ塞ぎます。UI のキー設定と `_spawn_env` が、有効な
  config の `keyEnv`（既定は `ANTHROPIC_API_KEY`）が指す環境変数を設定するようにし、非既定の `keyEnv` も
  serve 下で動くようにします。

決定的な `run` ／ CI ゲートには手を付けません。これはすべて Tier-1（crawl 探索のガイドと serve の
オーサリングジョブ）であり、pass/fail は機械のみのまま、ゲートにモデルは増えません。ノブは既存の `ai`
設定です（アプリ非依存）。

### 検証

いずれも高速ゲート内、実 API 不要です（SDK クライアントは注入可能で、BE-0047 のテストもそれに依拠します）。

- crawl ガイドのエージェントとアラートガードが `eff.ai` と `Redactor` を受け取り、クロールした要素の
  value ／ label とアラート instruction に含めた既知の秘密が、送信ペイロードで秘匿化されること。
- 設定したプロバイダに資格情報が無いとき `crawl --guide ai` が明確なエラーで終了し、クライアントが
  一度も構築されないこと。
- serve が起動する crawl が束縛 config からプロバイダ設定を解決し、非既定の `keyEnv` が起動環境へ
  エクスポートされること。

## 検討した代替案

- **新規項目にせず BE-0047 に取り込む。** 不採用。BE-0047 は出荷済み（`実装済み`）で、閉じた項目の拡張こそ
  新しく追跡可能な BE の役目です。本項目は BE-0047 を土台として参照します。
- **crawl は Tier-1 の探索でゲートではないので対象外にする。** 不採用。データ流出の懸念は
  「何がモデルへプロセスの外に出るか」であり、crawl ガイドは毎ステップそれを行います。一つの AI 経路でも
  例外があれば、主権の保証は意味を失います。
- **秘匿化が設定されていなければ `crawl --guide ai` を止める。** 鈍すぎるので不採用。秘匿化は前提条件では
  なくベストエフォートのマスキングです。正しい形は BE-0047 と同じく「秘匿化できるものは常に秘匿化し、常に
  あなたのプロバイダにのみ送る」であって、新しいゲートではありません。

## 進捗

- [x] 出荷済み。上記の *実装 PR* を参照してください。

## 参考

- [BE-0047 — AI データ主権](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md)
  — 本項目が拡張する出荷済みの保証。`ai` 設定、`Redactor` の配線、フェイルクローズのパターンを再利用します。
- `bajutsu/anthropic_client.py`（`AiConfig` ／ `make_client` ／ `resolve_model` ／ `credential_gap`）、
  `bajutsu/cli/commands/crawl.py`（`_ai_credential_gap`、ガイドエージェントとアラートガードの構築）、
  `bajutsu/crawl.py`、`bajutsu/alerts.py`、`bajutsu/redaction.py`、`bajutsu/serve/jobs.py`（`_spawn_env`）、
  `bajutsu/serve/operations.py`（`set_api_key`）— 修正が触れる面。
- [DESIGN §2 / §3.1](../../../DESIGN.md) — Tier-1 の AI と決定的ゲート。

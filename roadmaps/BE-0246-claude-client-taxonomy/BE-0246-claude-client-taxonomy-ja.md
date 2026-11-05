[English](BE-0246-claude-client-taxonomy.md) · **日本語**

# BE-0246 — Claude と話すためのモジュール構成を整理する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0246](BE-0246-claude-client-taxonomy-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0246") |
| 実装 PR | [#1012](https://github.com/bajutsu-e2e/bajutsu/pull/1012), [#1030](https://github.com/bajutsu-e2e/bajutsu/pull/1030), [#1041](https://github.com/bajutsu-e2e/bajutsu/pull/1041), [#1056](https://github.com/bajutsu-e2e/bajutsu/pull/1056) |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

「モデルにどう到達するか」を決めるコードは、3 つの層に分かれ、互いに重なり合っています。認証情報の欠落
チェック、プロバイダの解決、プロバイダ非依存の設定解決の 3 層です。加えて、モジュールが実際に持っている中身を
誤解させる命名もいくつかあります。

どの層も動作自体は正しく、docstring も分割の理由を丁寧に説明しています。しかし、その分割を正当化するのに
docstring 一段落が必要になること自体が、本項目の対象とする症状です。一文で済む問いに答えるために、読み手は
3 つのモジュールの関係を頭の中に保持しなければなりません。

本項目では、タクソノミーの整理を提案します。内容は、リネーム、1 件の統合、そして混乱の周りに積み上がった
定型処理の重複解消です。挙動の変更は伴いません。

## 動機

**「モデルにどう到達するか」に答える層が 3 つあり、そのうち 2 つは独立していません。**
`bajutsu/anthropic_client.py:309` は `credential_gap` を定義しています。`bajutsu/ai/registry.py:148` は
もう一つの `credential_gap` を定義しています。`bajutsu/ai_availability.py:20` の `availability` が、
この両方を包む 3 つ目のラッパーです。

registry 側の実装は、チェックを重複させているわけではありません。`api-key`、`bedrock`、`ant` の各
プロバイダに対しては、`anthropic_client` の関数へ（`_adapter(ai).credential_gap(ai)` という形で）
ディスパッチします。`claude-code` プロバイダに対しては、`ai/claude_code.py` 自身の `credential_gap` へ
ディスパッチします。プロバイダの解決にも、同様に 2 つの実装があります。`anthropic_client.py:140` の
`provider` が一つ、`ai/registry.py:105` の `_provider_name` と `:126` の `resolved_provider` がもう一つです。

registry 側のチェックの大半は重複ではなく委譲なので、3 層構成自体は冗長ではありません。しかし、それを
確かめるには 3 つのファイルすべてを読むしかありません。この説明が必要になっていること自体が問題です。
解決関数、ディスパッチャ、便利ラッパーは、同じ問いに答えるものです。コントリビュータが手作業で突き合わせる
3 つのファイルではなく、ひと目でわかる一つの形として読めるべきです。

**`anthropic_client.py` という名前は実態と合っていません。** ベンダー名を冠していますが、このモジュールが
持っているのは、`claude-code` を含むすべてのバックエンドが使う、プロバイダ非依存の設定解決です。

`resolve_model`（236 行目）、`resolve_effort`（254 行目）、`resolve_language`（273 行目）、
`language_instruction`（291 行目）、そして `AiConfig` の再エクスポート（42 行目）です。

`ai/claude_code.py:38` と `ai/registry.py:22` は、どちらもこのモジュールから import しています。
つまり、あらゆるプロバイダと話すために存在するモジュールが、特定 1 社のソフトウェア開発キット（SDK）の名前を
冠しています。「モデル・effort・言語の解決はどこにあるか」を import 文から追うコントリビュータには、
`anthropic_client` という名前のファイルを覗く理由がありません。

**`agent.py` と `agents.py` は、単数形と複数形の見分けにくさという罠です。** `agent.py` は `Agent` /
`EnrichmentAgent` のプロトコルと、データ転送オブジェクト（DTO）の `Observation`、`Proposal` を持ちます。
`agents.py` は 44 行のファクトリ（`make_agent`、`make_enrichment_agent`）です。名前の違いは 1 文字だけです。
どのファイル一覧でも隣り合って並ぶので、両方を開くまで区別がつきません。

**7 つの `Claude*` クラスが、同じバックエンドと使用量記録の定型処理を再実装しています。** 次のクラスは
いずれも、バイト単位で同一の `_ensure_backend` を持っています。

`ClaudeAgent`（`bajutsu/claude_agent.py:632`）、`ClaudeTriageAgent` と `ClaudeCrossRunTriageAgent`
（`bajutsu/claude_triage.py:253` と `:473`）、`ClaudeEnrichmentAgent`（`bajutsu/claude_enrich_agent.py:190`）、
`ClaudeActionProposer`（`bajutsu/crawl_guide.py:360`）、`ClaudeTabLocator`（`bajutsu/crawl_tabs.py:192`）、
`ClaudeAlertLocator`（`bajutsu/alerts.py:178`）です。

```python
def _ensure_backend(self) -> AiBackend:
    if self._backend is None:
        self._backend = create_backend(ai=self._ai)
    return self._backend
```

各クラスは、ほぼ同一の `__init__` も持っています（`self._backend`、`self._ai`、`self._redactor`、
`resolve_model` 経由の `self._model`）。モデルとのやり取りのたびに、ほぼ同一の
`usage.record(response.usage, provider=..., model=...)` も呼んでいます。

この形の重複は、BE-0140 がかつて `ensure_client` として一度解消しています。その後、BE-0104 が生の SDK
クライアントから `AiBackend` という抽象へ移行し、`ensure_client` は役目を終えました。共有の置き換えは
導入されず、各クラスが独自の `_ensure_backend` を静かに書き足していきました。`anthropic_client.py` に残る
`ensure_client` は、いまや呼び出し元が一つもないデッドコードです。デッドラッパー自体の除去は、別の提案が
担います。本項目は、7 つのクラスに共有の基底を与えます。次に増えるクラスが、同じパターンを手作業でコピー
しなくて済むようにするためです。

**プロンプトの組み立ても分散しています。** 同じファイル群の中に、8 つの system プロンプトがインラインで
存在します。プライムディレクティブの境界線は、モデルに「提案するだけで pass/fail は決して判断しない」と
念押しする一文です。この一文が、3 つのファイルの 4 箇所に手作業でコピーされています。

`claude_triage.py:52` と `:289` は「You are advisory only — you never decide pass/fail, you diagnose and suggest.」です。
`crawl_guide.py:186` は「You only choose what to TRY. You never decide pass/fail and never judge results.」です。
`crawl_tabs.py:131` は「You only report where the tabs are. You never decide pass/fail.」です。

それぞれの文言はわずかに異なります。境界線を手作業でコピーすると、文言がずれていくリスクがあります。
この境界線は、AI の各パスを prime directive 1 に対して誠実に保つために存在します。

これとは別に、要素ツリーのレンダラーが 5 か所で再実装されています。レンダラーは、UI のスナップショットを
モデルが読めるテキストに変換します。5 か所の形式は、微妙に異なります。

`claude_agent.py:416` のインライン実装、`claude_enrich_agent.py:132` の `_render_elements`、
`claude_triage.py:159` の `_render` と `:391` の `_render_evidence`、`crawl_guide.py:251` の
`_render_elements` です。

フィールドの順序、クォートの有無、どの属性を省くかが、それぞれ異なります。このような共有フラグメントを
一箇所にまとめる前例は、コードベースにすでにあります。`claude_agent.py:173` の `_TARGET_PROPS`
（ツールスキーマのフラグメント）は、`claude_enrich_agent.py:31` から再定義ではなく直接 import されています。

**`record.py` の private なヘルパーは、事実上の共有 API になっています。** `record.py` の
`_describe_step`、`_screenshot_bytes`、`_settle_step`、`_execute`、`_clear_blocking` は、いずれも先頭に
アンダースコアが付いています。この名前は「モジュール内部専用」を意味します。しかし、他のモジュールが
import しています。

`_screenshot_bytes` は、`alerts.py`、`crawl_guide.py`、`enrich.py` から import されています。
`_describe_step` と `_settle_step` は、`claude_enrich_agent.py` から import されています。
`_clear_blocking` と `_execute` は、`enrich.py` から import されています。

同じパターンは、隣のモジュールでも繰り返されます。`alerts.py` の `_png_size` と `_fraction` は、
`crawl_tabs.py:36` から import されています。先頭のアンダースコアは、本来「モジュール外から import しない」
という合図です。実際には、これらの関数のすべてに、モジュール外の呼び出し元が少なくとも 1 つあります。
命名が、モジュールの公開範囲を正しく表していません。

**ここまでの内容は、いずれも決定論的なゲートに触れません。** 挙げた対象はすべて、AI による作成と調査の
パスの内側にあります。`record`、`enrich`、`triage`、アラート閉じ、crawl のガイドとタブ位置特定です。
7 つの `Claude*` クラス、プロンプトのフラグメント、要素レンダラーのいずれにも、`run` や CI の判定パスから
は到達できません。したがって本項目は、prime directive 1 を脅かしません。pass/fail の判定内容や判定方法は
変わらず、内部のタクソノミーを整理するだけです。

## 詳細設計

作業全体を通して、挙動は変わりません。作業はリネーム、1 件の統合、重複したコードの共有の置き場への
引き上げだけです。CLI の挙動、シナリオのスキーマ、`run` / CI の結果には影響しません。作業は、対象とする
サーフェスごとに MECE な 6 つの単位に分かれます。

1. **`anthropic_client.py` を、実際の中身に合う名前へリネームします。** プロバイダ非依存の設定解決
   （`resolve_model`、`resolve_effort`、`resolve_language`、`language_instruction`、`AiConfig` の
   再エクスポート）を、単一ベンダーを連想させない名前へ移します。たとえば `ai_config.py` にするか、
   `bajutsu/ai/` パッケージの内側に `registry.py` や `claude_code.py` と並べて畳み込みます。どちらにするかは、
   実装者が import のグラフを見たうえで、読みやすいほうを選びます。Anthropic の SDK に固有な部分は、
   ベンダー名を冠したモジュールに残します。この部分の分割は実態と合っているからです。該当するのは、
   `make_client` のようなクライアント生成と、`ant` コマンドラインインターフェース（CLI）の
   サブプロセス呼び出しです。
2. **2 組の `credential_gap` / プロバイダ解決を、`bajutsu/ai/` 内の 1 つの解決処理へ統合します。**
   `anthropic_client.provider` と `ai/registry._provider_name` / `resolved_provider` を、単一の
   プロバイダ解決関数にまとめます。同様に、`anthropic_client.credential_gap` と
   `ai/registry.credential_gap` を、内部でプロバイダごとにディスパッチする単一の認証情報チェックに
   まとめます。他の 2 つが統合された後に `ai_availability.availability` の存在意義が残らないなら、
   これも含めます。`claude-code` に対するディスパッチの挙動（`ai/claude_code.py` への委譲）はそのまま保ち、
   公開される入り口の数だけを減らします。
3. **共有の `ClaudeBackedAgent` 基底クラスを導入します。** 基底クラスには、共通の属性（`_ai` /
   `_redactor` / `_backend` / `_model` / `_lang`）、共有の `_ensure_backend()`、`_record_usage(response)`
   ヘルパーを持たせます。7 つのクラス（`ClaudeAgent`、`ClaudeTriageAgent`、`ClaudeCrossRunTriageAgent`、
   `ClaudeEnrichmentAgent`、`ClaudeActionProposer`、`ClaudeTabLocator`、`ClaudeAlertLocator`）は、この基底を
   継承します。すると各クラスは、同じ 3 行の `_ensure_backend` と、繰り返される `usage.record(...)` の
   呼び出しを再実装しなくなります。この基底クラスは、BE-0104 が包む対象の形を変える前に、
   `anthropic_client.py` のデッドな `ensure_client` が本来なるはずだった共有の置き場です。デッドラッパー
   自体の除去は、別の提案が担います。そのため本項目では、`ensure_client` をその場で移行せず、
   新しくきれいな基底から始めます。
4. **`agent.py` / `agents.py` の単数形と複数形の罠を取り除くようリネームします。** 一つの選択肢は、
   ひと目で見分けがつく名前へのリネームです。プロトコルと DTO の定義を `agent_protocols.py` へ、
   44 行のファクトリを `agent_factory.py` へ移します。もう一つの選択肢は、サイズを踏まえて、ファクトリを
   `agent.py` へそのまま統合することです。どちらにするかは、実装者が呼び出し元を見渡したうえで、
   import の見通しがよいほうを選びます。
5. **`bajutsu/ai/prompts.py` を新設します。** system プロンプトの間で手作業でコピーされている共有の
   指示フラグメントを、ここに集約します。
   共有の言い回しをまとめた `ADDRESSING_RULES` のような定数と、プライムディレクティブの境界線用の
   `NEVER_JUDGE_BOUNDARY` という定数です。

   8 つの system プロンプトは、それぞれこれらの定数を組み込み、再入力しません。あわせて
   `render_elements(elements, *, compact)` を 1 つ追加し、現在 5 か所の呼び出し元（`claude_agent.py`、
   `claude_enrich_agent.py`、`claude_triage.py` の 2 か所、`crawl_guide.py`）が使うようにします。
   現状で実際に意味を持つフォーマットの違い（compact か verbose か）だけをパラメータ化します。意味の
   ない違い（クォートの有無、フィールドの順序）は統一します。この一般化の前例が `_TARGET_PROPS`
   フラグメントです。`claude_agent.py:173` にすでに集約され、`claude_enrich_agent.py` から import されています。
6. **`record.py` / `alerts.py` の、実際にモジュールをまたいで使われている private なヘルパーを、公開
   モジュールへ格上げします。** `_screenshot_bytes`、`_png_size`、`_fraction` の置き場には、
   `screenshots.py` や `ai/vision.py` のような名前が候補です。すでにモジュール外の呼び出し元がある関数
   （`_screenshot_bytes`、`_describe_step`、`_settle_step`、`_clear_blocking`、`_execute`、`_png_size`、
   `_fraction`）から、先頭のアンダースコアを外します。モジュールをまたぐ import（`alerts.py`、
   `crawl_guide.py`、`enrich.py`、`claude_enrich_agent.py`、`crawl_tabs.py`）は、すべて新しい置き場へ更新
   します。調べた結果、自分のモジュールの外に呼び出し元がないとわかったヘルパーは、private のまま残し、
   移動の対象から外します。

それぞれの単位は独立に出荷でき、独立にテストできます。対象となる各クラスの既存テストが、リネームや
引き上げが挙動を変えていないことを確かめる回帰ネットになります。

`tests/test_claude_agent.py`、`tests/test_claude_triage.py`、`tests/test_claude_enrich_agent.py`、
`tests/test_crawl_guide.py`、`tests/test_crawl_tabs.py`、`tests/test_alerts.py`、`tests/test_record.py`
です。

ここでの変更は、クラスの振る舞いではなく、コードの置き場と名前だけだからです。

## 検討した代替案

- **解決処理をいまの場所に残し、層構成をより丁寧に文書化するだけにとどめる。** 却下します。
  現在の docstring は、`credential_gap` とプロバイダ解決が 3 つに分かれている理由を正当化するために、
  すでに力を尽くしています。その労力自体が、分割が自明ではなく紛らわしいという合図です。説明を書き足しても、
  説明を要らなくする名前や置き場の代わりにはなりません。
- **`anthropic_client.py` という名前をそのまま残し、モジュール docstring で、共有の設定ハブであることを
  説明するだけにとどめる。** 同じ理由で却下します。プロバイダ非依存のモジュールにベンダー名が付いていると、
  読み手がどの docstring を読むよりも前の、最初に触れた瞬間に誤解を生みます。必要なのは説明の追記では
  なく、名前か置き場の変更です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `anthropic_client.py` を、プロバイダ非依存の設定解決を表す名前へリネームし、純粋に
      Anthropic の SDK に固有なコードはベンダー名を冠したモジュールに残す。設定解決は新設の
      トップレベル `ai_config.py` へ移し、`anthropic_client.py` には Anthropic SDK クライアントの
      生成と `ant` CLI のトークン入出力だけを残しました
- [x] 2 組の `credential_gap` / プロバイダ解決（`anthropic_client.py` と `ai/registry.py`、
      および `ai_availability.availability` ラッパー）を `bajutsu/ai/` 内の 1 つの解決処理へ
      統合する
- [x] 7 つの `Claude*` クラスの `_ensure_backend` / 使用量記録の定型処理のために、共有の
      `ClaudeBackedAgent` 基底クラスを導入する — 新設の `bajutsu/claude_backed_agent.py` が
      `_backend` / `_ai` / `_redactor` / `_model` 属性、まったく同一だった `_ensure_backend`、
      `_record_usage(response, category)` ヘルパーを持ち、7 クラスがこれを継承します
- [x] `agent.py` / `agents.py`（またはファクトリを `agent.py` へ統合）をリネームし、
      単数形・複数形の見分けにくさという罠を取り除く。`agent.py` → `agent_protocols.py`（プロトコルと
      DTO）、`agents.py` → `agent_factory.py`（構築ファクトリ）へ分けました
- [x] `bajutsu/ai/prompts.py` を新設し、プライムディレクティブの境界線をはじめとする共有の
      プロンプトフラグメントと、5 か所の要素ツリーレンダラー呼び出しのための共有
      `render_elements` ヘルパーを集約する — 新設した `bajutsu/ai/prompts.py` に
      `NEVER_JUDGE_BOUNDARY`（triage / crawl の 4 つの system プロンプトへ合成）と
      `render_elements(elements, *, compact)`（5 か所の呼び出し元が共有）を置きました。設計で
      挙げていた `ADDRESSING_RULES` フラグメントは実際には `crawl_guide` の 1 か所にしか
      現れなかったため、共有定数として新設せずそのまま残しました
- [x] `record.py` / `alerts.py` のモジュールをまたいで使われている private なヘルパー
      （`_screenshot_bytes`、`_describe_step`、`_settle_step`、`_clear_blocking`、
      `_execute`、`_png_size`、`_fraction`）を公開モジュールへ格上げする。画像系の 3 つは
      新設した `bajutsu/screenshots.py`（`screenshot_bytes`、`png_size`、`fraction`）へ移し、
      記録フローの 4 つは `record.py` に残したまま先頭のアンダースコアを外しました
      （`describe_step`、`settle_step`、`execute`、`clear_blocking`）

**ログ**

- [#1012](https://github.com/bajutsu-e2e/bajutsu/pull/1012) — Unit 4: `bajutsu/agent.py` を `bajutsu/agent_protocols.py`（`Agent` /
  `EnrichmentAgent` プロトコルと `Observation` / `Proposal` の DTO）へ、`bajutsu/agents.py` を
  `bajutsu/agent_factory.py`（`make_agent` / `make_enrichment_agent` の構築ファクトリ）へ
  リネームしました。すべての import 元、docstring lint と E2E 関連度の許可リスト、architecture /
  recording ドキュメントも更新しています。振る舞いは変えておらず、既存のテストスイートが回帰の
  網です。
- [#1030](https://github.com/bajutsu-e2e/bajutsu/pull/1030) — Unit 1 + 2（#1012 マージ後に `main` へ rebase）: プロバイダ非依存の設定解決を
  `anthropic_client.py` から新設のトップレベル `ai_config.py`（`resolve_model` / `resolve_effort` /
  `resolve_language` / `language_instruction` / `resolve_provider`、共有の環境変数定数、`AiConfig`
  の再エクスポート）へ切り出し、`anthropic_client.py` には Anthropic SDK クライアントの生成
  （`make_client`）と `ant` CLI のトークン入出力だけを残しました。重複していたプロバイダ解決を
  `ai_config.resolve_provider` 1 つへまとめ（`ai/registry` が再利用）、Anthropic 系の
  `credential_gap` を `bajutsu/ai/anthropic` アダプタへ移し、冗長な `ai_availability.availability`
  の受け渡しラッパーを削除して `bajutsu.ai.credential_gap` を唯一の入口にしました。振る舞いは
  変えておらず、既存のテストスイートが回帰の網です。
- [#1034](https://github.com/bajutsu-e2e/bajutsu/pull/1034) — Unit 3: `bajutsu/claude_backed_agent.py` を新設し、`ClaudeBackedAgent` 基底
  （`_backend` / `_ai` / `_redactor` / `_model` 属性、まったく同一だった `_ensure_backend`、
  `_record_usage(response, category)` ヘルパー）を置きました。7 つの `Claude*` クラス
  （`ClaudeAgent`、`ClaudeTriageAgent`、`ClaudeCrossRunTriageAgent`、`ClaudeEnrichmentAgent`、
  `ClaudeActionProposer`、`ClaudeTabLocator`、`ClaudeAlertLocator`）は、同じ定型処理をそれぞれ
  再実装する代わりにこれを継承し、重複していた `_ensure_backend` と `usage.record` の定型を
  取り除きました。あわせて docstring lint のパス一覧と、決定的コアの独立性を守る import-linter
  契約に新モジュールを追加しています。振る舞いは変えておらず、既存のクラス別テストスイートが
  回帰の網です。
- [#1041](https://github.com/bajutsu-e2e/bajutsu/pull/1041) — Unit 5:
  `bajutsu/ai/prompts.py` を新設し、`NEVER_JUDGE_BOUNDARY`（プライムディレクティブ 1 の境界文。
  手作業でコピーされていた `claude_triage` の 2 つの system プロンプト・`crawl_guide`・`crawl_tabs`
  へ合成）と、`render_elements(elements, *, compact)`（`claude_agent`・`claude_enrich_agent`・
  `claude_triage` ×2・`crawl_guide` が共有する唯一の要素ツリーレンダラー）を置きました。
  フィールドの順序・`repr` クォート・traits の書式は呼び出し元間で統一し、`claude_agent` は
  BE-0194 の大画面キャップだけを自身に残しています。設計が想定していた `ADDRESSING_RULES`
  フラグメントは 1 か所にしか現れなかったため、そのまま残しました。振る舞いは AI 経路の中だけで
  完結し、`run` / CI の判定には一切届きません。既存のモジュール別スイートに加え、新設の
  `tests/ai/test_prompts.py` が回帰の網です。
- [#1056](https://github.com/bajutsu-e2e/bajutsu/pull/1056) — Unit 6（最終。本項目を実装済みへ
  移します）: 名前が公開範囲を正しく表していなかったモジュール横断の private ヘルパーを格上げ
  しました。画像系の 3 つは新設の `bajutsu/screenshots.py` へ移し、`screenshot_bytes`
  （旧 `record._screenshot_bytes`。循環 import を避けるため `MAX_IMAGE_LONG_EDGE` と `_downscaled`
  のまとまりごと持ち込みました）、`png_size` と `fraction`（旧 `alerts._png_size` /
  `._fraction`）を置きました。記録フローの 4 つは `record.py` に残したまま先頭のアンダースコアを
  外しています（`describe_step`、`settle_step`、`execute`、`clear_blocking`）。モジュールを
  またぐ import はすべて新しい公開名へ更新し（`alerts`、`enrich`、`claude_enrich_agent`、
  `crawl_guide`、`crawl_tabs`、`cli/commands/crawl`）、`crawl.py` ではローカルの seam が
  `clear_blocking` という名前を先に持っているため、record のヘルパーを `clear_blocking_overlay`
  という別名で import しています。モジュール外に呼び出し元がないヘルパー（`record._downscaled`
  など）は private のまま残しました。あわせて docstring lint のパス一覧、E2E 関連度の許可リスト、
  周辺層の独立性を守る import-linter 契約、そして recording ドキュメントの両言語版を更新しています。
  振る舞いは AI 経路の中だけで完結し、`run` / CI の判定には一切届きません。既存のモジュール別
  スイートに加え、新設の `tests/test_screenshots.py` が回帰の網です。

## 参考

- `bajutsu/anthropic_client.py:140`（`provider`）、`:236`（`resolve_model`）、`:254`
  （`resolve_effort`）、`:273`（`resolve_language`）、`:291`（`language_instruction`）、
  `:309`（`credential_gap`）、`:223`（いまや呼び出し元のない `ensure_client`）
- `bajutsu/ai/registry.py:22`（`anthropic_client` からの import）、`:105`
  （`_provider_name`）、`:126`（`resolved_provider`）、`:148`（`credential_gap`）
- `bajutsu/ai/claude_code.py:38`（`anthropic_client` から `AiConfig`、`resolve_effort`、
  `resolve_model` を import）、`:388`（自身の `credential_gap`）
- `bajutsu/ai_availability.py:20`（`availability`）
- `bajutsu/agent.py`（プロトコルと DTO）、`bajutsu/agents.py`（44 行のファクトリ）
- `bajutsu/claude_agent.py:632`（`ClaudeAgent._ensure_backend`）、`:173`
  （`_TARGET_PROPS`）、`:416`（インラインの要素レンダラー）
- `bajutsu/claude_triage.py:52` と `:289`（プライムディレクティブの境界線）、`:159` と
  `:391`（`_render` / `_render_evidence`）、`:253`
  （`ClaudeTriageAgent._ensure_backend`）、`:473`
  （`ClaudeCrossRunTriageAgent._ensure_backend`）
- `bajutsu/claude_enrich_agent.py:31`（`claude_agent.py` から `_TARGET_PROPS` を
  import）、`:132`（`_render_elements`）、`:190`
  （`ClaudeEnrichmentAgent._ensure_backend`）
- `bajutsu/crawl_guide.py:186`（プライムディレクティブの境界線）、`:251`
  （`_render_elements`）、`:360`（`ClaudeActionProposer._ensure_backend`）
- `bajutsu/crawl_tabs.py:131`（プライムディレクティブの境界線）、`:192`
  （`ClaudeTabLocator._ensure_backend`）、`:36`（`alerts.py` から `_png_size` /
  `_fraction` を import）
- `bajutsu/alerts.py:178`（`ClaudeAlertLocator._ensure_backend`）、`:135`
  （`_png_size`）、`:142`（`_fraction`）
- `bajutsu/record.py:75`（`_describe_step`）、`:173`（`_screenshot_bytes`）、`:251`
  （`_settle_step`）、`:266`（`_execute`）、`:291`（`_clear_blocking`）
- [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md)
  （ベンダー中立な AI バックエンドインターフェース）を土台としています。7 つのクラスの
  `_ensure_backend` の定型処理は、この提案が導入した `AiBackend` という抽象があるからこそ
  いまの形になっています。また
  [BE-0140](../BE-0140-dedupe-claude-client-init/BE-0140-dedupe-claude-client-init-ja.md)
  （Claude クライアント初期化の重複をなくす）で行ったクライアント初期化の重複解消を、
  BE-0104 が導入した新しい `_ensure_backend` という接点へ一般化するものです
- デッドラッパーとなった `ensure_client` 自体の除去は別の提案が、`bajutsu/ai/` 層の
  境界をより広くパッケージ化する話はさらに別の提案が担う想定です。どちらも本文中では
  名前だけで参照し、BE 番号の割り当てはそれぞれの提案に委ねます

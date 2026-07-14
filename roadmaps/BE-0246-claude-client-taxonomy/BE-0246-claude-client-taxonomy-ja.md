[English](BE-0246-claude-client-taxonomy.md) · **日本語**

# BE-0246 — Claude と話すためのモジュール構成を整理する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0246](BE-0246-claude-client-taxonomy-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装中** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0246") |
| 実装 PR | [#1012](https://github.com/bajutsu-e2e/bajutsu/pull/1012), [#1030](https://github.com/bajutsu-e2e/bajutsu/pull/1030) |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

「モデルにどう到達するか」を決めるコードは、認証情報の欠落チェック、プロバイダの解決、
プロバイダ非依存の設定解決という 3 つの層に分かれ、しかも互いに重なり合っています。
加えて、モジュールが実際に持っている中身を誤解させる命名もいくつか見られます。
どの層も動作自体は正しく、docstring も分割の理由を丁寧に説明しています。しかし、その分割を
docstring 一段落かけて正当化しなければならないこと自体が、本項目が問題にしたい症状です。
一文で済む問いに答えるために、読み手は 3 つのモジュールの関係を頭の中に保持しなければ
なりません。本項目では、リネームと 1 件の統合、そしてこの混乱の周りに積み上がった定型処理の
重複解消からなるタクソノミーの整理を提案します。挙動の変更は伴いません。

## 動機

**「モデルにどう到達するか」に答える層が 3 つあり、しかもそのうち 2 つは独立していません。**
`bajutsu/anthropic_client.py:309` は `credential_gap` を定義しており、
`bajutsu/ai/registry.py:148` はもう一つの `credential_gap` を定義しています。さらに
`bajutsu/ai_availability.py:20` の `availability` が、この両方を包む 3 つ目のラッパーです。
registry 側の実装はチェックを重複させているわけではありません。`api-key`、`bedrock`、`ant`
の各プロバイダに対しては `anthropic_client` の関数へ（`_adapter(ai).credential_gap(ai)` という
形で）ディスパッチし、`claude-code` プロバイダに対しては `ai/claude_code.py` 自身の
`credential_gap` へディスパッチします。プロバイダの解決にも同様に 2 つの実装があります。
`anthropic_client.py:140` の `provider` と、`ai/registry.py:105` の `_provider_name` および
`:126` の `resolved_provider` です。registry 側のチェックの大半は重複ではなく委譲であるため、
この 3 層構成自体は冗長ではありません。しかし、それを読み手が確かめるには 3 つのファイルすべてに
目を通すしかなく、そもそもこの説明が必要になっていること自体が兆候です。解決関数、
ディスパッチャ、そして便利ラッパーは、同じ問いに答えるものである以上、コントリビュータが
手作業で突き合わせて確かめる 3 つのファイルではなく、ひと目でわかる一つの形として読めるべき
です。

**`anthropic_client.py` という名前は実態と合っていません。** ベンダー名を冠していながら、
このモジュールが持っているのは `claude-code` を含むすべてのバックエンドが使う、プロバイダ
非依存の設定解決です。`resolve_model`（236 行目）、`resolve_effort`（254 行目）、
`resolve_language`（273 行目）、`language_instruction`（291 行目）に加えて、`AiConfig` の
再エクスポート（42 行目）もここにあります。`ai/claude_code.py:38` と `ai/registry.py:22` は
どちらもここから import しています。つまり、あらゆるプロバイダと話すために存在するモジュールが、
特定 1 社の SDK の名前を冠しているのです。「モデル・effort・言語の解決はどこにあるか」を
import 文だけで追おうとするコントリビュータには、`anthropic_client` という名前のファイルを
覗く理由がありません。

**`agent.py` と `agents.py` は単数形・複数形の見分けにくさという罠です。** `agent.py` は
`Agent` / `EnrichmentAgent` のプロトコルと DTO（`Observation`、`Proposal`）を持ち、
`agents.py` は 44 行のファクトリ（`make_agent`、`make_enrichment_agent`）です。名前の違いは
1 文字だけで、どのファイル一覧でも隣り合って並ぶため、両方を開くまで区別がつきません。

**7 つの `Claude*` クラスが、同じバックエンドと使用量記録の定型処理を再実装しています。**
`ClaudeAgent`（`bajutsu/claude_agent.py:632`）、`ClaudeTriageAgent` と
`ClaudeCrossRunTriageAgent`（`bajutsu/claude_triage.py:253` と `:473`）、
`ClaudeEnrichmentAgent`（`bajutsu/claude_enrich_agent.py:190`）、`ClaudeActionProposer`
（`bajutsu/crawl_guide.py:360`）、`ClaudeTabLocator`（`bajutsu/crawl_tabs.py:192`）、
`ClaudeAlertLocator`（`bajutsu/alerts.py:178`）は、いずれもバイト単位で同一の
`_ensure_backend` を持っています。

```python
def _ensure_backend(self) -> AiBackend:
    if self._backend is None:
        self._backend = create_backend(ai=self._ai)
    return self._backend
```

これに加えて、ほぼ同一の `__init__`（`self._backend`、`self._ai`、`self._redactor`、
`resolve_model` 経由の `self._model`）と、モデルとのやり取りのたびに呼ばれるほぼ同一の
`usage.record(response.usage, provider=..., model=...)` も繰り返されています。この形は、
BE-0140 がかつて `ensure_client` として一度重複を解消した形と同じです。BE-0104 が生の SDK
クライアントから `AiBackend` という抽象へ移行したことで、その `ensure_client` は役目を
終えました。しかし各クラスは共有の置き換えを導入する代わりに、それぞれ独自の
`_ensure_backend` を静かに書き足していったのです。`anthropic_client.py` に残る
`ensure_client` は、いまや呼び出し元が一つもないデッドコードです。デッドラッパー自体を
取り除くのは別の提案の役目であり、本項目は 7 つのクラスに共有の基底を与え、次に増える
クラスがまた同じパターンを手作業でコピーしなくて済むようにすることが目的です。

**プロンプトの組み立ても分散しています。** 同じファイル群の中に、8 つの system プロンプトが
インラインで存在しています。プライムディレクティブの境界線（モデルに、提案するだけで
pass/fail は決して判断しないと念押しする一文）は、3 つのファイルの 4 箇所に手作業でコピーされて
います。`claude_triage.py:52` と `:289`（「You are advisory only — you never decide pass/fail,
you diagnose and suggest.」）、`crawl_guide.py:186`（「You only choose what to TRY. You never
decide pass/fail and never judge results.」）、`crawl_tabs.py:131`（「You only report where the
tabs are. You never decide pass/fail.」）です。それぞれの文言はわずかに異なっており、これは
まさに、prime directive 1 に対して AI の各パスを誠実に保つための境界線を手作業でコピーする
ことにともなうずれのリスクです。これとは別に、UI のスナップショットをモデルが読めるテキストに
変換する要素ツリーのレンダラーも、5 か所で微妙に異なる形式のまま再実装されています。
`claude_agent.py:416` のインライン実装、`claude_enrich_agent.py:132` の
`_render_elements`、`claude_triage.py:159` の `_render` と `:391` の `_render_evidence`、
`crawl_guide.py:251` の `_render_elements` です。フィールドの順序、クォートの有無、
どの属性を省くかがそれぞれ微妙に異なります。このような共有フラグメントを一箇所にまとめる
前例は、すでにコードベースにあります。`claude_agent.py:173` の `_TARGET_PROPS` という
ツールスキーマのフラグメントは、`claude_enrich_agent.py:31` から再定義ではなく直接
import されています。

**`record.py` の private なヘルパーは、事実上の共有 API になっています。** `record.py` の
`_describe_step`、`_screenshot_bytes`、`_settle_step`、`_execute`、`_clear_blocking` は
いずれも先頭にアンダースコアが付き、「モジュール外から import しない」ことを示す名前です。
しかし実際には、`_screenshot_bytes` は `alerts.py`、`crawl_guide.py`、`enrich.py` から
import されており、`_describe_step` と `_settle_step` は `claude_enrich_agent.py` から、
`_clear_blocking` と `_execute` は `enrich.py` から import されています。同じパターンは
もう一段階先でも繰り返されており、`alerts.py` の `_png_size` と `_fraction` は
`crawl_tabs.py:36` から import されています。先頭のアンダースコアは本来「モジュール外から
import しない」という合図のはずですが、実際にはこれらの関数のすべてに、モジュール外の
呼び出し元が少なくとも 1 つ存在しています。命名がモジュールの公開範囲を正しく表していません。

**ここまでの内容はいずれも決定論的なゲートに触れません。** 挙げた対象はすべて、AI による
作成・調査のパス、すなわち `record`、`enrich`、`triage`、アラート閉じ、crawl のガイドと
タブ位置特定の内側にあります。7 つの `Claude*` クラス、プロンプトのフラグメント、要素
レンダラーのいずれも、`run` や CI の判定パスからは到達できません。したがって本項目は
prime directive 1 を脅かしません。pass/fail の判定内容や判定方法を変えるものではなく、
純粋に内部のタクソノミーを整理するものです。

## 詳細設計

作業全体を通して挙動は変わりません。リネームと 1 件の統合、そして重複したコードを共有の
置き場へ引き上げる作業だけであり、CLI の挙動やシナリオのスキーマ、`run` / CI の結果には
一切影響しません。作業は、対象とするサーフェスごとに MECE な 6 つの単位に分かれます。

1. **`anthropic_client.py` を、実際の中身に合う名前へリネームします。** プロバイダ非依存の
   設定解決（`resolve_model`、`resolve_effort`、`resolve_language`、`language_instruction`、
   `AiConfig` の再エクスポート）を、単一ベンダーを連想させない名前へ移します。たとえば
   `ai_config.py` という名前にするか、`bajutsu/ai/` パッケージの内側に `registry.py` や
   `claude_code.py` と並べて畳み込むかは、実装者が import のグラフを見たうえで読みやすい
   ほうを選べば構いません。純粋に Anthropic の SDK に固有な部分（`make_client` のような
   クライアント生成や、`ant` CLI のサブプロセス呼び出し）は、ベンダー名を冠したモジュールに
   残します。この部分の分割は実態と合っているからです。
2. **2 組の `credential_gap` / プロバイダ解決を、`bajutsu/ai/` 内の 1 つの解決処理へ
   統合します。** `anthropic_client.provider` と `ai/registry._provider_name` /
   `resolved_provider` を単一のプロバイダ解決関数にまとめ、同様に
   `anthropic_client.credential_gap` と `ai/registry.credential_gap`（さらに、他の 2 つが
   統合された後も存在意義が残らないようであれば `ai_availability.availability` も含めて）を、
   内部でプロバイダごとにディスパッチする単一の認証情報チェックにまとめます。`claude-code`
   に対する（`ai/claude_code.py` への委譲という）ディスパッチの挙動はそのまま保ち、
   公開される入り口の数だけを減らします。
3. **共有の `ClaudeBackedAgent` 基底クラスを導入します。** `_ai` / `_redactor` / `_backend` /
   `_model` / `_lang` という共通の属性、共有の `_ensure_backend()`、そして
   `_record_usage(response)` ヘルパーをこの基底クラスに持たせ、7 つのクラス
   （`ClaudeAgent`、`ClaudeTriageAgent`、`ClaudeCrossRunTriageAgent`、
   `ClaudeEnrichmentAgent`、`ClaudeActionProposer`、`ClaudeTabLocator`、
   `ClaudeAlertLocator`）が、それぞれ同じ 3 行の `_ensure_backend` と繰り返される
   `usage.record(...)` の呼び出しを再実装する代わりに、この基底クラスを継承するように
   します。これは、BE-0104 が包む対象の形を変える前に `ensure_client` が本来なるはずだった
   共有の置き場です。デッドラッパー自体の除去は別の提案が担うため、本項目では
   `ensure_client` をその場で移行するのではなく、新しくきれいな基底から始めます。
4. **`agent.py` / `agents.py` の単数形・複数形の罠を取り除くようリネームします。** ひと目で
   見分けがつく名前へリネームするか（たとえばプロトコルと DTO の定義を
   `agent_protocols.py` へ、44 行のファクトリを `agent_factory.py` へ)、あるいはサイズを
   踏まえてファクトリを `agent.py` へそのまま統合するか、実装者が呼び出し元を見渡した
   うえで、より import の見通しがよいほうを選べば構いません。
5. **`bajutsu/ai/prompts.py` を新設します。** system プロンプトの間で手作業でコピーされている
   共有の指示フラグメントをここに集約します。共有の言い回しをまとめた
   `ADDRESSING_RULES` のような定数と、プライムディレクティブの境界線用の
   `NEVER_JUDGE_BOUNDARY` という定数を、8 つの system プロンプトそれぞれに再入力せず
   組み込むようにします。あわせて、現在 5 か所の呼び出し元（`claude_agent.py`、
   `claude_enrich_agent.py`、`claude_triage.py` の 2 か所、`crawl_guide.py`）が使う
   `render_elements(elements, *, compact)` を 1 つ追加し、現状で実際に意味を持つ
   フォーマットの違い（compact か verbose か）だけをパラメータ化して、意味のない違い
   （クォートの有無、フィールドの順序）は統一します。`claude_agent.py:173` の
   `_TARGET_PROPS` フラグメントがすでに一箇所に集約され、`claude_enrich_agent.py` から
   import されているのが、この一般化の前例です。
6. **`record.py` / `alerts.py` の、実際にモジュールをまたいで使われている private な
   ヘルパーを公開モジュールへ格上げします。** `_screenshot_bytes`、`_png_size`、
   `_fraction` であれば `screenshots.py` や `ai/vision.py` のような名前が候補です。すでに
   モジュール外の呼び出し元がある関数（`_screenshot_bytes`、`_describe_step`、
   `_settle_step`、`_clear_blocking`、`_execute`、`_png_size`、`_fraction`）から先頭の
   アンダースコアを外し、モジュールをまたぐ import（`alerts.py`、`crawl_guide.py`、
   `enrich.py`、`claude_enrich_agent.py`、`crawl_tabs.py`）をすべて新しい置き場へ
   更新します。調べた結果、自分のモジュールの外に呼び出し元がないとわかったヘルパーは
   private のまま残し、今回の移動の対象から外します。

それぞれの単位は独立に出荷でき、独立にテストできます。対象となる各クラスの既存テスト
（`tests/test_claude_agent.py`、`tests/test_claude_triage.py`、
`tests/test_claude_enrich_agent.py`、`tests/test_crawl_guide.py`、
`tests/test_crawl_tabs.py`、`tests/test_alerts.py`、`tests/test_record.py`）が、
それぞれのリネームや引き上げが挙動を変えていないことを確かめる回帰ネットになります。
ここでの変更はクラスの振る舞いではなく、コードの置き場と名前だけだからです。

## 検討した代替案

- **解決処理をいまの場所に残し、層構成をより丁寧に文書化するだけにとどめる。** 却下します。
  現在の docstring は、`credential_gap` とプロバイダ解決が 3 つに分かれている理由を
  正当化するためにすでに力を尽くしています。その労力自体が、この分割が自明ではなく
  紛らわしいという合図です。説明を書き足すことは、説明を要らなくする名前や置き場の
  代わりにはなりません。
- **`anthropic_client.py` という名前をそのまま残し、モジュール docstring でこれが共有の
  設定ハブであることを説明するだけにとどめる。** 同じ理由で却下します。プロバイダ非依存の
  モジュールにベンダー名が付いていることは、読み手がどの docstring を読むよりも前の、
  最初に触れた瞬間に誤解を生みます。必要なのは説明の追記ではなく、名前か置き場の変更です。

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
- [ ] `bajutsu/ai/prompts.py` を新設し、プライムディレクティブの境界線をはじめとする共有の
      プロンプトフラグメントと、5 か所の要素ツリーレンダラー呼び出しのための共有
      `render_elements` ヘルパーを集約する
- [ ] `record.py` / `alerts.py` のモジュールをまたいで使われている private なヘルパー
      （`_screenshot_bytes`、`_describe_step`、`_settle_step`、`_clear_blocking`、
      `_execute`、`_png_size`、`_fraction`）を公開モジュールへ格上げする

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
- _PR pending_ — Unit 3: `bajutsu/claude_backed_agent.py` を新設し、`ClaudeBackedAgent` 基底
  （`_backend` / `_ai` / `_redactor` / `_model` 属性、まったく同一だった `_ensure_backend`、
  `_record_usage(response, category)` ヘルパー）を置きました。7 つの `Claude*` クラス
  （`ClaudeAgent`、`ClaudeTriageAgent`、`ClaudeCrossRunTriageAgent`、`ClaudeEnrichmentAgent`、
  `ClaudeActionProposer`、`ClaudeTabLocator`、`ClaudeAlertLocator`）は、同じ定型処理をそれぞれ
  再実装する代わりにこれを継承し、重複していた `_ensure_backend` と `usage.record` の定型を
  取り除きました。あわせて docstring lint のパス一覧と、決定的コアの独立性を守る import-linter
  契約に新モジュールを追加しています。振る舞いは変えておらず、既存のクラス別テストスイートが
  回帰の網です。

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

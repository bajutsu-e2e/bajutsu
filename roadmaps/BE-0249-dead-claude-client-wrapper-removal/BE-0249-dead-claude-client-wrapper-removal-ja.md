[English](BE-0249-dead-claude-client-wrapper-removal.md) · **日本語**

# BE-0249 — バックエンドの seam に取り残された、使われていない Claude クライアントのラッパーを削除する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0249](BE-0249-dead-claude-client-wrapper-removal-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0249") |
| 実装 PR | [#1091](https://github.com/bajutsu-e2e/bajutsu/pull/1091) |
| トピック | コードベース品質・技術的負債 |
| 関連 | [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md), [BE-0140](../BE-0140-dedupe-claude-client-init/BE-0140-dedupe-claude-client-init-ja.md), [BE-0246](../BE-0246-claude-client-taxonomy/BE-0246-claude-client-taxonomy-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`bajutsu/anthropic_client.py` には、いまも `ensure_client` と `CachesClient` プロトコル
（216〜233 行目付近）が定義されています。どちらも、呼び出すプロダクションコードのパスが残っていません。
本項目では、この 2 つと、それを対象にしたテストを削除します。これにより、モジュールの公開面は、AI による作成・調査の各クラスが実際に使っている経路と一致します。

## 動機

BE-0140 は、`ensure_client` を「AI クラスが共有する、遅延生成してキャッシュするラッパー」として追加しました。
当時は 6 つの `Claude*` クラスが、それぞれ `self._ensure_client()` を呼んでいました。このメソッドは、モジュールレベルの `ensure_client(agent)` に処理を委ねます。委ねられた側は、`make_client` 経由で SDK クライアントを生成し、`agent._client` にキャッシュしていました。

その後、BE-0104 がベンダー中立な `AiBackend` の seam（`bajutsu/ai/registry.create_backend`）を導入しました。これらのクラスはすべて、その seam へ移行しています。
いまでは各クラスが、自前の `_ensure_backend` メソッドを持ちます。このメソッドは `create_backend(ai=self._ai)` を呼び、結果を `self._backend` にキャッシュします。
呼び出し箇所は次のとおりです。

- `bajutsu/claude_agent.py:632`
- `bajutsu/claude_triage.py:253` と `:473`
- `bajutsu/claude_enrich_agent.py:190`
- `bajutsu/alerts.py:178`
- `bajutsu/crawl_guide.py:360`
- `bajutsu/crawl_tabs.py:192`

実装は全部で 7 つです。`claude_triage.py` が `ClaudeTriageAgent` と `ClaudeCrossRunTriageAgent` の 2 つを定義しているためです。

`bajutsu/ai/anthropic.py` の `AnthropicBackend` は、`create_backend` の背後で Anthropic プロバイダを担うアダプターです。
このアダプターは、`make_client` を直接呼ぶ private な `_ensure_client` メソッド（`bajutsu/ai/anthropic.py:64`）で、自前のクライアントを構築します。`anthropic_client.py` のモジュールレベル `ensure_client` も、呼んでいません。

つまり、`ensure_client` と `CachesClient` には、プロダクション側の呼び出し元が残っていません。
`ensure_client` の docstring も、いまや事実と異なります。この docstring は、いまも「AI による作成・調査の各クラスが共有するラッパー（BE-0140）」と書いています。しかし、どのクラスも共有していません。すべてのクラスが共有しているのは `create_backend` です。

使われていない仕組みの名前を、現在の実装が採用していない docstring とともに残すのは、単なる無害な放置ではありません。
`Claude*` の各クラスがどうやってモデルにたどり着くのかを grep で調べた読み手は、この関数を見つけて信じ込みます。そして、間違った seam を出発点にしてしまいます。
この関数が生き残っている理由は 1 つだけです。`tests/test_anthropic_client.py`（289〜313 行目付近）が、いまもこの関数を実行しています。守るべきコードが先になくなったのに、テストだけが残りました。

この修正は挙動を変えません。範囲も、AI による作成・調査のパス（`record`、`enrich`、`triage`、アラート閉じ、crawl）の内側に閉じています。
決定的な `run` / CI のジャッジ経路には触れません。したがって、prime directive 1（AI は作成者であり失敗調査者であって、決してジャッジ役にはならない）は、どちらの意味でも影響を受けません。

## 詳細設計

作業は単純な削除です。以下のファイルの外に、ラッパーへの依存が残っていないことを確認する検証ステップを、1 つ添えます。

1. **`ensure_client` を `bajutsu/anthropic_client.py` から削除する**（223〜233 行目付近）。
2. **`CachesClient` プロトコルを `bajutsu/anthropic_client.py` から削除する**（216〜220 行目
   付近）。このプロトコルは、`ensure_client` の `agent` 引数の型付けのためだけに存在しています。
3. **`tests/test_anthropic_client.py` の該当テストを削除する**（289〜313 行目付近）。
   削除の対象は次のとおりです。
   - スタンドインクラス `_CacheHolder`
   - 「ensure_client is the lazy-build-then-cache wrapper…」というコメント
   - `test_ensure_client_returns_injected_client_without_building`
   - `test_ensure_client_builds_once_and_reuses`
4. **削除前にリポジトリ全体を grep して、呼び出し元が残っていないことを確認する。**
   上記 3 つの削除のあとで `rg -n "ensure_client|CachesClient"` を実行すると、この提案自体の記述を除いて一致がないはずです。
   名前が似ていても別物で、変更しないものが 2 つあります。`bajutsu/ai/anthropic.py` の無関係な `_ensure_client` メソッドと、`Claude*` の各クラスの `_ensure_backend` メソッドです。

`ensure_client` や `CachesClient` を import しているファイルは、他にありません。したがって、削除に伴う追加の変更は、`bajutsu/claude_agent.py`、`bajutsu/claude_triage.py`、`bajutsu/claude_enrich_agent.py`、`bajutsu/alerts.py`、`bajutsu/crawl_guide.py`、`bajutsu/crawl_tabs.py`、`bajutsu/ai/anthropic.py` のいずれにも必要ありません。
前の 6 ファイルは、すでに `create_backend` と自前の `_ensure_backend` だけを通じてモデルに到達しています。`AnthropicBackend` は、`make_client` を直接呼んで到達しています。

## 検討した代替案

- **「念のため」残しておく。** 将来 AI クラスが、`AiBackend` の seam ではなく生の SDK クライアントを欲しがるかもしれない、という理由です。
  却下します。docstring が誤ったアーキテクチャを主張している死んだコードは、コードが何もないより悪い状態です。
  見つけたコントリビュータは、まず「使われていない」ことを独力で突き止めなければなりません。それができて初めて、信頼するか無視するかを決められます。
  生のクライアントをキャッシュするヘルパーが将来必要になったとします。そのときに存在する seam に対して、小さく正しい docstring を付けたものを新しく用意するほうが、安く済みます。いまのアーキテクチャについてすでに嘘をついている docstring を抱え続けるより、コストが低いのです。
- **`ensure_client` を、`Claude*` クラス向けの新しい共有基底クラスに作り替える。**
  仮に `ClaudeBackedAgent` 基底クラスと呼べるものです。ここでは名前だけを挙げ、本提案はこの項目に番号を振らず、スコープも定めません。
  本項目では却下します。基底クラスが何を持つか、`Claude*` の各クラスのコンストラクタの違い（`max_tokens` の有無など）をどう吸収するか、という独立した設計の検討が必要になるからです。
  この検討には、固有の提案が値します。ここに含めてしまうと、即日で済むはずの削除が、削除をブロックする設計論議に化けてしまいます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `bajutsu/anthropic_client.py` から `ensure_client` を削除する
- [x] `bajutsu/anthropic_client.py` から `CachesClient` プロトコルを削除する
- [x] `tests/test_anthropic_client.py` の該当テストを削除する
- [x] マージ前にリポジトリ全体を grep して、呼び出し元が残っていないことを確認する

_ログ:_

- [#1091](https://github.com/bajutsu-e2e/bajutsu/pull/1091) — `ensure_client` / `CachesClient`
  とその該当テストを削除し、使われなくなった `Protocol` の import も外しました。あわせて
  `bajutsu/claude_backed_agent.py` の docstring が名指ししていた（もう存在しない）ラッパーへの
  参照を、実態に合わせて書き換えました。

## 参考

- `bajutsu/anthropic_client.py:216`（`CachesClient`）および
  `bajutsu/anthropic_client.py:223`（`ensure_client`）：本項目が削除する使われていないコード
- `tests/test_anthropic_client.py:289`：あわせて削除する対象のテスト
- [BE-0140](../BE-0140-dedupe-claude-client-init/BE-0140-dedupe-claude-client-init-ja.md)
  （Claude クライアント初期化の重複をなくす）：`ensure_client` / `CachesClient` を共有
  ラッパーとして導入した項目。本項目はそれを削除します
- [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md)
  （ベンダー中立な AI バックエンドインターフェース）：それに取って代わった `create_backend`
  の seam
- `bajutsu/ai/anthropic.py:64`（`AnthropicBackend._ensure_client`）：`make_client` を
  直接呼ぶ、無関係かつ今も使われている private メソッドで、本項目の影響を受けません

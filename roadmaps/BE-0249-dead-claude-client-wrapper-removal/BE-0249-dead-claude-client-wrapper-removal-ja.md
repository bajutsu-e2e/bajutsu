[English](BE-0249-dead-claude-client-wrapper-removal.md) · **日本語**

# BE-0249 — バックエンドの seam に取り残された、使われていない Claude クライアントのラッパーを削除する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0249](BE-0249-dead-claude-client-wrapper-removal-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0249") |
| 実装 PR | _pending_ |
| トピック | コードベース品質・技術的負債 |
| 関連 | [BE-0104](../BE-0104-vendor-neutral-ai-backend/BE-0104-vendor-neutral-ai-backend-ja.md), [BE-0140](../BE-0140-dedupe-claude-client-init/BE-0140-dedupe-claude-client-init-ja.md), [BE-0246](../BE-0246-claude-client-taxonomy/BE-0246-claude-client-taxonomy-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`bajutsu/anthropic_client.py` には、いまも `ensure_client` と `CachesClient` プロトコル
（216〜233 行目付近）が定義されています。どちらも使われていません。もはやこれらを呼び出す
プロダクションコードのパスは残っていないのです。本項目ではこの 2 つと、それを覆っている
テストを削除し、モジュールの公開面を「AI による作成・調査の各クラスが実際に使っている経路」
に一致させます。

## 動機

`ensure_client` は BE-0140 が「AI クラスが共有する、遅延生成してキャッシュするラッパー」として
追加したものです。当時は 6 つの `Claude*` クラスがそれぞれ `self._ensure_client()` を呼び、
それがモジュールレベルの `ensure_client(agent)` に処理を委ね、`make_client` 経由で SDK
クライアントを生成して `agent._client` にキャッシュしていました。その後 BE-0104 がベンダー
中立な `AiBackend` の seam（`bajutsu/ai/registry.create_backend`）を導入し、これらのクラスは
すべてそちらへ移行しました。いまでは各クラスが自前の `_ensure_backend` メソッドを持ち、
`create_backend(ai=self._ai)` を呼んで結果を `self._backend` にキャッシュしています。たとえば
`bajutsu/claude_agent.py:632`、`bajutsu/claude_triage.py:253` と `:473`、
`bajutsu/claude_enrich_agent.py:190`、`bajutsu/alerts.py:178`、`bajutsu/crawl_guide.py:360`、
`bajutsu/crawl_tabs.py:192` がそれにあたります（実装は全部で 7 つで、`claude_triage.py` は
`ClaudeTriageAgent` と `ClaudeCrossRunTriageAgent` の 2 つを定義しています）。`create_backend` の背後で Anthropic
プロバイダを担う `bajutsu/ai/anthropic.py` の `AnthropicBackend` も、`make_client` を直接
呼ぶ private な `_ensure_client` メソッド（`bajutsu/ai/anthropic.py:64`）で自前のクライアントを
構築しており、こちらも `anthropic_client.py` のモジュールレベル `ensure_client` は呼んで
いません。

つまり `ensure_client` と `CachesClient` には、もうプロダクション側の呼び出し元が一つも
残っていません。しかも `ensure_client` 自身の docstring は、いまや事実と異なります。
「AI による作成・調査の各クラスが共有するラッパー（BE-0140）」だといまだに書いていますが、
どのクラスも共有していないのです。すべて `create_backend` を共有しています。すでに使われて
いない仕組みの名前を、今の実装が採用していない docstring とともに残しておくことは、単に
無害な放置ではありません。`Claude*` の各クラスがどうやってモデルにたどり着くのかを grep で
調べた読み手は、この関数を見つけて信じ込み、間違った seam を出発点にしてしまいます。これが
生き残っている唯一の理由は、`tests/test_anthropic_client.py`（289〜313 行目付近）がいまだに
これを実行しているからです。守るべきコードが先になくなったのに、テストだけが残った形です。

この修正は挙動を変えません。範囲も AI による作成・調査のパス（`record`、`enrich`、`triage`、
アラート閉じ、crawl）の内側に閉じています。決定的な `run` / CI のジャッジ経路にはいっさい
触れないため、prime directive 1（AI は作成者であり失敗調査者であって、決してジャッジ役には
ならない）はどちらの意味でも影響を受けません。

## 詳細設計

作業は単純な削除であり、他に依存箇所が残っていないことを確認する検証ステップを一つ添えます。

1. **`ensure_client` を `bajutsu/anthropic_client.py` から削除する**（223〜233 行目付近）。
2. **`CachesClient` プロトコルを `bajutsu/anthropic_client.py` から削除する**（216〜220 行目
   付近）。これは `ensure_client` の `agent` 引数の型付けのためだけに存在しています。
3. **`tests/test_anthropic_client.py` の該当テストを削除する**（289〜313 行目付近）。
   スタンドインクラス `_CacheHolder`、「ensure_client is the lazy-build-then-cache wrapper…」
   というコメント、`test_ensure_client_returns_injected_client_without_building`、
   `test_ensure_client_builds_once_and_reuses` が対象です。
4. **削除前にリポジトリ全体を grep して、呼び出し元が残っていないことを確認する。** 上記
   3 つの削除のあとで `rg -n "ensure_client|CachesClient"` を実行すると、この提案自体の記述を
   除いて一致がないはずです（`bajutsu/ai/anthropic.py` の無関係な `_ensure_client` メソッドと、
   `Claude*` の各クラスの `_ensure_backend` メソッドは別名であり、どちらも変更しません）。

`ensure_client` や `CachesClient` を import しているファイルは他にないため、
`bajutsu/claude_agent.py`、`bajutsu/claude_triage.py`、`bajutsu/claude_enrich_agent.py`、
`bajutsu/alerts.py`、`bajutsu/crawl_guide.py`、`bajutsu/crawl_tabs.py`、
`bajutsu/ai/anthropic.py` への追加の変更は必要ありません。これらはすでに `create_backend` /
自前の `_ensure_backend` を通じて、あるいは（`AnthropicBackend` の場合は）`make_client` を
直接呼ぶ形で、もっぱらモデルに到達しています。

## 検討した代替案

- **「念のため」残しておく。** 将来 AI クラスが `AiBackend` の seam ではなく生の SDK
  クライアントを欲しがるかもしれない、という理由です。却下します。docstring が誤った
  アーキテクチャを主張している死んだコードは、コードが何もないより悪い状態です。見つけた
  コントリビュータは、それを信頼していいのか無視していいのか、独力で「使われていない」こと
  を突き止めなければなりません。生のクライアントをキャッシュするヘルパーが将来必要になった
  としても、そのときに存在する seam に対して小さく正しく docstring を書いたものを新しく
  用意するほうが、すでにいまのアーキテクチャについて嘘をついている docstring を持つものを
  抱え続けるより安く済みます。
- **`ensure_client` を、`Claude*` クラス向けの新しい共有基底クラスに作り替える**
  （仮に `ClaudeBackedAgent` 基底クラスと呼べるもので、ここでは名前だけを挙げます。本提案は
  それに番号を振ったり、スコープを定めたりするものではありません）。本項目では却下します。
  それは、基底クラスが何を持つか、`Claude*` の各クラスのコンストラクタの違い（`max_tokens` の有無
  など）をどう吸収するかという、独立した設計の検討に値する仕事です。ここに含めてしまうと、
  即日で済むはずの削除が、それをブロックする設計論議に化けてしまいます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `bajutsu/anthropic_client.py` から `ensure_client` を削除する
- [x] `bajutsu/anthropic_client.py` から `CachesClient` プロトコルを削除する
- [x] `tests/test_anthropic_client.py` の該当テストを削除する
- [x] マージ前にリポジトリ全体を grep して、呼び出し元が残っていないことを確認する

_ログ:_

- _pending_ — `ensure_client` / `CachesClient` とその該当テストを削除し、使われなくなった
  `Protocol` の import も外す。あわせて `bajutsu/claude_backed_agent.py` の docstring が名指し
  していた（もう存在しない）ラッパーへの参照を、実態に合わせて書き換える。

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

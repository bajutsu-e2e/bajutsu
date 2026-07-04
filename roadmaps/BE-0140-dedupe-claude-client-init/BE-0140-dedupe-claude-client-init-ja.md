[English](BE-0140-dedupe-claude-client-init.md) · **日本語**

# BE-0140 — Claude クライアント初期化の重複をなくす

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0140](BE-0140-dedupe-claude-client-init-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0140") |
| 実装 PR | [#613](https://github.com/bajutsu-e2e/bajutsu/pull/613) |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

AI による作成・調査の各パスにまたがる 6 つの `Claude*` クラスは、それぞれが同じ 3 行の
`_ensure_client` メソッドを private に持っています。注入済みのクライアントがあればそれを返し、
なければ新しく作ってキャッシュするという処理です。本項目では、この共通処理をこれらのクラスが
すでに import している場所に一本化し、クライアントの生成やキャッシュの挙動を変える際の
編集箇所を 6 箇所から 1 箇所に減らします。

## 動機

`_ensure_client` は次の 6 箇所でバイト単位まで同一です。`bajutsu/claude_agent.py:331`
（`ClaudeAgent`）、`bajutsu/claude_triage.py:216`（`ClaudeTriageAgent`）、
`bajutsu/claude_enrich_agent.py:187`（`ClaudeEnrichmentAgent`）、`bajutsu/alerts.py:182`
（`ClaudeAlertLocator`）、`bajutsu/crawl_guide.py:352`（`ClaudeActionProposer`）、
`bajutsu/crawl_tabs.py:183`（`ClaudeTabLocator`）です。当初のコードベース分析レポートが数えた
3 箇所の、実は倍にあたります。

```python
def _ensure_client(self) -> Any:
    if self._client is None:
        self._client = make_client(ai=self._ai)
    return self._client
```

この 6 箇所はいずれも `bajutsu/anthropic_client.py:72` の `make_client` をすでに import して
おり、`make_client` 自身も注入された `client` があればそれをそのまま返します（`if client is
not None: return client`）。つまり `_ensure_client` が追加しているのは、生成したクライアントを
`self._client` にメモ化して、呼び出すたびに SDK クライアントを開き直し（API キーの環境変数も
読み直し）ないようにする処理だけです。6 つの `__init__` もそれぞれ、`_ensure_client` が
閉じ込めている `self._client = client; self._ai = ai` という同じ代入を繰り返しています。この
6 つのコピーを結びつけるものが何もないため、クライアント生成やキャッシュの挙動を将来変更する
とき（新しいプロバイダの追加、メモ化のキーの変更、ログの追加など）、コントリビュータは 6 箇所
すべてを手作業で見つけて編集しなければなりません。しかも、今後 7 つ目の AI クラスが追加される
ときも、共有できる置き場があることに気づかないまま、同じパターンをまた手作業でコピーする
可能性が高いでしょう。この修正は AI による作成・調査のパス（`record`、`enrich`、`triage`、
アラート閉じ、crawl）の内側だけで完結します。6 つのクラスはいずれも、決定的な `run` / CI の
ゲートから到達可能ではありません。

## 詳細設計

この修正は挙動を変えません。6 つのクラスはいずれも、現在のコンストラクタのシグネチャ
（`client`、`model`、`ai`、`redactor`、および `max_tokens` のようなクラスごとの追加引数）と、
`_client` を遅延生成してキャッシュするという同じセマンティクスを保ちます。重複は、6 つの
private なコピーの代わりに、6 つのクラスが呼び出せる共有の実装を 1 つ用意することで取り除き
ます。

- **キャッシュ用のラッパーを `make_client` の隣に引き上げる。** `(client, ai)` を受け取り、
  現在 `_ensure_client` が計算しているのと同じ 3 行のメモ化ロジックの結果を返す関数、または
  小さな mixin を 1 つ追加します。置き場は `bajutsu/anthropic_client.py` の `make_client` の
  隣です。この 6 つのクラスがクライアント生成ロジックを import している場所は、すでにそこ
  だからです。プレーンな関数（`self`、あるいは `(client, ai)` のペアを受け取り、代入すべき
  値を返す形）にすれば、互いに無関係な 6 つの `Protocol` 実装に新しい基底クラスを持ち込まずに
  済みます。6 つのクラスのコンストラクタも合わせて共有しやすいとわかれば、mixin がその代替に
  なります（詳しくは「検討した代替案」を参照）。
- **6 つの `_ensure_client` メソッドを、それぞれ共有実装への呼び出しに置き換える。** 各クラスは
  `self._client` という属性を自分で持ち続け（メモ化はインスタンスごとのまま）、「未生成なら
  作る」という判断だけを共有関数に委ねます。クラスごとに 1 行だけ本体を書き換える機械的な
  変更で済みます。6 つの呼び出し元（`claude_agent.py` の `next_action` / `plan`、
  `claude_triage.py` の `triage`、`claude_enrich_agent.py` の `propose_assertions`、
  `alerts.py` の `locate`、`crawl_guide.py` の `propose`、`crawl_tabs.py` の `locate`）は
  影響を受けません。これまでどおり `self._ensure_client()` を呼び出す形を維持するか
  （あるいはその名前のまま 1 行の薄いラッパーとして残すか）は、それぞれの呼び出し箇所で
  読みやすいほうを選べば構いません。
- **共有実装自体にユニットテストを追加します**（クライアント注入によるショートサーキット、
  および一度生成したら再利用するキャッシュの挙動）。そのうえで、各クラスの既存テスト
  （`tests/test_claude_agent.py`、`tests/test_claude_triage.py`、`tests/test_alerts.py`、
  `tests/test_crawl_tabs.py`、および `claude_enrich_agent.py` / `crawl_guide.py` のカバレッジ）
  に、どの呼び出し元の挙動も変わっていないことの確認を任せます。

## 検討した代替案

- **6 つのコピーを現状のまま残す。** 却下します。まさにこの重複をなくすために本項目がある
  うえ、レポートの当初の集計（3 箇所）がすでに実際の総数（6 箇所）を数え漏らしていたという
  事実自体が、新しい AI クラスが追加されるたびにこのコピーが見失われやすいことを示しています。
- **6 つの `Claude*` クラスすべてが継承する共有の基底クラスを導入し**、`_ensure_client` だけで
  なく、`__init__` で繰り返されている代入（`self._client`、`self._ai`、`self._redactor`、
  `resolve_model` 経由の `self._model`）もまとめて引き上げる案。この修正をより大きくした版
  として検討しましたが、本項目のスコープとしては見送ります。6 つのクラスはそれぞれ異なる
  `Protocol`（`TriageAgent`、`EnrichmentAgent`、`AlertLocator`、`ActionProposer`、
  `TabLocator`、および `ClaudeAgent` の背後にある行動提案エージェント）を実装しており、
  コンストラクタの追加引数も異なります（`max_tokens`、`max_actions`、`redactor` の有無）。
  そのため基底クラスは、すべてのサブクラスの必要に応えようとしてオプション引数が増えるか、
  一部しか重複を取り除けないかのどちらかになります。プレーンな共有関数のほうが、構造的に
  無関係な `Protocol` 実装どうしに継承関係を強いることなく組み合わせられます。
- **`_ensure_client` の重複はそのまま残し、新しくバイト単位で同一のコピーが現れたら失敗する
  lint ルール（カスタムの `ruff` チェックや grep ベースのテストなど）を追加する。** flag-mirror
  の項目で検討した同種の代替案と同じ理由で、主な修正としては却下します。チェックは事後的に
  新たな重複を検出できますが、すでにある 6 つのコピーを取り除くことも、それらを手作業で
  同期させ続ける必要をなくすこともできません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `bajutsu/anthropic_client.py` に、クライアント初期化とキャッシュを行う共有実装を追加する
- [x] `_ensure_client` の 6 つのコピー（`claude_agent.py`、`claude_triage.py`、
      `claude_enrich_agent.py`、`alerts.py`、`crawl_guide.py`、`crawl_tabs.py`）を、共有実装
      への呼び出しに置き換える
- [x] 共有実装に対するユニットテスト（注入によるショートサーキット、一度生成したら再利用する
      キャッシュ）を追加し、6 つのコピーが担保していた挙動をカバーする

- 2026-07-03（[#613](https://github.com/bajutsu-e2e/bajutsu/pull/613)）: `make_client` の隣に
  `ensure_client` と構造的 Protocol `CachesClient` を追加し、生成したクライアントをインスタンスに
  メモ化するようにしました。バイト単位で同一だった 6 つの `_ensure_client` の本体を
  `return ensure_client(self)` に置き換え、共有ヘルパーには注入時のショートサーキットと
  「一度だけ生成して再利用する」挙動のテストを追加しました。

## 参考

- `bajutsu/claude_agent.py:331`（`ClaudeAgent._ensure_client`）
- `bajutsu/claude_triage.py:216`（`ClaudeTriageAgent._ensure_client`）
- `bajutsu/claude_enrich_agent.py:187`（`ClaudeEnrichmentAgent._ensure_client`）
- `bajutsu/alerts.py:182`（`ClaudeAlertLocator._ensure_client`）
- `bajutsu/crawl_guide.py:352`（`ClaudeActionProposer._ensure_client`）
- `bajutsu/crawl_tabs.py:183`（`ClaudeTabLocator._ensure_client`）
- `bajutsu/anthropic_client.py:72`（`make_client`） — 6 つのクラスがすでに呼び出しており、
  共有実装の置き場となるファクトリ
- 関連: [BE-0021](../BE-0021-ai-triage/BE-0021-ai-triage-ja.md)（AI トリアージ）、
  [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md)
  （AI データ主権）
- 2026-07-02 のコードベース分析レポート（技術的負債の棚卸し）に由来します。

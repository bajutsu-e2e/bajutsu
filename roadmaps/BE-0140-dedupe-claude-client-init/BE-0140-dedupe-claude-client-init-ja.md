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

AI による作成と調査の各パスにまたがる 6 つの `Claude*` クラスは、それぞれ同じ 3 行の
`_ensure_client` メソッドを private に持っています。このメソッドは、注入済みのクライアントが
あればそれを返し、なければ新しく作ってキャッシュします。本項目では、この共通処理を、これらの
クラスがすでに import しているモジュールに一本化します。クライアントの生成やキャッシュの挙動を
変えるときの編集箇所は、6 箇所から 1 箇所に減ります。

## 動機

`_ensure_client` は、次の 6 箇所でバイト単位まで同一です。

- `bajutsu/claude_agent.py:331`（`ClaudeAgent`）
- `bajutsu/claude_triage.py:216`（`ClaudeTriageAgent`）
- `bajutsu/claude_enrich_agent.py:187`（`ClaudeEnrichmentAgent`）
- `bajutsu/alerts.py:182`（`ClaudeAlertLocator`）
- `bajutsu/crawl_guide.py:352`（`ClaudeActionProposer`）
- `bajutsu/crawl_tabs.py:183`（`ClaudeTabLocator`）

当初のコードベース分析レポートが数えた 3 箇所の、倍にあたります。

```python
def _ensure_client(self) -> Any:
    if self._client is None:
        self._client = make_client(ai=self._ai)
    return self._client
```

6 つのクラスはいずれも、`bajutsu/anthropic_client.py:72` の `make_client` をすでに import
しています。`make_client` 自身も、注入された `client` があればそのまま返します（`if client is
not None: return client`）。したがって、`_ensure_client` が `make_client` に付け加えているのは
1 つだけです。生成したクライアントを `self._client` にメモ化する処理です。メモ化がなければ、
呼び出すたびに SDK クライアントを開き直し、API キーの環境変数も読み直すことになります。

6 つの `__init__` にも、同じ代入 `self._client = client; self._ai = ai` が繰り返されています。
`_ensure_client` は、この代入が作る属性を参照しています。

6 つのコピーを結びつけるものは何もありません。クライアント生成やキャッシュの挙動を将来変える
ときは（新しいプロバイダの追加、メモ化のキーの変更、ログの追加など）、コントリビュータが 6 箇所
すべてを手作業で見つけて編集しなければなりません。7 つ目の AI クラスを追加するときも、共有の
置き場を示すものがないので、同じパターンをまた手作業でコピーする可能性が高いでしょう。

この修正は、AI による作成と調査のパス（`record`、`enrich`、`triage`、アラート閉じ、crawl）の
内側で完結します。6 つのクラスはいずれも、決定的な `run` / CI のゲートから到達できません。

## 詳細設計

この修正は挙動を変えません。6 つのクラスはいずれも、現在のコンストラクタのシグネチャを保ちます
（`client`、`model`、`ai`、`redactor`、および `max_tokens` のようなクラスごとの追加引数）。
`_client` を遅延生成してキャッシュするセマンティクスも同じです。重複は、6 つの private な
コピーの代わりに、6 つのクラスが呼び出す共有実装を 1 つ用意して取り除きます。

- **キャッシュ用のラッパーを `make_client` の隣に引き上げる。** `bajutsu/anthropic_client.py` の
  `make_client` の隣に、関数または小さな mixin を 1 つ追加します。このモジュールは、6 つの
  クラスがクライアント生成ロジックをすでに import している唯一の場所です。追加するものは
  `(client, ai)` を受け取り、現在 `_ensure_client` が計算しているのと同じ 3 行のメモ化ロジックの
  結果を返します。プレーンな関数（`self` または `(client, ai)` のペアを受け取り、代入すべき値を
  返す形）にすれば、互いに無関係な 6 つの `Protocol` 実装に新しい基底クラスを持ち込まずに
  済みます。6 つのクラスのコンストラクタも共有しやすいとわかれば、代替として mixin を使います
  （「検討した代替案」を参照）。
- **6 つの `_ensure_client` メソッドを、それぞれ共有実装への呼び出しに置き換える。** 各クラスは
  `self._client` 属性を自分で持ち続けるので、メモ化はインスタンスごとのままです。「未生成なら
  作る」という判断だけを、共有関数に委ねます。変更は機械的で、クラスごとに本体を 1 行書き換える
  だけです。次の 6 つの呼び出し元は影響を受けません。
  - `claude_agent.py` の `next_action` と `plan`
  - `claude_triage.py` の `triage`
  - `claude_enrich_agent.py` の `propose_assertions`
  - `alerts.py` の `locate`
  - `crawl_guide.py` の `propose`
  - `crawl_tabs.py` の `locate`

  呼び出し元は、これまでどおり `self._ensure_client()` を呼びます。その名前のまま 1 行の薄い
  ラッパーとして残してもかまいません。どちらを選ぶかは、各呼び出し箇所で読みやすいほうで
  決めます。
- **共有実装自体にユニットテストを追加する。** テストでは、クライアント注入によるショート
  サーキットと、一度生成したら再利用するキャッシュの挙動を確かめます。そのうえで、各クラスの
  既存テストが、どの呼び出し元の挙動も変わっていないことを確認します。対象は
  `tests/test_claude_agent.py`、`tests/test_claude_triage.py`、`tests/test_alerts.py`、
  `tests/test_crawl_tabs.py`、および `claude_enrich_agent.py` と `crawl_guide.py` のカバレッジです。

## 検討した代替案

- **6 つのコピーを現状のまま残す。** 却下します。この重複を取り除くために本項目があるので、
  残せば目的に反します。レポートの当初の集計（3 箇所）が実際の総数（6 箇所）を数え漏らして
  いたことも、新しい AI クラスが追加されるたびにコピーを見失いやすいことを示しています。
- **6 つの `Claude*` クラスすべてが継承する共有の基底クラスを導入する。** 基底クラスには、
  `_ensure_client` だけでなく、`__init__` で繰り返されている代入（`self._client`、`self._ai`、
  `self._redactor`、`resolve_model` 経由の `self._model`）も引き上げます。この修正をより大きく
  した版として検討しましたが、本項目のスコープとしては見送ります。6 つのクラスは、それぞれ
  異なる `Protocol` を実装しています（`TriageAgent`、`EnrichmentAgent`、`AlertLocator`、
  `ActionProposer`、`TabLocator`、および `ClaudeAgent` の背後にある行動提案エージェント）。
  コンストラクタの追加引数も異なります（`max_tokens`、`max_actions`、`redactor` の有無）。
  そのため基底クラスは、すべてのサブクラスの必要に応えようとしてオプション引数が増えるか、
  重複の一部しか取り除けないかのどちらかになります。プレーンな共有関数のほうが、構造的に
  無関係な `Protocol` 実装どうしに継承関係を強いることなく組み合わせられます。
- **`_ensure_client` の重複はそのまま残し、バイト単位で同一の新しいコピーが現れたら失敗する lint
  ルールを追加する。** ルールは、カスタムの `ruff` チェックや grep ベースのテストなどです。
  flag-mirror の項目で検討した同種の代替案と同じ理由で、主な修正としては却下します。チェックは
  事後的に新たな重複を検出できます。しかし、すでにある 6 つのコピーを取り除くことも、手作業で
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
- `bajutsu/anthropic_client.py:72`（`make_client`）：6 つのクラスがすでに呼び出しており、
  共有実装の置き場となるファクトリ
- 関連: [BE-0021](../BE-0021-ai-triage/BE-0021-ai-triage-ja.md)（AI トリアージ）、
  [BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty-ja.md)
  （AI データ主権）
- 2026-07-02 のコードベース分析レポート（技術的負債の棚卸し）に由来します。

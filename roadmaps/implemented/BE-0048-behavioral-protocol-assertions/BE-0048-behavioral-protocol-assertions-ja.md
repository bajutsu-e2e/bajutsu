[English](BE-0048-behavioral-protocol-assertions.md) · **日本語**

# BE-0048 — 振る舞い／プロトコルアサーション

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0048](BE-0048-behavioral-protocol-assertions-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| 実装 PR | [#205](https://github.com/bajutsu-e2e/bajutsu/pull/205) · [#208](https://github.com/bajutsu-e2e/bajutsu/pull/208) · [#212](https://github.com/bajutsu-e2e/bajutsu/pull/212) |
| トピック | 競合調査（Maestro）由来の候補 |
| 由来 | Maestro |
<!-- /BE-METADATA -->

## はじめに

画面に映っているものだけでなく、アプリが実際に*何をしたか*を検証します。送信されたアナリティクス
／テレメトリのイベント、アプリが受け取ったレスポンスのスキーマ、アプリが発行したリクエストの順序
と回数です。どの検査も、Bajutsu がすでに捕捉しているネットワーク交信（`network.json`）に対する
純粋で決定的な関数であり、LLM は使いません。したがって Tier-2 の run／CI ゲートに収まります。

## 動機

Maestro をはじめ大半の UI 層 E2E ツールは、見える表面しか検証しません。要素が存在する、ラベルが
ある文言である、スクリーンショットが一致する、といった具合です。しかし要件の大きな部分は、画面に
決して現れない振る舞いに関するものです。`purchase_completed` というアナリティクスイベントが正しい
金額でちょうど 1 回発火したか、一覧を構成するレスポンスが取り決めたスキーマに適合していたか、保護
された呼び出しの前にトークン更新が起きていて、しかも 2 回起きていないか。これらは、実際の契約が壊
れていても、画面上の検査はすべて通過してしまいます。

この点で Bajutsu は際立って有利な位置にあります。すでにアプリ自身のトラフィックを観測しており
（`bajutsu/network.py` + アプリ内コレクタ `BajutsuKit`）、`request` アサーション、
`wait: { until: request }`、決定的な `mocks` を備えています。今日薄いのは、その捕捉データに対する
*アサーションの表面*で、メソッド／URL／ステータス程度しか照合できません。これを深めることは、本
プロジェクトの最も明確な堀です。Maestro は「コード計装ではなく UI 層の自動化」と明言しており、その
創業前提を捨てない限り、アナリティクスのペイロードやレスポンス本文の形状を構造的に検証できません。
ネットワークの真実に対するアサーションを拡張することは、E2E を「画面を操作して見る」から「アプリの
観測可能な契約を検証する」へと組み替えることであり、計装を持たない競合には模倣できない差別化です。

## 詳細設計

これは設計粒度の提案です。以下はすべて**すでに捕捉済み**の交信に対して評価するため、判定はモデルを
介さない機械チェックのままです。

ネットワークのタイムラインを読む、新しいシナリオレベルの `expect` 形式（最終的な名称は採用時に確定）：

```yaml
expect:
  # アナリティクス／テレメトリのイベントが送信された。エンドポイント + 本文フィールドを回数付きで照合。
  - event:
      url: "https://t.example.com/track"
      body: { name: "purchase_completed", amount: "300" }   # 完全一致または ${vars.*} 一致
      count: { equals: 1 }
  # 捕捉したレスポンス本文が JSON Schema に適合する。
  - responseSchema:
      request: { method: GET, urlMatches: ".*/api/items" }
      schema: schemas/items.json        # アプリのスキーマディレクトリからの相対パス
  # リクエストが期待した順序と多重度で発生した。
  - requestSequence:
      - { method: POST, urlMatches: ".*/auth/refresh" }
      - { method: GET,  urlMatches: ".*/api/account" }
```

- **評価。** `bajutsu/assertions.py` 内の、パース済み `network.json` 交信（`request` がすでに読む
  のと同じデータ）に対する純粋関数です。`event` ／ `requestSequence` はフィールドと順序の照合、
  `responseSchema` は捕捉した本文を保存済みの JSON Schema に対して検証します。同じ記録が与えられれば
  決定的で、run／CI の判定は機械のみのままです。
- **redaction（秘匿化）。** ペイロードの照合は `bajutsu/redaction.py` を再利用するため、アサート対象
  の値も書き出される証跡ではマスクされます（アサーションは捕捉した交信を見、レポートは秘匿化された形
  を見ます）。
- **アプリ非依存。** どのエンドポイントがアナリティクスを運ぶか、スキーマファイルがどこにあるかは、
  アプリごとの config（`apps.<name>`）に置きます。アサーション機構はアプリをまたいで同一です。
- **兄弟項目との関係。** これは取得志向のユーティリティステップ
  （[BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps-ja.md)）に対する*観測と
  検証*側の対です。あちらのステップは側方チャネルの値を `${vars.*}` へ*取得*し、こちらのアサーション
  はアプリ自身のトラフィックを*検証*します。また、決定的で非構造的な
  [BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions-ja.md)
  のビジュアル回帰アサーションを補完します。どちらもアクセシビリティツリーでは表現できないものを、
  LLM なしで検査します。

### 実装状況

まず **`event`** アサーションを提供しました（`bajutsu/scenario/models/assertions.py` の `EventMatch`
＋ `CountOp`、`bajutsu/assertions.py` の `_eval_event`）。捕捉済みタイムラインを、イベントのエンドポイント
（既存の `RequestMatch` マッチャを再利用）で絞り、続けて構造化した JSON リクエストボディのフィールドで絞り、
残った通信数を `equals` / `atLeast` / `atMost` 演算子（省略時は 1 件以上）と突き合わせます。ボディの値への
`${vars.*}` / `${secrets.*}` 補間は既存のアサーション置換経路を通るため、フィールドごとの配線は不要でした。

続いて **`requestSequence`** アサーションを提供しました（`Assertion.request_sequence`、
`bajutsu/assertions.py` の `_eval_request_sequence`）。空でない `RequestMatch` のリストを、タイムライン上の
順序を保った部分列として照合します（各マッチャは直前より厳密に後ろの別々の通信に一致し、間に無関係な通信が
挟まってもよい）。`match_request` を再利用し、新依存を加えず、純粋です。役割は順序なので、マッチャ自身の
`count` は無視します。

**`responseSchema`** アサーションで項目を完成させました（`ResponseSchemaMatch`、`bajutsu/assertions.py`
の `_eval_response_schema` ＋ `SchemaContext`）。最初に一致した交信のレスポンスボディを、保存済みの
JSON Schema に対して検証します。スキーマはアプリのスキーマディレクトリ（`--schemas` フラグ、config の
`apps.<name>.schemas`、またはシナリオ脇の `schemas/`）内で解決し、`visual` の baselines とまったく同じ
ように runner を通して配線します。検証には `jsonschema` を使い、opt-in の `schema` extra として遅延
import するので、基本インストールは軽量なまま、extra 未導入時はクリーンに失敗します。

三つとも AI を使わず、他のアサーションと同じく Tier-2 の run/CI ゲートに乗ります。判定にモデルは入りません。

## 検討した代替案

* **ペイロードを LLM に読ませて「正しいことが起きたか」を判定させる。** 不採用。pass/fail ゲートに
  LLM を入れることはプライムディレクティブ #1 に反し、判定を再現不能にします。眼目は、プロトコル
  検査が UI 検査より*さらに*決定的であることです。
* **run の外で外部の契約テストツールにシェルアウトする。** 不採用。アサーションを共有のハブである
  シナリオ YAML の外へ移し、実行された契約がファイルやレポートで見えなくなり、テストを CI ホストへ
  結びつけます。
* **ステータスコードレベルの `request` アサーションだけを維持する（現状）。** スモークには十分です
  が、最も深い差別化を手つかずのままにします。ネットワークデータはすでに捕捉済みなので、それに対して
  アサートする限界費用は小さいです。

## 参考

`bajutsu/network.py`、`bajutsu/assertions.py`、`bajutsu/redaction.py`、
[`BajutsuKit`](../../../BajutsuKit/README.md)、[evidence.md](../../../docs/ja/evidence.md)、
[DESIGN §2 / §6.4](../../../DESIGN.md)

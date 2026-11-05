[English](BE-0048-behavioral-protocol-assertions.md) · **日本語**

# BE-0048 — 振る舞い／プロトコルアサーション

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0048](BE-0048-behavioral-protocol-assertions-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0048") |
| 実装 PR | [#205](https://github.com/bajutsu-e2e/bajutsu/pull/205) · [#208](https://github.com/bajutsu-e2e/bajutsu/pull/208) · [#212](https://github.com/bajutsu-e2e/bajutsu/pull/212) |
| トピック | 検証とカバレッジ |
| 由来 | Maestro |
<!-- /BE-METADATA -->

## はじめに

この項目は、画面に映っているものだけでなく、アプリが実際に*何をしたか*をシナリオで検証できるようにします。検証の対象は 3 つあります。送信されたアナリティクス／テレメトリのイベント、アプリが受け取ったレスポンスのスキーマ、アプリが発行したリクエストの順序と回数です。どの検査も、Bajutsu がすでに捕捉しているネットワーク交信（`network.json`）に対する、純粋で決定的な関数です。LLM は使いません。したがって、Tier-2 の run／CI ゲートに収まります。

## 動機

Maestro をはじめ大半の UI 層 E2E ツールは、見える表面しか検証しません。要素が存在する、ラベルが特定の文言である、スクリーンショットが一致する、といった具合です。しかし、要件の大きな部分は、画面に決して現れない振る舞いに関するものです。たとえば次の 3 つです。

- `purchase_completed` というアナリティクスイベントが、正しい金額でちょうど 1 回発火したかどうかです。
- 一覧を構成するレスポンスが、取り決めたスキーマに適合していたかどうかです。
- 保護された呼び出しの前にトークン更新が起きていて、しかも 2 回起きていないかどうかです。

これらの要件は、実際の契約が壊れていても、画面上の検査をすべて通過してしまいます。

この点で、Bajutsu は際立って有利な位置にあります。すでにアプリ自身のトラフィックを観測しています（`bajutsu/network.py` とアプリ内コレクタ `BajutsuKit`）。さらに、`request` アサーション、`wait: { until: request }`、決定的な `mocks` を備えています。今日薄いのは、その捕捉データに対する*アサーションの表面*です。メソッド／URL／ステータス程度しか照合できません。

この表面を深めることは、本プロジェクトの最も明確な堀です。Maestro は「コード計装ではなく UI 層の自動化」と明言しています。その創業前提を捨てない限り、アナリティクスのペイロードやレスポンス本文の形状を、構造的に検証できません。ネットワークの真実に対するアサーションを拡張すると、E2E は「画面を操作して見る」から「アプリの観測可能な契約を検証する」へと組み替わります。計装を持たない競合には、この差別化を模倣できません。

## 詳細設計

この節は設計粒度の提案です。以下はすべて**すでに捕捉済み**の交信に対して評価するため、判定はモデルを介さない機械チェックのままです。

設計では、シナリオレベルの `expect` 形式を新しく追加します（最終的な名称は採用時に確定）。いずれも、記録したネットワークのタイムラインを読みます。

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

- **評価。** `bajutsu/assertions.py` 内の純粋関数で、パース済み `network.json` 交信に対して評価します。データは `request` がすでに読むものと同じです。`event` ／ `requestSequence` はフィールドと順序を照合します。`responseSchema` は、捕捉した本文を保存済みの JSON Schema に対して検証します。同じ記録が与えられれば結果は決定的で、run／CI の判定は機械のみのままです。
- **redaction（秘匿化）。** ペイロードの照合は `bajutsu/redaction.py` を再利用します。そのため、アサート対象の値も、書き出される証跡ではマスクされます。アサーションは捕捉した交信を見て、レポートは秘匿化された形を見ます。
- **アプリ非依存。** どのエンドポイントがアナリティクスを運ぶか、スキーマファイルがどこにあるかは、アプリごとの config（`apps.<name>`）に置きます。アサーション機構はアプリをまたいで同一です。
- **兄弟項目との関係。** これらのアサーションは、取得志向のユーティリティステップ（[BE-0036](../BE-0036-utility-steps/BE-0036-utility-steps-ja.md)）に対する、*観測と検証*側の対です。あちらのステップは側方チャネルの値を `${vars.*}` へ*取得*し、こちらのアサーションはアプリ自身のトラフィックを*検証*します。また、決定的で非構造的な [BE-0029](../BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions-ja.md) のビジュアル回帰アサーションを補完します。どちらも、アクセシビリティツリーでは表現できないものを、LLM なしで検査します。

## 検討した代替案

* **ペイロードを LLM に読ませて「正しいことが起きたか」を判定させる。** 不採用です。pass/fail ゲートに LLM を入れることはプライムディレクティブ #1 に反し、判定を再現不能にします。眼目は、プロトコル検査が UI 検査より*さらに*決定的であることです。
* **run の外で外部の契約テストツールにシェルアウトする。** 不採用です。共有のハブであるシナリオ YAML の外へアサーションを移すので、実行された契約がファイルやレポートで見えなくなります。テストが CI ホストにも結びつきます。
* **ステータスコードレベルの `request` アサーションだけを維持する（現状）。** スモークには十分です。しかし、最も深い差別化を手つかずのままにします。ネットワークデータはすでに捕捉済みなので、それに対してアサートする限界費用は小さいです。

## 進捗

- [x] `event` アサーション。記録したタイムラインをエンドポイントと JSON のボディ欄で絞り込み、残った件数を `equals` / `atLeast` / `atMost` で検査します。
- [x] `requestSequence` アサーション。タイムライン上の順序付き部分列として照合します（あいだに無関係な通信が挟まってもかまいません）。
- [x] `responseSchema` アサーション。最初に一致した応答を、保存済みの JSON Schema に対して検証します。`jsonschema` エクストラは遅延読み込みです。
- 三つともほかのアサーションと同じく AI を使わず、Tier-2 の run / CI ゲート上にあります。

## 参考

- `bajutsu/network.py`、`bajutsu/assertions.py`、`bajutsu/redaction.py`
- [`BajutsuKit`](../../BajutsuKit/README.md)、[evidence.md](../../docs/ja/evidence.md)
- [DESIGN §2 / §6.4](../../DESIGN.md)

[English](../vision.md) · **日本語**

# 将来構想（north star）

> 将来構想 —— Bajutsu がどこへ向かうかの **形**と、すべての方向が守るべき唯一の制約。本ページは
> 個別の将来構想ページを束ねる戦略的な傘であり、粒度の細かい優先順位付きバックログは
> [roadmap](roadmap/README.md)、今日の設計の *なぜ* は [`DESIGN.md`](../../DESIGN.md) にある。
> ここを読んで **各ピースがどう積み上がるか**を掴み、各計画の詳細はリンク先へ。

関連: [concepts](concepts.md) · [roadmap](roadmap/README.md) · [multi-platform](multi-platform.md) · [cloud-hosting](cloud-hosting.md) · [self-hosting](self-hosting.md)

---

## 不変条件: 何が決して変わらないか

すべての将来方向は **prime directive**（[CLAUDE.md](../../CLAUDE.md) · [concepts](concepts.md) ·
[DESIGN §2](../../DESIGN.md)）に照らして評価する。これらは構想全体が回る固定点:

1. **AI は著者であり失敗時の調査役、決して判定者ではない。** どの将来機能も Tier-2 の `run`/CI
   （継続的インテグレーション）ゲートに LLM（大規模言語モデル）を入れてはならない。合否は常に
   機械チェック可能なまま。
2. **決定性ファースト。** 固定 sleep なし、曖昧なセレクタは即失敗。どの新プラットフォーム・ホスト・
   オーサリングツールもこれを継承する —— 到達範囲や利便性のために譲ることはない。
3. **app-agnostic / backend-agnostic。** アプリ別・プラットフォーム別の差分は config と `Driver` /
   環境の継ぎ目の背後に置き、決定的コアはどこでも同じ。

> どのロードマップ項目も判定基準は単純: *AI をゲートの外に保ち、ゲートを決定的に保つか？* 否なら
> それは **Tier 1（オーサリング）か triage（調査）** —— ゲートの外 —— に属するか、Bajutsu に属さない。

---

## 成長の 3 軸

Bajutsu は 3 つの独立した軸に沿って広がる。これらは合成可能で（どれも他をブロックしない）、
それぞれ具体ページに対応する。

```
                 ▲ REACH（より多くのプラットフォーム / 面）
                 │   Web · Android · Flutter / ハイブリッド
                 │   → multi-platform.md
                 │
   AUTHORING ────┼───────────────▶ SCALE & COLLABORATION
   & MAINTENANCE │                 ホスト / セルフホストのサービス · MCP
   GUI エディタ · │                 → cloud-hosting.md · self-hosting.md
   操作キャプチャ ·│
   ビジュアル回帰 ·
   自己修復 triage
   → roadmap §3, §6, §10
```

### 1. Reach —— より多くのプラットフォームと面

`Driver` / 環境 / id 規約の継ぎ目は、設定だけでなく **差し替え**られるよう作ってある。構想は
**同じ決定的コアが iOS・Android・Web を駆動する**こと。各プラットフォームは自分の actuator + 環境 +
安定 id 規約だけを足す。完全な具体計画 —— セレクタ可搬性の写像、プラットフォーム別バックエンド、
展開順（既存の Linux ゲートで動くので Web を最初に）—— は **[multi-platform](multi-platform.md)** に。
2 つ目の iOS actuator（XCUITest）は 1 つの OS 内での同じ動き（[roadmap → バックエンド拡張](roadmap/README.md#バックエンド拡張ios-actuator)）。

### 2. Scale & Collaboration —— ローカルツールから共有サービスへ

`bajutsu serve` は今日はローカル・単一ユーザのランチャ。構想は **共有サービス**: 安価な Linux
コントロールプレーン（認証・履歴・キュー・レポートビューア）を、高価なデバイスワーカープールから
分離し、チームがブラウザから実行・レビューする。

- **[cloud-hosting](cloud-hosting.md)** —— 公開 / マルチテナント: control-plane ⇄ macOS ワーカープールの
  分離、`subprocess.Popen` → ジョブキューのリファクタ、公開が必須にするセキュリティ堅牢化。
- **[self-hosting](self-hosting.md)** —— 自前 Mac: 今日使える単一 Mac 構成と、完全セルフホストの
  マルチテナント構成。
- **MCP（Model Context Protocol）統合**（[roadmap → 統合・自動化](roadmap/README.md#統合自動化mcp-化)）—— `run`/`doctor`/`record`/`codegen` を MCP ツールとして、
  証跡を MCP リソースとして公開し、エージェントが直接 Bajutsu を駆動。これは Tier-1 の境界に綺麗に乗る:
  エージェントは *著者・調査役*、ゲートは決定的なまま。

### 3. Authoring & Maintenance —— テストを所有するコストを下げる

シナリオは人間が所有するただの YAML。この軸は、ゲートを一切緩めずに *書く・保つ* を安くする。

- **GUI エディタ & 非 AI 操作キャプチャ**（[roadmap → オーサリング体験](roadmap/README.md#オーサリング体験record--gui-エディタ)）—— シナリオを可視編集し、
  スクショ上でセレクタを選び、実操作（tap/type）を LLM なしでシナリオ化。`bajutsu serve` はその第一歩。
- **ビジュアル回帰アサーション**（[roadmap: BE-0029](roadmap/BE-0029-visual-regression-assertions.md)）——
  新しい *決定的*アサーション種別（ベースライン差分）。AI 判定ではなく機械チェックなので、まさに適合する。
- **自己修復 triage**（[roadmap: BE-0021](roadmap/BE-0021-ai-triage.md)）—— 既に出荷済み: AI が
  失敗証跡を読み **最小差分**を提案、人間がレビューして `--write` で適用。ガードレール ——
  *コミット済みテストを自動で緩めない* —— が、これを directive の内側に保つ。

---

## 3 軸すべてで固定されるもの

下表のすべては **共有・決定的・プラットフォーム/ホスト中立** —— Bajutsu が成長しても分岐しない。
これが 3 軸を独立にする: 軸は縁を伸ばすだけで、コアは伸ばさない。

| 固定コア | どこ |
|---|---|
| シナリオ DSL・文法 | [scenarios](scenarios.md) · [dsl-grammar](dsl-grammar.md) |
| セレクタモデルと決定的解決 | [selectors](selectors.md) |
| 機械アサーション（唯一の判定者） | `assertions.py` · [concepts](concepts.md) |
| observe → act → verify オーケストレータ | [run-loop](run-loop.md) |
| 証跡サブシステム（capturePolicy / manifest） | [evidence](evidence.md) |
| レポーター（manifest / JUnit / HTML） | [reporting](reporting.md) |
| 設定の階層（`defaults × apps`） | [configuration](configuration.md) |

新プラットフォームは `Driver` の継ぎ目の背後にバックエンドを足し、新ホスティングは `run` を
*どこで起動するか* を動かす（*何をするか* ではない）、新オーサリングは同じ YAML を生む。コアは定数。

---

## 直近の north star（コミットではなく推奨）

構想に順序を付けるなら、最もレバレッジの高い次の一手 —— それぞれが後段のリスクを最小コストで下げる ——
は:

1. **Playwright による Web**（[multi-platform](multi-platform.md) 段階 1）。コアが本当に
   プラットフォーム中立であることを **既存の Linux ゲートの内側**（[ci](ci.md)）で証明する —— Mac も
   エミュレータも不要 —— と同時に、能力モデルの豊かな端（ネイティブ network/video/意味的操作）を行使する。
2. **MCP サーバ**（[roadmap → 統合・自動化](roadmap/README.md#統合自動化mcp-化)）。表面積が小さく、Tier-1 オーサリングループへの
   レバレッジが大きく、ゲートに触れない。
3. **ビジュアル回帰アサーション**（[roadmap: BE-0029](roadmap/BE-0029-visual-regression-assertions.md)）。競合が
   AI でゲートする決定的な能力 —— directive を緊張させるどころか *強める*差別化要素。

ホスティング軸（[cloud-hosting](cloud-hosting.md) / [self-hosting](self-hosting.md)）はより大きく
分離可能な投資。需要が個人ではなく協働になったときに進める。

> **[roadmap](roadmap/README.md) との関係:** 本ページは *なぜと形*（north star）、ロードマップは
> *優先順位付きの生きたバックログ*（次の具体項目）。ここの項目が着手可能になるとロードマップに優先度・
> 状態付きで現れ、出荷されると [architecture 実装状況](architecture.md#実装状況) へ移る。3 者を同期させる。

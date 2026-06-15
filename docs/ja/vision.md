[English](../vision.md) · **日本語**

# 将来構想

> 将来構想です。Bajutsu がどこへ向かうかの全体的な方向と、すべての方向が守るべき唯一の制約を扱います。本ページは
> 個別の将来構想ページを横断する戦略的な概観で、粒度の細かい優先順位付きバックログは
> [roadmap](../roadmap/README-ja.md)、今日の設計の根拠は [`DESIGN.md`](../../DESIGN.md) にあります。
> ここを読んで各ピースがどう積み上がるかを掴み、各計画の詳細はリンク先を参照してください。

関連: [concepts](concepts.md) · [roadmap](../roadmap/README-ja.md) · [multi-platform](multi-platform.md) · [cloud-hosting](cloud-hosting.md) · [self-hosting](self-hosting.md)

---

## 不変条件: 何が決して変わらないか

すべての将来方向は **prime directive**（[CLAUDE.md](../../CLAUDE.md) · [concepts](concepts.md) ·
[DESIGN §2](../../DESIGN.md)）に照らして評価します。これらは以下のどの方向でも固定されます。

1. **AI は著者であり失敗時の調査役、決して判定者ではありません。** どの将来機能も Tier-2 の `run`/CI
   （継続的インテグレーション）ゲートに LLM（大規模言語モデル）を入れてはなりません。合否は常に
   機械チェック可能なままです。
2. **決定性ファースト。** 固定 sleep なし、曖昧なセレクタは即失敗です。どの新プラットフォーム・ホスト・
   オーサリングツールもこれを継承します。到達範囲や利便性のために譲ることはありません。
3. **app-agnostic / backend-agnostic。** アプリ別・プラットフォーム別の差分は config と `Driver` /
   環境の継ぎ目の背後に置き、決定的コアはどこでも同じです。

> どのロードマップ項目も、判定基準は「AI をゲートの外に保ち、ゲートを決定的に保つか」です。そうでないなら、
> それは **Tier 1（オーサリング）か triage（調査）** というゲートの外に属するか、Bajutsu に属しません。

---

## 成長の 3 軸

Bajutsu は 3 つの独立した軸に沿って広がります。これらは合成可能で（どれも他をブロックしません）、
それぞれ具体ページに対応します。

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

`Driver` / 環境 / id 規約の継ぎ目は、設定だけでなく **差し替え**られるよう作ってあります。目標は
**同じ決定的コアが iOS・Android・Web を駆動する**ことです。各プラットフォームは自分の actuator + 環境 +
安定 id 規約だけを足します。完全な具体計画（セレクタ可搬性の写像、プラットフォーム別バックエンド、
展開順は既存の Linux ゲートで動くので Web を最初に）は **[multi-platform](multi-platform.md)** にあります。
2 つ目の iOS actuator（XCUITest）は、1 つの OS 内での同じ変更です（[roadmap → バックエンド拡張](../roadmap/README-ja.md#バックエンド拡張ios-actuator)）。

### 2. Scale & Collaboration —— ローカルツールから共有サービスへ

`bajutsu serve` は今日はローカル・単一ユーザのランチャです。目標は **共有サービス**です。安価な Linux
コントロールプレーン（認証・履歴・キュー・レポートビューア）を、高価なデバイスワーカープールから
分離し、チームがブラウザから実行・レビューできるようにします。

- **[cloud-hosting](cloud-hosting.md)** —— 公開 / マルチテナント: control-plane ⇄ macOS ワーカープールの
  分離、`subprocess.Popen` → ジョブキューのリファクタ、公開時に必須となるセキュリティ堅牢化。
- **[self-hosting](self-hosting.md)** —— 自前 Mac: 今日使える単一 Mac 構成と、完全セルフホストの
  マルチテナント構成。
- **MCP（Model Context Protocol）統合**（[roadmap → 統合・自動化](../roadmap/README-ja.md#統合自動化mcp-化)）—— `run`/`doctor`/`record`/`codegen` を MCP ツールとして、
  証跡を MCP リソースとして公開し、エージェントが直接 Bajutsu を駆動します。これは Tier-1 の境界の内側に収まります。
  エージェントは著者・調査役であり、ゲートは決定的なままです。

### 3. Authoring & Maintenance —— テストを所有するコストを下げる

シナリオは人間が所有するただの YAML です。この軸は、ゲートを一切緩めずに、書く・保つコストを下げます。

- **GUI エディタ & 非 AI 操作キャプチャ**（[roadmap → オーサリング体験](../roadmap/README-ja.md#オーサリング体験record--gui-エディタ)）—— シナリオを可視編集し、
  スクショ上でセレクタを選び、実操作（tap/type）を LLM なしでシナリオ化します。`bajutsu serve` はその第一歩です。
- **ビジュアル回帰アサーション**（[roadmap: BE-0029](../roadmap/BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions-ja.md)）——
  新しい決定的アサーション種別（ベースライン差分）です。AI 判定ではなく機械チェックなので、適合します。
- **自己修復 triage**（[roadmap: BE-0021](../roadmap/BE-0021-ai-triage/BE-0021-ai-triage-ja.md)）—— 既に出荷済みです。AI が
  失敗証跡を読み **最小差分**を提案し、人間がレビューして `--write` で適用します。ガードレール
  （コミット済みテストを自動で緩めない）が、これを directive の内側に保ちます。

---

## 3 軸すべてで固定されるもの

下表のすべては **共有・決定的・プラットフォーム/ホスト中立**で、Bajutsu が成長しても分岐しません。
これが 3 軸を独立にします。軸は縁を伸ばすだけで、コアは伸ばしません。

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
どこで起動するかを変えます（何をするかは変えません）。新オーサリングは同じ YAML を生みます。コアは一定です。

---

## 直近の推奨順序（コミットではなく推奨）

構想に順序を付けるなら、最もレバレッジの高い次の一手（それぞれが後段のリスクを低コストで下げます）
は次のとおりです。

1. **Playwright による Web**（[multi-platform](multi-platform.md) 段階 1）。コアが
   プラットフォーム中立であることを **既存の Linux ゲートの内側**（[ci](ci.md)）で示せます。Mac も
   エミュレータも不要で、同時に、能力モデルの豊かな端（ネイティブ network/video/意味的操作）を行使します。
2. **MCP サーバ**（[roadmap → 統合・自動化](../roadmap/README-ja.md#統合自動化mcp-化)）。表面積が小さく、Tier-1 オーサリングループへの
   レバレッジが大きく、ゲートに触れません。
3. **ビジュアル回帰アサーション**（[roadmap: BE-0029](../roadmap/BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions-ja.md)）。競合が
   AI でゲートする決定的な能力で、directive を緊張させるどころか強める差別化要素です。

ホスティング軸（[cloud-hosting](cloud-hosting.md) / [self-hosting](self-hosting.md)）はより大きく
分離可能な投資です。需要が個人ではなく協働になったときに進めます。

> **[roadmap](../roadmap/README-ja.md) との関係:** 本ページは根拠と全体的な方向を扱い、ロードマップは
> 優先順位付きの生きたバックログ（次の具体項目）です。ここの項目が着手可能になるとロードマップに優先度・
> 状態付きで現れ、出荷されると [architecture 実装状況](architecture.md#実装状況) へ移ります。3 者を同期させます。

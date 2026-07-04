[English](../vision.md) · **日本語**

# 将来構想

> 将来を見据えたページです。Bajutsu が向かう全体的な方向と、すべての方向が守るべき唯一の制約を扱います。個別の
> ロードマップ項目を横断する戦略的な概観であり、粒度の細かい優先順位付きバックログは
> [roadmap](../../roadmaps/README-ja.md) に、今日の設計の根拠は [`DESIGN.md`](../../DESIGN.md) にあります。
> ここを読めば各ピースがどう組み合わさるかを掴めます。各計画の詳細はリンク先を参照してください。

関連: [concepts](concepts.md) · [roadmap](../../roadmaps/README-ja.md) · [multi-platform](multi-platform.md) · [roadmap → ホスティング](../../roadmaps/README-ja.md#web-ui-のホスティングクラウド--セルフホスト)

---

## 不変条件: 何が決して変わらないか

すべての将来方向は **prime directive**（[CLAUDE.md](../../CLAUDE.md) · [concepts](concepts.md) ·
[DESIGN §2](../../DESIGN.md)）に照らして評価します。これらは以下のどの方向でも固定されたままです。

1. **AI は著者であり失敗時の調査役であって、決して判定者ではありません。** どの将来機能も、Tier-2 の `run`/CI
   （継続的インテグレーション）ゲートに LLM（大規模言語モデル）を入れてはなりません。合否は常に
   機械チェック可能なままにします。
2. **決定性ファースト。** 固定 sleep は使わず、曖昧なセレクタは即失敗させます。どの新しいプラットフォーム、ホスト、
   オーサリングツールもこれを継承します。到達範囲や利便性のために譲ることはありません。
3. **app-agnostic / backend-agnostic。** アプリ別、プラットフォーム別の差分は config と、`Driver` や
   環境の継ぎ目の背後に置きます。決定的コアはどこでも同じです。

> どのロードマップ項目でも、判定基準は「AI をゲートの外に保ち、ゲートを決定的に保つか」です。そうでないなら、
> その項目は **Tier 1（オーサリング）か triage（調査）** というゲートの外側に属するか、そもそも Bajutsu に属しません。

---

## 成長の 3 軸

Bajutsu は 3 つの独立した軸に沿って広がります。これらは合成可能で（どれも他をブロックしません）、
それぞれが具体的なページに対応します。

```
                 ▲ REACH（より多くのプラットフォーム / 面）
                 │   Web · Android · Flutter / ハイブリッド
                 │   → multi-platform.md
                 │
   AUTHORING ────┼───────────────▶ SCALE & COLLABORATION
   & MAINTENANCE │                 ホスト / セルフホストのサービス · MCP
   GUI エディタ · │                 → roadmap: ホスティング（BE-0015 / BE-0016）
   操作キャプチャ ·│
   ビジュアル回帰 ·
   自己修復 triage
   → roadmap §3, §6, §10
```

### 1. Reach：より多くのプラットフォームと面

`Driver`、環境、id 規約の継ぎ目は、設定で調整できるだけでなく **丸ごと差し替え**られるよう作ってあります。目標は、
**同じ決定的コアが iOS、Android、Web を駆動する**ことです。各プラットフォームは、自分の actuator と環境、
安定 id 規約だけを足します。完全な具体計画は **[multi-platform](multi-platform.md)** にあります。セレクタ可搬性の写像、
プラットフォーム別バックエンド、そして展開順（既存の Linux ゲートの上で動くので Web を最初に）を扱っています。
2 つ目の iOS actuator（XCUITest）は、1 つの OS 内での同じ変更にあたります（[roadmap → バックエンド拡張](../../roadmaps/README-ja.md#バックエンド拡張ios-actuator)）。

### 2. Scale & Collaboration：ローカルツールから共有サービスへ

`bajutsu serve` は、今日のところローカルで動く単一ユーザのランチャです。目標は **共有サービス**にすることです。安価な Linux
コントロールプレーン（認証、履歴、キュー、レポートビューア）を、高価なデバイスワーカープールから
切り離し、チームがブラウザから実行とレビューを行えるようにします。

- **[BE-0015（公開 / クラウドホスティング）](../../roadmaps/in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md)**：公開とマルチテナントを扱います。control-plane ⇄ macOS ワーカープールの
  分離、`subprocess.Popen` からジョブキューへのリファクタ、そして公開時に必須となるセキュリティ堅牢化です。
- **[BE-0016（セルフホスティング）](../../roadmaps/in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)**：自前の Mac を扱います。今日すぐ使える単一 Mac 構成と、完全にセルフホストする
  マルチテナント構成です。
- **MCP（Model Context Protocol）統合**（[roadmap → 統合と自動化](../../roadmaps/README-ja.md#統合と自動化mcp-化)）：`run`/`doctor`/`record`/`codegen` を MCP ツールとして、
  証跡を MCP リソースとして公開し、エージェントが直接 Bajutsu を駆動できるようにします。これは Tier-1 の境界の内側に収まります。
  エージェントは著者と調査役であり、ゲートは決定的なままです。

### 3. Authoring & Maintenance：テストを所有するコストを下げる

シナリオは人間が所有するただの YAML です。この軸は、ゲートを一切緩めずに、書くコストと保つコストを下げます。

- **GUI（graphical user interface）エディタと非 AI 操作キャプチャ**（[roadmap → オーサリング体験](../../roadmaps/README-ja.md#オーサリング体験record--gui-エディタ)）：シナリオを画面上で編集し、
  スクリーンショット上でセレクタを選び、実際の tap や type を LLM なしでシナリオに取り込みます。`bajutsu serve` はその第一歩です。
- **ビジュアル回帰アサーション**（[roadmap: BE-0029](../../roadmaps/BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions-ja.md)）：新しい決定的アサーション種別（ベースライン差分）です。AI が
  判定するのではなく機械でチェックするので、原則に適合します。
- **自己修復 triage**（[roadmap: BE-0021](../../roadmaps/BE-0021-ai-triage/BE-0021-ai-triage-ja.md)）：既に出荷済みです。AI が
  失敗証跡を読んで **最小差分**を提案し、人間がレビューして `--write` で適用します。コミット済みテストを自動で緩めないという
  ガードレールが、これを directive の内側に保ちます。

---

## 3 軸すべてで固定されるもの

下表のすべては **共有され、決定的で、プラットフォームとホストに中立**であり、Bajutsu が成長しても分岐しません。
3 軸が独立でいられるのはこのためです。各軸は縁を伸ばすだけで、コアには手を付けません。

| 固定コア | どこ |
|---|---|
| シナリオ DSL（domain-specific language）と文法 | [scenarios](scenarios.md) · [dsl-grammar](dsl-grammar.md) |
| セレクタモデルと決定的解決 | [selectors](selectors.md) |
| 機械アサーション（唯一の判定者） | `assertions.py` · [concepts](concepts.md) |
| observe → act → verify オーケストレータ | [run-loop](run-loop.md) |
| 証跡サブシステム（capturePolicy / manifest） | [evidence](evidence.md) |
| レポーター（manifest / JUnit / HTML） | [reporting](reporting.md) |
| 設定の階層（`defaults × targets`） | [configuration](configuration.md) |

新しいプラットフォームは `Driver` の継ぎ目の背後にバックエンドを足します。新しいホスティングは `run` を
どこで起動するかを変えるだけで、何をするかは変えません。新しいオーサリングは同じ YAML を生みます。コアは一定のままです。

---

## 直近の推奨順序（コミットではなく推奨）

構想に順序を付けるなら、最もレバレッジの高い次の一手は次のとおりです。いずれも、後段の一手のリスクを低コストで下げます。

1. **Playwright による Web**（[multi-platform](multi-platform.md) 段階 1）。コアが
   プラットフォーム中立であることを、**既存の Linux ゲートの内側**（[ci](ci.md)）で、Mac もエミュレータも使わずに
   示せます。同時に、能力モデルの豊かな側（ネイティブの network、video、意味的操作）を行使します。
2. **MCP サーバ**（[roadmap → 統合と自動化](../../roadmaps/README-ja.md#統合と自動化mcp-化)）。表面積が小さく、Tier-1 のオーサリングループへの
   レバレッジが大きく、しかもゲートに触れません。
3. **ビジュアル回帰アサーション**（[roadmap: BE-0029](../../roadmaps/BE-0029-visual-regression-assertions/BE-0029-visual-regression-assertions-ja.md)）。競合が
   AI でゲートしている決定的な能力であり、directive を緊張させるどころか、むしろ強める差別化要素です。

ホスティング軸（[BE-0015](../../roadmaps/in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting-ja.md) / [BE-0016](../../roadmaps/in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting-ja.md)）は、より大きく、
切り離して進められる投資です。需要が個人のものではなく協働のものになったときに進めます。

> **[roadmap](../../roadmaps/README-ja.md) との関係：** 本ページは根拠と全体的な方向を扱い、ロードマップは
> 優先順位付きの生きたバックログ（次の具体的な項目）を扱います。ここの項目が着手可能になるとロードマップに優先度と
> 状態付きで現れ、出荷されると [architecture 実装状況](architecture.md#実装状況) へ移ります。3 者は同期させます。

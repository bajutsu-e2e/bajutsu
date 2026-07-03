[English](BE-0146-serve-coverage.md) · **日本語**

# BE-0146 — serve Web UI で E2E カバレッジマップを見る

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0146](BE-0146-serve-coverage-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トピック | serve Web UI への CLI 機能の取り込み |
<!-- /BE-METADATA -->

## はじめに

E2E カバレッジマップ（[BE-0050](../../implemented/BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map-ja.md)）を
`serve` Web UI に出します。シナリオスイートがアプリの表面をどれだけ動かしているかを、ブラウザで見せます
（宣言済みの id 名前空間に対するカバー状況、不足の一覧、名前空間から外れた id、そして run の集合があれば
観測されたエンドポイントとアサート済みのエンドポイントの比）。読み取り専用で AI を使わず、ゲートには決して
なりません。

## 動機

[BE-0050](../../implemented/BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map-ja.md) は `bajutsu coverage` を
出荷しています。「自分たちの E2E テストは実際に何をカバーしているのか」に答える、読み取り専用で決定的な
集計です。アプリの宣言済み `idNamespaces` に対する名前空間ごとの id カバレッジ、不足の一覧（どのシナリオも
触れていない宣言済み名前空間）、名前空間から外れた id、そして `--runs` を付ければ観測 vs アサートの
エンドポイント（`bajutsu/coverage.py`）を返します。チームが日常的に問い、UI だけの競合には答えられない
問いです。だがそれは CLI にあり、チームが自分のスイートと run を眺める場所はブラウザです。Replay／履歴
ビューはすでに run を並べ、レポートを埋め込んでいます。そこにカバレッジマップを出せば、「この画面／名前空間
はテストされているか」が、それを導いた run の隣で見えるようになります。

## 詳細設計

Tier 1 の読み取り専用です。UI は既存の集計を起動するだけです。

- **「Coverage」ビュー**を置き、`POST /api/coverage`（`{target, runs?}`）を叩きます。集計を実行し、名前空間
  ごとの id カバレッジ、不足の一覧、名前空間から外れた id、そして run の集合が選ばれていれば、観測 vs
  アサートのエンドポイント次元（それらの run にまたがる `network.json` の和集合）を返します。
- **読み取り専用で決定的、AI を使わない。** どの数字も、宣言済みの名前空間と捕捉したアーティファクトに対する
  決定的な数え上げです。モデルも判断もなく、ゲートにもなりません（チームが情報として CI で数字を追うのは
  自由で、この UI はそれを変えません）。
- **次元をスライスで。** id 名前空間の次元が最初のスライスです（分母が完全に定義済みでディスク上にあります）。
  エンドポイントの次元は run の集合を選んだときに加わります。訪問画面の次元は、crawl が発見する分母
  （[BE-0038](../../in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)）の
  背後で先送りのままです。CLI と同じです。
- **アプリ非依存。** 分母（`idNamespaces`）と run は config と runs ディレクトリから来ます。ハードコードされた
  知識ではありません。

## 検討した代替案

* **カバレッジを CLI 専用のままにする。** 不採用です。カバレッジはレポートのビューであり、スイートと run を
  すでに見直す場所はブラウザです。眺めるためのマップに、端末のテーブルはふさわしくありません。
* **生のアーティファクトからブラウザでカバレッジをその場計算する。** 不採用です。決定的な集計はすでにサーバ側
  にあります。JS で作り直せば、ずれの危険を抱え、厳密な数え上げを二重化してしまいます。
* **UI からカバレッジのしきい値で CI をゲートする。** スコープ外であり、流儀にも反します。カバレッジは情報
  で、チームが自分で CI で追うのはよいものの、UI はそれを合否に変えません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 集計を実行し、名前空間ごとのカバレッジ・不足の一覧・名前空間から外れた id を返す
      `POST /api/coverage`（`{target, runs?}`）エンドポイントを追加する
- [ ] その結果をブラウザに出す「Coverage」ビューを追加する
- [ ] run の集合が選ばれたときに、観測 vs アサートのエンドポイント次元を組み込む

まだ着手した PR はありません。

## 参考

* `bajutsu/coverage.py`、`bajutsu/cli/commands/coverage.py`（ここで露出する集計）。
* [BE-0050 — E2E カバレッジマップ](../../implemented/BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map-ja.md)
  （これがその Web UI 面となる機能）、
  [BE-0038 — 自律クロール探索](../../in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)
  （先送りの訪問画面の分母）、
  [BE-0048 — 振る舞い／プロトコルのアサーション](../../implemented/BE-0048-behavioral-protocol-assertions/BE-0048-behavioral-protocol-assertions-ja.md)
  （エンドポイント次元の「宣言された」側の半分）。
* [BE-0011 — ローカル Web UI（`bajutsu serve`）](../../implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve-ja.md)、
  [BE-0072 — serve Web UI のレスポンシブ対応](../../implemented/BE-0072-responsive-web-ui/BE-0072-responsive-web-ui-ja.md)
  （拡張する UI と、引き継ぐ小さい画面向けレイアウト）。
* [evidence.md](../../../docs/ja/evidence.md)、[configuration.md](../../../docs/ja/configuration.md)
  （マップが集計する、捕捉したアーティファクトと宣言済みの名前空間）。[CLAUDE.md](../../../CLAUDE.md)、
  [DESIGN §2](../../../DESIGN.md)（どの数字も決定的な数え上げで、合否ではありません）。

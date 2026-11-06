[English](BE-0050-e2e-coverage-map.md) · **日本語**

# BE-0050 — E2E カバレッジマップ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0050](BE-0050-e2e-coverage-map-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0050") |
| 実装 PR | [#204](https://github.com/bajutsu-e2e/bajutsu/pull/204), [#213](https://github.com/bajutsu-e2e/bajutsu/pull/213), [#259](https://github.com/bajutsu-e2e/bajutsu/pull/259), [#306](https://github.com/bajutsu-e2e/bajutsu/pull/306), [#309](https://github.com/bajutsu-e2e/bajutsu/pull/309) |
| トピック | 検証とカバレッジ |
| 由来 | Maestro |
<!-- /BE-METADATA -->

## はじめに

この項目は、シナリオスイートがアプリの表面をどれだけ実行しているかを示す、読み取り専用のレポートを追加します。レポートは、どの画面を訪れたか、どの安定 `id` を操作したりアサートしたりしたか、どのネットワークエンドポイントを観測したかを示します。この 3 つを、アプリが宣言した id 名前空間に照らして測ります。レポートは、run の証跡とシナリオの静的解析から導出します。LLM は使わず、pass/fail には影響しません。

## 動機

チームは「私たちの E2E テストは実際に何をカバーしているのか」を日常的に問いますが、UI 系 E2E ツールがそれに答えることはまれです。Maestro にはカバレッジの概念がまったくありません。一方、Bajutsu は素材をすでに持っています。

- 各 run は、要素ツリー、スクリーンショット、`network.json` を捕捉します。
- シナリオは、触れるセレクタを静的に宣言します。兄弟項目の振る舞いアサーションを使えば、アサート対象のエンドポイントも宣言します。
- アプリ config は、安定 id の名前空間（`apps.<name>.idNamespaces`）をすでに宣言しています。これが分母になります。

スイート全体で集計すると、この素材がカバレッジマップになります。マップには、スイートが到達する `id` 名前空間と画面、観測するエンドポイントが載ります。何より重要なのは**ギャップ**も載ることです。ギャップとは、どのシナリオも触れていない宣言済み名前空間と、一度も訪れていない画面を指します。

自律クロール（[BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)）と組み合わせれば、クローラの発見した表面が第二の分母になります。「探索が見つけた画面のうち、スイートはいくつ訪れるか」を測れます。このマップは、テストのギャップを可読にする支援ツールです。UI 専用の競合は、この能力を提供していません。

## 詳細設計

この節は提案粒度で書きます。

- **入力。** 入力は 2 つあります。(1) シナリオの静的パースから、各シナリオが参照するセレクタ、画面（`setup` ／ deeplink 経由）、アサート対象エンドポイントを得ます。(2) run セット全体の証跡から、実際に描画された要素ツリー、実際に到達した画面、`network.json` の交信を得ます。
- **分母。** 分母は、アプリが宣言した `idNamespaces` と、利用可能なら [BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md) のクロールが発見した表面です。設計はアプリ非依存で、分母はハードコードした知識ではなく `apps.<name>` config から得ます。
- **出力。** 出力は HTML ／ JSON のカバレッジレポートです。名前空間ごとのカバレッジ、既知／発見済みに対する訪問済み画面、宣言済みに対する観測済みエンドポイント、そして明示的なギャップ一覧（未テストの名前空間／未訪問の画面）を載せます。レポートは読み取り専用で、判定を変えることはなく、CI ゲートの一部でもありません。ただし、チームが参考情報としてその数値を CI で追跡することは選べます。
- **決定性。** どの数値も、捕捉済み成果物に対する決定的な集計です。モデルも判断の介在もありません。これにより、この機能は Tier-1 ／レポート側にしっかり留まります。

## 検討した代替案

* **ソースレベルのコードカバレッジ計装。** これは別の層です。アプリのコード経路を測り、言語／ビルドに固有で、アプリの計装を要するため、アプリ非依存性を壊します。ここでのカバレッジは、Bajutsu がすでに観測する*テスト可能な UI ／プロトコルの表面*に対して意図的に定義し、アプリのソースに対しては定義しません。
* **シナリオから LLM にカバレッジを推定させる。** 不採用です。非決定的で不要だからです。宣言済み名前空間と捕捉済み証跡に対するカバレッジは、正確な計数です。
* **何もしない（現状）。** 許容できます。しかし、「何がカバーされているか」は未回答のままで、ギャップは不可視のままです。答えるのに必要な証跡と名前空間の宣言は、すでに存在します。

## 進捗

- [x] id 名前空間の次元（静的）。`bajutsu coverage --app`。
- [x] 観測エンドポイントと宣言エンドポイントの対比。`bajutsu coverage --runs`。
- [x] 観測した id を id 名前空間マップへ統合（`--runs`）。
- [x] HTML レポート。`bajutsu coverage --html`（自己完結、JavaScript なし）。
- [x] 到達画面の次元。`bajutsu coverage --crawl <screenmap> --runs`。`crawl.fingerprint` を再利用し、到達した画面と発見した画面を比較可能に保ちます。

## 参考

- `bajutsu/doctor.py`（id 名前空間／規約スコア）
- [BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)
- [evidence.md](../../docs/ja/evidence.md)、[configuration.md](../../docs/ja/configuration.md)
- [DESIGN §2 / §7](../../DESIGN.md)

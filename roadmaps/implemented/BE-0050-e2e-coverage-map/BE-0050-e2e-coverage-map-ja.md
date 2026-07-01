[English](BE-0050-e2e-coverage-map.md) · **日本語**

# BE-0050 — E2E カバレッジマップ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0050](BE-0050-e2e-coverage-map-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| 実装 PR | [#204](https://github.com/bajutsu-e2e/bajutsu/pull/204), [#213](https://github.com/bajutsu-e2e/bajutsu/pull/213), [#259](https://github.com/bajutsu-e2e/bajutsu/pull/259), [#306](https://github.com/bajutsu-e2e/bajutsu/pull/306), [#309](https://github.com/bajutsu-e2e/bajutsu/pull/309) |
| トピック | 競合調査（Maestro）由来の候補 |
| 由来 | Maestro |
<!-- /BE-METADATA -->

## はじめに

シナリオスイートがアプリの表面をどれだけ実行しているかを示す、読み取り専用のレポートです。どの画面
を訪れたか、どの安定 `id` を操作したりアサートしたりしたか、どのネットワークエンドポイントを観測したかを、
アプリが宣言した id 名前空間に照らして測ります。run の証跡とシナリオの静的解析から導出するもので、
LLM は使わず、pass/fail には影響しません。

## 動機

チームは「私たちの E2E テストは実際に何をカバーしているのか」を日常的に問いますが、UI 系 E2E ツール
がそれに答えることはまれで、Maestro にはカバレッジの概念がまったくありません。一方 Bajutsu は素材
をすでに持っています。各 run は要素ツリー、スクリーンショット、`network.json` を捕捉し、シナリオは
触れるセレクタを静的に宣言します（兄弟項目の振る舞いアサーションを使えば、アサート対象のエンド
ポイントも宣言します）。アプリ config はすでに安定 id の名前空間（`apps.<name>.idNamespaces`）を
宣言しており、これが分母になります。

スイート全体で集計すると、これがカバレッジマップになります。スイートが到達する `id` 名前空間と画面
の集合、観測するエンドポイント、そして何より**ギャップ**です。ギャップとは、どのシナリオも触れて
いない宣言済み名前空間、一度も訪れていない画面を指します。自律クロール
（[BE-0038](../../in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)）
と組み合わせれば、クローラが発見した表面が第二の分母になり、「探索が見つけた画面のうち、スイート
はいくつ訪れるか」を測れます。これはテストのギャップを可読にする支援ツールであり、UI 専用の競合が
提供していない能力です。

## 詳細設計

提案粒度です。

- **入力。** (1) シナリオの静的パース。各シナリオが参照するセレクタ、画面（`setup` ／ deeplink
  経由）、アサート対象エンドポイントを得ます。(2) run セット全体の証跡。実際に描画された要素ツリー、
  実際に到達した画面、`network.json` の交信を得ます。
- **分母。** アプリが宣言した `idNamespaces`、および利用可能なら
  [BE-0038](../../in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)
  のクロールが発見した表面です。アプリ非依存であり、分母はハードコードした知識ではなく `apps.<name>`
  config から得ます。
- **出力。** HTML ／ JSON のカバレッジレポートです。名前空間ごとのカバレッジ、既知／発見済みに対する
  訪問済み画面、宣言済みに対する観測済みエンドポイント、そして明示的なギャップ一覧（未テストの名前
  空間／未訪問の画面）を載せます。読み取り専用で、判定を変えることはなく、CI ゲートの一部でもありません
  （チームが参考情報としてその数値を CI で追跡することは選べます）。
- **決定性。** どの数値も、捕捉済み成果物に対する決定的な集計です。モデルも判断の介在もありません。
  これにより本機能は Tier-1 ／レポート側にしっかり留まります。

## 検討した代替案

* **ソースレベルのコードカバレッジ計装。** これは別の層です（アプリのコード経路を測り、言語／ビルド
  に固有で、アプリの計装を要する）。アプリ非依存性を壊します。ここでのカバレッジは、Bajutsu がすでに
  観測する*テスト可能な UI ／プロトコルの表面*に対して意図的に定義し、アプリのソースに対しては定義
  しません。
* **シナリオから LLM にカバレッジを推定させる。** 不採用。非決定的で不要です。宣言済み名前空間と捕捉
  済み証跡に対するカバレッジは、正確な計数です。
* **何もしない（現状）。** 許容できますが、「何がカバーされているか」は未回答のままで、ギャップは不可視
  のままです。答えるのに必要な証跡と名前空間の宣言はすでに存在します。

## 進捗

- [x] id 名前空間の次元（静的）。`bajutsu coverage --app`。
- [x] 観測エンドポイントと宣言エンドポイントの対比。`bajutsu coverage --runs`。
- [x] 観測した id を id 名前空間マップへ統合（`--runs`）。
- [x] HTML レポート。`bajutsu coverage --html`（自己完結、JavaScript なし）。
- [x] 到達画面の次元。`bajutsu coverage --crawl <screenmap> --runs`。`crawl.fingerprint` を再利用し、到達した画面と発見した画面を比較可能に保ちます。

## 参考

`bajutsu/doctor.py`（id 名前空間／規約スコア）、
[BE-0038](../../in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)、
[evidence.md](../../../docs/ja/evidence.md)、[configuration.md](../../../docs/ja/configuration.md)、
[DESIGN §2 / §7](../../../DESIGN.md)

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

### 実装状況

最初のスライスは **id 名前空間の次元**を静的に提供します。分母（宣言済みの `idNamespaces`）がすでに
完全に定まっており、最も価値の高い数値だからです。`bajutsu coverage --app <name>`
（`bajutsu/coverage.py`、`bajutsu/cli/commands/coverage.py`）は、アプリの設定済み `scenarios`
ディレクトリのシナリオをすべて走査し、各シナリオが参照する安定 id を（audit のセレクタ走査を再利用する
`bajutsu.audit.referenced_ids` 経由で）名前空間ごとにまとめ、名前空間ごとのカバレッジ、gap 一覧、
off-namespace な id を報告します。読み取り専用かつ AI 非依存で、gap があっても終了 0 です。`doctor` の
1画面ごとの規約スコアの、スイート単位の従兄弟にあたります。

第二のスライスは **観測 vs 宣言エンドポイント**の次元を追加します。「宣言」側を担う兄弟項目
（[BE-0048](../../implemented/BE-0048-behavioral-protocol-assertions/BE-0048-behavioral-protocol-assertions-ja.md)）が
出荷されたためです。`bajutsu coverage --runs <dir>` は run セット配下のすべての `network.json` の和集合
（マップが取り込む最初の run 証跡）を読み、**観測した**エンドポイント（`METHOD path`）のうち、スイートの
ネットワークアサーション（`request` / `event` / `requestSequence`。`coverage.referenced_requests` で収集し
`assertions.match_request` で照合）がカバーする割合、**未アサートの**観測エンドポイント（未テストの
トラフィック）、どの run でも観測されなかった宣言マッチャを報告します。（`responseSchema` の `request` は
[#212](https://github.com/bajutsu-e2e/bajutsu/pull/212) マージ後に宣言集合へ加わります。）

第三のスライスは、同じ `--runs` フラグの下で **観測 id** を id-namespace マップへ静的解析と並べて
織り込みます。`coverage.observed_id_coverage` は、run が実際に*描画した*安定 id
（run セット配下のステップごとの `elements.json` から各要素の `identifier` を集めたもの。null の id は除く）を集め、
宣言済みの `idNamespaces` ごとにまとめます。静的な `coverage()` を踏襲し、namespace ごとの観測 id、
*どの run でも描画されなかった* namespace、off-namespace な観測 id を報告します。これにより、
シナリオが*書く* id を表す静的な「参照」値に、run が*見せた* id を表す run 証跡側の「観測」値が加わり、
run セットが一度も行使しなかった namespace が見えるようになります。引き続き read-only かつ AI 非依存で、
gap があっても終了 0 です。

第四のスライスは、提案の*出力*が掲げる **HTML レポート**を、既存のテキスト／JSON 出力と並べて提供します。
`bajutsu coverage --html <path>` は `coverage.render_html` と `coverage.html.j2` テンプレートを通じて、
自己完結したページ（CSS は埋め込み、JavaScript も外部アセットも無し。ディスクから直接開けます）を書き出します。
次元ごとにカバレッジバーを描き、gap と off-namespace の一覧を目立たせます。揃っている次元だけを描画し
（静的な id 名前空間マップは常に、エンドポイントと観測 id のマップは `--runs` が供給したときに）、
read-only かつ AI 非依存のままです。このフラグはファイルを書き出すだけで、判定もテキスト／JSON 出力も変えません。

第五のスライスは、*動機*が掲げる **訪問済み画面**の次元を提供します。最後の 1 つで、自律クロール
（[BE-0038](../../in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)）が
クロール発見の分母を供給できるようになったことで実現しました。`bajutsu coverage --crawl <screenmap> --runs <dir>` は、
クロールが発見した画面のうち run セットが到達した割合を測ります。分母は `screenmap.json` のノード、分子は
ステップごとの `elements.json` を*同じ* `crawl.fingerprint` で指紋化したものです。これにより訪問した画面が
発見した画面と突き合わせられます。`coverage.screen_coverage` は到達した割合と、**訪問されなかった**画面
（発見済みだがどの run も触れていない）を報告し、他の次元と同様にテキスト／JSON／HTML 出力へ渡します。
クロールが一度も見つけていない run の指紋は数値を水増しできません。分母は発見した集合だけです。引き続き
read-only かつ AI 非依存で、gap があっても終了 0 です。`crawl.fingerprint` を再利用することで、訪問と発見の
同一性を二つ目のアルゴリズムなしに比較できます。

静的、エンドポイント、観測 id、訪問済み画面のすべての次元が出荷され、提案が掲げた次元はすべて実装済みに
なりました。

## 検討した代替案

* **ソースレベルのコードカバレッジ計装。** これは別の層です（アプリのコード経路を測り、言語／ビルド
  に固有で、アプリの計装を要する）。アプリ非依存性を壊します。ここでのカバレッジは、Bajutsu がすでに
  観測する*テスト可能な UI ／プロトコルの表面*に対して意図的に定義し、アプリのソースに対しては定義
  しません。
* **シナリオから LLM にカバレッジを推定させる。** 不採用。非決定的で不要です。宣言済み名前空間と捕捉
  済み証跡に対するカバレッジは、正確な計数です。
* **何もしない（現状）。** 許容できますが、「何がカバーされているか」は未回答のままで、ギャップは不可視
  のままです。答えるのに必要な証跡と名前空間の宣言はすでに存在します。

## 参考

`bajutsu/doctor.py`（id 名前空間／規約スコア）、
[BE-0038](../../in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration-ja.md)、
[evidence.md](../../../docs/ja/evidence.md)、[configuration.md](../../../docs/ja/configuration.md)、
[DESIGN §2 / §7](../../../DESIGN.md)

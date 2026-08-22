[English](BE-XXXX-cyclomatic-complexity-ceiling.md) · **日本語**

# BE-XXXX — 循環的複雑度に上限を設ける

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-cyclomatic-complexity-ceiling-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Contributor workflow |
<!-- /BE-METADATA -->

## はじめに

`make check` には、1つの関数がどれだけ大きく枝分かれしてよいかを縛るものが何もありません。この
ゲートはスタイルと型とセキュリティを見ていますが、構造は見ていません。本項目は、専用の複雑度
ツールである [Radon](https://pypi.org/project/radon/) やそのしきい値チェッカーである
[Xenon](https://pypi.org/project/xenon/) を新たに導入する代わりに、ruff 自身の仕組みでこの
ギャップを閉じます。閉じる際に使うのは、ruff に組み込まれている `C901`（mccabe の循環的複雑度）と、
ruff がすでに再実装している Pylint の「多すぎる」系ルールの一部（return 文・分岐・文の数それぞれを
数える `PLR0911` / `PLR0912` / `PLR0915`）です。上限は緩い値からはじめ、その後のPRで段階的に締めていきます。これは
[BE-0117](../BE-0117-coverage-floor-ratchet/BE-0117-coverage-floor-ratchet-ja.md) がカバレッジフロア
に対して使った、最悪のケースから手当てしてから数値を締める、という同じ手順です。

## 動機

`bajutsu/` を mccabe 自身のデフォルトの上限（10）で測定すると、55個の関数がこれを超えます。
この分布は平坦でなく急です。上限を20まで上げると、件数は13個に落ちます。そのなかでも2つの関数
だけが残りから大きく突出しています。複雑度99の `_make_handler`
（[bajutsu/serve/handler.py:90](../../bajutsu/serve/handler.py)）と、複雑度54の `make_app`
（[bajutsu/serve/server/app.py:122](../../bajutsu/serve/server/app.py)）です。両方とも、1つの関数
本体のなかで多数のエンドポイントメソッドを定義する HTTP ハンドラのファクトリです。この数値が本体
内の実際の分岐から来ているのか、それぞれのネストしたハンドラ自身の分岐を mccabe がファクトリの
合計へ畳み込んでいるだけなのかは、実装の段階で答えを出すべき問いであり、どちらの関数を書き直すか
除外するかを本項目はあらかじめ決めません。複雑度20を超える残り11個の関数には共通する型がなく、
コードベース全体に散らばっています。`crawl`（46、`bajutsu/crawl/core.py:849`）、`device_pool`
（43、`bajutsu/runner/pool.py:71`）、`_wait`（35、`bajutsu/orchestrator/waits.py:376`）、
`_emit_step`（Playwright・uiautomator・XCUITest それぞれの codegen モジュールで26・23・36）、
`run_one`（32、`bajutsu/runner/pipeline.py:257`）、`record`（30、`bajutsu/record.py:469`）、
`_handle_action`（27、`bajutsu/orchestrator/loop.py:1000`）、`lease`
（23、`bajutsu/runner/pool.py:228`）、`_step_selectors`（21、`bajutsu/analysis/audit.py:94`）です。

Pylint 由来の関数サイズルールが測っているのは、関連はしていますが別の性質です。分岐点の数では
なく、文・分岐・`return` がどれだけ積み重なっているかを測ります。デフォルトのしきい値でも、より多くの
指摘が見つかります。`PLR0911`（return が多すぎる、デフォルトの上限は6）は45件、`PLR0912`（分岐が多す
ぎる、デフォルトの上限は12）は23件、`PLR0915`（文が多すぎる、デフォルトの上限は50）は14件です。
`bajutsu/` の外では、上限25の `C901` はクリーンですが、`PLR0911` と `PLR0912` が `tests/`・
`demos/`・`scripts/` にわたってさらに5件（`PLR0911` 3件、`PLR0912` 2件）を持ち込むため、
`ruff check .` のもとでの合計は82件ではなく87件になります。同じ refactor 層にある他の2つの
Pylint 由来ルール——`PLR0913`（引数が多すぎる、93件）と `PLR2004`
（比較のなかのマジックバリュー、73件）——は、構造ではなく API の表面積とリテラルの使い方を測る
ものであり、本項目の範囲には含めません。詳しくは *検討した代替案* を参照してください。

## 詳細設計

`C901`・`PLR0911`・`PLR0912`・`PLR0915` を `[tool.ruff.lint]` の `select` リストへ加えます。
mccabe のしきい値はデフォルト値のままにせず、明示的に設定します。

1. **`[tool.ruff.lint.mccabe] max-complexity = 25` を設定します。** この上限では、今日測定した
   複雑度上位10個の関数だけが違反します。レビューできる小さな一覧です。実装の段階で、複雑度が
   実際の分岐を反映している関数はリファクタし、そうでない関数には理由を名指しした `# noqa: C901`
   を添えます。上記の `_make_handler` と `make_app` の畳み込みに関する問いも、この振り分けの
   なかで答えを出します。
2. **`PLR0911` / `PLR0912` / `PLR0915` をそれぞれのデフォルトのしきい値（return 6、分岐12、文50）で
   有効化し**、合計87件の指摘を同じように振り分けます。関数を分けるべきだと件数が示している箇所
   は修正し、そうでない箇所には理由付きの `# noqa` を添えます。
3. **手順1の一覧が片付いた段階で、`max-complexity` を段階的に締めます。** 20まで下げると3個、
   15まで下げるとさらに5個、12まで下げるとさらに10個が新たに対象へ入ります。前の上限の一覧が
   完全に解消してから、次の下げ幅をそれぞれ独立した PR として進めます。これは
   [BE-0117](../BE-0117-coverage-floor-ratchet/BE-0117-coverage-floor-ratchet-ja.md) の「まず手当て
   してから、フロアを上げる」という順序を、複雑度上限に対して踏襲するものです。ruff 自身のデフォルト値
   である10は、そのまま押し進める目標ではなく、妥当な着地点として扱います。後の測定でもなお
   コードベースの実際の関数に対して上限が緩いとわかった場合にだけ、その先を見直します。

## 検討した代替案

- **ruff の `C901` の代わりに Radon と Xenon を採用する** — 却下します。ruff はすでにゲートの
  linter であり、`C901` は Xenon のしきい値チェックが Radon の計算をラップしているのと同じ mccabe
  循環的複雑度を測ります。2つ目のツールや `make check` の追加ステップを要しません。これは、
  BE-0067 が単体の Bandit 導入ではなく ruff の `S` ルールを使ったことと整合します。
- **`max-complexity` を ruff のデフォルト値（10）へただちに設定する** — 却下します。それでは55個の
  関数が一度に検出され、そのほとんどは本当に手当てが必要な突出したケースではなく、本項目が設計
  した段階的な振り分けの代わりに拙速な振り分けを強いることになります。
- **`PLR0913`（引数が多すぎる）と `PLR2004`（マジックバリュー比較）を本項目で採用する** — 却下
  します。どちらも構造上の複雑度を測るものではありません。`PLR0913` は関数の引数リストの大きさを
  測り、`PLR2004` は比較の定数が名前付きの値であるべきかを測るもので、これを「複雑度の上限」という
  1つの見出しへ混ぜ込むと、2つの異なる品質の問いを一緒にしてしまいます。どちらも、将来の別項目の
  候補です。
- **ツールの変更に先立って `_make_handler` と `make_app` をただちにリファクタする** — 却下します。
  コードベースの残り全体を同じ上限で測る前にリファクタすると、その2つの関数だけを個別に直す
  ことになり、同種の関数を——現在のものも将来のものも——捉える上限を確立することにはなりません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `max-complexity = 25` で `C901` を追加し、これが検出する10個の関数を振り分ける（リファクタ
  するか、理由付きの `# noqa: C901` を添える）。まず `_make_handler` と `make_app` の畳み込みについての
  問いに答える。
- [ ] `PLR0911`・`PLR0912`・`PLR0915` をデフォルトのしきい値で有効化し、合計87件の指摘を振り分ける。
- [ ] `max-complexity` を20まで締め、新たに対象となる3個の関数を振り分ける。
- [ ] `max-complexity` を15まで締め、新たに対象となる5個の関数を振り分ける。
- [ ] `max-complexity` を12まで締め、新たに対象となる10個の関数を振り分ける。
- [ ] ruff のデフォルト値である10に対して再測定し、さらに締めるか、そこで止めるかを判断する。

## 参考

- [BE-0117 — CLI コマンド層の残りをテストしてから、カバレッジフロアをラチェットする](../BE-0117-coverage-floor-ratchet/BE-0117-coverage-floor-ratchet-ja.md)
  — 「まず最悪のケースを手当てしてから、フロアを締める」という順序を確立し、本項目はこれを
  カバレッジフロアの代わりに複雑度上限へ踏襲しています。
- [BE-0067 — コード品質ゲートの強化](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening-ja.md)
  — ruff 自身の `S` ルールを有効化し、Bandit を別途導入しませんでした。
- [Ruff の mccabe ルール（`C901`）](https://docs.astral.sh/ruff/rules/complex-structure/) と
  [Ruff の Pylint ルール](https://docs.astral.sh/ruff/rules/#pylint-pl) — 本項目が有効化する
  ルールの定義です。
- [Radon](https://pypi.org/project/radon/) と [Xenon](https://pypi.org/project/xenon/) —
  *検討した代替案* で ruff 組み込みの代替と比較した専用ツールです。

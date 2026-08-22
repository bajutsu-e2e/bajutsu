[English](BE-0388-test-suite-mypy-typing.md) · **日本語**

# BE-0388 — テストスイートを mypy で型チェックする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0388](BE-0388-test-suite-mypy-typing-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0388") |
| トピック | Contributor workflow |
<!-- /BE-METADATA -->

## はじめに

`make check` の `typecheck` ステップは、`bajutsu`・`demos`・`scripts` に対して mypy を strict
モードで実行します（[BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening-ja.md)）。
一方で `tests/` は 361 ファイルとリポジトリ最大のソースツリーでありながら、型チェックの対象に
まったく入っていません。本項目は、pytest スイートの慣習に合わせて設定を緩めたうえで、ディレクトリ
単位で段階的に `tests/` を mypy の対象へ組み込みます。BE-0067 が「緩めたモジュール単位の設定による
フォローアップが必要」として見送った課題を、ここで引き受けます。

## 動機

`mypy tests` を試しに実行してみると（まだゲートには含まれていない参考値です）、361 ファイルのうち
159 ファイルにわたって 1361 件のエラーが報告されます。このうち 4 種類のエラーコードが件数の大半を
占めます。値の型が受け取り側の引数と一致しない `arg-type`（522 件）と、mypy から見えない属性への
アクセスを指す `attr-defined`（269 件）が、その大半です。`attr-defined` の多くは、テストがすでに
import 済みのモジュールへ入り込んでパッチや読み取りを行うものです。たとえば
`bajutsu.drivers.base.time.sleep` のような参照は、strict モードの再エクスポートチェックが
モジュール内部の名前とみなします。残る2つは、戻り値の型注釈のない `def test_...():` を指す
`no-untyped-def`（139 件、この慣習は本リポジトリのテストがすでに一貫して採用しています）と、
もはや何も抑制していない `# type: ignore` コメントを指す `unused-ignore`（60 件）です。これら
4 種類のどれも、テスト対象のコードにある欠陥を報告しているわけではありません。
report しているのは、strict モードのデフォルト値と、pytest スイートの書き方（モック、モンキーパッチ、型
注釈のないテスト関数）とのあいだのずれです。

mypy 自身の pydantic プラグイン（`plugins = ["pydantic.mypy"]`、今日の設定では未使用）を有効に
すると、設定を1か所変えるだけの見返りとリスクの両方が見えてきます。有効化すると、`tests/` の
1361 件のうち 46 件が解消します。`XcuitestConfig(test_runner=..., device_type=...)` のような呼び出し
は、`testRunner` / `deviceType` という別名ではなく Python のフィールド名でモデルを構築するものであり、
モデルの `populate_by_name=True` は実行時にこれを受け付けますが、プラグインなしの mypy は受け付け
ません。ところが同じ変更は `bajutsu/` 自体にも新たに 9 件のエラーを持ち込みます。こちらの本体コード
は同種のモデルを別名で構築しており（`OrgConfig(editorTeams=...)`、
`RequestMatch(urlMatches=..., pathMatches=...)`、`SystemAlertHandling(pollInterval=...)`）、プラグインが
合成するコンストラクタはその形をもう受け付けません。1つの設定変更が、あるクラスの偽陽性を別のクラス
の偽陽性と取り替えているだけであり、導入するなら `pyproject.toml` を思いつきで書き換えるのではなく、
影響を受けるすべての呼び出し箇所を洗い出す作業とあわせて行う必要があります。

## 詳細設計

`tests/` を `typecheck` へ組み込む作業を3段階に分け、ゲートが移行の途中で赤くならないよう各段階を
別々の PR として進めます。

1. **`tests.*` にスコープした緩めたオーバーライドを設定します（設定のみです。`tests` を
   `typecheck` の対象へ加えるのは最終ステップで行います）。**
   `disallow_untyped_defs = false` により、素の `def test_x():` はそのまま許容されます。pytest は
   戻り値の型を見ないため、すべてのテストに `-> None` の注釈を付けても安全性は増えません。
   `warn_unused_ignores = false` は、既存の `# type: ignore` コメントを一掃するまでの暫定です。
   見つけたコメントは、見つけたその作業のなかで削除するか、`# type: ignore[<code>]` の形にして
   理由を明記します。後回しにはしません。
2. **モジュールが自身の import へ入り込んでパッチする形の `attr-defined` を、ディレクトリ単位で
   解消します。** 以下に挙げる件数は、それぞれのディレクトリの `attr-defined` の内訳ではなく、
   そのディレクトリの mypy エラー総数です（ベースライン全体の1361件のうち `attr-defined` は
   269件です）。1つのディレクトリを完全に解消してから次のディレクトリへ進むためです。件数の
   少ないディレクトリ、つまり `tests/scenario/` の7件と `tests/report/` の
   17件からはじめます。件数の多いディレクトリは後に回します。`tests/serve/` の228件と、`tests/`
   直下のフラットなファイル群の902件です。後者のなかでは `test_crawl.py` の200件と
   `test_record.py` の75件が多くを占めます。

   各修正は、テストが本当に必要としている呼び出しにパッチを当てます。対象モジュールの内部 import へ
   入り込むのではなく、モジュールがすでに公開している名前に対して `patch.object(module, "sleep")`
   を使うか、標準ライブラリの呼び出しそのものではなく、それを呼んでいる上位のメソッドにパッチを
   当てます。
3. **残る `arg-type` / `call-arg` は、雑音ではなく実際のシグナルとして1件ずつ検討します。** 呼び出し
   側のシグネチャが `dict[str, str | None] | None` を要求する箇所へ `dict[str, str]` を渡している
   テストは、現在のシグネチャがもう許していないケースを検証しているか、シグネチャが実際に必要な
   以上に厳しいかのどちらかです。どちらであっても、テスト側の修正か本体コードの型を広げる修正で
   対応し、`# type: ignore` で一律に抑え込むことはしません。

`tests/` のすべてのディレクトリがクリーンになったら、`typecheck` の Makefile ターゲットと CI の
ステップへ `tests` を加え、`bajutsu demos scripts` と同じように以後実行されるようにします。

pydantic プラグインをどうするかは、本項目の範囲には含めません。*検討した代替案* を参照してください。

## 検討した代替案

- **`tests/` 全体に strict モードを1つの PR で有効化する** — 却下します。1361 件のエラーが一度に
  出てはレビューできず、その圧力のもとで行う修正は精査ではなく拙速になります。
- **本項目で pydantic mypy プラグインを有効化する** — いまは却下します。計測した限り、コードベース
  全体でエイリアス付きモデルを呼び出しているすべての箇所を洗い出さないかぎり、`tests/` の46件を
  直しても `bajutsu/` に9件の新規エラーを持ち込み、差し引きで負になります。その洗い出しが済んだ
  段階でのフォローアップ項目としての価値はありますが、本項目はそれに依存しません。
- **エラーの多いファイルを mypy から永久に除外する** — 却下します。ファイル単位の除外は静かに
  古びていきます。除外されたファイルへ新しいテストを追加しても、誰も決めないままその除外を引き継ぐ
  ことになるためです。下記の *進捗* チェックリストを備えたディレクトリ単位のオーバーライドなら、
  この状態が見え続けます。
- **`tests/` を型なしのまま放置し、コードレビューに委ねる** — 現状そのものであり、却下します。
  BE-0067 がすでにギャップとして指摘した現状であり、レビューだけでは今回の参考実行が明らかにした
  `attr-defined` や `arg-type` の類のずれを捉えられていませんでした。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `tests.*` の緩めたオーバーライド（`disallow_untyped_defs = false`、
  `warn_unused_ignores = false`）を追加する。まだ `tests` を `typecheck` の対象には加えない。
- [ ] `tests/ai/`（すでにクリーン、0件）と `tests/scenario/`（7件）を解消し、オーバーライドで
  十分か確認してから、より大きなディレクトリへ進む。
- [ ] `tests/report/`（17件）と `tests/orchestrator/`（83件）を解消する。
- [ ] `tests/runner/`（124件）を解消する。
- [ ] `tests/serve/`（228件、123ファイルと最大のディレクトリ）を解消する。
- [ ] `tests/` 直下のフラットなファイル群（約180ファイルで902件）を、`test_crawl.py`（200件）、
  `test_record.py`（75件）、`test_intervals.py`（49件）から解消する。
- [ ] 残るすべての `unused-ignore` を一掃し、各 `# type: ignore` を削除するか理由を明記する。
- [ ] `typecheck` の Makefile ターゲットと CI ステップに `tests` を追加し、上記の一掃が終わった時点で
  オーバーライドの `warn_unused_ignores = false` を外す。

## 参考

- [BE-0067 — コード品質ゲートの強化](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening-ja.md)
  — `scripts/` を `typecheck` に加え、本項目が引き継ぐフォローアップとして `tests/` を明示的に
  見送りました。
- [pyproject.toml](../../pyproject.toml)、[Makefile](../../Makefile) — 本項目が拡張する `[tool.mypy]`
  設定と `typecheck` ターゲットです。
- [Pydantic mypy プラグインのドキュメント](https://docs.pydantic.dev/latest/integrations/mypy/) —
  *動機* で触れ、本項目の範囲からは外したプラグインです。

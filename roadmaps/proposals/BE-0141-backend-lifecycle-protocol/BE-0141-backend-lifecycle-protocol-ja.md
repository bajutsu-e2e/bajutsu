[English](BE-0141-backend-lifecycle-protocol.md) · **日本語**

# BE-0141 — backend のライフサイクルを型システムに載せる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0141](BE-0141-backend-lifecycle-protocol-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0141") |
| トピック | プラットフォーム拡張（Android / Web / Flutter） |
| 関連 | [BE-0009](../../implemented/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)、[BE-0042](../../implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md)、[BE-0007](../../in-progress/BE-0007-android-backend/BE-0007-android-backend-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`bajutsu/drivers/base.py` は、runner が iOS・web・（将来の）Android を 1 つのインターフェース
越しに動かせるよう、すべての backend が実装する `Protocol` である `Driver`（`base.py:91`）を
定義しています。しかし、1 回の実行の前後で backend が必要とする 4 つのライフサイクル操作
（`navigate`、`close`、`reset_context`、`await_ready`）はこの Protocol に含まれていません。
これらは具象クラス（`PlaywrightDriver`、`XcuitestDriver`）にしか存在せず、
`bajutsu/environment.py` からは `# type: ignore[attr-defined]` によるエスケープ経由で呼ばれて
います。本提案は、これらの操作を型システムに載せます。第 2 の `Protocol` として切り出すか、
environment 側を driver の型についてジェネリックにするか、いずれかの形をとります。

## 動機

`environment.py` は、`Driver`（`base.py:91-116`）が宣言していない 4 つのライフサイクル
メソッドを呼び出しています。

- `driver.navigate()` — `environment.py:276`（web の起動）と `environment.py:589`（web の
  relaunch）。いずれも `# type: ignore[attr-defined]  # web-only lifecycle`。
- `driver.close()` — `environment.py:309`、`# type: ignore[attr-defined]  # web-only lifecycle,
  confined to this env`。
- `driver.reset_context()` — `environment.py:323`、`# type: ignore[attr-defined]  # web-only
  lifecycle (fresh context)`。
- `driver.await_ready()` — `environment.py:486`、`# type: ignore[attr-defined]  # xcuitest-only
  lifecycle`。

この 5 箇所は、`PlaywrightDriver` 上の `navigate`／`reset_context`／`close`
（`bajutsu/drivers/playwright.py:383,388,434`）と、`XcuitestDriver` 上の `await_ready`
（`bajutsu/drivers/xcuitest.py:259`）を実装している箇所とちょうど対応します。本リポジトリの
`bajutsu/` 配下のソース（`tests/` を除く）には `type: ignore` コメントが 16 個あり、**その
うち 5 個、実に 3 割近くが、この 1 ファイルに集中した 4 つのライフサイクル呼び出し**です。
これはコードベース分析レポートが技術的負債の指摘 #5 として別途取り上げている箇所でもあります。
この集中そのものがシグナルです。散発的な型チェックのノイズではなく、「呼び出し側が型付けの
対象としているインターフェースにライフサイクルメソッドが存在しない」という同じ形が、4 つの
異なるメソッドについて繰り返し現れています。プロジェクト全体で mypy は strict であるため、
これらはうっかり見落とされたものではなく、意図的に繰り返されたエスケープです。そして、この
エスケープが回避している構造（`Driver` Protocol が知らないライフサイクル呼び出しを、型では
なくコメントで通す）は、Android 自身の起動・終了シーケンスでもそのまま再発します。

深刻度は**中程度**です。この 5 箇所のいずれも、現時点で誤りというわけではありません。それぞれ
どのプラットフォームに属するかが狭い範囲でコメントされていますし、同じファイルの他の箇所
（`environment.py:293,334,344,354` の読み取り系の呼び出し）では `cast(PlaywrightDriver,
driver)` が正しく使われています。リスクは、リグレッションに対する安全性とオンボーディングの
コストです。`# type: ignore` はその行の mypy を黙らせるだけで、その呼び出しが安全であることを
証明してはいません。そのため、あるライフサイクル呼び出しを、それを実装していない driver へ
移してしまうリファクタリングは、`make check` の時点ではなく実行時の `AttributeError` として
初めて発覚します。これは、コードベースの他の部分で「mypy が strict である」ことが保証しようと
している性質とは逆の挙動です。

## 詳細設計

この gap を埋めるには、互いに補い合う 2 つの変更が必要です。どちらも問題の異なる半分（欠けて
いる型そのものと、呼び出し箇所での不格好なキャストと）に対応しており、修正を完全にするには
両方が要ります。

1. **`Lifecycle` Protocol を導入する。** `bajutsu/drivers/base.py` に、`Driver` や
   `EvidenceProvider` と並べて、実際に使われている 4 つの操作
   （`navigate() -> None`、`close() -> None`、`reset_context() -> None`、
   `await_ready(timeout: float = ..., poll: float = ...) -> None`）を宣言します。既存の
   `EvidenceProvider`（`base.py:119-133`）の前例に倣い、`Driver` の必須拡張ではなく、
   backend が任意に採用する狭い `@runtime_checkable` Protocol とします。これにより、
   ライフサイクルを必要としない backend（idb の `simctl` シーケンスは boot/erase/install を
   driver の外で完結させています）に、no-op のメソッドの実装を強いずに済みます。名前は、
   姉妹にあたる `module-naming-debt` 提案（environment と config のモジュール命名の負債）と
   衝突してはなりません。もしそちらの `environment.py` のリネームが先に着地した場合、この
   Protocol には `Lifecycle` 以外の名前（例えば `BackendLifecycle` や `DriverLifecycle`）が
   必要になります。
2. **呼び出し箇所を `attr-defined` の抑制ではなく Protocol 越しに型付けする。**
   `environment.py` の 5 箇所（276、309、323、486、589）はそれぞれ、
   `driver.navigate()  # type: ignore[attr-defined]` から `cast(Lifecycle, driver).navigate()`
   （スコープ内で具象型がすでに判明している箇所では、その型への直接呼び出し）に変わります。
   これは、同じファイルが他の web 固有の呼び出しですでに正しく使っている
   `cast(PlaywrightDriver, driver)` のパターンをなぞるものです。これにより、対象に
   `navigate`／`close` などが*存在すること*自体が、コメント上の主張ではなく型チェックされた
   事実になります。あるメソッドの実装を backend がやめてしまった場合、実行時ではなく
   `make check`（mypy strict）の時点で失敗するようになります。
3. **コミット前にスコープを見極める価値がある代替案：driver の型についてジェネリックな
   `Environment[D]`。** 実装を進める中で、`Lifecycle` の呼び出しのほとんどが environment
   クラスごとに 1 つに閉じている（`WebEnvironment` は常に `PlaywrightDriver` にしか
   ライフサイクル呼び出しをせず、XCUITest を駆動する environment は常に `XcuitestDriver`
   にしか呼び出さない、など）と判明した場合、environment クラス自身を driver の型について
   ジェネリックにする（`D` を `PlaywrightDriver` に束縛した `class WebEnvironment(Generic[D])`）
   ことで、各呼び出し箇所でのキャストが丸ごと不要になる可能性があります。実装者は、実際の
   呼び出し箇所を洗い出した上で、キャストがより少なく済むほうを基準に `Lifecycle` Protocol と
   ジェネリックな environment のどちらを採るか（呼び出しの一部が横断的で一部がそうでないなら
   両方を併用するか）を選びます。これはコードを前にして判断すべき設計上の決定であり、
   あらかじめ規定するものではありません。
4. **挙動にリグレッションがないことを確認する。** これは型付けのみの変更であり（prime
   directive 上の制約には触れず、実行時の分岐ロジックも変わりません）、`make check`
   （mypy strict と既存のテストスイート）が検証のすべてです。5 つの呼び出し箇所が引き続き
   同じ具象メソッドに解決されることを確認する以上の新規テストシナリオは必要ありません。

## 検討した代替案

- **`type: ignore` をドキュメントとしてそのまま残す。** それぞれすでにどのプラットフォームに
  属するかがコメントされており、読み手が意図を誤解することはありません。しかしコメントは
  mypy によってチェックされません。コメントが記録している前提を壊すリファクタリングを、
  コメントは生き延びてしまいます。これは、コードベースの他の箇所で `make check` がまさに
  検出しようとしている失敗のパターンそのものです。
- **`navigate`／`close`／`reset_context`／`await_ready` を `Driver` Protocol に直接組み込み、
  すべての backend に実装させる。**（idb の `Driver` 実装には no-op のスタブが増えることに
  なります。）この案は却下します。idb のようにこれらの関心事を持たない backend に、
  プラットフォーム固有のライフサイクルの都合を押し付けることになるためです。これは、姉妹に
  あたる `per-platform-effective-config` 提案（`Effective` をプラットフォームごとの設定に
  分割する）が config について扱っている「1 つのフラットなインターフェースに、あらゆる
  プラットフォームの関心事が積み上がる」という同じ問題です。2 つの項目の一貫性という観点
  からも、ここでも狭い任意採用の Protocol のほうが適しています。
- **各呼び出し箇所で `cast` の代わりに `hasattr`／`getattr` を使う。** `type: ignore` は
  なくなりますが、静的な保証を、型システムを完全に迂回する実行時のダックタイピングの
  チェックと引き換えにすることになります。今日の狭い範囲に限定された `type: ignore` より
  悪化させるだけで、改善にはなりません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `bajutsu/drivers/base.py` に `Lifecycle`（名称は姉妹項目である命名負債の解消待ち）Protocol を導入する。
- [ ] `environment.py` の 5 箇所の `type: ignore[attr-defined]` を、Protocol に対する型付きの `cast` に移行する。
- [ ] driver の型についてジェネリックな `Environment[D]` を検討し、キャストが減るなら採用する。
- [ ] 挙動を変えずに `make check`（mypy strict とテストスイート全体）が通ることを確認する。

まだ着手した PR はありません。

## 参考

- `bajutsu/drivers/base.py:90-134` — `Driver` と `EvidenceProvider` の各 Protocol。`Lifecycle`
  は後者の狭い任意採用という形を踏襲します。
- `bajutsu/environment.py:276,309,323,486,589` — 5 つの `# type: ignore[attr-defined]`
  ライフサイクル呼び出し箇所。
- `bajutsu/environment.py:293,334,344,354` — 本提案のパターンが踏襲する、既存の
  `cast(PlaywrightDriver, driver)` 呼び出し箇所。
- `bajutsu/drivers/playwright.py:383,388,434` — `PlaywrightDriver.navigate`／
  `reset_context`／`close`。
- `bajutsu/drivers/xcuitest.py:259` — `XcuitestDriver.await_ready`。
- `bajutsu/` 配下のソース（`tests/` を除く）にある `type: ignore` コメントは合計 16 個で、
  上記の 5 箇所がそのうちに含まれます。
- 関連するロードマップ項目：[BE-0009](../../implemented/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)
  （cross-platform abstractions）、[BE-0042](../../implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md)
  （platform backend registry）、[BE-0007](../../in-progress/BE-0007-android-backend/BE-0007-android-backend-ja.md)
  （Android backend）。
- 2026-07-02 のコードベース分析レポート（design）に由来します。同レポートの技術的負債の
  指摘 #5 でもあります。

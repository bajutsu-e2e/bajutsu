[English](BE-0141-backend-lifecycle-protocol.md) · **日本語**

# BE-0141 — backend のライフサイクルを型システムに載せる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0141](BE-0141-backend-lifecycle-protocol-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0141") |
| 実装 PR | [#671](https://github.com/bajutsu-e2e/bajutsu/pull/671) |
| トピック | ドライバとバックエンドのアーキテクチャ |
| 関連 | [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)、[BE-0042](../BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md)、[BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`bajutsu/drivers/base.py` は、すべての backend が実装する `Protocol` である `Driver`
（`base.py:91`）を定義しています。runner は、この 1 つのインターフェース越しに、iOS、web、
（将来の）Android を動かします。しかし、1 回の実行の前後で backend が必要とする 4 つの
ライフサイクル操作は、`Driver` Protocol に含まれていません。操作は `navigate`、`close`、
`reset_context`、`await_ready` です。これらは具象クラス（`PlaywrightDriver`、`XcuitestDriver`）
にしか存在せず、`bajutsu/environment.py` は `# type: ignore[attr-defined]` によるエスケープ経由で
呼んでいます。

本提案は、この 4 つの操作を型システムに載せます。第 2 の `Protocol` として切り出すか、
environment 側を driver の型についてジェネリックにするか、いずれかの形をとります。

## 動機

`environment.py` は、`Driver`（`base.py:91-116`）が宣言していない 4 つのライフサイクル
メソッドを呼び出しています。

- `driver.navigate()`：`environment.py:276`（web の起動）と `environment.py:589`（web の
  relaunch）。どちらにも `# type: ignore[attr-defined]  # web-only lifecycle` が付いています。
- `driver.close()`：`environment.py:309`。`# type: ignore[attr-defined]  # web-only lifecycle,
  confined to this env` が付いています。
- `driver.reset_context()`：`environment.py:323`。`# type: ignore[attr-defined]  # web-only
  lifecycle (fresh context)` が付いています。
- `driver.await_ready()`：`environment.py:486`。`# type: ignore[attr-defined]  # xcuitest-only
  lifecycle` が付いています。

この 5 箇所は、実装側の位置とちょうど対応します。`navigate`、`close`、`reset_context` は
`PlaywrightDriver` に実装されています（`bajutsu/drivers/playwright.py:383,388,434`）。
`await_ready` は `XcuitestDriver` に実装されています（`bajutsu/drivers/xcuitest.py:259`）。

`bajutsu/` 配下のソース（`tests/` を除く）には `type: ignore` コメントが 16 個あります。
**そのうち 5 個、実に 3 割近くが、1 つのファイルに集中した 4 つのライフサイクル呼び出しです。**
コードベース分析レポートは、この箇所を技術的負債の指摘 #5 として別途取り上げています。

この集中そのものがシグナルです。散発的な型チェックのノイズではなく、同じ形が繰り返し現れて
います。呼び出し側が型付けの対象としているインターフェースにライフサイクルメソッドが存在しない、
という形で、4 つの異なるメソッドについて現れています。プロジェクト全体で mypy は strict なので、
どのエスケープも、見落としではなく意図的な繰り返しです。

このエスケープが回避している構造は、Android 自身の起動と終了のシーケンスでも、そのまま再発
します。構造とは、`Driver` Protocol が知らないライフサイクル呼び出しを、型ではなくコメントで
通すことです。

深刻度は**中程度**です。5 箇所のいずれも、現時点で誤りではありません。それぞれ、どの
プラットフォームに属するかが狭い範囲でコメントされています。同じファイルの他の箇所では、
読み取り系の呼び出しに `cast(PlaywrightDriver, driver)` が正しく使われています（たとえば
`environment.py:293,334,344,354`）。

リスクは、リグレッションに対する安全性とオンボーディングのコストです。`# type: ignore` は、
その行の mypy を黙らせるだけで、呼び出しが安全であることを証明しません。あるライフサイクル
呼び出しを、それを実装していない driver へ移すリファクタリングを考えます。その失敗は、
`make check` の時点ではなく、実行時の `AttributeError` として初めて発覚します。これは、
コードベースの他の部分で「mypy が strict である」ことが保証しようとしている性質とは逆の
挙動です。

## 詳細設計

この gap は、互いに補い合う 2 つの変更で埋めます。それぞれ問題の異なる半分に対応します。欠けて
いる型そのものと、呼び出し箇所での不格好なキャストです。修正を完全にするには、両方が要ります。

1. **`Lifecycle` Protocol を導入する。** `bajutsu/drivers/base.py` に、`Driver` や
   `EvidenceProvider` と並べて、実際に使われている 4 つの操作を宣言します。宣言は
   `navigate() -> None`、`close() -> None`、`reset_context() -> None`、
   `await_ready(timeout: float = ..., poll: float = ...) -> None` です。

   この Protocol は、既存の `EvidenceProvider`（`base.py:119-133`）の前例に倣います。
   `Driver` の必須拡張ではなく、backend が任意に採用する、狭い `@runtime_checkable` Protocol
   とします。ライフサイクルを必要としない backend に、no-op のメソッドの実装を強いずに済みます。
   idb がその例で、`simctl` のシーケンスが boot、erase、install を driver の外で完結させて
   います。

   名前は、姉妹にあたる `module-naming-debt` 提案（environment と config のモジュール命名の
   負債）と衝突してはなりません。そちらの `environment.py` のリネームが先に着地した場合、
   この Protocol には `Lifecycle` 以外の名前が必要になります。たとえば `BackendLifecycle` や
   `DriverLifecycle` です。
2. **呼び出し箇所を、`attr-defined` の抑制ではなく Protocol 越しに型付けする。**
   `environment.py` の 5 箇所（276、309、323、486、589）を、それぞれ
   `driver.navigate()  # type: ignore[attr-defined]` から
   `cast(Lifecycle, driver).navigate()` に変えます。スコープ内で具象型がすでに判明している箇所
   では、その型への直接呼び出しに変えます。

   この変更は、同じファイルが他の web 固有の呼び出しですでに正しく使っている
   `cast(PlaywrightDriver, driver)` のパターンをなぞります。対象に `navigate`、`close` などが
   *存在すること*自体が、コメント上の主張ではなく、型チェックされた事実になります。ある
   メソッドの実装を backend がやめた場合は、実行時だけでなく、`make check`（mypy strict）の
   時点で失敗するようになります。
3. **コミット前に代替案のスコープを見極める：driver の型についてジェネリックな
   `Environment[D]`。** 実装を進める中で、`Lifecycle` の呼び出しのほとんどが environment
   クラスごとに 1 つに閉じていると判明するかもしれません。`WebEnvironment` は常に
   `PlaywrightDriver` にしかライフサイクル呼び出しをせず、XCUITest を駆動する environment は
   常に `XcuitestDriver` にしか呼び出さない、という場合です。その場合は、environment クラス
   自身を driver の型についてジェネリックにできます。`D` を `PlaywrightDriver` に束縛した
   `class WebEnvironment(Generic[D])` です。クラス内で driver の具象型がすでに判明するので、
   各呼び出し箇所でのキャストが不要になる可能性があります。

   実装者は、`Lifecycle` Protocol とジェネリックな environment のどちらを採るかを選びます。
   呼び出しの一部が横断的で一部がそうでないなら、両方の併用もありえます。選ぶ基準は、実際の
   呼び出し箇所を洗い出したうえで、キャストがより少なく済むほうです。これは、コードを前にして
   実装者が判断する設計上の決定であり、あらかじめ規定するものではありません。
4. **挙動にリグレッションがないことを確認する。** これは型付けのみの変更です。prime directive
   上の制約には触れず、実行時の分岐ロジックも変わりません。`make check`（mypy strict と既存の
   テストスイート）が検証のすべてです。5 つの呼び出し箇所が引き続き同じ具象メソッドに解決される
   ことを確認する以上の、新規テストシナリオは必要ありません。

## 検討した代替案

- **`type: ignore` をドキュメントとしてそのまま残す。** それぞれ、どのプラットフォームに属するか
  がすでにコメントされているので、読み手が意図を誤解することはありません。しかし、mypy は
  コメントをチェックしません。コメントが記録している前提を壊すリファクタリングを、コメントは
  生き延びてしまいます。これは、コードベースの他の箇所で `make check` がまさに検出しようと
  している失敗のパターンです。
- **`navigate`、`close`、`reset_context`、`await_ready` を `Driver` Protocol に直接組み込み、
  すべての backend に実装させる。** idb の `Driver` 実装には、no-op のスタブが増えます。
  この案は却下します。idb のようにこれらの関心事を持たない backend に、プラットフォーム固有の
  ライフサイクルの都合を押し付けることになるためです。姉妹にあたる
  `per-platform-effective-config` 提案（`Effective` をプラットフォームごとの設定に分割する）は、
  config について同じ問題を扱っています。1 つのフラットなインターフェースに、あらゆる
  プラットフォームの関心事が積み上がる問題です。2 つの項目の一貫性という観点からも、ここで
  狭い任意採用の Protocol を選ぶほうが適しています。
- **各呼び出し箇所で `cast` の代わりに `hasattr`／`getattr` を使う。** `type: ignore` はなく
  なります。しかし、静的な保証を、実行時のチェックと引き換えにすることになります。この実行時の
  チェックは、型システムを完全に迂回するダックタイピングです。今日の狭い範囲に限定された
  `type: ignore` より悪化させるだけで、改善にはなりません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `bajutsu/drivers/base.py` に `BackendLifecycle` Protocol を導入する。名称は `Lifecycle` ではなく `BackendLifecycle` としました。姉妹項目である命名負債の解消がすでに着地して `environment.py` が `platform_lifecycle.py` へリネームされており、`Lifecycle` ではそのモジュールと紛らわしくなるためです。
- [x] `platform_lifecycle.py` の 5 箇所の `type: ignore[attr-defined]` を、`cast(BackendLifecycle, driver)` による型付きのキャストに移行する。
- [x] driver の型についてジェネリックな `Environment[D]` を検討する。**採用しません。** 4 つのフックは backend ごとに排他的に分かれており（web が `navigate`／`close`／`reset_context` を、XCUITest が `await_ready` を持つ）、`Environment` クラスをジェネリックにすると `environment_for` の戻り値型と runner／pool のすべての消費側に波及し、型付けのみの修正の範囲を大きく超えます。任意採用の Protocol と、同じファイルにすでにある `cast(PlaywrightDriver, driver)` の慣用を用いることで、変更を焦点の絞られた正確なものに保てます。
- [x] 挙動を変えずに `make check`（mypy strict とテストスイート全体）が通ることを確認する。

- 2026-07-05: `@runtime_checkable` な `BackendLifecycle` Protocol を追加し、5 箇所の呼び出しを移行して、Protocol への適合を確認するテストを追加しました（[#671](https://github.com/bajutsu-e2e/bajutsu/pull/671)）。

## 参考

- `bajutsu/drivers/base.py:90-134`：`Driver` と `EvidenceProvider` の各 Protocol。`Lifecycle` は、
  `EvidenceProvider` の狭い任意採用という形を踏襲します。
- `bajutsu/environment.py:276,309,323,486,589`：5 つの `# type: ignore[attr-defined]`
  ライフサイクル呼び出し箇所。
- `bajutsu/environment.py:293,334,344,354`：本提案のパターンが踏襲する、既存の
  `cast(PlaywrightDriver, driver)` 呼び出し箇所。
- `bajutsu/drivers/playwright.py:383,388,434`：`PlaywrightDriver.navigate`／
  `reset_context`／`close`。
- `bajutsu/drivers/xcuitest.py:259`：`XcuitestDriver.await_ready`。
- `bajutsu/` 配下のソース（`tests/` を除く）にある `type: ignore` コメントは合計 16 個です。
  上記の 5 箇所は、そのうちに含まれます。
- 関連するロードマップ項目：[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)
  （cross-platform abstractions）、[BE-0042](../BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md)
  （platform backend registry）、[BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md)
  （Android backend）。
- 2026-07-02 のコードベース分析レポート（design）に由来します。同レポートは、この箇所を技術的
  負債の指摘 #5 としても挙げています。

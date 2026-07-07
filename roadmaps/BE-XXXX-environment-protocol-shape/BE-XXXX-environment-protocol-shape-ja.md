[English](BE-XXXX-environment-protocol-shape.md) · **日本語**

# BE-XXXX — 3 つめのプラットフォームに向けて Environment protocol の形をそろえる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-environment-protocol-shape-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## はじめに

[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) の Phase
0 は、runner の actuator ごとの分岐を終わらせる `Environment` Protocol を導入しました。1 つのインタ
フェースで、`start` があるプラットフォームの run ごとの立ち上げをまるごと担い、lease を形づくるメソッド
群が、iOS と web を同じ面から runner に駆動させます。この seam は出荷済みで、正しく動いています。しかし、
今の実装者たちが Protocol を満たすやり方はそろっておらず、そのそろわなさこそ、3 つめのプラットフォームで
ある Android（[BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)）が真似るか推測する
かしなければならないものです。この項目は seam の挙動を変えません。Protocol の形をそろえて、「新しい
`Environment` は何を用意しなければならず、当てはまらないものはどう断るのか」に、Android が来る前に、それ
自体で明らかな 1 つの答えを与えます。

## 動機

`bajutsu/platform_lifecycle.py` は `Environment` Protocol（`:47`）と、3 つの系統の実装者を定義してい
ます。デバイス型の基底 `_DeviceEnvironment`（`:124`）とその上の `IosEnvironment` / `FakeEnvironment`
/ `XcuitestEnvironment`、そして Protocol を直接実装する独立した `WebEnvironment`（`:250`）です。seam
は正しいのですが、次の 3 つの不揃いが、次の実装者を追加する作業を本来より難しくしています。

- **「該当なし」の表し方が 3 通りに分かれている**。あるプラットフォームに用のない Protocol メソッドは、
  黙って `return None` する場合（`WebEnvironment.controller`、`_DeviceEnvironment.crawl_recover` /
  `crawl_aliveness` / `crawl_dialog_clearer`）、`return {}` / `return False` の no-op にする場合
  （`_DeviceEnvironment.records_video_up_front`、`WebEnvironment.device_catalog`）、そして
  `raise NotImplementedError` する場合（`_DeviceEnvironment.hook_collector`、
  `platform_lifecycle.py:146`）があります。この 3 つは互いに置き換えられません。`hook_collector` が例
  外を送出してよいのは、runner が `observes_network_via_driver()` が真の**ときだけ**それを呼ぶことに
  なっているからですが、その約束は型ではなく 1 行の docstring にあります。新しい実装者は、どのメソッドを例
  外送出のまま残してよく、どれを本物の no-op にしなければならず、どれを runner が実際に自分に対して呼ぶの
  かを、Protocol から読み取れません。

- **2 つのフラグメソッドが 3 つの capability メソッドを、慣習だけで gate している**。Protocol は、述語
  メソッド（`observes_network_via_driver`、`records_video_up_front`、`has_devices`）を、それらが
  gate する capability メソッド（`hook_collector`、`controller`、crawl 系のメソッド）と一緒に束ねてい
  ます。`hook_collector` を `observes_network_via_driver() == True` に結びつけるものは型システムには何
  もないので、「フラグを確認したときだけ安全」という関係は、散文と runner の呼び出し箇所を読んで読者が組み
  立て直すしかないルールです。これは「capability を bool で広告し、慣習で守る」という形であり、新しい実装
  者がわずかに間違えやすいものです。

- **Protocol が `run` の lease 用メソッドと crawl 専用メソッドを混ぜている**。Protocol の下半分
  （`has_devices`、`plan_lanes`、`crawl_reset`、`crawl_aliveness`、`crawl_recover`、
  `crawl_dialog_clearer`。`:97` のコメントで区切られています）は crawl コマンドのためにあり、上半分
  （`start`、`relauncher`、`controller`、`teardown`、`hook_collector`、動画・ネットワークの述語）は
  `run` の lease に仕えます。両方を 1 つの Protocol が抱えると、読者が `run` にしか関心がなくても、どの
  実装者も crawl の問いに答えなければならず、run の lease だけが必要な将来の利用者も面の全体に依存します。
  この 2 つの関心事は段階的に 1 つの Protocol へ畳み込まれ（BE-0009 の slice 2 と slice 3）、その結果と
  して一緒になったにすぎず、両者に一緒である必然性はありません。

これらはどれもバグではなく、seam は今日も正しく振る舞います。しかし BE-0009 は Android を「同じやり方で
はまる」（[BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)）と明言しており、今の形は
その「同じやり方」を曖昧にしています。Android の作者は `WebEnvironment` と `_DeviceEnvironment` を並べ
て読み、メソッドごとに 3 つの「該当なし」の書き方のどれを使うのか、ある述語と capability の組を一貫させ
ねばならないのか、run だけの最初の版でどの crawl メソッドを後回しにできるのかを、推測しなければなりません。
形をそろえるなら、まだ突き合わせるべき実装者の系統が 2 つしかない今のほうが、3 つめが着地して曖昧さを固め
てしまった後より安上がりです。`platform_lifecycle.py` は決定的なコアにあるため、これは必然的に挙動を保ち
ます。どのプラットフォームのライフサイクルも変えない、形のリファクタリングです。規模は M です。

## 詳細設計

iOS、web、fake、XCUITest の挙動は保たれます。`start` からは同じドライバが返り、同じ lease の形が作られ、
runner と crawl コマンドは同じメソッドを同じ結果で呼びます。作業は互いに排他的な形の変更の集まりで、それぞ
れが小さな PR として着地でき、後のものが後回しになっても各々が単独で価値を持ちます。

- **「該当なし」に正規の形を 1 つ与え、その約束を Protocol に書く**。メソッドごとに、参加しないプラット
  フォームが使う 1 つの書き方を決め、それを Protocol メソッドの docstring に記します（API 面の docstring
  ルール、BE-0065）。
  - runner が述語の真の**ときだけ**呼ぶメソッド（`observes_network_via_driver` が gate する
    `hook_collector`）は `raise NotImplementedError` のまま残しますが、gate の約束を docstring で明示
    します（「呼び出し側はまず `observes_network_via_driver()` を確認すること。ここで False を返すプラッ
    トフォームは例外を送出してよい」）。これで、例外送出が安全な理由を読者の推測に委ねず、文書に残します。
  - runner が**常に**呼び、null の答えを解釈するメソッド（`controller` → `None` はデバイス制御なし、
    `crawl_recover` / `crawl_aliveness` / `crawl_dialog_clearer` → `None` はこのプラットフォームにそ
    の振る舞いがない、`device_catalog` → `{}` はデバイスなし）は null や空の返り値のままにしますが、その
    null 値は未実装のスタブではなく「このプラットフォームには存在しない」という一級の意味だと docstring に
    書きます。これらは今のままで、利得は、docstring が確認前送出の組と区別するようになることです。
- **各 capability メソッドを、それを gate する述語と 1 箇所で結びつける**。「どの述語がどのメソッドを
  gate するか」を読者が組み立て直すのではなく、各述語を、それが gate するメソッドと、その組を守る runner
  の呼び出し箇所と対にした短い表を（モジュールの docstring かドキュメントコメントに）加えます。
  `observes_network_via_driver` と `hook_collector`、`records_video_up_front` と `start` の
  `record_video_dir` の扱い、`has_devices` と `plan_lanes` / `controller` です。これは型の変更ではなく
  文書です。gate を型システムに符号化すること（たとえば capability ごとに Protocol を分けること）は下で検
  討する重い代替案であり、ここでの狙いは、慣習を呼び出し箇所に散らばらせず、定義の場所で見つけられるようにす
  ることだからです。
- **crawl の lease 面を run の lease 面から分ける**。1 つの `Environment` Protocol を 2 つに分けます。
  `start`、`relauncher`、`controller`、`teardown`、`hook_collector`、run の述語を持つ `RunEnvironment`
  （または基底の `Environment`）と、`has_devices`、`plan_lanes`、`crawl_reset`、`crawl_aliveness`、
  `crawl_recover`、`crawl_dialog_clearer` を持つ拡張の `CrawlEnvironment` です。具体的な実装者は引き続
  き両方を満たし（クラスは何も変わりません）、`run` の runner は `RunEnvironment` だけが必要だと宣言し、
  crawl コマンドは `CrawlEnvironment` が必要だと宣言します。こうして各読者は自分のコマンドが使う面だけを見
  ることになり、run だけの新しいプラットフォームを crawl メソッド抜きで考えられます。これを Protocol の分割
  にするか、文書による区分けにするかは判断が要ります。分割は境界を型検査の対象にする、より強い形です。
- **seam が今含意する「プラットフォームを追加する」チェックリストを書き出す**。形がそろったら、新しい
  `Environment` が何を実装しなければならず、どのメソッドを gate 付き送出の書き方に委ねてよく、どの crawl
  メソッドを run 優先の版で後回しにできるのかを列挙した短い節を（モジュールの docstring か
  `docs/architecture.md` に）加えます。これが BE-0009 の「同じやり方ではまる」への具体的な答えとなり、
  Android の作者（[BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)）は推測ではなくそ
  れを読みます。これで `docs/architecture.md` が seam と歩調を合わせます（BE-0113）。

決定性の保証には手を触れません。この項目はライフサイクルのロジックを動かさず、`sleep` もセレクタの意味論も
足しません。構造上 app-agnostic です。Protocol の形をそろえること自体が、app-agnostic な seam のための作
業だからです。どの経路にも LLM は入りません（seam は決定的なコアのコードです）。

## 検討した代替案

- **Protocol を 1 つの面のまま残し、既存の docstring に頼る**。全面的な解決としては却下しますが、上で述
  べた docstring の改善は残します。seam は正しいものの、目標は「次の作者が推測なしに正しく写せる」ことであ
  り、今日、新しい実装者が最も間違えやすいのが、まさに 3 通りの「該当なし」の書き方と、慣習に頼った述語と
  capability の対だからです。文書だけ（run と crawl の分割なし）ではその隔たりを狭めても、境界を型検査の対
  象にはできません。
- **各 capability の gate を型システムに符号化する**。たとえば `hook_collector` だけを持つ別の
  `NetworkObserving` Protocol を設け、ドライバ経由でネットワークを観測しないプラットフォームはそれを実装
  しないだけにし、runner は呼ぶ前に型を絞り込みます。gate 付き送出の書き方を丸ごと消せる点で魅力的ですが、
  この項目の範囲では却下します。runner が `Environment` を保持し絞り込むやり方への大きな変更となり、
  Protocol を多数の単一メソッドのインタフェースへ細分化しすぎる恐れがあります。run と crawl の分割によって、
  より細かい方向が読みやすいと分かれば再考に値します。この項目は明快な 1 つの分割（run と crawl）を採り、残
  りは文書にします。
- **Android を実際に作るときまでこれをすべて後回しにする**（[BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)）。
  BE-0009 が Phase 0 を 2 つめのプラットフォームより前に行うと正当化したのと同じ理屈で却下します。曖昧さ
  は、突き合わせるべき実装者の系統が 2 つしかないうちに消すのが最も安上がりです。Android の作業中に行うと、
  Android の作者に曖昧な形を学ぶことと直すことの両方を強い、片付けを機能に結びつけて、Android の PR を大き
  くレビューしにくくします。
- **これを BE-0009 に畳み込む**。却下します。BE-0009 は Implemented であり（seam を作るという Phase 0
  の仕事は終わり、その PR は merge 済みです）、出荷済みの項目を開き直して後続の形の作業を足すと、
  「Implemented」の意味がぼやけます。これは BE-0009 が作った seam の、別個の後続の洗練なので、BE-0009 を
  編集するのではなく、BE-0009 を参照する独立した項目にします。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 「該当なし」にメソッドごとの正規の形を 1 つ与え、各メソッドの約束（gate 付き送出か、一級の null か）
      を Protocol に記す
- [ ] 各 capability メソッドを、それを gate する述語と runner の呼び出し箇所と 1 箇所で対にする
- [ ] `Environment` Protocol を run の lease 面と crawl の lease 面に分け、各コマンドが必要とするより狭
      い型を宣言するようにする
- [ ] 「プラットフォームを追加する」チェックリスト（モジュールの docstring か `docs/architecture.md`）を
      書き、文書を seam と歩調を合わせて保つ

## 参考

- `bajutsu/platform_lifecycle.py:47`〜`121`（`Environment` Protocol。run と crawl を区切るコメントは
  `:97`）
- `bajutsu/platform_lifecycle.py:124`〜`195`（`_DeviceEnvironment`。`:146` の
  `raise NotImplementedError`、`:187`〜`194` の `None` を返す crawl メソッド）
- `bajutsu/platform_lifecycle.py:250`〜`359`（`WebEnvironment`。`{}` / `False` / `None` の no-op
  と、`observes_network_via_driver` から `hook_collector` への gate）
- `bajutsu/platform_lifecycle.py:542`（`environment_for`。新しいプラットフォームが拡張する factory）
- [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) の
  Phase 0（ここでそろえる seam）を延長し、
  [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md)（最初の新しい実装者となる Android
  backend）のために行います。あわせて
  [BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)（この形の由来である
  v1 の近道を採った web backend）と
  [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)（デバイス型の基底の上の XCUITest
  environment）も参照してください
- [docs/architecture.md](../../docs/architecture.md)（BE-0113 に従い seam と歩調を合わせて保つ）

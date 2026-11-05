[English](BE-0256-platform-lifecycle-package-split.md) · **日本語**

# BE-0256 — platform_lifecycle をパッケージへ分割し、デバイス解決を Environment という seam に通す

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0256](BE-0256-platform-lifecycle-package-split-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0256") |
| 実装 PR | [#1080](https://github.com/bajutsu-e2e/bajutsu/pull/1080) |
| トピック | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## はじめに

[BE-0197](../BE-0197-environment-protocol-shape/BE-0197-environment-protocol-shape-ja.md) は、3 つめの
プラットフォームがそのまま採用できるよう、`Environment` Protocol とその実装者たちの形をそろえました。
このとき、2 つの点には意図的に手を付けませんでした。モジュールの大きさと、CLI から seam をどう使うかです。

問題は 2 つ残っています。

- `bajutsu/platform_lifecycle.py` は現在 1076 行あり、6 つ以上の異なる責務を 1 つのファイルに抱えて
  います。
- モジュールの外側にある 4 つの呼び出し箇所（`record`、`run`、`audit`、`doctor`）は、`Environment` に
  何ができるかを問わず、今も actuator の名前文字列で分岐しています。そのうち 1 つは `xcuitest` に対して
  明確に誤っています。

本項目は、モジュールを既存の区切りに沿ってパッケージへ分割します。あわせて、CLI に不足している
`Environment` への問い合わせを 2 つ追加します。これで、actuator の素性が BE-0197 でそろえた seam の外へ
漏れ出さなくなります。

## 動機

1076 行のモジュールはたどりにくいものです。`platform_lifecycle.py` は、次のものを 1 つのファイルに
まとめて抱えています。

- `Environment` 系の Protocol 群
- 6 つの具象 environment クラス
- `environment_for` ファクトリ
- `base.wait_until` とは別に実装された、2 つめの readiness ポーリング
- relauncher のファクトリ群
- `DeviceControl` のファクトリ群
- XCUITest 専用の `.xctestrun` パッケージングのヘルパー

どのプラットフォームのコードも、このファイル全体を読み込みます。`XcuitestEnvironment.start` だけでも
およそ 95 行あります。

これは god module です。「iOS のデバイスカタログのロジックはどこにあるのか」「プラットフォームを追加する
とどこに手が入るのか」という問いに、本来より答えにくくなっています。BE-0197 自身の動機も、Protocol の
形について同じ「たどりにくさ」を挙げていました。本項目は、同じ懸念を 1 つ外側の層、つまり型の境界では
なくモジュールの境界で扱います。

これとは別に、seam の利用側を読む過程で、2 つめの問題が見つかりました。CLI と doctor 側の 4 箇所が、
`Environment` に問い合わせず、actuator の素性を直接ハードコードしています。

- `bajutsu/cli/commands/record.py:259` は `capture_video=actuator == "idb"` としています。これは
  `xcuitest` に対して誤りです。`xcuitest` は `idb` と同じ simctl ベースのデバイスライフサイクルを共有
  しており、同じやり方で動画を撮れます。現状では、XCUITest 配下でシナリオを実行すると、動画キャプチャが
  まったく行われません。そのことを知らせる出力もありません。
- `bajutsu/cli/commands/run.py:911` は、`actuator == "adb"` かどうかで `resolve_device` を選んでいます。
- `bajutsu/cli/commands/audit.py:157` は、`playwright` 以外の actuator はすべて `simctl` で解決できると
  仮定しています。
- `bajutsu/doctor.py:230` から `234` は、同じ `xcuitest` / `adb` / それ以外という分岐を繰り返し、
  問い合わせ用の actuator と resolver を選んでいます。

4 箇所はどれも、`environment_for` が返す `Environment` がすでに知っている判断を、CLI の層で再実装して
います。プライムディレクティブ 3 は、この状態を禁じています。ツールは app-agnostic であり、
プラットフォームごとの違いは seam の背後に置きます。ツール側に散らばらせません。

この漏れは、BE-0197 が Protocol の水準で塞いだ漏れの、一回り小さな形です。BE-0197 は、「プラット
フォームが何を実装しなければならないか」という**形**をあいまいでなくしました。本項目は、その形の
**利用側**が、actuator の文字列を直接調べて seam を迂回するのをやめさせます。

モジュールの分割と、この漏れをふさぐことは、同じ seam を見直す 1 回のパスの 2 つの側面です。そのため、
2 つのロードマップ項目に分けず、両方をまとめて提案します。

## 詳細設計

以下で説明する動画キャプチャの修正を除き、既存のどのプラットフォーム（iOS、web、fake、XCUITest、
Android）についても、挙動を変えません。各項目は、独立してマージできる、互いに排他的な作業単位です。

- **`platform_lifecycle.py` を `platform_lifecycle/` パッケージへ分割します。** 分割は、モジュールが
  既に持っている内部の区切りに沿って行い、挙動は変えません。想定するレイアウトは次のとおりです。
  - `protocols.py`：`ReadinessResult` データクラスと 3 つの Protocol（`RunEnvironment`、
    `CrawlEnvironment`、`Environment`）です。BE-0197 が書いた「メソッドの断り方」と「プラットフォームの
    追加のしかた」のドキュメントも、これらの型のそばに置きます。
  - `readiness.py`：`_await_ready` と `_await_boot` です。この 2 つは、モジュールが手作りしている、
    デッドラインつきのポーリングループです。隣り合わせに移すことが、次の項目で述べる `base.wait_until`
    のデッドライン規律へ統合する、自然な足場になります。モジュールは、微妙に異なる 2 つめの readiness
    ループを抱え続けずに済みます。
  - `device_control.py`：`device_control` と `android_device_control` の 2 つのファクトリです
    （`DeviceControl` protocol を実装する `_Control` クラス群）。environment 系のクラスとは、
    `simctl.Env` と `adb.Env` のハンドルをラップする点以外、何も共有していません。
  - `environments/`：具象実装者ごとに 1 モジュールです。
    - `ios.py`：`_DeviceEnvironment` と `IosEnvironment`
    - `android.py`：`AndroidEnvironment`
    - `web.py`：`WebEnvironment`
    - `xcuitest.py`：`XcuitestEnvironment`、`_patch_xctestrun_env`、`_allocate_port`、
      `_RUNNER_STARTUP_TIMEOUT`
    - `fake.py`：`FakeEnvironment`

    このレイアウトは、`plistlib`、`tempfile`、`shlex` の import も、全プラットフォームが読み込む
    モジュール本体から切り離します。これらを必要とするのは、`.xctestrun` パッケージングだけです。
  - `__init__.py`：公開名（`Environment`、`environment_for`、`device_relauncher`、`device_control`、
    `android_device_control` など）を re-export します。既存の import 箇所
    （`from bajutsu import platform_lifecycle` と `from bajutsu.platform_lifecycle import …`）は、
    そのまま動きます。`environment_for` と relauncher のファクトリ（`_web_relauncher`、
    `device_relauncher`）は、パッケージのルートに残します。1 つのプラットフォームに閉じない、モジュールの
    公開ファクトリ面だからです。
  - 分割は純粋な再編成です。どのクラスもメソッドが増減せず、どの Protocol も形を変えません。
    `environment_for` の分岐も今のままです。以降の項目は、新しい問い合わせメソッドを**追加する**だけで、
    このファクトリの既存のディスパッチには手を入れません。
- **`_await_ready` と `_await_boot` を、`base.wait_until` のデッドライン規律に統合します。** 現状、
  このモジュールは、デッドラインつきのポーリングループをもう 1 つ手作りしています。`_await_ready` は
  デバイスと web 系向け、`_await_boot` は Android 向けです。このループは、runner のステップ単位の待機が
  頼る `base.wait_until` の実装とは別物です。

  両者の契約は異なるため、単純な呼び出し置き換えにはなりません。
  - `base.wait_until`（`base.py:359`）は、具体的な `sel: Selector` を必須とします。固定間隔で
    `driver.wait_for(sel)` をポーリングし、素の `bool` を返します。
  - `_await_ready` は `driver.query()` をポーリングします。セレクタが与えられないときは、`ready_sel`、
    `id_namespaces`、素の件数ヒューリスティックの順にフォールバックします。指数的にバックオフし
    （`poll_init` から `poll_max`）、`ReadinessResult`（シグナルと経過時間）を返します。BE-0231 の
    タイムアウト診断は、この `ReadinessResult` に依存します。

  そこで方針は、単調デッドラインと指数バックオフの*ループの骨組み*を、1 つのプリミティブへ抽出する
  ことです。`base.wait_until` と `_await_ready` の双方がそれを土台にし、それぞれのポーリング本体と
  戻り値の型は保ちます。`_await_ready` を `base.wait_until` に通す案は採りません。フォールバック段と
  `ReadinessResult` を落とすことになるからです。

  この分割により、手作りのデッドライン実装が 1 つ減ります。これは決定性優先（プライムディレクティブ 2）
  に沿います。どちらの呼び出し元も、何を待つかは変わりません。
- **`Environment.resolve_device(actuator, udid) -> str` を Protocol に追加します。** どの environment
  も、自分のプラットフォームでデバイスハンドルをどう解決するかを、既に知っています。iOS 系は
  `simctl.resolve_udid`、Android は `adb.resolve_serial`、web はそのまま素通しです。この知識を Protocol
  のメソッドとして公開すれば、`run.py:911`、`audit.py:157`、`doctor.py:230` から `234` は、
  `environment_for(actuator, udid).resolve_device(...)` を呼ぶだけになります。現状の 3 箇所は、
  `actuator == "adb"` / それ以外は simctl という同じ分岐を、それぞれ導出し直しています。次にどの
  プラットフォームが追加されても、この 3 箇所は同じ 1 行の呼び出しのままです。
- **新しい述語 `Environment.captures_video -> bool` を追加します。** `capture_video` を、CLI が
  `actuator == "idb"` と書き下す代わりに、seam が答えるようにします。
  - 述語は新設する必要があります。既存の `records_video_up_front` は再利用できません。この述語が答えるのは、
    「配線が起動前か、オンデマンドか」という直交する軸です。simctl ベースの環境はいずれも `False` を返す
    ため、録画できる `idb` と `xcuitest`、録画できない `fake` が、同じ値を共有します。この意味で使う
    既存の呼び出し元（`runner/pool.py:172`）もあります。「そもそも録画できるか」という軸を表す余地は
    ありません。
  - 本項目で挙動が変わるのは、ここだけです。`record.py:259` は、`xcuitest` の下では動画をまったく
    キャプチャしません。`XcuitestEnvironment` は `IosEnvironment` と同じ simctl ベースのデバイスを共有し、
    `idb` と同じやり方で動画を撮れるにもかかわらずです。
  - 判断を `Environment` 経由に通せば、漏れをふさぐ直接の帰結として、このバグが直ります。別立てで理由
    づけして直す必要はありません。
  - 回帰テストで、`idb` と `xcuitest` の両方で `capture_video` が真になることを固定します。実際に録画
    できないプラットフォーム（例えば `fake`）では、偽になることも固定します。
- **4 つの呼び出し箇所を、2 つの新しい seam メソッドを使うように書き換え、actuator の文字列分岐を
  削除します。** `record.py`、`run.py`、`audit.py`、`doctor.py` はそれぞれ、ローカルな
  `actuator == "..."` の条件分岐を、`Environment` 経由の呼び出しに置き換えます。周囲のコードが既に
  `Environment` を構築している箇所はそれを使い、構築していない箇所は新たに構築します。新しい CLI
  フラグや設定キーは導入しません。既存の判断を、既存の seam へ通す変更です。

シナリオの YAML やセレクタの意味づけは、どこも変わりません。新たな固定 `sleep` も入りません。
プライムディレクティブ 2 はそのままです。readiness の統合は、重複したデッドラインループを**なくす**
変更であり、増やしません。

パッケージの分割と 2 つの seam メソッドは、どちらもプライムディレクティブ 3 に仕えます。プラット
フォームごとの違いは、引き続き `Environment` の背後に置かれます。本項目のあと、CLI と doctor の層は、
自分で正しく保たなければならない actuator の名前を持たなくなります。

この経路に LLM 呼び出しは入りません。`platform_lifecycle` は引き続き deterministic core のコードで
あり、Tier-2 の `run` / CI のゲートで検証され続けます。プライムディレクティブ 1 に影響はありません。

## 検討した代替案

- **モジュールは分割せず、2 つの seam メソッド（`resolve_device`、`captures_video`）だけを追加する。**
  これだけで済ませる案は却下しました。god module のほうが、この問題のより解決しづらい半分だからです。
  `platform_lifecycle.py` が 1076 行のままでは、この先のプラットフォームや seam メソッドも、この 1 つの
  ファイルに積み上がります。分割と seam メソッドの追加は、同じ seam に触れる作業です。ファイルを開いて
  いるこの機会に、まとめてレビューするのが適しています。2 つのロードマップ項目に分けると、同じ背景を
  二度説明し直すことになります。
- **モジュールは分割するが、4 箇所の actuator 文字列分岐には触れない。** 却下しました。理由は 2 つ
  あります。1 つは、この分岐が現に起きている正しさの問題（`xcuitest` では動画がまったく撮れない）を
  隠していることです。これは同じパスで seam の利用側を読む過程で見つかりました。もう 1 つは、呼び出し
  箇所に触れる理由のないパッケージ分割は、それ単体では価値の低いリファクタリングにとどまることです。
  たどりやすさが上がること自体は本物の効果です。漏れをふさぐことと組み合わせてはじめて、本項目は
  単なる整理ではなく、プライムディレクティブ 3 の修正になります。
- **`resolve_device` を `Environment` のメソッドではなく、actuator をキーにしたフリー関数
  （モジュールレベルの `resolve_device(actuator, udid)` ディスパッチ関数）として実装する。** 却下
  しました。actuator の文字列をキーにしたフリー関数は、本項目がなくそうとしているまさにその形です。
  `actuator == "adb"` という分岐を、4 箇所の呼び出し元から 1 つの新しい関数へ移すだけで、削除にはなり
  ません。`Environment` のメソッドにすれば、プラットフォームごとの判断を、BE-0009 と BE-0197 が
  すでに置いている場所にそろえられます。
- **Android 自身の environment 作業がこの分割を必要とするまで先送りする。** BE-0197 が「Android の
  作業まで先送りする」案を却下した理由と同じ理由で、却下しました。このモジュールは、デバイス型と web
  という 2 つのプラットフォーム系統だけで、すでに大きくなっています。Android は 3 つめの系統で、
  `AndroidEnvironment` としてすでに相当量のコードがこのファイルに入っています。4 つめのプラット
  フォームのコードが同じファイルに積み増される前に分割するほうが、あとで分割するより安上がりです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `platform_lifecycle.py` を `platform_lifecycle/` パッケージ（`protocols.py`、
      `readiness.py`、`device_control.py`、`environments/{ios,android,web,xcuitest,fake}.py`、
      `__init__.py` の re-export）へ、挙動を変えずに分割する
- [x] `_await_ready` / `_await_boot` を `base.wait_until` のデッドライン規律に統合する
- [x] `Environment.resolve_device(udid)` を追加し、`run.py` / `audit.py` / `doctor.py`
      をそこに通す
- [x] 新しい述語 `Environment.captures_video`（`records_video_up_front` の再利用ではない）を追加し、
      `record.py` の `capture_video` をそこに通して XCUITest の動画キャプチャのバグを修正する。
      回帰テストを添える

**ログ**

- 1 つの PR としてまとめて実装しました。`platform_lifecycle/` パッケージへの分割
  （`protocols.py`、`readiness.py`、`device_control.py`、`relaunchers.py`、`factories.py`、
  `environments/{ios,android,web,xcuitest,fake}.py`）、`base.wait_until` と `_await_ready` /
  `_await_boot` が共有する `base.deadline_ticks` プリミティブ、`resolve_device` /
  `captures_video` という 2 つの seam メソッド、そしてこれらへ書き換えた CLI と doctor 側の
  4 箇所（XCUITest の動画キャプチャのバグ修正を含みます）を、この PR に含めています。

## 参考

- [BE-0197](../BE-0197-environment-protocol-shape/BE-0197-environment-protocol-shape-ja.md) は、
  3 つめのプラットフォームに向けて `Environment` Protocol の形をそろえました。この項目は、同じ seam
  への見直しを、モジュールの内部の整理と、seam の CLI 側の呼び出し元という、1 つ外側の層で引き継ぎます。
- [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) は、
  この項目のパッケージ分割と新しいメソッドが土台にする `Environment` Protocol と `environment_for`
  という seam を導入しました。
- [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md) は、`AndroidEnvironment` の
  コードが既にこのモジュールに入っているプラットフォームです。分割によって、1 つの巨大なファイルに
  積み増さずに、その先の成長のための余地ができます。
- プライムディレクティブ 3（app-agnostic。プラットフォームごとの違いは seam に置き、CLI には置かな
  い）が、この項目の `resolve_device` / `captures_video` 側の判断を導く制約です。
- `bajutsu/platform_lifecycle.py`、`bajutsu/cli/commands/record.py:259`、
  `bajutsu/cli/commands/run.py:911`、`bajutsu/cli/commands/audit.py:157`、
  `bajutsu/doctor.py:230` から `234` が、この項目が触れるファイルです。

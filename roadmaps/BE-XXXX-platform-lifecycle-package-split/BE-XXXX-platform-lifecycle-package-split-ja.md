[English](BE-XXXX-platform-lifecycle-package-split.md) · **日本語**

# BE-XXXX — platform_lifecycle をパッケージへ分割し、デバイス解決を Environment という seam に通す

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-platform-lifecycle-package-split-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## はじめに

[BE-0197](../BE-0197-environment-protocol-shape/BE-0197-environment-protocol-shape-ja.md) は、3
つめのプラットフォームがそのまま採用できるよう、`Environment` Protocol とその実装者たちの形をそろえ
ました。この項目は、モジュールの大きさと、CLI から seam をどう使うかについては、意図的に手を付けずに
残しました。`bajutsu/platform_lifecycle.py` は現在 1076 行あり、6 つ以上の異なる責務を 1 つのファイル
に抱えています。さらに、モジュールの外側にある 4 つの呼び出し箇所（`record`、`run`、`audit`、
`doctor`）は、`Environment` に何ができるかを問う代わりに、今も actuator の名前文字列で分岐しており、
そのうちの 1 つは `xcuitest` に対して明確に誤っています。この項目は、モジュールを既存の区切りに沿って
パッケージへ分割し、CLI に不足している `Environment` への問い合わせを 2 つ追加することで、actuator の
素性が BE-0197 でそろえた seam の外へ漏れ出さないようにします。

## 動機

1076 行のモジュールはたどりにくいものです。`platform_lifecycle.py` は、`Environment` 系の Protocol
群、6 つの具象 environment クラス、`environment_for` ファクトリ、`base.wait_until` とは別に実装され
た 2 つめの readiness ポーリング、relauncher のファクトリ群、`DeviceControl` のファクトリ群、そして
XCUITest 専用の `.xctestrun` パッケージングのヘルパーを、1 つのファイルにまとめて抱えています。どの
プラットフォームのコードも、このファイル全体を読み込みます。`XcuitestEnvironment.start` だけでもおよ
そ 95 行あります。「iOS のデバイスカタログのロジックはどこにあるのか」「プラットフォームを追加すると
どこに手が入るのか」に本来より答えにくくなる、まさに god module の典型です。これは BE-0197 自身の動機
が Protocol の形について挙げていた「たどりにくさ」への懸念と同じものが、型の境界ではなくモジュールの
境界という 1 つ外側の層で起きている状態だといえます。

この項目を準備する過程で、seam の利用側を読んでいるあいだに、独立してもう 1 つの問題が見つかりました。
CLI と doctor 側の 4 箇所が、`Environment` に問い合わせる代わりに actuator の素性を直接ハードコードして
いるという問題です。

- `bajutsu/cli/commands/record.py:259` は `capture_video=actuator == "idb"` としています。これは
  `xcuitest` に対して誤りです。`xcuitest` は `idb` と同じ simctl ベースのデバイスライフサイクルを共有
  しており、同じやり方で動画を撮れるはずですが、今は XCUITest 配下でシナリオを実行すると、動画キャプ
  チャが黙ってまったく行われません。
- `bajutsu/cli/commands/run.py:911` は `actuator == "adb"` かどうかで `resolve_device` を選んでいます。
  `bajutsu/cli/commands/audit.py:157` は、`playwright` 以外の actuator はすべて `simctl` で解決できる
  と仮定しています。`bajutsu/doctor.py:230` から `234` にかけても、問い合わせ用の actuator と resolver
  を選ぶために、同じ `xcuitest` / `adb` / それ以外という分岐を繰り返しています。

これら 4 箇所はどれも、`environment_for` が返す `Environment` がすでに知っている判断を、CLI の層で再実
装しているにすぎません。プライムディレクティブ 3（app-agnostic。プラットフォームごとの違いは seam の
背後に置き、ツール側に散らばらせない）が禁じているのは、まさにこの状態です。BE-0197 が Protocol の水準
で塞いだのと同じ種類の漏れが、一回り小さな形で残っていたということです。BE-0197 は「プラットフォームが
何を実装しなければならないか」という**形**をあいまいでなくしましたが、この項目は、その形の**利用側**が
actuator の文字列を直接調べて seam を迂回するのをやめさせます。モジュールの分割と、この漏れをふさぐこと
は、同じ seam を見直す 1 回のパスの、自然な 2 つの側面です。そのため、この項目では両方をまとめて 1 つの
ロードマップ項目として提案します。

## 詳細設計

以下で説明する動画キャプチャの修正を除き、既存のどのプラットフォーム（iOS、web、fake、XCUITest、
Android）についても、この項目は挙動を変えません。各項目は、独立してマージできる、互いに排他的な作業
単位です。

- **`platform_lifecycle.py` を、モジュールが既に持っている区切りに沿って `platform_lifecycle/`
  パッケージへ分割します。挙動の変更はありません。** 想定するレイアウトは次のとおりです。
  - `protocols.py`：`ReadinessResult` データクラスと 3 つの Protocol（`RunEnvironment`、
    `CrawlEnvironment`、`Environment`）、それに BE-0197 が書いた「メソッドの断り方」「プラット
    フォームの追加のしかた」のドキュメントです。これらの型のすぐそばに置くのが自然です。
  - `readiness.py`：`_await_ready` と `_await_boot` です。この 2 つは、モジュールが手作りしている
    デッドラインつきポーリングのループであり、隣り合わせて移すこと自体が、下で述べる
    `base.wait_until` のデッドライン規律への統合先として自然な置き場所になります。もう 1 つの、微妙に
    異なる readiness ループを別に抱え続けるのではなく、です。
  - `device_control.py`：`device_control` と `android_device_control` の 2 つのファクトリ
    （`DeviceControl` protocol を実装する `_Control` クラス群）です。`simctl.Env` / `adb.Env` の
    ハンドルをラップするという点以外、environment 系のクラスとは何も共有していません。
  - `environments/`：具象実装者ごとに 1 モジュールです。`ios.py`（`_DeviceEnvironment`、
    `IosEnvironment`）、`android.py`（`AndroidEnvironment`）、`web.py`（`WebEnvironment`）、
    `xcuitest.py`（`XcuitestEnvironment`、`_patch_xctestrun_env`、`_allocate_port`、
    `_RUNNER_STARTUP_TIMEOUT`）、`fake.py`（`FakeEnvironment`）です。これにより、`.xctestrun`
    パッケージングにしか要らない `plistlib` / `tempfile` / `shlex` の import も、今はどのプラット
    フォームでも読み込まれているモジュール本体から切り離されます。
  - `__init__.py`：公開名（`Environment`、`environment_for`、`device_relauncher`、
    `device_control`、`android_device_control` など）を re-export し、既存の import 箇所
    （`from bajutsu import platform_lifecycle` / `from bajutsu.platform_lifecycle import …`）
    がそのまま動くようにします。`environment_for` と relauncher のファクトリ（`_web_relauncher`、
    `device_relauncher`）は、1 つのプラットフォームに閉じないモジュールの公開ファクトリ面なので、
    パッケージのルートに残します。
  - これは純粋な再編成です。どのクラスもメソッドが増減せず、どの Protocol も形を変えず、
    `environment_for` の分岐も今のままです（下の 2 点目は新しい問い合わせメソッドを**追加する**だけ
    で、このファクトリの既存のディスパッチには手を入れません）。
- **`_await_ready` / `_await_boot` を、`base.wait_until` のデッドライン規律に統合します。** 現状、
  このモジュールは、runner のステップ単位の待機が頼っている `base.wait_until` とは別に、デバイスと web
  系向けの `_await_ready` と Android 向けの `_await_boot` という、もう 1 つのデッドラインつきポーリング
  ループを手作りしています。ただし両者は同じ契約ではないため、これは単純な呼び出し置き換えではありません。
  `base.wait_until`（`base.py:359`）は具体的な `sel: Selector` を必須とし、固定間隔で
  `driver.wait_for(sel)` をポーリングし、素の `bool` を返します。一方 `_await_ready` は
  `driver.query()` をポーリングし、セレクタが与えられなければ `ready_sel` → `id_namespaces` →
  素の件数ヒューリスティックへとフォールバックし、指数的にバックオフし（`poll_init` → `poll_max`）、
  BE-0231 のタイムアウト診断が依存する `ReadinessResult`（シグナルと経過時間）を返します。そこで方針は、
  単調デッドラインと指数バックオフの*ループの骨組み*だけを 1 つのプリミティブへ抽出し、`base.wait_until`
  と `_await_ready` の双方がそれを土台にする（各自のポーリング本体と戻り値の型は保つ）ことであり、
  `_await_ready` を `base.wait_until` に通す（フォールバック段と `ReadinessResult` を落としてしまう）
  ことではありません。これにより、決定性優先（プライムディレクティブ 2）と足並みをそろえるべき、もう
  1 つの手作りデッドライン実装を分割のついでに消せます。どちらの呼び出し元が何を待っているかには手を
  入れません。
- **`Environment.resolve_device(actuator, udid) -> str` を Protocol に追加します。** どの environment
  も、自分のプラットフォームでデバイスハンドルをどう解決するかは既に知っています（iOS 系は
  `simctl.resolve_udid`、Android は `adb.resolve_serial`、web はそのまま素通しです）。これを
  Protocol のメソッドとして公開すれば、`run.py:911`、`audit.py:157`、`doctor.py:230` から `234` は、
  それぞれ独自に `actuator == "adb"` / それ以外は simctl という分岐を書く代わりに、
  `environment_for(actuator, udid).resolve_device(...)` を呼ぶだけになります。次にどのプラットフォーム
  が追加されても、この 3 箇所は同じ 1 行の呼び出しのままです。
- **新しい述語 `Environment.captures_video -> bool` を追加し、`capture_video` を
  `actuator == "idb"` と書き下す代わりに seam へ問い合わせます。** これは既存の
  `records_video_up_front` の再利用ではなく、新しい述語である必要があります。`records_video_up_front`
  は「配線が起動前か、オンデマンドか」という直交する軸を答えるもので、simctl ベースの環境はいずれも
  `False` を返すため、録画できる `idb`/`xcuitest` と録画できない `fake` が同じ値を共有します。さらに
  その意味で使う既存の呼び出し元（`runner/pool.py:172`）もあり、「そもそも録画できるか」という軸を
  表す余地がありません。この項目のなかで挙動が変わるのはここだけです。`record.py:259` は今、
  `XcuitestEnvironment` が `IosEnvironment` と
  同じ simctl ベースのデバイスを共有し、`idb` と同じやり方で動画を撮れるにもかかわらず、`xcuitest` の下
  では動画をまったくキャプチャしません。この判断を `Environment` 経由に通すことは、漏れをふさぐことの
  直接の帰結としてこのバグを修正する形になります。別立てで理由づけして直す修正ではありません。
  `idb` と `xcuitest` の両方で `capture_video` が真になり、実際に録画できないプラットフォーム
  （例えば `fake`）でだけ偽になることを、回帰テストで固定します。
- **4 つの呼び出し箇所を、この 2 つの新しい seam のメソッドを使うように書き換え、actuator の文字列分岐
  を削除します。** `record.py`、`run.py`、`audit.py`、`doctor.py` はそれぞれ、ローカルな
  `actuator == "..."` の条件分岐を、周囲のコードが既に構築している `Environment` 経由の呼び出し（まだ
  構築していない箇所では新たに構築したうえでの呼び出し）に置き換えます。新しい CLI フラグや設定キーは
  導入しません。既存の判断を、既存の seam へ通すだけの変更です。

シナリオの YAML やセレクタの意味づけは、どこも変わりません（プライムディレクティブ 2 はそのままです。
readiness の統合は重複したデッドラインループを**なくす**側の変更であり、新たな固定 `sleep` を加えるもの
ではありません）。パッケージの分割と 2 つの seam メソッドは、いずれもプライムディレクティブ 3 に仕える
ものです。プラットフォームごとの違いは引き続き `Environment` の背後に置かれ、この項目のあとは、CLI と
doctor の層が自分で正しく保たなければならない actuator の名前を一切持たなくなります。この経路に LLM 呼び
出しが入る箇所はどこにもありません。`platform_lifecycle` は引き続き deterministic core のコードであり、
Tier-2 の `run` / CI のゲートで検証され続けます（プライムディレクティブ 1 に影響はありません）。

## 検討した代替案

- **モジュールは分割せず、2 つの seam メソッド（`resolve_device`、`captures_video`）だけを追加する。**
  これだけで済ませることは却下しました。god module のほうが、この問題のより解決しづらい半分だからです。
  `platform_lifecycle.py` を 1076 行のまま残せば、この先のプラットフォームや seam メソッドも、結局この
  1 つのファイルに積み上がっていきます。分割と seam メソッドの追加はどちらも同じ seam に触れる作業なの
  で、ファイルをすでに開いているこの機会にまとめてレビューするのが理にかなっています。2 つのロードマップ
  項目に分けても、同じ背景をもう一度説明し直すだけになります。
- **モジュールは分割するが、4 箇所の actuator 文字列分岐にはあえて触れない。** この分岐は、seam の利用
  側を読んでいる過程で見つかった、現に起きている正しさの問題（`xcuitest` では動画がまったく撮れない）で
  あり、また呼び出し箇所に触れる理由のないパッケージ分割は、それ単体では価値の低いリファクタリングにと
  どまるため、却下しました。たどりやすさが上がること自体は本物の効果ですが、この漏れをふさぐことと組み
  合わせてはじめて、この項目は単なる整理ではなく、プライムディレクティブ 3 の修正になります。
- **`resolve_device` を `Environment` のメソッドではなく、actuator をキーにしたフリー関数（例えば
  `resolve_device(actuator, udid)` というモジュールレベルのディスパッチ関数）として実装する。** これは
  却下しました。actuator の文字列をキーにしたフリー関数は、この項目がなくそうとしているまさにその形だか
  らです。それでは `actuator == "adb"` という分岐を、4 箇所の呼び出し元から 1 つの新しい関数へ移すだけ
  で、なくしたことにはなりません。`Environment` のメソッドにすれば、プラットフォームごとの判断を
  BE-0009 と BE-0197 がすでに置いている場所にそろえられます。
- **Android 自身の environment 作業がこの分割を必要とするまで先送りする。** BE-0197 が「Android の
  作業まで先送りする」という代替案を却下した理由と同じ理由で却下しました。このモジュールは、デバイス型
  と web という 2 つのプラットフォームの系統だけで、既にかなりの大きさになっています。Android は 3 つ
  めの系統で、`AndroidEnvironment` としてすでにこのファイルに相当量のコードが入っています。4 つめのプラ
  ットフォームのコードが同じファイルに積み増される前に分割するほうが、あとで分割するより安上がりです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `platform_lifecycle.py` を `platform_lifecycle/` パッケージ（`protocols.py`、
      `readiness.py`、`device_control.py`、`environments/{ios,android,web,xcuitest,fake}.py`、
      `__init__.py` の re-export）へ、挙動を変えずに分割する
- [ ] `_await_ready` / `_await_boot` を `base.wait_until` のデッドライン規律に統合する
- [ ] `Environment.resolve_device(actuator, udid)` を追加し、`run.py` / `audit.py` / `doctor.py`
      をそこに通す
- [ ] 新しい述語 `Environment.captures_video`（`records_video_up_front` の再利用ではない）を追加し、
      `record.py` の `capture_video` をそこに通して XCUITest の動画キャプチャのバグを修正する。
      回帰テストを添える

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

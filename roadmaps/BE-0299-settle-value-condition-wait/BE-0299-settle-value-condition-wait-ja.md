[English](BE-0299-settle-value-condition-wait.md) · **日本語**

# BE-0299 — シナリオ途中の読み取りと idb の settle タイミングに残る読み取り競合を解消する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0299](BE-0299-settle-value-condition-wait-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0299") |
| 実装 PR | [#1274](https://github.com/bajutsu-e2e/bajutsu/pull/1274) |
| トピック | ドライバとバックエンドのアーキテクチャ |
| 関連 | [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)、[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本項目は、座標ベースのデバイスバックエンドが、[ステップ](../../docs/glossary.md#scenario-authoring)による
画面の変化を直後に観測する際の、関連する2つの隙間をふさぎます。対象のバックエンドは、idb 経由の
iOS Simulator と、adb 経由の Android です。

1つめの隙間は、読み取りにあります。ステップの `assert` と `extract` は、それぞれツリーを1回だけ読み取り、
再試行しません。アクションがツリーへ反映する結果が一拍遅れて届く場合があります。ラベルへ映される
カウンタの値や、編集後のフィールドの文字列がその例です。その場合、1回の読み取りは反映前の値を掴んで
しまいます。

2つめの隙間は、idb [ドライバ](../../docs/glossary.md#driver-backend-actuator-platform)にあります。
アクション前の settle 待ちが、経過時間ではなく固定回数のポーリングで区切られています。実行機が遅い
場面や負荷が高い場面では、Android 側の同等の待ちに比べて、猶予がずっと狭くなります。

どちらの隙間も新しい問題ではありません。
[BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)
は、シナリオ末尾の `expect` ブロックという1箇所について、この種の競合を診断して修正しました。あわせて、
Android 側の settle 待ちを、固定ポーリング回数から壁時計（経過秒数）によるデッドラインへ切り替えました。
本項目は、この2つの修正を残りの箇所まで届けます。シナリオ途中のあらゆる読み取りと、idb の settle 待ちです。

## 動機

プルリクエスト #1221（XCUITest の Simulator ランナーを wheel に同梱する変更）の追随作業で、この変更とは
無関係な CI 実行の中で、2つの隙間の両方に行き当たりました。症状は2つ現れました。

- ショーケースのあるシナリオで、`extract` ステップが2回目のタップの直後に Log タブのカウンタの値を
  捉えたところ、そのタップより前の値を取得しました。後続の `assert` ステップは、捕えた値と実際の
  正しい値を突き合わせて失敗しました。
- 同じコミットの iOS Simulator 向け conformance スイートは、3回連続の再実行のたびに別のアサーションで
  失敗しました。1回目は削除後のフィールドの文字数、2回目はフォーカス可能なはずの要素、3回目は再び
  フィールドでした。

3つの症状は見かけ上ばらばらですが、原因は1つの競合です。3つの独立した不具合ではありません。
いずれのレーンも、変更したコードを経由していません。ローカルの実行では再現せず、CI（継続的
インテグレーション）のより遅く負荷の高いマシンでのみ、この競合が再現しました。

Bajutsu には、この競合への正しい修正がすでにあります。ただし、競合の起こりうる箇所すべてには適用
されていません。`_evaluate_expect`（`bajutsu/orchestrator/loop.py:69`）は、シナリオ末尾の `expect`
ブロックを条件待ちとして評価します。ツリーを読み直してブロックのアサーションを再評価する処理を、
通るか壁時計のデッドラインに達するまで繰り返します。この関数の docstring（BE-0245 で追加されたもの）が
理由を述べています。「アクションがツリーへ映す値は、アクションが返った後に一拍遅れて届くことがある」
からです。

アクションに続く、再試行しないそれ以外の読み取りは、いずれも1回だけのスナップショットで済ませて
います。`wait` ステップはこれに含まれません。`wait` 自身の条件待ち（`_wait`、
`bajutsu/orchestrator/waits.py:137`）が、満たされるまで `query()` をポーリングしているため、この競合を
引き継がないからです。残る読み取りは2つあります。

- シナリオ途中の `assert` ステップは、ツリーを1回だけ読み取ります（`bajutsu/orchestrator/loop.py:245`）。
- `extract` ステップは、`_ScreenRead`（`bajutsu/orchestrator/loop.py:101`）経由でもう1回だけ読み取ります。
  `_ScreenRead` は、設計上1ステップにつき最大1回しか読み取りません。

結果がまだ伝わっている途中のアクションの直後に、このどちらかが動くと、`_evaluate_expect` がふさいだ
はずの競合を、修正なしにそのまま引き継ぎます。

idb の settle の隙間は、`_settle` が採るポーリング戦略の違いです。両バックエンドが何を共有しているかとは
別の話です。`bajutsu/drivers/coordinate_tree.py` の docstring は、`_settle` そのものは各バックエンド固有の
まま保つと明記しています。idb は固定の読み取り回数で、adb は壁時計のデッドラインで、ポーリングを区切って
います。docstring はこれを「共有された調整値ではなく、正真正銘の戦略上の違い」と述べています。そのため
`IdbDriver` と `AdbDriver` は今も、それぞれ独自の `_settle` を持っています。本項目は、`_settle` 自体を
共有基底クラスへ引き上げることを提案しません。

両バックエンドが実際に共有しているのは、各 `_settle` がポーリング対象とする写像、つまり `_stable_key`
（`bajutsu/drivers/coordinate_tree.py:117`）です。この写像は、ツリーを識別子とフレームだけに縮約し、
値、traits、label を意図的に除外します。これにより、静止した画面上の通常のデータ更新を、実際の
ジェスチャーがまだ着地している最中の動きと取り違えません。

BE-0245 は、`AdbDriver._settle` 自身のポーリング戦略を、固定回数から8秒の壁時計デッドラインで区切る
方式へ切り替えました（`bajutsu/drivers/adb.py:230`）。理由は具体的です。BE-0245 自身が導入した常駐
チャンネルのような速い読み取りが、settle の待ち時間を切り詰めてしまいます。その結果、まだ動いている
途中のツリーを settle 済みとして通しかねなかったためです。

`IdbDriver._settle`（`bajutsu/drivers/idb.py:335`〜`336`）は、BE-0245 が Android 側で置き換えた戦略を
今も使っています。50ミリ秒間隔で固定3回ポーリングするだけです。実行機がどれだけ負荷が高くても、上限は
150ミリ秒のままで、Android 側の8秒という猶予のおよそ53分の1にすぎません。

固定回数のポーリングは、BE-0245 が手を入れる前の adb でそうだったのと同じく、idb でも猶予を使い切って
しまいます。`_settle` をバックエンドごとに保つという明文化された理由は、片方のバックエンドだけを、すでに
猶予不足とわかっている戦略のまま残しておくべき理由にはなりません。後述する作業4は、idb に `_settle`
自身は保ったまま、adb と同じ壁時計方式を採らせます。docstring が求めるバックエンドごとの分離は、
そのまま保たれます。

値を除外する settle の写像は、まだ伝わっている途中の値の変化を、そもそも検出できません。これが前述の
隙間です。2つめの隙間は、さらに狭い問題です。idb の写像が検出しようとしているレイアウトの安定について
さえ、idb がどれだけの猶予しか与えていないか、という問題です。

## 詳細設計

以下のどの作業も、新しい仕組みを考案するのではなく、BE-0245 がすでに確立した型を再利用します。壁時計の
デッドラインで区切ったポーリングです。ランナー内のあらゆる条件待ちがすでに従っている、同じ待ち時間の
下限値を尊重します。固定の sleep は使いません。

### 作業の分解（MECE）

1. **`_evaluate_expect` のポーリングを、共有して再利用できる形に一般化します。** 「通るかデッドラインに
   達するまで読み直して再評価する」ループを、現在の `expect` 専用の形から切り出します。評価対象の
   アサーション群を引数として渡せるヘルパーにまとめ、2つ目の呼び出し元がループを複製せずに再利用
   できるようにします。

2. **シナリオ途中の `assert` ステップを、その共有ヘルパー経由にします。**
   `bajutsu/orchestrator/loop.py:245` にある1回だけの `driver.query()` を、作業1のヘルパー呼び出しへ
   置き換えます。ステップ単位の `assert` にも、シナリオ末尾の `expect` ブロックと同じ条件待ちが働くように
   し、1回のスナップショットだけで値を判定させません。

   この呼び出し箇所がすでに行っているコンテキストの絞り込みは、そのまま保ちます
   （`bajutsu/orchestrator/loop.py:246`〜`249`、BE-0250 Unit 2）。ステップ単位の `assert` は per-step の
   スクリーンショットを撮らないため、`ctx` から `visual` と `schema` を除外しています。
   `_evaluate_expect` は `ctx` を引数としてそのまま受け取り、自ら絞り込みはしません。そのため、この作業は
   絞り込み済みの `step_ctx` を共有ヘルパーへ渡します。素の `ctx` を渡すと、`visual` や
   `responseSchema` の古い入力が紛れ込みかねません。

3. **`extract` の読み取りにも、それに合った形で、実際に読み取るプロパティ群に応じた猶予を与えます。**
   `extract` ステップには満たすべきアサーションがありません。ツリーから値を取り出して変数へ写すだけです。
   作業1の「通るか失敗するか」のループは、そのままでは当てはまりません。

   1ステップの `extract` は、単一のプロパティではなく、名前付き extract の辞書です（`_run_extract` は
   `extracts: Mapping[str, Extract]` を受け取ります、`bajutsu/orchestrator/evidence_rules.py:72`）。
   それぞれの extract が、独自のセレクタと独自のプロパティを持ちます。`el.get(ext.prop)`
   （`bajutsu/orchestrator/evidence_rules.py:83`）は、その1つの extract が指すプロパティを読み取ります。
   `ext.prop` が取り得る値は `value`、`label`、`identifier` の3つです。したがって、`ext.prop` は
   `value` と同じくらい `label` にもなり得ます。本項目の動機となった事例、つまりラベルへ映される
   カウンタの値も、`value` ではなく `label` を取り出しています。複数の extract を名指すステップが、
   別々の要素から別々のプロパティを取り出すこともあります。

   そこで、読み取ったツリーの写像が、連続する2回の読み取りの間で変化しなくなるか、同じ壁時計の
   デッドラインに達するまでポーリングする、並行する読み取りを追加します。この写像は、識別子、フレーム、
   そのステップの `extract` が名指す（セレクタ, `ext.prop`）の組すべての和集合を含みます。
   `bajutsu/drivers/coordinate_tree.py` の settle 用写像を包含する形で、1つだけでなく、このステップの
   どの extract が読むプロパティであってもすべて対象にします。この読み取りを `_ScreenRead` の読み取り
   （`bajutsu/orchestrator/loop.py:101`）へ組み込みます。`extract` は、単発の読み取りでまだ伝わっている
   途中の値ではなく、落ち着いた値を受け取れるようになります。

   `.get()` は初回呼び出しの結果をキャッシュします。そのため、このポーリングを選ぶかどうかは、
   `_ScreenRead` の生成時（`bajutsu/orchestrator/loop.py:591`）か、その初回呼び出し時に決める必要が
   あります。決める条件は、`interp_step.extract` の有無です。`extract` 側の呼び出し
   （`bajutsu/orchestrator/loop.py:613`）の中で決めるのでは遅すぎます。`screenChanged` キャプチャの
   無条件かつポーリングなしの呼び出し（592行目）が先に走り、両方を持つステップではキャッシュがすでに
   埋まっているからです。

   同じ理屈は、`extract` も持つ非破壊的なステップ（`assert_`、`wait`）にも及びます。これらのステップ
   自身の読み取りが、`_ScreenRead` が生成される前に、その `seed` になってしまいます。その読み取りとは、
   `assert_` の読み取り（`bajutsu/orchestrator/loop.py:245`）か、`wait` がすでに落ち着かせたツリー
   です。生成時点でどう決めても、すでに存在する `seed` へさかのぼってポーリングをかけることは
   できません。これらのステップ種別では、プロパティを意識したポーリングを、`_ScreenRead` の内側ではなく
   その手前の読み取り箇所で行う必要があります。

   この作業は、`_ScreenRead` の読み取りが遅延され、1ステップにつき最大1回しか行われない理由の一部と
   引き換えになります。adb では、画面の読み取り（`uiautomator dump`）がステップごとのコストの大半を
   占めます。同じ読み取りは idb では0.1〜0.3秒ですが、adb ではおよそ2.4秒かかります。安定するまで
   ポーリングすると、なかなか落ち着かない画面では、`extract` ステップ1回あたりのコストが、同じ壁時計の
   デッドラインまで膨らみかねません。この作業がこのコストを引き受けるのは、ステップの `extract` が実際に
   その読み取りを消費する場合だけです。`_ScreenRead` の遅延読み取りにより、消費者のいない素の `assert` や
   `tap` ステップは、もともと一切読み取りません。追加のポーリングは、それを求めるステップにしかかかりません。

4. **`IdbDriver._settle` を壁時計デッドライン方式へ切り替えます。** 固定の `_SETTLE_MAX_POLLS` /
   `_SETTLE_POLL_S` によるループ（`bajutsu/drivers/idb.py:393`〜`400`）を、`AdbDriver._settle`
   （`bajutsu/drivers/adb.py:303`〜`311`）がすでに採っているのと同じデッドライン方式へ置き換えます。
   これにより、idb の settle 待ちは、速いマシン向けに調整された固定の猶予ではなく、実際に動かしている
   マシンの遅さに応じて伸び縮みするようになります。この作業は、作業1〜3とは独立しています。修正するのは
   ランナー側のアクション後の読み取りではなく、ドライバ側のアクション前の待ちだからです。

   この変換が済むと、2つの `_settle` は同じ `_stable_key` 写像を、同じ壁時計デッドライン方式で
   ポーリングするようになります。違いは、デッドラインとポーリング間隔の定数だけです。これはまさに
   `coordinate_tree.py` の docstring が「共有された調整値」と呼ぶものです。`_settle` をバックエンド
   ごとに保つ理由として docstring が今挙げている「正真正銘の戦略上の違い」では、なくなります。この作業
   では、変換によって消える違いを docstring が主張し続けないよう、同じ変更の中で docstring も更新します。

   `_settle` 自体を `CoordinateTreeDriver` へ引き上げるところまで踏み込むかどうかは、実装時の判断に
   委ねます。idb の `query()` は、今も accessibility bridge の固着からの復旧（`_AX_RESET_RETRIES`）を
   担っています。adb の `query()` にはこの復旧がありません。この関わりを見極めてから、2つのメソッドを
   統合すべきかどうかを判断する価値があります。

5. **本項目の動機となった不具合そのものに対して検証します。** `FakeDriver` ベースの高速ゲートスイートである
   `tests/test_driver_conformance.py` に、`extract` がタップ直後に競合を起こす状況を再現する回帰テストを
   追加します。このテストは、値を1拍遅れてツリーへ映すドライバの二重体を使います。
   `tests/test_driver_conformance_ondevice.py` は実機の iOS Simulator バックエンドだけを駆動し、二重体を
   使いません。二重体を使う回帰テストの置き場所としては、高速ゲート側が自然です。

   作業4の変換にも、BE-0245 が Android 側の対応する変更に追加したのと同じ形の、決定的な検証を与えます
   （`tests/test_adb.py:963`〜`1024`）。`tests/test_idb.py` に `FakeClock` を使ったテストを追加します。
   `IdbDriver._settle` が、古い固定ポーリング回数の上限を超えても、ツリーが動いている間はポーリングを
   続けることを確かめます。固定回数ではなく壁時計のデッドラインで諦めることも確かめます。これにより、
   この変換は on-device の再実行だけでなく、高速で決定的なテストによっても証明されます。

   続いて、ショーケースの `extract` シナリオを on-device で CI 上に繰り返し実行します。本項目の動機の中で
   フレークした iOS 向け conformance テスト（`test_delete_text_reduces_the_field_length` と
   `test_tap_point_focuses_the_field_like_a_semantic_tap`）も、同じ方法で繰り返し実行します。そこでも
   フレークが再現しなくなることを確かめるためです。この結果だけを、作業1〜3の証明とは扱いません。

   名前を挙げた2つの conformance テストは、どちらもドライバを直接駆動します。`driver.tap()`、
   `type_text`、`delete_text`、`tap_point`、そして `field_value` と `_field_center`
   （`tests/driver_conformance.py:59`〜`67`）経由の素の `driver.query()` です。作業1〜3が変更する
   `bajutsu/orchestrator/loop.py` の `assert` と `extract` のステップは、一切経由しません。作業4が届く
   のは、各テストが最初に行う `driver.tap({"id": FIELD_ID})` だけです。`_settle()` は、セレクタベースの
   `tap`/`double_tap`/`long_press` の経路である `_center()` からしか呼ばれません。`type_text`、
   `delete_text`、`tap_point`、素の `query()` からは呼ばれないためです。`AdbDriver` も同様です。

   広がった猶予だけで、この2つのテストのフレークが止まるかどうかは、まさに CI での繰り返し実行が確かめる
   対象です。作業1〜3を実際に経由する on-device 側の信号は、ショーケースの `extract` シナリオだけです。
   シナリオの実行は、必ずオーケストレータを経由するためです。

## 検討した代替案

- **`extract` 専用の、読み取るプロパティを意識した読み取りを追加する代わりに、
  `bajutsu/drivers/coordinate_tree.py` の settle 用写像そのものに、あらゆる場面で値、traits、label を
  含めること。** 採りません。この写像は、あらゆるセレクタベースのアクションの、アクション前の settle 待ちで
  共有されています。`extract` が消費するアクション後の読み取りだけのものではありません。`_settle()` は
  `_center()` からしか呼ばれず、`tap`/`double_tap`/`long_press` を支えるだけです。`type_text`、
  `delete_text`、`tap_point`、素の `query()` からは呼ばれません。これは作業5で追跡したとおりです。
  この写像が今日、値、traits、label を除外している理由は、docstring に明記されています。静止した画面上の
  通常のデータ更新のたびに、セレクタベースのアクションの前に余分なポーリングを走らせてはならない、という
  ものです。写像を広げれば、修正が必要だった1つのステップだけでなく、あらゆるセレクタベースのアクションに、
  この余分なポーリングのコストを持ち込んでしまいます。

- **共有ヘルパーではなく、それぞれの読み取り箇所へ個別に再試行ループを書くこと。** 採りません。`assert` の
  呼び出し元と `extract` の呼び出し元にそれぞれ手書きのポーリングを置くと、次に共有ロジックへ修正が
  必要になったとき、2つが食い違ったまま放置されかねません。`_evaluate_expect` のループを一度だけ切り出す
  価値があるのも、同じ理由からです。

- **壁時計デッドラインへ切り替える代わりに、`IdbDriver` の固定ポーリング回数や間隔を増やすこと。**
  採りません。固定の猶予を大きくしても、新しい定数を決めた根拠のマシンよりさらに遅いマシンでは、いずれ
  尽きてしまいます。BE-0245 は、まさにこの失敗のパターンを、Android 側で、実際に動かしているマシンに
  応じて伸び縮みするデッドラインへ置き換えました。

## 進捗

> 作業の進行に合わせてここを最新の状態に保ってください。チェックリストは「詳細設計」の
> MECE の作業分解（作業単位ごとに 1 つのチェック項目）に対応します。ログには、変更内容と
> 日時を古い順に記録し、関連する PR へリンクしてください。

- [x] `_evaluate_expect` のポーリングを、共有して再利用できる形（`_poll_asserts`）に一般化する。
- [x] シナリオ途中の `assert` ステップを、その共有ヘルパー経由にする。
- [x] `extract` の読み取りにも、それに合った形（プロパティを意識した settle）で同じ猶予を与える。
- [x] `IdbDriver._settle` を壁時計デッドライン方式へ切り替え、`AdbDriver._settle` に揃える。
- [x] この項目の動機となった不具合そのものに対して検証する（`tests/orchestrator/test_extract_settle.py`
      の `extract` 競合の回帰テストと、作業単位 4 向けの `FakeClock` を使った `tests/test_idb.py`
      への追加。on-device での CI 繰り返し実行は on-device 側の裏づけとして残ります）。

## 参考

[BE-0245 — adb の読み取り向け常駐 UI Automator サーバ](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)、
[BE-0114 — ドライバ conformance スイート](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)、
[`bajutsu/orchestrator/loop.py`](../../bajutsu/orchestrator/loop.py)、
[`bajutsu/drivers/coordinate_tree.py`](../../bajutsu/drivers/coordinate_tree.py)、
[`bajutsu/drivers/idb.py`](../../bajutsu/drivers/idb.py)、
[`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py)

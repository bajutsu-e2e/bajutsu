[English](BE-XXXX-device-executor-shared-selector-core.md) · **日本語**

# BE-XXXX — RustとUniFFIで、iOSとAndroidの端末側セレクタコアを共有する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-device-executor-shared-selector-core-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Platform support |
| 関連 | [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)、[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)、[BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md)、[BE-0405](../BE-0405-android-identifiertool/BE-0405-android-identifiertool-ja.md)、[BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md)、[BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md)、[BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor-ja.md)、[BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md)
は、iOSとAndroid向けの端末側ステップ実行プロトコルを定義しています。BE-0408は、SwiftとKotlinがそれぞれ
独自にホスト側のセレクタ照合ロジックを持つ必要があると述べています。さらに、2つの実装がホスト側と
まったく同じ結果を返す必要があるとも述べています。本項目は、このロジックをRustで一度だけ書くことを提案します。両プラット
フォームは、[UniFFI](https://mozilla.github.io/uniffi-rs/)が生成するバインディング経由でこれを呼び出し
ます。手書きのSwift移植と手書きのKotlin移植の代わりに、1つのコンパイル済みコアを囲む2つの薄いバインディ
ングを置きます。
[BE-0409](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor-ja.md)の
iOS実行エンジンと
[BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md)の
Android実行エンジンは、それぞれ独自に書いた`matches`、`find_all`、`resolve_unique`を持つ代わりに、同じ
クレートを呼び出すことになります。

## 動機

BE-0408自身の設計は、リスクを明確に名指ししています。端末側の2つの実装は、「ホスト側の実装と同じ要素に
すべてのセレクタを解決」しなければなりません。さらに、「あいまいな一致に対しても同じように失敗」しなけれ
ばなりません。BE-0409とBE-0410は、順序付けでこのリスクに対応しています。iOS側の移植を先に行い、「この
移植が明らかにするギャップを、両プラットフォームが個別に再発見しなくて済むように」する設計です。この順序
付けは、ギャップを再発見するコストを下げます。しかし、2つの移植が最初から食い違うこと自体は防ぎません。
どちらの項目も、`find_all`／`resolve_unique`の移植をまだ始めていません。BE-0409は
BE-0408（今は[Implemented](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md)）
の完了を待っており、BE-0410はBE-0409の完了を待っています。SwiftのコードもKotlinのコードも、
`find_all`／`resolve_unique`の実装は今日の時点でまだ存在しません。だからこそ今は、1つの実装が2つの実装
より安く済む時点です。

本項目が移す関数は、すでに純粋なデータ変換です。プラットフォーム固有の配線ではありません。
[`bajutsu/common/drivers/base/_functions.py`](../../bajutsu/common/drivers/base/_functions.py)の
`matches`、`find_all`、`resolve_unique`、`_collapse_identical_duplicates`は、`Element`のリストと
`Selector`を受け取ります。どちらも、文字列・リスト・タプルだけの単純な辞書です。各関数は、真偽値か、
絞り込んだリストか、単一の要素か、送出したエラーのいずれかを返します。この4つの関数は、アクセシビリティツリーを
読みません。タップも注入しません。ソケットも開きません。プラットフォーム固有の処理は、すべてこの関数の
外側で起きています。今日はPythonドライバの中で、実装後は両方の端末実行エンジンの中でです。プラット
フォームに依存しない関数が、独立に保守された3つのコピーとして存在する理由はありません。

独自に移植したときのリスクは、ふつうの実装ズレより深刻です。`idMatches`はPythonの
`fnmatch.fnmatchcase`を使います。`labelMatches`はPythonの`re.compile(...).search`を使います。独自の
Swift移植は`NSRegularExpression`とFoundation自身のglob処理に頼るでしょう。独自のKotlin移植は
`java.util.regex`とその独自の挙動に頼るでしょう。3つのエンジンが、シナリオ作者自身のパターンが何に
一致するかを、それぞれ別のルールで決めることになります。文字クラス、アンカリング、Unicodeプロパティの
扱いは、エンジンごとに違います。ホストでは通るのに端末実行エンジンでは落ちるセレクタは、大きなエラーで
はなく不安定なテストとして表面化します。別の要素に一致してしまう場合も同様です。誤った一致は、呼び出し
側から見て正しい一致と区別がつきません。Rustのエンジンを1つ共有すれば、この数は独立した3エンジンから
2エンジンへ減ります。ホスト側ではPythonの`re`と`fnmatch`が引き続き検証済みの基準であり続けます。Rustコア
は、端末実行エンジンが走らせるもう1つのエンジンになります。

実装が終われば、後の読者はこの結果を直接確かめられます。BE-0408は、それを確かめるためのフィクスチャを
すでに出荷しています。[`tests/fixtures/be0408/`](../../tests/fixtures/be0408/)の42件のセレクタ解決
ケースと9件の派生ラベルケースで、
[`tests/test_selector_fixtures.py`](../../tests/test_selector_fixtures.py)がPythonの実装と照合して
います。現行の計画では、SwiftとKotlinそれぞれの移植ができた時点で、この同じフィクスチャを別々に実行
することになります。それぞれの移植は全フィクスチャに通りながら、フィクスチャが網羅していないケースで
互いに食い違う可能性があります。新しいセレクタ規則（新しいtrait、新しいフォールバック）を追加すると
きの手間も変わります。共有コアなら、1回のRustの変更を両方の実行エンジンが次のバイナリ更新で拾います。
現行の計画では、記憶だけを頼りに歩調を合わせる、2回の手書きの変更が必要です。

## 詳細設計

**実装順序。** 本項目は、BE-0407→BE-0408→BE-0409→BE-0410という4項目の並びを拡張します。
BE-0408は[Implemented](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md)
（[#1949](https://github.com/bajutsu-e2e/bajutsu/pull/1949)）です。本項目が置き換える手順は、コード
としてではなく、文章としてすでに出荷済みです。
[`docs/selectors.md`](../../docs/selectors.md)に新設された「端末側リゾルバの移植契約」節が、
フィールドレベルのセレクタ契約（`within`、`idMatches`、traitの導出、重複排除のキー）を、2つの独立した
SwiftとKotlinの実装が一致できるだけ精密に述べており、動機の節が挙げたフィクスチャ一式でこれを裏づけて
います。本項目が置き換えるのは、まだ着手していない手順ではありません。「これを手作業で正しく再現する」
というすでに出荷済みの文章による契約を、どちらの移植も手作業で再現する必要のないコンパイル済みクレート
へ置き換えます。着手のタイミングはBE-0408への後続作業としてであり、BE-0409またはBE-0410がプラット
フォーム側の照合コードを書き始める前です。そうしないと、両者とも本項目が無用にしようとしている契約を
相手に、手作業での移植を始めてしまいます。本項目が着地すると、BE-0409とBE-0410が互いに順序付けている
理由も消えます。両者とも同じ検証済みのクレートを呼ぶだけになるため、どちらかの手書き移植を先に待つ必要
がなくなります。本項目のクレートと2つのバインディングができたあとは、BE-0409とBE-0410はどちらの順で
進めても、並行に進めてもかまいません。

**クレートで実装し直す関数と、ホスト側だけに残す関数。**
[`bajutsu/common/drivers/base/_functions.py`](../../bajutsu/common/drivers/base/_functions.py)の
9つの関数を、Rustで実装し直します。Pythonの実装は、適合スイートの基準としてそのまま残ります。対象は
`matches`、`find_all`、`resolve_unique`、`_collapse_identical_duplicates`、`contains`、
`topmost_at_point`、`redirect_candidates`、`raise_if_covered`、`frame_center`です。これらは、セレクタ
解決と、BE-0408が端末へ移すアクチュエーション種別の1つである`tap`の被覆判定をカバーします。動機の節で
示したとおり、各関数は純粋です。`Element`と`Selector`の組（またはそのリスト）を受け取り、真偽値・
インデックスまたはそのリスト・座標・送出したエラーのいずれかを返します。

FFI（foreign function interface）境界を越えること自体が理由で、シグネチャが2箇所変わります。Python
のオブジェクト同一性には、境界の向こう側に対応物がないためです。1つ目は、`find_all`が`Element`の
リストの代わりに`Vec<u32>`（`elements`の中でのインデックス）を返し、`resolve_unique`が`Element`の
代わりに`u32`を1つ返す変更です。UniFFIのレコードはこの境界を値として越えるため、返された`Element`は
コピーにすぎず、呼び出し元がそこから解決した先のプラットフォーム側ハンドル（`XCUIElement`や
`AccessibilityNodeInfo`）へたどる手がかりを持ちません。インデックスであれば、呼び出し元は自分が組み
立てたリストから、`Element`と自分自身のハンドルの両方を同じ位置で引けます。2つ目は、`topmost_at_point`、
`redirect_candidates`、`raise_if_covered`が、`Element`の代わりに`target_index: u32`を受け取る変更
です。理由は同じです。Pythonは`target`引数を`elements`の中からオブジェクト同一性（`is`）で探して
います。等価性では探していません。これは意図的な設計です。内容が同一の2要素（既知のXCUITestの重複
登録）があっても、呼び出し元が実際に手にしている一方だけを指し続ける必要があるためです。インデックス
はこの区別を保ちますが、値としてコピーされたレコードはこの区別を保てません。同じ理由で、
`redirect_candidates`は戻り値も`Vec<u32>`にします。呼び出し元は選んだ子孫要素に対してアクチュエーション
を行うため、今日の`XcuitestDriver._tap_sole_reachable_descendant`が`handles[id(el)]`で行っているのと
同じ同一性ルックアップを必要とします。値としてコピーされたレコードはこれを壊すという点で、
`find_all`が返す要素と事情は同じです。`topmost_at_point`の`Element`を返す仕様はコピーのままで構いません。
`raise_if_covered`はその識別子とフレームをメッセージへ整形するだけで、下流の誰もプラットフォーム側
ハンドルへたどり直す必要がないためです。

セレクタのパターン照合には、シグネチャの変更だけでなく、使うエンジンの名指しも必要です。`idMatches`は
Pythonの`fnmatch.fnmatchcase`を、`labelMatches`は`re.compile(...).search`を使っています
（[`_functions.py:112-122`](../../bajutsu/common/drivers/base/_functions.py)）。クレートは
`labelMatches`に[`regex`](https://docs.rs/regex)クレートを使い、`idMatches`には`fnmatch`自身が内部で
行っているのと同じアプローチ、つまりシェルのglobパターンを正規表現文字列へ翻訳する処理を移植したうえで、
同じ`regex`クレートでコンパイルします。こうすることで、クレートが抱えるパターンエンジンをちょうど1つに
絞れます。この翻訳だけでは足りません。`fnmatch.translate`が出す終端アンカーは`\Z`ですが、`regex`は
これを認識しません（`regex`が持つのは`\z`だけです）。さらに`fnmatchcase`の先頭アンカーはパターン自体
ではなく`re.match`の呼び出しから来ており、`regex`側の照合はアンカーなしです。そのためクレートの
`idMatches`は、翻訳済みパターンの`\Z`を`\z`へ書き換え、コンパイル前に全体を`^(?:…)\z`で包みます。
これは[`docs/selectors.md`](../../docs/selectors.md)自身の指示、つまりポートは各フィールドについて
ポート先言語自体の既定のアンカリングではなくPythonのアンカリングを再現しなければならない（`idMatches`
は完全アンカー、`labelMatches`はアンカーなし）という指示を再現したものです。

`labelMatches`には、`idMatches`にはない独自のギャップがあります。`regex`は、Pythonの`re`が受け付ける
複数の構文を拒否します。先読み・後読み（`(?=…)`、`(?!…)`）、後方参照、文字列終端アンカー`\Z`、原子
グループと所有量指定子、そして条件付きパターン（`(?(id)yes|no)`）です。これらのいずれかを使った
`labelMatches`のパターンは、ホストではコンパイルできますが、クレートではコンパイルできません。これは、
本項目がなくそうとしているホストと端末の食い違いが、なくなったのではなく場所を移しただけということ
です。単なる`$`は、拒否されるより厄介です。両方のエンジンでコンパイルできてしまい、しかも意味が違う
からです。Pythonの`$`（`MULTILINE`なし）は文字列末尾の直前の改行1つの手前にも一致しますが、`regex`の
`$`は文字列の真の末尾にしか一致しません。`regex`には先読みがなくPythonの挙動を表現できないため、
クレートは`$`を静かに違う一致へコンパイルさせるのではなく、そのまま拒否します。クレートは、この節の
どの構文についても、呼び出し元が「要素が存在しない」場合と区別できない静かな不一致にせず、初回使用時の
明確で大きな失敗として扱います。この失敗は次節のエラー列挙型に組み込みます。フィクスチャ一式に追加する
1件のケース（検証の節を参照）がそのようなパターンを固定しておけば、どちらかのエンジンへの将来の変更が
このギャップを再び開いたとき、不安定なシナリオとして表面化する前にスイートで検出できます。

`gesture_anchor`も同じく純粋ですが、本項目の範囲外です。BE-0408が端末へ移すのは`tap`、`type`、`swipe`、
`scroll`であり、`gesture_anchor`が支点を計算する2本指の`pinch`／`rotate`ではありません。この関数を今
移植すると、本項目のプロトコルがまだ到達していない段階のための実装になってしまいます。

`deadline_ticks`と`wait_until`も移しません。どちらも、単発の判定の周りにホスト側のポーリングループを
実装したものです。BE-0408のステージ1は、ホストにポーリングの手段を公開する代わりに、端末側で内部的に
ポーリングさせる設計です。端末実行エンジンに必要なのは、共有した照合関数を包む、プラットフォームごとの
イベント駆動またはタイマー駆動のループです。ホストのポーリングループをそのまま移植したものではありませ
ん。すべてのバックエンドの`wait_for`が委ねる単発判定`default_wait_for`にも、同じ理由から個別の移植は
要りません。この関数の中身は`find_all(...).len() >= 1`そのものであり、共有した`find_all`に対して各
実行エンジン自身のネイティブなループが直接この判定を表現できます。`id_candidates`、
`validate_id_candidates`、`permission_capability`、`native_z_from_json`もPythonに残ります。
`id_candidates`が残る理由はデータモデルの節で述べます。残る3つの理由はもっと単純です。これらは、
シナリオの記述やエビデンスの解析といった、端末実行エンジンが担わない作業に使うためです。

**データモデル。** [`Element`](../../bajutsu/common/drivers/base/element.py)は、フィールドごとに
UniFFIの辞書型レコードになります。`identifier`、`label`、`value`はオプショナルな文字列に、`traits`
は文字列のリストに、`frame`は`(x, y, w, h)`の4フィールドのレコードに、`nativeZ`はオプショナルな浮動
小数点数になります。[`Selector`](../../bajutsu/common/drivers/base/selector.py)は、単純な改名だけ
では済みません。`id`と`idMatches`は、Pythonでは単一の文字列かリストのどちらも受け取ります
（`str | list[str]`、BE-0221のOR候補形式）。UniFFIにはこの種の合併型がないため、レコードの境界では
どちらも`Option<Vec<String>>`になります。フィールドが不在なら`None`です。これはPython自身のフィールド
存在チェック（`"id" in sel`）と対応し、空リストを不在の代わりに使うわけではありません。単一の値を
1要素のリストへ包むのは1行で済む作業であり、わざわざ
移植する価値はありません。そのため各呼び出し元がこの処理を済ませます。今日同じ処理を担う
Pythonのヘルパー`id_candidates`は、クレートへ移さずホスト側に残る理由がここにあります。クレート内部
の`matches`と`find_all`は、すでに正規化されたリストだけを見ます。`within`は、`Selector`が自分自身の
`within`フィールドを入れ子として持てるため、`Option<Box<Selector>>`になります。`index`は`u32`では
なく`Option<i32>`になります。Pythonでは末尾からの位置を表す負の値を受け付けるためです
（`_functions.py:322-325`）。Pythonの`total=False`な辞書でのフィールドの不在は、両側で同じ`None`
になります。

[`Trait`](../../bajutsu/common/drivers/base/trait.py)の6つの文字列定数、`button`、`link`、
`notEnabled`、`selected`、`other`、`secureTextField`は、両側でそのまま文字列として渡ります。これは、
PythonのコードとJavaScript object notation（JSON）のワイヤフォーマットがすでに使っている形と一致します。
そのため、7つ目の定数が加わっても、歩調を合わせる新しい列挙型は不要です。
[`ElementNotFound`](../../bajutsu/common/drivers/base/element_not_found.py)、
[`AmbiguousSelector`](../../bajutsu/common/drivers/base/ambiguous_selector.py)、
[`ElementNotTappable`](../../bajutsu/common/drivers/base/element_not_tappable.py)は、前節のパターン
エンジンのギャップのために追加する4つ目のバリアント`UnsupportedPattern`（フィールド名とパターン自体を
持ち、`regex`クレートがコンパイルできない`labelMatches`の値を表す）とあわせて、1つのUniFFIエラー
列挙型になります。今日のPythonにはこれに相当するものはありません。`re.compile`はこの4つ目のバリアント
が存在する理由となるパターンをすべて受け付けるためで、これに当たるのは端末実行エンジンだけです。
残る3つのバリアントは、整形済みのメッセージ文字列ではなく、型付きフィールドとしての構造化された失敗
の詳細を持ちます。`ElementNotFound`はセレクタと、`noMatch`または`outOfRange`のいずれかを表す`reason`
を持ちます。`resolve_unique`は今日、「インデックスが範囲外」（`_functions.py:324`）と「一致なし」
（`:327`）という2つの異なるメッセージを送出しており、BE-0408自身のフィクスチャ一式もすでにこの同じ
区別を`resolveUnique.reason`と名付けているため、このバリアントは新しい名前を発明せずその名前を再利用
します。`AmbiguousSelector`はセレクタと候補数を持ちます。`ElementNotTappable`はセレクタに加えて、
被覆した要素の識別子・ラベル・フレームを持ちます。`raise_if_covered`は
`covering["identifier"] or covering["label"] or "<unnamed>"`をメッセージへ整形しており
（`_functions.py:452`）、このフォールバックを再現するには識別子だけでなく両方のフィールドが必要だから
です。これらのフィールドを、今日実行レポートが表示するメッセージ文へ整形するのはホスト側であり、
デバイス側ではありません。SwiftやKotlinの呼び出し元は、整形前のバリアントをそのまま既存のエビデンス
経路へ渡すため、端末実行エンジンで失敗したステップは、今日ホストで失敗した同じステップとまったく同じ
見え方になります。

`_collapse_identical_duplicates`は引数を追加せずに移植します。クレート側の実装も、ホスト側とまったく
同じように、許容差なしでフレームの完全一致を比較します。`docs/selectors.md`の移植契約は、この関数に
ついてもう1つ、許容差とは別の規則を「重複排除のキー」という節に持っています。この排除は、
`find_all`自身の出現順で、あるキーを最初に持った候補を残すというものであり、単純なハッシュマップには
それを保つための決まった反復順序がないため、契約自身がSwiftの`Dictionary`を具体的な落とし穴として
名指ししています。Rustの`std::collections::HashMap`は同じ落とし穴であり、しかもさらに厄介です。
既定のハッシャーはプロセスごとに再シードされるため、反復順序はプラットフォーム間だけでなく、同じ
バイナリの実行のあいだでも変わりえます。そのためクレート側の移植では、この排除に`HashMap`ではなく
挿入順を保つマップ（`indexmap`クレートの`IndexMap`など）を使い、ホスト側の`seen`辞書と同じキーで
持たせます。`docs/selectors.md`の移植契約は、許容差そのものを分岐させたまま保つべき理由を、「守るべき
であり閉じてはならない2つの相違」という節で述べています。
[`PositionPath.swift`](../../BajutsuKit/Sources/BajutsuRunner/PositionPath.swift)の
`resolvableMatchingIndex`と`attributesMatch`は、すでに意図的にこのモジュールから外れています。
フレームの各値に1ポイントの許容差を認める点と、traitを集合ではなく順序ありの配列として比較する点です。
どちらも、`resolvableMatchingIndex`が別の問いに答えているために存在します。すでに記録済みの要素ハンドル
を、新しいライブ読み取りで再解決してもなお同じ要素かという問いです。一方`_collapse_identical_duplicates`
は、「これらの候補は1回の原子的なスナップショットから見て内容が同一か」という問いに答えます。移植契約
自身の文章は、ポートが「どちらか一方もPython自身の挙動へ『修正』してはならない」と明言しています。
本項目のクレートは、そのため`_collapse_identical_duplicates`だけで止まります。`resolvableMatchingIndex`
と`framesEqual`はSwift側にそのまま残り、本項目はこれに触れません。ここでのどの進捗ステップも、
両者を1つに畳み込むことを提案してはなりません。

Androidの派生ラベルのフォールバック（`_derived_label`。
[`bajutsu/common/drivers/adb/_functions.py`](../../bajutsu/common/drivers/adb/_functions.py)にあり、
`_to_element`の中で適用）は、パイプライン中の現在の位置にそのまま留まります。共有した照合コードが読む
前に、`Element`へラベルを計算して
おく処理です。これは、今日のPythonドライバでも、BE-0410実装後の`BajutsuAndroidUIAutomatorServer`の
Kotlin側呼び出し元でも同じです。共有クレート側にAndroid固有の分岐は不要です。呼び出し側がそれぞれ、
自分のプラットフォームの生の読み取り結果を素の`Element`へ正規化してから、1つの共有コアを呼ぶという
構造だからです。ここには、手書きのKotlin移植が1つ残ります。`_derived_label`自体にはRust側の対応物が
ないため、Pythonの実装と歩調を合わせ続ける必要があります。その出力は後続処理のない単一の文字列で
あり、BE-0408の9件の
[`android_derived_label.json`](../../tests/fixtures/be0408/android_derived_label.json)ケースがすでに
これをカバーしています。この残存リスクは、照合パス全体を独立に移植していた場合よりはるかに小さく
済みます。

**iOS: `BajutsuRunner`。** パッケージマニフェストは、リポジトリのルートにある
[`Package.swift`](../../Package.swift)であり、`BajutsuKit/`配下のファイルではありません。SwiftPMの
git連携解決の都合でクローンのルートに置く必要があり、各ターゲット自身の`path:`が`BajutsuKit/Sources/`
配下を指し戻す形になっています。このマニフェストはすでに、Swift Package Managerのビルドプラグイン
`OpenAPIGenerator`を使い、`BajutsuRunner`のソースをビルドするたびに、どのホストでも生成しています。
`uniffi-bindgen`が生成するSwiftファイルは、これとは別のもう1つのソース生成経路です。違いは1点です。
ネイティブ側の実体が、プラグインの都度コンパイルではなく、あらかじめビルド済みの`.xcframework`成果物
である点です。`cargo build`の対象は、Simulator向けの
`aarch64-apple-ios-sim`と`x86_64-apple-ios`、[BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md)
の実機対応向けの`aarch64-apple-ios`、そして
[`swift.yml`](../../.github/workflows/swift.yml)のSimulatorを使わない素のApple Silicon macOSランナー
（`swift build --package-path .`と`swift test --package-path .`）が今日どおりこのパッケージのビルド・
テストを続けられるようにする`aarch64-apple-darwin`です。`x86_64-apple-darwin`のスライスはビルドしません。
これは対応ホストの範囲を1つ狭めます。Intel Macは、シナリオの実行に必要なSimulatorスライスこそ持ち
続けますが、今日のようにパッケージをネイティブにビルド・ユニットテストできなくなります。
`swift.yml`がすでにApple Siliconだけで動いているため、本項目はこの狭まりを受け入れます。
`uniffi-bindgen`が生成するのはSwiftバインディング、ヘッダー、モジュールマップだけであり、
`.xcframework`のスライスを統合する機能は持ちません。それを行うのは`lipo`（ユニバーサルSimulator
スライス用）と`xcodebuild -create-xcframework`です。これらが、4つの`cargo build`成果物を統合した
`.xcframework`（2つのSimulatorターゲットは1つのユニバーサルスライスへまとまるため、実質3系統の
プラットフォームスライス）を作り、それを`Package.swift`にバイナリターゲットとして追加し、
`BajutsuRunner`はそれに依存します。

このマニフェストが1つしかないことは、このバイナリターゲットが無償ではない理由でもあります。
`Package.swift`は`BajutsuKit`と`BajutsuRunner`という2つのプロダクトを、1つのターゲットグラフから
公開しています。同じグラフを、`BajutsuKit`プロダクトだけに依存するアプリも解決します。ビルド時
プラグインと違い、バイナリターゲットはSwiftPMのリゾルバがマニフェスト全体に対して所在確認・検証を
行う対象であり、消費者が実際にリンクするプロダクトだけに絞られません。そのため、`BajutsuRunner`に
一切触れないアプリでも、使いもしない成果物の解決時コストを支払う可能性があります。これは、
「`BajutsuKit`というアプリ組み込みライブラリをこのビルド手順から自由に保つ」という本項目自身の狙いに
逆行します。BE-0405が`IdentifierTool`を`BajutsuAndroid`の依存から切り離しているのと同じ意味です。
このコストをそのまま受け入れるか、`BajutsuRunner`を切り離すための独自のマニフェストを持たせるかは、
本項目がここで決め切らず実装に委ねる、開かれた論点です。進捗の節はこれを決定としてではなく、
論点として名指しします。

`BajutsuRunner`は、bajutsu自身が同梱するテストランナーです。
[BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner-ja.md)のコンテンツハッシュ
方式のキャッシュを通じて、開発者の手元にはビルド済みの状態で届きます。利用側プロジェクトのビルド内で
コンパイルされることはありません。したがって、このビルド手順が走るのはbajutsu自身のリリースパイプライン
の中だけです。テスト対象アプリ自身のビルドの中では走りません。

**Android: `BajutsuAndroidUIAutomatorServer`。**
[`BajutsuAndroidUIAutomatorServer/server/build.gradle.kts`](../../BajutsuAndroidUIAutomatorServer/server/build.gradle.kts)
は、この実行エンジンが拡張するのと同じインストルメンテーションのために、すでに`androidx.test.uiautomator`
に依存しています。このモジュールにネイティブの依存を加えても、増えるのは依存の「種類」です。依存を
許容する度合いを新たに広げるわけではありません。ただし、これはモジュールが届く範囲そのものへの変更
でもあります。このインストルメンテーションAPKは今日ネイティブコードを持たないため、`minSdk = 26`が
まだ許している32ビットの`armeabi-v7a`や`x86`を含め、端末が提供するどのABI（application binary
interface）にもインストールできます。`cargo-ndk`は、`arm64-v8a`と、
[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)のエミュレータレーン
向けの`x86_64`へクレートをクロスコンパイルします。これは、この範囲を初めて狭める変更です。32ビット
端末は、常駐サーバをもう実行できなくなります。本項目は、4つ全部のABI向けにビルドするのではなく、
この狭まりを受け入れます。32ビットのみのAndroid端末は、2019年以降のPlayストアがアプリからすでに受け付け
なくなっており、常駐サーバ自身の端末カバレッジは、今日実際のターゲットアプリが配布
できる範囲に揃うことになります。`cargo-ndk`が生成した`.so`ファイルは`jniLibs`の下に置きます。
`uniffi-bindgen`は、実行エンジンが直接呼ぶKotlinバインディングを生成します。このビルドファイル自身
のコメントは、サーバが
「dependency-light（依存を軽く保つ）」だとすでに述べています。理由は、HTTPとJSONのどちらのライブラリも
持たない、自己完結したインストルメンテーションだからです。これは、不要な依存を避けるという方針の表明
です。この実行エンジンが実際に必要とするネイティブの依存を禁じるものではありません。

**検証。** [`tests/fixtures/be0408/`](../../tests/fixtures/be0408/)は、このクレートに必要な言語非依存
のフィクスチャ一式として、すでに存在しています。`selector_resolution.json`のスキーマ付き42ケース
（それぞれ`elements`のリスト・1つの`selector`・Pythonが出す`findAll`／`resolveUnique`の結果を持ちます）
と、`android_derived_label.json`の9ケースを、
[`tests/test_selector_fixtures.py`](../../tests/test_selector_fixtures.py)が今日すでに変更のたびに
Pythonの実装と照合しています。小さなRustのバイナリ（CIホスト自身のアーキテクチャ向けにビルドし、
wheelと常駐サーバのどちらにも同梱しません）が、同じJSONから1ケース分の`Element`リストと`Selector`を標準
入力で読み、一致したインデックス、またはエラーのバリアントとその構造化された詳細を標準出力へ書きます。
`test_selector_fixtures.py`は、この42ケースをこのバイナリ経由でも走らせる、2つ目の照合経路を持つよう
になります。すでにPython経由で走らせている経路と並んで動くため、双方が1つの共有フィクスチャ一式に
対して検証され続け、独立に管理された別々のフィクスチャ一式にはなりません。

このフィクスチャ一式は、そのままでは`find_all`と`resolve_unique`（内部で`matches`と
`_collapse_identical_duplicates`を使います）をカバーしますが、本項目が移植する残り5つの関数は、
`selector_resolution.json`の「`elements`＋`selector`」という形では表せません。`contains`（2つの
`Frame`）、`frame_center`（1つの`Frame`）、`topmost_at_point`（1つの`Point`と対象のインデックス）、
`redirect_candidates`と`raise_if_covered`（セレクタに加えて対象のインデックス）です。スキーマにこれら
のためのバージョン2を加えます。ケースごとの`function`フィールドが9関数のうちどれを検証するかを選び、
各ケースはその関数固有の追加入力（座標、対象のインデックス、2つ目のフレーム）を、既存の`elements`
リストと並べて持ちます。`test_selector_fixtures.py`は、すでに`findAll`／`resolveUnique`の有無で
分岐しているのと同じやり方で`function`によって分岐し、この1つのフィクスチャ一式と1つの照合テストが、
2つ目の仕組みへ分裂することなく9関数すべてをカバーするよう育ちます。BE-0409とBE-0410が実装された
あとは、同じ一式を3回目として、各プラットフォームのバインディング経由でも実行します。そこでフィク
スチャが落ちたときは、共有ロジック自体を疑い直す前に、まず「バインディング側」だと絞り込めます。

## 検討した代替案

- **BE-0408が出荷した計画を最終形のまま残す案。文章による移植契約とフィクスチャ一式、そしてそれに照らして
  検証する、独立に書いたSwift・Kotlinの2つの移植です。** 唯一の安全策としては採用しません。BE-0408自身
  がすでに名指ししているリスクは残ります。2つの独立した実装が、あいまいな一致でどの候補を報告するかまで
  含めて完全に一致しなければならない、というリスクです。このリスクは、設計で取り除かれないまま残ります。
  今後セレクタ規則を追加するたびに、テストの失敗という事後の発見だけを頼りに、2つの手書きパッチの歩調を
  合わせる必要があります。
- **同じRustコアをPyO3経由でPythonにも広げ、3言語を1つの実装にまとめる案。** 採用しません。`bajutsu`の
  pipパッケージは、今日は純粋なPythonです。AI SDKとPlaywrightを基本の依存にせず、オプトインの拡張に
  とどめる方針をすでに取っています
  （[BE-0111](../BE-0111-ai-sdk-optional-dependency/BE-0111-ai-sdk-optional-dependency-ja.md)）。
  基本インストールへネイティブ拡張を加えると、`pip install bajutsu`のたびにクロスプラットフォームの
  wheelビルドが必要になります。[maturin](https://github.com/PyO3/maturin)のようなツールが、そのビルド
  を担います。このビルドは、上記のiOS・Androidのビルドとは違い、bajutsu自身のリリースパイプラインの
  中だけでなく、インストールのたびに走ります。Pythonの実装は、すでに適合スイートがRustコアを照合する
  検証済みの基準です。本項目の動機は、Python側を変える理由になりません。
- **同じRustコアを、BajutsuKitとBajutsuAndroidのアプリ組み込みコレクタ（`BajutsuNet`、`BajutsuZOrder`、
  クリップボードのレシーバ）にも広げる案。** 採用しません。これらのコンポーネントのプラットフォームフック
  機構は、収集先へPOSTするJSONペイロードの形という薄い契約以外に、共有できるロジックを持ちません。片方は
  `URLProtocol`のswizzling、もう片方はOkHttpの`Interceptor`です。片方はループバックのHTTPサーバ、もう
  片方は`AccessibilityNodeInfo`のextra-dataです。ここに共有Rustコアを持ち込んでも、置き換わるのはその
  薄い契約だけです。双方でゼロからのプラットフォーム固有実装が引き続き必要になります。`BajutsuRunner`と
  `BajutsuAndroidUIAutomatorServer`はbajutsu自身のテスト基盤として動きますが、これらのライブラリはテスト
  対象アプリの中に同梱されます。
  [BE-0405](../BE-0405-android-identifiertool/BE-0405-android-identifiertool-ja.md)は、そこに置く
  `IdentifierTool`を、依存を持たない最小フットプリントの設計にすでにコミットしています。同梱したRustの
  静的ライブラリは、その設計と噛み合いません。
- **1つの仕様言語からSwiftとKotlinの同等なソースを生成する案。両プラットフォームがリンクする1つのネイ
  ティブライブラリをコンパイルする代わりに。** 採用しません。独立にコンパイルされた2つの生成コードは、
  言語ごとのコード生成バックエンドがそれぞれ固有のバグを抱えれば、それでも食い違いえます。これは、本項目
  がなくそうとしている失敗のパターンそのものです。このリポジトリには、この水準のコード生成バックエンド
  の前例がありません。一方UniFFIは、Rustのコアを1つ用意し、プラットフォームごとにホスト言語のバインディ
  ングを生成する形のために存在するツールです。しかも、保守が続いています。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `rust/selector-core/`にクレートの雛形を用意します。`Element`、`Selector`、`Trait`をUniFFIレコード
  として、3つのセレクタ関連エラーと`UnsupportedPattern`を合わせて1つのUniFFIエラー列挙型として、
  それぞれ用意します。
- [ ] `idMatches`と`labelMatches`の照合を、[`regex`](https://docs.rs/regex)クレート1本の上に実装します。
  `idMatches`には`fnmatch`自身のglob→正規表現変換ロジックを移植し、その`\Z`を`\z`へ書き換えたうえで、
  コンパイル前に全体を`^(?:…)\z`で包みます（Pythonの`re.match`が担う先頭アンカーも再現するため）。
  `labelMatches`はアンカーなしでコンパイルし、先読み・後読み、後方参照、`\Z`、原子グループ、所有量
  指定子、条件付きパターンに加えて`$`も拒否し、`regex`がコンパイルできないこれらのパターンには
  `UnsupportedPattern`を返します（`regex`には先読みがなく、Pythonの`$`が持つ「末尾直前の改行を許容
  する」挙動を再現できないためです）。
- [ ] [`bajutsu/common/drivers/base/_functions.py`](../../bajutsu/common/drivers/base/_functions.py)から、
  9つのセレクタ・幾何関数を移植します。`find_all`と`redirect_candidates`は`Vec<u32>`を、
  `resolve_unique`は`u32`を（いずれも呼び出し元の`elements`へのインデックス）返し、
  `topmost_at_point`・`redirect_candidates`・`raise_if_covered`は`Element`の代わりに
  `target_index: u32`を受け取るよう変更します。`_collapse_identical_duplicates`は引数を追加せず、
  ホスト側とまったく同じフレームの完全一致で移植し、排除のキー付けには`std::collections::HashMap`
  ではなく挿入順を保つマップ（`indexmap::IndexMap`など）を使います。`HashMap`の反復順序はプラット
  フォーム依存であるだけでなく、プロセスごとに再シードされるためです。`docs/selectors.md`の移植契約は、
  フレーム許容差の相違を`resolvableMatchingIndex`から意図的に切り離しておくためのものなので、ここでの
  手順が両者を1つにまとめようとしてはなりません。
  - `matches`
  - `find_all`
  - `resolve_unique`
  - `_collapse_identical_duplicates`
  - `contains`
  - `topmost_at_point`
  - `redirect_candidates`
  - `raise_if_covered`
  - `frame_center`
- [ ] [`tests/fixtures/be0408/`](../../tests/fixtures/be0408/)のスキーマにバージョン2を加えます。
  ケースごとの`function`フィールドと、各関数固有の追加入力（座標、対象のインデックス、2つ目のフレーム）
  を持たせ、バージョン1の「`elements`＋`selector`」という形では表せない5つの関数（`contains`、
  `frame_center`、`topmost_at_point`、`redirect_candidates`、`raise_if_covered`）をカバーします。
  [`tests/test_selector_fixtures.py`](../../tests/test_selector_fixtures.py)を拡張し、`function`に
  よって分岐して新しいケースをPythonの実装と照合します。バージョン1をすでに照合しているのと同じ
  やり方です。
- [ ] [`tests/fixtures/be0408/`](../../tests/fixtures/be0408/)の1ケースをJSONで標準入力から読み、一致
  したインデックス、またはエラーのバリアントとその構造化された詳細を標準出力へ書く、CLI形式の適合
  ランナーバイナリを構築します。`test_selector_fixtures.py`に、既存のフィクスチャ一式をこのバイナリ
  経由で走らせる2つ目の照合経路を、Python経由の経路と並べて追加します。
- [ ] `cargo`、`uniffi-bindgen`、`lipo`、`xcodebuild -create-xcframework`を`BajutsuKit`のSwift Package
  ビルドに組み込みます。`BajutsuRunner`がリンクする`.xcframework`（Simulator、実機、macOS向けの4つの
  `cargo build`成果物を統合した、実質3系統のプラットフォームスライス）バイナリターゲットを生成します。
  このバイナリターゲットを追加する前に、リポジトリのルートにある`Package.swift`が1つのターゲットグラフ
  しか持たないことにより、`BajutsuKit`プロダクトだけに依存するアプリもこのターゲットを解決してしまうか
  どうかを確認し、確認できたなら、そのコストを受け入れるか、BE-0405が`IdentifierTool`を
  `BajutsuAndroid`から切り離しているのと同じように、`BajutsuRunner`へ独自のマニフェストを持たせて
  切り離すかを決めます。
- [ ] `cargo-ndk`と`uniffi-bindgen`を`BajutsuAndroidUIAutomatorServer`のGradleビルドに組み込みます。
  実行エンジンがリンクする、Kotlinバインディングと`arm64-v8a`／`x86_64`向け`jniLibs`を生成します。
- [ ] `rust/selector-core/`向けにRustのCIレーン（`cargo test`、`cargo fmt --check`、`clippy`）を追加し、
  `make check`が直接これを呼ぶか、別のワークフローに任せるかを決めます。
- [ ] `docs/selectors.md`の「端末側リゾルバの移植契約」節（および
  [日本語版](../../docs/ja/selectors.md)）を更新し、`find_all`・`resolve_unique`・
  `_collapse_identical_duplicates`が手書きのSwift・Kotlin移植ではなくこのクレートに由来すると述べます。
  「守るべきであり閉じてはならない2つの相違」節はそのまま残します。この2つの相違が説明しているのは
  `resolvableMatchingIndex`であり、本項目はこれに触れないためです。BE-0409・BE-0410の「Swift／Kotlin
  へ移植する」手順を、コンパイル済みバインディングを呼ぶ形に更新します。BE-0410の派生ラベル移植は、
  `_derived_label`が共有コアより手前で正規化を行うため、独立したステップとして残します。あわせて、
  iOS側移植を先に行うという順序付けを、BE-0409の詳細設計と、BE-0410の実装順序・「順序ステータス」の
  両方から取り除きます。BE-0409の「順序ステータス」はBE-0408ではなく本項目を指すよう、BE-0410の
  「順序ステータス」はBE-0409ではなく本項目を指すよう、それぞれ付け替えます。どちらも、互いの完了順
  ではなく本項目のクレートに依存するためです。
- [ ] `roadmap-id`ワークフローがmain上で本項目のIDを割り当てたら、BE-0408・BE-0409・BE-0410へ相互の
  「関連」リンクを反映します。

## 参考

[BE-0111 — AI SDKをオプトインの依存にする](../BE-0111-ai-sdk-optional-dependency/BE-0111-ai-sdk-optional-dependency-ja.md)、
[BE-0407 — 証拠読み取りの重複排除とドライバ内部の調整によるステップ実行の高速化](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning-ja.md)、
[BE-0114 — backend 非依存の挙動を検査する driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)、
[BE-0208 — AndroidエミュレータのCI](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)、
[BE-0238 — iOS端末クラウド実行](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md)、
[BE-0292 — XCUITestの同梱ランナー](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner-ja.md)、
[BE-0405 — Android向けIdentifierTool](../BE-0405-android-identifiertool/BE-0405-android-identifiertool-ja.md)、
[BE-0408 — ステップレイテンシのための端末側実行プロトコル](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol-ja.md)、
[BE-0409 — XCTest ランナー内の iOS 端末側ステップ実行機](../BE-0409-step-latency-ios-device-executor/BE-0409-step-latency-ios-device-executor-ja.md)、
[BE-0410 — Androidの端末側ステップ実行エンジン](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor-ja.md)、
[`bajutsu/common/drivers/base/_functions.py`](../../bajutsu/common/drivers/base/_functions.py)、
[`bajutsu/common/drivers/adb/_functions.py`](../../bajutsu/common/drivers/adb/_functions.py)、
[`BajutsuKit/Sources/BajutsuRunner/PositionPath.swift`](../../BajutsuKit/Sources/BajutsuRunner/PositionPath.swift)、
[`docs/selectors.md`](../../docs/selectors.md)、
[`tests/fixtures/be0408/`](../../tests/fixtures/be0408/)、
[`tests/test_selector_fixtures.py`](../../tests/test_selector_fixtures.py)、
[UniFFI](https://mozilla.github.io/uniffi-rs/)

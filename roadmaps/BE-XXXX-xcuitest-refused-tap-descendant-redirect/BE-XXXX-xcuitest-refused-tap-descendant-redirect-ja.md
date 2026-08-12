[English](BE-XXXX-xcuitest-refused-tap-descendant-redirect.md) · **日本語**

# BE-XXXX — container がタップを拒んだとき、到達できる名前付きの子孫が 1 つだけならそこへ差し出す

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-xcuitest-refused-tap-descendant-redirect-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | [#1571](https://github.com/bajutsu-e2e/bajutsu/pull/1571) |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check-ja.md), [BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee-ja.md), [BE-0285](../BE-0285-scenario-feature-real-backend-coverage/BE-0285-scenario-feature-real-backend-coverage-ja.md) |
<!-- /BE-METADATA -->

## はじめに

iOS は、**包んでいるコントロールより大きく膨らんだ**アクセシビリティ要素を報告し、そこへのタップを
拒みながら、内側のコントロールには問題なく届く状態を見せることがあります。本項目は、XCUITest
バックエンドがその形に気づくようにします。一意に解決した対象が拒まれたとき、ドライバは対象の
**名前付きの子孫**、すなわち文書順で後にあり、frame の内側にあり、identifier を持つ要素を調べます。
到達できるものが**ちょうど 1 つ**ならそこへタップし、理由を記録します。0 個または複数なら失敗し、
どれかを選ぶかわりに**候補を名指し**します。そこではタップに唯一の意味がなく、1 つを選ぶことは
prime directive 2 が禁じる当てずっぽうになるからです。理由は `Actuation.substitution` という新しい
項目に載るので、レポートも `trace` のタイムラインも、差し替えたタップを通常のタップのようには見せません。

## 動機

showcase の Log タブに対する `tap: { id: log.count }` は、iOS 18.6 の Simulator で SwiftUI ビルドに
対して失敗します。

```
still not tappable after a bounded scroll attempt: element resolved but not hittable: {'id': ['log.count', 'log_count']}
```

要素は `[16, 268, 358, 44]` に表示されており、一意に解決しています。XCTest が拒んでいるのは、
アクセシビリティ要素がフォームの行全体、すなわち囲んでいる cell と同じ frame まで膨らんだ SwiftUI の
`Stepper` です。その内側の 2 つのボタンは `isHittable` に `ok` を返します。実機で 3 つ同時に測りました。

| 要素 | traits | frame | `POST /isHittable` |
|---|---|---|---|
| `log.count` | `other` | `[16, 268, 358, 44]` | `not-hittable` |
| `log.count-Decrement` | `button` | `[264, 274, 46.5, 32]` | `ok` |
| `log.count-Increment` | `button` | `[311.5, 274, 46.5, 32]` | `ok` |

[BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check-ja.md) が
ドライバの上に足した、回数を区切ったスクロールが今日唯一の回復手段ですが、これは効きません。対象の
中心はすでにビューポートの中にあるので、スクロールのたびに同じ問いを投げて同じ答えを受け取り、予算を
使い切るだけです。読み手に届く失敗は container を名指して「hittable ではない」と述べます。目に見えて
いる要素についてそう言い、しかも内側にある、使えたはずの 2 つのコントロールには何も触れません。

**この形は珍しくもなく、1 つの OS の癖でもありません。** XCUITest は測ったどの iOS バージョンでも
stepper 自身の identifier から `-Increment` と `-Decrement` を合成しますし、frame が膨らむかどうかは
バージョンではなく**ツールキット**の差でした。UIKit ビルドの `UIStepper` は自身の 94×32 の矩形を報告して
hittable ですが、SwiftUI ビルドは行全体に広がって hittable ではありません。アプリがラベルを付けた
container であれば、`Stepper` でも、スイッチ 1 つを包んだ行でも、ボタン 1 つだけを持つカードでも同じ形になりえます。

放置する代償は 2 つあります。シナリオの作者は、そこに見えている要素についての拒否を読まされ、
`log.count-Increment` が存在することをメッセージから知る手段がありません。しかも回数を区切った
スクロールが先に予算を使い切るので、従量課金の実機レーンで失敗がゆっくり届きます。

## 詳細設計

修正は XCUITest ドライバ（[`xcuitest.py`](../../bajutsu/drivers/xcuitest.py)）の `tap` に置き、
`not-hittable` の応答に対して `_actuate` が上げる `ElementNotTappable` を捕まえます。候補の計算は
[`base.py`](../../bajutsu/drivers/base.py) の純粋関数として、共有のドライバヘルパの隣に置きます。

**どの要素を差し出してよいか。** `redirect_candidates(elements, target)` は `topmost_at_point` の鏡像で、
同じ after-`target` の範囲を走査し、あちらが捨てるものをそのまま採ります。条件は 3 つで、それぞれが
「差し出しが間違いになる道」を 1 つずつ塞ぎます。

- **文書順で `target` より後にあること。** `Element` は親へのポインタを持たないので、幾何だけでは子孫と、
  祖先や同じ frame を囲む無関係なオーバーレイとを区別できません。pre-order の走査は祖先を必ず子孫より
  先に出すので、frame の検査にはできない区別をここが担います。`topmost_at_point` が逆の場合について
  すでに述べている理由と同じです。
- **`target` の frame の内側にあること**（境界を含む）。frame が完全に一致する要素も採ります。1 か所に
  二重登録された 1 つのコントロールは、小さい子と同じくらい正当な差し出し先であり、3 つ目の条件は
  依然かかるからです。
- **identifier を持つこと。** これにより、差し出す先はつねに作者が自分で名指せたはずの要素になります。
  差し替えが「作者に予測できない書き換え」にならないのはこの条件のためで、
  [BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee-ja.md)
  がドライバ側の吸収に対して挙げた 3 つの指摘のうち、ここでも当たる最初の 1 つに答えます。拒むときに、
  選ばなかった候補を名指せるのもこの条件のおかげです。

`MAX_REDIRECT_CANDIDATES` はグループを 4 件で区切ります。それを超える container は、操作できる子を
1 つ持つコントロールではなくレイアウトの領域であり、検査は 1 件ごとに 1 往復です。ドライバは 1 件も
使わずに拒みます。

**どこでタップが動くか。** 到達できる候補がちょうど 1 つなら選択の余地がないので、タップはそこへ行きます。
0 個または複数なら選択が生じるので、ドライバは送出し直します。そのときメッセージが候補をすべて名指します。
これが本項目の行動可能な半分です。実物の `Stepper` は到達できる子を 2 つ持つので、
`tap: { id: log.count }` には本当に唯一の意味がなく、どちらの id を選ぶべきかを作者に伝えるほうが、
ドライバが下せるどの選択よりも価値があります。送出し直しは元の拒否を原因として連鎖させるので、原因は
保たれます。

対象は `tap` だけです。long-press や 2 本指のジェスチャを子へ向けるのは、同じ意図が目標に届くことではなく、
別の意図だからです。

**子は自身の id で操作します。** `_actuate` は stale の再試行で、渡されたセレクタから再解決します
（BE-0289）。container のセレクタを渡すと、その再試行で差し替えが黙って取り消されます。

**なぜこれが BE-0221 の退けた暗黙の書き換えではないのか。** あの項目はドライバ側の `.`↔`_` の変換を
4 つの理由で退けました。4 つ目、すなわち「assertion や `wait` や `forEach` はドライバの外で `query()` の
出力に対して解決するので変換が届かない」は当たりません。本項目はアクチュエーションだけに触れ、container
についての assertion はこれまでどおり container に対して評価されるからです。残る 3 つには作りで答えます。
記録が明示的なトークンを持つので暗黙ではありません。名前を作り出しません。ツリーに実在し、対象の frame の
内側にあり、identifier が一意に解決する要素だけを操作するので、別々の id を取り違えません。そして
差し出し先は名前付きに限られ、複数あればドライバは選ばずに拒むので、予測できます。支えとなる先例は
`_collapse_identical_duplicates` で、XCUITest の産物を共有のコアで吸収しつつ、過剰な吸収に対する歯止めを
自ら明記しています。

**置き換えを見えるようにする。** `_actuate` は応答が返る前に記録するので、差し替えは「拒否」と「受理」の
2 レコードを残します。しかし読み手は、それを step のセレクタと突き合わせないと、意図的な差し替えなのか
stale の再解決なのかを区別できません。そこで `Actuation` に省略可能な `substitution` を足し、固定の
`SUBSTITUTIONS` から値を採り、manifest を `schemaVersion` 7 へ進めます。`via` の新しい値にはしません。
`CHANNELS` は「どの経路で対象に届いたか」に答えるもので、差し替えたタップも `handle` で届きます。変わった
のは**どの要素か**です。固定のトークンなら記録の規則 3（作者が書いた文字列は載せない）も保たれるので、
秘匿処理をしない `manifest.json` に載せても安全です。読み手はどちらも表示します。レポートは `via` の隣の
バッジとして、`trace` のタイムラインは `↷<トークン>` としてです。

**欠陥が見つかった fixture も、ドライバとは別に直します。** `log.count` はプラットフォームごとに別のものを
指しています。Android では increment のコントロールそのもので、iOS では Stepper の container です。
`extract.yaml` は `log.count-Increment` を並べ、`traits: [button]` を付けるようにしました。この trait が
iOS の container（`other`）を落として increment だけを残し、Android では id が指す 1 つのボタンが
もともと `button` なので影響しません。これはシナリオが自分の意味を述べるということであり、2 通りに読める
セレクタに対する BE-0221 と同じ筋の解決です。ドライバの差し替えは、作者にそれ以上うまく名指す手段のない
container のためのものであって、曖昧なセレクタを放置してよいという許可ではありません。

**テスト。** 候補の計算は純粋で、実測した iOS 18.6 のツリーから覆います。container の後ろに 8 要素が
内側に入っていて、platform が名前を付けた 2 つだけが残ることに加え、祖先・frame の外・frame が完全一致・
同一性でリストに無い場合を固定します。ドライバの 5 つの結末は、同じツリーから組んだ偽のトランスポートに
対して固定します。到達できる子が 1 つ、2 つ（実物の Stepper。両方の id がメッセージに出ることを確かめます）、
0 個、名前付きの子孫がない場合、そして上限を超えて混み合った container（検査を 1 件も使わないこと）です。
証跡の項目は、往復・古い manifest が `None` で読めること・不正値がレコードを捨てずに落ちること・トークンが
描画したレポートと trace のタイムラインの両方に届くことで覆います。

## 検討した代替案

**fixture だけで直す。** `extract.yaml` を `log.count-Increment` に向け直せば showcase は通り、ドライバの
変更は要りません。本項目もそれは行います。シナリオは自分の意味を述べるべきだからです。ただし一般の場合は
手つかずのまま残ります。ラベルを付けた container が単一のコントロールを包んでいるアプリでは、作者が名指せる
唯一の要素へのタップが依然として拒まれ、しかもメッセージは子について何も述べません。

**オーケストレータで回復させる。** `_tap_with_recovery` はすでに `ElementNotTappable` を捕まえています。
しかしあちらは対象を**セレクタ**でしか指し直せず、幾何からセレクタを合成することになるので、ドライバが
すでに handle で持っている曖昧さを持ち込み直します。ドライバはスナップショットごとの handle の対応表と、
唯一のネイティブな hittability の判定手段を持ちますが、オーケストレータはどちらも持ちません。

**到達できる子が複数のとき、どれかを選ぶ。** 先頭を採るか、左端を採れば、showcase の Stepper のタップは
シナリオを変えずに「動いて」いました。同時に、`tap: { id: log.count }` が iOS では「increment」を意味し、
2 つを逆順に並べるビルドでは「decrement」を意味することにもなり、どちらなのかをシナリオは何も述べません。
BE-0221 の言う作者に予測できない状態であり、形式はともかく実質は決定性の違反です。

**runner の `typeName` で `.stepper` を分類する。** container が `other` の trait に落ちるのは `typeName` が
`XCUIElementType.stepper` を名指していないためで、これがバージョン非依存の直し方に見えます。計測はこれを
否定します。trait のトークンは変わっても frame も `isHittable` も変わらないので、タップは拒まれたままです。
`resolve_unique` の `other` trait による同点落としのためには、それ自体を行う価値がありますが、それは別の
問題です。

**すべての要素の hittability を先に調べる。** 拒否された場合を「特別な場合ではないもの」にできますが、
どの問い合わせでも要素 1 つにつきネイティブの呼び出しが 1 回かかり、そのほとんどは拒否に関わりません。
検査を拒まれたタップ自身の子孫に絞れば、費用はこの形が実際に起きる頻度に比例したままです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 設計する前に実機で形を測る。iOS 18.6 の Simulator で `log.count` と合成された子 2 つに
      `POST /isHittable` を投げる。
- [x] `Actuation.substitution` を一通り。`SUBSTITUTIONS` の組、`schemaVersion` 7、ローダの degrade、
      レポートのバッジ、`trace` の区間。
- [x] `base.redirect_candidates` と `MAX_REDIRECT_CANDIDATES`。純粋で、実測したツリーから覆う。
- [x] `xcuitest.py` の `tap` におけるドライバの差し替え。5 つの結末をすべて固定する。
- [x] `extract.yaml` が increment を名指す。`docs/selectors.md`・`docs/drivers.md`・`docs/evidence.md`・
      `docs/reporting.md` と `docs/ja/` の対が、差し替えと新しい項目を述べる。

ログを次に記します。

- 本項目の形を決めた計測。iOS 18.6 の Simulator で SwiftUI の showcase に対して、`log.count` は
  `not-hittable` を返し、`log.count-Increment` と `log.count-Decrement` は**両方とも** `ok` を返しました。
  到達できる子が 2 つあるということは「複数」の場合に当たるので、差し替えはそれを促した当のコントロールに
  対してはあえて発火しません。そこで行動可能な結末は、メッセージが両方の id を名指すことと、シナリオが
  意味した 1 つを名指すことです。2 度目の計測で、frame が膨らむかどうかはバージョンではなくツールキットの
  差だと判明しました。UIKit ビルドの `UIStepper` は自身の 94×32 の矩形を報告して hittable ですが、その中心は
  2 つの半分を分ける 1pt の仕切りに落ちます。
- 変更後の実機での確認。`extract.yaml` が `showcase-swiftui` と `showcase-uikit` × iOS 18.6 と 26.5 の
  4 通りで通ります。SwiftUI と 18.6 の組み合わせは以前は失敗していました。manifest では 3 回のタップが
  `log.count-Increment` に着地し、substitution は記録されていません。シナリオが自分の意味を名指すように
  なったので、差し替えが発火する必要がないからです。

## 参考

- [BE-0349 — 操作の前にタップ可能性を確かめ、回数を区切ったスクロールを安全網にする](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check-ja.md)：
  `isHittable` と `TapResult.notHittable`、および本項目が続く先となる、回数を区切ったスクロールを導入した
  項目です。対象がすでに中央にあるとき、そのスクロールは効きません。
- [BE-0221 — Android のシナリオ可搬性の保証](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee-ja.md)：
  ドライバ側の暗黙の id 書き換えを 4 つの理由で退けた項目です。うち 3 つはここでも当たり、歯止めがそれに
  答えます。「シナリオで述べる」というあちらの解決を `extract.yaml` が採ります。
- [BE-0285 — シナリオ記述機能を実バックエンドで検証する](../BE-0285-scenario-feature-real-backend-coverage/BE-0285-scenario-feature-real-backend-coverage-ja.md)：
  `extract.yaml` を所有する項目です。そのカウンタのタップが本項目のきっかけであり、本項目がそのセレクタを
  直します。
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py)：`redirect_candidates`、
  `MAX_REDIRECT_CANDIDATES`、および本項目が after-target の走査を鏡写しにする `topmost_at_point` です。
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py)：`tap` と
  `_tap_sole_reachable_descendant` です。
- [`bajutsu/drivers/actuation.py`](../../bajutsu/drivers/actuation.py)：`SUBSTITUTIONS` と記録の新しい項目です。

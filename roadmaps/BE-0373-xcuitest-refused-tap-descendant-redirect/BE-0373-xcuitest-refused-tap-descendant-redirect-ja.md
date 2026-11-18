[English](BE-0373-xcuitest-refused-tap-descendant-redirect.md) · **日本語**

# BE-0373 — container がタップを拒んだとき、到達できる名前付きの子孫が 1 つだけならそこへ差し出す

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0373](BE-0373-xcuitest-refused-tap-descendant-redirect-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0373") |
| 実装 PR | [#1571](https://github.com/bajutsu-e2e/bajutsu/pull/1571) |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check-ja.md), [BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee-ja.md), [BE-0285](../BE-0285-scenario-feature-real-backend-coverage/BE-0285-scenario-feature-real-backend-coverage-ja.md) |
<!-- /BE-METADATA -->

## はじめに

iOS は、**包んでいるコントロールより大きく膨らんだ**アクセシビリティ要素を報告することがあります。
その要素はタップを拒みますが、内側のコントロールには問題なく届きます。本項目は、XCUITest バックエンドが
その形に気づくようにします。

一意に解決した対象が拒まれたとします。
[driver](../../docs/ja/glossary.md#driver-backend-actuator-platform) は、対象の**名前付きの子孫**を調べます。
名前付きの子孫とは、文書順で対象より後にあり、対象の frame の内側にあり、identifier を持つ要素です。

- 到達できるものが**ちょうど 1 つ**なら、ドライバはそこへタップし、理由を記録します。
- 到達できるものが 0 個または複数なら、ドライバは失敗し、**候補を名指し**します。どれかを選ぶことは
  しません。その場合、タップに唯一の意味がなく、1 つを選ぶことは prime directive 2 が禁じる当てずっぽうに
  なるからです。

理由は `Actuation.substitution` という新しい項目に載ります。レポートも `trace` のタイムラインも、差し
替えたタップを通常のタップのようには見せません。

## 動機

showcase の Log タブに対する `tap: { id: log.count }` は、iOS 18.6 の Simulator で SwiftUI ビルドに
対して失敗します。

```
still not tappable after a bounded scroll attempt: element resolved but not hittable: {'id': ['log.count', 'log_count']}
```

要素は `[16, 268, 358, 44]` に表示されており、一意に解決しています。XCTest が拒んでいるのは、
アクセシビリティ要素がフォームの行全体まで膨らんだ SwiftUI の `Stepper` です。frame は、囲んでいる
cell と同じです。その内側の 2 つのボタンは、`isHittable` に `ok` を返します。実機で 3 つ同時に測りました。

| 要素 | traits | frame | `POST /isHittable` |
|---|---|---|---|
| `log.count` | `other` | `[16, 268, 358, 44]` | `not-hittable` |
| `log.count-Decrement` | `button` | `[264, 274, 46.5, 32]` | `ok` |
| `log.count-Increment` | `button` | `[311.5, 274, 46.5, 32]` | `ok` |

今日唯一の回復手段は、[BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check-ja.md)
がドライバの上に足した、回数を区切ったスクロールです。これは効きません。対象の中心は、すでにビューポート
の中にあります。スクロールのたびに同じ問いを投げて同じ答えを受け取り、予算を使い切るだけです。

読み手に届く失敗は、container を名指して「hittable ではない」と述べます。目に見えている要素について
そう言い、内側にある使えたはずの 2 つのコントロールには、何も触れません。

**この形は珍しくもなく、1 つの OS の癖でもありません。** XCUITest は、測ったどの iOS バージョンでも、
stepper 自身の identifier から `-Increment` と `-Decrement` を合成します。frame が膨らむかどうかは、
バージョンではなく**ツールキット**の差でした。UIKit ビルドの `UIStepper` は、自身の 94×32 の矩形を
報告して hittable です。SwiftUI ビルドは、行全体に広がって hittable ではありません。アプリがラベルを
付けた container であれば、同じ形になりえます。`Stepper`、スイッチ 1 つを包んだ行、ボタン 1 つだけを
持つカードが例です。

放置する代償は 2 つあります。

- シナリオの作者は、そこに見えている要素についての拒否を読まされます。`log.count-Increment` が存在する
  ことを、メッセージから知る手段がありません。
- 回数を区切ったスクロールが先に予算を使い切るので、従量課金の実機レーンで、失敗がゆっくり届きます。

## 詳細設計

修正は XCUITest ドライバ（[`xcuitest.py`](../../bajutsu/drivers/xcuitest.py)）の `tap` に置きます。
`not-hittable` の応答に対して `_actuate` が上げる `ElementNotTappable` を、捕まえます。候補の計算は
[`base.py`](../../bajutsu/drivers/base.py) の純粋関数として、共有のドライバヘルパの隣に置きます。

**どの要素を差し出してよいか。** `redirect_candidates(elements, target)` は `topmost_at_point` の鏡像です。
同じ after-`target` の範囲を走査し、あちらが捨てるものをそのまま採ります。条件は 3 つあり、それぞれが
「差し出しが間違いになる道」を 1 つずつ塞ぎます。

- **文書順で `target` より後にあること。** `Element` は親へのポインタを持ちません。幾何だけでは、子孫と、
  祖先や同じ frame を囲む無関係なオーバーレイとを区別できません。pre-order の走査は祖先を必ず子孫より
  先に出すので、frame の検査にはできない区別を、ここが担います。`topmost_at_point` が逆の場合について
  すでに述べている理由と同じです。
- **`target` の frame の内側にあること**（境界を含む）。frame が完全に一致する要素も採ります。1 か所に
  二重登録された 1 つのコントロールは、小さい子と同じくらい正当な差し出し先です。3 つ目の条件は、依然
  かかります。
- **identifier を持つこと。** 差し出す先は、つねに作者が自分で名指せたはずの要素になります。差し替えが
  「作者に予測できない書き換え」にならないのは、この条件のためです。
  [BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee-ja.md)
  がドライバ側の吸収に対して挙げた 3 つの指摘のうち、ここでも当たる最初の 1 つに答えます。拒むときに、
  選ばなかった候補を名指せるのも、この条件のおかげです。

`MAX_REDIRECT_CANDIDATES` は、グループを 4 件で区切ります。それを超える container は、操作できる子を
1 つ持つコントロールではなく、レイアウトの領域です。検査は 1 件ごとに 1 往復かかります。ドライバは、
1 件も使わずに拒みます。

**どこでタップが動くか。** 到達できる候補がちょうど 1 つなら選択の余地がないので、タップはそこへ行きます。
0 個または複数なら選択が生じるので、ドライバは送出し直します。そのとき、メッセージが候補をすべて名指し
します。これが、本項目の行動可能な半分です。

実物の `Stepper` は、到達できる子を 2 つ持ちます。`tap: { id: log.count }` には本当に唯一の意味が
ありません。どちらの id を選ぶべきかを作者に伝えるほうが、ドライバが下せるどの選択よりも価値が
あります。送出し直しは元の拒否を原因として連鎖させるので、原因は保たれます。

対象は `tap` だけです。long-press や 2 本指のジェスチャを子へ向けるのは、別の意図です。同じ意図が目標に
届くことではありません。

**子は自身の id で操作します。** `_actuate` は、stale の再試行で、渡されたセレクタから再解決します
（BE-0289）。container のセレクタを渡すと、その再試行で差し替えが黙って取り消されます。

**なぜこれが BE-0221 の退けた暗黙の書き換えではないのか。** あの項目は、ドライバ側の `.`↔`_` の変換を
4 つの理由で退けました。

- 4 つ目は当たりません。その理由は、assertion、`wait`、`forEach` がドライバの外で `query()` の出力に対して
  解決するので、変換が届かないというものです。本項目はアクチュエーションだけに触れます。container に
  ついての assertion は、これまでどおり container に対して評価されます。
- 残る 3 つには、作りで答えます。
  - 記録が明示的なトークンを持つので、暗黙ではありません。
  - 名前を作り出しません。ツリーに実在し、対象の frame の内側にあり、identifier が一意に解決する要素
    だけを操作するので、別々の id を取り違えません。
  - 差し出し先は名前付きに限られ、複数あればドライバは選ばずに拒むので、予測できます。

支えとなる先例は `_collapse_identical_duplicates` です。XCUITest の産物を共有のコアで吸収しつつ、
過剰な吸収に対する歯止めを自ら明記しています。

**置き換えを見えるようにする。** `_actuate` は応答が返る前に記録するので、差し替えは「拒否」と「受理」の
2 レコードを残します。しかし読み手は、それを step のセレクタと突き合わせないと、意図的な差し替えなのか
stale の再解決なのかを区別できません。

そこで `Actuation` に省略可能な `substitution` を足します。値は固定の `SUBSTITUTIONS` から採り、manifest を
`schemaVersion` 7 へ進めます。`via` の新しい値にはしません。`CHANNELS` は「どの経路で対象に届いたか」に
答えるものです。差し替えたタップも `handle` で届きます。変わったのは**どの要素か**です。

固定のトークンなら、記録の規則 3（作者が書いた文字列は載せない）も保たれます。秘匿処理をしない
`manifest.json` に載せても安全です。読み手はどちらも表示します。レポートは `via` の隣のバッジとして、
`trace` のタイムラインは `↷<トークン>` として表示します。

**欠陥が見つかった fixture も、ドライバとは別に直します。** `log.count` は、プラットフォームごとに別の
ものを指しています。Android では increment のコントロールそのもので、iOS では Stepper の container です。

`extract.yaml` は `log.count-Increment` を並べ、`traits: [button]` を付けるようにしました。この trait が
iOS の container（`other`）を落として increment だけを残します。Android では、id が指す 1 つのボタンが
もともと `button` なので、影響しません。

これは、シナリオが自分の意味を述べるということです。2 通りに読めるセレクタに対する、BE-0221 と同じ筋の
解決でもあります。ドライバの差し替えは、作者にそれ以上うまく名指す手段のない container のためのもの
です。曖昧なセレクタを放置してよいという許可ではありません。

**テスト。** 候補の計算は純粋で、実測した iOS 18.6 のツリーから覆います。container の後ろの内側に 8 要素が
あり、platform が名前を付けた 2 つだけが残ることを固定します。祖先、frame の外、frame が完全一致、同一性で
リストに無い場合も固定します。

ドライバの 5 つの結末は、同じツリーから組んだ偽のトランスポートに対して固定します。

1. 到達できる子が 1 つ
2. 到達できる子が 2 つ（実物の Stepper。両方の id がメッセージに出ることを確かめます）
3. 到達できる子が 0 個
4. 名前付きの子孫がない場合
5. 上限を超えて混み合った container（検査を 1 件も使わないこと）

証跡の項目は、4 つの観点で覆います。往復、古い manifest が `None` で読めること、不正値がレコードを捨てずに
落ちること、トークンが描画したレポートと trace のタイムラインの両方に届くことです。

## 検討した代替案

**fixture だけで直す。** `extract.yaml` を `log.count-Increment` に向け直せば、showcase は通り、ドライバの
変更は要りません。本項目もそれは行います。シナリオは自分の意味を述べるべきだからです。ただし一般の場合は
手つかずのまま残ります。ラベルを付けた container が単一のコントロールを包んでいるアプリでは、作者が
名指せる唯一の要素へのタップが依然として拒まれます。メッセージは、子について何も述べません。

**オーケストレータで回復させる。** `_tap_with_recovery` は、すでに `ElementNotTappable` を捕まえています。
しかしあちらは、対象を**セレクタ**でしか指し直せません。幾何からセレクタを合成することになり、ドライバが
すでに handle で持っている曖昧さを持ち込み直します。ドライバは、スナップショットごとの handle の対応表と、
唯一のネイティブな hittability の判定手段を持ちます。オーケストレータは、どちらも持ちません。

**到達できる子が複数のとき、どれかを選ぶ。** 先頭を採るか、左端を採れば、showcase の Stepper のタップは
シナリオを変えずに「動いて」いました。同時に、`tap: { id: log.count }` が iOS では「increment」を意味し、
2 つを逆順に並べるビルドでは「decrement」を意味することにもなります。どちらなのかを、シナリオは何も述べ
ません。BE-0221 の言う作者に予測できない状態であり、形式はともかく実質は決定性の違反です。

**runner の `typeName` で `.stepper` を分類する。** container が `other` の trait に落ちるのは、`typeName`
が `XCUIElementType.stepper` を名指していないためです。これは、バージョン非依存の直し方に見えます。
計測はこれを否定します。trait のトークンは変わっても、frame も `isHittable` も変わらないので、タップは
拒まれたままです。`resolve_unique` の `other` trait による同点落としのためには、この分類にそれ自体を行う
価値がありますが、それは別の問題です。

**すべての要素の hittability を先に調べる。** 拒否された場合を「特別な場合ではないもの」にできます。
ただし、どの問い合わせでも要素 1 つにつきネイティブの呼び出しが 1 回かかり、そのほとんどは拒否に関わり
ません。検査を拒まれたタップ自身の子孫に絞れば、費用はこの形が実際に起きる頻度に比例したままです。

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

- 本項目の形を決めた計測は、iOS 18.6 の Simulator で SwiftUI の showcase に対して行いました。`log.count` は
  `not-hittable` を返し、`log.count-Increment` と `log.count-Decrement` は**両方とも** `ok` を返しました。
  - 到達できる子が 2 つあるということは「複数」の場合に当たります。差し替えは、それを促した当のコント
    ロールに対しては、あえて発火しません。行動可能な結末は、メッセージが両方の id を名指すことと、
    シナリオが意味した 1 つを名指すことです。
  - 2 度目の計測で、frame が膨らむかどうかはバージョンではなくツールキットの差だと判明しました。UIKit
    ビルドの `UIStepper` は、自身の 94×32 の矩形を報告して hittable です。その中心は、2 つの半分を分ける
    1pt の仕切りに落ちます。
- 変更後の実機での確認では、`extract.yaml` が `showcase-swiftui` と `showcase-uikit` × iOS 18.6 と 26.5 の
  4 通りで通りました。SwiftUI と 18.6 の組み合わせは、以前は失敗していました。manifest では、3 回のタップ
  が `log.count-Increment` に着地し、substitution は記録されていません。シナリオが自分の意味を名指す
  ようになったので、差し替えが発火する必要がないからです。

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

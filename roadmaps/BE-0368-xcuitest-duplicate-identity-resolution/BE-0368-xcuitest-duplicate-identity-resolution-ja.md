[English](BE-0368-xcuitest-duplicate-identity-resolution.md) · **日本語**

# BE-0368 — 内容が同一の重複ノードを解決失敗にせず、1 つのライブ要素へ解決する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0368](BE-0368-xcuitest-duplicate-identity-resolution-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0368") |
| 実装 PR | [#1567](https://github.com/bajutsu-e2e/bajutsu/pull/1567) |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0357](../BE-0357-xcuitest-duplicate-node-hittable-tiebreak/BE-0357-xcuitest-duplicate-node-hittable-tiebreak-ja.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md), [BE-0312](../BE-0312-xcuitest-content-addressed-snapshot-handle/BE-0312-xcuitest-content-addressed-snapshot-handle-ja.md), [BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本項目は、**内容が同一の重複ノード**、つまり iOS のアクセシビリティツリーが識別子・ラベル・traits・
値・frame のすべてを同じ値で報告する 2 つの登録を、1 つのライブ要素へ解決できるようにします。標準の
`UIAlertController` のボタンで知られる現象で、いまはこの重複に対するあらゆる操作が
`element vanished (stale handle)` で失敗します。iOS バックエンドでアプリを駆動する runner は、Apple の
UI テストフレームワークである XCUITest を通じて、記録しておいた要素の参照を操作可能な要素へ 2 段階で
戻します。まずツリー上の記録した位置を再生し、その再生が一致しなくなっていたら、記録した同一性による
平坦な問い合わせへ退きます。この退避を担う
[`XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift) の
`uniquelyIdentifiedElement` は候補が**ちょうど 1 件**であることを要求し、2 件なら何も返しません。
つまり重複ペアは、それを救うはずの退避自身を破り、ペアのどちらの要素にも手が届かなくなります。本項目
では、候補**どうし**が値と frame まで一致するグループを、実体どおりの 1 つのコントロールとして扱い、
あきらめるかわりにその 1 件へ解決します。

## 動機

iOS 26 では、ネイティブアラートのボタンへの `tap` が、`id` で引いても `label` と `traits` で引いても
失敗します。

```
step 8 (tap): element vanished (stale handle): {'id': 'log.alert.ok'}
```

同じシナリオが iOS 18 では通ります。showcase の fixture に対する Spike で、その差を直接測りました。
iOS 26.5 のアクセシビリティツリーは、アラートの「OK」ボタンを識別子・ラベル・traits・frame が同じ
**2 件**の登録として報告します。iOS 18.6 は 1 件です。アラートを表示したまま runner の `/isHittable`
エンドポイントを各登録に対して順に叩くと、アラートのボタンの登録 4 件はすべて `stale` を返しました。
一方、同じ部分木にある重複していない通常のノード、すなわちアラートのコンテナとメッセージのテキストは
`ok` を返しました。さらに、そのボタンの frame 中心を素の座標でタップすると、アラートは閉じ、アプリの
結果のミラーは `ok` になりました。

この 3 つの観測は、欠陥の位置を正確に指します。中心をタップすれば動くのですから、ペアは実在する 1 つの
タップできるコントロールです。隣接する要素が正常に答えるのですから、チャネルと参照の管理は健全です。
壊れているのは解決です。ペアのどちらの要素も、runner が操作できる要素へ戻せません。

その解決は 2 段階とも失敗し、しかも 2 段目は重複そのものが原因で失敗します。`liveElement(for:)` は、
まず記録した position path、つまりスナップショット上で記録した根からの子インデックスの並びを再生し、
同一性がなお一致する場合だけその要素を採ります。ライブのアラートの階層には、スナップショットの走査には
なかったシステム所有の wrapper ノードが挟まるため、インデックスの並びを再生すると別の場所に着きます。
これは退避側のコードのコメントが名指す回復のケースそのもので、コメントは iOS の「Save Password」シート
を、ライブの wrapper ノードのせいでスナップショットの子インデックスを再生できない画面の例として挙げて
います。続いて退避は、記録した識別子とラベルに一致するライブの要素をすべて集め、
[`PositionPath.swift`](../../BajutsuKit/Sources/BajutsuRunner/PositionPath.swift) の
`uniqueMatchingIndex` に渡します。この関数の契約は「**唯一の**候補の番号を返し、それ以外は nil を返す」
ことで、docstring 自身が「一致が 0 件でも複数件でも nil を返す。どちらの場合も 1 つの要素を安全に特定
できない」と述べています。重複ペアは一致が 2 件なので退避は何も返さず、`liveElement` も何も返さず、
runner は `stale` を報告します。iOS 18 では同じ退避が働いて成功します。唯一になれる候補が 1 件しかない
からです。

複数件で nil を返す規則自体は、それが書かれた場面では正しいものです。識別子が同じでも別々のコントロール
を指す 2 つの要素は同一性では区別できず、片方を選ぶことは prime directive 2 が禁じる当てずっぽうに
なります。しかし重複**ペア**はその場面ではありません。ペアの要素は frame まで一致し、画面上の 1 か所に
ある 1 つのコントロールを表しているので、どちらを採るかは操作対象の選択ではないからです。いまの runner
はこの区別を表現できず、冗長な登録を曖昧さとして扱い、はじめから曖昧ではなかったコントロールを拒んで
います。

[BE-0357](../BE-0357-xcuitest-duplicate-node-hittable-tiebreak/BE-0357-xcuitest-duplicate-node-hittable-tiebreak-ja.md)
は、同じアラートの失敗をひとつ手前の層で直そうとした項目です。重複グループのうちタップできない側を落として、
参照の管理へ届く候補を 1 件だけにする、という方針でした。同項目自身が、その方針を未検証の前提、すなわち
「ちょうど 1 件が hittable を報告する」に依存すると書き、コードを書く前に Spike で確かめるよう求めて
います。上の Spike がその Spike であり、前提は成立しませんでした。`.ok` を返す要素は 1 件ではなく 0 件
です。`isHittable` は本項目が修理する `liveElement` の解決を通るため、どちらの要素も見えないからです。
BE-0357 は自身の仕様に従えばこのグループに触れないので、書かれたとおりに実装しても、取り除こうとした当の
失敗には効きません。同項目の *進捗* にこの結果を記録し、`状態` は本項目と同じ変更で「保留」へ
移します。解決を先に直すことは、BE-0357 が働くための手がかりを与えることでもあります。

放置した場合の代償は、この種の失敗がこれまで払わせてきたものと同じです。ネイティブアラートは実際の
アプリでありふれた部品ですし、画面から消えていないボタンで必須のオンデバイスのゲートが落ちれば、ゲート
への信頼は損なわれ、何も悪くない step の調査に従量課金の macOS ランナーの時間が消えます。加えて、いま
持っている検証の範囲も失います。showcase の `alert.yaml` は、解決が直るまで iOS 26 のどの端末でも
走らせられません。

## 詳細設計

修正は flat-query の退避に置きます。記録した同一性に対するライブの候補をすべて手元に持ち、候補どうしを
比べられる唯一の場所だからです。

**値と frame が一致する候補グループを 1 つのコントロールとして扱います。**
[`XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift) の
`uniquelyIdentifiedElement` は、いまと同じように候補を集めます。新しい関数は一致が 0 件のときと
1 件のときの `uniqueMatchingIndex` の答えをそのまま保ちます。そのため現在解決できている画面は、すべて
変わらず解決できます。新しい規則が働くのは一致が **2 件以上**のときだけです。一致した候補がすべて
同じ値と同じ frame を持つなら、そのグループは 1 つのコントロールの冗長な登録です。退避は何も返さずに
終わるのではなく、その 1 件へ解決します。値または frame のどちらかが異なるグループは、いまと同じく
未解決のままにします。報告している内容が異なる 2 つのコントロールは、当てずっぽうではなく失敗で応じる
べき本物の曖昧さだからです。

frame を判定材料に使えるのは、
[BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md)
がそれを `attributesMatch` から外したからこそです。値のほうはもともとこの一致判定に入っていたことがなく、
別の理由で使えます。2 つの用途は矛盾しません。`attributesMatch` は
**記録した**値と**いまライブの**値を比べます。そのあいだには、落ち着きつつある画面が要素を正当に動かす
余地があります(BE-0287 は、変わっていないフィールドの 49 ポイントの移動が stale と読まれた例を測って
います)。また、スライダーやテキストフィールドが値を正当に変える余地もあります。本項目が加える比較は、
**1 回のライブの問い合わせから読んだ**候補どうしを、**互いにだけ**比べるものです。記録した側はこの
比較に入らないため、この種の余地は生じません。

とはいえ、その候補は 1 つの瞬間に採取されているわけではありません。この事実が、frame の一致をどれだけ
厳密に求めるべきかを決めます。`uniquelyIdentifiedElement` は `candidates.map(...)` で候補の属性を組み立てる
ため、各候補の frame はそれぞれ独立した XCUITest の属性取得です。したがって、BE-0287 が測ったのと同じ
アニメーションの最中で画面がまだ落ち着いていなければ、1 つのコントロールの 2 つの登録が 1 ポイント未満
だけずれて報告されることがあります。そこで frame は厳密な一致ではなく**1 ポイント以内の一致**とします。
この許容幅は、本当に画面上の 2 か所にあるコントロールを隔てる距離よりはるかに小さいため、あいまいさを
きちんと失敗として報告する分岐を犠牲にしません。

**ホストと同じフィールドを判定材料にします。**
[`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) の `_collapse_identical_duplicates` は、
`/elements` の応答の中でこの重複をすでに解消しています。判定材料は識別子・ラベル・traits・値・frame
です。本項目の規則は、その runner 側の対になるものです。*記録した*参照を操作時に再解決する際、
`/elements` のどの応答も絞り込んでいない候補集合に対して働くため、同じフィールドを判定材料にします。
互いの docstring から相手を参照するのは、このためです。判定材料をこれより減らせば、ホスト側が大声で
失敗するはずの場面を runner が当てずっぽうで通してしまいます。値を持つコントロールが 2 回登録され、
それぞれの値が食い違っているとします。ホスト側ではそれはあいまいさであり、runner 側でもあいまいさの
ままでなければなりません。そこで `RecordedAttributes` は `value` を新たに持ちます。この値は、
フラット化された `ElementSnapshot` がすでに報告しているものです。使うのはグループの規則だけで、
`attributesMatch` には引き続き含めません。

**どの要素へ解決するかは、思い込みではなく計測で答えるべき問い**です。本項目が正そうとしている誤りが
まさにそれでした。規則の候補は 2 つあります。一致した先頭の要素を返す規則は、もっとも安く、ネイティブの
呼び出しを増やしません。もう 1 つは各要素を `isHittable(backingElement:)` で調べて hittable な要素を
優先し、区別がつかなければ先頭に戻る規則です。両方の要素を操作できるなら前者が正解であり、片方しか操作
できないなら後者が要ります。ここまでの計測はこの問いを決めません。Spike が調べた要素は flat query では
なく position path で辿ったものであり、flat query 自身の取り出し方である `allElementsBoundByIndex` は、
記録したツリー上の位置ではなくライブの問い合わせへの番号で各候補を束ねるからです。そこで下の作業分解の
最初の単位を、重複したアラートのボタンを flat query のグループの各要素経由で順にタップし、どれが実際に
操作できるかを記録する Spike とします。どの要素も操作できないなら、BE-0357 と同じ形で本項目の前提が
崩れるので、実装ではなく設計の見直しへ回します。

**範囲。** `uniquelyIdentifiedElement` は `liveElement(for:)` から呼ばれ、`liveElement(for:)` は
あらゆる操作と hittability の検査がすでに共有しています。したがって `tap`・`gesture`・`isHittable` は
1 か所の変更でまとめて直り、どれも個別の対応を必要としません。`/elements` の応答には手を付けません。
本項目が変えるのは、記録した参照がライブの要素へどう戻るかであってツリーが何を報告するかではありません。
したがって重複ペアはこれまでどおり 2 件として現れ、その同一性に対する `count` の判定も 2 のままです。
応答の側でもペアをまとめるべきかは BE-0357 が問うている論点であり、本項目はついでに答えず、そちらに
残します。[BE-0312](../BE-0312-xcuitest-content-addressed-snapshot-handle/BE-0312-xcuitest-content-addressed-snapshot-handle-ja.md)
の参照の仕組みにも手を付けません。両方の要素がそれぞれの参照を保ったまま、どちらも解決できるように
なります。

**規則の置き場所。** `uniqueMatchingIndex` は端末に依存しない `BajutsuRunner` ライブラリの純粋な関数で、
XCUITest 固有の provider の 1 か所から呼ばれています。その隣に純粋な関数をもう 1 つ、つまり
「グループの値と frame が一致する」規則のもとで採るべき候補の番号を返す関数を足せば、判断は実機なしで
テストできるままで、既存の関数とその 3 つのテストにも触れずに済みます。provider は
`uniqueMatchingIndex` の呼び出しをこの新しい関数へ置き換えます。0 件と 1 件のふるまいは新しい関数が
引き継ぐので、厳密な一意性だけを求める呼び出し元のために、これまでの関数はその隣に残ります。

**テスト。** 規則は純粋なリストの処理なので、`PositionPathTests.swift` が Simulator なしで覆います。
値と frame まで同一の候補 2 件は 1 件へ解決すること、同一性は一致するが frame が異なる候補 2 件、
および値だけが異なる候補 2 件は未解決のままであること、frame が 1 ポイント未満だけ異なる候補 2 件は
それでも 1 件へ解決すること、候補 1 件と候補 0 件はこれまでどおりに振る舞うことを固定します。
オンデバイスの検証は showcase の既存の `alert.yaml` です。いまこの失敗を再現しており、修正後は iOS 26
の Simulator で通り、iOS 18 でも通り続けなければなりません。

## 検討した代替案

**`/elements` の応答から hittable でない要素を落とす（BE-0357）。** 応答を絞って参照の管理へ届く候補を
1 件にするには要素を区別する手がかりが要りますが、*動機* の Spike が示すとおり、その手がかりは得られ
ません。`isHittable` は壊れている当の解決を通るので、どちらの要素にも `stale` を返します。本項目が入った
あとも両方の要素が解決して両方が `.ok` を返すので、これは BE-0357 自身が「触れないままにする」と定めた
場合に当たります。2 つの項目はアラートについては噛み合いません。BE-0357 は、ライブの検査で要素どうしを
本当に区別できる重複ペアがあってはじめて成り立ちます。

**position path の再生をアラートの階層でも成立させる。** 解決の 1 段目を直せば、この画面で flat query に
退くこと自体がなくなります。しかしスナップショットの子インデックスとライブの階層の wrapper ノードの
食い違いは一般には解消できず、それこそが再生を直すかわりに flat query の退避が再生の隣に置かれている
理由です。この道はすでに負けが判定されている戦いを挑むことになり、しかも退避へ落ちる他の画面の重複は
失敗したままです。

**解決に失敗したら座標タップへ退く。** 記録した frame の中心をタップすれば動くことは Spike が示しました。
しかしそれは、本当に消えた要素に対して、いまその位置を占めている何かへの盲目的なタップを許すことでも
あります。これは Bajutsu の設計が避けようとしている安定性ラダーの降下そのもので、
[BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve-ja.md) の
リトライが頼りにしている `stale` の報告も隠してしまいます。

**`uniqueMatchingIndex` 自体を緩めて、複数一致のときは先頭を返す。** 隣に関数を足すのではなく既存の関数を
変えると、その docstring が約束している厳密な一意性の用途も含めて、すべての呼び出し元の契約が黙って
「曖昧なら当てずっぽうで選ぶ」に広がります。グループの規則は値と frame の両方の一致を要求しますが、
その関数はどちらも一切比べません。両方を見る規則を両方とも見ない同一性の検査に混ぜると、1 つの関数が
2 つの異なる問いに答えることになります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] Spike（この先すべての前提）：`UIAlertController` を表示した iOS 26 の Simulator で、重複した
      ボタンを flat query の候補グループの各要素経由で順に操作し、どの要素が実際に操作できるかを
      記録する。1 件だけ操作できるなら選び方は hittable 優先に、両方操作できるなら先頭を採る規則に
      決まる。どれも操作できないなら本項目の前提が崩れるので、実装ではなく設計の見直しへ回す。
      **要素ごとの検査ではなく実験として実施しました**。検査そのものは実施できません。下のログを
      参照してください。
- [x] `PositionPath.swift` の `uniqueMatchingIndex` の隣にグループ解決の関数を足し、Spike が決めた
      選び方を実装する。`XcuitestElementProvider.swift` の `uniquelyIdentifiedElement` では
      `uniqueMatchingIndex` の呼び出しをこの関数へ置き換える。0 件と 1 件のふるまいは引き継ぐ。
      `uniqueMatchingIndex`・`attributesMatch`・`/elements` の応答・`SnapshotStore` には手を付けない。
- [x] `_collapse_identical_duplicates` が判定材料にしているフィールドをグループの規則にも使う。
      `RecordedAttributes` に `value` を追加し、グループの判定で frame と合わせて要求し、2 つの定義が
      それぞれの docstring から互いを参照するようにして、片方への後の変更がもう片方から見えるようにする。
      `value` は `attributesMatch` には含めない。
- [x] `PositionPathTests.swift` の実機なしテスト：値と frame まで同一の候補 2 件は 1 件へ解決すること、
      同一性は一致するが frame が異なる候補 2 件、および値だけが異なる候補 2 件は未解決のままであること、
      frame が 1 ポイント未満だけ異なる候補 2 件はそれでも 1 件へ解決すること、候補 1 件と候補 0 件は
      これまでどおりに振る舞うことを固定する。
- [x] オンデバイスの検証：`demos/showcase/scenarios/alert.yaml` が iOS 26 の Simulator で
      `showcase-swiftui` と `showcase-uikit` の両方に対して通り、iOS 18 でも通り続けることを確かめる。
- [x] BE-0357 に結果を記録する。*進捗* に、成立しなかった前提と Spike の証拠を書き、`状態` を
      「保留」へ移す。flat-query の一致を同一性だけが決めるかのように読める記述が
      `attributesMatch` の周辺に残っていれば、それも直す。

ログを次に記します。

- この単位が定める要素ごとの検査は、どちらの向きにも実施できません。修正前はペアのどちらの要素も解決
  できず、それこそが欠陥そのものだからです。修正後はどちらの要素の flat-query の退避もグループの先頭の
  要素へ解決するため、「2 つ目の要素経由で操作する」ことは 1 つ目が解決する要素をタップすることに
  なります。そこで Spike は実験として実施しました。先頭を採る規則を入れ、iOS 26.5 の Simulator で
  showcase の 2 つの fixture に対して `alert.yaml` を走らせたところ、どちらも通りました。これで、先頭の
  要素が操作できて先頭を採る規則で足りることが分かります。hittable を優先する規則にしても要素どうしを
  区別できないので、`resolvableMatchingIndex` はネイティブの hittability の呼び出しを必要とせず、
  グループの規則は実機なしのゲートに載る純粋な関数のままです。同じシナリオは iOS 18.6 でも通り続けます。
  iOS 18.6 ではペアが現れず、唯一の一致を採る経路が変わらないためです。
- [#1567](https://github.com/bajutsu-e2e/bajutsu/pull/1567) でのレビューが、最初の実装が前提としていた
  2 つの命題を訂正しました。1 つ目は、frame の一致を厳密な一致としていたことです。候補を「1 つの瞬間」
  に読んでいるという理由づけでした。しかし実際には、各候補の frame はそれぞれ独立した属性取得です。
  画面がまだ落ち着いていなければ、1 つのコントロールの 2 つの登録が 1 ポイント未満だけずれて報告され
  ます。これは、本項目が取り除こうとした失敗へペアを引き戻してしまいます。そこで frame の一致は 1
  ポイント以内の一致に改めました。2 つ目は、規則が frame だけを判定材料にしていたことです。ホスト側の
  `_collapse_identical_duplicates` は `value` も判定材料にしています。そのため、値が食い違う状態で
  2 回登録された値を持つコントロールは、本項目側では解決してしまう一方でした。ホスト側ではそれが
  `AmbiguousSelector` を送出していました。そこで `RecordedAttributes` に `value` を追加し、グループの
  規則はこれも要求するようにしました。

## 参考

- [BE-0357 — XCUITest の重複アクセシビリティノードでタップできる要素が 1 つだけなら他を取り除く](../BE-0357-xcuitest-duplicate-node-hittable-tiebreak/BE-0357-xcuitest-duplicate-node-hittable-tiebreak-ja.md)：
  同じアラートの失敗を `/elements` の応答を絞ることで直そうとした項目。重複ペアのちょうど 1 件が
  hittable を報告するという前提を、本項目の Spike が否定した。本項目が棚上げしたのち、
  [BE-0366](../BE-0366-roadmap-rejected-status/BE-0366-roadmap-rejected-status-ja.md)
  が「却下」へ再分類した。
- [BE-0287 — 多点タッチ操作下での XCUITest runner チャネルの耐障害性](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md)：
  `attributesMatch` から frame を外した項目（Unit 5）。記録した値とライブの値の比較であり、本項目が
  加える候補どうしの比較とは矛盾しない。
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py)：`_collapse_identical_duplicates`。
  本項目の規則のホスト側の対で、`/elements` の応答に対して同じフィールドを判定材料にする。
- [BE-0312 — 画面が変わっていなければ参照が有効なままになるよう、XCUITest の操作参照を要素の同一性から導く](../BE-0312-xcuitest-content-addressed-snapshot-handle/BE-0312-xcuitest-content-addressed-snapshot-handle-ja.md)：
  本項目が手を付けない参照の仕組み。重複ペアの両方の要素はそれぞれの参照を保ち、本項目のあとはどちらも
  解決できる。
- [BE-0289 — XCUITest チャネルが stale な操作参照を、失敗にする前に解決し直すようにする](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve-ja.md)：
  この失敗を回復できないリトライ。どの試行も、解決できない同じ候補グループを導き直すため。
- [`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)：
  `liveElement(for:)` と `uniquelyIdentifiedElement`。本項目が 2 段目を変える 2 段階の解決。
- [`BajutsuKit/Sources/BajutsuRunner/PositionPath.swift`](../../BajutsuKit/Sources/BajutsuRunner/PositionPath.swift)：
  `uniqueMatchingIndex` と `attributesMatch`。新しいグループの規則が、置き換えるのではなく隣に並ぶ相手。
- [`demos/showcase/scenarios/alert.yaml`](../../demos/showcase/scenarios/alert.yaml)：iOS 26 でこの失敗を
  再現し、iOS 18 では通るシナリオ。

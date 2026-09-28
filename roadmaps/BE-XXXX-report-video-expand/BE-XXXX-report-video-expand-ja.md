[English](BE-XXXX-report-video-expand.md) · **日本語**

# BE-XXXX — report.htmlで動画を拡大し、ステップ一覧と同期させる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-report-video-expand-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | [#2071](https://github.com/bajutsu-e2e/bajutsu/pull/2071) |
| トピック | Authoring experience |
<!-- /BE-METADATA -->

## はじめに

report.htmlは、各シナリオの画面録画を、Resultパネルの横にある幅300px固定のカラムに埋め込みます
（[report.css:50](../../bajutsu/templates/report.css)）。この項目では、すべての録画に拡大コント
ロールを追加します。押すと、動画を大きく表示するモーダルが開き、そのシナリオのステップ一覧が複製
されて横に並びます。ステップ一覧は、すでに実行中のステップをハイライトし、クリックで動画をシーク
できます。この項目は、その同期の仕組みを拡大表示にもそのまま引き継ぎます。

## 動機

失敗の原因を調べる読者は、画面で何が起きたかを見る必要があります。report.htmlの動画カラムは、
録画自体の解像度によらず幅300pxのままです（[report.css:64](../../bajutsu/templates/report.css)）。
Web（Playwright）バックエンドの録画は、多くの場合解像度が高くなります。このカラムに収まると、
ボタンのラベルやアイコンが見分けにくいほど小さくなります。

ステップ一覧には、読者が必要とする同期の仕組みがすでにあります。各ステップ行は、録画内の位置を
`data-t`属性に保持しています。クリックするとその位置へ動画がシークし、再生中は実行中の行が
ハイライトされます
（[report.html.j2:45](../../bajutsu/templates/report.html.j2)、
[report.js:796](../../bajutsu/templates/report.js)〜[report.js:825](../../bajutsu/templates/report.js)）。
ただし、その文脈を失わずに録画を大きく見る場所は、reportのどこにもありません。

この項目が実装されると、録画の新しい拡大ボタンを押すことで、拡大した録画と、複製された同じステップ
一覧が横に並ぶモーダルが開きます。複製されたステップをクリックすると、現在のコンパクト表示のクリック
と同じように、拡大表示の動画がシークします。

## 詳細設計

### 拡大コントロールの追加

各プレイヤーの操作バー（`.vctl`、[report.html.j2:129](../../bajutsu/templates/report.html.j2)）に、
既存の`.vplay`と並べてボタンを追加します。

```html
<button class="vexpand" type="button" aria-label="expand video">⤢</button>
```

スタイルは`report.css`に追加し、`.vplay`の大きさと配色に合わせます
（[report.css:66](../../bajutsu/templates/report.css)〜[report.css:68](../../bajutsu/templates/report.css)）。
クリックの配線は、既存の委譲パターン（`ROOT.addEventListener('click', ...)`、
[report.js:9](../../bajutsu/templates/report.js)以降）に追加します。
`e.target.closest('.vexpand')`でボタンを見つけ、その`.player`要素を辿って`vzOpen(player)`を呼びます。

### モーダルの骨格

`report.html.j2`に、`.tv`や`.imgz`と並ぶ新しいシングルトンを1つ追加します
（[report.html.j2:131](../../bajutsu/templates/report.html.j2)）。

```html
<div class="vz" id="vz">
  <div class="vz-box">
    <button class="vz-close" type="button" aria-label="close">✕</button>
    <div class="vz-tabs" hidden></div>
    <div class="vz-video"></div>
    <div class="vz-steps"></div>
  </div>
</div>
```

`report.css`には、`.tv` / `.tv-box`と同じ考え方の規則を追加します。`.vz`は`.tv`と同じ、固定表示
の全画面の暗幕です（[report.css:148](../../bajutsu/templates/report.css)）。`.vz-box`は横方向の
flexで、動画側が可変幅（`flex:1`）を、ステップ一覧側が固定幅320〜360px程度を受け持ちます。
録画を大きく見せることが目的のため、`.vz-box`の上限は`.tv-box`の
`width:min(94vw,1040px)`（[report.css:195](../../bajutsu/templates/report.css)）よりも広くします。
値は`width:min(96vw,1400px);height:min(92vh,900px)`のようにします。

`.vz-tabs`のボタンには、`.tab`と別の新しいクラス`.vz-tab`を与えます。見た目は`.tab`に似せますが、
クラス名は共有しません。`.tab`のROOTへの委譲クリックハンドラは`t.closest('.scn')`を前提にしています
（[report.js:9](../../bajutsu/templates/report.js)〜[report.js:14](../../bajutsu/templates/report.js)）。
`.vz-tabs`は`.scn`の外、モーダル側にあります。`.tab`のクラスをそのまま使うと、そのハンドラが誤って
反応します。`closest('.scn')`が`null`になり、例外になります。

固定幅320〜360pxのステップ列は、`.vz-box`自体が`96vw`まで縮む幅では、動画の取り分をわずかな
すき間にしてしまいます。幅760px以下では、既存の`.body`グリッドが同じ境界でカラム落ちするのと
同じ考え方で（[report.css:51](../../bajutsu/templates/report.css)）、`.vz-box`を縦方向のflexに
切り替えます。`.vz-steps`は`width:auto`にして、残った高さいっぱいに伸ばします。

### 動画は複製せず移動する

`report.js`には動画セットアップループがあります
（[report.js:545](../../bajutsu/templates/report.js)〜[report.js:772](../../bajutsu/templates/report.js)）。
これは`ROOT.querySelectorAll('.player').forEach(function(p){ ... })`という形をしています。その
`forEach`の直前に、次の宣言を1つだけ追加します。

```js
var videoHome = new WeakMap();   // <video> -> its original .player element
```

ループの中では、各プレイヤーの`<video>`を変数`v`へ解決した直後に、元の居場所を記録します。

```js
videoHome.set(v, p);
```

`vzMount(player)`は、そのプレイヤーの`<video>`と`.vctl`操作バーの両方を、単純な`appendChild`で
`.vz-video`へ移します。これは複製ではなくDOMの移動です。`.vctl`も一緒に移すのは、拡大表示でも
再生/一時停止ボタンとシークバーを使えるようにするためです。動画だけを移すと、この操作バーが元の
プレイヤーに取り残されてしまいます。再生位置、一時停止状態、すでに張られているイベントリスナー
（`play` / `pause` / `timeupdate` / `syncSiblings`、
[report.js:746](../../bajutsu/templates/report.js)〜[report.js:771](../../bajutsu/templates/report.js)）
は、要素を移動しただけでは外れないため、そのまま保たれます。移した`.vctl`が持つ`.vexpand`ボタンは、
モーダルの中に移った時点で`closest('.player')`が`null`になり、押しても何も起きません。`vzMount`は
このボタンに`hidden`を付け、何も起きないボタンをそのまま見せないようにします。`.vexpand`は自身の
規則に`display:flex`を持つため、詳細度に関係なくUAスタイルシートの`[hidden]{display:none}`に勝って
しまいます。`report.css`には`.vz-tabs[hidden]`や`.sttbl>tbody>tr[hidden]`と同じように、
`.vexpand[hidden]{display:none}`という形でこの規則を書き直します。両方の子要素が抜けた元の
`.player`には`hidden`を付けます。

`vzRestore()`はこれを元へ戻します。`home.appendChild(vzActive)`で動画を戻し、続けて`.vz-video`に
残っている`.vctl`の`.vexpand`から`hidden`を外してから、同じく`appendChild`で操作バーを戻します。
`player`が持つ子要素は動画と`.vctl`だけなので、この順番でappendChildするだけで元の並びが
再現できます。最後に`hidden`を外して再表示します。

プレイヤーの`hidden`を切り替えると、`.players`の高さも変わります。そのため`vzRestore`は、既存の
`syncResultHeight(scn)`
（[report.js:492](../../bajutsu/templates/report.js)〜[report.js:504](../../bajutsu/templates/report.js)）
も呼び直します。Resultタブの高さ計算を、現在のプレイヤー構成に合わせるためです。

マルチターゲットシナリオ
（[BE-0428](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution-ja.md)）
の他の録画は、元の場所に残ったまま同期再生を続けます。`syncSiblings`
（[report.js:589](../../bajutsu/templates/report.js)）は、`<video>`要素がどの親の下にあっても、
同じノードを追い続けます。

### ステップ一覧の複製

`vzBuildSteps(player)`は、`scn.querySelectorAll('tr.srow[data-target], tr.skip[data-target]')`で
ドキュメント上の並び順をたどり、2種類の行を残します。1つはこのプレイヤー自身の`tr.srow`で、既存の`rowsFor(scn, target)`
ヘルパー（[report.js:528](../../bajutsu/templates/report.js)）でどれが自分の行かを判定します。
もう1つは`tr.skip`のすべてです。これは実行が途中で止まり、一度も走らなかったステップです
（[rows.py](../../bajutsu/common/report/rows.py)の`_step_skip_row`）。`tr.skip`は自分の`target`を
持ちません。そのステップを走らせるはずだった対象が、名乗る機会を得られなかったためです。そのため
実行済みの行のようにプレイヤーへ絞り込めず、無条件にすべて残します。これは、次にどの対象が実行する
予定だったかにかかわらず、コンパクト表示がすでに同じ未実行ステップを1回だけ見せているのと同じ挙動に
合わせるためです。単一ターゲットのシナリオでは`rowsFor`が持っている全行を返し、マルチターゲットの
シナリオでは該当ターゲットの行だけを返します。`rowsFor`が返すのは本体行（`tr.srow[data-target]`）
だけであり、その行自身の付随行
（`.alertrow` / `.actrow` / `.genrow`）は含みません。そのため`vzBuildSteps`は、残した各行の
直後にある付随行も、あれば同様に複製します。`tr.skip`は`data-t`を持たないため、複製はしても
ハイライトとシークの対象配列には加えません。

セレクタの両側に`[data-target]`を付けているのは、`tr.srow`側だけの都合ではありません。検証結果
テーブル（`.extbl`、`exrow`マクロ、[report.html.j2:61](../../bajutsu/templates/report.html.j2)）も、
runが到達しなかった`expect`を同じ`class="skip"`で描画しますが、こちらには`data-target`が付きません。
`tr.skip`側でこの絞り込みを外すと、この4列の行が7列の`.vz-sttbl`グリッドへ紛れ込み、レイアウトが
崩れます。

あるターゲットの全ステップが（別のターゲットのステップが先に失敗したことにより）未実行だった場合、
そのプレイヤー自身の`tr.srow`はありませんが、シナリオ共有のスキップ行はそれでもそのモーダルに
表示すべきものです。`vzBuildSteps`が処理を打ち切るのは、`mine`の件数だけで判断するのではなく、
組み立てた`tbody`に子要素が1つもなかったときに限ります。

複製先には、`<table class="sttbl vz-sttbl"><tbody></tbody></table>`のように、明示的な`tbody`を
持つテーブルを作ります。単に`<tr>`を追加していくだけでは足りません。既存のステップ行の規則は
すべて`.sttbl>tbody>tr`という形でスコープされています
（[report.css:342](../../bajutsu/templates/report.css)〜[report.css:416](../../bajutsu/templates/report.css)）。
`tbody`を自動で補うのはHTMLパーサだけです。素の`<table>`へ`<tr>`を`appendChild`しても、`tbody`は
生まれません。

複製は、1つを取り除き、1つをそのまま残します。取り除くのは、screenshot / element tree列
（`class="ev"`）の中身です。セルそのものではなく、中身だけを空にします（例:
`clonedCell.textContent = ''`）。`.sttbl`の行は、列位置を`nth-child`で決めるCSSグリッドです
（[report.css:383](../../bajutsu/templates/report.css)〜[report.css:416](../../bajutsu/templates/report.css)）。
セルそのものを取り除くと、後続の列がひとつずつ前へずれて壊れます。中身だけを空にすれば列は残り、
既存の`table.sttbl>…>td:empty{display:none}`（[report.css:372](../../bajutsu/templates/report.css)）
がその空セルを隠します。中身を空にすると、同じセルにある`class="shot"` / `class="treebtn"`も
一緒に消えます。これらを残すと、既存のElement Viewer（`.tv`）がこのモーダルの上に開いてしまいます
（[report.js:274](../../bajutsu/templates/report.js)）。

そのまま残すのは、折りたたまれた`group:`グループの行です（`hidden`属性ごと複製します）。
コンパクト表示のハイライトも、`hidden`な行を「実行中」として一度マークすることがあり、次に見える
行へ達するまで見た目のハイライトは出ません。複製もこの挙動をそのまま踏襲します。既存の
`.sttbl>tbody>tr[hidden]{display:none!important}`（[report.css:357](../../bajutsu/templates/report.css)）
が`vz-sttbl`にもそのまま効きます。そのため、追加のCSSはいりません。

もう1つ手順があります。複製した行すべてから`data-group-id`属性を取り除きます。`.grouptoggle`
ボタンを持つ見出し行（`.grouphead`）自体は`class="srow"`を持たず、`data-target`属性も持ちません。
そのため`rowsFor`は最初からこれを返さず、複製にも含まれません
（[BE-0439](../BE-0439-step-groups-report-folding/BE-0439-step-groups-report-folding-ja.md)）。
ただし複製した本体行と付随行自身は、元の行と同じ`data-group-id`を引き継ぎます。ROOTへの委譲は
この属性を手がかりに拾います
（[report.js:26](../../bajutsu/templates/report.js)〜[report.js:34](../../bajutsu/templates/report.js)）。
クリックされた`.grouptoggle`と同じ`data-group-id`を持つ行を、ROOT全体から探して開閉する仕組みです。
複製の行がこの属性を持ったままだと、コンパクト表示側の見出しをクリックしたとき、複製の行まで
一緒に開閉してしまいます。取り除くのは、この衝突を防ぐためです。コンパクト表示側で折りたたみ状態を
あとから変えても、モーダルを開いている間は複製に反映されず、次に開き直したときの状態が反映されます。

複製した行には、独自のクリック配線を与えます。既存の行ごとのハンドラ
（[report.js:796](../../bajutsu/templates/report.js)〜[report.js:816](../../bajutsu/templates/report.js)）
と同じ内容を使います。ただし、元の`v`の代わりに`vzActive`を使います。行のクリックや、その中の
`.stepjump`ボタンは、いま表示中の動画をシークします。ハイライトと自動スクロールも、既存の
`timeupdate`ハンドラ
（[report.js:817](../../bajutsu/templates/report.js)〜[report.js:825](../../bajutsu/templates/report.js)）
と同じ考え方にします。スクロールには、既存の`scrollIntoBox`ヘルパー
（[report.js:776](../../bajutsu/templates/report.js)）を使い、`.vz-steps`の中を動かします。畳まれて
`hidden`な行が「実行中」と判定された瞬間は、このヘルパーは座標0の矩形を受け取ります。これは
コンパクト表示のスクロールにもすでにある挙動であり、この項目で直す対象ではありません。

### マルチターゲットのタブ

`vzOpen(player)`は、`scn.querySelectorAll('.player')`でそのシナリオの全プレイヤーを集めます。
単一ターゲットのシナリオでは`.vz-tabs`を隠し、そのプレイヤーだけをマウントします。プレイヤーが
2つ以上あれば、`data-target`のラベルを付けたタブを1つずつ並べます。最初のタブは、拡大ボタンを
押したプレイヤーです。タブを切り替えると`vzMount`が再び呼ばれ、まず直前のターゲットの動画を元に
戻します。モーダルの中には常に1つの録画だけがあります。他のターゲットのプレイヤーは、モーダルを
開く前と同じコンパクト表示のまま見え、同期再生も続きます。

### 閉じる操作

`vzClose()`は、動画を元に戻し、複製したステップとタブを消し、`.open`を外します。`.tv` /
`.imgz`がすでに使っている3つの閉じ方
（[report.js:278](../../bajutsu/templates/report.js)〜[report.js:308](../../bajutsu/templates/report.js)）
と同じ配線にします。背景クリック（`e.target === vz`）、閉じるボタン、Escapeキーです。Escapeは、
`.tv` / `.imgz`のどちらかがすでに開いていれば、そちらを優先します。この項目では、3つのうち複数を
同時に開く経路自体を作りません。

### スコープ外

- Network / Device Log / App Traceの各タブには触れません。矢印キーによるステップ送り
  （`.tv`ビューア自体が持つ前後移動）にも触れません。モバイル幅専用のレイアウトも作り込まず、
  `.tv-box`が持つ素のモバイル挙動をそのまま流用します。
- 前提条件（preconditions）と検証結果（expects）は、どちらもモーダルに複製しません。どちらも
  録画内の位置を持たないため、この項目が加える同期の対象になりません。

## 検討した代替案

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| ブラウザのネイティブな`video.requestFullscreen()`を呼ぶ | 数行の実装で、OS標準の拡大表示に乗れます | フルスクリーンは動画だけを占有し、ステップ一覧の置き場所がありません。動画とステップの並びを大画面で読むという動機が満たされないまま残ります |
| モーダルを作らず、既存の`.body`グリッド（[report.css:50](../../bajutsu/templates/report.css)）の`grid-template-columns`をその場で書き換えて動画カラムを広げる | 新しいモーダルの骨格が不要です | ページの他の部分（他のシナリオカードやヘッダー）は元の位置に残ったままになり、モーダルが与える広い視界より窮屈になります。既存の`.tv`ビューアも同じ理由でモーダルを選んでいます |
| 複製せず、元のステップ行をそのままモーダルへ移動する | 行のDOM実体が1つで済み、リスナーの二重管理がありません | マルチターゲットのタブ切り替えのたびに、行を元のResultタブから抜き差しする必要があり、切り替えの一瞬だけResultタブが空になるといった事故が起きやすくなります。複製なら、モーダルを開いている間もResultタブは常に完全な状態を保ちます |

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `.vctl`に`.vexpand`ボタンを追加し、`report.css`でスタイルを与えます
- [x] `report.html.j2`に`.vz`モーダルの骨格を追加し、`report.css`にCSSを追加します。モバイル幅では
      動画とステップ一覧を横並びではなく縦に積む代替レイアウトも加えます
- [x] `report.js`に`videoHome`、`vzMount`、`vzRestore`を追加します。プレイヤーの`.vctl`バーも
      動画と一緒に移し、拡大表示でも再生/一時停止とシークバーを使えるようにします
- [x] `vzBuildSteps`を追加します（行の複製、シーク配線、ハイライト、自動スクロール）
- [x] `vzOpen`にマルチターゲットのタブ切り替えを追加します
- [x] 3つの閉じ方（背景クリック、閉じるボタン、Escape）を配線し、`.tv` / `.imgz`との優先順位を付けます
- [x] ダークモードに新しい配色が必要ないことを確認します。新しい要素は既存の`--card` /
      `--ink` / `--line`トークンを再利用し、操作系（`.vexpand` / `.vz-tab` / `.vz-close`）は
      `.vplay`と同じ固定のダーク配色をそのまま使います
- [x] `docs/reporting.md`と`docs/ja/reporting.md`を更新します
- [x] `make check`が通ることを確認します

## 参考

- [docs/specs/report-video-expand.md](../../docs/specs/report-video-expand.md) — この項目が要約
  する詳細な技術設計（日本語）
- [BE-0428 — マルチターゲットシナリオ実行](../BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution-ja.md)
  — この項目のタブが扱う、1シナリオに複数の録画がある場合
- [BE-0439 — ステップを名前付き区画にまとめ、report.htmlで折りたたむ](../BE-0439-step-groups-report-folding/BE-0439-step-groups-report-folding-ja.md)
  — この項目の複製したステップ一覧が回避し続ける折りたたみ操作との衝突と、その方法
- [docs/reporting.md](../../docs/reporting.md#reporthtml) — この項目が更新するドキュメント

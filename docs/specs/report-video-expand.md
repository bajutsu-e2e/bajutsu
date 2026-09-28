# report.htmlでの動画拡大表示とステップ一覧の同期パネル

> ステータス: ドラフト
> 対象: `bajutsu/templates/report.html.j2`、`bajutsu/templates/report.css`、`bajutsu/templates/report.js`
> 関連: [BE-0428](../../roadmaps/BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md)（マルチターゲット動画）、[BE-0439](../../roadmaps/BE-0439-step-groups-report-folding/BE-0439-step-groups-report-folding.md)（ステップの折りたたみ）、[docs/reporting.md](../reporting.md)

各シナリオの動画プレイヤーに拡大ボタンを追加する。押すと、動画本体を画面いっぱいのモーダルへ移し、その右側にそのシナリオのステップ一覧を複製して並べる。動画の再生に合わせてどのステップが実行中かをハイライトする挙動は、既存のコンパクト表示にあるものをモーダル側にも再現する。

## 1. なにをつくるのか

`report.html`の各シナリオカードは、左側に固定幅300pxの動画プレイヤーを置く（[report.css:50](../../bajutsu/templates/report.css)）。右側には実行結果のタブ（Result/Network/Device Log/App Trace）を並べる。動画はこの300px幅に収まるだけで、それより大きく見る手段はない。

この項目は、動画プレイヤーの操作バー（`.vctl`、[report.html.j2:129](../../bajutsu/templates/report.html.j2)）に拡大ボタンを追加する。押すと、次の要素を持つ全画面モーダルが開く。

- **動画本体。** 元のプレイヤーが持っていた`<video>`要素そのものをモーダルへ移し、モーダルの横幅いっぱいに大きく表示する。再生位置と再生/一時停止の状態は、移す前後で変わらない。
- **ステップ一覧。** そのシナリオのResultタブが持つステップテーブル（`before`テーブル、シナリオ本体のステップテーブル、`after`テーブルのうち存在するもの）を複製する。それをモーダルの右側に固定幅で並べる。ステップをクリックすると、既存のコンパクト表示と同じく動画がその時刻へシークする。動画の再生に合わせて、今実行中のステップの行にハイライトが付く。一覧は自動的にスクロールし、そのステップを表示範囲に収める。

閉じるボタン、背景クリック、Escapeキーのいずれでもモーダルを閉じられる。閉じると、動画は元のプレイヤーへ戻り、コンパクト表示は開く前の状態に復元される。

マルチターゲットシナリオ（1シナリオに動画が複数、BE-0428）では、どのプレイヤーの拡大ボタンを押しても同じモーダルが開き、上部にターゲットごとのタブが並ぶ。タブを切り替えると、モーダルが表示する動画とステップ一覧が、そのターゲットの持ち分に入れ替わる。

### やらないこと

- **Result以外のタブ（Network / Device Log / App Trace）をモーダルに含めることは対象外にする。** モーダルは動画とステップ一覧だけを扱う。他のタブの情報が要る場合は、モーダルを閉じて元のResultパネルで確認する。
- **前提条件（preconditions）と検証結果（expects）は複製しない。** どちらも動画の再生位置と結びつかない情報である。今回の同期表示の対象は、ステップ一覧に絞る。
- **`group:`グループの折りたたみ開閉（[BE-0439](../../roadmaps/BE-0439-step-groups-report-folding/BE-0439-step-groups-report-folding.md)）は再現しない。** モーダルを開いた時点でコンパクト表示が持つ行を、そのまま複製する。畳まれている行も`hidden`属性ごと複製し、折りたたみの見た目はコンパクト表示と同じ状態にする。ただし畳んだり開いたりする操作自体は複製に持ち込まない。コンパクト表示側で折りたたみ状態をあとから変えても、開いたままのモーダルには反映しない。次に開き直せば、そのときの状態を反映する。
- **ステップのscreenshot / element tree列（`view`列）は複製に含めない。** この列のクリックは、既存のElement Viewer（`.tv`）を開く。開き方は`ROOT`全体への委譲イベントである（[report.js:274](../../bajutsu/templates/report.js)）。複製した行に同じ`class="shot"` / `class="treebtn"`があると、モーダルの上にElement Viewerが開いてしまう。この列を含めないことで、その重なりを避ける。
- **モーダル内での矢印キーによるステップ送りは対象外にする。** Element Viewerの◀ / ▶と同種の操作である。動画の再生位置に沿った自動ハイライトと、行クリックによるシークがあれば、今回の動機（動画が小さくて見づらい）には足りる。
- **モバイル幅専用のレイアウト作り込みは対象外にする。** 既存の`.tv`も760px以下で特別な調整をしていない。[report.css:51](../../bajutsu/templates/report.css)のブレークポイントは`.body`グリッドのみを対象にする。モーダルも同じ扱いにとどめ、`.tv-box`と同様のはみ出し防止（`width:min(...)`、`max-height:...`）だけを持たせる。

## 2. なぜつくるのか

動画プレイヤーは300px幅の固定カラムに収まるだけであり（[report.css:50](../../bajutsu/templates/report.css)、[report.css:64](../../bajutsu/templates/report.css)）、これより大きく見る手段は現状ない。Web（Playwright）バックエンドの録画は、多くの場合解像度が高い。この幅では、ボタンの文字やアイコンを判別しにくい。失敗調査でまず確認したいのは「画面のどこで何が起きたか」だ。動画が小さいままでは、確認そのものに手間がかかる。

一方で、ステップとの同期という土台はすでにある。各ステップ行は、動画内の再生位置（秒数）を`data-t` / `data-t-end`属性に持つ（[report.html.j2:45](../../bajutsu/templates/report.html.j2)）。クリックでその位置へシークでき、動画の再生中は現在位置に対応する行が自動でハイライトされる。表示範囲外なら、自動でスクロールもされる（[report.js:796](../../bajutsu/templates/report.js)〜[report.js:825](../../bajutsu/templates/report.js)）。この同期の仕組みは、動画を大きく表示する場所さえ用意すれば、そのまま転用できる。

似た構造を持つ既存の仕組みとして、Element Viewer（`.tv`）がある。これは1つのステップの画面キャプチャとアクセシビリティ要素一覧を、画面中央のモーダルに並べる。並べ方は左右2ペインで、左に固定表示の画像、右にスクロールする一覧を置く（[report.css:222](../../bajutsu/templates/report.css)〜[report.css:237](../../bajutsu/templates/report.css)）。今回作る動画拡大モーダルも、「左（または中央）に大きな表示対象、右にスクロールする一覧」という同じ骨格を持つ。この既存パターンをそのまま土台にできるため、ゼロから新しいモーダルの様式を起こす必要はない。

放置した場合、動画が小さいという困りごとは、Webバックエンドの利用が増えるほど頻繁に持ち上がる。ブラウザのネイティブなフルスクリーン再生（`video.requestFullscreen()`）で動画だけを拡大する対処もある。ただしその場合はステップ一覧が見えなくなり、どの瞬間がどのステップに対応するかを目視で追う手間が残る。動画とステップ一覧を同じ画面に大きく並べることで、その手間をなくす。

## 3. どう実現するか

### 拡大ボタンの追加

`.vctl`（[report.html.j2:129](../../bajutsu/templates/report.html.j2)）に、既存の`.vplay`と並べて拡大ボタンを追加する。

```html
<button class="vexpand" type="button" aria-label="expand video">⤢</button>
```

スタイルは`report.css`に追加する。大きさと配色は、`.vplay`規則（[report.css:66](../../bajutsu/templates/report.css)〜[report.css:68](../../bajutsu/templates/report.css)）に合わせる。

クリックの配線は、既存の委譲パターン（`ROOT.addEventListener('click', ...)`、[report.js:9](../../bajutsu/templates/report.js)ほか）に1つ追加する。`e.target.closest('.vexpand')`でボタンを見つけ、その`.player`要素を辿って`vzOpen(player)`を呼ぶ。

### モーダルの骨格

`report.html.j2`に、`.tv` / `.imgz`と並ぶ新しいシングルトンを1つ追加する（[report.html.j2:131](../../bajutsu/templates/report.html.j2)、`.imgz`の直後）。

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

`report.css`に、`.tv` / `.tv-box`と同じ考え方の規則を追加する。

- `.vz`は`.tv`と同じ`position:fixed;inset:0`の暗幕（[report.css:148](../../bajutsu/templates/report.css)を参考にする）。
- `.vz-box`は横方向のflexにする。`.vz-video`が可変幅（`flex:1`）を受け持ち、`.vz-steps`が固定幅320〜360px程度を受け持つ。全体の大きさは`width:min(96vw,1400px);height:min(92vh,900px)`のようにする。`.tv-box`の`width:min(94vw,1040px)`（[report.css:195](../../bajutsu/templates/report.css)）よりも広い上限を持たせる。動画を大きく見せることが動機のため、`.tv-box`よりも広い枠を許す。
- `.vz-video`は縦方向のflexにする。動画（可変の高さ）の下に、動画から移した`.vctl`操作バーを固定高さで置く（後述）。`.vz-video video`は`width:100%;height:100%;object-fit:contain`で、割り当てられた高さいっぱいに収まるだけ大きく表示する。
- `.vz-tabs`は複数ターゲットのときだけ`hidden`を外し、`.vz-tab`という新しいクラスのボタンを並べる。見た目は`.tab`に似せるが、クラス名は共有しない。`.tab`のROOTへの委譲クリックハンドラは`t.closest('.scn')`を前提にしている（[report.js:9](../../bajutsu/templates/report.js)〜[report.js:14](../../bajutsu/templates/report.js)）。`.vz-tabs`は`.scn`の外、モーダル側にある。`.tab`のクラスをそのまま使うと、そのハンドラが誤って反応し、`closest('.scn')`が`null`になって例外になる。
- 幅760px以下では、`.vz-box`を縦方向のflexに切り替え、`.vz-steps`の幅を`auto`にして高さ方向へ伸ばす。`.vz-steps`の固定幅320〜360pxをそのまま残すと、`.vz-box`自体が`96vw`まで縮む画面幅で動画の取り分がわずか数十ピクセルまで潰れてしまう。これは既存の`.body`グリッドが同じ760pxの境界でカラム落ちする（[report.css:51](../../bajutsu/templates/report.css)）のと同じ考え方である。

### 動画本体の移動と復元

`report.js`の動画セットアップループ（各`.player`を`v` / `btn` / `seek` / …に分解する箇所、[report.js:582](../../bajutsu/templates/report.js)〜[report.js:822](../../bajutsu/templates/report.js)）がある。このループは`ROOT.querySelectorAll('.player').forEach(function(p){ ... })`という形をしている。その`forEach`の直前に、次の宣言を1つだけ追加する。

```js
var videoHome = new WeakMap();   // <video> -> its original .player element
```

ループの中では、`var v = p.querySelector('video')`のすぐ後、`if(!v || !btn || !seek || !time) return;`という早期リターンより前に`if(v) videoHome.set(v, p);`を追加する。動画要素から「元のプレイヤーdiv」への対応をこのWeakMapが持つため、動画がモーダルへ移ってプレイヤー内から消えても、閉じるときにどこへ戻すかをこれで引ける。`if(v)`で包むのは、`WeakMap.set`がオブジェクト以外のキーを受け付けず、`v`が`null`のとき例外になるためである。早期リターンより前に置くのは、`.vplay` / `.vseek` / `.vtime`のいずれかを欠く`.player`でも動画そのものは持ちうるためだ。登録をこの早期リターンの後ろに置くと、`vzMount`がそのような動画を戻す先を見失い、モーダルを閉じても録画が静かに消える。

モーダル側は、要素への参照と状態を次の変数で持つ。

```js
var vz = ROOT.getElementById('vz');
var vzVideoSlot = vz && vz.querySelector('.vz-video');
var vzSteps = vz && vz.querySelector('.vz-steps');
var vzTabs = vz && vz.querySelector('.vz-tabs');
var vzActive = null;          // the <video> currently mounted inside the modal
var vzTimeupdate = null;      // the timeupdate listener currently bound to vzActive, so it can be removed on swap
```

`vzMount(player)`は、指定したプレイヤーの動画をモーダルへ移す。

1. すでに別の動画がモーダル内にあれば、`vzRestore()`で元のプレイヤーへ戻す。
2. `player.querySelector('video')`と`player.querySelector('.vctl')`を、両方ともモーダルの`.vz-video`へ`appendChild`する。これはDOMの移動であり、複製ではない。再生位置、一時停止状態、すでに張られているイベントリスナーは、どれもそのまま保たれる。対象は`play` / `pause` / `timeupdate` / `syncSiblings`などである（[report.js:796](../../bajutsu/templates/report.js)〜[report.js:822](../../bajutsu/templates/report.js)）。`.vctl`も一緒に移すのは、再生/一時停止ボタンとシークバーを拡大表示でも使えるようにするためである。動画だけを移すと、この操作バーが元のプレイヤーに取り残され、コントロールを持たない拡大動画になってしまう。
3. 移した`.vctl`が持つ`.vexpand`ボタンには`hidden`を付ける。このボタンは`.player`を辿ってモーダルを開く仕組みのため、モーダルの中に移った状態では`closest('.player')`が`null`になり、押しても何も起きない。何も起きないボタンをそのまま見せずに隠す。`.vexpand`は`display:flex`を自身の規則に持つため、`report.css`には`.vexpand[hidden]{display:none}`を別途追加する。作者が指定した`display`は、詳細度に関係なくUAスタイルシートの`[hidden]{display:none}`に勝ってしまうためである。
4. `player`自身に`hidden`属性を付ける。動画と`.vctl`の両方が抜けた元のプレイヤーは空になるため、プレイヤーごと隠す。マルチターゲットで他のプレイヤーが残っている場合、それらは今まで通りコンパクト表示のまま見える。再生も同期され続ける。`syncSiblings`（[report.js:639](../../bajutsu/templates/report.js)）は、`v`がどのDOM位置にあっても同じインスタンスを指し続けるため、この移動の影響を受けない。
5. `vzActive = v`とし、`vzBuildSteps(player)`でステップ一覧を組み立てる。

`vzRestore()`は、`vzActive`があれば`videoHome.get(vzActive)`で元のプレイヤーを引く。`home.appendChild(vzActive)`で動画を戻し、続けて`.vz-video`に残っている`.vctl`の`.vexpand`から`hidden`を外してから、同じく`home.appendChild`で操作バーを戻す。`appendChild`だけで正しい順序（動画の次に`.vctl`）に戻るのは、`player`要素にはこの2つ以外の子（マルチターゲットの`tgtlbl`ラベルを除く）がなく、末尾に追加するだけで元の並びを再現できるためである。`home.hidden = false`で再表示し、`vzActive`と結び付けていた`timeupdate`リスナーがあれば`removeEventListener`で外す。プレイヤーの`hidden`を切り替えると`.players`の高さも変わる。そのため、既存の`syncResultHeight(scn)`（[report.js:529](../../bajutsu/templates/report.js)〜[report.js:541](../../bajutsu/templates/report.js)）を呼び直す。Resultタブの高さ計算を、この時点のプレイヤー構成に合わせて更新するためである。

### ステップ一覧の複製

`vzBuildSteps(player)`は、そのプレイヤーが担当するターゲットのステップ行だけを複製する。行の所有判定には、既存の`rowsFor(scn, target)`（[report.js:565](../../bajutsu/templates/report.js)）をそのまま使う。この関数（[report.js:555](../../bajutsu/templates/report.js)〜[report.js:573](../../bajutsu/templates/report.js)のownedTargets/rowsFor）は、単一ターゲットシナリオでは全行を返す。複数ターゲットシナリオでは、対象ターゲットの行だけを返す。ただし`rowsFor`が返すのは本体行（`tr.srow[data-target]`）だけであり、`.alertrow` / `.actrow` / `.genrow`のような付随行は含まない。

`rich()`（report.html.j2）は、1つのシナリオにつき`.steps-sec`ブロックを最大3つ描画する。`before` / シナリオ自身のステップ / `after`の3区分で、それぞれが自分の`.deflbl`見出しと自分の`.sttbl`を持ち、行番号は区分ごとに0から数え直す。この3区分を1つのテーブルへ平らに詰めると、別の区分にある「0番目」の行同士が番号で衝突し、どちらの区分の行かも失われる。そこで`vzBuildSteps`は、シナリオの`.steps-sec`を順に取り出し、区分ごとに1つの`.vz-sttbl`を組み立てる。

1. 区分ごとのループの中で、その区分の`table.sttbl`（`section.querySelector('table.sttbl')`）から`srcTable.querySelectorAll('tr.srow[data-target], tr.skip[data-target]')`を取り、実行済み行と未実行行をドキュメント上の並び順で集める。`[data-target]`を両方に付けるのは、`rowsFor`自身が使うのと同じ絞り込みである。検証結果テーブル（`.extbl`、`exrow`マクロ）も、評価されなかった`expect`を同じ`class="skip"`で描画するが、こちらには`data-target`が付かない。`[data-target]`を付けないと、この4列の行が7列の`.vz-sttbl`グリッドへ紛れ込み、レイアウトが崩れる。
2. 集めた行それぞれについて、`r.classList.contains('skip') || mine.indexOf(r) !== -1`で複製するかどうかを判定する（`mine`は手順の冒頭で求めた`rowsFor(scn, target)`の戻り値）。`tr.skip`は無条件で複製する。`rows.py`側でそもそも`target`が渡されないため（そのステップを走らせるはずだった対象がまだ決まっていない）、`rowsFor`によるターゲット絞り込みができないからだ。絞り込まずに全件を複製するのは、コンパクト表示の1つの`.sttbl`が最初からターゲットを問わず未実行行を並べて見せているのと同じ挙動に合わせるためである。判定を通った行は`cloneNode(true)`で複製し、新しく作った`tbody`へ追加する。続けてその行の直後にある付随行（`.alertrow` / `.actrow` / `.genrow`）もあれば同様に複製する。1つのステップがこれらの付随行を出しうる仕組みは、[report.html.j2:45](../../bajutsu/templates/report.html.j2)にある。`tr.skip`は`data-t`を持たないため、ハイライトとシークの対象配列である`cloneRows`には、本体行（`r.classList.contains('srow')`）だけを加える。
3. 複製した行のうち、`class="ev"`のセル（screenshot / element tree列）は、複製後に中身を空にする（`evCell.textContent = ''`）。`.sttbl`の行は、列位置を`nth-child`で決めるCSSグリッドである（[report.css:383](../../bajutsu/templates/report.css)〜[report.css:416](../../bajutsu/templates/report.css)）。セルそのものを取り除くと、後続の列がひとつずつ前へずれて壊れる。空にするだけなら列は残り、既存の`table.sttbl>…>td:empty{display:none}`（[report.css:372](../../bajutsu/templates/report.css)）がその空セルを自然に隠す。中身を空にするのは、「やらないこと」に書いた、Element Viewerとの重なりを避けるためである。
4. 複製した本体行と付随行は、どちらも`hidden = false`を明示して`hidden`属性を落とす。コンパクト表示がデフォルトで畳む`group:`グループの中身は、元の行では`hidden`が付いている。モーダル自身には`.grouptoggle`のような開閉の手段がなく、`.grouphead`見出し行自体も（`srow`も`skip`も持たないため）複製されない。畳まれたままの行を複製すると、モーダルの中でその行へ二度と辿り着けなくなる。折りたたみ状態にかかわらず常に表示することで、この行き止まりを避ける。
5. 複製した本体行と付随行から`data-group-id`属性を取り除く。`.grouptoggle`のクリックは`data-group-id`を手がかりにROOT全体から同じ値を持つ行を探して開閉する。この属性を複製へ残すと、コンパクト表示側の見出しをクリックしたときに複製の行まで一緒に開閉してしまう。

区分ごとのループを終えたら、その区分で1行でも複製できていれば（`tbody.children.length`）、区分の`.deflbl`の文字列を複製した`<span class="deflbl">`と、`class="sttbl vz-sttbl"`の新しい`<table>`を`.vz-steps`へ追加する。複製が0件だった区分（例えば`after`がないシナリオ）は、そのままスキップしてテーブルを作らない。全区分を通じて1件も複製できなかった場合に限り、`vzBuildSteps`は`.vz-steps`を空のまま何もせず終える。あるターゲットの全ステップが（他のターゲットの失敗により）未実行だった場合でも、そのプレイヤー自身の`tr.srow`がゼロ件というだけで打ち切らない。シナリオ共有の`tr.skip`はまだ複製できる余地があるためである。

複製した行への操作は、既存の`ROOT`委譲イベントには頼らない。`vzBuildSteps`の中で、`.vz-steps`自身への委譲クリックハンドラを1つだけ配線する。個別の行ではなくコンテナへ委譲するのは、区分ごとに新しい`<table>`をまるごと作り直す都合上、行1つずつへ`addEventListener`するより、複製のたびに張り直す配線を1箇所にまとめるほうが単純だからだ。このハンドラは`vzBuildSteps`が呼ばれるたびに一度`removeEventListener`で外してから張り直す。

```js
vzStepClick = function(e){
  if(!vzActive) return;
  var jump = e.target.closest('.stepjump');
  if(jump){
    e.stopPropagation();
    var jt = parseFloat(jump.getAttribute('data-t'));
    if(!isNaN(jt)) vzActive.currentTime = jt;
    return;
  }
  if(e.target.closest('a')) return;
  var row = e.target.closest('tr.srow');
  if(!row) return;
  var t = parseFloat(row.getAttribute('data-t'));
  if(!isNaN(t)) vzActive.currentTime = t;
};
vzStepsEl.addEventListener('click', vzStepClick);
```

`.stepjump`を先に判定して`e.stopPropagation()`で打ち切るのは、ステップの開始/終了ジャンプボタンを押したときに、続けて行本体のクリックが発火し、ジャンプ直後の位置へ再シークし直してしまう事故を防ぐためである。

ハイライトと自動スクロールも、コンパクト表示側の`timeupdate`ハンドラと同じ考え方を、`cloneRows`（本体行だけを集めた配列）に対して行う。「いま再生中の行はどれか」という判定そのものは、コンパクト表示・モーダルの双方で共有する`pickPlayingRow(rows, currentTime)`（[report.js:837](../../bajutsu/templates/report.js)〜[report.js:844](../../bajutsu/templates/report.js)）という関数へ切り出す。現在時刻以下の`data-t`を持つ行のうち、もっとも遅いものを返すだけの関数で、この判定基準を2箇所で書き分けると、片方だけ直して他方が古いままという食い違いが起こりうる。共有することでその心配をなくす。

```js
vzTimeupdate = function(){
  var cur = pickPlayingRow(cloneRows, vzActive.currentTime);
  cloneRows.forEach(function(r){ r.classList.toggle('playing', r === cur); });
  if(cur !== lastCur){ lastCur = cur; if(cur) scrollIntoBox(vzStepsEl, cur); }
};
vzActive.addEventListener('timeupdate', vzTimeupdate);
```

この処理は、`vzMount`が`vzActive`を差し替えるたびに張り直す。そのため、`vzActive`に登録したリスナーへの参照を`vzTimeupdate`に保持し、`vzRestore`で確実に外す。自動スクロールの対象は`.vz-steps`自身にする。既存の`scrollIntoBox`（[report.js:826](../../bajutsu/templates/report.js)）がそのまま使える。第一引数を`.vz-steps`に替えるだけでよい。手順4のとおり、モーダルの複製行はどれも`hidden`を持たないため、「実行中」と判定された行が座標0の矩形しか持たない事態は起こらない。

### マルチターゲットのタブ

`vzOpen(player)`は、まずそのシナリオの全プレイヤーを集める。`var scn = player.closest('.scn')`と`var players = Array.from(scn.querySelectorAll('.player'))`で行う。

- `players.length === 1`なら`vzTabs.hidden = true`とし、そのままそのプレイヤーで`vzMount`する。
- `players.length > 1`なら、`players`の順にタブボタンを`vzTabs`へ並べる。ラベルは`data-target`の値を使い、無名の主ターゲットには「primary」のような固定ラベルを与える。タブのクリックで`vzMount`にそのプレイヤーを渡す。初期タブは、押した`.vexpand`が属するプレイヤーにする。

タブを切り替えるたびに`vzMount`が`vzRestore`から始まるため、直前まで表示していたターゲットの動画は自動的に元のコンパクト表示へ戻り、そのプレイヤーも再表示される。切り替え後の新しいターゲットのプレイヤーだけが隠れる。

### 開閉とキー操作

`vzClose()`は`vzRestore()`を呼ぶ。続けて`vzSteps.innerHTML = ''`、`vzTabs.innerHTML = ''`、`vz.classList.remove('open')`を行う。

配線は`.tv` / `.imgz`の既存パターン（[report.js:278](../../bajutsu/templates/report.js)〜[report.js:308](../../bajutsu/templates/report.js)）に合わせる。

- 背景（`vz`要素自身）のクリックで閉じる。`.vz-box`内のクリックは`stopPropagation`しない。そのぶん、`e.target === vz`の判定で背景だけに絞る（`.tv`の`if(e.target === tv) tvClose()`と同じ書き方）。
- `.vz-close`ボタンのクリックで閉じる。
- Escapeキーで閉じる。既存の`keydown`リスナー（[report.js:282](../../bajutsu/templates/report.js)、[report.js:302](../../bajutsu/templates/report.js)）と同様、`.tv` / `.imgz`が開いていないときに限って`vz`のEscapeを処理する。同時に開く経路は今回作らないため、実際には排他になる。

## 4. 検討した代替案と、採らなかった理由

| 案 | 概要 | 採らなかった理由 |
|---|---|---|
| ブラウザのネイティブなフルスクリーンAPI（`video.requestFullscreen()`）をボタンから呼ぶ | 実装が数行で済み、OS標準の拡大表示に乗れる | フルスクリーンは動画だけを占有し、ステップ一覧を同じ画面に出せない。動機である「動画とステップの対応を大きな画面で追う」を満たせない |
| モーダルではなく、シナリオカードの`.body`グリッド自体を一時的に組み替えて動画カラムを広げる | 新しいモーダルの骨格を作らずに済み、既存の`.body`グリッド（[report.css:50](../../bajutsu/templates/report.css)）の`grid-template-columns`を書き換えるだけで足りる | ページの他の部分（他のシナリオカードやヘッダー）がスクロール位置に残ったまま動画だけ拡大されるため、「大きく見る」という動機に対して視界が狭い。既存の`.tv`がモーダルという形を選んでいるのも同じ理由からであり、様式を割る理由がない |
| ステップ一覧を複製せず、元のResultタブの行をそのままモーダルへ移動する（複製ではなく移動） | イベントリスナーの二重登録を避けられ、DOM実体が1つで済む | 複数ターゲットのタブ切り替えのたびに、元のResultタブから行を抜き差しする必要があり、閉じ忘れや切り替え中の一瞬だけ元のResultタブが空になるなど、状態管理が複雑になる。複製なら、モーダルを開いている間も元のResultタブは常に完全な状態を保つ |

## 5. 作業手順

| # | やること | 触るファイル | 完了条件 | 前提 |
|---|---|---|---|---|
| 1 | `.vctl`に`.vexpand`拡大ボタンを追加し、`.vplay`と並ぶスタイルを与える | `bajutsu/templates/report.html.j2`、`bajutsu/templates/report.css` | `demos/showcase`で生成した`report.html`をブラウザで開き、各シナリオの動画プレイヤーに拡大ボタンが表示されることを目視で確認する | — |
| 2 | `.vz`モーダルの骨格（`.vz-box` / `.vz-tabs` / `.vz-video` / `.vz-steps`）を`report.html.j2`に追加し、対応するCSSを`report.css`に追加する。幅760px以下では`.vz-box`を縦積みに切り替える代替レイアウトも加える | `bajutsu/templates/report.html.j2`、`bajutsu/templates/report.css` | ブラウザの開発者ツールで`#vz`要素が存在し、`.open`クラスを手動で付けると画面いっぱいに表示されることを確認する。幅を760px以下に狭めたとき、動画とステップ一覧が横並びではなく縦に積まれることを確認する | 1 |
| 3 | `videoHome`のWeakMapと、`vzMount` / `vzRestore`を実装し、拡大ボタンのクリックで動画と`.vctl`操作バーがモーダルへ移り、閉じるボタンで元のプレイヤーへ戻ることを配線する | `bajutsu/templates/report.js` | 単一ターゲットのシナリオで、拡大ボタンを押すと動画がモーダル内で大きく表示され、再生位置と再生/一時停止の状態が移動の前後で変わらないこと、拡大表示のままシークバーで操作できること、閉じると元のプレイヤーへ動画と操作バーが戻り、そこでも同じ位置から再生を続けられることを目視で確認する | 2 |
| 4 | `vzBuildSteps`でステップ一覧を複製し、行クリックとstepjumpクリックによるシーク、`timeupdate`によるハイライトと自動スクロールを実装する | `bajutsu/templates/report.js` | モーダルを開いた状態で、ステップ行をクリックすると動画がその時刻へシークすること、動画を再生すると現在のステップ行にハイライトが付き、一覧が自動でスクロールしてそのステップを表示範囲に収めることを目視で確認する | 3 |
| 5 | マルチターゲットシナリオでのタブ切り替え（`vzOpen`のプレイヤー列挙とタブ構築）を実装する | `bajutsu/templates/report.html.j2`、`bajutsu/templates/report.js`、`bajutsu/templates/report.css` | `demos/showcase/scenarios/cross_platform_app_web.yaml`のようなマルチターゲットシナリオを実行して生成した`report.html`で、タブを切り替えると表示される動画とステップ一覧がそのターゲットのものに入れ替わり、切り替え前のターゲットのプレイヤーがコンパクト表示へ戻ることを目視で確認する | 4 |
| 6 | 背景クリック、閉じるボタン、Escapeキーでモーダルを閉じられることを配線し、`.tv` / `.imgz`が開いているときはEscapeがそちらを優先することを確認する | `bajutsu/templates/report.js` | 3つの閉じ方それぞれでモーダルが閉じ、動画とステップ一覧が正しく後始末されることを目視で確認する | 3, 4 |
| 7 | ダークモードに新しい配色が要らないことを確認する。`.vz-box`や`.vz-steps`は`var(--card)` / `var(--ink)` / `var(--line)`を使い、`report.css`の`@media (prefers-color-scheme: dark)`ブロック（[report.css:558](../../bajutsu/templates/report.css)以下）がすでに切り替える。`.vexpand` / `.vz-tab` / `.vz-close`は、常に暗い動画領域の上に乗る操作系であり、`.vplay`と同じ固定のダーク配色を使うため、こちらも追加のCSSはいらない | — | OSをダークモードにした状態でモーダルを開き、既存の`.tv`と同程度に配色が馴染んでいることを目視で確認する | 2 |
| 8 | `docs/reporting.md`と`docs/ja/reporting.md`のreport.html章に、動画拡大モーダルの説明を追記する | `docs/reporting.md`、`docs/ja/reporting.md` | 追記した文章がtextlintの指摘ゼロで通ることを確認する | 6 |
| 9 | `make check`が通ることを確認する | — | `make check`がgreenで終わる | 1–8 |

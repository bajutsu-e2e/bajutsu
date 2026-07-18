[English](BE-XXXX-xcuitest-stale-handle-reresolve.md) · **日本語**

# BE-XXXX — 失敗させる前に stale なアクチュエーションハンドルを解決し直す：XCUITest チャネルの決定論を保つ stale リトライ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-xcuitest-stale-handle-reresolve-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md), [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md), [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) |
<!-- /BE-METADATA -->

## はじめに

オンデバイスのエンドツーエンド（E2E）ジョブ `xcuitest (multi-touch)` は、シナリオの最初の tap で
不安定に失敗します。対象の要素が画面に存在しているのに、`element vanished (stale handle)` で落ちます。
この失敗は、シナリオやテスト対象アプリの欠陥ではありません。XCUITest バックエンドは、要素の解決と
アクチュエーションを、オンデバイス runner への2回の別々の往復に分けます。2回の往復のあいだにアプリの
アクセシビリティツリーが取り直される（新しく前面に出た画面が落ち着くまでのあいだ、ふつうに起こります）と、
1回目の往復が発行したスナップショット単位のハンドルは、2回目の往復が使うころには古くなっています。
本項目は、決定論的な判定を弱めずに、この「解決してからアクチュエーションする」あいだのギャップを
塞ぐことを提案します。`stale` の応答を受けたら runner に問い合わせ直し、同じセレクタがなお1つの要素に
解決できるときに限って再アクチュエーションし、解決できなくなった瞬間には今までと同じように大きく失敗
させます。変更は xcuitest バックエンドの Python チャネルの内側に収まり、触れる面は runner、リトライの
継ぎ目、決定論的な判定の3つだけです。

## 動機

この不安定さは、必須の `E2E (iOS)` ゲートで再現します。実行 29630685037 では、`xcuitest (multi-touch)`
ジョブの2本目のシナリオ（[`permission.yaml`](../../demos/showcase/scenarios/permission.yaml)）が、
最初のステップで失敗しました。

```
step 0 (tap): element vanished (stale handle): {'label': 'Permissions', 'traits': ['button']}
FAIL  runs/20260718-044659/manifest.json
```

このステップが tap する「Permissions」タブバーボタンは、アプリの起動画面の固定された要素なので、ボタンは
消えません。古くなったのはハンドルであって、要素ではありません。原因は、2段階のアドレス指定にあります。

XCUITest バックエンドは、1回の tap を、runner の要素スナップショットに対する2回の往復に分けます。
`_resolve_handle` はまず `GET /elements` を送り、Python 側でセレクタを1つの要素に解決し、runner が
その要素に対して発行した不透明なスナップショット単位のハンドルを読み取ります。続いて `tap` が
`POST /tap {handle}` を送ります。runner は各ハンドルを1つのスナップショット版に対応づけているので、
問い合わせと tap のあいだにアクセシビリティツリーが取り直されると、先のハンドルはもう生きた要素に
対応しません。runner は `status: "stale"` を返し、ドライバは要素消失のエラーを送出します。新しく前面に
出た画面は、落ち着くまでのあいだ何度もスナップショットを取り直すので、シナリオの最初の tap が、この
レースの当たる場所になります。

失敗したステップの直前のログは、原因が runner の欠陥ではなく起動直後の安定化にあることを示します。
失敗したステップの前のおよそ60秒間、ドライバは `await_ready` が runner のループバックサーバを
ポーリングするあいだ `runner channel GET /health failed ... [Errno 61] Connection refused` を記録し
（固定 `sleep` ではなく、有界の条件待ちです）、tap は runner が応答してから初めて発火しました。
つまり runner は、`stale` を報告した時点で生きていて応答していました。runner は自身がアクチュエーション
する前に `stale` を返すので、tap はどの要素にも届いておらず、画面上で実際に消えたものはありませんでした。

この不安定さは3つの意味で高くつきます。必須の集約チェック `E2E (iOS)` がランダムに失敗すると、ゲートへの
信頼が損なわれます。メンテナは本物の回帰と区別できないため、反射的な再実行か、無駄な調査のどちらかを
招きます。再実行のたびに、最初の tap に到達する前にアプリをビルドし、Simulator を起動し、runner を
立ち上げる macOS ランナーの従量課金枠を消費します。さらに悪いことに、`element vanished` という
メッセージは調査者を誤った場所へ向かわせます。「Permissions」要素が存在しないかのように読めますが、
実際に起きたのは、ずっと存在しているボタンの上でのスナップショット版のレースです。存在しない消失を
調査者に追わせてしまいます。

既存のどの仕組みも、この stale レースを扱いませんし、もともと扱う設計でもありません。一時的リトライの
継ぎ目（[BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md)）は
1回のチャネル呼び出しの中のトランスポートのブレを吸収しますが、`stale` の応答はトランスポートの失敗では
なくデコード済みの結果なので、継ぎ目は意図的にこの結果を再試行しません。結果を再試行するのは、
[BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) が退ける
「吸収による不安定さ」だからです。準備完了とタイムアウトの作業
（[BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)）は、
runner が誤った画面で準備完了と判定されるのを止め、遅い書き込みにより長い有界の窓を与えましたが、
どちらの変更も「解決してからアクチュエーションする」あいだのギャップには触れていません。姉妹の不安定さ
（[BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md)）は
別の故障クラスです。BE-0287 では runner がクラッシュして応答をやめますが、本項目では runner は生きていて
正しく `stale` を返します。stale レースを塞ぐには、専用の仕組みが必要です。

## 詳細設計

作業は4つの単位に分かれます。Unit 1 はドライバの変更、Unit 2 は決定論のディレクティブの下で変更を
誠実に保つ作業、Unit 3 は実機なしで変更を証明する作業、Unit 4 はドキュメントを一致させる作業です。
各単位は依存順に並びますが、別々のプルリクエストとして着地できます。

**Unit 1 — ドライバチャネルに、stale をゲートにした再解決リトライを入れる。**
[`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) のハンドルベースの
アクチュエーション経路（`_resolve_handle` の継ぎ目が供給する `_actuate`）で、`stale` の応答を、
即座の要素消失エラーではなく、問い合わせ直しのきっかけとして扱います。`_resolve_handle(sel)` を
実行し直し、セレクタがなお1つの要素に解決できるときはハンドルを取り直してアクチュエーションを
再発行します（小さな有界回数まで）。セレクタが0個または複数に解決して `ElementNotFound` や
`AmbiguousSelector` を送出するときは、それ以上試行せず、その結果で即座に失敗します。再解決こそが、
要素がなお存在するスナップショット版のレースと、要素が存在しない本物の消失とを分ける条件なので、
ゲートはたまたま一致した何かを tap しません。ハンドルベースのアクチュエーション
（`tap`、`double_tap`、`long_press`、`pinch`、`rotate`）はすべてこの継ぎ目を通り、リトライを継承します。
座標指定の `tap_point` は古くなるハンドルを持たないので、変更しません。試行回数の上限は BE-0207 の
バックオフを再利用し、ループ全体を1秒未満に保つので、本当になくなった要素を長く再試行しません。

**Unit 2 — 決定論を保ち、二重にアクチュエーションしない。**
`stale` の応答の後で tap を再発行するのは、タイムアウトの後で tap を再発行するのとは違って安全です
（BE-0218 が拠り所にした区別です）。runner は自身が要素に触れる前に `stale` を返します
（[`BajutsuKit/Sources/BajutsuRunner/Router.swift`](../../BajutsuKit/Sources/BajutsuRunner/Router.swift)
で `store.lookup(handle:)` が `.stale` を報告し、ハンドラはアクチュエーションせずに戻ります）。
したがって `stale` の応答は、アクチュエーションが起きなかったことの確定的な証拠であり、再送しても
ジェスチャを二重に適用することはありません。対してタイムアウトは、届いたかどうかが不明のままなので、
BE-0218 は期限を過ぎた書き込みの再試行を拒みました。決定論はそのほかの点でも損なわれません。大規模
言語モデル（LLM）は経路に入らず、固定 `sleep` も足しません（問い合わせ直しの往復が条件であり、試行
間のバックオフは安定化のための待機ではなく有界の BE-0207 バックオフです）。本物の `stale` の消失、
曖昧な再解決、使い切った試行の予算は、いずれも今までと同じ大きな失敗のままです。

**Unit 3 — 実機なしで変更を証明する。** 模擬トランスポートに対する単体テストで継ぎ目を覆います
（Simulator のいらない高速ゲートで動きます）。`stale` の応答が1回あってから `ok` になり、セレクタが
なお一意に解決するときは回復します。`stale` が続いてもセレクタがなお解決するときは上限を使い切って
大きく失敗します。`stale` の後の問い合わせ直しでセレクタがもう解決しないときは、追加の試行を使わず
`ElementNotFound` で即座に失敗します。`stale` の後の問い合わせ直しでセレクタが曖昧に解決するときは、
`AmbiguousSelector` で失敗し、決してアクチュエーションしません。4つのケースは、スナップショットの
レースが回復することと、本物の消失や曖昧さがなお失敗することの両方を固定するので、後の変更が誠実な
ゲートを、こっそり吸収するゲートに変えられなくなります。

**Unit 4 — ドキュメントを一致させる（BE-0113）。**
[`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) のモジュール docstring は、いまは
stale なハンドルが「idb が送出するのと同じ要素消失エラーとして表面化する」と述べています。この
docstring を、再解決リトライと、stale なハンドルがなお失敗する正確な条件を述べるものに更新します。
[`DESIGN.md`](../../DESIGN.md) と [`docs/architecture.md`](../../docs/architecture.md) が stale の
セマンティクスを述べているなら、散文の記述が挙動に合うように更新します。

## 検討した代替案

**再解決のゲートなしに、stale なアクチュエーションを無条件で再試行する。** ゲートを外すと、本物の消失
（前のステップの遷移が取り除いた要素）を、静かな再試行に吸収してしまいます。BE-0049 が禁じる「吸収に
よる不安定さ」です。再解決のゲートこそが、リトライを誠実に保ちます。要素が明らかになお存在するときに
限って、再アクチュエーションするからです。

**起動後、最初の tap の前に固定の安定化待機を入れる。** 条件を無視した固定 `sleep` は、プライム
ディレクティブ 2 が明確に禁じます。しかもこの待機は、レースの窓を狭めるだけで塞ぎません。固定の予算
より遅い安定化は、それでもハンドルを古くします。再解決リトライは、画面が落ち着くまでどれだけかかっても、
レースを塞ぎます。

**解決を runner に移し、2回の往復を1回に畳む。** runner がアクチュエーション時にセレクタを解決すれば
スナップショットのギャップは消えますが、Python 側での解決は、バックエンドの核となる不変条件です。
ドライバは、自身が解決したまさにその要素に作用し、別の要素に一致しうる再解決された述語には決して
作用しません。解決を runner に移すと、決定論的な核が防ぐために存在する曖昧さと、stale レースを
引き換えにしてしまいます。

**CI でチェックを隔離するか、ジョブ単位で再試行する。** `xcuitest (multi-touch)` を失敗許容にする、
あるいはジョブを再試行で包むと、レースに触れないままチェックを緑にできます。BE-0287 が姉妹の不安定さ
に対して退けるのと同じ理由です。本物のレースを緑のチェックの裏に隠し、静かな再試行のたびに macOS の
分を消費し続けるからです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] Unit 1 — `_actuate` と `_resolve_handle` の継ぎ目に、stale をゲートにした再解決リトライを入れる。
      ハンドルベースのアクチュエーション（`tap`、`double_tap`、`long_press`、`pinch`、`rotate`）すべてを
      対象とし、座標指定の `tap_point` は変更しない。
- [ ] Unit 2 — 決定論を保つ。`stale` の書き込みを再発行しても二重にアクチュエーションしない（runner は
      アクチュエーションする前に `stale` を返す）。LLM を入れず、固定 `sleep` を足さず、本物の `stale`・
      曖昧さ・使い切りは大きな失敗のままにする。
- [ ] Unit 3 — 4つのケース（1回で回復、使い切り、即座の `ElementNotFound`、即座の `AmbiguousSelector`）の
      実機なし単体テスト。
- [ ] Unit 4 — ドキュメントを一致させる（BE-0113）。`xcuitest.py` の docstring と、stale の
      セマンティクスを述べているなら `DESIGN.md` と `docs/architecture.md`。

## 参考

- [BE-0207 — XCUITest ランナーチャネルを一過性のタイムアウトに強くする](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md)：
  本項目がバックオフを再利用する一時的なブレのリトライの継ぎ目と、デコード済みの `stale` を
  トランスポートのリトライから外す「結果とトランスポート」の分割
- [BE-0218 — E2E Simulator ゲートを安定させる](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)：
  同じゲートに対する準備完了とメソッド別タイムアウトの作業と、本項目の二重アクチュエーション回避の
  論拠が拠り所にする「届いた書き込み」の区別
- [BE-0287 — 多点タッチ操作下での XCUITest runner チャネルの耐障害性](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md)：
  同じジョブ上の姉妹の不安定さ（実行途中の runner クラッシュ）であり、本項目の stale レースの双子が
  重複ではなく補完する相手
- [BE-0049 — 決定論と不安定さの監査](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)：
  再解決のゲートが整合を保つ決定論のスタンス（スナップショットのレースは許容し、本物の結果は決して
  吸収しない）
- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)：
  「Python 側で解決し、ハンドルでアクチュエーションする」アドレス指定が、2回往復のギャップを生む
  バックエンド
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py)：本項目が強化する解決と
  アクチュエーションの継ぎ目（`_resolve_handle`、`_actuate`、`_with_retry`、`await_ready`）
- [`BajutsuKit/Sources/BajutsuRunner/Router.swift`](../../BajutsuKit/Sources/BajutsuRunner/Router.swift)：
  アクチュエーションする前に `stale` を返す `handleTap`。二重アクチュエーション回避の論拠の拠り所
- [`demos/showcase/scenarios/permission.yaml`](../../demos/showcase/scenarios/permission.yaml)：
  最初の tap がこの不安定さを露呈したシナリオ
- [`.github/workflows/ios-e2e.yml`](../../.github/workflows/ios-e2e.yml)：本項目が安定させる `E2E (iOS)`
  ゲート

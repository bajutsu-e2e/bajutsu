[English](BE-0289-xcuitest-stale-handle-reresolve.md) · **日本語**

# BE-0289 — 操作対象への参照が古くなっても、失敗として扱う前に XCUITest のしくみで指定し直す

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0289](BE-0289-xcuitest-stale-handle-reresolve-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0289") |
| 実装 PR | [#1266](https://github.com/bajutsu-e2e/bajutsu/pull/1266) |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md), [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md), [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md) |
<!-- /BE-METADATA -->

## はじめに

オンデバイスのエンドツーエンド（E2E）ジョブ `xcuitest (multi-touch)` は、シナリオの最初の tap で不安定に失敗します。対象の要素は画面に存在しているのに、tap は `element vanished (stale handle)` で落ちます。シナリオにも、テスト対象アプリにも、欠陥はありません。

XCUITest バックエンドは、要素を探すことと操作することを、オンデバイスの runner との 2 回のやり取りに分けています。2 回のやり取りのあいだに、アクセシビリティツリーが取り直される場合があります。新しく前面に出た画面が落ち着くまでのあいだは、ふつうに起こる現象です。取り直しが起こると、1 回目のやり取りが発行した、スナップショットごとのハンドルは、2 回目のやり取りが使うころには古くなっています。

本項目は、決定論的な判定を弱めずに、この「探してから操作する」あいだのすき間を塞ぐことを提案します。

- `stale` という返事を受けたら、runner に問い合わせ直します。
- 同じセレクターから、なお 1 つの要素だけが見つかる場合に限って、操作をやり直します。
- セレクターが 1 つの要素に解決しなくなった瞬間には、今までと同じように大きく失敗させます。

変更は、xcuitest バックエンドの Python 側のチャネルだけで完結します。触れる面は、runner、リトライの継ぎ目、決定論的な判定の 3 つに限られます。

## 動機

この不安定さは、必須の `E2E (iOS)` ゲートで再現します。実行 29630685037 では、`xcuitest (multi-touch)` ジョブの 2 本目のシナリオ（[`permission.yaml`](../../demos/showcase/scenarios/permission.yaml)）が、最初のステップで失敗しました。

```
step 0 (tap): element vanished (stale handle): {'label': 'Permissions', 'traits': ['button']}
FAIL  runs/20260718-044659/manifest.json
```

このステップが tap する「Permissions」タブバーボタンは、アプリの起動画面の固定された要素です。ボタンは消えていません。古くなったのは要素ではなく、ハンドルです。原因は、要素の指定が 2 段階に分かれていることにあります。

XCUITest バックエンドは、1 回の tap を、runner が持つ要素スナップショットに対する 2 回のやり取りに分けます。

1. `_resolve_handle` が `GET /elements` を送ります。Python 側でセレクターを 1 つの要素に絞り込み、runner がその要素に発行した、スナップショットごとの不透明なハンドルを受け取ります。
2. 続いて `tap` が `POST /tap {handle}` を送ります。

runner は、ハンドルのひとつひとつを、1 つのスナップショットのバージョンに結びつけています。問い合わせと tap のあいだにアクセシビリティツリーが取り直されたとします。先のハンドルは、生きた要素に対応しなくなります。runner は `status: "stale"` を返し、ドライバは要素消失のエラーを送出します。新しく前面に出た画面は、落ち着くまでのあいだ、何度もスナップショットを取り直します。そのため、このタイミングのずれは、シナリオの最初の tap で起こります。

失敗したステップの前のログは、原因が runner の欠陥ではなく、起動直後の画面の安定化にあることを示しています。失敗したステップの前のおよそ 60 秒間、ドライバは `runner channel GET /health failed ... [Errno 61] Connection refused` を記録し続けました。この間、`await_ready` が runner のループバックサーバをポーリングしていました。このポーリングは、固定の待機ではなく、有界の条件待ちです。tap は、runner が応答してから初めて発火しました。

したがって、runner は `stale` を報告した時点で、生きて応答していました。runner は操作する前に `stale` を返すので、tap はどの要素にも届いていません。画面上で消えたものは、何もありませんでした。

この不安定さは、3 つの意味で負担になります。

- 必須の集約チェック `E2E (iOS)` がランダムに失敗すると、ゲートへの信頼が損なわれます。メンテナは、この不安定さを本物の回帰と見分けられません。そのため、反射的な再実行か、無駄な調査のどちらかを招きます。
- 再実行のたびに、macOS ランナーの従量課金枠を消費します。ジョブは、最初の tap に到達するまでに、アプリをビルドし、Simulator を起動し、runner を立ち上げるからです。
- `element vanished` というメッセージは、調査者を誤らせます。「Permissions」要素が存在しないかのように読めます。実際に起きたのは、ずっと存在しているボタンの上での、スナップショットのバージョンの競合です。調査者は、起きていない消失を追わされます。

既存のどの仕組みも、この stale の競合を扱っていません。もともと扱う設計でもありません。

- 一時的なリトライの継ぎ目（[BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md)）は、1 回のチャネル呼び出しの中で起こる通信のブレを吸収します。`stale` という返事は、通信の失敗ではなく、デコード済みの結果です。継ぎ目は、この返事をあえてリトライしません。結果をリトライすることは、[BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md) が退ける「吸収による不安定さ」だからです。
- 準備完了と時間切れの作業（[BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)）は、runner が誤った画面で準備完了と判定されるのを止めました。遅い書き込みには、長い有界の猶予も与えました。どちらの変更も、「探してから操作する」あいだのすき間には触れていません。
- 姉妹の不安定さ（[BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md)）は、別の種類の故障です。BE-0287 では runner がクラッシュして応答しなくなります。本項目では、runner は生きていて、`stale` を正しく返します。

この stale の競合を塞ぐには、専用の仕組みが必要です。

## 詳細設計

作業は 4 つの単位に分かれます。

- Unit 1 は、ドライバの変更です。
- Unit 2 は、決定論の指針の下で、変更を誠実に保ちます。
- Unit 3 は、実機なしで変更を確かめます。
- Unit 4 は、文書を実際の動きに合わせます。

各単位は依存順に並びますが、別々のプルリクエストとして着地できます。

**Unit 1 — `stale` を契機にして再解決する、ドライバチャネルのリトライ。** ハンドルを使う操作の経路（`_resolve_handle` の継ぎ目が供給する `_actuate`）は、[`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) にあります。この経路で、`stale` という返事を、すぐに要素消失のエラーとせず、問い合わせ直す契機として扱います。`_resolve_handle(sel)` をもう一度実行します。

- セレクターが、なお 1 つの要素に解決する場合は、ハンドルを取り直して操作をやり直します。回数は、小さな決まった上限までです。
- セレクターが 0 個または複数の要素に解決する場合は、`ElementNotFound` か `AmbiguousSelector` が送出されます。ドライバは、その結果ですぐに失敗し、それ以上は試しません。

再解決は、2 つの場合を分ける条件です。スナップショットのバージョンの競合では、要素がなお存在します。本当の消失では、要素が存在しません。この条件があるので、ゲートは、たまたま一致した何かを tap することがありません。

ハンドルを使う操作（`tap`、`double_tap`、`long_press`、`pinch`、`rotate`）は、すべてこの継ぎ目を通り、リトライを引き継ぎます。座標を直接指定する `tap_point` は、古くなるハンドルを持たないので、変更しません。

試す回数の上限には、stale のリトライ専用の定数を使います。BE-0207 の通信リトライの `_MAX_ATTEMPTS` と `_BACKOFF_BASE_SECONDS` とは、分けて持ちます。初期値は同じです。3 回まで試し、バックオフは 0.5 秒、次に 1.0 秒の指数です。2 つのループは、上限を決める対象が違います。一方は、`_resolve_handle` と `_actuate` のあいだで画面が落ち着くのを待ちます。もう一方は、`_with_retry` の中で通信のブレに対処します。片方をあとから調整しても、たとえば遅い CI ランナー向けに通信の予算を緩めても、もう片方が黙って変わってはいけません。ループ全体は、1 秒未満ではなく、数秒程度で収まります。本当に消えた要素を、長く試し続けることはありません。

再解決は、リトライの経路に限って、バックエンドの「解決したまさにその要素に操作する」という保証を緩めます。本提案は、この引き換えを隠さずに述べます。再解決して 1 つの要素に絞れても、証明できるのは、セレクターに合う要素が*存在する*ことだけです。その要素が、1 回目に見つけた要素と*同じ*であることまでは、証明しません。2 回のやり取りのあいだに画面が変わる場合があります。たとえば、モーダルが閉じる場合です。すると、別の要素だけが、セレクターの唯一の一致になりえます。リトライは、その別の要素に操作してしまいます。

この引き換えは受け入れられます。見かけより狭い引き換えでもあります。

- 解決は、Python 側にとどまります。保証の目的は、解決の権限を runner の外に保つことです。runner 側では、あいまいな条件が、複数の一致のうち 1 つを黙って選ぶおそれがあります。この目的は損なわれません。
- リトライが操作するのは、Python 側の*唯一の*一致だけです。複数の一致のうちの 1 つには、操作しません。
- 操作する瞬間に再解決することは、idb が行っていることと同じです。idb は、解決と tap を 1 回の手順で行います。リトライは、XCUITest の stale への対応を、ほかのバックエンドに揃えます。より緩い規則を作り出すものではありません。

より厳しい変種も、`implement-be` の選択肢として残します。セレクターだけの一致が緩すぎるとわかった場合に備えるものです。この変種は、再解決した要素が、もとの要素の識別属性にも合うときに限って、操作をやり直します。

**Unit 2 — 決定論を保ち、操作を二重にしない。** `stale` という返事のあとで tap をやり直すことは、*時間切れ*のあとでやり直すこととは違い、安全です。BE-0218 も、同じ区別を拠り所にしました。runner は、要素に触れる前に `stale` を返します。[`BajutsuKit/Sources/BajutsuRunner/Router.swift`](../../BajutsuKit/Sources/BajutsuRunner/Router.swift) では、`store.lookup(handle:)` が `.stale` を報告し、ハンドラは操作せずに戻ります。したがって、`stale` という返事は、操作が起きなかったことの確かな証拠です。もう一度送っても、ジェスチャを二重に適用することはありません。対して時間切れは、届いたかどうかが不明のままです。BE-0218 が、期限を過ぎた書き込みのリトライを拒んだのは、そのためです。

決定論は、そのほかの点でも損なわれません。

- 大規模言語モデル（LLM）は、経路に入りません。
- 固定の `sleep` は足しません。問い合わせ直しのやり取りが条件です。試行のあいだのバックオフは、Unit 1 で述べた stale リトライ専用のバックオフで、安定化のための待機ではありません。
- 本当の `stale` による消失、あいまいな再解決、使い切った試行の予算は、いずれも今までと同じく大きな失敗のままです。

**Unit 3 — 実機なしで変更を確かめる。** 継ぎ目は、スクリプト化したトランスポートに対するユニットテストで覆います。Simulator のいらない高速なゲートで動くテストです。次の 4 つの場合を扱います。

- `stale` の返事が 1 回あってから `ok` になり、セレクターがなお 1 つに解決する場合は、回復します。
- `stale` が続き、セレクターがなお解決する場合は、上限を使い切って大きく失敗します。
- `stale` のあとの問い合わせ直しで、セレクターが解決しなくなった場合は、追加の試行をせず、`ElementNotFound` ですぐに失敗します。
- `stale` のあとの問い合わせ直しで、セレクターがあいまいに解決する場合は、`AmbiguousSelector` で失敗し、操作は決してしません。

この 4 つの場合は、ゲートの両面を固定します。スナップショットの競合は回復します。本当の消失とあいまいさは、なお失敗します。こうしておけば、あとの変更が、誠実なゲートをこっそり吸収するゲートに変えることはできません。

**Unit 4 — 文書を実際の動きに合わせる（BE-0113）。** [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) のモジュールの説明は、いまの動きを次のように述べています。ハンドルが古くなると、「idb が送出するのと同じ要素消失エラーとして表面化する」というものです。この説明を、再解決のリトライと、古いハンドルがなお失敗する正確な条件を述べるものに更新します。[`DESIGN.md`](../../DESIGN.md) と [`docs/architecture.md`](../../docs/architecture.md) が stale の扱いを述べているなら、散文の説明が実際の動きに合うように更新します。

## 検討した代替案

**再解決のゲートなしに、`stale` になった操作を無条件でリトライする。** ゲートを外すと、本当の消失を、静かなリトライに吸収してしまいます。たとえば、前のステップの遷移が取り除いた要素です。これは、BE-0049 が禁じる「吸収による不安定さ」です。再解決のゲートが、リトライを誠実に保ちます。要素がなお存在することが確かめられた場合に限って、操作をやり直すからです。

**起動後、最初の tap の前に、固定の待機を入れる。** 条件を無視した固定の `sleep` は、プライムディレクティブ 2 が明確に禁じています。しかも、この待機は、競合の起こる幅を狭めるだけで、なくしません。画面の安定化が、固定の予算より遅ければ、ハンドルはそれでも古くなります。再解決のリトライは、画面が落ち着くまでにどれだけかかっても、競合を塞ぎます。

**解決を runner に移し、2 回のやり取りを 1 回にまとめる。** runner が操作の時点でセレクターを解決すれば、スナップショットのすき間は消えます。しかし、Python 側での解決は、バックエンドの中核の不変条件です。解決の権限は Python 側にとどまり、複数の要素に合いうる runner 側の条件には移りません。stale のリトライは、Python 側で、しかも唯一の一致の場合に限って、再解決します（この引き換えは、*詳細設計*に詳しく述べました）。そのため、権限は Python 側に保たれます。解決を runner に移すと、権限を手放すことになります。stale の競合と引き換えに、決定論的なコアが防ごうとするあいまいさを、持ち込むことになります。

**CI でチェックを隔離するか、ジョブ単位でリトライする。** `xcuitest (multi-touch)` を失敗許容にする、あるいはジョブをリトライで包むと、競合に触れないままチェックを緑にできます。BE-0287 は、姉妹の不安定さについて、同じ理由でこの選択肢を退けています。本物の競合を緑のチェックの裏に隠し、静かなリトライのたびに macOS の従量課金枠を消費し続けるからです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の作業分解
> （もれなく重なりなく、作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を
> プルリクエストへのリンクとともに記録します。

- [x] Unit 1 — `_actuate` と `_resolve_handle` の部分に、`stale` を探し直すきっかけにするやり
      直しを入れる。参照を使う操作（`tap`、`double_tap`、`long_press`、`pinch`、`rotate`）すべて
      を対象とし、座標指定の `tap_point` は変更しない。
- [x] Unit 2 — 判定のしかたを保つ。`stale` の書き込みをやり直しても操作を二重にしない（runner は
      操作する前に `stale` を返す）。LLM を入れず、決まった待ち時間を足さず、本当の `stale`・
      あいまいさ・使い切りは大きな失敗のままにする。
- [x] Unit 3 — 4つの場合（1回で回復、使い切り、すぐの `ElementNotFound`、すぐの `AmbiguousSelector`）の
      実機なしユニットテスト。
- [x] Unit 4 — 文書を実際の動きに合わせる（BE-0113）。`xcuitest.py` の説明と、`stale` の扱いを
      述べているなら `DESIGN.md` と `docs/architecture.md`。

## 参考

- [BE-0207 — XCUITest ランナーチャネルを一過性のタイムアウトに強くする](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry-ja.md)：
  本項目が専用の定数でならう、一時的な通信のブレへのリトライの継ぎ目と、デコード済みの `stale`
  を通信のリトライから外す「結果と通信」の分け方
- [BE-0218 — E2E Simulator ゲートを安定させる](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)：
  同じゲートに対する準備完了と方法ごとの時間切れの作業と、本項目の操作を二重にしない論拠が拠り所
  にする「届いた書き込み」の区別
- [BE-0287 — 多点タッチ操作下での XCUITest runner チャネルの耐障害性](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience-ja.md)：
  同じジョブ上の姉妹の不安定さ（実行途中の runner のクラッシュ）。本項目の stale の競合は、これと
  重複せず、補い合う
- [BE-0049 — 決定論と不安定さの監査](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)：
  再解決のゲートが整合を保つ決定論のスタンス（スナップショットの競合は許すが、本当の結果は決して
  吸収しない）
- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)：
  「Python 側で解決し、ハンドルで操作する」指定のしかたが、2 回のやり取りのすき間を生むバックエンド
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py)：本項目が強化する、解決と操作の
  継ぎ目（`_resolve_handle`、`_actuate`、`_with_retry`、`await_ready`）
- [`BajutsuKit/Sources/BajutsuRunner/Router.swift`](../../BajutsuKit/Sources/BajutsuRunner/Router.swift)：
  操作する前に `stale` を返す `handleTap` と `handleGesture`。`pinch` と `rotate` を含む、操作を
  二重にしない論拠の拠り所
- [`demos/showcase/scenarios/permission.yaml`](../../demos/showcase/scenarios/permission.yaml)：
  最初の tap がこの不安定さを露呈したシナリオ
- [`.github/workflows/ios-e2e.yml`](../../.github/workflows/ios-e2e.yml)：本項目が安定させる
  `E2E (iOS)` のゲート

[English](BE-XXXX-serve-ui-device-farm-fan-out.md) · **日本語**

# BE-XXXX — serve の Web UI から Device Farm の fan-out を投入する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-serve-ui-device-farm-fan-out-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | Device-cloud execution |
<!-- /BE-METADATA -->

## はじめに

本項目は、serve の Web UI に Device Farm の fan-out を投入する入口を設けます。現在、ターゲットを
AWS Device Farm へシナリオごとに1 run ずつ展開するには、`POST /api/run-set` を手で呼ぶしかありません
（[BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out-ja.md)）。
本項目では、Replay タブに実行環境の選択を加えます。デバイスクラウドの環境を選ぶと、オペレーターは
シナリオを複数選び、Run を1回押すだけで fan-out を投入できます。投入後は run-set パネルが、
シナリオごとのジョブを1つの一覧で追跡します。選択肢にデバイスクラウドの環境が出るのは、生きた worker が
その環境を名乗っている間に限ります。そのため、一度も処理されたことのない環境へ投入することはなくなります。
ただし、一覧を取得した後に worker が止まると、ジョブはキューに残ります。run-set パネルの no-worker 通知が
その場合を示します。

## 動機

BE-0336 は、serve の Web UI から Device Farm の run を直接投げられるようにすることを目標にしました。
実際に出荷されたのはサーバー側の半分です。具体的には、`POST /api/run-set` のルート、シナリオごとの
fan-out、各ジョブを提出するデバイスクラウド用 worker
（[BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch-ja.md)）です。
Web UI のどの画面も、このルートを呼びません。Replay タブの Run ボタンは1つのシナリオを
`POST /api/run` へ送るだけで、リクエスト本体に `environment` はありません。fan-out を使いたい
オペレーターは、ブラウザを離れて JSON を手で書く必要があります。

```json
{ "target": "showcase-compose", "environment": "devicefarm", "scenarios": ["firstlook.yaml", "controls.yaml"] }
```

手書きのリクエストでは、サーバーがすでに持っている次の3つの事実がオペレーターから見えません。

- **どの環境を worker が処理できるか。** worker は Device Farm の provider を登録すると、
  ルーティング用の token `environment:devicefarm` を名乗ります。サーバーは生きた worker ごとに
  この token を記録していますが、返す読み取りルートがありません。Device Farm の worker が
  動いていないことにオペレーターが気付くのは、投入したジョブが「処理できる worker がいない」と
  報告したときです。
- **ターゲットがどのシナリオ名を受け付けるか。** リクエストは、ターゲットのシナリオディレクトリからの
  相対名でシナリオファイルを指定します。名前が1つでも不明なら、リクエスト全体が失敗します。
  この挙動は fan-out を安全に保ちますが、オペレーターはファイル名を事前に知っている必要があります。
- **fan-out のうちどれだけが投入されたか。** サーバーには全体、ユーザー別、組織別の同時実行上限が
  あり、大きな fan-out は途中で打ち切られることがあります。その場合、応答はステータス 200 のまま、
  要求したシナリオ数より少ないジョブ ID を返します。残りを落としたことは、応答のどこにも書かれません。

本項目の出荷後、オペレーターはリクエスト本体を書かずに Replay タブから Device Farm の fan-out を投入し、
シナリオごとのジョブを1つの一覧で追跡できます。後から読む人は、次の2点で成果を確かめられます。
環境の選択肢に `devicefarm` が出るのは、生きた Device Farm の worker がいるときに限られます。
run-set パネルは、サーバーが投入しなかったシナリオの数を表示します。短くなった一覧を説明なしに
見せることはありません。

## 詳細設計

### 変更前と変更後

| 観点 | 現在 | 本項目の後 |
|---|---|---|
| fan-out の開始 | `curl` やスクリプトで `POST /api/run-set` を呼ぶ | Replay タブでデバイスクラウドの環境を選んで開始する |
| 環境の選択 | リクエスト本体に書く。誤記や worker の不在は投入後に判明する | `local` と、生きた worker が名乗るデバイスクラウドの環境を選択肢として表示する |
| シナリオの選択 | ファイル名を本体に手で書く | ターゲットのシナリオから複数選択する。全シナリオも選べる |
| 進捗の追跡 | ジョブ ID ごとに `GET /api/jobs/{id}` を呼ぶ | シナリオごとに1行の run-set パネルで追跡する |
| 上限による打ち切り | `jobIds` が短くなり、ステータスは 200 | 投入されなかったシナリオの数をパネルに表示する |

`POST /api/run-set` の契約、worker、Device Farm への提出は変えません。本項目が加えるのは読み取り
ルート1つで、ほかの変更はすべて Web UI の中に収まります。

### 利用できる環境の一覧

新しい読み取りルート `GET /api/environments` が、Web UI に表示してよい環境を返します。

```json
{ "environments": [ { "name": "devicefarm", "liveWorkers": 2 } ] }
```

サーバーは、capability によるルーティングのためにすでに持っている workers テーブルから一覧を導きます。
新しいリポジトリのメソッドが、lease のタイムアウト内に確認された worker の `environment:<name>`
token を読み取ります。「処理できる worker がいない」という通知も、同じ時間幅で生存を判定します
（`lacks_live_worker`）。このメソッドが読むのは capability の列だけで、ジョブの spec は読みません。
ルートは、`run-set` が受け付ける名前、すなわち `BATCH_PROVIDERS` に含まれる名前だけを残します。
`run-set` は grid への fan-out を拒否するので、`appium` の worker は一覧に現れません。キューを持たない
サーバーには workers テーブルがないため、ルートは空の一覧を返します。この答えは、キューのないサーバーで
`run-set` が投入を拒否する挙動と一致します。

一覧に載るのは provider の名前と worker の数です。Amazon Resource Name（ARN）や認証情報は載せません。
これらの値は各 worker 自身の環境変数にだけあり、サーバーには届かないからです。ルートには、ほかの
読み取りルートと同じ認証をかけます。応答には `local` を含めません。`local` は通常の `POST /api/run`
が処理するので、Web UI が常に選択肢として表示します。

### デバイスクラウドの環境を選んだときの Replay タブ

Run フォームの Target の隣に、**Environment** の選択欄を加えます。デフォルトは `local` で、`local` を
選んでいる間のフォームは現在と変わりません。Web UI は、タブを開いたときと、選択欄の横の再読み込み
ボタンをオペレーターが押したときに、`GET /api/environments` から選択欄を埋めます。

デバイスクラウドの環境を選ぶと、フォームは次の3点で変わります。

1. **シナリオ。** 1件を選ぶ選択欄が、ターゲットのシナリオを複数選べる選択欄に変わります。
   選択肢には「All scenarios」も含めます。「All scenarios」を選ぶとリクエストから `scenarios` を
   省き、現在と同じくサーバーがディレクトリを解決します。
2. **fan-out が使わない項目。** デバイスはデバイスクラウドの worker が確保するので、端末の選択欄と
   Workers 数を隠します。run 単位のオプション `erase`、`headed`、`systemAlertHandling` も隠します。
   `run-set` はこれらを読まないため、値を設定しても通知なしに捨てられるからです。
   Readiness パネルも隠します。このパネルが確認するのは serve のホストであり、デバイスクラウドでは
   ないからです。View scenario、判定バッジ、codegen は1件を選ぶ選択欄に結び付けたままにし、
   オペレーターが `local` に戻すとその選択欄を元に戻します。
3. **リクエスト。** Run は `{ target, environment, scenarios? }` を `POST /api/run-set` へ送ります。

web のターゲットでは、フォームがデバイスクラウドの環境を選べなくし、選択欄の横に理由を示します。web かどうかは、
`GET /api/targets` が返す backend ですでに判別できます。`app.path` の有無はフォームでは確認しません。
`app.path` のないターゲットはサーバーがジョブを1つも投入する前に拒否し、フォームはその拒否をほかの拒否と同じく
表示します。最終的な判断はサーバーが下します。それ以外の理由による
拒否は、サーバーが返すエラー応答（400、または最初のジョブが同時実行上限に当たったときの 429）の
内容をそのままフォームのステータス行に表示します。

### run-set パネル

投入に成功すると、単一 run のログの代わりに run-set パネルを開きます。パネルにはシナリオごとに1行が
並びます。各行は、シナリオのファイル名と、`GET /api/jobs/{id}` が返すジョブの状態を表示します。状態は、
投入から run の終了までは実行中、終了後は判定付きの完了です。run が完了した行は、そのレポートへ
リンクします。生きた worker がいないまま待機しているジョブの行には、同じルートがすでに返す
「処理できる worker がいない」という通知を表示します。そのため、worker を待ち続けている行と
実行中の行を見分けられます。

パネルは各行について `GET /api/jobs/{id}` を一定の間隔で読み、ジョブが完了した行の読み取りを
止めます。オペレーターが行を展開してログを読むときに限り、その行のジョブのイベントストリーム
（単一 run の画面が使うもの）を開きます。そのため、開いている接続の数はシナリオの数に左右されません。
**Stop all** ボタンは、
待機中または実行中のジョブを既存のキャンセル用ルートですべて取り消します。スケジュール済みの
Device Farm の run は、単一のキャンセルと同じく worker が停止します。

パネルは、応答に含まれるジョブ ID の数と、オペレーターが選んだシナリオの数を比べます。サーバーが
投入した数のほうが少なければ、投入されなかったシナリオの数と、同時実行上限で fan-out が打ち切られた
ことを表示します。オペレーターは、残りを後から投入し直せます。「All scenarios」の場合、Web UI は
Run を押した時点でシナリオの一覧に表示されていた数を基準にします。

パネルはブラウザの中だけに存在します。再読み込みするとグループ分けは失われますが、各 run は従来どおり
History の一覧に残ります。サーバー側に run-set の記録を持たせることは範囲外とし、理由は
「検討した代替案」で述べます。

### 範囲の境界

- **投入の仕組みは変えません。** `POST /api/run-set` とその拒否条件、worker、Device Farm の
  クライアントは現状のままです。
- **デバイスのフィルタは加えません。** fan-out は引き続き、プラットフォームだけで1 run あたり1台を
  確保します。機種や OS バージョンの指定は範囲外です。
- **新しい種類の環境は加えません。** 選択欄に並ぶのは `BATCH_PROVIDERS` にある環境です。現時点では
  `devicefarm` だけです。

### 基本原則との整合

- **AI は判定しません。** 本項目が加えるのは、選択欄、読み取りルート、進捗の表示です。判定は引き続き
  各 run の `manifest.json` から下し、投入の経路にもパネルにも大規模言語モデル（LLM）の呼び出しは
  入りません。
- **決定性を優先します。** 環境の一覧は、ルーティングがすでに使っている lease のタイムアウトに従います。
  そのため、どの worker を生きているとみなすかについて、選択欄とキューの判断が一致します。
  シナリオ名は、従来どおり投入の前にサーバーが解決します。
- **アプリに依存しません。** フォームは、ターゲットのプラットフォームを `GET /api/targets` から読みます。
  アプリごとのコードは Web UI に入りません。

### 作業分解（MECE）

1. **環境の一覧。** 生きた worker の `environment:` token を読むリポジトリのメソッドと、その結果を
   `BATCH_PROVIDERS` で絞る `GET /api/environments` ルートを加えます。生きた worker、期限切れの
   worker、`appium` の worker、キューのないサーバーの各場合をテストします。
2. **Replay のフォーム。** Environment の選択欄とその再読み込みボタン、「All scenarios」付きの複数選択、隠す項目と Readiness パネル、
   web のターゲットでの選択不可の状態、`POST /api/run-set` へのリクエストを
   加えます。
3. **run-set パネル。** シナリオごとの行、行ごとのポーリング、展開した行のイベントストリーム、レポートへのリンク、
   「処理できる worker がいない」という通知、**Stop all**、投入されなかった分の表示を加えます。
4. **テストとドキュメント。** `demos/serve-ui/` にある serve UI の dogfood シナリオを拡張し、
   偽の worker を相手にデバイスクラウド用のフォームを検証します。`docs/web-ui.md` と
   `docs/devicefarm.md` を、日本語版とあわせて更新します。

## 検討した代替案

- **クラウド実行専用のタブ。** 別のタブにすれば Replay のフォームは小さく保てます。一方で、
  ターゲットとシナリオの選択欄が重複し、run の履歴も2か所に分かれます。オペレーターはすでに Replay から
  run を開始しているので、Replay を拡張します。
- **固定の環境一覧。** Web UI が常に `local` と `devicefarm` を表示し、worker の不在は投入後の通知に
  任せる方法もあります。読み取りルートは1つ減りますが、どの worker も lease しない fan-out を丸ごと
  キューに積めてしまいます。生きた環境を一覧にすれば、worker の不在に投入前に気付けます。
- **行ごとにイベントストリームを開く。** 各行が単一 run の画面と同じイベントストリームを持つ方法も
  あります。serve は HTTP/1.1 で応答し、ブラウザは同じ接続先への同時接続をおよそ6本に制限します。
  シナリオが数本を超える fan-out では接続をすべて使い切り、**Stop all** の要求もストリームの後ろで
  待たされます。
- **進捗を History だけで見せる。** Web UI は投入したジョブの数だけを表示し、残りを History の一覧に
  任せる方法もあります。History に run が現れるのは、その run が完了した後です。worker がいないまま
  待機しているジョブや、上限で打ち切られた fan-out は見えないままになります。
- **サーバー側の run-set の記録。** fan-out ごとに記録を残せば、パネルは再読み込みしても残り、
  History も fan-out 単位で run をまとめられます。ただし、新しいテーブル、マイグレーション、
  読み取り経路が必要です。ブラウザ側のパネルでも、投入した直後の fan-out を見守るオペレーターには
  足ります。run-set の記録は、まとめた履歴の必要性がはっきりした時点で、別の項目として扱えます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] ユニット 1 — 環境の一覧（`GET /api/environments`）
- [ ] ユニット 2 — デバイスクラウドの環境での Replay のフォーム
- [ ] ユニット 3 — run-set パネル
- [ ] ユニット 4 — テストとドキュメント

## 参考

- [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out-ja.md)：serve からの fan-out と `POST /api/run-set`
- [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch-ja.md)：デバイスクラウド用の worker と、ルーティング用の `environment:` token
- [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure-ja.md)：実行環境を、ターゲットの属性ではなく run ごとの選択とする設計
- [BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs-ja.md)：キューと「処理できる worker がいない」という通知
- [`docs/ja/devicefarm.md`](../../docs/ja/devicefarm.md)：serve からの投入
- [`docs/ja/web-ui.md`](../../docs/ja/web-ui.md)：Replay タブ

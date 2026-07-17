[English](BE-XXXX-android-network-capture.md) · **日本語**

# BE-XXXX — Android バックエンドのネットワークキャプチャアサーション

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-android-network-capture-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0003](../BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci-ja.md)、[BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md)、[BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md) |
<!-- /BE-METADATA -->

## はじめに

本項目は、adb バックエンドに `request` / `requestSequence` によるネットワークキャプチャアサーションを
移植します。手段は2つです。`BajutsuAndroid`（`BajutsuKit` の Android 版で、現状はクリップボードのみに
対応しています）に OkHttp の interceptor を追加し、各通信を、iOS 向けにランナーがすでに起動している
インプロセスの collector へ報告させること。そして、エミュレータの隔離されたループバックを `adb
reverse` でその collector へ橋渡しすることです。アサーションのパイプラインにも、シナリオのスキーマにも、
capability モデルにも変更は必要ありません。`bajutsu/capability_preflight.py` はすでに、`network` を
どのバックエンドも宣言しなくても満たせる構成要素として扱っており、これは今日 idb が頼っているのと
同じ扱いです。

## 動機

[BE-0003](../BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci-ja.md) は、
ネットワーク観測のためにアプリ内 collector というモデルをランナーに導入しました。`BajutsuKit` の
`BajutsuURLProtocol` が `URLSession` の通信を捕捉し、ランナーが `127.0.0.1:<port>` で起動する実行ごとの
`NetworkCollector`（`bajutsu/network.py`）へ各通信を POST します。この結果を `request` /
`requestSequence` アサーションと `until: { request }` の待機（`bajutsu/assertions/network.py`）が
検証します。この仕組みが追加の配線なしに動くのは、iOS シミュレータが Mac 上のホストプロセスとして
動作し、Mac のループバックを共有しているからです。

[BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md) は adb バックエンドを、ネットワーク
対応を明示的にスコープ外としたまま出荷しており、この欠落は今も残っています。`AndroidEnvironment`
（`bajutsu/platform_lifecycle/environments/android.py:172-179`）は `observes_network_via_driver()` で
`False` を返し、`hook_collector` から `NotImplementedError` を送出します。これは iOS と同じ扱いです。
ランナーのデバイスプール（`bajutsu/runner/pool.py:136-193`）はこの違いを意識しておらず、Android の
デバイスごとに `NetworkCollector` を事前起動し、`BAJUTSU_COLLECTOR` / `BAJUTSU_COLLECTOR_TOKEN` を
起動時の環境変数へ、iOS とまったく同じ手順で注入します。Android 側にはこれを読み取るコードがこれまで
一切なかったため、`request` アサーションは大きく失敗するのではなく、捕捉した通信は0件という形で
黙って劣化します。これはバックエンドの未実装機能ではなく、アプリ側のバグのように見えてしまいます。
[BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md) が「宣言してはいるが
実現できない capability」で見つけたのと同じ信頼性の問題が、今回は最初からゲートされていない証跡の
種類で起きています。

この仕組みはそのまま移植できません。Android エミュレータは、iOS シミュレータのようにホストの
ループバックを共有しないからです。エミュレータ内部の `127.0.0.1` はエミュレータ自身を指し、collector
が動く Mac を指しません。この橋渡しには、このバックエンドにすでに動く前例があります。`bajutsu/adb.py`
の `forward_cmd` / `forward_remove_cmd`（`bajutsu/adb.py:524-535`）は、常駐 UI Automator サーバー
（[BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)）
のために、デバイス側の固定ポートをホストへトンネルしています。本項目に必要なのは逆方向のトンネル、
つまりデバイスからホストへ接続しにいく経路であり、これは `adb reverse` が提供します。

## 詳細設計

### 作業の分解（MECE）

1. **`BajutsuAndroid`: 通信を報告する OkHttp interceptor。** `BajutsuAndroid/` に `BajutsuNet.kt` を
   追加します。`BajutsuKit/Sources/BajutsuKit/BajutsuNet.swift` と `BajutsuURLProtocol.swift` に対応する
   Kotlin 版です。iOS の `URLProtocol` はすべての `URLSessionConfiguration` へ透過的に swizzle
   されますが、Android にはあらゆるクライアントに届く単一の OS レベルの HTTP フックがありません。
   そのため、テスト対象アプリは `OkHttpClient.Builder` に `.addInterceptor(BajutsuNet.interceptor())`
   という1行を追加します。この interceptor は `BAJUTSU_COLLECTOR` が存在しないときは何もしません
   （`BajutsuNet.startIfEnabled()` と同じ発想です）。起動時の env をすでに運んでいる intent extras
   （`bajutsu/adb.py:565-574`）から `BAJUTSU_COLLECTOR` / `BAJUTSU_COLLECTOR_TOKEN` を読み取り、
   完了した各通信を `NetworkExchange`（`bajutsu/network.py:29-47`）と同じフィールド名・エイリアスの
   JSON として POST します。これにより collector 側とアサーションのパイプラインは変更が不要になります。
   ライブラリは app-agnostic のままです（すべてのアプリが同じライブラリを組み込むのであって、アプリ
   ごとの設定差分ではありません）。これは `BajutsuAndroid` のクリップボード receiver がすでに確立した
   前例と同じです。

2. **bajutsu 側: `adb reverse` による collector への橋渡し。** `bajutsu/adb.py` に、`forward_cmd` /
   `forward_remove_cmd` と対をなす `reverse_cmd` / `reverse_remove_cmd` を追加します。`adb reverse
   tcp:<port> tcp:<port>` を実行し（デバイス側のポートを、`NetworkCollector` がすでに選んだホスト側の
   同じポート番号へ結び付けます）、後始末も行います。このトンネルを、`bajutsu/runner/pool.py` の
   Android リースのライフサイクルへ、既存の `BAJUTSU_COLLECTOR` 環境変数の注入（`pool.py:190-193`）と
   並べて組み込みます。起動の直前に確立し、リースの解放と同時に解除します。こうすると、今日すでに
   計算されている env の値（`http://127.0.0.1:<port>`）が、URL を書き換えることなくそのままデバイス上
   で解決します。

3. **capability モデルに変更が不要であることを確認する。** `bajutsu/capability_preflight.py:36-39` は
   すでに、`network` を意図的にゲートしていない理由を記録しています。idb はアプリ側の collector 経由
   で通信を捕捉していますが、`network` capability（このトークンは*ネイティブな*ドライバ観測を意味し、
   Playwright だけが持っています）を宣言していないからです。ユニット1・2が完了すれば、adb にも同じ
   理屈が当てはまります。`AdbDriver.CAPABILITIES`（`bajutsu/drivers/adb.py:542-554`）にも、
   `KIND_CAPABILITY` / `resolve_evidence_providers`（`bajutsu/backends.py:339-409`。BE-0020 の同一
   プラットフォーム内フォールバックで、Android には委譲先となる兄弟アクチュエータがないため、ここでは
   関係しません）にも変更は要りません。このユニットは実装ではなく検証です。この理由をここに記録して
   おくことで、将来の読み手が `AdbDriver` を誤って「`network` にネイティブ対応している」と宣言する
   方向に「修正」しないようにします。

4. **テストする。** `reverse_cmd` / `reverse_remove_cmd`（コマンドの形、collector のライフサイクルと
   対応した後始末）と、interceptor が送る JSON の形が `NetworkExchange` のパースに合うことを、fast
   gate で検証します。加えて、実際のエミュレータ上で `request` アサーションが通信をエンドツーエンドで
   観測できることを確認するオンデバイスの検証も、他の Android オンデバイス検証と同様に高速な Linux
   ゲートの対象外として追加します。

5. **ショーケースへの反映。** ショーケースの Android アプリに `BajutsuNet` の interceptor を組み込みます
   （クリップボード receiver の前例と同様、デバッグビルドに限ります）。そして Android の e2e シナリオを
   `request` アサーションで拡張し、この機能に動作確認済みの実例を持たせます。これは、BE-0233 における
   クリップボードの読み戻しが `device_android.yaml` で果たしている役割と同じです。

### 対応範囲の限界

捕捉の対象は OkHttp 経由の HTTP(S) 通信に限ります。これは iOS の `URLSession` のみという範囲
（`BajutsuKit/README.md:47-51`）と同じ形の制限です。`HttpURLConnection` を直接使うアプリや、別の
HTTP クライアント、`WebView` の通信は対象外です。iOS で `WKWebView` に別途の後続対応
（[BE-0037](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support-ja.md)）が必要だったのと
同じ理由です。

## 検討した代替案

- **バイトコードレベル、あるいはクラスローダレベルでの包括的なインターセプト。** iOS の `URLProtocol`
  swizzle と同じ透過性を狙う案です。却下しました。Android には `URLSessionConfiguration` のような
  単一かつ普遍的な HTTP の入口がありません。OkHttp、`HttpURLConnection`、サードパーティ製クライアント
  はそれぞれ別のフックを必要とするため、「すべてを捕捉する」アプローチは、より広範で決定性の低い
  計装（prime directive 2）を要求します。それに見合う利益は、明示的で opt-in な interceptor が
  すでに一般的なケース（OkHttp。Retrofit をはじめ現代の Android の通信の多くはこの上に構築されて
  います）で得られています。
- **`adb reverse` の代わりに、エミュレータ専用の固定エイリアス（`10.0.2.2`）を使う案。** 却下しました。
  `10.0.2.2` はエミュレータ専用の慣習であり、実機には対応するものがありません。これでは対象の種類に
  よって通信経路が分岐してしまい、`adb reverse` がすでに一様に果たしている役割（常駐 UI Automator
  サーバー向けに `forward_cmd` がすでに果たしている役割、BE-0245）を重複させることになります。
- **`mocks`（決定的なスタブ応答）を本項目に含め、iOS の `BajutsuMocks` と一気に機能を揃える案。**
  後続の項目へ切り出しました。スタブ応答を返すには、interceptor が通信を観測するだけでなく置き換える
  必要があり、これは設計として質の異なる部分です。これを含めると、観測側の変更に対して本項目の
  レビュー範囲を広げるだけで得るものがありません。BE-0003 自身も、その後の強化（BE-0115、BE-0130）を
  一度にまとめず、別々の後続項目として出荷しています。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] ユニット1 — `BajutsuAndroid` に `BajutsuNet.kt` の OkHttp interceptor を追加する。
- [ ] ユニット2 — `adb reverse` で collector を橋渡しする（`reverse_cmd` / `reverse_remove_cmd` を
  Android のリースのライフサイクルへ組み込む）。
- [ ] ユニット3 — capability モデルに変更が不要であることを確認し、記録する。
- [ ] ユニット4 — fast gate とオンデバイスのテストを追加する。
- [ ] ユニット5 — ショーケースの Android アプリと e2e シナリオに反映する。

## 参考

[BE-0003 — codegen・トレース・ネットワーク・CI（M3）](../BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci-ja.md)、
[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend-ja.md)、
[BE-0245 — adb の読み取り向け常駐 UI Automator サーバ](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server-ja.md)、
[BE-0233 — adb クリップボードの実機での忠実性](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md)、
[BE-0037 — WebView / ハイブリッド対応](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support-ja.md)、
`BajutsuKit/Sources/BajutsuKit/BajutsuNet.swift`、`BajutsuKit/Sources/BajutsuKit/BajutsuURLProtocol.swift`、
`BajutsuAndroid/src/main/java/dev/bajutsu/android/Bajutsu.kt`、
`bajutsu/network.py`、`bajutsu/assertions/network.py`、`bajutsu/adb.py`、
`bajutsu/platform_lifecycle/environments/android.py`、`bajutsu/runner/pool.py`、
`bajutsu/capability_preflight.py`

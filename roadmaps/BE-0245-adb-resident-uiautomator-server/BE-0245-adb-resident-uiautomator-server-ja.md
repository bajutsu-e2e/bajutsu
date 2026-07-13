[English](BE-0245-adb-resident-uiautomator-server.md) · **日本語**

# BE-0245 — adb の読み取り向け常駐 UI Automator サーバ

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0245](BE-0245-adb-resident-uiautomator-server-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装中** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0245") |
| 実装 PR | [#1011](https://github.com/bajutsu-e2e/bajutsu/pull/1011) |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance-ja.md), [BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md), [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md), [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md) |
<!-- /BE-METADATA -->

## はじめに

Android の adb バックエンドでの画面読み取りは、iOS より 1 桁遅く、その差はまるごと呼び出しごとの
起動コストにあります。`AdbDriver.query()` は毎回 `adb exec-out uiautomator dump` を呼び出しますが、
これは新しいインストゥルメンテーションを起動し、`UiAutomation` セッションを接続し、アイドルを待ち、
ダンプし、セッションを破棄します。1 回あたり約 2.4 秒で、同じ読み取りが idb では 0.1〜0.3 秒です。
この項目では、実行のあいだ**常駐する UI Automator インストゥルメンテーション**を保ち、階層を
ローカルのソケット／HTTP 経由で問い合わせることで、その起動コストを取り除きます。Appium の
UiAutomator2 ドライバが採る方式です。これは
[BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance-ja.md) から切り出した 4 つ目
（最後の、アーキテクチャ上の）作業単位です。検証に実機が必要で、計装をパッケージする作業も伴うため、
BE-0234 の高速ゲート内の変更には収まらず、別項目としました。ここで挙げる変更は決定性の契約を
崩しません。読み取りは条件待ちのままで、曖昧なセレクタは即座に失敗し、`run` の経路に LLM は
入りません。

## 動機

[BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance-ja.md) は Android の実行が
遅い原因を画面読み取りに絞り込み、低リスクな 2 つの削減（ランナーのステップ末尾の読み取りを遅延させ、
直前ステップのツリーを次ステップの `before` として再利用する）と adb の `_settle` の再調整を入れました。
これらは読み取る**回数**を減らしますが、下限は下げられません。すなわち `uiautomator dump` の
**呼び出しごと**の約 2.4 秒です。BE-0234 の計測が示すとおり、このコストはツリーのサイズ・圧縮・
出力先に依存せず、XML の転送や走査ではなくインストゥルメンテーションの起動なので、読み取りを減らす
だけでは越えられません。必要な読み取りが依然として 1 回約 2.4 秒かかる読み取り主体のバックエンドは、
BE-0234 の削減を入れてもなお Android のシナリオ作成を重くします。

常駐セッションは、その起動コストを実行全体で**一度だけ**払い、以降の各読み取りには開いたままの
チャネルで応答します。これがまさに iOS との 10〜20 倍の差を縮めます。読み取りは約 2.4 秒から
約 0.1〜0.3 秒へ下がる見込みです。コストは現実にあり（計装のパッケージとそのライフサイクル）、
それがノブではなく独立した項目にした理由ですが、見返りは Android の読み取りを iOS 並みに安くする
唯一の変更です。

常駐サーバは**一様でアプリに依存しない**部品です。テスト対象がどのアプリであってもそれを駆動します。
これは on-device SDK（[BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md)、
BajutsuKit／BajutsuAndroid）と同じ性質です。どのターゲットも同一に使う一様なライブラリはアプリ
ごとの特別扱いではないので、これらの SDK と同じ意味で prime directive 3（アプリ非依存）に収まります。
ツール・ドライバ・ランナーはターゲットをまたいで変わらず、どのシナリオも変えません。

## 詳細設計

変更は、adb ドライバが階層ダンプを**どう取得するか**に限られます。差し替えるのは
`AdbDriver._describe()`（[`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py)）の内部だけです。
パース（`parse_hierarchy`）、Elements への正規化、transient-empty のリトライ、`_settle`、そして
セレクタ／解決の契約はいまのままなので、既存の adb テストとドライバ conformance スイート
（[BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)）はそのまま
通り続けます。常駐サーバが起動できないデバイスのために既存の `uiautomator dump` 経路をフォールバック
として残すので、どのデバイスも今より悪くはなりません。

### 作業分解（MECE）

1. **常駐インストゥルメンテーションのパッケージと起動。** UI Automator のインストゥルメンテーション
   （Appium の UiAutomator2 サーバと同じ形の `androidx.test` UiAutomator サーバ）を同梱し、実行の
   はじめに対象デバイスへインストールして起動します。これがアプリに依存しない部品です。配布方法
   （プリビルド APK をリポジトリに置くか、必要に応じてビルドするか）とリポジトリ内の置き場所を、
   on-device SDK のパッケージ方法（[BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md)）
   にならって決めます。

2. **階層問い合わせのチャネル。** アクセシビリティ階層をローカルのソケット／HTTP（常駐サーバへの
   `adb forward`）で公開し、「現在の階層をダンプする」要求と応答を定めます。応答は現在の
   `uiautomator dump` の XML と同じ情報を持たせ、`parse_hierarchy`（またはそこへの薄いアダプタ）が
   同一の Elements を生み続けるようにします。セレクタの意味は変えません。この単位が変えるのは意味
   ではなく転送です。

3. **`AdbDriver._describe()` を常駐チャネルへ切り替え、ダンプ経路をフォールバックに。** 常駐サーバが
   使えるときは `_describe()` をそこへ通し、起動やチャネルの失敗時は `adb exec-out uiautomator dump`
   （現在の経路）へフォールバックして、失敗ではなく現在の挙動へ縮退させます。`_describe()` より上、
   すなわち `query()` の transient-empty リトライ・`_settle`・`_resolve`・操作は変えません。

4. **サーバのライフサイクルを実行に結びつける。** 常駐サーバはデバイスのリースごとに一度起動し、
   実行の終了時（失敗・中断を含む）に停止して、デバイスに計装を残しません。専用の経路ではなく、
   既存のデバイスプール／リースのライフサイクルに畳み込みます。

5. **実機で検証し、リグレッションから守る。** 実機のエミュレータで前後の 1 ステップあたりの読み取り
   実時間を記録し（BE-0234 の読み取り回数の物差しはすでにあります）、読み取りが約 0.1〜0.3 秒へ下がる
   ことを確認し、Android の e2e レーン
   （[BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)）を拡張して
   常駐経路とダンプのフォールバックの両方を通します。実時間の CI ゲートはスコープ外とします（実時間は
   環境依存で不安定になるため。BE-0234 と同じ方針です）。

## 検討した代替案

- **呼び出しごとの `uiautomator dump` を保ち、読み取りを減らすだけにとどめる（BE-0234 の作業単位
  1〜3）。** すでに入っており有用ですが、1 回約 2.4 秒という読み取りの下限は越えられません。その下限
  こそこの項目が取り除くものです。両者は補い合います。読み取りを減らし、かつ読み取りを安くします。

- **`uiautomator dump --compressed`／デバイス上のファイルへ dump して `cat`。** どちらも BE-0234 の
  計測で約 2.0〜2.5 秒で、改善しません。コストは XML のサイズや出力先ではなくインストゥルメンテーション
  の起動だからです。BE-0234 で却下しており、ここでもボトルネックには効きません。

- **Appium の UiAutomator2 サーバをそのまま依存に取り込む。** 魅力的ですが、bajutsu が必要とするもの
  （常駐の階層ダンプ）に対して、フル機能の Appium サーバ・そのコマンド群・そのバージョニングという
  大きな表面を持ち込みます。本設計は Appium の*方式*（ソケット越しに問い合わせる常駐インストゥルメン
  テーション）を借りつつ、サーバ全体は取り込みません。再利用と最小実装の境目は実装時の論点です。

- **読み取りをまたいで `uiautomator` シェルプロセスを常駐させて使い回す。** 効きません。シェルを
  開いたままにしても、`uiautomator dump` の各呼び出しは自前のインストゥルメンテーションを起動する
  ため、起動コストはシェルごとではなくダンプごとに払われます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 常駐 UI Automator インストゥルメンテーションのパッケージと起動（アプリに依存しない部品。配布方法を決める）。
- [ ] 階層問い合わせのチャネル（ローカルのソケット／HTTP）を定め、その応答を `parse_hierarchy` がそのまま消費できるようにする。
- [ ] `AdbDriver._describe()` を常駐チャネルへ切り替え、`uiautomator dump` をフォールバックとして残す。
- [ ] 常駐サーバのライフサイクルをデバイスのリースに結びつける（一度起動し、実行の終了・失敗時に停止）。
- [ ] 実機で検証し（読み取りが約 0.1〜0.3 秒へ）、Android の e2e レーンで常駐経路とフォールバックの両方を守る。

ログ:

- PR-A（[#1011](https://github.com/bajutsu-e2e/bajutsu/pull/1011)）— 常駐チャネルへの切り替え（作業単位 3）に向けた、Python のみ・実機不要の足場です。
  `AdbDriver` に注入できる `fetch_hierarchy` の継ぎ目を足しました。`_describe()` は `_read_source()`
  経由で読み取り、常駐フェッチが設定されていればそれを使い、`AdbResidentError` のときは警告を大きく
  出して `uiautomator dump` へ縮退します。`parse_hierarchy` と `_describe()` より上（transient-empty
  リトライ・`_settle`・`_resolve`・操作）は変えず、既定（フェッチなし）はいまのダンプ都度読み取りの
  ままなので、チェックはまだ 1 つも付きません。作業単位 2 の階層問い合わせチャネル（`adb forward` による
  トランスポートとその HTTP ハンドシェイク）はまだ書いておらず、後続の PR で入ります。

## 参考

[BE-0234 — adb のシナリオ実行を高速化する](../BE-0234-adb-run-performance/BE-0234-adb-run-performance-ja.md)、
[BE-0007 — Android バックエンド](../BE-0007-android-backend/BE-0007-android-backend-ja.md)、
[BE-0233 — adb クリップボードの実機忠実度](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity-ja.md)、
[BE-0114 — ドライバ conformance スイート](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)、
[BE-0208 — CI での Android 実機 e2e](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci-ja.md)、
[`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py)、
[`bajutsu/adb.py`](../../bajutsu/adb.py)

[English](BE-0208-android-emulator-e2e-ci.md) · **日本語**

# BE-0208 — Android の実機 e2e を CI に配線する（KVM 経由のエミュレータ）

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0208](BE-0208-android-emulator-e2e-ci-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装中** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0208") |
| 実装 PR | [#851](https://github.com/bajutsu-e2e/bajutsu/pull/851)、[#880](https://github.com/bajutsu-e2e/bajutsu/pull/880)、[#899](https://github.com/bajutsu-e2e/bajutsu/pull/899)、[#901](https://github.com/bajutsu-e2e/bajutsu/pull/901)、[#906](https://github.com/bajutsu-e2e/bajutsu/pull/906)、[#910](https://github.com/bajutsu-e2e/bajutsu/pull/910)、[#924](https://github.com/bajutsu-e2e/bajutsu/pull/924) |
| トピック | Platform expansion (Android / Web / Flutter) |
| 関連 | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md)、[BE-0223](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation-ja.md) |
<!-- /BE-METADATA -->

## はじめに

iOS には実機 e2e ワークフロー（`.github/workflows/e2e.yml`）があり、web バックエンドにも専用の
もの（`web-e2e.yml`）があります。しかし Android の e2e レーンはありません。Android バックエンド
（[BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md)）はローカルの arm64
エミュレータで一度検証されましたが（2026-07-07）、その検証が黙って退行するのを防ぐものが CI に
ありません。本項目は Android エミュレータの e2e ワークフローを追加します。Linux ランナーで KVM の
もとに AVD を起動し、showcase シナリオを `--backend android` で駆動するもので、idb と web の e2e
レーンとまったく同じく、fast の `make check` ゲートの外に置きます。

## 動機

Android の実機アクチュエーションとデバイス制御の作業（この同じバッチで起票した、Android
アクチュエーション忠実度の項目とデバイス制御の項目）は、実機に対してのみ現れるので、Linux の
fast ゲートでは覆えません。CI にエミュレータのレーンがないと、これらの挙動は一度手で検証される
だけで、その後のどの変更でも気付かれずに退行しかねません。iOS も web もすでに e2e レーンを
持っています。Android の分を追加すれば、3 つ目のバックエンドについてもその安全網が戻り、実機の
スライスをローカルだけでなく CI で検証できるようになります。BE-0007 の phasing のノートも、これを
すでに見込んでいました。「エミュレータは KVM 経由の Linux CI（`android-emulator-runner`
アクション）で動く」のとおりです。

## 詳細設計

ワークフローは既存の e2e レーンをなぞります。専用のファイルを持ち、関連するパスでトリガーし、
`make check` には含めません。LLM は使わず、固定のシナリオに対する決定論的な `run` なので、prime
directive の枠内にとどまります。

### 作業分解（MECE）

1. **ワークフロー**（`.github/workflows/android-e2e.yml`）。KVM つきの
   `reactivecircus/android-emulator-runner` を使う Linux ランナーで、ローカル検証が使った API
   レベル（arm64 API 34）の AVD を起動します。他の e2e ワークフローと同じく、パスフィルタで
   ゲートします。
2. **showcase をビルドしてインストールする**。Android の showcase（Compose と Views の双子）を
   ビルドし、起動したエミュレータにインストールします。
3. **通るシナリオを実行する**。すでに実機で通っている中核の id/tap/type/value シナリオを
   `--backend android` で駆動し、決定論的な合否を検証します。
4. **visual／golden ベースラインの同等性**。2026-07-07 の検証で未確認のまま残った唯一の証跡の
   次元である、Android の visual／golden ベースラインのチェックを、このレーンの範囲に含めます。
5. **実機スライスとともに育てる**。アクチュエーション忠実度の項目とデバイス制御の項目が着地する
   につれて、それらが直すフロー（`notices`・`gestures`・`controls`・位置情報／クリップボード）まで
   シナリオ集合を広げ、レーンが増えていく実機の対象面を追随するようにします。

## 検討した代替案

- **self-hosted の macOS ランナー**。却下しました。Android エミュレータは KVM 経由の Linux で
  動き、そのほうが安価で、BE-0007 の phasing の選択（Android は Linux CI で lean な側を担う）に
  合います。macOS ランナーが要るのは idb レーンだけです。
- **エミュレータの実行を `make check` に畳み込む**。却下しました。エミュレータの起動は fast
  ゲートには重すぎます。fast ゲートは実機なしで Linux を含むどこでも走らなければなりません。e2e
  レーンは設計上分かれており、それは idb と web でも同じです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] ワークフロー（`.github/workflows/android-e2e.yml`）。`android-emulator-runner` ＋ KVM、パスでゲート。
- [x] 起動したエミュレータへの Android showcase のビルドとインストール。
- [x] 通る中核シナリオを `--backend android` で実行。
- [x] visual／golden ベースラインの同等性チェックのうち、**golden**（要素ツリー）の次元（Compose の Stable カタログ）。
- [ ] visual／golden ベースラインの同等性チェックのうち、**visual**（スクリーンショット）の次元（ホスト依存のベースラインで、CI での採取が要るため保留）。
- [ ] アクチュエーション忠実度とデバイス制御のスライスの着地に合わせたシナリオ集合の拡張。

### ログ

- 2026-07-09 — 最初のスライス（ユニット 1〜3）。`.github/workflows/android-e2e.yml` を追加しました。
  KVM のもとで AVD を起動し（`reactivecircus/android-emulator-runner`）、コアの id/tap/type/value
  シナリオを `--backend android` で回す Linux レーンで、`demos/showcase/android/Makefile` に足した
  `e2e` ターゲットが駆動します。AVD は（ローカル検証の arm64 ではなく）**x86_64** の API 34 です。
  x86_64 の GitHub Linux ランナーで KVM が加速するには x86_64 のシステムイメージが要り、異なる
  アーキテクチャのイメージは遅いソフトウェアエミュレーションに落ちるためです。API レベルは一致し、
  ABI だけを CI ホストに合わせています。`docs/ci.md`（と ja）に記載しました。`web-e2e.yml` と
  同じくパスでゲートし、fast の `make check` ゲートの外に置いています。最初の CI 実行で、エミュレータが
  起動してアプリを駆動できることを確認しました。`smoke`・`firstlook`・`search` は通りましたが、
  `components` は sheet の表示を 5 秒待つところでタイムアウトしました。CI のエミュレータは
  ソフトウェア描画（swiftshader）のため、ローカルのハードウェア加速された arm64 実機では通る 5 秒の
  sheet 表示待ちには、modal の表示が遅すぎます。そこで sheet／cover 系の 2 本（`components`・`modals`）は
  レーンの初期集合（`smoke`・`firstlook`・`search`・`data_driven`・`relaunch`・`system`）から外し、
  実機タイミングの調整後に戻します（ユニット 5 に畳み込み）。ユニット 4（visual／golden ベースラインの
  同等性）と 5（シナリオ集合の拡張）は残します。ベースラインの次元は実機での初回採取が前提で、追加
  シナリオは先に BE-0007 の後続スライス（と上記の modal タイミング調整）が着地する必要があるためです。
  項目は**実装中**のままです。
- 2026-07-10 — ユニット 5（Stable タブのスライス）。並行して入った BE-0107 が `SHOWCASE_TAB` 起動
  ショートカットを廃止したため、共有シナリオはネイティブのタブバーをタップしてタブへ到達するように
  なりました。adb はタブバーを駆動できません（駆動できるのは XCUITest バックエンドだけです）。そのため
  Stable タブ以外のシナリオ、すなわち `search`、`data_driven`、`relaunch`、`system` と、Log／Notices
  タブのフローである `components`、`modals`、`gestures`、`controls`、`notices` は adb レーンから外れ
  （レーンは `smoke` と `firstlook` に縮小しました）、adb のタブバー移動（BE-0007 のドライバー側の後続
  作業）を待つ状態になりました。これは、modal のタイミング調整だけで `components`／`modals` を戻すという
  当初の計画に取って代わります。ブロッカーは modal の遅さではなく、Log タブへ到達できないことだからです。
  この制約のもとで、`navigation` を加えてレーンを広げます（`demos/showcase/android/Makefile` の
  `E2E_SCENARIOS`）。このシナリオはアプリが起動する Stable タブから離れません。カタログの行をタップして
  Horse Detail を push し、favorite を切り替えてから、バックエンド共通の `back` ステップ（Android では
  システムキー）で pop して戻ります。タブバーを必要とせず、詳細画面のアサーションと back ナビゲーションを
  レーンのカバレッジに加えます。検証は Python ゲート（`make check`）で行い、レーン自体は CI で回します
  （ローカルにエミュレータはありません）。ユニット 5 の残り（タブに依存するシナリオ）は adb のタブバー
  移動にブロックされ、ユニット 4（visual／golden の同等性）は残します。項目は**実装中**のままです。
- 2026-07-10 — ユニット 4（golden の次元）。実機での golden 要素ツリーのチェックをレーンに加えました。
  新しいシナリオ `demos/showcase/scenarios/golden/golden_android.yaml` が、Compose の Stable カタログの
  正規化ツリー（行、refresh ボタン、値を反映した status）を、採取したベースライン
  `demos/showcase/scenarios/golden/goldens/lists_android.json` と突き合わせます。これはバックエンド固有の
  ベースラインで、idb の `lists.json` や XCUITest の `controls.json` の adb 版にあたります（バックエンド
  ごとにアクセシビリティツリーの見え方が異なり、adb のツリーの trait は idb の `button`／`staticText` では
  なく `view`／`textView` です）。シナリオは（起動タブである）Stable タブで動くため、タブバー移動を必要と
  しません。`demos/showcase/android/Makefile` に独立した `e2e-golden` ターゲットとして配線し、
  `android-e2e.yml` では `e2e` と同じエミュレータセッションで実行します。ベースラインは**ローカルの arm64**
  エミュレータ（API 34、`google_apis`）で採取しましたが、CI の **x86_64** エミュレータでも通ります。golden の
  比較がフィールド単位で、identity／label／trait は厳密一致、frame は健全性チェックだけ（`bajutsu/golden.py`）
  だからです。identity／label／trait は ABI をまたいでも変わらず、密度で拡縮する frame だけが異なり、それは
  許容されます。ユニット 4 の visual（スクリーンショットの画素比較）の次元は意図的に保留します。画素
  ベースラインはホスト依存で（ローカルの arm64 と CI の x86_64 ではソフトウェア描画が画素単位で食い違います）、
  CI で採取したベースラインが要るためです。これは後続のスライスに回します。項目は**実装中**のままです
  （ユニット 4 の visual の次元と、タブに依存するユニット 5 の残りが残ります）。
- 2026-07-10 — ユニット 5（タブに依存するシナリオ）：[BE-0223](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation-ja.md)
  で adb ドライバーがネイティブのタブバーを操作できるようになり（Compose の `NavigationBarItem` が共有の
  `{ label, traits: [button] }` セレクターに解決します）、このユニットのブロックが解けました。タブバーを
  操作できるようになったことで、`search`・`data_driven`・`relaunch`・`system` が `E2E_SCENARIOS` へ戻り、
  いずれもローカルの arm64 エミュレータで確認しました。残りの除外シナリオは、タブバーとは無関係の理由で
  除外したままです。`components` と `modals` はローカルでは通りますが、5 秒のシート表示待ちが CI の
  x86_64 ソフトウェアレンダリングで超過する恐れがあり、`gestures`（マルチタッチ、BE-0210）・`controls`
  （segmented control の value）・`notices`（深いスクロール）はそれぞれ BE-0007 のフォローアップのスライスを
  待ちます。ユニット 4 の visual の次元は、CI で採取したベースラインが引き続き必要です。項目は**実装中**の
  ままです。
- 2026-07-11 — ユニット 5（シート/カバーのフロー）。`components` と `modals` が `E2E_SCENARIOS` へ
  戻りました。どちらもローカルの arm64 エミュレータでは通りますが、共有している 5 秒のシート表示待ちが、
  モーダルの描画が遅い CI の x86_64 ソフトウェアレンダリングで超過する恐れがありました。共有シナリオを
  バックエンドごとに調整し直す（`timeout: 5` はどのバックエンドでも同じです）のではなく、Android の
  レーンだけが待ちの上限を引き上げます。`bajutsu/orchestrator/waits.py` が `BAJUTSU_MIN_WAIT_TIMEOUT`
  を各待ちのタイムアウトの下限として尊重するようになり、`demos/showcase/android/Makefile` の `e2e`
  ターゲットがこれを渡します（既定 15 秒）。条件待ちは条件が満たされた瞬間に返るので、上限を広げても固定の
  待ち時間にはならず安全な上限にとどまります（プライムディレクティブ 2 は保たれます。依然として条件待ちで
  あり、固定の sleep ではありません）。ほかのバックエンドには影響しません。`docs/ci.md`（と ja）に記載
  しました。残りの除外シナリオは、待ち時間とは無関係の理由で除外したままです。`gestures`（マルチタッチ、
  BE-0210）、`controls`（segmented control の value）、`notices`（深いスクロール）はそれぞれ BE-0007 の
  フォローアップです。ユニット 4 の visual の次元は、CI で採取したベースラインが引き続き必要です。項目は
  **実装中**のままです。
- 2026-07-11 — ユニット 5（深いスクロールのフロー）。`controls` と `notices` が `E2E_SCENARIOS` へ
  戻りました。どちらも遠くの対象を画面内までスクロールします。`controls` はボタンの下にある segmented
  control の値ノードを、`notices` は折り返しよりかなり下の一覧の行を対象にします。実機ではどちらも失敗し
  ましたが、原因は segmented control の値ノードや行が存在しないことではありませんでした（以前の「segmented
  control の value を adb が公開していない」という診断は誤りで、ノードは公開されています）。既定の方向
  スワイプが対象を画面内に運ぶほど十分にスクロールしなかっただけです。既定のスワイプは固定の座標量を移動して
  おり、その量は frame の単位に依存します。iOS はポイント、Android は生ピクセルなので、iOS 向けの量では
  密度の高い Android の 2400px の画面を約 2.6 分の 1 しかスクロールできず、対象に届きませんでした。修正では
  既定のスワイプが画面に対する割合分を移動するようにしました（`bajutsu/orchestrator/actions/handlers/
  gestures.py` の `_SWIPE_FRACTION = 0.125`）。これで、どちらのバックエンドでも同じ割合まで届きます。0.125
  は歴史的な高さ 800 の基準画面で従来の 100 単位の移動量を再現するので、既存の iOS／web のスワイプは変わらず、
  共有シナリオにも手を入れません。`docs/ci.md`、`docs/run-loop.md`、`docs/scenarios.md`、
  `docs/dsl-grammar.md`（と ja）に記載しました。ローカルの arm64 エミュレータで検証しました。新しい 2 本を
  含むレーン全体の 11 シナリオが通り、Python ゲート（`make check`）も緑です。最後に除外したままの `gestures`
  は理由が異なります。long-press は届きますが、double-tap が adb の `input tap ; input tap` では登録され
  ません。各 tap が新しい `input` プロセスを起動するため、速い実機でも tap 間の間隔がプラットフォームの
  double-tap ウィンドウを超過します。raw な `sendevent` による double-tap は別のスライスにします。
  `gestures_multitouch`（pinch／rotate）はマルチタッチを必要とし（adb は単一タッチです）、ユニット 4 の
  visual の次元は、CI で採取したベースラインが引き続き必要です。項目は**実装中**のままです。
- 2026-07-11 — ユニット 5（gestures の double-tap）。最後に除外したままだった単一タッチのフロー
  `gestures` が `E2E_SCENARIOS` へ戻りました。long-press はすでに届いていましたが、double-tap は届き
  ませんでした。`input tap ; input tap` は tap ごとに新しい `input` の JVM を起動するため、1 回のラウンド
  トリップに連ねても tap 間の間隔がプラットフォームの double-tap ウィンドウを超過していたためです。adb
  ドライバは、double-tap を生の `sendevent` によるタッチ列で実行するようになりました（`bajutsu/adb.py`
  の `sendevent_double_tap_cmd` と `parse_touch_device`／`scale_to_touch`、配線は
  `bajutsu/drivers/adb.py`）。protocol B の 2 つの接触を 1 回の `adb shell` のなかで発火するので、tap の
  間には JVM ではなく `sendevent` の小さなネイティブ起動だけが挟まり、間隔がウィンドウの内側に収まります。
  `sendevent` は `/dev/input` に直接書き込むので、root と具体的なタッチスクリーンのノードが必要です。
  ドライバは `getevent -lp` でノードを探索し（エミュレータは同一の `virtio_input_multi_touch_*` ノードを
  複数並べますが、画面につながっているのは最小番号の `eventN` だけです）、`id -u` でゲートして、いずれかが
  欠けるときは `input tap` にフォールバックします。これで、root 化していないデバイスが従来より悪くなること
  はありません。`e2e` の Makefile ターゲットは、実行前にエミュレータを root 化します（`adb root`、
  google_apis イメージで許可されています）。ローカルの arm64 エミュレータで検証し（3 回とも通り、
  double-tap のカウンタが 1 に達します）、Python ゲート（`make check`）も緑です。`gestures_multitouch`
  （pinch／rotate）はマルチタッチを必要とし（adb は単一タッチです）、ユニット 4 の visual の次元は、CI で
  採取したベースラインが引き続き必要です。項目は**実装中**のままです。

## 参考

[BE-0007 — Android バックエンド](../BE-0007-android-backend/BE-0007-android-backend-ja.md)、
`.github/workflows/e2e.yml`、`.github/workflows/web-e2e.yml`

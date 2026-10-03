[English](BE-XXXX-target-config-restructure.md) · **日本語**

# BE-XXXX — target の設定を用途別に整理し、動作環境（端末、OS、ブラウザ）を宣言できるようにする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-target-config-restructure-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | — |
| トピック | ドライバとバックエンドのアーキテクチャ |
| 関連 | [BE-0126](../BE-0126-per-platform-effective-config/BE-0126-per-platform-effective-config-ja.md)、[BE-0358](../BE-0358-device-os-as-a-first-class-fact/BE-0358-device-os-as-a-first-class-fact-ja.md)、[BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation-ja.md)、[BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines-ja.md)、[BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks-ja.md)、[BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction-ja.md)、[BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch-ja.md)、[BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md)、[BE-0447](../BE-0447-install-app-step/BE-0447-install-app-step-ja.md)、[BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md) |
<!-- /BE-METADATA -->

## はじめに

現在、target の設定（`targets.<name>`）は、40を超えるキーを1階層に並べています。`browser`と`deviceMode`は Web 専用、`nativeZ`は Android 専用、`bundleId`と`xcuitest`は iOS 専用のキーですが、これらも同じ階層に並んでいます。しかしスキーマは、キーとプラットフォームの対応を持っていません。また、target が想定する端末とオペレーティングシステム（OS）を書くキーもありません。

この項目では、1階層の並びを11個のキーに置き換えます。各キーが受け持つ用途は1つだけです。たとえば`app`はテスト対象のアプリを、`runsOn`は動かす端末を、`driver`は Bajutsu の駆動方法を受け持ちます。11個のうち`app`、`runsOn`、`driver`、`run`の4つは、`platform`の値によって形が変わります（`run`は iOS で項目が増えます）。`platform`を含む残りの7つは、どのプラットフォームでも同じ形です。

`runsOn`には、target を実施する環境（端末、OS、ブラウザ）を書きます。シナリオも`preconditions`の下に自分の`runsOn`を書け、シナリオの値が target の値より優先されます。（機種、AVD、ブラウザのエンジンの）リストや、上限と下限の両方がある版の範囲は、値ごと、メジャーバージョンごとに1回ずつの実行を求め、手元の端末で引き受けられない回は失敗します。`>=18`のような片側が開いた条件を満たす端末が手元にないシナリオは、走らせずに「対象外」として記録します。後方互換は意図して持ちません。旧形式の設定は読み込み時に失敗し、エラーには受け付けなくなったキーの名前が出ます。

## 動機

target に別のプラットフォームのキーを書いても、設定はエラーなく読み込まれ、そのキーは何の効果も持ちません。たとえば iOS の target に`browser: firefox`と書くと、検証は通り、run はこの設定を無視します。[`docs/configuration.md`](../../docs/configuration.md)は、キーの表に「iOS ignores it」という注記を繰り返し書いて、この欠落を補っています。利用者は、キーが効くかどうかをこの表で調べるしかありません。

全プラットフォームが1つの名前空間を共有しているため、キーの名前にも無理が出ています。[`defaults.py`](../../bajutsu/common/config/schema/defaults.py)の`device`は iOS Simulator の機種名です。それなのに、デフォルト値として Web や Android の target にも重なります。Web のバックエンドの端末設定が`deviceMode`という別名になったのは、`device`という名前がすでに使われていたためです。

target を動かす環境を指定するキーもありません。iOS のバージョンは、`--udid`で選んだ Simulator によって決まります。Android の application programming interface（API）レベルは、adb のシリアルで選んだ端末によって決まります。`device`が効くのは、実行中に消えた Simulator の代わりを作る場面だけです。run は、実際に動いた OS を`device_runtime`として記録します（[BE-0358](../BE-0358-device-os-as-a-first-class-fact/BE-0358-device-os-as-a-first-class-fact-ja.md)）。そのため、シナリオが想定外の OS で落ちても、気づくのは実行後です。しかもその失敗は、flakiness の履歴に雑音として混ざります。

シナリオの側にも、どこで実施するかを書く手段や、複数の画面サイズで回すよう求める手段がありません。たとえば、iOS 18 の機能を試すシナリオや、iPad にしかない画面のシナリオです。これらを他の端末で走らせないようにするには、現在は target を`showcase-iphone`と`showcase-ipad`に分けるか、シナリオの一覧を手で分けて管理するしかありません。iOS 17 と iOS 18、あるいは小さい端末と大きい端末で同じ一式を回すときも、回すたびに端末とシナリオを選び直すことになります。

同じ用途の設定が、複数のキーに散らばってもいます。アプリの起動に関わる設定は`launchEnv`、`launchArgs`、`readyWhen`の3つです。どこで実行するかに関わる設定は`deviceProvider`、`cloudBatch`、`cloudBatchBudget`、`requires`の4つです。しかも、どこで実行するかは target を書くチームではなく、マシンを運用する人が決めることです。全シナリオの前に走る手順は、target の`setup`、シナリオの`preconditions.setup`、`before`の3つのキーで書けてしまい、文書はそれらの違いを説明し続けています。プラットフォームの判定も複雑です。[`resolve.py`](../../bajutsu/common/config/resolve.py)の`_effective_platform`は、`platform`、`backend`、識別子の有無を優先順位の順に調べて、プラットフォームを推論します。Flutter のようなバックエンドを足すと、1階層のキーも推論の分岐も増えます。

この項目の実装後は、次の3点で効果を確かめられます。

- 別のプラットフォームのキーを書くと、設定の読み込み時に失敗します。エラーには、そのプラットフォームで使えるフィールドの一覧が出ます。
- iOS 17 と iOS 18 の Simulator が混ざった端末群で、2つの機種と範囲`>=17 <19`を書いたシナリオを回すと、機種 × OS の結果表が出ます。各マスで1回ずつ走ります。
- 機種名を書き間違えると、その回は「no available device for model …」として失敗します。カバレッジが黙って減ることはありません。

## 詳細設計

### 前提となる項目

この項目は、[BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch-ja.md)と[BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md)が入ってから着手します。ここでいう「入った」とは、最終的な2つの削除を除き、両項目のすべての作業単位がマージされた状態を指します。1つは、BE-0448 の作業単位5による`cloudBatchBudget`とサーバー側の予算の仕組み（`deviceBudget`、`max_concurrent_batch`、`try_register(device_budget=…)`）の削除です。もう1つは、BE-0450 による`requires`の削除です。これらは、非推奨になりながらもまだ受け付けられています。この項目の作業単位4が、それらをすべて取り除きます。そのため、fan-out の要求の`deviceBudget`の非推奨期間は、BE-0448 だけの場合より早く、スキーマの切り替えの時点で終わります。この2つは、worker capability のファイル`worker.yaml`と`environment:<name>`による振り分けを提供し、target から外れるキーはそこへ移ります（「どこで実行するか」を参照）。先に着手すると、一括でスキーマを切り替える時点で、`deviceProvider`と`cloudBatch`の移る先がありません。

### 11個のキー

target は次のキーだけを受け付けます。`platform`は必須です。`defaults.platform`と、`backend`や識別子の有無からの推論はなくなります。

| キー | 用途 | 形が`platform`で変わるか |
|---|---|---|
| `platform` | どのバックエンドで動かすか | —（判別子） |
| `app` | 何をテストするか（識別子、入手と起動の方法、起動待ち、id の約束） | 変わる |
| `runsOn` | 何の上で動くか（端末と OS の条件、ブラウザ、端末の状態） | 変わる |
| `driver` | Bajutsu がどう駆動するか | 変わる |
| `services` | run が起動する、または代わりを務めるサーバーは何か | 変わらない |
| `run` | 実行の方針は何か | iOS で項目が増える |
| `hooks` | 全シナリオを包むステップ列は何か | 変わらない |
| `evidence` | 何を残し、何を隠すか | 変わらない |
| `paths` | ファイルをどこに置くか | 変わらない |
| `ai` | AI の経路をどの提供元で動かすか | 変わらない |
| `notify` | 結果をどこへ知らせるか | 変わらない |

キーは、どのプラットフォームが使うかではなく、用途で置き場所を決めます。たとえば`nativeZ`は Android 固有ですが、Bajutsu が端末をどう駆動するかを答えるので`driver`に入ります。

```yaml
targets:
  showcase-swiftui:
    platform: ios
    app:
      id: com.bajutsu.showcase.ios.swiftui
      path: build/Showcase.app
      build: make -C demos/showcase swiftui-build
      launch: { env: { SHOWCASE_UITEST: "1" } }
      startWhen: { exists: { id: home.title } }
    runsOn:   { kind: simulator, locale: en_US }
    driver:   { runner: { testRunner: build/Runner.xctestrun } }
    run:      { erase: true, secrets: [LOGIN_PASSWORD], tipKitHandling: true }
    hooks:    { setup: [{ use: { component: components/login.yaml } }] }
    paths:    { scenarios: demos/showcase/scenarios }

  site:
    platform: web
    app:      { url: "http://127.0.0.1:8787/index.html" }
    runsOn:   { browser: { engine: webkit }, emulate: iPhone 13 }
    driver:   { headless: false }
    services: { server: { cmd: "python -m http.server 8787", readyUrl: "http://127.0.0.1:8787/" } }

  showcase-android:
    platform: android
    app:    { id: com.example.showcase, grantPermissions: [android.permission.POST_NOTIFICATIONS] }
    runsOn: { avd: Pixel_8, apiLevel: ">=33" }
    driver: { nativeZ: true }
```

同じ`site`を現行の記法で書くと、次のようになります。

```yaml
  site:
    platform: web
    backend: [web]
    baseUrl: "http://127.0.0.1:8787/index.html"
    launchServer: { cmd: "python -m http.server 8787", readyUrl: "http://127.0.0.1:8787/" }
    browser: webkit
    deviceMode: "iPhone 13"
    headless: false
```

トップレベルの`notify`は、トップレベルのまま残します。target の`notify`はこれを上書きし、`[]`を書くとその target では通知しません。現在と同じ規則です。

### プラットフォームで形が変わるグループ

| グループ | iOS | Android | Web |
|---|---|---|---|
| `app` | `id`、`path`、`build`、`reinstall`、`launch.env`、`launch.args`、`launch.deeplink` | `id`、`path`、`build`、`reinstall`、`launch.env`、`launch.deeplink`、`grantPermissions` | `url`、`launch.env` |
| `app`（iOS、Android、Web で共通） | `startWhen`、`idNamespaces` | 同左 | 同左 |
| `runsOn` | `model`、`os`、`kind`、`locale`、`seedPhotos` | `avd`、`apiLevel` | `browser.engine`、`browser.version`、`emulate` |
| `driver` | `runner.testRunner`、`runner.build` | `nativeZ` | `headless` |
| `run`（追加分） | `tipKitHandling` | — | — |

どの配置も、現在のコードにある読み手に従っています。Android は`launchEnv`を intent の extra として読み、シナリオの`deeplink`を開きます。Web の Playwright のコード生成は`launchEnv`を読みます。Web の run 自体はこの値をどこにも渡さないので、Web の`launch.env`はコード生成のための入力です。`launchArgs`と`locale`を読むのは iOS だけです。`runsOn.locale`は、iOS で現在の組み込みのデフォルト`en_US`を引き継ぎます。[BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md)の Simulator の言語の固定が、この値に依存しているためです。

テスト用の`fake`プラットフォームは、最小限のモデルを登録します。`fake`の`app`は任意の`id`だけを持ち、`runsOn`と`driver`はフィールドを持ちません。そのため、`fake`の target には照合する条件がありません。`fake`の target は、現在と同じく iOS の形の`Effective`に解決されます。

`startWhen`は`readyWhen`を置き換えます。値は、`interrupts`の各項目が`condition`に書くのと同じ形の条件です。これで、「この要素が画面にある」を表す書き方が、設定全体で1つになります。たとえば`startWhen: { exists: { id: home.title } }`と書きます。run は最初のステップの前と、シナリオの途中でアプリを起動し直すたびに、条件が成り立つまで待ちます。この待ちは条件を繰り返し確かめるもので、固定の sleep は使いません。`startWhen`を省くと、現在の起動待ちの順序のままです。まず画面の遷移を、次に`idNamespaces`を、最後に要素数を見ます。この項目が受け付けるのは、`negate`を持たない`exists`だけです。現在の起動待ちの仕組みが評価できるのが、この条件だからです。待ちが時間切れになったときは、仕組みが「準備できていない」と報告した場合の現在の挙動と同じく、その回を続けます。

判断が分かれた置き場所は4つあります。

- **`locale`と`seedPhotos`は`runsOn`に置きます。** iOS では、`locale`は Simulator 自体のシステム言語を固定し、`seedPhotos`は写真ライブラリを埋めます。どちらも起動引数ではなく、端末の状態です。
- **Web のブラウザと`emulate`は`runsOn`に置きます。** Web の target にとって、ブラウザが「何の上で動くか」にあたります。`headless`は`driver`に置きます。表示の有無は Bajutsu がブラウザをどう見せるかを変えるだけで、何の上で動くかは変えません。
- **`secrets`は`run`に置きます。** シークレットは`${secrets.X}`として注入する入力であり、証跡で値を伏せるのはその役割から来る結果です。
- **`launchServer`は`services.server`になります。** 現在でも、起動待ちの URL があればどのプラットフォームでも動きます。そのため、Web のアプリではなく、run が起動する、または代わりを務める他のサーバーと同じ場所に置きます。

### setup と cleanup

`hooks`は`setup`、`cleanup`、`interrupts`を持ちます。`setup`と`cleanup`は`before`と`after`を置き換えるもので、target とシナリオの両方で同じ名前を使います。これで、2つのフェーズを両方の階層で同じ語で呼べます。report は、2つのフェーズを`setup`と`cleanup`と表示します。

`cleanup`は、現在の`after`の規則をそのまま引き継ぎます。各項目は`on`と手順の組で、`on`は`always`、`success`、`failure`のいずれかです。`failure`は`error`を置き換えます。`capturePolicy`の`result: error`という契機も`result: failure`に改め、失敗した結果を表す語を1つにそろえます。cleanup は、setup が失敗したとき、手順が失敗したとき、run がキャンセルされたときを含め、シナリオを抜けるどの経路でも走ります。cleanup の項目が1つ失敗しても残りは走り、その失敗は元の失敗の後ろに追記されます。現在と同じです。

hook のフェーズについては、2つの階層の順序を現在のまま保ちます。target の`setup`はシナリオの`setup`より先に走り、target の`interrupts`はシナリオのものより先に確かめます。シナリオの`cleanup`は target の`cleanup`より先に走ります。シナリオが自分で作ったものを解放してから、アプリ全体の後片付けがそれを包む順序です。

前置きのファイルは、コンポーネントとして`setup`に統合します。現在は、target の`setup`とシナリオの`preconditions.setup`がそれぞれ前置きのシナリオファイルを指し、その手順を`steps`に差し込みます。シナリオの値は target の値を置き換えます。この2つのキーはなくなります。

- target の hook の手順（`setup`、`cleanup`、`interrupts`）は、`use: { component: <file>, with: … }`でコンポーネントを呼べます。target の hook のコンポーネントは設定の読み込み時に解決するので、「target の hook は`use:`を拒否する」という現在の規則はなくなります。コンポーネントのパスは設定ファイルを基準に解決し、スイートのルートの中に収まっている必要があります。ルートは、Git から取り出した場合はそのチェックアウト、アップロードや合成の場合はバンドルのルート、ローカルのファイルの場合は設定ファイルのディレクトリです。ルートの外を指す参照は、現在の`contained_ref`と同じエラーで失敗します（[BE-0174](../BE-0174-scenario-ref-path-containment/BE-0174-scenario-ref-path-containment-ja.md)）。そのため、serve が信頼できないまま受け取った設定も、自分のツリーの外を読めません。こうした理由で、読み込みは設定の本文と一緒に、そのパスと suite のルートを受け取ります。`with`の値は読み込み時に置き換えます。`${secrets.*}`と`${vars.*}`は、これまでどおり手順を実行するときに解決します。`group:`と`setMocks`は、引き続き拒否します。
- シナリオは、現在と同じく、自分の`setup`の中で`use:`としてコンポーネントを呼びます。
- シナリオが宣言したすべての target が、自分の setup を出します。現在は主 target の前置きだけを差し込むので、複数 target のシナリオでは、ほかの target の前置きも走るようになります。この変化は意図したものです。target の setup は、その target を使うのに必要な準備だからです。
- target の setup を走らせたくないシナリオは、`preconditions.run.inheritSetup: false`を書きます。飛ばすのは、シナリオが宣言したすべての target の setup だけです。`targets`を宣言しないシナリオは、自分を走らせる target を宣言したものとみなします。target の cleanup はどの経路でも走るので、引き続き走ります。現在は、シナリオが target の前置きを差し替えつつ target の`before`を残せますが、この組み合わせはできなくなります。
- 前置きのファイルとコンポーネントのファイルは形式が違うので、各前置きのファイルを一度だけコンポーネントの形式に書き換えます。`group:`、`setMocks`、データ行の差し込みを使う前置きは、target の hook のコンポーネントにできません。それを必要とする各シナリオが、自分の`setup`の中で`use:`として呼びます。こうした前置きは作業単位2で洗い出します。
- 前置きの参照の基準が変わります。現在、target の`setup:`は各シナリオのファイルのディレクトリを基準に解決するので、1つの参照がディレクトリごとに別の前置きを指せます。コンポーネントのパスは、設定ファイルを基準に解決します。作業単位2でそうした target を洗い出し、該当するシナリオは、それぞれ自分の前置きを`use:`で呼ぶようにします。
- 前置きが走る順番が変わります。現在は`steps`に差し込まれるので、シナリオの`before`のあとに走ります。target の`setup`の末尾（target の旧`before`の手順のあと）に足すコンポーネントになると、シナリオの`setup`より前に走ります。シナリオ自身の前置きは、`use:`に変換してシナリオの`setup`の末尾に足すので、現在と同じく、そのシナリオの旧`before`の手順のあとに走ります。

シナリオは`setup`、`cleanup`、`interrupts`をトップレベルに、`steps`と並べて持ちます。target はこれらを`hooks`の下にまとめますが、フィールドの名前は両方の階層で同じです。

代償として、これまで`steps`に差し込まれて通常の手順として走っていた前置きの手順は、report の setup フェーズとして走ります。そこでの失敗は setup の失敗として数えます。

### プラットフォームのスキーマのレジストリ

各バックエンドは、`app`、`runsOn`、`driver`、`run`の追加分のモデルを、`bajutsu/common/config/schema/platform/`のレジストリに登録します。`TargetConfig`は`platform`を読んでレジストリを引き、登録されたモデルで4つのグループを検証します。どのモデルも未知のキーを禁じるため、他のプラットフォームのキーは未知のキーとして失敗します。Pydantic のエラーは未知のキーを示しますが、使えるキーは示しません。そこで`TargetConfig`がこのエラーを、モデルのフィールド一覧を含むメッセージに書き換えます。メッセージは作業単位3のテストで固定します。レジストリがあれば、設定の読み込みは Playwright や simctl を import せずに済みます。現在`deviceMode`を遅延解決できているのと同じ性質です。core はスキーマの中でプラットフォームを名指ししなくなるので、Flutter を足すときはエントリを1つ登録するだけになります。

`Config`は、各 target に`defaults`を重ねてから、登録済みのモデルで検証します。そのため、`app.id`のような必須のフィールドを`defaults.platforms.<platform>`から受け取れます。明示の`platform`が優先順位の連鎖に取って代わります。`backend`は廃止します。どのプラットフォームも現在 actuator は1つなので、順序つきのフォールバックのリストには、プラットフォームの中で選ぶ対象がありません。Linux のホストで`[ios, web]`が`playwright`に解決されるような、プラットフォームをまたぐフォールバックのリストは、意図してなくします。target は1つのプラットフォームを名指しし、Web も回したい run は2つ目の target を使います。`_effective_platform`、`_PLATFORM_IDENTIFIER`、`Config`の突き合わせも一緒に消えます。各`app`モデルが自分の識別子を必須にするためです。あるプラットフォームが2つ目の actuator を持った場合は、`driver`に`actuator`フィールドを足します。

`bajutsu config schema`は、レジストリから生成した JavaScript Object Notation（JSON）Schema を出力します。`platform`を`oneOf`の判別子にするので、エディタはプラットフォームごとにキーを補完できます。

### シナリオの preconditions

シナリオの`preconditions`は、target と同じグループ名を使います。これで、1つのフィールドを両方の階層で同じ書き方にできます。`preconditions`が持つグループは、`app`、`runsOn`、`run`の3つです。シナリオに書いたフィールドは、target の同じフィールドを上書きします。例外は現在の規則を引き継ぐ3つです。`launch.env`はキー単位で重ね、`launch.args`は target の引数の後ろにシナリオの引数を足します。`run.systemAlertHandling`の規則は連結し、シナリオの規則を target の規則より先に確かめます。

```yaml
preconditions:
  app:
    reinstall: overwrite
    launch: { env: { FEATURE_X: "1" }, args: ["-debug"], deeplink: "showcase://cart" }
  runsOn:
    ios: { model: iPhone 16, os: ">=18", locale: ja_JP, seedPhotos: [photos/cat.jpg] }
  run: { erase: true, tipKitHandling: true }
```

| 現在のシナリオのフィールド | 新しい位置 |
|---|---|
| `preconditions.launchEnv`、`launchArgs`、`deeplink` | `preconditions.app.launch.env`、`.args`、`.deeplink` |
| `preconditions.reinstall` | `preconditions.app.reinstall` |
| `preconditions.erase` | `preconditions.run.erase` |
| `preconditions.locale`、`seedPhotos` | `preconditions.runsOn.ios.locale`、`preconditions.runsOn.ios.seedPhotos` |
| `preconditions.setup` | 廃止（「setup と cleanup」を参照） |
| トップレベルの`systemAlertHandling`、`iosTipKitHandling` | `preconditions.run.systemAlertHandling`、`.tipKitHandling` |
| トップレベルの`before`、`after` | トップレベルの`setup`、`cleanup` |

シナリオのトップレベルの`network`は、今の場所に残します。これは report のタイムラインに載せる通信を絞り込む証跡の設定で、収集の有無を決める target の`run.network`とは別物です。

`preconditions.run`が受け付けるのは、`erase`、`systemAlertHandling`、`inheritSetup`、そして iOS では`tipKitHandling`です。`secrets`は受け付けません。`secrets`は target の階層で和集合をとるものとして残します。`tipKitHandling`のような iOS 専用のフィールドを、iOS の target を動かさないシナリオに書くと、動かす target のどれもが持たない`app`のフィールドと同じ規則で、端末を操作する前に失敗します。

プラットフォーム名で分けるのは`runsOn`だけです。`runsOn`のフィールドはプラットフォームごとに違うためです。`app.launch`のフィールドは、対応するどのプラットフォームでも意味が同じなので、`app`は分けません。シナリオの各 target は、自分のプラットフォームが持つ`app`のフィールドを当てはめます。そのため、iOS と Web をまたぐシナリオでも、iOS 側の`launch.args`を書けます。シナリオが動かすどの target もそのフィールドを持たない場合（Web だけのシナリオでの`launch.args`や、Web の target だけに対する`reinstall`など）は、端末を操作する前に失敗し、そのフィールドを示します。黙って無視することはありません。これは、走らせないプラットフォームのブロックが効かないだけの`runsOn`とは、意図して違えています。`runsOn`のブロックはプラットフォーム名で分けるので、どのプラットフォーム向けかを自分で示します。一方、分けない`app`や`run`のフィールドは、シナリオが動かすすべての target に向けたものと読めるためです。この違いの代償は受け入れます。単一プラットフォームの複数の target が`paths.scenarios`で同じシナリオファイルを拾う場合、たとえば Android の target も読むディレクトリに`tipKitHandling`を持つ iOS のシナリオがある場合は、そのフィールドを持たない target で失敗します。今は、その target がフィールドを無視しています。そうしたシナリオは`targets`を書くか、合う target だけが読むディレクトリへ移します。アプリを識別したりビルドしたりするフィールド（`id`、`path`、`build`、`startWhen`、`idNamespaces`など）は、シナリオでは書けません。これらは target が持ちます。

`seedPhotos`のパスは、それを書いたファイルを基準に解決します。シナリオに書けばシナリオのファイル、target に書けば設定ファイルが基準です。写真を入れるには消去した端末が引き続き必要なので、run は重ね合わせたあとの有効な`run.erase`が true であることを確かめます。target の`erase: true`でも条件を満たします。

### シナリオ自身の`runsOn`

シナリオは、自分の条件を`preconditions.runsOn`の下に、プラットフォームごとに書きます。各ブロックは、プラットフォームの登録済みの`runsOn`モデルのうち、シナリオで書けるフィールド（後述）に限って検証するため、フィールド名の書き間違いは読み込み時に失敗します。

```yaml
# iPad にしかない画面のシナリオ
preconditions:
  runsOn:
    ios: { model: "iPad Pro 13-inch (M4)", os: ">=18" }
```

```yaml
# iOS の target と Android の target の両方で回すシナリオ
preconditions:
  runsOn:
    ios:     { os: ">=18" }
    android: { apiLevel: ">=34" }
```

```yaml
# 複数 target のシナリオ（showcase は iOS、site は Web）
targets: [showcase, site]
preconditions:
  runsOn:
    ios: { os: ">=18" }
    web: { browser: { version: ">=120" } }
```

シナリオが動かす target ごとに、有効な`runsOn`は target の`runsOn`から始まります。そこへ、その target のプラットフォームに対応するシナリオのブロックを、フィールド単位で上書きします。つまりシナリオの値が勝ちます。シナリオは target の値を広げることもできます。たとえば target の`>=17`に対して、シナリオで`os: ">=16"`と書けます。これは、シナリオを優先するという規則の帰結です。run が動かさないプラットフォームのブロックは効果を持たないので、1つのシナリオで複数のプラットフォームに対応できます。同じプラットフォームの target 2つを別の端末で動かす場合、両者は同じブロックを共有し、それぞれが端末群から自分の端末をとります。ある種類の端末が手元の台数より多く要る組み合わせは、端末がない場合と同じく失敗します。2つの target に別々の条件が要るなら、シナリオを2つに分けます。

シナリオのブロックが受け付けるのは、条件のフィールド（`model`、`os`、`avd`、`apiLevel`、`browser.engine`、`browser.version`）と、iOS での`locale`と`seedPhotos`です。`kind`と`emulate`は target に残します。[BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation-ja.md)は、端末モードを target をどう駆動するかの性質としています。両方の見た目が要るシナリオは、2つの target で回します。

### 端末との照合

`runsOn`は、シナリオを実施する環境を表します。端末を作ることはありません。例外は1つだけで、実行中に消えた Simulator の置き換えです。置き換えは、消えた端末の機種と runtime を引き継ぎます（「defaults」を参照）。Bajutsu は手元の端末を1台ずつ読みます。手元の端末とは、`--udid`の端末群の各 udid、Android では`--udid`の端末群の各シリアル、Web のレーンが起動したブラウザです。デフォルトの`--udid booted`は simctl が解決する1台の Simulator を指すので、端末群は1台です。URL を udid として渡すことは、今後は受け付けません。「どこで実行するか」で述べる`appium`の environment が、この経路を置き換えます。端末群にある Android の実機は、エミュレータと同じく照合します。API レベルは`getprop`で読み、`avd`とは一致しません。

| 条件 | 読み取り元 | 比較 |
|---|---|---|
| iOS の`runsOn.os` | Simulator の runtime ラベルを`DeviceOS`で解析した値 | 版の範囲 |
| iOS の`runsOn.model` | その udid の simctl のデバイスタイプ名 | 完全一致 |
| Android の`runsOn.apiLevel` | `ro.build.version.sdk` | 整数に対する版の範囲 |
| Android の`runsOn.avd` | エミュレータの Android Virtual Device（AVD）名 | 完全一致。実機は一致しない |
| Web の`runsOn.browser.version` | Playwright の`browser.version` | 版の範囲 |

照合の対象は、invocation が自分のホストで動かす端末です。`runsOn.kind`が`device`の target（iPhone の実機）や、device cloud や Appium のグリッドへ送る run には、実行前に読める端末群がありません。そうした target は、`model`、`os`、リストを書けません。`kind: device`の場合は、defaults を重ねたあとでローダーが拒否します。有効な`runsOn`が条件を持つシナリオ（条件は target 由来でもシナリオ由来でもかまいません）をそうした target やリモートの環境で走らせると、端末を操作する前に失敗し、そのフィールドを示します。黙って無視することはありません。この検査は target ごとに行います。グリッドの target とローカルの target が混ざるシナリオでは、グリッドの target の有効な`runsOn`だけを見て、ローカルの target の条件はいつもどおり照合します。リモートの環境では、この検査をジョブを出す場所で行います。BE-0448 の worker が、何かを投入する前にそうしたシナリオを失敗させるので、Device Farm のホスト上の素の`bajutsu run`は、自分がどこで動いているかを知る必要がありません。`locale`と`seedPhotos`は条件ではなく端末の状態なので、そこでも書けます。確保したリモートの端末から事実を読み取ることは、後続の項目に残します。それまでは、条件を取り入れた一式は Device Farm や iPhone の実機では走らせられません。この項目は、これを代償として受け入れます。

版の範囲は、Bajutsu 独自の記法ではなく、npm の[node-semver の範囲の記法](https://github.com/npm/node-semver#ranges)をすべて採用します。多くのチームがすでに書いている記法であり、すべてを採用すれば、Bajutsu 固有に説明すべきことが残りません。

| 形 | 例 | 意味 |
|---|---|---|
| 比較子（空白区切りで「かつ」） | `>=17 <19` | 17.0以上、19未満 |
| x の範囲、または一部だけの版 | `18`、`18.x` | 18.x のどのリリースか |
| ハイフンの範囲 | `33 - 35` | 33から35まで（両端を含む） |
| キャレット | `^17` | 17以上、18未満 |
| チルダ | `~17.4` | 17.4以上、17.5未満 |
| 和 | `17 \|\| 19` | 17または19 |

版はすべて先頭3成分で比べます。短い版は0で補います。iOS の runtime ラベルは`18.2`のようにメジャーとマイナーだけを持つので、パッチは0として読みます。API レベル`34`は`34.0.0`として読みます。Chrome の`130.0.6723.31`のような長い版は、4成分目以降を捨てます。プレリリースのタグは使いません。タグを含む範囲は読み込み時に拒否し、ベータ版の runtime は数字だけで比べます。範囲の解析、版の比較、範囲に含まれるメジャーバージョンの列挙は、新しい`bajutsu/common/devices/version.py`の`VersionSpec`が担います。作業単位1では、この記法を保守されている Python の実装で採用するか、自前で実装し、npm 自身の例をテストで固定します。`DeviceOS`は意図して比較演算子を持たないままにします。この項目は宣言で端末を選ぶだけで、OS ごとの分岐を足さないためです。

### 回とその結果

以下では、マトリクスの1マス（1組の座標でのシナリオ）を「回」と呼び、コマンドの実行全体を「invocation」と呼びます。シナリオの有効な`runsOn`が、何回走るかと、端末が見つからない回をどう扱うかを決めます。回数を増やすフィールドは2種類で、それ以外は回数を増やしません。

| フィールド | 回 | 引き受けられる端末が手元にない回 |
|---|---|---|
| `model`（iOS）、`avd`（Android）、`browser.engine`（Web）のリスト | 列挙した値ごとに1回 | その値を示して**失敗** |
| `os`（iOS）や`apiLevel`（Android）の、上限と下限の両方がある範囲（`>=17 <19`、`18`、`^17`、`33 - 35`など） | 範囲に含まれるメジャーバージョンごとに1回。`>=17 <19`なら17と18 | そのメジャーバージョンを示して**失敗** |
| 片側が開いた範囲（`>=18`、`<19`、`<=17.4`など） | 手元の端末が持つ、範囲内のメジャーバージョンごとに1回。機種のリストと一緒なら、列挙した値ごとに、その機種の端末から求める。手元に1台もない機種は失敗 | 範囲に入る端末がなければ、**対象外**の記録を1つ |
| 和（`17 \|\| >=19`など） | 両側をそれぞれの規則で扱い、メジャーバージョンを合わせる | 両側の規則に従う |
| 単一の`model`や`avd`（単独でも、範囲と一緒でも） | 回を増やさない | その機種の端末がなければ**失敗**。対象外になり得るのは、回の版の部分だけ |
| 条件がまったくない | 1回 | 現在と同じく、端末群のどの端末でも走れる。後述の候補の集合は当てはめない |

Android では、API レベル1つを1つのメジャーバージョンとして数えます。Web の`browser.version`の範囲は回数を増やしません。Playwright はエンジンごとに1つの版しか持たないためです。この範囲は絞り込みとして働き、結果の扱いは範囲の形に応じて上の規則に従います。両側に境界のある範囲から外れた回は、範囲と起動した版を示して1度だけ失敗します。エンジンごとに版の付け方が違うので、`browser.version`の範囲とエンジンのリストを一緒に書くと、読み込み時に失敗します。`--browsers`によって実行時に同じ組み合わせができた場合は、端末を操作する前に終了コード2で失敗します。Web のバックエンドは足りないエンジンをその場で入れるので、エンジンのリストで端末が足りないことはありません。複数 target のシナリオでは、各 target の回の組み合わせをすべて走らせます。`>=17 <19 || >=18`のように両側が重なる和では、各メジャーバージョンを1回だけ走らせ、境界のある側が含むメジャーバージョンは「必ず走らせる」扱いを保ちます。組み合わせは、いずれかの target の回が失敗すれば失敗とし、失敗がなくいずれかが対象外なら対象外とします。`*`や`>=0`のようにすべてに一致する範囲は、端末群が持つメジャーバージョンごとに1回走ります。

```yaml
preconditions:
  runsOn:
    ios:
      model: ["iPhone SE (3rd generation)", "iPad Pro 13-inch (M4)"]
      os: ">=17 <19"
```

このブロックは4回走ります。2つの機種それぞれで、iOS 17 と iOS 18 の回です。report には、これらの回とシナリオの結果表が出ます。`--browsers`がすでに出している表と同じ形です（[BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines-ja.md)）。

```
                  SE / iOS 17   SE / iOS 18   iPad Pro / iOS 17   iPad Pro / iOS 18
login             pass          pass          pass                pass
checkout          fail          fail          pass                pass
```

失敗と対象外の分かれ目は、書き手が何を求めたかに従います。リストや両側に境界のある範囲は、必ず走らせたい回を名指しします。そのため、端末が手元にない場合（多くは機種名の書き間違い）は、カバレッジを黙って減らさずに失敗させます。`>=18`のような片側が開いた範囲は、シナリオを実施する場所を表します。そのため、iOS 18 の端末がない端末群では、そのシナリオは対象外になります。target に範囲を書くと、その target の全シナリオの回が増えます。両側に境界のある範囲では、小さな端末群で引き受けられない回がすべて失敗します。片側が開いた範囲でも、端末群が持つメジャーバージョンの数だけ回が増えます。たとえば iOS 17 と iOS 18 の端末群で`os: ">=17"`と書くと、すべてのシナリオが2回ずつ走ります。シナリオごとに1回だけ走らせたい target は、`os: "18"`のようにメジャーバージョンを1つに絞るか、上の例のように`os`を書きません。

**各回が使う端末。** 条件を1つでも持つ回は、候補の集合から端末をとります。候補は次の3段階で決めます。

1. 回の条件を満たす手元の端末を残します。版の範囲があるときは、リリースが範囲に入り、かつその回のメジャーバージョンに属する端末を残します。
2. 回が`model`（iOS）や`avd`（Android）を決めていないときは、端末群の並び順で、この候補を使う target（1つの組み合わせの中で、回の座標が等しい同じプラットフォームの target）の数以上の台数がまだ残っている最初の機種の端末だけを残します。機種を決めている場合も、同じ台数を満たす必要があります。
3. その台数をまだ保てる中で、最も新しいリリースの端末だけを残します。

ここでいう端末の「機種」は、iOS では simctl のデバイスタイプ名です。Android では、エミュレータは AVD 名、AVD を持たない実機は`ro.product.model`です。端末の「リリース」は、iOS では runtime ラベル、Android では API レベルです。Web のレーンのエンジンと版は invocation が決めるので、手順2と3は Web のレーンを絞り込みません。

いずれかの段階で台数が target の数を下回ったときは、端末がない場合と同じく、その回は失敗します。

これで候補はすべて同じ機種、同じリリースになります。そのため、空いている候補ならどれでもその回を引き受けられ、`--workers`は、回が観測する内容を変えずに、同じ端末へ回を分散できます。[BE-0447](../BE-0447-install-app-step/BE-0447-install-app-step-ja.md)のデバイスグループ（メンバーの target が1台の端末を共有するもの）は、1つの単位として回を作ります。メンバーの有効な条件は一致している必要があり、食い違えばシナリオの読み込み時に失敗します。同じ actuator の target は、現在と同じく1つの端末群を共有します。`--browser`と`--browsers`は、1回の実行に限って Web のリストを上書きします。フラグはシナリオより優先され、シナリオは target より優先されます。

**対象外。** 対象外の記録は、成功でも失敗でもありません。理由には、満たせなかった条件と、回が見た端末が示されます。

```
not applicable: showcase/ipad-split-view
  runsOn.ios.os  ">=18"  available: iOS 17.5 "iPhone 15" (5A3F...), iOS 17.5 "iPhone 16" (7B21...)
```

report では対象外を別に並べ、flakiness の履歴からは除きます。すべての記録が対象外で1回も走らなかったときは、「no run executed」として、失敗した invocation と同じ終了コード1で終了します。空の invocation が緑になることはありません。失敗（事前検査や capability の検査での失敗を含む）は、もともと0以外の終了コードで終わります。通常は1で、BE-0450 の capability の検査で走らせられるシナリオが1つも残らないときは2です。シナリオを1つも選ばない絞り込みは、現在の挙動のままです。すべての回が対象外になった serve のジョブも、同じ理由で失敗したジョブとして報告します。列挙した値に端末がない回を失敗させることと合わせて、これが[BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md)の主な懸念に応えます。設定を誤った端末群で一式を飛ばしても、invocation 全体が緑になることはありません。ただし、ある OS の版を持たない端末群では、一部のシナリオが対象外になり、残りが成功することはあり得ます。そのため、要約の行には、成功と失敗の件数と並べて対象外の件数を出します。

**履歴とほかのコマンド。** 現在、flakiness の履歴は、判定をシナリオの内容の指紋と端末の OS の組で記録しています。新しい組には、target と回の座標（target ごとの列挙した値とメジャーバージョン）が加わります。そのため、ある機種でのレイアウトの失敗が、不安定なシナリオとして数えられることはありません。これまでの履歴は座標を持たないので、新しい組で記録し直します。run のマニフェストには、各回の座標と、観測した OS を記録します。`record`、`crawl`、`repl`は回数を増やしません。これらは最初の回の座標、つまり最初に列挙した値と、範囲の中で端末群が持つ最も新しいメジャーバージョンを使い、合う端末がなければ失敗します。コード生成は端末の条件を出力しません。生成したテストは起動された場所で走ります。このことはコード生成の文書に書きます。`bajutsu doctor`は、シナリオごとに、その回と、各回に合う手元の端末を一覧にします。読める端末群がないときは、宣言された回だけを報告し、片側が開いた範囲は「手元のメジャーバージョンごとに1回」と示します。その回は端末群によって決まるためです。

比較、回数、候補の集合は決定的で、モデルの呼び出しを含みません。合う端末をその場で作ることは範囲外です（「検討した代替案」を参照）。

### どこで実行するか

target は、どこで実行するかを持たなくなります。target が書くのは、何をテストし、何の上で動かし、Bajutsu がどう駆動するかです。どこで実行するかはマシンの性質であり、知っているのはマシンの運用者です。[BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md)も、`worker.yaml`を`bajutsu.config.yaml`に入れない理由として、同じ線を引いています。そのため、現在のキーのうち4つを target から外します。

| 旧キー | 移る先 | 理由 |
|---|---|---|
| `cloudBatchBudget` | `worker.yaml`の`maxJobConcurrency` | [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch-ja.md)が置き換えます。端末を確保する worker が予算を数えるためです |
| `cloudBatch` | serve の fan-out の要求（`run-set`）の`environment` | BE-0448 では同じ target がローカルでも Device Farm でも走るため、送り先は target の性質ではなく実行ごとの選択です |
| `deviceProvider` | `worker.yaml`の`appium`という environment と、グリッドの`endpoint` | どのグリッドが端末を出すかは、device cloud と同じくインフラの選択です |
| `requires` | 廃止 | BE-0450 は、自由記述の振り分けタグを、worker が在庫として持つものに置き換えます |

この項目のスキーマの外にも、3つの変更が要ります。作業単位4が、BE-0448 と BE-0450 と調整しながら、スキーマの切り替えと一緒に入れます。serve の2つのエンドポイントが`environment`を受け取り、BE-0448 の`environment:<name>`による振り分けにつなぎます。fan-out の要求（`run-set`）は今の役割のままで、batch provider の種類だけを受け付け、`appium`や値の省略は 400 の応答で拒否します。device cloud のためにアプリをまとめる要求だからです。通常の run の要求には、`appium`だけを受け付ける任意の`environment`を足します。そこから作るジョブは`environment:appium`を求め、`environment`のない要求は今と同じくローカルで走ります。現在、Device Farm に送るコマンドラインのコマンドはないので、CLI のオプションは足しません。ほかの値は 400 の応答で拒否します。`worker.yaml`は、`appium`を`endpoint`つきの environment として受け付けます。これは BE-0450 の environment の語彙を、batch provider の外へ広げる変更です。`appium`の environment は、グリッドが受け持つプラットフォームを書きます（`platform: ios`。現在の値はこれだけです）。そのプラットフォームの target はグリッドへ行き、同じシナリオのほかの target はローカルに残ります。`worker.yaml`の`appium`の environment は、device cloud のものと同じく振る舞います。`maxJobConcurrency`は1を超えてもよく、`drivers`にはローカルの target が使うドライバとグリッドのドライバを並べ、BE-0450 のホストの規則はローカルの target にだけ当てはめます。今の URL の udid と同じく endpoint は1台の端末として数えるので、グリッドの target を2つ以上持つシナリオは読み込み時に拒否します。1つの endpoint が同時に開けるセッションの数の決め方は、後続の項目に回します。端末を動かすコマンド（`run`、`record`、`crawl`、`repl`、`audit`、`doctor`）と、起動時の Model Context Protocol（MCP）のサーバーはどれも`--worker-config`でこの environment を受け付けるので、今の URL の udid と同じく、どれからもグリッドに届きます。`triage --rerun`は、今`--udid`を渡しているのと同じく、起動する`run`に`--worker-config`を渡します。`run`以外のコマンドも、`run`と同じ`acquire_device`を通じて`Effective.device_provider`を udid の指定に変え、`run`と同じく BE-0450 のドライバとホストについての capability の検査をします。この項目は、これらのコマンドでほかのローカル以外の environment をすべて拒否します。hosted のジョブは`environment:appium`だけで振り分けるので、グリッドの target とローカルの target が混ざるシナリオは、ジョブを出す時点で拒否します。そうしたシナリオは、両方を持つマシンで`--worker-config`を使って走らせます。`appium`の worker は`environment:appium`だけを広告し、それを求めるジョブは`platform:*`や`host:*`のトークンを持ちません。Device Farm のジョブと同じく、端末とそのホストはグリッドのものだからです。worker はそうしたジョブを BE-0448 のスロットの仕組みで走らせます。ただしスロットは、Device Farm に投入する代わりに`--worker-config`をつけたローカルの`bajutsu run`を起動し、同じ経路で結果を返します。

`requires`を取り除くことには代償があり、この項目はそれを受け入れます。BE-0450 は、後続の項目が振り分けのための iOS のランタイムと端末の種類の要件を導くまで、`requires`を残します。この項目は新しいスキーマとともに`requires`を取り除くので、導出が入るまで、hosted のジョブは iOS のランタイムや端末の種類を要件にできません。導出が読むのは、各シナリオの有効な`runsOn`、つまり target とシナリオの`os`と`model`を合わせたものです。`>=17 <19`のような範囲は、現在の振り分けが使う「すべてを含む」タグの照合では表せません。そのため導出は、その後続の項目に残します。

### defaults

`defaults`には、target のキーのうち`platform`と`notify`以外を書きます。`notify`はトップレベルに残します。共通のグループは`defaults`の直下に書きます。プラットフォームで形が変わるグループ（`app`、`runsOn`、`driver`と、`run`の iOS の項目）は`defaults.platforms.<platform>`の下に書き、そのプラットフォームの target にだけ重なります。これで1つのファイルに複数のプラットフォームのデフォルトを同時に持てます。辞書はキー単位で重ね、target の値が勝ちます。target は`null`を書いて、継承した値を消せます。実機の target は、この方法でチーム共通の`model`のデフォルトを消します。リストは置き換えます。例外は、現行どおり和集合をとる`evidence.redact`と`run.secrets`の2つです。`run.secrets`を置き換えにすると、シークレットを1つ足した target で、チーム共通のシークレットがすべて伏せられなくなってしまいます。`ai`は現行どおりフィールド単位で重ねます。`evidence.capture`と`app.reinstall`は、target の階層でも書けるようになります。現在、`capture`は`defaults`だけに、`reinstall`はシナリオだけにあります。シナリオの`app.reinstall`は書かない限り未設定で、組み込みの`clean`は最後に当てはめるので、target の値が効きます。

組み込みのデフォルト`device: "iPhone 15"`は廃止します。`runsOn.model`のデフォルトとして残すと、`defaults`を書かない全設定が気づかないうちにこの値を要件として課すことになります。置き換えの Simulator は、消えた端末自身のデバイスタイプと runtime を引き継ぎます。これは現在の第一の選択肢です。現在のそれ以降のフォールバック（設定の`device`と、最新の iPhone）はなくします。回が観測する機種を変えてしまうおそれがあるためです。runtime を固定せずに作り直す現在の処理も、照合した回が見る OS を変え得るので、なくします。デバイスタイプと runtime を引き継げない置き換えは、その回を失敗させます。組み込みの`locale: en_US`は、iOS の`runsOn.locale`として残します。

### 旧キーの移行先

| 旧キー | 新しい位置 |
|---|---|
| `platform` | `platform`。すべての target で必須になる |
| `backend`、`defaults.platform` | 廃止。`platform`が actuator を決める |
| `bundleId`、`package` / `baseUrl` | `app.id` / `app.url` |
| `appPath`、`build`、`launchEnv`、`launchArgs` | `app.path`、`app.build`、`app.launch.env`、`app.launch.args` |
| `deeplinkScheme` | 廃止。現在このフィールドを読むコードはない |
| `readyWhen`、`idNamespaces`、`grantPermissions` | `app.startWhen`（selector は`exists`の下へ移る）、`app.idNamespaces`、`app.grantPermissions` |
| `device` | `runsOn.model`。意味が、置き換えの Simulator のためのヒントから、すべての回が満たすべき条件に変わる |
| `locale`、`xcuitest.deviceType` | `runsOn.locale`、`runsOn.kind` |
| `browser`、`deviceMode` | `runsOn.browser.engine`、`runsOn.emulate`（省略で desktop） |
| `headless`、`nativeZ`、`xcuitest.testRunner`、`xcuitest.build` | `driver.headless`、`driver.nativeZ`、`driver.runner.*` |
| `launchServer`、`mockServer`、`mailbox` | `services.server`、`services.mockServer`、`services.mailbox` |
| `deviceProvider`、`cloudBatch`、`cloudBatchBudget`、`requires` | target から外す（「どこで実行するか」を参照） |
| `erase`、`network`、`visualCompare`、`secrets`、`systemAlertHandling`、`iosTipKitHandling` | `run.*`（最後は`run.tipKitHandling`） |
| `setup`、`before`、`after`、`interrupts` | `hooks.setup`（前置きは`use:`で呼ぶコンポーネントになる）、`hooks.setup`、`hooks.cleanup`（`on: error`は`on: failure`になる）、`hooks.interrupts` |
| `capture`、`redact` | `evidence.*` |
| `scenarios`、`baselines`、`schemas`、`goldens` | `paths.*` |
| `defaults.device`、`defaults.locale` | `defaults.platforms.ios.runsOn.model`、`defaults.platforms.ios.runsOn.locale` |
| `ai`、`notify` | 変更なし |
| `defaults.reservedNamespaces`、`defaults.doctor` | トップレベルの`reservedNamespaces`と`doctor`。どちらも target ごとではなくチーム全体の値 |
| シナリオのフィールド | 「シナリオの preconditions」を参照 |

解決後の`Effective`は属性名を変えません。例外は、元のキーがなくなる属性です。`device`、`cloud_batch`、`cloud_batch_budget`、`requires`、`setup`、`ready_when`、`IosConfig.deeplink_scheme`がこれにあたります。起動待ちは、`ready_when`の代わりに、`startWhen`の`exists`の下の selector を読みます。これらを読む箇所はキーと一緒に移り、`serve/helpers.py`は代わりに要求の`environment`を読みます。`Effective.device_provider`は残します。`appium`の environment を持つ`--worker-config`が、グリッドのプラットフォームの target についてこの属性を埋めるので、`acquire_device`は引き続き endpoint を udid の指定として run に渡します。URL を受け付けなくなるのはコマンドラインの`--udid`だけで、内部では、endpoint は現在と同じく live のドライバへ振り分けられます。`Effective.backend`も、`platform`から導いた1要素のリストとして残します。これを読む箇所（`provision`、`serve/operations/doctor.py`、actuator の選択）は、そのまま動きます。属性名を変えると呼び出し箇所が数百に及び、しかも設定の形とは別に進められます。そのため`resolve`が、新しい辞書から現在の`Effective`を組み立てます。target の階層で新しく書けるフィールドは、現在の名前に置き場所がないので、`Effective`に属性を足します。`startWhen`と target の`evidence.capture`は作業単位4で、`app.reinstall`、`app.launch.deeplink`、`runsOn.seedPhotos`は、シナリオとの重ね合わせと一緒に作業単位7で足します。作業単位7までは、この3つは未知のキーとして読み込みに失敗するので、書いたのに効かない状態にはなりません。`Effective`ではなく生のスキーマを読む箇所もいくつかあります（`serve/operations/reads.py`、`capture.py`、`enrich.py`、`doctor.py`、`config.py`、`codegen.py`、`serve/helpers.py`、`launchEnv`を同梱する Device Farm の batch provider、`common/report/rows.py`、`templates/serve.html.j2`、`analysis/impact`、`cloudBatch`と`cloudBatchBudget`を読んで報告する`serve/operations/dispatch.py`、`Effective`を直接組み立てる`triage/cli.py`など）。作業単位4でこれらも更新します。

### コマンドラインのフラグ

- `--backend`は検査になります。受け付けるのは、target の`platform`、そのプラットフォームの actuator の名前（`xcuitest`など）、`fake`です。それ以外の値やカンマ区切りの並びを渡すと、終了コード2で終わります。順序つきのフォールバックのリストと、`backends.py`のコスト順の actuator の選択は、`backend`とともになくなります。`fake`は上書きとして引き続き認めるので、どの target も fake のドライバで走らせられます。fake のドライバには読む端末がないので、`fake`のときは条件を評価せず、回も増やしません。invocation はそのことを1行知らせます。条件を飛ばすのは、ここだけです。target を動かすコマンド（`record`、`crawl`、`repl`、`triage --rerun`、`audit`、MCP のツール）と、serve の要求本文の`backend`にも、同じ規則を当てはめます。サーバーの構成を選ぶ`serve --backend`と、依存の導入のために backend を指定する`provision --backend`は、target を名指ししないので、今の意味のままです。そのため、target が複数のプラットフォームにまたがるシナリオでは、`--backend`として受け付けるのは`fake`だけになります。これは意図した挙動です。
- `--browser`、`--browsers`、`--headed`は、`runsOn.browser.engine`と`driver.headless`を上書きします。
- `--erase`、`--system-alert-handling`、`--ios-tipkit-handling`は名前を変えず、対応する`run`のフィールドを上書きします。フラグはシナリオより、シナリオは target より優先されます。
- `--scenarios`、`--baselines`、`--goldens`は、引き続き対応する`paths`のフィールドを上書きします。
- `--udid`は引き続き端末群を指定します。照合は、その中から端末を選びます。

### プライムディレクティブへの適合

- **AI は判定しない。** 照合、回数、候補の集合、結果は、設定、シナリオ、読み取った端末から決まる決定的な関数です。モデルの呼び出しは含みません。
- **決定性を優先する。** 条件を持つ回では、候補はすべて同じ機種、同じリリースなので、回がどの端末を使っても観測する内容は変わりません。条件をまったく持たないシナリオは、現在と同じく端末群のどの端末でも走れます。条件による待ちは残し、`startWhen`も条件を繰り返し確かめます。
- **アプリに依存しない。** アプリごとの違いは`targets.<name>`に置き、プラットフォームのレジストリによって core はプラットフォームの名前を持ちません。

### 範囲外

- 旧キーの読み替えと、移行コマンド。
- 1つの target が複数のプラットフォームにまたがること。この用途は、引き続き複数 target のシナリオで扱います。
- エントリポイントを通じた、外部パッケージからのプラットフォームの登録。
- 宣言した条件の run マニフェストへの記録。マニフェストには、代わりに各回の座標と観測した OS を記録します。
- Playwright が起動したもの以外のブラウザのバージョンを選ぶこと。
- トップレベルの`orgs`と`ui`のブロック。この項目は変更しません。
- target でホスト OS を宣言すること。BE-0450 はホストをマシンから読み、各 driver が動けるホストを宣言し、`host:<os>`で振り分けます。target でも宣言すると、同じ事実を書く場所が2つになります。
- iPhone の実機やリモートの環境を、`runsOn`の条件で照合すること。
- `runsOn`から振り分けの要件を導くこと（「どこで実行するか」を参照）。

### 作業の分解

1. **`VersionSpec`。** `bajutsu/common/devices/version.py`で、npm の範囲の記法を解析し、版を先頭3成分で比べ、プレリリースのタグを拒否し、範囲に含まれるメジャーバージョンを列挙します。
2. **未確定の事実の確認。** AVD 名が対応する API レベル全体で読めるか（たとえば`ro.boot.qemu.avd_name`で）を確かめます。前置きが`steps`に差し込まれることに依存するシナリオとデモと、シナリオのディレクトリによって別のファイルに解決される target の`setup:`の参照を洗い出します。コードを入れる前に、表を更新します。
3. **プラットフォームのレジストリとモデル。** レジストリとプラットフォームごとのモデルを追加します。作業単位7で足す target の階層の3つのフィールド（`app.reinstall`、`app.launch.deeplink`、`runsOn.seedPhotos`）は、まだ含めません。未知のキーのエラーを使えるフィールドの一覧つきに書き換えます。レジストリのキーが`backends.PLATFORMS`と一致することをテストで固定します。
4. **スキーマの切り替え。** `TargetConfig`、`Defaults`、`Config`、`resolve`を置き換え、`redact`と`secrets`の和集合を保ち、生のスキーマを読む箇所を更新します。実行場所の移行も一緒に入れます。`deviceProvider`、`cloudBatch`、`cloudBatchBudget`、`requires`と、サーバー側の予算の仕組み（`deviceBudget`、`max_concurrent_batch`、`try_register(device_budget=…)`）を外し、serve の fan-out の要求（batch の種類）と通常の run の要求（`appium`）に`environment`を足して両方を振り分けに使い、`worker.yaml`と、`run`、`record`、`crawl`、`repl`、`audit`、`doctor`、MCP のサーバーの`--worker-config`で、`appium`の environment を受け付けます。target の hook のコンポーネントを解決し、前置きのファイルをコンポーネントの形式に書き換え、target の`hooks.setup`と`hooks.cleanup`を現在の`before`と`after`のフェーズに対応づけます。serve の要求本文を含め、「コマンドラインのフラグ」の`--backend`の規則も当てはめます。設定の読み込み関数が設定のパスと suite のルートを受け取るように変え、すべての呼び出し元（MCP のツール、`provision`、serve のアップロード、組織、合成、各操作、triage、coverage、`cli/_shared.py`など、約20か所）を更新します。target の hook のコンポーネントにできない target の前置きは、その target の`paths.scenarios`の下にある全シナリオの`before`に`use:`として配ります。シナリオの`preconditions.setup`もここで取り除き、各利用を、シナリオの`before`の末尾に足す`use:`に変換します。シナリオの`run`グループの最初のフィールドとして`preconditions.run.inheritSetup`を足し、シナリオが target の前置きを置き換えていた箇所では`false`にし、宣言したすべての target の旧`before`の手順を、それぞれ`target:`を付けてそのシナリオの`before`の先頭へ写します。`targets`を宣言しないシナリオには、その`paths.scenarios`にシナリオを含む target の手順を、`target:`を付けずに写します。そうしたシナリオは target を名指しできず、今の hook もそこでは印なしで走るためです。手順の違う複数の target がそのシナリオを含むときは、移行を止め、手で直すシナリオとして名前を示します。こうして、シナリオが現在の順序のままそれを保てるようにします。これで、この作業単位のあとに前置きのファイルを指すシナリオは残りません。`startWhen`と target の`evidence.capture`のために`Effective`の属性を足します。置き換えの Simulator の、設定の`device`と最新の iPhone へのフォールバックと、runtime を固定しない作り直しを外します。コマンドラインの`--udid`の URL の形を外し、`--worker-config`から`Effective.device_provider`を埋め、端末を動かすすべてのコマンドを`acquire_device`経由にし、runner の準備のために設定を読む`scripts/serve.sh`を更新し、`demos/showcase/live/showcase.live.config.yaml`を、target と`appium`の environment を持つ`worker.yaml`に分けます。作業単位5までは、`hooks.cleanup`は`on: error`のままです。テストの固定データ、`demos/`の設定ファイル9個、ルートの`bajutsu.config.yaml`を変換します。そこにある`device:`キーは、`model`が条件になったので何にも変換しません。作業単位9が入るまでは、`run`、`record`、`crawl`、`repl`、`audit`、MCP のツールの回と、`doctor`の端末を使う経路で、有効な`runsOn`が条件（`model`、`os`、`avd`、`apiLevel`、`browser.version`、リスト）を持つときは「not yet supported」で失敗させ、書いたのに効かない条件を作りません。条件を評価しない`--backend fake`のときは、この検査を飛ばします。`--browsers`が作るエンジンのリストは、現在の結果表の経路のまま、この検査の対象外とします。切り替えを分けると、読み込み側と全設定ファイルが同時に壊れるので、コミットの間でゲートが赤になります。
5. **setup と cleanup。** フェーズと、シナリオの`before`と`after`を`setup`と`cleanup`に改め、`on: error`と capturePolicy の`result: error`を`failure`に改め、それらを使うシナリオファイルとテストの固定データ（`demos/showcase/scenarios/before_after.yaml`など）を変換し、report のフェーズの表示を改めます。保存する`after_verdict`の値`error`を`failure`に改め、`report/load.py`は古い manifest の`error`を`failure`として読むので、以前の run も読み込めます。
6. **コマンドラインのフラグ。** `--browser`、`--browsers`、`--headed`を`runsOn.browser.engine`と`driver.headless`に、`--erase`、`--system-alert-handling`、`--ios-tipkit-handling`を`run`のフィールドに、`--scenarios`、`--baselines`、`--goldens`を`paths`に向けます。作業単位4は、変わらない`Effective`の名前を通じてこれらのフラグを動かし続けます。この作業単位は、フラグの解釈とヘルプの文面を新しいフィールドへ移します。これらを、`run`、`record`、`crawl`、`repl`、`triage --rerun`、`audit`、MCP のツールに当てはめます。
7. **シナリオの preconditions。** `preconditions`を`app`、`runsOn`、`run`のグループに組み直し、トップレベルの実行の方針のフィールドを`run`の下へ移し、`runsOn`をプラットフォームごとに分け、各グループを target の値の上に重ねます。target の階層の`app.reinstall`、`app.launch.deeplink`、`runsOn.seedPhotos`を、その`Effective`の属性と一緒に足し、シナリオの`reinstall`のデフォルトを未設定にし、重ね合わせた`seedPhotos`の消去の条件を確かめます。`app`のフィールドと iOS 専用の`run`のフィールドを、run の解決時に、動かす target のプラットフォームと照らします。シナリオを読む箇所（Device Farm の batch provider の`launchEnv`、`analysis/impact`の deeplink の読み取り、コード生成）を更新します。
8. **端末の読み取り。** 手元の各端末から、機種と OS（iOS）、API レベルと AVD 名（Android）、ブラウザのバージョン（Web）を読みます。`kind: device`とリモートの環境では、条件を拒否します。
9. **回と結果。** リストと範囲から回を作り、複数 target のシナリオとデバイスグループでは組み合わせ、各回をその候補の集合から割り当てます。端末のない回を失敗させ、片側が開いた範囲では対象外を記録し、1回も走らなかったときは終了コード1で終了します。各回の座標と結果を run のマニフェストと、hosted の runs のテーブルの新しい列（そのマイグレーションを含む）に保存し、flakiness の履歴をそれで記録します。作業単位10までは、対象外の記録はコンソールとマニフェストにだけ出し、ほかの出力では各回を別々の項目として示し、JUnit と CTRF のテスト名が重ならないように、シナリオ名に座標を付けた名前にします。そのうえで、`record`、`crawl`、`repl`には最初の回を渡し、`audit`と MCP のツールには`run`と同じく回を作らせ、`doctor`を除くすべてのコマンドで、作業単位4の「not yet supported」の検査を外します。`doctor`の端末を使う経路では、作業単位11までこの検査を残します。
10. **報告。** 回の結果表と対象外の状態を、HTML の report、JUnit と CTRF の出力、通知の本文、serve の Web UI に表示します。
11. **`doctor`。** シナリオごとに、その回と各回に合う端末を一覧にし、端末を使う経路に残っていた「not yet supported」の検査を外します。
12. **`bajutsu config schema`。** `config`というコマンドのグループを足し、その`schema`コマンドで、レジストリから設定の JSON Schema を出力します。あわせて、既存の`bajutsu schema`コマンドを更新します。シナリオのスキーマが、`preconditions`、`setup`、`cleanup`によって形を変えるためです。
13. **文書。** `docs/configuration.md`、`docs/scenarios.md`、`docs/drivers.md`、`docs/cli.md`、`docs/run-loop.md`、`docs/reporting.md`、`docs/evidence.md`、`docs/dsl-grammar.md`、`docs/codegen.md`、`docs/cookbook.md`、`docs/showcase.md`、`docs/devicefarm.md`、`docs/ios-device-cloud.md`、`docs/self-hosting.md`、`docs/ci.md`、`docs/recording.md`、`docs/selectors.md`、`docs/web-ui.md`、`docs/developer-guide.md`、`docs/architecture.md`、`DESIGN.md`、`docs/glossary.md`、`docs/getting-started/index.md`、`docs/getting-started/web.md`、`docs/api/scenario.md`、`docs/ai-development.md`と、それぞれの`docs/ja/`版、そして`README.md`、`README.ja.md`、`deploy/self-host/README.md`、デモの README を更新します。`DESIGN.md`では、`deeplinkScheme`の例も含みます。

## 検討した代替案

| 代替案 | 採らなかった理由 |
|---|---|
| flat な並びのまま、他プラットフォームのキーを拒否する | 書き間違いは捕まえられますが、`device`の名前の衝突が残り、条件の置き場所も増えず、1つの用途の設定は散らばったままです |
| プラットフォーム名をキーにする（`ios: {…}`などのうち、ちょうど1つ） | `targets.web.web:`のように同じ語が二重に入れ子になります。キー名が可変なので、JSON Schema では「ちょうど1つ」を別の規則で表す必要があります |
| `platform`と、プラットフォーム固有のキーをすべて入れる`configuration`を組にする | 用途ではなく、どのプラットフォームが使うかでキーをまとめるため、アプリ、端末、駆動方法の設定が1つのブロックに混ざり、入れ子も一段深くなります |
| `driver: { kind: xcuitest \| adb \| playwright }`の判別共用体 | 現在はどのプラットフォームも actuator が1つで、2層目を設けても得るものがありません。2つ目の actuator を持つプラットフォームが出たら見直します |
| core にプラットフォームのモデルの閉じた共用体を持たせる | バックエンドを足すたびに core のスキーマを編集することになり、バックエンドに依存しない設計と矛盾します |
| 全プラットフォームで共通の`runsOn`の形 | iOS のブラウザや Web の AVD のように、どのプラットフォームも使えないフィールドが残り、flat な並びの問題が再び生じます |
| `deviceProvider`と`cloudBatch`を target に残す | BE-0448 と BE-0450 が worker と実行の要求へ移す判断と重複し、どこで実行するかを target を書くチームに決めさせることになります |
| BE-0448 と BE-0450 より先に着手し、外すキーに仮の置き場所を用意する | 待たずに済みますが、すべての設定が一時的なスキーマを経由して二度移行することになります |
| target の hook の名前だけを変える | シナリオは同じフェーズを`before`と`after`のまま持つので、1つの概念が2つの名前を持つことになります |
| 前置きのキーと`setup`を両方残す | 現在の差し込みを保てますが、1つの用途を2つのキーが受け持ったままになります |
| target に、デフォルトの前置きファイルへの参照を残す | 設定でコンポーネントを解決せずに、シナリオごとの上書きを保てます。ただし、`setup`との重複が残ります |
| 端末が合わない回をすべて失敗させる | 複数の OS で同じ一式を回すと、片側が開いた条件で版を除外しているシナリオがすべて失敗します |
| 列挙した値に端末がない回を対象外として記録する | 機種名を書き間違えると、一式は緑のまま、カバレッジが黙って減ります |
| 合う端末をその場で作る | ランタイムの導入、時間、後片付けを Bajutsu が負います |
| 範囲は1台に絞り込み、1回だけ走らせる | 範囲を書く目的である、複数の OS の版を1度の実行で確かめることができません |
| 範囲に含まれるマイナーとパッチをすべて走らせる | 挙動がほとんど変わらないリリースの数だけ回が増えます。メジャーごとに1回なら、回数に上限があります |
| 列挙した`model`を「どれか1台」として扱う | 単一の値の絞り込みと意味はそろいますが、複数の画面サイズでテストするという、機種を列挙する目的を果たせません |
| 絞り込みのフィールドとは別に`matrix`キーを設ける | キーで意味を区別できますが、キーが1つ増えます。リストを受け付けるフィールドはプラットフォームごとに1つなので、曖昧さは生じません |
| 片側が開いた範囲も、含むメジャーバージョンごとに回を増やす | `<19`は0から18までのメジャーを含むことになり、ほとんどの回が失敗します |
| 回が機種を決めていないとき、候補に複数の機種が混ざるのを許す | どの空き端末が回を引き受けるかで、回が観測するレイアウトが変わります |
| この項目のうちに、リモートや実機の端末から事実を読み取る | 条件を書いても Device Farm や実機を使い続けられますが、提供元ごとに読み取りの経路が要ります。後続の項目で足せます |
| 各回を端末群の並び順で1台に固定する | udid まで決定的になりますが、同じメジャーの回がすべて同じ端末を待つため、`--workers`が直列になります |
| シナリオの条件を target 名で分ける | `targets`を宣言するかどうかでキーの形が変わります。また、iOS の target と Android の target の両方で回すシナリオでは、両方の条件を書けません |
| シナリオの条件のフィールドを平たく並べる | どのフィールドがどのプラットフォームに効くかがファイルから読めず、flat な設定の「書いたのに効かない」問題が戻ります |
| Bajutsu 独自の記法（比較子の一部だけ） | どの形が使えるかを利用者が覚える必要があります。npm の記法はすでに知られており、文書もそろっています |
| PEP 440 の指定子（`>=17,<19`、`==18.*`） | Python の`packaging`で扱え、4成分の版も比べられますが、アプリのチームにはなじみが薄く、「18系」は`==18.*`と書く必要があります |

## 進捗

> 作業の進行に合わせて更新してください。チェックリストは「詳細設計」の MECE な作業分解を
> 反映し（作業単位ごとに1つ）、ログは何がいつ変わったかを（古い順に）PR へのリンクつきで
> 記録します。

- [ ] 作業単位 1: `VersionSpec`
- [ ] 作業単位 2: 未確定の事実の確認
- [ ] 作業単位 3: プラットフォームのレジストリとモデル
- [ ] 作業単位 4: スキーマの切り替え、固定データ、`demos/`の設定ファイル
- [ ] 作業単位 5: setup と cleanup
- [ ] 作業単位 6: コマンドラインのフラグ
- [ ] 作業単位 7: シナリオの preconditions
- [ ] 作業単位 8: 端末の読み取り
- [ ] 作業単位 9: 回と結果
- [ ] 作業単位 10: 報告
- [ ] 作業単位 11: `doctor`
- [ ] 作業単位 12: `bajutsu config schema`
- [ ] 作業単位 13: 文書

### ログ

- まだ記録はありません。

## 参考

- [BE-0126](../BE-0126-per-platform-effective-config/BE-0126-per-platform-effective-config-ja.md)：解決後の`Effective`のプラットフォーム別の分割。
- [BE-0358](../BE-0358-device-os-as-a-first-class-fact/BE-0358-device-os-as-a-first-class-fact-ja.md)：`DeviceOS`と、記録される`device_runtime`。
- [BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation-ja.md)と[BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines-ja.md)：`runsOn`へ移る`deviceMode`と`browser`。
- [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks-ja.md)：`setup`と`cleanup`になる`before`と`after`のフェーズ。
- [BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction-ja.md)：target から外れ、`worker.yaml`の`appium`の environment へ移る`deviceProvider`。
- [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch-ja.md)と[BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md)：どこで実行するかを引き受ける、前提となる項目。
- `bajutsu/common/config/schema/target_config.py`と`bajutsu/common/config/resolve.py`：この項目が置き換えるスキーマと解決処理。

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
| 関連 | [BE-0126](../BE-0126-per-platform-effective-config/BE-0126-per-platform-effective-config-ja.md)、[BE-0358](../BE-0358-device-os-as-a-first-class-fact/BE-0358-device-os-as-a-first-class-fact-ja.md)、[BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation-ja.md)、[BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines-ja.md)、[BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks-ja.md)、[BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction-ja.md)、[BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch-ja.md)、[BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md) |
<!-- /BE-METADATA -->

## はじめに

現在、target の設定（`targets.<name>`）は、約50個のキーを1階層に並べています。`browser`と`deviceMode`は Web 専用、`nativeZ`は Android 専用、`bundleId`と`xcuitest`は iOS 専用のキーですが、これらも同じ階層に並んでいます。しかしスキーマは、キーとプラットフォームの対応を持っていません。また、target が想定する端末とオペレーティングシステム（OS）を書くキーもありません。

この項目では、1階層の並びを11個のキーに置き換えます。各キーが受け持つ用途は1つだけです。たとえば`app`はテスト対象のアプリを、`runsOn`は動かす端末を、`driver`は Bajutsu の駆動方法を受け持ちます。11個のうち`app`、`runsOn`、`driver`、`run`の4つは、`platform`の値によって形が変わります（`run`は iOS で1項目増えます）。残りの7つは、どのプラットフォームでも同じ形です。`runsOn`には、target を実施する環境（端末、OS、ブラウザ）を書きます。シナリオも`preconditions`の下に自分の`runsOn`を書け、シナリオの値が target の値より優先されます。Bajutsu は、手元の端末のうち、合わせた条件を満たす端末で各シナリオを走らせます。条件を満たす端末がないシナリオは、走らせずに「対象外」として記録します。後方互換は意図して持ちません。旧形式の設定は読み込み時に失敗し、エラーには受け付けなくなったキーの名前が出ます。

## 動機

target に別のプラットフォームのキーを書いても、設定はエラーなく読み込まれ、そのキーは何の効果も持ちません。たとえば iOS の target に`browser: firefox`と書くと、検証は通り、run はこの設定を無視します。[`docs/configuration.md`](../../docs/configuration.md)は、キーの表に「iOS ignores it」という注記を繰り返し書いて、この欠落を補っています。利用者は、キーが効くかどうかをこの表で調べるしかありません。

全プラットフォームが1つの名前空間を共有しているため、キーの名前にも無理が出ています。[`defaults.py`](../../bajutsu/common/config/schema/defaults.py)の`device`は iOS Simulator の機種名です。それなのに、デフォルト値として Web や Android の target にも重なります。Web のバックエンドの端末設定が`deviceMode`という別名になったのは、`device`という名前がすでに使われていたためです。

target を動かす環境を指定するキーもありません。iOS のバージョンは、`--udid`で選んだ Simulator によって決まります。Android の application programming interface（API）レベルは、adb のシリアルで選んだ端末によって決まります。`device`が効くのは、実行中に消えた Simulator の代わりを作る場面だけです。run は、実際に動いた OS を`device_runtime`として記録します（[BE-0358](../BE-0358-device-os-as-a-first-class-fact/BE-0358-device-os-as-a-first-class-fact-ja.md)）。そのため、シナリオが想定外の OS で落ちても、気づくのは実行後です。しかもその失敗は、flakiness の履歴に雑音として混ざります。

シナリオの側にも、どこで実施するかを書く手段がありません。たとえば、iOS 18 の機能を試すシナリオや、iPad にしかない画面のシナリオです。これらを他の端末で走らせないようにするには、現在は target を`showcase-iphone`と`showcase-ipad`に分けるか、シナリオの一覧を手で分けて管理するしかありません。iOS 17 と iOS 18 で同じ一式を回すときも、回すたびにシナリオを選び直すことになります。

同じ用途の設定が、複数のキーに散らばってもいます。アプリの起動に関わる設定は`launchEnv`、`launchArgs`、`readyWhen`の3つです。どこで実行するかに関わる設定は`deviceProvider`、`cloudBatch`、`cloudBatchBudget`、`requires`の4つです。しかも、どこで実行するかは target を書くチームではなく、マシンを運用する人が決めることです。全シナリオの前に走る手順は`setup`と`before`のどちらでも書けてしまい、文書は両者の違いを説明し続けています。プラットフォームの判定も複雑です。[`resolve.py`](../../bajutsu/common/config/resolve.py)の`_effective_platform`は、`platform`、`backend`、識別子の有無を優先順位の順に調べて、プラットフォームを推論します。Flutter のようなバックエンドを足すと、1階層のキーも推論の分岐も増えます。

この項目の実装後は、次の2点で効果を確かめられます。

- 別のプラットフォームのキーを書くと、設定の読み込み時に失敗します。エラーには、そのプラットフォームで使えるフィールドの一覧が出ます。
- iOS 17 と iOS 18 が混ざった端末群で一式を回すと、各シナリオは自分の`runsOn`が許す端末で走ります。許す端末がないシナリオは「対象外」と報告され、満たせなかった条件が示されます。

## 詳細設計

### 11個のキー

target は次のキーだけを受け付けます。

| キー | 用途 | 形が`platform`で変わるか |
|---|---|---|
| `platform` | どのバックエンドで動かすか | —（判別子） |
| `app` | 何をテストするか（識別子、入手と起動の方法、起動待ち、id の約束） | 変わる |
| `runsOn` | 何の上で動くか（端末と OS の要件、ブラウザ、言語） | 変わる |
| `driver` | Bajutsu がどう駆動するか | 変わる |
| `services` | アプリが話す代替サービスは何か | 変わらない |
| `run` | 実行の方針は何か | iOS だけ1項目増える |
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
      readyWhen: { id: home.title }
    runsOn:   { model: iPhone 15, os: ">=17 <19", kind: simulator, locale: en_US }
    driver:   { runner: { testRunner: build/Runner.xctestrun } }
    run:      { erase: true, secrets: [LOGIN_PASSWORD], tipKitHandling: true }
    hooks:    { before: [{ use: login }] }
    paths:    { scenarios: demos/showcase/scenarios }

  site:
    platform: web
    app:    { url: "http://127.0.0.1:8787/index.html" }
    runsOn: { browser: { engine: webkit, version: ">=18" }, emulate: iPhone 13 }
    driver: { headless: false }

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
    browser: webkit
    deviceMode: "iPhone 13"
    headless: false
```

### プラットフォームで形が変わるグループ

| グループ | iOS | Android | Web |
|---|---|---|---|
| `app` | `id`、`path`、`build`、`deeplink`、`launch.env`、`launch.args` | `id`、`path`、`build`、`grantPermissions` | `url`、`server` |
| `app`（全プラットフォーム共通） | `readyWhen`、`idNamespaces` | 同左 | 同左 |
| `runsOn` | `model`、`os`、`kind`、`locale` | `avd`、`apiLevel` | `browser.engine`、`browser.version`、`emulate` |
| `driver` | `runner.testRunner`、`runner.build` | `nativeZ` | `headless` |
| `run`（追加分） | `tipKitHandling` | — | — |

テスト用の`fake`プラットフォームは、最小限のモデルを登録します。`fake`の`app`は任意の`id`だけを持ち、`runsOn`と`driver`はフィールドを持ちません。そのため、`fake`の target には照合する要件がありません。`fake`の target は、現在と同じく iOS の形の`Effective`に解決されます。

判断が分かれた置き場所は4つあります。

- **`locale`は`runsOn`に置きます。** iOS では Simulator 自体のシステム言語を固定するため（[BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism-ja.md)）、起動引数ではなく端末の状態にあたります。
- **Web のブラウザと`emulate`は`runsOn`に置きます。** Web の target にとって、ブラウザが「何の上で動くか」にあたります。`headless`は`driver`に置きます。表示の有無は Bajutsu がブラウザをどう見せるかを変えるだけで、何の上で動くかは変えません。
- **`secrets`は`run`に置きます。** シークレットは`${secrets.X}`として注入する入力であり、証跡で値を伏せるのはその役割から来る結果です。
- **`setup`は廃止し、`hooks.before`に統合します。** 1つの用途を2つのキーが受け持つ重複こそ、この項目が取り除くものです。代償として、これまで`setup`がシナリオの`steps`に差し込んでいた手順は report の`before`フェーズとして走り、そこでの失敗は`before`の失敗として数えます。

### プラットフォームのスキーマのレジストリ

各バックエンドは、`app`、`runsOn`、`driver`、`run`の追加分のモデルを、`bajutsu/common/config/schema/platform/`のレジストリに登録します。`TargetConfig`は`platform`を読んでレジストリを引き、登録されたモデルで4つのグループを検証します。どのモデルも未知のキーを禁じるため、他のプラットフォームのキーは未知のキーとして失敗します。Pydantic のエラーは未知のキーを示しますが、使えるキーは示しません。そこで`TargetConfig`がこのエラーを、モデルのフィールド一覧を含むメッセージに書き換えます。メッセージは作業単位3のテストで固定します。レジストリがあれば、設定の読み込みは Playwright や simctl を import せずに済みます。現在`deviceMode`を遅延解決できているのと同じ性質です。core はスキーマの中でプラットフォームを名指ししなくなるので、Flutter を足すときはエントリを1つ登録するだけになります。

明示の`platform`が優先順位の連鎖に取って代わります。`backend`は廃止します。どのプラットフォームも現在 actuator は1つなので、順序つきのフォールバックのリストには選ぶ対象がありません。`_effective_platform`、`_PLATFORM_IDENTIFIER`、`Config`の突き合わせも一緒に消えます。各`app`モデルが自分の識別子を必須にするためです。あるプラットフォームが2つ目の actuator を持った場合は、`driver`に`actuator`フィールドを足します。

`bajutsu config schema`は、レジストリから生成した JavaScript Object Notation（JSON）Schema を出力します。`platform`を`oneOf`の判別子にするので、エディタはプラットフォームごとにキーを補完できます。

### シナリオ自身の`runsOn`

シナリオは、自分の条件を`preconditions.runsOn`の下に、プラットフォームごとに書きます。各ブロックは target と同じ登録済みの`runsOn`モデルで検証するため、フィールド名の書き間違いは読み込み時に失敗します。

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

シナリオが動かす target ごとに、有効な`runsOn`は target の`runsOn`から始まります。そこへ、その target のプラットフォームに対応するシナリオのブロックを、フィールド単位で上書きします。つまりシナリオの値が勝ちます。run が動かさないプラットフォームのブロックは効果を持たないので、1つのシナリオで複数のプラットフォームに対応できます。同じプラットフォームの target 2つを別の端末で動かす場合、両者は同じブロックを共有します。別々の条件が要るなら、シナリオを2つに分けます。

シナリオのブロックが受け付けるのは、条件のフィールド（`model`、`os`、`avd`、`apiLevel`、`browser.version`）と`locale`です。`locale`は現在の`preconditions.locale`を置き換えるもので、target とシナリオの両方で同じ場所に置くことになります。`kind`、`browser.engine`、`emulate`は target に残します。[BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation-ja.md)は、端末モードを target をどう駆動するかの性質としています。両方の見た目が要るシナリオは、2つの target で回します。

### シナリオを走らせる端末の選び方

`runsOn`は、シナリオを実施する環境を表します。端末を作ることはありません。Bajutsu は手元の端末を1台ずつ読みます。手元の端末とは、`--udid`の端末群の各 udid と、Web のレーンが起動したブラウザです。そのうえで、各端末をシナリオの有効な`runsOn`と比べます。

| 条件 | 読み取り元 | 比較 |
|---|---|---|
| iOS の`runsOn.os` | Simulator の runtime ラベルを`DeviceOS`で解析した値 | `major.minor`での範囲 |
| iOS の`runsOn.model` | その udid の simctl のデバイスタイプ名 | 完全一致 |
| Android の`runsOn.apiLevel` | `ro.build.version.sdk` | 整数の範囲 |
| Android の`runsOn.avd` | エミュレータの Android Virtual Device（AVD）名 | 完全一致。実機は一致しない |
| Web の`runsOn.browser.version` | Playwright の`browser.version` | 範囲 |

runner は、各シナリオに条件を満たす端末を割り当てます。端末群を順にたどるので、割り当ては決定的です。条件を満たす端末がないシナリオは走らせません。run はそのシナリオを新しい「**対象外**」（not applicable）の状態で記録し、満たせなかったフィールドと、見た端末を理由として示します。

```
not applicable: showcase/ipad-split-view
  runsOn.ios.model  "iPad Pro 13-inch (M4)"  available: "iPhone 15" (5A3F...), "iPhone 16" (7B21...)
```

対象外のシナリオは、成功でも失敗でもありません。report では別に並べ、flakiness の履歴からは除きます。選んだシナリオがすべて対象外のときは、「何も走らなかった」として run を非ゼロで終了します。空の実行が緑になることはありません。[BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md)は、設定を誤った worker がすべてを飛ばしても成功に見えることを理由に、スキップの状態を退けています。この終了の規則が、その懸念に応えます。`bajutsu doctor`は、シナリオごとに条件を満たす手元の端末を一覧にします。

範囲は比較子の論理積で書きます。比較子は`>=`、`>`、`<=`、`<`、`==`、または演算子のない裸の版です。裸の`18`は18.x のどのリリースにも一致します。`==`は0で埋めてから比べるので、`==18`が一致するのは18.0だけです。18.x 全体に一致させるには裸の形を使います。範囲の解析と比較は、新しい`bajutsu/common/devices/version.py`の`VersionSpec`が担います。`DeviceOS`は意図して比較演算子を持たないままにします。この項目は宣言で端末を選ぶだけで、OS ごとの分岐を足さないためです。

比較と割り当ては決定的で、モデルの呼び出しを含みません。合う端末をその場で作ることは範囲外です（「検討した代替案」を参照）。

### どこで実行するか

target は、どこで実行するかを持たなくなります。target が書くのは、何をテストし、何の上で動かし、Bajutsu がどう駆動するかです。どこで実行するかはマシンの性質であり、知っているのはマシンの運用者です。[BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md)も、worker capability のファイル`worker.yaml`を`bajutsu.config.yaml`に入れない理由として、同じ線を引いています。そのため、現在のキーのうち4つを target から外します。

| 旧キー | 移る先 | 理由 |
|---|---|---|
| `cloudBatchBudget` | `worker.yaml`の`maxJobConcurrency` | [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch-ja.md)がすでに置き換えています。端末を確保する worker が予算を数えるためです |
| `requires` | 廃止 | [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md)が自由記述の振り分けタグを廃止します。worker は、持っているものを在庫から広告します |
| `cloudBatch` | 実行の要求の`environment`（serve の fan-out の要求と、対応する CLI のオプション） | [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch-ja.md)では同じ target がローカルでも Device Farm でも走るため、送り先は target の性質ではなく実行ごとの選択です |
| `deviceProvider` | `worker.yaml`の`appium`という environment と、グリッドの`endpoint` | どのグリッドが端末を出すかは、device cloud と同じくインフラの選択です |

この項目のスキーマの外にも、2つの変更が要ります。serve の fan-out の要求に`environment`フィールドを足し、[BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch-ja.md)の`environment:<name>`による振り分けにつなぎます。また、`worker.yaml`が`appium`を`endpoint`つきの environment として受け付けるようにします。これは[BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md)の environment の語彙を、batch provider の外へ広げる変更です。どちらも、作業単位12で両項目と調整します。

`runsOn`は、[BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md)が残した穴も埋めます。`requires`がなくなると、後続の項目が target から要件を導くまで、ジョブは iOS のランタイムや端末の種類を要件にできません。その導出が読むのは、各シナリオの有効な`runsOn`です。有効な`runsOn`は、target とシナリオの`os`と`model`を合わせたものです。

### defaults

`defaults`には、target のキーのうち`platform`以外を書きます。共通のグループは`defaults`の直下に書きます。プラットフォームで形が変わるグループ（`app`、`runsOn`、`driver`と、`run`の iOS の項目）は`defaults.platforms.<platform>`の下に書き、そのプラットフォームの target にだけ重なります。これで1つのファイルに複数のプラットフォームのデフォルトを同時に持てます。辞書はキー単位で重ね、target の値が勝ちます。リストは置き換えます。例外は`evidence.redact`で、現行どおり和集合をとります。`ai`は現行どおりフィールド単位で重ねます。

組み込みのデフォルト`device: "iPhone 15"`は廃止します。`runsOn.model`のデフォルトとして残すと、`defaults`を書かない全設定が気づかないうちにこの値を要件として課すことになります。`model`を宣言しないまま置き換えの Simulator を作るときは、現行のフォールバックが最新の iPhone を選びます。

### 旧キーの移行先

| 旧キー | 新しい位置 |
|---|---|
| `backend` | 廃止。`platform`が actuator を決める |
| `bundleId`、`package` / `baseUrl`、`launchServer` | `app.id` / `app.url`、`app.server` |
| `appPath`、`build`、`deeplinkScheme`、`launchEnv`、`launchArgs` | `app.path`、`app.build`、`app.deeplink`、`app.launch.env`、`app.launch.args` |
| `readyWhen`、`idNamespaces`、`grantPermissions` | `app.readyWhen`、`app.idNamespaces`、`app.grantPermissions` |
| `device`、`locale`、`xcuitest.deviceType` | `runsOn.model`、`runsOn.locale`、`runsOn.kind` |
| `browser`、`deviceMode` | `runsOn.browser.engine`、`runsOn.emulate`（省略で desktop） |
| `headless`、`nativeZ`、`xcuitest.testRunner`、`xcuitest.build` | `driver.headless`、`driver.nativeZ`、`driver.runner.*` |
| `deviceProvider`、`cloudBatch`、`cloudBatchBudget`、`requires` | target から外す（「どこで実行するか」を参照） |
| `mockServer`、`mailbox` | `services.*` |
| `erase`、`network`、`visualCompare`、`secrets`、`systemAlertHandling`、`iosTipKitHandling` | `run.*`（最後は`run.tipKitHandling`） |
| `setup`、`before`、`after`、`interrupts` | `hooks.before`（`setup`を吸収）、`hooks.before`、`hooks.after`、`hooks.interrupts` |
| `capture`、`redact` | `evidence.*` |
| シナリオの`preconditions.locale` | シナリオの`preconditions.runsOn.<platform>.locale` |
| `scenarios`、`baselines`、`schemas`、`goldens` | `paths.*` |
| `defaults.reservedNamespaces`、`defaults.doctor` | トップレベルの`reservedNamespaces`と`doctor`。どちらも target ごとではなくチーム全体の値なので、`defaults`には置かない |

解決後の`Effective`は属性名を変えません。属性名を変えると呼び出し箇所が数百に及び、しかも設定の形とは別に進められます。そのため`resolve`が、新しい辞書から現行の`Effective`を組み立てます。

### 範囲外

- 旧キーの読み替えと、移行コマンド。
- 1つの target が複数のプラットフォームにまたがること。この用途は、引き続き複数 target のシナリオで扱います。
- エントリポイントを通じた、外部パッケージからのプラットフォームの登録。
- 宣言した範囲の run マニフェストへの記録。マニフェストは引き続き観測した OS を記録します。
- Playwright が起動したもの以外のブラウザのバージョンを選ぶこと。
- target でホスト OS を宣言すること。BE-0450 はホストをマシンから読み、各 driver が動けるホストを宣言し、`host:<os>`で振り分けます。target でも宣言すると、同じ事実を書く場所が2つになります。
- `runsOn`から振り分けの要件を導くこと。`>=17 <19`のような範囲は、現在の振り分けが使う「すべてを含む」タグの照合では表せません。そのため導出は、[BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md)が挙げる後続の項目に残します。

### 作業の分解

1. **`VersionSpec`。** `bajutsu/common/devices/version.py`で、版の範囲を解析し比較します。
2. **未確定の置き場所の確認。** `deeplinkScheme`、`launchEnv`、`launchArgs`、`locale`を読むバックエンドを洗い出します。AVD 名が対応する API レベル全体で読めるか（たとえば`ro.boot.qemu.avd_name`で）を確かめます。`setup`が`steps`に差し込まれることに依存するシナリオがないかを調べます。コードを入れる前に、置き場所の表を更新します。
3. **プラットフォームのレジストリとモデル。** レジストリとプラットフォームごとのモデルを追加します。レジストリのキーが`backends.PLATFORMS`と一致することをテストで固定します。
4. **スキーマの切り替え。** `TargetConfig`、`Defaults`、`Config`、`resolve`を置き換え、テストの固定データと`demos/`の設定ファイル10個を1つの変更で変換します。切り替えを分けると、読み込み側と全設定ファイルが同時に壊れるので、コミットの間でゲートが赤になります。
5. **`setup`の`hooks.before`への統合。** runner から`setup`の経路を取り除きます。
6. **コマンドラインインターフェイス（CLI）。** `--backend`は、`platform`と食い違えば終了コード2で終わる検査になります。`--browser`と`--headed`は、`runsOn.browser.engine`と`driver.headless`を上書きします。
7. **シナリオの`runsOn`。** プラットフォームごとに分けた`preconditions.runsOn`を足し、登録済みのモデルで検証します。`preconditions.locale`をその中へ移し、target の`runsOn`の上に重ねます。
8. **端末の読み取り。** 手元の各端末から、機種と OS（iOS）、API レベルと AVD 名（Android）、ブラウザのバージョン（Web）を読みます。
9. **割り当てと「対象外」の状態。** 有効な`runsOn`を満たす端末を各シナリオに割り当てます。対象外を理由つきで記録し、flakiness の履歴から除き、何も走らなかったときは非ゼロで終了します。
10. **`doctor`。** シナリオごとに、条件を満たす手元の端末を一覧にします。
11. **`bajutsu config schema`。**
12. **実行場所の移行。** target から`deviceProvider`、`cloudBatch`、`cloudBatchBudget`、`requires`を外します。serve の fan-out の要求と CLI に`environment`を足し、`worker.yaml`で`endpoint`つきの`appium`の environment を受け付けます。BE-0448 と BE-0450 と調整して進めます。
13. **文書。** `docs/configuration.md`、`docs/drivers.md`、`docs/cli.md`、`docs/architecture.md`、`DESIGN.md`、`docs/glossary.md`と、それぞれの`docs/ja/`版を更新します。

## 検討した代替案

| 代替案 | 採らなかった理由 |
|---|---|
| flat な並びのまま、他プラットフォームのキーを拒否する | 書き間違いは捕まえられますが、`device`の名前の衝突が残り、要件の置き場所も増えず、1つの用途の設定は散らばったままです |
| プラットフォーム名をキーにする（`ios: {…}`などのうち、ちょうど1つ） | `targets.web.web:`のように同じ語が二重に入れ子になります。キー名が可変なので、JSON Schema では「ちょうど1つ」を別の規則で表す必要があります |
| `platform`と、プラットフォーム固有のキーをすべて入れる`configuration`を組にする | 用途ではなく、どのプラットフォームが使うかでキーをまとめるため、アプリ、端末、駆動方法の設定が1つのブロックに混ざり、入れ子も一段深くなります |
| `driver: { kind: xcuitest \| adb \| playwright }`の判別共用体 | 現在はどのプラットフォームも actuator が1つで、2層目を設けても得るものがありません。2つ目の actuator を持つプラットフォームが出たら見直します |
| core にプラットフォームのモデルの閉じた共用体を持たせる | バックエンドを足すたびに core のスキーマを編集することになり、バックエンドに依存しない設計と矛盾します |
| 全プラットフォームで共通の`runsOn`の形 | iOS のブラウザや Web の AVD のように、どのプラットフォームも使えないフィールドが残り、flat な並びの問題が再び生じます |
| `dispatch`（`deviceProvider`、`cloudBatch`）を target に残す | BE-0448 と BE-0450 が worker と実行の要求へ移す判断と重複し、どこで実行するかを target を書くチームに決めさせることになります |
| `setup`と`before`を両方残す | 現行の挙動を保てますが、1つの用途を2つのキーが受け持ったままになります |
| 端末が合わなければ run を失敗させる | 複数の OS で同じ一式を回すと、OS 専用のシナリオが、対象でない OS の側で毎回失敗します |
| 合う端末をその場で作る | ランタイムの導入、時間、後片付けを Bajutsu が負います |
| シナリオの条件を target 名で分ける | `targets`を宣言するかどうかでキーの形が変わります。また、iOS の target と Android の target の両方で回すシナリオでは、両方の条件を書けません |
| シナリオの条件のフィールドを平たく並べる | どのフィールドがどのプラットフォームに効くかがファイルから読めず、flat な設定の「書いたのに効かない」問題が戻ります |
| 範囲なしの前方一致 | `>=17 <19`のような互換の範囲を1行で書けません |

## 進捗

> 作業の進行に合わせて更新してください。チェックリストは「詳細設計」の MECE な作業分解を
> 反映し（作業単位ごとに1つ）、ログは何がいつ変わったかを（古い順に）PR へのリンクつきで
> 記録します。

- [ ] 作業単位 1: `VersionSpec`
- [ ] 作業単位 2: 未確定の置き場所の確認
- [ ] 作業単位 3: プラットフォームのレジストリとモデル
- [ ] 作業単位 4: スキーマの切り替え、固定データ、`demos/`の設定ファイル
- [ ] 作業単位 5: `setup`の`hooks.before`への統合
- [ ] 作業単位 6: CLI
- [ ] 作業単位 7: シナリオの`runsOn`
- [ ] 作業単位 8: 端末の読み取り
- [ ] 作業単位 9: 割り当てと「対象外」の状態
- [ ] 作業単位 10: `doctor`
- [ ] 作業単位 11: `bajutsu config schema`
- [ ] 作業単位 12: 実行場所の移行
- [ ] 作業単位 13: 文書

## 参考

- [BE-0126](../BE-0126-per-platform-effective-config/BE-0126-per-platform-effective-config-ja.md)：解決後の`Effective`のプラットフォーム別の分割。
- [BE-0358](../BE-0358-device-os-as-a-first-class-fact/BE-0358-device-os-as-a-first-class-fact-ja.md)：`DeviceOS`と、記録される`device_runtime`。
- [BE-0228](../BE-0228-web-device-mode-emulation/BE-0228-web-device-mode-emulation-ja.md)と[BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines-ja.md)：`runsOn`へ移る`deviceMode`と`browser`。
- [BE-0392](../BE-0392-scenario-before-after-hooks/BE-0392-scenario-before-after-hooks-ja.md)：`hooks`が持つ`before`と`after`のフェーズ。
- [BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction-ja.md)：target から外れ、`worker.yaml`の`appium`の environment へ移る`deviceProvider`。
- `bajutsu/common/config/schema/target_config.py`と`bajutsu/common/config/resolve.py`：この項目が置き換えるスキーマと解決処理。
- [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch-ja.md)と[BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md)：どこで実行するかを引き受ける、worker capability のファイルと Device Farm の worker。

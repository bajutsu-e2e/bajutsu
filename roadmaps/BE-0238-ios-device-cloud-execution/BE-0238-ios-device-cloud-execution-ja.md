[English](BE-0238-ios-device-cloud-execution.md) · **日本語**

# BE-0238 — iOS device-cloud execution

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0238](BE-0238-ios-device-cloud-execution-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0238") |
| 実装 PR | [#1192](https://github.com/bajutsu-e2e/bajutsu/pull/1192)（ユニット 1: XCUITest 実機ターゲティング）、[#1193](https://github.com/bajutsu-e2e/bajutsu/pull/1193)（ユニット 2: batch package 化）、[#1195](https://github.com/bajutsu-e2e/bajutsu/pull/1195)（ユニット 3: 再署名 / 実機ケーパビリティの preflight）、[#1196](https://github.com/bajutsu-e2e/bajutsu/pull/1196)（ユニット 4: live の経路 Appium endpoint provider、seam のみ）、[#1197](https://github.com/bajutsu-e2e/bajutsu/pull/1197)（ユニット 5: 境界を fake にしたテスト）、[#1198](https://github.com/bajutsu-e2e/bajutsu/pull/1198)（ユニット 6: iOS デバイスクラウドの手順）、[#1201](https://github.com/bajutsu-e2e/bajutsu/pull/1201)（live の経路 スライス A: WebDriver トランスポート）、[#1203](https://github.com/bajutsu-e2e/bajutsu/pull/1203)（live の経路 スライス B: 入力とジェスチャ）、[#1205](https://github.com/bajutsu-e2e/bajutsu/pull/1205)（live の経路 スライス C: ケーパビリティ縮退、showcase ターゲット、手順） |
| トピック | デバイスクラウド実行 |
<!-- /BE-METADATA -->

## はじめに

デバイスクラウドは、iOS を**実機**で走らせます。Bajutsu の現在の iOS actuator は、その実機に届きません。

- `simctl` は Simulator だけを対象とします。実機のクラウドに simulator はありません。
- `idb` は、Mac に常駐する `idb_companion` のデーモンを要します。マネージドの macOS ホストは、それを
  露出しません。

iOS でクラウドが*話せる*のは、**XCTest** です。AWS Device Farm は、**Appium の XCUITest ドライバ**も
話します。

この項目は、それらの成果物を生む iOS の実行経路を足します。経路は、すでに出荷済みの **XCUITest backend
（BE-0019）**の上に築きます。クラウドで、そして副産物としてローカルでも、iOS 実機の自動化が可能になります。

## 動機

iOS は、クラウド対応に実際の新規コードが要る唯一のプラットフォームです。既存の iOS actuator は、構造と
してクラウドと相容れません。config を直しても、この隔たりは埋まりません。後で誤診しないよう、制約を
ここで確認します。

- **`simctl`** は Simulator を駆動します。実機のクラウドには、対象となる simulator がありません。
- **`idb`** は、Mac ホスト上で動く `idb_companion` を要します。マネージドのクラウド macOS ホストは、
  限られたシェルしか提供せず、任意のデーモンを常駐させません。

AWS Device Farm も Firebase も、iOS のテストを物理デバイス上で XCTest により走らせます。Device Farm は、
Appium の XCUITest ドライバでも走らせます。AWS Device Farm も Firebase も、`idb` や `simctl` を記載していません。

前進の経路は、XCTest と Appium-XCUITest を話すことです。駆動の層は新しく作らず、**BE-0019 の XCUITest
backend** を再利用します。

この作業には、単体での価値もあります。同じ作業が、Bajutsu に**ローカルの iOS 実機**を、デバイス target に
対する `xcodebuild` を通じて駆動させます。今日、この経路は事実上 Simulator 専用です。

これはデバイスクラウドの項目のうち最も重く、意図的に独立させています。Android の provider は adb backend
をほぼそのまま再利用します。iOS は、実機を駆動できる経路と、クラウドの package 化を要します。

項目は foundation の継ぎ目の後に順序づけます。理由は 2 つあります。

- iOS の *live* な経路（たとえば Appium endpoint の provider）が、*device-cloud-provider-abstraction* を
  再利用できます。
- *batch* の経路（Device Farm の XCTest / Appium の package）が、*aws-device-farm-submitter* の
  package 化を再利用できます。

## 詳細設計

2 つの経路が、XCUITest の駆動層を共有します。

- **batch（AWS Device Farm）。** アプリと、Bajutsu のシナリオ実行を担う XCTest / Appium-XCUITest の
  bundle を package 化します。*aws-device-farm-submitter* の仕掛けで提出し、artifact を回収します。
  経路は、Device Farm の iOS の制約を織り込む必要があります。
  - アプリは、デバイス向けに**再署名**されます。再署名で、App Groups や Push などの一部の entitlement が
    剥がれます。
  - `.ipa` は、デバイス向けビルドが要ります（simulator ビルドは不可）。
  - XCTest は、Appium の経路と同じようにはカスタマイズできません。
- **live（遠隔デバイス、後続）。** クラウドが、予約済みの iOS デバイスに Appium / WebDriver の endpoint を
  露出する場合があります。その場合、クラウドを live の継ぎ目上の `DeviceProvider` としてモデル化します。
  provider は、adb の serial の代わりに endpoint を渡します。その endpoint を、Appium-XCUITest の経路で
  駆動します。この経路は、foundation の継ぎ目をそのまま再利用します。

まとめとなる作業は、**XCUITest backend（BE-0019）を実機対応にする**ことです。今日の backend は、
`xcodebuild` を通じて Simulator を対象にします。この項目は、target の選択を実機へ一般化します。まず
ローカル、次にクラウドの package 化です。この一般化が、両経路の再利用可能な核になります。

### 作業分解（MECE）

1. **XCUITest の実機ターゲティング。** BE-0019 の backend を一般化し、iOS の実機を駆動します（ローカルの
   `xcodebuild` のデバイス target）。両経路の共有の土台です。
2. **batch の package 化（Device Farm）。** シナリオ実行を担う XCTest / Appium-XCUITest の package を
   作ります。*aws-device-farm-submitter* のアップロードと回収の流れに統合します。
3. **再署名と entitlement の扱い。** Device Farm の再署名を記述して扱います。どの entitlement が落ちるか、
   それに依存するシナリオを preflight でどう縮退または skip するかを定めます。
4. **live の経路（Appium endpoint の provider）。** 予約済みの iOS デバイスに Appium の endpoint を渡す
   `DeviceProvider` を作ります。XCUITest / Appium の経路で駆動します。後続の slice として着地しうる
   作業です。
5. **テスト。** 実機ターゲティングの解決と package の組み立てを検証します。`xcodebuild` とツールチェインの
   境界を fake にします。ゲートに live のクラウドは持ち込みません。
6. **ドキュメント。** iOS のデバイスクラウドの手順（両言語）を置きます。`idb` / `simctl` が使えない理由、
   XCTest と Appium の経路、再署名の注意を記します。

### live 経路の transport（後続の slice）

ユニット 4 は、`appium` の `DeviceProvider`（live の継ぎ目）を着地させました。provider は、予約済みの
iOS デバイスの固定の Appium / WebDriver endpoint を、実行の udid spec として渡します。その endpoint を
駆動するには、別の transport が要ります。transport は、まだ作られていません。

transport は、今日の経路に WebDriver クライアントをかぶせるだけでは実現できません。udid spec は、そのまま
`XcuitestEnvironment` へ流れます。その `_destination()` は、値を `simctl.validated_udid` に通します。共有の
`device_id` の文字集合は、URL の `/` を許しません。そのため、実在の `http(s)://` endpoint は、今日
`DeviceError: invalid udid` を送出します。以下の設計は、この隙間を塞ぎます。

**WebDriver は runner チャネルではなく Python から話します。** ローカルの XCUITest 経路は、常駐する
BajutsuKit の runner を、独自の loopback HTTP チャネルで駆動します。Appium のグリッドは、その runner を
露出しません。露出するのは、W3C（World Wide Web Consortium）WebDriver の endpoint（Appium の XCUITest
driver）だけです。そこで live の transport は、遠隔の endpoint へ W3C WebDriver を Python から直接話します。
グリッドが提供しないポートへ、runner チャネルをトンネルすることはしません。

**要素の解決は Python 側に保ちます。** WebDriver は、locator をサーバ側で解決します。決定性優先
（prime directive 2）は、Bajutsu がセレクタを Python 側で解決することを求めます。そうすれば、曖昧な
セレクタは、最初に一致した要素を操作せず、即座に失敗します。

そこで live の driver は、既存の `XcuitestDriver` の形をそのまま再利用します。

- 画面全体を 1 つの広い locator で query します（`//*` または iOS の class chain に対する
  `findElements`）。
- `base.Element` のリストを組み立てます。
- そのリストに対して、`resolve_unique` でセレクタを解決します。
- query が返した WebDriver の element id で、選ばれた要素を操作します。

これは、runner チャネルがすでに踏襲している「query して解決し handle で操作する」流れです。runner の
per-snapshot handle の位置に、WebDriver の element id が入ります。

**注入可能な transport の継ぎ目を、最小の自前クライアントで再利用します。** `XcuitestDriver` は、すでに
ワイヤプロトコルを注入可能な transport として受け取ります。入力は `(method, path, body)`、出力は復号した
応答です。要求と応答のロジックは、ゲート上に Simulator を持たずに fake に対して動きます。

live の driver は、標準ライブラリの `http.client` の上に組んだ最小の自前 W3C WebDriver クライアントを、
同じやり方で注入して受け取ります。自前のクライアントは、runner チャネル自身の stdlib クライアントと
揃います。第三者の WebDriver 依存を、ゲートに持ち込みません。ネットワーク境界の fake の endpoint に対して、
WebDriver の写像をゲートで動かせます。

**endpoint を simctl と xcodebuild の仕掛けの外へ回します。** `environment_for` は、実行の environment を
選びます。xcuitest actuator の udid spec が `http(s)://` の endpoint のとき、実行は live の経路を採ります。
live の経路は、simctl の bring-up と `xcodebuild test-without-building` のサブプロセスを、丸ごと飛ばします。
endpoint に対して WebDriver のセッションを開き、live の driver で駆動し、teardown でセッションを閉じます。

URL のスキームが、ルーティングのシグナルです。スキームはまさに `validated_udid` が弾く値です。スキームを
先に認識すれば、live の経路を選べます。誤解を招く `invalid udid` エラーも置き換えられます。endpoint は
udid の仕掛けに触れません。udid の仕掛けは、構造として URL を運べないからです。

BE-0236 の `ProvisionProfile` は、予約済みのデバイスを、ビルドを入れて起動済みだと報告します。そのため
live の経路は、simctl の bring-up のフラグを何も honor しません。実機の経路（ユニット 1）は、すでに
それらを飛ばします。

後続は 3 つの slice に分けて着地します。いずれも WebDriver の境界で fake にします。

- **slice A：セッションと最小の driver。** この slice は、次の 3 つを置きます。
  - 自前の W3C クライアント（セッションの作成と削除）
  - live の driver の `query`、`tap`、`screenshot`、準備完了待ち
  - `environment_for` のルーティング（セッションを開き、`xcodebuild` を飛ばす）

  この slice で、tap して assert する live の実行が、端から端まで通ります。
- **slice B：入力とジェスチャ。** `swipe`、`scroll`、`type_text`、`delete_text`、`select_all`、
  `copy_selection`、`double_tap`、`long_press`、`tap_point`、`pinch`、`rotate` を、WebDriver のアクション
  へ写像します。アクションは、W3C の Actions の API（application programming interface）か、Appium の
  `mobile:` コマンドです。`double_tap` と `long_press` は、今日 `tap` と同じ `/tap` runner endpoint を
  extra param で使います。WebDriver への写像の規模は、他のジェスチャと同程度です。`tap_point` は、
  システムアラートなどに使うハンドルなし座標タップです。同じ Actions の経路に写像されます。
- **slice C：capability・config・ドキュメント。** live 経路の実行時の capability の集合を、
  Appium-XCUITest が届く範囲へ縮退させます（`capabilities_for_run`、ユニット 3 の仕組み）。live の
  グリッド向けの showcase target を足します。手順に live 経路を加えます。

### prime directive への適合

- **AI をゲートに入れない。** 実機の iOS 実行は、決定的な XCTest / XCUITest です。合否判定の経路に
  モデルはありません。
- **決定性優先。** 固定 sleep は導入しません。準備完了は、既存 backend と同じく条件に基づいたままです。
- **app 非依存。** iOS のクラウド固有の事柄は、target の config と、batch では CI 側の submitter に
  置きます。ランナーとシナリオ形式は変わりません。

## 検討した代替案

- **`idb` / `simctl` をクラウドへ移植する。** 構造として実現不能なので却下します。実機のクラウドに
  simulator はなく、マネージドの macOS ホストはデーモンを常駐できません。そこで XCTest / Appium の経路を
  採ります。
- **iOS 用のクラウド backend を一から新設する。** 却下します。BE-0019 の XCUITest backend を重複させる
  ためです。代わりに、BE-0019 を実機へ一般化します。
- **iOS を Android と同時に行う。** 却下します。Android は adb backend をほぼそのまま再利用しますが、
  iOS は駆動と package 化の実際の新規コードを要します。2 つを分けておけば、Android の経路は、重い iOS の
  作業を待たずに先に出荷できます。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] XCUITest の実機ターゲティング（BE-0019 を Simulator の先へ一般化）
- [x] Device Farm 向けの batch package 化（submitter への統合）
- [x] 再署名と entitlement の扱い（記述 + preflight の縮退）
- [x] live の経路：Appium endpoint の `DeviceProvider`。WebDriver トランスポートでエンドツーエンドに
  駆動（スライス A〜C）
- [x] テスト（`xcodebuild` とツールチェインの境界を fake に）— 実機ターゲティングはユニット 1 で担保
- [x] ドキュメント（iOS のデバイスクラウドの手順、idb / simctl の理由、再署名の注意）

**ログ。**

- ユニット 1（[#1192](https://github.com/bajutsu-e2e/bajutsu/pull/1192)）：`xcuitest.deviceType`（既定 `simulator` / `device`）を追加し、XCUITest
  環境の `-destination` を実機向けに `platform=iOS` へ一般化しました。駆動レイヤ（`xcodebuild
  test-without-building`）は Simulator と実機で共通です。実機は simctl の端末準備を飛ばし、実機では
  成立しない simctl 依存の前提条件（erase / `appPath` インストール / 権限付与）は明示的に失敗させ、
  ユニット 2〜3 に先送りします。`xcodebuild` とツールチェインの境界を fake にし、ゲートに Simulator は
  不要です。
- ユニット 2（[#1193](https://github.com/bajutsu-e2e/bajutsu/pull/1193)）：*aws-device-farm-submitter*（`scripts/devicefarm_submit.py`）を Android 専用から
  iOS の投入にも対応させました。`platform` がアプリのアップロード種別（`ANDROID_APP` / `IOS_APP`）と
  プラットフォームごとの実行方法を選びます。iOS は Device Farm が公開する予約済みのデバイスの
  `$DEVICEFARM_DEVICE_UDID` に対して `bajutsu run --backend xcuitest` を走らせ（ユニット 1 の実機
  ターゲティングを再利用）、Android は従来どおり `--backend adb --udid booted` を使います。Appium-Python の
  カスタム環境向けテストパッケージとスペックの種別は変更していません。XCTest / Appium-XCUITest の
  バンドルを別に作るのではなく、Bajutsu のシナリオ実行をすでに担っている既存の Appium-Python カスタム
  環境パッケージをあえて再利用し、iOS の実行だけをそこに通す方針です。そのため `build_package` には
  手を入れず、変更は `render_test_spec` と CLI 側の配線（backend、`--udid`、アップロード種別）に
  限られます。テストは AWS SDK の境界だけを fake にします。showcase の iOS 用 Device Farm 設定と
  CI ワークフローのジョブは、実機向けの `.ipa` を署名なしにはビルドできないため、ユニット 3（再署名）を
  待ちます。
- ユニット 3（[#1195](https://github.com/bajutsu-e2e/bajutsu/pull/1195)）：実機の iOS（`xcuitest.deviceType: device`）では simctl
  依存のケーパビリティが失われることを preflight に教えました。新しい
  `backends.capabilities_for_run(actuator, eff)` が静的な XCUITest のケーパビリティ集合を縮退させ、
  シミュレータにしか届かない `DeviceControl` 一式と simctl-privacy の権限付与を落とします。これにより、
  デバイス制御や権限付与を使うシナリオは、実行の途中で `simctl` エラーとして遅れて失敗する代わりに、
  明確な理由とともに前もって（BE-0082）スキップされます。ユニット 1 の実行時の明示的な失敗に対する
  preflight 側の対応物です。あわせて Device Farm の再署名を `docs/devicefarm.md`（両言語）に記述しました。
  再署名はエンタイトルメント（Push / App Groups）を剥がすため、それに依存するアプリの機能は再署名後の
  挙動になり、simctl 依存のステップは実機ではスキップされます。showcase の iOS 用 Device Farm 設定と
  CI ワークフローのジョブは、実機の署名基盤（署名なしの実機向け `.ipa` はビルドできません）を引き続き
  待つため、後続に残します。
- ユニット 4（[#1196](https://github.com/bajutsu-e2e/bajutsu/pull/1196)）：BE-0236 の provider seam の上に、live
  の経路の `DeviceProvider`（seam のみ）を足しました。新しい組み込みの `appium` provider（`deviceProvider.kind:
  appium`）は、予約済みの iOS デバイスが待つ固定の Appium / WebDriver `endpoint`（セルフホストの grid）を
  udid spec としてそのまま run に渡し、デバイスは起動済みでビルドも導入済み（simctl で起動もインストールも
  しない live なリモートデバイス）と報告し、解放する対象は持ちません（予約は grid のものです）。`endpoint`
  が無ければ解決時に fail-closed で、未知の `kind` のガードと同型です。今回は seam のみで、endpoint を
  Appium / WebDriver プロトコルで駆動する部分は後続のトランスポート（XCUITest backend は現状 W3C WebDriver
  ではなく独自の runner チャネルを話します）に残すため、チェックは付けず、live の経路はまだ端から端まで
  走りません。そのトランスポートは今日の経路の上に WebDriver クライアントを重ねるだけでは実現できません。
  udid spec はそのまま `XcuitestEnvironment` に流れ、その `_destination()` が `simctl.validated_udid` を
  通しますが、共有の `device_id` の文字集合は URL の `/` を除くため、実際の `http(s)://` endpoint は現状
  `DeviceError: invalid udid` を送出します。後続のスライスでは、この値を simctl / xcodebuild の udid 機構
  ごと迂回させる必要があります（この機構は構造的に URL を運べないためです）。Android 環境の
  `ProvisionProfile` 配線は XCUITest には意図的に持ち込みません。実機の経路
  （ユニット 1）は既に simctl の端末準備を丸ごと飛ばすため、そこでフラグを尊重しても到達しないコードに
  なるからです。合否判定の経路の外で、fake で、デバイスは不要です。
- ユニット 5（[#1197](https://github.com/bajutsu-e2e/bajutsu/pull/1197)）：ユニット 1〜4 のあとに残っていた
  テストの穴を埋めました。いずれも純粋で、ゲート上で走ります（Simulator もクラウドも要りません）。ユニット 4 の
  live 経路の境界を実行可能な事実として固定しました。`appium` provider が渡す Appium / WebDriver の endpoint は、
  そのまま `_destination` に流れる udid spec そのものなので、実際の `http(s)://` endpoint は共有の `device_id`
  ポリシー（`invalid udid`）で現状は弾かれます。これは live の経路がまだ端から端まで走らないことを示す事実であり、
  後続のトランスポートが endpoint を simctl / xcodebuild の udid 機構ごと迂回させたときに、このテストが目に見えて
  赤くなって更新の合図になります。また、ユニット 3 の capability 縮退を支える `xcuitest_targets_real_device`
  アクセサに直接のユニットテスト（device / simulator / ブロック省略 / 非 iOS）を足しました。これまでは間接的に
  しか叩かれていませんでした。加えて `appium` provider の空 endpoint の分岐（falsy だが `None` ではない）を、
  endpoint 欠如の場合とは別の経路として押さえました。
- ユニット 6（[#1198](https://github.com/bajutsu-e2e/bajutsu/pull/1198)）：iOS のデバイスクラウドの手順
  （`docs/ios-device-cloud.md` と `docs/ja/` のミラー）を追加しました。work breakdown の *ドキュメント* の
  ユニットです。このページは、実機で `idb` / `simctl` が構造的にデバイスクラウドと相容れない理由
  （シミュレータ専用、デーモンの常駐）、BE-0019 バックエンドの `-destination` を実機（ローカル接続の
  デバイスを含む）へ一般化する再利用可能な `xcuitest.deviceType: device` の核、batch の経路
  （submitter の仕組みを重複させず `docs/devicefarm.md` へ相互リンク）と seam だけの live の経路
  （`appium` endpoint provider と、その endpoint がまだエンドツーエンドで動かない理由）、そして実機の
  注意点（再署名で entitlement が剥がれること、simctl に支えられたデバイス制御と権限付与が preflight で
  縮退すること）を説明します。mkdocs のナビゲーションにも追加しました。ドキュメントだけで、製品コードの
  変更はありません。live の経路のトランスポートが、残る最後の未了ボックスです。
- live の経路 スライス A（[#1201](https://github.com/bajutsu-e2e/bajutsu/pull/1201)）：live トランスポートの最初のスライスを実装しました。`appium`
  provider が渡す endpoint を、tap と assert を伴う run でエンドツーエンドに駆動できるようにしました。
  自前の最小 W3C WebDriver クライアント（`drivers/xcuitest_live.py`、`http.client` を土台にし、その
  wire は `XcuitestDriver` がトランスポートを注入するのと同じ方式で注入するので、第三者の WebDriver
  依存はゲートに入りません）と、query-resolve-act-by-handle の形をそのまま再利用する
  `XcuitestLiveDriver` を追加しました。1 回の広い `findElements` で `base.Element` の一覧を組み立て、
  `resolve_unique` がセレクタを Python 側で解決し（曖昧なセレクタは actuation の前に落ちます。決定性が
  第一です）、tap は query が返した WebDriver の element id で actuate します。この element id が、runner
  の snapshot ごとの handle の代わりを務めます。`environment_for` は `http(s)://` の udid spec を
  ルーティングの合図として認識し（共有の `device_id` ポリシーが拒否する値そのものです）、新しい
  `XcuitestLiveEnvironment` を返します。この環境は endpoint に対して WebDriver のセッションを開き、
  simctl / `xcodebuild` を完全に迂回します。simctl に支えられた seam のメソッド（`resolve_device`、
  デバイスカタログ、`DeviceControl` の controller、relauncher）は live の経路の形へ上書きします。いずれ
  も上書きしなければ URL を拒否する `simctl.Env(endpoint)` を構築してしまうためです。これでユニット
  4 / 5 の境界を閉じました。実際の endpoint はもう `_destination` に届かないので、境界のテストは新しい
  ルーティングを検証する形へ更新しました。スライス A は `query` / `tap` / `screenshot` と readiness を
  担います。入力とジェスチャ（スライス B）、実行時のケーパビリティ縮退 / config / ドキュメント（スライ
  ス C）は残るので、live の経路のボックスは未チェックのままにします。WebDriver のネットワーク境界を
  fake にしており、ゲートにグリッドも実機もありません。
- live の経路 スライス B（[#1203](https://github.com/bajutsu-e2e/bajutsu/pull/1203)）：live の経路の入力とジェスチャを、Appium
  の XCUITest `mobile:` コマンド（`POST /execute/sync` 経由）へ対応づけました。これらはローカルの runner
  が持つ意味的なエンドポイントの、ネイティブな対応物です。`tap_point` → `mobile: tap`、`double_tap` →
  `mobile: doubleTap`、`long_press` → `mobile: touchAndHold`、`swipe` / `scroll` →
  `mobile: dragFromToForDuration`、`pinch` → `mobile: pinch`、`rotate` → `mobile: rotateElement` と
  対応させました。要素を対象とするジェスチャは、tap と同じ `_resolve_handle` を通してセレクタを Python
  側で解決するので、曖昧なセレクタは actuation の前に落ちます（決定性が第一です）。テキスト入力は、
  フォーカス中のフィールドへ標準の W3C send-keys で入力します（`type_text`、そして 1 文字ごとに backspace
  キーを送る `delete_text`）。2 つのアクションには第一級の Appium XCUITest コマンドがありません。ローカル
  の runner は `select_all` / `copy_selection` をネイティブに行いますが、live の経路ではこれらを黙って
  no-op にせず `UnsupportedAction` で明示的に落とします。これらを事前に skip する実行時のケーパビリティ
  縮退はスライス C です。pinch / rotate を実装したので、driver は `MULTI_TOUCH` を宣言するようになり、
  自身が駆動できる範囲をそのまま報告します。スライス C（実行時のケーパビリティ縮退、showcase の live
  グリッド用ターゲット、手順への live の経路の節の追加）は残るので、live の経路のボックスは未チェックの
  ままにします。WebDriver のネットワーク境界を fake にしており、ゲートにグリッドも実機もありません。
- live の経路 スライス C（[#1205](https://github.com/bajutsu-e2e/bajutsu/pull/1205)）：live の経路を仕上げました。実行時のケーパビリティ
  縮退、動く showcase の設定、そして手順です。`capabilities_for_run` は、live の実行では XCUITest のセット
  を live driver 自身の `CAPABILITIES` へ縮退させます。これを唯一の真実の源にすることで preflight と
  driver が歩調を合わせ、WebDriver トランスポートが駆動できない 3 つの系統を落とします。`TEXT_SELECTION`
  （select-all / copy に第一級の `mobile:` コマンドがない）と、他のあらゆる実機と同じく `DeviceControl` の
  系統、そして simctl のプライバシー権限付与です。いずれかを必要とするシナリオは、実行の途中で
  `UnsupportedAction` として遅れて失敗する代わりに、前もって明確な理由とともにスキップされるようになりました
  （BE-0082）。ユニット 3 の実機縮退の、live の経路版です。`capabilities_for_run` は、解決済みの udid スペック
  を `is_webdriver_endpoint`（`environment_for` がルーティングに使うのとまさに同じ述語）に通して経路を判定する
  ため、preflight とルーティングが食い違うことはありません（`appium` プロバイダーのエンドポイントと、既定の
  local プロバイダーでの生の `--udid https://…` は同じように縮退します）。`demos/showcase/live/` に動く例の
  設定（`showcase-swiftui` ターゲットに `appium` プロバイダーを付け、ローカルの `appPath` / runner は持たな
  い）を置き、手順の live の節（両言語）を「継ぎ目だけ」からエンドツーエンドの経路へ書き直し、縮退を記述
  しました。設定とケーパビリティの境界を fake にしており、ゲートにグリッドも実機もありません。これで live の
  経路のボックスが埋まり、すべてのユニットが着地したので BE-0238 は実装済みです。（batch の経路の showcase
  CI ジョブは実機署名の基盤を待っています。署名なしの実機 `.ipa` はビルドできないためで、本項目の MECE な
  ユニットの外の後続作業として残ります。）

## 参考

- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)
- [AWS Device Farm — iOS custom test environment hosts](https://docs.aws.amazon.com/devicefarm/latest/developerguide/custom-test-environments-hosts-ios.html)
- [AWS Device Farm — Appium test types](https://docs.aws.amazon.com/devicefarm/latest/developerguide/test-types-appium.html)
- [Firebase Test Lab — iOS (XCTest)](https://firebase.google.com/docs/test-lab/ios/get-started)
- 依存する兄弟項目：**device-cloud-provider-abstraction**（live の継ぎ目）、**aws-device-farm-submitter**
  （batch の package 化）

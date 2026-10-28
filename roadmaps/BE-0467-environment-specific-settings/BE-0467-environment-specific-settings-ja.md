[English](BE-0467-environment-specific-settings.md) · **日本語**

# BE-0467 — 環境に固有の設定：対応表、ターゲット設定での無視の宣言、lint と振り分け時の検査

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0467](BE-0467-environment-specific-settings-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装中** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0467") |
| 実装 PR | [#2249](https://github.com/bajutsu-e2e/bajutsu/pull/2249)（単位 1〜3） |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md), [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues-ja.md), [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md), [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability-ja.md), [BE-0457](../BE-0457-real-device-step-lint/BE-0457-real-device-step-lint-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[シナリオ](../../docs/ja/glossary.md#シナリオのオーサリング)は、性質の異なる環境で動きます。
iOS Simulator、実機の iPhone、WebDriver エンドポイントの向こうのデバイス、Android エミュレータ、web ブラウザです。
*設定*の中には、特定の環境でしか適用できないものがあります。
`run.erase` は `simctl erase` を、`seedPhotos` は `simctl addmedia` を使うため、Simulator にしか届きません。

この項目では、そうした設定の扱いを次の規則でどの環境でも揃えます。

- **ステップは決して飛ばしません。** 振り分け先の[ターゲット](../../docs/ja/glossary.md#target-app-device)が実行できないステップはエラーで、除外の手段はありません。
- **環境が適用できない設定は、デフォルトでエラーにします。** 唯一の除外は、その設定を無視するとターゲット設定（`targets.<name>`）に理由付きで宣言することです。シナリオには書きません。
- **どちらの検査もデバイスを使う前に行います。** `bajutsu lint --config --target` と、serve がジョブを worker に渡すときです。ジョブは、必要なステップをすべて実行でき、必要な設定をすべて適用できる worker にだけ届きます。

環境のルートごとに 1 つの対応表がこの 3 つを決めるため、lint、run、振り分けの判定が食い違いません。

## 動機

現状では、設定を適用できない環境の振る舞いが 3 通りあり、どれになるかは設定とルートによって変わります。

| 設定 | iOS Simulator | iOS 実機 | ライブ WebDriver | Android | web |
|---|---|---|---|---|---|
| `run.erase` | 適用 | 起動時に例外 | 起動時に例外 | 適用 | 無視 |
| `app.path` のインストール、`app.reinstall` | 適用 | 起動時に例外 | 無視 | 適用 | 無視 |
| `permissions` | 適用 | preflight で失敗（BE-0082） | preflight で失敗（BE-0082） | 適用 | preflight で失敗（BE-0082） |
| `runsOn.ios.seedPhotos` | 適用 | 未到達 | 未到達 | 設定不可 | 設定不可 |
| `runsOn.ios.locale` | 適用 | アプリへの受け渡しのみ | 起動時に例外 | 設定不可 | 設定不可 |
| `app.launch.env`、`args`、`deeplink` | 適用 | 適用 | 起動時に例外 | 適用 | 無視 |

根拠は `bajutsu/common/platform_lifecycle/environments/` 配下の `xcuitest_environment.py:352-372`、`xcuitest_live.py:73-125`、`android_environment.py:186-243`、`web.py:40-61` です。
「設定不可」は、iOS 専用のフィールドであることを表します。このフィールドは、読み込み時に Android や web のブロックへは書けません（BE-0452）。プラットフォームごとの書き分けであり、欠落ではありません。
この混在から、次の 3 つの負担が生じています。

- **黙って無視すると、壊れた前提条件が隠れます。** ライブ WebDriver のルートではターゲットの `app.path` が使われないため、run は直前にビルドしたものではなく、デバイスにすでに入っているビルドを操作します。web では `app.launch.env` がページに届きません。結果は緑でも、シナリオは別のものを検査しています。
- **例外が遅れて表に出ます。** 例外が出るのは、デバイスを借りてアプリを起動する直前です。BE-0082 の capability preflight が扱うのは `permissions` だけで、`erase` やインストールは対象外です。
- **振り分けの前に何も検査しません。** `bajutsu lint` は設定を読まないため、ターゲットを見ません。serve はジョブを platform、host、environment、machine のトークンで振り分けますが、どれもステップの capability や設定を表しません（`serve/capabilities.py:94-178`）。そのため、`setLocation` を含むジョブが実機の iPhone を操作する worker に届き、そこで失敗することがあります。

BE-0457 は、実機で*ステップ*を飛ばす修飾子 `skipOnRealDevice` を提案していました。
しかしステップを飛ばすと、同じシナリオの結果が実行した場所によって変わります。
実機では検査が減ったから合格する、ということが起き、結果を読む人を混乱させます。
この項目は BE-0457 を置き換え、BE-0457 は却下とします。

この項目が入ると、変化は 1 つのコマンドで確かめられます。
`bajutsu lint --config <file> --target <実機>` は、ターゲットが扱えないステップと設定を列挙して終了コード 1 で終わります。この時点でデバイスは借りません。
`app.launch.env` を設定したシナリオを web で実行すると、ブラウザを起動する前に失敗します。web のターゲットがその設定の無視を宣言していれば失敗せず、レポートに無視した設定とその理由が載ります。
serve は、`setLocation` を含むジョブを、その capability を持たない環境の worker に渡しません。

## 詳細設計

設計は、対応表、無視の宣言、run、lint、振り分け、証跡の種類の 6 つからなります。
どれもモデルの呼び出しや固定の待機を加えません。環境ごとの差は、対応表とターゲット設定の中に収めます。

### 対応表

新しいモジュール `bajutsu/common/capability/settings_support.py` が、環境に固有の設定を列挙し、環境のルートごとにその扱いを答えます。

| 対応 | 意味 | run での振る舞い |
|---|---|---|
| `supported` | ルートが設定を適用できます | 適用します |
| `satisfied` | 設定が求める状態を、ルートがすでに保証しています | 何もしません。エラーにもしません |
| `unsupported` | ルートが設定を適用できません | エラーにします。ターゲットが無視を宣言していれば適用しません |

`satisfied` は、ルートが仕組みとして満たしている要求を扱います。
web の run は毎回新しいブラウザコンテキストで始まるため、`erase: true` はすでに成り立っています。
この場合を `unsupported` とすると、すべての web ターゲットに意味のない宣言を強いることになります。

対象の設定と、宣言で使うキーは次のとおりです。

| キー | 元のフィールド |
|---|---|
| `erase` | `run.erase`（シナリオまたはターゲット） |
| `install` | ターゲットの `app.path` のインストールと `app.reinstall` |
| `permissions.<service>` | シナリオの `permissions` の各サービス |
| `seedPhotos` | `runsOn.ios.seedPhotos` |
| `locale` | `runsOn.ios.locale` |
| `launchEnv`、`launchArgs`、`deeplink` | `app.launch.env`、`app.launch.args`、`app.launch.deeplink` |

ルートは、`capabilities_for_run`（`bajutsu/common/backends.py:270`）がすでに解決しているものと同じです。アクチュエータ、ターゲットの `runsOn.kind`、WebDriver エンドポイントで決まります。
関数 `settings_support(actuator, eff, udid_spec)` が、そのルートの対応表を返します。
権限はステップより前に適用する設定なので、その行を `capability_preflight._REQUIREMENTS` からこちらへ移します。
サービスごとの capability トークンは引き続きルートの対応を表し、BE-0082 のステップの検査は `permissions` を判定しなくなります。
そのため、`permissions.<service>` の無視の宣言は 1 つの検査だけで扱われます。
`erase_precondition_supported`（`backends.py:326`）は、同じ表の参照に置き換えます。

判定するのは各環境ではなく対応表です。
各環境の起動処理は解決済みの計画を読み、`supported` の設定を適用し、計画が `unsupported` とした設定をターゲットが無視していなければ例外を投げます。
例外を残すのは、preflight が省略されうるためです。lease を直接駆動するときは actuator がなく、`caps` が `None` になります（`bajutsu/common/runner/pipeline.py:1989`）。そのため、例外は環境モジュールがすでに実行時の最後の砦と呼んでいる役割を担い続けます。
計画が置き換えるのは、環境ごとの条件分岐と黙って無視する処理であり、失敗そのものではありません。

### 無視の宣言

`RunPolicy`（`bajutsu/common/config/schema/run_policy.py:14`）に、マップ `ignoredSettings` を 1 つ追加します。

```yaml
targets:
  iphone-device:
    run:
      ignoredSettings:
        erase: "実機はアプリのデータを保持する。このレーンは前回の状態を受け入れる"
        seedPhotos: "この端末の写真ライブラリは手で整えている"
```

| 規則 | 振る舞い |
|---|---|
| キー | 上の表のキー。未知のキーは設定の読み込み時に失敗します |
| 値 | 空白でない理由。レポートに表示します |
| ルートが対応しているか、満たしているキー | ルートを解決した時点（run の preflight と `bajutsu lint --config`）でエラーにします。古い宣言がリグレッションを隠さないようにするためです |
| 範囲 | そのターゲットだけ。無視の宣言は 1 つの環境に属するため、`defaults.run` には書けません |

シナリオには無視の宣言を書きません。
環境が何をできないかを知っている環境の持ち主が、そのトレードオフを受け入れます。
シナリオは、環境に依存しない 1 つのテストの定義のままです。

### run

実行の preflight（`bajutsu/common/runner/pipeline.py` の `_preflight_targets` と単一ターゲットの経路）に、BE-0082 のステップの検査と並べて検査を 1 つ加えます。
ターゲットごとに、シナリオが有効にする設定を解決し、そのターゲットの対応表で引きます。

- `supported` か `satisfied`：指摘しません。
- `unsupported` で宣言あり：適用せず、無視した設定として記録します。
- `unsupported` で宣言なし：デバイスを使う前にシナリオを失敗させます。理由には、設定、ターゲット、そしてその設定を無視する宣言の書き方を示します。

`RunResult` に `ignored_settings: list[IgnoredSetting]` を追加します。
各要素はターゲット、設定のキー、理由を持ちます。これは `skipped_captures` がすでに使っている形（`bajutsu/common/orchestrator/types/run_result.py:83`）で、2 つのターゲットが同じ設定を別の理由で無視しても、両方がレポートに残ります。
各レポートは、判定を変えずに次のように描画します。

- マニフェストには `ignoredSettings` として書き出します。
- HyperText Markup Language（HTML）レポートは、run の環境の隣に一覧を表示します。
- JUnit は `bajutsu.ignoredSettings` プロパティに記録します。
- Common Test Report Format（CTRF）は、テストの `extra` に入れます。

ステップの扱いは現状のままです。BE-0082 の preflight は、ターゲットが実行できないステップを含むシナリオを失敗させます。
この項目は、ステップに修飾子を追加しません。

### lint

`bajutsu lint` に `--config <path>` と `--target <name>` を追加します（`bajutsu/cli/commands/lint.py`）。
これらを付けると、lint は run と同じ方法でターゲットを解決します。

- `targets` を自分で宣言しているシナリオには `--target` は要りません。lint は宣言された各ターゲットを解決します。
- 宣言していないシナリオは、指定されたターゲットを使います。そうしたシナリオで `--target` を付けずに `--config` だけを渡すと、終了コード 2 で終わります。フラグの付け忘れで指摘が隠れないようにするためです。

続いて lint は、展開後のシナリオをターゲットごとに 2 通りの方法で検査します。
1 つは、そのターゲットへ振り分けられたステップに対する BE-0082 のステップの検査で、もう 1 つは上の設定の検査です。
あわせて、現状は run だけが呼ぶ `check_preconditions`（`bajutsu/common/config/preconditions.py:230`）も実行します。
各指摘は場所を示し、コンポーネントの中の指摘は、それを取り込んだ `use:` の行に表示します。
`--config` がない場合、lint は現状と同じに振る舞います。

serve のエディタの lint（`bajutsu/serve/operations/lint.py`）は、エディタで選んでいるターゲットを `/api/lint` のリクエストに含めて送ります。
サーバーは、そのリクエストに結び付いた設定でターゲットを解決します。
ターゲットを選んでいない場合、エディタの lint は新しい指摘を出しません。

### 振り分け

serve はジョブを capability のトークンで振り分けます（BE-0166）。
`can_serve`（`serve/capabilities.py:178`）は、ジョブが必要とするトークンをすべて掲げる worker にジョブを渡します。
この項目は、トークンを 2 種類加えます。

| トークン | ジョブが必要とする条件 | worker が掲げる条件 |
|---|---|---|
| `cap:<route>:<capability>` | `<route>` のターゲットへ振り分けたステップが、その capability を必要とします | worker が `<route>` を駆動でき、そのルートが capability に対応しています |
| `setting:<route>:<key>` | `<route>` のターゲットで設定が有効で、ターゲットがその設定の無視を宣言していません | worker が `<route>` を駆動でき、そのルートが設定に対応しているか、満たしています |

各トークンは、動機の表の列にあたるルート名（`ios-simulator`、`ios-device`、`ios-live`、`android`、`web`）を含みます。
iOS の 3 つのルートは対応する設定が異なるため、プラットフォーム名だけでは足りません。
worker は、駆動できるルートごとに集合を 1 つ掲げます。
そのため、Simulator と接続した iPhone の両方を扱う Mac が、実機のルートで `erase` を求めるジョブを受け取ることはなく、実行できる Simulator のジョブを断ることもありません。
2 つのルートの集合の共通部分を取ると、Simulator の `setLocation` が消えてしまいます。

`required_capabilities` は、展開後のシナリオとターゲット設定からジョブのトークンを導きます。
`worker_capabilities`（`bajutsu/serve/capabilities.py:195`）は、worker のトークンを `worker.yaml` の環境とドライバ（BE-0450）から導きます。
導くときは、同じ対応表と `capabilities_for_run` を通します。
そのため、`local` の iOS worker は Simulator の集合を掲げ、`worker.yaml` に実機があれば実機のルートの集合も掲げます。
`appium` の worker はライブのルートの集合を掲げ、そこには `cap:ios-live:deviceControl.setLocation` が含まれません。

生きているどの worker も掲げないトークンを必要とするジョブは、キューに残り、`bajutsu_unroutable_jobs`（BE-0166）に数えられます。そのため、コールドスタート中や worker の再起動中に投入しても拒否されません。
投入時点で失敗するのは、ジョブのルートを駆動する worker が生きているのに、そのどれもジョブが必要とするトークンをすべては掲げていない場合だけです。
失敗には、足りないトークンと、それを必要とするステップや設定を列挙します。
worker の環境は登録から受け取りまでの間に変わりうるため、worker はジョブを受け取ったとき、デバイスを借りる前に同じ検査をもう一度行います。

`cap:` と `setting:` は、どちらも予約済みのルーティング接頭辞（`reserved_routing_tokens`、`bajutsu/common/capability/worker_capability.py:61`）に加えます。
これらのトークンは worker 自身が計算し、非推奨の `--capabilities` による上書きには含められません。そのため、運用者が手で書いたトークンで、ルートが持たない capability を名乗ることはできません。

この項目より前の worker は、`cap:` や `setting:` のトークンを掲げません。
サーバーはそうした worker を現状のトークンだけで照合し、扱えるものとして数えます。そのため、更新の途中でキューが止まることはありません。
その worker はこの項目より前のコードで動くため、現状の BE-0082 のステップの preflight と起動時の例外は持ちますが、この項目の設定の検査は持ちません。そのため、更新するまでは黙って無視する処理が残ります。
この項目は、移行期間中のこの残る欠落を受け入れたうえで、見える形にします。サーバーは、検査を持たない worker が実行したことをジョブに記録し、レポートにもその旨を表示します。

### 証跡の種類

`video`、`deviceLog`、`appTrace` は、エラーの規則の対象外とします。
証跡は判定を決めないため、記録できないルートでシナリオを失敗させるべきではありません。
それでも、記録できなかったことは見えなければなりません。
`resolve_evidence_providers`（`bajutsu/common/backends.py:631`）が skip の理由を計算するのは、`KIND_CAPABILITY`（`backends.py:589`）にある種類だけで、現在は `network` しかありません。
しかも、`bajutsu/common/runner/pool.py:325` がその理由まで捨てています。
この項目では、その理由を lease の `skipped_captures` に残します。レポートはそれをすでに描画できます。
あわせて `video`、`deviceLog`、`appTrace` にもそれぞれ capability を与え、同じ解決処理で答えられるようにします。

### 対象外

- **Android のエミュレータと実機の区別。** Android のターゲット設定には両者を区別するフィールドがないため、同じ対応表を共有します。後続の項目がフィールドを追加すれば、行を分けられます。
- **実機向けの代替の分岐。** 別の環境で別のステップを実行するには `if` の拡張が必要です。その拡張はシナリオが実行するステップを変えるため、この項目の方針に反します。
- **ライブ WebDriver の配線。** この項目がライブのルートに与える行は、`xcuitest_live.py` が現状で配線しているものを反映します。配線を増やすのは BE-0238 の作業で、配線するたびにその行が `supported` に変わります。

## 検討した代替案

| 代替案 | 概要 | 採らなかった理由 |
|---|---|---|
| 環境ごとにステップを飛ばす（BE-0457） | ステップ修飾子 `skipOnRealDevice` / `skipIfUnsupported` | 同じシナリオの結果が実行した場所によって変わります。検査が減ったことで合格してしまいます |
| シナリオに無視の宣言を書く | シナリオファイルの設定の隣に理由を書きます | シナリオが環境に依存するようになり、作者が見たこともない環境について判断することになります |
| `worker.yaml` に無視の宣言を書く | 各 worker が無視してよい設定を述べます | 同じターゲットの振る舞いが worker ごとに変わり、worker を持たない lint では判定を知りえません |
| 失敗させず警告にとどめる | 未対応の設定を報告して実行を続けます | 警告は判定を変えないため、黙って壊れた前提条件が合格し続けます |
| 受け取り時の拒否だけにする | 振り分けは現状のまま、worker が実行できないジョブを断ります | ジョブは実行できない worker を待ち、そこへ届いてしまいます。利用者が気付くのが遅れます |

worker 単位の宣言は、1 つのターゲットを本当に異なる環境の worker 群が扱うようになった時点で、再検討する価値があります。

## 進捗

> 作業の進行に合わせて更新してください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `settings_support.py` を追加する。設定のキー、3 つの対応状態、ルートごとの表を持ち、権限の行をステップの検査から移し、`erase_precondition_supported` を置き換える
- [x] `RunPolicy` に `run.ignoredSettings` と読み込み時の規則を追加する
- [x] 実行の preflight に設定の検査を追加し、各環境が独自の条件分岐や黙って無視する処理の代わりに、`supported` の設定を適用し、無視されていない未対応の設定で例外を投げるようにする
- [ ] `RunResult.ignored_settings` を追加し、マニフェスト、HTML、JUnit、CTRF の各レポートで描画する
- [ ] `video`、`deviceLog`、`appTrace` にそれぞれ capability を与え、`pool.py` で捨てている証跡の skip の理由を lease に残す
- [ ] `bajutsu lint` に `--config` と `--target` を追加し、展開後のシナリオに対してターゲットごとにステップの検査、設定の検査、`check_preconditions` を行う
- [ ] serve のエディタの `/api/lint` リクエストで、選択中のターゲットを送る
- [ ] ジョブの必要な集合と worker の掲げる集合に `cap:` と `setting:` のトークンを加え、両方の接頭辞を予約し、生きているどの worker も扱えないジョブはキューに残し、ジョブのルートを駆動する worker が生きていてもどれもトークンをすべては掲げていない場合だけ投入時点で失敗させる。この項目より前の worker は現状のトークンで照合し、検査なしで実行したことをジョブに記録する
- [ ] worker がジョブを受け取ったとき、デバイスを借りる前にステップと設定の検査をもう一度行う
- [ ] 対応表、宣言、lint のフラグ、振り分けのトークンを `docs/` と `docs/ja/` に記載する（`configuration.md`、`cli.md`、`drivers.md`、`scenarios.md`）。`DESIGN.md` と `docs/architecture.md` を更新する

ログ：

- [#2249](https://github.com/bajutsu-e2e/bajutsu/pull/2249) — 単位 1〜3。`bajutsu/common/capability/settings_support.py` を追加しました。ルートごとの表、検査、起動時の歯止めを持ちます。`permissions` の行を BE-0082 のステップの検査から移し、`erase_precondition_supported` を表の参照に置き換えました。`RunPolicy` に `run.ignoredSettings` を加え、`defaults` では書けないようにしました。実行の preflight は、lease の前に各ターゲットの設定を検査します。iOS Simulator、実機、ライブ WebDriver、Android、Web の起動処理は、独自の条件分岐の代わりに表を読みます。デバイスグループのメンバーも同じです。
- 設計の記述から外れた点が 4 つあります。1 つ目に、Android の `launchArgs` は `unsupported` としました。Android には `app.launch.args` がなく、`am start` も引数を受け取らないためです。2 つ目に、シナリオの `app.launch` の値は、そのフィールドを持つプラットフォームのターゲットでだけ有効とみなします。BE-0452 の扱いに合わせたもので、iOS と Web を組み合わせたシナリオの deeplink で Web ターゲットが失敗することはありません。3 つ目に、実機の `locale` は起動引数としてアプリへ届くので `supported` としました。4 つ目に、ライブのルートは `extra_env` の拒否を表の外に残しました。この値は設定ではなく、Bajutsu 自身が注入するものだからです。同じ理由で、実行時に Bajutsu が起動環境へ書き込む `BAJUTSU_*` のキー（`BAJUTSU_MOCKS` など）も `launchEnv` の設定とはみなしません。ライブのルートは、これらのキーを起動時に引き続き拒否します。
- Web ターゲット自身の `app.launch.env` も有効とみなしません。スキーマ上、この値は Playwright のコード生成のためのものだからです。一方、シナリオ側の値は有効とみなすので、showcase の `web` ターゲットは `cross_platform_app_web.yaml` のために `launchEnv` の無視を宣言しました。`record`、`crawl`、`repl`、`doctor` は、拒否された設定を通常のエラーとして報告します。
- 単位 4 でレポートに表示するまでの間、起動処理は無視した設定ごとに警告をログへ出します。`docs/configuration.md` とその日本語版に `run.ignoredSettings` を記載しました。

## 参考

- `bajutsu/common/platform_lifecycle/environments/`：各環境が現状で設定を適用または拒否している箇所（`xcuitest/xcuitest_environment.py:352-372`、`xcuitest_live.py:73-125`、`android/android_environment.py:186-243`、`web.py:40-61`）
- `bajutsu/common/backends.py:270-350`：`capabilities_for_run` と `erase_precondition_supported`
- `bajutsu/common/capability/capability_preflight.py`：`_REQUIREMENTS` と `unsupported()`
- `bajutsu/common/config/preconditions.py:230`：現状は run だけが呼ぶ `check_preconditions`
- `bajutsu/common/config/schema/run_policy.py:14`：`RunPolicy`
- `bajutsu/common/lint.py`、`bajutsu/cli/commands/lint.py`、`bajutsu/serve/operations/lint.py`：設定を読まない現状の lint
- `bajutsu/serve/capabilities.py:161-215`：ジョブと worker のルーティングトークン
- `bajutsu/common/runner/pool.py:325`：捨てられている証跡の skip の理由

[English](../evidence.md) · **日本語**

# 証跡（Evidence/Trace）サブシステム

繰り返し発生する動作の[証跡](glossary.md#証跡-capturepolicy-trace-triage)は、単発の指示ではなく **繰り返し発火するルール**として表現します。こうすると、2 回目以降も AI なしで同じ証跡が集まります。

実装: `bajutsu/evidence/core.py`（瞬時 + Sink）、`bajutsu/evidence/intervals.py`（区間: video / deviceLog / appTrace）。発火判定は orchestrator 側（[run-loop](run-loop.md#証跡ルールの発火)）で行います。

関連: [scenarios の capture トークン](scenarios.md#capture-トークン文法) · [reporting](reporting.md)

---

## 証跡の指示方法（3 つ）

| 方法 | 用途 | 例 |
|---|---|---|
| **A. ルール（`capturePolicy`）** ★中心 | 「特定動作の **たびに**」自動取得 | `settings.*` を tap するたびにネットワーク通信 |
| **B. ステップ単体（`capture:`）** | この 1 ステップだけ | 特定の wait 後に video + deviceLog |
| **C. 既定ポリシー** | 全体の最低保証 | config の `capture: [screenshot.after, elements, actionLog]` |

> C（config 既定）の `capture` は `Effective.capture` に解決されます（[configuration](configuration.md)）。この値は、シナリオの `capturePolicy` やステップごとの `capture` と並んで、すべてのステップに適用されます。他の2つとは異なり、条件によって発火するのではなく常に適用されるため、ルールではなく最低保証として働きます。
>
> この 3 つはいずれも、各ステップが動作の前後ですでに記録している証跡に**上乗せ**して指示するものです。
> したがって、どれかで `screenshot.after` や `elements` を挙げても結果は変わらず、挙げなくても失うものは
> ありません。`before.png` と `after.png`、そして動作後の `elements.json` は、3 つが何を要求したかに
> よらず取得します（後述）。

## 証跡種別と取得タイミング

`capture:` トークンは `<種別>[.<修飾子>]`（[scenarios](scenarios.md#capture-トークン文法)）。

| 種別 | 取得元 | 区間 / 瞬時 | 現状 |
|---|---|---|---|
| `screenshot` | ドライバ（XCUITest 自身の `/screenshot` エンドポイント、`adb` の `screencap`、Playwright はネイティブに取得） | 瞬時 | ✅ 取得 |
| `elements`（a11y＝アクセシビリティのツリー） | `driver.query()` を JSON 化 | 瞬時 | ✅ 取得 |
| `actionLog` | orchestrator 内部（操作と所要時間）と、各ドライバ自身の actuation の記録 | — | ✅ manifest に内在 |
| `video` | `simctl io recordVideo` | 区間 | ✅ 取得（要 udid） |
| `deviceLog` | `simctl spawn log stream` | 区間 | ✅ 取得（要 udid） |
| `network` | アプリ内 collector（BajutsuKit → `network.json`） | 区間 | ✅ 取得（`--network` フラグ） |
| `appTrace` | `simctl spawn log stream`（アプリの os_log subsystem） | 区間 | ✅ 取得（要 udid + subsystem） |
| `rawTree` | `elements` の元になった、bajutsu の加工を経ていない生の応答（`base.RawSourceProvider`。現状 adb と XCUITest） | 瞬時 | ✅ 取得（opt-in。他のバックエンドでは何もしません） |

> `appTrace` はアプリの `os_signpost` / `os_log` が出す `<name> started` / `<name> finished` マーカーを、時刻つきの区間にペアリングします（`intervals.parse_app_trace`）。`network` は区間システムではなく request collector が生成し、その exchange を `<sid>/network.json` に書き出します（[network observation](drivers.md)、`--network` フラグ）。

> `elements.json` の各要素は、`identifier`、`label`、`traits`、`value`、`frame` に加えて、診断用のフィールドを1つ持ちます。テスト対象のアプリ自身が測った、その要素の実際の前後位置を表す `nativeZ` です（[BE-0355](../../roadmaps/BE-0355-native-z-position/BE-0355-native-z-position-ja.md)）。このフィールドを読んでも、実行の判定は何も変わりません。`nativeZ` でマッチするセレクタはなく、重なりの判定（`is_tappable`、`topmost_at_point`、XCUITest 自身の `isHittable`）もフィールドが存在しなかったときとまったく同じように振る舞います。
>
> **数値の意味はバックエンドごとに異なり、バックエンドをまたいで言えるのは向きだけです。** どちらのバックエンドでも、`nativeZ` が大きいほど手前にあります。それ以外は、同じバックエンドから得た2つの値どうしでなければ比較できません。iOS はアプリの実際の合成順を奥から数えた序数を報告します。`CALayer.zPosition` は通常の平坦なレイアウトではすべて 0 になり、Apple 自身が兄弟レイヤーの順序を決める用途には使うべきでないと明記しているためです。Android は `View.getZ()`、つまり elevation とZ軸方向の移動を合わせた値を device pixel で報告します。この値が決めるのは同じ親を持つ兄弟同士の順序だけで、それより広い範囲は決めません。`getZ()` が 8 の親の下にある 0 の子は、その親の兄弟である 4 の要素より依然として手前に描かれます。したがって2つの数値の大小が前後関係を表すのは、Android では兄弟同士のときに限られます。iOS では、同じ読み取りから得た値どうしなら、序数が画面全体に及ぶため常に比較できます。どちらの場合も、別のバックエンドの実行結果とのあいだでは表しません。
>
> **値が `null` になるほうが一般的で、これは推測で埋めるべき欠落ではなく意図した表現です。** 実際の値を報告するには、アプリ側の opt-in が要ります。iOS では BajutsuKit をリンクすること、Android ではデバッグビルドで `BajutsuZOrder.report(view)` を呼ぶことです。iOS ではさらに、bajutsu 自身の XCUITest runner 経由で駆動している必要があります。WebDriverAgent 経由の live/record 経路はレスポンダが待ち受けるポートを注入しないため、opt-in 済みのアプリでも `null` のままです。opt-in しても値を報告しない UI ツールキットが2つあります。どちらも自身でアクセシビリティ要素を生成し、位置の測定元となる実体を外に出さないためです。**SwiftUI** はアクセシビリティ要素を、支援技術がプロセスへ接続したときに初めて実体化するので、アプリが自分のビューツリーを見ても識別子はどこにもありません。**Jetpack Compose** は、アプリが宣言した追加データキーを自身のノード生成に通しません。UIKit と Android の `View` で書かれた画面は値を報告し、SwiftUI と Compose の画面は `null` になります。要素の並び順から位置を導き出す方法（`topmost_at_point` が代替として使っている描画順の近似）を採れば、権威ある値のように見えて、調査者が証跡を開くまさにその場面で間違った値を返します。たとえば Android の `elevation` によって、後に宣言された兄弟要素より手前に持ち上げられたビューが、そうした場面にあたります。

> `rawTree` は、デバイス・runner 自身が返した応答をそのまま `hierarchy.raw<suffix>` として書き出します。adb なら `uiautomator dump` または resident channel の XML（`.xml`）、XCUITest なら `GET /elements` の未デコードのボディ（`.json`）で、いずれも bajutsu 側の加工を経ていません。adb の resident channel で narrowing が何かを変えた場合に限り、パーサーが実際に消費したデータ（SystemUI の装飾ウィンドウを取り除いた後のダンプ）も `hierarchy.parsed-input<suffix>` として併せて書き出します。XCUITest はこの種の変換を一切行わないため、2 つ目のファイルを書き出すことはありません。狙いは、解決した座標と実際の画面がずれたときに、デバイス側・runner 側の応答がすでにおかしかったのか、bajutsu 側のパースで変わったのかを切り分けることです。既定の capture リストには含まれず、シナリオが `capture: [rawTree, ...]` で明示的に指定したときだけ取得します。
>
> `redact.labels` を設定した場合に限り、この redact の規則が `rawTree` の出力を拒否します。実行全体を通じて何も書き出さず、その理由をログに出力します。`redact.labels` は、ラベルが設定された要素の値を構造的にマスクします。`elements.json` はパース済みのツリーから書き出すため、どの値を隠すべきかを書き出し側が把握しているからです。これに対して生ダンプは構造を持たないフリーテキストなので、同じマスクを構造的には適用できず、`elements.json` がすでにマスクした内容を無防備な形で書き出してしまいます。それ以外の redact の規則（`headers`、`fields`、解決済みの secret 値）は、生ダンプにもフリーテキストとしてそのまま適用され、`rawTree` の取得を妨げません。ただし `redact.headers` と `redact.fields` には一つ注意点があります。これらのキーパターンによるマスクは、マッチした値が次の改行で終わる複数行のログを想定して書かれていますが、UI Automator のダンプは 1 行として出力されます。設定したキーがダンプ自身のテキスト（画面上のラベルや、`Token: ...` のように読める `content-desc` など）にたまたまマッチすると、マッチした値だけでなく、そこから先のファイル全体がマスクされてしまいます。ダンプ自体は書き出されますが、末尾が欠けた状態になります。`${secrets.X}` で束縛した解決済みの secret 値は、キーパターンではなく既知のリテラルとの一致でマスクするため、この問題の影響を受けません。

### 各ステップが画面に対して実際に行ったこと（`actionLog`）

`actionLog` は取得の要求を必要とせず、独自のファイルも書きません。すべてのステップの結果が `manifest.json` の `actuations` に、ドライバが行ったプリミティブごとに 1 件を持ち、レポートと `bajutsu trace` のタイムラインはそこから読みます。これは、スクリーンショットと要素ツリーでは答えられない問いに答えます。このタップはどこに着地したのか、このスワイプはどれだけ動いたのか、という問いです。

| フィールド | 意味 |
|---|---|
| `gesture` | ドライバのプリミティブ。`tap`、`doubleTap`、`longPress`、`swipe`、`scroll`、`pinch`、`rotate`、文字系のプリミティブ、`selectOption`、`setPickerValue`、`systemAlert`、`back` |
| `via` | ジェスチャが対象へ届いた方法。`coordinate`（ドライバが点を計算して送った）、`handle`（XCUITest がスナップショットの handle を操作した）、`identity`（Android の端末が要素を解決して点を選んだ）、`bridge`（WebView を要素 id で呼んだ）、`focused`（フォーカスを持つ入力欄に対する文字系のプリミティブ）、`key`、`history` |
| `unit` | 座標系。`point`（iOS）、`pixel`（Android）、`cssPixel`（ブラウザのページ、または WebView 自身の空間） |
| `points` | ドライバが送った座標を順に並べたもの。タップは 1 点、ドラッグは始点と終点の 2 点です。二本指のジェスチャでは、2 つの接触点そのものではなく、そこから導いた単一のアンカーを記録します |
| `frame` / `target` | 解決した要素の領域と、そのアクセシビリティ識別子 |
| `accepted` | その試行をプラットフォームが受け入れたかどうか。答えを返す 2 つの経路（XCUITest の handle 操作と Android の端末側エンドポイント）で設定されます。拒否された試行は打ち消し線で表示されるので、stale で再試行したタップが複数回のタップには見えません。`None` はその経路が個別の答えを返さなかったことを意味します |
| `substitution` | 操作した要素が、ドライバの既定の規則が指すはずだった要素と異なる理由。拒まれたタップが、frame の内側にある到達可能な名前付きの子孫 1 つへ振り向けられたときに `soleHittableDescendant` になります。読み手はどちらも表示します。レポートは `via` の隣のバッジとして、`trace` のタイムラインは `↷<トークン>` として出します。通常の経路では付きません。`schemaVersion` 7 より前に記録された実行にも付かず、どちらも「置き換えは起きていない」と読めます |
| `duration_s` / `scale` / `radians` | ジェスチャが持つ場合の、位置以外のパラメータ |

記録が書ける内容には 3 つの規則があり、すべてのバックエンドがこれを守ります。

- **実際に送った座標だけを書きます。** プラットフォームへ座標が渡っていない場合、`points` は空です。handle 経由の iOS のタップや Android の端末側のジェスチャでは、点を選んだのが向こう側だからです。そのときは代わりに解決した `frame` を示し、取っていない測定値として frame の中心を差し出すことはしません。
- **端末側の処理を増やしません。** 記録が持つ値はどれも actuator がすでに手にしていた値なので、query も read も往復も追加で発生しません。
- **著者が書いた文字列は決して持ちません。** `manifest.json` は秘匿処理を通さずに書き出されるので、記録は `type` ステップのテキスト（文字数さえ持ちません。`Redactor` が固定長のプレースホルダを使うのは、秘匿情報の長さがどの成果物にも出ないようにするためです）、`selectOption` の option、`setPickerValue` の value、要素のアクセシビリティ label のいずれも持ちません。`target` は常に解決したアクセシビリティ識別子だけなので、識別子を持たない要素では未設定になります。

記録はジェスチャを「試行した」時点、つまり通信が答える前に書きます。そのため操作に失敗したステップでも、何を狙ったかは残ります。ステップが動いたかどうかを語るのはステップ自身の結果です。

どのステップにも属さない actuation が 1 つあります。反応型のシステムアラートガードはシナリオ末尾の `expect` の再チェックの前にも発火するので、その記録は `expect_alerts` の隣、シナリオの `expect_actuations` に載ります。記録を実装していないバックエンドは何も持たず、run の挙動は変わりません。ドライバの蓄積器には上限があるので、極端なステップ（`maxScrolls` が数百のような場合）は最初のほうの記録を失うことがあります。レポートがマニフェストを読み戻す際に、壊れた記録が同じように失われることもあります。`dropped_actuations` はそのどちらの件数も数え、記録を完全なものとして示すことを防ぎます。

**修飾子の既定**：常時発火するベースライン（後述）は `before` です。ステップが動作したあとではなく、動作する前に取得します。`capturePolicy` ルールやインラインの `capture:` が発火したときは、修飾子なしの瞬時種別は従来どおり `after` が既定のままです。区間系（`video`/`deviceLog`）は `around`（操作前に開始し、ステップ後に停止）です。ルール・インラインで `screenshot.before` を明示しても、ベースラインと重複するため撮り直されません。

## A. `capturePolicy`（ルール方式）

繰り返し発火するルールです。シナリオ単位で記述します（実装: `scenario/models/evidence.py` `CaptureRule` / `Trigger`）。

```yaml
capturePolicy:
  # settings.* を tap するたびに、ネットワーク通信も追加で取得する。スクショと要素は config の
  # 既定ポリシー（上の C）が全ステップに既に保証している
  - on: { action: tap, idMatches: "settings.*" }
    capture: [network]

  # 画面遷移のたびに
  - on: { event: screenChanged }
    capture: [screenshot.around, elements]

  # どのステップでもエラー時は最大限の証跡（安全網）
  - on: { result: error }
    capture: [screenshot, video, deviceLog, elements, actionLog]
```

トリガー `on` は **`action` / `event` / `result` のいずれか 1 つ**です。

- `action: <tap|longPress|type|swipe|...>`：任意で `idMatches`（主対象の `id` に glob 一致）を併用できます。`idMatches` は `action` とのみ併用できます。
- `event: screenChanged`：そのステップで `query()` が変化したら発火します。
- `result: error`：ステップが失敗したら発火します（安全網）。

発火の詳細ロジックは [run-loop](run-loop.md#証跡ルールの発火) にあります。

> **実行前に発火を確認する（BE-0028）。** 緩い glob や `screenChanged` ルールは、意図より多くの
> ステップで発火しがちです。そこに heavy capture（`video` / `deviceLog` / `appTrace` / `network`）を付けると、
> 気づかぬうちにギガバイト級の証跡を生みます。`bajutsu trace --explain <scenario.yaml>` は読み取り専用のドライランで、
> 各ルールが何回（どのステップで）発火するかを数え、広くマッチするルールの heavy capture を ⚠ で
> 警告します。コストを払う前にマッチを絞り込めます。詳細は [cli](cli.md#trace)。

## B. インライン証跡

特定の 1 ステップだけ証跡を取りたい場合は、そのステップに直接 `capture:` を付けます。

```yaml
- tap: { id: settings.reindex }
- wait: { for: { id: settings.reindexComplete }, timeout: 5 }
  capture: [video, deviceLog]     # この wait の区間を録る
```

（[`demos/showcase/scenarios/evidence.yaml`](../../demos/showcase/scenarios/evidence.yaml) に実例）

## 区間証跡（video / deviceLog / appTrace）

実装: `bajutsu/evidence/intervals.py`。これらは **子プロセス**であり（iOS は `simctl`、Android は `adb`）、操作前に開始し、ステップが落ち着いてから停止します。プロセス起動は注入可能（`Spawn`）で、テストできます。

web は子プロセスを使いません。区間証跡は Playwright ネイティブで、driver が供給します（後述）。`appTrace` も video / deviceLog と同じ区間系です（ペアリングの仕組みは前掲の注を参照）。

> **区間証跡は opt-in です（BE-0028）。** `video` / `deviceLog` / `appTrace` は重いため、シナリオが
> **その kind を要求したときだけ**記録します。要求の経路は、インライン `capture:` か `capturePolicy` ルール
> （例: `video` を取得する `result: error` ルール）です。何も要求しなければ何も記録せず、通常ケースを
> 安価に保ちます。軽量な瞬時の baseline（`screenshot` + `elements`）は常に取得するので、失敗時も証跡が
> 残ります（DESIGN §10）。証跡は、どのステップでも動作の**前後の両方**で取得します。
> 動作の前に取る `before.png` と `elements.json` は、そのステップが動作の対象とした画面を示し、
> 動作のあとに取る `after.png` と 2 度目の `elements` は、その動作が残した画面を示します。前後の
> どちらも `capture` の指定によらないため、`capture` を絞っても、ステップから 2 枚のスクリーンショットも
> 動作後のツリーも失われることはありません。`elements.json` のファイル名は 1 つなので、動作後の取得が動作前のツリーを
> 置き換えます。実行のあとに残るツリーは、動作が生んだ画面を表します。これは `after.png` が写す画面であり、
> ビューアが要素の枠を描く基準とする画面でもあります。
> 何が記録されるかは `bajutsu trace --explain` で事前に確認できます
> （[cli](cli.md#trace) 参照）。

| 種別 | 開始コマンド（iOS / Android） | 停止シグナル | ファイル名 |
|---|---|---|---|
| `video` | `simctl io <udid> recordVideo --codec h264` / `adb shell screenrecord` | **SIGINT**（強制 kill だと mp4 が壊れる） | `scenario.mp4` |
| `deviceLog` | `simctl spawn <udid> log stream --level debug --style compact [--predicate ...]` / `adb logcat -b main,system,crash,events -T 1` | SIGTERM | `device.log` |

- iOS は `start_video` / `start_device_log`、Android は `start_screenrecord` / `start_logcat` が `Interval` を返し、`Interval.stop()` がシグナルを送ってファイルを確定します。`deviceLog` の停止は最大 10 秒待ち、超えたら kill します。`video` は停止から kill までに 120 秒という余裕のある確定待ちを取ります。`recordVideo` / `screenrecord` はクリップ全体をディスクへ flush して mux し終える必要があり、途中で kill すると mp4 が壊れ（`moov` atom を持たないファイルになり）、iOS では Simulator の録画セッションが解放されずに残ってしまうためです。
- `screenrecord` はデバイス側に録画するので、その `Interval` は停止時に確定した mp4 を `adb pull` で回収し、デバイス側のコピーを削除します。pull が失敗した場合（デバイスが消えたなど）、Sink は実体のないパスを記録せず、その 1 件だけを警告つきで捨てます。区間証跡の確定処理の I/O で、通過するはずのシナリオを失敗させません。
- なお `adb screenrecord` は 1 回の録画を約 180 秒（プラットフォームの既定／上限であり、bajutsu が調整できるものではありません）で打ち切るので、それより長いシナリオの Android 動画はその時点で終わります。
- deviceLog は iOS では `--predicate`（NSPredicate）でサブシステムなどに絞れます（CLI の `--log-predicate`）。`adb logcat` はタグや優先度で絞り込みません（logcat の filterspec は別の構文で、後続の knob です）。取得はリングバッファ全体ではなくシナリオの区間を反映するよう、末尾から追従を始めます。バッファは `logcat` 単体の既定（`main,system,crash`）より広げ、`events` を加えています。アプリ自身の未捕捉例外は `crash` に記録されます。`ActivityManager` がメモリ不足を理由にプロセスを kill した場合は、`events` に `am_kill` や `am_low_memory` という構造化エントリが記録されます。この記録は `events` にしかありません。`events` を加えなければ、この原因は取得漏れによる失敗と区別できません。カーネル自身の OOM や low memory killer の経路はカーネルのリングバッファに記録されます。logd が `ro.logd.kernel` で `/proc/kmsg` を取り込んでいる環境（主に userdebug ビルド）なら `logcat -b kernel` で読めますが、環境に依存するのでここでは加えていません。
- `INTERVAL_KINDS = {"video", "deviceLog", "appTrace"}`。orchestrator はこの集合で「区間 / 瞬時」を振り分けます。
- **シナリオ全体の `video` は、Android ではアプリの起動より前に開始します**。録画がアプリの起動（コールドスタート）を取りこぼさず含むようにするためです。環境の `start` が録画を開始し（デバイスの boot とアプリの install の後、`am start` の前）、動いている `Interval` を `prestarted_intervals` で返します。Sink はシナリオ開始時にこの録画を新たに開始し直さず引き取り（`intervals.adopt`）、停止時に確定してファイルを `scenario.mp4` へ移します。web も同じ前倒しの取得をブラウザコンテキストの生成時に組み込みます。現在の iOS バックエンドである XCUITest は、代わりにオンデマンドで録画します。`xcodebuild` ランナーが起動してアプリを立ち上げるまでのあいだに録画を始める処理がどこにもないため、`prestarted_intervals` は常に空です。この前倒しの録画は `records_video_up_front` で制御します。`True` を返すのは Android と web だけです。`video` を要求しないシナリオは、いずれのバックエンドでも何も開始しません。
- **確認済みの開始時刻が、レポートのステップ/通信ログのタイムスタンプを、単に録画を要求した瞬間ではなく動画の実際の開始に合わせて補正します。** `start_video`（iOS）と`start_screenrecord`（Android）は、本番の呼び出し箇所で`confirm_started=True`を渡されると、録画プロセスを起動したあとに実在の信号をポーリングします。iOSは出力ファイルの最初の書き込みバイトを、Androidはデバイス側のプロセスが現れることを確認します（後者はより弱い保証です。プロセスが存在しても、そのエンコーダーがすでにフレームを出力しているとは限りませんが、それでも実在の信号であり、当て推量よりは早い時点を示します）。確認できた`time.monotonic()`の値は`Interval.true_start`に記録されます。`intervals.adopt`は、事前録画されたインターバルを移すときも`true_start`をそのまま引き継ぐため、`adopt`が動く前に確認済みだったAndroidの値が失われることはありません。web アクチュエータはポーリングせず、録画対象のページが生成された直後に`true_start`を記録します。`record_video_dir`はコンテキスト配下のページに対する録画を有効にするだけで、動画そのものはページが生成されるまで存在しないため、記録するタイミングは`new_context()`ではなく`new_page()`の直後です。ポーリングが一度も確認できなかった場合は`true_start`が`None`のままとなり、基準時刻は`scenario_start`に戻ります。当て推量の数値が入ることはありません。

  このポーリングをどれだけ続けられるかが、ここで唯一調整できる値です。`simctl`と`adb`の起動のばらつきは、開発機よりも負荷の高い継続的インテグレーション（CI）のマシンで目に見えて大きくなります。ポーリングが諦めると、そのシナリオは補正を丸ごと失います。そのため上限を上書きできます。`BAJUTSU_VIDEO_START_TIMEOUT`（秒）が、コンパイル時のデフォルト値である5秒を置き換えます。[`.github/workflows/ios-e2e.yml`](../../.github/workflows/ios-e2e.yml)はiOSのレーンでこの値を引き上げ、すでに同じ仕組みを持つ3つの`BAJUTSU_XCUITEST_*`のタイムアウトと並べています。ポーリングは録画を確認できた瞬間に返るので、値を大きくしても正常な経路では何のコストもかかりません。
- **確定した録画そのものが原点を示し、その測定値が上の開始確認より優先されます。** `true_start`はいずれも**代理の信号**です。最初に書き出されたバイト、現れたデバイス側のプロセス、生成済みのブラウザページのいずれも、録画が最初のフレームを収めた瞬間からそれぞれ固有の距離だけ離れて届きます。レポートのシークは、その距離のぶんだけずれます。録画そのものが、この問いに答えられます。確定したクリップは自分の長さを申告し、`Interval.stop()`は録画が終わった瞬間を知っています。したがって、引き算がそのまま原点になります（`measured_start = ended_at - duration`）。長さは外部ツールを使わずコンテナから読みます。[`evidence/media.py`](../../bajutsu/evidence/media.py)が、`simctl`と`screenrecord`が書くムービーヘッダと、Playwright のレコーダーが書く Matroska のセグメントを読みます。

  「録画が終わった瞬間」がどちらを指すかは、レコーダーごとに違います。子プロセスによる録画は停止シグナルが届いた時点で終わり、そのあとの確定処理（Android ではデバイスからの pull も含みます）は、すでに収め終えた映像を書き出しているだけです。Playwright は`stop()`が行うコンテキストのクローズまで撮り続けます。どちらの形かは`Interval.stops_when_stop_returns`で宣言します。`run_scenario`は`finish_scenario_intervals`のあとで`video_start_offset`を求めます。`measured_start`があればその値を、なければ`true_start`を使い、結果を`RunResult.video_anchor_s`として記録します。

  この引き算の精度は2つの入力で決まり、それぞれに、もう一方からは見えない誤り方があります。1つは、長さが実時間の測定値とは限らないことです。実時間の測定値かどうかは、引き算ではなくレコーダーの性質で決まります。名目上のフレームレートで書かれたコンテナは、レコーダーが動いていた時間より長い秒数を申告し、Playwright の短いクリップが実際にそうなります。この場合、原点は起動より前へずれます。もう1つは、`ended_at`が録画の終わった瞬間とは限らないことです。レコーダーは自分で止まることがあります。Android の`screenrecord`は`SCREENRECORD_TIME_LIMIT_S`という自前の上限で終了します。その上限を超える長さのシナリオは、数分前に止まったレコーダーへ停止シグナルを送ることになります。この場合、原点は最初のフレームより後へ、その差のぶんだけずれます。

  この2つをまとめて抑えるのが`Interval.spawned_at`です。確認を必要としない唯一の時刻だからです。録画が最初のフレームを収める瞬間は、その起動時刻と、レコーダーの立ち上がりのために`BAJUTSU_VIDEO_START_TIMEOUT`がすでに許している上限との間にあります。この範囲の外にある原点は、2つの入力のどちらかがこの録画を表していないことを示すので、読み取り側が捨て、その録画は`true_start`による基準時刻を保ちます。
- **記録するタイムスタンプは絶対時刻であり、動画基準の相対値は描画する側がレポートを描くときに求めます。** `run_scenario`は`time.monotonic()`の値を取るのと並べて、実時刻の時計を一度だけ読みます。この2つ1組が、そのシナリオの基準時刻です。以降の単調増加時計の値`t`は、`scenario_wall_start + (t - scenario_start)`によって実時刻へ変換できます。各ステップの`started_at`も各通信ログの`startedAt`もこの形で記録するので、run のあとに残るのは、補正で加工済みの数値ではなく生の計時データです。`report.html`や`bajutsu trace`といった描画する側は、描画のときに`video_anchor_s`を引いて、その出来事を録画のタイムライン上に置きます。だからこそ、その位置は保存済みの run から計算し直せます。基準時刻の求め方を修正したあとも、シナリオを実行し直さず、描画し直すだけで各出来事が本来の位置に載ります（[BE-0348](../../roadmaps/BE-0348-absolute-timestamp-recording/BE-0348-absolute-timestamp-recording-ja.md)）。時間に関する*判断*は、これまでどおり単調増加時計だけを読みます。実時刻の時計は run の途中で過去へ戻ることがあり、wait のタイムアウトやステップの所要時間を壊すからです。記録される各フィールドがレポートの読み手にとって何を意味するかは、[reporting](reporting.md#manifestjson)を参照してください。

### 録画にタッチのマーカーを写す（`--touch-markers`）

録画にはジェスチャのもたらした結果がすべて写る一方で、ジェスチャそのものは写りません。そこで
`bajutsu run --touch-markers` は、テスト対象アプリが受け取ったタッチの位置にマーカーを描くよう
アプリに指示します。接点には半透明の円を出し、接点が動いた場合はその軌跡を残します。円も軌跡も、
タッチが押されているあいだは緑、離れたあとは赤で描きます。そのため静止したフレーム 1 枚を見るだけで、
写っている接点がその瞬間に起きている操作なのか、それとも残された跡なのかが分かります。マーカーは
アプリ自身のプロセスの中で描かれるため、録画にも各ステップの `after.png` にも同じように届きます。
描画のもとになるのはドライバが送った座標ではなく、アプリが取り出した `UIEvent` です。そのため
マーカーは、タッチが「届いた」ことの証跡になります。ドライバ側の座標の記録では示せない事実です。

有効にする前に知っておくべき性質が 3 つあります。

- **BajutsuKit をリンクしたアプリが必要です。** 描画は `BajutsuKit` の `BajutsuTouch` にあり、
  デモアプリはこれをすでにリンクしています。フラグはアプリの起動環境に `BAJUTSU_TOUCH_MARKERS=1`
  を設定するだけなので、BajutsuKit をリンクしていないアプリはこの変数を無視します。
- **マーカーは `CALayer` なので、アクセシビリティツリーには決して入りません。** レイヤーは
  `UIResponder` ではなく、アクセシビリティのプロトコルにも適合しないため、どのセレクタも解決先に
  できず、ジェスチャを飲み込むこともありません。この主張は 2 つの検査が支えています。1 つは
  `demos/showcase/scenarios/golden/golden_xcuitest.yaml` で、同じツリーのゴールデンを、マーカーを
  有効にした場合と無効にした場合の 2 回検証します。可視化が固定済みのコントロールを乱せば、
  オンデバイスの E2E で失敗します。もう 1 つは `tests/test_touch_markers.py` で、描画のコードが `UIView` に手を出せば
  失敗します。マーカーがアクセシビリティ上の表現を得てしまう道筋は、それだけだからです。
- **あるジェスチャの跡は、次のジェスチャが始まるまで残ります。** タイマーで消える仕組みは
  ありません。この性質があるからこそステップのスクリーンショットに跡が写り、同時に、フラグを
  有効にした run のスクリーンショットは無効の run と異なるものになります。画素を比較する場面では
  フラグを無効のままにしてください。Android のレーンが、画素を比較するレーンにかぎって OS の
  `show_touches` と `pointer_location` を無効にしているのと同じ判断です（`demos/showcase/android/Makefile`）。

マーカーは証跡専用です。どのアサーションも参照せず、素の `bajutsu run` ではフラグは既定で無効です。
ただしこのリポジトリ自身の iOS のレーンはこれを渡します。`.github/actions/bajutsu-e2e` と、showcase の
`run-swiftui` / `run-uikit` はマーカーを有効にして走るので、そこで失敗したときにジェスチャの着地点が
見えます。これが安全なのは、スクリーンショットを入力とするアサーションが `visual` だけだからです。
ほかの種別はアクセシビリティツリー、通信の記録、クリップボードのいずれかを読みます。

1 つだけ、自動的に無効になる組み合わせがあります。判定でスクリーンショットを比較するシナリオです。
`visual` アサーションが読むのは、まさにマーカーが描き込まれた画像ですから、`--touch-markers` は
そのシナリオを対象から外し、どれを外したかを標準エラー出力に示します。アプリと無関係な理由で
ベースラインが不一致になるのを避けるためです。マーカーはジェスチャに追従して動き、決まった領域に
収まらないので、マスクでは救えません。この除外が run の残りに影響しないのは、アプリがシナリオごとに
そのシナリオ自身の起動環境で終了と再起動を経るからです。除外されたシナリオはフックが導入されていない
プロセスで走り、同じ run の他のシナリオは変わらずマーカーを描きます。これ以上細かく、あるシナリオの
ジェスチャには描いて `visual` のステップだけ描かないという分け方は、現状ではできません。アプリへの
唯一の入力が起動環境であり、それはプロセスの生存期間中は固定だからです。

## Sink（証跡の出力先）

```python
class EvidenceSink(Protocol):
    def capture(self, driver, step_id, kinds, *, elements=None) -> list[Artifact]: ...   # ステップ後に瞬時を取得
    def wait_diagnostic(self, step_id, *, trace, elements) -> Artifact | None: ...       # 初回 wait のタイムアウト診断（後述）
    def start_scenario_intervals(self, scenario_id, kinds) -> list[Interval]: ...         # シナリオ全体の video / deviceLog / appTrace を開始
    def finish_scenario_intervals(self, scenario_id, started) -> list[Artifact]: ...      # 停止してファイルを回収
```

| Sink | 挙動 |
|---|---|
| `NullSink`（既定） | 何も書かない（run を副作用フリーに保つ） |
| `FileSink(run_dir, udid, log_predicate)` | `run_dir/<step_id>/` 配下に書き出す |

環境が起動前にすでに開始した録画（Android の `video`）は、新たに開始せず引き取ります。Sink は停止時に確定したファイルをシナリオのディレクトリへ移します。それ以外の区間証跡は、driver が `driver_interval` provider を供給していればそこから取得し（web の Playwright ネイティブなコンソール / 動画、Android の `adb` logcat）、供給していなければ `FileSink` は simctl の経路を使い、`udid` が無ければスキップします。CLI の `run` は `FileSink(runs/<runId>, udid=..., log_predicate=...)` を使用します（[cli](cli.md#run)）。

## 初回 wait のタイムアウト診断（BE-0231）

`wait for <要素>` がタイムアウトすると、`run_dir/<step_id>/wait-timeout.json` を **無条件で** 書き出します。`capturePolicy` とは独立しているため、どのルールも取得しないようなタイムアウトでも、なぜ発生したかを判断するのに必要な証跡が残ります。これは純粋な診断であり、判定の入力にはなりません（run の合否は、機械で検査できるアサーションだけから決まります）。

このファイルは自己完結しているので、リトライで緑になっても証跡が失われません。

| フィールド | 何を答えるか |
|---|---|
| `readiness` | 起動後の準備完了ゲートを通過したか、どのシグナル（`readyWhen` / `namespace` / `count`、あるいは通過せず `timeout`）で通過したか、そして画面が静止していたか（`settled`）です。「ゲートがコンテンツより先に返った」のか「コンテンツは描画されたが待機対象の要素が現れなかった」のかを切り分けます。`settled: false` は、ツリーがまだ変化している最中にゲートが返ったことを示します。合成されたタッチが取りこぼされるのはこの状態なので、「この wait の手前の操作が届いていない可能性がある」と読みます。取りこぼされたタップも配送済みとして報告されるためです。準備完了結果を持たないレーンでは `null` になります。 |
| `trace` | ポーリングの時系列です。何回ポーリングしたか、ツリーが最初に空でなくなった時刻（`firstNonemptySeconds`、一度も空でなくならなければ `null`）、タイムアウト時点で要素がいくつあったかを記録し、「何も描画されなかった / 一時的に空」「描画されたが待機対象の要素が無い」「コールドブートで描画が遅い」を切り分けます。 |
| `provenance` | [BE-0049](../../roadmaps/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md) のスタンプ（シナリオハッシュ、ツールバージョン、git リビジョン）です。run から独立して証跡を識別できるようにします。この `scenarioHash` は**このシナリオ単体**のフィンガープリントです。run マニフェストの `scenarioHash` は、存在すればファイルレベルの `description` を取り込みますが、こちらはそれを含みません。そのため、スイートやマトリクスの run に限らず、単一シナリオの run でもマニフェストのハッシュと一致しないことがあります。 |
| `elements` | タイムアウトした瞬間の要素ツリー（マスキング済み）です。 |

これは `Artifact(kind="waitDiagnostic", provider="runner")` として記録します。バックエンドの actuator ではなく、run ループが書き出します。

## アーティファクトの来歴（provider）

すべての証跡は `Artifact(name, kind, provider, depicts)` として記録し、**どの provider から来たか**と
**どの画面を写したか**を manifest に残します。

```python
@dataclass
class Artifact:
    name: str            # ファイル名（例 "before.png"）
    kind: str            # "screenshot" / "elements" / "video" / "deviceLog" / "network" / "waitDiagnostic"
    provider: str        # このアーティファクトを供給した provider（下表参照）
    depicts: str | None  # 写した画面。"<driver>:<moment>" 形式（後述）
```

| `provider` の値 | 意味 |
|---|---|
| `"driver"` | actuator が直接取得した証跡です（スクリーンショット、要素ツリー）。 |
| `"runner"` | run ループが書き出した証跡です（初回 wait のタイムアウト診断、[BE-0231](../../roadmaps/BE-0231-smoke-idb-first-wait-settling/BE-0231-smoke-idb-first-wait-settling-ja.md)）。 |
| `"simctl"` | `simctl` による区間証跡です（動画、デバイスログ、アプリトレース）。 |
| `"adb"` | `adb` による区間証跡です（screenrecord の動画、logcat のデバイスログ）。 |
| `"collector"` | アプリ側のネットワークコレクタ（`BAJUTSU_COLLECTOR`）です。 |
| `"playwright"` | Playwright のネイティブなネットワーク観測です（web バックエンド）。 |
| `"<backend> (fallback)"` | read-only な証跡フォールバックが供給したアーティファクトです（[BE-0020](../../roadmaps/BE-0020-multi-backend-evidence-fallback/BE-0020-multi-backend-evidence-fallback-ja.md)）。 |

証跡の種別をリスト内のどのバックエンドも供給できない場合は、シナリオごとに `SkippedCapture(kind, reason)` を記録し、manifest で開示します。gap を黙って空にすることはありません。

## アーティファクトが写した画面（`depicts`）

ステップのスクリーンショットと要素ツリーは並べて表示し、ビューアはカーソルを合わせた要素の枠を画像の
上に描きます。そのため2つは同じ画面を表している必要があります。`depicts` は、その条件を確認できるよう
にするフィールドです。`"<driver>:<moment>"` の形で、ファイルを生んだ読み取りを行ったドライバの名前と、
ステップの操作のどちら側で取得したか（`before` または `after`）を表します。ネイティブのステップの
`before.png` は `"xcuitest:before"` を持ち、同じステップの `after.png` と要素ツリーは
`"xcuitest:after"` を持ちます。

**2つのアーティファクトが同じ画面を表すのは、`depicts` の値が等しいとき、かつそのときに限ります。**
利用者は値を比較するだけで、解析はしません。比較する場所は `evidence.step_view` の1箇所です。HTML
レポートの要素ビューア、serve の編集画面の要素ピッカー、失敗の調査役へ渡すトリアージのコンテキストが、
いずれもここを通ります。`step_view` は、ステップを1枚のスクリーンショットと1つの要素ツリー、そして2つが
対応しているかどうかへ解決します。対応が取れないステップは画像を保ったまま枠を失います。枠を描けば、
それを説明したことのない画素の上に落ちるからです。

対応が取れない状況は2つあります。1つは
[`web` ブロック](scenarios.md#webwebview-の-dom-コンテキストに入る)の内側です。要素ツリーは WebView
自身の座標系で返り、スクリーンショットはネイティブドライバから取得します（`WebContextDriver` は
スクリーンショットを取れません）。もう1つは、`after.png` をストアが失った実行結果です。ゴミ箱から
復元したものや、最後の書き込みを受け取れなかったオブジェクトストレージへ同期したものが該当し、隣の
`before.png` を代わりに表示します。この画像を、操作後の要素ツリーは説明しません。なお、操作
する前に失敗したステップは2つのどちらでもありません。操作前の組だけを記録するため、対応は取れます。

`depicts` は、フィールドが存在する前に記録した実行結果、すなわち `manifest.json` の
`schemaVersion` が 8 以下の実行結果にはありません。そうした manifest には、操作のどちら側で取得したかがどこにも書かれていません。そのため
利用者は以前と同じ選択、すなわち隣の `before.png` より `after.png` を優先する選択を再現し、対応が取れて
いるものとして扱います。古い実行結果がこれまで描けていた枠を失わないようにするためです。

## ビジュアル証跡

`visual` アサーションは `VisualEvidence` レコードを生成し、manifest とレポートに反映します。run ディレクトリからの相対パスとして、baseline コピー、実際のスクリーンショット、差分画像（差分が見つかった場合）を持ち、`diff_pct`（差分ピクセルの割合）と `engine`（判定を行った比較エンジン、`"exact"` または `"pixelmatch"`。[BE-0165](../../roadmaps/BE-0165-visual-compare-engines/BE-0165-visual-compare-engines-ja.md)）を記録します。

エンジンはアサーション単位（`compare:`）で選択でき、ターゲットレベルの config（`visualCompare`）にフォールバックします。使用されたエンジンは manifest に記録されるため、各判定がどのアルゴリズムで行われたかを追跡できます。実装: `bajutsu/assertions/visual.py` `VisualEvidence`。

## マスキング（redact）

スクリーンショット、ログ、ネットワークデータには、PII（個人情報）やトークンが写り込む可能性があります。保存前に、マスクする対象を宣言してください。実装: `scenario/models/evidence.py` `Redact`。config の `redact` とシナリオの `redact` はマージ（union）されます（[configuration](configuration.md#redact-のマージ)）。

```yaml
redact:
  labels: ["カード番号"]            # accessibility ラベル
  headers: ["X-Session"]           # 追加の HTTP ヘッダ名（既定集合に上乗せ）
  fields: ["token", "password"]    # JSON/body フィールド名
  unmaskHeaders: ["authorization"] # 既定の保護を外す（明示的で目に見える指定）
  unmaskSecureFields: true         # プラットフォームが印を付けた欄の既定を外す（後述）
  unmaskCredentialNames: true      # 資格情報を示す名前の既定を外す（後述）
```

### redact が動く場所

run ディレクトリへの書き込みは、すべて1つのシンクを通ります。証跡がマスクされる理由は、書き手が
マスクを依頼したからではなく、その証跡が置かれる場所によるものです（[BE-0331](../../roadmaps/BE-0331-artifact-redaction-boundary/BE-0331-artifact-redaction-boundary-ja.md)）。
シンクは直列化前の内容を受け取り、要素ツリー、network exchange、crawl のスクリーンマップ、
フリーテキストという形ごとに入口を分けています。後述の規則のうち2つが構造的なもので、要素の trait、
あるいは identifier と label を読むためです。要素ツリーがひとたび JSON 文字列になると、その対応関係は
失われます。シンクが内容を検査できないもの（スクリーンショット、video、アーカイブ）は別の入口を通し、
マスクしていない証跡として記録します。画像を保護済みと称することはありません。実装:
`evidence/sink.py` `RunArtifactWriter`。

この境界が管理するのは書き込みだけです。run の読み取りに制限はありません。Web UI、証跡の読み手、
`export`、比較系のコマンドはいずれも読み取りを必要とし、読み取り操作が証跡を作ることはないためです。
書き込み側は2つの機械的な検査で閉じてあります。書き込み可能な run のパスを導出できるモジュールを
シンクだけに限る import 契約と、run ルートのパスリテラルが他の場所に書かれると失敗する検査です。
どちらもソースだけを読むので、まだ誰も書いていない書き手も、書かれた時点で対象になります。

### 設定を必要としないマスキング

次の3つの規則は `redact:` なしで動きます。いずれも、シナリオの作者が先回りして想定すべきではない
ケースを扱うためです。`crawl` にはそもそもシナリオがないので、その証跡に届く規則はこの3つだけです。

- **プラットフォームが秘密と印を付けた欄**：バックエンドがマスク入力の trait を報告した要素は value を
  マスクし、そうした欄に入力した値もマスクします。trait の導出元は各バックエンドが持ちます。XCUITest の
  `secureTextField`、web の `input[type=password]`、Android のアクセシビリティノードの `password`
  フラグです。ドライバ適合性スイート（[BE-0114](../../roadmaps/BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md)）が
  全バックエンドでこの trait を固定するので、iOS でも web でも Android でも同じ意味になります。外すには
  `unmaskSecureFields: true` を指定します。
- **identifier か label が資格情報を示す欄**：語彙は `password`、`passwd`、`secret`、`token`、
  `apikey`、`api_key`、`credential`、`otp`、`pin` で、大文字小文字を区別せず語境界で照合します。
  `settings.apikey` という名前の欄はマスクされ、`prefs.pinned` はマスクされません。語彙を小さく保って
  文書化してあるのは、作者が予測できない規則は頼りにできないからです。外すには
  `unmaskCredentialNames: true` を指定します。
- **見分けのつく資格情報の形**：最後の受け皿として、シンクは書き出すテキストのなかで確度の高い形を
  マスクします。対象は次の5つです。Anthropic のキー（`sk-ant-`）。Amazon Web Services（AWS）の
  アクセスキー ID（`AKIA`）。GitHub のトークン（`ghp_` とその同族）。3区切りの JSON Web Token（JWT）。
  Privacy-Enhanced Mail（PEM）の秘密鍵ブロック。一致した箇所はマスクし、証跡の名前を添えた警告を出します。この受け皿まで値が
  届いたということは、より的確な規則が先に捕まえるべきだったことを意味するからです。パターンはリテラルな正規
  表現なので、モデルは介在しません。設定済みの名前と値のどちらも、あらかじめ知らずに済む規則はこれだけです。
  したがって、ツール自身が生成した値へ届く規則もこれだけになります。`crawl --guide ai` の実行で、
  モデルが欄を埋めようとしてそれらしい API キーを発明したときの証跡が、まさにその例です。

### redact が保証しないこと

マスキングは共有した証跡が明かす範囲を狭めますが、漏洩を不可能にするものではありません。次の4つが
残るので、レポートを共有してよいかを判断するときに考慮してください。

- **画素はマスクされません**：入力済みのパスワード欄を写したスクリーンショットには、その値が写ったまま
  です。だからこそ、画像を保護済みとは示唆せず、あらかじめ警告します。
  [BE-0151](../../roadmaps/BE-0151-screenshot-secret-capture-warning/BE-0151-screenshot-secret-capture-warning-ja.md) を参照してください。
- **印のない欄に入力された任意の値は残りえます**：プラットフォームが印を付けず、identifier と label の
  どちらも資格情報らしい名前ではない欄があります。`${secrets.X}` で束縛されておらず、どの受け皿の
  パターンとも当てはまらない値なら、ふつうのテキストと見分けがつきません。見分けるには意味的な判断が
  要りますが、その判断がこの経路へ乗ることはありません。
- **受け皿の語彙は有限です**：誰もパターンを追加していない資格情報の形式は、そのまま通り抜けます。
- **設定したキーは構造化された文字列を隠しすぎます**：`redact.headers` と `redact.fields` の名前は
  `name: value` の形でも照合します。この形の値には区切りがなく、現れた文字列の末尾まで届きます。
  ログの1行に対しては正しい振る舞いですが、証跡が運ぶ境界のある文字列の内側では過剰です。
  `redact.fields` に `app` を挙げると、Android のリソース識別子 `com.example.app:id/login` は
  証跡のどこであれ `com.example.app:[REDACTED]` に書き換わり、そのスクリーンマップを読み戻した
  `crawl --continue` は何も解決しない枝をたどります。ここは意図的に、可読性よりマスキングを優先して
  います。作者が挙げたキーは、作者が意図したキーだからです。`redact.fields` には、制御そのものの
  識別子にも現れる語ではなく、アプリの body のフィールド名を挙げてください。

> **機密ヘッダは既定でマスクされます**（この保護にシナリオ側の `redact:` は不要です）。組み込みの集合は
> `authorization`、`proxy-authorization`、`cookie`、`set-cookie`、`x-api-key`、`x-auth-token` で、
> 大文字小文字を区別せずに照合します。`cookie` と `set-cookie` は一つの関心事として扱い、どちらか一方を
> 指定（または解除）すると両方に適用されます。`redact.headers` に書いたヘッダ名はこの集合に上乗せされる
> だけで、集合を置き換えることはありません。既定ヘッダの生の値がどうしても必要なとき（認証失敗のデバッグ
> など）は、そのヘッダ名を `unmaskHeaders` に書きます。保護を外すのは明示的で目に見える選択であり、
> `redact:` を書かないだけで外れることはありません。

> redact は証跡の書き出し前に **適用されます**（`evidence/redaction.py` `Redactor`）。device log と app trace は key→value パターンでスクラブし、要素ツリーは label が設定済みなら value をマスクします（または埋め込まれた secret をスクラブします）。各 network exchange は構造的にマスクします。ヘッダ値は名前で処理し、url / request / response の body はフリーテキストとして処理するので、クエリパラメータや `token` / `password` の body フィールドも捕捉します。画像（スクリーンショット / video）はマスクできず、そのまま残ります。
>
> redact は **secret の入力値** にも及びます。`${secrets.X}` の背後にある実値（環境から解決し、config の `secrets:` で宣言します。[configuration](configuration.md#シークレットsecrets)）は、設定済みの `labels` / `headers` / `fields` だけでなく、証跡に現れる箇所すべてでマスクします。長い値から先にマスクするため、ある値が別の値の部分文字列であっても、部分的な漏れは起きません。
>
> 値の照合は **エンコードを考慮** します。同じ秘密値でも、証跡に現れるときは多くの場合エンコードされており、そのままのバイト列は現れません。redact は、生の値に加えて、よくあるエンコード形もマスクします。パーセントエンコード（URL のクエリやフォームフィールド。たとえば `p@ss` は `p%40ss` になります）、HTML エスケープと JSON エスケープの形、そして `Authorization: Basic <base64(user:pass)>` トークンのうちデコードした認証情報がその値を含むもの、の三種です。これは既知の値に対して固定された変換を適用する方式（値をエンコードしてから検索します）であり、証跡内のあらゆる文字列をデコードして総当たりする方式ではないので、コストと誤検出の範囲は限定的なままです。一つ制約が残ります。redact が動く前に証跡が実際に断片化している場合（ストリーミングのチャンクにまたがって分割され、redact が一つの連続した文字列として見られない値）は、照合がベストエフォートになります。組み立て済みの全文の証跡という通常のケースには影響しません。
>
> 実行したシナリオは run ディレクトリにもスナップショットとして保存されます（`scenario.yaml`、およびレポートの生 YAML 表示）。`totp` ステップの `secret` は使い捨てのコードではなく恒久的な base32 シードなので、シナリオに **リテラル** で書かれたシードは、このスナップショット内で `<redacted>` にマスクします。`${secrets.X}` 参照はそのまま残します（参照自体はシードではなく、解決後の実値は上記の secret 入力値のルールでマスクされるためです）。`totp` のシードは `${secrets.X}` で渡し、シナリオファイルにシードが残らないようにするのが望ましい方法です。

## ファイルパーミッション

マスキングは漏えいした証跡が明かす内容を減らしますが、ベストエフォートの denylist なので、証跡を誰が読めるかも同じく重要です。ランナーは各 run ディレクトリを所有者のみ（`0700`）で作成し、機微な内容を含み得るファイル（`network.json`、コピーした `scenario.yaml`、要素ダンプ（`elements.json`）、スクリーンショット）を、ホストの `umask` に依存せず所有者のみ（`0600`）で書き込みます（[BE-0131](../../roadmaps/BE-0131-run-artifact-permissions/BE-0131-run-artifact-permissions-ja.md)）。それ以外の証跡も `0700` の run ディレクトリ配下に置かれるため、共有ホスト（CI ランナーなど）の別のローカルアカウントからは既定で読めません。実装: `bajutsu/common/run_meta/artifact_perms.py`。

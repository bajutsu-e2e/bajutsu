[English](BE-0236-device-cloud-provider-abstraction.md) · **日本語**

# BE-0236 — Device-cloud provider abstraction

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0236](BE-0236-device-cloud-provider-abstraction-ja.md) |
| 提案者 | [@hirosassa](https://github.com/hirosassa) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0236") |
| 実装 PR | [#1189](https://github.com/bajutsu-e2e/bajutsu/pull/1189) |
| トピック | デバイスクラウド実行 |
<!-- /BE-METADATA -->

## はじめに

Bajutsu が駆動するデバイスは、これまで常にローカルにありました。Android backend は `adb -s <serial>`
を、iOS backend は `simctl` / `idb` / `xcodebuild` を、web backend はローカルで起動したブラウザを
呼び出します。つまり実行は、駆動対象のデバイスが同じホスト上にいることを前提にしています。この項目は
**device provider** の継ぎ目を導入し、決定的コアを変えないまま、クラウドサービスが払い出したデバイス
（ネットワーク越しに到達できる、予約された実機）を実行に手渡せるようにします。

この継ぎ目は意図的に狭く、意図的に `run` と CI の合否判定の経路から外してあります。provider の仕事は
「到達可能なデバイスを取得し、後で解放する」ことだけです。その先にあるセレクタ解決、決定的ランナー、
アサーション、証跡はどれも変わりません。ランナーから見れば、クラウドのデバイスも他と同じ 1 つの serial
（または endpoint）にすぎないからです。個々の provider（Firebase Device Streaming、AWS Device Farm、
そして将来のその他）は、**任意で個別にインストールする adapter** として置き、決定的ゲートがクラウドの
SDK に依存しないようにします。

## 動機

デバイスクラウドへ向かう理由は 2 つあります。1 つは実機そのものです。emulator や simulator では、実機
固有の挙動（実際の GPU、センサ、OEM のスキン、iOS の entitlement や署名の経路）が抜け落ちますし、CI の
runner は物理端末のマトリクスを抱えられません。もう 1 つは適用範囲の広さです。すでに AWS Device Farm や
Firebase に費用を払っているチームは、並行するテスト基盤を別に保守するのではなく、Bajutsu のシナリオを
そこで走らせたいと考えます。

ロードマップは、これを禁止ではなく先送りとして扱ってきました。README の「Not adopting」は現在
「Cloud device farm / real-device / cloud execution — out of scope」と読めますが、[DESIGN §1](../../DESIGN.md)
は、これが**恒久的な制約ではなく将来課題である**ことを明記しています。最初に iOS Simulator を選んだのは
「最初の足場の選択」であり、決定的コアが backend 非依存なのは、後で実機やクラウドへ届くためでした。この
項目は、その内部の食い違いを DESIGN の立場に沿って解消し、スコープ文を合わせて更新します（*詳細設計*を
参照）。

重要なのは、デバイスクラウド対応がどの prime directive とも衝突しない点です。実機を adb 越しに駆動する
ことは emulator を駆動するのと同じだけ決定的で、provider はゲートに LLM を足しません。どの provider を
使うかは target ごとの config なので、ツールとドライバとランナーは app 非依存のままです。継ぎ目が避け
なければならないのは、*provider* 側の仕掛け（SDK、認証情報、予約、課金、ネットワークのリトライ）を決定的
コアへ漏らすことです。その仕掛けを任意の adapter に閉じ込めることが、この項目の眼目です。

継ぎ目の形を決める設計上の観察があります。デバイスクラウドには実行トポロジが 2 種類あり、両者は同じ形を
していません。

- **live（遠隔デバイス）。** 実行はローカルで動き、*遠隔の*デバイスをネットワーク経由で駆動します。
  Firebase の Android Device Streaming は予約した実機を「adb over SSL」として見せ、商用クラウドは
  Appium / WebDriver の endpoint を見せます。ここでの provider の仕事は「デバイスを予約して接続を渡す」
  こと（`adb connect` 用の serial、または endpoint）です。これは Bajutsu の既存のライフサイクル、すなわち
  取得してランナーが駆動し解放する、という流れにそのまま対応します。
- **batch（遠隔実行）。** 実行は*クラウドのホスト上で*動きます。AWS Device Farm の custom test
  environment は package をアップロードし、そこであなたのコマンドを走らせます。ここでは Bajutsu が呼び出す
  側ではなく積荷であり、「provider」は実行時のオブジェクトではなく CI 側の packager と submitter です。

この項目は、実行時の継ぎ目を **live** トポロジに限定します。ここでこそ、単一の provider 横断の抽象が
本当に効きます。batch トポロジは別立ての CI 側 submitter の項目（兄弟項目の *aws-device-farm-submitter*）
で扱います。両者を 1 つの実行時 interface に押し込むと、抽象が漏れてしまうからです。

## 詳細設計

### provider の継ぎ目

`DeviceProvider` の狭い protocol を追加します。その唯一の責務は、実行のあいだ到達可能なデバイスを貸し出し、
後で解放することです。自然な形は、既存の lease ベースの `device_pool`（`bajutsu/runner/pool.py`）を写し
取ったものになります。この pool はすでにランナーへデバイスのハンドルを渡し、それを回収しています。

- `acquire(target) -> DeviceLease`：デバイスを予約し、該当する backend がすでに理解する接続座標を持つ
  ハンドルを返します。Android なら、ドライバが既に受け付ける `IP:port` 形式の `serial`
  （`bajutsu/device_id.py` が `adb connect` の対象を検証します）、Appium 系の provider なら endpoint の
  URL です。
- `DeviceLease.release()`：予約を終了します（そして課金を止めます）。

target が provider を指名しないときの既定は、現在の **local** provider です。既存の target の挙動は
変わりません。

### registry と任意の adapter（この repo の作法）

個々の provider は `kind` キーで登録し、registry を通じて解決します。これは backend（BE-0042）、mailbox の
transport（BE-0186）、証跡ストアの URI スキーム（BE-0110）、AI provider で、この repo がすでに採っている
やり方と同じです。未知の `kind` は明快な `ValueError` で fail-closed にします。

個々の provider（Firebase Device Streaming、後に商用クラウド）は、provider の CLI や SDK を包む
**任意の extra**（たとえば `pip install "bajutsu[firebase]"`）として出荷します。決定的ゲートはそのどれも
インストールしないので、`make check` がクラウドの SDK を引くことはありません。これにより、非決定的な面
（認証、ネットワーク、リトライ）を、コードの経路だけでなく依存関係のレベルでもコアの外に保ち、依存の
レベルで determinism-first を満たします。

### config の表面

target は既存の config の重ね合わせ（`bajutsu/config.py`）を通じて、`targets.<name>` の下で provider を
選びます。

```yaml
targets:
  pixel-cloud:
    platform: android
    backend: [adb]
    deviceProvider:
      kind: firebase-streaming   # 省略時の既定は local
      # provider 固有のフィールド（project, device model, api target …）は adapter が検証する
```

正確なキー名は PR で詰める実装詳細です。制約は、provider の選択が **target 単位の app 非依存な config**
であって、シナリオ単位やステップ単位ではないことです。

### クラウドの差異を local の経路から隔離する

クラウドが払い出したデバイスは、起動直後のローカル emulator といくつかの点で異なります。その違いは backend
全体へにじませるのではなく、継ぎ目の裏に隔離します。これらは既存の `RunEnvironment` protocol
（`bajutsu/runner/platform_lifecycle.py`）を通じて表面化させ、ドライバ自体は変えません。

- **起動と準備完了。** クラウドのデバイスはすでに起動済みなので、provider がデバイスの準備完了を報告した
  ときはローカルの起動待ち（`_await_boot`）を省けるようにします。
- **アプリのインストール。** provider 自身がアプリの package をインストールする場合は `appPath` の
  インストールを省き、そうでない場合は既存の `adb install` の経路をそのまま走らせます。
- **device control の縮退。** クラウドのデバイスは emulator 限定の device control プリミティブ（位置情報の
  設定、clipboard、ステータスバー）に対応しないことがあります。provider が縮小した capability を宣言し、
  既存の preflight（BE-0082）が非対応のアクションを実行の*前に*明快に切り落とすようにします。実行の途中で
  失敗させません。

### スコープ文の更新

これは明文化されたスコープを変えるので、同じ変更で [DESIGN §1](../../DESIGN.md) と README の「Not
adopting」の記述を更新し、デバイスクラウドでの実行が（任意の adapter の裏で）非対象ではなく対応する方向で
あることを反映します。これは DESIGN とロードマップを挙動に合わせて保つためです（BE-0113）。立場を切り替える
のはこの項目で、兄弟の adapter と submitter の項目は新しい立場の上に築きます。

### 作業分解（MECE）

1. **`DeviceProvider` / `DeviceLease` の protocol** — 狭い継ぎ目とそのハンドル型を定義し、run と CI の
   合否判定の経路から外れる不変条件を明文化する。
2. **local provider（既定）** — 現在のローカルなデバイス取得を継ぎ目の裏へ移し、既存 target の挙動は
   変えない。
3. **provider の registry** — `kind` キーの登録と解決、未知の `kind` で fail-closed（BE-0042 / BE-0186 に
   倣う）。
4. **config の表面** — `targets.<name>` 上の `deviceProvider`。config 読み込みがクラウドの SDK を import
   しないよう遅延解決し、未知の kind は明快なエラーにする。
5. **`RunEnvironment` のクラウド用フック** — 起動待ちの省略、任意のアプリインストール、device control の
   capability 縮退を、既存の environment protocol を通じて配線する。
6. **テスト** — provider の解決、local の既定（挙動不変）、未知の kind での fail-closed、クラウド差異の
   フックを、fake の provider で検証する（ゲートに live のクラウドを持ち込まない）。
7. **ドキュメントとスコープ更新** — provider のモデルを `docs/`（両言語）に記述し、DESIGN §1 と README の
   「Not adopting」の記述を更新する。

### prime directive への適合

- **AI をゲートに入れない。** provider はデバイスの取得と解放だけを行い、モデルは介在しません。決定的
  ランナーと CI の判定は変わりません。
- **決定性優先。** 実機を adb 越しに駆動することは emulator の駆動と同じだけ再現可能で、固定 sleep は
  導入しません（準備完了は条件のままで、provider が報告します）。
- **app 非依存。** provider の選択は `targets.<name>` の config で、ツールとドライバとランナーの形は
  変わりません。provider の SDK は任意の extra に置くので、決定的ゲートはクラウド非依存のままです。

## 検討した代替案

- **live と batch の両トポロジを 1 つの client で抽象化する。** AWS Device Farm（batch）と Device
  Streaming（live）の両方を覆う単一の実行時 interface は、抽象が漏れます。batch では Bajutsu がアップロード
  される積荷であって呼び出す側ではないので、「デバイスのハンドルを取得する」ことに実行時の意味がありません。
  却下し、実行時の継ぎ目は live トポロジに限定します。batch は CI 側の submitter（兄弟項目の
  *aws-device-farm-submitter*）で扱います。
- **provider の仕掛けをコアに組み込む（任意の extra にしない）。** クラウドの SDK、認証情報、ネットワークの
  リトライを決定的コアとその依存の閉包へ引き込みます。却下し、個々の provider は任意の adapter として
  ゲートをクラウド非依存に保ちます。
- **初日から client を完全に別リポジトリにする。** 切り離しの点では魅力的ですが、interface が実証される前に
  リリースとバージョン管理のコストを払います。当面は却下し、安定した protocol の裏で in-repo の任意 extra
  として始め、後の抽出を安価に保ちます。
- **どの provider より先に抽象を完全に設計する。** 誤った抽象を切る危険があります。代わりに、最初の具体
  adapter（*firebase-device-streaming-adapter*）が継ぎ目を実証し、この項目はその必要から得た知見をもとに
  継ぎ目を確定させます。PoC を先行させる順序です。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `DeviceProvider` / `DeviceLease` の protocol（`bajutsu/runner/device_provider.py`）
- [x] local provider（既定、挙動不変）— `--udid` の文字列をそのまま通します
- [x] provider の registry（`kind` キー、fail-closed）— mailbox の registry（BE-0186）に倣います
- [x] config の表面（`targets.<name>` 上の `deviceProvider`、遅延解決。未知の kind は実行時にエラー）
- [x] `RunEnvironment` のクラウド用フック — 起動待ち省略と任意インストールを、lease が持つ
  `ProvisionProfile` を `run` → pool → `environment_for` → `AndroidEnvironment.start` へ渡して実現します
  （実際のパスは `bajutsu/platform_lifecycle/` で、提案が記す旧 `runner/platform_lifecycle.py` ではありません）
- [x] テスト（fake の provider）— registry の解決、local の既定、fail-closed、起動待ち省略とインストール省略
- [x] ドキュメントとスコープ更新（DESIGN.md §1 の「やらないこと」のスコープ除外リスト、`docs/` の両言語）

> **Unit 5 のうち先送りした部分 — デバイス制御の capability 縮退。** クラウド差分の 3 つ目
> （provider が縮小した device-control の capability 集合を宣言し、preflight が未対応アクションを
> 事前に切り落とす）は、この seam の PR では意図的に配線していません。出荷したのは `local` provider
> だけで、縮小した集合を生む経路が存在しないため、いま field を追加してもテストのない投機的な表面に
> なってしまい、「seam と参照アダプタ 1 つを出す」という方針（BE-0186）に反します。この部分は最初の
> クラウドアダプタ（**firebase-device-streaming-adapter**）と一緒に入れます。そこには preflight の
> 切り落としを実際に動かしテストできる縮小 capability 集合があります。seam 側の準備は整っています。
> `ProvisionProfile` が自然な受け皿で、preflight（BE-0082）はすでに capability 集合を消費します。

> **スコープ — `run` のみ。** `acquire_device` を配線したのは `bajutsu run` です。`record`・
> `crawl`・`audit --repeat` は従来どおりの方法でデバイスを解決します。そのため、これらのコマンドが
> 駆動するターゲットにクラウドの `kind` を付けても、そこでは fail-closed にならず無視されます。出荷が
> `local` だけの間は無害です（兄弟のクラウドアダプタがこれらのコマンドへ seam を広げます）が、拾い
> 上げられるよう記します。

### 進捗ログ

- seam・local provider・registry・config の表面・起動待ち省略とインストール省略のフックを、すべて
  `local` の既定の背後に置き、既存のターゲットは 1 バイトも変わりません。レビュー由来の硬化として、
  セットアップ中のエラーでも lease を解放し、その `release` は warn-only にして provider の teardown が
  機械判定を覆せないようにしました（判定後の zip / upload の規則に倣います）。`make check` は緑
  （テスト 4414 件、カバレッジ 92%）。PR: [#1189](https://github.com/bajutsu-e2e/bajutsu/pull/1189)。

## 参考

- [DESIGN.md §1 — 目的とスコープ](../../DESIGN.md)
- [docs/architecture.md — 実装状況](../../docs/architecture.md)
- [BE-0042 — Platform-aware backend registry & selection](../BE-0042-platform-backend-registry/BE-0042-platform-backend-registry.md)
- [BE-0186 — mailbox provider registry](../BE-0186-mailbox-provider-registry/BE-0186-mailbox-provider-registry.md)
- [BE-0110 — evidence store URI](../BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md)
- [BE-0082 — capability preflight check](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md)
- [BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md)
- 兄弟項目：**firebase-device-streaming-adapter**（最初の live adapter）、**aws-device-farm-submitter**
  （batch、CI 側）、**ios-device-cloud-execution**（iOS 実機の経路）

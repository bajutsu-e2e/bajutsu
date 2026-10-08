[English](BE-XXXX-runner-device-signing-build.md) · **日本語**

# BE-XXXX — 利用者ごとに署名する XCUITest ランナーの実機ビルド

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-runner-device-signing-build-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **承認済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | デバイスクラウド実行 |
| 関連 | [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md), [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md), [BE-0288](../BE-0288-ios-device-signing-batch-build/BE-0288-ios-device-signing-batch-build-ja.md), [BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner-ja.md) |
<!-- /BE-METADATA -->

## はじめに

実機の iPhone や iPad を操作するには、実行する本人が署名した XCUITest ランナーが必要です。
現状では、リポジトリを書き換えずにそのランナーを作る方法がありません。ランナーの bundle id は直書きされており、
ビルド用の補助も Apple Developer のチームしか受け取らないためです。

この項目は `bajutsu runner build --device` を追加します。wheel に同梱したソースからランナーをビルドし、
入力には利用者ごとの署名ファイルを使います。署名ファイルには、bundle id の接頭辞、チーム、
署名方式（自動、または証明書と Provisioning profile を指定する手動）を書きます。
成果物は、これらの入力から求めたキーでキャッシュします。`xcuitest.testRunner` を書かない実機の実行は、
このキャッシュを自動的に使います。

## 動機

署名済みのランナーさえあれば、実機の実行はすでに動きます。足りないのは、各利用者が自分の Apple Developer
アカウントでそのランナーを作る手段です。

- **bundle id が 1 つのチームに紐づいています。** `BajutsuKit/Runner/project.yml` は
  `com.bajutsu.runner.host` と `com.bajutsu.runner.uitests` を直書きしています。App ID（Apple に登録する識別子）は
  1 つのチームにしか所属できません。そのため、別のチームがこの識別子で署名すると、自動署名でも失敗します。
  `xcodebuild` のコマンドラインで `PRODUCT_BUNDLE_IDENTIFIER` を 1 つ渡しても解決しません。
  ホストとテストバンドルの両方が同じ値に上書きされるからです。
- **自動署名しか選べません。** `demos/showcase/Makefile` の `runner-build-device`
  （[BE-0288](../BE-0288-ios-device-signing-batch-build/BE-0288-ios-device-signing-batch-build-ja.md)）は、
  環境変数 `DEVELOPMENT_TEAM` だけを読み、常に自動で署名します。証明書と Provisioning profile を固定したい
  利用者（継続的インテグレーション（CI）のマシンや、エンタープライズ配布の会社など）は、それを指定できません。
- **ビルド手順がソースのチェックアウトとデモの Makefile を前提にしています。** wheel に入るのは Simulator 用の
  ランナー成果物だけです（[BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner-ja.md)）。
  wheel から導入した利用者は、ランナーのソースも手順も持っていません。
- **利用者ごとの値を置く安全な場所がありません。** ターゲットの設定ファイルはコミットされ、共有され、
  Git リポジトリから取得されることもあります。チーム ID や bundle id をそこへ書くと、他の利用者全員に届きます。

期待する変化は次のとおりです。まっさらなマシンの利用者が署名ファイルを 1 つ書き、
`bajutsu runner build --device` を 1 回実行します。以後の `bajutsu run` は、`testRunner` の行もコミット済みファイルの
編集もなしに、実機を操作します。現状では、同じ利用者が `project.yml` をフォークする必要があります。

## 詳細設計

追加するのは、ビルドコマンド、署名ファイル、実行時の解決段階の 3 つです。実行ループ、ドライバー、チャネル、
各シナリオは変えません。ビルドは `run` の判定経路の外にあるため、LLM も固定スリープも入りません
（プライムディレクティブ 1 と 2）。ランナーはアプリ非依存のままなので、アプリごとの差は引き続き
`targets.<name>` に置きます（プライムディレクティブ 3）。

### 署名ファイル

ランナーは汎用なので、署名はターゲットではなく利用者の属性です。したがって、利用者ごとに 1 ファイルで足ります。
Bajutsu は次の順で探し、最初に見つかったものを使います。

1. コマンドラインの `--signing <path>`
2. 環境変数 `BAJUTSU_SIGNING_FILE`
3. `~/.config/bajutsu/signing.yaml`

ファイルはリポジトリの外にあるため、コミットに混入しません。秘密は含みません。チーム ID、証明書名、
profile 名が指す鍵と profile は、Keychain と `~/Library/MobileDevice/Provisioning Profiles/` に残ります。

```yaml
bundleIdPrefix: com.acme           # ランナーのホストアプリ = <prefix>.bajutsu.runner-host、
                                   # UI テストバンドル = <prefix>.bajutsu.runner-uitests
# bundleIds:                       # bundleIdPrefix の代わりに、2 つの識別子を自分で指定する
#   host: com.acme.e2e.runner-host
#   uitests: com.acme.e2e.runner-tests
teamId: ABCDE12345                 # 必須
signing: automatic                 # automatic（既定）| manual
manual:                            # signing: manual のとき必須
  identity: "Apple Development: Jane Doe (ABCDE12345)"
  profile: "Acme Bajutsu Wildcard"           # すべての識別子を覆う 1 つの profile、または
  profiles:                                  # 識別子ごとの profile
    host: "Acme Bajutsu Host"
    runner: "Acme Bajutsu Runner"            # UI テストバンドルの .xctrunner アプリ
```

`bundleIdPrefix` と `bundleIds` は、どちらか一方を必ず書きます。接頭辞の形は、2 つの識別子を導出します。
`bundleIds` の形は、2 つを名前で直接指定します。Apple Developer アカウントに、別名の明示型 App ID と
profile がすでにある利用者や、組織が命名を制限している利用者のための形です。どちらの形でも、
UI テストバンドルの `.xctrunner` アプリの識別子は `<uitests の識別子>.xctrunner` になります。
識別子ごとの profile で手動署名する利用者は、この 3 つ目の識別子に合う profile も用意する必要があります。

読み込み時に、ターゲットの設定と同様に pydantic のモデルで検証します。未知のキー、`teamId` の欠落、
`identity` のない `signing: manual` を拒否します。`profile` と `profiles` の両方を書いた、または
両方とも書かない `manual` ブロックも拒否します。さらに、`bundleIdPrefix` と `bundleIds` の両方を書いた、
または両方とも書かないファイル、`host` か `uitests` を欠く `bundleIds`、2 つの値が等しい `bundleIds` も
拒否します。既定の接頭辞は用意しません。既定値を置くと、この項目が取り除こうとする衝突を再び招くからです。
ビルドはターゲットを知らないため、ランナーの識別子と操作対象アプリの識別子の比較はできません。
ランナーの識別子がアプリの識別子と等しいと、インストールでアプリが上書きされます。この点はドキュメントで
注意を促します。

### ソースのステージング

コマンドは、ソースを作業用ディレクトリにステージングし、そこでビルドします。チェックアウトには触れないので、
ビルド後も `git status` は変わりません。ステージングの入力元は次の 2 か所です。

- ソースのチェックアウトでは、リポジトリのルート
- wheel の導入では、新設するパッケージデータ `bajutsu/_runner_source/`

`make runner-source` が、このディレクトリを埋めます。元にするのは、ランナーの鮮度ハッシュをすでに定義している
パス一覧、つまり `bajutsu/common/platform_lifecycle/environments/_bundled_runner.py` の
`_HASH_SOURCE_PATHS` です。同じ一覧を使えば、同梱するものとハッシュするものが一致します。
ディレクトリは gitignore 対象で、現在の `_xcuitest_runner/` と同様に `pyproject.toml` の `artifacts` で
wheel に取り込みます。パスを選んでコピーするのは、`BajutsuKit/` に `force-include` を使うと、
ビルド出力まで wheel に入ってしまうからです。`Package.swift` は 2 つのテストターゲットを宣言していて、
そのパスはステージング先にありません。Swift Package Manager（SPM）は、存在しないターゲットのパスを持つ
マニフェストを拒否します。そのためステージングでは、この 2 ターゲットを除いたマニフェストを書き出します。

### 利用者ごとのプロジェクト生成

XcodeGen（`xcodegen`）が `project.yml` から Xcode プロジェクトを生成します。コマンドは、ステージング先の
`project.yml` を読み込み、生成の前にターゲットごとの設定を上書きします。これは `xcodebuild` の
コマンドライン設定ではできないことです。変更するのはステージング先のコピーで、コミット済みのファイルは
変えません。

| 設定 | ランナーのホストアプリのターゲット | UI テストターゲット |
|---|---|---|
| `PRODUCT_BUNDLE_IDENTIFIER` | `<prefix>.bajutsu.runner-host`、または `bundleIds.host` | `<prefix>.bajutsu.runner-uitests`、または `bundleIds.uitests` |
| `DEVELOPMENT_TEAM` | `teamId` | `teamId` |
| `CODE_SIGNING_ALLOWED` | `YES` | `YES` |
| `CODE_SIGN_STYLE` | `Automatic` または `Manual` | 同左 |
| `CODE_SIGN_IDENTITY`（手動） | `identity` | `identity` |
| `PROVISIONING_PROFILE_SPECIFIER`（手動） | `profile` または `profiles.host` | `profile` または `profiles.runner` |

ステージング先の spec では、SPM パッケージのパスも `../..` からステージング先のルートへ書き換えます。
そのうえで `xcodebuild build-for-testing -destination generic/platform=iOS` を実行します。
`-allowProvisioningUpdates` を付けるのは自動署名のときだけです。最後に、現在の `runner-build-device` と同じく、
1 つだけある `*.xctestrun` を `BajutsuRunner.xctestrun` へコピーします。

### キャッシュとそのキー

ビルド出力は `~/.cache/bajutsu/xcuitest-runner-device/<key>/Products/` に置きます。キーは、ランナーの
ソースハッシュ、検証済みの署名項目（解決後の 2 つの識別子を含む）、Xcode のビルドバージョンをハッシュして作ります。どれかが変わると
別のディレクトリになるため、古い署名済みランナーを再利用することはありません。キーが一致するディレクトリは、
再ビルドせずに使います。コマンドは `.xctestrun` のパスを表示します。`--out <dir>` を付けると、`Products`
ディレクトリ全体もそこへコピーします。`.xctestrun` はテストバンドルを相対パスで参照するため、
Device Farm のパッケージ化にはディレクトリ全体が必要です。

### コマンド

```text
bajutsu runner build --device [--signing PATH] [--out DIR] [--force]
```

- `xcodebuild` を呼ぶ前に、事前チェックを行います。macOS であること、`xcodebuild` と `xcodegen` があることを
  確かめます。手動署名では、`security find-identity -v -p codesigning` に identity が出ること、
  名前を挙げた各 profile がインストール済みであることも確かめます。失敗するたびに、足りないものと
  直し方を示します。
- `--force` は、キーが一致するキャッシュを上書きして再ビルドします。
- 現時点では `--device` が必須です。このフラグを置くのは、将来 Simulator 用ランナーのビルドを同じ
  コマンドへ統合する余地を残すためです。

### 実行時の解決

`xcuitest.deviceType: device` は、現状では `testRunner` の明示を要求します
（[BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner-ja.md)）。環境に段階を 1 つ足します。
`testRunner` がなければ、署名ファイルを探してキーを計算し、そのディレクトリがあればキャッシュ済みの
`.xctestrun` を使います。ディレクトリがなければ、実行をすぐ失敗させ、実行すべき
`bajutsu runner build --device` のコマンドをそのまま示します。実行中に暗黙でビルドすることはしません。
署名ビルドは遅く、Keychain のプロンプトを出すことがあり、Apple 側に識別子を登録することもあるため、
プール実行の途中に置くべきではないからです。`testRunner` を明示した場合は、引き続きキャッシュより優先します。

### showcase の Makefile

`demos/showcase/Makefile` の `runner-build-device` は、`bajutsu runner build --device --signing $(SIGNING)`
を呼ぶ形に置き換えます。出力先は、Device Farm の手順が使う `build/` 配下のままにします。
`DEVELOPMENT_TEAM` だけを渡す呼び出しは、ランナーについては使えなくなり、署名ファイルを案内する
メッセージで失敗します。変えるのはランナーの経路だけです。`swiftui-archive-device` と
`swiftui-ipa-device` は、これまでどおり `DEVELOPMENT_TEAM` からデモアプリをビルドします。

### スコープ外

- 利用者自身のアプリの署名とパッケージ化。アプリのビルド方法はプロジェクトごとに違い、薄い抽象は
  どれにも合いません。
- Xcode の `-allowProvisioningUpdates` が行う範囲を超えるデバイス登録。
- 証明書や profile の作成、Keychain の管理。
- CI での署名済み成果物のビルド。BE-0288 に書いたとおり、Apple Developer アカウントを CI に
  接続するまで待ちます。
- Android、Simulator 用ランナー、Device Farm が行うアップロード済みアプリの再署名。

### 検証

- どのホストでも動く単体テスト（Linux を含む）。署名ファイルの検証（`bundleIdPrefix` と `bundleIds` の排他を含む）と探索順、両方式の spec 上書き、
  `xcodebuild` と `xcodegen` の引数の組み立て、キャッシュキーの安定性と感度、実行時の解決エラーを確かめます。
  外部コマンドは注入した実行器の背後に置くので、テストに Xcode は要りません。
- `make check` の外で行う実機の手動確認。別チームの識別子での自動署名ビルド、手動署名ビルド、
  `testRunner` なしでの 1 シナリオの実行を行います。

## 検討した代替案

| 案 | 採らなかった理由 |
|---|---|
| 環境変数だけ（`BAJUTSU_SIGN_*`） | 5 つ以上の変数をシェルと CI ジョブごとに設定する必要があります。ファイルなら、まとめて持ち、まとめて検証できます。 |
| `targets.<name>.xcuitest` に署名キーを置く | 設定ファイルはコミットされ共有されうるので、チーム ID が漏れます。ランナーは汎用なのに、同じ値をすべてのターゲットに繰り返すことにもなります。 |
| プロジェクト内の署名ファイル（`bajutsu.signing.yaml`） | 利用者固有のファイルがリポジトリ内にあると、誤ってコミットされるおそれがあります。`.gitignore` とシークレットスキャナの例外も必要です。グローバルなファイルと明示指定なら、どちらも避けられます。 |
| コマンドラインの `xcodebuild PRODUCT_BUNDLE_IDENTIFIER=…` | 1 つの値が両ターゲットを上書きし、ホストとテストバンドルが衝突します。 |
| コミット済み `project.yml` への `${VAR}` プレースホルダー | Simulator ビルドと単体テストが、その変数の設定や既定値に依存します。コミット済みの spec に、利用者ごとの事情が入り込みます。 |
| BE-0288 が `ExportOptions.plist` に使う `sed` 置換 | 構造化されたファイルへのテキスト置換は、レイアウトが変わると黙って壊れます。パース済みの spec を上書きする方式なら、壊れたときに明示的に失敗します。 |
| キャッシュが空なら暗黙でビルドする | 署名ビルドは、Keychain のプロンプトを出し、実行中に Apple のサーバーへ接続することがあります。プール実行では、最初のシナリオが遅いというだけの症状に隠れてしまいます。 |
| ビルド済みの実機用ランナーだけを配布する | ビルド済みランナーは 1 チームの署名を持ち、別のチームの下ではインストールできません。 |
| ソースのチェックアウトだけを対象にし、wheel へソースを入れない | 設計レビューで見送りました。wheel だけの利用者が実機用ランナーを作れなくなるためです。同梱するソースが大きすぎる、または追従が難しいと分かったときに見直します。 |

## 進捗

> 作業の進行に合わせて更新します。チェックリストは「詳細設計」の MECE な作業分解（1 作業につき 1 つの
> ボックス）に対応します。ログには、変更内容と日付を古い順に、PR へのリンクとともに記録します。

- [ ] スパイク：実機 1 台で、手動ビルドに必要な profile（ホスト、`.xctrunner` アプリ、または 1 つの
  ワイルドカード）を特定し、`.xctrunner` の識別子を確認し、ステージング済みマニフェストと生成 spec の
  方式でビルドできることを確認する。
- [ ] 署名ファイルのモデル、検証（接頭辞または明示の `bundleIds`）、探索順（テスト付き）
- [ ] ソースのステージング：`make runner-source`、`artifacts` の項目、ステージング用マニフェスト
- [ ] プロジェクト spec の上書き、ビルドコマンドの組み立て、事前チェック、キャッシュ（テスト付き）
- [ ] `bajutsu runner build --device` コマンド
- [ ] XCUITest 環境の実行時解決の段階と、未ビルド時のエラー
- [ ] `demos/showcase/Makefile` の `runner-build-device` のラッパー化
- [ ] 両言語のドキュメント：`docs/ios-device-cloud.md`、`docs/devicefarm.md`、`docs/cli.md`、
  `docs/configuration.md`、および `docs/architecture.md` のモジュール一覧
- [ ] 実機での手動確認（Apple Developer アカウントとデバイスが必要）

## 参考

- [BE-0288](../BE-0288-ios-device-signing-batch-build/BE-0288-ios-device-signing-batch-build-ja.md)：
  この項目が一般化する、デモ向けの実機署名ビルド。
- [BE-0292](../BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner-ja.md)：同梱の Simulator
  用ランナーと、実機での `testRunner` 明示の規則。
- [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution-ja.md)：実機を対象にする
  XCUITest。
- [iOS を実機とデバイスクラウドで動かす](../../docs/ja/ios-device-cloud.md)
- `BajutsuKit/Runner/project.yml`、`Package.swift`、`demos/showcase/Makefile`、
  `bajutsu/common/platform_lifecycle/environments/_bundled_runner.py`

[English](BE-0292-xcuitest-bundled-runner.md) · **日本語**

# BE-0292 — XCUITest ランナーを同梱して testRunner を省略可能にする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0292](BE-0292-xcuitest-bundled-runner-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0292") |
| 実装 PR | [#1221](https://github.com/bajutsu-e2e/bajutsu/pull/1221)、[#1276](https://github.com/bajutsu-e2e/bajutsu/pull/1276) |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)、[BE-0288](../BE-0288-ios-device-signing-batch-build/BE-0288-ios-device-signing-batch-build-ja.md)、[BE-0291](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios-ja.md) |
<!-- /BE-METADATA -->

## はじめに

XCUITest バックエンドは、単一の汎用ランナーであらゆるアプリを駆動します。ところが今は、Simulator の
シナリオを1本走らせるだけでも、利用者がそのランナーを自分でビルドし、そのパスを config に書く必要が
あります。

本項目は、ビルド済みの Simulator ランナーを Bajutsu の wheel に package data として同梱します。
config がランナーを指定していないときは、`xcuitest` バックエンドが同梱ランナーへ解決します。これにより
`xcuitest.testRunner` と `xcuitest.build` は、どちらも省略できます。Simulator を対象とする run は
`make -C demos/showcase runner-build` もランナーのパス指定も不要になり、バックエンドが設定なしで動きます。

明示的な `testRunner` や `build` を書けば、従来どおり同梱ランナーより優先されます。署名付きランナーを
要する実機の run は、Bajutsu が同梱できないため、これまでどおり明示指定のままです。

## 動機

ランナー自体は汎用ですが、そのために要求される準備は汎用ではありません。ターゲットごとに、そして
クローンし直すたびに、どのターゲットでも中身の変わらない成果物のためにビルドと設定のコストを払って
います。

[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) は、XCUITest ランナーがアプリに
依存しない単一の XCTest ターゲットであることを定めました。ビルド済みの `.xctestrun` が1つあれば、run が
対象とするどの bundle id でも駆動できます。そのため、ランナーはすべてのアプリ、すべてのターゲットで
同一です。一方で、BE-0019 はランナーの配布を、ターゲットごとの2つのつまみに委ねました。
`xcuitest.testRunner` はビルド済み `.xctestrun` を指し、`xcuitest.build` はそれをオンデマンドで生成する
シェルコマンドです。どちらも、アプリに依存しない成果物をターゲットごとの設定の後ろに置いています。

このずれは、XCUITest を初めて使うときの手間として現れます。利用者は3つの手順を踏む必要があります。

- `make -C demos/showcase runner-build`、または同等の `xcodebuild build-for-testing` コマンドを走らせる。
- `DerivedData` の products ディレクトリの奥にある `.xctestrun` を探す。
- そのパスを各ターゲットの config に貼り付ける。たとえば
  `testRunner: ../../BajutsuKit/Runner/build/dd/Build/Products/BajutsuRunner.xctestrun` です。

クローンし直した直後には成果物がないので、config が指すパスはビルドを走らせるまで存在しません。
どのクローンでも中身は同じなのに、新しい作業ツリーごとにランナーを作り直します。ターゲットごとの
`testRunner` 行は、config にある XCUITest ターゲットの数だけ、このパスを増やします。

Bajutsu は、非 Python の資産をすでに同じ方法で他のサブシステムに同梱しています。`bajutsu/templates/`
は `serve` とレポートの HTML、CSS、JavaScript を wheel の中に持ち、実行時にパッケージからの相対パスで
参照します。汎用ランナーも、一度作れば場所を問わず同一という同じ種類の資産です。置き場所として自然な
のは wheel であって、利用者ごとに繰り返すビルド手順ではありません。

同梱は DESIGN §1 とも整合します。DESIGN §1 は、Bajutsu が利用者の環境で成果物をビルドするのではなく、
ビルド済みの成果物を受け取る立場を取ります。ランナーは Bajutsu 自身のリリースパイプラインで一度だけ
ビルドされ、すぐ走る形で配布されます。

## 詳細設計

本変更は、ランナー解決に第三の、もっとも優先度の低い段を加えます。既存の `testRunner` と `build` の
つまみの下に置く同梱デフォルトです。あわせて、ランナーを wheel に入れるためのパッケージングを行います。
触れるのは、XCUITest 環境のランナーパス解決と、ビルドおよびパッケージングの設定だけです。ドライバー、
チャネル、セレクタ解決、run ループ、そしてすべてのシナリオは変わりません。

### ランナー解決に同梱デフォルトの段を加える

現在
[`bajutsu/platform_lifecycle/environments/xcuitest.py`](../../bajutsu/platform_lifecycle/environments/xcuitest.py)
は `xcuitest.testRunner` を必須とします。ターゲットがランナーを指定しないと、環境は即座に失敗します。
ランナーのパスが指定されると、そのパスがディスク上にまだ存在しないときに、`xcuitest.build`（設定
されていれば）がファイルを生成します。つまり `build` は、指定済みだが未生成のランナーを作る手段で
あって、`testRunner` 不在の代替ではありません。

本項目は `testRunner` を省略可能にし、その下に同梱デフォルトを加えます。Simulator の run が
`testRunner` も `build` もない状態で解決に達したとき、環境は失敗する代わりに同梱ランナーへ解決します。

優先順位は、明示指定がデフォルトに勝つ形を保ちます。指定された `testRunner` が最優先です。直接ビルド
されたものでも、`build` が生成したものでも同じです。同梱ランナーは、よくある場合に設定を一切要らなく
するフォールバックです。

解決は LLM と固定 sleep のいずれも持たない、決定論的なファイルパスの判断のままです。そのため
prime directive 1 と 2 には触れません。アプリに依存しない成果物をターゲットごとの設定から外すことは、
directive 3 を曲げるのではなく強めます。

### wheel がビルド済み Simulator ランナーを package data として持つ

`.xctestrun` は単独のファイルではありません。その `__TESTROOT__` は `.xctestrun` 自身のディレクトリを
基準に解決するので、隣にあるテストバンドルを一緒に配布する必要があります。そこで同梱するのは、ビルド
された products ディレクトリ全体です。`.xctestrun` と、`xcodebuild build-for-testing` が生成する
ランナーおよびホストの `.app`/`.xctest` バンドルを含みます。このディレクトリは
`bajutsu/_xcuitest_runner/` のような package data のパスに置きます。実行時には、`bajutsu/templates/` と
同じく、パッケージからの相対パスで参照します。

Hatchling の既定の wheel パッケージングは VCS を意識した仕組みです。`pyproject.toml` に `artifacts` を
指定して呼び戻さない限り、`.gitignore` のパターンに一致するファイルを既定で除外します。これは、
パッケージツリーの外にあるファイルだけを対象とする `force-include` とは別の仕組みです。

本リポジトリのルート `.gitignore` には、深さを問わず一致する `build/` と `DerivedData/` のパターンが
すでにあります。これはまさに `xcodebuild` 自身の出力ディレクトリが持つ名前です（上で引用した
`.../Runner/build/dd/Build/Products/...` のパスにもこの名前が含まれています）。そのためリリース
パイプラインは、次のどちらかを行う必要があります。

- gitignore されるセグメントを含まない package data のパスに、products を配置する。
- products に対する `artifacts` エントリを宣言する。

そうしなければ、Hatchling はエラーを出さないままランナーを wheel から除外します。基底 wheel の Linux
インストールは XCUITest を一度も実行しないため、この抜けを検出できません。

同梱するのは Simulator ランナーだけです。実機ランナーは、操作者の Apple Developer team による署名を
要します（[BE-0288](../BE-0288-ios-device-signing-batch-build/BE-0288-ios-device-signing-batch-build-ja.md)）。
Bajutsu はリリース時に署名できません。したがって `xcuitest.deviceType: device` は、引き続き明示的な
`testRunner` を要求します。指定がなければ、明確なエラーを報告します。実機にインストールできない
Simulator ランナーへは、フォールバックしません。

### 同梱ランナーは使用前に書き込み可能なキャッシュへ展開する

インストール済み wheel の package data は、読み取り専用として扱うべきです。その隣に書き込むと
インストール先を汚し、site-packages が書き込み不可のときはそのまま失敗します。しかし run は、すでに
ランナーの隣へ書き込みます。`_patch_xctestrun_env` が、`BAJUTSU_*` の起動環境を注入するために、
`.xctestrun` のパッチ済みコピーをランナー自身のディレクトリに作るからです。

そこで、同梱ランナーへの解決は、まず products ディレクトリを書き込み可能なキャッシュ（たとえば
`~/.cache/bajutsu/xcuitest-runner/<hash>/`）へ展開します。`testRunner` は、キャッシュ側のコピーへ解決
します。暖まったキャッシュは、再コピーせず再利用します。run ごとのパッチ済みコピーと unlink は、
site-packages ではなくキャッシュを対象に動きます。

コピーは、Bajutsu のバージョンではなく、同梱 products のコンテンツハッシュで鍵付けします。本リポジトリは
`pyproject.toml` の `version = "0.0.0"` を静的に固定しており、それを更新するリリースワークフローは
今のところ存在しません。バージョンで鍵付けすると、アップグレードしてもキャッシュが無効化されません。
products 自体のハッシュを使えば、バージョン更新という運用に頼らず、ランナーが実際に変わったときだけ
キャッシュが無効化されます。

デバイスプールは、1つの run set の中で複数デバイスを並行にリースします
（[BE-0291](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios-ja.md)
のデバイスごとのランナーキャッシュも、同じ並行性に依拠しています）。そのため、まだ何も入っていない
ハッシュ鍵付きのキャッシュディレクトリへ、複数のリースが同時に届くことがあります。展開は、キャッシュの
隣に作った一時ディレクトリへコピーしたうえで、それをキャッシュの場所へ rename する形を取ります。

すでに他のリースが展開し終えた場所へ rename しようとすると、失敗します（`ENOTEMPTY`）。空でない
ディレクトリへの rename は、素通りしません。負けた側のリースはこのエラーを捕捉し、勝った側が展開した
キャッシュをそのまま自分の結果として扱います。ここで守るべき保証は、並行して読みに来た側が、
キャッシュディレクトリがまだ存在しない状態か、完全に展開し終わった状態のどちらかしか見ないことです。

過去のランナービルドが残したハッシュ鍵付きの古いキャッシュを刈り込む機能は、本項目の対象外とします。
各コピーは際限なく増え続けるものではなく、有界の成果物です。蓄積したサイズが実害になった場合に、
キャッシュ削除を別項目として追加できます。

### ランナーは Bajutsu のリリースパイプラインでビルドして同梱する

同梱する products は、コンパイル済みの macOS 成果物です。バイナリの塊をリポジトリにコミットするの
ではなく、ビルド手順がそれを生成し、リリース向けに wheel をビルドするときに package data ディレクトリ
へ置きます。

ランナーが実行されるのは macOS 上の Simulator に対してだけなので、products のバイトは他の
プラットフォームでは動かない死荷重です。基底の wheel は pure-Python のまま Linux にインストールでき、
products を未使用のデータとして運ぶだけです。どこでも走る必要がある決定論的な `make check` ゲートは、
ランナーに触れません。

`doctor --target` は、ターゲットがどのランナーへ解決するか（同梱、`testRunner`、`build` のいずれか）を
報告します。同梱ランナーに対する Xcode/SDK の不一致も知らせ、`build` と `testRunner` の上書きを逃げ道
として示します。これにより、同梱ランナーがビルドされた Xcode/SDK と合わない環境でも、不透明な
`xcodebuild` の失敗ではなく明確なメッセージへ落ちます。

### 検証

分担は、BE-0019 がすでに引いている高速ゲートと実機の境界に従います。

- **高速ゲート（端末なし）。** 解決を、注入した偽の同梱ランナーディレクトリでユニットテストします。
  次の5点を確かめます。
  - `testRunner` と `build` のどちらもない config が、同梱パスへ解決すること。
  - 明示的な `testRunner` が、同梱パスに勝つこと。
  - `testRunner` のない `deviceType: device` が、同梱 Simulator ランナーへ解決せず、明確な実機ランナー
    エラーで失敗すること。
  - キャッシュへの展開が一度だけコピーし、ハッシュ鍵付きの暖まったキャッシュを再利用すること。
  - 未展開のキャッシュへ2つのリースが同時に到達しても、片方だけが展開に成功し、負けた側はそれを
    自分の結果として扱うこと。

  端末も LLM も、ゲートに載せません。
- **実機（e2e 経路）。** より重い `e2e.yml` 経路で、ターゲット config から `testRunner` 行を外した
  showcase の XCUITest シナリオを走らせます。バックエンドがビルド手順とランナーパスのどちらも用いずに、
  同梱ランナーでアプリを駆動することを実証します。

## 検討した代替案

- **ランナーをオンデマンドでダウンロードする（Playwright 方式）。** CI でランナーをビルドし、バージョン
  付きのリリース資産としてアップロードします。Bajutsu は初回に取得してキャッシュします。wheel を
  コンパイル済みの塊から解放し、ランナーのリリース周期を wheel から切り離せます。一方で、初回に
  ネットワーク依存とダウンロードおよび検証の経路が加わり、成果物のホスティングをメンテナに負わせます。
  wheel への同梱は、初回をオフラインかつ自己完結に保ちます。代償は、macOS 以外のインストールで動かない
  バイトを運ぶことです。ダウンロード方式は、同梱の wheel サイズや Xcode 結合の代償が高くつくとわかった
  場合の、自然なフォールバックとして残します。
- **同梱ソースからランナーをオンデマンドでビルドする。** ランナーの Swift ソース（すでに
  `BajutsuKit/Runner/` にあります）を配布し、初回に `xcodebuild build-for-testing` をキャッシュへ
  走らせます。コンパイル済み成果物を運ばず、ホストの Xcode に常に合致します。しかし、本項目が取り除こう
  とする端末ごとのビルドを、そのまま呼び戻します。クローンし直したどの環境でも、最初の XCUITest run が
  ビルドの全コストを払います。オンデマンドビルドの選択肢は、既存の `xcuitest.build` つまみがすでに
  カバーしています。同梱デフォルトはビルドを避けるために存在するのであって、ビルドを別の場所へ移す
  ためではありません。
- **ビルド済み products をリポジトリにコミットする。** ビルド済み products をバージョン管理に置けば、
  ビルド時の手順なしに、すべての sdist と wheel へ入ります。しかし、ランナーが変わるたびに再生成して
  コミットし直す必要のある数メガバイトのバイナリで、リポジトリを膨らませます。ツリーも1つの Xcode
  バージョンへ結び付きます。成果物をリリースパイプラインで生成すれば、バイナリを履歴から外したまま
  wheel には同梱できます。
- **ランナーを別のコンパニオンパッケージとして配布する。** extra としてインストールする macOS 専用の
  `bajutsu-xcuitest-runner` パッケージにすれば、基底の wheel をバイト単位で純粋に保てます。しかし、
  リリースが2つのパッケージに分かれます。XCUITest が動く前に、利用者が思い出してインストールする手順も
  増えます。これは、別の場所に準備の手間を呼び戻します。macOS 以外では動かないバイトを運ぶ単一の
  wheel は、インストールを1手順に保ちます。

## 進捗

> 作業の進行に合わせて更新してください。チェックリストは *Detailed design* の MECE な作業分解を写し
> (作業単位1つにつき1つのボックス)、ログは何がいつ変わったかを(古い順に)記録し、PR を結びます。

- [x] **ランナー解決**：XCUITest 環境で `testRunner`/`build` の下に同梱デフォルトの段を加えます。明示的な
  config は引き続き優先し、`deviceType: device` は引き続き明示ランナーを要求します。
- [x] **キャッシュへの展開**：同梱 products をコンテンツハッシュで鍵付けした書き込み可能なキャッシュへ
  コピーし、同時展開の競合を処理したうえで `testRunner` をコピー側へ解決します。run ごとの
  パッチ済みコピー手順はそのままにします。
- [x] **パッケージング**：ビルド済みの Simulator products を、wheel のビルドが走る前に package data
  ディレクトリの下へ置き、それを生成するリリースパイプラインのビルド手順を加えます。基底の wheel は
  Linux にインストールできる状態を保ちます。
- [x] **doctor / 開示**：解決したランナーの出所を報告し、Xcode/SDK の不一致を上書きの逃げ道とともに
  知らせます。両方の半分が実装済みです。`doctor --target` は
  `xcuitest runner: bundled (wheel-shipped Simulator runner)` / `testRunner: <path>` を表示し、
  ターゲットが同梱ランナーへ解決するときは、ホストの Xcode / Simulator SDK のメジャーが
  `make runner-bundle` の記録した（`build-info.json`）ツールチェインと食い違う場合に `⚠` 行を足し、
  `xcuitest.testRunner` / `xcuitest.build` を逃げ道として案内します。
- [x] **検証**：高速ゲートの解決テスト(同梱デフォルト、上書きの優先、実機エラー、キャッシュ再利用、
  同時展開の競合)と、build-info / ツールチェイン不一致の開示をユニットテストで押さえました。実機 e2e run
  も同梱ランナーの経路を通すようになり、`xcuitest (multi-touch)` ジョブが `make runner-bundle` で
  ランナーを配置してから、`testRunner` を持たない config（`showcase.bundled-runner.config.yaml`）で
  `smoke.yaml` を SwiftUI と UIKit の a11y アプリの両方に対して走らせ、app-agnostic な同梱ランナーが
  runner-build もランナーパスもなしにどちらのツールキットも駆動することを確かめます。

ログ：

- [#1221](https://github.com/bajutsu-e2e/bajutsu/pull/1221) — ランナー解決・キャッシュへの展開・
  パッケージングを実装し、高速ゲートのテストを追加しました。`doctor --target` の解決済みランナー
  出所の表示（`runner_source` / `xcuitest_runner_summary`）も追加しましたが、Xcode/SDK の不一致検知は
  スコープを絞って今回は見送り、別途のフォローアップとしました。
- [#1276](https://github.com/bajutsu-e2e/bajutsu/pull/1276) — 見送っていた 2 つの半分を完了しました。
  doctor の Xcode/SDK 不一致の開示（`make runner-bundle` が記録する `build-info.json` をホストの
  ツールチェインと比べ、逃げ道を案内）と、実機での同梱経路 e2e（`testRunner` を持たない
  `showcase.bundled-runner.config.yaml` を、`make runner-bundle` のあとに `smoke.yaml` で SwiftUI と
  UIKit の a11y アプリ両方に対して実行）です。本項目を実装済みへ移します。

## 参考

- [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md)：XCUITest バックエンドと、
  本項目が拡張する `testRunner`/`build` のランナー配布モデル。
- [BE-0288](../BE-0288-ios-device-signing-batch-build/BE-0288-ios-device-signing-batch-build-ja.md)：
  署名付きの実機ランナー。同梱デフォルトを Simulator 限定にする理由。
- [DESIGN §1](../../DESIGN.md)：Bajutsu は利用者の環境でビルドするのではなく、ビルド済みの成果物を
  受け取る。

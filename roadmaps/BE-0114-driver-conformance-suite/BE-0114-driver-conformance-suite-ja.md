[English](BE-0114-driver-conformance-suite.md) · **日本語**

# BE-0114 — backend 非依存の挙動を検査する driver conformance suite

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0114](BE-0114-driver-conformance-suite-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装中** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0114") |
| 実装 PR | [#632](https://github.com/bajutsu-e2e/bajutsu/pull/632), [#644](https://github.com/bajutsu-e2e/bajutsu/pull/644) |
| トピック | プラットフォーム拡張（Android / Web / Flutter） |
| 関連 | [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md), [BE-0042](../BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md), [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md), [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md), [BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md) |
<!-- /BE-METADATA -->

## はじめに

**driver conformance suite** を足します。同じ backend 非依存の仕様を、parametrize した 1 つのテスト
本体ですべての backend に対して流すものです。いまドライバのテストは backend ごとに書かれており、各
backend が守るべき決定性の中核仕様（曖昧なセレクタは失敗する、0 件は失敗する、`capabilities()` の
申告が実挙動と一致する）は、backend ごとに個別に検査される（あるいは検査されない）状態です。共通の
conformance suite（TCK、technology compatibility kit に相当）は、「どの backend も同じように振る舞う」
を願望から検査に変えます。

## 動機

プライムディレクティブ 3 は、ツール、ドライバ、ランナーが backend 非依存であることを求めます。
プラットフォームは 1 つの界面の背後にある backend です。これを実現している決定性の中核仕様は、いま
2 つのものが守っています。`drivers/base` の共通実装と、各ドライバ固有のテストです。全 backend に
同じ仕様を一度に課す共通の契約テストはありません。

backend の数はこれから増えます。いまは fake、idb、Playwright があり、XCUITest（BE-0019）が進行中、
Android（BE-0007）が提案中です。backend が増えると、backend ごとの微妙な挙動差が backend 非依存の
約束を静かに侵食しやすくなります。そしてそれが最も起きやすいのは、ドライバが共通実装を迂回する箇所、
すなわち固有の query 解決や固有の settle 処理です。いまの体制では、この差を検出する仕組みがありません。
曖昧なセレクタで最初の一致を tap する backend や、0 件の query に成功を返す backend があっても、自身の
テストは通り、落とす共通テストがないからです。conformance suite の費用対効果は、backend が増える直前の
いまが頂点です。以後に足す backend はすべて、同じ仕様に対して生まれることになるからです。

## 詳細設計

作業は次の 5 つに MECE に分解できます。

### 1. backend 非依存の契約を列挙する

`Driver` Protocol と DESIGN を根拠に、すべての `Driver` が満たすべき不変条件を書き下します。少なくとも
次を含めます。曖昧なセレクタ（2 件以上の一致）は最初の一致に作用せず失敗する、0 件の query は成功を
返さず失敗する、`capabilities()` の申告が観測される挙動と一致する、待機の意味論を固定 sleep ではなく
条件待ちが支える、証跡とエラーの形が backend をまたいで一様である。この列挙が、backend が満たすべき
定義になります。

### 2. parametrize した conformance suite を作る

ドライバのインスタンスを（fixture や parametrize で）受け取り、契約の表明をそれに対して走らせる 1 つの
pytest スイートを実装します。同じテスト本体がすべての backend に対して実行されます。スイートは
`Driver` の界面に依存し、backend の内部には決して依存しません。

### 3. Linux で動く backend を Simulator なしで走らせる

FakeDriver を `make check` に組み込み、conformance suite を Simulator なしに Linux 上で PR ごとに
走らせます。Playwright backend は、高速ゲートではなく、`bajutsu[web]` とブラウザバイナリを入れた別の
web CI ジョブで走らせます。これは `web-e2e.yml` がいま Playwright を走らせている形と同じです。

### 4. オンデバイスの backend を E2E 経路で走らせる

idb と XCUITest を組み込み、同じスイートをオンデバイスの E2E 経路（macOS と Simulator）で走らせ、
オンデバイスの backend が同一の契約を満たすことを示します。ピース 2 のスイートを再利用し、第 2 の仕様は
作りません。

### 5. `capabilities()` の適合と文書化

各 backend の申告する `capabilities()` が観測される挙動と一致することを表明し（BE-0082 の capability
preflight と結び付けます）、conformance の契約を、新しい backend の実装者が目標にする定義として文書化
します。これにより BE-0007（Android）と BE-0019（XCUITest）は、ドライバ界面についての具体的な「完了」
の定義を得ます。

### 機械的に検査できる成果

同じ parametrize したスイートが、すべての backend で通ること。契約の不変条件に反する backend（曖昧な
セレクタで最初の一致を tap する、0 件で成功する、`capabilities()` を過大申告する）は、スイートを
落とします。スイートは決定的でモデルを含みません。ディレクティブ 2 と 3 を、backend をまたいで一様に、
実行できる形にしたものです。

### プライムディレクティブとの整合

スイートは決定的な経路だけで走ります。機械的に検査できる表明のみで、LLM はありません。ディレクティブ 3
（backend 非依存）を、backend ごとの行儀ではなく共有され強制される仕様にすることで強め、ディレクティブ
2（決定性）を、同じ待機 / セレクタ / 0 件の規則をすべての backend で検査することで強めます。

## 検討した代替案

- **backend ごとのテストを続ける（現状）。** 却下します。共有の仕様がないため、`drivers/base` を迂回
  するどの経路の差も検出できず、新しい backend はそのたびに不変条件の検査を作り直す（あるいは省く）
  ことになります。
- **`drivers/base` の共通実装だけを通してテストする。** 却下します。ドライバは共通層を迂回する
  backend 固有の query と settle のコードを抱えており、差が隠れるのはまさにそこです。スイートは共通の
  base だけでなく、実際のドライバのインスタンスに対して走らせなければなりません。
- **Android / XCUITest が入るまでスイートを先送りする。** 却下します。価値が最も高いのは、それらが入る
  **前**です。いまスイートを作れば、新しい backend はそれぞれ最初から契約に対して開発され、差が入り
  込んだ後で後付けで合わせることにはなりません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] backend 非依存の契約を列挙する（曖昧 / 0 件 / `capabilities()` / 待機 / 証跡の不変条件）
- [x] `Driver` の界面に対して parametrize した conformance suite を作る
- [x] FakeDriver を高速な Linux ゲート（`make check`）で走らせ、Playwright は別の web CI ジョブで走らせる
- [x] idb と XCUITest をオンデバイスの E2E 経路で走らせる（同じスイート）
- [x] `capabilities()` の適合を検査し、契約を新しい backend の「完了」定義として文書化する

ログ:

- 2026-07-04: 第 1 スライス。実行可能な契約（`tests/driver_conformance.py`）と FakeDriver の
  conformance suite（`tests/test_driver_conformance.py`）を高速な Linux ゲートに載せ、契約を
  `docs/architecture.md` に文書化しました。Playwright（web CI）と idb / XCUITest（オンデバイス
  E2E）はこの項目で追跡します。
- 2026-07-04: Playwright スライス。同じ契約を実際の headless Chromium に対しても走らせるように
  しました（`tests/test_driver_conformance_web.py`）。各 conformance 画面を `data-testid` の HTML
  として実際の `PlaywrightDriver` 上に描画します。`web-e2e.yml` に新設した `web-conformance`
  ジョブで実行し、高速ゲートでは走らせません。`web` という pytest マーカーと `-m 'not web'` で
  除外するため、`web` extra が入っていてもゲートはブラウザなしのままです。idb / XCUITest
  （オンデバイス E2E）は残ります。
- 2026-07-04: オンデバイススライス。同じ契約を実際の iOS backend である idb と XCUITest に対しても
  走らせるようにしました。指定した識別子だけを描画する `SHOWCASE_CONFORMANCE` の起動 env を渡して
  showcase アプリを再起動します（`tests/test_driver_conformance_ondevice.py`、`ConformanceView.swift`。
  deeplink ではなく起動 env を使うのは、`simctl openurl` がカスタムスキームに対して iOS の
  「アプリで開きますか?」という確認ダイアログを出すためです）。`e2e.yml` に新設した `conformance`
  ジョブがアプリと常駐ランナーをビルドし、`launch_driver` を通じて両 backend を直列で走らせます。
  `ondevice` という pytest マーカー（`-m 'not web and not ondevice'`）で高速ゲートからは除外します。
  Simulator 上で検証し、全 18 ケース（backend ごとに 9 ケース）が通ることを確認しました。これで 5 つの
  ピースの作業分解が完了します。

## 参考

`Driver` Protocol と `drivers/base` の共通実装（スイートが列挙する契約と、backend が迂回する層）、
既存の backend ごとのドライバテスト（fake / idb / Playwright。本項目が 1 つの仕様に統合するもの）、
[DESIGN.md](../../../DESIGN.md) と [architecture.md](../../../docs/ja/architecture.md)（backend
非依存の思想と、実装状態の source of truth）、
[BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)
（本項目が検査するクロスプラットフォームの抽象）、
[BE-0042](../BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md)
（スイートが parametrize する backend レジストリ）、
[BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check-ja.md)
（この `capabilities()` の適合が結び付く capability preflight）、
[BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend-ja.md) と
[BE-0007](../BE-0007-android-backend/BE-0007-android-backend-ja.md)（契約から具体的な「完了」定義を
得る、これから入る backend）。

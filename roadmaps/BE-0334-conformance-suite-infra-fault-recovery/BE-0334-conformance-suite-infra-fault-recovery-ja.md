[English](BE-0334-conformance-suite-infra-fault-recovery.md) · **日本語**

# BE-0334 — 実機 conformance スイートに run パイプラインと同じインフラ障害からの復旧を持たせる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0334](BE-0334-conformance-suite-infra-fault-recovery-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装中** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0334") |
| 実装 PR | [#1448](https://github.com/bajutsu-e2e/bajutsu/pull/1448), [#1450](https://github.com/bajutsu-e2e/bajutsu/pull/1450), [#1452](https://github.com/bajutsu-e2e/bajutsu/pull/1452) |
| トピック | プラットフォーム対応 |
| 関連 | [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md), [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md), [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience-ja.md), [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`bajutsu run` は Simulator のインフラ障害から復旧します。シナリオ実行中の
`base.BackendCrashError` は verdict ではなくインフラの問題として扱われます。死んだ lease は破棄され、
コールド再起動で新しいデバイスを lease し、`crash_retries` を上限としてシナリオを再実行します
（BE-0323）。コールド起動そのものも、診断可能かつ自己修復するようになっています（BE-0319）。

実機のドライバ conformance スイートは、この復旧を1つも受け継いでいません。そして**マージを止める** iOS
ジョブのうち、Bajutsu のドライバでデバイスを駆動しながらパイプラインの復旧を経ない唯一のジョブです。
`codegen` はネイティブの XCUITest を実行するため、そもそもこのパイプラインの経路にありません。
`bundled-runner` は `bajutsu run` 経由でデバイスに到達するため、すでに復旧を継承しています。
スイートは module スコープの `pytest` fixture
から `launch_driver` を直接呼んでデバイスを得ます。iOS ワークフロー自身もコメントでそう述べています。
「`bajutsu run` ではなく pytest の実機ハーネスである」と。
したがってスイート実行中の Simulator 障害は、原因を作りえなかったプルリクエストの必須チェック
`E2E (iOS)` を赤くします。本項目は、ハーネスでインフラ障害を分類し、パイプラインと同じように復旧させる
ことを提案します。本物の契約違反は、今と同じだけ明確に落ちたままにします。

## 動機

この失敗は仮定ではなく観測です。PR [#1405](https://github.com/bajutsu-e2e/bajutsu/pull/1405) の差分は、
CI のパスフィルタのロジック、Makefile の lint 対象リスト、docstring 1行、ドキュメントだけでした。それでも
`conformance (xcuitest)` が次のように落ちました。

```
bajutsu.drivers.xcuitest.XcuitestRunnerCrashError: runner channel POST /tap failed: timed out
FAILED tests/test_driver_conformance_ondevice.py::TestXcuitestDriverConformance::test_unique_match_acts_without_error
1 failed, 16 passed in 221.27s
```

コードを変えずにジョブを再実行すると合格しました。同じ時間帯に `golden (xcuitest)` が
`xcuitest runner did not come up: health never ready` で落ちています。無関係な別ブランチの
`actuation (xcuitest)` も、同じ readiness の signature で落ちました。Simulator レーンの既知の
フレーキー（BE-0218）が、混雑したホストを引いたジョブに現れているということです。

conformance ジョブは、この障害が落ちる先として最も悪い場所です。理由は2つあります。1つめは、
**マージを止める**ことです。集約チェックの `needs:` から意図的に外されている `golden` や `visual` と
違い、`conformance` は必須なので、フレーキーは参考シグナルとして出るのではなくマージを塞ぎます。
2つめは、lease が **module スコープ**であることです。1回のクラッシュのあと、そのモジュールの後続テストは
すべて死んだランナーを駆動します。したがって1件のインフラ障害が、1テストの損失ではなくスイート全体へ
波及しえます。

要点は非対称性にあります。どちらのジョブも同じ Simulator に対して同じ XCUITest バックエンドを駆動し、
`conformance` を落とすのと同じ障害を `run` は生き延びます。ドライバ契約に、復旧に値しない理由はありません。
復旧が置かれているコード経路を通っていない、というだけのことです。

## 詳細設計

4つのユニットに分け、それぞれ単独で着地できるようにします。

### ユニット1 — ハーネスで障害を分類する

インフラ障害と契約違反を、ハーネスの境界で切り分けます。ランナーのクラッシュ
（`XcuitestRunnerCrashError`）、readiness のタイムアウト、lease の立ち上げ失敗はインフラです。
セレクタの誤解決、何も起こさないアクチュエータ、壊れた形で返るツリーは契約違反であり、今と同じく即座に
落ちなければなりません。

この切り分けは、ドライバがすでに投げている例外の型にもとづきます。したがって判断は Python のクラスに対する
決定論的な分岐にとどまります。大規模言語モデルは関与せず、スイートはドライバ契約の判定者のままです。
prime directive 1 が要求するとおりです。

### ユニット2 — インフラ障害では lease を取り直して再試行する

インフラ障害のときは、lease を破棄し、デバイスをコールド再起動し、影響を受けたテストを再実行します。
上限はパイプラインの `crash_retries` に対応する再試行予算とします。第2の実装を書くのではなく、
パイプラインの既存の復旧を再利用します。そうすれば2つがドリフトしません。

予算は小さく、かつ明示的でなければなりません。無制限に再試行するスイートは、慢性的なインフラの問題を
「遅い緑」に変えてしまいます。それは赤よりも悪い状態です。レーンが手当てを必要としているという signal が
消えてしまうからです。

### ユニット3 — module スコープの lease を封じ込める

lease を module スコープのままにするかを決めます。module スコープの fixture は、高価なコールド起動1回を
17件のテストで分割償却します。それが存在理由です。しかし同時に、1回のクラッシュが後続の全テストを汚す
ことも許します。障害の次のテストで遅延的に lease を取り直せば、通常時の償却を保ったまま、悪いときの波及を
止められます。

### ユニット4 — 再試行をすべて報告する

復旧のたびにジョブのログとアップロードする成果物へ出力し、回数を数えます。痕跡を残さない再試行は、劣化して
いくレーンを「ただ遅いだけのレーン」に見せてしまいます。そして回数こそが、根本の障害が稀なままなのか
悪化しているのかを保守担当に伝えるものです。

## 検討した代替案

- **`conformance (xcuitest)` を必須ゲートから降格する。** 却下します。ドライバ契約は決定論的で実行環境に
  依存せず、それはこのリポジトリがゲート対象のチェックに求める性質そのものです。フレーキーは下にある
  Simulator から来ており、チェックから来ているのではありません。降格は、インフラの問題を避けるために
  本物のゲートを手放すことになります。
- **これまでどおり手でジョブを再実行する。** 現状がこれです。発生ごとに人の注意を費やし、赤い必須チェックを
  読まずに再実行する習慣を寄稿者に教えてしまいます。本物の regression が最終的に見逃される道筋がそれです。
- **conformance スイートを `bajutsu run` 経由で走らせる。** 却下します。スイートは設計上 `pytest` の契約
  ハーネスであり、構造は Android と web のレーンの conformance ジョブと共通です。そして assertion は
  シナリオ水準ではなくドライバ水準です。復旧を受け継ぐためにシナリオへ作り替えると、配管に合わせて契約を
  歪めてしまいます。
- **代わりに readiness の上限を引き上げる。** それだけでは足りません。BE-0319 がすでにコールド起動を
  診断可能かつ自己修復にしたうえで、`health never ready` の失敗は今も起きています。したがって予算を広げても
  閾値が動くだけで、スイート実行中に起きるクラッシュには手が届きません。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] ユニット1 — ハーネスの境界で、インフラ障害と契約違反を分類する。
- [x] ユニット2 — インフラ障害で lease を取り直して再試行し、パイプラインの復旧を再利用する。
- [x] ユニット3 — module スコープの lease を封じ込め、1回のクラッシュが波及しないようにする。
- [ ] ユニット4 — 復旧をすべてログと成果物に報告し、回数を数える。

## 参考

- [`tests/test_driver_conformance_ondevice.py`](../../tests/test_driver_conformance_ondevice.py) —
  本項目が変えるハーネスです。module スコープの fixture から `launch_driver` を呼びます。
- [`bajutsu/runner/pipeline.py`](../../bajutsu/runner/pipeline.py) — `crash_retries` と既存の復旧が
  置かれている場所です。
- [`docs/ja/ci.md`](../../docs/ja/ci.md) — iOS のどのジョブがマージを止め、どれが参考シグナルに
  とどまるかです。
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite-ja.md) — スイートが
  強制するドライバ conformance の契約です。
- [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn-ja.md)
  — run パイプラインが獲得した readiness とクラッシュの再起動であり、ユニット2 が再利用する復旧です。
- [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience-ja.md) —
  診断可能で自己修復するコールド起動であり、その `health never ready` の失敗は今も CI に届いています。
- [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)
  — Simulator レーンのフレーキーの経緯であり、`golden` と `visual` が必須でない理由です。

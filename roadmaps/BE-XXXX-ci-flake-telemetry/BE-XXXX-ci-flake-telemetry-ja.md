[English](BE-XXXX-ci-flake-telemetry.md) · **日本語**

# BE-XXXX — 実機系 CI ジョブを毎回計測し、繰り返す不安定な失敗を原因までたどれるようにする

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-ci-flake-telemetry-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | [#2505](https://github.com/bajutsu-e2e/bajutsu/pull/2505) |
| トピック | CI / build infrastructure |
| 関連 | [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics-ja.md)、[BE-0367](../BE-0367-android-ci-emulator-diagnostics/BE-0367-android-ci-emulator-diagnostics-ja.md)、[BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)、[BE-0220](../BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix-ja.md) |
<!-- /BE-METADATA -->

## はじめに

継続的インテグレーション（CI）の実機系エンドツーエンド（E2E）ジョブが、変更内容では説明できない形で
ときどき失敗します。iOS のジョブは XCUITest で Simulator を動かし、Android のジョブは adb で
エミュレーターを動かします。再実行するとたいてい通るので、原因はわからないまま残ります。

この項目では、ジョブごとに機械可読な記録を 1 つ残します。記録は失敗したジョブだけでなく、成功した
ジョブでも書きます。続いてスクリプトが実行をまたいで記録を集め、失敗したジョブと成功したジョブを
比べます。この比較から、失敗したジョブどうしに共通し、成功したジョブには少ない要因の候補がわかります。

## 動機

最近の失敗の 1 つが、この問題の形をよく表しています。`actuation (xcuitest)` ジョブで、あるシナリオの
最初の `wait` が 20 秒でタイムアウトしました。同じ時間帯に、runner channel の読み取り
（`GET /elements`）が 2 回タイムアウトしてリトライしました。さらに、すべてのシナリオで
`recordVideo` が 5 秒以内に開始を報告しませんでした。テスト対象の変更は、どの経路にも触れていません。

それぞれの症状の証拠はすでに集まっています。ただし、どの証拠も 1 つのジョブについて 1 つの問いにしか
答えません。

| 証拠 | 現在の置き場所 | 答えられないこと |
|---|---|---|
| Run の manifest（ステップの所要時間、判定） | ジョブの `runs/` artifact | ホストが高負荷だったか |
| ホストのサンプル（`top`、`vm_stat`、`free`） | `runs/diagnostics/host-telemetry.log`（自由形式のテキスト） | 成功したジョブと比べてどうか |
| 描画プローブ、stall capture | `runs/diagnostics/`（自由形式のテキスト） | 成功したジョブでどのくらい起きるか |
| runner image、ハードウェア、Xcode のバージョン | `runs/diagnostics/host-environment.txt`（iOS、自由形式のテキスト） | image ごとの失敗率 |
| channel のリトライ、開始を確認できなかった録画 | ジョブのログ（警告として） | 回数そのもの |
| ステップの所要時間、キュー待ち時間 | GitHub のアプリケーションプログラミングインターフェース（API） | 誰かが問い合わせない限り何も |

これらを組み合わせれば、1 つの失敗したジョブは診断できます。しかし 30 回分の実行にまたがる傾向は
見えません。証拠を並べてそろえる仕組みがないからです。不安定な失敗は毎回再実行で終わるので、原因は
直らないままです。[BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics-ja.md)
と [BE-0367](../BE-0367-android-ci-emulator-diagnostics/BE-0367-android-ci-emulator-diagnostics-ja.md)
は、1 つの失敗を説明できるようにしました。この項目は、多数の失敗を比べられるようにします。

成果は観察できます。この変更のあと、あるワークフローの直近の実行に対してコマンドを 1 つ実行すると、
要因の候補ごとに、その兆候を持つ失敗したジョブと成功したジョブの数が出力されます。runner image、
ハードウェア、ツールチェインのバージョン、時間帯ごとの失敗率も出力されます。失敗したジョブで多く、
成功したジョブではまれな要因が、最初に直すべき要因です。

## 詳細設計

この設計は prime directive の 1 番目を守ります。以下のどの単位も、観測して報告するだけです。シナリオの
判定を読んで変えることはなく、言語モデルも使いません。要因の候補は計測値に対する固定の閾値の規則であり、
読み手はヒントとして扱います。

この設計は CI の速さも保ちます。ジョブごとのステップは、ジョブがすでに書いたファイルを読み直すだけです。
デバイスへの呼び出しも、ネットワークへの呼び出しもしません。重い収集は、BE-0361 と BE-0367 が決めた
とおり、失敗時だけに残します。

### Unit 1：ジョブごとに 1 つの計測記録

`scripts/ci_telemetry.py summarize` は、各レーンの診断アクションの `collect` フェーズの最後に動きます。
対象は `collect-ios-diagnostics` と `collect-android-diagnostics` の 2 つの composite action です。
これらをすでに呼んでいるジョブは、ジョブ自体を変えずに記録を得ます。スクリプトは標準ライブラリだけを
使い、runner に入っている `python3` で動きます。環境の同期より先に `collect` に達するジョブがあるため
です。Xcode に付属する Python 3.9 でも動くように書いています。

記録 `ci-telemetry.json` は次の項目を持ちます。

- **結合キー**：GitHub が環境変数で渡すワークフロー、ジョブ、run の ID、試行回数、コミット、
  イベント、runner 名です。runner image の名前とバージョンも含みます。
- **ホストのキー**：OS のバージョン、ハードウェアのモデル、中央処理装置（CPU）の数、メモリー量、Xcode のバージョンです。
  iOS では BE-0361 が書く `host-environment.txt` から読み、Android では Linux ホストから読みます。
- **ホストの負荷**：1 分間のロードアベレージの最大値、CPU のアイドル率の最小値、I/O 待ち、
  ハイパーバイザーによる steal、空きメモリーの最小値、ディスク転送量の最大の増分です。既存のサンプラーの
  ログからすべて解析します。
- **描画プローブ（iOS）**：実行前のスクリーンショットにかかった時間と、5 秒間の録画がバイトを
  生んだかどうかです。
- **stall capture**：`stall-NN-<reason>-<pid>` というディレクトリ名から数えた、トリガーごとの回数です。
- **シナリオ**：各 manifest の各シナリオについての判定、失敗したアクション、合計の所要時間、
  最初の `wait` の所要時間、最長の `wait` の所要時間です。

最初の `wait` を別の項目にするのは、遅いデバイスの影響を最初に受けるからです。アプリは起動した直後で、
ふだんは 1 秒で解ける待機がタイムアウトまで使い切ることがあります。

記録は、すべての run の manifest の写しと一緒に `runs/diagnostics/ci-telemetry/` に置きます。
アクションはこのディレクトリを、独立した artifact `ci-telemetry-<job>-<attempt>-<一意な接尾辞>` として
アップロードします。artifact を分けておけば、実行をまたぐ集計で各実行の動画をダウンロードせずに
済みます。接尾辞は、`bundled-runner (xcuitest)` のように 1 つのジョブで 2 回収集する場合にも名前を
一意に保ちます。アップロードのステップには `continue-on-error` を付けるので、計測記録のアップロードが
ジョブを失敗させることはありません。

### Unit 2：区間を測る CPU の読み取り

どちらのサンプラーも 20 秒ごとに `top` を 1 回だけ取ります。1 回だけの `top` が報告する CPU 時間は
起動以降の平均なので、そのアイドル率は stall の瞬間について何も教えてくれません。そこで各サンプラーは、
2 回分のサンプルを取る `top` から、直前の 1 秒を測った行を 1 行追加します。この行には `Interval` という
接頭辞を付け、解析ではこの行を優先します。Linux では同じ行から I/O 待ちと steal も得られます。
追加の負荷は、もともと 20 秒眠るバックグラウンドのループの中での 1 秒です。

### Unit 3：実行をまたいで記録を API と結合する

`scripts/ci_telemetry.py fetch --workflow <file>` は、`gh` にサインインした運用者が CI の外で
実行します。あるワークフローの直近 `N` 回の完了した実行のそれぞれについて、次の 4 つを行います。

1. API から実行のジョブを一覧し、各ステップの所要時間と結論、ジョブのキュー待ち時間を得ます。
2. 実行の `ci-telemetry-*` artifact をダウンロードし、各記録を runner 名でジョブに結びつけます。
3. 各ジョブのログから、ここで意味を持つ警告を数えます。対象は channel のリトライ、実行中の channel の
   クラッシュ、開始を確認できなかった録画、ネットワークエラーです。
4. 各ジョブについて、そのジョブの開始時点で走っていた、取得済みのほかのジョブの数を数えます。

計測記録の artifact を持たないジョブにも、API だけから記録を作ります。そうすれば、`collect` の前に
止まったジョブも失敗として分母に残ります。API がログを返さないジョブは、0 ではなく不明として記録します。
キャンセルされたジョブは除きます。E2E のワークフローは新しい push が来ると古い実行をキャンセルする
ので、その実行は兆候を持ちません。結合には runner 名を使うので、1 つの実行のなかで runner 名が重なる
ジョブは API だけの記録にします。

ログのパターンは、`bajutsu` 自身が書く警告を写したものです。テストが各パターンを元の書式文字列に
固定しているので、警告の文言を変えると、黙って 0 を数え続ける代わりにゲートが失敗します。

続いて `scripts/ci_telemetry.py report <dir>` が 4 つの表を出力します。

- 要因の候補ごとの、失敗したジョブと成功したジョブでの件数
- 指標ごとの、失敗したジョブと成功したジョブでの中央値
- ジョブ、runner image、OS、Xcode のバージョン、ハードウェア、4 時間ごとの協定世界時（UTC）の時間帯ごとの失敗率
- 要因の候補を 1 つも持たない失敗したジョブの名前と、失敗したステップ

取得結果にはすべての manifest も入ります。`bajutsu flakiness --history <dir>/manifests` はこの
ディレクトリをそのまま読み、同じフィンガープリントのまま判定が反転する頻度でシナリオを順位付けします。

### Unit 4：要因の候補と、その兆候

要因の候補ごとに、それを見分ける兆候を対応づけます。`candidate_causes` は、成功したジョブを含むすべての
記録にこの規則を当てます。成功したジョブでも同じくらい現れる兆候は、失敗の説明になりません。報告が
その事実を示せるのは、両方の側に同じラベルが付いているときだけです。

| 要因の候補 | 兆候と閾値 |
|---|---|
| `host-resources` | CPU 1 個あたりのロードが 1.5 超、区間で測ったアイドル率が 5％ 未満、空きメモリーが 10％ 未満、I/O 待ちが 20％ 超、または steal が 10％ 超 |
| `device-slow-or-wedged` | デバイスの起動や待機のステップが 240 秒超、実行前のスクリーンショットが止まったか 10 秒超、または `runner-crash` の stall capture |
| `channel-timeout` | ジョブのログにある runner channel のリトライか、実行中の channel のクラッシュ |
| `video-start` | ログにある録画開始の未確認、描画プローブの録画が 0 バイト、または `video-no-bytes` か `screenrecord-no-growth` の stall capture |
| `network-or-deps` | ログにあるネットワークエラー、またはセットアップ、インストール、ダウンロードだけを行うステップの失敗 |
| `runner-contention` | 300 秒を超えてキューで待ったジョブ |
| `app-wait-timeout` | 上のどの兆候もない、失敗した `wait` ステップ |
| `test-assertion` | 上のどの兆候もない、それ以外の失敗したステップ |

最後の 2 つは残余の規則です。インフラの兆候を伴わずに失敗したシナリオを指し、本当の不具合や、
短すぎる待機はここに現れます。時間帯と runner image は、要因ではなく比較の軸として扱います。
どちらも、1 つのジョブが兆候として持つものではないからです。

## 検討した代替案

- **`bajutsu` 自身が構造化したカウンターを出力する。** runner が channel のリトライや録画の結果を、
  ログ行ではなく JavaScript Object Notation（JSON）のファイルに書く案です。CI だけのために driver と証跡のコードを変えることになります。
  ジョブのログから既存の警告を数えればコアの変更は要らず、元の書式文字列に固定するテストが、ログ解析に
  つきものの危うさを取り除きます。
- **新しいものはアップロードせず、`runs/` の artifact を読む。** この案では、集計のたびに各実行の動画と
  スクリーンショットを、1 ジョブあたり数百 MB ダウンロードすることになります。数 KB の独立した artifact
  なら、数十回分の実行にまたがる集計も安く済みます。
- **すべてをジョブのログから読む。** ホストのサンプル、描画プローブ、manifest はジョブのログに
  出ないので、ログだけでは運べません。
- **言語モデルで失敗を分類する。** CI の判定のそばに非決定的な判定者を置くことになり、prime directive の
  1 番目に反します。固定の閾値はレビューでき、閾値が誤っていれば、報告の成功したジョブの列がそれを示します。
- **サンプラーの間隔を短くする。** 負荷の系列は細かくなりますが、測っているホストの負荷を増やします。
  区間の CPU の行なら、1 サンプルあたり 1 秒の追加で済みます。

## 進捗

> 作業の進行に合わせてここを最新に保ってください。チェックリストは「詳細設計」の `MECE` な
> 作業分解を鏡写しにします（作業単位ごとに1つのボックス）。ログには何がいつ変わったかを
> 古い順に、PR へのリンク付きで記録します。

- [x] Unit 1：`summarize` が両レーンの `collect` フェーズで `ci-telemetry.json` と manifest を書き、
  独立した artifact としてアップロードする
- [x] Unit 2：両方のホストサンプラーが、2 回分のサンプルによる `Interval` の CPU の行を追加する
- [x] Unit 3：`fetch` が記録を API のジョブ、ステップ、ログの情報と結合し、`report` が実行をまたぐ表を
  出力する
- [x] Unit 4：`candidate_causes` が要因ごとに兆候を対応づけ、`docs/ci.md` と `docs/ja/ci.md` が
  対応表と分析の手順を説明する

## 参考

- [BE-0361 — iOS の CI 失敗の原因究明に必要な多層の診断ログを収集する](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics-ja.md)
- [BE-0367 — Android の CI 失敗の原因究明に必要な多層の診断ログを収集する](../BE-0367-android-ci-emulator-diagnostics/BE-0367-android-ci-emulator-diagnostics-ja.md)
- [BE-0049 — 決定性／フレーキネス監査](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit-ja.md)
- [BE-0220 — DB の Run 履歴からフラッキーテストをサジェストし、Run 横断で修正を提案する](../BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix-ja.md)
- [`scripts/ci_telemetry.py`](../../scripts/ci_telemetry.py)
- [`docs/ja/ci.md`](../../docs/ja/ci.md)

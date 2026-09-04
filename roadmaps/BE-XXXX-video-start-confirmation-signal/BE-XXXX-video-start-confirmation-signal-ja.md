[English](BE-XXXX-video-start-confirmation-signal.md) · **日本語**

# BE-XXXX — 録画の開始は、伸びないファイルではなくレコーダー自身の信号で確認する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-video-start-confirmation-signal-ja.md) |
| 提案者 | [@akiramatsuda](https://github.com/akiramatsuda) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| 実装 PR | [#1901](https://github.com/bajutsu-e2e/bajutsu/pull/1901) |
| トピック | Verification & coverage |
| 関連 | [BE-0346](../BE-0346-video-timing-sync/BE-0346-video-timing-sync-ja.md) · [BE-0348](../BE-0348-absolute-timestamp-recording/BE-0348-absolute-timestamp-recording-ja.md) · [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics-ja.md) · [BE-0367](../BE-0367-android-ci-emulator-diagnostics/BE-0367-android-ci-emulator-diagnostics-ja.md) |
<!-- /BE-METADATA -->

## はじめに

[BE-0346](../BE-0346-video-timing-sync/BE-0346-video-timing-sync-ja.md) は、録画に**確認済みの開始
時刻**を与えました。レポートのシーク位置が、プロセスを起動した瞬間ではなく、レコーダーが最初の
フレームを収めた瞬間を指すようにするためです。iOS では、その開始を出力ファイルの成長をポーリング
して確認していました。この信号は存在しません。`simctl io recordVideo`は録画のあいだ mp4 を 0 バイト
のままに保ち、確定処理のときに一括で書き出します。ポーリングは成功しようがなく、シナリオごとに上限を
まるごと消費していました。本項目は、ファイルの成長を見るポーリングを、simctl が実際に提供している信号、
すなわち自身の標準エラー出力へ書く`Recording started`の行に置き換えます。あわせて、測定した原点を
受け入れる範囲の上側を、ポーリングと共有していたレーン単位の調整値から切り離します。

## 動機

論拠は3つあり、いずれも議論ではなく実測によるものです。

**確認は一度も成功しておらず、しかも CI に限った不具合ではありません。** 完全にアイドルな機械
（Xcode 26.6、iPhone 17 Pro、iOS 26.5）で計測しました。mp4 は 10 秒、30 秒、90 秒のいずれの録画でも
0 バイトのままでした。10 秒の場合は、ポーリング自身の刻みである 0.05 秒で 186 回サンプリングしています。ファイルの
inode は確定処理の前後で変わらないので、一時ファイルからの改名でもありません。録画中の`lsof`を見ても、
simctl は mp4 への書き込み用の記述子を持っていません。`simctl io --help`は両方をそのまま述べて
います。最初のフレームを処理した時点で標準エラー出力へ`Recording started`を書くこと。そして
in-flight のフレームを処理し、ファイルを確定したあとに終了することです。コンパイル時のデフォルト値は5秒なので、
動画を取得するローカルの実行も同じ代償を払っていました。

**代償は上限まるごとであり、それをシナリオごとに、赤い実行と同じだけ緑の実行でも払っていました。**
iOS のレーンは`BAJUTSU_VIDEO_START_TIMEOUT`を20秒へ引き上げていました。「上限を超過しても正常な経路では
損をしない。ポーリングは最初のバイトが着いた瞬間に返るのだから」という理由づけによるものです。400 件の
`ios-e2e`の実行（2026-08-25 から 09-04）を見ると、警告はどのジョブでもシナリオごとに1回発火していました。
`actuation`は14シナリオに対し15件、`golden`は4件に対し4件、`network`は3件に対し3件です。ジョブごとの
中央値で積み上げると、フル実行1回あたり**およそ14.5分**が空費されていた計算になり、実行1回が消費する
macOS のジョブ時間およそ157分に対する割合になります。macOS ランナーの同時実行の上限は10で、実測した
ピーク10は2つの異なる実行にまたがっていました。したがって繁忙日のレーンはスループットの上限近くにあり、
待ち行列は p90 で206分に達していました。
[BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics-ja.md)は、
警告が全シナリオで発火する事実をすでに記録し、修正を後続の提案に先送りしていました。本項目がその後続です。

**ポーリングは何も買っていませんでした。それ無しでも基準時刻はすでに正しかったからです。** 本番の
`start_video`の経路をそのまま通すと、計算が裏付けられます。20.03秒ブロックしたうえで諦めた一方、
`measured_start`は`spawned_at + 0.147s`に解決しました。標準エラー出力の行が届く0.155秒との差は8ミリ秒
です。CI でも同じです。`ios-golden-run`の成果物では、最初のステップの`started_at`と`video_anchor_s`の
差がシナリオごとに +18.26 秒、+29.91 秒、+14.71 秒でした。いずれも 0 ではないので、CI でも
`measured_start`が解決していたことになります。`scenario.mp4`もすべて存在し、中身も空ではありません。
ランナー自身の描画プローブは`recordVideo: exit 0, 91512 bytes`と報告しており、パイプラインは終始
健全でした。

4つ目の問題は、原因ではなく隣接するものです。`_measured_start`は、測定した原点を受け入れる範囲の上側に
`_video_start_timeout()`を使っていました。そのためレーンの20秒という値が、この範囲を静かに20倍へ広げ、
どの録画も取り得ない原点まで受け入れていました。

## 詳細設計

作業は3つの単位に分かれます。単位2が単位1に依存するのは順序の面だけです。範囲の修正を先に着地させると、
収集済みの CI の測定値が出荷済みの挙動を記述したままになります。

**単位1 — 原点の範囲の上側を、レーンの我慢強さから切り離す。**
[`intervals.py`](../../bajutsu/common/evidence/intervals.py)で`_ORIGIN_STARTUP_CEILING = 5.0`を
`_ORIGIN_SLACK`の隣に置きます。`_measured_start`は`_video_start_timeout()`ではなく、こちらで原点を
抑えます。
2つは別の問いに答えます。**レコーダー**が最初のフレームを開くまでにかかりうる時間はレコーダーの性質で
あり、`BAJUTSU_VIDEO_START_TIMEOUT`が言うのはレーンがどれだけ待つつもりでいるかだけです。Android と
web のバックエンドは、この変数を上書きしないので影響を受けません。

**単位2 — 標準エラー出力で確認する。** `Proc`に`await_stderr(needle, timeout)`を加えます。行が現れた
瞬間を返し、期限まで現れなければ`None`を返します。`_SubprocessProc`は子プロセスの標準エラー出力を
パイプではなく`tempfile.TemporaryFile`へ送ります。レコーダーが動いている数分のあいだ、その標準エラー
出力を読み出す者はおらず、パイプのバッファが埋まると子プロセスが録画の途中で止まってしまうためです。
待ち処理は`os.pread`で読むので子プロセス自身の書き込み位置には触れず、読み出しをまたいで一致する行を
取りこぼさないように、探索語の長さだけ末尾を持ち越します。期限を確認する前に必ず1回は読みます。
`start_video`はファイルをポーリングする代わりに`Recording started`を待ち、`_await_video_file_growing`と
`_file_size`は削除します。3値をとる`Interval.start_confirmed`と、シナリオ開始前の`on_video_start_stall`の
報告は、現在の意味論とタイミングを保ちます。報告を読む BE-0354 の置換デバイスの段も同じです。
変わるのは、信号が本物になることだけです。

**単位3 — レーンの上書きを撤去し、その根拠だった記述を正す。**
[`ios-e2e.yml`](../../.github/workflows/ios-e2e.yml)から`BAJUTSU_VIDEO_START_TIMEOUT: "20"`を外します。
0.15秒で届く信号に対し、コンパイル時のデフォルト値である5秒は3桁の余裕があります。変数と
`_video_start_timeout()`は残します。Android の2つの確認処理がいまも読むためです。
[`docs/evidence.md`](../../docs/evidence.md)は、iOS の代理の信号を「出力ファイルの最初の書き込みバイト」
と述べ、しかも2つのバックエンドのうち**強いほう**として位置づけていました。事実は逆です。加えて、上限を
引き上げても「正常な経路では何のコストもかからない」と主張していました。
[`docs/ci.md`](../../docs/ci.md)は、捕捉の上限をトリガーごとに分ける根拠を、映像の警告が全シナリオで
発火することに置いていました。いずれも両言語で修正します。

作業の分解（*進捗*に対応します）:

1. `_ORIGIN_STARTUP_CEILING`と`_measured_start`の範囲。
2. `Proc.await_stderr`、標準エラー出力による確認、ファイル成長のポーリングの削除。
3. ワークフローの上書きと、両言語のドキュメント。

## 検討した代替案

**確認をやめ、`spawned_at`を基準時刻の代理にする。** 測定によれば`measured_start`はすでに解決するので、
基準時刻自体は保たれます。しかし`true_start`が恒久的に`None`、`start_confirmed`が恒久的に`False`と
なり、`Lease.video_start_stalled`が全シナリオでストールを報告し続けます。常に真である信号の上に、
段階的な復旧の判断が乗ることになります。CI でこれが発火しなかったのは、UDID を固定しているために
`can_replace`が偽だったからにすぎません。

**確認をプロセスの生存確認1回に格下げする。** 安価で誠実であり、Android の`true_start`が実質的に
行っていることでもあります。採らなかったのは、simctl が同じコストでより強い信号を明文化しているから
です。生存はプロセスの存在を示すだけですが、`Recording started`はフレームが処理されたことを示します。

**ポーリングを残して上限を短くする、あるいはバックグラウンドのスレッドへ移す。** 短くしても、20秒が
「依然として決して成功しない確認」に置き換わるだけです。バックグラウンド化は答えを出せないプローブを
温存します。さらに`None`が「未試行」と「未解決」の両方を意味するようになるので、
[BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement-ja.md)
が読むシナリオ開始前のストール報告を武装解除してしまいます。

**何かを生成できたかどうかの判定を`stop()`で行う。** `Interval.stop()`はすでに確定後のファイルを読むので、
判定は無料で、しかも完全に正確です。**置き換えとしては**採りませんでした。その判定は
`finish_scenario_intervals`のあと、つまり判定を消費するはずのクラッシュより後に届くからです。

## 進捗

> 作業の進行に合わせて最新に保ちます。チェックリストは*詳細設計*の MECE な作業分解に対応し
> （作業単位ごとに1つ）、ログは何がいつ変わったかを古い順に記録し、PR へリンクします。

- [x] 単位1 — `_ORIGIN_STARTUP_CEILING`と`_measured_start`の範囲。
- [x] 単位2 — `Proc.await_stderr`、標準エラー出力による確認、ファイル成長のポーリングの削除。
- [x] 単位3 — ワークフローの上書きと、両言語のドキュメント。

## 参考

- [BE-0346](../BE-0346-video-timing-sync/BE-0346-video-timing-sync-ja.md) — 確認済みの開始時刻と、
  本項目が置き換える iOS のファイル成長ポーリングを導入した項目です。
- [BE-0348](../BE-0348-absolute-timestamp-recording/BE-0348-absolute-timestamp-recording-ja.md) —
  `BAJUTSU_VIDEO_START_TIMEOUT`を追加し、iOS のレーンで引き上げた項目です。
- [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics-ja.md) —
  警告が全シナリオで発火する事実を記録し、修正を本項目へ先送りした項目です。
- [BE-0367](../BE-0367-android-ci-emulator-diagnostics/BE-0367-android-ci-emulator-diagnostics-ja.md)
  — Android の成長チェックです。`screenrecord`は実際に書き進めるので、こちらは残ります。
- [`docs/evidence.md`](../../docs/ja/evidence.md) · [`docs/ci.md`](../../docs/ja/ci.md)

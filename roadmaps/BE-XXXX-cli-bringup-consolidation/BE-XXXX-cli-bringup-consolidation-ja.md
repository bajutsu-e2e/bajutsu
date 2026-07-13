[English](BE-XXXX-cli-bringup-consolidation.md) · **日本語**

# BE-XXXX — 重複した CLI コマンドの起動処理を統合し、中立な DeviceError を追加する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-cli-bringup-consolidation-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

run、crawl、record、audit の各コマンドは、実行の前に同じ 4 種類のデバイス／バックエンド起動処理を
行っています。アクチュエータの選択、対象アプリの起動サーバーの立ち上げ、UDID の解決、そして
該当する場合はアラートガードの構築です。各コマンドはこれらの処理を、同じ
`try: … except RuntimeError → typer.Exit(2)`（または `except DeviceError`）という定型コードとともに
個別に実装しています。本項目では、この 4 種類の複製をそれぞれ `bajutsu/cli/_shared.py` の共有
ヘルパーへ統合し、あわせて `adb.DeviceError` にプラットフォーム中立な基底クラスを与えることで、
汎用的なハンドラが例外の名前を得るためだけに iOS 側の `simctl` モジュールを import する必要を
なくします。

## 動機

同じ起動処理のロジックが、コマンドごとに手書きで 4 重に複製されています。

- **アクチュエータの選択**（`run.py:303-312`、`crawl.py:501-507`、`record.py:196-202`、
  `audit.py:146-151`）：`try: ensure_web_runtime(...); actuator = select_actuator(...) except
  RuntimeError → typer.Exit(2)` という同一の形です。どの複製も web ランタイムを準備してから
  アクチュエータを解決し、同じ `RuntimeError` で終了コード 2 を返します。呼び出し元の周辺だけが
  異なっています。
- **起動サーバーの立ち上げ**（`run.py:504-510`、`crawl.py:286-292`、`record.py:216-220`、
  `audit.py:163-170`）：`try: start_launch_server(eff, upload_exec=...) except RuntimeError →
  typer.Exit(2)` です。呼び出し、例外、終了コードのすべてが同じものが 4 回現れています。
- **UDID の解決**（`run.py:515`、`record.py:211-212`、`audit.py:152-156`、`crawl.py:322`）：
  `_simctl.resolve_udid(...)` を `except _simctl.DeviceError → typer.Exit(2)` で包んでいます。
- **アラートガードの構築**（`run.py:375` と `392`、`crawl.py:231-232`、`record.py:207-208`）：
  `ClaudeAlertLocator(ai=eff.ai, redactor=redactor)` を作り、続けて
  `SystemAlertGuard(locator, instruction).dismiss` を得る処理です。

これらのいずれかの挙動（終了コード、終了前のログ出力、捕捉する例外の種類）を変えるには、
すべての複製を手作業で追随させる必要があります。追随し忘れた複製は、テストの失敗としてではなく、
コマンド間の静かな不整合として残ります。各複製はそれぞれ自コマンドのテストだけを個別に
通過してしまうためです。

これとは別に、`adb.DeviceError` は `simctl.DeviceError` を継承しています（`bajutsu/adb.py:32`）。
そのため、iOS 固有ではなく単に何らかのデバイスエラーを捕捉したいだけの呼び出し側、約 9 箇所が、
例外の名前を得るためだけに `bajutsu.simctl` を import しています。該当箇所は `crawl.py:1016`、
`cli/commands/crawl.py:189,322,365`、`cli/commands/run.py:515`、`cli/commands/audit.py:154,179`、
`cli/commands/record.py:242`、`doctor.py:136`、`serve/operations/doctor.py:130` です。これは、
本ツールがプラットフォームに依存しない（プラットフォームは単一インターフェースの背後にある
バックエンドの一つにすぎない）という prime directive が期待する依存の向きを逆転させています。
汎用的な `except DeviceError` を書きたいだけなのに、iOS バックエンドのモジュールに手を伸ばさざるを
得ない状態です。

## 詳細設計

上で挙げた 2 つの独立した問題、すなわち起動処理の重複と例外階層の逆転にもとづいて MECE に
分解します。前者はさらに複製されている 4 つの部分に分かれます。

1. **`_select_actuator_or_exit(backend, eff, engines)`**（`bajutsu/cli/_shared.py`）：
   `ensure_web_runtime` のループ、`select_actuator` の呼び出し、`except RuntimeError →
   typer.Exit(2)` の境界を 1 つのヘルパーにまとめ、`(actuator, backends)` を返します。
   `run.py`、`crawl.py`、`record.py`、`audit.py` の 4 箇所の複製をこれに置き換えます。
2. **`_start_launch_server_or_exit(eff, *, upload_exec)`**（`bajutsu/cli/_shared.py`）：
   `start_launch_server` の呼び出しと `except RuntimeError → typer.Exit(2)` の境界を 1 つの
   ヘルパーにまとめ、`(stop_server, exec_decision)` を返します。4 箇所の複製をこれに置き換えます。
   呼び出し側ごとの違い（crawl の `atexit.register(stop_server)` と run の
   `finally: stop_server()` など）は呼び出し側に残します。ヘルパーが担うのは、4 箇所に共通する
   「立ち上げてだめなら終了する」部分だけです。
3. **`_build_alert_guard(eff, redactor, instruction)`**（`bajutsu/cli/_shared.py`）：
   `ClaudeAlertLocator` の生成と `SystemAlertGuard(...).dismiss` の取得を 1 つのヘルパーに
   まとめ、束縛済みの `dismiss` 呼び出し可能オブジェクトを返します。`run.py`、`crawl.py`、
   `record.py` の 3 箇所の複製をこれに置き換えます。
4. **UDID の解決は、共有ヘルパーを新設せず、呼び出し側ごとの薄いラッパーのままとします。**
   各呼び出し側の `_simctl.resolve_udid(...)` は UDID 以外の引数がすでに異なっており、
   共通しているのは `except DeviceError → typer.Exit(2)` の境界だけです。この部分は、手順 5 が
   実現すれば `except device_errors.DeviceError` という 1 行に収まり、別建てのヘルパーは
   不要になります。
5. **`bajutsu/device_errors.py`** を新設します。既存の `simctl.DeviceError` と同じ形
   （メッセージを保持する）の、プラットフォーム中立な `DeviceError` を定義するモジュールです。
   `simctl.DeviceError` と `adb.DeviceError` はどちらもこれを継承する形に変え（それぞれ
   プラットフォーム固有の詳細を持つ独自クラスは維持し、互いを継承する関係はなくします）、
   動機で挙げた汎用的な `except _simctl.DeviceError` ／ `except simctl.DeviceError` の
   約 9 箇所は `except device_errors.DeviceError` に切り替えて `bajutsu.simctl` の import を
   落とします。iOS 固有の `simctl.DeviceError` を本当に必要とする呼び出し側があれば、そこだけは
   引き続き直接 import します。

`_shared.py` に加える新しいヘルパーは、いずれも挙動を変えません。捕捉する例外、表示する
メッセージ、`typer.Exit(2)`、戻り値の形はすべて置き換え前のコードと同じであり、本項目は
定型コードを移すだけで終了コードの契約は変えません。`_shared.py` 自身のモジュール docstring も
すでに「本当にコマンドをまたぐ部分だけをここに置く」と述べており、この 4 つはどのコマンド
ファイルにもまったく同じ形で存在しているという点で、その基準に当てはまります。

## 検討した代替案

- **各コマンドのエントリポイントをデコレータで包む案。** 検討したうえで採用しません。4 つの
  処理はコマンドごとに異なる箇所で立ち上がります。アクチュエータの選択は設定解決が終わる前に、
  起動サーバーの立ち上げはプランの構築後に、アラートガードは `--dismiss-alerts` が指定された
  場合に限って構築されます。単一のデコレータにしても、結局は素朴なヘルパーと同じだけの引数が
  必要になり、しかも Typer のコマンドシグネチャや、どの時点で終了しうるかが呼び出し箇所からは
  読み取りにくくなります。
- **`adb.DeviceError` が `simctl.DeviceError` を継承する現状を維持する案。** 採用しません。
  これがまさに、汎用ハンドラが `bajutsu.simctl` を import する原因そのものであり、3 つ目の
  プラットフォームバックエンド（Android はすでに実装済みで、さらに増える可能性があります）は、
  `simctl.DeviceError` をさらに継承して逆転を重ねるか、独自の場当たり的な基底クラスを用意するかを
  迫られることになります。
- **4 つのヘルパーを `bajutsu/orchestrator.py` やドライバ側のモジュールに置く案。** 採用しません。
  4 つはいずれも CLI コマンドの起動処理に関する関心事（設定、Typer の終了コード、`typer.echo`）
  であり、決定的なドライバ／オーケストレータのコアには何の役割も持ちません。`_shared.py` は、
  既存のコマンド間で重複していたヘルパー（設定の読み込みやシークレットの redaction など）が
  すでに置かれている場所であり、理由も同じです。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `_select_actuator_or_exit` を `cli/_shared.py` に追加し、run、crawl、record、audit を移行
- [ ] `_start_launch_server_or_exit` を `cli/_shared.py` に追加し、run、crawl、record、audit を移行
- [ ] `_build_alert_guard` を `cli/_shared.py` に追加し、run、crawl、record を移行
- [ ] 中立な `DeviceError` を持つ `bajutsu/device_errors.py` を追加し、`simctl.DeviceError` と
      `adb.DeviceError` をこれを継承する形に変更し、汎用的な呼び出し側を `bajutsu.simctl` から
      切り離す

## 参考

- [`bajutsu/cli/_shared.py`](../../bajutsu/cli/_shared.py) — 新しいヘルパーの置き場所（「本当に
  コマンドをまたぐ部分だけ」という既存のスコープに沿っています）
- [`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py) ·
  [`bajutsu/cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py) ·
  [`bajutsu/cli/commands/record.py`](../../bajutsu/cli/commands/record.py) ·
  [`bajutsu/cli/commands/audit.py`](../../bajutsu/cli/commands/audit.py) — 重複した起動処理を
  抱える 4 つのコマンド
- [`bajutsu/simctl.py`](../../bajutsu/simctl.py) · [`bajutsu/adb.py`](../../bajutsu/adb.py) — 現在の
  `DeviceError` の継承関係（`adb.DeviceError` が `simctl.DeviceError` を継承しています）
- [BE-0143](../BE-0143-run-command-decomposition/BE-0143-run-command-decomposition-ja.md) — run の
  巨大関数をこれらの起動処理の手順へと分解した項目。本項目はそれをコマンド横断で統合します
- [BE-0205](../BE-0205-crawl-command-decomposition/BE-0205-crawl-command-decomposition-ja.md) — 同じ
  分解を crawl に適用し、各ヘルパーの 2 つ目の複製を生み出した項目。本項目はそれを統合します

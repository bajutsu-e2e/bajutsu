[English](BE-0260-cli-bringup-consolidation.md) · **日本語**

# BE-0260 — 重複した CLI コマンドの起動処理を統合し、中立な DeviceError を追加する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0260](BE-0260-cli-bringup-consolidation-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0260") |
| 実装 PR | [#1033](https://github.com/bajutsu-e2e/bajutsu/pull/1033) |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

`run`、`crawl`、`record`、`audit` の各コマンドは、自分の処理へ入る前に、同じ 4 種類のデバイスとバックエンドの起動処理を行っています。

- [アクチュエータ](../../docs/ja/glossary.md#driver-backend-actuator-platform)の選択
- 対象アプリの起動サーバーの立ち上げ
- UDID の解決
- アラートガードの構築（該当する場合）

各コマンドは、同じ `try: … except RuntimeError → typer.Exit(2)`（または `except DeviceError`）という定型コードで、これらを個別に包んでいます。

本項目は、4 種類の複製をそれぞれ `bajutsu/cli/_shared.py` の共有ヘルパーへ統合します。あわせて `adb.DeviceError` にプラットフォーム中立な基底クラスを与えます。これにより、汎用的なハンドラが、捕捉する例外の名前を得るためだけに iOS 側の `simctl` モジュールを import する必要はなくなります。

## 動機

同じ起動処理のロジックが、コマンドごとに手書きで 4 重に複製されています。

- **アクチュエータの選択**は、`run.py:303-312`、`crawl.py:501-507`、`record.py:196-202`、`audit.py:146-151` にあります。どの複製も `try: ensure_web_runtime(...); actuator = select_actuator(...) except RuntimeError → typer.Exit(2)` という同一の形です。web ランタイムを準備してからアクチュエータを解決し、同じ `RuntimeError` で終了コード 2 を返します。異なるのは呼び出し元の周辺だけです。
- **起動サーバーの立ち上げ**は、`run.py:504-510`、`crawl.py:286-292`、`record.py:216-220`、`audit.py:163-170` にあります。形は `try: start_launch_server(eff, upload_exec=...) except RuntimeError → typer.Exit(2)` です。呼び出し、例外、終了コードは、4 つとも同じです。
- **UDID の解決**は、`run.py:515`、`record.py:211-212`、`audit.py:152-156`、`crawl.py:322` にあります。どの複製も `_simctl.resolve_udid(...)` を `except _simctl.DeviceError → typer.Exit(2)` で包んでいます。
- **アラートガードの構築**は、`run.py:375` と `392`、`crawl.py:231-232`、`record.py:207-208` にあります。どの複製も `ClaudeAlertLocator(ai=eff.ai, redactor=redactor)` を作り、続けて `SystemAlertGuard(locator, instruction).dismiss` を得ます。ただし、この 3 つはバイト単位で同一ではありません。`run.py` の `_alert_guard_factory`（`run.py:363-376`）は、先に `credential_gap(eff.ai)` を確認します。資格情報が欠けている場合は、locator の構築を省いて `None` にフォールバックし、利用者向けの警告を出します。`crawl.py` と `record.py` は、無条件に構築します。そのため、共有ヘルパーは 3 つの複製を丸ごと、振る舞いを保って持ち上げるものにはなりません。解決方法は、詳細設計の項目 3 に書きます。

これらのいずれかの挙動を変えるには、すべての複製を手作業で追随させる必要があります。たとえば、終了コードの変更、終了前のログ出力の追加、捕捉する例外の追加です。追随し忘れた複製は、テストの失敗としてではなく、コマンド間の静かな不整合として残ります。各複製は、それぞれ自分のコマンドのテストだけを個別に通過するためです。

もう 1 つの問題は、例外の継承関係です。`adb.DeviceError` は `simctl.DeviceError` を継承しています（`bajutsu/adb.py:32`）。iOS 固有ではなく、何らかのデバイスエラーを捕捉したいだけの呼び出し側が 10 箇所あります。それでも、例外の名前を得るために `bajutsu.simctl` を import しています。

- `crawl.py:1016`
- `cli/commands/crawl.py:189,322,365`
- `cli/commands/run.py:516`
- `cli/commands/audit.py:158,193`
- `cli/commands/record.py:242`
- `cli/commands/doctor.py:181`
- `serve/operations/doctor.py:130`

この状態は、プライムディレクティブが期待する依存の向きを逆転させています。`bajutsu` は、プラットフォームを単一インターフェースの背後にあるバックエンドの 1 つとして扱い、バックエンドに依存しないことを目指しています。しかし現状では、汎用的な `except DeviceError` を書こうとすると、iOS バックエンドのモジュールに手を伸ばさざるを得ません。

## 詳細設計

設計は、上で挙げた 2 つの独立した問題、つまり起動処理の重複と例外階層の逆転について、MECE に分かれます。前者は、さらに複製されている 4 つの部分に分かれます。

1. **`_select_actuator_or_exit(backend, eff, engines)`**（`bajutsu/cli/_shared.py`）は、`ensure_web_runtime` のループ、`select_actuator` の呼び出し、`except RuntimeError → typer.Exit(2)` の境界を 1 つのヘルパーにまとめます。ヘルパーは `(actuator, backends)` を返し、`run.py`、`crawl.py`、`record.py`、`audit.py` にある 4 箇所の複製を置き換えます。
2. **`_start_launch_server_or_exit(eff, *, upload_exec)`**（`bajutsu/cli/_shared.py`）は、`start_launch_server` の呼び出しと `except RuntimeError → typer.Exit(2)` の境界を 1 つのヘルパーにまとめます。ヘルパーは `(stop_server, exec_decision)` を返し、4 箇所の複製を置き換えます。
   - 呼び出し側ごとの違いは、呼び出し側に残します。たとえば、crawl は `atexit.register(stop_server)` を使い、run は `finally: stop_server()` を使います。ヘルパーが担うのは、4 箇所に共通する「立ち上げて、だめなら終了する」部分だけです。
   - 1 つだけ、明示的な判断を要する違いがあります。`audit.py` の `except RuntimeError` ブロック（`audit.py:171-173`）は、`typer.Exit(2)` の前に `shutdown()` も呼びます。`shutdown()` は、すでに確保済みのデバイスプールのリースを片付けます。他の 3 つのコマンドには、この呼び出しがありません。
   - そこでヘルパーは、任意の `on_error: Callable[[], None] | None`（終了前に実行するクリーンアップフック）を受け取ります。`audit` は `shutdown` を渡して、この片付けを保ちます。他の 3 つは何も渡さず、今日とまったく同じ挙動になります。
3. **`_build_alert_guard(eff, redactor, instruction)`**（`bajutsu/cli/_shared.py`）は、`ClaudeAlertLocator` の生成と `SystemAlertGuard(...).dismiss` の取得を 1 つのヘルパーにまとめます。ヘルパーは、束縛済みの `dismiss` 呼び出し可能オブジェクトを返し、`run.py`、`crawl.py`、`record.py` にある 3 箇所の複製を置き換えます。
   - ヘルパーは、`run.py` の `credential_gap(eff.ai)` 分岐（`run.py:363-376`）を取り込みます。資格情報が欠けている場合は、同じ警告を出し、何もしないガードを返します。
   - 3 箇所のうち 2 箇所については、この変更は、純粋に振る舞いを保つものではありません。`crawl.py` と `record.py` は、今日は無条件に locator を構築しています。取り込むと、両コマンドは、資格情報が欠けているときに先へ進まず、`run` と同じく穏当に何もしない挙動を得ます。
   - この揃えは意図的です。AI オーサリング系の 3 コマンドは、同じように振る舞いを弱めるべきだからです。本項目は、これが偶発的な変更ではなく、決定された振る舞いの変更であることを明示するために、ここに記します。
4. **UDID の解決は、共有ヘルパーを新設せず、呼び出し側ごとの薄いラッパーのままとします。**
   - 各呼び出し側の `_simctl.resolve_udid(...)` は、UDID 以外の引数がすでに異なっています。
   - 共通しているのは、`except DeviceError → typer.Exit(2)` の境界だけです。手順 5 が実現すれば、この境界は `except device_errors.DeviceError` という 1 行に収まるため、別建てのヘルパーは不要になります。
5. **`bajutsu/device_errors.py`** は、プラットフォーム中立な `DeviceError` を定義する新しいモジュールです。既存の `simctl.DeviceError` と同じ形で、メッセージを保持します。
   - `simctl.DeviceError` と `adb.DeviceError` は、どちらもこれを継承する形に変えます。それぞれ、プラットフォーム固有の詳細を持つ独自クラスは維持し、互いを継承する関係はなくします。
   - 動機で挙げた、汎用的な `except _simctl.DeviceError` と `except simctl.DeviceError` の 10 箇所は、`except device_errors.DeviceError` に切り替え、`bajutsu.simctl` の import を落とします。
   - iOS 固有の `simctl.DeviceError` を本当に必要とする呼び出し側があれば、そこだけは引き続き直接 import します。

`_shared.py` に加える新しいヘルパーは、いずれも挙動を変えません。捕捉する例外、表示するメッセージ、`typer.Exit(2)`、戻り値の形は、置き換え前のコードと同じです。本項目は、定型コードを移すだけで、終了コードの契約は変えません。

`_shared.py` のモジュール docstring は、すでにこのファイルの範囲を「本当にコマンドをまたぐ部分」に限っています。4 つの部分は、どのコマンドファイルにもまったく同じ形で存在しているため、この基準に当てはまります。

## 検討した代替案

- **各コマンドのエントリポイントをデコレータで包む案。** 検討したうえで採用しません。4 つの処理は、コマンドごとに異なる箇所で立ち上がります。アクチュエータの選択は設定解決が終わる前、起動サーバーの立ち上げはプランの構築後、アラートガードは `--dismiss-alerts` が指定された場合に限って行われます。単一のデコレータにしても、素朴なヘルパーと同じだけの引数が必要です。しかも、Typer のコマンドシグネチャと、どの時点で終了しうるかが、明示的な関数呼び出しより読み取りにくくなります。
- **`adb.DeviceError` が `simctl.DeviceError` を継承する現状を維持する案。** 採用しません。この継承関係が、汎用ハンドラごとに `bajutsu.simctl` を import させる直接の原因です。3 つ目のプラットフォームバックエンド（Android はすでに実装済みで、さらに増える可能性があります）は、次のどちらかを迫られます。`simctl.DeviceError` をさらに継承して逆転を重ねるか、独自の場当たり的な基底クラスを用意するかです。
- **4 つのヘルパーを `cli/_shared.py` の代わりに `bajutsu/orchestrator.py` やドライバ側のモジュールに置く案。** 採用しません。4 つはいずれも CLI コマンドの起動処理に関する関心事（設定、Typer の終了コード、`typer.echo`）で、決定的なドライバとオーケストレータのコアには役割がありません。`_shared.py` には、設定の読み込みやシークレットの redaction など、コマンドごとに重複していた他のヘルパーが、同じ理由ですでに置かれています。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `_select_actuator_or_exit` を `cli/_shared.py` に追加し、run、crawl、record、audit を移行
- [x] `_start_launch_server_or_exit` を `cli/_shared.py` に追加し、run、crawl、record、audit を移行
- [x] `_build_alert_guard` を `cli/_shared.py` に追加し、run、crawl、record を移行（`run` は共有 locator
      を維持するため `_build_alert_locator` を利用）
- [x] 中立な `DeviceError` を持つ `bajutsu/device_errors.py` を追加し、`simctl.DeviceError` と
      `adb.DeviceError` をこれを継承する形に変更し、汎用的な呼び出し側を `bajutsu.simctl` から
      切り離す

### ログ

- 2026-07-14: 4 ユニットすべてを実装しました（[PR #1033](https://github.com/bajutsu-e2e/bajutsu/pull/1033)）。振る舞いを変えないリファクタですが、
  意図的な挙動統一が 1 つだけあります。`_build_alert_guard` により、`crawl`/`record` も
  credential 欠如時には `run` と同じく穏当な no-op へフォールバックするようになりました（両コマンドは
  `_require_ai_credential` で先に fail-closed するため、実フローでは影響しません）。また `adb.DeviceError` は
  `simctl.DeviceError` の兄弟となり、汎用ハンドラ 10 箇所が `bajutsu.simctl` の import を落とせるようになりました。

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

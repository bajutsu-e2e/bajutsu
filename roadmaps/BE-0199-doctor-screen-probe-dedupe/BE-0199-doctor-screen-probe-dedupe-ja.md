[English](BE-0199-doctor-screen-probe-dedupe.md) · **日本語**

# BE-0199 — doctor の画面プローブを CLI と serve で共有する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0199](BE-0199-doctor-screen-probe-dedupe-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0199") |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

doctor の「現在の画面を取得する」プローブは 2 回実装されています。CLI 用
（`bajutsu/cli/commands/doctor.py` の `_current_screen`）と、serve の Web UI パネル用
（`bajutsu/serve/operations/doctor.py` の `_current_screen`）です。両者はほぼ逐語的な複製で、
独立に保守されており、周辺の検査項目にはすでにずれが生じています。本項目では 1 つの共有
プローブに統合し、そのずれも解消します。

## 動機

serve 側の docstring 自身が「`cli.commands.doctor._current_screen` を mirror する」と明言して
います。Playwright の分岐（ドライバの構築、`navigate()`、`query()`、close 時の
`contextlib.suppress(*_playwright_error_types())`）は両者で逐語一致で、xcuitest から idb への
フォールバックと `resolve_udid` の後始末も共通です。異なるのは、まさに注入可能な部分だけです。
serve 側は `state.simctl` を渡し、`fake` バックエンドを扱い、カンマ区切りリストの先頭の udid を
使います。`baseUrl` 欠落時には CLI が `typer.Exit` を、serve が `ValueError` を送出します。

複製そのものに加えて、複製が招くずれもすでに現れています。CLI 側の検査の組み立ては xcuitest と
idb の実行可否を統合し、idb のバージョンピン検査を加えています
（`bajutsu/cli/commands/doctor.py:104-121`）。
serve のパネル（`bajutsu/serve/operations/doctor.py:86-100`）にはどちらもないため、同じターゲットに
対して Web UI の doctor は CLI より少ない検査結果を静かに返します。共有プローブ（と、両面で
一致してよい範囲の共有された検査の組み立て）は、次の分岐を防ぐ手当てそのものです。

## 詳細設計

1. `bajutsu/doctor.py` に、環境を注入できる共有プローブを 1 つ追加します。simctl 用の `RunFn`、
   `fake` バックエンドのためのフラグまたはフック、udid の正規化を受け取り、トランスポート固有の
   例外の代わりに型付きのエラー（例: `DoctorProbeError`）を送出します。
2. CLI 側のアダプタは型付きエラーを `typer.Exit(2)` に写し、現在の使い勝手を維持します。
3. serve 側のアダプタは `state.simctl` を渡し、カンマ区切り udid の扱いを保ち、型付きエラーを
   既存の `ValueError` の面に写します。
4. 検査項目のずれの解消を明示的な判断として行います。パネルを狭いままにする理由が見つからない
   限り、serve のパネルにも xcuitest と idb の実行可否の統合ビューと idb バージョンピン検査を
   加えます（どちらに決めても結果をここに記録します）。
5. 共有プローブをユニットテスト（fake ドライバと注入した `RunFn`）で覆い、2 つのアダプタは薄い
   表面のテストだけで済むようにします。

## 検討した代替案

- **複製のまま残す。** 検査の組み立てに生じたずれが行き先を示しています。2 つの doctor は
  「このターゲットは健全か」という同じ問いに違う徹底度で答え続け、今後の検査追加も片側だけに
  着地します。
- **小さなヘルパ（udid の解析、エラー型）だけ共有して、プローブは 2 つ残す。** 危険なのは逐語
  一致の Playwright 分岐とバックエンドのフォールバックのロジックであり、周辺だけを共有しても
  肝心の複製が残ります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] 環境注入（`simctl_run`）と型付きエラー（`DoctorProbeError`）を備えた共有プローブを `bajutsu/doctor.py` に追加
- [x] CLI 側アダプタ（typer.Exit への写像）を移行
- [x] serve 側アダプタ（state.simctl、カンマ区切り udid）を移行
- [x] 検査項目のずれを解消。共有の `preflight.doctor_environment_checks` により、serve パネルにも CLI と同じ xcuitest→idb の統合と idb バージョンピン検査が入る（判断: serve を CLI 側の広い検査に揃える）
- [x] 共有プローブと共有された検査の組み立てのユニットテスト

**ログ**

- 共有の `doctor.probe_screen`（および `DoctorProbeError`、`_first_udid`）と `preflight.doctor_environment_checks` を切り出し、CLI と serve の doctor はそれらに委譲する薄いアダプタに整理しました。`ios_pin` の取り出しは `config.idb_version_pin` として引き上げています。serve パネルは CLI と同じ環境検査を報告するようになりました。この統合により、内包していた 2 つの潜在的なバグも解消しました。CLI の `fake` バックエンドが udid を解決しなくなり（`xcrun` を呼び出すおそれがありました）、serve は adb のシリアルを `simctl.resolve_udid` ではなく `adb.resolve_serial` で解決します。

## 参考

- [`bajutsu/cli/commands/doctor.py`](../../bajutsu/cli/commands/doctor.py) · [`bajutsu/serve/operations/doctor.py`](../../bajutsu/serve/operations/doctor.py) · [`bajutsu/doctor.py`](../../bajutsu/doctor.py)
- [BE-0148](../BE-0148-serve-doctor/BE-0148-serve-doctor-ja.md) — 本項目が統合するプローブ複製を持つ serve の doctor パネル

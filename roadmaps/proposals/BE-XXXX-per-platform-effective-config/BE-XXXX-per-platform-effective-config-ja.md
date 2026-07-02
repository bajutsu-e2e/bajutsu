[English](BE-XXXX-per-platform-effective-config.md) · **日本語**

# BE-XXXX — Effective をプラットフォームごとの設定に分割する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-per-platform-effective-config-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トピック | プラットフォーム拡張（Android / Web / Flutter） |
| 関連 | [BE-0009](../../implemented/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)、[BE-0042](../../implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md)、[BE-0057](../../implemented/BE-0057-rename-apps-to-targets/BE-0057-rename-apps-to-targets-ja.md)、[BE-0007](../../proposals/BE-0007-android-backend/BE-0007-android-backend-ja.md) |
<!-- /BE-METADATA -->

## はじめに

`bajutsu/config.py` は各 target の YAML を、1 つの frozen dataclass である `Effective`
（`config.py:420`）に解決します。runner とすべての backend は、この `Effective` をその target の
設定の単一の情報源として読みます。現状の `Effective` は、どのプラットフォームが実際にその target を
動かすかに関わらず、あらゆるプラットフォームが必要としうるフィールドをすべて抱えています。本提案は
`Effective` を小さな共通コアとプラットフォームごとのサブ設定に分割し、target が自分のプラットフォーム
の項目だけを持つようにします。

## 動機

`Effective` は iOS、続いて web が追加されるにつれてフィールドが増え、現在 35 個に達しており
（`config.py:420-487`）、すべてを 1 つの型に union しています。具体的には次のとおりです。

- `browser`（`config.py:471`）と `headless`（`config.py:468`）は Playwright（web）用の項目で、
  デフォルトは `chromium` です。ですが、これらは iOS target の `Effective` インスタンス上にも
  読み書き可能な状態で存在します。
- `xcuitest`（`config.py:478`）と `idb_version`（`config.py:481`）は iOS 専用ですが、web target の
  `Effective` にも同様に存在します。
- `package`（`config.py:464`）は未実装の Android backend（[BE-0007]）向けに予約されており、
  現状すべての iOS・web target で常に `""` です。

型システムは、iOS の実行で `eff.browser` を読むことも、web の実行で `eff.xcuitest` を読むことも
妨げません。歯止めは `environment.py:266`（`if not eff.base_url: raise ...`）や
`environment.py:437`（`xcfg = eff.xcuitest`）のような呼び出し側の実行時の規律だけであり、これらは
値を使う前にチェックしているだけで、型システムがそのフィールドをそもそも排除しているわけではあり
ません。これは**中程度の深刻度**の正しさ・保守性のリスクです。今日の実行を失敗させるものではあり
ませんが、「app-agnostic で、プラットフォームは backend の違いにすぎない」という境界（prime
directive 3）を型のレベルで侵食しており、プラットフォーム固有のフィールドが増えるたびに「妥当な
`Effective` とは何か」がさらに曖昧になります。Android（[BE-0007]）はこれを押し進める要因です。
`adb` 固有のフィールド（デバイス選択、`am`/`pm` 呼び出しの項目など）を今の union にそのまま積むと、
フィールド数は 35 を大きく超え、すべての target の設定に「常に無関係なフィールドの軸」が 4 つ目
できてしまい、新しいプラットフォームが追加されるたびに dataclass の可読性・レビューのしやすさ・
正しさの維持が難しくなります。

## 詳細設計

対応方針は、既存の `platform` フィールド（`config.py:462`。すでに `ios | android | web` を取り、
`config.py:122` の `_check_platform` で検証済み）を判別子とする**タグ付き union** です。作業は
フィールドの所有グループごとに MECE に分解できます。

1. **共通コア。** `Effective` には、全プラットフォームが本当に共有するフィールドだけを残します。
   `target`、`backend`、`device`、`locale`、`launch_env`、`launch_args`、`id_namespaces`、
   `reserved_namespaces`、`mock_server`、`setup`、`capture`、`redact`、`secrets`、`ai`、
   `mailbox`、`scenarios`、`baselines`、`schemas`、`goldens`、`launch_server`、`ready_when`、
   `doctor_ok_coverage`、`doctor_fail_coverage`、`notify`、そして判別子である `platform`
   自体です（`device`・`locale` は実際には iOS/Android 専用と判明する可能性もあるため、実装者は
   このリストを確定として扱わず、現在の呼び出し箇所での実際の使われ方を各フィールドについて
   再確認してから割り当てます）。
2. **iOS 用サブ設定。** `bundle_id`、`deeplink_scheme`、`app_path`、`build`、`xcuitest`、
   `idb_version` を、`platform == "ios"` のときだけ `Effective` に付与される `IosConfig`
   （仮称）に移します。
3. **web 用サブ設定。** `base_url`、`headless`、`browser` を、`platform == "web"` のときだけ
   付与される `WebConfig` に移します。
4. **Android 用の受け皿。** `package` を、`platform == "android"` のときだけ付与される
   `AndroidConfig` に移し、他の 2 つのサブ設定に手を入れることなく [BE-0007] が拡張できる
   ようにします。
5. **`resolve()` と `rebased()` の更新。** `Config` から `Effective` を組み立てる `resolve()`
   と、Git 由来の設定（BE-0063）向けに `xcuitest.testRunner` などのパスフィールドを
   再配置する `Effective.rebased()`（`config.py:489`）の両方を、フラットなフィールドではなく
   サブ設定越しに構築・再配置するよう更新する必要があります。
6. **呼び出し箇所の移行。** 移動したフィールドを読んでいる箇所（例えば
   `environment.py:266-273`、`environment.py:437`）はすべて、`eff.base_url` のような書き方から
   `eff.web.base_url` のような書き方に変わります。これは `eff.platform == "web"` のときにしか
   到達できないため、今日の実行時ガード（`if not eff.base_url`）が、mypy strict が静的に検査
   できるもの（サブ設定に対する `match eff.platform` や `isinstance` によるパターンなど）に
   置き換わります。常に存在はするが意味を持たないこともあるフィールドではなくなります。

これは設定レベルの変更に留まります。`targets.<name>` の YAML の形、runner、各 backend は変わり
ません（prime directive 3、app-agnostic）。プラットフォーム固有のフィールドに到達するための
属性名は変わる（`eff.browser` → `eff.web.browser` など）ため、上記で挙げた既存の呼び出し箇所は
すべて同じ変更の中で追随させる必要があります。mypy strict が効いているため、これらは実行後に
発覚する不具合ではなく、コンパイル時のチェックとして一つずつ見つかります。

## 検討した代替案

- **Android が来るまで何もしない。** 痛みを先送りするだけでなく、むしろ悪化させます。3 つ目の
  プラットフォームのフィールドが同じフラットな dataclass に積まれ、最終的な分割では 2
  プラットフォームではなく 3 プラットフォーム分の呼び出し箇所を移行することになります。iOS と
  web だけの今のうちにやるほうが、変更は小さく済みます。
- **本当の分割の代わりに、命名規則（`ios_`／`web_` プレフィックスなど）を持つ `Optional`
  フィールドにする。** すべてを 1 つのフラットな型に残せるため差分は小さくなりますが、これは
  見た目だけの対策です。`eff.web_browser` が iOS target で読まれることを何も防がず、mypy
  strict も命名規則に対してはタグ付き union のような絞り込みができません。
- **プラットフォームごとのサブ設定フィールドを、すべて無条件に `Effective` に持たせる**
  （`ios: IosConfig | None`、`web: WebConfig | None`、`android: AndroidConfig | None` を
  すべて併存させる）。今日より多少安全にはなります（使う前に `None` チェックが必要になる
  ため）が、それでも呼び出し側が誤ったプラットフォームの Optional なサブ設定を参照して、
  「この target は web ではない」という明確なエラーの代わりに*型としては正しい* `None`
  を得てしまう余地が残ります。サブ設定の存在を `platform` 判別子に結びつける設計であれば、
  プラットフォームをまたいだ誤読は `None` を確認し損なうバグではなく、型エラーになります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] 共通コア：`Effective` を本当に共有されるフィールドだけに絞る。
- [ ] iOS 用サブ設定（`IosConfig`）：`bundle_id`、`deeplink_scheme`、`app_path`、`build`、`xcuitest`、`idb_version`。
- [ ] web 用サブ設定（`WebConfig`）：`base_url`、`headless`、`browser`。
- [ ] Android 用の受け皿サブ設定（`AndroidConfig`）：`package`。
- [ ] `resolve()` と `Effective.rebased()` をサブ設定越しに構築・再配置するよう更新する。
- [ ] `environment.py` などのすべての呼び出し箇所を、新しいプラットフォームごとのアクセサに移行する。

まだ着手した PR はありません。

## 参考

- `bajutsu/config.py:420-487` — `Effective` dataclass と、その 35 個のフィールド。
- `bajutsu/config.py:462,471,468,478,481,464` — `platform`、`browser`、`headless`、`xcuitest`、
  `idb_version`、`package` の各フィールド宣言。
- `bajutsu/config.py:489-529` — `xcuitest.testRunner` を含むパスフィールドを再配置する
  `Effective.rebased()`。
- `bajutsu/config.py:122-136,557-587` — 既存の `platform` 判別子とその `backend` からの導出を
  担う `_check_platform` と `_effective_platform`。
- `bajutsu/environment.py:266-273,437` — プラットフォーム固有のフィールドを、型システムではなく
  実行時にガードしている呼び出し箇所。
- 関連するロードマップ項目：[BE-0009](../../implemented/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions-ja.md)
  （cross-platform abstractions）、[BE-0042](../../implemented/BE-0042-platform-backend-registry/BE-0042-platform-backend-registry-ja.md)
  （platform backend registry）、[BE-0057](../../implemented/BE-0057-rename-apps-to-targets/BE-0057-rename-apps-to-targets-ja.md)
  （`apps` → `targets` へのリネーム）、[BE-0007](../../proposals/BE-0007-android-backend/BE-0007-android-backend-ja.md)
  （Android backend）。
- 2026-07-02 のコードベース分析レポート（design）に由来します。

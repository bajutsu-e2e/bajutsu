[English](BE-XXXX-config-package-split.md) · **日本語**

# BE-XXXX — config をパッケージに分割し、Effective をサブレコードにまとめる

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-XXXX](BE-XXXX-config-package-split-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

[`bajutsu/config.py`](../../bajutsu/config.py) は 882 行の中に、性質の異なる 4 つの責務を
抱え込んでいます。pydantic による**入力スキーマ**（`MockServer`、`LaunchServer`、`Mailbox`、
`XcuitestConfig`、`PricingEntry`、`AiSettings`、`DoctorConfig`、`NotifyEndpoint`、`Defaults`、
`TargetConfig`、`Config`、59〜452 行目）、**解決済みの出力型**（`AiConfig`、`IosConfig`、
`WebConfig`、`AndroidConfig`、`Effective`、26〜49 行目および 455〜637 行目）、入力を出力へ
変換する**解決とマージのロジック**（`_merge_redact`、`_merge_ai`、`_platform_for_backend`、
`_effective_platform`、`_platform_config`、`resolve`、`parse_config_dict`、`load_config`、
709〜883 行目）、そして**パスの再基準化とナローイング用アクセサ**（`Effective.rebased`、
580〜636 行目、および `require_ios`、`require_web`、`require_android` とソフトアクセサ群、
639〜707 行目）です。本項目では、この 4 つの継ぎ目に沿って `config.py` を `bajutsu/config/`
パッケージへ分割し、同じ作業のなかで `Effective` の約 30 フィールドをまとまりのあるサブレコード
へグループ化します。どちらも挙動を変えない移動です。パッケージ分割後も外部からの呼び出し方は
変わりません。

## 動機

スキーマ検証、出力型の定義、マージと導出のロジック、パスの再基準化を 1 つの 882 行のモジュールに
詰め込むと、見通しが悪くなります。「`doctor_ok_coverage` はどう解決されているか」を知りたい読み手
は、無関係なフィールドの pydantic バリデータを読み飛ばしてから `resolve()` にたどり着く必要が
あります。この 4 つの責務には一方向の依存関係もあります。解決ロジックはスキーマを読んで出力型を
組み立てますが、スキーマや出力型の側が解決ロジックに依存することはありません。これは
[BE-0206](../BE-0206-serve-state-module-split/BE-0206-serve-state-module-split-ja.md) など、
本リポジトリの過去のモジュール分割が根拠にしてきたのと同じ形の依存関係です。

`Effective`（512〜569 行目）は、この問題をさらに大きくしています。約 30 フィールドを抱える
巨大なオブジェクトで、フィールドは識別情報（`target`、`platform_config`、`backend`、`device`、
`locale`）、起動関連（`launch_env`、`launch_args`、`setup`、`launch_server`、`ready_when`）、
証跡ディレクトリ（`scenarios`、`baselines`、`schemas`、`goldens`）、AI（`ai`）、doctor の閾値
（`doctor_ok_coverage`、`doctor_fail_coverage`）、実行時のデフォルト（`dismiss_alerts`、
`erase`、`network`）、通知（`notify`）、ルーティング（`requires`）、シークレットと mailbox と
mock サーバー（`redact`、`secrets`、`mailbox`、`mock_server`）にまたがっています。`Effective`
を受け取る側は、実際に使うフィールドがどれであるかに関わらず、常に全 30 フィールドを渡されます。
`resolve()`（821〜859 行目）も、フィールドを 1 つずつ組み立てる 38 行のコンストラクタ呼び出しに
なっています。`Effective.platform_config` はすでに判別可能な `PlatformConfig` の union
（509 行目）になっており、呼び出し側はプラットフォーム固有の値を読む前に `isinstance` や
`require_*` によるナローイングを強制されます。これは、本設計があえて避けている
`cfg["targets"][name]["..."]` のような文字列キーによるアクセスとは対照的な、保つ価値のある
パターンです。他のフィールド群にも同じ考え方を広げるべきだと考えます。

`config` と `backends` の間には、現時点で成立している循環 import も 2 箇所あります。
`_check_platform`（177 行目）と `_platform_for_backend`（752 行目）は、いずれも `from
bajutsu.backends import …` を関数本体に遅延させて循環を回避しています（この遅延は現状では
暗黙で、説明コメントは付いていません）。`config.py`
のうち `bajutsu.backends` を必要とするのは解決ロジックの側だけなので、これを独立したモジュール
に移すことで、各関数の内側ではなくパッケージの境界で循環を解消できます。

## 詳細設計

分割は、上で挙げた 4 つの責務にそのまま沿っており、構成として MECE になっています
（入力スキーマの解析、解決済みの出力の形、マージと導出のロジック、パスの再基準化とアクセサの
4 つに、今日の `config.py` のすべてのシンボルがちょうど 1 つずつ属します）。

1. **`bajutsu/config/schema.py`**：pydantic による入力モデル一式です。`MockServer`、
   `LaunchServer`、`Mailbox`、`XcuitestConfig`、`PricingEntry`、`AiSettings`、`DoctorConfig`、
   `NotifyEndpoint`、`Defaults`、`TargetConfig`、`Config`、およびそれぞれのフィールド
   バリデータ（現在の 51〜452 行目）に加えて、スキーマのフィールドを検証している
   `_check_platform`（170 行目）も含みます。このモジュールは、すでに関数内で遅延させている
   もの以外に `bajutsu.backends` への依存を持ちません。

2. **`bajutsu/config/effective.py`**：解決済みの frozen dataclass 群です。`AiConfig`、
   `IosConfig`、`WebConfig`、`AndroidConfig`、`PlatformConfig` の union、そして `Effective`
   本体（`platform` プロパティと `rebased` メソッドを含む、現在の 26〜49 行目および
   455〜637 行目）を置きます。`Effective` の約 30 フィールドは、`platform_config` がすでに
   プラットフォームの軸をナローイングしているのと同じ考え方で、次のサブレコードにまとめます。
   - **`EvidenceDirs`**：`scenarios`、`baselines`、`schemas`、`goldens`。
   - **`RunDefaults`**：`dismiss_alerts`、`erase`、`network`。
   - **`DoctorThresholds`**：`doctor_ok_coverage`、`doctor_fail_coverage`。

   `Effective` には、残りのフィールド（`target`、`platform_config`、`backend`、`device`、
   `locale`、`launch_env`、`launch_args`、`id_namespaces`、`reserved_namespaces`、
   `mock_server`、`setup`、`capture`、`redact`、`secrets`、`ai`、`mailbox`、`launch_server`、
   `ready_when`、`notify`、`visual_compare`、`requires`）をトップレベルのまま残し、新設する
   3 つのサブレコードのフィールド（`evidence_dirs`、`run_defaults`、`doctor_thresholds`）を
   加えます。これにより、フラットなフィールド数はおおよそ半分に減ります。`rebased` は、
   4 つの `at(...)` 呼び出しを個別に行う代わりに、`replace` で `evidence_dirs` をまとめて
   再構築する形に変わります。

3. **`bajutsu/config/resolve.py`**：マージと導出のロジックです。`_merge_redact`、
   `_merge_ai`、`_platform_for_backend`、`_effective_platform`、`_platform_config`、
   `resolve`、`parse_config_dict`、`load_config`（現在の 709〜883 行目）に加えて、
   `_platform_for_backend` と `_effective_platform` が行っている `bajutsu.backends` の
   import を、遅延させずトップレベルの import に変えます。`bajutsu.backends` を import
   するのはこのモジュールだけになるため、循環はパッケージの境界で消えます。現在 177 行目と
   752 行目にある遅延 import は、通常のトップレベル import になります。

4. **`bajutsu/config/accessors.py`**：ナローイング用アクセサとソフトなゲッター群です。
   `require_ios`、`require_web`、`require_android`、`web_base_url`、`web_engine`、
   `ios_bundle_id`、`android_package`、`idb_version_pin`（現在の 639〜707 行目）を置きます。

5. **`bajutsu/config/__init__.py`** が、4 つのサブモジュールの公開シンボルをすべて
   再エクスポートします。[`bajutsu/report/__init__.py`](../../bajutsu/report/__init__.py) の
   再エクスポートによるファサード（その docstring は「Public API is re-exported here, so
   `from bajutsu.report import …` is unchanged」と説明しています）が、この分割で踏襲する
   前例です。これにより `from bajutsu.config import Effective, resolve, require_ios, …`
   は変更なく動作し続け、パッケージの外にあるどの呼び出し側も変更する必要がありません。

6. `config.py` に言及している箇所について、[`docs/architecture.md`](../../docs/architecture.md)
   （と日本語版）を更新します。これは、振る舞いに合わせてこのドキュメントを最新に保つという
   BE-0113 の慣行に沿ったものです。

## 検討した代替案

- **`Effective` のフィールドだけをサブレコードにまとめ、`config.py` は 1 ファイルのまま
  残す。** これは読みやすさの改善の一部（巨大オブジェクトのフィールド数が減る点）は得られますが、
  より大きな見通しの改善は得られません。スキーマ検証、解決済みの型、マージのロジック、パスの
  再基準化は依然として 800 行超の 1 ファイルに同居したままで、`config` と `backends` の循環
  import による遅延 import もそのまま残ります。4 つの責務がすでに明確に MECE で切り分け
  られることを踏まえると、これは部分的な対処にとどまると判断し、見送りました。
- **責務ではなくファイルサイズだけを基準に分割する（たとえばおおよそ均等な 2 分割）。**
  これは循環 import を解消しません。どちらの循環の発生箇所も、`bajutsu.backends` を必要と
  する解決ロジック側の半分に落ち着く必要があり、また各モジュールに単一の名付けられる役割を
  与えることもできないため、見送りました。
- **`config.py` を現状のまま維持する。** 新しいスキーマのフィールドと新しい解決ロジックが
  同じファイルに積み重なり続け、モジュールは両方の軸で成長し続けます。2 箇所の lazy import
  による回避策も、無期限に残ることになります。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `bajutsu/config/schema.py` を新設し、pydantic の入力モデルを移動する（純粋な移動）
- [ ] `bajutsu/config/effective.py` を新設し、解決済みの dataclass を移動する。`Effective`
      のフィールドを `EvidenceDirs`、`RunDefaults`、`DoctorThresholds` のサブレコードに
      グループ化する
- [ ] `bajutsu/config/resolve.py` を新設し、マージと導出のロジックを移動する。`config` と
      `backends` の遅延 import 2 箇所を、このモジュールに限りトップレベルの import にする
- [ ] `bajutsu/config/accessors.py` を新設し、`require_*` とソフトアクセサを移動する
- [ ] `bajutsu/config/__init__.py` が公開 API 全体を再エクスポートする。パッケージの外にある
      呼び出し側は変更しない
- [ ] `docs/architecture.md`（en/ja）のうち `config.py` に言及している箇所を更新する

## 参考

- [`bajutsu/config.py`](../../bajutsu/config.py) — 本項目が分割するモジュール
- [`bajutsu/report/__init__.py`](../../bajutsu/report/__init__.py) — 本分割が踏襲する
  再エクスポートによるファサードの前例
- [BE-0206](../BE-0206-serve-state-module-split/BE-0206-serve-state-module-split-ja.md) —
  「責務どうしが一方向にしか依存しない」という同じ形に沿った、過去のモジュール分割
- [BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment-ja.md) —
  `docs/architecture.md` を振る舞いに合わせて最新に保つ慣行。本項目ではモジュール一覧の
  更新にこれを適用しています
- prime directive 3（アプリに依存しないコア） — config はアプリに依存しない継ぎ目であり、
  本項目が変えるのは内部のモジュール構成だけで、挙動やスキーマは変えません

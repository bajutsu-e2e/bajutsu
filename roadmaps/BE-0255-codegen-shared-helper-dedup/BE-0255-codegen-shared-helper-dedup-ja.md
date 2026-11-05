[English](BE-0255-codegen-shared-helper-dedup.md) · **日本語**

# BE-0255 — codegen の識別子と正規表現のヘルパーを codegen_common に集約する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0255](BE-0255-codegen-shared-helper-dedup-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **実装済み** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0255") |
| 実装 PR | [#1071](https://github.com/bajutsu-e2e/bajutsu/pull/1071) |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

[BE-0083](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification.md) は、
codegen 各ターゲットに共通のシナリオ走査を切り出しました。`codegen_common.py` が `CodeGenerator`
プロトコルと `render_test_file`・`_scenario_lines` を持ちます。`codegen_emit.py` は、すべての
呼び出し元が経由する単一のディスパッチャです。この共通化が扱うのは、シナリオを「どう走査するか」
です。

この共通化の一段下では、各ターゲットが小さな言語非依存のヘルパーを、コピーアンドペーストで自前に
持っています。

- シナリオ名を識別子に整形する処理
- クラス名を導出する処理
- 秒をミリ秒に変換する処理
- 文字列がプレーンな部分文字列か本物の正規表現かを判定する処理

本項目は、`codegen_common.py` という既存の共通化を、これらのヘルパーにも広げます。3 番目や 4 番目の
ターゲットは、再度コピーせずにこれらを継承できます。

これは、内部的で決定的な、AI を介さない経路に対する、**振る舞いを保つ**内部リファクタリングです。
codegen は Tier 1 の作成支援の出力であり、決定的な `run`/CI の判定経路には乗りません。コードを
移動するだけで、出力は変えません。例外は、重複がすでにターゲット間の食い違いを生んでいる箇所
（「動機」参照）です。そこだけは、より正しい振る舞いに揃えます。これは、BE-0083 の「単に配置を
変えるだけでなく揃える」という方針と同じです。

## 動機

次のヘルパーが、per-target の codegen モジュールのあいだでコピーアンドペーストされています。

- **`_ident` は `bajutsu/codegen.py:50-57`（XCUITest）と `bajutsu/codegen_uiautomator.py:75-82`
  （uiautomator/Kotlin）でバイト単位まで同一です。** どちらも、シナリオ名を `test_` 始まりの
  メソッド識別子に整形します。正規表現も数字始まりガードも同じです。
- **`_class_name` は `bajutsu/codegen.py:60-64` と `bajutsu/codegen_uiautomator.py:85-92` でほぼ
  同一です。** 違いは 1 つではなく 2 つあります。
  - クラス名の接尾辞が異なります。`codegen.py:64` は `f"{cleaned}UITests"`（複数形）を返し、
    `codegen_uiautomator.py:92` は `f"{cleaned}UITest"`（単数形）を返します。そのため共通ヘルパーは、
    接尾辞をターゲットごとの引数として残す必要があります。
  - `codegen_uiautomator.py:89-91` の数字始まりガード（コメントは「Kotlin のクラス名は数字で
    始められない」）に、XCUITest 側の対応物がありません。Swift の `class` 名にも同じ制約があります。
    現状では、数字で始まるシナリオ名から不正な Swift クラス名が生成されます。ガードしているのは
    uiautomator ターゲットだけです。

  これは、コピーされたヘルパーが招く典型的な食い違いです。一方の複製にだけ修正が入り、もう一方には
  反映されないまま残ります。
- **`_ms` は `bajutsu/codegen_playwright.py:289-290` と `bajutsu/codegen_uiautomator.py:163-164`
  で同一です。** どちらも、`float` 秒を、生成するタイムアウトや遅延の呼び出し用に `int` ミリ秒へ
  変換します。
- **`_RE_METACHARS` は同一の frozenset で、コメントもほぼ同一のまま `bajutsu/codegen.py:73` と
  `bajutsu/codegen_uiautomator.py:55` に存在します。** どちらのターゲットも、`labelMatches` パターンの
  分類に使います。
  - メタ文字を含まないプレーンな部分文字列は、ネイティブの `CONTAINS`/`contains` 呼び出しに変換できます。
  - 本物の正規表現は `// TODO` のまま残します。NSPredicate にも UiAutomator2 にも、Python の
    `re.search` と忠実に対応する任意正規表現の形がないためです。
- **`_NO_NETWORK` 定数とネットワークアサーションの `// TODO` ブロックは、`bajutsu/codegen.py` と
  `bajutsu/codegen_uiautomator.py` で重複しています。** どちらもブラックボックスなオンデバイス
  ターゲットで、ネットワークのインターセプト手段を持ちません。どちらも、同じ形の
  `// TODO: wait until request (...)` 行と `// TODO: request assertion (...)` 行を出力します。違うのは
  定数の文面だけです（「XCUITest has no network interception...」と「the adb backend has no network
  interception...」）。

これらは、現状のテスト済みの出力にとってバグではありません。ただし、どのヘルパーも言語非依存です。
Swift の識別子も Kotlin の識別子も、仮に増える 4 番目のターゲットの識別子も、シナリオ名を同じ処理で
整形します。独立した複製を持ち続けても、見合う利点はなく、重複のコストだけが残ります。実際に、
一方のターゲットの修正（数字始まりガード）が、もう一方から食い違ってしまいました。

BE-0083 は、`codegen_common.py` を codegen ロジックの共通で target-agnostic な置き場所として確立
済みです。ヘルパーをここに集約すれば、食い違いのリスクがなくなります。per-target のモジュールは、
本当にターゲット固有の部分、つまり行の構文だけに絞り込まれます。

## 詳細設計

作業はヘルパーごとに MECE に分解できます。ネットワーク TODO の重複について範囲を絞った項目を 1 件、
明示的な非対象を 1 件添えます。

1. **`_ident` を `codegen_common.py` に移動します。** `codegen.py` と `codegen_uiautomator.py` で
   バイト単位まで同一です。両方の呼び出し元は、共通関数を import して自前のコピーを削除します。
   Playwright ターゲットが出力するのは JS/TS の `test(...)` 呼び出しで、Swift/Kotlin 風の裸の
   識別子を必要としません。必要になった時点で乗ります。
2. **`_class_name` を `codegen_common.py` に移動し、接尾辞を引数として受け取りつつ、数字始まり
   ガードをすべてのターゲットに統一して適用します。**
   - 2 つのターゲットは、クラス名の接尾辞が異なります（XCUITest は `"UITests"`、uiautomator は
     `"UITest"`）。共通ヘルパーは、接尾辞をターゲットごとの引数として保ちます。
   - 数字始まりガード（`cleaned[0].isdigit()` を見て `_` を先頭に付ける処理）は、現状は
     `codegen_uiautomator.py` にしかありません。同じ制約を持つ XCUITest の Swift `class` 名にも、
     等しく必要です。XCUITest 側は黙って無防備なままになっています。

   ヘルパーを移動するこの機会に、バグを修正と一緒に移すのではなく、この不整合そのものを閉じます。
3. **`_ms` を `codegen_common.py` に移動します。** `codegen_playwright.py` と
   `codegen_uiautomator.py` で同一です。XCUITest 側の `codegen.py` は、現状ミリ秒変換を必要と
   しません。共通ヘルパーへのアクセスは得られますが、使うかどうかは強制されません。
4. **`codegen_common.py` に、既存の `_RE_METACHARS` を土台にした共有の `is_plain_substring(pattern)`
   を追加します。** `codegen.py` と `codegen_uiautomator.py` は、`set(pattern) & _RE_METACHARS` を
   それぞれ個別に書く代わりに、この関数を呼び出します。frozenset も共通モジュールの定数として
   一緒に移動します。ほぼ同じコメントが 2 箇所にある状態は、1 箇所のコメントにまとめます。
5. **ブラックボックスなモバイル 2 ターゲットの `_NO_NETWORK` TODO ブロック（wait-until-request と
   request/requestSequence アサーションの行）について、共有の `NetworkUnsupported` 風ヘルパーを
   検討します。**
   - 「実施する」ではなく「検討する」にとどめています。2 つの定数の文面は、実際のバックエンド名
     （XCUITest か adb バックエンドか）を挙げるターゲット固有の説明文です。共有ヘルパーにする
     場合は、これをパラメータ化する必要があります。
   - パラメータ化した結果が、置き換える対象の 2 つの短い定数より読みにくくなる場合は、定数自体は
     分けたままで構いません。その場合は、周辺の `// TODO` 行の形（すでにほぼ同一）だけを共有します。
   - Playwright は、リクエストをインターセプトできるという別のネットワーク事情をすでに持っています。
     この重複の対象外なので、変更しません。
6. **per-target の `_emit_step` ディスパッチの形はそのまま残します。** 各ターゲットには、`Step` の
   種類ごとにターゲット固有の行へ振り分ける `if` 分岐の連なりがあります。この連なりは、per-target
   なコード生成に本質的に伴うものです。BE-0083 が、この層より上をすでに共通化しています。連なりを
   さらに畳み込むことは、本項目の対象外です（「検討した代替案」を参照）。

各ステップは、明記した箇所を除いて、独立に着地させられ、振る舞いも保たれます。例外は、手順 2 の
数字始まりガードの修正です。これは、共通モジュールへの移動に相乗りする、範囲を絞った正しさの
修正で、別の提案にはしません。既知のバグを共通モジュールへそのまま複製しては、集約する意味が
失われるからです。

## 検討した代替案

**per-target のフックを呼ぶ共有イテレータを用意し、3 つの `_emit_step` の if 連鎖そのものを畳み
込む案。** 上記の小さなヘルパーにとどめず、ここまで踏み込む案です。検討しましたが、見送りました。

3 つの `_emit_step` の実装は、ステップ種別ごとの行生成が十分に異なります（Swift、Kotlin、TypeScript
の構文の違いに加え、各ターゲット固有のセレクタとプレディケートの構築ロジックもあります）。これらを
1 つの汎用ディスパッチに通すには、現状の `CodeGenerator` プロトコルより広いフック面が必要です。
見返りが生まれるのは、4 番目のターゲットが実在してからです。

すでに重複している具体的なヘルパー群を今回集約するほうが、労力は小さく、見返りは大きくなります。
ディスパッチの形は、Android など別ターゲットが実際に着地した時点で見直せば足ります。2 例目の具体的な
必要が生まれる前に抽象化を設計するよりも、そのほうが理にかなっています。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [x] `_ident` を `codegen/common.py` に `ident` として移動し、`codegen/xcuitest.py` と
      `codegen/uiautomator.py` を import に切り替える。
- [x] `_class_name` を `codegen/common.py` に `class_name(name, suffix)` として移動し、数字始まり
      ガードをすべてのターゲットに統一して適用する（XCUITest 側の無防備な不整合を閉じる）。
- [x] `_ms` を `codegen/common.py` に `ms` として移動し、`codegen/playwright.py` と
      `codegen/uiautomator.py` を import に切り替える。
- [x] 共有の `is_plain_substring(pattern)`/`_RE_METACHARS` ヘルパーを `codegen/common.py` に追加し、
      `codegen/xcuitest.py`/`codegen/uiautomator.py` をそちらに切り替える。
- [x] `_NO_NETWORK` TODO ブロック向けの共有 `network_unsupported(subject)` ヘルパーを検討し、採用
      した。パラメータ化した版は現行の 2 つの定数と同程度以上に読みやすく、重複していた末尾の文面
      とその理由コメントを 1 箇所にまとめられる。
- [x] per-target の `_emit_step` ディスパッチの形を対象外とすることを（黙って落とすのではなく）
      「検討した代替案」に明記して確認する。

> **補足（パスの変化）**：本提案を書いた時点から、フラットな `bajutsu/codegen*.py` の各モジュールは
> `bajutsu/codegen/` パッケージになりました（BE-0257）。そのため上記の移動先は `codegen/common.py`、
> `codegen/xcuitest.py`、`codegen/uiautomator.py`、`codegen/playwright.py` です。ヘルパーも継ぎ目も
> 変わりません。

**ログ**

- [#1071](https://github.com/bajutsu-e2e/bajutsu/pull/1071) で実装しました。`ident`、`class_name`、
  `ms`、`is_plain_substring`（と `_RE_METACHARS`）、`network_unsupported` を `codegen/common.py` に
  移し、3 つの per-target emitter がそれらを import するようにしました。XCUITest の数字始まりガード
  の欠落（項目 2）も閉じました。

## 参考

- [BE-0083 — codegen の emitter を共通のシナリオ走査へ統一する](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification-ja.md) —
  本提案が同じ、単に配置を変えるだけでなく揃えるという方針を広げる対象の、共通シナリオ走査です。
- [`bajutsu/codegen_common.py`](../../bajutsu/codegen_common.py) —
  本提案が識別子生成、正規表現判定、時間変換のヘルパーを追加する、既存の共通モジュールです。
- [`bajutsu/codegen_emit.py`](../../bajutsu/codegen_emit.py) —
  すべての codegen 呼び出し元がすでに経由している単一のディスパッチャです。本提案の影響を受けません。
- [`bajutsu/codegen.py`](../../bajutsu/codegen.py)、
  [`bajutsu/codegen_uiautomator.py`](../../bajutsu/codegen_uiautomator.py)、
  [`bajutsu/codegen_playwright.py`](../../bajutsu/codegen_playwright.py) —
  現状、重複したヘルパーを抱えている 3 つの per-target emitter です。

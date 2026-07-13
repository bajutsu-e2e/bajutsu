[English](BE-0255-codegen-shared-helper-dedup.md) · **日本語**

# BE-0255 — codegen の識別子と正規表現のヘルパーを codegen_common に集約する

<!-- BE-METADATA -->
| 項目 | 値 |
|---|---|
| 提案 | [BE-0255](BE-0255-codegen-shared-helper-dedup-ja.md) |
| 提案者 | [@0x0c](https://github.com/0x0c) |
| 状態 | **提案** |
| トラッキング Issue | [検索](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0255") |
| トピック | コードベース品質・技術的負債 |
<!-- /BE-METADATA -->

## はじめに

[BE-0083](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification.md) は、
codegen 各ターゲットの共通の走査を切り出しました。`codegen_common.py` が `CodeGenerator` プロト
コルと `render_test_file`/`_scenario_lines` を持ち、`codegen_emit.py` がすべての呼び出し元にとって
単一のディスパッチャになっています。この共通化はシナリオを「どう走査するか」をカバーするもので、
その一段下にある、各ターゲットがコピーアンドペーストで自前に持っている小さな言語非依存のヘルパー
群はカバーしていません。シナリオ名を識別子に整形する処理、クラス名を導出する処理、秒をミリ秒に
変換する処理、文字列がプレーンな部分文字列か本物の正規表現かを判定する処理がそれです。本提案は、
`codegen_common.py` という既存の共通化をこれらのヘルパーにも広げ、3 番目や 4 番目のターゲットが
再度コピーするのではなく、これらを継承できるようにするものです。

これは内部的で決定的、かつ AI を介さない経路に対する、**振る舞いを保つ**内部リファクタリングです
（codegen は Tier 1 の作成支援の出力であり、決定的な `run`/CI の判定経路には一度も乗りません）。
コードを移動するだけで出力は変えませんが、重複がすでにターゲット間の食い違いを生んでいる箇所（
「動機」参照）だけは、より正しい振る舞いに揃えます。これは BE-0083 が持つ、単に配置を変えるだけ
でなく揃えるという方針と同じです。

## 動機

具体的には、次のヘルパーが per-target の codegen モジュール間でコピーアンドペーストされています。

- **`_ident` は `bajutsu/codegen.py:50-57`（XCUITest）と `bajutsu/codegen_uiautomator.py:75-82`
  （uiautomator/Kotlin）でバイト単位まで同一です。** どちらもシナリオ名を、同じ正規表現と同じ数字
  始まりガードで `test_` 始まりのメソッド識別子に整形します。
- **`_class_name` は `bajutsu/codegen.py:60-64` と `bajutsu/codegen_uiautomator.py:85-92` でほぼ
  同一です。** 違いは 1 つではなく 2 つあります。まずクラス名の接尾辞が異なり、`codegen.py:64` は
  `f"{cleaned}UITests"`（複数形）を、`codegen_uiautomator.py:92` は `f"{cleaned}UITest"`（単数形）
  を返すため、共通ヘルパーは接尾辞をターゲットごとの引数として残す必要があります。次に
  `codegen_uiautomator.py:89-91` の数字始まりガード（コメントには
  「Kotlin のクラス名は数字で始められない」とあります）です。XCUITest 側にはこのガードが
  ありませんが、Swift の `class` 名にも同じ制約があります。つまり現状では、数字で始まるシナリオ名
  を渡すと、uiautomator ターゲットだけがガードしている問題を XCUITest 側は無防備に踏み抜いて不正
  な Swift クラス名を生成します。これは、コピーされたヘルパーが招く典型的な食い違いです。一方の
  複製にだけ修正が入り、もう一方には反映されないまま残ります。
- **`_ms` は `bajutsu/codegen_playwright.py:289-290` と `bajutsu/codegen_uiautomator.py:163-164`
  で同一です。** どちらも `float` 秒を、生成するタイムアウトや遅延呼び出し用の `int` ミリ秒に変換
  します。
- **`_RE_METACHARS` は同一の frozenset で、コメントもほぼ同一のまま `bajutsu/codegen.py:73` と
  `bajutsu/codegen_uiautomator.py:55` に存在します。** どちらも、`labelMatches` パターンがメタ
  文字を含まないプレーンな部分文字列（ネイティブの `CONTAINS`/`contains` 呼び出しに変換可能）か、
  本物の正規表現（`// TODO` のまま残します。NSPredicate にも UiAutomator2 にも Python の
  `re.search` と忠実に対応する任意正規表現の形がないためです）かを、この判定で切り分けています。
- **`_NO_NETWORK` 定数とネットワークアサーションの `// TODO` ブロックは `bajutsu/codegen.py` と
  `bajutsu/codegen_uiautomator.py` の間で重複しています。** どちらもブラックボックスなオンデバイス
  ターゲットでネットワークのインターセプト手段を持たないため、同じ形の `// TODO: wait until
  request (...)` / `// TODO: request assertion (...)` 行を出力します。違うのは定数の文面（
  「XCUITest has no network interception...」と「the adb backend has no network
  interception...」）だけです。

これらはいずれも、現状のテスト済みの出力にとってバグではありません。しかし、これらのヘルパーは
いずれも言語非依存です。Swift でも Kotlin でも、仮に 4 番目のターゲットが増えても、シナリオ名の
整形は同じ処理で済みます。したがって独立した複製として持ち続けることは、見合う利点のない純粋な
重複コストであり、実際に一方のターゲットの修正（数字始まりガード）がもう一方から食い違ってしまい
ました。BE-0083 がすでに codegen ロジックの共通で target-agnostic な置き場所として確立した
`codegen_common.py` にこれらを集約すれば、この食い違いのリスクをなくし、per-target のモジュール
を本当にターゲット固有の部分、すなわち行の構文だけに絞り込めます。

## 詳細設計

作業はヘルパーごとに MECE に分解できます。加えて、ネットワーク TODO の重複についての範囲を絞った
項目と、明示的な非対象を 1 件ずつ添えます。

1. **`_ident` を `codegen_common.py` に移動します。** `codegen.py` と `codegen_uiautomator.py` で
   バイト単位まで同一なので、両方の呼び出し元は共通関数を import し、自前のコピーを削除します。
   Playwright ターゲットは JS/TS の `test(...)` 呼び出しであり、Swift/Kotlin 風の裸の識別子を必要
   としないため、今後必要になった時点で乗るという扱いにとどめます。
2. **`_class_name` を `codegen_common.py` に移動し、接尾辞を引数として受け取りつつ、数字始まり
   ガードをすべてのターゲットに統一して適用します。** 2 つのターゲットはクラス名の接尾辞が異なる
   （XCUITest は `"UITests"`、uiautomator は `"UITest"`）ため、共通ヘルパーはこれをターゲットごとの
   引数として保ちます。加えて、現状は `codegen_uiautomator.py` にしかない数字始まりガード
   （`cleaned[0].isdigit()` を見て `_` を先頭に付ける処理）は、同じ制約を持つ XCUITest の Swift
   `class` 名にも等しく必要です。ヘルパーを移動するこの機会に、バグを一緒に移すのではなく、この
   不整合そのものを閉じます。
3. **`_ms` を `codegen_common.py` に移動します。** `codegen_playwright.py` と
   `codegen_uiautomator.py` で同一なので、両者を共通関数の import に切り替えます。XCUITest 側の
   `codegen.py` は現状ミリ秒変換を必要としませんが、共通ヘルパーへのアクセス自体は得られ、使うか
   どうかは強制されません。
4. **既存の `_RE_METACHARS` を土台に、`codegen_common.py` へ共有の `is_plain_substring(pattern)`
   を追加します。** `codegen.py` と `codegen_uiautomator.py` は、それぞれ `set(pattern) &
   _RE_METACHARS` を個別に書く代わりにこの関数を呼び出すようにします。frozenset 自体も共通モジュ
   ールの定数として一緒に移動し、ほぼ同じコメントが 2 箇所にある状態を 1 箇所のコメントにまとめ
   ます。
5. **ブラックボックスなモバイル 2 ターゲットの `_NO_NETWORK` TODO ブロック（wait-until-request と
   request/requestSequence アサーションの行）について、共有の `NetworkUnsupported` 風ヘルパーを
   検討します。** 「実施する」ではなく「検討する」にとどめているのは、2 つの定数の文面がターゲット
   固有の説明文（実際のバックエンド名、XCUITest か adb バックエンドかを挙げる文）であり、共有
   ヘルパーにする場合はこれをパラメータ化する必要があるからです。パラメータ化した結果が、置き換え
   る対象の 2 つの短い定数より読みにくくなるなら、定数自体は分けたままにし、周辺の `// TODO` 行の
   形（すでにほぼ同一）だけを共有するにとどめるのも妥当な結論です。Playwright はすでにリクエスト
   をインターセプトできるという別のネットワーク事情を持っており、この重複の対象外なので変更しま
   せん。
6. **per-target の `_emit_step` ディスパッチの形はそのまま残します。** `Step` の種類ごとにターゲッ
   ト固有の行へ振り分ける各ターゲットの `if` 分岐の連なりは、per-target なコード生成に本質的に
   伴うものです（BE-0083 がこの層より上をすでに共通化しています）。これをさらに畳み込むことは
   本提案の対象外とします。詳細は「検討した代替案」を参照してください。

各ステップは、明記した箇所を除いて独立に着地させられ、振る舞いも保たれます。例外は項目 2 の数字
始まりガードの修正で、これは共通モジュールへの移動に相乗りする形の、範囲を絞った正しさの修正です。
既知のバグを共通モジュールにそのまま複製したのでは、集約する意味そのものが失われるからです。

## 検討した代替案

**per-target のフックを呼ぶ共有イテレータを用意し、3 つの `_emit_step` の if 連鎖そのものを畳み
込む案を検討しましたが、見送りました。** 上記の小さなヘルパーだけにとどめず、ここまで踏み込む案
です。3 つの `_emit_step` の実装は、ステップ種別ごとの行生成において（Swift、Kotlin、TypeScript
という構文の違いに加え、各ターゲット固有のセレクタとプレディケートの構築ロジックもあり）十分に
異なっており、これらを 1 つの汎用ディスパッチに通そうとすると、現状の `CodeGenerator` プロトコル
より広いフック面が必要になります。しかもその見返りは、4 番目のターゲットが実在して初めて生まれ
ます。今回は、すでに重複している具体的なヘルパー群をそのまま集約するほうが労力が小さく見返りも
大きく、ディスパッチの形を見直すのは Android など別ターゲットが実際に着地した時点まで待つほうが、
2 例目の具体的な必要が生まれる前に抽象化を設計するより理にかなっています。

## 進捗

> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な
> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと
> ともに記録します。

- [ ] `_ident` を `codegen_common.py` に移動し、`codegen.py` と `codegen_uiautomator.py` を import
      に切り替える。
- [ ] `_class_name` を `codegen_common.py` に移動し、数字始まりガードをすべてのターゲットに統一
      して適用する。
- [ ] `_ms` を `codegen_common.py` に移動し、`codegen_playwright.py` と
      `codegen_uiautomator.py` を import に切り替える。
- [ ] 共有の `is_plain_substring(pattern)`/`_RE_METACHARS` ヘルパーを `codegen_common.py` に追加し、
      `codegen.py`/`codegen_uiautomator.py` をそちらに切り替える。
- [ ] `_NO_NETWORK` TODO ブロック向けの共有 `NetworkUnsupported` 風ヘルパーを検討する。パラメータ
      化した版が現行の 2 つの定数と同程度以上に読みやすい場合のみ採用する。
- [ ] per-target の `_emit_step` ディスパッチの形を対象外とすることを（黙って落とすのではなく）
      ここに明記して確認する。

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
